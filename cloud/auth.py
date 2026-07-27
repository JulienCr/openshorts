"""Authentication for cloud mode: magic link + JWT sessions + current-user loading.

- Magic link: POST /api/auth/magic-link -> emailed single-use token (15 min).
  POST /api/auth/magic-link/verify consumes it (POST, not GET, so email scanners
  that prefetch links can't burn them) and returns a 30-day JWT.
- get_current_user_optional never raises: a missing/expired token degrades to the
  anonymous BYOK flow rather than breaking it.
- /api/me returns the user's plan + minute balance (no Stripe call needed).
"""
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

import jwt
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, func, and_

from .config import settings
from . import config, database, metering, email_policy
from .models import User, MagicLinkToken, UploadPostProfile, SignupAttribution

router = APIRouter()

JWT_ALGO = "HS256"
JWT_TTL_DAYS = 30
MAGIC_TTL_MINUTES = 15
MAGIC_RATE_LIMIT = 3          # per email
MAGIC_RATE_WINDOW_MIN = 15
# Only accept attribution for an account this young. Both sign-in flows post it
# right after the redirect, so anything older is a returning user whose "source"
# would be their re-entry, not their sign-up.
ATTRIBUTION_MAX_AGE_MIN = 60
ATTRIBUTION_FIELD_MAX = 500   # client-supplied strings are truncated to this


def _now():
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# JWT + current user
# --------------------------------------------------------------------------- #
def issue_jwt(user_id, email) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": _now() + timedelta(days=JWT_TTL_DAYS),
        "iat": _now(),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGO)


def _decode_jwt(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGO])
    except Exception:
        return None


class CurrentUser:
    """Lightweight per-request user snapshot carried through endpoints.

    Kept small and eager (no lazy DB access) so sync helpers like
    ``managed_keys.has_active_entitlement`` can read it without awaiting.
    """

    def __init__(self, id, email, entitled=False, plan=None, upload_post_profile=None):
        self.id = id
        self.email = email
        self.entitled = entitled
        self.plan = plan
        self.upload_post_profile = upload_post_profile


async def _load_current_user(session, user_id) -> Optional["CurrentUser"]:
    user = await session.get(User, user_id)
    if user is None:
        return None
    sub = await metering._active_subscription(session, user_id)
    topups = await metering._topups_fifo(session, user_id)
    topup_remaining = sum(
        (float(t.minutes_total) - float(t.minutes_consumed) for t in topups), 0.0
    )
    profile = await session.get(UploadPostProfile, user_id)
    # Entitled to managed keys with an active plan, any top-up credit, or the
    # free monthly allowance (Google-authenticated accounts only).
    free = sub is None and metering.free_plan_eligible(user)
    entitled = bool(sub is not None or topup_remaining > 0 or free)
    return CurrentUser(
        id=user.id,
        email=user.email,
        entitled=entitled,
        plan=sub.plan if sub else ("free" if free else None),
        upload_post_profile=profile.profile_username if profile else None,
    )


async def get_current_user_optional(request: Request) -> Optional[CurrentUser]:
    """Return the authenticated user, or None for anonymous / BYOK requests."""
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    payload = _decode_jwt(auth[7:].strip())
    if not payload:
        return None
    try:
        import uuid
        user_id = uuid.UUID(payload["sub"])
    except Exception:
        return None
    async with database.session() as session:
        return await _load_current_user(session, user_id)


async def get_current_user_required(request: Request) -> CurrentUser:
    user = await get_current_user_optional(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


# --------------------------------------------------------------------------- #
# Magic link
# --------------------------------------------------------------------------- #
class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicVerifyRequest(BaseModel):
    token: str


@router.post("/api/auth/magic-link")
async def request_magic_link(payload: MagicLinkRequest, request: Request):
    from .emails import send_magic_link_email

    # Block temp-mail before doing anything else — the free plan is open to
    # email accounts now, so disposable domains would be a free-minute farm.
    if email_policy.is_disposable(payload.email):
        raise HTTPException(status_code=400, detail=(
            "That email provider isn't supported. Please use a permanent "
            "address (Gmail, Outlook, iCloud…) or sign in with Google."))

    # Normalize (strip +tags / Gmail dots) so aliases can't mint extra accounts.
    email = email_policy.normalize_email(payload.email)
    async with database.session() as session:
        async with session.begin():
            window_start = _now() - timedelta(minutes=MAGIC_RATE_WINDOW_MIN)
            recent = (await session.execute(
                select(func.count(MagicLinkToken.id)).where(and_(
                    MagicLinkToken.email == email,
                    MagicLinkToken.created_at >= window_start,
                ))
            )).scalar_one()
            if recent >= MAGIC_RATE_LIMIT:
                raise HTTPException(status_code=429, detail="Too many login attempts. Try again in a few minutes.")

            raw_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
            session.add(MagicLinkToken(
                email=email,
                token_hash=token_hash,
                expires_at=_now() + timedelta(minutes=MAGIC_TTL_MINUTES),
                request_ip=request.client.host if request.client else None,
            ))

    link = f"{settings.frontend_url}/#/auth/verify?ml={raw_token}"
    await send_magic_link_email(email, link)
    return {"ok": True}


@router.post("/api/auth/magic-link/verify")
async def verify_magic_link(payload: MagicVerifyRequest):
    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    async with database.session() as session:
        async with session.begin():
            row = (await session.execute(
                select(MagicLinkToken).where(MagicLinkToken.token_hash == token_hash)
                .with_for_update()
            )).scalar_one_or_none()
            if row is None or row.used_at is not None or row.expires_at < _now():
                raise HTTPException(status_code=400, detail="This sign-in link is invalid or expired.")
            row.used_at = _now()
            email = row.email.lower()

            user = (await session.execute(
                select(User).where(User.email == email)
            )).scalar_one_or_none()
            if user is None:
                user = User(email=email, last_login_at=_now())
                session.add(user)
                await session.flush()
            else:
                user.last_login_at = _now()
            user_id, user_email = user.id, user.email

        token = issue_jwt(user_id, user_email)
        cu = await _load_current_user(session, user_id)
    return {"token": token, "user": {"id": str(user_id), "email": user_email, "entitled": cu.entitled}}


# --------------------------------------------------------------------------- #
# Signup attribution
# --------------------------------------------------------------------------- #
class AttributionPayload(BaseModel):
    referrer: Optional[str] = None
    landing_path: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None


def _clip(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    return value[:ATTRIBUTION_FIELD_MAX] or None


def _referrer_host(referrer: Optional[str]) -> Optional[str]:
    """Bare host of the referrer, the key we actually group by."""
    if not referrer:
        return None
    try:
        from urllib.parse import urlparse
        host = (urlparse(referrer).hostname or "").lower()
    except Exception:
        return None
    return host or None


@router.post("/api/auth/attribution")
async def record_attribution(payload: AttributionPayload, request: Request):
    """Record where a brand-new account came from. First touch wins.

    Fire-and-forget from the client right after sign-in: it never fails the
    sign-up, it just returns ``recorded: false`` when the row already exists or
    the account is too old to be a genuine sign-up.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    user = await get_current_user_required(request)
    async with database.session() as session:
        async with session.begin():
            row = await session.get(User, user.id)
            if row is None or row.created_at is None:
                return {"ok": True, "recorded": False}
            if _now() - row.created_at > timedelta(minutes=ATTRIBUTION_MAX_AGE_MIN):
                return {"ok": True, "recorded": False}
            referrer = _clip(payload.referrer)
            # ON CONFLICT DO NOTHING keeps first touch even if the client posts
            # twice concurrently (two tabs finishing the same redirect).
            result = await session.execute(
                pg_insert(SignupAttribution).values(
                    user_id=user.id,
                    referrer=referrer,
                    referrer_host=_referrer_host(referrer),
                    landing_path=_clip(payload.landing_path),
                    utm_source=_clip(payload.utm_source),
                    utm_medium=_clip(payload.utm_medium),
                    utm_campaign=_clip(payload.utm_campaign),
                ).on_conflict_do_nothing(index_elements=["user_id"])
            )
    return {"ok": True, "recorded": result.rowcount > 0}


# --------------------------------------------------------------------------- #
# /api/me
# --------------------------------------------------------------------------- #
@router.get("/api/me")
async def get_me(request: Request):
    user = await get_current_user_required(request)
    async with database.session() as session:
        bal = await metering._balance(session, user.id)
        sub = bal["_sub"]
    # Report the entitling subscription when there is one; otherwise fall back to
    # a row that needs the customer's attention (card declined, payment never
    # finished). Without this a past_due customer sees a plain free account and
    # never learns their card failed. `plan` stays as computed — the entitlement
    # really is free — so the UI shows "free plan" plus the warning badge.
    attention = bal["_sub_row"]
    if attention is not None and attention.status not in config.BILLING_ATTENTION_STATES:
        attention = None
    shown = sub or attention
    return {
        "user": {"id": str(user.id), "email": user.email},
        "entitled": user.entitled,
        "plan": bal["plan"],
        "status": shown.status if shown else None,
        "interval": shown.interval if shown else None,
        "period_end": bal["period_end"].isoformat() if bal["period_end"] else None,
        "cancel_at_period_end": shown.cancel_at_period_end if shown else False,
        # Drives the "Manage billing" route in the UI: any Stripe relationship,
        # live or broken, must keep the portal reachable.
        "has_billing_account": bool(sub or attention),
        "minutes": {
            "plan_allowance": bal["plan_allowance"],
            "plan_used": bal["plan_used"],
            "plan_remaining": bal["plan_remaining"],
            "topup_remaining": bal["topup_remaining"],
            "remaining": bal["remaining"],
        },
        "upload_post_profile": user.upload_post_profile,
    }

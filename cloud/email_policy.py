"""Email hygiene for sign-up: block disposable domains, normalize aliases,
verify the domain can actually receive mail.

Three abuse vectors this closes, now that the free plan is open to email (not
just Google) accounts:
  * temp-mail domains → free-minute farms (blocklist below, plus MX-target
    matching for the rotating front domains the blocklist can't keep up with).
  * provider aliases (gmail dots / +tags) → one address becomes infinite
    accounts → normalized to a single canonical form.
  * nonexistent addresses/domains → every magic link bounces back into the
    support inbox and burns sender reputation with PrivateEmail.
"""
import os
import time

_DOMAINS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "disposable_domains.txt")

# Providers where a "+tag" suffix and (for Google) dots are ignored by the
# mail server, so foo+1@ and f.oo@ all deliver to the same inbox.
_DOT_INSENSITIVE = {"gmail.com", "googlemail.com"}
_PLUS_ALIASING = _DOT_INSENSITIVE | {
    "outlook.com", "hotmail.com", "live.com", "icloud.com", "me.com",
    "fastmail.com", "protonmail.com", "proton.me", "yahoo.com",
}


def _load_disposable() -> set:
    domains = set()
    try:
        with open(_DOMAINS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    domains.add(line)
    except FileNotFoundError:
        pass
    return domains


_DISPOSABLE = _load_disposable()


def is_disposable(email: str) -> bool:
    """True if the address's domain is a known disposable/temp-mail provider."""
    domain = (email or "").rsplit("@", 1)[-1].strip().lower()
    return domain in _DISPOSABLE


def normalize_email(email: str) -> str:
    """Canonical form used as the account key.

    Lowercases; strips a +tag from providers that ignore it; strips dots from
    the Gmail local part. Non-aliasing providers keep their local part intact
    so we never merge two genuinely different addresses.
    """
    email = (email or "").strip().lower()
    if "@" not in email:
        return email
    local, domain = email.rsplit("@", 1)
    if domain in _PLUS_ALIASING and "+" in local:
        local = local.split("+", 1)[0]
    if domain in _DOT_INSENSITIVE:
        local = local.replace(".", "")
    return f"{local}@{domain}"


def disposable_count() -> int:
    return len(_DISPOSABLE)


# --------------------------------------------------------------------------- #
# MX verification
# --------------------------------------------------------------------------- #
# Disposable providers rotate their front domains faster than any blocklist
# (onldm.net was one of 10minutemail's), but every front domain points its MX
# at the provider's real SMTP hosts, which stay put. Matching the MX target
# catches the whole rotation at once. Suffix match on these domains.
_DISPOSABLE_MX = {
    "10minutemail.com",
    "1secmail.com",
    "dropmail.me",
    "generator.email",
    "guerrillamail.com",
    "harakirimail.com",
    "mail.tm",
    "maildrop.cc",
    "mailinator.com",
    "mohmal.com",
    "temp-mail.org",
    "yopmail.com",
}

MX_OK = "ok"
MX_NONE = "no_mx"
MX_DISPOSABLE = "disposable_mx"

_MX_TIMEOUT = 4.0        # seconds; a slow resolver must not stall sign-in
_MX_CACHE_TTL = 3600.0
_MX_CACHE_MAX = 10_000   # bots probing random domains must not grow it unbounded
_mx_cache = {}           # domain -> (verdict, monotonic expiry)


def classify_mx_hosts(hosts) -> str:
    """Verdict from a domain's MX target hostnames."""
    hosts = [str(h).rstrip(".").lower() for h in hosts]
    # RFC 7505 null MX (a single "." target): the domain declares it takes no mail.
    if not any(hosts):
        return MX_NONE
    for host in hosts:
        if any(host == d or host.endswith("." + d) for d in _DISPOSABLE_MX):
            return MX_DISPOSABLE
    return MX_OK


async def _resolve(domain: str, rtype: str):
    import dns.asyncresolver

    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = _MX_TIMEOUT
    return await resolver.resolve(domain, rtype)


async def mx_verdict(email: str) -> str:
    """MX_OK / MX_NONE / MX_DISPOSABLE for the address's domain.

    Fails open (MX_OK) on resolver trouble — a DNS blip must never lock real
    users out of sign-in.
    """
    import dns.resolver

    domain = (email or "").rsplit("@", 1)[-1].strip().lower()
    if not domain:
        return MX_NONE
    now = time.monotonic()
    hit = _mx_cache.get(domain)
    if hit and hit[1] > now:
        return hit[0]

    try:
        answers = await _resolve(domain, "MX")
        verdict = classify_mx_hosts(r.exchange for r in answers)
    except dns.resolver.NXDOMAIN:
        verdict = MX_NONE
    except dns.resolver.NoAnswer:
        # No MX published: RFC 5321 falls back to the A record for delivery.
        try:
            await _resolve(domain, "A")
            verdict = MX_OK
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            verdict = MX_NONE
        except Exception:
            verdict = MX_OK
    except Exception:
        verdict = MX_OK

    if len(_mx_cache) >= _MX_CACHE_MAX:
        _mx_cache.clear()
    _mx_cache[domain] = (verdict, now + _MX_CACHE_TTL)
    return verdict

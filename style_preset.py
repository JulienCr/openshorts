"""The server's default style: one JSON file, read at submit time.

Every clip already ships captioned, but with a look hard-coded in
``subtitles.AUTO_CAPTION_STYLE`` — identical for everyone, and only changeable
per clip, after the render, through a modal. That does not survive being
automated: a batch of thirty recordings, or a job launched by an agent, has
nowhere to carry a look.

So the look lives in ``style.json`` instead, and the pipeline reads it. Two
properties this module exists to guarantee:

- **Read per call, never cached at import.** Editing style.json takes effect on
  the next job, without restarting the container. A module-level constant would
  quietly require a redeploy for a colour change.
- **A broken preset costs nothing.** Missing file, malformed JSON, a list where
  an object belongs: all read as "no preset", and the renderer keeps its
  built-in defaults. Same doctrine as ``auto_caption_clip`` — a styling problem
  must never cost the user a clip they already paid for.

The dict travels to the ``main.py`` subprocess in ONE environment variable
(``OPENSHORTS_STYLE``) rather than fifteen, so it rides ``jobs[job_id]['env']``
next to WATERMARK and the layouts with no extra plumbing.
"""
import json
import os

# Default location, overridable so a deployment can mount the preset elsewhere.
# docker-compose already bind-mounts the repo at /app, so the repo root needs no
# compose change to be writable from the dashboard.
STYLE_FILE_ENV = "OPENSHORTS_STYLE_FILE"
STYLE_ENV = "OPENSHORTS_STYLE"
DEFAULT_STYLE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "style.json")


def style_file_path(path=None):
    """Where the preset lives: explicit argument, then env, then the repo root."""
    return path or os.environ.get(STYLE_FILE_ENV) or DEFAULT_STYLE_FILE


def _as_preset(raw, source):
    """Accept an object, refuse anything else — loudly in the log, quietly to
    the caller. A list or a bare string parses as valid JSON but has no keys to
    merge, and letting it through only moves the failure somewhere less
    obvious."""
    if isinstance(raw, dict):
        return raw
    print(f"⚠️ Ignoring style preset in {source}: expected a JSON object, "
          f"got {type(raw).__name__}.")
    return {}


def load_style(path=None):
    """Read the style preset from disk. Returns {} when there isn't a usable one.

    Called once per submission, never memoised — see the module docstring.
    """
    resolved = style_file_path(path)
    try:
        with open(resolved) as f:
            return _as_preset(json.load(f), resolved)
    except FileNotFoundError:
        # The default install has no style.json. Not a problem, not worth a log
        # line on every single submission.
        return {}
    except (ValueError, OSError) as e:
        print(f"⚠️ Could not read style preset {resolved} ({type(e).__name__}: {e}) "
              f"— using built-in defaults.")
        return {}


def style_env(preset):
    """Environment overrides carrying the preset into the renderer subprocess.

    Shaped like ``layout_env``: an empty preset adds nothing at all, so a job
    without a style is byte-for-byte the job we ran before this existed.
    """
    if not preset:
        return {}
    return {STYLE_ENV: json.dumps(preset)}


# Gemini writes a viral_hook_text for every clip and nothing has ever burned it
# — it waits for someone to open the modal. Turning that into an automatic
# overlay is opt-in: with no preset, "enabled" stays false and a job renders
# byte-for-byte the way it did before any of this existed.
HOOK_DEFAULTS = {
    "enabled": False,
    "style": "classic",
    "position": "top",
    "size": "M",
    "duration_seconds": 3.0,
    "font_path": None,
}


def resolve_hook_style():
    """The automatic-hook settings for this job, defaults filled in.

    ``size`` (the S/M/L the preset and /api/hook both speak) is converted to the
    ``font_scale`` multiplier the renderer wants, so the file keeps one
    vocabulary and the conversion lives in one place.
    """
    from hooks import HOOK_SIZE_SCALE

    hook = dict(HOOK_DEFAULTS)
    configured = preset_from_env().get("hook")
    if isinstance(configured, dict):
        hook.update({k: v for k, v in configured.items() if k in HOOK_DEFAULTS})
    hook["enabled"] = bool(hook["enabled"])
    hook["font_scale"] = HOOK_SIZE_SCALE.get(hook["size"], 1.0)
    return hook


def preset_from_env():
    """The preset as seen from inside the renderer subprocess."""
    raw = os.environ.get(STYLE_ENV, "").strip()
    if not raw:
        return {}
    try:
        return _as_preset(json.loads(raw), f"${STYLE_ENV}")
    except ValueError as e:
        print(f"⚠️ Could not parse ${STYLE_ENV} ({e}) — using built-in defaults.")
        return {}

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


def coerce_like(value, default):
    """Return ``value`` typed like ``default``, or ``default`` when it cannot be.

    The preset is hand-edited JSON and can also arrive inline on a request, so
    nothing guarantees the types. Checking only the top-level shape was not
    enough: a plausible typo — a number written as a string, ``"max_chars": "16"``
    — reached the caption generator, raised when compared against an int, and
    auto_caption_clip swallowed it. Every clip then shipped with no captions at
    all, silently, which is exactly what "a broken preset costs nothing" is
    supposed to prevent.
    """
    # bool before int: isinstance(True, int) is True in Python, and a string
    # like "false" is truthy — reading that as True is the wrong answer.
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, (int, float)):
        if isinstance(value, bool):
            return default
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        if number != number or number in (float("inf"), float("-inf")):
            return default
        return int(number) if isinstance(default, int) else number
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    return value


def coerce_section(configured, defaults):
    """Merge a preset section over its defaults, keeping only known keys and
    only values that can be typed like the default. One bad field must not take
    the good ones down with it."""
    resolved = dict(defaults)
    if isinstance(configured, dict):
        for key, value in configured.items():
            if key in defaults:
                resolved[key] = coerce_like(value, defaults[key])
    return resolved


def resolve_hook_style():
    """The automatic-hook settings for this job, defaults filled in.

    ``size`` (the S/M/L the preset and /api/hook both speak) is converted to the
    ``font_scale`` multiplier the renderer wants, so the file keeps one
    vocabulary and the conversion lives in one place.
    """
    from hooks import HOOK_SIZE_SCALE

    configured = preset_from_env().get("hook")
    hook = coerce_section(configured, HOOK_DEFAULTS)

    # duration_seconds is the one field where None is meaningful ("the whole
    # clip", the same meaning /api/hook gives it) AND where the value ends up
    # interpolated into an ffmpeg filtergraph. /api/hook gets this checked for
    # free from a pydantic float field; nothing types the preset, so a crafted
    # string could otherwise close between(...) and rewrite the graph.
    raw_duration = (configured or {}).get("duration_seconds", HOOK_DEFAULTS["duration_seconds"]) \
        if isinstance(configured, dict) else HOOK_DEFAULTS["duration_seconds"]
    hook["duration_seconds"] = (
        None if raw_duration is None
        else _positive_seconds(raw_duration, HOOK_DEFAULTS["duration_seconds"]))

    hook["font_scale"] = HOOK_SIZE_SCALE.get(hook["size"], 1.0)
    return hook


def _positive_seconds(value, fallback):
    """A finite, non-negative number of seconds, or ``fallback``."""
    if isinstance(value, bool):
        return fallback
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        return fallback
    return number


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

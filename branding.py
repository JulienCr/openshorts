"""Channel branding: a permanent logo lockup burned into every finished clip.

Not the free-plan watermark. ``apply_watermark`` in main.py is a *defensive*
mark — deliberately parked at 40% of the height, inside the picture, so a free
user cannot crop it off (the reasoning is written out above
WATERMARK_WIDTH_RATIO there). This module is the opposite intent: an operator
branding their own clips, who wants the mark to look deliberate and to stay out
of the way. The two are independent flags and compose fine — at y≈13% and y=40%
they never touch.

It is also NOT the hook (``hooks.py``). A hook is a timed, user-written text card
the viewer is meant to read; branding is permanent furniture nobody reads twice.

WHERE THE MARK GOES, AND WHY THAT BAND

On a 9:16 clip three things are already spoken for:

  - the platform's own chrome — TikTok's "Following / For You" tabs and Shorts'
    search icons run down to y≈12% of the height; the right-hand icon rail
    starts around y≈52%; the username / audio / nav block owns the bottom from
    y≈86%
  - burned captions — ``subtitles.SAFE_MARGIN_V`` is 43 in PlayResY=288 units,
    so the caption block lands at roughly y 59%-85%
  - the hook's default "top" position — ``hooks.py`` puts that card at
    ``int(video_height * 0.20)``

The two hard constraints are the platform chrome above and the captions below,
which leaves everything from y≈12% to y≈59%. Y_RATIO=0.13 puts the band just
under the chrome, where a mark reads as a channel bug rather than as part of the
shot. It is the band's TOP edge, not its centre — see the constant.

The hook's top card is the third constraint, and it is what MAX_BAND_HEIGHT_RATIO
exists to respect: widths are a fraction of the clip WIDTH, so how much HEIGHT a
mark eats depends on the aspect ratio of both the frame and the asset, and
neither is ours to pick. The clamp keeps the band inside its strip whatever the
operator supplies and whatever `output_format` delivers.

Horizontally the two marks split the line, logo left and badge right. The right
edge is safe at this height precisely because the icon rail starts far below it.

Off by default (``BRAND_WATERMARK=1``). Turned on with nothing in
``assets/brand/`` it warns once per run, not once per clip — a ten-clip job
should not print the same line ten times.
"""
import os
import subprocess
from collections import namedtuple

from PIL import Image

from ffmpeg_utils import METADATA_SCRUB, QUALITY, video_encode_args

_REPO_DIR = os.path.dirname(os.path.abspath(__file__))

# __file__-relative, not CWD-relative: hooks.py resolves its font dir off the
# working directory and only gets away with it because WORKDIR happens to be
# /app. main.py's watermark does it this way and is the one to copy.
DEFAULT_BRAND_DIR = os.path.join(_REPO_DIR, "assets", "brand")
DEFAULT_LOGO_FILE = "logo.png"
DEFAULT_BADGE_FILE = "twitch.png"

# TOP of the branding band, as a fraction of the clip height — not its centre.
#
# This has to be the top edge, because the band's height depends on the logo's
# aspect ratio, which the operator chooses. Anchoring the centre instead let a
# taller lockup grow upwards into the platform's own top bar: at 0.13 a 3:1 logo
# put its top edge at 0.109, back under TikTok's tabs. The top edge is the
# constraint, so the top edge is what gets pinned.
DEFAULT_Y_RATIO = 0.13

# Same inset as the free-plan watermark, so a clip carrying both reads as one
# design rather than two margins.
DEFAULT_MARGIN_RATIO = 0.05

# Widths as a fraction of the clip width. The logo is smaller than the free-plan
# mark's 0.30 because it is not trying to be hard to remove, and the badge is
# smaller again — it is a handle, not a signature.
DEFAULT_LOGO_WIDTH_RATIO = 0.22
DEFAULT_BADGE_WIDTH_RATIO = 0.16

DEFAULT_OPACITY = 0.85

# Below this a downscaled lockup stops being legible on a phone, so a very small
# clip gets a proportionally larger mark rather than an unreadable one.
MIN_MARK_WIDTH = 80

# Ceiling on the band's height, as a fraction of the clip height.
#
# Widths are a fraction of the clip WIDTH, which is the right axis for how big a
# mark looks — but height is what eats the safe band, and the two are only
# related through the aspect ratio of the frame *and* of the asset. Neither is
# ours to choose: `output_format` also produces 1920x1080 and 1080x1080, and the
# operator picks the logo. Unclamped, a 3:1 logo at 22% width spans 13% of a
# 9:16 frame's height but 26% of a 16:9 one, which walks straight into the hook
# card at 20%. Measured across both frame shapes and both asset shapes, every
# combination except the 9:16 wide lockup collided.
#
# 0.06 is chosen so the intended case — a wide lockup on a vertical clip, 4.1% —
# is untouched, and everything else is scaled down until it fits rather than
# being refused. Scaling beats refusing: a slightly smaller mark is a mark, and
# the alternative on a landscape export is no branding at all.
MAX_BAND_HEIGHT_RATIO = 0.06

Placement = namedtuple("Placement", "path width height x y")

Settings = namedtuple("Settings", "enabled brand_dir logo_file badge_file y_ratio "
                                  "margin_ratio logo_width_ratio badge_width_ratio opacity")

_warned_missing = False


def _env_float(name, default):
    """A malformed ratio must not take the whole job down with a ValueError."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"   ⚠️ {name}={raw!r} is not a number; using {default}.")
        return default


def settings():
    """Read every branding setting from the environment, per call.

    Deliberately NOT frozen at import the way ``punch_in.ENABLED`` is, because
    ``main.py`` is also a CLI: there the import block runs *before*
    ``load_dotenv()``, so an import-time read sees nothing from the `.env` file —
    which is the documented way to turn this on. Freezing left `BRAND_WATERMARK=1`
    in `.env` silently doing nothing on every direct `python main.py` run.

    Reordering that one import would have papered over it, but nothing in the
    file says the order is load-bearing and no comment survives an autoformatter.
    Reading at call time removes the ordering constraint instead of documenting
    it, and costs one dict lookup per clip.
    """
    return Settings(
        enabled=os.environ.get("BRAND_WATERMARK", "0") == "1",
        brand_dir=os.environ.get("BRAND_DIR") or DEFAULT_BRAND_DIR,
        logo_file=os.environ.get("BRAND_LOGO") or DEFAULT_LOGO_FILE,
        badge_file=os.environ.get("BRAND_BADGE") or DEFAULT_BADGE_FILE,
        y_ratio=_env_float("BRAND_Y_RATIO", DEFAULT_Y_RATIO),
        margin_ratio=_env_float("BRAND_MARGIN_RATIO", DEFAULT_MARGIN_RATIO),
        logo_width_ratio=_env_float("BRAND_LOGO_WIDTH_RATIO", DEFAULT_LOGO_WIDTH_RATIO),
        badge_width_ratio=_env_float("BRAND_BADGE_WIDTH_RATIO", DEFAULT_BADGE_WIDTH_RATIO),
        opacity=_env_float("BRAND_OPACITY", DEFAULT_OPACITY),
    )


def _collect_marks(cfg=None):
    """The brand PNGs that actually exist, with their native pixel size.

    Returns ``[(path, (native_w, native_h), width_ratio, align)]``. Either file
    may be absent: a logo with no Twitch badge, or a badge alone, are both
    legitimate setups and each renders on its own.
    """
    cfg = cfg or settings()
    specs = [
        (os.path.join(cfg.brand_dir, cfg.logo_file), cfg.logo_width_ratio, "left"),
        (os.path.join(cfg.brand_dir, cfg.badge_file), cfg.badge_width_ratio, "right"),
    ]
    marks = []
    for path, ratio, align in specs:
        if not os.path.exists(path):
            continue
        try:
            with Image.open(path) as im:
                native = im.size
        except Exception as e:
            print(f"   ⚠️ Brand asset unreadable ({path}): {e}")
            continue
        if native[0] <= 0 or native[1] <= 0:
            continue
        marks.append((path, native, ratio, align))
    return marks


def assets_present(cfg=None):
    """Whether burning branding would actually put anything on screen.

    The API surfaces this so the dashboard can hide a checkbox that could only
    ever be a no-op, rather than letting someone tick it and wonder.
    """
    return bool(_collect_marks(cfg))


def plan_marks(vw, vh, marks, cfg=None):
    """Lay the marks out on the branding line. Pure geometry, no I/O.

    Scaled heights are computed here rather than left to ffmpeg's ``scale=W:-1``
    for two reasons: the plan and the filter graph then cannot disagree, and the
    vertical centring below needs the height anyway.

    Vertically the band hangs from ``cfg.y_ratio``: the tallest mark's top edge
    sits exactly there and the others are centred against it. Two passes are
    needed because that centre line is only known once every height is.

    Centring rather than sharing a top edge matters because the aspect ratios
    differ — a squarish logo beside a wide text badge — and a shared top edge
    leaves the line visibly lopsided. Hanging the whole band from the tallest
    mark keeps that centring while still pinning the one edge that has a hard
    constraint above it.
    """
    cfg = cfg or settings()
    margin = int(vw * cfg.margin_ratio)

    sized = []
    for path, (native_w, native_h), width_ratio, align in marks:
        width = max(MIN_MARK_WIDTH, int(vw * width_ratio))
        # Never wider than the space between the margins, or the mark would be
        # clipped by the frame edge on a very aggressive ratio override.
        width = min(width, max(1, vw - 2 * margin))
        height = max(1, int(round(width * native_h / native_w)))
        sized.append((path, width, height, align))

    # Clamp each mark to the ceiling on its own, not the band as a whole.
    #
    # Scaling every mark by the tallest one's excess was tried first and is
    # worse: it keeps the marks' relative sizes, but a square logo then drags
    # the badge down with it — measured with the real assets, the handle came
    # out 83x19 on a 1080x1920 clip, which is not readable on a phone. Only the
    # mark that actually breaks the band needs to shrink; the others already fit
    # and there is nothing to gain by touching them.
    #
    # Applied after the MIN_MARK_WIDTH floor and allowed to win over it: an
    # unreadably small mark is a cosmetic problem, a mark printed over the hook
    # card is a broken frame.
    ceiling = vh * MAX_BAND_HEIGHT_RATIO
    sized = [
        (path, w, h, align) if h <= ceiling else
        (path, max(1, int(w * ceiling / h)), max(1, int(ceiling)), align)
        for path, w, h, align in sized
    ]

    band_top = int(vh * cfg.y_ratio)
    centre = band_top + max(h for _, _, h, _ in sized) / 2

    placements = []
    for path, width, height, align in sized:
        x = margin if align == "left" else vw - margin - width
        y = int(centre - height / 2)
        placements.append(Placement(path, width, height, max(0, x), max(0, y)))
    return placements


def marks_collide(placements):
    """True when two marks share horizontal space and would print over each other.

    Cannot happen at the default ratios (0.22 + 0.16 + two 0.05 margins leaves
    half the width free) — this only catches an operator's override.
    """
    spans = sorted((p.x, p.x + p.width) for p in placements)
    return any(spans[i][1] > spans[i + 1][0] for i in range(len(spans) - 1))


def build_filter(placements, opacity=None):
    """filter_complex chaining one overlay per mark onto the clip.

    Input 0 is the clip; each mark is input N+1, in the order given. Widths are
    explicit because ``overlay`` cannot read the other input's size and the
    ``scale2ref`` that used to solve this is deprecated.
    """
    opacity = settings().opacity if opacity is None else opacity
    scales, overlays = [], []
    current = "0:v"
    for i, p in enumerate(placements):
        scales.append(f"[{i + 1}:v]scale={p.width}:{p.height},format=rgba,"
                      f"colorchannelmixer=aa={opacity}[m{i}]")
        # The last overlay leaves its output unlabelled, which is what makes it
        # the filtergraph's implicit output.
        label = f"[b{i}]" if i < len(placements) - 1 else ""
        overlays.append(f"[{current}][m{i}]overlay=x={p.x}:y={p.y}{label}")
        current = f"b{i}"
    return ";".join(scales + overlays)


def _remove_quietly(path):
    """Drop a half-written temp file. Never the reason a job fails."""
    try:
        os.remove(path)
    except OSError:
        pass


def probe_size(video_path):
    """(width, height) of the clip, or None.

    Never assume 1080x1920: ``reframe_v2.delivery_size()`` does not downscale, so
    a 4K source is delivered at 2160x3840 and every ratio here has to follow.
    """
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
             video_path],
            stderr=subprocess.STDOUT, timeout=60,
        ).decode().strip().split("x")
        return int(out[0]), int(out[1])
    except Exception as e:
        print(f"   ⚠️ Could not probe clip for branding ({e}); clip left unbranded.")
        return None


def apply_branding(video_path, cfg=None):
    """Burn the channel branding into a finished clip. One re-encode pass.

    Self-gating on the env flag so both call sites in main.py stay a single line
    and neither can forget the check. Fails soft everywhere: a missing asset, an
    unprobeable clip or an ffmpeg error leaves the clip unbranded rather than
    failing the job — an unbranded clip is still a deliverable clip.
    """
    global _warned_missing
    cfg = cfg or settings()
    if not cfg.enabled:
        return False

    marks = _collect_marks(cfg)
    if not marks:
        if not _warned_missing:
            _warned_missing = True
            print(f"   ⚠️ BRAND_WATERMARK=1 but no brand asset in {cfg.brand_dir} "
                  f"(expected {cfg.logo_file} and/or {cfg.badge_file}); "
                  f"clips left unbranded.")
        return False

    size = probe_size(video_path)
    if not size:
        return False
    vw, vh = size

    placements = plan_marks(vw, vh, marks, cfg)
    if marks_collide(placements):
        print("   ⚠️ Brand marks overlap at these ratios — lower "
              "BRAND_LOGO_WIDTH_RATIO or BRAND_BADGE_WIDTH_RATIO.")

    tmp_path = video_path + ".brand.mp4"
    cmd = ["ffmpeg", "-y", "-i", video_path]
    # Input order has to match build_filter's [1:v], [2:v], ... labels.
    for p in placements:
        cmd += ["-i", p.path]
    cmd += ["-filter_complex", build_filter(placements, cfg.opacity),
            *video_encode_args(QUALITY), "-c:a", "copy", *METADATA_SCRUB,
            "-movflags", "+faststart", tmp_path]

    # subprocess.run RAISES on timeout rather than returning, and this function
    # is only fail-soft if nothing escapes it. A --skip-analysis run on a long
    # source, or a slow 4K clip, can genuinely pass 1800s; letting TimeoutExpired
    # out would fail a clip that had already rendered fine, skip its captions,
    # and leave the .brand.mp4 behind.
    try:
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, timeout=1800)
    except Exception as e:
        print(f"   ⚠️ Branding pass errored (clip left unbranded): "
              f"{type(e).__name__}: {e}")
        _remove_quietly(tmp_path)
        return False

    if result.returncode == 0 and os.path.exists(tmp_path):
        os.replace(tmp_path, video_path)
        print(f"   🏷️  Branding applied ({len(placements)} mark(s)).")
        return True

    err = (result.stderr or b"").decode(errors="ignore")[-300:]
    print(f"   ⚠️ Branding pass failed (clip left unbranded): {err}")
    _remove_quietly(tmp_path)
    return False

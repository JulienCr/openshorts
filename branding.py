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

The hook is a softer third constraint. At the default ratios a wide lockup
(3:1 or flatter) also clears the hook's top card, which is the nice case. A tall
or square logo at 22% width is ~24% of the height on its own and cannot clear
both, so it will sit behind a `top` hook — one more reason the asset should be a
wide lockup, as assets/brand/README.md says.

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

ENABLED = os.environ.get("BRAND_WATERMARK", "0") == "1"

_REPO_DIR = os.path.dirname(os.path.abspath(__file__))

# __file__-relative, not CWD-relative: hooks.py resolves its font dir off the
# working directory and only gets away with it because WORKDIR happens to be
# /app. main.py's watermark does it this way and is the one to copy.
BRAND_DIR = os.environ.get("BRAND_DIR") or os.path.join(_REPO_DIR, "assets", "brand")
LOGO_FILE = os.environ.get("BRAND_LOGO", "logo.png")
BADGE_FILE = os.environ.get("BRAND_BADGE", "twitch.png")

# TOP of the branding band, as a fraction of the clip height — not its centre.
#
# This has to be the top edge, because the band's height depends on the logo's
# aspect ratio, which the operator chooses. Anchoring the centre instead let a
# taller lockup grow upwards into the platform's own top bar: at 0.13 a 3:1 logo
# put its top edge at 0.109, back under TikTok's tabs. The top edge is the
# constraint, so the top edge is what gets pinned.
Y_RATIO = float(os.environ.get("BRAND_Y_RATIO", "0.13"))

# Same inset as the free-plan watermark, so a clip carrying both reads as one
# design rather than two margins.
MARGIN_RATIO = float(os.environ.get("BRAND_MARGIN_RATIO", "0.05"))

# Widths as a fraction of the clip width. The logo is smaller than the free-plan
# mark's 0.30 because it is not trying to be hard to remove, and the badge is
# smaller again — it is a handle, not a signature.
LOGO_WIDTH_RATIO = float(os.environ.get("BRAND_LOGO_WIDTH_RATIO", "0.22"))
BADGE_WIDTH_RATIO = float(os.environ.get("BRAND_BADGE_WIDTH_RATIO", "0.16"))

OPACITY = float(os.environ.get("BRAND_OPACITY", "0.85"))

# Below this a downscaled lockup stops being legible on a phone, so a very small
# clip gets a proportionally larger mark rather than an unreadable one.
MIN_MARK_WIDTH = 80

Placement = namedtuple("Placement", "path width height x y")

_warned_missing = False


def _collect_marks():
    """The brand PNGs that actually exist, with their native pixel size.

    Returns ``[(path, (native_w, native_h), width_ratio, align)]``. Either file
    may be absent: a logo with no Twitch badge, or a badge alone, are both
    legitimate setups and each renders on its own.
    """
    specs = [
        (os.path.join(BRAND_DIR, LOGO_FILE), LOGO_WIDTH_RATIO, "left"),
        (os.path.join(BRAND_DIR, BADGE_FILE), BADGE_WIDTH_RATIO, "right"),
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


def assets_present():
    """Whether burning branding would actually put anything on screen.

    The API surfaces this so the dashboard can hide a checkbox that could only
    ever be a no-op, rather than letting someone tick it and wonder.
    """
    return bool(_collect_marks())


def plan_marks(vw, vh, marks):
    """Lay the marks out on the branding line. Pure geometry, no I/O.

    Scaled heights are computed here rather than left to ffmpeg's ``scale=W:-1``
    for two reasons: the plan and the filter graph then cannot disagree, and the
    vertical centring below needs the height anyway.

    Vertically the band hangs from Y_RATIO: the tallest mark's top edge sits
    exactly there and the others are centred against it. Two passes are needed
    because that centre line is only known once every height is.

    Centring rather than sharing a top edge matters because the aspect ratios
    differ — a squarish logo beside a wide text badge — and a shared top edge
    leaves the line visibly lopsided. Hanging the whole band from the tallest
    mark keeps that centring while still pinning the one edge that has a hard
    constraint above it.
    """
    margin = int(vw * MARGIN_RATIO)

    sized = []
    for path, (native_w, native_h), width_ratio, align in marks:
        width = max(MIN_MARK_WIDTH, int(vw * width_ratio))
        # Never wider than the space between the margins, or the mark would be
        # clipped by the frame edge on a very aggressive ratio override.
        width = min(width, max(1, vw - 2 * margin))
        height = max(1, int(round(width * native_h / native_w)))
        sized.append((path, width, height, align))

    band_top = int(vh * Y_RATIO)
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
    opacity = OPACITY if opacity is None else opacity
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


def apply_branding(video_path):
    """Burn the channel branding into a finished clip. One re-encode pass.

    Self-gating on ENABLED so both call sites in main.py stay a single line and
    neither can forget the check. Fails soft everywhere: a missing asset, an
    unprobeable clip or an ffmpeg error leaves the clip unbranded rather than
    failing the job — an unbranded clip is still a deliverable clip.
    """
    global _warned_missing
    if not ENABLED:
        return False

    marks = _collect_marks()
    if not marks:
        if not _warned_missing:
            _warned_missing = True
            print(f"   ⚠️ BRAND_WATERMARK=1 but no brand asset in {BRAND_DIR} "
                  f"(expected {LOGO_FILE} and/or {BADGE_FILE}); clips left unbranded.")
        return False

    size = probe_size(video_path)
    if not size:
        return False
    vw, vh = size

    placements = plan_marks(vw, vh, marks)
    if marks_collide(placements):
        print("   ⚠️ Brand marks overlap at these ratios — lower "
              "BRAND_LOGO_WIDTH_RATIO or BRAND_BADGE_WIDTH_RATIO.")

    tmp_path = video_path + ".brand.mp4"
    cmd = ["ffmpeg", "-y", "-i", video_path]
    # Input order has to match build_filter's [1:v], [2:v], ... labels.
    for p in placements:
        cmd += ["-i", p.path]
    cmd += ["-filter_complex", build_filter(placements),
            *video_encode_args(QUALITY), "-c:a", "copy", *METADATA_SCRUB,
            "-movflags", "+faststart", tmp_path]

    result = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, timeout=1800)
    if result.returncode == 0 and os.path.exists(tmp_path):
        os.replace(tmp_path, video_path)
        print(f"   🏷️  Branding applied ({len(placements)} mark(s)).")
        return True

    err = (result.stderr or b"").decode(errors="ignore")[-300:]
    print(f"   ⚠️ Branding pass failed (clip left unbranded): {err}")
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    return False

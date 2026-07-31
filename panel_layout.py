"""PANEL layout: three or four people from one wide shot, tiled into 9:16.

The generalisation of split_layout past two people. A round table, a debate set
or a podcast with guests is a single wide shot where TRACK picks one person and
GENERAL shrinks everyone to a strip. OpusClip ships "Three" and "Four" layouts
for exactly this and we had nothing.

Geometry, for a 1080x1920 output:

    3 people   three stacked bands, 1080x640 each
    4 people   a 2x2 grid, 540x960 each

Three stacked rather than 2+1, because an odd tile reads as a mistake; four as a
grid rather than four bands, because a 1080x480 band is a letterbox slot nobody
fits in. Both keep every tile the same size: a bigger tile silently claims the
person in it is the important one, and the layout has no way to know that.

Off by default (``PANEL_LAYOUT=1``). Shares split_layout's rules, because the
failure modes are the same ones: faces must COEXIST in the frame (otherwise it
is a multi-camera cut and tiling would show one person several times), be far
enough apart, and be big enough not to be an audience.
"""
import os

import numpy as np

import split_layout

ENABLED = os.environ.get("PANEL_LAYOUT", "0") == "1"

# How many people this layout will lay out. Five-plus is a crowd: at 1080 wide
# each tile drops under 400px tall and nobody is legible, so those scenes are
# better served by GENERAL keeping the room intact.
MIN_PEOPLE = 3
MAX_PEOPLE = 4

# Same bar as SPLIT: the tiles must be showing different people at the same
# moment, not the same person across cuts.
MIN_COEXISTENCE = split_layout.MIN_COEXISTENCE
MIN_FACE_WIDTH = split_layout.MIN_FACE_WIDTH
MIN_SCENE_SECONDS = split_layout.MIN_SCENE_SECONDS

# Minimum gap between neighbouring faces, as a fraction of frame width. Lower
# than SPLIT's 0.20 because four people in one shot genuinely sit closer
# together; below this they overlap and the tiles repeat shoulders.
MIN_SEPARATION = 0.12

PANEL_TIGHTNESS = float(os.environ.get("PANEL_TIGHTNESS", "0.85"))


def tile_grid(count, out_w, out_h):
    """(columns, rows, tile_w, tile_h) for this many people."""
    if count == 3:
        cols, rows = 1, 3
    else:
        cols, rows = 2, 2
    tile_w = out_w // cols
    tile_h = out_h // rows
    return cols, rows, tile_w - (tile_w % 2), tile_h - (tile_h % 2)


def usable_faces(candidates, frame_w):
    """Faces big enough to be participants, deduped, left to right.

    Deduping because this layout decides by headcount and nothing upstream
    guarantees one box per head.
    """
    big = [c for c in split_layout.dedupe_faces(candidates)
           if c['box'][2] >= MIN_FACE_WIDTH * frame_w]
    big.sort(key=lambda c: c['box'][0])
    return big


def well_separated(faces, frame_w, min_gap=MIN_SEPARATION):
    """True when every pair of faces is far enough apart to be distinct people.

    Distance between centres, not horizontal gap. Participants are not always
    in a row: a shot measured on a real recording had two people stacked one
    above the other on the same side of the frame, 18px apart horizontally and
    545px vertically. Judging by horizontal gap alone called them the same
    person and threw the scene away.
    """
    threshold = min_gap * frame_w
    for i, a in enumerate(faces):
        ax = a['box'][0] + a['box'][2] / 2.0
        ay = a['box'][1] + a['box'][3] / 2.0
        for b in faces[i + 1:]:
            bx = b['box'][0] + b['box'][2] / 2.0
            by = b['box'][1] + b['box'][3] / 2.0
            if ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 < threshold:
                return False
    return True


def analyze_scene(frames, frame_w):
    """Median centres for a stable panel of 3-4 people, or None.

    ``frames`` is a list of per-frame candidate lists, as
    ``main.detect_face_candidates`` returns them.
    """
    if not frames:
        return None

    per_count = {}
    for candidates in frames:
        faces = usable_faces(candidates, frame_w)
        if not (MIN_PEOPLE <= len(faces) <= MAX_PEOPLE):
            continue
        if not well_separated(faces, frame_w):
            continue
        per_count.setdefault(len(faces), []).append(
            [split_layout._centre(c['box']) for c in faces])

    if not per_count:
        return None

    # Take the headcount that held for longest; a frame where one head turned
    # away should not decide the layout.
    count = max(per_count, key=lambda k: len(per_count[k]))
    seen = per_count[count]
    if len(seen) < MIN_COEXISTENCE * len(frames):
        return None

    arr = np.array(seen, dtype=float)          # (samples, people, 4)
    return [tuple(float(v) for v in person) for person in np.median(arr, axis=0)]


def panel_geometry(orig_w, orig_h, tile_w, tile_h, centre, tightness=None):
    """Crop (w, h, x, y) framing one person for one tile."""
    tightness = PANEL_TIGHTNESS if tightness is None else tightness
    aspect = tile_w / float(tile_h)

    crop_h = int(round(orig_h * max(0.3, min(tightness, 1.0))))
    crop_w = int(round(crop_h * aspect))
    if crop_w > orig_w:
        crop_w = orig_w
        crop_h = int(round(crop_w / aspect))
    crop_w -= crop_w % 2
    crop_h -= crop_h % 2

    cx, cy = centre[0], centre[1]
    x = max(0, min(int(round(cx - crop_w / 2.0)), orig_w - crop_w))
    y = max(0, min(int(round(cy - crop_h * 0.42)), orig_h - crop_h))
    return crop_w, crop_h, x - (x % 2), y - (y % 2)


def panel_filtergraph(orig_w, orig_h, out_w, out_h, centres):
    """Tile 3 or 4 people into the vertical frame, left-to-right, top-to-bottom."""
    count = len(centres)
    cols, rows, tile_w, tile_h = tile_grid(count, out_w, out_h)

    parts = [f"[0:v]split={count}" + "".join(f"[s{i}]" for i in range(count)) + ";"]
    for i, centre in enumerate(centres):
        cw, ch, cx, cy = panel_geometry(orig_w, orig_h, tile_w, tile_h, centre)
        parts.append(f"[s{i}]crop=w={cw}:h={ch}:x={cx}:y={cy},"
                     f"scale={tile_w}:{tile_h}[t{i}];")

    if count == 3:
        parts.append("[t0][t1][t2]vstack=inputs=3,")
    else:
        parts.append("[t0][t1]hstack=inputs=2[top];"
                     "[t2][t3]hstack=inputs=2[bot];"
                     "[top][bot]vstack=inputs=2,")
    parts.append(f"pad={out_w}:{out_h}:0:0,setsar=1[v]")
    return "".join(parts)


def detect_panel_scenes(video_path, scenes, strategies, samples=None):
    """Scenes to render as PANEL: ``{scene_index: [centres]}``.

    Only scenes the classifier already sent to GENERAL are considered, for the
    same reason SPLIT does it: GENERAL already means "more than one face", and
    single-speaker material is most of the corpus and must not be touched.
    """
    import cv2
    import main as m

    if not ENABLED:
        return {}

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {}

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    found = {}

    try:
        for i, (start, end) in enumerate(scenes):
            if i < len(strategies) and strategies[i] != 'GENERAL':
                continue
            s_f, e_f = start.get_frames(), end.get_frames()
            duration = (e_f - s_f) / fps
            if duration < MIN_SCENE_SECONDS:
                continue

            n = samples or int(min(max(duration / split_layout.SECONDS_PER_SAMPLE,
                                       split_layout.MIN_SAMPLES),
                                   split_layout.MAX_SAMPLES))
            last_f = e_f - 1
            if total_frames:
                last_f = min(last_f, total_frames - 1)
            if last_f < s_f:
                continue

            sampled = []
            for f_idx in np.linspace(s_f, last_f, n):
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(f_idx)))
                ok, frame = cap.read()
                if not ok:
                    continue
                if frame.mean() < 16:
                    continue
                sampled.append(m.detect_face_candidates(frame))

            if len(sampled) < max(4, n // 2):
                continue

            centres = analyze_scene(sampled, frame_w)
            if centres:
                found[i] = centres
    finally:
        cap.release()

    return found

"""Reframe engine v2: analyze in Python, render natively in ffmpeg.

v1 decodes every frame at full resolution in OpenCV, crops/resizes in numpy
and pipes raw frames back into ffmpeg. v2 splits that into:

  1. ANALYSIS — one ffmpeg-decoded pass at <=640px feeding the same detectors
     and the same SmoothedCameraman/SpeakerTracker state machines as v1, so
     the resulting camera trajectory (crop x per frame) is equivalent.
  2. RENDER — one ffmpeg process per scene doing decode -> dynamic crop
     (sendcmd) -> scale -> encode natively (TRACK scenes), or the blurred
     background filtergraph (GENERAL scenes); segments are then concatenated
     with stream copy and the audio mapped straight from the source clip.

No raw-frame piping, no second full-res decode, one less intermediate encode.
Callers must treat any exception as "fall back to the v1 loop".

Pure helpers (sendcmd/concat generation, scene slicing) have no heavy imports
so they stay unit-testable in CI.
"""
import os
import subprocess
import tempfile

import active_speaker
import camera_inset
import punch_in
import screencast_layout
import split_layout
from ffmpeg_utils import video_encode_args, QUALITY_FAST, METADATA_SCRUB

ANALYSIS_MAX_WIDTH = 640


# Short-form platforms (TikTok / Reels / Shorts) expect a 1080-wide vertical
# upload; anything smaller is treated as low quality and re-encoded from the
# already-soft source. The crop region is whatever the source height allows, so
# a 720p input yields a 406x720 crop — we scale that up to the delivery floor
# rather than shipping sub-HD. Sources that already exceed it are left alone
# (never downscale quality the user supplied).
DELIVERY_MIN_WIDTH = 1080


# --- pure helpers (CI-testable) --------------------------------------------

def delivery_size(orig_w, orig_h, aspect_ratio):
    """Output (width, height) for a reframe of this source.

    Picks the largest crop the source allows, then upscales to
    ``DELIVERY_MIN_WIDTH`` if that crop is narrower. Both dimensions come back
    even (x264/NVENC reject odd ones).
    """
    out_h = orig_h
    out_w = int(out_h * aspect_ratio)
    if out_w > orig_w:
        out_w = orig_w
        out_h = int(out_w / aspect_ratio)

    if out_w < DELIVERY_MIN_WIDTH:
        out_w = DELIVERY_MIN_WIDTH
        out_h = int(round(out_w / aspect_ratio))

    return out_w + (out_w % 2), out_h + (out_h % 2)


def dedupe_sendcmd_lines(xs, fps, target="crop@c"):
    """sendcmd lines setting crop x per frame, deduped to change-points.

    Timestamps are relative to the segment (the render seeks per scene).
    """
    lines = []
    prev = None
    for i, x in enumerate(xs):
        if x != prev:
            lines.append(f"{i / fps:.4f} {target} x {x};")
            prev = x
    return lines


def scene_frame_ranges(scene_boundaries, strategies, total_frames):
    """Clamp scene (start, end) frame ranges to the decoded frame count,
    dropping empty ranges. Each range keeps its strategy so later indices
    can't misalign when a range is dropped."""
    ranges = []
    for i, (start_f, end_f) in enumerate(scene_boundaries):
        strategy = strategies[i] if i < len(strategies) else 'TRACK'
        start_f = max(0, min(start_f, total_frames))
        end_f = max(start_f, min(end_f, total_frames))
        if end_f > start_f:
            ranges.append((start_f, end_f, strategy))
    return ranges


def concat_list_content(segment_paths):
    # Single quotes per concat-demuxer spec; our paths are tempfile-generated
    # (no quotes in them).
    return "".join(f"file '{p}'\n" for p in segment_paths)


# How much of the frame height the real content should fill in GENERAL layout.
#
# Fitting a 16:9 source to the full output width leaves it 608px tall in a
# 1920px frame — the content is 32% of the screen and 68% is blurred filler.
# That reads as a thumbnail floating in soup, and it is what a GENERAL scene
# looked like in real delivered clips (audited 26-jul-2026).
#
# Scaling the content up and letting the sides overflow trades width for
# presence, and the trade has to stay conservative: GENERAL is chosen for group
# shots and landscapes, exactly the material where cropping the sides cuts
# someone out of frame. At 0.42 a 16:9 source keeps ~76% of its width while
# going from 32% to 42% of the frame height. 0.55 was tried and rejected — it
# reaches 55% height but throws away 42% of the width.
#
# GENERAL_CONTENT_HEIGHT_RATIO=0.32 restores the old full-width behaviour.
GENERAL_CONTENT_HEIGHT_RATIO = float(
    os.environ.get("GENERAL_CONTENT_HEIGHT_RATIO", "0.42"))


def full_width_content_height(orig_w, orig_h, out_w):
    """Height the source fills when its FULL width is kept (even)."""
    fg_h = int(round(out_w * orig_h / float(orig_w)))
    return fg_h + (fg_h % 2)


def general_filtergraph(out_w, out_h, content_h=None):
    """Blurred-background 'general shot' layout: bg fills the frame (centre-
    cropped, blurred), fg is scaled to a readable share of the height and
    centred, overflowing the sides rather than floating small in the middle.

    ``content_h`` overrides the height ratio. Passing the full-width height
    turns the side-cropping off entirely, which is what a scene full of charts
    or spreadsheets needs: the default 0.42 ratio buys presence by throwing away
    ~24% of the width, and on that material the discarded columns are the point.
    """
    fg_h = content_h if content_h else int(out_h * GENERAL_CONTENT_HEIGHT_RATIO)
    fg_h += fg_h % 2
    return (
        f"[0:v]split=2[bga][fga];"
        f"[bga]scale=-2:{out_h},crop=w=min(iw\\,{out_w}):h={out_h},"
        f"scale={out_w}:{out_h},gblur=sigma=12[bg];"
        # Scale by HEIGHT, then trim any overflow to the output width. crop
        # centres by default, and min() makes it a no-op when the scaled source
        # is already narrower than the frame (portrait/square sources).
        f"[fga]scale=-2:{fg_h},crop=w=min(iw\\,{out_w}):h=ih[fg];"
        f"[bg][fg]overlay=x=(W-w)/2:y=(H-h)/2,setsar=1[v]"
    )


# --- delivery variants ------------------------------------------------------

# A clip can be delivered more than once, in different framings. "auto" is the
# pipeline's own verdict (TRACK/GENERAL/SPLIT per scene); "safe" is the whole
# 16:9 frame on a blurred background with a camera that never moves.
#
# The point of "safe" is to be the version that cannot be wrong. Every failure
# mode the auto framing has — hunting left-right, following the wrong face,
# stacking a silent listener — comes from choosing something per scene. This
# chooses nothing, so a bad auto render costs a re-pick rather than a re-run.
VARIANT_AUTO = "auto"
VARIANT_SAFE = "safe"
VARIANTS = (VARIANT_AUTO, VARIANT_SAFE)


def parse_variants(raw):
    """Ordered, deduped, validated variant list. ``auto`` is never droppable.

    Accepts a comma-separated string, a list, or None. Unknown names are
    ignored rather than rejected, matching how ``layouts`` already behaves:
    a typo should cost the caller the feature, not the job.
    """
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, (list, tuple)):
        raw = []
    asked = {str(v).strip().lower() for v in raw}
    picked = [v for v in VARIANTS if v in asked]
    if VARIANT_AUTO not in picked:
        picked.insert(0, VARIANT_AUTO)
    return tuple(picked)


def variant_filename(clip_filename, variant):
    """``Talk_clip_3.mp4`` + ``safe`` -> ``Talk_clip_3_safe.mp4``.

    A SUFFIX, never a prefix. ``_canonical_clip_file`` and
    ``_strip_burned_captions`` in app.py rebuild the clean name from the
    ``subtitled_<ts>_`` PREFIX, and the caption glob for the auto variant
    requires the name to END in ``_<base>_clip_<n>.mp4``. Suffixing keeps the
    two variants in disjoint namespaces for free; prefixing would put the safe
    file inside the auto variant's glob.
    """
    stem, dot, ext = clip_filename.rpartition(".")
    if variant == VARIANT_AUTO or not dot:
        return clip_filename
    return f"{stem}_{variant}{dot}{ext}"


def clear_stale_variants(output_dir, clip_filename):
    """Drop a previous run's variant files for this clip index.

    Variant files are resolved from the clip INDEX, and re-analysing the same
    source can return a different number of clips — so a
    ``<base>_clip_7_safe.mp4`` left by a longer run would otherwise surface as
    a variant of a clip that was never rendered with one.

    The burned-in copies go too. Dropping only the pristine file already hides
    the variant (app.py tests existence on the pristine name), but it leaves
    every ``subtitled_<ts>_<clip>_safe.mp4`` on disk with nothing able to reach
    it again — dead weight on the one deployment where the size cap deletes
    whole projects for good. Found by re-running a job without the flag and
    listing the directory, not by reasoning about it.
    """
    import glob

    removed = []
    for variant in VARIANTS:
        if variant == VARIANT_AUTO:
            continue
        name = variant_filename(clip_filename, variant)
        candidates = [os.path.join(output_dir, name)]
        candidates += glob.glob(os.path.join(output_dir, f"subtitled_*_{name}"))
        for path in candidates:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    removed.append(os.path.basename(path))
            except OSError as e:
                print(f"   ⚠️ Could not remove stale variant {path}: {e}")
    return removed


def _probe_dimensions(video_path):
    """(width, height) of the first video stream, via ffprobe. Raises on failure.

    Deliberately not ``main.get_video_resolution``: that one goes through cv2
    and, worse, forces ``import main``, which pulls in mediapipe/torch and is
    exactly what keeps the existing camera tests skipped in CI. ffprobe is
    already a hard dependency of every render path here.
    """
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", video_path],
        stderr=subprocess.STDOUT, timeout=60,
    ).decode().strip().splitlines()[0].split("x")
    return int(out[0]), int(out[1])


def general_render_cmd(input_video, output_video, out_w, out_h, content_h):
    """argv for the safe variant. Split out so CI can assert on it without ffmpeg."""
    return [
        "ffmpeg", "-y", "-loglevel", "error", "-i", input_video,
        "-filter_complex", general_filtergraph(out_w, out_h, content_h),
        "-map", "[v]",
        # The ? matters: a silent source must still render. Audio is copied
        # because the clip cut already encoded it with loudnorm applied.
        "-map", "0:a:0?", "-c:a", "copy",
        *video_encode_args(QUALITY_FAST), *METADATA_SCRUB,
        "-movflags", "+faststart",
        output_video,
    ]


def render_general(input_video, final_output_video, aspect_ratio):
    """Render one clip as the safe variant: one ffmpeg pass, zero analysis.

    Deliberately NOT a flag threaded through ``render()``. Three reasons:

    - ``render()`` has already paid ``detect_scenes`` and
      ``analyze_scenes_strategy`` before it reaches the segment loop, and the
      latter is MediaPipe inference serialised behind ``main.DETECT_LOCK``.
      This variant needs no detection at all, so going through there would
      queue it behind the auto renders of the other clip workers for nothing.
    - ``process_video_to_vertical`` swallows any v2 exception and silently
      falls back to the v1 frame loop, which would not know about the flag —
      the "safe" variant could then ship a tracked render with no signal.
    - Reading the layout globals (``split_layout.ENABLED`` and friends) is a
      race: ``layout_picker.apply()`` mutates them process-wide while three
      clips render in threads.

    Reading no flag at all makes "the camera never moves" a structural
    property rather than a conditional one.

    ``full_width_content_height`` (not the default 0.42 ratio) is what makes
    this keep the WHOLE frame: the default GENERAL crops the sides and throws
    away ~24% of the width to buy presence, which is exactly the "nothing is
    ever cut off" guarantee this variant exists to make.
    """
    orig_w, orig_h = _probe_dimensions(input_video)
    out_w, out_h = delivery_size(orig_w, orig_h, aspect_ratio)
    content_h = full_width_content_height(orig_w, orig_h, out_w)
    _run(general_render_cmd(input_video, final_output_video, out_w, out_h, content_h))
    print(f"   ✅ Safe variant saved to {final_output_video}")
    return True


# --- analysis ---------------------------------------------------------------

def _analyze_trajectory(input_video, scenes_boundaries, scene_strategies,
                        fps, orig_w, orig_h, cameraman, tracker):
    """Replays v1's per-frame decision loop on a downscaled ffmpeg-decoded
    stream. Returns xs: crop x per frame (None on GENERAL frames)."""
    import numpy as np
    import main as m

    small_w = min(ANALYSIS_MAX_WIDTH, orig_w)
    if small_w % 2:
        small_w -= 1
    small_h = max(int(orig_h * small_w / orig_w), 2)
    if small_h % 2:
        small_h += 1
    scale = orig_w / small_w
    frame_bytes = small_w * small_h * 3

    proc = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-i", input_video,
         "-vf", f"scale={small_w}:{small_h}",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=frame_bytes * 4)

    xs = []
    frame_number = 0
    current_scene_index = 0
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape((small_h, small_w, 3))

            if current_scene_index < len(scenes_boundaries):
                start_f, end_f = scenes_boundaries[current_scene_index]
                if frame_number >= end_f and current_scene_index < len(scenes_boundaries) - 1:
                    current_scene_index += 1

            strategy = (scene_strategies[current_scene_index]
                        if current_scene_index < len(scene_strategies) else 'TRACK')

            # SPLIT, SCREENCAST and WIDE crops are static (fixed boxes for the
            # whole scene), so like GENERAL they need no camera trajectory.
            # ALTERNATE gets one written in after this pass.
            if strategy in ('GENERAL', 'SPLIT', 'SCREENCAST', 'WIDE',
                            'INSET', 'ALTERNATE'):
                cameraman.current_center_x = orig_w / 2
                cameraman.target_center_x = orig_w / 2
                xs.append(None)
            else:
                if frame_number % m.DETECT_STRIDE == 0:
                    candidates = m.detect_face_candidates(frame)
                    for cand in candidates:
                        cand['box'] = [int(v * scale) for v in cand['box']]
                        cand['score'] = cand['box'][2] * cand['box'][3]
                    target_box = tracker.get_target(candidates, frame_number, orig_w)
                    if target_box:
                        cameraman.update_target(target_box)
                    elif frame_number % m.YOLO_FALLBACK_STRIDE == 0:
                        person_box = m.detect_person_yolo(frame)
                        if person_box:
                            cameraman.update_target([int(v * scale) for v in person_box])

                is_scene_start = (
                    current_scene_index < len(scenes_boundaries)
                    and frame_number == scenes_boundaries[current_scene_index][0])
                x1, _y1, _x2, _y2 = cameraman.get_crop_box(force_snap=is_scene_start)
                xs.append(x1)

            frame_number += 1
    finally:
        proc.stdout.close()
        proc.wait()

    return xs


# --- render -----------------------------------------------------------------

def _run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.PIPE, timeout=1800)


def render(input_video, final_output_video, aspect_ratio, content_ranges=None):
    """Full v2 reframe of one clip. Raises on failure (caller falls back).

    ``content_ranges`` comes from screencast_layout.detect_content_ranges() on
    the SOURCE video, already translated into this clip's timeline. None or []
    means the layout never triggers, which is the default.
    """
    import main as m
    content_ranges = content_ranges or []

    print("   🚀 Reframe engine v2 (ffmpeg-native render)")
    scenes, fps = m.detect_scenes(input_video)
    fps = float(fps)  # PySceneDetect can hand back a Fraction
    orig_w, orig_h = m.get_video_resolution(input_video)

    out_w, out_h = delivery_size(orig_w, orig_h, aspect_ratio)

    if not scenes:
        import cv2
        cap = cv2.VideoCapture(input_video)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        from scenedetect import FrameTimecode
        scenes = [(FrameTimecode(0, fps), FrameTimecode(total, fps))]

    scene_boundaries = [(s.get_frames(), e.get_frames()) for s, e in scenes]
    strategies = m.analyze_scenes_strategy(input_video, scenes)

    # SPLIT is an upgrade applied on top of the TRACK/GENERAL verdict, keyed by
    # the scene's START FRAME rather than its index: scene_frame_ranges() drops
    # empty ranges, so indices there don't line up with `scenes`. A surviving
    # range always keeps its original start_f (the clamp only bites on scenes
    # that begin past the last decoded frame, and those get dropped).
    splits = {}
    split_scene_of = {}
    for scene_idx, centres in split_layout.detect_split_scenes(
            input_video, scenes, strategies).items():
        strategies[scene_idx] = 'SPLIT'
        start_f = scene_boundaries[scene_idx][0]
        splits[start_f] = centres
        split_scene_of[start_f] = scene_idx

    # Geometry alone will stack a scene where one person never speaks. Ask who
    # is actually talking before spending half the frame on the other one.
    alternates = {}
    if splits and active_speaker.ENABLED:
        for start_f in list(splits):
            scene_idx = split_scene_of[start_f]
            end_f = scene_boundaries[scene_idx][1]
            verdicts = active_speaker.verdicts_for_scene(
                input_video, start_f, end_f, fps, splits[start_f])
            if not active_speaker.is_conversation(verdicts):
                a, b = active_speaker.shares(verdicts)
                print(f"   🔇 Scene {scene_idx}: one speaker holds the floor "
                      f"({max(a, b):.0%}) — not stacking")
                del splits[start_f]
                strategies[scene_idx] = 'GENERAL'
            elif active_speaker.CUT_MODE:
                strategies[scene_idx] = 'ALTERNATE'
                alternates[start_f] = (
                    active_speaker.hold(verdicts), splits.pop(start_f))
    if splits:
        print(f"   🪞 SPLIT layout on {len(splits)} scene(s)")
    if alternates:
        print(f"   🎬 Speaker-cut layout on {len(alternates)} scene(s)")

    # SCREENCAST wins over SPLIT on the rare scene that qualifies for both: two
    # faces beside a chart still means the chart is what the shot is about, and
    # stacking the two speakers would crop it away entirely.
    # A screen with a webcam composited into a corner gets its own layout, and
    # the question "is there an inset" is settled geometrically rather than by
    # asking Gemini: offered as a fourth choice it answered "screencast" on all
    # five clips that had one, while camera_inset.detect finds all five with no
    # false positives. The box is fixed for the whole video, so it is found once.
    inset = None
    if content_ranges and screencast_layout.ENABLED:
        try:
            inset = camera_inset.detect(input_video)
        except Exception as e:
            print(f"   ⚠️ Inset check failed ({e}) — using the screen layouts.")
        if inset:
            print(f"   📹 Webcam inset at {inset}")

    screencasts = {}
    wide_count = 0
    inset_count = 0
    if content_ranges:
        for scene_idx, (plan, centre) in screencast_layout.detect_screencast_scenes(
                input_video, scenes, strategies, content_ranges).items():
            # An inset beats both screen plans: it is the only one that can show
            # the screen whole AND the person at a readable size.
            if inset:
                plan, centre = 'INSET', None
            strategies[scene_idx] = plan
            start_f = scene_boundaries[scene_idx][0]
            splits.pop(start_f, None)
            if plan == 'SCREENCAST':
                screencasts[start_f] = centre
            elif plan == 'INSET':
                inset_count += 1
            else:
                wide_count += 1
    if screencasts:
        print(f"   🖥️ SCREENCAST layout on {len(screencasts)} scene(s)")
    if wide_count:
        print(f"   📐 Full-width layout on {wide_count} scene(s)")
    if inset_count:
        print(f"   📹 Camera-inset layout on {inset_count} scene(s)")

    # The crop geometry comes from the SOURCE dims only — SmoothedCameraman
    # derives crop_width/crop_height from video_width/video_height and never
    # reads the output pair. So out_w/out_h being the (possibly upscaled)
    # delivery size doesn't move the camera; only the final scale= uses it.
    cameraman = m.SmoothedCameraman(out_w, out_h, orig_w, orig_h, aspect_ratio=aspect_ratio)
    tracker = m.SpeakerTracker(cooldown_frames=30)

    xs = _analyze_trajectory(input_video, scene_boundaries, strategies, fps,
                             orig_w, orig_h, cameraman, tracker)
    if not xs:
        raise RuntimeError("analysis produced no frames")

    # Beats are found once per clip; each scene takes the ones inside it.
    beats = []
    if punch_in.ENABLED:
        beats = punch_in.emphasis_times(input_video, len(xs) / fps)
        if beats:
            print(f"   🔍 Punch-in on {len(beats)} beat(s)")

    crop_w, crop_h = cameraman.crop_width, cameraman.crop_height

    # ALTERNATE renders through the TRACK path: hard cuts between two speakers
    # are still just a list of crop x values, so no new filtergraph is needed.
    # The trajectory is written here because the analysis pass deliberately
    # skips these scenes rather than tracking a face through them.
    for start_f, (held, centres) in alternates.items():
        end_f = scene_boundaries[split_scene_of[start_f]][1]
        end_f = min(end_f, len(xs))
        if end_f <= start_f:
            continue
        xs[start_f:end_f] = active_speaker.speaker_xs(
            held, centres, crop_w, orig_w, end_f - start_f, fps)

    ranges = scene_frame_ranges(scene_boundaries, strategies, len(xs))
    if not ranges:
        raise RuntimeError("no usable scene ranges")
    workdir = tempfile.mkdtemp(prefix="reframe_v2_")
    segments = []
    try:
        for idx, (start_f, end_f, strategy) in enumerate(ranges):
            seg_path = os.path.join(workdir, f"seg_{idx:03d}.mp4")
            ss = start_f / fps
            dur = (end_f - start_f) / fps

            if strategy == 'INSET':
                graph = camera_inset.inset_filtergraph(
                    orig_w, orig_h, out_w, out_h, inset)
            elif strategy == 'SCREENCAST':
                graph = screencast_layout.screencast_filtergraph(
                    orig_w, orig_h, out_w, out_h, screencasts[start_f])
            elif strategy == 'WIDE':
                graph = general_filtergraph(
                    out_w, out_h,
                    full_width_content_height(orig_w, orig_h, out_w))
            elif strategy == 'SPLIT':
                left, right = splits[start_f]
                graph = split_layout.split_filtergraph(
                    orig_w, orig_h, out_w, out_h, left, right)
            elif strategy == 'GENERAL':
                graph = general_filtergraph(out_w, out_h)
            else:
                seg_xs = [x if x is not None else 0 for x in xs[start_f:end_f]]
                cmd_path = os.path.join(workdir, f"cmd_{idx:03d}.txt")
                if beats:
                    zooms = punch_in.zoom_curve(len(seg_xs), fps, beats,
                                                start_offset=ss)
                    boxes = punch_in.crop_boxes(seg_xs, zooms, crop_w, crop_h,
                                                orig_w, orig_h)
                    lines = punch_in.sendcmd_lines(boxes, fps)
                    first = boxes[0]
                    init = f"w={first[0]}:h={first[1]}:x={first[2]}:y={first[3]}"
                else:
                    lines = dedupe_sendcmd_lines(seg_xs, fps)
                    init = f"w={crop_w}:h={crop_h}:x={seg_xs[0]}:y=0"
                with open(cmd_path, "w") as f:
                    f.write("\n".join(lines) + "\n")
                graph = (
                    f"[0:v]sendcmd=f='{cmd_path}',"
                    f"crop@c={init},"
                    f"scale={out_w}:{out_h},setsar=1[v]"
                )

            _run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", f"{ss:.4f}", "-t", f"{dur:.4f}", "-i", input_video,
                "-filter_complex", graph, "-map", "[v]",
                *video_encode_args(QUALITY_FAST), "-an", seg_path,
            ])
            segments.append(seg_path)

        list_path = os.path.join(workdir, "concat.txt")
        with open(list_path, "w") as f:
            f.write(concat_list_content(segments))

        # Concat video segments (stream copy) + audio straight from the clip.
        _run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-i", input_video,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "copy", "-c:a", "copy", *METADATA_SCRUB,
            # +faststart moves the moov atom to the front so the browser <video>
            # can start playing before the whole file downloads. Without it the
            # in-app preview spins forever (download still works) — the moov
            # lands at the end of a plain concat.
            "-movflags", "+faststart",
            final_output_video,
        ])
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"   ✅ Clip saved to {final_output_video}")
    return True

"""Replay recorded detections through the cameraman, and score the result.

The corpus and harness this repo's commit messages cite ("48 clips",
"labels-layout.json in the reframe-testing skill") are not in the repository
and are not on this machine. This rebuilds the part that matters for camera
work, on the methodology commit bdd9e5d used and wrote down:

    variants are compared by recording the detections ONCE and replaying them
    through the cameraman, so every variant sees identical input rather than a
    fresh run of a non-deterministic detector.

That is the whole point. MediaPipe does not return the same boxes twice, so
comparing two cameramen by rendering twice measures the detector as much as the
change. Record once, replay N times.

Split from scripts/replay_camera.py because everything here is pure: no ffmpeg,
no cv2, no mediapipe. It runs in CI, and the metrics are unit-tested rather than
trusted.

Metrics, both counted INSIDE a scene only:

  reversals/s  direction changes of the crop. A cut is *supposed* to reframe,
               so scoring the snap at a scene boundary would measure the
               feature instead of the bug.
  travel px/s  total distance the crop travelled.

Reported per scene as well as in aggregate, because bdd9e5d found its change
made 7 of 84 scenes measurably BUSIER while improving the mean, and a mean on
its own would have hidden that.
"""
import json


def load_trace(path):
    with open(path) as f:
        return json.load(f)


def save_trace(trace, path):
    with open(path, "w") as f:
        json.dump(trace, f)


def replay(trace, cameraman, tracker, detect_stride=4, phase_on_scene=False):
    """Drive a cameraman through a recorded trace. Returns crop x per frame.

    ``trace`` is what scripts/replay_camera.py record produces:
        {"width", "height", "fps", "scenes": [[start_f, end_f], ...],
         "strategies": [...],
         "detections": {"<frame>": [{"box": [x, y, w, h], "score": n}, ...]}}

    Mirrors reframe_v2._analyze_trajectory's decision loop exactly, minus the
    decoding. ``phase_on_scene`` selects between numbering detection frames
    from frame 0 (what ships today) and numbering them from each scene's first
    frame — the difference being whether a scene's opening frame is ever a
    detection frame, and therefore whether its force_snap lands on a fresh
    target or on the previous shot's.
    """
    width = trace["width"]
    scenes = [tuple(s) for s in trace["scenes"]]
    strategies = trace.get("strategies") or []
    detections = {int(k): v for k, v in trace["detections"].items()}
    total = trace["frames"]

    xs = []
    scene_index = 0
    for frame_number in range(total):
        if scene_index < len(scenes):
            start_f, end_f = scenes[scene_index]
            if frame_number >= end_f and scene_index < len(scenes) - 1:
                scene_index += 1
                start_f, end_f = scenes[scene_index]
        else:
            start_f = 0

        strategy = (strategies[scene_index]
                    if scene_index < len(strategies) else "TRACK")
        if strategy != "TRACK":
            cameraman.current_center_x = width / 2
            cameraman.target_center_x = width / 2
            xs.append(None)
            continue

        offset = frame_number - start_f if phase_on_scene else frame_number
        if offset % detect_stride == 0:
            candidates = detections.get(frame_number) or []
            box = tracker.get_target(candidates, frame_number, width)
            if box:
                cameraman.update_target(box)

        is_scene_start = frame_number == start_f
        x1, _y1, _x2, _y2 = cameraman.get_crop_box(force_snap=is_scene_start)
        xs.append(x1)

    return xs


def scene_metrics(xs, scenes, fps):
    """Per-scene (reversals_per_s, travel_per_s, seconds), boundaries excluded.

    The first frame of a scene is a force_snap, so both its displacement and
    any direction change it implies are dropped: they are the reframe doing its
    job, not the camera hunting.
    """
    out = []
    for start_f, end_f in scenes:
        span = [x for x in xs[start_f:end_f] if x is not None]
        if len(span) < 3:
            continue
        seconds = (end_f - start_f) / float(fps)
        if seconds <= 0:
            continue

        travel = 0.0
        reversals = 0
        last_direction = 0
        # Start at index 2: index 1's delta is measured against the snapped
        # frame, which is the boundary move we are excluding.
        for i in range(2, len(span)):
            delta = span[i] - span[i - 1]
            travel += abs(delta)
            if delta == 0:
                continue
            direction = 1 if delta > 0 else -1
            if last_direction and direction != last_direction:
                reversals += 1
            last_direction = direction

        out.append({
            "start_frame": start_f,
            "seconds": seconds,
            "reversals_per_s": reversals / seconds,
            "travel_per_s": travel / seconds,
        })
    return out


def summarise(xs, scenes, fps):
    """Aggregate over the whole clip, weighted by scene length."""
    per_scene = scene_metrics(xs, scenes, fps)
    seconds = sum(s["seconds"] for s in per_scene)
    if not seconds:
        return {"scenes": 0, "seconds": 0.0,
                "reversals_per_s": 0.0, "travel_per_s": 0.0, "per_scene": []}
    reversals = sum(s["reversals_per_s"] * s["seconds"] for s in per_scene)
    travel = sum(s["travel_per_s"] * s["seconds"] for s in per_scene)
    return {
        "scenes": len(per_scene),
        "seconds": seconds,
        "reversals_per_s": reversals / seconds,
        "travel_per_s": travel / seconds,
        "per_scene": per_scene,
    }


def compare(baseline, candidate):
    """Scene-by-scene verdict between two summaries.

    Returns counts of scenes that got calmer, busier or stayed put on travel,
    because reporting only the mean is how a change that helps on average
    while wrecking a handful of scenes gets shipped as a clean win.
    """
    a = {s["start_frame"]: s for s in baseline["per_scene"]}
    b = {s["start_frame"]: s for s in candidate["per_scene"]}
    calmer = busier = unchanged = 0
    worst = None
    for key, before in a.items():
        after = b.get(key)
        if not after:
            continue
        d = after["travel_per_s"] - before["travel_per_s"]
        if abs(d) < 1e-6:
            unchanged += 1
        elif d < 0:
            calmer += 1
        else:
            busier += 1
            if worst is None or d > worst["delta"]:
                worst = {"start_frame": key, "delta": d,
                         "before": before["travel_per_s"],
                         "after": after["travel_per_s"]}
    return {"calmer": calmer, "busier": busier, "unchanged": unchanged,
            "worst_regression": worst}


def format_report(name, summary):
    return (f"{name:<22} {summary['reversals_per_s']:>6.2f} rev/s "
            f"{summary['travel_per_s']:>7.1f} px/s "
            f"({summary['scenes']} scenes, {summary['seconds']:.1f}s)")

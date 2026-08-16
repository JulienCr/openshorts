#!/usr/bin/env python3
"""Record a clip's face detections once, then score cameraman variants on them.

Why record rather than render twice: MediaPipe is not deterministic, so
comparing two cameramen by running the pipeline twice measures the detector as
much as the change. Commit bdd9e5d established this methodology and its numbers
(reversals/s, travel px/s, in-scene only) are the ones this reproduces.

Recording needs the ML stack, so run it inside the backend image:

    docker run --rm --entrypoint python \
      -v "$PWD":/app -v /path/to/clips:/data -w /app openshorts-backend \
      scripts/replay_camera.py record /data/clip.mp4 -o /data/clip.trace.json

Scoring is pure and runs anywhere:

    python3 scripts/replay_camera.py replay /data/clip.trace.json

The input is a CLIP, already cut — never a source video with the pipeline left
to choose the range. Clip boundaries are being changed in parallel by other
work, and a trace recorded against one set of bounds would not line up with the
same source afterwards. Pinning the input removes that coupling entirely.
"""
import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_replay  # noqa: E402


def record(clip_path, out_path, detect_stride=None):
    """Decode the clip once, dump every detection and scene boundary to JSON."""
    import numpy as np
    import subprocess

    import main as m
    import reframe_v2

    scenes, fps = m.detect_scenes(clip_path)
    fps = float(fps)
    orig_w, orig_h = m.get_video_resolution(clip_path)
    if not scenes:
        raise SystemExit("no scenes detected")

    scene_boundaries = [(s.get_frames(), e.get_frames()) for s, e in scenes]
    strategies = m.analyze_scenes_strategy(clip_path, scenes)
    stride = detect_stride or m.DETECT_STRIDE

    small_w = min(reframe_v2.ANALYSIS_MAX_WIDTH, orig_w)
    if small_w % 2:
        small_w -= 1
    small_h = max(int(orig_h * small_w / orig_w), 2)
    if small_h % 2:
        small_h += 1
    scale = orig_w / small_w
    frame_bytes = small_w * small_h * 3

    proc = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-i", clip_path,
         "-vf", f"scale={small_w}:{small_h}",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=frame_bytes * 4)

    detections = {}
    frame_number = 0
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape((small_h, small_w, 3))
            # Record on EVERY multiple of 1 the caller might replay with, so a
            # trace can be replayed at a different stride than it was taken.
            if frame_number % stride == 0:
                cands = m.detect_face_candidates(frame)
                for c in cands:
                    c['box'] = [int(v * scale) for v in c['box']]
                    c['score'] = c['box'][2] * c['box'][3]
                if cands:
                    detections[str(frame_number)] = [
                        {"box": list(c['box']), "score": c['score']} for c in cands]
            frame_number += 1
    finally:
        proc.stdout.close()
        proc.wait()

    trace = {
        "clip": os.path.basename(clip_path),
        "width": orig_w, "height": orig_h, "fps": fps,
        "frames": frame_number,
        "detect_stride": stride,
        "scenes": [list(b) for b in scene_boundaries],
        "strategies": strategies,
        "detections": detections,
    }
    camera_replay.save_trace(trace, out_path)
    track = sum(1 for s in strategies if s == 'TRACK')
    print(f"Recorded {frame_number} frames, {len(scene_boundaries)} scenes "
          f"({track} TRACK), {len(detections)} detection frames -> {out_path}")


def _variants():
    """The cameraman configurations to score.

    Each entry gets a FRESH cameraman and tracker, built from the trace, so no
    state leaks between runs.
    """
    return {
        "shipping": {},
        "confirm=1": {"jump_confirm_frames": 1},
        "confirm=5": {"jump_confirm_frames": 5},
    }


def _score_one(trace, name, overrides, phase_on_scene):
    import camera

    cam = camera.SmoothedCameraman(
        trace["width"], trace["height"], trace["width"], trace["height"],
        aspect_ratio=9 / 16)
    for key, value in overrides.items():
        setattr(cam, key, value)
    tracker = camera.SpeakerTracker(cooldown_frames=30)

    xs = camera_replay.replay(copy.deepcopy(trace), cam, tracker,
                              detect_stride=trace.get("detect_stride", 4),
                              phase_on_scene=phase_on_scene)
    return camera_replay.summarise(xs, [tuple(s) for s in trace["scenes"]],
                                   trace["fps"])


def _pool(summaries):
    """Merge per-clip summaries into one, weighted by seconds of TRACK footage.

    Scene keys are made unique per clip first: two clips both have a scene
    starting at frame 0, and letting them collide would silently drop half the
    corpus from the per-scene verdict.
    """
    per_scene = []
    for i, s in enumerate(summaries):
        for scene in s["per_scene"]:
            scene = dict(scene)
            scene["start_frame"] = i * 1_000_000 + scene["start_frame"]
            per_scene.append(scene)
    seconds = sum(s["seconds"] for s in per_scene)
    if not seconds:
        return {"scenes": 0, "seconds": 0.0, "reversals_per_s": 0.0,
                "travel_per_s": 0.0, "per_scene": []}
    return {
        "scenes": len(per_scene),
        "seconds": seconds,
        "reversals_per_s": sum(s["reversals_per_s"] * s["seconds"] for s in per_scene) / seconds,
        "travel_per_s": sum(s["travel_per_s"] * s["seconds"] for s in per_scene) / seconds,
        "per_scene": per_scene,
    }


def replay(trace_paths, phase_on_scene=False):
    traces = [camera_replay.load_trace(p) for p in trace_paths]
    for t in traces:
        print(f"  {t.get('clip', '?'):<24} {t['frames']:>6} frames @ "
              f"{t['fps']:.0f}fps  {t['width']}x{t['height']}  "
              f"{len(t['scenes'])} scenes")

    results = {}
    for name, overrides in _variants().items():
        results[name] = _pool([_score_one(t, name, overrides, phase_on_scene)
                               for t in traces])

    print()
    for name, summary in results.items():
        print(camera_replay.format_report(name, summary))

    baseline = results.get("shipping")
    if baseline:
        print("\nper-scene verdict vs shipping (travel):")
        for name, summary in results.items():
            if name == "shipping":
                continue
            c = camera_replay.compare(baseline, summary)
            line = (f"  {name:<20} calmer {c['calmer']}  "
                    f"busier {c['busier']}  unchanged {c['unchanged']}")
            if c["worst_regression"]:
                w = c["worst_regression"]
                line += (f"   worst: {w['before']:.0f} -> {w['after']:.0f} px/s")
            print(line)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="decode a clip and dump its detections")
    rec.add_argument("clip")
    rec.add_argument("-o", "--out", required=True)
    rec.add_argument("--detect-stride", type=int, default=None)

    rep = sub.add_parser("replay", help="score cameraman variants on traces")
    rep.add_argument("trace", nargs="+")
    rep.add_argument("--phase-on-scene", action="store_true",
                     help="number detection frames from each scene start, so a "
                          "scene's opening frame is always a detection frame")

    args = parser.parse_args()
    if args.cmd == "record":
        record(args.clip, args.out, args.detect_stride)
    else:
        replay(args.trace, args.phase_on_scene)


if __name__ == "__main__":
    main()

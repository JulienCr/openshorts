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
            # Record at the RECORDING stride, which should be 1 for any trace
            # used to compare detection phasing: a trace holding only frames
            # 0,4,8..., replayed with the stride phased onto a scene starting
            # at frame 6, consults frames 6,10,14... — none of which hold a
            # detection. The camera then receives no target at all and the
            # travel metric collapses, which reads as a spectacular win and is
            # an artefact of the trace, not a property of the change.
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
        "no demotion (old)": {"min_track": 0.0},
        "min_track=1.0s": {"min_track": 1.0},
        "min_track=1.5s (ships)": {"min_track": 1.5},
        "min_track=3.0s": {"min_track": 3.0},
    }


def _score_one(trace, name, overrides, phase_on_scene, replay_stride):
    import camera

    cam = camera.SmoothedCameraman(
        trace["width"], trace["height"], trace["width"], trace["height"],
        aspect_ratio=9 / 16)
    flags = {"phase_on_scene": phase_on_scene}
    # Copy: _variants() hands the same dict to every trace, and popping from it
    # would leave all but the first clip scored with default settings.
    overrides = dict(overrides)
    min_track = overrides.pop("min_track", None)
    for key, value in overrides.items():
        if key in flags:
            flags[key] = value
        else:
            setattr(cam, key, value)
    if min_track is not None:
        trace = dict(trace)
        trace["strategies"] = camera_replay.demote_short_track(
            trace["strategies"], [tuple(s) for s in trace["scenes"]],
            trace["fps"], min_track)
    tracker = camera.SpeakerTracker(cooldown_frames=30)

    xs = camera_replay.replay(copy.deepcopy(trace), cam, tracker,
                              detect_stride=replay_stride, **flags)
    scenes = [tuple(s) for s in trace["scenes"]]
    summary = camera_replay.summarise(xs, scenes, trace["fps"])
    motion, jumps, longest = camera_replay.screen_motion(xs, scenes, trace["fps"])
    summary["screen_px_per_s"] = motion
    summary["cut_jumps"] = jumps
    summary["longest_pan"] = longest
    return summary


def _pool(summaries):
    """Merge per-clip summaries into one, weighted by seconds of TRACK footage.

    Scene keys are made unique per clip first: two clips both have a scene
    starting at frame 0, and letting them collide would silently drop half the
    corpus from the per-scene verdict.
    """
    jumps = [j for s in summaries for j in s.get("cut_jumps", [])]
    longest = max((s.get("longest_pan", 0) for s in summaries), default=0)
    motion_num = sum(s.get("screen_px_per_s", 0.0) * s["seconds"] for s in summaries)
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
        "screen_px_per_s": motion_num / seconds,
        "cut_jumps": jumps,
        "longest_pan": longest,
        "per_scene": per_scene,
    }


def replay(trace_paths, phase_on_scene=False, replay_stride=4):
    traces = [camera_replay.load_trace(p) for p in trace_paths]
    for t in traces:
        print(f"  {t.get('clip', '?'):<24} {t['frames']:>6} frames @ "
              f"{t['fps']:.0f}fps  {t['width']}x{t['height']}  "
              f"{len(t['scenes'])} scenes")

    results = {}
    for name, overrides in _variants().items():
        results[name] = _pool([_score_one(t, name, overrides, phase_on_scene,
                                          replay_stride)
                               for t in traces])

    print(f"\n{'variant':<24} {'in-scene':>18}   {'on screen (incl. cuts)':>34}")
    print(f"{'':<24} {'rev/s':>8} {'px/s':>9}   {'px/s':>8} {'cuts':>6} "
          f"{'worst jump':>11} {'longest pan':>12}")
    for name, s in results.items():
        worst = max((abs(j) for j in s.get("cut_jumps", [])), default=0)
        print(f"{name:<24} {s['reversals_per_s']:>8.2f} {s['travel_per_s']:>9.1f}   "
              f"{s.get('screen_px_per_s', 0):>8.1f} {len(s.get('cut_jumps', [])):>6} "
              f"{worst:>11} {s.get('longest_pan', 0):>12}")

    baseline = results.get("no demotion (old)")
    if baseline:
        print("\nper-scene verdict vs the old controller (travel):")
        for name, summary in results.items():
            if name == "no demotion (old)":
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
    rec.add_argument("--detect-stride", type=int, default=1,
                     help="Record every Nth frame. Keep at 1: a sparser trace "
                          "cannot honestly score a change to WHICH frames the "
                          "decision loop consults.")

    rep = sub.add_parser("replay", help="score cameraman variants on traces")
    rep.add_argument("trace", nargs="+")
    rep.add_argument("--replay-stride", type=int, default=4,
                     help="Stride the decision loop uses (main.DETECT_STRIDE).")
    rep.add_argument("--phase-on-scene", action="store_true",
                     help="number detection frames from each scene start, so a "
                          "scene's opening frame is always a detection frame")

    args = parser.parse_args()
    if args.cmd == "record":
        record(args.clip, args.out, args.detect_stride)
    else:
        replay(args.trace, args.phase_on_scene, args.replay_stride)


if __name__ == "__main__":
    main()

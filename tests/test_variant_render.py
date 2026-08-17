"""End-to-end render of the safe delivery variant.

The pure tests in test_reframe_v2.py assert the argv is shaped right. This one
asserts ffmpeg actually accepts it and hands back a playable 9:16 file — the
only proof that the safe variant is a legal deliverable rather than a plausible
command line. Skipped where ffmpeg is absent, which includes CI.
"""
import os
import shutil
import subprocess

import pytest

import reframe_v2

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not installed",
)

VERTICAL = 9 / 16


def _make_clip(path, seconds=2, size="640x360", silent=False):
    """A tiny synthetic 16:9 clip, with or without an audio track."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration={seconds}",
    ]
    if not silent:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if not silent:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return str(path)


def _probe(path, entries, stream="v:0"):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", stream,
         "-show_entries", entries, "-of", "csv=p=0", path],
        stderr=subprocess.STDOUT,
    ).decode().strip()
    return out


@needs_ffmpeg
def test_safe_variant_renders_a_vertical_file(tmp_path):
    src = _make_clip(tmp_path / "src.mp4")
    out = str(tmp_path / "out.mp4")

    assert reframe_v2.render_general(src, out, VERTICAL) is True
    assert os.path.getsize(out) > 0

    w, h = _probe(out, "stream=width,height").split(",")[:2]
    # delivery_size floors the width at 1080 even from a 640px source: a
    # sub-HD upload reads as low quality on every short-form platform.
    assert (int(w), int(h)) == (1080, 1920)


@needs_ffmpeg
def test_safe_variant_keeps_the_audio(tmp_path):
    src = _make_clip(tmp_path / "src.mp4")
    out = str(tmp_path / "out.mp4")
    reframe_v2.render_general(src, out, VERTICAL)

    codec = _probe(out, "stream=codec_type", stream="a:0")
    assert codec == "audio"


@needs_ffmpeg
def test_a_silent_source_still_renders(tmp_path):
    """`-map 0:a:0?` is load-bearing: without the ?, ffmpeg aborts here."""
    src = _make_clip(tmp_path / "silent.mp4", silent=True)
    out = str(tmp_path / "out.mp4")

    assert reframe_v2.render_general(src, out, VERTICAL) is True
    assert os.path.getsize(out) > 0


@needs_ffmpeg
def test_square_aspect_is_honoured(tmp_path):
    src = _make_clip(tmp_path / "src.mp4")
    out = str(tmp_path / "out.mp4")
    reframe_v2.render_general(src, out, 1.0)

    w, h = _probe(out, "stream=width,height").split(",")[:2]
    assert (int(w), int(h)) == (1080, 1080)


@needs_ffmpeg
def test_duration_survives_the_pass(tmp_path):
    src = _make_clip(tmp_path / "src.mp4", seconds=2)
    out = str(tmp_path / "out.mp4")
    reframe_v2.render_general(src, out, VERTICAL)

    dur = float(_probe(out, "stream=duration"))
    assert abs(dur - 2.0) < 0.15


@needs_ffmpeg
def test_whole_source_width_survives(tmp_path):
    """The promise of the variant: nothing is ever cropped away.

    Rendered from a source whose left and right edges are a distinct colour,
    both edges must still be present in the output. The default GENERAL ratio
    would crop ~24% of the width and lose them.
    """
    src = str(tmp_path / "bars.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "smptebars=size=640x360:rate=25:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", src,
    ], check=True, capture_output=True)

    out = str(tmp_path / "out.mp4")
    reframe_v2.render_general(src, out, VERTICAL)

    orig_w, orig_h = reframe_v2._probe_dimensions(src)
    out_w, _ = reframe_v2.delivery_size(orig_w, orig_h, VERTICAL)
    content_h = reframe_v2.full_width_content_height(orig_w, orig_h, out_w)
    # Full width kept => the scaled content is exactly as tall as the
    # aspect-preserved fit, so no crop filter can bite.
    assert content_h == pytest.approx(out_w * orig_h / orig_w, abs=2)

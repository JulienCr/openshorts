"""The automatic hook: Gemini's viral_hook_text burned onto the clip.

Gemini has always written a `viral_hook_text` for every clip and nothing ever
burned it — it waited for someone to open the modal. These tests cover turning
that into an automatic overlay, and the one-pass ffmpeg burn it shares with the
captions.

Nothing here imports ``main``: it pulls torch/ultralytics/mediapipe at module
level, which CI deliberately does not install. The pieces this feature adds
therefore live in ``hooks``/``subtitles``/``style_preset``, where they can be
tested — ``main`` only wires them together.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

import hooks
import subtitles

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not installed",
)


def _make_clip(path, seconds=1, silent=True):
    """A tiny synthetic clip, with or without an audio track."""
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i",
           f"testsrc=size=270x480:rate=10:duration={seconds}"]
    if not silent:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    return str(path)


def _streams(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(path)]).decode()
    return out.split()


def _ass(path):
    """A minimal but real ASS file, produced by the same code the pipeline uses."""
    transcript = {"segments": [{"start": 0, "end": 1, "text": "hook", "words": [
        {"word": " hook", "start": 0.0, "end": 0.5},
        {"word": " word", "start": 0.5, "end": 1.0},
    ]}]}
    assert subtitles.generate_ass(transcript, 0, 1, str(path)) is True
    return str(path)


class TestAutoHookOverlay:
    """Deciding whether there is a hook to draw at all, and cleaning up after.
    Every no-answer has to be silent and cheap — this runs once per clip."""

    def test_no_text_means_no_overlay(self, monkeypatch):
        monkeypatch.setenv("OPENSHORTS_STYLE", json.dumps({"hook": {"enabled": True}}))
        with hooks.auto_hook_overlay("clip.mp4", None, 123) as overlay:
            assert overlay == {}

    def test_disabled_preset_means_no_overlay(self, monkeypatch):
        monkeypatch.delenv("OPENSHORTS_STYLE", raising=False)
        with hooks.auto_hook_overlay("clip.mp4", "Stop doing this", 123) as overlay:
            assert overlay == {}

    def test_unreadable_clip_costs_the_hook_not_the_clip(self, monkeypatch):
        # Probing a file that isn't there must not raise: the caller is midway
        # through delivering a clip the user already paid for.
        monkeypatch.setenv("OPENSHORTS_STYLE", json.dumps({"hook": {"enabled": True}}))
        with hooks.auto_hook_overlay("/nonexistent/clip.mp4", "Stop", 123) as overlay:
            assert overlay == {}

    @needs_ffmpeg
    def test_enabled_preset_draws_the_card(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENSHORTS_STYLE", json.dumps(
            {"hook": {"enabled": True, "duration_seconds": 2.5, "position": "top"}}))
        clip = _make_clip(tmp_path / "clip.mp4")

        with hooks.auto_hook_overlay(clip, "Stop doing this", 123) as overlay:
            assert os.path.exists(overlay["overlay_png"])
            assert overlay["overlay_until"] == 2.5
            x, y = overlay["overlay_xy"]
            assert 0 <= x < 270 and 0 <= y < 480

    @needs_ffmpeg
    def test_temp_card_is_cleaned_up(self, monkeypatch, tmp_path):
        # The PNG is scratch. Leaving one per clip behind would quietly fill the
        # output dir that self-host treats as the permanent project library.
        monkeypatch.setenv("OPENSHORTS_STYLE", json.dumps({"hook": {"enabled": True}}))
        clip = _make_clip(tmp_path / "clip.mp4")

        with hooks.auto_hook_overlay(clip, "Stop doing this", 123):
            pass

        assert [f for f in os.listdir(tmp_path) if f.endswith(".png")] == []

    @needs_ffmpeg
    def test_cleanup_happens_even_when_the_burn_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENSHORTS_STYLE", json.dumps({"hook": {"enabled": True}}))
        clip = _make_clip(tmp_path / "clip.mp4")

        with pytest.raises(ZeroDivisionError):
            with hooks.auto_hook_overlay(clip, "Stop doing this", 123):
                raise ZeroDivisionError("ffmpeg blew up")

        assert [f for f in os.listdir(tmp_path) if f.endswith(".png")] == []

    @needs_ffmpeg
    def test_a_failing_burn_reports_its_own_error(self, monkeypatch, tmp_path):
        # The failure must reach the caller intact. Catching it around the yield
        # and yielding again turns a real ffmpeg error into contextlib's
        # "generator didn't stop after throw()", which says nothing about what
        # actually broke — and auto_caption_clip prints that message.
        monkeypatch.setenv("OPENSHORTS_STYLE", json.dumps({"hook": {"enabled": True}}))
        clip = _make_clip(tmp_path / "clip.mp4")

        with pytest.raises(ZeroDivisionError) as caught:
            with hooks.auto_hook_overlay(clip, "Stop doing this", 123):
                raise ZeroDivisionError("ffmpeg blew up")

        assert "ffmpeg blew up" in str(caught.value)


class TestCaptionedOutputName:
    """auto_caption_clip's output name is a contract, not a label: app.py finds
    the newest derived clip with glob('subtitled_*_<clean>') and walks it back
    with re.match(r'^subtitled_\\d+_(.+)$'). Both must keep matching."""

    WALK_BACK = re.compile(r'^subtitled_\d+_(.+)$')

    def test_walks_back_to_the_clean_clip(self):
        name = subtitles.captioned_output_name("myvideo_clip_1.mp4", 1755300000)
        assert self.WALK_BACK.match(name).group(1) == "myvideo_clip_1.mp4"

    def test_timestamp_is_digits_only(self):
        # The walk-back regex requires \d+ between the prefix and the stem; a
        # uuid or a suffixed timestamp there silently orphans every clip.
        name = subtitles.captioned_output_name("myvideo_clip_1.mp4", 1755300000)
        assert name.split("_")[1].isdigit()

    def test_survives_a_stem_with_apostrophes(self):
        # Titles carry apostrophes constantly ("Earth's", "Don't").
        stem = "Don't stop_clip_2.mp4"
        name = subtitles.captioned_output_name(stem, 1755300000)
        assert self.WALK_BACK.match(name).group(1) == stem


@needs_ffmpeg
class TestMergedBurn:
    """Captions and hook in ONE ffmpeg pass, on a real file."""

    def _png(self, tmp_path):
        png = tmp_path / "hook.png"
        hooks.create_hook_image("Stop doing this", 240, str(png))
        return str(png)

    def test_silent_source_survives_the_overlay(self, tmp_path):
        # The regression this guards: two inputs make -map mandatory, and
        # without the trailing '?' ffmpeg aborts on a source with no audio.
        clip = _make_clip(tmp_path / "clip.mp4", silent=True)
        out = tmp_path / "out.mp4"

        subtitles.burn_subtitles(
            clip, _ass(tmp_path / "s.ass"), str(out),
            overlay_png=self._png(tmp_path), overlay_xy=(10, 20), overlay_until=0.5)

        assert os.path.exists(out)
        assert _streams(out) == ["video"]

    def test_audio_is_preserved(self, tmp_path):
        clip = _make_clip(tmp_path / "clip.mp4", silent=False)
        out = tmp_path / "out.mp4"

        subtitles.burn_subtitles(
            clip, _ass(tmp_path / "s.ass"), str(out),
            overlay_png=self._png(tmp_path), overlay_xy=(10, 20), overlay_until=0.5)

        assert sorted(_streams(out)) == ["audio", "video"]

    def test_hook_burns_on_a_clip_with_no_captions(self, tmp_path):
        # AUTO_CAPTIONS=0 and a wordless clip are both legitimate, and both used
        # to skip the hook entirely. Goes through burn_subtitles, not just the
        # filter builder: the path escaping runs before the filter is chosen and
        # blew up on a None subtitle file while the unit test stayed green.
        clip = _make_clip(tmp_path / "clip.mp4", silent=False)
        out = tmp_path / "out.mp4"

        subtitles.burn_subtitles(
            clip, None, str(out),
            overlay_png=self._png(tmp_path), overlay_xy=(10, 20), overlay_until=0.5)

        assert os.path.exists(out)
        assert sorted(_streams(out)) == ["audio", "video"]

    def test_captions_only_path_is_unchanged(self, tmp_path):
        # No overlay: the single-input form the pipeline has always used.
        clip = _make_clip(tmp_path / "clip.mp4", silent=False)
        out = tmp_path / "out.mp4"

        subtitles.burn_subtitles(clip, _ass(tmp_path / "s.ass"), str(out))

        assert os.path.exists(out)
        assert sorted(_streams(out)) == ["audio", "video"]

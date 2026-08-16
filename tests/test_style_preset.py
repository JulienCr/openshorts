"""Tests for the server-side default style preset.

The preset is a plain JSON file read at submit time. Every failure mode here is
"fall back to the built-in defaults and keep going" — a malformed preset must
never cost a job, which is the same doctrine auto_caption_clip already follows
for captions.
"""
import json
import os

import pytest

import style_preset


@pytest.fixture
def preset_file(tmp_path, monkeypatch):
    """Point the loader at a throwaway file and hand back its path."""
    path = tmp_path / "style.json"
    monkeypatch.setenv("OPENSHORTS_STYLE_FILE", str(path))
    return path


class TestLoadStyle:
    def test_missing_file_is_not_an_error(self, preset_file):
        # Self-host without a style.json is the default install, not a problem.
        assert style_preset.load_style() == {}

    def test_reads_the_file(self, preset_file):
        preset_file.write_text(json.dumps({"captions": {"font_name": "Anton"}}))
        assert style_preset.load_style() == {"captions": {"font_name": "Anton"}}

    def test_malformed_json_falls_back_to_defaults(self, preset_file):
        preset_file.write_text("{ this is not json")
        assert style_preset.load_style() == {}

    def test_non_object_json_falls_back_to_defaults(self, preset_file):
        # A list parses fine but has no keys to merge; treat it as absent
        # rather than letting .get() blow up deeper in the pipeline.
        preset_file.write_text("[1, 2, 3]")
        assert style_preset.load_style() == {}

    def test_reread_on_every_call(self, preset_file):
        # The whole point of reading at submit time: editing style.json takes
        # effect on the next job, without restarting the container.
        preset_file.write_text(json.dumps({"output_format": "vertical"}))
        assert style_preset.load_style()["output_format"] == "vertical"

        preset_file.write_text(json.dumps({"output_format": "square"}))
        assert style_preset.load_style()["output_format"] == "square"

    def test_explicit_path_wins_over_env(self, preset_file, tmp_path):
        preset_file.write_text(json.dumps({"output_format": "vertical"}))
        other = tmp_path / "other.json"
        other.write_text(json.dumps({"output_format": "square"}))
        assert style_preset.load_style(str(other))["output_format"] == "square"


class TestStyleEnv:
    def test_empty_preset_adds_nothing(self):
        # Mirrors layout_env: no preset means the job's environment is untouched
        # and the renderer keeps its built-in defaults.
        assert style_preset.style_env({}) == {}

    def test_preset_rides_in_a_single_variable(self):
        env = style_preset.style_env({"captions": {"font_name": "Anton"}})
        assert list(env) == ["OPENSHORTS_STYLE"]
        assert json.loads(env["OPENSHORTS_STYLE"]) == {"captions": {"font_name": "Anton"}}

    def test_round_trips_through_the_environment(self, monkeypatch):
        preset = {"captions": {"font_name": "Anton"}, "hook": {"enabled": True}}
        for key, value in style_preset.style_env(preset).items():
            monkeypatch.setenv(key, value)
        assert style_preset.preset_from_env() == preset

    def test_absent_variable_reads_as_no_preset(self, monkeypatch):
        monkeypatch.delenv("OPENSHORTS_STYLE", raising=False)
        assert style_preset.preset_from_env() == {}

    def test_corrupt_variable_reads_as_no_preset(self, monkeypatch):
        # Nothing should be able to hand-edit this, but a job that resumes with
        # a truncated environment must still render.
        monkeypatch.setenv("OPENSHORTS_STYLE", "{ truncated")
        assert style_preset.preset_from_env() == {}


class TestResolveHookStyle:
    """Whether the pipeline burns Gemini's viral_hook_text onto the clip, and
    how. Off unless a preset turns it on."""

    def _with_hook(self, monkeypatch, hook):
        monkeypatch.setenv("OPENSHORTS_STYLE", json.dumps({"hook": hook}))

    def test_no_preset_means_no_automatic_hook(self, monkeypatch):
        # A job without a style must be byte-for-byte the job we ran before any
        # of this existed — and that job never burned a hook.
        monkeypatch.delenv("OPENSHORTS_STYLE", raising=False)
        assert style_preset.resolve_hook_style()["enabled"] is False

    def test_preset_can_turn_it_on(self, monkeypatch):
        self._with_hook(monkeypatch, {"enabled": True})
        assert style_preset.resolve_hook_style()["enabled"] is True

    def test_defaults_fill_the_unset_fields(self, monkeypatch):
        self._with_hook(monkeypatch, {"enabled": True})
        hook = style_preset.resolve_hook_style()
        assert hook["position"] == "top"
        assert hook["style"] == "classic"
        assert hook["duration_seconds"] == 3.0

    def test_preset_overrides_what_it_names(self, monkeypatch):
        self._with_hook(monkeypatch, {"enabled": True, "style": "yellow",
                                      "position": "center", "duration_seconds": 5})
        hook = style_preset.resolve_hook_style()
        assert hook["style"] == "yellow"
        assert hook["position"] == "center"
        assert hook["duration_seconds"] == 5

    def test_size_becomes_a_font_scale(self, monkeypatch):
        # The preset speaks S/M/L like /api/hook does; the renderer needs the
        # multiplier. Converting here keeps one vocabulary in the file.
        self._with_hook(monkeypatch, {"enabled": True, "size": "L"})
        assert style_preset.resolve_hook_style()["font_scale"] == 1.3

    def test_unknown_size_falls_back_to_medium(self, monkeypatch):
        self._with_hook(monkeypatch, {"enabled": True, "size": "XXL"})
        assert style_preset.resolve_hook_style()["font_scale"] == 1.0

    def test_unknown_keys_are_ignored(self, monkeypatch):
        self._with_hook(monkeypatch, {"enabled": True, "sparkle": True})
        assert "sparkle" not in style_preset.resolve_hook_style()

    def test_whole_clip_duration_is_expressible(self, monkeypatch):
        # null means "for the whole clip", the meaning /api/hook already gives
        # duration_seconds=None.
        self._with_hook(monkeypatch, {"enabled": True, "duration_seconds": None})
        assert style_preset.resolve_hook_style()["duration_seconds"] is None

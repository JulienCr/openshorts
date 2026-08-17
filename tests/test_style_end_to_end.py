"""One vocabulary, from the dashboard's save to the renderer's read.

Everything else tests one side of the contract: the endpoint writes JSON, the
renderer reads env. Between them sit key names that no unit test on either side
would catch if they drifted — the panel could save `highlight` while the burner
reads `highlight_color`, and both suites would stay green while every clip
silently rendered with the default look.

So this walks the whole chain: PUT /api/style -> style.json -> _build_job_env ->
OPENSHORTS_STYLE -> resolve_caption_style / resolve_hook_style.
"""
import asyncio
import json

import httpx
import pytest

import app as app_module
import style_preset
import subtitles
from app import app


def _put_style(style):
    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.put("/api/style", json={"style": style})
    return asyncio.run(_run())


@pytest.fixture
def saved_style(tmp_path, monkeypatch):
    """Save a style the way the dashboard does, then hand back a callable that
    reads it back the way the renderer subprocess does."""
    monkeypatch.setenv("OPENSHORTS_STYLE_FILE", str(tmp_path / "style.json"))

    def _save_and_load(style):
        assert _put_style(style).status_code == 200
        env = app_module._build_job_env("key", [], "job-1")
        # The renderer is a separate process; the environment is all it gets.
        monkeypatch.setenv(style_preset.STYLE_ENV, env[style_preset.STYLE_ENV])
        return env

    return _save_and_load


# What the dashboard's DefaultStyleCard actually PUTs.
PANEL_SAVE = {
    "captions": {
        "font_name": "Impact",
        "font_size": 44,
        "font_color": "#FFFF00",
        "highlight_color": "#00BBFF",
        "border_color": "#000000",
        "border_width": 3,
        "style": "karaoke",
        "effect": "glow",
        "alignment": "middle",
        "base_opacity": 0.6,
        "uppercase": False,
        "max_chars": 16,
        "max_duration": 1.4,
    },
    "hook": {
        "enabled": True,
        "style": "yellow",
        "position": "bottom",
        "size": "L",
        "duration_seconds": 4,
    },
    "layouts": ["auto", "punch_in"],
    "output_format": "square",
}


class TestPanelReachesTheRenderer:
    def test_every_caption_field_survives_the_trip(self, saved_style):
        saved_style(PANEL_SAVE)
        style = subtitles.resolve_caption_style()
        for key, value in PANEL_SAVE["captions"].items():
            assert style[key] == value, f"caption field {key!r} did not survive"

    def test_every_hook_field_survives_the_trip(self, saved_style):
        saved_style(PANEL_SAVE)
        hook = style_preset.resolve_hook_style()
        assert hook["enabled"] is True
        assert hook["style"] == "yellow"
        assert hook["position"] == "bottom"
        assert hook["duration_seconds"] == 4
        assert hook["font_scale"] == 1.3  # "L"

    def test_layouts_reach_the_renderer_as_env_switches(self, saved_style):
        env = saved_style(PANEL_SAVE)
        assert env["AUTO_LAYOUT"] == "1"
        assert env["PUNCH_IN"] == "1"

    def test_output_format_reaches_the_submission(self, saved_style):
        saved_style(PANEL_SAVE)
        assert app_module.resolve_output_format(None) == "square"

    def test_the_burn_uses_the_saved_look(self, saved_style, tmp_path):
        # The last link: the resolved style is what generate_ass writes into the
        # ASS header. A field that resolves correctly but is never passed to the
        # burner would still lose the user's look.
        saved_style(PANEL_SAVE)
        style = subtitles.resolve_caption_style()
        out = tmp_path / "subs.ass"
        transcript = {"segments": [{"start": 0, "end": 1, "text": "a b", "words": [
            {"word": " hook", "start": 0.0, "end": 0.5},
            {"word": " word", "start": 0.5, "end": 1.0},
        ]}]}
        assert subtitles.generate_ass(
            transcript, 0, 1, str(out),
            max_chars=style["max_chars"], max_duration=style["max_duration"],
            alignment=style["alignment"], fontsize=style["font_size"],
            font_name=style["font_name"], font_color=style["font_color"],
            border_color=style["border_color"], border_width=style["border_width"],
            highlight_color=style["highlight_color"], effect=style["effect"],
            base_opacity=style["base_opacity"], uppercase=style["uppercase"]) is True

        ass = out.read_text(encoding="utf-8-sig")
        style_line = next(l for l in ass.splitlines() if l.startswith("Style: Default,"))
        fields = style_line[len("Style: "):].split(",")
        assert fields[1] == "Impact"          # Fontname
        assert fields[18] == "5"              # Alignment: 5 is middle-centre in ASS
        assert fields[16] == "3"              # Outline, from border_width
        # The active word's highlight rides the per-word overrides, not the header.
        assert "\\3c&HFFBB00&" in ass         # #00BBFF, ASS colours are BGR


class TestNoPresetIsTheOldBehaviour:
    """A server with no style.json must render byte-for-byte what it rendered
    before any of this existed."""

    def test_captions_fall_back_to_the_built_in_look(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENSHORTS_STYLE_FILE", str(tmp_path / "absent.json"))
        monkeypatch.delenv(style_preset.STYLE_ENV, raising=False)
        env = app_module._build_job_env("key", [], "job-1")
        assert style_preset.STYLE_ENV not in env
        assert subtitles.resolve_caption_style() == subtitles.AUTO_CAPTION_STYLE

    def test_no_hook_is_burned(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENSHORTS_STYLE_FILE", str(tmp_path / "absent.json"))
        monkeypatch.delenv(style_preset.STYLE_ENV, raising=False)
        assert style_preset.resolve_hook_style()["enabled"] is False

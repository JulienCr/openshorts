"""The style preset reaching the pipeline, and the endpoint that edits it.

Self-host mode is guaranteed by tests/conftest.py (BILLING_ENABLED=0).
"""
import asyncio
import json

import httpx
import pytest

import app as app_module
from app import app


def _client_call(method, path, **kw):
    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await getattr(client, method)(path, **kw)
    return asyncio.run(_run())


@pytest.fixture
def preset_file(tmp_path, monkeypatch):
    path = tmp_path / "style.json"
    monkeypatch.setenv("OPENSHORTS_STYLE_FILE", str(path))
    return path


class TestJobEnv:
    """_build_job_env is the single seam both /api/process and
    /api/process/batch go through, so wiring the preset there covers the
    interactive lane and the batch lane at once."""

    def test_no_preset_leaves_the_environment_alone(self, preset_file):
        env = app_module._build_job_env("key", [], "job-1")
        assert "OPENSHORTS_STYLE" not in env

    def test_preset_rides_along(self, preset_file):
        preset_file.write_text(json.dumps({"captions": {"font_name": "Anton"}}))
        env = app_module._build_job_env("key", [], "job-1")
        assert json.loads(env["OPENSHORTS_STYLE"]) == {"captions": {"font_name": "Anton"}}

    def test_preset_composes_with_layouts(self, preset_file):
        # The two are independent overrides on the same job; neither may eat
        # the other.
        preset_file.write_text(json.dumps({"captions": {"font_name": "Anton"}}))
        env = app_module._build_job_env("key", ["punch_in"], "job-1")
        assert env["PUNCH_IN"] == "1"
        assert "OPENSHORTS_STYLE" in env


class TestInlineStyle:
    """A caller can carry a whole style on the request — the CLI's --style and
    the MCP tool's `style` both land here. It replaces the server default for
    that job rather than merging into it: a half-file, half-request hybrid is
    not something anyone can predict from either source alone."""

    def test_inline_style_replaces_the_file(self, preset_file):
        preset_file.write_text(json.dumps({"captions": {"font_name": "Anton"}}))
        env = app_module._build_job_env(
            "key", [], "job-1", style={"captions": {"font_name": "Impact"}})
        carried = json.loads(env["OPENSHORTS_STYLE"])
        assert carried == {"captions": {"font_name": "Impact"}}

    def test_inline_layouts_apply(self, preset_file):
        env = app_module._build_job_env(
            "key", [], "job-1", style={"layouts": ["punch_in"]})
        assert env["PUNCH_IN"] == "1"

    def test_no_inline_style_falls_back_to_the_file(self, preset_file):
        preset_file.write_text(json.dumps({"captions": {"font_name": "Anton"}}))
        env = app_module._build_job_env("key", [], "job-1", style=None)
        assert json.loads(env["OPENSHORTS_STYLE"])["captions"]["font_name"] == "Anton"

    def test_a_non_object_style_is_ignored(self, preset_file):
        # Never let a malformed request silently blank the server's look.
        preset_file.write_text(json.dumps({"captions": {"font_name": "Anton"}}))
        env = app_module._build_job_env("key", [], "job-1", style=[1, 2, 3])
        assert json.loads(env["OPENSHORTS_STYLE"])["captions"]["font_name"] == "Anton"


class TestProcessDefaults:
    """The preset supplies job settings the caller didn't name — that is the
    whole point for n8n and agents, which carry no style fields at all."""

    def test_preset_supplies_the_layouts(self, preset_file):
        preset_file.write_text(json.dumps({"layouts": ["auto", "punch_in"]}))
        assert app_module.resolve_layouts(None) == ["auto", "punch_in"]

    def test_request_layouts_win(self, preset_file):
        preset_file.write_text(json.dumps({"layouts": ["auto"]}))
        assert app_module.resolve_layouts(["split"]) == ["split"]

    def test_empty_request_layouts_are_not_a_choice(self, preset_file):
        # /api/process/batch normalises a missing key to [], which must still
        # mean "the caller said nothing", not "the caller wants none".
        preset_file.write_text(json.dumps({"layouts": ["auto"]}))
        assert app_module.resolve_layouts([]) == ["auto"]

    def test_preset_supplies_the_output_format(self, preset_file):
        preset_file.write_text(json.dumps({"output_format": "square"}))
        assert app_module.resolve_output_format(None) == "square"

    def test_request_format_wins(self, preset_file):
        preset_file.write_text(json.dumps({"output_format": "square"}))
        assert app_module.resolve_output_format("horizontal") == "horizontal"

    def test_unknown_format_in_the_preset_is_ignored(self, preset_file):
        preset_file.write_text(json.dumps({"output_format": "hexagonal"}))
        assert app_module.resolve_output_format(None) == "auto"

    def test_no_preset_keeps_auto(self, preset_file):
        assert app_module.resolve_output_format(None) == "auto"

    def test_preset_can_pre_accept_the_quality_warning(self, preset_file):
        # Thirty queued recordings must not each stop on a low-resolution
        # confirmation nobody is sitting there to click.
        preset_file.write_text(json.dumps({"force_low_quality": True}))
        assert app_module.resolve_force_low_quality(None) is True

    def test_quality_warning_stands_by_default(self, preset_file):
        assert app_module.resolve_force_low_quality(None) is False

    def test_request_can_force_it_without_a_preset(self, preset_file):
        assert app_module.resolve_force_low_quality("true") is True


class TestOmittedIsNotTheSameAsExplicit:
    """"The request beats the file" only holds if an explicit value is
    distinguishable from an absent one. Collapsing the two makes some settings
    impossible to ask for, which is worse than not having the preset at all."""

    def test_explicit_auto_format_beats_a_concrete_preset(self, preset_file):
        # "auto" is a documented value (the MCP tool lists it in its enum), so a
        # caller asking for it must get pipeline-driven formatting rather than
        # silently inheriting the server's square/horizontal choice.
        preset_file.write_text(json.dumps({"output_format": "square"}))
        assert app_module.resolve_output_format("auto") == "auto"

    def test_omitted_format_still_takes_the_preset(self, preset_file):
        preset_file.write_text(json.dumps({"output_format": "square"}))
        assert app_module.resolve_output_format(None) == "square"

    def test_empty_string_format_counts_as_omitted(self, preset_file):
        # An untouched multipart form field arrives as "", not as absent.
        preset_file.write_text(json.dumps({"output_format": "square"}))
        assert app_module.resolve_output_format("") == "square"

    def test_explicit_false_quality_beats_a_true_preset(self, preset_file):
        # Without this a caller can never re-enable the low-resolution
        # confirmation for one job once the preset waives it.
        preset_file.write_text(json.dumps({"force_low_quality": True}))
        assert app_module.resolve_force_low_quality(False) is False

    def test_explicit_false_string_beats_a_true_preset(self, preset_file):
        preset_file.write_text(json.dumps({"force_low_quality": True}))
        assert app_module.resolve_force_low_quality("false") is False

    def test_omitted_quality_still_takes_the_preset(self, preset_file):
        preset_file.write_text(json.dumps({"force_low_quality": True}))
        assert app_module.resolve_force_low_quality(None) is True

    def test_explicit_true_wins_over_a_silent_preset(self, preset_file):
        assert app_module.resolve_force_low_quality(True) is True


class TestStyleEndpoint:
    def test_get_returns_the_current_preset(self, preset_file):
        preset_file.write_text(json.dumps({"output_format": "square"}))
        resp = _client_call("get", "/api/style")
        assert resp.status_code == 200
        assert resp.json()["style"]["output_format"] == "square"

    def test_get_without_a_preset_is_not_an_error(self, preset_file):
        resp = _client_call("get", "/api/style")
        assert resp.status_code == 200
        assert resp.json()["style"] == {}

    def test_put_writes_the_file(self, preset_file):
        resp = _client_call("put", "/api/style",
                            json={"style": {"output_format": "square"}})
        assert resp.status_code == 200
        assert json.loads(preset_file.read_text())["output_format"] == "square"

    def test_put_round_trips_through_get(self, preset_file):
        _client_call("put", "/api/style",
                     json={"style": {"captions": {"font_name": "Anton"}}})
        resp = _client_call("get", "/api/style")
        assert resp.json()["style"]["captions"]["font_name"] == "Anton"

    def test_put_rejects_a_non_object(self, preset_file):
        # Rejected by the request schema (422), not by a hand-rolled check in
        # the handler — a list would otherwise be written to style.json and
        # every later load_style() would silently discard it.
        resp = _client_call("put", "/api/style", json={"style": [1, 2, 3]})
        assert resp.status_code == 422
        assert not preset_file.exists()

    def test_put_is_refused_in_cloud_mode(self, preset_file, monkeypatch):
        # One server-wide default makes no sense when tenants share the
        # instance: whoever saved last would restyle everybody's clips.
        monkeypatch.setattr(app_module, "BILLING_ENABLED", True)
        resp = _client_call("put", "/api/style", json={"style": {}})
        assert resp.status_code == 403


class TestStyleFileIsWrittenAtomically:
    """load_style() runs on every submission, so a save landing mid-submit must
    not be observable. Truncating the file in place lets a job read partial JSON,
    fall back to the built-in defaults and render with the wrong look — and a
    crash mid-write leaves the preset permanently broken."""

    def test_a_failed_write_leaves_the_previous_style_intact(self, preset_file, monkeypatch):
        preset_file.write_text(json.dumps({"output_format": "square"}))

        def boom(*a, **kw):
            raise OSError("disk full")
        monkeypatch.setattr(app_module.json, "dump", boom)

        _client_call("put", "/api/style", json={"style": {"output_format": "vertical"}})

        assert json.loads(preset_file.read_text())["output_format"] == "square"

    def test_no_scratch_file_is_left_behind(self, preset_file, tmp_path):
        _client_call("put", "/api/style", json={"style": {"output_format": "square"}})
        assert [p.name for p in tmp_path.iterdir()] == ["style.json"]

    def test_the_saved_file_is_always_complete(self, preset_file):
        _client_call("put", "/api/style", json={"style": {"captions": {"font_name": "Anton"}}})
        assert json.loads(preset_file.read_text())["captions"]["font_name"] == "Anton"

"""Tests for the per-job branding flag: parsing, job env, and resume.

The geometry tests in test_branding.py all pass whether or not this wiring
works, so a regression here would silently brand — or unbrand — every job while
the suite stayed green. What is load-bearing is the TRI-STATE: "not sent" must
mean "inherit the server's BRAND_WATERMARK", not "off". Collapsing it into a
boolean would disarm the operator's .env for every caller that predates the
parameter (the CLI, the MCP tools).
"""
import json
import os

import pytest

import app as app_module
from app import _build_job_env, _parse_branding


class TestParseBranding:
    @pytest.mark.parametrize("raw", [None, ""])
    def test_absent_means_inherit(self, raw):
        assert _parse_branding(raw) is None

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", True])
    def test_truthy_forms(self, raw):
        assert _parse_branding(raw) is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", "nonsense", False])
    def test_falsy_forms(self, raw):
        assert _parse_branding(raw) is False

    def test_none_is_not_false(self):
        """The distinction the whole feature rests on."""
        assert _parse_branding(None) is not _parse_branding("false")


class TestJobEnv:
    def test_omitted_inherits_the_server_default(self, monkeypatch):
        monkeypatch.setenv("BRAND_WATERMARK", "1")
        env = _build_job_env("k", [], "job1", branding=None)
        assert env.get("BRAND_WATERMARK") == "1"

    def test_omitted_stays_off_when_the_server_is_off(self, monkeypatch):
        monkeypatch.delenv("BRAND_WATERMARK", raising=False)
        env = _build_job_env("k", [], "job1", branding=None)
        assert "BRAND_WATERMARK" not in env

    def test_explicit_false_overrides_the_server_default(self, monkeypatch):
        monkeypatch.setenv("BRAND_WATERMARK", "1")
        env = _build_job_env("k", [], "job1", branding=False)
        assert "BRAND_WATERMARK" not in env

    def test_explicit_true_turns_it_on_for_this_job(self, monkeypatch):
        monkeypatch.delenv("BRAND_WATERMARK", raising=False)
        env = _build_job_env("k", [], "job1", branding=True)
        assert env["BRAND_WATERMARK"] == "1"

    def test_branding_does_not_disturb_the_layout_flags(self, monkeypatch):
        monkeypatch.delenv("BRAND_WATERMARK", raising=False)
        env = _build_job_env("k", ["split"], "job1", branding=True)
        assert env["SPLIT_LAYOUT"] == "1"
        assert env["SPEAKER_SIGNAL"] == "1"
        assert env["BRAND_WATERMARK"] == "1"


class TestResumeManifest:
    """Env is rebuilt from os.environ on resume, so a job submitted with the box
    UNticked would come back branded from the server default unless the resolved
    decision is stored. That is the whole reason it is in the manifest."""

    def _write(self, tmp_path, monkeypatch, job_id, branding):
        monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
        os.makedirs(os.path.join(str(tmp_path), job_id), exist_ok=True)
        app_module._write_resume_manifest(
            job_id, ["python", "main.py"], 1, None, None,
            watermark=False, branding=branding)
        with open(os.path.join(str(tmp_path), job_id, app_module._RESUME_FILE)) as f:
            return json.load(f)

    def test_an_unticked_box_is_persisted(self, tmp_path, monkeypatch):
        assert self._write(tmp_path, monkeypatch, "j1", False)["branding"] is False

    def test_a_ticked_box_is_persisted(self, tmp_path, monkeypatch):
        assert self._write(tmp_path, monkeypatch, "j2", True)["branding"] is True

    def test_branding_is_stored_next_to_watermark(self, tmp_path, monkeypatch):
        """They are two independent marks; the manifest must carry both."""
        m = self._write(tmp_path, monkeypatch, "j3", True)
        assert m["watermark"] is False and m["branding"] is True

    def test_an_unticked_box_survives_a_server_default_of_on(self, monkeypatch):
        """The case the manifest exists for: the server says brand everything,
        this job said no, and the redeploy must not overrule the user."""
        monkeypatch.setenv("BRAND_WATERMARK", "1")
        env = app_module._resume_mark_env(os.environ.copy(), {"branding": False})
        assert "BRAND_WATERMARK" not in env

    def test_a_ticked_box_survives_a_server_default_of_off(self, monkeypatch):
        monkeypatch.delenv("BRAND_WATERMARK", raising=False)
        env = app_module._resume_mark_env(os.environ.copy(), {"branding": True})
        assert env["BRAND_WATERMARK"] == "1"

    def test_the_free_plan_watermark_resumes_independently(self, monkeypatch):
        monkeypatch.delenv("WATERMARK", raising=False)
        monkeypatch.delenv("BRAND_WATERMARK", raising=False)
        env = app_module._resume_mark_env(
            os.environ.copy(), {"watermark": True, "branding": False})
        assert env["WATERMARK"] == "1"
        assert "BRAND_WATERMARK" not in env

    def test_a_manifest_predating_branding_resumes_unbranded(self, monkeypatch):
        """Old manifests on disk have no `branding` key at all."""
        monkeypatch.setenv("BRAND_WATERMARK", "1")
        env = app_module._resume_mark_env(os.environ.copy(), {"watermark": True})
        assert "BRAND_WATERMARK" not in env


class TestConfigEndpoint:
    def _config(self):
        import asyncio
        return asyncio.run(app_module.get_config())

    def test_branding_default_reflects_the_env(self, monkeypatch):
        monkeypatch.setenv("BRAND_WATERMARK", "1")
        assert self._config()["brandingDefault"] is True
        monkeypatch.setenv("BRAND_WATERMARK", "0")
        assert self._config()["brandingDefault"] is False

    def test_availability_follows_the_assets(self, tmp_path, monkeypatch):
        """The checkbox is hidden when ticking it could only be a no-op."""
        from PIL import Image
        monkeypatch.setenv("BRAND_DIR", str(tmp_path))
        assert self._config()["brandingAvailable"] is False
        Image.new("RGBA", (900, 300)).save(tmp_path / "logo.png")
        assert self._config()["brandingAvailable"] is True

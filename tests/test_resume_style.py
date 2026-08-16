"""A resumed job must render like the job it is resuming.

The resume manifest deliberately holds no secrets — the environment is rebuilt
from os.environ on restart. That rebuild used to drop the job's layouts, so a
redeploy halfway through silently finished the run without SPLIT, screencast or
punch-in. The style preset would have joined them.

That is worst exactly where it is least visible: a thirty-file batch interrupted
mid-run would deliver two halves that do not match.
"""
import json

import app as app_module


class TestResumeJobEnv:
    def test_layouts_come_back(self):
        env = app_module._resume_job_env({"layouts": ["split", "punch_in"]})
        assert env["SPLIT_LAYOUT"] == "1"
        assert env["PUNCH_IN"] == "1"

    def test_implied_layouts_come_back_too(self):
        # split needs to know who is speaking; layout_env encodes that, and the
        # resume path must not reimplement it.
        env = app_module._resume_job_env({"layouts": ["split"]})
        assert env["SPEAKER_SIGNAL"] == "1"

    def test_no_layouts_sets_nothing(self):
        env = app_module._resume_job_env({})
        assert "SPLIT_LAYOUT" not in env
        assert "PUNCH_IN" not in env

    def test_style_preset_is_resolved_again(self, tmp_path, monkeypatch):
        # The preset is a server default, so re-reading the file gives the same
        # answer — no need to carry it in the manifest.
        preset = tmp_path / "style.json"
        preset.write_text(json.dumps({"captions": {"font_name": "Anton"}}))
        monkeypatch.setenv("OPENSHORTS_STYLE_FILE", str(preset))

        env = app_module._resume_job_env({})
        assert json.loads(env["OPENSHORTS_STYLE"])["captions"]["font_name"] == "Anton"

    def test_watermark_still_replays(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENSHORTS_STYLE_FILE", str(tmp_path / "none.json"))
        assert app_module._resume_job_env({"watermark": True})["WATERMARK"] == "1"

    def test_watermark_is_cleared_when_the_job_had_none(self, monkeypatch, tmp_path):
        # A free-plan marker left over in the server's own environment must not
        # leak onto a paid job that resumes.
        monkeypatch.setenv("OPENSHORTS_STYLE_FILE", str(tmp_path / "none.json"))
        monkeypatch.setenv("WATERMARK", "1")
        assert "WATERMARK" not in app_module._resume_job_env({"watermark": False})


class TestManifestCarriesLayouts:
    def test_layouts_are_written_to_the_manifest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
        (tmp_path / "job-1").mkdir()

        app_module._write_resume_manifest(
            "job-1", ["python", "main.py"], 2, None, None, watermark=False,
            layouts=["punch_in"])

        manifest = json.loads((tmp_path / "job-1" / app_module._RESUME_FILE).read_text())
        assert manifest["layouts"] == ["punch_in"]

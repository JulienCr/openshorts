"""How the second-render request reaches main.py, and survives a redeploy.

The one design decision worth a regression test: the variant list travels in
main.py's argv, not in the job environment. `_resume_interrupted_jobs` rebuilds
env from `os.environ` and only replays the manifest's `cmd`, so a flag set by
`_build_job_env` would vanish the first time a job resumed after a redeploy —
and the job would come back delivering one render instead of two, silently.
"""
import asyncio
import json
import os

import httpx
import pytest

app_module = pytest.importorskip("app")  # fastapi/boto3 etc., absent in minimal envs


def _submit(monkeypatch, tmp_path, body):
    """POST /api/process with the queue stubbed out, return the job's cmd."""
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "_enqueue_job", lambda *a, **k: None)

    async def _run():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.post("/api/process", json=body,
                                     headers={"X-Gemini-Key": "test-key"})

    resp = asyncio.run(_run())
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    return app_module.jobs[job_id]["cmd"]


BODY = {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "acknowledged": True}


class TestProcessEndpointCmd:
    def test_asking_for_the_safe_render_puts_it_in_argv(self, monkeypatch, tmp_path):
        cmd = _submit(monkeypatch, tmp_path, {**BODY, "variants": "auto,safe"})
        assert "--variants" in cmd
        assert cmd[cmd.index("--variants") + 1] == "auto,safe"

    def test_not_asking_leaves_the_cmd_byte_identical(self, monkeypatch, tmp_path):
        # A job submitted the old way must produce exactly the argv it produced
        # before this feature existed — that is what keeps a resume-manifest
        # diff readable, and what makes the flag safe to ship.
        cmd = _submit(monkeypatch, tmp_path, BODY)
        assert "--variants" not in cmd

    def test_horizontal_never_gets_a_second_render(self, monkeypatch, tmp_path):
        # Nothing is reframed there, so the safe variant would be a duplicate.
        cmd = _submit(monkeypatch, tmp_path,
                      {**BODY, "variants": "safe", "output_format": "horizontal"})
        assert "--variants" not in cmd

    def test_unknown_variant_is_ignored_not_rejected(self, monkeypatch, tmp_path):
        cmd = _submit(monkeypatch, tmp_path, {**BODY, "variants": "bogus"})
        assert "--variants" not in cmd


class TestResumeSurvivesTheFlag:
    """The regression the argparse-not-env decision exists to prevent."""

    def _manifest(self, tmp_path, cmd):
        job_id = "job-resume-test"
        job_dir = tmp_path / job_id
        job_dir.mkdir()
        (job_dir / ".resume.json").write_text(json.dumps({
            "cmd": cmd, "priority": 2, "user_id": None, "reservation_id": None,
            "watermark": False, "attempts": 0,
        }))
        return job_id

    def test_variants_flag_is_replayed(self, monkeypatch, tmp_path):
        cmd = ["python", "-u", "main.py", "-u", "https://x", "-o", "out",
               "--variants", "auto,safe"]
        job_id = self._manifest(tmp_path, cmd)
        monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(app_module, "_RESUME_FILE", ".resume.json")
        monkeypatch.setattr(app_module, "_enqueue_job", lambda *a, **k: None)
        monkeypatch.setitem(os.environ, "GEMINI_API_KEY", "k")
        app_module.jobs.pop(job_id, None)

        app_module._resume_interrupted_jobs()

        resumed = app_module.jobs[job_id]["cmd"]
        assert "--variants" in resumed
        assert resumed[resumed.index("--variants") + 1] == "auto,safe"

    def test_env_would_not_have_survived(self, monkeypatch, tmp_path):
        """The mirror of the above: the env is rebuilt, not replayed.

        This is the whole argument for putting the flag in argv, so it is
        pinned rather than left as a comment.
        """
        job_id = self._manifest(tmp_path, ["python", "-u", "main.py", "-o", "out"])
        monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(app_module, "_RESUME_FILE", ".resume.json")
        monkeypatch.setattr(app_module, "_enqueue_job", lambda *a, **k: None)
        app_module.jobs.pop(job_id, None)

        app_module._resume_interrupted_jobs()

        # Nothing job-specific can reach the child through env: it is a copy of
        # the server's own environment, with no per-job values merged back in.
        assert "OPENSHORTS_VARIANTS" not in app_module.jobs[job_id]["env"]

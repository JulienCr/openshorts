"""The ingest picker's per-source report, and the two ways a mount breaks.

Needs `import app`, so this runs in the backend container rather than on the
host. Self-host mode is guaranteed by tests/conftest.py (BILLING_ENABLED=0).
"""
import errno
import os

import pytest

import app as app_module
import local_stage


def test_an_empty_source_is_reported_rather_than_dropped(tmp_path):
    (tmp_path / "replays").mkdir()
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "a.mkv").write_text("")

    sources = {s["name"]: s for s in app_module._local_ingest_sources(str(tmp_path))}

    assert sources["replays"]["entries"] == 0
    assert sources["replays"]["status"] == "ok"
    assert sources["inbox"]["entries"] == 1


def test_a_dead_mount_stays_in_the_list_and_says_so(tmp_path, monkeypatch):
    """The regression: it used to vanish, which reads as "not configured".

    os.path.isdir() answers False for a mountpoint whose transport died — it
    swallows the ENODEV — so the source was filtered out before anything could
    describe it. A source the operator configured must never disappear from its
    own picker; the whole point of this list is to tell "broken" from "empty".
    """
    (tmp_path / "replays").mkdir()
    (tmp_path / "inbox").mkdir()
    real_listdir = os.listdir

    def dead_replays(path):
        if str(path).endswith("replays"):
            # ENODEV, as measured on WSL 9p. Spelled symbolically on purpose:
            # written as a bare 18 this reads like ENODEV and is EXDEV, and the
            # test then passes against code that never handles a dead mount.
            raise OSError(errno.ENODEV, os.strerror(errno.ENODEV))
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", dead_replays)
    sources = {s["name"]: s for s in app_module._local_ingest_sources(str(tmp_path))}

    assert "replays" in sources, "a broken source must not silently disappear"
    assert sources["replays"]["status"] == "dead"
    assert sources["inbox"]["status"] == "ok"


def test_a_plain_file_beside_the_sources_is_not_one(tmp_path):
    (tmp_path / "notes.txt").write_text("")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "replays").mkdir()

    names = [s["name"] for s in app_module._local_ingest_sources(str(tmp_path))]

    assert names == ["replays"]

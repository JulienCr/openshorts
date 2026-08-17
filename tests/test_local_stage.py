"""Tests for local_stage: filesystem detection and the staging cache.

Stdlib only, no server import — the CI job installs neither FastAPI nor uvicorn.
"""

import errno
import os
import time

import pytest

import local_stage


# A real excerpt: the number of optional fields before " - " varies from line to
# line, and drvfs mountpoints carry octal escapes for spaces.
MOUNTINFO = [
    "25 30 0:24 / / rw,relatime shared:1 - ext4 /dev/sdd rw,discard",
    "132 82 0:70 / /mnt/c rw,noatime - 9p C:\\134 rw,aname=drvfs;path=C:\\",
    # The kernel escapes space/tab/newline/backslash only, so "é" arrives raw…
    "140 82 0:71 / /mnt/j/Drive\\040partagés rw - 9p J:\\134 rw",
    # …but an escaped byte pair must still decode to one character, not two.
    "141 82 0:72 / /mnt/k/partag\\303\\251 rw - 9p K:\\134 rw",
    "150 25 0:80 / /ingest rw - ext4 /dev/sdd rw",
    "151 25 0:81 / /ingestion rw - 9p X:\\134 rw",
    "malformed line without a separator",
]


def test_parse_mountinfo_handles_variable_optional_fields():
    mounts = dict(local_stage.parse_mountinfo(MOUNTINFO))
    # "shared:1" present on / and absent on /mnt/c: indexing by position would
    # read the fstype off by one on exactly one of them.
    assert mounts["/"] == "ext4"
    assert mounts["/mnt/c"] == "9p"


def test_parse_mountinfo_unescapes_octal():
    mounts = dict(local_stage.parse_mountinfo(MOUNTINFO))
    # A space in the mountpoint is real on this deployment (REPLAY_DIR is
    # "/mnt/j/Drive partagés/..."). Leaving it escaped makes every prefix match
    # fail, and a failed match silently reads as "fast storage".
    assert "/mnt/j/Drive partagés" in mounts
    # One escape is one byte: unescaping per character would yield "partagÃ©".
    assert "/mnt/k/partagé" in mounts


def test_parse_mountinfo_skips_malformed_lines():
    assert all(mp for mp, _fs in local_stage.parse_mountinfo(MOUNTINFO))


def test_fstype_for_matches_the_longest_mountpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(local_stage, "_mounts",
                        lambda: local_stage.parse_mountinfo(MOUNTINFO))
    monkeypatch.setattr(os.path, "realpath", lambda p: p)
    assert local_stage.fstype_for("/ingest/replays/a.mkv") == "ext4"
    assert local_stage.fstype_for("/mnt/c/foo") == "9p"
    assert local_stage.fstype_for("/etc/hosts") == "ext4"  # falls back to /


def test_fstype_for_does_not_match_a_partial_component(monkeypatch):
    monkeypatch.setattr(local_stage, "_mounts",
                        lambda: local_stage.parse_mountinfo(MOUNTINFO))
    monkeypatch.setattr(os.path, "realpath", lambda p: p)
    # /ingestion must resolve to its own mount, not to /ingest.
    assert local_stage.fstype_for("/ingestion/a.mkv") == "9p"


def test_fstype_for_returns_empty_without_proc(monkeypatch):
    monkeypatch.setattr(local_stage, "_mounts", list)
    assert local_stage.fstype_for("/anything") == ""


# --- dir_state ----------------------------------------------------------------

def test_dir_state_counts_a_readable_directory(tmp_path):
    (tmp_path / "a.mkv").write_text("")
    (tmp_path / "notes.txt").write_text("")
    # Every entry, not just the videos: a source with literally nothing in it is
    # the signal, and filtering by extension would hide a folder of .srt files.
    assert local_stage.dir_state(str(tmp_path)) == ("ok", 2)


def test_dir_state_reports_an_empty_directory_as_ok(tmp_path):
    # Empty is not broken. It is the case the picker already words correctly.
    assert local_stage.dir_state(str(tmp_path)) == ("ok", 0)


@pytest.mark.parametrize("err", [errno.ENODEV, errno.ENOTCONN, errno.ESTALE,
                                 errno.ENXIO, errno.EIO, errno.EREMOTEIO])
def test_dir_state_reports_a_mount_whose_transport_died(monkeypatch, err):
    """The mount is still in the table; the thing underneath it is gone.

    Measured on WSL: a Google Drive client restart takes the 9p session down and
    leaves the mountpoint in /proc/mounts, where every syscall against it then
    returns ENODEV. sshfs and NFS reach the same state through ENOTCONN/ESTALE.
    """
    def dead(_path):
        raise OSError(err, os.strerror(err))

    monkeypatch.setattr(os, "listdir", dead)
    assert local_stage.dir_state("/ingest/replays") == ("dead", 0)


@pytest.mark.parametrize("err", [errno.EACCES, errno.EPERM])
def test_dir_state_keeps_a_directory_it_cannot_read(monkeypatch, err):
    """Unreadable is neither dead nor missing, and must not be either.

    os.path.isdir() answers True here — stat only needs to traverse the parent —
    so the source used to reach the picker with entries: 0. Folding EACCES into
    "absent" makes the caller drop it, which is the disappearance this whole
    change exists to stop, reintroduced through another errno. A UID/GID
    mismatch on a bind mount is the ordinary way to land here.
    """
    def refused(_path):
        raise OSError(err, os.strerror(err))

    monkeypatch.setattr(os, "listdir", refused)
    assert local_stage.dir_state("/ingest/replays") == ("unreadable", 0)


def test_dir_state_shows_an_unknown_error_rather_than_hiding_it(monkeypatch):
    """"absent" is an allowlist, like FAST_FSTYPES above and for the same reason.

    An errno nobody thought of costs one puzzling line in the picker here; the
    reverse costs a configured source vanishing with nothing said.
    """
    def odd(_path):
        raise OSError(errno.ELOOP, os.strerror(errno.ELOOP))

    monkeypatch.setattr(os, "listdir", odd)
    assert local_stage.dir_state("/ingest/replays")[0] == "unreadable"


def test_dir_state_does_not_call_a_missing_directory_dead(tmp_path):
    # "dead" has to mean something narrow, or the picker cries wolf on every
    # typo in LOCAL_INGEST_DIR.
    assert local_stage.dir_state(str(tmp_path / "nope"))[0] == "absent"


def test_dir_state_does_not_call_a_plain_file_dead(tmp_path):
    f = tmp_path / "a.mkv"
    f.write_text("")
    assert local_stage.dir_state(str(f))[0] == "absent"


# --- is_slow ------------------------------------------------------------------

@pytest.fixture
def fs(monkeypatch):
    """Let a test declare the fstype of any path."""
    table = {}
    monkeypatch.setattr(local_stage, "fstype_for", lambda p: table.get(p, "ext4"))
    return table


def test_is_slow_uses_an_allowlist(monkeypatch, fs):
    monkeypatch.setattr(local_stage, "MODE", "auto")
    monkeypatch.setattr(local_stage, "_stage_root", lambda: "/stage")
    fs["/src.mkv"] = "9p"
    assert local_stage.is_slow("/src.mkv")
    fs["/src.mkv"] = "xfs"
    assert not local_stage.is_slow("/src.mkv")
    # An fstype nobody listed is assumed slow: one wasted copy, never a missed one.
    fs["/src.mkv"] = "somethingnew"
    assert local_stage.is_slow("/src.mkv")


def test_is_slow_honours_the_override(monkeypatch, fs):
    monkeypatch.setattr(local_stage, "_stage_root", lambda: "/stage")
    fs["/src.mkv"] = "ext4"
    monkeypatch.setattr(local_stage, "MODE", "always")
    assert local_stage.is_slow("/src.mkv")
    fs["/src.mkv"] = "9p"
    monkeypatch.setattr(local_stage, "MODE", "never")
    assert not local_stage.is_slow("/src.mkv")


def test_is_slow_refuses_when_the_destination_is_slow_too(monkeypatch, fs):
    monkeypatch.setattr(local_stage, "MODE", "auto")
    monkeypatch.setattr(local_stage, "_stage_root", lambda: "/stage")
    fs["/src.mkv"] = "9p"
    fs["/stage"] = "9p"   # repo cloned on /mnt/d: copying pays the wire twice
    assert not local_stage.is_slow("/src.mkv")


# --- cache --------------------------------------------------------------------

@pytest.fixture
def stage(monkeypatch, tmp_path):
    """A cache rooted in tmp_path, with generous limits unless a test says otherwise."""
    root = tmp_path / "stage"
    monkeypatch.setattr(local_stage, "STAGE_DIR", str(root))
    monkeypatch.setattr(local_stage, "TTL_SECONDS", 12 * 3600)
    monkeypatch.setattr(local_stage, "MAX_GB", 100.0)
    monkeypatch.setattr(local_stage, "MIN_FREE_GB", 0.0)
    monkeypatch.setattr(local_stage, "_refs", {})
    return root


def _source(tmp_path, name="2025-08-10-retour-avignon.mkv", data=b"video-bytes"):
    p = tmp_path / "src" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return str(p)


def test_key_changes_when_the_source_changes(tmp_path):
    src = _source(tmp_path)
    first = local_stage._key_for(src)
    os.utime(src, (0, 0))
    assert local_stage._key_for(src) != first


def test_staging_preserves_the_original_basename(stage, tmp_path):
    src = _source(tmp_path)
    staged = local_stage.acquire(src)
    # main.py derives the project title from the filename, so a hashed name would
    # rename every project of a batch to gibberish in the library.
    assert os.path.basename(staged) == "2025-08-10-retour-avignon.mkv"
    assert os.path.dirname(os.path.dirname(staged)) == str(stage)
    assert open(staged, "rb").read() == b"video-bytes"


def test_second_acquire_reuses_the_copy(stage, tmp_path):
    src = _source(tmp_path)
    staged = local_stage.acquire(src)
    marker = os.stat(staged).st_mtime_ns
    again = local_stage.acquire(src)
    assert again == staged
    assert os.stat(staged).st_mtime_ns == marker   # not recopied


def test_a_failed_copy_leaves_nothing_to_mistake_for_a_hit(stage, tmp_path, monkeypatch):
    src = _source(tmp_path, data=b"x" * 4096)

    def boom(*_a, **_kw):
        raise OSError("link died mid-copy")

    monkeypatch.setattr(local_stage, "_copy", boom)
    assert local_stage.acquire(src) == src          # falls back, does not raise
    assert local_stage.staged_path_for(src) is None
    assert not any(p.name.endswith(".part") for p in stage.iterdir())


def test_out_of_space_falls_back_instead_of_failing(stage, tmp_path, monkeypatch):
    src = _source(tmp_path)
    monkeypatch.setattr(local_stage, "_room_for", lambda *_a, **_kw: False)
    assert local_stage.acquire(src) == src
    assert local_stage._refs == {}                  # no reference leaked


# --- sweep --------------------------------------------------------------------

def test_sweep_expires_entries_past_the_ttl(stage, tmp_path):
    src = _source(tmp_path)
    staged = local_stage.acquire(src)
    local_stage.release(staged)
    local_stage.sweep(now=time.time() + 13 * 3600, log=lambda _m: None)
    assert local_stage.staged_path_for(src) is None


def test_sweep_keeps_entries_within_the_ttl(stage, tmp_path):
    src = _source(tmp_path)
    staged = local_stage.acquire(src)
    local_stage.release(staged)
    local_stage.sweep(now=time.time() + 3600, log=lambda _m: None)
    assert local_stage.staged_path_for(src) == staged


def test_ttl_zero_disables_the_sweep(stage, tmp_path, monkeypatch):
    # The uploads sweep in app.py makes the opposite choice: `now - mtime > 0` is
    # true for everything, so a retention of zero — which reads as "off" — wipes
    # the directory on the next pass. This asserts we did not copy that.
    monkeypatch.setattr(local_stage, "TTL_SECONDS", 0)
    src = _source(tmp_path)
    staged = local_stage.acquire(src)
    local_stage.release(staged)
    local_stage.sweep(now=time.time() + 10 ** 6, log=lambda _m: None)
    assert os.path.isfile(staged)


def test_sweep_never_drops_an_entry_in_use(stage, tmp_path):
    src = _source(tmp_path)
    staged = local_stage.acquire(src)          # acquired, never released
    local_stage.sweep(now=time.time() + 13 * 3600, log=lambda _m: None)
    # A long render on a reused source outlives 12 h; age must not outrank use.
    assert os.path.isfile(staged)


def test_eviction_skips_referenced_entries(stage, tmp_path, monkeypatch):
    held = local_stage.acquire(_source(tmp_path, "held.mkv", b"a" * 1024))
    free = local_stage.acquire(_source(tmp_path, "free.mkv", b"b" * 1024))
    local_stage.release(free)
    local_stage._evict(need_bytes=10 ** 9, log=lambda _m: None)
    assert os.path.isfile(held)
    assert not os.path.isfile(free)


def test_release_drops_the_reference(stage, tmp_path):
    staged = local_stage.acquire(_source(tmp_path))
    local_stage.release(staged)
    assert local_stage._refs == {}


def test_release_restarts_the_ttl_clock(stage, tmp_path):
    """A render longer than the TTL must not expire the moment it finishes."""
    src = _source(tmp_path)
    staged = local_stage.acquire(src)
    key_dir = os.path.dirname(staged)
    # Pretend the copy was made 13h ago and the job has been reading it since.
    old = time.time() - 13 * 3600
    os.utime(key_dir, (old, old))

    local_stage.sweep(now=time.time(), log=lambda _m: None)
    assert os.path.isfile(staged), "an entry still in use must survive its TTL"

    local_stage.release(staged)
    local_stage.sweep(now=time.time(), log=lambda _m: None)
    # The TTL is "since last use", so letting go must restart it — otherwise the
    # copy dies on the next sweep and the re-run it exists for pays full price.
    assert os.path.isfile(staged)


def test_release_of_an_unstaged_source_is_harmless(stage, tmp_path):
    local_stage.release_key("never-staged")  # no directory to touch
    assert local_stage._refs == {}

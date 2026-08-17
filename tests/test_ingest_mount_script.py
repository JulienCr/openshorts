"""Tests for scripts/ensure-ingest-mounts.sh.

The script is driven as a subprocess with `mount`, `umount`, `mountpoint` and
`stat` stubbed on PATH, so none of this needs root or a real filesystem to
mount. What is asserted is which of those get called and in what order, which
is the whole of the script's contract.

Why `stat` is stubbed rather than the state simulated: a dead mount answers
ENODEV to every syscall, and there is no way to produce one without root. The
tempting stand-in — a mountpoint under a mode-000 directory — reproduces the
shape exactly for a normal user and not at all for root, and the suite's real
gate runs in a container as root (`docker run -u 0`), where it would have
quietly passed while testing nothing. Stubbing the predicate the script actually
branches on works as any uid. That the predicate matches reality was measured
separately, on the live failure of 17 Aug 2026:

    $ stat /mnt/j
    stat: cannot statx '/mnt/j': No such device      # exit 1
    $ mountpoint /mnt/j
    mountpoint: /mnt/j: No such device               # exit 1, i.e. "not mounted"
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ensure-ingest-mounts.sh"

pytestmark = pytest.mark.skipif(
    not SCRIPT.exists() or shutil.which("bash") is None,
    reason="needs the shell script and bash",
)


def _stub(bin_dir, name, body):
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _run(tmp_path, mountpoint, *, mounted=False, unstattable=None):
    """Run the script against one spec; return (proc, [calls in order])."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"

    _stub(bin_dir, "mountpoint", f"exit {0 if mounted else 1}\n")
    _stub(bin_dir, "mount", f'echo "mount $*" >> "{log}"\nexit 0\n')
    _stub(bin_dir, "umount", f'echo "umount $*" >> "{log}"\nexit 0\n')
    if unstattable is not None:
        _stub(bin_dir, "stat", f'case "$*" in *"{unstattable}"*) exit 1 ;; esac\nexit 0\n')

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["INGEST_MOUNTS"] = f"J: {mountpoint} drvfs uid=1000,gid=1000"
    env["INGEST_PENDING_FLAG"] = str(tmp_path / "pending")
    env.pop("ON_MOUNT_CMD", None)

    proc = subprocess.run(["bash", str(SCRIPT)], env=env,
                          capture_output=True, text=True, timeout=60)
    calls = log.read_text().splitlines() if log.exists() else []
    return proc, calls


def test_a_stale_mount_is_detached_before_remounting(tmp_path):
    """The regression: the script used to give up before ever calling mount.

    A mount whose transport died stays in /proc/mounts, so this is not the
    "drive was never there" case. `mountpoint -q` reports "not mounted" — it
    cannot stat it either — so the loop does not skip; but `[ -d ]` is then
    false and `mkdir -p` fails on the unstattable path, which sent it straight
    to `continue`. Once a minute, forever, with the drive still dead.
    """
    mountpoint = tmp_path / "mnt" / "j"
    mountpoint.mkdir(parents=True)

    proc, calls = _run(tmp_path, mountpoint, unstattable=str(mountpoint))

    assert [c.split()[0] for c in calls] == ["umount", "mount"], \
        f"{calls}\n{proc.stderr}"
    assert calls[0].startswith("umount -l "), calls[0]


def test_a_mount_that_is_already_up_is_left_alone(tmp_path):
    """The no-op property: this runs every 60s and must stay silent."""
    mountpoint = tmp_path / "mnt" / "j"
    mountpoint.mkdir(parents=True)

    proc, calls = _run(tmp_path, mountpoint, mounted=True)

    assert calls == [], calls
    assert proc.returncode == 0, proc.stderr


def test_a_healthy_mountpoint_is_not_unmounted_on_the_way_in(tmp_path):
    """Detaching must be reserved for the mountpoint that cannot be stat'd.

    `umount -l` on a directory that is merely waiting for its drive is a no-op
    today, but making it unconditional would put a detach on the path of every
    ordinary retry — and this unit runs against live mounts once a minute.
    """
    mountpoint = tmp_path / "mnt" / "j"
    mountpoint.mkdir(parents=True)

    proc, calls = _run(tmp_path, mountpoint)

    assert [c.split()[0] for c in calls] == ["mount"], f"{calls}\n{proc.stderr}"


def test_an_absent_mountpoint_is_still_created_and_mounted(tmp_path):
    """The original case (the drive was never mounted) must keep working."""
    mountpoint = tmp_path / "mnt" / "j"

    proc, calls = _run(tmp_path, mountpoint)

    assert any(c.startswith("mount ") for c in calls), f"{calls}\n{proc.stderr}"
    assert mountpoint.is_dir()

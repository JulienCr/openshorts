"""Tests for scripts/install-ingest-mount-timer.sh.

Installs into a tmpdir (BIN_DIR/SYSTEMD_DIR/CONF_DIR) with `systemctl` stubbed,
so this runs as any uid and touches nothing on the machine.

Worth testing at all because the installer is the part that was missing: the
previous fix shipped as six commands in a README, nobody ran them, and the
outage repeated with the automation sitting unused in the repo. An installer
that aborts halfway is the same failure wearing a script.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent
          / "scripts" / "install-ingest-mount-timer.sh")

pytestmark = pytest.mark.skipif(
    not SCRIPT.exists() or shutil.which("bash") is None,
    reason="needs the shell script and bash",
)


def _install(tmp_path, **extra_env):
    bin_dir = tmp_path / "bin"
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir(exist_ok=True)
    systemctl_log = tmp_path / "systemctl.log"
    stub = stub_dir / "systemctl"
    stub.write_text(f'#!/bin/sh\necho "$*" >> "{systemctl_log}"\nexit 0\n')
    stub.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
    env["BIN_DIR"] = str(bin_dir)
    env["SYSTEMD_DIR"] = str(tmp_path / "systemd")
    env["CONF_DIR"] = str(tmp_path / "conf")
    env.pop("INGEST_MOUNTS", None)
    env.pop("ON_MOUNT_CMD", None)
    env.update(extra_env)

    proc = subprocess.run(["bash", str(SCRIPT)], env=env,
                          capture_output=True, text=True, timeout=120)
    conf = tmp_path / "conf" / "ingest-mounts.conf"
    return proc, conf, (systemctl_log.read_text() if systemctl_log.exists() else "")


def test_it_installs_the_units_and_arms_the_timer(tmp_path):
    proc, conf, systemctl = _install(
        tmp_path, INGEST_MOUNTS="J: /mnt/j drvfs uid=1000", ON_MOUNT_CMD="true")

    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "bin" / "ensure-ingest-mounts.sh").exists()
    assert (tmp_path / "systemd" / "openshorts-ingest-mount.timer").exists()
    assert (tmp_path / "systemd" / "openshorts-ingest-mount.service").exists()
    assert 'INGEST_MOUNTS="J: /mnt/j drvfs uid=1000"' in conf.read_text()
    assert 'ON_MOUNT_CMD="true"' in conf.read_text()
    assert "daemon-reload" in systemctl
    assert "enable --now openshorts-ingest-mount.timer" in systemctl


def test_it_finishes_without_an_on_mount_cmd(tmp_path):
    """ON_MOUNT_CMD is optional, and skipping it must not strand the install.

    The installer runs under `set -e` and writes the config from a command
    group, so a conditional line that is merely absent is one plausible way to
    exit after the config exists and before the timer is enabled — leaving an
    install that looks done and never fires.
    """
    proc, conf, systemctl = _install(tmp_path, INGEST_MOUNTS="J: /mnt/j drvfs")

    assert proc.returncode == 0, proc.stderr
    assert "ON_MOUNT_CMD" not in conf.read_text()
    assert "enable --now openshorts-ingest-mount.timer" in systemctl


def test_it_leaves_an_existing_config_alone(tmp_path):
    """Re-running is the supported way to pick up a change to the script; the
    config is the one file here holding hand-written local truth."""
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    (conf_dir / "ingest-mounts.conf").write_text('INGEST_MOUNTS="hand written"\n')

    proc, conf, systemctl = _install(tmp_path, INGEST_MOUNTS="J: /mnt/j drvfs")

    assert proc.returncode == 0, proc.stderr
    assert conf.read_text() == 'INGEST_MOUNTS="hand written"\n'
    assert "enable --now openshorts-ingest-mount.timer" in systemctl


def test_without_a_spec_it_still_installs_but_stays_inert(tmp_path):
    """The unit reads the config with a leading "-", so an inert timer is the
    right outcome of an install with nothing to mount — not a failure."""
    proc, conf, systemctl = _install(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "#INGEST_MOUNTS=" in conf.read_text()
    assert "--force-recreate" in conf.read_text(), \
        "the commented ON_MOUNT_CMD must not teach the plain `up -d` that no-ops"

#!/usr/bin/env bash
# Install (or re-install) the ingest-mount timer. Idempotent, run as root.
#
# Why this is a script and not the copy-paste block it replaces: that block *was*
# the fix for the outage of 16 Aug 2026, it stayed in the README, and nobody ran
# it — so on the 17th the same drive dropped again with the automation sitting
# unused in the repo. A fix that needs a human to transcribe six commands is a
# fix that is off by default.
#
# Re-running it is the supported way to pick up a change to the script or the
# units. An existing /etc/openshorts/ingest-mounts.conf is never overwritten:
# it is the one file here that holds hand-written local truth.
#
#     sudo scripts/install-ingest-mount-timer.sh
#     sudo INGEST_MOUNTS="J: /mnt/j drvfs uid=1000,gid=1000,metadata,noatime" \
#          ON_MOUNT_CMD="cd /srv/openshorts && docker compose ... up -d --force-recreate backend" \
#          scripts/install-ingest-mount-timer.sh
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# Overridable so the tests can install into a tmpdir. Not a documented knob:
# every real install writes to these.
BIN_DIR="${BIN_DIR:-/usr/local/bin}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
CONF_DIR="${CONF_DIR:-/etc/openshorts}"
CONF="$CONF_DIR/ingest-mounts.conf"

if [ "$BIN_DIR" = "/usr/local/bin" ] && [ "$(id -u)" -ne 0 ]; then
    echo "This writes to /usr/local/bin and /etc/systemd/system — run it as root." >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "No systemctl here. Under WSL, systemd needs 'systemd=true' in /etc/wsl.conf" >&2
    echo "and a 'wsl --shutdown' to take effect." >&2
    exit 1
fi

install -d "$BIN_DIR" "$SYSTEMD_DIR"
install -m 755 "$REPO_ROOT/scripts/ensure-ingest-mounts.sh" "$BIN_DIR/"
install -m 644 "$REPO_ROOT/scripts/openshorts-ingest-mount.service" \
               "$REPO_ROOT/scripts/openshorts-ingest-mount.timer" \
               "$SYSTEMD_DIR/"
echo "Installed the script and the units."

mkdir -p "$CONF_DIR"
if [ -e "$CONF" ]; then
    echo "Kept $CONF as it is."
elif [ -n "${INGEST_MOUNTS:-}" ]; then
    # systemd's EnvironmentFile takes one KEY="value" per line and does not
    # understand continuations, so a spec with an embedded double quote would
    # need editing by hand. Nothing here has one.
    {
        printf '# Written by scripts/install-ingest-mount-timer.sh. Edit freely;\n'
        printf '# re-running the installer will not touch this file again.\n'
        printf 'INGEST_MOUNTS="%s"\n' "$INGEST_MOUNTS"
        if [ -n "${ON_MOUNT_CMD:-}" ]; then
            printf 'ON_MOUNT_CMD="%s"\n' "$ON_MOUNT_CMD"
        fi
    } > "$CONF"
    chmod 644 "$CONF"
    echo "Wrote $CONF."
else
    # The unit reads this file with a leading "-", so an inert timer is the
    # correct outcome of an install with nothing to mount — not a failure.
    {
        printf '# <source> <mountpoint> <fstype> [mount options], one per line.\n'
        printf '# The timer stays a no-op until INGEST_MOUNTS is set.\n'
        printf '#INGEST_MOUNTS="J: /mnt/j drvfs uid=1000,gid=1000,metadata,noatime"\n'
        printf '# Re-run Compose after a mount comes back. --force-recreate is not\n'
        printf '# optional: see scripts/README.md.\n'
        printf '#ON_MOUNT_CMD="cd /path/to/openshorts && docker compose -f docker-compose.yml -f docker-compose.ingest.yml up -d --force-recreate backend"\n'
    } > "$CONF"
    chmod 644 "$CONF"
    echo "Wrote a commented $CONF — fill in INGEST_MOUNTS to arm the timer."
fi

systemctl daemon-reload
systemctl enable --now openshorts-ingest-mount.timer

# Report rather than assume: `enable --now` can succeed and the first run still
# fail, and a timer nobody verified is how this ended up uninstalled for a day.
systemctl list-timers openshorts-ingest-mount.timer --no-pager || true
echo
echo "Last run:"
systemctl status openshorts-ingest-mount.service --no-pager -n 10 || true

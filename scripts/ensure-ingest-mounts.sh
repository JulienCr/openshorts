#!/usr/bin/env bash
# Mount the ingest sources that are not there yet, and say nothing when they are.
#
# Why this exists: a drive that appears *after* the machine has booted never gets
# mounted on its own. Under WSL that is the normal case for a cloud drive — Google
# Drive File Stream creates its drive letter once the Windows client is up, well
# after WSL has finished its automount pass — and /etc/fstab does not help, since
# WSL walks it exactly once at boot and a failure there is final. The same shape
# happens on a plain server with an SMB share that comes back after the network.
#
# So this is run by a timer, not at boot, and it is a no-op on every tick where
# the mount is already there.
#
# Configure with INGEST_MOUNTS, one spec per line:
#     <source> <mountpoint> <fstype> [mount options]
# e.g. INGEST_MOUNTS="J: /mnt/j drvfs uid=1000,gid=1000,metadata,noatime"
#
# ON_MOUNT_CMD runs once after any mount succeeds — set it to the compose command
# that brings the backend up. This is NOT optional garnish: with
# `create_host_path: false`, a missing bind source makes Docker fail at *container
# creation*, so no container exists and `restart: unless-stopped` has nothing to
# restart. Measured: `docker compose up -d` errors out and `docker compose ps -a`
# lists nothing; and an already-created container whose source went away stays
# `exited` after a failed start, with no daemon retry. Mounting the drive without
# this leaves the backend down until a human re-runs Compose.
set -uo pipefail

if [ -z "${INGEST_MOUNTS:-}" ]; then
    echo "INGEST_MOUNTS is empty — nothing to do." >&2
    exit 0
fi

status=0
mounted_any=0
while read -r src mountpoint fstype options; do
    [ -z "${src:-}" ] && continue

    if mountpoint -q "$mountpoint"; then
        continue
    fi

    # A mount can sit in the table with its transport dead underneath it, which
    # is a different failure from "never mounted" and the one that actually
    # recurs: the cloud client restarts, the drive letter comes back, and the 9p
    # session WSL held is gone — the mountpoint stays listed in /proc/mounts and
    # every syscall against it answers ENODEV. Nothing above catches that.
    # `mountpoint -q` reports "not mounted" (it cannot stat it either) so we do
    # not skip, but `[ -d ]` below is then false and `mkdir -p` fails on the
    # unstattable path, which sent the loop to `continue` without ever reaching
    # the mount — forever, once a minute. Detach the corpse first; when there is
    # simply nothing at this path, the umount fails and costs nothing.
    if ! stat "$mountpoint" >/dev/null 2>&1; then
        umount -l "$mountpoint" 2>/dev/null
    fi

    # A previous failed start may have left a directory here — Docker creates a
    # missing bind source on the host, which is exactly how an unmounted drive
    # turns into a silently empty ingest folder. Removing it while it is empty
    # keeps "this directory is empty" meaning one single thing. rmdir, never rm
    # -rf: if anything real is in there, we must not be the one deleting it.
    if [ -d "$mountpoint" ]; then
        find "$mountpoint" -mindepth 1 -depth -type d -exec rmdir {} + 2>/dev/null
    else
        mkdir -p "$mountpoint" || { status=1; continue; }
    fi

    if mount -t "$fstype" "$src" "$mountpoint" ${options:+-o "$options"}; then
        echo "Mounted $src on $mountpoint ($fstype)."
        mounted_any=1
    else
        # Not an error worth failing the unit over: the drive is simply not up
        # yet, and the timer will come back in a minute.
        echo "Still waiting for $src (not mountable yet)." >&2
    fi
done <<< "$INGEST_MOUNTS"

# A pending marker, because "did a mount just happen" is the wrong question on
# every tick but the first. This unit is ordered after local-fs.target only, so at
# boot it can easily mount the drive before dockerd is listening; ON_MOUNT_CMD
# then fails, and on the next tick the drive is already mounted, mounted_any is 0,
# and nothing would ever retry — the backend stays down while the timer keeps
# cheerfully firing. The marker lives in /run, so it is cleared by a reboot, which
# is exactly right: a fresh boot re-mounts and re-arms on its own.
PENDING_FLAG="${INGEST_PENDING_FLAG:-/run/openshorts-ingest-start-pending}"

if [ -n "${ON_MOUNT_CMD:-}" ] && { [ "$mounted_any" = "1" ] || [ -f "$PENDING_FLAG" ]; }; then
    # Armed *before* the attempt: a command that hangs and gets killed must still
    # be retried, and a marker written only on failure would be lost with it.
    : > "$PENDING_FLAG" 2>/dev/null
    echo "Mounts present — running: $ON_MOUNT_CMD"
    # `up -d` and not `start`: a bind mount is resolved when the container is
    # created, so a container created against the old (absent or empty) path
    # would keep seeing it. Compose recreates it when the mount config differs
    # and is a no-op when it does not — which is what makes retrying free.
    if sh -c "$ON_MOUNT_CMD"; then
        rm -f "$PENDING_FLAG"
    else
        echo "ON_MOUNT_CMD failed — retrying on the next tick." >&2
        status=1
    fi
fi

exit "$status"

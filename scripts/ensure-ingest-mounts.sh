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
set -uo pipefail

if [ -z "${INGEST_MOUNTS:-}" ]; then
    echo "INGEST_MOUNTS is empty — nothing to do." >&2
    exit 0
fi

status=0
while read -r src mountpoint fstype options; do
    [ -z "${src:-}" ] && continue

    if mountpoint -q "$mountpoint"; then
        continue
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
    else
        # Not an error worth failing the unit over: the drive is simply not up
        # yet, and the timer will come back in a minute.
        echo "Still waiting for $src (not mountable yet)." >&2
    fi
done <<< "$INGEST_MOUNTS"

exit "$status"

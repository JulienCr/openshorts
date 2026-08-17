# Keeping the ingest mounts alive

Optional, and only useful when a source folder lives on a drive that is **not
there when the machine boots**. A plain Linux server with local disks does not
need any of this.

There are two ways for the drive to be missing, they do not look alike, and the
timer has to handle both.

**Never mounted.** Under WSL, a Google Drive (or any cloud client) creates its
drive letter once the Windows client is up, which is well after WSL has done its
automount pass. The drive is then never mounted, Docker creates the missing bind
source as an empty directory, and the ingest tab quietly lists nothing from it.
`/etc/fstab` does not help — WSL walks it once at boot and a failure there is
final.

**Mounted, then dead underneath.** The client restarts mid-session; the drive
letter goes away and comes back on the Windows side, but the 9p session WSL held
does not follow. The mountpoint stays listed in `/proc/mounts` and every syscall
against it answers `ENODEV` — `stat`, `ls`, even `mountpoint` itself, which then
reports "not mounted" because it cannot stat it either. Measured on 17 Aug 2026:
a `GoogleDriveFS` process started at 17:35 and took `/mnt/j` down with it while
`J:` stayed present in Windows. `ensure-ingest-mounts.sh` detaches such a mount
(`umount -l`) before remounting, because `mkdir -p` fails on a path in that state
and the loop used to give up there without ever reaching `mount`.

Hence a timer that retries, and is a no-op on every tick where the mount is
already up.

## Install

```bash
sudo scripts/install-ingest-mount-timer.sh
```

Idempotent — re-run it to pick up a change to the script or the units. It never
overwrites an existing `/etc/openshorts/ingest-mounts.conf`; pass the settings on
the first run, or edit that file afterwards:

```bash
sudo INGEST_MOUNTS="J: /mnt/j drvfs uid=1000,gid=1000,metadata,noatime" \
     ON_MOUNT_CMD="cd /home/julien/dev/openshorts && docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.ingest.yml -f docker-compose.override.yml up -d --force-recreate backend" \
     scripts/install-ingest-mount-timer.sh
```

Two details in that `ON_MOUNT_CMD` are load-bearing, and both were wrong here
until a drive dropped and proved it:

- **`--force-recreate`**, not a plain `up -d`. Compose diffs the *declared*
  config, and after a remount the bind path string is character-for-character
  what it was — so `up -d` answers `Container openshorts-backend Running` and
  changes nothing, leaving the container holding the dead mount. Measured. It
  only fires on a tick where a mount actually came back, so it is not recreating
  the backend every minute; a job running at that moment is lost and recovered
  from the resume manifest on restart.
- **Every compose file the container was created with**, `docker-compose.override.yml`
  included. Listing fewer recreates the backend on a different config — dropping
  the override here also drops the published port. `docker inspect <name> --format
  '{{index .Config.Labels "com.docker.compose.project.config_files"}}'` prints the
  set actually in use.

Check it:

```bash
systemctl list-timers openshorts-ingest-mount.timer
journalctl -u openshorts-ingest-mount.service -n 20
```

## Why ON_MOUNT_CMD is required, not optional

A Docker bind mount is resolved when the container is created, and Docker's
mounts are private, so a drive mounted *after* the container came up does not
appear inside it. Compose has to be re-run.

`restart: unless-stopped` does **not** cover this, which is worth stating plainly
because it looks like it should. Measured both ways:

- **Source missing at `up`** — Docker fails at container *creation*:
  `invalid mount config for type "bind": bind source path does not exist`, and
  `docker compose ps -a` then lists nothing. There is no container, so there is
  no restart policy in effect.
- **Container created earlier, source removed since** — `docker start` fails with
  the same error and the container stays `exited`. The daemon does not retry it.

So without `ON_MOUNT_CMD` the timer would mount the drive, log success, and leave
the backend down until someone re-ran Compose by hand.

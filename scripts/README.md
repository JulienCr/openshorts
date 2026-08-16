# Keeping the ingest mounts alive

Optional, and only useful when a source folder lives on a drive that is **not
there when the machine boots**. A plain Linux server with local disks does not
need any of this.

The case it exists for: under WSL, a Google Drive (or any cloud client) creates
its drive letter once the Windows client is up, which is well after WSL has done
its automount pass. The drive is then never mounted, Docker creates the missing
bind source as an empty directory, and the ingest tab quietly lists nothing from
it. `/etc/fstab` does not help — WSL walks it once at boot and a failure there is
final. Hence a timer that retries, and is a no-op on every tick where the mount
is already up.

## Install

```bash
sudo install -m 755 scripts/ensure-ingest-mounts.sh /usr/local/bin/
sudo install -m 644 scripts/openshorts-ingest-mount.service \
                    scripts/openshorts-ingest-mount.timer /etc/systemd/system/

sudo mkdir -p /etc/openshorts
sudo tee /etc/openshorts/ingest-mounts.conf >/dev/null <<'EOF'
# <source> <mountpoint> <fstype> [mount options]
INGEST_MOUNTS="J: /mnt/j drvfs uid=1000,gid=1000,metadata,noatime"
# Run once after any mount succeeds. Adjust the path and the compose files.
ON_MOUNT_CMD="cd /home/julien/dev/openshorts && docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.ingest.yml up -d backend"
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now openshorts-ingest-mount.timer
```

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

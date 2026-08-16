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
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now openshorts-ingest-mount.timer
```

Check it:

```bash
systemctl list-timers openshorts-ingest-mount.timer
journalctl -u openshorts-ingest-mount.service -n 20
```

## What still needs a container restart

A Docker bind mount is resolved when the container starts, and Docker's mounts
are private, so a drive mounted *after* the container came up does not appear
inside it. With `create_host_path: false` in `docker-compose.ingest.yml` and
`restart: unless-stopped`, that resolves itself: the backend refuses to start
while the mount is missing and comes up on a later retry, re-resolving the bind.

If your setup does not restart the container on its own, add the recreate to the
script:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml \
               -f docker-compose.ingest.yml up -d --force-recreate backend
```

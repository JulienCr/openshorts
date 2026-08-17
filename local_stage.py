"""Copy a source off a slow filesystem before the pipeline reads it a dozen times.

`local_path` ingest reads the source where it lies, which is right when it lies on
the server's own disk and wrong when it lies on a Google Drive exposed as a Windows
drive and reached from WSL over 9p. The pipeline is not a single sequential read:
ffprobe, the audio extraction for Whisper, the scene detector decoding the whole
file, the layout picker's twelve seeks, then one decode per clip at extraction and
another at reframe. Every one of those passes goes back over the wire. One
sequential copy replaces all of them.

The cache is keyed on (realpath, size, mtime_ns) and kept for LOCAL_STAGE_TTL_SECONDS
after its *last use*, so re-running the same source — or resuming it after a
redeploy — costs nothing.

Deliberately stdlib-only and importing nothing from app.py: the CI installs neither
FastAPI nor uvicorn, and this is the part worth testing.
"""

import errno
import hashlib
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# Filesystems fast enough that copying would only waste a pass. An ALLOWLIST, not a
# blocklist of slow ones: an fstype nobody thought of costs one needless copy here,
# where the reverse costs exactly the problem this module exists to fix.
FAST_FSTYPES = frozenset({
    "ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs",
    "overlay", "tmpfs", "ramfs",
})

# auto  → copy when the source's filesystem is not in FAST_FSTYPES
# always→ copy every local_path source
# never → never copy (the behaviour before this module existed)
MODE = os.environ.get("LOCAL_STAGE", "auto").strip().lower()
STAGE_DIR = os.environ.get("LOCAL_STAGE_DIR", "stage")
TTL_SECONDS = int(os.environ.get("LOCAL_STAGE_TTL_SECONDS", str(12 * 3600)))
MAX_GB = float(os.environ.get("LOCAL_STAGE_MAX_GB", "50"))
MIN_FREE_GB = float(os.environ.get("LOCAL_STAGE_MIN_FREE_GB", "10"))

# Copies run one at a time, which buys two things for zero locking: two jobs on the
# same source never copy it twice (the second one runs after the first and finds a
# complete file), and two concurrent reads of the same 9p link are slower than
# doing them back to back anyway.
STAGE_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stage")

_COPY_CHUNK = 8 * 1024 * 1024
_PART_SUFFIX = ".part"

# key -> number of jobs currently using it. Never persisted: a restart kills every
# job, so after one nothing is in use by definition.
_refs = {}
_refs_lock = threading.Lock()


# --- filesystem detection -----------------------------------------------------

def _unescape_mountpoint(raw):
    """Undo the octal escapes the kernel writes for space, tab, newline, backslash.

    Not cosmetic: REPLAY_DIR on this very deployment is
    "/mnt/j/Drive partagés/..." and mountinfo spells that space "\\040". Without
    this the prefix match silently fails, and a failed match reads as "fast" —
    the copy would just never happen, with nothing in the logs to say why.

    Unescaped at the BYTE level, then decoded: an escape is one byte, so
    chr(0o303) + chr(0o251) would hand back "Ã©" where the two bytes together
    spell "é". surrogateescape on both ends carries a path that is not valid
    UTF-8 through unharmed, which is also how os.path represents one.
    """
    data = re.sub(rb"\\([0-7]{3})", lambda m: bytes([int(m.group(1), 8)]),
                  raw.encode("utf-8", "surrogateescape"))
    return data.decode("utf-8", "surrogateescape")


def _mounts():
    """[(mountpoint, fstype)] from /proc/self/mountinfo, longest mountpoint first."""
    try:
        with open("/proc/self/mountinfo", encoding="utf-8",
                  errors="surrogateescape") as f:
            return parse_mountinfo(f.read().splitlines())
    except OSError:
        return []


def parse_mountinfo(lines):
    """Split out from _mounts so the format's traps can be tested without /proc."""
    out = []
    for line in lines:
        # The optional fields before " - " are variable in number (shared:1,
        # master:2, ...), so the fstype cannot be addressed by index — it is the
        # first field after the separator.
        left, sep, right = line.partition(" - ")
        if not sep:
            continue
        left_fields, right_fields = left.split(), right.split()
        if len(left_fields) < 5 or not right_fields:
            continue
        out.append((_unescape_mountpoint(left_fields[4]), right_fields[0]))
    out.sort(key=lambda mp_fs: len(mp_fs[0]), reverse=True)
    return out


def fstype_for(path):
    """Filesystem type of the mount `path` lives on, or "" if undeterminable.

    A Docker bind mount reports the *underlying* superblock here, not "bind", which
    is what makes this work inside the container: a host directory on 9p shows up
    as 9p on /ingest/replays. (Corollary worth knowing: when the host path does not
    exist, Docker creates it on the host root and the mount then honestly reports
    ext4 — an empty ingest folder, not a detection bug.)
    """
    try:
        target = os.path.realpath(path)
    except OSError:
        return ""
    for mountpoint, fstype in _mounts():
        stripped = mountpoint.rstrip("/")
        # Compare on path components: a raw startswith would match /ingestion
        # against the /ingest mount.
        if target == mountpoint or target.startswith(stripped + "/"):
            return fstype
    return ""


# Errnos meaning "the mount is still in the table and the thing under it is
# gone", as opposed to "there is nothing here". Measured on WSL: a Google Drive
# client restart takes the 9p session down, leaves the mountpoint listed in
# /proc/mounts, and every syscall against it answers ENODEV. FUSE reaches the
# same state through ENOTCONN and NFS through ESTALE.
DEAD_MOUNT_ERRNOS = frozenset({
    errno.ENODEV, errno.ENXIO, errno.ENOTCONN, errno.ESTALE,
    errno.EIO, errno.EREMOTEIO, errno.EHOSTDOWN,
})

# Errnos meaning "there is nothing at this path". An ALLOWLIST, and narrow on
# purpose — this is the only verdict that lets a caller drop the source, so
# anything not listed here is reported instead of silently discarded. An errno
# nobody thought of then costs one puzzling line in the picker; the reverse
# costs a configured source vanishing, which is the bug this module exists to
# stop. EACCES in particular is ordinary: the backend runs as a non-root
# appuser against bind-mounted host folders, and os.path.isdir() answers True
# there — stat only needs to traverse the parent — so such a source has always
# reached the picker.
ABSENT_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR})


def dir_state(path):
    """("ok" | "dead" | "unreadable" | "absent", entry count) for a source dir.

    One listdir answers all four, and that is the point: os.path.isdir()
    swallows the OSError and returns False, so a mount whose transport died is
    indistinguishable from a path that was never configured. A caller that
    filters on isdir() therefore drops a broken source from the picker
    *entirely* — which reads as "this source does not exist here", strictly
    worse than the empty folder the isdir() guard was written to explain.

    The count is every entry, not just the videos: a source folder with
    literally nothing in it is almost always a mount that did not happen.
    """
    try:
        return "ok", len(os.listdir(path))
    except OSError as exc:
        if exc.errno in DEAD_MOUNT_ERRNOS:
            return "dead", 0
        if exc.errno in ABSENT_ERRNOS:
            return "absent", 0
        return "unreadable", 0


def is_slow(path):
    """Whether reading `path` in place is slow enough to be worth copying first."""
    if MODE == "never":
        return False
    if MODE == "always":
        return True
    # Copying from a slow disk onto a slow disk gains nothing — it pays the
    # traversal twice. Happens for real when the repo itself sits on /mnt/d.
    if fstype_for(_stage_root()) not in FAST_FSTYPES:
        return False
    # "" (no /proc, unreadable) lands here and counts as slow, matching the
    # allowlist's bias.
    return fstype_for(path) not in FAST_FSTYPES


# --- cache --------------------------------------------------------------------

def _stage_root():
    return os.path.abspath(STAGE_DIR)


def _key_for(source):
    """Cache key from identity + content stamp, so an edited source never hits."""
    st = os.stat(source)
    stamp = f"{os.path.realpath(source)}|{st.st_size}|{st.st_mtime_ns}"
    return hashlib.sha256(stamp.encode("utf-8", "surrogateescape")).hexdigest()[:16]


def _key_dir(key):
    return os.path.join(_stage_root(), key)


def staged_path_for(source):
    """The staged copy of `source` if one is on disk right now, else None."""
    try:
        key = _key_for(source)
    except OSError:
        return None
    path = os.path.join(_key_dir(key), os.path.basename(source))
    return path if os.path.isfile(path) else None


def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _entries():
    """[(mtime, key, size)] for every complete cache entry, oldest first."""
    root = _stage_root()
    out = []
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for name in names:
        if name.endswith(_PART_SUFFIX):
            continue
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        try:
            out.append((os.path.getmtime(path), name, _dir_size(path)))
        except OSError:
            pass
    out.sort()
    return out


def _in_use(key):
    with _refs_lock:
        return _refs.get(key, 0) > 0


def _evict(need_bytes=0, log=print):
    """Drop unreferenced entries, oldest first, until `need_bytes` would fit."""
    root = _stage_root()
    for _mtime, key, size in _entries():
        if need_bytes <= 0:
            return
        if _in_use(key):
            # A job is reading this one right now. Under Linux the unlink would
            # not break its open fd, but it would break the *next* pass of the
            # same pipeline, which reopens the file.
            continue
        shutil.rmtree(os.path.join(root, key), ignore_errors=True)
        need_bytes -= size
        log(f"🧹 Staging: évincé {key} ({_human(size)})")


def _room_for(size, log):
    """Make `size` bytes fit under both the disk's free space and MAX_GB."""
    root = _stage_root()
    used = sum(e[2] for e in _entries())
    over_cap = used + size - MAX_GB * 1024 ** 3
    if over_cap > 0:
        _evict(over_cap, log)

    try:
        free = shutil.disk_usage(root).free
    except OSError:
        return True  # unknowable → try the copy and let it fail honestly
    short = size + MIN_FREE_GB * 1024 ** 3 - free
    if short > 0:
        _evict(short, log)
        try:
            free = shutil.disk_usage(root).free
        except OSError:
            return True
    return free >= size + MIN_FREE_GB * 1024 ** 3


def _human(nbytes):
    """Sizes as the reader thinks of them — "0.0 Go" for a 3 MB file is noise."""
    if nbytes >= 1024 ** 3:
        return f"{nbytes / 1024 ** 3:.1f} Go"
    return f"{nbytes / 1024 ** 2:.0f} Mo"


def _copy(source, dest, size, log):
    """Copy with a progress line every 10%, quiet for anything under a gigabyte."""
    chatty = size >= 1024 ** 3
    started = time.monotonic()
    done = next_mark = 0
    with open(source, "rb") as src, open(dest, "wb") as dst:
        while True:
            chunk = src.read(_COPY_CHUNK)
            if not chunk:
                break
            dst.write(chunk)
            done += len(chunk)
            if chatty and next_mark < 9 and done * 10 // size > next_mark:
                next_mark = done * 10 // size
                rate = done / max(time.monotonic() - started, 1e-6) / 1024 ** 2
                log(f"📁 Copie… {next_mark * 10} %  "
                    f"({_human(done)}/{_human(size)}, {rate:.0f} Mo/s)")
    elapsed = max(time.monotonic() - started, 1e-6)
    log(f"📁 Copie terminée en {elapsed:.0f} s "
        f"({size / elapsed / 1024 ** 2:.0f} Mo/s).")


def acquire(source, on_log=None):
    """Return the path the pipeline should read — the staged copy, or `source`.

    Blocking; meant to be run in STAGE_POOL. Falls back to `source` on any
    failure: staging is a speed-up, and a full disk must degrade throughput, never
    fail a job.
    """
    log = on_log or (lambda msg: None)
    try:
        st = os.stat(source)
        key = _key_for(source)
    except OSError as e:
        log(f"⚠️ Staging impossible ({e}) — lecture en place.")
        return source

    root = _stage_root()
    key_dir = _key_dir(key)
    final = os.path.join(key_dir, os.path.basename(source))

    # Reference the key BEFORE any work: this is what stops a concurrent sweep
    # from rmtree-ing the directory out from under the .part being written. Taking
    # the reference afterwards would leave exactly that window open.
    with _refs_lock:
        _refs[key] = _refs.get(key, 0) + 1

    try:
        if os.path.isfile(final) and os.path.getsize(final) == st.st_size:
            os.utime(key_dir, None)  # last-use clock, read by both TTL and LRU
            return final

        os.makedirs(root, exist_ok=True)
        if not _room_for(st.st_size, log):
            log(f"⚠️ Pas assez d'espace pour stager "
                f"({_human(st.st_size)}) — lecture en place.")
            release_key(key)
            return source

        log(f"📁 Source sur un système de fichiers {fstype_for(source) or 'inconnu'} "
            f"— copie locale d'abord ({_human(st.st_size)}).")

        # Stage as <key>/<original basename>, never <key>.mp4: main.py derives the
        # project title from the input filename, so a hashed name would rename
        # every project in the library to gibberish. The directory also makes
        # eviction a single atomic rmtree.
        part_dir = key_dir + _PART_SUFFIX
        shutil.rmtree(part_dir, ignore_errors=True)
        os.makedirs(part_dir, exist_ok=True)
        try:
            _copy(source, os.path.join(part_dir, os.path.basename(source)),
                  st.st_size, log)
            if not os.path.isdir(part_dir):
                # Only reachable if something outside this module deleted the
                # cache while the copy was running — clearing stage/ by hand on a
                # live server does exactly this, and an open fd keeps the copy
                # writing happily to an unlinked inode until the rename. Say so,
                # because the bare ENOENT on the rename reads like a code bug.
                raise OSError("staging directory was removed while copying "
                              "(was stage/ cleared by hand?)")
            # Atomic promotion: an interrupted copy can never leave a truncated
            # file that a later run would mistake for a cache hit.
            os.rename(part_dir, key_dir)
        except BaseException:
            shutil.rmtree(part_dir, ignore_errors=True)
            raise
        return final
    except Exception as e:
        log(f"⚠️ Copie locale échouée ({e}) — lecture en place.")
        release_key(key)
        return source


def release(path):
    """Drop the reference taken by acquire() for a staged path."""
    if not path:
        return
    key = os.path.basename(os.path.dirname(os.path.abspath(path)))
    release_key(key)


def release_key(key):
    with _refs_lock:
        left = _refs.get(key, 0) - 1
        if left > 0:
            _refs[key] = left
            return
        _refs.pop(key, None)

    # The clock restarts when the last job lets go, not when the copy was made.
    # Without this the TTL is "12h since the copy", which is a different promise
    # and breaks in the one case that matters: a render longer than the TTL is
    # correctly kept while it runs, then expires on the very next sweep, so the
    # re-run it was cached for pays the full slow-filesystem copy again.
    try:
        os.utime(_key_dir(key), None)
    except OSError:
        pass  # never staged, or already evicted — nothing to date


def sweep(now=None, log=print):
    """Expire entries unused for TTL_SECONDS, then trim to MAX_GB, LRU first."""
    # The guard lives here rather than at the call site so there is one place to
    # get it right. The uploads sweep in app.py makes the opposite choice and got
    # it wrong: `now - mtime > 0` is true for every file, so a retention of zero —
    # which reads as "disabled" — deletes everything on the next pass.
    if TTL_SECONDS <= 0:
        return
    root = _stage_root()
    if not os.path.isdir(root):
        return
    now = time.time() if now is None else now

    for mtime, key, size in _entries():
        if now - mtime <= TTL_SECONDS:
            continue
        if _in_use(key):
            # Past its TTL but still being read: a long render on a reused source
            # outlives 12 h. Age never outranks use.
            continue
        shutil.rmtree(os.path.join(root, key), ignore_errors=True)
        log(f"🧹 Staging: expiré {key} ({_human(size)})")

    used = sum(e[2] for e in _entries())
    over = used - MAX_GB * 1024 ** 3
    if over > 0:
        _evict(over, log)

    # A .part left behind by a killed process is dead weight nothing will claim.
    for name in os.listdir(root):
        if not name.endswith(_PART_SUFFIX):
            continue
        path = os.path.join(root, name)
        try:
            if now - os.path.getmtime(path) > TTL_SECONDS:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass

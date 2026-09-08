"""JSON file persistence — swappable for a real DB (SQLite) later.

Zero-budget storage that must nevertheless survive THREE processes (reward bot,
partnership bot, admin bot) reading and writing the SAME files on the same box.
Two properties matter:

  1) ATOMIC WRITES — write to a temp file in the same directory and `os.replace`
     it over the target. A crash/power-cut can never leave a half-written (and
     therefore unparsable) ledger behind.
  2) NO LOST UPDATES — before writing, re-read what is on disk and merge: keys
     this process changed win, keys another process added/changed since our last
     read are preserved. Without this, the admin bot approving a review item
     would silently clobber everything the reward bot wrote in the meantime.

A cross-process lock (fcntl, POSIX) serialises the read-merge-write cycle so two
bots can't interleave inside it. On platforms without fcntl the lock degrades to
a no-op and the merge alone still prevents whole-file clobbering.
"""
from __future__ import annotations

import json
import os
import tempfile

try:                                    # POSIX only; Windows degrades gracefully
    import fcntl
    _HAS_FCNTL = True
except ImportError:                     # pragma: no cover - non-POSIX fallback
    _HAS_FCNTL = False

try:                                    # Windows file locking
    import msvcrt
    _HAS_MSVCRT = True
except ImportError:                     # pragma: no cover - non-Windows
    _HAS_MSVCRT = False


class _FileLock:
    """Best-effort advisory lock around the read-merge-write cycle."""

    def __init__(self, path: str):
        self.path = path + ".lock"
        self._fh = None

    def __enter__(self):
        if not _HAS_FCNTL and not _HAS_MSVCRT:
            return self
        try:
            self._fh = open(self.path, "a+b")
            if _HAS_FCNTL:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
            else:
                # msvcrt locks byte ranges and requires a byte to exist.
                self._fh.seek(0, os.SEEK_END)
                if self._fh.tell() == 0:
                    self._fh.write(b"0")
                    self._fh.flush()
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
        except OSError:                 # read-only dir etc. — carry on unlocked
            if self._fh is not None:
                self._fh.close()
            self._fh = None
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            try:
                if _HAS_FCNTL:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                elif _HAS_MSVCRT:
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                self._fh.close()
                self._fh = None
        return False


class JsonStore:
    def __init__(self, path):
        self.path = path
        self._data = {}
        self._dirty = set()             # keys this process changed since last sync
        self._load()

    # ------------------------------------------------------------------ read
    def _read_disk(self) -> dict:
        if not os.path.exists(self.path) or os.path.getsize(self.path) == 0:
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _load(self):
        self._data = self._read_disk()

    def reload(self):
        """Re-read from disk, keeping any not-yet-synced local changes."""
        disk = self._read_disk()
        for k in self._dirty:
            if k in self._data:
                disk[k] = self._data[k]
        self._data = disk
        return self._data

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def __contains__(self, key):
        return key in self._data

    def __setitem__(self, key, val):
        self._data[key] = val
        self._dirty.add(key)

    def mark_dirty(self, key):
        """Declare that `key`'s value was mutated in place, so the next sync
        keeps OUR version of it instead of the copy currently on disk."""
        if key in self._data:
            self._dirty.add(key)

    def __getitem__(self, key):
        return self._data[key]

    # ----------------------------------------------------------------- write
    def sync(self):
        """Merge with whatever is on disk and write atomically."""
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        with _FileLock(self.path):
            merged = self._read_disk()
            # everything we hold wins for keys we touched; untouched keys written
            # by another process survive.
            for k, v in self._data.items():
                if k in self._dirty or k not in merged:
                    merged[k] = v
            self._write_atomic(merged, directory)
            self._data = merged
            self._dirty.clear()

    def _write_atomic(self, payload: dict, directory: str):
        fd, tmp = tempfile.mkstemp(prefix=".clickmint-", suffix=".json",
                                   dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

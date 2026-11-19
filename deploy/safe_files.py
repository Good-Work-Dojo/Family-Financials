# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Reads and writes that never go through a link, for the files two containers share (the refresh mailbox: the
request, the status view, the ingest marker, the run's lock; the family documents) and the ones a worker keeps.

A container that can write a shared directory can put a link, a FIFO or a folder at any name in it, and a link
resolves inside the container that follows it, where it can name the feed credential or the snapshot store. So:

  read_regular(path)        the bytes of the regular file at `path` itself: opened with O_NOFOLLOW and O_NONBLOCK
                            (nothing waits on a FIFO), checked on the descriptor to be a regular file of at most
                            MAX_BYTES (`limit`); anything else is an OSError whose strerror is NOT_REGULAR or
                            TOO_LARGE, and nothing there FileNotFoundError. Callers read both as "missing or
                            unreadable".
  write_atomic(path, data)  a new file beside `path` (mkstemp: created with O_EXCL, never through a link), its bytes
                            fsynced, renamed over `path` (a link there is replaced, never followed), the directory
                            fsynced; its mode is what open(path, "w") gives, or `mode` when given (set before a byte
                            is written, so a private file is never readable by others, even for a moment).
  open_lock(path)           a lock file's descriptor, created when absent (`create`, with `mode`, 0644 unless given;
                            the umask applies), never through a link and never truncated.
  touch(path, stamp)        the file at `path`, made when absent, given the times `stamp`, never through a link.
  regular_mtime(path)       the mtime of the regular file at `path` itself, else None.

Stdlib only and importing nothing of the project: the store and the refresh worker import it by name (deploy/ is on
their path), the ETL loads it by its path (etl/files.py)."""
from __future__ import annotations

import contextlib
import errno
import os
import stat
import tempfile
from pathlib import Path

MAX_BYTES = 16 * 1024 * 1024
NOT_REGULAR = "not a regular file"
TOO_LARGE = "larger than the most that is read"

# never through a link, never waiting on a FIFO, never kept by a child process, never a controlling terminal
_SAFE = os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOCTTY
# what an open of something other than a regular file answers: a link (O_NOFOLLOW), a folder opened to write, a FIFO
# opened to write with no reader
_NOT_REGULAR_ERRNOS = (errno.ELOOP, errno.EISDIR, errno.ENXIO)


def _refused(path, why: str) -> OSError:
    return OSError(errno.EINVAL, why, str(path))


def _open_regular(path, flags: int, mode: int = 0o666) -> int:
    """A descriptor of the regular file at `path` itself, else NOT_REGULAR (the descriptor closed)."""
    try:
        fd = os.open(path, flags | _SAFE, mode)
    except OSError as e:
        if e.errno in _NOT_REGULAR_ERRNOS:
            raise _refused(path, NOT_REGULAR) from None
        raise
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise _refused(path, NOT_REGULAR)
    return fd


def read_regular(path, *, limit: int | None = None) -> bytes:
    cap = MAX_BYTES if limit is None else limit
    with os.fdopen(_open_regular(path, os.O_RDONLY), "rb") as f:
        if os.fstat(f.fileno()).st_size > cap:
            raise _refused(path, TOO_LARGE)
        data = f.read(cap + 1)                      # the file may have grown since its size was read
    if len(data) > cap:
        raise _refused(path, TOO_LARGE)
    return data


def _umask() -> int:
    """The process's umask, read without changing it (os.umask would, for every thread at once)."""
    with contextlib.suppress(OSError, ValueError):
        with open("/proc/self/status", encoding="ascii", errors="replace") as f:
            for line in f:
                if line.startswith("Umask:"):
                    return int(line.split()[1], 8)
    return 0o022


def write_atomic(path, data: bytes, *, mode: int | None = None) -> None:
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), (0o666 & ~_umask()) if mode is None else mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    with contextlib.suppress(OSError):              # not every filesystem or mount allows it
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def open_lock(path, *, create: bool = True, mode: int = 0o644) -> int:
    return _open_regular(path, (os.O_RDWR | os.O_CREAT) if create else os.O_RDONLY, mode)


def touch(path, stamp: float) -> None:
    fd = _open_regular(path, os.O_WRONLY | os.O_CREAT)
    try:
        os.utime(fd, (stamp, stamp))
    finally:
        os.close(fd)


def regular_mtime(path) -> float | None:
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return info.st_mtime if stat.S_ISREG(info.st_mode) else None

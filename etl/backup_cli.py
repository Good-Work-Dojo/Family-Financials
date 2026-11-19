# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""`python -m etl backup [--include-secrets]` and `python -m etl restore FILE [--force]` — the household's own files
in one archive, and back.

`backup` writes `backups/ff-<UTC stamp>.tar.gz`, mode 0600 from its creation: config/, state/family/ (the family's
documents), state/engine/ (the learned rules and the refresh's own state, without its log, locks and heartbeat),
store/ (the bank feed's snapshots: history no feed gives again) and, only with --include-secrets, secrets/. Never
state/auth/ (sessions: everyone signs in again), state/caddy/ (the web server remakes it), state/refresh/ (the
mailbox) or data/ (an ingest rebuilds it). Only regular files are archived: a link or anything else in the tree is
left out and named, and so is, without --include-secrets, a file under config/ that looks like a secret put in the
wrong place (an answers file, or one only its owner may read). A manifest, ff-backup.json, says what is inside and
which release wrote it.

`restore FILE` (a file inside the install directory) refuses when config/ or store/ already holds anything, unless
--force (then the archive's files replace the ones of the same name; nothing else is removed). The whole archive is
checked BEFORE anything is written: each member a regular file or a directory, under one of the places a backup holds,
with no absolute path, no `..`, no control character, no link, hard link or device; the member count and total size
bounded; no name twice, no file where one of the install's folders belongs or that another member needs as its
folder; and, on disk, no link anywhere on a member's path and nothing of the other kind where it goes; and what it
unpacks to must leave 512 MiB free. One problem stops it, and nothing is extracted. Members are copied in chunks;
directories take the install table's modes (0700, and 0700 for one the table does not list) and files 0644 under
config/ (as init writes them), 0600 elsewhere, never the archive's. Then: "Now run ./ff up" (an ingest rebuilds data/).

Exit 0 when done, 2 on a refusal (one line)."""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import clock, install
from .install import Refused

MANIFEST = "ff-backup.json"
PLACES = ("config", "state/family", "state/engine", "store")
SECRET_PLACES = ("secrets/web", "secrets/feed", "secrets/ai", "secrets/alerts")
# what state/engine holds that a backup leaves out: the log, the locks, the heartbeat and half-written files
_LEFT_OUT = re.compile(r"(fetch\.log(\.\d+)?|.*\.lock|heartbeat|\..*\.tmp|.*\.tmp)\Z")
# a restore's bounds: an archive past them is refused before anything is written
MAX_MEMBERS = 200_000
MAX_TOTAL_BYTES = 8 * 1024 ** 3
MAX_MEMBER_BYTES = 1024 ** 3
# the free space a restore leaves under the install at least, counting what the archive says it unpacks to
MIN_FREE_BYTES = 512 * 1024 ** 2


def _stamp(now: dt.datetime) -> str:
    return now.astimezone(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def _regular(path: Path):
    """The file opened for reading without following a link, or None when it is no regular file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    f = os.fdopen(fd, "rb")
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        f.close()
        return None
    return f


def backup(*, include_secrets: bool = False, out: Callable[[str], None] = print,
           now: dt.datetime | None = None) -> Path:
    root = install.home()
    install.refuse_root()
    now = now or clock.wall_utc()
    wanted = (*PLACES, *(SECRET_PLACES if include_secrets else ()))
    for p in wanted:
        if (root / p).is_symlink():
            out(f"left out {p}: a link, not the install's own folder")
    places = [p for p in wanted if (root / p).is_dir() and not (root / p).is_symlink()]
    folder = root / "backups"
    if not folder.is_dir():
        os.mkdir(folder, 0o700)
    target = folder / f"ff-{_stamp(now)}.tar.gz"
    if target.exists():
        raise Refused(f"{install.shown(target, root)} exists already: wait a second and run backup again")
    tmp = folder / f".{target.name}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    left_out: list[tuple[str, str]] = []
    files = 0
    try:
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
            manifest = {"format": 1, "app_version": install.VERSION, "created": now.astimezone(dt.UTC).isoformat(),
                        "included": places, "secrets": include_secrets}
            data = (json.dumps(manifest, indent=2) + "\n").encode()
            info = tarfile.TarInfo(MANIFEST)
            info.size, info.mode, info.mtime = len(data), 0o600, int(now.timestamp())
            tar.addfile(info, io.BytesIO(data))
            for place in places:
                for dirpath, dirnames, filenames in os.walk(root / place, followlinks=False):
                    rel_dir = Path(dirpath).relative_to(root).as_posix()
                    tar.addfile(_dir_info(rel_dir, now))
                    for name in sorted(filenames) + sorted(d for d in dirnames if (Path(dirpath) / d).is_symlink()):
                        rel = f"{rel_dir}/{name}"
                        if place == "state/engine" and _LEFT_OUT.match(name):
                            continue
                        f = _regular(Path(dirpath) / name)
                        if f is None:
                            left_out.append((rel, "not a regular file"))
                            continue
                        if place == "config" and not include_secrets and _looks_secret(name, f):
                            f.close()
                            left_out.append((rel, "it looks like a secret (an answers file, or readable by you "
                                                  "alone); --include-secrets keeps it"))
                            continue
                        with f:
                            info = tar.gettarinfo(arcname=rel, fileobj=f)
                            info.uid = info.gid = 0
                            info.uname = info.gname = ""
                            tar.addfile(info, f)
                            files += 1
                    dirnames.sort()
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    for rel, why in left_out:
        out(f"left out {rel}: {why}")
    out(f"backed up {files} file{'s' if files != 1 else ''} of {', '.join(places)} to "
        f"{install.shown(target, root)} (mode 600)")
    if include_secrets:
        out("it holds the credentials too: keep it as safe as the install directory")
    return target


def _looks_secret(name: str, f) -> bool:
    """A file under config/ that is a secret put in the wrong place: an answers file (it may hold a passcode and a
    setup token) or one only its owner may read."""
    return "answers" in name.casefold() or not stat.S_IMODE(os.fstat(f.fileno()).st_mode) & 0o077


def _dir_info(name: str, now: dt.datetime) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o700, int(now.timestamp())
    return info


# ------------------------------------------------------------------ restore

ALLOWED = (*PLACES, *SECRET_PLACES)


def _member_problem(m: tarfile.TarInfo) -> str | None:
    """Why the archive member cannot be restored, judged alone, or None."""
    name = m.name
    if not name or name.startswith("/") or "\\" in name or "\0" in name:
        return f"{name!r} is not a plain relative path"
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in name):
        return f"{name!r} holds a control character, which a backup's names never do"
    parts = PurePosixPath(name).parts
    if any(p in ("..", ".", "") for p in parts) or name != "/".join(parts):
        return f"{name!r} is not a plain relative path"
    if not (m.isreg() or m.isdir()):
        return f"{name!r} is a link or a device, which a backup never holds"
    if m.isreg() and m.size > MAX_MEMBER_BYTES:
        return f"{name!r} is larger than a backup's file can be"
    if name == MANIFEST:
        return None if m.isreg() else f"{name!r} is not the manifest file"
    if m.isdir() and name in ("state", "secrets"):
        return None
    if not any(name == p or name.startswith(p + "/") for p in ALLOWED):
        return f"{name!r} is outside what a backup holds ({', '.join(ALLOWED)})"
    return None


@dataclass(frozen=True)
class Entry:
    """What one member of a checked archive will be: its path under the install, a directory or a file, its size."""
    name: str
    folder: bool
    size: int


# the install's own folders a backup's members live in (and their parents): never a file
_FOLDERS = frozenset({"state", "secrets", *ALLOWED})


def _whole_problem(entries: list[Entry]) -> str | None:
    """What makes the members together unrestorable, or None: a name given twice, a file where one of the install's
    folders belongs, or a name that is both a file and the folder of another member."""
    seen: set[str] = set()
    parents: set[str] = set()
    for e in entries:
        if e.name in seen:
            return f"{e.name!r} is in it twice"
        seen.add(e.name)
        parts = PurePosixPath(e.name).parts
        parents.update("/".join(parts[:i]) for i in range(1, len(parts)))
    for e in entries:
        if not e.folder and (e.name in _FOLDERS or e.name in parents):
            return f"{e.name!r} is a file where a folder belongs"
    return None


def _disk_problem(root: Path, entries: list[Entry]) -> str | None:
    """What on disk stands in the way of a member, or None: a link anywhere on its path, a file where a folder must
    be, or a folder where a file goes."""
    looked: dict[Path, int | None] = {}

    def mode(path: Path) -> int | None:
        if path not in looked:
            try:
                looked[path] = os.lstat(path).st_mode
            except FileNotFoundError:
                looked[path] = None
        return looked[path]

    for e in entries:
        parts = PurePosixPath(e.name).parts
        for i in range(1, len(parts) + 1):
            path = root.joinpath(*parts[:i])
            m = mode(path)
            if m is None:
                break
            shown = install.shown(path, root)
            if stat.S_ISLNK(m):
                return f"{shown} is a link, which a restore never writes through: move it aside"
            last = i == len(parts)
            if (not last or e.folder) and not stat.S_ISDIR(m):
                return f"{shown} is a file where the backup has a folder: move it aside"
            if last and not e.folder and not stat.S_ISREG(m):
                return f"{shown} is not a file, and the backup has one there: move it aside"
    return None


def _check(archive: Path) -> tuple[dict, list[Entry]]:
    """Every member checked alone, in one pass that stops at the first problem, then all of them together: the
    manifest and what the archive will write."""
    manifest = None
    entries: list[Entry] = []
    members = total = 0
    try:
        with tarfile.open(archive, mode="r:gz") as tar:
            while True:
                m = tar.next()
                if m is None:
                    break
                members += 1
                total += max(m.size, 0)
                if members > MAX_MEMBERS:
                    raise Refused(f"the archive holds more than {MAX_MEMBERS} members: it is not a backup this "
                                  "release wrote, and nothing was restored")
                if total > MAX_TOTAL_BYTES:
                    raise Refused("the archive unpacks to more than a backup can: nothing was restored")
                problem = _member_problem(m)
                if problem:
                    raise Refused(f"the archive cannot be restored: {problem}; nothing was restored")
                if m.name == MANIFEST:
                    if m.size > 64 * 1024:
                        raise Refused("the archive's manifest is too large: nothing was restored")
                    try:
                        manifest = json.loads(tar.extractfile(m).read())
                    except (ValueError, AttributeError):
                        raise Refused("the archive's manifest cannot be read: nothing was restored") from None
                else:
                    entries.append(Entry(m.name, m.isdir(), max(m.size, 0) if m.isreg() else 0))
    except (tarfile.TarError, OSError, EOFError) as e:
        raise Refused(f"the archive cannot be read ({e.__class__.__name__}): nothing was restored") from None
    if not isinstance(manifest, dict):
        raise Refused("the archive holds no backup manifest: it is not a backup ./ff backup wrote; nothing was "
                      "restored")
    problem = _whole_problem(entries)
    if problem:
        raise Refused(f"the archive cannot be restored: {problem}; nothing was restored")
    return manifest, entries


def _place_dir(root: Path, rel: str) -> None:
    """Make the directory `rel` and its parents under `root`, each at the install table's mode (0700 when the table
    does not list it), never through a link."""
    path = root
    for part in PurePosixPath(rel).parts:
        path = path / part
        d = install.BY_PATH.get(path.relative_to(root).as_posix())
        mode = d.mode if d else 0o700
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            os.mkdir(path, mode)
            os.chmod(path, mode)
            continue
        if not stat.S_ISDIR(st.st_mode):
            raise Refused(f"{install.shown(path, root)} is in the way (not a directory): move it aside and restore "
                          "again")


def _public(rel: str) -> bool:
    return rel == "config" or rel.startswith("config/")


def restore(file: str, *, force: bool = False, out: Callable[[str], None] = print) -> int:
    root = install.home()
    install.ensure_dirs(root, out=out)
    archive = Path(file)
    if not archive.is_file():
        raise Refused(f"{file}: no such file (give a backup inside the install directory, such as backups/ff-….tar.gz)")
    for place in ("config", "store"):
        if any((root / place).iterdir()) and not force:
            raise Refused(f"{place}/ already holds files: restore with --force to replace them with the backup's")
    manifest, entries = _check(archive)
    # against what is on disk, before a byte is written
    problem = _disk_problem(root, entries)
    if problem:
        raise Refused(f"{problem}; nothing was restored")
    size = sum(e.size for e in entries)
    if shutil.disk_usage(root).free - size < MIN_FREE_BYTES:
        raise Refused(f"the backup unpacks to {size / 1024 ** 2:.0f} MiB, which would leave less than "
                      f"{MIN_FREE_BYTES // 1024 ** 2} MiB free under the install directory: free some space; nothing "
                      "was restored")
    restored = 0
    with tarfile.open(archive, mode="r:gz") as tar:
        for m in tar:
            if m.name == MANIFEST:
                continue
            if m.isdir():
                _place_dir(root, m.name)
                continue
            parent = PurePosixPath(m.name).parent.as_posix()
            _place_dir(root, parent)
            mode = 0o644 if _public(m.name) else 0o600
            src = tar.extractfile(m)
            install.write_stream(root / m.name, src if src else io.BytesIO(), mode)
            restored += 1
    out(f"restored {restored} file{'s' if restored != 1 else ''} of {', '.join(manifest.get('included') or [])} from "
        f"a backup of {manifest.get('created', 'an unknown day')} (release {manifest.get('app_version', '?')})")
    out("Now run ./ff up: the first refresh rebuilds the dashboard's data")
    return 0


def run(command: str, *, file: str | None = None, include_secrets: bool = False, force: bool = False) -> int:
    try:
        if command == "backup":
            backup(include_secrets=include_secrets)
            return 0
        return restore(file, force=force)
    except Refused as e:
        print(f"{command}: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"{command}: {e.filename or ''} {e.strerror or e}".strip(), file=sys.stderr)
        return 2

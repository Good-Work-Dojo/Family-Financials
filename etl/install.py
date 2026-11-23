# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The install root (FF_HOME) as the `ff` commands share it: its directories, each with its mode and what it holds, made
as the invoking user before anything else runs; the settings file (.env) read and written as `KEY=value` lines; the
two files an install directory takes from the release (compose.yaml and the settings' template); and the words every
command uses for a place.

Every directory compose mounts must exist, owned by the user the services run as, before the stack starts: a mount
never creates a missing one (Docker would make it root-owned). `ensure_dirs` makes each missing one with its mode,
tightens a private one that is open to others (one line each), and refuses one owned by another user, naming the
directory, its owner and the command that hands it back. Nothing here runs as root: uid 0 is refused.

`ff` passes FF_HOST_HOME, the install directory as the host names it, so a path in a message is one the person can
type (`shown`); without it the path is the container's own."""
from __future__ import annotations

import hashlib
import io
import os
import re
import secrets
import shutil
import stat
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

ROOT = Path(__file__).resolve().parent.parent
# the release's version, as pyproject.toml states it (tests pin it to pyproject.toml, app/package.json, the default
# images in compose.yaml and ff, and the images' version label)
VERSION = "1.0.1"
ENV_FILE = ".env"
ENV_PREVIOUS = ".env.previous"
COMPOSE_FILE = "compose.yaml"
# the settings' template: .env.example in a checkout, env.example in the runtime image (which holds no .env* file)
ENV_TEMPLATES = (ROOT / ".env.example", ROOT / "env.example")
DEFAULT_PORT = 8080
LOOPBACK = "127.0.0.1"
EVERYWHERE = "0.0.0.0"


@dataclass(frozen=True)
class Dir:
    """One directory of the install: its path under FF_HOME, its mode, and what it holds, for the words that name
    it. A wider mode found on disk is tightened to it."""
    path: str
    mode: int
    holds: str


# The install directory and every directory in it are the owner's alone: each holds the household's finances (its
# statements, its config, the database and the exports) or a secret, and every service runs as the owner's uid, so
# nothing needs a group or others to open one, whatever folder the install sits in.
ROOT_MODE = 0o700
DIRS: tuple[Dir, ...] = (
    Dir("config", 0o700, "the household's YAML"),
    Dir("secrets", 0o700, "the credentials"),
    Dir("secrets/web", 0o700, "the sign-in passcodes"),
    Dir("secrets/feed", 0o700, "the bank feed's access"),
    Dir("secrets/ai", 0o700, "the AI step's key"),
    Dir("secrets/alerts", 0o700, "the alerts' credentials"),
    Dir("imports", 0o700, "the statement files"),
    Dir("store", 0o700, "the bank feed's snapshots"),
    Dir("state", 0o700, "the app's state"),
    Dir("state/family", 0o700, "the family's documents"),
    Dir("state/auth", 0o700, "the sign-in sessions"),
    Dir("state/refresh", 0o700, "the refresh's mailbox"),
    Dir("state/engine", 0o700, "the refresh's own files"),
    Dir("state/caddy", 0o700, "the web server's own files"),
    Dir("data", 0o700, "the database and the dashboard's data"),
    Dir("data/exports", 0o700, "the dashboard's data"),
    Dir("backups", 0o700, "the backups"),
)
BY_PATH = {d.path: d for d in DIRS}


class Refused(Exception):
    """A command that cannot go on: the one line says what is wrong and what to do; nothing more was changed."""


# ------------------------------------------------------------------ who and where

def invoking_ids() -> tuple[int, int]:
    """The uid and gid the command runs as (the invoking user's: `ff` starts every container with them)."""
    return os.getuid(), os.getgid()


def refuse_root() -> None:
    if invoking_ids()[0] == 0:
        raise Refused("this runs as root, so everything it makes would be root's: run ff as the user who owns the "
                      "install directory")


def home() -> Path:
    """FF_HOME, the install root: `ff` sets it to /ff inside the container."""
    value = os.environ.get("FF_HOME", "")
    if not value:
        raise Refused("FF_HOME is not set: run this through ./ff, which sets it to the install directory")
    return Path(os.path.abspath(value))


def shown(path: Path | str, root: Path | None = None) -> str:
    """`path` as the person names it: under FF_HOST_HOME when `ff` passed it and the path lies in the install root."""
    path = Path(path)
    host = os.environ.get("FF_HOST_HOME", "")
    root = root or Path(os.path.abspath(os.environ.get("FF_HOME", "") or "/"))
    if host:
        try:
            rel = path.relative_to(root)
        except ValueError:
            return str(path)
        return str(Path(host) / rel) if str(rel) != "." else host
    return str(path)


def _lstat(path: Path) -> os.stat_result:
    """The one place a directory's owner and mode are read (a seam the tests use for another user's directory)."""
    return os.lstat(path)


def owner_problem(path: Path, st: os.stat_result, root: Path) -> str | None:
    """Why `path` cannot be used as the invoking user's, or None: owned by another uid."""
    uid, gid = invoking_ids()
    if st.st_uid == uid:
        return None
    return (f"{shown(path, root)} belongs to uid {st.st_uid}, not to you (uid {uid}): give it back with "
            f"sudo chown -R {uid}:{gid} {shown(path, root)}")


def ensure_dirs(root: Path, *, out: Callable[[str], None] = print) -> list[str]:
    """Make every missing directory of DIRS under `root` with its mode, as the invoking user; tighten the install
    directory (ROOT_MODE) and any of them a group or others can open, one line each. Refuses (Refused, before anything
    is made) uid 0, a root or a directory owned by another user, a link or a file where a directory belongs. Returns
    the paths made."""
    refuse_root()
    try:
        st = _lstat(root)
    except FileNotFoundError:
        raise Refused(f"the install directory {shown(root, root)} does not exist") from None
    if not stat.S_ISDIR(st.st_mode):
        raise Refused(f"the install directory {shown(root, root)} is not a directory")
    problem = owner_problem(root, st, root)
    if problem:
        raise Refused(problem)
    root_st = st
    found: dict[str, os.stat_result | None] = {}
    for d in DIRS:
        path = root / d.path
        try:
            st = _lstat(path)
        except FileNotFoundError:
            found[d.path] = None
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            what = "a link" if stat.S_ISLNK(st.st_mode) else "not a directory"
            raise Refused(f"{shown(path, root)} is {what}: it must be a directory of its own (it holds {d.holds}); "
                          "move it aside")
        problem = owner_problem(path, st, root)
        if problem:
            raise Refused(problem)
        found[d.path] = st
    made = []
    _tighten(root, root_st, ROOT_MODE, root, out)
    for d in DIRS:
        path = root / d.path
        st = found[d.path]
        if st is None:
            os.mkdir(path, d.mode)
            os.chmod(path, d.mode)          # the umask may have taken bits the table gives
            made.append(d.path)
        else:
            _tighten(path, st, d.mode, root, out)
    return made


def _tighten(path: Path, st: os.stat_result, mode: int, root: Path, out: Callable[[str], None]) -> None:
    """A directory with a bit beyond `mode` gets `mode`, said in one line."""
    if stat.S_IMODE(st.st_mode) & ~mode:
        os.chmod(path, mode)
        out(f"{shown(path, root)} was open to others ({stat.S_IMODE(st.st_mode):04o}): it is now {mode:04o}")


# ------------------------------------------------------------------ the settings file

# The settings file is read here exactly as compose reads it, or refused: one `KEY=value` per line (lines split on
# "\n" alone), the key upper case, nothing around the `=`, and a value with no character compose would read otherwise
# (a quote, `$`, a backtick, ` #`, a control character or a Unicode line separator) and no space at its end.
# Comments and blank lines are passed over; anything else is a line number and the fix.
_LINE = re.compile(r"([A-Z_][A-Z0-9_]*)=(.*)")
_UNSAFE = set("'\"$`")


def value_problem(value: str) -> str | None:
    """Why `value` cannot be a setting's value, read alike by compose and here; None when it can."""
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in value):
        return "it holds a control character or a line break"
    if _UNSAFE & set(value):
        return "it holds a quote, $ or a backtick, which compose would read otherwise"
    if " #" in value:
        return "it holds ' #', after which compose reads a comment"
    if value != value.rstrip(" "):
        return "it ends with a space"
    return None


def parse_env(text: str, name: str = ENV_FILE) -> dict[str, str]:
    """The `KEY=value` lines of a settings file; a key set twice takes its last value, as compose reads it. Refused
    (naming the line) when a line that is neither blank nor a comment is not one compose and this reader agree on."""
    out = {}
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip(" \t") or line.lstrip(" \t").startswith("#"):
            continue
        m = _LINE.fullmatch(line)
        if not m:
            raise Refused(f"{name} line {number} is not KEY=value (an upper-case key, nothing around the =): "
                          "correct it, or make it a comment with #")
        problem = value_problem(m.group(2))
        if problem:
            raise Refused(f"{name} line {number}: the value of {m.group(1)} {problem}: correct it")
        out[m.group(1)] = m.group(2)
    return out


def read_env(root: Path, name: str = ENV_FILE) -> dict[str, str] | None:
    """The settings file's values, or None when there is none (Refused when a line is not one compose reads alike)."""
    try:
        # bytes decoded as they are: a text-mode read would turn a carriage return into a line break compose never sees
        return parse_env((root / name).read_bytes().decode("utf-8"), name)
    except FileNotFoundError:
        return None
    except UnicodeDecodeError:
        raise Refused(f"{name} is not UTF-8 text: correct it") from None


def env_template() -> str:
    for path in ENV_TEMPLATES:
        if path.is_file():
            return path.read_text("utf-8")
    raise Refused("the release's settings template is missing from this image: pull or build the image again")


def env_keys() -> list[str]:
    """The settings the release knows, in the template's order."""
    return list(parse_env(env_template()))


def write_atomic(path: Path, data: bytes, mode: int) -> None:
    """Replace `path` with `data`: a new file of `mode`, created beside it before a byte is written, synced, then
    renamed over the name (a link there is replaced, never written through)."""
    write_stream(path, io.BytesIO(data), mode)


def write_stream(path: Path, source: BinaryIO, mode: int, *, chunk: int = 1024 * 1024) -> None:
    """write_atomic for what is read from `source` in chunks of `chunk` bytes, never whole. The new file is removed
    whenever anything fails before it has replaced the name."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb", closefd=True) as f:
            os.fchmod(f.fileno(), mode)
            shutil.copyfileobj(source, f, chunk)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_env(root: Path, values: Mapping[str, str], *, name: str = ENV_FILE) -> None:
    """Set `values` in the settings file, keeping every other line (comments and keys this release does not know) as
    it is: an existing file is edited in place, a missing one starts as the release's template. A key the file lacks
    is added at its end. The file is written 0600. A value compose would read otherwise, or a file with a line it
    would, is refused before anything is written."""
    for key, value in values.items():
        problem = value_problem(value)
        if problem or not _LINE.fullmatch(f"{key}={value}"):
            raise Refused(f"the setting {key} cannot be written: {problem or 'not a settings key'}")
    path = root / name
    try:
        text = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        text = env_template()
    except UnicodeDecodeError:
        raise Refused(f"{name} is not UTF-8 text: correct it") from None
    parse_env(text, name)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    left = dict(values)
    for i, line in enumerate(lines):
        m = _LINE.fullmatch(line)
        if m and m.group(1) in left:
            lines[i] = f"{m.group(1)}={left.pop(m.group(1))}"
    lines += [f"{key}={value}" for key, value in left.items()]
    write_atomic(path, ("\n".join(lines) + "\n").encode("utf-8"), 0o600)


def base_env(root: Path, chosen: Mapping[str, str]) -> dict[str, str]:
    """The settings an install writes: `chosen`, the invoking user's uid and gid, and the images the environment names
    (FF_IMAGE_ETL / FF_IMAGE_WEB, as `ff` passes them through) where the file names none yet."""
    uid, gid = invoking_ids()
    values = {**chosen, "FF_UID": str(uid), "FF_GID": str(gid)}
    current = read_env(root) or {}
    for key in ("FF_IMAGE_ETL", "FF_IMAGE_WEB"):
        if os.environ.get(key) and not current.get(key):
            values[key] = os.environ[key]
    return values


# ------------------------------------------------------------------ what an install takes from the release

def ensure_compose(root: Path, *, out: Callable[[str], None] = print) -> bool:
    """Copy the release's compose.yaml into an install directory that has none; True when it was copied."""
    target = root / COMPOSE_FILE
    if target.exists() or target.is_symlink():
        return False
    source = ROOT / COMPOSE_FILE
    if not source.is_file():
        raise Refused("the release's compose.yaml is missing from this image: pull or build the image again")
    write_atomic(target, source.read_bytes(), 0o644)
    out(f"wrote {shown(target, root)}, which runs the four services")
    return True


COMPOSE_PREVIOUS = "compose.yaml.previous"
COMPOSE_NEW = "compose.yaml.new"
# The sha256 of every compose.yaml a release has shipped: an install's file that is one of them was never changed by
# hand, so a newer release replaces it (refresh_compose). Frozen: each release adds its own before it ships, and a test
# fails while this release's file is missing from it.
SHIPPED_COMPOSE = frozenset({
    "4c2213c8135bb7fd769f40363e24f8137d72fcfb38a1528b0fd7e5a553a708ff",   # 1.0.0
    "787232dd7581c8a42de7de18f91ea52f58c40e98686416fb1952adc71eed37d1",   # 1.0.1
})


def refresh_compose(root: Path, *, out: Callable[[str], None] = print) -> str:
    """Bring the install's compose.yaml to the release's: "same" when it is already; "replaced" when it is a file a
    release shipped (kept first as compose.yaml.previous); "new" when it was changed by hand, which is left as it is,
    the release's written beside it as compose.yaml.new with one note. Run by preflight after every check passed, so
    an upgrade the new release refuses leaves the old file in place."""
    target = root / COMPOSE_FILE
    source = ROOT / COMPOSE_FILE
    if not source.is_file():
        raise Refused("the release's compose.yaml is missing from this image: pull or build the image again")
    new = source.read_bytes()
    old = None if target.is_symlink() else target.read_bytes()
    if old == new:
        return "same"
    if old is not None and hashlib.sha256(old).hexdigest() in SHIPPED_COMPOSE:
        write_atomic(root / COMPOSE_PREVIOUS, old, 0o644)
        write_atomic(target, new, 0o644)
        out(f"compose.yaml is this release's now: the one before is kept as {COMPOSE_PREVIOUS}")
        return "replaced"
    side = root / COMPOSE_NEW
    if side.is_symlink() or not side.is_file() or side.read_bytes() != new:
        write_atomic(side, new, 0o644)
    out(f"note: compose.yaml holds changes of your own, so it is kept and this release's is in {COMPOSE_NEW}: compare "
        "the two and carry your changes over to it")
    return "new"


def copy_starters(config_dir: Path, starter_dir: Path, *, out: Callable[[str], None] = print) -> list[str]:
    """Copy each starter rule file the config directory lacks; a file already there is never touched."""
    copied = []
    for source in sorted(starter_dir.glob("*.yaml")):
        target = config_dir / source.name
        if target.exists() or target.is_symlink():
            continue
        shutil.copyfile(source, target)
        os.chmod(target, 0o644)
        copied.append(source.name)
    if copied:
        out(f"copied the starter rule files into {shown(config_dir)}: {', '.join(copied)}")
    return copied


def address(env: Mapping[str, str]) -> str:
    """Where the dashboard answers, from the settings: a domain's https address, else http on the bind and port."""
    site = env.get("SITE_ADDRESS", "")
    if is_domain(site):
        port = env.get("FF_PORT_TLS") or "8443"
        return f"https://{site}" + ("" if port == "443" else f":{port}")
    bind = env.get("FF_BIND") or LOOPBACK
    host = "<this computer's address>" if bind in (EVERYWHERE, "::") else f"[{bind}]" if ":" in bind else bind
    return f"http://{host}:{env.get('FF_PORT') or DEFAULT_PORT}"


_DOMAIN = re.compile(r"(?=.{1,253}\Z)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}")


def is_domain(site: str) -> bool:
    """Whether SITE_ADDRESS names a domain (automatic HTTPS): a dotted name with no scheme or port, not an address."""
    return bool(_DOMAIN.fullmatch(site or ""))

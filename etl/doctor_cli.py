# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""`python -m etl doctor` — what stands between the install and a working dashboard, one line per check:

    ok    <what>
    warn  <what> — <the fix>
    FAIL  <what> — <the fix>

Read-only: it changes nothing. Exit 1 when any check fails, else 0. The checks: the user it runs as (never root); the
install directory and each directory of the install's table (there, the invoking user's, its mode); the settings file
(.env: there, FF_UID / FF_GID the invoking user's and not 0, FF_BIND an address, FF_PORT a port from 1024, and a
warning when the dashboard serves the network over plain HTTP); the config (the validator's own lines); the passcodes
file (its mode and how many usable lines, never what they hold); the bank feed's credential (none is fine: the
statement files alone; a mode others may read fails) and the age of its newest snapshot against the schedule; the
alert and AI credentials' modes; the statement files no account reads; the dashboard's data and its age; the free
space; and, from what `ff` gathered outside the container (FF_HOST_LISTENERS, FF_STACK_PS), whether the port is free
or held by this stack and how the stack's services are. A secret's content is never read into a line."""
from __future__ import annotations

import datetime as dt
import fnmatch
import json
import os
import re
import shutil
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import clock, install
from .config import Paths

GIB = 1024 ** 3


@dataclass(frozen=True)
class Finding:
    status: str                    # ok | warn | FAIL
    what: str
    fix: str = ""

    def line(self) -> str:
        return f"{self.status:<4}  {self.what}" + (f" — {self.fix}" if self.fix else "")


def ok(what: str) -> Finding:
    return Finding("ok", what)


def warn(what: str, fix: str) -> Finding:
    return Finding("warn", what, fix)


def fail(what: str, fix: str) -> Finding:
    return Finding("FAIL", what, fix)


# ------------------------------------------------------------------ the checks

def check_user() -> list[Finding]:
    uid, _ = install.invoking_ids()
    if uid == 0:
        return [fail("doctor runs as root", "run ff as the user who owns the install directory")]
    return [ok(f"runs as uid {uid}")]


def check_dirs(root: Path) -> list[Finding]:
    uid, gid = install.invoking_ids()
    out: list[Finding] = []
    try:
        st = install._lstat(root)
    except FileNotFoundError:
        return [fail(f"the install directory {install.shown(root, root)} is missing", "create it, then ./ff init")]
    problem = install.owner_problem(root, st, root)
    out.append(fail("the install directory is not yours", problem) if problem else ok("the install directory is yours"))
    mode = stat.S_IMODE(st.st_mode)
    if mode & ~install.ROOT_MODE:
        shown = install.shown(root, root)
        out.append(fail(f"the install directory {shown} is open to others ({mode:04o}): they could read the "
                        "household's finances", f"chmod {install.ROOT_MODE:o} {shown}"))
    missing, wrong = [], 0
    for d in install.DIRS:
        path = root / d.path
        shown = install.shown(path, root)
        try:
            st = install._lstat(path)
        except FileNotFoundError:
            missing.append(d.path)
            continue
        if stat.S_ISLNK(st.st_mode):
            out.append(fail(f"{shown} ({d.holds}) is a link", "it must not be one: move it aside, then ./ff up makes "
                                                                 "the folder"))
            wrong += 1
            continue
        if not stat.S_ISDIR(st.st_mode):
            out.append(fail(f"{shown} is not a directory ({d.holds})", "move it aside, then ./ff up makes the folder"))
            wrong += 1
            continue
        problem = install.owner_problem(path, st, root)
        if problem:
            out.append(fail(f"{shown} ({d.holds}) belongs to uid {st.st_uid}",
                            f"sudo chown -R {uid}:{gid} {shown}"))
            wrong += 1
            continue
        mode = stat.S_IMODE(st.st_mode)
        if mode & ~d.mode:
            out.append(fail(f"{shown} ({d.holds}) is open to others ({mode:04o})", f"chmod {d.mode:o} {shown}"))
            wrong += 1
        if mode & 0o700 != 0o700:
            out.append(fail(f"{shown} ({d.holds}) is not writable and searchable by you ({mode:04o})",
                            f"chmod u+rwx {shown}"))
            wrong += 1
    if missing:
        out.append(fail(f"missing: {', '.join(missing)}", "./ff up (or ./ff init) makes them as you"))
    elif not wrong:
        out.append(ok(f"the {len(install.DIRS)} directories are there, yours, with their modes"))
    return out


def check_env(root: Path) -> tuple[list[Finding], dict[str, str]]:
    try:
        env = install.read_env(root)
    except install.Refused as e:
        what, _, fix = str(e).rpartition(": ")
        return [fail(what or str(e), fix if what else "correct the settings file")], {}
    if env is None:
        return [fail("no settings file (.env)", "./ff init writes it")], {}
    uid, gid = install.invoking_ids()
    out: list[Finding] = []
    for key, want in (("FF_UID", uid), ("FF_GID", gid)):
        value = env.get(key, "")
        if value == "0":
            out.append(fail(f"{key} in .env is 0: the services would run as root", f"set {key}={want} in .env"))
        elif value != str(want):
            out.append(fail(f"{key} in .env is {value or 'not set'}, not yours ({want}): the services could not "
                            "write your files", f"set {key}={want} in .env"))
    bind, port = env.get("FF_BIND") or install.LOOPBACK, env.get("FF_PORT") or str(install.DEFAULT_PORT)
    import ipaddress
    try:
        ip = ipaddress.ip_address(bind)
    except ValueError:
        out.append(fail(f"FF_BIND in .env is {bind!r}, not an address", "set FF_BIND=127.0.0.1 (this computer) or "
                                                                         "0.0.0.0 (the home network)"))
        ip = None
    if not port.isdigit() or not 1024 <= int(port) <= 65535:
        out.append(fail(f"FF_PORT in .env is {port!r}", "set a port from 1024 to 65535, such as 8080"))
    if ip is not None and not ip.is_loopback and not install.is_domain(env.get("SITE_ADDRESS", "")):
        out.append(warn(f"the dashboard serves the network on {bind} over plain HTTP: the passcode crosses it "
                        "unencrypted", "serve it under a domain (SITE_ADDRESS) or keep FF_BIND=127.0.0.1"))
    if not out:
        out.append(ok(f"the settings: you (uid {uid}), listening on {bind} port {port}"))
    return out, env


def check_config() -> tuple[list[Finding], object]:
    from .config import Config
    from .validate import ConfigError
    try:
        cfg = Config(check_paths=True)
    except ConfigError as e:
        lines = [str(p) for p in e.problems]
        return ([fail(f"the config has {len(lines)} problem{'s' if len(lines) != 1 else ''}",
                      "correct each line below, then ./ff validate")]
                + [Finding("", f"      {line}") for line in lines]), None
    return [ok(f"the config validates ({len(cfg.files)} files, {len(cfg.accounts)} accounts)")], cfg


LINK_FIX = "it must not be one: move it aside and set it up again"


def _link_on_path(path: Path) -> Path | None:
    """The first link from the install directory down to `path` (itself included), or None: a secret is never read
    through one, so what lies behind it is never listed or opened."""
    root = install.home()
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return None
    for i in range(1, len(parts) + 1):
        candidate = root.joinpath(*parts[:i])
        if candidate.is_symlink():
            return candidate
    return None


def _secret_mode(path: Path, label: str) -> Finding | None:
    """A secret file's mode: FAIL when its group or others may read it, or it or a folder above it is a link; None
    when it is fine (or absent)."""
    linked = _link_on_path(path)
    if linked is not None:
        return fail(f"{install.shown(linked)} ({label}'s place) is a link", LINK_FIX)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return fail(f"{label} {install.shown(path)} is not a file", "move it aside and set it up again")
    if stat.S_IMODE(st.st_mode) & 0o077:
        return fail(f"{label} {install.shown(path)} may be read by others ({stat.S_IMODE(st.st_mode):04o})",
                    f"chmod 600 {install.shown(path)}")
    return None


def check_passcodes() -> list[Finding]:
    from .setup_cli import usable_passcodes
    path = Paths().secrets_web_dir() / "passcodes"
    bad = _secret_mode(path, "the passcodes file")
    if bad:
        return [bad]
    if not path.exists():
        return [fail("no passcode is set up: nobody can sign in", "./ff passcode add <name>")]
    n = usable_passcodes(path)
    if not n:
        return [fail("the passcodes file holds no usable passcode: nobody can sign in", "./ff passcode add <name>")]
    return [ok(f"{n} passcode{'s' if n != 1 else ''} set up (mode 600)")]


def _schedule_gap(config_dir: Path) -> float:
    """The longest wait, in hours, between two scheduled refreshes (household.yaml `refresh.hours`)."""
    from .validate import DEFAULT_REFRESH_HOURS
    try:
        doc = yaml.safe_load((config_dir / "household.yaml").read_text("utf-8")) or {}
        hours = sorted(set((doc.get("refresh") or {}).get("hours") or DEFAULT_REFRESH_HOURS))
    except (OSError, yaml.YAMLError, AttributeError, TypeError):
        hours = sorted(DEFAULT_REFRESH_HOURS)
    gaps = [(b - a) % 24 or 24 for a, b in zip(hours, hours[1:] + hours[:1], strict=True)]
    return float(max(gaps))


def check_feed(now: dt.datetime) -> list[Finding]:
    paths = Paths()
    store = paths.store_dir({})
    cred = paths.credential_path(store)
    bad = _secret_mode(cred, "the bank feed's credential")
    if bad:
        return [bad]
    if not cred.exists():
        return [ok("no bank feed is set up: the statement files alone are read")]
    if cred.stat().st_size == 0:
        return [fail("the bank feed's credential is empty (a setup that did not finish)", "./ff init connects it again")]
    out = [ok("the bank feed's credential is there (mode 600)")]
    snaps = sorted((store / "snapshots").glob("accounts-*.json")) if (store / "snapshots").is_dir() else []
    if not snaps:
        out.append(warn("the bank feed has no snapshot yet", "it arrives with the next scheduled refresh, or ./ff "
                                                              "refresh"))
        return out
    m = re.search(r"accounts-(\d{8}T\d{6}Z)", snaps[-1].name)
    taken = (dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC) if m
             else dt.datetime.fromtimestamp(snaps[-1].stat().st_mtime, dt.UTC))
    hours = (now - taken).total_seconds() / 3600
    limit = 2 * _schedule_gap(paths.config_dir) + 1
    if hours > limit:
        out.append(warn(f"the bank feed was last fetched {hours:.0f} hours ago (the schedule fetches at least every "
                        f"{limit / 2 - 0.5:.0f})", "the Data page's refresh card says why; ./ff refresh tries now"))
    else:
        out.append(ok(f"the bank feed was fetched {hours:.0f} hours ago"))
    return out


def check_other_secrets() -> list[Finding]:
    paths = Paths()
    out = []
    for folder, label in ((paths.secrets_alerts_dir(), "the alert credential"), (paths.secrets_ai_dir(),
                                                                                  "the AI key")):
        linked = _link_on_path(folder)
        if linked is not None:
            out.append(fail(f"{install.shown(linked)} ({label}'s place) is a link", LINK_FIX))
            continue
        try:
            names = sorted(p for p in folder.iterdir() if not p.name.startswith("."))
        except OSError:
            continue
        out += [f for f in (_secret_mode(p, label) for p in names) if f]
    return out


def check_statements(cfg) -> list[Finding]:
    if cfg is None:
        return []
    folder = cfg.source_dir if getattr(cfg, "source_dir", None) else Paths().imports_dir({})
    try:
        files = sorted(p.name for p in Path(folder).iterdir() if p.is_file() and not p.name.startswith("."))
    except (OSError, TypeError):
        return []
    patterns = [s.glob for a in cfg.accounts for s in cfg.txn_sources(a)]
    skipped = list(cfg.accounts_cfg.get("skip_files") or [])
    unread = [f for f in files if not any(fnmatch.fnmatch(f, p) for p in (*patterns, *skipped))]
    if unread:
        return [warn(f"{len(unread)} statement file{'s' if len(unread) != 1 else ''} in imports/ no account reads",
                     "./ff import detect imports/<file> says what each is; name it in accounts.yaml")]
    return [ok(f"every statement file in imports/ is read ({len(files)})")]


def check_exports(now: dt.datetime) -> list[Finding]:
    manifest = Paths().data_dir() / "exports" / "manifest.json"
    try:
        age = (now.timestamp() - manifest.stat().st_mtime) / 3600
    except FileNotFoundError:
        return [warn("the dashboard has no data yet", "the first refresh builds it; ./ff refresh builds it now")]
    if age > 48:
        return [warn(f"the dashboard's data was built {age / 24:.0f} days ago",
                     "the Data page's refresh card says why; ./ff refresh builds it now")]
    return [ok(f"the dashboard's data was built {age:.0f} hours ago")]


def check_space(root: Path) -> list[Finding]:
    free = shutil.disk_usage(root).free
    if free < GIB:
        return [warn(f"{free / 1024 ** 2:.0f} MiB free under the install directory", "free some space: the data and "
                                                                                       "the backups grow")]
    return [ok(f"{free / GIB:.1f} GiB free")]


def _services(raw: str) -> list[dict]:
    raw = raw.strip()
    if not raw:
        return []
    try:
        doc = json.loads(raw)
        return [s for s in (doc if isinstance(doc, list) else [doc]) if isinstance(s, dict)]
    except ValueError:
        out = []
        for line in raw.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out


def check_ports(env: dict[str, str]) -> list[Finding]:
    """The two ports web publishes (FF_PORT, and FF_PORT_TLS for a domain's HTTPS), against what `ff` read of this
    computer's listeners (FF_HOST_LISTENERS) and of the stack (FF_STACK_PS): free, held by this stack's running web
    server, or taken by another program (which would leave the stack half started)."""
    listeners = os.environ.get("FF_HOST_LISTENERS")
    services = _services(os.environ.get("FF_STACK_PS") or "")
    web = next((s for s in services if s.get("Service") == "web"), None)
    if web is not None and web.get("State") == "running":
        return [ok("the ports are held by this stack's web server")]
    out = []
    for key, default in (("FF_PORT", str(install.DEFAULT_PORT)), ("FF_PORT_TLS", "8443")):
        port = env.get(key) or default
        if listeners is None or listeners.strip() in ("", "unknown"):
            out.append(warn(f"whether port {port} is free could not be checked",
                            f"if ./ff up says it is taken, set another {key} in .env"))
        elif re.search(rf"[:\]]{re.escape(port)}\s", listeners + " "):
            out.append(fail(f"port {port} is taken by another program", f"set another {key} in .env"))
        else:
            out.append(ok(f"port {port} is free"))
    return out


def check_outside(env: dict[str, str]) -> list[Finding]:
    """The two checks `ff` gathers outside the container: the ports, and the stack's services."""
    if os.environ.get("FF_HOST_LISTENERS") is None and os.environ.get("FF_STACK_PS") is None:
        return []
    services = _services(os.environ.get("FF_STACK_PS") or "")
    out = check_ports(env)
    if not services:
        out.append(ok("the stack is not running (./ff up starts it)"))
        return out
    for s in sorted(services, key=lambda s: str(s.get("Service"))):
        name, state, health = s.get("Service"), s.get("State"), s.get("Health") or ""
        if state == "running" and health in ("", "healthy"):
            out.append(ok(f"{name} is running" + (" and healthy" if health else "")))
        elif state == "running" and health == "starting":
            out.append(warn(f"{name} is starting", "wait a minute, then ./ff doctor again"))
        else:
            out.append(fail(f"{name} is {state}{' (' + health + ')' if health else ''}",
                            f"./ff logs {name} says why"))
    return out


def run(*, out: Callable[[str], None] = print, now: dt.datetime | None = None) -> int:
    now = now or clock.wall_utc()
    try:
        root = install.home()
    except install.Refused as e:
        print(f"doctor: {e}", file=sys.stderr)
        return 2
    findings = check_user() + check_dirs(root)
    env_findings, env = check_env(root)
    findings += env_findings
    config_findings, cfg = check_config()
    findings += config_findings
    findings += check_passcodes() + check_feed(now) + check_other_secrets() + check_statements(cfg)
    findings += check_exports(now) + check_space(root) + check_outside(env)
    for f in findings:
        out(f.line() if f.status else f.what)
    failed = sum(f.status == "FAIL" for f in findings)
    out(f"{failed} problem{'s' if failed != 1 else ''} found" if failed else "everything checked is in order")
    return 1 if failed else 0

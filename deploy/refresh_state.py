# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Shared state for the dashboard's "Refresh now" button, and where the store and
the refresh worker find their directories (LOCATIONS, locate).

Three processes touch the refresh directory, the mailbox (FF_HOME's state/refresh/,
var/refresh/ by default; mounted into the store and both workers) and
none of them talk to each other directly:

  store          writes request.json + ledger.json   (rate limit, then enqueue)
                 touches ingest_wanted               (an ingest without a fetch is wanted)
                 touches ai_wanted, ai_reset_wanted  (the AI step, or forgetting its answers, then an ingest)
  refresher      writes status.json                  (claims a request or an ingest, runs it)
  refresh.sh     writes status.json                  (running -> done; the ingest_* fields)
  scheduler      writes schedule.json                (at its start: deploy/make_crontab.py)

Two sides write the mailbox, so every read and write of it goes through
safe_files: never through a link, never waiting on a FIFO, a write always a new
file renamed over the name.

schedule.json (SCHEDULE_FILE, Schedule) is the household's time zone and the
hours and minute of its scheduled refresh, which the scheduler reads from
household.yaml and writes as it starts. It is how the household's zone reaches
the store, which cannot read the config: every "local day" here (the manual
cap, the ingest-only cap, the seconds to midnight) is the day on that zone's
clock (household_zone), UTC while the file is missing or malformed, and the
store's refresh view reports the schedule from it, or none.

request.json and ledger.json have exactly one writer. status.json has two, and
they are kept apart by LOCK_FILE: the refresher takes that lock BEFORE claiming
a request and holds it until the run ends, so its writes and refresh.sh's can
never interleave. The lock lives in the engine's directory (FF_HOME's
state/engine/, which only the workers mount; the mailbox itself by
default and when only the mailbox is named: engine_dir), and every run that holds
it also holds a file of the same name in the mailbox, the run's liveness, which
the store only probes (is_running): the kernel lets both go when the run dies.
`queued` is not a written state at all — a request.json whose nonce has not yet
been copied into status.json IS the queue.

An ingest without a fetch has no request file and no nonce. The store touches
INGEST_MARKER (its mtime, a whole second one to two seconds after the touch, is
the request); every ingest, an ingest-only run (`refresh.sh ingest`) or a
refresh's own ingest step, records its start, end and exit code in status.json's
ingest_* fields, merged beside the refresh's own fields, which it leaves alone;
ingest_runs holds the local day's ingest-only starts, for MAX_INGEST_PER_DAY,
and ingest_only is true while the refresher's ingest-only run holds the lock. A
marker older than the last ingest's start is answered (ingest_due), so it is
never removed, unless that ingest ended interrupted.

ingest_rc, the last ingest's exit code, is one of: 0 (it succeeded); the
ingest's own failure code (1, say); 143, stopped from outside (refresh.sh's
TERM trap, as when the refresher is stopped mid-run); INGEST_TIMED_OUT_RC (-1),
killed by the refresher past its time limit; INGEST_INTERRUPTED_RC (-2), found
open with no run holding the lock (its run died before it could record an end);
or 128 + N, when the refresher saw the script killed by signal N before it
recorded the end, as the shell reports one (130 for INT, 137 for KILL; killed by
TERM it reads 143, stopped from outside). The two negative codes are the
refresher's own and nothing else. An ingest that ended stopped (143) or found
dead (-2) read nothing reliably, so it does not answer the marker and the
marker's ingest is run again (ingest_wanted); a timed-out or failed one is not
retried, so a hung or broken ingest is never looped.

Stdlib only (with safe_files beside it), and importable by BOTH scenario_store.py
and refresh_runner.py — the store imports nothing of the ETL, so nothing here
may reach into etl/.

The canonical timestamps in these files are UTC epoch floats: they get compared
and bucketed into local days, and a naive local ISO string would make that
silently wrong the day the household's zone changes. The `*_iso` fields written alongside
them are read-only mirrors, there so `docker exec … cat status.json` is legible
during a debug session — never parse those back.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import math
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import safe_files

MIN_GAP_S = 15 * 60            # minimum between refreshes of ANY kind
MAX_MANUAL_PER_DAY = 6         # manual runs per local calendar day
# status says running + lock free + started longer ago than this -> the run died.
# The only legitimate gap between those two facts is the refresher writing
# `running` and the refresh.sh it spawns reaching its flock — a process spawn,
# well under a second. 90s is generous for that and still re-enables the button
# quickly after a `docker kill`, rather than leaving it spinning for a quarter
# of an hour on a run that is already gone.
STALE_RUN_S = 90
WORKER_STALE_S = 180           # unclaimed request + nothing running -> worker down
LEDGER_KEEP_S = 48 * 3600
# SimpleFIN allows <=24 requests/day; at most MAX_SCHEDULE_HOURS scheduled + 6
# manual = 12 a day. This backstop counts the fetches of the last 24 hours, and
# exists so that no combination of bugs in the rate limiter can get the
# household throttled.
HARD_24H_FETCH_CEILING = 12
# An ingest without a fetch spends nothing of the feed's budget, so its cap only bounds a runaway: one every half hour
# of a day. The settle time makes a person saving several manual assets in a row cause one ingest, not one per save.
MAX_INGEST_PER_DAY = 48        # ingest-only runs per local calendar day
INGEST_SETTLE_S = 60           # a marker touched less than this long ago waits
# The ingest_rc values the refresher writes itself (the module docstring lists every value a reader meets): an ingest
# it killed past its time limit, and one it found open with no run holding the lock.
INGEST_TIMED_OUT_RC = -1
INGEST_INTERRUPTED_RC = -2
# what refresh.sh's TERM trap records: the ingest was stopped from outside
INGEST_STOPPED_RC = 143
# an ingest that ended so read nothing reliably, so it does not answer the marker (ingest_wanted)
INGEST_RETRIED_RCS = (INGEST_STOPPED_RC, INGEST_INTERRUPTED_RC)

REQUEST_FILE = "request.json"
STATUS_FILE = "status.json"
LEDGER_FILE = "ledger.json"
LOCK_FILE = "refresh.lock"
INGEST_MARKER = "ingest_wanted"
# What the household asks of the AI step from the app (the store touches them, the refresher answers them): run it, and
# forget its stored answers, each followed by an ingest (ai_due).
AI_MARKER = "ai_wanted"
AI_RESET_MARKER = "ai_reset_wanted"
AI_MARKERS = {"run": AI_MARKER, "reset": AI_RESET_MARKER}
SCHEDULE_FILE = "schedule.json"

# The most hours a day the scheduled refresh may run at (etl/validate.py MAX_REFRESH_HOURS, which this module cannot
# import: tests/test_make_crontab.py pins the two equal), and the zone every local day is drawn in while the scheduler
# has written no schedule.
MAX_SCHEDULE_HOURS = 6
DEFAULT_ZONE = "UTC"

# The bank feed's errors as the fetch prints them (etl/simplefin.py _warn_errors: one line each, this prefix, then
# `Connection to <name> may need attention. ` when the entry names a connection, then `[<code>] <message> <id>` for a
# version-2 entry or the text of a version-1 one). The status keeps the first MAX_FEED_ERRORS, each at most
# MAX_FEED_TEXT characters; an entry that names no connection is filed under FEED_PROVIDER. REAUTH_CODE is the
# version-2 code of a connection whose bank the household must sign in to again.
FEED_WARNING = "WARN simplefin: "
MAX_FEED_ERRORS = 10
MAX_FEED_TEXT = 200
FEED_PROVIDER = "the feed provider"
REAUTH_CODE = "con.auth"

IngestDue = Literal["no", "wait", "cap", "run"]

TERMINAL_STATES = ("done", "interrupted")


# ---------------------------------------------------------------- where the directories are

@dataclass(frozen=True)
class Location:
    """A directory the store or the refresh worker takes: the location's own variable (`below` is the part under the
    variable's directory, for a variable that names a root), a variable that names the folder holding it and its
    siblings at once (`flat`, consulted next), its place under FF_HOME, and its default place
    (`legacy`, under the containers' /app)."""
    variable: str
    under_home: str
    legacy: str
    below: str = ""
    flat: str = ""


# The one table of the deploy side's directories. etl/config.py Paths resolves the same variables for the ETL, which
# these stdlib programs cannot import (the store's container holds no ETL code path); tests/test_paths.py holds the
# two to one answer.
LOCATIONS: dict[str, Location] = {
    # the family documents (scenario_store.py --dir); FF_STATE_DIR names the one folder of the whole state, as Paths
    # reads it
    "documents": Location("FF_STATE_FAMILY_DIR", "state/family", "/app/scenarios", flat="FF_STATE_DIR"),
    # the mailbox: this module's files (scenario_store.py --refresh-dir, refresh_runner.py --dir)
    "refresh": Location("FF_STATE_REFRESH_DIR", "state/refresh", "/app/var/refresh"),
    # what only the workers keep: the refresh lock, the heartbeat, refresh.sh's streaks and log (refresh_runner.py
    # --engine-dir); with none of these named, the mailbox (engine_dir)
    "engine": Location("FF_STATE_ENGINE_DIR", "state/engine", "/app/var/refresh"),
    # the feed's snapshots, which the worker counts (refresh_runner.py --snapshots); FF_STORE_DIR names the stores'
    # root, as it does for the ETL
    "snapshots": Location("FF_STORE_DIR", "store/simplefin/snapshots", "/app/simplefin/snapshots",
                          below="simplefin/snapshots"),
    # the sign-in's passcodes file (scenario_store.py --secrets-web-dir) and its sessions and devices
    # (scenario_store.py --state-auth-dir); the default places are where Paths puts them under the containers' /app
    "secrets_web": Location("FF_SECRETS_WEB_DIR", "secrets/web", "/app/secrets/web"),
    "auth": Location("FF_STATE_AUTH_DIR", "state/auth", "/app/state/auth"),
}


def _env_dir(name: str) -> Path | None:
    """The variable's directory as an absolute path (a relative value is taken from the current directory), or None
    when it is unset or empty: etl/config.py reads every path variable the same way."""
    value = os.environ.get(name, "")
    return Path(os.path.abspath(value)) if value else None


def locate(name: str, given: str | None = None, *, fallback: bool = True) -> Path | None:
    """Where the directory `name` (LOCATIONS) is: `given`, a command-line argument, as it is given; else its variable
    (with the location's `below` under it); else its `flat` variable; else its place under FF_HOME; else its default
    place, or None when `fallback` is false (a program that goes without the directory when nothing names it). An
    empty argument or variable counts as unset."""
    loc = LOCATIONS[name]
    if given:
        return Path(given)
    named = _env_dir(loc.variable)
    if named:
        return named / loc.below if loc.below else named
    flat = _env_dir(loc.flat) if loc.flat else None
    if flat:
        return flat
    home = _env_dir("FF_HOME")
    if home:
        return home / loc.under_home
    return Path(loc.legacy) if fallback else None


def engine_dir(refresh_dir: Path, given: str | None = None) -> Path:
    """The engine's directory of a worker whose mailbox is `refresh_dir`: `given`, else its variable, else FF_HOME's
    state/engine/, else the mailbox itself (by default, and a mailbox named alone), as refresh.sh finds it."""
    return locate("engine", given, fallback=False) or refresh_dir


# ---------------------------------------------------------------- time helpers

def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (KeyError, ValueError, OSError) as e:   # ZoneInfoNotFoundError subclasses KeyError
        print(f"refresh_state: unknown time zone {tz!r} ({e}) — using UTC",
              file=sys.stderr, flush=True)
        return ZoneInfo(DEFAULT_ZONE)


def utc_iso(ts: float) -> str:
    """Epoch -> '2026-08-10T22:00:00Z'. Always UTC, so the browser can parse it
    with new Date() anywhere."""
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_day(ts: float, tz: str) -> dt.date:
    return dt.datetime.fromtimestamp(ts, _zone(tz)).date()


def seconds_to_local_midnight(now: float, tz: str) -> int:
    """Seconds until the daily cap resets: the next midnight on the clock of
    `tz`, the household's zone. Daylight-saving days are 23h/25h long and this
    handles both by construction — America/New_York changes its clocks at 02:00,
    so its midnight always exists; in a zone whose midnight is skipped, the
    instant the day begins is the change itself."""
    z = _zone(tz)
    tomorrow = dt.datetime.fromtimestamp(now, z).date() + dt.timedelta(days=1)
    midnight = dt.datetime.combine(tomorrow, dt.time(0, 0), tzinfo=z)
    return max(1, math.ceil(midnight.timestamp() - now))


def new_nonce() -> str:
    return secrets.token_hex(8)


# ------------------------------------------------------------------------- io

def atomic_write_json(path: Path, doc: dict) -> None:
    """A new file beside `path`, fsynced, renamed over it (safe_files.write_atomic: a link at the name is replaced,
    never written through).

    NEVER call this on LOCK_FILE: os.replace would swap the inode out from
    under a holder, and flock(2) locks the inode. Two processes would then each
    "hold" a different file and both believe they had the lock."""
    safe_files.write_atomic(path, json.dumps(doc).encode("utf-8"))


def read_json(path: Path, default):
    """Missing -> default. Corrupt, unreadable or no regular file (a link, a FIFO, a folder: never followed, never
    waited on) -> loud stderr + default (self-heal), the same contract as scenario_store.read_store."""
    try:
        return json.loads(safe_files.read_regular(path).decode("utf-8"))
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        print(f"refresh_state: corrupt/unreadable {path}: {e} — using default",
              file=sys.stderr, flush=True)
        return default


def read_request(d: Path) -> dict | None:
    doc = read_json(d / REQUEST_FILE, None)
    return doc if isinstance(doc, dict) else None


def write_request(d: Path, *, nonce: str, now: float) -> None:
    atomic_write_json(d / REQUEST_FILE, {"nonce": nonce, "requested_at": now})


def read_status(d: Path) -> dict:
    doc = read_json(d / STATUS_FILE, {})
    return doc if isinstance(doc, dict) else {}


def write_status(d: Path, **fields) -> dict:
    """Merge `fields` over the stored doc and write it back. Merging is safe
    because every writer holds LOCK_FILE for the whole run (the refresher takes
    it before claiming; refresh.sh takes it itself when run any other way), so
    the read-modify-write can never interleave. The `running` transition must
    still pass explicit nulls for rc/finished_at/steps_failed/warnings, or the
    previous run's outcome leaks into the new one."""
    doc = read_status(d)
    doc.update(fields)
    atomic_write_json(d / STATUS_FILE, doc)
    return doc


def load_ledger(d: Path) -> list[float]:
    doc = read_json(d / LEDGER_FILE, None)
    runs = doc.get("manual_runs") if isinstance(doc, dict) else None
    if not isinstance(runs, list):
        return []
    return sorted(
        float(t) for t in runs
        if isinstance(t, (int, float)) and not isinstance(t, bool))


def save_ledger(d: Path, runs: list[float]) -> None:
    atomic_write_json(d / LEDGER_FILE, {"version": 1, "manual_runs": sorted(runs)})


def prune(runs: list[float], now: float, keep_s: int = LEDGER_KEEP_S) -> list[float]:
    """Bound the ledger. 48h covers the widest daily window (a 25h DST day)
    with room to spare; without this the file grows forever."""
    return [t for t in runs if now - t < keep_s]


# ------------------------------------------------------------------ the schedule

@dataclass(frozen=True)
class Schedule:
    """The scheduled refresh as the scheduler writes it in the mailbox (SCHEDULE_FILE): the household's zone, the hours
    of the day it runs at on that zone's clock (in order, each once) and the minute past each."""
    timezone: str
    hours: tuple[int, ...]
    minute: int

    def times(self) -> list[str]:
        """Its times of day, `HH:MM`, in order: what the store's refresh view reports."""
        return [f"{h:02d}:{self.minute:02d}" for h in self.hours]

    def doc(self) -> dict:
        return {"timezone": self.timezone, "hours": list(self.hours), "minute": self.minute}


def _whole(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def known_zone(name) -> bool:
    """A zone name the zone database knows (never `localtime`, which is whatever the machine is set to)."""
    if not isinstance(name, str) or not name.strip() or name == "localtime":
        return False
    try:
        ZoneInfo(name)
    except (KeyError, ValueError, OSError):   # ZoneInfoNotFoundError subclasses KeyError
        return False
    return True


def schedule_from(doc) -> Schedule | None:
    """`doc` as a Schedule: an object whose `timezone` is a zone the database knows (known_zone), whose `hours` are one
    to MAX_SCHEDULE_HOURS distinct whole hours from 0 to 23 and whose `minute` is a whole minute from 0 to 59; None for
    anything else."""
    if not isinstance(doc, dict):
        return None
    tz, hours, minute = doc.get("timezone"), doc.get("hours"), doc.get("minute")
    if not known_zone(tz) or not isinstance(hours, list) or not 1 <= len(hours) <= MAX_SCHEDULE_HOURS:
        return None
    if not all(_whole(h) and 0 <= h <= 23 for h in hours) or len(set(hours)) != len(hours):
        return None
    if not _whole(minute) or not 0 <= minute <= 59:
        return None
    return Schedule(tz, tuple(sorted(hours)), minute)


def read_schedule(d: Path | None) -> Schedule | None:
    """The schedule the scheduler wrote in the mailbox `d`, or None while it has written none, or what is there is no
    schedule (schedule_from) or no regular file (never followed: read_json)."""
    return None if d is None else schedule_from(read_json(d / SCHEDULE_FILE, None))


def write_schedule(d: Path, schedule: Schedule) -> None:
    """Write `schedule` in the mailbox `d`, a new file renamed over the name (never through a link)."""
    atomic_write_json(d / SCHEDULE_FILE, schedule.doc())


def household_zone(d: Path | None) -> str:
    """The zone every local day of the mailbox `d` is drawn in: the schedule's (read_schedule), else DEFAULT_ZONE."""
    schedule = read_schedule(d)
    return schedule.timezone if schedule is not None else DEFAULT_ZONE


# ------------------------------------------------------------------ the feed's errors

_FEED_CONNECTION = re.compile(r"Connection to (?P<name>.+?) may need attention\.(?: (?P<rest>.*))?")
_FEED_CODED = re.compile(r"\[(?P<code>[^\]\s]*)\](?: (?P<rest>.*))?")
_URL_USER = re.compile(r"//[^/\s]*@")


def _plain(text: str) -> str:
    """Feed text as a person reads it: no character that does not print, its whitespace squashed, any URL's user and
    password taken out, at most MAX_FEED_TEXT characters."""
    text = " ".join("".join(c if c.isprintable() else " " for c in text).split())
    text = _URL_USER.sub("//", text)
    return text if len(text) <= MAX_FEED_TEXT else text[:MAX_FEED_TEXT - 1].rstrip() + "…"


def feed_report(text: str) -> tuple[list[str], list[str]]:
    """What a fetch's output (`text`) says of the bank feed, from its FEED_WARNING lines: the errors, each
    `<connection>: <message>` (FEED_PROVIDER for an entry that names no connection, and a version-2 entry's code and
    trailing id left out), the first MAX_FEED_ERRORS distinct ones; and the connections whose bank the household must
    sign in to again, each once in the order met: a version-2 entry coded REAUTH_CODE, or a version-1 entry naming a
    connection (the feed words it "may need attention", which the refresh's alert has always read as that)."""
    errors: list[str] = []
    reauth: list[str] = []
    for line in text.splitlines():
        at = line.find(FEED_WARNING)
        if at < 0:
            continue
        name, rest = None, line[at + len(FEED_WARNING):].strip()
        if m := _FEED_CONNECTION.fullmatch(rest):
            name, rest = _plain(m["name"]), m["rest"] or ""
        code = None
        if m := _FEED_CODED.fullmatch(rest):
            code, rest = m["code"], m["rest"] or ""
            if name:
                rest = rest.rpartition(" ")[0]          # the account's or the connection's id, which comes last
        item = f"{name or FEED_PROVIDER}: {_plain(rest) or 'it needs attention'}"
        if item not in errors and len(errors) < MAX_FEED_ERRORS:
            errors.append(item)
        if name and code in (None, REAUTH_CODE) and name not in reauth:
            reauth.append(name)
    return errors, reauth


def _feed_text(path: str) -> str:
    """The text of the run output at `path` (never through a link), or "" when none is named or it cannot be read."""
    if not path:
        return ""
    try:
        return safe_files.read_regular(path).decode("utf-8", errors="replace")
    except OSError as e:
        print(f"refresh_state: the run's output {path} cannot be read: {e.strerror or e}", file=sys.stderr, flush=True)
        return ""


# ------------------------------------------------------------------ liveness

LIVENESS_TRIES = 20            # a liveness file another holds is tried again for about a second, then gone without


def hold_liveness(paths, held: list[int]) -> None:
    """With the refresh lock held, also take each other path's lock (the run's liveness in the mailbox, which the
    store probes): appended to `held`. One the store is probing this instant is tried again shortly; one that cannot
    be had, or holds no regular file (never followed), is gone without, which only leaves the store's view of the run
    the poorer: the refresh lock alone keeps two runs apart."""
    for path in paths:
        try:
            fd = safe_files.open_lock(path)
        except OSError as e:
            print(f"refresh_state: no liveness lock at {path}: {e.strerror or e}", file=sys.stderr, flush=True)
            continue
        for _ in range(LIVENESS_TRIES):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(fd)
                break
            except OSError:
                time.sleep(0.05)
        else:
            os.close(fd)
            print(f"refresh_state: the liveness lock at {path} is held elsewhere", file=sys.stderr, flush=True)


@contextlib.contextmanager
def try_lock(lock_path: Path, *liveness: Path):
    """Take the refresh lock if it is free (yield True), and with it each of
    `liveness` that is another file (hold_liveness); yield False if someone else
    holds the refresh lock. No file is opened through a link (safe_files).

    Never blocks. The only caller is a 2-second poll loop, so "come back
    shortly" is simpler than waiting — and it is also more honest: while a run
    holds the lock, /api/refresh reports `running` off that same lock, so the
    browser is already watching the right thing instead of the worker sitting
    in a blocking wait with nothing to show for it.

    Holding this around the whole claim-and-run is what makes the sequence
    safe. Claiming outside it would let a scheduled run that is already in
    flight overwrite the claim with its own terminal status (the browser would
    see its refresh "finish" early), and would skip the coalescing check that
    stops a click landing mid-cron-run from spending a second SimpleFIN
    request for data that run is already fetching."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    held = [safe_files.open_lock(lock_path)]
    try:
        try:
            fcntl.flock(held[0], fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        hold_liveness([p for p in liveness if os.path.abspath(p) != os.path.abspath(lock_path)], held)
        yield True
    finally:
        for fd in held:
            os.close(fd)   # closing releases the locks, exception or not


def is_running(lock_path: Path) -> bool:
    """Is a refresh actually alive right now?

    Answered by probing the lock, NOT by trusting status.json's contents. The
    kernel releases an flock when the holding process dies — SIGKILL, OOM,
    `docker kill`, the 10s grace after `docker compose up -d` — so this can
    never wedge the button the way a stale {"state":"running"} file can.

    flock(2) is per open-file-description, so a fresh open() here contends with
    a holder even inside the same process. A read-only fd is fine: unlike the
    fcntl record locks, flock ignores the open mode. The store probes the run's
    liveness file in the mailbox; a link or anything but a regular file there
    is never followed and reads as nothing running."""
    try:
        fd = safe_files.open_lock(lock_path, create=False)
    except OSError:
        return False   # no lock file yet -> nothing has ever run
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False   # we took it, so nobody was holding it
    except OSError:
        return True
    finally:
        os.close(fd)   # closing releases whatever we just took


def queued_age(request: dict | None, status: dict, now: float) -> float | None:
    """Seconds since an UNCLAIMED request was written, or None if there is
    none. A request whose nonce the refresher has already copied into
    status.json is claimed, not queued."""
    if not isinstance(request, dict):
        return None
    nonce = request.get("nonce")
    if not isinstance(nonce, str) or not nonce:
        return None
    if status.get("request_nonce") == nonce:
        return None
    requested = request.get("requested_at")
    if not isinstance(requested, (int, float)) or isinstance(requested, bool):
        return None
    return max(0.0, now - float(requested))


def last_started_at(status: dict) -> float | None:
    """When the last refresh of any kind began. The SimpleFIN fetch is the
    first thing a run does, so the start — not the finish — is when the
    request was actually spent."""
    ts = status.get("started_at")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return float(ts)
    return None


def is_stalled(status: dict, lock_path: Path, now: float,
               stale_s: int = STALE_RUN_S) -> bool:
    """status.json says running, but nobody holds the lock and it started long
    enough ago that this is not a startup race — the run died mid-flight."""
    if status.get("state") != "running":
        return False
    if is_running(lock_path):
        return False
    started = last_started_at(status)
    return started is None or (now - started) > stale_s


def already_satisfied(request: dict, status: dict) -> bool:
    """True when a refresh that STARTED after this request was made has already
    finished — the data it asked for is on disk, so running again would spend a
    second SimpleFIN request for nothing.

    This is coalescing, not just serializing. Without it, a click landing during
    the 09:00 cron run waits out the lock and then immediately re-fetches.
    Deliberately does not require rc == 0: if the covering run failed, the
    request has still been answered ("it failed"), and re-running on the spot
    would just fail again while spending another request."""
    if status.get("state") not in TERMINAL_STATES:
        return False
    started = last_started_at(status)
    requested = request.get("requested_at")
    if started is None or not isinstance(requested, (int, float)):
        return False
    return started >= float(requested)


def count_recent_fetches(snapshots_dir: Path, now: float,
                         window_s: int = 86_400) -> int:
    """SimpleFIN requests actually spent in the last 24h, counted from the
    snapshot files themselves (mtime — etl.simplefin.fetch writes them once and
    never touches them again).

    Ground truth: no store bug, hand-edited request file, or wiped
    ledger can desync this. Rolling window rather than a calendar day, which is
    also how SimpleFIN's own "<=24 requests/day" reads."""
    try:
        entries = list(snapshots_dir.glob("accounts-*.json"))
    except OSError:
        return 0
    n = 0
    for p in entries:
        try:
            if now - p.stat().st_mtime < window_s:
                n += 1
        except OSError:
            continue
    return n


# ---------------------------------------------------- the ingest without a fetch

def _epoch(value) -> float | None:
    """An epoch-seconds field as written, or None for anything else (a bool is no time)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def ingest_started_at(status: dict) -> float | None:
    """When the last ingest of any kind began (status.json ingest_started_at), or None."""
    return _epoch(status.get("ingest_started_at"))


def touch_ingest_marker(d: Path, now: float | None = None) -> None:
    """Ask for an ingest: set INGEST_MARKER's mtime to the whole second `floor(t) + 2`, `t` being `now` (epoch seconds)
    or the current time, creating the file when it is absent. One to two seconds after the touch, so a filesystem that
    keeps one- or two-second mtimes, truncating it, still leaves it after any ingest that started before the save: a
    save made in the second an ingest starts is never read as answered by it. ingest_due waits out a marker in the
    future like any fresh one, so the settle ends 61 to 62 seconds after the save. Touched in place, never replaced,
    emptied or removed: only its mtime means anything, and an ingest that starts after it answers it (ingest_due).
    Never through a link (safe_files.touch: anything but a regular file at the name is an OSError)."""
    t = time.time() if now is None else now
    safe_files.touch(d / INGEST_MARKER, math.floor(t) + 2)


def marker_time(d: Path) -> float | None:
    """When the marker was last touched (its mtime), or None when there is none or the name holds no regular file."""
    return safe_files.regular_mtime(d / INGEST_MARKER)


def touch_ai_marker(d: Path, which: str, now: float | None = None) -> None:
    """Ask for the AI step (`which` "run") or for its stored answers to be forgotten ("reset"), and for the ingest that
    follows: the AI marker and then INGEST_MARKER, each touched as touch_ingest_marker touches its own (the whole
    second floor(t) + 2, never through a link), so the ingest marker's followers (the store's refresh view, the app)
    see the ingest the request brings."""
    t = time.time() if now is None else now
    safe_files.touch(d / AI_MARKERS[which], math.floor(t) + 2)
    touch_ingest_marker(d, t)


def ai_marker_time(d: Path, which: str) -> float | None:
    """When an AI marker ("run" or "reset") was last touched, or None when there is none or the name holds no regular
    file."""
    return safe_files.regular_mtime(d / AI_MARKERS[which])


def ai_due(d: Path, status: dict) -> dict[str, bool]:
    """Which AI markers are not answered yet: each one newer than status.json `ai_handled_at`, which the refresher
    writes as it claims the ingest-only run that answers them (so a run that dies is not repeated: the AI step's own
    caps bound it, and a new request touches the marker again)."""
    handled = _epoch(status.get("ai_handled_at"))
    out = {}
    for which in AI_MARKERS:
        mark = ai_marker_time(d, which)
        out[which] = mark is not None and (handled is None or mark > handled)
    return out


def ingest_wanted(marker: float | None, status: dict) -> bool:
    """A marker exists, and it is newer than the last ingest's start or that ingest ended interrupted
    (ingest_interrupted). That start is any ingest's, an ingest-only run's or a refresh's own ingest step's (both write
    ingest_started_at as they begin): one that began after the touch read what the touch announced, since the store
    writes the document before it touches the marker, unless it was stopped before it finished. With no marker
    nothing is wanted, and an ingest still open (no end recorded) answers it as before."""
    if marker is None:
        return False
    started = ingest_started_at(status)
    return started is None or marker > started or ingest_interrupted(status)


def ingest_interrupted(status: dict) -> bool:
    """The last ingest has recorded its end, and its exit code is one of INGEST_RETRIED_RCS: stopped from outside
    (143) or found dead (INGEST_INTERRUPTED_RC). A timed-out ingest (INGEST_TIMED_OUT_RC) or a failed one is not:
    retrying those would loop a hung or broken ingest."""
    rc = status.get("ingest_rc")
    return (ingest_started_at(status) is not None and not ingest_open(status)
            and type(rc) is int and rc in INGEST_RETRIED_RCS)


def ingest_runs_today(status: dict, now: float, tz: str) -> int:
    """The ingest-only runs that started on `now`'s local day (status.json ingest_runs; junk entries count for
    nothing)."""
    runs = status.get("ingest_runs")
    if not isinstance(runs, list):
        return 0
    today = local_day(now, tz)
    return sum(1 for t in runs if (e := _epoch(t)) is not None and local_day(e, tz) == today)


def ingest_due(d: Path, now: float, tz: str) -> IngestDue:
    """Should an ingest-only run start now? Pure given the directory's files (the marker's mtime, status.json):
    "no" when nothing is wanted (ingest_wanted: no marker, or one no newer than the last ingest's start that ended
    other than interrupted); "wait" while the marker's time is less than INGEST_SETTLE_S ago (each touch restarts it;
    a marker after an interrupted ingest is old, so it does not wait again); "cap" once MAX_INGEST_PER_DAY ingest-only
    runs have started on the local day of `tz` (until midnight, or until a refresh's own ingest answers the marker);
    else "run". An AI marker not answered yet (ai_due) wants an ingest-only run too, its own settle and cap alike,
    whatever answered the ingest marker: only that run answers it."""
    marker = marker_time(d)
    status = read_status(d)
    touched = [marker] if ingest_wanted(marker, status) else []
    touched += [ai_marker_time(d, which) for which, due in ai_due(d, status).items() if due]
    if not touched:
        return "no"
    if now - max(touched) < INGEST_SETTLE_S:
        return "wait"
    if ingest_runs_today(status, now, tz) >= MAX_INGEST_PER_DAY:
        return "cap"
    return "run"


def ingest_open(status: dict) -> bool:
    """An ingest has recorded its start and no end after it: one is running, or the run that began it died before it
    could record one."""
    started = ingest_started_at(status)
    if started is None:
        return False
    finished = _epoch(status.get("ingest_finished_at"))
    return finished is None or finished < started


def ingest_only_in_flight(status: dict) -> bool:
    """status.json shows an ingest-only run in flight rather than a refresh: its `ingest_only` mark is true. Both hold
    the refresh lock. The refresher writes the mark true with the run's start, as its first write under the lock, and
    false as its last before it lets the lock go (a stopped refresher too), so the mark covers the whole time the run
    holds the lock, before the script's first write and after its ingest ended. Meaningful only while the lock is held;
    a mark a killed refresher left true is cleared by whatever starts a refresh (the `running` writes) and by the
    closing of a dead ingest."""
    return status.get("ingest_only") is True


def ingest_start_fields(status: dict, now: float, tz: str, *, only: bool) -> dict:
    """The ingest_* fields an ingest writes as it starts at `now`: its start, its end and exit code cleared (so the
    previous ingest's outcome does not read as this one's). An ingest-only run (`only`) is also counted in ingest_runs,
    which keeps the start times of `now`'s local day only, and holds `now` once however often it is written (the
    refresher claims the run, then refresh.sh writes the same start)."""
    fields = {"ingest_started_at": now, "ingest_started_at_iso": utc_iso(now), "ingest_finished_at": None,
              "ingest_finished_at_iso": None, "ingest_rc": None}
    if only:
        today = local_day(now, tz)
        runs = status.get("ingest_runs")
        kept = {e for t in (runs if isinstance(runs, list) else ())
                if (e := _epoch(t)) is not None and local_day(e, tz) == today}
        fields["ingest_runs"] = sorted(kept | {now})
    return fields


def ingest_done_fields(now: float, rc: int | None) -> dict:
    """The ingest_* fields an ingest writes as it ends at `now` with exit code `rc`."""
    return {"ingest_finished_at": now, "ingest_finished_at_iso": utc_iso(now), "ingest_rc": rc}


# ------------------------------------------------------------------ decision

@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str            # ok | already_running | worker_unavailable | daily_cap | min_gap
    retry_after_s: int
    used_today: int        # BEFORE this request; the caller adds 1 when it accepts
    limit_per_day: int
    nonce_in_flight: str | None = None
    last_refresh_at: float | None = None


def decide(*, now: float, tz: str, manual_runs: list[float],
           last_run_at: float | None, running: bool,
           nonce_in_flight: str | None, queued_age_s: float | None,
           min_gap_s: int = MIN_GAP_S,
           max_per_day: int = MAX_MANUAL_PER_DAY,
           worker_busy: bool = False) -> Decision:
    """Pure: may this caller enqueue a manual refresh right now?

    `running` is a refresh in flight; `worker_busy` an ingest-only run in
    flight, which is no refresh (it spends no feed request and moves no gap),
    but keeps the worker from claiming a request until it ends, so a request
    waiting on it is never taken for a dead worker.

    Kept free of I/O so the whole policy is unit-testable with an injected
    clock and timezone (tests/test_refresh_state.py)."""
    today = local_day(now, tz)
    used = sum(1 for t in manual_runs if local_day(t, tz) == today)
    # The gap is measured against the last refresh of ANY kind, not just manual
    # ones: a manual pull five minutes after the 09:00 cron run fetches nothing
    # new and still spends one of SimpleFIN's 24 daily requests.
    candidates = [t for t in (last_run_at, max(manual_runs, default=None)) if t is not None]
    last = max(candidates) if candidates else None

    def refuse(reason: str, retry: int) -> Decision:
        return Decision(False, reason, retry, used, max_per_day, nonce_in_flight, last)

    # Checked FIRST: the refresher blocks for the whole refresh.sh run, so it
    # legitimately cannot claim a new request meanwhile — a request queued
    # during a run is not evidence of a dead worker.
    if running:
        return refuse("already_running", 5)
    if queued_age_s is not None:
        if queued_age_s > WORKER_STALE_S and not worker_busy:
            return refuse("worker_unavailable", 0)
        return refuse("already_running", 5)
    if used >= max_per_day:
        return refuse("daily_cap", seconds_to_local_midnight(now, tz))
    if last is not None and now - last < min_gap_s:
        return refuse("min_gap", max(1, math.ceil(min_gap_s - (now - last))))
    return Decision(True, "ok", 0, used, max_per_day, None, last)


# ----------------------------------------------------------------------- cli

def _rc(text: str) -> int | None:
    return int(text) if text.strip().lstrip("-").isdigit() else None


LOCK_WAIT_RC = 75             # hold-lock's exit when the refresh lock stayed held past --wait (as `flock -E 75` did)


def _hold_lock(lock: Path, liveness: Path | None, wait_s: float, command: list[str]) -> int:
    """`refresh_state.py hold-lock --lock L [--liveness M] [--wait S] -- CMD...`: take the refresh lock L, waiting up
    to S seconds (then exit LOCK_WAIT_RC), and with it the run's liveness M (hold_liveness), then become CMD with both
    held: the descriptors stay open in CMD and in what it starts, so the locks last as long as the run, and the kernel
    lets them go when it ends however it ends. Neither file is opened through a link (exit 1 when one is)."""
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = safe_files.open_lock(lock)
    except OSError as e:
        print(f"refresh_state: cannot take the refresh lock {lock}: {e.strerror or e}", file=sys.stderr, flush=True)
        return 1
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                return LOCK_WAIT_RC
            time.sleep(0.5)
    held = [fd]
    if liveness is not None and os.path.abspath(liveness) != os.path.abspath(lock):
        hold_liveness([liveness], held)
    for each in held:
        os.set_inheritable(each, True)
    os.execvp(command[0], command)
    return 1   # not reached: execvp replaces this process or raises


def _main(argv: list[str] | None = None) -> int:
    """`refresh_state.py write-status ...` and `hold-lock ...` — the entry points
    refresh.sh uses. Bash must not hand-roll this JSON: the failed-step list and
    the re-auth warnings carry spaces, quotes and emoji; and flock(1) opens its
    file through a link.

    `--state` writes a refresh's own fields; `--ingest start|done` writes only
    the ingest_* fields (ingest_start_fields / ingest_done_fields), leaving the
    last refresh's state, trigger, nonce, rc, failed steps and warnings as the
    dashboard shows them.

    `--state done --fetched ok|failed|none` also says what the run's fetch was:
    `none`, no bank feed is set up, so the run read the files alone
    (files_only, and no feed error or connection to sign in to); `ok` or
    `failed`, the feed's errors from the run's output (`--feed-lines`, a file:
    feed_report) replace the last run's, and after `ok` so do the connections
    to sign in to again (reauth_needed). A failed fetch learned nothing of the
    connections, so the last known ones stay. The zone of `--ingest start`'s
    local day is `--tz`, else the mailbox's (household_zone)."""
    ap = argparse.ArgumentParser(description="refresh state helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hold-lock")
    h.add_argument("--lock", required=True)
    h.add_argument("--liveness", default="")
    h.add_argument("--wait", type=float, default=600.0)
    h.add_argument("command", nargs=argparse.REMAINDER)
    w = sub.add_parser("write-status")
    w.add_argument("--dir", required=True)
    which = w.add_mutually_exclusive_group(required=True)
    which.add_argument("--state", choices=["running", "done", "interrupted"])
    which.add_argument("--ingest", choices=["start", "done"])
    w.add_argument("--trigger", default="schedule")
    w.add_argument("--nonce", default="")
    w.add_argument("--rc", default="")
    w.add_argument("--failed", default="")       # space-separated step labels
    w.add_argument("--warnings", default="")     # '|'-joined, as refresh.sh builds it
    # --state done only: what the fetch was, and the file holding the run's output
    w.add_argument("--fetched", choices=["ok", "failed", "none"], default=None)
    w.add_argument("--feed-lines", default="")
    # --ingest start only: an ingest-only run (counted in ingest_runs), the start the refresher already recorded
    # for it (epoch seconds; empty = now), and the zone whose local day ingest_runs keeps (empty: the mailbox's)
    w.add_argument("--only", action="store_true")
    w.add_argument("--at", default="")
    w.add_argument("--tz", default="")
    args = ap.parse_args(argv)

    if args.cmd == "hold-lock":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            ap.error("hold-lock needs a command after --")
        return _hold_lock(Path(args.lock), Path(args.liveness) if args.liveness else None, args.wait, command)
    d = Path(args.dir)
    now = time.time()
    if args.ingest == "start":
        try:
            at = float(args.at) if args.at.strip() else now
        except ValueError:
            at = now
        if not math.isfinite(at):
            at = now
        tz = args.tz.strip() or household_zone(d)
        write_status(d, **ingest_start_fields(read_status(d), at, tz, only=args.only))
    elif args.ingest == "done":
        write_status(d, **ingest_done_fields(now, _rc(args.rc)))
    elif args.state == "running":
        # explicit nulls: without them the previous run's outcome would survive
        # the merge and this run would look like it had already failed; a refresh
        # starting is no ingest-only run, whatever mark a killed refresher left
        write_status(d, state="running", trigger=args.trigger,
                     request_nonce=args.nonce or None, started_at=now,
                     started_at_iso=utc_iso(now), finished_at=None, rc=None,
                     steps_failed=[], warnings=[], ingest_only=False)
    else:
        feed: dict = {}
        if args.state == "done" and args.fetched == "none":
            feed = {"files_only": True, "feed_errors": [], "reauth_needed": []}
        elif args.state == "done" and args.fetched is not None:
            errors, reauth = feed_report(_feed_text(args.feed_lines))
            feed = {"files_only": False, "feed_errors": errors,
                    **({"reauth_needed": reauth} if args.fetched == "ok" else {})}
        write_status(d, state=args.state, trigger=args.trigger,
                     request_nonce=args.nonce or None, finished_at=now,
                     finished_at_iso=utc_iso(now), rc=_rc(args.rc),
                     steps_failed=args.failed.split(),
                     warnings=[w for w in args.warnings.split("|") if w], **feed)
    return 0


if __name__ == "__main__":
    sys.exit(_main())

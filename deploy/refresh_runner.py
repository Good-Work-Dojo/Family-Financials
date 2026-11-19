# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The `refresher` service: turns a queued request into a real data refresh.

Headless on purpose. It polls request.json in the refresh directory every couple of seconds
and runs deploy/refresh.sh when it finds an unclaimed nonce — it opens no port
and Caddy cannot reach it. That is the whole point of the design: this process
holds the SimpleFIN credential and every ETL write mount, so it must never be
the thing terminating user-controlled HTTP. The browser talks to the store,
which can only drop a small JSON file in a shared directory.

With no request to run, it also runs an ingest without a fetch (`refresh.sh
ingest`) when the store's ingest marker asks for one and has settled
(refresh_state.ingest_due), under the same lock: a refresh request always
comes first, and a refresh in flight answers the marker with its own ingest.
An AI marker (the household switching the AI step on, or asking for its
answers to be forgotten) is answered by such a run only, which first does what
the marker asks.

Each pass of its loop touches a heartbeat file in the refresh directory
(Worker.beat), whose age the container's health check reads.

Why a dedicated process rather than a `* * * * *` supercronic job: cron's
one-minute granularity would put up to 60s of dead air in front of a ~30s
refresh (or force a sleep-poll hack inside the cron slot), and it would bury
the scheduler's log — the one `refresh.sh`'s own alerts tell you to read —
under 1,440 job lines a day.

Stdlib only, as scenario_store.py: it imports nothing of the ETL, which only
the refresh.sh it starts runs.

Where its directories are: the argument, else the location's own variable, else
its place under FF_HOME, else its default place (refresh_state.locate, the one
table of the deploy side; the ETL resolves the same variables):

  argument        variable                 under FF_HOME                neither
  --dir           FF_STATE_REFRESH_DIR     state/refresh/               /app/var/refresh
  --engine-dir    FF_STATE_ENGINE_DIR      state/engine/                the --dir directory
  --snapshots     FF_STORE_DIR (a root:    store/simplefin/snapshots/   /app/simplefin/snapshots
                  its simplefin/snapshots/)
  --refresh-cmd   -                        -                            refresh.sh beside this file

The refresh directory is the mailbox the store writes too (the request, the
status, the ingest marker, the run's liveness lock), read and written only
through refresh_state, which never follows a link there; the engine's directory
holds what only the workers touch (the refresh lock, the heartbeat, refresh.sh's
streaks and log). refresh.sh finds both by the same order, so the run it starts
is told this worker's (FF_STATE_REFRESH_DIR, FF_STATE_ENGINE_DIR) whenever it
would not find them by itself (script_env).
"""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import refresh_state as rs
import safe_files

POLL_S = 2.0
# A refresh is 22-44s normally; the worst case is ~5 min (three 90s SimpleFIN
# attempts plus backoff). 15 min means "wedged", not "slow" — and it bounds how
# long a hung manual run can hold the lock away from the 3x/day schedule.
RUN_TIMEOUT_S = 900
# What _run returns for a run it killed past RUN_TIMEOUT_S; an ingest that run began is recorded with it
# (rs.INGEST_TIMED_OUT_RC), whatever the script's trap wrote, so a hung ingest is never retried.
TIMEOUT_RC = rs.INGEST_TIMED_OUT_RC
# How long a run is given to end after the refresher, itself being stopped, signals it: docker's default stop grace is
# 10 s, so the script's TERM trap has time to record its end before the group is killed and the refresher exits.
STOP_GRACE_S = 8
# the script the image installs beside this file
DEFAULT_REFRESH_CMD = str(Path(__file__).resolve().parent / "refresh.sh")
# A file in the engine's directory that every pass touches, at most every HEARTBEAT_S, before it runs anything: the
# container's health check reads its age, so a worker that stopped looping shows unhealthy. A pass can hold the loop
# for one run (RUN_TIMEOUT_S, then the kill's grace), so the check allows that long plus HEARTBEAT_S.
HEARTBEAT_FILE = "heartbeat"
HEARTBEAT_S = 30.0
# a request's nonce, exactly as the store writes it (refresh_state.new_nonce: 8 random bytes in hex)
NONCE = re.compile(r"[0-9a-f]{16}")


def log(msg: str) -> None:
    print(f"refresh_runner: {msg}", file=sys.stderr, flush=True)


def _killpg(proc: subprocess.Popen, sig: int) -> None:
    """Signal the child's whole process group, tolerating it having already
    exited (getpgid raises once the pid is reaped)."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(proc.pid), sig)


def script_env(d: Path, engine: Path | None = None) -> dict[str, str]:
    """What refresh.sh is told of the refresh directory `d` and the engine's directory `engine`: nothing of either the
    script, resolving with no argument in this environment, finds itself (the default places among them, where it
    keeps its streak and log in the feed store); else `d` as FF_STATE_REFRESH_DIR and `engine` as
    FF_STATE_ENGINE_DIR, so the worker and the run it starts share one of each."""
    env = {} if rs.locate("refresh") == d else {"FF_STATE_REFRESH_DIR": str(d)}
    if engine is not None and rs.engine_dir(d) != engine:
        env["FF_STATE_ENGINE_DIR"] = str(engine)
    return env


def locks(d: Path, engine: Path | None = None) -> tuple[Path, Path]:
    """The refresh lock, in the engine's directory (the mailbox when none is given), and the run's liveness in the
    mailbox, which the store probes (rs.try_lock takes the second only when it is another file)."""
    return (engine or d) / rs.LOCK_FILE, d / rs.LOCK_FILE


def spawn(cmd: str, nonce: str, timeout_s: int | None = None, *, d: Path | None = None,
          engine: Path | None = None) -> int:
    """Run refresh.sh to completion, tagged as a manual run (RUN_TIMEOUT_S unless `timeout_s` says otherwise), told
    the refresh directory `d` and the engine's directory (script_env) when `d` is given."""
    return _run([cmd], {"FF_TRIGGER": "manual", "FF_NONCE": nonce, **(script_env(d, engine) if d else {})},
                f"refresh {nonce}", RUN_TIMEOUT_S if timeout_s is None else timeout_s)


def _stop_group(proc: subprocess.Popen, grace_s: float, after_kill_s: float) -> None:
    """TERM the child's process group, give it `grace_s` to end (refresh.sh's trap records the run's end), then KILL
    the group if it is still alive and wait up to `after_kill_s` for it to be reaped. Whatever cuts the grace short (an
    exception a signal handler raises in the wait), the group is KILLed before it propagates: a child that ignores TERM
    is never left running with no lock."""
    _killpg(proc, signal.SIGTERM)
    ended = False
    try:
        proc.wait(timeout=grace_s)
        ended = True
    except subprocess.TimeoutExpired:
        pass
    finally:
        if not ended:
            _killpg(proc, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=after_kill_s)


@contextlib.contextmanager
def _stop_signals_ignored():
    """SIGTERM and SIGINT are ignored inside, then given back their handlers: once the refresher is stopping, a second
    `docker stop` or interrupt must not cut the run's grace short. Only the main thread can set a handler; anywhere
    else nothing changes (and _stop_group still KILLs the group if the wait is cut short)."""
    saved: dict[int, object] = {}
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(ValueError):
                saved[sig] = signal.signal(sig, signal.SIG_IGN)
        yield
    finally:
        for sig, handler in saved.items():
            if handler is not None:
                signal.signal(sig, handler)


def _exit_status(rc: int) -> int:
    """A run's exit status as the shell reports it: `Popen.wait` gives -N for a death by signal N, which reads 128 + N
    (TERM 143, KILL 137, INT 130), so a negative status is only ever one the refresher writes itself (TIMEOUT_RC,
    rs.INGEST_INTERRUPTED_RC)."""
    return 128 - rc if rc < 0 else rc


def _run(argv: list[str], env: dict[str, str], label: str, timeout_s: float) -> int:
    """Run refresh.sh (in either mode) to completion and return its exit status (TIMEOUT_RC on a timeout; 128 + N for a
    death by signal N, as the shell reports one).

    FF_LOCK_HELD tells refresh.sh to skip its own flock re-exec: we are already
    holding that lock for the whole run (see claim_and_run), and Python fds are
    non-inheritable, so the child could not have taken it anyway.

    start_new_session so the whole tree gets its own process group: on timeout
    refresh.sh's grandchildren (`python -m etl …`) have to die too, and
    killing just the shell would orphan a live SimpleFIN fetch. For the same
    reason, when the refresher itself is stopped mid-run (`docker stop`, `docker
    compose up -d`: its SIGTERM handler raises SystemExit; or an interrupt), the
    group is signalled before the refresher goes: the script's TERM trap records
    the run's end, the group is killed if it outlives STOP_GRACE_S, and the stop
    goes on; a second stop signal meanwhile is ignored, so the grace and the kill
    always happen. Otherwise the child, in a session of its own, would outlive the
    refresher and be killed by the container's teardown with no end recorded."""
    proc = subprocess.Popen(argv, env={**os.environ, **env, "FF_LOCK_HELD": "1"}, start_new_session=True)
    try:
        return _exit_status(proc.wait(timeout=timeout_s))
    except subprocess.TimeoutExpired:
        log(f"{label} exceeded {timeout_s}s — killing the process group")
        _stop_group(proc, 30, 10)
        return TIMEOUT_RC
    except (SystemExit, KeyboardInterrupt):
        with _stop_signals_ignored():
            log(f"stopping: ending {label} first")
            _stop_group(proc, STOP_GRACE_S, 1)
        raise


def claim_and_run(d: Path, *, cmd: str, snapshots: Path,
                  now: float | None = None, engine: Path | None = None) -> str | None:
    """Handle at most one queued request, under the refresh lock in `engine` (the mailbox `d` when none is given).
    Returns the nonce it acted on."""
    req = rs.read_request(d)
    if req is None:
        return None
    nonce = req.get("nonce")
    if not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
        # exactly what rs.new_nonce writes: anything else (a line break forging a log line, a leading - read as an
        # option where the nonce reaches an argument list) is never claimed, logged or passed on
        return None
    if rs.read_status(d).get("request_nonce") == nonce:
        return None                     # already handled — cheap check, no lock

    # Everything below happens while WE hold the refresh lock, so status.json
    # has exactly one writer for the whole run and the coalescing check reads a
    # settled state. See rs.try_lock for why that ordering matters.
    with rs.try_lock(*locks(d, engine)) as held:
        if not held:
            # A refresh is already running — almost always the 3x/day cron one.
            # Leave the request queued and come back in POLL_S: /api/refresh
            # reports `running` off this same lock, so the browser is already
            # watching the right thing.
            return None

        # Re-read under the lock: the run we just waited out may have satisfied
        # this request, or another pass may have claimed it.
        status = rs.read_status(d)
        if status.get("request_nonce") == nonce:
            return None
        now = time.time() if now is None else now

        # Coalesce: a run that STARTED after this request was made has already
        # fetched what it asked for. Claim the nonce so the browser sees its
        # refresh complete, but don't spend a second SimpleFIN request on it.
        if rs.already_satisfied(req, status):
            log(f"request {nonce} already satisfied by the run at "
                f"{rs.utc_iso(rs.last_started_at(status) or now)} — coalescing")
            rs.write_status(d, request_nonce=nonce)
            return nonce

        # Ground-truth quota backstop, counted from the snapshot files
        # themselves. No store bug, hand-edited request file or wiped
        # ledger can desync it — which is what makes it worth having on top of
        # the rate limiter.
        spent = rs.count_recent_fetches(snapshots, now)
        if spent >= rs.HARD_24H_FETCH_CEILING:
            log(f"refusing request {nonce}: {spent} SimpleFIN fetches in the last 24h "
                f"(ceiling {rs.HARD_24H_FETCH_CEILING})")
            rs.write_status(d, state="done", trigger="manual", request_nonce=nonce,
                            started_at=now, started_at_iso=rs.utc_iso(now),
                            finished_at=now, finished_at_iso=rs.utc_iso(now),
                            rc=75, steps_failed=["quota-backstop"], warnings=[])
            return nonce

        # CLAIM FIRST, RUN SECOND. If refresh.sh dies or this container is
        # killed mid-run, the nonce stays consumed — otherwise a crashing
        # refresh replays the same request every two seconds and drains the
        # daily quota in minutes.
        # (a refresh starting is no ingest-only run, whatever mark a killed
        # refresher left: see rs.ingest_only_in_flight)
        rs.write_status(d, state="running", trigger="manual", request_nonce=nonce,
                        started_at=now, started_at_iso=rs.utc_iso(now),
                        finished_at=None, finished_at_iso=None, rc=None,
                        steps_failed=[], warnings=[], ingest_only=False)
        log(f"running refresh for {nonce}")
        code = spawn(cmd, nonce, d=d, engine=engine)
        log(f"refresh {nonce} exited {code}")

        # refresh.sh writes its own terminal status. If it died before it could
        # (SIGKILL, or it never started), leave a terminal one here so the
        # button doesn't spin forever waiting on a run that is gone; an ingest
        # step it had begun gets its end too, so nothing follows that ingest
        # for ever. An ingest step this run began and the timeout cut short is
        # recorded as timed out whatever the script's trap wrote (143 would ask
        # for it again: rs.ingest_wanted), so a hung ingest is not retried.
        status = rs.read_status(d)
        began_here = (rs.ingest_started_at(status) or 0) >= now
        fin = time.time()
        if status.get("state") == "running":
            rs.write_status(d, state="interrupted", finished_at=fin,
                            finished_at_iso=rs.utc_iso(fin), rc=code)
            if rs.ingest_open(status) and began_here:
                rs.write_status(d, **rs.ingest_done_fields(fin, code))
        if code == TIMEOUT_RC and began_here:
            rs.write_status(d, **rs.ingest_done_fields(fin, TIMEOUT_RC))
    return nonce


def ingest_if_due(d: Path, *, cmd: str, tz: str, now: float | None = None, engine: Path | None = None) -> str:
    """Run an ingest without a fetch (`cmd ingest`) when the marker asks for one (rs.ingest_due says "run"). Returns
    what it found: ingest_due's "no", "wait" or "cap" (nothing run), "busy" when a refresh holds the lock (nothing run:
    its own ingest answers the marker if it starts after the touch, and the next poll sees whatever is left), or "ran".

    Under the lock, as a refresh request: ingest_due is asked again there (the run just waited out may have answered
    it), the run is claimed before it starts (its start written and counted toward MAX_INGEST_PER_DAY, so a script
    that dies at once cannot be re-run every POLL_S), and refresh.sh is told that start (FF_INGEST_STARTED_AT) so it
    counts the run once. The claim also marks the run `ingest_only` (rs.ingest_only_in_flight), and the last write
    before the lock is let go clears the mark, however the run ended (the refresher being stopped too). The last
    refresh's own status fields are never written here. An AI marker not answered yet (rs.ai_due) is answered by this
    run: the claim records it (`ai_handled_at`) and refresh.sh is told to forget the AI step's answers (FF_AI_RESET)
    and to run the AI step (FF_AI_RUN), each before its ingest; each variable is empty otherwise."""
    t = time.time() if now is None else now
    due = rs.ingest_due(d, t, tz)
    if due != "run":
        return due
    with rs.try_lock(*locks(d, engine)) as held:
        if not held:
            return "busy"
        due = rs.ingest_due(d, t, tz)
        if due != "run":
            return due
        status = rs.read_status(d)
        ai = rs.ai_due(d, status)
        # the AI markers this run answers are claimed with it (ai_handled_at), as the run itself is
        rs.write_status(d, **rs.ingest_start_fields(status, t, tz, only=True), ingest_only=True,
                        **({"ai_handled_at": t} if any(ai.values()) else {}))
        log("running an ingest without a fetch" + "".join(f", {AI_NOTES[w]} first" for w in ("reset", "run") if ai[w]))
        code: int | None = None
        try:
            code = _run([cmd, "ingest"], {"FF_TRIGGER": "ingest", "FF_NONCE": "", "FF_INGEST_STARTED_AT": repr(t),
                                          "FF_AI_RESET": "1" if ai["reset"] else "",
                                          "FF_AI_RUN": "1" if ai["run"] else "",
                                          **script_env(d, engine)}, "ingest", RUN_TIMEOUT_S)
            log(f"ingest exited {code}")
        finally:
            # refresh.sh records the ingest's end itself; if it died before it could, record it here, so a dashboard
            # following the ingest sees it end. A timed-out run is recorded as such whatever its trap wrote (a hung
            # ingest is not retried). Stopped mid-run (no code), the script's trap has recorded the end, or the next
            # pass finds the ingest dead and closes it (close_dead_ingest).
            end: dict = {}
            if code == TIMEOUT_RC:
                end = rs.ingest_done_fields(time.time(), TIMEOUT_RC)
            elif code is not None and rs.ingest_open(rs.read_status(d)):
                end = rs.ingest_done_fields(time.time(), code)
            rs.write_status(d, **end, ingest_only=False)
    return "ran"


def close_dead_ingest(d: Path, engine: Path | None = None) -> bool:
    """Record the end of an ingest that recorded a start and no end while no run holds the refresh lock: its run is
    gone (killed before the script's trap could record an end, or the container went down with it), so the ingest is
    closed with rs.INGEST_INTERRUPTED_RC, which asks for the marker's ingest again (rs.ingest_wanted), and a stale
    `ingest_only` mark is cleared with it. A cheap status read with no lock while nothing is open; nothing is written
    while someone else holds the lock (the ingest may be theirs, in flight). True when it closed one."""
    if not rs.ingest_open(rs.read_status(d)):
        return False
    with rs.try_lock(*locks(d, engine)) as held:
        if not held:
            return False
        status = rs.read_status(d)
        if not rs.ingest_open(status):
            return False
        started = rs.ingest_started_at(status)
        log(f"the ingest started at {rs.utc_iso(started or 0)} has no end and no run holds the lock: "
            f"recording it as interrupted ({rs.INGEST_INTERRUPTED_RC})")
        rs.write_status(d, **rs.ingest_done_fields(max(time.time(), started or 0), rs.INGEST_INTERRUPTED_RC),
                        ingest_only=False)
    return True


# What an ingest-only run that answers an AI marker does before its ingest, as its log line says it.
AI_NOTES = {"reset": "forgetting the AI step's stored answers", "run": "running the AI step"}

# What a wanted ingest that waits says, once when the wait starts (the loop polls every POLL_S).
INGEST_NOTES = {
    "wait": f"an ingest is wanted: waiting until the marker has been still for {rs.INGEST_SETTLE_S}s",
    "cap": (f"an ingest is wanted, but {rs.MAX_INGEST_PER_DAY} ingest-only runs have started today: waiting for "
            "local midnight or the next refresh's own ingest"),
}


class Worker:
    """One pass of the refresher's loop at a time: the heartbeat touched (beat), a dead ingest closed
    (close_dead_ingest), then a queued refresh request (claim_and_run), and only with none acted on, a due ingest
    (ingest_if_due). It remembers what the last pass found about the ingest, so a marker that waits is logged once when
    the wait starts, not on every pass. The ingest-only cap's local day is drawn in `tz` when one is given, else in the
    household's zone, read from the mailbox at each pass (rs.household_zone: the schedule the scheduler wrote from
    household.yaml), so it is the day the store counts too."""

    def __init__(self, d: Path, *, cmd: str, snapshots: Path, tz: str | None = None, engine: Path | None = None):
        self.d, self.cmd, self.snapshots, self.tz = d, cmd, snapshots, tz
        self.engine = engine or d
        self.ingest_seen: str | None = None
        self.beaten: float | None = None

    def beat(self, now: float) -> None:
        """Touch HEARTBEAT_FILE in the engine's directory (never through a link) unless the last touch is under
        HEARTBEAT_S old. A touch that fails is left to the health check, which then reads an old file."""
        if self.beaten is not None and now - self.beaten < HEARTBEAT_S:
            return
        with contextlib.suppress(OSError):
            safe_files.touch(self.engine / HEARTBEAT_FILE, time.time())
        self.beaten = now

    def poll(self, now: float | None = None) -> None:
        self.beat(time.time() if now is None else now)
        close_dead_ingest(self.d, self.engine)
        if claim_and_run(self.d, cmd=self.cmd, snapshots=self.snapshots, now=now, engine=self.engine) is not None:
            return
        seen = ingest_if_due(self.d, cmd=self.cmd, tz=self.tz or rs.household_zone(self.d), now=now, engine=self.engine)
        if seen != self.ingest_seen and seen in INGEST_NOTES:
            log(INGEST_NOTES[seen])
        self.ingest_seen = seen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Family Financials refresh worker")
    ap.add_argument("--dir", default=None,
                    help="the refresh directory; default: FF_STATE_REFRESH_DIR, else FF_HOME's "
                         "state/refresh/, else /app/var/refresh")
    ap.add_argument("--engine-dir", default=None,
                    help="the workers' own directory (the refresh lock, the heartbeat); default: "
                         "FF_STATE_ENGINE_DIR, else FF_HOME's state/engine/, else the refresh directory")
    ap.add_argument("--snapshots", default=None,
                    help="the feed's snapshots; default: FF_STORE_DIR's simplefin/snapshots/, else "
                         "FF_HOME's store/simplefin/snapshots/, else /app/simplefin/snapshots")
    ap.add_argument("--refresh-cmd", default=DEFAULT_REFRESH_CMD,
                    help="the script to run (default: refresh.sh beside this file); point it at a "
                         "stub to exercise the plumbing without spending a SimpleFIN request")
    ap.add_argument("--once", action="store_true",
                    help="handle at most one request (with none, a due ingest), then exit")
    args = ap.parse_args(argv)

    # absolute, so it compares with what refresh.sh would find (script_env); this process never changes directory
    d = Path(os.path.abspath(rs.locate("refresh", args.dir)))
    found = rs.locate("refresh")
    if d != found:
        # The runs this worker starts are told `d`; the scheduled ones (cron runs refresh.sh with the environment
        # alone) find `found`: two locks and two failure streaks, unless the schedule is given `d` too.
        log(f"WARNING: --dir names {d}, but the environment alone gives {found}: this worker uses {d}; give the "
            "scheduled refresh the same directory (FF_STATE_REFRESH_DIR)")
    engine = Path(os.path.abspath(rs.engine_dir(d, args.engine_dir)))
    for each in (d, engine):
        each.mkdir(parents=True, exist_ok=True)
        if not os.access(each, os.W_OK):
            # A bind source docker created itself is root-owned, and then every
            # write fails while reads keep working — fail loudly, not subtly.
            log(f"FATAL: {each} is not writable by uid {os.getuid()}")
            return 1
    snapshots = rs.locate("snapshots", args.snapshots)
    # MAX_INGEST_PER_DAY's local days are the household's, from the mailbox's schedule at each pass, as the store's
    worker = Worker(d, cmd=args.refresh_cmd, snapshots=snapshots, engine=engine)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))   # fast `docker stop`
    if args.once:
        worker.poll()
        return 0

    log(f"watching {d} every {POLL_S}s -> {args.refresh_cmd} (the household's zone, read at each pass: {rs.household_zone(d)})")
    while True:
        try:
            worker.poll()
        except Exception as e:   # noqa: BLE001 — one bad request file must not
            log(f"poll failed: {e!r}")   # kill the loop; manual refresh would
        time.sleep(POLL_S)               # then be dead until someone noticed


if __name__ == "__main__":
    sys.exit(main())

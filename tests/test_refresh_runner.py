# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""deploy/refresh_runner.py — the worker that turns a queued request into a run.

Every test drives it with `--refresh-cmd` pointed at a stub script, or runs the
real refresh.sh with a stub `python` (the ETL's `python -m etl`) first on PATH,
so nothing here spends a SimpleFIN request.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import select
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import refresh_runner as runner  # tests/conftest.py puts deploy/ on sys.path
import refresh_state as rs

NOW = 1_786_000_000.0
N1 = "00000000000000a1"         # a request's nonce, of the shape the store writes (refresh_state.new_nonce)


def hold_lock(path: Path) -> subprocess.Popen:
    """Stand in for a scheduled refresh that is already in flight."""
    code = ("import fcntl,sys;f=open(sys.argv[1],'w');fcntl.flock(f,fcntl.LOCK_EX);"
            "sys.stdout.write('x');sys.stdout.flush();sys.stdin.read()")
    p = subprocess.Popen([sys.executable, "-c", code, str(path)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert p.stdout.read(1) == b"x"
    return p


def stub(tmp_path: Path, body: str) -> str:
    """A stand-in for refresh.sh."""
    p = tmp_path / "stub.sh"
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def run(d: Path, cmd: str, *, snapshots: Path | None = None, now=NOW):
    return runner.claim_and_run(d, cmd=cmd, snapshots=snapshots or (d / "snapshots"),
                                now=now)


def test_no_request_is_a_no_op(tmp_path):
    assert run(tmp_path, stub(tmp_path, "exit 0")) is None
    assert rs.read_status(tmp_path) == {}


def test_a_queued_request_is_claimed_and_run(tmp_path):
    marker = tmp_path / "ran"
    rs.write_request(tmp_path, nonce=N1, now=NOW - 5)
    assert run(tmp_path, stub(tmp_path, f"touch {marker}")) == N1
    assert marker.exists()
    assert rs.read_status(tmp_path)["request_nonce"] == N1


@pytest.mark.parametrize("nonce", ["n1", "00000000000000A1", "0000000000000a1", "00000000000000a1\n",
                                   "-0000000000000a1", "00000000000000a1\nFORGED LINE", "0" * 17, ""])
def test_a_nonce_of_another_shape_than_the_stores_is_never_claimed_or_run(tmp_path, nonce):
    """The store writes 16 lower-case hex digits (refresh_state.new_nonce); a request holding anything else (a line
    break that would forge a log line, a leading - read as an option downstream) is left alone: no run, no status."""
    marker = tmp_path / "ran"
    rs.write_request(tmp_path, nonce=nonce, now=NOW - 5)
    assert run(tmp_path, stub(tmp_path, f"touch {marker}")) is None
    assert not marker.exists()
    assert rs.read_status(tmp_path) == {}


def test_the_nonce_is_claimed_before_the_run_starts(tmp_path):
    """If refresh.sh dies mid-run — or the container is killed — the nonce has
    to stay consumed, or the crashing request replays every two seconds and
    drains the daily SimpleFIN quota in minutes."""
    seen = tmp_path / "status-as-seen-by-the-child.json"
    rs.write_request(tmp_path, nonce=N1, now=NOW - 5)
    run(tmp_path, stub(tmp_path, f"cp {tmp_path / rs.STATUS_FILE} {seen}; exit 2"))
    claimed = json.loads(seen.read_text())
    assert (claimed["state"], claimed["request_nonce"]) == ("running", N1)
    assert claimed["trigger"] == "manual"


def test_an_already_claimed_request_is_not_run_again(tmp_path):
    marker = tmp_path / "ran"
    rs.write_request(tmp_path, nonce=N1, now=NOW - 5)
    rs.write_status(tmp_path, state="done", request_nonce=N1, started_at=NOW)
    assert run(tmp_path, stub(tmp_path, f"touch {marker}")) is None
    assert not marker.exists()


def test_a_request_covered_by_a_finished_run_coalesces(tmp_path):
    """A click landing during the 09:00 cron run must not wait out the lock and
    then immediately re-fetch the data that run already pulled."""
    marker = tmp_path / "ran"
    rs.write_request(tmp_path, nonce=N1, now=NOW - 60)
    rs.write_status(tmp_path, state="done", request_nonce="scheduled-run",
                    started_at=NOW - 30, rc=0)
    assert run(tmp_path, stub(tmp_path, f"touch {marker}")) == N1
    assert not marker.exists()
    assert rs.read_status(tmp_path)["request_nonce"] == N1   # UI sees it finish


def test_a_run_that_started_before_the_request_does_not_coalesce(tmp_path):
    marker = tmp_path / "ran"
    rs.write_request(tmp_path, nonce=N1, now=NOW)
    rs.write_status(tmp_path, state="done", request_nonce="older",
                    started_at=NOW - 30, rc=0)
    assert run(tmp_path, stub(tmp_path, f"touch {marker}")) == N1
    assert marker.exists()


def test_the_quota_backstop_refuses_without_running(tmp_path):
    """Ground truth from the snapshot files: no rate-limiter bug upstream can
    get the household throttled by SimpleFIN."""
    marker = tmp_path / "ran"
    snaps = tmp_path / "snapshots"
    snaps.mkdir()
    for i in range(rs.HARD_24H_FETCH_CEILING):
        p = snaps / f"accounts-20260811T{i:02d}0000Z.json"
        p.write_text("{}")
        os.utime(p, (NOW - 600, NOW - 600))
    rs.write_request(tmp_path, nonce=N1, now=NOW - 5)

    assert run(tmp_path, stub(tmp_path, f"touch {marker}"), snapshots=snaps) == N1
    assert not marker.exists()
    doc = rs.read_status(tmp_path)
    assert (doc["state"], doc["rc"], doc["steps_failed"]) == ("done", 75, ["quota-backstop"])


def test_a_run_that_never_wrote_a_terminal_status_is_marked_interrupted(tmp_path):
    """SIGKILL leaves refresh.sh no chance to write `done`. Without this the
    button would spin forever waiting on a run that is already gone."""
    rs.write_request(tmp_path, nonce=N1, now=NOW - 5)
    run(tmp_path, stub(tmp_path, "kill -9 $$"))
    doc = rs.read_status(tmp_path)
    assert doc["state"] == "interrupted"
    assert doc["finished_at_iso"].endswith("Z")


def test_main_once_handles_a_single_request(tmp_path):
    marker = tmp_path / "ran"
    rs.write_request(tmp_path, nonce=N1, now=NOW - 5)
    assert runner.main(["--dir", str(tmp_path), "--once",
                        "--snapshots", str(tmp_path / "snapshots"),
                        "--refresh-cmd", stub(tmp_path, f"touch {marker}")]) == 0
    assert marker.exists()


# ------------------------------------------- the lock has to come FIRST
# Claiming a request outside the refresh lock looked fine in isolation and was
# wrong in two ways at once: a cron run already in flight would overwrite the
# claim with its own terminal status (the browser sees its refresh "finish"
# early, reloads, then watches it start again), and the coalescing check read a
# mid-run status so it never fired — the click it was meant to absorb spent a
# second SimpleFIN request anyway.

def test_a_request_is_left_queued_while_another_refresh_holds_the_lock(tmp_path):
    marker = tmp_path / "ran"
    rs.write_request(tmp_path, nonce=N1, now=NOW)
    holder = hold_lock(tmp_path / rs.LOCK_FILE)
    try:
        assert run(tmp_path, stub(tmp_path, f"touch {marker}")) is None
        assert not marker.exists()
        # nothing claimed: the request must still be there for the next poll,
        # and the status must be untouched so /api/refresh keeps reporting the
        # run that is genuinely in flight
        assert rs.read_request(tmp_path)["nonce"] == N1
        assert rs.read_status(tmp_path) == {}
    finally:
        holder.kill()
        holder.wait()
    # once the other run lets go, the very next poll picks it up
    assert run(tmp_path, stub(tmp_path, f"touch {marker}")) == N1
    assert marker.exists()


def test_a_click_during_a_cron_run_coalesces_once_that_run_finishes(tmp_path):
    """The click landed at NOW; the cron run started a second later, so by the
    time the lock frees, the data it asked for is already on disk."""
    marker = tmp_path / "ran"
    rs.write_request(tmp_path, nonce=N1, now=NOW)
    holder = hold_lock(tmp_path / rs.LOCK_FILE)
    try:
        assert run(tmp_path, stub(tmp_path, f"touch {marker}")) is None
        # the in-flight run finishes and writes its terminal status
        rs.write_status(tmp_path, state="done", trigger="schedule",
                        request_nonce=None, started_at=NOW + 1, rc=0)
    finally:
        holder.kill()
        holder.wait()
    assert run(tmp_path, stub(tmp_path, f"touch {marker}")) == N1
    assert not marker.exists()                              # no second fetch
    assert rs.read_status(tmp_path)["request_nonce"] == N1   # UI sees it land


def test_the_lock_is_held_for_the_whole_run_not_just_the_claim(tmp_path):
    """/api/refresh reports `running` off this lock, so a gap would show the
    button as idle mid-run."""
    lock = tmp_path / rs.LOCK_FILE
    probe = tmp_path / "probe"
    rs.write_request(tmp_path, nonce=N1, now=NOW - 5)
    run(tmp_path, stub(tmp_path, f"python3 -c \"import sys;sys.path.insert(0,'"
                       f"{Path(runner.__file__).parent}');import refresh_state as r,pathlib;"
                       f"open('{probe}','w').write(str(r.is_running(pathlib.Path('{lock}'))))\""))
    assert probe.read_text() == "True"
    assert rs.is_running(lock) is False        # and released when the run ended


def test_the_child_is_told_the_lock_is_already_held(tmp_path):
    """Otherwise refresh.sh's own flock re-exec would block on the lock this
    process is holding, and the run would hang until FF_LOCK_WAIT expired."""
    seen = tmp_path / "env"
    rs.write_request(tmp_path, nonce=N1, now=NOW - 5)
    run(tmp_path, stub(tmp_path, f"echo \"$FF_LOCK_HELD $FF_TRIGGER $FF_NONCE\" > {seen}"))
    assert seen.read_text().strip() == f"1 manual {N1}"


# ------------------------------------------- the ingest without a fetch
# The store touches ingest_wanted; once it has settled, and with no refresh request to run, the worker runs
# `refresh.sh ingest` under the refresh lock. The stub stands in for the script.

LAST_REFRESH = {"state": "done", "trigger": "manual", "request_nonce": "n0", "started_at": NOW - 7200,
                "finished_at": NOW - 7170, "rc": 1, "steps_failed": ["SimpleFIN-fetch"], "warnings": []}


def worker(d: Path, cmd: str, tz: str = "UTC") -> runner.Worker:
    return runner.Worker(d, cmd=cmd, snapshots=d / "snapshots", tz=tz)


def recording_stub(tmp_path: Path, extra: str = "") -> tuple[str, Path]:
    """A refresh.sh stand-in that appends one line per run: its arguments, then what it was told in its env, and
    copies the status it saw as it started."""
    calls = tmp_path / "calls"
    body = (f'echo "[$*] $FF_TRIGGER $FF_LOCK_HELD ${{FF_NONCE:-}}" >> {calls}\n'
            f"cp {tmp_path / rs.STATUS_FILE} {tmp_path / 'seen.json'} 2>/dev/null\n{extra}")
    return stub(tmp_path, body), calls


def test_the_worker_counts_ingest_only_runs_in_the_households_day(tmp_path):
    """With no zone given, each pass reads the household's from the mailbox's schedule: 48 ingest-only runs early on
    Kiritimati's Aug 11 still cap its 23:00 (09:00 UTC, when UTC's Aug 11 holds none of them), and with no schedule
    there the day is UTC's, so the same marker runs."""
    kiritimati = ZoneInfo("Pacific/Kiritimati")
    first = dt.datetime(2026, 8, 11, 0, 10, tzinfo=kiritimati).timestamp()
    now = dt.datetime(2026, 8, 11, 23, 0, tzinfo=kiritimati).timestamp()
    runs = [first + k * 60 for k in range(rs.MAX_INGEST_PER_DAY)]
    rs.write_status(tmp_path, ingest_runs=runs, ingest_started_at=runs[-1], ingest_finished_at=runs[-1] + 5,
                    ingest_rc=0)
    rs.touch_ingest_marker(tmp_path, now - 300)
    rs.write_schedule(tmp_path, rs.Schedule("Pacific/Kiritimati", (1, 9, 17), 0))
    cmd, calls = recording_stub(tmp_path, "exit 0")
    w = runner.Worker(tmp_path, cmd=cmd, snapshots=tmp_path / "snapshots")
    w.poll(now=now)
    assert w.ingest_seen == "cap" and not calls.exists()
    (tmp_path / rs.SCHEDULE_FILE).unlink()
    w.poll(now=now)
    assert w.ingest_seen == "ran" and calls.read_text().splitlines() == ["[ingest] ingest 1 "]


def test_a_due_marker_runs_an_ingest_only(tmp_path):
    """A settled marker newer than the last ingest start runs `refresh.sh ingest` with the lock held and the trigger
    `ingest`. The run is claimed before it starts (its start written and counted, its end cleared), its end is
    recorded when the script records none, the last refresh's own fields are left alone, and the marker, now older
    than that start, asks for nothing more."""
    cmd, calls = recording_stub(tmp_path, "exit 0")
    rs.write_status(tmp_path, **LAST_REFRESH, ingest_started_at=NOW - 7190, ingest_finished_at=NOW - 7180,
                    ingest_rc=0)
    rs.touch_ingest_marker(tmp_path, NOW - 120)
    w = worker(tmp_path, cmd)
    w.poll(now=NOW)
    assert calls.read_text().splitlines() == ["[ingest] ingest 1 "]
    claimed = json.loads((tmp_path / "seen.json").read_text())
    assert (claimed["ingest_started_at"], claimed["ingest_finished_at"], claimed["ingest_rc"]) == (NOW, None, None)
    assert claimed["ingest_runs"] == [NOW] and claimed["ingest_only"] is True
    doc = rs.read_status(tmp_path)
    assert {k: doc[k] for k in LAST_REFRESH} == LAST_REFRESH
    assert doc["ingest_rc"] == 0 and doc["ingest_finished_at"] > NOW and doc["ingest_finished_at_iso"].endswith("Z")
    assert doc["ingest_only"] is False
    assert rs.ingest_due(tmp_path, NOW + 2, "UTC") == "no"
    w.poll(now=NOW + 2)
    assert len(calls.read_text().splitlines()) == 1
    assert (tmp_path / rs.INGEST_MARKER).exists()                         # outgrown, never removed


def test_no_ingest_while_a_refresh_holds_the_lock(tmp_path):
    """A refresh in flight holds the lock: nothing runs and nothing is written (the marker stays, and the refresh's own
    ingest answers it when it starts after the touch); once the lock is free, a marker still unanswered runs."""
    cmd, calls = recording_stub(tmp_path, "exit 0")
    rs.write_status(tmp_path, **LAST_REFRESH)
    rs.touch_ingest_marker(tmp_path, NOW - 120)
    before = (tmp_path / rs.STATUS_FILE).read_bytes()
    holder = hold_lock(tmp_path / rs.LOCK_FILE)
    try:
        assert runner.ingest_if_due(tmp_path, cmd=cmd, tz="UTC", now=NOW) == "busy"
        assert not calls.exists()
        assert (tmp_path / rs.STATUS_FILE).read_bytes() == before
        assert rs.marker_time(tmp_path) == NOW - 118                   # the touch's time, two seconds on
    finally:
        holder.kill()
        holder.wait()
    assert runner.ingest_if_due(tmp_path, cmd=cmd, tz="UTC", now=NOW) == "ran"
    assert calls.read_text().splitlines() == ["[ingest] ingest 1 "]


def test_a_refresh_after_the_marker_satisfies_it(tmp_path):
    """A refresh request comes first and runs as it always has; its ingest step, which records its start as
    refresh.sh's does, began after the marker was touched, so no ingest-only run follows it."""
    record_ingest = (f"python3 {Path(runner.__file__).parent / 'refresh_state.py'} write-status --dir {tmp_path} "
                     "--ingest start\n"
                     f"python3 {Path(runner.__file__).parent / 'refresh_state.py'} write-status --dir {tmp_path} "
                     "--ingest done --rc 0\n")
    cmd, calls = recording_stub(tmp_path, record_ingest)
    rs.touch_ingest_marker(tmp_path, NOW - 300)
    rs.write_request(tmp_path, nonce=N1, now=NOW - 5)
    w = worker(tmp_path, cmd)
    w.poll(now=NOW)
    assert calls.read_text().splitlines() == [f"[] manual 1 {N1}"]
    assert rs.ingest_due(tmp_path, NOW + 2, "UTC") == "no"
    w.poll(now=NOW + 2)
    assert calls.read_text().splitlines() == [f"[] manual 1 {N1}"]        # no ingest-only run after it
    assert "ingest_runs" not in rs.read_status(tmp_path)


def test_a_waiting_or_capped_marker_does_nothing_and_logs_once(tmp_path, capsys):
    """A marker still settling, or one past the day's MAX_INGEST_PER_DAY ingest-only runs, runs nothing; the worker
    polls every POLL_S, so it says so once when that starts, not on every pass."""
    cmd, calls = recording_stub(tmp_path, "exit 0")
    w = worker(tmp_path, cmd)
    rs.touch_ingest_marker(tmp_path, NOW)
    for k in range(5):
        w.poll(now=NOW + k)
    assert capsys.readouterr().err.count("waiting until the marker has been still") == 1
    runs = [NOW - 3600 + k for k in range(rs.MAX_INGEST_PER_DAY)]
    rs.write_status(tmp_path, ingest_runs=runs, ingest_started_at=runs[-1], ingest_finished_at=runs[-1] + 1)
    for k in range(5):
        w.poll(now=NOW + 120 + k)
    assert capsys.readouterr().err.count(f"{rs.MAX_INGEST_PER_DAY} ingest-only runs have started today") == 1
    assert not calls.exists()


# ------------------------------------------- the AI markers
# The store touches ai_wanted (the household switched the AI step on) or ai_reset_wanted (it asked for the AI step's
# answers to be forgotten), each with the ingest marker; the ingest-only run that answers them is told what to do first.

def ai_stub(tmp_path: Path) -> tuple[str, Path]:
    return recording_stub(tmp_path, f'echo "reset=${{FF_AI_RESET-unset}} run=${{FF_AI_RUN-unset}}" >> '
                                    f'{tmp_path / "calls"}\nexit 0')


@pytest.mark.parametrize("which, told", [("run", "reset= run=1"), ("reset", "reset=1 run=")])
def test_an_ai_marker_runs_an_ingest_only_run_told_what_to_do_first(tmp_path, which, told):
    """The settled marker runs `refresh.sh ingest`, told to run the AI step (FF_AI_RUN) or to forget its answers
    (FF_AI_RESET), the other variable empty; the claim records the markers answered, so the next pass runs nothing."""
    cmd, calls = ai_stub(tmp_path)
    rs.touch_ai_marker(tmp_path, which, NOW - 120)
    w = worker(tmp_path, cmd)
    w.poll(now=NOW)
    assert calls.read_text().splitlines() == ["[ingest] ingest 1 ", told]
    assert json.loads((tmp_path / "seen.json").read_text())["ai_handled_at"] == NOW
    assert rs.ai_due(tmp_path, rs.read_status(tmp_path)) == {"run": False, "reset": False}
    w.poll(now=NOW + 2)
    assert len(calls.read_text().splitlines()) == 2


def test_an_ingest_marker_alone_tells_the_run_nothing_of_the_ai_step(tmp_path):
    cmd, calls = ai_stub(tmp_path)
    rs.touch_ingest_marker(tmp_path, NOW - 120)
    worker(tmp_path, cmd).poll(now=NOW)
    assert calls.read_text().splitlines() == ["[ingest] ingest 1 ", "reset= run="]
    assert "ai_handled_at" not in rs.read_status(tmp_path)


def test_an_ai_marker_a_refresh_left_unanswered_still_runs_after_it(tmp_path):
    """A refresh's own ingest answers the ingest marker but never an AI marker (it runs the AI step only when a key is
    stored, and never forgets the answers): the ingest-only run follows it."""
    record_ingest = (f"python3 {Path(runner.__file__).parent / 'refresh_state.py'} write-status --dir {tmp_path} "
                     "--ingest start\n"
                     f"python3 {Path(runner.__file__).parent / 'refresh_state.py'} write-status --dir {tmp_path} "
                     "--ingest done --rc 0\n")
    cmd, calls = recording_stub(tmp_path, record_ingest + f'echo "reset=$FF_AI_RESET" >> {tmp_path / "calls"}')
    rs.touch_ai_marker(tmp_path, "reset", NOW - 300)
    rs.write_request(tmp_path, nonce=N1, now=NOW - 5)
    w = worker(tmp_path, cmd)
    w.poll(now=NOW)
    assert calls.read_text().splitlines() == [f"[] manual 1 {N1}", "reset="]
    assert rs.ai_due(tmp_path, rs.read_status(tmp_path))["reset"] is True
    w.poll(now=NOW + 2)
    assert calls.read_text().splitlines()[2:] == ["[ingest] ingest 1 ", "reset=1"]


def test_an_ai_marker_waits_out_its_settle_and_the_days_cap(tmp_path):
    cmd, calls = ai_stub(tmp_path)
    rs.touch_ai_marker(tmp_path, "run", NOW - 10)
    assert runner.ingest_if_due(tmp_path, cmd=cmd, tz="UTC", now=NOW) == "wait"
    runs = [NOW - 3600 + k for k in range(rs.MAX_INGEST_PER_DAY)]
    rs.write_status(tmp_path, ingest_runs=runs, ingest_started_at=runs[-1], ingest_finished_at=runs[-1] + 1,
                    ingest_rc=0)
    assert runner.ingest_if_due(tmp_path, cmd=cmd, tz="UTC", now=NOW + 120) == "cap"
    assert not calls.exists()


# ------------------------------------------- the heartbeat
# The container's health check reads the age of a file the worker touches as it loops: a worker that stopped looping
# goes unhealthy while its process still runs.

def test_a_pass_touches_the_heartbeat_at_most_every_heartbeat_s(tmp_path):
    beat = tmp_path / runner.HEARTBEAT_FILE
    w = worker(tmp_path, stub(tmp_path, "exit 0"))
    w.poll(now=NOW)
    assert beat.is_file() and time.time() - beat.stat().st_mtime < 60
    os.utime(beat, (0, 0))
    w.poll(now=NOW + runner.HEARTBEAT_S - 1)
    assert beat.stat().st_mtime == 0, "touched again sooner than HEARTBEAT_S"
    w.poll(now=NOW + runner.HEARTBEAT_S)
    assert time.time() - beat.stat().st_mtime < 60


def test_the_heartbeat_is_touched_before_the_pass_runs_anything(tmp_path, monkeypatch):
    """A pass that runs a refresh touches the file first, so the age the health check reads is at most one run plus
    HEARTBEAT_S; and a pass that fails part way (the loop logs it and goes on) has still beaten."""
    beat = tmp_path / runner.HEARTBEAT_FILE
    seen = tmp_path / "seen"
    rs.write_request(tmp_path, nonce=N1, now=NOW - 5)
    worker(tmp_path, stub(tmp_path, f"test -f {beat} && touch {seen}")).poll(now=NOW)
    assert seen.exists()
    beat.unlink()

    def fails(*args):
        raise OSError("the status could not be read")

    monkeypatch.setattr(runner, "close_dead_ingest", fails)
    with pytest.raises(OSError):
        worker(tmp_path, stub(tmp_path, "exit 0")).poll(now=NOW + 60)
    assert beat.is_file()


def test_the_worker_started_once_leaves_a_heartbeat_in_its_engines_directory(tmp_path, monkeypatch):
    """The engine's directory: the refresh directory when nothing else names one, else FF_HOME's state/engine/."""
    for v in ("FF_HOME", "FF_STATE_REFRESH_DIR", "FF_STATE_ENGINE_DIR"):
        monkeypatch.delenv(v, raising=False)
    d = tmp_path / "home" / "state" / "refresh"
    rest = ["--once", "--snapshots", str(tmp_path / "snapshots"), "--refresh-cmd", stub(tmp_path, "exit 0")]
    assert runner.main(["--dir", str(d), *rest]) == 0
    assert (d / runner.HEARTBEAT_FILE).is_file()
    monkeypatch.setenv("FF_HOME", str(tmp_path / "home"))
    assert runner.main(rest) == 0
    assert (tmp_path / "home" / "state" / "engine" / runner.HEARTBEAT_FILE).is_file()


def test_an_ingest_that_never_recorded_its_end_is_closed_by_the_worker(tmp_path):
    """SIGKILL leaves the script no chance to record the ingest's end; the worker records it with the exit status, so
    nothing waits forever on an ingest that is gone. The last refresh's state stays as it was."""
    rs.write_status(tmp_path, **LAST_REFRESH)
    rs.touch_ingest_marker(tmp_path, NOW - 120)
    assert runner.ingest_if_due(tmp_path, cmd=stub(tmp_path, "kill -9 $$"), tz="UTC", now=NOW) == "ran"
    doc = rs.read_status(tmp_path)
    assert doc["ingest_rc"] == 137 and rs.ingest_open(doc) is False
    assert doc["state"] == "done"


def test_a_refresh_killed_in_its_ingest_step_has_that_ingest_closed_too(tmp_path):
    """A refresh that dies after its ingest step began leaves an ingest with no end; the worker closes it as it marks
    the refresh interrupted."""
    record_start = (f"python3 {Path(runner.__file__).parent / 'refresh_state.py'} write-status --dir {tmp_path} "
                    "--ingest start\nkill -9 $$")
    rs.write_request(tmp_path, nonce=N1, now=NOW - 5)
    run(tmp_path, stub(tmp_path, record_start))
    doc = rs.read_status(tmp_path)
    assert (doc["state"], doc["rc"]) == ("interrupted", 137)
    assert doc["ingest_rc"] == 137 and rs.ingest_open(doc) is False


# ------------------------------------------- an ingest that is stopped, found dead or timed out
# A stopped ingest (143) or one found open with no run holding the lock (INGEST_INTERRUPTED_RC) read nothing reliably:
# its end is recorded and the marker's ingest is wanted again. A timed-out one (INGEST_TIMED_OUT_RC) is not retried.

RS_CLI = f"python3 {Path(runner.__file__).parent / 'refresh_state.py'} write-status"


def trapping_stub(tmp_path: Path, *, refresh: bool = False, sleep_s: int = 30) -> str:
    """refresh.sh in miniature: it records its ingest's start (an ingest-only run's, or a refresh's own step with
    `refresh`), says it started, sleeps, and records the end; its TERM trap records the end as stopped (143), and a
    refresh's state as interrupted, as the script's trap does. The sleep's pid is kept, to see whether it outlived
    the run."""
    cli = f"{RS_CLI} --dir {tmp_path}"
    start = f"{cli} --ingest start" if refresh else f'{cli} --ingest start --only --at "$FF_INGEST_STARTED_AT"'
    interrupted = f"; {cli} --state interrupted --rc 143" if refresh else ""
    return stub(tmp_path, f"trap '{cli} --ingest done --rc 143{interrupted}; exit 143' TERM\n{start}\n"
                          f"sleep {sleep_s} &\necho $! > {tmp_path / 'sleep.pid'}\ntouch {tmp_path / 'started'}\n"
                          f"wait $!\n{cli} --ingest done --rc 0")


def alive(pid: int) -> bool:
    """The process exists and is no zombie (an orphan nobody has reaped yet is gone for this purpose)."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def test_a_stopped_refresher_ends_its_ingest_before_it_exits(tmp_path):
    """`docker stop` SIGTERMs the refresher mid-run: it signals the run's whole process group, whose trap records the
    ingest's end as stopped, before the refresher exits (not after the run would have finished), and the mark is
    cleared. A stopped ingest read nothing reliably, so the marker's ingest is wanted again, with no new wait."""
    cmd = trapping_stub(tmp_path, sleep_s=30)
    rs.touch_ingest_marker(tmp_path, time.time() - 300)
    proc = subprocess.Popen([sys.executable, runner.__file__, "--dir", str(tmp_path), "--once",
                             "--snapshots", str(tmp_path / "snapshots"), "--refresh-cmd", cmd],
                            stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 20
        while not (tmp_path / "started").exists():
            assert proc.poll() is None and time.monotonic() < deadline, "the stub never started"
            time.sleep(0.05)
        assert rs.ingest_only_in_flight(rs.read_status(tmp_path)) is True
        stopped = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        _, err = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == 0, err
    assert time.monotonic() - stopped < runner.STOP_GRACE_S, "the run was waited out, not signalled"
    assert not alive(int((tmp_path / "sleep.pid").read_text())), "the run's group outlived the refresher"
    doc = rs.read_status(tmp_path)
    assert (doc["ingest_rc"], rs.ingest_open(doc), doc["ingest_only"]) == (rs.INGEST_STOPPED_RC, False, False)
    assert rs.ingest_due(tmp_path, time.time(), "UTC") == "run"


def read_until(stream, text: str, timeout_s: float) -> bytes:
    """Read a pipe (unbuffered) until `text` has appeared in it, failing past `timeout_s`."""
    seen = b""
    deadline = time.monotonic() + timeout_s
    while text.encode() not in seen:
        left = deadline - time.monotonic()
        assert left > 0, f"never printed {text!r}"
        if select.select([stream], [], [], left)[0]:
            chunk = os.read(stream.fileno(), 4096)
            assert chunk, f"the pipe closed before {text!r}"
            seen += chunk
    return seen


def test_a_second_stop_during_the_grace_does_not_cut_it_short(tmp_path):
    """Stopped twice (`docker stop`, then another), the refresher gets its second SIGTERM while it waits out the run's
    grace: the grace runs to its end and the run's group is KILLed then, so a run that ignores TERM does not outlive
    the refresher with no lock. The grace is patched to a second and a half."""
    cmd = stub(tmp_path, f"trap '' TERM\necho $$ > {tmp_path / 'shell.pid'}\nsleep 30 &\n"
                         f"echo $! > {tmp_path / 'sleep.pid'}\ntouch {tmp_path / 'started'}\nwait $!")
    rs.touch_ingest_marker(tmp_path, time.time() - 300)
    grace = 1.5
    code = (f"import sys; sys.path.insert(0, {str(Path(runner.__file__).parent)!r}); import refresh_runner as r; "
            f"r.STOP_GRACE_S = {grace}; sys.exit(r.main(sys.argv[1:]))")
    proc = subprocess.Popen([sys.executable, "-c", code, "--dir", str(tmp_path), "--once",
                             "--snapshots", str(tmp_path / "snapshots"), "--refresh-cmd", cmd],
                            stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 20
        while not (tmp_path / "started").exists():
            assert proc.poll() is None and time.monotonic() < deadline, "the stub never started"
            time.sleep(0.05)
        stopped = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        read_until(proc.stderr, "stopping: ending ingest first", 10)
        time.sleep(0.3)                                   # inside the grace
        proc.send_signal(signal.SIGTERM)
        # not communicate(): a run left alive would hold the inherited stderr open
        proc.wait(timeout=20)
        ended = time.monotonic()
        pids = {name: int((tmp_path / f"{name}.pid").read_text()) for name in ("shell", "sleep")}
        left = [name for name, pid in pids.items() if alive(pid)]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stderr.close()
        shell = tmp_path / "shell.pid"
        if shell.exists() and alive(int(shell.read_text())):                # a failed run leaves nothing behind
            with contextlib.suppress(OSError):
                os.killpg(int(shell.read_text()), signal.SIGKILL)
    assert proc.returncode == 0
    assert ended - stopped >= grace, "the second signal cut the grace short"
    assert left == [], f"the run's {' and '.join(left)} outlived the refresher"


@pytest.mark.parametrize("body,rc,again", [
    ("kill -s INT $$", 130, False),
    ("kill -s TERM $$", rs.INGEST_STOPPED_RC, True),       # TERM from outside: a stop, so the ingest is wanted again
    ("kill -s KILL $$", 137, False),
    ("sleep 20 & wait $!", rs.INGEST_TIMED_OUT_RC, False),   # the refresher's own timeout
], ids=["INT", "TERM", "KILL", "timed out"])
def test_a_run_killed_by_signal_n_is_recorded_as_128_plus_n(tmp_path, monkeypatch, body, rc, again):
    """`Popen.wait` gives -N for a death by signal N, which would read as the refresher's own codes (INT, 2, as found
    dead and retried; HUP, 1, as timed out). The run is recorded as the shell reports it, 128 + N; only the refresher's
    own timeout records INGEST_TIMED_OUT_RC."""
    monkeypatch.setattr(runner, "RUN_TIMEOUT_S", 1)
    rs.touch_ingest_marker(tmp_path, NOW - 300)
    assert runner.ingest_if_due(tmp_path, cmd=stub(tmp_path, body), tz="UTC", now=NOW) == "ran"
    doc = rs.read_status(tmp_path)
    assert (doc["ingest_rc"], rs.ingest_open(doc)) == (rc, False)
    assert rs.ingest_due(tmp_path, NOW + 2, "UTC") == ("run" if again else "no")


def test_a_dead_ingest_is_closed_by_the_next_pass_and_run_again(tmp_path):
    """An ingest with a start and no end while no run holds the lock is dead: the next pass records its end with
    INGEST_INTERRUPTED_RC and clears a mark left behind, and the marker, though older than that start, wants the
    ingest again; the same pass runs it. The last refresh's own fields are left alone."""
    cmd, calls = recording_stub(tmp_path, "exit 0")
    rs.touch_ingest_marker(tmp_path, NOW - 300)
    rs.write_status(tmp_path, **LAST_REFRESH, ingest_started_at=NOW - 100, ingest_finished_at=None, ingest_rc=None,
                    ingest_runs=[NOW - 100], ingest_only=True)
    assert rs.ingest_due(tmp_path, NOW, "UTC") == "no"                  # open: it answers the marker as it runs
    assert runner.close_dead_ingest(tmp_path) is True
    doc = rs.read_status(tmp_path)
    assert (doc["ingest_rc"], rs.ingest_open(doc), doc["ingest_only"]) == (rs.INGEST_INTERRUPTED_RC, False, False)
    assert {k: doc[k] for k in LAST_REFRESH} == LAST_REFRESH
    assert rs.ingest_due(tmp_path, NOW, "UTC") == "run"
    assert runner.close_dead_ingest(tmp_path) is False                  # nothing open: nothing written
    # the same state reached by a pass of the loop: closed, then run at once
    rs.write_status(tmp_path, ingest_finished_at=None, ingest_rc=None, ingest_only=True)
    worker(tmp_path, cmd).poll(now=NOW)
    assert calls.read_text().splitlines() == ["[ingest] ingest 1 "]
    assert rs.read_status(tmp_path)["ingest_rc"] == 0


def test_an_open_ingest_is_left_alone_while_another_run_holds_the_lock(tmp_path):
    """The lock held elsewhere (a scheduled refresh in its ingest step): the ingest may be theirs, in flight, so
    nothing is written."""
    rs.write_status(tmp_path, **LAST_REFRESH, ingest_started_at=NOW - 100, ingest_finished_at=None, ingest_rc=None)
    before = (tmp_path / rs.STATUS_FILE).read_bytes()
    holder = hold_lock(tmp_path / rs.LOCK_FILE)
    try:
        assert runner.close_dead_ingest(tmp_path) is False
        assert (tmp_path / rs.STATUS_FILE).read_bytes() == before
    finally:
        holder.kill()
        holder.wait()


def test_a_timed_out_ingest_is_recorded_as_timed_out_and_not_wanted_again(tmp_path, monkeypatch):
    """The timeout TERMs the run's group, whose trap records 143; the refresher records the ingest it began as timed
    out whatever the trap wrote, so a hung ingest is not run again every fifteen minutes."""
    monkeypatch.setattr(runner, "RUN_TIMEOUT_S", 1)
    rs.touch_ingest_marker(tmp_path, NOW - 300)
    assert runner.ingest_if_due(tmp_path, cmd=trapping_stub(tmp_path, sleep_s=20), tz="UTC", now=NOW) == "ran"
    doc = rs.read_status(tmp_path)
    assert (doc["ingest_rc"], rs.ingest_open(doc), doc["ingest_only"]) == (rs.INGEST_TIMED_OUT_RC, False, False)
    assert rs.ingest_due(tmp_path, NOW + 2, "UTC") == "no"


def test_a_refresh_timed_out_in_its_ingest_step_records_that_ingest_as_timed_out(tmp_path, monkeypatch):
    """The same for a refresh whose ingest step began in the run the timeout cut short: its trap marks the refresh
    interrupted and the ingest stopped, and the refresher records the ingest as timed out."""
    monkeypatch.setattr(runner, "RUN_TIMEOUT_S", 1)
    rs.touch_ingest_marker(tmp_path, NOW - 300)
    rs.write_request(tmp_path, nonce=N1, now=NOW - 5)
    assert run(tmp_path, trapping_stub(tmp_path, refresh=True, sleep_s=20)) == N1
    doc = rs.read_status(tmp_path)
    assert doc["state"] == "interrupted"
    assert (doc["ingest_rc"], rs.ingest_open(doc)) == (rs.INGEST_TIMED_OUT_RC, False)
    assert rs.ingest_wanted(rs.marker_time(tmp_path), doc) is False


@pytest.mark.parametrize("how", ["succeeds", "fails", "times out"])
def test_the_ingest_only_mark_covers_the_run_and_is_cleared_after_it(tmp_path, monkeypatch, how):
    """The mark is written with the run's claim, so the script sees it from its first moment, and cleared as the
    refresher's last write before the lock goes, however the run ended."""
    monkeypatch.setattr(runner, "RUN_TIMEOUT_S", 1)
    tail = {"succeeds": "exit 0", "fails": "exit 3", "times out": "sleep 20 & wait $!"}[how]
    cmd, _ = recording_stub(tmp_path, tail)
    rs.touch_ingest_marker(tmp_path, NOW - 300)
    assert runner.ingest_if_due(tmp_path, cmd=cmd, tz="UTC", now=NOW) == "ran"
    assert json.loads((tmp_path / "seen.json").read_text())["ingest_only"] is True
    doc = rs.read_status(tmp_path)
    assert doc["ingest_only"] is False and rs.ingest_open(doc) is False
    assert doc["ingest_rc"] == {"succeeds": 0, "fails": 3, "times out": rs.INGEST_TIMED_OUT_RC}[how]


def test_a_refresh_claim_clears_a_stale_ingest_only_mark(tmp_path):
    """A mark a killed refresher left true cannot make the refresh it claims next read as an ingest-only run."""
    cmd, _ = recording_stub(tmp_path, "exit 0")
    rs.write_status(tmp_path, **LAST_REFRESH, ingest_only=True)
    rs.write_request(tmp_path, nonce=N1, now=NOW - 5)
    assert run(tmp_path, cmd) == N1
    seen = json.loads((tmp_path / "seen.json").read_text())
    assert (seen["state"], seen["ingest_only"]) == ("running", False)


# ------------------------------------------- the directories it is given
# The worker takes its directories from arguments, else their variables, else FF_HOME, else the legacy layout
# (refresh_state.locate), and runs the refresh.sh beside it; the script, which resolves the refresh directory itself,
# is told the worker's whenever it would not find that one by itself.

# the ETL's `python -m etl …`, logged; any other `python` is the real interpreter
PYTHON_LOGGING = '#!/bin/sh\ncase "$*" in\n  "-m etl "*) echo "$*" >> "$ETL_CALLS" ;;\n  *) exec python3 "$@" ;;\nesac\n'


def started(tmp_path: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    """The worker run once as the container runs it, by path, with a stub `python` (the ETL) first on PATH, an
    invented feed credential and an empty config folder outside the trees it is given (FF_SECRETS_DIR, FF_CONFIG_DIR),
    no other path variable but those given and nothing else telling it where anything is."""
    binp = tmp_path / "bin"
    binp.mkdir(exist_ok=True)
    (binp / "python").write_text(PYTHON_LOGGING)
    (binp / "python").chmod(0o755)
    credential = tmp_path / "secrets" / "feed" / "simplefin_access_url"
    credential.parent.mkdir(parents=True, exist_ok=True)
    credential.write_text("https://user:invented@bridge.example.net/simplefin")
    credential.chmod(0o600)
    (tmp_path / "config").mkdir(exist_ok=True)
    clean = {k: v for k, v in os.environ.items() if not k.startswith("FF_")}
    return subprocess.run([sys.executable, runner.__file__, *args, "--once"], capture_output=True, text=True,
                          timeout=120, cwd=tmp_path,
                          env={**clean, "PATH": f"{binp}:{clean['PATH']}", "FF_SECRETS_DIR": str(tmp_path / "secrets"),
                               "FF_CONFIG_DIR": str(tmp_path / "config"), "ETL_CALLS": str(tmp_path / "etl-calls"),
                               **env})


def assert_a_refresh_ran_in(d: Path, tmp_path: Path, own: Path | None = None) -> None:
    """The status in the mailbox `d`; the script's log and streak in `own` (the engine's directory), else in `d`."""
    doc = rs.read_status(d)
    assert (doc["state"], doc["request_nonce"], doc["rc"], doc["trigger"]) == ("done", N1, 0, "manual")
    assert "refresh done (rc=0" in ((own or d) / "fetch.log").read_text()
    assert ((own or d) / ".refresh_state").read_text() == "0\n"
    assert (tmp_path / "etl-calls").read_text().splitlines() == ["-m etl simplefin fetch", "-m etl ingest"]


def test_a_worker_given_only_its_directories_runs_the_refresh_in_that_tree(tmp_path):
    d, snapshots = tmp_path / "tree" / "refresh", tmp_path / "tree" / "snapshots"
    d.mkdir(parents=True)
    rs.write_request(d, nonce=N1, now=time.time() - 5)
    r = started(tmp_path, "--dir", str(d), "--snapshots", str(snapshots))
    assert r.returncode == 0, r.stderr
    assert_a_refresh_ran_in(d, tmp_path)
    assert sorted(p.name for p in (tmp_path / "tree").iterdir()) == ["refresh"]


def test_a_worker_given_only_ff_home_and_its_script_share_the_home_tree(tmp_path):
    """Under FF_HOME the mailbox (state/refresh/) holds what the store reads too, and only that: the request, the status
    and the run's liveness lock; the refresh lock, the heartbeat, the log and the streak are the engine's
    (state/engine/), which the store never sees."""
    home = tmp_path / "home"
    d = home / "state" / "refresh"
    d.mkdir(parents=True)
    rs.write_request(d, nonce=N1, now=time.time() - 5)
    r = started(tmp_path, FF_HOME=str(home))
    assert r.returncode == 0, r.stderr
    engine = home / "state" / "engine"
    assert_a_refresh_ran_in(d, tmp_path, own=engine)
    assert sorted(p.name for p in home.iterdir()) == ["state"]
    assert sorted(p.name for p in (home / "state").iterdir()) == ["engine", "refresh"]
    assert sorted(p.name for p in d.iterdir()) == [rs.LOCK_FILE, rs.REQUEST_FILE, rs.STATUS_FILE]
    assert {rs.LOCK_FILE, runner.HEARTBEAT_FILE, "fetch.log", ".refresh_state"} <= {p.name for p in engine.iterdir()}


def test_the_script_is_told_the_directory_only_when_it_would_not_find_it_itself(tmp_path, monkeypatch):
    legacy = Path(rs.LOCATIONS["refresh"].legacy)
    assert runner.script_env(legacy) == {}
    assert runner.script_env(tmp_path) == {"FF_STATE_REFRESH_DIR": str(tmp_path)}
    monkeypatch.setenv("FF_HOME", str(tmp_path))
    assert runner.script_env(tmp_path / "state" / "refresh") == {}
    assert runner.script_env(legacy) == {"FF_STATE_REFRESH_DIR": str(legacy)}
    monkeypatch.setenv("FF_STATE_REFRESH_DIR", str(tmp_path / "named"))
    assert runner.script_env(tmp_path / "named") == {}


def test_the_script_is_told_the_engines_directory_only_when_it_would_not_find_it_itself(tmp_path, monkeypatch):
    for v in ("FF_HOME", "FF_STATE_REFRESH_DIR", "FF_STATE_ENGINE_DIR"):
        monkeypatch.delenv(v, raising=False)
    mailbox = tmp_path / "mailbox"
    assert runner.script_env(mailbox, mailbox) == {"FF_STATE_REFRESH_DIR": str(mailbox)}
    assert runner.script_env(mailbox, tmp_path / "engine") == {"FF_STATE_REFRESH_DIR": str(mailbox),
                                                               "FF_STATE_ENGINE_DIR": str(tmp_path / "engine")}
    monkeypatch.setenv("FF_HOME", str(tmp_path))
    found = tmp_path / "state" / "engine"
    assert runner.script_env(tmp_path / "state" / "refresh", found) == {}
    assert runner.script_env(tmp_path / "state" / "refresh", mailbox) == {"FF_STATE_ENGINE_DIR": str(mailbox)}


def test_the_refresh_lock_is_the_engines_and_the_mailbox_holds_the_runs_liveness(tmp_path):
    """While a run holds the engine's refresh lock, the store's probe of the mailbox (rs.is_running) sees it running;
    once it ends, it does not."""
    mailbox, engine = tmp_path / "mailbox", tmp_path / "engine"
    mailbox.mkdir()
    seen = tmp_path / "seen"
    probe = (f"{sys.executable} -c 'import sys; sys.path.insert(0, \"{Path(runner.__file__).parent}\"); "
             f"import refresh_state as rs; print(rs.is_running(rs.Path(\"{mailbox / rs.LOCK_FILE}\")))' > {seen}")
    rs.write_request(mailbox, nonce=N1, now=NOW - 5)
    assert runner.claim_and_run(mailbox, cmd=stub(tmp_path, probe), snapshots=tmp_path / "snapshots", now=NOW,
                                engine=engine) == N1
    assert seen.read_text().strip() == "True"
    assert (engine / rs.LOCK_FILE).is_file() and not rs.is_running(mailbox / rs.LOCK_FILE)
    holder = hold_lock(engine / rs.LOCK_FILE)                 # a scheduled run holds the engine's lock
    try:
        rs.write_request(mailbox, nonce="n2", now=NOW + 1)
        assert runner.claim_and_run(mailbox, cmd=stub(tmp_path, "exit 0"), snapshots=tmp_path / "snapshots",
                                    now=NOW + 2, engine=engine) is None
    finally:
        holder.kill()
        holder.wait()


def test_the_child_runs_with_the_directory_the_worker_was_given(tmp_path):
    seen = tmp_path / "seen"
    rs.write_request(tmp_path, nonce=N1, now=NOW - 5)
    run(tmp_path, stub(tmp_path, f'echo "${{FF_STATE_REFRESH_DIR:-unset}}" > {seen}'))
    assert seen.read_text().strip() == str(tmp_path)
    rs.touch_ingest_marker(tmp_path, NOW - 300)
    seen.unlink()
    assert runner.ingest_if_due(tmp_path, cmd=stub(tmp_path, f'echo "${{FF_STATE_REFRESH_DIR:-unset}}" > {seen}'),
                                tz="UTC", now=NOW) == "ran"
    assert seen.read_text().strip() == str(tmp_path)


def test_a_dir_that_the_environment_contradicts_is_named_in_one_warning(tmp_path, monkeypatch, capsys):
    """`--dir /app/var/refresh` beside an FF_HOME, say: the runs the worker starts and cron's would keep two locks and
    two streaks, so the worker says which directory it uses and that the schedule needs the same one."""
    monkeypatch.setenv("FF_HOME", str(tmp_path / "home"))
    found, given = tmp_path / "home" / "state" / "refresh", tmp_path / "given"
    rest = ["--once", "--snapshots", str(tmp_path / "snapshots"), "--refresh-cmd", stub(tmp_path, "exit 0")]
    assert runner.main(["--dir", str(given), *rest]) == 0
    assert [line for line in capsys.readouterr().err.splitlines() if "WARNING" in line] == [
        f"refresh_runner: WARNING: --dir names {given}, but the environment alone gives {found}: this worker uses "
        f"{given}; give the scheduled refresh the same directory (FF_STATE_REFRESH_DIR)"]
    for agreeing in (["--dir", str(found)], []):
        assert runner.main([*agreeing, *rest]) == 0
        assert "WARNING" not in capsys.readouterr().err


def test_the_default_script_is_the_refresh_sh_beside_the_worker():
    assert runner.DEFAULT_REFRESH_CMD == str(Path(runner.__file__).resolve().parent / "refresh.sh")

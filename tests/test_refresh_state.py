# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""deploy/refresh_state.py — the manual-refresh rate limit and liveness probe.

decide() is pure, so the whole policy is testable with an injected clock and
timezone. The liveness/ledger helpers get their own tests because they are what
stands between a crashed refresh and a permanently disabled button.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

import pytest
import refresh_state as rs  # tests/conftest.py puts deploy/ on sys.path

from etl import simplefin as sf

TZ = "America/New_York"
NY = ZoneInfo(TZ)
ROOT = Path(__file__).resolve().parent.parent


def ny(y, m, d, hh=0, mm=0, ss=0) -> float:
    """Epoch for a wall-clock time in New York, the household's zone in these tests."""
    return dt.datetime(y, m, d, hh, mm, ss, tzinfo=NY).timestamp()


def wall(zone: str, y, m, d, hh=0, mm=0, ss=0, fold=0) -> float:
    """Epoch for a wall-clock time in `zone` (`fold` 1: the second of a repeated hour)."""
    return dt.datetime(y, m, d, hh, mm, ss, tzinfo=ZoneInfo(zone), fold=fold).timestamp()


def decide(**kw):
    """decide() with the 'nothing is happening' defaults filled in."""
    base = {"now": ny(2026, 8, 11, 12, 0), "tz": TZ, "manual_runs": [], "last_run_at": None,
            "running": False, "nonce_in_flight": None, "queued_age_s": None}
    return rs.decide(**{**base, **kw})


# ------------------------------------------------------------- decide() table

def test_allows_when_nothing_has_run():
    d = decide()
    assert (d.allow, d.reason, d.used_today, d.limit_per_day) == (True, "ok", 0, 6)


def test_min_gap_refuses_just_under_and_allows_just_over():
    now = ny(2026, 8, 11, 12, 0)
    under = decide(now=now, last_run_at=now - (15 * 60 - 1))
    assert (under.allow, under.reason, under.retry_after_s) == (False, "min_gap", 1)
    assert decide(now=now, last_run_at=now - (15 * 60 + 1)).allow


def test_min_gap_measures_against_any_refresh_not_just_manual_ones():
    """A manual pull five minutes after the 09:00 cron run fetches nothing new
    and still spends one of SimpleFIN's 24 daily requests."""
    now = ny(2026, 8, 11, 9, 5)
    d = decide(now=now, manual_runs=[now - 3 * 3600], last_run_at=now - 300)
    assert (d.allow, d.reason) == (False, "min_gap")
    assert d.retry_after_s == 600


def test_running_refuses_without_spending_budget():
    d = decide(running=True, nonce_in_flight="abc123")
    assert (d.allow, d.reason, d.nonce_in_flight) == (False, "already_running", "abc123")
    assert d.used_today == 0


def test_queued_request_attaches_then_flags_a_dead_worker():
    assert decide(queued_age_s=4).reason == "already_running"
    assert decide(queued_age_s=rs.WORKER_STALE_S + 1).reason == "worker_unavailable"


def test_a_run_in_flight_outranks_a_queued_request():
    """The refresher blocks for the whole refresh.sh run, so it legitimately
    cannot claim a new request meanwhile — that is not a dead worker."""
    d = decide(running=True, queued_age_s=rs.WORKER_STALE_S + 60)
    assert d.reason == "already_running"


def test_daily_cap_refuses_until_local_midnight():
    now = ny(2026, 8, 11, 13, 0)
    runs = [ny(2026, 8, 11, h, 0) for h in (1, 2, 3, 4, 5, 6)]
    d = decide(now=now, manual_runs=runs)
    assert (d.allow, d.reason, d.used_today) == (False, "daily_cap", 6)
    assert d.retry_after_s == 11 * 3600      # 13:00 in New York -> midnight


def test_used_today_is_the_count_before_this_request():
    now = ny(2026, 8, 11, 13, 0)
    runs = [ny(2026, 8, 11, h, 0) for h in (1, 2)]
    assert decide(now=now, manual_runs=runs).used_today == 2


# ------------------------------------------------- the timezone bucketing test

def test_daily_cap_buckets_by_LOCAL_day_not_utc():
    """The regression this guards: at 23:00 in New York the UTC date has already rolled
    over, so a naive datetime.now()/UTC implementation buckets `now` into
    tomorrow, sees zero runs today, and hands out six more refreshes at 11pm.
    """
    runs = [ny(2026, 8, 10, h, 0) for h in (8, 9, 10, 11, 12, 13)]
    late = decide(now=ny(2026, 8, 10, 23, 0), manual_runs=runs)
    assert (late.allow, late.reason, late.used_today) == (False, "daily_cap", 6)

    after_midnight = decide(now=ny(2026, 8, 11, 0, 0), manual_runs=runs)
    assert (after_midnight.allow, after_midnight.used_today) == (True, 0)


def test_seconds_to_local_midnight_handles_both_dst_transitions():
    # 2026-03-08 springs forward (23h day), 2026-11-01 falls back (25h day)
    assert rs.seconds_to_local_midnight(ny(2026, 3, 8), TZ) == 23 * 3600
    assert rs.seconds_to_local_midnight(ny(2026, 11, 1), TZ) == 25 * 3600
    assert rs.seconds_to_local_midnight(ny(2026, 8, 11), TZ) == 24 * 3600


# ------------------------------------------------- the household's zone (UTC, a day behind it, a day ahead of it)

ZONES = ["UTC", "America/New_York", "Pacific/Kiritimati"]


@pytest.mark.parametrize("zone", ZONES)
def test_the_manual_cap_turns_over_at_the_households_midnight(zone):
    """Six manual refreshes in the household's evening use the day up; a minute before its midnight the seventh waits
    exactly that minute, and thirty seconds after it the day is new, whatever day UTC is on."""
    runs = [wall(zone, 2026, 8, 11, h, 0) for h in range(18, 24)]
    before = decide(now=wall(zone, 2026, 8, 11, 23, 59), tz=zone, manual_runs=runs)
    assert (before.allow, before.reason, before.used_today, before.retry_after_s) == (False, "daily_cap", 6, 60)
    after = decide(now=wall(zone, 2026, 8, 12, 0, 0, 30), tz=zone, manual_runs=runs)
    assert (after.allow, after.used_today) == (True, 0)


def test_kiritimatis_day_starts_fourteen_hours_before_utcs():
    """Kiritimati (UTC+14) is a day ahead: at 10:00:30 UTC its new day has begun and its cap is spent no more, while a
    household in UTC is still on the day those six runs were made."""
    runs = [wall("Pacific/Kiritimati", 2026, 8, 11, h, 0) for h in range(18, 24)]     # 04:00 to 09:00 UTC, Aug 11
    now = dt.datetime(2026, 8, 11, 10, 0, 30, tzinfo=dt.UTC).timestamp()
    assert decide(now=now, tz="Pacific/Kiritimati", manual_runs=runs).used_today == 0
    assert decide(now=now, tz="UTC", manual_runs=runs).used_today == 6
    assert rs.seconds_to_local_midnight(now, "Pacific/Kiritimati") == 24 * 3600 - 30
    assert rs.seconds_to_local_midnight(now, "UTC") == 14 * 3600 - 30


@pytest.mark.parametrize("zone", ZONES)
def test_the_ingest_only_cap_turns_over_at_the_households_midnight(tmp_path, zone):
    """MAX_INGEST_PER_DAY ingest-only runs in the household's day: capped until its midnight, then due again; the
    start written just after midnight keeps only that day's runs."""
    day = [wall(zone, 2026, 8, 11, 0, 10) + k * 60 for k in range(rs.MAX_INGEST_PER_DAY)]
    rs.write_status(tmp_path, ingest_runs=day, ingest_started_at=day[-1], ingest_finished_at=day[-1] + 5, ingest_rc=0)
    rs.touch_ingest_marker(tmp_path, wall(zone, 2026, 8, 11, 22, 0))
    late, early = wall(zone, 2026, 8, 11, 23, 59, 59), wall(zone, 2026, 8, 12, 0, 0, 1)
    assert rs.ingest_due(tmp_path, late, zone) == "cap"
    assert rs.ingest_due(tmp_path, early, zone) == "run"
    fields = rs.ingest_start_fields(rs.read_status(tmp_path), early, zone, only=True)
    assert fields["ingest_runs"] == [early]


def test_a_day_the_clocks_go_forward_is_23_hours_and_the_cap_still_turns_over_at_its_midnight():
    """New York's 2026-03-08 has no 02:00 to 03:00: six runs that day spend it, the one to midnight waits 30 minutes
    from 23:30, and the day after starts with none."""
    runs = [ny(2026, 3, 8, h, 30) for h in (0, 1, 3, 4, 5, 6)]
    assert runs[2] - runs[1] == 3600                                   # 01:30 EST to 03:30 EDT is one hour
    late = decide(now=ny(2026, 3, 8, 23, 30), manual_runs=runs)
    assert (late.reason, late.used_today, late.retry_after_s) == ("daily_cap", 6, 1800)
    assert decide(now=ny(2026, 3, 9, 0, 0, 1), manual_runs=runs).used_today == 0
    assert rs.seconds_to_local_midnight(ny(2026, 3, 8, 1, 0), TZ) == 22 * 3600


def test_a_day_the_clocks_go_back_is_25_hours_and_both_of_its_1_30s_count_in_it():
    """New York's 2026-11-01 holds 01:00 to 02:00 twice: a run in each counts toward that day, and its midnight is 25
    hours after the one before; the second 01:30 is 23 and a half hours from it."""
    first, second = wall(TZ, 2026, 11, 1, 1, 30), wall(TZ, 2026, 11, 1, 1, 30, fold=1)
    assert second - first == 3600
    runs = [ny(2026, 11, 1, 0, 30), first, second, ny(2026, 11, 1, 3, 0), ny(2026, 11, 1, 9, 0), ny(2026, 11, 1, 17, 0)]
    late = decide(now=ny(2026, 11, 1, 23, 0), manual_runs=runs)
    assert (late.reason, late.used_today, late.retry_after_s) == ("daily_cap", 6, 3600)
    assert decide(now=ny(2026, 11, 2, 0, 0), manual_runs=runs).used_today == 0
    assert rs.seconds_to_local_midnight(ny(2026, 11, 1), TZ) == 25 * 3600
    assert rs.seconds_to_local_midnight(second, TZ) == 22 * 3600 + 1800


# ------------------------------------------------------------------ the schedule the scheduler leaves

@pytest.mark.parametrize("doc, schedule", [
    ({"timezone": "America/New_York", "hours": [17, 1, 9], "minute": 5}, rs.Schedule("America/New_York", (1, 9, 17), 5)),
    ({"timezone": "Pacific/Kiritimati", "hours": [0], "minute": 59}, rs.Schedule("Pacific/Kiritimati", (0,), 59)),
    ({"timezone": "UTC", "hours": [0, 4, 8, 12, 16, 20], "minute": 0}, rs.Schedule("UTC", (0, 4, 8, 12, 16, 20), 0)),
    ({"timezone": "Mars/Olympus", "hours": [1], "minute": 0}, None),
    ({"timezone": "localtime", "hours": [1], "minute": 0}, None),
    ({"timezone": "../../etc/passwd", "hours": [1], "minute": 0}, None),
    ({"timezone": "UTC", "hours": [], "minute": 0}, None),
    ({"timezone": "UTC", "hours": [0, 1, 2, 3, 4, 5, 6], "minute": 0}, None),
    ({"timezone": "UTC", "hours": [1, 1], "minute": 0}, None),
    ({"timezone": "UTC", "hours": [24], "minute": 0}, None),
    ({"timezone": "UTC", "hours": [True], "minute": 0}, None),
    ({"timezone": "UTC", "hours": [1], "minute": 60}, None),
    ({"timezone": "UTC", "hours": [1], "minute": "0"}, None),
    ({"timezone": "UTC", "hours": [1]}, None),
    (["UTC"], None),
])
def test_a_schedule_is_read_only_when_it_is_one(doc, schedule):
    assert rs.schedule_from(doc) == schedule


def test_without_a_schedule_the_days_are_utcs_and_none_is_reported(tmp_path, capsys):
    assert rs.read_schedule(tmp_path) is None and rs.household_zone(tmp_path) == "UTC"
    assert rs.read_schedule(None) is None and rs.household_zone(None) == "UTC"
    (tmp_path / rs.SCHEDULE_FILE).write_text("{not json")
    assert rs.read_schedule(tmp_path) is None and rs.household_zone(tmp_path) == "UTC"
    assert "corrupt/unreadable" in capsys.readouterr().err
    (tmp_path / rs.SCHEDULE_FILE).write_text(json.dumps({"timezone": "Mars/Olympus", "hours": [1], "minute": 0}))
    assert rs.household_zone(tmp_path) == "UTC"
    rs.write_schedule(tmp_path, rs.Schedule("Pacific/Kiritimati", (6, 18), 30))
    assert json.loads((tmp_path / rs.SCHEDULE_FILE).read_text()) == {
        "timezone": "Pacific/Kiritimati", "hours": [6, 18], "minute": 30}
    assert rs.household_zone(tmp_path) == "Pacific/Kiritimati"
    assert rs.read_schedule(tmp_path).times() == ["06:30", "18:30"]


def test_an_ingest_start_counts_its_run_in_the_mailboxs_zone_unless_one_is_given(tmp_path):
    """`write-status --ingest start --only` keeps the ingest-only runs of the household's day: the mailbox's zone, so
    in Kiritimati a run at 23:00 its time yesterday is dropped where UTC would still count it today."""
    rs.write_schedule(tmp_path, rs.Schedule("Pacific/Kiritimati", (1, 9, 17), 0))
    yesterday = wall("Pacific/Kiritimati", 2026, 8, 11, 23, 0)                 # 09:00 UTC, Aug 11
    now = wall("Pacific/Kiritimati", 2026, 8, 12, 1, 0)                        # 11:00 UTC, Aug 11
    rs.write_status(tmp_path, ingest_runs=[yesterday])
    rs._main(["write-status", "--dir", str(tmp_path), "--ingest", "start", "--only", "--at", repr(now)])
    assert rs.read_status(tmp_path)["ingest_runs"] == [now]
    rs.write_status(tmp_path, ingest_runs=[yesterday])
    rs._main(["write-status", "--dir", str(tmp_path), "--ingest", "start", "--only", "--at", repr(now), "--tz", "UTC"])
    assert rs.read_status(tmp_path)["ingest_runs"] == [yesterday, now]


def test_unknown_timezone_falls_back_to_utc_instead_of_raising(capsys):
    assert rs.local_day(0.0, "Mars/Olympus") == dt.date(1970, 1, 1)
    assert "unknown time zone 'Mars/Olympus'" in capsys.readouterr().err


def test_utc_iso_is_unambiguous():
    assert rs.utc_iso(ny(2026, 8, 10, 17, 0)) == "2026-08-10T21:00:00Z"


# --------------------------------------------------------------------- ledger

def test_ledger_roundtrips_and_prunes_to_the_window(tmp_path):
    now = ny(2026, 8, 11, 12, 0)
    rs.save_ledger(tmp_path, [now - 10, now - 3 * 86_400])
    assert len(rs.load_ledger(tmp_path)) == 2
    assert rs.prune(rs.load_ledger(tmp_path), now) == [now - 10]


def test_corrupt_ledger_self_heals_to_empty(tmp_path, capsys):
    (tmp_path / rs.LEDGER_FILE).write_text("{not json")
    assert rs.load_ledger(tmp_path) == []
    assert "corrupt/unreadable" in capsys.readouterr().err


def test_ledger_ignores_junk_entries(tmp_path):
    (tmp_path / rs.LEDGER_FILE).write_text(
        json.dumps({"version": 1, "manual_runs": [5.0, "nope", None, True, 1.0]}))
    assert rs.load_ledger(tmp_path) == [1.0, 5.0]


# ------------------------------------------------------------------- liveness

def _hold_lock(path: Path) -> subprocess.Popen:
    """Hold an exclusive flock in another process, and don't return until it
    actually has it (the child writes a byte once locked — no sleep-polling)."""
    code = ("import fcntl,sys;f=open(sys.argv[1],'w');fcntl.flock(f,fcntl.LOCK_EX);"
            "sys.stdout.write('x');sys.stdout.flush();sys.stdin.read()")
    p = subprocess.Popen([sys.executable, "-c", code, str(path)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert p.stdout.read(1) == b"x"
    return p


def test_is_running_follows_the_lock_and_clears_when_the_holder_dies(tmp_path):
    lock = tmp_path / rs.LOCK_FILE
    assert rs.is_running(lock) is False          # no lock file yet
    holder = _hold_lock(lock)
    try:
        assert rs.is_running(lock) is True
    finally:
        holder.kill()
        holder.wait()
    # SIGKILL: the kernel drops the lock, which is the whole point of probing it
    # rather than trusting a {"state":"running"} file that nobody got to update.
    assert rs.is_running(lock) is False


def test_is_stalled_only_when_the_lock_is_free_and_it_started_long_ago(tmp_path):
    lock = tmp_path / rs.LOCK_FILE
    now = ny(2026, 8, 11, 12, 0)
    old = {"state": "running", "started_at": now - 30 * 60}
    assert rs.is_stalled(old, lock, now) is True
    # inside the spawn window: the refresher wrote `running` and the refresh.sh
    # it launched has not reached its flock yet — not a death
    assert rs.is_stalled({"state": "running", "started_at": now - 5}, lock, now) is False
    assert rs.is_stalled({"state": "running", "started_at": now - 91}, lock, now) is True
    assert rs.is_stalled({"state": "done", "started_at": now - 30 * 60}, lock, now) is False

    holder = _hold_lock(lock)
    try:
        assert rs.is_stalled(old, lock, now) is False   # it is genuinely alive
    finally:
        holder.kill()
        holder.wait()


# ------------------------------------------------------- request / status docs

def test_queued_age_is_none_once_the_refresher_claims_the_nonce(tmp_path):
    now = ny(2026, 8, 11, 12, 0)
    rs.write_request(tmp_path, nonce="n1", now=now - 30)
    req = rs.read_request(tmp_path)
    assert rs.queued_age(req, {}, now) == 30
    assert rs.queued_age(req, {"request_nonce": "n1"}, now) is None
    assert rs.queued_age(req, {"request_nonce": "other"}, now) == 30
    assert rs.queued_age(None, {}, now) is None


def test_already_satisfied_only_for_a_run_that_started_after_the_request():
    req = {"nonce": "n1", "requested_at": 1000.0}
    assert rs.already_satisfied(req, {"state": "done", "started_at": 1001.0}) is True
    assert rs.already_satisfied(req, {"state": "done", "started_at": 999.0}) is False
    assert rs.already_satisfied(req, {"state": "running", "started_at": 1001.0}) is False
    # a covering run that FAILED still answers the request — re-running on the
    # spot would just fail again while spending another SimpleFIN request
    assert rs.already_satisfied(
        req, {"state": "done", "started_at": 1001.0, "rc": 2}) is True


def test_running_status_clears_the_previous_runs_outcome(tmp_path):
    rs.write_status(tmp_path, state="done", rc=2, steps_failed=["ingest"],
                    request_nonce="old")
    rs._main(["write-status", "--dir", str(tmp_path), "--state", "running",
              "--trigger", "manual", "--nonce", "new"])
    doc = rs.read_status(tmp_path)
    assert doc["state"] == "running"
    assert doc["rc"] is None and doc["steps_failed"] == []
    assert doc["request_nonce"] == "new"


def test_scheduled_run_does_not_inherit_a_stale_manual_nonce(tmp_path):
    """Otherwise the browser watching for its own nonce would see the next cron
    run finish and claim its manual refresh had succeeded."""
    rs.write_status(tmp_path, state="done", request_nonce="mine")
    rs._main(["write-status", "--dir", str(tmp_path), "--state", "running",
              "--trigger", "schedule", "--nonce", ""])
    assert rs.read_status(tmp_path)["request_nonce"] is None


def test_done_status_records_failures_and_warnings(tmp_path):
    rs._main(["write-status", "--dir", str(tmp_path), "--state", "done",
              "--trigger", "schedule", "--rc", "2",
              "--failed", "SimpleFIN-fetch ingest",
              "--warnings", "Example Payroll Plus|Example Bank"])
    doc = rs.read_status(tmp_path)
    assert doc["rc"] == 2
    assert doc["steps_failed"] == ["SimpleFIN-fetch", "ingest"]
    assert doc["warnings"] == ["Example Payroll Plus", "Example Bank"]
    assert doc["finished_at_iso"].endswith("Z")


# --------------------------------------------------------------- the feed's errors

def warn_lines(capsys, doc: dict) -> str:
    """What the fetch prints for an answer's error list (etl/simplefin.py's own formatter, captured)."""
    sf._warn_errors(doc)
    return capsys.readouterr().err


def test_each_feed_error_names_its_connection_and_a_needed_sign_in_is_listed(capsys):
    """A version-2 answer: the con.auth entry asks for a sign-in at its bank, the act.failed one is only an error, and
    one that names no connection is the feed provider's. The code and the trailing id are left out of the message."""
    text = warn_lines(capsys, {
        "errlist": [{"code": "con.auth", "msg": "Sign in again", "conn_id": "CON-fake-0001"},
                    {"code": "act.failed", "msg": "Failed to get account information. Try again later.",
                     "conn_id": "CON-fake-0002", "account_id": "ACT-fake-0003"},
                    {"code": "gen.auth", "msg": "The access token was revoked."},
                    {"code": "con.auth", "msg": "", "conn_id": "CON-fake-0004"}],
        "connections": [{"conn_id": "CON-fake-0001", "name": "Sample Bank"},
                        {"conn_id": "CON-fake-0002", "name": "Sample Credit Union"}]})
    errors, reauth = rs.feed_report("some other line\n" + text + "ingest output\n")
    assert errors == ["Sample Bank: Sign in again",
                      "Sample Credit Union: Failed to get account information. Try again later.",
                      f"{rs.FEED_PROVIDER}: The access token was revoked.",
                      "CON-fake-0004: it needs attention"]
    assert reauth == ["Sample Bank", "CON-fake-0004"]


def test_a_version_1_error_naming_a_connection_asks_for_a_sign_in():
    """Version 1's errors are text; one that names a connection is the feed's word for a connection that needs its
    household to sign in again, as the refresh's alert has always read it."""
    errors, reauth = rs.feed_report("  WARN simplefin: Connection to Example Bank may need attention. Auth required\n"
                                    "  WARN simplefin: Connection to Example Bank may need attention.\n"
                                    "  WARN simplefin: The bridge is down for maintenance\n")
    assert errors == ["Example Bank: Auth required", "Example Bank: it needs attention",
                      f"{rs.FEED_PROVIDER}: The bridge is down for maintenance"]
    assert reauth == ["Example Bank"]


def test_feed_errors_are_bounded_plain_and_free_of_a_urls_password(capsys):
    """At most MAX_FEED_ERRORS, each once; a message past MAX_FEED_TEXT is cut; a control character never reaches it;
    an address's user and password are taken out."""
    many = [{"code": "act.failed", "msg": f"Account {i} failed", "conn_id": "CON-fake-0001"} for i in range(15)]
    text = warn_lines(capsys, {"errlist": [*many, many[0]], "connections": [{"conn_id": "CON-fake-0001",
                                                                             "name": "Sample Bank"}]})
    errors, reauth = rs.feed_report(text)
    assert errors == [f"Sample Bank: Account {i} failed" for i in range(rs.MAX_FEED_ERRORS)] and reauth == []
    errors, _ = rs.feed_report("WARN simplefin: Connection to Sample\x1b[31m Bank may need attention. [con.auth] "
                               "see https://user:secret@bridge.example.net/x " + "y" * 300 + " CON-1\n")
    [error] = errors
    assert error.startswith("Sample [31m Bank: see https://bridge.example.net/x yyy") and "secret" not in error
    assert len(error.split(": ", 1)[1]) == rs.MAX_FEED_TEXT and error.endswith("…") and "\x1b" not in error


@pytest.mark.parametrize("fetched", ["ok", "failed", "none"])
def test_a_finished_refresh_records_what_its_fetch_learned_of_the_feed(tmp_path, fetched):
    """--fetched none: the run read the files alone, so no feed error and no connection to sign in to. ok: this run's
    errors and connections. failed: this run's errors, and the connections the last good fetch named (a failed fetch
    learned nothing of them)."""
    d = tmp_path / "refresh"
    d.mkdir()
    rs.write_status(d, feed_errors=["Old Bank: old"], reauth_needed=["Old Bank"])
    out = tmp_path / "run-output"
    out.write_text("  WARN simplefin: Connection to Sample Bank may need attention. [con.auth] Sign in again CON-1\n")
    rs._main(["write-status", "--dir", str(d), "--state", "done", "--rc", "0" if fetched != "failed" else "1",
              "--fetched", fetched, "--feed-lines", str(out)])
    doc = rs.read_status(d)
    assert doc["files_only"] is (fetched == "none")
    assert (doc["feed_errors"], doc["reauth_needed"]) == {
        "ok": (["Sample Bank: Sign in again"], ["Sample Bank"]),
        "failed": (["Sample Bank: Sign in again"], ["Old Bank"]),
        "none": ([], []),
    }[fetched]


def test_a_refresh_told_nothing_of_its_fetch_leaves_the_feed_fields_alone(tmp_path):
    """An interrupted run, or a caller that does not say, writes none of them."""
    rs.write_status(tmp_path, feed_errors=["Old Bank: old"], reauth_needed=["Old Bank"], files_only=False)
    for state in ("done", "interrupted"):
        rs._main(["write-status", "--dir", str(tmp_path), "--state", state, "--rc", "0"])
        doc = rs.read_status(tmp_path)
        assert (doc["feed_errors"], doc["reauth_needed"], doc["files_only"]) == (["Old Bank: old"], ["Old Bank"], False)


def test_the_runs_output_is_never_read_through_a_link(tmp_path, capsys):
    """The feed lines come from the run's own temporary file: a link given in its place is read as no output."""
    target = tmp_path / "elsewhere"
    target.write_text("  WARN simplefin: Connection to Planted Bank may need attention. [con.auth] x CON-1\n")
    link = tmp_path / "run-output"
    link.symlink_to(target)
    d = tmp_path / "refresh"
    d.mkdir()
    rs._main(["write-status", "--dir", str(d), "--state", "done", "--rc", "0", "--fetched", "ok",
              "--feed-lines", str(link)])
    doc = rs.read_status(d)
    assert (doc["feed_errors"], doc["reauth_needed"]) == ([], [])
    assert "cannot be read" in capsys.readouterr().err


def test_status_survives_a_missing_or_corrupt_file(tmp_path):
    assert rs.read_status(tmp_path) == {}
    (tmp_path / rs.STATUS_FILE).write_text("[]")
    assert rs.read_status(tmp_path) == {}      # a list is not a status doc


# ---------------------------------------------------- the ingest without a fetch
# The store touches ingest_wanted (a manual asset changed, or someone asked for a rebuild); the runner runs
# `refresh.sh ingest` once the marker has settled, at most MAX_INGEST_PER_DAY times a local day. Nothing here fetches.

def test_ingest_is_due_only_after_the_marker_has_settled(tmp_path):
    """No marker, nothing wanted; a fresh one waits INGEST_SETTLE_S from its time, two seconds after the touch, so
    several saves in a row cause one ingest; then it runs. Each touch moves the mtime, which restarts the wait."""
    now = ny(2026, 8, 11, 12, 0)
    assert rs.ingest_due(tmp_path, now, TZ) == "no"
    rs.touch_ingest_marker(tmp_path, now)
    assert (tmp_path / rs.INGEST_MARKER).exists()
    assert rs.INGEST_SETTLE_S == 60
    assert rs.ingest_due(tmp_path, now, TZ) == "wait"               # a marker in the future waits like a fresh one
    assert rs.ingest_due(tmp_path, now + 61, TZ) == "wait"
    assert rs.ingest_due(tmp_path, now + 62, TZ) == "run"
    rs.touch_ingest_marker(tmp_path, now + 50)                 # another save before it ran
    assert rs.ingest_due(tmp_path, now + 62, TZ) == "wait"
    assert rs.ingest_due(tmp_path, now + 111, TZ) == "wait"
    assert rs.ingest_due(tmp_path, now + 112, TZ) == "run"
    assert (tmp_path / rs.INGEST_MARKER).stat().st_mtime == now + 52


def test_a_marker_older_than_the_last_ingest_start_is_satisfied(tmp_path):
    """An ingest that started after the marker's time read what the touch announced, whatever started it (an
    ingest-only run or a refresh's own ingest step): the marker is never removed, only outgrown."""
    touched = ny(2026, 8, 11, 12, 0)
    later = touched + 3600
    rs.touch_ingest_marker(tmp_path, touched)
    marker = rs.marker_time(tmp_path)
    assert marker == touched + 2
    rs.write_status(tmp_path, ingest_started_at=marker + 1)
    assert rs.ingest_due(tmp_path, later, TZ) == "no"
    rs.write_status(tmp_path, ingest_started_at=marker)         # the same instant: the marker is not newer
    assert rs.ingest_due(tmp_path, later, TZ) == "no"
    rs.write_status(tmp_path, ingest_started_at=marker - 1)
    assert rs.ingest_due(tmp_path, later, TZ) == "run"
    assert rs.ingest_wanted(rs.marker_time(tmp_path), rs.read_status(tmp_path)) is True
    assert rs.ingest_wanted(None, {}) is False
    assert rs.ingest_wanted(touched, {"ingest_started_at": True}) is True      # junk is no start


def test_a_save_in_the_second_an_ingest_starts_is_still_wanted(tmp_path):
    """The marker's time is a whole second, one to two seconds after the touch: a filesystem that keeps one- or
    two-second mtimes truncates it to a time still after the touch, so an ingest that started half a second after the
    save (and so may not have read it) never answers it."""
    t = ny(2026, 8, 11, 12, 0) + 0.25
    rs.touch_ingest_marker(tmp_path, t)
    marker = rs.marker_time(tmp_path)
    assert marker == math.floor(t) + 2 and marker == int(marker)
    status = {"ingest_started_at": t + 0.5, "ingest_finished_at": t + 10, "ingest_rc": 0}
    assert rs.ingest_wanted(marker, status) is True
    for kept in (1, 2):                                          # what a coarse filesystem keeps of it
        assert rs.ingest_wanted(marker - marker % kept, status) is True
    before = time.time()
    rs.touch_ingest_marker(tmp_path)                            # the current time when none is given
    assert before + 1 <= rs.marker_time(tmp_path) <= time.time() + 2


@pytest.mark.parametrize(("rc", "wanted"), [(0, False), (1, False), (rs.INGEST_TIMED_OUT_RC, False),
                                            (rs.INGEST_STOPPED_RC, True), (rs.INGEST_INTERRUPTED_RC, True)])
def test_an_ingest_that_ended_interrupted_does_not_answer_the_marker(rc, wanted):
    """A marker older than the last ingest's start is answered by it, unless that ingest ended interrupted: stopped from
    outside (143) or found dead (INGEST_INTERRUPTED_RC) read nothing reliably, so the marker's ingest is wanted again.
    One that succeeded, failed (1) or timed out (INGEST_TIMED_OUT_RC) answers it: a broken or hung ingest is not
    looped."""
    marker = 1000.0
    ended = {"ingest_started_at": 1100.0, "ingest_finished_at": 1150.0, "ingest_rc": rc}
    assert rs.ingest_wanted(marker, ended) is wanted
    assert rs.ingest_interrupted(ended) is wanted
    assert rs.ingest_wanted(None, ended) is False                                  # no marker: nothing wanted
    still_open = {"ingest_started_at": 1100.0, "ingest_finished_at": None, "ingest_rc": rc}
    assert rs.ingest_wanted(marker, still_open) is False                           # open: answered, as before
    assert rs.ingest_wanted(marker, {**ended, "ingest_rc": True}) is False         # junk is no exit code


def test_an_interrupted_ingest_is_run_again_with_no_new_wait(tmp_path):
    """The marker is old, so an ingest that ended stopped is wanted at once (no settle), within the daily cap."""
    touched = ny(2026, 8, 11, 12, 0)
    rs.touch_ingest_marker(tmp_path, touched)
    rs.write_status(tmp_path, ingest_started_at=touched + 600, ingest_finished_at=touched + 610,
                    ingest_rc=rs.INGEST_STOPPED_RC, ingest_runs=[touched + 600])
    assert rs.ingest_due(tmp_path, touched + 611, TZ) == "run"
    rs.write_status(tmp_path, ingest_runs=[touched + k for k in range(rs.MAX_INGEST_PER_DAY)])
    assert rs.ingest_due(tmp_path, touched + 611, TZ) == "cap"


def test_the_forty_ninth_ingest_of_a_local_day_waits_for_midnight(tmp_path):
    """MAX_INGEST_PER_DAY ingest-only runs per LOCAL day, the day the household's zone gives (as the manual-refresh cap
    buckets): at 23:00 in New York the UTC day has rolled over, and the cap must still hold until local midnight."""
    assert rs.MAX_INGEST_PER_DAY == 48
    day_start = ny(2026, 8, 10, 0, 0)
    runs = [day_start + k * 60 for k in range(48)]
    rs.write_status(tmp_path, ingest_runs=runs, ingest_started_at=runs[-1])
    rs.touch_ingest_marker(tmp_path, ny(2026, 8, 10, 22, 0))
    late = ny(2026, 8, 10, 23, 0)
    assert rs.ingest_due(tmp_path, late, TZ) == "cap"
    assert rs.ingest_runs_today(rs.read_status(tmp_path), late, TZ) == 48
    assert rs.ingest_due(tmp_path, ny(2026, 8, 11, 0, 0), TZ) == "run"   # local midnight
    rs.write_status(tmp_path, ingest_runs=runs[:47])
    assert rs.ingest_due(tmp_path, late, TZ) == "run"
    rs.write_status(tmp_path, ingest_runs=[*runs[:47], "junk", None, True])
    assert rs.ingest_due(tmp_path, late, TZ) == "run"                    # junk entries count for nothing


def test_the_ingest_fields_are_written_without_touching_the_last_refresh(tmp_path):
    """`write-status --ingest` writes only the ingest fields: the last refresh's state, trigger, nonce, rc and failed
    steps stay as the dashboard shows them. An ingest-only start (`--only`) is counted in the local day's ingest_runs,
    once even when the runner already recorded it (`--at` names that start), and yesterday's runs are pruned."""
    rs._main(["write-status", "--dir", str(tmp_path), "--state", "done", "--trigger", "manual", "--nonce", "n1",
              "--rc", "1", "--failed", "SimpleFIN-fetch", "--warnings", "Example Bank"])
    before = rs.read_status(tmp_path)
    yesterday = ny(2026, 8, 10, 9, 0)
    claimed = ny(2026, 8, 11, 9, 0) + 0.123456789
    rs.write_status(tmp_path, ingest_runs=[yesterday, claimed])
    rs._main(["write-status", "--dir", str(tmp_path), "--ingest", "start", "--only", "--at", repr(claimed),
              "--tz", TZ])
    doc = rs.read_status(tmp_path)
    assert {k: doc[k] for k in before} == before
    assert doc["ingest_started_at"] == claimed and doc["ingest_started_at_iso"].endswith("Z")
    assert doc["ingest_finished_at"] is None and doc["ingest_rc"] is None
    assert doc["ingest_runs"] == [claimed]                                  # counted once; yesterday's pruned
    assert rs.ingest_open(doc) is True
    rs._main(["write-status", "--dir", str(tmp_path), "--ingest", "done", "--rc", "2"])
    doc = rs.read_status(tmp_path)
    assert {k: doc[k] for k in before} == before
    assert doc["ingest_rc"] == 2 and doc["ingest_finished_at"] >= claimed
    assert rs.ingest_open(doc) is False
    # a refresh's own ingest step records its start and end but is no ingest-only run
    rs._main(["write-status", "--dir", str(tmp_path), "--ingest", "start"])
    doc = rs.read_status(tmp_path)
    assert doc["ingest_runs"] == [claimed] and doc["ingest_started_at"] > claimed and rs.ingest_open(doc)


def test_an_ingest_only_run_is_told_apart_from_a_refresh_by_its_mark():
    """Both hold the refresh lock. The refresher marks its ingest-only run (`ingest_only`) for the whole time it holds
    the lock, so the run reads as one before its ingest starts and after it ended; nothing else is one."""
    closed = {"ingest_started_at": 100.0, "ingest_finished_at": 101.0, "ingest_rc": 1}
    assert rs.ingest_only_in_flight({"state": "done", "ingest_only": True, **closed}) is True
    assert rs.ingest_only_in_flight({"state": "done", "ingest_only": True}) is True
    assert rs.ingest_only_in_flight({"state": "done", "ingest_only": False,
                                     "ingest_started_at": 100.0, "ingest_finished_at": None}) is False
    assert rs.ingest_only_in_flight({"state": "running", "ingest_only": False}) is False
    assert rs.ingest_only_in_flight({"state": "done", "ingest_only": 1}) is False             # junk is no mark
    assert rs.ingest_only_in_flight({}) is False
    assert rs.ingest_open({"ingest_started_at": 100.0, "ingest_finished_at": 99.0}) is True   # an older run's end


def test_a_refresh_starting_clears_a_stale_ingest_only_mark(tmp_path):
    """A mark a killed refresher left true cannot make a refresh read as an ingest-only run: `write-status --state
    running` clears it."""
    rs.write_status(tmp_path, state="done", ingest_only=True)
    rs._main(["write-status", "--dir", str(tmp_path), "--state", "running"])
    doc = rs.read_status(tmp_path)
    assert (doc["state"], doc["ingest_only"]) == ("running", False)


def test_touching_the_marker_never_rewrites_it(tmp_path):
    """The marker is touched in place (created when absent), never replaced or emptied: only its mtime carries
    meaning, and the runner reads that."""
    rs.touch_ingest_marker(tmp_path, 1000.0)
    marker = tmp_path / rs.INGEST_MARKER
    inode = marker.stat().st_ino
    rs.touch_ingest_marker(tmp_path, 2000.0)
    assert (marker.stat().st_ino, marker.stat().st_mtime, marker.read_bytes()) == (inode, 2002.0, b"")
    assert rs.marker_time(tmp_path) == 2002.0
    assert rs.marker_time(tmp_path / "nope") is None


def test_a_queued_request_waiting_on_an_ingest_only_run_is_not_a_dead_worker():
    """While the worker runs an ingest it cannot claim a refresh request; the request waits for it and is not
    reported as a dead worker however long the ingest takes."""
    d = decide(queued_age_s=rs.WORKER_STALE_S + 60, worker_busy=True)
    assert (d.allow, d.reason) == (False, "already_running")
    assert decide(queued_age_s=rs.WORKER_STALE_S + 60).reason == "worker_unavailable"


# ------------------------------------------------------------ quota backstop

def test_count_recent_fetches_reads_the_snapshots_themselves(tmp_path):
    now = ny(2026, 8, 11, 12, 0)
    for name, age in [("accounts-20260811T120000Z.json", 60),
                      ("accounts-20260811T090000Z.json", 3 * 3600),
                      ("accounts-20260809T090000Z.json", 2 * 86_400),
                      ("prices-20260811T120000Z.json", 60)]:     # not a SimpleFIN pull
        p = tmp_path / name
        p.write_text("{}")
        os.utime(p, (now - age, now - age))
    assert rs.count_recent_fetches(tmp_path, now) == 2
    assert rs.count_recent_fetches(tmp_path / "nope", now) == 0


# -------------------------------------------------------------- shell syntax

@pytest.mark.parametrize("script", ["refresh.sh"])
def test_deploy_shell_scripts_parse(script):
    """ruff lints the Python in deploy/; nothing else syntax-checks the shell,
    and a typo in refresh.sh only shows up at 01:00 the next morning."""
    r = subprocess.run(["bash", "-n", str(ROOT / "deploy" / script)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ------------------------------------------------------------- where the directories are

# each directory the store and the worker take: its variable, the part below that variable's directory, its place
# under FF_HOME, its legacy place (the old compose file's mounts; the sign-in's two sit where etl/config.py Paths puts
# them under the containers' /app) and the variable naming the folder that holds it with its siblings
DIRECTORIES = {
    "documents": ("FF_STATE_FAMILY_DIR", "", "state/family", "/app/scenarios", "FF_STATE_DIR"),
    "refresh": ("FF_STATE_REFRESH_DIR", "", "state/refresh", "/app/var/refresh", ""),
    "engine": ("FF_STATE_ENGINE_DIR", "", "state/engine", "/app/var/refresh", ""),
    "snapshots": ("FF_STORE_DIR", "simplefin/snapshots", "store/simplefin/snapshots", "/app/simplefin/snapshots", ""),
    "secrets_web": ("FF_SECRETS_WEB_DIR", "", "secrets/web", "/app/secrets/web", ""),
    "auth": ("FF_STATE_AUTH_DIR", "", "state/auth", "/app/state/auth", ""),
}


def test_the_directories_are_the_six_the_programs_take():
    assert {name: (loc.variable, loc.below, loc.under_home, loc.legacy, loc.flat)
            for name, loc in rs.LOCATIONS.items()} == DIRECTORIES


def test_the_family_documents_follow_the_state_in_one_folder_after_their_own_variable(tmp_path, monkeypatch):
    """FF_STATE_DIR names the folder of the whole state (as etl/config.py Paths reads it): after FF_STATE_FAMILY_DIR,
    before FF_HOME."""
    monkeypatch.setenv("FF_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FF_STATE_DIR", str(tmp_path / "one"))
    monkeypatch.delenv("FF_STATE_FAMILY_DIR", raising=False)
    assert rs.locate("documents") == tmp_path / "one"
    monkeypatch.setenv("FF_STATE_FAMILY_DIR", str(tmp_path / "family"))
    assert rs.locate("documents") == tmp_path / "family"


def test_the_engine_is_the_mailbox_unless_its_own_variable_or_ff_home_names_one(tmp_path, monkeypatch):
    for v in ("FF_HOME", "FF_STATE_ENGINE_DIR", "FF_STATE_DIR"):
        monkeypatch.delenv(v, raising=False)
    mailbox = tmp_path / "mailbox"
    assert rs.engine_dir(mailbox) == mailbox
    monkeypatch.setenv("FF_STATE_DIR", str(tmp_path / "one"))                  # the ETL's one folder moves nothing here
    assert rs.engine_dir(mailbox) == mailbox
    monkeypatch.setenv("FF_HOME", str(tmp_path / "home"))
    assert rs.engine_dir(mailbox) == tmp_path / "home" / "state" / "engine"
    monkeypatch.setenv("FF_STATE_ENGINE_DIR", str(tmp_path / "engine"))
    assert rs.engine_dir(mailbox) == tmp_path / "engine"
    assert rs.engine_dir(mailbox, str(tmp_path / "given")) == tmp_path / "given"


@pytest.mark.parametrize("source", ["argument", "variable", "home", "legacy"])
@pytest.mark.parametrize("name", list(DIRECTORIES))
def test_each_directory_takes_the_first_source_that_is_set(tmp_path, monkeypatch, name, source):
    """Every source below the one under test is set as well, so each case also proves what that source beats."""
    variable, below, under_home, legacy, flat = DIRECTORIES[name]
    home, named, given = tmp_path / "home", tmp_path / "named", tmp_path / "given"
    for v in ("FF_HOME", variable, flat or variable):
        monkeypatch.delenv(v, raising=False)
    if source != "legacy":
        monkeypatch.setenv("FF_HOME", str(home))
    if source in ("argument", "variable"):
        monkeypatch.setenv(variable, str(named))
    expected = {"argument": given, "variable": named / below if below else named, "home": home / under_home,
                "legacy": Path(legacy)}[source]
    assert rs.locate(name, str(given) if source == "argument" else None) == expected


def test_without_a_fallback_a_directory_nothing_names_is_none(tmp_path, monkeypatch):
    """The store's refresh directory: with no argument, variable or FF_HOME, /api/refresh stays off."""
    for v in ("FF_HOME", "FF_STATE_REFRESH_DIR"):
        monkeypatch.delenv(v, raising=False)
    assert rs.locate("refresh", None, fallback=False) is None
    monkeypatch.setenv("FF_HOME", str(tmp_path))
    assert rs.locate("refresh", None, fallback=False) == tmp_path / "state" / "refresh"


def test_an_empty_argument_or_variable_counts_as_unset_and_a_relative_variable_is_taken_from_the_cwd(tmp_path,
                                                                                                   monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FF_HOME", "")
    monkeypatch.setenv("FF_STATE_REFRESH_DIR", "")
    assert rs.locate("refresh", "") == Path("/app/var/refresh")
    monkeypatch.setenv("FF_HOME", "home")
    assert rs.locate("refresh") == tmp_path / "home" / "state" / "refresh"
    monkeypatch.setenv("FF_STATE_REFRESH_DIR", "here/../there")
    assert rs.locate("refresh") == tmp_path / "there"
    assert rs.locate("refresh", "as/given") == Path("as/given")    # an argument is used as it is given


# ------------------------------------------------------------- no household's zone, and no TZ

# the text files of the refresh's code, its tests, the validator and the key reference
TEXT_SUFFIXES = (".py", ".sh", ".md", ".html", ".js", ".css", ".json", ".svg", ".webmanifest")


def zone_scanned() -> list[Path]:
    found = [*(ROOT / "deploy").rglob("*"), *(ROOT / "tests").glob("test_refresh*"),
             *(ROOT / "tests").glob("test_scenario_store*"), ROOT / "etl" / "validate.py",
             ROOT / "docs" / "configuration.md"]
    return [p for p in found if p.is_file() and (p.suffix in TEXT_SUFFIXES or p.name.startswith(("Dockerfile",
                                                                                                   "Caddyfile")))]


_ZONE_NAME = re.compile(r"\b(?:Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Brazil|Canada|Chile|Etc|Europe|"
                        r"Indian|Mexico|Pacific|US)/[A-Za-z_]+(?:/[A-Za-z_]+)?")


def test_no_zone_but_utc_new_york_and_kiritimati_is_named_there():
    """The zones these files name are the three the program's tests use, by name or by abbreviation (every other
    zone's abbreviations, from the zone database itself)."""
    allowed = {"UTC", "America/New_York", "Pacific/Kiritimati"}
    keep = {ZoneInfo(z).tzname(dt.datetime(2026, m, 1)) for z in allowed for m in (1, 7)}
    abbreviations = set()
    for zone in available_timezones():
        with contextlib.suppress(Exception):
            abbreviations |= {ZoneInfo(zone).tzname(dt.datetime(2026, m, 1)) for m in (1, 7)}
    banned = sorted(a for a in abbreviations - keep if a and a.isalpha() and a.isupper() and len(a) >= 3)
    assert keep == {"UTC", "EST", "EDT", "+14"} and len(banned) > 30, (keep, len(banned))
    word = re.compile(r"\b(?:" + "|".join(banned) + r")\b")
    named = {}
    for path in zone_scanned():
        text = path.read_text("utf-8")
        found = {z for z in _ZONE_NAME.findall(text) if z not in allowed} | set(word.findall(text))
        if found:
            named[path.relative_to(ROOT).as_posix()] = sorted(found)
    assert named == {}


def test_nothing_under_deploy_reads_a_tz_variable():
    """The household's zone decides every day and hour; no process's TZ does (refresh.sh's log stamps are UTC)."""
    reads = re.compile(r"""environ(?:\.get)?\s*[(\[]\s*["']TZ["']|getenv\(\s*["']TZ["']|\$\{?TZ\b""")
    assert [p.name for p in (ROOT / "deploy").rglob("*") if p.suffix in (".py", ".sh") and reads.search(
        p.read_text("utf-8"))] == []
    assert re.search(r"^ts\(\)\s+\{ date -u ", (ROOT / "deploy" / "refresh.sh").read_text("utf-8"), re.MULTILINE)

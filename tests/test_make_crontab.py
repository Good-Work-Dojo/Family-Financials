# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""deploy/make_crontab.py — the scheduler's crontab from household.yaml, and the schedule it leaves in the mailbox.

The crontab is checked as text here, in each of the three zones the tests use and with a minute chosen at random;
tools/verify/ci.sh has supercronic itself read one in the runtime image. The `--run` form runs against a stub
supercronic on PATH, so nothing is scheduled."""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

import make_crontab as mc  # tests/conftest.py puts deploy/ on sys.path
import pytest
import refresh_state as rs
from conftest import ROOT, write_household

from etl import validate as v
from etl.config import Paths

SCRIPT = ROOT / "deploy" / "make_crontab.py"
ZONES = ["UTC", "America/New_York", "Pacific/Kiritimati"]


def config_with(tmp_path: Path, **household) -> Path:
    """A config folder whose household.yaml is the synthetic one with `household`'s keys over it."""
    d = tmp_path / "config"
    write_household(d, **household)
    return d


def text_for(tmp_path: Path, **household) -> str:
    return mc.crontab_text(mc.household_schedule(Paths(config_with(tmp_path, **household))))


@pytest.mark.parametrize("zone", ZONES)
def test_the_crontab_runs_the_refresh_at_the_households_hours_on_its_clock(tmp_path, zone):
    """One CRON_TZ line naming the household's zone (supercronic schedules every line on that clock), then one line
    per hour, in order, at the household's minute, each running the refresh.sh beside the script."""
    minute = random.randint(0, 59)
    text = text_for(tmp_path, timezone=zone, refresh={"hours": [17, 1, 9], "minute": minute})
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    script = ROOT / "deploy" / "refresh.sh"
    assert lines == [f"CRON_TZ={zone}", f"{minute} 1 * * * {script}", f"{minute} 9 * * * {script}",
                     f"{minute} 17 * * * {script}"], minute
    assert text.endswith("\n") and mc.REFRESH_SCRIPT == script


def test_without_a_refresh_block_the_defaults_hold(tmp_path):
    """No `refresh:` (or a null one, or one with neither key) is hours 1, 9 and 17 at minute 0."""
    def schedule_of(**household) -> rs.Schedule:
        return mc.household_schedule(Paths(config_with(tmp_path, **household)))

    defaults = rs.Schedule("America/New_York", (1, 9, 17), 0)
    assert v.DEFAULT_REFRESH_HOURS == (1, 9, 17) and v.DEFAULT_REFRESH_MINUTE == 0
    for refresh in ({}, None, {"hours": None, "minute": None}):
        assert schedule_of(timezone="America/New_York", refresh=refresh) == defaults, refresh
    assert schedule_of(timezone="America/New_York") == defaults
    assert schedule_of(timezone="UTC", refresh={"minute": 45}) == rs.Schedule("UTC", (1, 9, 17), 45)
    assert schedule_of(timezone="UTC", refresh={"hours": [0]}) == rs.Schedule("UTC", (0,), 0)


def test_the_bounds_are_the_validators():
    """The store and the worker cannot import the ETL, so refresh_state keeps its own copy of the most hours a day."""
    assert rs.MAX_SCHEDULE_HOURS == v.MAX_REFRESH_HOURS == 6


def run(tmp_path: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    clean = {k: val for k, val in os.environ.items() if not k.startswith("FF_")}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60,
                          cwd=tmp_path, env={**clean, **env})


def test_printed_as_the_household_states_it(tmp_path):
    config = config_with(tmp_path, timezone="Pacific/Kiritimati", refresh={"hours": [6], "minute": 7})
    r = run(tmp_path, FF_CONFIG_DIR=str(config))
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[1:] == ["CRON_TZ=Pacific/Kiritimati", f"7 6 * * * {ROOT / 'deploy' / 'refresh.sh'}"]


@pytest.mark.parametrize("household, lines", [
    ({"timezone": "Mars/Olympus"},
     ["household.yaml: timezone: unknown zone 'Mars/Olympus' — use an IANA name such as America/New_York"]),
    ({"timezone": None}, ["household.yaml: timezone: missing — set the household's IANA zone name, e.g. "
                          "America/New_York"]),
    ({"refresh": {"hours": [1, 9, 25], "minute": 60}},
     ["household.yaml: refresh.hours[2]: expected a whole hour from 0 to 23 — found a number; write an hour of the "
      "day such as 9 or 17",
      "household.yaml: refresh.minute: expected a whole minute from 0 to 59 — found a number; write a minute such as "
      "0 or 30, or leave it out (0)"]),
    ({"refresh": [1, 9]}, ["household.yaml: refresh: expected a mapping — write `refresh: {hours: [1, 9, 17], "
                           "minute: 0}`, or leave it out"]),
])
def test_a_household_yaml_that_cannot_give_a_schedule_stops_it_with_the_validators_lines(tmp_path, household, lines):
    """Exit 1 with nothing printed on standard output: the validator's own lines (`python -m etl validate` prints the
    same ones), then the one line saying the schedule does not start."""
    config = config_with(tmp_path, **household)
    r = run(tmp_path, FF_CONFIG_DIR=str(config))
    assert (r.returncode, r.stdout) == (1, "")
    assert r.stderr.splitlines() == [*lines, "make_crontab: the scheduled refresh does not start until household.yaml "
                                             "is fixed"]


def test_a_missing_or_unreadable_household_yaml_stops_it_too(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    r = run(tmp_path, FF_CONFIG_DIR=str(config))
    assert r.returncode == 1 and r.stderr.startswith(f"household.yaml: not found in {config} — create it")
    (config / "household.yaml").write_text("timezone: [unclosed\n")
    r = run(tmp_path, FF_CONFIG_DIR=str(config))
    assert r.returncode == 1 and r.stderr.startswith("household.yaml: not valid YAML (line ")
    (config / "household.yaml").write_text("- a list\n")
    r = run(tmp_path, FF_CONFIG_DIR=str(config))
    assert r.returncode == 1 and r.stderr.startswith("household.yaml: top level: expected a mapping — write ")
    r = run(tmp_path, FF_HOME=str(tmp_path / "nowhere"))
    assert r.returncode == 1 and "FF_HOME names a folder with no config/ in it" in r.stderr


def test_only_the_schedules_keys_stop_it(tmp_path):
    """A problem elsewhere in household.yaml (a member without a role) is the ingest's to report: the schedule runs."""
    config = config_with(tmp_path, timezone="UTC", members=[{"name": "Alex"}])
    assert run(tmp_path, FF_CONFIG_DIR=str(config)).returncode == 0


# ------------------------------------------------------------------ --run: the scheduler's start

STUB = """#!/bin/sh
# a stand-in for supercronic: what it was given, and the crontab it was given
printf '%s\\n' "$@" > "$SEEN/args"
cp "$1" "$SEEN/crontab"
"""


def started(tmp_path: Path, home: Path) -> subprocess.CompletedProcess:
    binp, seen, tmp = tmp_path / "bin", tmp_path / "seen", tmp_path / "tmp"
    for d in (binp, seen, tmp):
        d.mkdir(exist_ok=True)
    (binp / "supercronic").write_text(STUB)
    (binp / "supercronic").chmod(0o755)
    return run(tmp_path, "--run", FF_HOME=str(home), PATH=f"{binp}:{os.environ['PATH']}", TMPDIR=str(tmp),
               SEEN=str(seen))


def test_run_writes_the_crontab_under_tmp_and_the_schedule_in_the_mailbox_then_becomes_supercronic(tmp_path):
    home = tmp_path / "home"
    write_household(home / "config", timezone="America/New_York", refresh={"hours": [17, 6], "minute": 25})
    (home / "state" / "refresh").mkdir(parents=True)
    r = started(tmp_path, home)
    assert r.returncode == 0, r.stderr
    [given] = (tmp_path / "seen" / "args").read_text().splitlines()
    assert Path(given).parent == tmp_path / "tmp" and Path(given).name.startswith("ff-crontab.")
    expected = mc.crontab_text(rs.Schedule("America/New_York", (6, 17), 25))
    assert (tmp_path / "seen" / "crontab").read_text() == expected
    mailbox = home / "state" / "refresh"
    assert json.loads((mailbox / rs.SCHEDULE_FILE).read_text()) == {
        "timezone": "America/New_York", "hours": [6, 17], "minute": 25}
    assert rs.read_schedule(mailbox) == rs.Schedule("America/New_York", (6, 17), 25)
    assert rs.household_zone(mailbox) == "America/New_York"
    assert "the refresh runs at 06:25, 17:25 (America/New_York)" in r.stderr


def test_run_with_a_broken_household_yaml_writes_nothing_and_starts_nothing(tmp_path):
    home = tmp_path / "home"
    write_household(home / "config", timezone="Mars/Olympus")
    (home / "state" / "refresh").mkdir(parents=True)
    r = started(tmp_path, home)
    assert r.returncode == 1 and "unknown zone 'Mars/Olympus'" in r.stderr
    assert list((tmp_path / "seen").iterdir()) == [] and list((tmp_path / "tmp").iterdir()) == []
    assert list((home / "state" / "refresh").iterdir()) == []


def test_run_replaces_a_link_planted_at_the_schedule_and_never_writes_through_it(tmp_path):
    """The store can write the mailbox, so a link may sit at schedule.json: the scheduler's write puts a file of its own
    at the name and leaves the link's target as it was."""
    home = tmp_path / "home"
    write_household(home / "config", timezone="UTC")
    mailbox = home / "state" / "refresh"
    mailbox.mkdir(parents=True)
    target = tmp_path / "kept"
    target.write_text("KEEP")
    (mailbox / rs.SCHEDULE_FILE).symlink_to(target)
    assert started(tmp_path, home).returncode == 0
    assert target.read_text() == "KEEP" and not (mailbox / rs.SCHEDULE_FILE).is_symlink()
    assert rs.read_schedule(mailbox) == rs.Schedule("UTC", (1, 9, 17), 0)


def test_run_without_a_writable_mailbox_still_starts_the_schedule(tmp_path):
    """A mailbox that cannot be written is said once: the schedule still runs, and the store reports none."""
    home = tmp_path / "home"
    write_household(home / "config", timezone="UTC")
    (home / "state").mkdir()
    (home / "state" / "refresh").write_text("a file where the mailbox should be")
    r = started(tmp_path, home)
    assert r.returncode == 0, r.stderr
    assert "the schedule could not be written" in r.stderr and (tmp_path / "seen" / "crontab").exists()

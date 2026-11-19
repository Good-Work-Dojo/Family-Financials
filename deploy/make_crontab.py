# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The scheduler's start: its crontab from household.yaml, the schedule in the refresh mailbox, then supercronic.

  python make_crontab.py          print the crontab
  python make_crontab.py --run    what the scheduler container runs: the crontab written to a new file in the
                                  temporary directory, the schedule written to the refresh mailbox (schedule.json,
                                  refresh_state.write_schedule: how the household's zone and hours reach the store,
                                  which cannot read the config), then this process becomes `supercronic <that file>`

A household.yaml that is missing or cannot give the schedule stops either form before anything is written: the
validator's lines on standard error and exit 1, so the scheduler container never runs, or reports healthy, on a
schedule the household did not write.

The crontab is supercronic's: a `CRON_TZ=<zone>` line, which supercronic's documentation says schedules the jobs in
that zone ("if you need your cron jobs to be scheduled in a timezone A ... add a CRON_TZ=A line to your crontab"; the
jobs it starts get it as their TZ too), then one line for each hour of household.yaml `refresh.hours`, at
`refresh.minute`, running the refresh.sh beside this file. Of the config it reads only household.yaml's `timezone` and
`refresh` (validate.timezone_problems, validate.refresh_problems), with the defaults validate.DEFAULT_REFRESH_HOURS and
DEFAULT_REFRESH_MINUTE: a problem anywhere else is the ingest's to report, and the scheduled runs are what report it. A
change to them takes effect when the scheduler restarts.

Daylight saving: supercronic's documentation says nothing of a day the clocks change. Each run is scheduled on the
zone's wall clock, so on such a day an hour that does not exist (America/New_York's 02:00 to 03:00 in March) may lose
its run, and an hour that repeats (its 01:00 to 02:00 in November, where the default hours' 1 o'clock run falls) may run
twice. Nothing more is done about it: supercronic waits for a job to end before it schedules that job again, the
refresh lock keeps two runs apart, and a second run is harmless (the rate limits hold, and the feed allows far more
requests a day than the schedule makes). UTC and Pacific/Kiritimati change no clocks.

The scheduler holds the ETL, so this imports its household readers (etl.config Paths, etl.validate); the store never
runs it."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

import refresh_state as rs  # deploy/ is on the path: the image's .pth, or this script's own directory
import yaml

from etl import validate as v
from etl.config import Paths

HOUSEHOLD = "household.yaml"
# the script every line runs: the refresh.sh installed beside this file
REFRESH_SCRIPT = Path(__file__).resolve().parent / "refresh.sh"
SUPERCRONIC = "supercronic"


def _household(paths: Paths) -> dict:
    """household.yaml's document, or ConfigError with the one problem that says why there is none to read."""
    if not paths.config_dir.is_dir():
        raise v.ConfigError([v.missing_config_dir_problem(paths.config_dir, paths.config_from)])
    path = paths.config_dir / HOUSEHOLD
    if not path.exists():
        raise v.ConfigError([v.missing_file_problem(HOUSEHOLD, paths.config_dir)])
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise v.ConfigError([v.yaml_problem(HOUSEHOLD, e)]) from None
    except (OSError, UnicodeDecodeError) as e:
        raise v.ConfigError([v.unreadable_problem(HOUSEHOLD, e)]) from None
    except (ValueError, OverflowError) as e:
        raise v.ConfigError([v.unbuildable_value_problem(HOUSEHOLD, path, e)]) from None
    if not isinstance(doc, dict):
        raise v.ConfigError([v.Problem(HOUSEHOLD, "", "top level: expected a mapping",
                                       "write version, members, joint_label and timezone as `key: value` lines")])
    return doc


def household_schedule(paths: Paths | None = None) -> rs.Schedule:
    """The scheduled refresh household.yaml states (its config directory found by Paths): its zone, its hours and its
    minute, each hour once and in order. ConfigError with every problem of those keys when it cannot give one."""
    doc = _household(paths or Paths())
    problems = [*v.refresh_problems(doc), *v.timezone_problems(doc)]
    if problems:
        raise v.ConfigError(problems)
    block = doc.get("refresh") or {}
    hours = block.get("hours")
    minute = block.get("minute")
    return rs.Schedule(doc["timezone"], tuple(sorted(v.DEFAULT_REFRESH_HOURS if hours is None else hours)),
                       v.DEFAULT_REFRESH_MINUTE if minute is None else minute)


def crontab_text(schedule: rs.Schedule, script: Path = REFRESH_SCRIPT) -> str:
    """The crontab of `schedule`: the zone line, then one line an hour running `script`."""
    lines = ["# the scheduled refresh, written by make_crontab.py from household.yaml as the scheduler starts",
             f"CRON_TZ={schedule.timezone}",
             *(f"{schedule.minute} {hour} * * * {script}" for hour in schedule.hours)]
    return "\n".join(lines) + "\n"


def _say(message: str) -> None:
    print(f"make_crontab: {message}", file=sys.stderr, flush=True)


def start(schedule: rs.Schedule) -> None:
    """The scheduler's start (--run): the crontab to a new file, the schedule to the mailbox, then supercronic in this
    process's place. A mailbox that cannot be written is said and gone without: the schedule still runs, and the
    store's refresh view says it is not known."""
    fd, crontab = tempfile.mkstemp(prefix="ff-crontab.", dir=tempfile.gettempdir())
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(crontab_text(schedule))
    mailbox = rs.locate("refresh")
    try:
        mailbox.mkdir(parents=True, exist_ok=True)
        rs.write_schedule(mailbox, schedule)
    except OSError as e:
        _say(f"the schedule could not be written in {mailbox} ({e.strerror or e}): the dashboard will not know it")
    _say(f"the refresh runs at {', '.join(schedule.times())} ({schedule.timezone})")
    os.execvp(SUPERCRONIC, [SUPERCRONIC, crontab])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="The scheduled refresh's crontab, from household.yaml")
    ap.add_argument("--run", action="store_true",
                    help="write the crontab and the mailbox's schedule, then become supercronic on that crontab")
    args = ap.parse_args(argv)
    try:
        schedule = household_schedule()
    except v.ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        _say("the scheduled refresh does not start until household.yaml is fixed")
        return 1
    if args.run:
        start(schedule)
        return 1                    # not reached: execvp replaces this process or raises
    sys.stdout.write(crontab_text(schedule))
    return 0


if __name__ == "__main__":
    sys.exit(main())

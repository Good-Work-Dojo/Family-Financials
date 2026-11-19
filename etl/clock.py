# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The one wall clock the pipeline reads for a value that reaches a file.

`now(zone)` is the current time in `zone`. When the environment variable FF_NOW is set it is that instant instead, so
an ingest of generated data is reproducible to the byte. FF_NOW is anything `datetime.fromisoformat` takes that has a
date and a time: with an offset (or `Z`) it is that instant, converted to `zone`; without one it is the wall-clock time
in `zone`. A date alone, text that is no date, or an instant too close to year 1 or 9999 to read in a zone raises
ConfigError, which `python -m etl ingest` reports as one line (exit 2) before it takes the lock or creates anything.
An unset, empty or blank variable is the real clock; an ordinary install never sets it.
"""
from __future__ import annotations

import datetime as dt
import os
from zoneinfo import ZoneInfo

from .validate import ConfigError, Problem

VARIABLE = "FF_NOW"


def _problem(value: str) -> Problem:
    return Problem(VARIABLE, "", f"'{value}' is not a date and time", "write it like 2026-10-31T18:00:00")


def _pinned(value: str) -> dt.datetime:
    """FF_NOW's value as a datetime (aware or naive); ConfigError when it is not a date and a time."""
    try:
        dt.date.fromisoformat(value)         # a date alone parses as midnight below; it carries no time
    except ValueError:
        try:
            return dt.datetime.fromisoformat(value)
        except ValueError:
            pass
    raise ConfigError([_problem(value)])


def _value() -> str | None:
    """FF_NOW's text, or None when it is unset, empty or only spaces (all of which mean the real clock)."""
    value = os.environ.get(VARIABLE)
    return value if value is not None and value.strip() else None


def _shift(pinned: dt.datetime, tz: dt.tzinfo, value: str) -> dt.datetime:
    """`pinned` in `tz`; ConfigError when the instant has no representation there (a year-1 or year-9999 edge)."""
    try:
        return pinned.replace(tzinfo=tz) if pinned.tzinfo is None else pinned.astimezone(tz)
    except (ValueError, OverflowError):
        raise ConfigError([_problem(value)]) from None


def check() -> None:
    """Raise ConfigError when FF_NOW is set to something that is not a date and time, or that cannot be read in a
    zone a day either side of UTC."""
    value = _value()
    if value is not None:
        pinned = _pinned(value)
        for hours in (-23, 23):
            _shift(pinned, dt.timezone(dt.timedelta(hours=hours)), value)


def now(zone: str) -> dt.datetime:
    """The current time in `zone` (an IANA name), or FF_NOW's instant when it is set."""
    tz = ZoneInfo(zone)
    value = _value()
    if value is None:
        return dt.datetime.now(tz)
    return _shift(_pinned(value), tz, value)


def utc_stamp() -> str:
    """`now` in UTC to the second, written with a trailing Z."""
    return now("UTC").replace(microsecond=0).isoformat().replace("+00:00", "Z")


def wall_utc() -> dt.datetime:
    """The real current instant in UTC, whatever FF_NOW says: what the install's own commands stamp or measure against
    the files on disk (a backup's name, a snapshot's age, the demo's last day), never a value of the exports, which
    `now` gives."""
    return dt.datetime.now(dt.UTC)

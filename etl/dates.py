# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Date parsing helpers. All dates are ISO 'YYYY-MM-DD' strings internally."""
from __future__ import annotations

import datetime as dt
import re

_MDY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def parse_date(raw: str) -> str:
    """Parse MM/DD/YYYY, M/D/YYYY or ISO dates to 'YYYY-MM-DD'. A date the calendar has not got (month 13, day 0,
    February 30th, February 29th in a common year) is no date, in every form: ValueError, as for any other text."""
    s = raw.strip()
    m = _MDY.match(s)
    if m:
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _ISO.match(s)
        if not m:
            raise ValueError(f"unparseable date: {raw!r}")
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        raise ValueError(f"not a calendar date: {raw!r}") from None


_SNAPSHOT_STAMP = re.compile(r"-(\d{8}T\d{6}Z)(?:-\d+)?\.json$")


def snapshot_fetched_at(name: str) -> str | None:
    """When a raw feed snapshot was pulled, as UTC ISO-8601 with a Z, read from
    its own filename: 'accounts-20260810T220000Z.json' -> '2026-08-10T22:00:00Z'.
    Also matches the collision variant '…Z-1.json' (etl/simplefin.fetch never
    overwrites a same-second file).

    That stamp is now_utc() at request time, which makes it the honest answer to
    "when was this data last pulled" — unlike the manifest's `generated_at`,
    which is the ingest time and so moves on every code deploy and every failed
    fetch that still re-ingested. Unrecognizable name -> None, never raises."""
    m = _SNAPSHOT_STAMP.search(name)
    if not m:
        return None
    try:
        stamp = dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ")
    except ValueError:
        return None
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def days_between(a: str, b: str) -> int | None:
    """Signed calendar days from `a` to `b` (positive when b is later). None when
    either side isn't a parseable ISO date — callers use this for staleness warnings,
    where a malformed date must degrade to "can't tell", never to a crash."""
    try:
        return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
    except (TypeError, ValueError):
        return None


def business_days_between(a: str, b: str) -> int:
    """Inclusive-exclusive count of business days between ISO dates (abs)."""
    d1 = dt.date.fromisoformat(min(a, b))
    d2 = dt.date.fromisoformat(max(a, b))
    days = 0
    cur = d1
    while cur < d2:
        cur += dt.timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def month_of(iso_date: str) -> str:
    return iso_date[:7]


def squash(s: str) -> str:
    """Collapse all whitespace runs (padding, newlines) to single spaces —
    the descriptor-normalization idiom shared by every parser."""
    return " ".join(s.split())

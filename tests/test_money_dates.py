# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
import random
import time
from datetime import date, timedelta

import pytest

from etl.dates import (
    business_days_between,
    parse_date,
    snapshot_fetched_at,
    squash,
)
from etl.money import fmt, parse_cents


@pytest.mark.parametrize("raw,expected", [
    ("1234.56", 123456),
    ("-58.11", -5811),
    ("$104594.69", 10459469),
    ("+$44,496.74", 4449674),
    ("+74.04%", 7404),
    ("(58.11)", -5811),
    ("2,904.71", 290471),
    ('"-11,812.94"', None),  # csv layer strips quotes; raw quotes are garbage
    ("1210430.5499999998", 121043055),   # the repr of a binary float
    ("1210430.545", 121043055),          # half-up on 3rd decimal
    ("", None),
    ("   ", None),
    ("0", 0),
])
def test_parse_cents(raw, expected):
    if expected is None and raw.strip().strip('"') != raw.strip():
        with pytest.raises(ValueError):
            parse_cents(raw)
    else:
        assert parse_cents(raw) == expected


def test_parse_cents_numeric_passthrough():
    assert parse_cents(12.34) == 1234
    assert parse_cents(-5) == -500


def test_parse_cents_of_a_missing_cell_is_none():
    assert parse_cents(None) is None


@pytest.mark.parametrize("raw", [float("inf"), float("-inf"), float("nan"), 1e16, -1e16, 10**16, "99999999999999999999.00",
                                 "-1000000000000000.01", True, [1], {"amount": 1}])
def test_an_amount_no_ledger_can_hold_is_refused_as_unreadable(raw):
    """Beyond 10^17 cents (a quadrillion dollars, the bound OFX keeps too) no amount is real and SQLite cannot store
    it; a non-finite float, a truth value or a container is no amount either: each is a ValueError, which every
    caller turns into one unreadable row."""
    with pytest.raises(ValueError):
        parse_cents(raw)


def test_the_largest_amount_a_ledger_holds_is_read():
    assert parse_cents("1000000000000000.00") == 10**17
    assert parse_cents(-10**15) == -10**17


def _business_days_by_walking(a: str, b: str) -> int:
    """The count stepped one day at a time: every weekday after the earlier date up to and including the later"""
    d1, d2 = sorted((date.fromisoformat(a), date.fromisoformat(b)))
    return sum((d1 + timedelta(days=i)).weekday() < 5 for i in range(1, (d2 - d1).days + 1))


def test_business_days_between_agrees_with_walking_the_days():
    """Weekends on either end, year ends, 29 February and long spans; both orders"""
    named = [("2030-01-04", "2030-01-07"), ("2030-01-05", "2030-01-06"), ("2030-01-06", "2030-01-12"),
             ("2029-12-27", "2030-01-03"), ("2028-02-28", "2028-03-01"), ("2028-02-29", "2032-02-29"),
             ("2030-03-09", "2030-03-09"), ("2025-12-31", "2031-01-01")]
    rng = random.Random(20301231)
    start = date(2024, 1, 1)
    pairs = named + [((start + timedelta(days=rng.randrange(3000))).isoformat(),
                      (start + timedelta(days=rng.randrange(3000))).isoformat()) for _ in range(3000)]
    pairs += [((start + timedelta(days=d)).isoformat(), (start + timedelta(days=d + k)).isoformat())
              for d in range(14) for k in range(15)]
    for a, b in pairs:
        expected = _business_days_by_walking(a, b)
        assert business_days_between(a, b) == business_days_between(b, a) == expected, (a, b)


def test_business_days_between_costs_the_same_whatever_the_span():
    """The generic transfer matcher asks this for every open pair across a household's whole history"""
    started = time.perf_counter()
    for _ in range(20_000):
        business_days_between("2025-01-01", "2030-12-31")
    assert time.perf_counter() - started < 1.0


def test_fmt():
    assert fmt(-123456) == "-$1,234.56"


@pytest.mark.parametrize("raw,expected", [
    ("05/27/2026", "2026-05-27"),
    ("5/4/2026", "2026-05-04"),
    ("2024-10-25", "2024-10-25"),
    ("02/29/2032", "2032-02-29"),           # a leap day
    ("2032-02-29", "2032-02-29"),
    ("2032-02-29T12:00:00", "2032-02-29"),
])
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", [
    "02/30/2030", "2/29/2031", "13/01/2030", "00/10/2030", "01/00/2030", "04/31/2030", "01/01/0000",
    "2030-02-30", "2031-02-29", "2030-13-01", "2030-00-10", "2030-01-00", "2030-04-31", "2030-02-30T00:00:00",
])
def test_a_date_that_is_not_on_the_calendar_is_no_date(raw):
    """Month 13, day 0, February 30th, February 29th in a common year: in every form parse_date reads, a date the
    calendar has not got is refused, never stored as written"""
    with pytest.raises(ValueError):
        parse_date(raw)


def test_business_days():
    assert business_days_between("2026-05-22", "2026-05-26") == 2  # Fri -> Tue
    assert business_days_between("2026-05-26", "2026-05-22") == 2  # symmetric
    assert business_days_between("2026-05-26", "2026-05-26") == 0


def test_squash():
    assert squash("  EXAMPLE   CARD\tONLINE\nPMT  ") == "EXAMPLE CARD ONLINE PMT"
    assert squash("already clean") == "already clean"
    assert squash("") == ""


# ---- snapshot fetch stamps (manifest simplefin_fetched_at) ----

def test_snapshot_fetched_at_reads_the_filename_stamp():
    assert snapshot_fetched_at(
        "accounts-20260810T220000Z.json") == "2026-08-10T22:00:00Z"
    assert snapshot_fetched_at(
        "prices-20260810T220024Z.json") == "2026-08-10T22:00:24Z"


def test_snapshot_fetched_at_handles_the_same_second_collision_suffix():
    """etl.simplefin.fetch never overwrites a same-second file; it appends -1."""
    assert snapshot_fetched_at(
        "accounts-20260810T220000Z-1.json") == "2026-08-10T22:00:00Z"


@pytest.mark.parametrize("name", [
    "accounts.json", "accounts-2026-08-10.json", "accounts-20261345T990000Z.json",
    "", "accounts-20260810T220000Z.txt",
])
def test_snapshot_fetched_at_returns_none_rather_than_raising(name):
    assert snapshot_fetched_at(name) is None


def test_collision_suffix_still_sorts_next_to_its_twin():
    """The manifest takes the LAST entry of a lexically sorted list, so the
    ordering has to stay chronological — '-' (0x2D) sorts before '.' (0x2E)."""
    names = sorted(["accounts-20260810T220000Z.json",
                    "accounts-20260810T220000Z-1.json",
                    "accounts-20260810T220001Z.json"])
    assert names[-1] == "accounts-20260810T220001Z.json"

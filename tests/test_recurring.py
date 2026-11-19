# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Recurring detection: cadence bands, amount modes, price changes."""
import json
from datetime import date, timedelta

import pytest
from conftest import demo_config, make_txn

from etl.enrich.recurring import (
    CONFIDENCE_FLOOR,
    FIRST_BUSINESS_DAY,
    FIRST_EXPECTED_MIN_DAYS,
    GRACE_DAYS,
    LAST_BUSINESS_DAY,
    MIN_OCCURRENCES,
    annualized_cents,
    detect,
    expected_dates,
    monthly_dates,
    monthly_rule,
    promote,
)
from etl.pipeline import Pipeline
from tools.demo import household as hh


def tx(date, vendor, cents, category="Subscriptions"):
    return make_txn(date=date, account=hh.CARD_ALEX, desc=vendor,
                    cents=cents, vendor=vendor, category=category)


def test_monthly_fixed_subscription_with_price_increase():
    rows = ([tx(f"2025-{m:02d}-15", "Netflix", -1549) for m in range(1, 7)]
            + [tx(f"2025-{m:02d}-15", "Netflix", -1749) for m in range(7, 13)])
    out = detect(rows, "2025-12-20")
    assert len(out) == 1
    r = out[0]
    assert r["cadence"] == "monthly"
    assert r["amount_mode"] == "fixed"
    assert r["active"] is True
    assert r["typical_amount_cents"] == 1749
    assert r["price_changes"] == [
        {"date": "2025-07-15", "from_cents": 1549, "to_cents": 1749}]
    assert 19000 < r["annualized_cents"] < 22000


def test_variable_monthly_utility():
    amounts = [-4100, -4500, -3900, -4300, -4700]
    rows = [tx(f"2025-{m:02d}-22", "Harbor Electric", a, "Utilities")
            for m, a in enumerate(amounts, start=1)]
    out = detect(rows, "2025-06-01")
    assert len(out) == 1
    assert out[0]["amount_mode"] == "variable"


def test_frequent_store_rejected_by_spread():
    amounts = [-1200, -8900, -23000, -4500, -15600, -700]
    rows = [tx(f"2025-{m:02d}-10", "Maple Street Market", a, "Groceries")
            for m, a in enumerate(amounts, start=1)]
    assert detect(rows, "2025-07-01") == []


def test_inactive_when_charges_stop():
    rows = [tx(f"2024-{m:02d}-05", "Hulu", -799) for m in range(1, 7)]
    out = detect(rows, "2025-06-01")
    assert len(out) == 1
    assert out[0]["active"] is False


def test_biweekly_cadence():
    dates = ["2026-01-02", "2026-01-16", "2026-01-30", "2026-02-13", "2026-02-27"]
    rows = [tx(d, "Mortgage Co", -126914, "Housing") for d in dates]
    out = detect(rows, "2026-03-05")
    assert len(out) == 1
    assert out[0]["cadence"] == "biweekly"


def test_fewer_than_three_occurrences_ignored():
    rows = [tx("2025-01-01", "OneOff", -5000), tx("2025-02-01", "OneOff", -5000)]
    assert detect(rows, "2025-03-01") == []


def test_small_addon_charges_dont_break_detection():
    # one monthly $99 membership + a $3 top-up and $25 add-on pack mid-stream
    rows = [tx(f"2025-{m:02d}-23", "ClassPass", -9900) for m in range(1, 9)]
    rows.insert(3, tx("2025-04-10", "ClassPass", -2500))
    rows.insert(6, tx("2025-06-12", "ClassPass", -300))
    out = detect(rows, "2025-09-01")
    assert len(out) == 1
    assert out[0]["cadence"] == "monthly"
    assert out[0]["typical_amount_cents"] == 9900
    assert out[0]["occurrences"] == 8        # add-ons not counted as the cycle


def test_cancelled_subscription_is_retired_immediately():
    """Annual cadence keeps a series "active" for ~2 years after its last charge, so a
    cancelled annual sub would sit in the monthly total and raise a false renewal alert.
    Declaring it retires it now; history and annualized figures stay intact."""
    rows = [tx(f"{y}-03-17", "Orbitwave Maps Annual", -5499) for y in (2024, 2025, 2026)]
    live = detect(rows, "2026-04-02")
    assert live[0]["active"] is True          # data alone would keep it active
    assert live[0]["cadence"] == "annual"

    out = detect(rows, "2026-04-02",
                 [{"vendor": "Orbitwave Maps Annual", "recorded_on": "2026-03-30",
                   "permanent": True}])
    assert out[0]["active"] is False
    # only activity is retired — the series is still reported in full
    assert out[0]["occurrences"] == 3
    assert out[0]["last_seen"] == "2026-03-17"
    assert out[0]["typical_amount_cents"] == 5499


def test_cancellation_match_is_case_insensitive_and_scoped():
    rows = ([tx(f"2026-{m:02d}-21", "Tunecrate Plus", -1349) for m in range(3, 7)]
            + [tx(f"2026-{m:02d}-09", "Netflix", -1749) for m in range(3, 7)])
    out = {r["vendor"]: r for r in
           detect(rows, "2026-07-01", [{"vendor": "tunecrate PLUS"}])}
    assert out["Tunecrate Plus"]["active"] is False
    assert out["Netflix"]["active"] is True   # unrelated series untouched


# ----------------------------------------------------------------- schedules

def test_every_series_carries_the_schedule_fields_and_expected_dates_follow_next_expected():
    rows = [tx(f"2025-{m:02d}-15", "Netflix", -1549) for m in range(1, 9)]
    r = detect(rows, "2025-08-20")[0]
    # gaps 31,28,31,30,31,30,31 -> median 31; a bill on the 15th is expected on the 15th, not 31 days on
    assert r["median_gap_days"] == 31
    assert r["expected_dates"] == ["2025-09-15", "2025-10-15", "2025-11-15"]
    assert r["expected_dates"][0] == r["next_expected"]
    assert r["grace_days"] == 6 and r["confidence"] == 1.0 and r["schedule"] is True


# ------------------------------------------------- a monthly bill's day of the month

def monthly(dates, vendor="Bill", cents=-5000):
    """The one series detected from charges on `dates`, judged at its last day."""
    [r] = detect([tx(d, vendor, cents, "Utilities") for d in dates], dates[-1])
    assert r["cadence"] == "monthly"
    return r


def test_a_bill_on_the_first_business_day_is_expected_on_the_next_months_first_business_day():
    # the 1st, or the Monday after a weekend 1st: 1 Feb and 1 Mar 2026 are Sundays, 1 Aug a Saturday
    r = monthly(["2026-02-02", "2026-03-02", "2026-04-01", "2026-05-01", "2026-06-01", "2026-07-01"])
    assert monthly_rule([date.fromisoformat(d) for d in
                         ("2026-02-02", "2026-03-02", "2026-04-01", "2026-05-01", "2026-06-01", "2026-07-01")]) \
        == FIRST_BUSINESS_DAY
    assert r["expected_dates"] == ["2026-08-03", "2026-09-01", "2026-10-01"]
    assert r["next_expected"] == "2026-08-03"
    # a day count after the last payment (the median gap, 30 days) would read 31 July
    assert expected_dates(r["last_seen"], r["median_gap_days"])[0] == "2026-07-31"


def test_three_of_the_last_four_on_the_first_business_day_is_enough():
    # one payment a day late (Friday 2 January; the 1st was a Thursday), the other three on their first business day
    r = monthly(["2025-11-03", "2025-12-01", "2026-01-02", "2026-02-02", "2026-03-02", "2026-04-01"])
    assert r["expected_dates"] == ["2026-05-01", "2026-06-01", "2026-07-01"]
    # a day count (the median gap, 30 days) would read 1 May, then 31 May and 30 June
    assert expected_dates(r["last_seen"], r["median_gap_days"]) == ["2026-05-01", "2026-05-31", "2026-06-30"]
    # two of the last four: not the business-day case (and the days wander, so no stable day either)
    two = monthly(["2026-01-05", "2026-02-02", "2026-03-04", "2026-04-01", "2026-05-04"])
    assert two["expected_dates"] == expected_dates(two["last_seen"], two["median_gap_days"])
    assert two["next_expected"] == "2026-06-02"


def test_the_first_expected_date_is_at_least_fifteen_days_after_the_last_payment():
    assert FIRST_EXPECTED_MIN_DAYS == 15
    # due on the 1st, paid the business day before a weekend 1st: 31 July pays August (1 August 2026 is a Saturday),
    # so the next payment is September's, not 3 August, which is already paid
    early = monthly(["2026-05-01", "2026-06-01", "2026-07-01", "2026-07-31"])
    assert early["expected_dates"] == ["2026-09-01", "2026-10-01", "2026-11-02"]
    # a last-business-day bill whose October payment came on 2 November: November's is still expected on the 30th
    late = monthly(["2026-07-31", "2026-08-31", "2026-09-30", "2026-11-02"])
    assert late["expected_dates"] == ["2026-11-30", "2026-12-31", "2027-01-29"]
    # the ordinary cases: the next month's date
    assert monthly_dates("2026-10-01", FIRST_BUSINESS_DAY)[0] == "2026-11-02"
    assert monthly_dates("2026-09-13", 15)[0] == "2026-10-15"
    assert monthly_dates("2026-01-31", 31)[0] == "2026-02-28"


def test_a_payment_on_a_weekend_says_the_bill_is_no_business_day_bill():
    # a charge that posts on the 1st whatever the day (Saturday 1 August among them) is a bill on the 1st: Sunday
    # 1 November, not Monday 2 November
    first = ["2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01", "2026-10-01"]
    assert monthly_rule([date.fromisoformat(d) for d in first]) == 1
    assert monthly(first)["expected_dates"] == ["2026-11-01", "2026-12-01", "2027-01-01"]
    # one that posts on the month's last day (Sunday 31 May, Saturday 31 October): 31 January 2027 (a Sunday), not
    # the last business day, the 29th
    last = ["2026-05-31", "2026-06-30", "2026-07-31", "2026-08-31", "2026-09-30", "2026-10-31"]
    assert monthly_rule([date.fromisoformat(d) for d in last]) == 31
    assert monthly(last)["expected_dates"] == ["2026-11-30", "2026-12-31", "2027-01-31"]
    # a genuine first-business-day bill (3 August for the Saturday 1st) still is one
    genuine = ["2026-06-01", "2026-07-01", "2026-08-03", "2026-09-01", "2026-10-01"]
    assert monthly_rule([date.fromisoformat(d) for d in genuine]) == FIRST_BUSINESS_DAY
    assert monthly(genuine)["expected_dates"] == ["2026-11-02", "2026-12-01", "2027-01-01"]


def test_one_odd_payment_does_not_undo_a_stable_day():
    # a bill on the 15th paid once on the 18th: still the 15th, the odd payment left out of the median
    once = monthly(["2026-05-15", "2026-06-15", "2026-07-15", "2026-08-18", "2026-09-15", "2026-10-15"])
    assert once["expected_dates"] == ["2026-11-15", "2026-12-15", "2027-01-15"]
    # the odd payment the latest one: still the 15th, and November's (at least fifteen days after the 18th)
    latest = monthly(["2026-05-15", "2026-06-15", "2026-07-15", "2026-08-15", "2026-09-15", "2026-10-18"])
    assert latest["expected_dates"] == ["2026-11-15", "2026-12-15", "2027-01-15"]


def test_a_bill_that_moved_its_day_is_read_on_its_new_day():
    # from the 5th to the 20th three payments ago: the last three alone are a stable day
    moved = ["2026-04-05", "2026-05-05", "2026-06-05", "2026-07-05", "2026-08-20", "2026-09-20", "2026-10-20"]
    assert monthly_rule([date.fromisoformat(d) for d in moved]) == 20
    assert monthly(moved)["expected_dates"] == ["2026-11-20", "2026-12-20", "2027-01-20"]


def test_two_odd_payments_or_a_drifting_day_are_no_stable_day():
    # two payments on the 18th among five: neither all but one nor the last three are within three days
    twice = ["2026-06-15", "2026-07-18", "2026-08-15", "2026-09-18", "2026-10-15"]
    assert monthly_rule([date.fromisoformat(d) for d in twice]) is None
    r = monthly(twice)
    assert r["expected_dates"] == expected_dates(r["last_seen"], r["median_gap_days"])
    # a day that drifts a day a month (1st, 2nd, 3rd, 4th, 5th) moved nowhere: the last three are near the one before
    drift = ["2026-06-01", "2026-07-02", "2026-08-03", "2026-09-04", "2026-10-05"]
    assert monthly_rule([date.fromisoformat(d) for d in drift]) is None
    d = monthly(drift)
    assert d["expected_dates"] == expected_dates(d["last_seen"], d["median_gap_days"])


def test_a_bill_on_a_stable_day_is_expected_on_its_median_day():
    r = monthly(["2026-03-14", "2026-04-15", "2026-05-16", "2026-06-15"])
    assert r["expected_dates"] == ["2026-07-15", "2026-08-15", "2026-09-15"]
    # the LAST six payments decide: a bill that moved from the 20th to the 2nd is read on the 2nd, not as irregular
    moved = monthly(["2025-04-20", "2025-05-20", "2025-06-20", "2025-07-20", "2025-08-20", "2025-09-02",
                     "2025-10-02", "2025-11-02", "2025-12-02", "2026-01-02", "2026-02-02", "2026-03-02"])
    assert moved["next_expected"] == "2026-04-02"


def test_an_anchor_past_a_short_months_end_is_that_months_last_day():
    # the 31st, paid on 30 November (its last day) and on a weekend 31st: not the last-business-day case
    r = monthly(["2025-10-31", "2025-11-30", "2025-12-31", "2026-01-31"])
    assert r["expected_dates"] == ["2026-02-28", "2026-03-31", "2026-04-30"]
    # a February in between: 28 February and 30 April are the 31st clamped, so the anchor stays the 31st
    between = monthly(["2025-12-31", "2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"])
    assert between["expected_dates"] == ["2026-05-31", "2026-06-30", "2026-07-31"]
    # a leap year's February has a 29th
    assert monthly_dates("2028-01-31", 31) == ["2028-02-29", "2028-03-31", "2028-04-30"]
    # a bill on the 28th keeps the 28th, February's last day included
    assert monthly(["2026-01-28", "2026-02-28", "2026-03-28", "2026-04-28"])["next_expected"] == "2026-05-28"
    # and one on the 30th keeps the 30th, February's 28th and April's 30th read as the 30th
    on30 = monthly(["2026-01-30", "2026-02-28", "2026-03-30", "2026-04-30", "2026-05-30"])
    assert on30["expected_dates"] == ["2026-06-30", "2026-07-30", "2026-08-30"]


def test_a_bill_on_the_last_business_day_is_expected_on_the_next_months_last_business_day():
    # the month's last weekday: 29 May 2026 (the 31st is a Sunday), and 30 October (the 31st is a Saturday)
    r = monthly(["2026-04-30", "2026-05-29", "2026-06-30", "2026-07-31", "2026-08-31", "2026-09-30"])
    assert monthly_rule([date.fromisoformat(d) for d in
                         ("2026-04-30", "2026-05-29", "2026-06-30", "2026-07-31", "2026-08-31", "2026-09-30")]) \
        == LAST_BUSINESS_DAY
    assert r["expected_dates"] == ["2026-10-30", "2026-11-30", "2026-12-31"]


def test_the_month_end_wrap_is_no_stable_day():
    # the 30th, the 31st and the 1st are no three days of one month: not a stable day
    days = [date.fromisoformat(d) for d in ("2026-01-30", "2026-03-01", "2026-03-31", "2026-04-30", "2026-06-01")]
    assert monthly_rule(days) is None


def test_an_irregular_monthly_bill_keeps_the_interval_after_its_last_payment():
    # its days of the month wander (the 3rd, the 19th, the 9th): no stable day, no business day
    dates = ["2025-10-03", "2025-11-03", "2025-12-03", "2026-01-03", "2026-02-03", "2026-03-19", "2026-04-19",
             "2026-05-19", "2026-06-19", "2026-07-09"]
    r = monthly(dates)
    assert monthly_rule([date.fromisoformat(d) for d in dates]) is None
    assert r["median_gap_days"] == 31
    assert r["expected_dates"] == expected_dates("2026-07-09", 31) == ["2026-08-09", "2026-09-09", "2026-10-10"]


@pytest.mark.parametrize("cadence,dates", [
    ("weekly", ["2026-01-02", "2026-01-09", "2026-01-16", "2026-01-23", "2026-01-30"]),
    ("biweekly", ["2026-01-02", "2026-01-16", "2026-01-30", "2026-02-13", "2026-02-27"]),
    # a stable day of the month on a quarterly or an annual bill reads nothing: the interval after the last payment
    ("quarterly", ["2025-01-05", "2025-04-05", "2025-07-05", "2025-10-05"]),
    ("annual", ["2023-10-06", "2024-10-06", "2025-10-06"]),
])
def test_every_other_cadence_keeps_the_interval_after_its_last_payment(cadence, dates):
    [r] = detect([tx(d, "Vendor", -4999) for d in dates], dates[-1])
    assert r["cadence"] == cadence
    assert r["expected_dates"] == expected_dates(r["last_seen"], r["median_gap_days"])
    assert r["next_expected"] == r["expected_dates"][0]


def test_the_expected_dates_read_the_payments_only():
    dates = ["2026-02-02", "2026-03-02", "2026-04-01", "2026-05-01", "2026-06-01", "2026-07-01"]
    rows = [tx(d, "Bill", -5000) for d in dates]
    first = detect(rows, "2026-07-01")
    assert detect(rows, "2026-07-01") == first                          # the same input twice
    assert detect(list(reversed(rows)), "2026-07-01") == first          # in any order
    # a later data end (the ingest day) moves no date
    later = detect(rows, "2026-07-20")
    assert [r["expected_dates"] for r in later] == [r["expected_dates"] for r in first]


def test_a_yearly_sum_counts_the_charges_a_calendar_year_holds():
    # monthly: twelve charges; quarterly: four; annual: one; weekly and biweekly: 365 days over their period (a year
    # is no whole number of weeks)
    rent = monthly(["2026-01-05", "2026-02-05", "2026-03-05", "2026-04-05"], cents=-200_000)
    assert rent["annualized_cents"] == 2_400_000
    assert annualized_cents(200_000, "monthly") == 2_400_000
    assert annualized_cents(90_000, "quarterly") == 360_000
    assert annualized_cents(13_900, "annual") == 13_900
    assert annualized_cents(2500, "weekly") == int(2500 * 365 / 7)
    assert annualized_cents(2500, "biweekly") == int(2500 * 365 / 14)


@pytest.mark.parametrize("cadence,dates,cents,monthly_figure,yearly", [
    ("monthly", [f"2026-{m:02d}-05" for m in range(1, 5)], 200_000, 2_000.0, 24_000.0),
    ("quarterly", ["2025-01-05", "2025-04-05", "2025-07-05", "2025-10-05"], 90_000, 300.0, 3_600.0),
    ("annual", ["2023-10-06", "2024-10-06", "2025-10-06"], 13_900, 11.58, 139.0),
    ("weekly", [f"2026-01-{d:02d}" for d in (2, 9, 16, 23, 30)], 2_500, 108.63, 1_303.57),
    ("biweekly", ["2026-01-02", "2026-01-16", "2026-01-30", "2026-02-13", "2026-02-27"], 2_500, 54.31, 651.78),
])
def test_the_exports_monthly_and_yearly_figures_per_cadence(tmp_path, cadence, dates, cents, monthly_figure, yearly):
    """A quarterly item is a third of its amount a month and four of them a year; an annual one a twelfth and itself:
    the figures the bills chart counts. The dashboard prints these two fields as they are."""
    rows = [tx(d, "Vendor", -cents) for d in dates]
    p = Pipeline(demo_config())
    p.txns, p.data_end = rows, dates[-1]
    p.recurring_rows = detect(rows, p.data_end)
    p._export_ledger(tmp_path)
    [it] = json.loads((tmp_path / "recurring.json").read_text())["items"]
    assert it["cadence"] == cadence
    assert (it["monthly_equivalent"], it["annualized"]) == (monthly_figure, yearly)


def test_expected_dates_rolls_forward_by_the_median_gap():
    assert expected_dates("2026-01-31", 30) == ["2026-03-02", "2026-04-01", "2026-05-01"]
    assert expected_dates("2026-12-20", 7, n=2) == ["2026-12-27", "2027-01-03"]


@pytest.mark.parametrize("cadence,dates,grace,min_occ", [
    ("weekly", [f"2026-01-{d:02d}" for d in (2, 9, 16, 23, 30)], 3, 4),
    ("biweekly", ["2026-01-02", "2026-01-16", "2026-01-30", "2026-02-13", "2026-02-27"], 4, 4),
    ("monthly", [f"2026-{m:02d}-10" for m in range(1, 5)], 6, 3),
    ("quarterly", ["2025-01-05", "2025-04-05", "2025-07-05", "2025-10-05"], 14, 3),
    ("annual", ["2023-10-06", "2024-10-06", "2025-10-06"], 21, 3),
])
def test_grace_days_and_minimum_occurrences_per_cadence(cadence, dates, grace, min_occ):
    rows = [tx(d, "Vendor", -4999, "Subscriptions") for d in dates]
    r = detect(rows, dates[-1])[0]
    assert r["cadence"] == cadence and r["grace_days"] == grace == GRACE_DAYS[cadence]
    assert MIN_OCCURRENCES[cadence] == min_occ
    assert r["schedule"] is (len(dates) >= min_occ)


def test_weekly_needs_four_occurrences_monthly_needs_three():
    weekly3 = detect([tx(d, "Gym", -2500) for d in ("2026-01-02", "2026-01-09", "2026-01-16")], "2026-01-20")[0]
    weekly4 = detect([tx(d, "Gym", -2500) for d in ("2026-01-02", "2026-01-09", "2026-01-16", "2026-01-23")], "2026-01-25")[0]
    monthly3 = detect([tx(f"2026-{m:02d}-10", "Hulu", -799) for m in (1, 2, 3)], "2026-03-15")[0]
    assert (weekly3["schedule"], weekly4["schedule"], monthly3["schedule"]) == (False, True, True)


def test_confidence_is_cadence_share_times_the_amount_mode_factor():
    # 7 gaps of 30 and 3 of 45: share 0.7 (the detection floor); fixed -> 0.7, variable -> 0.595
    gaps = [30] * 7 + [45] * 3
    dates, d = [], date(2025, 1, 10)
    for g in [0, *gaps]:
        d = d + timedelta(days=g)
        dates.append(d.isoformat())
    fixed = detect([tx(x, "Fixed Co", -5000) for x in dates], dates[-1])[0]
    assert fixed["confidence"] == 0.7 and fixed["schedule"] is True            # >= the 0.7 floor
    amounts = [-4000 - 100 * i for i in range(len(dates))]                    # all distinct, inside the 35% variable-mode spread
    var = detect([tx(x, "Var Co", a, "Utilities") for x, a in zip(dates, amounts, strict=True)], dates[-1])[0]
    assert var["amount_mode"] == "variable" and var["confidence"] == 0.595 and var["schedule"] is False
    assert CONFIDENCE_FLOOR == 0.7


def test_promote_requires_every_condition():
    base = {"active": True, "cadence": "monthly", "amount_mode": "fixed", "occurrences": 3, "confidence": 0.7}
    assert promote(base) is True
    for bad in ({"active": False}, {"cadence": None}, {"amount_mode": None}, {"occurrences": 2},
                {"confidence": 0.699}, {"cadence": "weekly", "occurrences": 3}):
        assert promote({**base, **bad}) is False, bad


def test_inactive_and_cancelled_series_are_never_schedules():
    lapsed = detect([tx(f"2024-{m:02d}-05", "Hulu", -799) for m in range(1, 7)], "2025-06-01")[0]
    assert lapsed["active"] is False and lapsed["schedule"] is False
    rows = [tx(f"{y}-03-17", "Orbitwave Maps Annual", -5499) for y in (2024, 2025, 2026)]
    cancelled = detect(rows, "2026-04-02", [{"vendor": "Orbitwave Maps Annual", "recorded_on": "2026-03-30"}])[0]
    assert cancelled["schedule"] is False and cancelled["expected_dates"][0] == "2027-03-17"   # still reported


def test_biweekly_needs_four_occurrences_too():
    biweekly3 = detect([tx(d, "Meal Kit", -6000) for d in ("2026-01-02", "2026-01-16", "2026-01-30")], "2026-02-05")[0]
    assert biweekly3["cadence"] == "biweekly" and biweekly3["occurrences"] == 3
    assert biweekly3["active"] is True and biweekly3["confidence"] == 1.0 and biweekly3["schedule"] is False


def test_same_day_duplicates_do_not_count_toward_the_minimum():
    # three rows but ONE observed gap: occurrences stays 3 as exported, the series is not a schedule
    dup_monthly = detect([tx(d, "Hulu", -799) for d in ("2026-01-10", "2026-01-10", "2026-02-10")], "2026-02-15")[0]
    assert dup_monthly["occurrences"] == 3 and dup_monthly["confidence"] == 1.0 and dup_monthly["active"] is True
    assert dup_monthly["schedule"] is False
    # weekly: four rows, three distinct dates -> below the weekly minimum of four
    dup_weekly = detect([tx(d, "Gym", -2500) for d in ("2026-01-02", "2026-01-09", "2026-01-09", "2026-01-16")],
                        "2026-01-20")[0]
    assert dup_weekly["occurrences"] == 4 and dup_weekly["schedule"] is False
    # three distinct dates still clear the monthly minimum, with or without a duplicate among them
    clean = detect([tx(d, "Hulu", -799) for d in ("2026-01-10", "2026-02-10", "2026-03-10")], "2026-03-15")[0]
    padded = detect([tx(d, "Hulu", -799) for d in ("2026-01-10", "2026-01-10", "2026-02-10", "2026-03-10")],
                    "2026-03-15")[0]
    assert clean["schedule"] is True and padded["schedule"] is True and padded["occurrences"] == 4


def test_promote_counts_distinct_dates_when_given_and_falls_back_to_occurrences():
    base = {"active": True, "cadence": "monthly", "amount_mode": "fixed", "occurrences": 5, "confidence": 1.0}
    assert promote(base) is True and promote(base, distinct_dates=3) is True
    assert promote(base, distinct_dates=2) is False                       # occurrences alone would have passed
    assert promote({**base, "cadence": "weekly"}, distinct_dates=3) is False
    assert promote({**base, "cadence": "weekly"}, distinct_dates=4) is True


# ------------------------------------------------- recurring.json passthrough (no pipeline change)

def test_recurring_json_carries_exactly_the_five_schedule_keys_and_no_cents_keys(tmp_path):
    rows = [tx(f"2025-{m:02d}-15", "Netflix", -1549) for m in range(1, 9)]
    p = Pipeline(demo_config())                # a bare Pipeline, as in test_export_ledger_writes_vendors_json
    p.txns = rows
    p.data_end = "2025-08-20"
    p.recurring_rows = detect(rows, p.data_end)
    p._export_ledger(tmp_path)
    items = json.loads((tmp_path / "recurring.json").read_text())["items"]
    assert len(items) == 1
    it = items[0]
    assert not [k for k in it if k.endswith("_cents")]
    assert set(it) == {"vendor", "account_id", "category", "cadence", "amount_mode", "occurrences",
                       "first_seen", "last_seen", "next_expected", "active", "typical_amount",
                       "last_amount", "annualized", "monthly_equivalent", "price_changes",
                       "schedule", "expected_dates", "grace_days", "confidence", "median_gap_days"}
    assert it["schedule"] is True
    assert it["expected_dates"] == ["2025-09-15", "2025-10-15", "2025-11-15"]
    assert it["monthly_equivalent"] == it["typical_amount"] and it["annualized"] == 185.88   # a monthly item's own amount
    assert it["grace_days"] == 6 and isinstance(it["grace_days"], int)
    assert it["median_gap_days"] == 31 and isinstance(it["median_gap_days"], int)
    assert it["confidence"] == 1.0 and isinstance(it["confidence"], float)
    assert it["typical_amount"] == 15.49                                  # dollars at the boundary, as before



def test_split_parents_form_the_series_and_their_parts_never_do():
    """The parts (-30 / -20 a month) WOULD be a variable monthly series of their own if they were
    counted — six rows, a 20% spread — so a detector that read the children (or both) cannot pass"""
    parents = [tx(d, "Synth Box", -5000) for d in ("2026-01-05", "2026-02-05", "2026-03-05")]
    parts = []
    for p in parents:
        p.is_parent = True
        parts += [make_txn(date=p.date, account=hh.CARD_ALEX, desc="Synth Box", cents=c, vendor="Synth Box",
                           category=cat, category_source="split", parent_id=p.uid, split_index=i)
                  for i, (c, cat) in enumerate(((-3000, "Subscriptions"), (-2000, "Entertainment")), start=1)]
    out = detect([*parents, *parts], "2026-03-10")
    assert len(out) == 1
    assert (out[0]["occurrences"], out[0]["typical_amount_cents"], out[0]["amount_mode"]) == (3, 5000, "fixed")   # one series, from the parents
    assert detect(parts, "2026-03-10") == []                                   # parts alone are never a series

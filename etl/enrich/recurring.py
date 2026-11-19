# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Recurring-charge detection over external spending (purchases + fees).

Groups by (vendor, account); needs >=3 occurrences. Cadence assigned when >=70%
of consecutive gaps fall in one band; amount mode 'fixed' (stdev/median < 2% or
>=80% share an exact amount) or 'variable' (within +/-35% of median); wider
spread -> not recurring (frequent store, not a subscription).

Schedules: every series also carries median_gap_days, expected_dates (its next three
occurrences, next_expected the first), grace_days per cadence, confidence (cadence-band share × 1.0 fixed /
0.85 variable) and schedule = promote(row): active, cadence set, amount mode fixed/variable,
>= 4 occurrences weekly/biweekly else >= 3, confidence >= 0.7. Clock-free — status (paid, due,
missed, data stale) is the APP's job, from these dates and the account's txn_data_end.
The new keys are export-only: the DB `recurring` table's positional INSERT (pipeline.py) is unchanged.
Split purchases are read from their parents: a subscription is one series, however the rule splits its charge.

The expected dates of a MONTHLY series follow its day of the month (`monthly_rule`, read from its own distinct
payment dates only, never the clock or the data end):
- the first or last business day: when none of its last four payments fell on a Saturday or Sunday (a payment on a
  weekend says the bill is paid on its calendar day) and at least three of them fell on their month's first business
  day (the 1st, or the Monday after a weekend 1st), each later month's first business day; the same for the last;
- else a stable day (`_stable_day`): its last six payments' days of the month within three days (the 14th to the 16th),
  or all but one of them when there are four or more (one odd payment), or else its last three alone when the payment
  before them was on another day (a bill that moved its day); the median day of those within the span, clamped to a
  shorter month's last day (the 31st is 28 or 29 February). The 30th / 31st / 1st wrap is no such span;
- else none: an irregular series, stepped by its median gap like every other cadence.
The first expected date is the first the rule gives at least FIRST_EXPECTED_MIN_DAYS after the last payment, from the
last payment's own month on, so a payment that crossed a month end (paid on 31 July for 1 August) is not followed by a
date it already paid; the later dates are the months after it (`monthly_dates`). A business day is Monday to Friday:
there is no holiday calendar, so a holiday can put the real payment a day later, inside the grace window.
A series' yearly sum counts the charges a calendar year holds (`annualized_cents`): twelve monthly, four quarterly, one
annual, so its monthly figure is its charge, a third of it or a twelfth of it; weekly and biweekly scale 365 days by
their period (a year is no whole number of weeks).
"""
from __future__ import annotations

import statistics
from calendar import monthrange
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta

from ..models import SPEND_TYPES, Txn

CADENCE_BANDS = {
    "weekly": (6, 8),
    "biweekly": (12, 16),
    "monthly": (27, 33),
    "quarterly": (83, 97),
    "annual": (350, 381),
}
# the charges a calendar year holds, for the cadences a calendar steps (annualized_cents)
CHARGES_PER_YEAR = {"monthly": 12, "quarterly": 4, "annual": 1}
# the period in days a yearly sum divides 365 by, for the cadences a day count steps
PERIOD_DAYS = {"weekly": 7, "biweekly": 14}
GRACE_DAYS = {"weekly": 3, "biweekly": 4, "monthly": 6, "quarterly": 14, "annual": 21}
MIN_OCCURRENCES = {"weekly": 4, "biweekly": 4, "monthly": 3, "quarterly": 3, "annual": 3}
CONFIDENCE_FLOOR = 0.7
AMOUNT_MODE_FACTOR = {"fixed": 1.0, "variable": 0.85}

FIRST_BUSINESS_DAY = "first_business_day"
LAST_BUSINESS_DAY = "last_business_day"
BUSINESS_DAY_PAYMENTS = 4      # the business-day case reads the last four payments, none on a weekend...
BUSINESS_DAY_MIN = 3           # ...and needs three of them on their month's first (or last) business day
STABLE_DAY_PAYMENTS = 6        # a stable day is read from the last six payments...
STABLE_DAY_SPAN = 3            # ...whose days of the month fall within three days...
ODD_PAYMENT_MIN = 4            # ...all but one of them when there are at least four...
MOVED_DAY_PAYMENTS = 3         # ...or the last three alone, after a payment on another day
FIRST_EXPECTED_MIN_DAYS = 15   # the first expected date is at least this many days after the last payment


def expected_dates(last_seen: str, median_gap_days: int, n: int = 3) -> list[str]:
    """The next `n` expected charge dates: last_seen + k × median_gap_days."""
    start = date.fromisoformat(last_seen)
    return [(start + timedelta(days=median_gap_days * k)).isoformat() for k in range(1, n + 1)]


def annualized_cents(typical_cents: int, cadence: str) -> int:
    """A series' charges over a year: the charges a calendar year holds for a monthly, quarterly or annual one (its
    monthly figure is then its charge, a third of it or a twelfth of it), else 365 days over the cadence's period."""
    if cadence in CHARGES_PER_YEAR:
        return typical_cents * CHARGES_PER_YEAR[cadence]
    return int(typical_cents * 365 / PERIOD_DAYS[cadence])


def _month_days(year: int, month: int) -> int:
    return monthrange(year, month)[1]


def first_business_day(year: int, month: int) -> date:
    """The month's first Monday-to-Friday."""
    day = date(year, month, 1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def last_business_day(year: int, month: int) -> date:
    """The month's last Monday-to-Friday."""
    day = date(year, month, _month_days(year, month))
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _clamped(d: date) -> bool:
    """A payment on the last day of a month shorter than 31 days, which may be a later day clamped to it (a bill on
    the 31st is paid on 30 April): it shows only that the bill's day is that one or later."""
    return d.day == _month_days(d.year, d.month) < 31


def _as_day(d: date, anchor: int) -> int:
    """`d`'s day of the month as a bill on `anchor` reads it: a clamped payment is on the anchor when the anchor is its
    day or later."""
    return anchor if _clamped(d) and anchor >= d.day else d.day


def _within_span(days: list[date]) -> int | None:
    """The day of the month `days` all fall on within three days — their median (the lower middle), a clamped payment
    left out of it unless every one is clamped — or None."""
    plain = [d.day for d in days if not _clamped(d)]
    anchor = statistics.median_low(plain or [d.day for d in days])
    seen = [_as_day(d, anchor) for d in days]
    return anchor if max(seen) - min(seen) < STABLE_DAY_SPAN else None


def _stable_day(dates: list[date]) -> int | None:
    """The day of the month a bill is paid on, from its distinct payment dates (oldest first), in this order: the last
    six all within three days; all but one of them, when there are four or more (one odd payment, left out of the
    median; the oldest such payment when several would do); the last three alone, when the payment before them is
    three days or more from their day (the bill moved its day: a day that drifts a day a month did not). None
    otherwise."""
    recent = dates[-STABLE_DAY_PAYMENTS:]
    anchor = _within_span(recent)
    if anchor is not None:
        return anchor
    if len(recent) >= ODD_PAYMENT_MIN:
        for i in range(len(recent)):
            anchor = _within_span(recent[:i] + recent[i + 1:])
            if anchor is not None:
                return anchor
    if len(dates) > MOVED_DAY_PAYMENTS:
        anchor = _within_span(dates[-MOVED_DAY_PAYMENTS:])
        before = dates[-MOVED_DAY_PAYMENTS - 1]
        if anchor is not None and abs(_as_day(before, anchor) - anchor) >= STABLE_DAY_SPAN:
            return anchor
    return None


def monthly_rule(dates: list[date]) -> str | int | None:
    """Which day a monthly series is paid on, from its distinct payment dates (oldest first): FIRST_BUSINESS_DAY or
    LAST_BUSINESS_DAY when none of the last four fell on a weekend and three of them fell on that day of their month,
    else its stable day of the month (an int, `_stable_day`), else None — an irregular series, stepped by its median
    gap."""
    recent = dates[-BUSINESS_DAY_PAYMENTS:]
    # a payment on a Saturday or Sunday says the bill is paid on its calendar day, whatever the weekdays around it
    if all(d.weekday() < 5 for d in recent):
        if sum(d == first_business_day(d.year, d.month) for d in recent) >= BUSINESS_DAY_MIN:
            return FIRST_BUSINESS_DAY
        if sum(d == last_business_day(d.year, d.month) for d in recent) >= BUSINESS_DAY_MIN:
            return LAST_BUSINESS_DAY
    return _stable_day(dates)


def _rule_date(rule: str | int, year: int, month: int) -> date:
    """The day a rule (`monthly_rule`) gives in a month: its first or last business day, or the stable day clamped to
    the month's last day."""
    if rule == FIRST_BUSINESS_DAY:
        return first_business_day(year, month)
    if rule == LAST_BUSINESS_DAY:
        return last_business_day(year, month)
    return date(year, month, min(int(rule), _month_days(year, month)))


def monthly_dates(last_seen: str, rule: str | int, n: int = 3) -> list[str]:
    """The next `n` expected dates of a monthly series by its rule (`monthly_rule`): the first is the first date the
    rule gives at least FIRST_EXPECTED_MIN_DAYS after the last payment, from the last payment's own month on; then one
    in each month after it."""
    last = date.fromisoformat(last_seen)
    index = last.year * 12 + last.month - 1
    while (_rule_date(rule, index // 12, index % 12 + 1) - last).days < FIRST_EXPECTED_MIN_DAYS:
        index += 1
    return [_rule_date(rule, (index + k) // 12, (index + k) % 12 + 1).isoformat() for k in range(n)]


def promote(record: dict, distinct_dates: int | None = None) -> bool:
    """The promotion rule — pure, so the thresholds are testable without a series.

    The minimum counts DISTINCT charge dates: same-day duplicates raise `occurrences` but
    add no interval, so three rows on two dates are one observed gap, not a rhythm.
    `distinct_dates` defaults to `occurrences` for a bare record (a hand-built dict in a
    test); `detect` always passes the real count."""
    cadence = record.get("cadence")
    seen = record.get("occurrences", 0) if distinct_dates is None else distinct_dates
    return bool(record.get("active")
                and cadence in MIN_OCCURRENCES
                and record.get("amount_mode") in AMOUNT_MODE_FACTOR
                and seen >= MIN_OCCURRENCES[cadence]
                and record.get("confidence", 0.0) >= CONFIDENCE_FLOOR)


def detect(txns: list[Txn], data_end: str,
           cancelled: Iterable[dict] = ()) -> list[dict]:
    """`cancelled` retires series the family has told us they cancelled (see
    assumptions.yaml `cancelled_subscriptions`). Activity is otherwise inferred purely
    from charge history, which lags reality by up to two cadence periods — an annual
    subscription keeps reading "active" for ~2 years after its last charge. Declaring it
    only forces active=False; nothing else is faked, and if the charge ever reappears the
    series simply re-detects from the data, so a restart needs no config change."""
    cancelled_vendors = {c["vendor"].casefold() for c in cancelled if c.get("vendor")}
    groups: dict[tuple[str, str], list[Txn]] = defaultdict(list)
    for t in txns:
        if t.parent_id is not None:          # a split purchase is one charge — its parent is the series' row
            continue
        if t.type in SPEND_TYPES and t.amount_cents < 0 and t.vendor:
            groups[(t.vendor, t.account_id)].append(t)

    out = []
    for (vendor, account_id), all_rows in sorted(groups.items()):
        if len(all_rows) < 3:
            continue
        all_rows.sort(key=lambda t: t.date)
        # detect on the core amount cluster: small add-on/top-up charges from
        # the same vendor (e.g. a gym's small top-ups beside its monthly plan)
        # would otherwise break both cadence and the amount-spread test
        med_all = statistics.median(abs(t.amount_cents) for t in all_rows)
        rows = [t for t in all_rows if abs(t.amount_cents) >= 0.4 * med_all]
        if len(rows) < 3:
            continue
        dates = [date.fromisoformat(t.date) for t in rows]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:], strict=False) if (b - a).days > 0]
        if not gaps:
            continue
        cad = _cadence(gaps)
        if cad is None:
            continue
        cadence, share = cad
        amounts = [abs(t.amount_cents) for t in rows]
        mode = _amount_mode(amounts)
        if mode is None:
            continue
        median_gap = statistics.median(gaps)
        median_gap_days = round(median_gap)
        typical = int(statistics.median(amounts[-6:]))
        last_seen = rows[-1].date
        rule = monthly_rule(sorted(set(dates))) if cadence == "monthly" else None
        nexts = (expected_dates(last_seen, median_gap_days) if rule is None
                 else monthly_dates(last_seen, rule))
        active = ((date.fromisoformat(data_end) - date.fromisoformat(last_seen)).days
                  <= 2 * median_gap)
        if vendor.casefold() in cancelled_vendors:
            active = False
        rec = {
            "vendor": vendor, "account_id": account_id,
            "category": rows[-1].category,
            "cadence": cadence, "amount_mode": mode,
            "typical_amount_cents": typical,
            "last_amount_cents": abs(rows[-1].amount_cents),
            "occurrences": len(rows),
            "first_seen": rows[0].date, "last_seen": last_seen,
            "next_expected": nexts[0], "active": active,
            "annualized_cents": annualized_cents(typical, cadence),
            "price_changes": _price_changes(rows),
            "median_gap_days": median_gap_days,
            "expected_dates": nexts,
            "grace_days": GRACE_DAYS[cadence],
            "confidence": round(share * AMOUNT_MODE_FACTOR[mode], 3),
        }
        # `gaps` skips zero-day gaps (same-day duplicates), so it holds one entry per step
        # between distinct charge dates: distinct dates = len(gaps) + 1
        rec["schedule"] = promote(rec, distinct_dates=len(gaps) + 1)
        out.append(rec)
    return out


def _cadence(gaps: list[int]) -> tuple[str, float] | None:
    """The best-fitting band and the share of gaps inside it (>= 0.7), or None."""
    best = None
    for name, (lo, hi) in CADENCE_BANDS.items():
        share = sum(1 for g in gaps if lo <= g <= hi) / len(gaps)
        if share >= 0.7 and (best is None or share > best[1]):
            best = (name, share)
    return best


def _amount_mode(amounts: list[int]) -> str | None:
    med = statistics.median(amounts)
    if med == 0:
        return None
    distinct = {a: amounts.count(a) for a in set(amounts)}
    exact_share = max(distinct.values()) / len(amounts)
    stdev = statistics.pstdev(amounts)
    if stdev / med < 0.02 or exact_share >= 0.8:
        return "fixed"
    # price-stepped subscriptions: a few exact price points, each repeated
    if len(distinct) <= 3 and min(distinct.values()) >= 2:
        return "fixed"
    # tier-wandering subscriptions (e.g. a membership moving between price tiers) are
    # still the same charge; truly wide spreads (frequent stores) stay rejected
    if all(abs(a - med) / med <= 0.35 for a in amounts):
        return "variable"
    return None


def _price_changes(rows: list[Txn]) -> list[dict]:
    changes = []
    current = abs(rows[0].amount_cents)
    i = 1
    while i < len(rows):
        amt = abs(rows[i].amount_cents)
        if current and abs(amt - current) / current > 0.01:
            # require the new amount to repeat at least twice to count as a re-price
            repeats = sum(1 for r in rows[i:] if abs(r.amount_cents) == amt)
            if repeats >= 2:
                changes.append({"date": rows[i].date,
                                "from_cents": current, "to_cents": amt})
                current = amt
        i += 1
    return changes

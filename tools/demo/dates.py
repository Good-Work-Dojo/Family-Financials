# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Calendar arithmetic the plan and the behaviours share. A business day is Monday to Friday: there is no holiday
calendar."""
from __future__ import annotations

from datetime import date, timedelta


def shift_months(day: date, months: int) -> date:
    """The first day of the month `months` after (negative: before) `day`'s month."""
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def month_end(day: date) -> date:
    """The last day of `day`'s month."""
    return shift_months(day, 1) - timedelta(days=1)


def month_starts(first: date, last: date) -> list[date]:
    """The first day of every month from `first`'s month to `last`'s, both included."""
    out, month = [], first.replace(day=1)
    while month <= last:
        out.append(month)
        month = shift_months(month, 1)
    return out


def is_business_day(day: date) -> bool:
    return day.weekday() < 5


def business_day_on_or_before(day: date) -> date:
    """`day`, or the Friday before it when it falls on a weekend."""
    while not is_business_day(day):
        day -= timedelta(days=1)
    return day


def business_day_on_or_after(day: date) -> date:
    """`day`, or the Monday after it when it falls on a weekend."""
    while not is_business_day(day):
        day += timedelta(days=1)
    return day


def last_business_day(day: date) -> date:
    """The last Monday-to-Friday of `day`'s month."""
    return business_day_on_or_before(month_end(day))


def add_business_days(day: date, n: int) -> date:
    """The `n`-th business day after `day` (n >= 0; 0 is `day` itself)."""
    if n < 0:
        raise ValueError(f"add_business_days counts forward, got {n}")
    while n:
        day += timedelta(days=1)
        n -= is_business_day(day)
    return day


def business_days_between(a: date, b: date) -> int:
    """How many business days lie after the earlier of the two days, up to and including the later one: the distance
    the pairing of two legs is measured in."""
    first, last = min(a, b), max(a, b)
    return sum(is_business_day(first + timedelta(days=k)) for k in range(1, (last - first).days + 1))

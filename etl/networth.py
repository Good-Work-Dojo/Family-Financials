# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Net worth as one series map: per account, its dated balances; per manual asset (etl/manual_assets.py: the
config's entries and the assets entered in the app), its dated values. A snapshot on a day reads each account
series' last point on or before that day and each manual asset's value on the day (manual_value_on), and the daily
line (networth_daily.json) sums the same figures on every date an account has a point or, within the accounts' span,
a manual asset takes a value or is acquired. Pure functions over plain data
(no Config, no I/O): Pipeline.parse_assets builds the map once per run and reads a snapshot from it on every date
snapshot_dates names: each month end before the current date, then the current date.

Which accounts count: every registry account of a cash kind, a brokerage or a loan, and a credit card when the
household counts cards (household.yaml `net_worth.include_credit_cards`), plus every manual asset. An account that
has never been observed is a series with no point. An account series is carried at its last point however old it is
(an account that stopped reporting keeps its last balance; the feed check `simplefin.stale.<id>` says so); one with
no point by the day is listed as missing, never guessed. A manual asset is never missing: before its first value it
is carried back at that value (the household stated what it is worth, not when it came), unless it states the day it
was acquired, before which it is not held at all.
"""
from __future__ import annotations

import bisect
import calendar
import datetime as dt
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import (
    CASH_KINDS,
    ILLIQUID_CLASSES,
    LIABILITY_CLASSES,
    LIABILITY_KINDS,
    AssetRow,
    BalanceObs,
    Holding,
    account_asset_class,
)

if TYPE_CHECKING:
    from .manual_assets import ManualAsset

# the kinds whose balances are net worth; credit cards join them when the household counts cards
SERIES_KINDS = CASH_KINDS | {"brokerage", "loan"}
CARD_KIND = "credit-card"
MANUAL_PREFIX = "manual:"       # a manual asset's row id is this and its id
MAX_MANUAL_ID_LEN = 64          # the longest manual asset id: a config entry's slug included (validate checks it)
# a manual value's bound either way, in dollars: the cents of a larger one overflow the database's 64-bit integers
MAX_MANUAL_VALUE = 1e12


@dataclass(frozen=True)
class Series:
    account_id: str | None      # the registry id; None for a manual asset
    name: str                   # the display name every row of the series takes (a margin remainder excepted)
    institution: str            # "" for a manual asset entered in the app, which names none
    owner: str
    asset_class: str
    liquid: bool
    kind: str | None            # the registry kind; None for a manual asset
    manual: bool
    margin: bool                # a `margin: true` brokerage: the current snapshot breaks it into its positions
    points: tuple[tuple[str, int], ...]   # (ISO date, cents), ascending, one per date
    acquired: str | None = None # a manual asset's ISO day of acquisition, when it states one
    manual_id: str | None = None  # a manual asset's id


SeriesMap = dict[str, Series]   # row id -> series: the registry id, or `manual:<id>` for a manual asset


@dataclass(frozen=True)
class Row:
    """One net-worth row: what models.AssetRow carries, plus the id of the series it comes from (a margin account's
    rows share their account's id)."""
    row_id: str
    account: str
    account_id: str | None
    institution: str
    owner: str
    asset_class: str
    value_cents: int
    liquid: bool
    source: str                 # bank_balance | simplefin_balance | positions | stated (models.AssetRow)
    as_of: str
    manual_id: str | None = None  # the manual asset the row is; None for an account's row
    carried_back: bool = False  # a manual asset before its first value, at that value (`as_of` is its date)


@dataclass(frozen=True)
class Snapshot:
    date: str
    rows: tuple[Row, ...]
    missing: tuple[str, ...]    # row ids of the account series with no point on or before `date`, in map order
    coverage: float             # series held on the day with a row / series held on the day (unrounded)


def _liquid(asset_class: str, kind: str | None) -> bool:
    """A row is liquid unless its class is an illiquid one or a debt, or its account is a debt."""
    return asset_class not in ILLIQUID_CLASSES and asset_class not in LIABILITY_CLASSES and kind not in LIABILITY_KINDS


def manual_slug(account: str) -> str:
    """A manual entry's id: its name lowercased, every run of other than letters and digits one `-`, none at either
    end (validate refuses a name whose id comes out empty or over MAX_MANUAL_ID_LEN characters)."""
    return re.sub(r"[\W_]+", "-", account.lower()).strip("-")


def manual_row_id(account: str) -> str:
    """`manual:` plus the entry's id (manual_slug)."""
    return MANUAL_PREFIX + manual_slug(account)


def manual_entry_problems(entries: Iterable) -> list[str]:
    """accounts.yaml `manual_entries`: two entries whose names give one row id (manual_row_id) would be one series,
    so the later one is refused. Entries without a text `account` are left to the shape checks."""
    out, first = [], {}
    for i, e in enumerate(entries or ()):
        name = e.get("account") if isinstance(e, dict) else None
        if not isinstance(name, str):
            continue
        rid = manual_row_id(name)
        if rid in first:
            out.append(f"accounts.yaml: manual_entries[{i}].account: same name as entry {first[rid]} — "
                       "give each entry its own name")
        else:
            first[rid] = i
    return out


def build_series(balance_obs: Iterable[BalanceObs], accounts: Mapping[str, Mapping],
                 manual_assets: Sequence[ManualAsset], *, include_credit_cards: bool) -> SeriesMap:
    """The series map, registry accounts first (in `accounts` order), then the manual assets (in the given order:
    the pipeline passes manual_assets.effective's).

    Every account of an included kind has a series, one never observed a series with no points (it is missing at
    every date and counts in `coverage`, but dates nothing). An account's points are its observations, one per date:
    a statement-file observation (source `csv`) beats a feed one on the same date, and for one account and date the
    last feed observation in the list wins (likewise for two file ones). The pipeline appends them oldest file first,
    a newer feed file's report of a date replacing an older one's, so the newest report of a balance date is the one
    booked. Its class is models.account_asset_class; it is liquid unless the class or the kind is a debt or the class
    is illiquid. A manual asset is its values, under row id `manual:<id>`, with its own class, owner, institution (""
    when it names none), `liquid` (a debt class never is) and acquisition day. Two manual assets of one id raise
    ValueError (the effective list never holds two)."""
    kinds = SERIES_KINDS | ({CARD_KIND} if include_credit_cards else set())
    by_date: dict[str, dict[str, tuple[bool, int]]] = {}
    for b in balance_obs:
        meta = accounts.get(b.account_id)
        if meta is None or meta.get("kind") not in kinds:
            continue
        day = by_date.setdefault(b.account_id, {})
        from_file = b.source == "csv"
        held = day.get(b.as_of)
        if held is None or from_file or not held[0]:
            day[b.as_of] = (from_file, b.balance_cents)
    out: SeriesMap = {}
    for account_id, meta in accounts.items():
        if meta.get("kind") not in kinds:
            continue
        cls = account_asset_class(meta)
        out[account_id] = Series(
            account_id=account_id, name=meta["name"], institution=meta["institution"], owner=meta["owner"],
            asset_class=cls, liquid=_liquid(cls, meta["kind"]), kind=meta["kind"], manual=False,
            margin=bool(meta.get("margin")),
            points=tuple(sorted((d, cents) for d, (_, cents) in by_date.get(account_id, {}).items())))
    for a in manual_assets:
        row_id = MANUAL_PREFIX + a.id
        if row_id in out:
            raise ValueError(f"two manual assets with the id {a.id!r}")
        out[row_id] = Series(
            account_id=None, name=a.name, institution=a.institution or "", owner=a.owner, asset_class=a.asset_class,
            liquid=a.liquid and a.asset_class not in LIABILITY_CLASSES, kind=None, manual=True, margin=False,
            points=tuple((day.isoformat(), cents) for day, cents in a.values),
            acquired=None if a.acquired is None else a.acquired.isoformat(), manual_id=a.id)
    return out


def current_date(series: SeriesMap, today: str) -> str | None:
    """The current snapshot's date: the newest point of any account series. A manual asset dates nothing while an
    account has a point (a later value is carried back to that day). With no account point, the newest value of any
    manual asset dated on or before `today` (the ingest day, the household's), else `today` itself: a value dated
    ahead takes effect on its day, as with accounts present (a first value is carried back to the date). None for a
    map with no point at all, or when no manual asset is held on that date (each one acquired after it)."""
    accounts = [s.points[-1][0] for s in series.values() if not s.manual and s.points]
    if accounts:
        return max(accounts)
    manual = [s for s in series.values() if s.manual and s.points]
    if not manual:
        return None
    day = max((p[0] for s in manual if (p := _last_on_or_before(s.points, today)) is not None), default=today)
    return day if any(manual_value_on(s, day) is not None for s in manual) else None


def _month_end(year: int, month: int) -> str:
    return dt.date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def snapshot_dates(series: SeriesMap, today: str) -> list[str]:
    """The dates a snapshot is read on, oldest first: the last day of every month from the month of the earliest
    point of any account series while that day is before the current date, then the current date (current_date, which
    `today`, the ingest day, bounds when no account has a point), once even when it is a month end. A manual asset
    starts no month (it is in every snapshot it is held on, carried back before its first value); with no account
    series the one date is current_date's. No point anywhere, or no series held on that date: no date."""
    day = current_date(series, today)
    if day is None:
        return []
    out: list[str] = []
    first = min((s.points[0][0] for s in series.values() if not s.manual and s.points), default=None)
    if first is not None:
        year, month = int(first[:4]), int(first[5:7])
        while (end := _month_end(year, month)) < day:
            out.append(end)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    out.append(day)
    return out


def _row_source(s: Series) -> str:
    if s.manual:
        return "stated"
    return "bank_balance" if s.kind in CASH_KINDS else "simplefin_balance"


def _last_on_or_before(points: tuple[tuple[str, int], ...], day: str) -> tuple[str, int] | None:
    """The last point dated on or before `day` (a point on the day itself included), None when every point is later.
    `points` ascend by date, one per date (build_series), so a binary search on the dates finds it: snapshot_at and
    daily read it once per series per date, and a scan of every point would make a long history quadratic."""
    i = bisect.bisect_right(points, day, key=lambda p: p[0])
    return points[i - 1] if i else None


def manual_value_on(s: Series, day: str) -> tuple[str, int, bool] | None:
    """A manual asset's value on `day` as (as_of, cents, carried_back), the one rule snapshot_at and daily share.
    Before the day it was acquired (when it states one) it is not held: None, so no row, no part of the total, and
    not missing. Otherwise the value in force is its last value dated on or before the day, and before its first
    value that first value, carried back (as_of is that value's date, carried_back true). A value dated after the
    day is never in force on it except as that carried-back first value."""
    if not s.points or (s.acquired is not None and day < s.acquired):
        return None
    point = _last_on_or_before(s.points, day)
    if point is not None:
        return point[0], point[1], False
    return s.points[0][0], s.points[0][1], True


def _margin_rows(row_id: str, s: Series, as_of: str, balance_cents: int, held: list[Holding]) -> list[Row]:
    """A live margin account: one row per holdings class at the positions' gross value (source `positions`, dated by
    the holdings), then the remainder `balance - positions` — a margin loan (`<institution> Margin`, not liquid) when
    negative, uninvested cash (`<institution> Cash`) when positive, no row when zero — at the feed's balance date.
    The rows sum to the feed balance and all carry the account's id."""
    gross: dict[str, list] = {}
    for h in held:
        slot = gross.setdefault(h.asset_class, [0, h.as_of])
        slot[0] += h.value_cents
    rows = [Row(row_id=row_id, account=s.name, account_id=s.account_id, institution=s.institution, owner=s.owner,
                asset_class=cls, value_cents=cents, liquid=_liquid(cls, s.kind), source="positions", as_of=day)
            for cls, (cents, day) in sorted(gross.items())]
    remainder = balance_cents - sum(h.value_cents for h in held)
    if remainder:
        label, cls = ("Margin", "Margin") if remainder < 0 else ("Cash", "Cash")
        rows.append(Row(row_id=row_id, account=f"{s.institution} {label}", account_id=s.account_id,
                        institution=s.institution, owner=s.owner, asset_class=cls, value_cents=remainder,
                        liquid=remainder > 0, source="simplefin_balance", as_of=as_of))
    return rows


def snapshot_at(series: SeriesMap, day: str, *, holdings: Iterable[Holding] | None = None,
                live: frozenset[str] = frozenset()) -> Snapshot:
    """Net worth on `day`: per account series its last point on or before the day, carried however old, else its row
    id in `missing`; per manual asset its value on the day (manual_value_on: carried back before its first value,
    never missing), and no row at all before the day it was acquired. `coverage` = the series held on the day with a
    row / the series held on the day (every account series is; a manual asset from its acquisition on). A series is
    one row (an account's at its registry name, a manual asset's at its own name, with its id and whether it is
    carried back), except a margin account when `holdings` is given, the account is in `live` (it reported in the
    newest feed file) and holdings of it exist: then its positions and the remainder (_margin_rows). Any other
    brokerage is one row at its balance whatever its holdings. An empty map, or one with no series held on the day,
    raises ValueError."""
    if not series:
        raise ValueError("snapshot_at: no series, so no snapshot")
    held: dict[str, list[Holding]] = {}
    for h in holdings or ():
        held.setdefault(h.account_id, []).append(h)
    rows: list[Row] = []
    missing: list[str] = []
    counted = 0
    for row_id, s in series.items():
        if s.manual:
            value = manual_value_on(s, day)
            if value is None:
                continue
            counted += 1
            as_of, cents, carried = value
            rows.append(Row(row_id=row_id, account=s.name, account_id=None, institution=s.institution, owner=s.owner,
                            asset_class=s.asset_class, value_cents=cents, liquid=s.liquid, source=_row_source(s),
                            as_of=as_of, manual_id=s.manual_id, carried_back=carried))
            continue
        counted += 1
        point = _last_on_or_before(s.points, day)
        if point is None:
            missing.append(row_id)
            continue
        as_of, cents = point
        positions = held.get(s.account_id) if s.margin and s.account_id in live else None
        if positions:
            rows.extend(_margin_rows(row_id, s, as_of, cents, positions))
        else:
            rows.append(Row(row_id=row_id, account=s.name, account_id=s.account_id, institution=s.institution,
                            owner=s.owner, asset_class=s.asset_class, value_cents=cents, liquid=s.liquid,
                            source=_row_source(s), as_of=as_of))
    if not counted:
        raise ValueError(f"snapshot_at: no series is held on {day}, so no snapshot")
    return Snapshot(date=day, rows=tuple(rows), missing=tuple(missing), coverage=(counted - len(missing)) / counted)


def daily(series: SeriesMap) -> list[tuple[str, int, float]]:
    """The daily net-worth line, oldest first, as (date, cents, coverage): one point on every date an account series
    has a point, and on every day within the account points' span (the first account point to the last) on which a
    manual asset takes a value or is acquired, so the line moves on the day net worth does; a manual date outside that
    span adds no point (no snapshot reads one). On a date every account series with a point counts its last point on
    or before it, as snapshot_at reads it, else its first point, held flat backwards; an account never observed
    counts nothing; a manual asset counts its value on the day (manual_value_on, as snapshot_at reads it: carried
    back before its first value, nothing before it was acquired). Cards are in the line exactly when they are in the
    map (`include_credit_cards`). `coverage` = the account series with a point on or before the date / the account
    series with any point: a manual asset and an account never observed are in neither count. No account series with
    a point: no line.

    The line and the snapshots read one map, so a snapshot whose every account series with a point has one by its
    day totals the line's value at the last line date on or before that day, and the last point is the current
    snapshot's total."""
    accounts = [s for s in series.values() if not s.manual and s.points]
    if not accounts:
        return []
    manual = [s for s in series.values() if s.manual]
    days = {d for s in accounts for d, _ in s.points}
    first, last = min(days), max(days)
    for s in manual:
        days.update(d for d in (*(d for d, _ in s.points), *(() if s.acquired is None else (s.acquired,)))
                    if first <= d <= last)
    out: list[tuple[str, int, float]] = []
    for day in sorted(days):
        cents = sum((_last_on_or_before(s.points, day) or s.points[0])[1] for s in accounts)
        cents += sum(v[1] for s in manual if (v := manual_value_on(s, day)) is not None)
        covered = sum(1 for s in accounts if s.points[0][0] <= day)
        out.append((day, cents, covered / len(accounts)))
    return out


def asset_rows(snapshot: Snapshot) -> list[AssetRow]:
    """The snapshot's rows as the AssetRows the database and the export take, each stamped with the snapshot date."""
    return [AssetRow(snapshot_date=snapshot.date, account=r.account, institution=r.institution, owner=r.owner,
                     asset_class=r.asset_class, value_cents=r.value_cents, liquid=r.liquid, source=r.source,
                     as_of=r.as_of, account_id=r.account_id, manual_id=r.manual_id, carried_back=r.carried_back)
            for r in snapshot.rows]


def missing_names(series: SeriesMap, snapshot: Snapshot) -> list[str]:
    """The display names of the snapshot's missing series, sorted (assets_timeseries.json `missing`)."""
    return sorted(series[rid].name for rid in snapshot.missing)


def mortgage_included(snapshot: Snapshot, mortgage_id: str | None) -> bool:
    """True when no `mortgage: true` account is configured or that account's own row is in the snapshot: a
    hand-stated row of the Mortgage class does not stand in for it, so nothing is subtracted and nothing guessed."""
    return mortgage_id is None or any(r.account_id == mortgage_id for r in snapshot.rows)

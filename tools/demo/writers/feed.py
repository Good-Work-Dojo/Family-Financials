# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Bank-feed snapshots: `<store>/snapshots/accounts-<UTC stamp>.json`, each a dated picture of the accounts the feed
reports. The balance includes pending rows; the transactions list carries them flagged, and the engine skips those."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..ledger import Ledger, Row
from . import plain


@dataclass(frozen=True)
class Holding:
    symbol: str
    name: str
    shares: str
    value_cents: int
    cost_cents: int


@dataclass(frozen=True)
class FeedAccount:
    """One account the feed reports. Its kind is the ledger's: a credit card reports its limit and available
    credit."""
    account: str                       # the registry id
    feed_id: str                       # the id the feed reports it under
    name: str
    org: str
    transactions: bool = True
    credit_limit_cents: int | None = None
    holdings: tuple[Holding, ...] = ()


def _noon(day: date, zone: ZoneInfo) -> int:
    return int(datetime.combine(day, time(12), tzinfo=zone).timestamp())


def _transaction(row: Row, zone: ZoneInfo) -> dict:
    out: dict = {"id": row.feed_id}
    if row.pending:
        out["posted"] = 0
        out["transacted_at"] = _noon(row.date, zone)
    else:
        out["posted"] = _noon(row.date, zone)
    out["amount"] = plain(row.cents)
    out["description"] = row.desc
    if row.payee:
        out["payee"] = row.payee
    out["pending"] = row.pending
    if row.bank_category:
        out["extra"] = {"category": row.bank_category}
    return out


def listed(ledger: Ledger, account: str, today: date, window_days: int, ahead_days: int = 0) -> list[Row]:
    """The rows a snapshot taken on `today` lists for the account, pending ones included: the last `window_days`
    days, `today` included, and the `ahead_days` days after it (rows the bank already dates after today), oldest
    first."""
    return ledger.rows(account, today - timedelta(days=window_days - 1), today + timedelta(days=ahead_days))


def _transactions(account: FeedAccount, ledger: Ledger, today: date, window_days: int, ahead_days: int,
                  zone: ZoneInfo) -> list[dict]:
    rows = listed(ledger, account.account, today, window_days, ahead_days)
    seen: set[str] = set()
    for row in rows:
        if not row.feed_id:
            raise ValueError(f"{account.account}: a row needs a feed_id to be in a snapshot "
                             f"({row.date} {row.desc!r})")
        if row.feed_id in seen:
            raise ValueError(f"{account.account}: feed_id {row.feed_id!r} is used by more than one row")
        seen.add(row.feed_id)
    return [_transaction(r, zone) for r in rows]


def _account(spec: FeedAccount, ledger: Ledger, today: date, window_days: int, ahead_days: int,
             zone: ZoneInfo) -> dict:
    card = ledger.kind(spec.account) == "credit-card"
    balance = ledger.balance(spec.account, today, with_pending=True)
    out: dict = {"id": spec.feed_id, "name": spec.name, "org": {"name": spec.org}, "currency": "USD",
                 "balance": plain(balance)}
    if card:
        if spec.credit_limit_cents is not None:
            out["available-balance"] = plain(spec.credit_limit_cents - max(0, -balance))
    else:
        out["available-balance"] = plain(balance)
    out["balance-date"] = _noon(today, zone)
    if card and spec.credit_limit_cents is not None:
        out["extra"] = {"credit-limit": plain(spec.credit_limit_cents)}
    if spec.transactions:
        out["transactions"] = _transactions(spec, ledger, today, window_days, ahead_days, zone)
    if spec.holdings:
        out["holdings"] = [{"symbol": h.symbol, "description": h.name, "shares": h.shares,
                            "market_value": plain(h.value_cents), "cost_basis": plain(h.cost_cents)}
                           for h in spec.holdings]
    return out


def write_snapshot(store: Path, at: datetime, accounts: list[FeedAccount], ledger: Ledger, *, zone: str,
                   window_days: int = 45, ahead_days: int = 0, omit: frozenset[str] = frozenset(),
                   unmapped: tuple[dict, ...] = ()) -> Path:
    """Write the snapshot fetched at `at` (timezone-aware) and return its path. A snapshot is never overwritten:
    two fetches never share a second. Rows dated in the `ahead_days` days after the snapshot's day are listed as
    posted rows with their own date; every balance stays that day's own. `omit` leaves registry accounts out of
    this one; `unmapped` appends raw feed accounts as given. An account listed twice, two accounts under one feed
    id, a window under one day and a negative `ahead_days` are refused before anything is written."""
    if at.tzinfo is None:
        raise ValueError(f"{at} is not timezone-aware")
    if window_days < 1:
        raise ValueError(f"the transaction window must be at least one day, got {window_days}")
    if ahead_days < 0:
        raise ValueError(f"ahead_days counts days after the snapshot, got {ahead_days}")
    for attr in ("account", "feed_id"):
        values = [getattr(a, attr) for a in accounts]
        twice = sorted({v for v in values if values.count(v) > 1})
        if twice:
            raise ValueError(f"a snapshot lists each {attr} once, got {', '.join(twice)} more than once")
    tz = ZoneInfo(zone)
    today = at.astimezone(tz).date()
    doc = {"accounts": [_account(a, ledger, today, window_days, ahead_days, tz) for a in accounts
                        if a.account not in omit]
           + list(unmapped)}
    directory = store / "snapshots"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"accounts-{at.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    with open(path, "x", encoding="utf-8", newline="") as f:
        f.write(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    return path

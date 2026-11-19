# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""A double-entry ledger of an invented household, in integer cents.

An account opens with a balance (the balance at the end of the day before `opened`). Money then enters through
`post` (to or from outside the household), `move` (between two household accounts, two exactly opposite rows) or
`adjust` (a balance change no row reports: a market move, loan principal, interest the issuer nets). A balance is
always opening + the rows + the adjustments, at the end of a day; a pending row counts only when asked."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

KINDS = ("checking", "savings", "credit-card", "brokerage", "loan")
CASH_KINDS = ("checking", "savings")      # accounts whose balance may never go below zero
FUTURE_DAYS = 7         # a row may be dated this many days after the ledger's end


def half_up(numerator: int, denominator: int) -> int:
    """`numerator / denominator` to the nearest whole number, a half away from zero: the generator's one rounding
    rule for money, in integers (Decimal's ROUND_HALF_UP; never the half-to-even `round()`). `denominator` > 0."""
    if denominator <= 0:
        raise ValueError(f"half_up divides by a positive number, got {denominator}")
    whole, rest = divmod(abs(numerator), denominator)
    whole += 2 * rest >= denominator
    return whole if numerator >= 0 else -whole


def cents_of(dollars: float | int | str) -> int:
    """A dollar amount as a config file writes it (read as its decimal text, never as the binary float), in cents by
    `half_up`."""
    return half_up(*(Decimal(str(dollars)) * 100).as_integer_ratio())


@dataclass(frozen=True)
class Expect:
    """What the engine must make of a row. `pair` is a key two rows share when they must end in one transfer group;
    `split` is the legs `((category, subcategory, cents), ...)`; `question` names the transfer rule whose open
    question the row raises; `gap` is the counter hint under which the row, an internal leg whose other side the
    data does not show, is listed as a gap in the data."""
    type: str
    category: str | None
    subcategory: str | None
    source: str
    vendor: str | None
    needs_review: bool = False
    internal: bool = False
    earner: str | None = None
    employer: str | None = None
    pair: str | None = None
    split: tuple[tuple[str, str, int], ...] | None = None
    question: str | None = None
    gap: str | None = None


@dataclass(frozen=True)
class Row:
    account: str
    date: date
    cents: int
    desc: str
    bank_type: str | None = None
    bank_category: str | None = None
    payee: str | None = None
    pending: bool = False
    feed_id: str | None = None
    expect: Expect | None = None
    plant: str | None = None

    def __post_init__(self) -> None:
        if not self.desc or " ".join(self.desc.split()) != self.desc:
            raise ValueError(f"{self.account}: description {self.desc!r} is empty or not whitespace-squashed "
                             "(the parsers would rewrite it)")


@dataclass(frozen=True)
class _Account:
    kind: str
    opening: int
    opened: date


class Ledger:
    def __init__(self, start: date, end: date):
        if end < start:
            raise ValueError(f"ledger ends {end} before it starts {start}")
        self.start = start
        self.end = end
        self._accounts: dict[str, _Account] = {}
        self._rows: dict[str, list[Row]] = {}
        self._adjustments: dict[str, list[tuple[date, int]]] = {}
        self._moves: list[tuple[Row, Row]] = []

    # ------------------------------------------------------------------ writing

    def open(self, account: str, kind: str, opening_cents: int, opened: date | None = None) -> None:
        if account in self._accounts:
            raise ValueError(f"{account}: already opened")
        if kind not in KINDS:
            raise ValueError(f"{account}: kind {kind!r} is not one of {', '.join(KINDS)}")
        when = self.start if opened is None else opened
        if not self.start <= when <= self.end:
            raise ValueError(f"{account}: opened {when} is outside the ledger's range {self.start} to {self.end}")
        self._accounts[account] = _Account(kind, opening_cents, when)
        self._rows[account] = []
        self._adjustments[account] = []

    def post(self, row: Row) -> Row:
        """Money to or from outside the household."""
        self._validate(row.account, row.date, row.cents)
        self._append(row)
        return row

    def move(self, out: Row, into: Row) -> tuple[Row, Row]:
        """Money between two household accounts. The legs may differ in date and descriptor, never in amount."""
        if out.cents == 0:
            raise ValueError(f"{out.account}: a zero amount on {out.date}")
        if out.cents > 0:
            raise ValueError(f"a move's outgoing leg must be negative, got {out.cents} on {out.account}")
        if out.pending != into.pending:
            raise ValueError(f"a move's legs must both be pending or both posted: {out.account} and {into.account}")
        if into.cents != -out.cents:
            raise ValueError(f"a move's legs must be exactly opposite: {out.cents} on {out.account}, "
                             f"{into.cents} on {into.account}")
        if out.account == into.account:
            raise ValueError(f"a move's two accounts must differ, both are {out.account}")
        self._validate(out.account, out.date, out.cents)
        self._validate(into.account, into.date, into.cents)
        self._append(out)
        self._append(into)
        self._moves.append((out, into))
        return out, into

    def adjust(self, account: str, on: date, cents: int) -> None:
        """A balance change no row reports."""
        self._validate(account, on, cents)
        self._adjustments[account].append((on, cents))

    def _validate(self, account: str, on: date, cents: int) -> None:
        info = self._accounts.get(account)
        if info is None:
            raise ValueError(f"{account}: account was never opened")
        if isinstance(cents, bool) or not isinstance(cents, int):
            raise ValueError(f"{account}: amount {cents!r} is not integer cents")
        if cents == 0:
            raise ValueError(f"{account}: a zero amount on {on}")
        if not self.start <= on <= self.end + timedelta(days=FUTURE_DAYS):
            raise ValueError(f"{account}: {on} is outside the ledger's range {self.start} to {self.end} "
                             f"(plus {FUTURE_DAYS} days)")
        if on < info.opened:
            raise ValueError(f"{account}: {on} is before the account opened on {info.opened}")

    def _append(self, row: Row) -> None:
        self._rows[row.account].append(row)

    # ------------------------------------------------------------------ reading

    def _info(self, account: str) -> _Account:
        info = self._accounts.get(account)
        if info is None:
            raise ValueError(f"{account}: account was never opened")
        return info

    def accounts(self) -> list[str]:
        """Account ids in the order they were opened."""
        return list(self._accounts)

    def kind(self, account: str) -> str:
        return self._info(account).kind

    def rows(self, account: str, first: date | None = None, last: date | None = None) -> list[Row]:
        """The account's rows (pending ones included) dated `first` to `last`, both inclusive, in date order and,
        within a day, in the order they were posted."""
        self._info(account)
        picked = [r for r in self._rows[account]
                  if (first is None or r.date >= first) and (last is None or r.date <= last)]
        return sorted(picked, key=lambda r: r.date)

    def adjustments(self, account: str, first: date, last: date) -> list[tuple[date, int]]:
        """The account's adjustments dated `first` to `last`, both inclusive, in date order."""
        self._info(account)
        return sorted(((d, c) for d, c in self._adjustments[account] if first <= d <= last), key=lambda a: a[0])

    def balance(self, account: str, on: date, *, with_pending: bool = False) -> int:
        """The balance at the end of day `on`: opening + every row and adjustment dated on or before it. Before the
        account opened that is the opening balance."""
        info = self._info(account)
        total = info.opening
        total += sum(r.cents for r in self._rows[account] if r.date <= on and (with_pending or not r.pending))
        total += sum(c for d, c in self._adjustments[account] if d <= on)
        return total

    def running(self, account: str, first: date, last: date) -> list[tuple[Row, int]]:
        """Each non-pending row dated `first` to `last`, oldest first, with the account's balance after it. A day's
        adjustments apply before its first row, so a day's last row carries that day's end-of-day balance."""
        bal = self.balance(account, first - timedelta(days=1))
        adjustments = self.adjustments(account, first, last)
        applied = 0
        out: list[tuple[Row, int]] = []
        for row in self.rows(account, first, last):
            if row.pending:
                continue
            while applied < len(adjustments) and adjustments[applied][0] <= row.date:
                bal += adjustments[applied][1]
                applied += 1
            bal += row.cents
            out.append((row, bal))
        return out

    def check(self) -> None:
        """Every move is two rows of the ledger, exactly opposite, and no checking or savings account's end-of-day
        balance (non-pending rows and adjustments) is ever below zero; ValueError names the account and, for a
        balance, the first day it goes below zero."""
        for out, into in self._moves:
            if (out not in self._rows[out.account] or into not in self._rows[into.account]
                    or out.cents + into.cents != 0 or out.account == into.account):
                raise ValueError(f"{out.account}: the move to {into.account} does not balance "
                                 f"({out.cents} against {into.cents})")
        for account, info in self._accounts.items():
            if info.kind not in CASH_KINDS:
                continue
            change: dict[date, int] = {}
            for r in self._rows[account]:
                if not r.pending:
                    change[r.date] = change.get(r.date, 0) + r.cents
            for d, c in self._adjustments[account]:
                change[d] = change.get(d, 0) + c
            total = info.opening
            if total < 0:
                raise ValueError(f"{account}: balance {total} is below zero from {info.opened}")
            for day in sorted(change):
                total += change[day]
                if total < 0:
                    raise ValueError(f"{account}: balance {total} is below zero on {day}")

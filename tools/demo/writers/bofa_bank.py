# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Bank of America statement-style export: a summary block true of exactly the rows in the file, a blank line, then
the rows oldest first with a running balance. The balance runs from the file's own beginning balance, so an account
written this way carries no adjustments inside the range: one there is refused, because the file's summary and
running balance could not both be true."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from ..ledger import Ledger, Row
from . import check_range, field, grouped, us_date, write_lines


def _money(cents: int) -> str:
    return f'"{grouped(cents)}"'


def listed(ledger: Ledger, account: str, first: date, last: date) -> list[Row]:
    """The rows a file of `first` to `last` lists, in the order it lists them."""
    return [r for r in ledger.rows(account, first, last) if not r.pending]


def write(path: Path, account: str, ledger: Ledger, first: date, last: date) -> int:
    check_range(first, last)
    if ledger.adjustments(account, first, last):
        raise ValueError(f"{account}: an adjustment inside {first} to {last} would break the file's balances")
    rows = listed(ledger, account, first, last)
    beginning = ledger.balance(account, first - timedelta(days=1))
    credits = sum(r.cents for r in rows if r.cents > 0)
    debits = sum(r.cents for r in rows if r.cents < 0)
    ending = beginning + credits + debits
    start = us_date(first)
    lines = [
        "Description,,Summary Amt.",
        f"Beginning balance as of {start},,{_money(beginning)}",
        f"Total credits,,{_money(credits)}",
        f"Total debits,,{_money(debits)}",
        f"Ending balance as of {us_date(last)},,{_money(ending)}",
        "",
        "Date,Description,Amount,Running Bal.",
        f"{start},Beginning balance as of {start},,{_money(beginning)}",
    ]
    balance = beginning
    for row in rows:
        balance += row.cents
        lines.append(f"{us_date(row.date)},{field(row.desc)},{_money(row.cents)},{_money(balance)}")
    write_lines(path, lines)
    return len(rows)

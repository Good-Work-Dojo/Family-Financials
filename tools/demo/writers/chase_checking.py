# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Chase checking activity: newest first, each row with the running balance after it, a trailing empty field. An
adjustment moves the running balance on its date and prints no row."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from ..ledger import Ledger, Row
from . import check_range, field, plain, us_date, write_lines

HEADER = "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #"


def listed(ledger: Ledger, account: str, first: date, last: date) -> list[Row]:
    """The rows a file of `first` to `last` lists, in the order it lists them."""
    return [row for row, _ in reversed(ledger.running(account, first, last))]


def write(path: Path, account: str, ledger: Ledger, first: date, last: date) -> int:
    check_range(first, last)
    pairs = ledger.running(account, first, last)
    lines = [HEADER]
    for row, balance in reversed(pairs):        # later postings first, so a date's first row ends that day
        inflow = row.cents > 0
        kind = row.bank_type or ("ACH_CREDIT" if inflow else "ACH_DEBIT")
        lines.append(",".join(["CREDIT" if inflow else "DEBIT", us_date(row.date), field(row.desc),
                               plain(row.cents), field(kind), plain(balance), ""]))
    write_lines(path, lines)
    return len(pairs)

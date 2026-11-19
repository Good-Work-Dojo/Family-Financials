# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Chase credit-card activity: oldest first, the issuer's own category, no balance column, so an adjustment shows
nowhere."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from ..ledger import Ledger, Row
from . import check_range, field, plain, us_date, write_lines

HEADER = "Transaction Date,Post Date,Description,Category,Type,Amount,Memo"


def listed(ledger: Ledger, account: str, first: date, last: date) -> list[Row]:
    """The rows a file of `first` to `last` lists, in the order it lists them."""
    return [r for r in ledger.rows(account, first, last) if not r.pending]


def write(path: Path, account: str, ledger: Ledger, first: date, last: date) -> int:
    check_range(first, last)
    rows = listed(ledger, account, first, last)
    lines = [HEADER]
    for row in rows:
        kind = row.bank_type or ("Return" if row.cents > 0 else "Sale")
        lines.append(",".join([us_date(row.date), us_date(row.date), field(row.desc),
                               field(row.bank_category or ""), field(kind), plain(row.cents), ""]))
    write_lines(path, lines)
    return len(rows)

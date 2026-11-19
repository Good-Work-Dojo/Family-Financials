# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Chase checking activity CSV.

Header: Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #
Signed Amount (negative = debit), newest-first, rows end with trailing empty fields.
An export can contain a row whose quoted Description spans heavy padding and whose
Balance is a single space — handled by csv module + blank-tolerant parsing.
"""
from __future__ import annotations

import csv
from pathlib import Path

from ..dates import parse_date, squash
from ..models import FileStats, Txn
from ..money import parse_cents
from .registry import NotThisFormat, register


def parse(path: Path, account_id: str) -> tuple[list[Txn], FileStats, dict]:
    stats = FileStats(source_file=path.name, parser="chase_checking")
    txns: list[Txn] = []
    latest_balance: tuple[str, int] | None = None

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:                  # no bytes: no rows, as in every native format
            return txns, stats, {"latest_balance": None}
        if not header or header[0].strip() != "Details":
            raise NotThisFormat("its first line is not the header this format starts with")
        idx = {name.strip(): i for i, name in enumerate(header)}
        for i, row in enumerate(reader):
            stats.rows_total += 1
            if not row or all(not c.strip() for c in row):
                stats.skip("blank-line")
                continue
            if row[0].strip() == "Details":
                stats.skip("repeated-header")
                continue
            try:
                date = parse_date(row[idx["Posting Date"]])
                amount = parse_cents(row[idx["Amount"]])
            except (ValueError, IndexError):
                stats.skip("unparseable-row")
                continue
            if amount is None:
                stats.skip("no-amount")
                continue
            desc = squash(row[idx["Description"]])
            bank_type = row[idx["Type"]].strip() if len(row) > idx["Type"] else None
            txns.append(Txn(date=date, account_id=account_id, description_raw=desc,
                            amount_cents=amount, source_file=path.name,
                            bank_type=bank_type, seq=i))
            stats.observe(date, amount)
            bal = parse_cents(row[idx["Balance"]]) if len(row) > idx["Balance"] else None
            if bal is not None and (latest_balance is None or date > latest_balance[0]):
                latest_balance = (date, bal)

    return txns, stats, {"latest_balance": latest_balance}


register("chase_checking", parse)

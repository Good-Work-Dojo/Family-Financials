# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Chase credit-card activity CSV.

Header: Transaction Date,Post Date,Description,Category,Type,Amount,Memo
Oldest-first; negative = spend; Type in {Sale, Payment, Return, Adjustment};
Category is Chase's own categorization (kept as bank_category).
"""
from __future__ import annotations

import csv
from pathlib import Path

from ..dates import parse_date, squash
from ..models import FileStats, Txn
from ..money import parse_cents
from .registry import NotThisFormat, register

# the columns a row is read from, by their exact header names: without any one of them no row of the file can be read
_READS = frozenset({"Transaction Date", "Description", "Amount"})


def parse(path: Path, account_id: str) -> tuple[list[Txn], FileStats, dict]:
    stats = FileStats(source_file=path.name, parser="chase_card")
    txns: list[Txn] = []

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        # the first line is the header, judged whenever it holds anything: a file with no bytes reads as no rows
        header = reader.fieldnames
        if header and any(name.strip() for name in header) and not _READS <= set(header):
            raise NotThisFormat("its first line is not the header this format starts with")
        for i, row in enumerate(reader):
            stats.rows_total += 1
            tdate = (row.get("Transaction Date") or "").strip()
            if not tdate or tdate == "Transaction Date":
                stats.skip("blank-or-repeated-header")
                continue
            try:
                date = parse_date(tdate)
                amount = parse_cents(row["Amount"])
            except (ValueError, KeyError):
                stats.skip("unparseable-row")
                continue
            if amount is None:
                stats.skip("no-amount")
                continue
            txns.append(Txn(date=date, account_id=account_id,
                            description_raw=squash(row["Description"]),
                            amount_cents=amount, source_file=path.name,
                            bank_type=(row.get("Type") or "").strip() or None,
                            bank_category=(row.get("Category") or "").strip() or None,
                            seq=i))
            stats.observe(date, amount)

    return txns, stats, {}


register("chase_card", parse)

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Bank of America checking/savings statement-style CSV.

Layout:
  line 1: Description,,Summary Amt.
  line 2: Beginning balance as of MM/DD/YYYY,,"X,XXX.XX"
  line 3: Total credits,,"..."
  line 4: Total debits,,"-..."
  line 5: Ending balance as of MM/DD/YYYY,,"..."
  line 6: (blank)
  line 7: Date,Description,Amount,Running Bal.
  then transactions oldest-first; a pseudo-row "Beginning balance as of ..." with
  empty Amount appears first and must be skipped.
The summary block is returned for reconciliation.

The header line's first four cells are `Date,Description,Amount,Running Bal.`, each compared with its whitespace
collapsed and its case folded, the final dot optional; cells after the fourth may hold anything. A file without it is
not this format, whatever its first cell says. A row under it whose amount or date cannot be read (a date the calendar
has not got included) is skipped as unparseable-row; one whose running balance cannot be read is read without a
balance.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from ..dates import parse_date, squash
from ..models import FileStats, Txn
from ..money import parse_cents
from .registry import UNPARSEABLE_ROW, NotThisFormat, register

_BAL_RE = re.compile(r"^(Beginning|Ending) balance as of (\d{2}/\d{2}/\d{4})$")
# the header line's first four cells, each with its whitespace collapsed and its case folded, the final dot left out
HEADER = ("date", "description", "amount", "running bal")


def _is_header(row: list[str]) -> bool:
    cells = [" ".join(c.split()).casefold() for c in row[:4]]
    if len(cells) == 4:
        cells[3] = cells[3].removesuffix(".")
    return tuple(cells) == HEADER


def parse(path: Path, account_id: str) -> tuple[list[Txn], FileStats, dict]:
    stats = FileStats(source_file=path.name, parser="bofa_bank")
    txns: list[Txn] = []
    summary: dict = {}
    latest_balance: tuple[str, int] | None = None
    in_txns = False
    lines = False                    # a non-blank line was read

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            stats.rows_total += 1
            if not row or all(not c.strip() for c in row):
                stats.skip("blank-line")
                continue
            lines = True
            first = row[0].strip()
            if not in_txns:
                m = _BAL_RE.match(first)
                if m:
                    key = ("beginning_balance" if m.group(1) == "Beginning"
                           else "ending_balance")
                    summary[key] = parse_cents(row[-1])
                    summary[key + "_date"] = parse_date(m.group(2))
                    stats.skip("summary-row")
                elif first == "Total credits":
                    summary["total_credits"] = parse_cents(row[-1])
                    stats.skip("summary-row")
                elif first == "Total debits":
                    summary["total_debits"] = parse_cents(row[-1])
                    stats.skip("summary-row")
                elif _is_header(row):
                    in_txns = True
                    stats.skip("header")
                else:
                    stats.skip("preamble")
                continue
            # transaction section: Date,Description,Amount,Running Bal.
            if len(row) < 4:
                stats.skip("short-row")
                continue
            try:
                amount = parse_cents(row[2])
            except ValueError:
                stats.skip(UNPARSEABLE_ROW)
                continue
            if amount is None:
                stats.skip("pseudo-row-no-amount")  # e.g. "Beginning balance as of ..."
                continue
            try:
                date = parse_date(row[0])
            except ValueError:
                stats.skip(UNPARSEABLE_ROW)
                continue
            desc = squash(row[1])
            txns.append(Txn(date=date, account_id=account_id, description_raw=desc,
                            amount_cents=amount, source_file=path.name, seq=i))
            stats.observe(date, amount)
            try:
                bal = parse_cents(row[3])
            except ValueError:
                bal = None
            if bal is not None and (latest_balance is None or date >= latest_balance[0]):
                latest_balance = (date, bal)

    # A statement keeps its header line even for a month with no transaction: a file with lines and none of them the
    # header (another bank's export, a page saved in its place) is not this format, never a file of skipped lines
    if lines and not in_txns:
        raise NotThisFormat("none of its lines is the header line this format reads its rows under")
    # Prefer the statement's own ending balance (it has an explicit as-of date)
    if "ending_balance" in summary and summary["ending_balance"] is not None:
        latest_balance = (summary["ending_balance_date"], summary["ending_balance"])
    return txns, stats, {"summary": summary, "latest_balance": latest_balance}


register("bofa_bank", parse)

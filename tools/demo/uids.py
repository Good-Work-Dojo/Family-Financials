# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The uid the engine will give a row, so a generated review answer can address it. Each helper builds a `Txn` and reads
its own `uid` (the one hash), so they cannot drift from it; what they reproduce is only how the parsers (and, for a
part of a split purchase, the split) fill the fields the hash reads."""
from __future__ import annotations

from etl.models import Txn
from etl.parsers.simplefin import _seq_from_id

from .ledger import Row


def _squash(text: str) -> str:
    return " ".join(text.split())          # the parsers' whitespace squash


def _uid(account: str, row: Row, description: str, seq: int) -> str:
    return Txn(date=row.date.isoformat(), account_id=account, description_raw=description,
               amount_cents=row.cents, source_file="", seq=seq).uid


def file_uids(account: str, rows: list[Row]) -> list[str]:
    """The uids of the rows of ONE statement file, given in the order the file lists them: a row's seq is its
    0-based occurrence among the rows with the same date, amount and description in that file. A pending row is
    refused: no statement file lists one."""
    seen: dict[tuple, int] = {}
    out = []
    for row in rows:
        if row.pending:
            raise ValueError(f"{account}: a statement file lists no pending row ({row.date} {row.desc!r})")
        desc = _squash(row.desc)
        key = (row.date, row.cents, desc)
        out.append(_uid(account, row, desc, seen.get(key, 0)))
        seen[key] = seen.get(key, 0) + 1
    return out


def feed_uid(account: str, row: Row) -> str:
    """The uid of a feed row: its seq is a hash of the feed's transaction id (`row.feed_id`), and its description is
    what the feed parser stores, `payee — description` (an em dash) unless the payee is already in the description."""
    if not row.feed_id:
        raise ValueError(f"{account}: a feed row needs a feed_id ({row.date} {row.desc!r})")
    desc = _squash(row.desc)
    payee = _squash(row.payee or "")
    if payee and payee.lower() not in desc.lower():
        desc = f"{payee} — {desc}" if desc else payee
    return _uid(account, row, desc or "(no description)", _seq_from_id(row.feed_id))


def part_uid(parent_uid: str, index: int) -> str:
    """The uid of part `index` (1-based, the leg's position in the split rule) of the split purchase stored under
    `parent_uid`: a part's uid is made from its parent's and its position alone."""
    if isinstance(index, bool) or not isinstance(index, int) or index < 1:
        raise ValueError(f"a part's index is its leg's 1-based position, got {index!r}")
    return Txn(date="", account_id="", description_raw="", amount_cents=0, source_file="", parent_id=parent_uid,
               split_index=index).uid

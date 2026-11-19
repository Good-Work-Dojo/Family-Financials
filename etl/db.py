# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""SQLite schema and persistence. The DB is rebuilt from scratch on every ingest
(written to finance.db.tmp then atomically renamed)."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from .models import (
    AssetRow,
    BalanceObs,
    FileStats,
    Holding,
    OpenQuestion,
    QualityCheck,
    Txn,
)

DDL = """
CREATE TABLE accounts (
    account_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    institution TEXT NOT NULL,
    owner TEXT NOT NULL,
    kind TEXT NOT NULL,
    txn_data_start TEXT,
    txn_data_end TEXT,
    txn_count INTEGER DEFAULT 0,
    latest_balance_cents INTEGER,
    latest_balance_as_of TEXT
);

CREATE TABLE transactions (
    txn_uid TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    account_id TEXT NOT NULL REFERENCES accounts(account_id),
    owner TEXT NOT NULL,
    description_raw TEXT NOT NULL,
    vendor TEXT,
    category TEXT,
    subcategory TEXT,
    amount_cents INTEGER NOT NULL,
    type TEXT NOT NULL CHECK (type IN
        -- literal SQL on purpose; keep in sync with models.TXN_TYPES
        ('purchase','income','transfer','cc-payment','investment','dividend','fee','interest')),
    is_internal INTEGER NOT NULL DEFAULT 0,
    transfer_group_id TEXT,
    match_status TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    needs_review INTEGER NOT NULL DEFAULT 0,
    earner TEXT,
    bank_type TEXT,
    bank_category TEXT,
    source_file TEXT NOT NULL,
    -- splits: a child names its parent's uid; a parent is flagged and in no sum (etl/enrich/splits.py)
    parent_id TEXT REFERENCES transactions(txn_uid),
    is_parent INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_txn_date ON transactions(date);
CREATE INDEX idx_txn_account ON transactions(account_id);
CREATE INDEX idx_txn_type ON transactions(type);

CREATE TABLE assets (
    snapshot_date TEXT NOT NULL,
    account TEXT NOT NULL,
    account_id TEXT,                 -- registry key when SimpleFIN-sourced (join key)
    institution TEXT NOT NULL,
    owner TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    value_cents INTEGER NOT NULL,
    liquid INTEGER NOT NULL,
    source TEXT NOT NULL,
    as_of TEXT,
    as_of_assumed INTEGER NOT NULL DEFAULT 0,
    needs_review INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_assets_date ON assets(snapshot_date);

CREATE TABLE holdings (
    account TEXT NOT NULL,
    account_id TEXT,                 -- registry key when SimpleFIN-sourced (join key)
    institution TEXT NOT NULL,
    owner TEXT NOT NULL,
    symbol TEXT,
    name TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    quantity REAL,
    value_cents INTEGER NOT NULL,
    cost_basis_cents INTEGER,
    as_of TEXT NOT NULL,
    liquid INTEGER NOT NULL DEFAULT 1,
    notes TEXT
);
CREATE INDEX idx_holdings_account_id ON holdings(account_id);

CREATE TABLE account_balances (
    account_id TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    balance_cents INTEGER NOT NULL,
    available_cents INTEGER,
    limit_cents INTEGER,
    source TEXT NOT NULL,
    PRIMARY KEY (account_id, as_of_date, source)
);
CREATE INDEX idx_balances_acct ON account_balances(account_id);

CREATE TABLE transfer_matches (
    transfer_group_id TEXT NOT NULL,
    out_uid TEXT NOT NULL REFERENCES transactions(txn_uid),
    in_uid TEXT NOT NULL REFERENCES transactions(txn_uid),
    match_method TEXT NOT NULL,
    bday_distance INTEGER NOT NULL
);

CREATE TABLE recurring (
    vendor TEXT NOT NULL,
    account_id TEXT NOT NULL,
    category TEXT,
    cadence TEXT NOT NULL,
    typical_amount_cents INTEGER NOT NULL,
    last_amount_cents INTEGER NOT NULL,
    amount_mode TEXT NOT NULL,
    occurrences INTEGER NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    next_expected TEXT,
    active INTEGER NOT NULL,
    annualized_cents INTEGER NOT NULL,
    price_changes TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE file_stats (
    source_file TEXT NOT NULL,
    parser TEXT NOT NULL,
    rows_total INTEGER, rows_parsed INTEGER, rows_skipped INTEGER,
    skip_reasons TEXT, duplicates_removed INTEGER,
    date_min TEXT, date_max TEXT, sum_cents INTEGER,
    account TEXT
);

CREATE TABLE data_quality (
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pass','warn','fail','info')),
    detail TEXT NOT NULL
);

CREATE TABLE open_questions (
    topic TEXT NOT NULL,
    question TEXT NOT NULL,
    context TEXT
);
"""


def build(db_path: Path) -> sqlite3.Connection:
    """The new database, at finance.db.tmp: made empty and 0600 first (the household's ledger is its owner's alone;
    SQLite gives its journal the same mode), never opened through a link left at that name."""
    tmp = db_path.with_suffix(".db.tmp")
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
    os.chmod(tmp, 0o600)
    conn = sqlite3.connect(tmp)
    conn.executescript(DDL)
    return conn


def finalize(conn: sqlite3.Connection, db_path: Path) -> None:
    conn.commit()
    conn.close()
    os.replace(db_path.with_suffix(".db.tmp"), db_path)


def insert_txns(conn: sqlite3.Connection, txns: list[Txn], owner_of: dict[str, str]) -> None:
    conn.executemany(
        """INSERT INTO transactions (txn_uid, date, account_id, owner, description_raw,
           vendor, category, subcategory, amount_cents, type, is_internal, transfer_group_id,
           match_status, tags, needs_review, earner, bank_type, bank_category, source_file,
           parent_id, is_parent)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(t.uid, t.date, t.account_id, owner_of[t.account_id], t.description_raw,
          t.vendor, t.category, t.subcategory, t.amount_cents, t.type, int(t.is_internal),
          t.transfer_group_id, t.match_status, json.dumps(t.tags), int(t.needs_review),
          t.earner, t.bank_type, t.bank_category, t.source_file,
          t.parent_id, int(t.is_parent)) for t in txns])


def insert_assets(conn: sqlite3.Connection, rows: list[AssetRow]) -> None:
    conn.executemany(
        """INSERT INTO assets (snapshot_date, account, account_id, institution, owner,
           asset_class, value_cents, liquid, source, as_of, as_of_assumed, needs_review)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(a.snapshot_date, a.account, a.account_id, a.institution, a.owner, a.asset_class,
          a.value_cents, int(a.liquid), a.source, a.as_of, int(a.as_of_assumed),
          int(a.needs_review)) for a in rows])


def insert_holdings(conn: sqlite3.Connection, rows: list[Holding]) -> None:
    conn.executemany(
        """INSERT INTO holdings (account, account_id, institution, owner, symbol, name,
           asset_class, quantity, value_cents, cost_basis_cents, as_of, liquid, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(h.account, h.account_id, h.institution, h.owner, h.symbol, h.name, h.asset_class,
          h.quantity, h.value_cents, h.cost_basis_cents, h.as_of, int(h.liquid), h.notes)
         for h in rows])


def insert_balances(conn: sqlite3.Connection, rows: list[BalanceObs]) -> None:
    conn.executemany(
        """INSERT OR REPLACE INTO account_balances
           (account_id, as_of_date, balance_cents, available_cents, limit_cents, source)
           VALUES (?,?,?,?,?,?)""",
        [(b.account_id, b.as_of, b.balance_cents, b.available_cents, b.limit_cents,
          b.source) for b in rows])


def insert_file_stats(conn: sqlite3.Connection, stats: list[FileStats]) -> None:
    conn.executemany(
        """INSERT INTO file_stats VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [(s.source_file, s.parser, s.rows_total, s.rows_parsed, s.rows_skipped,
          json.dumps(s.skip_reasons), s.duplicates_removed, s.date_min, s.date_max,
          s.sum_cents, s.account) for s in stats])


def insert_quality(conn: sqlite3.Connection, checks: list[QualityCheck]) -> None:
    conn.executemany("INSERT INTO data_quality VALUES (?,?,?)",
                     [(c.name, c.status, c.detail) for c in checks])


def insert_questions(conn: sqlite3.Connection, qs: list[OpenQuestion]) -> None:
    conn.executemany("INSERT INTO open_questions VALUES (?,?,?)",
                     [(q.topic, q.question, q.context) for q in qs])

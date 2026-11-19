# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The account model: the closed set of account kinds, the class each account's rows take (Config.asset_class_of),
the load checks on both, and the sign a loan's balance is stored with; also the database's check of a row's type
against the closed set of types. Synthetic accounts and feed snapshots only."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
import yaml
from conftest import write_household

from etl import pipeline as pipeline_mod
from etl.config import Config
from etl.models import (
    ACCOUNT_KINDS,
    ASSET_CLASSES,
    CASH_KINDS,
    CASHFLOW_KINDS,
    DEFAULT_CLASS_BY_KIND,
    ILLIQUID_CLASSES,
    LIABILITY_CLASSES,
    LIABILITY_KINDS,
    FileStats,
)
from etl.parsers import simplefin as sfp
from etl.pipeline import Pipeline

TZ = "UTC"
_KINDS = "checking, savings, credit-card, brokerage, loan"
_CLASSES = ", ".join(ASSET_CLASSES)
# the class an account of each kind takes when it declares none
_DEFAULT_CLASS = {"checking": "Cash", "savings": "Cash", "credit-card": "Other Debt", "brokerage": "Stocks/ETF",
                  "loan": "Other Debt"}


def _account(kind: str, **extra) -> dict:
    return {"institution": "Sample Bank", "owner": "Alex", "kind": kind, **extra}


def _named(accounts: dict) -> dict:
    """The accounts, each named after its id."""
    return {a: {"name": f"Sample {a}", **m} for a, m in accounts.items()}


def _config(tmp_path: Path, accounts: dict, *, manual: list | None = None) -> Config:
    d = tmp_path / "cfg"
    d.mkdir(parents=True, exist_ok=True)
    doc: dict = {"source_dir": "src", "accounts": _named(accounts)}
    if manual is not None:
        doc["manual_entries"] = manual
    (d / "accounts.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    write_household(d)
    (tmp_path / "src").mkdir(exist_ok=True)
    return Config(d, source_dir=str(tmp_path / "src"))


def _refused(tmp_path: Path, accounts: dict, *, manual: list | None = None) -> str:
    with pytest.raises(ValueError) as e:
        _config(tmp_path, accounts, manual=manual)
    return str(e.value)


# ------------------------------------------------------------------------------------------- the constants

def test_the_constants_name_only_known_kinds_and_classes():
    assert ACCOUNT_KINDS == ("checking", "savings", "credit-card", "brokerage", "loan")
    assert set(DEFAULT_CLASS_BY_KIND) == set(ACCOUNT_KINDS) == set(_DEFAULT_CLASS)
    assert dict(DEFAULT_CLASS_BY_KIND) == _DEFAULT_CLASS
    for kinds in (CASH_KINDS, LIABILITY_KINDS, CASHFLOW_KINDS):
        assert kinds <= set(ACCOUNT_KINDS)
    for classes in (set(DEFAULT_CLASS_BY_KIND.values()), LIABILITY_CLASSES, ILLIQUID_CLASSES, {"Mortgage"}):
        assert classes <= set(ASSET_CLASSES)
    assert CASH_KINDS == {"checking", "savings"} and LIABILITY_KINDS == {"credit-card", "loan"}
    assert CASHFLOW_KINDS == CASH_KINDS | {"credit-card"}
    assert LIABILITY_CLASSES == {"Mortgage", "Margin", "Other Debt"}
    assert ILLIQUID_CLASSES == {"Retirement", "529"}
    # a liability kind defaults to a liability class and a cash kind to the cash class
    assert {DEFAULT_CLASS_BY_KIND[k] for k in LIABILITY_KINDS} <= LIABILITY_CLASSES
    assert {DEFAULT_CLASS_BY_KIND[k] for k in CASH_KINDS} == {"Cash"}


def test_db_type_check_matches_models():
    """The transactions.type CHECK is literal SQL on purpose — this pins it to
    models.TXN_TYPES so the two can't drift."""
    import re

    from etl.db import DDL
    from etl.models import TXN_TYPES

    m = re.search(r"type TEXT NOT NULL CHECK \(type IN\s*(?:--[^\n]*\n\s*)?\(([^)]*)\)", DDL)
    assert m, "transactions.type CHECK not found in DDL"
    sql_types = tuple(t.strip().strip("'") for t in m.group(1).split(","))
    assert sql_types == TXN_TYPES


# ------------------------------------------------------------------------------------------- the accessor

@pytest.mark.parametrize("kind", ACCOUNT_KINDS)
def test_an_account_that_declares_no_class_takes_its_kinds_default(tmp_path, kind):
    cfg = _config(tmp_path, {"demo_account_0000": _account(kind)})
    assert cfg.asset_class_of("demo_account_0000") == _DEFAULT_CLASS[kind]


def test_a_declared_asset_class_wins_over_the_kinds_default(tmp_path):
    cfg = _config(tmp_path, {
        "demo_brokerage_0000": _account("brokerage", asset_class="Retirement"),
        "demo_brokerage_0001": _account("brokerage", asset_class="529"),
        "demo_brokerage_0002": _account("brokerage", asset_class="Cash"),
        "demo_checking_0000": _account("checking", asset_class="Cash"),
    })
    assert [cfg.asset_class_of(a) for a in cfg.accounts] == ["Retirement", "529", "Cash", "Cash"]


def test_the_mortgage_loan_is_a_mortgage_and_other_loans_and_cards_are_other_debt(tmp_path):
    cfg = _config(tmp_path, {
        "demo_loan_0000": _account("loan", mortgage=True),
        "demo_loan_0001": _account("loan"),
        "demo_card_0000": _account("credit-card"),
    })
    assert [cfg.asset_class_of(a) for a in cfg.accounts] == ["Mortgage", "Other Debt", "Other Debt"]


def test_an_unknown_account_id_is_a_key_error(tmp_path):
    cfg = _config(tmp_path, {"demo_checking_0000": _account("checking")})
    with pytest.raises(KeyError):
        cfg.asset_class_of("demo_checking_9999")


# ------------------------------------------------------------------------------------------- the load checks

def test_an_unknown_or_missing_kind_is_refused(tmp_path):
    assert _refused(tmp_path / "a", {"demo_account_0000": _account("piggy-bank")}) == (
        f"accounts.yaml: accounts.demo_account_0000.kind: unknown kind 'piggy-bank' — one of {_KINDS}")
    kindless = {k: v for k, v in _account("checking").items() if k != "kind"}
    assert _refused(tmp_path / "b", {"demo_account_0000": kindless}) == (
        f"accounts.yaml: accounts.demo_account_0000.kind: missing — one of {_KINDS}")


def test_an_unknown_class_on_an_account_is_refused(tmp_path):
    assert _refused(tmp_path, {"demo_brokerage_0000": _account("brokerage", asset_class="Bonds")}) == (
        f"accounts.yaml: accounts.demo_brokerage_0000.asset_class: unknown class 'Bonds' — one of {_CLASSES}")


def test_an_unknown_class_on_a_manual_entry_is_refused(tmp_path):
    manual = [{"account": "Sample House", "institution": "Sample", "owner": "Alex", "asset_class": "House",
               "value": 1000, "as_of": "2030-01-01"},
              {"account": "Sample Boat", "institution": "Sample", "owner": "Sam", "asset_class": "Boat",
               "value": 500, "as_of": "2030-01-01"}]
    assert _refused(tmp_path, {"demo_checking_0000": _account("checking")}, manual=manual) == (
        f"accounts.yaml: manual_entries[1].asset_class: unknown class 'Boat' — one of {_CLASSES}")


# ------------------------------------------------------------------------------------------- the feed

def _epoch(day: str) -> int:
    return int(dt.datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp())


def _feed_account(sfid: str, balance: str, *, holdings: list | None = None) -> dict:
    return {"id": sfid, "name": sfid, "org": {"name": "Sample Bank"}, "currency": "USD", "balance": balance,
            "balance-date": _epoch("2030-01-10"), "transactions": [], "holdings": holdings or []}


def _write_snapshot(d: Path, accounts: list[dict]) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    path = d / "accounts-20300110T180000Z.json"
    path.write_text(json.dumps({"accounts": accounts}))
    return path


_HOLDING = {"symbol": "SMPL", "description": "Sample fund", "shares": "2", "cost_basis": "150.00",
            "market_value": "200.00"}


def test_a_loans_balance_is_stored_negative_and_a_cards_as_reported(tmp_path):
    meta = _named({"demo_loan_0000": _account("loan"), "demo_loan_0001": _account("loan"),
                   "demo_card_0000": _account("credit-card"), "demo_card_0001": _account("credit-card")})
    snap = _write_snapshot(tmp_path, [
        _feed_account("SF-DEMO-L0", "1234.56"),       # a feed that reports what is owed as a positive number
        _feed_account("SF-DEMO-L1", "-1234.56"),
        _feed_account("SF-DEMO-C0", "25.00"),         # a card in credit
        _feed_account("SF-DEMO-C1", "-80.00")])
    sfids = {"SF-DEMO-L0": "demo_loan_0000", "SF-DEMO-L1": "demo_loan_0001",
             "SF-DEMO-C0": "demo_card_0000", "SF-DEMO-C1": "demo_card_0001"}
    balances = sfp.parse_snapshot(snap, sfids, {}, meta, tz=TZ)[1]
    assert {b.account_id: b.balance_cents for b in balances} == {
        "demo_loan_0000": -123_456, "demo_loan_0001": -123_456, "demo_card_0000": 2_500, "demo_card_0001": -8_000}


@pytest.mark.parametrize("reported", ["0.00", "-0.00", "0"])
def test_a_loan_whose_feed_balance_is_zero_stays_zero(tmp_path, reported):
    meta = _named({"demo_loan_0000": _account("loan")})
    snap = _write_snapshot(tmp_path, [_feed_account("SF-DEMO-L0", reported)])
    (balance,) = sfp.parse_snapshot(snap, {"SF-DEMO-L0": "demo_loan_0000"}, {}, meta, tz=TZ)[1]
    assert (balance.balance_cents, balance.as_of) == (0, "2030-01-10")


def test_every_reader_downstream_of_the_feed_sees_the_loans_stored_sign(tmp_path):
    cfg = _config(tmp_path, {
        "demo_checking_0000": _account("checking", simplefin_id="SF-DEMO-CHK"),
        "demo_loan_0000": _account("loan", mortgage=True, simplefin_id="SF-DEMO-MORT"),
    })
    cfg.simplefin_snapshots_dir = tmp_path / "snaps"
    _write_snapshot(cfg.simplefin_snapshots_dir, [_feed_account("SF-DEMO-CHK", "500.00"),
                                                  _feed_account("SF-DEMO-MORT", "200000.00")])
    p = Pipeline(cfg)
    p.parse_simplefin()
    assert p.simplefin_latest_bal["demo_loan_0000"] == ("2030-01-10", -20_000_000)
    assert [b.balance_cents for b in p.balance_obs if b.account_id == "demo_loan_0000"] == [-20_000_000]
    assert p._latest_balance_all()["demo_loan_0000"] == ("2030-01-10", -20_000_000)
    p.parse_assets()
    rows = {r.account_id: (r.asset_class, r.value_cents, r.liquid) for r in p.asset_rows}
    assert rows == {"demo_checking_0000": ("Cash", 50_000, True),
                    "demo_loan_0000": ("Mortgage", -20_000_000, False)}
    assert p.mortgage_included["2030-01-10"] is True


def test_holdings_take_the_accounts_declared_class(tmp_path):
    meta = _named({"demo_brokerage_0000": _account("brokerage", asset_class="Retirement")})
    snap = _write_snapshot(tmp_path, [_feed_account("SF-DEMO-B0", "200.00", holdings=[_HOLDING])])
    holdings = sfp.parse_snapshot(snap, {"SF-DEMO-B0": "demo_brokerage_0000"}, {}, meta, tz=TZ)[2]
    assert [h.asset_class for h in holdings] == ["Retirement"]


def test_the_current_snapshot_classes_each_brokerage_by_the_one_accessor(tmp_path):
    """A brokerage with holdings and one without class their rows the same way: the declared class, else the
    brokerage default, which is liquid."""
    cfg = _config(tmp_path, {
        "demo_brokerage_0000": _account("brokerage", asset_class="529", simplefin_id="SF-DEMO-B0"),
        "demo_brokerage_0001": _account("brokerage", simplefin_id="SF-DEMO-B1"),
        "demo_brokerage_0002": _account("brokerage", asset_class="Retirement", simplefin_id="SF-DEMO-B2"),
    })
    cfg.simplefin_snapshots_dir = tmp_path / "snaps"
    _write_snapshot(cfg.simplefin_snapshots_dir, [
        _feed_account("SF-DEMO-B0", "700.00"),                        # balance only
        _feed_account("SF-DEMO-B1", "300.00"),                        # balance only
        _feed_account("SF-DEMO-B2", "200.00", holdings=[_HOLDING])])
    p = Pipeline(cfg)
    p.parse_simplefin()
    p.parse_assets()
    rows = {r.account_id: (r.asset_class, r.liquid, r.value_cents, r.source) for r in p.asset_rows}
    assert rows == {"demo_brokerage_0000": ("529", False, 70_000, "simplefin_balance"),
                    "demo_brokerage_0001": ("Stocks/ETF", True, 30_000, "simplefin_balance"),
                    "demo_brokerage_0002": ("Retirement", False, 20_000, "simplefin_balance")}


def test_only_a_cash_account_books_a_cash_row_from_its_file_balance(tmp_path, monkeypatch):
    """The statement-file parsers hand back each account's newest balance, and net worth reads it like any other
    observation, in the account's own class: only a checking or savings account's is booked at all, a cash row. A
    brokerage's or a card's file balance is not counted (an info check says so), so with no feed neither has a row.
    (The household counts cards here, the default.)"""
    def parse(path, account_id):
        return [], FileStats(source_file=path.name, parser="sample_file"), {"latest_balance": ("2030-01-10", 40_000)}
    monkeypatch.setitem(pipeline_mod.TXN_PARSERS, "sample_file", parse)
    accounts = {a: _account(kind, parser="sample_file", glob=f"{a}-*.csv")
                for a, kind in (("demo_checking_0000", "checking"), ("demo_brokerage_0000", "brokerage"),
                                ("demo_card_0000", "credit-card"))}
    cfg = _config(tmp_path, accounts)
    cfg.simplefin_snapshots_dir = tmp_path / "snaps"          # no feed: the file balances are all there is
    for a in accounts:
        (tmp_path / "src" / f"{a}-2030.csv").write_text("")
    p = Pipeline(cfg)
    p.parse_transactions()
    p.parse_simplefin()
    p.parse_assets()
    assert [(r.account_id, r.asset_class, r.source) for r in p.asset_rows] == [
        ("demo_checking_0000", "Cash", "bank_balance")]
    assert {b.account_id for b in p.balance_obs} == {"demo_checking_0000"}
    assert [(c.name, c.status) for c in p.checks if c.name.startswith("import.balance_ignored.")] == [
        ("import.balance_ignored.demo_brokerage_0000", "info"), ("import.balance_ignored.demo_card_0000", "info")]

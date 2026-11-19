# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The mortgage liability is the newest feed balance of the `mortgage: true` account; its loan terms are keys of that
account (accounts.yaml `loan:`), and assumptions.json `mortgage` exports both with the account's balance at every
snapshot date as its schedule."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
import yaml
from conftest import isolated_pipeline, write_household

from etl import exports
from etl.config import Config
from etl.models import BalanceObs
from etl.pipeline import THRESHOLD_KEYS

_CHECKING = {"name": "Sample Checking", "institution": "Sample Bank",
             "owner": "Alex", "kind": "checking", "simplefin_id": "ACT-demo-checking"}
_MORTGAGE = {"name": "Sample Mortgage", "institution": "Sample Lender",
             "owner": "Alex", "kind": "loan", "mortgage": True, "simplefin_id": "ACT-demo-mortgage"}
_LOAN = {"rate": 6.5, "monthly_payment": 2000, "monthly_escrow": 450.25, "escrow_balance": 2100.0,
         "origination": {"date": dt.date(2030, 1, 15), "amount": 250_000}, "term_months": 360}
_KEYS = ["account_id", "balance", "balance_as_of", "rate_pct", "monthly_payment", "monthly_escrow", "escrow_balance",
         "origination", "term_months", "schedule"]
_WHERE = "accounts.yaml: accounts.demo_mortgage_0000.loan"


def _config_dir(tmp_path: Path, *, mortgage: bool = True, manual: list | None = None, loan=None,
                assumptions: dict | None = None, checking: dict | None = None) -> Path:
    accounts = {"demo_checking_0000": {**_CHECKING, **(checking or {})}}
    if mortgage:
        accounts["demo_mortgage_0000"] = {**_MORTGAGE, **({"loan": loan} if loan is not None else {})}
    d = tmp_path / "cfg"
    d.mkdir(parents=True)
    (d / "accounts.yaml").write_text(yaml.safe_dump({"source_dir": "src", "accounts": accounts,
                                                     **({"manual_entries": manual} if manual else {})}))
    if assumptions is not None:
        (d / "assumptions.yaml").write_text(yaml.safe_dump(assumptions))
    write_household(d)
    (tmp_path / "src").mkdir(exist_ok=True)
    return d


def _config(tmp_path: Path, **kw) -> Config:
    return Config(_config_dir(tmp_path, **kw), source_dir=str(tmp_path / "src"))


def _pipeline(tmp_path: Path, *, mortgage: bool = True, feed: tuple[str, int] | None = None,
              manual: list | None = None, loan=None):
    p = isolated_pipeline(tmp_path, [], cfg=_config(tmp_path, mortgage=mortgage, manual=manual, loan=loan),
                          windows={})
    if feed:
        p.balance_obs = [BalanceObs("demo_mortgage_0000", *feed)]
    return p


def _mortgage_rows(p):
    p.parse_assets()
    return [r for r in p.asset_rows if r.asset_class == "Mortgage"]


def _block(p, tmp_path: Path):
    p._export_assumptions(tmp_path)
    return json.loads((tmp_path / "assumptions.json").read_text())["mortgage"]


def test_the_mortgage_row_is_the_newest_feed_balance(tmp_path):
    p = _pipeline(tmp_path, feed=("2030-01-31", -12_345_600))
    (row,) = _mortgage_rows(p)
    assert (row.value_cents, row.as_of, row.source, row.account_id) == (
        -12_345_600, "2030-01-31", "simplefin_balance", "demo_mortgage_0000")
    assert (row.account, row.institution, row.owner, row.liquid) == (
        "Sample Mortgage", "Sample Lender", "Alex", False)
    assert "simplefin.mortgage_freshness" not in [c.name for c in p.checks]


def test_without_a_feed_balance_there_is_no_mortgage_row(tmp_path):
    assert _mortgage_rows(_pipeline(tmp_path)) == []


def _snapshot(p):
    p.balance_obs.append(BalanceObs("demo_checking_0000", "2030-01-31", 500_000, source="csv"))
    p.parse_assets()
    return p.mortgage_included["2030-01-31"]


def test_mortgage_included_follows_the_configured_account(tmp_path):
    assert _snapshot(_pipeline(tmp_path / "a", mortgage=False)) is True
    assert _snapshot(_pipeline(tmp_path / "b")) is False
    assert _snapshot(_pipeline(tmp_path / "c", feed=("2030-01-31", -12_345_600))) is True


def test_a_hand_stated_mortgage_row_neither_includes_the_mortgage_nor_fills_its_balance(tmp_path):
    """With the `mortgage: true` account configured and never observed, a manual entry of the Mortgage class is
    counted as a row of its own, but the snapshot still says the account's mortgage is not included and the
    block's balance stays unknown, its schedule empty."""
    stated = {"account": "Sample Stated Loan", "institution": "Sample Lender", "owner": "Alex",
              "asset_class": "Mortgage", "value": -1_000.0, "as_of": "2030-01-31"}
    p = _pipeline(tmp_path, manual=[stated])
    assert _snapshot(p) is False
    assert [(r.account, r.account_id, r.source) for r in p.asset_rows if r.asset_class == "Mortgage"] == [
        ("Sample Stated Loan", None, "stated")]
    mg = _block(p, tmp_path)
    assert (mg["balance"], mg["balance_as_of"], mg["schedule"]) == (None, None, [])


def _exported_snapshot(p) -> dict:
    return exports.export_assets_timeseries(p.asset_rows, p.mortgage_included, p.snapshot_coverage)["snapshots"][-1]


def test_the_exported_snapshot_subtracts_the_mortgage_and_says_whether_it_is_in(tmp_path):
    p = _pipeline(tmp_path / "a", feed=("2030-01-31", -12_345_600))
    _snapshot(p)
    snap = _exported_snapshot(p)
    assert (snap["total"], snap["by_class"]["Mortgage"], snap["mortgage_included"]) == (5_000.0 - 123_456.0,
                                                                                     -123_456.0, True)
    p = _pipeline(tmp_path / "b")
    _snapshot(p)
    snap = _exported_snapshot(p)
    assert (snap["total"], "Mortgage" in snap["by_class"], snap["mortgage_included"]) == (5_000.0, False, False)


def test_a_feed_balance_of_zero_books_a_zero_row_and_exports_a_zero_balance(tmp_path):
    p = _pipeline(tmp_path, feed=("2030-01-31", 0))
    assert _snapshot(p) is True
    (row,) = [r for r in p.asset_rows if r.asset_class == "Mortgage"]
    assert (row.value_cents, row.as_of) == (0, "2030-01-31")
    mg = _block(p, tmp_path)
    assert (mg["balance"], mg["balance_as_of"]) == (0.0, "2030-01-31")
    assert mg["schedule"] == [{"date": "2030-01-31", "principal_balance": 0.0}]


def test_a_mortgage_account_never_observed_has_a_null_balance_and_an_empty_schedule(tmp_path):
    p = _pipeline(tmp_path, loan={"rate": 6.5})
    p.balance_obs = [BalanceObs("demo_checking_0000", "2030-01-31", 500_000)]   # the feed reported another account
    assert _snapshot(p) is False
    assert [r for r in p.asset_rows if r.asset_class == "Mortgage"] == []
    mg = _block(p, tmp_path)
    assert list(mg) == _KEYS
    assert (mg["account_id"], mg["balance"], mg["balance_as_of"], mg["rate_pct"], mg["schedule"]) == (
        "demo_mortgage_0000", None, None, 6.5, [])


def test_the_block_for_an_account_with_every_loan_term(tmp_path):
    p = _pipeline(tmp_path, feed=("2030-01-31", -12_345_600), loan=_LOAN)
    _snapshot(p)
    assert _block(p, tmp_path) == {
        "account_id": "demo_mortgage_0000", "balance": 123_456.0, "balance_as_of": "2030-01-31",
        "rate_pct": 6.5, "monthly_payment": 2000, "monthly_escrow": 450.25, "escrow_balance": 2100.0,
        "origination": {"date": "2030-01-15", "amount": 250_000}, "term_months": 360,
        "schedule": [{"date": "2030-01-31", "principal_balance": 123_456.0}]}


def test_the_block_for_an_account_with_no_loan_terms_has_every_term_null_and_the_feeds_balance(tmp_path):
    p = _pipeline(tmp_path, feed=("2030-01-31", -12_345_600))
    _snapshot(p)
    mg = _block(p, tmp_path)
    assert list(mg) == _KEYS
    assert (mg["account_id"], mg["balance"], mg["balance_as_of"]) == ("demo_mortgage_0000", 123_456.0, "2030-01-31")
    assert [mg[k] for k in ("rate_pct", "monthly_payment", "monthly_escrow", "escrow_balance", "origination",
                            "term_months")] == [None] * 6


def test_without_a_mortgage_account_the_block_is_null(tmp_path):
    p = _pipeline(tmp_path, mortgage=False)
    _snapshot(p)
    assert _block(p, tmp_path) is None


def test_the_schedule_is_the_accounts_balance_at_every_snapshot_date(tmp_path):
    """Four months of loan balances: one row per snapshot date at which the account has a point on or before it
    (the month end before its first point has none), each the positive dollars owed then (carried to the date),
    ascending, the last the block's balance."""
    p = _pipeline(tmp_path)
    p.balance_obs = [BalanceObs("demo_checking_0000", "2029-12-20", 100_000),
                     BalanceObs("demo_mortgage_0000", "2030-01-15", -200_000_00),
                     BalanceObs("demo_mortgage_0000", "2030-02-14", -199_500_00),
                     BalanceObs("demo_mortgage_0000", "2030-03-16", -199_000_00),
                     BalanceObs("demo_mortgage_0000", "2030-04-10", -198_500_00),
                     BalanceObs("demo_checking_0000", "2030-04-20", 120_000)]
    p.parse_assets()
    mg = _block(p, tmp_path)
    assert mg["schedule"] == [{"date": "2030-01-31", "principal_balance": 200_000.0},
                              {"date": "2030-02-28", "principal_balance": 199_500.0},
                              {"date": "2030-03-31", "principal_balance": 199_000.0},
                              {"date": "2030-04-20", "principal_balance": 198_500.0}]
    assert (mg["balance"], mg["balance_as_of"]) == (198_500.0, "2030-04-10")
    assert mg["schedule"][-1]["principal_balance"] == mg["balance"]


# ---------------------------------------------------------------- loan terms on the account

def test_loan_terms_reads_every_key_and_a_yaml_date_as_iso_text(tmp_path):
    cfg = _config(tmp_path, loan=_LOAN)
    assert cfg.loan_terms("demo_mortgage_0000") == {
        "rate": 6.5, "monthly_payment": 2000, "monthly_escrow": 450.25, "escrow_balance": 2100.0,
        "origination": {"date": "2030-01-15", "amount": 250_000}, "term_months": 360}


def test_loan_terms_are_none_where_absent(tmp_path):
    cfg = _config(tmp_path / "a", loan={"rate": 6.5, "origination": {"date": "2030-01-15"}})
    assert cfg.loan_terms("demo_mortgage_0000") == {
        "rate": 6.5, "monthly_payment": None, "monthly_escrow": None, "escrow_balance": None,
        "origination": {"date": "2030-01-15", "amount": None}, "term_months": None}
    cfg = _config(tmp_path / "b")
    assert cfg.loan_terms("demo_mortgage_0000") == dict.fromkeys(
        ("rate", "monthly_payment", "monthly_escrow", "escrow_balance", "origination", "term_months"))
    assert cfg.loan_terms("demo_checking_0000")["rate"] is None


def test_a_null_loan_block_or_key_reads_as_absent(tmp_path):
    d = _config_dir(tmp_path)
    doc = yaml.safe_load((d / "accounts.yaml").read_text())
    doc["accounts"]["demo_mortgage_0000"]["loan"] = None
    doc["accounts"]["demo_checking_0000"]["loan"] = None
    (d / "accounts.yaml").write_text(yaml.safe_dump(doc))
    assert Config(d, source_dir=str(tmp_path / "src")).loan_terms("demo_mortgage_0000")["rate"] is None
    cfg = _config(tmp_path / "b", loan={"rate": None, "origination": None})
    assert cfg.loan_terms("demo_mortgage_0000")["rate"] is None
    assert cfg.loan_terms("demo_mortgage_0000")["origination"] is None


@pytest.mark.parametrize("loan, message", [
    ([6.5], f"{_WHERE}: expected a mapping"),
    ("6.5", f"{_WHERE}: expected a mapping"),
    ({"rates": 6.5}, f"{_WHERE}.rates: unknown key — one of rate, monthly_payment, monthly_escrow, escrow_balance, "
                     "origination, term_months"),
    ({"rate": 100}, f"{_WHERE}.rate: expected a percent"),
    ({"rate": -0.5}, f"{_WHERE}.rate: expected a percent"),
    ({"rate": "6.5"}, f"{_WHERE}.rate: expected a percent"),
    ({"rate": True}, f"{_WHERE}.rate: expected a percent"),
    ({"monthly_payment": -1}, f"{_WHERE}.monthly_payment: expected an amount of 0 or more"),
    ({"monthly_payment": "2000"}, f"{_WHERE}.monthly_payment: expected an amount of 0 or more"),
    ({"monthly_payment": float("inf")}, f"{_WHERE}.monthly_payment: expected an amount of 0 or more"),   # YAML .inf
    ({"monthly_payment": float("nan")}, f"{_WHERE}.monthly_payment: expected an amount of 0 or more"),   # YAML .nan
    ({"monthly_escrow": True}, f"{_WHERE}.monthly_escrow: expected an amount of 0 or more"),
    ({"escrow_balance": [1]}, f"{_WHERE}.escrow_balance: expected an amount of 0 or more"),
    ({"term_months": 0}, f"{_WHERE}.term_months: expected a positive whole number"),
    ({"term_months": 360.5}, f"{_WHERE}.term_months: expected a positive whole number"),
    ({"term_months": "360"}, f"{_WHERE}.term_months: expected a positive whole number"),
    ({"term_months": True}, f"{_WHERE}.term_months: expected a positive whole number"),
    ({"origination": "2030-01-15"}, f"{_WHERE}.origination: expected a mapping"),
    ({"origination": {"when": "2030-01-15"}}, f"{_WHERE}.origination.when: unknown key — one of date, amount"),
    ({"origination": {"date": "2030-13-01"}}, f"{_WHERE}.origination.date: expected an ISO date"),
    ({"origination": {"date": "01/15/2030"}}, f"{_WHERE}.origination.date: expected an ISO date"),
    ({"origination": {"date": 20300115}}, f"{_WHERE}.origination.date: expected an ISO date"),
    ({"origination": {"amount": 0}}, f"{_WHERE}.origination.amount: expected an amount above 0"),
    ({"origination": {"amount": True}}, f"{_WHERE}.origination.amount: expected an amount above 0"),
])
def test_a_malformed_loan_block_is_refused_at_load(tmp_path, loan, message):
    with pytest.raises(ValueError) as err:
        _config(tmp_path, loan=loan)
    assert str(err.value).startswith(message)


def test_a_whole_number_too_large_for_a_float_is_no_amount(tmp_path):
    with pytest.raises(ValueError) as err:            # it would overflow wherever it is used as dollars
        _config(tmp_path, loan={"escrow_balance": 10 ** 400})
    assert str(err.value).startswith("accounts.yaml: accounts.demo_mortgage_0000.loan.escrow_balance: expected an "
                                     "amount of 0 or more")


def test_loan_terms_on_an_account_that_is_not_a_loan_are_refused(tmp_path):
    with pytest.raises(ValueError) as err:
        _config(tmp_path, checking={"loan": {"rate": 6.5}})
    assert str(err.value).startswith("accounts.yaml: accounts.demo_checking_0000.loan: only a loan account has loan "
                                     "terms")


def test_a_loan_account_that_is_not_the_mortgage_may_carry_loan_terms(tmp_path):
    cfg = _config(tmp_path, mortgage=False,
                  checking={"kind": "loan", "loan": {"rate": 6.5, "monthly_payment": 2000}})
    assert cfg.loan_terms("demo_checking_0000")["monthly_payment"] == 2000


def test_thresholds_no_longer_carry_a_mortgage_block(tmp_path):
    assert "mortgage" not in THRESHOLD_KEYS
    p = isolated_pipeline(tmp_path, [], cfg=_config(tmp_path, assumptions={"match_window_bdays": 4}), windows={})
    p._export_assumptions(tmp_path)
    assert json.loads((tmp_path / "assumptions.json").read_text())["thresholds"] == {"match_window_bdays": 4}


def test_the_projections_are_exported_without_a_home_value(tmp_path):
    """The home's value is the House-class rows of the snapshot: the projections carry no second figure for it, and
    nothing adds one when the file states none."""
    manual = [{"account": "Sample Home", "institution": "Self", "owner": "Alex", "asset_class": "House",
               "value": 500_000, "as_of": "2030-01-31"}]
    p = isolated_pipeline(tmp_path, [], windows={},
                          cfg=_config(tmp_path, manual=manual, assumptions={"projections": {"inflation_pct": 2.5}}))
    p._export_assumptions(tmp_path)
    projections = json.loads((tmp_path / "assumptions.json").read_text())["projections"]
    defaults = {k: list(v) if isinstance(v, tuple) else v for k, v in exports.PROJECTION_DEFAULTS.items()}
    assert projections == {**defaults, "inflation_pct": 2.5}

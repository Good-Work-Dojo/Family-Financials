# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Household facts come from config/household.yaml, config/accounts.yaml `household:` and the counter-hint groups of
config/transfer_rules.yaml, not from code. The tests that need a whole config start from the demo household's.
Everything here is invented."""
from __future__ import annotations

import json

import yaml
from conftest import (
    demo_config,
    demo_copy,
    isolated_pipeline,
    load_demo,
    make_txn,
    write_household,
)

from etl.config import Config
from etl.enrich.transfers import TransferEngine
from etl.models import BalanceObs, Holding
from etl.pipeline import Pipeline
from tools.demo import household as hh

CFG = demo_config()


def _engine(household):
    rules = {"rules": [], "venmo_patterns": {"debit": "VENMO", "credit": "CASHOUT", "zelle": "ZELLE",
                                             "zelle_party": r"ZELLE PAYMENT TO ([A-Z ]+?) \d"}}
    return TransferEngine([], rules, {}, {"chk": ("2026-01-01", "2026-12-31")}, {"chk"}, household=household)


def test_own_name_zelle_rows_are_candidates_only_with_household():
    ours = make_txn(desc="ZELLE PAYMENT TO SAM SAMPLE 123", cents=-5000, account="chk")
    theirs = make_txn(desc="ZELLE PAYMENT TO SOMEONE ELSE 123", cents=-5000, account="chk")
    eng = _engine({"own_name_tokens": [hh.SURNAME.upper()]})
    assert eng._is_venmoish(ours) and not eng._is_venmoish(theirs)
    assert not _engine(None)._is_venmoish(ours)        # no household -> nobody is "us"


def test_an_own_name_token_matches_a_whole_word_of_the_party_only():
    """An adult named Ed is not every party whose name holds those letters: FRED SMITH and TEDDY are third parties,
    whose Zelle rows are real spending; ED SMITH and SMITH ED are the household's own."""
    eng = _engine({"own_name_tokens": ["ED"]})
    for party, ours in (("FRED SMITH", False), ("TEDDY ROE", False), ("EDNA ROE", False), ("ED SMITH", True),
                        ("SMITH ED", True)):
        row = make_txn(desc=f"ZELLE PAYMENT TO {party} 123", cents=-5000, account="chk")
        assert eng._is_venmoish(row) is ours, party


def test_counter_groups_come_from_the_rule_file_and_skip_own_account():
    groups = {"demo_pair": {"accounts": ["a_chk", "a_sav"], "expect_counterpart": True}}
    eng = TransferEngine([], {"groups": groups}, {}, {}, set())
    t = make_txn(desc="X", cents=-100, account="a_chk", counter_hint="demo_pair")
    assert eng._counter_accounts(t) == ["a_sav"]
    assert _engine(None)._counter_accounts(t) == []


_LIABILITY_ACCOUNTS = {
    "demo_checking_0000": {"name": "Sample Checking", "institution": "Sample Bank", "owner": "Alex",
                           "kind": "checking", "simplefin_id": "ACT-demo-checking"},
    "demo_brokerage_0000": {"name": "Sample Brokerage", "institution": "Sample Broker", "owner": "Sam",
                            "kind": "brokerage", "margin": True, "simplefin_id": "ACT-demo-brokerage"},
    "demo_mortgage_0000": {"name": "Sample Mortgage", "institution": "Sample Lender", "owner": "Alex",
                           "kind": "loan", "mortgage": True, "simplefin_id": "ACT-demo-mortgage"},
}


def test_the_margin_and_mortgage_rows_take_their_names_and_owners_from_config(tmp_path):
    d = tmp_path / "cfg"
    d.mkdir()
    (tmp_path / "src").mkdir()
    (d / "accounts.yaml").write_text(yaml.safe_dump({"source_dir": "src", "accounts": _LIABILITY_ACCOUNTS}))
    write_household(d)
    cfg = Config(d, source_dir=str(tmp_path / "src"))
    p = isolated_pipeline(tmp_path / "state", [], cfg=cfg, windows={})
    # live feed balances: the brokerage's balance minus its holdings is the margin loan, the loan's is the mortgage
    p.simplefin_live_accounts = {"demo_brokerage_0000", "demo_mortgage_0000"}
    p.balance_obs = [BalanceObs("demo_brokerage_0000", "2030-01-15", 100_000_00),
                     BalanceObs("demo_mortgage_0000", "2030-01-14", -250_000_00)]
    p.holdings = [Holding(account="Sample Brokerage", institution="Sample Broker", owner="Sam", symbol="DEMO",
                          name="Demo Fund", asset_class="Stocks/ETF", quantity=10.0, value_cents=130_000_00,
                          cost_basis_cents=None, as_of="2030-01-15", account_id="demo_brokerage_0000")]
    p.parse_assets()
    rows = {r.asset_class: r for r in p.asset_rows}
    margin, mort = rows["Margin"], rows["Mortgage"]
    assert (mort.account, mort.institution, mort.owner, mort.account_id) == \
        ("Sample Mortgage", "Sample Lender", "Alex", "demo_mortgage_0000")
    assert (mort.value_cents, mort.as_of) == (-250_000_00, "2030-01-14")
    assert (margin.account, margin.institution, margin.owner, margin.account_id) == \
        ("Sample Broker Margin", "Sample Broker", "Sam", "demo_brokerage_0000")
    assert (margin.value_cents, margin.as_of) == (-30_000_00, "2030-01-15")


def _cfg(adults=(), surname=None, **household):
    """A fresh Config (own dicts, safe to mutate) with these adults and surname and its accounts.yaml
    `household:` block replaced."""
    cfg = demo_config()
    cfg.adults, cfg.surname = tuple(adults), surname
    cfg.accounts_cfg["household"] = household
    return cfg


def test_explicit_household_lists_are_normalized_like_derived_ones():
    # derived from adults + surname: any spelling in the YAML, always upper
    hh_ = _cfg(adults=["alex", "SAM"], surname="sample").household
    assert hh_["own_name_tokens"] == ["ALEX", "SAM", "SAMPLE"]
    # given explicitly: the matcher compares against UPPER descriptors, so a lowercase token
    # would silently never match
    hh_ = _cfg(adults=["Alex"], surname="Sample", own_name_tokens=["saMple", "Alex"]).household
    assert hh_["own_name_tokens"] == ["SAMPLE", "ALEX"]
    # and the normalized tokens really do match a Zelle row
    eng = _engine(hh_)
    assert eng._is_venmoish(make_txn(desc="ZELLE PAYMENT TO ALEX SAMPLE 123", cents=-5000, account="chk"))


def test_assumptions_export_carries_household_and_keeps_it_out_of_thresholds(tmp_path):
    Pipeline(CFG)._export_assumptions(tmp_path)
    doc = json.loads((tmp_path / "assumptions.json").read_text())
    exported = doc["household"]
    assert [(p["name"], p["birth_year"]) for p in exported["people"]] == [(m.name, m.birth_year) for m in hh.MEMBERS]
    stated = load_demo("assumptions.yaml")["household"]["margin_apr"]
    assert stated is not None and exported["margin_apr"] == stated
    assert exported["margin_account"] == CFG.accounts[CFG.margin_account_ids()[0]]["name"] == \
        hh.ACCOUNTS[hh.BROKERAGE].name
    assert "household" not in doc["thresholds"]
    assert "cancelled_subscriptions" in doc["thresholds"]      # the passthrough is otherwise untouched
    assert doc["category_renames"] == []   # the demo config carries renames: []
    assert "category_renames" not in doc["thresholds"]


def test_assumptions_export_carries_the_category_renames_map(tmp_path):
    # two independent entries (a pair rename inside Dining next to Eating Out -> Dining would be a chain,
    # which Config refuses): a whole-category move and a pair rename in another category
    renames = [{"from": {"category": "Eating Out"}, "to": {"category": "Dining"}},
               {"from": {"category": "Housing", "subcategory": "Home Services"},
                "to": {"category": "Housing", "subcategory": "Home Improvement"}}]
    d = demo_copy(tmp_path, {"category_rules.yaml": lambda doc: doc.__setitem__("renames", renames)})
    out = tmp_path / "exports"
    out.mkdir()
    Pipeline(Config(d))._export_assumptions(out)
    exported = json.loads((out / "assumptions.json").read_text())
    assert exported["category_renames"] == renames


def _exported_margin_account(tmp_path, accounts):
    cfg = _cfg()
    cfg.accounts_cfg["accounts"] = accounts
    Pipeline(cfg)._export_assumptions(tmp_path)
    return json.loads((tmp_path / "assumptions.json").read_text())["household"]["margin_account"]


def test_margin_account_is_null_without_a_margin_flag(tmp_path):
    accounts = {"alpha": {"name": "Alpha Brokerage"}, "beta": {"name": "Beta Brokerage", "margin": False}}
    assert _exported_margin_account(tmp_path, accounts) is None


def test_margin_account_is_the_first_flagged_in_accounts_yaml_order(tmp_path):
    # deliberately not alphabetical: "the" margin account is file order, never a sort
    accounts = {"zeta": {"name": "Zeta Brokerage", "margin": True},
                "plain": {"name": "Plain Checking"},
                "alpha": {"name": "Alpha Brokerage", "margin": True}}
    assert _exported_margin_account(tmp_path, accounts) == "Zeta Brokerage"

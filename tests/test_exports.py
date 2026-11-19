# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""etl/exports.py — contract pieces of the export layer that have no e2e-free test."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from types import SimpleNamespace

import yaml
from conftest import ROOT, write_household

from etl import exports, networth
from etl.config import Config
from etl.models import (
    ACCOUNT_KINDS,
    ASSET_CLASSES,
    DEFAULT_CLASS_BY_KIND,
    LIABILITY_CLASSES,
    BalanceObs,
    Holding,
)
from etl.pipeline import Pipeline

SETUP = {"accounts": 2, "feed": True, "import_files": 3, "manual_assets": 1}
# what a bare config needs for parse_assets beyond its accounts: no document of assets entered in the app, the
# owners an app asset may name, and the zone whose day is the ingest day
_NO_APP = {"manual_assets_path": Path("/nonexistent-manual-assets/manual_assets.json"), "owners": ("Alex", "Sam", "Joint"),
           "timezone": "UTC"}


def _manifest(files: list[str]) -> dict:
    return exports.export_manifest(
        generated_at="2026-10-02T10:00:00-05:00",
        simplefin_fetched_at="2026-10-02T15:00:00Z",
        transaction_count=3, current_snapshot_date="2026-10-02", setup=SETUP, files=files)


def test_manifest_carries_schema_version_and_lists_itself():
    # the caller globs the export dir BEFORE manifest.json exists (a from-scratch dir), so the
    # input does not name it — export_manifest must add it
    m = _manifest(["transactions.json"])
    assert m["schema_version"] == exports.SCHEMA_VERSION == 23
    assert m["files"] == ["manifest.json", "transactions.json"]      # sorted, includes itself
    assert m["transaction_count"] == 3 and m["generated_at"].endswith("-05:00")
    assert m["setup"] == SETUP
    assert set(m) == {"schema_version", "generated_at", "simplefin_fetched_at",
                      "transaction_count", "current_snapshot_date", "setup", "files"}


def test_an_empty_household_has_no_current_total(capsys):
    """With no snapshot the current net worth is unknown: exported null, never a $0 that reads as a balance, and the
    console summary says so. A household whose balances really add up to zero still exports 0.0."""
    empty = Pipeline(SimpleNamespace(accounts={}, accounts_cfg={"manual_entries": []},
                                     net_worth_options={"include_credit_cards": True}, **_NO_APP))
    empty.parse_assets()
    assert (empty.current_snapshot_date, empty.current_total_cents()) == (None, None)
    assert exports.export_networth_daily(empty.networth_series, empty.current_total_cents(), None) == {
        "series": [], "current_total": None, "current_snapshot_date": None}
    empty.print_summary()
    assert "current net worth: not available yet (no balance reported)" in capsys.readouterr().out.splitlines()
    accounts = {"demo_checking_0000": _CHECKING,
                "demo_card_0000": {"name": "Sample Card", "institution": "Sample Bank", "owner": "Alex",
                                   "kind": "credit-card"}}
    zero = Pipeline(SimpleNamespace(accounts=accounts, accounts_cfg={"manual_entries": []},
                                    net_worth_options={"include_credit_cards": True}, mortgage_account_id=lambda: None,
                                    **_NO_APP))
    zero.balance_obs = [BalanceObs("demo_checking_0000", "2030-01-10", 100_00, source="csv"),
                        BalanceObs("demo_card_0000", "2030-01-10", -100_00)]
    zero.parse_assets()
    assert (zero.current_snapshot_date, zero.current_total_cents()) == ("2030-01-10", 0)
    out = exports.export_networth_daily(zero.networth_series, zero.current_total_cents(), zero.current_snapshot_date)
    assert out["current_total"] == 0.0 and out["current_snapshot_date"] == "2030-01-10"


def test_the_asset_classes_are_exported(tmp_path):
    """assumptions.json lists every asset class the engine knows, the assets in the order etl/models.py gives them,
    then the debts in that order; the dashboard's built-in list (lib/tokens.ts, for an export older than the key)
    names the same classes and the same debts."""
    d = tmp_path / "cfg"
    write_household(d)
    (tmp_path / "src").mkdir()
    (d / "accounts.yaml").write_text(yaml.safe_dump({"source_dir": "src", "accounts": {"demo_checking_0000": _CHECKING}}))
    out = tmp_path / "exports"
    out.mkdir()
    Pipeline(Config(d, source_dir=str(tmp_path / "src")))._export_assumptions(out)
    classes = json.loads((out / "assumptions.json").read_text())["asset_classes"]
    assert classes == {"assets": ["Cash", "Retirement", "Stocks/ETF", "Crypto", "Precious Metals", "Real Estate/Venture",
                                  "House", "Car", "529", "Private Stock"],
                       "liabilities": ["Mortgage", "Margin", "Other Debt"]}
    assert classes["assets"] + classes["liabilities"] == [c for c in ASSET_CLASSES if c not in LIABILITY_CLASSES] + [
        c for c in ASSET_CLASSES if c in LIABILITY_CLASSES]
    assert set(classes["liabilities"]) == LIABILITY_CLASSES
    ts = (ROOT / "app" / "src" / "lib" / "tokens.ts").read_text("utf-8")
    colours = re.search(r"CLASS_COLORS[^=]*=\s*\{(.*?)\};", ts, re.S)
    debts = re.search(r"DEBT_CLASSES\s*=\s*new Set\(\[(.*?)\]\)", ts, re.S)
    assert colours and debts, "CLASS_COLORS or DEBT_CLASSES literal not found in lib/tokens.ts"
    keys = re.findall(r'^\s*"?([^":\n]+?)"?\s*:', colours[1], re.M)
    assert set(keys) == set(ASSET_CLASSES)
    assert re.findall(r'"([^"]+)"', debts[1]) == classes["liabilities"]


def test_the_apps_live_balance_sources_are_exactly_the_one_the_pipeline_emits():
    """A brokerage's row at its own feed balance carries one source label, with holdings or without,
    and the app counts as invested balances exactly the sources in lib/holdings.ts
    LIVE_BALANCE_SOURCES: a label the set lacks makes the account vanish from Investments and
    Planning without an error."""
    accounts = {a: {"name": a, "institution": "Sample", "owner": "Alex", "kind": "brokerage"}
                for a in ("demo_brokerage_0000", "demo_brokerage_0001")}
    series = networth.build_series([BalanceObs(a, "2030-01-10", 100_00) for a in accounts], accounts, [],
                                   include_credit_cards=False)
    holdings = [Holding(account="demo_brokerage_0001", institution="Sample", owner="Alex", symbol="SMPL",
                        name="Sample fund", asset_class="Stocks/ETF", quantity=1.0, value_cents=90_00,
                        cost_basis_cents=None, as_of="2030-01-10", account_id="demo_brokerage_0001")]
    snap = networth.snapshot_at(series, "2030-01-10", holdings=holdings, live=frozenset(accounts))
    emitted = {r.source for r in snap.rows}
    ts = (ROOT / "app" / "src" / "lib" / "holdings.ts").read_text("utf-8")
    m = re.search(r"LIVE_BALANCE_SOURCES(?::[^=]*)?\s*=\s*new Set\(\[(.*?)\]\)", ts, re.S)
    assert m, "LIVE_BALANCE_SOURCES Set literal not found in lib/holdings.ts"
    assert set(re.findall(r'"([^"]+)"', m[1])) == emitted == {"simplefin_balance"}


def test_the_dashboards_kind_defaults_are_the_engines():
    """An export older than the resolved class says null for an account that takes its kind's default; the dashboard
    reads it as the class the engine gives such an account (lib/accounts.ts DEFAULT_CLASS_BY_KIND), for every kind the
    engine knows."""
    ts = (ROOT / "app" / "src" / "lib" / "accounts.ts").read_text("utf-8")
    m = re.search(r"DEFAULT_CLASS_BY_KIND[^=]*=\s*\{(.*?)\};", ts, re.S)
    assert m, "DEFAULT_CLASS_BY_KIND literal not found in lib/accounts.ts"
    table = dict(re.findall(r'"?([\w-]+)"?:\s*"([^"]+)"', m[1]))
    assert {k: v for k, v in table.items() if k in ACCOUNT_KINDS} == dict(DEFAULT_CLASS_BY_KIND)


def test_manifest_files_never_duplicates_itself():
    # a re-ingest into a populated dir: the glob already sees last run's manifest.json
    m = _manifest(["transactions.json", "manifest.json", "accounts.json"])
    assert m["files"] == ["accounts.json", "manifest.json", "transactions.json"]


def test_the_asset_series_is_the_current_snapshot_of_feed_and_stated_rows_with_its_roll_ups():
    """With every account point in one month the current snapshot is the only one: the balance series' rows plus the
    family's `manual_entries` (source `stated`, never assumed-dated), dated by the newest account observation. Its
    total, assets and liabilities, the total without house and car, the liquid total and the owner and class roll-ups
    all sum those rows; its source is its first exported row's, and the file carries nothing but the snapshots."""
    accounts = {"demo_checking_0000": {"name": "Sample Checking", "institution": "Sample Bank",
                                       "owner": "Alex", "kind": "checking"}}
    manual = [
        {"account": "Sample House", "institution": "Sample", "owner": "Joint", "asset_class": "House",
         "value": 400_000, "as_of": "2030-01-01"},
        {"account": "Sample Car", "institution": "Sample", "owner": "Sam", "asset_class": "Car",
         "value": 20_000, "as_of": "2030-01-01"},
        {"account": "Sample Cash", "institution": "Sample", "owner": "Sam", "asset_class": "Cash",
         "value": 300, "liquid": True, "as_of": "2030-01-05"},
    ]
    p = Pipeline(SimpleNamespace(accounts=accounts, accounts_cfg={"manual_entries": manual},
                                 net_worth_options={"include_credit_cards": True},
                                 margin_account_ids=lambda: [], mortgage_account_id=lambda: None, **_NO_APP))
    p.balance_obs = [BalanceObs("demo_checking_0000", "2030-01-10", 1_000_00, source="csv")]
    p.parse_assets()
    out = exports.export_assets_timeseries(p.asset_rows, p.mortgage_included, p.snapshot_coverage)
    assert set(out) == {"snapshots"}
    (snap,) = out["snapshots"]
    assert snap["snapshot_date"] == p.current_snapshot_date == "2030-01-10"
    assert (snap["total"], snap["total_excl_house_car"], snap["liquid_total"]) == (421_300.0, 1_300.0, 1_300.0)
    assert (snap["assets_total"], snap["liabilities_total"], snap["coverage"], snap["missing"]) == (
        421_300.0, 0.0, 1.0, [])
    assert snap["source"] == snap["rows"][0]["source"] == "stated"
    assert snap["by_owner"] == {"Alex": 1_000.0, "Joint": 400_000.0, "Sam": 20_300.0}
    assert snap["by_class"] == {"Car": 20_000.0, "Cash": 1_300.0, "House": 400_000.0}
    assert snap["mortgage_included"] is True                      # no mortgage account is configured
    assert [(r["account"], r["source"], r["as_of"], r["as_of_assumed"], r["liquid"]) for r in snap["rows"]] == [
        ("Sample House", "stated", "2030-01-01", False, False),
        ("Sample Car", "stated", "2030-01-01", False, False),
        ("Sample Checking", "bank_balance", "2030-01-10", False, True),
        ("Sample Cash", "stated", "2030-01-05", False, True),
    ]
    # a stated row names its manual asset; an account's row names none; none of these is carried back
    assert [(r["manual_id"], r["carried_back"]) for r in snap["rows"]] == [
        ("sample-house", False), ("sample-car", False), (None, False), ("sample-cash", False)]
    assert list(snap["rows"][0]) == ["account", "account_id", "institution", "owner", "asset_class", "value", "liquid",
                                     "as_of", "as_of_assumed", "needs_review", "source", "manual_id", "carried_back"]


_CHECKING = {"name": "Sample Checking", "institution": "Sample Bank", "owner": "Alex", "kind": "checking"}
_LOAN = {"name": "Sample Loan", "institution": "Sample Lender", "owner": "Alex", "kind": "loan", "mortgage": True}


def test_every_snapshot_exports_in_date_order_with_its_own_missing_coverage_and_mortgage_flag():
    accounts = {"demo_checking_0000": _CHECKING, "demo_loan_0000": _LOAN}
    p = Pipeline(SimpleNamespace(accounts=accounts, accounts_cfg={"manual_entries": []},
                                 net_worth_options={"include_credit_cards": False},
                                 mortgage_account_id=lambda: "demo_loan_0000", **_NO_APP))
    p.balance_obs = [BalanceObs("demo_checking_0000", "2030-03-12", 300_00),
                     BalanceObs("demo_loan_0000", "2030-02-10", -1_000_00),
                     BalanceObs("demo_checking_0000", "2030-01-05", 100_00)]
    p.parse_assets()
    # the rows' order does not decide the snapshots' order
    out = exports.export_assets_timeseries(list(reversed(p.asset_rows)), p.mortgage_included, p.snapshot_coverage)
    assert [(s["snapshot_date"], s["coverage"], s["missing"], s["mortgage_included"], s["total"])
            for s in out["snapshots"]] == [
        ("2030-01-31", 0.5, ["Sample Loan"], False, 100.0),
        ("2030-02-28", 1.0, [], True, -900.0),
        ("2030-03-12", 1.0, [], True, -700.0)]
    assert [len(s["rows"]) for s in out["snapshots"]] == [1, 2, 2]


def test_the_assumptions_mortgage_is_the_current_snapshots_row_when_an_older_snapshot_has_one_too(tmp_path):
    d = tmp_path / "cfg"
    write_household(d)
    (tmp_path / "src").mkdir()
    (d / "accounts.yaml").write_text(yaml.safe_dump({"source_dir": "src", "accounts": {
        "demo_checking_0000": _CHECKING, "demo_loan_0000": _LOAN}}))
    p = Pipeline(Config(d, source_dir=str(tmp_path / "src")))
    p.balance_obs = [BalanceObs("demo_loan_0000", "2030-01-20", -200_000_00),
                     BalanceObs("demo_loan_0000", "2030-03-10", -199_000_00),
                     BalanceObs("demo_checking_0000", "2030-03-15", 100_00)]
    p.parse_assets()
    assert [(r.snapshot_date, r.value_cents) for r in p.asset_rows if r.account_id == "demo_loan_0000"] == [
        ("2030-01-31", -200_000_00), ("2030-02-28", -200_000_00), ("2030-03-15", -199_000_00)]
    out = tmp_path / "exports"
    out.mkdir()
    p._export_assumptions(out)
    mortgage = json.loads((out / "assumptions.json").read_text())["mortgage"]
    assert (mortgage["balance"], mortgage["balance_as_of"]) == (199_000.0, "2030-03-10")


def test_networth_daily_timeline_is_the_sorted_union_of_every_net_worth_observation_date():
    # every source dates the curve alike; a credit card's dates stay out of it with its balance while cards are out
    accounts = {a: {"name": a, "institution": "Sample Bank", "owner": "Alex", "kind": kind}
                for a, kind in (("demo_checking_0000", "checking"), ("demo_brokerage_0000", "brokerage"),
                                ("demo_card_0000", "credit-card"))}
    obs = [BalanceObs("demo_brokerage_0000", "2030-01-20", 900_00, source="csv"),
           BalanceObs("demo_checking_0000", "2030-01-05", 100_00, source="csv"),
           BalanceObs("demo_checking_0000", "2030-01-15", 150_00),
           BalanceObs("demo_brokerage_0000", "2030-01-10", 800_00),
           BalanceObs("demo_card_0000", "2030-01-12", -50_00)]
    series = networth.build_series(obs, accounts, [], include_credit_cards=False)
    out = exports.export_networth_daily(series, 1050_00, "2030-01-20")
    assert set(out) == {"series", "current_total", "current_snapshot_date"}
    assert (out["current_total"], out["current_snapshot_date"]) == (1050.0, "2030-01-20")
    assert [p["date"] for p in out["series"]] == ["2030-01-05", "2030-01-10", "2030-01-15", "2030-01-20"]
    # each date sums every account's balance on it; before its first observation the brokerage is held at that
    # first balance and the day is partly covered
    assert [p["networth"] for p in out["series"]] == [900.0, 900.0, 950.0, 1050.0]
    assert [p["coverage"] for p in out["series"]] == [0.5, 1.0, 1.0, 1.0]
    assert {tuple(p) for p in out["series"]} == {("date", "networth", "coverage", "source")}
    assert {p["source"] for p in out["series"]} == {"simplefin"}
    # the line reads the series map only: the total beside it decides no point
    assert exports.export_networth_daily(series, None, None)["series"] == out["series"]
    # with cards counted the card's date and balance join the line, and coverage is rounded to 3 places
    with_cards = exports.export_networth_daily(
        networth.build_series(obs, accounts, [], include_credit_cards=True), 1000_00, "2030-01-20")
    assert [(p["date"], p["networth"], p["coverage"]) for p in with_cards["series"]] == [
        ("2030-01-05", 850.0, 0.333), ("2030-01-10", 850.0, 0.667), ("2030-01-12", 850.0, 1.0),
        ("2030-01-15", 900.0, 1.0), ("2030-01-20", 1000.0, 1.0)]


def test_write_json_is_atomic_and_does_not_fsync_by_default(tmp_path, monkeypatch):
    # the derived exports keep their plain behaviour: only durable state pays for an fsync
    calls = []
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd))
    p = tmp_path / "nested" / "x.json"
    exports.write_json(p, {"a": [1, 2]})
    assert json.loads(p.read_text("utf-8")) == {"a": [1, 2]} and not (tmp_path / "nested" / "x.tmp").exists()
    assert calls == []


def test_write_json_replaces_a_link_at_the_name_and_never_writes_through_it(tmp_path):
    """The web server follows links in the exports folder, so a link planted at an export's name is replaced by the
    new file, never written through: what it pointed at is untouched."""
    target = tmp_path / "elsewhere.key"
    target.write_text("not the export's\n")
    exports_dir = tmp_path / "exports"
    exports_dir.mkdir()
    (exports_dir / "manifest.json").symlink_to(target)
    exports.write_json(exports_dir / "manifest.json", {"a": 1})
    assert target.read_text() == "not the export's\n"
    assert not (exports_dir / "manifest.json").is_symlink()
    assert json.loads((exports_dir / "manifest.json").read_text()) == {"a": 1}
    assert stat.S_IMODE(os.stat(exports_dir / "manifest.json").st_mode) == exports.EXPORT_MODE
    assert sorted(p.name for p in exports_dir.iterdir()) == ["manifest.json"]


def test_the_database_is_its_owners_alone_and_never_made_through_a_link(tmp_path):
    from etl import db
    target = tmp_path / "elsewhere"
    target.write_text("untouched\n")
    (tmp_path / "finance.db.tmp").symlink_to(target)
    old = os.umask(0)
    try:
        db.finalize(db.build(tmp_path / "finance.db"), tmp_path / "finance.db")
    finally:
        os.umask(old)
    assert target.read_text() == "untouched\n"
    assert stat.S_IMODE(os.stat(tmp_path / "finance.db").st_mode) == 0o600


def test_export_category_renames_normalizes_pair_and_whole_category_entries():
    """The app mirrors CategoryRenamer.map, so the export must say which entries are PAIR renames:
    `from.subcategory` is present exactly when the YAML entry named it (even as null), and
    `to.subcategory` only when it is set."""
    out = exports.export_category_renames([
        {"from": {"category": "Eating Out"}, "to": {"category": "Dining"}},
        {"from": {"category": "Dining", "subcategory": "Pubs"}, "to": {"category": "Dining", "subcategory": "Bars"}},
        {"from": {"category": "Nightlife", "subcategory": "Shows"}, "to": {"category": "Entertainment", "subcategory": None}},
        {"from": {"category": "Odd", "subcategory": None}, "to": {"category": "Dining"}},
    ])
    assert out == [
        {"from": {"category": "Eating Out"}, "to": {"category": "Dining"}},
        {"from": {"category": "Dining", "subcategory": "Pubs"}, "to": {"category": "Dining", "subcategory": "Bars"}},
        {"from": {"category": "Nightlife", "subcategory": "Shows"}, "to": {"category": "Entertainment"}},
        {"from": {"category": "Odd", "subcategory": None}, "to": {"category": "Dining"}},
    ]
    assert exports.export_category_renames(None) == []
    assert exports.export_category_renames([]) == []

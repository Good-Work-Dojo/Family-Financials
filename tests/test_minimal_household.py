# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The smallest config a stranger could write — examples/minimal: one adult, one checking account read from one small
statement file, no rule file at all — validates, ingests and exports a complete set the dashboard's strict contract
takes (app/src/data/schemas.ts): every key assumptions.json must carry is there, with an honest empty value or its
documented default, never an invented figure. Each test runs a copy of the example in its own tmp dir through FF_HOME,
so nothing is written outside the copy (conftest._repo_state_untouched watches the repo's own data/ and scenarios/).
The example is invented throughout."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from conftest import ROOT

from etl import pipeline
from etl.__main__ import main
from etl.config import Config
from etl.exports import PROJECTION_DEFAULTS, SCHEMA_VERSION, export_projections
from etl.models import RESERVED, RESERVED_TAXONOMY
from etl.validate import KEYS, validate

EXAMPLE = ROOT / "examples" / "minimal"
STARTER = ROOT / "starter"
DOC = ROOT / "docs" / "configuration.md"
ACCOUNT = "demo_checking_0000"
# the export files the dashboard loads (app/src/data/types.ts EXPORT_FILES)
EXPORT_FILES = sorted(["manifest.json", "transactions.json", "accounts.json", "assets_timeseries.json", "holdings.json",
                       "cashflow_monthly.json", "recurring.json", "quality_report.json", "open_questions.json",
                       "assumptions.json", "balances_timeseries.json", "networth_daily.json", "vendors.json",
                       "ai_status.json"])
# the keys of assumptions.json the contract requires (schemas.ts AssumptionsSchema), every one of them
CONTRACT_KEYS = {"projections", "projections_stated", "targets", "budgets", "household", "modules", "category_renames",
                 "reserved", "net_worth", "thresholds", "taxonomy", "owners", "income_phases", "mortgage",
                 "account_windows", "asset_classes"}
# the defaults as the export writes them (a list where the table holds a tuple)
DEFAULTS = {k: list(v) if isinstance(v, tuple) else v for k, v in PROJECTION_DEFAULTS.items()}


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """A copy of the example with FF_HOME pointing at it."""
    d = tmp_path / "home"
    shutil.copytree(EXAMPLE, d)
    monkeypatch.setenv("FF_HOME", str(d))
    return d


def exported(home: Path) -> dict[str, object]:
    out = home / "data" / "exports"
    return {p.name: json.loads(p.read_text("utf-8")) for p in sorted(out.glob("*.json"))}


def cli(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["etl", *args])
    return main()


def add_starter_files(home: Path) -> list[str]:
    names = sorted(p.name for p in STARTER.glob("*.yaml"))
    for name in names:
        shutil.copy(STARTER / name, home / "config" / name)
    return names


# ------------------------------------------------------------------------------------------------ the example

def test_the_example_is_two_config_files_and_one_statement_file():
    assert sorted(p.name for p in (EXAMPLE / "config").iterdir()) == ["accounts.yaml", "household.yaml"]
    assert [p.name for p in (EXAMPLE / "imports").iterdir()] == [f"{ACCOUNT}-2025-01.csv"]
    accounts = yaml.safe_load((EXAMPLE / "config" / "accounts.yaml").read_text("utf-8"))
    assert "source_dir" not in accounts                       # the FF_HOME layout finds imports/
    assert list(accounts["accounts"]) == [ACCOUNT] and "simplefin_id" not in accounts["accounts"][ACCOUNT]


def test_it_validates_with_no_problem(home, monkeypatch, capsys):
    assert validate() == []
    assert cli(monkeypatch, "validate") == 0
    assert capsys.readouterr().out == "config OK (2 files, 1 accounts)\n"


def test_the_ingest_writes_the_13_exports_and_no_check_fails(home):
    assert pipeline.run() == 0
    files = exported(home)
    assert list(files) == EXPORT_FILES
    manifest = files["manifest.json"]
    assert sorted(manifest["files"]) == EXPORT_FILES and manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["transaction_count"] == 15 and manifest["simplefin_fetched_at"] is None
    assert manifest["setup"] == {"accounts": 1, "feed": False, "import_files": 1, "manual_assets": 0}
    assert [c["name"] for c in files["quality_report.json"]["checks"] if c["status"] == "fail"] == []
    assert files["holdings.json"] == {"holdings": []}


def test_assumptions_carry_every_contract_key_empty_or_at_its_default(home):
    assert pipeline.run() == 0
    a = exported(home)["assumptions.json"]
    assert set(a) == CONTRACT_KEYS
    assert a["owners"] == ["Alex", "Joint"]
    # no margin rate is estimated and no retirement account assumed: the config states neither
    assert a["household"] == {"name": "Sample Household", "timezone": "America/New_York",
                              "people": [{"name": "Alex", "role": "owner"}], "margin_apr": None,
                              "retirement_accounts": {}, "margin_account": None}
    assert a["reserved"] == dict(RESERVED)
    assert a["net_worth"] == {"include_credit_cards": True}
    assert a["mortgage"] is None
    assert a["modules"] == {}
    assert a["projections"] == DEFAULTS and list(a["projections"]) == list(PROJECTION_DEFAULTS)
    assert a["projections_stated"] == []
    assert a["taxonomy"] == {c: list(subs) for c, subs in RESERVED_TAXONOMY.items()}
    assert (a["targets"], a["budgets"], a["thresholds"]) == ({}, {}, {})
    assert (a["category_renames"], a["income_phases"]) == ([], [])
    assert a["account_windows"] == {ACCOUNT: {"start": "2025-01-02", "end": "2025-01-30"}}


def test_net_worth_is_one_snapshot_of_one_cash_row_and_a_one_point_daily_line(home):
    """The statement format carries a balance on every row: the newest one is the snapshot's one row and the daily
    line's one point. The example's month is in the past, so ingested on the wall clock (no FF_NOW reaches a test), as
    a newcomer's first run is, the month is in coverage and the floor is set."""
    assert pipeline.run() == 0
    files = exported(home)
    [snap] = files["assets_timeseries.json"]["snapshots"]
    [row] = snap["rows"]
    assert (row["account_id"], row["asset_class"], row["value"], row["as_of"]) == (ACCOUNT, "Cash", 4865.0,
                                                                                    "2025-01-30")
    assert (snap["snapshot_date"], snap["total"], snap["coverage"], snap["missing"]) == ("2025-01-30", 4865.0, 1.0, [])
    daily = files["networth_daily.json"]
    assert [(p["date"], p["networth"]) for p in daily["series"]] == [("2025-01-30", 4865.0)]
    assert daily["current_snapshot_date"] == "2025-01-30"
    cashflow = files["cashflow_monthly.json"]
    assert cashflow["coverage_floor"] == "2025-01"
    assert [(m["month"], m["in_coverage"]) for m in cashflow["months"]] == [("2025-01", True)]


def test_text_that_is_not_unicode_in_a_family_document_never_stops_the_ingest(home):
    """A manual-assets document, review answers and learned rules each holding a lone surrogate (a hand edit, or a
    store that predates the check) are read as corrupt files: the ingest finishes, with one warning each, and counts
    none of them."""
    family, engine = home / "state" / "family", home / "state" / "engine"
    family.mkdir(parents=True, exist_ok=True)
    engine.mkdir(exist_ok=True)
    stamp = "2030-01-20T09:00:00.000Z"
    (family / "manual_assets.json").write_text(json.dumps({"version": 1, "updated_at": stamp, "assets": [
        {"id": "family-home", "name": "\ud800 home", "owner": "Alex", "asset_class": "House", "liquid": False,
         "values": [{"as_of": "2030-01-15", "value": 100_000}], "updated_at": stamp}]}), "utf-8")
    (family / "answers.json").write_text(json.dumps({"version": 1, "updated_at": stamp, "answers": [
        {"key": "vendor:\ud800", "scope": "vendor", "vendor": "\ud800", "set": {"category": "Dining"},
         "answered_at": stamp}]}), "utf-8")
    (engine / "learned_rules.json").write_text(json.dumps({"version": 1, "rules": [
        {"vendor": "\ud800", "category": "Dining"}]}), "utf-8")
    assert pipeline.run() == 0
    files = exported(home)
    warned = {c["name"] for c in files["quality_report.json"]["checks"] if c["status"] == "warn"}
    assert {"manual_assets.load", "answers.load", "learned_rules.load"} <= warned
    assert files["accounts.json"]["manual_assets"] == []
    assert files["manifest.json"]["setup"]["manual_assets"] == 0


def _listing(root: Path) -> list[tuple[str, int, int]]:
    return [(str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(root.rglob("*"))]


def test_the_run_writes_nothing_into_the_repository_s_example(home, monkeypatch, capsys):
    """The tests run a copy: validating and ingesting it leaves the repository's examples/ exactly as it was."""
    examples = ROOT / "examples"
    before = _listing(examples)
    assert cli(monkeypatch, "validate") == 0
    assert pipeline.run() == 0
    assert (home / "data" / "exports" / "manifest.json").is_file()      # the run wrote its copy's exports
    assert _listing(examples) == before


def test_a_second_run_is_identical_but_for_the_generation_stamp(home):
    assert pipeline.run() == 0
    first = exported(home)
    assert pipeline.run() == 0
    second = exported(home)
    for files in (first, second):
        files["manifest.json"].pop("generated_at")
    assert first == second


def test_the_starter_rule_files_beside_it_validate_and_ingest_too(home, monkeypatch, capsys):
    names = add_starter_files(home)
    assert validate() == []
    assert cli(monkeypatch, "validate") == 0
    assert capsys.readouterr().out == f"config OK ({2 + len(names)} files, 1 accounts)\n"
    assert pipeline.run() == 0
    files = exported(home)
    assert list(files) == EXPORT_FILES
    assert [c["name"] for c in files["quality_report.json"]["checks"] if c["status"] == "fail"] == []
    starter_taxonomy = yaml.safe_load((STARTER / "category_rules.yaml").read_text("utf-8"))["taxonomy"]
    assert files["assumptions.json"]["taxonomy"] == starter_taxonomy
    assert files["assumptions.json"]["projections"] == DEFAULTS     # the starter writes the defaults out
    assert files["assumptions.json"]["projections_stated"] == sorted(PROJECTION_DEFAULTS)


# ------------------------------------------------------------------------------------------------ the defaults

def test_a_stated_projection_wins_and_the_rest_take_their_defaults(home):
    (home / "config" / "assumptions.yaml").write_text("projections: {ltcg_pct: 20}\n", "utf-8")
    assert pipeline.run() == 0
    projections = exported(home)["assumptions.json"]["projections"]
    assert projections == {**DEFAULTS, "ltcg_pct": 20}
    assert list(projections) == ["ltcg_pct", *(k for k in PROJECTION_DEFAULTS if k != "ltcg_pct")]


def test_projections_stated_lists_only_what_the_config_states(home):
    """The keys assumptions.yaml `projections` states, sorted: a key written with no value is not stated, and the
    projections block itself still carries every key, stated or at its default."""
    (home / "config" / "assumptions.yaml").write_text(
        "projections: {state_income_tax_pct: 5, inflation_pct: null, ltcg_pct: 20}\n", "utf-8")
    assert pipeline.run() == 0
    a = exported(home)["assumptions.json"]
    assert a["projections_stated"] == ["ltcg_pct", "state_income_tax_pct"]
    assert a["projections"] == {**DEFAULTS, "ltcg_pct": 20, "state_income_tax_pct": 5}


def test_every_account_exports_its_resolved_class(home, tmp_path):
    """accounts.json gives every account the class its rows take (Config.asset_class_of): the one accounts.yaml
    states, else Mortgage for the `mortgage: true` loan, else its kind's default — never null."""
    path = home / "config" / "accounts.yaml"
    accounts = yaml.safe_load(path.read_text("utf-8"))
    sample = {"institution": "Sample Bank", "owner": "Alex"}
    accounts["accounts"].update({
        "demo_brokerage_0000": {"name": "Sample Brokerage", **sample, "kind": "brokerage"},
        "demo_retirement_0000": {"name": "Sample Retirement", **sample, "kind": "brokerage", "asset_class": "Retirement"},
        "demo_card_0000": {"name": "Sample Card", **sample, "kind": "credit-card"},
        "demo_loan_0000": {"name": "Sample Loan", **sample, "kind": "loan"},
        "demo_mortgage_0000": {"name": "Sample Mortgage", **sample, "kind": "loan", "mortgage": True},
    })
    path.write_text(yaml.safe_dump(accounts, sort_keys=False), "utf-8")
    assert validate() == []
    cfg = Config(home / "config")
    out = tmp_path / "exports"
    out.mkdir()
    pipeline.Pipeline(cfg)._export_accounts(out)
    rows = json.loads((out / "accounts.json").read_text("utf-8"))["accounts"]
    classes = {a["id"]: a["asset_class"] for a in rows}
    assert classes == {ACCOUNT: "Cash", "demo_brokerage_0000": "Stocks/ETF", "demo_retirement_0000": "Retirement",
                       "demo_card_0000": "Other Debt", "demo_loan_0000": "Other Debt", "demo_mortgage_0000": "Mortgage"}
    assert classes == {a: cfg.asset_class_of(a) for a in cfg.accounts}


def test_export_projections_keeps_what_is_stated_in_its_order_and_type():
    assert export_projections({}) == DEFAULTS
    assert export_projections({"inflation_pct": 2}) == {**DEFAULTS, "inflation_pct": 2}
    assert type(export_projections({"inflation_pct": 2})["inflation_pct"]) is int
    stated = {"state_income_tax_pct": 5, "ltcg_pct": 20, "withdrawal_rate_pct": 3.5, "inflation_pct": 2.5,
              "return_scenarios_pct": [4, 6]}
    assert list(export_projections(stated).items()) == list(stated.items())      # a full block comes back as written
    # a key written with no value counts as left out, and keeps its place
    assert list(export_projections({"inflation_pct": None, "ltcg_pct": 20}).items())[:2] == [("inflation_pct", 3.0),
                                                                                            ("ltcg_pct", 20)]
    assert export_projections({})["return_scenarios_pct"] is not export_projections({})["return_scenarios_pct"]


def test_a_margin_account_without_a_rate_validates_and_exports_no_rate(home, tmp_path):
    """The dashboard says which key to add; the validator does not require the rate and nothing estimates one."""
    path = home / "config" / "accounts.yaml"
    accounts = yaml.safe_load(path.read_text("utf-8"))
    accounts["accounts"]["demo_brokerage_0001"] = {"name": "Sample Brokerage", "institution": "Sample Broker",
                                                   "owner": "Alex", "kind": "brokerage", "margin": True}
    path.write_text(yaml.safe_dump(accounts, sort_keys=False), "utf-8")
    assert validate() == []
    out = tmp_path / "exports"
    out.mkdir()
    pipeline.Pipeline(Config(home / "config"))._export_assumptions(out)
    household = json.loads((out / "assumptions.json").read_text("utf-8"))["household"]
    assert (household["margin_apr"], household["margin_account"]) == (None, "Sample Brokerage")


def _registry_default(path: str) -> str:
    [key] = [k for k in KEYS if (k.file, k.path) == ("assumptions.yaml", path)]
    return key.default


def _documented_default(path: str) -> str:
    """The Default cell of the key's row in docs/configuration.md's assumptions.yaml table."""
    section = None
    for line in DOC.read_text("utf-8").splitlines():
        if line.startswith("## "):
            section = line
        elif section == "## `assumptions.yaml`" and line.startswith(f"| `{path}` |"):
            return [cell.strip() for cell in line.strip().strip("|").split("|")][2]
    raise AssertionError(f"docs/configuration.md lists no row for assumptions.yaml {path}")


def test_the_registry_and_the_document_state_the_export_defaults():
    for key, default in DEFAULTS.items():
        stated = _registry_default(f"projections.{key}")
        assert yaml.safe_load(stated) == default, key
        assert _documented_default(f"projections.{key}") == stated, key
    no_rate = "none: margin interest is not estimated"
    assert _registry_default("household.margin_apr") == _documented_default("household.margin_apr") == no_rate

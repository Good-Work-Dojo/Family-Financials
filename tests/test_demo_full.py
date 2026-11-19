# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The `full` demo household, generated once and ingested once (conftest `demo_home`, `demo_exports`), against the
truth the generator wrote beside it. Truth is declared by the generator from what a person would say about each row;
these tests are where the engine is shown to agree. Everything here is invented."""
from __future__ import annotations

import importlib
import json
import pkgutil
import shutil
import sys
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import demo_checks
import pytest
import yaml
from conftest import DEMO_END

from etl.__main__ import main as etl_main
from etl.ai import cli as ai_cli
from etl.ai import policy
from etl.ai.candidates import candidates, withhold
from etl.config import Config
from etl.enrich.vendors import VendorNormalizer
from tools.demo import behaviours, config_writer, generate, profiles, sources
from tools.demo import household as hh
from tools.demo import plan as plan_mod
from tools.demo import truth as truth_mod
from tools.demo.behaviours import Merchant, balance_sheet, spending
from tools.demo.behaviours import ai as ai_behaviour
from tools.demo.dates import business_day_on_or_after, shift_months
from tools.demo.ledger import Expect, Row

HEADER = "# Generated demo household — full. Edit freely.\n"
CONFIG_FILES = ["accounts.yaml", "assumptions.yaml", "bank_category_map.yaml", "category_rules.yaml",
                "household.yaml", "overrides.yaml", "transfer_rules.yaml", "vendor_rules.yaml"]
PATHS = ("vendor_rule", "keyword", "bank_label", "fallback")
HANDLES = {"seam.account", "seam.cutover", "seam.twins", "income.alex_first", "income.alex_second", "income.sam",
           "income.unknown_credit", "income.refund", "income.interest",
           "card_payment.pair", "transfer.pair", "transfer.unpaired_kind_leg", "data_gap.uid", "data_gap.account",
           "data_gap.hint", "open_question.rule", "open_question.uid", "zelle.own_pair", "zelle.third_party",
           "p2p.venmo_above", "p2p.venmo_below", "pending.rows", "future.uid", "future.date",
           *(f"paths.{p}.{side}" for p in PATHS for side in ("below", "above")),
           "mortgage.account", "mortgage.payment", "home.entry", "car.entry", "car.purchase", "modules",
           "brokerage.account", "brokerage.symbols",
           "margin.loan_cents", "retirement.account", "child.account", "child.pair", "stale.account",
           "unmapped.feed_id", "card.limit_cents",
           *(f"subscriptions.{s}" for s in ("monthly", "annual", "price_changed", "cancelled", "lapsed")),
           "split.vendor", "split.purchase", "learned.vendor", "learned.answered", "learned.unanswered",
           "vendor_answer.vendor", "conflict.row", "conflict.key",
           *(f"ai.{kind}" for kind in ("sendable", "name", "digits", "seen", "payment_app", "internal"))}
# the categories a household spends in: every category of the taxonomy but these
NOT_SPENDING = {"Income", "Internal", "Fees & Interest", "Cash & Misc"}


def load(exports: Path, name: str) -> dict:
    return json.loads((exports / name).read_text("utf-8"))


def last_snapshot(home: Path) -> Path:
    return sorted((home / "store" / "simplefin" / "snapshots").glob("accounts-*.json"))[-1]


def snapshot_account(path: Path, account: str) -> dict:
    [entry] = [a for a in json.loads(path.read_text("utf-8"))["accounts"] if a["id"] == hh.ACCOUNTS[account].feed_id]
    return entry


def cents(dollars: float) -> int:
    return round(dollars * 100)


@pytest.fixture(scope="module")
def txns(demo_exports) -> dict[str, dict]:
    return {t["id"]: t for t in load(demo_exports.exports, "transactions.json")["transactions"]}


# ------------------------------------------------------------------ the config

def test_the_generated_config_validates(demo_home, monkeypatch, capsys):
    monkeypatch.setenv("FF_HOME", str(demo_home.path))
    monkeypatch.setattr(sys, "argv", ["etl", "validate"])
    assert etl_main() == 0
    assert capsys.readouterr().out == f"config OK (8 files, {len(demo_home.truth['accounts'])} accounts)\n"
    assert len(demo_home.truth["accounts"]) == len(profiles.FULL.accounts)


def test_every_account_states_its_asset_class(demo_exports):
    """accounts.yaml names each account's asset class (the household's own statement, which truth reads); the engine
    books the account under it."""
    doc = yaml.safe_load((demo_exports.exports.parents[1] / "config" / "accounts.yaml").read_text("utf-8"))
    stated = {a: e.get("asset_class") for a, e in doc["accounts"].items()}
    assert stated == {a: hh.ACCOUNTS[a].asset_class for a in demo_exports.truth["accounts"]}
    exported = {a["id"]: a["asset_class"] for a in load(demo_exports.exports, "accounts.json")["accounts"]}
    assert exported == stated


def test_the_config_writer_alone_writes_the_same_config(demo_home, tmp_path):
    config_writer.write(profiles.FULL, date.fromisoformat(demo_home.truth["end"]), tmp_path / "config")
    assert [p.name for p in tmp_path.iterdir()] == ["config"]                  # nothing but the config directory
    written = sorted(p.name for p in (tmp_path / "config").iterdir())
    assert written == sorted(p.name for p in (demo_home.path / "config").iterdir()) == CONFIG_FILES
    for name in written:
        text = (tmp_path / "config" / name).read_bytes()
        assert text == (demo_home.path / "config" / name).read_bytes(), name
        assert text.decode("utf-8").startswith(HEADER), name


def declared_merchants() -> set[Merchant]:
    """Every merchant a behaviour module names (a module-level Merchant), across the whole behaviours package."""
    out: set[Merchant] = set()
    for info in pkgutil.iter_modules(behaviours.__path__):
        module = importlib.import_module(f"{behaviours.__name__}.{info.name}")
        out |= {v for v in vars(module).values() if isinstance(v, Merchant)}
    return out


def test_every_merchant_is_filed_by_its_vendor_entry(demo_home):
    """Each merchant's descriptor reaches the vendor pattern whose vendor, category and subcategory it declares — the
    starter's, or one the demo adds ahead of them — after the starter's own cleanup (read here through the engine's
    normalizer, over the generated vendor_rules.yaml)."""
    rules = yaml.safe_load((demo_home.path / "config" / "vendor_rules.yaml").read_text("utf-8"))
    starter = yaml.safe_load((config_writer.STARTER / "vendor_rules.yaml").read_text("utf-8"))
    normalizer, starter_only = VendorNormalizer(rules), VendorNormalizer(starter)
    merchants = declared_merchants()
    assert len(merchants) == 31
    own = 0
    for m in sorted(merchants, key=lambda m: m.desc):
        vendor, entry = normalizer.normalize(m.desc)
        assert entry is not None, m.desc
        assert (vendor, entry.get("category"), entry.get("subcategory")) == (m.vendor, m.category, m.subcategory)
        if entry not in starter["patterns"]:
            own += 1
            assert starter_only.normalize(m.desc)[1] is None, m.desc        # no starter entry files it either
    assert own == 6                         # Giving, Taxes, Debt, the lender, the car dealer and the wholesale club


# ------------------------------------------------------------------ the rows

def test_ingest_reports_no_failed_check(demo_exports):
    checks = load(demo_exports.exports, "quality_report.json")["checks"]
    assert [c["name"] for c in checks if c["status"] == "fail"] == []


def test_every_stored_row_is_what_truth_says(demo_exports):
    assert len(demo_exports.truth["figures"]["rows"]) > 900
    assert demo_checks.rows(demo_exports.exports, demo_exports.truth) == []


def test_every_section_of_truth_equals_the_exports(demo_exports):
    assert demo_checks.mismatches(demo_exports.exports, demo_exports.truth) == []


def test_a_truth_of_another_version_or_of_none_is_reported_first(demo_exports):
    """The generator writes the version the comparisons read; a document of any other version, or none, is one line."""
    assert demo_exports.truth["version"] == truth_mod.VERSION == demo_checks.VERSION
    for doc, got in (({**demo_exports.truth, "version": 2}, "2"),
                     ({k: v for k, v in demo_exports.truth.items() if k != "version"}, "None")):
        lines = demo_checks.mismatches(demo_exports.exports, doc)
        assert lines == [f"version: truth.json is version {got}; these checks read version 1"]


# a value each compared field of the refund row never has: what a wrong engine would store instead
WRONG = {"type": "income", "category": "Shopping", "subcategory": "Clothing", "category_source": "keyword",
         "vendor": "Someone Else", "needs_review": True, "is_internal": True, "earner": hh.SAM, "is_parent": True,
         "parent_id": "tx_0000000000000000"}


def wrong_exports(demo_exports, tmp_path: Path) -> tuple[Path, dict, dict]:
    exports = tmp_path / "exports"
    shutil.copytree(demo_exports.exports, exports)
    doc = load(exports, "transactions.json")
    [row] = [t for t in doc["transactions"] if t["id"] == demo_exports.truth["planted"]["income.refund"]]
    return exports, doc, row


def rows_section(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("rows: ")]


@pytest.mark.parametrize("key", [key for _, key in demo_checks.FIELDS])
def test_a_wrong_engine_fails_the_comparison_on_every_field(demo_exports, tmp_path, key):
    """The whole comparison (`demo_checks.mismatches`) names the changed field of the changed row, once, in its rows
    section (other sections may see the change too: a category moves a month's spending)."""
    assert sorted(WRONG) == sorted(key for _, key in demo_checks.FIELDS)
    exports, doc, row = wrong_exports(demo_exports, tmp_path)
    uid, before = row["id"], row[key]
    assert WRONG[key] != before
    row[key] = WRONG[key]
    (exports / "transactions.json").write_text(json.dumps(doc), "utf-8")
    assert rows_section(demo_checks.mismatches(exports, demo_exports.truth)) == [
        f"rows: {uid}: {key}: stored {WRONG[key]!r}, truth {before!r}"]


def test_a_wrong_engine_fails_the_comparison_on_a_missing_or_an_unknown_row(demo_exports, tmp_path):
    exports, doc, row = wrong_exports(demo_exports, tmp_path)
    uid = row["id"]
    doc["transactions"].remove(row)
    (exports / "transactions.json").write_text(json.dumps(doc), "utf-8")
    missing = demo_checks.mismatches(exports, demo_exports.truth)
    assert rows_section(missing) == [f"rows: {uid}: in truth but not stored"]
    assert any(line.startswith("accounts: ") for line in missing)      # its account has a row fewer
    stranger = dict(row, id="tx_0000000000000000", description="A ROW TRUTH NEVER DECLARED")
    doc["transactions"].append(row)
    doc["transactions"].append(stranger)
    (exports / "transactions.json").write_text(json.dumps(doc), "utf-8")
    assert rows_section(demo_checks.mismatches(exports, demo_exports.truth)) == [
        f"rows: tx_0000000000000000: stored but not in truth ({row['date']} 'A ROW TRUTH NEVER DECLARED')"]


def test_a_wrong_setup_or_household_is_seen(demo_exports, tmp_path):
    """The household block, the modules and manifest.setup are compared key by key: a wrong name, zone, module switch
    or count is one line naming its key, and a count only one side has is a line too."""
    exports = tmp_path / "exports"
    shutil.copytree(demo_exports.exports, exports)
    manifest, assumptions = load(exports, "manifest.json"), load(exports, "assumptions.json")
    setup = manifest["setup"]
    assert assumptions["modules"] == demo_exports.truth["planted"]["modules"] == {"home_buying": True}
    manifest["setup"] = {"accounts": setup["accounts"], "import_files": setup["import_files"] + 1,
                         "manual_assets": setup["manual_assets"] + 2}                 # and no feed
    assumptions["household"] = {**assumptions["household"], "name": "Household", "timezone": "Europe/Paris"}
    assumptions["modules"] = {}
    (exports / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    (exports / "assumptions.json").write_text(json.dumps(assumptions), "utf-8")
    lines = [line for line in demo_checks.mismatches(exports, demo_exports.truth)
             if line.startswith(("setup: ", "household: "))]
    assert [line.split(": exported ")[0] for line in lines] == [
        "household: name", "household: timezone", "household: modules", "setup: in truth but not exported: 'feed'",
        "setup: import_files", "setup: manual_assets"]
    assert lines[2] == "household: modules: exported {}, truth {'home_buying': True}"
    assert lines[-1] == (f"setup: manual_assets: exported {setup['manual_assets'] + 2!r}, "
                         f"truth {setup['manual_assets']!r}")


def _first_value_a_cent_up(assets: list[dict]) -> None:
    assets[0]["values"][0]["value"] = round(assets[0]["values"][0]["value"] + 0.01, 2)


OTHER_ORIGIN = {"app": "config", "config": "app"}
MANUAL_CHANGES = [
    # (case, what a wrong engine does to accounts.json `manual_assets`, the line naming it: {id}, {origin}, {other}
    # and {acquired} are the first asset's id, origin, the other origin and its acquisition day)
    ("a-cent-off", _first_value_a_cent_up, "manual_assets: {id}: values: exported "),
    ("origin-changed", lambda m: m[0].update(origin=OTHER_ORIGIN[m[0]["origin"]]),
     "manual_assets: {id}: origin: exported {other!r}, truth {origin!r}"),
    ("asset-dropped", lambda m: m.pop(0), "manual_assets: in truth but not exported: '{id}'"),
    ("extra-asset", lambda m: m.append({**m[0], "id": "zz-extra"}), "manual_assets: exported but not in truth: 'zz-extra'"),
    ("acquired-changed", lambda m: m[0].update(acquired="2001-01-01"),
     "manual_assets: {id}: acquired: exported '2001-01-01', truth {acquired!r}"),
    ("key-removed", lambda m: m[0].pop("liquid"), "manual_assets: {id}: liquid: exported None, truth False"),
    ("order-swapped", lambda m: m.reverse(), "manual_assets: order: exported "),
]


@pytest.mark.parametrize(("change", "line"), [c[1:] for c in MANUAL_CHANGES], ids=[c[0] for c in MANUAL_CHANGES])
def test_a_wrong_engine_fails_the_comparison_on_a_manual_asset(demo_exports, tmp_path, change, line):
    """The manual assets are compared id by id and key by key: a cent off, a changed origin or acquisition day, an
    asset dropped or added, a key removed and the order changed are each a line in the manual_assets section."""
    exports = tmp_path / "exports"
    shutil.copytree(demo_exports.exports, exports)
    doc = load(exports, "accounts.json")
    assert len(doc["manual_assets"]) == 2, "the full household states two manual assets"
    first = dict(doc["manual_assets"][0])
    assert demo_checks.manual_assets(exports, demo_exports.truth) == []                  # the control
    change(doc["manual_assets"])
    (exports / "accounts.json").write_text(json.dumps(doc), "utf-8")
    lines = [x for x in demo_checks.mismatches(exports, demo_exports.truth) if x.startswith("manual_assets: ")]
    want = line.format(id=first["id"], origin=first["origin"], other=OTHER_ORIGIN[first["origin"]],
                       acquired=first["acquired"])
    assert any(x.startswith(want) for x in lines), lines


def _car(truth: dict) -> tuple[dict, int, str, str]:
    """The car as truth declares it: the asset, its one value in cents, the day it was acquired, the day it was
    valued."""
    [car] = [a for a in truth["figures"]["manual_assets"] if a["id"] == truth["planted"]["car.entry"]]
    [value] = car["values"]
    return car, value["value_cents"], car["acquired"], value["as_of"]


def _shift_snapshot(snapshot: dict, cents: int) -> None:
    """Moves a snapshot's Car class and totals by `cents` (a row added or taken away)."""
    car = round(snapshot["by_class"].get("Car", 0) * 100) + cents
    if car:
        snapshot["by_class"]["Car"] = car / 100
    else:
        snapshot["by_class"].pop("Car", None)
    for key in ("total", "assets_total"):
        snapshot[key] = round(snapshot[key] * 100 + cents) / 100


def test_a_wrong_engine_that_holds_the_car_before_it_was_acquired_fails_the_comparison(demo_exports, tmp_path):
    """An engine that ignores the day the car was acquired carries it back from its value to every earlier snapshot
    and day: the snapshots' manual rows and the daily line before that day are each lines."""
    exports = tmp_path / "exports"
    shutil.copytree(demo_exports.exports, exports)
    car, cents, acquired, valued = _car(demo_exports.truth)
    assert demo_checks.mismatches(exports, demo_exports.truth) == []                        # the control
    doc = load(exports, "assets_timeseries.json")
    # the car's carried-back row, from the month end between the two days, copied onto every snapshot before them
    carried = max((s for s in doc["snapshots"] if s["snapshot_date"] < valued), key=lambda s: s["snapshot_date"])
    [row] = [r for r in carried["rows"] if r["manual_id"] == car["id"]]
    early = [s for s in doc["snapshots"] if s["snapshot_date"] < acquired]
    assert early
    for s in early:
        s["rows"].append(dict(row))
        _shift_snapshot(s, cents)
    (exports / "assets_timeseries.json").write_text(json.dumps(doc), "utf-8")
    line = load(exports, "networth_daily.json")
    for p in line["series"]:
        if p["date"] < acquired:
            p["networth"] = round(p["networth"] * 100 + cents) / 100
    (exports / "networth_daily.json").write_text(json.dumps(line), "utf-8")
    lines = demo_checks.mismatches(exports, demo_exports.truth)
    for s in early:
        assert any(x.startswith(f"snapshots: {s['snapshot_date']}: manual_rows: exported ") for x in lines), lines
    days = [d for d in demo_exports.truth["figures"]["daily_points"] if d < acquired]
    assert days and all(any(x.startswith(f"daily_line: {d}: exported ") for x in lines) for d in days), lines


def test_a_wrong_engine_that_does_not_carry_the_car_back_fails_the_comparison(demo_exports, tmp_path):
    """An engine that holds a stated value only from its own date leaves the car out of the month end between the day
    it was bought and the day it was valued, and out of the daily line from the day it was bought: each is a line."""
    exports = tmp_path / "exports"
    shutil.copytree(demo_exports.exports, exports)
    car, cents, acquired, valued = _car(demo_exports.truth)
    doc = load(exports, "assets_timeseries.json")
    [between] = [s for s in doc["snapshots"] if acquired <= s["snapshot_date"] < valued]
    [row] = [r for r in between["rows"] if r["manual_id"] == car["id"]]
    assert row["carried_back"] is True and row["as_of"] == valued
    between["rows"].remove(row)
    _shift_snapshot(between, -cents)
    (exports / "assets_timeseries.json").write_text(json.dumps(doc), "utf-8")
    line = load(exports, "networth_daily.json")
    for p in line["series"]:
        if acquired <= p["date"] < valued:
            p["networth"] = round(p["networth"] * 100 - cents) / 100
    (exports / "networth_daily.json").write_text(json.dumps(line), "utf-8")
    lines = demo_checks.mismatches(exports, demo_exports.truth)
    assert any(x.startswith(f"snapshots: {between['snapshot_date']}: manual_rows: exported ") for x in lines), lines
    assert any(x.startswith(f"daily_line: {acquired}: exported ") for x in lines), lines


def test_a_repeated_check_gone_wrong_is_seen(demo_exports, tmp_path):
    """A check name that repeats (one per statement file) is compared occurrence by occurrence: the first of them
    turning to fail is named, though a later one still passes."""
    exports = tmp_path / "exports"
    shutil.copytree(demo_exports.exports, exports)
    doc = load(exports, "quality_report.json")
    seen: dict[str, list[dict]] = {}
    for check in doc["checks"]:
        seen.setdefault(check["name"], []).append(check)
    [name] = sorted(n for n, c in seen.items() if len(c) > 1 and all(x["status"] == "pass" for x in c))[:1]
    seen[name][0]["status"] = "fail"
    (exports / "quality_report.json").write_text(json.dumps(doc), "utf-8")
    assert [line for line in demo_checks.mismatches(exports, demo_exports.truth) if line.startswith("checks: ")] == [
        f"checks: exported but not in truth: {(name, 'fail')!r}"]


def test_a_learned_rule_truth_declares_but_the_check_lacks_is_seen(demo_exports, tmp_path):
    exports = tmp_path / "exports"
    shutil.copytree(demo_exports.exports, exports)
    truth = json.loads(json.dumps(demo_exports.truth))
    truth["figures"]["learned"].append({"vendor": "Made Up Vendor"})
    lines = demo_checks.answers(exports, truth)
    assert any("Made Up Vendor" in line for line in lines), lines


def test_a_comparison_names_both_directions_of_every_set(demo_exports, tmp_path):
    """A check the engine never reported and a pair it never made, each named once from each side."""
    exports = tmp_path / "exports"
    shutil.copytree(demo_exports.exports, exports)
    truth = json.loads(json.dumps(demo_exports.truth))
    truth["figures"]["checks"]["made.up.check"] = "warn"
    [dropped] = [n for n, s in truth["figures"]["checks"].items() if s == "info"][:1]
    del truth["figures"]["checks"][dropped]
    first = truth["figures"]["pairs"].pop(0)
    assert sorted(demo_checks.checks(exports, truth)) == [
        f"checks: exported but not in truth: {(dropped, 'info')!r}",
        f"checks: in truth but not exported: {('made.up.check', 'warn')!r}"]
    assert demo_checks.pairs(exports, truth)[0] == (
        f"pairs: exported but not in truth: {(first['type'], first['out'], first['into'])!r}")
    # a section truth lacks is compared as empty: every exported pair is then one truth lacks
    del truth["figures"]["pairs"]
    assert len([line for line in demo_checks.pairs(exports, truth) if "not in truth" in line]) == len(
        demo_exports.truth["figures"]["pairs"])


# ------------------------------------------------------------------ months and accounts

def test_the_ai_steps_own_filters_over_the_demo_agree_with_what_truth_declares(demo_home, monkeypatch, tmp_path):
    """The AI step's candidate predicate and its never-send filters, run over the demo's rows as `ai preview` reads
    them, ask about exactly the vendors truth declares sendable and keep back each withheld one for the reason truth
    gives; the payment app's and the internal decoy's vendors are no candidate at all."""
    home = tmp_path / "home"
    shutil.copytree(demo_home.path, home)
    monkeypatch.setenv("FF_HOME", str(home))
    cfg = Config(home / "config")
    found = candidates(cfg, ai_cli._enriched(cfg))
    sendable, withheld = withhold(found, cfg)
    want = demo_home.truth["figures"]["ai"]
    assert want["sendable"] and want["withheld"] and want["not_candidate"]
    assert list(sendable) == want["sendable"]
    assert withheld == want["preview"]["withheld"] and len(sendable) == want["preview"]["vendors"]
    seen = Counter(c.vendor for c in found)
    tokens = policy.household_tokens(cfg)
    assert {key: policy.text_reason(key, tokens) or ("seen" if seen[key] < cfg.ai.min_seen else None)
            for key in want["withheld"]} == want["withheld"]
    assert not set(seen) & set(want["not_candidate"])
    assert {key: len(uids) for key, uids in want["rows"].items() if key in seen} == dict(seen)


def test_the_demos_ai_block_names_the_engines_default_service_and_model_and_its_withheld_name_is_a_member():
    from etl import validate
    assert dict(profiles.FULL.ai) == {"provider": validate.AI_PROVIDERS[0], "model": validate.AI_DEFAULT_MODEL}
    named = [vendor for _, vendor, reason, _ in ai_behaviour.VENDORS if reason == "name"]
    assert named and all(vendor.split()[0] in {m.name for m in hh.MEMBERS} for vendor in named)


def test_a_wrong_ai_preview_or_a_row_filed_by_ai_is_seen(demo_exports, tmp_path):
    exports = tmp_path / "exports"
    shutil.copytree(demo_exports.exports, exports)
    assert demo_checks.ai(exports, demo_exports.truth) == []
    status = load(exports, "ai_status.json")
    status["preview"]["withheld"]["seen"] += 1
    (exports / "ai_status.json").write_text(json.dumps(status), "utf-8")
    doc = load(exports, "transactions.json")
    uid = demo_exports.truth["figures"]["ai"]["rows"][demo_exports.truth["figures"]["ai"]["sendable"][0]][0]
    next(t for t in doc["transactions"] if t["id"] == uid)["category_source"] = "ai"
    (exports / "transactions.json").write_text(json.dumps(doc), "utf-8")
    lines = demo_checks.ai(exports, demo_exports.truth)
    assert len(lines) == 2 and lines[0].startswith("ai: preview: exported ")
    assert lines[1] == f"ai: rows filed by it: exported but not in truth: {uid!r}"


def test_month_totals_equal_truth(demo_exports):
    """Income, spending, investing and spending by category each month, and whether every cash-flow account covers
    the month; the future row's month is among truth's months."""
    assert demo_exports.truth["planted"]["future.date"][:7] in demo_exports.truth["figures"]["months"]
    assert demo_checks.months(demo_exports.exports, demo_exports.truth) == []


def test_accounts_equal_truth(demo_exports):
    assert sorted(demo_exports.truth["figures"]["accounts"]) == sorted(demo_exports.truth["accounts"])
    assert demo_checks.accounts(demo_exports.exports, demo_exports.truth) == []


# ------------------------------------------------------------------ the seam

def test_the_seam(demo_exports, txns):
    report = load(demo_exports.exports, "quality_report.json")
    seam = demo_exports.truth["figures"]["seam"]
    assert sorted(seam) == ["demo_card_0303", "demo_checking_0101", "demo_savings_0202"]
    # the checking exports overlap by a month and the savings month exported twice repeat rows; the card files do not
    assert [bool(seam[a]["duplicates"]) for a in sorted(seam)] == [False, True, True]
    assert all(s["clipped"] > 0 for s in seam.values())
    assert demo_checks.seam(demo_exports.exports, demo_exports.truth) == []
    checks = {c["name"]: c for c in report["checks"]}
    assert [n for n in checks if n.startswith(("simplefin.cutover_gap.", "simplefin.cutover_overlap."))] == []
    planted = demo_exports.truth["planted"]
    twins = [txns[uid] for uid in planted["seam.twins"]]
    assert len({t["id"] for t in twins}) == 2
    assert len({(t["account"], t["date"], t["amount"], t["description"]) for t in twins}) == 1
    assert twins[0]["account"] == planted["seam.account"] and twins[0]["date"] < planted["seam.cutover"]
    first = sorted(seam[planted["seam.account"]]["duplicates"])
    assert len(first) == 1 and twins[0]["source_file"] < first[0]      # stored from the export that lists them first


# ------------------------------------------------------------------ income

def test_income_phases_equal_truth(demo_exports, txns):
    assert demo_checks.income_phases(demo_exports.exports, demo_exports.truth) == []
    phases = load(demo_exports.exports, "assumptions.json")["income_phases"]
    got = [(p["person"], p["phase"], p["start"], p["end"]) for p in phases]
    assert [g[:2] for g in got] == [("Alex", "Quillworks Design"), ("Alex", "Larkspur Robotics"),
                                    ("Sam", "Harbor City Schools")]
    left, joined = got[0][3], got[1][2]
    assert (date.fromisoformat(joined) - date.fromisoformat(left)).days > 21          # three weeks without pay
    assert not [t for t in txns.values() if t["earner"] == "Alex" and left < t["date"] < joined]


def test_the_unknown_credit_is_income_for_review_and_the_refund_reduces_spending(demo_exports, txns):
    planted = demo_exports.truth["planted"]
    credit = txns[planted["income.unknown_credit"]]
    assert (credit["type"], credit["category"], credit["subcategory"], credit["category_source"],
            credit["needs_review"], credit["amount"], credit["earner"]) == (
        "income", "Income", "Other", "fallback", True, 1200.0, None)
    refund = txns[planted["income.refund"]]
    assert (refund["type"], refund["category"], refund["needs_review"], refund["amount"]) == (
        "purchase", "Groceries", False, 38.5)
    month = refund["date"][:7]
    [m] = [m for m in load(demo_exports.exports, "cashflow_monthly.json")["months"] if m["month"] == month]
    charges = -sum(cents(t["amount"]) for t in txns.values()
                   if t["date"][:7] == month and t["category"] == "Groceries" and t["amount"] < 0
                   and not t["is_parent"])          # a split purchase counts by its parts
    assert cents(m["by_category"]["Groceries"]["total"]) == charges - 3850
    assert cents(m["income"]) == demo_exports.truth["figures"]["months"][month]["income_cents"]


def test_coverage_floor_equals_truth(demo_exports):
    """The floor and every month's place on either side of it; the floor is not the first month with data, as the
    cash-flow accounts start at different times."""
    floor = demo_exports.truth["figures"]["coverage_floor"]
    assert floor is not None and min(demo_exports.truth["figures"]["months"]) < floor
    assert demo_checks.coverage_floor(demo_exports.exports, demo_exports.truth) == []


# ------------------------------------------------------------------ transfers and card payments

def test_pairs_share_a_group_and_nothing_else_does(demo_exports, txns):
    """The exported pairs are exactly truth's: every group id holds two rows, an outgoing and an incoming leg of one
    internal type, and the set of (type, outgoing uid, incoming uid) equals truth's pairs. No other row has a group."""
    figures = demo_exports.truth["figures"]
    pairs = figures["pairs"]
    assert {p["type"] for p in pairs} == {"transfer", "cc-payment"}
    assert demo_checks.pairs(demo_exports.exports, demo_exports.truth) == []
    for p in pairs:
        out, into = txns[p["out"]], txns[p["into"]]
        assert out["category"] == into["category"] == "Internal" and out["amount"] == -into["amount"] < 0, p
        assert out["account"] != into["account"] and out["transfer_group_id"] == into["transfer_group_id"], p
    assert figures["internal_rows"] > 2 * len(pairs)
    planted = demo_exports.truth["planted"]
    for handle in ("card_payment.pair", "transfer.pair", "zelle.own_pair"):
        assert sorted(planted[handle]) in [sorted([p["out"], p["into"]]) for p in pairs], handle


def test_the_unpaired_card_payment_is_an_ordinary_row_for_review(demo_exports, txns):
    row = txns[demo_exports.truth["planted"]["transfer.unpaired_kind_leg"]]
    assert (row["type"], row["category"], row["subcategory"], row["category_source"], row["needs_review"],
            row["is_internal"], row["transfer_group_id"], row["account"]) == (
        "purchase", "Cash & Misc", "Uncategorized", "fallback", True, False, None, hh.CHECKING)
    threshold = load(demo_exports.exports, "assumptions.json")["thresholds"]["uncategorized_review_threshold"]
    assert 0 < -row["amount"] < threshold                     # flagged because it reads like a payment, not by its size
    assert "CREDIT CRD" in row["description"]
    assert row["id"] in demo_exports.truth["figures"]["review"]


def test_open_questions_and_data_gaps_equal_truth(demo_exports, txns):
    doc = load(demo_exports.exports, "open_questions.json")
    figures, planted = demo_exports.truth["figures"], demo_exports.truth["planted"]
    assert demo_checks.open_questions(demo_exports.exports, demo_exports.truth) == []
    assert figures["open_questions"] == [planted["open_question.rule"]]
    question = txns[planted["open_question.uid"]]
    assert (question["type"], question["is_internal"], question["transfer_group_id"]) == ("transfer", True, None)
    assert [g["topic"] for g in doc["data_gaps"]] == ["unmatched-internal"]
    [gap] = figures["data_gaps"]
    row = txns[planted["data_gap.uid"]]
    assert (gap["account"], gap["hint"], gap["rows"], gap["net_cents"]) == (
        planted["data_gap.account"], planted["data_gap.hint"], 1, cents(row["amount"]))
    assert (row["type"], row["is_internal"], row["transfer_group_id"]) == ("transfer", True, None)
    window = {a["id"]: a for a in load(demo_exports.exports, "accounts.json")["accounts"]}[gap["hint"]]
    assert window["txn_data_start"] < row["date"] < window["txn_data_end"]   # the other side's data covers the day


# ------------------------------------------------------------------ categorization and person-to-person rows

def test_each_categorization_path_below_and_above_the_threshold(demo_exports, txns):
    planted = demo_exports.truth["planted"]
    threshold = load(demo_exports.exports, "assumptions.json")["thresholds"]["uncategorized_review_threshold"]
    want = {"keyword": ("Kids", "Childcare"), "bank_label": ("Dining", None),
            "fallback": ("Cash & Misc", "Uncategorized")}
    for path in PATHS:
        for side in ("below", "above"):
            row = txns[planted[f"paths.{path}.{side}"]]
            where = f"{path}.{side}"
            assert row["category_source"] == path and row["type"] == "purchase", where
            assert (-row["amount"] < threshold) == (side == "below") and row["amount"] < 0, where
            assert row["needs_review"] == (path == "fallback" and side == "above"), where
            if path in want:
                assert (row["category"], row["subcategory"]) == want[path], where
    label = [txns[planted[f"paths.bank_label.{side}"]] for side in ("below", "above")]
    assert {t["account"] for t in label} == {hh.CARD_ALEX}
    assert sorted(t["source_file"].startswith("accounts-") for t in label) == [False, True]    # a file and the feed
    assert {txns[planted[f"paths.fallback.{side}"]]["account"] for side in ("below", "above")} == {hh.CHECKING}


def test_person_to_person_rows(demo_exports, txns):
    planted = demo_exports.truth["planted"]
    thresholds = load(demo_exports.exports, "assumptions.json")["thresholds"]
    out, into = sorted((txns[u] for u in planted["zelle.own_pair"]), key=lambda t: t["amount"])
    assert (out["account"], into["account"]) == (hh.CHECKING, hh.SAVINGS)
    assert out["type"] == into["type"] == "transfer" and out["transfer_group_id"] == into["transfer_group_id"]
    assert "ALEX SAMPLE" in out["description"] and "ALEX SAMPLE" in into["description"]
    p2p = ("Cash & Misc", "Venmo/Zelle Unknown")
    third = txns[planted["zelle.third_party"]]
    assert (third["type"], third["category"], third["subcategory"], third["category_source"], third["needs_review"],
            third["transfer_group_id"]) == ("purchase", *p2p, "keyword", False, None)
    assert "ZELLE" in third["description"] and 0 < -third["amount"] < thresholds["uncategorized_review_threshold"]
    above, below = txns[planted["p2p.venmo_above"]], txns[planted["p2p.venmo_below"]]
    for row, flagged in ((above, True), (below, False)):
        assert (row["type"], row["category"], row["subcategory"], row["category_source"], row["needs_review"],
                row["transfer_group_id"]) == ("purchase", *p2p, "fallback", flagged, None)
        assert "VENMO" in row["description"]
    assert thresholds["venmo_review_threshold"] <= -above["amount"] < thresholds["uncategorized_review_threshold"]
    assert 0 < -below["amount"] < thresholds["venmo_review_threshold"]


# ------------------------------------------------------------------ pending and future rows

def test_pending_rows_are_in_the_feed_and_not_in_the_exports(demo_home, demo_exports, txns):
    planted, figures = demo_exports.truth["planted"], demo_exports.truth["figures"]
    card = snapshot_account(last_snapshot(demo_home.path), hh.CARD_SAM)
    pending = [t for t in card["transactions"] if t["pending"]]
    assert sorted(t["id"] for t in pending) == sorted(planted["pending.rows"]) and len(pending) == 3
    end = date.fromisoformat(demo_exports.truth["end"])
    days = {datetime.fromtimestamp(t["transacted_at"], ZoneInfo(hh.TIMEZONE)).date().isoformat() for t in pending}
    assert days <= {(end - timedelta(days=k)).isoformat() for k in (1, 2)}
    stored = {(t["date"], t["description"]) for t in txns.values()}
    for t in pending:
        assert not any(d in days and desc == t["description"] for d, desc in stored), t["description"]
    want = figures["pending"]
    assert list(want) == [hh.CARD_SAM] and want[hh.CARD_SAM]["rows"] == 3
    assert want[hh.CARD_SAM]["cents"] == sum(cents(float(t["amount"])) for t in pending) < 0
    posted = plan_mod.build(profiles.FULL, end).ledger.balance(hh.CARD_SAM, end)
    assert cents(float(card["balance"])) - posted == want[hh.CARD_SAM]["cents"]
    # the engine skips, file by file, the pending rows truth says each snapshot lists
    assert demo_checks.pending(demo_exports.exports, demo_exports.truth) == []
    assert figures["pending_listed"][last_snapshot(demo_home.path).name] == 3


def test_the_future_dated_row_is_stored_with_its_own_date(demo_home, demo_exports, txns):
    planted = demo_exports.truth["planted"]
    row = txns[planted["future.uid"]]
    end = demo_exports.truth["end"]
    assert row["date"] == planted["future.date"] > end
    assert row["source_file"] == last_snapshot(demo_home.path).name and row["account"] == hh.CHECKING
    assert row["category_source"] == "vendor_rule" and row["type"] == "purchase"
    months = {m["month"]: m for m in load(demo_exports.exports, "cashflow_monthly.json")["months"]}
    month = row["date"][:7]
    others = [t for t in txns.values() if t["date"][:7] == month and t["type"] in ("purchase", "fee")
              and not t["is_parent"] and t["id"] != row["id"]]
    # the month's spending holds the row's amount, whatever else that month holds
    assert cents(months[month]["spend"]) == demo_exports.truth["figures"]["months"][month]["spend_cents"] == (
        -sum(cents(t["amount"]) for t in others) - cents(row["amount"]))
    balance = {a["id"]: a for a in load(demo_exports.exports, "accounts.json")["accounts"]}[hh.CHECKING]
    assert balance["latest_balance_as_of"] == end and balance["txn_data_end"] == row["date"]


# ------------------------------------------------------------------ the household as a whole

def test_every_spending_category_has_a_purchase(demo_home, txns):
    taxonomy = yaml.safe_load((demo_home.path / "config" / "category_rules.yaml").read_text("utf-8"))["taxonomy"]
    spending_categories = set(taxonomy) - NOT_SPENDING
    assert len(spending_categories) == 17
    bought = {t["category"] for t in txns.values() if t["type"] == "purchase" and t["amount"] < 0}
    assert sorted(spending_categories - bought) == []


def test_the_recurring_detector_lists_exactly_the_declared_bills(demo_exports):
    """The regular spending the behaviours declare (the two utilities and the mortgage payment among the
    subscriptions) and nothing else: no irregular stream, and none of the monthly internal rows (contributions,
    allowances), which are no spending."""
    items = load(demo_exports.exports, "recurring.json")["items"]
    bills = {(s.merchant.vendor, s.account, s.cadence, s.amount_mode) for s in spending.STREAMS if s.cadence}
    bills.add((balance_sheet.LENDER.vendor, hh.CHECKING, *balance_sheet.MORTGAGE_CADENCE))
    listed = {(i["vendor"], i["account_id"], i["cadence"], i["amount_mode"]): i for i in items}
    assert bills <= set(listed) and all(listed[b]["schedule"] and listed[b]["active"] for b in bills)
    assert sorted((v, a) for v, a, _, _ in listed) == [
        (r["vendor"], r["account"]) for r in demo_exports.truth["figures"]["recurring"]]
    vendors = {t["vendor"] for t in load(demo_exports.exports, "transactions.json")["transactions"]
               if t["type"] == "investment" or t["is_internal"]}
    assert not vendors & {v for v, _, _, _ in listed}


def test_the_mortgage_is_expected_on_the_first_business_day_after_the_end(demo_exports):
    """The lender is paid on the first business day of every month, so the next payment is expected on the first
    business day of the month after the end, not a day count after the last one; its monthly figure is its amount."""
    [item] = [i for i in load(demo_exports.exports, "recurring.json")["items"]
              if (i["vendor"], i["account_id"]) == (balance_sheet.LENDER.vendor, hh.CHECKING)]
    first = business_day_on_or_after(shift_months(DEMO_END, 1))
    assert item["cadence"] == "monthly" and item["next_expected"] == item["expected_dates"][0] == first.isoformat()
    assert item["monthly_equivalent"] == item["typical_amount"]


def test_cards_stay_within_reason(demo_home, demo_exports):
    snapshots = sorted((demo_home.path / "store" / "simplefin" / "snapshots").glob("accounts-*.json"))
    limit = demo_exports.truth["planted"]["card.limit_cents"]
    for path in snapshots:
        card = snapshot_account(path, hh.CARD_SAM)
        assert cents(float(card["extra"]["credit-limit"])) == limit, path.name
        assert -cents(float(card["balance"])) <= limit, path.name
        assert cents(float(card["available-balance"])) >= 0, path.name
    accounts = {a["id"]: a for a in load(demo_exports.exports, "accounts.json")["accounts"]}
    assert 5_000 <= accounts[hh.CHECKING]["latest_balance"] <= 60_000


def test_inserting_a_behaviour_leaves_every_other_rows_feed_id_and_uid(tmp_path):
    """A profile equal to `full` with one more behaviour run first: every row of `full` keeps its date, amount,
    descriptor, feed id and stored uid; the only new rows are the new behaviour's own."""
    def gift(plan: plan_mod.Plan) -> None:
        expect = Expect("purchase", "Cash & Misc", "Uncategorized", "fallback", "Gift Deposit")
        plan.post(Row(hh.SAVINGS, plan.start + timedelta(days=40), 5_013, "GIFT DEPOSIT", expect=expect))
        plan.post(Row(hh.SAVINGS, plan.feed_start + timedelta(days=100), 7_019, "GIFT DEPOSIT", expect=expect))
        plan.post(Row(hh.CHECKING, plan.end, -1_117, "CORNER KIOSK", expect=expect))

    def rows(profile: profiles.Profile, home: Path):
        plan = plan_mod.build(profile, DEMO_END)
        listed = {a: [(r.date, r.cents, r.desc, r.feed_id, r.pending) for r in plan.ledger.rows(a)]
                  for a in plan.ledger.accounts()}
        return listed, truth_mod.stored_rows(plan, sources.write(plan, home)).rows

    base, base_stored = rows(profiles.FULL, tmp_path / "base")
    more, more_stored = rows(replace(profiles.FULL, behaviours=(gift, *profiles.FULL.behaviours)), tmp_path / "more")
    for account, listed in base.items():
        assert [r for r in more[account] if "-gift-" not in (r[3] or "")] == listed, account
    assert sorted(r[2] for a in more for r in more[a] if "-gift-" in (r[3] or "")) == [
        "CORNER KIOSK", "GIFT DEPOSIT", "GIFT DEPOSIT"]
    assert set(base_stored) <= set(more_stored) and len(more_stored) == len(base_stored) + 3
    for uid, row in base_stored.items():
        other = more_stored[uid]
        assert (other.account, other.date, other.cents, other.desc, other.feed_id) == (
            row.account, row.date, row.cents, row.desc, row.feed_id), uid


def test_every_planted_handle_resolves(demo_home, demo_exports, txns):
    planted = demo_exports.truth["planted"]
    assert HANDLES <= set(planted)
    accounts = {a["id"] for a in load(demo_exports.exports, "accounts.json")["accounts"]}
    dates = sorted(t["date"] for t in txns.values())
    topics = {q["topic"] for q in load(demo_exports.exports, "open_questions.json")["questions"]}
    feed = [a for path in sorted((demo_home.path / "store" / "simplefin" / "snapshots").glob("*.json"))
            for a in json.loads(path.read_text("utf-8"))["accounts"]]
    pending = {t["id"] for a in feed for t in a.get("transactions", ()) if t["pending"]}
    feed_ids = {a["id"] for a in feed}
    snapshots = load(demo_exports.exports, "assets_timeseries.json")["snapshots"]
    # a hand-stated value is named by its id, which the snapshot rows of it carry
    stated = {r["manual_id"] for s in snapshots for r in s["rows"] if r["source"] == "stated"}
    symbols = {h["symbol"] for h in load(demo_exports.exports, "holdings.json")["holdings"]}
    # an amount names a figure the exports state: a margin loan (a Margin row) or a card's credit limit
    amounts = {-cents(r["value"]) for s in snapshots for r in s["rows"] if r["asset_class"] == "Margin"} | {
        cents(p["limit"]) for points in load(demo_exports.exports, "balances_timeseries.json")["series"].values()
        for p in points if p["limit"] is not None}
    vendors = {t["vendor"] for t in txns.values()}
    keys = {a["key"] for a in json.loads((demo_home.path / "state" / "family" / "answers.json").read_text("utf-8"))
            ["answers"]}

    def resolves(value: str | int) -> bool:
        if isinstance(value, int):
            return value in amounts
        if value in txns or value in accounts or value in topics or value in pending or value in feed_ids \
                or value in stated or value in symbols or value in vendors or value in keys:
            return True
        try:
            return dates[0] <= date.fromisoformat(value).isoformat() <= dates[-1]
        except ValueError:
            return False

    # the modules handle is the switches household.yaml states, which assumptions.json exports as they are
    assert planted["modules"] == load(demo_exports.exports, "assumptions.json")["modules"]
    for handle, value in planted.items():
        if handle == "modules":
            continue
        values = value if isinstance(value, list) else [value]
        assert values and all(isinstance(v, (str, int)) and resolves(v) for v in values), handle


# ------------------------------------------------------------------ the command

def test_the_command_writes_the_tree_and_refuses_bad_arguments(demo_home, tmp_path, capsys):
    out, end = tmp_path / "tree", demo_home.truth["end"]
    assert generate.main(["--profile", "full", "--end", end, "--out", str(out)]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[0] == f"FF_NOW={end}T18:00:00"
    assert sorted(p.name for p in out.iterdir()) == ["config", "imports", "state", "store", "truth.json"]
    # the family's answers and the asset it entered in the app, where the store keeps them; never the learned rules
    assert sorted(p.name for p in (out / "state").iterdir()) == ["family"]
    assert sorted(p.name for p in (out / "state" / "family").iterdir()) == ["answers.json", "manual_assets.json"]
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
    assert sorted(printed[1:]) == files and "truth.json" in files
    assert (out / "truth.json").read_bytes() == (demo_home.path / "truth.json").read_bytes()
    text = (out / "truth.json").read_text("utf-8")
    assert text.endswith("}\n") and text == json.dumps(json.loads(text), sort_keys=True, indent=2) + "\n"

    good = ["--profile", "full", "--end", end, "--out"]
    for args, says in ((["--profile", "nope", "--end", "2026-10-31", "--out", str(tmp_path / "a")], "nope"),
                       (["--profile", "full", "--end", "2026-13-01", "--out", str(tmp_path / "b")], "2026-13-01"),
                       (["--profile", "full", "--end", "20261031", "--out", str(tmp_path / "c")], "20261031"),
                       (["--profile", "full", "--end", "2026-W44-6", "--out", str(tmp_path / "d")], "2026-W44-6"),
                       ([*good, str(out)], str(out))):
        assert generate.main(args) == 2
        captured = capsys.readouterr()
        assert captured.out == "" and len(captured.err.splitlines()) == 1 and says in captured.err
    assert not [p for p in "abcd" if (tmp_path / p).exists()]

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""etl/enrich/vendor_registry.py: one row per vendor seen on spend rows -> vendors.json.
Synthetic rows only; the pipeline tests run on the demo household's config."""
from __future__ import annotations

import datetime as dt
import json

from conftest import demo_config, isolate, make_txn

from etl import exports
from etl.enrich import vendor_registry as vr
from etl.enrich.categorize import Categorizer
from etl.pipeline import Pipeline
from tools.demo import household as hh

VENDOR_RULES = {"patterns": [
    {"pattern": "COSTCO", "vendor": "Costco"},
    {"pattern": "UBER\\b", "vendor": "Uber", "category": "Transport", "subcategory": "Rideshare"},
    {"pattern": "ZZZ", "vendor": "Zzz Shop", "learn": False},
], "learning": {"exclude": ["Amazon", "venmo"]}}
DATA_END = "2026-09-30"


def tx(vendor, cents, date="2026-09-01", **over):
    return make_txn(desc=vendor.upper(), cents=cents, vendor=vendor, date=date,
                    account=hh.CARD_ALEX, **over)


def test_build_counts_spend_rows_only_and_nets_refunds_over_ttm():
    rows = [
        tx("Costco", -10000, "2026-09-01"), tx("Costco", 2500, "2026-09-02"),   # a refund nets
        tx("Costco", -99999, "2025-09-30"),      # exactly data_end - 365d: OUTSIDE (strict >)
        tx("Costco", -100, "2025-10-01"),        # first day inside the window
        tx("Costco", -5000, "2024-01-15"),       # older: counts in txns / first_seen only
        tx("Late Fee", -3500, type="fee"),       # fees are spend
        tx("Venmo", -4000, type="transfer", is_internal=True),   # internal: never
        tx("Acme Payroll", 500000, type="income"),                # income: never
        make_txn(desc="NO VENDOR", cents=-100, vendor=None),      # vendor-less: never
        tx("Uber", -1200, "2026-09-10"),
    ]
    out = {r["vendor"]: r for r in vr.build(rows, VENDOR_RULES, None, DATA_END)}
    assert set(out) == {"Costco", "Late Fee", "Uber"}
    c = out["Costco"]
    assert (c["txns"], c["spend_ttm_cents"], c["first_seen"], c["last_seen"]) == \
        (5, 10000 - 2500 + 100, "2024-01-15", "2026-09-02")
    assert (c["has_rule"], c["category"], c["subcategory"], c["category_source"], c["learn"]) == \
        (True, None, None, None, True)
    u = out["Uber"]
    assert (u["has_rule"], u["category"], u["subcategory"], u["category_source"]) == \
        (True, "Transport", "Rideshare", "vendor_rule")
    f = out["Late Fee"]
    assert (f["has_rule"], f["category_source"], f["txns"], f["spend_ttm_cents"]) == (False, None, 1, 3500)
    assert vr.ttm_start(DATA_END) == "2025-09-30"


def test_internal_flag_excludes_purchase_typed_rows():
    # production marks PURCHASE-typed rows is_internal (venmo funding, transfer legs, a brokerage's funding):
    # SPEND_TYPES alone does not keep them out, so the flag itself must
    rows = [tx("Venmo Funding", -4000, is_internal=True),                       # type stays "purchase"
            tx("Costco", -10000), tx("Costco", -2500, "2026-09-02", is_internal=True)]
    out = {r["vendor"]: r for r in vr.build(rows, VENDOR_RULES, None, DATA_END)}
    assert set(out) == {"Costco"}                                                # the all-internal vendor is absent
    assert (out["Costco"]["txns"], out["Costco"]["spend_ttm_cents"], out["Costco"]["last_seen"]) == \
        (1, 10000, "2026-09-01")                                                 # and an internal row never counts


def test_duplicate_vendor_patterns_report_the_category_the_categorizer_applies():
    # two patterns naming one vendor: the Categorizer's vendor_cat keeps only the entries that
    # carry a category (last of those wins), so a later category-less pattern must not blank it
    rules = {"patterns": [
        {"pattern": "AAA", "vendor": "Dup", "category": "Dining", "subcategory": "Coffee"},
        {"pattern": "BBB", "vendor": "Dup"},                                     # later, no category
        {"pattern": "CCC", "vendor": "Two"},
        {"pattern": "DDD", "vendor": "Two", "category": "Pets"},                 # category-carrying entry last
        {"pattern": "EEE", "vendor": "Two", "category": "Travel", "subcategory": "Air"},   # last wins
        {"pattern": "FFF", "vendor": "Bare"}, {"pattern": "GGG", "vendor": "Bare"},
    ]}
    out = {r["vendor"]: r for r in vr.build([tx("Dup", -100), tx("Two", -100), tx("Bare", -100)],
                                            rules, None, DATA_END)}
    assert (out["Dup"]["has_rule"], out["Dup"]["category"], out["Dup"]["subcategory"],
            out["Dup"]["category_source"]) == (True, "Dining", "Coffee", "vendor_rule")
    assert (out["Two"]["category"], out["Two"]["subcategory"]) == ("Travel", "Air")
    assert (out["Bare"]["has_rule"], out["Bare"]["category"], out["Bare"]["category_source"]) == (True, None, None)
    vendor_cat = Categorizer({}, {}, rules, {}, {}).vendor_cat       # the engine's own lookup
    for v in ("Dup", "Two"):
        assert (out[v]["category"], out[v]["subcategory"]) == (vendor_cat[v]["category"], vendor_cat[v].get("subcategory"))
    assert "Bare" not in vendor_cat


def test_learn_flag_comes_from_exclude_list_and_learn_false_casefolded():
    rows = [tx("Amazon", -100), tx("Venmo", -100), tx("Zzz Shop", -100), tx("Other", -100)]
    out = {r["vendor"]: r["learn"] for r in vr.build(rows, VENDOR_RULES, None, DATA_END)}
    assert out == {"Amazon": False, "Venmo": False, "Zzz Shop": False, "Other": True}
    assert vr.learning_excluded(VENDOR_RULES) == frozenset({"amazon", "venmo", "zzz shop"})
    assert vr.learning_excluded({"patterns": []}) == frozenset()


def test_learned_doc_supplies_category_when_no_explicit_rule():
    learned = {"rules": [{"vendor": "Other", "category": "Pets", "subcategory": None},
                         {"vendor": "Uber", "category": "Dining", "subcategory": None}]}   # explicit rule wins
    rows = [tx("Other", -100), tx("Uber", -100)]
    out = {r["vendor"]: r for r in vr.build(rows, VENDOR_RULES, learned, DATA_END)}
    assert (out["Other"]["category"], out["Other"]["category_source"]) == ("Pets", "learned")
    assert (out["Uber"]["category"], out["Uber"]["category_source"]) == ("Transport", "vendor_rule")


def test_sorted_by_ttm_spend_desc_then_vendor_and_handles_no_data_end():
    rows = [tx("B", -100), tx("A", -100), tx("C", -300), tx("Old", -900, "2020-01-01")]
    assert [r["vendor"] for r in vr.build(rows, VENDOR_RULES, None, DATA_END)] == ["C", "A", "B", "Old"]
    assert vr.build([], VENDOR_RULES, None, None) == []
    none = vr.build(rows, VENDOR_RULES, None, None)
    assert [r["vendor"] for r in none] == ["A", "B", "C", "Old"] and all(r["spend_ttm_cents"] == 0 for r in none)


def test_export_vendors_converts_cents_and_counts():
    rows = vr.build([tx("A", -1234), tx("A", -1)], VENDOR_RULES, None, DATA_END)
    doc = exports.export_vendors(rows)
    assert doc["count"] == 1
    assert doc["vendors"] == [{"vendor": "A", "has_rule": False, "category": None, "subcategory": None,
                               "category_source": None, "learn": True, "txns": 2, "spend_ttm": 12.35,
                               "first_seen": "2026-09-01", "last_seen": "2026-09-01"}]
    assert exports.SCHEMA_VERSION == 23


def test_export_ledger_writes_vendors_json(tmp_path):
    p = Pipeline(demo_config())               # a bare Pipeline: no balances, assets or windows
    p.txns = [tx("Costco", -10000), tx("Uber", -1200)]
    p.data_end = DATA_END                      # enrich() sets it in a real run; this pipeline skips enrich
    p._export_ledger(tmp_path)
    doc = json.loads((tmp_path / "vendors.json").read_text())
    assert doc["count"] == 2 and [v["vendor"] for v in doc["vendors"]] == ["Costco", "Uber"]
    assert [v["spend_ttm"] for v in doc["vendors"]] == [100.0, 12.0]     # the TTM window is anchored on p.data_end
    # the config's vendor rules reach the registry: the demo's Costco rule (from the starter list) names no
    # category, its Uber rule carries one
    assert [(v["has_rule"], v["category"], v["category_source"]) for v in doc["vendors"]] == \
        [(True, None, None), (True, "Transport", "vendor_rule")]
    assert (tmp_path / "quality_report.json").exists()                  # written after vendors.json, as before


def test_enrich_sets_data_end_once_for_recurring_and_vendors(tmp_path):
    p = Pipeline(isolate(demo_config(), tmp_path))            # never a developer's live scenarios/
    p.txns = [tx("Costco", -10000, "2026-09-01"), tx("Costco", -777, "2025-01-15"),   # older than the TTM window
              tx("Uber", -1200, "2026-09-30")]
    assert p.data_end is None
    p.enrich()
    assert p.data_end == "2026-09-30"                                    # newest transaction date
    p._export_ledger(tmp_path)
    by = {v["vendor"]: v for v in json.loads((tmp_path / "vendors.json").read_text())["vendors"]}
    assert (by["Costco"]["txns"], by["Costco"]["spend_ttm"]) == (2, 100.0)      # the 2025-01 row is outside
    assert by["Uber"]["spend_ttm"] == 12.0


def test_data_end_never_passes_the_ingest_day(tmp_path):
    """A row the bank dated ahead (a scheduled payment) is no day of data: data_end is the ingest day, as the
    dashboard's dataThrough is, so the trailing year ends on that day and the row dated ahead is outside it."""
    p = Pipeline(isolate(demo_config(), tmp_path))
    p.ingested_at = dt.datetime.fromisoformat("2026-10-31T18:00:00")
    p.txns = [tx("Costco", -10000, "2026-09-01"), tx("Costco", -500, "2025-11-01"),   # 364 days before the ingest day
              tx("Uber", -1200, "2026-11-04")]                                        # dated ahead
    p.enrich()
    assert p.data_end == "2026-10-31"
    p._export_ledger(tmp_path)
    by = {v["vendor"]: v for v in json.loads((tmp_path / "vendors.json").read_text())["vendors"]}
    assert by["Costco"]["spend_ttm"] == 105.0                       # both rows inside the year ending on the day
    assert (by["Uber"]["txns"], by["Uber"]["spend_ttm"]) == (1, 0.0)  # listed, but not in the trailing year yet
    # the control: an ingest on the day the row is dated counts it
    q = Pipeline(isolate(demo_config(), tmp_path / "later"))
    q.ingested_at = dt.datetime.fromisoformat("2026-11-04T18:00:00")
    q.txns = [tx("Uber", -1200, "2026-11-04")]
    q.enrich()
    assert q.data_end == "2026-11-04"


def test_vendor_registry_matches_learned_rules_casefolded():
    learned = {"rules": [{"vendor": "OTHER", "category": "Pets", "subcategory": None}]}
    out = {r["vendor"]: r for r in vr.build([tx("Other", -100)], VENDOR_RULES, learned, DATA_END)}
    assert (out["Other"]["category"], out["Other"]["category_source"]) == ("Pets", "learned")


def test_build_counts_a_split_purchase_once_from_its_parent_and_skips_the_parts():
    parent = tx("Costco", -30000, "2026-09-01")
    parent.is_parent = True
    parts = [tx("Costco", c, "2026-09-01", parent_id=parent.uid, split_index=i, category_source="split")
             for i, c in ((1, -21000), (2, -9000))]
    out = {r["vendor"]: r for r in vr.build([parent, *parts], VENDOR_RULES, None, DATA_END)}
    assert (out["Costco"]["txns"], out["Costco"]["spend_ttm_cents"]) == (1, 30000)


def test_a_split_vendor_is_excluded_from_learning_and_vendors_json_says_so():
    vr_split = {"patterns": [*VENDOR_RULES["patterns"], {"pattern": "SPLITCO", "vendor": "Split Co",
                "split": [{"pct": 70, "category": "Groceries"}, {"remainder": True, "category": "Shopping"}]}],
                "learning": VENDOR_RULES["learning"]}
    assert "split co" in vr.learning_excluded(vr_split) and "costco" not in vr.learning_excluded(vr_split)
    out = {r["vendor"]: r for r in vr.build([tx("Split Co", -100), tx("Costco", -100)], vr_split, None, DATA_END)}
    assert out["Split Co"]["learn"] is False and out["Costco"]["learn"] is True

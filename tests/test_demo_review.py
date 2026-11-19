# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The `full` demo household's review features against its truth: the recurring list (subscriptions among the bills),
a vendor whose purchases split into parts, review answers that teach the engine a vendor, an answer for every purchase
of a vendor, and an override that beats an answer. Truth declares each from what the household knows; these tests
show the engine agrees. Everything here is invented."""
from __future__ import annotations

import importlib.util
import json
from collections import defaultdict
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import demo_checks
import pytest
import yaml
from conftest import ROOT

from tools.demo import household as hh
from tools.demo import plan as plan_mod
from tools.demo.behaviours import subscriptions
from tools.demo.dates import shift_months

SUBSCRIPTIONS = ("monthly", "annual", "price_changed", "cancelled", "lapsed")
_spec = importlib.util.spec_from_file_location("scenario_store", ROOT / "deploy" / "scenario_store.py")
scenario_store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scenario_store)


def load(exports: Path, name: str) -> dict:
    return json.loads((exports / name).read_text("utf-8"))


def cents(dollars: float) -> int:
    return round(dollars * 100)


@pytest.fixture(scope="module")
def txns(demo_exports) -> dict[str, dict]:
    return {t["id"]: t for t in load(demo_exports.exports, "transactions.json")["transactions"]}


@pytest.fixture(scope="module")
def figures(demo_exports) -> dict:
    return demo_exports.truth["figures"]


@pytest.fixture(scope="module")
def planted(demo_exports) -> dict:
    return demo_exports.truth["planted"]


@pytest.fixture(scope="module")
def checks(demo_exports) -> dict[str, dict]:
    return {c["name"]: c for c in load(demo_exports.exports, "quality_report.json")["checks"]}


@pytest.fixture(scope="module")
def recurring(demo_exports) -> dict[tuple[str, str], dict]:
    return {(i["vendor"], i["account_id"]): i for i in load(demo_exports.exports, "recurring.json")["items"]}


@pytest.fixture(scope="module")
def vendors(demo_exports) -> dict[str, dict]:
    return {v["vendor"]: v for v in load(demo_exports.exports, "vendors.json")["vendors"]}


@pytest.fixture(scope="module")
def learned(demo_exports) -> dict[str, dict]:
    """The learned rules the ingest wrote into the ingested tree's state/engine/ (the generator writes none)."""
    path = demo_exports.exports.parents[1] / "state" / "engine" / "learned_rules.json"
    return {r["vendor"]: r for r in json.loads(path.read_text("utf-8"))["rules"]}


@pytest.fixture(scope="module")
def answers(demo_home) -> dict[str, dict]:
    doc = json.loads((demo_home.path / "state" / "family" / "answers.json").read_text("utf-8"))
    return {a["key"]: a for a in doc["answers"]}


def item_of(recurring: dict, vendor: str) -> dict:
    [item] = [i for (v, _), i in recurring.items() if v == vendor]
    return item


# ------------------------------------------------------------------ subscriptions and the recurring list

def test_recurring_items_are_exactly_the_declared_ones(demo_exports, recurring, figures, planted):
    assert demo_checks.recurring(demo_exports.exports, demo_exports.truth) == []
    # the five subscriptions, the two utility bills and the mortgage payment, and nothing else
    vendors = [r["vendor"] for r in figures["recurring"]]
    assert sorted(planted[f"subscriptions.{s}"] for s in SUBSCRIPTIONS) == sorted(
        v for v in vendors if v not in ("Xfinity", "Verizon Wireless", "Example Home Loans"))
    assert len(vendors) == 8
    for s in SUBSCRIPTIONS:
        item = item_of(recurring, planted[f"subscriptions.{s}"])
        assert (item["category"], item["amount_mode"]) == ("Subscriptions", "fixed"), s
    annual = item_of(recurring, planted["subscriptions.annual"])
    assert (annual["cadence"], annual["account_id"], annual["active"]) == ("annual", hh.CHECKING, True)
    assert (date.fromisoformat(annual["last_seen"]) - date.fromisoformat(annual["first_seen"])).days >= 2 * 350


def test_the_price_change_names_both_prices(recurring, txns, planted):
    vendor = planted["subscriptions.price_changed"]
    item = item_of(recurring, vendor)
    [change] = item["price_changes"]
    old, new = subscriptions.PRICE_CHANGED.cents, subscriptions.PRICE_CHANGED.raised_cents
    assert (cents(change["from"]), cents(change["to"])) == (old, new) and (new - old) * 100 > old
    rows = sorted((t for t in txns.values() if t["vendor"] == vendor and t["account"] == item["account_id"]),
                  key=lambda t: t["date"])
    raised = [t for t in rows if cents(t["amount"]) == -new]
    assert change["date"] == raised[0]["date"] and len(raised) >= 3
    assert all(cents(t["amount"]) == -old for t in rows if t["date"] < change["date"])
    assert (item["account_id"], item["cadence"], item["active"]) == (hh.CARD_SAM, "monthly", True)
    assert cents(item["last_amount"]) == new


def test_the_cancelled_and_the_lapsed_subscription_are_listed_and_not_active(demo_exports, recurring, planted):
    end = date.fromisoformat(demo_exports.truth["end"])
    assumptions = yaml.safe_load((demo_exports.exports.parents[1] / "config" / "assumptions.yaml").read_text("utf-8"))
    cancelled = item_of(recurring, planted["subscriptions.cancelled"])
    lapsed = item_of(recurring, planted["subscriptions.lapsed"])
    assert [c["vendor"] for c in assumptions["cancelled_subscriptions"]] == [cancelled["vendor"]]
    for item in (cancelled, lapsed):
        assert (item["cadence"], item["active"], item["schedule"]) == ("monthly", False, False), item["vendor"]
    # the cancelled one is still being charged; the lapsed one stopped more than four months before the end
    assert (end - date.fromisoformat(cancelled["last_seen"])).days <= 31
    assert date.fromisoformat(lapsed["last_seen"]) < shift_months(end, -4)
    assert lapsed["vendor"] not in [c["vendor"] for c in assumptions["cancelled_subscriptions"]]


# ------------------------------------------------------------------ the split vendor

def test_split_purchases_count_once(demo_exports, txns, figures, planted, checks):
    assert demo_checks.split(demo_exports.exports, demo_exports.truth) == []
    parents = {u: t for u, t in txns.items() if t["is_parent"]}
    assert set(parents) == {u for u, r in figures["rows"].items() if r["is_parent"]}
    assert planted["split.purchase"] in parents and len(parents) == figures["splits"]["purchases"]
    parts: dict[str, list[dict]] = defaultdict(list)
    for t in txns.values():
        if t["parent_id"] is not None:
            parts[t["parent_id"]].append(t)
    assert set(parts) == set(parents)
    for uid, parent in parents.items():
        mine = parts[uid]
        assert len(mine) == len(figures["splits"]["legs"]) and sum(cents(t["amount"]) for t in mine) == cents(
            parent["amount"]), uid
        assert all(t["account"] == parent["account"] and t["date"] == parent["date"] for t in mine), uid
    # no money total reads a parent: each month's spending is its money rows' alone
    months = {m["month"]: m for m in load(demo_exports.exports, "cashflow_monthly.json")["months"]}
    for month in {t["date"][:7] for t in parents.values()}:
        money = [t for t in txns.values() if t["date"][:7] == month and t["type"] in ("purchase", "fee")
                 and not t["is_parent"]]
        assert cents(months[month]["spend"]) == -sum(cents(t["amount"]) for t in money), month
    # the manifest counts money rows (parts and ordinary rows), as truth's accounts do; the transactions list both
    manifest = load(demo_exports.exports, "manifest.json")
    money_rows = sum(a["rows"] for a in figures["accounts"].values())
    assert manifest["transaction_count"] == money_rows == len(txns) - len(parents)
    assert load(demo_exports.exports, "transactions.json")["count"] == len(txns)
    assert checks["splits.sum_identity"]["status"] == "pass"
    assert checks["splits.applied"]["status"] == "info"


def test_the_split_vendor_never_learns(demo_exports, txns, figures, planted, vendors, learned, checks):
    vendor = planted["split.vendor"]
    split = figures["splits"]
    assert split["vendor"] == vendor
    doc = yaml.safe_load((demo_exports.exports.parents[1] / "config" / "vendor_rules.yaml").read_text("utf-8"))
    [entry] = [e for e in doc["patterns"] if e.get("split")]
    assert entry["vendor"] == vendor and entry.get("note")
    # no export states the rule's legs: what the engine made of them is each purchase's parts — one per leg, under
    # its category, the percent leg that share of the purchase and the remainder what is left — and the rule's
    # line in the quality report
    assert demo_checks.split(demo_exports.exports, demo_exports.truth) == []
    row = vendors[vendor]
    assert (row["learn"], row["txns"]) == (False, split["purchases"])
    assert vendor not in learned and vendor not in checks["learned_rules.active"]["detail"]
    for t in txns.values():
        if t["vendor"] == vendor:
            assert t["needs_review"] is False and (t["category_source"] == "split") == (t["parent_id"] is not None)


# ------------------------------------------------------------------ answers and learned rules

def test_three_answers_teach_the_vendor_and_the_rest_follow(demo_exports, txns, figures, planted, vendors, learned,
                                                            answers, checks):
    assert demo_checks.answers(demo_exports.exports, demo_exports.truth) == []
    assert demo_checks.vendors(demo_exports.exports, demo_exports.truth) == []
    vendor, answered = planted["learned.vendor"], planted["learned.answered"]
    rows = [t for t in txns.values() if t["vendor"] == vendor]
    assert len(rows) >= 5 and len(answered) == 3 and planted["learned.unanswered"] not in answered
    for t in rows:
        assert (t["account"], t["category"], t["subcategory"], t["needs_review"]) == (
            hh.CHECKING, "Housing", "Home Improvement", False), t["id"]
        assert t["category_source"] == ("answer" if t["id"] in answered else "learned"), t["id"]
    assert txns[planted["learned.unanswered"]]["category_source"] == "learned"
    assert all(answers[f"txn:{uid}"]["set"] == {"category": "Housing", "subcategory": "Home Improvement"}
               for uid in answered)
    # the rules the ingest learned are exactly truth's, this one from three row answers
    assert sorted(({"vendor": r["vendor"], "category": r["category"], "subcategory": r["subcategory"],
                    "scope": r["evidence"]["scope"]} for r in learned.values()), key=lambda r: r["vendor"]) == \
        figures["learned"]
    rule = learned[vendor]
    assert (rule["evidence"]["scope"], rule["evidence"]["n_agree"]) == ("txn", 3)
    assert rule["evidence"]["answer_keys"] == sorted(f"txn:{uid}" for uid in answered)
    want = figures["vendors"][vendor]
    assert {k: vendors[vendor][k] for k in want} == want == {
        "category": "Housing", "subcategory": "Home Improvement", "category_source": "learned"}
    assert checks["learned_rules.active"]["status"] == "info" and vendor in checks["learned_rules.active"]["detail"]


def test_a_vendor_answer_files_every_purchase_of_the_vendor(txns, figures, planted, vendors, learned, answers):
    vendor = planted["vendor_answer.vendor"]
    rows = [t for t in txns.values() if t["vendor"] == vendor]
    assert len(rows) >= 3
    for t in rows:
        assert (t["type"], t["category"], t["subcategory"], t["category_source"], t["needs_review"]) == (
            "purchase", "Health", "Medical", "answer", False), t["id"]
    answer = answers[f"vendor:{vendor}"]
    assert (answer["scope"], answer["set"]) == ("vendor", {"category": "Health", "subcategory": "Medical"})
    assert learned[vendor]["evidence"]["scope"] == "vendor"
    want = figures["vendors"][vendor]
    assert {k: vendors[vendor][k] for k in want} == want == {
        "category": "Health", "subcategory": "Medical", "category_source": "learned"}


def test_the_override_wins_and_the_conflict_is_named(demo_exports, txns, figures, planted, answers, checks):
    row = txns[planted["conflict.row"]]
    assert (row["category"], row["subcategory"], row["category_source"], row["needs_review"]) == (
        "Giving", "Charity", "override", False)
    key = planted["conflict.key"]
    assert key == f"txn:{row['id']}" and answers[key]["set"] == {"category": "Entertainment", "subcategory": "Events"}
    assert figures["answers"]["conflicts"] == [key]
    assert demo_checks.answers(demo_exports.exports, demo_exports.truth) == []
    assert checks["answers.applied"]["detail"].startswith(
        f"{figures['answers']['applied_rows']} transactions adjusted by {len(answers)} review-inbox answers")
    overrides = yaml.safe_load((demo_exports.exports.parents[1] / "config" / "overrides.yaml").read_text("utf-8"))
    [entry] = overrides
    match = entry["match"]
    assert match["contains"] in row["description"] and (str(match["date"]), match["account"]) == (
        row["date"], row["account"])
    assert entry["set"] == {"category": "Giving", "subcategory": "Charity"} and entry.get("note")
    # the override reached that one row
    assert [t["id"] for t in txns.values() if t["category_source"] == "override"] == [row["id"]]


def test_the_answers_file_is_what_the_store_accepts(demo_home, txns):
    truth = demo_home.truth
    path = demo_home.path / "state" / "family" / "answers.json"
    doc = json.loads(path.read_text("utf-8"))
    assert scenario_store.validate_answers(path.read_bytes()) == doc
    # the household ingests the end day's data at the plan's ingest hour, in its own zone
    ingest = datetime.combine(date.fromisoformat(truth["end"]), time(plan_mod.INGEST_HOUR),
                              tzinfo=ZoneInfo(hh.TIMEZONE)).astimezone(UTC)
    stamps = []
    for a in doc["answers"]:
        at = datetime.fromisoformat(a["answered_at"].replace("Z", "+00:00"))
        assert a["answered_at"].endswith("Z") and at < ingest, a["key"]
        if a["scope"] == "txn":
            # answered on a later day than the row's, in the household's zone
            assert at.astimezone(ZoneInfo(hh.TIMEZONE)).date().isoformat() > txns[a["txn_id"]]["date"], a["key"]
        stamps.append(a["answered_at"])
    assert stamps == sorted(stamps) and doc["updated_at"] == stamps[-1]
    assert len({a["key"] for a in doc["answers"]}) == len(doc["answers"]) == 5

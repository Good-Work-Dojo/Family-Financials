# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""deploy/export_diff.py — the gate's comparison module, on synthetic export dirs.
One passing and one failing fixture per policy, the fetched-at precheck, the CLI exit
codes and the aggregate-only output contract. No real data anywhere in here."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import export_diff as ed  # tests/conftest.py puts deploy/ on sys.path
import pytest

KNOWN = frozenset({"Known Co"})   # stands in for vendor_rules.yaml patterns[].vendor


def row(i: int, **over) -> dict:
    r = {"id": f"tx_{i:04d}", "date": "2026-01-15", "account": "chk", "account_name": "Chk",
         "owner": "Joint", "description": f"MERCHANT {i}", "vendor": f"Vendor {i}",
         "category": "Dining", "subcategory": "Restaurants", "amount": -10.0,
         "type": "purchase", "is_internal": False, "transfer_group_id": None,
         "tags": [], "needs_review": False, "earner": None, "source_file": "s.csv",
         "category_source": "keyword"}
    r.update(over)
    return r


def docs() -> dict[str, dict]:
    rows = [row(0), row(1), row(2), row(3, vendor="Known Co")]
    return {
        "manifest.json": {
            "generated_at": "2026-10-02T10:00:00-05:00",
            "simplefin_fetched_at": "2026-10-02T15:00:00Z",
            "prices_fetched_at": "2026-10-02T15:00:10Z",
            "broker_fetched_at": "2026-10-02T15:00:20Z",
            "transaction_count": 4, "current_snapshot_date": "2026-10-02",
            "files": ["accounts.json", "cashflow_monthly.json", "manifest.json",
                      "quality_report.json", "recurring.json", "transactions.json"]},
        "transactions.json": {"count": 4, "transactions": rows},
        "recurring.json": {"items": [{"vendor": "Vendor 1", "account_id": "chk",
                                      "category": "Dining", "cadence": "monthly"}]},
        "cashflow_monthly.json": {"months": [{
            "month": "2026-01", "spend": 40.0,
            "by_category": {"Dining": {"total": 40.0, "by_subcategory": {"Restaurants": 40.0}}}}]},
        "quality_report.json": {
            "checks": [{"name": "x", "status": "pass", "detail": "ok"}],
            "top_unmapped_vendors": [{"vendor": "Vendor 2", "spend": 10.0, "txns": 1}]},
        "accounts.json": {"accounts": [{"id": "chk", "name": "Chk"}]},
    }


def write_dir(root: Path, name: str, d: dict[str, dict]) -> Path:
    p = root / name
    p.mkdir()
    for fname, obj in d.items():
        (p / fname).write_text(json.dumps(obj))
    return p


def compare(tmp_path: Path, base: dict, head: dict, policy: str, **kw) -> ed.Report:
    return ed.compare(write_dir(tmp_path, "base", base), write_dir(tmp_path, "head", head),
                      policy, **kw)


def tx(d: dict, i: int) -> dict:
    return d["transactions.json"]["transactions"][i]


# ---------------------------------------------------------------- identical

def test_identical_passes_on_equal_dirs_even_when_generated_at_moves(tmp_path):
    base, head = docs(), docs()
    head["manifest.json"]["generated_at"] = "2026-10-02T11:00:00-05:00"
    rep = compare(tmp_path, base, head, "identical")
    assert rep.ok and rep.violations == []


def test_identical_fails_on_any_value_change(tmp_path):
    base, head = docs(), docs()
    tx(head, 0)["amount"] = -11.0
    rep = compare(tmp_path, base, head, "identical")
    assert not rep.ok
    assert any("transactions.json" in v and "amount" in v for v in rep.violations)


# ----------------------------------------------------------- schema-additive

def test_schema_additive_allows_new_keys_files_and_manifest_entries(tmp_path):
    base, head = docs(), docs()
    for r in head["transactions.json"]["transactions"]:
        r["parent_id"] = None                                  # new key on every row
    head["manifest.json"]["schema_version"] = 2               # new top-level key
    head["vendors.json"] = {"count": 0, "vendors": []}        # new file ...
    head["manifest.json"]["files"] = sorted(head["manifest.json"]["files"] + ["vendors.json"])
    rep = compare(tmp_path, base, head, "schema-additive")
    assert rep.ok, rep.violations


@pytest.mark.parametrize("mutate", [
    lambda h: tx(h, 0).__setitem__("category", "Groceries"),            # changed value
    lambda h: tx(h, 0).pop("earner"),                                    # removed key
    lambda h: h["transactions.json"]["transactions"].pop(),              # removed row
    lambda h: h["transactions.json"]["transactions"].append(row(9)),     # a NEW row is data, not schema
    lambda h: h.pop("recurring.json"),                                   # removed file
    lambda h: h["manifest.json"].__setitem__("files", ["manifest.json"]),   # files shrank
])
def test_schema_additive_rejects_non_additive_changes(tmp_path, mutate):
    base, head = docs(), docs()
    mutate(head)
    rep = compare(tmp_path, base, head, "schema-additive")
    assert not rep.ok


# ------------------------------------------------------- fetched-at precheck

def test_fetched_at_mismatch_is_reported_before_any_policy_judgement(tmp_path):
    base, head = docs(), docs()
    head["manifest.json"]["simplefin_fetched_at"] = "2026-10-03T01:00:00Z"
    rep = compare(tmp_path, base, head, "identical")
    assert rep.inputs_changed == ["simplefin_fetched_at"] and not rep.ok
    assert ed.check_fetched_at(base["manifest.json"], head["manifest.json"]) == ["simplefin_fetched_at"]
    b, h = write_dir(tmp_path, "b2", base), write_dir(tmp_path, "h2", head)
    assert ed.main([str(b), str(h), "--expect", "identical"]) == ed.EXIT_INPUTS_CHANGED
    # skipping the precheck still reports the stamp as an ordinary changed value
    assert ed.main([str(b), str(h), "--expect", "identical", "--skip-fetched-at"]) == ed.EXIT_VIOLATION


# ----------------------------------------------------- vendors-fallback-only

def test_vendors_fallback_only_passes_when_only_fallback_vendor_strings_move(tmp_path):
    base, head = docs(), docs()
    tx(head, 0)["vendor"] = "VENDOR 0"                          # fallback row, re-cased: allowed
    head["recurring.json"]["items"][0]["vendor"] = "Vendor One"
    head["quality_report.json"]["top_unmapped_vendors"][0]["vendor"] = "Vendor Two"
    head["vendors.json"] = {"count": 1, "vendors": [{"vendor": "VENDOR 0", "txns": 1}]}
    head["manifest.json"]["files"] = sorted(head["manifest.json"]["files"] + ["vendors.json"])
    rep = compare(tmp_path, base, head, "vendors-fallback-only", known_vendors=KNOWN)
    assert rep.ok, rep.violations


@pytest.mark.parametrize("mutate", [
    lambda h: tx(h, 3).__setitem__("vendor", "Known Company"),      # a patterns hit drifted
    lambda h: tx(h, 0).__setitem__("category", "Groceries"),        # categories stay identical
    lambda h: h["recurring.json"]["items"][0].__setitem__("category", "Groceries"),
    lambda h: h["quality_report.json"]["checks"][0].__setitem__("detail", "changed"),
])
def test_vendors_fallback_only_rejects_everything_else(tmp_path, mutate):
    base, head = docs(), docs()
    mutate(head)
    rep = compare(tmp_path, base, head, "vendors-fallback-only", known_vendors=KNOWN)
    assert not rep.ok


# ---------------------------------------------------------- categories-only

def test_categories_only_passes_and_builds_the_matrix(tmp_path):
    base, head = docs(), docs()
    tx(head, 0).update(category="Groceries", subcategory=None, category_source="learned")
    tx(head, 1).update(subcategory="Coffee", category_source="learned")
    head["cashflow_monthly.json"]["months"][0]["by_category"] = {
        "Dining": {"total": 30.0, "by_subcategory": {"Restaurants": 20.0, "Coffee": 10.0}},
        "Groceries": {"total": 10.0, "by_subcategory": {"-": 10.0}}}
    head["recurring.json"]["items"][0]["category"] = "Groceries"
    head["quality_report.json"]["checks"].append(
        {"name": "learned_rules.active", "status": "info", "detail": "1"})
    rep = compare(tmp_path, base, head, "categories-only")
    assert rep.ok, rep.violations
    assert rep.matrix[("Dining/Restaurants", "Groceries/-")] == 1
    assert rep.matrix[("Dining/Restaurants", "Dining/Coffee")] == 1


@pytest.mark.parametrize("mutate", [
    lambda h: tx(h, 0).update(category="Groceries"),                              # source still "keyword"
    lambda h: tx(h, 0).update(category="Groceries", category_source="answer"),    # not learned
    lambda h: tx(h, 0).__setitem__("vendor", "Other"),                            # only category fields may move
    lambda h: h["cashflow_monthly.json"]["months"][0].__setitem__("spend", 41.0),  # totals are not a consequence
    lambda h: h["transactions.json"].__setitem__("count", 5),
])
def test_categories_only_rejects_non_category_changes(tmp_path, mutate):
    base, head = docs(), docs()
    mutate(head)
    rep = compare(tmp_path, base, head, "categories-only")
    assert not rep.ok


# ------------------------------------------------------------------- splits

def test_splits_passes_with_children_and_parent_flags(tmp_path):
    base, head = docs(), docs()
    for r in head["transactions.json"]["transactions"]:
        r["parent_id"], r["is_parent"] = None, False
    tx(head, 0)["is_parent"] = True
    head["transactions.json"]["transactions"] += [
        row(100, parent_id="tx_0000", is_parent=False, amount=-7.0, category="Groceries",
            category_source="split"),
        row(101, parent_id="tx_0000", is_parent=False, amount=-3.0, category="Shopping",
            category_source="split"),
    ]
    head["transactions.json"]["count"] = 6
    head["manifest.json"]["transaction_count"] = 5
    head["cashflow_monthly.json"]["months"][0]["by_category"]["Groceries"] = {
        "total": 7.0, "by_subcategory": {"-": 7.0}}
    head["quality_report.json"]["checks"].append(
        {"name": "splits.sum_identity", "status": "pass", "detail": "ok"})
    rep = compare(tmp_path, base, head, "splits")
    assert rep.ok, rep.violations


@pytest.mark.parametrize("mutate", [
    lambda h: h["transactions.json"]["transactions"].append(row(100)),                   # no parent_id key
    lambda h: h["transactions.json"]["transactions"].append(row(100, parent_id=None)),   # parent_id null
    lambda h: h["transactions.json"]["transactions"].pop(),                              # a base row vanished
    lambda h: tx(h, 1).__setitem__("amount", -5.0),                                      # non-split row edited
])
def test_splits_rejects_unparented_rows_and_edits(tmp_path, mutate):
    base, head = docs(), docs()
    mutate(head)
    rep = compare(tmp_path, base, head, "splits")
    assert not rep.ok

def splits_head(base: dict) -> dict:
    """the shape a rule-driven split produces: every base row gains the two keys, tx_0000 becomes the parent of two
    children that sum to it; count / transaction_count move"""
    head = copy.deepcopy(base)
    for r in head["transactions.json"]["transactions"]:
        r["parent_id"], r["is_parent"] = None, False
    tx(head, 0)["is_parent"] = True
    head["transactions.json"]["transactions"] += [
        row(100, parent_id="tx_0000", is_parent=False, amount=-7.0, category="Groceries", category_source="split"),
        row(101, parent_id="tx_0000", is_parent=False, amount=-3.0, category="Shopping", category_source="split"),
    ]
    head["transactions.json"]["count"] = 6
    head["manifest.json"]["transaction_count"] = 5
    return head


def test_splits_passes_the_shape_d8_produces_and_names_the_children_per_row(tmp_path):
    base = docs()
    base["manifest.json"]["schema_version"] = 5
    head = splits_head(base)
    head["manifest.json"]["schema_version"] = 6
    rep = compare(tmp_path, base, head, "splits")
    assert rep.ok, rep.violations
    out = ed.render(rep)
    # the children are keyed by row id, so the per-file counts name them one by one
    assert "added tx_0100 x1" in out and "added tx_0101 x1" in out and "added transactions" not in out
    assert "manifest.json: schema_version 5->6" in out
    assert "accounts.json: identical" in out and "RESULT: OK (splits)" in out


def test_splits_lets_a_parent_clear_needs_review(tmp_path):
    base = docs()
    tx(base, 0)["needs_review"] = True                      # the parent was in the inbox before the split
    head = splits_head(base)
    tx(head, 0)["needs_review"] = False
    rep = compare(tmp_path, base, head, "splits")
    assert rep.ok, rep.violations
    assert "changed needs_review x1" in ed.render(rep)


def test_splits_rejects_clearing_needs_review_on_a_row_that_is_not_a_parent(tmp_path):
    # compare() writes base/ and head/ under the root it is given, so each further comparison
    # gets its own existing directory
    base = docs()
    tx(base, 1)["needs_review"] = True
    other = splits_head(base)
    tx(other, 1)["needs_review"] = False
    rep = compare(tmp_path, base, other, "splits")
    assert not rep.ok and any("needs_review" in v for v in rep.violations)


def test_splits_rejects_a_parent_that_sets_needs_review(tmp_path):
    # the one allowed move is True -> False: a parent must not enter the inbox
    head = splits_head(docs())
    tx(head, 0)["needs_review"] = True
    rep = compare(tmp_path, docs(), head, "splits")
    assert not rep.ok and any("needs_review" in v for v in rep.violations)


def test_splits_rejects_a_non_parent_row_setting_needs_review(tmp_path):
    head = splits_head(docs())
    tx(head, 1)["needs_review"] = True
    rep = compare(tmp_path, docs(), head, "splits")
    assert not rep.ok and any("needs_review" in v for v in rep.violations)


PARENT_EDITS = {
    "category": "Groceries",            # display-only, but a split keeps the parent's own category
    "subcategory": "Supermarket",
    "category_source": "split",
    "amount": -9.0,
    "vendor": "Other",
    "tags": ["split"],
    "type": "transfer",
}


@pytest.mark.parametrize("field,value", PARENT_EDITS.items(), ids=PARENT_EDITS.keys())
def test_splits_rejects_a_parent_that_changes_anything_but_needs_review(tmp_path, field, value):
    base = docs()
    tx(base, 0)["needs_review"] = True
    head = splits_head(base)
    tx(head, 0)["needs_review"] = False                     # the allowed move rides along with the forbidden one
    tx(head, 0)[field] = value
    rep = compare(tmp_path, base, head, "splits")
    assert not rep.ok
    assert any(f"transactions/tx_0000/{field}" in v for v in rep.violations), rep.violations


def last_child(h: dict) -> dict:
    return h["transactions.json"]["transactions"][-1]


def extra_child(h: dict, parent_id: str) -> None:
    """a zero-amount third child: the parent's own two children still balance, so the only thing
    wrong with the head is where this one points"""
    h["transactions.json"]["transactions"].append(
        row(102, parent_id=parent_id, is_parent=False, amount=0.0, category="Shopping", category_source="split"))
    h["transactions.json"]["count"] = 7


def lone_child(h: dict) -> None:
    """one child carrying the whole amount: balanced, but not a split"""
    kids = h["transactions.json"]["transactions"]
    kids.pop()
    kids[-1]["amount"] = -10.0
    h["transactions.json"]["count"] = 5


NO_CENTS = "has no finite whole-cent amount"


# name -> (mutation of a valid split head, the reason the report must give)
MALFORMED = {
    "orphan": (lambda h: extra_child(h, "tx_9999"), "split child tx_0102 names no head row flagged is_parent ('tx_9999')"),
    "child-of-a-non-parent": (lambda h: extra_child(h, "tx_0001"),
                              "split child tx_0102 names no head row flagged is_parent ('tx_0001')"),
    "single-child": (lone_child, "split parent tx_0000 has 1 child row(s); a split needs at least 2"),
    "children-do-not-sum": (lambda h: last_child(h).__setitem__("amount", -2.0), "tx_0000: its children do not sum"),
    "child-flagged-parent": (lambda h: last_child(h).__setitem__("is_parent", True),
                             "split row tx_0101 is both a child and a parent"),
    "flagged-parent-without-children": (lambda h: tx(h, 2).__setitem__("is_parent", True),
                                        "split parent tx_0002 has 0 child row(s)"),
    "child-names-itself": (lambda h: last_child(h).__setitem__("parent_id", "tx_0101"),
                           "split child tx_0101 names no head row flagged is_parent ('tx_0101')"),
    "child-amount-missing": (lambda h: last_child(h).pop("amount"), NO_CENTS),
    "child-amount-nan": (lambda h: last_child(h).__setitem__("amount", float("nan")), NO_CENTS),
    "child-amount-infinite": (lambda h: last_child(h).__setitem__("amount", float("inf")), NO_CENTS),
    "child-amount-is-a-string": (lambda h: last_child(h).__setitem__("amount", "-3.0"), NO_CENTS),
    "child-amount-is-a-bool": (lambda h: last_child(h).__setitem__("amount", True), NO_CENTS),
    "child-amount-has-a-sub-cent": (lambda h: last_child(h).__setitem__("amount", -3.004), NO_CENTS),
    "child-amount-is-astronomical": (lambda h: last_child(h).__setitem__("amount", 10**400),
                                     "tx_0000: its children do not sum"),
    "child-is-parent-false-as-zero": (lambda h: last_child(h).__setitem__("is_parent", 0),
                                      "split child tx_0101 has a non-boolean is_parent"),
    "child-is-parent-null": (lambda h: last_child(h).__setitem__("is_parent", None),
                             "split child tx_0101 has a non-boolean is_parent"),
    "child-is-parent-a-string": (lambda h: last_child(h).__setitem__("is_parent", "false"),
                                 "split child tx_0101 has a non-boolean is_parent"),
    "parent-amount-moved-with-the-children": (lambda h: tx(h, 0).__setitem__("amount", -11.0),
                                              "tx_0000: its children do not sum"),
}


@pytest.mark.parametrize("mutate,reason", MALFORMED.values(), ids=MALFORMED.keys())
def test_splits_rejects_a_malformed_split(tmp_path, mutate, reason):
    head = splits_head(docs())
    mutate(head)
    rep = compare(tmp_path, docs(), head, "splits")
    assert not rep.ok
    assert any(reason in v for v in rep.violations), rep.violations


def test_splits_mutations_start_from_a_head_that_passes(tmp_path):
    # the fixtures above only prove something if the unmutated shape is accepted
    assert compare(tmp_path, docs(), splits_head(docs()), "splits").ok


def test_splits_rejects_an_existing_row_that_becomes_a_child(tmp_path):
    # base row tx_0001 (-10) joins tx_0000's children (-7, -3) and the parent is -20 on both sides,
    # so the sums balance: a head that is arithmetically consistent must still not re-parent a row
    # the base already had as an ordinary one
    base = docs()
    tx(base, 0)["amount"] = -20.0
    head = splits_head(base)
    tx(head, 1)["parent_id"] = "tx_0000"
    rep = compare(tmp_path, base, head, "splits")
    assert not rep.ok
    assert any("transactions/tx_0001/parent_id" in v for v in rep.violations), rep.violations


@pytest.mark.parametrize("key,value,also", [
    ("parent_id", "tx_0002", ["split child tx_0003 names no head row flagged is_parent ('tx_0002')"]),
    ("is_parent", "yes", []),
    ("is_parent", None, []),
    ("split_index", 1, []),                 # a leaked internal field of the split model
    ("zzz", "anything", []),                # any third new key at all
], ids=["parent-id-set", "is-parent-not-a-bool", "is-parent-null", "split-index", "unknown-key"])
def test_splits_only_accepts_the_two_new_keys_in_their_own_shapes(tmp_path, key, value, also):
    head = splits_head(docs())
    tx(head, 3)[key] = value
    rep = compare(tmp_path, docs(), head, "splits")
    assert rep.violations == [f"transactions.json: added at transactions/tx_0003/{key}", *also]


def test_splits_rejects_a_new_key_on_an_existing_row_even_when_the_split_is_valid(tmp_path):
    # the pinned counter-case: nothing else is wrong with this head
    head = splits_head(docs())
    assert compare(tmp_path, docs(), head, "splits").ok
    tx(head, 1)["split_index"] = 0
    rep = compare(subdir(tmp_path, "k"), docs(), head, "splits")
    assert not rep.ok
    assert rep.violations == ["transactions.json: added at transactions/tx_0001/split_index"], rep.violations


def test_splits_rejects_a_split_head_whose_schema_version_goes_down(tmp_path):
    base = docs()
    base["manifest.json"]["schema_version"] = 6
    head = splits_head(base)
    head["manifest.json"]["schema_version"] = 5
    rep = compare(tmp_path, base, head, "splits")
    assert not rep.ok
    assert any("schema_version may only increase" in v for v in rep.violations), rep.violations


def test_splits_does_not_free_accounts_json(tmp_path):
    # the free zone is exactly the listed files: accounts.json has no txn_count key in the export,
    # so nothing in it may move
    base = docs()
    base["accounts.json"]["accounts"][0]["txn_count"] = 4
    head = splits_head(base)
    head["accounts.json"]["accounts"][0]["txn_count"] = 6
    rep = compare(tmp_path, base, head, "splits")
    assert not rep.ok and any("accounts.json" in v for v in rep.violations)


def test_splits_free_zone_files_may_differ_beside_a_valid_split(tmp_path):
    head = splits_head(docs())
    head["recurring.json"]["items"] = []
    head["vendors.json"] = {"vendors": [{"vendor": "Vendor 0", "spend_ttm": 1.0}]}
    head["quality_report.json"]["checks"].append({"name": "splits.sum_identity", "status": "pass", "detail": "ok"})
    head["cashflow_monthly.json"]["months"][0]["by_category"]["Groceries"] = {"total": 7.0, "by_subcategory": {}}
    rep = compare(tmp_path, docs(), head, "splits")
    assert rep.ok, rep.violations
    # ...but not the month totals the split must leave alone
    spend = splits_head(docs())
    spend["cashflow_monthly.json"]["months"][0]["spend"] = 50.0
    assert not compare(subdir(tmp_path, "spend"), docs(), spend, "splits").ok


def test_splits_accepts_cent_exact_children_despite_float_dust(tmp_path):
    # 0.1 + 0.2 != 0.3 in floats; the check is in cents
    base = docs()
    tx(base, 0)["amount"] = -0.3
    head = splits_head(base)
    tx(head, 0)["amount"] = -0.3
    head["transactions.json"]["transactions"][-2]["amount"] = -0.1
    head["transactions.json"]["transactions"][-1]["amount"] = -0.2
    assert compare(tmp_path, base, head, "splits").ok
    three = splits_head(docs())
    kids = three["transactions.json"]["transactions"]
    kids[-2]["amount"], kids[-1]["amount"] = -3.33, -3.33
    kids.append(row(102, parent_id="tx_0000", is_parent=False, amount=-3.34, category="Shopping",
                    category_source="split"))
    three["transactions.json"]["count"] = 7
    assert compare(subdir(tmp_path, "three"), docs(), three, "splits").ok


def split_with(parent, *kids) -> tuple[dict, dict]:
    """(base, head) where tx_0000 is `parent` on both sides and its children carry `kids`"""
    base = docs()
    tx(base, 0)["amount"] = parent
    head = splits_head(base)
    rows = head["transactions.json"]["transactions"]
    del rows[-2:]
    rows += [row(100 + i, parent_id="tx_0000", is_parent=False, amount=a, category="Shopping", category_source="split")
             for i, a in enumerate(kids)]
    head["transactions.json"]["count"] = 4 + len(kids)
    head["manifest.json"]["transaction_count"] = 3 + len(kids)
    return base, head


@pytest.mark.parametrize("parent,kids", [
    (-20.00, (-19.99, -0.01)),              # none of these is exact in binary: the gate counts cents
    (-10.07, (-7.05, -3.02)),
    (-0.3, (-0.1, -0.2)),
    (-66.67, (-33.33, -33.34)),
    (-33.0, (-11.0, -11.0, -11.0)),
    (-10, (-7, -3)),                        # whole-dollar ints are amounts too
    (12.34, (6.17, 6.17)),                  # a refund splits the same way
    (-1234567.89, (-1234567.88, -0.01)),
    (-0.02, (-0.01, -0.01)),
])
def test_splits_accepts_children_that_sum_to_the_parent_to_the_cent(tmp_path, parent, kids):
    base, head = split_with(parent, *kids)
    rep = compare(tmp_path, base, head, "splits")
    assert rep.ok, rep.violations


@pytest.mark.parametrize("parent,kids", [
    (-3.01, (-1.50, -1.50)),                # a single cent short
    (-3.00, (-1.50, -1.51)),                # a single cent over
    (-10.07, (-7.05, -3.03)),
    (-20.00, (-19.99, -0.02)),
    (-20.00, (-19.99, -0.00)),
    (-1234567.89, (-1234567.88, -0.02)),
    (-0.03, (-0.01, -0.01)),
])
def test_splits_rejects_children_that_miss_the_parent_by_a_cent(tmp_path, parent, kids):
    base, head = split_with(parent, *kids)
    rep = compare(tmp_path, base, head, "splits")
    assert not rep.ok
    assert any("children do not sum" in v for v in rep.violations), rep.violations


def test_splits_rejects_sub_cent_amounts_even_when_they_balance(tmp_path):
    # -7.000 + -3.004 == -10.004 exactly, but money is whole cents: neither side may carry the fraction
    base, head = split_with(-10.004, -7.0, -3.004)
    rep = compare(tmp_path, base, head, "splits")
    assert not rep.ok
    assert "split parent tx_0000 has no finite whole-cent amount" in rep.violations
    assert "split child tx_0101 has no finite whole-cent amount" in rep.violations
    assert "-3.004" not in ed.render(rep) and "-10.004" not in ed.render(rep)


def test_splits_rejects_a_child_that_leaks_a_key_an_ordinary_row_does_not_have(tmp_path):
    head = splits_head(docs())
    assert compare(tmp_path, docs(), head, "splits").ok               # the well-formed child stays green
    last_child(head)["split_index"] = 2
    rep = compare(subdir(tmp_path, "one"), docs(), head, "splits")
    assert rep.violations == ["split child tx_0101 does not have an ordinary row's keys (extra: split_index)"]
    last_child(head)["zzz"] = 1
    rep = compare(subdir(tmp_path, "two"), docs(), head, "splits")
    assert rep.violations == ["split child tx_0101 does not have an ordinary row's keys (extra: split_index, zzz)"]


def test_splits_rejects_a_child_that_is_missing_a_key_an_ordinary_row_has(tmp_path):
    head = splits_head(docs())
    del last_child(head)["vendor"]
    rep = compare(tmp_path, docs(), head, "splits")
    assert rep.violations == ["split child tx_0101 does not have an ordinary row's keys (missing: vendor)"]
    # is_parent is part of the shape: a child without it is missing a key, not "false by default"
    gone = splits_head(docs())
    del last_child(gone)["is_parent"]
    rep = compare(subdir(tmp_path, "gone"), docs(), gone, "splits")
    assert rep.violations == ["split child tx_0101 does not have an ordinary row's keys (missing: is_parent)"]
    both = splits_head(docs())
    del last_child(both)["vendor"]
    last_child(both)["zzz"] = 1
    rep = compare(subdir(tmp_path, "both"), docs(), both, "splits")
    assert rep.violations == ["split child tx_0101 does not have an ordinary row's keys (extra: zzz; missing: vendor)"]


def test_every_child_is_checked_against_the_shape_not_just_the_first(tmp_path):
    head = splits_head(docs())
    head["transactions.json"]["transactions"][-2]["zzz"] = 1
    last_child(head)["yyy"] = 1
    rep = compare(tmp_path, docs(), head, "splits")
    assert rep.violations == ["split child tx_0100 does not have an ordinary row's keys (extra: zzz)",
                              "split child tx_0101 does not have an ordinary row's keys (extra: yyy)"]


def test_child_shape_is_the_head_ordinary_rows_keys_with_the_base_as_the_fallback():
    ordinary = {"a": {"x": 1, "parent_id": None, "is_parent": False},
                "b": {"x": 1, "parent_id": None, "is_parent": True},
                "c": {"x": 1, "parent_id": "b", "is_parent": False, "split_index": 1}}
    assert ed.child_shape({}, ordinary) == frozenset({"x", "parent_id", "is_parent"})
    # a stray row does not move the shape: the most common key set wins
    odd = dict(ordinary, d={"x": 1, "parent_id": None, "is_parent": False, "stray": 1})
    assert ed.child_shape({}, odd) == frozenset({"x", "parent_id", "is_parent"})
    # nothing ordinary in head: the base rows' keys plus the two new ones
    assert ed.child_shape({"a": {"x": 1}}, {"c": {"x": 1, "parent_id": "b"}}) == frozenset({"x", "parent_id", "is_parent"})
    assert ed.child_shape({}, {"c": {"x": 1, "parent_id": "b"}}) is None
    assert ed.child_shape({}, {}) is None


def test_split_structure_without_any_ordinary_row_skips_the_shape_but_still_judges_the_split():
    rows = {"c1": {"parent_id": "p", "amount": -1.0}, "c2": {"parent_id": "p", "amount": -2.0}}
    assert ed.split_structure(rows) == ["split child c1 names no head row flagged is_parent ('p')",
                                        "split child c2 names no head row flagged is_parent ('p')"]


@pytest.mark.parametrize("policy", ed.POLICIES)
@pytest.mark.parametrize("side", ["base", "head"])
def test_a_duplicate_transaction_id_is_a_violation_under_every_policy(tmp_path, policy, side):
    # _rows_by_id keeps the last row of an id, so a duplicate would hide the first one from every check
    base, head = docs(), docs()
    doc = base if side == "base" else head
    doc["transactions.json"]["transactions"].insert(0, row(1, amount=-99.0))     # same id as tx_0001
    doc["transactions.json"]["count"] = 5
    head["transactions.json"]["count"] = 5
    base["transactions.json"]["count"] = 5
    kw = {"known_vendors": KNOWN} if policy == "vendors-fallback-only" else {}
    rep = compare(tmp_path, base, head, policy, **kw)
    assert not rep.ok
    assert any(f"duplicate transaction id(s) in {side}" in v and "tx_0001" in v for v in rep.violations), rep.violations
    assert "-99.0" not in ed.render(rep)


def test_a_duplicate_child_id_cannot_hide_behind_a_balanced_split(tmp_path):
    head = splits_head(docs())
    head["transactions.json"]["transactions"].insert(
        4, row(101, parent_id="tx_0000", is_parent=False, amount=-5.0, category="Shopping", category_source="split"))
    head["transactions.json"]["count"] = 7
    rep = compare(tmp_path, docs(), head, "splits")
    assert not rep.ok
    assert any("duplicate transaction id(s) in head" in v for v in rep.violations), rep.violations


def test_splits_report_counts_parents_and_children_and_names_no_amount(tmp_path):
    out = ed.render(compare(tmp_path, docs(), splits_head(docs()), "splits"))
    assert "splits: 1 parents, 2 children" in out
    assert "transactions: 4 base rows, 6 head rows, 0 removed, 2 added" in out
    bad = splits_head(docs())
    bad["transactions.json"]["transactions"][-1]["amount"] = -2.0
    rep = compare(subdir(tmp_path, "b"), docs(), bad, "splits")
    text = ed.render(rep)
    assert "children do not sum" in text and "-2.0" not in text and "-10.0" not in text and "-7.0" not in text
    assert "MERCHANT" not in text
    assert rep.vendor_merges == {} and rep.vendor_splits == {}          # the vendor-move machinery is unaffected


def test_splits_note_is_zero_when_nothing_was_split_and_absent_under_other_policies(tmp_path):
    assert "splits: 0 parents, 0 children" in ed.render(compare(tmp_path, docs(), docs(), "splits"))
    out = ed.render(compare(subdir(tmp_path, "sa"), docs(), splits_head(docs()), "schema-additive"))
    assert "splits:" not in out.replace("policy: splits", "")


def test_split_structure_is_pure_and_passes_a_head_without_splits():
    assert ed.split_structure({"tx_1": row(1, parent_id=None, is_parent=False)}) == []
    assert ed.split_structure({}) == []
    rows = {r["id"]: r for r in splits_head(docs())["transactions.json"]["transactions"]}
    before = copy.deepcopy(rows)
    assert ed.split_structure(rows) == []
    assert rows == before
    rows["tx_0101"]["amount"] = -2.0
    assert ed.split_structure(rows) == ["split parent tx_0000: its children do not sum to its amount"]


# ------------------------------------------------------------- output rules

def test_render_is_aggregate_only_unless_show_rows(tmp_path):
    base, head = docs(), docs()
    tx(head, 0)["category"] = "Groceries"
    rep = compare(tmp_path, base, head, "schema-additive")
    quiet = ed.render(rep)
    assert "MERCHANT 0" not in quiet and "-10.0" not in quiet
    assert "transactions.json" in quiet and "RESULT:" in quiet
    assert "MERCHANT 0" in ed.render(rep, show_rows=True)


def test_cli_exit_codes(tmp_path):
    base, head = docs(), docs()
    b, h = write_dir(tmp_path, "base", base), write_dir(tmp_path, "head", head)
    assert ed.main([str(b), str(h), "--expect", "identical"]) == ed.EXIT_OK
    tx(head, 0)["category"] = "Groceries"
    h2 = write_dir(tmp_path, "head2", head)
    assert ed.main([str(b), str(h2), "--expect", "identical"]) == ed.EXIT_VIOLATION
    assert ed.main([str(b), str(tmp_path / "nope"), "--expect", "identical"]) == ed.EXIT_USAGE
    # the vendor policy is meaningless without the patterns vendor set
    assert ed.main([str(b), str(h), "--expect", "vendors-fallback-only"]) == ed.EXIT_USAGE


def test_known_vendors_from_rules(tmp_path):
    p = tmp_path / "vendor_rules.yaml"
    p.write_text("patterns:\n"
                 "  - { pattern: 'KNOWN', vendor: 'Known Co' }\n"
                 "  - { pattern: 'X', vendor: 'X Inc', category: Dining }\n")
    assert ed.known_vendors_from_rules(p) == {"Known Co", "X Inc"}


# ------------------------------------------- pins beyond the brief's fixtures
# Found by mutation testing: the cases above reject these only by accident or not at all.

def test_categories_only_rejects_a_vendor_move_even_on_a_legitimately_learned_row(tmp_path):
    base, head = docs(), docs()
    tx(head, 0).update(category="Groceries", category_source="learned", vendor="Other")
    rep = compare(tmp_path, base, head, "categories-only")
    assert not rep.ok
    assert any("vendor" in v for v in rep.violations)


@pytest.mark.parametrize("bad_rules", ["missing", "malformed"])
def test_cli_unreadable_vendor_rules_is_usage_not_violation(tmp_path, bad_rules):
    b, h = write_dir(tmp_path, "base", docs()), write_dir(tmp_path, "head", docs())
    rules = tmp_path / "vendor_rules.yaml"
    if bad_rules == "malformed":
        rules.write_text("patterns: [unclosed\n")
    assert ed.main([str(b), str(h), "--expect", "vendors-fallback-only",
                    "--vendor-rules", str(rules)]) == ed.EXIT_USAGE


# ------------------------------- no vacuous green on dirs without exports

NON_IDENTICAL = [p for p in ed.POLICIES if p != "identical"]


def nested(root: Path, name: str, d: dict[str, dict]) -> Path:
    """`<name>/exports/*.json` — the parent of an exports dir holds no *.json itself."""
    p = root / name
    p.mkdir()
    write_dir(p, "exports", d)
    return p


@pytest.mark.parametrize("skip", [False, True])
@pytest.mark.parametrize("case", ["both-empty", "head-empty", "base-empty",
                                  "parents-of-exports", "manifest-not-an-object"])
def test_cli_refuses_dirs_without_a_manifest(tmp_path, capsys, case, skip):
    if case == "both-empty":
        b, h = write_dir(tmp_path, "base", {}), write_dir(tmp_path, "head", {})
    elif case == "head-empty":
        b, h = write_dir(tmp_path, "base", docs()), write_dir(tmp_path, "head", {})
    elif case == "base-empty":
        b, h = write_dir(tmp_path, "base", {}), write_dir(tmp_path, "head", docs())
    elif case == "parents-of-exports":
        b, h = nested(tmp_path, "base", docs()), nested(tmp_path, "head", docs())
    else:
        b = write_dir(tmp_path, "base", {"manifest.json": []})
        h = write_dir(tmp_path, "head", {"manifest.json": []})
    argv = [str(b), str(h), "--expect", "identical"] + (["--skip-fetched-at"] if skip else [])
    assert ed.main(argv) == ed.EXIT_USAGE
    out, err = capsys.readouterr()
    assert "manifest.json" in err and "RESULT" not in out


def test_compare_raises_when_a_side_has_no_manifest(tmp_path):
    b, h = write_dir(tmp_path, "base", docs()), write_dir(tmp_path, "head", {})
    with pytest.raises(FileNotFoundError, match="manifest.json"):
        ed.compare(b, h, "identical")


def test_render_reports_how_many_files_were_compared(tmp_path):
    assert "files compared: 6" in ed.render(compare(tmp_path, docs(), docs(), "identical"))
    head = docs()
    head["vendors.json"] = {"count": 0, "vendors": []}      # a file on one side still counts
    other = tmp_path / "other"
    other.mkdir()
    assert "files compared: 7" in ed.render(compare(other, docs(), head, "schema-additive"))


# ------------------------------- manifest.schema_version may only increase

def versioned(base_v, head_v) -> tuple[dict, dict]:
    base, head = docs(), docs()
    base["manifest.json"]["schema_version"] = base_v
    head["manifest.json"]["schema_version"] = head_v
    return base, head


@pytest.mark.parametrize("policy", NON_IDENTICAL)
def test_schema_version_increase_is_allowed_and_noted(tmp_path, policy):
    base, head = versioned(2, 3)
    rep = compare(tmp_path, base, head, policy, known_vendors=KNOWN)
    assert rep.ok, rep.violations
    assert "manifest.json: schema_version 2->3" in ed.render(rep)


@pytest.mark.parametrize("policy", NON_IDENTICAL)
@pytest.mark.parametrize("old,new", [(3, 2), (2, "3"), (2, 3.5), (0, True), (2, None), ("2", 3)])
def test_schema_version_decrease_or_non_integer_is_rejected(tmp_path, policy, old, new):
    base, head = versioned(old, new)
    rep = compare(tmp_path, base, head, policy, known_vendors=KNOWN)
    assert not rep.ok
    assert any("schema_version" in v for v in rep.violations)


def test_schema_version_change_is_rejected_under_identical(tmp_path):
    base, head = versioned(2, 3)
    rep = compare(tmp_path, base, head, "identical")
    assert not rep.ok and any("schema_version" in v for v in rep.violations)


def test_schema_version_removal_is_rejected_and_addition_stays_additive(tmp_path):
    base, head = docs(), docs()
    base["manifest.json"]["schema_version"] = 2
    assert not compare(tmp_path, base, head, "schema-additive").ok          # key removed
    other = tmp_path / "other"
    other.mkdir()
    assert compare(other, head, base, "schema-additive").ok                 # key added


def test_a_schema_version_bump_does_not_excuse_other_changes(tmp_path):
    base, head = versioned(2, 3)
    tx(head, 0)["amount"] = -11.0
    rep = compare(tmp_path, base, head, "schema-additive")
    assert len(rep.violations) == 1 and "amount" in rep.violations[0]


# ------------------------------- no permissive reading, no vacuous precheck

def test_vendors_fallback_only_without_known_vendors_is_refused_not_permissive(tmp_path):
    # an empty known-vendor set would make EVERY row a "fallback" row (any vendor string may
    # move) — the most permissive reading, so the library refuses rather than pass quietly
    base, head = docs(), docs()
    tx(head, 3)["vendor"] = "Known Company"                    # a patterns hit drifting
    for kw in ({}, {"known_vendors": frozenset()}):
        other = tmp_path / f"case{len(kw)}"
        other.mkdir()
        with pytest.raises(ValueError, match="known_vendors"):
            compare(other, base, head, "vendors-fallback-only", **kw)
    # every other policy has no use for the set and still takes none
    for policy in (p for p in ed.POLICIES if p != "vendors-fallback-only"):
        other = tmp_path / policy
        other.mkdir()
        compare(other, docs(), docs(), policy)


def test_cli_vendor_rules_without_any_patterns_is_usage_not_violation(tmp_path):
    b, h = write_dir(tmp_path, "base", docs()), write_dir(tmp_path, "head", docs())
    rules = tmp_path / "vendor_rules.yaml"
    for text in ("patterns: []\n", "other: 1\n", ""):
        rules.write_text(text)
        assert ed.main([str(b), str(h), "--expect", "vendors-fallback-only",
                        "--vendor-rules", str(rules)]) == ed.EXIT_USAGE


def stamps(base: dict, head: dict, allowed_absent: frozenset[str] = frozenset()) -> list[str]:
    return ed.check_fetched_at(base["manifest.json"], head["manifest.json"], allowed_absent)


@pytest.mark.parametrize("missing_from", ["base", "head", "both"])
def test_simplefin_fetched_at_is_required_on_both_sides(tmp_path, missing_from):
    key = "simplefin_fetched_at"
    assert ed.REQUIRED_FETCHED_AT == (key,)
    base, head = docs(), docs()
    for side, d in (("base", base), ("head", head)):
        if missing_from in (side, "both"):
            del d["manifest.json"][key]                       # None == None must not pass
    problems = stamps(base, head)
    assert len(problems) == 1 and problems[0].startswith(key)
    # the required stamp can never be excused, whatever the caller passes
    assert stamps(base, head, frozenset({key})) == problems
    rep = compare(tmp_path, base, head, "identical" if missing_from == "both" else "schema-additive")
    assert rep.inputs_changed == problems and not rep.ok
    b, h = write_dir(tmp_path, "b2", base), write_dir(tmp_path, "h2", head)
    assert ed.main([str(b), str(h), "--expect", "schema-additive"]) == ed.EXIT_INPUTS_CHANGED
    assert ed.main([str(b), str(h), "--expect", "removals"]) == ed.EXIT_INPUTS_CHANGED
    # --skip-fetched-at (synthetic dirs only) still opts out of the precheck entirely
    assert ed.main([str(b), str(h), "--expect", "schema-additive", "--skip-fetched-at"]) \
        in (ed.EXIT_OK, ed.EXIT_VIOLATION)


def test_another_fetched_at_stamp_is_compared_when_both_sides_have_it(tmp_path):
    base, head = docs(), docs()
    head["manifest.json"]["prices_fetched_at"] = "2026-10-03T01:00:00Z"
    assert stamps(base, head) == ["prices_fetched_at"]
    # an allowance for a stamp's absence never excuses a different value
    assert stamps(base, head, frozenset({"prices_fetched_at"})) == ["prices_fetched_at"]
    rep = compare(tmp_path, base, head, "identical")
    assert rep.inputs_changed == ["prices_fetched_at"] and not rep.ok
    b, h = write_dir(tmp_path, "b2", base), write_dir(tmp_path, "h2", head)
    assert ed.main([str(b), str(h), "--expect", "identical"]) == ed.EXIT_INPUTS_CHANGED
    assert ed.main([str(b), str(h), "--expect", "removals",
                    "--allow-removed", "manifest.json:prices_fetched_at"]) == ed.EXIT_INPUTS_CHANGED
    # any key ending _fetched_at is a stamp, whatever it is called; other keys are not
    for d in (base, head):
        d["manifest.json"]["prices_fetched_at"] = "2026-10-02T15:00:10Z"
    base["manifest.json"]["other_fetched_at"], head["manifest.json"]["other_fetched_at"] = "a", "b"
    base["manifest.json"]["fetched_at_note"], head["manifest.json"]["fetched_at_note"] = "a", "b"
    assert stamps(base, head) == ["other_fetched_at"]
    # equal on both sides, or absent from both, is no problem
    assert stamps(docs(), docs()) == []
    plain_base, plain_head = docs(), docs()
    for d in (plain_base, plain_head):
        del d["manifest.json"]["prices_fetched_at"], d["manifest.json"]["broker_fetched_at"]
    assert stamps(plain_base, plain_head) == []


def test_a_stamp_missing_from_head_is_a_problem_unless_allow_listed(tmp_path):
    base, head = docs(), docs()
    del head["manifest.json"]["prices_fetched_at"]
    problems = stamps(base, head)
    assert problems == ["prices_fetched_at (no such key in head manifest)"]
    assert stamps(base, head, frozenset({"prices_fetched_at"})) == []
    assert stamps(base, head, frozenset({"broker_fetched_at"})) == problems     # another stamp's allowance
    b, h = write_dir(tmp_path, "b2", base), write_dir(tmp_path, "h2", head)
    assert ed.main([str(b), str(h), "--expect", "schema-additive"]) == ed.EXIT_INPUTS_CHANGED
    assert ed.main([str(b), str(h), "--expect", "removals"]) == ed.EXIT_INPUTS_CHANGED
    for spec in ("manifest.json:broker_fetched_at", "manifest.json:prices_fetched_at.x",
                 "manifest.json:prices_fetched_at[name=*]"):
        # only an allowance for exactly that top-level key excuses it
        assert ed.main([str(b), str(h), "--expect", "removals", "--allow-removed", spec]) == ed.EXIT_INPUTS_CHANGED
    argv = [str(b), str(h), "--expect", "removals", "--allow-removed", "manifest.json:prices_fetched_at"]
    assert ed.main(argv) == ed.EXIT_OK
    rep = ed.compare(b, h, "removals", allow_removed=["manifest.json:prices_fetched_at"])
    assert rep.ok and rep.inputs_changed == []
    assert "allowance manifest.json:prices_fetched_at: 1 removed" in ed.render(rep)


def test_a_stamp_only_in_head_is_a_problem(tmp_path):
    base, head = docs(), docs()
    del base["manifest.json"]["broker_fetched_at"]
    problems = stamps(base, head)
    assert problems == ["broker_fetched_at (no such key in base manifest)"]
    # an allowance covers a stamp that vanished, never one that appeared
    assert stamps(base, head, frozenset({"broker_fetched_at"})) == problems
    fresh_base, fresh_head = docs(), docs()
    fresh_head["manifest.json"]["new_fetched_at"] = None          # null is still a key that appeared
    assert stamps(fresh_base, fresh_head) == ["new_fetched_at (no such key in base manifest)"]
    b, h = write_dir(tmp_path, "b2", base), write_dir(tmp_path, "h2", head)
    assert ed.main([str(b), str(h), "--expect", "removals"]) == ed.EXIT_INPUTS_CHANGED
    assert ed.main([str(b), str(h), "--expect", "removals",
                    "--allow-removed", "manifest.json:broker_fetched_at"]) == ed.EXIT_INPUTS_CHANGED


def test_a_null_stamp_on_both_sides_is_equal_not_missing(tmp_path):
    # "never fetched" is a real state: the key is present, the value is null
    base, head = docs(), docs()
    for d in (base, head):
        d["manifest.json"]["broker_fetched_at"] = None
    assert ed.check_fetched_at(base["manifest.json"], head["manifest.json"]) == []
    rep = compare(tmp_path, base, head, "identical")
    assert rep.ok and rep.inputs_changed == []


def test_a_null_stamp_against_a_real_one_still_differs(tmp_path):
    base, head = docs(), docs()
    head["manifest.json"]["prices_fetched_at"] = None
    assert ed.check_fetched_at(base["manifest.json"], head["manifest.json"]) == ["prices_fetched_at"]


def test_all_three_stamps_present_and_equal_passes_the_precheck(tmp_path):
    m = docs()["manifest.json"]
    assert ed.check_fetched_at(m, dict(m)) == []


# ------------------- cancelled_subscriptions vendor strings + the vendor-move matrix

def subdir(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir()
    return d


def with_cancelled(d: dict, vendor: str) -> dict:
    d["assumptions.json"] = {"projections": {"inflation_pct": 2}, "thresholds": {
        "uncategorized_review_threshold": 250,
        "cancelled_subscriptions": [{"vendor": vendor, "recorded_on": "2026-08-11", "permanent": True}]}}
    d["manifest.json"]["files"] = sorted(set(d["manifest.json"]["files"]) | {"assumptions.json"})
    return d


def test_vendors_fallback_only_allows_cancelled_subscription_vendor_strings_to_move(tmp_path):
    base, head = with_cancelled(docs(), "Vendor Two"), with_cancelled(docs(), "Vendor 2")
    rep = compare(tmp_path, base, head, "vendors-fallback-only", known_vendors=KNOWN)
    assert rep.ok, rep.violations
    assert any(n.startswith("assumptions.json: changed vendor x1") for n in rep.notes)
    # and the same file is NOT free under the other policies
    assert not compare(subdir(tmp_path, "sa"), base, head, "schema-additive").ok


@pytest.mark.parametrize("mutate", [
    lambda h: h["assumptions.json"]["thresholds"]["cancelled_subscriptions"][0].__setitem__("permanent", False),
    lambda h: h["assumptions.json"]["thresholds"]["cancelled_subscriptions"].append(
        {"vendor": "New", "recorded_on": "2026-10-03", "permanent": True}),
    lambda h: h["assumptions.json"]["thresholds"].__setitem__("uncategorized_review_threshold", 300),
    lambda h: h["assumptions.json"]["projections"].__setitem__("inflation_pct", 3),
])
def test_vendors_fallback_only_rejects_other_assumptions_changes(tmp_path, mutate):
    base, head = with_cancelled(docs(), "Vendor Two"), with_cancelled(docs(), "Vendor Two")
    mutate(head)
    rep = compare(tmp_path, base, head, "vendors-fallback-only", known_vendors=KNOWN)
    assert not rep.ok


def test_vendors_fallback_only_rejects_a_non_list_where_a_vendor_list_belongs(tmp_path):
    # a value that is not a list cannot be judged "vendor strings only": it is compared whole
    # (pruning it from both sides would hide any change inside it)
    base, head = docs(), docs()
    for d, v in ((base, "Vendor Two"), (head, "Vendor Three")):
        d["assumptions.json"] = {"thresholds": {"cancelled_subscriptions": {"vendor": v}}}
        d["manifest.json"]["files"] = sorted(set(d["manifest.json"]["files"]) | {"assumptions.json"})
    assert not compare(tmp_path, base, head, "vendors-fallback-only", known_vendors=KNOWN).ok


def test_render_lists_vendor_moves_as_names_only(tmp_path):
    base, head = docs(), docs()
    tx(head, 0)["vendor"] = "Vendor Zero"
    tx(head, 1)["vendor"] = "Vendor Zero"
    tx(head, 2)["vendor"] = "Vendor Too"
    # these are respellings, not case changes: only renames: entries can excuse them
    renames = {"Vendor 0": "Vendor Zero", "Vendor 1": "Vendor Zero", "Vendor 2": "Vendor Too"}
    rep = compare(tmp_path, base, head, "vendors-fallback-only", known_vendors=KNOWN, vendor_renames=renames)
    assert rep.ok, rep.violations
    assert rep.vendor_matrix == {("Vendor 0", "Vendor Zero"): 1, ("Vendor 1", "Vendor Zero"): 1, ("Vendor 2", "Vendor Too"): 1}
    out = ed.render(rep)
    assert "vendor changes (old -> new): rows" in out
    assert out.index("Vendor 0 -> Vendor Zero: 1") < out.index("Vendor 1 -> Vendor Zero: 1") < out.index("Vendor 2 -> Vendor Too: 1")
    assert "MERCHANT" not in out and "-10.0" not in out
    assert "vendor changes" not in ed.render(compare(subdir(tmp_path, "same"), docs(), docs(), "identical"))


# ------- a vendor merge or respelling must not pass as "fallback only"
# The per-row rule used to ask only "was the BASE vendor a fallback vendor?", so any NEW string
# passed — two distinct vendors merging into one, or a respelling to anything — and vendors.json
# was waved through because the base (main) has no such file.

def moved(**moves: tuple[str, str]) -> tuple[dict, dict]:
    """base/head pair; `t0=("old", "new")` sets row 0's vendor on each side."""
    base, head = docs(), docs()
    for key, (old, new) in moves.items():
        tx(base, int(key[1:]))["vendor"], tx(head, int(key[1:]))["vendor"] = old, new
    return base, head


def vendors_json(d: dict, *names: str) -> None:
    d["vendors.json"] = {"count": len(names), "vendors": [{"vendor": n, "txns": 1} for n in names]}
    d["manifest.json"]["files"] = sorted(set(d["manifest.json"]["files"]) | {"vendors.json"})


@pytest.mark.parametrize("case", ["merge-two-distinct", "respell-one", "merge-into-an-existing-vendor",
                                  "merge-with-consistent-secondary-exports"])
def test_vendors_fallback_only_rejects_merges_and_respellings(tmp_path, case):
    if case == "respell-one":
        base, head = moved(t0=("Vendor 0", "Totally Different"))
    elif case == "merge-into-an-existing-vendor":
        base, head = moved(t0=("Foo Bar", "Baz Qux"), t1=("Baz Qux", "Baz Qux"))   # one row changed
    else:
        base, head = moved(t0=("Foo Bar", "Zed"), t1=("Baz Qux", "Zed"))
    if case == "merge-with-consistent-secondary-exports":
        # every second line (recurring, top_unmapped, a brand-new vendors.json) agrees with head
        head["recurring.json"]["items"][0]["vendor"] = "Zed"
        head["quality_report.json"]["top_unmapped_vendors"][0]["vendor"] = "Zed"
        vendors_json(head, "Zed")
    rep = compare(tmp_path, base, head, "vendors-fallback-only", known_vendors=KNOWN)
    assert not rep.ok
    assert any("transactions.json" in v and "vendor" in v for v in rep.violations)
    if case.startswith("merge"):
        assert any(v.startswith("vendor merge into ") for v in rep.violations), rep.violations
        assert rep.vendor_merges            # and the merge is reported by name


def test_a_case_only_vendor_change_passes_but_not_on_a_patterns_row(tmp_path):
    base, head = moved(t0=("Vendor 0", "VENDOR 0"), t1=("Straße Cafe", "STRASSE CAFE"))
    rep = compare(tmp_path, base, head, "vendors-fallback-only", known_vendors=KNOWN)
    assert rep.ok, rep.violations
    assert rep.vendor_merges == {} and rep.vendor_splits == {}
    # a patterns-hit vendor may not move at all, not even in case
    base, head = moved(t3=("Known Co", "KNOWN CO"))
    assert not compare(subdir(tmp_path, "k"), base, head, "vendors-fallback-only", known_vendors=KNOWN).ok


def test_a_casefold_equal_merge_passes_and_is_listed_by_name(tmp_path):
    # smart_title turned "Jo'S" into "Jo's", which another row already spelled that way
    base, head = moved(t0=("Jo'S", "Jo's"), t1=("Jo's", "Jo's"))
    rep = compare(tmp_path, base, head, "vendors-fallback-only", known_vendors=KNOWN)
    assert rep.ok, rep.violations
    assert rep.vendor_merges == {"Jo's": ["Jo'S", "Jo's"]} and rep.vendor_splits == {}
    out = ed.render(rep)
    assert "vendor merges (head <- base vendors): 1" in out
    assert "  Jo's <- Jo'S, Jo's" in out
    assert "vendor splits (base -> head vendors): 0" in out
    assert "MERCHANT" not in out and "-10.0" not in out


def test_a_casefold_equal_split_passes_and_is_listed_by_name(tmp_path):
    base, head = moved(t0=("Foo Bar", "Foo Bar"), t1=("Foo Bar", "FOO BAR"))
    rep = compare(tmp_path, base, head, "vendors-fallback-only", known_vendors=KNOWN)
    assert rep.ok, rep.violations
    assert rep.vendor_splits == {"Foo Bar": ["FOO BAR", "Foo Bar"]} and rep.vendor_merges == {}
    out = ed.render(rep)
    assert "vendor splits (base -> head vendors): 1" in out and "  Foo Bar -> FOO BAR, Foo Bar" in out


def test_a_split_into_distinct_vendors_is_rejected(tmp_path):
    base, head = moved(t0=("Foo Bar", "Foo Bar"), t1=("Foo Bar", "Zed"))
    rep = compare(tmp_path, base, head, "vendors-fallback-only", known_vendors=KNOWN)
    assert not rep.ok and rep.vendor_splits == {"Foo Bar": ["Foo Bar", "Zed"]}


def test_a_renames_entry_explains_a_respelling_and_a_merge(tmp_path):
    base, head = moved(t0=("Old Name", "New Name"), t1=("OLD NAME", "New Name"), t2=("Other Old", "New Name"))
    renames = {"old name": "New Name", "Other Old": "New Name"}       # `from` matches case-insensitively
    rep = compare(tmp_path, base, head, "vendors-fallback-only", known_vendors=KNOWN, vendor_renames=renames)
    assert rep.ok, rep.violations
    assert rep.vendor_merges == {"New Name": ["OLD NAME", "Old Name", "Other Old"]}


@pytest.mark.parametrize("renames", [
    {},                                          # nothing explains it
    {"Old Name": "Another Name"},                # the entry points somewhere else
    {"Old Names": "New Name"},                   # the entry is for another vendor
    {"Old Name": "new name"},                    # `to` must match exactly: the new spelling is the rename's
])
def test_a_renames_entry_that_does_not_match_explains_nothing(tmp_path, renames):
    base, head = moved(t0=("Old Name", "New Name"))
    assert not compare(tmp_path, base, head, "vendors-fallback-only", known_vendors=KNOWN,
                       vendor_renames=renames).ok


def test_a_rename_does_not_excuse_a_patterns_row(tmp_path):
    base, head = moved(t3=("Known Co", "Renamed Co"))
    assert not compare(tmp_path, base, head, "vendors-fallback-only", known_vendors=KNOWN,
                       vendor_renames={"Known Co": "Renamed Co"}).ok


def test_non_string_vendors_never_count_as_explained(tmp_path):
    base, head = docs(), docs()
    tx(base, 0)["vendor"], tx(head, 0)["vendor"] = None, "Zed"
    assert not compare(tmp_path, base, head, "vendors-fallback-only", known_vendors=KNOWN).ok


def test_vendor_merge_check_is_independent_of_the_per_row_rule():
    # the second line, called directly: a merge of distinct names is a violation however the
    # rows that fed it were judged
    bad = ed.bad_merges({"Zed": ["Baz Qux", "Foo Bar"]}, {})
    assert len(bad) == 1 and bad[0].startswith("vendor merge into 'Zed'") and "Baz Qux" in bad[0]
    assert ed.bad_merges({"Jo's": ["Jo'S", "Jo's"]}, {}) == []                          # casefold-equal
    assert ed.bad_merges({"Zed": ["Jo'S", "Jo's"]}, {}) != []                            # equal to each other, not to Zed
    assert ed.bad_merges({"Zed": ["Baz Qux", "Foo Bar"]}, {"baz qux": "Zed", "foo bar": "Zed"}) == []
    assert ed.bad_merges({"Zed": ["Baz Qux", "Foo Bar"]}, {"baz qux": "Zed"}) != []     # one source unexplained


def test_vendor_moves_counts_unchanged_rows_and_only_rows_on_both_sides():
    def rows(*vendors):
        return {f"t{i}": {"vendor": v} for i, v in enumerate(vendors)}
    merges, splits = ed.vendor_moves(rows("A", "a", "B", "gone"), {**rows("a", "a", "B"), "new": {"vendor": "a"}})
    assert merges == {"a": ["A", "a"]} and splits == {}
    assert ed.vendor_moves(rows("A", "A"), rows("A", "a")) == ({}, {"A": ["A", "a"]})
    assert ed.vendor_moves({"t0": {"vendor": None}}, {"t0": {"vendor": "x"}}) == ({}, {})


def test_render_prints_the_merge_and_split_counts_only_where_they_matter(tmp_path):
    out = ed.render(compare(tmp_path, docs(), docs(), "vendors-fallback-only", known_vendors=KNOWN))
    assert "vendor merges (head <- base vendors): 0" in out and "vendor splits (base -> head vendors): 0" in out
    assert "vendor merges" not in ed.render(compare(subdir(tmp_path, "o"), docs(), docs(), "schema-additive"))


# ------- the --vendor-rules file also supplies the renames:

RULES = "patterns:\n  - { pattern: 'KNOWN', vendor: 'Known Co' }\n"


def test_vendor_renames_from_rules(tmp_path):
    p = tmp_path / "vendor_rules.yaml"
    for text in (RULES, RULES + "renames: []\n", RULES + "renames:\n"):
        p.write_text(text)
        assert ed.vendor_renames_from_rules(p) == {}
    p.write_text(RULES + "renames:\n  - { from: 'Old Name', to: 'New Name' }\n  - { from: 'Cora', to: 'Cora Co' }\n")
    assert ed.vendor_renames_from_rules(p) == {"Old Name": "New Name", "Cora": "Cora Co"}


@pytest.mark.parametrize("renames", [
    "renames: oops\n", "renames: [oops]\n", "renames: [{ from: A }]\n", "renames: [{ from: '', to: B }]\n",
    "renames: [{ from: 1, to: B }]\n",
    "renames: [{ from: A, to: B }, { from: a, to: C }]\n",            # same name once casefolded
])
def test_malformed_renames_are_a_usage_error_not_a_pass(tmp_path, capsys, renames):
    b, h = write_dir(tmp_path, "base", docs()), write_dir(tmp_path, "head", docs())
    rules = tmp_path / "vendor_rules.yaml"
    rules.write_text(RULES + renames)
    with pytest.raises(ValueError, match="renames"):
        ed.vendor_renames_from_rules(rules)
    assert ed.main([str(b), str(h), "--expect", "vendors-fallback-only",
                    "--vendor-rules", str(rules)]) == ed.EXIT_USAGE
    assert "renames" in capsys.readouterr().err


def test_cli_passes_the_rules_renames_to_the_comparison(tmp_path):
    base, head = moved(t0=("Old Name", "New Name"))
    b, h = write_dir(tmp_path, "base", base), write_dir(tmp_path, "head", head)
    rules = tmp_path / "vendor_rules.yaml"
    argv = [str(b), str(h), "--expect", "vendors-fallback-only", "--vendor-rules", str(rules)]
    rules.write_text(RULES)
    assert ed.main(argv) == ed.EXIT_VIOLATION
    rules.write_text(RULES + "renames:\n  - { from: 'old name', to: 'New Name' }\n")
    assert ed.main(argv) == ed.EXIT_OK


# ------------------------------------------------ categories-only against documents with schedule keys

SCHEDULE_KEYS = {"schedule": True, "expected_dates": ["2026-02-15", "2026-03-15", "2026-04-15"],
                 "grace_days": 6, "confidence": 1.0, "median_gap_days": 30}


def schedule_head() -> dict:
    """What a head run with schedule keys adds on top of docs(): schema 4, schedule keys, ledger_drift,
    the learned_rules checks, a learned vendor in vendors.json, one learned row."""
    head = docs()
    head["manifest.json"]["schema_version"] = 4
    head["recurring.json"]["items"][0].update(SCHEDULE_KEYS)
    head["accounts.json"]["accounts"][0]["ledger_drift"] = {"from": "2026-01-01", "to": "2026-01-10",
                                                            "expected": -60.0, "got": -50.0, "drift": 10.0}
    head["quality_report.json"]["checks"].append(
        {"name": "learned_rules.active", "status": "info", "detail": "1 learned vendor rule(s) active: Vendor 0"})
    head["quality_report.json"]["checks"].append(
        {"name": "reconcile.chk.ledger_drift", "status": "info", "detail": "drift"})
    head["vendors.json"] = {"count": 1, "vendors": [{"vendor": "Vendor 0", "has_rule": False, "category": "Groceries",
                            "subcategory": None, "category_source": "learned", "learn": True, "txns": 1,
                            "spend_ttm": 10.0, "first_seen": "2026-01-15", "last_seen": "2026-01-15"}]}
    tx(head, 0).update(category="Groceries", subcategory=None, category_source="learned")
    head["cashflow_monthly.json"]["months"][0]["by_category"] = {
        "Dining": {"total": 30.0, "by_subcategory": {"Restaurants": 30.0}},
        "Groceries": {"total": 10.0, "by_subcategory": {"-": 10.0}}}
    return head


def schedule_base() -> dict:
    base = docs()
    base["manifest.json"]["schema_version"] = 3
    base["vendors.json"] = {"count": 1, "vendors": [{"vendor": "Vendor 0", "has_rule": False, "category": None,
                            "subcategory": None, "category_source": None, "learn": True, "txns": 1,
                            "spend_ttm": 10.0, "first_seen": "2026-01-15", "last_seen": "2026-01-15"}]}
    return base


def test_categories_only_accepts_the_phase2_shape(tmp_path):
    rep = compare(tmp_path, schedule_base(), schedule_head(), "categories-only")
    assert rep.ok, rep.violations
    out = ed.render(rep)
    assert "manifest.json: schema_version 3->4" in out
    assert "recurring.json: added confidence x1, added expected_dates x1, added grace_days x1, added median_gap_days x1, added schedule x1" in out
    assert "accounts.json: added ledger_drift x1" in out
    assert rep.matrix == {("Dining/Restaurants", "Groceries/-"): 1}


@pytest.mark.parametrize("mutate", [
    lambda h: h["recurring.json"]["items"][0].pop("cadence"),                           # a removed key is never additive
    lambda h: h["manifest.json"].__setitem__("transaction_count", 5),                   # a count changed but rows didn't
    lambda h: h["accounts.json"]["accounts"][0].__setitem__("name", "Renamed"),         # an existing value moved
    lambda h: h["transactions.json"]["transactions"].append(row(9, category_source="learned")),  # a new row
    lambda h: h["cashflow_monthly.json"]["months"][0].__setitem__("spend", 41.0),       # a total moved
    lambda h: h["manifest.json"].__setitem__("schema_version", 2),                      # went down
])
def test_categories_only_still_rejects_non_additive_changes_on_phase2_docs(tmp_path, mutate):
    head = schedule_head()
    mutate(head)
    assert not compare(tmp_path, schedule_base(), head, "categories-only").ok


def test_categories_only_lets_tags_move_only_on_a_cascade_recategorized_row(tmp_path):
    # a tag move rides with the category move on a row a learned rule recategorized — and nowhere else
    base, head = docs(), docs()
    tx(head, 1).update(category="Kids", subcategory="Childcare", category_source="learned", tags=["kids"])
    head["cashflow_monthly.json"]["months"][0]["by_category"] = {
        "Dining": {"total": 30.0, "by_subcategory": {"Restaurants": 30.0}},
        "Kids": {"total": 10.0, "by_subcategory": {"Childcare": 10.0}}}
    assert compare(tmp_path, base, head, "categories-only").ok
    # equal-length tag substitution (reported element-wise at .../tags/0) on a cascade row passes
    base_eq, head_eq = docs(), docs()
    tx(base_eq, 1).update(tags=["a"])
    tx(head_eq, 1).update(category="Kids", subcategory="Childcare", category_source="learned", tags=["b"])
    head_eq["cashflow_monthly.json"]["months"][0]["by_category"] = {
        "Dining": {"total": 30.0, "by_subcategory": {"Restaurants": 30.0}},
        "Kids": {"total": 10.0, "by_subcategory": {"Childcare": 10.0}}}
    eq_dir = tmp_path / "eq"
    eq_dir.mkdir()
    assert compare(eq_dir, base_eq, head_eq, "categories-only").ok
    # ... the same equal-length swap without a category move is still a violation at the element path
    base_eq2, head_eq2 = docs(), docs()
    tx(base_eq2, 1).update(tags=["a"])
    tx(head_eq2, 1).update(category_source="learned", tags=["b"])
    eq2_dir = tmp_path / "eq2"
    eq2_dir.mkdir()
    rep_eq2 = compare(eq2_dir, base_eq2, head_eq2, "categories-only")
    assert not rep_eq2.ok and any("/tags/0" in v for v in rep_eq2.violations)
    # tags may shrink on a learned row (a learned rule replacing a keyword rule drops that rule's tags)
    base_shrink, head_shrink = docs(), docs()
    tx(base_shrink, 1).update(tags=["kw"])
    tx(head_shrink, 1).update(category="Kids", subcategory="Childcare", category_source="learned", tags=[])
    head_shrink["cashflow_monthly.json"]["months"][0]["by_category"] = {
        "Dining": {"total": 30.0, "by_subcategory": {"Restaurants": 30.0}},
        "Kids": {"total": 10.0, "by_subcategory": {"Childcare": 10.0}}}
    shrink_dir = tmp_path / "shrink"
    shrink_dir.mkdir()
    assert compare(shrink_dir, base_shrink, head_shrink, "categories-only").ok
    # ... but the same tag move alone is not a category change
    base2, head2 = docs(), docs()
    tx(head2, 1).update(category_source="learned", tags=["kids"])
    alone_dir = tmp_path / "alone"
    alone_dir.mkdir()
    rep = compare(alone_dir, base2, head2, "categories-only")
    assert not rep.ok and any("tags" in v for v in rep.violations)
    # ... and a category move by a keyword rule may not carry tags either (nor pass at all)
    base3, head3 = docs(), docs()
    tx(head3, 1).update(category="Kids", subcategory="Childcare", category_source="keyword", tags=["kids"])
    head3["cashflow_monthly.json"]["months"][0]["by_category"] = {
        "Dining": {"total": 30.0, "by_subcategory": {"Restaurants": 30.0}},
        "Kids": {"total": 10.0, "by_subcategory": {"Childcare": 10.0}}}
    keyword_dir = tmp_path / "keyword"
    keyword_dir.mkdir()
    rep3 = compare(keyword_dir, base3, head3, "categories-only")
    assert not rep3.ok and any("tags" in v for v in rep3.violations)


def test_other_policies_do_not_inherit_the_tags_allowance(tmp_path):
    base, head = docs(), docs()
    tx(head, 1).update(category="Kids", subcategory="Childcare", category_source="learned", tags=["kids"])
    head["cashflow_monthly.json"]["months"][0]["by_category"] = {
        "Dining": {"total": 30.0, "by_subcategory": {"Restaurants": 30.0}},
        "Kids": {"total": 10.0, "by_subcategory": {"Childcare": 10.0}}}
    for policy, kw in [("identical", {}), ("schema-additive", {}), ("vendors-fallback-only", {"known_vendors": KNOWN}), ("splits", {})]:
        other = tmp_path / policy
        other.mkdir()
        rep = compare(other, base, head, policy, **kw)
        assert not rep.ok and any("/tags" in v for v in rep.violations)


# ------------------------------------------------------------------ removals
# Each comparison gets its own directory under tmp_path (`name`), so one test can run several.

def removals(tmp_path: Path, base: dict, head: dict, *specs: str, name: str = "r") -> ed.Report:
    return compare(subdir(tmp_path, name), base, head, "removals", allow_removed=specs)


def with_file(d: dict, name: str, doc: dict) -> dict:
    """`name` holding `doc`, listed in manifest.files"""
    d[name] = doc
    d["manifest.json"]["files"] = sorted(set(d["manifest.json"]["files"]) | {name})
    return d


def checks(*rows) -> list[dict]:
    """quality_report checks: a bare name is a passing check, a dict is taken as written"""
    return [r if isinstance(r, dict) else {"name": r, "status": "pass", "detail": "ok"} for r in rows]


def with_checks(d: dict, *rows) -> dict:
    d["quality_report.json"]["checks"] = checks(*rows)
    return d


def test_removals_with_no_allowance_is_identical_except_a_schema_bump(tmp_path):
    assert removals(tmp_path, docs(), docs(), name="equal").ok
    up = removals(tmp_path, *versioned(6, 7), name="up")
    assert up.ok, up.violations
    assert "manifest.json: schema_version 6->7" in ed.render(up)
    down = removals(tmp_path, *versioned(7, 6), name="down")
    assert down.violations == ["manifest.json: changed at schema_version (schema_version may only increase)"]
    base, head = docs(), docs()
    tx(head, 0)["amount"] = -11.0
    assert removals(tmp_path, base, head, name="changed").violations == [
        "transactions.json: changed at transactions/tx_0000/amount"]
    base, head = docs(), docs()
    head["recurring.json"]["items"][0]["schedule"] = True
    assert removals(tmp_path, base, head, name="added").violations == ["recurring.json: added at items/0/schedule"]
    # removals is not additive: a new file, a new row or a new manifest entry is outside it
    base, head = docs(), with_file(docs(), "vendors.json", {"vendors": []})
    assert set(removals(tmp_path, base, head, name="new-file").violations) == {
        "vendors.json: added at <file>", "manifest.json: changed at files"}
    base, head = docs(), docs()
    head["transactions.json"]["transactions"].append(row(9))
    assert "transactions.json: added at transactions/tx_0009" in removals(
        tmp_path, base, head, name="new-row").violations
    # and nothing may vanish without an allowance
    base, head = docs(), docs()
    del head["recurring.json"]["items"][0]["cadence"]
    assert not removals(tmp_path, base, head, name="unlisted").ok


def test_removals_allows_a_listed_file_and_shrinks_manifest_files(tmp_path):
    base, head = with_file(docs(), "x.json", {"rows": [{"name": "a"}]}), docs()
    rep = removals(tmp_path, base, head, "x.json", name="ok")
    assert rep.ok, rep.violations
    out = ed.render(rep)
    assert "allowance x.json: 1 removed" in out and "x.json: removed <file> x1" in out
    assert set(removals(tmp_path, base, head, name="none").violations) == {
        "x.json: removed at <file> (no --allow-removed spec covers it)", "manifest.json: changed at files"}
    # a file allowance covers the file's absence only, never a key gone from a file head still has
    trimmed = with_file(docs(), "x.json", {})
    assert removals(tmp_path, base, trimmed, "x.json", name="trimmed").violations == [
        "x.json: removed at rows (no --allow-removed spec covers it)"]
    # an unlisted file vanishing beside it is still a violation, file and manifest entry both
    also = docs()
    del also["recurring.json"]
    also["manifest.json"]["files"].remove("recurring.json")
    assert set(removals(tmp_path, base, also, "x.json", name="unlisted").violations) == {
        "recurring.json: removed at <file> (no --allow-removed spec covers it)", "manifest.json: changed at files"}


def test_removals_manifest_files_follows_the_files_head_really_has(tmp_path):
    base = with_file(docs(), "x.json", {"rows": []})
    # the file is gone but head's manifest still lists it
    listed = docs()
    listed["manifest.json"]["files"] = list(base["manifest.json"]["files"])
    assert removals(tmp_path, base, listed, "x.json", name="listed").violations == [
        "manifest.json: files still lists x.json, which head does not have"]
    # the manifest entry is gone but the file stays
    kept = with_file(docs(), "x.json", {"rows": []})
    kept["manifest.json"]["files"].remove("x.json")
    assert removals(tmp_path, base, kept, "x.json", name="kept").violations == ["manifest.json: changed at files"]
    # the remaining entries keep their order, and nothing joins them
    for case, files in (("reordered", sorted(docs()["manifest.json"]["files"], reverse=True)),
                        ("grown", docs()["manifest.json"]["files"] + ["y.json"]),           # x.json's slot, renamed
                        ("longer", docs()["manifest.json"]["files"] + ["y.json", "z.json"])):
        head = docs()
        head["manifest.json"]["files"] = files
        found = removals(tmp_path, base, head, "x.json", name=case).violations
        assert found and all(v.startswith("manifest.json: changed at files") for v in found), found
    # a listed file that head still has must be identical
    edited = with_file(docs(), "x.json", {"rows": [{"name": "a"}]})
    rep = removals(tmp_path, base, edited, "x.json", name="edited")
    assert rep.violations == ["x.json: changed at rows"]
    assert "allowance x.json: 0 removed" in ed.render(rep)


def test_removals_allows_a_listed_key_path_and_its_subtree(tmp_path):
    base = with_file(docs(), "assumptions.json", {"a": {"b": 1, "c": 2}, "d": {"e": 1}})
    specs = ("assumptions.json:a.b", "assumptions.json:d")

    def run(head_doc: dict, name: str) -> ed.Report:
        return removals(tmp_path, base, with_file(docs(), "assumptions.json", head_doc), *specs, name=name)

    ok = run({"a": {"c": 2}}, "ok")
    assert ok.ok, ok.violations
    out = ed.render(ok)
    assert "allowance assumptions.json:a.b: 1 removed" in out and "allowance assumptions.json:d: 1 removed" in out
    # the key's subtree: d stays, d.e goes
    assert run({"a": {"c": 2}, "d": {}}, "subtree").ok
    assert run({"a": {"c": 3}}, "changed").violations == ["assumptions.json: changed at a/c"]
    assert run({"a": {"c": 2}, "d": {"e": 2}}, "kept-changed").violations == ["assumptions.json: changed at d/e"]
    assert run({"a": {}}, "dropped").violations == [
        "assumptions.json: removed at a/c (no --allow-removed spec covers it)"]
    assert run({}, "parent").violations == ["assumptions.json: removed at a (no --allow-removed spec covers it)"]
    assert run({"a": {"c": 2, "z": 1}}, "added").violations == ["assumptions.json: added at a/z"]
    assert run({"a": {"c": 2}, "d": {"e": 1, "f": 1}}, "added-under").violations == ["assumptions.json: added at d/f"]
    assert run({"a": {"c": 2}, "d": 5}, "retyped").violations == ["assumptions.json: changed at d"]


def test_a_key_path_never_reaches_into_a_list(tmp_path):
    # list elements are addressed by index in the walk, never by a KEY (a digit KEY is a dict key)
    base = with_file(docs(), "x.json", {"a": [{"b": 1, "c": 2}]})
    head = with_file(docs(), "x.json", {"a": [{"c": 2}]})
    rep = removals(tmp_path, base, head, "x.json:a.0.b", name="digit")
    assert rep.violations == ["x.json: removed at a/0/b (no --allow-removed spec covers it)"]
    assert "allowance x.json:a.0.b: 0 removed" in ed.render(rep)


ROW_SHAPES = {
    "checks": ("quality_report.json:checks[name=gone.*]", "checks",
               lambda v: {"name": v, "status": "pass", "detail": "ok"}, "status",
               ("keep.one", "gone.a", "keep.two", "gone.b"), "gone.c"),
    "files": ("quality_report.json:files[parser=old_*]", "files",
              lambda v: {"file": f"{v}.csv", "parser": v, "rows": 3}, "rows",
              ("new_one", "old_a", "new_two", "old_b"), "old_c"),
}


@pytest.mark.parametrize("shape", ROW_SHAPES.values(), ids=ROW_SHAPES.keys())
def test_removals_allows_listed_rows_by_field_glob(tmp_path, shape):
    spec, key, make, other, (k1, g1, k2, g2), g3 = shape

    def side(*rows) -> dict:
        d = docs()
        d["quality_report.json"][key] = [make(r) if isinstance(r, str) else r for r in rows]
        return d

    base = side(k1, g1, k2, g2)

    def run(head: dict, name: str) -> ed.Report:
        return removals(tmp_path, base, head, spec, name=name)

    ok = run(side(k1, k2), "ok")
    assert ok.ok, ok.violations
    assert f"allowance {spec}: 2 removed" in ed.render(ok)
    one = run(side(k1, g1, k2), "kept")
    assert one.ok, one.violations
    assert f"allowance {spec}: 1 removed" in ed.render(one)
    new_or_changed = [f"quality_report.json: {key} row matching {spec} is new or changed in head"]
    assert run(side(k1, dict(make(g1), **{other: "x"}), k2), "changed").violations == new_or_changed
    assert run(side(k1, k2, g3), "new").violations == new_or_changed
    assert run(side(k1, g1, g1, k2), "twice").violations == new_or_changed      # a distinct base row each
    assert run(side(k1), "dropped").violations == [f"quality_report.json: changed at {key}"]
    swapped = run(side(k2, k1), "swapped").violations
    assert swapped and all(v.startswith(f"quality_report.json: changed at {key}/") for v in swapped), swapped
    assert run(side(k1, dict(make(k2), **{other: "x"})), "kept-changed").violations == [
        f"quality_report.json: changed at {key}/1/{other}"]


def test_removals_rows_a_glob_keeps_stay_where_they_were(tmp_path):
    spec = "quality_report.json:checks[name=gone.*]"
    base = with_checks(docs(), "keep.one", "gone.a", "keep.two", "gone.b")
    assert removals(tmp_path, base, with_checks(docs(), "keep.one", "gone.a", "keep.two", "gone.b"), spec,
                    name="all-kept").ok
    moved = [f"quality_report.json: checks row matching {spec} moved in head"]
    assert removals(tmp_path, base, with_checks(docs(), "gone.a", "keep.one", "keep.two"), spec,
                    name="before").violations == moved
    assert removals(tmp_path, base, with_checks(docs(), "keep.one", "keep.two", "gone.b", "gone.a"), spec,
                    name="swapped").violations == moved


def test_removals_takes_two_row_specs_on_one_list(tmp_path):
    first, second = "quality_report.json:checks[name=m.*]", "quality_report.json:checks[name=a.*]"
    base = with_checks(docs(), "keep", "m.one", "a.one", "keep.two", "a.two")
    rep = removals(tmp_path, base, with_checks(docs(), "keep", "keep.two"), first, second, name="both")
    assert rep.ok, rep.violations
    out = ed.render(rep)
    assert f"allowance {first}: 1 removed" in out and f"allowance {second}: 2 removed" in out
    assert "quality_report.json: removed checks rows x3" in out
    # a row both globs match belongs to the first spec only
    overlap = "quality_report.json:checks[name=*.one]"
    rep = removals(tmp_path, base, with_checks(docs(), "keep", "keep.two"), first, second, overlap, name="overlap")
    assert rep.ok and f"allowance {overlap}: 0 removed" in ed.render(rep)
    # the same spec twice is one allowance
    rep = removals(tmp_path, base, with_checks(docs(), "keep", "a.one", "keep.two", "a.two"), first, first,
                   name="twice")
    assert rep.ok and ed.render(rep).count(f"allowance {first}:") == 1


def test_removals_a_glob_that_matches_nothing_passes_with_zero(tmp_path):
    rep = removals(tmp_path, docs(), docs(), "quality_report.json:checks[name=nothing.*]", "x.json",
                   "recurring.json:nothing")
    assert rep.ok, rep.violations
    out = ed.render(rep)
    for spec in ("quality_report.json:checks[name=nothing.*]", "x.json", "recurring.json:nothing"):
        assert f"allowance {spec}: 0 removed" in out
    assert "quality_report.json: identical" in out


@pytest.mark.parametrize("spec", [
    "quality_report.json:missing[name=*]",                       # no such key
    "nothing.json:rows[name=*]",                                 # no such file
    "accounts.json:accounts.0[name=*]",                          # a digit KEY is no list index
    "quality_report.json:top_unmapped_vendors.0[vendor=*]",      # likewise
    "manifest.json:transaction_count[name=*]",                   # a number
])
def test_removals_a_row_spec_must_name_a_list_in_base(tmp_path, spec):
    rep = removals(tmp_path, docs(), docs(), spec)
    assert rep.violations == [f"allowance {spec}: not a list of objects in base"]


@pytest.mark.parametrize("value", [{"a": 1}, ["a", "b"], [{"name": "a"}, 3], "text", None])
def test_removals_a_row_spec_over_anything_but_a_list_of_objects_is_one_violation(tmp_path, value):
    spec = "x.json:rows[name=*]"
    base, head = with_file(docs(), "x.json", {"rows": value}), with_file(docs(), "x.json", {"rows": value})
    rep = removals(tmp_path, base, head, spec)
    assert rep.violations == [f"allowance {spec}: not a list of objects in base"]


def test_removals_a_row_spec_is_not_a_key_or_file_allowance(tmp_path):
    spec = "quality_report.json:checks[name=*]"
    base = docs()
    no_key = docs()
    del no_key["quality_report.json"]["checks"]
    assert removals(tmp_path, base, no_key, spec, name="key").violations == [
        "quality_report.json: removed at checks (no --allow-removed spec covers it)"]
    no_file = docs()
    del no_file["quality_report.json"]
    no_file["manifest.json"]["files"].remove("quality_report.json")
    assert not removals(tmp_path, base, no_file, spec, name="file").ok
    # rows in head that are not objects are never taken out: they meet the walk
    odd = docs()
    odd["quality_report.json"]["checks"] = ["x"]
    assert not removals(tmp_path, base, odd, spec, name="odd").ok
    # a row without the field is matched as "None"
    gone = with_checks(docs(), {"status": "pass"})
    assert removals(tmp_path, gone, with_checks(docs()), "quality_report.json:checks[name=None]", name="none").ok


@pytest.mark.parametrize("spec,expected", [
    ("x.json", ("x.json",)),
    ("a-b_c.1.json", ("a-b_c.1.json",)),
    ("quality_report.json:checks", ("quality_report.json", ("checks",))),
    ("assumptions.json:a.b-c.d_e.0", ("assumptions.json", ("a", "b-c", "d_e", "0"))),
    ("quality_report.json:checks[name=gone.*]", ("quality_report.json", ("checks",), "name", "gone.*")),
    ("x.json:a.b[f-1_x=a [b*?=c]", ("x.json", ("a", "b"), "f-1_x", "a [b*?=c")),
    ("manifest.json:prices_fetched_at", ("manifest.json", ("prices_fetched_at",))),
    ("manifest.json:transaction_count", ("manifest.json", ("transaction_count",))),
])
def test_allow_removed_grammar(spec, expected):
    a = ed.parse_allowance(spec)
    assert a == ed.Allowance(*expected) and a.spec == spec


@pytest.mark.parametrize("spec", [
    "", "x", "x.json:", "x.json:a..b", "x.json:a[name]", "x.json:a[=g]", "x.json:a[name=]",
    "transactions.json", "transactions.json:transactions[id=*]", "transactions.json:count",
    "manifest.json:files", "manifest.json:schema_version", "manifest.json:generated_at",
    "manifest.json:simplefin_fetched_at", "manifest.json:files[name=*]", "manifest.json:schema_version.x",
    "manifest.json",                        # never absent: compare refuses a dir without one
    ".json", "x.JSON", "x.json.bak", "dir/x.json", " x.json", "x.json ", "x.json:a.", "x.json:.a", "x.json:a b",
    "x.json[name=g]", "x.json:a[name=g]]", "x.json:a[name=g", "x.json:a[name=g]x", "x.json:a[na.me=g]",
    "x.json:a:b", "x.json\n",
])
def test_allow_removed_grammar_refusals(spec):
    with pytest.raises(ValueError) as e:
        ed.parse_allowance(spec)
    assert repr(spec) in str(e.value)


def test_allow_removed_needs_the_removals_policy(tmp_path):
    for policy in (p for p in ed.POLICIES if p not in ("removals", "refactor")):
        kw = {"known_vendors": KNOWN} if policy == "vendors-fallback-only" else {}
        with pytest.raises(ValueError, match="removals and refactor"):
            compare(subdir(tmp_path, policy), docs(), docs(), policy, allow_removed=["x.json"], **kw)
    for policy in ("removals", "refactor"):
        rep = compare(subdir(tmp_path, f"{policy}-ok"), docs(), docs(), policy, allow_removed=["x.json"])
        assert rep.ok and "allowance x.json: 0 removed" in ed.render(rep)
    with pytest.raises(ValueError, match="'bad'"):
        compare(subdir(tmp_path, "bad"), docs(), docs(), "removals", allow_removed=["bad"])
    with pytest.raises(ValueError, match="not one string"):          # not read character by character
        compare(subdir(tmp_path, "str"), docs(), docs(), "removals", allow_removed="x.json")


def test_cli_allow_removed_needs_removals_and_a_valid_spec(tmp_path, capsys):
    b, h = write_dir(tmp_path, "base", docs()), write_dir(tmp_path, "head", docs())
    assert ed.main([str(b), str(h), "--expect", "identical", "--allow-removed", "x.json"]) == ed.EXIT_USAGE
    assert ed.main([str(b), str(h), "--expect", "removals", "--allow-removed", "bad"]) == ed.EXIT_USAGE
    assert ed.main([str(b), str(h), "--expect", "removals", "--allow-removed", "x.json",
                    "--allow-removed", "transactions.json"]) == ed.EXIT_USAGE
    out, err = capsys.readouterr()
    assert "RESULT" not in out and "'bad'" in err and "'transactions.json'" in err
    # every --allow-removed is taken
    gone = with_file(docs(), "x.json", {"rows": []})
    with_file(gone, "y.json", {"rows": []})
    b2 = write_dir(tmp_path, "base2", gone)
    assert ed.main([str(b2), str(h), "--expect", "removals"]) == ed.EXIT_VIOLATION
    assert ed.main([str(b2), str(h), "--expect", "removals", "--allow-removed", "x.json"]) == ed.EXIT_VIOLATION
    capsys.readouterr()
    assert ed.main([str(b2), str(h), "--expect", "removals",
                    "--allow-removed", "x.json", "--allow-removed", "y.json"]) == ed.EXIT_OK
    out = capsys.readouterr().out
    assert "allowance x.json: 1 removed" in out and "allowance y.json: 1 removed" in out
    assert "RESULT: OK (removals)" in out


def test_render_names_allowances_with_counts_and_no_values(tmp_path):
    spec = "quality_report.json:checks[name=gone.*]"
    base = with_checks(docs(), "x", {"name": "gone.a", "status": "warn", "detail": "1234.56"})
    with_file(base, "assumptions.json", {"limit": 4321.09, "keep": 1})
    head = with_file(with_checks(docs(), "x"), "assumptions.json", {"keep": 1})
    rep = removals(tmp_path, base, head, spec, "assumptions.json:limit", name="ok")
    out = ed.render(rep)
    assert f"allowance {spec}: 1 removed" in out and "allowance assumptions.json:limit: 1 removed" in out
    assert "RESULT: OK (removals)" in out
    assert "1234.56" not in out and "4321.09" not in out and "warn" not in out
    # a failing comparison names the spec and the path, never the row
    bad = with_checks(docs(), "x", {"name": "gone.a", "status": "warn", "detail": "9876.54"})
    with_file(bad, "assumptions.json", {"keep": 2})
    out = ed.render(removals(tmp_path, base, bad, spec, "assumptions.json:limit", name="bad"))
    assert "is new or changed in head" in out and "assumptions.json: changed at keep" in out
    assert "9876.54" not in out and "1234.56" not in out and "4321.09" not in out


# ------------------------------------------------------------------ refactor
# Each comparison gets its own directory under tmp_path (`name`). Invented vendors, ids and 2030 dates only.

def refactor(tmp_path: Path, base: dict, head: dict, *, removed=(), changed=(), name: str = "r") -> ed.Report:
    return compare(subdir(tmp_path, name), base, head, "refactor", allow_removed=removed, allow_changed=changed)


def allowance_lines(rep: ed.Report) -> list[str]:
    return [line for line in ed.render(rep).splitlines() if line.startswith("allowance")]


def test_refactor_passes_identical_and_additive(tmp_path):
    same = refactor(tmp_path, docs(), docs(), name="same")
    assert same.ok, same.violations
    assert ed.render(same).splitlines()[-1] == "RESULT: OK (refactor)"
    base, head = versioned(12, 13)
    head["accounts.json"]["accounts"][0]["opened"] = {"on": "2030-01-02", "by": "Alex"}   # a new key at depth
    tx(head, 0)["parent_id"] = None                                                      # a new key on a row
    with_file(head, "household.json", {"name": "Sample"})                     # a new file in manifest.files
    rep = refactor(tmp_path, base, head, name="additive")
    assert rep.ok, rep.violations
    assert "manifest.json: schema_version 12->13" in ed.render(rep)
    # a new transaction row is data, not schema
    head = docs()
    head["transactions.json"]["transactions"].append(row(9))
    assert refactor(tmp_path, docs(), head, name="new-row").violations == [
        "transactions.json: added at transactions/tx_0009"]


def test_refactor_fails_on_a_changed_value_and_names_the_path(tmp_path, capsys):
    head = docs()
    head["accounts.json"]["accounts"][0]["name"] = "Sample Checking"
    b, h = write_dir(tmp_path, "base", docs()), write_dir(tmp_path, "head", head)
    assert ed.main([str(b), str(h), "--expect", "refactor"]) == ed.EXIT_VIOLATION
    out = capsys.readouterr().out
    assert "RESULT: 1 change(s) outside policy refactor" in out
    assert "  ! accounts.json: changed at accounts/0/name" in out
    assert "Sample Checking" not in out
    # a removed key, a longer list and a reordered list are not additive either
    two = [{"id": "demo_checking_0000", "name": "Sample"}, {"id": "demo_savings_0000", "name": "Sample Savings"}]
    for case, base_accounts, head_accounts, found in (
            ("removed-key", [{"id": "chk", "name": "Chk"}], [{"id": "chk"}],
             ["accounts.json: removed at accounts/0/name"]),
            ("longer", two[:1], two, ["accounts.json: changed at accounts"]),
            ("shorter", two, two[:1], ["accounts.json: changed at accounts"]),
            ("reordered", two, two[::-1], ["accounts.json: changed at accounts/0/id",
                                           "accounts.json: changed at accounts/0/name",
                                           "accounts.json: changed at accounts/1/id",
                                           "accounts.json: changed at accounts/1/name"])):
        base, head = docs(), docs()
        base["accounts.json"]["accounts"], head["accounts.json"]["accounts"] = base_accounts, head_accounts
        assert refactor(tmp_path, base, head, name=case).violations == found, case


def test_checks_compare_as_a_multiset_of_name_and_status(tmp_path):
    warn = {"name": "b.one", "status": "warn", "detail": "3 rows unmatched"}
    base = with_checks(docs(), "a.one", "a.two", warn)
    # order and details never count
    moved = with_checks(docs(), dict(warn, detail="4 rows unmatched"), "a.two",
                        {"name": "a.one", "status": "pass", "detail": "other words"})
    rep = refactor(tmp_path, base, moved, name="reordered")
    assert rep.ok, rep.violations
    out = ed.render(rep)
    assert "×" not in out and "rows unmatched" not in out and "other words" not in out
    assert not compare(subdir(tmp_path, "additive"), base, moved, "schema-additive").ok     # only refactor
    # a status flip, a removed check and an added check each fail
    flipped = with_checks(docs(), {"name": "a.one", "status": "fail", "detail": "ok"}, "a.two", warn)
    assert refactor(tmp_path, base, flipped, name="flip").violations == [
        "checks: a.one fail ×0 → ×1", "checks: a.one pass ×1 → ×0"]
    assert refactor(tmp_path, base, with_checks(docs(), "a.one", warn), name="removed").violations == [
        "checks: a.two pass ×1 → ×0"]
    assert refactor(tmp_path, base, with_checks(docs(), "a.one", "a.two", warn, "c.new"), name="added").violations == [
        "checks: c.new pass ×0 → ×1"]
    assert refactor(tmp_path, base, with_checks(docs(), "a.one", "a.one", "a.two", warn), name="twice").violations == [
        "checks: a.one pass ×1 → ×2"]
    # an allowance on the check name admits what it matches and nothing else
    spec = "quality_report.json:checks[name=validate.*]"
    ok = refactor(tmp_path, base, with_checks(docs(), "a.one", "a.two", warn, "validate.accounts"),
                  changed=[spec], name="allowed")
    assert ok.ok, ok.violations
    out = ed.render(ok)
    assert "checks: validate.accounts pass ×0 → ×1" in out and f"allowance-changed {spec}: 1 changed" in out
    other = refactor(tmp_path, base, with_checks(docs(), "a.one", "a.two", warn, "validate.accounts", "other.check"),
                     changed=[spec], name="other")
    assert other.violations == ["checks: other.check pass ×0 → ×1"]
    # a status flip is two (name, status) differences: one allowance on the name covers both, counted once
    flip_spec = "quality_report.json:checks[name=a.*]"
    rep = refactor(tmp_path, base, flipped, changed=[flip_spec], name="flip-allowed")
    assert rep.ok, rep.violations
    out = ed.render(rep)
    assert "checks: a.one fail ×0 → ×1" in out and "checks: a.one pass ×1 → ×0" in out
    assert f"allowance-changed {flip_spec}: 1 changed" in out
    assert "quality_report.json: changed checks (name, status) x2" in out
    # FIELD may also be the status; any other field is a usage error (it could only match nothing)
    assert refactor(tmp_path, base, flipped, changed=["quality_report.json:checks[status=fail]"],
                    name="by-status").violations == ["checks: a.one pass ×1 → ×0"]
    with pytest.raises(ValueError, match="name or status"):
        refactor(tmp_path, base, flipped, changed=["quality_report.json:checks[detail=*]"], name="by-detail")
    # the key path covers every check
    rep = refactor(tmp_path, base, with_checks(docs(), "z.new"), changed=["quality_report.json:checks"], name="all")
    assert rep.ok and "allowance-changed quality_report.json:checks: 3 changed" in ed.render(rep)


def with_series(d: dict, *totals: float) -> dict:
    return with_file(d, "assets_timeseries.json", {"currency": "USD", "snapshots": [
        {"date": f"2030-{m:02d}-28", "total": t} for m, t in enumerate(totals, 1)]})


def test_allow_changed_key_path_covers_values_and_list_growth(tmp_path):
    spec = "assets_timeseries.json:snapshots"
    base = with_series(docs(), 1000.0, 1100.0)
    reordered = with_series(docs(), 1000.0, 1100.0)
    reordered["assets_timeseries.json"]["snapshots"].reverse()
    gone = with_series(docs())
    del gone["assets_timeseries.json"]["snapshots"]
    for case, head, n in (("grown", with_series(docs(), 1000.0, 1100.0, 1250.0), 1),
                          ("changed", with_series(docs(), 1000.0, 1175.0), 1),
                          ("grown-and-changed", with_series(docs(), 1000.0, 1175.0, 1250.0), 1),
                          ("shrunk", with_series(docs(), 1000.0), 1),
                          ("reordered", reordered, 4),
                          ("key-gone", gone, 1)):
        rep = refactor(tmp_path, base, head, changed=[spec], name=case)
        assert rep.ok, (case, rep.violations)
        out = ed.render(rep)
        assert allowance_lines(rep) == [f"allowance-changed {spec}: {n} changed"], case
        assert "1175" not in out and "1250" not in out and "1100" not in out
    # without the allowance the same change is a violation
    assert refactor(tmp_path, base, with_series(docs(), 1000.0, 1175.0), name="none").violations == [
        "assets_timeseries.json: changed at snapshots/1/total"]
    # a sibling key and another file stay under the additive rules
    sibling = with_series(docs(), 1000.0, 1250.0, 1300.0)
    sibling["assets_timeseries.json"]["currency"] = "EUR"
    assert refactor(tmp_path, base, sibling, changed=[spec], name="sibling").violations == [
        "assets_timeseries.json: changed at currency"]
    other = with_series(docs(), 1000.0, 1250.0, 1300.0)
    other["accounts.json"]["accounts"][0]["name"] = "Sample"
    assert refactor(tmp_path, base, other, changed=[spec], name="other-file").violations == [
        "accounts.json: changed at accounts/0/name"]
    # a key path never reaches into a list
    deep = "assets_timeseries.json:snapshots.0.total"
    rep = refactor(tmp_path, base, with_series(docs(), 1050.0, 1100.0), changed=[deep], name="deep")
    assert rep.violations == ["assets_timeseries.json: changed at snapshots/0/total"]
    assert allowance_lines(rep) == [f"allowance-changed {deep}: 0 changed"]


def with_items(d: dict, *items: tuple[str, str]) -> dict:
    d["recurring.json"]["items"] = [{"vendor": v, "account_id": "demo_checking_0000", "category": "Dining",
                                     "cadence": c} for v, c in items]
    return d


def test_allow_changed_row_filter(tmp_path):
    spec = "recurring.json:items[vendor=Sample*]"
    store, other, cafe, third = (("Sample Store", "monthly"), ("Other Shop", "monthly"),
                                 ("Sample Cafe", "weekly"), ("Third Place", "yearly"))
    base = with_items(docs(), store, other, cafe, third)

    def run(name: str, *items, removed=(), changed=(spec,)) -> ed.Report:
        return refactor(tmp_path, base, with_items(docs(), *items), removed=removed, changed=changed, name=name)

    # matching rows may change, appear, vanish or move, so the list may change length
    for case, items, n in (
            ("changed", (("Sample Store", "weekly"), other, cafe, third), 1),
            ("added", (store, other, cafe, third, ("Sample Market", "monthly")), 1),
            ("removed", (store, other, third), 1),
            ("moved", (cafe, other, store, third), 0),
            ("all", (("Sample Store", "yearly"), other, third, ("Sample Bakery", "weekly"),
                     ("Sample Deli", "monthly")), 3)):
        rep = run(case, *items)
        assert rep.ok, (case, rep.violations)
        assert allowance_lines(rep) == [f"allowance-changed {spec}: {n} changed"], case
        out = ed.render(rep)
        assert "Sample" not in out.replace(spec, "") and "weekly" not in out and "yearly" not in out
    assert "recurring.json: changed items rows x1" in ed.render(run("note", store, other, third))
    # every other row is compared pairwise as usual
    assert run("other-changed", store, ("Other Shop", "weekly"), cafe, third).violations == [
        "recurring.json: changed at items/0/cadence"]
    assert run("other-added", store, other, cafe, third, ("Fourth Place", "monthly")).violations == [
        "recurring.json: changed at items"]
    assert run("other-removed", store, cafe, third).violations == ["recurring.json: changed at items"]
    swapped = run("other-swapped", store, third, cafe, other).violations
    assert swapped and all(v.startswith("recurring.json: changed at items/") for v in swapped), swapped
    # a row whose FIELD leaves the glob is a matching row gone and a new other row
    assert run("left-the-glob", ("Store Sample", "monthly"), other, cafe, third).violations == [
        "recurring.json: changed at items"]
    # the spec must name a list of objects in base
    assert run("no-list", store, other, cafe, third, changed=("recurring.json:nothing[vendor=*]",)).violations == [
        "allowance recurring.json:nothing[vendor=*]: not a list of objects in base"]
    # a removal spec on the same list takes its rows first and keeps its own rules
    gone = "recurring.json:items[vendor=Sample Cafe]"
    rep = run("both", ("Sample Store", "weekly"), other, third, removed=(gone,))
    assert rep.ok, rep.violations
    assert allowance_lines(rep) == [f"allowance {gone}: 1 removed", f"allowance-changed {spec}: 1 changed"]
    assert run("both-changed", store, other, ("Sample Cafe", "yearly"), third, removed=(gone,)).violations == [
        f"recurring.json: items row matching {gone} is new or changed in head"]


def test_allow_changed_transaction_field(tmp_path, capsys):
    spec = "transactions.json:earner"
    head = docs()
    tx(head, 0)["earner"] = "Alex"
    tx(head, 2)["earner"] = "Sam"
    rep = refactor(tmp_path, docs(), head, changed=[spec], name="ok")
    assert rep.ok, rep.violations
    assert allowance_lines(rep) == [f"allowance-changed {spec}: 2 changed"]
    assert "Alex" not in ed.render(rep) and "Sam" not in ed.render(rep)
    assert refactor(tmp_path, docs(), head, name="none").violations == [
        "transactions.json: changed at transactions/tx_0000/earner",
        "transactions.json: changed at transactions/tx_0002/earner"]
    # another field on the same row is not covered
    tx(head, 0)["category"] = "Groceries"
    assert refactor(tmp_path, docs(), head, changed=[spec], name="category").violations == [
        "transactions.json: changed at transactions/tx_0000/category"]
    # rows are never added or removed
    added = docs()
    added["transactions.json"]["transactions"].append(row(9, earner="Alex"))
    assert refactor(tmp_path, docs(), added, changed=[spec], name="added").violations == [
        "transactions.json: added at transactions/tx_0009"]
    removed = docs()
    removed["transactions.json"]["transactions"].pop(0)
    assert refactor(tmp_path, docs(), removed, changed=[spec], name="removed").violations == [
        "transactions.json: removed at transactions/tx_0000"]
    # FIELD is a row field, never the file's own keys
    counted = docs()
    counted["transactions.json"]["count"] = 5
    assert refactor(tmp_path, docs(), counted, changed=["transactions.json:count"], name="count").violations == [
        "transactions.json: changed at count"]
    # a row counts once however much of its field moved
    base, tagged = docs(), docs()
    tx(base, 1)["tags"] = ["trip", "work"]
    tx(tagged, 1)["tags"] = ["work", "trip"]
    rep = refactor(tmp_path, base, tagged, changed=["transactions.json:tags"], name="tags")
    assert rep.ok and allowance_lines(rep) == ["allowance-changed transactions.json:tags: 1 changed"]
    # transactions.json in any other form is a usage error naming the accepted form
    b, h = write_dir(tmp_path, "cli-base", docs()), write_dir(tmp_path, "cli-head", docs())
    capsys.readouterr()
    for bad in ("transactions.json", "transactions.json:a.b", "transactions.json:transactions[id=*]"):
        assert ed.main([str(b), str(h), "--expect", "refactor", "--allow-changed", bad]) == ed.EXIT_USAGE
        out, err = capsys.readouterr()
        assert "RESULT" not in out and repr(bad) in err and "transactions.json:FIELD" in err


def test_allow_removed_works_under_refactor(tmp_path):
    base = with_file(docs(), "assumptions.json", {"limit": 250, "keep": 1})
    head = with_file(docs(), "assumptions.json", {"keep": 1})
    rep = refactor(tmp_path, base, head, removed=["assumptions.json:limit"], name="key")
    assert rep.ok, rep.violations
    assert allowance_lines(rep) == ["allowance assumptions.json:limit: 1 removed"]
    assert refactor(tmp_path, base, head, name="none").violations == ["assumptions.json: removed at limit"]
    # a whole file: manifest.files loses its name, and must not still list it
    rep = refactor(tmp_path, base, docs(), removed=["assumptions.json"], name="file")
    assert rep.ok and allowance_lines(rep) == ["allowance assumptions.json: 1 removed"]
    listed = docs()
    listed["manifest.json"]["files"] = list(base["manifest.json"]["files"])
    assert refactor(tmp_path, base, listed, removed=["assumptions.json"], name="listed").violations == [
        "manifest.json: files still lists assumptions.json, which head does not have"]
    # quality checks by glob: removals only, never an addition
    spec = "quality_report.json:checks[name=gone.*]"
    checked = with_checks(docs(), "keep", "gone.a")
    rep = refactor(tmp_path, checked, with_checks(docs(), "keep"), removed=[spec], name="checks")
    assert rep.ok and allowance_lines(rep) == [f"allowance {spec}: 1 removed"]
    assert refactor(tmp_path, checked, with_checks(docs(), "keep", "gone.a", "gone.b"), removed=[spec],
                    name="checks-added").violations == ["checks: gone.b pass ×0 → ×1"]
    # a feed's stamp may vanish under an --allow-removed spec only
    stamp = docs()
    del stamp["manifest.json"]["prices_fetched_at"]
    rep = refactor(tmp_path, docs(), stamp, removed=["manifest.json:prices_fetched_at"], name="stamp")
    assert rep.ok and allowance_lines(rep) == ["allowance manifest.json:prices_fetched_at: 1 removed"]
    with pytest.raises(ValueError, match="never allow-listed"):         # it could never take effect
        refactor(tmp_path, docs(), stamp, changed=["manifest.json:prices_fetched_at"], name="stamp-changed")


@pytest.mark.parametrize("policy", [p for p in ed.POLICIES if p != "refactor"])
def test_allow_changed_is_refused_under_other_policies(tmp_path, capsys, policy):
    rules = tmp_path / "vendor_rules.yaml"
    rules.write_text(RULES)
    b, h = write_dir(tmp_path, "base", docs()), write_dir(tmp_path, "head", docs())
    argv = [str(b), str(h), "--expect", policy, "--vendor-rules", str(rules)]
    assert ed.main(argv + ["--allow-changed", "accounts.json:accounts"]) == ed.EXIT_USAGE
    out, err = capsys.readouterr()
    assert "RESULT" not in out and "--allow-changed" in err and "refactor" in err
    assert ed.main(argv) == ed.EXIT_OK                       # the same comparison without the flag passes
    kw = {"known_vendors": KNOWN} if policy == "vendors-fallback-only" else {}
    with pytest.raises(ValueError, match="only under refactor"):
        compare(subdir(tmp_path, "lib"), docs(), docs(), policy, allow_changed=["accounts.json:accounts"], **kw)


def test_an_allowance_that_matches_nothing_reports_zero(tmp_path, capsys):
    removed = ["x.json", "recurring.json:nothing", "quality_report.json:checks[name=gone.*]"]
    changed = ["accounts.json:accounts", "recurring.json:items[vendor=Nothing*]", "transactions.json:earner",
               "quality_report.json:checks[name=nothing.*]"]
    rep = refactor(tmp_path, docs(), docs(), removed=removed, changed=changed)
    assert rep.ok, rep.violations
    assert allowance_lines(rep) == [*(f"allowance {s}: 0 removed" for s in removed),
                                    *(f"allowance-changed {s}: 0 changed" for s in changed)]
    # one line per allowance: the --allow-removed specs, then the --allow-changed specs, each in the order given
    b, h = write_dir(tmp_path, "base", docs()), write_dir(tmp_path, "head", docs())
    assert ed.main([str(b), str(h), "--expect", "refactor", "--allow-changed", "transactions.json:earner",
                    "--allow-removed", "x.json", "--allow-changed", "accounts.json:accounts",
                    "--allow-removed", "recurring.json:nothing"]) == ed.EXIT_OK
    out = capsys.readouterr().out
    assert [line for line in out.splitlines() if line.startswith("allowance")] == [
        "allowance x.json: 0 removed", "allowance recurring.json:nothing: 0 removed",
        "allowance-changed transactions.json:earner: 0 changed", "allowance-changed accounts.json:accounts: 0 changed"]
    assert out.splitlines()[-1] == "RESULT: OK (refactor)"


@pytest.mark.parametrize("spec,expected", [
    ("accounts.json:accounts", ("accounts.json", ("accounts",))),
    ("assumptions.json:a.b-c.d_e", ("assumptions.json", ("a", "b-c", "d_e"))),
    ("recurring.json:items[vendor=Sample*]", ("recurring.json", ("items",), "vendor", "Sample*")),
    ("quality_report.json:checks[name=validate.*]", ("quality_report.json", ("checks",), "name", "validate.*")),
    ("transactions.json:earner", ("transactions.json", ("earner",))),
    ("manifest.json:transaction_count", ("manifest.json", ("transaction_count",))),
])
def test_allow_changed_grammar(spec, expected):
    a = ed.parse_allowance(spec, flag="--allow-changed")
    assert a == ed.Allowance(*expected, kind="changed") and a.spec == spec


@pytest.mark.parametrize("spec,reason", [
    ("accounts.json", "a key path"),
    ("manifest.json", "manifest.json is never allow-listed"),
    ("transactions.json", "transactions.json:FIELD"),
    ("transactions.json:a.b", "transactions.json:FIELD"),
    ("transactions.json:transactions[id=*]", "transactions.json:FIELD"),
    ("transactions.json:earner[name=*]", "transactions.json:FIELD"),
    ("manifest.json:files", "never allow-listed"),
    ("manifest.json:schema_version", "never allow-listed"),
    ("manifest.json:generated_at", "never allow-listed"),
    ("manifest.json:simplefin_fetched_at", "never allow-listed"),
    ("", "expected"), ("x.json:a..b", "expected"), ("x.json:a[name=]", "expected"), ("x.json:a b", "expected"),
])
def test_allow_changed_grammar_refusals(spec, reason):
    with pytest.raises(ValueError) as e:
        ed.parse_allowance(spec, flag="--allow-changed")
    assert str(e.value).startswith(f"--allow-changed {spec!r}: ") and reason in str(e.value)


def test_parse_allowance_knows_two_flags():
    assert ed.parse_allowance("x.json") == ed.parse_allowance("x.json", flag="--allow-removed")
    assert ed.parse_allowance("x.json:a").kind == "removed"
    with pytest.raises(ValueError, match="--allow-other"):
        ed.parse_allowance("x.json:a", flag="--allow-other")


# ------------------------------------------------------- refactor: places, types and refusals

def with_recurring(d: dict, *items: dict) -> dict:
    d["recurring.json"]["items"] = [dict(i) for i in items]
    return d


def run_cli(tmp_path: Path, capsys, name: str, base: dict, head: dict, *args: str) -> tuple[int, str, str]:
    """`main` on a pair of dirs of its own: (exit code, stdout, stderr)."""
    root = subdir(tmp_path, name)
    capsys.readouterr()
    code = ed.main([str(write_dir(root, "base", base)), str(write_dir(root, "head", head)), *args])
    out, err = capsys.readouterr()
    return code, out, err


def gone_rows() -> tuple[dict, ...]:
    return tuple({"vendor": v, "c": 1} for v in ("Other", "Gone A", "Gone B", "Third"))


def test_refactor_reports_a_kept_spec_row_that_moved_even_when_other_rows_gained_keys(tmp_path, capsys):
    spec = "recurring.json:items[vendor=Gone*]"
    other, gone_a, gone_b, third = gone_rows()
    base = with_recurring(docs(), other, gone_a, gone_b, third)
    args = ("--expect", "refactor", "--allow-removed", spec)
    moved = f"  ! recurring.json: items row matching {spec} moved in head"
    # the kept row now stands before the row it followed, and that row gained a key
    code, out, _ = run_cli(tmp_path, capsys, "moved", base,
                           with_recurring(docs(), gone_a, dict(other, added=True), third), *args)
    assert code == ed.EXIT_VIOLATION and moved in out, out
    # moved past a later row while every other row gained a key
    code, out, _ = run_cli(tmp_path, capsys, "moved-later", base,
                           with_recurring(docs(), dict(other, added=True), dict(third, added=True), gone_a), *args)
    assert code == ed.EXIT_VIOLATION and moved in out, out
    # the same move with no key added
    code, out, _ = run_cli(tmp_path, capsys, "moved-plain", base, with_recurring(docs(), gone_a, other, third), *args)
    assert code == ed.EXIT_VIOLATION and moved in out, out
    # the kept row in its place passes: the added key is additive
    code, out, _ = run_cli(tmp_path, capsys, "in-place", base,
                           with_recurring(docs(), dict(other, added=True), gone_a, third), *args)
    assert code == ed.EXIT_OK and out.splitlines()[-1] == "RESULT: OK (refactor)", out
    assert f"allowance {spec}: 1 removed" in out
    # removals keeps its own reading: the added key is the one violation there, and no place check runs
    assert removals(tmp_path, base, with_recurring(docs(), gone_a, dict(other, added=True), third), spec,
                    name="removals").violations == ["recurring.json: added at items/0/added"]


BOOL_CASES = {"false->0": (False, 0), "true->1": (True, 1), "0->false": (0, False)}


@pytest.mark.parametrize("policy", ["identical", "schema-additive", "refactor"])
def test_a_boolean_never_equals_a_number(tmp_path, capsys, policy):
    def sides(old, new) -> tuple[dict, dict]:
        base, head = docs(), docs()
        tx(base, 0)["needs_review"], tx(head, 0)["needs_review"] = old, new
        base["accounts.json"]["accounts"][0]["active"], head["accounts.json"]["accounts"][0]["active"] = old, new
        return base, head

    for case, (old, new) in BOOL_CASES.items():
        code, out, _ = run_cli(tmp_path, capsys, case, *sides(old, new), "--expect", policy)
        assert code == ed.EXIT_VIOLATION, (case, out)
        assert "  ! transactions.json: changed at transactions/tx_0000/needs_review" in out, (case, out)
        assert "  ! accounts.json: changed at accounts/0/active" in out, (case, out)
    # 1 and 1.0 are one JSON number
    code, out, _ = run_cli(tmp_path, capsys, "1->1.0", *sides(1, 1.0), "--expect", policy)
    assert code == ed.EXIT_OK, out


def test_every_other_equality_site_tells_a_boolean_from_a_number(tmp_path):
    # vendors-fallback-only frees a vendor-string file only when the rest of it is the same
    base, head = docs(), docs()
    base["recurring.json"]["complete"], head["recurring.json"]["complete"] = False, 0
    assert compare(subdir(tmp_path, "vendor-rest"), base, head, "vendors-fallback-only",
                   known_vendors=KNOWN).violations == ["recurring.json: changed at complete"]
    base, head = docs(), docs()
    base["recurring.json"]["items"], head["recurring.json"]["items"] = True, 1       # not a list of rows
    assert compare(subdir(tmp_path, "vendor-list"), base, head, "vendors-fallback-only",
                   known_vendors=KNOWN).violations == ["recurring.json: changed at items"]
    # the fetched-at precheck
    base, head = docs(), docs()
    base["manifest.json"]["prices_fetched_at"], head["manifest.json"]["prices_fetched_at"] = False, 0
    assert stamps(base, head) == ["prices_fetched_at"]
    # manifest.files keeping every name it had
    base, head = docs(), docs()
    base["manifest.json"]["files"].append(0)
    head["manifest.json"]["files"] += [False, "household.json"]
    for policy in ("schema-additive", "refactor"):
        assert compare(subdir(tmp_path, f"files-{policy}"), base, head, policy).violations == [
            "manifest.json: changed at files"], policy
    # removals: manifest.files losing exactly the gone file
    base, head = with_file(docs(), "x.json", {"rows": []}), docs()
    base["manifest.json"]["files"].append(False)
    head["manifest.json"]["files"].append(0)
    assert removals(tmp_path, base, head, "x.json", name="files-removals").violations == [
        "manifest.json: changed at files"]


def test_check_keys_do_not_collide_across_types(tmp_path):
    # name and status count by their JSON text: null, a missing key and "None" are three values, 1 and "1"
    # are two, and so are true and "True"; each pair prints alike, so the lines say which side holds which
    for case, b_check, h_check, shown in (
            ("null-missing", {"name": None, "status": "pass"}, {"status": "pass"}, "None pass"),
            ("null-text", {"name": None, "status": "pass"}, {"name": "None", "status": "pass"}, "None pass"),
            ("missing-text", {"status": "pass"}, {"name": "None", "status": "pass"}, "None pass"),
            ("number-text", {"name": 1, "status": "pass"}, {"name": "1", "status": "pass"}, "1 pass"),
            ("bool-text", {"name": "a.one", "status": True}, {"name": "a.one", "status": "True"}, "a.one True")):
        rep = refactor(tmp_path, with_checks(docs(), "a.two", b_check), with_checks(docs(), "a.two", h_check),
                       name=case)
        assert rep.violations == [f"checks: {shown} ×0 → ×1", f"checks: {shown} ×1 → ×0"], case
    # the same typed values in another order still pass
    same = [{"name": 1, "status": True}, {"status": None}, {"name": None, "status": "pass"}, {}]
    rep = refactor(tmp_path, with_checks(docs(), *same), with_checks(docs(), *same[::-1]), name="same")
    assert rep.ok, rep.violations


def test_allow_changed_key_path_matches_components_not_prefixes(tmp_path):
    spec = "assumptions.json:x.y"
    base = with_file(docs(), "assumptions.json", {"x": {"y": {"deep": 1}, "yy": 1, "z": 1}})

    def run(name: str, doc: dict) -> ed.Report:
        return refactor(tmp_path, base, with_file(docs(), "assumptions.json", doc), changed=[spec], name=name)

    assert run("yy", {"x": {"y": {"deep": 1}, "yy": 2, "z": 1}}).violations == ["assumptions.json: changed at x/yy"]
    assert run("z", {"x": {"y": {"deep": 1}, "yy": 1, "z": 2}}).violations == ["assumptions.json: changed at x/z"]
    assert run("x-scalar", {"x": 5}).violations == ["assumptions.json: changed at x"]
    assert run("x-gone", {}).violations == ["assumptions.json: removed at x"]
    deep = run("deep", {"x": {"y": {"deep": 2}, "yy": 1, "z": 1}})
    assert deep.ok, deep.violations
    assert allowance_lines(deep) == [f"allowance-changed {spec}: 1 changed"]


def test_checks_fall_back_to_the_strict_walk_when_malformed(tmp_path):
    base = with_checks(docs(), "a.one", "a.two")
    # checks missing from head
    head = docs()
    del head["quality_report.json"]["checks"]
    assert refactor(tmp_path, base, head, name="missing").violations == ["quality_report.json: removed at checks"]
    # not a list, on either side
    for case, value in (("object", {"a.one": "pass"}), ("text", "pass")):
        odd = docs()
        odd["quality_report.json"]["checks"] = value
        for side, b, h in (("head", base, odd), ("base", odd, base)):
            assert refactor(tmp_path, b, h, name=f"{side}-{case}").violations == [
                "quality_report.json: changed at checks"], (side, case)

    def stray(*rows) -> dict:
        d = with_checks(docs(), *rows)
        d["quality_report.json"]["checks"].append("stray")
        return d

    # a non-object element: order and details count, element by element, and no multiset line is printed
    rep = refactor(tmp_path, stray("a.one", "a.two"), stray("a.two", "a.one"), name="reordered")
    assert rep.violations == ["quality_report.json: changed at checks/0/name",
                              "quality_report.json: changed at checks/1/name"]
    assert "×" not in ed.render(rep)
    detail = stray({"name": "a.one", "status": "pass", "detail": "other words"}, "a.two")
    assert refactor(tmp_path, stray("a.one", "a.two"), detail, name="detail").violations == [
        "quality_report.json: changed at checks/0/detail"]
    # on one side only
    one_side = with_checks(docs(), "a.two", "a.one", "stray")
    assert refactor(tmp_path, stray("a.one", "a.two"), one_side, name="one-side").violations == [
        "quality_report.json: changed at checks/0/name", "quality_report.json: changed at checks/1/name",
        "quality_report.json: changed at checks/2"]


def test_a_check_entry_without_name_or_status_is_still_counted(tmp_path):
    no_name, no_status, neither = ({"status": "pass", "detail": "x"}, {"name": "b.one", "detail": "x"},
                                   {"detail": "x"})
    base = with_checks(docs(), "a.one", no_name, no_status, neither)
    for case, kept, line in (("no-name", (no_status, neither), "checks: None pass ×1 → ×0"),
                             ("no-status", (no_name, neither), "checks: b.one None ×1 → ×0"),
                             ("neither", (no_name, no_status), "checks: None None ×1 → ×0")):
        assert refactor(tmp_path, base, with_checks(docs(), "a.one", *kept), name=case).violations == [line]
    added = with_checks(docs(), "a.one", no_name, no_status, neither, {})
    assert refactor(tmp_path, base, added, name="added").violations == ["checks: None None ×1 → ×2"]
    # their details are free like any other check's
    reworded = with_checks(docs(), dict(neither, detail="y"), no_status, dict(no_name, detail="y"), "a.one")
    rep = refactor(tmp_path, base, reworded, name="reworded")
    assert rep.ok, rep.violations


def test_allow_changed_transaction_field_also_covers_the_field_appearing_or_vanishing(tmp_path):
    spec = "transactions.json:earner"
    gone = docs()
    for r in gone["transactions.json"]["transactions"]:
        del r["earner"]
    rep = refactor(tmp_path, docs(), gone, changed=[spec], name="vanished")
    assert rep.ok, rep.violations
    assert allowance_lines(rep) == [f"allowance-changed {spec}: 4 changed"]
    assert refactor(tmp_path, docs(), gone, name="vanished-unallowed").violations == [
        f"transactions.json: removed at transactions/tx_000{i}/earner" for i in range(4)]
    appeared = copy.deepcopy(gone)
    tx(appeared, 1)["earner"] = "Sam"
    rep = refactor(tmp_path, gone, appeared, changed=[spec], name="appeared")
    assert rep.ok, rep.violations
    assert allowance_lines(rep) == [f"allowance-changed {spec}: 1 changed"]
    # another field vanishing is not covered
    other = docs()
    del tx(other, 0)["category"]
    assert refactor(tmp_path, docs(), other, changed=[spec], name="other").violations == [
        "transactions.json: removed at transactions/tx_0000/category"]


def test_an_additive_key_on_a_kept_removal_spec_row_is_a_violation(tmp_path):
    spec = "recurring.json:items[vendor=Gone*]"
    other, gone_a, _, third = gone_rows()
    base = with_recurring(docs(), other, gone_a, third)
    rep = refactor(tmp_path, base, with_recurring(docs(), other, dict(gone_a, added=True), third), removed=[spec],
                   name="kept-row")
    assert rep.violations == [f"recurring.json: items row matching {spec} is new or changed in head"]
    # the same key on a row no spec takes is additive
    rep = refactor(tmp_path, base, with_recurring(docs(), dict(other, added=True), gone_a, third), removed=[spec],
                   name="other-row")
    assert rep.ok, rep.violations


def test_refactor_refuses_a_checks_row_spec_on_any_field_but_name_or_status(tmp_path, capsys):
    b, h = write_dir(tmp_path, "base", docs()), write_dir(tmp_path, "head", docs())
    bad = "quality_report.json:checks[detail=*]"
    for flag in ("--allow-changed", "--allow-removed"):
        assert ed.main([str(b), str(h), "--expect", "refactor", flag, bad]) == ed.EXIT_USAGE
        out, err = capsys.readouterr()
        assert "RESULT" not in out and f"{flag} {bad!r}" in err and "name or status" in err, err
        for ok in ("quality_report.json:checks[name=*]", "quality_report.json:checks[status=*]"):
            assert ed.main([str(b), str(h), "--expect", "refactor", flag, ok]) == ed.EXIT_OK
        capsys.readouterr()
    # removals reads the list row by row, where any field is a field
    assert ed.main([str(b), str(h), "--expect", "removals", "--allow-removed", bad]) == ed.EXIT_OK


def test_allow_changed_refuses_a_manifest_fetch_stamp(tmp_path, capsys):
    b, h = write_dir(tmp_path, "base", docs()), write_dir(tmp_path, "head", docs())
    for spec in ("manifest.json:prices_fetched_at", "manifest.json:broker_fetched_at",
                 "manifest.json:other_fetched_at.x"):
        assert ed.main([str(b), str(h), "--expect", "refactor", "--allow-changed", spec]) == ed.EXIT_USAGE
        out, err = capsys.readouterr()
        assert "RESULT" not in out and f"--allow-changed {spec!r}" in err, err
        assert "files, generated_at, schema_version, simplefin_fetched_at" in err and "_fetched_at stamp" in err
    # a removal allowance still lets a feed's stamp vanish
    assert ed.main([str(b), str(h), "--expect", "refactor", "--allow-removed", "manifest.json:prices_fetched_at"]
                   ) == ed.EXIT_OK


def test_refactor_does_not_call_checks_that_differ_identical(tmp_path):
    warn = {"name": "a.two", "status": "warn", "detail": "3 rows"}
    base = with_checks(docs(), "a.one", warn)
    for case, head in (("details", with_checks(docs(), "a.one", dict(warn, detail="4 rows"))),
                       ("order", with_checks(docs(), warn, "a.one"))):
        rep = refactor(tmp_path, base, head, name=case)
        assert rep.ok, rep.violations
        lines = ed.render(rep).splitlines()
        assert "quality_report.json: checks equal as (name, status)" in lines, case
        assert "quality_report.json: identical" not in lines, case
    same = refactor(tmp_path, base, copy.deepcopy(base), name="same")
    assert "quality_report.json: identical" in ed.render(same).splitlines()

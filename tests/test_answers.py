# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Review-inbox answers (etl/enrich/answers.py): load tolerance, precedence
(vendor < txn < YAML overrides), expiry semantics, and conflict verification.
Synthetic transactions only."""
from __future__ import annotations

import copy
import dataclasses
import json
import random
import re

import pytest
from conftest import ROOT, answer, answers_doc, make_txn

from etl.enrich import answers, overrides


def tx(desc="VENMO PAYMENT", cents=-3500, account="demo_checking_0000",
       vendor=None, category="Cash & Misc", subcategory="Uncategorized",
       needs_review=True, date="2026-04-09"):
    return make_txn(desc=desc, cents=cents, account=account, vendor=vendor,
                    category=category, subcategory=subcategory,
                    needs_review=needs_review, date=date)


def answer_txn(t, category="Dining", subcategory="Restaurants", **extra):
    return {**answer("txn", t, {"category": category, "subcategory": subcategory, "needs_review": False},
                     "2026-07-16T10:00:00Z"), **extra}


def answer_vendor(vendor: str, category="Kids", subcategory="Childcare"):
    return answer("vendor", vendor, {"category": category, "subcategory": subcategory, "needs_review": False},
                  "2026-07-16T09:00:00Z")


def doc(*entries):
    return answers_doc(*entries, updated_at="2026-07-16T10:00:00Z")


# -------------------------------------------------------------------- load

def test_load_missing_file_is_silent(tmp_path):
    d, checks = answers.load(tmp_path / "nope.json")
    assert d == {"answers": []} and checks == []


def test_load_corrupt_file_warns_but_never_fails(tmp_path):
    p = tmp_path / "answers.json"
    p.write_text("{ nope")
    d, checks = answers.load(p)
    assert d == {"answers": []}
    assert checks[0].status == "warn" and "corrupt" in checks[0].detail


def test_load_reads_text_that_is_not_unicode_or_nesting_past_the_parser_as_corrupt(tmp_path):
    """A lone surrogate anywhere (here the vendor and a key) would stop the ingest when it reaches the database, and a
    document nested past the parser would stop it at the load: each is a corrupt file, skipped with the warning."""
    p = tmp_path / "answers.json"
    for body in (json.dumps(doc(answer_vendor("\ud800 Brook"))), json.dumps({"answers": [], "\udfff": 1}),
                 "[" * 200_000):
        p.write_text(body, "utf-8")
        d, checks = answers.load(p)
        assert d == {"answers": []}
        assert [c.name for c in checks] == ["answers.load"] and "corrupt" in checks[0].detail


def test_load_reads_bytes_that_are_not_utf8_as_corrupt(tmp_path):
    """A hand-edited file holding bytes that are no UTF-8 text is a corrupt file like any other: the empty document
    and the one warning, never an exception that stops the ingest."""
    p = tmp_path / "answers.json"
    for body in (b"\xff", b'{"answers": [], "note": "caf\xe9"}'):
        p.write_bytes(body)
        d, checks = answers.load(p)
        assert d == {"answers": []}
        assert [(c.name, c.status) for c in checks] == [("answers.load", "warn")] and "corrupt" in checks[0].detail


def test_load_valid_roundtrip(tmp_path):
    p = tmp_path / "answers.json"
    body = doc(answer_vendor("Riley Brook"))
    p.write_text(json.dumps(body))
    d, checks = answers.load(p)
    assert d == body and checks == []


# ------------------------------------------------------------------- apply

def test_txn_answer_applies_by_uid_and_clears_review():
    t = tx()
    checks = answers.apply([t], doc(answer_txn(t)))
    assert (t.category, t.subcategory, t.needs_review) == ("Dining", "Restaurants", False)
    assert "1 transactions adjusted" in checks[0].detail


def test_vendor_answer_applies_case_insensitively():
    a, b = tx(vendor="Riley Brook"), tx(desc="OTHER", vendor="riley brook")
    other = tx(desc="X", vendor="Someone Else")
    answers.apply([a, b, other], doc(answer_vendor("Riley Brook")))
    assert a.category == b.category == "Kids"
    assert other.category == "Cash & Misc"          # untouched


def test_txn_scope_beats_vendor_scope():
    t = tx(vendor="Cedar Hollow Diner")
    answers.apply([t], doc(answer_vendor("Cedar Hollow Diner", category="Groceries"),
                           answer_txn(t, category="Dining")))
    assert t.category == "Dining"                    # specificity wins


def test_expired_txn_answer_is_one_info_check_not_a_warn():
    t = tx()
    gone = dict(answer_txn(t), key="txn:tx_gone", txn_id="tx_gone")
    checks = answers.apply([t], doc(gone))
    expired = [c for c in checks if c.name == "answers.expired"]
    assert len(expired) == 1 and expired[0].status == "info"
    assert not [c for c in checks if c.status == "warn"]


def test_vendor_answer_matching_nothing_is_silent():
    checks = answers.apply([tx(vendor="A")], doc(answer_vendor("Nobody Yet")))
    assert not [c for c in checks if c.name == "answers.expired"]


def test_answers_never_touch_internal_rows():
    t = tx(desc="VENMO CASHOUT", vendor="Venmo Transfer")
    t.type = "transfer"
    t.is_internal = True
    t.category, t.subcategory = "Internal", "transfer"
    answers.apply([t], doc(answer_vendor("Venmo Transfer", category="Dining"),
                           answer_txn(t, category="Dining")))
    assert (t.category, t.type) == ("Internal", "transfer")   # untouched


def test_vendor_answers_never_repaint_income_rows():
    salary = tx(desc="ACME PAYROLL", vendor="Acme Payroll (Example Co)",
                category="Income", subcategory="Salary - Alex", needs_review=False)
    salary.type = "income"
    purchase = tx(desc="ACME PAYROLL STORE", vendor="Acme Payroll (Example Co)")
    answers.apply([salary, purchase],
                  doc(answer_vendor("Acme Payroll (Example Co)", category="Shopping",
                                    subcategory=None)))
    assert salary.category == "Income"                        # protected
    assert purchase.category == "Shopping"                    # spending repainted


def test_set_whitelist_enforced_even_from_hand_edited_file():
    t = tx()
    evil = answer_txn(t)
    evil["set"]["type"] = "transfer"                 # not allowed from answers
    answers.apply([t], doc(evil))
    assert t.type == "purchase" and t.category == "Cash & Misc"  # entry dropped whole


# --------------------------------------------------- precedence vs overrides

def test_yaml_override_wins_and_verify_reports_conflict():
    t = tx(desc="P2P TAYLOR BROOKS")
    d = doc(answer_txn(t, category="Giving", subcategory="Gifts to People"))
    answers.apply([t], d)
    overrides.apply([t], [{"match": {"contains": "TAYLOR"},
                           "set": {"category": "Giving", "subcategory": "Charity"}}])
    assert (t.category, t.subcategory) == ("Giving", "Charity")     # config beat the answer
    conflict = answers.verify([t], d)
    assert conflict and conflict[0].name == "answers.conflict"
    assert f"txn:{t.uid}" in conflict[0].detail


def test_verify_quiet_when_answers_absorbed():
    t = tx()
    d = doc(answer_txn(t))
    answers.apply([t], d)
    assert answers.verify([t], d) == []


# ------------------------------------------------ new override match keys

def test_override_uid_and_vendor_match_keys():
    t1, t2 = tx(vendor="Photo Pages"), tx(desc="OTHER ROW", vendor="Photo Pages")
    overrides.apply([t1, t2], [{"match": {"uid": t1.uid},
                                "set": {"category": "Shopping"}}])
    assert t1.category == "Shopping" and t2.category == "Cash & Misc"
    overrides.apply([t1, t2], [{"match": {"vendor": "photo pages"},
                                "set": {"subcategory": "Gifts"}}])
    assert t1.subcategory == t2.subcategory == "Gifts"


# ------------------------------------------- tags-only answers (the bulk tag editor)
# The dashboard's tag editor sends {"tags": [...]} and NOTHING else - no category, no subcategory,
# no needs_review. Such a set adds tags and touches nothing else: it is not a category answer, so it
# cannot restamp the row's provenance, cannot pin its category against later rule fixes, and is never
# a conflict (only a category answer can be outranked).

def tags_only(t, *tags):
    return {"key": f"txn:{t.uid}", "scope": "txn", "txn_id": t.uid, "set": {"tags": list(tags)},
            "answered_at": "2026-07-16T10:00:00Z"}


def keyword_row(**over):
    return make_txn(desc="SYNTH TOY SHOP", cents=-3200, account="demo_card_0000", vendor="Synth Toy Shop",
                    category="Shopping", subcategory="Toys", needs_review=True, tags=["a"],
                    category_source="keyword", **over)


def test_a_tags_only_answer_unions_tags_and_leaves_category_provenance_and_review_alone():
    t = keyword_row()
    answers.apply([t], doc(tags_only(t, "kids", "a")))
    assert t.tags == ["a", "kids"]
    assert (t.category, t.subcategory, t.category_source, t.needs_review) == ("Shopping", "Toys", "keyword", True)
    # the control: a category answer on the same row does restamp it, so the asserts above can fail
    c = keyword_row()
    answers.apply([c], doc(answer_txn(c, category="Kids", subcategory="Childcare")))
    assert (c.category, c.category_source, c.needs_review) == ("Kids", "answer", False)


def test_a_tags_only_answer_does_not_pin_the_category_a_vendor_answer_still_sets_it():
    # a txn-scope CATEGORY answer beats a vendor-scope one (specificity) - that is the pin. A tags-only
    # txn answer names no category, so the vendor answer's category lands on the row beneath it
    only_tags, pinned = keyword_row(), keyword_row()
    answers.apply([only_tags], doc(answer_vendor("Synth Toy Shop", "Kids", "Childcare"), tags_only(only_tags, "kids")))
    assert (only_tags.category, only_tags.subcategory, only_tags.category_source) == ("Kids", "Childcare", "answer")
    assert only_tags.tags == ["a", "kids"]
    answers.apply([pinned], doc(answer_vendor("Synth Toy Shop", "Kids", "Childcare"),
                                answer_txn(pinned, category="Gifts", subcategory="Toys")))
    assert pinned.category == "Gifts"


def test_a_vendor_scope_tags_only_answer_tags_the_spend_rows_and_nothing_else():
    spend, income = keyword_row(), make_txn(desc="SYNTH TOY SHOP REFUND", cents=900, vendor="Synth Toy Shop",
                                            type="income", category="Income", subcategory="Other",
                                            category_source="keyword", tags=[])
    vendor_tags = {"key": "vendor:Synth Toy Shop", "scope": "vendor", "vendor": "Synth Toy Shop",
                   "set": {"tags": ["kids"]}, "answered_at": "2026-07-16T10:00:00Z"}
    answers.apply([spend, income], doc(vendor_tags))
    assert spend.tags == ["a", "kids"] and income.tags == []                 # vendor scope never repaints income rows
    assert (spend.category, spend.subcategory, spend.category_source) == ("Shopping", "Toys", "keyword")


def test_verify_never_reports_a_tags_only_answer_as_a_conflict():
    t = keyword_row()
    tags_doc = doc(tags_only(t, "kids"))
    answers.apply([t], tags_doc)
    overrides.apply([t], [{"match": {"uid": t.uid}, "set": {"category": "Gifts", "subcategory": "Toys"}}])
    assert t.category == "Gifts"                                             # config moved the category afterwards
    assert answers.verify([t], tags_doc) == []                               # ... which a tags-only answer never asserted
    cat_doc = doc(answer_txn(t, category="Kids", subcategory="Childcare"))   # control: a category answer IS outranked
    assert answers.verify([t], cat_doc)[0].name == "answers.conflict"


# ------------------------------------------------------- split purchases

def split_purchase(vendor="Synth Mart", cents=-30000):
    """a purchase of `vendor` split by rule: the parent flagged, two children with the parts"""
    parent = tx(desc="SYNTHMART", vendor=vendor, cents=cents, category="Groceries", subcategory=None, needs_review=False)
    parent.is_parent = True
    kids = [tx(desc="SYNTHMART", vendor=vendor, cents=c, category=cat, subcategory=sub, needs_review=False)
            for c, cat, sub in ((-21000, "Groceries", None), (-9000, "Shopping", "Home Goods"))]
    for i, k in enumerate(kids, start=1):
        k.parent_id, k.split_index, k.category_source = parent.uid, i, "split"
    return parent, kids


def test_a_vendor_answer_skips_split_children_repaints_the_vendors_plain_rows_and_is_logged_on_the_parent():
    parent, (a, b) = split_purchase()
    plain = tx(desc="SYNTHMART", vendor="Synth Mart", cents=-4000, category="Groceries", subcategory=None, needs_review=False)
    checks = answers.apply([parent, a, b, plain], doc(answer_vendor("Synth Mart", category="Kids", subcategory="Childcare")))
    assert (a.category, a.subcategory, a.category_source) == ("Groceries", None, "split")      # the parts keep their legs
    assert (b.category, b.subcategory) == ("Shopping", "Home Goods")
    assert plain.category == "Kids"                                                             # an ordinary row still follows
    assert parent.category == "Kids"                                                            # display-only, and said so:
    on_parent = [c for c in checks if c.name == "answers.on_split_parent"]
    assert len(on_parent) == 1 and on_parent[0].status == "warn" and on_parent[0].detail.startswith("1 answer landed on split parents")
    assert not answers._answerable(a, "vendor") and answers._answerable(a, "txn") and answers._answerable(plain, "vendor")


def test_a_txn_answer_on_a_part_repaints_that_part_only_and_on_a_parent_is_display_only():
    parent, (a, b) = split_purchase()
    answers.apply([parent, a, b], doc(answer_txn(a, category="Pets", subcategory=None)))
    assert (a.category, a.category_source) == ("Pets", "answer") and b.category == "Shopping" and parent.category == "Groceries"
    checks = answers.apply([parent, a, b], doc(answer_txn(parent, category="Pets", subcategory=None)))
    assert parent.category == "Pets" and b.category == "Shopping"
    assert any(c.name == "answers.on_split_parent" for c in checks)
    assert not any(c.name == "answers.on_split_parent" for c in answers.apply([parent, a, b], doc(answer_txn(b, category="Pets", subcategory=None))))


def test_verify_never_reports_an_answer_on_a_split_parent_as_a_conflict():
    parent, (a, b) = split_purchase()
    d = doc(answer_vendor("Synth Mart", category="Kids", subcategory="Childcare"))     # the parent reads Groceries: not what the answer says
    assert answers.verify([parent, a, b], d) == []
    plain = tx(desc="SYNTHMART", vendor="Synth Mart", cents=-4000, category="Groceries", subcategory=None, needs_review=False)
    assert [c.name for c in answers.verify([parent, a, b, plain], d)] == ["answers.conflict"]    # an ordinary row still can be one


def test_verify_still_reports_a_conflict_on_a_part_whose_txn_answer_a_config_override_beat():
    """the parent skip must not become a part skip: a txn answer on a part is a real assertion about that part"""
    for how in ("uid", "vendor", "contains"):
        parent, (a, b) = split_purchase()
        d = doc(answer_txn(a, category="Pets", subcategory=None))
        answers.apply([parent, a, b], d)
        assert a.category == "Pets" and answers.verify([parent, a, b], d) == [], how       # it holds until something outranks it
        match = {"uid": a.uid} if how == "uid" else {"vendor": "Synth Mart"} if how == "vendor" else {"contains": "synthmart"}
        overrides.apply([parent, a, b], [{"match": match, "set": {"category": "Gifts", "subcategory": "Toys"}}])
        assert a.category == "Gifts", how
        assert [c.name for c in answers.verify([parent, a, b], d)] == ["answers.conflict"], how


# ------------------------------- split parents: warn only when a category / subcategory answer is stranded

def test_a_parent_hit_that_sets_a_category_warns_and_a_tags_only_hit_stays_info():
    parent, (a, b) = split_purchase()
    checks = answers.apply([parent, a, b], doc(answer_txn(parent, category="Pets", subcategory=None)))
    hit = [c for c in checks if c.name == "answers.on_split_parent"]
    assert len(hit) == 1 and hit[0].status == "warn" and "1 of them set a category" in hit[0].detail
    parent2, kids = split_purchase()
    tagged = {"key": f"txn:{parent2.uid}", "scope": "txn", "txn_id": parent2.uid, "set": {"tags": ["kids"]},
              "answered_at": "2026-07-16T10:00:00Z"}
    checks = answers.apply([parent2, *kids], doc(tagged))
    hit = [c for c in checks if c.name == "answers.on_split_parent"]
    assert len(hit) == 1 and hit[0].status == "info" and "set a category" not in hit[0].detail
    assert parent2.tags == ["kids"]


def test_a_subcategory_only_answer_on_a_parent_is_stranded_too_and_warns():
    parent, kids = split_purchase()
    sub_only = {"key": f"txn:{parent.uid}", "scope": "txn", "txn_id": parent.uid, "set": {"subcategory": "Toys"},
                "answered_at": "2026-07-16T10:00:00Z"}
    hit = [c for c in answers.apply([parent, *kids], doc(sub_only)) if c.name == "answers.on_split_parent"]
    assert len(hit) == 1 and hit[0].status == "warn" and "1 of them set a category the parts do not follow" in hit[0].detail
    assert [k.subcategory for k in kids] != ["Toys", "Toys"]                    # a parent answer never reaches the parts
    parent2, kids2 = split_purchase()
    vendor_wide = answer_vendor("Synth Mart", category="Kids", subcategory="Childcare")      # a vendor-scope one skips the parts
    hit = [c for c in answers.apply([parent2, *kids2], doc(vendor_wide)) if c.name == "answers.on_split_parent"]
    assert len(hit) == 1 and hit[0].status == "warn"
    needs = {"key": f"txn:{parent2.uid}", "scope": "txn", "txn_id": parent2.uid, "set": {"needs_review": False},
             "answered_at": "2026-07-16T10:00:00Z"}
    hit = [c for c in answers.apply([parent2, *kids2], doc(needs)) if c.name == "answers.on_split_parent"]
    assert len(hit) == 1 and hit[0].status == "info"


def test_the_expired_text_does_not_promise_the_review_inbox_for_split_parts():
    gone = {"key": "txn:tx_gone", "scope": "txn", "txn_id": "tx_gone", "set": {"category": "Dining"},
            "answered_at": "2026-07-16T10:00:00Z"}
    plain = [c for c in answers.apply([tx()], doc(gone)) if c.name == "answers.expired"][0]
    assert plain.detail.endswith("the rows will reappear in the Review inbox")               # nothing is split: unchanged
    parent, kids = split_purchase()
    withparts = [c for c in answers.apply([parent, *kids], doc(gone)) if c.name == "answers.expired"][0]
    assert withparts.status == "info" and "ordinary rows will reappear in the Review inbox" in withparts.detail
    assert "a split part follows its leg position and does not" in withparts.detail
    assert "the rows will reappear" not in withparts.detail


# ------------------------------------------------------------------ capacity of the answers store

def many(n_txn, n_vendor=0):
    def stamp(i):
        return f"2026-07-16T{10 + i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}Z"
    vendors = ({"key": f"vendor:V{i}", "scope": "vendor", "vendor": f"V{i}", "set": {"tags": ["x"]},
                "answered_at": stamp(i)} for i in range(n_vendor))
    one_offs = ({"key": f"txn:t{i}", "scope": "txn", "txn_id": f"t{i}", "set": {"tags": ["x"]},
                 "answered_at": stamp(n_vendor + i)} for i in range(n_txn))
    return doc(*vendors, *one_offs)


def capacity(d):
    return [c for c in answers.apply([tx()], d) if c.name == "answers.capacity"]


def test_a_store_at_ninety_percent_of_the_one_off_cap_says_so_and_below_it_does_not():
    at_450 = capacity(many(450))
    assert len(at_450) == 1 and at_450[0].status == "info"
    assert at_450[0].detail.startswith("450 of 500 one-off review-inbox answers")
    assert "never dropped" in at_450[0].detail
    assert not capacity(many(449))
    assert capacity(many(500))[0].detail.startswith("500 of 500")


def test_vendor_answers_never_count_toward_the_capacity_info():
    # the store never drops a vendor-scope answer and does not count it toward MAX_ANSWERS, so neither does the info
    assert not capacity(many(0, 600))
    assert not capacity(many(449, 600))
    assert capacity(many(450, 600))[0].detail.startswith("450 of 500 one-off")


def test_the_answer_cap_is_one_number_in_the_etl_the_store_and_the_app():
    store = (ROOT / "deploy" / "scenario_store.py").read_text("utf-8")
    app = (ROOT / "app" / "src" / "lib" / "answers.ts").read_text("utf-8")
    in_store = re.search(r"^MAX_ANSWERS = (\d+)$", store, re.M)
    in_app = re.search(r"^export const MAX_ANSWERS = (\d+);$", app, re.M)
    assert in_store and in_app, "the MAX_ANSWERS literal moved: update this pin"
    assert answers.MAX_ANSWERS == int(in_store[1]) == int(in_app[1])


# ------------------------------------- the indexed apply/verify equal the naive scans

def naive_apply(txns, d):
    """the pre-index implementation, kept as the reference: every answer scans every row with
    overrides.matches. Returns (applied, expired, on_parent)."""
    entries = answers.valid_entries(d)
    entries.sort(key=lambda a: (0 if a["scope"] == "vendor" else 1, str(a.get("answered_at", "")), str(a.get("key", ""))))
    applied = expired = on_parent = 0
    for a in entries:
        match = {"uid": a["txn_id"]} if a["scope"] == "txn" else {"vendor": a["vendor"]}
        hits = 0
        for t in txns:
            if overrides.matches(t, match) and answers._answerable(t, a["scope"]):
                overrides.apply_set(t, a["set"], "answer")
                hits += 1
                on_parent += t.is_parent
        applied += hits
        if hits == 0 and a["scope"] == "txn":
            expired += 1
    return applied, expired, on_parent


def naive_verify(txns, d):
    conflicts = []
    for a in answers.valid_entries(d):
        want = a["set"]
        match = {"uid": a["txn_id"]} if a["scope"] == "txn" else {"vendor": a["vendor"]}
        for t in txns:
            if not overrides.matches(t, match) or not answers._answerable(t, a["scope"]) or t.is_parent:
                continue
            if "category" in want and t.category != want["category"]:
                conflicts.append(str(a.get("key", "?")))
                break
            if "subcategory" in want and want["subcategory"] is not None and t.subcategory != want["subcategory"]:
                conflicts.append(str(a.get("key", "?")))
                break
    return sorted(set(conflicts))


def synthetic_world(rng):
    vendors = ["Synth Mart", "SYNTH MART", "synth mart", "Alpha Co", "Beta Co", "Straße Shop", None, ""]
    rows = []
    for _ in range(rng.randint(30, 70)):
        rows.append(make_txn(desc=f"ROW {rng.randint(0, 25)}", cents=-100 * rng.randint(1, 9), account="demo_card_0000",
                             date=f"2026-03-{1 + rng.randint(0, 4):02d}", vendor=rng.choice(vendors),
                             type=rng.choice(["purchase", "purchase", "fee", "income", "transfer"]),
                             category=rng.choice(["Dining", "Groceries", None]), subcategory=rng.choice([None, "Coffee"]),
                             needs_review=rng.random() < 0.5, is_internal=rng.random() < 0.1))    # repeats share a uid on purpose
    for parent in rng.sample(rows, 4):
        if parent.type in ("purchase", "fee"):
            parent.is_parent = True
            rows.extend(dataclasses.replace(parent, is_parent=False, parent_id=parent.uid, split_index=k,
                                            category="Shopping", tags=[]) for k in (1, 2))
    rng.shuffle(rows)
    return rows


def synthetic_answers(rng, rows):
    out = []
    for n in range(rng.randint(5, 40)):
        s = rng.choice([{"category": "Pets"}, {"category": "Kids", "subcategory": "Childcare"}, {"tags": ["kids"]},
                        {"category": "Dining", "subcategory": "Coffee", "needs_review": False}, {"needs_review": True},
                        {"subcategory": "Coffee"}])
        stamp = f"2026-07-{1 + rng.randint(0, 3):02d}T10:00:00Z"
        if rng.random() < 0.5:
            t = rng.choice(rows)
            txn_id = t.uid if rng.random() < 0.85 else "tx_gone"
            out.append({"key": f"txn:{txn_id}", "scope": "txn", "txn_id": txn_id, "set": s, "answered_at": stamp})
        else:
            v = rng.choice(["synth mart", "SYNTH MART", "Alpha Co", "ALPHA CO", "Nobody", "", "STRASSE SHOP", "Straße Shop"])
            out.append({"key": f"vendor:{v}:{n}", "scope": "vendor", "vendor": v, "set": s, "answered_at": stamp})
    out.append({"scope": "txn", "txn_id": 5, "set": {"category": "X"}})                  # malformed: skipped by both
    out.append({"scope": "vendor", "vendor": "Alpha Co", "set": {"type": "transfer"}})   # a non-whitelisted key
    return doc(*out)


def snapshot(rows):
    return [(t.uid, t.category, t.subcategory, t.category_source, tuple(t.tags), t.needs_review, t.family_fact,
             t.prior_needs_review) for t in rows]


@pytest.mark.parametrize("seed", range(40))
def test_indexed_apply_and_verify_equal_the_naive_scans_on_random_worlds(seed):
    rng = random.Random(seed)
    rows = synthetic_world(rng)
    d = synthetic_answers(rng, rows)
    fast, slow = copy.deepcopy(rows), copy.deepcopy(rows)
    checks = answers.apply(fast, d)
    applied, expired, on_parent = naive_apply(slow, d)
    assert snapshot(fast) == snapshot(slow)
    by_name = {c.name: c.detail for c in checks}
    assert by_name["answers.applied"].startswith(f"{applied} transactions adjusted")
    if expired:
        assert by_name["answers.expired"].startswith(f"{expired} txn answers reference")
    else:
        assert "answers.expired" not in by_name
    if on_parent:
        assert by_name["answers.on_split_parent"].startswith(f"{on_parent} answer{'s' if on_parent != 1 else ''} landed")
    else:
        assert "answers.on_split_parent" not in by_name
    for t in rng.sample(range(len(fast)), len(fast) // 3):                 # a config override "wins" on some rows afterwards
        fast[t].category = slow[t].category = "Overridden"
        fast[t].subcategory = slow[t].subcategory = "Elsewhere"
    got = answers.verify(fast, d)
    want = naive_verify(slow, d)
    assert (sorted(set(got[0].detail.split(": ", 1)[1].split("; remove the override")[0].split(", "))) if got else []) == want

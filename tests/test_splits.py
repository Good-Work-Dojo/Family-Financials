# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Rule-driven splits: the model and the export shape, then etl/enrich/splits.py —
rule validation at load, child construction, the stages that must count a split purchase once, the
identity check, the un-split after a retype, and the real Pipeline.enrich wiring. Synthetic rows, on the demo
household's config; never the repo's scenarios/ or data/ (every Pipeline here gets tmp paths)."""
from __future__ import annotations

import dataclasses
import json
import re
import sqlite3

import pytest
from conftest import demo_config, demo_copy, isolated_pipeline, load_demo, make_txn

from etl import db, exports
from etl.config import Config
from etl.enrich import answers, overrides
from etl.enrich import learned as L
from etl.enrich import rules as R
from etl.enrich import splits as S
from etl.enrich import vendor_registry as vr
from etl.models import BalanceObs
from etl.pipeline import assign_occurrence_seq
from tools.demo import household as hh

CARD = hh.CARD_ALEX


def split_apply(rows, vendor_rules):
    """splits.apply over the compiled view of a vendor_rules dict (what Config.split_rules holds, minus
    the taxonomy check most of these synthetic legs do not need)"""
    return S.apply(rows, S.compile_split_rules(vendor_rules))


def mart(cents=-30000, date="2026-03-05", **over):
    """one purchase of the synthetic split vendor, categorized the way a keyword rule would"""
    over.setdefault("category", "Groceries")
    over.setdefault("category_source", "keyword")
    return make_txn(desc="SYNTHMART 1234", cents=cents, vendor="Synth Mart", account=CARD,
                    date=date, **over)


def hand_split(parent, legs=((-21000, "Groceries", None), (-9000, "Shopping", "Home Goods"))):
    """a parent and its children built by hand (the shape splits.apply builds)"""
    parent.is_parent = True
    kids = [dataclasses.replace(parent, amount_cents=c, category=cat, subcategory=sub, category_source="split",
                                parent_id=parent.uid, is_parent=False, split_index=i, tags=list(parent.tags))
            for i, (c, cat, sub) in enumerate(legs, start=1)]
    return parent, kids


# ------------------------------------------------------------ model, db, export

def test_db_stores_the_parent_flag_and_parent_id_and_parent_uids_still_resolve_for_transfer_matches(tmp_path):
    parent, kids = hand_split(mart())
    conn = db.build(tmp_path / "finance.db")
    conn.execute("INSERT INTO accounts (account_id, name, institution, owner, kind) "
                 f"VALUES ('{CARD}', 'C', '{hh.CARD_ISSUER}', 'Joint', 'credit-card')")
    db.insert_txns(conn, [parent, *kids], {CARD: "Joint"})
    rows = conn.execute("SELECT txn_uid, parent_id, is_parent, amount_cents FROM transactions "
                        "ORDER BY is_parent DESC, amount_cents").fetchall()
    assert rows[0] == (parent.uid, None, 1, -30000)
    assert [(r[1], r[2]) for r in rows[1:]] == [(parent.uid, 0), (parent.uid, 0)]
    assert conn.execute("SELECT SUM(amount_cents) FROM transactions WHERE parent_id = ?",
                        (parent.uid,)).fetchone()[0] == -30000
    # a parent's uid is the ordinary row's uid, so a transfer match that named it still resolves (foreign
    # keys are off, so only a JOIN proves it; the second match names a uid that is not a row: the control)
    conn.execute("INSERT INTO transfer_matches VALUES ('TG-synth', ?, ?, 'synthetic', 0)", (parent.uid, parent.uid))
    conn.execute("INSERT INTO transfer_matches VALUES ('TG-gone', 'tx_missing', 'tx_missing', 'synthetic', 0)")
    assert conn.execute("SELECT COUNT(*) FROM transfer_matches").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM transfer_matches m JOIN transactions o ON o.txn_uid = m.out_uid "
                        "JOIN transactions i ON i.txn_uid = m.in_uid").fetchone()[0] == 1


def test_two_identical_looking_split_parents_insert_without_a_primary_key_collision(tmp_path):
    """two same-day, same-descriptor, same-amount purchases of one vendor (told apart by seq),
    both split 50/50: four children, six rows, no txn_uid collision aborting the ingest"""
    a, b = mart(-30000), mart(-30000)
    assign_occurrence_seq([a, b])
    legs = ((-15000, "Groceries", None), (-15000, "Shopping", None))
    txns = []
    for p in (a, b):
        parent, kids = hand_split(p, legs)
        txns += [parent, *kids]
    conn = db.build(tmp_path / "finance.db")
    conn.execute("INSERT INTO accounts (account_id, name, institution, owner, kind) "
                 f"VALUES ('{CARD}', 'C', '{hh.CARD_ISSUER}', 'Joint', 'credit-card')")
    db.insert_txns(conn, txns, {CARD: "Joint"})
    assert conn.execute("SELECT COUNT(*), COUNT(DISTINCT txn_uid) FROM transactions").fetchone() == (6, 6)
    per_parent = conn.execute("SELECT parent_id, COUNT(*) FROM transactions WHERE parent_id IS NOT NULL "
                              "GROUP BY parent_id").fetchall()
    assert sorted(per_parent) == sorted([(a.uid, 2), (b.uid, 2)])


def test_export_rows_carry_parent_id_and_is_parent_and_count_every_row():
    parent, kids = hand_split(mart())
    plain = make_txn(desc="OTHER SHOP", cents=-500, vendor="Other Shop", account=CARD)
    doc = exports.export_transactions([parent, *kids, plain], {CARD: "Joint"}, {CARD: "C"})
    assert doc["count"] == 4                                   # parents AND children (test_e2e: count == len)
    by = {r["id"]: r for r in doc["transactions"]}
    assert (by[parent.uid]["is_parent"], by[parent.uid]["parent_id"], by[parent.uid]["amount"]) == (True, None, -300.0)
    assert [(by[k.uid]["parent_id"], by[k.uid]["is_parent"], by[k.uid]["category_source"]) for k in kids] \
        == [(parent.uid, False, "split"), (parent.uid, False, "split")]
    assert (by[plain.uid]["parent_id"], by[plain.uid]["is_parent"]) == (None, False)
    assert exports.SCHEMA_VERSION == 23


# ------------------------------------------------------------ validation at load

TAX = {"Groceries": [], "Shopping": ["Home Goods"], "Cash & Misc": ["Uncategorized"]}
LEGS = [{"pct": 70, "category": "Groceries"},
        {"remainder": True, "category": "Shopping", "subcategory": "Home Goods"}]
SEED = {"pattern": "SYNTHSPLITCO", "vendor": "Synth Split Co", "split": LEGS}


def rules(*legs, vendor="Synth Mart"):
    return {"patterns": [{"pattern": "SYNTHMART", "vendor": vendor, "split": list(legs)}]}


def test_a_valid_split_compiles_to_legs_keyed_by_casefolded_vendor():
    out = S.compile_split_rules(rules(*LEGS), taxonomy=TAX)
    assert list(out) == ["synth mart"]
    r = out["synth mart"]
    assert (r.vendor, r.index) == ("Synth Mart", 0)
    assert r.legs == (S.Leg("Groceries", None, pct=70.0), S.Leg("Shopping", "Home Goods", remainder=True))
    assert S.compile_split_rules({"patterns": [{"pattern": "X", "vendor": "X"}]}) == {}      # no split: nothing to compile


@pytest.mark.parametrize("legs,msg", [
    ([LEGS[0]], "at least 2 legs"),
    (["x", LEGS[1]], "leg 0 must be a mapping"),
    ([LEGS[0], {"pct": 30, "category": "Shopping"}], "exactly one leg must be the remainder"),
    ([{"remainder": True, "category": "Groceries"}, LEGS[1]], "exactly one leg must be the remainder"),
    ([{"pct": 100, "category": "Groceries"}, LEGS[1]], "between 0 and 100"),
    ([{"pct": 60, "category": "Groceries"}, {"pct": 40, "category": "Shopping"}, LEGS[1]], "leave room for the remainder"),
    ([{"pct": 0, "category": "Groceries"}, LEGS[1]], "between 0 and 100"),
    ([{"pct": True, "category": "Groceries"}, LEGS[1]], "between 0 and 100"),                # a bool is not a number
    ([{"pct": float("nan"), "category": "Groceries"}, LEGS[1]], "between 0 and 100"),
    ([{"amount": -5, "category": "Groceries"}, LEGS[1]], "positive number of dollars"),
    ([{"amount": float("inf"), "category": "Groceries"}, LEGS[1]], "positive number of dollars"),
    ([{"amount": float("nan"), "category": "Groceries"}, LEGS[1]], "positive number of dollars"),
    ([{"amount": 0.004, "category": "Groceries"}, LEGS[1]], "positive number of dollars"),     # rounds to 0 cents
    ([{"pct": 70, "amount": 5, "category": "Groceries"}, LEGS[1]], "exactly one of pct / amount / remainder"),
    ([{"pct": 70}, LEGS[1]], "needs category"),
    ([{"pct": 70, "category": "Groceries", "note": "x"}, LEGS[1]], "unexpected keys ['note']"),
    ([{"pct": 70, "category": "Dinning"}, LEGS[1]], "category 'Dinning' not in taxonomy"),
    ([{"pct": 70, "category": "Groceries", "subcategory": "Produce"}, LEGS[1]], "subcategory 'Produce' not seeded under 'Groceries'"),
    ([{"remainder": "yes", "category": "Groceries"}, LEGS[0]], "remainder must be true"),
])
def test_a_bad_split_fails_at_load_with_the_entry_index(legs, msg):
    with pytest.raises(ValueError, match=r"vendor_rules\[0\]\.split: .*" + re.escape(msg)):
        S.compile_split_rules(rules(*legs), taxonomy=TAX)


def test_a_split_entry_needs_a_vendor_to_key_on():
    with pytest.raises(ValueError, match=r"vendor_rules\[0\]\.split: a split entry needs vendor:"):
        S.compile_split_rules({"patterns": [{"pattern": "X", "split": LEGS}]})


def test_shape_is_checked_without_a_taxonomy_but_membership_needs_one():
    S.compile_split_rules(rules({"pct": 70, "category": "Nonsense"}, LEGS[1]))          # no taxonomy given: membership is not checked
    with pytest.raises(ValueError, match="not in taxonomy"):
        S.compile_split_rules(rules({"pct": 70, "category": "Nonsense"}, LEGS[1]), taxonomy=TAX)


def test_two_splits_for_one_vendor_and_a_split_on_a_keyword_rule_are_refused():
    two = {"patterns": [{"pattern": "A", "vendor": "Synth Mart", "split": LEGS},
                        {"pattern": "B", "vendor": "synth mart", "split": LEGS}]}
    with pytest.raises(ValueError, match=r"vendor_rules\[1\]\.split: vendor 'synth mart' already has a split \(entry \[0\]\)"):
        S.compile_split_rules(two)
    with pytest.raises(ValueError, match="split: is only valid on a vendor_rules pattern"):
        R.compile_rules([{"pattern": "X", "category": "Dining", "split": LEGS}], source="keyword_rules")
    R.compile_rules([{"pattern": "X", "vendor": "X", "split": LEGS}], source="vendor_rules")   # the key itself is allowed here


def _config_with(tmp_path, mutate):
    """A throwaway copy of the demo config with vendor_rules.yaml edited in place."""
    return demo_copy(tmp_path, {"vendor_rules.yaml": mutate})


def test_config_validates_split_legs_against_the_live_taxonomy_and_exposes_them(tmp_path):
    own = list(demo_config().split_rules)                                          # the demo's own split rides along
    cfg = Config(_config_with(tmp_path, lambda doc: doc["patterns"].append(SEED)))
    assert own and list(cfg.split_rules) == [*own, "synth split co"] and len(cfg.split_rules["synth split co"].legs) == 2
    bad = dict(SEED, split=[{"pct": 70, "category": "Groceries", "subcategory": "Produce"}, LEGS[1]])
    with pytest.raises(ValueError, match=r"patterns\[\d+\]\.split: .*not seeded under"):
        Config(_config_with(tmp_path / "bad", lambda doc: doc["patterns"].append(bad)))


# ------------------------------------------------------------ apply

VR = rules(*LEGS)


def test_a_split_makes_a_flagged_parent_and_children_that_sum_to_it_with_the_dust_in_the_remainder():
    parent = mart(cents=-10001, needs_review=True, tags=["kids"])
    plain = make_txn(desc="OTHER", cents=-500, vendor="Other", account=CARD)
    parent_uid = parent.uid                                                       # read before apply mutates it
    out, checks = split_apply([plain, parent], VR)
    assert [t.amount_cents for t in out] == [-500, -10001, -7001, -3000]          # children right after their parent
    assert parent.uid == parent_uid                                               # a parent keeps the ordinary row's uid
    assert (parent.is_parent, parent.needs_review, parent.category, parent.category_source) == (True, False, "Groceries", "keyword")
    a, b = out[2], out[3]
    assert (a.category, a.subcategory, b.category, b.subcategory) == ("Groceries", None, "Shopping", "Home Goods")
    for i, k in enumerate((a, b), start=1):
        assert (k.parent_id, k.is_parent, k.split_index, k.category_source, k.needs_review) == (parent.uid, False, i, "split", False)
        assert (k.date, k.account_id, k.description_raw, k.vendor, k.type, k.source_file, k.seq) == \
            (parent.date, parent.account_id, parent.description_raw, parent.vendor, parent.type, parent.source_file, parent.seq)
        assert (k.transfer_group_id, k.match_status, k.counter_hint) == (None, None, None)
        assert k.tags == ["kids"] and k.tags is not parent.tags                   # a copy, never the parent's list
    assert len({parent.uid, a.uid, b.uid}) == 3 and sum(k.amount_cents for k in (a, b)) == parent.amount_cents
    assert [c.name for c in checks] == ["splits.applied"]
    assert "Synth Mart: 1 purchase(s) split into 2 parts" in checks[0].detail


def test_a_child_does_not_inherit_the_parents_transfer_fields_or_stashed_review_flag():
    parent = mart(transfer_group_id="TG-x", match_status="matched", counter_hint="somewhere", needs_review=True)
    out, _ = split_apply([parent], VR)
    assert (parent.transfer_group_id, parent.match_status, parent.counter_hint) == ("TG-x", "matched", "somewhere")   # the parent is untouched
    assert [(k.transfer_group_id, k.match_status, k.counter_hint, k.prior_needs_review) for k in out[1:]] == [(None, None, None, None)] * 2
    assert parent.prior_needs_review is True


def test_three_way_pct_split_an_amount_leg_and_a_parent_too_small_for_its_fixed_legs():
    vr = rules({"pct": 33.33, "category": "Groceries"}, {"amount": 5.00, "category": "Shopping"},
               {"remainder": True, "category": "Cash & Misc"})
    out, checks = split_apply([mart(cents=-10000), mart(cents=-400)], vr)
    assert [t.amount_cents for t in out] == [-10000, -3333, -500, -6167, -400]     # the small one is left whole
    assert out[-1].is_parent is False and out[-1].needs_review is False
    assert "Synth Mart: 1 purchase(s) split into 3 parts, 1 too small to split" in checks[0].detail
    assert S.child_amounts(-10000, S.compile_split_rules(vr)["synth mart"]) == [-3333, -500, -6167]
    assert S.child_amounts(-400, S.compile_split_rules(vr)["synth mart"]) is None
    assert S.child_amounts(0, S.compile_split_rules(VR)["synth mart"]) is None
    assert S.child_amounts(-1, S.compile_split_rules(VR)["synth mart"]) is None    # 70% of a cent rounds to the whole cent: no room


def test_a_row_too_small_to_split_keeps_its_review_flag_and_its_own_state():
    out, _ = split_apply([mart(cents=-1, needs_review=True)], VR)
    assert len(out) == 1 and (out[0].is_parent, out[0].needs_review, out[0].prior_needs_review) == (False, True, None)


def test_a_refund_splits_with_positive_children_and_amount_legs_take_the_parents_sign():
    vr = rules({"amount": 5.00, "category": "Groceries"}, {"remainder": True, "category": "Shopping"})
    out, _ = split_apply([mart(cents=3000)], vr)
    assert [t.amount_cents for t in out] == [3000, 500, 2500]
    out, _ = split_apply([mart(cents=30000)], VR)
    assert [t.amount_cents for t in out] == [30000, 21000, 9000]


@pytest.mark.parametrize("cents", [-10001, -9999, -1001, -333, -30001, -2, 7777])
def test_the_remainder_leg_absorbs_every_rounding_so_the_children_always_sum_to_the_parent(cents):
    three = rules({"pct": 33.33, "category": "Groceries"}, {"pct": 33.33, "category": "Shopping"},
                  {"remainder": True, "category": "Cash & Misc"})
    for vrules in (VR, three):       # (not `vr`: that name is the vendor_registry import)
        rule = next(iter(S.compile_split_rules(vrules).values()))
        amounts = S.child_amounts(cents, rule)
        if amounts is None:
            continue
        assert sum(amounts) == cents and all((a < 0) == (cents < 0) and a != 0 for a in amounts)
        out, _ = split_apply([mart(cents=cents)], vrules)
        assert sum(k.amount_cents for k in out[1:]) == out[0].amount_cents == cents
        assert S.sum_identity(out).status == "pass"


def test_only_external_spend_rows_of_the_vendor_split():
    income = mart(cents=5000, type="income", category="Income", subcategory="Other")
    transfer = mart(cents=-30000, type="transfer", is_internal=True, category="Internal", subcategory="transfer")
    invest = mart(cents=-30000, type="investment", is_internal=True)
    other = make_txn(desc="OTHER", cents=-30000, vendor="Other", account=CARD)
    nameless = make_txn(desc="X", cents=-30000, vendor=None, account=CARD)
    fee = mart(cents=-300, type="fee", category="Fees & Interest")
    out, _ = split_apply([income, transfer, invest, other, nameless, fee], VR)
    assert len(out) == 8                                                       # only the fee (SPEND_TYPES) split
    assert [t.is_parent for t in out[:5]] == [False] * 5 and out[5].is_parent is True
    assert [t.amount_cents for t in out[5:]] == [-300, -210, -90]


def test_vendor_matching_is_by_the_casefolded_vendor_string_not_a_prefix():
    a, b = mart(), mart(date="2026-03-06")
    a.vendor, b.vendor = "SYNTH mart", "Synth Martian"
    out, _ = split_apply([a, b], VR)
    assert [t.is_parent for t in out] == [True, False, False, False] and len(out) == 4


def test_a_spend_typed_row_that_is_internal_never_splits():
    internal_purchase = mart(cents=-30000, is_internal=True)         # the flag alone must stop it, whatever the type says
    out, _ = split_apply([internal_purchase], VR)
    assert len(out) == 1 and out[0].is_parent is False


def test_apply_is_a_no_op_without_split_rules_and_never_re_splits():
    rows = [mart()]
    out, checks = split_apply(rows, {"patterns": [{"pattern": "SYNTHMART", "vendor": "Synth Mart"}]})
    assert out == rows and checks == [] and rows[0].is_parent is False
    once, _ = split_apply([mart()], VR)
    twice, _ = split_apply(once, VR)
    assert [t.uid for t in twice] == [t.uid for t in once] and len(twice) == 3


def test_applied_reports_every_rule_and_zero_for_a_vendor_with_no_rows():
    vr = {"patterns": [*VR["patterns"], {"pattern": "GHOST", "vendor": "Ghost Co", "split": LEGS}]}
    _, checks = split_apply([mart(), mart(date="2026-03-06")], vr)
    assert checks[0].status == "info"
    assert checks[0].detail.startswith("2 split rule(s): Ghost Co: 0 purchase(s) split into 0 parts; Synth Mart: 2 purchase(s) split into 4 parts")


def test_the_remainder_leg_may_be_listed_first_or_in_the_middle_and_every_leg_lands_in_its_own_position():
    first = rules({"remainder": True, "category": "Shopping", "subcategory": "Home Goods"},
                  {"pct": 70, "category": "Groceries"})
    out, _ = split_apply([mart(cents=-10001)], first)
    assert [(k.split_index, k.category, k.subcategory, k.amount_cents) for k in out[1:]] == \
        [(1, "Shopping", "Home Goods", -3000), (2, "Groceries", None, -7001)]
    middle = rules({"pct": 20, "category": "Groceries"}, {"remainder": True, "category": "Shopping"},
                   {"amount": 5.00, "category": "Cash & Misc"})
    rule = S.compile_split_rules(middle)["synth mart"]
    assert S.child_amounts(-10001, rule) == [-2000, -7501, -500]
    out, _ = split_apply([mart(cents=-10001)], middle)
    assert [(k.split_index, k.category, k.amount_cents) for k in out[1:]] == \
        [(1, "Groceries", -2000), (2, "Shopping", -7501), (3, "Cash & Misc", -500)]
    assert S.sum_identity(out).status == "pass"


def test_applied_is_reported_when_a_split_rule_exists_even_if_no_row_matched_or_there_are_no_rows():
    for rows in ([make_txn(desc="OTHER", cents=-500, vendor="Other", account=CARD)], []):
        out, checks = split_apply(rows, VR)
        assert out == rows and len(out) == len(rows)
        assert [(c.name, c.status) for c in checks] == [("splits.applied", "info")]
        assert checks[0].detail == "1 split rule(s): Synth Mart: 0 purchase(s) split into 0 parts"


def test_sum_identity_passes_with_no_splits_and_fails_on_a_tampered_child_a_lone_child_or_an_orphan():
    assert S.sum_identity([mart()]).status == "pass"
    out, _ = split_apply([mart()], VR)
    ok = S.sum_identity(out)
    assert (ok.name, ok.status) == ("splits.sum_identity", "pass") and "1 split parent(s), 2 children" in ok.detail
    out[1].amount_cents += 1
    bad = S.sum_identity(out)
    assert bad.status == "fail" and out[0].uid in bad.detail and "-20999" not in bad.detail   # ids, never amounts
    out[1].amount_cents -= 1
    lone = dataclasses.replace(out[1], amount_cents=out[0].amount_cents)              # the sum is right: only "fewer than 2" can catch it
    assert S.sum_identity([out[0], lone]).status == "fail"
    orphan = dataclasses.replace(out[1], parent_id="tx_nobody", split_index=7)
    assert "1 child row(s) with no parent" in S.sum_identity([*out, orphan]).detail


def test_unsplit_retyped_takes_back_the_children_of_a_parent_an_override_retyped():
    out, _ = split_apply([mart(), mart(date="2026-03-06")], VR)
    parent = out[0]
    overrides.apply(out, [{"match": {"uid": parent.uid}, "set": {"type": "transfer"}}])
    assert (parent.type, parent.is_internal, parent.is_parent) == ("transfer", True, True)
    kept, n = S.unsplit_retyped(out)
    assert n == 1 and len(kept) == 4                                               # 1 undone parent + 1 parent + 2 parts
    assert parent.is_parent is False and all(k.parent_id != parent.uid for k in kept)
    assert S.sum_identity(kept).status == "pass"
    same, zero = S.unsplit_retyped(kept)
    assert zero == 0 and same is kept                                              # the identity when nothing to undo


@pytest.mark.parametrize("flagged", [True, False])
def test_unsplit_gives_back_the_review_flag_the_split_took_away(flagged):
    """A split parent leaves the Review inbox (display-only), but a parent an override retypes out of
    spending is an ordinary row again and must be exactly as it would have been had it never been split"""
    out, _ = split_apply([mart(needs_review=flagged)], VR)
    parent = out[0]
    assert parent.needs_review is False                                            # out of the inbox while it is split
    overrides.apply(out, [{"match": {"uid": parent.uid}, "set": {"type": "transfer"}}])
    kept, n = S.unsplit_retyped(out)
    assert n == 1 and kept == [parent]
    assert (parent.needs_review, parent.is_parent, parent.prior_needs_review) == (flagged, False, None)


@pytest.mark.parametrize("before", [True, False])
@pytest.mark.parametrize("explicit", [True, False])
@pytest.mark.parametrize("via", ["override", "answer"])
def test_unsplit_does_not_overrule_an_explicit_needs_review_from_an_override_or_an_answer(before, explicit, via):
    """The stash is a default for a row nobody spoke about; an override or answer that sets
    needs_review (true OR false: the dashboard editor always writes false) outranks it."""
    out, _ = split_apply([mart(needs_review=before)], VR)
    parent = out[0]
    if via == "answer":
        answers.apply(out, {"answers": [{"key": "k", "scope": "txn", "txn_id": parent.uid, "answered_at": "2026-07-02T00:00:00Z",
                                         "set": {"category": "Dining", "subcategory": "Restaurants", "needs_review": explicit}}]})
    else:
        overrides.apply(out, [{"match": {"uid": parent.uid}, "set": {"needs_review": explicit}}])
    assert parent.prior_needs_review is None                          # whoever spoke dropped the stash
    overrides.apply(out, [{"match": {"uid": parent.uid}, "set": {"type": "transfer"}}])
    S.unsplit_retyped(out)
    assert parent.needs_review is explicit


def test_a_set_that_does_not_mention_needs_review_leaves_the_stash_alone():
    out, _ = split_apply([mart(needs_review=True)], VR)
    parent = out[0]
    overrides.apply(out, [{"match": {"uid": parent.uid}, "set": {"tags": ["x"], "category": "Dining", "type": "transfer"}}])
    S.unsplit_retyped(out)
    assert parent.needs_review is True


def test_the_stashed_review_flag_is_transient_it_reaches_no_uid_export_row_db_row_or_equality(tmp_path):
    parent = mart(needs_review=True)
    uid, twin = parent.uid, mart(needs_review=True)
    out, _ = split_apply([parent], VR)
    assert parent.prior_needs_review is True and parent.uid == uid
    assert "prior_needs_review" not in repr(parent)
    assert dataclasses.replace(parent, prior_needs_review=None) == parent          # compare=False: equality ignores it
    doc = exports.export_transactions(out, {CARD: "Joint"}, {CARD: "C"})
    plain = exports.export_transactions([twin], {CARD: "Joint"}, {CARD: "C"})
    assert all(set(r) == set(plain["transactions"][0]) for r in doc["transactions"])
    conn = db.build(tmp_path / "finance.db")
    conn.execute("INSERT INTO accounts (account_id, name, institution, owner, kind) "
                 f"VALUES ('{CARD}', 'C', '{hh.CARD_ISSUER}', 'Joint', 'credit-card')")
    db.insert_txns(conn, out, {CARD: "Joint"})
    cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
    assert not any("prior" in c or "review_" in c for c in cols)


# ------------------------------------------------------------ the pipeline

def pipeline(tmp_path, rows, extra_patterns=(SEED,), answers=(), overrides_=None):
    """A Pipeline on a validated throwaway Config whose vendor_rules carry `extra_patterns`; the
    answers and learned paths, the data dir and the exports dir all point into tmp_path."""
    cfg = Config(_config_with(tmp_path, lambda doc: doc["patterns"].extend(extra_patterns)))
    if overrides_ is not None:
        cfg.overrides = overrides_
    return isolated_pipeline(tmp_path, rows, cfg=cfg, answers=answers, answers_updated_at="2026-07-01T00:00:00Z",
                             windows={CARD: ("2026-01-01", "2026-12-31"),
                                      hh.CHECKING: ("2026-01-01", "2026-12-31")})


def raw(desc="SYNTHSPLITCO 1234", cents=-30000, date="2026-03-05", account=CARD):
    """an un-enriched row (no vendor yet): the normalizer, the Categorizer and the split stage do the rest"""
    return make_txn(desc=desc, cents=cents, account=account, date=date)


def stamps(p):
    return [(t.uid, t.amount_cents, t.category, t.subcategory, t.category_source, t.is_parent, t.parent_id) for t in p.txns]


def test_enrich_splits_after_the_cascade_and_before_the_answers_and_is_deterministic(tmp_path):
    """The answer targets the SECOND PART of the split by its uid, so it can only land if the part
    already exists when answers.apply runs — move the stage after the answers and the answer expires"""
    part2 = dataclasses.replace(raw(), parent_id=raw().uid, split_index=2)               # what apply will build
    answer = {"key": f"txn:{part2.uid}", "scope": "txn", "txn_id": part2.uid,
              "set": {"category": "Dining", "subcategory": "Restaurants"}, "answered_at": "2026-07-02T00:00:00Z"}
    p = pipeline(tmp_path, [raw(), raw(desc="ZZYZX GENERAL #1234", cents=-5000)], answers=[answer])    # a descriptor no rule knows
    p.enrich()
    assert [t.vendor for t in p.txns] == ["Synth Split Co", "Synth Split Co", "Synth Split Co", "Zzyzx General"]
    parent, a, b, plain = p.txns
    # the parent fell through to the fallback above the review threshold, and the split took it out of the inbox
    assert (parent.is_parent, parent.category, parent.subcategory, parent.needs_review) == (True, "Cash & Misc", "Uncategorized", False)
    assert b.uid == part2.uid
    assert [(k.category, k.subcategory, k.category_source, k.amount_cents) for k in (a, b)] == \
        [("Groceries", None, "split", -21000), ("Dining", "Restaurants", "answer", -9000)]
    assert plain.is_parent is False and plain.parent_id is None
    names = [c.name for c in p.checks]
    assert "splits.applied" in names and "splits.unsplit" not in names
    assert "answers.applied" in names and "answers.expired" not in names               # the part existed when the answers ran
    assert names.index("splits.applied") < names.index("answers.applied")
    q = pipeline(tmp_path / "again", [raw(), raw(desc="ZZYZX GENERAL #1234", cents=-5000)], answers=[answer])
    q.enrich()
    assert stamps(q) == stamps(p)


ROW_KEYS = ("uid", "amount_cents", "type", "is_internal", "category", "subcategory", "category_source",
            "needs_review", "is_parent", "parent_id")


def unsplit_vs_control(tmp_path, answers_=(), overrides_=None):
    """Run the same single row through a vendor WITH the split rule and a control vendor of the same name
    with none, under the same answers and overrides. Returns (split pipeline, control pipeline)."""
    p = pipeline(tmp_path / "split", [raw()], answers=answers_, overrides_=overrides_)
    p.enrich()
    ctl = pipeline(tmp_path / "ctl", [raw()], extra_patterns=({"pattern": "SYNTHSPLITCO", "vendor": "Synth Split Co"},),
                   answers=answers_, overrides_=overrides_)
    ctl.enrich()
    return p, ctl


def test_enrich_undoes_a_split_whose_parent_an_override_retyped_and_the_identity_still_holds(tmp_path):
    p = pipeline(tmp_path, [raw()])
    p.enrich()
    parent_uid = p.txns[0].uid
    assert p.txns[0].is_parent and p.txns[0].needs_review is False and len(p.txns) == 3
    retype = [{"match": {"uid": parent_uid}, "set": {"type": "transfer"}}]
    q, ctl = unsplit_vs_control(tmp_path / "q", overrides_=retype)
    assert [t.uid for t in q.txns] == [parent_uid] and q.txns[0].is_parent is False
    undo = {c.name: c for c in q.checks}["splits.unsplit"]
    assert undo.status == "info" and undo.detail == ("1 split purchase left whole: an override retyped the purchase out "
                                                     "of spending (overrides.yaml).")
    q.finalize_quality()
    assert {c.name: c.status for c in q.checks}["splits.sum_identity"] == "pass"
    # and the row is exactly what the same override leaves on a vendor that never had a split rule
    assert [getattr(q.txns[0], k) for k in ROW_KEYS] == [getattr(ctl.txns[0], k) for k in ROW_KEYS]
    assert q.txns[0].needs_review is True                                              # the fallback's flag came back


def test_an_explicit_needs_review_false_from_an_override_survives_the_unsplit(tmp_path):
    """An override of `{type: transfer, needs_review: false}` on a parent the fallback flagged keeps its explicit flag."""
    parent_uid = raw().uid
    retype = [{"match": {"uid": parent_uid}, "set": {"type": "transfer", "needs_review": False}}]
    p, ctl = unsplit_vs_control(tmp_path, overrides_=retype)
    assert [t.uid for t in p.txns] == [parent_uid] and "splits.unsplit" in {c.name for c in p.checks}
    assert ctl.txns[0].needs_review is False                                           # the control: the override stands
    assert [getattr(p.txns[0], k) for k in ROW_KEYS] == [getattr(ctl.txns[0], k) for k in ROW_KEYS]


def test_an_explicit_needs_review_false_from_a_stored_answer_survives_the_unsplit(tmp_path):
    """The dashboard editor stored `{needs_review: false}` against this uid before the split
    rule existed; an override then retypes the row, and the stored answer's flag still stands (its category gives way
    to the new type's: the override names none, so the transfer is filed Internal)."""
    parent_uid = raw().uid
    answer = {"key": f"txn:{parent_uid}", "scope": "txn", "txn_id": parent_uid, "answered_at": "2026-07-02T00:00:00Z",
              "set": {"category": "Dining", "subcategory": "Restaurants", "needs_review": False}}
    retype = [{"match": {"uid": parent_uid}, "set": {"type": "transfer"}}]
    p, ctl = unsplit_vs_control(tmp_path, answers_=[answer], overrides_=retype)
    assert [t.uid for t in p.txns] == [parent_uid] and "splits.unsplit" in {c.name for c in p.checks}
    assert (ctl.txns[0].needs_review, ctl.txns[0].category) == (False, "Internal")
    assert [getattr(p.txns[0], k) for k in ROW_KEYS] == [getattr(ctl.txns[0], k) for k in ROW_KEYS]


# ------------------------------------------------------------ counted once

def test_cashflow_reads_the_parts_and_never_the_parent():
    parent = mart(category="Cash & Misc", subcategory="Uncategorized")
    parent, kids = hand_split(parent)
    doc = exports.export_cashflow_monthly([parent, *kids], {CARD: ("2026-01-01", "2026-12-31")}, [CARD],
                                          min_share=exports.DEFAULT_COVERAGE_MIN_SHARE)
    m = doc["months"][0]
    assert (m["month"], m["spend"]) == ("2026-03", 300.0)
    assert m["by_category"] == {"Groceries": {"total": 210.0, "by_subcategory": {"—": 210.0}},
                                "Shopping": {"total": 90.0, "by_subcategory": {"Home Goods": 90.0}}}


def test_count_rows_is_every_row_but_the_parents():
    parent, kids = hand_split(mart())
    assert S.count_rows([parent, *kids, mart(date="2026-03-06")]) == 3
    assert S.count_rows([]) == 0


def test_a_split_purchase_counts_once_in_every_total(tmp_path, capsys):
    """End to end: one split purchase (-300 -> -210 / -90) and one plain purchase
    (-50) go through enrich, finalize_quality, persist and export."""
    p = pipeline(tmp_path, [raw(), raw(desc="ZZYZX GENERAL #1234", cents=-5000)])
    p.enrich()
    p.acct_file_sums = {CARD: -35000}                 # what the raw file summed to
    p.finalize_quality()
    status = {c.name: c.status for c in p.checks}
    assert status[f"reconcile.{CARD}.store_vs_files"] == "pass"              # -210 - 90 - 50; the parent would double it
    assert status["splits.sum_identity"] == "pass"
    assert p.month_sums == {CARD: {"2026-03": -35000}}
    assert p.uncat_share == 5000 / 35000                                        # the plain row's fallback; the parent's own does not count
    p.persist()
    conn = sqlite3.connect(p.cfg.data_dir / "finance.db")
    assert conn.execute("SELECT txn_count FROM accounts WHERE account_id=?", (CARD,)).fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*), SUM(is_parent) FROM transactions").fetchone() == (4, 1)
    conn.close()
    p.export()
    out = p.cfg.exports_dir
    tx = json.loads((out / "transactions.json").read_text())
    assert tx["count"] == 4 and json.loads((out / "manifest.json").read_text())["transaction_count"] == 3
    cf = json.loads((out / "cashflow_monthly.json").read_text())["months"]
    assert [(m["month"], m["spend"]) for m in cf] == [("2026-03", 350.0)]
    assert cf[0]["by_category"]["Groceries"]["total"] == 210.0 and cf[0]["by_category"]["Shopping"]["total"] == 90.0
    vendors = {v["vendor"]: v for v in json.loads((out / "vendors.json").read_text())["vendors"]}
    assert (vendors["Synth Split Co"]["txns"], vendors["Synth Split Co"]["spend_ttm"]) == (1, 300.0)
    assert json.loads((out / "recurring.json").read_text())["items"] == []
    p.print_summary()
    text = capsys.readouterr().out
    # the manifest's count (a split purchase counted once), and what the split made of it
    assert "transactions: 3 (1 purchase split into 2 parts)" in text and "FAIL" not in text


def test_ledger_drift_counts_a_split_purchase_once_through_the_pipeline(tmp_path):
    """Pipeline level (the reconcile unit test hands ledger_drift_inputs a hand-built list): a feed purchase of
    the split vendor goes through enrich (which splits it) and finalize_quality, and the drift window sums its
    parts once - with the parent counted too, `got` would be the purchase twice and the drift its whole amount."""
    feed = dict(account=CARD, bank_type="simplefin")                  # a SimpleFIN-mapped card
    p = pipeline(tmp_path, [make_txn(desc="SYNTHSPLITCO 1234", cents=-30000, date="2026-09-05", **feed),
                            make_txn(desc="ZZYZX GENERAL #1234", cents=-5000, date="2026-09-06", **feed)])
    p.enrich()
    assert [t.is_parent for t in p.txns].count(True) == 1 and len(p.txns) == 4       # the pipeline did split it
    p.balance_obs = [BalanceObs(CARD, "2026-09-01", 0), BalanceObs(CARD, "2026-09-10", -35000)]
    p.finalize_quality()
    assert p.ledger_drift[CARD] == {"from": "2026-09-01", "to": "2026-09-10", "expected_cents": -35000,
                                                 "got_cents": -35000, "drift_cents": 0}
    assert {c.name: c.status for c in p.checks}[f"reconcile.{CARD}.ledger_drift"] == "info"


def test_print_summary_counts_a_dated_split_purchase_once_in_the_year_spend_and_the_top_vendors(tmp_path, capsys):
    """The summary sums one calendar year; with every row in 2025 that is 2025, marked partial (Pipeline.print_summary).
    The split purchase is dated inside it: -300 split -210 / -90, plus a plain -50"""
    p = pipeline(tmp_path, [raw(date="2025-06-05"), raw(desc="ZZYZX GENERAL #1234", cents=-5000, date="2025-06-06")])
    p.enrich()
    p.acct_file_sums = {CARD: -35000}
    p.finalize_quality()
    p.print_summary()
    text = capsys.readouterr().out
    assert "2025 spending (partial year): $350.00" in text                     # the parent on top would read $650.00
    top = text.split("top vendors:\n", 1)[1].splitlines()
    assert [ln.split() for ln in top if ln.strip()] == [["$300.00", "Synth", "Split", "Co"], ["$50.00", "Zzyzx", "General"]]
    assert "transactions: 3 (1 purchase split into 2 parts)" in text


# ------------------------------------------------------------ answers and learning

def test_answers_on_a_split_vendors_rows_teach_nothing():
    """learned.derive runs BEFORE splits.apply in Pipeline.enrich, so it sees the purchases whole: five agreeing
    answers on them would promote any other vendor (the control), and the split vendor's `split:` alone keeps it
    out of learning. Answers keyed to the parts it will become resolve to no row at that point and teach nothing."""
    rows = [mart(date=f"2026-03-{d:02d}") for d in (1, 2, 3, 4, 5)]                  # pre-split: no parent, no part yet
    assert not any(t.is_parent or t.parent_id for t in rows)
    on_rows = [{"key": f"txn:{t.uid}", "scope": "txn", "txn_id": t.uid, "set": {"category": "Pets", "subcategory": None},
                "answered_at": f"2026-07-01T{i:02d}:00:00Z"} for i, t in enumerate(rows)]
    parts = [t for t in split_apply([dataclasses.replace(t) for t in rows], VR)[0] if t.parent_id]
    on_parts = [{"key": f"txn:{t.uid}", "scope": "txn", "txn_id": t.uid, "set": {"category": "Pets", "subcategory": None},
                 "answered_at": f"2026-07-02T{i:02d}:00:00Z"} for i, t in enumerate(parts[:5])]
    taxonomy = {"Pets": [], "Groceries": [], "Shopping": ["Home Goods"]}

    def derive(vendor_rules, answers_):
        return L.derive(rows, {"version": 1, "updated_at": "2026-07-03T00:00:00Z", "answers": answers_}, None,
                        learn_excluded=vr.learning_excluded(vendor_rules), explicit_vendor_cats={}, taxonomy=taxonomy)
    control, _ = derive({"patterns": [{"pattern": "SYNTHMART", "vendor": "Synth Mart"}]}, on_rows)
    assert [(r["vendor"], r["category"]) for r in control["rules"]] == [("Synth Mart", "Pets")]   # no split: learned
    doc, checks = derive(VR, on_rows)
    assert doc["rules"] == []
    assert "1 vendor(s) skipped: excluded from learning" in checks[0].detail
    assert derive(VR, on_parts)[0]["rules"] == []          # part uids name no row before the split: nothing to learn from


def test_enrich_applies_a_vendor_answer_to_the_plain_rows_only_and_logs_the_parent(tmp_path):
    vendor_answer = {"key": "vendor:Synth Split Co", "scope": "vendor", "vendor": "Synth Split Co",
                     "set": {"category": "Pets", "subcategory": None, "needs_review": False}, "answered_at": "2026-07-01T00:00:00Z"}
    p = pipeline(tmp_path, [raw(), raw(cents=-1)], answers=[vendor_answer])        # a 1-cent row is too small to split: an ordinary row
    p.enrich()
    parent, a, b, small = p.txns
    assert (parent.is_parent, small.is_parent, small.parent_id) == (True, False, None)
    assert [(k.category, k.category_source) for k in (a, b)] == [("Groceries", "split"), ("Shopping", "split")]
    assert (small.category, small.category_source) == ("Pets", "answer")
    names = {c.name for c in p.checks}
    assert "answers.on_split_parent" in names and "answers.conflict" not in names and "learned_rules.conflict" not in names


def test_a_learned_rule_on_a_vendor_that_gains_a_split_is_superseded_not_kept(tmp_path):
    """the prior learned doc already holds a rule for the vendor; the vendor_rules entry (the seed) now carries
    split: legs. The guard (vendor_registry.learning_excluded -> learned.derive) drops the rule as `superseded`,
    so it is neither replayed onto the parent nor written back. The control is the same vendor with no split."""
    prior = {"version": 1, "updated_at": "2026-07-01T01:00:00Z", "history": [], "rules": [
        {"vendor": "Synth Split Co", "category": "Dining", "subcategory": "Coffee", "learned_at": "2026-07-01T01:00:00Z",
         "last_confirmed_at": "2026-07-01T01:00:00Z",
         "evidence": {"scope": "txn", "n_agree": 3, "n_total": 3, "answer_keys": []}}]}
    for sub in ("control", "split"):                                                           # one Config copy per Pipeline
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "learned_rules.json").write_text(json.dumps(prior), "utf-8")
    control = pipeline(tmp_path / "control", [raw()], extra_patterns=({"pattern": "SYNTHSPLITCO", "vendor": "Synth Split Co"},))
    control.enrich()
    assert [(t.category, t.subcategory, t.category_source) for t in control.txns] == [("Dining", "Coffee", "learned")]   # the rule bites
    assert [r["vendor"] for r in control.learned_doc["rules"]] == ["Synth Split Co"] and control.learned_events == []
    p = pipeline(tmp_path / "split", [raw()])                                                   # the same rule on disk, now with split legs
    p.enrich()
    parent, a, b = p.txns
    assert parent.is_parent and parent.category_source != "learned" and (parent.category, parent.subcategory) != ("Dining", "Coffee")
    assert [(k.category, k.category_source) for k in (a, b)] == [("Groceries", "split"), ("Shopping", "split")]
    assert p.learned_doc["rules"] == []                                                        # dropped, not carried forward ...
    assert [(e["event"], e["vendor"]) for e in p.learned_events] == [("superseded", "Synth Split Co")]   # ... and said so in the history
    row = {r["vendor"]: r for r in vr.build(p.txns, p.cfg.vendor_rules, p.learned_doc, "2026-12-31")}["Synth Split Co"]
    assert (row["learn"], row["category"], row["category_source"]) == (False, None, None)      # vendors.json agrees: no learned category


def test_each_split_rule_of_the_demo_config_splits_a_synthetic_purchase_of_its_vendor_per_its_legs():
    vendor_rules = load_demo("vendor_rules.yaml")
    rules = S.compile_split_rules(vendor_rules)
    assert rules                                              # the demo config splits a vendor
    cents = -50000                                            # above every fixed leg, so each rule splits
    for key, rule in rules.items():                           # one entry per split vendor
        parent = make_txn(desc="SEED ROW", cents=cents, vendor=rule.vendor, account=CARD)
        out, checks = split_apply([parent], vendor_rules)
        fixed = {i: round(cents * leg.pct / 100) if leg.pct is not None else -leg.amount_cents
                 for i, leg in enumerate(rule.legs) if not leg.remainder}     # read from the legs, not from child_amounts
        want = [cents - sum(fixed.values()) if leg.remainder else fixed[i] for i, leg in enumerate(rule.legs)]
        assert [(t.category, t.subcategory, t.amount_cents) for t in out[1:]] == \
            [(leg.category, leg.subcategory, a) for leg, a in zip(rule.legs, want, strict=True)]
        assert sum(t.amount_cents for t in out[1:]) == cents and out[0].is_parent
        assert key == rule.vendor.casefold() and f"1 purchase(s) split into {len(rule.legs)} parts" in checks[0].detail


# ------------------------------------------------------------ the config's compiled view

def test_enrich_splits_from_the_configs_compiled_view_and_never_compiles_a_second_time(tmp_path, monkeypatch):
    # Config compiles (and validates against the taxonomy) the split legs once at load; Pipeline.enrich hands that
    # view to splits.apply. Two compilations could drift (a leg order, a taxonomy check), so the second is gone:
    # compile_split_rules raising after Config exists must not stop an enrich, and the rows follow cfg.split_rules
    p = pipeline(tmp_path / "view", [raw()])
    off = pipeline(tmp_path / "off", [raw()])
    assert list(p.cfg.split_rules) == [*demo_config().split_rules, "synth split co"]
    off.cfg.split_rules = {}                              # vendor_rules still carry the split: only the compiled view decides

    def boom(*a, **kw):
        raise AssertionError("splits.apply compiled the legs again")
    monkeypatch.setattr(S, "compile_split_rules", boom)
    p.enrich()
    assert [t.is_parent for t in p.txns] == [True, False, False] and p.txns[1].parent_id == p.txns[0].uid
    off.enrich()
    assert len(off.txns) == 1 and off.txns[0].is_parent is False


def test_a_split_vendors_refund_splits_with_positive_parts_through_the_pipeline(tmp_path):
    # A refund of the split vendor is split like its purchases, with positive parts (the seed rule: 70% Groceries,
    # the rest Shopping / Home Goods); a refund of a merchant with no split stays whole. The uid hashes the raw
    # descriptor: it must not move.
    refund = raw(desc="SYNTHSPLITCO REFUND", cents=2500)
    other = raw(desc="Refund: Sample Outfitters", cents=2500)
    plain = raw(desc="ZZYZX GENERAL REFUND", cents=2500)
    uids = [t.uid for t in (refund, other, plain)]
    p = pipeline(tmp_path, [refund, other, plain])
    p.enrich()
    assert [t.uid for t in p.txns if t.parent_id is None] == uids
    split = [t for t in p.txns if t.vendor == "Synth Split Co"]
    assert [t.is_parent for t in split] == [True, False, False] and split[0].uid == uids[0]
    assert [t.amount_cents for t in split[1:]] == [1750, 750]
    assert [(t.category, t.subcategory) for t in split[1:]] == [("Groceries", None), ("Shopping", "Home Goods")]
    assert [t.category_source for t in split[1:]] == ["split", "split"]
    assert [(t.vendor, t.is_parent) for t in p.txns if t.uid in uids[1:]] == [
        ("Refund: Sample Outfitters", False), ("Zzyzx General Refund", False)]

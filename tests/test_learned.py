# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""etl/enrich/learned.py: promotion, demotion, stickiness, guards, casefold keying,
idempotence, and a load/write path that never fails an ingest. Synthetic rows only."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from conftest import answer, answers_doc, make_txn

from etl.enrich import learned as L
from etl.enrich import renames

TAX = {"Dining": ["Restaurants", "Coffee"], "Kids": ["Childcare"], "Groceries": [],
       "Transport": ["Rideshare"], "Cash & Misc": ["Uncategorized"]}
EXPLICIT = {"uber": {"category": "Transport", "subcategory": "Rideshare"}}
D, K, G = ("Dining", "Restaurants"), ("Kids", "Childcare"), ("Groceries", None)


def row(i, vendor="Cora's Bakery", cents=-1850, **over):
    return make_txn(desc=f"{(vendor or 'NO VENDOR').upper()} 1234", cents=cents, vendor=vendor,
                    date=f"2026-05-{1 + i:02d}", account="demo_card_0000", **over)


def at(i):
    """Distinct, increasing timestamps: at(0) is the oldest."""
    return f"2026-07-{1 + i // 24:02d}T{i % 24:02d}:00:00Z"


T0 = at(0)


def txn_answer(t, pair, i):
    return answer("txn", t, {"category": pair[0], "subcategory": pair[1]}, at(i))


def vendor_answer(vendor, pair, i):
    return answer("vendor", vendor, {"category": pair[0], "subcategory": pair[1]}, at(i))


def doc(*answers):
    return answers_doc(*answers, updated_at="2026-07-16T10:00:00Z")


def rule(vendor, pair, learned_at=T0, confirmed=T0, scope="txn", n=3, total=5):
    return {"vendor": vendor, "category": pair[0], "subcategory": pair[1],
            "learned_at": learned_at, "last_confirmed_at": confirmed,
            "evidence": {"scope": scope, "n_agree": n, "n_total": total, "answer_keys": []}}


def prior(*rules, history=()):
    return {"version": 1, "updated_at": "2026-07-01T00:00:00Z", "rules": list(rules), "history": list(history)}


def derive(txns, answers, prior_doc=None, **kw):
    kw.setdefault("learn_excluded", frozenset({"amazon"}))
    kw.setdefault("explicit_vendor_cats", EXPLICIT)
    kw.setdefault("taxonomy", TAX)
    return L.derive(txns, answers, prior_doc, **kw)


def sequence(pairs, vendor="Cora's Bakery"):
    """pairs oldest-first -> (rows, answers): answer i is on row i at time at(i)."""
    rows = [row(i, vendor) for i in range(len(pairs))]
    return rows, [txn_answer(rows[i], p, i) for i, p in enumerate(pairs)]


def pairs(out):
    return {r["vendor"]: (r["category"], r["subcategory"]) for r in out["rules"]}


def events(out):
    return [(e["event"], e["vendor"], e["category"]) for e in out["history"]]


# ------------------------------------------------------------ promotion (txn scope)

def test_three_of_five_agreeing_txn_answers_promote_with_evidence():
    rows, answers = sequence([K, D, D, D, K])               # newest three D D K: one dissent, not contested
    out, checks = derive(rows, doc(*answers))
    assert pairs(out) == {"Cora's Bakery": D}
    r = out["rules"][0]
    assert r["evidence"] == {"scope": "txn", "n_agree": 3, "n_total": 5,
                             "answer_keys": sorted(a["key"] for a in answers[1:4])}
    assert r["learned_at"] == r["last_confirmed_at"] == at(3)        # newest AGREEING answer, no clock
    assert out["history"] == [{"at": at(3), "event": "promoted", "vendor": "Cora's Bakery", "category": "Dining"}]
    active = [c for c in checks if c.name == "learned_rules.active"]
    assert len(active) == 1 and active[0].status == "info" and "Cora's Bakery" in active[0].detail
    assert not [c for c in checks if c.name == "learned_rules.conflict"]


@pytest.mark.parametrize("seq,want", [
    ([D, D, K, K, G], {}),                       # 2 of 5: no
    ([D, D, D], {"Cora's Bakery": D}),           # 3 of 3: yes (n_total 3)
    ([D, D], {}),                                # 2 of 2: no
])
def test_the_three_of_five_boundary(seq, want):
    rows, answers = sequence(seq)
    out, _ = derive(rows, doc(*answers))
    assert pairs(out) == want
    if want:
        assert out["rules"][0]["evidence"]["n_total"] == len(seq)


def test_only_the_five_most_recent_answers_count():
    rows, answers = sequence([D, D, D, K, K, K, K, K])      # the five newest are all K
    out, _ = derive(rows, doc(*answers))
    assert pairs(out) == {"Cora's Bakery": K}
    # Dining leads the newest three (K D D) and has 5 of all 8 answers, but only 2 of the newest
    # FIVE (G K K D D): outside the window it is not evidence, so nothing is promoted
    rows, answers = sequence([D, D, D, G, K, K, D, D])
    out, _ = derive(rows, doc(*answers))
    assert out["rules"] == []
    # newest five G D K D D: Dining has 3 -> promoted on the window's numbers alone, never the
    # 5 of 7 that counting the evicted answers would give
    rows, answers = sequence([D, D, G, D, K, D, D])
    out, _ = derive(rows, doc(*answers))
    assert pairs(out) == {"Cora's Bakery": D}
    assert out["rules"][0]["evidence"] == {"scope": "txn", "n_agree": 3, "n_total": 5,
                                           "answer_keys": sorted(answers[i]["key"] for i in (3, 5, 6))}


def test_the_window_is_taken_after_ineligible_answers_are_skipped():
    # answers on vanished uids, income rows, internal rows and vendor-less rows are not evidence,
    # so even when they are the NEWEST answers they use up no slot of the five-answer window
    rows, answers = sequence([D, D, D, D, D])
    income = row(5, cents=90000, type="income")
    internal = row(6, type="transfer", is_internal=True)
    bare = row(7, vendor=None)
    answers += [txn_answer(t, K, 5 + i) for i, t in enumerate([income, internal, bare])]
    answers += [{**txn_answer(rows[0], K, 8 + i), "key": f"txn:ghost{i}", "txn_id": f"tx_ghost{i}"} for i in range(3)]
    out, _ = derive([*rows, income, internal, bare], doc(*answers))
    assert pairs(out) == {"Cora's Bakery": D}
    assert out["rules"][0]["evidence"]["n_total"] == 5 and out["rules"][0]["evidence"]["n_agree"] == 5


def test_a_pair_the_newest_three_answers_dispute_is_not_promoted():
    # 3 of 5 agree, but two of the newest three disagree: the demotion test would remove the
    # rule on the very next ingest, so it is never promoted (no promote/demote flip-flop)
    rows, answers = sequence([D, D, D, K, K])
    fresh, _ = derive(rows, doc(*answers))
    assert fresh["rules"] == [] and fresh["history"] == []
    # ...which is exactly what an existing rule gets from the same answers: removed, not re-added
    withprior, _ = derive(rows, doc(*answers), prior(rule("Cora's Bakery", D)))
    assert withprior["rules"] == fresh["rules"]
    again, _ = derive(rows, doc(*answers), withprior)
    assert again == withprior


def test_answers_are_evidence_only_on_spend_rows_with_a_vendor():
    income = row(0, cents=90000, type="income")
    internal = row(1, type="transfer", is_internal=True)
    bare = row(2, vendor=None)
    spend = [row(3), row(4), row(5)]
    answers = [txn_answer(t, D, i) for i, t in enumerate([income, internal, bare, *spend])]
    out, _ = derive([income, internal, bare, *spend], doc(*answers))
    assert pairs(out) == {"Cora's Bakery": D}                # only the three spend rows counted
    assert out["rules"][0]["evidence"]["n_total"] == 3


def test_fee_rows_teach_nothing_because_learned_rules_apply_to_purchases_only():
    # the Categorizer applies a learned rule to purchases (fees take type_default), so answers on a
    # vendor's fee rows must not teach a rule that then paints its purchases
    fees = [row(i, type="fee") for i in range(3)]
    out, _ = derive(fees, doc(*[txn_answer(t, D, i) for i, t in enumerate(fees)]))
    assert out["rules"] == [] and out["history"] == []
    purchases = [row(i) for i in range(3)]                                 # the control: the same answers on purchases promote
    out, _ = derive(purchases, doc(*[txn_answer(t, D, i) for i, t in enumerate(purchases)]))
    assert pairs(out) == {"Cora's Bakery": D}
    mixed = [row(0), row(1), row(2, type="fee"), row(3, type="fee")]      # two purchases + two fees: 2 of 2, below the bar
    out, _ = derive(mixed, doc(*[txn_answer(t, D, i) for i, t in enumerate(mixed)]))
    assert out["rules"] == []


def test_tags_only_and_malformed_answers_are_not_evidence():
    rows = [row(i) for i in range(4)]
    answers = [{"key": f"txn:{rows[0].uid}", "scope": "txn", "txn_id": rows[0].uid,
                "set": {"tags": ["kids"]}, "answered_at": at(0)},                  # valid, teaches nothing
               {"key": f"txn:{rows[1].uid}", "scope": "txn", "txn_id": rows[1].uid,
                "set": {"category": "Dining"}},                                    # no answered_at
               {"key": "txn:ghost", "scope": "txn", "txn_id": "tx_nope",
                "set": {"category": "Dining"}, "answered_at": at(2)},               # uid gone
               txn_answer(rows[3], D, 3)]
    out, _ = derive(rows, doc(*answers))
    assert out["rules"] == []


def tags_only(t, i):
    return {"key": f"txn:{t.uid}", "scope": "txn", "txn_id": t.uid, "set": {"tags": ["kids"]}, "answered_at": at(i)}


def test_a_tags_only_answer_is_never_learning_evidence_however_many_there_are():
    # the bulk tag editor sends {"tags": [...]} and nothing else: tagging five rows of one
    # vendor must not promote a rule, and the txn-scope ones must not count as votes beside real answers
    rows = [row(i) for i in range(5)]
    out, _ = derive(rows, doc(*[tags_only(t, i) for i, t in enumerate(rows)]))
    assert out["rules"] == [] and out["history"] == []
    vendor_wide = {"key": "vendor:Cora's Bakery", "scope": "vendor", "vendor": "Cora's Bakery",
                   "set": {"tags": ["kids"]}, "answered_at": at(9)}              # a vendor-scope tags-only answer too
    out, _ = derive(rows, doc(*[tags_only(t, i) for i, t in enumerate(rows)], vendor_wide))
    assert out["rules"] == []
    # beside two real agreeing answers they add nothing: 2 of 2 is still below the 3-answer bar
    real = [txn_answer(rows[0], D, 0), txn_answer(rows[1], D, 1)]
    out, _ = derive(rows, doc(*real, *[tags_only(t, i + 2) for i, t in enumerate(rows[2:])]))
    assert out["rules"] == []
    # ... and the control: the same rows with a third REAL answer do promote, so the asserts above can fail
    out, _ = derive(rows, doc(*real, txn_answer(rows[2], D, 2), tags_only(rows[3], 3)))
    assert pairs(out) == {"Cora's Bakery": D}


def test_the_learned_window_reads_stamps_so_a_tags_only_save_must_not_move_a_category_answer_to_now():
    # The dashboard keeps a tags-only save from teaching anything by stamping the merged answer one tick after the
    # stored category answer (app/src/lib/answers.ts mergeAnswer): same place in the newest-answers window. Restamping it to
    # "now" instead would put the two oldest answers at the front and demote a rule that three newer rows still support.
    rows, answers = sequence([K, K, D, D, D])                  # newest five: K K D D D -> Dining 3 of 5, newest three all Dining
    out, _ = derive(rows, doc(*answers))
    assert pairs(out) == {"Cora's Bakery": D}

    def retimed(stamps):
        return [{**a, "answered_at": stamps.get(i, a["answered_at"])} for i, a in enumerate(answers)]

    bumped = retimed({0: "2026-07-01T00:00:01Z", 1: "2026-07-01T01:00:01Z"})        # +1 second: the legacy-format bump
    out, _ = derive(rows, doc(*bumped))
    assert pairs(out) == {"Cora's Bakery": D}                  # untouched: the rule stands
    out, _ = derive(rows, doc(*retimed({0: at(20), 1: at(21)})), prior(rule("Cora's Bakery", D)))
    assert pairs(out) == {}                                    # restamped to now: newest three are D K K, the rule is demoted
    assert events(out) == [("demoted", "Cora's Bakery", "Dining")]


def test_answers_without_a_string_key_or_with_a_blank_vendor_teach_nothing():
    rows = [row(i) for i in range(3)]
    keyless = [{k: v for k, v in txn_answer(t, D, i).items() if k != "key"} for i, t in enumerate(rows)]
    out, _ = derive(rows, doc(*keyless, vendor_answer("   ", K, 5)))
    assert out["rules"] == []
    odd = [{**txn_answer(t, D, i), "key": 7} for i, t in enumerate(rows)]
    out, _ = derive(rows, doc(*odd))
    assert out["rules"] == []


# -------------------------------------------------------------- vendor scope

def test_vendor_scope_answer_promotes_immediately_and_outranks_txn_evidence():
    rows, answers = sequence([D, D, D])
    out, _ = derive(rows, doc(*answers, vendor_answer("Cora's Bakery", K, 10)))
    assert pairs(out) == {"Cora's Bakery": K}
    r = out["rules"][0]
    assert r["evidence"] == {"scope": "vendor", "n_agree": 1, "n_total": 1, "answer_keys": ["vendor:Cora's Bakery"]}
    assert r["learned_at"] == r["last_confirmed_at"] == at(10)
    # an OLDER vendor answer still rules: txn answers are per-row exceptions, never a vote against it
    out2, _ = derive(rows, doc(*answers, vendor_answer("Cora's Bakery", K, 0)))
    assert pairs(out2) == {"Cora's Bakery": K}


def test_vendor_scope_answer_replaces_a_differing_prior_rule():
    rows = [row(0)]
    out, _ = derive(rows, doc(vendor_answer("Cora's Bakery", K, 5)), prior(rule("Cora's Bakery", D)))
    assert pairs(out) == {"Cora's Bakery": K}
    assert events(out) == [("demoted", "Cora's Bakery", "Dining"), ("promoted", "Cora's Bakery", "Kids")]
    assert out["history"][0]["at"] == at(5)
    # the same vendor answer again: re-confirmed, learned_at kept, no new event
    again, _ = derive(rows, doc(vendor_answer("Cora's Bakery", K, 5)), out)
    assert again["rules"] == out["rules"] and again["history"] == out["history"]


def test_a_vendor_scope_rule_stays_authoritative_after_its_answer_is_gone():
    rows, answers = sequence([D, D, D])                      # three txn Dining answers, all newer than the vendor one
    asked, _ = derive(rows, doc(vendor_answer("Cora's Bakery", K, 0), *[{**a, "answered_at": at(5 + i)} for i, a in enumerate(answers)]))
    assert pairs(asked) == {"Cora's Bakery": K} and asked["rules"][0]["evidence"]["scope"] == "vendor"
    assert events(asked) == [("promoted", "Cora's Bakery", "Kids")]
    # the vendor answer is gone (the store never evicts one; answers.json trimmed by hand); the txn answers alone would
    # promote Dining (fresh, no prior) but they are per-row exceptions: the standing rule is neither demoted nor replaced
    left = doc(*[{**a, "answered_at": at(5 + i)} for i, a in enumerate(answers)])
    fresh, _ = derive(rows, left)
    assert pairs(fresh) == {"Cora's Bakery": D}              # (what a lost prior doc would learn instead)
    after, checks = derive(rows, left, asked)
    assert after["rules"] == asked["rules"] and after["history"] == asked["history"]
    again, _ = derive(rows, left, after)
    assert again == after                                    # and it stays that way, every ingest
    assert "1 learned vendor rule" in [c for c in checks if c.name == "learned_rules.active"][0].detail
    # only a newer vendor-scope answer replaces it
    newer, _ = derive(rows, doc(*left["answers"], vendor_answer("Cora's Bakery", G, 20)), after)
    assert pairs(newer) == {"Cora's Bakery": G}
    assert events(newer) == [("promoted", "Cora's Bakery", "Kids"), ("demoted", "Cora's Bakery", "Kids"),
                             ("promoted", "Cora's Bakery", "Groceries")]
    # ...and the guards still apply to it: an explicit YAML category supersedes it, as always
    explicit, _ = derive(rows, left, asked, explicit_vendor_cats={"cora's bakery": {"category": "Dining", "subcategory": None}})
    assert explicit["rules"] == [] and events(explicit)[-1] == ("superseded", "Cora's Bakery", "Kids")


# ------------------------------------------------- demotion wins over promotion

def test_demotion_two_of_last_three_wins_over_promotion():
    # oldest-first [A, A, A, B, B] with prior A: the newest three are A B B -> demoted, and A is
    # ineligible this pass even though it has 3 of 5; B has only 2 -> nothing promoted
    rows, answers = sequence([D, D, D, K, K])
    out, _ = derive(rows, doc(*answers), prior(rule("Cora's Bakery", D)))
    assert out["rules"] == []
    assert events(out) == [("demoted", "Cora's Bakery", "Dining")]
    assert out["history"][0]["at"] == at(4)                 # newest disagreeing answer
    # oldest-first [A, A, B, B, B] with prior A: newest three B B B -> demoted, B 3 of 5 -> promoted
    rows, answers = sequence([D, D, K, K, K])
    out, _ = derive(rows, doc(*answers), prior(rule("Cora's Bakery", D)))
    assert pairs(out) == {"Cora's Bakery": K}
    assert events(out) == [("demoted", "Cora's Bakery", "Dining"), ("promoted", "Cora's Bakery", "Kids")]


def test_one_of_three_disagreeing_keeps_and_reconfirms_the_rule():
    rows, answers = sequence([D, D, D, D, K])                # newest three: D D K
    old = rule("Cora's Bakery", D, learned_at=at(0), confirmed=at(0))
    out, _ = derive(rows, doc(*answers), prior(old))
    assert pairs(out) == {"Cora's Bakery": D} and out["history"] == []
    r = out["rules"][0]
    assert r["learned_at"] == at(0)                          # kept from the prior
    assert r["last_confirmed_at"] == at(3)                   # newest agreeing answer
    assert r["evidence"]["n_agree"] == 4 and r["evidence"]["n_total"] == 5


def test_sticky_rule_survives_evicted_answers():
    old = rule("Cora's Bakery", D, learned_at=at(0), confirmed=at(3))
    out, checks = derive([row(0)], doc(), prior(old, history=[{"at": at(3), "event": "promoted",
                                                             "vendor": "Cora's Bakery", "category": "Dining"}]))
    assert out["rules"] == [old]
    assert out["history"] == [{"at": at(3), "event": "promoted", "vendor": "Cora's Bakery", "category": "Dining"}]
    assert out["updated_at"] == "2026-07-01T00:00:00Z"       # copied from the prior, never the clock
    assert "1 learned vendor rule" in [c for c in checks if c.name == "learned_rules.active"][0].detail


# ------------------------------------------------------------------- guards

def test_explicit_vendor_category_blocks_learning_with_one_conflict_warn():
    rows, answers = sequence([D, D, D, D, D], vendor="Uber")
    out, checks = derive(rows, doc(*answers))
    assert out["rules"] == []
    conflict = [c for c in checks if c.name == "learned_rules.conflict"]
    assert len(conflict) == 1 and conflict[0].status == "warn" and "Uber" in conflict[0].detail
    assert "Dining" not in conflict[0].detail and "tx_" not in conflict[0].detail    # names only
    # agreeing answers (same category, a finer subcategory): nothing learned, nothing to warn about
    rows, answers = sequence([("Transport", "Parking/Tolls")] * 3, vendor="Uber")
    out, checks = derive(rows, doc(*answers))
    assert out["rules"] == [] and not [c for c in checks if c.name == "learned_rules.conflict"]


def test_prior_rule_is_superseded_by_a_new_explicit_vendor_category():
    out, checks = derive([row(0, vendor="Uber")], doc(), prior(rule("Uber", D)))
    assert out["rules"] == [] and events(out) == [("superseded", "Uber", "Dining")]
    assert not [c for c in checks if c.name == "learned_rules.conflict"]


def test_excluded_vendor_is_skipped_silently_and_a_prior_rule_dropped():
    rows, answers = sequence([D] * 5, vendor="Amazon")
    out, checks = derive(rows, doc(*answers, vendor_answer("AMAZON", K, 9)), prior(rule("Amazon", G)))
    assert out["rules"] == [] and events(out) == [("superseded", "Amazon", "Groceries")]
    assert [c.name for c in checks] == ["learned_rules.active"]
    assert "1 vendor(s) skipped" in checks[0].detail and "Amazon" not in checks[0].detail


def test_off_taxonomy_candidates_and_priors_are_dropped_without_a_rule():
    rows, answers = sequence([("Dinning", None)] * 3)
    out, checks = derive(rows, doc(*answers), prior(rule("Old Shop", ("Eating Out", None))))
    assert out["rules"] == [] and out["history"] == []
    assert [c.name for c in checks] == ["learned_rules.active"]     # unknown_references warns, not the learner


def test_a_subcategory_outside_the_taxonomy_is_not_promoted_and_is_warned_once():
    # the category is fine, the subcategory is stale (a taxonomy edit since the answers)
    stale = ("Dining", "Brunch")
    rows, answers = sequence([stale] * 3)
    out, checks = derive(rows, doc(*answers))
    assert out["rules"] == [] and out["history"] == []
    assert [c.name for c in checks] == ["learned_rules.active", "learned_rules.off_taxonomy"]
    warn = checks[1]
    assert warn.status == "warn" and "1 vendor(s)" in warn.detail and "Cora's Bakery" in warn.detail
    assert "Brunch" not in warn.detail and "tx_" not in warn.detail         # vendor names only
    # the same through a vendor-scope answer, and for a category with an empty subcategory list
    out, checks = derive([row(0)], doc(vendor_answer("Cora's Bakery", ("Groceries", "Organic"), 3)))
    assert out["rules"] == [] and [c.name for c in checks][-1] == "learned_rules.off_taxonomy"


def test_in_taxonomy_pairs_still_promote_whatever_their_subcategory_shape():
    for pair in (("Dining", "Coffee"), ("Dining", None), ("Groceries", None), ("Kids", "Childcare")):
        rows, answers = sequence([pair] * 3)
        out, checks = derive(rows, doc(*answers))
        assert pairs(out) == {"Cora's Bakery": pair}, pair
        assert [c.name for c in checks] == ["learned_rules.active"], pair
    out, checks = derive([row(0)], doc(vendor_answer("Cora's Bakery", ("Dining", "Coffee"), 3)))
    assert pairs(out) == {"Cora's Bakery": ("Dining", "Coffee")} and [c.name for c in checks] == ["learned_rules.active"]


def test_a_stale_subcategory_replaces_nothing_and_the_demotion_rules_still_apply():
    stale = ("Dining", "Brunch")
    # answers that dispute a valid prior rule still demote it (2 of the newest 3), but the stale
    # pair is not promoted in its place
    rows, answers = sequence([stale] * 3)
    out, checks = derive(rows, doc(*answers), prior(rule("Cora's Bakery", D)))
    assert out["rules"] == [] and events(out) == [("demoted", "Cora's Bakery", "Dining")]
    assert checks[-1].name == "learned_rules.off_taxonomy"
    # a prior rule whose own subcategory went stale, re-confirmed by agreeing answers, is left as
    # it was (not re-confirmed, not erased) and the warn keeps reporting the drift
    out, checks = derive(rows, doc(*answers), prior(rule("Cora's Bakery", stale)))
    assert pairs(out) == {"Cora's Bakery": stale} and events(out) == []
    assert out["rules"][0]["last_confirmed_at"] == T0 and checks[-1].name == "learned_rules.off_taxonomy"


def test_a_sticky_prior_rule_with_an_unseeded_subcategory_and_no_answers_is_named_in_off_taxonomy():
    stale = ("Dining", "Brunch")                                           # Dining is seeded, Brunch is not
    out, checks = derive([row(0)], doc(), prior(rule("Cora's Bakery", stale)))
    assert pairs(out) == {"Cora's Bakery": stale}                          # the rule is kept ...
    warn = [c for c in checks if c.name == "learned_rules.off_taxonomy"]
    assert len(warn) == 1 and warn[0].status == "warn" and "Cora's Bakery" in warn[0].detail    # ... and said so
    assert "Brunch" not in warn[0].detail
    again, checks2 = derive([row(0)], doc(), out)                          # idempotent: same state checks, same doc
    assert again == out and [(c.name, c.detail) for c in checks2] == [(c.name, c.detail) for c in checks]
    ok, checks3 = derive([row(0)], doc(), prior(rule("Cora's Bakery", ("Dining", "Coffee"))))
    assert not [c for c in checks3 if c.name == "learned_rules.off_taxonomy"]       # a seeded pair is quiet


def test_active_detail_lists_at_most_twenty_vendors_then_a_count():
    twenty_five = [rule(f"Shop {i:02d}", D) for i in range(25)]
    out, checks = derive([], doc(), prior(*twenty_five))
    assert len(out["rules"]) == 25                                  # the cap is cosmetic: every rule is kept
    detail = checks[0].detail
    assert detail.startswith("25 learned vendor rule(s) active")
    assert "Shop 19" in detail and "Shop 20" not in detail and detail.endswith(", +5 more")
    # at the cap exactly: no suffix
    out, checks = derive([], doc(), prior(*twenty_five[:20]))
    assert "Shop 19" in checks[0].detail and "more" not in checks[0].detail


# ------------------------------------------------------- casefold keying

def test_keys_are_casefolded_and_display_spelling_follows_the_rows():
    rows = [row(0), row(1)]                                            # rows spell it Cora's Bakery
    out, _ = derive(rows, doc(vendor_answer("CORA'S BAKERY", K, 3)), prior(rule("Cora'S Bakery", D)))
    assert [r["vendor"] for r in out["rules"]] == ["Cora's Bakery"]    # one rule, the rows' spelling
    assert pairs(out) == {"Cora's Bakery": K}
    assert events(out) == [("demoted", "Cora'S Bakery", "Dining"), ("promoted", "Cora's Bakery", "Kids")]
    assert L.as_rules(out) == {"cora's bakery": {"vendor": "Cora's Bakery", "category": "Kids", "subcategory": "Childcare"}}
    # no rows at all: the answer's own spelling is all there is
    out2, _ = derive([], doc(vendor_answer("CORA'S BAKERY", K, 3)))
    assert [r["vendor"] for r in out2["rules"]] == ["CORA'S BAKERY"]


def test_a_sticky_prior_rule_is_respelled_to_the_rows_and_stays_otherwise_unchanged():
    # no answers at all this pass: the rule survives, but its display spelling follows the rows
    # ("an older spelling in the file") — and a second pass over that output is a no-op
    rows = [row(0), row(1)]
    old = rule("CORA'S BAKERY", D, learned_at=at(2), confirmed=at(5), scope="vendor", n=1, total=1)
    out, _ = derive(rows, doc(), prior(old))
    assert out["rules"] == [{**old, "vendor": "Cora's Bakery"}]
    assert events(out) == []
    again, _ = derive(rows, doc(), out)
    assert again["rules"] == out["rules"] and again["history"] == out["history"]
    # no rows to take a spelling from: the stored one stays
    kept, _ = derive([], doc(), prior(old))
    assert kept["rules"] == [old]


def test_explicit_vendor_categories_are_casefolded_and_last_entry_wins():
    vr = {"patterns": [{"pattern": "UBER", "vendor": "Uber", "category": "Transport", "subcategory": "Rideshare"},
                       {"pattern": "UBER EATS", "vendor": "Uber", "category": "Dining"},
                       {"pattern": "COSTCO", "vendor": "Costco"}]}
    assert L.explicit_vendor_categories(vr) == {"uber": {"category": "Dining", "subcategory": None}}


def test_as_rules_skips_malformed_entries_and_none():
    assert L.as_rules(None) == {} and L.as_rules({"rules": "x"}) == {}
    got = L.as_rules({"rules": [rule("Alpha", K), {"vendor": 1}, "junk", rule("ALPHA", D)]})
    assert got == {"alpha": {"vendor": "Alpha", "category": "Kids", "subcategory": "Childcare"}}   # first wins


# ------------------------------------------------------------- idempotence

def test_derive_is_idempotent_and_clock_free():
    rows, answers = sequence([K, D, D, D, K])
    rows += [row(7, vendor="Uber"), row(8, vendor="Costco")]
    answers += [txn_answer(rows[-2], D, 7), vendor_answer("Costco", G, 8)]
    once, checks1 = derive(rows, doc(*answers))
    assert pairs(once) == {"Cora's Bakery": D, "Costco": G}      # the scenario really learns something
    twice, checks2 = derive(rows, doc(*answers), once)
    assert twice == once
    assert [(c.name, c.status, c.detail) for c in checks1] == [(c.name, c.status, c.detail) for c in checks2]
    stamps = {r[k] for r in once["rules"] for k in ("learned_at", "last_confirmed_at")} | {e["at"] for e in once["history"]}
    assert stamps <= {a["answered_at"] for a in answers}           # every timestamp is an answered_at
    assert once["updated_at"] is None                              # no prior, no clock


def test_state_checks_are_identical_when_a_dropped_prior_rule_has_no_answers():
    # a prior rule for a vendor that has since been excluded, with no answers left: pass one
    # supersedes it, pass two (prior now empty) must report the very same state checks
    first, c1 = derive([row(0, vendor="Amazon")], doc(), prior(rule("Amazon", G)))
    second, c2 = derive([row(0, vendor="Amazon")], doc(), first)
    assert second == first
    assert [(c.name, c.status, c.detail) for c in c1] == [(c.name, c.status, c.detail) for c in c2]


def test_a_vendor_rename_that_folds_two_stored_rules_keeps_the_vendor_scope_one_and_logs_the_other():
    # "Foo Cafe" is renamed to "Bar Cafe", which already has a (txn-scope) rule: the stored file lists
    # Bar before Foo, so a first-wins fold would keep the wrong one and drop the other without a trace
    bar = rule("Bar Cafe", D, learned_at=at(1), confirmed=at(5))
    foo = rule("Foo Cafe", G, learned_at=at(0), confirmed=at(2), scope="vendor", n=1, total=1)
    folded = renames.VendorRenamer([{"from": "Foo Cafe", "to": "Bar Cafe"}]).apply_to_learned(prior(bar, foo))
    out, checks = derive([], doc(), folded)
    assert pairs(out) == {"Bar Cafe": G} and out["rules"][0]["evidence"]["scope"] == "vendor"
    assert events(out) == [("superseded", "Bar Cafe", "Dining")] and out["history"][0]["at"] == at(5)
    warn = [c for c in checks if c.name == "learned_rules.rename_collision"]
    assert len(warn) == 1 and warn[0].status == "warn" and "Bar Cafe" in warn[0].detail
    assert "Dining" not in warn[0].detail and "Groceries" not in warn[0].detail                # names only
    again, checks2 = derive([], doc(), out)                                  # fed back: nothing left to fold
    assert again == out and not [c for c in checks2 if c.name == "learned_rules.rename_collision"]


def test_a_rename_collision_between_two_txn_scope_rules_keeps_the_newer_and_a_tie_keeps_the_first():
    old = rule("Bar Cafe", D, confirmed=at(1))
    new = rule("Foo Cafe", K, confirmed=at(4))
    renamer = renames.VendorRenamer([{"from": "Foo Cafe", "to": "Bar Cafe"}])
    out, _ = derive([], doc(), renamer.apply_to_learned(prior(old, new)))
    assert pairs(out) == {"Bar Cafe": K} and events(out) == [("superseded", "Bar Cafe", "Dining")]
    tie_a, tie_b = rule("Bar Cafe", D, confirmed=at(3)), rule("Foo Cafe", K, confirmed=at(3))
    out, _ = derive([], doc(), renamer.apply_to_learned(prior(tie_a, tie_b)))
    assert pairs(out) == {"Bar Cafe": D} and events(out) == [("superseded", "Bar Cafe", "Kids")]
    # a case-only rename folds the same way (a rename is casefolded)
    out, _ = derive([], doc(), prior(rule("Bar Cafe", D, confirmed=at(1)), rule("BAR CAFE", K, confirmed=at(2))))
    assert len(out["rules"]) == 1 and out["rules"][0]["category"] == "Kids"


def test_derive_tolerates_a_hostile_prior_doc():
    rows, answers = sequence([D, D, D])
    for bad in (None, "x", [], {"rules": "x"}, {"rules": [None, 5, {"vendor": 1}], "history": "nope", "updated_at": 5}):
        out, _ = derive(rows, doc(*answers), bad)
        assert pairs(out) == {"Cora's Bakery": D} and out["history"] == [
            {"at": at(2), "event": "promoted", "vendor": "Cora's Bakery", "category": "Dining"}]
        assert out["updated_at"] is None


# ----------------------------------------------------------------- load

def test_load_tolerates_missing_corrupt_and_hostile_files(tmp_path):
    p = tmp_path / "learned_rules.json"
    assert L.load(p) == (L.empty_doc(), [])
    for bad in ("not json", "[]", '{"rules": "x"}', '{"version": 1}'):
        p.write_text(bad, "utf-8")
        d, checks = L.load(p)
        assert d == L.empty_doc() and [c.name for c in checks] == ["learned_rules.load"] and checks[0].status == "warn"
    p.write_text(json.dumps({"version": 1, "updated_at": 5, "history": "nope", "rules": [
        {"vendor": 1, "category": "Dining"},                                   # vendor not a string
        {"vendor": "A", "category": None},                                     # no category
        {"vendor": "B", "category": "Dining", "subcategory": 5},               # subcategory not str|None
        {"vendor": "c", "category": "Kids"},                                   # well-formed: kept
        {"vendor": "C", "category": "Dining", "evidence": "zzz", "learned_at": 42},   # casefold duplicate of "c": skipped (file order wins)
        "garbage",
    ]}), "utf-8")
    d, checks = L.load(p)
    assert [r["vendor"] for r in d["rules"]] == ["c"]                          # the first of the case pair, normalized
    assert d["rules"][0]["evidence"] == {"scope": "txn", "n_agree": 0, "n_total": 0, "answer_keys": []}
    assert d["rules"][0]["learned_at"] is None and d["updated_at"] is None and d["history"] == []
    assert len(checks) == 1 and checks[0].name == "learned_rules.load" and "5 malformed" in checks[0].detail


def test_load_never_raises_on_undecodable_unreadable_or_pathological_input(tmp_path):
    p = tmp_path / "learned_rules.json"
    p.write_bytes(b"\xff\xfe\x00 not utf-8 \x80")
    d, checks = L.load(p)
    assert d == L.empty_doc() and [c.name for c in checks] == ["learned_rules.load"]
    p.write_text("[" * 200000, "utf-8")                                       # RecursionError inside json
    d, checks = L.load(p)
    assert d == L.empty_doc() and [c.name for c in checks] == ["learned_rules.load"]
    d, checks = L.load(tmp_path)                                               # a directory where the file should be
    assert d == L.empty_doc() and [c.name for c in checks] == ["learned_rules.load"]
    d, checks = L.load(tmp_path / "scenarios" / "learned_rules.json")          # missing parent: just missing
    assert (d, checks) == (L.empty_doc(), [])
    (tmp_path / "afile").write_text("x", "utf-8")
    d, checks = L.load(tmp_path / "afile" / "learned_rules.json")              # parent is a file (NotADirectoryError)
    assert d == L.empty_doc() and [c.name for c in checks] == ["learned_rules.load"]


def test_load_reads_text_that_is_not_unicode_as_a_corrupt_file(tmp_path):
    """A lone surrogate anywhere in the file (a vendor, a key) would stop the ingest when the rule reaches the
    database: the file is corrupt, so learning starts over and the file is copied aside before it is replaced."""
    p = tmp_path / "learned_rules.json"
    for bad in ({"version": 1, "rules": [{"vendor": "\ud800 Cafe", "category": "Dining"}]},
                {"version": 1, "rules": [], "\udfff": 1}):
        p.write_text(json.dumps(bad), "utf-8")
        d, checks = L.load(p)
        assert d == L.empty_doc() and [c.name for c in checks] == ["learned_rules.load"]
        assert "corrupt" in checks[0].detail and L._load(p).state == "corrupt"


def test_load_drops_malformed_history_events_with_a_warn(tmp_path):
    p = tmp_path / "learned_rules.json"
    ok = {"at": at(1), "event": "demoted", "vendor": "x", "category": "Dining"}
    p.write_text(json.dumps({"version": 1, "rules": [], "history": [
        ok, "junk", {"at": at(2), "event": "exploded", "vendor": "x", "category": "Dining"},
        {"at": 5, "event": "promoted", "vendor": "x", "category": "Dining"},
        {"at": at(3), "event": "promoted", "vendor": "x", "category": "Dining", "extra": 1},
    ]}), "utf-8")
    d, checks = L.load(p)
    assert d["history"] == [ok, {"at": at(3), "event": "promoted", "vendor": "x", "category": "Dining"}]
    assert len(checks) == 1 and checks[0].name == "learned_rules.load" and "3 malformed" in checks[0].detail


def test_load_normalizes_a_good_file_and_sorts_by_casefold(tmp_path):
    p = tmp_path / "learned_rules.json"
    good = {"version": 1, "updated_at": "2026-07-16T10:00:00Z", "history": [{"at": at(1), "event": "promoted",
            "vendor": "b shop", "category": "Dining"}],
            "rules": [rule("b shop", D, confirmed=at(1)), rule("Alpha", K, scope="vendor", n=1, total=1)]}
    p.write_text(json.dumps(good), "utf-8")
    d, checks = L.load(p)
    assert checks == [] and [r["vendor"] for r in d["rules"]] == ["Alpha", "b shop"]
    assert d["updated_at"] == "2026-07-16T10:00:00Z" and d["history"] == good["history"]


# ------------------------------------------------------------------ write

def test_write_if_changed_compares_rules_only_and_skips_empty(tmp_path):
    p = tmp_path / "learned_rules.json"
    assert L.write_if_changed(p, L.empty_doc()) == (False, []) and not p.exists()   # nothing to learn, no file
    d = {**L.empty_doc(), "rules": [rule("Cora's Bakery", D)], "history": [{"at": at(0), "event": "promoted",
                                                                          "vendor": "Cora's Bakery", "category": "Dining"}]}
    assert L.write_if_changed(p, d) == (True, [])
    on_disk = json.loads(p.read_text("utf-8"))
    assert on_disk["rules"] == d["rules"] and on_disk["history"] == d["history"]
    assert on_disk["updated_at"].endswith("Z") and len(on_disk["updated_at"]) == 20
    stamp = p.stat().st_mtime_ns
    # same rules, different history / updated_at -> not rewritten
    assert L.write_if_changed(p, {**d, "history": [], "updated_at": "1999-01-01T00:00:00Z"}) == (False, [])
    assert p.stat().st_mtime_ns == stamp
    # changed rules -> rewritten; emptied rules with a file present -> rewritten (the file says what is active)
    assert L.write_if_changed(p, {**d, "rules": [rule("Cora's Bakery", K)]}) == (True, [])
    assert L.write_if_changed(p, L.empty_doc()) == (True, [])
    assert json.loads(p.read_text("utf-8"))["rules"] == []
    # a corrupt file is replaced even when the rules compare equal to "nothing"
    p.write_text("not json", "utf-8")
    assert L.write_if_changed(p, L.empty_doc()) == (True, [])
    assert json.loads(p.read_text("utf-8"))["rules"] == []


def test_write_if_changed_survives_an_untraversable_parent(tmp_path, monkeypatch):
    # a corrupt file and empty rules take the "is there a file at all?" branch; Path.exists() raising
    # PermissionError (a parent dir that turned untraversable after the read) must be the warn, not an abort
    p = tmp_path / "scenarios" / "learned_rules.json"
    p.parent.mkdir()
    p.write_text("{ torn", "utf-8")

    def deny(self, *a, **kw):
        raise PermissionError(13, "Permission denied", str(self))
    monkeypatch.setattr(type(p), "exists", deny)
    written, checks = L.write_if_changed(p, L.empty_doc())
    assert written is False
    assert [(c.name, c.status) for c in checks] == [("learned_rules.write_failed", "warn")]


def test_an_unreadable_learned_file_is_never_written_over(tmp_path, monkeypatch):
    # tests run as root, so chmod cannot stage a refusal: the read raises PermissionError for this one path.
    # The file holds a sticky vendor-scope rule and history that no answer can rebuild - overwriting it with
    # rules derived from the answers alone is the data loss this pins
    p = tmp_path / "learned_rules.json"
    sticky = {**L.empty_doc(), "rules": [rule("Cora's Bakery", K, scope="vendor", n=1, total=1)],
              "history": [{"at": at(0), "event": "promoted", "vendor": "Cora's Bakery", "category": "Kids"}]}
    p.write_text(json.dumps(sticky), "utf-8")
    before = p.read_bytes()
    real = L.files.read_regular

    def deny(path, *a, **kw):
        if Path(path) == p:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, *a, **kw)
    monkeypatch.setattr(L.files, "read_regular", deny)
    d, load_checks = L.load(p)
    assert d == L.empty_doc() and [(c.name, c.status) for c in load_checks] == [("learned_rules.load", "warn")]
    assert "unreadable" in load_checks[0].detail and "nothing is saved over the file" in load_checks[0].detail
    written, checks = L.write_if_changed(p, {**L.empty_doc(), "rules": [rule("Other Shop", D)]})
    assert written is False
    assert [(c.name, c.status) for c in checks] == [("learned_rules.write_failed", "warn")]
    assert "not saved" in checks[0].detail and "PermissionError" in checks[0].detail and "Other Shop" not in checks[0].detail
    assert p.read_bytes() == before and not (tmp_path / "learned_rules.json.corrupt").exists()
    assert L.write_if_changed(p, L.empty_doc()) == (False, checks)                  # empty rules take no other way in


@pytest.mark.parametrize("kind", ["link", "fifo", "folder"])
def test_a_name_holding_no_regular_file_is_never_read_copied_or_written_through(tmp_path, kind):
    """A link at the learned file's name (to the feed credential, say) is never followed: nothing is read through it,
    no copy of it is kept, nothing is written over it, and the load says what is there. A FIFO answers at once."""
    secret = tmp_path / "secret"
    secret.write_bytes(b"SECRET-FEED-ACCESS-URL")
    p = tmp_path / "engine" / "learned_rules.json"
    p.parent.mkdir()
    if kind == "link":
        p.symlink_to(secret)
    elif kind == "fifo":
        os.mkfifo(p)
    else:
        p.mkdir()
    d, checks = L.load(p)
    assert d == L.empty_doc() and [(c.name, c.status) for c in checks] == [("learned_rules.load", "warn")]
    assert "not a regular file" in checks[0].detail
    written, wchecks = L.write_if_changed(p, {**L.empty_doc(), "rules": [rule("Cora's Bakery", D)]})
    assert written is False and [c.name for c in wchecks] == ["learned_rules.write_failed"]
    assert secret.read_bytes() == b"SECRET-FEED-ACCESS-URL"
    assert sorted(x.name for x in p.parent.iterdir()) == ["learned_rules.json"], "no copy aside, no temporary file"


def test_the_copy_aside_is_written_from_the_bytes_read_never_from_the_name(tmp_path, monkeypatch):
    """Between the read and the copy the name could come to hold something else; the copy is of what was read."""
    p = tmp_path / "learned_rules.json"
    p.write_bytes(b"{ torn")
    real = L._load

    def then_swap(path):
        loaded = real(path)
        p.unlink()
        p.write_bytes(b"swapped in after the read")
        return loaded
    monkeypatch.setattr(L, "_load", then_swap)
    assert L.write_if_changed(p, {**L.empty_doc(), "rules": [rule("Cora's Bakery", D)]}) == (True, [])
    assert (tmp_path / "learned_rules.json.corrupt").read_bytes() == b"{ torn"


def test_a_corrupt_learned_file_is_copied_aside_before_it_is_healed(tmp_path):
    p = tmp_path / "learned_rules.json"
    aside = tmp_path / "learned_rules.json.corrupt"
    d = {**L.empty_doc(), "rules": [rule("Cora's Bakery", D)]}
    for bad in (b"{ torn", b"[]", b'{"rules": "x"}', b"\xff\xfe\x00 not utf-8 \x80"):
        p.write_bytes(bad)
        assert L.write_if_changed(p, d) == (True, [])
        assert aside.read_bytes() == bad, bad                                    # the evidence survives the heal ...
        assert json.loads(p.read_text("utf-8"))["rules"] == d["rules"]           # ... and the file is healed
        p.unlink()
    # a file that loads with entries skipped is healed by the same rewrite, so it is copied aside too
    # (a skipped rule or event would otherwise vanish for good) ...
    aside.unlink()
    p.write_text(json.dumps({"version": 1, "rules": [rule("Cora's Bakery", D), {"vendor": 1}],
                             "history": ["junk"]}), "utf-8")
    original = p.read_bytes()
    assert L.write_if_changed(p, d) == (True, [])
    assert aside.read_bytes() == original and json.loads(p.read_text("utf-8"))["rules"] == d["rules"]
    # ... and an ordinary change to a clean file is not
    aside.unlink()
    assert L.write_if_changed(p, {**d, "rules": [rule("Cora's Bakery", K)]}) == (True, [])
    assert not aside.exists()


def test_a_damaged_file_that_cannot_be_copied_aside_is_not_written_over(tmp_path, monkeypatch):
    p = tmp_path / "learned_rules.json"
    p.write_text("{ torn", "utf-8")
    real = L.files.write_atomic

    def refuse(path, data):
        if Path(path).name.endswith(L.CORRUPT_SUFFIX):
            raise PermissionError(13, "Permission denied")
        return real(path, data)
    monkeypatch.setattr(L.files, "write_atomic", refuse)
    written, checks = L.write_if_changed(p, {**L.empty_doc(), "rules": [rule("Cora's Bakery", D)]})
    assert written is False and [(c.name, c.status) for c in checks] == [("learned_rules.write_failed", "warn")]
    assert "not saved" in checks[0].detail and p.read_text("utf-8") == "{ torn"
    # the same for a file that only loads with entries skipped
    q = tmp_path / "second" / "learned_rules.json"
    q.parent.mkdir()
    q.write_text(json.dumps({"version": 1, "rules": [rule("Cora's Bakery", D), {"vendor": 1}]}), "utf-8")
    before = q.read_bytes()
    written, checks = L.write_if_changed(q, {**L.empty_doc(), "rules": [rule("Cora's Bakery", D)]})
    assert written is False and [(c.name, c.status) for c in checks] == [("learned_rules.write_failed", "warn")]
    assert q.read_bytes() == before


def test_the_history_is_capped_at_the_newest_events_when_written(tmp_path):
    p = tmp_path / "learned_rules.json"
    events_ = [{"at": at(i % 100), "event": "promoted", "vendor": f"Shop {i:03d}", "category": "Dining"}
               for i in range(L.HISTORY_CAP + 25)]
    d = {**L.empty_doc(), "rules": [rule("Cora's Bakery", D)], "history": events_}
    assert L.write_if_changed(p, d) == (True, [])
    on_disk = json.loads(p.read_text("utf-8"))["history"]
    assert len(on_disk) == L.HISTORY_CAP == 500 and on_disk == events_[25:]         # the newest 500, oldest dropped
    assert len(d["history"]) == L.HISTORY_CAP + 25                                  # the caller's doc is not trimmed in memory


def test_write_if_changed_turns_oserror_into_one_warn(tmp_path):
    # tests run as root in the image, so chmod cannot stage a read-only failure: a regular file where the
    # parent directory should be makes the read raise NotADirectoryError (an OSError) for anyone, so this
    # reaches the unreadable branch; the write-failure branch itself is pinned by the fsync tests below
    (tmp_path / "scenarios").write_text("i am a file, not a dir", "utf-8")
    p = tmp_path / "scenarios" / "learned_rules.json"
    written, checks = L.write_if_changed(p, {**L.empty_doc(), "rules": [rule("Cora's Bakery", D)]})
    assert written is False
    assert [(c.name, c.status) for c in checks] == [("learned_rules.write_failed", "warn")]
    assert "read-write" in checks[0].detail and "Cora" not in checks[0].detail


def test_the_learned_file_is_written_durably_temp_fsynced_before_the_rename_dir_after(tmp_path, monkeypatch):
    p = tmp_path / "scenarios" / "learned_rules.json"
    seen = []

    def spy(fd):
        kind = "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        temp = any(x.name.startswith(f".{p.name}.") for x in p.parent.iterdir())
        seen.append((kind, temp, p.exists()))
    monkeypatch.setattr(os, "fsync", spy)
    assert L.write_if_changed(p, {**L.empty_doc(), "rules": [rule("Cora's Bakery", D)]}) == (True, [])
    # the temp file is synced while it still exists and the target does not (so before the rename);
    # the directory is synced after it (the temp gone, the target there)
    assert seen == [("file", True, False), ("dir", False, True)]
    assert json.loads(p.read_text("utf-8"))["rules"][0]["vendor"] == "Cora's Bakery"


def test_a_failing_directory_fsync_is_best_effort_but_a_failing_file_fsync_is_the_write_warn(tmp_path, monkeypatch):
    real = os.fsync

    def fail_on(kind):
        def fsync(fd):
            if ("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file") == kind:
                raise OSError(5, "I/O error")
            real(fd)
        return fsync
    d = {**L.empty_doc(), "rules": [rule("Cora's Bakery", D)]}
    p = tmp_path / "a" / "learned_rules.json"
    monkeypatch.setattr(os, "fsync", fail_on("dir"))          # e.g. a filesystem that cannot sync a directory
    assert L.write_if_changed(p, d) == (True, [])
    assert json.loads(p.read_text("utf-8"))["rules"] == d["rules"]
    q = tmp_path / "b" / "learned_rules.json"
    monkeypatch.setattr(os, "fsync", fail_on("file"))         # the data itself could not be made durable
    written, checks = L.write_if_changed(q, d)
    assert written is False and [(c.name, c.status) for c in checks] == [("learned_rules.write_failed", "warn")]
    assert not q.exists()                                      # never renamed over the target

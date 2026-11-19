# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Learned rules through the real Pipeline.enrich (wiring): precedence slot 4 by casefolded
vendor, vendors.json agreement, the _finalize_venmo inbox effect, determinism with a prior doc,
the quality-report checks, and a write failure that warns instead of aborting. Synthetic rows
on the demo config (with the entries below added) and a temp scenarios dir only — never the
repo's scenarios/. Everything here is invented."""
from __future__ import annotations

import atexit
import copy
import functools
import json
import shutil
import tempfile
from pathlib import Path

import pytest
from conftest import (
    DEMO_TREE_DIRS,
    answer,
    demo_config,
    demo_copy,
    isolate,
    isolated_pipeline,
    make_txn,
    state_fingerprint,
)

from etl import files as etl_files
from etl import pipeline as pipeline_mod
from etl.config import Config
from etl.enrich import learned as L
from etl.enrich import renames as R
from etl.enrich import transfers as transfers_mod
from etl.models import BalanceObs, QualityCheck
from etl.pipeline import Pipeline
from tools.demo import household as hh

# a P2P marker no demo vendor pattern knows, so the rows reach the fallback vendor
P2P_RULES = {"rules": [], "venmo_patterns": {"debit": r"VENMO\s+PAYMENT", "credit": r"VENMO\s+CASHOUT",
                                             "zelle": "SYNTHP2P"}}

# What the demo's rulebook lacks and these tests need: one vendor pattern per generic payment family, naming the
# family's one vendor and keeping it out of learning (`learn: false`), last in the file; a keyword rule that parks
# checks in Review; and a keyword rule for a specific Cash & Misc purpose. Both keyword rules come first, ahead of
# the demo's P2P catch-all.
PAYMENT_FAMILIES = [
    {"pattern": r"^CHECK\b", "vendor": "Check", "learn": False},
    {"pattern": r"^WITHDRAWAL\b", "vendor": "Withdrawal", "learn": False},
    {"pattern": r"^OUTGOING WIRE\b", "vendor": "Outgoing Wire", "learn": False},
    {"pattern": r"\bZELLE PAYMENT TO\b", "vendor": "Zelle Payment", "learn": False},
]
KEYWORDS = [
    {"pattern": r"^CHECK\b", "category": "Cash & Misc", "subcategory": "Checks", "needs_review": True},
    {"pattern": r"\bNOTARY\b", "category": "Cash & Misc", "subcategory": "Legal"},
]

# the two demo accounts the rows post to
CHK, CARD = hh.CHECKING, hh.CARD_ALEX


@functools.cache
def config_dir() -> Path:
    """A demo_copy with PAYMENT_FAMILIES and KEYWORDS added, made once per process in a temporary directory removed
    when the interpreter exits. Every test of the module shares it, so it is read-only: `build` isolates each
    pipeline's writes (answers, learned rules, data, exports) into the test's tmp dir. The copy's parent holds
    `config`, `scenarios` and `data`; the persisting test below asserts that whole tree is unchanged after
    `persist_learned()` and `persist()`."""
    tmp = Path(tempfile.mkdtemp(prefix="ff-learned-pipeline-"))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    return demo_copy(tmp, {
        "vendor_rules.yaml": lambda doc: doc["patterns"].extend(PAYMENT_FAMILIES),
        "category_rules.yaml": lambda doc: doc.__setitem__("keyword_rules", [*KEYWORDS, *doc["keyword_rules"]])})


def at(i):
    return f"2026-07-01T{i:02d}:00:00Z"


def txn_answer(t, category, subcategory, i):
    return answer("txn", t, {"category": category, "subcategory": subcategory}, at(i))


def vendor_answer(vendor, category, subcategory, i):
    return answer("vendor", vendor, {"category": category, "subcategory": subcategory}, at(i))


def bakery_rows(n, big_last=False):
    """n purchases whose fallback vendor is "Baker's Dozen" (smart_title; the store number is
    cleanup noise). The last one is above the review threshold when big_last."""
    return [make_txn(desc="BAKER'S DOZEN #1234", cents=-90000 if (big_last and i == n - 1) else -1650,
                     account=CARD, date=f"2026-05-{1 + i:02d}") for i in range(n)]


def p2p(n):
    return [make_txn(desc="SYNTHP2P TO SYNTH PLUMBER #1234", cents=-90000, account=CHK,
                     date=f"2026-04-{1 + i:02d}") for i in range(n)]


def build(tmp_path, rows, answers=(), prior=None, cfg=None):
    cfg = cfg or Config(config_dir())
    cfg.transfer_rules = P2P_RULES
    cfg.overrides = []
    return isolated_pipeline(tmp_path, rows, cfg=cfg, answers=answers, answers_updated_at=at(23), prior=prior,
                             windows={a: ("2026-01-01", "2026-12-31") for a in (CHK, CARD)})


def prior_doc(*rules):
    return {"version": 1, "updated_at": at(1), "history": [], "rules": [
        {"vendor": v, "category": c, "subcategory": s, "learned_at": at(1), "last_confirmed_at": at(1),
         "evidence": {"scope": "txn", "n_agree": 3, "n_total": 3, "answer_keys": []}} for v, c, s in rules]}


def stamps(p):
    return [(t.category, t.subcategory, t.category_source, t.needs_review) for t in p.txns]


def checks_named(p, prefix):
    return {c.name: (c.status, c.detail) for c in p.checks if c.name.startswith(prefix)}


def exported(p, out):
    p._export_ledger(out)
    return {name: json.loads((out / name).read_text("utf-8"))
            for name in ("transactions.json", "vendors.json", "quality_report.json")}


# ------------------------------------------------------------- precedence slot 4

def test_three_answers_teach_the_vendor_and_its_other_rows_stamp_learned(tmp_path):
    rows = bakery_rows(7, big_last=True)
    p = build(tmp_path, rows, [txn_answer(rows[i], "Dining", "Coffee", i) for i in range(3)])
    p.enrich()
    assert stamps(p)[:3] == [("Dining", "Coffee", "answer", False)] * 3          # answers re-stamp their rows
    assert stamps(p)[3:] == [("Dining", "Coffee", "learned", False)] * 4         # the rest follow, review untouched
    assert [(r["vendor"], r["category"], r["subcategory"]) for r in p.learned_doc["rules"]] == \
        [("Baker's Dozen", "Dining", "Coffee")]
    assert p.learned_doc["rules"][0]["evidence"]["scope"] == "txn"
    assert [e["event"] for e in p.learned_events] == ["promoted"]
    active = checks_named(p, "learned_rules.")
    assert set(active) == {"learned_rules.active"}
    assert active["learned_rules.active"][0] == "info" and "Baker's Dozen" in active["learned_rules.active"][1]
    ex = exported(p, tmp_path / "out")
    assert sum(1 for t in ex["transactions.json"]["transactions"] if t["category_source"] == "learned") == 4
    vend = {v["vendor"]: v for v in ex["vendors.json"]["vendors"]}
    assert (vend["Baker's Dozen"]["category"], vend["Baker's Dozen"]["category_source"]) == ("Dining", "learned")
    assert any(c["name"] == "learned_rules.active" for c in ex["quality_report.json"]["checks"])


def test_persisting_writes_into_the_tests_dir_and_leaves_the_cached_copy_alone(tmp_path):
    # a pipeline's learned rules, database and answers land BESIDE the copy's config (in scenarios/ and data/ under
    # its parent) when it is not isolated: fingerprint the whole tree around a run that persists
    before = state_fingerprint(config_dir().parent, DEMO_TREE_DIRS)
    rows = bakery_rows(4)
    p = build(tmp_path, rows, [txn_answer(rows[i], "Dining", "Coffee", i) for i in range(3)])
    p.enrich()
    p.persist_learned()
    p.persist()
    assert p.learned_written is True and p.cfg.learned_path.exists()               # the writes went to tmp_path
    assert state_fingerprint(config_dir().parent, DEMO_TREE_DIRS) == before


def tags_only_answer(t, i, tags=("kids",)):
    return answer("txn", t, {"tags": list(tags)}, at(i))


def test_bulk_tags_only_answers_teach_nothing_restamp_nothing_and_pin_nothing(tmp_path):
    # the dashboard's bulk tag editor writes one {"tags": [...]} answer per selected row.
    # Run the same five rows with and without those answers through the real Pipeline.enrich: the
    # answers may add the tag and may change NOTHING else
    plain = build(tmp_path / "plain", bakery_rows(5, big_last=True))
    plain.enrich()
    rows = bakery_rows(5, big_last=True)
    tagged = build(tmp_path / "tagged", rows, [tags_only_answer(t, i) for i, t in enumerate(rows)])
    tagged.enrich()
    assert [t.tags for t in tagged.txns] == [sorted({*p.tags, "kids"}) for p in plain.txns]    # the tag landed ...
    assert stamps(tagged) == stamps(plain)            # ... category, provenance (not "answer") and review did not move
    assert all(t.category_source != "answer" for t in tagged.txns)
    assert tagged.learned_doc["rules"] == [] and tagged.learned_events == []      # five answers on one vendor taught nothing
    assert checks_named(tagged, "learned_rules.") == checks_named(plain, "learned_rules.")
    # not pinned: a vendor-scope CATEGORY answer reaches every one of those rows, exactly as it does when
    # they carry no tag answer at all - while a txn-scope CATEGORY answer on a row does hold it (the control)
    vendor_cat = vendor_answer("Baker's Dozen", "Dining", "Coffee", 9)
    vendor_only = build(tmp_path / "vendor-only", bakery_rows(5, big_last=True), [vendor_cat])
    vendor_only.enrich()
    rows = bakery_rows(5, big_last=True)
    both = build(tmp_path / "both", rows, [tags_only_answer(t, i) for i, t in enumerate(rows)] + [vendor_cat])
    both.enrich()
    assert plain.txns[-1].needs_review                # the big row the fallback flags ...
    # ... and the vendor answer teaches its vendor too, so no row is left to the fallback's review flag
    assert stamps(both) == stamps(vendor_only) == [("Dining", "Coffee", "answer", False)] * 5
    rows = bakery_rows(5, big_last=True)
    held = build(tmp_path / "held", rows, [txn_answer(rows[0], "Kids", "Childcare", 0), vendor_cat])
    held.enrich()
    assert stamps(held)[0][:3] == ("Kids", "Childcare", "answer") and stamps(held)[1][:3] == ("Dining", "Coffee", "answer")


def test_learned_rule_applies_across_case_and_vendors_json_agrees(tmp_path):
    # a rule stored under an older / typed spelling must hit the rows' normalized vendor
    rows = bakery_rows(3)
    p = build(tmp_path, rows, prior=prior_doc(("BAKER'S DOZEN", "Dining", "Coffee")))
    p.enrich()
    assert stamps(p) == [("Dining", "Coffee", "learned", False)] * 3
    assert [r["vendor"] for r in p.learned_doc["rules"]] == ["Baker's Dozen"]        # display follows the rows
    vend = {v["vendor"]: v for v in exported(p, tmp_path / "out")["vendors.json"]["vendors"]}
    assert vend["Baker's Dozen"]["category_source"] == "learned"
    # the same through a vendor-scope answer typed in caps. The answers pass runs after the
    # Categorizer and stamps the vendor's rows `answer` itself; the rule is stored once, under the
    # rows' spelling, and vendors.json reports it as learned
    p2 = build(tmp_path / "two", bakery_rows(3), [vendor_answer("BAKER'S DOZEN", "Dining", "Coffee", 5)])
    p2.enrich()
    assert stamps(p2) == [("Dining", "Coffee", "answer", False)] * 3
    assert [(r["vendor"], r["evidence"]["scope"]) for r in p2.learned_doc["rules"]] == [("Baker's Dozen", "vendor")]
    vend2 = {v["vendor"]: v for v in exported(p2, tmp_path / "out2")["vendors.json"]["vendors"]}
    assert vend2["Baker's Dozen"]["category_source"] == "learned"
    # once that answer is gone (the store never evicts one, but the file can be trimmed by hand), the written rule
    # keeps the rows' category as `learned`
    p3 = build(tmp_path / "three", bakery_rows(3), prior=p2.learned_doc)
    p3.enrich()
    assert stamps(p3) == [("Dining", "Coffee", "learned", False)] * 3
    assert [r["vendor"] for r in p3.learned_doc["rules"]] == ["Baker's Dozen"] and p3.learned_events == []


def test_learned_category_on_a_p2p_fallback_vendor_removes_it_from_the_inbox(tmp_path):
    # _finalize_venmo applies "Venmo/Zelle Unknown + needs_review" only while the
    # category is still Cash & Misc — a learned category skips it on purpose (docs/design/categorization-and-learned-rules.md)
    control = build(tmp_path / "control", p2p(5))
    control.enrich()
    assert set(stamps(control)) == {("Cash & Misc", "Venmo/Zelle Unknown", "fallback", True)}
    rows = p2p(5)
    p = build(tmp_path, rows, [txn_answer(rows[i], "Housing", "Home Services", i) for i in range(3)])
    p.enrich()
    assert stamps(p)[3:] == [("Housing", "Home Services", "learned", False)] * 2
    assert {t.vendor for t in p.txns} == {t.vendor for t in control.txns}     # same fallback vendor both runs


def test_a_learned_cash_and_misc_subcategory_on_a_p2p_payee_is_not_overwritten(tmp_path):
    # the label "Cash & Misc" is not the generic fallback - a learned rule naming Cash & Misc/Services for a
    # Zelle payee is the family's own decision, and _finalize_venmo must leave it (and the review flag) alone
    rows = p2p(5)
    p = build(tmp_path, rows, [txn_answer(rows[i], "Cash & Misc", "Services", i) for i in range(3)])
    p.enrich()
    assert len(p.learned_doc["rules"]) == 1 and p.learned_doc["rules"][0]["category"] == "Cash & Misc"
    assert stamps(p)[3:] == [("Cash & Misc", "Services", "learned", False)] * 2
    assert [s[:3] for s in stamps(p)[:3]] == [("Cash & Misc", "Services", "answer")] * 3     # the answered rows took their own
    assert checks_named(p, "learned_rules.")["learned_rules.active"][1].startswith("1 learned vendor rule")


def test_an_explicit_vendor_rule_naming_cash_and_misc_for_a_p2p_payee_is_not_overwritten(tmp_path):
    cfg = Config(config_dir())
    cfg.vendor_rules = {**cfg.vendor_rules, "patterns": [
        {"pattern": "SYNTH PLUMBER", "vendor": "Synth Plumber", "category": "Cash & Misc", "subcategory": "Services"},
        *cfg.vendor_rules["patterns"]]}
    p = build(tmp_path, p2p(3), cfg=cfg)
    p.enrich()
    assert {t.vendor for t in p.txns} == {"Synth Plumber"}
    assert stamps(p) == [("Cash & Misc", "Services", "vendor_rule", False)] * 3


def test_the_p2p_catch_all_keyword_row_is_still_restamped_fallback_unknown(tmp_path, monkeypatch):
    # the predicate protects `learned`, `vendor_rule` and a keyword rule's SPECIFIC Cash & Misc subcategory: the
    # catch-all's Venmo/Zelle Unknown label on a P2P outflow is still the generic treatment. The premise first:
    # finalize_venmo switched off, these rows carry keyword provenance, so a `category_source == "fallback"` guard
    # would let them through
    def catch_all_rows():     # "VENMO" is the catch-all keyword rule's text and P2P_RULES' debit pattern marks the row P2P
        return [make_txn(desc="VENMO PAYMENT SYNTH PLUMBER #1234", cents=-90000, account=CHK,
                         date=f"2026-04-{1 + i:02d}") for i in range(2)]
    monkeypatch.setattr(transfers_mod.TransferEngine, "_finalize_venmo", lambda *a, **k: None)
    raw = build(tmp_path / "raw", catch_all_rows())
    raw.enrich()
    assert set(stamps(raw)) == {("Cash & Misc", "Venmo/Zelle Unknown", "keyword", True)}
    monkeypatch.undo()
    treated = build(tmp_path / "treated", catch_all_rows())
    treated.enrich()
    assert set(stamps(treated)) == {("Cash & Misc", "Venmo/Zelle Unknown", "fallback", True)}


def test_a_keyword_rules_specific_cash_and_misc_subcategory_on_a_p2p_outflow_stands(tmp_path):
    # "NOTARY" is a keyword rule (Cash & Misc / Legal, no review flag) ahead of the P2P catch-all. The
    # descriptor says what the payment was, so _finalize_venmo keeps the label, its provenance and its review
    # flag, though the rows are P2P outflows above the review threshold
    def legal_rows():
        return [make_txn(desc="VENMO PAYMENT SYNTH NOTARY #1234", cents=-90000, account=CHK,
                         date=f"2026-04-{1 + i:02d}") for i in range(2)]
    p = build(tmp_path, legal_rows())
    p.enrich()
    assert {t.type for t in p.txns} == {"purchase"}
    assert stamps(p) == [("Cash & Misc", "Legal", "keyword", False)] * 2


def checks_rows(n):
    """n different outgoing checks (the number is cleanup noise): ONE fallback-shaped vendor,
    "Check", above the review threshold - KEYWORDS parks them in Review on purpose."""
    return [make_txn(desc=f"CHECK {1001 + i}", cents=-400000, account=CHK, date=f"2026-08-{1 + i:02d}")
            for i in range(n)]


def test_agreeing_answers_on_different_checks_never_teach_the_generic_check_vendor(tmp_path):
    # the reviewer's rail repro: three different checks answered alike would otherwise promote
    # "Check" and recategorize every check, silently emptying them out of Review. The positive
    # control rides in the same pipeline: three agreeing answers on a normal fallback vendor DO promote
    checks_, shop = checks_rows(5), bakery_rows(4)
    p = build(tmp_path, checks_ + shop,
              [txn_answer(checks_[i], "Kids", "Childcare", i) for i in range(3)]
              + [txn_answer(shop[i], "Dining", "Coffee", 3 + i) for i in range(3)])
    p.enrich()
    assert {t.vendor for t in p.txns[:5]} == {"Check"}
    assert [r["vendor"] for r in p.learned_doc["rules"]] == ["Baker's Dozen"]            # Check never learned
    assert [s[:3] for s in stamps(p)[:3]] == [("Kids", "Childcare", "answer")] * 3        # the answered checks keep their answers
    assert stamps(p)[3:5] == [("Cash & Misc", "Checks", "keyword", True)] * 2             # the rest stay in Review
    assert [s[:3] for s in stamps(p)[5:8]] == [("Dining", "Coffee", "answer")] * 3
    assert stamps(p)[8] == ("Dining", "Coffee", "learned", False)                        # positive control promoted
    active = checks_named(p, "learned_rules.")["learned_rules.active"][1]
    assert "1 learned vendor rule" in active and "1 vendor(s) skipped: excluded from learning" in active
    vend = {v["vendor"]: v for v in exported(p, tmp_path / "out")["vendors.json"]["vendors"]}
    assert (vend["Check"]["has_rule"], vend["Check"]["learn"], vend["Check"]["category"]) == (True, False, None)


@pytest.mark.parametrize("desc", ["WITHDRAWAL 12/31", "OUTGOING WIRE 123456", "ZELLE PAYMENT TO SYNTH PAYEE #1234"])
def test_the_other_generic_payment_families_are_not_learnable_either(tmp_path, desc):
    rows = [make_txn(desc=desc.replace("PAYEE", f"PAYEE{i}"), cents=-90000, account=CHK,
                     date=f"2026-08-{1 + i:02d}") for i in range(4)]
    p = build(tmp_path, rows, [txn_answer(rows[i], "Housing", "Home Services", i) for i in range(3)])
    p.enrich()
    assert p.learned_doc["rules"] == [] and p.learned_events == []
    assert stamps(p)[3][2] != "learned" and stamps(p)[3][3] is True        # still in Review, not recategorized


def test_conflict_warn_reaches_the_quality_report_and_rows_keep_vendor_rule(tmp_path):
    # "UBER *TRIP" normalizes to the explicit Uber rule (Transport / Rideshare)
    rows = [make_txn(desc="UBER *TRIP #1234", cents=-1500, account=CARD, date=f"2026-05-{1 + i:02d}")
            for i in range(5)]
    p = build(tmp_path, rows, [txn_answer(rows[i], "Dining", "Restaurants", i) for i in range(3)])
    p.enrich()
    assert stamps(p)[:3] == [("Dining", "Restaurants", "answer", False)] * 3
    assert stamps(p)[3:] == [("Transport", "Rideshare", "vendor_rule", False)] * 2
    assert p.learned_doc["rules"] == []
    conflict = checks_named(p, "learned_rules.conflict")
    assert len(conflict) == 1 and conflict["learned_rules.conflict"][0] == "warn"
    assert "Uber" in conflict["learned_rules.conflict"][1] and "tx_" not in conflict["learned_rules.conflict"][1]
    qr = exported(p, tmp_path / "out")["quality_report.json"]
    assert [c["status"] for c in qr["checks"] if c["name"] == "learned_rules.conflict"] == ["warn"]


def test_a_learned_rule_never_flips_a_row_an_override_categorizes(tmp_path):
    # the overrides half of "learned rules never outrank explicit YAML": three agreeing answers teach the vendor,
    # and overrides.yaml entries categorize two of its rows - one unanswered (rows[4]) and one that carries one of
    # the teaching answers (rows[0]). Both keep the override's pair with source `override`; the unanswered
    # neighbours (the control) take the learned rule. Dies if the learned rule were replayed after the overrides
    # (rows[4]), or the overrides ran before the Categorizer - the answers would then land on top of them (rows[0]).
    rows = bakery_rows(6)
    p = build(tmp_path, rows, [txn_answer(rows[i], "Dining", "Coffee", i) for i in range(3)])
    p.cfg.overrides = [{"match": {"uid": rows[i].uid}, "set": {"category": "Kids", "subcategory": "Childcare"}}
                       for i in (0, 4)]
    p.enrich()
    assert [(r["vendor"], r["category"]) for r in p.learned_doc["rules"]] == [("Baker's Dozen", "Dining")]
    assert [stamps(p)[i][:3] for i in (0, 4)] == [("Kids", "Childcare", "override")] * 2
    assert [stamps(p)[i][:3] for i in (3, 5)] == [("Dining", "Coffee", "learned")] * 2
    assert "learned_rules.conflict" not in checks_named(p, "learned_rules.")        # an override is not a vendor rule
    assert checks_named(p, "answers.conflict")["answers.conflict"][0] == "info"    # rows[0]'s answer lost: Review shows it


def test_prior_learned_doc_follows_a_vendor_rename(tmp_path):
    rows = [make_txn(desc="SYNTH WIDGET EMPORIUM #4411", cents=-2500, account=CARD,
                     date=f"2026-03-{1 + i:02d}") for i in range(2)]
    p = build(tmp_path, rows, prior=prior_doc(("Old Emporium", "Dining", "Coffee")))
    p.cfg.vendor_renamer = R.VendorRenamer([{"from": "Old Emporium", "to": "Synth Widget Emporium"}])
    p.enrich()
    assert stamps(p) == [("Dining", "Coffee", "learned", False)] * 2
    assert [r["vendor"] for r in p.learned_doc["rules"]] == ["Synth Widget Emporium"]
    assert json.loads(p.cfg.learned_path.read_text("utf-8")) == prior_doc(("Old Emporium", "Dining", "Coffee"))  # on-disk untouched by enrich


def test_prior_learned_doc_follows_a_category_rename_into_the_rows_and_vendors_json(tmp_path):
    """Config accepts a non-empty category renames: list. A real Config carrying
    one (a whole-category rename off a retired name and a pair rename) maps the prior learned doc
    inside Pipeline.enrich, so the rule applies under its NEW category, vendors.json reports it,
    and nothing is warned as an off-taxonomy reference."""
    renames = [
        {"from": {"category": "Eating Out"}, "to": {"category": "Dining"}},
        {"from": {"category": "Housing", "subcategory": "Home Services"},
         "to": {"category": "Housing", "subcategory": "Home Improvement"}}]
    d = demo_copy(tmp_path, {"category_rules.yaml": lambda doc: doc.__setitem__("renames", renames)})
    rows = [make_txn(desc="SYNTH WIDGET EMPORIUM #4411", cents=-2500, account=CARD,
                     date=f"2026-03-{1 + i:02d}") for i in range(2)] + \
           [make_txn(desc="SYNTH BOLT DEPOT #7788", cents=-3500, account=CARD,
                     date=f"2026-03-{10 + i:02d}") for i in range(2)]
    prior = prior_doc(("Synth Widget Emporium", "Eating Out", "Coffee"),
                      ("Synth Bolt Depot", "Housing", "Home Services"))
    p = build(tmp_path / "run", rows, prior=prior, cfg=Config(d))
    assert p.cfg.category_renames == renames
    p.enrich()
    assert stamps(p) == [("Dining", "Coffee", "learned", False)] * 2 + \
                        [("Housing", "Home Improvement", "learned", False)] * 2
    assert {r["vendor"]: (r["category"], r["subcategory"]) for r in p.learned_doc["rules"]} == \
        {"Synth Widget Emporium": ("Dining", "Coffee"), "Synth Bolt Depot": ("Housing", "Home Improvement")}
    assert [c for c in p.checks if c.name == "taxonomy.unknown_reference"] == []
    vend = {v["vendor"]: v for v in exported(p, tmp_path / "out")["vendors.json"]["vendors"]}
    assert (vend["Synth Widget Emporium"]["category"], vend["Synth Widget Emporium"]["subcategory"],
            vend["Synth Widget Emporium"]["category_source"]) == ("Dining", "Coffee", "learned")
    assert (vend["Synth Bolt Depot"]["category"], vend["Synth Bolt Depot"]["subcategory"],
            vend["Synth Bolt Depot"]["category_source"]) == ("Housing", "Home Improvement", "learned")
    assert json.loads(p.cfg.learned_path.read_text("utf-8")) == prior          # on-disk untouched by enrich


def test_an_off_taxonomy_prior_rule_warns_once_and_is_dropped(tmp_path):
    p = build(tmp_path, bakery_rows(2), prior=prior_doc(("Baker's Dozen", "Eating Out", None)))
    p.enrich()
    warns = [c for c in p.checks if c.name == "taxonomy.unknown_reference"]
    assert len(warns) == 1 and "learned Baker's Dozen" in warns[0].detail and "Eating Out" not in warns[0].detail
    assert p.learned_doc["rules"] == []
    assert "learned" not in {s[2] for s in stamps(p)}                 # the dropped rule applied nowhere
    assert "Eating Out" not in {s[0] for s in stamps(p)}


# ---------------------------------------------------------- determinism

def test_second_enrich_with_the_written_doc_as_prior_is_identical(tmp_path):
    def answers_for(rows):
        return [txn_answer(rows[i], "Dining", "Coffee", i) for i in range(3)] + \
               [vendor_answer("Synth Plumber Co", "Housing", "Home Services", 9)]
    rows1 = bakery_rows(6, big_last=True) + p2p(2)
    p1 = build(tmp_path, rows1, answers_for(rows1))
    p1.enrich()
    written, checks = L.write_if_changed(p1.cfg.learned_path, p1.learned_doc)
    assert written and checks == []
    rows2 = bakery_rows(6, big_last=True) + p2p(2)                       # fresh rows, same uids
    p2 = build(tmp_path, rows2, answers_for(rows2))                 # prior = the file p1 wrote
    p2.enrich()
    assert stamps(p2) == stamps(p1)
    assert p2.learned_doc["rules"] == p1.learned_doc["rules"]
    assert checks_named(p2, "learned_rules.") == checks_named(p1, "learned_rules.")
    assert p2.learned_events == []                                  # nothing new happened
    assert L.write_if_changed(p2.cfg.learned_path, p2.learned_doc) == (False, [])
    out1, out2 = exported(p1, tmp_path / "o1"), exported(p2, tmp_path / "o2")
    assert out1["transactions.json"] == out2["transactions.json"] and out1["vendors.json"] == out2["vendors.json"]
    assert out1["quality_report.json"] == out2["quality_report.json"]    # state checks only: no wall clock, no events


# ------------------------------------------------------------ persist_learned

def test_a_read_only_scenarios_mount_warns_but_never_aborts(tmp_path):
    # a `:ro` mount surfaces as OSError on the .tmp write; staged here with a
    # regular file where the dir should be (tests run as root, chmod would not bite). That exercises
    # the same OSError -> `learned_rules.write_failed` warn path; the real `:ro` bind mount was
    # verified manually to behave the same.
    rows = bakery_rows(4)
    p = build(tmp_path, rows, [txn_answer(rows[i], "Dining", "Coffee", i) for i in range(3)])
    p.enrich()                                                       # loads from the (absent) temp file: no load warn
    (tmp_path / "ro").write_text("not a directory", "utf-8")
    p.cfg.learned_path = tmp_path / "ro" / "learned_rules.json"      # now make the WRITE fail
    p.persist_learned()
    assert p.learned_written is False
    named = checks_named(p, "learned_rules.")
    assert set(named) == {"learned_rules.active", "learned_rules.write_failed"}
    assert named["learned_rules.write_failed"][0] == "warn"
    assert stamps(p)[3] == ("Dining", "Coffee", "learned", False)                 # applied this run anyway
    qr = exported(p, tmp_path / "out")["quality_report.json"]
    assert any(c["name"] == "learned_rules.write_failed" for c in qr["checks"])


def test_persist_learned_skips_when_a_check_failed_and_writes_otherwise(tmp_path):
    rows = bakery_rows(4)
    p = build(tmp_path, rows, [txn_answer(rows[i], "Dining", "Coffee", i) for i in range(3)])
    p.enrich()
    p.checks.append(QualityCheck(name="synth.failing", status="fail", detail="x"))
    p.persist_learned()
    assert not p.cfg.learned_path.exists() and p.learned_written is False
    p.checks = [c for c in p.checks if c.status != "fail"]
    p.persist_learned()
    assert p.learned_written is True
    assert [r["vendor"] for r in json.loads(p.cfg.learned_path.read_text("utf-8"))["rules"]] == ["Baker's Dozen"]
    assert "learned_rules.write_failed" not in checks_named(p, "learned_rules.")


def test_a_failed_check_leaves_a_not_saved_note_naming_the_check(tmp_path):
    rows = bakery_rows(4)
    p = build(tmp_path, rows, [txn_answer(rows[i], "Dining", "Coffee", i) for i in range(3)])
    p.enrich()
    p.persist_learned()                                                       # no failure: written, and no note
    assert p.learned_written is True and "learned_rules.not_saved" not in checks_named(p, "learned_rules.")
    q = build(tmp_path / "failing", rows, [txn_answer(rows[i], "Dining", "Coffee", i) for i in range(3)])
    q.enrich()
    q.checks.append(QualityCheck(name="synth.failing", status="fail", detail="amount 12345 secret"))
    q.persist_learned()
    assert not q.cfg.learned_path.exists() and q.learned_written is False
    note = checks_named(q, "learned_rules.")["learned_rules.not_saved"]
    assert note[0] == "info" and "synth.failing" in note[1] and "secret" not in note[1]       # names the check, not its detail
    assert note[1].startswith("Learned rules were not saved this run: 1 check failed (")
    assert note[1].endswith("); they apply now and are saved once an ingest passes.")
    qr = exported(q, tmp_path / "out")["quality_report.json"]
    assert [c["status"] for c in qr["checks"] if c["name"] == "learned_rules.not_saved"] == ["info"]


def test_an_unreadable_prior_file_keeps_its_rules_and_the_report_says_they_were_not_saved(tmp_path, monkeypatch):
    # end to end (tests run as root: the refusal is staged on the read of this one path). The file holds a
    # sticky rule whose answers are long evicted; the run must not replace it with rules derived from answers alone
    rows = bakery_rows(4)
    p = build(tmp_path, rows, [txn_answer(rows[i], "Dining", "Coffee", i) for i in range(3)],
              prior=prior_doc(("Old Shop", "Dining", "Coffee")))
    before = p.cfg.learned_path.read_bytes()
    real = etl_files.read_regular

    def deny(path, *a, **kw):
        if Path(path) == p.cfg.learned_path:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, *a, **kw)
    monkeypatch.setattr(etl_files, "read_regular", deny)
    p.enrich()
    assert stamps(p)[3] == ("Dining", "Coffee", "learned", False)             # learning starts over, ingest proceeds
    p.persist_learned()
    assert p.learned_written is False and p.cfg.learned_path.read_bytes() == before
    named = checks_named(p, "learned_rules.")
    assert named["learned_rules.load"][0] == "warn" and named["learned_rules.write_failed"][0] == "warn"
    assert "not saved" in named["learned_rules.write_failed"][1]
    qr = exported(p, tmp_path / "out")["quality_report.json"]
    assert {c["name"] for c in qr["checks"]} >= {"learned_rules.load", "learned_rules.write_failed"}


def test_nothing_to_learn_writes_no_file_and_still_reports_zero_active(tmp_path):
    p = build(tmp_path, bakery_rows(2))
    p.enrich()
    p.persist_learned()
    assert not p.cfg.learned_path.exists()
    assert checks_named(p, "learned_rules.")["learned_rules.active"][1].startswith("0 learned vendor rule")


def test_a_corrupt_prior_file_warns_in_the_report_even_though_persist_replaces_it(tmp_path):
    # the load warn is raised in enrich(), before write_if_changed heals the file; it must still
    # be in quality_report.json afterwards (write_if_changed re-reads the file but returns no
    # load check of its own)
    rows = bakery_rows(4)
    p = build(tmp_path, rows, [txn_answer(rows[i], "Dining", "Coffee", i) for i in range(3)])
    p.cfg.learned_path.write_text("{ torn", "utf-8")
    p.enrich()
    assert stamps(p)[3] == ("Dining", "Coffee", "learned", False)        # learning starts over, ingest proceeds
    p.persist_learned()
    assert p.learned_written is True                                      # the corrupt file was replaced...
    assert [r["vendor"] for r in json.loads(p.cfg.learned_path.read_text("utf-8"))["rules"]] == ["Baker's Dozen"]
    assert p.cfg.learned_path.with_name("learned_rules.json.corrupt").read_text("utf-8") == "{ torn"   # ...a copy kept
    named = checks_named(p, "learned_rules.")
    assert named["learned_rules.load"][0] == "warn"                       # ...and the warn survives it
    qr = exported(p, tmp_path / "out")["quality_report.json"]
    assert [c["status"] for c in qr["checks"] if c["name"] == "learned_rules.load"] == ["warn"]


def test_print_summary_reports_active_rules_and_this_passes_events_on_stdout_only(tmp_path, capsys):
    rows = bakery_rows(4)
    p = build(tmp_path, rows, [txn_answer(rows[i], "Dining", "Coffee", i) for i in range(3)])
    p.enrich()
    p.print_summary()
    out = capsys.readouterr().out
    assert "learned rules: 1 active\n" in out and "(learned_rules.json written)" not in out
    assert "  promoted Baker's Dozen -> Dining\n" in out
    p.persist_learned()
    p.print_summary()
    assert "learned rules: 1 active (learned_rules.json written)\n" in capsys.readouterr().out
    # a quiet second pass: the rule is still active, the event is gone, nothing is written
    p2 = build(tmp_path, bakery_rows(4), [txn_answer(rows[i], "Dining", "Coffee", i) for i in range(3)])
    p2.enrich()
    p2.persist_learned()
    p2.print_summary()
    out2 = capsys.readouterr().out
    assert "learned rules: 1 active\n" in out2 and "promoted" not in out2


def test_run_persists_learned_after_finalize_quality_and_before_persist(tmp_path, monkeypatch):
    cfg = isolate(demo_config(), tmp_path)                                # the ingest lock lives in its data/
    monkeypatch.setattr(pipeline_mod, "Config", lambda source_dir=None: cfg)
    calls = []
    for name in ("parse_transactions", "parse_simplefin", "enrich", "parse_assets", "finalize_quality",
                 "persist_learned", "persist", "export", "print_summary"):
        monkeypatch.setattr(Pipeline, name, lambda self, n=name: calls.append(n))
    assert pipeline_mod.run() == 0
    assert calls == ["parse_transactions", "parse_simplefin", "enrich", "parse_assets", "finalize_quality",
                     "persist_learned", "persist", "export", "print_summary"]


def test_one_categorizer_constructor_serves_the_pipeline_and_the_rules_cli():
    # a future Categorizer argument must be added in one place: enrich() and the CLI both build
    # through pipeline.categorizer_for, and the CLI's table is the bare rulebook
    from etl import rules_cli
    cfg = demo_config()
    assert rules_cli.categorizer_for is pipeline_mod.categorizer_for
    assert pipeline_mod.categorizer_for(cfg).learned == {}
    got = pipeline_mod.categorizer_for(cfg, learned={"Baker's Dozen": {"category": "Dining", "subcategory": None}})
    assert set(got.learned) == {"baker's dozen"}
    assert got.owner_of(CARD) == cfg.owner_of()[CARD]


# ------------------------------------------------------------- ledger drift

def test_ledger_drift_eligibility_and_accounts_json_payload(tmp_path):
    rows = [make_txn(date="2026-09-05", account="card", desc="FEED", cents=-5000, bank_type="simplefin"),
            make_txn(date="2026-09-05", account="chk_csv", desc="CSV ROW", cents=-5000),         # feed-less: not eligible
            make_txn(date="2026-09-05", account="brk", desc="BUY", cents=-5000, bank_type="simplefin"),  # brokerage: wrong kind
            make_txn(date="2026-09-05", account="noid", desc="FEED NOID", cents=-5000, bank_type="simplefin")]  # no simplefin_id: not eligible
    p = build(tmp_path, rows)
    p.cfg.accounts_cfg = copy.deepcopy(p.cfg.accounts_cfg)
    p.cfg.accounts_cfg["accounts"] = {
        "card": {"name": "Synth Card", "institution": "Bank", "owner": "Joint", "kind": "credit-card", "simplefin_id": "ACT-1"},
        "chk_csv": {"name": "Synth CSV Checking", "institution": "Bank", "owner": "Joint", "kind": "checking", "simplefin_id": "ACT-2"},
        "noid": {"name": "Synth Savings", "institution": "Bank", "owner": "Joint", "kind": "savings"},
        "brk": {"name": "Synth Brokerage", "institution": "Broker", "owner": hh.ALEX, "kind": "brokerage",
                "simplefin_id": "ACT-3", "simplefin_cutover": "2026-01-01"},
    }
    p.balance_obs = [BalanceObs(a, d, c) for a, d, c in (
        ("card", "2026-09-01", -10000), ("card", "2026-09-10", -16000),
        ("chk_csv", "2026-09-01", 100), ("chk_csv", "2026-09-10", 200),
        ("noid", "2026-09-01", 100), ("noid", "2026-09-10", 200),
        ("brk", "2026-09-01", 100), ("brk", "2026-09-10", 200))]
    checks = p._ledger_drift_checks()
    assert [c.name for c in checks] == ["reconcile.card.ledger_drift"] and checks[0].status == "info"
    assert set(p.ledger_drift) == {"card"}
    assert p.ledger_drift["card"] == {"from": "2026-09-01", "to": "2026-09-10", "expected_cents": -6000,
                                      "got_cents": -5000, "drift_cents": 1000}
    p._export_accounts(tmp_path / "out")
    accounts = {a["id"]: a for a in json.loads((tmp_path / "out" / "accounts.json").read_text("utf-8"))["accounts"]}
    assert accounts["card"]["ledger_drift"] == {"from": "2026-09-01", "to": "2026-09-10", "expected": -60.0, "got": -50.0, "drift": 10.0}
    assert all(accounts[a]["ledger_drift"] is None for a in ("chk_csv", "noid", "brk"))


def test_ledger_drift_sums_csv_rows_before_a_cutover_inside_the_window(tmp_path):
    """Pipeline level (the reconcile unit tests pass a hand-built row list, so they cannot prove the
    pipeline hands over non-feed rows): the CSV owns the days before a simplefin_cutover and the feed
    the days from it, both inside (d1, d2] — `got` is the whole ledger, the drift is 0."""
    rows = [make_txn(date="2026-09-03", account="card", desc="CSV ROW", cents=-10000),          # before the cutover: CSV
            make_txn(date="2026-09-01", account="card", desc="CSV ROW", cents=-77777),          # on d1: half-open, out
            make_txn(date="2026-09-08", account="card", desc="FEED ROW", cents=-20000, bank_type="simplefin"),
            make_txn(date="2026-09-08", account="csv_only", desc="CSV ROW", cents=-5000)]       # no feed row at all: not eligible
    p = build(tmp_path, rows)
    p.cfg.accounts_cfg = copy.deepcopy(p.cfg.accounts_cfg)
    p.cfg.accounts_cfg["accounts"] = {
        "card": {"name": "Synth Card", "institution": "Bank", "owner": "Joint", "kind": "credit-card",
                 "simplefin_id": "ACT-1", "simplefin_cutover": "2026-09-05"},
        "csv_only": {"name": "Synth CSV Card", "institution": "Bank", "owner": "Joint", "kind": "credit-card",
                     "simplefin_id": "ACT-2", "simplefin_cutover": "2026-09-05"},
    }
    p.balance_obs = [BalanceObs(a, d, c) for a, d, c in (
        ("card", "2026-09-01", 0), ("card", "2026-09-10", -30000),
        ("csv_only", "2026-09-01", 0), ("csv_only", "2026-09-10", -5000))]
    p._ledger_drift_checks()
    assert set(p.ledger_drift) == {"card"}
    assert p.ledger_drift["card"] == {"from": "2026-09-01", "to": "2026-09-10", "expected_cents": -30000,
                                      "got_cents": -30000, "drift_cents": 0}

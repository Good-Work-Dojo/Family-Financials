# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""category_source: every categorization path stamps where a row's category came
from. Pure annotation — the categories themselves are unchanged. Synthetic rows, on the demo household's config."""
from __future__ import annotations

import pytest
from conftest import demo_config, isolate, load_demo, make_txn

from etl import exports
from etl.enrich import answers, overrides
from etl.enrich.categorize import Categorizer, set_internal
from etl.enrich.income import IncomeIdentifier
from etl.enrich.transfers import TransferEngine
from etl.enrich.vendors import VendorNormalizer
from etl.models import CATEGORY_SOURCES, INTERNAL_TYPES
from etl.pipeline import Pipeline
from tools.demo import household as hh
from tools.demo.behaviours import income, paths

load = load_demo


def daycare_vendor_rules() -> dict:
    """The demo's vendor rules with one entry that carries no category: the demo's daycare, which its keyword rule
    files."""
    rules = load("vendor_rules.yaml")
    rules["patterns"].insert(0, {"pattern": paths.DAYCARE_RULE["pattern"], "vendor": paths.DAYCARE_EXPECT.vendor})
    return rules


def categorize(desc, cents=-1000, *, category_rules=None, vendor_rules=None, **over):
    """One row through the vendor normalizer and the Categorizer on the demo's rulebook (`category_rules` and
    `vendor_rules`, when given, in place of daycare_vendor_rules() and a fresh copy of the demo's category rules)."""
    vendor_rules = vendor_rules or daycare_vendor_rules()
    n = VendorNormalizer(vendor_rules)
    t = make_txn(desc=desc, cents=cents, account=hh.CARD_ALEX, **over)
    if not t.vendor:
        t.vendor, _ = n.normalize(desc)
    Categorizer(category_rules or load("category_rules.yaml"), load("bank_category_map.yaml"),
                vendor_rules, load("assumptions.yaml"), {}).apply([t], n.clean)
    return t


def test_every_categorizer_path_stamps_its_source():
    internal = categorize("PAYMENT THANK YOU", 50000, type="cc-payment", is_internal=True)
    income_default = categorize("SOME DEPOSIT", 50000, type="income")
    fee_default = categorize("LATE FEE", type="fee")
    parser_income = categorize("SYNTH DIVIDEND", 500, type="dividend", category="Income", subcategory="Dividends")
    parser_purchase = categorize("VENMO PAYMENT TO PAT LEE", category="Housing",
                                 subcategory="Home Improvement")
    vendor_rule = categorize("KUMON #0042", bank_category="Entertainment")      # patterns entry WITH category:
    keyword = categorize(paths.DAYCARE, -150000, bank_category="Bills & Utilities")  # patterns entry WITHOUT
    bank_label = categorize("SOME RANDOM BOUTIQUE", -4200, bank_category="Shopping")
    fallback = categorize("MYSTERY MERCHANT XYZ", -90000)
    assert (internal.category, internal.category_source) == ("Internal", "internal")
    assert (income_default.category, income_default.category_source) == ("Income", "type_default")
    assert (fee_default.category, fee_default.category_source) == ("Fees & Interest", "type_default")
    assert (parser_income.subcategory, parser_income.category_source) == ("Dividends", "parser")
    assert (parser_purchase.category, parser_purchase.category_source) == ("Housing", "parser")
    assert (vendor_rule.vendor, vendor_rule.category, vendor_rule.category_source) == ("Kumon", "Kids", "vendor_rule")
    assert (keyword.vendor, keyword.category, keyword.subcategory, keyword.category_source) == \
        (paths.DAYCARE_EXPECT.vendor, "Kids", "Childcare", "keyword")
    assert (bank_label.category, bank_label.category_source) == ("Shopping", "bank_label")
    assert (fallback.category, fallback.category_source) == ("Cash & Misc", "fallback")
    assert fallback.needs_review
    # a row a categorized vendor pattern and a keyword rule both match files by the vendor pattern, though the
    # keyword rule is the first of its file and names another category
    def keyword_first():
        rules = load("category_rules.yaml")
        rules["keyword_rules"].insert(0, {"pattern": r"\bKUMON\b", "category": "Entertainment",
                                          "subcategory": "Hobbies"})
        return rules
    both = categorize("KUMON #0042", category_rules=keyword_first())
    assert (both.vendor, both.category, both.subcategory, both.category_source) == \
        ("Kumon", "Kids", "Activities", "vendor_rule")
    # control: without the vendor pattern the same keyword rule files the row
    patterns = daycare_vendor_rules()
    patterns["patterns"] = [p for p in patterns["patterns"] if p.get("vendor") != "Kumon"]
    keyword_only = categorize("KUMON #0042", category_rules=keyword_first(), vendor_rules=patterns)
    assert (keyword_only.category, keyword_only.subcategory, keyword_only.category_source) == \
        ("Entertainment", "Hobbies", "keyword")


def test_income_identifier_stamps_rule_hit_and_unknown_credit():
    ident = IncomeIdentifier(load("category_rules.yaml"), load("assumptions.yaml"), {hh.CHECKING})
    salary = make_txn(desc=income.QUILLWORKS_PAY.desc, cents=income.QUILLWORKS_PAY.cents, account=hh.CHECKING)
    unknown = make_txn(desc="RANDOM BIG CREDIT", cents=90000, account=hh.CHECKING)
    small = make_txn(desc="small refund", cents=900, account=hh.CHECKING)
    ident.apply([salary, unknown, small])
    assert (salary.subcategory, salary.earner, salary.employer, salary.category_source) == \
        ("Salary", hh.ALEX, hh.QUILLWORKS, "type_default")
    assert (unknown.type, unknown.category_source) == ("income", "fallback")
    assert small.category_source is None          # untouched: a small credit stays a purchase


def test_categorizer_keeps_a_stamp_an_earlier_stage_already_set():
    # IncomeIdentifier runs BEFORE the Categorizer and has stamped the rows it typed; the
    # Categorizer's "parser" stamp is only for pre-set categories nobody has stamped yet
    ident = IncomeIdentifier(load("category_rules.yaml"), load("assumptions.yaml"), {hh.CHECKING})
    salary = make_txn(desc=income.QUILLWORKS_PAY.desc, cents=income.QUILLWORKS_PAY.cents, account=hh.CHECKING)
    unknown = make_txn(desc="RANDOM BIG CREDIT", cents=90000, account=hh.CHECKING)
    ident.apply([salary, unknown])
    n = VendorNormalizer(load("vendor_rules.yaml"))
    for t in (salary, unknown):
        t.vendor, _ = n.normalize(t.description_raw)
    Categorizer(load("category_rules.yaml"), load("bank_category_map.yaml"),
                load("vendor_rules.yaml"), load("assumptions.yaml"), {}).apply([salary, unknown], n.clean)
    assert (salary.category, salary.category_source) == ("Income", "type_default")
    assert (unknown.category, unknown.category_source) == ("Income", "fallback")
    # the purchase early return honors a stamp too (the guard is not parser-only)
    stamped = categorize("X", category="Housing", subcategory="Home Improvement", category_source="keyword")
    assert (stamped.category, stamped.category_source) == ("Housing", "keyword")


def _engine():
    rules = {"rules": [], "venmo_patterns": {"debit": "VENMO", "credit": "CASHOUT", "zelle": "ZELLE"}}
    return TransferEngine([], rules, {}, {"chk": ("2026-01-01", "2026-12-31")}, {"chk"})


def test_finalize_venmo_stamps_unknown_p2p_and_cashout_but_not_identified_rows():
    debit = make_txn(desc="VENMO PAYMENT 123", cents=-5000, account="chk", date="2026-03-01")
    cashout = make_txn(desc="VENMO CASHOUT 123", cents=7000, account="chk", date="2026-03-02")
    known = make_txn(desc="VENMO PAYMENT 456", cents=-3000, account="chk", date="2026-03-03",
                     category="Dining", subcategory="Restaurants", category_source="keyword")
    rows = [debit, cashout, known]
    eng = _engine()
    res = eng.phase_a(rows)
    eng.phase_b(rows, res)
    eng.phase_c(rows, res)
    assert (debit.category, debit.subcategory, debit.category_source) == \
        ("Cash & Misc", "Venmo/Zelle Unknown", "fallback")
    assert (cashout.type, cashout.category, cashout.category_source) == ("income", "Income", "type_default")
    assert (known.category, known.category_source) == ("Dining", "keyword")   # a rule already named the payee


def test_finalize_venmo_keeps_a_keyword_rules_specific_cash_and_misc_subcategory():
    # a keyword rule that names a SPECIFIC Cash & Misc subcategory has said what the P2P payment was, so the
    # row keeps its label, its provenance and its review flag; the catch-all's Venmo/Zelle Unknown, a keyword
    # rule with no or a generic subcategory, the fallback and a bank label all still get the treatment
    def out(day, **stamp):     # above the $200 review threshold, so the treatment would set needs_review
        return make_txn(desc=f"VENMO PAYMENT {day}", cents=-50000, account="chk", date=f"2026-03-{day:02d}",
                        category="Cash & Misc", **stamp)
    legal = out(1, subcategory="Legal", category_source="keyword")
    wire = out(2, subcategory="Wire", category_source="keyword", needs_review=True)
    catch_all = out(3, subcategory="Venmo/Zelle Unknown", category_source="keyword", needs_review=True)
    generic = out(4, subcategory="Uncategorized", category_source="keyword")
    bare = out(5, category_source="keyword")
    fallback = out(6, subcategory="Uncategorized", category_source="fallback")
    bank = out(7, subcategory="Services", category_source="bank_label")
    learned = out(8, subcategory="Services", category_source="learned")
    vendor = out(9, subcategory="Legal", category_source="vendor_rule")
    rows = [legal, wire, catch_all, generic, bare, fallback, bank, learned, vendor]
    eng = _engine()
    res = eng.phase_a(rows)
    eng.phase_b(rows, res)
    eng.phase_c(rows, res)

    def stamp(t):
        return (t.category, t.subcategory, t.category_source, t.needs_review)
    assert stamp(legal) == ("Cash & Misc", "Legal", "keyword", False)      # flag untouched, not raised
    assert stamp(wire) == ("Cash & Misc", "Wire", "keyword", True)
    for t in (catch_all, generic, bare, fallback, bank):
        assert stamp(t) == ("Cash & Misc", "Venmo/Zelle Unknown", "fallback", True), t.date
    assert stamp(learned) == ("Cash & Misc", "Services", "learned", False)
    assert stamp(vendor) == ("Cash & Misc", "Legal", "vendor_rule", False)
    assert {t.type for t in rows} == {"purchase"}


def test_finalize_venmo_keeps_every_specific_cash_and_misc_subcategory_the_taxonomy_seeds():
    # not an allow-list: every seeded Cash & Misc subcategory but the two generic ones (none of which a P2P keyword
    # rule of the demo's names) stands on a keyword P2P outflow
    seeded = load("category_rules.yaml")["taxonomy"]["Cash & Misc"]
    specific = [s for s in seeded if s not in ("Uncategorized", "Venmo/Zelle Unknown")]
    assert set(specific) == {"Services", "Checks", "ATM", "Legal", "Wire"}
    rows = [make_txn(desc=f"VENMO PAYMENT {i}", cents=-50000, account="chk", date=f"2026-04-{i + 1:02d}",
                     category="Cash & Misc", subcategory=sub, category_source="keyword") for i, sub in enumerate(specific)]
    eng = _engine()
    res = eng.phase_a(rows)
    eng.phase_b(rows, res)
    eng.phase_c(rows, res)
    assert [(t.subcategory, t.category_source, t.needs_review) for t in rows] == [(s, "keyword", False) for s in specific]


def test_overrides_and_answers_stamp_their_source():
    t = make_txn(desc="CHECK 2318", cents=-1276400, account=hh.CHECKING, category="Cash & Misc",
                 subcategory="Checks", category_source="keyword")
    overrides.apply([t], [{"match": {"contains": "CHECK 2318"}, "set": {"category": "Housing"}}])
    assert (t.category, t.category_source) == ("Housing", "override")
    a = make_txn(desc="X", cents=-5000, category="Cash & Misc", subcategory="Uncategorized",
                 category_source="fallback")
    answers.apply([a], {"answers": [{"key": f"txn:{a.uid}", "scope": "txn", "txn_id": a.uid,
                                     "set": {"category": "Dining", "subcategory": "Restaurants"},
                                     "answered_at": "2026-07-16T10:00:00Z"}]})
    assert (a.category, a.category_source) == ("Dining", "answer")
    # a set that only retypes leaves the category's provenance alone
    r = make_txn(desc="WIRE", cents=-100000, category="Cash & Misc", category_source="keyword")
    overrides.apply_set(r, {"type": "transfer"}, "override")
    assert (r.type, r.is_internal, r.category_source) == ("transfer", True, "keyword")


def test_set_internal_resync_stamps_internal():
    t = make_txn(desc="VENMO PAYMENT", cents=-5000, type="transfer", category="Cash & Misc",
                 category_source="fallback")
    set_internal(t)
    assert (t.category, t.subcategory, t.category_source) == ("Internal", "transfer", "internal")


def test_export_row_carries_category_source():
    t = make_txn(desc="X", cents=-100, account="a", category="Dining", category_source="keyword")
    row = exports.export_transactions([t], {"a": "Joint"}, {"a": "A"})["transactions"][0]
    assert row["category_source"] == "keyword"


def test_sources_are_a_closed_set():
    assert set(CATEGORY_SOURCES) == {"internal", "type_default", "parser", "vendor_rule", "learned",
                                     "keyword", "bank_label", "fallback", "answer", "override", "split", "ai"}
    assert len(set(CATEGORY_SOURCES)) == len(CATEGORY_SOURCES)


# --------------------------------------------- Pipeline.enrich on a synthetic pipeline
# Everything above exercises one stage at a time. These run the REAL Pipeline.enrich over
# rows that end up internal by every route (phase A rule, phase B pairing, an override
# retype) and check what the stages leave behind in combination.

SYNTH_OVERRIDES = [
    # retypes between internal types: still Internal, its subcategory the new type, its provenance `internal`
    {"match": {"contains": "SYNTH CARD PAYMENT 0002"}, "set": {"type": "investment"}},
    # retypes a categorized purchase to internal: filed Internal/transfer, `internal`, as the categorizer files one
    {"match": {"contains": "SYNTH LUNCH RETYPED"}, "set": {"type": "transfer"}},
]


def _enriched(tmp_path, overrides_list):
    """name -> Txn after a full Pipeline.enrich of a synthetic pipeline (synthetic transfer
    rules and overrides, the demo config's category / vendor / income rules, no answers)."""
    cfg = demo_config()
    cfg.transfer_rules = {
        "rules": [{"name": "synth-card-payment", "pattern": "^SYNTH CARD PAYMENT",
                   "type": "cc-payment", "counter_hint": hh.CARD_ALEX}],
        "venmo_patterns": {"debit": r"VENMO\s+PAYMENT", "credit": r"VENMO\s+CASHOUT",
                           "zelle": "QUICKPAY|ZELLE"}}
    cfg.overrides = overrides_list
    p = Pipeline(isolate(cfg, tmp_path))                   # absent files = no answers, nothing learned
    window = ("2026-01-01", "2026-12-31")
    p.windows = {a: window for a in (hh.CHECKING, hh.SAVINGS, hh.CARD_ALEX)}
    rows = {
        # internal by a phase A rule
        "rule_a": make_txn(desc="SYNTH CARD PAYMENT 0001", cents=-50000, account=hh.CHECKING,
                           date="2026-03-02"),
        "rule_b": make_txn(desc="SYNTH CARD PAYMENT 0002", cents=-12000, account=hh.CHECKING,
                           date="2026-03-03"),
        # internal only once phase B pairs the two Venmo legs across bank accounts
        "pair_out": make_txn(desc="VENMO PAYMENT 7001", cents=-4000, account=hh.CHECKING,
                             date="2026-03-02"),
        "pair_in": make_txn(desc="VENMO CASHOUT 7001", cents=4000, account=hh.SAVINGS,
                            date="2026-03-03"),
        # a purchase that only an override retypes internal
        "retyped": make_txn(desc="SYNTH LUNCH RETYPED", cents=-2500, account=hh.CARD_ALEX,
                            date="2026-03-04"),
        # controls: an unpaired Venmo debit and the assorted non-internal paths
        "lone_venmo": make_txn(desc="VENMO PAYMENT 9999", cents=-3000, account=hh.CHECKING,
                               date="2026-03-05"),
        "purchase": make_txn(desc="SYNTH HARDWARE STORE", cents=-8000, account=hh.CARD_ALEX,
                             date="2026-03-06", bank_category="Shopping"),
        "mystery": make_txn(desc="MYSTERY MERCHANT XYZ", cents=-9000, account=hh.CARD_ALEX,
                            date="2026-03-07"),
        "fee": make_txn(desc="SYNTH LATE FEE", cents=-3500, account=hh.CARD_ALEX,
                        date="2026-03-08", type="fee"),
        "dividend": make_txn(desc="SYNTH DIVIDEND", cents=500, account=hh.CHECKING, date="2026-03-09",
                             type="dividend", category="Income", subcategory="Dividends"),
        "credit": make_txn(desc="RANDOM BIG CREDIT", cents=90000, account=hh.CHECKING,
                           date="2026-03-10"),
        "refund": make_txn(desc="SYNTH SMALL REFUND", cents=900, account=hh.CHECKING,
                           date="2026-03-11"),
    }
    p.txns = list(rows.values())
    # scoped by its rows' account: an accounts: rule's unpaired legs stay internal (a kinds: rule's are released)
    cfg.transfer_rules["rules"][0]["accounts"] = [rows["rule_a"].account_id]
    p.enrich()
    return rows


def test_internal_category_always_carries_internal_provenance(tmp_path):
    rows = _enriched(tmp_path, SYNTH_OVERRIDES)
    # the invariant, for every row: a row the pipeline calls Internal says why
    for name, t in rows.items():
        if t.category == "Internal":
            assert t.category_source == "internal", name
    # and every route into "internal" really ends there. Phase B only types the pair
    # AFTER the Categorizer has labelled it, so this is the post-matching resync's job:
    # without it the pair keeps its old, non-Internal category with a stale provenance
    for name in ("rule_a", "rule_b", "pair_out", "pair_in"):
        t = rows[name]
        assert t.type in INTERNAL_TYPES and t.is_internal, name
        assert (t.category, t.category_source) == ("Internal", "internal"), name
    assert rows["pair_out"].transfer_group_id == rows["pair_in"].transfer_group_id is not None


def test_an_override_retype_to_an_internal_type_files_the_row_internal_with_internal_provenance(tmp_path):
    """An override that sets an internal type and names no category leaves type and category agreeing: the row is
    Internal/<its new type>, stamped `internal` (the type decides it), never the category it had before"""
    plain = _enriched(tmp_path / "plain", [])
    retyped = _enriched(tmp_path / "retyped", SYNTH_OVERRIDES)
    for name, new_type in (("rule_b", "investment"), ("retyped", "transfer")):
        before, after = plain[name], retyped[name]
        assert after.type == new_type and after.is_internal, name            # the retype landed ...
        assert after.type != before.type, name
        assert (after.category, after.subcategory, after.category_source) == ("Internal", new_type, "internal"), name
        assert after.family_fact                                             # the override did touch it
    assert plain["retyped"].category != "Internal"                           # it was filed elsewhere before


def test_category_source_is_always_a_known_value_after_enrich(tmp_path):
    for overrides_list in ([], SYNTH_OVERRIDES):
        rows = _enriched(tmp_path / f"o{len(overrides_list)}", overrides_list)
        for name, t in rows.items():
            assert t.category_source is not None, name
            assert t.category_source in CATEGORY_SOURCES, (name, t.category_source)


def test_apply_set_refuses_a_source_outside_the_closed_set():
    for bad in ("overide", "learnt", "", None, "Override"):
        t = make_txn(desc="X", cents=-100, category="Dining", subcategory="Restaurants",
                     category_source="keyword")
        with pytest.raises(ValueError, match="category_source"):
            overrides.apply_set(t, {"category": "Housing"}, bad)
        # refused before anything was applied, so the row is exactly as it was
        assert (t.category, t.subcategory, t.category_source, t.family_fact) == \
            ("Dining", "Restaurants", "keyword", False)
    # a retype-only set stamps nothing, but its source is validated all the same
    t = make_txn(desc="X", cents=-100)
    with pytest.raises(ValueError, match="category_source"):
        overrides.apply_set(t, {"type": "transfer"}, "bogus")
    assert t.type == "purchase" and not t.is_internal
    # every source a caller actually passes is in the set
    for ok in ("override", "answer"):
        overrides.apply_set(t, {"category": "Housing"}, ok)
        assert t.category_source == ok and ok in CATEGORY_SOURCES


def test_categorizer_learned_slot_sits_between_vendor_rule_and_keyword_and_sets_only_category():
    learned = {"MAPLE STREET BAKERY": {"vendor": "Maple Street Bakery", "category": "Dining", "subcategory": "Coffee"},
               "uber": {"vendor": "Uber", "category": "Dining", "subcategory": None},
               paths.DAYCARE_EXPECT.vendor.casefold(): {"vendor": paths.DAYCARE_EXPECT.vendor, "category": "Dining",
                                                        "subcategory": None}}
    # the demo's daycare keyword rule, given a tag: the rule a learned category beats, whose tag must still land
    category_rules = load("category_rules.yaml")
    daycare_rule = next(r for r in category_rules["keyword_rules"] if r.get("pattern") == paths.DAYCARE_RULE["pattern"])
    daycare_rule["tags"] = ["kids"]
    vendor_rules = daycare_vendor_rules()
    n = VendorNormalizer(vendor_rules)
    cat = Categorizer(category_rules, load("bank_category_map.yaml"), vendor_rules,
                      load("assumptions.yaml"), {}, learned=learned)
    rows = {
        "bakery": make_txn(desc="MAPLE STREET BAKERY #1234", cents=-90000, account=hh.CARD_ALEX, tags=["x"]),
        "parser": make_txn(desc="MAPLE STREET BAKERY #1234", cents=-1500, account=hh.CARD_ALEX,
                           category="Kids", subcategory="Activities"),
        "uber": make_txn(desc="UBER *TRIP 1234", cents=-1500, account=hh.CARD_ALEX),
        "daycare": make_txn(desc=paths.DAYCARE, cents=-150000, account=hh.CHECKING,
                            bank_category="Bills & Utilities"),
    }
    for t in rows.values():
        if not t.vendor:
            t.vendor, _ = n.normalize(t.description_raw)
    cat.apply(list(rows.values()), n.clean)
    t = rows["bakery"]
    assert t.vendor == "Maple Street Bakery"
    assert (t.category, t.subcategory, t.category_source) == ("Dining", "Coffee", "learned")   # keys casefolded
    assert t.needs_review is False and t.tags == ["x"]            # returns before the fallback path; tags untouched
    assert (rows["parser"].category, rows["parser"].category_source) == ("Kids", "parser")        # step 2 wins
    assert (rows["uber"].category, rows["uber"].category_source) == ("Transport", "vendor_rule")  # step 3 wins
    assert (rows["daycare"].category, rows["daycare"].category_source) == ("Dining", "learned")   # beats keyword
    assert rows["daycare"].tags == ["kids"]                       # ...but the keyword rule's tag still lands


# a learned rule replaces the CATEGORY, never the rulebook's tags: the Kids page and the kids
# insight filter on `kids`. Synthetic rulebooks only.
TAG_RULES = {"keyword_rules": [
    {"pattern": "SYNTH DAYCARE", "category": "Kids", "subcategory": "Childcare", "tags": ["kids"]},
    {"pattern": "SYNTH BOOKSHOP", "category": "Shopping"},                       # a hit WITHOUT tags
]}
TAG_MAP = {"Education": {"category": "Kids", "subcategory": "School", "tags": ["kids"]}}


def learned_tags(desc, learned_cat, bank_category=None, account_tags=None, tags=None):
    vr = {"patterns": []}
    n = VendorNormalizer(vr)
    t = make_txn(desc=desc, cents=-30000, account=hh.CHECKING, bank_category=bank_category, tags=tags or [])
    t.vendor = n.normalize(t.description_raw)[0]
    learned = {t.vendor.casefold(): {"category": learned_cat[0], "subcategory": learned_cat[1]}}
    Categorizer(TAG_RULES, TAG_MAP, vr, {}, account_tags or {}, learned=learned).apply([t], n.clean)
    return t


def test_learned_rule_that_refines_a_keyword_tagged_vendor_keeps_the_keyword_tag():
    t = learned_tags("SYNTH DAYCARE 9999", ("Kids", "Activities"))
    assert (t.category, t.subcategory, t.category_source) == ("Kids", "Activities", "learned")
    assert t.tags == ["kids"]                                    # the reviewer's repro: it used to be []
    assert t.needs_review is False
    # a learned CATEGORY change keeps the tag too - tags describe the rulebook's knowledge of the vendor
    assert learned_tags("SYNTH DAYCARE 9999", ("Shopping", None)).tags == ["kids"]
    # the union with the row's own tags and the account's tags is unchanged
    t = learned_tags("SYNTH DAYCARE 9999", ("Kids", "Activities"), account_tags={hh.CHECKING: ["house"]}, tags=["x"])
    assert t.tags == ["house", "kids", "x"]


def test_learned_rule_keeps_the_bank_label_tags_when_no_keyword_rule_hits():
    t = learned_tags("SYNTH LEARNING CENTER 1234", ("Kids", "Activities"), bank_category="Education")
    assert (t.category, t.category_source) == ("Kids", "learned") and t.tags == ["kids"]
    # no label, or a label with no tags: nothing is added
    assert learned_tags("SYNTH LEARNING CENTER 1234", ("Kids", "Activities")).tags == []
    assert learned_tags("SYNTH LEARNING CENTER 1234", ("Kids", "Activities"), bank_category="Other").tags == []


def test_learned_rule_follows_the_non_learned_tag_order_keyword_hit_shadows_the_bank_label():
    # a keyword hit with no tags still wins over the bank label (step 6 runs only when no keyword
    # rule hit), so the label's tag must not leak in just because the category is learned
    t = learned_tags("SYNTH BOOKSHOP 4411", ("Kids", "School"), bank_category="Education")
    assert t.category_source == "learned" and t.tags == []

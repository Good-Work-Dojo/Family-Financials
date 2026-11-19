# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""etl/enrich/rules.py: one rule shape for keyword rules, vendor patterns (data only)
and preview candidates. Synthetic descriptors only — the equivalence corpus is
built from the demo config's regexes' literal alternatives, never from data."""
from __future__ import annotations

import functools
import re

import pytest
from conftest import load_demo, make_txn

from etl.enrich import rules as R
from etl.enrich.vendors import VendorNormalizer
from tools.demo import household as hh

load = load_demo
# two joint accounts and one an adult holds alone, with the demo household's owners
OWNERS = {a: hh.ACCOUNTS[a].owner for a in (hh.CHECKING, hh.SAVINGS, hh.CARD_ALEX)}
owner_of = OWNERS.get


def tx(desc="SOME SHOP", cents=-1000, **over):
    return make_txn(desc=desc, cents=cents, account=over.pop("account", hh.SAVINGS), **over)


def one(entry, source="keyword_rules"):
    return R.compile_rules([entry], source=source)[0]


def hit(rule, t, cleaner=lambda s: s):
    return R.rule_matches(rule, t, cleaner=cleaner, owner_of=owner_of)


# ------------------------------------------------------------------ shapes

def test_legacy_entry_compiles_to_one_description_match_in_default_stage():
    r = one({"pattern": r"^CHECK \d+$", "category": "Cash & Misc", "subcategory": "Checks",
             "needs_review": True, "tags": ["x"]})
    assert (r.name, r.stage, r.conditions_op, r.source, r.index) == ("keyword_rules:0", "default", "all", "keyword_rules", 0)
    assert len(r.conditions) == 1
    c = r.conditions[0]
    assert (c.field, c.op) == ("description", "matches") and isinstance(c.value, re.Pattern)
    assert c.value.flags & re.IGNORECASE
    assert r.set == {"category": "Cash & Misc", "subcategory": "Checks", "needs_review": True, "tags": ["x"]}


def test_names_stages_and_sources():
    rules = R.compile_rules([
        {"pattern": "A", "category": "Dining"},
        {"name": "coffee", "stage": "pre", "pattern": "B", "category": "Dining", "subcategory": "Coffee"},
        {"stage": "post", "all": [{"field": "vendor", "op": "is", "value": "X"}], "set": {"category": "Pets"}},
    ], source="preview")
    assert [r.name for r in rules] == ["preview:0", "coffee", "preview:2"]
    assert [r.stage for r in rules] == ["default", "pre", "post"]
    assert all(r.source == "preview" for r in rules) and [r.index for r in rules] == [0, 1, 2]
    assert R.compile_rules([{"pattern": "A", "category": "Dining"}], source="learned")[0].stage == "default"


def test_vendor_pattern_compiles_as_data_with_vendor_in_set():
    r = one({"pattern": "UBER\\b", "vendor": "Uber", "category": "Transport", "subcategory": "Rideshare",
             "learn": False}, source="vendor_rules")
    assert r.set == {"vendor": "Uber", "category": "Transport", "subcategory": "Rideshare"}   # learn is not a set key
    assert one({"pattern": "COSTCO", "vendor": "Costco"}, source="vendor_rules").set == {"vendor": "Costco"}


def test_split_is_a_legacy_key_on_vendor_patterns_only():
    legs = [{"pct": 70, "category": "Groceries"}, {"remainder": True, "category": "Shopping"}]
    r = one({"pattern": "ZZZ", "vendor": "Zzz", "split": legs}, source="vendor_rules")
    assert r.set == {"vendor": "Zzz"}                       # the legs are not a `set`: splits.py reads them
    with pytest.raises(ValueError, match="split: is only valid on a vendor_rules pattern"):
        one({"pattern": "ZZZ", "category": "Dining", "split": legs})


# ------------------------------------------------------------ description

def test_description_matches_uses_cleaned_then_raw_like_the_old_scan():
    r = one({"pattern": r"^CHECK \d+$", "category": "Cash & Misc"})
    clean = VendorNormalizer(load("vendor_rules.yaml")).clean
    t = tx("CHECK 20451", -150000)
    assert clean("CHECK 20451") == "CHECK"           # the cleanup ate the digits ...
    assert hit(r, t, clean)                          # ... so only the raw fallback can match
    assert not hit(one({"pattern": r"^CHECK$", "category": "Cash & Misc"}), tx("CHECK 20451"))   # identity cleaner: raw only
    assert hit(one({"pattern": r"^CHECK$", "category": "Cash & Misc"}), tx("CHECK 20451"), clean)  # cleaned matches


def test_description_contains_is_case_insensitive_on_cleaned_or_raw():
    r = one({"all": [{"field": "description", "op": "contains", "value": "taco tuesday"}], "set": {"category": "Dining"}})
    assert hit(r, tx("TACO TUESDAY CANTINA"))
    assert not hit(r, tx("TACO WEDNESDAY"))


# ----------------------------------------------------------------- vendor

@pytest.mark.parametrize("op,value,yes,no", [
    ("is", "amazon", "Amazon", "Amazon Fresh"),
    ("isNot", "Amazon", "Costco", "amazon"),
    ("oneOf", ["Costco", "target"], "Target", "Walmart"),
    ("contains", "mart", "Walmart", "Target"),
    ("matches", r"^wal-?mart$", "Wal-Mart", "Walmart Pharmacy"),
])
def test_vendor_ops(op, value, yes, no):
    r = one({"all": [{"field": "vendor", "op": op, "value": value}], "set": {"category": "Shopping"}})
    assert hit(r, tx(vendor=yes)) and not hit(r, tx(vendor=no))


def test_vendor_none_matches_nothing_but_isNot():
    r = one({"all": [{"field": "vendor", "op": "contains", "value": "a"}], "set": {"category": "Pets"}})
    assert not hit(r, tx(vendor=None))
    assert not hit(one({"all": [{"field": "vendor", "op": "matches", "value": "."}], "set": {"category": "Pets"}}), tx(vendor=None))
    assert hit(one({"all": [{"field": "vendor", "op": "isNot", "value": "X"}], "set": {"category": "Pets"}}), tx(vendor=None))


# ----------------------------------------------------------------- amount

def test_amount_values_are_dollars_in_yaml_and_signed_cents_in_the_rule():
    r = one({"all": [{"field": "amount", "op": "lt", "value": -500}], "set": {"category": "Travel"}})
    assert r.conditions[0].value == -50000
    assert hit(r, tx(cents=-60000)) and not hit(r, tx(cents=-40000)) and not hit(r, tx(cents=60000))
    b = one({"all": [{"field": "amount", "op": "between", "value": [-100, -50.5]}], "set": {"category": "Travel"}})
    assert b.conditions[0].value == (-10000, -5050)
    assert hit(b, tx(cents=-7500)) and hit(b, tx(cents=-10000)) and hit(b, tx(cents=-5050))
    assert not hit(b, tx(cents=-5049)) and not hit(b, tx(cents=7500))
    assert hit(one({"all": [{"field": "amount", "op": "gte", "value": 10}], "set": {"category": "Income"}}), tx(cents=1000))
    assert not hit(one({"all": [{"field": "amount", "op": "gt", "value": 10}], "set": {"category": "Income"}}), tx(cents=1000))
    assert hit(one({"all": [{"field": "amount", "op": "lte", "value": -0.01}], "set": {"category": "Pets"}}), tx(cents=-1))


def test_inflow_and_outflow_take_no_value():
    inflow = one({"all": [{"field": "amount", "op": "inflow"}], "set": {"category": "Income"}})
    outflow = one({"all": [{"field": "amount", "op": "outflow"}], "set": {"category": "Pets"}})
    assert hit(inflow, tx(cents=1)) and not hit(inflow, tx(cents=-1)) and not hit(inflow, tx(cents=0))
    assert hit(outflow, tx(cents=-1)) and not hit(outflow, tx(cents=1)) and not hit(outflow, tx(cents=0))


# -------------------------------------------- account / owner / type / bank_category

def test_account_owner_type_and_bank_category_ops():
    acct = one({"all": [{"field": "account", "op": "oneOf", "value": [hh.CHECKING, hh.CARD_ALEX]}], "set": {"category": "Pets"}})
    assert hit(acct, tx(account=hh.CARD_ALEX)) and not hit(acct, tx(account=hh.SAVINGS))
    own = one({"all": [{"field": "owner", "op": "is", "value": hh.ALEX.lower()}], "set": {"category": "Pets"}})
    assert hit(own, tx(account=hh.CARD_ALEX)) and not hit(own, tx(account=hh.CHECKING))
    assert not hit(own, tx(account="unknown_account"))               # owner_of -> None: no match, no crash
    typ = one({"all": [{"field": "type", "op": "isNot", "value": "purchase"}], "set": {"category": "Pets"}})
    assert hit(typ, tx(type="fee")) and not hit(typ, tx())
    bank = one({"all": [{"field": "bank_category", "op": "matches", "value": "^food"}], "set": {"category": "Dining"}})
    assert hit(bank, tx(bank_category="Food & Drink")) and not hit(bank, tx(bank_category=None))
    bank2 = one({"any": [{"field": "bank_category", "op": "oneOf", "value": ["Gas", "Automotive"]}], "set": {"category": "Transport"}})
    assert hit(bank2, tx(bank_category="gas")) and not hit(bank2, tx(bank_category="Travel"))


@pytest.mark.parametrize("op,value", [("is", hh.JOINT), ("oneOf", [hh.JOINT, hh.ALEX]), ("isNot", hh.ALEX)])
def test_every_owner_op_fails_on_an_unresolvable_owner(op, value):
    """isNot included: `owner isNot Alex` must not fire on a row whose owner is unknown."""
    r = one({"all": [{"field": "owner", "op": op, "value": value}], "set": {"category": "Pets"}})
    assert hit(r, tx(account=hh.CHECKING))                        # control: owner Joint satisfies all three
    assert not hit(r, tx(account="unknown_account"))              # OWNERS.get -> None
    for unresolved in (lambda account_id: "", lambda account_id: None):
        assert not R.rule_matches(r, tx(account=hh.CHECKING), cleaner=lambda s: s, owner_of=unresolved)


# -------------------------------------------------------------- all / any

def test_all_requires_every_condition_and_any_requires_one():
    conds = [{"field": "vendor", "op": "is", "value": "Costco"}, {"field": "amount", "op": "lt", "value": -200}]
    all_ = one({"all": conds, "set": {"category": "Groceries"}})
    any_ = one({"any": conds, "set": {"category": "Groceries"}})
    big_costco, small_costco, big_other = tx(vendor="Costco", cents=-30000), tx(vendor="Costco", cents=-3000), tx(vendor="X", cents=-30000)
    assert hit(all_, big_costco) and not hit(all_, small_costco) and not hit(all_, big_other)
    assert hit(any_, big_costco) and hit(any_, small_costco) and hit(any_, big_other) and not hit(any_, tx(vendor="X", cents=-1))


def test_owner_of_is_only_consulted_for_owner_conditions():
    def boom(account_id):                                  # e.g. a dict's __getitem__ on an unknown account
        raise KeyError(account_id)

    def go(entry, t):
        return R.rule_matches(one(entry), t, cleaner=lambda s: s, owner_of=boom)

    def cond(field, op, value):
        return {"field": field, "op": op, "value": value}

    cat = {"category": "Pets"}
    assert go({"all": [cond("vendor", "is", "Costco")], "set": cat}, tx(vendor="Costco"))
    assert go({"all": [cond("account", "is", hh.CARD_ALEX)], "set": cat}, tx(account=hh.CARD_ALEX))
    assert go({"all": [cond("type", "is", "fee")], "set": cat}, tx(type="fee"))
    assert go({"all": [cond("bank_category", "contains", "gas")], "set": cat}, tx(bank_category="Gas"))
    assert go({"all": [{"field": "amount", "op": "outflow"}], "set": cat}, tx(cents=-5))
    assert go({"pattern": "SHOP", "category": "Pets"}, tx("SOME SHOP"))
    # the lookup also stays lazy across short-circuits
    assert go({"any": [cond("vendor", "is", "Costco"), cond("owner", "is", "Joint")], "set": cat}, tx(vendor="Costco"))
    assert not go({"all": [cond("vendor", "is", "Costco"), cond("owner", "is", "Joint")], "set": cat}, tx(vendor="X"))
    with pytest.raises(KeyError):                          # ... but an owner condition does reach it
        go({"all": [cond("owner", "is", "Joint")], "set": cat}, tx())


# ------------------------------------------------------------- validation

@pytest.mark.parametrize("entry,msg", [
    ({"pattern": "A", "category": "Dining", "stage": "first"}, "unknown stage 'first'"),
    ({"pattern": "A", "category": "Dining", "subcatgory": "X"}, "unexpected keys"),
    ({"pattern": "A", "category": "Dining", "all": []}, "unexpected keys"),
    ({"pattern": "(", "category": "Dining"}, "bad regex"),
    ({"pattern": "A"}, "set needs category"),
    ({"pattern": "A", "category": "Dining", "vendor": "X"}, "vendor"),
    ({"all": [{"field": "descr", "op": "contains", "value": "x"}], "set": {"category": "Dining"}}, "unknown field 'descr'"),
    ({"all": [{"field": "description", "op": "equals", "value": "x"}], "set": {"category": "Dining"}}, "unknown op 'equals'"),
    ({"all": [{"field": "description", "op": "gt", "value": 1}], "set": {"category": "Dining"}}, "not valid for field 'description'"),
    ({"all": [{"field": "amount", "op": "lt", "value": "500"}], "set": {"category": "Dining"}}, "number"),
    ({"all": [{"field": "amount", "op": "inflow", "value": 1}], "set": {"category": "Dining"}}, "takes no value"),
    ({"all": [{"field": "amount", "op": "between", "value": [1]}], "set": {"category": "Dining"}}, "two numbers"),
    ({"all": [{"field": "vendor", "op": "oneOf", "value": "Costco"}], "set": {"category": "Dining"}}, "list of strings"),
    ({"all": [{"field": "vendor", "op": "is"}], "set": {"category": "Dining"}}, "needs a value"),
    ({"all": [], "set": {"category": "Dining"}}, "non-empty list"),
    ({"all": [{"field": "vendor", "op": "is", "value": "x"}], "any": [], "set": {"category": "Dining"}}, "exactly one of all:/any:"),
    ({"set": {"category": "Dining"}}, "exactly one of all:/any:"),
    ({"all": [{"field": "vendor", "op": "is", "value": "x"}]}, "set: must be a mapping"),
    ({"all": [{"field": "vendor", "op": "is", "value": "x"}], "set": {"category": "Dining", "earner": "J"}}, "set keys not allowed"),
    ({"all": [{"field": "vendor", "op": "is", "value": "x"}], "set": {"category": "Dining", "needs_review": "yes"}}, "needs_review must be a boolean"),
    ({"all": [{"field": "vendor", "op": "is", "value": "x"}], "set": {"category": "Dining", "tags": "home"}}, "tags must be a list"),
    # non-finite / overflowing amounts fail with the index, never a bare ValueError/OverflowError
    ({"all": [{"field": "amount", "op": "lt", "value": float("nan")}], "set": {"category": "Dining"}}, "finite number"),
    ({"all": [{"field": "amount", "op": "gte", "value": float("inf")}], "set": {"category": "Dining"}}, "finite number"),
    ({"all": [{"field": "amount", "op": "lt", "value": -float("inf")}], "set": {"category": "Dining"}}, "finite number"),
    ({"all": [{"field": "amount", "op": "gt", "value": 1e308}], "set": {"category": "Dining"}}, "out of range"),
    ({"all": [{"field": "amount", "op": "between", "value": [float("nan"), 5]}], "set": {"category": "Dining"}}, "two numbers"),
    ({"all": [{"field": "amount", "op": "between", "value": [-1e308, 5]}], "set": {"category": "Dining"}}, "out of range"),
    # blank text values would be silent catch-alls (is ""/isNot "" stay legal: the "no vendor" idiom)
    ({"pattern": "", "category": "Dining"}, "must not be empty or blank"),
    ({"pattern": "   ", "category": "Dining"}, "must not be empty or blank"),
    ({"all": [{"field": "description", "op": "contains", "value": ""}], "set": {"category": "Dining"}}, "must not be empty or blank"),
    ({"all": [{"field": "vendor", "op": "contains", "value": "  "}], "set": {"category": "Dining"}}, "must not be empty or blank"),
    ({"all": [{"field": "vendor", "op": "matches", "value": ""}], "set": {"category": "Dining"}}, "must not be empty or blank"),
    ({"all": [{"field": "bank_category", "op": "matches", "value": " "}], "set": {"category": "Dining"}}, "must not be empty or blank"),
    # a type value outside etl.models.TXN_TYPES would silently never match
    ({"all": [{"field": "type", "op": "is", "value": "purchse"}], "set": {"category": "Dining"}},
     "type value 'purchse' is not a transaction type"),
    ({"all": [{"field": "type", "op": "isNot", "value": "transfers"}], "set": {"category": "Dining"}},
     "type value 'transfers' is not a transaction type"),
    ({"any": [{"field": "type", "op": "oneOf", "value": ["fee", "dividends"]}], "set": {"category": "Dining"}},
     "type value 'dividends' is not a transaction type"),
    ({"all": [{"field": "type", "op": "is", "value": ""}], "set": {"category": "Dining"}},
     "type value '' is not a transaction type"),
    ({"all": [{"field": "vendor", "op": "oneOf", "value": [""]}], "set": {"category": "Dining"}}, "empty or blank items"),
    ({"all": [{"field": "account", "op": "oneOf", "value": [hh.CARD_ALEX, " "]}], "set": {"category": "Dining"}}, "empty or blank items"),
    # a typo inside a condition must not be silently dropped
    ({"all": [{"field": "vendor", "op": "is", "value": "x", "not": True}], "set": {"category": "Dining"}}, "unexpected condition keys ['not']"),
    ({"any": [{"field": "vendor", "op": "is", "value": "x"}, {"field": "amount", "op": "inflow", "valeu": 1}],
      "set": {"category": "Dining"}}, "unexpected condition keys ['valeu']"),
    ("not a mapping", "entry must be a mapping"),
])
def test_compile_rejects_malformed_entries_with_the_index(entry, msg):
    with pytest.raises(ValueError, match=r"keyword_rules\[1\]: ") as e:
        R.compile_rules([{"pattern": "OK", "category": "Dining"}, entry], source="keyword_rules")
    assert msg in str(e.value), str(e.value)


def test_vendor_source_requires_vendor_and_learn_must_be_a_bool():
    with pytest.raises(ValueError, match=r"vendor_rules\[0\]: set needs vendor"):
        R.compile_rules([{"pattern": "A", "category": "Dining"}], source="vendor_rules")
    with pytest.raises(ValueError, match=r"vendor_rules\[0\]: learn must be a boolean"):
        R.compile_rules([{"pattern": "A", "vendor": "A", "learn": "no"}], source="vendor_rules")


def test_type_conditions_accept_every_transaction_type_casefolded():
    from etl.models import TXN_TYPES
    for ty in TXN_TYPES:
        r = one({"all": [{"field": "type", "op": "is", "value": ty}], "set": {"category": "Pets"}})
        assert hit(r, tx(type=ty))
    r = one({"all": [{"field": "type", "op": "oneOf", "value": ["Purchase", "FEE"]}], "set": {"category": "Pets"}})
    assert hit(r, tx(type="purchase")) and hit(r, tx(type="fee")) and not hit(r, tx(type="income"))


def test_vendor_patterns_take_no_stage_or_name():
    """VendorNormalizer matches patterns in file order and ignores both keys, so accepting them
    on a vendor pattern would look meaningful and do nothing. Keyword rules keep both."""
    ok = {"pattern": "OK", "vendor": "Ok"}
    for extra, shown in (({"stage": "pre"}, "stage:"), ({"name": "mine"}, "name:"),
                         ({"stage": "default", "name": "mine"}, "stage: or name:")):
        with pytest.raises(ValueError, match=r"vendor_rules\[1\]: vendor patterns take no " + shown):
            R.compile_rules([ok, {"pattern": "A", "vendor": "A", **extra}], source="vendor_rules")
    R.compile_rules([ok], source="vendor_rules")
    kw = R.compile_rules([{"name": "n", "stage": "pre", "pattern": "A", "category": "Dining"}], source="keyword_rules")
    assert (kw[0].name, kw[0].stage) == ("n", "pre")
    # every vendor pattern of the demo config still compiles (none carries either key)
    patterns = load("vendor_rules.yaml")["patterns"]
    assert len(R.compile_rules(patterns, source="vendor_rules")) == len(patterns) > 0


def test_validate_references_checks_owner_and_account_values():
    owners, accounts = (hh.ALEX, hh.JOINT), {hh.CHECKING: {}, hh.CARD_ALEX: {}}

    def check(*conds, name=None):
        entry = {"all": list(conds), "set": {"category": "Pets"}, **({"name": name} if name else {})}
        R.validate_references(R.compile_rules([{"pattern": "OK", "category": "Dining"}, entry], source="keyword_rules"),
                              owners=owners, accounts=accounts)

    ok_owner = {"field": "owner", "op": "oneOf", "value": [hh.ALEX.lower(), hh.JOINT]}   # casefolded, like the ops
    ok_account = {"field": "account", "op": "is", "value": hh.CARD_ALEX.upper()}
    check(ok_owner, ok_account)                                                       # no raise
    check({"field": "vendor", "op": "is", "value": "Whatever"})                       # other fields are not checked
    with pytest.raises(ValueError, match=r"keyword_rules\[1\]: unknown owner 'Alexx' \(one of Alex, Joint\)"):
        check({"field": "owner", "op": "is", "value": "Alexx"})
    with pytest.raises(ValueError, match=r"keyword_rules\[1\]: unknown owner 'Casey'"):
        check(ok_account, {"field": "owner", "op": "oneOf", "value": [hh.JOINT, "Casey"]})
    listed = ", ".join(sorted(accounts))
    with pytest.raises(ValueError, match=rf"keyword_rules\[1\]: unknown account 'demo_card_9999' \(one of {listed}\)"):
        check({"field": "account", "op": "isNot", "value": "demo_card_9999"}, name="named")
    # vendor patterns only have a description condition: nothing to check
    R.validate_references(R.compile_rules([{"pattern": "A", "vendor": "A"}], source="vendor_rules"),
                          owners=owners, accounts=accounts)


def test_blank_is_and_isNot_stay_legal_for_the_no_vendor_idiom():
    none_vendor = one({"all": [{"field": "vendor", "op": "is", "value": ""}], "set": {"category": "Pets"}})
    has_vendor = one({"all": [{"field": "vendor", "op": "isNot", "value": ""}], "set": {"category": "Pets"}})
    assert hit(none_vendor, tx(vendor=None)) and not hit(none_vendor, tx(vendor="X"))
    assert hit(has_vendor, tx(vendor="X")) and not hit(has_vendor, tx(vendor=None))


def test_duplicate_rule_names_are_rejected_within_one_compile():
    dup = [{"name": "dup", "pattern": "A", "category": "Dining"}, {"name": "dup", "pattern": "B", "category": "Dining"}]
    with pytest.raises(ValueError, match=r"keyword_rules\[1\]: duplicate rule name 'dup' \(also entry \[0\]\)"):
        R.compile_rules(dup, source="keyword_rules")
    forged = [{"name": "keyword_rules:1", "pattern": "A", "category": "Dining"}, {"pattern": "B", "category": "Dining"}]
    with pytest.raises(ValueError, match=r"keyword_rules\[1\]: duplicate rule name 'keyword_rules:1'"):
        R.compile_rules(forged, source="keyword_rules")                      # an auto name can be forged
    R.compile_rules(dup[:1], source="keyword_rules")                         # across compiles: not this function's job
    R.compile_rules(dup[:1], source="learned")
    auto = R.compile_rules([{"pattern": "A", "category": "Dining"}] * 3, source="keyword_rules")
    assert [r.name for r in auto] == ["keyword_rules:0", "keyword_rules:1", "keyword_rules:2"]


def test_validate_taxonomy_names_the_rule():
    taxonomy = {"Dining": ["Coffee"], "Internal": []}
    rules = R.compile_rules([{"name": "a", "pattern": "A", "category": "Dining", "subcategory": "Coffee"},
                             {"pattern": "B", "category": "Internal", "subcategory": "anything"}],   # Internal subs are dynamic
                            source="keyword_rules")
    R.validate_taxonomy(rules, taxonomy)                                   # no raise
    R.validate_taxonomy(R.compile_rules([{"pattern": "C", "vendor": "C"}], source="vendor_rules"), taxonomy)   # no category: fine
    with pytest.raises(ValueError, match=r"^keyword_rules\[0\]: category 'Dinning' not in taxonomy"):    # nameless: the position says it
        R.validate_taxonomy(R.compile_rules([{"pattern": "A", "category": "Dinning"}], source="keyword_rules"), taxonomy)
    with pytest.raises(ValueError, match=r"keyword_rules\[0\] \(late\): subcategory 'Bars' not seeded under 'Dining'"):
        R.validate_taxonomy(R.compile_rules([{"name": "late", "pattern": "A", "category": "Dining", "subcategory": "Bars"}],
                                            source="keyword_rules"), taxonomy)


# ------------------------------------------------- legacy equivalence

_LITERAL = re.compile(r"^[A-Z0-9 &'/-]+$")


def literal_alternatives(pattern: str) -> list[str]:
    """Alternatives of a regex that are plain text once anchors/word-boundaries are dropped."""
    out = []
    for alt in pattern.split("|"):
        alt = alt.replace("^", "").replace("$", "").replace(r"\b", "").strip()
        if alt and _LITERAL.match(alt):
            out.append(alt)
    return out


def legacy_pairs(file: str, key: str, source: str) -> list[tuple[dict, R.Rule]]:
    """(entry, compiled Rule) for every `pattern:`-shaped entry of a rulebook list of the demo config. The
    legacy-equivalence claim is about those entries only: a structured entry (no `pattern`, an
    all:/any: body) may join a rulebook at any time and has no old scan to match."""
    entries = load(file)[key]
    rules = R.compile_rules(entries, source=source)
    return [(e, r) for e, r in zip(entries, rules, strict=True) if "pattern" in e]


def demo_legacy_pairs() -> list[tuple[dict, R.Rule]]:
    return (legacy_pairs("category_rules.yaml", "keyword_rules", "keyword_rules")
            + legacy_pairs("vendor_rules.yaml", "patterns", "vendor_rules"))


def corpus() -> list[str]:
    """Several hundred synthetic descriptors: one literal per demo rule x three bank-noise dressings,
    plus the hand cases where the cleaned and raw targets disagree."""
    lits: list[str] = []
    for e, _ in demo_legacy_pairs():
        lits += literal_alternatives(e["pattern"])[:1]
    descs = {"CHECK 20451", "WT 12345 BANK OF SOMEWHERE", "UNITED 01623456", "TST* ACME COFFEE",
             "ACME STORE WEB ID: NETFLIX", "WITHDRAWAL 05/20", "Payment Thank You-Mobile"}
    for lit in lits:
        descs.add(lit)
        descs.add(f"SQ *{lit} HARBOR CITY, ST 1234")
        descs.add(f"{lit.lower()} recurring 05/21")
    return sorted(descs)


def legacy(pattern: str, raw: str, clean) -> bool:
    pat = re.compile(pattern, re.IGNORECASE)
    return bool(pat.search(clean(raw).upper()) or pat.search(raw.upper()))


def test_legacy_entries_compile_to_one_description_match_rule():
    """Every `pattern:` entry of the demo config is one description/matches Rule in the stage it names
    (`default` when it names none: a later `stage: pre` on a legacy entry is legal)."""
    for file, key, source in (("category_rules.yaml", "keyword_rules", "keyword_rules"),
                              ("vendor_rules.yaml", "patterns", "vendor_rules")):
        assert len(R.compile_rules(load(file)[key], source=source)) == len(load(file)[key])
        pairs = legacy_pairs(file, key, source)
        assert pairs, f"no pattern: entries read from {file}: the check would pass vacuously"
        for e, r in pairs:
            assert r.stage == e.get("stage", "default")
            assert r.conditions_op == "all" and len(r.conditions) == 1
            c = r.conditions[0]
            assert (c.field, c.op, c.value.pattern) == ("description", "matches", e["pattern"])
            assert r.set == {k: e[k] for k in ("category", "subcategory", "tags", "needs_review", "vendor") if k in e}


def test_legacy_rules_match_exactly_like_the_old_scan():
    clean = functools.lru_cache(maxsize=None)(VendorNormalizer(load("vendor_rules.yaml")).clean)
    descs = corpus()
    assert len(descs) >= 200, len(descs)
    pairs = demo_legacy_pairs()
    assert pairs, "no pattern: entries read from the demo rulebook: the check would pass vacuously"
    mismatches = []
    for d in descs:
        t = tx(d)
        pre = clean(d)
        for e, r in pairs:
            want = legacy(e["pattern"], d, clean)
            got = R.rule_matches(r, t, cleaner=clean, owner_of=owner_of)
            got_pre = R.rule_matches(r, t, cleaner=clean, owner_of=owner_of, cleaned=pre)
            if got != want or got_pre != want:
                mismatches.append((r.name, d))
    assert not mismatches, mismatches[:10]


# --------------------------------------------- the vendor target rule

def test_vendor_normalizer_target_is_not_the_keyword_target():
    """A raw descriptor that matches a pattern only through _BASE_CLEANUP noise must NOT
    normalize to that vendor (normalize searches the cleaned string and falls back to raw only
    when the cleaned string is empty) — while the same pattern compiled as a Rule DOES match it
    (keyword semantics: cleaned OR raw). Vendor patterns compile as data only."""
    n = VendorNormalizer(load("vendor_rules.yaml"))
    raw = "ACME STORE WEB ID: NETFLIX"
    assert n.clean(raw) == "ACME STORE"                       # the WEB ID token (and its value) is institution noise
    assert n.normalize(raw) == ("Acme Store", None)           # not Netflix
    r = one({"pattern": r"NETFLIX\b", "vendor": "Netflix", "category": "Subscriptions"}, source="vendor_rules")
    assert R.rule_matches(r, tx(raw), cleaner=n.clean, owner_of=owner_of)
    assert n.normalize("NETFLIX.COM")[0] == "Netflix"         # the real path still works
    assert n.normalize("PPD ID: NETFLIX")[0] == "Netflix"     # cleaned is EMPTY -> raw fallback, by the existing rule

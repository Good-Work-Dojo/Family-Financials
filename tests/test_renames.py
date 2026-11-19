# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""etl/enrich/renames.py: one rename in config reaches every stored reference, and a
reference the taxonomy no longer knows warns instead of crashing. Synthetic docs only."""
from __future__ import annotations

import json

import pytest
import yaml
from conftest import (
    answer,
    answers_doc,
    demo_config,
    demo_copy,
    isolated_pipeline,
    make_txn,
)

from etl.config import Config
from etl.enrich import renames as R
from tools.demo import household as hh

TAXONOMY = {"Dining": ["Restaurants", "Coffee", "Bars"], "Entertainment": ["Events"],
            "Shopping": ["Clothing"], "Housing": ["Home Improvement"], "Internal": []}


def answers(*entries):
    return answers_doc(*entries, updated_at="2026-07-16T10:00:00Z")


def vendor_answer(vendor, **set_):
    return answer("vendor", vendor, set_ or {"category": "Dining"}, "2026-07-16T09:00:00Z")


def txn_answer(uid, **set_):
    return answer("txn", uid, set_, "2026-07-16T10:00:00Z")


# ------------------------------------------------------------------ vendor

def test_vendor_rename_reaches_every_consumer():
    r = R.VendorRenamer([{"from": "Baker'S Dozen", "to": "Baker's Dozen"}, {"from": "Old Co", "to": "New Co"}])
    assert r.map("Baker'S Dozen") == "Baker's Dozen" and r.map("Other") == "Other" and r.map(None) is None
    doc = answers(vendor_answer("Baker'S Dozen"), vendor_answer("Other"), txn_answer("tx_1", category="Dining"))
    out = r.apply_to_answers(doc)
    assert out["answers"][0]["vendor"] == "Baker's Dozen" and out["answers"][0]["key"] == "vendor:Baker's Dozen"
    assert out["answers"][1] == doc["answers"][1] and out["answers"][2] == doc["answers"][2]
    assert doc["answers"][0]["vendor"] == "Baker'S Dozen"                       # input untouched
    assert r.apply_to_cancelled([{"vendor": "Old Co", "recorded_on": "2026-03-19"}, {"vendor": "Keep"}]) == \
        [{"vendor": "New Co", "recorded_on": "2026-03-19"}, {"vendor": "Keep"}]
    assert r.apply_to_names(["Old Co", "Amazon", "Baker'S Dozen", "Amazon"]) == ["Amazon", "Baker's Dozen", "New Co"]
    learned = {"version": 1, "rules": [{"vendor": "Old Co", "category": "Dining", "subcategory": None}]}
    assert r.apply_to_learned(learned)["rules"][0]["vendor"] == "New Co"
    assert r.apply_to_learned(None) is None
    assert R.VendorRenamer(None).map("X") == "X" and R.VendorRenamer([]).apply_to_names(None) == []


@pytest.mark.parametrize("renames,msg", [
    ([{"from": "A"}], "needs non-empty string from: and to:"),
    ([{"from": "A", "to": ""}], "needs non-empty string"),
    ([{"from": "A", "to": "A"}], "identical"),
    ([{"from": "A", "to": "B"}, {"from": "A", "to": "C"}], "duplicate from 'A'"),
    ([{"from": "A", "to": "B"}, {"from": "B", "to": "C"}], "is itself renamed"),
])
def test_vendor_renamer_rejects_bad_entries(renames, msg):
    with pytest.raises(ValueError, match=msg):
        R.VendorRenamer(renames)


def test_vendor_renames_compare_casefolded_and_allow_a_case_only_rename():
    """Vendor names compare case-insensitively everywhere else (answers, cancelled, learn), so a
    rename must too — and `Baker'S Dozen -> Baker's Dozen` (what smart_title causes) is valid."""
    r = R.VendorRenamer([{"from": "Baker'S Dozen", "to": "Baker's Dozen"}, {"from": "Old Co", "to": "New Co"},
                         {"from": "Older Co", "to": "New Co"}])
    assert r.map("BAKER'S DOZEN") == "Baker's Dozen" and r.map("Baker's Dozen") == "Baker's Dozen"
    assert r.map("old co") == "New Co" and r.map("OLDER CO") == "New Co" and r.map("New Co") == "New Co"
    learned = {"rules": [{"vendor": "old co", "category": "Dining"}, {"category": "Dining"}]}
    assert r.apply_to_learned(learned)["rules"] == [{"vendor": "New Co", "category": "Dining"}, {"category": "Dining"}]
    assert r.apply_to_cancelled([{"vendor": "OLD CO"}, {"note": "no vendor"}]) == [{"vendor": "New Co"}, {"note": "no vendor"}]
    doc = answers(vendor_answer("OLD CO"), vendor_answer("Baker's Dozen"))
    out = r.apply_to_answers(doc)
    assert out["answers"][0]["vendor"] == "New Co" and out["answers"][0]["key"] == "vendor:New Co"
    assert out["answers"][1] == doc["answers"][1]                        # already canonical: untouched


@pytest.mark.parametrize("renames,msg", [
    ([{"from": "A", "to": "B"}, {"from": "a", "to": "C"}], "duplicate from 'a'"),
    ([{"from": "A", "to": "B"}, {"from": "b", "to": "C"}], "is itself renamed"),
    ([{"from": "A", "to": "b"}, {"from": "B", "to": "C"}], "is itself renamed"),
])
def test_vendor_renamer_rejects_case_variant_duplicates_and_chains(renames, msg):
    with pytest.raises(ValueError, match=msg):
        R.VendorRenamer(renames)


# ---------------------------------------------------------------- category

def test_category_rename_reaches_every_consumer():
    r = R.CategoryRenamer([
        {"from": {"category": "Eating Out"}, "to": {"category": "Dining"}},                       # whole category
        {"from": {"category": "Housing", "subcategory": "Pubs"},
         "to": {"category": "Housing", "subcategory": "Home Improvement"}},                         # one pair
        {"from": {"category": "Nightlife", "subcategory": "Shows"}, "to": {"category": "Entertainment"}},   # pair -> bare
    ], TAXONOMY)
    assert r.map("Eating Out", "Coffee") == ("Dining", "Coffee")
    assert r.map("Eating Out", None) == ("Dining", None)
    assert r.map("Housing", "Pubs") == ("Housing", "Home Improvement")
    assert r.map("Housing", "Coffee") == ("Housing", "Coffee")        # a pair rename leaves other pairs alone
    assert r.map("Nightlife", "Shows") == ("Entertainment", None)
    assert r.map("Nightlife", "Other") == ("Nightlife", "Other")      # unmatched: untouched, unknown_references reports it
    assert r.apply_to_set({"category": "Eating Out"}) == {"category": "Dining"}
    assert r.apply_to_set({"category": "Eating Out", "subcategory": "Coffee", "tags": ["x"]}) == \
        {"category": "Dining", "subcategory": "Coffee", "tags": ["x"]}
    assert r.apply_to_set({"category": "Nightlife", "subcategory": "Shows"}) == {"category": "Entertainment", "subcategory": None}
    assert r.apply_to_set({"type": "transfer"}) == {"type": "transfer"}
    doc = answers(txn_answer("tx_1", category="Eating Out", subcategory="Coffee"),
                  vendor_answer("V", category="Housing", subcategory="Pubs"))
    out = r.apply_to_answers(doc)
    assert out["answers"][0]["set"] == {"category": "Dining", "subcategory": "Coffee"}
    assert out["answers"][1]["set"] == {"category": "Housing", "subcategory": "Home Improvement"}
    assert doc["answers"][0]["set"]["category"] == "Eating Out"       # input untouched
    ov = r.apply_to_overrides([{"match": {"contains": "X"}, "set": {"category": "Eating Out", "needs_review": False}}])
    assert ov[0]["set"] == {"category": "Dining", "needs_review": False} and ov[0]["match"] == {"contains": "X"}
    learned = {"rules": [{"vendor": "V", "category": "Housing", "subcategory": "Pubs"}]}
    assert r.apply_to_learned(learned)["rules"][0] == {"vendor": "V", "category": "Housing", "subcategory": "Home Improvement"}


# A chain is an entry whose result another entry renames again. The list applies once per reference, so
# A->B plus B->C would leave A-answers at B while the persisted learned rules step one hop per ingest (and a
# cycle A->B, B->A flips them on every ingest) - the load refuses it and names both entries.
def _W(from_category, /, **to):
    """a whole-category rename"""
    return {"from": {"category": from_category}, "to": to}


def _P(from_category, from_subcategory, /, **to):
    """a pair rename (the subcategory may be None: rows whose subcategory is null)"""
    return {"from": {"category": from_category, "subcategory": from_subcategory}, "to": to}


@pytest.mark.parametrize("renames,msg", [
    # whole category -> a category another entry moves as a whole
    ([_W("A", category="Dining"), _W("Dining", category="Shopping")],
     r"renames\[0\]: its to 'Dining' is renamed again by renames\[1\]; write the final name"),
    # file order does not matter: the result is stale either way
    ([_W("Dining", category="Shopping"), _W("A", category="Dining")],
     r"renames\[1\]: its to 'Dining' is renamed again by renames\[0\]"),
    # a cycle is a chain
    ([_W("Dining", category="Shopping"), _W("Shopping", category="Dining")],
     r"renames\[0\]: its to 'Shopping' is renamed again by renames\[1\]"),
    # pair -> a pair another pair entry moves
    ([_P("Dining", "Pubs", category="Dining", subcategory="Bars"),
      _P("Dining", "Bars", category="Entertainment", subcategory="Events")],
     r"renames\[0\]: its to 'Dining'/'Bars' is renamed again by renames\[1\]"),
    # pair -> a pair inside a category another entry moves as a whole
    ([_P("A", "x", category="Dining", subcategory="Bars"), _W("Dining", category="Shopping")],
     r"renames\[0\]: its to 'Dining'/'Bars' is renamed again by renames\[1\]"),
    # a whole category with no to.subcategory keeps every subcategory, so a pair entry in the target
    # category catches some of them (Eating Out/Pubs -> Dining/Pubs -> Dining/Bars)
    ([_W("Eating Out", category="Dining"), _P("Dining", "Pubs", category="Dining", subcategory="Bars")],
     r"renames\[0\]: its to 'Dining' is renamed again by renames\[1\]"),
    # ... including the null pair (Eating Out/None -> Dining/None)
    ([_W("Eating Out", category="Dining"), _P("Dining", None, category="Shopping")],
     r"renames\[0\]: its to 'Dining' is renamed again by renames\[1\]"),
    # a pair moved to a bare to lands on (to.category, null): a null-pair entry there renames it again
    ([_P("A", "x", category="Dining"), _P("Dining", None, category="Shopping")],
     r"renames\[0\]: its to 'Dining' is renamed again by renames\[1\]"),
])
def test_category_rename_chains_and_cycles_are_refused_naming_both_entries(renames, msg):
    with pytest.raises(ValueError, match=msg):
        R.CategoryRenamer(renames, TAXONOMY)


def test_category_renames_without_a_chain_still_load():
    """No entry's result is renamed again: a pair rename inside one category next to a whole-category move
    elsewhere, two entries converging on one name, a whole-category move that names its subcategory (each
    reference lands on that one pair, which is not itself renamed) and a pair moved to a bare to (it lands on
    the NULL pair, so a pair entry for another subcategory of that category does not catch it)."""
    r = R.CategoryRenamer([
        _P("Dining", "Pubs", category="Dining", subcategory="Bars"),
        _W("Eating Out", category="Entertainment"),
        _W("Nightlife", category="Entertainment"),                          # converging on one name is not a chain
        _W("Old Shop", category="Shopping", subcategory="Clothing"),
        _P("Shopping", "Gifts", category="Shopping", subcategory="Clothing"),   # converges on the same pair
        _P("A", "x", category="Housing"),
        _P("Housing", "Repairs", category="Housing", subcategory="Home Improvement"),
        _W("Internal", category="Internal", subcategory="Transfers"),     # maps its own result to itself
    ], TAXONOMY)
    assert r.map("Eating Out", "Pubs") == ("Entertainment", "Pubs")
    assert r.map("Dining", "Pubs") == ("Dining", "Bars")
    assert r.map("Old Shop", "x") == ("Shopping", "Clothing")
    assert r.map("A", "x") == ("Housing", None)
    assert r.map("Internal", "x") == ("Internal", "Transfers")
    for cat, sub in [("Eating Out", "Pubs"), ("Dining", "Pubs"), ("Old Shop", "x"), ("A", "x"), ("Shopping", "Gifts"),
                     ("Internal", "x")]:
        once = r.map(cat, sub)
        assert r.map(*once) == once, f"{cat}/{sub} would move again"    # what "no chain" means: one pass is final


@pytest.mark.parametrize("renames,msg", [
    ([{"from": {"category": "A"}, "to": {"category": "Nope"}}], "to.category 'Nope' not in taxonomy"),
    ([{"from": {"category": "A"}, "to": {"category": "Dining", "subcategory": "Brunch"}}],
     "to.subcategory 'Brunch' not seeded under 'Dining'"),
    ([{"from": "A", "to": {"category": "Dining"}}], "need a category"),
    ([{"from": {"category": "Dining"}, "to": {"category": "Dining"}}], "identical"),
    ([{"from": {"category": "A", "subcategory": ["x"]}, "to": {"category": "Dining"}}], "need a category"),
    # a duplicate from is refused with its own index and the first entry's (later ones could never apply)
    ([{"from": {"category": "A"}, "to": {"category": "Dining"}}, {"from": {"category": "A"}, "to": {"category": "Shopping"}}],
     r"renames\[1\]: duplicate from 'A' \(already renamed by renames\[0\]"),
    ([{"from": {"category": "A", "subcategory": "x"}, "to": {"category": "Dining"}},
      {"from": {"category": "B"}, "to": {"category": "Dining"}},
      {"from": {"category": "A", "subcategory": "x"}, "to": {"category": "Shopping"}}],
     r"renames\[2\]: duplicate from 'A'/'x' \(already renamed by renames\[0\]"),
    ([{"from": {"category": "A", "subcategory": None}, "to": {"category": "Dining"}},
      {"from": {"category": "A", "subcategory": None}, "to": {"category": "Shopping"}}],
     r"renames\[1\]: duplicate from 'A'/None"),
])
def test_category_renamer_rejects_bad_entries(renames, msg):
    with pytest.raises(ValueError, match=msg):
        R.CategoryRenamer(renames, TAXONOMY)


def test_category_renames_with_distinct_from_identities_are_not_duplicates():
    """Identity is the category plus whether the entry names a subcategory and which one, so a
    whole-category from, a pair from and a null-pair from for one category are three entries."""
    r = R.CategoryRenamer([
        {"from": {"category": "A", "subcategory": "x"}, "to": {"category": "Dining", "subcategory": "Bars"}},
        {"from": {"category": "A", "subcategory": None}, "to": {"category": "Shopping"}},
        {"from": {"category": "A"}, "to": {"category": "Dining"}},
        {"from": {"category": "A", "subcategory": "y"}, "to": {"category": "Dining", "subcategory": "Coffee"}},
    ], TAXONOMY)
    assert r.map("A", "x") == ("Dining", "Bars")
    assert r.map("A", None) == ("Shopping", None)       # the null pair wins over the whole-category entry after it
    assert r.map("A", "z") == ("Dining", "z")
    assert r.map("A", "y") == ("Dining", "y")           # the pair entry after a whole-category one is shadowed, not a duplicate


# ------------------------------------------------------------ unknown refs

def test_unknown_references_warn_once_with_keys_only():
    doc = answers(txn_answer("tx_1", category="Dinning"), vendor_answer("Some Shop", category="Shopping"),
                  txn_answer("tx_2", category="Dining", subcategory="Brunch"))      # an unseeded subcategory is fine
    overrides = [{"match": {"contains": "secret descriptor"}, "set": {"category": "Housng"}},
                 {"match": {"contains": "x"}, "set": {"type": "transfer"}}]
    learned = {"rules": [{"vendor": "V", "category": "Pets"}]}
    checks = R.unknown_references(doc, overrides, learned, TAXONOMY)
    assert len(checks) == 1 and (checks[0].name, checks[0].status) == ("taxonomy.unknown_reference", "warn")
    d = checks[0].detail
    assert d.startswith("3 stored categorization(s)")
    assert "answer txn:tx_1" in d and "override[0]" in d and "learned V" in d
    assert "secret descriptor" not in d and "Dinning" not in d and "Housng" not in d
    assert R.unknown_references(answers(vendor_answer("S", category="Dining")), [], None, TAXONOMY) == []
    assert R.unknown_references({"answers": []}, None, None, TAXONOMY) == []


# ---------------------------------------------------------- through Config

def test_config_applies_vendor_renames_to_cancelled_and_exclude(tmp_path):
    """The temp copy of the demo config carries its own synthetic cancelled entry and exclude list, so the
    test reads nothing from the demo's assumptions or vendor_rules beyond their shape."""
    def vendor_edits(doc):
        doc["renames"] = [{"from": "Synth Gadget Monthly", "to": "Synth Gadgets Plan"}]
        doc["learning"] = {"exclude": ["Synth Gadget Monthly", "Amazon"]}

    def assumption_edits(doc):
        doc["cancelled_subscriptions"] = [
            *(doc.get("cancelled_subscriptions") or []),
            {"vendor": "Synth Gadget Monthly", "recorded_on": "2026-04-27", "permanent": True}]

    d = demo_copy(tmp_path, {"vendor_rules.yaml": vendor_edits, "assumptions.yaml": assumption_edits})
    cfg = Config(d)
    assert cfg.vendor_rules["learning"]["exclude"] == ["Amazon", "Synth Gadgets Plan"]
    cancelled = {c["vendor"] for c in cfg.assumptions["cancelled_subscriptions"]}
    assert "Synth Gadgets Plan" in cancelled and "Synth Gadget Monthly" not in cancelled
    doc = cfg.vendor_renamer.apply_to_answers(
        answers(vendor_answer("Synth Gadget Monthly", category="Housing", subcategory="Home Services")))
    assert doc["answers"][0]["key"] == "vendor:Synth Gadgets Plan"
    assert doc["answers"][0]["set"] == {"category": "Housing", "subcategory": "Home Services"}   # category untouched


@pytest.mark.parametrize("renames,expected_set", [
    # a pair rename: the category is unchanged, so only the subcategory proves the rename applied
    ([{"from": {"category": "Housing", "subcategory": "Home Services"},
       "to": {"category": "Housing", "subcategory": "Home Improvement"}}],
     {"category": "Housing", "subcategory": "Home Improvement"}),
    # a whole-category rename on a set that names no subcategory: none appears
    ([{"from": {"category": "Eating Out"}, "to": {"category": "Dining"}}],
     {"category": "Dining"}),
])
def test_config_applies_category_renames_to_overrides_and_exposes_the_map(tmp_path, renames, expected_set):
    """The dashboard applies the same map (app/src/lib/categoryRenames.ts, fed by
    assumptions.json category_renames), so Config no longer refuses a non-empty list. Overrides are
    renamed in memory as before; the validated list is exposed for the export."""
    src = renames[0]["from"]
    d = demo_copy(tmp_path, {"category_rules.yaml": lambda doc: doc.__setitem__("renames", renames)})
    stored = {"category": src["category"], **({"subcategory": src["subcategory"]} if "subcategory" in src else {})}
    (d / "overrides.yaml").write_text(yaml.safe_dump([{"match": {"contains": "SYNTH"}, "set": stored}]), "utf-8")
    cfg = Config(d)
    assert stored != expected_set                       # the fixture really is one the rename moves
    assert cfg.overrides[0]["set"] == expected_set      # a concrete changed value, not a no-op equality
    assert cfg.category_renames == renames
    # an empty or absent list stays the common case
    assert Config(demo_copy(tmp_path / "empty", {"category_rules.yaml": lambda doc: doc.__setitem__("renames", [])})).category_renames == []
    assert Config(demo_copy(tmp_path / "absent", {"category_rules.yaml": lambda doc: doc.pop("renames", None)})).category_renames == []


@pytest.mark.parametrize("bad,msg", [
    ({"from": {"category": "Old Two"}, "to": {"category": "Nope"}}, r"renames\[1\]: to\.category 'Nope' not in taxonomy"),
    ({"from": {"category": "Dining"}, "to": {"category": "Dining"}}, r"renames\[1\]: from and to are identical"),
    ({"from": {"category": "Old One"}, "to": {"category": "Shopping"}}, r"renames\[1\]: duplicate from 'Old One'"),
    # a chain stops the load too: Old One -> Dining, then Dining -> Shopping
    ({"from": {"category": "Dining"}, "to": {"category": "Shopping"}},
     r"renames\[0\]: its to 'Dining' is renamed again by renames\[1\]; write the final name"),
])
def test_config_refuses_a_bad_category_rename_naming_its_index(tmp_path, bad, msg):
    """A valid first entry, then a bad second one: the load stops at Config and names renames[1]."""
    good = {"from": {"category": "Old One"}, "to": {"category": "Dining"}}
    d = demo_copy(tmp_path, {"category_rules.yaml": lambda doc: doc.__setitem__("renames", [good, bad])})
    with pytest.raises(ValueError, match=msg):
        Config(d)
    Config(demo_copy(tmp_path / "ok", {"category_rules.yaml": lambda doc: doc.__setitem__("renames", [good])}))


def test_config_rejects_a_vendor_rename_from_a_live_pattern_vendor(tmp_path):
    """Pattern rows keep emitting their own `vendor:` string, so renaming that string would orphan
    every stored reference instead of moving it: change the pattern's `vendor:` instead."""
    def rename(frm, to="Somewhere Else"):
        return lambda doc: doc.__setitem__("renames", [{"from": "Old Co", "to": "Old Co 2"},
                                                       {"from": frm, "to": to}])

    d = demo_copy(tmp_path, {"vendor_rules.yaml": rename("Amazon")})
    with pytest.raises(ValueError, match=r"renames\[1\].*'Amazon'.*patterns"):
        Config(d)
    d2 = demo_copy(tmp_path / "two", {"vendor_rules.yaml": rename("AMAZON")})        # casefolded, like the renamer
    with pytest.raises(ValueError, match=r"renames\[1\].*'AMAZON'.*patterns"):
        Config(d2)
    # the other direction is the intended use: an old spelling renamed TO a pattern vendor
    d3 = demo_copy(tmp_path / "three", {"vendor_rules.yaml": rename("Amazon Marketplace Old", "Amazon")})
    assert Config(d3).vendor_renamer.map("amazon marketplace old") == "Amazon"


def test_config_validates_the_learning_block(tmp_path):
    d = demo_copy(tmp_path, {"vendor_rules.yaml": lambda doc: doc.__setitem__("learning", {"exclude": "Amazon"})})
    with pytest.raises(ValueError, match="learning"):
        Config(d)
    d2 = demo_copy(tmp_path / "two", {"vendor_rules.yaml": lambda doc: doc["patterns"].__getitem__(0).__setitem__("learn", "no")})
    with pytest.raises(ValueError, match="learn must be a boolean"):
        Config(d2)


def test_pipeline_renames_the_answers_doc_and_warns_once_on_an_unknown_category(tmp_path):
    """Pipeline.enrich: a vendor-scope answer filed under an old vendor name and an old category
    name still lands (renamed in memory, the file untouched); a stray category warns, keys only."""
    cfg = demo_config()
    cfg.vendor_renamer = R.VendorRenamer([{"from": "Old Emporium", "to": "Synth Widget Emporium"}])
    cfg.category_renamer = R.CategoryRenamer(
        [{"from": {"category": "Eating Out"}, "to": {"category": "Dining"}}], cfg.category_rules["taxonomy"])
    shop = make_txn(desc="SYNTH WIDGET EMPORIUM #4411", cents=-2500, account=hh.CARD_ALEX, date="2026-03-04")
    stray = make_txn(desc="SYNTH OTHER SHOP 9", cents=-1500, account=hh.CARD_ALEX, date="2026-03-05")
    stored = answers(vendor_answer("Old Emporium", category="Eating Out", subcategory="Coffee"),
                     txn_answer(stray.uid, category="Dinning"))
    p = isolated_pipeline(tmp_path, [shop, stray], cfg=cfg, answers=stored["answers"],      # nothing learned on disk
                          answers_updated_at=stored["updated_at"], windows={hh.CARD_ALEX: ("2026-01-01", "2026-12-31")})
    p.enrich()
    assert shop.vendor == "Synth Widget Emporium"
    assert (shop.category, shop.subcategory, shop.category_source) == ("Dining", "Coffee", "answer")
    warns = [c for c in p.checks if c.name == "taxonomy.unknown_reference"]
    assert len(warns) == 1 and warns[0].status == "warn"
    assert f"answer txn:{stray.uid}" in warns[0].detail and "Dinning" not in warns[0].detail
    assert json.loads(cfg.answers_path.read_text("utf-8")) == stored          # never rewritten on disk

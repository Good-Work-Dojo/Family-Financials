# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Golden categorization cases on the starter rule files: invented descriptors, printed the way card networks print
national merchants, run through VendorNormalizer and the Categorizer exactly as the pipeline builds them, from a config
that is a copy of starter/ plus an invented household with one account of each kind. Every vendor pattern is the first
match of some case, the look-alikes (a neighbour's name, the merchant's word inside another word) reach no pattern,
and the cases cover every path the rulebook decides. The oracle is the code: when a hand-derived expectation and the
Categorizer disagree, the case is wrong or the pattern is, never the engine."""
from __future__ import annotations

import re
import shutil

import pytest
import yaml
from conftest import ROOT, make_txn, write_household
from test_starter import ACCOUNT_OF, KIND_ACCOUNTS, STARTER
from test_starter import P2P_DESCRIPTORS as STARTER_P2P_DESCRIPTORS

from etl.config import Config
from etl.enrich.vendor_registry import learning_excluded
from etl.enrich.vendors import VendorNormalizer
from etl.models import (
    ACCOUNT_KINDS,
    CATEGORY_SOURCES,
    FALLBACK_CATEGORY,
    P2P_UNKNOWN_SUB,
)
from etl.pipeline import categorizer_for

CASES_PATH = ROOT / "tests" / "golden" / "starter_cases.yaml"
CASES: list[dict] = yaml.safe_load(CASES_PATH.read_text("utf-8"))["cases"]
INPUT_KEYS = {"desc", "amount", "type", "kind", "bank_category"}
EXPECT_KEYS = {"vendor", "category", "subcategory", "source", "needs_review"}
VENDOR_RULES = yaml.safe_load((STARTER / "vendor_rules.yaml").read_text("utf-8"))
PATTERNS: list[dict] = VENDOR_RULES["patterns"]
VENDORS = {e["vendor"] for e in PATTERNS}
TAXONOMY: dict[str, list[str]] = yaml.safe_load((STARTER / "category_rules.yaml").read_text("utf-8"))["taxonomy"]
# the person-to-person descriptors tests/test_starter.py pins the catch-all on, and payments to people who share a
# merchant's name: a vendor pattern outranks the catch-all, so one that matched these would strip their review flag
P2P_DESCRIPTORS = (*STARTER_P2P_DESCRIPTORS, "ZELLE PAYMENT TO JOHN MARRIOTT", "ZELLE PAYMENT TO JANE NORDSTROM",
                   "ZELLE PAYMENT TO MARY WALGREEN", "VENMO *DAVID ORKIN", "ZELLE PAYMENT TO SEPHORA JEAN",
                   "ZELLE PAYMENT TO JIMMY JOHNS", "ZELLE PAYMENT TO SHERWIN WILLIAMS", "ZELLE PAYMENT FROM PAT DUNKIN")


@pytest.fixture(scope="module")
def cfg(tmp_path_factory) -> Config:
    """A Config read from a copy of starter/ with household.yaml and one invented account of each kind."""
    d = tmp_path_factory.mktemp("golden") / "config"
    shutil.copytree(STARTER, d)
    write_household(d)
    (d / "accounts.yaml").write_text(yaml.safe_dump({"accounts": KIND_ACCOUNTS}), "utf-8")
    return Config(d)


@pytest.fixture(scope="module")
def normalizer(cfg):
    return VendorNormalizer(cfg.vendor_rules)


@pytest.fixture(scope="module")
def categorizer(cfg):
    return categorizer_for(cfg)


def run_case(case: dict, normalizer, categorizer):
    t = make_txn(desc=case["desc"], cents=round(case.get("amount", -10) * 100), type=case.get("type", "purchase"),
                 account=ACCOUNT_OF[case.get("kind", "credit-card")], bank_category=case.get("bank_category"))
    t.vendor, _ = normalizer.normalize(t.description_raw)
    categorizer.apply([t], normalizer.clean)
    return t


def first_pattern(normalizer, desc: str) -> int | None:
    """The index in `patterns:` of the entry that names the vendor of `desc`, or None when none matches."""
    _, entry = normalizer.normalize(desc)
    return None if entry is None else next(i for i, (_, e) in enumerate(normalizer.patterns) if e is entry)


# ---------------------------------------------------------------------------------------------------- the cases

@pytest.mark.parametrize("case", CASES, ids=[c["desc"] for c in CASES])
def test_golden_case(case, normalizer, categorizer):
    t = run_case(case, normalizer, categorizer)
    got = (t.vendor, t.category, t.subcategory, t.category_source, t.needs_review)
    want = (case["vendor"], case["category"], case.get("subcategory"), case["source"], case.get("needs_review", False))
    assert got == want, f"{case['desc']!r}: got {got}, want {want}"


def test_the_golden_file_is_well_formed():
    descs = [c["desc"] for c in CASES]
    assert len(set(descs)) == len(descs), "duplicate desc"
    for c in CASES:
        assert set(c) <= INPUT_KEYS | EXPECT_KEYS, (c["desc"], set(c) - INPUT_KEYS - EXPECT_KEYS)
        assert {"desc", "vendor", "category", "source"} <= set(c), c["desc"]
        assert c.get("kind", "credit-card") in ACCOUNT_KINDS and c["source"] in CATEGORY_SOURCES, c["desc"]


def test_every_vendor_pattern_is_the_first_match_of_a_case(normalizer):
    """No dead pattern and no shadowed one: each entry names the vendor of at least one case's descriptor."""
    reached = {first_pattern(normalizer, c["desc"]) for c in CASES}
    assert [e["pattern"] for i, e in enumerate(PATTERNS) if i not in reached] == []


def merchant_words() -> set[str]:
    """The words the patterns spell out, upper case, three letters or more: the literal text left once the escapes
    (`\\b`, `\\d`) are taken out, so a word a lookahead excludes counts too."""
    return {w for e in PATTERNS for w in re.findall(r"[A-Z]{3,}", re.sub(r"\\[A-Za-z]", " ", e["pattern"]))}


def shares_a_merchant_word(cleaned: str, words: set[str]) -> bool:
    """A word of the descriptor is a merchant's word, or holds one of four letters or more (as its prefix or inside)."""
    return any(d == w or (len(w) >= 4 and w in d) for d in re.findall(r"[A-Z]+", cleaned.upper()) for w in words)


def test_look_alikes_reach_no_pattern(normalizer):
    """A descriptor that shares a word or a prefix with a listed merchant but is not that merchant (a neighbour's name,
    the merchant's word inside another word) reaches no pattern: the rulebook files it as it would without the list."""
    words = merchant_words()
    look_alikes = [c for c in CASES if c["source"] != "vendor_rule" and c["vendor"] not in VENDORS
                   and shares_a_merchant_word(normalizer.clean(c["desc"]), words)]
    assert [c["desc"] for c in look_alikes if first_pattern(normalizer, c["desc"]) is not None] == []
    assert len(look_alikes) >= 40, len(look_alikes)


@pytest.mark.parametrize("desc", P2P_DESCRIPTORS)
def test_no_pattern_matches_a_person_to_person_descriptor(desc, normalizer, categorizer):
    assert first_pattern(normalizer, desc) is None
    t = run_case({"desc": desc, "amount": -30, "kind": "checking"}, normalizer, categorizer)
    assert (t.category, t.subcategory, t.category_source) == (FALLBACK_CATEGORY, P2P_UNKNOWN_SUB, "keyword")


def test_the_cases_cover_every_path_a_rulebook_decides(cfg):
    assert {c["source"] for c in CASES} >= {"vendor_rule", "keyword", "bank_label", "fallback"}
    # a fallback case on each side of the review threshold
    threshold = cfg.assumptions["uncategorized_review_threshold"]
    fallbacks = [(abs(c.get("amount", -10)), c.get("needs_review", False)) for c in CASES if c["source"] == "fallback"]
    assert any(a < threshold and not review for a, review in fallbacks)
    assert any(a >= threshold and review for a, review in fallbacks)
    # every category and subcategory a pattern assigns, through a pattern; every bank label, through that label
    by_pattern = {(e["category"], e.get("subcategory")) for e in PATTERNS if "category" in e}
    covered = {(c["category"], c.get("subcategory")) for c in CASES if c["source"] == "vendor_rule"}
    assert by_pattern and sorted(by_pattern - covered, key=str) == []
    labelled = {c.get("bank_category") for c in CASES if c["source"] == "bank_label"}
    assert sorted(set(cfg.bank_category_map) - labelled) == []


def required_letters(pattern: str) -> int:
    """The most letters in one stretch of literal text that every match of `pattern` holds. Letters, digits, spaces and
    plain or escaped punctuation make a stretch; an anchor, a class, an escape such as \\d, a group or an alternation
    ends it. A character, class or group a quantifier makes optional counts for nothing, a lookaround counts for
    nothing, and a group counts its weakest branch."""
    pos = 0

    def alternatives() -> int:
        nonlocal pos
        values = [sequence()]
        while pos < len(pattern) and pattern[pos] == "|":
            pos += 1
            values.append(sequence())
        return min(values)

    def atom() -> tuple[str, int]:
        nonlocal pos
        c = pattern[pos]
        if c == "\\":
            pos += 2
            return ("other", 0) if pattern[pos - 1].isalnum() else ("char", 0)
        if c == "[":
            pos = pattern.index("]", pos + 1) + 1
            return ("other", 0)
        if c == "(":
            pos += 1
            lookaround = pattern.startswith(("?=", "?!", "?<=", "?<!"), pos)
            if pattern.startswith("?", pos):
                pos += 2 if pattern[pos + 1] in ":=!" else 3
            value = alternatives()
            pos += 1                                                    # the closing parenthesis
            return ("other", 0) if lookaround else ("group", value)
        pos += 1
        return ("other", 0) if c in "^$." else ("char", int(c.isalpha()))

    def sequence() -> int:
        nonlocal pos
        best = run = 0
        while pos < len(pattern) and pattern[pos] not in "|)":
            kind, letters = atom()
            optional = repeated = False
            if pos < len(pattern) and pattern[pos] in "?*+{":
                if pattern[pos] == "{":
                    end = pattern.index("}", pos)
                    optional, repeated = pattern[pos + 1:end].split(",")[0] in ("", "0"), True
                    pos = end + 1
                else:
                    optional, repeated = pattern[pos] in "?*", pattern[pos] in "*+"
                    pos += 1
                if pos < len(pattern) and pattern[pos] == "?":
                    pos += 1                                            # a lazy quantifier
            if kind == "char" and not optional:
                run += letters
                best = max(best, run)
                run = 0 if repeated else run
            else:
                run = 0
                best = max(best, letters) if kind == "group" and not optional else best
        return best

    return alternatives()


def test_the_letter_count_reads_only_what_every_match_holds():
    assert required_letters(r"\bTACO BELL\b") == 8
    assert required_letters(r"\bCVS\b") == 3
    assert required_letters(r"\b(?:WHOLEFDS|WHOLE FOODS)\b") == 8
    assert required_letters(r"\bSHELL (?:OIL|SERVICE)\b") == 5
    assert required_letters(r"\bAB(?:CDEFGH)?\b") == 2
    assert required_letters(r"\bABCD[ -]?EFGH\b") == 4
    assert required_letters(r"\bABCD'?S\b") == 4
    assert required_letters(r"\bAB\d+CDEFG\b") == 5
    assert required_letters(r"\b(?:ABCDEF|AB)\b") == 2
    assert required_letters(r"\bABCD(?! EFGHIJ)\b") == 4
    assert required_letters(r"^AB ?\*? ?CD\b") == 2


@pytest.mark.parametrize("entry", PATTERNS, ids=[f"{i}-{e['vendor']}" for i, e in enumerate(PATTERNS)])
def test_every_pattern_is_anchored(entry):
    """Each pattern starts at a word boundary or at the start of the descriptor, ends at a word boundary (a trailing
    negative lookahead aside) or on an escaped punctuation mark (the `*` a processor prints after a merchant's name),
    and holds a literal run of at least five letters unless it starts at the start of the descriptor."""
    p = entry["pattern"]
    assert p.startswith(("^", r"\b")), p
    core = re.sub(r"\(\?![^()]*\)$", "", p)
    assert core.endswith(r"\b") or re.search(r"\\[^\w\s]$", core), p
    assert p.startswith("^") or required_letters(p) >= 5, p


def test_every_category_is_in_the_starter_taxonomy():
    for e in PATTERNS:
        assert "subcategory" not in e or "category" in e, e["vendor"]
        if "category" in e:
            assert e["category"] in TAXONOMY, e["vendor"]
            assert e.get("subcategory") is None or e["subcategory"] in TAXONOMY[e["category"]], e["vendor"]
    for c in CASES:
        assert c["category"] in TAXONOMY, c["desc"]
        assert c.get("subcategory") is None or c["subcategory"] in TAXONOMY[c["category"]], c["desc"]
    # the categorizer keys a pattern's category by vendor name: every pattern of one vendor files the same way
    filed: dict[str, tuple] = {}
    for e in PATTERNS:
        assert filed.setdefault(e["vendor"], (e.get("category"), e.get("subcategory"))) == (
            e.get("category"), e.get("subcategory")), e["vendor"]


def test_an_everything_store_has_no_category_and_never_learns(cfg):
    """A store whose baskets span several categories names its vendor and files nothing, and no review answer turns
    into a learned rule for it; every vendor that files nothing is such a store. Amazon is the one that is also filed,
    under its own subcategory of Shopping."""
    no_category = {e["vendor"] for e in PATTERNS if "category" not in e}
    assert no_category and not no_category & {e["vendor"] for e in PATTERNS if "category" in e}
    excluded = set(VENDOR_RULES["learning"]["exclude"])
    assert excluded == no_category | {"Amazon"}
    assert {(e.get("category"), e.get("subcategory")) for e in PATTERNS if e["vendor"] == "Amazon"} == {
        ("Shopping", "Amazon")}
    assert {v.casefold() for v in excluded} <= learning_excluded(cfg.vendor_rules)

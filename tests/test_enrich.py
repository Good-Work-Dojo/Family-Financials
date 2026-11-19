# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Vendor normalization and smart titles, keyword stages and owner conditions. Synthetic rows, on the demo household's
vendor rules where a test needs a config's."""
import pytest
from conftest import load_demo, make_txn

from etl.enrich.categorize import Categorizer
from etl.enrich.vendors import VendorNormalizer, smart_title
from tools.demo import household as hh


@pytest.fixture(scope="module")
def normalizer():
    return VendorNormalizer(load_demo("vendor_rules.yaml"))


def tx(desc, amount=-1000, account=hh.CARD_ALEX, bank_category=None,
       bank_type=None, ttype="purchase"):
    return make_txn(desc=desc, cents=amount, account=account,
                    bank_category=bank_category, bank_type=bank_type, type=ttype)


def test_keyword_stages_run_pre_then_default_then_post():
    """Stages pre -> default -> post, file order within a stage, first match wins. A `pre`
    rule outranks an earlier-in-file default rule; a `post` rule never beats a default hit."""
    rules = {"taxonomy": {}, "keyword_rules": [
        {"pattern": "TACO", "category": "Dining"},                                   # default
        {"name": "late", "stage": "post", "pattern": "TACO", "category": "Pets"},
        {"name": "early", "stage": "pre", "pattern": "TUESDAY", "category": "Entertainment"},
    ]}
    cat = Categorizer(rules, {}, {"patterns": []}, {}, {})
    n = VendorNormalizer({"patterns": []})
    a, b, c = tx("TACO TUESDAY"), tx("TACO MONDAY"), tx("NOTHING HERE")
    cat.apply([a, b, c], n.clean)
    assert (a.category, a.category_source) == ("Entertainment", "keyword")
    assert (b.category, b.category_source) == ("Dining", "keyword")
    assert (c.category, c.category_source) == ("Cash & Misc", "fallback")
    assert cat.first_hit(a, n.clean).name == "early"
    assert [r.name for r in cat.all_hits(a, n.clean)] == ["early", "keyword_rules:0", "late"]
    assert cat.first_hit(c, n.clean) is None and cat.all_hits(c, n.clean) == []
    assert [r.name for r in cat.by_stage["default"]] == ["keyword_rules:0"]


def test_owner_conditions_need_the_owner_map_and_default_to_never_matching():
    rules = {"taxonomy": {}, "keyword_rules": [
        {"name": "alexs", "all": [{"field": "owner", "op": "is", "value": hh.ALEX},
                                  {"field": "description", "op": "contains", "value": "ZZZ"}],
         "set": {"category": "Pets"}}]}
    n = VendorNormalizer({"patterns": []})
    with_map = Categorizer(rules, {}, {"patterns": []}, {}, {}, owner_of={"acct_a": hh.ALEX})
    without = Categorizer(rules, {}, {"patterns": []}, {}, {})
    a, b = tx("ZZZ SHOP", account="acct_a"), tx("ZZZ SHOP", account="acct_a")
    with_map.apply([a], n.clean)
    without.apply([b], n.clean)
    assert (a.category, a.category_source) == ("Pets", "keyword")
    assert (b.category, b.category_source) == ("Cash & Misc", "fallback")    # no map: the owner condition is false


@pytest.mark.parametrize("op,value", [("is", hh.ALEX), ("oneOf", [hh.ALEX, hh.SAM]), ("isNot", hh.SAM)])
@pytest.mark.parametrize("owners,account,fires", [
    ({"acct_a": hh.ALEX}, "acct_a", True),        # control: a resolvable owner that satisfies the op
    ({"acct_a": hh.ALEX}, "acct_x", False),       # non-empty map, account missing from it
    ({}, "acct_a", False),                        # empty map
    (None, "acct_a", False),                      # no map at all
])
def test_owner_ops_never_fire_on_an_unresolvable_owner(op, value, owners, account, fires):
    """A rule about owners must not fire on a row whose owner is unknown — `isNot` included,
    which would otherwise be true of the empty owner."""
    rules = {"taxonomy": {}, "keyword_rules": [
        {"name": "owned", "all": [{"field": "owner", "op": op, "value": value},
                                  {"field": "description", "op": "contains", "value": "ZZZ"}],
         "set": {"category": "Pets"}}]}
    n = VendorNormalizer({"patterns": []})
    cat = Categorizer(rules, {}, {"patterns": []}, {}, {}, owner_of=owners)
    t = tx("ZZZ SHOP", account=account)
    cat.apply([t], n.clean)
    assert (t.category, t.category_source) == (("Pets", "keyword") if fires else ("Cash & Misc", "fallback"))
    assert (cat.first_hit(t, n.clean) is not None) == fires


@pytest.mark.parametrize("raw,want", [
    ("BAKER'S DOZEN", "Baker's Dozen"),
    ("CPA ONLINE", "CPA Online"),                          # an acronym the config adds ...
    ("ATM WITHDRAWAL AUTHORIZED ON", "ATM Withdrawal Authorized On"),
    ("DDSTAG HARBOR DDS", "Ddstag Harbor DDS"),            # ... as a whole token only
    ("US POSTAL SERVICE", "US Postal Service"),
    ("ACME GELATO INC", "Acme Gelato Inc"),                # INC is not an acronym ...
    ("ACME GELATO LLC", "Acme Gelato LLC"),                # ... LLC and PLLC still are
    ("SMITH & JONES PLLC", "Smith & Jones PLLC"),
    ("ZOOM.US", "Zoom.Us"),                                # US is the country only as a standalone word ...
    ("PAY.US", "Pay.Us"),
    ("GLOBO*US WIDGET HUT", "Globo*Us Widget Hut"),        # (a dot or a star joins a word to its neighbour)
    ("ACME (US)", "Acme (US)"),                            # ... and parentheses, commas and slashes end a word
    ("MADE IN US, LLP", "Made In US, LLP"),
    ("US/CANADA TOUR", "US/Canada Tour"),
    ("CANADA/US", "Canada/US"),
    ("ZOOM.US 888", "Zoom.Us 888"),
    ("WWW.EXAMPLE.US BILLING", "Www.Example.Us Billing"),
    ("PAYMENT TO US", "Payment To US"),                    # whole token at the end ...
    ("US", "US"),                                          # ... alone ...
    ("MADE IN US-1 DINER", "Made In Us-1 Diner"),          # ... but never part of a longer token
    ("HARBOR CPA US", "Harbor CPA US"),
    ("USPS PO", "USPS Po"),
    ("AT&T STORE", "AT&T Store"),
    ("A-B-C", "A-B-C"),
    ("WAL-MART", "Wal-Mart"),                              # hyphens: exactly str.title()
    ("ACME9X.NET PLAN", "Acme9X.Net Plan"),                # digits and dots: exactly str.title()
    ("THE GAS BAR AND GRILL", "The Gas Bar And Grill"),    # no length rule — short words stay words
    ("MCDONALD'S F1234", "Mcdonald's F1234"),              # Mc casing is out of scope by decision
    ("O'NEILL'S PUB", "O'Neill's Pub"),                    # one letter before an apostrophe = a name prefix: kept
    ("O'REILLY AUTO PARTS", "O'Reilly Auto Parts"),
    ("D'ANGELO", "D'Angelo"),
    ("L'OCCITANE", "L'Occitane"),
    ("JO'S", "Jo's"),                                      # two letters before it = a possessive / contraction
    ("DOMINO'S PIZZA", "Domino's Pizza"),
    ("ROCK 'N' ROLL", "Rock 'N' Roll"),
])
def test_smart_title_cases(normalizer, raw, want):
    assert smart_title(raw, normalizer.acronyms) == want      # the built-in acronyms plus the config's own


def test_smart_title_equals_str_title_without_apostrophes_or_acronyms():
    for s in ["MAPLE STREET BAKERY", "PARKING GARAGE", "GOOGLE *CLOUD", "NYTIMES*NYT DIGITAL", "7-ELEVEN 12345",
              "SQ *ACME COFFEE", "WT BANK OF SOMEWHERE", "BEST BUY FOOD MART", "B&N BOOKSELLERS",
              "UBER *TRIP HELP.UBER.COM", "SOME RANDOM BOUTIQUE"]:
        assert smart_title(s) == s.title(), s


def test_smart_title_treats_non_ascii_letters_exactly_like_str_title():
    for s in ["CAFÉ ÉTOILE", "JALAPEÑO GRILL", "STRASSE ß"]:
        assert smart_title(s) == s.title(), s
    assert smart_title("ÉCOLE US") == "École US"                  # the acronym fix still applies beside them
    assert smart_title("CAFÉ O'BRIEN'S") == "Café O'Brien's"      # and so does the apostrophe fix


def test_smart_title_is_injective_on_uppercase_input_and_fallback_keeps_mixed_case(normalizer):
    seen: dict[str, str] = {}
    for s in ["BAKER'S DOZEN", "BAKERS DOZEN", "CPA ONLINE", "CPA  ONLINE", "ATM", "ATMS", "OK", "OKAY"]:
        out = smart_title(s)
        assert out.upper() == s and out not in seen, (s, out, seen.get(out))   # case-only change: no two vendors merge
        seen[out] = s
    assert normalizer._fallback("Payment Thank You-Mobile") == "Payment Thank You-Mobile"   # mixed case: untouched
    assert normalizer._fallback("BAKER'S DOZEN 1234 MORE WORDS") == "Baker's Dozen 1234 More"   # first four words
    assert normalizer._fallback("") == "Unknown"


@pytest.mark.parametrize("raw,vendor", [
    ("Synth Daily News — Digital Subscription", "Synth Daily News"),    # the four-word cut ends on the joining dash
    ("SYNTH DAILY NEWS — DIGITAL SUBSCRIPTION", "Synth Daily News"),    # shouty: title-cased after the dash is gone
    ("SYNTH DAILY NEWS —", "Synth Daily News"),                         # a payee-only descriptor ending on it
    ("Synth Daily News —   ", "Synth Daily News"),
    ("Synth Co — Autopay Thank You", "Synth Co — Autopay"),             # a dash inside the four words stays
    ("Synth Daily News Digital Extra", "Synth Daily News Digital"),     # no dash: unchanged
    ("Synth Daily News - Digital", "Synth Daily News -"),               # a hyphen is not the joining dash
    ("—", "Unknown"),
])
def test_a_fallback_vendor_never_ends_on_the_joining_dash(normalizer, raw, vendor):
    assert normalizer.normalize(raw) == (vendor, None)


def test_the_dash_fix_moves_no_uid_and_no_pattern_match(normalizer):
    t = tx("Synth Daily News — Digital Subscription")
    uid = t.uid
    t.vendor, rule = normalizer.normalize(t.description_raw)
    assert (t.vendor, rule) == ("Synth Daily News", None) and t.uid == uid          # uid hashes the raw descriptor
    assert normalizer.normalize("AMZN Mktp US*ZZ00AA111 —")[0] == "Amazon"          # a vendor pattern still hits first

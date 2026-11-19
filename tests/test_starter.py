# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The starter files (starter/): what a new household's config/ begins from. A household copies them into its config/
directory; the engine reads only config/, never starter/. Every file must load as a config, file rows under its own
taxonomy and carry no key the engine does not read for that file. Also here: the vendor cleanup and acronyms the
engine takes from vendor_rules.yaml in addition to its own format-neutral built-ins, and how the bank-format
fragments at the head of the starter's cleanup list clean. Invented descriptors and an invented household only."""
from __future__ import annotations

import re
import shutil

import pytest
import yaml
from conftest import ROOT, make_txn, write_household

from etl.config import PATH_VARIABLES, Config
from etl.enrich import rules as R
from etl.enrich.categorize import Categorizer
from etl.enrich.income import IncomeIdentifier
from etl.enrich.transfers import TransferEngine, compile_transfer_rules
from etl.enrich.vendors import VendorNormalizer
from etl.exports import PROJECTION_DEFAULTS, export_projections
from etl.models import (
    ACCOUNT_KINDS,
    CASHFLOW_KINDS,
    FALLBACK_CATEGORY,
    INCOME_CATEGORY,
    P2P_UNKNOWN_SUB,
    RESERVED,
    RESERVED_TAXONOMY,
)

STARTER = ROOT / "starter"
YAML_FILES = ("assumptions.yaml", "bank_category_map.yaml", "category_rules.yaml", "transfer_rules.yaml",
              "vendor_rules.yaml")
# the top-level keys the engine reads in each file; bank_category_map.yaml is keyed by label (its entries are checked)
READ_KEYS = {
    "category_rules.yaml": {"taxonomy", "keyword_rules", "income_rules", "renames"},
    "vendor_rules.yaml": {"strip_prefixes", "cleanup", "acronyms", "patterns", "learning", "renames"},
    "assumptions.yaml": {"match_window_bdays", "venmo_match_window_bdays", "venmo_review_threshold",
                         "uncategorized_review_threshold", "unknown_credit_review_threshold", "generic_min_amount",
                         "projections"},
    "transfer_rules.yaml": {"rules", "groups", "outside", "venmo_patterns", "generic_matching"},
}
# keys a starter file may carry ahead of the code that reads them
NOT_YET_READ = {"assumptions.yaml": {"coverage"}}
LABEL_KEYS = {"category", "subcategory", "tags"}
# a word after " - " in a taxonomy name that names a role, never a person
ROLE_WORDS = {"Kids", "Children", "Adults", "Family", "Household"}
_DASH_WORD = re.compile(r" - ([A-Z][\w'’-]*)")


def starter(name: str):
    return yaml.safe_load((STARTER / name).read_text("utf-8"))


def taxonomy() -> dict[str, list[str]]:
    return starter("category_rules.yaml")["taxonomy"]


def test_the_starter_directory_holds_the_rule_files_and_a_readme():
    assert sorted(p.name for p in STARTER.iterdir()) == sorted([*YAML_FILES, "README.md"])


@pytest.mark.parametrize("name", YAML_FILES)
def test_every_starter_file_is_a_yaml_mapping(name):
    assert isinstance(starter(name), dict)


@pytest.mark.parametrize("name", sorted(READ_KEYS))
def test_a_starter_file_carries_only_keys_the_engine_reads(name):
    assert set(starter(name)) <= READ_KEYS[name] | NOT_YET_READ.get(name, set())


def test_a_bank_label_carries_only_the_keys_categorize_reads():
    for label, entry in starter("bank_category_map.yaml").items():
        assert isinstance(entry, dict) and set(entry) <= LABEL_KEYS and "category" in entry, label


def test_the_taxonomy_names_no_person():
    tax = taxonomy()
    names = [*tax, *(s for subs in tax.values() for s in subs)]
    assert all(isinstance(n, str) and n for n in names)
    named = [n for n in names for word in _DASH_WORD.findall(n) if word not in ROLE_WORDS]
    assert named == []
    assert {"Salary", "Bonus", "Consulting"} <= set(tax[INCOME_CATEGORY])   # the income streams, by stream only


def test_a_taxonomy_list_names_each_subcategory_once():
    for category, subs in taxonomy().items():
        assert len(subs) == len(set(subs)), category


def test_the_taxonomy_holds_the_names_the_engine_files_rows_under():
    tax = taxonomy()
    for category, subs in RESERVED_TAXONOMY.items():
        assert category in tax
        assert set(subs) <= set(tax[category]), category
    assert set(RESERVED.values()) <= {*tax, *(s for listed in tax.values() for s in listed)}


def test_the_keyword_rules_compile_against_the_starter_taxonomy():
    rules = starter("category_rules.yaml")
    compiled = R.compile_rules(rules["keyword_rules"], source="keyword_rules")
    R.validate_taxonomy(compiled, rules["taxonomy"])
    assert rules["income_rules"] == [] and rules["renames"] == []


def test_the_p2p_catch_all_is_the_one_keyword_rule_and_the_last_default_rule():
    compiled = R.compile_rules(starter("category_rules.yaml")["keyword_rules"], source="keyword_rules")
    assert len(compiled) == 1
    last = [r for r in compiled if r.stage == "default"][-1]
    assert last.conditions[0].op == "matches" and "VENMO" in last.conditions[0].value.pattern
    assert (last.set["category"], last.set["subcategory"]) == (FALLBACK_CATEGORY, P2P_UNKNOWN_SUB)


# the descriptors the catch-all and the payment-app patterns are pinned on (tests/test_golden_categorize.py reuses them)
P2P_DESCRIPTORS = ("VENMO PAYMENT 1234567", "VENMO CASHOUT 1234567", "ZELLE PAYMENT TO SAMPLE ROBIN", "PAYPAL INST XFER",
                   "PAYPAL INST XFER 1234567890", "PAYPAL TRANSFER", "PAYPAL TRANSFER 1234567890",
                   "PAYPAL     INST XFER  1234567890 WEB ID: PAYPALSI77", "CASH APP*SAMPLE ROBIN", "CASH APP*CASH OUT",
                   "Cash App Sample Robin")


@pytest.mark.parametrize("desc", P2P_DESCRIPTORS)
def test_the_catch_all_matches_each_common_p2p_app(desc):
    (rule,) = R.compile_rules(starter("category_rules.yaml")["keyword_rules"], source="keyword_rules")
    assert R.rule_matches(rule, make_txn(desc=desc), cleaner=lambda s: s, owner_of=lambda _: "")


# a card payment through PayPal names a shop: a purchase, not a person-to-person payment
@pytest.mark.parametrize("desc", ["ACME COFFEE", "VENMOUTH SUPPLY", "CASHAPPLE FARMS", "PAYPAL *SAMPLESHOP"])
def test_the_catch_all_leaves_other_descriptors_alone(desc):
    (rule,) = R.compile_rules(starter("category_rules.yaml")["keyword_rules"], source="keyword_rules")
    assert not R.rule_matches(rule, make_txn(desc=desc), cleaner=lambda s: s, owner_of=lambda _: "")


def shipped_taxonomy() -> list[tuple[str, list[str]]]:
    """The dashboard's SHIPPED_TAXONOMY (app/src/lib/categories.ts), parsed from its source: the object literal's keys
    (bare or double-quoted), each with its array of double-quoted strings, in order."""
    ts = (ROOT / "app" / "src" / "lib" / "categories.ts").read_text("utf-8")
    m = re.search(r"export const SHIPPED_TAXONOMY\b[^=]*=\s*\{(.*?)\n\};", ts, re.S)
    assert m, "SHIPPED_TAXONOMY object literal not found in lib/categories.ts"
    entries = re.findall(r'(?:"([^"]+)"|([A-Za-z_]\w*))\s*:\s*\[([^\]]*)\]', m[1])
    return [(quoted or bare, re.findall(r'"([^"]*)"', body)) for quoted, bare, body in entries]


def test_the_dashboard_s_shipped_taxonomy_is_the_starter_taxonomy():
    """SHIPPED_TAXONOMY - the names the dashboard's category classes are written against, and what a rename can merge
    onto - is the starter taxonomy, category for category and subcategory for subcategory, in order."""
    parsed = shipped_taxonomy()
    assert len(parsed) >= 10 and parsed[0][1], "the parser read the literal"
    assert parsed == [(category, list(subs)) for category, subs in taxonomy().items()]


def test_every_bank_label_targets_the_starter_taxonomy():
    tax = taxonomy()
    labels = starter("bank_category_map.yaml")
    assert len(labels) >= 10
    for label, entry in labels.items():
        assert entry["category"] in tax, label
        assert entry.get("subcategory") is None or entry["subcategory"] in tax[entry["category"]], label


def test_the_thresholds_are_the_code_defaults():
    a = starter("assumptions.yaml")
    engine = TransferEngine([], {}, {}, {}, set())
    assert engine.window == a["match_window_bdays"]
    assert engine.venmo_window == a["venmo_match_window_bdays"]
    assert engine.venmo_threshold == a["venmo_review_threshold"] * 100
    assert engine.generic_min_cents == a["generic_min_amount"] * 100
    assert Categorizer({}, {}, {}, {}, {}).review_threshold == a["uncategorized_review_threshold"] * 100
    assert IncomeIdentifier({}, {}, set()).threshold == a["unknown_credit_review_threshold"] * 100
    assert a["coverage"] == {"min_share": 0.6}


def test_the_projections_are_the_export_defaults_in_their_order():
    """The starter writes out every projection key at the value the export gives a config that leaves it out."""
    projections = starter("assumptions.yaml")["projections"]
    assert list(projections) == list(PROJECTION_DEFAULTS)
    assert projections == {k: list(v) if isinstance(v, tuple) else v for k, v in PROJECTION_DEFAULTS.items()}
    assert export_projections(projections) == projections


def test_a_config_built_from_the_starter_files_loads_and_files_a_p2p_payment_as_unknown(tmp_path, monkeypatch):
    for name in PATH_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    d = tmp_path / "config"
    shutil.copytree(STARTER, d)
    write_household(d)
    account = {"name": "Sample Checking", "institution": "Sample Bank", "owner": "Alex", "kind": "checking"}
    (d / "accounts.yaml").write_text(yaml.safe_dump({"accounts": {"demo_checking_0000": account}}), "utf-8")
    cfg = Config(d)
    n = VendorNormalizer(cfg.vendor_rules)
    cat = Categorizer(cfg.category_rules, cfg.bank_category_map, cfg.vendor_rules, cfg.assumptions, {},
                      owner_of=cfg.owner_of())
    t = make_txn(desc="VENMO PAYMENT 1234567", account="demo_checking_0000", date="2030-01-15", cents=-30000)
    t.vendor, _ = n.normalize(t.description_raw)
    cat.apply([t], n.clean)
    assert (t.category, t.subcategory, t.category_source) == (FALLBACK_CATEGORY, P2P_UNKNOWN_SUB, "keyword")
    assert cat.first_hit(t, n.clean).set["subcategory"] == P2P_UNKNOWN_SUB
    assert t.needs_review                                   # $300 is above the starter's review threshold
    assert t.vendor == "Venmo Payment"                      # the reference number is cleaned away


# ------------------------------------------------------------------ transfer rules

# one invented account of each kind
KIND_ACCOUNTS = {f"demo_{kind.replace('-', '_')}_0000": {"name": f"Sample {kind}", "institution": "Sample Bank",
                                                         "owner": "Alex", "kind": kind} for kind in ACCOUNT_KINDS}
ACCOUNT_OF = {m["kind"]: a for a, m in KIND_ACCOUNTS.items()}
BANKS_AND_CARDS = {a for a, m in KIND_ACCOUNTS.items() if m["kind"] in CASHFLOW_KINDS}

# each starter rule: the rows it types and look-alike rows no starter rule types, as (account kind, descriptor, cents)
STARTER_RULES = {
    "card-payment-received": (
        [("credit-card", "PAYMENT THANK YOU-MOBILE", 25000), ("credit-card", "AUTOPAY PAYMENT - THANK YOU", 25000),
         ("credit-card", "ONLINE PAYMENT, THANK YOU", 25000), ("credit-card", "AUTOPAY PAYMENT", 25000)],
        [("credit-card", "SAMPLE PAYMENT SOLUTIONS REFUND", 2500), ("credit-card", "THANK YOU SAMPLE STORE", 2500),
         ("credit-card", "PAYMENT THANK YOU", -2500), ("checking", "PAYMENT THANK YOU", 2500)]),
    "card-payment-sent": (
        [("checking", "SAMPLE BANK CREDIT CRD EPAY 0001", -25000), ("checking", "SAMPLE CREDIT CARD PAYMENT", -25000),
         ("savings", "SAMPLE BANK CRCARDPMT 0001", -25000), ("checking", "SAMPLE CREDIT CRD AUTOPAY", -25000),
         ("checking", "SAMPLE CREDITCARD E-PAYMENT", -25000)],
        [("checking", "SAMPLE PAYMENT SOLUTIONS", -2500), ("checking", "SAMPLE GIFT CARD PAYMENT", -2500),
         ("checking", "SAMPLE CREDIT UNION LOAN PAYMENT", -2500), ("checking", "SAMPLE UTILITY E-PAYMENT", -2500),
         ("checking", "SAMPLE CREDIT CARD PAYMENT", 2500), ("credit-card", "SAMPLE CREDIT CARD PAYMENT", -2500)]),
    "transfer-with-savings": (
        [("checking", "ONLINE TRANSFER TO SAV ...0001", -5000), ("checking", "TRANSFER FROM SAVINGS 0001", 5000),
         ("savings", "Online Banking transfer to SAV 0002", -5000)],
        [("checking", "ONLINE TRANSFER TO SAMPLE LANDLORD", -5000), ("checking", "SAVINGS CATCHER SAMPLE STORE", -5000),
         ("checking", "TRANSFER TO SAVANNAH SAMPLE", -5000), ("credit-card", "TRANSFER TO SAVINGS", -5000)]),
    "transfer-with-checking": (
        [("savings", "ONLINE TRANSFER FROM CHK ...0001", 5000), ("checking", "TRANSFER TO CHECKING 0002", -5000)],
        [("checking", "TRANSFER TO CHECKPOINT SAMPLE", -5000), ("checking", "CHECKING ACCOUNT FEE", -500),
         ("brokerage", "TRANSFER FROM CHECKING", 5000)]),
}
RULE_CASES = [(name, *case, types) for name, (yes, no) in STARTER_RULES.items()
              for types, cases in ((True, yes), (False, no)) for case in cases]


@pytest.fixture(scope="module")
def starter_cfg(tmp_path_factory) -> Config:
    """A Config read from a copy of starter/ with household.yaml and one account of each kind."""
    d = tmp_path_factory.mktemp("starter") / "config"
    shutil.copytree(STARTER, d)
    write_household(d)
    (d / "accounts.yaml").write_text(yaml.safe_dump({"accounts": KIND_ACCOUNTS}), "utf-8")
    return Config(d)


def test_the_starter_transfer_rules_compile_against_one_account_of_each_kind_and_the_starter_taxonomy(starter_cfg):
    assert starter_cfg.taxonomy == taxonomy()
    rules = compile_transfer_rules(starter_cfg)
    assert [r.name for r in rules] == list(STARTER_RULES)
    for r in rules:
        assert r.accounts is None and r.kinds, r.name                               # scoped by kind only
        assert r.counter_hint in {"bank_any", *(f"kind:{k}" for k in ACCOUNT_KINDS)}, r.name
    assert all(yes and no for yes, no in STARTER_RULES.values())                    # a case each way per rule
    doc = starter("transfer_rules.yaml")
    assert doc["groups"] == {} and doc["outside"] == []
    assert set(doc["venmo_patterns"]) == {"debit", "credit", "zelle"}              # a bank's own keys stay optional


@pytest.mark.parametrize("name, kind, desc, cents, types", RULE_CASES)
def test_each_starter_rule_types_its_descriptors_and_no_rule_types_a_look_alike(starter_cfg, name, kind, desc, cents,
                                                                                types):
    eng = TransferEngine.from_config(starter_cfg, {}, BANKS_AND_CARDS)
    t = make_txn(desc=desc, cents=cents, account=ACCOUNT_OF[kind], date="2030-01-15")
    first = next((r for r in eng.rules if r.applies_to(t, kind) and r.pattern.search(desc)), None)
    eng.phase_a([t])
    if types:
        assert first is not None and first.name == name
        assert (t.type, t.is_internal, t.counter_hint) == (first.type, True, first.counter_hint)
    else:
        assert first is None and t.type == "purchase" and not t.is_internal


@pytest.mark.parametrize("desc, p2p", [
    ("VENMO PAYMENT 1234567", True), ("VENMO CASHOUT 1234567", True), ("CASH APP*SAMPLE ROBIN", True),
    ("PAYPAL INST XFER 1234567890", True), ("PAYPAL TRANSFER 1234567890", True), ("ZELLE PAYMENT TO SAMPLE ROBIN", True),
    ("VENMOUTH SUPPLY", False), ("CASHAPPLE FARMS", False), ("PAYPAL *SAMPLESHOP", False), ("ZELLEWOOD SAMPLE", False),
])
def test_the_starter_p2p_patterns_find_each_common_app_and_leave_look_alikes(starter_cfg, desc, p2p):
    eng = TransferEngine.from_config(starter_cfg, {}, BANKS_AND_CARDS)
    assert eng._is_venmoish(make_txn(desc=desc, cents=-5000, account=ACCOUNT_OF["checking"])) is p2p


@pytest.mark.parametrize("desc", ["VENMO CASHOUT 1234567", "CASH APP*CASH OUT", "PAYPAL TRANSFER 1234567890"])
def test_an_unpaired_starter_cash_out_credit_is_income_for_review(starter_cfg, desc):
    eng = TransferEngine.from_config(starter_cfg, {}, BANKS_AND_CARDS)
    t = make_txn(desc=desc, cents=5000, account=ACCOUNT_OF["checking"], date="2030-01-15")
    res = eng.phase_a([t])
    eng.phase_b([t], res)
    eng.phase_c([t], res)
    assert (t.type, t.category, t.needs_review) == ("income", INCOME_CATEGORY, True)


@pytest.mark.parametrize("raw, want", [
    ("SAMPLE STORE #1234 512-555-0100 01/31", "SAMPLE STORE"),
    ("ACME COFFEE (555) 555-0100", "ACME COFFEE"),
    ("ACME COFFEE 1-800-555-0199 01/31/30", "ACME COFFEE"),
    ("ACME COFFEE +18005550199", "ACME COFFEE"),
    ("ACME COFFEE STORE # 0042 12345", "ACME COFFEE STORE"),
    ("ACME COFFEE XXXXXXXXXXXX1234", "ACME COFFEE"),
    ("ACME COFFEE CARD 1234", "ACME COFFEE"),
    ("ACME COFFEE PPD ID: 1234567890", "ACME COFFEE"),
    ("ACME COFFEE CO ID: 1234567890", "ACME COFFEE"),
    ("ACME COFFEE DES:MONTHLY FEE", "ACME COFFEE"),
    ("ACME COFFEE INDN:ROBIN SAMPLE", "ACME COFFEE"),
    ("ACME COFFEE RECURRING", "ACME COFFEE"),
    ("SQ *ACME COFFEE", "ACME COFFEE"),
    ("TST* ACME COFFEE", "ACME COFFEE"),
    ("SP ACME COFFEE", "ACME COFFEE"),
    ("PAYPAL *ACME COFFEE", "ACME COFFEE"),
    ("PP*ACME COFFEE", "ACME COFFEE"),
    # ordinary vendor names keep their words and short numbers
    ("7-ELEVEN", "7-ELEVEN"),
    ("ACME 24 HOUR FITNESS", "ACME 24 HOUR FITNESS"),
    ("CARD FACTORY", "CARD FACTORY"),
    ("SPOTIFY", "SPOTIFY"),
    ("ACME COFFEE CO", "ACME COFFEE CO"),
])
def test_the_starter_cleanup_strips_store_numbers_phone_numbers_dates_and_metadata(raw, want):
    assert VendorNormalizer(starter("vendor_rules.yaml")).clean(raw) == want


def test_the_starter_acronyms_stay_upper_case_in_a_fallback_vendor():
    rules = starter("vendor_rules.yaml")
    n = VendorNormalizer(rules)
    assert rules["acronyms"]
    for word in rules["acronyms"]:
        assert n.normalize(f"SAMPLE {word}")[0] == f"Sample {word}"


# ------------------------------------------------ the configured cleanup and acronyms

def test_configured_acronyms_add_to_the_built_in_ones():
    assert VendorNormalizer({}).normalize("ACME ZQX ATM")[0] == "Acme Zqx ATM"
    assert VendorNormalizer({"acronyms": ["zqx"]}).normalize("ACME ZQX ATM")[0] == "Acme ZQX ATM"
    assert VendorNormalizer({"acronyms": []}).normalize("ACME ZQX ATM")[0] == "Acme Zqx ATM"


@pytest.mark.parametrize("bad", ["ZQX", [""], [3], {"ZQX": 1}])
def test_acronyms_must_be_a_list_of_words(bad):
    with pytest.raises(ValueError, match="acronyms"):
        VendorNormalizer({"acronyms": bad})


@pytest.mark.parametrize("raw", ["ACME COFFEE PPD ID: 1234567890", "ACME COFFEE CO ID: 1234567890",
                                 "ACME COFFEE DES:MONTHLY FEE", "ACME COFFEE INDN:ROBIN SAMPLE",
                                 "ACME COFFEE CKCD 5814", "ACME COFFEE transaction#: 123456",
                                 "ACME COFFEE RECURRING", "ACME COFFEE 01/31"])
def test_the_built_in_cleanup_leaves_bank_formats_to_the_config(raw):
    """A bank's own export format is configured (vendor_rules.yaml cleanup), never built in."""
    assert VendorNormalizer({}).clean(raw) == raw


def test_the_built_in_cleanup_squashes_whitespace_and_trims_separators_around_the_configured_cleanup():
    n = VendorNormalizer({"cleanup": [r"\bZQX\b"]})
    assert n.clean("  ACME \t ZQX  COFFEE -*# ") == "ACME COFFEE"
    assert VendorNormalizer({}).clean("  ACME \t ZQX  COFFEE -*# ") == "ACME ZQX COFFEE"


# The starter's cleanup list starts with bank export-format fragments, in a fixed order: each one is cleaned on its
# own, and after a trailing date that only its removal leaves at the end.
@pytest.mark.parametrize("raw, want", [
    ("ACME COFFEE PPD ID: 1234567890", "ACME COFFEE"),
    ("ACME COFFEE WEB ID: A123456789", "ACME COFFEE"),
    ("ACME COFFEE CCD ID:1234567890", "ACME COFFEE"),
    ("ACME COFFEE ARC ID: 1234567890", "ACME COFFEE"),
    ("ACME COFFEE TEL ID 1234567890", "ACME COFFEE"),
    ("ACME COFFEE CO ID: 1234567890", "ACME COFFEE"),
    ("ACME COFFEE DES:MONTHLY FEE", "ACME COFFEE"),
    ("ACME COFFEE INDN:ROBIN SAMPLE", "ACME COFFEE"),
    ("ACME COFFEE INDN:ROBIN SAMPLE CO ID:1234567890", "ACME COFFEE"),
    ("ACME COFFEE CKCD 5814", "ACME COFFEE"),
    ("ACME COFFEE RECURRING", "ACME COFFEE"),
    ("ACME COFFEE 01/31", "ACME COFFEE"),
    ("ACME COFFEE 01/31 PPD ID: 1234567890", "ACME COFFEE"),
    ("ACME COFFEE 01/31 CKCD 5814", "ACME COFFEE"),
])
def test_the_starter_cleanup_cleans_bank_format_fragments_alone_and_before_a_trailing_date(raw, want):
    assert VendorNormalizer(starter("vendor_rules.yaml")).clean(raw) == want

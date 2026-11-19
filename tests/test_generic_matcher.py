# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The generic pair matcher (`TransferEngine.phase_generic`): two rows nothing claimed or explained — exactly opposite
amounts, on two different accounts of the household, within the business-day window, of a kind pair the fixed matrix
allows, and each the other's only rival (the only unpaired row of the opposite amount on any other account in the
window) — are one transfer or one card payment. A row a `kinds:` rule typed that nothing paired goes back to what it
would have been without the rule, flagged for review — except a payment a card received, which stays internal.
Invented accounts, descriptors and dates only."""
from __future__ import annotations

import copy
import random
from types import SimpleNamespace

import pytest
import yaml
from conftest import ROOT, answer, isolated_pipeline, make_txn, write_household

from etl import exports
from etl.config import Config
from etl.enrich.categorize import UNCATEGORIZED_REASON, Categorizer
from etl.enrich.transfers import (
    DEFAULT_GENERIC_MIN_AMOUNT,
    GENERIC_PAIRS,
    UNPAIRED_TRANSFER,
    TransferEngine,
    compile_transfer_rules,
)
from etl.enrich.vendors import VendorNormalizer
from etl.models import (
    CASHFLOW_KINDS,
    FALLBACK_CATEGORY,
    INCOME_CATEGORY,
    P2P_UNKNOWN_SUB,
    UNCATEGORIZED_SUB,
)

ACCOUNTS = {
    "demo_checking_0000": {"name": "Sample Checking", "institution": "Sample Bank", "owner": "Alex",
                           "kind": "checking", "tags": ["demo-account"]},
    "demo_checking_0001": {"name": "Sample Checking 2", "institution": "Sample Bank", "owner": "Sam",
                           "kind": "checking"},
    "demo_savings_0000": {"name": "Sample Savings", "institution": "Sample Bank", "owner": "Joint",
                          "kind": "savings"},
    "demo_card_0000": {"name": "Sample Card", "institution": "Sample Bank", "owner": "Alex", "kind": "credit-card"},
    "demo_card_0001": {"name": "Sample Card 2", "institution": "Sample Bank", "owner": "Sam", "kind": "credit-card"},
    "demo_brokerage_0000": {"name": "Sample Brokerage", "institution": "Sample Broker", "owner": "Joint",
                            "kind": "brokerage"},
    "demo_loan_0000": {"name": "Sample Loan", "institution": "Sample Lender", "owner": "Joint", "kind": "loan"},
}
KINDS = {a: m["kind"] for a, m in ACCOUNTS.items()}
BANKS = {a for a, k in KINDS.items() if k in CASHFLOW_KINDS}
EVERY_WINDOW = {a: ("2030-01-01", "2030-12-31") for a in ACCOUNTS}
P2P = {"debit": r"\bSAMPLEPAY\s+PAYMENT\b", "credit": r"\bSAMPLEPAY\s+CASHOUT\b"}
MONDAY = "2030-03-04"


def tx(account, desc, cents, date=MONDAY, **fields):
    """A row as the categorizer's fallback leaves one nothing explained (the stages that run before the generic phase
    restamp it when they place it)."""
    fields.setdefault("category_source", "fallback")
    return make_txn(date=date, account=account, desc=desc, cents=cents, **fields)


def engine(*rules, generic=None, assumptions=None, venmo_patterns=None, split_vendors=(),
           windows=None) -> TransferEngine:
    doc: dict = {"rules": list(rules)}
    if generic is not None:
        doc["generic_matching"] = generic
    if venmo_patterns is not None:
        doc["venmo_patterns"] = venmo_patterns
    compiled = compile_transfer_rules(SimpleNamespace(transfer_rules=doc, accounts=ACCOUNTS))
    return TransferEngine(compiled, doc, assumptions or {}, EVERY_WINDOW if windows is None else windows, BANKS,
                          account_kinds=KINDS, split_vendors=split_vendors)


def run(eng: TransferEngine, rows: list):
    """The engine's phases in Pipeline.enrich order (the stages between them are the pipeline's)."""
    res = eng.phase_a(rows)
    eng.phase_b(rows, res)
    eng.release_unpaired(rows, res)
    eng.phase_generic(rows, res)
    eng.phase_c(rows, res)
    return res


def paired(a, b) -> bool:
    return a.transfer_group_id is not None and a.transfer_group_id == b.transfer_group_id


def untouched(rows, before) -> bool:
    return rows == before and all(t.transfer_group_id is None and not t.is_internal for t in rows)


def rule(name="demo-rule", pattern="^DEMO MOVE", type="transfer", **keys) -> dict:
    return {"name": name, "pattern": pattern, "type": type, **keys}


# ------------------------------------------------------------------ the matrix

CELLS = [
    ("demo_checking_0000", "demo_savings_0000", "transfer"),
    ("demo_savings_0000", "demo_checking_0000", "transfer"),
    ("demo_checking_0000", "demo_checking_0001", "transfer"),     # two accounts of one cash kind
    ("demo_checking_0000", "demo_card_0000", "cc-payment"),       # cash out, card in: a card payment
    ("demo_savings_0000", "demo_card_0001", "cc-payment"),
    ("demo_card_0000", "demo_checking_0000", None),               # card out, cash in: the wrong direction
    ("demo_card_0000", "demo_card_0001", None),                   # card to card
    ("demo_card_0000", "demo_savings_0000", None),
    ("demo_brokerage_0000", "demo_checking_0000", None),          # any other kind never pairs
    ("demo_checking_0000", "demo_brokerage_0000", None),
    ("demo_loan_0000", "demo_checking_0000", None),
    ("demo_checking_0000", "demo_loan_0000", None),
    ("demo_checking_0000", "demo_checking_0000", None),           # one account
]


def test_the_matrix_is_cash_to_cash_and_cash_to_card_only():
    assert dict(GENERIC_PAIRS) == {
        ("checking", "checking"): "transfer", ("checking", "savings"): "transfer",
        ("savings", "checking"): "transfer", ("savings", "savings"): "transfer",
        ("checking", "credit-card"): "cc-payment", ("savings", "credit-card"): "cc-payment"}


@pytest.mark.parametrize("out_account, in_account, typ", CELLS)
def test_each_matrix_cell(out_account, in_account, typ):
    out = tx(out_account, "DEMO OUT 0001", -12345)
    inn = tx(in_account, "DEMO IN 0001", 12345, date="2030-03-05")
    before = copy.deepcopy([out, inn])
    res = run(engine(), [out, inn])
    if typ is None:
        assert untouched([out, inn], before) and res.matches == []
        return
    assert (out.type, inn.type) == (typ, typ)
    assert out.is_internal and inn.is_internal and paired(out, inn)
    assert out.match_status == inn.match_status == "matched"
    assert out.transfer_group_id.startswith("TG-")
    assert res.matches == [(out.transfer_group_id, out.uid, inn.uid, "generic", 1)]


# ------------------------------------------------------------------ window and minimum

@pytest.mark.parametrize("in_date, window, pairs", [
    ("2030-03-07", None, True),       # Thursday: three business days after Monday, the default window
    ("2030-03-08", None, False),      # Friday: four
    ("2030-02-27", None, True),       # the inflow may come first: Wednesday before is three
    ("2030-02-26", None, False),
    ("2030-03-05", 1, True),          # a household's own window
    ("2030-03-06", 1, False),
])
def test_the_window_edge(in_date, window, pairs):
    out = tx("demo_checking_0000", "DEMO OUT 0001", -12345)
    inn = tx("demo_savings_0000", "DEMO IN 0001", 12345, date=in_date)
    run(engine(assumptions={} if window is None else {"match_window_bdays": window}), [out, inn])
    assert paired(out, inn) is pairs


@pytest.mark.parametrize("cents, minimum, pairs", [
    (1999, None, False),              # under the default minimum: never
    (2000, None, True),               # at it: pairs
    (4999, 50, False),
    (5000, 50, True),
    (1, 0, True),                     # a minimum of 0 lets every amount pair
])
def test_the_minimum_amount(cents, minimum, pairs):
    assert DEFAULT_GENERIC_MIN_AMOUNT == 20.0
    out = tx("demo_checking_0000", "DEMO OUT 0001", -cents)
    inn = tx("demo_card_0000", "DEMO IN 0001", cents)
    run(engine(assumptions={} if minimum is None else {"generic_min_amount": minimum}), [out, inn])
    assert paired(out, inn) is pairs


# ------------------------------------------------------------------ uniqueness

def test_two_inflow_candidates_for_one_outflow_pair_nothing():
    rows = [tx("demo_checking_0000", "DEMO OUT 0001", -12345),
            tx("demo_savings_0000", "DEMO IN 0001", 12345, date="2030-03-05"),
            tx("demo_card_0000", "DEMO IN 0002", 12345, date="2030-03-06")]
    before = copy.deepcopy(rows)
    res = run(engine(), rows)
    assert untouched(rows, before) and res.matches == []


def test_two_outflow_candidates_for_one_inflow_pair_nothing():
    rows = [tx("demo_checking_0000", "DEMO OUT 0001", -12345),
            tx("demo_savings_0000", "DEMO OUT 0002", -12345, date="2030-03-05"),
            tx("demo_card_0000", "DEMO IN 0001", 12345, date="2030-03-06")]
    before = copy.deepcopy(rows)
    res = run(engine(), rows)
    assert untouched(rows, before) and res.matches == []


def test_a_rival_on_one_side_only_still_blocks_the_pair_on_the_other():
    """The outflow's only counterpart is the inflow, but the inflow has two: neither side is unique, nothing pairs."""
    out = tx("demo_checking_0000", "DEMO OUT 0001", -12345)
    inn = tx("demo_card_0000", "DEMO IN 0001", 12345, date="2030-03-07")
    rival = tx("demo_savings_0000", "DEMO OUT 0002", -12345, date="2030-03-11")   # two days after the inflow
    run(engine(), [out, inn, rival])
    assert not paired(out, inn) and not paired(rival, inn)


def test_a_row_outside_the_window_or_already_paired_is_no_rival():
    rules = [rule(name="demo-out", kinds=["checking"], sign="debit"),
             rule(name="demo-in", pattern="^DEMO ARRIVED", kinds=["savings"], sign="credit")]
    out = tx("demo_checking_0000", "DEMO OUT 0001", -12345)
    inn = tx("demo_card_0000", "DEMO IN 0001", 12345)
    late = tx("demo_savings_0000", "DEMO IN 0002", 12345, date="2030-03-15")            # nine business days on
    pair_b = [tx("demo_checking_0001", "DEMO MOVE 0001", -12345), tx("demo_savings_0000", "DEMO ARRIVED", 12345)]
    run(engine(*rules), [out, inn, late, *pair_b])
    assert paired(out, inn) and out.type == "cc-payment"
    assert paired(*pair_b) and late.transfer_group_id is None


# each case: a row that cannot pair itself (or not with these two) but could be one of their other legs
RIVALS = {
    "a card outflow the matrix forbids": (dict(), ("demo_card_0001", "DEMO OUT 0002", -12345)),
    "a brokerage inflow": (dict(), ("demo_brokerage_0000", "DEMO IN 0002", 12345)),
    "a purchase a rule filed": (dict(), ("demo_savings_0000", "DEMO IN 0002", 12345, {"category_source": "keyword"})),
    "an income rule's credit": (dict(), ("demo_savings_0000", "DEMO IN 0002", 12345,
                                         {"type": "income", "category_source": "type_default"})),
    "a rule-typed leg left unpaired": (dict(rules=[rule(accounts=["demo_checking_0001"])]),
                                       ("demo_checking_0001", "DEMO MOVE 0002", -12345)),
    "a payment-app row": (dict(venmo_patterns=P2P), ("demo_checking_0001", "SAMPLEPAY PAYMENT 0002", -12345)),
}


@pytest.mark.parametrize("case", sorted(RIVALS))
def test_any_unpaired_row_with_the_opposite_amount_makes_the_pair_ambiguous(case):
    keys, (account, desc, cents, *fields) = RIVALS[case]
    out = tx("demo_checking_0000", "DEMO OUT 0001", -12345)
    inn = tx("demo_card_0000", "DEMO IN 0001", 12345)
    rival = tx(account, desc, cents, date="2030-03-05", **(fields[0] if fields else {}))
    run(engine(*keys.get("rules", ()), venmo_patterns=keys.get("venmo_patterns")), [out, inn, rival])
    assert not paired(out, inn)
    assert out.transfer_group_id is None and inn.transfer_group_id is None


@pytest.mark.parametrize("source", ["keyword", "vendor_rule", "learned", "bank_label", "parser", None])
@pytest.mark.parametrize("side", ["outflow", "inflow"])
def test_a_row_something_filed_is_never_a_candidate(source, side):
    """Only rows nothing explained pair: a purchase or a refund a rule, a learned rule, the bank's label or a parser
    filed is spending at something the household's vocabulary names."""
    out = tx("demo_checking_0000", "DEMO OUT 0001", -12345)
    inn = tx("demo_card_0000", "DEMO IN 0001", 12345)
    (out if side == "outflow" else inn).category_source = source
    run(engine(), [out, inn])
    assert not paired(out, inn)


def test_a_purchase_and_a_refund_at_a_named_store_are_not_a_card_payment(tmp_path):
    rows = [tx("demo_checking_0000", "SAMPLE STORE 0001", -4321), tx("demo_card_0000", "SAMPLE STORE REFUND", 4321)]
    enrich(tmp_path / "store", rows)
    assert [(t.type, t.category, t.category_source) for t in rows] == [("purchase", "Shopping", "keyword")] * 2
    assert not paired(*rows)
    rows = [tx("demo_checking_0000", "DEMO OUT 0001", -4321), tx("demo_card_0000", "DEMO IN 0001", 4321)]
    enrich(tmp_path / "plain", rows)                           # control: two rows nothing filed pair
    assert paired(*rows) and rows[0].type == "cc-payment"


# ------------------------------------------------------------------ who is a candidate

def test_a_rule_typed_row_is_never_a_candidate():
    rules = [rule(accounts=["demo_checking_0000"])]
    out = tx("demo_checking_0000", "DEMO MOVE 0001", -12345)
    inn = tx("demo_savings_0000", "DEMO IN 0001", 12345)
    res = run(engine(*rules), [out, inn])
    assert out.type == "transfer" and out.is_internal and out.match_status in ("unmatched_unexpected",
                                                                                "unmatched_window")
    assert inn.type == "purchase" and not inn.is_internal and res.matches == []
    out, inn = tx("demo_checking_0000", "DEMO MOVE 0001", -12345), tx("demo_savings_0000", "DEMO IN 0001", 12345)
    run(engine(), [out, inn])                                  # control: without the rule the two pair
    assert paired(out, inn)


def test_a_row_phase_b_paired_is_not_paired_again():
    rules = [rule(name="demo-out", kinds=["checking"], sign="debit"),
             rule(name="demo-in", pattern="^DEMO ARRIVED", kinds=["savings"], sign="credit")]
    out = tx("demo_checking_0000", "DEMO MOVE 0001", -12345)
    inn = tx("demo_savings_0000", "DEMO ARRIVED 0001", 12345)
    other = tx("demo_card_0000", "DEMO IN 0001", 12345)
    res = run(engine(*rules), [out, inn, other])
    assert paired(out, inn) and [m[3] for m in res.matches] == ["pair"]
    assert other.transfer_group_id is None and other.type == "purchase"


def test_a_p2p_row_is_never_a_candidate():
    out = tx("demo_checking_0000", "SAMPLEPAY PAYMENT 0001", -12345)
    inn = tx("demo_savings_0000", "DEMO IN 0001", 12345)
    run(engine(venmo_patterns=P2P), [out, inn])
    assert not paired(out, inn)
    assert (out.type, out.subcategory, inn.type) == ("purchase", P2P_UNKNOWN_SUB, "purchase")
    out, inn = tx("demo_checking_0000", "SAMPLEPAY PAYMENT 0001", -12345), tx("demo_savings_0000", "DEMO IN 0001", 12345)
    run(engine(), [out, inn])                                  # control: with no P2P patterns it is an ordinary row
    assert paired(out, inn)


def test_a_split_part_or_parent_is_never_a_candidate():
    parent = tx("demo_checking_0000", "DEMO OUT 0001", -12345, is_parent=True)
    part = tx("demo_checking_0000", "DEMO OUT 0002", -12345, parent_id=parent.uid, split_index=1)
    for row in (parent, part):
        rows = [row, tx("demo_savings_0000", "DEMO IN 0001", 12345)]
        before = copy.deepcopy(rows)
        run(engine(), rows)
        assert untouched(rows, before)


def test_a_row_of_a_split_rule_vendor_is_never_a_candidate():
    """A vendor with a split rule is a mixed-basket purchase by the household's own word: its rows are split later in
    the enrich order, so they are treated as split parents here."""
    out = tx("demo_checking_0000", "SAMPLE MART 0001", -12345, vendor="Sample Mart")
    inn = tx("demo_savings_0000", "DEMO IN 0001", 12345)
    before = copy.deepcopy([out, inn])
    run(engine(split_vendors={"sample mart"}), [out, inn])
    assert untouched([out, inn], before)
    run(engine(split_vendors={"another mart"}), [out, inn])
    assert paired(out, inn)


def test_an_income_typed_row_is_a_candidate_only_when_the_fallback_typed_it():
    rows = [tx("demo_checking_0000", "DEMO OUT 0001", -60000),
            tx("demo_savings_0000", "DEMO IN 0001", 60000, type="income", category=INCOME_CATEGORY,
               category_source="type_default")]
    run(engine(), rows)
    assert rows[0].transfer_group_id is None and rows[1].type == "income"
    rows = [tx("demo_checking_0000", "DEMO OUT 0001", -60000),
            tx("demo_savings_0000", "DEMO IN 0001", 60000, type="income", category=INCOME_CATEGORY,
               category_source="fallback")]
    run(engine(), rows)
    assert paired(*rows) and rows[1].type == "transfer"
    rows = [tx("demo_checking_0000", "DEMO OUT 0001", -60000),
            tx("demo_savings_0000", "DEMO IN 0001", 60000, type="interest", category=INCOME_CATEGORY,
               category_source="fallback")]
    run(engine(), rows)
    assert rows[0].transfer_group_id is None                   # interest, dividends and fees are never candidates


def test_generic_matching_false_pairs_nothing_but_still_flags_an_unpaired_kind_leg():
    out = tx("demo_checking_0000", "DEMO OUT 0001", -12345)
    inn = tx("demo_savings_0000", "DEMO IN 0001", 12345)
    leg = tx("demo_checking_0001", "DEMO MOVE 0001", -54321)
    res = run(engine(rule(kinds=["checking"]), generic=False), [out, inn, leg])
    assert not paired(out, inn) and res.matches == []
    assert (leg.type, leg.is_internal, leg.needs_review, leg.review_reason) == (
        "purchase", False, True, UNPAIRED_TRANSFER)


def test_the_result_does_not_depend_on_row_order():
    def rows():
        return [tx("demo_checking_0000", "DEMO OUT 0001", -12345),
                tx("demo_savings_0000", "DEMO IN 0001", 12345, date="2030-03-05"),
                tx("demo_checking_0001", "DEMO OUT 0002", -5000),
                tx("demo_card_0000", "DEMO IN 0002", 5000),
                tx("demo_card_0001", "DEMO IN 0003", 5000, date="2030-03-06"),       # a rival: 5000 pairs nothing
                tx("demo_savings_0000", "DEMO OUT 0003", -7777, date="2030-03-11"),
                tx("demo_checking_0000", "DEMO IN 0004", 7777, date="2030-03-12"),
                tx("demo_card_0000", "DEMO OUT 0004", -2500),                         # card out: never
                tx("demo_checking_0001", "DEMO IN 0005", 2500)]

    def outcome(order_seed):
        rs = rows()
        if order_seed is not None:
            random.Random(order_seed).shuffle(rs)
        res = run(engine(), rs)
        return sorted((t.uid, t.type, t.transfer_group_id) for t in rs), res.matches

    first = outcome(None)
    assert len(first[1]) == 2
    assert all(outcome(seed) == first for seed in range(8))


# ------------------------------------------------------------------ settings

def _config_dir(tmp_path, *, transfer_rules=None, assumptions=None, category_rules=None, vendor_rules=None,
                bank_category_map=None):
    d = tmp_path / "config"
    write_household(d)
    (d / "accounts.yaml").write_text(yaml.safe_dump({"accounts": ACCOUNTS}, sort_keys=False), "utf-8")
    for name, doc in (("transfer_rules.yaml", transfer_rules), ("assumptions.yaml", assumptions),
                      ("category_rules.yaml", category_rules), ("vendor_rules.yaml", vendor_rules),
                      ("bank_category_map.yaml", bank_category_map)):
        if doc is not None:
            (d / name).write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")
    return d


@pytest.mark.parametrize("value", ["yes please", 1, 0, [True], {"on": True}])
def test_generic_matching_must_be_true_or_false(tmp_path, value):
    d = _config_dir(tmp_path, transfer_rules={"rules": [], "generic_matching": value})
    with pytest.raises(ValueError, match=r"^transfer_rules\.yaml: generic_matching: expected true or false"):
        Config(d)


@pytest.mark.parametrize("value, on", [(True, True), (False, False), (None, True)])
def test_generic_matching_reads_true_false_and_absent(tmp_path, value, on):
    doc = {"rules": []} if value is None else {"rules": [], "generic_matching": value}
    cfg = Config(_config_dir(tmp_path, transfer_rules=doc))
    assert TransferEngine.from_config(cfg, EVERY_WINDOW, BANKS).generic_matching is on


@pytest.mark.parametrize("value", ["twenty", -1, -0.01, True, float("nan"), float("inf"), [20]])
def test_generic_min_amount_must_be_a_number_of_dollars_zero_or_more(tmp_path, value):
    d = _config_dir(tmp_path, assumptions={"generic_min_amount": value})
    with pytest.raises(ValueError, match=r"^assumptions\.yaml: generic_min_amount: expected a number of dollars, "
                                         r"0 or more"):
        Config(d)


@pytest.mark.parametrize("value, cents", [(None, 2000), (0, 0), (12.5, 1250), (100, 10000)])
def test_generic_min_amount_reads_dollars(tmp_path, value, cents):
    cfg = Config(_config_dir(tmp_path, assumptions={} if value is None else {"generic_min_amount": value}))
    assert TransferEngine.from_config(cfg, EVERY_WINDOW, BANKS).generic_min_cents == cents


def test_the_starter_turns_the_matcher_on():
    doc = yaml.safe_load((ROOT / "starter" / "transfer_rules.yaml").read_text("utf-8"))
    assert doc["generic_matching"] is True


# ------------------------------------------------------------------ through the enrich order

TAXONOMY = {"Cash & Misc": ["Uncategorized", "Venmo/Zelle Unknown"], "Internal": [], "Income": ["Other", "Salary - Alex"],
            "Fees & Interest": [], "Shopping": ["Sample Store"]}
CATEGORY_RULES = {
    "taxonomy": TAXONOMY,
    "keyword_rules": [{"pattern": "SAMPLE STORE", "category": "Shopping", "subcategory": "Sample Store",
                       "tags": ["demo-tag"]}],
    "income_rules": [{"pattern": "SAMPLE EMPLOYER PAYROLL", "subcategory": "Salary - Alex", "earner": "Alex",
                      "employer": "Sample Employer"}],
}


def enrich(tmp_path, rows, *, rules=(), generic=None, venmo_patterns=None, answers=(), overrides=None,
           vendor_rules=None, bank_category_map=None):
    """Pipeline.enrich over `rows` with a synthetic config: the accounts above, the categories above and `rules`."""
    doc: dict = {"rules": list(rules)}
    if generic is not None:
        doc["generic_matching"] = generic
    if venmo_patterns is not None:
        doc["venmo_patterns"] = venmo_patterns
    d = _config_dir(tmp_path / "config-dir", transfer_rules=doc, category_rules=CATEGORY_RULES,
                    vendor_rules=vendor_rules, bank_category_map=bank_category_map)
    if overrides is not None:
        (d / "overrides.yaml").write_text(yaml.safe_dump(overrides, sort_keys=False), "utf-8")
    p = isolated_pipeline(tmp_path / "state", rows, cfg=Config(d), windows=EVERY_WINDOW, answers=answers)
    p.enrich()
    return p


def exported(p, uid) -> dict:
    names = {a: m["name"] for a, m in p.cfg.accounts.items()}
    rows = exports.export_transactions(p.txns, p.cfg.owner_of(), names)["transactions"]
    return next(r for r in rows if r["id"] == uid)


# each case: the kind rule (with a hint, which the undo must take away too), the row it types, and what the row is
# without that rule
UNPAIRED_LEGS = {
    "an outflow a keyword rule files": (
        rule(name="demo-card-paid", pattern="^DEMO CARD PAYMENT", type="cc-payment", kinds=["checking"],
             sign="debit", counter_hint="kind:credit-card"),
        ("demo_checking_0000", "DEMO CARD PAYMENT SAMPLE STORE", -4321), ("purchase", "Shopping", "keyword")),
    "an outflow nothing files, large enough for review": (
        rule(name="demo-card-paid", pattern="^DEMO CARD PAYMENT", type="cc-payment", kinds=["checking"],
             sign="debit", counter_hint="kind:credit-card"),
        ("demo_checking_0000", "DEMO CARD PAYMENT 0001", -30000), ("purchase", FALLBACK_CATEGORY, "fallback")),
    "an inflow an income rule types": (
        rule(name="demo-from-savings", pattern="^DEMO TRANSFER FROM SAVINGS", kinds=["checking"],
             counter_hint="kind:savings"),
        ("demo_checking_0000", "DEMO TRANSFER FROM SAVINGS SAMPLE EMPLOYER PAYROLL", 250000),
        ("income", INCOME_CATEGORY, "type_default")),
    "an inflow the income fallback types": (
        rule(name="demo-from-savings", pattern="^DEMO TRANSFER FROM SAVINGS", kinds=["checking"],
             counter_hint="kind:savings"),
        ("demo_checking_0000", "DEMO TRANSFER FROM SAVINGS 0001", 70000), ("income", INCOME_CATEGORY, "fallback")),
    "a small inflow that stays a purchase": (
        rule(name="demo-from-savings", pattern="^DEMO TRANSFER FROM SAVINGS", kinds=["checking"],
             counter_hint="kind:savings"),
        ("demo_checking_0000", "DEMO TRANSFER FROM SAVINGS 0002", 4000),
        ("purchase", FALLBACK_CATEGORY, "fallback")),
    # a card credit a cc-payment rule types is never released (test_an_unpaired_payment_received_on_a_card_...)
    "a card credit a transfer rule types": (
        rule(name="demo-card-move", pattern="^DEMO CARD MOVE", type="transfer", kinds=["credit-card"],
             sign="credit", counter_hint="bank_any"),
        ("demo_card_0000", "DEMO CARD MOVE 0001", 4000), ("purchase", FALLBACK_CATEGORY, "fallback")),
    "a card debit a cc-payment rule types": (
        rule(name="demo-card-debit", pattern="^DEMO CARD DEBIT", type="cc-payment", kinds=["credit-card"],
             sign="debit", counter_hint="bank_any"),
        ("demo_card_0000", "DEMO CARD DEBIT 0001", -4000), ("purchase", FALLBACK_CATEGORY, "fallback")),
    # a credit a cc-payment rule types is a payment received only on a card: on a bank account it is released
    "a bank-account credit a cc-payment rule types": (
        rule(name="demo-card-credit", pattern="^DEMO CARD CREDIT", type="cc-payment", kinds=["checking"],
             sign="credit", counter_hint="kind:credit-card"),
        ("demo_checking_0000", "DEMO CARD CREDIT 0001", 4000), ("purchase", FALLBACK_CATEGORY, "fallback")),
    "a payment-app row": (
        rule(name="demo-p2p-move", pattern="^SAMPLEPAY PAYMENT", kinds=["checking"], counter_hint="kind:checking"),
        ("demo_checking_0000", "SAMPLEPAY PAYMENT 0001", -30000), ("purchase", FALLBACK_CATEGORY, "fallback")),
}


@pytest.mark.parametrize("case", sorted(UNPAIRED_LEGS))
def test_an_unpaired_kind_rule_leg_is_exactly_the_row_without_the_rule_plus_a_review_flag(tmp_path, case):
    kind_rule, (account, desc, cents), (typ, category, source) = UNPAIRED_LEGS[case]
    noise = ("demo_savings_0000", "SAMPLE STORE 0009", -1234)       # an unrelated row in both runs

    def run_once(name, rules):
        rows = [tx(account, desc, cents), tx(*noise)]
        p = enrich(tmp_path / name, rows, rules=rules, venmo_patterns=P2P)
        return p, rows[0]

    with_rule, row = run_once("with", [kind_rule])
    without_rule, plain = run_once("without", [])
    a, b = exported(with_rule, row.uid), exported(without_rule, plain.uid)
    assert set(a) == set(b)
    assert {k: v for k, v in a.items() if k != "needs_review"} == {k: v for k, v in b.items() if k != "needs_review"}
    assert (b["type"], b["category"], b["category_source"]) == (typ, category, source)
    assert a["needs_review"] is True and row.review_reason == UNPAIRED_TRANSFER
    # without the rule, the only reason is the categorizer's own, given when its fallback flagged a purchase (the
    # income fallback flags a deposit with none)
    assert plain.review_reason == (UNCATEGORIZED_REASON if b["needs_review"] and b["type"] == "purchase" else None)
    assert not row.is_internal and row.counter_hint is None and row.match_status is None
    assert with_rule.income_phases == without_rule.income_phases        # the employer label the undo restores
    assert bool(with_rule.income_phases) is (case == "an inflow an income rule types")


CARD_THANKS = rule(name="demo-card-thanks", pattern="^DEMO THANK YOU", type="cc-payment", kinds=["credit-card"],
                   sign="credit", counter_hint="bank_any")


def _month_spend(p) -> float:
    cashflow_ids = [a for a, m in p.cfg.accounts.items() if m["kind"] in CASHFLOW_KINDS]
    [month] = exports.export_cashflow_monthly(p.txns, p.windows, cashflow_ids,
                                              min_share=p.cfg.coverage_min_share)["months"]
    return month["spend"]


def test_an_unpaired_payment_received_on_a_card_stays_internal_and_never_reduces_spending(tmp_path):
    """A household that tracks a card but not the bank account that pays it: the payment the card received finds no
    partner, yet it is never a refund — it stays a card payment, out of spending, and phase C names the data gap."""
    def rows():
        return [tx("demo_card_0000", "DEMO THANK YOU 0001", 40000), tx("demo_card_0000", "SAMPLE STORE 0001", -2500)]

    paid = rows()
    p = enrich(tmp_path / "with", paid, rules=[CARD_THANKS])
    payment = paid[0]
    assert (payment.type, payment.is_internal, payment.category, payment.category_source) == (
        "cc-payment", True, "Internal", "internal")
    assert payment.match_status == "unmatched_unexpected"            # the bank accounts' data covers its date
    assert not payment.needs_review and payment.review_reason is None and payment.transfer_group_id is None
    assert [q.topic for q in p.questions] == ["unmatched-internal"]
    assert _month_spend(p) == 25.0                                    # the purchase alone
    plain = rows()
    assert _month_spend(enrich(tmp_path / "without", plain)) == 25.0 - 400.0   # without the rule it would net out


def test_an_unpaired_payment_received_on_a_card_is_a_data_gap_only_where_a_bank_account_covers_its_date(tmp_path):
    payment = tx("demo_card_0000", "DEMO THANK YOU 0001", 40000)
    res = run(engine(CARD_THANKS), [payment])
    assert (payment.type, payment.is_internal, payment.match_status) == ("cc-payment", True, "unmatched_unexpected")
    assert res.released == set() and [q.topic for q in res.open_questions] == ["unmatched-internal"]
    lone = tx("demo_card_0000", "DEMO THANK YOU 0002", 40000)
    res = run(engine(CARD_THANKS, windows={}), [lone])                # no account's data covers the date
    assert (lone.type, lone.is_internal, lone.match_status) == ("cc-payment", True, "unmatched_window")
    assert res.released == set() and res.open_questions == []


PAYMENT_RECEIVED = ("demo_card_0000", "DEMO THANK YOU 0001", 40000, "2030-03-05")


def test_a_payment_received_phase_b_left_unpaired_pairs_generically_with_its_one_cash_outflow(tmp_path):
    """A transfer rule recognised the credit, so the generic phase may pair it — as the card inflow of a cash-out,
    card-in pair — with an ordinary candidate outflow, when each is the other's only rival."""
    out = tx("demo_checking_0000", "DEMO OUT 0001", -40000)
    credit = tx(*PAYMENT_RECEIVED[:3], date=PAYMENT_RECEIVED[3])
    res = run(engine(CARD_THANKS), [out, credit])
    assert paired(out, credit) and (out.type, credit.type) == ("cc-payment", "cc-payment")
    assert out.is_internal and credit.is_internal and out.match_status == credit.match_status == "matched"
    assert res.matches == [(out.transfer_group_id, out.uid, credit.uid, "generic", 1)]
    assert res.released == set() and not out.needs_review and out.review_reason is None
    # through the enrich order: both rows are out of the month's spending
    rows = [tx("demo_checking_0000", "DEMO OUT 0001", -40000), tx(*PAYMENT_RECEIVED[:3], date=PAYMENT_RECEIVED[3])]
    p = enrich(tmp_path, rows, rules=[CARD_THANKS])
    assert paired(*rows) and [m[3] for m in p.transfer_matches] == ["generic"]
    assert _month_spend(p) == 0.0


def test_a_payment_received_with_two_possible_cash_outflows_stays_internal_and_unpaired():
    out = tx("demo_checking_0000", "DEMO OUT 0001", -40000)
    rival = tx("demo_savings_0000", "DEMO OUT 0002", -40000, date="2030-03-06")
    credit = tx(*PAYMENT_RECEIVED[:3], date=PAYMENT_RECEIVED[3])
    res = run(engine(CARD_THANKS), [out, rival, credit])
    assert res.matches == [] and credit.transfer_group_id is None
    assert (credit.type, credit.is_internal, credit.match_status) == ("cc-payment", True, "unmatched_unexpected")
    for t in (out, rival):
        assert (t.type, t.is_internal, t.transfer_group_id, t.category_source) == ("purchase", False, None, "fallback")


@pytest.mark.parametrize("outflow", [
    ("demo_card_0001", "DEMO OUT 0001", -40000, {}),                                   # card out: no matrix cell
    ("demo_checking_0000", "SAMPLE STORE 0001", -40000, {"category_source": "keyword"}),  # something filed it
], ids=["a-card-outflow", "a-categorised-outflow"])
def test_a_payment_received_never_pairs_with_a_card_or_a_categorised_outflow(outflow):
    account, desc, cents, fields = outflow
    out = tx(account, desc, cents, **fields)
    credit = tx(*PAYMENT_RECEIVED[:3], date=PAYMENT_RECEIVED[3])
    res = run(engine(CARD_THANKS), [out, credit])
    assert res.matches == [] and not paired(out, credit)
    assert (credit.type, credit.is_internal) == ("cc-payment", True) and not out.is_internal


def test_with_generic_matching_off_a_payment_received_stays_internal_and_unpaired():
    out = tx("demo_checking_0000", "DEMO OUT 0001", -40000)
    credit = tx(*PAYMENT_RECEIVED[:3], date=PAYMENT_RECEIVED[3])
    res = run(engine(CARD_THANKS, generic=False), [out, credit])
    assert res.matches == [] and credit.transfer_group_id is None
    assert (credit.type, credit.is_internal, credit.match_status) == ("cc-payment", True, "unmatched_unexpected")
    assert (out.type, out.is_internal) == ("purchase", False)


def test_an_accounts_rule_payment_received_is_no_generic_candidate():
    """Only a `kinds:` rule's credit is the exception: an `accounts:` rule's row is the rule's own and never a generic
    candidate."""
    by_account = rule(name="demo-card-thanks", pattern="^DEMO THANK YOU", type="cc-payment",
                      accounts=["demo_card_0000"], sign="credit", counter_hint="bank_any")
    out = tx("demo_checking_0000", "DEMO OUT 0001", -40000)
    credit = tx(*PAYMENT_RECEIVED[:3], date=PAYMENT_RECEIVED[3])
    res = run(engine(by_account), [out, credit])
    assert res.matches == [] and credit.is_internal and not out.is_internal


def test_the_card_payment_s_bank_side_with_no_card_in_the_data_is_still_released_and_flagged():
    sent = rule(name="demo-card-paid", pattern="^DEMO CARD PAYMENT", type="cc-payment", kinds=["checking"],
                sign="debit", counter_hint="kind:credit-card")
    leg = tx("demo_checking_0000", "DEMO CARD PAYMENT 0001", -40000)
    banks_only = {a: w for a, w in EVERY_WINDOW.items() if KINDS[a] != "credit-card"}
    res = run(engine(sent, windows=banks_only), [leg])
    assert leg.uid in res.released
    assert (leg.type, leg.is_internal, leg.needs_review, leg.review_reason) == (
        "purchase", False, True, UNPAIRED_TRANSFER)


def test_an_income_rule_credit_is_never_a_candidate_and_a_fallback_credit_is(tmp_path):
    def pair(name, desc, cents):
        rows = [tx("demo_checking_0000", "DEMO OUT 0001", -cents),
                tx("demo_savings_0000", desc, cents, date="2030-03-05")]
        enrich(tmp_path / name, rows)
        return rows

    out, inn = pair("rule", "SAMPLE EMPLOYER PAYROLL 0001", 250000)
    assert (inn.type, inn.earner, out.type) == ("income", "Alex", "purchase") and not paired(out, inn)
    out, inn = pair("small-rule", "SAMPLE EMPLOYER PAYROLL 0002", 4000)    # below the review threshold: still never
    assert inn.type == "income" and not paired(out, inn)
    out, inn = pair("fallback", "DEMO DEPOSIT 0001", 250000)               # an unexplained credit is a candidate
    assert paired(out, inn) and (out.type, inn.type) == ("transfer", "transfer")
    assert inn.category_source == "internal" and inn.earner is None
    out, inn = pair("small", "DEMO DEPOSIT 0002", 4000)                    # so is a small one left a purchase
    assert paired(out, inn)


def test_a_kind_rule_leg_phase_b_leaves_unpaired_may_pair_generically_and_is_then_not_flagged(tmp_path):
    rows = [tx("demo_checking_0000", "DEMO CARD PAYMENT 0001", -12345),
            tx("demo_card_0000", "DEMO PAYMENT RECEIVED 0001", 12345, date="2030-03-05")]
    p = enrich(tmp_path, rows, rules=[rule(name="demo-card-paid", pattern="^DEMO CARD PAYMENT", type="cc-payment",
                                           kinds=["checking"], sign="debit")])
    out, inn = rows
    assert paired(out, inn) and (out.type, inn.type) == ("cc-payment", "cc-payment")
    assert not out.needs_review and out.review_reason is None
    assert [m[3] for m in p.transfer_matches] == ["generic"]


def test_a_kind_rule_pair_is_typed_by_its_rules_and_never_flagged(tmp_path):
    rules = [rule(name="demo-out", pattern="^DEMO TRANSFER TO SAVINGS", kinds=["checking"], sign="debit"),
             rule(name="demo-in", pattern="^DEMO TRANSFER FROM CHECKING", kinds=["savings"], sign="credit")]
    rows = [tx("demo_checking_0000", "DEMO TRANSFER TO SAVINGS", -50000),
            tx("demo_savings_0000", "DEMO TRANSFER FROM CHECKING", 50000)]
    p = enrich(tmp_path, rows, rules=rules)
    assert paired(*rows) and all(t.type == "transfer" and not t.needs_review and t.review_reason is None
                                 for t in rows)
    assert [m[3] for m in p.transfer_matches] == ["pair"]


@pytest.mark.parametrize("generic", [None, False])
def test_an_accounts_rule_leg_nothing_pairs_stays_internal_as_before(tmp_path, generic):
    rows = [tx("demo_checking_0000", "DEMO MOVE 0001", -12345),
            tx("demo_savings_0000", "DEMO IN 0001", 12345)]
    enrich(tmp_path, rows, rules=[rule(accounts=["demo_checking_0000"], counter_hint="demo_brokerage_0000")],
           generic=generic)
    leg, other = rows
    assert (leg.type, leg.is_internal, leg.category, leg.category_source) == ("transfer", True, "Internal",
                                                                               "internal")
    assert leg.match_status == "unmatched_unexpected" and not leg.needs_review and leg.review_reason is None
    assert other.type == "purchase" and other.category == FALLBACK_CATEGORY and other.subcategory == UNCATEGORIZED_SUB


SHOP = {"category": "Shopping", "subcategory": "Sample Store"}
ANSWERED_AT = "2030-03-10T10:00:00Z"


def _answer_for(scope, row):
    """A review-inbox answer filing `row` (a vendor answer names its vendor in another case: the keys are
    compared lower-cased, as answers.apply compares them)."""
    return answer("txn", row, SHOP, ANSWERED_AT) if scope == "txn" else answer("vendor", "SAMPLE VENDOR", SHOP,
                                                                                 ANSWERED_AT)


# A vendor answer also becomes a learned rule, which files the vendor's rows before the matcher runs; with the vendor
# kept out of learning only the answer itself names the row, so these runs test the matcher's own reading of it.
NOT_LEARNED = {"learning": {"exclude": ["Sample Vendor"]}}


@pytest.mark.parametrize("scope", ["txn", "vendor"])
@pytest.mark.parametrize("side", [0, 1])
def test_a_row_the_family_answered_is_never_a_candidate(tmp_path, scope, side):
    """Answers apply after the pairing phases, so the matcher reads them first: the answered row is the family's to
    file. The answer applies, nothing pairs, and the other row keeps what it was."""
    rows = [tx("demo_checking_0000", "DEMO OUT 0001", -12345), tx("demo_savings_0000", "DEMO IN 0001", 12345)]
    rows[side].vendor = "Sample Vendor"
    p = enrich(tmp_path, rows, answers=[_answer_for(scope, rows[side])], vendor_rules=NOT_LEARNED)
    mine, other = rows[side], rows[1 - side]
    assert not paired(*rows) and p.transfer_matches == []
    assert (mine.type, mine.category, mine.subcategory, mine.category_source) == (
        "purchase", "Shopping", "Sample Store", "answer")
    assert (other.type, other.category, other.category_source) == ("purchase", FALLBACK_CATEGORY, "fallback")
    assert not any(c.name == "answers.expired" for c in p.checks)


@pytest.mark.parametrize("scope", ["txn", "vendor"])
def test_an_answered_row_still_makes_a_pair_ambiguous(tmp_path, scope):
    out = tx("demo_checking_0000", "DEMO OUT 0001", -12345)
    inn = tx("demo_savings_0000", "DEMO IN 0001", 12345)
    rival = tx("demo_checking_0001", "DEMO IN 0002", 12345, date="2030-03-05", vendor="Sample Vendor")
    enrich(tmp_path, [out, inn, rival], answers=[_answer_for(scope, rival)], vendor_rules=NOT_LEARNED)
    assert not paired(out, inn) and rival.transfer_group_id is None
    assert (rival.category, rival.category_source) == ("Shopping", "answer")


# each case: how the household's vocabulary names a large credit (its descriptor, its bank label, the extra config)
NAMED_CREDITS = {
    "a keyword rule": ("SAMPLE STORE REFUND 0001", None, {}),
    "a vendor rule": ("SAMPLEMART REFUND 0001", None, {"vendor_rules": {"patterns": [
        {"pattern": "SAMPLEMART", "vendor": "Sample Mart", "category": "Shopping", "subcategory": "Sample Store"}]}}),
    "the bank's label": ("DEMO CREDIT 0001", "Sample Label", {"bank_category_map": {
        "Sample Label": {"category": "Shopping"}}}),
    "nothing (the control)": ("DEMO DEPOSIT 0003", None, {}),
}


@pytest.mark.parametrize("case", sorted(NAMED_CREDITS))
def test_a_large_credit_the_household_s_vocabulary_names_is_never_a_candidate(tmp_path, case):
    """A credit at or above the review threshold is typed income by the income fallback before the categorizer runs,
    so no category rule saw it; it is a candidate only when the categorizer would leave it to its fallback too."""
    desc, label, extra = NAMED_CREDITS[case]
    out = tx("demo_checking_0000", "DEMO OUT 0001", -60000)
    inn = tx("demo_savings_0000", desc, 60000, date="2030-03-05", bank_category=label)
    enrich(tmp_path, [out, inn], **extra)
    if case.startswith("nothing"):
        assert paired(out, inn) and (out.type, inn.type) == ("transfer", "transfer")
        return
    assert not paired(out, inn)
    assert (inn.type, inn.category, inn.category_source) == ("income", INCOME_CATEGORY, "fallback")
    assert (out.type, out.category_source) == ("purchase", "fallback")


def test_places_reads_a_row_as_an_ordinary_purchase_and_changes_nothing():
    rules = {**CATEGORY_RULES, "keyword_rules": [
        *CATEGORY_RULES["keyword_rules"],
        {"name": "demo-typed", "stage": "default", "set": {"category": "Shopping"},
         "all": [{"field": "type", "op": "is", "value": "purchase"},
                 {"field": "description", "op": "contains", "value": "SAMPLE TYPED"}]}]}
    cat = Categorizer(rules, {}, {}, {}, {})
    clean = VendorNormalizer({}).clean
    for desc, places in (("SAMPLE STORE REFUND", True), ("SAMPLE TYPED REFUND", True), ("DEMO DEPOSIT", False)):
        row = tx("demo_savings_0000", desc, 60000, type="income", category=INCOME_CATEGORY,
                 subcategory="Other", needs_review=True)
        before = copy.deepcopy(row)
        assert cat.places(row, clean) is places, desc
        assert row == before and row.tags == before.tags


def test_a_generic_pair_is_internal_for_the_later_stages(tmp_path):
    """The resync files both rows under Internal, and an override can still retype one."""
    def rows():
        return [tx("demo_checking_0000", "DEMO OUT 0001", -12345), tx("demo_savings_0000", "DEMO IN 0001", 12345)]

    plain = rows()
    enrich(tmp_path / "plain", plain)
    assert [(t.category, t.subcategory, t.category_source) for t in plain] == [("Internal", "transfer", "internal")] * 2
    overridden = rows()
    enrich(tmp_path / "overridden", overridden,
           overrides=[{"match": {"uid": overridden[0].uid}, "set": {"type": "purchase", **SHOP}}])
    assert (overridden[0].type, overridden[0].is_internal, overridden[0].category) == ("purchase", False, "Shopping")
    assert overridden[1].type == "transfer"

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""transfer_rules.yaml as a validated model: each rule scoped by `accounts:` or by `kinds:`, counter hints that name an
account, a `groups:` entry, `kind:<kind>`, `bank_any`, an `outside:` name or a `from_text:` template, the two
optional person-to-person keys a bank's own format needs, and `venmo_patterns.pairing` (a pair with a person-to-person
leg is claimed only when unique, or the nearest one). Every problem is refused at load with the rule it names.
Invented accounts and descriptors only."""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
import yaml
from conftest import make_txn, write_household

from etl.config import Config
from etl.enrich.transfers import (
    RULE_TYPES,
    Rule,
    TransferEngine,
    compile_transfer_rules,
    transfer_rule_problems,
)
from etl.models import (
    CASHFLOW_KINDS,
    FALLBACK_CATEGORY,
    INCOME_CATEGORY,
    INTERNAL_TYPES,
    P2P_UNKNOWN_SUB,
)

ACCOUNTS = {
    "demo_checking_0000": {"name": "Sample Checking", "institution": "Sample Bank", "owner": "Alex",
                           "kind": "checking"},
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
P2P = {"debit": r"\bSAMPLEPAY\s+PAYMENT\b", "credit": r"\bSAMPLEPAY\s+CASHOUT\b", "zelle": r"\bZELLE\b"}


def doc(*rules, groups=None, outside=None, venmo_patterns=None) -> dict:
    d: dict = {"rules": list(rules)}
    if groups is not None:
        d["groups"] = groups
    if outside is not None:
        d["outside"] = outside
    if venmo_patterns is not None:
        d["venmo_patterns"] = venmo_patterns
    return d


def compiled(d: dict, accounts: dict = ACCOUNTS) -> list[Rule]:
    return compile_transfer_rules(SimpleNamespace(transfer_rules=d, accounts=accounts))


def engine(d: dict, windows: dict | None = None, household: dict | None = None) -> TransferEngine:
    return TransferEngine(compiled(d), d, {}, EVERY_WINDOW if windows is None else windows, BANKS,
                          account_kinds=KINDS, household=household)


def run(eng: TransferEngine, rows: list):
    res = eng.phase_a(rows)
    eng.phase_b(rows, res)
    eng.phase_c(rows, res)
    return res


def tx(account, desc, cents, date="2030-03-04", **fields):
    return make_txn(date=date, account=account, desc=desc, cents=cents, **fields)


def rule(name="demo-rule", pattern="^DEMO MOVE", type="transfer", **keys) -> dict:
    r = {"name": name, "pattern": pattern, "type": type}
    if "accounts" not in keys and "kinds" not in keys:
        keys["accounts"] = ["demo_checking_0000"]
    return {**r, **keys}


def _re_error(pattern: str) -> str:
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return str(e)
    raise AssertionError(f"{pattern!r} compiles")


# ------------------------------------------------------------------ load errors

FIX_REGEX = "correct the pattern (a Python regular expression, matched with case ignored)"
HINT_FORMS = "is not an account, a group, kind:<kind>, bank_any, an outside name or from_text:<template>"
FIX_GROUP = "list the group's account ids under accounts: and say true or false for expect_counterpart"

LOAD_ERRORS = [
    (doc(rule(pattern="DEMO (MOVE")),
     f"transfer_rules.yaml: rules[0] (demo-rule): pattern is not a valid regular expression "
     f"({_re_error('DEMO (MOVE')}) — {FIX_REGEX}"),
    (doc(rule(pattern="")),                          # an empty pattern would type every row in scope
     "transfer_rules.yaml: rules[0] (demo-rule): pattern is missing — write a regular expression the descriptor is "
     "matched against"),
    (doc(rule(type="payment")),
     "transfer_rules.yaml: rules[0] (demo-rule): unknown type 'payment' — one of transfer, cc-payment, investment"),
    (doc(rule(accounts=["demo_checking_0000", "demo_missing_9999"])),
     "transfer_rules.yaml: rules[0] (demo-rule): unknown account 'demo_missing_9999' in accounts — "
     "use an account id from accounts.yaml"),
    (doc(rule(kinds=["checking", "current"])),
     "transfer_rules.yaml: rules[0] (demo-rule): unknown kind 'current' in kinds — "
     "one of checking, savings, credit-card, brokerage, loan"),
    (doc({"name": "demo-rule", "pattern": "^DEMO MOVE", "type": "transfer"}),
     "transfer_rules.yaml: rules[0] (demo-rule): give exactly one of accounts or kinds — scope the rule by account "
     "ids (accounts: [...]) or by account kinds (kinds: [...])"),
    (doc(rule(accounts=["demo_checking_0000"], kinds=["checking"])),
     "transfer_rules.yaml: rules[0] (demo-rule): give exactly one of accounts or kinds — scope the rule by account "
     "ids (accounts: [...]) or by account kinds (kinds: [...])"),
    (doc(rule(sign="outflow")),
     "transfer_rules.yaml: rules[0] (demo-rule): unknown sign 'outflow' — credit or debit"),
    (doc(rule(counter_hint="demo_somewhere")),
     f"transfer_rules.yaml: rules[0] (demo-rule): counter_hint 'demo_somewhere' {HINT_FORMS} — name an account id, "
     "or list the name under groups: or outside:"),
    (doc(rule(counter_hint="kind:current")),
     f"transfer_rules.yaml: rules[0] (demo-rule): counter_hint 'kind:current' {HINT_FORMS} — name an account id, "
     "or list the name under groups: or outside:"),
    (doc(rule(), {"pattern": "^DEMO OTHER", "type": "transfer", "kinds": ["checking"], "sign": "both"}),
     "transfer_rules.yaml: rules[1]: unknown sign 'both' — credit or debit"),        # no name: the index alone
    (doc(groups={"demo_cards": {"accounts": ["demo_card_0000", "demo_card_9999"], "expect_counterpart": True}}),
     "transfer_rules.yaml: groups.demo_cards: unknown account 'demo_card_9999' — use an account id from accounts.yaml"),
    (doc(groups={"demo_cards": ["demo_card_0000"]}),
     f"transfer_rules.yaml: groups.demo_cards: expected {{accounts: [...], expect_counterpart: true|false}} — "
     f"{FIX_GROUP}"),
    (doc(groups={"demo_cards": {"accounts": ["demo_card_0000"]}}),
     f"transfer_rules.yaml: groups.demo_cards: expected {{accounts: [...], expect_counterpart: true|false}} — "
     f"{FIX_GROUP}"),
    (doc(groups={"demo_card_0000": {"accounts": ["demo_card_0001"], "expect_counterpart": True}}),
     "transfer_rules.yaml: groups.demo_card_0000: the name is also an account id — give the group another name"),
    (doc(groups={"demo_elsewhere": {"accounts": [], "expect_counterpart": False}}, outside=["demo_elsewhere"]),
     "transfer_rules.yaml: groups.demo_elsewhere: the name is also an outside: name — give the group another name"),
    (doc(groups={"bank_any": {"accounts": [], "expect_counterpart": False}}),
     "transfer_rules.yaml: groups.bank_any: the name is also bank_any — give the group another name"),
    (doc(outside="demo_elsewhere"),
     "transfer_rules.yaml: outside: expected a list of names — e.g. `outside: [demo_elsewhere]`"),
    (doc(outside=["demo_checking_0000"]),
     "transfer_rules.yaml: outside: 'demo_checking_0000' is also an account id — leave it out (an account id "
     "already names the account)"),
    (doc(venmo_patterns={**P2P, "debit": "SAMPLEPAY ("}),
     f"transfer_rules.yaml: venmo_patterns.debit: is not a valid regular expression ({_re_error('SAMPLEPAY (')}) — "
     f"{FIX_REGEX}"),
    (doc(venmo_patterns={**P2P, "credit": "[SAMPLEPAY"}),
     f"transfer_rules.yaml: venmo_patterns.credit: is not a valid regular expression ({_re_error('[SAMPLEPAY')}) — "
     f"{FIX_REGEX}"),
    (doc(venmo_patterns={**P2P, "zelle": "ZELLE)"}),
     f"transfer_rules.yaml: venmo_patterns.zelle: is not a valid regular expression ({_re_error('ZELLE)')}) — "
     f"{FIX_REGEX}"),
    (doc(venmo_patterns={**P2P, "zelle_party": "ZELLE TO (["}),
     f"transfer_rules.yaml: venmo_patterns.zelle_party: is not a valid regular expression "
     f"({_re_error('ZELLE TO ([')}) — {FIX_REGEX}"),
    (doc(venmo_patterns={**P2P, "zelle_party": "ZELLE TO [A-Z ]+"}),
     "transfer_rules.yaml: venmo_patterns.zelle_party: has no group — put the counterparty's name in ( )"),
    (doc(venmo_patterns={**P2P, "zelle_bank_types": "P2P_DEBIT"}),
     "transfer_rules.yaml: venmo_patterns.zelle_bank_types: expected a list of bank type codes — "
     "e.g. `zelle_bank_types: [P2P_DEBIT, P2P_CREDIT]`"),
    *((doc(venmo_patterns={**P2P, "pairing": value}),
       "transfer_rules.yaml: venmo_patterns.pairing: expected unique or nearest — leave it out for unique")
      for value in ("closest", "Unique", True, ["unique"])),
]


def test_a_rule_gives_one_of_the_internal_types():
    assert set(RULE_TYPES) == INTERNAL_TYPES and len(RULE_TYPES) == len(INTERNAL_TYPES)


@pytest.mark.parametrize("d, message", LOAD_ERRORS)
def test_each_problem_is_named_with_its_rule_and_its_fix(d, message):
    assert transfer_rule_problems(d, ACCOUNTS) == [message]
    with pytest.raises(ValueError) as e:
        compiled(d)
    assert str(e.value) == message


def test_every_problem_is_collected_not_only_the_first():
    d = doc(rule(type="payment"), rule(name="demo-other", counter_hint="demo_somewhere"), outside="demo_elsewhere")
    problems = transfer_rule_problems(d, ACCOUNTS)
    assert len(problems) == 3
    assert {p.split(":")[1].strip().split(" ")[0] for p in problems} == {"rules[0]", "rules[1]", "outside"}


def _config_dir(tmp_path, transfer_rules: dict):
    d = tmp_path / "config"
    write_household(d)
    (d / "accounts.yaml").write_text(yaml.safe_dump({"accounts": ACCOUNTS}, sort_keys=False), "utf-8")
    (d / "transfer_rules.yaml").write_text(yaml.safe_dump(transfer_rules, sort_keys=False), "utf-8")
    return d


def test_a_broken_rule_file_stops_the_config_load_with_every_problem(tmp_path):
    d = _config_dir(tmp_path, doc(rule(type="payment"), rule(name="demo-other", sign="sideways")))
    with pytest.raises(ValueError) as e:
        Config(d)
    assert str(e.value).splitlines() == [
        "transfer_rules.yaml: rules[0] (demo-rule): unknown type 'payment' — one of transfer, cc-payment, investment",
        "transfer_rules.yaml: rules[1] (demo-other): unknown sign 'sideways' — credit or debit"]


def test_a_rule_with_neither_accounts_nor_kinds_no_longer_applies_everywhere(tmp_path):
    """An unscoped rule used to apply to every account; now the file is refused, so a rule never widens silently."""
    d = _config_dir(tmp_path, doc({"name": "demo-everywhere", "pattern": "^DEMO MOVE", "type": "transfer"}))
    with pytest.raises(ValueError, match=r"rules\[0\] \(demo-everywhere\): give exactly one of accounts or kinds"):
        Config(d)


def test_every_hint_form_loads_and_the_config_keeps_the_raw_document(tmp_path):
    d = doc(rule(name="a", counter_hint="demo_card_0000"),
            rule(name="b", counter_hint="demo_cards"),
            rule(name="c", counter_hint="kind:credit-card"),
            rule(name="d", counter_hint="bank_any"),
            rule(name="e", counter_hint="demo_elsewhere"),
            rule(name="f", pattern=r"^DEMO MOVE TO (\d{4})", counter_hint="from_text:demo_card_{1}"),
            rule(name="g", kinds=["credit-card"]),
            groups={"demo_cards": {"accounts": ["demo_card_0000", "demo_card_0001"], "expect_counterpart": True},
                    "demo_none": {"accounts": [], "expect_counterpart": False}},
            outside=["demo_elsewhere"],
            venmo_patterns={**P2P, "zelle_bank_types": ["P2P_DEBIT"], "zelle_party": r"ZELLE TO ([A-Z ]+)",
                            "cashout_income": [{"accounts": ["demo_checking_0000"], "subcategory": "Other"}]})
    assert transfer_rule_problems(d, ACCOUNTS) == []
    cfg = Config(_config_dir(tmp_path, d))
    assert cfg.transfer_rules == d
    assert [r.name for r in compile_transfer_rules(cfg)] == list("abcdefg")


def test_compiled_rules_keep_file_order_and_every_key():
    (first, second) = compiled(doc(
        rule(name="demo-first", pattern="^demo move", accounts=["demo_checking_0000", "demo_savings_0000"],
             sign="debit", bank_type="XFER", counter_hint="bank_any", needs_review=True, open_question="Which?"),
        {"pattern": "^DEMO PAID", "type": "cc-payment", "kinds": ["credit-card"]}))
    assert first == Rule(name="demo-first", pattern=first.pattern, type="transfer",
                         accounts=frozenset({"demo_checking_0000", "demo_savings_0000"}), kinds=None, sign="debit",
                         bank_type="XFER", counter_hint="bank_any", needs_review=True, open_question="Which?")
    assert first.pattern.search("DEMO MOVE 1") and first.pattern.flags & re.IGNORECASE
    assert (second.name, second.type, second.accounts, second.kinds) == (
        "rules[1]", "cc-payment", None, frozenset({"credit-card"}))
    assert (second.sign, second.bank_type, second.counter_hint, second.needs_review, second.open_question) == (
        None, None, None, False, None)


# ------------------------------------------------------------------ scoping

def test_a_kinds_rule_types_rows_on_every_account_of_its_kinds_and_no_other():
    d = doc(rule(name="demo-paid", pattern="^DEMO PAYMENT RECEIVED", type="cc-payment", kinds=["credit-card"],
                 sign="credit"))
    rows = {a: tx(a, "DEMO PAYMENT RECEIVED 0001", 5000) for a in ACCOUNTS}
    engine(d).phase_a(list(rows.values()))
    typed = {a for a, t in rows.items() if t.type == "cc-payment"}
    assert typed == {"demo_card_0000", "demo_card_0001"}
    assert all(rows[a].is_internal for a in typed)
    assert all(t.type == "purchase" and not t.is_internal for a, t in rows.items() if a not in typed)


def test_an_accounts_rule_types_rows_on_its_accounts_only():
    d = doc(rule(accounts=["demo_checking_0001"]))
    rows = {a: tx(a, "DEMO MOVE 0001", -5000) for a in ("demo_checking_0000", "demo_checking_0001")}
    engine(d).phase_a(list(rows.values()))
    assert (rows["demo_checking_0001"].type, rows["demo_checking_0000"].type) == ("transfer", "purchase")


def test_the_first_matching_rule_wins_and_only_purchases_are_typed():
    d = doc(rule(name="demo-a", type="investment", kinds=["checking"]), rule(name="demo-b", kinds=["checking"]))
    row = tx("demo_checking_0000", "DEMO MOVE 0001", -5000)
    income = tx("demo_checking_0000", "DEMO MOVE 0002", 5000, type="income")
    engine(d).phase_a([row, income])
    assert row.type == "investment" and income.type == "income" and not income.is_internal


# ------------------------------------------------------------------ hints

def _pair(hint, *, hinted_account="demo_card_0000", groups=None, outside=None, pattern="^DEMO MOVE"):
    """An internal outflow on the first checking account with `hint`, and two equal hint-less internal inflows: one
    on `hinted_account` two business days later, one on the savings account the same day. Returns the outflow and
    the inflow it paired with."""
    d = doc(rule(name="demo-out", pattern=pattern, kinds=["checking"], sign="debit",
                 **({"counter_hint": hint} if hint else {})),
            rule(name="demo-in", pattern="^DEMO ARRIVED", kinds=["savings", "credit-card", "brokerage"],
                 sign="credit"),
            groups=groups, outside=outside)
    out = tx("demo_checking_0000", "DEMO MOVE TO 0000", -12345, date="2030-03-04")
    far = tx(hinted_account, "DEMO ARRIVED", 12345, date="2030-03-06")
    near = tx("demo_savings_0000", "DEMO ARRIVED", 12345, date="2030-03-04")
    run(engine(d), [out, far, near])
    paired = next(t for t in (far, near) if t.transfer_group_id == out.transfer_group_id)
    return out, paired


def test_without_a_hint_the_nearer_leg_pairs():
    _, paired = _pair(None)
    assert paired.account_id == "demo_savings_0000"


@pytest.mark.parametrize("expect", [True, False])
def test_a_group_hint_scores_a_member_whatever_it_expects(expect):
    groups = {"demo_cards": {"accounts": ["demo_card_0000"], "expect_counterpart": expect}}
    _, paired = _pair("demo_cards", groups=groups)
    assert paired.account_id == "demo_card_0000"
    _, paired = _pair("demo_cards", hinted_account="demo_card_0001", groups=groups)    # not a member: no score
    assert paired.account_id == "demo_savings_0000"


def test_a_group_that_expects_a_counterpart_covers_its_members_but_the_row_s_own_account():
    groups = {"demo_banks": {"accounts": ["demo_checking_0000", "demo_savings_0000"], "expect_counterpart": True}}
    d = doc(rule(kinds=["checking"], counter_hint="demo_banks"), groups=groups)
    lone = tx("demo_checking_0000", "DEMO MOVE 0001", -5000)
    eng = engine(d)
    res = run(eng, [lone])
    assert eng._counter_accounts(lone) == ["demo_savings_0000"]
    assert lone.match_status == "unmatched_unexpected"           # the savings data covers the date: a data gap
    assert [q.topic for q in res.open_questions] == ["unmatched-internal"]
    assert "no matching row in the accounts of the group demo_banks" in res.open_questions[0].question
    lone = tx("demo_checking_0000", "DEMO MOVE 0001", -5000)
    run(engine(d, windows={"demo_checking_0000": ("2030-01-01", "2030-12-31")}), [lone])
    assert lone.match_status == "unmatched_window"               # no member's data covers it: silent


def test_a_group_that_expects_no_counterpart_covers_nothing():
    groups = {"demo_elsewhere": {"accounts": ["demo_brokerage_0000"], "expect_counterpart": False}}
    d = doc(rule(kinds=["checking"], counter_hint="demo_elsewhere"), groups=groups)
    lone = tx("demo_checking_0000", "DEMO MOVE 0001", -5000)
    eng = engine(d)
    res = run(eng, [lone])
    assert eng._counter_accounts(lone) == []
    assert lone.type == "transfer" and lone.is_internal and lone.match_status == "unmatched_window"
    assert res.open_questions == []


def test_a_kind_hint_scores_a_card_counterpart_and_covers_the_cards_but_the_row_s_own():
    _, paired = _pair("kind:credit-card")
    assert paired.account_id == "demo_card_0000"
    _, paired = _pair("kind:credit-card", hinted_account="demo_brokerage_0000")    # another kind: no score
    assert paired.account_id == "demo_savings_0000"
    d = doc(rule(kinds=["checking", "credit-card"], counter_hint="kind:credit-card"))
    eng = engine(d)
    on_card = tx("demo_card_0001", "DEMO MOVE 0001", 5000)
    on_checking = tx("demo_checking_0000", "DEMO MOVE 0002", -6000)     # not the card row's opposite: no pair
    res = run(eng, [on_card, on_checking])
    assert eng._counter_accounts(on_card) == ["demo_card_0000"]
    assert eng._counter_accounts(on_checking) == ["demo_card_0000", "demo_card_0001"]
    assert on_card.match_status == on_checking.match_status == "unmatched_unexpected"
    assert len(res.open_questions) == 2                          # one per (account, hint)


def test_an_account_hint_scores_its_account_over_a_nearer_leg():
    _, paired = _pair("demo_card_0000")
    assert paired.account_id == "demo_card_0000"


def test_an_outside_hint_scores_nothing_and_is_never_a_data_gap():
    _, paired = _pair("demo_elsewhere", outside=["demo_elsewhere"])
    assert paired.account_id == "demo_savings_0000"              # as with no hint
    d = doc(rule(kinds=["checking"], counter_hint="demo_elsewhere"), outside=["demo_elsewhere"])
    lone = tx("demo_checking_0000", "DEMO MOVE 0001", -5000)
    eng = engine(d)                                              # every account's data covers the date
    res = run(eng, [lone])
    assert eng._counter_accounts(lone) == []
    assert lone.counter_hint == "demo_elsewhere" and lone.match_status == "unmatched_window"
    assert res.open_questions == []


def test_from_text_fills_the_template_and_resolves_the_result_like_a_literal_hint():
    groups = {"demo_card_group": {"accounts": ["demo_card_0001"], "expect_counterpart": True}}
    pattern = r"^DEMO MOVE TO (\w+)"
    # the result is an account id: it scores +2 and covers that account
    _, paired = _pair("from_text:demo_card_{1}", pattern=pattern)
    assert paired.account_id == "demo_card_0000"
    # the result is a group: it scores the group's members
    _, paired = _pair("from_text:demo_card_group", hinted_account="demo_card_0001", groups=groups, pattern=pattern)
    assert paired.account_id == "demo_card_0001"
    # the result names nothing: it is kept as the row's hint, scores nothing and covers nothing
    out, paired = _pair("from_text:demo_nowhere_{1}", pattern=pattern)
    assert paired.account_id == "demo_savings_0000" and out.counter_hint == "demo_nowhere_0000"
    d = doc(rule(pattern=pattern, kinds=["checking"], counter_hint="from_text:demo_nowhere_{1}"))
    lone = tx("demo_checking_0000", "DEMO MOVE TO 0000", -5000)
    eng = engine(d)
    run(eng, [lone])
    assert eng._counter_accounts(lone) == [] and lone.match_status == "unmatched_window"
    d = doc(rule(pattern=pattern, kinds=["checking"], counter_hint="from_text:demo_card_{1}"))
    lone = tx("demo_checking_0000", "DEMO MOVE TO 0001", -5000)
    eng = engine(d)
    run(eng, [lone])
    assert eng._counter_accounts(lone) == ["demo_card_0001"] and lone.match_status == "unmatched_unexpected"


def test_bank_any_scores_and_covers_the_bank_and_card_accounts():
    eng = engine(doc())
    out = tx("demo_brokerage_0000", "DEMO MOVE", -5000, counter_hint="bank_any")
    scores = {a: eng._hint_score(out, tx(a, "DEMO ARRIVED", 5000)) for a in ACCOUNTS if a != out.account_id}
    assert {a for a, s in scores.items() if s} == BANKS and set(scores.values()) == {0, 1}
    assert eng._counter_accounts(out) == sorted(BANKS)


# ------------------------------------------------------------------ person-to-person keys

def _p2p_engine(own=("ALEX", "SAMPLE"), **keys) -> TransferEngine:
    return engine(doc(venmo_patterns={**P2P, **keys}), household={"own_name_tokens": list(own)})


PARTY = r"^ZELLE (?:TO|FROM) ([A-Z ]+?)(?: \d|$)"


def test_a_listed_bank_type_marks_a_row_whatever_its_wording():
    eng = _p2p_engine(zelle_bank_types=["P2P_DEBIT", "P2P_CREDIT"])
    assert eng._is_venmoish(tx("demo_checking_0000", "SAMPLE BANK TRANSFER 0001", -5000, bank_type="P2P_DEBIT"))
    assert not eng._is_venmoish(tx("demo_checking_0000", "SAMPLE BANK TRANSFER 0001", -5000, bank_type="ACH"))
    assert not eng._is_venmoish(tx("demo_brokerage_0000", "SAMPLE TRANSFER", -5000, bank_type="P2P_DEBIT"))


def test_the_party_pattern_keeps_own_name_rows_and_drops_third_party_ones():
    eng = _p2p_engine(zelle_party=PARTY)
    assert eng._is_venmoish(tx("demo_checking_0000", "Zelle to Alex Sample 0001", -5000))     # case ignored
    assert not eng._is_venmoish(tx("demo_checking_0000", "ZELLE TO ROBIN OTHER 0001", -5000))
    assert eng._is_venmoish(tx("demo_checking_0000", "SAMPLE ZELLE TRANSFER", -5000))          # no party named


def test_without_the_two_keys_no_bank_type_marks_a_row_and_no_party_is_read():
    eng = _p2p_engine()
    assert not eng._is_venmoish(tx("demo_checking_0000", "SAMPLE BANK TRANSFER", -5000, bank_type="P2P_DEBIT"))
    assert eng._is_venmoish(tx("demo_checking_0000", "ZELLE TO ROBIN OTHER 0001", -5000))     # still a candidate


def test_a_third_party_zelle_row_stays_spending_when_the_party_pattern_names_it():
    eng = _p2p_engine(zelle_party=PARTY)
    out = tx("demo_checking_0000", "ZELLE TO ROBIN OTHER 0001", -5000)
    back = tx("demo_checking_0001", "ZELLE FROM ROBIN OTHER 0002", 5000)
    run(eng, [out, back])
    assert out.transfer_group_id is None and out.type == "purchase" and not out.is_internal


# ------------------------------------------------------------------ person-to-person pairing

P2P_OUT = ("demo_checking_0000", "SAMPLEPAY PAYMENT 0001", -12345, "2030-03-04")
P2P_IN = ("demo_checking_0001", "SAMPLEPAY CASHOUT 0001", 12345, "2030-03-05")


def _pairing_engine(pairing=None, *rules) -> TransferEngine:
    keys = {} if pairing is None else {"pairing": pairing}
    return engine(doc(*rules, venmo_patterns={**P2P, **keys}))


def _rows(*specs) -> list:
    return [tx(account, desc, cents, date=date) for account, desc, cents, date in specs]


@pytest.mark.parametrize("pairing", [None, "unique", "nearest"])
def test_a_payment_app_outflow_and_its_one_counterpart_pair(pairing):
    out, inn = _rows(P2P_OUT, P2P_IN)
    res = run(_pairing_engine(pairing), [out, inn])
    assert out.transfer_group_id == inn.transfer_group_id is not None
    assert (out.type, inn.type, out.is_internal, inn.is_internal) == ("transfer", "transfer", True, True)
    assert [m[3] for m in res.matches] == ["venmo_cashout"]


@pytest.mark.parametrize("second", [("demo_savings_0000", "SAMPLEPAY CASHOUT 0002", 12345, "2030-03-07"),
                                    ("demo_savings_0000", "DEMO DEPOSIT 0001", 12345, "2030-03-07")],
                         ids=["a-second-cash-out", "an-ordinary-deposit"])
@pytest.mark.parametrize("pairing", [None, "unique"])
def test_under_unique_a_second_equal_inflow_leaves_both_rows_to_the_unmatched_treatment(pairing, second):
    """Two adults' accounts: a payment out on one and a receipt of the same amount on each of two others. Either could
    be its other leg, so neither is: the payment stays spending and the cash-out stays income for review."""
    out, inn, other = _rows(P2P_OUT, P2P_IN, second)
    res = run(_pairing_engine(pairing), [out, inn, other])
    assert res.matches == [] and all(t.transfer_group_id is None and not t.is_internal for t in (out, inn, other))
    assert (out.type, out.category, out.subcategory) == ("purchase", FALLBACK_CATEGORY, P2P_UNKNOWN_SUB)
    assert (inn.type, inn.category, inn.needs_review) == ("income", INCOME_CATEGORY, True)


def test_under_nearest_the_nearer_of_two_inflows_pairs():
    out, inn, far = _rows(P2P_OUT, P2P_IN, ("demo_savings_0000", "SAMPLEPAY CASHOUT 0002", 12345, "2030-03-07"))
    res = run(_pairing_engine("nearest"), [out, inn, far])
    assert out.transfer_group_id == inn.transfer_group_id is not None and far.transfer_group_id is None
    assert len(res.matches) == 1 and far.type == "income"


@pytest.mark.parametrize("pairing", ["unique", "nearest"])
def test_a_pair_of_two_rule_typed_rows_pairs_whatever_the_setting_with_a_rival_present(pairing):
    rules = (rule(name="demo-out", kinds=["checking"], sign="debit"),
             rule(name="demo-in", pattern="^DEMO ARRIVED", kinds=["savings"], sign="credit"))
    out, inn, rival = _rows(("demo_checking_0000", "DEMO MOVE 0001", -12345, "2030-03-04"),
                            ("demo_savings_0000", "DEMO ARRIVED 0001", 12345, "2030-03-04"),
                            ("demo_checking_0001", "SAMPLEPAY CASHOUT 0001", 12345, "2030-03-05"))
    res = run(_pairing_engine(pairing, *rules), [out, inn, rival])
    assert out.transfer_group_id == inn.transfer_group_id is not None and [m[3] for m in res.matches] == ["pair"]
    assert rival.transfer_group_id is None and rival.type == "income"


def test_under_unique_a_rule_typed_leg_and_a_payment_app_row_pair_only_when_unique():
    rules = (rule(name="demo-out", kinds=["checking"], sign="debit"),)
    out, inn = _rows(("demo_checking_0000", "DEMO MOVE 0001", -12345, "2030-03-04"), P2P_IN)
    run(_pairing_engine("unique", *rules), [out, inn])
    assert out.transfer_group_id == inn.transfer_group_id is not None
    out, inn, other = _rows(("demo_checking_0000", "DEMO MOVE 0001", -12345, "2030-03-04"), P2P_IN,
                            ("demo_card_0000", "DEMO REFUND 0001", 12345, "2030-03-04"))
    run(_pairing_engine("unique", *rules), [out, inn, other])
    assert out.transfer_group_id is None and inn.transfer_group_id is None


@pytest.mark.parametrize("value, pairing", [(None, "unique"), ("unique", "unique"), ("nearest", "nearest")])
def test_the_pairing_key_loads_and_reaches_the_engine(tmp_path, value, pairing):
    vp = {**P2P, **({} if value is None else {"pairing": value})}
    cfg = Config(_config_dir(tmp_path, doc(venmo_patterns=vp)))
    assert TransferEngine.from_config(cfg, EVERY_WINDOW, BANKS).p2p_pairing == pairing


def test_a_pairing_written_with_no_value_reads_as_unique(tmp_path):
    cfg = Config(_config_dir(tmp_path, doc(venmo_patterns={**P2P, "pairing": None})))
    assert TransferEngine.from_config(cfg, EVERY_WINDOW, BANKS).p2p_pairing == "unique"


def test_an_unknown_pairing_stops_the_config_load(tmp_path):
    with pytest.raises(ValueError) as e:
        Config(_config_dir(tmp_path, doc(venmo_patterns={**P2P, "pairing": "closest"})))
    assert str(e.value) == ("transfer_rules.yaml: venmo_patterns.pairing: expected unique or nearest — leave it out "
                            "for unique")


def test_no_p2p_patterns_detect_no_p2p_rows():
    eng = engine(doc())
    rows = [tx("demo_checking_0000", "SAMPLEPAY PAYMENT 0001", -5000),
            tx("demo_checking_0001", "SAMPLEPAY CASHOUT 0001", 5000),
            tx("demo_checking_0000", "ZELLE TO ALEX SAMPLE", -7000)]
    assert not any(eng._is_venmoish(t) for t in rows)
    run(eng, rows)
    assert all(t.type == "purchase" and t.transfer_group_id is None and not t.needs_review for t in rows)
    assert all(t.category is None for t in rows)                 # nothing filed them as P2P

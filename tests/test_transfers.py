# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The transfer and card-payment engine (etl/enrich/transfers.py), the highest-risk logic: phases A, B and C over
synthetic rows on the demo household's accounts, with its transfer rules and the few rules a test needs that it lacks.
Everything here is invented."""
from types import SimpleNamespace

import pytest
from conftest import demo_config, make_txn

from etl.enrich.transfers import TransferEngine, compile_transfer_rules
from etl.models import CASHFLOW_KINDS
from tools.demo import household as hh

# Rules the demo's file lacks, ahead of its own. Each is scoped by `accounts:`, so a leg nothing pairs stays internal:
# a payment from joint checking to Alex's card, and one descriptor read by its sign (money sent to an outside brokerage
# is invested, money coming back from it is a transfer; the debit rule comes first).
EXTRA_RULES = [
    {"name": "checking-pays-alex-card", "pattern": r"^EXAMPLE CARD ONLINE PMT\b", "type": "cc-payment",
     "accounts": [hh.CHECKING], "counter_hint": hh.CARD_ALEX},
    {"name": "acme-invest-sent", "pattern": r"^ACME INVEST CONTRIB\b", "type": "investment", "sign": "debit",
     "accounts": [hh.CHECKING, hh.SAVINGS]},
    {"name": "acme-invest-returned", "pattern": r"^ACME INVEST CONTRIB\b", "type": "transfer", "sign": "credit",
     "accounts": [hh.CHECKING, hh.SAVINGS]},
]


def config(pairing: str | None = None):
    """The demo config with EXTRA_RULES first and, when given, `venmo_patterns.pairing` (the demo states none, so its
    pairing is the default, `unique`)."""
    cfg = demo_config()
    doc = cfg.transfer_rules
    venmo = dict(doc["venmo_patterns"], **({"pairing": pairing} if pairing else {}))
    cfg.transfer_rules = {**doc, "venmo_patterns": venmo, "rules": [*EXTRA_RULES, *doc["rules"]]}
    return cfg


CFG = config()

# the bank and card accounts, as the pipeline hands them to the engine
BANKS = {a for a, m in CFG.accounts.items() if m["kind"] in CASHFLOW_KINDS}

# each account's transaction window: Alex's card's data starts three months after the others'
WINDOWS = {
    hh.CHECKING: ("2030-01-02", "2031-06-30"),
    hh.SAVINGS: ("2030-01-02", "2031-06-30"),
    hh.CARD_ALEX: ("2030-04-01", "2031-06-30"),
    hh.CARD_SAM: ("2030-01-02", "2031-06-30"),
}


def engine(cfg=CFG):
    return TransferEngine.from_config(cfg, WINDOWS, BANKS)


def tx(date, account, desc, cents):
    return make_txn(date=date, account=account, desc=desc, cents=cents)


def run_all(txns, cfg=CFG):
    eng = engine(cfg)
    res = eng.phase_a(txns)
    eng.phase_b(txns, res)
    eng.phase_c(txns, res)
    return res


def test_cc_payment_pair_matches():
    out = tx("2030-05-14", hh.CHECKING, "EXAMPLE CARD CREDIT CRD EPAY PPD ID: 770000303", -163418)
    inn = tx("2030-05-15", hh.CARD_ALEX, "PAYMENT THANK YOU-MOBILE", 163418)
    res = run_all([out, inn])
    assert out.type == "cc-payment" and inn.type == "cc-payment"
    assert out.transfer_group_id == inn.transfer_group_id is not None
    assert out.match_status == inn.match_status == "matched"
    assert len(res.matches) == 1


def test_cc_payment_excluded_even_without_counterpart_window():
    # a payment to Alex's card before the card's data starts: unmatched_window, still excluded from spending (type
    # cc-payment), no review flag
    t = tx("2030-02-08", hh.CHECKING, "EXAMPLE CARD ONLINE PMT 5513", -84270)
    run_all([t])
    assert t.type == "cc-payment" and t.is_internal
    assert t.match_status == "unmatched_window"
    assert not t.needs_review


def test_unmatched_unexpected_aggregates_one_question():
    rows = [tx(f"2030-0{m}-17", hh.CHECKING, f"EXAMPLE CARD ONLINE PMT 7{m}3", -48300 - m) for m in range(5, 10)]
    res = run_all(rows)
    assert all(r.match_status == "unmatched_unexpected" for r in rows)
    qs = [q for q in res.open_questions if q.topic == "unmatched-internal"]
    assert len(qs) == 1 and "5 internal rows" in qs[0].question


def test_one_to_one_matching():
    # one $430 payment must not match two $430 counterparts
    out = tx("2030-05-14", hh.CHECKING, "EXAMPLE CARD CREDIT CRD EPAY PPD ID: 770000303", -43000)
    in1 = tx("2030-05-15", hh.CARD_ALEX, "ONLINE PAYMENT - THANK YOU", 43000)
    in2 = tx("2030-05-16", hh.CARD_ALEX, "ONLINE PAYMENT - THANK YOU", 43000)
    res = run_all([out, in1, in2])
    matched = [t for t in (in1, in2) if t.match_status == "matched"]
    assert len(matched) == 1
    assert len(res.matches) == 1


# `unique` is decided by the uniqueness hold alone (the inflow has two rivals, so no pair is viable but the hinted
# one); only `nearest` makes hint scoring decide. Both ids stay: each pairing setting must give this answer.
@pytest.mark.parametrize("pairing", ["unique", "nearest"])
def test_mutual_hint_pair_beats_earlier_sorted_weak_pair(pairing):
    # a same-amount payment-app debit on the inflow's own day must not take it from the transfer a day earlier whose
    # hints point at each other: under `nearest` the payment-app pair is viable and sorts first by distance, so the
    # hints decide; under `unique` the inflow has two rivals, so the payment-app pair is never viable
    cfg = config(pairing)
    venmo = tx("2030-07-17", hh.CHECKING, "VENMO PAYMENT 1000000001", -134000)
    own = tx("2030-07-16", hh.CHECKING, "BRANCH XFER TO SAV ACCT ENDING 0202", -134000)
    inn = tx("2030-07-17", hh.SAVINGS, "ONLINE TRANSFER FROM CHK XXXXXX0101", 134000)
    run_all([venmo, own, inn], cfg)
    assert own.transfer_group_id == inn.transfer_group_id is not None
    assert venmo.transfer_group_id is None
    assert venmo.type == "purchase"   # unmatched venmo debit stays spending


# `unique` is decided by the uniqueness hold (the card credit has two rivals, so the Venmo debit is never viable);
# only `nearest` makes hint scoring decide. Both ids stay: each pairing setting must give this answer.
@pytest.mark.parametrize("pairing", ["unique", "nearest"])
def test_card_payment_prefers_epay_leg_over_venmo_debit(pairing):
    # the checking Epay that paid the card must outbid a same-amount Venmo debit posted on the card payment's own day
    cfg = config(pairing)
    epay = tx("2030-07-24", hh.CHECKING, "EXAMPLE CARD CREDIT CRD EPAY PPD ID: 770000303", -7425)
    venmo = tx("2030-07-23", hh.CHECKING, "VENMO PAYMENT 1000000002", -7425)
    inn = tx("2030-07-23", hh.CARD_ALEX, "PAYMENT THANK YOU - WEB", 7425)
    run_all([epay, venmo, inn], cfg)
    assert epay.transfer_group_id == inn.transfer_group_id is not None
    assert venmo.transfer_group_id is None


def test_venmo_matched_cashout_is_transfer():
    out = tx("2030-08-05", hh.CHECKING, "VENMO PAYMENT 1000000003", -183460)
    inn = tx("2030-08-08", hh.SAVINGS, "VENMO CASHOUT 1000000003", 183460)
    res = run_all([out, inn])
    assert out.type == "transfer" and inn.type == "transfer"
    assert res.matches[0][3] == "venmo_cashout"


def test_venmo_unmatched_debit_is_spending_with_threshold():
    big = tx("2030-08-05", hh.CHECKING, "VENMO PAYMENT 1000000004", -31500)
    small = tx("2030-08-06", hh.CHECKING, "VENMO PAYMENT 1000000005", -2750)
    run_all([big, small])
    for t in (big, small):
        assert t.type == "purchase" and not t.is_internal
        assert t.subcategory == "Venmo/Zelle Unknown"
    assert big.needs_review and not small.needs_review


def test_a_signed_rule_types_only_its_sign_and_the_other_sign_reaches_the_next_rule():
    """One descriptor, two rules split by `sign:`: the debit is an investment, the credit (money coming back) a
    transfer, on each account the rules list. The debit rule comes first, so without its sign test the credit would be
    typed an investment too, and exports book an investment row as its negated amount: a withdrawal would read as
    negative investing."""
    for account in (hh.CHECKING, hh.SAVINGS):
        sent = tx("2030-09-04", account, "ACME INVEST CONTRIB PPD ID: 0000000000", -362500)
        returned = tx("2030-09-18", account, "ACME INVEST CONTRIB PPD ID: 0000000000", 241000)
        run_all([sent, returned])
        assert (sent.type, sent.is_internal) == ("investment", True), f"{account}: debit typed {sent.type!r}"
        assert (returned.type, returned.is_internal) == ("transfer", True), f"{account}: credit typed {returned.type!r}"


def test_bank_any_hint_points_at_every_bank_account():
    """`counter_hint: bank_any` names no account. In pairing it scores a counterpart on any bank account above an
    equally viable one elsewhere, nearer or not; once a leg is left unmatched, every bank account is a counterpart
    whose data window decides between a data gap and an uncovered leg."""
    banks = {"demo_checking_0000", "demo_savings_0000"}
    windows = {a: ("2030-01-01", "2030-03-31") for a in (*banks, "demo_brokerage_0000")}
    kinds = {"demo_checking_0000": "checking", "demo_savings_0000": "savings", "demo_brokerage_0000": "brokerage"}
    scope = sorted(set(kinds.values()))

    def run(hint, rows):
        sweep_out = {"name": "demo-sweep-out", "pattern": "DEMO SWEEP OUT", "type": "transfer", "sign": "debit",
                     "kinds": scope}
        if hint:
            sweep_out["counter_hint"] = hint
        rules = {"rules": [sweep_out, {"name": "demo-sweep-in", "pattern": "DEMO SWEEP IN", "type": "transfer",
                                       "sign": "credit", "kinds": scope}]}
        accounts = {a: {"kind": k} for a, k in kinds.items()}
        compiled = compile_transfer_rules(SimpleNamespace(transfer_rules=rules, accounts=accounts))
        eng = TransferEngine(compiled, rules, {}, windows, banks, account_kinds=kinds)
        res = eng.phase_a(rows)
        eng.phase_b(rows, res)
        eng.phase_c(rows, res)
        return res

    def legs():
        return (tx("2030-01-07", "demo_checking_0000", "DEMO SWEEP OUT 0001", -12345),
                tx("2030-01-07", "demo_brokerage_0000", "DEMO SWEEP IN 0001", 12345),   # same day, not a bank
                tx("2030-01-08", "demo_savings_0000", "DEMO SWEEP IN 0002", 12345))     # a day later, a bank

    out, near, bank = legs()
    run("bank_any", [out, near, bank])
    assert out.transfer_group_id == bank.transfer_group_id is not None
    assert out.match_status == bank.match_status == "matched"
    assert out.type == bank.type == "transfer"
    assert near.transfer_group_id is None
    out, near, bank = legs()                       # control: without the hint the nearer leg wins
    run(None, [out, near, bank])
    assert out.transfer_group_id == near.transfer_group_id is not None
    assert bank.transfer_group_id is None

    lone = tx("2030-02-05", "demo_brokerage_0000", "DEMO SWEEP OUT 0003", -6789)
    res = run("bank_any", [lone])
    assert lone.type == "transfer" and lone.is_internal
    assert lone.match_status == "unmatched_unexpected"              # a bank account's data covers the date
    assert [q.topic for q in res.open_questions] == ["unmatched-internal"]
    lone = tx("2030-02-05", "demo_brokerage_0000", "DEMO SWEEP OUT 0003", -6789)
    run(None, [lone])                              # control: no hint, no expected counterpart
    assert lone.match_status == "unmatched_window"

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The edges of the data: purchases still pending on Sam's card when the history ends (in the feed's balance, never
stored), a purchase on checking the bank already dates two days after the end, a transfer to savings that savings
does not show (a gap in the data) and transfers to an account the household does not track, which raise an open
question."""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from .. import household as hh
from ..dates import business_day_on_or_after, month_end, shift_months
from ..ledger import Expect, Row
from . import Merchant, fits

if TYPE_CHECKING:
    from ..plan import Plan

TRANSFERS = "transfer_rules.yaml"

# (days before the end, descriptor, issuer's label, cents): purchases the card has authorised but not yet posted
PENDING = ((2, "TARGET T-2214", "Shopping", 3_418), (2, "DOLLAR TREE #5521", "Shopping", 1_250),
           (1, "CHICK-FIL-A #01734", "Food & Drink", 1_689))
WALGREENS = Merchant("WALGREENS #10234", "Walgreens", "Health", "Pharmacy", label="Health & Wellness")
FUTURE_DAYS_AFTER_END = 2
FUTURE_CENTS = 1_893

GAP_RULE = {"name": "branch-transfer-to-savings", "pattern": r"^BRANCH XFER TO SAV\b", "type": "transfer",
            "accounts": [hh.CHECKING], "counter_hint": hh.SAVINGS}
GAP_DESC = f"BRANCH XFER TO SAV ACCT ENDING {hh.SAVINGS[-4:]}"
GAP_CENTS = 72_500

OUTSIDE = "maple_ridge_cu"
QUESTION_RULE = {"name": "maple-ridge-transfer", "pattern": r"^MAPLE RIDGE CU XFER\b", "type": "transfer",
                 "accounts": [hh.CHECKING], "counter_hint": OUTSIDE,
                 "open_question": "Whose account at Maple Ridge Credit Union do the transfers from joint checking go to?"}
QUESTION_DESC = "MAPLE RIDGE CU XFER 0717"
QUESTION_CENTS = 45_000


def pending_rows(plan: Plan) -> None:
    """Three pending purchases on Sam's card on the last two days before the end, when the feed reports the card."""
    card = hh.CARD_SAM
    days = [plan.end - timedelta(days=before) for before, *_ in PENDING]
    if not (plan.has(card) and plan.sources(card).feed and all(fits(plan, card, d) for d in days)):
        return
    rows = [plan.post(Row(card, day, -cents, desc, bank_category=label, pending=True))
            for day, (_, desc, label, cents) in zip(days, PENDING, strict=True)]
    plan.plant("pending.rows", tuple(rows))


def future_row(plan: Plan) -> None:
    """A pharmacy purchase on checking dated two days after the end, which the last snapshot lists."""
    day = plan.end + timedelta(days=FUTURE_DAYS_AFTER_END)
    if not (plan.has(hh.CHECKING) and plan.sources(hh.CHECKING).feed and plan.opened(hh.CHECKING) <= plan.end):
        return
    row = plan.post(WALGREENS.purchase(hh.CHECKING, day, -FUTURE_CENTS, card=False, bank_type="DEBIT_CARD"))
    plan.plant("future.uid", row)
    plan.plant("future.date", day)


def data_gap(plan: Plan) -> None:
    """One transfer from checking that the demo's account-scoped rule types, hinting savings, on a day savings' own
    rows cover (its monthly interest, posted on every month's last day, lies on both sides): savings shows no other
    leg, so the row stays internal and is listed as a gap in the data."""
    day = business_day_on_or_after(shift_months(plan.end, -9) + timedelta(days=13))
    if not (fits(plan, hh.CHECKING, day) and plan.has(hh.SAVINGS)
            and month_end(plan.opened(hh.SAVINGS)) < day < month_end(shift_months(plan.end, -1))):
        return
    plan.add(TRANSFERS, "rules", [GAP_RULE])
    row = plan.post(Row(hh.CHECKING, day, -GAP_CENTS, GAP_DESC, bank_type="ACH_DEBIT",
                        expect=Expect("transfer", "Internal", "transfer", "internal", "Branch Xfer To Sav",
                                      internal=True, gap=hh.SAVINGS)))
    plan.plant("data_gap.uid", row)
    plan.plant("data_gap.account", hh.CHECKING)
    plan.plant("data_gap.hint", hh.SAVINGS)


def open_question(plan: Plan) -> None:
    """One transfer from checking to an account outside the household's data, typed by an account-scoped rule that
    asks whose account it is: one open question, and no gap in the data (the hint names an outside place)."""
    day = business_day_on_or_after(shift_months(plan.end, -5) + timedelta(days=3))
    if not fits(plan, hh.CHECKING, day):
        return
    plan.add(TRANSFERS, "outside", [OUTSIDE])
    plan.add(TRANSFERS, "rules", [QUESTION_RULE])
    row = plan.post(Row(hh.CHECKING, day, -QUESTION_CENTS, QUESTION_DESC, bank_type="ACH_DEBIT",
                        expect=Expect("transfer", "Internal", "transfer", "internal", "Maple Ridge Cu Xfer",
                                      internal=True, question=QUESTION_RULE["name"])))
    plan.plant("open_question.rule", QUESTION_RULE["name"])
    plan.plant("open_question.uid", row)

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Money between the household's own accounts, as the starter's kind-scoped transfer rules read it.

- Card payments: early each month checking pays each card what it owed at the end of the month before (so the cards
  stop growing); the debit is worded as a card payment (`card-payment-sent`) and the card shows the credit one or two
  business days later with the issuer's thanks (`card-payment-received`). The two legs are exactly opposite and no
  other row near either has the opposite amount, so they pair.
- Savings: on the business day from the 20th of each month checking moves to savings the round hundreds it holds
  above a cushion (`transfer-with-savings` on checking, `transfer-with-checking` on savings), the same day.
- One debit worded as a payment to a store card the household does not track: `card-payment-sent` types it, nothing
  pairs it, so it is released back to an ordinary purchase in the fallback category and flagged for review, however
  small (`store_card`, an ordinary behaviour).

The first two read balances, so a profile runs them as settlements, after every ordinary behaviour: `card_payments`
a card's posted balance at a month's end, which every behaviour that posts to a card decides; `transfers` checking's
balance the day before each transfer, which every behaviour that posts to checking (and the card payments) decide.
Whether a month has a payment or a move depends on those balances, so each row's feed id names its month
(`Plan.move`'s `key`), and a month that has none moves no other month's id."""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from .. import household as hh
from ..dates import (
    add_business_days,
    business_day_on_or_after,
    month_starts,
    shift_months,
)
from ..ledger import Expect, Row
from . import fits, uncategorized_review_cents

if TYPE_CHECKING:
    from ..plan import Plan

PAYMENT_DAY = {hh.CARD_ALEX: 5, hh.CARD_SAM: 8}        # the day of the month each card's payment leaves checking
PAYMENT_SENT = "EXAMPLE CARD CREDIT CRD EPAY PPD ID: 77000{last4}"
PAYMENT_RECEIVED = "PAYMENT THANK YOU"
SWEEP_DAY = 20
CUSHION_CENTS = 1_000_000                               # what checking keeps after each month's move to savings
TO_SAVINGS = f"ONLINE TRANSFER TO SAV XXXXXX{hh.SAVINGS[-4:]}"
FROM_CHECKING = f"ONLINE TRANSFER FROM CHK XXXXXX{hh.CHECKING[-4:]}"
STORE_CARD = "LAKESIDE OUTFITTERS CREDIT CRD AUTOPAY PPD ID: 4410022871"
STORE_CARD_CENTS = 8_745


def _internal(kind: str, vendor: str, pair: str) -> Expect:
    return Expect(kind, "Internal", kind, "internal", vendor, internal=True, pair=pair)


def card_payments(plan: Plan) -> None:
    """Each month's payment of each card from checking, from the card's first month on (a month in which the card
    owed nothing at the end of the month before has none); the first one planted as `card_payment.pair`. Each card
    draws its landing days from a stream of its own, so one card's history moves no other card's payments."""
    if not plan.has(hh.CHECKING):
        return
    first = None
    for card, day in PAYMENT_DAY.items():
        if not plan.has(card):
            continue
        lag = plan.stream(f"card_payments.lag.{card}")
        for month in month_starts(plan.opened(card), plan.end):
            landing = lag.choice((1, 2))                # drawn every month, so a skipped month moves no other
            owed = -plan.ledger.balance(card, month - timedelta(days=1))
            sent = business_day_on_or_after(month.replace(day=day))
            landed = add_business_days(sent, landing)
            if owed <= 0 or not (fits(plan, hh.CHECKING, sent) and fits(plan, card, landed)):
                continue
            key = f"card_payment.{card}.{month:%Y-%m}"
            pair = plan.move(
                Row(hh.CHECKING, sent, -owed, PAYMENT_SENT.format(last4=card[-4:]), bank_type="ACH_DEBIT",
                    expect=_internal("cc-payment", "Example Card Credit Crd", key)),
                Row(card, landed, owed, PAYMENT_RECEIVED, bank_type="Payment",
                    expect=_internal("cc-payment", "Payment Thank You", key)),
                key=f"{card}-{month:%Y-%m}")
            first = first or pair
    if first is not None:
        plan.plant("card_payment.pair", first)


def transfers(plan: Plan) -> None:
    """Each month's move to savings; the first move planted as `transfer.pair`."""
    if not (plan.has(hh.CHECKING) and plan.has(hh.SAVINGS)):
        return
    first = None
    for month in month_starts(max(plan.opened(hh.CHECKING), plan.opened(hh.SAVINGS)), plan.end):
        day = business_day_on_or_after(month.replace(day=SWEEP_DAY))
        if not (fits(plan, hh.CHECKING, day) and fits(plan, hh.SAVINGS, day)):
            continue
        spare = (plan.ledger.balance(hh.CHECKING, day - timedelta(days=1)) - CUSHION_CENTS) // 10_000 * 10_000
        if spare <= 0:                                   # nothing above the cushion: nothing moves this month
            continue
        key = f"transfer.{month:%Y-%m}"
        pair = plan.move(Row(hh.CHECKING, day, -spare, TO_SAVINGS, bank_type="ACH_DEBIT",
                             expect=_internal("transfer", "Online Transfer To Sav", key)),
                         Row(hh.SAVINGS, day, spare, FROM_CHECKING,
                             expect=_internal("transfer", "Online Transfer From Chk", key)),
                         key=f"{month:%Y-%m}")
        first = first or pair
    if first is not None:
        plan.plant("transfer.pair", first)


def store_card(plan: Plan) -> None:
    """The payment to the untracked store card, eleven months before the end's month (an ordinary behaviour: its
    amount reads no balance), planted as `transfer.unpaired_kind_leg`."""
    if STORE_CARD_CENTS >= uncategorized_review_cents(plan):
        raise ValueError("the store-card payment stays under the review threshold: its flag comes from the rule")
    day = business_day_on_or_after(shift_months(plan.end, -11) + timedelta(days=14))
    if not fits(plan, hh.CHECKING, day):
        return
    plan.plant("transfer.unpaired_kind_leg", plan.post(Row(
        hh.CHECKING, day, -STORE_CARD_CENTS, STORE_CARD, bank_type="ACH_DEBIT",
        expect=Expect("purchase", "Cash & Misc", "Uncategorized", "fallback", "Lakeside Outfitters Credit Crd",
                      needs_review=True))))

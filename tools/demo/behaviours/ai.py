# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The vendors the AI step meets: purchases on Sam's card that no rule, label or answer places, so the fallback files
them, and the rows beside them the step must never send. The household's AI step stays off (no key, no consent):
these rows are ordinary uncategorized purchases until a test switches it on.

- Two invented shops, each bought from twice on different days: every filter passes them, so they are what the step
  asks about (`ai.sendable`).
- A shop holding Alex's name, a framer holding a four-digit run and a cider mill bought from once: each is kept back
  for its reason (`name`, `digits`, `seen`, as etl/ai/policy.py REASONS names them), though the fallback files them
  like the two above.
- A person paid twice through PayPal (`PAYPAL *` and a name): the fallback files it as a purchase, but a payment
  app's row is no candidate at all.
- A balance transfer onto the card that an account-scoped transfer rule types as internal, its far side an account
  outside the data: a decoy that is no purchase.

Each vendor is declared with what the step makes of it (`plan.declare_ai`), and truth states the candidates, the
withheld and why, and the preview's counts from those declarations. No card label is printed on these rows (the
issuer's label would file them), and every amount sits under the uncategorized review threshold, so none is flagged."""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from .. import household as hh
from ..dates import shift_months
from ..ledger import Expect, Row
from . import fits, uncategorized_review_cents

if TYPE_CHECKING:
    from ..plan import Plan

TRANSFERS = "transfer_rules.yaml"
FALLBACK = ("Cash & Misc", "Uncategorized")

# (descriptor, the vendor the starter's cleanup makes of it, what the step makes of it, purchases as (months before
# the end, day of that month, cents))
# (the amounts are small: they come out of net worth, whose $100,000 levels the household crosses with room to spare)
VENDORS = (
    ("MERIDIAN BIKE WORKS", "Meridian Bike Works", None, ((9, 6, 3_415), (3, 19, 4_960))),
    ("FERNHILL POTTERY STUDIO", "Fernhill Pottery Studio", None, ((12, 14, 2_240), (5, 2, 1_875))),
    (f"{hh.ALEX.upper()} PAINT AND PAPER", f"{hh.ALEX} Paint And Paper", "name", ((11, 21, 1_433), (4, 9, 1_119))),
    ("STUDIO 1824 FRAMES", "Studio 1824 Frames", "digits", ((10, 25, 2_502), (2, 12, 1_388))),
    ("QUARRY HILL CIDER MILL", "Quarry Hill Cider Mill", "seen", ((7, 17, 946),)),
    ("PAYPAL *JORDANEXAMPL", "Jordanexampl", "payment_app", ((8, 4, 1_500), (6, 27, 1_250))),
)

OUTSIDE = "bramble_trust_card"
DECOY_RULE = {"name": "card-balance-transfer", "pattern": r"^BRAMBLE TRUST BAL XFER\b", "type": "transfer",
              "accounts": [hh.CARD_SAM], "counter_hint": OUTSIDE}
DECOY_DESC, DECOY_VENDOR = "BRAMBLE TRUST BAL XFER", "Bramble Trust Bal Xfer"
DECOY_MONTHS, DECOY_DAY, DECOY_CENTS = 13, 8, 9_500


def ai_vendors(plan: Plan) -> None:
    """Each vendor whose every purchase fits Sam's card's history, declared with what the step makes of it, its rows
    planted under `ai.sendable` or `ai.<reason>`; the decoy and its rule when its day fits, planted as `ai.internal`."""
    card = hh.CARD_SAM
    if not plan.has(card):
        return
    threshold = uncategorized_review_cents(plan)
    planted: dict[str, tuple[Row, ...]] = {}
    for desc, vendor, reason, buys in VENDORS:
        days = [shift_months(plan.end, -months) + timedelta(days=day - 1) for months, day, _ in buys]
        if not all(fits(plan, card, d) for d in days):
            continue
        if any(cents >= threshold for *_, cents in buys) or len(set(days)) != len(days):
            raise ValueError(f"{vendor}: its purchases sit under the review threshold, each on its own day")
        rows = tuple(plan.post(Row(card, d, -cents, desc,
                                   expect=Expect("purchase", *FALLBACK, "fallback", vendor)))
                     for d, (*_, cents) in zip(days, buys, strict=True))
        plan.declare_ai(vendor, reason)
        handle = f"ai.{reason or 'sendable'}"
        planted[handle] = planted.get(handle, ()) + rows
    day = shift_months(plan.end, -DECOY_MONTHS) + timedelta(days=DECOY_DAY - 1)
    if fits(plan, card, day):
        plan.add(TRANSFERS, "outside", [OUTSIDE])
        plan.add(TRANSFERS, "rules", [DECOY_RULE])
        planted["ai.internal"] = (plan.post(Row(card, day, -DECOY_CENTS, DECOY_DESC, expect=Expect(
            "transfer", "Internal", "transfer", "internal", DECOY_VENDOR, internal=True))),)
        plan.declare_ai(DECOY_VENDOR, "internal")
    for handle, rows in planted.items():
        plan.plant(handle, rows)

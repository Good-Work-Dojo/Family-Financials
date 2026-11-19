# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Person-to-person payments on joint checking. The demo config adds `venmo_patterns.zelle_party`, the wording that
names a Zelle row's counterparty, so a Zelle row naming someone outside the household is spending like any other.

- Alex Zelles money from checking to savings under the household's own name: both legs name him, the amounts are
  exactly opposite and each leg is the other's only rival, so the two pair as a transfer.
- A Zelle payment to a friend, Jordan Example (a name sharing no token with the household's own names), is no
  pairing candidate: the starter's catch-all files it as an unknown person-to-person payment, reviewed only from
  the uncategorized threshold, which it stays under.
- Two payment-app debits nobody explains are unknown person-to-person payments: the one at or above the payment-app
  review threshold is flagged, the one under it is not."""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from .. import household as hh
from ..dates import add_business_days, business_day_on_or_after, shift_months
from ..ledger import Expect, Row
from . import fits, uncategorized_review_cents, venmo_review_cents

if TYPE_CHECKING:
    from ..plan import Plan

TRANSFERS = "transfer_rules.yaml"
# the counterparty's name in one group: the capitals after "ZELLE PAYMENT TO|FROM", up to the reference number
ZELLE_PARTY = r"\bZELLE PAYMENT (?:TO|FROM) ([A-Z]+(?: [A-Z]+)*)"
OWN_NAME = f"{hh.ALEX} {hh.SURNAME}".upper()
FRIEND = "JORDAN EXAMPLE"
OWN_CENTS = 18_525                 # under the uncategorized threshold, so neither leg is ever flagged
FRIEND_CENTS = 14_250
VENMO_ABOVE_CENTS = 22_640         # from the payment-app threshold up, under the uncategorized one
VENMO_BELOW_CENTS = 4_860
P2P_UNKNOWN = ("Cash & Misc", "Venmo/Zelle Unknown")


def _own_tokens() -> list[str]:
    """The household's own-name tokens as the engine derives them: the adults' names and the surname, upper-cased;
    a party whose name holds one as a substring is the household's own."""
    return [m.name.upper() for m in hh.MEMBERS if m.role == "adult"] + [hh.SURNAME.upper()]


def p2p(plan: Plan) -> None:
    if any(t in FRIEND for t in _own_tokens()) or not any(t in OWN_NAME for t in _own_tokens()):
        raise ValueError(f"{FRIEND!r} must share no own-name token and {OWN_NAME!r} must hold one")
    uncategorized, venmo = uncategorized_review_cents(plan), venmo_review_cents(plan)
    if not (OWN_CENTS < uncategorized and FRIEND_CENTS < uncategorized
            and VENMO_BELOW_CENTS < venmo <= VENMO_ABOVE_CENTS < uncategorized):
        raise ValueError("the person-to-person amounts must sit on their sides of the review thresholds")
    if not plan.has(hh.CHECKING):
        return
    end = plan.end
    planted = False

    # Alex's own Zelle from checking to savings, landing the next business day
    sent = business_day_on_or_after(shift_months(end, -6) + timedelta(days=6))
    landed = add_business_days(sent, 1)
    if fits(plan, hh.CHECKING, sent) and fits(plan, hh.SAVINGS, landed):
        planted = True
        ref = "31847562093"
        legs = [(hh.CHECKING, sent, -OWN_CENTS, f"ZELLE PAYMENT TO {OWN_NAME} {ref}", "Zelle Payment To Alex"),
                (hh.SAVINGS, landed, OWN_CENTS, f"ZELLE PAYMENT FROM {OWN_NAME} {ref}", "Zelle Payment From Alex")]
        out, into = (Row(a, d, c, desc, bank_type="QUICKPAY_DEBIT" if c < 0 else None,
                         expect=Expect("transfer", "Internal", "transfer", "internal", vendor, internal=True,
                                       pair="zelle.own_pair"))
                     for a, d, c, desc, vendor in legs)
        plan.plant("zelle.own_pair", plan.move(out, into))

    # a Zelle payment to a friend: spending the catch-all files, under its review threshold
    day = business_day_on_or_after(shift_months(end, -10) + timedelta(days=3))
    if fits(plan, hh.CHECKING, day):
        planted = True
        plan.plant("zelle.third_party", plan.post(Row(
            hh.CHECKING, day, -FRIEND_CENTS, f"ZELLE PAYMENT TO {FRIEND} 52203918744", bank_type="QUICKPAY_DEBIT",
            expect=Expect("purchase", *P2P_UNKNOWN, "keyword", "Zelle Payment To Jordan"))))

    # two payment-app debits, one from the payment-app review threshold up and one under it
    for handle, months, days, cents, ref in (("p2p.venmo_above", -8, 11, VENMO_ABOVE_CENTS, "1000000001"),
                                             ("p2p.venmo_below", -14, 20, VENMO_BELOW_CENTS, "1000000002")):
        day = business_day_on_or_after(shift_months(end, months) + timedelta(days=days))
        if fits(plan, hh.CHECKING, day):
            planted = True
            plan.plant(handle, plan.post(Row(hh.CHECKING, day, -cents, f"VENMO PAYMENT {ref}", bank_type="ACH_DEBIT",
                                             expect=Expect("purchase", *P2P_UNKNOWN, "fallback", "Venmo Payment",
                                                           needs_review=cents >= venmo))))
    if planted:
        plan.put(TRANSFERS, "venmo_patterns", {"zelle_party": ZELLE_PARTY})

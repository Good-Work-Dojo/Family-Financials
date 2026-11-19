# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Subscriptions: charges at streaming and membership merchants of the starter's vendor list, which their vendor
rules file under Subscriptions, each a fixed amount on a fixed day of the month (on joint checking the business day
from it), and each declared to the recurring list as the household knows it:

- monthly: Netflix on Alex's card, every month of the card's history;
- annual: Amazon Prime on joint checking (the one account whose history holds three renewals), renewed a year apart,
  the first more than two years before the end;
- price_changed: Spotify on Sam's card, whose price rose once, five months before the end's month;
- cancelled: Hulu on Alex's card, which the family wrote down as cancelled (assumptions.yaml
  `cancelled_subscriptions`) while the charge goes on: listed, and not active;
- lapsed: Peacock on Sam's card, whose last charge is five months before the end's month: listed, and not active.

A subscription whose charges the plan's window cannot hold — three, all three renewals for the annual one, and two at
the old price and three at the new for the price change — is left out whole, with its handle and its config."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from .. import household as hh
from ..dates import business_day_on_or_after, month_starts, shift_months
from ..plan import Recurring
from . import MIN_CHARGES, Merchant, fits

if TYPE_CHECKING:
    from ..plan import Plan

ASSUMPTIONS = "assumptions.yaml"
YEAR_MONTHS = 12
RENEWALS = 3                         # the annual subscription's renewals in the window
OLD_PRICE_CHARGES, NEW_PRICE_CHARGES = 2, 3     # the price change needs both prices seen this often

NETFLIX = Merchant("NETFLIX.COM", "Netflix", "Subscriptions", "Streaming", label="Entertainment")
AMAZON_PRIME = Merchant("AMAZON PRIME MEMBERSHIP", "Amazon Prime", "Subscriptions", "Memberships")
SPOTIFY = Merchant("SPOTIFY USA", "Spotify", "Subscriptions", "Streaming", label="Entertainment")
HULU = Merchant("HULU.COM/BILL", "Hulu", "Subscriptions", "Streaming", label="Entertainment")
PEACOCK = Merchant("PEACOCKTV PREMIUM", "Peacock", "Subscriptions", "Streaming", label="Entertainment")


@dataclass(frozen=True)
class Subscription:
    """One subscription: its handle (`subscriptions.<handle>`), merchant and account, how often it is charged
    (`monthly`, or `annual`: RENEWALS renewals a year apart, the latest `renewed_months_before` months before the
    end's month), the day of the month and the price; `raised_cents` the price from `raised_months_before` months
    before the end's month on; `stops_months_before` the month of the last charge, counted back from the end's;
    `cancelled` whether the family wrote it down as cancelled; `active` whether the household still pays it."""
    handle: str
    merchant: Merchant
    account: str
    cadence: str
    day: int
    cents: int
    raised_cents: int | None = None
    raised_months_before: int = 0
    stops_months_before: int = 0
    renewed_months_before: int = 0
    cancelled: bool = False
    active: bool = True

    def __post_init__(self) -> None:
        if self.cadence not in ("monthly", "annual") or not 1 <= self.day <= 28 or self.cents <= 0:
            raise ValueError(f"{self.handle}: a monthly or annual charge on a day from 1 to 28, of a positive price")
        if self.raised_cents is not None and not (self.raised_cents - self.cents) * 100 > self.cents:
            raise ValueError(f"{self.handle}: a raised price is more than one percent above the old one")

    def months(self, plan: Plan) -> list[date]:
        if self.cadence == "annual":
            return [shift_months(plan.end, -(self.renewed_months_before + YEAR_MONTHS * k))
                    for k in range(RENEWALS - 1, -1, -1)]
        return month_starts(plan.opened(self.account), shift_months(plan.end, -self.stops_months_before))

    def charges(self, plan: Plan) -> list[tuple[date, int]]:
        """The (day, price) of every charge inside the window, oldest first."""
        bank = hh.ACCOUNTS[self.account].kind in ("checking", "savings")
        raised_from = shift_months(plan.end, -self.raised_months_before)
        out = []
        for month in self.months(plan):
            day = month.replace(day=self.day)
            day = business_day_on_or_after(day) if bank else day
            if fits(plan, self.account, day):
                out.append((day, self.raised_cents if self.raised_cents and month >= raised_from else self.cents))
        return out

    def held(self, charges: list[tuple[date, int]]) -> bool:
        """Does the window hold the charges the detector needs to list it as declared?"""
        if self.cadence == "annual":
            return len(charges) == RENEWALS
        if self.raised_cents is not None:
            raised = sum(c == self.raised_cents for _, c in charges)
            return raised >= NEW_PRICE_CHARGES and len(charges) - raised >= OLD_PRICE_CHARGES
        return len(charges) >= MIN_CHARGES


MONTHLY = Subscription("monthly", NETFLIX, hh.CARD_ALEX, "monthly", 9, 1_549)
ANNUAL = Subscription("annual", AMAZON_PRIME, hh.CHECKING, "annual", 14, 13_900, renewed_months_before=2)
PRICE_CHANGED = Subscription("price_changed", SPOTIFY, hh.CARD_SAM, "monthly", 22, 1_199, raised_cents=1_299,
                             raised_months_before=5)
CANCELLED = Subscription("cancelled", HULU, hh.CARD_ALEX, "monthly", 17, 1_899, cancelled=True, active=False)
LAPSED = Subscription("lapsed", PEACOCK, hh.CARD_SAM, "monthly", 5, 799, stops_months_before=5, active=False)
SUBSCRIPTIONS = (MONTHLY, ANNUAL, PRICE_CHANGED, CANCELLED, LAPSED)


def subscriptions(plan: Plan) -> None:
    for s in SUBSCRIPTIONS:
        if not plan.has(s.account):
            continue
        charges = s.charges(plan)
        if not s.held(charges):
            continue
        card = hh.ACCOUNTS[s.account].kind == "credit-card"
        for day, cents in charges:
            plan.post(s.merchant.purchase(s.account, day, -cents, card=card, bank_type=None if card else "DEBIT_CARD"))
        plan.declare_recurring(Recurring(s.merchant.vendor, s.account, s.cadence, "fixed", active=s.active,
                                         price_changes=int(s.raised_cents is not None)))
        if s.cancelled:
            plan.add(ASSUMPTIONS, "cancelled_subscriptions", [{"vendor": s.merchant.vendor,
                                                               "recorded_on": plan.end.replace(day=1).isoformat()}])
        plan.plant(f"subscriptions.{s.handle}", s.merchant.vendor)

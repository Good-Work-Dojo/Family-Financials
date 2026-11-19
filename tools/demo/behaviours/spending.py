# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Everyday spending: groceries, dining and fuel at national merchants of the starter's vendor list, on joint checking
and the two cards, and two utility bills — each row filed by its merchant's vendor rule.

Every stream declares its regularity, in the terms of the engine's recurring detector, which reads a vendor's charges
on one account: a stream with a cadence repeats in that band with amounts of that mode; an irregular one (no cadence)
draws every gap between two charges outside all the bands and has at least one charge above 1.5 times its median
and none under 0.4 times it, so the detector, which keeps the charges from 0.4 times the median up, sees neither a
cadence nor a spread it accepts."""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction
from typing import TYPE_CHECKING

from .. import household as hh
from ..dates import business_day_on_or_after, month_starts
from ..ledger import half_up
from ..plan import Recurring
from . import MIN_CHARGES, Merchant

if TYPE_CHECKING:
    from ..plan import Plan

# the detector's cadence bands: days between two charges, both ends included
CADENCE_BANDS = {"weekly": (6, 8), "biweekly": (12, 16), "monthly": (27, 33), "quarterly": (83, 97),
                 "annual": (350, 381)}
BIG_SHARE = 0.1              # an irregular stream's share of large charges ...
BIG = (1.6, 2.1)             # ... drawn at these multiples of its typical charge
USUAL = (0.65, 1.35)         # the rest at these
VARIABLE = (0.9, 1.1)        # a variable monthly bill's multiples of its typical charge

WHOLE_FOODS = Merchant("WHOLEFDS MKT #10234", "Whole Foods Market", "Groceries", label="Groceries")
TRADER_JOES = Merchant("TRADER JOE'S #552", "Trader Joe's", "Groceries", label="Groceries")
ALDI = Merchant("ALDI 72031", "ALDI", "Groceries", label="Groceries")
STARBUCKS = Merchant("STARBUCKS STORE 08812", "Starbucks", "Dining", "Coffee", label="Food & Drink")
CHIPOTLE = Merchant("CHIPOTLE 2318", "Chipotle", "Dining", "Restaurants", label="Food & Drink")
PANERA = Merchant("PANERA BREAD #601234", "Panera Bread", "Dining", "Restaurants", label="Food & Drink")
SHELL = Merchant("SHELL OIL 00000000101", "Shell", "Transport", "Fuel", label="Gas")
EXXON = Merchant("EXXONMOBIL 00000101", "ExxonMobil", "Transport", "Fuel", label="Gas")
XFINITY = Merchant("COMCAST CABLE COMM PPD ID: 0000738421", "Xfinity", "Utilities", "Internet",
                   label="Bills & Utilities")
VERIZON = Merchant("VZWRLSS*APOCC VISB", "Verizon Wireless", "Utilities", "Mobile", label="Bills & Utilities")


def _in_a_band(gap: int) -> bool:
    return any(lo <= gap <= hi for lo, hi in CADENCE_BANDS.values())


def _times(cents: int | float, factor: float | Fraction) -> int:
    """`cents` times `factor`, each taken exactly as the number it is (a drawn float included), to the cent, a half
    up (the ledger's one rounding rule)."""
    return half_up(*(Fraction(cents) * Fraction(factor)).as_integer_ratio())


@dataclass(frozen=True)
class SpendStream:
    """One merchant's charges on one account, drawn from the random stream `name`. Declared regularity: `cadence`
    (a CADENCE_BANDS name, None for an irregular stream) and `amount_mode` (fixed or variable, None when irregular).
    An irregular stream draws each gap from `gaps`; a monthly one charges on `day` of each month (on a bank account
    the next business day when that is a weekend)."""
    name: str
    account: str
    merchant: Merchant
    cents: int                         # the typical charge, as a positive number
    cadence: str | None = None
    amount_mode: str | None = None
    gaps: tuple[int, ...] = ()
    day: int = 0
    bank_type: str | None = None       # the checking export's type code

    def __post_init__(self) -> None:
        if self.cadence is None:
            if self.amount_mode is not None or not self.gaps or any(g < 1 or _in_a_band(g) for g in self.gaps):
                raise ValueError(f"{self.name}: an irregular stream draws gaps of a day or more outside every band")
        elif (self.cadence, self.amount_mode) != ("monthly", "variable") or not 1 <= self.day <= 28:
            raise ValueError(f"{self.name}: a monthly variable bill on a day from 1 to 28 is the one regular kind")


STREAMS = (
    SpendStream("groceries.checking", hh.CHECKING, TRADER_JOES, 9_640, gaps=(3, 4, 5, 9, 10), bank_type="DEBIT_CARD"),
    SpendStream("groceries.alex", hh.CARD_ALEX, WHOLE_FOODS, 11_820, gaps=(3, 4, 5, 9, 10)),
    SpendStream("groceries.sam", hh.CARD_SAM, ALDI, 8_410, gaps=(4, 5, 9, 10, 11)),
    SpendStream("dining.checking", hh.CHECKING, STARBUCKS, 875, gaps=(2, 3, 4, 5, 9, 11), bank_type="DEBIT_CARD"),
    SpendStream("dining.alex", hh.CARD_ALEX, CHIPOTLE, 2_640, gaps=(3, 4, 5, 9, 10, 11)),
    SpendStream("dining.sam", hh.CARD_SAM, PANERA, 3_115, gaps=(4, 5, 9, 10, 11)),
    SpendStream("fuel.alex", hh.CARD_ALEX, SHELL, 5_230, gaps=(9, 10, 11, 17, 19)),
    SpendStream("fuel.sam", hh.CARD_SAM, EXXON, 4_690, gaps=(9, 10, 11, 18, 20)),
    SpendStream("utilities.internet", hh.CHECKING, XFINITY, 8_940, "monthly", "variable", day=12,
                bank_type="ACH_DEBIT"),
    SpendStream("utilities.mobile", hh.CARD_ALEX, VERIZON, 14_280, "monthly", "variable", day=21),
)


def everyday(plan: Plan) -> None:
    """Every stream of an account the plan holds; a regular one is declared to the recurring list once the window
    holds the charges the detector needs."""
    for stream in STREAMS:
        if plan.has(stream.account):
            draw = _irregular if stream.cadence is None else _monthly
            charges = draw(plan, stream)
            for day, cents in charges:
                plan.post(stream.merchant.purchase(stream.account, day, -cents, bank_type=stream.bank_type,
                                                   card=hh.ACCOUNTS[stream.account].kind == "credit-card"))
            if stream.cadence is not None and len(charges) >= MIN_CHARGES:
                plan.declare_recurring(Recurring(stream.merchant.vendor, stream.account, stream.cadence,
                                                 stream.amount_mode))


def _irregular(plan: Plan, s: SpendStream) -> list[tuple[date, int]]:
    rng = plan.stream(s.name)
    charges, day = [], plan.opened(s.account) + timedelta(days=rng.choice(s.gaps) - 1)
    while day <= plan.end:
        factor = rng.uniform(*BIG) if rng.random() < BIG_SHARE else rng.uniform(*USUAL)
        charges.append((day, _times(s.cents, factor)))
        day += timedelta(days=rng.choice(s.gaps))
    if not charges:                                  # a window shorter than the first gap holds no charge
        return []
    amounts = [c for _, c in charges]
    median = statistics.median(amounts)
    if max(amounts) <= 1.5 * median:                 # too few draws for a large one: the largest becomes one
        i = amounts.index(max(amounts))
        charges[i] = (charges[i][0], _times(median, Fraction(9, 5)))
        amounts[i] = charges[i][1]
    if min(amounts) < 0.4 * statistics.median(amounts):
        raise ValueError(f"{s.name}: a charge under 0.4 times the median would hide gaps from the detector")
    return charges


def _monthly(plan: Plan, s: SpendStream) -> list[tuple[date, int]]:
    rng = plan.stream(s.name)
    bank = hh.ACCOUNTS[s.account].kind in ("checking", "savings")
    charges = []
    for month in month_starts(plan.opened(s.account), plan.end):
        day = month.replace(day=s.day)
        day = business_day_on_or_after(day) if bank else day
        if plan.opened(s.account) <= day <= plan.end:
            charges.append((day, _times(s.cents, rng.uniform(*VARIABLE))))
    return charges

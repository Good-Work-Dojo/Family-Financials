# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""What the family settles by rule and by review, beyond what the starter's rules file:

- A split vendor: Harbor Wholesale Club, an invented warehouse club on Alex's card, whose baskets the demo's own
  vendor pattern splits — 70 percent Groceries, the rest Shopping / Home Goods — so each of its dozen purchases is a
  parent (filed by the pattern, display-only) and two parts. The vendor never learns (a split vendor is excluded).
- A learned vendor: Ferndale Garden Centre, which no rule names, six purchases on joint checking. In the Review inbox
  the family files three of them under Housing / Home Improvement; three row answers that agree teach the engine
  the vendor, so its other purchases are filed by the learned rule and the three answered ones by their answers.
- A vendor answer: Brightleaf Pediatric Care on Sam's card. One answer "always Health / Medical" files every
  purchase of the practice, and by itself teaches the engine the vendor.
- A conflict: one ticket to the Lantern Hall Benefit Gala on checking, answered Entertainment / Events; overrides.yaml
  says it was a donation (Giving / Charity). Overrides run after answers and win, and the answer is reported as a
  conflict.

Every purchase here lies at gaps outside every cadence band of the recurring detector and spreads its amounts wider
than any amount mode allows, so the detector lists none of them. A feature whose purchases the window cannot hold is
left out whole (the split vendor posts those it holds), with its config, answers and handles."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .. import household as hh
from ..dates import shift_months
from ..ledger import Expect, Row, half_up
from ..plan import Learned
from . import Merchant, fits

if TYPE_CHECKING:
    from ..plan import Plan

VENDORS = "vendor_rules.yaml"
ZONE = ZoneInfo(hh.TIMEZONE)


@dataclass(frozen=True)
class Payee:
    """A payee no rule names: its descriptor and the vendor the engine names it by (the descriptor's first four
    words, title-cased)."""
    desc: str
    vendor: str


def _answered(day: date, days_after: int, at: time) -> datetime:
    """The instant the family answers: `days_after` days after `day`, at `at` in the household's zone."""
    return datetime.combine(day + timedelta(days=days_after), at, tzinfo=ZONE)


# ------------------------------------------------------------------ the split vendor

WHOLESALE = Merchant("HARBOR WHOLESALE CLUB #0417", "Harbor Wholesale Club", "Groceries", label="Shopping")
WHOLESALE_PATTERN = r"^HARBOR WHOLESALE CLUB\b"
GROCERY_PCT = 70
WHOLESALE_LEGS = ({"pct": GROCERY_PCT, "category": "Groceries"},
                  {"remainder": True, "category": "Shopping", "subcategory": "Home Goods"})
WHOLESALE_NOTE = "an estimate of the usual basket: tune the share from a few receipts, or delete the split"
# (days after Alex's card opens, cents): gaps of 41 to 72 days and amounts from $87 to $313
WHOLESALE_PURCHASES = ((20, 8_743), (61, 21_468), (118, 15_621), (184, 31_274), (232, 9_816), (304, 24_337),
                       (357, 17_592), (401, 12_844), (470, 28_963), (528, 11_207), (578, 20_136), (642, 16_489))


def wholesale_legs(cents: int) -> tuple[tuple[str, str | None, int], ...]:
    """A purchase's two parts as the household splits it: GROCERY_PCT percent of it, rounded to the cent, and the
    rest. A purchase whose share falls on half a cent is refused: the rounding would be a guess."""
    if abs(cents) * GROCERY_PCT % 100 == 50:
        raise ValueError(f"{cents}: {GROCERY_PCT} percent of it is half a cent")
    groceries = half_up(cents * GROCERY_PCT, 100)
    return (("Groceries", None, groceries), ("Shopping", "Home Goods", cents - groceries))


def split_vendor(plan: Plan) -> None:
    """The wholesale club's purchases the window holds, each declared as a parent and its two parts; the split rule
    when any is posted, planted as `split.vendor` and the first purchase as `split.purchase`."""
    card = hh.CARD_ALEX
    if not plan.has(card):
        return
    posted = []
    for days, cents in WHOLESALE_PURCHASES:
        day = plan.opened(card) + timedelta(days=days)
        if fits(plan, card, day):
            row = WHOLESALE.purchase(card, day, -cents, card=True)
            posted.append(plan.post(replace(row, expect=replace(row.expect, split=wholesale_legs(-cents)))))
    if not posted:
        return
    plan.add(VENDORS, "patterns", [{"pattern": WHOLESALE_PATTERN, "vendor": WHOLESALE.vendor,
                                    "category": WHOLESALE.category, "note": WHOLESALE_NOTE,
                                    "split": [dict(leg) for leg in WHOLESALE_LEGS]}])
    plan.plant("split.vendor", WHOLESALE.vendor)
    plan.plant("split.purchase", posted[0])


# ------------------------------------------------------------------ a vendor learned from three answers

GARDEN = Payee("FERNDALE GARDEN CENTRE", "Ferndale Garden Centre")
HOME_IMPROVEMENT = {"category": "Housing", "subcategory": "Home Improvement"}
# (months before the end's month, day, cents, answered): about 42, 105, 205, 50 and 104 days apart
GARDEN_PURCHASES = ((18, 12, 6_418, False), (17, 24, 13_247, True), (13, 6, 4_192, True), (7, 28, 21_806, False),
                    (5, 16, 8_731, True), (2, 28, 15_674, False))
GARDEN_ANSWERED = (3, time(20, 15))          # three days after each purchase, in the evening


def learned_vendor(plan: Plan) -> None:
    """The garden centre's six purchases and the family's three answers; the newest purchase planted as
    `learned.unanswered`, the three answered ones as `learned.answered`."""
    days = [shift_months(plan.end, -months).replace(day=day) for months, day, _, _ in GARDEN_PURCHASES]
    if not all(fits(plan, hh.CHECKING, d) for d in days):
        return
    answered, rows = [], []
    for day, (_, _, cents, answer) in zip(days, GARDEN_PURCHASES, strict=True):
        expect = Expect("purchase", *HOME_IMPROVEMENT.values(), "answer" if answer else "learned", GARDEN.vendor)
        rows.append(plan.post(Row(hh.CHECKING, day, -cents, GARDEN.desc, bank_type="DEBIT_CARD", expect=expect)))
        if answer:
            plan.answer(rows[-1], HOME_IMPROVEMENT, _answered(day, *GARDEN_ANSWERED))
            answered.append(rows[-1])
    plan.learns(Learned(GARDEN.vendor, HOME_IMPROVEMENT["category"], HOME_IMPROVEMENT["subcategory"], "txn"))
    plan.plant("learned.vendor", GARDEN.vendor)
    plan.plant("learned.answered", tuple(answered))
    plan.plant("learned.unanswered", rows[-1])


# ------------------------------------------------------------------ a vendor answer

PEDIATRICS = Payee("BRIGHTLEAF PEDIATRIC CARE", "Brightleaf Pediatric Care")
PEDIATRICS_LABEL = "Health & Wellness"       # the card issuer's label, which the answer outranks
MEDICAL = {"category": "Health", "subcategory": "Medical"}
# (months before the end's month, day, cents): about 195, 140 and 205 days apart
PEDIATRICS_VISITS = ((20, 6, 3_500), (14, 19, 4_820), (9, 2, 3_500), (3, 25, 11_240))
PEDIATRICS_ANSWERED = (2, time(21, 5))       # two days after the first visit


def vendor_answer(plan: Plan) -> None:
    """The practice's visits on Sam's card and one answer for the vendor, planted as `vendor_answer.vendor`."""
    card = hh.CARD_SAM
    days = [shift_months(plan.end, -months).replace(day=day) for months, day, _ in PEDIATRICS_VISITS]
    if not all(fits(plan, card, d) for d in days):
        return
    expect = Expect("purchase", *MEDICAL.values(), "answer", PEDIATRICS.vendor)
    visits = tuple(plan.post(Row(card, day, -cents, PEDIATRICS.desc, bank_category=PEDIATRICS_LABEL, expect=expect))
                   for day, (_, _, cents) in zip(days, PEDIATRICS_VISITS, strict=True))
    plan.answer(PEDIATRICS.vendor, MEDICAL, _answered(days[0], *PEDIATRICS_ANSWERED), reaches=visits)
    plan.learns(Learned(PEDIATRICS.vendor, MEDICAL["category"], MEDICAL["subcategory"], "vendor"))
    plan.plant("vendor_answer.vendor", PEDIATRICS.vendor)


# ------------------------------------------------------------------ an override that beats an answer

GALA = Payee("LANTERN HALL BENEFIT GALA", "Lantern Hall Benefit Gala")
GALA_MATCH = "LANTERN HALL"
EVENTS = {"category": "Entertainment", "subcategory": "Events"}
CHARITY = {"category": "Giving", "subcategory": "Charity"}
GALA_TICKET = (11, 9, 18_000)                # months before the end's month, day, cents: under the review threshold
GALA_ANSWERED = (2, time(19, 40))
GALA_NOTE = "the gala ticket was a donation to the hall's restoration fund"


def conflict(plan: Plan) -> None:
    """The ticket, its answer and the override that files it otherwise, matched by its descriptor, day and account
    (its uid depends on the files the sources write after the config); planted as `conflict.row` and the answer as
    `conflict.key`."""
    months, day, cents = GALA_TICKET
    when = shift_months(plan.end, -months).replace(day=day)
    if not fits(plan, hh.CHECKING, when):
        return
    row = plan.post(Row(hh.CHECKING, when, -cents, GALA.desc, bank_type="DEBIT_CARD",
                        expect=Expect("purchase", *CHARITY.values(), "override", GALA.vendor)))
    plan.alone(row)                          # nothing files it before the answer: no other row may pair with it
    answer = plan.answer(row, EVENTS, _answered(when, *GALA_ANSWERED), outranked=True)
    plan.override({"contains": GALA_MATCH, "date": when.isoformat(), "account": hh.CHECKING}, CHARITY, GALA_NOTE)
    plan.plant("conflict.row", row)
    plan.plant("conflict.key", answer)

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""How a purchase gets its category: one purchase below and one above the uncategorized review threshold for each way
the engine files one — a vendor rule of the starter's list, the demo's own keyword rule (a daycare), the card
issuer's label (a bistro no pattern names) and the fallback (a shop nothing places) — and the occasional purchases
that give every spending category of the starter's taxonomy at least one row.

Only the fallback reviews by size: a vendor rule, a keyword rule that asks for no review and a bank label never flag
a row, whatever its amount. Every merchant here has at most two rows on an account, so the recurring detector, which
needs three, never reads one as a schedule."""
from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from .. import household as hh
from ..dates import business_day_on_or_after, shift_months
from ..ledger import Expect, Row
from . import Merchant, fits, uncategorized_review_cents

if TYPE_CHECKING:
    from ..plan import Plan

RULES = "category_rules.yaml"
VENDORS = "vendor_rules.yaml"

# the vendor-rule path: two starter merchants
BARNES_NOBLE = Merchant("BARNES & NOBLE #2214", "Barnes & Noble", "Entertainment", "Books/Games", label="Shopping")
BEST_BUY = Merchant("BEST BUY 00000101", "Best Buy", "Shopping", "Electronics", label="Shopping")
# the keyword path: the demo's own rule for an invented daycare (no review asked)
DAYCARE = "CEDAR HOLLOW DAYCARE PPD ID: 5550199283"
DAYCARE_RULE = {"pattern": r"^CEDAR HOLLOW DAYCARE\b", "category": "Kids", "subcategory": "Childcare"}
DAYCARE_EXPECT = Expect("purchase", "Kids", "Childcare", "keyword", "Cedar Hollow Daycare")
# the bank-label path: an invented bistro the issuer labels Food & Drink
BISTRO, BISTRO_LABEL = "MOSSY OAK BISTRO", "Food & Drink"
BISTRO_EXPECT = Expect("purchase", "Dining", None, "bank_label", "Mossy Oak Bistro")
# the fallback path: an invented shop no rule or label places
GENERAL_STORE = "TALLGRASS GENERAL STORE"


def _fallback(needs_review: bool) -> Expect:
    return Expect("purchase", "Cash & Misc", "Uncategorized", "fallback", "Tallgrass General Store",
                  needs_review=needs_review)


def _bank_day(day: date) -> date:
    return business_day_on_or_after(day)


def paths(plan: Plan) -> None:
    """Each path's two purchases, below and above the threshold, planted as `paths.<path>.below` / `.above`; a path
    whose days the plan's window cannot hold is left out whole."""
    start, end = plan.start, plan.end
    card = hh.CARD_ALEX
    features: dict[str, tuple[tuple[Row, Row], list[tuple[str, str, dict]]]] = {}

    below = BARNES_NOBLE.purchase(card, shift_months(start, 5) + timedelta(days=10), -2_743, card=True)
    above = BEST_BUY.purchase(card, shift_months(end, -7) + timedelta(days=17), -64_999, card=True)
    features["vendor_rule"] = ((below, above), [])

    below = Row(hh.CHECKING, _bank_day(shift_months(start, 6) + timedelta(days=6)), -7_500, DAYCARE,
                bank_type="ACH_DEBIT", expect=DAYCARE_EXPECT)
    above = Row(hh.CHECKING, _bank_day(shift_months(start, 14) + timedelta(days=2)), -118_000, DAYCARE,
                bank_type="ACH_DEBIT", expect=DAYCARE_EXPECT)
    features["keyword"] = ((below, above), [(RULES, "keyword_rules", DAYCARE_RULE)])

    # one in the card's statement files (the month before the cutover), one in the feed
    cutover = plan.cutover(card) if plan.has(card) else None
    if cutover is not None:
        below = Row(card, shift_months(cutover, -1) + timedelta(days=13), -4_218, BISTRO, bank_category=BISTRO_LABEL,
                    expect=BISTRO_EXPECT)
        above = Row(card, shift_months(cutover, 7) + timedelta(days=22), -28_640, BISTRO, bank_category=BISTRO_LABEL,
                    expect=BISTRO_EXPECT)
        features["bank_label"] = ((below, above), [])

    below = Row(hh.CHECKING, _bank_day(shift_months(start, 10) + timedelta(days=4)), -6_317, GENERAL_STORE,
                bank_type="DEBIT_CARD", expect=_fallback(False))
    above = Row(hh.CHECKING, _bank_day(shift_months(end, -8) + timedelta(days=9)), -31_245, GENERAL_STORE,
                bank_type="DEBIT_CARD", expect=_fallback(True))
    features["fallback"] = ((below, above), [])

    threshold = uncategorized_review_cents(plan)
    for path, ((below, above), _) in features.items():
        if not -below.cents < threshold <= -above.cents:
            raise ValueError(f"{path}: the two purchases must lie below and from the review threshold")
    for path, ((below, above), entries) in features.items():
        if not all(fits(plan, r.account, r.date) for r in (below, above)):
            continue
        for file, key, entry in entries:
            plan.add(file, key, [entry])
        posted = [plan.post(r) for r in (below, above)]
        plan.plant(f"paths.{path}.below", posted[0])
        plan.plant(f"paths.{path}.above", posted[1])


# the occasional purchases: starter merchants where the list names one for the category ...
HOME_DEPOT = Merchant("THE HOME DEPOT #6512", "The Home Depot", "Housing", "Home Improvement", label="Home")
CVS = Merchant("CVS/PHARMACY #04521", "CVS Pharmacy", "Health", "Pharmacy", label="Health & Wellness")
DELTA = Merchant("DELTA AIR LINES 0000000000101", "Delta Air Lines", "Travel", "Flights", label="Travel")
MARRIOTT = Merchant("MARRIOTT HARBOR VIEW HOTEL", "Marriott", "Travel", "Lodging", label="Travel")
GREAT_CLIPS = Merchant("GREAT CLIPS #4410", "Great Clips", "Personal Care", "Hair/Beauty", label="Personal")
PETCO = Merchant("PETCO 1187", "Petco", "Pets", label="Shopping")
STATE_FARM = Merchant("STATE FARM INSURANCE PPD ID: 9000313004", "State Farm", "Insurance")
# ... and invented payees, each filed by a vendor pattern the demo adds, where it names none
FOOD_BANK = Merchant("RIVERBEND FOOD BANK DONATION", "Riverbend Food Bank", "Giving", "Charity",
                     label="Gifts & Donations")
TAX_OFFICE = Merchant("HARBOR COUNTY TAX OFFICE PPD ID: 3300418877", "Harbor County Tax Office", "Taxes",
                      "Property Tax")
STUDENT_LOANS = Merchant("EXAMPLE STUDENT LOANS PPD ID: 6100273395", "Example Student Loans", "Debt", "Student Loan")
OWN_PATTERNS = {
    FOOD_BANK: r"^RIVERBEND FOOD BANK\b",
    TAX_OFFICE: r"^HARBOR COUNTY TAX OFFICE\b",
    STUDENT_LOANS: r"^EXAMPLE STUDENT LOANS\b",
}

# (merchant, account, months after the plan's start, day of that month, cents)
OCCASIONAL = (
    (HOME_DEPOT, hh.CARD_ALEX, 3, 14, 18_462),
    (HOME_DEPOT, hh.CARD_ALEX, 20, 6, 7_318),
    (CVS, hh.CARD_SAM, 10, 9, 2_347),
    (CVS, hh.CARD_SAM, 22, 17, 4_106),
    (DELTA, hh.CARD_ALEX, 11, 3, 48_620),
    (MARRIOTT, hh.CARD_ALEX, 11, 19, 61_288),
    (GREAT_CLIPS, hh.CARD_SAM, 13, 22, 2_800),
    (GREAT_CLIPS, hh.CARD_SAM, 24, 5, 3_100),
    (PETCO, hh.CARD_SAM, 15, 11, 5_739),
    (STATE_FARM, hh.CHECKING, 2, 15, 61_240),
    (STATE_FARM, hh.CHECKING, 14, 15, 64_815),
    (FOOD_BANK, hh.CARD_ALEX, 5, 28, 15_000),
    (TAX_OFFICE, hh.CHECKING, 4, 10, 191_240),
    (TAX_OFFICE, hh.CHECKING, 16, 10, 198_765),
    (STUDENT_LOANS, hh.CHECKING, 8, 21, 235_000),
)


def occasional(plan: Plan) -> None:
    """The occasional purchases that fall inside the plan's window, and the demo's vendor pattern for each invented
    payee that has one."""
    added = set()
    for merchant, account, months, day, cents in OCCASIONAL:
        when = shift_months(plan.start, months) + timedelta(days=day - 1)
        card = hh.ACCOUNTS[account].kind == "credit-card"
        when = when if card else business_day_on_or_after(when)
        if not fits(plan, account, when):
            continue
        if merchant in OWN_PATTERNS and merchant not in added:
            added.add(merchant)
            plan.add(VENDORS, "patterns", [{"pattern": OWN_PATTERNS[merchant], "vendor": merchant.vendor,
                                           "category": merchant.category, "subcategory": merchant.subcategory}])
        plan.post(merchant.purchase(account, when, -cents, card=card, bank_type=None if card else "ACH_DEBIT"))

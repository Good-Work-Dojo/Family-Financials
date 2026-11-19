# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Income: Alex's and Sam's pay into joint checking, each recognised by an income rule naming the earner and the
employer, Alex changing jobs on the way; one deposit no rule names, large enough to be reviewed as income; a
merchant's refund, small enough to stay a purchase that reduces spending; and the savings account's monthly
interest, recognised by an income rule typed interest. Four behaviours, so that a profile can leave the pay and the
unknown deposit out and keep the rest."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from .. import household as hh
from ..dates import (
    business_day_on_or_after,
    business_day_on_or_before,
    last_business_day,
    month_end,
    month_starts,
    shift_months,
)
from ..ledger import Expect, Row, half_up
from . import fits
from .spending import TRADER_JOES

if TYPE_CHECKING:
    from ..plan import Plan

RULES = "category_rules.yaml"
JOB_CHANGE_MONTHS = 15           # Alex changes jobs in the month this many months before the end's month
WEEKS_WITHOUT_PAY = 3            # between the last pay of the old job and the first day of the new one
SAVINGS_APR_BP = 385             # the savings account's yearly rate, 3.85 percent, paid monthly on the month's last day


@dataclass(frozen=True)
class Payroll:
    """One job's pay: who earns it, from whom, the deposit's descriptor, the vendor that descriptor gives (its first
    four words once the starter's cleanup drops the `PPD ID:` metadata), the income rule's pattern and the net pay."""
    earner: str
    employer: str
    desc: str
    vendor: str
    pattern: str
    cents: int

    def rule(self) -> dict:
        return {"pattern": self.pattern, "subcategory": "Salary", "earner": self.earner, "employer": self.employer}

    def deposit(self, day: date) -> Row:
        return Row(hh.CHECKING, day, self.cents, self.desc, bank_type="ACH_CREDIT",
                   expect=Expect("income", "Income", "Salary", "type_default", self.vendor, earner=self.earner,
                                 employer=self.employer))


QUILLWORKS_PAY = Payroll(hh.ALEX, hh.QUILLWORKS, "QUILLWORKS DESIGN PAYROLL PPD ID: 4402918837",
                         "Quillworks Design Payroll", r"\bQUILLWORKS DESIGN PAYROLL\b", 328_417)
LARKSPUR_PAY = Payroll(hh.ALEX, hh.LARKSPUR, "LARKSPUR ROBOTICS DIRECT DEP PPD ID: 7730451196",
                       "Larkspur Robotics Direct Dep", r"\bLARKSPUR ROBOTICS\b", 374_152)
HARBOR_CITY_PAY = Payroll(hh.SAM, hh.HARBOR_CITY, "HARBOR CITY SCHOOLS PAYROLL PPD ID: 2215589034",
                          "Harbor City Schools Payroll", r"\bHARBOR CITY SCHOOLS\b", 411_806)

UNKNOWN_CREDIT_CENTS = 120_000
UNKNOWN_CREDIT = Expect("income", "Income", "Other", "fallback", "Remote Online Deposit", needs_review=True)
REFUND_CENTS = 3_850
INTEREST = Expect("interest", "Income", "Interest", "type_default", "Interest Earned")
INTEREST_RULE = {"pattern": "^INTEREST EARNED$", "subcategory": "Interest", "type": "interest",
                 "accounts": [hh.SAVINGS]}


def semimonthly(first: date, last: date) -> list[date]:
    """Paydays on the 15th (the Friday before when it falls on a weekend) and the last business day of each month,
    `first` to `last` included."""
    days = [d for m in month_starts(first, last)
            for d in (business_day_on_or_before(m.replace(day=15)), last_business_day(m))]
    return [d for d in days if first <= d <= last]


def monthly(first: date, last: date) -> list[date]:
    """Paydays on the last business day of each month, `first` to `last` included."""
    return [d for d in (last_business_day(m) for m in month_starts(first, last)) if first <= d <= last]


def pay(plan: Plan) -> None:
    """Alex's and Sam's pay into checking, each job with its income rule and its handle (the first and the last
    deposit); a job whose paydays all fall outside the window is left out whole.

    Alex is paid by Quillworks Design up to the 15th of the change month, then goes three weeks without work, then is
    paid by Larkspur Robotics from the first payday after starting there (a window that starts after the change sees
    only the second job)."""
    if not plan.has(hh.CHECKING):
        return
    start, end = plan.opened(hh.CHECKING), plan.end
    left = business_day_on_or_before(shift_months(end, -JOB_CHANGE_MONTHS).replace(day=15))
    joined = left + timedelta(weeks=WEEKS_WITHOUT_PAY)
    for job, days, handle in ((QUILLWORKS_PAY, semimonthly(start, left), "income.alex_first"),
                              (LARKSPUR_PAY, semimonthly(max(start, joined), end), "income.alex_second"),
                              (HARBOR_CITY_PAY, monthly(start, end), "income.sam")):
        if days:
            plan.add(RULES, "income_rules", [job.rule()])
            deposits = [plan.post(job.deposit(d)) for d in days]
            plan.plant(handle, (deposits[0], deposits[-1]))


def unknown_credit(plan: Plan) -> None:
    """A deposit no rule names, four months before the end's month: income, flagged for review."""
    day = business_day_on_or_after(shift_months(plan.end, -4) + timedelta(days=8))
    if fits(plan, hh.CHECKING, day):
        plan.plant("income.unknown_credit", plan.post(Row(hh.CHECKING, day, UNKNOWN_CREDIT_CENTS,
                                                          "REMOTE ONLINE DEPOSIT", bank_type="CHECK_DEPOSIT",
                                                          expect=UNKNOWN_CREDIT)))


def grocery_return(plan: Plan) -> None:
    """A grocer's refund four months into checking's history: below the review threshold, so a purchase that nets
    spending."""
    if not plan.has(hh.CHECKING):
        return
    day = business_day_on_or_after(shift_months(plan.opened(hh.CHECKING), 4) + timedelta(days=17))
    if fits(plan, hh.CHECKING, day):
        plan.plant("income.refund", plan.post(TRADER_JOES.purchase(
            hh.CHECKING, day, REFUND_CENTS, card=False, bank_type="DEBIT_CARD", desc="TRADER JOE'S #552 RETURN")))


def savings_interest(plan: Plan) -> None:
    """The savings interest on the last day of each month inside the window, at SAVINGS_APR_BP on the opening
    balance and the interest paid so far (a balance of its own, so another behaviour's deposits never move these
    rows), rounded half up to the cent; its income rule and its handle (the first and the last row)."""
    if not plan.has(hh.SAVINGS):
        return
    balance, rows = plan.sources(hh.SAVINGS).opening_cents, []
    for m in month_starts(plan.opened(hh.SAVINGS), plan.end):
        day = month_end(m)
        if day > plan.end:
            break
        cents = half_up(balance * SAVINGS_APR_BP, 12 * 10_000)
        rows.append(plan.post(Row(hh.SAVINGS, day, cents, "INTEREST EARNED", expect=INTEREST)))
        balance += cents
    if rows:
        plan.add(RULES, "income_rules", [INTEREST_RULE])
        plan.plant("income.interest", (rows[0], rows[-1]))


# the four, in the order a profile runs them (pay's income rules come before the interest's)
INCOME = (pay, unknown_credit, grocery_return, savings_interest)

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The household's balance sheet beyond its everyday accounts.

- Housing: the home, an asset the household entered in the app with three values, rising: on the history's first day,
  about a year before the end and about a month before it (a value whose day is not after the history's first is left
  out, the first day then taking the value in force on it), so its newest value is what it is worth today whatever the
  history holds; and its mortgage. The loan was made
  on the first of the month LOAN.months_before_start months before the history starts and is paid on the first
  business day of every month after that: the level payment of its terms (principal and interest) plus the escrow,
  one fixed debit on joint checking that the demo's own vendor pattern files as Housing / Mortgage, a monthly bill
  the recurring detector lists. Each payment's interest is the balance times the monthly rate, rounded to the cent;
  the rest of the level payment is principal, which lowers the balance. The lender's feed reports the balance from
  the feed's start, opening at the schedule's balance after every payment made before then.
- Investing: a joint brokerage on margin and Alex's 401(k), both reported by the feed as balances from its start.
  Each month checking sends the brokerage a fixed contribution, a row the demo's own account-scoped rule types as an
  investment (the brokerage imports no rows, so its other leg is never missing data) and, once the feed reports the
  brokerage, the same amount onto its balance the same day; the 401(k) gains a fixed payroll contribution on the
  month's last business day. At each month end each account moves by its own market return, a whole number of basis
  points from MARKET_BP on its positions (the brokerage's gross value, its margin loan aside). The brokerage's
  balance is its positions less a constant margin loan; the newest snapshot lists the positions as three funds. The
  feed stops reporting the 401(k) for its last two snapshots (the stale account): it reports a balance and no rows, so
  net worth carries its last balance and no month's cash-flow coverage depends on it.
- Goals: the household's target asset mix and its monthly budgets.
- Savings: Robin's savings account gets an allowance from joint checking each month, a pair in the starter's transfer
  wording, and its own interest at each month end; a joint certificate earns interest at each month end, a row on
  each one to the end.
- What else the feed reports: an old closed account no registry entry maps, in every snapshot, and Sam's card's
  credit limit.
- The car: Sam's, bought from a dealer about eight months before the end, stated in accounts.yaml with the day it was
  acquired and one value six weeks later, so exactly one month end falls between them, on which the car is carried
  back at that value. That day savings moves the price to checking and checking pays the dealer
  (one purchase the demo's own vendor pattern files under Transport), so checking's balance is what it would be
  without the car and every month's move to savings is unchanged."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .. import household as hh
from ..dates import (
    business_day_on_or_after,
    last_business_day,
    month_end,
    month_starts,
    shift_months,
)
from ..ledger import Expect, Row, half_up
from ..plan import AppAsset, Fund, Portfolio, Recurring, UnmappedFeed, manual_id
from . import MIN_CHARGES, Merchant, fits
from .income import INTEREST

if TYPE_CHECKING:
    from ..plan import Plan

ACCOUNTS = "accounts.yaml"
ASSUMPTIONS = "assumptions.yaml"
RULES = "category_rules.yaml"
TRANSFERS = "transfer_rules.yaml"
VENDORS = "vendor_rules.yaml"


def _dollars(cents: int) -> float:
    return float(Decimal(cents) / 100)


# ------------------------------------------------------------------ housing

@dataclass(frozen=True)
class Loan:
    """A fixed-rate loan: what was borrowed, the yearly rate in percent, its length, the escrow added to each
    payment and held for taxes and insurance, and how long before the history starts it was made."""
    amount_cents: int
    rate_pct: Decimal
    term_months: int
    escrow_cents: int
    escrow_balance_cents: int
    months_before_start: int

    def origination(self, start: date) -> date:
        return shift_months(start, -self.months_before_start)

    def payment_cents(self) -> int:
        """The level monthly payment, principal and interest: P·r / (1 − (1 + r)^−n), r the monthly rate."""
        r = self.rate_pct / 1200
        level = Decimal(self.amount_cents) * r / (1 - (1 + r) ** -self.term_months)
        return half_up(*level.as_integer_ratio())

    def schedule(self, start: date, last: date) -> list[tuple[date, int, int]]:
        """(day, principal, balance after) of every payment up to `last`: on the first business day of each month
        after the loan was made's."""
        r, payment, balance = self.rate_pct / 1200, self.payment_cents(), self.amount_cents
        out = []
        for month in month_starts(shift_months(self.origination(start), 1), last):
            day = business_day_on_or_after(month)
            if day > last:
                break
            interest = half_up(*(Decimal(balance) * r).as_integer_ratio())
            balance -= payment - interest
            out.append((day, payment - interest, balance))
        return out


LOAN = Loan(amount_cents=45_200_000, rate_pct=Decimal("6.125"), term_months=360, escrow_cents=61_200,
            escrow_balance_cents=244_800, months_before_start=36)
LENDER = Merchant("EXAMPLE HOME LOANS MTG PYMT PPD ID: 8830227714", hh.LENDER, "Housing", "Mortgage")
LENDER_PATTERN = r"^EXAMPLE HOME LOANS\b"
MORTGAGE_CADENCE = ("monthly", "fixed")      # the payments' regularity, in the recurring detector's terms
HOME = "Home"                                 # the home as the household entered it in the app: its name and id
HOME_ID = "family-home"
# its values, oldest first: on the history's first day, then these many months before the end's month (on the
# month's first day); the last is what it is worth today
HOME_VALUES_CENTS = (59_000_000, 61_500_000, 64_000_000)
HOME_REVALUED_MONTHS = (12, 1)
APP_EDIT_HOUR = 12                            # the local hour the household entered the home's newest value


def home_values(plan: Plan) -> tuple[tuple[date, int], ...]:
    """The home's values: each later value whose day is after the history's first day, and before them on that first
    day the value in force then (the first of HOME_VALUES_CENTS when every later one fits)."""
    later = [(shift_months(plan.end, -months), cents)
             for months, cents in zip(HOME_REVALUED_MONTHS, HOME_VALUES_CENTS[1:], strict=True)]
    kept = [(day, cents) for day, cents in later if day > plan.start]
    first = HOME_VALUES_CENTS[len(HOME_VALUES_CENTS) - 1 - len(kept)]
    return ((plan.start, first), *kept)


def housing(plan: Plan) -> None:
    """The mortgage (when the profile holds it): the loan's balance from the feed's start, its terms in
    accounts.yaml, the home the household entered in the app beside it (last edited on its newest value's day), and
    every payment the window holds on checking."""
    if not plan.has(hh.MORTGAGE):
        return
    schedule = LOAN.schedule(plan.start, plan.end)
    opened = plan.opened(hh.MORTGAGE)
    before = [balance for day, _, balance in schedule if day < opened]
    plan.open(hh.MORTGAGE, -(before[-1] if before else LOAN.amount_cents))
    for day, principal, _ in schedule:
        if day >= opened:
            plan.ledger.adjust(hh.MORTGAGE, day, principal)
    plan.put(ACCOUNTS, "accounts", {hh.MORTGAGE: {"mortgage": True, "loan": {
        "rate": float(LOAN.rate_pct), "monthly_payment": _dollars(LOAN.payment_cents()),
        "monthly_escrow": _dollars(LOAN.escrow_cents), "escrow_balance": _dollars(LOAN.escrow_balance_cents),
        "origination": {"date": LOAN.origination(plan.start).isoformat(), "amount": _dollars(LOAN.amount_cents)},
        "term_months": LOAN.term_months}}})
    values = home_values(plan)
    edited = datetime.combine(values[-1][0], time(APP_EDIT_HOUR), tzinfo=ZoneInfo(hh.TIMEZONE))
    plan.enter(AppAsset(HOME_ID, HOME, hh.JOINT, "House", False, values, edited))
    plan.plant("mortgage.account", hh.MORTGAGE)
    plan.plant("home.entry", HOME_ID)
    paid = [plan.post(LENDER.purchase(hh.CHECKING, day, -(LOAN.payment_cents() + LOAN.escrow_cents), card=False,
                                      bank_type="ACH_DEBIT"))
            for day, _, _ in schedule if fits(plan, hh.CHECKING, day)]
    if paid:
        plan.add(VENDORS, "patterns", [{"pattern": LENDER_PATTERN, "vendor": LENDER.vendor,
                                        "category": LENDER.category, "subcategory": LENDER.subcategory}])
        plan.plant("mortgage.payment", paid[0])
    if len(paid) >= MIN_CHARGES:
        plan.declare_recurring(Recurring(LENDER.vendor, hh.CHECKING, *MORTGAGE_CADENCE))


# ------------------------------------------------------------------ investing

# invented funds under symbols no US-listed fund can carry (a US ticker is letters only)
PORTFOLIO = Portfolio(funds=(Fund("DEMO1", "Sample Total Market Fund", 60, 11_240),
                             Fund("DEMO2", "Sample International Fund", 25, 5_875),
                             Fund("DEMO3", "Sample Bond Fund", 15, 2_430)),
                      loan_cents=1_250_000, cost_percent=80)
MARGIN_APR = 0.0825
MARKET_BP = (-300, 450)                      # a month's market return, in basis points, both ends included
CONTRIBUTION = "EXAMPLE BROKERAGE CONTRIB PPD ID: 5520183374"
CONTRIBUTION_CENTS = 125_000                 # not a whole hundred, so no move to savings ever has its amount
CONTRIBUTION_DAY = 16                        # the business day from the 16th, after the mid-month pay
CONTRIBUTION_RULE = {"name": "brokerage-contribution", "pattern": r"^EXAMPLE BROKERAGE CONTRIB\b",
                     "type": "investment", "accounts": [hh.CHECKING], "counter_hint": hh.BROKERAGE}
CONTRIBUTION_EXPECT = Expect("investment", "Internal", "investment", "internal", "Example Brokerage Contrib",
                             internal=True)
PAYROLL_DEFERRAL_CENTS = 180_000             # Alex's 401(k) deferral and the employer's match, each month


def _contribute(plan: Plan, day: date) -> Row:
    """One contribution: the debit on checking and, once the feed reports the brokerage, the same amount onto its
    balance that day (before, the amount is in the balance the brokerage opens with)."""
    row = plan.post(Row(hh.CHECKING, day, -CONTRIBUTION_CENTS, CONTRIBUTION, bank_type="ACH_DEBIT",
                        expect=CONTRIBUTION_EXPECT))
    if fits(plan, hh.BROKERAGE, day):
        plan.ledger.adjust(hh.BROKERAGE, day, CONTRIBUTION_CENTS)
    return row


def _market(plan: Plan, account: str, stream: str, loan_cents: int) -> None:
    """Each month end from the account's opening to the end: its return on its positions (balance plus loan). A
    draw is taken for every month, so how far the window reaches moves no other month's return."""
    rng = plan.stream(stream)
    for month in month_starts(plan.opened(account), plan.end):
        bp = rng.randint(*MARKET_BP)
        day = month_end(month)
        if day > plan.end:
            continue
        cents = half_up((plan.ledger.balance(account, day) + loan_cents) * bp, 10_000)
        if cents:
            plan.ledger.adjust(account, day, cents)


def investing(plan: Plan) -> None:
    """The brokerage (its contributions from checking, its returns, its portfolio and margin loan) and the 401(k)
    (its payroll contributions and returns), each when the profile holds it, with their config."""
    if plan.has(hh.BROKERAGE):
        days = [business_day_on_or_after(m.replace(day=CONTRIBUTION_DAY)) for m in month_starts(plan.start, plan.end)]
        if plan.has(hh.CHECKING):
            sent = [_contribute(plan, d) for d in days if fits(plan, hh.CHECKING, d)]
            if sent:
                plan.add(TRANSFERS, "rules", [CONTRIBUTION_RULE])
        _market(plan, hh.BROKERAGE, "market", PORTFOLIO.loan_cents)
        plan.hold(hh.BROKERAGE, PORTFOLIO)
        plan.put(ACCOUNTS, "accounts", {hh.BROKERAGE: {"margin": True}})
        plan.put(ASSUMPTIONS, "household", {"margin_apr": MARGIN_APR})
        plan.plant("brokerage.account", hh.BROKERAGE)
        plan.plant("brokerage.symbols", [f.symbol for f in PORTFOLIO.funds])
        plan.plant("margin.loan_cents", PORTFOLIO.loan_cents)
    if plan.has(hh.RETIREMENT):
        for month in month_starts(plan.opened(hh.RETIREMENT), plan.end):
            if fits(plan, hh.RETIREMENT, last_business_day(month)):
                plan.ledger.adjust(hh.RETIREMENT, last_business_day(month), PAYROLL_DEFERRAL_CENTS)
        _market(plan, hh.RETIREMENT, "market-retirement", 0)
        account = hh.ACCOUNTS[hh.RETIREMENT]
        plan.put(ASSUMPTIONS, "household", {"retirement_accounts": {account.name: account.owner}})
        plan.plant("retirement.account", hh.RETIREMENT)
        stale(plan)


def stale(plan: Plan) -> None:
    """The feed leaves the 401(k) out of its last STALE_SNAPSHOTS snapshots when at least one snapshot from its
    connection on, before them, reports it (else it is reported to the end), planted as `stale.account`. A balance-only
    account, so the stale warning and the carried balance touch no month's cash-flow coverage."""
    if len(plan.reporting_days(hh.RETIREMENT)) > STALE_SNAPSHOTS:
        plan.stop_reporting(hh.RETIREMENT, STALE_SNAPSHOTS)
        plan.plant("stale.account", hh.RETIREMENT)


# ------------------------------------------------------------------ goals

# the target mix: percent of net worth by asset class; the home's equity (about a fifth of net worth) is the rest
TARGETS = {"Cash": 10, "Stocks/ETF": 30, "Retirement": 40}
BUDGETS = {"Groceries": 1100, "Dining": 450, "Transport": 350, "Utilities": 300}    # dollars a month


def goals(plan: Plan) -> None:
    """The target asset mix when the household invests, and the monthly budgets when it has everyday accounts."""
    if plan.has(hh.BROKERAGE) or plan.has(hh.RETIREMENT):
        plan.put(ASSUMPTIONS, "targets", TARGETS)
    if any(hh.ACCOUNTS[s.account].kind in ("checking", "savings", "credit-card") for s in plan.profile.accounts):
        plan.put(ASSUMPTIONS, "budgets", BUDGETS)


# ------------------------------------------------------------------ savings

ALLOWANCE_CENTS = 2_500
ALLOWANCE_DAY = 3                            # the business day from the 3rd of each month
TO_CHILD = f"ONLINE TRANSFER TO SAV XXXXXX{hh.CHILD_SAVINGS[-4:]}"
FROM_CHECKING = f"ONLINE TRANSFER FROM CHK XXXXXX{hh.CHECKING[-4:]}"
CHILD_APR_BP = 200                           # Robin's savings: 2.00 percent a year, paid each month end
CHILD_INTEREST_RULE = {"pattern": "^INTEREST EARNED$", "subcategory": "Interest", "type": "interest",
                       "accounts": [hh.CHILD_SAVINGS]}
CERTIFICATE_APR_BP = 450                     # the certificate: 4.50 percent a year, credited each month end
CERTIFICATE_INTEREST_RULE = {"pattern": "^INTEREST EARNED$", "subcategory": "Interest", "type": "interest",
                             "accounts": [hh.CERTIFICATE]}
STALE_SNAPSHOTS = 2                          # the feed leaves the 401(k) out of this many of its last snapshots


def _transfer(vendor: str, pair: str) -> Expect:
    return Expect("transfer", "Internal", "transfer", "internal", vendor, internal=True, pair=pair)


def allowance(plan: Plan) -> None:
    """Robin's savings: each month the allowance from checking (when the household has checking) and the interest
    on the account's balance at the month end, from the month it opens; the first allowance planted as
    `child.pair`."""
    child = hh.CHILD_SAVINGS
    if not plan.has(child):
        return
    plan.plant("child.account", child)
    balance, pairs, interest = plan.sources(child).opening_cents, [], []
    for month in month_starts(plan.opened(child), plan.end):
        day = business_day_on_or_after(month.replace(day=ALLOWANCE_DAY))
        if fits(plan, hh.CHECKING, day) and fits(plan, child, day):
            key = f"allowance.{month:%Y-%m}"
            pairs.append(plan.move(
                Row(hh.CHECKING, day, -ALLOWANCE_CENTS, TO_CHILD, bank_type="ACH_DEBIT",
                    expect=_transfer("Online Transfer To Sav", key)),
                Row(child, day, ALLOWANCE_CENTS, FROM_CHECKING, expect=_transfer("Online Transfer From Chk", key))))
            balance += ALLOWANCE_CENTS
        day = month_end(month)
        cents = half_up(balance * CHILD_APR_BP, 12 * 10_000)
        if day <= plan.end and cents:
            interest.append(plan.post(Row(child, day, cents, "INTEREST EARNED", expect=INTEREST)))
            balance += cents
    if interest:
        plan.add(RULES, "income_rules", [CHILD_INTEREST_RULE])
    if pairs:
        plan.plant("child.pair", pairs[0])


def certificate(plan: Plan) -> None:
    """The certificate's monthly interest, a row at each month end from the day it opens to the end, recognised by the
    demo's income rule for the account."""
    account = hh.CERTIFICATE
    if not plan.has(account):
        return
    interest = []
    for month in month_starts(plan.opened(account), plan.end):
        day = month_end(month)
        if day > plan.end:
            break
        cents = half_up(plan.ledger.balance(account, day) * CERTIFICATE_APR_BP, 12 * 10_000)
        if cents:
            interest.append(plan.post(Row(account, day, cents, "INTEREST EARNED", expect=INTEREST)))
    if interest:
        plan.add(RULES, "income_rules", [CERTIFICATE_INTEREST_RULE])


# ------------------------------------------------------------------ what else the feed reports

OLD_ACCOUNT = "ACT-demo-9999"                # an old joint savings account, closed before the feed connected


def feed_edges(plan: Plan) -> None:
    """The closed account in every snapshot (a warning each ingest names), when the feed reports anything, and the
    credit limit Sam's card reports, planted as `card.limit_cents`."""
    if not plan.snapshot_days():
        return
    plan.report_unmapped(UnmappedFeed(OLD_ACCOUNT, "Old Joint Savings", hh.BANK, 0,
                                      plan.feed_start - timedelta(days=1)))
    plan.plant("unmapped.feed_id", OLD_ACCOUNT)
    if plan.has(hh.CARD_SAM) and plan.sources(hh.CARD_SAM).feed and plan.sources(hh.CARD_SAM).feed_limit_cents:
        plan.plant("card.limit_cents", plan.sources(hh.CARD_SAM).feed_limit_cents)


# ------------------------------------------------------------------ the car

CAR = "Sedan"                                # the car's manual entry: its name (its id is manual_id's) and owner
CAR_OWNER = hh.SAM
DEALER = Merchant("EXAMPLE MOTORS SALES PPD ID: 6610394852", hh.DEALER, "Transport", "Car Payment")
DEALER_PATTERN = r"^EXAMPLE MOTORS\b"
CAR_PRICE_CENTS = 2_185_000                  # what checking pays the dealer: under savings' balance in every profile
CAR_VALUE_CENTS = 2_100_000                  # what the household states it is worth, CAR_VALUED_AFTER later
CAR_BOUGHT_MONTHS = 8                        # bought this many months before the end's month ...
CAR_BOUGHT_DAY = 12                          # ... on the business day from this day of that month
# Bought between the 12th and the 14th, valued 42 days later: in the next month, before its end (the 14th of
# February plus six weeks is the 28th of March), so the month end of the month it was bought, and that one alone,
# falls between the two days and holds the car carried back at its value.
CAR_VALUED_AFTER = timedelta(weeks=6)
FROM_SAVINGS = f"ONLINE TRANSFER FROM SAV XXXXXX{hh.SAVINGS[-4:]}"
TO_CHECKING = f"ONLINE TRANSFER TO CHK XXXXXX{hh.CHECKING[-4:]}"


def car(plan: Plan) -> None:
    """The car (when checking and savings both hold the day it was bought): savings' transfer of the price to
    checking, a pair in the starter's transfer wording, and checking's payment to the dealer the same day, filed by the
    demo's vendor pattern; its accounts.yaml entry, acquired that day and valued CAR_VALUED_AFTER later. Planted:
    `car.entry` (the entry's id) and `car.purchase` (the payment)."""
    day = business_day_on_or_after(shift_months(plan.end, -CAR_BOUGHT_MONTHS).replace(day=CAR_BOUGHT_DAY))
    valued = day + CAR_VALUED_AFTER
    if not (fits(plan, hh.CHECKING, day) and fits(plan, hh.SAVINGS, day) and valued <= plan.end):
        return
    plan.move(Row(hh.SAVINGS, day, -CAR_PRICE_CENTS, TO_CHECKING,
                  expect=_transfer("Online Transfer To Chk", "car.funding")),
              Row(hh.CHECKING, day, CAR_PRICE_CENTS, FROM_SAVINGS, bank_type="ACH_CREDIT",
                  expect=_transfer("Online Transfer From Sav", "car.funding")))
    purchase = plan.post(DEALER.purchase(hh.CHECKING, day, -CAR_PRICE_CENTS, card=False, bank_type="ACH_DEBIT"))
    plan.add(VENDORS, "patterns", [{"pattern": DEALER_PATTERN, "vendor": DEALER.vendor, "category": DEALER.category,
                                    "subcategory": DEALER.subcategory}])
    plan.add(ACCOUNTS, "manual_entries", [{"account": CAR, "institution": hh.DEALER, "owner": CAR_OWNER,
                                           "asset_class": "Car", "value": _dollars(CAR_VALUE_CENTS),
                                           "as_of": valued.isoformat(), "acquired": day.isoformat()}])
    plan.plant("car.entry", manual_id(CAR))
    plan.plant("car.purchase", purchase)

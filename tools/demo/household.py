# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The Sample household: the invented family every demo profile describes. Constants only — the people, their
employers, the institutions (the car dealer among them) and the nine accounts a profile may hold — so that every
profile, behaviour and test names them from here."""
from __future__ import annotations

from dataclasses import dataclass

NAME = "The Sample household"
SURNAME = "Sample"
JOINT = "Joint"
TIMEZONE = "America/New_York"


@dataclass(frozen=True)
class Member:
    name: str
    role: str                          # adult | child
    birth_year: int


ALEX = "Alex"
SAM = "Sam"
ROBIN = "Robin"
JAMIE = "Jamie"
MEMBERS = (Member(ALEX, "adult", 1986), Member(SAM, "adult", 1988),
           Member(ROBIN, "child", 2016), Member(JAMIE, "child", 2019))

# employers: Alex's first and second, then Sam's
QUILLWORKS = "Quillworks Design"
LARKSPUR = "Larkspur Robotics"
HARBOR_CITY = "Harbor City Schools"

# institutions
BANK = "Example Bank"
CARD_ISSUER = "Example Card"
BROKER = "Example Brokerage"
RETIREMENT_PLAN = "Example Retirement"
LENDER = "Example Home Loans"
DEALER = "Example Motors"                # where the household bought its car

# account ids
CHECKING = "demo_checking_0101"          # joint checking
SAVINGS = "demo_savings_0202"            # joint savings
CARD_ALEX = "demo_card_0303"
CARD_SAM = "demo_card_0404"
BROKERAGE = "demo_brokerage_0505"
RETIREMENT = "demo_retirement_0606"
MORTGAGE = "demo_mortgage_0707"
CHILD_SAVINGS = "demo_savings_0808"      # Robin's
CERTIFICATE = "demo_savings_0909"        # a certificate of deposit


@dataclass(frozen=True)
class Account:
    id: str
    kind: str                          # checking | savings | credit-card | brokerage | loan
    name: str
    institution: str
    owner: str                         # a member's name or the joint label
    asset_class: str                   # the class net worth counts its balance under, as accounts.yaml states it

    @property
    def feed_id(self) -> str:
        """The id the bank feed reports the account under: `ACT-demo-` and the id's last four digits."""
        return f"ACT-demo-{self.id[-4:]}"


ACCOUNTS: dict[str, Account] = {a.id: a for a in (
    Account(CHECKING, "checking", "Joint Checking", BANK, JOINT, "Cash"),
    Account(SAVINGS, "savings", "Joint Savings", BANK, JOINT, "Cash"),
    Account(CARD_ALEX, "credit-card", "Alex's Card", CARD_ISSUER, ALEX, "Other Debt"),
    Account(CARD_SAM, "credit-card", "Sam's Card", CARD_ISSUER, SAM, "Other Debt"),
    Account(BROKERAGE, "brokerage", "Joint Brokerage", BROKER, JOINT, "Stocks/ETF"),
    Account(RETIREMENT, "brokerage", "Alex's 401(k)", RETIREMENT_PLAN, ALEX, "Retirement"),
    Account(MORTGAGE, "loan", "Home Mortgage", LENDER, JOINT, "Mortgage"),
    Account(CHILD_SAVINGS, "savings", "Robin's Savings", BANK, ROBIN, "Cash"),
    Account(CERTIFICATE, "savings", "Savings Certificate", BANK, JOINT, "Cash"),
)}

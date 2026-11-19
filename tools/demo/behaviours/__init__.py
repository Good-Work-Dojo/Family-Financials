# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Behaviours: each posts one strand of the household's history into a plan (see `tools.demo.plan`). This module
holds what several of them share: a merchant from a vendor list and the purchase row it makes, the review thresholds
of the generated assumptions.yaml, and the test of whether a day falls inside an account's history."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from ..ledger import Expect, Row, cents_of

if TYPE_CHECKING:
    from ..plan import Plan

MIN_CHARGES = 3              # the charges the recurring detector needs on one account before it lists a series


def uncategorized_review_cents(plan: Plan) -> int:
    """uncategorized_review_threshold in cents: a purchase no rule places is reviewed from here."""
    return cents_of(plan.assumption("uncategorized_review_threshold"))


def venmo_review_cents(plan: Plan) -> int:
    """venmo_review_threshold in cents: a payment-app row no rule names is reviewed from here."""
    return cents_of(plan.assumption("venmo_review_threshold"))


def fits(plan: Plan, account: str, day: date) -> bool:
    """Is `account` one of the plan's, and `day` inside its history (from the day it opens to the plan's end)?"""
    return plan.has(account) and plan.opened(account) <= day <= plan.end


@dataclass(frozen=True)
class Merchant:
    """A merchant of the vendor list (`starter/vendor_rules.yaml`, or an entry the demo adds ahead of it): the
    descriptor the bank prints, which the entry's pattern matches once the starter's cleanup has run, and the entry's
    vendor, category and subcategory, which file every purchase there. `label` is the card issuer's own category,
    printed on card rows only."""
    desc: str
    vendor: str
    category: str
    subcategory: str | None = None
    label: str | None = None

    def purchase(self, account: str, day: date, cents: int, *, card: bool, bank_type: str | None = None,
                 desc: str | None = None) -> Row:
        """A row at this merchant (negative: a charge, positive: a refund), filed by its vendor rule."""
        return Row(account, day, cents, desc or self.desc, bank_type=bank_type,
                   bank_category=self.label if card else None,
                   expect=Expect("purchase", self.category, self.subcategory, "vendor_rule", self.vendor))

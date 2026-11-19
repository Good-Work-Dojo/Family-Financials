# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Income identification on positive bank-account rows (runs after transfer
Phase A so internal movement is already typed) + income-phase detection."""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Collection, Sequence

from ..models import INCOME_CATEGORY, INCOME_OTHER_SUB, Txn


def _earner_from_subcategory(sub: str | None, members: Collection[str]) -> str | None:
    """'Salary - Alex' / 'Consulting - Sam' -> the person after the dash, trimmed, when that is a member's name;
    else None ('Salary - Bonus Pool' names no one)."""
    if sub and " - " in sub:
        who = sub.rsplit(" - ", 1)[1].strip()
        return who if who in members else None
    return None


class IncomeIdentifier:
    def __init__(self, category_rules: dict, assumptions: dict,
                 bank_account_ids: set[str], *, members: Collection[str] = ()):
        """`members` are household.yaml's member names (Config.adults and Config.children): the only names an earner
        derived from a subcategory may be."""
        self.rules = [
            (re.compile(r["pattern"], re.IGNORECASE), r)
            for r in category_rules.get("income_rules", [])
        ]
        self.threshold = assumptions.get("unknown_credit_review_threshold", 500) * 100
        self.bank_accounts = bank_account_ids
        self.members = frozenset(members)

    def apply(self, txns: list[Txn]) -> None:
        for t in txns:
            if t.amount_cents <= 0 or t.type != "purchase":
                continue
            if t.account_id not in self.bank_accounts:
                continue
            matched = False
            for pat, rule in self.rules:
                if rule.get("accounts") and t.account_id not in rule["accounts"]:
                    continue
                if pat.search(t.description_raw):
                    t.type = rule.get("type", "income")
                    t.category = INCOME_CATEGORY
                    t.category_source = "type_default"
                    t.subcategory = rule.get("subcategory")
                    # the rule's earner: key (a member, checked at load) wins; else a member a "Salary - X"-style
                    # subcategory names, else none
                    t.earner = rule.get("earner") or _earner_from_subcategory(t.subcategory, self.members)
                    # the rule's optional employer: label, which detect_income_phases groups by
                    t.employer = rule.get("employer")
                    matched = True
                    break
            if not matched and t.amount_cents >= self.threshold:
                t.type = "income"
                t.category = INCOME_CATEGORY
                t.subcategory = INCOME_OTHER_SUB
                t.category_source = "fallback"
                t.needs_review = True


def detect_income_phases(txns: list[Txn], owners: Sequence[str]) -> list[dict]:
    """One employment phase per (earner, employer): the income rows an income rule with an `employer:` label typed
    (IncomeIdentifier records the label on the row), grouped by who earned them and that label. Two rules sharing a
    label and an earner make one phase; a row without an employer or without an earner makes none, and a split
    parent or part is no deposit. The earner is the row's final one (an override may change it), while the label is
    the matched rule's. A phase spans its group's first to last deposit, and its monthly average divides the total by
    the calendar months the span touches. Rows come in `owners` order (earners it does not list last, by name), then
    by start, then by employer.
    Exported to assumptions.json `income_phases`, which the dashboard draws as bands on the cash-flow chart."""
    groups: dict[tuple[str, str], list[Txn]] = defaultdict(list)
    for t in txns:
        if (t.type == "income" and t.category == INCOME_CATEGORY and t.earner and t.employer
                and not t.is_parent and t.parent_id is None):
            groups[(t.earner, t.employer)].append(t)

    phases: list[dict] = []
    for (person, employer), rows in groups.items():
        dates = sorted(t.date for t in rows)
        start, end = dates[0], dates[-1]
        total = sum(t.amount_cents for t in rows)
        months = max(1, (int(end[:4]) - int(start[:4])) * 12 + int(end[5:7]) - int(start[5:7]) + 1)
        phases.append({"person": person, "phase": employer, "start": start, "end": end,
                       "deposits": len(rows), "total_cents": total,
                       "avg_monthly_cents": total // months})
    rank = {name: i for i, name in enumerate(owners)}
    phases.sort(key=lambda p: (rank.get(p["person"], len(rank)), p["person"], p["start"], p["phase"]))
    return phases

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Categorization — precedence, one row at a time, first hit wins:

  1. type-driven: INTERNAL_TYPES rows become Internal/<type> (`internal`); income and fee
     rows get Income / Fees & Interest when nothing upstream set a category
     (`type_default`), and keep a category a parser or the IncomeIdentifier already set
     (stamped `parser` only when nobody stamped it yet). These names, and the fallback's
     in step 7, are the reserved ones (etl/models.py RESERVED)
  2. a category a parser pre-set on a purchase                                  `parser`
  3. explicit vendor rule `category:` (vendor_rules.yaml patterns)              `vendor_rule`
  4. learned vendor rule (scenarios/learned_rules.json: the family's confirmed review answers,
     3-of-5 agreement or a vendor-scope answer — etl/enrich/learned.py), looked up by
     vendor.casefold(); returns here, so it never sets needs_review; it keeps the rulebook's
     tags (the keyword hit's, else the bank label's) — a learned category never strips
     `kids`/`home`                                                                  `learned`
  5. keyword rules (etl/enrich/rules.py): stage pre -> default -> post, file order
     within a stage, first match wins; the match target is the cleaned descriptor
     OR the raw one, so `^CHECK \\d+$` still hits after the cleanup ate the digits  `keyword`
  6. the bank's own label (bank_category_map.yaml) when no keyword rule hit      `bank_label`
  7. Cash & Misc / Uncategorized (+ needs_review at >= the review threshold,    `fallback`
     with review_reason "uncategorized" when nothing flagged the row before)

Every branch stamps `category_source` (etl/models.py CATEGORY_SOURCES) and never overwrites
a stamp an earlier stage left — provenance is annotation, it never decides a category.
Answers, overrides and the pipeline's post-matching resync (`set_internal`) re-stamp
afterwards. Vendor patterns compile through rules.compile_rules as DATA only (their
`category:` feeds step 3); VendorNormalizer owns the vendor matching and its own target rule.
"""
from __future__ import annotations

import dataclasses

from ..models import (
    FALLBACK_CATEGORY,
    FEES_CATEGORY,
    INCOME_CATEGORY,
    INCOME_TYPES,
    INTERNAL_CATEGORY,
    INTERNAL_TYPES,
    UNCATEGORIZED_SUB,
    Txn,
)
from .rules import STAGES, Rule, compile_rules, rule_matches

# Txn.review_reason of a purchase step 7 files under the fallback pair and flags for review
UNCATEGORIZED_REASON = "uncategorized"


@dataclasses.dataclass(frozen=True)
class Placement:
    """Where steps 3-7 file a purchase (Categorizer.placement): its category, subcategory and `category_source`, the
    tags the step adds and whether it sends the row to review."""
    category: str
    subcategory: str | None
    source: str
    tags: tuple[str, ...] = ()
    review: bool = False


def set_internal(t: Txn) -> None:
    """Internal money movement: category follows the type. Shared by the Categorizer and
    the pipeline's post-matching resync (rows re-typed internal during phase B/C)."""
    t.category, t.subcategory = INTERNAL_CATEGORY, t.type
    t.category_source = "internal"


class Categorizer:
    def __init__(self, category_rules: dict, bank_category_map: dict, vendor_rules: dict,
                 assumptions: dict, account_tags: dict[str, list[str]],
                 owner_of: dict[str, str] | None = None,
                 learned: dict[str, dict] | None = None):
        """`owner_of` (account id -> owner, Config.owner_of()) only serves `owner` conditions in
        structured rules; without it, or for an account missing from it, every owner op (`isNot`
        included) is false. `learned` is learned.as_rules(doc): vendor.casefold() ->
        {category, subcategory} (step 4); None / {} means nothing learned."""
        # file order, NOT precedence order: precedence is stage-then-file — see by_stage / first_hit
        self.rules = compile_rules(category_rules.get("keyword_rules", []), source="keyword_rules")
        self.by_stage = {s: [r for r in self.rules if r.stage == s] for s in STAGES}
        self.bank_category_map = bank_category_map
        # vendor patterns as data: the entries carrying a category feed precedence step 3
        self.vendor_cat = {r.set["vendor"]: r.set
                           for r in compile_rules(vendor_rules.get("patterns", []), source="vendor_rules")
                           if "category" in r.set}
        self.review_threshold = assumptions.get("uncategorized_review_threshold", 250) * 100
        self.account_tags = account_tags
        owners = dict(owner_of or {})
        self.owner_of = lambda account_id: owners.get(account_id, "")
        # learned vendor rules, keyed by vendor.casefold() — casefolded again here so a
        # hand-built dict in a test or a future caller cannot miss on case
        self.learned = {str(k).casefold(): v for k, v in (learned or {}).items()}

    def apply(self, txns: list[Txn], cleaner) -> None:
        for t in txns:
            tags = set(t.tags) | set(self.account_tags.get(t.account_id, []))

            if t.type in INTERNAL_TYPES:
                set_internal(t)
            elif t.type in INCOME_TYPES or t.type == "fee":
                if not t.category:
                    t.category = INCOME_CATEGORY if t.type in INCOME_TYPES else FEES_CATEGORY
                    t.category_source = "type_default"
                elif not t.category_source:
                    t.category_source = "parser"     # dividends / margin interest set by a parser
            else:
                self._categorize_purchase(t, tags, cleaner)

            t.tags = sorted(tags)

    def first_hit(self, t: Txn, cleaner) -> Rule | None:
        """The keyword rule that wins for `t` under stage order (pre -> default -> post, file
        order within a stage), or None. Pure — `python -m etl rules` uses it for hit counts."""
        cleaned = cleaner(t.description_raw)
        for stage in STAGES:
            for rule in self.by_stage[stage]:
                if rule_matches(rule, t, cleaner=cleaner, owner_of=self.owner_of, cleaned=cleaned):
                    return rule
        return None

    def all_hits(self, t: Txn, cleaner) -> list[Rule]:
        """Every keyword rule that matches `t`, in precedence order (so all_hits(t)[0] is
        first_hit(t)). For `python -m etl rules overlaps`."""
        cleaned = cleaner(t.description_raw)
        return [rule for stage in STAGES for rule in self.by_stage[stage]
                if rule_matches(rule, t, cleaner=cleaner, owner_of=self.owner_of, cleaned=cleaned)]

    def placement(self, t: Txn, cleaner) -> Placement:
        """Where precedence steps 3-7 file `t`, from its vendor, descriptor, bank label and the fields structured
        rules read — whatever category it carries now. Pure: it changes nothing. The purchase path applies it
        (`_categorize_purchase`); `places` asks it on another stage's behalf."""
        # 3. explicit vendor rule
        ventry = self.vendor_cat.get(t.vendor or "")
        if ventry:
            return Placement(ventry["category"], ventry.get("subcategory"), "vendor_rule", tuple(ventry.get("tags", [])))
        # 4. learned vendor rule — the family's own confirmed answers replayed. Returns before
        #    the keyword/fallback paths exactly like step 3, so needs_review is never set here
        #    and a flag already True (transfer rules, a parser) stays True. It keeps the
        #    rulebook's tags, though: the keyword rule that would have hit (else the bank
        #    label) still tags the row, because the app's tag filter reads those tags and a
        #    learned category must never silently drop one.
        lentry = self.learned.get(t.vendor.casefold()) if t.vendor else None
        if lentry:
            hit = self.first_hit(t, cleaner)
            if hit is not None:
                learned_tags = hit.set.get("tags", [])
            elif t.bank_category and (m := self.bank_category_map.get(t.bank_category)):
                learned_tags = m.get("tags") or []
            else:
                learned_tags = []
            return Placement(lentry["category"], lentry.get("subcategory"), "learned", tuple(learned_tags))
        # 5. keyword rules get first shot at household-knowledge rules even when the bank has
        #    a label (e.g. a nursery school the bank files as Bills & Utilities): pre -> default -> post
        hit = self.first_hit(t, cleaner)
        # 6. bank label — used only when no keyword rule matched
        if hit is None and t.bank_category:
            m = self.bank_category_map.get(t.bank_category)
            if m:
                # a label's `tags:` written with no value is none, as the validator reads it
                return Placement(m["category"], m.get("subcategory"), "bank_label", tuple(m.get("tags") or ()))
        if hit:
            return Placement(hit.set["category"], hit.set.get("subcategory"), "keyword", tuple(hit.set.get("tags", [])),
                             review=bool(hit.set.get("needs_review")) and abs(t.amount_cents) >= self.review_threshold)
        # 7. fallback
        return Placement(FALLBACK_CATEGORY, UNCATEGORIZED_SUB, "fallback",
                         review=abs(t.amount_cents) >= self.review_threshold)

    def places(self, t: Txn, cleaner) -> bool:
        """Would steps 3-6 file `t` (anything but the fallback) were it an ordinary purchase? Read on a copy typed
        purchase, so a structured rule's `type` condition reads it as it would read such a row; `t` itself is never
        changed. The transfer engine asks it of a credit the income fallback typed, which never reached step 3."""
        return self.placement(dataclasses.replace(t, type="purchase"), cleaner).source != "fallback"

    def _categorize_purchase(self, t: Txn, tags: set, cleaner) -> None:
        if t.category:        # 2. a parser-set category wins
            if not t.category_source:
                t.category_source = "parser"
            return
        p = self.placement(t, cleaner)
        t.category, t.subcategory, t.category_source = p.category, p.subcategory, p.source
        tags.update(p.tags)
        if p.review:
            # the reason is recorded only when this step is what flags the row, so clearing it (the AI step files an
            # uncategorized row) never drops a flag something else raised
            if p.source == "fallback" and not t.needs_review and t.review_reason is None:
                t.review_reason = UNCATEGORIZED_REASON
            t.needs_review = True

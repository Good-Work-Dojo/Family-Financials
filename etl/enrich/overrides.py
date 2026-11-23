# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Per-transaction overrides from config/overrides.yaml — facts only the family
knows (e.g. "this pharmacy purchase was a birthday present, filed under Giving"). Applied last, highest precedence.
An entry matching zero rows produces a quality warning so a refreshed export
can't silently orphan these classifications.

The matcher and set-applier are shared with enrich/answers.py (the review
inbox's answers file) — answers are converted to the same {match, set} shape.

An entry that changes a row's `type` and names no category files the row as its new type files (resync_retyped):
Internal for an internal type, the type's default for a row that leaves the internal types or becomes income, so a
row's type and category never disagree (spending and income read the type). An entry whose `type` is the row's own
leaves it where it is filed.

A vendor / contains / account / date match reaches the parts of a split purchase (they copy those
fields) and repaints them — an explicit override overrides a split; a uid or amount match
reaches the parent only, which is display-only and logged.
"""
from __future__ import annotations

from ..models import (
    CATEGORY_SOURCES,
    FALLBACK_CATEGORY,
    FEES_CATEGORY,
    INCOME_CATEGORY,
    INCOME_TYPES,
    INTERNAL_CATEGORY,
    INTERNAL_TYPES,
    UNCATEGORIZED_SUB,
    QualityCheck,
    Txn,
)
from .categorize import set_internal


def matches(t: Txn, match: dict) -> bool:
    if "uid" in match and t.uid != match["uid"]:
        return False
    if "vendor" in match and (t.vendor or "").lower() != str(match["vendor"]).lower():
        return False
    if "account" in match and t.account_id != match["account"]:
        return False
    if "date" in match and t.date != str(match["date"]):
        return False
    if "amount" in match and t.amount_cents != round(match["amount"] * 100):
        return False
    if "contains" in match \
            and match["contains"].lower() not in t.description_raw.lower():
        return False
    return True


def apply_set(t: Txn, target: dict, source: str) -> None:
    """Apply a `set:` dict to one transaction (shared by overrides + answers). `source`
    ("override" | "answer") is stamped as category_source only when the set touches the
    category — a retype-only or tags-only set leaves provenance alone — and the row is marked a
    family fact (`family_fact`) by every set except a tags-only one. `source` must be one
    of the closed CATEGORY_SOURCES, checked before anything is applied (a real raise, not an
    assert, so `python -O` can't strip it)."""
    if source not in CATEGORY_SOURCES:
        raise ValueError(f"apply_set: category_source {source!r} is not one of {CATEGORY_SOURCES}")
    if "type" in target:
        t.type = target["type"]
        t.is_internal = target["type"] in INTERNAL_TYPES
    if "category" in target:
        t.category = target["category"]
    if "subcategory" in target:
        t.subcategory = target["subcategory"]
    if "category" in target or "subcategory" in target:
        t.category_source = source
    if "tags" in target:
        t.tags = sorted(set(t.tags) | set(target["tags"]))
    if "needs_review" in target:
        t.needs_review = bool(target["needs_review"])
        # an explicit flag from the family settles it: splits.unsplit_retyped must not put the pre-split one back
        t.prior_needs_review = None
    if "earner" in target:
        t.earner = target["earner"]
    # a tags-only set (the dashboard's tag editor) resolves nothing about the row: it stays a candidate for
    # the unmapped-vendor ranking (reconcile.unmapped_vendors skips family facts)
    if set(target) != {"tags"}:
        t.family_fact = True


def resync_retyped(t: Txn, subcategory: str | None = None) -> None:
    """The category of a row an override retyped without naming a category follows its new type, as the categorizer
    files a row of that type: an internal type is Internal/<type> (`internal`); a row that left the internal types
    while still filed Internal, or that became income while filed outside Income, takes its type's default — Income or
    Fees & Interest (`type_default`), or the fallback's Uncategorized for a purchase (`fallback`). Otherwise the
    category stays: a purchase may become a fee, or income a dividend, under the category it had. A `subcategory` the
    override named is kept under the category the row is filed in, stamped `override` as apply_set stamped it."""
    if t.type in INTERNAL_TYPES:
        set_internal(t)
    elif t.category == INTERNAL_CATEGORY or (t.type in INCOME_TYPES and t.category != INCOME_CATEGORY):
        if t.type in INCOME_TYPES or t.type == "fee":
            t.category = INCOME_CATEGORY if t.type in INCOME_TYPES else FEES_CATEGORY
            t.subcategory, t.category_source = None, "type_default"
        else:
            t.category, t.subcategory, t.category_source = FALLBACK_CATEGORY, UNCATEGORIZED_SUB, "fallback"
    if subcategory is not None:
        t.subcategory, t.category_source = subcategory, "override"


def apply(txns: list[Txn], entries: list[dict]) -> list[QualityCheck]:
    checks: list[QualityCheck] = []
    total = 0
    on_parent = 0
    stranded = 0      # parent hits that tried to set a category the parts do not follow: it cannot take effect
    for i, entry in enumerate(entries or []):
        match, target = entry["match"], entry["set"]
        hit_rows = [t for t in txns if matches(t, match)]
        # a category / subcategory on a split parent is display-only; it only matters when the entry also hit
        # the parent's parts (they carry the spend). A `type` in the set is a retype that un-splits the parent
        # (splits.unsplit_retyped), so it is not stranded. Judged per parent by whether THIS entry hit any of
        # its parts, not by which match keys are present: a uid + vendor match reaches the parent alone too.
        paints = bool({"category", "subcategory"} & target.keys()) and "type" not in target
        parts_hit = {t.parent_id for t in hit_rows if t.parent_id is not None}
        for t in hit_rows:
            if t.is_parent:
                on_parent += 1
                stranded += paints and t.uid not in parts_hit
            was = t.type
            apply_set(t, target, "override")
            # a `type` the row has already is no retype: the row stays where it is filed
            if "type" in target and "category" not in target and t.type != was:
                resync_retyped(t, target.get("subcategory"))
        hits = len(hit_rows)
        total += hits
        if hits == 0:
            checks.append(QualityCheck(
                name=f"overrides.entry_{i}", status="warn",
                detail=f"override matched no transactions: {match!r} "
                       f"({entry.get('note', 'no note')}) — did a refreshed "
                       f"export change the descriptor?"))
    if on_parent:
        checks.append(QualityCheck(
            name="overrides.on_split_parent", status="warn" if stranded else "info",
            detail=f"{on_parent} override hit(s) landed on split parents, which carry no spend (display only)"
                   + (f"; {stranded} of them set a category the parts do not follow" if stranded else "")
                   + ": a vendor / contains / account / date match also repaints the parts, a uid or amount match "
                   "reaches the parent alone — to change the parts match by vendor, contains, account or date, "
                   "or change the split rule"))
    if entries:
        checks.append(QualityCheck(
            name="overrides.applied", status="info",
            detail=f"{total} transactions adjusted by {len(entries)} override entries"))
    return checks

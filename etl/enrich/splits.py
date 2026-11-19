# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Rule-driven splits: a vendor_rules.yaml pattern entry with `split:` legs turns every external
spend row of that vendor into a PARENT (kept, flagged, excluded from every sum) plus one CHILD per leg
(the amounts and the categories). Runs in Pipeline.enrich after every stage that pairs or retypes rows
(transfers phase B/C, the internal resync) and before the answers, so a child
is never a pairing candidate and vendor answers can see parent_id (answers._answerable). Overrides may
still retype a parent; `unsplit_retyped` then takes its children back (an internal row has nothing to
split) and `sum_identity` judges the final rows in finalize_quality.

  - pattern: 'COSTCO'
    vendor: Costco
    split:
      - { pct: 70, category: Groceries }                              # pct of the parent, or amount: in dollars
      - { remainder: true, category: Shopping, subcategory: Home Goods }   # exactly one; absorbs the rounding

Validated at load (Config._validate_rules -> compile_split_rules with the taxonomy; the result is
Config.split_rules, the one compiled view Pipeline.enrich hands to apply()): >= 2 legs, each
exactly one of pct / amount / remainder, exactly one remainder, 0 < pct and sum pct < 100, amount > 0,
categories in the taxonomy. `split:` is a vendor-pattern key only (rules.compile_rules refuses it on a
keyword rule). Children copy date / account / description / vendor / type / tags (a copied list) and
get category_source "split", needs_review False and no transfer fields; amounts are rounded cents with
the remainder leg taking what is left, so sum children == parent. Only `type in SPEND_TYPES and not
is_internal` rows split; a refund (positive) splits with positive children; a parent with no room for
its legs (0, or |fixed legs| >= |parent|, or a leg rounding to 0) stays whole and is counted. Vendor
matching is by casefolded vendor string. Deterministic: list order in, children right after their
parent out. Everything that aggregates excludes parents; the full reader list, the YAML recipe and the
answers / overrides / learning rules are in docs/design/splits.md.
"""
from __future__ import annotations

import dataclasses
import math
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass

from ..models import SPEND_TYPES, QualityCheck, Txn

LEG_KEYS = frozenset({"pct", "amount", "remainder", "category", "subcategory"})


@dataclass(frozen=True)
class Leg:
    category: str
    subcategory: str | None
    pct: float | None = None          # exactly one of pct / amount_cents / remainder is set
    amount_cents: int | None = None
    remainder: bool = False


@dataclass(frozen=True)
class SplitRule:
    vendor: str
    legs: tuple[Leg, ...]
    index: int                        # the patterns[] index, for messages


def _err(index: int, msg: str) -> None:
    raise ValueError(f"vendor_rules[{index}].split: {msg}")


def _is_num(v) -> bool:
    """A finite number — YAML happily loads .nan / .inf, and a bool is not a quantity. An int is finite whatever its
    size (math.isfinite would raise on one too large for a float)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and (isinstance(v, int) or math.isfinite(v))


def _split_rule(e: dict, index: int, taxonomy: dict | None, out: dict[str, SplitRule]) -> SplitRule:
    """One patterns entry's split, or ValueError naming `vendor_rules[<index>].split` at its first problem. `out`
    holds the splits compiled so far, for the one-split-per-vendor check."""
    raw = e["split"]
    if not isinstance(raw, list) or len(raw) < 2:
        _err(index, "needs a list of at least 2 legs")
    legs: list[Leg] = []
    pct_total = 0.0
    for j, leg in enumerate(raw):
        if not isinstance(leg, dict):
            _err(index, f"leg {j} must be a mapping")
        unknown = set(leg) - LEG_KEYS
        if unknown:
            _err(index, f"leg {j}: unexpected keys {sorted(unknown, key=str)}")
        if len([k for k in ("pct", "amount", "remainder") if k in leg]) != 1:
            _err(index, f"leg {j}: exactly one of pct / amount / remainder")
        if not isinstance(leg.get("category"), str) or not leg["category"]:
            _err(index, f"leg {j}: needs category")
        sub = leg.get("subcategory")
        if sub is not None and not isinstance(sub, str):
            _err(index, f"leg {j}: subcategory must be a string or null")
        if taxonomy is not None:
            if leg["category"] not in taxonomy:
                _err(index, f"leg {j}: category {leg['category']!r} not in taxonomy")
            if sub is not None and sub not in (taxonomy.get(leg["category"]) or ()):
                _err(index, f"leg {j}: subcategory {sub!r} not seeded under {leg['category']!r}")
        if "pct" in leg:
            if not _is_num(leg["pct"]) or not 0 < leg["pct"] < 100:
                _err(index, f"leg {j}: pct must be a number between 0 and 100 (exclusive)")
            pct_total += float(leg["pct"])
            legs.append(Leg(leg["category"], sub, pct=float(leg["pct"])))
        elif "amount" in leg:
            # a fixed leg of less than one cent could never be a child (child_amounts refuses a 0 leg), and one too
            # large for a float is no amount either
            try:
                cents = round(float(leg["amount"]) * 100) if _is_num(leg["amount"]) else 0
            except OverflowError:
                cents = 0
            if cents < 1:
                _err(index, f"leg {j}: amount must be a positive number of dollars (at least one cent)")
            legs.append(Leg(leg["category"], sub, amount_cents=cents))
        else:
            if leg["remainder"] is not True:
                _err(index, f"leg {j}: remainder must be true")
            legs.append(Leg(leg["category"], sub, remainder=True))
    if sum(1 for leg in legs if leg.remainder) != 1:
        _err(index, "exactly one leg must be the remainder")
    if pct_total >= 100:
        _err(index, f"pct legs sum to {pct_total:g}; they must leave room for the remainder (< 100)")
    vendor = e.get("vendor")
    if not isinstance(vendor, str) or not vendor:
        _err(index, "a split entry needs vendor:")
    key = vendor.casefold()
    if key in out:
        _err(index, f"vendor {vendor!r} already has a split (entry [{out[key].index}])")
    return SplitRule(vendor=vendor, legs=tuple(legs), index=index)


def compile_split_rules(vendor_rules: dict, *, taxonomy: dict | None = None, problems: list[str] | None = None,
                        skip: Collection[int] = ()) -> dict[str, SplitRule]:
    """vendor.casefold() -> SplitRule for every patterns entry carrying `split:`. Shape is always
    validated; taxonomy membership only when `taxonomy` is given (Config does, once, at load).
    Without `problems` the first bad split raises ValueError; with it, each bad split's problem is
    appended there and the entry left out. The patterns indexes in `skip` are left out unread."""
    out: dict[str, SplitRule] = {}
    for index, e in enumerate(vendor_rules.get("patterns", []) or []):
        if index in skip or not isinstance(e, dict) or "split" not in e:
            continue
        try:
            rule = _split_rule(e, index, taxonomy, out)
        except ValueError as err:
            if problems is None:
                raise
            problems.append(str(err))
            continue
        out[rule.vendor.casefold()] = rule
    return out


def child_amounts(parent_cents: int, rule: SplitRule) -> list[int] | None:
    """One amount per leg, in leg order, summing exactly to parent_cents: a pct leg is rounded cents
    of the parent (sign included), an amount leg its fixed cents with the parent's sign, the
    remainder leg what is left. None when there is nothing to split (0), the fixed legs leave the
    remainder no room, or a leg would be 0 — the row then stays whole."""
    if parent_cents == 0:
        return None
    sign = -1 if parent_cents < 0 else 1
    out: list[int | None] = []
    for leg in rule.legs:
        if leg.pct is not None:
            out.append(round(parent_cents * leg.pct / 100))
        elif leg.amount_cents is not None:
            out.append(sign * leg.amount_cents)
        else:
            out.append(None)
    rest = parent_cents - sum(a for a in out if a is not None)
    amounts = [rest if a is None else a for a in out]
    if any(a == 0 or (a > 0) != (parent_cents > 0) for a in amounts):
        return None
    return amounts


def apply(txns: list[Txn], rules: dict[str, SplitRule]) -> tuple[list[Txn], list[QualityCheck]]:
    """Split every eligible row of a split-rule vendor. `rules` is compile_split_rules' result
    (Config.split_rules). Returns the new list (children right after their parent) and one
    `splits.applied` info check — per rule, the purchases split, the parts made and the rows too
    small to split. No split rule -> the rows as given, no check. Rows already split (is_parent /
    parent_id) are left alone, so a second pass is a no-op."""
    if not rules:
        return txns, []
    out: list[Txn] = []
    made: Counter = Counter()
    small: Counter = Counter()
    for t in txns:
        out.append(t)
        rule = rules.get(t.vendor.casefold()) if t.vendor else None
        if rule is None or t.is_parent or t.parent_id is not None \
                or t.type not in SPEND_TYPES or t.is_internal:
            continue
        amounts = child_amounts(t.amount_cents, rule)
        if amounts is None:
            small[rule.vendor] += 1
            continue
        parent_uid = t.uid                # a child's uid derives from this: read it before anything is mutated
        # the parent leaves the Review inbox (it is display-only); unsplit_retyped gives the flag back if an
        # override later retypes the parent out of spending and it becomes an ordinary row again
        t.prior_needs_review = t.needs_review
        t.is_parent = True
        t.needs_review = False
        for i, (leg, cents) in enumerate(zip(rule.legs, amounts, strict=True), start=1):
            out.append(dataclasses.replace(
                t, amount_cents=cents, category=leg.category, subcategory=leg.subcategory,
                category_source="split", needs_review=False, parent_id=parent_uid, is_parent=False,
                split_index=i, tags=list(t.tags), transfer_group_id=None, match_status=None,
                counter_hint=None, prior_needs_review=None))
        made[rule.vendor] += 1
    parts = []
    for r in sorted(rules.values(), key=lambda r: r.vendor):
        line = f"{r.vendor}: {made[r.vendor]} purchase(s) split into {made[r.vendor] * len(r.legs)} parts"
        if small[r.vendor]:
            line += f", {small[r.vendor]} too small to split"
        parts.append(line)
    return out, [QualityCheck(name="splits.applied", status="info",
                              detail=f"{len(rules)} split rule(s): " + "; ".join(parts))]


def unsplit_retyped(txns: list[Txn]) -> tuple[list[Txn], int]:
    """After the overrides: a parent an override retyped out of SPEND_TYPES (or marked internal) has
    nothing to split any more — drop its children, clear its flag and give back the review flag the
    split took away, so it is the ordinary row it would have been had it never been split. The flag
    comes back only if nobody spoke about it meanwhile: overrides.apply_set drops the stash whenever an
    answer or override sets needs_review, so an explicit true OR false stands. Returns the list (the
    same object when nothing changed) and how many parents were undone."""
    gone = {t.uid for t in txns if t.is_parent and (t.type not in SPEND_TYPES or t.is_internal)}
    if not gone:
        return txns, 0
    out: list[Txn] = []
    for t in txns:
        if t.parent_id in gone:
            continue
        if t.is_parent and t.uid in gone:
            t.is_parent = False
            if t.prior_needs_review is not None:      # still there: no answer or override set needs_review since
                t.needs_review = t.prior_needs_review
                t.prior_needs_review = None
        out.append(t)
    return out, len(gone)


def sum_identity(txns: list[Txn]) -> QualityCheck:
    """`splits.sum_identity`: fail when any parent's children do not sum to its amount or number fewer
    than 2, or a child names no parent; pass otherwise — also with no splits at all. Ids only."""
    parents = {t.uid: t for t in txns if t.is_parent}
    sums: Counter = Counter()
    counts: Counter = Counter()
    orphans = 0
    for t in txns:
        if t.parent_id is None:
            continue
        if t.parent_id not in parents:
            orphans += 1
            continue
        sums[t.parent_id] += t.amount_cents
        counts[t.parent_id] += 1
    bad = sorted(uid for uid, p in parents.items() if sums[uid] != p.amount_cents or counts[uid] < 2)
    if not bad and not orphans:
        return QualityCheck(name="splits.sum_identity", status="pass",
                            detail=f"{len(parents)} split parent(s), {sum(counts.values())} children; "
                                   "every parent equals the sum of its children")
    detail = (f"{len(bad)} parent(s) whose children do not sum to the parent (or number fewer than 2): "
              + ", ".join(bad[:10]))
    if orphans:
        detail += f"; {orphans} child row(s) with no parent"
    return QualityCheck(name="splits.sum_identity", status="fail", detail=detail)


def count_rows(txns: list[Txn]) -> int:
    """the rows that carry money: every row but the parents — what `manifest.transaction_count` reports."""
    return sum(1 for t in txns if not t.is_parent)

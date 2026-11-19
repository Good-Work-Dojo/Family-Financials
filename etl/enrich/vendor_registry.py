# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Vendor entity registry -> vendors.json.

One row per vendor seen on a spend row (SPEND_TYPES, vendor set, not internal, split purchases from
their parents — one row per purchase) with its rule
status (`has_rule`: a vendor_rules.yaml pattern names it; `category`/`subcategory` +
`category_source` from an explicit vendor rule, else from the learned rules (etl/enrich/learned.py,
matched by vendor.casefold()), else null), its learning eligibility (`learn`: not in
`learning.exclude`, not `learn: false`, not a split vendor), and TTM spend. Money stays in cents here;
exports.export_vendors converts. Pure function of its inputs — no wall clock, so two ingests
of the same data export the same file.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..models import SPEND_TYPES, Txn


def learning_excluded(vendor_rules: dict) -> frozenset[str]:
    """Vendors the learner (enrich/learned.py) must skip, casefolded: `learning.exclude` names plus every
    pattern flagged `learn: false` and every pattern carrying `split:` legs. Casefolded because a hand-typed exclude entry must not
    fail on case (vendor-scope answers already match case-insensitively)."""
    learning = vendor_rules.get("learning") or {}
    names = set(learning.get("exclude") or [])
    names |= {e["vendor"] for e in vendor_rules.get("patterns", []) or []
              if isinstance(e, dict) and e.get("learn") is False}
    # a split vendor's rows are the parts the rule made; answers on them (or on the parent) teach nothing
    names |= {e["vendor"] for e in vendor_rules.get("patterns", []) or []
              if isinstance(e, dict) and e.get("split") and isinstance(e.get("vendor"), str)}
    return frozenset(str(n).casefold() for n in names)


def ttm_start(data_end: str) -> str:
    """The day 365 days before data_end; rows strictly after it are 'trailing twelve months'."""
    return (date.fromisoformat(data_end) - timedelta(days=365)).isoformat()


def build(txns: list[Txn], vendor_rules: dict, learned_doc: dict | None,
          data_end: str | None) -> list[dict]:
    """`spend_ttm_cents` = net spend (refunds subtract) over (data_end - 365d, data_end];
    `txns` counts every purchase (split parts are skipped). Sorted by spend_ttm desc, then vendor. data_end None (no
    transactions) -> every spend_ttm is 0."""
    entries = [e for e in vendor_rules.get("patterns", []) or [] if isinstance(e, dict) and "vendor" in e]
    named = {e["vendor"] for e in entries}                   # has_rule: any pattern names the vendor
    # category lookup exactly as Categorizer.vendor_cat builds it: only the entries carrying a
    # category count (the last one wins), so a later category-less pattern for the same vendor
    # cannot blank the category the engine really applies
    with_cat = {e["vendor"]: e for e in entries if "category" in e}
    excluded = learning_excluded(vendor_rules)
    # learned rules by vendor.casefold(): a rule recorded under another spelling
    # still describes these rows
    learned = {str(r["vendor"]).casefold(): r for r in (learned_doc or {}).get("rules", []) or []
               if isinstance(r, dict) and r.get("vendor")}
    start = ttm_start(data_end) if data_end else None
    agg: dict[str, dict] = {}
    for t in txns:
        if t.type not in SPEND_TYPES or not t.vendor or t.is_internal:
            continue
        if t.parent_id is not None:     # one row per purchase — the parent carries the full amount; its parts are skipped
            continue
        a = agg.setdefault(t.vendor, {"txns": 0, "spend_ttm_cents": 0,
                                      "first_seen": t.date, "last_seen": t.date})
        a["txns"] += 1
        a["first_seen"] = min(a["first_seen"], t.date)
        a["last_seen"] = max(a["last_seen"], t.date)
        if start is not None and start < t.date <= data_end:
            a["spend_ttm_cents"] += -t.amount_cents
    rows = []
    for vendor, a in agg.items():
        entry = with_cat.get(vendor)
        if entry is not None:
            cat, sub, src = entry["category"], entry.get("subcategory"), "vendor_rule"
        elif vendor.casefold() in learned:
            lr = learned[vendor.casefold()]
            cat, sub, src = lr.get("category"), lr.get("subcategory"), "learned"
        else:
            cat, sub, src = None, None, None
        rows.append({"vendor": vendor, "has_rule": vendor in named,
                     "category": cat, "subcategory": sub, "category_source": src,
                     "learn": vendor.casefold() not in excluded, **a})
    rows.sort(key=lambda r: (-r["spend_ttm_cents"], r["vendor"]))
    return rows

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Taxonomy and vendor renames: rename once in config and every stored reference follows.

vendor_rules.yaml    renames: [ { from: "Old Vendor", to: "New Vendor" } ]
  Applied at load to vendor-scope review answers (`vendor` field AND the `vendor:<name>` key),
  learned rules, assumptions.cancelled_subscriptions[].vendor and learning.exclude.
category_rules.yaml  renames: [ { from: {category, subcategory?}, to: {category, subcategory?} } ]
  Applied at load to answers' `set`, overrides.yaml `set` and learned rules. A `from` without a
  subcategory moves the whole category (each reference keeps its subcategory unless `to`
  names one); a `from` with a subcategory moves exactly that pair (the subcategory becomes
  to.subcategory, or null when `to` has none). First matching entry wins; one rename each,
  in file order — and a chain or cycle (an entry whose result another entry renames again)
  is refused at load, like a vendor chain: write the final name.
  Applied by the ETL at load AND by the dashboard: assumptions.json carries the list as
  `category_renames` (exports.export_category_renames) and app/src/lib/categoryRenames.ts mirrors
  CategoryRenamer.map over the answers overlay and the category-keyed prefs.

Matching convention. A vendor rename's `from:` matches CASEFOLDED, so it finds a stored
reference whatever its capitalization, and a case-only rename (`Cora'S Bakery` ->
`Cora's Bakery`, the usual smart_title fallout) is valid: it re-spells every case variant to
the canonical `to:`. Two `from:` names that differ only by case are one name (a duplicate).
Casefolding consumers: this renamer; vendor_registry.learning_excluded (and so
`learn` / `learning.exclude`); the learned-rule lookups (learned.py, the Categorizer and
vendor_registry all key on `vendor.casefold()`); split rules (splits.py); vendor-scope answers
(overrides.matches lower-cases both sides); and recurring.py's cancelled_subscriptions test.
NOT casefolding: vendor_registry.build groups rows and `has_rule` by the exact vendor string,
and recurring groups series by exact (vendor, account). So a rename, `learning.exclude` and
`learn:` agree on what "the same vendor" means. CATEGORY names compare exactly: they are
keys of the closed taxonomy. Config rejects a vendor rename whose `from:` is a current
`patterns` vendor (pattern rows keep emitting their own spelling, so it would orphan stored
references): reword the pattern's `vendor:` instead.

The on-disk files (scenarios/answers.json, overrides.yaml) are never rewritten: the renamed
view lives in memory for one ingest. `unknown_references` reports every stored categorization
whose CATEGORY is still outside the taxonomy afterwards as one taxonomy.unknown_reference warn
— keys and vendor names only, never a descriptor, an amount or the stray label. Subcategories
are "free-text but seeded" (category_rules.yaml), so an unseeded one is not a problem.
"""
from __future__ import annotations

import copy
from collections.abc import Collection, Iterable

from ..models import INTERNAL_CATEGORY, QualityCheck


def _refused(message: str, problems: list[str] | None) -> None:
    """Raise `message` as ValueError when there is no `problems` list, else append it there."""
    if problems is None:
        raise ValueError(message)
    problems.append(message)


class VendorRenamer:
    def __init__(self, renames: list[dict] | None, problems: list[str] | None = None, skip: Collection[int] = ()):
        """Without `problems` the first bad entry raises ValueError naming `vendor_rules.renames[<index>]`; with it,
        each problem is appended there and the bad entry is left out, so every entry is checked in one run. The
        indexes in `skip` (entries another check already refused) are left out unread."""
        self.map_: dict[str, str] = {}               # from -> to, spelled as written in the file
        self._by_fold: dict[str, str] = {}           # from.casefold() -> to: what map() consults
        for i, r in enumerate(renames or []):
            if i in skip:
                continue
            if (not isinstance(r, dict) or not isinstance(r.get("from"), str) or not isinstance(r.get("to"), str)
                    or not r["from"] or not r["to"]):
                _refused(f"vendor_rules.renames[{i}]: needs non-empty string from: and to:", problems)
            elif r["from"] == r["to"]:
                _refused(f"vendor_rules.renames[{i}]: from and to are identical ({r['from']!r})", problems)
            elif r["from"] in self.map_:
                _refused(f"vendor_rules.renames[{i}]: duplicate from {r['from']!r}", problems)
            elif r["from"].casefold() in self._by_fold:
                _refused(f"vendor_rules.renames[{i}]: duplicate from {r['from']!r} "
                         "(vendor names compare case-insensitively)", problems)
            else:
                self.map_[r["from"]] = r["to"]
                self._by_fold[r["from"].casefold()] = r["to"]
        for src, dst in self.map_.items():
            # a chain is a rename whose result is renamed again; a case-only rename (A -> a)
            # maps its own result to itself, so it is not one
            final = self.map(dst)
            if final != dst:
                _refused(f"vendor_rules.renames: {src!r} -> {dst!r} is itself renamed to "
                         f"{final!r}; write the final name (no chains)", problems)

    def map(self, vendor):
        return self._by_fold.get(vendor.casefold(), vendor) if isinstance(vendor, str) else vendor

    def apply_to_answers(self, doc: dict) -> dict:
        out = copy.deepcopy(doc)
        for a in out.get("answers", []) or []:
            if isinstance(a, dict) and a.get("scope") == "vendor" and isinstance(a.get("vendor"), str):
                new = self.map(a["vendor"])
                if new != a["vendor"]:
                    a["vendor"] = new
                    a["key"] = f"vendor:{new}"
        return out

    def apply_to_learned(self, doc: dict | None) -> dict | None:
        if not doc:
            return doc
        out = copy.deepcopy(doc)
        for r in out.get("rules", []) or []:
            if isinstance(r, dict) and isinstance(r.get("vendor"), str):
                r["vendor"] = self.map(r["vendor"])
        return out

    def apply_to_cancelled(self, entries: Iterable[dict] | None) -> list[dict]:
        out = copy.deepcopy(list(entries or []))
        for e in out:
            if isinstance(e, dict) and isinstance(e.get("vendor"), str):
                e["vendor"] = self.map(e["vendor"])
        return out

    def apply_to_names(self, names: Iterable[str] | None) -> list[str]:
        return sorted({self.map(str(n)) for n in (names or [])})


class CategoryRenamer:
    def __init__(self, renames: list[dict] | None, taxonomy: dict, problems: list[str] | None = None,
                 skip: Collection[int] = ()):
        """Without `problems` the first bad entry raises ValueError naming `category_rules.renames[<index>]`; with
        it, each problem is appended there and the bad entry is left out. The indexes in `skip` are left out
        unread."""
        self.rules: list[tuple[str, str | None, bool, str, str | None]] = []
        self._index: list[int] = []                  # each rule's renames[] index, for the chain messages
        seen: dict[tuple[str, bool, str | None], int] = {}
        for i, r in enumerate(renames or []):
            if i in skip:
                continue
            src = r.get("from") if isinstance(r, dict) else None
            dst = r.get("to") if isinstance(r, dict) else None
            if not (isinstance(src, dict) and isinstance(dst, dict)
                    and isinstance(src.get("category"), str) and isinstance(dst.get("category"), str)
                    and isinstance(src.get("subcategory"), (str, type(None)))
                    and isinstance(dst.get("subcategory"), (str, type(None)))):
                _refused(f"category_rules.renames[{i}]: from: and to: need a category "
                         "(and an optional subcategory)", problems)
                continue
            to_sub = dst.get("subcategory")
            ident = (src["category"], "subcategory" in src, src.get("subcategory"))
            if dst["category"] not in taxonomy:
                _refused(f"category_rules.renames[{i}]: to.category {dst['category']!r} not in taxonomy", problems)
            elif (to_sub is not None and dst["category"] != INTERNAL_CATEGORY
                    and to_sub not in (taxonomy.get(dst["category"]) or [])):
                _refused(f"category_rules.renames[{i}]: to.subcategory {to_sub!r} not seeded under "
                         f"{dst['category']!r}", problems)
            elif (src["category"], src.get("subcategory")) == (dst["category"], to_sub):
                _refused(f"category_rules.renames[{i}]: from and to are identical", problems)
            # a from's identity is its category plus whether it names a subcategory (a pair, which
            # may be null) and which one: a whole-category from and a pair from never collide
            elif ident in seen:
                label = repr(src["category"]) + (f"/{src['subcategory']!r}" if "subcategory" in src else "")
                _refused(f"category_rules.renames[{i}]: duplicate from {label} "
                         f"(already renamed by renames[{seen[ident]}]; only the first would ever apply)", problems)
            else:
                seen[ident] = i
                self.rules.append((src["category"], src.get("subcategory"), "subcategory" in src,
                                   dst["category"], to_sub))
                self._index.append(i)
        self._refuse_chains(problems)

    def _refuse_chains(self, problems: list[str] | None = None) -> None:
        """A chain is an entry whose result ANOTHER entry renames again. The list applies once per
        reference, so A->B plus B->C would leave an A-answer at B while the persisted learned rules
        step one hop per ingest - and a cycle (A->B, B->A) would flip them on every ingest. So the
        load refuses it and names both entries: write the final name. What an entry's result can be:
        (to.category, to.subcategory) when `to` names a subcategory; (to.category, null) for a pair
        `from` with a bare `to`; and (to.category, ANY subcategory) for a whole-category `from` with a
        bare `to`, since each reference keeps its own. An entry never chains with itself: a
        whole-category A -> A/x maps its own result to itself. Each entry's first chain is raised, or
        appended to `problems` when it is given."""
        for j, (_, _, pair_j, to_cat, to_sub) in enumerate(self.rules):
            for i, (from_cat, from_sub, pair_i, _, _) in enumerate(self.rules):
                if i == j or from_cat != to_cat:
                    continue
                if not pair_i:
                    hit = True                          # the whole result category is moved again
                elif to_sub is not None:
                    hit = from_sub == to_sub            # exactly the result pair is moved again
                elif pair_j:
                    hit = from_sub is None              # a pair with a bare `to` lands on the null pair
                else:
                    hit = True                          # every subcategory arrives, this pair's among them
                if hit:
                    label = repr(to_cat) + (f"/{to_sub!r}" if to_sub is not None else "")
                    _refused(f"category_rules.renames[{self._index[j]}]: its to {label} is renamed again by "
                             f"renames[{self._index[i]}]; write the final name (no chains or cycles)", problems)
                    break

    def map(self, category, subcategory):
        for from_cat, from_sub, pair, to_cat, to_sub in self.rules:
            if category != from_cat:
                continue
            if pair:
                if subcategory == from_sub:
                    return to_cat, to_sub
            else:
                return to_cat, (to_sub if to_sub is not None else subcategory)
        return category, subcategory

    def apply_to_set(self, set_: dict) -> dict:
        if not isinstance(set_, dict) or "category" not in set_:
            return set_
        cat, sub = self.map(set_["category"], set_.get("subcategory"))
        out = dict(set_)
        out["category"] = cat
        if "subcategory" in set_ or sub != set_.get("subcategory"):
            out["subcategory"] = sub
        return out

    def apply_to_answers(self, doc: dict) -> dict:
        out = copy.deepcopy(doc)
        for a in out.get("answers", []) or []:
            if isinstance(a, dict) and isinstance(a.get("set"), dict):
                a["set"] = self.apply_to_set(a["set"])
        return out

    def apply_to_overrides(self, entries: Iterable[dict] | None) -> list[dict]:
        out = copy.deepcopy(list(entries or []))
        for e in out:
            if isinstance(e, dict) and isinstance(e.get("set"), dict):
                e["set"] = self.apply_to_set(e["set"])
        return out

    def apply_to_learned(self, doc: dict | None) -> dict | None:
        if not doc:
            return doc
        out = copy.deepcopy(doc)
        for r in out.get("rules", []) or []:
            if isinstance(r, dict) and "category" in r:
                r.update(self.apply_to_set({k: r[k] for k in ("category", "subcategory") if k in r}))
        return out


def unknown_references(answers_doc: dict | None, overrides: Iterable[dict] | None,
                       learned_doc: dict | None, taxonomy: dict) -> list[QualityCheck]:
    """Stored categorizations whose category is not in the taxonomy (after renames)."""
    bad: list[str] = []
    for a in (answers_doc or {}).get("answers", []) or []:
        if isinstance(a, dict) and isinstance(a.get("set"), dict) and "category" in a["set"] \
                and a["set"]["category"] not in taxonomy:
            bad.append(f"answer {a.get('key', '?')}")
    for i, e in enumerate(overrides or []):
        cat = (e.get("set") or {}).get("category") if isinstance(e, dict) else None
        if cat is not None and cat not in taxonomy:
            bad.append(f"override[{i}]")
    for r in (learned_doc or {}).get("rules", []) or []:
        if isinstance(r, dict) and r.get("category") not in taxonomy:
            bad.append(f"learned {r.get('vendor', '?')}")
    if not bad:
        return []
    return [QualityCheck(
        name="taxonomy.unknown_reference", status="warn",
        detail=f"{len(bad)} stored categorization(s) reference a category outside the taxonomy "
               "(fix the reference, restore the category in the taxonomy, or add a "
               "category_rules.yaml renames: entry): "
               + ", ".join(bad[:20]) + (" ..." if len(bad) > 20 else ""))]

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Structured categorization rules.

One rule shape for every rule source — the legacy `{pattern, category, ...}` entries of
config/category_rules.yaml `keyword_rules` and config/vendor_rules.yaml `patterns`, the
structured entries and the candidates `python -m etl rules preview` tries out (learned
rules are a per-vendor lookup in the Categorizer, never a Rule):

    Rule(name, stage, conditions, conditions_op, set, source, index)

Legacy shape   {pattern, category, subcategory?, tags?, needs_review?}          (keyword rules)
               {pattern, vendor, category?, subcategory?, tags?, learn?}        (vendor patterns)
  compiles to ONE Condition("description", "matches", <compiled regex>) in stage `default`
  with today's exact semantics: an IGNORECASE regex searched against cleaner(raw).upper()
  OR raw.upper() (the raw fallback is what lets `^CHECK \\d+$` match after the cleanup has
  eaten the digits).
New shape      {name?, stage?, all: [{field, op, value}, ...] | any: [...], set: {...}}
  `amount` values are dollars in YAML (like overrides.yaml) and are compared as SIGNED
  cents (negative = outflow, like every amount in this ETL); `inflow` / `outflow` take no
  value. Text comparisons are case-insensitive.

Stages run pre -> default -> post; within a stage, file order; first match wins. An entry
without a stage is `default`, so a rulebook of legacy entries runs in file order. Vendor
patterns compile here as DATA only (vendor name, category,
tags): VendorNormalizer.normalize keeps its own loop and its own target rule
(`cleaned.upper() or raw.upper()` — no raw fallback when the cleaned string is non-empty).

Validation happens at compile time and raises ValueError naming `<source>[<index>]`: shape,
field/op pairs, and a `type` value that is not a transaction type (a typo'd value would
otherwise never match). Handed a `problems` list, compile_rules, validate_taxonomy and
validate_references collect instead: every bad entry's problem is appended and the entry left
out (Config's load reports them all at once). `validate_taxonomy` checks category/subcategory membership so Config
fails an ingest on a typo instead of minting a new bucket in the dashboard, and
`validate_references` checks `owner` / `account` values against the known owners and the
configured account ids (those lists live outside this module, so Config supplies them).
A vendor pattern takes no `stage:` / `name:` — the VendorNormalizer matches in file order and
ignores both, so accepting them would only mislead.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass

from ..models import INTERNAL_CATEGORY, TXN_TYPES, Txn

STAGES = ("pre", "default", "post")
FIELDS = ("description", "vendor", "amount", "account", "owner", "type", "bank_category")
OPS = ("is", "isNot", "oneOf", "contains", "matches", "gt", "gte", "lt", "lte", "between",
       "inflow", "outflow")
FIELD_OPS: dict[str, frozenset[str]] = {
    "description": frozenset({"contains", "matches"}),
    "vendor": frozenset({"is", "isNot", "oneOf", "contains", "matches"}),
    "amount": frozenset({"gt", "gte", "lt", "lte", "between", "inflow", "outflow"}),
    "account": frozenset({"is", "isNot", "oneOf"}),
    "owner": frozenset({"is", "isNot", "oneOf"}),
    "type": frozenset({"is", "isNot", "oneOf"}),
    "bank_category": frozenset({"is", "isNot", "oneOf", "contains", "matches"}),
}
SET_KEYS = frozenset({"category", "subcategory", "tags", "needs_review", "vendor"})
# every key a legacy entry may carry — anything else is a typo and must fail loudly
LEGACY_KEYS = frozenset({"pattern", "name", "stage", "category", "subcategory", "tags",
                         "needs_review", "vendor", "learn", "note",
                         "split"})   # split: legs are vendor-pattern only and are compiled by etl/enrich/splits.py
NEW_KEYS = frozenset({"name", "stage", "all", "any", "set", "note"})
# a condition is exactly {field, op, value}; anything else is a typo (e.g. `not: true`)
CONDITION_KEYS = frozenset({"field", "op", "value"})
VENDOR_SOURCES = frozenset({"vendor_rules"})


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: object   # re.Pattern | str | tuple[str, ...] | int (signed cents) | tuple[int, int] | None


@dataclass(frozen=True)
class Rule:
    name: str                       # entry["name"] or f"{source}:{index}"
    stage: str                      # one of STAGES
    conditions: tuple[Condition, ...]
    conditions_op: str              # "all" | "any"
    set: dict                       # category/subcategory/tags/needs_review (+ vendor for vendor rules)
    source: str                     # "keyword_rules" | "vendor_rules" | "preview"
    index: int


def _err(source: str, index: int, msg: str) -> None:
    raise ValueError(f"{source}[{index}]: {msg}")


def _is_num(v) -> bool:
    """A real, finite YAML number (`.nan` / `.inf` are floats too, and are not amounts)."""
    if isinstance(v, bool):
        return False
    return isinstance(v, int) or (isinstance(v, float) and math.isfinite(v))


def _cents(source: str, index: int, dollars) -> int:
    try:
        return round(dollars * 100)
    except (OverflowError, ValueError):     # e.g. 1e308 * 100 overflows to inf
        _err(source, index, f"amount {dollars!r} is out of range")


def _check_types(source: str, index: int, values) -> None:
    """A `type` condition can only ever match a real transaction type (compared casefolded, like
    every text op): any other value is a typo that would silently never fire."""
    for x in values:
        if x.casefold() not in TXN_TYPES:
            _err(source, index, f"type value {x!r} is not a transaction type (one of {', '.join(TXN_TYPES)})")


def _condition(source: str, index: int, c) -> Condition:
    if not isinstance(c, dict) or not isinstance(c.get("field"), str) or not isinstance(c.get("op"), str):
        _err(source, index, f"condition must be {{field, op, value}}, got {c!r}")
    extra = set(c) - CONDITION_KEYS
    if extra:
        _err(source, index, f"unexpected condition keys {sorted(extra, key=str)} (a condition is field/op/value)")
    field, op = c["field"], c["op"]
    if field not in FIELDS:
        _err(source, index, f"unknown field {field!r} (one of {', '.join(FIELDS)})")
    if op not in OPS:
        _err(source, index, f"unknown op {op!r} (one of {', '.join(OPS)})")
    if op not in FIELD_OPS[field]:
        _err(source, index, f"op {op!r} is not valid for field {field!r} "
                            f"(valid: {', '.join(sorted(FIELD_OPS[field]))})")
    if op in ("inflow", "outflow"):
        if "value" in c:
            _err(source, index, f"{op} takes no value")
        return Condition(field, op, None)
    if "value" not in c:
        _err(source, index, f"{field}/{op} needs a value")
    v = c["value"]
    if op == "matches":
        if not isinstance(v, str):
            _err(source, index, f"{field}/matches value must be a regex string")
        if not v.strip():
            _err(source, index, f"{field}/matches value must not be empty or blank "
                                f"(it would match nearly everything)")
        try:
            return Condition(field, op, re.compile(v, re.IGNORECASE))
        except re.error as e:
            raise ValueError(f"{source}[{index}]: bad regex {v!r}: {e}") from e
    if op == "between":
        if not (isinstance(v, list) and len(v) == 2 and all(_is_num(x) for x in v)):
            _err(source, index, "between needs two numbers (dollars)")
        lo, hi = sorted(_cents(source, index, x) for x in v)
        return Condition(field, op, (lo, hi))
    if field == "amount":
        if not _is_num(v):
            _err(source, index, f"amount/{op} value must be a finite number (dollars)")
        return Condition(field, op, _cents(source, index, v))
    if op == "oneOf":
        if not (isinstance(v, list) and v and all(isinstance(x, str) for x in v)):
            _err(source, index, f"{field}/oneOf value must be a list of strings")
        if not all(x.strip() for x in v):
            _err(source, index, f"{field}/oneOf value must not contain empty or blank items")
        if field == "type":
            _check_types(source, index, v)
        return Condition(field, op, tuple(v))
    if not isinstance(v, str):
        _err(source, index, f"{field}/{op} value must be a string")
    if op == "contains" and not v.strip():
        _err(source, index, f"{field}/contains value must not be empty or blank "
                            f"(it would match nearly everything)")
    if field == "type":
        _check_types(source, index, [v])
    return Condition(field, op, v)      # is / isNot may be "" on purpose: the "no vendor" idiom


def _check_set(source: str, index: int, set_: dict) -> None:
    unknown = set(set_) - SET_KEYS
    if unknown:
        _err(source, index, f"set keys not allowed: {sorted(unknown, key=str)}")
    if source in VENDOR_SOURCES:
        if not isinstance(set_.get("vendor"), str) or not set_["vendor"]:
            _err(source, index, "set needs vendor (a canonical vendor name)")
        if "category" in set_ and not isinstance(set_["category"], str):
            _err(source, index, "category must be a string")
    else:
        if "vendor" in set_:
            _err(source, index, "vendor is only settable by vendor_rules patterns")
        if not isinstance(set_.get("category"), str) or not set_["category"]:
            _err(source, index, "set needs category")
    if "subcategory" in set_ and not (set_["subcategory"] is None or isinstance(set_["subcategory"], str)):
        _err(source, index, "subcategory must be a string or null")
    if "tags" in set_ and not (isinstance(set_["tags"], list) and all(isinstance(x, str) for x in set_["tags"])):
        _err(source, index, "tags must be a list of strings")
    if "needs_review" in set_ and not isinstance(set_["needs_review"], bool):
        _err(source, index, "needs_review must be a boolean")


def _compile_entry(entry, index: int, source: str, seen: dict[str, int]) -> Rule:
    """One entry as a Rule, or ValueError naming `<source>[<index>]` at its first problem. `seen` (name -> index)
    records the entry's name once it is known, so a later duplicate is reported against it."""
    if not isinstance(entry, dict):
        _err(source, index, "entry must be a mapping")
    if source in VENDOR_SOURCES:
        ignored = [k for k in ("stage", "name") if k in entry]
        if ignored:
            _err(source, index, f"vendor patterns take no {' or '.join(k + ':' for k in ignored)} "
                                "(VendorNormalizer matches them in file order and ignores both)")
    stage = entry.get("stage", "default")
    if not isinstance(stage, str) or stage not in STAGES:
        _err(source, index, f"unknown stage {stage!r} (one of {', '.join(STAGES)})")
    name = str(entry["name"]) if entry.get("name") else f"{source}:{index}"
    if name in seen:
        _err(source, index, f"duplicate rule name {name!r} (also entry [{seen[name]}])")
    seen[name] = index
    if "pattern" in entry:
        unknown = set(entry) - LEGACY_KEYS
        if unknown:
            _err(source, index, f"unexpected keys {sorted(unknown, key=str)} on a pattern: entry")
        if "split" in entry and source not in VENDOR_SOURCES:
            _err(source, index, "split: is only valid on a vendor_rules pattern (etl/enrich/splits.py)")
        if "learn" in entry and not isinstance(entry["learn"], bool):
            _err(source, index, "learn must be a boolean")
        conditions = (_condition(source, index,
                                 {"field": "description", "op": "matches", "value": entry["pattern"]}),)
        op = "all"
        set_ = {k: entry[k] for k in ("category", "subcategory", "tags", "needs_review", "vendor") if k in entry}
    else:
        unknown = set(entry) - NEW_KEYS
        if unknown:
            _err(source, index, f"unexpected keys {sorted(unknown, key=str)}")
        if ("all" in entry) == ("any" in entry):
            _err(source, index, "exactly one of all:/any: is required")
        op = "all" if "all" in entry else "any"
        raw = entry[op]
        if not isinstance(raw, list) or not raw:
            _err(source, index, f"{op}: must be a non-empty list of conditions")
        conditions = tuple(_condition(source, index, c) for c in raw)
        set_ = entry.get("set")
        if not isinstance(set_, dict):
            _err(source, index, "set: must be a mapping")
        set_ = dict(set_)
    # `tags:` or `needs_review:` written with no value counts as left out: no tags, no review
    set_ = {k: v for k, v in set_.items() if not (k in ("tags", "needs_review") and v is None)}
    _check_set(source, index, set_)
    return Rule(name=name, stage=stage, conditions=conditions, conditions_op=op, set=set_, source=source, index=index)


def compile_rules(entries: list[dict], *, source: str, problems: list[str] | None = None,
                  skip: Collection[int] = ()) -> list[Rule]:
    """Turn YAML entries (legacy or structured) into Rules, validating shape as it goes.
    Taxonomy membership is a separate step (validate_taxonomy) because the taxonomy lives in
    another file. Without `problems` the first bad entry raises ValueError naming `<source>[<index>]`;
    with it, each bad entry's problem is appended there (one per entry, in that form) and the entry
    is left out, so every entry is checked in one run. The indexes in `skip` (entries another check
    already refused) are left out unread."""
    rules: list[Rule] = []
    seen: dict[str, int] = {}               # name -> index, so a duplicate is reported against both
    for index, entry in enumerate(entries or []):
        if index in skip:
            continue
        try:
            rules.append(_compile_entry(entry, index, source, seen))
        except ValueError as e:
            if problems is None:
                raise
            problems.append(str(e))
    return rules


def _text(subject: str | None, c: Condition) -> bool:
    s = (subject or "").casefold()
    if c.op == "is":
        return s == str(c.value).casefold()
    if c.op == "isNot":
        return s != str(c.value).casefold()
    if c.op == "oneOf":
        return s in {v.casefold() for v in c.value}
    if c.op == "contains":
        return bool(subject) and str(c.value).casefold() in s
    if c.op == "matches":
        return bool(subject) and c.value.search(subject) is not None
    raise ValueError(f"op {c.op!r} is not a text op")      # unreachable after compile_rules


def _holds(c: Condition, t: Txn, cleaner, owner_of, cleaned: str | None) -> bool:
    if c.field == "description":
        raw = t.description_raw
        up = (cleaned if cleaned is not None else cleaner(raw)).upper()
        if c.op == "matches":
            return bool(c.value.search(up) or c.value.search(raw.upper()))
        needle = str(c.value).upper()
        return needle in up or needle in raw.upper()
    if c.field == "amount":
        a = t.amount_cents
        if c.op == "gt":
            return a > c.value
        if c.op == "gte":
            return a >= c.value
        if c.op == "lt":
            return a < c.value
        if c.op == "lte":
            return a <= c.value
        if c.op == "between":
            return c.value[0] <= a <= c.value[1]
        if c.op == "inflow":
            return a > 0
        return a < 0                                          # outflow
    # owner_of is looked up only for an owner condition: a vendor-only rule must keep matching
    # even when the caller's owner_of raises on an account it does not know.
    if c.field == "owner":
        subject = owner_of(t.account_id)
        if not subject:         # unresolvable owner: a rule about owners must not fire — isNot included
            return False
    else:
        subject = {"vendor": t.vendor, "account": t.account_id, "type": t.type,
                   "bank_category": t.bank_category}[c.field]
    return _text(subject, c)


def rule_matches(rule: Rule, t: Txn, *, cleaner: Callable[[str], str],
                 owner_of: Callable[[str], str], cleaned: str | None = None) -> bool:
    """Does `rule` match transaction `t`? `cleaner` is VendorNormalizer.clean; `owner_of`
    maps an account id to its owner (None/"" when unknown — every owner op then fails,
    `isNot` included: a rule about owners never fires on a row whose owner is unknown).
    `cleaned` is an optional precomputed cleaner(t.description_raw): the Categorizer
    cleans each descriptor once and scans ~140 rules against it."""
    results = (_holds(c, t, cleaner, owner_of, cleaned) for c in rule.conditions)
    return all(results) if rule.conditions_op == "all" else any(results)


def _where(r: Rule) -> str:
    """`<source>[<index>]`, plus the rule's name when it has one of its own (a rule without a `name:` is
    called `<source>:<index>`, which would only repeat the position)."""
    where = f"{r.source}[{r.index}]"
    return where if r.name == f"{r.source}:{r.index}" else f"{where} ({r.name})"


def _raise_or_collect(found: list[str], problems: list[str] | None) -> None:
    """The first of `found` raised as ValueError when there is no `problems` list, else all of them appended to it."""
    if problems is None:
        if found:
            raise ValueError(found[0])
    else:
        problems.extend(found)


def validate_taxonomy(rules: list[Rule], taxonomy: dict[str, list[str]], problems: list[str] | None = None) -> None:
    """Every rule that sets a category must name a taxonomy category, and its subcategory
    must be seeded there, so the loader refuses a typo instead of minting a new bucket. No rule may
    name the internal category: a rule sets no type, and the type alone takes a row out of spending
    and income, so a row filed Internal by a rule would still count (a transfer rule or an override
    sets the type). The first problem raises ValueError, or every one is appended to `problems` when
    it is given."""
    found = []
    for r in rules:
        cat, sub = r.set.get("category"), r.set.get("subcategory")
        if cat is None:
            continue
        if cat == INTERNAL_CATEGORY:
            found.append(f"{_where(r)}: category {cat!r} does not make a row internal, so it would still count in "
                         "spending or income — set type: transfer (transfer_rules.yaml or an override) instead")
        elif cat not in taxonomy:
            found.append(f"{_where(r)}: category {cat!r} not in taxonomy")
        elif sub is not None and sub not in (taxonomy.get(cat) or []):
            found.append(f"{_where(r)}: subcategory {sub!r} not seeded under {cat!r}")
    _raise_or_collect(found, problems)


def validate_references(rules: list[Rule], *, owners, accounts, problems: list[str] | None = None) -> None:
    """`owner` / `account` condition values must name a known owner (Config.owners) or a
    configured account id. Compared casefolded, like the ops themselves; a typo would otherwise
    be a rule that silently never matches. Errors name `<source>[<index>]` like compile_rules: the
    first raises ValueError, listing the known values in parentheses; with `problems` each is appended
    there as `<source>[<index>]: unknown <field> '<value>' — <fix>`, the owners listed in their order."""
    known = {"owner": {o.casefold() for o in owners}, "account": {a.casefold() for a in accounts}}
    found = []
    for r in rules:
        for c in r.conditions:
            if c.field not in known:
                continue
            for x in ((c.value,) if isinstance(c.value, str) else c.value):
                if x.casefold() in known[c.field]:
                    continue
                if problems is None:
                    shown = sorted(owners if c.field == "owner" else accounts)
                    raise ValueError(f"{r.source}[{r.index}]: unknown {c.field} {x!r} "
                                     f"(one of {', '.join(shown)})")
                fix = (f"one of: {', '.join(owners)}" if c.field == "owner"
                       else "use an account id from accounts.yaml")
                found.append(f"{r.source}[{r.index}]: unknown {c.field} {x!r} — {fix}")
    _raise_or_collect(found, problems)


def last_in_stage_problems(rules: list[Rule], category: str, subcategory: str) -> list[str]:
    """A rule that files rows under `category / subcategory` — the person-to-person catch-all, whose pattern matches
    every payment-app descriptor — that is not the last rule of its stage: the rules after it in that stage could
    never file such a row. One `<source>[<index>] (<name>): <what> — <fix>` per such rule; `rules` are compiled ones
    in file order."""
    out = []
    for r in rules:
        if (r.set.get("category"), r.set.get("subcategory")) != (category, subcategory):
            continue
        after = [x for x in rules if x.stage == r.stage and x.index > r.index]
        if after:
            out.append(f"{_where(r)}: files rows under '{category} / {subcategory}', the catch-all for person-to-person "
                       f"payments, yet {len(after)} rule(s) of the {r.stage} stage come after it and can never file such "
                       "a row — move it to the end of the stage — a stage can hold only one such rule; merge two into "
                       "one pattern")
    return out

#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Compare two ETL export directories under a named change policy.

The verification harness (tools/verify/export-diff.py) runs this inside the ETL image
against a `base` and a `head` scratch ingest of the SAME live inputs, and asks one
question per change: did this change alter exactly what its gate policy allows,
and nothing else?

Every policy except `identical` and `removals` also accepts *additive* changes — a new key
anywhere, a new export file, a longer `manifest.files` — and rejects any removed key/file/row and
any changed existing value. On top of that:
  identical              nothing differs (manifest.generated_at is always ignored)
  schema-additive        additive only
  vendors-fallback-only  transactions[].vendor may change on rows whose BASE vendor is not
                         a vendor_rules `patterns` name, and only to a case variant of itself
                         (smart_title's re-casing) or to the `to:` of a vendor_rules `renames:`
                         entry whose `from:` it matches (case-insensitively): a respelling to
                         anything else, or a merge of two distinct vendors, is a violation.
                         Merges (a head vendor fed by >= 2 base vendors) and splits are
                         listed by name; recurring.json items, quality_report
                         .top_unmapped_vendors, vendors.json vendors and assumptions.json
                         thresholds.cancelled_subscriptions may differ in vendor strings only
  categories-only        transactions[] may change only category / subcategory /
                         category_source / needs_review (printed as an old->new matrix),
                         and every changed row's new category_source must be `learned`;
                         a row a learned rule recategorized may also move `tags`;
                         cashflow_monthly months[].by_category, recurring items[].category,
                         quality_report.json and vendors.json may differ as consequences; the
                         recurring schedule keys and accounts[] ledger_drift are additive
  splits                 transactions.json may gain rows whose parent_id names a head row flagged
                         is_parent (split children: >= 2 per parent, summing to the parent's amount in
                         cents; each child has a finite whole-cent amount, is_parent false and exactly
                         the keys of an ordinary row, none extra and none missing); a parent row may
                         only clear needs_review; every other existing row is untouched beyond the two
                         new keys (parent_id null, is_parent a bool) — any third new key on an existing
                         row is outside the policy; transactions.count, manifest.transaction_count,
                         cashflow_monthly by_category, recurring.json, quality_report.json and
                         vendors.json may differ (accounts.json may not: its export carries no
                         per-account row count). The report counts parents and children.
  removals               nothing is added and no remaining value moves; only what an
                         --allow-removed SPEC names may vanish (below)
  refactor               the schema-additive rules (a new file, a new key at any depth and a new
                         key on an existing transaction row are free; a new or removed transaction
                         row, a removed key, a changed value and a list whose length or order
                         changes are violations) with one exception: quality_report.json `checks`
                         is taken out of both sides before the walk and compared as a multiset of
                         (name, status) — every key of a check other than name and status, and the
                         list's order, is ignored; the two values count by their JSON text, so
                         null, a missing key and "None" are three values — and each (name, status)
                         whose count differs is one difference, `checks: <name> <status> ×a → ×b`
                         (a status flip is two). On top, exactly what its --allow-removed SPECs (as
                         under removals) and --allow-changed SPECs (below) name. With either side's
                         checks missing or not a list of objects, the list is left to the walk and
                         the row allowances like any other key. A row a removal spec keeps holds
                         its place among the other rows even when those gain keys

Removals: `removals` is `identical` plus a schema_version increase plus exactly the removals its
--allow-removed SPECs name (repeatable; accepted under removals and refactor, a usage error under
any other policy):
  FILE                       the file may be absent from head; manifest.files then loses exactly
                             that name and is otherwise identical, order included
  FILE:KEY.PATH              that key and its subtree may be removed; an added or changed value
                             under it may not. KEY.PATH walks dicts from the file root and never
                             reaches into a list (a list element is addressed by index, not by KEY).
                             When KEY.PATH holds a list, its subtree is every row: while the list
                             keeps its length, any field of any row may vanish with no row removed,
                             so callers allow-list a scalar key, a whole file or the rows a
                             [FIELD=GLOB] spec names instead
  FILE:KEY.PATH[FIELD=GLOB]  KEY.PATH names a list of objects in base (anything else is one
                             violation naming the spec). Before the walk, the rows whose
                             str(row.get(FIELD)) matches GLOB (fnmatchcase) are taken out of both
                             sides: each such head row must equal a distinct such base row and keep
                             its place among the rest, and the base rows left over are the
                             removals. The other rows are walked as usual (indices in their paths
                             count the rows left), so their order and content stay identical. A
                             row two specs on one list match belongs to the first of them. A row
                             whose FIELD is missing or null is matched as the text None.
FILE is [A-Za-z0-9_.-]+ ending in .json; KEY and FIELD are [A-Za-z0-9_-]+; GLOB is one or more
characters other than "]". Never allow-listed: transactions.json (its rows are matched by id),
manifest.json itself, and its files, schema_version, generated_at and simplefin_fetched_at keys.
Each allowance is reported as `allowance <spec>: <n> removed`; a spec that matches nothing passes
with 0.

Changes: under `refactor` only, each --allow-changed SPEC (repeatable; a usage error under any
other policy) names what may differ beyond the additive rules:
  FILE:KEY.PATH              any difference at or under that key: a changed value, a key added or
                             removed (the key itself included) and a list at or under it that
                             grows, shrinks or reorders. KEY.PATH never reaches into a list, but
                             when a list is at or under it, everything inside that list's rows may
                             change too, so callers name a scalar key or a [FIELD=GLOB] spec where
                             that matters
  FILE:KEY.PATH[FIELD=GLOB]  KEY.PATH names a list of objects in base (anything else is one
                             violation naming the spec). Before the walk, the rows whose
                             str(row.get(FIELD)) matches GLOB are taken out of base and out of head,
                             each side on its own, and may be added, removed, changed or moved; the
                             rows left are walked as usual, pairwise by position, so their order
                             and content follow the additive rules. A row whose FIELD leaves the
                             glob is a matching base row gone and a new row left in head: a
                             violation. A row whose FIELD is missing or null is matched as the text
                             None. On quality_report.json:checks, FIELD must be name or status (the
                             two values the multiset rule compares; any other FIELD is a usage
                             error, for --allow-removed specs too)
  transactions.json:FIELD    FIELD, one row key, may differ on rows present on both sides (rows are
                             matched by id), appear on such a row or vanish from it; rows are never
                             added or removed and nothing else in transactions.json is allowed. Any
                             other transactions.json spec is refused
A whole FILE is never allowed to change, nor are manifest.json's files, schema_version,
generated_at and simplefin_fetched_at, nor any other `*_fetched_at` stamp (a stamp that differs
fails the precheck, exit 3, before any allowance is read). Under refactor the --allow-removed
specs come before the --allow-changed specs, each flag's specs in the order given: a removal two
specs cover, or a row two row specs on one list match, belongs to the first of them in that order,
and the report prints one line per allowance in the same order — `allowance <spec>: <n> removed` or
`allowance-changed <spec>: <n> changed`, 0 when it matched nothing, which passes. A changed spec's
<n> counts the differences the walk found under a key path (a list whose length changed is one),
the rows that differ under a row spec (the larger of its rows only in base and its rows only in
head, so a changed row is one and so is a status flip under the checks rule) and the rows whose FIELD
differs for transactions.json:FIELD. Under the checks rule a removal spec counts the check rows it
let go and covers only a (name, status) whose count fell. The fetched-at precheck reads
--allow-removed specs only.

A transaction id that occurs twice in either side's transactions.json is a violation under every
policy (the row-by-row match keys on id, so a duplicate would hide its twin from every check).

Values compare as JSON values under every policy: a boolean equals only a boolean (in Python
False == 0 and True == 1; here false -> 0 is a change). The walk takes 1 and 1.0 as one number;
rows a row spec takes, and the rows of the lists compared as multisets, are compared by their
canonical JSON text, where the two differ.

Output is aggregate only — counts, key names, category labels, vendor names, transaction
ids, quality check names and statuses, allowance specs — never descriptors, amounts or a check's
detail, unless --show-rows is given (on-box eyeballing only).

manifest.schema_version is the one value allowed to change (under every policy but
`identical`): head may be higher than base — a change that adds export keys bumps it — never lower,
and never a non-integer.

Precheck: a live refresh between the two ingests changes the inputs, which the manifest's
fetched-at stamps show. manifest.simplefin_fetched_at must be present on both sides and equal.
Every other manifest key ending `_fetched_at` must be equal when both sides have it; it may be
missing from head only when an --allow-removed `manifest.json:<key>` names it (its feed is being
removed), and it may never appear in head alone. Exit 3 when any of that fails (null on both
sides is equal: never fetched).
Both directories must hold a manifest.json — a dir without one (empty, or the parent of
exports/) would otherwise compare as "nothing changed" — else exit 2.

Exit codes: 0 within policy · 1 outside policy · 2 usage / unreadable dir / no manifest ·
3 inputs changed.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import math
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import yaml

POLICIES = ("identical", "schema-additive", "vendors-fallback-only", "categories-only", "splits", "removals",
            "refactor")
# the policies that take --allow-removed; --allow-changed is refactor's alone
REMOVAL_POLICIES = ("removals", "refactor")
REQUIRED_FETCHED_AT = ("simplefin_fetched_at",)
STAMP_SUFFIX = "_fetched_at"
# --allow-removed / --allow-changed: FILE, FILE:KEY.PATH or FILE:KEY.PATH[FIELD=GLOB]
ALLOWANCE_SPEC = re.compile(r"(?P<file>[A-Za-z0-9_.-]+\.json)"
                            r"(?::(?P<keypath>[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)"
                            r"(?:\[(?P<field>[A-Za-z0-9_-]+)=(?P<glob>[^\]]+)\])?)?")
ALLOWANCE_KINDS = {"--allow-removed": "removed", "--allow-changed": "changed"}     # flag -> Allowance.kind
TRANSACTIONS = "transactions.json"
# refactor compares this list as a multiset of (name, status); a row spec on it matches one of the two
QUALITY, CHECKS, CHECK_FIELDS = "quality_report.json", ("checks",), ("name", "status")
ABSENT = "<absent>"     # a check's missing name or status in its multiset key; never the JSON text of a value
# The manifest keys that say what the exports are and when they were made: a removal of one of these
# would not be a removal of a feature but of the comparison's own footing.
NEVER_REMOVED_MANIFEST_KEYS = frozenset({"files", "schema_version", "generated_at", *REQUIRED_FETCHED_AT})
CATEGORY_FIELDS = frozenset({"category", "subcategory", "category_source", "needs_review"})
LEARNED_SOURCES = frozenset({"learned"})
# Under vendors-fallback-only these lists may differ in their rows' `vendor` strings only
# (compared as multisets with `vendor` dropped); the rest of each file must be identical.
VENDOR_STRING_LISTS: dict[str, tuple[str, ...]] = {
    "recurring.json": ("items",),
    "quality_report.json": ("top_unmapped_vendors",),
    "vendors.json": ("vendors",),
    "assumptions.json": ("thresholds", "cancelled_subscriptions"),   # a config passthrough that renames reach
}
EXIT_OK, EXIT_VIOLATION, EXIT_USAGE, EXIT_INPUTS_CHANGED = 0, 1, 2, 3
MAX_MOVE_LINES = 40     # merge / split names printed per report


@dataclass
class Change:
    file: str
    path: tuple            # keys / indices from the file root; () is the file itself
    kind: str              # "added" | "removed" | "changed"
    old: object = None
    new: object = None

    @property
    def key(self) -> str:
        """Last string key on the path: the field a row-level change touched."""
        return next((str(p) for p in reversed(self.path) if isinstance(p, str)), "<file>")

    def describe(self) -> str:
        where = "/".join(str(p) for p in self.path) or "<file>"
        return f"{self.file}: {self.kind} at {where}"


@dataclass
class Report:
    policy: str
    inputs_changed: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    matrix: Counter = field(default_factory=Counter)   # (old "Cat/Sub", new "Cat/Sub") -> rows
    vendor_matrix: Counter = field(default_factory=Counter)   # (old vendor, new vendor) -> rows
    vendor_merges: dict[str, list[str]] = field(default_factory=dict)   # head vendor <- >= 2 base vendors
    vendor_splits: dict[str, list[str]] = field(default_factory=dict)   # base vendor -> >= 2 head vendors
    rows: list[str] = field(default_factory=list)      # per-row detail; printed only with --show-rows

    @property
    def ok(self) -> bool:
        return not self.violations and not self.inputs_changed


@dataclass(frozen=True)
class Allowance:
    """One --allow-removed (kind "removed") or --allow-changed (kind "changed") spec: a whole file
    (no keypath; removals only), a key and its subtree (keypath, no field) or the rows of a list
    whose `field` matches the glob `pattern`. A changed allowance on transactions.json names one row
    field: `keypath` is that one key."""
    file: str
    keypath: tuple[str, ...] = ()
    field: str | None = None
    pattern: str | None = None
    kind: str = "removed"

    @property
    def spec(self) -> str:
        """The spec as written on the command line (the grammar has one spelling per allowance)."""
        out = self.file + (":" + ".".join(self.keypath) if self.keypath else "")
        return out + (f"[{self.field}={self.pattern}]" if self.field is not None else "")

    @property
    def tx_field(self) -> bool:
        """A transactions.json:FIELD allowance (only --allow-changed accepts one)."""
        return self.kind == "changed" and self.file == TRANSACTIONS


def parse_allowance(spec: str, *, flag: str = "--allow-removed") -> Allowance:
    """An --allow-removed or --allow-changed spec as an Allowance; ValueError quoting the flag and
    the spec when it does not fit that flag's grammar or names something never allow-listed."""
    kind = ALLOWANCE_KINDS.get(flag)
    if kind is None:
        raise ValueError(f"unknown allowance flag {flag!r}; expected one of {', '.join(ALLOWANCE_KINDS)}")
    m = ALLOWANCE_SPEC.fullmatch(spec) if isinstance(spec, str) else None
    if m is None:
        forms = ("FILE, FILE:KEY.PATH or FILE:KEY.PATH[FIELD=GLOB]" if kind == "removed"
                 else "FILE:KEY.PATH, FILE:KEY.PATH[FIELD=GLOB] or transactions.json:FIELD")
        raise ValueError(f"{flag} {spec!r}: expected {forms} "
                         "(FILE a .json name, KEY and FIELD [A-Za-z0-9_-]+, GLOB without ']'; no part empty)")
    keypath = tuple(m["keypath"].split(".")) if m["keypath"] else ()
    if m["file"] == TRANSACTIONS:
        if kind == "changed" and len(keypath) == 1 and m["field"] is None:
            return Allowance(TRANSACTIONS, keypath, kind=kind)
        if kind == "changed":
            raise ValueError(f"{flag} {spec!r}: transactions.json takes only transactions.json:FIELD (one row "
                             "field, no key path, no row filter; rows are matched by id and are never added "
                             "or removed)")
        raise ValueError(f"{flag} {spec!r}: transactions.json is never allow-listed "
                         "(its rows are matched by id)")
    if m["file"] == "manifest.json" and not keypath:
        raise ValueError(f"{flag} {spec!r}: manifest.json is never allow-listed "
                         "(a directory without one is refused)")
    if m["file"] == "manifest.json" and keypath[0] in NEVER_REMOVED_MANIFEST_KEYS and kind == "removed":
        raise ValueError(f"{flag} {spec!r}: manifest.json's "
                         f"{', '.join(sorted(NEVER_REMOVED_MANIFEST_KEYS))} are never allow-listed")
    # a fetch stamp that differs fails the precheck (exit 3) before any allowance is read, so a
    # changed allowance on one could never take effect (a removal allowance lets a stamp vanish)
    if kind == "changed" and m["file"] == "manifest.json" and (keypath[0] in NEVER_REMOVED_MANIFEST_KEYS
                                                               or keypath[0].endswith(STAMP_SUFFIX)):
        raise ValueError(f"{flag} {spec!r}: manifest.json's "
                         f"{', '.join(sorted(NEVER_REMOVED_MANIFEST_KEYS))} and every other *{STAMP_SUFFIX} "
                         "stamp are never allow-listed (a stamp that differs means the inputs changed: exit 3)")
    if kind == "changed" and not keypath:
        raise ValueError(f"{flag} {spec!r}: a whole file is never allowed to change; name a key path "
                         "(FILE:KEY.PATH or FILE:KEY.PATH[FIELD=GLOB])")
    return Allowance(m["file"], keypath, m["field"], m["glob"], kind)


# ------------------------------------------------------------------ loading

def load_dir(path: Path) -> dict[str, object]:
    if not path.is_dir():
        raise FileNotFoundError(f"not a directory: {path}")
    return {p.name: json.loads(p.read_text("utf-8")) for p in sorted(path.glob("*.json"))}


def known_vendors_from_rules(path: Path) -> set[str]:
    """The `patterns[].vendor` names of a vendor_rules.yaml — rows normalized to one of
    these are rule hits, not fallback strings, so their vendor may never drift."""
    doc = yaml.safe_load(path.read_text("utf-8")) or {}
    return {str(e["vendor"]) for e in doc.get("patterns", []) if "vendor" in e}


def vendor_renames_from_rules(path: Path) -> dict[str, str]:
    """The `renames:` of a vendor_rules.yaml as {from: to}, spelled as written. A vendor whose
    stored spelling a rename explains may move to that `to:` under vendors-fallback-only. The
    shape is the config's own (`etl/enrich/renames.py`); a malformed entry is a ValueError here
    rather than a silently narrower (or wider) reading of what the gate allows."""
    doc = yaml.safe_load(path.read_text("utf-8")) or {}
    entries = doc.get("renames") or []
    if not isinstance(entries, list):
        raise ValueError(f"{path}: renames must be a list of {{from, to}} entries")
    out: dict[str, str] = {}
    for i, r in enumerate(entries):
        if (not isinstance(r, dict) or not isinstance(r.get("from"), str) or not isinstance(r.get("to"), str)
                or not r["from"] or not r["to"]):
            raise ValueError(f"{path}: renames[{i}] needs non-empty string from: and to:")
        out[r["from"]] = r["to"]
    _fold_renames(out)
    return out


def _fold_renames(renames: Mapping[str, str]) -> dict[str, str]:
    """{from.casefold(): to}; two names that differ only by case are one name, so a pair of
    them is ambiguous and refused (the config refuses it too)."""
    folded: dict[str, str] = {}
    for src, dst in renames.items():
        if src.casefold() in folded:
            raise ValueError(f"renames: {src!r} duplicates another from: once casefolded")
        folded[src.casefold()] = dst
    return folded


def check_fetched_at(base_manifest: dict, head_manifest: dict,
                     allowed_absent: frozenset[str] = frozenset()) -> list[str]:
    """Problems with the inputs precheck, empty when the two ingests read the same inputs.

    A stamp that differs between the manifests is reported by key name. The REQUIRED_FETCHED_AT
    keys must exist in both manifests, and a missing one is a problem named with the side:
    comparing with `.get()` alone would let a pair of manifests that both lack the key pass as
    `None == None`, certifying nothing. Every other key ending `_fetched_at` is the stamp of a
    feed some exports carry: compared when both sides have it, a problem when only head has it,
    and a problem when only base has it unless it is in `allowed_absent` (that feed is being
    removed). `allowed_absent` never excuses a required key. A key that is present with a null
    value on both sides is equal — that is a real "never fetched" state, not a missing key."""
    problems: list[str] = []
    for k in REQUIRED_FETCHED_AT:
        absent = [side for side, m in (("base", base_manifest), ("head", head_manifest))
                  if k not in m]
        if absent:
            problems.append(f"{k} (no such key in {' or '.join(absent)} manifest)"
                            if len(absent) == 1 else f"{k} (no such key in either manifest)")
        elif not _same(base_manifest[k], head_manifest[k]):
            problems.append(k)
    others = sorted(k for k in base_manifest.keys() | head_manifest.keys()
                    if isinstance(k, str) and k.endswith(STAMP_SUFFIX) and k not in REQUIRED_FETCHED_AT)
    for k in others:
        if k in base_manifest and k in head_manifest:
            if not _same(base_manifest[k], head_manifest[k]):
                problems.append(k)
        elif k in head_manifest:
            problems.append(f"{k} (no such key in base manifest)")
        elif k not in allowed_absent:
            problems.append(f"{k} (no such key in head manifest)")
    return problems


# ------------------------------------------------------------------ diffing

def _same(a, b) -> bool:
    """Equal as JSON values. Python's == makes False equal 0 and True equal 1, which would let a
    flag turn into a number unseen; here a boolean equals only a boolean, while 1 still equals 1.0
    (one JSON number). Objects compare key by key and arrays element by element."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(v, b[k]) for k, v in a.items())
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


def _member(v) -> tuple[bool, object]:
    """`v` as a set member that keeps a boolean apart from a number (False and 0 hash alike)."""
    return isinstance(v, bool), v


def _walk(file: str, base, head, path: tuple, out: list[Change]) -> None:
    if isinstance(base, dict) and isinstance(head, dict):
        for k in sorted(base.keys() | head.keys()):
            p = path + (k,)
            if k not in head:
                out.append(Change(file, p, "removed", base[k], None))
            elif k not in base:
                out.append(Change(file, p, "added", None, head[k]))
            else:
                _walk(file, base[k], head[k], p, out)
    elif isinstance(base, list) and isinstance(head, list):
        if len(base) != len(head):
            out.append(Change(file, path, "changed", base, head))
            return
        for i, (b, h) in enumerate(zip(base, head, strict=True)):
            _walk(file, b, h, path + (i,), out)
    elif not _same(base, head):
        out.append(Change(file, path, "changed", base, head))


def _rows_by_id(doc) -> dict[str, dict]:
    rows = doc.get("transactions", []) if isinstance(doc, dict) else []
    return {str(r.get("id")): r for r in rows if isinstance(r, dict)}


def duplicate_ids(doc) -> list[str]:
    """Row ids that occur more than once in a transactions.json document, sorted. `_rows_by_id`
    keeps only the last row of an id, so a duplicate would hide its twin from every check."""
    rows = doc.get("transactions", []) if isinstance(doc, dict) else []
    seen = Counter(str(r.get("id")) for r in rows if isinstance(r, dict))
    return sorted(i for i, n in seen.items() if n > 1)


def diff_file(name: str, base, head) -> list[Change]:
    """transactions.json is matched row-by-row on `id`; everything else structurally."""
    out: list[Change] = []
    if name == "transactions.json" and isinstance(base, dict) and isinstance(head, dict):
        b_rows, h_rows = _rows_by_id(base), _rows_by_id(head)
        for rid in sorted(b_rows.keys() - h_rows.keys()):
            out.append(Change(name, ("transactions", rid), "removed", b_rows[rid], None))
        for rid in sorted(h_rows.keys() - b_rows.keys()):
            out.append(Change(name, ("transactions", rid), "added", None, h_rows[rid]))
        for rid in sorted(b_rows.keys() & h_rows.keys()):
            _walk(name, b_rows[rid], h_rows[rid], ("transactions", rid), out)
        rest_b = {k: v for k, v in base.items() if k != "transactions"}
        rest_h = {k: v for k, v in head.items() if k != "transactions"}
        _walk(name, rest_b, rest_h, (), out)
        return out
    _walk(name, base, head, (), out)
    return out


# ------------------------------------------------------------------ removals

def _canon(row) -> str:
    """A row as canonical JSON: two rows are the same row when these strings are equal."""
    return json.dumps(row, sort_keys=True)


def _list_at(doc, keypath: tuple[str, ...]) -> list | None:
    """The list `keypath` names in `doc` (dict keys from the root); None when anything else is there."""
    for k in keypath:
        if not isinstance(doc, dict) or k not in doc:
            return None
        doc = doc[k]
    return doc if isinstance(doc, list) else None


def _with_list(doc: dict, keypath: tuple[str, ...], rows: list) -> dict:
    """`doc` with the list at `keypath` replaced by `rows` (copied along the path only)."""
    out = dict(doc)
    out[keypath[0]] = rows if len(keypath) == 1 else _with_list(out[keypath[0]], keypath[1:], rows)
    return out


def _owner(row, specs: Sequence[Allowance]) -> Allowance | None:
    """The first row allowance whose glob matches `row`, None when none does or `row` is no object."""
    if not isinstance(row, dict):
        return None
    return next((a for a in specs if fnmatch.fnmatchcase(str(row.get(a.field)), a.pattern)), None)


def _first_out_of_place(base_keys: list, head_keys: list, deletable: list[bool]) -> int | None:
    """The index of the first head key out of place, None when `head_keys` is `base_keys` with only
    deletable keys taken out. Equal keys are interchangeable, so matching each head key to the
    earliest equal base key is never worse than a later one."""
    i = 0
    for j, key in enumerate(head_keys):
        while i < len(base_keys) and base_keys[i] != key:
            if not deletable[i]:
                return j
            i += 1
        if i == len(base_keys):
            return j
        i += 1
    return None


def _place_keys(rows: Sequence[tuple[dict, Allowance | None]], by_rank: bool) -> list[tuple]:
    """One key per (row, owner) for the place check: the row's canonical JSON, or, with `by_rank`,
    for a row no spec owns its rank among those rows. The walk pairs the unowned rows by position
    and judges their content itself, so by rank a content change there (an added key) leaves the
    place check standing."""
    keys: list[tuple] = []
    rank = 0
    for r, o in rows:
        if by_rank and o is None:
            keys.append(("rank", rank))
            rank += 1
        else:
            keys.append(("row", _canon(r)))
    return keys


def take_out_rows(base: dict[str, object], head: dict[str, object], allowances: Sequence[Allowance],
                  *, additive: bool = False
                  ) -> tuple[dict[str, object], dict[str, object], list[str], dict[Allowance, int]]:
    """The row allowances' rows taken out of both sides before the walk.

    Returns the two document maps with those rows gone (the inputs are not modified), the
    violations and a count per row allowance. Allowances on one list are applied together, a row
    belonging to the first spec whose glob matches it. Rows are paired by equality. A head row a
    removal spec takes must equal a distinct base row that spec takes, else one violation for the
    spec; the spec counts its base rows left over (the removals). A changed spec's rows are free —
    added, removed, changed or moved — and it counts the larger of its base rows with no equal head
    row and its head rows with no equal base row. With every removal spec on the list clean and the
    remaining rows equal, the rows a removal spec keeps must also stand where they stood (head is
    base with only taken rows deleted, the rows changed specs take set aside on both sides).
    `additive` is for a policy under which the remaining rows may gain keys (refactor): the place
    check then runs whenever the remaining rows are as many on both sides (a different number is
    the walk's violation) and reads each remaining row as its rank among them, so a kept row that
    moved is reported even when the rows around it changed. A list missing from head, or no longer
    a list there, is left to the walk. Violations name specs and paths, never a row."""
    base, head = dict(base), dict(head)
    violations: list[str] = []
    counts: dict[Allowance, int] = {}
    lists: dict[tuple[str, tuple[str, ...]], list[Allowance]] = {}
    for a in allowances:
        if a.field is not None:
            lists.setdefault((a.file, a.keypath), []).append(a)
    for (name, keypath), specs in lists.items():
        counts.update((a, 0) for a in specs)
        b_rows = _list_at(base.get(name), keypath)
        if b_rows is None or not all(isinstance(r, dict) for r in b_rows):
            violations.extend(f"allowance {a.spec}: not a list of objects in base" for a in specs)
            continue
        h_rows = _list_at(head.get(name), keypath)
        if h_rows is None:
            continue
        b_own, h_own = [_owner(r, specs) for r in b_rows], [_owner(r, specs) for r in h_rows]
        clean = True
        for a in specs:
            left = Counter(_canon(r) for r, o in zip(b_rows, b_own, strict=True) if o is a)
            unmatched = 0
            for r, o in zip(h_rows, h_own, strict=True):
                if o is a:
                    k = _canon(r)
                    if left[k]:
                        left[k] -= 1
                    else:
                        unmatched += 1
            if a.kind == "changed":
                counts[a] = max(sum(left.values()), unmatched)
                continue
            counts[a] = sum(left.values())
            if unmatched:
                clean = False
                violations.append(f"{name}: {'.'.join(keypath)} row matching {a.spec} is new or changed in head")
        rest_b = [r for r, o in zip(b_rows, b_own, strict=True) if o is None]
        rest_h = [r for r, o in zip(h_rows, h_own, strict=True) if o is None]
        comparable = (len(rest_b) == len(rest_h) if additive
                      else [_canon(r) for r in rest_b] == [_canon(r) for r in rest_h])
        if clean and comparable:
            # rows a changed spec takes may stand anywhere; the others keep their places
            fixed_b = [(r, o) for r, o in zip(b_rows, b_own, strict=True) if o is None or o.kind == "removed"]
            fixed_h = [(r, o) for r, o in zip(h_rows, h_own, strict=True) if o is None or o.kind == "removed"]
            j = _first_out_of_place(_place_keys(fixed_b, additive), _place_keys(fixed_h, additive),
                                    [o is not None for _, o in fixed_b])
            if j is not None:
                moved = fixed_h[j][1] or next((a for a in specs if a.kind == "removed"), specs[0])
                violations.append(f"{name}: {'.'.join(keypath)} row matching {moved.spec} moved in head")
        base[name] = _with_list(base[name], keypath, rest_b)
        head[name] = _with_list(head[name], keypath, rest_h)
    return base, head, violations, counts


def _check_key(row: dict) -> tuple[str, ...]:
    """A check's (name, status) as a multiset key: each value's JSON text, or ABSENT for a missing
    key, so null, a missing key and the string "None" are three values, as are 1 and "1"."""
    return tuple(json.dumps(row[f], sort_keys=True) if f in row else ABSENT for f in CHECK_FIELDS)


def _check_text(row: dict) -> tuple[str, str]:
    """A check's (name, status) as printed and as a row spec's glob reads it: plain text, so a
    missing or null value reads None, as it does for any row filter."""
    return str(row.get("name")), str(row.get("status"))


def _check_owner(key: tuple[str, str], fell: bool, specs: Sequence[Allowance]) -> Allowance | None:
    """The first allowance on quality_report.json:checks that covers the (name, status) `key` (as
    text) whose count changed (`fell`: it went down): a changed spec any difference, a removal spec
    only a fall; a key-path spec every key, a row spec the keys whose name or status its glob
    matches (compare refuses a row spec on any other FIELD under refactor)."""
    for a in specs:
        if a.kind == "removed" and not fell:
            continue
        if a.field is None:
            return a
        if a.field in CHECK_FIELDS and fnmatch.fnmatchcase(key[CHECK_FIELDS.index(a.field)], a.pattern):
            return a
    return None


def take_out_checks(base: dict[str, object], head: dict[str, object], allowances: Sequence[Allowance]
                    ) -> tuple[dict[str, object], dict[str, object], list[str], list[str], dict[Allowance, int],
                               bool]:
    """refactor: quality_report.json `checks` taken out of both sides and compared as a multiset of
    (name, status); every other key of a check and the list's order are ignored.

    Applies only when both sides hold a quality_report.json object whose `checks` is a list of
    objects; otherwise the maps come back untouched with no lines, no counts and False, and the list
    is left to the row allowances and the walk. Returns the two document maps without `checks` (the
    inputs are not modified), one line per (name, status) whose count differs, sorted by the
    printed text (`checks: <name> <status> ×a → ×b`), the lines no allowance covers (the
    violations), a count for every allowance on quality_report.json:checks — a removal spec counts
    the check rows it let go, a changed spec the larger of its rows gone and its rows new, so a
    status flip counts once — and whether the two lists differ at all (order or details included),
    so the summary never calls them identical. The values count by their JSON text (`_check_key`)
    and print as plain text. Names and statuses only — never a detail."""
    b_doc, h_doc = base.get(QUALITY), head.get(QUALITY)
    b_list, h_list = _list_at(b_doc, CHECKS), _list_at(h_doc, CHECKS)
    if (b_list is None or h_list is None
            or not all(isinstance(r, dict) for r in b_list) or not all(isinstance(r, dict) for r in h_list)):
        return base, head, [], [], {}, False
    specs = [a for a in allowances if a.file == QUALITY and a.keypath == CHECKS]
    text: dict[tuple[str, ...], tuple[str, str]] = {}
    for r in (*b_list, *h_list):
        text.setdefault(_check_key(r), _check_text(r))
    b_n, h_n = Counter(map(_check_key, b_list)), Counter(map(_check_key, h_list))
    gone: Counter = Counter()
    new: Counter = Counter()
    lines: list[str] = []
    violations: list[str] = []
    for key in sorted(b_n.keys() | h_n.keys(), key=lambda k: (text[k], k)):
        was, now = b_n[key], h_n[key]
        if was == now:
            continue
        name, status = text[key]
        line = f"checks: {name} {status} ×{was} → ×{now}"
        lines.append(line)
        owner = _check_owner(text[key], now < was, specs)
        if owner is None:
            violations.append(line)
        elif now < was:
            gone[owner] += was - now
        else:
            new[owner] += now - was
    counts = {a: gone[a] if a.kind == "removed" else max(gone[a], new[a]) for a in specs}
    differ = _canon(b_list) != _canon(h_list)
    base, head = dict(base), dict(head)
    base[QUALITY], head[QUALITY] = _prune(b_doc, CHECKS), _prune(h_doc, CHECKS)
    return base, head, lines, violations, counts, differ


def _covering(c: Change, allowances: Sequence[Allowance]) -> Allowance | None:
    """The first file or key --allow-removed allowance that covers removal `c`: a file allowance
    the file itself, a key allowance its key and anything below it (path elements compare exactly,
    so a KEY never matches a list index). Row allowances act before the walk and cover nothing
    here."""
    for a in allowances:
        if a.kind != "removed" or a.file != c.file or a.field is not None:
            continue
        if (c.path[:len(a.keypath)] == a.keypath) if a.keypath else c.path == ():
            return a
    return None


def _covering_change(c: Change, allowances: Sequence[Allowance]) -> Allowance | None:
    """The first --allow-changed key allowance that covers `c`, whatever its kind: a key path its
    key and anything below it (a KEY never matches a list index), transactions.json:FIELD that
    field and anything below it on a row both sides have (a whole row, added or removed, sits at
    a shorter path and is never covered). Row allowances act before the walk."""
    for a in allowances:
        if a.kind != "changed" or a.file != c.file or a.field is not None:
            continue
        if a.tx_field:
            if len(c.path) >= 3 and c.path[0] == "transactions" and c.path[2] == a.keypath[0]:
                return a
        elif c.path[:len(a.keypath)] == a.keypath:
            return a
    return None


def _claim(c: Change, allowances: Sequence[Allowance]) -> Allowance | None:
    """The allowance that admits `c`: a removal goes to the first --allow-removed spec covering it,
    then, like any other change, to the first --allow-changed spec covering it."""
    if c.kind == "removed":
        a = _covering(c, allowances)
        if a is not None:
            return a
    return _covering_change(c, allowances)


def _files_without(old, gone: frozenset[str]) -> list | None:
    """Base's manifest.files with the allowed-and-gone files taken out, order kept; None when
    base's value is not a list."""
    if not isinstance(old, list):
        return None
    return [n for n in old if not (isinstance(n, str) and n in gone)]


def _absent_stamps(allowances: Sequence[Allowance]) -> frozenset[str]:
    """The manifest keys an --allow-removed `manifest.json:<key>` names: stamps head may lack."""
    return frozenset(a.keypath[0] for a in allowances
                     if a.kind == "removed" and a.file == "manifest.json" and len(a.keypath) == 1
                     and a.field is None)


# ------------------------------------------------------------------ policies

def _same_ignoring(a: list, b: list, drop: set[str]) -> bool:
    """Equal as multisets once the `drop` keys are removed from every row."""
    def norm(rows):
        return sorted(json.dumps({k: v for k, v in r.items() if k not in drop}, sort_keys=True)
                      for r in rows if isinstance(r, dict))
    return len(a) == len(b) and norm(a) == norm(b)


def _dig(doc, path: tuple[str, ...]) -> list | None:
    """The list at `path` in `doc` ([] when absent); None when something else sits there."""
    for k in path:
        if not isinstance(doc, dict) or k not in doc:
            return []
        doc = doc[k]
    return doc if isinstance(doc, list) else None


def _prune(doc: dict, path: tuple[str, ...]) -> dict:
    """`doc` without the list at `path` (copied along the path only)."""
    out = dict(doc)
    k = path[0]
    if len(path) == 1:
        out.pop(k, None)
    elif isinstance(out.get(k), dict):
        out[k] = _prune(out[k], path[1:])
    return out


def vendor_move_explained(old, new, renames: Mapping[str, str]) -> bool:
    """May `old` become `new` under vendors-fallback-only? Only as a re-casing of itself
    (smart_title's fix-ups: Cora'S -> Cora's, Atm -> ATM) or as a `renames:` entry says:
    `old` casefolds to the entry's `from` and `new` is exactly its `to`. `renames` is keyed by
    the casefolded `from`. Anything else is a respelling — and when two vendors do it to the
    same target, a merge."""
    if not (isinstance(old, str) and isinstance(new, str)):
        return False
    return new.casefold() == old.casefold() or renames.get(old.casefold()) == new


def vendor_moves(base_rows: Mapping[str, dict], head_rows: Mapping[str, dict]
                 ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(merges, splits) of the base -> head vendor mapping over every row present on both
    sides, changed or not: a merge is a head vendor fed by >= 2 distinct base vendors
    ({head: [bases]}), a split a base vendor that ended up as >= 2 head vendors
    ({base: [heads]}). Unchanged rows count — a respelling INTO an existing vendor is a merge."""
    heads_of: dict[str, set[str]] = defaultdict(set)
    bases_of: dict[str, set[str]] = defaultdict(set)
    for rid in base_rows.keys() & head_rows.keys():
        old, new = base_rows[rid].get("vendor"), head_rows[rid].get("vendor")
        if isinstance(old, str) and isinstance(new, str):
            heads_of[old].add(new)
            bases_of[new].add(old)
    merges = {new: sorted(olds) for new, olds in sorted(bases_of.items()) if len(olds) >= 2}
    splits = {old: sorted(news) for old, news in sorted(heads_of.items()) if len(news) >= 2}
    return merges, splits


def bad_merges(merges: Mapping[str, list[str]], renames: Mapping[str, str]) -> list[str]:
    """One violation per merge that is not a pure re-casing: every vendor feeding head vendor
    H must be a case variant of H or be sent to H by a `renames:` entry (`renames` keyed by
    the casefolded `from`). Two spellings of one name collapsing into one is smart_title's
    doing and fine (listed in the report); two different vendors collapsing is not."""
    out = []
    for head_vendor, bases in merges.items():
        unexplained = [b for b in bases if not vendor_move_explained(b, head_vendor, renames)]
        if unexplained:
            shown = ", ".join(repr(b) for b in unexplained[:8]) + (
                f" (+{len(unexplained) - 8} more)" if len(unexplained) > 8 else "")
            out.append(f"vendor merge into {head_vendor!r} from distinct vendors {shown}")
    return out


def _cents(v) -> int | None:
    """An export amount in whole cents, computed exactly from the number as written in the JSON
    (the shortest decimal that round-trips, so 19.99 is 1999 and 3.004 is not a cent amount at all).
    None for anything that is not a finite number of whole cents — a missing, string, bool, null,
    NaN, infinite or sub-cent amount can never be part of a sum that balances. Integers of any size
    are fine (Fraction never overflows), so there is nothing to catch."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    c = Fraction(v if isinstance(v, int) else repr(v)) * 100
    return int(c) if c.denominator == 1 else None


def child_shape(base_rows: Mapping[str, dict], head_rows: Mapping[str, dict]) -> frozenset[str] | None:
    """The key set a split child must have: the one the head's ordinary (non-child) rows share —
    the most common key set when they disagree (a stray row is reported on its own) — else, with
    no ordinary row in head, the base rows' key set plus the two split keys. None when neither
    side has a row to take it from."""
    ordinary = Counter(frozenset(r) for r in head_rows.values() if r.get("parent_id") is None)
    if ordinary:
        return max(ordinary.items(), key=lambda kv: (kv[1], sorted(kv[0])))[0]
    base = Counter(frozenset(r) for r in base_rows.values())
    if base:
        return max(base.items(), key=lambda kv: (kv[1], sorted(kv[0])))[0] | {"parent_id", "is_parent"}
    return None


def split_structure(rows: Mapping[str, dict], shape: frozenset[str] | None = None) -> list[str]:
    """splits: every child has exactly the keys of an ordinary row (`shape`, by default taken from
    the ordinary rows of `rows`), names a head row flagged is_parent, carries a whole-cent amount
    and is_parent false; every parent has >= 2 children whose amounts (in cents) sum to its own.
    Ids and key names only — never an amount."""
    out: list[str] = []
    kids: dict[str, list[dict]] = defaultdict(list)
    if shape is None:
        shape = child_shape({}, rows)
    for rid, r in sorted(rows.items()):
        pid = r.get("parent_id")
        if pid is None:
            continue
        if shape is not None and set(r) != shape:
            diff = [f"{word}: {', '.join(sorted(keys))}" for word, keys in
                    (("extra", set(r) - shape), ("missing", shape - set(r))) if keys]
            out.append(f"split child {rid} does not have an ordinary row's keys ({'; '.join(diff)})")
        flag = r.get("is_parent", False)
        if flag is True:
            out.append(f"split row {rid} is both a child and a parent")
        elif flag is not False:
            out.append(f"split child {rid} has a non-boolean is_parent")
        if _cents(r.get("amount")) is None:
            out.append(f"split child {rid} has no finite whole-cent amount")
        parent = rows.get(str(pid))
        if parent is None or parent.get("is_parent") is not True:
            out.append(f"split child {rid} names no head row flagged is_parent ({pid!r})")
            continue
        kids[str(pid)].append(r)
    for rid, r in sorted(rows.items()):
        if r.get("is_parent") is not True:
            continue
        ch = kids.get(rid, [])
        if len(ch) < 2:
            out.append(f"split parent {rid} has {len(ch)} child row(s); a split needs at least 2")
            continue
        parent_cents = _cents(r.get("amount"))
        total = [_cents(c.get("amount")) for c in ch]
        if parent_cents is None:
            out.append(f"split parent {rid} has no finite whole-cent amount")
        elif None not in total and sum(total) != parent_cents:     # a bad child was reported above
            out.append(f"split parent {rid}: its children do not sum to its amount")
    return out


class _Ctx:
    def __init__(self, base: dict[str, object], head: dict[str, object],
                 known_vendors: frozenset[str], renames: Mapping[str, str] | None = None, *,
                 allowances: Sequence[Allowance] = (), row_counts: Mapping[Allowance, int] | None = None,
                 check_lines: Sequence[str] = (), check_counts: Mapping[Allowance, int] | None = None,
                 checks_differ: bool = False):
        self.base, self.head, self.known_vendors = base, head, known_vendors
        self.renames = _fold_renames(renames or {})
        self.base_rows = _rows_by_id(base.get("transactions.json", {}))
        self.head_rows = _rows_by_id(head.get("transactions.json", {}))
        self._flags: dict[str, bool] = {}
        self.allowances = tuple(allowances)
        self.row_counts = dict(row_counts or {})          # row allowance -> rows it took (take_out_rows)
        self.check_lines = list(check_lines)              # refactor's checks multiset differences
        self.check_counts = dict(check_counts or {})      # allowance on checks -> its count there
        self.checks_differ = checks_differ                # the checks lists the multiset step took differ
        # files a file allowance lets go that base has and head does not
        self.gone_files = frozenset(a.file for a in self.allowances
                                    if a.kind == "removed" and not a.keypath
                                    and a.file in base and a.file not in head)

    def vendor_strings_only(self, name: str) -> bool:
        """True when `name` differs from base only in vendor strings (or is a new file)."""
        if name not in self._flags:
            self._flags[name] = self._vendor_flag(name)
        return self._flags[name]

    def _vendor_flag(self, name: str) -> bool:
        b, h = self.base.get(name), self.head.get(name)
        if b is None:
            return True
        path = VENDOR_STRING_LISTS.get(name)
        if path is None or not isinstance(b, dict) or not isinstance(h, dict):
            return False
        b_list, h_list = _dig(b, path), _dig(h, path)
        if b_list is None or h_list is None:
            return _same(b, h)        # not a list of rows: nothing to compare "vendor strings only"
        return (_same_ignoring(b_list, h_list, {"vendor"})
                and _same(_prune(b, path), _prune(h, path)))


def _is_schema_version_change(c: Change) -> bool:
    return c.file == "manifest.json" and c.path == ("schema_version",) and c.kind == "changed"


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _schema_version_increased(c: Change) -> bool:
    return _is_int(c.old) and _is_int(c.new) and c.new > c.old


def _is_by_category(c: Change) -> bool:
    return (c.file == "cashflow_monthly.json" and len(c.path) >= 3
            and c.path[0] == "months" and c.path[2] == "by_category")


def _in_free_zone(c: Change, policy: str, ctx: _Ctx) -> bool:
    """Subtrees where a policy accepts any kind of change."""
    f, p = c.file, c.path
    if policy == "vendors-fallback-only":
        return f in VENDOR_STRING_LISTS and ctx.vendor_strings_only(f)
    if policy == "categories-only":
        return f in ("quality_report.json", "vendors.json") or _is_by_category(c)
    if policy == "splits":
        if f in ("quality_report.json", "recurring.json", "vendors.json") or _is_by_category(c):
            return True
        if f == "transactions.json" and p == ("count",):
            return True
        if f == "manifest.json" and p == ("transaction_count",):
            return True
        if f == "transactions.json" and len(p) == 2 and c.kind == "added":
            return isinstance(c.new, dict) and c.new.get("parent_id") is not None
    return False


def _cascade_row(ctx: _Ctx, rid: str) -> bool:
    """categories-only: did the learned-rule cascade recategorize this row (new source learned AND
    the category pair moved)? Only such a row may also move its `tags`; a tag move on any other
    row is a violation."""
    b, h = ctx.base_rows.get(rid, {}), ctx.head_rows.get(rid, {})
    return h.get("category_source") in LEARNED_SOURCES and _label(b) != _label(h)


def _allowed_removal(c: Change, ctx: _Ctx) -> bool:
    """removals: a schema_version increase, a removal an allowance covers, and manifest.files
    losing exactly the allowed files head no longer has. Nothing added, nothing else changed."""
    if _is_schema_version_change(c):
        return _schema_version_increased(c)
    if c.kind == "removed":
        return _covering(c, ctx.allowances) is not None
    if c.kind == "changed" and c.file == "manifest.json" and c.path == ("files",):
        return isinstance(c.new, list) and _same(_files_without(c.old, ctx.gone_files), c.new)
    return False


def _allowed_refactor(c: Change, ctx: _Ctx) -> bool:
    """refactor: a schema_version increase, whatever an allowance admits (`_claim`), manifest.files
    keeping every name but the allowed-and-gone files, and otherwise the schema-additive rules."""
    if _is_schema_version_change(c):
        return _schema_version_increased(c)
    if _claim(c, ctx.allowances) is not None:
        return True
    if c.kind == "changed" and c.file == "manifest.json" and c.path == ("files",):
        kept = _files_without(c.old, ctx.gone_files)
        return (kept is not None and isinstance(c.new, list)
                and set(map(_member, kept)) <= set(map(_member, c.new)))
    return _allowed(c, "schema-additive", ctx)


def _allowed(c: Change, policy: str, ctx: _Ctx) -> bool:
    if policy == "identical":
        return False
    if policy == "removals":
        return _allowed_removal(c, ctx)
    if policy == "refactor":
        return _allowed_refactor(c, ctx)
    if _in_free_zone(c, policy, ctx):
        return True
    if _is_schema_version_change(c):
        return _schema_version_increased(c)
    is_tx_row = c.file == "transactions.json" and len(c.path) == 2 and c.path[0] == "transactions"
    is_tx_field = c.file == "transactions.json" and len(c.path) == 3 and c.path[0] == "transactions"
    if c.kind == "added":
        if policy == "splits" and c.file == "transactions.json" and len(c.path) >= 3 and c.path[0] == "transactions":
            # exactly the two split keys may appear on a row the base already had, and nothing else: a
            # split child is always a NEW row, so parent_id may only appear as null; is_parent is
            # the parent flag, a bool either way
            if len(c.path) == 3 and c.path[2] == "parent_id":
                return c.new is None
            if len(c.path) == 3 and c.path[2] == "is_parent":
                return isinstance(c.new, bool)
            return False
        return not is_tx_row            # a new key or file is additive; a new row is not
    if c.kind == "removed":
        return False
    # kind == "changed"
    if c.file == "manifest.json" and c.path == ("files",):
        return (isinstance(c.old, list) and isinstance(c.new, list)
                and set(map(_member, c.old)) <= set(map(_member, c.new)))
    if policy == "vendors-fallback-only" and is_tx_field and c.path[2] == "vendor":
        # a patterns hit never moves; a fallback vendor only re-cases (or follows a renames: entry)
        return (ctx.base_rows.get(c.path[1], {}).get("vendor") not in ctx.known_vendors
                and vendor_move_explained(c.old, c.new, ctx.renames))
    if policy == "categories-only" and is_tx_field:
        return c.path[2] in CATEGORY_FIELDS or (c.path[2] == "tags" and _cascade_row(ctx, str(c.path[1])))
    # tags may change at any depth (e.g., individual elements) on cascade rows
    if policy == "categories-only" and c.file == "transactions.json" and len(c.path) >= 3 and c.path[0] == "transactions" and c.path[2] == "tags":
        return _cascade_row(ctx, str(c.path[1]))
    if policy == "categories-only" and c.file == "recurring.json":
        return len(c.path) == 3 and c.path[0] == "items" and c.path[2] == "category"
    if policy == "splits" and is_tx_field and c.path[2] == "needs_review":
        # a split parent is display-only and must not sit in the inbox — its flag may clear,
        # nothing else on it moves, and no other row may touch the flag
        return (c.old is True and c.new is False
                and ctx.head_rows.get(str(c.path[1]), {}).get("is_parent") is True)
    return False


# ------------------------------------------------------------------ reporting

def _label(row: dict) -> str:
    return f"{row.get('category')}/{row.get('subcategory') or '-'}"


def _summarize(rep: Report, changes: list[Change], ctx: _Ctx, policy: str) -> None:
    per_file: dict[str, Counter] = defaultdict(Counter)
    bumps: dict[str, str] = {}
    for c in changes:
        if _is_schema_version_change(c):
            bumps[c.file] = (f"schema_version {c.old}->{c.new}"
                             if _is_int(c.old) and _is_int(c.new) else "schema_version changed")
        else:
            per_file[c.file][(c.kind, c.key)] += 1
    names = sorted(ctx.base.keys() | ctx.head.keys())
    rep.notes.append("inputs: fetched-at stamps "
                     + ("equal" if not rep.inputs_changed
                        else f"DIFFER ({', '.join(rep.inputs_changed)})"))
    rep.notes.append(f"files compared: {len(names)}")
    rows_out: dict[str, Counter] = defaultdict(Counter)  # file -> {(kind, key path): rows the row allowances took}
    for a, n in ctx.row_counts.items():
        rows_out[a.file][(a.kind, ".".join(a.keypath))] += n
    for name in names:
        parts = ([bumps[name]] if name in bumps else []) + [
            f"{kind} {key} x{n}" for (kind, key), n in sorted(per_file.get(name, {}).items())] + [
            f"{kind} {path} rows x{n}" for (kind, path), n in sorted(rows_out.get(name, {}).items()) if n]
        if name == QUALITY and ctx.check_lines:
            parts.append(f"changed checks (name, status) x{len(ctx.check_lines)}")
        elif name == QUALITY and ctx.checks_differ:
            parts.append("checks equal as (name, status)")       # order or details moved: not identical
        rep.notes.append(f"{name}: {', '.join(parts)}" if parts else f"{name}: identical")
    rep.notes.extend(ctx.check_lines)
    if policy in REMOVAL_POLICIES:
        claims: dict[Allowance, list[Change]] = defaultdict(list)
        for c in changes:
            a = _claim(c, ctx.allowances)
            if a is not None:
                claims[a].append(c)
        for a in ctx.allowances:
            walked = (len({c.path[1] for c in claims[a]}) if a.tx_field else len(claims[a]))
            n = ctx.row_counts.get(a, 0) + ctx.check_counts.get(a, 0) + walked
            rep.notes.append(f"allowance {a.spec}: {n} removed" if a.kind == "removed"
                             else f"allowance-changed {a.spec}: {n} changed")
    tx_changes = [c for c in changes
                  if c.file == "transactions.json" and len(c.path) >= 2 and c.path[0] == "transactions"]
    if ctx.base_rows or ctx.head_rows:
        removed = sum(1 for c in tx_changes if len(c.path) == 2 and c.kind == "removed")
        added = sum(1 for c in tx_changes if len(c.path) == 2 and c.kind == "added")
        rep.notes.append(f"transactions: {len(ctx.base_rows)} base rows, {len(ctx.head_rows)} head rows, "
                         f"{removed} removed, {added} added")
    if policy == "splits":
        parents = sum(1 for r in ctx.head_rows.values() if r.get("is_parent") is True)
        children = sum(1 for r in ctx.head_rows.values() if r.get("parent_id") is not None)
        rep.notes.append(f"splits: {parents} parents, {children} children")
        rep.violations.extend(split_structure(ctx.head_rows, child_shape(ctx.base_rows, ctx.head_rows)))
    fields_by_row: dict[str, dict[str, tuple]] = defaultdict(dict)
    for c in tx_changes:
        if len(c.path) == 3 and c.kind == "changed":
            fields_by_row[str(c.path[1])][str(c.path[2])] = (c.old, c.new)
    not_learned: list[str] = []
    for rid in sorted(fields_by_row):
        fields = fields_by_row[rid]
        b, h = ctx.base_rows.get(rid, {}), ctx.head_rows.get(rid, {})
        if {"category", "subcategory"} & fields.keys():
            rep.matrix[(_label(b), _label(h))] += 1
        if "vendor" in fields:
            old_v, new_v = fields["vendor"]
            rep.vendor_matrix[(str(old_v), str(new_v))] += 1
        if CATEGORY_FIELDS & fields.keys() and h.get("category_source") not in LEARNED_SOURCES:
            not_learned.append(rid)
        detail = "; ".join(f"{k} {o!r} -> {n!r}" for k, (o, n) in sorted(fields.items()))
        rep.rows.append(f"{rid} {h.get('date')} {h.get('amount')} {h.get('description')!r}: {detail}")
    if policy == "categories-only" and not_learned:
        rep.violations.append(
            f"{len(not_learned)} changed rows whose new category_source is not learned "
            f"(e.g. {', '.join(not_learned[:5])})")
    rep.vendor_merges, rep.vendor_splits = vendor_moves(ctx.base_rows, ctx.head_rows)
    if policy == "vendors-fallback-only":
        # second line behind the per-row rule: two distinct vendors must never collapse into one
        rep.violations.extend(bad_merges(rep.vendor_merges, ctx.renames))


def compare(base_dir: Path, head_dir: Path, policy: str, *,
            known_vendors: frozenset[str] = frozenset(),
            vendor_renames: Mapping[str, str] | None = None,
            check_inputs: bool = True,
            allow_removed: Sequence[str] = (),
            allow_changed: Sequence[str] = ()) -> Report:
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}; choose from {', '.join(POLICIES)}")
    if isinstance(allow_removed, str):
        raise ValueError("allow_removed takes a sequence of specs, not one string")
    if isinstance(allow_changed, str):
        raise ValueError("allow_changed takes a sequence of specs, not one string")
    if allow_removed and policy not in REMOVAL_POLICIES:
        # every other policy has its own reading of a removal: an allowance there would mean nothing
        raise ValueError(f"--allow-removed applies only under removals and refactor, not {policy}")
    if allow_changed and policy != "refactor":
        raise ValueError(f"--allow-changed applies only under refactor, not {policy}")
    # removal specs first, so they claim a difference or a row before any changed spec does
    allowances = tuple(dict.fromkeys(
        [parse_allowance(s, flag="--allow-removed") for s in allow_removed or ()]
        + [parse_allowance(s, flag="--allow-changed") for s in allow_changed or ()]))
    if policy == "refactor":
        for a in allowances:
            if a.file == QUALITY and a.keypath == CHECKS and a.field is not None and a.field not in CHECK_FIELDS:
                # the multiset rule keeps only (name, status): a glob on any other field would match nothing
                flag = next(f for f, k in ALLOWANCE_KINDS.items() if k == a.kind)
                raise ValueError(f"{flag} {a.spec!r}: under refactor quality_report.json:checks compares as "
                                 f"(name, status), so a row spec on it takes FIELD {' or '.join(CHECK_FIELDS)}")
    if policy == "vendors-fallback-only" and not known_vendors:
        # with no rule-hit vendor names every row would count as a fallback row, i.e. any
        # vendor string may change: the most permissive reading, never a safe default
        raise ValueError("vendors-fallback-only needs a non-empty known_vendors set "
                         "(the vendor_rules.yaml patterns[].vendor names)")
    base, head = load_dir(Path(base_dir)), load_dir(Path(head_dir))
    for side, dir_, docs_ in (("base", base_dir, base), ("head", head_dir, head)):
        if not isinstance(docs_.get("manifest.json"), dict):
            # no manifest = not an exports dir (empty, or its parent): comparing two of
            # those would report "nothing changed" and pass every gate vacuously
            raise FileNotFoundError(f"no manifest.json object in {side} dir {dir_} "
                                    "(is it the exports/ directory itself?)")
    rep = Report(policy=policy)
    if check_inputs:
        rep.inputs_changed = check_fetched_at(base["manifest.json"], head["manifest.json"],
                                              _absent_stamps(allowances))
    check_lines: list[str] = []
    check_violations: list[str] = []
    check_counts: dict[Allowance, int] = {}
    checks_differ = False
    if policy == "refactor":
        base, head, check_lines, check_violations, check_counts, checks_differ = take_out_checks(
            base, head, allowances)
    # a row spec the checks step used is done; with no checks step every row spec meets take_out_rows
    base, head, row_violations, row_counts = take_out_rows(
        base, head, [a for a in allowances if a not in check_counts], additive=policy == "refactor")
    changes: list[Change] = []
    for name in sorted(base.keys() | head.keys()):
        if name not in head:
            changes.append(Change(name, (), "removed", base[name], None))
        elif name not in base:
            changes.append(Change(name, (), "added", None, head[name]))
        else:
            changes.extend(diff_file(name, base[name], head[name]))
    changes = [c for c in changes
               if not (c.file == "manifest.json" and c.path[:1] == ("generated_at",))]
    ctx = _Ctx(base, head, frozenset(known_vendors), vendor_renames,
               allowances=allowances, row_counts=row_counts,
               check_lines=check_lines, check_counts=check_counts, checks_differ=checks_differ)
    for side, docs_ in (("base", base), ("head", head)):
        dups = duplicate_ids(docs_.get("transactions.json"))
        if dups:
            shown = ", ".join(dups[:5]) + (f" (+{len(dups) - 5} more)" if len(dups) > 5 else "")
            rep.violations.append(f"transactions.json: {len(dups)} duplicate transaction id(s) in {side} ({shown})")
    rep.violations.extend(row_violations)
    rep.violations.extend(check_violations)
    for c in changes:
        if not _allowed(c, policy, ctx):
            hint = ""
            if _is_schema_version_change(c):
                hint = " (schema_version may only increase)"
            elif policy == "vendors-fallback-only" and c.file == "transactions.json" and c.key == "vendor":
                hint = (" (base vendor is a patterns name)"
                        if ctx.base_rows.get(str(c.path[1]), {}).get("vendor") in ctx.known_vendors
                        else " (not a case-only change, and no renames: entry explains it)")
            elif policy == "removals" and c.kind == "removed":
                hint = " (no --allow-removed spec covers it)"
            rep.violations.append(c.describe() + hint)
    if policy in REMOVAL_POLICIES:
        # the walk sees manifest.files only when it changed: a list that still names a file head
        # lacks has not lost it
        listed = head["manifest.json"].get("files")
        for name in sorted(ctx.gone_files):
            if isinstance(listed, list) and name in listed:
                rep.violations.append(f"manifest.json: files still lists {name}, which head does not have")
    _summarize(rep, changes, ctx, policy)
    return rep


def render(rep: Report, *, show_rows: bool = False) -> str:
    lines = [f"policy: {rep.policy}", *rep.notes]
    if rep.matrix:
        lines.append("category changes (old -> new): rows")
        for (old, new), n in sorted(rep.matrix.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {old} -> {new}: {n}")
    if rep.vendor_matrix:
        lines.append("vendor changes (old -> new): rows")
        for (old, new), n in sorted(rep.vendor_matrix.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {old} -> {new}: {n}")
    if rep.policy == "vendors-fallback-only" or rep.vendor_merges or rep.vendor_splits:
        for title, moves, arrow in (("vendor merges (head <- base vendors)", rep.vendor_merges, "<-"),
                                    ("vendor splits (base -> head vendors)", rep.vendor_splits, "->")):
            lines.append(f"{title}: {len(moves)}")
            lines.extend(f"  {one} {arrow} {', '.join(many)}"
                         for one, many in list(moves.items())[:MAX_MOVE_LINES])
            if len(moves) > MAX_MOVE_LINES:
                lines.append(f"  ... and {len(moves) - MAX_MOVE_LINES} more")
    if show_rows and rep.rows:
        lines.append("rows (--show-rows):")
        lines.extend("  " + r for r in rep.rows)
    if rep.inputs_changed:
        lines.append(f"RESULT: INPUTS CHANGED between base and head "
                     f"({', '.join(rep.inputs_changed)}) - re-run base")
    elif rep.violations:
        lines.append(f"RESULT: {len(rep.violations)} change(s) outside policy {rep.policy}")
        lines.extend("  ! " + v for v in rep.violations[:60])
        if len(rep.violations) > 60:
            lines.append(f"  ... and {len(rep.violations) - 60} more")
    else:
        lines.append(f"RESULT: OK ({rep.policy})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="export_diff", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", type=Path)
    ap.add_argument("head", type=Path)
    ap.add_argument("--expect", required=True, choices=POLICIES)
    ap.add_argument("--vendor-rules", type=Path, default=None,
                    help="vendor_rules.yaml whose patterns[].vendor names are rule hits and whose "
                         "renames: may explain a vendor respelling (required for vendors-fallback-only)")
    ap.add_argument("--show-rows", action="store_true",
                    help="print per-row detail incl. descriptors (on-box eyeballing only)")
    ap.add_argument("--skip-fetched-at", action="store_true",
                    help="do not require equal fetched-at stamps (synthetic dirs only)")
    ap.add_argument("--allow-removed", action="append", metavar="SPEC",
                    help="under removals and refactor, repeatable: a file (FILE), a key and its subtree "
                         "(FILE:KEY.PATH) or list rows (FILE:KEY.PATH[FIELD=GLOB]) head may lack")
    ap.add_argument("--allow-changed", action="append", metavar="SPEC",
                    help="under refactor only, repeatable: a key and anything under it (FILE:KEY.PATH), "
                         "list rows (FILE:KEY.PATH[FIELD=GLOB]) or one transaction row field "
                         "(transactions.json:FIELD) that may differ")
    a = ap.parse_args(argv)
    known: set[str] = set()
    renames: dict[str, str] = {}
    if a.expect == "vendors-fallback-only" and a.vendor_rules is None:
        print("vendors-fallback-only needs --vendor-rules <vendor_rules.yaml>", file=sys.stderr)
        return EXIT_USAGE
    try:
        if a.expect == "vendors-fallback-only":
            known = known_vendors_from_rules(a.vendor_rules)
            renames = vendor_renames_from_rules(a.vendor_rules)
            if not known:
                print(f"export_diff: {a.vendor_rules} has no patterns[].vendor names; "
                      "vendors-fallback-only would treat every row as a fallback",
                      file=sys.stderr)
                return EXIT_USAGE
        rep = compare(a.base, a.head, a.expect, known_vendors=frozenset(known),
                      vendor_renames=renames, check_inputs=not a.skip_fetched_at,
                      allow_removed=a.allow_removed or (), allow_changed=a.allow_changed or ())
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as e:
        # an unreadable dir / rules file / malformed JSON or renames: is a usage problem (2),
        # never an uncaught traceback whose exit status 1 would read as "outside policy"
        # (JSONDecodeError is a ValueError)
        print(f"export_diff: {e}", file=sys.stderr)
        return EXIT_USAGE
    print(render(rep, show_rows=a.show_rows))
    if rep.inputs_changed:
        return EXIT_INPUTS_CHANGED
    return EXIT_OK if rep.ok else EXIT_VIOLATION


if __name__ == "__main__":
    sys.exit(main())

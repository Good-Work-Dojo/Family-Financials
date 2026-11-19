# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Review-inbox answers (scenarios/answers.json, written by the dashboard via
deploy/scenario_store.py) — human confirmations of transaction categories.

Consumed as an overrides source with these semantics:
  - vendor-scope answers apply first, then txn-scope (specificity wins),
    and config/overrides.yaml still runs AFTER both (curated config beats
    casual UI input).
  - `set` is whitelisted to category/subcategory/tags/needs_review — an answer
    can never retype a transaction (that stays a curated overrides.yaml act);
    the server validates this too, this is defense in depth against a
    hand-edited file.
  - a txn answer whose uid vanished (superseded CSV row, SimpleFIN pending →
    posted descriptor change) expires SILENTLY with one aggregate info check —
    unlike YAML overrides there is no action to take; the re-issued row simply
    reappears in the app's Review inbox.
  - verify() runs after overrides.yaml and reports answers whose target rows
    ended up with different values (a config override won) — the app shows
    these as "conflicts" instead of leaving them pending forever.
  - the store keeps MAX_ANSWERS one-off (txn-scope) answers (deploy/scenario_store.py and
    app/src/lib/answers.ts carry the same constant; tests/test_answers.py pins all three); past it
    it evicts the oldest one-off answers (cap_answers), and an evicted answer stops applying.
    Vendor-scope answers are never evicted and do not count toward the cap. apply() says so as an
    `answers.capacity` info once the stored one-off answers reach 90% of the cap.

A malformed file must never fail ingest: load() degrades to an empty document
with a warn check.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import files
from ..models import INTERNAL_TYPES, SPEND_TYPES, QualityCheck, Txn
from ..validate import encodes_as_utf8
from .overrides import apply_set

ANSWER_SET_KEYS = {"category", "subcategory", "tags", "needs_review"}
MAX_ANSWERS = 500            # one-off answers kept: = deploy/scenario_store.py = app/src/lib/answers.ts MAX_ANSWERS
CAPACITY_PCT = 90            # answers.capacity (info) from this share of MAX_ANSWERS one-off answers on


def _answerable(t: Txn, scope: str) -> bool:
    """Answers must never touch internal money movement (that's curated YAML
    territory), and vendor-scope answers must not repaint income rows — a
    vendor whose name also appears on transfers/deposits (Venmo, Apple Cash,
    an employer) would otherwise corrupt them. Vendor-scope answers also skip the
    PARTS of a split purchase: one "always Costco" answer must not turn every
    Shopping leg into Groceries — the split rule decides the legs; a part takes a
    txn-scope answer (its own uid) like any row. The parent itself is answerable but
    display-only (it is in no sum); apply() counts such hits in answers.on_split_parent."""
    if t.is_internal or t.type in INTERNAL_TYPES:
        return False
    if scope == "vendor":
        return t.type in SPEND_TYPES and t.parent_id is None
    return True


def load(path: Path) -> tuple[dict, list[QualityCheck]]:
    """Missing file -> empty doc, no noise. Corrupt/unreadable -> empty + warn: bytes that are not UTF-8 text, not
    JSON, nested past the parser, holding text that is not valid Unicode (a lone surrogate, which the database would
    refuse: encodes_as_utf8) or not an answers document; a link, a FIFO or a folder at its name (never followed, never
    waited on: etl/files.py). Never raises, so a hand-edited file cannot stop an ingest."""
    try:
        raw = files.read_regular(path)
    except FileNotFoundError:
        return {"answers": []}, []
    except OSError as e:
        return {"answers": []}, [QualityCheck(
            name="answers.load", status="warn",
            detail=f"answers file unreadable ({e}) — review-inbox answers skipped this run")]
    try:
        try:
            doc = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            raise ValueError("not UTF-8 text") from None
        except RecursionError:
            raise ValueError("nested deeper than the parser reads") from None
        if not encodes_as_utf8(doc):
            raise ValueError("holds text that is not valid Unicode")
        if not isinstance(doc, dict) or not isinstance(doc.get("answers"), list):
            raise ValueError("not an answers document")
    except (json.JSONDecodeError, ValueError) as e:
        return {"answers": []}, [QualityCheck(
            name="answers.load", status="warn",
            detail=f"answers file corrupt ({e}) — review-inbox answers skipped this run")]
    return doc, []


def valid_entries(doc: dict) -> list[dict]:
    """Keep only well-formed answers with whitelisted set keys. Public: the learner must see
    exactly the answers apply() applies."""
    out = []
    for a in doc.get("answers", []):
        if not isinstance(a, dict) or not isinstance(a.get("set"), dict):
            continue
        if a.get("scope") == "txn" and isinstance(a.get("txn_id"), str):
            pass
        elif a.get("scope") == "vendor" and isinstance(a.get("vendor"), str):
            pass
        else:
            continue
        if set(a["set"]) - ANSWER_SET_KEYS or not a["set"]:
            continue
        out.append(a)
    return out


def _index(txns: list[Txn]) -> tuple[dict[str, list[Txn]], dict[str, list[Txn]]]:
    """(uid -> rows, vendor.lower() -> rows), each list in `txns` order: overrides.matches' uid and
    vendor tests (it compares vendors with .lower()) as dict lookups, built once per pass. Neither
    key is touched by apply_set, so the index stays valid while the answers are applied."""
    by_uid: dict[str, list[Txn]] = {}
    by_vendor: dict[str, list[Txn]] = {}
    for t in txns:
        by_uid.setdefault(t.uid, []).append(t)
        by_vendor.setdefault((t.vendor or "").lower(), []).append(t)
    return by_uid, by_vendor


def _targets(a: dict, by_uid: dict[str, list[Txn]], by_vendor: dict[str, list[Txn]]) -> list[Txn]:
    """The rows answer `a` reaches: what overrides.matches accepts for its uid / vendor and
    _answerable lets through, in `txns` order."""
    rows = by_uid.get(a["txn_id"], ()) if a["scope"] == "txn" else by_vendor.get(str(a["vendor"]).lower(), ())
    return [t for t in rows if _answerable(t, a["scope"])]


def apply(txns: list[Txn], doc: dict) -> list[QualityCheck]:
    entries = valid_entries(doc)
    if not entries:
        return []
    # vendor scope first, txn scope second (a "just this transaction" answer
    # must beat a standing vendor rule); stable order within a scope by
    # answered_at so re-answers land deterministically
    entries.sort(key=lambda a: (0 if a["scope"] == "vendor" else 1,
                                str(a.get("answered_at", "")), str(a.get("key", ""))))
    by_uid, by_vendor = _index(txns)
    applied = 0
    expired = 0
    on_parent = 0
    stranded = 0      # parent hits whose set carried a category / subcategory: no answer reaches a parent's parts
    # (a vendor-scope answer skips parts by design, a txn-scope one targets its own uid), so it cannot take effect
    for a in entries:
        hits = _targets(a, by_uid, by_vendor)
        for t in hits:
            apply_set(t, a["set"], "answer")
            if t.is_parent:
                on_parent += 1
                stranded += "category" in a["set"] or "subcategory" in a["set"]
        applied += len(hits)
        if not hits and a["scope"] == "txn":
            expired += 1     # vendor answers legitimately match nothing until
                             # that vendor next appears — no signal there
    checks = [QualityCheck(
        name="answers.applied", status="info",
        detail=f"{applied} transactions adjusted by {len(entries)} review-inbox answers")]
    one_off = sum(a["scope"] == "txn" for a in entries)      # the only answers the store's cap counts or drops
    if one_off * 100 >= MAX_ANSWERS * CAPACITY_PCT:
        checks.append(QualityCheck(
            name="answers.capacity", status="info",
            detail=f"{one_off} of {MAX_ANSWERS} one-off review-inbox answers stored: past {MAX_ANSWERS} the family "
                   "store drops the oldest one-off answers (they stop applying); “Always for a vendor” answers "
                   "are never dropped and do not count"))
    if expired:
        # a split part is never in the Review inbox (needs_review is cleared on it), so once any part exists
        # the "will reappear" promise holds for ordinary rows only
        reappear = ("ordinary rows will reappear in the Review inbox; a split part follows its leg position "
                    "and does not" if any(t.parent_id is not None for t in txns)
                    else "the rows will reappear in the Review inbox")
        checks.append(QualityCheck(
            name="answers.expired", status="info",
            detail=f"{expired} txn answers reference transactions absent from this run's data "
                   f"(descriptor/uid changed) — {reappear}"))
    if on_parent:
        checks.append(QualityCheck(
            name="answers.on_split_parent", status="warn" if stranded else "info",
            detail=f"{on_parent} answer{'s' if on_parent != 1 else ''} landed on split parents, which carry no "
                   "spend (display only)"
                   + (f"; {stranded} of them set a category the parts do not follow" if stranded else "")
                   + ": the vendor's split rule decides those purchases' parts — change the rule, or answer a part "
                   "(vendor_rules.yaml)."))
    return checks


def verify(txns: list[Txn], doc: dict) -> list[QualityCheck]:
    """After config/overrides.yaml has run: which answers did a config override
    beat? (Category/subcategory comparison only — that's what answers assert.)"""
    conflicts: list[str] = []
    by_uid, by_vendor = _index(txns)
    for a in valid_entries(doc):
        want = a["set"]
        for t in _targets(a, by_uid, by_vendor):
            if t.is_parent:
                continue    # a parent is reported by on_split_parent, never as a conflict
            if "category" in want and t.category != want["category"]:
                conflicts.append(str(a.get("key", "?")))
                break
            if "subcategory" in want and want["subcategory"] is not None \
                    and t.subcategory != want["subcategory"]:
                conflicts.append(str(a.get("key", "?")))
                break
    if not conflicts:
        return []
    return [QualityCheck(
        name="answers.conflict", status="info",
        detail="An override outranked these review answers: "
               + ", ".join(sorted(set(conflicts)))
               + "; remove the override if the answer should win (overrides.yaml).")]

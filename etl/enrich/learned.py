# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Learned vendor rules: the family's own confirmed review-inbox answers, replayed as
vendor -> (category, subcategory) at precedence step 4 of the Categorizer (below an explicit
vendor_rules.yaml `category:`, above every keyword rule). Derived on every ingest from the
answers doc AND the prior learned doc; stored in learned_rules.json in the engine's state
directory (Paths.state_engine_dir: state/engine/ in an install, scenarios/ in the developer layout)
— the ETL's only state file outside data/ (plus the .corrupt copy of a damaged one), and runtime
STATE, never config: load() never raises. It is read and written only as a regular file, never
through a link (etl/files.py), and the .corrupt copy is written from the bytes already read.

Evidence   valid answers whose set names a category (tags-only answers teach nothing) and that
           carry a string key and answered_at; a txn-scope answer counts only when its uid
           resolves to a purchase (not a fee row: the Categorizer applies learned rules to
           purchases only, so a fee row cannot teach one), not internal, with a vendor. Vendors
           group by vendor.casefold().
Vendor     the newest vendor-scope answer is an explicit "always for this vendor" instruction:
scope      it defines the rule by itself (scope "vendor", n_agree 1) and txn-scope answers for
           that vendor are per-row exceptions, never evidence against it. Differing from the
           prior rule -> demoted + promoted. The rule STAYS authoritative if that answer is ever
           gone (the store never evicts a vendor-scope answer, but answers.json can be trimmed by
           hand): a prior rule whose evidence.scope is "vendor" is replaced only by a newer
           vendor-scope answer (or superseded by the guards below), never demoted or re-derived
           from txn answers — they are per-row exceptions with or without the answer.
Txn scope  the 5 most recent txn answers. A pair is DISPUTED when >= 2 of the newest 3 answers
           name something else. A prior rule whose pair is disputed is demoted (it wins over
           promotion: oldest-first [A,A,A,B,B] with prior A demotes A and promotes nothing;
           [A,A,B,B,B] demotes A and promotes B). The most common pair with >= 3 agreeing is
           promoted unless it is disputed — the same test, so a rule is never promoted only to
           be demoted by the next ingest (that would flip-flop forever and keep appending to
           history); [A,A,A,B,B] with no prior promotes nothing either.
Sticky     a prior rule with no evidence this pass survives (the store evicts the oldest txn
           answers past 500; a vendor-scope rule is sticky against txn answers too) with its
           category, evidence and timestamps unchanged — only the display spelling adopts the
           newest row's; the same pair re-confirmed keeps learned_at and refreshes
           last_confirmed_at.
Guards     excluded vendor (learning.exclude / learn: false, casefolded) -> skipped silently
           (a prior rule dropped as `superseded`); explicit vendor_rules.yaml `category:` ->
           never learned, one learned_rules.conflict warn when the evidence names a DIFFERENT
           category, a prior rule `superseded`; a category outside the taxonomy -> dropped
           silently (renames.unknown_references reports the stored answer / prior rule); a
           subcategory outside taxonomy[category] (a stale answer after a taxonomy edit) -> not
           promoted, named in one learned_rules.off_taxonomy warn (unknown_references treats an
           unseeded subcategory as fine, so it never reports this one); a sticky prior rule whose
           own subcategory went stale is named there too, and kept.
Renames    a vendor rename can fold two stored rules into one casefolded vendor: the vendor-scope
           rule survives (else the one with the newer last_confirmed_at, else the first stored), the
           other gets a `superseded` event and is named in one learned_rules.rename_collision warn.

Keying: casefolded everywhere (this module, as_rules, the Categorizer, vendor_registry); the
stored `vendor` is display spelling only — the newest row's spelling when one exists.
No wall clock in rules[] or history[]: learned_at / last_confirmed_at / history.at are
answered_at values, so derive() is a pure function and two ingests of the same inputs produce
the same doc (feed a pass's output back as the prior and nothing changes). `updated_at` is
stamped by write_if_changed, only when the rules changed.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from .. import clock, files
from ..models import QualityCheck, Txn
from ..validate import encodes_as_utf8
from .answers import valid_entries

VERSION = 1
HISTORY_CAP = 500     # newest history events kept when the file is written
CORRUPT_SUFFIX = ".corrupt"   # a corrupt learned_rules.json is copied to <name>.corrupt before it is replaced
WINDOW = 5            # most recent txn-scope answers considered per vendor
PROMOTE_MIN = 3       # agreeing answers needed (3 of 5)
DEMOTE_WINDOW = 3     # newest txn answers checked against a pair
DEMOTE_MIN = 2        # disagreeing answers needed (2 of 3)
EVENTS = ("promoted", "demoted", "superseded")


def empty_doc() -> dict:
    return {"version": VERSION, "updated_at": None, "rules": [], "history": []}


def _check(name: str, status: str, detail: str) -> QualityCheck:
    return QualityCheck(name=f"learned_rules.{name}", status=status, detail=detail)


def _pair(set_: dict) -> tuple[str, str | None]:
    return set_["category"], set_.get("subcategory")


NAME_CAP = 20         # vendor names listed in a check detail before "+N more"


def _names(names: list[str]) -> str:
    """The first NAME_CAP names, then ", +N more" (the way renames.unknown_references caps)."""
    out = ", ".join(names[:NAME_CAP])
    return out + (f", +{len(names) - NAME_CAP} more" if len(names) > NAME_CAP else "")


def _event(kind: str, rule: dict, at: str | None) -> dict:
    return {"at": at, "event": kind, "vendor": rule["vendor"], "category": rule["category"]}


def explicit_vendor_categories(vendor_rules: dict) -> dict[str, dict]:
    """vendor.casefold() -> {category, subcategory} for every patterns entry carrying a
    `category:` (the last entry wins, exactly as Categorizer.vendor_cat resolves duplicates)."""
    out: dict[str, dict] = {}
    for e in vendor_rules.get("patterns", []) or []:
        if isinstance(e, dict) and isinstance(e.get("vendor"), str) and "category" in e:
            out[e["vendor"].casefold()] = {"category": e["category"], "subcategory": e.get("subcategory")}
    return out


# ------------------------------------------------------------------------- load

def _count(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _clean_rule(r) -> dict | None:
    """A normalized rule, or None for a malformed entry: str vendor, str category, str|None
    subcategory; timestamps kept only when strings; evidence filled in with defaults."""
    if (not isinstance(r, dict) or not isinstance(r.get("vendor"), str) or not r["vendor"]
            or not isinstance(r.get("category"), str) or not r["category"]):
        return None
    sub = r.get("subcategory")
    if sub is not None and not isinstance(sub, str):
        return None
    ev = r.get("evidence") if isinstance(r.get("evidence"), dict) else {}
    keys = ev.get("answer_keys") if isinstance(ev.get("answer_keys"), list) else []
    return {
        "vendor": r["vendor"], "category": r["category"], "subcategory": sub,
        "learned_at": r["learned_at"] if isinstance(r.get("learned_at"), str) else None,
        "last_confirmed_at": r["last_confirmed_at"] if isinstance(r.get("last_confirmed_at"), str) else None,
        "evidence": {"scope": ev.get("scope") if ev.get("scope") in ("txn", "vendor") else "txn",
                     "n_agree": _count(ev.get("n_agree")), "n_total": _count(ev.get("n_total")),
                     "answer_keys": sorted(str(k) for k in keys)},
    }


def _clean_event(h) -> dict | None:
    if (not isinstance(h, dict) or h.get("event") not in EVENTS or not isinstance(h.get("vendor"), str)
            or not isinstance(h.get("category"), str)
            or not (h.get("at") is None or isinstance(h.get("at"), str))):
        return None
    return {"at": h.get("at"), "event": h["event"], "vendor": h["vendor"], "category": h["category"]}


def _clean_rules(raw) -> tuple[list[dict], int]:
    """(normalized rules sorted by vendor.casefold(), number of entries skipped). A malformed
    entry or a casefold-duplicate vendor (the first in file order wins) is skipped."""
    rules: list[dict] = []
    seen: set[str] = set()
    skipped = 0
    for r in raw if isinstance(raw, list) else []:
        c = _clean_rule(r)
        if c is None or c["vendor"].casefold() in seen:
            skipped += 1
            continue
        seen.add(c["vendor"].casefold())
        rules.append(c)
    return sorted(rules, key=lambda r: r["vendor"].casefold()), skipped


def _fold_prior(raw) -> tuple[list[dict], list[dict]]:
    """(normalized prior rules, one per casefolded vendor and sorted by it; the rules that lost a
    collision). A vendor rename (VendorRenamer.apply_to_learned) can fold two stored rules into one
    vendor after load() has already deduplicated the file, so derive() meets the pair here: the
    vendor-scope rule is authoritative and wins, else the newer last_confirmed_at, else the first."""
    kept: dict[str, dict] = {}
    lost: list[dict] = []
    for r in raw if isinstance(raw, list) else []:
        c = _clean_rule(r)
        if c is None:
            continue
        k = c["vendor"].casefold()
        cur = kept.get(k)
        if cur is None:
            kept[k] = c
        elif _outranks(c, cur):
            kept[k] = c
            lost.append(cur)
        else:
            lost.append(c)
    return sorted(kept.values(), key=lambda r: r["vendor"].casefold()), lost


def _outranks(a: dict, b: dict) -> bool:
    """Does stored rule `a` beat `b` when a rename folds them together? Strictly: a tie keeps `b`."""
    return ((a["evidence"]["scope"] == "vendor", a["last_confirmed_at"] or "")
            > (b["evidence"]["scope"] == "vendor", b["last_confirmed_at"] or ""))


def _clean_history(raw) -> tuple[list[dict], int]:
    events = [_clean_event(h) for h in raw] if isinstance(raw, list) else []
    return [e for e in events if e is not None], sum(e is None for e in events)


class _Loaded(NamedTuple):
    doc: dict
    checks: list[QualityCheck]
    state: str                 # "missing" | "unreadable" | "corrupt" | "ok"
    error: str | None = None   # what refused the read when state is "unreadable"
    raw: bytes | None = None   # the bytes read, which a damaged file's copy aside is written from


def _load(path: Path) -> _Loaded:
    """load() plus WHY a document came back empty. The distinction matters to write_if_changed:
    a file the OS refuses to read may hold sticky rules and history that no answer can rebuild,
    so it is never written over, and neither is anything at the name that is no regular file
    (a link, a FIFO, a folder: never followed, never read); a corrupt one is copied aside first
    and then healed."""
    try:
        data = files.read_regular(path)
    except FileNotFoundError:
        return _Loaded(empty_doc(), [], "missing")
    except OSError as e:
        why = e.strerror if e.strerror in (files.NOT_REGULAR, files.TOO_LARGE) else e.__class__.__name__
        return _Loaded(empty_doc(), [_check("load", "warn",
                                            f"learned rules unreadable ({why}) — learning starts over this run, and "
                                            "nothing is saved over the file")],
                       "unreadable", why)
    try:
        raw = data.decode("utf-8")
    except ValueError as e:                   # UnicodeDecodeError: a binary file is corrupt content, not an I/O refusal
        return _Loaded(empty_doc(), [_corrupt_check(e)], "corrupt", raw=data)
    try:
        doc = json.loads(raw)
        if not encodes_as_utf8(doc):           # a lone surrogate the database would refuse
            raise ValueError("holds text that is not valid Unicode")
        if not isinstance(doc, dict) or not isinstance(doc.get("rules"), list):
            raise ValueError("not a learned-rules document")
    except (ValueError, RecursionError) as e:  # JSONDecodeError is a ValueError
        return _Loaded(empty_doc(), [_corrupt_check(e)], "corrupt", raw=data)
    rules, skipped = _clean_rules(doc["rules"])
    history, bad_events = _clean_history(doc.get("history"))
    out = {"version": VERSION,
           "updated_at": doc["updated_at"] if isinstance(doc.get("updated_at"), str) else None,
           "rules": rules, "history": history}
    notes = []
    if skipped:
        notes.append(f"{skipped} malformed learned rule(s) skipped (kept {len(rules)})")
    if bad_events:
        notes.append(f"{bad_events} malformed history event(s) dropped")
    if notes:
        notes.append(f"the file is copied to <name>{CORRUPT_SUFFIX} before it is rewritten")
    return _Loaded(out, [_check("load", "warn", "; ".join(notes))] if notes else [], "ok", raw=data)


def _corrupt_check(e: Exception) -> QualityCheck:
    reason = f"{e.__class__.__name__}: {e}"[:200]
    return _check("load", "warn", f"learned rules corrupt ({reason}) — learning starts over this run; the file is "
                                  f"copied to <name>{CORRUPT_SUFFIX} before it is replaced")


def load(path: Path) -> tuple[dict, list[QualityCheck]]:
    """Missing -> empty doc, no noise. Unreadable (the OS refuses) / undecodable / corrupt / not a
    learned document -> empty doc + one warn. Malformed rules, malformed history events and
    casefold-duplicate vendors are skipped with one warn and the rest load (rules sorted by
    vendor.casefold()). NEVER raises — this file is runtime state, and a torn or hand-edited
    copy must not take an ingest down."""
    loaded = _load(path)
    return loaded.doc, loaded.checks


# ----------------------------------------------------------------------- derive

def _evidence(answers_doc) -> list[dict]:
    if not isinstance(answers_doc, dict) or not isinstance(answers_doc.get("answers"), list):
        return []
    return [a for a in valid_entries(answers_doc)
            if isinstance(a["set"].get("category"), str)
            and (a["set"].get("subcategory") is None or isinstance(a["set"].get("subcategory"), str))
            and isinstance(a.get("answered_at"), str) and isinstance(a.get("key"), str)
            and (a["scope"] == "txn" or a["vendor"].strip())]


def _disputing(pair: tuple[str, str | None], recent: list[dict]) -> list[dict]:
    """The answers among the newest DEMOTE_WINDOW (newest first) that name something else."""
    return [a for a in recent[:DEMOTE_WINDOW] if _pair(a["set"]) != pair]


def derive(txns: list[Txn], answers_doc: dict, prior_doc: dict | None, *,
           learn_excluded: set[str] | frozenset[str], explicit_vendor_cats: dict[str, dict],
           taxonomy: dict) -> tuple[dict, list[QualityCheck]]:
    if not isinstance(prior_doc, dict):
        prior_doc = empty_doc()
    prior_rules, folded = _fold_prior(prior_doc.get("rules"))
    prior_history, _ = _clean_history(prior_doc.get("history"))
    prior = {r["vendor"].casefold(): r for r in prior_rules}
    excluded = {str(v).casefold() for v in learn_excluded}
    explicit = {str(k).casefold(): v for k, v in explicit_vendor_cats.items()}
    by_uid = {t.uid: t for t in txns}
    # display spelling per casefolded vendor: the newest row's (by date, uid)
    spelling: dict[str, tuple[tuple[str, str], str]] = {}
    for t in txns:
        if t.vendor:
            k = t.vendor.casefold()
            if k not in spelling or (t.date, t.uid) > spelling[k][0]:
                spelling[k] = ((t.date, t.uid), t.vendor)
    txn_ans: dict[str, list[dict]] = {}
    vendor_ans: dict[str, dict] = {}
    for a in _evidence(answers_doc):
        if a["scope"] == "txn":
            t = by_uid.get(a["txn_id"])
            if t is None or not t.vendor or t.type != "purchase" or t.is_internal:
                continue
            txn_ans.setdefault(t.vendor.casefold(), []).append(a)
        else:
            k = a["vendor"].casefold()
            cur = vendor_ans.get(k)
            if cur is None or (a["answered_at"], a["key"]) > (cur["answered_at"], cur["key"]):
                vendor_ans[k] = a
            spelling.setdefault(k, (("", ""), a["vendor"]))

    rules: list[dict] = []
    events: list[dict] = [_event("superseded", r, r["last_confirmed_at"]) for r in folded]
    conflicts: list[str] = []
    off_sub: list[str] = []
    n_excluded = 0
    for k in sorted(set(prior) | set(txn_ans) | set(vendor_ans)):
        old = prior.get(k)
        name = spelling[k][1] if k in spelling else old["vendor"]
        old_pair = (old["category"], old["subcategory"]) if old else None
        recent = sorted(txn_ans.get(k, []), key=lambda a: (a["answered_at"], a["key"]), reverse=True)[:WINDOW]
        v_ans = vendor_ans.get(k)

        if k in excluded:                                   # guard 1: never learned, never warned
            if k in txn_ans or k in vendor_ans:             # (counted by evidence only, so a pass
                n_excluded += 1                             # and its replay report the same)
            if old is not None:
                events.append(_event("superseded", old, old["last_confirmed_at"]))
            continue

        cand: tuple[tuple[str, str | None], dict, str] | None = None
        demote_at: str | None = None
        if v_ans is not None:                               # an explicit instruction defines the rule
            pair = _pair(v_ans["set"])
            cand = (pair, {"scope": "vendor", "n_agree": 1, "n_total": 1, "answer_keys": [v_ans["key"]]},
                    v_ans["answered_at"])
            if old is not None and old_pair != pair:
                demote_at = v_ans["answered_at"]
        elif old is not None and old["evidence"]["scope"] == "vendor":
            pass            # its vendor answer is gone (trimmed by hand): txn answers stay per-row exceptions
        else:
            if old is not None:
                disputing = _disputing(old_pair, recent)
                if len(disputing) >= DEMOTE_MIN:            # demotion wins over promotion
                    demote_at = max(a["answered_at"] for a in disputing)
            counts = Counter(_pair(a["set"]) for a in recent)
            ranked = sorted(counts.items(), key=lambda pn: (-pn[1], pn[0][0], pn[0][1] or ""))
            best = next(((p, n) for p, n in ranked
                         if n >= PROMOTE_MIN and len(_disputing(p, recent)) < DEMOTE_MIN), None)
            if best is not None:
                agreeing = [a for a in recent if _pair(a["set"]) == best[0]]
                cand = (best[0], {"scope": "txn", "n_agree": best[1], "n_total": len(recent),
                                  "answer_keys": sorted(a["key"] for a in agreeing)},
                        max(a["answered_at"] for a in agreeing))

        exp = explicit.get(k)
        if exp is not None:                                 # guard 2: YAML is never second-guessed
            if cand is not None and cand[0][0] != exp["category"]:
                conflicts.append(name)
            if old is not None:
                events.append(_event("superseded", old, old["last_confirmed_at"]))
            continue
        if cand is not None and cand[0][0] not in taxonomy:  # guard 3: unknown_references has warned
            cand = None
        elif cand is not None and cand[0][1] is not None \
                and cand[0][1] not in (taxonomy.get(cand[0][0]) or ()):   # guard 4: a stale subcategory
            off_sub.append(name)                            # (unknown_references does not report it)
            cand = None
        if demote_at is not None and old is not None:
            events.append(_event("demoted", old, demote_at))
            old, old_pair = None, None
        if cand is None:
            if old is not None and old["category"] in taxonomy:   # sticky; display follows the rows
                if old["subcategory"] is not None and old["subcategory"] not in (taxonomy.get(old["category"]) or ()):
                    off_sub.append(name)                    # guard 4 for a stored rule: kept, but said so
                rules.append({**old, "vendor": name})
            continue
        pair, evidence, confirmed = cand
        if old is not None and old_pair == pair:
            learned_at = old["learned_at"] or confirmed
        else:
            learned_at = confirmed
            events.append({"at": confirmed, "event": "promoted", "vendor": name, "category": pair[0]})
        rules.append({"vendor": name, "category": pair[0], "subcategory": pair[1],
                      "learned_at": learned_at, "last_confirmed_at": confirmed, "evidence": evidence})

    rules.sort(key=lambda r: r["vendor"].casefold())
    names = [r["vendor"] for r in rules]
    detail = (f"{len(rules)} learned vendor rule(s) active — confirmed review answers "
              f"(3-of-5 agreement or a vendor-scope answer) replayed at precedence 4")
    if names:
        detail += ": " + _names(names)
    if n_excluded:
        detail += f"; {n_excluded} vendor(s) skipped: excluded from learning"
    checks = [_check("active", "info", detail)]
    if conflicts:
        checks.append(_check("conflict", "warn",
                             f"{len(conflicts)} vendor(s) have review answers contradicting an explicit "
                             f"vendor_rules.yaml category (nothing learned; edit the YAML or the answers): "
                             + ", ".join(sorted(set(conflicts)))))
    if off_sub:
        checks.append(_check("off_taxonomy", "warn",
                             f"{len(set(off_sub))} vendor(s) have a learned rule or confirmed review answers naming "
                             f"a subcategory outside the taxonomy (nothing new is learned from it; fix the taxonomy "
                             f"or answer again): " + _names(sorted(set(off_sub)))))
    if folded:
        checks.append(_check("rename_collision", "warn",
                             f"{len(folded)} learned rule(s) were folded into another by a vendor rename (the "
                             f"vendor-scope rule, else the newer one, was kept): "
                             + _names(sorted({r["vendor"] for r in folded}, key=str.casefold))))
    updated = prior_doc.get("updated_at")
    doc = {"version": VERSION, "updated_at": updated if isinstance(updated, str) else None,
           "rules": rules, "history": prior_history + events}
    return doc, checks


def as_rules(doc: dict | None) -> dict[str, dict]:
    """vendor.casefold() -> {vendor, category, subcategory}: the Categorizer's step-4 table.
    A malformed entry is skipped; the first of two casefold-equal vendors wins."""
    out: dict[str, dict] = {}
    rules, _ = _clean_rules(doc.get("rules") if isinstance(doc, dict) else None)
    for r in rules:
        out.setdefault(r["vendor"].casefold(),
                       {"vendor": r["vendor"], "category": r["category"], "subcategory": r["subcategory"]})
    return out


# ------------------------------------------------------------------------ write

def _utc_now() -> str:
    return clock.utc_stamp()


def write_if_changed(path: Path, doc: dict) -> tuple[bool, list[QualityCheck]]:
    """Write `doc` atomically and durably (etl/files.py write_atomic: a new file made beside it,
    fsynced, renamed over it, the directory fsynced - this is state that outlives its answers,
    unlike the derived exports) when its `rules` differ from the file's (updated_at and history
    are not compared) or when the file is corrupt or partly malformed; skip when rules are empty
    and no file exists. The newest HISTORY_CAP history events are kept. A file the OS refuses to
    read, or a name holding no regular file, is NEVER written over - it may hold sticky rules and
    history that no answer can rebuild - and a corrupt or partly malformed one (entries skipped
    on load) is copied to <name>.corrupt first, from the bytes already read, so nothing it held is
    lost to the rewrite. OSError (a read-only mount, an unreadable file) -> one
    learned_rules.write_failed warn, never an exception: an ingest must not abort over its side
    file. Returns (written, checks)."""
    loaded = _load(path)
    on_disk, load_checks = loaded.doc, loaded.checks
    if loaded.state == "unreadable":
        return False, [_check("write_failed", "warn",
                              f"{path.name} is unreadable ({loaded.error}) — the learned rules were applied this run "
                              "but not saved, and the file was left as it is; is the scenarios mount readable and "
                              "read-write?")]
    if not load_checks and on_disk["rules"] == doc["rules"]:
        return False, []
    if load_checks:      # corrupt, or loaded with entries skipped: either way the rewrite drops something
        aside = path.with_name(path.name + CORRUPT_SUFFIX)
        try:
            files.write_atomic(aside, loaded.raw)
        except OSError as e:
            return False, [_check("write_failed", "warn",
                                  f"could not keep a copy of the damaged {path.name} as {aside.name} "
                                  f"({e.__class__.__name__}) — the learned rules were applied this run but not "
                                  "saved, and the file was left as it is")]
    try:
        if not doc["rules"] and not path.exists():     # exists() can raise PermissionError too
            return False, []
        body = {**doc, "updated_at": _utc_now(), "history": doc["history"][-HISTORY_CAP:]}
        os.makedirs(path.parent, exist_ok=True)
        files.write_atomic(path, json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    except (OSError, UnicodeError) as e:
        return False, [_check("write_failed", "warn",
                              f"could not write {path.name} ({e.__class__.__name__}) — the learned rules "
                              "were applied this run but not saved; is the scenarios mount read-write?")]
    return True, []

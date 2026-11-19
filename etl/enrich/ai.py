# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The AI step's offline half, run by every ingest: the stored answers applied to the rows nothing else filed, and the
step's status (ai_status.json). Offline: it imports nothing that opens a socket (only the AI store's reader, the
candidate predicate and the request's answer set: tests/test_offline_imports.py), reads no clock and writes nothing.

`apply` runs in Pipeline.enrich after the internal resync and before the splits: after every pairing phase, so the
generic matcher (and the Categorizer.places question it asks) never reads a category this step set, and before the
review answers and the overrides, which re-stamp a row they reach and so always outrank it. It acts only while the
household has switched the AI step on in the app for the current policy version and scope (the family's prefs, `ai`:
etl/ai/cache.py consent, read link-proof) and answers are stored (store/ai/vendor_categories.json, read link-proof;
a file that is empty, a link, not an answers file or holds no answer counts as none stored); otherwise it touches no
row. Then, for each stored answer, after its vendor key and its
category pass the household's renames (a vendor key renamed onto another's keeps the entry stored under the new key
itself, else the first in key order):
  * a null category (the model did not know) or a confidence below household.yaml `ai.confidence` is not applied
    (`low_confidence`);
  * a category / subcategory pair that is not one of the household's spending categories now (the request's answer
    set, or such a category with no subcategory, as a learned rule may name one — never the fallback category
    alone) is not applied (`stale_category`);
  * an answer whose vendor has no candidate row (etl/ai/candidates.py, the predicate the run asked by, evaluated
    here) is not applied (`not_candidate`): a rule, a learned rule or a bank label files those rows now;
  * otherwise every candidate row of the vendor takes the category through `set_category`: `category_source` "ai",
    and the review flag cleared only when the categorizer's fallback raised it (review_reason "uncategorized"). A
    row any other source filed is no candidate, so it is never touched. Nothing is written: no answer, no learned
    rule, no vendor rule (learned.derive reads the family's answers alone, and runs before this step).
Each stored answer is counted under the first reason that applies; the applied ones are counted from the rows at the
end of the enrich stage, so a row an answer or an override re-filed afterwards is not. While answers are stored and
apply, the quality report carries one `info` line, `ai.applied` (`check`, counts only).

`status` is ai_status.json, written by every ingest whether or not the step is on, counts only — never a vendor and
never the key:
  configured      a key file is stored (the AI secrets folder's api_key: a regular file, not a link, readable by its
                  owner alone; its content is never read)
  enabled         the stored answers apply (switched on for the current policy version and scope)
  provider, model household.yaml `ai`
  endpoint_host   the host[:port] of `ai.base_url` for an OpenAI-compatible service; null for a provider's own API
  policy_version  what is sent now (etl/ai/policy.py POLICY_VERSION); consent_policy the version the household last
                  agreed to (null when none)
  scope           who receives the vendor names (the provider; at a base_url also its host, path and the model)
                  and which accounts' rows are asked about (policy.consent_scope): consent given to another scope
                  does not hold
  caps            {vendors_per_run, runs_per_day}
  last_run        the run log's newest run {at, outcome, sent, answered, cached}, or null
  answers_stored  an answers file is stored (a regular, non-empty file; never read here), switched on or not
  applied         {vendors, rows} the stored answers filed
  skipped         {low_confidence, stale_category, not_candidate}: the stored answers not applied, by reason
  preview         {vendors, withheld}: how many vendors `python -m etl ai preview` would show as sent, and how many
                  it keeps back for each reason — over the rows as they stand without this step's categories
"""
from __future__ import annotations

import dataclasses
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from ..ai import cache
from ..ai import policy as policy_mod
from ..ai.candidates import Predicate, candidates, vendor_key, withhold
from ..ai.cli import prefs_path
from ..ai.request import answer_set
from ..config import Paths
from ..models import FALLBACK_CATEGORY, UNCATEGORIZED_SUB, QualityCheck, Txn
from .categorize import UNCATEGORIZED_REASON

SOURCE = "ai"
# the key file's name in the AI secrets folder (etl/ai/providers.py KEY_NAME, which this module may not import:
# tests/test_ai_apply.py pins the two equal)
KEY_NAME = "api_key"
# why a stored answer is not applied, in the order they are tested
SKIP_REASONS = ("low_confidence", "stale_category", "not_candidate")


@dataclass
class Applied:
    """What `apply` did: whether the stored answers apply at all, a problem reading them, and the answers it left
    unapplied, by reason."""
    enabled: bool = False
    problem: str | None = None
    skipped: dict[str, int] = field(default_factory=lambda: dict.fromkeys(SKIP_REASONS, 0))
    # answers are stored that the step could read: with none the step had nothing to do, and says nothing
    stored: bool = False


def set_category(t: Txn, category: str, subcategory: str | None) -> None:
    """File `t` under a stored answer: its category and subcategory, `category_source` "ai", and its review flag
    cleared only when the categorizer's fallback raised it. Not overrides.apply_set: an AI answer is no family fact."""
    t.category, t.subcategory, t.category_source = category, subcategory, SOURCE
    if t.review_reason == UNCATEGORIZED_REASON:
        t.needs_review = False
        t.review_reason = None


def _entries(cfg, stored: cache.Cache) -> dict[str, dict]:
    """The stored answers by vendor key after the household's vendor renames (the module docstring says which wins
    when two keys become one)."""
    out: dict[str, dict] = {}
    for key in sorted(stored.entries):
        new = vendor_key(cfg, key)
        if new and (new not in out or key == new):
            out[new] = stored.entries[key]
    return out


def _spending_pairs(cfg) -> frozenset[tuple[str, str | None]]:
    """The (category, subcategory) pairs a stored answer may file a row under: the answer set's, and each of its
    categories with no subcategory but the fallback one, which would file a row under the catch-all as if answered
    and clear its review flag."""
    pairs: set[tuple[str, str | None]] = set()
    for category, subs in answer_set(cfg):
        if category != FALLBACK_CATEGORY:
            pairs.add((category, None))
        pairs.update((category, s) for s in subs)
    return frozenset(pairs)


def apply(cfg, txns: list[Txn]) -> Applied:
    """Apply the stored answers to the candidate rows of `txns` (the module docstring says when and which)."""
    if cache.consent(prefs_path(cfg), policy_mod.POLICY_VERSION, policy_mod.consent_scope(cfg.ai)) != "on":
        return Applied()
    stored, problem = cache.read(Paths(cfg.dir).ai_store_dir() / cache.CACHE_NAME)
    if stored is None or not stored.entries:
        return Applied(enabled=True)
    done = Applied(enabled=True, problem=problem, stored=True)
    test = Predicate(cfg)
    rows: dict[str, list[Txn]] = {}
    for t in txns:
        if test(t):
            rows.setdefault(vendor_key(cfg, t.vendor), []).append(t)
    pairs = _spending_pairs(cfg)
    threshold = cfg.ai.confidence
    for key, entry in _entries(cfg, stored).items():
        if entry["category"] is None or entry["confidence"] < threshold:
            done.skipped["low_confidence"] += 1
            continue
        category, sub = cfg.category_renamer.map(entry["category"], entry["subcategory"])
        if (category, sub) not in pairs:
            done.skipped["stale_category"] += 1
        elif key not in rows:
            done.skipped["not_candidate"] += 1
        else:
            for t in rows[key]:
                set_category(t, category, sub)
    return done


def key_stored(path: Path) -> bool:
    """Whether a key file is stored at `path`: a regular file (a link is never followed), not empty, that only its
    owner may read. Its content is never read."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_size > 0 and not info.st_mode & 0o077


def answers_stored(path: Path) -> bool:
    """Whether an answers file is at `path` (a regular file, not a link, not empty), whatever the step's switch: the
    dashboard then offers to forget it. Its content is never read here."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_size > 0


def _before(t: Txn) -> Txn:
    """`t` as it stood before this step filed it: a copy under the fallback pair, for the preview's count."""
    if t.category_source != SOURCE:
        return t
    return dataclasses.replace(t, category=FALLBACK_CATEGORY, subcategory=UNCATEGORIZED_SUB,
                               category_source="fallback")


def status(cfg, txns: list[Txn], applied: Applied) -> dict:
    """ai_status.json (the module docstring lists its keys), from the enriched rows and what `apply` did."""
    paths = Paths(cfg.dir)
    ai = cfg.ai
    filed = [t for t in txns if t.category_source == SOURCE and not t.is_parent]
    sendable, withheld = withhold(candidates(cfg, [_before(t) for t in txns]), cfg)
    return {
        "configured": key_stored(paths.secrets_ai_dir() / KEY_NAME),
        "enabled": applied.enabled,
        "provider": ai.provider,
        "model": ai.model,
        "endpoint_host": policy_mod.endpoint_host(ai.provider, ai.base_url),
        "policy_version": policy_mod.POLICY_VERSION,
        "scope": policy_mod.consent_scope(ai),
        "consent_policy": cache.consented_policy(prefs_path(cfg)),
        "caps": {"vendors_per_run": ai.vendors_per_run, "runs_per_day": ai.runs_per_day},
        "last_run": cache.last_run(paths.ai_store_dir()),
        "answers_stored": answers_stored(paths.ai_store_dir() / cache.CACHE_NAME),
        "applied": {"vendors": len({vendor_key(cfg, t.vendor) for t in filed}), "rows": len(filed)},
        "skipped": {r: applied.skipped[r] for r in SKIP_REASONS},
        "preview": {"vendors": len(sendable), "withheld": dict(withheld)},
    }


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def check(doc: dict, applied: Applied) -> QualityCheck | None:
    """The quality report's `ai.applied` line while the step is on and answers are stored (counts only), else None:
    with the step off or nothing stored the ingest's exports are the same as with no AI step at all, but for
    ai_status.json."""
    if not doc["enabled"] or not applied.stored:
        return None
    filed, skipped = doc["applied"], doc["skipped"]
    detail = (f"The AI step's stored answers filed {_n(filed['rows'], 'purchase', 'purchases')} of "
              f"{_n(filed['vendors'], 'vendor', 'vendors')}. Not applied: "
              f"{_n(skipped['low_confidence'], 'answer', 'answers')} below the confidence it needs, "
              f"{skipped['stale_category']} naming a category your categories no longer hold, and "
              f"{skipped['not_candidate']} for vendors with no uncategorized purchase now.")
    if applied.problem is not None:
        detail += " Some stored answers could not be read; the next AI run stores them afresh."
    return QualityCheck(name="ai.applied", status="info", detail=detail)

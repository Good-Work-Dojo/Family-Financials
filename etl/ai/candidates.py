# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Which rows the AI step asks about, and which of their vendors are never sent. Offline and pure: reads enriched rows
and the Config, writes nothing.

`is_candidate` is the one predicate the preview, the run and the ingest's apply step share. A row is a candidate when
it is:
  * a purchase (`type`), filed by the fallback (`category_source` "fallback") under the reserved uncategorized pair
    (FALLBACK_CATEGORY / UNCATEGORIZED_SUB): no vendor rule, learned rule, keyword rule, bank label, answer or
    override placed it, and a payment the P2P catch-all files (P2P_UNKNOWN_SUB) is not under that pair;
  * neither a split parent nor a split part, and not internal;
  * not flagged for review for any reason but "uncategorized" (`review_reason`), so a `kinds:` transfer leg left
    unpaired ("unpaired-transfer") is never one;
  * a row the transfer stage left alone: no counter hint, transfer group or match status;
  * no payment-app or person-to-person row: its raw descriptor and its vendor, read folded, hold no payment app's
    mark anywhere (policy.is_payment_app: a shop paid through PayPal or a wallet is withheld too, failing safe) and
    match none of the household's transfer_rules.yaml `venmo_patterns` (debit, credit, zelle; or its bank type is a
    zelle_bank_types one). Neither the row nor the vendor registry carries a payment-app mark of its own, so these
    patterns are the test, applied to every account;
  * of a vendor the learner may learn (not in vendor_registry.learning_excluded) and that is not "Unknown";
  * on a credit-card account, unless household.yaml `ai.accounts` is `all`.

`vendor_key` is the key a vendor is asked about and cached under: the vendor after the household's vendor renames,
NFKC-normalised, case-folded, with control characters dropped and whitespace squashed.

`withhold` then drops, per vendor key, what policy.py never sends (a household name token, a run of three or more
digits, an address, an over-long key) and every key with fewer than `ai.min_seen` candidate rows, counting distinct
rows over the whole history the run read.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from ..enrich import vendor_registry
from ..enrich.categorize import UNCATEGORIZED_REASON
from ..models import FALLBACK_CATEGORY, UNCATEGORIZED_SUB, Txn
from . import policy

_UNKNOWN_VENDOR = "unknown"
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Candidate:
    """One candidate row: its uid (so the apply step can find it) and its vendor key. Nothing else of the row."""
    uid: str
    vendor: str


def vendor_key(cfg, vendor: str) -> str:
    """The key `vendor` is asked about and cached under (the module docstring says how it is made)."""
    renamed = cfg.vendor_renamer.map(vendor) if getattr(cfg, "vendor_renamer", None) is not None else vendor
    s = unicodedata.normalize("NFKC", str(renamed)).casefold()
    s = "".join(" " if c.isspace() else c for c in s if c.isspace() or unicodedata.category(c)[0] != "C")
    return unicodedata.normalize("NFKC", _SPACES.sub(" ", s).strip())


def _patterns(cfg) -> tuple[list[re.Pattern], frozenset[str]]:
    """The household's payment-app patterns (transfer_rules.yaml venmo_patterns, matched with case ignored, as the
    transfer engine matches them) and its Zelle bank types."""
    vp = (getattr(cfg, "transfer_rules", None) or {}).get("venmo_patterns") or {}
    found = []
    for key in ("debit", "credit", "zelle"):
        if isinstance(vp.get(key), str) and vp[key]:
            try:
                found.append(re.compile(vp[key], re.IGNORECASE))
            except re.error:
                continue
    types = vp.get("zelle_bank_types")
    return found, frozenset(str(t) for t in types) if isinstance(types, list) else frozenset()


class Predicate:
    """`is_candidate` bound to one Config: its patterns, exclusions and account kinds read once."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.patterns, self.zelle_types = _patterns(cfg)
        self.excluded = vendor_registry.learning_excluded(cfg.vendor_rules)
        ai = getattr(cfg, "ai", None)
        self.all_accounts = getattr(ai, "accounts", "cards") == "all"
        self.kinds = {a: m.get("kind") for a, m in cfg.accounts.items() if isinstance(m, dict)}

    def _payment_app(self, t: Txn) -> bool:
        texts = (t.description_raw or "", t.vendor or "")
        if any(policy.is_payment_app(s) for s in texts):
            return True
        if any(p.search(s) for p in self.patterns for s in texts):
            return True
        return t.bank_type is not None and t.bank_type in self.zelle_types

    def __call__(self, t: Txn) -> bool:
        if t.type != "purchase" or t.is_internal:
            return False
        if t.category_source != "fallback" or (t.category, t.subcategory) != (FALLBACK_CATEGORY, UNCATEGORIZED_SUB):
            return False
        if t.is_parent or t.parent_id is not None or t.split_index:
            return False
        if t.review_reason not in (None, UNCATEGORIZED_REASON):
            return False
        if t.counter_hint is not None or t.transfer_group_id is not None or t.match_status is not None:
            return False
        if not t.vendor or not t.vendor.strip():
            return False
        if t.account_id not in self.kinds or (not self.all_accounts and self.kinds[t.account_id] != "credit-card"):
            return False
        key = vendor_key(self.cfg, t.vendor)
        if not key or key == _UNKNOWN_VENDOR or key in self.excluded or t.vendor.casefold() in self.excluded:
            return False
        return not self._payment_app(t)


def is_candidate(cfg, t: Txn) -> bool:
    """Whether `t` is a candidate row under `cfg` (the module docstring lists the tests)."""
    return Predicate(cfg)(t)


def candidates(cfg, txns: list[Txn]) -> list[Candidate]:
    """The candidate rows of `txns` (an enriched pipeline's rows), in their order."""
    test = Predicate(cfg)
    return [Candidate(t.uid, vendor_key(cfg, t.vendor)) for t in txns if test(t)]


def withhold(found: list[Candidate], cfg) -> tuple[tuple[str, ...], dict[str, int]]:
    """The vendor keys that may be sent (sorted, each once) and how many keys each reason withholds (every reason of
    policy.REASONS, in that order, zero included; a key counts under the first reason that applies). The seen count
    is the number of distinct candidate rows (by uid) of the key."""
    tokens = policy.household_tokens(cfg)
    ai = getattr(cfg, "ai", None)
    min_seen = getattr(ai, "min_seen", 2)
    rows: dict[str, set[str]] = {}
    for c in found:
        rows.setdefault(c.vendor, set()).add(c.uid)
    withheld: Counter[str] = Counter({r: 0 for r in policy.REASONS})
    sendable = []
    for key in sorted(rows):
        reason = policy.text_reason(key, tokens)
        if reason is None and len(rows[key]) < min_seen:
            reason = "seen"
        if reason is None:
            sendable.append(key)
        else:
            withheld[reason] += 1
    return tuple(sendable), {r: withheld[r] for r in policy.REASONS}

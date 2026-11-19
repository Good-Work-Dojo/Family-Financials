# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The exact request body the AI step sends, provider-neutral and built without any network: the policy version, the
instruction text, the answer set and the vendor keys — nothing else (no household name, account kind, count, amount
or date). A provider module wraps this body in its own call; `python -m etl ai preview` prints it.

The answer set is the household's spending categories by name, each with its subcategories: the taxonomy without the
type-driven categories (internal, income, fees), the fallback category without its reserved subcategories (and left
out when nothing else is under it), and any category or subcategory whose name policy.py would not send (a household
name token, a number or a date, an address, an over-long name); a category left out takes its subcategories with
it, and a category whose subcategories were all left out is sent with none (the model then names the category alone).

An empty answer set leaves the model nothing to choose from: `build_request` refuses it with NothingToAsk, and no
request is made.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from ..models import (
    FALLBACK_CATEGORY,
    FEES_CATEGORY,
    INCOME_CATEGORY,
    INTERNAL_CATEGORY,
    P2P_UNKNOWN_SUB,
    UNCATEGORIZED_SUB,
)
from . import policy as policy_mod

# categories the engine fills by a row's type, which no purchase is filed under
_TYPE_DRIVEN = frozenset({INTERNAL_CATEGORY, INCOME_CATEGORY, FEES_CATEGORY})
_RESERVED_FALLBACK_SUBS = frozenset({UNCATEGORIZED_SUB, P2P_UNKNOWN_SUB})

AnswerSet = tuple[tuple[str, tuple[str, ...]], ...]

# what a person is told when the answer set is empty
NOTHING_TO_CHOOSE = ("Your categories hold only the built-in ones, so there is nothing to choose from: add "
                     "categories first.")


class NothingToAsk(Exception):
    """The answer set is empty: the household's categories hold only the built-in ones, so no request is built."""

    def __init__(self):
        super().__init__(NOTHING_TO_CHOOSE)


def answer_set(cfg) -> AnswerSet:
    """The household's answer set (the module docstring says what is in it), sorted by category, each category's
    subcategories sorted."""
    tokens = policy_mod.household_tokens(cfg)
    out = []
    for category, subs in (cfg.taxonomy or {}).items():
        if not isinstance(category, str) or category in _TYPE_DRIVEN:
            continue
        if policy_mod.text_reason(category, tokens) is not None:
            continue
        kept = sorted({s for s in subs or () if isinstance(s, str) and policy_mod.text_reason(s, tokens) is None
                       and not (category == FALLBACK_CATEGORY and s in _RESERVED_FALLBACK_SUBS)})
        if category == FALLBACK_CATEGORY and not kept:
            continue
        out.append((category, tuple(kept)))
    return tuple(sorted(out))


def build_request(sendable: Sequence[str], answers: AnswerSet, policy: policy_mod.Policy, *,
                  tokens: Iterable[str] = ()) -> dict:
    """The request body for the vendor keys `sendable` (as candidates.withhold gives them) and the answer set
    `answers` (as answer_set gives it). Every vendor and every name of the answer set is checked once more against
    policy.text_reason with `tokens` (the household's name tokens; none given, the name test finds nothing): one that
    may not be sent raises ValueError, naming its reason and never the text. An empty answer set raises NothingToAsk."""
    if not answers:
        raise NothingToAsk()
    tokens = frozenset(tokens)
    vendors = list(sendable)
    if len(set(vendors)) != len(vendors) or not all(isinstance(v, str) and v for v in vendors):
        raise ValueError("the vendors must be distinct, non-empty text")
    for v in vendors:
        reason = policy_mod.text_reason(v, tokens)
        if reason is not None:
            raise ValueError(f"a vendor that may not be sent ({reason}) reached the request")
    categories = []
    for category, subs in answers:
        for name in (category, *subs):
            reason = policy_mod.text_reason(name, tokens) if isinstance(name, str) and name else "not text"
            if reason is not None:
                raise ValueError(f"a category name that may not be sent ({reason}) reached the request")
        categories.append({"category": category, "subcategories": list(subs)})
    return {"policy_version": policy.version, "instructions": policy.instructions,
            "answer_set": categories, "vendors": vendors}


def serialise(body: dict) -> str:
    """The body as the preview prints it: JSON, indented, keys sorted, characters as they are."""
    return json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False)

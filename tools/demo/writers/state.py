# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The family store's state files the engine reads, each in the envelope the store writes: the review answers
(answers.json) and the assets entered in the app (manual_assets.json)."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

SET_KEYS = frozenset({"category", "subcategory", "tags", "needs_review"})


def _stamp(when: datetime) -> str:
    if when.tzinfo is None:
        raise ValueError(f"{when} is not timezone-aware")
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _asset_stamp(when: datetime) -> str:
    """A manual asset's stamp as the dashboard writes it (Date.toISOString): UTC, always with milliseconds."""
    if when.tzinfo is None:
        raise ValueError(f"{when} is not timezone-aware")
    utc = when.astimezone(UTC)
    return f"{utc:%Y-%m-%dT%H:%M:%S}.{utc.microsecond // 1000:03d}Z"


def _checked(set_: dict) -> dict:
    """The set, refused with ValueError when the family store would refuse it: keys outside SET_KEYS or none at all,
    a category that is not a non-empty string, a subcategory neither a string nor None, tags that are not a list of
    strings, a review flag that is not a bool."""
    if not set_ or set(set_) - SET_KEYS:
        raise ValueError(f"an answer's set must be a non-empty subset of {sorted(SET_KEYS)}, got {sorted(set_)}")
    if "category" in set_ and not (isinstance(set_["category"], str) and set_["category"]):
        raise ValueError(f"an answer's category must be a non-empty string, got {set_['category']!r}")
    if "subcategory" in set_ and not (set_["subcategory"] is None or isinstance(set_["subcategory"], str)):
        raise ValueError(f"an answer's subcategory must be a string or None, got {set_['subcategory']!r}")
    if "tags" in set_ and not (isinstance(set_["tags"], list) and all(isinstance(t, str) for t in set_["tags"])):
        raise ValueError(f"an answer's tags must be a list of strings, got {set_['tags']!r}")
    if "needs_review" in set_ and not isinstance(set_["needs_review"], bool):
        raise ValueError(f"an answer's needs_review must be true or false, got {set_['needs_review']!r}")
    return dict(set_)


def _target(what: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"an answer needs a {what}, got {value!r}")
    return value


def row_answer(uid: str, set_: dict, answered_at: datetime) -> dict:
    """The answer for one row (`scope: txn`), addressed by the row's uid."""
    uid = _target("uid", uid)
    return {"key": f"txn:{uid}", "scope": "txn", "txn_id": uid, "set": _checked(set_),
            "answered_at": _stamp(answered_at)}


def vendor_answer(vendor: str, set_: dict, answered_at: datetime) -> dict:
    """The answer for every spending row of one vendor (`scope: vendor`)."""
    vendor = _target("vendor", vendor)
    return {"key": f"vendor:{vendor}", "scope": "vendor", "vendor": vendor, "set": _checked(set_),
            "answered_at": _stamp(answered_at)}


def write_answers(path: Path, answers: list[dict], updated_at: datetime) -> None:
    """Write the document; ValueError, before anything is written, when two answers share a key (the store keeps
    one answer per key)."""
    seen: set[str] = set()
    for answer in answers:
        if answer["key"] in seen:
            raise ValueError(f"two answers share the key {answer['key']!r}")
        seen.add(answer["key"])
    doc = {"version": 1, "updated_at": _stamp(updated_at), "answers": answers}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="")


def manual_asset(asset_id: str, name: str, owner: str, asset_class: str, liquid: bool,
                 values: tuple[tuple[date, int], ...], updated_at: datetime) -> dict:
    """One live asset entered in the app: its values `(day, cents)` as `{as_of, value}` in dollars, oldest first, and
    no `acquired` (it states none) or `deleted_at` (it is not removed)."""
    return {"id": asset_id, "name": name, "owner": owner, "asset_class": asset_class, "liquid": liquid,
            "values": [{"as_of": day.isoformat(), "value": float(Decimal(cents) / 100)} for day, cents in values],
            "updated_at": _asset_stamp(updated_at)}


def write_manual_assets(path: Path, assets: list[dict], updated_at: datetime) -> None:
    """Write the document, stamped `updated_at` (the store's merge keeps the newer of two documents' stamps);
    ValueError, before anything is written, when two assets share an id (the store keeps one per id)."""
    ids = [a["id"] for a in assets]
    if len(set(ids)) != len(ids):
        raise ValueError(f"two assets share an id: {sorted(ids)}")
    doc = {"version": 1, "updated_at": _asset_stamp(updated_at), "assets": assets}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="")

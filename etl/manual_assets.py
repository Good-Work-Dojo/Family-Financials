# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Manual assets: the values no feed or statement reports, such as a home or a car, as the household states them —
an `accounts.yaml` `manual_entries` item, or an asset entered in the app, which the store keeps in `<state
dir>/manual_assets.json` (Config.manual_assets_path, beside answers.json). The ETL only reads that file.

The document is `{version: 1, updated_at, assets: [{id, name, owner, asset_class, liquid, acquired?, values: [{as_of,
value}], updated_at, deleted_at?}]}`, a value in dollars (negative for a debt). The store checks its shape only; the
meaning is checked here against the household, so an asset the household could not have meant is left out with a
`manual_assets.entry` warning naming it and why, never a figure.

The effective list is what net worth counts (etl/networth.py builds one series per asset): every live asset of the
document (no `deleted_at`, or a null one) that passes those checks, plus every config entry whose id no such asset
takes. A config entry's id is its name as networth.manual_slug gives it, so an app asset of that id overrides the
entry, and removing that asset (or its failing a check) brings the entry back; the app never hides a config entry.
The list is sorted by name (ignoring case), then id. The entries an app asset hides are exported beside it
(`overridden`, accounts.json `manual_overridden`), so the dashboard can say what removing the app asset brings back.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import files
from .models import LIABILITY_CLASSES, QualityCheck
from .networth import MAX_MANUAL_ID_LEN, MAX_MANUAL_VALUE, manual_slug
from .validate import encodes_as_utf8, is_number, is_text, iso_date

DOCUMENT_VERSION = 1
MAX_BYTES = 1 << 20                   # the largest document read: 1 MiB
# An app asset's id: runs of letters or digits, of any script, joined by single dashes, its own lowercase, at most
# MAX_ID_LEN characters (is_id). Every config entry's id is one (networth.manual_slug of its name; validate refuses a
# name whose slug is empty or longer), so an app asset can carry it to override the entry. deploy/scenario_store.py
# is_manual_id is the same rule.
ID = re.compile(r"[^\W_]+(?:-[^\W_]+)*")
MAX_ID_LEN = MAX_MANUAL_ID_LEN
# A C0 control character or DEL, which no app asset's name holds (has_control; deploy/scenario_store.py has_control is
# the same rule): a line break or an escape sequence would reach every page and the database as it is.
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
APP, CONFIG = "app", "config"         # ManualAsset.origin


@dataclass(frozen=True)
class ManualAsset:
    id: str
    name: str
    institution: str | None           # who holds or values it; None for an asset entered in the app
    owner: str
    asset_class: str
    liquid: bool
    acquired: dt.date | None          # before this day it is not held; None: held as far back as the history goes
    values: tuple[tuple[dt.date, int], ...]   # (day, cents), ascending, one per day, at least one
    origin: str                       # APP or CONFIG


def empty() -> dict:
    return {"assets": []}


def is_tombstone(raw: Mapping) -> bool:
    """An asset removed in the app: its `deleted_at` is a non-empty string (the time it was removed). Null or absent
    is a live asset; any other value hides nothing (the asset is left out with a warning instead)."""
    deleted = raw.get("deleted_at")
    return isinstance(deleted, str) and deleted != ""


def is_id(value) -> bool:
    """An app asset's id (ID, at most MAX_ID_LEN characters, its own lowercase)."""
    return (isinstance(value, str) and len(value) <= MAX_ID_LEN and value == value.lower()
            and ID.fullmatch(value) is not None)


def has_control(value: str) -> bool:
    """`value` holds a C0 control character or DEL (CONTROL)."""
    return CONTROL.search(value) is not None


def _load_warning(what: str) -> QualityCheck:
    return QualityCheck(name="manual_assets.load", status="warn",
                        detail=f"The assets entered in the app could not be read (the stored document {what}) and are "
                               "left out this run; the configuration's entries still count.")


def load(path: Path) -> tuple[dict, list[QualityCheck]]:
    """The document at `path`, never raising and never writing. A missing file is the empty document with no check;
    a name holding no regular file (a folder, a FIFO, a link: never followed and never waited on, etl/files.py), one
    that cannot be read, is larger than MAX_BYTES (its size checked before it is read), is not JSON, holds text that is
    not valid Unicode (a lone surrogate: encodes_as_utf8), is not an object holding a list `assets`, or is not version
    1 is the empty document and one `manual_assets.load` warning saying which."""
    try:
        raw = files.read_regular(path, limit=MAX_BYTES)
    except FileNotFoundError:
        return empty(), []
    except OSError as e:
        return empty(), [_load_warning("is larger than 1 MiB" if e.strerror == files.TOO_LARGE else "cannot be read")]
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):   # not UTF-8, not JSON, nested past the parser's depth
        return empty(), [_load_warning("is not valid JSON")]
    if not encodes_as_utf8(doc):                               # a lone surrogate the database would refuse
        return empty(), [_load_warning("holds text that is not valid Unicode")]
    if not isinstance(doc, dict) or not isinstance(doc.get("assets"), list):
        return empty(), [_load_warning("is not a JSON object with a list of assets")]
    version = doc.get("version")
    if type(version) is not int or version != DOCUMENT_VERSION:
        return empty(), [_load_warning(f"is not version {DOCUMENT_VERSION}")]
    return doc, []


def from_config(entry: Mapping) -> ManualAsset:
    """An `accounts.yaml` `manual_entries` item, which Config has checked (its dates ISO text): its one value on its
    `as_of`, rounded to the cent, and its `acquired` when stated. A debt class is never liquid."""
    cls = entry["asset_class"]
    acquired = entry.get("acquired")
    return ManualAsset(
        id=manual_slug(entry["account"]), name=entry["account"], institution=entry["institution"],
        owner=entry["owner"], asset_class=cls,
        liquid=bool(entry.get("liquid", False)) and cls not in LIABILITY_CLASSES,
        acquired=None if acquired is None else dt.date.fromisoformat(str(acquired)),
        values=((dt.date.fromisoformat(str(entry["as_of"])), round(entry["value"] * 100)),), origin=CONFIG)


# why an asset entered in the app is left out, in the household's words
NOT_AN_OBJECT = "it is not an object"
BAD_ID = "its id is not short runs of lowercase letters or digits joined by single dashes"
SAME_ID = "another asset in the file has the same id"
DELETED_SHAPE = "its deleted_at is neither null nor the time it was removed"
NO_NAME = "it has no name"
NAME_CONTROL = "its name holds a control character"
UNKNOWN_OWNER = "its owner is not one of the household's owners"
UNKNOWN_CLASS = "its asset class is not one the engine knows"
LIQUID_NOT_A_SWITCH = "its liquid flag is not true or false"
LIQUID_DEBT = "it is a debt marked liquid, and a debt never is"
NO_VALUE = "it has no value"
VALUE_SHAPE = "a value is not an as_of and a value"
VALUE_NOT_A_NUMBER = "a value is not a finite number"
VALUE_RANGE = "a value is out of range"
VALUE_DATE = "a value's date is not a day written YYYY-MM-DD"
ACQUIRED_DATE = "its acquired date is not a day written YYYY-MM-DD"
ACQUIRED_LATE = "it was acquired after its first value"
ACCOUNT_NAME = "its name is an account's name"
ASSET_NAME = "its name is another manual asset's name"
_FALLS_BACK = "; the configuration's entry with this id counts instead"


def _day(value) -> dt.date | None:
    """A day written YYYY-MM-DD as text (JSON has no date), else None."""
    text = iso_date(value) if isinstance(value, str) else None
    return None if text is None else dt.date.fromisoformat(text)


def _cents(value) -> int | None:
    """A dollar amount within MAX_MANUAL_VALUE either way, in cents; None when it is no finite number (a bool is none)
    or lies past that bound."""
    if not is_number(value) or not -MAX_MANUAL_VALUE <= value <= MAX_MANUAL_VALUE:
        return None
    return round(value * 100)


def _app_asset(raw, owners: Collection[str], asset_classes: Collection[str],
               liability_classes: Collection[str]) -> tuple[ManualAsset | None, str | None]:
    """An asset of the document checked against the household: (the asset, None), or (None, why it is left out)."""
    if not isinstance(raw, dict):
        return None, NOT_AN_OBJECT
    asset_id = raw.get("id")
    if not is_id(asset_id):
        return None, BAD_ID
    if raw.get("deleted_at") is not None:          # effective() passes no tombstone: this is no removal stamp
        return None, DELETED_SHAPE
    name = raw.get("name")
    if not is_text(name):
        return None, NO_NAME
    if has_control(name):
        return None, NAME_CONTROL
    owner, cls = raw.get("owner"), raw.get("asset_class")
    if not isinstance(owner, str) or owner not in owners:
        return None, UNKNOWN_OWNER
    if not isinstance(cls, str) or cls not in asset_classes:
        return None, UNKNOWN_CLASS
    liquid = raw.get("liquid")
    if liquid is not None and not isinstance(liquid, bool):
        return None, LIQUID_NOT_A_SWITCH
    if liquid and cls in liability_classes:
        return None, LIQUID_DEBT
    values = raw.get("values")
    if not isinstance(values, list) or not values:
        return None, NO_VALUE
    by_day: dict[dt.date, int] = {}
    for v in values:
        if not isinstance(v, dict):
            return None, VALUE_SHAPE
        day, cents = _day(v.get("as_of")), _cents(v.get("value"))
        if day is None:
            return None, VALUE_DATE
        if cents is None:
            return None, VALUE_NOT_A_NUMBER if not is_number(v.get("value")) else VALUE_RANGE
        by_day[day] = cents                   # of two values on one day, the later in the list
    acquired = raw.get("acquired")
    if acquired is not None:
        acquired = _day(acquired)
        if acquired is None:
            return None, ACQUIRED_DATE
        if acquired > min(by_day):
            return None, ACQUIRED_LATE
    return ManualAsset(id=asset_id, name=name, institution=None, owner=owner, asset_class=cls, liquid=bool(liquid),
                       acquired=acquired, values=tuple(sorted(by_day.items())), origin=APP), None


def _name_key(name: str) -> str:
    """Names are compared trimmed and ignoring case, as validate compares an entry's with an account's."""
    return name.strip().casefold()


def effective(doc: Mapping, config_entries: Sequence[Mapping], owners: Collection[str],
              asset_classes: Collection[str], liability_classes: Collection[str],
              account_names: Iterable[str]) -> tuple[list[ManualAsset], list[QualityCheck]]:
    """The manual assets net worth counts, and one `manual_assets.entry` warning per asset of `doc` left out (in the
    document's order). A tombstone (is_tombstone) is no asset. An asset of the document is left out when it is not an
    object, its id fails is_id (or an earlier live asset has it), its deleted_at is neither null nor a tombstone's, it
    has no name or its name holds a control character (has_control), its owner is not in `owners` or its class not in `asset_classes`, its liquid flag is no true or false
    or it is a debt (`liability_classes`) marked liquid, it has no value, a value is no finite number or lies past
    MAX_MANUAL_VALUE dollars either way or its date is no ISO day, its `acquired` is no ISO day or later than its
    first value, or its name (trimmed, ignoring case) is one of `account_names` or another counted asset's. A config
    entry keeps its name against an app asset: an asset whose name clashes with a counted entry is the one left out,
    which can bring the entry it overrode back, so the names are checked again until nothing more is left out. The
    warning names the asset by its id (by its place in the file when it has none it may use) and says why, never a
    value. A counted asset in a debt class that states a value above zero adds a `manual_assets.debt_sign` warning
    (debt_sign_checks) and still counts as stated."""
    config = [from_config(e) for e in config_entries]
    config_ids = {a.id for a in config}
    skipped: list[tuple[int, str | None, str]] = []   # (place in the document, its id when that names it, why)
    candidates: list[tuple[int, ManualAsset]] = []
    taken_ids: set[str] = set()
    for place, raw in enumerate(doc.get("assets") or ()):
        if isinstance(raw, dict) and is_tombstone(raw):
            continue                                  # removed in the app: a tombstone, not an asset
        asset, why = _app_asset(raw, owners, asset_classes, liability_classes)
        if asset is not None and asset.id in taken_ids:
            asset, why = None, SAME_ID
        if asset is None:
            skipped.append((place, None if why in (NOT_AN_OBJECT, BAD_ID, SAME_ID) else raw["id"], why))
            continue
        taken_ids.add(asset.id)
        candidates.append((place, asset))

    accounts = {_name_key(n) for n in account_names}
    left_out: dict[int, str] = {}
    while True:
        overridden = {a.id for place, a in candidates if place not in left_out}
        names = {_name_key(a.name) for a in config if a.id not in overridden}
        more = False
        for place, a in candidates:
            if place in left_out:
                continue
            key = _name_key(a.name)
            if key in accounts or key in names:
                left_out[place] = ACCOUNT_NAME if key in accounts else ASSET_NAME
                more = True
            else:
                names.add(key)
        if not more:
            break
    skipped += [(place, a.id, left_out[place]) for place, a in candidates if place in left_out]

    kept = [a for place, a in candidates if place not in left_out]
    kept_ids = {a.id for a in kept}
    out = sorted([*kept, *(a for a in config if a.id not in kept_ids)], key=lambda a: (a.name.casefold(), a.id))
    checks = [QualityCheck(name="manual_assets.entry", status="warn",
                           detail=f"manual asset {f'#{place + 1}' if asset_id is None else repr(asset_id)} from the "
                                  f"app left out: {why}{_FALLS_BACK if asset_id in config_ids else ''}")
              for place, asset_id, why in sorted(skipped, key=lambda s: s[0])]
    return out, checks + debt_sign_checks(out, liability_classes)


def overridden(assets: Sequence[ManualAsset], config_entries: Sequence[Mapping]) -> list[ManualAsset]:
    """The config entries an asset entered in the app hides (accounts.json `manual_overridden`): each `manual_entries`
    item whose id an app asset of `assets` (the effective list, `effective`) carries, as `from_config` reads it, sorted as
    the effective list is. Net worth counts the app asset in its place; removing that asset brings the entry back."""
    taken = {a.id for a in assets if a.origin == APP}
    return sorted((c for c in (from_config(e) for e in config_entries) if c.id in taken),
                  key=lambda a: (a.name.casefold(), a.id))


# why a counted asset in a debt class is suspect, in the household's words (it is counted as stated, never changed)
DEBT_ABOVE_ZERO = ("is in the debt class {cls} but states a value above zero, which net worth counts as an asset: a "
                   "debt's value is negative (in the dashboard, enter the amount owed)")


def debt_sign_checks(assets: Sequence[ManualAsset], liability_classes: Collection[str]) -> list[QualityCheck]:
    """One `manual_assets.debt_sign` warning per counted asset in a debt class (`liability_classes`) that states a
    value above zero: net worth adds it as it is stated, so a debt written as a positive figure raises net worth
    instead of lowering it. A warning, never a refusal; it names the asset by its id and where it was entered, never a
    value."""
    return [QualityCheck(name="manual_assets.debt_sign", status="warn",
                         detail=f"manual asset {a.id!r} {'from the app' if a.origin == APP else 'in accounts.yaml'} "
                                + DEBT_ABOVE_ZERO.format(cls=a.asset_class))
            for a in assets if a.asset_class in liability_classes and any(cents > 0 for _, cents in a.values)]

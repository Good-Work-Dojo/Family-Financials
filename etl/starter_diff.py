# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""`python -m etl rules starter-diff` — what the starter files hold that a household's own rule files lack.

A household copies `starter/` into its `config/` once and edits the copy, so a newer starter's additions never reach
it by themselves. This lists them, one `Entry(file, section, label)` each, and judges nothing: an entry the household
removed on purpose is listed again. Entries are compared by identity:

  category_rules.yaml   taxonomy: a category, or a subcategory under a category the household has;
                        keyword_rules: by name
  vendor_rules.yaml     patterns: by vendor (a pattern the household edited stays its own); strip_prefixes, cleanup,
                        acronyms and learning.exclude: by value
  transfer_rules.yaml   rules: by name; outside: by value
  bank_category_map.yaml  by label
  assumptions.yaml      by top-level key, and by key under projections and coverage

A category or subcategory that is the `from` of one of the household's category renames, and a vendor that is the
`from` of one of its vendor renames, counts as present. A rule file the household lacks is one entry for the whole
starter file. The files are read as YAML directly, so a config the validator would refuse still diffs. Offline;
nothing is written.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

import yaml

from .validate import CONFIG_FILES

RULE_FILES = tuple(f for f in CONFIG_FILES
                   if f in ("category_rules.yaml", "vendor_rules.yaml", "transfer_rules.yaml",
                            "bank_category_map.yaml", "assumptions.yaml"))
ASSUMPTION_BLOCKS = ("projections", "coverage")


class Entry(NamedTuple):
    file: str
    section: str
    label: str


def _read(path: Path) -> object | None:
    """The file's YAML (None when it is empty). Raises OSError when it exists but cannot be read or parsed as YAML:
    a file the tool cannot read is an error, never an empty file that would list every starter entry."""
    try:
        return yaml.safe_load(path.read_text("utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as e:      # ValueError: an impossible date, and bad UTF-8
        raise OSError(f"{path}: not readable as YAML") from e


def _map(doc, key: str) -> dict:
    value = doc.get(key) if isinstance(doc, dict) else None
    return value if isinstance(value, dict) else {}


def _list(doc, key: str) -> list:
    value = doc.get(key) if isinstance(doc, dict) else None
    return value if isinstance(value, list) else []


def _value(item) -> str:
    """A list item's identity: its text, or a stable rendering of a mapping or number."""
    if isinstance(item, str):
        return item
    try:
        return json.dumps(item, sort_keys=True, default=str)
    except TypeError:                # a mapping whose keys cannot be ordered together
        return repr(item)


def _names(items: list, key: str) -> set[str]:
    return {str(i[key]) for i in items if isinstance(i, dict) and i.get(key) is not None}


def _missing_values(mine: list, theirs: list) -> list[str]:
    have = {_value(i) for i in mine}
    return [_value(i) for i in theirs if _value(i) not in have]


def _missing_names(mine: list, theirs: list, key: str, *, renamed: Iterable[str] = ()) -> list[str]:
    have = _names(mine, key) | set(renamed)
    return [str(i[key]) for i in theirs if isinstance(i, dict) and i.get(key) is not None and str(i[key]) not in have]


def _category_renames(doc) -> tuple[set[str], set[tuple[str, str]]]:
    """The categories and the category/subcategory pairs the household's category renames move away from."""
    whole: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for r in _list(doc, "renames"):
        src = r.get("from") if isinstance(r, dict) else None
        if isinstance(src, dict) and src.get("category") is not None:
            if src.get("subcategory") is None:
                whole.add(str(src["category"]))
            else:
                pairs.add((str(src["category"]), str(src["subcategory"])))
    return whole, pairs


def _vendor_renames(doc) -> set[str]:
    """The vendor names the household's renames move away from, casefolded as the engine matches them."""
    return {str(r["from"]).casefold() for r in _list(doc, "renames") if isinstance(r, dict) and r.get("from") is not None}


def _category_rules(mine, theirs) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    have = _map(mine, "taxonomy")
    whole, pairs = _category_renames(mine)
    for category, subs in _map(theirs, "taxonomy").items():
        if category not in have:
            if category not in whole:
                out.append(("taxonomy", str(category)))
            continue
        mine_subs = have[category] if isinstance(have[category], list) else []
        for sub in subs if isinstance(subs, list) else []:
            if sub not in mine_subs and (category, sub) not in pairs:
                out.append(("taxonomy", f"{category} / {sub}"))
    out += [("keyword_rules", n) for n in _missing_names(_list(mine, "keyword_rules"), _list(theirs, "keyword_rules"), "name")]
    return out


def _vendor_rules(mine, theirs) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    renamed = _vendor_renames(mine)
    have = {str(q["vendor"]).casefold() for q in _list(mine, "patterns")
            if isinstance(q, dict) and q.get("vendor") is not None}
    for p in _list(theirs, "patterns"):
        if not isinstance(p, dict) or p.get("vendor") is None:
            continue
        if str(p["vendor"]).casefold() not in have | renamed:
            out.append(("patterns", str(p["vendor"])))
    for section in ("strip_prefixes", "cleanup", "acronyms"):
        out += [(section, v) for v in _missing_values(_list(mine, section), _list(theirs, section))]
    # an excluded vendor the household renamed is present under its new name, so the renamed `from` names count too
    have_excluded = {_value(v).casefold() for v in _list(_map(mine, "learning"), "exclude")} | renamed
    out += [("learning.exclude", _value(v)) for v in _list(_map(theirs, "learning"), "exclude")
            if _value(v).casefold() not in have_excluded]
    return out


def _transfer_rules(mine, theirs) -> list[tuple[str, str]]:
    out = [("rules", n) for n in _missing_names(_list(mine, "rules"), _list(theirs, "rules"), "name")]
    out += [("outside", v) for v in _missing_values(_list(mine, "outside"), _list(theirs, "outside"))]
    return out


def _bank_map(mine, theirs) -> list[tuple[str, str]]:
    have = mine if isinstance(mine, dict) else {}
    return [("bank_category_map", str(k)) for k in (theirs if isinstance(theirs, dict) else {}) if k not in have]


def _assumptions(mine, theirs) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    have = mine if isinstance(mine, dict) else {}
    for key, value in (theirs if isinstance(theirs, dict) else {}).items():
        if key not in have:
            out.append(("assumptions", str(key)))
        elif key in ASSUMPTION_BLOCKS and isinstance(value, dict):
            mine_block = _map(have, key)
            out += [("assumptions", f"{key}.{k}") for k in value if k not in mine_block]
    return out


_COMPARE = {
    "category_rules.yaml": _category_rules,
    "vendor_rules.yaml": _vendor_rules,
    "transfer_rules.yaml": _transfer_rules,
    "bank_category_map.yaml": _bank_map,
    "assumptions.yaml": _assumptions,
}


def diff(config_dir: Path, starter_dir: Path) -> list[Entry]:
    """Every starter entry the household's files lack, grouped by file in CONFIG_FILES order. Raises OSError when the
    starter directory is not a directory or holds none of the rule files, or when a file that exists cannot be read as
    YAML. An entry the starter lists twice is listed once."""
    config_dir, starter_dir = Path(config_dir), Path(starter_dir)
    if not starter_dir.is_dir():
        raise OSError(f"{starter_dir}: not a directory")
    if not any((starter_dir / name).is_file() for name in RULE_FILES):
        raise OSError(f"{starter_dir}: no starter rule files found")
    out: dict[Entry, None] = {}
    for name in RULE_FILES:
        starter_file = starter_dir / name
        if not starter_file.is_file():
            continue
        if not (config_dir / name).is_file():
            out[Entry(name, "", "not in your config")] = None
            continue
        for section, label in _COMPARE[name](_read(config_dir / name), _read(starter_file)):
            out[Entry(name, section, label)] = None
    return list(out)


def report(entries: list[Entry], *, out=print) -> None:
    """One line per entry, then the count (or that nothing is new)."""
    for e in entries:
        out(f"starter/{e.file}: {e.section}: {e.label}" if e.section else f"starter/{e.file}: {e.label}")
    out(f"{len(entries)} new starter entries" if entries else "nothing new in the starter files")

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The names the engine itself files rows under (etl/models.py RESERVED): a taxonomy holds every one, a household with
no taxonomy gets exactly them, a category rename cannot move one, and assumptions.json exports them as `reserved`.
Also here: assumptions.json `thresholds` carries a fixed list of assumptions.yaml keys, so a new key cannot leak into
it. Synthetic config dirs and invented names, except where a test reads the demo household's config."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from conftest import ROOT, demo_config, load_demo, make_txn, write_household

from etl import models
from etl.config import Config
from etl.enrich.vendors import VendorNormalizer
from etl.models import FALLBACK_CATEGORY, RESERVED, RESERVED_TAXONOMY, UNCATEGORIZED_SUB
from etl.pipeline import THRESHOLD_KEYS, Pipeline, categorizer_for

ROLES = ("fallback_category", "uncategorized_sub", "p2p_unknown_sub", "internal_category", "income_category",
         "income_other_sub", "fees_category")
_CHECKING = {"name": "Sample Checking", "institution": "Sample Bank", "owner": "Alex", "kind": "checking"}
# the reserved names plus one category of the household's own, as a taxonomy file writes them
_FULL = {**{c: list(subs) for c, subs in RESERVED_TAXONOMY.items()}, "Dining": ["Coffee", "Restaurants"]}


def _config_dir(tmp_path: Path, *, category_rules=None, assumptions=None) -> Path:
    """accounts.yaml (one invented checking account) and household.yaml, plus category_rules.yaml and
    assumptions.yaml when given (a string is written as the file's text, anything else as YAML)."""
    d = tmp_path / "cfg"
    d.mkdir(parents=True)
    (d / "accounts.yaml").write_text(yaml.safe_dump({"accounts": {"demo_checking_0000": _CHECKING}}), "utf-8")
    write_household(d)
    for name, doc in (("category_rules.yaml", category_rules), ("assumptions.yaml", assumptions)):
        if doc is not None:
            text = doc if isinstance(doc, str) else yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
            (d / name).write_text(text, "utf-8")
    return d


def _refused(tmp_path: Path, **files) -> str:
    with pytest.raises(ValueError) as e:
        Config(_config_dir(tmp_path, **files))
    return str(e.value)


def _exported_assumptions(tmp_path: Path, cfg: Config) -> dict:
    out = tmp_path / "exports"
    out.mkdir()
    Pipeline(cfg)._export_assumptions(out)
    return json.loads((out / "assumptions.json").read_text("utf-8"))


# ---------------------------------------------------------------- the names


def test_reserved_has_seven_roles_each_with_its_own_constant():
    assert tuple(RESERVED) == ROLES
    for role in ROLES:
        assert getattr(models, role.upper()) == RESERVED[role]
    assert len(set(RESERVED.values())) == len(RESERVED)
    with pytest.raises(TypeError):
        RESERVED["fallback_category"] = "Elsewhere"      # read-only: one definition, never patched at run time


def test_the_reserved_taxonomy_files_each_subcategory_under_its_category():
    cats = {RESERVED[r] for r in ROLES if r.endswith("_category")}
    subs = {RESERVED[r] for r in ROLES if r.endswith("_sub")}
    assert set(RESERVED_TAXONOMY) == cats
    assert {s for listed in RESERVED_TAXONOMY.values() for s in listed} == subs
    assert set(RESERVED_TAXONOMY[RESERVED["fallback_category"]]) == {RESERVED["uncategorized_sub"],
                                                                      RESERVED["p2p_unknown_sub"]}
    assert RESERVED_TAXONOMY[RESERVED["income_category"]] == (RESERVED["income_other_sub"],)


@pytest.mark.parametrize("taxonomy", [
    pytest.param(lambda: yaml.safe_load((ROOT / "starter" / "category_rules.yaml").read_text("utf-8"))["taxonomy"],
                 id="starter"),
    pytest.param(lambda: load_demo("category_rules.yaml")["taxonomy"], id="demo"),
])
def test_every_reserved_name_is_in_the_taxonomy(taxonomy):
    tax = taxonomy()
    for category, subs in RESERVED_TAXONOMY.items():
        assert category in tax
        for sub in subs:
            assert sub in tax[category], (category, sub)
    listed = {*tax, *(s for subs in tax.values() for s in subs or ())}
    assert set(RESERVED.values()) <= listed


# ---------------------------------------------------------------- no taxonomy: the reserved names alone


@pytest.mark.parametrize("category_rules", [
    pytest.param(None, id="no-file"),
    pytest.param("", id="empty-file"),
    pytest.param({"keyword_rules": []}, id="no-taxonomy-key"),
    pytest.param({"taxonomy": None}, id="null-taxonomy"),
    pytest.param({"taxonomy": {}}, id="empty-taxonomy"),
])
def test_without_a_taxonomy_the_taxonomy_is_the_reserved_names_and_a_row_lands_in_the_fallback(tmp_path,
                                                                                               category_rules):
    cfg = Config(_config_dir(tmp_path, category_rules=category_rules))
    assert cfg.taxonomy == {c: list(subs) for c, subs in RESERVED_TAXONOMY.items()}
    assert list(cfg.taxonomy) == list(RESERVED_TAXONOMY)
    normalizer = VendorNormalizer(cfg.vendor_rules)
    t = make_txn(desc="SAMPLE STORE 0042", account="demo_checking_0000", date="2030-01-15", cents=-1200)
    t.vendor, _ = normalizer.normalize(t.description_raw)
    categorizer_for(cfg).apply([t], normalizer.clean)
    assert (t.category, t.subcategory, t.category_source) == (FALLBACK_CATEGORY, UNCATEGORIZED_SUB, "fallback")
    assert t.subcategory in cfg.taxonomy[t.category]
    assert _exported_assumptions(tmp_path, cfg)["taxonomy"] == cfg.taxonomy


def test_without_a_taxonomy_rules_and_renames_are_checked_against_the_reserved_names(tmp_path):
    fallback, uncategorized = RESERVED["fallback_category"], RESERVED["uncategorized_sub"]
    ok = {"keyword_rules": [{"pattern": "SAMPLE", "category": fallback, "subcategory": uncategorized}]}
    Config(_config_dir(tmp_path / "ok", category_rules=ok))
    off = {"keyword_rules": [{"pattern": "SAMPLE", "category": "Dining"}]}
    assert "category 'Dining' not in taxonomy" in _refused(tmp_path / "off", category_rules=off)
    rename = {"renames": [{"from": {"category": "Eating Out"}, "to": {"category": "Dining"}}]}
    assert "to.category 'Dining' not in taxonomy" in _refused(tmp_path / "rename", category_rules=rename)


def test_a_taxonomy_that_is_present_is_the_one_every_reader_sees(tmp_path):
    cfg = Config(_config_dir(tmp_path, category_rules={"taxonomy": _FULL}))
    assert cfg.taxonomy == _FULL
    assert _exported_assumptions(tmp_path, cfg)["taxonomy"] == _FULL


# ---------------------------------------------------------------- a taxonomy must hold them


def _missing_cases():
    """(the taxonomy with one reserved name removed, the error it gets), one per role."""
    for role in ROLES:
        tax = json.loads(json.dumps(_FULL))
        if role.endswith("_category"):
            category = RESERVED[role]
            del tax[category]
            why = f"missing reserved category '{category}'"
        else:
            category = next(c for c, subs in RESERVED_TAXONOMY.items() if RESERVED[role] in subs)
            tax[category].remove(RESERVED[role])
            why = f"missing reserved subcategory '{category} / {RESERVED[role]}'"
        yield pytest.param(tax, f"category_rules.yaml: taxonomy: {why} — add it (the engine files rows there)",
                           id=role)


@pytest.mark.parametrize("taxonomy, message", list(_missing_cases()))
def test_a_taxonomy_missing_a_reserved_name_fails_at_load(tmp_path, taxonomy, message):
    assert _refused(tmp_path, category_rules={"taxonomy": taxonomy}) == message


def test_a_reserved_category_listed_with_no_subcategories_still_needs_its_reserved_ones(tmp_path):
    tax = {**_FULL, RESERVED["income_category"]: None}
    assert _refused(tmp_path, category_rules={"taxonomy": tax}) == (
        f"category_rules.yaml: taxonomy: missing reserved subcategory "
        f"'{RESERVED['income_category']} / {RESERVED['income_other_sub']}' — add it (the engine files rows there)")


def test_a_taxonomy_that_is_not_a_mapping_is_refused(tmp_path):
    assert _refused(tmp_path, category_rules={"taxonomy": list(_FULL)}).startswith(
        "category_rules.yaml: taxonomy: expected a mapping")


# ---------------------------------------------------------------- a rename cannot move one


_TO = {"category": "Dining", "subcategory": "Restaurants"}


def _rename_cases():
    """(a rename whose `from` is one reserved name, that name as the error spells it), one per role. Each `to` is a
    valid target the rest of the list does not rename again, so only the reserved name can refuse the entry."""
    for role in ROLES:
        if role.endswith("_category"):
            name = RESERVED[role]
            yield pytest.param({"from": {"category": name}, "to": dict(_TO)}, name, id=role)
        else:
            category = next(c for c, subs in RESERVED_TAXONOMY.items() if RESERVED[role] in subs)
            src = {"category": category, "subcategory": RESERVED[role]}
            yield pytest.param({"from": src, "to": dict(_TO)}, f"{category} / {RESERVED[role]}", id=role)


@pytest.mark.parametrize("rename, name", list(_rename_cases()))
def test_a_rename_of_a_reserved_name_is_refused(tmp_path, rename, name):
    keep = {"from": {"category": "Dining", "subcategory": "Coffee"}, "to": dict(_TO)}
    Config(_config_dir(tmp_path / "control", category_rules={"taxonomy": _FULL, "renames": [keep]}))
    rules = {"taxonomy": _FULL, "renames": [keep, rename]}
    assert _refused(tmp_path, category_rules=rules) == (
        f"category_rules.yaml: renames[1]: '{name}' is reserved and cannot be renamed — the engine files rows there")


def test_a_rename_inside_a_reserved_category_that_moves_no_reserved_name_is_allowed(tmp_path):
    fallback = RESERVED["fallback_category"]
    tax = {**_FULL, fallback: [*_FULL[fallback], "Sundries", "Odds"]}
    rename = {"from": {"category": fallback, "subcategory": "Sundries"},
              "to": {"category": fallback, "subcategory": "Odds"}}
    cfg = Config(_config_dir(tmp_path, category_rules={"taxonomy": tax, "renames": [rename]}))
    assert cfg.category_renamer.map(fallback, "Sundries") == (fallback, "Odds")


# ---------------------------------------------------------------- the export


def test_assumptions_json_carries_the_reserved_names(tmp_path):
    doc = _exported_assumptions(tmp_path, Config(_config_dir(tmp_path)))
    assert doc["reserved"] == dict(RESERVED)
    assert list(doc["reserved"]) == list(ROLES)


def test_thresholds_hold_exactly_the_keys_the_demo_config_exports_there(tmp_path):
    doc = _exported_assumptions(tmp_path, demo_config())
    assert set(doc["thresholds"]) == set(THRESHOLD_KEYS) == {
        "match_window_bdays", "venmo_match_window_bdays", "venmo_review_threshold",
        "uncategorized_review_threshold", "unknown_credit_review_threshold", "cancelled_subscriptions"}
    assert doc["taxonomy"] == load_demo("category_rules.yaml")["taxonomy"]
    assert doc["reserved"] == dict(RESERVED)


def test_an_assumptions_key_outside_the_list_never_reaches_thresholds(tmp_path):
    assumptions = {"match_window_bdays": 4, "coverage": {"min_share": 0.5}, "projections": {}, "household": {}}
    cfg = Config(_config_dir(tmp_path, assumptions=assumptions))
    cfg.assumptions["something_new"] = 1        # the load refuses an unknown key: set after it, it reaches the export
    doc = _exported_assumptions(tmp_path, cfg)
    assert doc["thresholds"] == {"match_window_bdays": 4}

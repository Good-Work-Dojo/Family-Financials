# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""`python -m etl rules starter-diff` on synthetic config directories: copies of starter/ with invented edits."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import yaml
from conftest import ROOT, write_household

from etl import starter_diff
from etl.__main__ import main as etl_main

STARTER = ROOT / "starter"
RULE_FILES = ("assumptions.yaml", "bank_category_map.yaml", "category_rules.yaml", "transfer_rules.yaml",
              "vendor_rules.yaml")


def household(tmp_path: Path, *, drop=(), edits=None) -> Path:
    """A config dir holding household.yaml and a copy of every starter file but `drop`; `edits` maps a file name to a
    function that changes its parsed YAML (the file is then written back)."""
    d = tmp_path / "config"
    write_household(d)
    for name in RULE_FILES:
        if name in drop:
            continue
        doc = yaml.safe_load((STARTER / name).read_text("utf-8"))
        if edits and name in edits:
            edits[name](doc)
        (d / name).write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), "utf-8")
    return d


def starter_with(tmp_path: Path, edits) -> Path:
    """A starter dir: a copy of starter/ with `edits` (file name -> function over the parsed YAML) applied."""
    d = tmp_path / "newer-starter"
    d.mkdir()
    for name in RULE_FILES:
        doc = yaml.safe_load((STARTER / name).read_text("utf-8"))
        if name in edits:
            edits[name](doc)
        (d / name).write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), "utf-8")
    return d


def fingerprint(root: Path) -> dict[str, tuple[int, int]]:
    return {str(f.relative_to(root)): (f.stat().st_size, f.stat().st_mtime_ns)
            for f in sorted(root.rglob("*")) if f.is_file()}


def test_a_config_copied_from_the_starter_has_nothing_new(tmp_path):
    assert starter_diff.diff(household(tmp_path), STARTER) == []


def _vendor(doc):
    doc["patterns"].append({"pattern": r"\bACME COFFEE\b", "vendor": "Acme Coffee", "category": "Dining"})


def _category(doc):
    doc["taxonomy"]["Hobbies"] = ["Crafts"]


def _subcategory(doc):
    doc["taxonomy"]["Groceries"].append("Bakery")


def _keyword(doc):
    doc["keyword_rules"].append({"name": "acme-rule", "any": [{"field": "description", "op": "contains",
                                                              "value": "ACME"}], "set": {"category": "Dining"}})


def _transfer(doc):
    doc["rules"].append({"name": "acme-transfer", "pattern": "ACME", "kinds": ["checking"], "type": "transfer"})


def _bank(doc):
    doc["Acme Label"] = {"category": "Dining"}


def _cleanup(doc):
    doc["cleanup"].append(r"\bACME REF\b")


def _assumption(doc):
    doc["projections"]["acme_rate_pct"] = 2.0


@pytest.mark.parametrize("file,edit,expected", [
    ("vendor_rules.yaml", _vendor, "starter/vendor_rules.yaml: patterns: Acme Coffee"),
    ("category_rules.yaml", _category, "starter/category_rules.yaml: taxonomy: Hobbies"),
    ("category_rules.yaml", _subcategory, "starter/category_rules.yaml: taxonomy: Groceries / Bakery"),
    ("category_rules.yaml", _keyword, "starter/category_rules.yaml: keyword_rules: acme-rule"),
    ("transfer_rules.yaml", _transfer, "starter/transfer_rules.yaml: rules: acme-transfer"),
    ("bank_category_map.yaml", _bank, "starter/bank_category_map.yaml: bank_category_map: Acme Label"),
    ("vendor_rules.yaml", _cleanup, r"starter/vendor_rules.yaml: cleanup: \bACME REF\b"),
    ("assumptions.yaml", _assumption, "starter/assumptions.yaml: assumptions: projections.acme_rate_pct"),
])
def test_each_kind_of_new_entry_is_listed_once(tmp_path, file, edit, expected, capsys):
    newer = starter_with(tmp_path, {file: edit})
    config = household(tmp_path)
    entries = starter_diff.diff(config, newer)
    assert len(entries) == 1
    starter_diff.report(entries)
    assert capsys.readouterr().out.splitlines() == [expected, "1 new starter entries"]


def test_an_edited_pattern_of_a_listed_vendor_is_not_new(tmp_path):
    def edit(doc):
        doc["patterns"][0]["pattern"] = r"\bMY OWN SPELLING\b"
        doc["patterns"][0]["category"] = "Shopping"
    assert starter_diff.diff(household(tmp_path, edits={"vendor_rules.yaml": edit}), STARTER) == []


def test_a_renamed_category_or_vendor_is_not_new(tmp_path):
    vendor = yaml.safe_load((STARTER / "vendor_rules.yaml").read_text("utf-8"))["patterns"][0]["vendor"]

    def categories(doc):
        del doc["taxonomy"]["Pets"]
        doc["taxonomy"]["Animals"] = []
        doc["taxonomy"]["Dining"].remove("Coffee")
        doc["renames"] = [{"from": {"category": "Pets"}, "to": {"category": "Animals"}},
                          {"from": {"category": "Dining", "subcategory": "Coffee"},
                           "to": {"category": "Dining", "subcategory": "Restaurants"}}]

    def vendors(doc):
        doc["patterns"] = [p for p in doc["patterns"] if p["vendor"] != vendor]
        doc["renames"] = [{"from": vendor, "to": "Alex's Grocer"}]

    config = household(tmp_path, edits={"category_rules.yaml": categories, "vendor_rules.yaml": vendors})
    assert starter_diff.diff(config, STARTER) == []


def test_a_removed_starter_vendor_is_listed(tmp_path):
    vendor = yaml.safe_load((STARTER / "vendor_rules.yaml").read_text("utf-8"))["patterns"][0]["vendor"]

    def edit(doc):
        doc["patterns"] = [p for p in doc["patterns"] if p["vendor"] != vendor]
    entries = starter_diff.diff(household(tmp_path, edits={"vendor_rules.yaml": edit}), STARTER)
    assert entries == [starter_diff.Entry("vendor_rules.yaml", "patterns", vendor)]


def test_a_missing_rule_file_is_one_entry(tmp_path, capsys):
    config = household(tmp_path, drop=("transfer_rules.yaml",))
    entries = starter_diff.diff(config, STARTER)
    assert len(entries) == 1
    starter_diff.report(entries)
    assert capsys.readouterr().out.splitlines() == ["starter/transfer_rules.yaml: not in your config",
                                                    "1 new starter entries"]


def test_an_invalid_config_still_diffs(tmp_path):
    def edit(doc):
        doc["keyword_rules"].append({"pattern": "(", "category": "No Such Category", "bogus_key": 1})
        doc["taxonomy"].pop("Pets")
    config = household(tmp_path, edits={"category_rules.yaml": edit})
    (config / "accounts.yaml").write_text("accounts: [this is: not valid")
    assert starter_diff.diff(config, STARTER) == [starter_diff.Entry("category_rules.yaml", "taxonomy", "Pets")]


def test_nothing_is_written(tmp_path):
    config = household(tmp_path, drop=("assumptions.yaml",))
    starter = tmp_path / "starter-copy"
    shutil.copytree(STARTER, starter)
    before = (fingerprint(tmp_path / "config"), fingerprint(starter))
    assert starter_diff.diff(config, starter)
    assert (fingerprint(tmp_path / "config"), fingerprint(starter)) == before


def test_the_entries_follow_the_config_file_order(tmp_path):
    config = household(tmp_path, drop=("bank_category_map.yaml", "vendor_rules.yaml", "assumptions.yaml"))
    assert [e.file for e in starter_diff.diff(config, STARTER)] == ["assumptions.yaml", "vendor_rules.yaml",
                                                                    "bank_category_map.yaml"]


def test_the_command_prints_the_lines_and_the_count(tmp_path, monkeypatch, capsys):
    newer = starter_with(tmp_path, {"vendor_rules.yaml": _vendor, "category_rules.yaml": _category})
    config = household(tmp_path)
    monkeypatch.setenv("FF_CONFIG_DIR", str(config))
    monkeypatch.setattr(sys, "argv", ["etl", "rules", "starter-diff", "--starter", str(newer)])
    assert etl_main() == 0
    assert capsys.readouterr().out.splitlines() == [
        "starter/category_rules.yaml: taxonomy: Hobbies",
        "starter/vendor_rules.yaml: patterns: Acme Coffee",
        "2 new starter entries"]
    monkeypatch.setattr(sys, "argv", ["etl", "rules", "starter-diff"])
    assert etl_main() == 0
    assert capsys.readouterr().out.strip() == "nothing new in the starter files"
    monkeypatch.setenv("FF_STARTER_DIR", str(newer))
    assert etl_main() == 0
    assert capsys.readouterr().out.splitlines()[-1] == "2 new starter entries"


def test_an_unreadable_config_or_starter_directory_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FF_CONFIG_DIR", str(tmp_path / "none"))
    monkeypatch.setattr(sys, "argv", ["etl", "rules", "starter-diff"])
    assert etl_main() == 2
    monkeypatch.setenv("FF_CONFIG_DIR", str(household(tmp_path)))
    monkeypatch.setattr(sys, "argv", ["etl", "rules", "starter-diff", "--starter", str(tmp_path / "none")])
    assert etl_main() == 2
    assert capsys.readouterr().out == ""


def test_the_starter_directory_is_the_argument_then_the_variable_then_the_default(tmp_path, monkeypatch):
    from etl.config import Paths
    monkeypatch.delenv("FF_STARTER_DIR", raising=False)
    assert Paths().starter_dir() == STARTER
    monkeypatch.setenv("FF_STARTER_DIR", str(tmp_path / "env"))
    assert Paths().starter_dir() == tmp_path / "env"
    assert Paths().starter_dir(tmp_path / "arg") == tmp_path / "arg"


# ------------------------------------------------------------------------------------------------ unreadable input

@pytest.mark.parametrize("text", ["patterns:\n\t- vendor: X\n", "patterns: [unclosed\n", "d: 2024-13-45\n"])
def test_an_unparseable_household_rule_file_is_an_error_not_a_list_of_everything(tmp_path, monkeypatch, capsys, text):
    config = household(tmp_path)
    (config / "vendor_rules.yaml").write_text(text, "utf-8")
    monkeypatch.setenv("FF_CONFIG_DIR", str(config))
    monkeypatch.setattr(sys, "argv", ["etl", "rules", "starter-diff"])
    assert etl_main() == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.count("\n") == 1 and str(config / "vendor_rules.yaml") in out.err


def test_an_unparseable_starter_file_is_an_error(tmp_path):
    newer = starter_with(tmp_path, {})
    (newer / "category_rules.yaml").write_text("taxonomy: [unclosed\n", "utf-8")
    with pytest.raises(OSError, match="category_rules.yaml: not readable as YAML"):
        starter_diff.diff(household(tmp_path), newer)


def test_a_starter_directory_without_rule_files_is_an_error(tmp_path, monkeypatch, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(OSError, match="no starter rule files found"):
        starter_diff.diff(household(tmp_path), empty)
    monkeypatch.setenv("FF_CONFIG_DIR", str(household(tmp_path / "again")))
    monkeypatch.setattr(sys, "argv", ["etl", "rules", "starter-diff", "--starter", str(empty)])
    assert etl_main() == 2
    assert capsys.readouterr().out == ""


# ------------------------------------------------------------------------------------------------ odd shapes

@pytest.mark.parametrize("file,text", [
    ("vendor_rules.yaml", ""),
    ("category_rules.yaml", "- a\n- b\n"),
    ("category_rules.yaml", "taxonomy: null\n"),
    ("vendor_rules.yaml", "patterns:\n  - {pattern: x}\n  - just a string\n"),
    ("category_rules.yaml", "keyword_rules:\n  - {pattern: x, category: Dining}\n"),
    ("transfer_rules.yaml", "rules:\n  - not a mapping\n  - 3\n"),
    ("vendor_rules.yaml", "learning: []\n"),
    ("vendor_rules.yaml", "acronyms: 3\n"),
    ("vendor_rules.yaml", "cleanup:\n  - {1: a, b: c}\n"),
    ("bank_category_map.yaml", "- a\n"),
    ("assumptions.yaml", "projections: 3\ncoverage: [a]\n"),
])
def test_a_malformed_rule_file_never_raises(tmp_path, file, text):
    config = household(tmp_path)
    (config / file).write_text(text, "utf-8")
    entries = starter_diff.diff(config, STARTER)
    assert all(isinstance(e, starter_diff.Entry) for e in entries)


# ------------------------------------------------------------------------------------------------ identity rules

def test_a_subcategory_under_a_missing_category_is_listed_once_as_the_category(tmp_path):
    def mine(doc):
        del doc["taxonomy"]["Dining"]
    config = household(tmp_path, edits={"category_rules.yaml": mine})
    entries = starter_diff.diff(config, STARTER)
    assert entries == [starter_diff.Entry("category_rules.yaml", "taxonomy", "Dining")]


def test_vendor_names_compare_without_case(tmp_path):
    def mine(doc):
        doc["patterns"].append({"pattern": r"\bACME COFFEE\b", "vendor": "acme coffee", "category": "Dining"})
    config = household(tmp_path, edits={"vendor_rules.yaml": mine})
    newer = starter_with(tmp_path, {"vendor_rules.yaml": _vendor})
    assert starter_diff.diff(config, newer) == []


def test_a_name_the_starter_lists_twice_is_listed_once(tmp_path):
    def twice(doc):
        _vendor(doc)
        _vendor(doc)
        _cleanup(doc)
        _cleanup(doc)
    newer = starter_with(tmp_path, {"vendor_rules.yaml": twice})
    entries = starter_diff.diff(household(tmp_path), newer)
    assert entries == [starter_diff.Entry("vendor_rules.yaml", "patterns", "Acme Coffee"),
                       starter_diff.Entry("vendor_rules.yaml", "cleanup", r"\bACME REF\b")]

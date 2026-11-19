# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""household.yaml says who the household is: its members (adults and children), the joint label, the surname, the
time zone, the net-worth options and the optional modules. Config reads it and refuses one it cannot use, every owner
name the other config files use must be one of its owners, and the assumptions export keeps its `owners` list and
`household` shape.
Synthetic config dirs, invented names."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from conftest import TIMEZONE, write_household

from etl.config import Config
from etl.models import INCOME_CATEGORY, RESERVED_TAXONOMY
from etl.pipeline import Pipeline

_CHECKING = {"name": "Sample Checking", "institution": "Sample Bank", "owner": "Joint", "kind": "checking"}
_HOUSE = {"account": "Sample House", "institution": "Sample", "owner": "Alex", "asset_class": "House",
          "value": 1000, "liquid": False, "as_of": "2030-01-31"}
# the reserved names every taxonomy holds, plus the ones these files use
_TAXONOMY = {**{c: list(subs) for c, subs in RESERVED_TAXONOMY.items()}, "Dining": [],
             INCOME_CATEGORY: [*RESERVED_TAXONOMY[INCOME_CATEGORY], "Salary - Sam"]}
# one reference of each kind, every one naming a household owner
_INCOME_RULE = {"pattern": "^SAMPLE PAYROLL", "type": "income", "subcategory": "Salary - Sam", "earner": "Sam"}
_OWNER_RULE = {"name": "joint-dining", "all": [{"field": "owner", "op": "is", "value": "Joint"}],
               "set": {"category": "Dining"}}
_OVERRIDE = {"match": {"vendor": "Sample Cafe"}, "set": {"earner": "Alex"}, "note": "an invented row"}
_RETIREMENT = {"Sample Checking": "Sam"}


def _config_dir(tmp_path: Path, *, accounts: dict | None = None, assumptions: dict | None = None,
                category_rules: dict | None = None, overrides: list | None = None, household: bool = True,
                **hh) -> Path:
    """A synthetic config dir whose files reference household owners in every place one can; `hh` goes to
    write_household, and `household=False` leaves household.yaml out."""
    d = tmp_path / "cfg"
    d.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    docs = {
        "accounts.yaml": accounts or {"source_dir": "src", "accounts": {"demo_checking_0000": dict(_CHECKING)},
                                      "manual_entries": [dict(_HOUSE)]},
        "assumptions.yaml": assumptions or {"household": {"margin_apr": 0.05,
                                                          "retirement_accounts": dict(_RETIREMENT)}},
        "category_rules.yaml": category_rules or {"taxonomy": _TAXONOMY, "income_rules": [dict(_INCOME_RULE)],
                                                  "keyword_rules": [dict(_OWNER_RULE)]},
        "overrides.yaml": overrides or [dict(_OVERRIDE)],
    }
    for name, doc in docs.items():
        (d / name).write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")
    if household:
        write_household(d, **hh)
    return d


def _config(tmp_path: Path, **kw) -> Config:
    return Config(_config_dir(tmp_path, **kw), source_dir=str(tmp_path / "src"))


def _refused(tmp_path: Path, **kw) -> str:
    with pytest.raises(ValueError) as e:
        _config(tmp_path, **kw)
    return str(e.value)


def _exported_assumptions(tmp_path: Path, cfg: Config) -> dict:
    out = tmp_path / "exports"
    out.mkdir()
    Pipeline(cfg)._export_assumptions(out)
    return json.loads((out / "assumptions.json").read_text("utf-8"))


def test_owners_are_adults_then_the_joint_label_then_children(tmp_path):
    # the file lists a child first and the adults out of alphabetical order: owners keep each group's file order
    members = [{"name": "Robin", "role": "child"}, {"name": "Sam", "role": "adult"},
               {"name": "Alex", "role": "adult"}, {"name": "Casey", "role": "child"}]
    accounts = {"source_dir": "src", "accounts": {"demo_checking_0000": {**_CHECKING, "owner": "Both"}}}
    cfg = _config(tmp_path, members=members, joint="Both", accounts=accounts, category_rules={"taxonomy": _TAXONOMY})
    assert cfg.adults == ("Sam", "Alex")
    assert cfg.children == ("Robin", "Casey")
    assert cfg.joint_label == "Both"
    assert cfg.owners == ("Sam", "Alex", "Both", "Robin", "Casey")
    assert _exported_assumptions(tmp_path, cfg)["owners"] == ["Sam", "Alex", "Both", "Robin", "Casey"]


def test_people_export_keeps_owner_and_kid_roles_and_optional_birth_years(tmp_path):
    members = [{"name": "Alex", "role": "adult", "birth_year": 1990}, {"name": "Sam", "role": "adult"},
               {"name": "Robin", "role": "child", "birth_year": 2030}]
    hh = _exported_assumptions(tmp_path, _config(tmp_path, members=members))["household"]
    assert hh["people"] == [{"name": "Alex", "role": "owner", "birth_year": 1990},
                            {"name": "Sam", "role": "owner"},
                            {"name": "Robin", "role": "kid", "birth_year": 2030}]
    # the rest of assumptions.yaml `household:` passes through beside them, plus the derived margin account
    assert hh["margin_apr"] == 0.05 and hh["retirement_accounts"] == _RETIREMENT
    assert hh["margin_account"] is None
    assert list(hh) == ["name", "timezone", "people", "margin_apr", "retirement_accounts", "margin_account"]


def test_the_household_name_zone_and_modules_are_exported(tmp_path):
    """assumptions.json says who the household is — its display name (Config.household_name) and its zone — and the
    optional modules household.yaml switches, exactly as it states them: a module it leaves out is not listed, so the
    dashboard can tell "switched off" from "not said"."""
    cfg = _config(tmp_path / "a", name="The Samples", surname="Sample", timezone="Europe/Paris",
                  modules={"home_buying": False})
    assert cfg.modules == {"home_buying": False}
    a = _exported_assumptions(tmp_path / "a", cfg)
    assert (a["household"]["name"], a["household"]["timezone"]) == ("The Samples", "Europe/Paris")
    assert a["modules"] == {"home_buying": False}
    plain = _exported_assumptions(tmp_path / "b", _config(tmp_path / "b", surname="Sample"))
    assert (plain["household"]["name"], plain["household"]["timezone"], plain["modules"]) == ("Sample", TIMEZONE, {})
    assert _config(tmp_path / "c", modules=None).modules == {}                   # written with no value: none stated
    assert _config(tmp_path / "d", modules={"home_buying": True}).modules == {"home_buying": True}


def test_an_unknown_module_id_is_refused_with_the_known_ids(tmp_path):
    lines = _refused(tmp_path, modules={"budgets": True, "home_buying": True}).splitlines()
    assert "household.yaml: modules.budgets: unknown module 'budgets' — one of: home_buying" in lines
    assert not any(line.startswith("household.yaml: modules.home_buying") for line in lines)


@pytest.mark.parametrize("hh,msg", [
    ({"household": False}, "household.yaml: not found in "),
    ({"members": None}, "household.yaml: members: missing"),
    ({"joint": None}, "household.yaml: joint_label: missing"),
    ({"timezone": None}, "household.yaml: timezone: missing"),
])
def test_household_yaml_is_required(tmp_path, hh, msg):
    assert _refused(tmp_path, **hh).startswith(msg)


@pytest.mark.parametrize("members,msg", [
    ([], "household.yaml: members: no adult"),
    ([{"name": "Robin", "role": "child"}], "household.yaml: members: no adult"),
])
def test_a_household_needs_an_adult(tmp_path, members, msg):
    assert _refused(tmp_path, members=members).startswith(msg)


@pytest.mark.parametrize("hh,msg", [
    ({"adults": ("Alex", "Sam"), "children": ("alex",)}, "household.yaml: members[2].name: 'alex' is listed twice"),
    ({"adults": ("Alex", "Sam"), "joint": "Sam"}, "household.yaml: joint_label: 'Sam' is also a member's name"),
    ({"adults": ("Alex", "Sam"), "joint": "SAM"}, "household.yaml: joint_label: 'SAM' is also a member's name"),
])
def test_duplicate_or_colliding_names_are_refused(tmp_path, hh, msg):
    assert _refused(tmp_path, **hh).startswith(msg)


def _with(doc: dict, path: tuple, value) -> dict:
    """A copy of `doc` with the value at `path` (keys and list indexes) replaced."""
    doc = json.loads(json.dumps(doc))
    node = doc
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    return doc


_BASE = {
    "accounts": {"source_dir": "src", "accounts": {"demo_checking_0000": _CHECKING}, "manual_entries": [_HOUSE]},
    "assumptions": {"household": {"margin_apr": 0.05, "retirement_accounts": _RETIREMENT}},
    "category_rules": {"taxonomy": _TAXONOMY, "income_rules": [_INCOME_RULE], "keyword_rules": [_OWNER_RULE]},
    "overrides": [_OVERRIDE],
}


@pytest.mark.parametrize("file,path,prefix", [
    ("accounts", ("accounts", "demo_checking_0000", "owner"),
     "accounts.yaml: accounts.demo_checking_0000.owner: unknown owner 'Taylor' — one of: Alex, Sam, Joint"),
    ("accounts", ("manual_entries", 0, "owner"),
     "accounts.yaml: manual_entries[0].owner: unknown owner 'Taylor' — one of: Alex, Sam, Joint"),
    ("category_rules", ("income_rules", 0, "earner"),
     "category_rules.yaml: income_rules[0].earner: unknown earner 'Taylor' — one of: Alex, Sam"),
    ("overrides", (0, "set", "earner"),
     "overrides.yaml: [0].set.earner: unknown earner 'Taylor' — one of: Alex, Sam"),
    ("assumptions", ("household", "retirement_accounts", "Sample Checking"),
     "assumptions.yaml: household.retirement_accounts.Sample Checking: unknown owner 'Taylor' — one of: Alex, Sam, "
     "Joint"),
    ("category_rules", ("keyword_rules", 0, "all", 0, "value"),
     "category_rules.yaml: keyword_rules[0]: unknown owner 'Taylor'"),
])
def test_every_owner_reference_must_be_a_household_owner(tmp_path, file, path, prefix):
    # the untouched references all name owners, so the base loads; one name outside the household fails the load
    _config(tmp_path / "ok", **{k: json.loads(json.dumps(v)) for k, v in _BASE.items()})
    edited = {**_BASE, file: _with(_BASE[file], path, "Taylor")}
    msg = _refused(tmp_path / "bad", **edited)
    assert msg.startswith(prefix), msg


@pytest.mark.parametrize("surname,explicit,tokens", [
    ("Sample", None, ["ALEX", "SAM", "SAMPLE"]),
    (None, None, ["ALEX", "SAM"]),
    ("Sample", ["sample", "Alex"], ["SAMPLE", "ALEX"]),     # an explicit list wins, upper-cased
])
def test_own_name_tokens_come_from_adults_and_surname(tmp_path, surname, explicit, tokens):
    accounts = {"source_dir": "src", "accounts": {"demo_checking_0000": _CHECKING},
                "household": {"own_name_tokens": explicit} if explicit else {}}
    cfg = _config(tmp_path, accounts=accounts, children=("Robin",), surname=surname)
    hh = cfg.household
    assert hh["own_name_tokens"] == tokens        # never a child's name or the joint label
    assert hh["owners"] == ["Alex", "Sam"] and hh["surname"] == surname


def test_include_credit_cards_defaults_on(tmp_path):
    assert _config(tmp_path / "a").net_worth_options == {"include_credit_cards": True}
    assert _config(tmp_path / "b", net_worth={}).net_worth_options == {"include_credit_cards": True}
    off = _config(tmp_path / "c", net_worth={"include_credit_cards": False})
    assert off.net_worth_options == {"include_credit_cards": False}


def test_zone_and_display_name_come_from_household_yaml(tmp_path):
    cfg = _config(tmp_path / "a", timezone="Europe/Paris", surname="Sample", name="The Samples")
    assert cfg.timezone == cfg.simplefin_tz == "Europe/Paris"
    assert (cfg.household_name, cfg.surname) == ("The Samples", "Sample")
    assert _config(tmp_path / "b", surname="Sample").household_name == "Sample"    # no name: the surname
    assert _config(tmp_path / "c").household_name == "Household"                   # neither


@pytest.mark.parametrize("hh,msg", [
    ({"version": 2}, "household.yaml: version: unsupported version 2"),
    ({"name": 5}, "household.yaml: name: expected text"),
    ({"surname": ""}, "household.yaml: surname: expected text"),
    ({"timezone": ["Europe/Paris"]}, "household.yaml: timezone: expected an IANA zone name"),
    ({"joint": 5}, "household.yaml: joint_label: expected text"),
    ({"modules": []}, "household.yaml: modules: expected a mapping"),
    ({"net_worth": True}, "household.yaml: net_worth: expected a mapping"),
    ({"net_worth": {"include_credit_cards": "yes"}}, "household.yaml: net_worth.include_credit_cards: expected true or false"),
    ({"members": {"Alex": "adult"}}, "household.yaml: members: expected a list"),
    ({"members": ["Alex"]}, "household.yaml: members[0]: expected a mapping"),
    ({"members": [{"role": "adult"}]}, "household.yaml: members[0].name: missing"),
    ({"members": [{"name": "Alex", "role": "parent"}]}, "household.yaml: members[0].role: unknown role 'parent' — one of: adult, child"),
    ({"members": [{"name": "Alex", "role": "adult", "birth_year": "1990"}]}, "household.yaml: members[0].birth_year: expected a year"),
])
def test_a_malformed_household_yaml_is_refused(tmp_path, hh, msg):
    # every problem is reported, one per line: a member that is no adult also leaves the household without one
    assert any(line.startswith(msg) for line in _refused(tmp_path, **hh).splitlines())


def test_a_household_yaml_that_is_not_a_mapping_is_refused(tmp_path):
    d = _config_dir(tmp_path)
    (d / "household.yaml").write_text("- Alex\n- Sam\n", "utf-8")
    with pytest.raises(ValueError, match=re.escape("household.yaml: top level: expected a mapping")):
        Config(d, source_dir=str(tmp_path / "src"))

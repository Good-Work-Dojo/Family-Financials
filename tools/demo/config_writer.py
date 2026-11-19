# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The demo household's `config/`: household.yaml from the household (and the modules its profile switches on, and
its AI block);
accounts.yaml from the profile's accounts (each with the asset class the household counts it under) with the plan's
additions merged over it (an account's own keys, such as a loan's terms, and the manual entries); and each starter
rule file with the plan's additions merged over it — a mapping key by key, a list with the plan's entries first (a
household's own entries come before the starter's, so a catch-all stays last). overrides.yaml is the plan's list,
empty when it adds none. The assets the household entered in the app are no config: sources.py writes them."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from . import household as hh
from . import plan as plan_mod
from .profiles import Profile

STARTER = Path(__file__).resolve().parents[2] / "starter"
FILES = ("household.yaml", "accounts.yaml", "assumptions.yaml", "category_rules.yaml", "vendor_rules.yaml",
         "transfer_rules.yaml", "bank_category_map.yaml", "overrides.yaml")


def merge(base: Any, add: Any) -> Any:
    """`add` over `base`: mappings key by key (base's order, then add's new keys), lists add's entries first, any
    other value add's."""
    if isinstance(base, dict) and isinstance(add, dict):
        out = dict(base)
        for key, value in add.items():
            out[key] = merge(base[key], value) if key in base else value
        return out
    if isinstance(base, list) and isinstance(add, list):
        return [*add, *base]
    return add


def household_doc(profile: Profile) -> dict:
    """household.yaml: the household, `modules` (each of the profile's switched on) only when it has one, and `ai`
    (the provider and model) only when the profile states one."""
    doc = {"version": 1, "name": hh.NAME, "surname": hh.SURNAME,
           "members": [{"name": m.name, "role": m.role, "birth_year": m.birth_year} for m in hh.MEMBERS],
           "joint_label": hh.JOINT, "timezone": hh.TIMEZONE}
    if profile.modules:
        doc["modules"] = {module: True for module in profile.modules}
    if profile.ai:
        doc["ai"] = dict(profile.ai)
    return doc


def accounts_doc(plan: plan_mod.Plan) -> dict:
    accounts = {}
    for source in plan.profile.accounts:
        a = hh.ACCOUNTS[source.account]
        entry: dict[str, Any] = {"name": a.name, "institution": a.institution, "owner": a.owner, "kind": a.kind,
                                 "asset_class": a.asset_class}
        if source.files is not None:
            entry |= {"parser": source.files.parser, "glob": f"{a.id}-*.csv"}
        if source.feed:
            entry["simplefin_id"] = a.feed_id
        cutover = plan.cutover(a.id)
        if cutover is not None:
            entry["simplefin_cutover"] = cutover.isoformat()
        accounts[a.id] = entry
    return {"version": 1, "accounts": accounts}


def assumptions(plan: plan_mod.Plan) -> dict[str, Any]:
    """The generated assumptions.yaml as the plan stands: the starter's, with the plan's additions merged over it."""
    with open(STARTER / "assumptions.yaml", encoding="utf-8") as f:
        return merge(yaml.safe_load(f), plan.config.get("assumptions.yaml", {}))


def documents(plan: plan_mod.Plan) -> dict[str, Any]:
    """Every config file's document, by file name, in FILES order."""
    accounts_add = plan.config.get("accounts.yaml", {})
    strangers = sorted(set(accounts_add.get("accounts", {})) - {s.account for s in plan.profile.accounts})
    if strangers:
        raise ValueError(f"accounts.yaml: the plan adds keys to accounts of no profile: {', '.join(strangers)}")
    docs: dict[str, Any] = {"household.yaml": household_doc(plan.profile),
                            "accounts.yaml": merge(accounts_doc(plan), accounts_add)}
    for name in FILES[2:-1]:
        with open(STARTER / name, encoding="utf-8") as f:
            docs[name] = merge(yaml.safe_load(f), plan.config.get(name, {}))
    docs["overrides.yaml"] = list(plan.config.get("overrides.yaml", []))
    unknown = sorted(set(plan.config) - set(FILES))
    if unknown:
        raise ValueError(f"the plan adds to files that are no config file: {', '.join(unknown)}")
    return docs


def render(plan: plan_mod.Plan) -> dict[str, str]:
    """Every config file's text: a one-line header, then its document."""
    header = f"# Generated demo household — {plan.profile.name}. Edit freely.\n"
    return {name: header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
            for name, doc in documents(plan).items()}


def write_plan(plan: plan_mod.Plan, config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    for name, text in render(plan).items():
        (config_dir / name).write_text(text, encoding="utf-8", newline="")


def write(profile: Profile, end: date, config_dir: Path) -> None:
    """The config of `profile` ending on `end` written into `config_dir` (made if absent) and nothing else."""
    write_plan(plan_mod.build(profile, end), config_dir)

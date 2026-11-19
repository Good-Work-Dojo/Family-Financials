# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The demo config as the tests read it (conftest's DEMO_CONFIG_DIR, load_demo, demo_copy, demo_config): the `full`
profile's config, written once per run outside the repository, that every test needing a valid config loads."""
from __future__ import annotations

import yaml
from conftest import DEMO_CONFIG_DIR, ROOT, demo_config, demo_copy, load_demo

from etl.config import Config
from etl.validate import validate
from tools.demo import config_writer, profiles
from tools.demo import household as hh


def _files(d) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(d.iterdir())}


def test_the_demo_config_validates():
    assert validate(DEMO_CONFIG_DIR) == []
    cfg = demo_config()
    assert cfg.dir == DEMO_CONFIG_DIR
    assert set(cfg.files) == set(config_writer.FILES)
    assert set(cfg.accounts) == set(hh.ACCOUNTS)
    assert cfg.adults == (hh.ALEX, hh.SAM) and cfg.children == (hh.ROBIN, hh.JAMIE)
    assert load_demo("household.yaml") == config_writer.household_doc(profiles.FULL)
    # written outside the repository, and every call loads it anew
    assert ROOT not in DEMO_CONFIG_DIR.parents
    assert demo_config() is not cfg


def test_load_demo_returns_a_fresh_copy():
    first = load_demo("vendor_rules.yaml")
    assert first["patterns"]
    first["patterns"].clear()
    first["acronyms"].append("ZZQ")
    again = load_demo("vendor_rules.yaml")
    assert again is not first
    assert again == yaml.safe_load((DEMO_CONFIG_DIR / "vendor_rules.yaml").read_text("utf-8"))
    assert again["patterns"] and "ZZQ" not in again["acronyms"]


def test_demo_copy_applies_edits_to_a_copy_only(tmp_path):
    before = _files(DEMO_CONFIG_DIR)
    d = demo_copy(tmp_path, {"vendor_rules.yaml": lambda doc: doc["acronyms"].append("ZZQ")})
    assert d == tmp_path / "config"
    assert "ZZQ" in yaml.safe_load((d / "vendor_rules.yaml").read_text("utf-8"))["acronyms"]
    copied = _files(d)
    assert set(copied) == set(before)
    assert all(copied[name] == data for name, data in before.items() if name != "vendor_rules.yaml")
    assert "ZZQ" in Config(d).vendor_rules["acronyms"]
    # the original is untouched
    assert _files(DEMO_CONFIG_DIR) == before
    assert "ZZQ" not in load_demo("vendor_rules.yaml")["acronyms"]
    assert demo_copy(tmp_path / "plain") and _files(tmp_path / "plain" / "config") == before

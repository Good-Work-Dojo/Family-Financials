# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""A config directory holding only accounts.yaml and household.yaml loads, and parse_assets tolerates absent asset
blocks."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from conftest import isolated_pipeline, write_household

from etl.config import Config
from etl.models import BalanceObs
from etl.validate import ConfigError

_CHECKING = {"name": "Sample Checking", "institution": "Sample Bank",
             "owner": "Alex", "kind": "checking"}
_MORTGAGE = {"name": "Sample Mortgage", "institution": "Sample Lender",
             "owner": "Alex", "kind": "loan", "mortgage": True}


def _write(d: Path, accounts: dict) -> Path:
    d.mkdir(exist_ok=True)
    (d / "accounts.yaml").write_text(yaml.safe_dump(accounts))
    write_household(d)
    return d


def _config(tmp_path: Path, **extra) -> Config:
    accounts = {"source_dir": "src", "accounts": {"demo_checking_0000": _CHECKING}, **extra}
    d = _write(tmp_path / "cfg", accounts)
    (tmp_path / "src").mkdir(exist_ok=True)
    return Config(d, source_dir=str(tmp_path / "src"))


def test_accounts_and_household_yaml_are_the_only_required_files(tmp_path):
    cfg = _config(tmp_path)
    for name in ("assumptions", "transfer_rules", "category_rules", "bank_category_map"):
        assert getattr(cfg, name) == {}
    assert cfg._load("vendor_rules.yaml") == {}          # Config.vendor_rules itself gains a normalized learning block
    (tmp_path / "cfg" / "vendor_rules.yaml").write_text("")
    assert cfg._load("vendor_rules.yaml") == {}
    (tmp_path / "cfg" / "accounts.yaml").unlink()
    with pytest.raises(FileNotFoundError, match="accounts.yaml"):
        cfg._load("accounts.yaml", required=True)
    with pytest.raises(ConfigError, match="accounts.yaml: not found"):        # a problem like any other
        Config(tmp_path / "cfg", source_dir=str(tmp_path / "src"))


def test_parse_assets_without_any_asset_block_is_silent(tmp_path):
    p = isolated_pipeline(tmp_path, [], cfg=_config(tmp_path), windows={})
    p.parse_assets()
    assert p.checks == []      # no asset source to miss, so no check of any kind


def test_the_mortgage_row_takes_the_flagged_accounts_identity_and_none_without_one(tmp_path):
    p = isolated_pipeline(tmp_path, [], cfg=_config(tmp_path), windows={})
    assert p.cfg.mortgage_account_id() is None
    p.balance_obs = [BalanceObs("demo_checking_0000", "2030-01-10", 100_00, source="csv")]
    p.parse_assets()
    assert [r.asset_class for r in p.asset_rows] == ["Cash"] and p.mortgage_included == {"2030-01-10": True}
    cfg = _config(tmp_path, accounts={"demo_checking_0000": _CHECKING, "demo_mortgage_0000": _MORTGAGE})
    p = isolated_pipeline(tmp_path, [], cfg=cfg, windows={})
    p.balance_obs = [BalanceObs("demo_mortgage_0000", "2030-01-10", -100_000_00)]
    p.parse_assets()
    (row,) = p.asset_rows
    assert (row.account_id, row.account, row.institution, row.owner, row.asset_class) == (
        "demo_mortgage_0000", "Sample Mortgage", "Sample Lender", "Alex", "Mortgage")

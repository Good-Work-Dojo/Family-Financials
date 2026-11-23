# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The collecting validator (etl/validate.py, Config): every problem a config has, in one run, each naming its file, the
key path in it, what is wrong and how to fix it. One synthetic config that uses every documented key validates clean;
each case breaks one thing and gets exactly its problem; every compiler reports every bad entry of its list; a block or
a nested value of the wrong type, or a null one, is a problem or reads as absent, never a crash; `python -m etl
validate`, `ingest` and `rules` report the same lines; the bank-feed commands check only what a fetch reads;
docs/configuration.md lists exactly the keys the validators know, and its examples validate. Invented household, ids
and 2030 dates only."""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import make_crontab  # tests/conftest.py puts deploy/ on sys.path
import pytest
import yaml
from conftest import ROOT, isolate, isolated_pipeline, make_txn, write_household

from etl import manual_assets as ma_mod
from etl import networth as nw_mod
from etl import pipeline as pipeline_mod
from etl.__main__ import main
from etl.config import Config, FeedConfig, Paths
from etl.enrich import vendor_registry
from etl.enrich.categorize import Categorizer
from etl.enrich.income import IncomeIdentifier
from etl.enrich.transfers import TransferEngine, compile_transfer_rules
from etl.exports import PROJECTION_DEFAULTS
from etl.models import (
    FALLBACK_CATEGORY,
    INCOME_CATEGORY,
    MODULE_IDS,
    P2P_UNKNOWN_SUB,
    RESERVED_TAXONOMY,
    FileStats,
    Txn,
)
from etl.pipeline import Pipeline
from etl.validate import (
    KEYS,
    VERSION,
    ConfigError,
    Problem,
    Problems,
    children,
    unknown_key_problems,
    validate,
)

# the statement format the synthetic accounts are read with (registered with the pipeline where a command needs it)
PARSERS = ("sample_format",)

VALID = {
    "household.yaml": {
        "version": 1, "name": "The Samples", "surname": "Sample",
        "members": [{"name": "Alex", "role": "adult", "birth_year": 1990}, {"name": "Sam", "role": "adult"},
                    {"name": "Robin", "role": "child", "birth_year": 2030}],
        "joint_label": "Joint", "timezone": "America/Denver", "modules": {"home_buying": True},
        "net_worth": {"include_credit_cards": True}, "refresh": {"hours": [6, 18], "minute": 30},
        "ai": {"provider": "openai", "model": "sample-model", "base_url": "https://llm.example.net/v1",
               "accounts": "all", "never_send": ["Biscuit", "Elmwood"], "min_seen": 2, "confidence": 0.8,
               "caps": {"vendors_per_run": 50, "runs_per_day": 4}},
    },
    "accounts.yaml": {
        "version": 1, "source_dir": "statements", "skip_files": ["*-old.csv"],
        "simplefin": {"store_dir": "store", "ignore": ["SF-DEMO-IGNORED"]},
        "household": {"own_name_tokens": ["ALEX", "SAM", "SAMPLE"]},
        "accounts": {
            "demo_checking_0000": {"name": "Sample Checking", "institution": "Sample Bank", "owner": "Joint",
                                   "kind": "checking", "parser": "sample_format", "glob": "demo_checking_0000-*.csv",
                                   "simplefin_id": "SF-DEMO-C0", "simplefin_cutover": "2030-01-01",
                                   "tags": ["Household"]},
            "demo_card_0000": {"name": "Sample Card", "institution": "Sample Bank", "owner": "Alex",
                               "kind": "credit-card", "credit_limit": 5000, "simplefin_id": "SF-DEMO-K0",
                               "sources": [{"format": "sample_format", "glob": "demo_card_0000-*.csv"},
                                           {"format": "sample_format", "glob": "demo_card_0000-*.ofx",
                                            "since": "2029-07-01"}],
                               "simplefin_cutover": "2030-02-01"},
            "demo_brokerage_0000": {"name": "Sample Brokerage", "institution": "Sample Broker", "owner": "Sam",
                                    "kind": "brokerage", "asset_class": "Stocks/ETF", "margin": True,
                                    "simplefin_id": "SF-DEMO-B0", "simplefin_id_history": ["SF-DEMO-B0-OLD"],
                                    "simplefin_balances_only": True},
            "demo_retirement_0000": {"name": "Sample Retirement", "institution": "Sample Broker", "owner": "Alex",
                                     "kind": "brokerage", "asset_class": "Retirement",
                                     "simplefin_id": "SF-DEMO-R0"},
            "demo_mortgage_0000": {"name": "Sample Mortgage", "institution": "Sample Lender", "owner": "Joint",
                                   "kind": "loan", "mortgage": True, "simplefin_id": "SF-DEMO-M0",
                                   "note": "an invented note",
                                   "loan": {"rate": 6.5, "monthly_payment": 2000, "monthly_escrow": 400,
                                            "escrow_balance": 1200, "term_months": 360,
                                            "origination": {"date": "2030-01-15", "amount": 250000}}},
        },
        "manual_entries": [{"account": "Sample House", "institution": "Sample", "owner": "Joint",
                            "asset_class": "House", "value": 350000, "as_of": "2030-01-31", "liquid": False,
                            "acquired": "2029-06-01", "note": "an invented estimate"}],
    },
    "assumptions.yaml": {
        "version": 1, "match_window_bdays": 3, "venmo_match_window_bdays": 5, "venmo_review_threshold": 200,
        "uncategorized_review_threshold": 250, "unknown_credit_review_threshold": 500, "generic_min_amount": 20,
        "coverage": {"min_share": 0.6},
        "cancelled_subscriptions": [{"vendor": "Sample Streaming", "recorded_on": "2030-01-20", "permanent": True,
                                     "note": "an invented cancellation"}],
        "household": {"margin_apr": 0.05, "retirement_accounts": {"Sample Retirement": "Alex"}},
        "projections": {"return_scenarios_pct": [4, 6, 8], "inflation_pct": 2.5, "withdrawal_rate_pct": 4,
                        "ltcg_pct": 15, "state_income_tax_pct": 0},
        "targets": {"Cash": 10, "Stocks/ETF": 60, "Retirement": 30},
        "budgets": {"Dining": 400, "Eating Out": 50},             # a category a rename moves still has its budget
    },
    "category_rules.yaml": {
        "version": 1,
        "taxonomy": {**{c: list(subs) for c, subs in RESERVED_TAXONOMY.items()},
                     INCOME_CATEGORY: [*RESERVED_TAXONOMY[INCOME_CATEGORY], "Salary"],
                     "Dining": ["Restaurants", "Coffee"], "Groceries": []},
        "keyword_rules": [
            {"pattern": "^SAMPLE CAFE", "category": "Dining", "subcategory": "Coffee", "tags": ["Treats"],
             "needs_review": False, "name": "demo-cafe", "stage": "default", "note": "an invented rule"},
            {"name": "demo-big-grocer", "stage": "pre",
             "all": [{"field": "vendor", "op": "is", "value": "Sample Grocer"},
                     {"field": "amount", "op": "lt", "value": -300},
                     {"field": "owner", "op": "oneOf", "value": ["Alex", "Joint"]},
                     {"field": "account", "op": "is", "value": "demo_checking_0000"}],
             "set": {"category": "Dining", "subcategory": "Restaurants", "tags": ["Bulk"], "needs_review": True}},
            {"name": "demo-sample-shop",
             "any": [{"field": "description", "op": "contains", "value": "SAMPLE SHOP"},
                     {"field": "bank_category", "op": "is", "value": "Shopping"},
                     {"field": "type", "op": "is", "value": "purchase"}, {"field": "amount", "op": "outflow"}],
             "set": {"category": "Groceries"}},
            # the person-to-person catch-all: the last rule of the default stage
            {"pattern": "\\bSAMPLEPAY\\b", "category": FALLBACK_CATEGORY, "subcategory": P2P_UNKNOWN_SUB,
             "needs_review": True},
        ],
        "income_rules": [{"pattern": "^SAMPLE EMPLOYER PAYROLL", "accounts": ["demo_checking_0000"],
                          "type": "income", "subcategory": "Salary", "earner": "Alex", "employer": "Sample Employer",
                          "note": "an invented rule"}],
        "renames": [{"from": {"category": "Eating Out", "subcategory": "Cafes"},
                     "to": {"category": "Dining", "subcategory": "Coffee"}}],
    },
    "vendor_rules.yaml": {
        "version": 1, "strip_prefixes": ["^SQ \\*"], "cleanup": ["#\\d+"], "acronyms": ["LLC"],
        "patterns": [
            {"pattern": "SAMPLE CAFE", "vendor": "Sample Cafe", "category": "Dining", "subcategory": "Coffee",
             "tags": ["Treats"], "learn": True, "note": "an invented pattern"},
            {"pattern": "SAMPLE GROCER", "vendor": "Sample Grocer",
             "split": [{"pct": 60, "category": "Groceries"}, {"amount": 5, "category": "Dining", "subcategory": "Coffee"},
                       {"remainder": True, "category": "Dining", "subcategory": "Restaurants"}]},
        ],
        "learning": {"exclude": ["Sample Grocer"]},
        "renames": [{"from": "Sample Old Cafe", "to": "Sample Cafe"}],
    },
    "transfer_rules.yaml": {
        "version": 1,
        "rules": [{"name": "demo-card-payment", "pattern": "^SAMPLE CARD PAYMENT", "type": "cc-payment",
                   "kinds": ["checking"], "sign": "debit", "bank_type": "ACH", "counter_hint": "demo_cards",
                   "needs_review": False, "open_question": "Which card does it pay?", "note": "an invented rule"},
                  {"name": "demo-move", "pattern": "^SAMPLE MOVE TO (\\d{4})", "type": "transfer",
                   "accounts": ["demo_checking_0000"], "counter_hint": "from_text:demo_card_{1}"}],
        "groups": {"demo_cards": {"accounts": ["demo_card_0000"], "expect_counterpart": True}},
        "outside": ["demo_elsewhere"],
        "venmo_patterns": {"debit": "\\bSAMPLEPAY\\b", "credit": "\\bSAMPLEPAY CASHOUT\\b", "zelle": "\\bSAMPLEZELLE\\b",
                           "zelle_party": "SAMPLEZELLE (?:TO|FROM) ([A-Z ]+)", "zelle_bank_types": ["P2P_DEBIT"],
                           "cashout_income": [{"accounts": ["demo_checking_0000"], "subcategory": "Salary",
                                               "earner": "Sam"}], "pairing": "unique"},
        "generic_matching": True,
    },
    "bank_category_map.yaml": {
        "version": 1, "Food & Drink": {"category": "Dining", "subcategory": "Restaurants", "tags": ["Eating"]},
    },
    "overrides.yaml": [
        {"match": {"vendor": "Sample Cafe"}, "set": {"category": "Dining"}, "note": "an invented override"},
        # a category a rename moves reads as the renamed one
        {"match": {"uid": "tx_demo_0001", "account": "demo_checking_0000", "date": "2030-01-15", "amount": -12.5,
                   "contains": "SAMPLE"},
         "set": {"type": "purchase", "category": "Eating Out", "subcategory": "Cafes", "tags": ["Treats"],
                 "needs_review": False, "earner": "Alex"}},
    ],
}
FILES = tuple(VALID)


def write_config(tmp_path: Path, docs: dict | None = None) -> Path:
    """The synthetic config at tmp_path/config, in the legacy layout: the statements folder (tmp_path/statements)
    exists beside it."""
    d = tmp_path / "config"
    d.mkdir(parents=True)
    (tmp_path / "statements").mkdir()
    for name, doc in (docs or VALID).items():
        (d / name).write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), "utf-8")
    return d


DROP = object()     # an edit's value that deletes the key


def edited(edits: list[tuple[str, tuple, object]]) -> dict:
    """A deep copy of VALID with each (file, key path, value) edit applied; an empty path replaces the whole file."""
    docs = copy.deepcopy(VALID)
    for file, path, value in edits:
        if not path:
            docs[file] = value
            continue
        node = docs[file]
        for k in path[:-1]:
            node = node[k]
        if value is DROP:
            del node[path[-1]]
        else:
            node[path[-1]] = value
    return docs


def problems_of(tmp_path: Path, edits: list[tuple[str, tuple, object]]) -> list[Problem]:
    return validate(write_config(tmp_path, edited(edits)), parser_names=PARSERS)


# ------------------------------------------------------------------------------------------------ the valid config

def test_the_valid_config_yields_no_problem(tmp_path):
    assert validate(write_config(tmp_path), parser_names=PARSERS) == []


def test_the_valid_config_uses_every_key_the_validators_know():
    """So a check that fires on a correct value of any documented key fails test_the_valid_config_yields_no_problem."""
    assert [(k.file, k.path) for k in KEYS if _place(VALID[k.file], _segments(k.path), strict=True) is None] == []


# ------------------------------------------------------------------------------------------------ one case each

@dataclass(frozen=True)
class Case:
    id: str
    edits: list
    expected: list          # (file, key path, a fragment of `message — fix`), in the collector's order


H, A = "household.yaml", "accounts.yaml"
AS, CR, VR, TR, BM, OV = ("assumptions.yaml", "category_rules.yaml", "vendor_rules.yaml", "transfer_rules.yaml",
                          "bank_category_map.yaml", "overrides.yaml")
CHK, CARD, BRK, MTG = "demo_checking_0000", "demo_card_0000", "demo_brokerage_0000", "demo_mortgage_0000"
SOURCES = ("accounts", CARD, "sources")             # the card's statement files: two sources
LOAN_0001 = {"name": "Sample Loan", "institution": "Sample Lender", "owner": "Alex", "kind": "loan", "mortgage": True}
# the line that says what a household.yaml with no usable roster leaves unchecked
NOT_CHECKED = ("owner and earner references in the other files are not checked until this is fixed — give "
               "household.yaml at least one adult and a joint label no member has")
NAME_FIX = "give each account its own name (the dashboard tells accounts apart by it)"
ENTRY_NAME_FIX = "rename the entry or the account (the dashboard tells accounts apart by name)"
QUOTE_FIX = "put it in quotes, e.g. \"{}\": YAML reads an unquoted number as a number, not as text"

CASES = [
    # household.yaml
    Case("household-version", [(H, ("version",), 2)], [(H, "version", "unsupported version 2")]),
    Case("household-unknown-key", [(H, ("timezon",), "Europe/Paris")], [(H, "timezon", "did you mean 'timezone'?")]),
    Case("member-unknown-key", [(H, ("members", 0, "nmae"), "Al")], [(H, "members[0].nmae", "did you mean 'name'?")]),
    Case("net-worth-unknown-key", [(H, ("net_worth", "include_credit_card"), False)],
         [(H, "net_worth.include_credit_card", "did you mean 'include_credit_cards'?")]),
    Case("member-name-untrimmed", [(H, ("members", 0, "name"), " Alex")], [(H, "members[0].name", "spaces")]),
    # a member renamed to a second "alex" leaves no member Sam: the keys naming Sam are reported
    Case("member-name-twice", [(H, ("members", 1, "name"), "alex")],
         [(A, f"accounts.{BRK}.owner", "unknown owner 'Sam' — one of: Alex, Joint, Robin"),
          (H, "members[1].name", "listed twice — give each member a distinct name (until it is fixed, owner and "
                                 "earner keys naming 'alex' are not checked)"),
          ("transfer_rules.yaml", "venmo_patterns.cashout_income[0].earner",
           "unknown earner 'Sam' — one of: Alex, Robin")]),
    Case("joint-label-is-a-member", [(H, ("joint_label",), "Sam")],
         [(H, "joint_label", "also a member's name"), (H, "members", NOT_CHECKED)]),
    Case("member-role", [(H, ("members", 2, "role"), "kid")], [(H, "members[2].role", "unknown role 'kid'")]),
    Case("member-birth-year", [(H, ("members", 0, "birth_year"), 1850)], [(H, "members[0].birth_year", "a year")]),
    Case("no-adult", [(H, ("members",), [{"name": "Robin", "role": "child"}])],
         [(H, "members", "no adult"), (H, "members", NOT_CHECKED)]),
    Case("joint-label-missing", [(H, ("joint_label",), DROP)],
         [(H, "joint_label", "missing"), (H, "members", NOT_CHECKED)]),
    Case("timezone-localtime", [(H, ("timezone",), "localtime")],
         [(H, "timezone", "expected an IANA zone name such as America/New_York — found 'localtime'")]),
    Case("refresh-not-a-mapping", [(H, ("refresh",), [1, 9])], [(H, "refresh", "expected a mapping")]),
    Case("refresh-unknown-key", [(H, ("refresh", "hour"), [9])], [(H, "refresh.hour", "did you mean 'hours'?")]),
    Case("refresh-hours-not-a-list", [(H, ("refresh", "hours"), 9)],
         [(H, "refresh.hours", "expected a list of hours — found a number")]),
    Case("refresh-no-hours", [(H, ("refresh", "hours"), [])], [(H, "refresh.hours", "0 hours listed — list one to 6")]),
    Case("refresh-seven-hours", [(H, ("refresh", "hours"), [0, 3, 6, 9, 12, 15, 18])],
         [(H, "refresh.hours", "7 hours listed — list one to 6 hours: the bank feed allows about 24 requests a day")]),
    Case("refresh-hour-out-of-range", [(H, ("refresh", "hours"), [6, 24, -1, True, "9"])],
         [(H, "refresh.hours[1]", "expected a whole hour from 0 to 23 — found a number"),
          (H, "refresh.hours[2]", "expected a whole hour from 0 to 23"),
          (H, "refresh.hours[3]", "found true or false"), (H, "refresh.hours[4]", "found '9'")]),
    Case("refresh-hour-twice", [(H, ("refresh", "hours"), [6, 18, 6])],
         [(H, "refresh.hours[2]", "6 is listed twice — list each hour once")]),
    Case("refresh-minute", [(H, ("refresh", "minute"), 60)],
         [(H, "refresh.minute", "expected a whole minute from 0 to 59 — found a number")]),
    Case("refresh-minute-not-whole", [(H, ("refresh", "minute"), 7.5)], [(H, "refresh.minute", "from 0 to 59")]),
    Case("timezone-not-text", [(H, ("timezone",), 5)], [(H, "timezone", "— found a number")]),
    Case("timezone-unknown", [(H, ("timezone",), "Mars/Olympus")], [(H, "timezone", "unknown zone 'Mars/Olympus'")]),
    Case("timezone-missing", [(H, ("timezone",), DROP)], [(H, "timezone", "missing")]),
    Case("module-not-a-switch", [(H, ("modules", "home_buying"), "yes")],
         [(H, "modules.home_buying", "true or false")]),
    Case("module-unknown", [(H, ("modules", "budgets"), True)],
         [(H, "modules.budgets", "unknown module 'budgets' — one of: home_buying")]),
    Case("household-name-not-text", [(H, ("name",), 5)], [(H, "name", "expected text")]),
    # the AI step's settings
    Case("ai-not-a-mapping", [(H, ("ai",), "on")], [(H, "ai", "expected a mapping — write `ai: {provider: anthropic}`")]),
    Case("ai-unknown-key", [(H, ("ai", "modle"), "x")], [(H, "ai.modle", "did you mean 'model'?")]),
    Case("ai-caps-unknown-key", [(H, ("ai", "caps", "runs_per_week"), 4)],
         [(H, "ai.caps.runs_per_week", "did you mean 'runs_per_day'?")]),
    Case("ai-caps-not-a-mapping", [(H, ("ai", "caps"), 50)], [(H, "ai.caps", "expected a mapping")]),
    Case("ai-unknown-provider", [(H, ("ai", "provider"), "sample-cloud")],
         [(H, "ai.provider", "expected anthropic or openai — found 'sample-cloud'")]),
    Case("ai-openai-without-model", [(H, ("ai", "model"), DROP)],
         [(H, "ai.model", "missing — name the model the openai server serves: it has no default")]),
    Case("ai-model-with-spaces", [(H, ("ai", "model"), "sample model")], [(H, "ai.model", "without spaces")]),
    Case("ai-base-url-with-anthropic", [(H, ("ai", "provider"), "anthropic")],
         [(H, "ai.base_url", "the anthropic provider takes no address — remove it, or set `provider: openai`")]),
    Case("ai-base-url-plain-http", [(H, ("ai", "base_url"), "http://llm.example.net/v1")],
         [(H, "ai.base_url", "the address is neither https:// nor http:// to this computer")]),
    Case("ai-base-url-with-a-password", [(H, ("ai", "base_url"), "https://user:secret@llm.example.net/v1")],
         [(H, "ai.base_url", "the address holds a user name or password")]),
    Case("ai-base-url-not-an-address", [(H, ("ai", "base_url"), "llm.example.net")],
         [(H, "ai.base_url", "not an address — write an https:// address")]),
    Case("ai-accounts", [(H, ("ai", "accounts"), "checking")], [(H, "ai.accounts", "expected cards or all")]),
    Case("ai-never-send-not-a-list", [(H, ("ai", "never_send"), "Biscuit")],
         [(H, "ai.never_send", "expected a list of words — found 'Biscuit'")]),
    Case("ai-never-send-blank-word", [(H, ("ai", "never_send"), ["Biscuit", ""])], [(H, "ai.never_send", "a list")]),
    Case("ai-base-url-with-a-query", [(H, ("ai", "base_url"), "https://llm.example.net/v1?key=x")],
         [(H, "ai.base_url", "the address holds a query or a fragment")]),
    Case("ai-min-seen-zero", [(H, ("ai", "min_seen"), 0)], [(H, "ai.min_seen", "a whole number from 1 to 100")]),
    Case("ai-confidence-too-low", [(H, ("ai", "confidence"), 0.3)], [(H, "ai.confidence", "a number from 0.5 to 1")]),
    Case("ai-confidence-not-a-number", [(H, ("ai", "confidence"), "high")], [(H, "ai.confidence", "found 'high'")]),
    Case("ai-vendors-per-run", [(H, ("ai", "caps", "vendors_per_run"), 500)],
         [(H, "ai.caps.vendors_per_run", "a whole number from 1 to 200 — found a number; leave it out for 50")]),
    Case("ai-runs-per-day", [(H, ("ai", "caps", "runs_per_day"), 0)], [(H, "ai.caps.runs_per_day", "from 1 to 24")]),
    Case("ai-runs-per-day-not-whole", [(H, ("ai", "caps", "runs_per_day"), 2.5)],
         [(H, "ai.caps.runs_per_day", "a whole number")]),
    # accounts.yaml: the file and its blocks
    Case("accounts-version", [(A, ("version",), "1.0")], [(A, "version", "unsupported version '1.0'")]),
    Case("accounts-unknown-key", [(A, ("source_dirs",), "x")], [(A, "source_dirs", "did you mean 'source_dir'?")]),
    Case("accounts-missing", [(A, ("accounts",), DROP)], [(A, "accounts", "missing")]),
    Case("accounts-not-a-mapping", [(A, ("accounts",), ["demo"])], [(A, "accounts", "expected a mapping")]),
    Case("source-dir-not-text", [(A, ("source_dir",), 5)], [(A, "source_dir", "expected text")]),
    Case("statements-folder-missing", [(A, ("source_dir",), "elsewhere")],
         [(A, "source_dir", "does not exist — create it, correct source_dir")]),
    Case("skip-files-not-a-list", [(A, ("skip_files",), "*-old.csv")], [(A, "skip_files", "a list of text")]),
    Case("simplefin-unknown-key", [(A, ("simplefin", "ignored"), [])], [(A, "simplefin.ignored", "'ignore'")]),
    Case("simplefin-store-not-text", [(A, ("simplefin", "store_dir"), ["store"])],
         [(A, "simplefin.store_dir", "expected text")]),
    Case("household-block-unknown-key", [(A, ("household", "own_name_token"), ["SAMPLE"])],
         [(A, "household.own_name_token", "did you mean 'own_name_tokens'?")]),
    # accounts.yaml: one account
    Case("account-id", [(A, ("accounts", "demo checking"), {**VALID[A]["accounts"][CARD], "simplefin_id": "X",
                                                            "name": "Sample Card 2"})],
         [(A, "accounts.demo checking", "account ids use letters, digits, _ and - — rename it")]),
    Case("account-not-a-mapping", [(A, ("accounts", CARD), "Sample Card")],
         [(A, f"accounts.{CARD}", "expected a mapping")]),
    Case("account-unknown-key", [(A, ("accounts", CHK, "institutoin"), "x")],
         [(A, f"accounts.{CHK}.institutoin", "did you mean 'institution'?")]),
    Case("account-name-missing", [(A, ("accounts", CHK, "name"), DROP)], [(A, f"accounts.{CHK}.name", "missing")]),
    Case("account-institution-not-text", [(A, ("accounts", CHK, "institution"), 5)],
         [(A, f"accounts.{CHK}.institution", "expected text")]),
    Case("account-owner-missing", [(A, ("accounts", CHK, "owner"), DROP)], [(A, f"accounts.{CHK}.owner", "missing")]),
    Case("account-owner-unknown", [(A, ("accounts", CHK, "owner"), "Taylor")],
         [(A, f"accounts.{CHK}.owner", "unknown owner 'Taylor' — one of: Alex, Sam, Joint, Robin")]),
    Case("account-kind-missing", [(A, ("accounts", CHK, "kind"), DROP)], [(A, f"accounts.{CHK}.kind", "missing")]),
    Case("account-kind-unknown", [(A, ("accounts", CHK, "kind"), "piggy-bank")],
         [(A, f"accounts.{CHK}.kind", "unknown kind 'piggy-bank'")]),
    Case("account-class-unknown", [(A, ("accounts", BRK, "asset_class"), "Bonds")],
         [(A, f"accounts.{BRK}.asset_class", "unknown class 'Bonds'")]),
    Case("card-class-is-no-debt", [(A, ("accounts", CARD, "asset_class"), "Cash")],
         [(A, f"accounts.{CARD}.asset_class", "'Cash' does not fit a credit-card account — ")]),
    Case("checking-class-is-a-debt", [(A, ("accounts", CHK, "asset_class"), "Other Debt")],
         [(A, f"accounts.{CHK}.asset_class", "'Other Debt' does not fit a checking account")]),
    Case("mortgage-class-on-another-loan",
         [(A, ("accounts", MTG, "mortgage"), False), (A, ("accounts", MTG, "asset_class"), "Mortgage")],
         [(A, f"accounts.{MTG}.asset_class", "'Mortgage' does not fit a loan account — set mortgage: true")]),
    Case("margin-class-declared", [(A, ("accounts", BRK, "asset_class"), "Margin")],
         [(A, f"accounts.{BRK}.asset_class", "'Margin' does not fit a brokerage account")]),
    Case("parser-unknown", [(A, ("accounts", CHK, "parser"), "sample_formt")],
         [(A, f"accounts.{CHK}.parser", "unknown parser 'sample_formt' — one of sample_format")]),
    Case("glob-without-parser", [(A, ("accounts", CHK, "parser"), DROP)],
         [(A, f"accounts.{CHK}.parser", "missing, though the account has a glob")]),
    Case("parser-without-glob", [(A, ("accounts", CHK, "glob"), DROP)],
         [(A, f"accounts.{CHK}.glob", "missing, though the account has a parser")]),
    Case("feed-id-twice", [(A, ("accounts", CARD, "simplefin_id"), "SF-DEMO-C0")],
         [(A, f"accounts.{CARD}.simplefin_id", f"also at accounts.{CHK}.simplefin_id")]),
    Case("feed-id-in-a-history", [(A, ("accounts", BRK, "simplefin_id_history"), ["SF-DEMO-K0"])],
         [(A, f"accounts.{BRK}.simplefin_id_history[0]", f"also at accounts.{CARD}.simplefin_id")]),
    Case("feed-id-not-text", [(A, ("accounts", CARD, "simplefin_id"), 1234)],
         [(A, f"accounts.{CARD}.simplefin_id", "expected text")]),
    Case("cutover-not-a-date", [(A, ("accounts", CHK, "simplefin_cutover"), "01/01/2030")],
         [(A, f"accounts.{CHK}.simplefin_cutover", "expected an ISO date")]),
    Case("file-source-and-feed-without-cutover", [(A, ("accounts", CHK, "simplefin_cutover"), DROP)],
         [(A, f"accounts.{CHK}", "has a file source and a feed id but no cutover — add simplefin_cutover: <date> or "
                                 "simplefin_balances_only: true")]),
    # an earlier feed id's snapshots are still read: it needs the cutover a current one does
    Case("file-source-and-only-a-former-feed-id-without-cutover",
         [(A, ("accounts", CHK, "simplefin_cutover"), DROP), (A, ("accounts", CHK, "simplefin_id"), DROP),
          (A, ("accounts", CHK, "simplefin_id_history"), ["SF-DEMO-C0-OLD"])],
         [(A, f"accounts.{CHK}", "has a file source and a feed id but no cutover")]),
    Case("glob-absolute", [(A, ("accounts", CHK, "glob"), "/statements/demo_checking_0000-*.csv")],
         [(A, f"accounts.{CHK}.glob", "write a pattern relative to the statements folder — an absolute path is not "
                                      "searched")]),
    # accounts.yaml: an account's sources
    Case("sources-with-parser", [(A, ("accounts", CARD, "parser"), "sample_format")],
         [(A, f"accounts.{CARD}.sources", "given with parser — state the account's statement files one way")]),
    Case("sources-with-parser-and-glob", [(A, ("accounts", CARD, "parser"), "sample_format"),
                                          (A, ("accounts", CARD, "glob"), "demo_card_0000-*.csv")],
         [(A, f"accounts.{CARD}.sources", "given with parser and glob — ")]),
    Case("sources-not-a-list", [(A, SOURCES, {"format": "sample_format", "glob": "demo_card_0000-*.csv"})],
         [(A, f"accounts.{CARD}.sources", "expected a list of one or more sources — write one `- {format: ..., "
                                          "glob: ...}` line per source")]),
    Case("sources-empty", [(A, SOURCES, [])], [(A, f"accounts.{CARD}.sources", "a list of one or more sources")]),
    Case("source-not-a-mapping", [(A, (*SOURCES, 0), "sample_format")],
         [(A, f"accounts.{CARD}.sources[0]", "expected a mapping — write {format: ..., glob: ...}")]),
    Case("source-unknown-key", [(A, (*SOURCES, 1, "sinse"), "2029-07-01")],
         [(A, f"accounts.{CARD}.sources[1].sinse", "did you mean 'since'?")]),
    Case("source-format-missing", [(A, (*SOURCES, 0, "format"), DROP)],
         [(A, f"accounts.{CARD}.sources[0].format", "missing — name the format the source's files are read with, "
                                                    "one of sample_format")]),
    Case("source-format-not-text", [(A, (*SOURCES, 0, "format"), 5)],
         [(A, f"accounts.{CARD}.sources[0].format", "expected text — name the format")]),
    Case("source-format-unknown", [(A, (*SOURCES, 1, "format"), "sample_formt")],
         [(A, f"accounts.{CARD}.sources[1].format", "unknown format 'sample_formt' — one of sample_format")]),
    Case("source-glob-missing", [(A, (*SOURCES, 1, "glob"), DROP)],
         [(A, f"accounts.{CARD}.sources[1].glob", "missing — give the file-name pattern of the source's files")]),
    Case("source-glob-not-text", [(A, (*SOURCES, 1, "glob"), ["*.ofx"])],
         [(A, f"accounts.{CARD}.sources[1].glob", "expected text")]),
    Case("source-glob-absolute", [(A, (*SOURCES, 0, "glob"), "/statements/demo_card_0000-*.csv")],
         [(A, f"accounts.{CARD}.sources[0].glob", "write a pattern relative to the statements folder")]),
    # a file both would match would be read twice
    Case("source-glob-twice", [(A, (*SOURCES, 1, "glob"), "demo_card_0000-*.csv")],
         [(A, f"accounts.{CARD}.sources[1].glob", "the same pattern as sources[0].glob — a file is read by one "
                                                  "source")]),
    Case("first-source-since", [(A, (*SOURCES, 0, "since"), "2029-01-01")],
         [(A, f"accounts.{CARD}.sources[0].since", "the first source has no start day — remove it")]),
    Case("later-source-since-missing", [(A, (*SOURCES, 1, "since"), DROP)],
         [(A, f"accounts.{CARD}.sources[1].since", "missing — give the first day this source holds as YYYY-MM-DD")]),
    Case("source-since-not-a-date", [(A, (*SOURCES, 1, "since"), "07/01/2029")],
         [(A, f"accounts.{CARD}.sources[1].since", "expected an ISO date")]),
    Case("source-since-the-same-day", [(A, SOURCES, [*VALID[A]["accounts"][CARD]["sources"],
                                                     {"format": "sample_format", "glob": "demo_card_0000-*.qfx",
                                                      "since": "2029-07-01"}])],
         [(A, f"accounts.{CARD}.sources[2].since", "not after sources[1].since (2029-07-01) — list the sources oldest "
                                                   "first")]),
    Case("source-since-earlier", [(A, SOURCES, [*VALID[A]["accounts"][CARD]["sources"],
                                                {"format": "sample_format", "glob": "demo_card_0000-*.qfx",
                                                 "since": "2029-06-30"}])],
         [(A, f"accounts.{CARD}.sources[2].since", "not after sources[1].since (2029-07-01)")]),
    Case("sources-and-feed-without-cutover", [(A, ("accounts", CARD, "simplefin_cutover"), DROP)],
         [(A, f"accounts.{CARD}", "has a file source and a feed id but no cutover")]),
    # from the cutover on the feed owns the rows: a source starting then would keep none
    Case("source-since-on-the-cutover", [(A, ("accounts", CARD, "simplefin_cutover"), "2029-07-01")],
         [(A, f"accounts.{CARD}.sources[1].since", "on or after the account's simplefin_cutover (2029-07-01) — the "
                                                   "feed owns the days from the cutover on")]),
    Case("source-since-after-the-cutover", [(A, ("accounts", CARD, "simplefin_cutover"), "2029-06-30")],
         [(A, f"accounts.{CARD}.sources[1].since", "on or after the account's simplefin_cutover (2029-06-30)")]),
    Case("account-id-a-number", [(A, ("accounts", 1234), {**VALID[A]["accounts"][CARD], "simplefin_id": "X",
                                                          "name": "Sample Card 2"})],
         [(A, "accounts.1234", "account ids use letters, digits, _ and - — " + QUOTE_FIX.format(1234))]),
    Case("account-names-alike", [(A, ("accounts", CARD, "name"), " sample checking ")],
         [(A, f"accounts.{CARD}.name", f"same name as accounts.{CHK} — {NAME_FIX}")]),
    Case("manual-entry-named-like-an-account", [(A, ("manual_entries", 0, "account"), "SAMPLE MORTGAGE")],
         [(A, "manual_entries[0].account", f"same name as accounts.{MTG} — {ENTRY_NAME_FIX}")]),
    Case("credit-limit-off-a-card", [(A, ("accounts", CHK, "credit_limit"), 5000)],
         [(A, f"accounts.{CHK}.credit_limit", "only a credit card has a credit limit")]),
    Case("credit-limit-negative", [(A, ("accounts", CARD, "credit_limit"), -5)],
         [(A, f"accounts.{CARD}.credit_limit", "expected an amount of 0 or more")]),
    # a whole number too large for a float is no usable number: refused here, never an overflow later
    Case("credit-limit-too-large", [(A, ("accounts", CARD, "credit_limit"), 10 ** 400)],
         [(A, f"accounts.{CARD}.credit_limit", "expected an amount of 0 or more")]),
    Case("margin-off-a-brokerage", [(A, ("accounts", CHK, "margin"), True)],
         [(A, f"accounts.{CHK}.margin", "only a brokerage account can be a margin account")]),
    Case("mortgage-off-a-loan", [(A, ("accounts", CHK, "mortgage"), True)],
         [(A, f"accounts.{CHK}.mortgage", "only a loan account can be the mortgage")]),
    Case("mortgage-not-a-switch", [(A, ("accounts", MTG, "mortgage"), "yes")],
         [(A, f"accounts.{MTG}.mortgage", "expected true or false")]),
    Case("second-mortgage", [(A, ("accounts", "demo_loan_0001"), LOAN_0001)],
         [(A, "accounts.demo_loan_0001.mortgage", f"a second mortgage account (accounts.{MTG} is one)")]),
    Case("tags-not-a-list", [(A, ("accounts", CHK, "tags"), "Household")],
         [(A, f"accounts.{CHK}.tags", "a list of text")]),
    Case("balances-only-not-a-switch", [(A, ("accounts", BRK, "simplefin_balances_only"), "yes")],
         [(A, f"accounts.{BRK}.simplefin_balances_only", "true or false")]),
    Case("loan-terms-off-a-loan", [(A, ("accounts", CHK, "loan"), {"rate": 6.5})],
         [(A, f"accounts.{CHK}.loan", "only a loan account has loan terms")]),
    Case("loan-unknown-key", [(A, ("accounts", MTG, "loan", "rates"), 6.5)],
         [(A, f"accounts.{MTG}.loan.rates", "unknown key — one of rate")]),
    Case("loan-rate", [(A, ("accounts", MTG, "loan", "rate"), 100)], [(A, f"accounts.{MTG}.loan.rate", "a percent")]),
    Case("loan-origination-date", [(A, ("accounts", MTG, "loan", "origination", "date"), "2030-13-01")],
         [(A, f"accounts.{MTG}.loan.origination.date", "an ISO date")]),
    # accounts.yaml: the manual entries
    Case("manual-not-a-list", [(A, ("manual_entries",), {"Sample House": 1})],
         [(A, "manual_entries", "expected a list")]),
    Case("manual-not-a-mapping", [(A, ("manual_entries", 0), "Sample House")],
         [(A, "manual_entries[0]", "expected a mapping")]),
    Case("manual-unknown-key", [(A, ("manual_entries", 0, "valeu"), 1)],
         [(A, "manual_entries[0].valeu", "did you mean 'value'?")]),
    Case("manual-name-missing", [(A, ("manual_entries", 0, "account"), DROP)],
         [(A, "manual_entries[0].account", "missing")]),
    Case("manual-name-gives-no-id", [(A, ("manual_entries", 0, "account"), "!!!")],
         [(A, "manual_entries[0].account", "has no letters or digits to make its id of — give it a name with "
                                           "letters or digits")]),
    Case("manual-name-gives-a-long-id", [(A, ("manual_entries", 0, "account"), "Sample House " * 6)],
         [(A, "manual_entries[0].account", "makes an id of 77 characters, over 64 — shorten the name")]),
    Case("manual-institution-missing", [(A, ("manual_entries", 0, "institution"), DROP)],
         [(A, "manual_entries[0].institution", "missing")]),
    Case("manual-owner-missing", [(A, ("manual_entries", 0, "owner"), DROP)],
         [(A, "manual_entries[0].owner", "missing")]),
    Case("manual-owner-unknown", [(A, ("manual_entries", 0, "owner"), "Taylor")],
         [(A, "manual_entries[0].owner", "unknown owner 'Taylor'")]),
    Case("manual-class-missing", [(A, ("manual_entries", 0, "asset_class"), DROP)],
         [(A, "manual_entries[0].asset_class", "missing")]),
    Case("manual-class-unknown", [(A, ("manual_entries", 0, "asset_class"), "Boat")],
         [(A, "manual_entries[0].asset_class", "unknown class 'Boat'")]),
    Case("manual-value-not-a-number", [(A, ("manual_entries", 0, "value"), "350000")],
         [(A, "manual_entries[0].value", "expected a number")]),
    Case("manual-value-missing", [(A, ("manual_entries", 0, "value"), DROP)],
         [(A, "manual_entries[0].value", "missing")]),
    Case("manual-value-out-of-range", [(A, ("manual_entries", 0, "value"), 1e13)],
         [(A, "manual_entries[0].value", "outside -1e12 to 1e12 dollars — write the value in dollars")]),
    Case("manual-debt-out-of-range", [(A, ("manual_entries", 0, "value"), -9.3e16)],
         [(A, "manual_entries[0].value", "outside -1e12 to 1e12 dollars — write the value in dollars")]),
    Case("manual-date", [(A, ("manual_entries", 0, "as_of"), "Jan 2030")],
         [(A, "manual_entries[0].as_of", "expected an ISO date")]),
    Case("manual-liquid-not-a-switch", [(A, ("manual_entries", 0, "liquid"), "no")],
         [(A, "manual_entries[0].liquid", "expected true or false")]),
    Case("manual-liquid-debt", [(A, ("manual_entries", 0, "asset_class"), "Other Debt"),
                                (A, ("manual_entries", 0, "liquid"), True)],
         [(A, "manual_entries[0].liquid", "a debt is never liquid — remove liquid or set it false")]),
    Case("manual-name-twice", [(A, ("manual_entries",), VALID[A]["manual_entries"] * 2)],
         [(A, "manual_entries[1].account", "same name as entry 0")]),
    Case("manual-acquired-not-a-date", [(A, ("manual_entries", 0, "acquired"), "spring 2029")],
         [(A, "manual_entries[0].acquired", "expected an ISO date")]),
    Case("manual-acquired-after-as-of", [(A, ("manual_entries", 0, "acquired"), "2030-02-01")],
         [(A, "manual_entries[0].acquired", "after the entry's as_of (2030-01-31) — ")]),
    # the rule files: their top level, `version` and the checks that existed
    Case("assumptions-version", [("assumptions.yaml", ("version",), 2)],
         [("assumptions.yaml", "version", "this release reads version 1")]),
    Case("assumptions-threshold-not-a-number", [("assumptions.yaml", ("match_window_bdays",), "3")],
         [("assumptions.yaml", "match_window_bdays", "expected a whole number — write a whole number")]),
    Case("assumptions-coverage", [("assumptions.yaml", ("coverage", "min_share"), 2)],
         [("assumptions.yaml", "coverage.min_share", "above 0 and at most 1")]),
    Case("assumptions-generic-min", [("assumptions.yaml", ("generic_min_amount",), -1)],
         [("assumptions.yaml", "generic_min_amount", "0 or more")]),
    Case("assumptions-retirement-owner", [(AS, ("household", "retirement_accounts", "Sample Retirement"), "Taylor")],
         [(AS, "household.retirement_accounts.Sample Retirement", "unknown owner 'Taylor'")]),
    Case("bank-label-not-a-mapping", [(BM, ("Food & Drink",), "Dining")], [(BM, "Food & Drink", "expected a mapping")]),
    Case("category-rules-not-a-mapping", [(CR, (), ["Dining"])], [(CR, "", "top level: expected a mapping")]),
    Case("keyword-rules-not-a-list", [(CR, ("keyword_rules",), "^SAMPLE")], [(CR, "keyword_rules", "expected a list")]),
    Case("keyword-rule-off-taxonomy", [(CR, ("keyword_rules", 0, "category"), "Dinning")],
         [(CR, "keyword_rules[0] (demo-cafe)", "category 'Dinning' not in taxonomy — correct the entry")]),
    Case("keyword-rule-stage", [(CR, ("keyword_rules", 0, "stage"), "late")],
         [(CR, "keyword_rules[0]", "unknown stage 'late'")]),
    Case("taxonomy-subcategories-not-a-list", [(CR, ("taxonomy", "Dining"), 5)],
         [(CR, "taxonomy.Dining", "expected a list of subcategories")]),
    Case("taxonomy-reserved", [(CR, ("taxonomy", "Internal"), DROP)],
         [(CR, "taxonomy", "missing reserved category 'Internal'")]),
    Case("taxonomy-key-a-number", [(CR, ("taxonomy", 2030), [])],
         [(CR, "taxonomy.2030", "a category name YAML read as a number — " + QUOTE_FIX.format(2030))]),
    Case("income-rule-earner", [(CR, ("income_rules",), [{"pattern": "^SAMPLE PAY", "type": "income",
                                                          "earner": "Taylor"}])],
         [(CR, "income_rules[0].earner", "unknown earner 'Taylor' — one of: Alex, Sam, Robin")]),
    Case("category-rename", [(CR, ("renames",), [*VALID[CR]["renames"], {"from": {"category": "Dining"},
                                                                         "to": {"category": "Nowhere"}}])],
         [(CR, "renames[1]", "not in taxonomy — correct the entry")]),
    Case("vendor-patterns-not-a-list", [(VR, ("patterns",), 5)], [(VR, "patterns", "expected a list")]),
    Case("vendor-pattern-without-pattern", [(VR, ("patterns", 0, "pattern"), DROP)],
         [(VR, "patterns[0]", "vendor patterns need a pattern:")]),
    Case("vendor-rename-of-a-pattern-vendor", [(VR, ("renames",), [{"from": "Sample Cafe", "to": "Sample Coffee"}])],
         [(VR, "renames[0]", "change the pattern's vendor: instead")]),
    Case("vendor-learning", [(VR, ("learning",), ["Sample Cafe"])], [(VR, "learning", "must be a mapping whose exclude:")]),
    Case("vendor-split", [(VR, ("patterns", 0, "split"), [{"pct": 50, "category": "Dining"}])],
         [(VR, "patterns[0].split", "needs a list of at least 2 legs")]),
    Case("transfer-rule-type", [(TR, ("rules", 0, "type"), "payment")],
         [(TR, "rules[0] (demo-card-payment)", "unknown type 'payment'")]),
    Case("transfer-group", [(TR, ("groups", "demo_cards"), [CARD])],
         [(TR, "groups.demo_cards", "expected {accounts: [...], expect_counterpart: true|false} — ")]),
    Case("transfer-cashout", [(TR, ("venmo_patterns", "cashout_income"), "x")],
         [(TR, "venmo_patterns.cashout_income", "expected a list")]),
    Case("transfer-rules-not-a-mapping", [(TR, (), ["x"])], [(TR, "", "top level: expected a mapping")]),
    Case("overrides-not-a-list", [(OV, (), {"match": {}})], [(OV, "", "top level: expected a list")]),
    Case("override-earner", [(OV, (0, "set", "earner"), "Taylor")],
         [(OV, "[0].set.earner", "unknown earner 'Taylor' — one of: Alex, Sam, Robin")]),
    # the Internal category files a row as internal only together with an internal type: alone it is spending
    Case("override-internal-category-without-a-type", [(OV, (0, "set", "category"), "Internal")],
         [(OV, "[0].set.category", "category 'Internal' with no internal type still counts the row in spending or "
                                   "income — set type: transfer (transfer_rules.yaml or an override) instead")]),
    Case("override-internal-category-on-a-purchase", [(OV, (1, "set", "category"), "Internal"),
                                                      (OV, (1, "set", "subcategory"), DROP)],
         [(OV, "[1].set.category", "category 'Internal' with no internal type still counts the row")]),
    Case("keyword-rule-internal-category", [(CR, ("keyword_rules", 0, "category"), "Internal"),
                                            (CR, ("keyword_rules", 0, "subcategory"), DROP)],
         [(CR, "keyword_rules[0] (demo-cafe)", "category 'Internal' does not make a row internal, so it would still "
                                               "count in spending or income — set type: transfer (transfer_rules.yaml "
                                               "or an override) instead")]),
    Case("vendor-pattern-internal-category", [(VR, ("patterns", 0, "category"), "Internal")],
         [(VR, "patterns[0]", "category 'Internal' does not make a row internal")]),
    # category_rules.yaml: unknown keys, every rule's names in the taxonomy, every regex, the catch-all's place
    Case("category-rules-unknown-key", [(CR, ("keyword_rule",), [])], [(CR, "keyword_rule", "'keyword_rules'?")]),
    Case("keyword-rule-unknown-key", [(CR, ("keyword_rules", 0, "subcatgory"), "Coffee")],
         [(CR, "keyword_rules[0].subcatgory", "did you mean 'subcategory'?")]),
    # a rule with an unknown key is left out of the checks after it: its off-taxonomy category is not reported
    Case("keyword-rule-unknown-key-is-left-out", [(CR, ("keyword_rules", 0, "catgory"), "Dining"),
                                                  (CR, ("keyword_rules", 0, "category"), "Dinning")],
         [(CR, "keyword_rules[0].catgory", "did you mean 'category'?")]),
    Case("keyword-condition-unknown-key", [(CR, ("keyword_rules", 1, "all", 0, "feild"), "vendor")],
         [(CR, "keyword_rules[1].all[0].feild", "did you mean 'field'?")]),
    Case("keyword-set-unknown-key", [(CR, ("keyword_rules", 1, "set", "catgory"), "Dining")],
         [(CR, "keyword_rules[1].set.catgory", "did you mean 'category'?")]),
    Case("keyword-condition-field", [(CR, ("keyword_rules", 2, "any", 0, "field"), "descripton")],
         [(CR, "keyword_rules[2]", "unknown field 'descripton'")]),
    Case("keyword-rule-subcategory-off-taxonomy", [(CR, ("keyword_rules", 0, "subcategory"), "Brunch")],
         [(CR, "keyword_rules[0] (demo-cafe)", "subcategory 'Brunch' not seeded under 'Dining'")]),
    Case("keyword-rule-regex", [(CR, ("keyword_rules", 0, "pattern"), "^SAMPLE (CAFE")],
         [(CR, "keyword_rules[0]", "bad regex '^SAMPLE (CAFE'")]),
    Case("keyword-rule-owner-condition", [(CR, ("keyword_rules", 1, "all", 2, "value"), ["Alex", "Taylor"])],
         [(CR, "keyword_rules[1]", "unknown owner 'Taylor' — one of: Alex, Sam, Joint, Robin")]),
    Case("keyword-rule-account-condition", [(CR, ("keyword_rules", 1, "all", 3, "value"), "demo_missing_9999")],
         [(CR, "keyword_rules[1]", "unknown account 'demo_missing_9999' — use an account id from accounts.yaml")]),
    Case("keyword-rule-split", [(CR, ("keyword_rules", 0, "split"), VALID[VR]["patterns"][1]["split"])],
         [(CR, "keyword_rules[0]", "split: is only valid on a vendor_rules pattern")]),
    Case("p2p-catch-all-not-last", [(CR, ("keyword_rules",), [VALID[CR]["keyword_rules"][i] for i in (0, 1, 3, 2)])],
         [(CR, "keyword_rules[2]", "1 rule(s) of the default stage come after it and can never file such a row — "
                                   "move it to the end of the stage — a stage can hold only one such rule; merge two "
                                   "into one pattern")]),
    Case("income-rule-unknown-key", [(CR, ("income_rules", 0, "employr"), "Sample Employer")],
         [(CR, "income_rules[0].employr", "did you mean 'employer'?")]),
    Case("income-rule-subcategory-off-taxonomy", [(CR, ("income_rules", 0, "subcategory"), "Bonus")],
         [(CR, "income_rules[0].subcategory", "subcategory 'Bonus' is not under 'Income' in the taxonomy — ")]),
    Case("income-rule-regex", [(CR, ("income_rules", 0, "pattern"), "^SAMPLE (PAY")],
         [(CR, "income_rules[0].pattern", "is not a valid regular expression")]),
    Case("income-rule-pattern-missing", [(CR, ("income_rules", 0, "pattern"), DROP)],
         [(CR, "income_rules[0].pattern", "missing")]),
    Case("income-rule-type", [(CR, ("income_rules", 0, "type"), "salary")],
         [(CR, "income_rules[0].type", "unknown type 'salary' — one of income, dividend, interest")]),
    Case("income-rule-account", [(CR, ("income_rules", 0, "accounts"), ["demo_missing_9999"])],
         [(CR, "income_rules[0].accounts", "unknown account 'demo_missing_9999'")]),
    Case("income-rule-earner-is-the-joint-label", [(CR, ("income_rules", 0, "earner"), "Joint")],
         [(CR, "income_rules[0].earner", "'Joint' is the joint label, which earns nothing — name the member")]),
    Case("income-rule-employer-empty", [(CR, ("income_rules", 0, "employer"), "")],
         [(CR, "income_rules[0].employer", "expected text")]),
    Case("category-rename-unknown-key", [(CR, ("renames",), [*VALID[CR]["renames"],
                                                            {"from": {"category": "Old Dining"},
                                                             "to": {"category": "Dining", "subcategry": "Coffee"}}])],
         [(CR, "renames[1].to.subcategry", "did you mean 'subcategory'?")]),
    # vendor_rules.yaml
    Case("vendor-rules-unknown-key", [(VR, ("pattern",), [])], [(VR, "pattern", "did you mean 'patterns'?")]),
    Case("vendor-pattern-unknown-key", [(VR, ("patterns", 0, "vendr"), "x")],
         [(VR, "patterns[0].vendr", "did you mean 'vendor'?")]),
    Case("vendor-split-leg-unknown-key", [(VR, ("patterns", 1, "split", 0, "pcts"), 10)],
         [(VR, "patterns[1].split[0].pcts", "did you mean 'pct'?")]),
    Case("vendor-pattern-off-taxonomy", [(VR, ("patterns", 0, "category"), "Dinning")],
         [(VR, "patterns[0]", "category 'Dinning' not in taxonomy")]),
    Case("vendor-pattern-stage", [(VR, ("patterns", 0, "stage"), "pre")],
         [(VR, "patterns[0]", "vendor patterns take no stage:")]),
    Case("vendor-pattern-structured", [(VR, ("patterns", 0), {"all": [{"field": "description", "op": "contains",
                                                                      "value": "SAMPLE"}], "set": {"vendor": "Sample"}})],
         [(VR, "patterns[0]", "vendor patterns need a pattern:")]),
    Case("vendor-pattern-regex", [(VR, ("patterns", 0, "pattern"), "SAMPLE (CAFE")],
         [(VR, "patterns[0]", "bad regex 'SAMPLE (CAFE'")]),
    Case("vendor-split-leg-off-taxonomy", [(VR, ("patterns", 1, "split", 0, "category"), "Dinning")],
         [(VR, "patterns[1].split", "leg 0: category 'Dinning' not in taxonomy")]),
    Case("strip-prefix-regex", [(VR, ("strip_prefixes",), ["^SQ (\\*"])],
         [(VR, "strip_prefixes[0]", "is not a valid regular expression")]),
    Case("cleanup-regex", [(VR, ("cleanup",), ["#\\d+", "STORE ["])], [(VR, "cleanup[1]", "not a valid regular")]),
    Case("learning-unknown-key", [(VR, ("learning", "excludes"), [])], [(VR, "learning.excludes", "'exclude'?")]),
    Case("learning-exclude-not-a-list", [(VR, ("learning", "exclude"), "Sample Grocer")],
         [(VR, "learning", "must be a mapping whose exclude: is a list of vendor names")]),
    Case("vendor-rename-unknown-key", [(VR, ("renames", 0, "too"), "x")], [(VR, "renames[0].too", "'to'?")]),
    # transfer_rules.yaml
    Case("transfer-rules-unknown-key", [(TR, ("group",), {})], [(TR, "group", "did you mean 'groups'?")]),
    Case("transfer-rule-unknown-key", [(TR, ("rules", 0, "counter_hnt"), "demo_cards")],
         [(TR, "rules[0].counter_hnt", "did you mean 'counter_hint'?")]),
    Case("transfer-rule-needs-review-not-a-switch", [(TR, ("rules", 0, "needs_review"), "yes")],
         [(TR, "rules[0].needs_review", "expected true or false")]),
    Case("transfer-rule-regex", [(TR, ("rules", 0, "pattern"), "^SAMPLE (CARD")],
         [(TR, "rules[0] (demo-card-payment)", "pattern is not a valid regular expression")]),
    Case("transfer-group-unknown-key", [(TR, ("groups", "demo_cards", "expect_counterparts"), True)],
         [(TR, "groups.demo_cards.expect_counterparts", "did you mean 'expect_counterpart'?")]),
    Case("venmo-unknown-key", [(TR, ("venmo_patterns", "zele"), "ZELLE")],
         [(TR, "venmo_patterns.zele", "did you mean 'zelle'?")]),
    Case("cashout-unknown-key", [(TR, ("venmo_patterns", "cashout_income", 0, "earnr"), "Sam")],
         [(TR, "venmo_patterns.cashout_income[0].earnr", "did you mean 'earner'?")]),
    Case("cashout-earner-is-the-joint-label", [(TR, ("venmo_patterns", "cashout_income", 0, "earner"), "Joint")],
         [(TR, "venmo_patterns.cashout_income[0].earner", "'Joint' is the joint label")]),
    Case("from-text-placeholder", [(TR, ("rules", 1, "counter_hint"), "from_text:demo_card_{2}")],
         [(TR, "rules[1] (demo-move)", "uses {2} but the pattern has 1 group(s)")]),
    Case("zelle-party-two-groups", [(TR, ("venmo_patterns", "zelle_party"), "SAMPLEZELLE (TO|FROM) ([A-Z ]+)")],
         [(TR, "venmo_patterns.zelle_party", "has 2 groups")]),
    # bank_category_map.yaml
    Case("bank-label-unknown-key", [(BM, ("Food & Drink", "categry"), "Dining")],
         [(BM, "Food & Drink.categry", "did you mean 'category'?")]),
    Case("bank-label-off-taxonomy", [(BM, ("Food & Drink", "category"), "Dinning")],
         [(BM, "Food & Drink.category", "category 'Dinning' is not in the taxonomy — ")]),
    Case("bank-label-subcategory-off-taxonomy", [(BM, ("Food & Drink", "subcategory"), "Brunch")],
         [(BM, "Food & Drink.subcategory", "subcategory 'Brunch' is not under 'Dining'")]),
    Case("bank-label-category-missing", [(BM, ("Food & Drink", "category"), DROP)],
         [(BM, "Food & Drink.category", "missing")]),
    Case("bank-label-tags-not-a-list", [(BM, ("Food & Drink", "tags"), "Eating")],
         [(BM, "Food & Drink.tags", "expected a list of text")]),
    # overrides.yaml
    Case("override-not-a-mapping", [(OV, (0,), "Sample Cafe")], [(OV, "[0]", "expected a mapping")]),
    Case("override-unknown-key", [(OV, (0, "nots"), "x")], [(OV, "[0].nots", "did you mean 'note'?")]),
    Case("override-match-missing", [(OV, (0, "match"), DROP)], [(OV, "[0].match", "missing")]),
    Case("override-match-nothing", [(OV, (0, "match"), {})], [(OV, "[0].match", "matches nothing")]),
    Case("override-match-unknown-key", [(OV, (0, "match", "vendr"), "x")],
         [(OV, "[0].match.vendr", "did you mean 'vendor'?")]),
    Case("override-match-account", [(OV, (1, "match", "account"), "demo_missing_9999")],
         [(OV, "[1].match.account", "unknown account 'demo_missing_9999'")]),
    Case("override-match-date", [(OV, (1, "match", "date"), "Jan 15")], [(OV, "[1].match.date", "an ISO date")]),
    Case("override-match-amount", [(OV, (1, "match", "amount"), "12.50")], [(OV, "[1].match.amount", "a number")]),
    Case("override-set-missing", [(OV, (0, "set"), DROP)], [(OV, "[0].set", "missing")]),
    Case("override-set-unknown-key", [(OV, (0, "set", "categry"), "Dining")],
         [(OV, "[0].set.categry", "did you mean 'category'?")]),
    Case("override-category-off-taxonomy", [(OV, (0, "set", "category"), "Dinning")],
         [(OV, "[0].set.category", "category 'Dinning' is not in the taxonomy")]),
    Case("override-subcategory-off-taxonomy", [(OV, (0, "set", "subcategory"), "Brunch")],
         [(OV, "[0].set.subcategory", "subcategory 'Brunch' is not under 'Dining'")]),
    Case("override-set-type", [(OV, (1, "set", "type"), "spend")], [(OV, "[1].set.type", "unknown type 'spend'")]),
    Case("override-earner-is-the-joint-label", [(OV, (1, "set", "earner"), "Joint")],
         [(OV, "[1].set.earner", "'Joint' is the joint label")]),
    # assumptions.yaml
    Case("assumptions-unknown-key", [(AS, ("match_window_bday",), 3)],
         [(AS, "match_window_bday", "did you mean 'match_window_bdays'?")]),
    Case("assumptions-threshold-negative", [(AS, ("venmo_review_threshold",), -1)],
         [(AS, "venmo_review_threshold", "expected 0 or more")]),
    Case("coverage-unknown-key", [(AS, ("coverage", "min_shares"), 0.5)],
         [(AS, "coverage.min_shares", "did you mean 'min_share'?")]),
    Case("cancelled-unknown-key", [(AS, ("cancelled_subscriptions", 0, "vendr"), "x")],
         [(AS, "cancelled_subscriptions[0].vendr", "did you mean 'vendor'?")]),
    Case("cancelled-vendor-missing", [(AS, ("cancelled_subscriptions", 0, "vendor"), DROP)],
         [(AS, "cancelled_subscriptions[0].vendor", "missing")]),
    Case("cancelled-recorded-on-not-a-date", [(AS, ("cancelled_subscriptions", 0, "recorded_on"), "last week")],
         [(AS, "cancelled_subscriptions[0].recorded_on", "expected an ISO date")]),
    Case("margin-apr-a-percent", [(AS, ("household", "margin_apr"), 5)],
         [(AS, "household.margin_apr", "expected a yearly rate from 0 up to 1")]),
    Case("assumptions-household-unknown-key", [(AS, ("household", "margin_rate"), 0.05)],
         [(AS, "household.margin_rate", "did you mean 'margin_apr'?")]),
    Case("retirement-account-unknown-name", [(AS, ("household", "retirement_accounts"), {"Sample Pension": "Alex"})],
         [(AS, "household.retirement_accounts.Sample Pension", "no account or manual entry has this name")]),
    Case("projections-unknown-key", [(AS, ("projections", "inflation"), 2)],
         [(AS, "projections.inflation", "did you mean 'inflation_pct'?")]),
    Case("projections-not-a-number", [(AS, ("projections", "ltcg_pct"), "15")],
         [(AS, "projections.ltcg_pct", "expected a number")]),
    Case("projections-scenarios", [(AS, ("projections", "return_scenarios_pct"), [4, "six"])],
         [(AS, "projections.return_scenarios_pct", "expected a list of numbers")]),
    # the key is optional (absent: the defaults), but present it states at least one return
    Case("projections-scenarios-empty", [(AS, ("projections", "return_scenarios_pct"), [])],
         [(AS, "projections.return_scenarios_pct", "an empty list states no return")]),
    Case("targets-unknown-class", [(AS, ("targets", "Bonds"), 0)], [(AS, "targets.Bonds", "unknown class 'Bonds'")]),
    Case("targets-debt-class", [(AS, ("targets", "Mortgage"), 0)], [(AS, "targets.Mortgage", "a debt is no target")]),
    # a whole number too large for a float is out of range, never summed into an overflow
    Case("targets-huge", [(AS, ("targets", "Cash"), 10 ** 400)], [(AS, "targets.Cash", "a percent from 0 to 100")]),
    Case("budgets-off-taxonomy", [(AS, ("budgets", "Dinning"), 400)],
         [(AS, "budgets.Dinning", "category 'Dinning' is not in the taxonomy")]),
    Case("budgets-negative", [(AS, ("budgets", "Dining"), -5)], [(AS, "budgets.Dining", "an amount of 0 or more")]),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_each_check_reports_its_problem_with_its_file_path_and_fix(tmp_path, case):
    problems = problems_of(tmp_path, case.edits)
    assert [(p.file, p.path) for p in problems] == [(f, p) for f, p, _ in case.expected], [str(p) for p in problems]
    for p, (_, _, fragment) in zip(problems, case.expected, strict=True):
        assert fragment in str(p), str(p)
        assert p.file and p.message and p.fix, str(p)
        # what it prints reads back as the same problem text (the form every older check returns)
        parsed = Problem.parse(str(p))
        assert str(parsed) == str(p) and parsed.file and parsed.message and parsed.fix


@pytest.mark.parametrize("internal_type", ["transfer", "cc-payment", "investment"])
def test_an_override_naming_internal_with_an_internal_type_is_valid(tmp_path, internal_type):
    assert problems_of(tmp_path, [(OV, (1, "set", "type"), internal_type), (OV, (1, "set", "category"), "Internal"),
                                  (OV, (1, "set", "subcategory"), internal_type)]) == []


@pytest.mark.parametrize("url", ["https://llm.example.net/v1", "http://127.0.0.1:18210/v1", "http://localhost:18210",
                                 "http://[::1]:18210/v1", "http://127.0.0.2/v1"])
def test_an_ai_server_is_https_or_this_computer(tmp_path, url):
    assert problems_of(tmp_path, [(H, ("ai", "base_url"), url)]) == []


@pytest.mark.parametrize("url", ["http://192.0.2.10/v1", "ftp://llm.example.net", "https://", "http://127.0.0.1:0/v1",
                                 "https://llm.example.net:99999/v1", "http://localhost.example.net/v1",
                                 "https://llm.example.net/v1#frag", "https://llm.example.net/v1?", "https://x.example.net#@localhost"])
def test_any_other_ai_server_address_is_refused(tmp_path, url):
    [problem] = problems_of(tmp_path, [(H, ("ai", "base_url"), url)])
    assert problem.path == "ai.base_url" and "never in the address" in problem.fix


@pytest.mark.parametrize("file, path, value", [
    (A, ("household", "p2p_counter_accounts"), {"demo_cards": [CARD]}),
    (AS, ("mortgage",), {"rate": 6.5}),
    (AS, ("projections", "home_value_estimate"), 350000),
    (A, ("accounts", BRK, "holdings_asset_class"), "Stocks/ETF"),
], ids=["p2p_counter_accounts", "mortgage", "home_value_estimate", "holdings_asset_class"])
def test_the_removed_keys_are_unknown_keys_like_any_other(tmp_path, file, path, value):
    """No validator knows these keys any more: each is one `unknown key` problem, as a misspelt key is, and neither
    the registry nor the document lists it."""
    [problem] = problems_of(tmp_path, [(file, path, value)])
    key = ".".join(map(str, path))
    assert (problem.file, problem.path, problem.message) == (file, key, "unknown key")
    assert not any(k.file == file and re.sub(r"<[^>]+>", BRK, k.path) == key for k in KEYS)


@pytest.mark.parametrize("edits, refused", [
    ([(A, ("accounts", CHK, "simplefin_cutover"), DROP)], True),
    ([(A, ("accounts", CHK, "simplefin_cutover"), DROP), (A, ("accounts", CHK, "simplefin_balances_only"), True)],
     False),                                          # the feed gives balances only: nothing to divide
    ([(A, ("accounts", CHK, "simplefin_cutover"), DROP), (A, ("accounts", CHK, "simplefin_balances_only"), False)],
     True),
    ([(A, ("accounts", CHK, "simplefin_cutover"), DROP), (A, ("accounts", CHK, "simplefin_id"), DROP)], False),
    ([(A, ("accounts", CHK, "simplefin_cutover"), DROP), (A, ("accounts", CHK, "parser"), DROP),
      (A, ("accounts", CHK, "glob"), DROP)], False),  # a feed-only account
], ids=["no-cutover", "balances-only", "balances-only-false", "no-feed-id", "no-file-source"])
def test_a_file_source_and_a_feed_id_need_a_cutover_or_balances_only(tmp_path, edits, refused):
    problems = [p for p in problems_of(tmp_path, edits) if p.message.endswith("no cutover")]
    assert [p.path for p in problems] == ([f"accounts.{CHK}"] if refused else [])


@pytest.mark.parametrize("edits, refused", [
    ([(A, ("accounts", CARD, "simplefin_cutover"), DROP)], True),
    ([(A, ("accounts", CARD, "simplefin_cutover"), DROP), (A, ("accounts", CARD, "simplefin_balances_only"), True)],
     False),
    ([(A, ("accounts", CARD, "simplefin_cutover"), DROP), (A, ("accounts", CARD, "simplefin_id"), DROP)], False),
    ([(A, ("accounts", CARD, "simplefin_cutover"), DROP), (A, SOURCES, DROP)], False),
    # a null list reads as absent
    ([(A, ("accounts", CARD, "simplefin_cutover"), DROP), (A, SOURCES, None)], False),
], ids=["no-cutover", "balances-only", "no-feed-id", "no-sources", "null-sources"])
def test_sources_and_a_feed_id_need_a_cutover_or_balances_only_as_the_short_form_does(tmp_path, edits, refused):
    problems = [p for p in problems_of(tmp_path, edits) if p.message.endswith("no cutover")]
    assert [p.path for p in problems] == ([f"accounts.{CARD}"] if refused else [])


BOTH = ("with simplefin_balances_only: true the feed brings no transactions, so there is nothing to cut over to — "
        "remove one of the two")


@pytest.mark.parametrize("account", [CHK, CARD, BRK], ids=["short-form", "sources", "feed-only"])
def test_a_cutover_and_balances_only_are_refused_together(tmp_path, account):
    """With balances only the feed supplies no row, yet the cutover would still clip the file rows from its day on once
    the feed reports a balance: rows stored nowhere. One problem, at the cutover, in either form of an account's files
    (and on an account with none)"""
    edits = [(A, ("accounts", account, "simplefin_balances_only"), True)]
    if account == BRK:
        edits.append((A, ("accounts", account, "simplefin_cutover"), "2030-03-01"))
    assert [(p.path, f"{p.message} — {p.fix}") for p in problems_of(tmp_path, edits)] == [
        (f"accounts.{account}.simplefin_cutover", BOTH)]


def test_a_source_starting_on_or_after_the_cutover_is_refused_with_balances_only_too(tmp_path):
    """The cutover and balances only are refused together, and a source starting on or after the cutover is refused
    as it is without balances only"""
    problems = problems_of(tmp_path, [(A, ("accounts", CARD, "simplefin_cutover"), "2029-06-30"),
                                      (A, ("accounts", CARD, "simplefin_balances_only"), True)])
    assert [(p.path, p.message) for p in problems] == [
        (f"accounts.{CARD}.simplefin_cutover", BOTH.split(" — ")[0]),
        (f"accounts.{CARD}.sources[1].since", "on or after the account's simplefin_cutover (2029-06-30)")]
    # the bank-feed commands load such an account as they load any
    d = write_config(tmp_path / "feed", edited([(A, ("accounts", CARD, "simplefin_cutover"), "2029-06-30"),
                                                (A, ("accounts", CARD, "simplefin_balances_only"), True)]))
    assert FeedConfig(d).simplefin_map()["SF-DEMO-K0"] == CARD


def test_the_feed_config_does_not_ask_for_a_cutover(tmp_path):
    """A fetch counts nothing twice: the bank-feed commands load an account with a file source, a feed id and no
    cutover."""
    d = write_config(tmp_path, edited([(A, ("accounts", CHK, "simplefin_cutover"), DROP)]))
    assert FeedConfig(d).simplefin_map()["SF-DEMO-C0"] == CHK


def test_each_compiler_reports_every_bad_entry_of_its_list_in_one_run(tmp_path):
    """The keyword-rule, vendor-pattern, split, rename and income-rule checks collect: two bad entries in each list are
    two problems, each naming its entry, in one run."""
    rules = copy.deepcopy(VALID[CR]["keyword_rules"])
    rules[0]["stage"] = "late"
    rules[2]["set"] = {"category": "Dinning"}
    patterns = copy.deepcopy(VALID[VR]["patterns"])
    patterns[0]["pattern"] = "SAMPLE (CAFE"
    patterns[1]["split"][0]["pct"] = 150
    patterns.append({"pattern": "SAMPLE MARKET", "vendor": "Sample Market",
                     "split": [{"pct": 50, "category": "Groceries"}, {"pct": 20, "category": "Groceries"}]})
    problems = problems_of(tmp_path, [
        (CR, ("keyword_rules",), rules),
        (CR, ("income_rules",), [{"pattern": "(", "subcategory": "Salary"}, {"pattern": "^SAMPLE", "type": "bonus"}]),
        (CR, ("renames",), [{"from": {"category": "A"}, "to": {"category": "Nowhere"}},
                            {"from": {"category": "B"}, "to": {"category": "Nowhere"}}]),
        (VR, ("patterns",), patterns),
        (VR, ("renames",), [{"from": "Sample A"}, {"from": "Sample B", "to": "Sample B"}]),
        # the category the valid renames move is gone with them
        (OV, (1, "set", "category"), "Dining"), (OV, (1, "set", "subcategory"), "Coffee"),
        (AS, ("budgets",), {"Dining": 400})])
    assert [(p.file, p.path) for p in problems] == [
        (CR, "income_rules[0].pattern"), (CR, "income_rules[1].type"),
        (CR, "keyword_rules[0]"), (CR, "keyword_rules[2] (demo-sample-shop)"),
        (CR, "renames[0]"), (CR, "renames[1]"),
        (VR, "patterns[0]"), (VR, "patterns[1].split"), (VR, "patterns[2].split"),
        (VR, "renames[0]"), (VR, "renames[1]")], [str(p) for p in problems]


def test_the_projection_keys_are_the_ones_the_dashboard_accepts():
    """The dashboard reads assumptions.json `projections` with a strict schema, which refuses a key it does not know:
    the registry lists exactly its keys, so such a key is a validation problem rather than a broken dashboard."""
    ts = (ROOT / "app" / "src" / "data" / "schemas.ts").read_text("utf-8")
    m = re.search(r"\n\s*projections: z\.strictObject\(\{(.*?)\}\),", ts, re.S)
    assert m, "the projections schema was not found in app/src/data/schemas.ts"
    accepted = set(re.findall(r"(\w+):", m[1]))
    assert accepted and {k.path.removeprefix("projections.") for k in KEYS
                         if k.file == AS and k.path.startswith("projections.")} == accepted


def test_three_problems_in_three_files_are_reported_in_one_run_sorted(tmp_path):
    problems = problems_of(tmp_path, [("vendor_rules.yaml", ("patterns",), 5), (H, ("version",), 2),
                                      (A, ("accounts", CHK, "kind"), "piggy-bank")])
    assert [(p.file, p.path) for p in problems] == [
        (A, f"accounts.{CHK}.kind"), (H, "version"), ("vendor_rules.yaml", "patterns")]
    with pytest.raises(ConfigError) as e:
        Config(write_config(tmp_path / "again", edited([("vendor_rules.yaml", ("patterns",), 5),
                                                        (H, ("version",), 2),
                                                        (A, ("accounts", CHK, "kind"), "piggy-bank")])),
               parser_names=PARSERS)
    assert str(e.value).splitlines() == [str(p) for p in problems]


def test_a_file_that_is_not_valid_yaml_is_a_problem_and_the_others_are_still_checked(tmp_path):
    d = write_config(tmp_path, edited([(H, ("version",), 2)]))
    (d / "vendor_rules.yaml").write_text("patterns:\n  - pattern: [unclosed\n", "utf-8")
    problems = validate(d, parser_names=PARSERS)
    assert [(p.file, p.path) for p in problems] == [(H, "version"), ("vendor_rules.yaml", "")]
    assert re.fullmatch(r"not valid YAML \(line \d+, column \d+: .+\)", problems[1].message), problems[1].message
    assert problems[1].fix == "fix the syntax near that line"


# an unquoted value YAML reads as a date or a time but cannot build: the line as written, and the impossible value
IMPOSSIBLE = [("as_of: '2030-01-31'", "as_of: 2030-02-30"),                          # manual_entries[0].as_of
              ("simplefin_cutover: '2030-01-01'", "simplefin_cutover: 2030-13-01"),  # a 13th month
              ("date: '2030-01-15'", "date: 2030-01-15 25:00:00")]                   # loan.origination.date, hour 25


def _with_impossible_value(tmp_path: Path, written: str, impossible: str) -> Path:
    d = write_config(tmp_path)
    text = (d / A).read_text("utf-8")
    assert text.count(written) == 1, written
    (d / A).write_text(text.replace(written, impossible), "utf-8")
    return d


@pytest.mark.parametrize("written, impossible", IMPOSSIBLE, ids=["day", "month", "hour"])
def test_an_unquoted_impossible_date_is_one_problem_and_no_command_prints_a_traceback(tmp_path, cli, capsys, written,
                                                                                      impossible):
    d = _with_impossible_value(tmp_path, written, impossible)
    [problem] = validate(d, parser_names=PARSERS)
    value = impossible.split(": ", 1)[1]
    assert (problem.file, problem.path) == (A, "")
    assert re.fullmatch(r"not valid YAML \(line \d+, column \d+: '" + re.escape(value) + r"' cannot be read \(.+\)\)",
                        problem.message), problem.message
    assert problem.fix == "write a valid value, or quote it to keep it as text"
    assert cli(d, "validate") == 1
    assert cli(d, "ingest", FF_DATA_DIR=str(tmp_path / "data-here")) == 2
    assert cli(d, "rules", "overlaps") == 2
    out = capsys.readouterr()
    assert "Traceback" not in out.out + out.err
    assert out.out.splitlines() == [str(problem)] and out.err.splitlines() == [str(problem)] * 2
    assert not (tmp_path / "data-here").exists()


def test_a_problem_with_no_fix_has_no_dangling_dash():
    p = Problem("assumptions.yaml", "coverage", "expected a mapping", "")
    assert str(p) == "assumptions.yaml: coverage: expected a mapping"
    assert Problem.parse(str(p)) == p


def test_a_typo_in_one_member_still_checks_the_owners_the_others_name(tmp_path):
    """The owner list comes from the well-formed members: a key naming a name nobody has is still reported, and one
    naming the member whose own entry is broken waits until that entry is fixed (its problem says so)."""
    problems = problems_of(tmp_path, [(H, ("members", 2, "role"), "kid"), (A, ("accounts", CHK, "owner"), "Taylor"),
                                      (A, ("accounts", CARD, "owner"), "Robin")])
    assert [str(p) for p in problems] == [
        f"accounts.yaml: accounts.{CHK}.owner: unknown owner 'Taylor' — one of: Alex, Sam, Joint",
        "household.yaml: members[2].role: unknown role 'kid' — one of: adult, child (until it is fixed, owner and "
        "earner keys naming 'Robin' are not checked)"]


def test_a_missing_required_file_is_a_problem_and_a_missing_optional_one_reads_as_empty(tmp_path):
    d = write_config(tmp_path)
    for name in ("assumptions.yaml", "overrides.yaml", "bank_category_map.yaml"):
        (d / name).unlink()
    assert validate(d, parser_names=PARSERS) == []
    (d / "household.yaml").unlink()
    (d / "accounts.yaml").unlink()
    assert [(p.file, p.path, p.message) for p in validate(d, parser_names=PARSERS)] == [
        (A, "", f"not found in {d}"), (H, "", f"not found in {d}")]


# ------------------------------------------------------------------------------------------------ the parse

@pytest.mark.parametrize("text, fields", [
    ("transfer_rules.yaml: rules[0] (demo-rule): unknown type 'payment' — one of transfer, cc-payment, investment",
     ("transfer_rules.yaml", "rules[0] (demo-rule)", "unknown type 'payment'", "one of transfer, cc-payment, investment")),
    ("household.yaml: top level: expected a mapping — write version, members, joint_label and timezone as `key: "
     "value` lines",
     ("household.yaml", "", "top level: expected a mapping",
      "write version, members, joint_label and timezone as `key: value` lines")),
    ("accounts.yaml: accounts.demo_mortgage_0000.loan: expected a mapping — write the terms as `key: value` lines "
     "under loan:, e.g. `rate: 6.5`",
     ("accounts.yaml", "accounts.demo_mortgage_0000.loan", "expected a mapping",
      "write the terms as `key: value` lines under loan:, e.g. `rate: 6.5`")),
    ("transfer_rules.yaml: venmo_patterns.cashout_income[0]: accounts: expected a list of account ids — list the "
     "accounts.yaml ids it files",
     ("transfer_rules.yaml", "venmo_patterns.cashout_income[0]", "accounts: expected a list of account ids",
      "list the accounts.yaml ids it files")),
    ("overrides.yaml: [0].set.earner: unknown owner 'Taylor' — one of: Alex, Sam, Joint",
     ("overrides.yaml", "[0].set.earner", "unknown owner 'Taylor'", "one of: Alex, Sam, Joint")),
    ("household.yaml: not found in /tmp/a/config — create it with version: 1",
     ("household.yaml", "", "not found in /tmp/a/config", "create it with version: 1")),
    ("keyword_rules.yaml: no fix part", ("keyword_rules.yaml", "", "no fix part", "")),
])
def test_parse_splits_the_rendered_form(text, fields):
    p = Problem.parse(text)
    assert (p.file, p.path, p.message, p.fix) == fields
    assert str(p) == text or not p.fix


def test_the_collector_sorts_by_file_then_path_with_numbers_in_order_and_drops_repeats():
    collected = Problems()
    collected.add("b.yaml", "x", "late", "fix")
    collected.add("a.yaml: members[10].name: missing — give every member a name")
    collected.add(Problem("a.yaml", "members[2].name", "missing", "give every member a name"))
    collected.add("a.yaml", "members[2].name", "missing", "give every member a name")
    collected.extend([Problem("a.yaml", "", "not found", "create it")])
    assert [p.path for p in collected] == ["", "members[2].name", "members[10].name", "x"]
    assert len(collected) == 4 and collected
    error = ConfigError(collected)
    assert isinstance(error, ValueError) and str(error).splitlines() == [str(p) for p in collected]


# ------------------------------------------------------------------------------------------------ unknown keys

# the blocks whose keys the unknown-key check reads from the registry, by file
CHECKED_BLOCKS = {H: ("", "members[]", "net_worth"),
                  A: ("", "accounts.<id>", "accounts.<id>.sources[]", "manual_entries[]", "simplefin", "household")}


@pytest.mark.parametrize("file, block, key", [(f, b, k) for f, blocks in CHECKED_BLOCKS.items() for b in blocks
                                              for k in children(f, b)])
def test_a_one_letter_typo_of_a_known_key_suggests_it(file, block, key):
    [problem] = unknown_key_problems(file, block, block, {key[:-1]: 1})
    assert (problem.message, problem.fix) == ("unknown key", f"did you mean '{key}'?")


def test_an_unknown_key_with_nothing_close_points_at_the_document():
    [problem] = unknown_key_problems(A, "", "", {"zzzz": 1})
    assert problem.fix == "remove it, or see docs/configuration.md for the keys this file takes"


# ------------------------------------------------------------------------------------------------ the sweep

TOP_KEYS = [(file, key) for file in FILES
            for key in (VALID[file] if isinstance(VALID[file], dict) else range(len(VALID[file])))] + \
           [(file, None) for file in FILES]


@pytest.mark.parametrize("value", [None, "text", ["a list"], 5], ids=["null", "text", "list", "number"])
@pytest.mark.parametrize("file, key", TOP_KEYS)
def test_a_block_of_any_shape_is_a_problem_or_reads_as_absent_never_a_crash(tmp_path, file, key, value):
    """Each top-level block of each file (and each whole file) set to null, text, a list and a number: the load
    succeeds or raises ConfigError, whose every problem names its file and has a fix — never another exception."""
    d = write_config(tmp_path, edited([(file, () if key is None else (key,), value)]))
    try:
        Config(d, parser_names=PARSERS, check_paths=True)
    except ConfigError as e:
        assert e.problems and all(p.file and p.message and p.fix for p in e.problems), str(e)


@pytest.mark.parametrize("file, key", [(f, k) for f, k in TOP_KEYS if k is not None and not isinstance(k, int)])
def test_a_null_block_that_loads_reads_as_absent_through_the_whole_run(tmp_path, monkeypatch, file, key):
    """A null block reads as absent: the load finds exactly the problems leaving the key out gives (a required key is
    missing, a statements folder no longer named, rules naming a category only the taxonomy held), and when there are
    none the whole pipeline runs over it."""
    monkeypatch.setitem(pipeline_mod.TXN_PARSERS, "sample_format", _no_rows)
    d = write_config(tmp_path / "null", edited([(file, (key,), None)]))
    absent = validate(write_config(tmp_path / "absent", edited([(file, (key,), DROP)])))
    assert [(p.file, p.path, p.message) for p in validate(d)] == [(p.file, p.path, p.message) for p in absent]
    if absent:
        return
    p = Pipeline(isolate(Config(d), tmp_path / "state"))
    cfg = p.cfg
    p.parse_transactions()
    p.parse_simplefin()
    p.enrich()
    p.parse_assets()
    p.finalize_quality()
    p.export()
    assert (cfg.exports_dir / "manifest.json").exists()


def _no_rows(path, account_id):
    return [], FileStats(source_file=path.name, parser="sample_format"), {}


def _segments(pattern: str) -> list[str]:
    """A registry key path pattern's steps: `keyword_rules[].all[].field` -> keyword_rules, [], all, [], field."""
    return re.findall(r"\[\]|[^.\[\]]+", pattern)


def _place(node, steps: list[str], *, strict: bool) -> tuple | None:
    """A concrete key path in `node` that the steps describe (`[]` any item, `<name>` any key but `version`), its last
    key present unless `strict` is false; None when the document has no such place."""
    if not steps:
        return ()
    head, rest = steps[0], steps[1:]
    if head == "[]":
        candidates = list(enumerate(node)) if isinstance(node, list) else []
    elif not isinstance(node, dict):
        return None
    elif head.startswith("<"):
        candidates = [(k, v) for k, v in node.items() if k != "version"]
    elif head in node:
        candidates = [(head, node[head])]
    else:
        return (head,) if not rest and not strict else None
    for key, child in candidates:
        found = _place(child, rest, strict=strict)
        if found is not None:
            return (key, *found)
    return None


# every key the registry knows below a file's top level, placed in the valid config (an existing occurrence first)
DEEP_TARGETS = [(k.file, k.path, _place(VALID[k.file], _segments(k.path), strict=True)
                 or _place(VALID[k.file], _segments(k.path), strict=False))
                for k in KEYS if len(_segments(k.path)) > 1]


def test_the_valid_config_has_a_place_for_every_key_below_the_top_level():
    assert DEEP_TARGETS and [(f, p) for f, p, at in DEEP_TARGETS if at is None] == []


@pytest.mark.parametrize("value", [None, "text", ["a list"], {"a mapping": 1}, 5],
                         ids=["null", "text", "list", "mapping", "number"])
@pytest.mark.parametrize("file, pattern, at", DEEP_TARGETS, ids=[f"{f}:{p}" for f, p, _ in DEEP_TARGETS])
def test_a_nested_value_of_any_shape_is_a_problem_or_loads_never_a_crash(tmp_path, file, pattern, at, value):
    """Every key the registry knows below the top level (an account's, a manual entry's, a member's, a rule's...), set
    to null, text, a list, a mapping and a number: the load succeeds or raises ConfigError, whose every problem names
    its file and has a fix — never another exception. The targets come from the registry, so a key added later is
    swept too."""
    d = write_config(tmp_path, edited([(file, at, value)]))
    try:
        Config(d, parser_names=PARSERS, check_paths=True)
    except ConfigError as e:
        assert e.problems and all(p.file and p.message and p.fix for p in e.problems), str(e)


# the keyword rule that files every purchase (it would win before any bank label), left out where a label must file
_NO_CATCH_EVERYTHING = (CR, ("keyword_rules",), [r for r in VALID[CR]["keyword_rules"]
                                                 if r.get("name") != "demo-sample-shop"])


def test_null_tags_on_a_bank_label_and_an_account_validate_and_tag_nothing_through_enrich(tmp_path):
    """`tags:` written with no value counts as left out: the validator passes it, and a row that label files on that
    account goes through enrich with no tag."""
    d = write_config(tmp_path, edited([_NO_CATCH_EVERYTHING, (BM, ("Food & Drink", "tags"), None),
                                       (A, ("accounts", CHK, "tags"), None)]))
    assert validate(d, parser_names=PARSERS) == []
    t = make_txn(desc="SAMPLE FLORIST", account=CHK, bank_category="Food & Drink", cents=-2500, date="2030-01-15")
    p = isolated_pipeline(tmp_path / "state", [t], cfg=Config(d, parser_names=PARSERS),
                          windows={CHK: ("2030-01-01", "2030-01-31")})
    p.enrich()
    assert (t.category, t.subcategory, t.category_source, t.tags) == ("Dining", "Restaurants", "bank_label", [])


def test_a_learned_vendor_keeps_a_null_tagged_bank_label_s_tags_as_none():
    cat = Categorizer({}, {"Food & Drink": {"category": "Dining", "tags": None}}, {}, {}, {},
                      learned={"sample florist": {"category": "Dining", "subcategory": "Restaurants"}})
    t = make_txn(desc="SAMPLE FLORIST", vendor="Sample Florist", bank_category="Food & Drink", cents=-2500)
    cat.apply([t], lambda s: s)
    assert (t.category, t.category_source, t.tags) == ("Dining", "learned", [])


# ------------------------------------------------------------------------------------------------ the commands

@pytest.fixture
def cli(monkeypatch):
    """Runs `python -m etl <args>` against a config dir: its exit code."""
    monkeypatch.setitem(pipeline_mod.TXN_PARSERS, "sample_format", _no_rows)

    def run(config_dir: Path, *args: str, **env: str) -> int:
        monkeypatch.setenv("FF_CONFIG_DIR", str(config_dir))
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(sys, "argv", ["etl", *args])
        return main()
    return run


def test_validate_prints_every_problem_sorted_and_exits_1(tmp_path, cli, capsys):
    d = write_config(tmp_path, edited([(H, ("version",), 2), (A, ("accounts", CHK, "kind"), "piggy-bank")]))
    assert cli(d, "validate") == 1
    out = capsys.readouterr()
    assert out.out.splitlines() == [str(p) for p in validate(d)] and len(out.out.splitlines()) == 2
    assert out.err == ""


def test_validate_says_config_ok_with_its_counts_and_exits_0(tmp_path, cli, capsys):
    assert cli(write_config(tmp_path), "validate") == 0
    assert capsys.readouterr().out == "config OK (8 files, 5 accounts)\n"


def test_ingest_on_an_invalid_config_prints_the_same_lines_exits_2_and_writes_nothing(tmp_path, cli, capsys):
    d = write_config(tmp_path, edited([(H, ("timezone",), "localtime"), ("vendor_rules.yaml", ("patterns",), 5)]))
    data, state = tmp_path / "data-here", tmp_path / "state-here"
    data.mkdir()
    state.mkdir()
    (state / "learned_rules.json").write_text('{"version": 1, "rules": []}', "utf-8")
    before = (state / "learned_rules.json").stat().st_mtime_ns
    assert cli(d, "ingest", FF_DATA_DIR=str(data), FF_STATE_DIR=str(state)) == 2
    out = capsys.readouterr()
    assert out.err.splitlines() == [str(p) for p in validate(d)] and len(out.err.splitlines()) == 2
    assert out.out == ""
    assert list(data.iterdir()) == []                                   # no database, no export, no lock
    assert [f.name for f in state.iterdir()] == ["learned_rules.json"]
    assert (state / "learned_rules.json").stat().st_mtime_ns == before


def test_ingest_refuses_a_missing_statements_folder_before_reading_anything(tmp_path, cli, capsys):
    d = write_config(tmp_path, edited([(A, ("source_dir",), "elsewhere")]))
    assert cli(d, "ingest", FF_DATA_DIR=str(tmp_path / "data-here")) == 2
    [line] = capsys.readouterr().err.splitlines()
    assert line.startswith("accounts.yaml: source_dir: the statements folder ") and "FF_IMPORTS_DIR" in line
    assert not (tmp_path / "data-here").exists()


@pytest.mark.parametrize("command", [("rules", "overlaps"), ("rules", "preview", "--file", "candidates.yaml"),
                                     ("ai", "preview")])
def test_rules_on_an_invalid_config_print_the_same_lines_and_exit_2(tmp_path, cli, capsys, command):
    d = write_config(tmp_path, edited([(A, ("accounts", CARD, "simplefin_id"), "SF-DEMO-C0")]))
    assert cli(d, *command) == 2
    assert capsys.readouterr().err.splitlines() == [str(p) for p in validate(d)]


# ------------------------------------------------------------------------------------------------ the feed config

def test_the_feed_config_loads_past_broken_rule_files_and_manual_entries(tmp_path):
    d = write_config(tmp_path, edited([("category_rules.yaml", ("keyword_rules",), 5),
                                       ("vendor_rules.yaml", (), ["x"]),
                                       ("transfer_rules.yaml", ("rules", 0, "type"), "payment"),
                                       (A, ("manual_entries", 0, "value"), "a lot"),
                                       (H, ("members",), None)]))
    assert len(validate(d, parser_names=PARSERS)) == 6           # members missing also says what it leaves unchecked
    cfg = FeedConfig(d)
    assert cfg.simplefin_tz == "America/Denver"
    assert cfg.simplefin_map()["SF-DEMO-B0-OLD"] == BRK
    assert cfg.simplefin_ignore() == {"SF-DEMO-IGNORED"}
    assert cfg.simplefin_dir == tmp_path / "store"
    assert cfg.simplefin_credential_path == tmp_path / "store" / "credentials" / "access_url"


def test_the_feed_config_reports_what_a_fetch_reads(tmp_path):
    d = write_config(tmp_path, edited([(A, ("accounts", CARD, "simplefin_id"), "SF-DEMO-C0"),
                                       (A, ("simplefin", "ignore"), "SF-DEMO-IGNORED"),
                                       (H, ("timezone",), "Mars/Olympus"),
                                       ("category_rules.yaml", ("keyword_rules",), 5)]))
    with pytest.raises(ConfigError) as e:
        FeedConfig(d)
    assert [(p.file, p.path) for p in e.value.problems] == [
        (A, f"accounts.{CARD}.simplefin_id"), (A, "simplefin.ignore"), (H, "timezone")]


def test_the_feed_config_reports_a_missing_or_broken_file(tmp_path):
    d = write_config(tmp_path)
    (d / "accounts.yaml").write_text("accounts: [unclosed\n", "utf-8")
    (d / "household.yaml").unlink()
    with pytest.raises(ConfigError) as e:
        FeedConfig(d)
    assert [(p.file, p.message.split(" (")[0]) for p in e.value.problems] == [
        (A, "not valid YAML"), (H, f"not found in {d}")]


# ------------------------------------------------------------------------------------------------ the document

DOC = ROOT / "docs" / "configuration.md"
_SECTION = re.compile(r"^## `?(?P<file>[a-z_]+\.yaml)`?")
_ROW = re.compile(r"^\| `(?P<path>[^`]+)` \|")


def documented_keys(text: str) -> set[tuple[str, str]]:
    """(file, key path) for each table row of the document, under the `## <file>` heading it sits below; a `## `
    heading that names no config file (the household's own presets) ends the file's section."""
    out, file = set(), None
    for line in text.splitlines():
        if m := _SECTION.match(line):
            file = m["file"]
        elif line.startswith("## "):
            file = None
        elif (m := _ROW.match(line)) and file is not None:
            out.add((file, m["path"]))
    return out


def test_the_document_lists_exactly_the_keys_the_validators_know():
    documented = documented_keys(DOC.read_text("utf-8"))
    registry = {(k.file, k.path) for k in KEYS}
    assert sorted(registry - documented) == [], "keys the validators know that docs/configuration.md does not list"
    assert sorted(documented - registry) == [], "keys docs/configuration.md lists that no validator knows"
    assert {f for f, _ in documented} == set(FILES)


def test_the_document_names_exactly_the_known_modules():
    """household.yaml's `modules` row lists the modules the validator knows (MODULE_IDS), each once and in order, as
    the registry's text, built from MODULE_IDS, does: a module added or removed fails here until the document says
    so."""
    file, row = None, None
    for line in DOC.read_text("utf-8").splitlines():
        if m := _SECTION.match(line):
            file = m["file"]
        elif (m := _ROW.match(line)) and (file, m["path"]) == ("household.yaml", "modules"):
            row = line
    assert row is not None
    named = re.findall(r"The modules: ([^.|]+)\.", row)
    assert len(named) == 1 and named[0].split(", ") == list(MODULE_IDS)
    (key,) = (k for k in KEYS if (k.file, k.path) == ("household.yaml", "modules"))
    assert f"The modules: {', '.join(MODULE_IDS)}." in key.meaning


def test_the_dashboards_modules_are_the_engines():
    """The dashboard gates its pages by the same module ids the validator accepts (app/src/lib/modules.ts MODULE_IDS),
    in the same order: an id on one side only is a switch the config refuses or a page no config can turn on."""
    ts = (ROOT / "app" / "src" / "lib" / "modules.ts").read_text("utf-8")
    m = re.search(r"^export const MODULE_IDS = \[([^\]]*)\] as const;", ts, re.M)
    assert m, "the MODULE_IDS literal moved in app/src/lib/modules.ts: update this pin"
    assert tuple(re.findall(r'"([a-z_]+)"', m[1])) == MODULE_IDS


def test_the_registry_names_each_key_once_with_a_type_a_default_and_a_meaning():
    assert len({(k.file, k.path) for k in KEYS}) == len(KEYS)
    assert all(k.type and k.default and k.meaning for k in KEYS)
    assert {k.file for k in KEYS} == set(FILES)


def document_examples(text: str) -> dict[str, str]:
    """file -> the text of the one ```yaml block under that file's heading (a section's other snippets are fenced
    without the yaml tag)."""
    file, blocks, inside = None, {}, None
    for line in text.splitlines():
        if m := _SECTION.match(line):
            file = m["file"]
        elif line.startswith("## "):
            file = None
        elif line.startswith("```yaml"):
            inside = []
        elif line.startswith("```") and inside is not None:
            assert file is not None and file not in blocks, f"a second yaml example under {file}"
            blocks[file] = "\n".join(inside) + "\n"
            inside = None
        elif inside is not None:
            inside.append(line)
    return blocks


def test_a_config_written_from_the_document_examples_is_valid(tmp_path, monkeypatch):
    """The document's examples are a config a household could copy: each file's example written as that file (the two
    required ones among them, optional files without one left out) validates with no problem, and an ingest of it (no
    statement file, no feed snapshot) writes every export as JSON — a value the YAML loader builds, such as an unquoted
    date in a block the export passes through, reaches no export unconverted."""
    examples = document_examples(DOC.read_text("utf-8"))
    assert {H, A} <= set(examples)
    d = tmp_path / "config"
    d.mkdir()
    (tmp_path / "statements").mkdir()
    for file, text in examples.items():
        assert yaml.safe_load(text) is not None, file
        (d / file).write_text(text, "utf-8")
    assert [str(p) for p in validate(d, parser_names=PARSERS)] == []
    monkeypatch.setenv("FF_HOME", str(tmp_path))
    assert pipeline_mod.run() == 0
    exports_dir = tmp_path / "data" / "exports"
    files = {p.name: json.loads(p.read_text("utf-8")) for p in sorted(exports_dir.glob("*.json"))}
    assert sorted(files) == sorted(files["manifest.json"]["files"]) and len(files) == 14
    cancelled = yaml.safe_load(examples[AS])["cancelled_subscriptions"]
    assert isinstance(cancelled[0]["recorded_on"], dt.date)          # the example writes the day unquoted
    assert files["assumptions.json"]["thresholds"]["cancelled_subscriptions"][0]["recorded_on"] == \
        cancelled[0]["recorded_on"].isoformat()


# ------------------------------------------------------------------------------------------------ values as loaded

def test_every_date_the_config_states_is_handed_on_as_iso_text(tmp_path):
    """An unquoted day is a date to the YAML loader: each date-typed key reaches the engine, and the export, as its
    ISO text."""
    docs = edited([(AS, ("cancelled_subscriptions", 0, "recorded_on"), dt.date(2030, 1, 20)),
                   (A, ("manual_entries", 0, "as_of"), dt.date(2030, 1, 31)),
                   (A, ("accounts", CHK, "simplefin_cutover"), dt.date(2030, 1, 1)),
                   (A, ("accounts", MTG, "loan", "origination", "date"), dt.date(2030, 1, 15)),
                   (OV, (1, "match", "date"), dt.date(2030, 1, 15))])
    d = write_config(tmp_path, docs)
    assert "recorded_on: 2030-01-20\n" in (d / AS).read_text("utf-8")      # written unquoted, so loaded as a date
    cfg = Config(d, parser_names=PARSERS)
    stated = [cfg.assumptions["cancelled_subscriptions"][0]["recorded_on"],
              cfg.accounts_cfg["manual_entries"][0]["as_of"], cfg.accounts[CHK]["simplefin_cutover"],
              cfg.accounts[MTG]["loan"]["origination"]["date"], cfg.overrides[1]["match"]["date"]]
    assert stated == ["2030-01-20", "2030-01-31", "2030-01-01", "2030-01-15", "2030-01-15"]
    assert all(type(v) is str for v in stated)
    out = tmp_path / "exports"
    out.mkdir()
    Pipeline(cfg)._export_assumptions(out)
    doc = json.loads((out / "assumptions.json").read_text("utf-8"))
    assert doc["thresholds"]["cancelled_subscriptions"][0]["recorded_on"] == "2030-01-20"


def test_a_category_written_with_no_value_is_exported_with_no_subcategories(tmp_path):
    d = write_config(tmp_path, edited([(CR, ("taxonomy", "Groceries"), None)]))
    assert validate(d, parser_names=PARSERS) == []
    cfg = Config(d, parser_names=PARSERS)
    assert cfg.taxonomy["Groceries"] == []
    out = tmp_path / "exports"
    out.mkdir()
    Pipeline(cfg)._export_assumptions(out)
    assert json.loads((out / "assumptions.json").read_text("utf-8"))["taxonomy"]["Groceries"] == []


@pytest.mark.parametrize("targets", [{"Cash": 10}, {"Cash": 20, "Stocks/ETF": 60, "Retirement": 30}, {}],
                         ids=["one-class", "above-100", "empty"])
def test_a_target_mix_need_not_add_up_to_100(tmp_path, targets):
    """The dashboard's own editor takes a partial target mix and only warns: the validator checks each class and
    percent, never the sum."""
    assert validate(write_config(tmp_path, edited([(AS, ("targets",), targets)])), parser_names=PARSERS) == []


def test_the_defaults_the_registry_states_are_the_ones_the_code_applies(tmp_path):
    """Every registry key whose default is a plain number or true/false — and the pairing setting — states the value
    the code applies when the key is left out, read from the code (an engine, a categorizer, a config with none of the
    keys), never restated here."""
    d = tmp_path / "config"
    write_household(d, adults=("Alex",))
    (d / A).write_text(yaml.safe_dump({"accounts": {CHK: {"name": "Sample Checking", "institution": "Sample Bank",
                                                          "owner": "Alex", "kind": "checking"}},
                                       "manual_entries": [{"account": "Sample House", "institution": "Sample",
                                                           "owner": "Alex", "asset_class": "Cash", "value": 1,
                                                           "as_of": "2030-01-31"}]}), "utf-8")
    cfg = Config(d, parser_names=PARSERS)
    engine = TransferEngine([], {}, {}, {}, set())
    transfer_rule = compile_transfer_rules(SimpleNamespace(transfer_rules={"rules": [
        {"name": "demo", "pattern": "^DEMO", "type": "transfer", "kinds": ["checking"]}]}, accounts={}))[0]
    keyword = Categorizer({"keyword_rules": [{"pattern": "^SAMPLE", "category": "Dining"},
                                             {"all": [{"field": "description", "op": "contains", "value": "OTHER"}],
                                              "set": {"category": "Dining"}}]}, {}, {}, {}, {})
    big = -100 * keyword.review_threshold
    [house] = [s for s in nw_mod.build_series([], cfg.accounts, [ma_mod.from_config(e)
                                                                 for e in cfg.accounts_cfg["manual_entries"]],
                                              include_credit_cards=True).values() if s.manual]
    applied = {
        **{(k.file, k.path): VERSION for k in KEYS if k.path == "version"},
        (AS, "match_window_bdays"): engine.window,
        (AS, "venmo_match_window_bdays"): engine.venmo_window,
        (AS, "venmo_review_threshold"): engine.venmo_threshold / 100,
        (AS, "uncategorized_review_threshold"): keyword.review_threshold / 100,
        (AS, "unknown_credit_review_threshold"): IncomeIdentifier({}, {}, set()).threshold / 100,
        (AS, "generic_min_amount"): engine.generic_min_cents / 100,
        (AS, "coverage.min_share"): cfg.coverage_min_share,
        **{(AS, f"projections.{k}"): v for k, v in PROJECTION_DEFAULTS.items() if not isinstance(v, tuple)},
        (H, "net_worth.include_credit_cards"): cfg.net_worth_options["include_credit_cards"],
        (H, "refresh.minute"): make_crontab.household_schedule(Paths(d)).minute,
        (H, "ai.min_seen"): cfg.ai.min_seen,
        (H, "ai.confidence"): cfg.ai.confidence,
        (H, "ai.caps.vendors_per_run"): cfg.ai.vendors_per_run,
        (H, "ai.caps.runs_per_day"): cfg.ai.runs_per_day,
        (A, "accounts.<id>.margin"): CHK in cfg.margin_account_ids(),
        (A, "accounts.<id>.mortgage"): cfg.mortgage_account_id() == CHK,
        (A, "manual_entries[].liquid"): house.liquid,
        (CR, "keyword_rules[].needs_review"): keyword.placement(make_txn(desc="SAMPLE 0001", cents=big),
                                                                lambda s: s).review,
        (CR, "keyword_rules[].set.needs_review"): keyword.placement(make_txn(desc="OTHER 0001", cents=big),
                                                                    lambda s: s).review,
        (VR, "patterns[].learn"): "sample vendor" not in vendor_registry.learning_excluded(
            {"patterns": [{"pattern": "^SAMPLE VENDOR", "vendor": "Sample Vendor"}]}),
        (TR, "rules[].needs_review"): transfer_rule.needs_review,
        (TR, "generic_matching"): engine.generic_matching,
        (TR, "venmo_patterns.pairing"): engine.p2p_pairing,
    }
    plain = {(k.file, k.path) for k in KEYS
             if type(yaml.safe_load(k.default)) in (int, float, bool)} | {(TR, "venmo_patterns.pairing")}
    assert sorted(plain - set(applied)) == [], "a plain default with no applied value read here"
    for k in KEYS:
        if (k.file, k.path) in plain:
            assert yaml.safe_load(k.default) == applied[(k.file, k.path)], (k.file, k.path, k.default)


@pytest.mark.parametrize("value, counted", [(None, True), (True, True), (False, False)])
def test_include_credit_cards_written_with_no_value_reads_as_left_out(tmp_path, value, counted):
    d = write_config(tmp_path, edited([(H, ("net_worth", "include_credit_cards"), value)]))
    assert validate(d, parser_names=PARSERS) == []
    assert Config(d, parser_names=PARSERS).net_worth_options == {"include_credit_cards": counted}


# a row the first keyword rule (demo-cafe, legacy shape) files, on an account with no tags of its own, large enough
# for review; and one the structured demo-big-grocer rule files
_CAFE_ROW = {"desc": "SAMPLE CAFE 0001", "account": CARD, "cents": -30000, "date": "2030-01-15"}
_GROCER_ROW = {"desc": "SAMPLE GROCER 0001", "account": CHK, "cents": -40000, "date": "2030-01-15",
               "vendor": "Sample Grocer"}


def _categorized(tmp_path, edits, row) -> Txn:
    d = write_config(tmp_path, edited(edits))
    assert validate(d, parser_names=PARSERS) == []
    t = make_txn(**row)
    pipeline_mod.categorizer_for(Config(d, parser_names=PARSERS)).apply([t], lambda s: s)
    return t


@pytest.mark.parametrize("where, row", [(("keyword_rules", 0, "tags"), _CAFE_ROW),
                                        (("keyword_rules", 1, "set", "tags"), _GROCER_ROW)],
                         ids=["legacy", "structured"])
def test_a_keyword_rule_s_tags_written_with_no_value_read_as_none(tmp_path, where, row):
    t = _categorized(tmp_path, [(CR, where, None)], row)
    assert t.category_source == "keyword" and t.tags == ([] if row is _CAFE_ROW else ["Household"])
    tagged = _categorized(tmp_path / "control", [], row)                 # the control: the rule's own tags
    assert set(tagged.tags) - set(t.tags) == ({"Treats"} if row is _CAFE_ROW else {"Bulk"})


@pytest.mark.parametrize("where, row", [(("keyword_rules", 0, "needs_review"), _CAFE_ROW),
                                        (("keyword_rules", 1, "set", "needs_review"), _GROCER_ROW)],
                         ids=["legacy", "structured"])
def test_a_keyword_rule_s_needs_review_written_with_no_value_reads_as_false(tmp_path, where, row):
    t = _categorized(tmp_path, [(CR, where, None)], row)
    assert t.category_source == "keyword" and t.needs_review is False
    flagged = _categorized(tmp_path / "control", [(CR, where, True)], row)
    assert flagged.needs_review is True


def test_a_valid_config_still_exports_its_owners_and_people(tmp_path):
    """The collector changes nothing a valid config loads into: the exported owners and people stay as they were."""
    cfg = Config(write_config(tmp_path), parser_names=PARSERS)
    out = tmp_path / "exports"
    out.mkdir()
    Pipeline(cfg)._export_assumptions(out)
    doc = json.loads((out / "assumptions.json").read_text("utf-8"))
    assert doc["owners"] == ["Alex", "Sam", "Joint", "Robin"]
    assert [p["role"] for p in doc["household"]["people"]] == ["owner", "owner", "kid"]

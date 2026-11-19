# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The AI step's offline half in the ingest (etl/enrich/ai.py): stored answers applied to the purchases nothing else
filed, with provenance "ai"; never outranking a vendor rule, a learned rule, an answer or an override; never feeding
the learned rules; nothing at all while the step is off or nothing is stored — the exports then the same byte for
byte but for ai_status.json — and ai_status.json itself, counts only.

Invented household (Alex and Sam Sample), invented merchants and answers; nothing here talks to a provider."""
from __future__ import annotations

import json
import os
import shutil
import socket
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from conftest import DEMO_NOW, answer, ingest_demo, isolate, make_txn, write_household

from etl import exports
from etl.ai import cache, policy, providers
from etl.ai import cli as ai_cli
from etl.config import Config, Paths, ai_settings
from etl.enrich import ai as ai_mod
from etl.enrich.categorize import UNCATEGORIZED_REASON
from etl.models import (
    FALLBACK_CATEGORY,
    FEES_CATEGORY,
    INCOME_CATEGORY,
    INTERNAL_CATEGORY,
    P2P_UNKNOWN_SUB,
    UNCATEGORIZED_SUB,
)
from etl.pipeline import Pipeline

CARD, CHECKING, SAVINGS = "demo_card_0303", "demo_checking_0000", "demo_savings_0000"
ACCOUNTS = {
    CARD: {"name": "Sample Card", "institution": "Example Card", "owner": "Alex", "kind": "credit-card"},
    CHECKING: {"name": "Sample Checking", "institution": "Example Bank", "owner": "Joint", "kind": "checking"},
    SAVINGS: {"name": "Sample Savings", "institution": "Example Bank", "owner": "Joint", "kind": "savings"},
}
TAXONOMY = {
    FALLBACK_CATEGORY: [UNCATEGORIZED_SUB, P2P_UNKNOWN_SUB],
    INTERNAL_CATEGORY: [], INCOME_CATEGORY: ["Other"], FEES_CATEGORY: [],
    "Dining": ["Coffee", "Restaurants"],
    "Groceries": [],
    "Home": ["Hardware", "Garden"],
    "Shopping": ["Sample Store"],
}
WINDOWS = {a: ("2026-01-01", "2026-12-31") for a in ACCOUNTS}
LANTERN = "Lantern Hardware"
HARDWARE = {"category": "Home", "subcategory": "Hardware", "confidence": 0.9, "asked_at": "2026-11-18T15:00:00Z"}
ANSWERED_AT = "2026-11-18T16:00:00Z"
SHOP = {"category": "Shopping", "subcategory": "Sample Store"}


# ------------------------------------------------------------------------------------------------------- the rig

def household(tmp_path: Path, *, ai: dict | None = None, vendor_rules: dict | None = None,
              overrides: list | None = None, category_rules: dict | None = None,
              transfer_rules: dict | None = None) -> Config:
    """An invented household with a card, a checking and a savings account and a small taxonomy, its state isolated
    in tmp_path/state (the prefs document beside the answers)."""
    d = tmp_path / "home" / "config"
    write_household(d, adults=("Alex", "Sam"), surname="Sample", **({"ai": ai} if ai is not None else {}))
    (d / "accounts.yaml").write_text(yaml.safe_dump({"accounts": ACCOUNTS}), "utf-8")
    (d / "category_rules.yaml").write_text(yaml.safe_dump(category_rules or {"taxonomy": TAXONOMY}), "utf-8")
    for name, doc in (("vendor_rules.yaml", vendor_rules), ("overrides.yaml", overrides),
                      ("transfer_rules.yaml", transfer_rules)):
        if doc is not None:
            (d / name).write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")
    return isolate(Config(d), tmp_path / "state")


def switch_on(cfg: Config, ai: object = "on") -> None:
    """The household's prefs document with `ai` as the store writes it ("on": switched on for this policy)."""
    if ai == "on":
        ai = {"enabled": True, "consent_policy": policy.POLICY_VERSION, "consent_scope": policy.consent_scope(cfg.ai),
              "consented_at": "2026-11-18T10:00:00Z"}
    path = ai_cli.prefs_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "updated_at": "2026-11-18T10:00:00Z", "ai": ai}), "utf-8")


def store(cfg: Config, entries: dict[str, dict]) -> Path:
    """The stored answers, as a run writes them."""
    where = cache.ready(Paths(cfg.dir).ai_store_dir())
    cache.write(where, cache.Cache(policy.POLICY_VERSION, "anthropic", "claude-opus-5-5", entries))
    return where / cache.CACHE_NAME


def entry(category: str | None = "Home", subcategory: str | None = "Hardware", confidence: float = 0.9) -> dict:
    return {"category": category, "subcategory": subcategory, "confidence": confidence,
            "asked_at": "2026-11-18T15:00:00Z"}


def row(vendor: str = LANTERN, cents: int = -1200, account: str = CARD, date: str = "2026-03-02", **fields):
    """A purchase as a parser leaves it: its vendor set, nothing filed yet."""
    return make_txn(date=date, account=account, desc=f"{vendor.upper()} {date}", cents=cents, vendor=vendor, **fields)


def enrich(cfg: Config, rows: list, *, answers=(), prior: dict | None = None) -> Pipeline:
    if answers:
        isolate(cfg, Path(cfg.answers_path).parent, answers=answers)
    if prior is not None:
        Path(cfg.learned_path).write_text(json.dumps(prior), "utf-8")
    p = Pipeline(cfg)
    p.windows = dict(WINDOWS)
    p.txns = rows
    p.enrich()
    return p


def filed(t) -> tuple:
    return t.category, t.subcategory, t.category_source, t.needs_review, t.review_reason


# --------------------------------------------------------------------------------------------- applying an answer

def test_a_stored_answer_files_every_candidate_row_of_its_vendor_marked_ai(tmp_path):
    """Above the confidence it needs, the answer files each of the vendor's uncategorized purchases: category and
    subcategory, provenance "ai", the review flag the fallback raised cleared with its reason; no family fact."""
    cfg = household(tmp_path)
    switch_on(cfg)
    store(cfg, {"lantern hardware": entry()})
    big, small = row(cents=-30000), row(cents=-1200, date="2026-04-02")
    other = row(vendor="Willow Garden Supply", cents=-30000)
    p = enrich(cfg, [big, small, other])
    assert filed(big) == filed(small) == ("Home", "Hardware", "ai", False, None)
    assert not big.family_fact and not small.family_fact
    assert filed(other) == (FALLBACK_CATEGORY, UNCATEGORIZED_SUB, "fallback", True, UNCATEGORIZED_REASON)
    assert p.ai_status["applied"] == {"vendors": 1, "rows": 2}
    assert p.ai_status["enabled"] is True
    check = next(c for c in p.checks if c.name == "ai.applied")
    assert check.status == "info" and "2 purchases of 1 vendor" in check.detail


def test_a_review_flag_another_stage_raised_is_kept(tmp_path):
    """A purchase flagged before the categorizer's fallback reached it (a parser's flag: no reason recorded) is filed,
    and keeps its flag: the step clears only the flag the fallback raised."""
    cfg = household(tmp_path)
    switch_on(cfg)
    store(cfg, {"lantern hardware": entry()})
    flagged = row(cents=-30000, needs_review=True)
    enrich(cfg, [flagged])
    assert filed(flagged) == ("Home", "Hardware", "ai", True, None)


@pytest.mark.parametrize("stored, threshold, applied", [
    (entry(confidence=0.79), None, False),               # below the default 0.8
    (entry(confidence=0.8), None, True),                 # at it
    (entry(confidence=0.9), 0.95, False),                # below household.yaml ai.confidence
    (entry(confidence=1.0), 0.95, True),
    (entry(None, None, 0.99), None, False),              # the model did not know
])
def test_only_an_answer_at_the_confidence_it_needs_is_applied(tmp_path, stored, threshold, applied):
    cfg = household(tmp_path, ai=None if threshold is None else {"confidence": threshold})
    switch_on(cfg)
    store(cfg, {"lantern hardware": stored})
    t = row()
    p = enrich(cfg, [t])
    assert (t.category_source == "ai") is applied
    assert p.ai_status["skipped"] == {"low_confidence": 0 if applied else 1, "stale_category": 0, "not_candidate": 0}


@pytest.mark.parametrize("category, subcategory", [
    ("Garden Tools", None),                    # no such category any more
    ("Home", "Lighting"),                      # no such subcategory
    (INCOME_CATEGORY, "Other"),                # not a spending category
    (INTERNAL_CATEGORY, None),
    (FEES_CATEGORY, None),
    (FALLBACK_CATEGORY, UNCATEGORIZED_SUB),    # the fallback's own pair
    (FALLBACK_CATEGORY, P2P_UNKNOWN_SUB),
    (FALLBACK_CATEGORY, None),                 # the catch-all itself, though a subcategory of it may be asked
])
def test_an_answer_naming_no_spending_category_of_the_household_is_skipped(tmp_path, category, subcategory):
    """The fallback category here holds a subcategory of the household's own besides the reserved two, so it is in
    the answer set: the catch-all with no subcategory is still never an answer."""
    taxonomy = {**TAXONOMY, FALLBACK_CATEGORY: [UNCATEGORIZED_SUB, P2P_UNKNOWN_SUB, "Odd Jobs"]}
    cfg = household(tmp_path, category_rules={"taxonomy": taxonomy})
    switch_on(cfg)
    store(cfg, {"lantern hardware": entry(category, subcategory)})
    t = row(cents=-30000)
    p = enrich(cfg, [t])
    assert filed(t) == (FALLBACK_CATEGORY, UNCATEGORIZED_SUB, "fallback", True, UNCATEGORIZED_REASON)
    assert p.ai_status["skipped"] == {"low_confidence": 0, "stale_category": 1, "not_candidate": 0}


def test_a_category_alone_is_a_spending_category_as_a_learned_rule_may_name_one(tmp_path):
    cfg = household(tmp_path)
    switch_on(cfg)
    store(cfg, {"lantern hardware": entry("Home", None)})
    t = row()
    enrich(cfg, [t])
    assert (t.category, t.subcategory, t.category_source) == ("Home", None, "ai")


def test_answers_follow_the_households_vendor_and_category_renames(tmp_path):
    """A vendor renamed since it was asked about keeps its answer under the new name, and a renamed category is filed
    under its new name: as learned rules follow them."""
    rules = {"taxonomy": {**TAXONOMY, "House": ["Tools"]},
             "renames": [{"from": {"category": "Home", "subcategory": "Hardware"},
                          "to": {"category": "House", "subcategory": "Tools"}}]}
    cfg = household(tmp_path, vendor_rules={"renames": [{"from": "Lantern Hdwe", "to": LANTERN}]},
                    category_rules=rules)
    switch_on(cfg)
    store(cfg, {"lantern hdwe": entry()})
    t = row()
    enrich(cfg, [t])
    assert (t.category, t.subcategory, t.category_source) == ("House", "Tools", "ai")


def test_two_answers_a_rename_makes_one_vendor_keep_the_one_stored_under_the_new_name(tmp_path):
    cfg = household(tmp_path, vendor_rules={"renames": [{"from": "Lantern Hdwe", "to": LANTERN}]})
    switch_on(cfg)
    store(cfg, {"lantern hdwe": entry("Home", "Garden"), "lantern hardware": entry("Home", "Hardware")})
    t = row()
    enrich(cfg, [t])
    assert (t.category, t.subcategory) == ("Home", "Hardware")


def test_an_answer_for_a_vendor_with_no_candidate_row_is_counted_and_touches_nothing(tmp_path):
    """A stored answer whose vendor has no uncategorized purchase now — and the rows that are no candidate: a payment
    app's, an unpaired transfer leg's, one on a checking account while the step asks about cards only."""
    cfg = household(tmp_path)
    switch_on(cfg)
    store(cfg, {"gone vendor": entry(), "venmo payment": entry(), "lantern hardware": entry()})
    on_checking = row(account=CHECKING)
    p2p = row(vendor="Venmo Payment")
    p = enrich(cfg, [on_checking, p2p])
    assert on_checking.category_source == "fallback" and p2p.category_source != "ai"
    assert p.ai_status["skipped"]["not_candidate"] == 3 and p.ai_status["applied"] == {"vendors": 0, "rows": 0}


def test_ai_accounts_all_reaches_every_account(tmp_path):
    cfg = household(tmp_path, ai={"accounts": "all"})
    switch_on(cfg)
    store(cfg, {"lantern hardware": entry()})
    t = row(account=CHECKING)
    enrich(cfg, [t])
    assert t.category_source == "ai"


# --------------------------------------------------------------------------------------------------- precedence

@pytest.mark.parametrize("how", ["vendor rule", "learned rule", "answer for the row", "answer for the vendor",
                                 "override"])
def test_the_households_own_filing_outranks_a_stored_answer(tmp_path, how):
    """A vendor later covered by a vendor rule, a learned rule, a review answer or an override is filed by that, not by
    the stored answer, whatever its confidence."""
    vendor_rules = ({"patterns": [{"pattern": "LANTERN HARDWARE", "vendor": LANTERN, "category": "Shopping",
                                   "subcategory": "Sample Store"}]} if how == "vendor rule" else None)
    overrides = [{"match": {"vendor": LANTERN}, "set": SHOP}] if how == "override" else None
    cfg = household(tmp_path, vendor_rules=vendor_rules, overrides=overrides)
    switch_on(cfg)
    store(cfg, {"lantern hardware": entry(confidence=1.0)})
    t = row(cents=-30000)
    answers = ([answer("txn", t, SHOP, ANSWERED_AT)] if how == "answer for the row"
               else [answer("vendor", LANTERN, SHOP, ANSWERED_AT)] if how == "answer for the vendor" else ())
    prior = ({"version": 1, "updated_at": ANSWERED_AT, "history": [], "rules": [
        {"vendor": LANTERN, "category": "Shopping", "subcategory": "Sample Store", "learned_at": ANSWERED_AT,
         "last_confirmed_at": ANSWERED_AT, "evidence": {"scope": "txn", "n_agree": 3, "n_total": 3,
                                                        "answer_keys": []}}]} if how == "learned rule" else None)
    p = enrich(cfg, [t], answers=answers, prior=prior)
    source = {"vendor rule": "vendor_rule", "learned rule": "learned", "override": "override"}.get(how, "answer")
    assert (t.category, t.subcategory, t.category_source) == ("Shopping", "Sample Store", source)
    assert p.ai_status["applied"] == {"vendors": 0, "rows": 0}


def test_an_override_runs_after_the_step_and_replaces_its_category(tmp_path, monkeypatch):
    """Overrides apply after the AI step (after the transfers): the row the stored answer filed is re-filed by the
    override, its provenance the override's."""
    seen = []
    cfg = household(tmp_path, overrides=[{"match": {"vendor": LANTERN}, "set": SHOP}])
    switch_on(cfg)
    store(cfg, {"lantern hardware": entry()})
    t = row()
    real = ai_mod.apply

    def watched(c, txns):
        out = real(c, txns)
        seen.append(filed(t))
        return out

    monkeypatch.setattr(ai_mod, "apply", watched)
    enrich(cfg, [t])
    assert seen == [("Home", "Hardware", "ai", False, None)]
    assert (t.category, t.subcategory, t.category_source) == ("Shopping", "Sample Store", "override")


def test_the_stored_answers_never_become_learned_rules_or_answers(tmp_path, monkeypatch):
    """Ingesting with stored answers applied leaves the learned rules and the answers exactly as ingesting without:
    the step writes neither, and learned.derive never reads an AI row as evidence. The clock is pinned, so the two
    files' write stamps cannot differ by the second between the two ingests."""
    monkeypatch.setenv("FF_NOW", "2026-11-18T16:00:00Z")

    def ingest(name: str, on: bool) -> tuple[bytes, bytes]:
        cfg = household(tmp_path / name)
        if on:
            switch_on(cfg)
            store(cfg, {"lantern hardware": entry(), "willow garden supply": entry("Home", "Garden")})
        rows = [row(), row(date="2026-04-02"), row(vendor="Willow Garden Supply"),
                row(vendor="Blue Heron Bakery")]
        isolate(cfg, Path(cfg.answers_path).parent,
                answers=[answer("vendor", "Blue Heron Bakery", {"category": "Dining", "subcategory": "Coffee"},
                                ANSWERED_AT)])
        p = enrich(cfg, rows)
        assert sum(t.category_source == "ai" for t in rows) == (3 if on else 0)
        p.finalize_quality()
        p.persist_learned()
        return Path(cfg.learned_path).read_bytes(), Path(cfg.answers_path).read_bytes()

    assert ingest("on", True) == ingest("off", False)


# ------------------------------------------------------------------------------- the transfer matcher's question

def test_no_pairing_reads_a_category_the_step_sets(tmp_path):
    """The step runs after every pairing phase: two rows nothing explained still pair however confidently their
    vendors are answered (an answered vendor is no "the household's rules explain this row"), and a credit the income
    fallback typed is still a candidate. The control: the outflow alone is filed by its answer."""
    cfg = household(tmp_path, ai={"accounts": "all"}, transfer_rules={"rules": [], "generic_matching": True})
    switch_on(cfg)
    store(cfg, {"demo out": entry(confidence=1.0), "demo deposit": entry("Groceries", None, 1.0)})
    out = row(vendor="Demo Out", account=CHECKING, cents=-60000, date="2026-03-02")
    inn = row(vendor="Demo Deposit", account=SAVINGS, cents=60000, date="2026-03-03")
    enrich(cfg, [out, inn])
    assert out.transfer_group_id is not None and out.transfer_group_id == inn.transfer_group_id
    assert (out.type, inn.type, out.category_source, inn.category_source) == ("transfer", "transfer", "internal",
                                                                               "internal")
    alone = row(vendor="Demo Out", account=CHECKING, cents=-60000, date="2026-03-02")
    enrich(household(tmp_path / "alone", ai={"accounts": "all"}), [alone])
    assert alone.category_source == "fallback"                   # off there: nothing stored, nothing switched on
    cfg = household(tmp_path / "alone-on", ai={"accounts": "all"})
    switch_on(cfg)
    store(cfg, {"demo out": entry(confidence=1.0)})
    alone = row(vendor="Demo Out", account=CHECKING, cents=-60000, date="2026-03-02")
    enrich(cfg, [alone])
    assert alone.category_source == "ai"


# --------------------------------------------------------------------------------------------------- off is off

@pytest.mark.parametrize("prefs", ["missing", "disabled", "another policy", "policy as text", "enabled as text",
                                   "not a mapping", "not json"])
def test_while_the_step_is_off_no_stored_answer_is_read_and_no_row_touched(tmp_path, monkeypatch, prefs):
    cfg = household(tmp_path)
    if prefs == "not json":
        path = ai_cli.prefs_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{", "utf-8")
    elif prefs != "missing":
        at = {"consent_scope": policy.consent_scope(cfg.ai), "consented_at": "2026-11-18T10:00:00Z"}
        switch_on(cfg, {"disabled": {"enabled": False, "consent_policy": policy.POLICY_VERSION, **at},
                        "another policy": {"enabled": True, "consent_policy": policy.POLICY_VERSION + 1, **at},
                        "policy as text": {"enabled": True, "consent_policy": str(policy.POLICY_VERSION), **at},
                        "enabled as text": {"enabled": "true", "consent_policy": policy.POLICY_VERSION, **at},
                        "not a mapping": [True]}[prefs])
    store(cfg, {"lantern hardware": entry()})

    def refused(*_):
        raise AssertionError("the stored answers were read while the step is off")

    monkeypatch.setattr(ai_mod.cache, "read", refused)
    rows = [row(cents=-30000)]
    p = enrich(cfg, rows)
    assert filed(rows[0]) == (FALLBACK_CATEGORY, UNCATEGORIZED_SUB, "fallback", True, UNCATEGORIZED_REASON)
    assert p.ai_status["enabled"] is False and not any(c.name == "ai.applied" for c in p.checks)


@pytest.mark.parametrize("ai, scope", [
    ({"accounts": "all"}, "anthropic||all"),
    ({"provider": "openai", "model": "gpt-sample"}, "openai||cards"),
    ({"provider": "openai", "model": "m", "base_url": "http://127.0.0.1:11434/v1"},
     "openai|127.0.0.1:11434/v1|m|cards"),
    ({"provider": "openai", "model": "m", "base_url": "https://llm.example.net/v1"}, "openai|llm.example.net/v1|m|cards"),
])
def test_consent_given_before_the_household_named_another_service_or_more_accounts_applies_nothing(
        tmp_path, monkeypatch, ai, scope):
    """The family agreed to the default scope (Anthropic, cards); household.yaml then names another recipient or every
    account: the step is off, as if what is sent had changed, until the family agrees to the new scope."""
    cfg = household(tmp_path, ai=ai)
    assert policy.consent_scope(cfg.ai) == scope
    switch_on(cfg, {"enabled": True, "consent_policy": policy.POLICY_VERSION, "consent_scope": "anthropic||cards",
                    "consented_at": "2026-11-18T10:00:00Z"})
    store(cfg, {"lantern hardware": entry()})
    reads = []
    monkeypatch.setattr(ai_mod.cache, "read", lambda *a: reads.append(a) or (None, None))
    t = row(account=CHECKING if ai.get("accounts") == "all" else CARD)
    p = enrich(cfg, [t])
    assert t.category_source == "fallback" and reads == []
    assert p.ai_status["enabled"] is False and p.ai_status["scope"] == scope
    assert ai_cli.prefs_path(cfg).exists()
    assert cache.consent(ai_cli.prefs_path(cfg), policy.POLICY_VERSION, scope) == "stale"


def test_the_scope_names_the_service_and_the_accounts_and_always_fits_the_store():
    from etl.config import AiSettings
    assert policy.consent_scope(AiSettings()) == "anthropic||cards"
    assert policy.consent_scope(AiSettings(model="another-model", accounts="all")) == "anthropic||all"
    assert policy.consent_scope(AiSettings(provider="openai", model="gpt-sample")) == "openai||cards"
    assert policy.consent_scope(AiSettings(provider="openai", model="m",
                                           base_url="https://u:p@LLM.example.net:8443/v1/?k=1#f")) \
        == "openai|llm.example.net:8443/v1/|m|cards"


def test_on_a_gateway_the_model_and_the_path_are_in_the_scope_and_on_a_providers_own_api_the_model_is_not():
    """A gateway's path or model can pick the company that receives the names: either changing asks again."""
    from etl.config import AiSettings
    gateway = AiSettings(provider="openai", model="vendor-a/model-1", base_url="https://gw.example.net/route-a/v1")
    scopes = {policy.consent_scope(s) for s in (
        gateway, replace(gateway, model="vendor-b/model-1"),
        replace(gateway, base_url="https://gw.example.net/route-b/v1"),
        replace(gateway, base_url="https://gw.example.net:8443/route-a/v1"),
        replace(gateway, accounts="all"))}
    assert len(scopes) == 5
    assert policy.consent_scope(gateway) == "openai|gw.example.net/route-a/v1|vendor-a/model-1|cards"
    # a "|" or "%" in a field cannot make two settings read alike
    assert policy.consent_scope(replace(gateway, model="a|b", base_url="https://gw.example.net/v1")) \
        != policy.consent_scope(replace(gateway, model="b", base_url="https://gw.example.net/v1|a"))
    assert policy.consent_scope(replace(gateway, model="a|b")) != policy.consent_scope(replace(gateway, model="a%7Cb"))
    own = AiSettings(provider="anthropic")
    assert policy.consent_scope(own) == policy.consent_scope(replace(own, model="another-model"))


@pytest.mark.parametrize("base_url, model", [
    ("https://" + "a" * 250 + ".example.net/v1", "m"),
    ("https://gw.example.net/" + "p" * 300, "m"),
    ("https://gw.example.net/v1", "vendor/" + "m" * 300),
])
def test_a_scope_too_long_for_the_store_is_cut_to_fit_and_ends_with_a_hash_of_the_whole(base_url, model):
    from etl.config import AiSettings
    s = AiSettings(provider="openai", model=model, base_url=base_url, accounts="all")
    scope = policy.consent_scope(s)
    assert len(scope) == policy.SCOPE_MAX and scope.startswith("openai|") and scope.endswith("|all")
    assert scope == policy.consent_scope(s)                                    # deterministic
    head = scope[:-len("|all")]
    marker, digest = head[-policy.SCOPE_HASH - 1], head[-policy.SCOPE_HASH:]
    assert marker == "~" and all(c in "0123456789abcdef" for c in digest)
    # two settings cut alike still differ, by the hash of what was cut
    other = replace(s, model=model + "x") if len(model) > 100 else replace(s, base_url=base_url + "x")
    cut = policy.consent_scope(other)
    assert len(cut) == policy.SCOPE_MAX and cut != scope and cut[:-len(digest) - 5] == scope[:-len(digest) - 5]


def test_switched_on_with_nothing_stored_touches_no_row(tmp_path):
    cfg = household(tmp_path)
    switch_on(cfg)
    t = row(cents=-30000)
    p = enrich(cfg, [t])
    assert filed(t) == (FALLBACK_CATEGORY, UNCATEGORIZED_SUB, "fallback", True, UNCATEGORIZED_REASON)
    assert p.ai_status["enabled"] is True and p.ai_status["applied"] == {"vendors": 0, "rows": 0}
    assert not any(c.name == "ai.applied" for c in p.checks)


def damage(path: Path, how: str) -> None:
    """Spoil the stored answers file at `path` one way."""
    if how == "an empty file":
        path.write_bytes(b"")
    elif how == "no answers in it":
        cache.write(path.parent, cache.Cache(policy.POLICY_VERSION, "anthropic", "claude-opus-5-5", {}))
    elif how == "not json":
        path.write_text("{", "utf-8")
    elif how == "not an answers file":
        path.write_text("[1, 2]", "utf-8")
    elif how == "a folder":
        path.unlink()
        path.mkdir()
    else:
        real = path.with_name("elsewhere.json")
        path.rename(real)
        path.symlink_to(real)


DAMAGE = ["an empty file", "no answers in it", "not json", "not an answers file", "a folder", "a link"]


@pytest.mark.parametrize("how", DAMAGE)
def test_stored_answers_that_are_empty_or_unusable_count_as_none_stored(tmp_path, how):
    """Switched on, an answers file that is empty, a link, a folder or not an answers file applies nothing and is as
    if nothing were stored: no quality line, so the exports are those of the step with nothing to do."""
    cfg = household(tmp_path)
    switch_on(cfg)
    damage(store(cfg, {"lantern hardware": entry()}), how)
    t = row(cents=-30000)
    p = enrich(cfg, [t])
    assert filed(t) == (FALLBACK_CATEGORY, UNCATEGORIZED_SUB, "fallback", True, UNCATEGORIZED_REASON)
    assert p.ai_status["enabled"] is True and p.ai_status["applied"] == {"vendors": 0, "rows": 0}
    assert not any(c.name == "ai.applied" for c in p.checks)


def test_stored_answers_with_some_left_out_still_apply_and_the_check_says_so(tmp_path):
    cfg = household(tmp_path)
    switch_on(cfg)
    path = store(cfg, {"lantern hardware": entry()})
    doc = json.loads(path.read_text("utf-8"))
    doc["entries"]["willow garden supply"] = {"category": 7}
    path.write_text(json.dumps(doc), "utf-8")
    t = row()
    p = enrich(cfg, [t])
    assert t.category_source == "ai"
    check = next(c for c in p.checks if c.name == "ai.applied")
    assert "could not be read" in check.detail


# ------------------------------------------------------------------------------------------------ ai_status.json

def test_the_status_is_counts_only_and_says_what_is_configured(tmp_path):
    cfg = household(tmp_path, ai={"caps": {"vendors_per_run": 20, "runs_per_day": 2}})
    switch_on(cfg)
    store(cfg, {"lantern hardware": entry(), "copper kettle cafe": entry("Dining", "Coffee", 0.5)})
    where = Paths(cfg.dir).ai_store_dir()
    cache.log_run(where, {"time": "2026-11-18T15:00:00Z", "provider": "anthropic", "model": "claude-opus-5-5",
                          "policy": 1, "outcome": "answered", "sent": 2, "answered": 2, "dropped": {}, "cached": 2,
                          "vendors": ["lantern hardware", "copper kettle cafe"]})
    rows = [row(), row(date="2026-04-02"), row(vendor="Copper Kettle Cafe"), row(vendor="Copper Kettle Cafe",
                                                                                  date="2026-04-03"),
            row(vendor="Robin Sample Tutoring"), row(vendor="Robin Sample Tutoring", date="2026-04-04"),
            row(vendor="Once Only Shop")]
    p = enrich(cfg, rows)
    doc = exports.export_ai_status(p.ai_status)
    assert doc == {
        "configured": False, "enabled": True, "provider": "anthropic", "model": "claude-opus-5-5",
        "endpoint_host": None, "policy_version": policy.POLICY_VERSION, "scope": "anthropic||cards",
        "consent_policy": policy.POLICY_VERSION,
        "caps": {"vendors_per_run": 20, "runs_per_day": 2},
        "last_run": {"at": "2026-11-18T15:00:00Z", "outcome": "answered", "sent": 2, "answered": 2, "cached": 2},
        "answers_stored": True, "applied": {"vendors": 1, "rows": 2},
        "skipped": {"low_confidence": 1, "stale_category": 0, "not_candidate": 0},
        # the preview reads the rows as they stand without the step's categories: lantern and copper kettle are sent,
        # the vendor holding a member's name and the one seen once are kept back
        "preview": {"vendors": 2, "withheld": {"name": 1, "digits": 0, "address": 0, "length": 0, "seen": 1}},
    }
    text = json.dumps(doc)
    assert not any(v in text.casefold() for v in ("lantern", "copper", "robin", "once only"))
    assert list(doc) == list(exports.AI_STATUS_KEYS)


@pytest.mark.parametrize("ai, host", [
    (None, None),                                                                          # anthropic's own API
    ({"provider": "openai", "model": "gpt-sample"}, None),                                 # openai's own API
    ({"provider": "openai", "model": "m", "base_url": "http://127.0.0.1:11434/v1"}, "127.0.0.1:11434"),
    ({"provider": "openai", "model": "m", "base_url": "https://llm.example.net/v1/"}, "llm.example.net"),
    ({"provider": "openai", "model": "m", "base_url": "HTTPS://LLM.Example.NET:8443/a/b"}, "llm.example.net:8443"),
    ({"provider": "openai", "model": "m", "base_url": "http://[::1]:8080/v1"}, "[::1]:8080"),
])
def test_the_status_names_an_openai_compatible_services_host_and_nothing_more_of_its_address(tmp_path, ai, host):
    cfg = household(tmp_path, ai=ai)
    p = enrich(cfg, [row()])
    assert p.ai_status["endpoint_host"] == host


@pytest.mark.parametrize("how, stored", [("none", False), ("answers", True), ("an empty file", False),
                                         ("a link", False), ("a folder", False)])
def test_the_status_says_whether_answers_are_stored_whatever_the_switch_without_reading_them(tmp_path, monkeypatch,
                                                                                            how, stored):
    cfg = household(tmp_path)
    if how != "none":
        path = store(cfg, {"lantern hardware": entry()})
        if how != "answers":
            damage(path, how)

    def refused(*_):
        raise AssertionError("the stored answers were read while the step is off")

    monkeypatch.setattr(ai_mod.cache, "read", refused)
    p = enrich(cfg, [row()])
    assert p.ai_status["enabled"] is False and p.ai_status["answers_stored"] is stored


@pytest.mark.parametrize("key, configured", [("none", False), ("owner only", True), ("readable by others", False),
                                             ("empty", False), ("a link", False), ("a folder", False)])
def test_configured_says_whether_a_usable_key_file_is_stored_without_reading_it(tmp_path, monkeypatch, key,
                                                                               configured):
    cfg = household(tmp_path)
    path = Paths(cfg.dir).secrets_ai_dir() / ai_mod.KEY_NAME
    path.parent.mkdir(parents=True)
    if key == "a folder":
        path.mkdir()
    elif key == "a link":
        real = path.with_name("real")
        real.write_text("sk-invented\n")
        real.chmod(0o600)
        path.symlink_to(real)
    elif key != "none":
        path.write_text("" if key == "empty" else "sk-invented\n")
        path.chmod(0o644 if key == "readable by others" else 0o600)
    opened = []
    real_open = os.open
    monkeypatch.setattr(os, "open", lambda p, *a, **k: (opened.append(str(p)), real_open(p, *a, **k))[1])
    p = enrich(cfg, [row()])
    assert p.ai_status["configured"] is configured
    assert not any(o.startswith(str(path.parent)) for o in opened)


def test_the_key_file_name_is_the_one_a_run_reads():
    assert ai_mod.KEY_NAME == providers.KEY_NAME


def test_the_run_outcomes_the_status_reports_are_every_outcome_a_run_logs():
    assert set(cache.RUN_OUTCOMES) == {"asked", "answered", "not_stored", *providers.FAILURES}


@pytest.mark.parametrize("line", [
    '{"time": "lantern hardware", "outcome": "answered", "sent": 1, "answered": 1, "cached": 1}',
    '{"time": "2026-11-18T15:00:00Z", "outcome": "lantern hardware", "sent": 1, "answered": 1, "cached": 1}',
    '{"time": "2026-11-18T15:00:00Z", "outcome": "answered", "sent": "lantern", "answered": 1, "cached": 1}',
    '{"time": "2026-11-18T15:00:00Z", "outcome": "answered", "sent": true, "answered": 1, "cached": 1}',
    'not json',
])
def test_a_run_log_line_that_is_not_a_runs_counts_is_never_reported(tmp_path, line):
    """Only a whole run's line reaches the status: a hand-edited log can never carry a vendor into it. The line
    before it, a run's, is what the status reports."""
    where = tmp_path / "ai"
    where.mkdir()
    good = '{"time": "2026-11-17T15:00:00Z", "outcome": "rejected_key", "sent": 3, "answered": 0, "cached": 0}'
    (where / cache.RUNS_NAME).write_text(f"{good}\n{line}\n", "utf-8")
    assert cache.last_run(where) == {"at": "2026-11-17T15:00:00Z", "outcome": "rejected_key", "sent": 3,
                                     "answered": 0, "cached": 0}


def test_a_run_log_that_is_a_link_is_no_last_run(tmp_path):
    where = tmp_path / "ai"
    where.mkdir()
    real = tmp_path / "log"
    real.write_text('{"time": "2026-11-17T15:00:00Z", "outcome": "answered", "sent": 1, "answered": 1, "cached": 1}\n')
    (where / cache.RUNS_NAME).symlink_to(real)
    assert cache.last_run(where) is None


def test_the_python_ai_pass_asks_about_rows_the_stored_answers_would_file(tmp_path):
    """`python -m etl ai`'s own pipeline pass leaves the stored answers unapplied, so an answered vendor is still one
    it may ask about again (`--refresh`) and the preview shows what the household's rules leave alone."""
    cfg = household(tmp_path)
    switch_on(cfg)
    store(cfg, {"lantern hardware": entry()})
    p = Pipeline(cfg)
    p.windows = dict(WINDOWS)
    p.txns = [row(), row(date="2026-04-02")]
    p.ai_apply = False
    p.enrich()
    assert {t.category_source for t in p.txns} == {"fallback"}
    assert p.ai_status["preview"]["vendors"] == 1


@pytest.mark.parametrize("state", ["not switched on", "switched on, no key"])
def test_the_refreshs_ai_run_opens_no_socket_and_says_nothing_while_the_step_is_off(tmp_path, monkeypatch, state):
    """What refresh.sh runs when a key is stored, `python -m etl ai run --from-refresh`, with the step not switched
    on or no key: exit 0, no line, and no socket opened (socket.socket refuses)."""
    cfg = household(tmp_path)
    if state == "not switched on":
        key = Paths(cfg.dir).secrets_ai_dir() / providers.KEY_NAME
        key.parent.mkdir(parents=True)
        key.write_text("sk-invented-not-a-key\n")
        key.chmod(0o600)
    else:
        switch_on(cfg)

    def no_socket(*_a, **_k):
        raise AssertionError("a socket was opened")

    monkeypatch.setattr(socket, "socket", no_socket)
    lines: list[str] = []
    assert ai_cli.run(cfg, from_refresh=True, txns=[row(), row(date="2026-04-02")], out=lines.append) == 0
    assert lines == []
    assert not Paths(cfg.dir).ai_store_dir().exists()


# --------------------------------------------------------------------------- the demo: off is byte for byte off

def _ingest_copy(demo_home, tmp_path, name: str, *, ai: dict | None = None, prefs: object = None,
                 entries: dict | None = None, spoil: str | None = None) -> Path:
    """A copy of the demo tree, with an `ai` block in its household.yaml, a prefs document and stored answers (spoilt
    one of `damage`'s ways when asked) as asked, ingested at the demo's pinned clock; its root."""
    home = tmp_path / name
    shutil.copytree(demo_home.path, home)
    if ai is not None:
        path = home / "config" / "household.yaml"
        doc = yaml.safe_load(path.read_text("utf-8"))
        doc["ai"] = ai
        path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), "utf-8")
    if prefs is not None:
        if prefs == "on":
            settings = ai_settings(yaml.safe_load((home / "config" / "household.yaml").read_text("utf-8")))
            prefs = {"enabled": True, "consent_policy": policy.POLICY_VERSION,
                     "consent_scope": policy.consent_scope(settings), "consented_at": "2026-10-31T10:00:00Z"}
        (home / "state" / "family").mkdir(parents=True, exist_ok=True)
        (home / "state" / "family" / "prefs.json").write_text(
            json.dumps({"version": 1, "updated_at": "2026-10-31T10:00:00Z", "ai": prefs}), "utf-8")
    if entries is not None:
        where = cache.ready(home / "store" / "ai")
        cache.write(where, cache.Cache(policy.POLICY_VERSION, "anthropic", "claude-opus-5-5", entries))
        if spoil is not None:
            damage(where / cache.CACHE_NAME, spoil)
    ingest_demo(home, DEMO_NOW)
    return home


def _files(home: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted((home / "data" / "exports").iterdir())}


# the demo's one uncategorized vendor on a checking account, which `ai.accounts: all` makes a candidate
TALLGRASS = "tallgrass general store"


def test_the_demo_exports_with_the_step_off_or_nothing_stored_differ_in_ai_status_alone(demo_home, tmp_path,
                                                                                      monkeypatch):
    """The demo household ingested as generated, then with every way of the step being off or having nothing to
    apply: every export byte for byte the same but ai_status.json, and the learned rules and answers the same too.
    The control: switched on with an answer stored, its rows change. No socket is opened by any of these ingests."""
    def no_socket(*_a, **_k):
        raise AssertionError("an ingest opened a socket")

    monkeypatch.setattr(socket, "socket", no_socket)
    stored = {TALLGRASS: entry("Groceries", None, 0.95)}
    every = {"accounts": "all"}
    base = _ingest_copy(demo_home, tmp_path, "as-generated")
    runs = {
        "stored, never switched on": _ingest_copy(demo_home, tmp_path, "a", ai=every, entries=stored),
        "switched off": _ingest_copy(demo_home, tmp_path, "b", ai=every, entries=stored,
                                     prefs={"enabled": False, "consent_policy": policy.POLICY_VERSION,
                                            "consent_scope": "anthropic||all", "consented_at": None}),
        "agreed to another policy": _ingest_copy(demo_home, tmp_path, "c", ai=every, entries=stored,
                                                 prefs={"enabled": True, "consent_policy": policy.POLICY_VERSION + 1,
                                                        "consent_scope": "anthropic||all",
                                                        "consented_at": "2026-10-31T10:00:00Z"}),
        "agreed to cards only, now all accounts": _ingest_copy(
            demo_home, tmp_path, "c2", ai=every, entries=stored,
            prefs={"enabled": True, "consent_policy": policy.POLICY_VERSION, "consent_scope": "anthropic||cards",
                   "consented_at": "2026-10-31T10:00:00Z"}),
        "on, nothing stored": _ingest_copy(demo_home, tmp_path, "d", ai=every, prefs="on"),
        **{f"on, stored answers {how}": _ingest_copy(demo_home, tmp_path, f"e{i}", ai=every, prefs="on",
                                                    entries=stored, spoil=how)
           for i, how in enumerate(("an empty file", "a link", "not json"))},
    }
    want = {k: v for k, v in _files(base).items() if k != "ai_status.json"}
    assert "transactions.json" in want and len(want) == 13
    for name, home in runs.items():
        got = _files(home)
        assert "ai_status.json" in got, name
        assert {k: v for k, v in got.items() if k != "ai_status.json"} == want, name
        for state in ("engine/learned_rules.json", "family/answers.json"):
            assert ((home / "state" / state).read_bytes() if (home / "state" / state).exists() else None) == \
                ((base / "state" / state).read_bytes() if (base / "state" / state).exists() else None), (name, state)
    status = json.loads((base / "data" / "exports" / "ai_status.json").read_text("utf-8"))
    assert (status["configured"], status["enabled"], status["last_run"], status["applied"]) == (
        False, False, None, {"vendors": 0, "rows": 0})
    on = _ingest_copy(demo_home, tmp_path, "on", ai=every, entries=stored, prefs="on")
    got = _files(on)
    assert got["transactions.json"] != want["transactions.json"]
    rows = json.loads(got["transactions.json"])["transactions"]
    mine = [t for t in rows if (t["vendor"] or "").casefold() == TALLGRASS]
    assert mine and all((t["category"], t["category_source"]) == ("Groceries", "ai") for t in mine)
    assert json.loads(got["ai_status.json"])["applied"] == {"vendors": 1, "rows": len(mine)}
    assert (on / "state" / "engine" / "learned_rules.json").read_bytes() == \
        (base / "state" / "engine" / "learned_rules.json").read_bytes()
    assert any(c["name"] == "ai.applied" for c in json.loads(got["quality_report.json"])["checks"])

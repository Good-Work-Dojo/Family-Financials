# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The demo household's feed snapshot writer, row-uid helpers and answers state file, each read back through the
engine's own readers. Invented accounts only."""
from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from conftest import ROOT, write_household

from etl import pipeline
from etl.config import Config
from etl.enrich import answers as engine_answers
from etl.parsers import simplefin
from etl.pipeline import Pipeline
from tools.demo import uids
from tools.demo.ledger import Ledger, Row
from tools.demo.writers import chase_checking as write_checking
from tools.demo.writers import feed, state

ZONE = "America/New_York"
NY = ZoneInfo(ZONE)
CHECKING = "demo_checking_0101"
CARD = "demo_card_0102"
BROKERAGE = "demo_brokerage_0103"
LOAN = "demo_loan_0104"
FEED_IDS = {CHECKING: "ACT-demo-0101", CARD: "ACT-demo-0102", BROKERAGE: "ACT-demo-0103", LOAN: "ACT-demo-0104"}
AT = datetime(2030, 1, 31, 17, 0, tzinfo=NY)
_spec = importlib.util.spec_from_file_location("scenario_store", ROOT / "deploy" / "scenario_store.py")
scenario_store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scenario_store)


def D(day: int, month: int = 1) -> date:
    return date(2030, month, day)


def ledger() -> Ledger:
    led = Ledger(D(1), D(31))
    led.open(CHECKING, "checking", 500_000)
    led.open(CARD, "credit-card", -20_000)
    led.open(BROKERAGE, "brokerage", 2_500_000)
    led.open(LOAN, "loan", -20_000_000)
    led.post(Row(CHECKING, D(2), 250_000, "PAYROLL ACME CORP", feed_id="c-1"))
    led.post(Row(CHECKING, D(5), -4_500, "ACME COFFEE", payee="Acme Coffee Co", feed_id="c-2", bank_category="Dining"))
    led.post(Row(CHECKING, D(5), -4_500, "ACME COFFEE", payee="Acme Coffee Co", feed_id="c-3"))
    led.post(Row(CHECKING, D(20), -999, "STREAMING SERVICE", feed_id="c-4"))
    led.post(Row(CHECKING, D(31), -1_250, "CORNER SHOP", feed_id="c-5", pending=True))
    led.post(Row(CHECKING, D(1, 2), -3_000, "TOMORROW'S ROW", feed_id="c-6"))        # after `at`: never in the snapshot
    led.post(Row(CARD, D(8), -4_599, "ACME STORE", feed_id="k-1"))
    led.adjust(BROKERAGE, D(15), 12_345)
    led.adjust(LOAN, D(20), 31_000)
    return led


KINDS = {CHECKING: "checking", CARD: "credit-card", BROKERAGE: "brokerage", LOAN: "loan"}


def accounts() -> list[feed.FeedAccount]:
    return [
        feed.FeedAccount(CHECKING, FEED_IDS[CHECKING], "Sample Checking", "Sample Bank"),
        feed.FeedAccount(CARD, FEED_IDS[CARD], "Sample Card", "Sample Card Co", credit_limit_cents=1_000_000),
        feed.FeedAccount(BROKERAGE, FEED_IDS[BROKERAGE], "Sample Brokerage", "Sample Broker", transactions=False,
                         holdings=(feed.Holding("DEMO4", "Sample Fund", "100", 2_000_000, 1_500_000),
                                   feed.Holding("DEMO5", "Demo Bond Fund", "12.5", 512_345, 500_000))),
        feed.FeedAccount(LOAN, FEED_IDS[LOAN], "Sample Mortgage", "Sample Lender", transactions=False),
    ]


def meta() -> dict[str, dict]:
    return {a.account: {"name": a.name, "institution": a.org, "owner": "Alex", "kind": KINDS[a.account]}
            for a in accounts()}


def parse(path: Path, zone: str = ZONE):
    sfid_map = {FEED_IDS[a]: a for a in FEED_IDS}
    return simplefin.parse_snapshot(path, sfid_map, {}, meta(), zone)


def cents_text(cents: int) -> str:
    return f"{'-' if cents < 0 else ''}{abs(cents) // 100}.{abs(cents) % 100:02d}"


def test_a_snapshot_parses_to_the_ledgers_rows_and_balances(tmp_path):
    led = ledger()
    path = feed.write_snapshot(tmp_path / "store", AT, accounts(), led, zone=ZONE)
    txns, balances, holdings, stats, checks, _ = parse(path)
    assert [c for c in checks] == []
    on = {b.account_id: b for b in balances}
    for account in FEED_IDS:
        assert on[account].as_of == "2030-01-31"
    assert on[CHECKING].balance_cents == led.balance(CHECKING, D(31), with_pending=True)
    assert on[CARD].balance_cents == -24_599 and on[CARD].limit_cents == 1_000_000
    assert on[CARD].available_cents == 1_000_000 - 24_599
    assert on[BROKERAGE].balance_cents == 2_512_345
    assert on[LOAN].balance_cents == -19_969_000 and on[LOAN].limit_cents is None
    want = [r for r in led.rows(CHECKING, D(1), D(31)) if not r.pending]
    assert [(t.date, t.amount_cents) for t in txns if t.account_id == CHECKING] == [
        (r.date.isoformat(), r.cents) for r in want]
    assert [t.account_id for t in txns].count(CARD) == 1 and not [t for t in txns if t.account_id in (BROKERAGE, LOAN)]
    by_desc = {(t.description_raw, t.bank_category) for t in txns if t.account_id == CHECKING}
    assert ("Acme Coffee Co — ACME COFFEE", "Dining") in by_desc and ("STREAMING SERVICE", None) in by_desc
    assert stats.rows_total == 6 and stats.skip_reasons == {"pending": 1}
    assert sorted((h.symbol, h.name, h.quantity, h.value_cents, h.cost_basis_cents, h.as_of) for h in holdings) == [
        ("DEMO4", "Sample Fund", 100.0, 2_000_000, 1_500_000, "2030-01-31"),
        ("DEMO5", "Demo Bond Fund", 12.5, 512_345, 500_000, "2030-01-31")]


def test_pending_rows_are_in_the_balance_and_not_in_the_rows(tmp_path):
    led = ledger()
    path = feed.write_snapshot(tmp_path, AT, accounts(), led, zone=ZONE)
    doc = json.loads(path.read_text("utf-8"))
    checking = doc["accounts"][0]
    assert checking["balance"] == cents_text(led.balance(CHECKING, D(31), with_pending=True))
    assert checking["balance"] != cents_text(led.balance(CHECKING, D(31)))
    pending = [t for t in checking["transactions"] if t["pending"]]
    assert [(t["id"], t["posted"], t["amount"]) for t in pending] == [("c-5", 0, "-12.50")]
    assert pending[0]["transacted_at"] == int(datetime(2030, 1, 31, 12, tzinfo=NY).timestamp())
    assert "c-6" not in [t["id"] for t in checking["transactions"]]               # dated after the snapshot
    txns = parse(path)[0]
    assert "c-5" not in [t.sf_tid for t in txns] and len([t for t in txns if t.account_id == CHECKING]) == 4


def test_ahead_days_lists_a_row_dated_after_the_snapshot_and_leaves_the_balance(tmp_path):
    led = ledger()
    led.post(Row(CHECKING, D(3, 2), -4_000, "THREE DAYS AHEAD", feed_id="c-7"))
    path = feed.write_snapshot(tmp_path, AT, accounts(), led, zone=ZONE, ahead_days=1)
    checking = json.loads(path.read_text("utf-8"))["accounts"][0]
    ids = [t["id"] for t in checking["transactions"]]
    assert ids[-1] == "c-6" and "c-7" not in ids                  # Feb 1 is one day ahead, Feb 3 is three
    [ahead] = [t for t in checking["transactions"] if t["id"] == "c-6"]
    assert ahead["pending"] is False and ahead["posted"] == int(datetime(2030, 2, 1, 12, tzinfo=NY).timestamp())
    assert checking["balance"] == cents_text(led.balance(CHECKING, D(31), with_pending=True))
    assert [r.feed_id for r in feed.listed(led, CHECKING, D(31), 45, ahead_days=1)] == ids
    txns, balances = parse(path)[:2]
    assert ("2030-02-01", -3_000) in [(t.date, t.amount_cents) for t in txns if t.account_id == CHECKING]
    assert {b.as_of for b in balances} == {"2030-01-31"}
    with pytest.raises(ValueError, match="ahead"):
        feed.write_snapshot(tmp_path / "again", AT, accounts(), led, zone=ZONE, ahead_days=-1)
    assert not (tmp_path / "again").exists()


def test_the_window_keeps_rows_in_the_last_days_only_oldest_first(tmp_path):
    led = ledger()
    led.post(Row(CHECKING, D(11), -700, "JUST OUTSIDE", feed_id="edge-out"))       # 31 - 20: one day too old
    led.post(Row(CHECKING, D(12), -800, "JUST INSIDE", feed_id="edge-in"))         # 31 - 20 + 1: the oldest kept
    path = feed.write_snapshot(tmp_path, AT, accounts(), led, zone=ZONE, window_days=20)
    ids = [t["id"] for t in json.loads(path.read_text("utf-8"))["accounts"][0]["transactions"]]
    assert ids == ["edge-in", "c-4", "c-5"]                         # (Jan 11, Jan 31]: the 12th, the 20th, the pending one
    assert [r.feed_id for r in feed.listed(led, CHECKING, D(31), 20)] == ids


@pytest.mark.parametrize("case", ["account-twice", "feed-id-twice", "no-window"])
def test_a_snapshot_refuses_an_account_twice_a_shared_feed_id_and_an_empty_window(tmp_path, case):
    led = ledger()
    specs, window, match = accounts(), 45, None
    if case == "account-twice":
        specs, match = [*specs, feed.FeedAccount(CHECKING, "ACT-demo-0199", "Again", "Sample Bank")], CHECKING
    elif case == "feed-id-twice":
        specs = [specs[0], feed.FeedAccount(CARD, FEED_IDS[CHECKING], "Sample Card", "Sample Card Co")]
        match = FEED_IDS[CHECKING]
    else:
        window, match = 0, "window"
    with pytest.raises(ValueError, match=match):
        feed.write_snapshot(tmp_path, AT, specs, led, zone=ZONE, window_days=window)
    assert not (tmp_path / "snapshots").exists()


@pytest.mark.parametrize("zone", ["Pacific/Auckland", "UTC"])
def test_a_snapshot_round_trips_in_any_household_zone(tmp_path, zone):
    """A row dated D reads back as D through the feed parser with that household zone, and the balance is dated on
    the snapshot's own local day."""
    led = ledger()
    at = datetime(2030, 1, 31, 17, 0, tzinfo=ZoneInfo(zone))
    path = feed.write_snapshot(tmp_path, at, accounts(), led, zone=zone)
    txns, balances = parse(path, zone)[:2]
    want = [r for r in led.rows(CHECKING) if not r.pending and r.date <= D(31)]
    assert [(t.date, t.amount_cents) for t in txns if t.account_id == CHECKING] == [
        (r.date.isoformat(), r.cents) for r in want]
    assert {b.as_of for b in balances} == {"2030-01-31"}


def test_money_is_written_as_plain_dollars_with_two_decimals(tmp_path):
    led = Ledger(D(1), D(31))
    led.open(CHECKING, "checking", 0)
    led.open(CARD, "credit-card", 0)
    led.post(Row(CARD, D(3), -5, "A NICKEL", feed_id="k-1"))
    led.post(Row(CARD, D(4), -123_450, "A BIG ONE", feed_id="k-2"))
    path = feed.write_snapshot(tmp_path, AT, accounts()[:2], led, zone=ZONE)
    checking, card = json.loads(path.read_text("utf-8"))["accounts"]
    assert (checking["balance"], checking["available-balance"]) == ("0.00", "0.00")
    assert [t["amount"] for t in card["transactions"]] == ["-0.05", "-1234.50"]
    assert card["balance"] == "-1234.55"


def test_a_card_in_credit_and_a_card_with_no_limit(tmp_path):
    led = Ledger(D(1), D(31))
    led.open(CARD, "credit-card", 5_000)                              # the issuer owes the household 50.00
    led.open(LOAN, "credit-card", -10_000)
    specs = [feed.FeedAccount(CARD, FEED_IDS[CARD], "Sample Card", "Sample Card Co", credit_limit_cents=1_000_000),
             feed.FeedAccount(LOAN, FEED_IDS[LOAN], "Limitless Card", "Sample Card Co")]
    path = feed.write_snapshot(tmp_path, AT, specs, led, zone=ZONE)
    in_credit, no_limit = json.loads(path.read_text("utf-8"))["accounts"]
    assert (in_credit["balance"], in_credit["available-balance"]) == ("50.00", "10000.00")
    assert in_credit["extra"] == {"credit-limit": "10000.00"}
    assert no_limit["balance"] == "-100.00"
    assert "available-balance" not in no_limit and "extra" not in no_limit


def test_a_row_without_a_feed_id_or_with_a_repeated_one_is_refused(tmp_path):
    led = ledger()
    led.post(Row(CHECKING, D(22), -100, "NO ID"))
    with pytest.raises(ValueError, match="feed_id"):
        feed.write_snapshot(tmp_path, AT, accounts(), led, zone=ZONE)
    led = ledger()
    led.post(Row(CHECKING, D(22), -100, "REPEATS", feed_id="c-1"))
    with pytest.raises(ValueError, match="c-1"):
        feed.write_snapshot(tmp_path, AT, accounts(), led, zone=ZONE)
    assert not (tmp_path / "snapshots").exists() or list((tmp_path / "snapshots").iterdir()) == []


def feed_home(tmp_path: Path, monkeypatch, *, file_account: bool = False) -> Path:
    home = tmp_path / "home"
    cfg = home / "config"
    entries = {
        CHECKING: {"name": "Sample Checking", "institution": "Sample Bank", "owner": "Alex", "kind": "checking",
                   "simplefin_id": FEED_IDS[CHECKING]}}
    if file_account:
        entries["demo_checking_0105"] = {"name": "Sample File Checking", "institution": "Sample Bank",
                                         "owner": "Alex", "kind": "checking", "parser": "chase_checking",
                                         "glob": "demo_checking_0105-*.csv"}
    cfg.mkdir(parents=True)
    (cfg / "accounts.yaml").write_text(yaml.safe_dump({"version": 1, "accounts": entries}), "utf-8")
    write_household(cfg, timezone=ZONE)
    (home / "imports").mkdir()
    monkeypatch.setenv("FF_HOME", str(home))
    return home


def stored_txns(home: Path) -> list:
    p = Pipeline(Config(home / "config"))
    p.parse_transactions()
    p.parse_simplefin()
    return p.txns


def test_two_snapshots_that_repeat_a_row_store_it_once(tmp_path, monkeypatch):
    home = feed_home(tmp_path, monkeypatch)
    led = ledger()
    store = home / "store" / "simplefin"
    only = [accounts()[0]]
    feed.write_snapshot(store, datetime(2030, 1, 10, 9, 0, tzinfo=NY), only, led, zone=ZONE)
    feed.write_snapshot(store, AT, only, led, zone=ZONE)                       # a later fetch repeats every row
    rows = [t for t in stored_txns(home) if t.account_id == CHECKING]
    assert sorted(t.sf_tid for t in rows) == ["c-1", "c-2", "c-3", "c-4"]
    assert len({t.uid for t in rows}) == 4


def test_identical_same_day_feed_rows_keep_distinct_ids(tmp_path, monkeypatch):
    home = feed_home(tmp_path, monkeypatch)
    feed.write_snapshot(home / "store" / "simplefin", AT, [accounts()[0]], ledger(), zone=ZONE)
    twins = [t for t in stored_txns(home) if t.date == "2030-01-05"]
    assert len(twins) == 2 and twins[0].uid != twins[1].uid
    assert {(t.amount_cents, t.description_raw) for t in twins} == {(-4_500, "Acme Coffee Co — ACME COFFEE")}


def test_the_file_name_stamp_is_the_fetch_instant_in_utc_and_sorts_by_time(tmp_path):
    led = ledger()
    late = datetime(2030, 1, 31, 23, 30, 5, tzinfo=NY)                         # 04:30:05 UTC on the next day
    early = datetime(2030, 1, 31, 9, 0, tzinfo=NY)
    a = feed.write_snapshot(tmp_path, late, [accounts()[0]], led, zone=ZONE)
    b = feed.write_snapshot(tmp_path, early, [accounts()[0]], led, zone=ZONE)
    assert a.parent == tmp_path / "snapshots" and a.name == "accounts-20300201T043005Z.json"
    assert b.name == "accounts-20300131T140000Z.json"
    assert sorted(p.name for p in a.parent.iterdir()) == [b.name, a.name]
    with pytest.raises(FileExistsError):
        feed.write_snapshot(tmp_path, early, [accounts()[0]], led, zone=ZONE)
    assert json.loads(b.read_text("utf-8"))["accounts"][0]["balance-date"] == int(
        datetime(2030, 1, 31, 12, tzinfo=NY).timestamp())


def test_an_omitted_account_and_an_unmapped_one(tmp_path):
    led = ledger()
    stray = {"id": "ACT-demo-9999", "name": "Mystery Account", "org": {"name": "Elsewhere"}, "balance": "1.00"}
    path = feed.write_snapshot(tmp_path, AT, accounts(), led, zone=ZONE, omit=frozenset({CARD}),
                               unmapped=(stray,))
    doc = json.loads(path.read_text("utf-8"))
    assert [a["id"] for a in doc["accounts"]] == ["ACT-demo-0101", "ACT-demo-0103", "ACT-demo-0104", "ACT-demo-9999"]
    assert doc["accounts"][-1] == stray
    checks = parse(path)[4]
    assert [c.name for c in checks] == ["simplefin.unmapped.ACT-demo-9999"]


def test_the_document_layout_is_fixed(tmp_path):
    path = feed.write_snapshot(tmp_path, AT, accounts(), ledger(), zone=ZONE)
    text = path.read_text("utf-8")
    assert text.endswith("}\n") and text.startswith('{\n "accounts": [\n  {\n   "id": "ACT-demo-0101",\n')
    first, card, brokerage, loan = json.loads(text)["accounts"]
    assert list(first) == ["id", "name", "org", "currency", "balance", "available-balance", "balance-date",
                           "transactions"]
    assert first["org"] == {"name": "Sample Bank"} and first["currency"] == "USD"
    assert card["extra"] == {"credit-limit": "10000.00"} and card["available-balance"] == "9754.01"
    assert list(brokerage)[-1] == "holdings" and "transactions" not in brokerage
    assert brokerage["holdings"][1] == {"symbol": "DEMO5", "description": "Demo Bond Fund", "shares": "12.5",
                                        "market_value": "5123.45", "cost_basis": "5000.00"}
    assert loan["balance"] == "-199690.00" and "extra" not in loan and "holdings" not in loan
    assert list(first["transactions"][0]) == ["id", "posted", "amount", "description", "pending"]
    assert list(first["transactions"][1]) == ["id", "posted", "amount", "description", "payee", "pending", "extra"]
    assert first["transactions"][1]["extra"] == {"category": "Dining"}
    assert first["transactions"][0]["posted"] == int(datetime(2030, 1, 2, 12, tzinfo=NY).timestamp())


# ------------------------------------------------------------------ uids

def test_demo_uids_are_the_uids_the_pipeline_stores(tmp_path, monkeypatch):
    home = feed_home(tmp_path, monkeypatch, file_account=True)
    file_account = "demo_checking_0105"
    led = Ledger(D(1), D(31))
    led.open(file_account, "checking", 100_000)
    led.open(CHECKING, "checking", 100_000)
    led.post(Row(file_account, D(4), -350, "ACME COFFEE"))
    led.post(Row(file_account, D(4), -350, "ACME COFFEE"))
    led.post(Row(file_account, D(6), -1_200, "HARDWARE STORE"))
    led.post(Row(CHECKING, D(7), -500, "STREAMING", feed_id="f-1"))
    led.post(Row(CHECKING, D(7), -800, "ACME COFFEE #12", payee="Acme", feed_id="f-2"))            # payee already inside
    led.post(Row(CHECKING, D(8), -900, "CORNER SHOP", payee="Corner Shop Inc", feed_id="f-3"))
    write_checking.write(home / "imports" / f"{file_account}-2030-01.csv", file_account, led, D(1), D(31))
    feed.write_snapshot(home / "store" / "simplefin", AT, [accounts()[0]], led, zone=ZONE)

    want = uids.file_uids(file_account, led.rows(file_account)) + [uids.feed_uid(CHECKING, r)
                                                                     for r in led.rows(CHECKING)]
    assert len(set(want)) == 6
    assert pipeline.run() == 0
    with sqlite3.connect(home / "data" / "finance.db") as db:
        stored = {r[0] for r in db.execute("SELECT txn_uid FROM transactions")}
    assert stored == set(want)


def test_a_split_parts_uid_is_the_engines(tmp_path, monkeypatch):
    """A three-row household whose one wholesale purchase the vendor rule splits in two: the parts' uids are the ones
    `part_uid` gives, from the parent's uid and each part's 1-based leg position."""
    home = feed_home(tmp_path, monkeypatch)
    cfg = home / "config"
    shutil.copy(ROOT / "starter" / "category_rules.yaml", cfg / "category_rules.yaml")
    (cfg / "vendor_rules.yaml").write_text(yaml.safe_dump({"patterns": [{
        "pattern": r"^ACME WAREHOUSE\b", "vendor": "Acme Warehouse", "note": "a guess at the usual basket",
        "split": [{"pct": 70, "category": "Groceries"},
                  {"remainder": True, "category": "Shopping", "subcategory": "Home Goods"}]}]}), "utf-8")
    led = Ledger(D(1), D(31))
    led.open(CHECKING, "checking", 100_000)
    split = led.post(Row(CHECKING, D(6), -12_347, "ACME WAREHOUSE 0417", feed_id="w-1"))
    others = [led.post(Row(CHECKING, D(9), -1_250, "CORNER SHOP", feed_id="w-2")),
              led.post(Row(CHECKING, D(12), 50_000, "PAYROLL ACME CORP", feed_id="w-3"))]
    feed.write_snapshot(home / "store" / "simplefin", AT, [accounts()[0]], led, zone=ZONE)
    assert pipeline.run() == 0
    rows = {t["id"]: t for t in json.loads((home / "data" / "exports" / "transactions.json").read_text("utf-8"))[
        "transactions"]}
    parent = uids.feed_uid(CHECKING, split)
    parts = [uids.part_uid(parent, i) for i in (1, 2)]
    assert set(rows) == {parent, *parts, *(uids.feed_uid(CHECKING, r) for r in others)}
    assert rows[parent]["is_parent"] and [rows[p]["parent_id"] for p in parts] == [parent, parent]
    # 70 percent of 123.47 is 86.429: the leg rounds to 86.43 and the remainder takes the rest
    assert [(rows[p]["category"], rows[p]["amount"]) for p in parts] == [("Groceries", -86.43), ("Shopping", -37.04)]
    with pytest.raises(ValueError, match="index"):
        uids.part_uid(parent, 0)


def test_file_uids_count_occurrences_per_file_in_file_order():
    rows = [Row("a", D(4), -350, "ACME COFFEE"), Row("a", D(5), -350, "ACME COFFEE"), Row("a", D(4), -350, "ACME COFFEE")]
    first = uids.file_uids("a", rows)
    assert len(set(first)) == 3 and first[0] != first[2]
    assert uids.file_uids("a", rows[:1]) == first[:1]
    assert uids.file_uids("b", rows[:1]) != first[:1]


def test_file_uids_depend_on_the_rows_not_on_the_file_that_lists_them():
    twin = Row("a", D(4), -350, "ACME COFFEE")
    other = Row("a", D(4), -1_200, "HARDWARE STORE")
    first = uids.file_uids("a", [twin, twin, other])
    assert uids.file_uids("a", [twin, twin, other]) == first                     # the same rows in another file
    between = uids.file_uids("a", [twin, other, twin])                           # a different row between the twins
    assert (between[0], between[2]) == (first[0], first[1]) and between[1] == first[2]


def test_file_uids_refuse_a_pending_row():
    """A statement file never lists a pending row, and a pending twin would move the occurrence count of the posted
    one away from the engine's."""
    rows = [Row("a", D(4), -350, "ACME COFFEE", pending=True), Row("a", D(4), -350, "ACME COFFEE")]
    with pytest.raises(ValueError, match="pending"):
        uids.file_uids("a", rows)


def test_feed_uid_needs_a_feed_id():
    with pytest.raises(ValueError, match="feed_id"):
        uids.feed_uid("a", Row("a", D(4), -350, "ACME COFFEE"))


# ------------------------------------------------------------------ the package imports

ALLOWED_ETL_IMPORTS = {("etl.models", "Txn"), ("etl.parsers.simplefin", "_seq_from_id")}


def test_tools_demo_imports_nothing_from_etl_but_the_uid_hash():
    files = sorted((ROOT / "tools" / "demo").rglob("*.py"))
    assert len(files) >= 10                                         # the scan reads the whole package
    found: set[tuple[str, str]] = set()
    for path in files:
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "etl":
                found |= {(node.module, a.name) for a in node.names}
            elif isinstance(node, ast.Import):
                found |= {(a.name, "*") for a in node.names if a.name.split(".")[0] == "etl"}
    assert found == ALLOWED_ETL_IMPORTS


# ------------------------------------------------------------------ the answers state file

def test_answers_document_is_accepted_by_the_engine_and_the_store(tmp_path):
    when = datetime(2030, 2, 1, 9, 30, 15, 999, tzinfo=NY)
    answers = [state.row_answer("tx_0123456789abcdef", {"category": "Food", "subcategory": "Dining"}, when),
               state.vendor_answer("Acme Coffee", {"category": "Food", "needs_review": False}, when)]
    path = tmp_path / "state" / "answers.json"
    state.write_answers(path, answers, when)
    text = path.read_text("utf-8")
    assert text.endswith("\n")
    doc = json.loads(text)
    assert doc["version"] == 1 and doc["updated_at"] == "2030-02-01T14:30:15Z"
    assert doc["answers"][0] == {"key": "txn:tx_0123456789abcdef", "scope": "txn", "txn_id": "tx_0123456789abcdef",
                                 "set": {"category": "Food", "subcategory": "Dining"},
                                 "answered_at": "2030-02-01T14:30:15Z"}
    assert doc["answers"][1]["key"] == "vendor:Acme Coffee" and doc["answers"][1]["vendor"] == "Acme Coffee"
    loaded, checks = engine_answers.load(path)
    assert checks == [] and engine_answers.valid_entries(loaded) == doc["answers"]
    assert scenario_store.validate_answers(path.read_bytes()) == doc


WHEN = datetime(2030, 2, 1, tzinfo=UTC)


@pytest.mark.parametrize(("build", "match"), [
    (lambda: state.row_answer("", {"category": "Food"}, WHEN), "uid"),
    (lambda: state.vendor_answer("", {"category": "Food"}, WHEN), "vendor"),
    (lambda: state.vendor_answer("   ", {"category": "Food"}, WHEN), "vendor"),
    (lambda: state.row_answer("tx_0123456789abcdef", {"category": ""}, WHEN), "category"),
    (lambda: state.row_answer("tx_0123456789abcdef", {"category": 3}, WHEN), "category"),
    (lambda: state.row_answer("tx_0123456789abcdef", {"category": "Food", "subcategory": 7}, WHEN), "subcategory"),
    (lambda: state.row_answer("tx_0123456789abcdef", {"tags": "kids"}, WHEN), "tags"),
    (lambda: state.row_answer("tx_0123456789abcdef", {"tags": ["kids", 4]}, WHEN), "tags"),
    (lambda: state.row_answer("tx_0123456789abcdef", {"needs_review": "no"}, WHEN), "needs_review"),
    (lambda: state.row_answer("tx_0123456789abcdef", {"needs_review": 0}, WHEN), "needs_review"),
], ids=["empty-uid", "empty-vendor", "blank-vendor", "empty-category", "number-category", "number-subcategory",
        "text-tags", "number-tag", "text-review", "number-review"])
def test_an_answer_the_store_would_refuse_is_refused_when_built(build, match):
    with pytest.raises(ValueError, match=match):
        build()


def test_an_answer_the_store_takes_is_built():
    answer = state.row_answer("tx_0123456789abcdef", {"category": "Food", "subcategory": None, "tags": ["kids"],
                                                      "needs_review": False}, WHEN)
    doc = {"version": 1, "updated_at": "2030-02-01T00:00:00Z", "answers": [answer]}
    assert scenario_store.validate_answers(json.dumps(doc).encode()) == doc


def test_two_answers_with_one_key_are_refused(tmp_path):
    answers = [state.vendor_answer("Acme Coffee", {"category": "Food"}, WHEN),
               state.vendor_answer("Acme Coffee", {"category": "Dining"}, WHEN)]
    path = tmp_path / "answers.json"
    with pytest.raises(ValueError, match="vendor:Acme Coffee"):
        state.write_answers(path, answers, WHEN)
    assert not path.exists()


def test_an_answer_may_set_only_the_keys_the_engine_accepts():
    when = datetime(2030, 2, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="type"):
        state.row_answer("tx_0123456789abcdef", {"type": "transfer"}, when)
    with pytest.raises(ValueError, match="set"):
        state.vendor_answer("Acme Coffee", {}, when)
    with pytest.raises(ValueError, match="aware"):
        state.vendor_answer("Acme Coffee", {"category": "Food"}, datetime(2030, 2, 1))

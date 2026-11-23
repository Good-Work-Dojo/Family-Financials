# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""SimpleFIN parser, pipeline integration, and fetch-module tests on synthetic
JSON (no real data, no network). The pipeline tests run on the demo config, and
the sample accounts below are three of its accounts. Everything here is invented."""
from __future__ import annotations

import datetime as dt
import json
import os
import time

import pytest
from conftest import demo_config

from etl import simplefin as sf
from etl.parsers import simplefin as sfp
from etl.pipeline import Pipeline
from tools.demo import household as hh


def epoch(date_str: str) -> int:
    y, m, d = map(int, date_str.split("-"))
    return int(dt.datetime(y, m, d, 12, tzinfo=dt.timezone.utc).timestamp())


# the household zone the parser unit tests read the feed in: their epochs sit at noon UTC (`epoch`), which every zone
# within eleven hours of UTC dates the same day (tests/test_timezones.py tells zones apart)
TZ = "UTC"


def acct(sfid, name, balance, *, currency="USD", available=None, bal_date="2026-06-10",
         org="Bank", txns=None, holdings=None, extra=None):
    a = {"id": sfid, "name": name, "org": {"name": org}, "currency": currency,
         "balance": balance, "balance-date": epoch(bal_date),
         "transactions": txns or []}
    if available is not None:
        a["available-balance"] = available
    if holdings is not None:
        a["holdings"] = holdings
    if extra is not None:
        a["extra"] = extra
    return a


def txn(tid, date, amount, desc, *, pending=False, payee=None):
    t = {"id": tid, "posted": epoch(date), "amount": amount, "description": desc}
    if pending:
        t["pending"] = True
        t["posted"] = 0
        t["transacted_at"] = epoch(date)
    if payee:
        t["payee"] = payee
    return t


# three of the demo household's accounts: joint checking, Alex's card and the joint brokerage
CHK, CARD, BRK = hh.CHECKING, hh.CARD_ALEX, hh.BROKERAGE
META = {a: {"name": hh.ACCOUNTS[a].name, "institution": hh.ACCOUNTS[a].institution, "owner": hh.ACCOUNTS[a].owner,
            "kind": hh.ACCOUNTS[a].kind} for a in (CHK, CARD, BRK)}
META[CARD]["credit_limit"] = 30000
SFID_MAP = {"SF-CHK": CHK, "SF-CARD": CARD, "SF-BRK": BRK}


def write_snapshot(path, accounts, errlist=None):
    doc = {"accounts": accounts, "errlist": errlist or []}
    path.write_text(json.dumps(doc))
    return path


# ----------------------------------------------------------------- parser unit

def test_parse_snapshot_signs_skips_and_balances(tmp_path):
    snap = write_snapshot(tmp_path / "accounts-1.json", [
        acct("SF-CHK", "Checking", "1500.00", bal_date="2026-06-10", txns=[
            txn("t1", "2026-06-05", "-42.10", "COFFEE BAR"),          # spend, kept
            txn("t2", "2026-06-03", "2500.00", "PAYROLL DEP"),        # income, kept
            txn("t3", "2026-05-20", "-9.99", "OLD CSV ERA"),          # before cutover
            txn("t4", "2026-06-06", "-1.00", "PENDING ONE", pending=True),
        ]),
        acct("SF-CARD", "Card", "-1200.00", available="28800.00", txns=[
            txn("c1", "2026-06-04", "-55.00", "GROCERY", payee="ACME GROCER"),
        ]),
        acct("SF-UNKNOWN", "Mystery", "10.00", org="WhoBank"),
    ])
    cutovers = {CHK: "2026-06-01", CARD: "2026-06-01"}
    txns, balances, holdings, stats, checks, earliest = sfp.parse_snapshot(
        snap, SFID_MAP, cutovers, META, tz=TZ)

    kept = {t.description_raw: t.amount_cents for t in txns}
    assert kept == {"COFFEE BAR": -4210, "PAYROLL DEP": 250000, "ACME GROCER — GROCERY": -5500}
    assert stats.skip_reasons.get("before-cutover") == 1
    assert stats.skip_reasons.get("pending") == 1
    # reconciliation guard: FileStats sum == sum of persisted rows
    assert stats.sum_cents == sum(t.amount_cents for t in txns)
    # every SimpleFIN txn carries the marker + stable seq (so uids are stable)
    assert all(t.bank_type == "simplefin" for t in txns)
    # unmapped account -> warn, not a crash, and no txns from it
    assert any(c.name == "simplefin.unmapped.SF-UNKNOWN" and c.status == "warn"
               for c in checks)
    # balances captured for mapped accounts
    bymap = {b.account_id: b for b in balances}
    assert bymap[CHK].balance_cents == 150000
    assert bymap[CARD].balance_cents == -120000


def test_brokerage_option_trades_are_investment(tmp_path):
    # An option leg is worded without the word "shares" ("Bought 3 ABCD call contracts, strike 75, to open"). It is
    # an internal portfolio move like a stock trade: typed purchase it would count as spending, and (answers cannot
    # change `type`) the Review inbox could not fix it.
    meta = {**META, BRK: {**META[BRK], "simplefin_cutover": "2030-05-01"}}
    snap = write_snapshot(tmp_path / "accounts-opt.json", [
        acct("SF-BRK", "Brokerage", "9000.00", org=hh.BROKER, txns=[
            txn("o1", "2030-06-11", "-1100.00", "Bought 3 ABCD call contracts, strike 75, to open",
                payee="Bought Abcd Call Contracts Strike to Open"),
            txn("o2", "2030-06-18", "300.00", "Sold 5 WXYZ put contracts, strike 20, to close",
                payee="Sold Wxyz Put Contracts Strike to Close"),
            txn("s1", "2030-06-13", "-500.00", "Bought 16 shares of Sample Index Fund at 31.25"),
        ])])
    txns, *_ = sfp.parse_snapshot(snap, SFID_MAP, {BRK: "2030-05-01"}, meta, tz=TZ)
    assert [(t.type, t.is_internal) for t in txns] == [("investment", True)] * 3


def test_a_long_brokerage_description_is_typed_from_its_first_characters_and_stored_whole(tmp_path):
    """The wording patterns read a description's first registry.MAX_DESCRIPTION characters, so a 20,000-character one
    is typed in well under a second; the description itself is stored whole, so the row's uid is the one its full text
    gives."""
    from etl.importers import registry
    meta = {**META, BRK: {**META[BRK], "simplefin_cutover": "2030-05-01"}}
    long = "Bought 2 ABCD " + "buy put " * 2500
    snap = write_snapshot(tmp_path / "accounts-long.json", [
        acct("SF-BRK", "Brokerage", "9000.00", org=hh.BROKER, txns=[
            txn("l1", "2030-06-11", "-100.00", long),
            txn("l2", "2030-06-12", "-100.00", "x" * registry.MAX_DESCRIPTION + " Bought 3 shares of SMPL")])])
    started = time.monotonic()
    txns, *_ = sfp.parse_snapshot(snap, SFID_MAP, {BRK: "2030-05-01"}, meta, tz=TZ)
    assert time.monotonic() - started < 1.0
    assert [t.description_raw for t in txns] == [long.strip(), "x" * registry.MAX_DESCRIPTION + " Bought 3 shares of SMPL"]
    # the wording past the first characters is not read: the second row stays a purchase
    assert [t.type for t in txns] == ["purchase", "purchase"]


def test_brokerage_cashback_is_income_not_negative_spend(tmp_path):
    # Card rewards swept into the brokerage. Typed purchase (the default), this positive row would subtract from
    # spending; it is the card issuer's rebate arriving, so it belongs in income. It is not internal: the other side
    # is the issuer's statement credit on the card, which the feed never gives as a transaction.
    meta = {**META, BRK: {**META[BRK], "simplefin_cutover": "2030-05-01"}}
    snap = write_snapshot(tmp_path / "accounts-cb.json", [
        acct("SF-BRK", "Brokerage", "9000.00", org=hh.BROKER, txns=[
            txn("cb1", "2030-06-03", "57.25", "Cash back rewards deposit from Sample Card 57.25"),
            # the dividend and margin-interest branches keep winning over the cash-back one
            txn("d1", "2030-06-04", "4.10", "Dividend received SMPL 4.10"),
            txn("m1", "2030-06-30", "-12.80", "Margin interest charged for June"),
        ])])
    txns, *_ = sfp.parse_snapshot(snap, SFID_MAP, {BRK: "2030-05-01"}, meta, tz=TZ)
    cb = next(t for t in txns if t.description_raw.startswith("Cash back"))
    div = next(t for t in txns if t.description_raw.startswith("Dividend"))
    mint = next(t for t in txns if t.description_raw.startswith("Margin interest"))
    assert (cb.type, cb.category, cb.subcategory) == ("income", "Income", "Reimbursement")
    assert cb.is_internal is False
    assert (div.type, div.subcategory) == ("dividend", "Dividends")
    assert (mint.type, mint.subcategory) == ("fee", "Margin Interest")


def test_credit_limit_derivation(tmp_path):
    # 1) from available-balance: limit = available + |balance|
    snap = write_snapshot(tmp_path / "accounts-2.json", [
        acct("SF-CARD", "Card", "-1200.00", available="28800.00")])
    _, balances, *_ = sfp.parse_snapshot(snap, {"SF-CARD": CARD}, {}, META, tz=TZ)
    assert balances[0].limit_cents == 30000_00  # 28800 + 1200

    # 2) from extra credit-limit, overriding the available math
    snap = write_snapshot(tmp_path / "accounts-3.json", [
        acct("SF-CARD", "Card", "-500.00", available="100.00",
             extra={"credit-limit": "5000.00"})])
    _, balances, *_ = sfp.parse_snapshot(snap, {"SF-CARD": CARD}, {}, META, tz=TZ)
    assert balances[0].limit_cents == 5000_00

    # 3) fallback to config credit_limit when SimpleFIN exposes neither
    snap = write_snapshot(tmp_path / "accounts-4.json", [
        acct("SF-CARD", "Card", "-500.00")])
    _, balances, *_ = sfp.parse_snapshot(snap, {"SF-CARD": CARD}, {}, META, tz=TZ)
    assert balances[0].limit_cents == 30000_00  # META credit_limit 30000

    # 4) available reported as 0 (issuer doesn't expose it) + no config -> no limit
    #    (guards against deriving a false 100% utilization)
    no_limit_meta = {CARD: {k: v for k, v in META[CARD].items()
                                         if k != "credit_limit"}}
    snap = write_snapshot(tmp_path / "accounts-4b.json", [
        acct("SF-CARD", "Card", "-216.11", available="0.00")])
    _, balances, *_ = sfp.parse_snapshot(snap, {"SF-CARD": CARD}, {}, no_limit_meta, tz=TZ)
    assert balances[0].limit_cents is None


def test_non_usd_account_skipped_entirely(tmp_path):
    # non-USD account contributes no txns, balance or holdings (no FX), just a warn
    snap = write_snapshot(tmp_path / "accounts-5.json", [
        acct("SF-CHK", "Euro", "100.00", currency="EUR", holdings=[
            {"id": "h", "symbol": "X", "market_value": "1.00", "shares": "1"}],
             txns=[txn("e1", "2026-06-05", "-10.00", "PARIS CAFE")])])
    txns, balances, holdings, *_ , checks, _ = sfp.parse_snapshot(
        snap, {"SF-CHK": CHK}, {}, META, tz=TZ)
    assert txns == [] and balances == [] and holdings == []
    assert any(c.name == f"simplefin.currency.{CHK}" for c in checks)


def test_holding_cost_basis_derived_from_purchase_price(tmp_path):
    # SimpleFIN sends cost_basis 0 but a per-share purchase_price; reconstruct it
    snap = write_snapshot(tmp_path / "accounts-cb.json", [
        acct("SF-BRK", "Brokerage", "1000.00", org=hh.BROKER, holdings=[
            {"id": "h1", "symbol": "DEMO2", "description": "Demo Growth Fund",
             "shares": "10", "cost_basis": "0.00", "purchase_price": "48.35",
             "market_value": "612.20"}])])
    _, _, holdings, *_ = sfp.parse_snapshot(snap, {"SF-BRK": BRK}, {}, META, tz=TZ)
    assert holdings[0].cost_basis_cents == round(48.35 * 10 * 100)   # 48350


def test_duplicate_sfid_in_snapshot_warns_and_skips(tmp_path):
    snap = write_snapshot(tmp_path / "accounts-dup.json", [
        acct("SF-CHK", "A", "100.00", txns=[txn("a", "2026-06-05", "-1.00", "X")]),
        acct("SF-CHK", "A again", "999.00", txns=[txn("b", "2026-06-06", "-2.00", "Y")])])
    txns, balances, _, _, checks, _ = sfp.parse_snapshot(
        snap, {"SF-CHK": CHK}, {}, META, tz=TZ)
    assert len(balances) == 1 and balances[0].balance_cents == 10000   # first wins
    assert {t.description_raw for t in txns} == {"X"}                   # 2nd copy skipped
    assert any(c.name == f"simplefin.duplicate.{CHK}" for c in checks)


def test_replaced_connection_current_id_wins_in_one_snapshot(tmp_path):
    """A deleted+re-linked institution returns under a new id; if the old id
    (simplefin_id_history) transiently coexists with it in ONE snapshot, the
    current connection's entry must win — even listed second — with the
    historical twin skipped silently (no duplicate warn)."""
    meta = {CHK: {**META[CHK], "simplefin_id": "SF-CHK-NEW",
                           "simplefin_id_history": ["SF-CHK"]}}
    sfid_map = {"SF-CHK-NEW": CHK, "SF-CHK": CHK}
    snap = write_snapshot(tmp_path / "accounts-both.json", [
        acct("SF-CHK", "old conn", "100.00", bal_date="2026-07-20",
             txns=[txn("a", "2026-07-19", "-1.00", "STALE")]),
        acct("SF-CHK-NEW", "new conn", "120.00", bal_date="2026-07-26",
             txns=[txn("b", "2026-07-25", "-2.00", "FRESH")])])
    txns, balances, _, _, checks, _ = sfp.parse_snapshot(snap, sfid_map, {}, meta, tz=TZ)
    assert len(balances) == 1 and balances[0].balance_cents == 12000
    assert {t.description_raw for t in txns} == {"FRESH"}
    assert not checks                                   # expected twin: no warn


def test_replaced_connection_old_snapshots_still_map(tmp_path):
    """Snapshots fetched before the replacement contain only the old id — it
    must keep mapping to the account so its balance history survives."""
    meta = {CHK: {**META[CHK], "simplefin_id": "SF-CHK-NEW",
                           "simplefin_id_history": ["SF-CHK"]}}
    snap = write_snapshot(tmp_path / "accounts-old.json",
                          [acct("SF-CHK", "old conn", "100.00")])
    _, balances, _, _, checks, _ = sfp.parse_snapshot(
        snap, {"SF-CHK-NEW": CHK, "SF-CHK": CHK}, {}, meta, tz=TZ)
    assert [(b.account_id, b.balance_cents) for b in balances] == [(CHK, 10000)]
    assert not checks


def test_timezone_dating_matches_bank_local(tmp_path):
    # 2026-06-06 03:00 UTC = 2026-06-05 23:00 in the demo household's zone (America/New_York) -> bank-local date
    import datetime as dt
    e = int(dt.datetime(2026, 6, 6, 3, 0, tzinfo=dt.timezone.utc).timestamp())
    snap = write_snapshot(tmp_path / "accounts-tz.json", [
        {"id": "SF-CHK", "name": "C", "currency": "USD", "balance": "1.00",
         "balance-date": e, "transactions": [
             {"id": "t", "posted": e, "amount": "-5.00", "description": "LATE NIGHT"}]}])
    txns, *_ = sfp.parse_snapshot(snap, {"SF-CHK": CHK}, {}, META,
                                  tz=hh.TIMEZONE)
    assert txns[0].date == "2026-06-05"   # not 06-06 (UTC)


def test_holdings_mapping(tmp_path):
    snap = write_snapshot(tmp_path / "accounts-6.json", [
        acct("SF-BRK", "Brokerage", "10000.00", org=hh.BROKER, holdings=[
            {"id": "h1", "symbol": "DEMO1", "description": "DEMO TOTAL MARKET ETF",
             "shares": "84.17", "cost_basis": "71000.00", "market_value": "83000.00"},
        ])])
    _, _, holdings, *_ = sfp.parse_snapshot(snap, {"SF-BRK": BRK}, {}, META, tz=TZ)
    assert len(holdings) == 1
    h = holdings[0]
    assert (h.symbol, h.name, h.account) == ("DEMO1", "DEMO TOTAL MARKET ETF",
                                             hh.ACCOUNTS[BRK].name)
    assert h.quantity == pytest.approx(84.17)
    assert h.value_cents == 83000_00 and h.cost_basis_cents == 71000_00


# ----------------------------------------------------- uid stability across fetches

def test_uid_stable_across_overlapping_snapshots(tmp_path):
    common = [txn("a", "2026-06-05", "-10.00", "STORE A"),
              txn("b", "2026-06-06", "-20.00", "STORE B")]
    snap1 = write_snapshot(tmp_path / "accounts-10.json",
                           [acct("SF-CHK", "Chk", "100.00", txns=list(common))])
    snap2 = write_snapshot(tmp_path / "accounts-11.json",  # same ids + one new txn
                           [acct("SF-CHK", "Chk", "80.00",
                                 txns=common + [txn("c", "2026-06-07", "-30.00", "STORE C")])])
    u1 = {t.uid for t in sfp.parse_snapshot(snap1, {"SF-CHK": CHK}, {}, META, tz=TZ)[0]}
    u2 = {t.uid for t in sfp.parse_snapshot(snap2, {"SF-CHK": CHK}, {}, META, tz=TZ)[0]}
    assert u1 < u2 and len(u2 - u1) == 1   # shared txns dedup away; only "c" is new


def test_distinct_txns_same_content_different_ids_stay_distinct(tmp_path):
    snap = write_snapshot(tmp_path / "accounts-12.json", [acct("SF-CHK", "Chk", "0.00", txns=[
        txn("id-1", "2026-06-05", "-10.00", "SAME SAME"),
        txn("id-2", "2026-06-05", "-10.00", "SAME SAME")])])
    txns = sfp.parse_snapshot(snap, {"SF-CHK": CHK}, {}, META, tz=TZ)[0]
    assert len({t.uid for t in txns}) == 2


# ------------------------------------------------------- pipeline integration

def _cfg_with_simplefin(tmp_path):
    """The demo config with its snapshots read from tmp_path and the three sample accounts mapped to the feed ids
    the snapshots below use."""
    cfg = demo_config()
    cfg.simplefin_snapshots_dir = tmp_path / "snaps"
    cfg.simplefin_snapshots_dir.mkdir(parents=True)
    cfg.accounts[CHK]["simplefin_id"] = "SF-CHK"
    cfg.accounts[CHK]["simplefin_cutover"] = "2026-06-01"
    cfg.accounts[CARD]["simplefin_id"] = "SF-CARD"
    cfg.accounts[CARD]["simplefin_cutover"] = "2026-06-01"
    cfg.accounts[BRK]["simplefin_id"] = "SF-BRK"
    return cfg


def test_pipeline_parse_simplefin(tmp_path):
    cfg = _cfg_with_simplefin(tmp_path)
    write_snapshot(cfg.simplefin_snapshots_dir / "accounts-20260610T000000Z.json", [
        acct("SF-CHK", "Chk", "1500.00", bal_date="2026-06-10", txns=[
            txn("t1", "2026-06-05", "-42.10", "COFFEE"),
            txn("t2", "2026-05-15", "-1.00", "PRE CUTOVER")]),
        acct("SF-CARD", "Card", "-1200.00", available="28800.00", txns=[
            txn("c1", "2026-06-04", "-55.00", "GROCERY")]),
        acct("SF-BRK", "Brokerage", "10000.00", holdings=[
            {"id": "h1", "symbol": "DEMO1", "description": "Demo Total Market",
             "shares": "10", "cost_basis": "1000.00", "market_value": "2000.00"}]),
    ])
    p = Pipeline(cfg)
    p.latest_balances = {CHK: ("2026-05-01", 100000)}   # prior CSV seed
    p.windows = {CHK: ("2024-01-01", "2026-05-31"),
                 CARD: ("2024-01-01", "2026-05-31")}
    p.parse_simplefin()

    descs = {t.description_raw for t in p.txns}
    assert "COFFEE" in descs and "GROCERY" in descs
    assert "PRE CUTOVER" not in descs                       # cutover excluded it
    # fresher cash balance injected for the CHECKING account...
    assert p.latest_balances[CHK] == ("2026-06-10", 150000)
    # ...but NOT for the credit card (would distort net worth)
    assert CARD not in p.latest_balances
    # balance timeseries holds the CSV seed + the SimpleFIN points
    sources = {(b.account_id, b.source) for b in p.balance_obs}
    assert (CHK, "csv") in sources
    assert (CHK, "simplefin") in sources
    assert (CARD, "simplefin") in sources
    # holdings captured; the account is counted among those with holdings (the simplefin.ingested detail)
    assert any(h.symbol == "DEMO1" for h in p.holdings)
    assert hh.ACCOUNTS[BRK].name in p.simplefin_holding_accounts
    # coverage window extended past the CSV end
    assert p.windows[CHK][1] == "2026-06-05"


def test_export_accounts_carries_asset_class(tmp_path):
    """accounts.json must expose asset_class — the app's cash grouping keys on it
    (a brokerage's kind is feed plumbing; its asset_class is what the money actually
    is) — as the class the account's rows take: what accounts.yaml states, else its
    kind's default, never null. The demo's 401(k) is a brokerage booked off the liquid
    "Stocks/ETF" default by its declared class."""
    cfg = demo_config()
    del cfg.accounts[hh.CERTIFICATE]["asset_class"]
    Pipeline(cfg)._export_accounts(tmp_path)
    doc = json.loads((tmp_path / "accounts.json").read_text())
    by_id = {a["id"]: a["asset_class"] for a in doc["accounts"]}
    assert by_id[hh.RETIREMENT] == "Retirement" and by_id[BRK] == "Stocks/ETF"
    assert by_id[hh.CERTIFICATE] == "Cash"                  # a savings account that states none: its kind's default
    assert by_id == {a: hh.ACCOUNTS[a].asset_class for a in hh.ACCOUNTS}


def test_balances_timeseries_exports_the_resolved_class(demo_exports):
    """The Balances and Investments pages read balances_timeseries.json's OWN accounts map, which gives each account
    the class accounts.json gives it: the one its rows take (Config.asset_class_of), never null. With one class left
    unstated the account takes its kind's; with every class left unstated, each takes its kind's and the mortgage loan
    Mortgage. On the demo's own ingest no class is null and each is accounts.json's."""
    from etl import exports as exports_mod
    from etl.models import BalanceObs
    cfg = demo_config()

    def classes() -> dict[str, str | None]:
        obs = [BalanceObs(account_id=account, as_of="2026-08-01", balance_cents=100, available_cents=None,
                          limit_cents=None, source="simplefin") for account in cfg.accounts]
        doc = exports_mod.export_balances_timeseries(obs, cfg.accounts)
        return {a: m["asset_class"] for a, m in doc["accounts"].items()}

    del cfg.accounts[hh.CERTIFICATE]["asset_class"]
    got = classes()
    assert got[hh.RETIREMENT] == "Retirement" and got[hh.CERTIFICATE] == "Cash"
    for meta in cfg.accounts.values():
        meta.pop("asset_class", None)
    got = classes()
    assert got == {a: cfg.asset_class_of(a) for a in cfg.accounts}
    assert (got[hh.RETIREMENT], got[hh.MORTGAGE], got[hh.CARD_ALEX]) == ("Stocks/ETF", "Mortgage", "Other Debt")
    exported = json.loads((demo_exports.exports / "balances_timeseries.json").read_text())["accounts"]
    listed = {a["id"]: a["asset_class"]
              for a in json.loads((demo_exports.exports / "accounts.json").read_text())["accounts"]}
    assert exported and {a: m["asset_class"] for a, m in exported.items()} == {a: listed[a] for a in exported}
    assert None not in listed.values()


def test_pipeline_dedupes_mutated_descriptor_by_sfid(tmp_path):
    """The feed re-reports the SAME transaction with a mutated description
    across snapshots (pending→posted, date tokens masked to XXXXXX) — the
    content-hash uid changes, so dedup must key on SimpleFIN's own id and the
    LATEST snapshot's wording must win, or one deposit is counted twice."""
    cfg = _cfg_with_simplefin(tmp_path)
    write_snapshot(cfg.simplefin_snapshots_dir / "accounts-20300312T000000Z.json", [
        acct("SF-CHK", "Chk", "1500.00", bal_date="2030-03-12", txns=[
            txn("dep-1", "2030-03-12", "3260.00", "Acme Payouts Ltd 8HK3 300312 X0001")])])
    write_snapshot(cfg.simplefin_snapshots_dir / "accounts-20300319T000000Z.json", [
        acct("SF-CHK", "Chk", "1500.00", bal_date="2030-03-19", txns=[
            txn("dep-1", "2030-03-12", "3260.00", "Acme Payouts Ltd 8HK3 XXXXXX X0001")])])
    p = Pipeline(cfg)
    p.windows = {CHK: ("2024-01-01", "2030-03-01")}
    p.parse_simplefin()
    deposits = [t for t in p.txns if "Acme Payouts" in t.description_raw]
    assert len(deposits) == 1                                  # not double-counted
    assert "XXXXXX" in deposits[0].description_raw             # latest wording wins
    assert p.acct_file_sums[CHK] == 326000           # counted exactly once


def test_pipeline_connection_replacement_keeps_history(tmp_path):
    """A replaced connection: old snapshots carry the old connection's id, new
    snapshots the new one. Config maps both (simplefin_id + simplefin_id_history)
    -> one continuous balance timeseries, live balance from the new connection,
    and no unmapped warns."""
    cfg = _cfg_with_simplefin(tmp_path)
    cfg.accounts[BRK]["simplefin_id"] = "SF-BRK-NEW"
    cfg.accounts[BRK]["simplefin_id_history"] = ["SF-BRK"]
    assert cfg.simplefin_map()["SF-BRK"] == cfg.simplefin_map()["SF-BRK-NEW"] == BRK
    write_snapshot(cfg.simplefin_snapshots_dir / "accounts-20260610T000000Z.json", [
        acct("SF-BRK", "old conn", "10000.00", bal_date="2026-06-10")])
    write_snapshot(cfg.simplefin_snapshots_dir / "accounts-20260612T000000Z.json", [
        acct("SF-BRK-NEW", "new conn", "10100.00", bal_date="2026-06-12", holdings=[
            {"id": "h1", "symbol": "DEMO1", "description": "Demo Total Market",
             "shares": "10", "cost_basis": "1000.00", "market_value": "2000.00"}])])
    p = Pipeline(cfg)
    p.parse_simplefin()
    points = {(b.as_of, b.balance_cents) for b in p.balance_obs
              if b.account_id == BRK}
    assert points == {("2026-06-10", 1000000), ("2026-06-12", 1010000)}
    assert p.simplefin_latest_bal[BRK] == ("2026-06-12", 1010000)
    assert BRK in p.simplefin_live_accounts     # supersession still fires
    assert any(h.symbol == "DEMO1" for h in p.holdings)   # newest snapshot's holdings
    assert not any("unmapped" in c.name for c in p.checks)


def test_pipeline_inert_without_config(tmp_path):
    """No simplefin_id mapped -> behaves exactly as the CSV-only era (only the
    CSV-derived balance seed lands in the timeseries)."""
    cfg = demo_config()
    cfg.simplefin_snapshots_dir = tmp_path / "none"      # no snapshots
    for m in cfg.accounts.values():                      # clear any live mappings
        m.pop("simplefin_id", None)
        m.pop("simplefin_id_history", None)
        m.pop("simplefin_cutover", None)
    assert cfg.simplefin_map() == {}
    p = Pipeline(cfg)
    p.latest_balances = {CHK: ("2026-05-01", 100000)}
    p.parse_simplefin()
    assert p.txns == []                       # no SimpleFIN txns added
    assert {(b.account_id, b.source) for b in p.balance_obs} == {(CHK, "csv")}


# ------------------------------------------------- a stored answer the reader cannot read

def _three_snapshots(cfg, middle: str) -> list[str]:
    """Three stored answers, the middle one's text `middle`; the outer two each report the checking account with one
    row and a balance. Returns their file names, oldest first."""
    names = ["accounts-20260601T000000Z.json", "accounts-20260605T000000Z.json", "accounts-20260610T000000Z.json"]
    d = cfg.simplefin_snapshots_dir
    write_snapshot(d / names[0], [acct("SF-CHK", "Chk", "1000.00", bal_date="2026-06-01", txns=[
        txn("t1", "2026-06-01", "-11.00", "SAMPLE FIRST")])])
    (d / names[1]).write_text(middle)
    write_snapshot(d / names[2], [acct("SF-CHK", "Chk", "1500.00", bal_date="2026-06-10", txns=[
        txn("t3", "2026-06-09", "-33.00", "SAMPLE THIRD")])])
    return names


_GOOD = json.dumps({"accounts": [acct("SF-CHK", "Chk", "1200.00", bal_date="2026-06-05", txns=[
    txn("t2", "2026-06-04", "-22.00", "SAMPLE SECOND")])]})


@pytest.mark.parametrize("middle", [_GOOD[: len(_GOOD) // 2], "", "[]", '{"accounts": null}', '{"accounts": "x"}',
                                    '{"accounts": {"id": "SF-CHK"}}'],
                         ids=["truncated", "empty", "a-list", "accounts-null", "accounts-text", "accounts-object"])
def test_a_stored_answer_the_reader_cannot_read_fails_its_own_check_and_the_others_are_read(tmp_path, middle):
    """A stored answer cut short while it was written, or not laid out as the feed's answers are, adds nothing and
    fails one check naming it in plain words; the answers before and after it are read, and the newest one is still
    the current one (its balance is live)."""
    cfg = _cfg_with_simplefin(tmp_path)
    names = _three_snapshots(cfg, middle)
    p = Pipeline(cfg)
    p.windows = {}
    p.parse_simplefin()
    assert sorted(t.description_raw for t in p.txns) == ["SAMPLE FIRST", "SAMPLE THIRD"]
    failed = [(c.name, c.detail) for c in p.checks if c.status == "fail"]
    assert failed == [(f"simplefin.snapshot_unreadable.{names[1]}",
                       f"Not read: the bank feed's stored answer {names[1]} is damaged or not laid out as the feed's "
                       "answers are, so it adds nothing; the other stored answers are read. Remove that one file from "
                       "the bank feed's store: the answers fetched before and after it overlap it, so they keep most "
                       "of its days.")]
    assert p.simplefin_live_accounts == {CHK} and p.simplefin_latest_bal[CHK] == ("2026-06-10", 150000)
    assert [s.source_file for s in p.file_stats] == [names[0], names[2]]
    # the scan for the accounts the feed reports passes over the damaged answer too
    assert p._feed_observed({CHK}) == {CHK}


def test_the_newest_answer_unreadable_leaves_the_one_before_it_current(tmp_path):
    cfg = _cfg_with_simplefin(tmp_path)
    d = cfg.simplefin_snapshots_dir
    write_snapshot(d / "accounts-20260601T000000Z.json", [acct("SF-BRK", "Brokerage", "10000.00", holdings=[
        {"id": "h1", "symbol": "DEMO1", "description": "Demo Total Market", "shares": "10", "cost_basis": "1000.00",
         "market_value": "2000.00"}])])
    (d / "accounts-20260610T000000Z.json").write_text('{"accounts": [')
    p = Pipeline(cfg)
    p.windows = {}
    p.parse_simplefin()
    assert p.simplefin_live_accounts == {BRK} and [h.symbol for h in p.holdings] == ["DEMO1"]
    assert [c.name for c in p.checks if c.status == "fail"] == [
        "simplefin.snapshot_unreadable.accounts-20260610T000000Z.json"]
    # how old the data is: the newest answer read, not the newest file's name
    assert p.last_fetch(cfg.find_simplefin_snapshots()) == "2026-06-01T00:00:00Z"
    (d / "accounts-20260610T000000Z.json").unlink()
    write_snapshot(d / "accounts-20260610T000000Z.json", [acct("SF-BRK", "Brokerage", "10000.00")])
    p = Pipeline(cfg)
    p.windows = {}
    p.parse_simplefin()
    assert p.last_fetch(cfg.find_simplefin_snapshots()) == "2026-06-10T00:00:00Z"


def _probe(**changes) -> dict:
    """The middle answer's checking account, with one row, its fields changed by `changes` (`row` the row's)"""
    row = {**txn("t2", "2026-06-04", "-22.00", "SAMPLE SECOND"), **changes.pop("row", {})}
    return {**acct("SF-CHK", "Chk", "1200.00", bal_date="2026-06-05", txns=[row]), **changes}


# each probe: the middle answer's accounts, the check it raises (None for none) and whether its row is read
PROBES = {
    "posted-text": ([_probe(row={"posted": "x"})], f"simplefin.bad_date.{CHK}", False),
    "posted-float-text": ([_probe(row={"posted": str(float(epoch("2026-06-04")))})], None, True),
    "balance-date-text": ([_probe(**{"balance-date": "x"})], f"simplefin.bad_balance_date.{CHK}", True),
    "balance-date-past-any-year": ([_probe(**{"balance-date": 99999999999999})],
                                   f"simplefin.bad_balance_date.{CHK}", True),
    "row-extra-text": ([_probe(row={"extra": "x"})], None, True),
    "unmapped-org-text": ([_probe(), {"id": "SF-OTHER", "name": "Other", "org": "Bank", "balance": "1.00"}],
                          "simplefin.unmapped.SF-OTHER", True),
    "amount-a-list": ([_probe(row={"amount": [1]})], f"simplefin.bad_amount.{CHK}", False),
    "amount-infinite": ([_probe(row={"amount": float("inf")})], f"simplefin.bad_amount.{CHK}", False),
    "amount-twenty-digits": ([_probe(row={"amount": "99999999999999999999.00"})], f"simplefin.bad_amount.{CHK}", False),
    "description-a-number": ([_probe(row={"description": 7})], None, True),
    "row-not-an-object": ([_probe(transactions=[txn("t2", "2026-06-04", "-22.00", "SAMPLE SECOND"), "x"])],
                          f"simplefin.bad_row.{CHK}", True),
    "transactions-text": ([_probe(transactions="x")], f"simplefin.bad_row.{CHK}", False),
    "transactions-null": ([_probe(transactions=None)], None, False),
    "holding-not-an-object": ([_probe(holdings=["x"])], f"simplefin.bad_row.{CHK}", True),
    "account-not-an-object": ([_probe(), "x"], "simplefin.bad_account.accounts-20260605T000000Z.json", True),
    "account-id-a-list": ([_probe(), {"id": ["SF-CHK"], "balance": "1.00"}],
                          "simplefin.bad_account.accounts-20260605T000000Z.json", True),
}


@pytest.mark.parametrize("probe", list(PROBES))
def test_an_account_or_row_of_the_wrong_shape_is_skipped_with_a_warning_and_the_rest_read(tmp_path, probe):
    accounts, check, row_read = PROBES[probe]
    cfg = _cfg_with_simplefin(tmp_path)
    _three_snapshots(cfg, json.dumps({"accounts": accounts}))
    p = Pipeline(cfg)
    p.windows = {}
    p.parse_simplefin()
    rows = sorted(p.txns, key=lambda t: t.date)
    assert rows[0].description_raw == "SAMPLE FIRST" and rows[-1].description_raw == "SAMPLE THIRD"
    assert [t.description_raw for t in rows[1:-1]] == ([{"description-a-number": "7"}.get(probe, "SAMPLE SECOND")]
                                                      if row_read else [])
    assert [c.name for c in p.checks if c.status == "fail"] == []
    raised = [c.name for c in p.checks if c.status == "warn" and not c.name.startswith(("simplefin.stale.",
                                                                                        "simplefin.cutover_"))]
    assert raised == ([check] if check else [])


@pytest.mark.parametrize("epoch_value,expected", [
    (1_900_000_000, "2030-03-17"), ("1900000000", "2030-03-17"), ("1900000000.0", "2030-03-17"),
    (1_900_000_000.7, "2030-03-17"), (0, None), ("", None), (None, None), ("x", None), (True, None), ([1], None),
    (99_999_999_999_999, None), (float("nan"), None), (float("inf"), None), ("1e400", None)])
def test_an_epoch_is_read_from_a_number_or_numeric_text_and_anything_else_is_no_date(epoch_value, expected):
    assert sfp._iso(epoch_value, dt.timezone.utc) == expected


# --------------------------------------------------------------- fetch module

class FakeCfg:
    def __init__(self, base):
        self.simplefin_dir = base
        self.simplefin_snapshots_dir = base / "snaps"
        self.simplefin_credential_path = base / "credentials" / "access_url"

    def find_simplefin_snapshots(self):
        d = self.simplefin_snapshots_dir
        return sorted(d.glob("accounts-*.json")) if d.exists() else []


def test_claim_writes_600_and_refuses_overwrite(tmp_path, monkeypatch):
    cfg = FakeCfg(tmp_path)
    calls = {}

    def fake_request(url, method="GET", data=None):
        calls["url"], calls["method"] = url, method
        return b"https://user:pass@beta-bridge.simplefin.org/simplefin"

    monkeypatch.setattr(sf, "_request", fake_request)
    # token is base64 of a claim URL
    import base64
    token = base64.b64encode(b"https://beta-bridge.simplefin.org/simplefin/claim/XYZ").decode()
    sf.claim(cfg, token)
    assert calls["method"] == "POST"
    assert oct(cfg.simplefin_credential_path.stat().st_mode & 0o777) == "0o600"
    with pytest.raises(SystemExit):
        sf.claim(cfg, token)              # refuses to overwrite without --force
    sf.claim(cfg, token, force=True)      # force is allowed


def test_read_access_url_refuses_world_readable(tmp_path):
    cfg = FakeCfg(tmp_path)
    sf.write_access_url(cfg.simplefin_credential_path, "https://u:p@host/simplefin")
    os.chmod(cfg.simplefin_credential_path, 0o644)
    with pytest.raises(SystemExit):
        sf.read_access_url(cfg.simplefin_credential_path)


class _FakeResp:
    def __init__(self, body): self._body = body
    def read(self): return self._body
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _urlopen_script(monkeypatch, outcomes):
    """Patch the module's one opener to yield `outcomes` in order (an exception is
    raised, bytes are returned) and record every attempt. Sleeps are stubbed so
    retries cost no time."""
    attempts = []

    def fake_open(req, timeout=None):
        attempts.append(req.get_method())
        out = outcomes[len(attempts) - 1]
        if isinstance(out, Exception):
            raise out
        return _FakeResp(out)

    monkeypatch.setattr(sf._opener(), "open", fake_open)
    monkeypatch.setattr(sf.time, "sleep", lambda s: None)
    return attempts


def _http_error(code):
    import email.message
    import io
    return sf.urllib.error.HTTPError(
        "https://host/simplefin/accounts", code, "boom", email.message.Message(),
        io.BytesIO(b"denied"))


def test_request_retries_transient_stall_then_succeeds(monkeypatch):
    """The Bridge's failure mode: it accepts the connection, then never answers.
    One stalled attempt must not cost the whole refresh cycle."""
    attempts = _urlopen_script(
        monkeypatch, [TimeoutError("read timed out"), b'{"accounts": []}'])
    assert sf._request("https://u:p@host/simplefin/accounts") == b'{"accounts": []}'
    assert len(attempts) == 2


def test_request_gives_up_with_clean_message_not_a_traceback(monkeypatch):
    """A read timeout is a bare TimeoutError (urllib only wraps connect errors in
    URLError), so it used to escape as a raw traceback."""
    attempts = _urlopen_script(monkeypatch, [TimeoutError("t")] * 3)
    with pytest.raises(SystemExit) as ei:
        sf._request("https://u:p@host/simplefin/accounts")
    assert len(attempts) == 3                       # 1 + len(_RETRY_WAITS)
    assert "timed out" in str(ei.value)


def test_request_does_not_retry_permanent_auth_failure(monkeypatch):
    """A revoked credential is permanent; retrying would spend three of the <=24
    daily requests to fail in exactly the same way."""
    attempts = _urlopen_script(monkeypatch, [_http_error(403)] * 3)
    with pytest.raises(SystemExit) as ei:
        sf._request("https://u:p@host/simplefin/accounts")
    assert len(attempts) == 1
    assert "403" in str(ei.value)


def test_request_retries_a_server_error(monkeypatch):
    for code in (500, 503):
        attempts = _urlopen_script(monkeypatch, [_http_error(code), b"{}"])
        assert sf._request("https://u:p@host/simplefin/accounts") == b"{}"
        assert len(attempts) == 2, code


def test_request_does_not_retry_a_rate_limit(monkeypatch):
    """A 429 spends no more of the day's request budget: the next scheduled refresh asks again."""
    attempts = _urlopen_script(monkeypatch, [_http_error(429), b"{}"])
    with pytest.raises(SystemExit) as ei:
        sf._request("https://u:p@host/simplefin/accounts")
    assert len(attempts) == 1 and "429" in str(ei.value)


def test_claim_post_is_never_retried(monkeypatch):
    """Claim spends a single-use setup token — a replay could burn it for nothing."""
    attempts = _urlopen_script(monkeypatch, [TimeoutError("t")] * 3)
    with pytest.raises(SystemExit) as ei:
        sf._request("https://host/simplefin/claim/XYZ", method="POST", data=b"")
    assert attempts == ["POST"]
    assert "1 attempt)" in str(ei.value)   # reports what it actually did, not the GET budget
    # the path it names stops before the setup token, which a timeout may have left unspent
    assert str(ei.value) == f"SimpleFIN request timed out after {sf.TIMEOUT}s for POST /simplefin/claim (1 attempt)"


def test_each_error_list_is_read_in_either_shape_one_warn_line_each(capsys):
    """Version 1's `errors` holds strings and version 2's `errlist` objects, but either list may hold either shape. An
    object naming a connection is worded as version 1 words it, with the connection's name (else its id), which
    deploy/refresh.sh reads; a string or an object is one line however many lines its text runs to."""
    sf._warn_errors({
        "errors": ["Connection to Sample Bank may need attention. Auth required",
                   {"code": "con.auth", "msg": "Sign in again", "conn_id": "CON-fake-0002"}],
        "errlist": [{"code": "gen.", "msg": "Sample notice"}, "A plain\nstring entry", 7,
                    {"code": "con.auth", "msg": "Sign in\nagain", "conn_id": "CON-fake-0009"}],
        "connections": [{"conn_id": "CON-fake-0002", "name": "Sample Credit Union"}]})
    assert capsys.readouterr().err.splitlines() == [
        "  WARN simplefin: Connection to Sample Bank may need attention. Auth required",
        "  WARN simplefin: Connection to Sample Credit Union may need attention. [con.auth] Sign in again CON-fake-0002",
        "  WARN simplefin: [gen.] Sample notice",
        "  WARN simplefin: A plain string entry",
        "  WARN simplefin: Connection to CON-fake-0009 may need attention. [con.auth] Sign in again CON-fake-0009"]


def test_retry_notice_avoids_the_reauth_alert_prefix(monkeypatch, capsys):
    """deploy/refresh.sh treats any "WARN simplefin" line as a connection needing
    re-auth, so a recovered retry must not use that prefix or it would alert."""
    _urlopen_script(monkeypatch, [TimeoutError("t"), b"{}"])
    sf._request("https://u:p@host/simplefin/accounts")
    assert "WARN simplefin" not in capsys.readouterr().err


def test_fetch_appends_snapshot_and_never_start_zero(tmp_path, monkeypatch):
    cfg = FakeCfg(tmp_path)
    sf.write_access_url(cfg.simplefin_credential_path, "https://u:p@host/simplefin")
    seen = {}

    def fake_request(url, method="GET", data=None):
        seen["url"] = url
        return json.dumps({"accounts": [{"id": "x", "name": "n", "balance": "1.00",
                                         "balance-date": epoch("2026-06-10")}],
                           "errlist": [{"code": "con.auth", "msg": "reauth needed",
                                        "conn_id": "c1"}]}).encode()

    monkeypatch.setattr(sf, "_request", fake_request)
    out = sf.fetch(cfg, days=35)
    assert out.exists() and out.name.startswith("accounts-")
    # explicit non-zero start-date (the budget foot-gun guard)
    assert "start-date=" in seen["url"] and "start-date=0" not in seen["url"]
    assert len(cfg.find_simplefin_snapshots()) == 1

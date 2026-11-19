# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Tests for the SimpleFIN roadmap foundation: the stale-feed checks, the current snapshot's
feed rows, holdings asset-class override, the holdings-coverage tripwire, simplefin_ignore,
balances-only asset feeds, and the daily net-worth invariant. Synthetic data, no network."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

from etl import exports, networth
from etl.models import BalanceObs, Holding, account_asset_class
from etl.parsers import simplefin as sfp
from etl.pipeline import Pipeline
from etl.quality import reconcile


def _epoch(date_str: str) -> int:
    y, m, d = map(int, date_str.split("-"))
    return int(dt.datetime(y, m, d, 12, tzinfo=dt.timezone.utc).timestamp())


TZ = "UTC"      # the household zone the feed is read in; noon-UTC epochs date the same day in any nearby zone


def _acct(sfid, name, balance, *, bal_date="2026-06-19", org="Bank",
          txns=None, holdings=None):
    return {"id": sfid, "name": name, "org": {"name": org}, "currency": "USD",
            "balance": balance, "balance-date": _epoch(bal_date),
            "transactions": txns or [], "holdings": holdings or []}


def _txn(tid, date, amount, desc):
    return {"id": tid, "posted": _epoch(date), "amount": amount, "description": desc}


def _write(path, accounts):
    path.write_text(json.dumps({"accounts": accounts}))
    return path


# --------------------------------------------------------------- F3: asset_class

def test_the_accounts_asset_class_classes_its_holdings(tmp_path):
    snap = _write(tmp_path / "accounts-1.json", [
        _acct("SF-DEMO-P", "Sample Plan", "7000.00", holdings=[
            {"symbol": "SMPL", "description": "Sample fund", "shares": "10",
             "cost_basis": "5000.00", "market_value": "7000.00"}])])
    meta = {"demo_brokerage_0000": {"name": "Sample Plan", "institution": "Sample", "owner": "Sam",
                                    "kind": "brokerage", "asset_class": "529"}}
    holdings = sfp.parse_snapshot(snap, {"SF-DEMO-P": "demo_brokerage_0000"}, {}, meta, tz=TZ)[2]
    assert holdings and holdings[0].asset_class == "529"
    assert holdings[0].account_id == "demo_brokerage_0000"      # keyed by the registry account


def test_default_asset_class_is_stocks_etf(tmp_path):
    snap = _write(tmp_path / "accounts-2.json", [
        _acct("SF-BRK", "Brokerage", "100.00", holdings=[
            {"symbol": "AAPL", "shares": "1", "cost_basis": "90", "market_value": "100"}])])
    meta = {"brk": {"name": "Brk", "institution": "X", "owner": "Alex", "kind": "brokerage"}}
    holdings = sfp.parse_snapshot(snap, {"SF-BRK": "brk"}, {}, meta, tz=TZ)[2]
    assert holdings[0].asset_class == "Stocks/ETF"


# ---------------------------------------------------- balances-only asset feeds

def test_balances_only_skips_transactions_keeps_balance_and_holdings(tmp_path):
    # a brokerage with no CSV parser and no cutover is a pure asset feed
    snap = _write(tmp_path / "accounts-3.json", [
        _acct("SF-IRA", "IRA", "300000.00", txns=[
            _txn("r1", "2030-02-12", "-88.40", "Reinvestment Sample Total Market Fund")],
            holdings=[{"symbol": "VTI", "shares": "1000", "cost_basis": "0",
                       "purchase_price": "250", "market_value": "300000.00"}])])
    meta = {"ira": {"name": "IRA", "institution": "Example Brokerage", "owner": "Alex",
                    "kind": "brokerage"}}
    txns, balances, holdings, *_ = sfp.parse_snapshot(snap, {"SF-IRA": "ira"}, {}, meta, tz=TZ)
    assert txns == []                       # internal reinvestment not imported
    assert balances and holdings            # balance + holdings still flow


def test_balances_only_override_false_imports_txns(tmp_path):
    snap = _write(tmp_path / "accounts-4.json", [
        _acct("SF-BRK", "Brk", "100.00", txns=[_txn("t", "2026-06-10", "-10", "BUY")])])
    meta = {"brk": {"name": "Brk", "institution": "X", "owner": "Alex",
                    "kind": "brokerage", "simplefin_balances_only": False}}
    txns = sfp.parse_snapshot(snap, {"SF-BRK": "brk"}, {}, meta, tz=TZ)[0]
    assert len(txns) == 1                    # explicit override re-enables import


# ------------------------------------------------------------ simplefin_ignore

def test_simplefin_ignore_suppresses_unmapped_warn(tmp_path):
    snap = _write(tmp_path / "accounts-5.json", [_acct("SF-DUP", "Twin", "9763.31")])
    checks = sfp.parse_snapshot(snap, {}, {}, {}, tz=TZ, ignore={"SF-DUP"})[4]
    assert not any(c.name.startswith("simplefin.unmapped") for c in checks)
    # without ignore, the same account warns
    checks2 = sfp.parse_snapshot(snap, {}, {}, {}, tz=TZ)[4]
    assert any(c.name == "simplefin.unmapped.SF-DUP" for c in checks2)


# ----------------------------------------------- F4: holdings-coverage tripwire

def test_holdings_coverage_thresholds():
    cc = reconcile.holdings_coverage
    assert cc("a", "A", 100_00, 100_00).status == "info"          # holdings == balance
    assert cc("a", "A", 100_00, 99_50).status == "info"           # small cash sweep
    assert cc("a", "A", 100_00, 110_00).status == "warn"          # holdings exceed balance
    assert cc("a", "A", 1000_00, 400_00).status == "warn"         # holdings cover <half — dropped feed
    assert cc("a", "A", 100_00, 110_00, is_margin=True).status == "info"  # margin: expected


# -------------------------------------------------- stale feeds (live vs dropped)

def test_stale_check_warns_for_every_mapped_account_that_did_not_report():
    """A mapped brokerage the feed did not report this run is carried in net worth at its last
    balance, so the total no longer moves with it. This is the check that makes that loud: once per
    silent account, only for accounts with a feed id, saying what net worth does with it."""
    accounts = {
        "demo_brokerage_0000": _demo_account("Sample Live", simplefin_id="SF-DEMO-L"),
        "demo_brokerage_0001": _demo_account("Sample Dropped", simplefin_id="SF-DEMO-D"),
        "demo_brokerage_0002": _demo_account("Sample Never Seen", simplefin_id="SF-DEMO-N"),
        "demo_card_0000": _demo_account("Sample Card", kind="credit-card", simplefin_id="SF-DEMO-C"),
        "demo_checking_0000": _demo_account("Sample File Only", kind="checking"),   # no simplefin_id
    }
    p = _live_pipeline(accounts, {"demo_brokerage_0000": ("2030-01-10", 100_00),
                                  "demo_brokerage_0001": ("2030-01-02", 500_00),
                                  "demo_card_0000": ("2030-01-03", -20_00)}, {"demo_brokerage_0000"})
    p.simplefin_snapshot_read = True
    p.parse_assets()
    checks = {c.name: c for c in p._simplefin_staleness_checks()}
    assert set(checks) == {"simplefin.stale.demo_brokerage_0001", "simplefin.stale.demo_brokerage_0002",
                           "simplefin.stale.demo_card_0000"}
    assert {c.status for c in checks.values()} == {"warn"}
    dropped = checks["simplefin.stale.demo_brokerage_0001"].detail
    assert "last seen 2030-01-02" in dropped                                             # when it went quiet
    assert "net worth carries its last balance, from 2030-01-02" in dropped and "contributing nothing" not in dropped
    assert "never seen in any snapshot" in checks["simplefin.stale.demo_brokerage_0002"].detail
    assert "no balance of it to carry" in checks["simplefin.stale.demo_brokerage_0002"].detail
    assert "net worth does not count it" in checks["simplefin.stale.demo_card_0000"].detail   # cards are off here
    # ...and the dropped account is carried, not dropped from the total
    rows = {r.account_id: r for r in p.asset_rows}
    assert (rows["demo_brokerage_0001"].value_cents, rows["demo_brokerage_0001"].as_of) == (500_00, "2030-01-02")

    # ...and it stays silent when there is no snapshot to be absent from, so a fresh
    # clone / CSV-only run doesn't warn once per mapped account.
    p.simplefin_snapshot_read = False
    assert p._simplefin_staleness_checks() == []


def _age_pipeline(latest_bal: dict, live: set, accounts: dict, newest: str):
    p = Pipeline(SimpleNamespace(accounts=accounts))
    p.simplefin_snapshot_read = True
    p.simplefin_live_accounts = live
    p.simplefin_latest_bal = latest_bal
    p.balance_obs = [BalanceObs(account_id="fresh", as_of=newest, balance_cents=1,
                                source="simplefin")]
    return p


def test_balance_age_check_flags_a_connection_stuck_upstream():
    """A feed connection that goes "needs attention" can keep re-sending one old balance, so the
    account stays "live" in every snapshot and nothing warns for weeks. The account's own
    balance-date is the tell."""
    accounts = {
        "fresh":  {"name": "Fresh", "simplefin_id": "SF-F"},
        "frozen": {"name": "Frozen", "simplefin_id": "SF-Z"},
        "edge":   {"name": "Edge", "simplefin_id": "SF-E"},
        "gone":   {"name": "Gone", "simplefin_id": "SF-G"},  # not live: other check's job
    }
    p = _age_pipeline(
        {"fresh": ("2030-04-18", 1), "frozen": ("2030-03-26", 1),
         "edge": ("2030-04-13", 1),                  # exactly 5 days: still healthy
         "gone": ("2029-12-02", 1)},
        {"fresh", "frozen", "edge"}, accounts, "2030-04-18")
    checks = p._simplefin_balance_age_checks()
    assert [c.name for c in checks] == ["simplefin.balance_stale.frozen"]
    assert checks[0].status == "warn"
    assert "2030-03-26" in checks[0].detail and "23 days" in checks[0].detail


def test_balance_age_check_is_silent_without_a_snapshot():
    p = _age_pipeline({"x": ("2020-01-01", 1)}, {"x"},
                      {"x": {"name": "X", "simplefin_id": "SF-X"}}, "2026-10-01")
    p.simplefin_snapshot_read = False
    assert p._simplefin_balance_age_checks() == []


# ----------------------------------------- live-feed valuation of the current snapshot

def _demo_account(name: str, kind: str = "brokerage", **extra) -> dict:
    return {"name": name, "institution": "Sample", "owner": "Alex", "kind": kind, **extra}


def _live_pipeline(accounts: dict, latest_bal: dict, live: set) -> Pipeline:
    """A pipeline whose feed reported `latest_bal` ({id: (date, cents)}: the balance series and the newest feed
    balances alike), with `live` the accounts in the newest file. Credit cards are not counted in net worth."""
    p = Pipeline(SimpleNamespace(
        accounts=accounts, accounts_cfg={"accounts": accounts},
        manual_assets_path=Path("/nonexistent-manual-assets/manual_assets.json"), owners=("Alex", "Joint"),
        net_worth_options={"include_credit_cards": False}, timezone="UTC",
        margin_account_ids=lambda: [a for a, m in accounts.items() if m.get("margin")],
        mortgage_account_id=lambda: next((a for a, m in accounts.items() if m.get("mortgage")), None),
        asset_class_of=lambda a: account_asset_class(accounts[a])))
    p.simplefin_live_accounts = set(live)
    p.simplefin_latest_bal = dict(latest_bal)
    p.balance_obs = [BalanceObs(a, d, cents) for a, (d, cents) in latest_bal.items()]
    return p


def _demo_holding(account_id: str, name: str, value_cents: int, asset_class: str = "Stocks/ETF") -> Holding:
    return Holding(account=name, account_id=account_id, institution="Sample", owner="Alex",
                   symbol="SMPL", name="Sample fund", asset_class=asset_class, quantity=1,
                   value_cents=value_cents, cost_basis_cents=None, as_of="2030-01-10")


def test_a_live_brokerage_is_one_row_at_its_feed_balance_not_its_holdings_sum():
    """The feed's own balance includes the uninvested cash its holdings leave out, so the
    current snapshot books that balance, once; the holdings stay its decomposition."""
    accounts = {"demo_brokerage_0000": _demo_account("Sample Brokerage", simplefin_id="SF-DEMO-B")}
    p = _live_pipeline(accounts, {"demo_brokerage_0000": ("2030-01-10", 1000_00)},
                       {"demo_brokerage_0000"})
    p.holdings = [_demo_holding("demo_brokerage_0000", "Sample Brokerage", 900_00)]
    p.parse_assets()
    rows = [(r.account_id, r.value_cents, r.source, r.as_of) for r in p.asset_rows]
    assert rows == [("demo_brokerage_0000", 1000_00, "simplefin_balance", "2030-01-10")]   # once, no Cash row
    assert [c.name for c in p._holdings_coverage_checks()] == [
        "simplefin.holdings_coverage.demo_brokerage_0000"]


def test_a_brokerage_with_no_holdings_is_one_row_at_its_balance_and_a_dropped_one_is_carried():
    accounts = {
        "demo_brokerage_0000": _demo_account("Sample Taxable", asset_class="Stocks/ETF"),
        "demo_brokerage_0001": _demo_account("Sample Plan", asset_class="Retirement"),
        "demo_brokerage_0003": _demo_account("Sample Undeclared"),  # no class: the brokerage default
        "demo_brokerage_0002": _demo_account("Sample Quiet"),       # did not report this run
        "demo_checking_0000": _demo_account("Sample Checking", kind="checking"),
    }
    p = _live_pipeline(
        accounts,
        {"demo_brokerage_0000": ("2030-01-10", 500_00), "demo_brokerage_0001": ("2030-01-09", 700_00),
         "demo_brokerage_0002": ("2030-01-02", 300_00), "demo_brokerage_0003": ("2030-01-10", 200_00),
         "demo_checking_0000": ("2030-01-10", 50_00)},
        {"demo_brokerage_0000", "demo_brokerage_0001", "demo_brokerage_0003", "demo_checking_0000"})
    p.parse_assets()
    rows = {r.account_id: r for r in p.asset_rows}
    assert set(rows) == set(accounts)
    taxable, plan, undeclared = rows["demo_brokerage_0000"], rows["demo_brokerage_0001"], rows["demo_brokerage_0003"]
    assert (taxable.asset_class, taxable.liquid, taxable.value_cents, taxable.source, taxable.as_of) == (
        "Stocks/ETF", True, 500_00, "simplefin_balance", "2030-01-10")
    assert (plan.asset_class, plan.liquid, plan.value_cents) == ("Retirement", False, 700_00)
    assert (undeclared.asset_class, undeclared.liquid, undeclared.value_cents) == ("Stocks/ETF", True, 200_00)
    # a mapped account that did not report this run is carried at its last balance, dated by it, and the
    # stale check says so; the snapshot is dated by the newest observation, not by the carried one
    quiet = rows["demo_brokerage_0002"]
    assert (quiet.asset_class, quiet.value_cents, quiet.source, quiet.as_of) == (
        "Stocks/ETF", 300_00, "simplefin_balance", "2030-01-02")
    assert p.current_snapshot_date == "2030-01-10" and p.snapshot_coverage["2030-01-10"] == (1.0, [])


def test_every_position_of_one_feed_account_takes_the_accounts_one_class(tmp_path):
    """Every position of a feed account takes the account's one class, so a live margin account's
    positions are one gross row per account, never one per security."""
    snap = _write(tmp_path / "accounts-6.json", [
        _acct("SF-DEMO-R", "Sample Plan", "300.00", bal_date="2030-01-10", holdings=[
            {"symbol": "SMPA", "shares": "1", "cost_basis": "90", "market_value": "100.00"},
            {"symbol": "SMPB", "shares": "2", "cost_basis": "150", "market_value": "200.00"}])])
    meta = {"demo_brokerage_0000": _demo_account("Sample Plan", asset_class="Retirement", margin=True)}
    holdings = sfp.parse_snapshot(snap, {"SF-DEMO-R": "demo_brokerage_0000"}, {}, meta, tz=TZ)[2]
    assert len(holdings) == 2 and {h.asset_class for h in holdings} == {"Retirement"}
    p = _live_pipeline(meta, {"demo_brokerage_0000": ("2030-01-10", 300_00)}, {"demo_brokerage_0000"})
    p.holdings = holdings
    p.parse_assets()
    assert [(r.asset_class, r.value_cents, r.source) for r in p.asset_rows] == [("Retirement", 300_00, "positions")]


def test_feed_positions_keep_class_liquidity_and_a_real_date():
    """A 529-class account books one illiquid row at its live balance and the feed's date, and so does a
    zero balance: a live account that is not a margin account is booked at its balance whatever its
    holdings sum. A margin account is its positions, dated by them, plus the remainder at the balance's
    date, every row carrying its id. No row is assumed-dated."""
    accounts = {
        "demo_brokerage_0000": _demo_account("Sample Plan", simplefin_id="SF-DEMO-P", asset_class="529"),
        "demo_brokerage_0001": _demo_account("Sample Leveraged", simplefin_id="SF-DEMO-M", margin=True),
        "demo_brokerage_0002": _demo_account("Sample Zero", simplefin_id="SF-DEMO-G"),
    }
    p = _live_pipeline(accounts, {"demo_brokerage_0000": ("2030-01-11", 800_00),
                                  "demo_brokerage_0001": ("2030-01-11", 300_00),
                                  "demo_brokerage_0002": ("2030-01-11", 0)}, set(accounts))
    p.holdings = [_demo_holding("demo_brokerage_0000", "Sample Plan", 700_00, asset_class="529"),
                  _demo_holding("demo_brokerage_0001", "Sample Leveraged", 500_00),
                  _demo_holding("demo_brokerage_0002", "Sample Zero", 200_00)]
    p.parse_assets()
    got = [(r.account_id, r.account, r.asset_class, r.value_cents, r.source, r.as_of, r.liquid) for r in p.asset_rows]
    assert got == [
        ("demo_brokerage_0000", "Sample Plan", "529", 800_00, "simplefin_balance", "2030-01-11", False),
        ("demo_brokerage_0001", "Sample Leveraged", "Stocks/ETF", 500_00, "positions", "2030-01-10", True),
        ("demo_brokerage_0001", "Sample Margin", "Margin", -200_00, "simplefin_balance", "2030-01-11", False),
        ("demo_brokerage_0002", "Sample Zero", "Stocks/ETF", 0, "simplefin_balance", "2030-01-11", True),
    ]
    assert not any(r.as_of_assumed for r in p.asset_rows)


# ------------------------------------------------- Phase C: daily net-worth invariant

def test_networth_daily_invariant_and_coverage():
    meta = {"chk": {"kind": "checking", "name": "Chk", "institution": "B", "owner": "J"},
            "brk": {"kind": "brokerage", "name": "Brk", "institution": "B", "owner": "J"},
            "card": {"kind": "credit-card", "name": "Card", "institution": "B", "owner": "J"}}
    obs = [BalanceObs("chk", "2026-06-01", 100_00),
           BalanceObs("chk", "2026-06-10", 120_00),
           BalanceObs("brk", "2026-06-10", 500_00),     # first seen only on the 10th
           BalanceObs("card", "2026-06-10", -5_00)]     # excluded from net worth
    nw_series = networth.build_series(obs, meta, [], include_credit_cards=False)
    current = networth.snapshot_at(nw_series, networth.current_date(nw_series, "2026-06-10"))
    current_total = sum(r.value_cents for r in current.rows)
    assert current_total == 620_00                      # chk 120 + brk 500 (card excluded)
    out = exports.export_networth_daily(nw_series, current_total, current.date)
    series = out["series"]
    # INVARIANT: the last point equals the current snapshot total exactly
    assert series[-1]["networth"] == out["current_total"] == 620.0
    assert series[-1]["coverage"] == 1.0               # both NW accounts observed
    # earlier day: brk not observed yet → partial coverage
    assert series[0]["date"] == "2026-06-01"
    assert series[0]["coverage"] == 0.5
    # the credit card never enters the net-worth series
    assert all(p["networth"] > 0 for p in series)

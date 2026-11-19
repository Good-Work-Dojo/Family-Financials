# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The `full` demo household's balance sheet against its truth: the net-worth snapshots and the daily line, the
quality checks and the ledger drift. Truth declares each account's balance from the generator's ledger, as the feed
and the statements reported it; these tests show the engine books the same. Everything here is invented."""
from __future__ import annotations

import importlib.util
import json
import re
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import demo_checks
import pytest
import yaml
from conftest import DEMO_END, ROOT

from tools.demo import household as hh
from tools.demo import plan as plan_mod
from tools.demo.behaviours import balance_sheet
from tools.demo.dates import shift_months

_spec = importlib.util.spec_from_file_location("scenario_store", ROOT / "deploy" / "scenario_store.py")
scenario_store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scenario_store)


def load(exports: Path, name: str) -> dict:
    return json.loads((exports / name).read_text("utf-8"))


def cents(dollars: float) -> int:
    return round(dollars * 100)


@pytest.fixture(scope="module")
def txns(demo_exports) -> dict[str, dict]:
    return {t["id"]: t for t in load(demo_exports.exports, "transactions.json")["transactions"]}


@pytest.fixture(scope="module")
def snapshots(demo_exports) -> dict[str, dict]:
    return {s["snapshot_date"]: s for s in load(demo_exports.exports, "assets_timeseries.json")["snapshots"]}


@pytest.fixture(scope="module")
def figures(demo_exports) -> dict:
    return demo_exports.truth["figures"]


# ------------------------------------------------------------------ snapshots and the daily line

def test_snapshots_equal_truth(demo_exports, snapshots, figures):
    assert list(snapshots) == [s["date"] for s in figures["snapshots"]]
    for w in figures["snapshots"]:
        assert w["total_cents"] == w["assets_cents"] + w["liabilities_cents"] == sum(w["by_class"].values())
    assert demo_checks.snapshots(demo_exports.exports, demo_exports.truth) == []


def test_every_full_month_end_equals_the_daily_line(demo_exports, figures):
    assert figures["comparable_month_ends"]
    assert demo_checks.daily_line(demo_exports.exports, demo_exports.truth) == []


def test_the_current_total_and_the_daily_lines_last_point(demo_exports, snapshots, figures):
    doc = load(demo_exports.exports, "networth_daily.json")
    end = demo_exports.truth["end"]
    assert doc["current_snapshot_date"] == end == list(snapshots)[-1]
    assert figures["current_total_cents"] == cents(snapshots[end]["total"])
    assert figures["daily_last"]["date"] == doc["series"][-1]["date"]
    assert demo_checks.daily_line(demo_exports.exports, demo_exports.truth) == []


def manual_asset(truth: dict, handle: str) -> dict:
    """Truth's manual asset whose id the planted handle names."""
    [asset] = [a for a in truth["figures"]["manual_assets"] if a["id"] == truth["planted"][handle]]
    return asset


def test_the_home_changes_value_and_each_snapshot_uses_the_value_in_force(demo_exports, snapshots, figures):
    """The home is an asset entered in the app with three values, rising: the first on the history's first day, long
    before any account reports a balance, then about a year and about a month before the end. Each snapshot holds it
    at the value in force on its day (the last one dated on or before it), so it is never carried back and never
    missing, and every value is in force in some snapshot. A stated value starts no month: the history begins with the
    first month an account reports, and a month end that misses an account (the balance-sheet accounts join the feed
    after the everyday ones) holds what had reported by then, the home among it."""
    truth = demo_exports.truth
    home, end = manual_asset(truth, "home.entry"), date.fromisoformat(truth["end"])
    start = min(figures["months"]) + "-01"
    values = home["values"]
    assert (home["origin"], home["asset_class"], home["owner"], home["institution"], home["acquired"],
            home["liquid"]) == ("app", "House", hh.JOINT, None, None, False)
    assert [v["as_of"] for v in values] == [start, shift_months(end, -12).isoformat(), shift_months(end, -1).isoformat()]
    worth = [v["value_cents"] for v in values]
    assert worth == sorted(set(worth)) and worth[-1] == balance_sheet.HOME_VALUES_CENTS[-1]
    in_force = set()
    for day, snap in snapshots.items():
        [row] = [r for r in snap["rows"] if r["asset_class"] == "House"]
        value = [v for v in values if v["as_of"] <= day][-1]
        assert (row["account"], row["account_id"], row["manual_id"], row["source"], row["as_of"], cents(row["value"]),
                row["carried_back"]) == (home["name"], None, home["id"], "stated", value["as_of"],
                                         value["value_cents"], False), day
        assert home["name"] not in snap["missing"], day
        in_force.add(value["as_of"])
    assert in_force == {v["as_of"] for v in values}
    # the newest snapshot holds the third value, so its row is dated by that value, not by the snapshot
    assert [r["as_of"] for r in snapshots[truth["end"]]["rows"] if r["asset_class"] == "House"] == [values[-1]["as_of"]]
    first_report = min(p["date"] for points in load(demo_exports.exports, "balances_timeseries.json")[
        "series"].values() for p in points)
    assert list(snapshots)[0][:7] == first_report[:7] > start[:7]
    assert figures["partial_month_ends"] == [d for d in list(snapshots)[:-1] if snapshots[d]["missing"]]
    truth_on = {s["date"]: s for s in figures["snapshots"]}
    for day in snapshots:
        assert cents(snapshots[day]["by_class"]["House"]) == truth_on[day]["by_class"]["House"], day


def test_the_car_is_not_held_before_it_was_acquired(demo_exports, snapshots, txns, figures):
    """The car is a config entry that states the day it was bought, about eight months before the end, and one value
    six weeks later, so exactly one month end lies between. Before that day it is not held: no row, no part of the
    total and not missing; from it on, every snapshot holds it at its value, carried back from that value's date to
    the day it was bought (the month end between holds a carried-back row, as truth declares). On that day the daily
    line steps up by the car's value, and checking pays the dealer the price, which savings moved to checking the same
    day: one purchase filed by the demo's vendor pattern, unflagged and no recurring bill."""
    truth = demo_exports.truth
    car, end = manual_asset(truth, "car.entry"), date.fromisoformat(truth["end"])
    [value] = car["values"]
    acquired = car["acquired"]
    assert (car["origin"], car["asset_class"], car["owner"], car["liquid"]) == ("config", "Car", hh.SAM, False)
    assert shift_months(end, -8) <= date.fromisoformat(acquired) < shift_months(end, -7)
    assert value["as_of"] == (date.fromisoformat(acquired) + timedelta(weeks=6)).isoformat()
    before = [d for d in snapshots if d < acquired]
    after = [d for d in snapshots if d >= acquired]
    assert before and after
    [between] = [d for d in after if d < value["as_of"]]
    [declared] = [s for s in figures["snapshots"] if s["date"] == between]
    assert {"id": car["id"], "value_cents": value["value_cents"], "as_of": value["as_of"],
            "carried_back": True} in declared["manual_rows"]
    # truth declares the line on the day the car was bought and on the day it was valued
    assert {acquired, value["as_of"]} <= set(figures["daily_points"])
    for day in before:
        snap = snapshots[day]
        assert [r for r in snap["rows"] if r["manual_id"] == car["id"]] == [], day
        assert "Car" not in snap["by_class"] and car["name"] not in snap["missing"], day
    for day in after:
        [row] = [r for r in snapshots[day]["rows"] if r["manual_id"] == car["id"]]
        assert (row["account"], row["asset_class"], row["source"], row["as_of"], cents(row["value"]),
                row["carried_back"]) == (car["name"], "Car", "stated", value["as_of"], value["value_cents"],
                                         day < value["as_of"]), day
    # no account reports a balance on the day it was bought, so the line moves by the car alone that day
    reported = {p["date"] for points in load(demo_exports.exports, "balances_timeseries.json")["series"].values()
                for p in points}
    line = [(p["date"], cents(p["networth"])) for p in load(demo_exports.exports, "networth_daily.json")["series"]]
    days = [d for d, _ in line]
    assert acquired not in reported and acquired in days and value["as_of"] in days
    i = days.index(acquired)
    assert line[i][1] - line[i - 1][1] == value["value_cents"]
    # the purchase, and savings' transfer that pays for it
    purchase = txns[truth["planted"]["car.purchase"]]
    price = -cents(purchase["amount"])
    assert (purchase["account"], purchase["date"], purchase["type"], purchase["category"], purchase["subcategory"],
            purchase["category_source"], purchase["vendor"], purchase["needs_review"], purchase["is_internal"],
            purchase["transfer_group_id"]) == (hh.CHECKING, acquired, "purchase", "Transport", "Car Payment",
                                               "vendor_rule", balance_sheet.DEALER.vendor, False, False, None)
    assert price > value["value_cents"] > 0 and purchase["id"] not in figures["review"]
    [funding] = [p for p in figures["pairs"] if txns[p["into"]]["date"] == acquired
                 and txns[p["into"]]["account"] == hh.CHECKING]
    assert (txns[funding["out"]]["account"], funding["type"], cents(txns[funding["into"]]["amount"])) == (
        hh.SAVINGS, "transfer", price)
    assert figures["months"][acquired[:7]]["by_category"]["Transport"] > price
    assert balance_sheet.DEALER.vendor not in {i["vendor"] for i in load(demo_exports.exports, "recurring.json")["items"]}


def test_the_car_comes_from_the_config_and_the_home_from_the_app(demo_home, demo_exports, figures):
    """accounts.json `manual_assets` lists the household's two hand-stated values with where each comes from: the car,
    which accounts.yaml states, and the home, which only the app's file holds; manifest.setup counts both."""
    planted = demo_exports.truth["planted"]
    accounts = yaml.safe_load((demo_home.path / "config" / "accounts.yaml").read_text("utf-8"))
    assert [e["account"] for e in accounts["manual_entries"]] == [manual_asset(demo_exports.truth, "car.entry")["name"]]
    doc = json.loads((demo_home.path / "state" / "family" / "manual_assets.json").read_text("utf-8"))
    assert [a["id"] for a in doc["assets"]] == [planted["home.entry"]]
    exported = load(demo_exports.exports, "accounts.json")["manual_assets"]
    assert {a["id"]: a["origin"] for a in exported} == {planted["home.entry"]: "app", planted["car.entry"]: "config"}
    assert load(demo_exports.exports, "manifest.json")["setup"]["manual_assets"] == figures["setup"]["manual_assets"] == 2
    assert demo_checks.manual_assets(demo_exports.exports, demo_exports.truth) == []


def test_the_manual_assets_file_is_what_the_store_accepts(demo_home):
    """The file is in the shape the family store keeps (its own validator takes it back unchanged), its stamps are
    the store's UTC form with milliseconds, before the household's ingest, the document's the newest asset's."""
    path = demo_home.path / "state" / "family" / "manual_assets.json"
    doc = json.loads(path.read_text("utf-8"))
    assert scenario_store.validate_manual_assets(path.read_bytes()) == doc
    ingest = datetime.combine(date.fromisoformat(demo_home.truth["end"]), time(plan_mod.INGEST_HOUR),
                              tzinfo=ZoneInfo(hh.TIMEZONE)).astimezone(UTC)
    stamps = [a["updated_at"] for a in doc["assets"]]
    for stamp in (*stamps, doc["updated_at"]):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", stamp), stamp
        assert datetime.fromisoformat(stamp.replace("Z", "+00:00")) < ingest, stamp
    assert doc["updated_at"] == max(stamps)
    assert all("acquired" not in a and "deleted_at" not in a for a in doc["assets"])


def test_the_mortgage_block_and_its_schedule(demo_exports, snapshots, figures):
    block = load(demo_exports.exports, "assumptions.json")["mortgage"]
    want, planted = figures["mortgage"], demo_exports.truth["planted"]
    assert sorted(block) == ["account_id", "balance", "balance_as_of", "escrow_balance", "monthly_escrow",
                             "monthly_payment", "origination", "rate_pct", "schedule", "term_months"]
    assert demo_checks.mortgage(demo_exports.exports, demo_exports.truth) == []
    assert want["account"] == planted["mortgage.account"] == hh.MORTGAGE
    assert want["balance_as_of"] == demo_exports.truth["end"]
    # the schedule has every snapshot date from the loan's first report on, and exactly those snapshots subtract it;
    # the ones before it name the loan as missing
    name = hh.ACCOUNTS[hh.MORTGAGE].name
    reported = [d for d, s in snapshots.items() if name not in s["missing"]]
    assert [p["date"] for p in want["schedule"]] == reported and reported
    assert [d for d, s in snapshots.items() if s["mortgage_included"]] == reported
    # each month's payment lowers the balance between every two consecutive month ends; only a current snapshot that
    # is not itself a month end, which may come before its month's payment, may equal the month end before it
    balances = [p["principal_balance_cents"] for p in want["schedule"]]
    steps = list(zip(balances, balances[1:], strict=False))
    current = date.fromisoformat(want["schedule"][-1]["date"])
    month_ends = steps if (current + timedelta(days=1)).day == 1 else steps[:-1]
    assert month_ends and all(a > b > 0 for a, b in month_ends)
    assert 0 < balances[-1] <= balances[-2]
    [row] = [r for r in snapshots[demo_exports.truth["end"]]["rows"] if r["asset_class"] == "Mortgage"]
    assert (row["account_id"], cents(row["value"])) == (want["account"], -want["balance_cents"])
    # the payment is the level payment of the loan's terms, principal and interest only
    r = Decimal(str(want["rate_pct"])) / 1200
    level = Decimal(want["origination"]["amount_cents"]) * r / (1 - (1 + r) ** -want["term_months"])
    assert want["monthly_payment_cents"] == int(level.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def consecutive(months: list[str]) -> bool:
    """Are the `YYYY-MM` months, in order, each the month after the one before (none repeated, none skipped)?"""
    index = [int(m[:4]) * 12 + int(m[5:7]) for m in months]
    return all(b - a == 1 for a, b in zip(index, index[1:], strict=False))


def test_each_mortgage_payment_is_housing_spending(demo_exports, txns, figures):
    want = figures["mortgage"]
    paid = [t for t in txns.values() if t["vendor"] == balance_sheet.LENDER.vendor]
    assert demo_exports.truth["planted"]["mortgage.payment"] in {t["id"] for t in paid}
    assert {t["id"] for t in paid} == {u for u, r in figures["rows"].items() if r["vendor"] == balance_sheet.LENDER.vendor}
    for t in paid:
        assert (t["account"], t["type"], t["category"], t["subcategory"], t["category_source"], t["is_internal"],
                t["needs_review"]) == (hh.CHECKING, "purchase", "Housing", "Mortgage", "vendor_rule", False, False)
        assert -cents(t["amount"]) == want["monthly_payment_cents"] + want["monthly_escrow_cents"]
    # one a month, from before the lender's feed reports the loan
    assert consecutive(sorted(t["date"][:7] for t in paid))
    assert min(t["date"] for t in paid) < want["schedule"][0]["date"]
    # a recurring bill, as the declaration says
    [item] = [i for i in load(demo_exports.exports, "recurring.json")["items"]
              if i["vendor"] == balance_sheet.LENDER.vendor]
    assert (item["account_id"], item["cadence"], item["amount_mode"]) == (hh.CHECKING, *balance_sheet.MORTGAGE_CADENCE)


def test_holdings_equal_truth_and_reconcile_with_the_balance(demo_exports, figures):
    holdings = load(demo_exports.exports, "holdings.json")["holdings"]
    planted, end = demo_exports.truth["planted"], demo_exports.truth["end"]
    assert demo_checks.holdings(demo_exports.exports, demo_exports.truth) == [] and figures["holdings"]
    # symbols no US-listed fund can have: a US ticker is letters only
    assert [h["symbol"] for h in holdings] == planted["brokerage.symbols"] == ["DEMO1", "DEMO2", "DEMO3"]
    assert {h["account_id"] for h in holdings} == {planted["brokerage.account"]} == {hh.BROKERAGE}
    prices = {f.symbol: f.price_cents for f in balance_sheet.PORTFOLIO.funds}
    for h in holdings:
        value = cents(h["value"])
        assert (h["asset_class"], h["as_of"], h["liquid"]) == ("Stocks/ETF", end, True), h["symbol"]
        assert abs(Decimal(str(h["quantity"])) * prices[h["symbol"]] - value) <= prices[h["symbol"]] / 2000
        assert cents(h["cost_basis"]) == (value * balance_sheet.PORTFOLIO.cost_percent + 50) // 100
    gross = sum(cents(h["value"]) for h in holdings)
    # 60 / 25 / 15 percent of the gross position value, the last fund taking what rounding leaves
    assert [cents(h["value"]) for h in holdings[:2]] == [(gross * 60 + 50) // 100, (gross * 25 + 50) // 100]
    balance = {a["id"]: a for a in load(demo_exports.exports, "accounts.json")["accounts"]}[hh.BROKERAGE]
    assert gross - planted["margin.loan_cents"] == cents(balance["latest_balance"])
    checks = {c["name"]: c for c in load(demo_exports.exports, "quality_report.json")["checks"]}
    check = checks[f"simplefin.holdings_coverage.{hh.BROKERAGE}"]
    assert check["status"] == "info" and "margin" in check["detail"]


def test_the_margin_loan_is_one_negative_row_in_the_current_snapshot_only(demo_exports, snapshots):
    planted, end = demo_exports.truth["planted"], demo_exports.truth["end"]
    rows = [r for r in snapshots[end]["rows"] if r["account_id"] == hh.BROKERAGE]
    [margin] = [r for r in rows if r["asset_class"] == "Margin"]
    assert (cents(margin["value"]), margin["liquid"]) == (-planted["margin.loan_cents"], False)
    assert planted["margin.loan_cents"] == balance_sheet.PORTFOLIO.loan_cents > 0
    [positions] = [r for r in rows if r["source"] == "positions"]
    held = sum(cents(h["value"]) for h in load(demo_exports.exports, "holdings.json")["holdings"])
    assert (positions["asset_class"], cents(positions["value"])) == ("Stocks/ETF", held) and len(rows) == 2
    series = {p["date"]: cents(p["balance"]) for p in load(demo_exports.exports, "balances_timeseries.json")[
        "series"][hh.BROKERAGE]}
    assert sum(cents(r["value"]) for r in rows) == series[end]
    for day, snap in snapshots.items():
        if day == end or hh.ACCOUNTS[hh.BROKERAGE].name in snap["missing"]:
            continue
        [row] = [r for r in snap["rows"] if r["account_id"] == hh.BROKERAGE]
        assert (row["asset_class"], row["source"], cents(row["value"])) == (
            "Stocks/ETF", "simplefin_balance", series[day]), day
        assert "Margin" not in snap["by_class"], day
    household = load(demo_exports.exports, "assumptions.json")["household"]
    assert (household["margin_account"], household["margin_apr"]) == (
        hh.ACCOUNTS[hh.BROKERAGE].name, balance_sheet.MARGIN_APR)


def test_the_retirement_account_reports_a_balance_only(demo_exports, snapshots, figures):
    planted = demo_exports.truth["planted"]
    assert planted["retirement.account"] == hh.RETIREMENT
    name = hh.ACCOUNTS[hh.RETIREMENT].name
    for day, snap in snapshots.items():
        if name in snap["missing"]:                     # a month end before the feed connects the account
            assert day in figures["partial_month_ends"], day
            continue
        [row] = [r for r in snap["rows"] if r["account_id"] == hh.RETIREMENT]
        assert (row["account"], row["asset_class"], row["liquid"], row["source"]) == (
            name, "Retirement", False, "simplefin_balance"), day
    assert hh.RETIREMENT not in {h["account_id"] for h in load(demo_exports.exports, "holdings.json")["holdings"]}
    doc = load(demo_exports.exports, "assumptions.json")
    assert doc["household"]["retirement_accounts"] == {name: hh.ACCOUNTS[hh.RETIREMENT].owner}
    # percent of net worth by asset class: the home's equity is the rest, so they add up to less than 100
    assert doc["targets"] == balance_sheet.TARGETS and sum(doc["targets"].values()) < 100
    assert doc["budgets"] == balance_sheet.BUDGETS and len(doc["budgets"]) == 4
    assert set(doc["budgets"]) <= set(doc["taxonomy"])


def test_contributions_are_invested_not_spent(demo_exports, txns, figures):
    rows = [t for t in txns.values() if t["type"] == "investment"]
    assert {t["id"] for t in rows} == {u for u, r in figures["rows"].items() if r["type"] == "investment"}
    assert consecutive(sorted(t["date"][:7] for t in rows))                                 # one a month
    for t in rows:
        assert (t["account"], t["category"], t["subcategory"], t["is_internal"], t["transfer_group_id"],
                t["needs_review"], cents(t["amount"])) == (
            hh.CHECKING, "Internal", "investment", True, None, False, -balance_sheet.CONTRIBUTION_CENTS)
    invested: dict[str, int] = {}
    for t in rows:
        invested[t["date"][:7]] = invested.get(t["date"][:7], 0) - cents(t["amount"])
    assert {m: invested.get(m, 0) for m in figures["months"]} == {
        m: w["invested_cents"] for m, w in figures["months"].items()}
    months = {m["month"]: m for m in load(demo_exports.exports, "cashflow_monthly.json")["months"]}
    assert {m: cents(v["invested"]) for m, v in months.items()} == {
        m: w["invested_cents"] for m, w in figures["months"].items()}
    # the brokerage imports no rows, so a contribution with no other leg is no gap in the data
    assert len(load(demo_exports.exports, "open_questions.json")["data_gaps"]) == len(figures["data_gaps"]) == 1


def test_the_allowance_is_a_transfer_pair_every_month(demo_exports, txns, figures):
    planted = demo_exports.truth["planted"]
    child = planted["child.account"]
    assert child == hh.CHILD_SAVINGS and hh.ACCOUNTS[child].owner == hh.ROBIN
    pairs = [p for p in figures["pairs"] if txns[p["into"]]["account"] == child]
    assert sorted(planted["child.pair"]) in [sorted([p["out"], p["into"]]) for p in pairs]
    for p in pairs:
        out, into = txns[p["out"]], txns[p["into"]]
        assert (out["account"], out["type"], into["type"], cents(out["amount"]), cents(into["amount"])) == (
            hh.CHECKING, "transfer", "transfer", -balance_sheet.ALLOWANCE_CENTS, balance_sheet.ALLOWANCE_CENTS)
        assert out["transfer_group_id"] == into["transfer_group_id"] is not None and out["date"] == into["date"]
        assert "TRANSFER TO SAV" in out["description"] and "TRANSFER FROM CHK" in into["description"]
    # one a month, from the month of Robin's account's first row
    months = [txns[p["out"]]["date"][:7] for p in pairs]
    assert months[0] == figures["accounts"][child]["first"][:7] and consecutive(months)
    # and the account's own interest, at each month end
    interest = [t for t in txns.values() if t["account"] == child and t["type"] == "interest"]
    assert interest and all((t["category"], t["subcategory"], t["amount"] > 0) == ("Income", "Interest", True)
                            for t in interest)
    assert len(interest) + len(pairs) == figures["accounts"][child]["rows"]          # its only rows


def test_the_stale_account_is_carried_and_warned(demo_home, demo_exports, snapshots):
    planted, end = demo_exports.truth["planted"], demo_exports.truth["end"]
    account = planted["stale.account"]
    assert account == hh.RETIREMENT
    files = sorted((demo_home.path / "store" / "simplefin" / "snapshots").glob("accounts-*.json"))
    listed = [hh.ACCOUNTS[account].feed_id in {a["id"] for a in json.loads(p.read_text("utf-8"))["accounts"]}
              for p in files]
    joined = listed.index(True)                                              # the feed connects it later
    assert joined > 0 and listed[-2:] == [False, False] and all(listed[joined:-2])     # left out of the last two
    reported = {p["date"]: cents(p["balance"]) for p in load(demo_exports.exports, "balances_timeseries.json")[
        "series"][account]}
    last_seen = max(reported)
    assert last_seen < end
    for day, snap in snapshots.items():
        if day < min(reported):                                              # before its first report: missing
            assert hh.ACCOUNTS[account].name in snap["missing"], day
            continue
        [row] = [r for r in snap["rows"] if r["account_id"] == account]
        assert hh.ACCOUNTS[account].name not in snap["missing"], day
        seen = max(d for d in reported if d <= day)
        assert (row["as_of"], cents(row["value"])) == (seen, reported[seen]), day
    assert [r["as_of"] for r in snapshots[end]["rows"] if r["account_id"] == account] == [last_seen]
    checks = {c["name"]: c for c in load(demo_exports.exports, "quality_report.json")["checks"]}
    stale = checks[f"simplefin.stale.{account}"]
    assert stale["status"] == "warn" and f"last seen {last_seen}" in stale["detail"]
    assert stale["detail"].endswith("If the bank re-linked it under a new id, point the account at the new id and keep "
                                    "the old one (simplefin_id_history in accounts.yaml); otherwise refresh.")
    # a balance-only account: no row of its own, so the months' cash-flow coverage does not depend on it
    txns = load(demo_exports.exports, "transactions.json")["transactions"]
    assert not [t for t in txns if t["account"] == account]
    assert account not in load(demo_exports.exports, "cashflow_monthly.json")["cashflow_accounts"]


def test_the_certificate_earns_interest_on_every_month_end_to_the_end(demo_exports):
    """The certificate's interest: a row on every month end from the month of its first report through the end, so
    it covers the newest month and every month of the last year is fully covered."""
    end = demo_exports.truth["end"]
    reported = load(demo_exports.exports, "balances_timeseries.json")["series"][hh.CERTIFICATE]
    rows = sorted((t for t in load(demo_exports.exports, "transactions.json")["transactions"]
                   if t["account"] == hh.CERTIFICATE), key=lambda t: t["date"])
    assert rows and all((t["type"], t["category"], t["subcategory"], t["category_source"]) == (
        "interest", "Income", "Interest", "type_default") and t["amount"] > 0 for t in rows)
    days = [date.fromisoformat(t["date"]) for t in rows]
    assert all((d + timedelta(days=1)).day == 1 for d in days) and consecutive([t["date"][:7] for t in rows])
    assert rows[0]["date"][:7] == min(p["date"] for p in reported)[:7] and rows[-1]["date"] == end
    months = load(demo_exports.exports, "cashflow_monthly.json")["months"]
    last_year = [m for m in months if shift_months(date.fromisoformat(end), -12).isoformat()[:7] < m["month"] <= end[:7]]
    assert len(last_year) == 12 and all(m["full_coverage"] for m in last_year)


def exported_levels(snapshots: dict[str, dict], comparable: list[str]) -> tuple[int, int, list[int]]:
    """Net worth at the first and the last comparable month end, as exported, and the $100,000 levels between."""
    first, last = (cents(snapshots[d]["total"]) for d in (comparable[0], comparable[-1]))
    lo, hi = sorted((first, last))
    return first, last, [k for k in range(10_000_000, hi + 1, 10_000_000) if lo < k <= hi]


MARGIN_CENTS = 1_000_000                # the room net worth leaves on either side of the levels it crosses


def test_the_levels_crossed_equal_truth(snapshots, figures):
    assert exported_levels(snapshots, figures["comparable_month_ends"])[2] == figures["levels_crossed"]


def test_at_the_pinned_end_net_worth_crosses_three_levels(demo_exports, snapshots, figures):
    """A fact of the household as generated to the pinned end (the market draws decide it): between $700,000 and
    $790,000 at the first comparable month end, $1,010,000 or more at the last, so three $100,000 levels are crossed
    with at least $10,000 to spare on either side."""
    assert demo_exports.truth["end"] == DEMO_END.isoformat()
    first, last, levels = exported_levels(snapshots, figures["comparable_month_ends"])
    assert 70_000_000 <= first <= 80_000_000 - MARGIN_CENTS, first
    assert last >= 100_000_000 + MARGIN_CENTS, last
    assert levels == figures["levels_crossed"] == [80_000_000, 90_000_000, 100_000_000]


BALANCE_SHEET = (hh.BROKERAGE, hh.RETIREMENT, hh.MORTGAGE, hh.CHILD_SAVINGS, hh.CERTIFICATE)


def test_three_month_ends_before_the_other_accounts_join_are_partial(demo_exports, snapshots, figures):
    """The feed connects the everyday accounts three months before the balance-sheet ones: the month ends between
    hold the everyday accounts and the home, and name the five others as missing. The mortgage is configured and not
    yet reported, so nothing is subtracted for it and the snapshot says so."""
    partial = figures["partial_month_ends"]
    assert len(partial) == 3 and partial == list(snapshots)[:3]
    names = sorted(hh.ACCOUNTS[a].name for a in BALANCE_SHEET)
    for day in partial:
        snap = snapshots[day]
        assert sorted(snap["missing"]) == names, day
        assert snap["mortgage_included"] is False, day
        assert {r["account_id"] for r in snap["rows"]} == {None, hh.CHECKING, hh.SAVINGS, hh.CARD_ALEX, hh.CARD_SAM}
        assert "Mortgage" not in snap["by_class"] and "House" in snap["by_class"], day
    first = date.fromisoformat(figures["comparable_month_ends"][0])
    assert all(date.fromisoformat(d) < first for d in partial)


def test_nineteen_month_ends_miss_nothing(snapshots, figures):
    comparable = figures["comparable_month_ends"]
    assert len(comparable) == 19
    for day in comparable:
        assert snapshots[day]["missing"] == [] and snapshots[day]["mortgage_included"] is True, day
    # every month end from the first comparable one on, before the current date
    assert comparable == list(snapshots)[3:-1]


def test_comparable_and_partial_month_ends_equal_truth(snapshots, figures):
    assert figures["comparable_month_ends"] == [d for d in list(snapshots)[:-1] if not snapshots[d]["missing"]]
    assert figures["partial_month_ends"] == [d for d in list(snapshots)[:-1] if snapshots[d]["missing"]]


# ------------------------------------------------------------------ the quality report

def test_the_checks_that_are_not_pass_are_exactly_truths(demo_exports, figures):
    checks = load(demo_exports.exports, "quality_report.json")["checks"]
    assert demo_checks.checks(demo_exports.exports, demo_exports.truth) == []
    assert "fail" not in figures["checks"].values()
    # the two planted warnings and nothing else warns
    planted = demo_exports.truth["planted"]
    assert sorted(n for n, s in figures["checks"].items() if s == "warn") == [
        f"simplefin.stale.{planted['stale.account']}", f"simplefin.unmapped.{planted['unmapped.feed_id']}"]
    [unmapped] = [c for c in checks if c["name"] == f"simplefin.unmapped.{planted['unmapped.feed_id']}"]
    assert "balance 0.00" in unmapped["detail"]
    # the one action in words, the config key a hint at the end
    assert unmapped["detail"].endswith("matches no configured account: add it with this feed id (simplefin_id in "
                                       "accounts.yaml), or leave it if the household does not track it.")


def test_ledger_drift_is_the_pending_total(demo_exports, figures):
    assert demo_checks.ledger_drift(demo_exports.exports, demo_exports.truth) == []
    # the feed's balance holds the card's pending purchases and no stored row does: the rows moved by less than the
    # balance (drift = rows - balance change), by exactly what is pending
    pending = figures["pending"]
    assert list(pending) == [hh.CARD_SAM]
    assert figures["ledger_drift"][hh.CARD_SAM] == -pending[hh.CARD_SAM]["cents"] > 0
    assert {a: d for a, d in figures["ledger_drift"].items() if a != hh.CARD_SAM} == {
        a: 0 for a in figures["ledger_drift"] if a != hh.CARD_SAM}

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""etl/networth.py: the per-account series map and the snapshot read from it. Synthetic observations and invented
accounts only."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from conftest import isolate, write_household

from etl import exports
from etl import manual_assets as ma
from etl import networth as nw
from etl import pipeline as pipeline_mod
from etl.config import Config
from etl.models import LIABILITY_CLASSES, BalanceObs, FileStats, Holding
from etl.pipeline import Pipeline

# no document of assets entered in the app: the manual assets are the config's entries alone
NO_DOCUMENT = Path("/nonexistent-manual-assets/manual_assets.json")
# an ingest day after every invented point below, unless a test says otherwise
TODAY = "2030-12-31"


def _acct(kind: str, name: str, *, institution: str = "Sample Bank", owner: str = "Alex", **extra) -> dict:
    return {"name": name, "institution": institution, "owner": owner, "kind": kind, **extra}


def _obs(account_id: str, day: str, cents: int, source: str = "simplefin") -> BalanceObs:
    return BalanceObs(account_id=account_id, as_of=day, balance_cents=cents, source=source)


def _holding(account_id: str, name: str, cents: int, *, asset_class: str = "Stocks/ETF",
             as_of: str = "2030-01-10", institution: str = "Sample Broker", owner: str = "Sam") -> Holding:
    return Holding(account=name, institution=institution, owner=owner, symbol="SMPL", name="Sample fund",
                   asset_class=asset_class, quantity=1.0, value_cents=cents, cost_basis_cents=None, as_of=as_of,
                   account_id=account_id)


def _series(accounts: dict, obs: list[BalanceObs], manual: list[dict] | None = None, *, cards: bool = False):
    """The series map of `accounts` and `obs`, with `manual` as accounts.yaml manual entries, in their order."""
    return nw.build_series(obs, accounts, [ma.from_config(e) for e in manual or []], include_credit_cards=cards)


def _values(snap: nw.Snapshot) -> dict[str, int]:
    return {r.row_id: r.value_cents for r in snap.rows}


# ------------------------------------------------------------------ carry, missing, coverage

def test_a_row_is_the_last_observation_on_or_before_the_day():
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking")}
    series = _series(accounts, [_obs("demo_checking_0000", "2030-01-20", 175_00),
                                _obs("demo_checking_0000", "2030-01-05", 100_00),
                                _obs("demo_checking_0000", "2030-01-10", 150_00)])
    assert series["demo_checking_0000"].points == (("2030-01-05", 100_00), ("2030-01-10", 150_00),
                                                   ("2030-01-20", 175_00))
    (row,) = nw.snapshot_at(series, "2030-01-15").rows
    assert (row.value_cents, row.as_of) == (150_00, "2030-01-10")
    (row,) = nw.snapshot_at(series, "2030-01-20").rows
    assert (row.value_cents, row.as_of) == (175_00, "2030-01-20")


def test_an_account_that_stopped_reporting_is_carried_at_its_last_balance_however_old():
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking"),
                "demo_brokerage_0000": _acct("brokerage", "Sample Brokerage")}
    series = _series(accounts, [_obs("demo_checking_0000", "2030-06-30", 100_00),
                                _obs("demo_brokerage_0000", "2030-01-02", 900_00)])
    snap = nw.snapshot_at(series, "2030-06-30")
    rows = {r.row_id: r for r in snap.rows}
    assert (rows["demo_brokerage_0000"].value_cents, rows["demo_brokerage_0000"].as_of) == (900_00, "2030-01-02")
    assert (snap.missing, snap.coverage) == ((), 1.0)


def test_an_account_first_seen_after_the_day_is_missing_and_lowers_coverage():
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking"),
                "demo_brokerage_0000": _acct("brokerage", "Sample Brokerage"),
                "demo_savings_0000": _acct("savings", "Sample Savings")}     # never observed: a series with no point
    series = _series(accounts, [_obs("demo_checking_0000", "2030-01-01", 100_00),
                                _obs("demo_brokerage_0000", "2030-01-15", 900_00)])
    assert list(series) == ["demo_checking_0000", "demo_brokerage_0000", "demo_savings_0000"]
    assert series["demo_savings_0000"].points == ()
    snap = nw.snapshot_at(series, "2030-01-10")
    assert _values(snap) == {"demo_checking_0000": 100_00}             # never guessed
    assert (snap.date, snap.missing) == ("2030-01-10", ("demo_brokerage_0000", "demo_savings_0000"))
    assert snap.coverage == pytest.approx(1 / 3)


def test_an_empty_series_map_has_no_snapshot():
    assert nw.current_date({}, TODAY) is None
    with pytest.raises(ValueError, match="no series"):
        nw.snapshot_at({}, "2030-01-01")


def test_a_long_series_reads_the_same_point_as_a_scan_of_every_point():
    """A few thousand points, one every other day: the point read on a day is the last one on or before it, exactly as
    a scan of every point finds it — on the first date, the last, a day between two points, a day before the first
    (none) and a day after the last."""
    start = dt.date(2030, 1, 1)

    def day(n: int) -> str:
        return (start + dt.timedelta(days=n)).isoformat()

    obs =[_obs("demo_checking_0000", day(2 * i), 100_00 + i) for i in range(3_000)]
    series = _series({"demo_checking_0000": _acct("checking", "Sample Checking")}, obs)
    points = series["demo_checking_0000"].points
    assert len(points) == 3_000

    def scan(d: str):
        on_or_before = [p for p in points if p[0] <= d]
        return on_or_before[-1] if on_or_before else None

    probes = {"first": day(0), "last": day(2 * 2_999), "between": day(2 * 1_500 + 1), "before": day(-1),
              "after": day(2 * 3_000 + 30)}
    for name, d in probes.items():
        assert nw._last_on_or_before(points, d) == scan(d), name
        rows = nw.snapshot_at(series, d).rows
        assert [(r.as_of, r.value_cents) for r in rows] == ([scan(d)] if scan(d) else []), name
    assert [nw._last_on_or_before(points, d) for d in probes.values()] == [
        points[0], points[-1], points[1_500], None, points[-1]]


def test_a_file_balance_beats_a_feed_balance_on_the_same_date():
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking")}
    series = _series(accounts, [_obs("demo_checking_0000", "2030-01-10", 120_00),
                                _obs("demo_checking_0000", "2030-01-10", 100_00, source="csv"),
                                _obs("demo_checking_0000", "2030-01-12", 130_00)])
    assert series["demo_checking_0000"].points == (("2030-01-10", 100_00), ("2030-01-12", 130_00))
    for day, cents in (("2030-01-10", 100_00), ("2030-01-12", 130_00)):
        (row,) = nw.snapshot_at(series, day).rows
        assert (row.value_cents, row.source) == (cents, "bank_balance")   # a cash row, from a file or the feed


@pytest.mark.parametrize("source", ["simplefin", "csv"])
def test_of_two_observations_of_one_account_date_and_kind_the_last_in_the_list_wins(source):
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking")}
    for first, last in ((100_00, 125_00), (125_00, 100_00)):
        series = _series(accounts, [_obs("demo_checking_0000", "2030-01-10", first, source),
                                    _obs("demo_checking_0000", "2030-01-10", last, source)])
        assert series["demo_checking_0000"].points == (("2030-01-10", last),)


# ------------------------------------------------------------------ the tie rules, through the pipeline

def _epoch(day: str) -> int:
    return int(dt.datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp())


def _feed_file(d: Path, stamp: str, reports: list[tuple[str, str, str]], holdings: dict | None = None) -> None:
    """One feed snapshot file `accounts-<stamp>.json` (stamps sort oldest first) reporting (feed id, balance, date),
    with `holdings` {feed id: market value} as one position of that account."""
    d.mkdir(parents=True, exist_ok=True)
    (d / f"accounts-{stamp}.json").write_text(json.dumps({"accounts": [
        {"id": sfid, "name": sfid, "org": {"name": "Sample Bank"}, "currency": "USD", "balance": balance,
         "balance-date": _epoch(day), "transactions": [],
         "holdings": [{"symbol": "SMPL", "shares": "1", "cost_basis": "1.00", "market_value": (holdings or {})[sfid]}]
         if sfid in (holdings or {}) else []} for sfid, balance, day in reports]}))


def _feed_pipeline(tmp_path: Path, accounts: dict, **household) -> Pipeline:
    d = tmp_path / "cfg"
    write_household(d, **household)
    (tmp_path / "src").mkdir(exist_ok=True)
    (d / "accounts.yaml").write_text(yaml.safe_dump({"source_dir": "src", "accounts": accounts}))
    cfg = Config(d, source_dir=str(tmp_path / "src"))
    cfg.simplefin_snapshots_dir = tmp_path / "snaps"
    return Pipeline(cfg)


def test_two_feed_files_reporting_one_balance_date_book_the_newer_files_balance(tmp_path):
    """The feed fetches several times a day and an account's balance moves within the day while its balance date
    does not: the newest report of the date is the one the balance series keeps, the snapshot books, the daily line
    ends on, and accounts.json, the database and the holdings-coverage check give as the latest balance — for a card
    while cards are out of net worth, which has no series, too."""
    accounts = {"demo_brokerage_0000": _acct("brokerage", "Sample Brokerage", simplefin_id="SF-DEMO-B0"),
                "demo_checking_0000": _acct("checking", "Sample Checking", simplefin_id="SF-DEMO-C0"),
                "demo_card_0000": _acct("credit-card", "Sample Card", simplefin_id="SF-DEMO-K0")}
    snaps = tmp_path / "snaps"
    _feed_file(snaps, "20300110T150000Z", [("SF-DEMO-B0", "1000.00", "2030-01-10"),
                                           ("SF-DEMO-C0", "50.00", "2030-01-09"),
                                           ("SF-DEMO-K0", "-50.00", "2030-01-10")])
    _feed_file(snaps, "20300110T230000Z", [("SF-DEMO-B0", "1025.50", "2030-01-10"),
                                           ("SF-DEMO-C0", "50.00", "2030-01-09"),
                                           ("SF-DEMO-K0", "-75.00", "2030-01-10")], holdings={"SF-DEMO-B0": "1025.50"})
    p = _feed_pipeline(tmp_path, accounts, net_worth={"include_credit_cards": False})
    isolate(p.cfg, tmp_path / "state")
    p.parse_simplefin()
    assert [b.balance_cents for b in p.balance_obs if b.account_id == "demo_brokerage_0000"] == [102_550]
    p.parse_assets()
    rows = {r.account_id: (r.value_cents, r.as_of, r.source) for r in p.asset_rows}
    assert rows["demo_brokerage_0000"] == (102_550, "2030-01-10", "simplefin_balance")
    assert p.current_snapshot_date == "2030-01-10"
    p.finalize_quality()
    (coverage,) = [c for c in p.checks if c.name == "simplefin.holdings_coverage.demo_brokerage_0000"]
    assert (coverage.status, "vs balance $1,025.50" in coverage.detail) == ("info", True), coverage.detail
    p.persist()
    p.export()
    exported = {a["id"]: (a["latest_balance"], a["latest_balance_as_of"])
                for a in json.loads((p.cfg.exports_dir / "accounts.json").read_text())["accounts"]}
    assert exported == {"demo_brokerage_0000": (1_025.5, "2030-01-10"), "demo_checking_0000": (50.0, "2030-01-09"),
                        "demo_card_0000": (-75.0, "2030-01-10")}
    assert "demo_card_0000" not in p.networth_series                     # cards out: no series to read it from
    conn = sqlite3.connect(p.cfg.data_dir / "finance.db")
    try:
        stored = {a: (cents, day) for a, cents, day in conn.execute(
            "SELECT account_id, latest_balance_cents, latest_balance_as_of FROM accounts")}
    finally:
        conn.close()
    assert stored == {"demo_brokerage_0000": (102_550, "2030-01-10"), "demo_checking_0000": (50_00, "2030-01-09"),
                      "demo_card_0000": (-75_00, "2030-01-10")}
    line = json.loads((p.cfg.exports_dir / "networth_daily.json").read_text())
    assert line["series"][-1] == {"date": "2030-01-10", "networth": 1_075.5, "coverage": 1.0, "source": "simplefin"}
    assert line["current_total"] == 1_075.5


def test_the_latest_balance_of_a_net_worth_account_is_its_series_last_point(tmp_path, monkeypatch):
    """Pipeline._latest_balance_all, which the database's accounts table, accounts.json and the holdings-coverage check
    read: a feed report newer than the file balance beats it, the file balance beats a feed report of its own date,
    and a newer feed file's report of a date replaces an older file's, exactly as the series map books them. A card
    while cards are out of net worth has no series and keeps the newest feed balance."""
    def parse(path, account_id):
        return [], FileStats(source_file=path.name, parser="sample_file"), {"latest_balance": ("2030-01-10", 400_00)}
    monkeypatch.setitem(pipeline_mod.TXN_PARSERS, "sample_file", parse)
    accounts = {
        "demo_checking_0000": _acct("checking", "Sample Checking", simplefin_id="SF-DEMO-C0", parser="sample_file",
                                    glob="demo_checking_0000-*.csv", simplefin_balances_only=True),
        "demo_checking_0001": _acct("checking", "Sample Checking Two", simplefin_id="SF-DEMO-C1",
                                    parser="sample_file", glob="demo_checking_0001-*.csv", simplefin_balances_only=True),
        "demo_brokerage_0000": _acct("brokerage", "Sample Brokerage", simplefin_id="SF-DEMO-B0"),
        "demo_card_0000": _acct("credit-card", "Sample Card", simplefin_id="SF-DEMO-K0"),
    }
    snaps = tmp_path / "snaps"
    _feed_file(snaps, "20300110T150000Z", [("SF-DEMO-C0", "600.00", "2030-01-11"),
                                           ("SF-DEMO-C1", "500.00", "2030-01-10"),
                                           ("SF-DEMO-B0", "1000.00", "2030-01-10"),
                                           ("SF-DEMO-K0", "-50.00", "2030-01-10")])
    _feed_file(snaps, "20300110T230000Z", [("SF-DEMO-B0", "1025.50", "2030-01-10")])
    p = _feed_pipeline(tmp_path, accounts, net_worth={"include_credit_cards": False})
    for account_id in ("demo_checking_0000", "demo_checking_0001"):
        (tmp_path / "src" / f"{account_id}-2030.csv").write_text("")
    p.parse_transactions()
    p.parse_simplefin()
    p.parse_assets()
    latest = p._latest_balance_all()
    assert latest == {
        "demo_checking_0000": ("2030-01-11", 600_00),     # the feed's newer report beats the file balance
        "demo_checking_0001": ("2030-01-10", 400_00),     # the file balance beats the feed's report of its date
        "demo_brokerage_0000": ("2030-01-10", 102_550),   # the newer file's report of the date
        "demo_card_0000": ("2030-01-10", -50_00),         # no series: the newest feed balance
    }
    assert {r.account_id: (r.as_of, r.value_cents) for r in p.asset_rows} == {
        a: v for a, v in latest.items() if a != "demo_card_0000"}


def test_a_file_balance_beats_a_feed_report_of_the_same_date_and_a_newer_report_beats_it(tmp_path, monkeypatch):
    def parse(path, account_id):
        return [], FileStats(source_file=path.name, parser="sample_file"), {"latest_balance": ("2030-01-10", 400_00)}
    monkeypatch.setitem(pipeline_mod.TXN_PARSERS, "sample_file", parse)
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking", simplefin_id="SF-DEMO-C0",
                                            parser="sample_file", glob="demo_checking_0000-*.csv",
                                            simplefin_balances_only=True)}
    snaps = tmp_path / "snaps"
    _feed_file(snaps, "20300110T150000Z", [("SF-DEMO-C0", "500.00", "2030-01-10")])
    p = _feed_pipeline(tmp_path, accounts)
    (tmp_path / "src" / "demo_checking_0000-2030.csv").write_text("")
    for later in (False, True):
        if later:
            _feed_file(snaps, "20300111T150000Z", [("SF-DEMO-C0", "600.00", "2030-01-11")])
            p = Pipeline(p.cfg)
        p.parse_transactions()
        p.parse_simplefin()
        p.parse_assets()
        (row,) = p.asset_rows
        assert (row.value_cents, row.as_of, row.source) == (
            (600_00, "2030-01-11", "bank_balance") if later else (400_00, "2030-01-10", "bank_balance"))


# ------------------------------------------------------------------ class, liquidity, sign, source

def test_class_liquidity_and_source_follow_the_account():
    accounts = {
        "demo_checking_0000": _acct("checking", "Sample Checking"),
        "demo_savings_0000": _acct("savings", "Sample Savings"),
        "demo_brokerage_0000": _acct("brokerage", "Sample Taxable"),                          # the kind's default
        "demo_brokerage_0001": _acct("brokerage", "Sample Plan", asset_class="Retirement"),
        "demo_brokerage_0002": _acct("brokerage", "Sample College", asset_class="529"),
        "demo_loan_0000": _acct("loan", "Sample Mortgage", mortgage=True),
        "demo_loan_0001": _acct("loan", "Sample Car Loan"),
    }
    series = _series(accounts, [_obs(a, "2030-01-10", -500_00 if a.startswith("demo_loan") else 500_00)
                                for a in accounts])
    got = {r.row_id: (r.asset_class, r.liquid, r.source) for r in nw.snapshot_at(series, "2030-01-10").rows}
    assert got == {
        "demo_checking_0000": ("Cash", True, "bank_balance"),
        "demo_savings_0000": ("Cash", True, "bank_balance"),
        "demo_brokerage_0000": ("Stocks/ETF", True, "simplefin_balance"),
        "demo_brokerage_0001": ("Retirement", False, "simplefin_balance"),
        "demo_brokerage_0002": ("529", False, "simplefin_balance"),
        "demo_loan_0000": ("Mortgage", False, "simplefin_balance"),
        "demo_loan_0001": ("Other Debt", False, "simplefin_balance"),
    }


def test_a_non_mortgage_loan_is_an_other_debt_row_not_liquid_and_negative():
    accounts = {"demo_loan_0000": _acct("loan", "Sample Car Loan")}
    (row,) = nw.snapshot_at(_series(accounts, [_obs("demo_loan_0000", "2030-01-10", -12_000_00)]), "2030-01-10").rows
    assert (row.account, row.account_id, row.asset_class, row.liquid, row.value_cents) == (
        "Sample Car Loan", "demo_loan_0000", "Other Debt", False, -12_000_00)


def test_credit_cards_are_in_or_out_by_the_flag():
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking"),
                "demo_card_0000": _acct("credit-card", "Sample Card")}
    obs = [_obs("demo_checking_0000", "2030-01-10", 1_000_00), _obs("demo_card_0000", "2030-01-10", -250_00)]
    assert set(_series(accounts, obs)) == {"demo_checking_0000"}
    series = _series(accounts, obs, cards=True)
    card = next(r for r in nw.snapshot_at(series, "2030-01-10").rows if r.row_id == "demo_card_0000")
    assert (card.asset_class, card.liquid, card.value_cents, card.source) == (
        "Other Debt", False, -250_00, "simplefin_balance")


# ------------------------------------------------------------------ manual entries

_HOUSE = {"account": "Sample House", "institution": "Sample", "owner": "Joint", "asset_class": "House",
          "value": 400_000.25, "as_of": "2030-01-01"}


def test_a_manual_entry_is_one_stated_row_with_no_account_id():
    series = _series({}, [], [_HOUSE, {**_HOUSE, "account": "Sample Cash  (old)", "asset_class": "Cash",
                                        "value": 300, "liquid": True}])
    assert list(series) == ["manual:sample-house", "manual:sample-cash-old"]
    rows = {r.row_id: r for r in nw.snapshot_at(series, "2030-01-01").rows}
    house = rows["manual:sample-house"]
    assert (house.account, house.account_id, house.institution, house.owner, house.asset_class) == (
        "Sample House", None, "Sample", "Joint", "House")
    assert (house.value_cents, house.liquid, house.source, house.as_of) == (40_000_025, False, "stated", "2030-01-01")
    assert rows["manual:sample-cash-old"].liquid is True


@pytest.mark.parametrize("cls", sorted(LIABILITY_CLASSES))
def test_a_stated_debt_is_never_liquid_even_when_the_entry_says_so(cls):
    stated = {**_HOUSE, "account": "Sample Stated Debt", "asset_class": cls, "value": -1_000, "liquid": True}
    (row,) = nw.snapshot_at(_series({}, [], [stated]), "2030-01-01").rows
    assert (row.asset_class, row.value_cents, row.liquid) == (cls, -1_000_00, False)


def test_two_manual_entries_with_one_slug_are_refused():
    clash = [_HOUSE, {**_HOUSE, "account": "sample-HOUSE!"}]
    msg = "accounts.yaml: manual_entries[1].account: same name as entry 0 — give each entry its own name"
    assert nw.manual_entry_problems(clash) == [msg]
    # two assets of one id would be one series: the map refuses them (the effective list never holds two)
    with pytest.raises(ValueError) as e:
        _series({}, [], clash)
    assert str(e.value) == "two manual assets with the id 'sample-house'"


def test_a_manual_slug_clash_is_a_config_load_error(tmp_path):
    d = tmp_path / "cfg"
    write_household(d)
    (tmp_path / "src").mkdir()
    (d / "accounts.yaml").write_text(yaml.safe_dump({
        "source_dir": "src", "accounts": {"demo_checking_0000": _acct("checking", "Sample Checking")},
        "manual_entries": [_HOUSE, {**_HOUSE, "account": "Sample  House"}]}))
    with pytest.raises(ValueError, match=r"manual_entries\[1\]\.account: same name as entry 0"):
        Config(d, source_dir=str(tmp_path / "src"))


def test_the_current_date_is_the_newest_account_observation_not_a_manual_entry():
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking")}
    later = {**_HOUSE, "account": "Sample Land", "as_of": "2030-03-01"}
    series = _series(accounts, [_obs("demo_checking_0000", "2030-01-10", 100_00),
                                _obs("demo_checking_0000", "2030-01-20", 110_00)], [later])
    day = nw.current_date(series, TODAY)
    assert day == "2030-01-20"
    snap = nw.snapshot_at(series, day)
    # dated after the day: carried back at its value, never missing
    assert _values(snap) == {"demo_checking_0000": 110_00, "manual:sample-land": 40_000_025}
    assert (snap.missing, snap.coverage) == ((), 1.0)
    assert [(r.as_of, r.carried_back) for r in snap.rows if r.row_id == "manual:sample-land"] == [("2030-03-01", True)]
    # with no account observed at all, the newest manual entry on or before the ingest day dates the snapshot
    assert nw.current_date(_series({}, [], [_HOUSE, later]), TODAY) == "2030-03-01"
    # an account point dates the snapshot whatever the ingest day
    assert nw.current_date(series, "2030-01-05") == "2030-01-20"


def test_with_no_account_point_a_value_dated_ahead_takes_effect_on_its_day():
    """No account has a point: the current snapshot is dated by the newest manual value on or before the ingest day,
    else the ingest day, so a value dated ahead waits for its day as it does with accounts present."""
    ahead = {**_HOUSE, "account": "Sample Land", "as_of": "2031-01-01", "value": 50_000}
    today = {**_HOUSE, "as_of": "2030-06-01"}
    series = _series({}, [], [today, ahead])
    assert nw.current_date(series, "2030-06-01") == "2030-06-01"
    snap = nw.snapshot_at(series, nw.current_date(series, "2030-06-01"))
    # the house at today's value; the land, whose only value is ahead, carried back at it (a first value counts at once)
    assert {r.row_id: (r.value_cents, r.as_of, r.carried_back) for r in snap.rows} == {
        "manual:sample-house": (40_000_025, "2030-06-01", False), "manual:sample-land": (50_000_00, "2031-01-01", True)}
    assert nw.snapshot_dates(series, "2030-06-01") == ["2030-06-01"]
    # on the ingest day the value ahead was dated, it dates the snapshot
    assert nw.snapshot_dates(series, "2031-01-01") == ["2031-01-01"]
    # every value ahead: the ingest day dates the snapshot, at the first value carried back
    assert nw.snapshot_dates(_series({}, [], [ahead]), "2030-06-01") == ["2030-06-01"]
    # acquired after the ingest day: nothing is held yet, so no snapshot
    assert nw.snapshot_dates(_series({}, [], [{**ahead, "acquired": "2031-01-01"}]), "2030-06-01") == []


def test_a_manual_entry_dated_ahead_does_not_date_the_pipelines_current_snapshot():
    """The same rule through Pipeline.parse_assets, from accounts.yaml `manual_entries` alone: the ingest day bounds
    the current snapshot's date."""
    ahead = {**_HOUSE, "account": "Sample Land", "as_of": "2031-01-01", "value": 50_000}
    p = Pipeline(SimpleNamespace(accounts={}, accounts_cfg={"manual_entries": [{**_HOUSE, "as_of": "2030-06-01"}, ahead]},
                                 net_worth_options={"include_credit_cards": False}, mortgage_account_id=lambda: None,
                                 manual_assets_path=NO_DOCUMENT, owners=("Alex", "Sam", "Joint")))
    p.ingested_at = dt.datetime.fromisoformat("2030-06-01T18:00:00")
    p.parse_assets()
    assert p.current_snapshot_date == "2030-06-01" and list(p.snapshot_coverage) == ["2030-06-01"]
    assert {r.account: r.value_cents for r in p.asset_rows} == {"Sample House": 40_000_025, "Sample Land": 50_000_00}


# ------------------------------------------------------------------ live brokerages and margin

_BROKER = {"institution": "Sample Broker", "owner": "Sam"}


def test_a_live_non_margin_brokerage_with_holdings_is_one_row_at_its_balance():
    """The feed's balance includes the uninvested cash its holdings leave out, so the account is one row at that
    balance in its class: a holdings sum that trails it is never a Cash or Margin row."""
    accounts = {"demo_brokerage_0000": _acct("brokerage", "Sample Brokerage", **_BROKER)}
    series = _series(accounts, [_obs("demo_brokerage_0000", "2030-01-10", 1_000_00)])
    holdings = [_holding("demo_brokerage_0000", "Sample Brokerage", 900_00)]
    snap = nw.snapshot_at(series, "2030-01-10", holdings=holdings, live=frozenset({"demo_brokerage_0000"}))
    assert [(r.account, r.account_id, r.asset_class, r.value_cents, r.source, r.as_of) for r in snap.rows] == [
        ("Sample Brokerage", "demo_brokerage_0000", "Stocks/ETF", 1_000_00, "simplefin_balance", "2030-01-10")]


@pytest.mark.parametrize(("balance", "remainder"), [
    (700_00, ("Sample Broker Margin", "Margin", -300_00, False)),     # borrowed against the positions
    (1_200_00, ("Sample Broker Cash", "Cash", 200_00, True)),         # uninvested cash
    (1_000_00, None),                                                 # the positions are the whole balance
])
def test_a_live_margin_account_breaks_down_into_gross_positions_and_a_remainder(balance, remainder):
    accounts = {"demo_brokerage_0000": _acct("brokerage", "Sample Margin", margin=True, **_BROKER)}
    series = _series(accounts, [_obs("demo_brokerage_0000", "2030-01-11", balance)])
    holdings = [_holding("demo_brokerage_0000", "Sample Margin", 600_00),
                _holding("demo_brokerage_0000", "Sample Margin", 400_00)]
    snap = nw.snapshot_at(series, "2030-01-11", holdings=holdings, live=frozenset({"demo_brokerage_0000"}))
    got = [(r.account, r.asset_class, r.value_cents, r.liquid, r.source, r.as_of) for r in snap.rows]
    want = [("Sample Margin", "Stocks/ETF", 1_000_00, True, "positions", "2030-01-10")]
    if remainder:
        want.append((*remainder, "simplefin_balance", "2030-01-11"))
    assert got == want
    assert {(r.row_id, r.account_id, r.institution, r.owner) for r in snap.rows} == {
        ("demo_brokerage_0000", "demo_brokerage_0000", "Sample Broker", "Sam")}
    assert sum(r.value_cents for r in snap.rows) == balance
    assert snap.coverage == 1.0                                       # one account, however many rows


def test_every_margin_account_breaks_down_not_only_the_first():
    accounts = {"demo_brokerage_0000": _acct("brokerage", "Sample Margin A", margin=True, **_BROKER),
                "demo_brokerage_0001": _acct("brokerage", "Sample Margin B", margin=True, institution="Sample Other",
                                             owner="Alex")}
    series = _series(accounts, [_obs("demo_brokerage_0000", "2030-01-10", 500_00),
                                _obs("demo_brokerage_0001", "2030-01-10", 900_00)])
    holdings = [_holding("demo_brokerage_0000", "Sample Margin A", 800_00),
                _holding("demo_brokerage_0001", "Sample Margin B", 600_00, institution="Sample Other", owner="Alex")]
    snap = nw.snapshot_at(series, "2030-01-10", holdings=holdings,
                          live=frozenset({"demo_brokerage_0000", "demo_brokerage_0001"}))
    assert [(r.account_id, r.account, r.asset_class, r.value_cents) for r in snap.rows] == [
        ("demo_brokerage_0000", "Sample Margin A", "Stocks/ETF", 800_00),
        ("demo_brokerage_0000", "Sample Broker Margin", "Margin", -300_00),
        ("demo_brokerage_0001", "Sample Margin B", "Stocks/ETF", 600_00),
        ("demo_brokerage_0001", "Sample Other Cash", "Cash", 300_00),
    ]


def test_a_margin_account_that_is_not_live_or_has_no_holdings_is_one_carried_row():
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking"),
                "demo_brokerage_0000": _acct("brokerage", "Sample Margin", margin=True, **_BROKER)}
    series = _series(accounts, [_obs("demo_brokerage_0000", "2030-01-02", 500_00),
                                _obs("demo_checking_0000", "2030-01-10", 100_00)])
    holdings = [_holding("demo_brokerage_0000", "Sample Margin", 800_00)]
    for kwargs in ({"holdings": holdings, "live": frozenset({"demo_checking_0000"})},     # dropped from the feed
                   {"holdings": None, "live": frozenset({"demo_brokerage_0000"})},     # no holdings asked for
                   {"holdings": [], "live": frozenset({"demo_brokerage_0000"})}):      # none reported
        rows = [r for r in nw.snapshot_at(series, "2030-01-10", **kwargs).rows if r.row_id == "demo_brokerage_0000"]
        assert [(r.account, r.asset_class, r.value_cents, r.source, r.as_of) for r in rows] == [
            ("Sample Margin", "Stocks/ETF", 500_00, "simplefin_balance", "2030-01-02")], kwargs


def test_a_brokerage_that_stopped_reporting_is_one_carried_row_in_its_class():
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking"),
                "demo_brokerage_0000": _acct("brokerage", "Sample Plan", asset_class="Retirement")}
    series = _series(accounts, [_obs("demo_brokerage_0000", "2030-01-02", 700_00),
                                _obs("demo_checking_0000", "2030-01-10", 100_00)])
    holdings = [_holding("demo_brokerage_0000", "Sample Plan", 650_00, asset_class="Retirement")]
    snap = nw.snapshot_at(series, "2030-01-10", holdings=holdings, live=frozenset({"demo_checking_0000"}))
    (row,) = [r for r in snap.rows if r.row_id == "demo_brokerage_0000"]
    assert (row.asset_class, row.liquid, row.value_cents, row.as_of) == ("Retirement", False, 700_00, "2030-01-02")


# ------------------------------------------------------------------ mortgage_included

def test_mortgage_included_is_the_mortgage_accounts_own_row():
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking"),
                "demo_loan_0000": _acct("loan", "Sample Mortgage", mortgage=True)}
    stated = {**_HOUSE, "account": "Sample Stated Mortgage", "asset_class": "Mortgage", "value": -100_000,
              "as_of": "2030-01-10"}
    seen = _series(accounts, [_obs("demo_checking_0000", "2030-01-10", 100_00),
                              _obs("demo_loan_0000", "2030-01-09", -200_000_00)])
    unseen = _series(accounts, [_obs("demo_checking_0000", "2030-01-10", 100_00)], [stated])
    assert nw.mortgage_included(nw.snapshot_at(unseen, "2030-01-10"), None) is True        # no mortgage account
    assert nw.mortgage_included(nw.snapshot_at(seen, "2030-01-10"), "demo_loan_0000") is True
    # configured but never observed: a hand-stated row of the Mortgage class does not stand in for it
    snap = nw.snapshot_at(unseen, "2030-01-10")
    assert "Mortgage" in {r.asset_class for r in snap.rows}
    assert nw.mortgage_included(snap, "demo_loan_0000") is False


# ------------------------------------------------------------------ roll-ups and the export

def test_the_exported_snapshot_rolls_up_assets_and_liabilities_coverage_and_missing():
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking"),
                "demo_brokerage_0000": _acct("brokerage", "Sample Margin", margin=True, **_BROKER),
                "demo_loan_0000": _acct("loan", "Sample Car Loan"),
                "demo_savings_0000": _acct("savings", "Sample Savings")}
    series = _series(accounts, [_obs("demo_checking_0000", "2030-01-10", 1_000_01),
                                _obs("demo_brokerage_0000", "2030-01-10", 700_00),
                                _obs("demo_loan_0000", "2030-01-08", -5_000_33),
                                _obs("demo_savings_0000", "2030-02-01", 50_00)],
                     [{**_HOUSE, "account": "Sample Later", "value": 300, "as_of": "2030-03-01"}])
    snap = nw.snapshot_at(series, "2030-01-10", holdings=[_holding("demo_brokerage_0000", "Sample Margin", 1_000_00)],
                          live=frozenset({"demo_brokerage_0000"}))
    rows = nw.asset_rows(snap)
    assert {r.snapshot_date for r in rows} == {"2030-01-10"}
    out = exports.export_assets_timeseries(
        rows, {"2030-01-10": True}, {"2030-01-10": (snap.coverage, nw.missing_names(series, snap))})
    (s,) = out["snapshots"]
    # the entry dated after the day is carried back at its value; only the account never observed is missing
    assert (s["assets_total"], s["liabilities_total"], s["total"]) == (2_300.01, -5_300.33, -3_000.32)
    assert round(s["assets_total"] + s["liabilities_total"], 2) == s["total"]
    assert (s["coverage"], s["missing"]) == (0.8, ["Sample Savings"])
    assert s["source"] == s["rows"][0]["source"] == "simplefin_balance"     # the first exported row: the loan
    assert [r["account_id"] for r in s["rows"] if r["asset_class"] == "Margin"] == ["demo_brokerage_0000"]


# ------------------------------------------------------------------ month-end snapshots

def _assets(accounts: dict, obs: list[BalanceObs], *, manual: list[dict] | None = None,
            holdings: list[Holding] | None = None, live: set[str] | None = None,
            mortgage_id: str | None = None) -> Pipeline:
    """Pipeline.parse_assets over invented accounts: the series map and every snapshot read from it."""
    p = Pipeline(SimpleNamespace(accounts=accounts, accounts_cfg={"manual_entries": manual or []},
                                 net_worth_options={"include_credit_cards": False},
                                 mortgage_account_id=lambda: mortgage_id,
                                 manual_assets_path=NO_DOCUMENT, owners=("Alex", "Sam", "Joint")))
    p.ingested_at = dt.datetime.fromisoformat(f"{TODAY}T18:00:00")
    p.balance_obs, p.holdings, p.simplefin_live_accounts = list(obs), list(holdings or []), set(live or ())
    p.parse_assets()
    return p


def _rows_on(p: Pipeline, day: str) -> list:
    return [r for r in p.asset_rows if r.snapshot_date == day]


_TWO = {"demo_checking_0000": _acct("checking", "Sample Checking"),
        "demo_brokerage_0000": _acct("brokerage", "Sample Brokerage")}
# four months of points, the newest mid-month; the brokerage first reports in the third month
_FOUR_MONTHS = [_obs("demo_checking_0000", "2030-01-15", 100_00), _obs("demo_brokerage_0000", "2030-03-05", 900_00),
                _obs("demo_checking_0000", "2030-02-10", 110_00), _obs("demo_checking_0000", "2030-04-12", 120_00)]


def test_the_snapshot_dates_are_each_month_end_before_the_current_date_then_the_current_date():
    series = _series(_TWO, _FOUR_MONTHS, [{**_HOUSE, "as_of": "2029-06-01"}])    # a manual entry starts no month
    assert nw.current_date(series, TODAY) == "2030-04-12"
    assert nw.snapshot_dates(series, TODAY) == ["2030-01-31", "2030-02-28", "2030-03-31", "2030-04-12"]
    across = _series(_TWO, [_obs("demo_checking_0000", "2030-11-20", 1_00), _obs("demo_checking_0000", "2032-03-01", 1_00)])
    dates = nw.snapshot_dates(across, TODAY)
    assert dates[:3] == ["2030-11-30", "2030-12-31", "2031-01-31"] and "2032-02-29" in dates
    assert dates[-2:] == ["2032-02-29", "2032-03-01"] and len(dates) == 17


def test_a_current_date_on_a_month_end_is_one_snapshot_date():
    series = _series(_TWO, [_obs("demo_checking_0000", "2030-01-15", 100_00),
                            _obs("demo_checking_0000", "2030-02-28", 110_00)])
    assert nw.snapshot_dates(series, TODAY) == ["2030-01-31", "2030-02-28"]


def test_an_account_whose_only_point_is_the_current_date_is_one_snapshot():
    series = _series(_TWO, [_obs("demo_checking_0000", "2030-01-10", 100_00)], [{**_HOUSE, "as_of": "2029-01-01"}])
    assert nw.snapshot_dates(series, TODAY) == ["2030-01-10"]
    # with no account series the newest manual entry on or before the ingest day is the one date; with nothing at all
    # there is none
    later = {**_HOUSE, "account": "Sample Land", "as_of": "2030-03-01"}
    assert nw.snapshot_dates(_series({}, [], [_HOUSE, later]), TODAY) == ["2030-03-01"]
    assert nw.snapshot_dates({}, TODAY) == [] and nw.snapshot_dates(_series(_TWO, []), TODAY) == []


def test_a_month_end_before_an_accounts_first_point_lists_it_as_missing():
    p = _assets(_TWO, _FOUR_MONTHS)
    assert list(p.snapshot_coverage) == ["2030-01-31", "2030-02-28", "2030-03-31", "2030-04-12"]
    for day in ("2030-01-31", "2030-02-28"):
        assert p.snapshot_coverage[day] == (0.5, ["Sample Brokerage"])
        assert [(r.account_id, r.value_cents) for r in _rows_on(p, day)] == [
            ("demo_checking_0000", 100_00 if day == "2030-01-31" else 110_00)]
    assert p.snapshot_coverage["2030-03-31"] == p.snapshot_coverage["2030-04-12"] == (1.0, [])
    assert {r.snapshot_date for r in p.asset_rows} == set(p.snapshot_coverage)
    assert p.current_snapshot_date == "2030-04-12"


def test_a_manual_entry_is_in_every_snapshot_carried_back_before_its_date():
    checking = {"demo_checking_0000": _TWO["demo_checking_0000"]}
    p = _assets(checking, [_obs("demo_checking_0000", "2030-01-10", 100_00),
                           _obs("demo_checking_0000", "2030-03-20", 130_00)], manual=[{**_HOUSE, "as_of": "2030-02-15"}])
    assert list(p.snapshot_coverage) == ["2030-01-31", "2030-02-28", "2030-03-20"]
    assert p.snapshot_coverage["2030-01-31"] == (1.0, [])                 # carried back: never missing
    for day, carried in (("2030-01-31", True), ("2030-02-28", False), ("2030-03-20", False)):
        (house,) = [r for r in _rows_on(p, day) if r.account == "Sample House"]
        assert (house.value_cents, house.as_of, house.source, house.manual_id, house.carried_back) == (
            40_000_025, "2030-02-15", "stated", "sample-house", carried), day
    assert {(r.manual_id, r.carried_back) for r in p.asset_rows if r.account_id} == {(None, False)}


def test_mortgage_included_is_false_before_the_mortgages_first_point_and_true_from_it():
    accounts = {"demo_checking_0000": _TWO["demo_checking_0000"],
                "demo_loan_0000": _acct("loan", "Sample Mortgage", mortgage=True)}
    p = _assets(accounts, [_obs("demo_checking_0000", "2030-01-05", 100_00),
                           _obs("demo_loan_0000", "2030-02-10", -200_000_00),
                           _obs("demo_checking_0000", "2030-03-12", 110_00)], mortgage_id="demo_loan_0000")
    assert p.mortgage_included == {"2030-01-31": False, "2030-02-28": True, "2030-03-12": True}
    assert p.snapshot_coverage["2030-01-31"] == (0.5, ["Sample Mortgage"])
    assert [r.as_of for r in p.asset_rows if r.asset_class == "Mortgage"] == ["2030-02-10", "2030-02-10"]


def test_an_account_never_observed_is_missing_at_every_date_and_dates_nothing():
    accounts = {**_TWO, "demo_savings_0000": _acct("savings", "Sample Savings")}      # configured, never reported
    obs = [_obs("demo_checking_0000", "2030-01-10", 100_00), _obs("demo_brokerage_0000", "2030-01-10", 900_00),
           _obs("demo_checking_0000", "2030-02-20", 110_00)]
    series = _series(accounts, obs)
    assert series["demo_savings_0000"].points == ()
    assert nw.current_date(series, TODAY) == "2030-02-20" == nw.current_date(_series(_TWO, obs), TODAY)
    assert nw.snapshot_dates(series, TODAY) == ["2030-01-31", "2030-02-20"] == nw.snapshot_dates(_series(_TWO, obs), TODAY)
    p = _assets(accounts, obs)
    assert p.snapshot_coverage == {day: (pytest.approx(2 / 3), ["Sample Savings"]) for day in ("2030-01-31", "2030-02-20")}


def test_the_current_snapshot_is_the_one_read_with_the_breakdown_and_month_ends_carry_none():
    accounts = {"demo_checking_0000": _TWO["demo_checking_0000"],
                "demo_brokerage_0000": _acct("brokerage", "Sample Margin", margin=True, **_BROKER)}
    obs = [_obs("demo_brokerage_0000", "2030-01-31", 700_00), _obs("demo_checking_0000", "2030-01-20", 100_00),
           _obs("demo_checking_0000", "2030-02-10", 120_00)]
    holdings = [_holding("demo_brokerage_0000", "Sample Margin", 1_000_00)]
    live = {"demo_brokerage_0000", "demo_checking_0000"}
    p = _assets(accounts, obs, holdings=holdings, live=live)
    assert p.current_snapshot_date == "2030-02-10"
    current = nw.snapshot_at(p.networth_series, "2030-02-10", holdings=holdings, live=frozenset(live))
    assert _rows_on(p, "2030-02-10") == nw.asset_rows(current)
    # the month end holds the margin account as one row at its balance; the current snapshot breaks the same
    # balance into its positions and the margin loan
    month_end = [r for r in _rows_on(p, "2030-01-31") if r.account_id == "demo_brokerage_0000"]
    now = [r for r in _rows_on(p, "2030-02-10") if r.account_id == "demo_brokerage_0000"]
    assert [(r.account, r.asset_class, r.value_cents, r.source) for r in month_end] == [
        ("Sample Margin", "Stocks/ETF", 700_00, "simplefin_balance")]
    assert [(r.asset_class, r.source) for r in now] == [("Stocks/ETF", "positions"), ("Margin", "simplefin_balance")]
    assert sum(r.value_cents for r in now) == sum(r.value_cents for r in month_end) == 700_00


# ------------------------------------------------------------------ the daily line

_HOUSE_CENTS = 40_000_025


def test_the_daily_line_sums_every_series_on_each_date_an_account_or_a_manual_value_starts():
    """The line's dates are the account series' dates and, within their span, a manual entry's; on each one every
    series counts its last point on or before it, and a series that has not started yet its first point, held flat
    backwards. A manual entry counts on every date, before its own too."""
    series = _series(_TWO, [_obs("demo_checking_0000", "2030-01-05", 100_00),
                            _obs("demo_brokerage_0000", "2030-01-10", 800_00),
                            _obs("demo_checking_0000", "2030-01-15", 150_00),
                            _obs("demo_brokerage_0000", "2030-01-20", 900_00)],
                     [{**_HOUSE, "as_of": "2030-01-12"}])
    assert nw.daily(series) == [("2030-01-05", 100_00 + 800_00 + _HOUSE_CENTS, 0.5),
                                ("2030-01-10", 100_00 + 800_00 + _HOUSE_CENTS, 1.0),
                                ("2030-01-12", 100_00 + 800_00 + _HOUSE_CENTS, 1.0),
                                ("2030-01-15", 150_00 + 800_00 + _HOUSE_CENTS, 1.0),
                                ("2030-01-20", 150_00 + 900_00 + _HOUSE_CENTS, 1.0)]


def test_the_daily_line_books_the_series_maps_balance_of_a_date_a_file_and_the_feed_both_report():
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking")}
    for file_cents, feed_cents in ((100_00, 120_00), (120_00, 100_00)):
        series = _series(accounts, [_obs("demo_checking_0000", "2030-01-10", feed_cents),
                                    _obs("demo_checking_0000", "2030-01-10", file_cents, source="csv")])
        assert nw.daily(series) == [("2030-01-10", file_cents, 1.0)]       # the file's, larger or smaller


def test_daily_coverage_steps_up_as_accounts_start_and_counts_observed_accounts_only():
    """Coverage on a date is the share of the accounts with a point that have one on or before it: a manual entry and
    an account never observed are in neither count."""
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking"),
                "demo_brokerage_0000": _acct("brokerage", "Sample Brokerage"),
                "demo_loan_0000": _acct("loan", "Sample Car Loan"),
                "demo_savings_0000": _acct("savings", "Sample Savings")}           # never observed
    series = _series(accounts, [_obs("demo_checking_0000", "2030-01-01", 100_00),
                                _obs("demo_brokerage_0000", "2030-01-10", 900_00),
                                _obs("demo_loan_0000", "2030-01-20", -500_00)],
                     [{**_HOUSE, "as_of": "2029-06-01"}])
    assert [(day, coverage) for day, _, coverage in nw.daily(series)] == [
        ("2030-01-01", 1 / 3), ("2030-01-10", 2 / 3), ("2030-01-20", 1.0)]


def test_no_account_point_is_no_daily_line():
    assert nw.daily({}) == []
    assert nw.daily(_series(_TWO, [])) == []                                   # configured, never observed
    assert nw.daily(_series(_TWO, [], [_HOUSE])) == []                         # a manual entry alone dates nothing


def test_an_account_that_stops_reporting_leaves_the_earlier_line_unchanged_and_is_carried_after():
    """Removing a quiet account's newest observation moves no point dated before it, and after its last point the
    account is carried at it: the line's history does not shift when an account drops out of the feed."""
    stops = _obs("demo_brokerage_0000", "2030-01-25", 950_00)
    obs = [_obs("demo_checking_0000", "2030-01-05", 100_00), _obs("demo_checking_0000", "2030-01-20", 110_00),
           _obs("demo_checking_0000", "2030-02-10", 120_00), _obs("demo_checking_0000", "2030-03-01", 130_00),
           _obs("demo_brokerage_0000", "2030-01-10", 900_00), stops]
    full = nw.daily(_series(_TWO, obs))
    cut = nw.daily(_series(_TWO, [o for o in obs if o is not stops]))
    assert [p for p in full if p[0] < stops.as_of] == [p for p in cut if p[0] < stops.as_of]
    assert [p for p in full if p[0] >= stops.as_of] == [
        ("2030-01-25", 110_00 + 950_00, 1.0), ("2030-02-10", 120_00 + 950_00, 1.0),
        ("2030-03-01", 130_00 + 950_00, 1.0)]
    assert [p for p in cut if p[0] > "2030-01-10"] == [
        ("2030-01-20", 110_00 + 900_00, 1.0), ("2030-02-10", 120_00 + 900_00, 1.0),
        ("2030-03-01", 130_00 + 900_00, 1.0)]


def test_credit_cards_are_in_the_daily_line_by_the_flag():
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking"),
                "demo_card_0000": _acct("credit-card", "Sample Card")}
    obs = [_obs("demo_checking_0000", "2030-01-10", 1_000_00), _obs("demo_card_0000", "2030-01-05", -200_00),
           _obs("demo_card_0000", "2030-01-12", -250_00)]
    assert nw.daily(_series(accounts, obs)) == [("2030-01-10", 1_000_00, 1.0)]
    assert nw.daily(_series(accounts, obs, cards=True)) == [
        ("2030-01-05", 800_00, 0.5), ("2030-01-10", 800_00, 1.0), ("2030-01-12", 750_00, 1.0)]


def test_the_last_daily_point_is_the_current_total_a_manual_entry_dated_after_the_current_date_included():
    """A manual entry dated after the current date is carried back into the current snapshot as into the line, so the
    line's last point is the snapshot's total with or without one."""
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking")}
    obs = [_obs("demo_checking_0000", "2030-01-10", 100_00), _obs("demo_checking_0000", "2030-01-20", 110_00)]
    later = {**_HOUSE, "account": "Sample Land", "as_of": "2030-03-01", "value": 50_000}
    for manual in ([_HOUSE], [_HOUSE, later]):
        series = _series(accounts, obs, manual)
        current = nw.snapshot_at(series, nw.current_date(series, TODAY))
        assert current.missing == ()
        day, cents, _ = nw.daily(series)[-1]
        assert (day, cents) == (current.date, sum(r.value_cents for r in current.rows))
    assert cents == 110_00 + 40_000_025 + 50_000_00                       # the control: the later entry counts


def test_every_fully_observed_snapshot_total_is_the_daily_value_on_its_date():
    """For every snapshot the pipeline reads at which every series with a point has one on or before the day, its
    total equals the line's value at the last line date on or before that day: the line and the snapshots read one
    map. The accounts start in turn, the loan stops reporting, a manual entry counts, an account is never observed,
    and the current snapshot breaks a live margin account into its positions."""
    accounts = {"demo_checking_0000": _acct("checking", "Sample Checking"),
                "demo_brokerage_0000": _acct("brokerage", "Sample Margin", margin=True, **_BROKER),
                "demo_loan_0000": _acct("loan", "Sample Car Loan"),
                "demo_savings_0000": _acct("savings", "Sample Savings")}           # never observed
    checking = [("2030-01-05", 1_000_00), ("2030-01-25", 1_200_00), ("2030-02-14", 900_00), ("2030-03-03", 1_100_00),
                ("2030-04-18", 1_300_00), ("2030-05-09", 1_250_00), ("2030-06-12", 1_400_00)]
    brokerage = [("2030-02-07", 5_000_00), ("2030-03-21", 5_400_00), ("2030-04-18", 5_250_00),
                 ("2030-05-20", 5_600_00), ("2030-06-12", 5_500_00)]
    loan = [("2030-03-10", -20_000_00), ("2030-03-28", -19_900_00)]                   # then stops reporting
    obs = [_obs(a, d, c) for a, pts in (("demo_checking_0000", checking), ("demo_brokerage_0000", brokerage),
                                         ("demo_loan_0000", loan)) for d, c in pts]
    p = _assets(accounts, obs, manual=[_HOUSE], holdings=[_holding("demo_brokerage_0000", "Sample Margin", 6_000_00)],
                live={"demo_brokerage_0000", "demo_checking_0000"})
    line = nw.daily(p.networth_series)
    checked = []
    for day, (_, missing) in p.snapshot_coverage.items():
        if missing != ["Sample Savings"]:
            continue
        value = [cents for d, cents, _ in line if d <= day][-1]
        assert sum(r.value_cents for r in _rows_on(p, day)) == value, day
        checked.append(day)
    assert checked == ["2030-03-31", "2030-04-30", "2030-05-31", "2030-06-12"]
    assert line[-1][0] == p.current_snapshot_date == "2030-06-12"
    assert [r.source for r in _rows_on(p, "2030-06-12") if r.account_id == "demo_brokerage_0000"] == [
        "positions", "simplefin_balance"]

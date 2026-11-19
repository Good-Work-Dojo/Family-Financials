# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""One zone, the household's (household.yaml `timezone`), draws every day boundary the ETL draws: the date of a feed
epoch (balances and transactions), the holdings date when an account has no balance date (the snapshot's UTC fetch
stamp read in that zone) and the offset `manifest.generated_at` carries. An unknown zone is a config error, never a
silent UTC. Three zones tell the cases apart: one behind UTC, UTC itself and one ahead of it. The ingest summary's
spending line names the data's last full calendar year. Synthetic snapshots and config dirs, invented ids, 2030 dates."""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from conftest import isolate, make_txn, write_household

from etl import pipeline
from etl.config import Config
from etl.parsers import simplefin as sfp
from etl.pipeline import Pipeline, summary_year

UNKNOWN_ZONE = ("household.yaml: timezone: unknown zone 'Mars/Olympus' — "
                "use an IANA name such as America/New_York")


def utc_epoch(y: int, mo: int, d: int, h: int, mi: int) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp())


EARLY = utc_epoch(2030, 3, 10, 3, 30)    # the 9th behind UTC; the 10th in UTC and ahead of it
LATE = utc_epoch(2030, 3, 10, 20, 30)    # the 10th behind UTC and in UTC; the 11th ahead of it

# zone -> (local date of EARLY, local date of LATE): each zone differs from the other two in at least one
ZONES = {
    "America/Chicago": ("2030-03-09", "2030-03-10"),
    "UTC": ("2030-03-10", "2030-03-10"),
    "Pacific/Auckland": ("2030-03-10", "2030-03-11"),
}

CHECKING = {"name": "Sample Checking", "institution": "Sample Bank", "owner": "Joint", "kind": "checking"}
BROKERAGE = {"name": "Sample Brokerage", "institution": "Sample Broker", "owner": "Alex", "kind": "brokerage"}
META = {"demo_checking_0000": CHECKING, "demo_brokerage_0000": BROKERAGE}
SFIDS = {"SF-DEMO-CHK": "demo_checking_0000", "SF-DEMO-BRK": "demo_brokerage_0000"}


def checking(*posted: int, balance_date: int | None = EARLY) -> dict:
    """The checking account as the feed reports it: a balance dated `balance_date` and one transaction per epoch."""
    a = {"id": "SF-DEMO-CHK", "name": "Checking", "org": {"name": "Sample Bank"}, "currency": "USD",
         "balance": "1000.00", "transactions": [
             {"id": f"t{i}", "posted": e, "amount": "-12.00", "description": f"SAMPLE SHOP {i}"}
             for i, e in enumerate(posted)]}
    if balance_date is not None:
        a["balance-date"] = balance_date
    return a


def brokerage_without_a_balance_date() -> dict:
    return {"id": "SF-DEMO-BRK", "name": "Brokerage", "org": {"name": "Sample Broker"}, "currency": "USD",
            "balance": "500.00", "transactions": [], "holdings": [
                {"id": "h1", "symbol": "SMPL", "description": "Sample fund", "shares": "5",
                 "cost_basis": "400.00", "market_value": "500.00"}]}


def write_snapshot(path: Path, *accounts: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"accounts": list(accounts), "errlist": []}), "utf-8")
    return path


def config_dir(tmp_path: Path, timezone: str) -> Path:
    """A synthetic config dir: two feed-mapped accounts and a household in `timezone`."""
    d = tmp_path / "config"
    d.mkdir(parents=True)
    (tmp_path / "statements").mkdir()
    accounts = {"source_dir": "../statements", "accounts": {
        "demo_checking_0000": {**CHECKING, "simplefin_id": "SF-DEMO-CHK"},
        "demo_brokerage_0000": {**BROKERAGE, "simplefin_id": "SF-DEMO-BRK"}}}
    (d / "accounts.yaml").write_text(yaml.safe_dump(accounts, sort_keys=False), "utf-8")
    write_household(d, timezone=timezone)
    return d


# ------------------------------------------------------------------ feed epochs -> dates

@pytest.mark.parametrize("tz", ZONES)
def test_a_feed_epoch_is_dated_in_the_households_zone(tmp_path, tz):
    early, late = ZONES[tz]
    snap = write_snapshot(tmp_path / "accounts-a.json", checking(EARLY, LATE))
    txns, balances, *_ = sfp.parse_snapshot(snap, SFIDS, {}, META, tz)
    assert [t.date for t in txns] == [early, late]
    assert [(b.account_id, b.as_of) for b in balances] == [("demo_checking_0000", early)]


def test_the_spring_forward_night_dates_by_the_offset_in_force_at_that_instant(tmp_path):
    # Chicago moves from UTC-6 to UTC-5 at 08:00 UTC on 2030-03-10. 07:30 UTC is 01:30 local, still the 10th;
    # 05:30 UTC on the 11th is 00:30 local on the 11th, where a fixed UTC-6 would still say the 10th.
    snap = write_snapshot(tmp_path / "accounts-dst.json",
                          checking(utc_epoch(2030, 3, 10, 7, 30), utc_epoch(2030, 3, 11, 5, 30)))
    txns = sfp.parse_snapshot(snap, SFIDS, {}, META, "America/Chicago")[0]
    assert [t.date for t in txns] == ["2030-03-10", "2030-03-11"]


@pytest.mark.parametrize("stamp,tz,expected", [
    ("20300310T033000Z", "America/Chicago", "2030-03-09"),
    ("20300310T033000Z", "UTC", "2030-03-10"),
    ("20300310T033000Z", "Pacific/Auckland", "2030-03-10"),
    ("20300310T203000Z", "America/Chicago", "2030-03-10"),
    ("20300310T203000Z", "UTC", "2030-03-10"),
    ("20300310T203000Z", "Pacific/Auckland", "2030-03-11"),
])
def test_holdings_without_a_balance_date_take_the_fetch_stamps_local_date(tmp_path, stamp, tz, expected):
    snap = write_snapshot(tmp_path / f"accounts-{stamp}.json", brokerage_without_a_balance_date())
    _, balances, holdings, *_ = sfp.parse_snapshot(snap, SFIDS, {}, META, tz)
    assert balances == []                                         # no balance date: no balance point
    assert [(h.account_id, h.as_of) for h in holdings] == [("demo_brokerage_0000", expected)]


def test_a_balance_date_still_dates_the_holdings_before_the_fetch_stamp(tmp_path):
    held = {**brokerage_without_a_balance_date(), "balance-date": LATE}
    snap = write_snapshot(tmp_path / "accounts-20300310T033000Z.json", held)
    holdings = sfp.parse_snapshot(snap, SFIDS, {}, META, "Pacific/Auckland")[2]
    assert [h.as_of for h in holdings] == ["2030-03-11"]


@pytest.mark.parametrize("tz", ["Mars/Olympus", "Not A Zone", ""])
def test_the_parser_refuses_an_unknown_zone_even_for_an_empty_snapshot(tmp_path, tz):
    snap = write_snapshot(tmp_path / "accounts-20300310T033000Z.json")
    with pytest.raises(ValueError, match="unknown time zone"):
        sfp.parse_snapshot(snap, SFIDS, {}, META, tz)


@pytest.mark.parametrize("tz", [5, None, ["UTC"], "localtime"])
def test_the_parser_refuses_a_zone_that_is_not_text_or_is_the_machines_own(tmp_path, tz):
    """The same error as an unknown name, never a TypeError: `localtime` is whatever zone the machine is set to."""
    snap = write_snapshot(tmp_path / "accounts-20300310T033000Z.json")
    with pytest.raises(ValueError, match="unknown time zone"):
        sfp.parse_snapshot(snap, SFIDS, {}, META, tz)


def test_a_balance_late_in_the_utc_day_lands_on_the_next_local_date_ahead_of_utc(tmp_path):
    """Ahead of UTC the late instant is already the next day: the balance takes that local date, not UTC's."""
    snap = write_snapshot(tmp_path / "accounts-a.json", checking(balance_date=LATE))
    balances = sfp.parse_snapshot(snap, SFIDS, {}, META, "Pacific/Auckland")[1]
    utc_day = dt.datetime.fromtimestamp(LATE, dt.timezone.utc).date().isoformat()
    assert utc_day == "2030-03-10"
    assert [(b.account_id, b.as_of) for b in balances] == [("demo_checking_0000", "2030-03-11")]


@pytest.mark.parametrize("tz", ZONES)
def test_the_pipeline_reads_the_feed_in_the_configs_zone(tmp_path, tz):
    early, late = ZONES[tz]
    cfg = Config(config_dir(tmp_path, tz))
    assert cfg.simplefin_tz == tz
    cfg.simplefin_snapshots_dir = tmp_path / "store" / "snapshots"
    write_snapshot(cfg.simplefin_snapshots_dir / "accounts-20300310T033000Z.json",
                   checking(EARLY, LATE), brokerage_without_a_balance_date())
    p = Pipeline(cfg)
    p.parse_simplefin()
    assert sorted(t.date for t in p.txns) == [early, late]
    assert {(b.account_id, b.as_of) for b in p.balance_obs} == {("demo_checking_0000", early)}
    assert [h.as_of for h in p.holdings] == [early]               # the stamp is the EARLY instant


# ------------------------------------------------------------------ manifest.generated_at

@pytest.fixture
def process_zone(monkeypatch):
    """The process's own zone set to one no household zone below shares (UTC+05:30, no daylight saving)."""
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _offset(tz: str) -> str:
    z = dt.datetime.now(ZoneInfo(tz)).strftime("%z")
    return f"{z[:3]}:{z[3:]}"


@pytest.mark.parametrize("tz", ZONES)
def test_generated_at_is_the_households_wall_clock_with_its_offset(tmp_path, tz, process_zone):
    assert dt.datetime.now().astimezone().strftime("%z") == "+0530"            # the process is elsewhere
    cfg = isolate(Config(config_dir(tmp_path, tz)), tmp_path / "state")
    before = _offset(tz)
    Pipeline(cfg).export()
    after = _offset(tz)
    stamp = json.loads((cfg.exports_dir / "manifest.json").read_text("utf-8"))["generated_at"]
    # the shape the dashboard reads: ISO 8601 to the second with a numeric offset; its first 19 characters are the
    # household's wall clock
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d", stamp), stamp
    assert stamp[19:] in {before, after}
    wall = dt.datetime.fromisoformat(stamp[:19])
    assert abs((dt.datetime.now(ZoneInfo(tz)).replace(tzinfo=None) - wall).total_seconds()) < 120


# ------------------------------------------------------------------ an unknown zone is a config error

@pytest.mark.parametrize("tz", ["Mars/Olympus", "America", "Not A Zone"])
def test_an_unknown_zone_is_refused_at_load(tmp_path, tz):
    with pytest.raises(ValueError) as e:
        Config(config_dir(tmp_path, tz))
    assert str(e.value).startswith(f"household.yaml: timezone: unknown zone {tz!r} — use an IANA name"), e.value


def test_an_unknown_zone_stops_the_ingest_before_anything_is_written(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_dir(home, "Mars/Olympus")
    with pytest.raises(ValueError) as e:
        Config(home / "config")
    assert str(e.value) == UNKNOWN_ZONE
    monkeypatch.setenv("FF_HOME", str(home))
    with pytest.raises(ValueError) as e:
        pipeline.run()
    assert str(e.value) == UNKNOWN_ZONE
    assert not (home / "data").exists() and not (home / "state").exists()     # no database, no export, no state


# ------------------------------------------------------------------ the summary's year

def _spend(date: str, cents: int, **kw):
    return make_txn(date=date, account="demo_checking_0000", desc="SAMPLE SHOP", cents=cents, **kw)


def test_summary_year_is_the_last_year_the_data_runs_past():
    assert summary_year([_spend("2030-06-01", -100), _spend("2031-02-01", -100)]) == ("2030", True)
    # a year with no rows is no candidate: the newest year before the data's own newest one that has rows
    assert summary_year([_spend("2028-12-31", -100), _spend("2031-01-01", -100)]) == ("2028", True)
    # every row in one year: that year, not complete (the data does not run past it, even on 31 December)
    assert summary_year([_spend("2030-01-01", -100), _spend("2030-12-31", -100)]) == ("2030", False)
    assert summary_year([]) is None


def _summary(tmp_path: Path, rows: list, capsys) -> str:
    p = Pipeline(isolate(Config(config_dir(tmp_path, "UTC")), tmp_path / "state"))
    p.txns = rows
    p.print_summary()
    return capsys.readouterr().out


def test_the_summary_sums_spending_for_the_last_full_year(tmp_path, capsys):
    rows = [_spend("2029-05-01", -999), _spend("2030-03-01", -1500), _spend("2030-11-30", -2500),
            _spend("2030-07-01", 4000, type="income"), _spend("2031-01-15", -7000)]
    out = _summary(tmp_path, rows, capsys)
    assert "2030 spending: $40.00" in out                             # spending only, and only 2030's
    assert "2031 spending" not in out and "partial" not in out


def test_a_summary_inside_one_year_says_that_year_is_partial(tmp_path, capsys):
    out = _summary(tmp_path, [_spend("2031-01-15", -7000)], capsys)
    assert "2031 spending (partial year): $70.00" in out


def test_a_summary_with_no_transactions_has_no_spending_line(tmp_path, capsys):
    out = _summary(tmp_path, [], capsys)
    assert "spending" not in out and "transactions: 0" in out

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Coverage as data: cashflow_monthly.json `months[].in_coverage` and top-level `coverage_floor`.

A month is in coverage when at least `coverage.min_share` (assumptions.yaml, default 0.6) of the cash-flow accounts
that have any transaction have their first transaction in that month or earlier; the floor is the first such month,
or null. An account that has begun never stops counting, so a quiet account or a thin newest month moves nothing; an
account whose first row is dated after the ingest day has not begun until that day arrives, so the floor is never a
month after the ingest day's.
`full_coverage` asks the same accounts, those with any transaction, whether every one's window holds the month: an
account that never transacts holds no month back, yet while no cash-flow account has a transaction no month is fully
covered, and a household with no cash-flow account at all misses none. Synthetic accounts and months, and the demo
config's share."""
from __future__ import annotations

import json
import random
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from conftest import (
    ROOT,
    demo_config,
    isolated_pipeline,
    load_demo,
    make_txn,
    write_household,
)

from etl import exports
from etl.config import DEFAULT_COVERAGE_MIN_SHARE, Config
from tools.demo import household as hh

MONTHS = ["2030-01", "2030-02", "2030-03", "2030-04", "2030-05", "2030-06"]


def _ids(n: int) -> list[str]:
    return [f"demo_checking_{i:04d}" for i in range(n)]


def _windows(spans: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """account -> (first month, last month) as the transaction window the pipeline records (first and last date)."""
    return {a: (f"{first}-03", f"{last}-27") for a, (first, last) in spans.items()}


def _export(spans: dict[str, tuple[str, str]], accounts: list[str], *, min_share: float = 0.6,
            months: list[str] = MONTHS) -> dict:
    """The cash-flow export over one purchase in each of `months`, with each account's window from `spans`."""
    rows = [make_txn(date=f"{m}-15", account="demo_checking_0000", cents=-1000) for m in months]
    return exports.export_cashflow_monthly(rows, _windows(spans), accounts, min_share=min_share)


def _flags(doc: dict) -> list[bool]:
    return [m["in_coverage"] for m in doc["months"]]


def _covered(doc: dict) -> list[int]:
    return [len(m["accounts_covered"]) for m in doc["months"]]


# ------------------------------------------------------------------ the rule

def test_eight_accounts_joining_over_six_months_cover_from_the_first_month_at_the_share():
    accounts = _ids(8)
    joins = ["2030-01", "2030-02", "2030-03", "2030-03", "2030-04", "2030-05", "2030-05", "2030-06"]
    doc = _export({a: (j, "2030-06") for a, j in zip(accounts, joins, strict=True)}, accounts)
    assert _covered(doc) == [1, 2, 4, 5, 7, 8]
    # 0.6 of 8 is 4.8: five accounts are the first count at the share, and every later month keeps it
    assert _flags(doc) == [False, False, False, True, True, True]
    assert doc["coverage_floor"] == "2030-04"
    assert [m["full_coverage"] for m in doc["months"]] == [False] * 5 + [True]
    assert doc["cashflow_accounts"] == accounts


def test_accounts_that_stop_reporting_after_the_floor_leave_it_where_it_is():
    accounts = _ids(8)
    spans = {**{a: ("2030-01", "2030-03") for a in accounts[:4]},     # stop reporting after March
             **{a: ("2030-01", "2030-06") for a in accounts[4:6]},    # report throughout
             **{a: ("2030-05", "2030-06") for a in accounts[6:]}}     # join in May
    doc = _export(spans, accounts, min_share=0.5)
    assert _covered(doc) == [6, 6, 6, 2, 4, 4]
    # six of the eight have begun by January; April's thin month is no gap in the history, so every month is in
    assert _flags(doc) == [True] * 6
    assert doc["coverage_floor"] == "2030-01"
    assert [m["full_coverage"] for m in doc["months"]] == [False] * 6


def test_the_flags_are_a_suffix_and_the_floor_is_its_first_month():
    accounts = _ids(8)
    joins = ["2030-01", "2030-02", "2030-03", "2030-03", "2030-04", "2030-05", "2030-05", "2030-06"]
    for share in (0.1, 0.25, 0.5, 0.6, 0.75, 0.9, 1.0):
        doc = _export({a: (j, "2030-06") for a, j in zip(accounts, joins, strict=True)}, accounts, min_share=share)
        flags = _flags(doc)
        first = flags.index(True) if True in flags else len(flags)
        assert flags == [False] * first + [True] * (len(flags) - first), share
        assert doc["coverage_floor"] == (doc["months"][first]["month"] if first < len(flags) else None), share


def test_whatever_the_windows_the_flags_are_a_suffix_that_counts_the_accounts_begun():
    """Random windows, gaps and accounts with no row among them: a month is in coverage exactly when the share of the
    accounts with history that began by it meets the share, and once a month is in, every later one is."""
    rng = random.Random(20300101)
    for _ in range(200):
        accounts = _ids(rng.randint(1, 9))
        spans = {}
        for a in accounts:
            if rng.random() < 0.8:
                first = rng.randrange(len(MONTHS))
                spans[a] = (MONTHS[first], MONTHS[rng.randrange(first, len(MONTHS))])
        share = rng.choice((0.1, 0.34, 0.5, 0.6, 0.75, 1.0))
        doc = _export(spans, accounts, min_share=share)
        flags = _flags(doc)
        first = flags.index(True) if True in flags else len(flags)
        assert flags == [False] * first + [True] * (len(flags) - first)
        begun = [sum(1 for f, _ in spans.values() if f <= m) for m in MONTHS]
        assert flags == [bool(spans) and Decimal(str(share)) * len(spans) <= n for n in begun]
        # fully covered: some account has history and every one with history holds the month in its window; those
        # with none are left out
        assert [m["full_coverage"] for m in doc["months"]] == [
            bool(spans) and all(f <= m <= last for f, last in spans.values()) for m in MONTHS]


def test_a_partial_newest_month_stays_in_coverage():
    """From the first row of a new month until the other accounts post in it, the month is thin, never a gap."""
    accounts = _ids(3)
    spans = {accounts[0]: ("2030-01", "2030-06"), accounts[1]: ("2030-01", "2030-05"),
             accounts[2]: ("2030-01", "2030-05")}                   # only one of the three has a June row yet
    doc = _export(spans, accounts)
    assert _covered(doc) == [3, 3, 3, 3, 3, 1]
    assert _flags(doc) == [True] * 6 and doc["coverage_floor"] == "2030-01"
    assert [m["full_coverage"] for m in doc["months"]] == [True] * 5 + [False]   # the chart still marks June thin


def test_a_quiet_account_pulls_nothing_out_of_coverage():
    """Two accounts at the default share, the second beginning in February and quiet for the last three months: 1 of 2
    report then, yet both have begun, so every month from February on stays in coverage."""
    accounts = _ids(2)
    spans = {accounts[0]: ("2030-01", "2030-06"), accounts[1]: ("2030-02", "2030-03")}   # no row April to June
    doc = _export(spans, accounts, min_share=DEFAULT_COVERAGE_MIN_SHARE)
    assert _covered(doc) == [1, 2, 2, 1, 1, 1]
    assert _flags(doc) == [False] + [True] * 5 and doc["coverage_floor"] == "2030-02"


def test_an_account_with_no_transaction_is_in_neither_the_count_nor_the_whole():
    accounts = _ids(8)
    spans = {accounts[0]: ("2030-01", "2030-06"), accounts[1]: ("2030-03", "2030-06")}   # six have no row at all
    doc = _export(spans, accounts)
    assert _covered(doc) == [1, 1, 2, 2, 2, 2]
    # of the two with history, one began in January (half, below 0.6) and both by March
    assert _flags(doc) == [False, False, True, True, True, True]
    assert doc["coverage_floor"] == "2030-03"
    assert doc["cashflow_accounts"] == accounts                       # still listed as cash-flow accounts
    # nor in full coverage: from March both accounts with history cover every month
    assert [m["full_coverage"] for m in doc["months"]] == [False, False, True, True, True, True]


def test_an_account_with_no_transaction_does_not_make_months_incomplete():
    """A cash-flow account that never transacts (a certificate that only reports a balance) is left out of
    full_coverage as it is out of in_coverage: every month the accounts with history all cover is fully covered."""
    accounts = _ids(3)
    spans = {accounts[0]: ("2030-01", "2030-06"), accounts[1]: ("2030-01", "2030-06")}   # the third has no row
    doc = _export(spans, accounts)
    assert _covered(doc) == [2] * 6
    assert [m["full_coverage"] for m in doc["months"]] == [True] * 6
    assert doc["cashflow_accounts"] == accounts                       # still listed as a cash-flow account


def test_an_account_whose_history_has_not_begun_still_holds_earlier_months_back():
    """An account with history counts in every month, the ones before its first row included; a never-transacting
    account beside it changes nothing."""
    accounts = _ids(3)
    spans = {accounts[0]: ("2030-01", "2030-06"), accounts[1]: ("2030-04", "2030-06")}   # the third has no row
    doc = _export(spans, accounts)
    assert _covered(doc) == [1, 1, 1, 2, 2, 2]
    assert [m["full_coverage"] for m in doc["months"]] == [False] * 3 + [True] * 3


def test_accounts_that_all_have_no_transaction_leave_every_flag_false_and_the_floor_null():
    doc = _export({}, _ids(3))
    assert len(doc["months"]) == 6 and _covered(doc) == [0] * 6
    assert _flags(doc) == [False] * 6
    assert doc["coverage_floor"] is None


def test_months_are_not_fully_covered_while_no_cash_flow_account_has_a_transaction():
    """A checking account whose feed has not delivered a row yet, beside a brokerage paying dividends: the dividends
    make months, but no cash-flow account covers any of them, so none is fully covered — the data is still to come."""
    brokerage = "demo_brokerage_0000"
    rows = [make_txn(date=f"{m}-15", account=brokerage, cents=2500, type="dividend") for m in MONTHS]
    doc = exports.export_cashflow_monthly(rows, _windows({brokerage: (MONTHS[0], MONTHS[-1])}),
                                          ["demo_checking_0000"], min_share=0.6)
    assert [m["month"] for m in doc["months"]] == MONTHS
    assert [m["income"] for m in doc["months"]] == [25.0] * 6           # the control: the dividends are the months
    assert _covered(doc) == [0] * 6
    assert [m["full_coverage"] for m in doc["months"]] == [False] * 6
    assert _flags(doc) == [False] * 6 and doc["coverage_floor"] is None


def test_a_household_with_no_cash_flow_account_reads_fully_covered():
    """A brokerage-only household has no cash-flow account to wait for: every month is fully covered."""
    brokerage = "demo_brokerage_0000"
    rows = [make_txn(date=f"{m}-15", account=brokerage, cents=2500, type="dividend") for m in MONTHS]
    doc = exports.export_cashflow_monthly(rows, _windows({brokerage: (MONTHS[0], MONTHS[-1])}), [], min_share=0.6)
    assert doc["cashflow_accounts"] == [] and _covered(doc) == [0] * 6
    assert [m["full_coverage"] for m in doc["months"]] == [True] * 6


def test_a_share_of_one_counts_from_the_month_the_last_account_began():
    accounts = _ids(8)
    joins = ["2030-01", "2030-02", "2030-03", "2030-03", "2030-04", "2030-04", "2030-04", "2030-04"]
    doc = _export({a: (j, "2030-06") for a, j in zip(accounts, joins, strict=True)}, accounts, min_share=1.0)
    assert _flags(doc) == [m["full_coverage"] for m in doc["months"]] == [False, False, False, True, True, True]
    assert doc["coverage_floor"] == "2030-04"


def test_a_share_of_one_differs_from_full_coverage_when_an_account_stops_before_the_end():
    # every account has begun by April, but one account's window ends in May: June has 7 of 8. full_coverage is a
    # per-month fact the chart reads (June is thin); in_coverage counts the accounts begun, so June stays in
    accounts = _ids(8)
    joins = ["2030-01", "2030-02", "2030-03", "2030-03", "2030-04", "2030-04", "2030-04", "2030-04"]
    spans = {a: (j, "2030-06") for a, j in zip(accounts, joins, strict=True)}
    spans[accounts[0]] = ("2030-01", "2030-05")
    doc = _export(spans, accounts, min_share=1.0)
    assert [m["full_coverage"] for m in doc["months"]] == [False, False, False, True, True, False]
    assert _flags(doc) == [False, False, False, True, True, True]
    assert doc["coverage_floor"] == "2030-04"


@pytest.mark.parametrize("share, count", [(0.7, 10), (0.28, 25), (0.56, 25)])
def test_the_share_is_compared_exactly_as_written(share, count):
    # share x count is a whole number of accounts; that many begun meet the share and one fewer does not. For 0.28 and
    # 0.56 of 25 the binary float product overshoots (7.000000000000001, 14.000000000000002), so a float comparison
    # would refuse exactly the share the config states. Every account has history; the rest begin in June.
    needed = int(Decimal(str(share)) * count)
    assert Decimal(str(share)) * count == needed
    accounts = _ids(count)

    def spans(begun: int) -> dict[str, tuple[str, str]]:
        return {a: ("2030-01" if i < begun else "2030-06", "2030-06") for i, a in enumerate(accounts)}

    at = _export(spans(needed), accounts, min_share=share)
    assert _covered(at) == [needed] * 5 + [count]
    assert _flags(at) == [True] * 6
    assert at["coverage_floor"] == "2030-01"
    below = _export(spans(needed - 1), accounts, min_share=share)
    assert _flags(below) == [False] * 5 + [True] and below["coverage_floor"] == "2030-06"


def test_the_float_product_really_overshoots_for_the_exactness_cases():
    assert 0.28 * 25 > 7 and 0.56 * 25 > 14


def test_no_cash_flow_accounts_means_no_month_is_in_coverage():
    doc = _export({}, [])
    assert doc["cashflow_accounts"] == []
    assert len(doc["months"]) == 6
    assert _flags(doc) == [False] * 6
    assert doc["coverage_floor"] is None


def test_every_cash_flow_account_of_the_demo_transacts_so_its_months_read_as_before(demo_exports):
    """The demo's certificate posts monthly interest, so every cash-flow account of the household has a row: leaving out
    an account with none moves no demo month, and a month is fully covered exactly when every listed account covers it."""
    doc = json.loads((demo_exports.exports / "cashflow_monthly.json").read_text())
    starts = {a["id"]: a["txn_data_start"]
              for a in json.loads((demo_exports.exports / "accounts.json").read_text())["accounts"]}
    listed = doc["cashflow_accounts"]
    assert hh.CERTIFICATE in listed and all(starts[a] for a in listed)
    assert [m["full_coverage"] for m in doc["months"]] == [len(m["accounts_covered"]) == len(listed)
                                                           for m in doc["months"]]
    assert {m["full_coverage"] for m in doc["months"]} == {True, False}     # the control: both kinds of month occur


# ------------------------------------------------------------------ rows dated after the ingest day

def _ahead(today: str, spans: dict[str, tuple[str, str]], accounts: list[str], *, min_share: float = 0.6) -> dict:
    """_export's household ingested on `today`"""
    rows = [make_txn(date=f"{m}-15", account="demo_checking_0000", cents=-1000) for m in MONTHS]
    return exports.export_cashflow_monthly(rows, _windows(spans), accounts, min_share=min_share, today=today)


def test_an_account_whose_first_row_is_dated_after_the_ingest_day_has_not_begun():
    """Two accounts at the default share, the second's first row (2030-04-03) dated after the ingest day: it holds
    every month back, those after the ingest day too, so no month is in coverage and the floor is never April. Without
    the ingest day April would be the floor."""
    accounts = _ids(2)
    spans = {accounts[0]: ("2030-01", "2030-06"), accounts[1]: ("2030-04", "2030-06")}
    assert _export(spans, accounts)["coverage_floor"] == "2030-04"                 # the control: no ingest day
    doc = _ahead("2030-03-20", spans, accounts)
    assert _flags(doc) == [False] * 6 and doc["coverage_floor"] is None


def test_an_account_begins_on_the_day_its_first_row_is_dated():
    """The ingest day before the first row's day in the same month keeps the account out; on that day it is in"""
    accounts = _ids(2)
    spans = {accounts[0]: ("2030-01", "2030-06"), accounts[1]: ("2030-04", "2030-06")}     # first rows on the 3rd
    assert _ahead("2030-04-02", spans, accounts)["coverage_floor"] is None
    doc = _ahead("2030-04-03", spans, accounts)
    assert _flags(doc) == [False, False, False, True, True, True] and doc["coverage_floor"] == "2030-04"


def test_an_ingest_day_after_every_first_row_changes_nothing():
    accounts = _ids(8)
    joins = ["2030-01", "2030-02", "2030-03", "2030-03", "2030-04", "2030-05", "2030-05", "2030-06"]
    spans = {a: (j, "2030-06") for a, j in zip(accounts, joins, strict=True)}
    for today in ("2030-06-03", "2030-06-30", "2031-01-01"):
        assert _ahead(today, spans, accounts) == _export(spans, accounts)


def test_whatever_the_ingest_day_the_floor_is_never_a_month_after_it():
    """Random windows and ingest days: the floor is null or a month on or before the ingest day's, the flags stay a
    suffix, and a month is in coverage exactly when the share of the accounts with history whose first row is dated by
    both that month and the ingest day meets the share."""
    rng = random.Random(20300102)
    for _ in range(300):
        accounts = _ids(rng.randint(1, 9))
        spans = {}
        for a in accounts:
            if rng.random() < 0.8:
                first = rng.randrange(len(MONTHS))
                spans[a] = (MONTHS[first], MONTHS[rng.randrange(first, len(MONTHS))])
        today = f"{rng.choice(MONTHS)}-{rng.choice(('01', '03', '15', '28'))}"
        share = rng.choice((0.1, 0.34, 0.5, 0.6, 0.75, 1.0))
        doc = _ahead(today, spans, accounts, min_share=share)
        flags = _flags(doc)
        first = flags.index(True) if True in flags else len(flags)
        assert flags == [False] * first + [True] * (len(flags) - first)
        assert doc["coverage_floor"] is None or doc["coverage_floor"] <= today[:7]
        begun = [sum(1 for f, _ in spans.values() if f <= m and f"{f}-03" <= today) for m in MONTHS]
        assert flags == [bool(spans) and Decimal(str(share)) * len(spans) <= n for n in begun]


def test_no_months_means_no_floor():
    doc = _export({a: ("2030-01", "2030-06") for a in _ids(3)}, _ids(3), months=[])
    assert doc == {"months": [], "cashflow_accounts": _ids(3), "coverage_floor": None}


def test_the_export_shape_adds_the_two_keys_and_nothing_else():
    accounts = _ids(2)
    doc = _export({a: ("2030-01", "2030-06") for a in accounts}, accounts)
    assert list(doc) == ["months", "cashflow_accounts", "coverage_floor"]
    assert list(doc["months"][0]) == ["month", "income", "spend", "invested", "savings_rate", "by_category",
                                      "income_by_subcategory", "accounts_covered", "full_coverage", "in_coverage"]


# ------------------------------------------------------------------ the config key

_CHECKING = {"name": "Sample Checking", "institution": "Sample Bank", "owner": "Alex", "kind": "checking"}


def _config(tmp_path: Path, assumptions: dict | None = None) -> Config:
    d = tmp_path / "cfg"
    d.mkdir(exist_ok=True)
    (d / "accounts.yaml").write_text(yaml.safe_dump(
        {"source_dir": "src", "accounts": {"demo_checking_0000": _CHECKING}}))
    write_household(d)
    if assumptions is not None:
        (d / "assumptions.yaml").write_text(yaml.safe_dump(assumptions))
    (tmp_path / "src").mkdir(exist_ok=True)
    return Config(d, source_dir=str(tmp_path / "src"))


@pytest.mark.parametrize("assumptions", [None, {}, {"coverage": None}, {"coverage": {}},
                                         {"coverage": {"min_share": None}}],
                         ids=["no-file", "no-block", "null-block", "empty-block", "null-key"])
def test_the_default_share_is_six_tenths_when_the_block_or_the_key_is_absent(tmp_path, assumptions):
    assert DEFAULT_COVERAGE_MIN_SHARE == 0.6
    assert _config(tmp_path, assumptions).coverage_min_share == 0.6


@pytest.mark.parametrize("value", [0.5, 0.33, 1, 1.0, 0.0001])
def test_a_share_above_zero_and_at_most_one_loads_as_a_float(tmp_path, value):
    share = _config(tmp_path, {"coverage": {"min_share": value}}).coverage_min_share
    assert share == value and isinstance(share, float)


_REFUSED = ("assumptions.yaml: coverage.min_share: expected a number above 0 and at most 1 — write a share of the "
            "cash-flow accounts such as 0.6, or leave the key out (the default is 0.6)")


@pytest.mark.parametrize("value", [0, 0.0, -0.5, 1.01, 2, True, False, "0.6", "most", [0.6], float("nan")],
                         ids=["zero", "zero-float", "negative", "just-above-one", "two", "true", "false", "text",
                              "word", "list", "nan"])
def test_a_share_outside_the_range_or_not_a_number_is_refused_at_load(tmp_path, value):
    with pytest.raises(ValueError) as e:
        _config(tmp_path, {"coverage": {"min_share": value}})
    assert str(e.value) == _REFUSED


def test_a_coverage_block_that_is_not_a_mapping_is_refused_at_load(tmp_path):
    with pytest.raises(ValueError) as e:
        _config(tmp_path, {"coverage": 0.6})
    assert str(e.value) == ("assumptions.yaml: coverage: expected a mapping — write `coverage: {min_share: 0.6}`, "
                            "or leave the block out")


def test_the_demo_config_states_a_share_that_loads():
    """The demo's assumptions.yaml is the starter's with the household's additions over it, and it keeps the
    starter's share; the loaded config reads that share."""
    stated = load_demo("assumptions.yaml")["coverage"]["min_share"]
    starter = yaml.safe_load((ROOT / "starter" / "assumptions.yaml").read_text("utf-8"))["coverage"]["min_share"]
    share = demo_config().coverage_min_share
    assert share == stated == starter and 0 < share <= 1


def test_the_coverage_block_is_not_exported_in_the_assumptions(tmp_path):
    cfg = _config(tmp_path, {"coverage": {"min_share": 0.5}, "match_window_bdays": 3})
    p = isolated_pipeline(tmp_path, [], cfg=cfg, windows={})
    p._export_assumptions(tmp_path / "out")
    doc = json.loads((tmp_path / "out" / "assumptions.json").read_text())
    assert "coverage" not in doc and "coverage" not in doc["thresholds"]
    assert doc["thresholds"]["match_window_bdays"] == 3                # the control: the file was written from cfg


# ------------------------------------------------------------------ the pipeline passes the share

_FLOOR_ACCOUNTS = {f"demo_checking_{i:04d}": {**_CHECKING, "name": f"Sample Checking {i}"} for i in range(4)}
_FLOOR_WINDOWS = {"demo_checking_0000": ("2030-01-03", "2030-03-27"), "demo_checking_0001": ("2030-02-03", "2030-03-27"),
                  "demo_checking_0002": ("2030-03-03", "2030-03-27"), "demo_checking_0003": ("2030-03-03", "2030-03-27")}


def _pipeline_floor(tmp_path: Path, share: float) -> str | None:
    """The floor the pipeline exports for the four accounts of _FLOOR_WINDOWS at `share`, at the clock FF_NOW pins"""
    rows = [make_txn(date=f"2030-0{m}-15", account="demo_checking_0000", cents=-1000) for m in (1, 2, 3)]
    d = tmp_path / str(share)
    cfg_dir = d / "cfg"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "accounts.yaml").write_text(yaml.safe_dump({"source_dir": "src", "accounts": _FLOOR_ACCOUNTS}))
    write_household(cfg_dir)
    (cfg_dir / "assumptions.yaml").write_text(yaml.safe_dump({"coverage": {"min_share": share}}))
    (d / "src").mkdir()
    p = isolated_pipeline(d, rows, cfg=Config(cfg_dir, source_dir=str(d / "src")), windows=_FLOOR_WINDOWS)
    p._export_ledger(d / "out")
    return json.loads((d / "out" / "cashflow_monthly.json").read_text())["coverage_floor"]


def test_the_pipeline_exports_the_floor_at_the_configured_share(tmp_path, monkeypatch):
    monkeypatch.setenv("FF_NOW", "2030-04-01T12:00:00")              # every row is dated by the ingest day
    # 1, 2 and 4 of the 4 accounts report in January, February and March
    assert tuple(_pipeline_floor(tmp_path, s) for s in (0.25, 0.5, 0.75)) == ("2030-01", "2030-02", "2030-03")


def test_the_pipeline_bounds_the_floor_by_its_ingest_day(tmp_path, monkeypatch):
    """Ingested on 2030-02-15: the two accounts whose first rows are dated in March have not begun, so three of the
    four accounts are never reached and the floor is null, never March"""
    monkeypatch.setenv("FF_NOW", "2030-02-15T12:00:00")
    assert tuple(_pipeline_floor(tmp_path, s) for s in (0.25, 0.5, 0.75)) == ("2030-01", "2030-02", None)

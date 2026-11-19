# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Pipeline.parse_transactions and Config.txn_sources over a synthetic source folder: overlapping re-exports of one
account are stored once and still reconcile, an account's window is its first and last parsed dates, a glob that
matches nothing warns, a parser-less feed account has no file sources, and a feed cutover clips an account's file rows
once the feed reports the account. At the cutover seam (parse_simplefin) a gap and an overlap are warned. One card
account on the chase_card parser (its real column layout) beside one feed-only account; invented rows dated 2030.

A file its account's format cannot read (the format refuses it, it is over the size cap or not a plain file, or none
of its lines is a row while some are unreadable) fails one check named for the account and the file and adds nothing,
while every other file loads; a file with no bytes is no failure in any native format; a file balance is booked for a
checking or savings account only; the files no account's glob matches are counted in one info check, a folder that
cannot be listed passed over; and the format an account names is looked up in the pipeline's table when a file is
read, so a test's patch of it is what is read."""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml
from conftest import ROOT, isolated_pipeline, write_household

from etl import pipeline
from etl.config import Config, Source
from etl.importers import registry
from etl.models import FileStats, Txn

CARD, FEED = "demo_card_0000", "demo_feed_0000"
GLOB = "SampleCard_Activity_*.csv"
ACCOUNTS = {
    CARD: {"name": "Sample Card", "institution": "Sample Bank", "owner": "Alex", "kind": "credit-card",
           "parser": "chase_card", "glob": GLOB},
    FEED: {"name": "Sample Feed Checking", "institution": "Sample Bank", "owner": "Sam", "kind": "checking",
           "simplefin_id": "ACT-demo-feed"},
}
HEADER = "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"


def _config(tmp_path: Path) -> Config:
    """The synthetic registry; its feed store sits inside tmp_path and is empty, so no cutover clips a row."""
    d = tmp_path / "cfg"
    d.mkdir()
    (tmp_path / "src").mkdir()
    doc = {"source_dir": "src", "simplefin": {"store_dir": "simplefin"}, "accounts": ACCOUNTS}
    (d / "accounts.yaml").write_text(yaml.safe_dump(doc))
    write_household(d)
    return Config(d, source_dir=str(tmp_path / "src"))


def _export(tmp_path: Path, name: str, rows: list[tuple[str, str, str]]) -> None:
    """One card export: (MM/DD/YYYY, description, amount in dollars) per row."""
    body = "".join(f"{date},{date},{desc},Shopping,Sale,{amount},\n" for date, desc, amount in rows)
    (tmp_path / "src" / name).write_text(HEADER + body)


def _parsed(tmp_path: Path, cfg: Config):
    p = isolated_pipeline(tmp_path / "state", [], cfg=cfg, windows={})
    p.parse_transactions()
    return p


def test_overlapping_re_exports_are_stored_once_and_reconcile(tmp_path):
    cfg = _config(tmp_path)
    _export(tmp_path, "SampleCard_Activity_20300131.csv",
            [("01/04/2030", "SAMPLE GROCER", "-40.00"), ("01/20/2030", "SAMPLE BOOKSHOP", "-15.00")])
    _export(tmp_path, "SampleCard_Activity_20300228.csv",     # repeats the first file's last row
            [("01/20/2030", "SAMPLE BOOKSHOP", "-15.00"), ("02/09/2030", "SAMPLE CAFE", "-5.00")])
    p = _parsed(tmp_path, cfg)
    stored = [t for t in p.txns if t.account_id == CARD]
    assert sorted((t.date, t.description_raw) for t in stored) == [
        ("2030-01-04", "SAMPLE GROCER"), ("2030-01-20", "SAMPLE BOOKSHOP"), ("2030-02-09", "SAMPLE CAFE")]
    assert len({t.uid for t in stored}) == 3
    assert p.overlaps == {"SampleCard_Activity_20300228.csv": 1}        # the repeat, counted on the later file
    assert (p.acct_file_sums[CARD], p.acct_dedup_sums[CARD]) == (-75_00, -15_00)
    # the bookkeeping the per-account reconciliation check reads: files net of dedup equal the stored rows
    assert p.acct_file_sums[CARD] - p.acct_dedup_sums[CARD] == sum(t.amount_cents for t in stored) == -60_00


def test_windows_come_from_the_parsed_rows(tmp_path):
    cfg = _config(tmp_path)
    _export(tmp_path, "SampleCard_Activity_20300331.csv",
            [("03/11/2030", "SAMPLE GROCER", "-20.00"), ("03/02/2030", "SAMPLE CAFE", "-5.00"),
             ("03/20/2030", "SAMPLE BOOKSHOP", "-10.00")])
    p = _parsed(tmp_path, cfg)
    assert p.windows[CARD] == ("2030-03-02", "2030-03-20")
    assert p.windows[FEED] == (None, None)


def test_a_glob_that_matches_nothing_warns_and_leaves_no_window(tmp_path):
    cfg = _config(tmp_path)
    (tmp_path / "src" / "SampleBank_Statement_2030.csv").write_text(HEADER)    # matches no account's glob
    p = _parsed(tmp_path, cfg)
    warned = [c for c in p.checks if c.name == f"source.{CARD}"]
    assert [c.status for c in warned] == ["warn"] and GLOB in warned[0].detail
    assert p.windows[CARD] == (None, None)
    assert p.txns == [] and p.file_stats == []


def test_a_parser_less_account_has_no_file_sources(tmp_path):
    cfg = _config(tmp_path)
    assert cfg.txn_sources(FEED) == []
    assert cfg.txn_sources(CARD) == [Source("chase_card", GLOB, None)]
    _export(tmp_path, "SampleCard_Activity_20300430.csv", [("04/15/2030", "SAMPLE GROCER", "-30.00")])
    p = _parsed(tmp_path, cfg)
    assert [s.source_file for s in p.file_stats] == ["SampleCard_Activity_20300430.csv"]
    assert {t.account_id for t in p.txns} == {CARD}
    assert FEED not in p.acct_file_sums and p.windows[FEED] == (None, None)
    assert not [c for c in p.checks if c.name.endswith(FEED)]


CLIP, CLIP_GLOB, CUTOVER, CLIP_FEED = "demo_clip_card_0000", "SampleClip_Activity_*.csv", "2030-02-01", "ACT-demo-clip"


def _noon(day: str) -> int:
    """The epoch of noon UTC on `day`: the same date in the household's zone."""
    return int(dt.datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp())


def _clip_config(tmp_path: Path, *, feed_rows: list[str] | None = None, balance: bool = True) -> Config:
    """One card read from statement files and from the feed, its cutover CUTOVER. With `feed_rows` (or `balance`) the
    store holds one snapshot reporting the account: a transaction posted on each of those days, and a balance."""
    d = tmp_path / "cfg"
    d.mkdir()
    (tmp_path / "src").mkdir()
    accounts = {CLIP: {"name": "Sample Clip Card", "institution": "Sample Bank", "owner": "Alex",
                       "kind": "credit-card", "parser": "chase_card", "glob": CLIP_GLOB,
                       "simplefin_id": CLIP_FEED, "simplefin_cutover": CUTOVER}}
    doc = {"source_dir": "src", "simplefin": {"store_dir": "simplefin"}, "accounts": accounts}
    (d / "accounts.yaml").write_text(yaml.safe_dump(doc))
    write_household(d)
    snapshots = tmp_path / "simplefin" / "snapshots"
    snapshots.mkdir(parents=True)
    if feed_rows is not None or balance:
        entry = {"id": CLIP_FEED, "name": "Sample Clip Card", "currency": "USD",
                 "transactions": [{"id": f"t{i}", "posted": _noon(day), "amount": "-1.00", "description": "SAMPLE SHOP"}
                                  for i, day in enumerate(feed_rows or [])]}
        if balance:
            entry.update({"balance": "-100.00", "balance-date": _noon("2030-02-28")})
        (snapshots / "accounts-20300301T120000Z.json").write_text(json.dumps({"accounts": [entry]}))
    return Config(d, source_dir=str(tmp_path / "src"))


def _clip_rows(tmp_path: Path, *days: str) -> None:
    """One statement export of the card: a row on each MM/DD/2030 day."""
    _export(tmp_path, "SampleClip_Activity_20300228.csv", [(f"{day}/2030", f"SAMPLE ROW {i}", "-10.00")
                                                           for i, day in enumerate(days)])


def _seam(tmp_path: Path, cfg: Config):
    """The pipeline after both parse stages, and its checks at the cutover seam by name."""
    p = isolated_pipeline(tmp_path / "state", [], cfg=cfg, windows={})
    p.parse_transactions()
    p.parse_simplefin()
    names = ("simplefin.csv_clip", "simplefin.cutover_gap", "simplefin.cutover_overlap")
    return p, {c.name.rsplit(".", 1)[0]: c for c in p.checks if c.name.rsplit(".", 1)[0] in names}


def test_a_feed_cutover_clips_file_rows_from_the_cutover_day_on(tmp_path):
    """A file-sourced account that also has a feed id and a cutover, whose feed has reported it: its file rows dated on
    or after the cutover are dropped before storing (the feed owns that window), booked as removed so the
    reconciliation still balances, and counted in one info check."""
    cfg = _clip_config(tmp_path)          # the snapshot reports the account's balance: the feed has taken over
    _export(tmp_path, "SampleClip_Activity_20300228.csv",
            [("01/15/2030", "SAMPLE GROCER", "-10.00"), ("01/31/2030", "SAMPLE CAFE", "-20.00"),
             ("02/01/2030", "SAMPLE BOOKSHOP", "-30.00"), ("02/10/2030", "SAMPLE PHARMACY", "-40.00")])
    p = _parsed(tmp_path, cfg)
    stored = [t for t in p.txns if t.account_id == CLIP]
    # the cutover day itself belongs to the feed: only the rows dated before it are stored
    assert sorted((t.date, t.description_raw) for t in stored) == [
        ("2030-01-15", "SAMPLE GROCER"), ("2030-01-31", "SAMPLE CAFE")]
    assert p.windows[CLIP] == ("2030-01-15", "2030-01-31")
    assert p.overlaps == {}
    # the dropped rows are booked as removed before store: the files net of removals equal the stored rows
    assert (p.acct_file_sums[CLIP], p.acct_dedup_sums[CLIP]) == (-100_00, -70_00)
    assert p.acct_file_sums[CLIP] - p.acct_dedup_sums[CLIP] == sum(t.amount_cents for t in stored) == -30_00
    clip = [c for c in p.checks if c.name == f"simplefin.csv_clip.{CLIP}"]
    assert [(c.status, c.detail) for c in clip] == [
        ("info", f"2 file rows on/after cutover {CUTOVER} dropped (SimpleFIN owns that window)")]
    assert p.file_clips == {CLIP: (2, "2030-02-10")}


@pytest.mark.parametrize("store", ["empty", "other account"])
def test_file_rows_are_kept_while_the_feed_has_nothing_for_the_account(tmp_path, store):
    """With a feed id and a cutover but no snapshot reporting the account (none at all, or one reporting another
    account), the file rows after the cutover are all the data there is: they are kept, and an info note says so."""
    cfg = _clip_config(tmp_path, balance=False)
    if store == "other account":
        (tmp_path / "simplefin" / "snapshots" / "accounts-20300301T120000Z.json").write_text(json.dumps(
            {"accounts": [{"id": "ACT-demo-other", "currency": "USD", "balance": "5.00",
                           "balance-date": _noon("2030-02-28")}]}))
    _clip_rows(tmp_path, "01/15", "02/01", "02/10")
    p = _parsed(tmp_path, cfg)
    assert sorted(t.date for t in p.txns) == ["2030-01-15", "2030-02-01", "2030-02-10"]
    assert p.acct_dedup_sums.get(CLIP, 0) == 0 and p.file_clips == {}
    [note] = [c for c in p.checks if c.name == f"simplefin.csv_clip.{CLIP}"]
    assert (note.status, note.detail) == (
        "info", f"nothing clipped: 2 file row(s) on/after cutover {CUTOVER} kept, as the feed has no data for this "
                "account yet")


@pytest.mark.parametrize("first_feed_row, gap", [(CUTOVER, None), ("2030-02-02", 1), ("2030-02-11", 10)])
def test_the_gap_check_fires_when_the_feed_starts_after_the_cutover(tmp_path, first_feed_row, gap):
    """The files end before the cutover; the feed's first transaction on the cutover day leaves no gap, and each day
    later is a day no source covers."""
    cfg = _clip_config(tmp_path, feed_rows=[first_feed_row, "2030-02-20"])
    _clip_rows(tmp_path, "01/15", "01/31")
    _, seam = _seam(tmp_path, cfg)
    if gap is None:
        assert "simplefin.cutover_gap" not in seam
    else:
        check = seam["simplefin.cutover_gap"]
        assert (check.name, check.status) == (f"simplefin.cutover_gap.{CLIP}", "warn")
        assert check.detail == (f"the feed's earliest transaction is {first_feed_row}, {gap} day(s) after the cutover "
                                f"{CUTOVER}: no source covers the days between")
    assert "simplefin.cutover_overlap" not in seam and "simplefin.csv_clip" not in seam


@pytest.mark.parametrize("feed_rows, overlap", [(["2030-02-20"], True), ([], True), (["2030-02-05"], False),
                                                (["2030-01-25"], False), (["2030-02-10"], False),
                                                (["2030-02-11"], True)])
def test_the_overlap_check_fires_when_dropped_file_rows_have_no_feed_rows_to_replace_them(tmp_path, feed_rows,
                                                                                         overlap):
    """File rows on 02-01 and 02-10, past the 02-01 cutover, are dropped: the feed starting after the files' last row
    (a day later is enough), or not at all (a balance only), replaces nothing — an overlap; a feed row on or before the
    files' last one, or one before the cutover, means the feed carries that window."""
    cfg = _clip_config(tmp_path, feed_rows=feed_rows)
    _clip_rows(tmp_path, "01/15", "02/01", "02/10")
    p, seam = _seam(tmp_path, cfg)
    assert seam["simplefin.csv_clip"].detail.startswith("2 file rows on/after cutover")
    assert [t.date for t in p.txns if t.source_file.endswith(".csv")] == ["2030-01-15"]
    if overlap:
        check = seam["simplefin.cutover_overlap"]
        assert (check.name, check.status) == (f"simplefin.cutover_overlap.{CLIP}", "warn")
        first = feed_rows[0] if feed_rows else "not yet there"
        assert check.detail == (f"2 file row(s) from the cutover {CUTOVER} to 2030-02-10 were dropped, and the feed's "
                                f"earliest transaction is {first}: nothing replaces them")
    else:
        assert "simplefin.cutover_overlap" not in seam


def test_a_clean_seam_raises_neither_check(tmp_path):
    """The files end the day before the cutover and the feed starts on it: nothing is clipped, nothing is missing."""
    cfg = _clip_config(tmp_path, feed_rows=[CUTOVER, "2030-02-12"])
    _clip_rows(tmp_path, "01/10", "01/31")
    p, seam = _seam(tmp_path, cfg)
    assert seam == {}
    assert sorted(t.date for t in p.txns) == ["2030-01-10", "2030-01-31", CUTOVER, "2030-02-12"]


# ------------------------------------------------------------------ the format an account names, and files it cannot read

CHK = "demo_checking_0000"


def _household(tmp_path: Path, accounts: dict, **doc) -> Config:
    """A synthetic registry of `accounts` over the empty source folder tmp_path/src; `doc` adds accounts.yaml keys."""
    d = tmp_path / "cfg"
    d.mkdir()
    (tmp_path / "src").mkdir()
    (d / "accounts.yaml").write_text(yaml.safe_dump(
        {"source_dir": "src", "simplefin": {"store_dir": "simplefin"}, "accounts": accounts, **doc}))
    write_household(d)
    return Config(d, source_dir=str(tmp_path / "src"))


def _account(kind: str, fmt: str, glob: str) -> dict:
    return {"name": f"Sample {kind}", "institution": "Sample Bank", "owner": "Alex", "kind": kind, "parser": fmt,
            "glob": glob}


def _not_read(reason: str, fmt: str) -> str:
    """The detail of a file's failing import check"""
    return (f"Not read: {reason}. Check that it is this account's own export, in the format the account names "
            f"(accounts.yaml parser: {fmt}).")


def _one_row(path: Path, account_id: str) -> tuple[list[Txn], FileStats, dict]:
    """A sample format: one -5.00 row on 2030-01-04 named for the file, whatever the file holds."""
    stats = FileStats(source_file=path.name, parser="sample_format")
    stats.observe("2030-01-04", -5_00)
    return [Txn(date="2030-01-04", account_id=account_id, description_raw=f"SAMPLE {path.stem.upper()}",
                amount_cents=-5_00, source_file=path.name)], stats, {}


def _import_checks(p) -> list[tuple[str, str, str]]:
    return [(c.name, c.status, c.detail) for c in p.checks if c.name.startswith("import.")]


def test_a_patched_format_is_the_one_an_account_reads(tmp_path, monkeypatch):
    """The pipeline looks the format up in TXN_PARSERS when it reads a file, so a patch of a native name is what the
    account's files are read with."""
    monkeypatch.setitem(pipeline.TXN_PARSERS, "chase_card", _one_row)
    cfg = _config(tmp_path)
    _export(tmp_path, "SampleCard_Activity_20300131.csv", [("01/04/2030", "SAMPLE GROCER", "-40.00")])
    p = _parsed(tmp_path, cfg)
    assert [(t.description_raw, t.amount_cents) for t in p.txns] == [("SAMPLE SAMPLECARD_ACTIVITY_20300131", -5_00)]
    assert [(s.source_file, s.parser, s.account) for s in p.file_stats] == [
        ("SampleCard_Activity_20300131.csv", "sample_format", CARD)]


def test_each_file_stat_names_the_account_it_was_read_for(tmp_path):
    cfg = _config(tmp_path)
    _export(tmp_path, "SampleCard_Activity_20300131.csv", [("01/04/2030", "SAMPLE GROCER", "-40.00")])
    _export(tmp_path, "SampleCard_Activity_20300228.csv", [("02/04/2030", "SAMPLE CAFE", "-4.00")])
    p = _parsed(tmp_path, cfg)
    assert [(s.source_file, s.account) for s in p.file_stats] == [
        ("SampleCard_Activity_20300131.csv", CARD), ("SampleCard_Activity_20300228.csv", CARD)]


def test_a_file_balance_is_booked_for_a_checking_or_savings_account_only(tmp_path, monkeypatch):
    """Each file states a balance on the day its name ends with. A checking or savings account books its newest; any
    other kind books none, and says so once however many of its files state one."""
    def balance(path, account_id):
        return [], FileStats(source_file=path.name, parser="sample_format"), {
            "latest_balance": (f"2030-01-{path.stem[-2:]}", 400_00)}
    monkeypatch.setitem(pipeline.TXN_PARSERS, "sample_format", balance)
    kinds = {"demo_checking_0000": "checking", "demo_savings_0000": "savings", "demo_card_0000": "credit-card",
             "demo_brokerage_0000": "brokerage", "demo_loan_0000": "loan"}
    cfg = _household(tmp_path, {a: _account(kind, "sample_format", f"{a}-*.csv") for a, kind in kinds.items()})
    for a in kinds:
        for day in ("10", "11"):
            (tmp_path / "src" / f"{a}-2030-01-{day}.csv").write_text("")
    p = _parsed(tmp_path, cfg)
    p.parse_simplefin()
    assert p.latest_balances == {"demo_checking_0000": ("2030-01-11", 400_00),
                                 "demo_savings_0000": ("2030-01-11", 400_00)}
    assert sorted(b.account_id for b in p.balance_obs) == ["demo_checking_0000", "demo_savings_0000"]
    assert [(c.name, c.status, c.detail) for c in p.checks if c.name.startswith("import.balance_ignored.")] == [
        (f"import.balance_ignored.{a}", "info",
         "This account's statement files state a balance, which is not counted: a balance is read from a file only "
         "for a checking or savings account. This account's balance comes from the bank feed when it has one.")
        for a in ("demo_brokerage_0000", "demo_card_0000", "demo_loan_0000")]        # the registry's order
    assert _import_checks(p) == [(c.name, c.status, c.detail) for c in p.checks
                                 if c.name.startswith("import.balance_ignored.")]       # no file failed


def test_a_file_over_the_size_cap_is_refused_before_it_is_opened(tmp_path, monkeypatch):
    opened = []

    def recorded(path, account_id):
        opened.append(path.name)
        return _one_row(path, account_id)
    monkeypatch.setitem(pipeline.TXN_PARSERS, "sample_format", recorded)
    cfg = _household(tmp_path, {CARD: _account("credit-card", "sample_format", "card-*.csv")})
    (tmp_path / "src" / "card-1.csv").write_text("sample\n")
    for name, size in (("card-2.csv", registry.MAX_FILE_BYTES + 1), ("card-3.csv", registry.MAX_FILE_BYTES)):
        with open(tmp_path / "src" / name, "wb") as f:
            f.truncate(size)                         # a sparse file: its size without its bytes on disk
    p = _parsed(tmp_path, cfg)
    assert opened == ["card-1.csv", "card-3.csv"]    # the cap itself is allowed
    assert _import_checks(p) == [(f"import.{CARD}.card-2.csv", "fail",
                                  _not_read("it is over 50 MB, far more than a statement export holds", "sample_format"))]
    assert [t.source_file for t in p.txns] == ["card-1.csv", "card-3.csv"]
    assert [s.source_file for s in p.file_stats] == ["card-1.csv", "card-3.csv"]
    assert p.acct_file_sums == {CARD: -10_00}


def test_a_format_that_raises_fails_that_file_and_every_other_file_loads(tmp_path, monkeypatch):
    """The words say what the file looked like, never the exception's own text (here an amount and a path)."""
    def fussy(path, account_id):
        if "bad" in path.name:
            raise ValueError(f"unexpected line '01/04/2030,-1,234.56' in {path}")
        return _one_row(path, account_id)
    monkeypatch.setitem(pipeline.TXN_PARSERS, "sample_format", fussy)
    cfg = _household(tmp_path, {CARD: _account("credit-card", "sample_format", "card-*.csv"),
                                CHK: _account("checking", "sample_format", "checking-*.csv")})
    for name in ("card-1.csv", "card-2-bad.csv", "card-3.csv", "checking-1.csv"):
        (tmp_path / "src" / name).write_text("sample\n")
    p = _parsed(tmp_path, cfg)
    [(name, status, detail)] = _import_checks(p)
    assert (name, status) == (f"import.{CARD}.card-2-bad.csv", "fail")
    assert detail == _not_read("a line in it is not laid out the way this format expects", "sample_format")
    assert "234" not in detail and str(tmp_path) not in detail
    assert sorted(t.source_file for t in p.txns) == ["card-1.csv", "card-3.csv", "checking-1.csv"]
    assert [s.source_file for s in p.file_stats] == ["card-1.csv", "card-3.csv", "checking-1.csv"]
    assert p.acct_file_sums == {CARD: -10_00, CHK: -5_00}


def test_a_checking_export_under_the_card_pattern_fails_its_check(tmp_path):
    """The card format reads a header with Transaction Date, Description and Amount; a checking export has none of the
    first, so it is refused by its header instead of loading as no rows, and the card's own export still loads."""
    cfg = _config(tmp_path)
    _export(tmp_path, "SampleCard_Activity_20300131.csv", [("01/04/2030", "SAMPLE GROCER", "-40.00")])
    (tmp_path / "src" / "SampleCard_Activity_20300228.csv").write_text(
        "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
        "DEBIT,02/03/2030,SAMPLE CAFE,-5.00,DEBIT_CARD,4865.00,\n")
    p = _parsed(tmp_path, cfg)
    assert _import_checks(p) == [(f"import.{CARD}.SampleCard_Activity_20300228.csv", "fail", _not_read(
        "its first line is not the header this format starts with", "chase_card"))]
    assert [t.description_raw for t in p.txns] == ["SAMPLE GROCER"]
    assert [s.source_file for s in p.file_stats] == ["SampleCard_Activity_20300131.csv"]
    assert p.acct_file_sums == {CARD: -40_00}


def test_a_file_whose_every_line_is_unreadable_fails_and_adds_nothing(tmp_path):
    """A card export with the card header whose dates are written day.month.year: the card format reads no row of it.
    Its line counts stay in the file stats; its rows, sum and balance are not counted. A file with no bytes, or the
    card header alone, has nothing unreadable and is no failure."""
    cfg = _config(tmp_path)
    _export(tmp_path, "SampleCard_Activity_20300131.csv", [("01/04/2030", "SAMPLE GROCER", "-40.00")])
    _export(tmp_path, "SampleCard_Activity_20300228.csv",
            [("03.02.2030", "SAMPLE CAFE", "-5.00"), ("05.02.2030", "SAMPLE BOOKSHOP", "-15.00")])
    (tmp_path / "src" / "SampleCard_Activity_20300331.csv").write_text(HEADER)
    (tmp_path / "src" / "SampleCard_Activity_20300430.csv").write_text("")
    p = _parsed(tmp_path, cfg)
    assert _import_checks(p) == [(f"import.{CARD}.SampleCard_Activity_20300228.csv", "fail", _not_read(
        "none of its lines has a date and an amount where this format reads them", "chase_card"))]
    assert [t.description_raw for t in p.txns] == ["SAMPLE GROCER"]
    assert {s.source_file: (s.account, s.rows_total, s.rows_parsed, s.skip_reasons, s.sum_cents)
            for s in p.file_stats}["SampleCard_Activity_20300228.csv"] == (CARD, 2, 0, {"unparseable-row": 2}, 0)
    assert p.acct_file_sums == {CARD: -40_00} and p.acct_dedup_sums == {}


def test_a_file_that_fails_books_no_balance(tmp_path, monkeypatch):
    def unreadable(path, account_id):
        stats = FileStats(source_file=path.name, parser="sample_format")
        stats.rows_total = 1
        stats.skip("unparseable-row")
        return [], stats, {"latest_balance": ("2030-01-31", 400_00)}
    monkeypatch.setitem(pipeline.TXN_PARSERS, "sample_format", unreadable)
    cfg = _household(tmp_path, {CHK: _account("checking", "sample_format", "checking-*.csv")})
    (tmp_path / "src" / "checking-1.csv").write_text("sample\n")
    p = _parsed(tmp_path, cfg)
    assert [status for _, status, _ in _import_checks(p)] == ["fail"]
    assert p.latest_balances == {}


def test_a_path_that_is_not_a_plain_file_is_refused_before_it_is_opened(tmp_path, monkeypatch):
    """A pipe (opening one would hold the ingest until something writes to it), a link to one, and a folder, each
    matched by the account's glob."""
    opened = []

    def recorded(path, account_id):
        opened.append(path.name)
        return _one_row(path, account_id)
    monkeypatch.setitem(pipeline.TXN_PARSERS, "sample_format", recorded)
    cfg = _household(tmp_path, {CARD: _account("credit-card", "sample_format", "card-*")})
    src = tmp_path / "src"
    (src / "card-1.csv").write_text("sample\n")
    os.mkfifo(src / "card-2.pipe")
    (src / "card-3.link").symlink_to(src / "card-2.pipe")
    (src / "card-4.dir").mkdir()
    p = _parsed(tmp_path, cfg)
    assert opened == ["card-1.csv"]
    assert _import_checks(p) == [(f"import.{CARD}.{name}", "fail", _not_read(
        "it is a folder, a pipe or a device, not a file", "sample_format"))
        for name in ("card-2.pipe", "card-3.link", "card-4.dir")]
    assert [s.source_file for s in p.file_stats] == ["card-1.csv"]


SAMPLE = "demo_sample_0000"


@pytest.mark.parametrize(("fmt", "kind"), [("chase_checking", "checking"), ("chase_card", "credit-card"),
                                           ("bofa_bank", "savings")])
def test_a_file_with_no_bytes_is_no_failure_in_any_native_format(tmp_path, fmt, kind):
    cfg = _household(tmp_path, {SAMPLE: _account(kind, fmt, "statement-*.csv")})
    (tmp_path / "src" / "statement-2030-01.csv").write_text("")
    p = _parsed(tmp_path, cfg)
    assert (_import_checks(p), p.txns, p.latest_balances) == ([], [], {})
    assert [(s.source_file, s.account, s.rows_total) for s in p.file_stats] == [("statement-2030-01.csv", SAMPLE, 0)]


SAV = "demo_savings_0000"
STATEMENT = """\
Description,,Summary Amt.
Beginning balance as of {first},,"{begin}"
Total credits,,"{credits}"
Total debits,,"0.00"
Ending balance as of {last},,"{end}"

Date,Description,Amount,Running Bal.
{first},"Beginning balance as of {first}",,"{begin}"
{rows}"""
NOT_STATEMENTS = {
    "a checking export": "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
                         "DEBIT,03/03/2030,SAMPLE CAFE,-5.00,DEBIT_CARD,1095.00,\n",
    "a card export": HEADER + "03/03/2030,03/04/2030,SAMPLE CAFE,Food & Drink,Sale,-5.00,\n",
    "an error page": "<!DOCTYPE html>\n<html><head><title>Service unavailable</title></head>\n"
                     "<body><p>Please try again later.</p></body></html>\n",
}


@pytest.mark.parametrize("wrong", NOT_STATEMENTS.values(), ids=NOT_STATEMENTS.keys())
def test_a_file_with_no_header_line_fails_under_the_bank_statement_format(tmp_path, wrong):
    """Under the bank statement format, a file with no `Date,Description,Amount,Running Bal.` line fails its check;
    a month with a transaction and a month with none (its summary and header line only) load."""
    cfg = _household(tmp_path, {SAV: _account("savings", "bofa_bank", "savings-*.csv")})
    src = tmp_path / "src"
    (src / "savings-2030-01.csv").write_text(STATEMENT.format(
        first="01/01/2030", last="01/31/2030", begin="1,000.00", credits="100.00", end="1,100.00",
        rows='01/15/2030,"SAMPLE EMPLOYER PAYROLL",100.00,"1,100.00"\n'))
    (src / "savings-2030-02.csv").write_text(STATEMENT.format(
        first="02/01/2030", last="02/28/2030", begin="1,100.00", credits="0.00", end="1,100.00", rows=""))
    (src / "savings-2030-03.csv").write_text(wrong)
    p = _parsed(tmp_path, cfg)
    assert _import_checks(p) == [(f"import.{SAV}.savings-2030-03.csv", "fail", _not_read(
        "none of its lines is the header line this format reads its rows under", "bofa_bank"))]
    assert [t.description_raw for t in p.txns] == ["SAMPLE EMPLOYER PAYROLL"]
    assert [s.source_file for s in p.file_stats] == ["savings-2030-01.csv", "savings-2030-02.csv"]
    assert p.latest_balances == {SAV: ("2030-02-28", 110_000)}


EXAMPLE = ROOT / "examples" / "minimal"


def test_a_checking_file_with_another_header_fails_its_check_and_the_household_still_loads(tmp_path, monkeypatch):
    """The smallest household plus a card account, and a card export saved under the checking account's file pattern:
    the ingest runs to the end, that one file fails its check, every other file is stored and both accounts reconcile
    for what was read."""
    home = tmp_path / "home"
    shutil.copytree(EXAMPLE, home)
    accounts = home / "config" / "accounts.yaml"
    doc = yaml.safe_load(accounts.read_text("utf-8"))
    doc["accounts"][CARD] = _account("credit-card", "chase_card", f"{CARD}-*.csv")
    accounts.write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")
    (home / "imports" / f"{CARD}-2030-01.csv").write_text(HEADER + "01/12/2030,01/13/2030,SAMPLE BOOKSHOP,Shopping,Sale,"
                                                                   "-15.00,\n")
    bad = f"{CHK}-2030-02.csv"
    (home / "imports" / bad).write_text(HEADER + "02/03/2030,02/04/2030,SAMPLE CAFE,Food & Drink,Sale,-5.00,\n")
    good = f"{CHK}-2025-01.csv"
    good_rows = len((home / "imports" / good).read_text("utf-8").splitlines()) - 1
    monkeypatch.setenv("FF_HOME", str(home))
    monkeypatch.setenv("FF_NOW", "2030-02-15T12:00:00")
    assert pipeline.run() == 0
    out = home / "data" / "exports"
    report = json.loads((out / "quality_report.json").read_text("utf-8"))
    checks = {c["name"]: (c["status"], c["detail"]) for c in report["checks"]}
    assert [name for name, (status, _) in checks.items() if status == "fail"] == [f"import.{CHK}.{bad}"]
    assert checks[f"import.{CHK}.{bad}"][1] == _not_read("its first line is not the header this format starts with",
                                                          "chase_checking")
    assert checks[f"reconcile.{CHK}.store_vs_files"][0] == checks[f"reconcile.{CARD}.store_vs_files"][0] == "pass"
    assert [(f["source_file"], f["account"]) for f in report["files"]] == [(good, CHK), (f"{CARD}-2030-01.csv", CARD)]
    txns = json.loads((out / "transactions.json").read_text("utf-8"))["transactions"]
    assert {t["source_file"] for t in txns} == {good, f"{CARD}-2030-01.csv"}
    assert sum(t["account"] == CHK for t in txns) == good_rows > 0


# ------------------------------------------------------------------ files no account reads

def test_the_files_no_glob_matches_are_counted_in_one_info_check(tmp_path):
    """Counted: the files in the statements folder and in each folder an account's glob looks in, that no glob matched,
    by their path in the folder, five named; not counted: a file a skip pattern names, a hidden file, and a file in a
    folder no glob looks in."""
    cfg = _household(tmp_path, {CARD: _account("credit-card", "chase_card", "cards/SampleCard_*.csv")},
                     skip_files=["*.pdf"])
    src = tmp_path / "src"
    for name in ("cards/SampleCard_2030.csv", "cards/notes.txt", "a-2030.csv", "b-2030.csv", "c-2030.csv",
                 "d-2030.csv", "e-2030.csv", "statement-2030.pdf", ".hidden-notes", "archive/old-2029.csv"):
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_text(HEADER)
    p = _parsed(tmp_path, cfg)
    unmatched = [(c.name, c.status, c.detail) for c in p.checks if c.name == "source.unmatched"]
    assert unmatched == [("source.unmatched", "info",
                          "6 files in the statements folder are read by no account: a-2030.csv, b-2030.csv, c-2030.csv, "
                          "cards/notes.txt, d-2030.csv, and 1 more. A statement file is read once its account's file "
                          "pattern matches it (accounts.yaml glob).")]
    assert [s.source_file for s in p.file_stats] == ["SampleCard_2030.csv"]


def test_one_unmatched_file_is_named_and_none_raises_no_check(tmp_path):
    cfg = _config(tmp_path)
    _export(tmp_path, "SampleCard_Activity_20300131.csv", [("01/04/2030", "SAMPLE GROCER", "-40.00")])
    p = _parsed(tmp_path, cfg)
    assert [c for c in p.checks if c.name == "source.unmatched"] == []
    (tmp_path / "src" / "SampleBank_Statement_2030.csv").write_text(HEADER)
    p = _parsed(tmp_path / "again", cfg)
    assert [(c.status, c.detail) for c in p.checks if c.name == "source.unmatched"] == [
        ("info", "1 file in the statements folder is read by no account: SampleBank_Statement_2030.csv. A statement "
                 "file is read once its account's file pattern matches it (accounts.yaml glob).")]


@contextlib.contextmanager
def _unlistable(monkeypatch, *folders: Path):
    """Each folder at mode 000, its mode restored on the way out so the temp dir can be removed. A test run as root
    lists such a folder anyway, so its listing is also refused here as the system refuses it to any other user. Yields
    the folders the code tried to list."""
    tried: list[Path] = []
    listing = Path.iterdir

    def iterdir(self):
        tried.append(self)
        if self in folders:
            raise PermissionError(13, "Permission denied", str(self))
        return listing(self)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    modes = [(d, d.stat().st_mode) for d in folders]
    try:
        for d, _ in modes:
            d.chmod(0)
        yield tried
    finally:
        for d, mode in modes:
            d.chmod(mode)


def test_a_folder_the_scan_cannot_list_is_passed_over(tmp_path, monkeypatch):
    """A folder an account's glob looks in that cannot be listed (a lost+found, a trash folder at mode 000) and an entry
    that cannot be looked at are passed over; a hidden folder is passed over before it is listed."""
    cfg = _household(tmp_path, {CARD: _account("credit-card", "chase_card", "**/SampleCard_*.csv")})
    src = tmp_path / "src"
    for name in ("cards/SampleCard_2030.csv", "cards/notes.txt", "lost+found/old-2029.csv", ".trash/old-2028.csv",
                 "a-2030.csv", "b-2030.csv"):
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_text(HEADER)
    looking = Path.is_file

    def is_file(self):
        if self.name == "b-2030.csv":
            raise PermissionError(13, "Permission denied", str(self))
        return looking(self)
    monkeypatch.setattr(Path, "is_file", is_file)
    with _unlistable(monkeypatch, src / "lost+found", src / ".trash") as tried:
        p = _parsed(tmp_path, cfg)
    assert [c.detail for c in p.checks if c.name == "source.unmatched"] == [
        "2 files in the statements folder are read by no account: a-2030.csv, cards/notes.txt. A statement file is "
        "read once its account's file pattern matches it (accounts.yaml glob)."]
    assert src / "lost+found" in tried and src / ".trash" not in tried


def test_a_statements_folder_that_cannot_be_listed_leaves_the_ingest_running(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    _export(tmp_path, "SampleCard_Activity_20300131.csv", [("01/04/2030", "SAMPLE GROCER", "-40.00")])
    (tmp_path / "src" / "notes.txt").write_text("sample\n")
    with _unlistable(monkeypatch, tmp_path / "src") as tried:
        p = _parsed(tmp_path, cfg)
    assert tmp_path / "src" in tried
    assert [c for c in p.checks if c.name == "source.unmatched"] == []

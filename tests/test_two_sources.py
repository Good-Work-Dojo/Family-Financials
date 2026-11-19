# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Two or more sources for one account (accounts.yaml `sources:`): an account whose older history is in one format and
whose newer history is in another names each as a source, the second and later ones with the first day they hold
(`since:`). A row of a source dated outside its own days (before its `since`, or on or after the next source's) is
left out, booked as removed so the account still reconciles: each real row is stored once, the row dated on a `since`
day belongs to the later source, and the rows kept keep the uid their format alone gives them. One check per such
account counts, for each source in order, the rows it kept and left out: info, or a warning when rows left out fall
on days the source holding them has no file for. Where two formats date one purchase apart across a `since` (the day
it was made in one, the day it posted in the other) it is kept twice or nowhere, and a seam warning counts the rows
whose amounts suggest it within five days of the `since`. A file no source can read fails its own check while the
other sources load, files two sources' patterns match are read once (for the first) and fail one check for the
account, a source whose pattern matches no file warns, a source starting on or after the feed cutover is refused (and
so is a cutover beside balances only), and the cutover still drops the file rows on or after it once the feed reports
the account. The short form (`parser:` and
`glob:`) raises none of these checks.

Every file here is invented: the samples under tests/importers/samples/ and the files written into tmp_path."""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import pytest
import yaml
from conftest import ROOT, isolated_pipeline, write_household

from etl import pipeline
from etl.config import Config, Source
from etl.importers import ofx
from etl.validate import ConfigError

SAMPLES = ROOT / "tests" / "importers" / "samples"
ACCOUNT = "demo_card_0000"
SINCE = "2030-10-15"
CARD_HEADER = "Posted Date,Reference Number,Payee,Address,Amount\n"
CHASE_HEADER = "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
OFX_HEADER = ("OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\nENCODING:USASCII\nCHARSET:1252\n"
              "COMPRESSION:NONE\nOLDFILEUID:NONE\nNEWFILEUID:NONE\n\n")

# One card's October 2030 in the Bank of America card layout (MM/DD/YYYY, payee, amount; newest first, as the bank
# writes it): two rows before SINCE only this file has, two coffees on one day (their occurrence index tells them
# apart), and from the 4th on the rows the card's OFX download (samples/ofx/card_sgml.qfx) holds as well.
OCTOBER_CSV = [("10/18/2030", "AUTOZONE 0000", "32.99"), ("10/15/2030", "PAYMENT - THANK YOU", "1250.00"),
               ("10/04/2030", "CHIPOTLE 0000", "-18.20"), ("10/02/2030", "STARBUCKS STORE 00000", "-4.00"),
               ("10/02/2030", "STARBUCKS STORE 00000", "-4.00"), ("10/01/2030", "WHOLEFDS SAMPLETOWN 00000", "-40.00")]
TWO_SOURCES = [{"format": "bofa_card", "glob": "card-*.csv"}, {"format": "ofx", "glob": "card-*.qfx", "since": SINCE}]
# what is stored: the CSV's rows before SINCE, the OFX download's from SINCE on
STORED = [("2030-10-01", -40_00, "WHOLEFDS SAMPLETOWN 00000", "card-2030-10.csv"),
          ("2030-10-02", -4_00, "STARBUCKS STORE 00000", "card-2030-10.csv"),
          ("2030-10-02", -4_00, "STARBUCKS STORE 00000", "card-2030-10.csv"),
          ("2030-10-04", -18_20, "CHIPOTLE 0000", "card-2030-10.csv"),
          ("2030-10-15", 1250_00, "PAYMENT - THANK YOU", "card-2030-10.qfx"),
          ("2030-10-18", 32_99, "AUTOZONE 0000 RETURN", "card-2030-10.qfx")]


def rows(n: int) -> str:
    return f"{n} row" if n == 1 else f"{n} rows"


def joined(*counts: tuple[str, int, int], unheld: int = 0) -> str:
    """The words of the account's import.sources check for (format, rows kept, rows left out) per source, and the
    rows left out on days the source holding them has no file for"""
    each = "; ".join(f"{name} kept {rows(kept)} and left out {out}" for name, kept, out in counts)
    head = f"Read from {len(counts)} sources, each holding its own days: {each}."
    if not unheld:
        return f"{head} A row a source holds outside its own days is left out: the source before or after it holds that day."
    return (f"{head} {unheld} of the rows left out {'falls' if unheld == 1 else 'fall'} on days the source holding "
            f"those days has no file for, so no source counts {'it' if unheld == 1 else 'them'}: add the missing "
            "files, or move since (accounts.yaml sources).")


def seam(*found: tuple[str, int, int]) -> str:
    """The words of the account's import.sources_seam check for (start day, rows kept twice, rows kept nowhere)"""
    def counts(day: str, doubles: int, losses: int) -> str:
        parts = [f"{rows(doubles)} that both sources keep may be counted twice" if doubles else "",
                 f"{rows(losses)} that both leave out may be counted nowhere" if losses else ""]
        return f"around {day}, " + " and ".join(p for p in parts if p)
    return ("Where this account's sources meet, the two formats seem to date a purchase differently, the purchase day "
            f"in one and the posting day in the other: {'; '.join(counts(*f) for f in found)}. Look at this account's "
            f"rows around {'that day' if len(found) == 1 else 'those days'}, and move since to a day with no purchase "
            "in flight, or fix a row with an override (accounts.yaml sources, overrides.yaml).")


# ------------------------------------------------------------------ helpers

def _config(tmp_path: Path, account: dict, *, feed: dict | None = None) -> Config:
    """A household of one card read from the statements folder tmp_path/src, `account` its source keys (and any
    other); with `feed` (the snapshot's entry for the account) the feed store holds one snapshot reporting it."""
    d = tmp_path / "cfg"
    d.mkdir(parents=True)
    (tmp_path / "src").mkdir(exist_ok=True)
    meta = {"name": "Sample Card", "institution": "Sample Bank", "owner": "Alex", "kind": "credit-card", **account}
    doc = {"source_dir": "src", "simplefin": {"store_dir": "simplefin"}, "accounts": {ACCOUNT: meta}}
    (d / "accounts.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    write_household(d)
    snapshots = tmp_path / "simplefin" / "snapshots"
    snapshots.mkdir(parents=True)
    if feed is not None:
        (snapshots / "accounts-20301101T120000Z.json").write_text(json.dumps({"accounts": [feed]}))
    return Config(d, source_dir=str(tmp_path / "src"))


def _bofa(folder: Path, name: str, rows: list[tuple[str, str, str]]) -> None:
    """One card export in the Bank of America card layout: (MM/DD/YYYY, payee, amount in dollars) per row"""
    body = "".join(f"{day},{i:023d},{payee},SAMPLETOWN ST,{amount}\n" for i, (day, payee, amount) in enumerate(rows))
    (folder / name).write_text(CARD_HEADER + body)


def _chase(folder: Path, name: str, rows: list[tuple[str, str, str]]) -> None:
    """One card export in the Chase card layout: (MM/DD/YYYY, description, amount in dollars) per row"""
    body = "".join(f"{day},{day},{desc},Shopping,Sale,{amount},\n" for day, desc, amount in rows)
    (folder / name).write_text(CHASE_HEADER + body)


def _ofx(folder: Path, name: str, rows: list[tuple[str, str, str]]) -> None:
    """One card statement downloaded as OFX (SGML): (YYYYMMDD, amount in dollars, name) per transaction"""
    trns = "".join(f"<STMTTRN>\n<TRNTYPE>DEBIT\n<DTPOSTED>{day}\n<TRNAMT>{amount}\n<FITID>S{i:04d}\n<NAME>{payee}\n"
                   "</STMTTRN>\n" for i, (day, amount, payee) in enumerate(rows))
    (folder / name).write_text(
        f"{OFX_HEADER}<OFX>\n<CREDITCARDMSGSRSV1>\n<CCSTMTTRNRS>\n<TRNUID>0\n<CCSTMTRS>\n<CURDEF>USD\n<CCACCTFROM>\n"
        f"<ACCTID>0000123412341234\n</CCACCTFROM>\n<BANKTRANLIST>\n{trns}</BANKTRANLIST>\n<LEDGERBAL>\n"
        "<BALAMT>-100.00\n<DTASOF>20300430\n</LEDGERBAL>\n</CCSTMTRS>\n</CCSTMTTRNRS>\n</CREDITCARDMSGSRSV1>\n</OFX>\n")


def _october(folder: Path) -> None:
    """The card's October 2030 twice: the CSV export and the OFX download (the sample)"""
    _bofa(folder, "card-2030-10.csv", OCTOBER_CSV)
    shutil.copy(SAMPLES / "ofx" / "card_sgml.qfx", folder / "card-2030-10.qfx")


def _parsed(tmp_path: Path, cfg: Config):
    p = isolated_pipeline(tmp_path / "state", [], cfg=cfg, windows={})
    p.parse_transactions()
    return p


def _rows(p) -> list[tuple[str, int, str, str]]:
    return sorted((t.date, t.amount_cents, t.description_raw, t.source_file) for t in p.txns)


def _checks(p, prefix: str) -> list[tuple[str, str]]:
    return [(c.name, c.status) for c in p.checks if c.name.startswith(prefix)]


def _detail(p, name: str) -> str:
    [check] = [c for c in p.checks if c.name == name]
    return check.detail


def _reconciles(p) -> bool:
    """What the account's reconciliation check compares: the files' rows net of every row removed before the store,
    against the rows stored"""
    stored = sum(t.amount_cents for t in p.txns if t.account_id == ACCOUNT)
    return p.acct_file_sums[ACCOUNT] - p.acct_dedup_sums.get(ACCOUNT, 0) == stored


# ------------------------------------------------------------------ the config

def test_the_short_form_is_one_source_and_a_feed_only_account_has_none(tmp_path):
    cfg = _config(tmp_path, {"parser": "bofa_card", "glob": "card-*.csv"})
    assert cfg.txn_sources(ACCOUNT) == [Source("bofa_card", "card-*.csv", None)]
    feed_only = _config(tmp_path / "feed", {"simplefin_id": "ACT-demo-card"})
    assert feed_only.txn_sources(ACCOUNT) == []


def test_each_source_is_read_with_its_start_day_as_iso_text(tmp_path):
    """An unquoted `since` is a date to the YAML loader; the source hands it on as its ISO text"""
    sources = [TWO_SOURCES[0], {**TWO_SOURCES[1], "since": dt.date(2030, 10, 15)}]
    cfg = _config(tmp_path, {"sources": sources})
    assert "since: 2030-10-15\n" in (tmp_path / "cfg" / "accounts.yaml").read_text()      # written unquoted
    assert cfg.txn_sources(ACCOUNT) == [Source("bofa_card", "card-*.csv", None), Source("ofx", "card-*.qfx", SINCE)]
    assert type(cfg.txn_sources(ACCOUNT)[1].since) is str


# ------------------------------------------------------------------ one month in two formats

def test_a_csv_and_an_ofx_of_one_month_store_each_row_once_and_reconcile(tmp_path, monkeypatch):
    """The whole ingest over a household whose card names its CSV exports and its OFX downloads as two sources joined
    at SINCE: each of the month's six real rows is stored once, the account reconciles, and its info check counts what
    each source kept and left out"""
    home = tmp_path / "home"
    write_household(home / "config")
    account = {"name": "Sample Card", "institution": "Sample Bank", "owner": "Alex", "kind": "credit-card",
               "sources": TWO_SOURCES}
    (home / "config" / "accounts.yaml").write_text(yaml.safe_dump({"version": 1, "accounts": {ACCOUNT: account}},
                                                                   sort_keys=False))
    (home / "imports").mkdir()
    _october(home / "imports")
    monkeypatch.setenv("FF_HOME", str(home))
    assert pipeline.run() == 0
    exports = home / "data" / "exports"
    rows = json.loads((exports / "transactions.json").read_text())["transactions"]
    assert sorted((r["date"], round(r["amount"] * 100), r["description"]) for r in rows) == [
        (date, cents, desc) for date, cents, desc, _ in STORED]
    checks = {c["name"]: c for c in json.loads((exports / "quality_report.json").read_text())["checks"]}
    assert checks[f"reconcile.{ACCOUNT}.store_vs_files"]["status"] == "pass"
    sources = checks[f"import.sources.{ACCOUNT}"]
    assert (sources["status"], sources["detail"]) == ("info", joined(("bofa_card", 4, 2), ("ofx", 2, 1)))
    assert not [n for n, c in checks.items() if c["status"] == "fail" and n.startswith(("import.", "reconcile."))]


def test_the_rows_kept_carry_the_uid_their_format_alone_gives_them(tmp_path):
    """The same files read by two households, each naming one of them in the short form, give the uids the two-source
    household keeps: a row's uid is its own file's, whichever source holds its day"""
    def household(name: str, account: dict):
        root = tmp_path / name
        cfg = _config(root, account)
        _october(root / "src")
        return _parsed(root, cfg)

    two = household("two", {"sources": TWO_SOURCES})
    csv_only = household("csv", {"parser": "bofa_card", "glob": "card-*.csv"})
    ofx_only = household("ofx", {"parser": "ofx", "glob": "card-*.qfx"})
    expected = ({t.uid for t in csv_only.txns if t.date < SINCE} | {t.uid for t in ofx_only.txns if t.date >= SINCE})
    assert len(expected) == len(two.txns) == 6
    assert {t.uid for t in two.txns} == expected
    assert sorted(t.seq for t in two.txns) == [0, 0, 0, 0, 0, 1]             # the second coffee of the day
    # neither short-form household raises the check the sources form does
    assert _checks(csv_only, "import.sources.") == _checks(ofx_only, "import.sources.") == []


def test_the_row_dated_on_since_belongs_to_the_later_source(tmp_path):
    cfg = _config(tmp_path, {"sources": TWO_SOURCES})
    _october(tmp_path / "src")
    p = _parsed(tmp_path, cfg)
    assert _rows(p) == STORED
    on_since = [r for r in _rows(p) if r[0] == SINCE]
    assert on_since == [("2030-10-15", 1250_00, "PAYMENT - THANK YOU", "card-2030-10.qfx")]
    assert p.windows[ACCOUNT] == ("2030-10-01", "2030-10-18")
    assert _reconciles(p)
    # the left-out rows' amounts are booked as removed before the store: the CSV's two from SINCE on, the OFX's one
    assert p.acct_dedup_sums[ACCOUNT] == 1250_00 + 32_99 - 18_20
    assert p.overlaps == {}                                                 # none of them is a duplicate
    assert _detail(p, f"import.sources.{ACCOUNT}") == joined(("bofa_card", 4, 2), ("ofx", 2, 1))


def test_three_sources_each_keep_their_own_days(tmp_path):
    """A Chase card export, then the Bank of America card export (the sample, March 2030) from 03-01, then OFX
    downloads from 03-20: each row's day decides its source, the days on a boundary go to the later one"""
    sources = [{"format": "chase_card", "glob": "chase-*.csv"},
               {"format": "bofa_card", "glob": "bofa-*.csv", "since": "2030-03-01"},
               {"format": "ofx", "glob": "card-*.ofx", "since": "2030-03-20"}]
    cfg = _config(tmp_path, {"sources": sources})
    src = tmp_path / "src"
    _chase(src, "chase-2030.csv", [("02/10/2030", "SAMPLE BOOKSHOP", "-12.00"), ("02/25/2030", "SAMPLE PHARMACY",
                                                                                   "-9.50"),
                                   ("03/03/2030", "AMTRAK TICKETS", "-112.00")])
    shutil.copy(SAMPLES / "bofa_card" / "activity.csv", src / "bofa-2030-03.csv")
    _ofx(src, "card-2030-04.ofx", [("20300315", "-32.99", "AUTOZONE 0000"), ("20300320", "-41.07", "SHELL OIL"),
                                   ("20300327", "-6.45", "STARBUCKS STORE 00000"),
                                   ("20300402", "-10.00", "SAMPLE CAFE")])
    p = _parsed(tmp_path, cfg)
    by_file = {}
    for date, _, _, file in _rows(p):
        by_file.setdefault(file, []).append(date)
    assert by_file == {
        "chase-2030.csv": ["2030-02-10", "2030-02-25"],
        "bofa-2030-03.csv": ["2030-03-01", "2030-03-05", "2030-03-10", "2030-03-12", "2030-03-15", "2030-03-18"],
        "card-2030-04.ofx": ["2030-03-20", "2030-03-27", "2030-04-02"]}
    assert _detail(p, f"import.sources.{ACCOUNT}") == joined(("chase_card", 2, 1), ("bofa_card", 6, 6),
                                                              ("ofx", 3, 1))
    assert _reconciles(p)
    assert p.windows[ACCOUNT] == ("2030-02-10", "2030-04-02")


def test_one_source_is_said_as_one(tmp_path):
    cfg = _config(tmp_path, {"sources": [TWO_SOURCES[0]]})
    _october(tmp_path / "src")
    p = _parsed(tmp_path, cfg)
    assert _checks(p, "import.sources") == [(f"import.sources.{ACCOUNT}", "info")]
    assert _detail(p, f"import.sources.{ACCOUNT}") == "Read from one source: bofa_card kept 6 rows."


# ------------------------------------------------------------------ where two sources meet

def _seam_household(tmp_path: Path, *, csv_first: bool, csv: list[tuple[str, str, str]],
                    downloads: list[tuple[str, str, str]]):
    """A card whose Chase CSV exports date a purchase by the day it was made and whose OFX downloads by the day it
    posted, joined at SINCE: the CSV first (the OFX from SINCE on), or the other way round"""
    first, later = (("chase_card", "card-*.csv"), ("ofx", "card-*.ofx")) if csv_first else \
        (("ofx", "card-*.ofx"), ("chase_card", "card-*.csv"))
    cfg = _config(tmp_path, {"sources": [{"format": first[0], "glob": first[1]},
                                         {"format": later[0], "glob": later[1], "since": SINCE}]})
    _chase(tmp_path / "src", "card-2030-10.csv", csv)
    _ofx(tmp_path / "src", "card-2030-10.ofx", downloads)
    return _parsed(tmp_path, cfg)


# both files hold the month: a coffee on 10-08, a purchase on 10-20 (each dated alike in both), and a $55 purchase made
# on 10-14 that posts on 10-16
SEAM_CSV = [("10/08/2030", "SAMPLE CAFE", "-4.00"), ("10/14/2030", "SAMPLE HARDWARE", "-55.00"),
            ("10/20/2030", "SAMPLE BOOKSHOP", "-12.00")]
SEAM_OFX = [("20301008", "-4.00", "SAMPLE CAFE"), ("20301016", "-55.00", "SAMPLE HARDWARE"),
            ("20301020", "-12.00", "SAMPLE BOOKSHOP")]


def test_a_purchase_dated_before_since_by_the_earlier_source_and_after_by_the_later_is_kept_twice_and_warned(
        tmp_path):
    p = _seam_household(tmp_path, csv_first=True, csv=SEAM_CSV, downloads=SEAM_OFX)
    # the join stays a clip by date: both copies are stored, and the household is told
    assert [(r[0], r[1]) for r in _rows(p)] == [("2030-10-08", -4_00), ("2030-10-14", -55_00), ("2030-10-16", -55_00),
                                                ("2030-10-20", -12_00)]
    assert _checks(p, "import.sources") == [(f"import.sources.{ACCOUNT}", "info"),
                                            (f"import.sources_seam.{ACCOUNT}", "warn")]
    assert _detail(p, f"import.sources_seam.{ACCOUNT}") == seam((SINCE, 1, 0))
    assert "55" not in _detail(p, f"import.sources_seam.{ACCOUNT}")         # no amount, no description
    assert _reconciles(p)


def test_the_sources_the_other_way_round_keep_that_purchase_nowhere_and_warn(tmp_path):
    p = _seam_household(tmp_path, csv_first=False, csv=SEAM_CSV, downloads=SEAM_OFX)
    assert [(r[0], r[1]) for r in _rows(p)] == [("2030-10-08", -4_00), ("2030-10-20", -12_00)]
    assert _detail(p, f"import.sources_seam.{ACCOUNT}") == seam((SINCE, 0, 1))
    assert _reconciles(p)


def test_one_start_day_can_show_both_and_each_start_day_is_named(tmp_path):
    """Three sources: around the first start day a purchase both keep and one both leave out, around the second one
    a purchase both keep"""
    sources = [{"format": "chase_card", "glob": "card-*.csv"}, {"format": "ofx", "glob": "card-*.ofx", "since": SINCE},
               {"format": "bofa_card", "glob": "bofa-*.csv", "since": "2030-11-01"}]
    cfg = _config(tmp_path, {"sources": sources})
    _chase(tmp_path / "src", "card-2030-10.csv", [("10/14/2030", "SAMPLE HARDWARE", "-55.00"),
                                                  ("10/16/2030", "SAMPLE GARDEN", "-30.00")])
    _ofx(tmp_path / "src", "card-2030-10.ofx", [("20301016", "-55.00", "SAMPLE HARDWARE"),
                                                ("20301013", "-30.00", "SAMPLE GARDEN"),
                                                ("20301030", "-8.00", "SAMPLE CAFE")])
    _bofa(tmp_path / "src", "bofa-2030-11.csv", [("11/02/2030", "SAMPLE CAFE", "-8.00")])
    p = _parsed(tmp_path, cfg)
    assert _detail(p, f"import.sources_seam.{ACCOUNT}") == seam((SINCE, 1, 1), ("2030-11-01", 1, 0))


@pytest.mark.parametrize("csv_first", [True, False], ids=["csv-first", "ofx-first"])
def test_amounts_on_the_same_day_on_both_sides_are_the_ordinary_overlap(tmp_path, csv_first):
    """Every purchase dated alike in both formats, a few of them on either side of SINCE and two of one amount: no
    warning, whichever source comes first"""
    days = [("10/12/2030", "-55.00"), ("10/14/2030", "-4.00"), ("10/14/2030", "-4.00"), ("10/15/2030", "-55.00"),
            ("10/17/2030", "-4.00")]
    p = _seam_household(tmp_path, csv_first=csv_first, csv=[(d, f"SAMPLE SHOP {i}", a) for i, (d, a) in enumerate(days)],
                        downloads=[(f"{d[6:]}{d[:2]}{d[3:5]}", a, f"SAMPLE SHOP {i}") for i, (d, a) in enumerate(days)])
    assert len(p.txns) == 5
    assert _checks(p, "import.sources") == [(f"import.sources.{ACCOUNT}", "info")]


@pytest.mark.parametrize("csv_first", [True, False], ids=["kept-twice", "kept-nowhere"])
def test_amounts_more_than_five_days_from_since_are_not_paired(tmp_path, csv_first):
    """The purchase made on 10-09 and posted on 10-21, six days either side of SINCE: no warning"""
    p = _seam_household(tmp_path, csv_first=csv_first, csv=[("10/09/2030", "SAMPLE HARDWARE", "-55.00")],
                        downloads=[("20301021", "-55.00", "SAMPLE HARDWARE")])
    assert _checks(p, "import.sources_seam") == []
    assert len(p.txns) == (2 if csv_first else 0)
    # five days either side are paired
    p = _seam_household(tmp_path / "five", csv_first=csv_first, csv=[("10/10/2030", "SAMPLE HARDWARE", "-55.00")],
                        downloads=[("20301020", "-55.00", "SAMPLE HARDWARE")])
    assert _checks(p, "import.sources_seam") == [(f"import.sources_seam.{ACCOUNT}", "warn")]


# ------------------------------------------------------------------ days no source holds

def test_rows_left_out_where_the_later_source_ends_early_are_counted_by_no_source(tmp_path):
    """The OFX download ends on 10-18 while the CSV holds rows to 10-25: the CSV's rows after 10-18 are the OFX
    source's days, which it never reached"""
    cfg = _config(tmp_path, {"sources": TWO_SOURCES})
    _october(tmp_path / "src")
    _bofa(tmp_path / "src", "card-2030-10-late.csv", [("10/25/2030", "SAMPLE CAFE", "-7.00"),
                                                      ("10/25/2030", "SAMPLE CAFE", "-7.00")])
    p = _parsed(tmp_path, cfg)
    assert _checks(p, "import.sources") == [(f"import.sources.{ACCOUNT}", "warn")]
    assert _detail(p, f"import.sources.{ACCOUNT}") == joined(("bofa_card", 4, 4), ("ofx", 2, 1), unheld=2)


def test_rows_left_out_where_the_earlier_source_ends_early_are_counted_by_no_source(tmp_path):
    """The CSV ends on 10-04 while the OFX download starts on 10-04: the download's row of 10-10 is the CSV source's
    day, after its last row"""
    cfg = _config(tmp_path, {"sources": [{"format": "bofa_card", "glob": "card-*.csv"},
                                         {"format": "ofx", "glob": "card-*.ofx", "since": SINCE}]})
    _bofa(tmp_path / "src", "card-2030-10.csv", OCTOBER_CSV[2:])               # 10-01 to 10-04
    _ofx(tmp_path / "src", "card-2030-10.ofx", [("20301004", "-18.20", "CHIPOTLE 0000"),
                                                ("20301010", "-9.50", "SAMPLE PHARMACY"),
                                                ("20301018", "32.99", "AUTOZONE 0000")])
    p = _parsed(tmp_path, cfg)
    assert _detail(p, f"import.sources.{ACCOUNT}") == joined(("bofa_card", 4, 0), ("ofx", 1, 2), unheld=1)
    assert _checks(p, "import.sources") == [(f"import.sources.{ACCOUNT}", "warn")]


def test_a_source_whose_files_hold_nothing_in_its_days_keeps_no_row(tmp_path):
    """OFX downloads named a source from November on, holding only October: every row is left out, the account's
    window and reconciliation come from the CSV's rows"""
    cfg = _config(tmp_path, {"sources": [TWO_SOURCES[0], {**TWO_SOURCES[1], "since": "2030-11-01"}]})
    _october(tmp_path / "src")
    p = _parsed(tmp_path, cfg)
    assert [r[3] for r in _rows(p)] == ["card-2030-10.csv"] * 6
    assert p.windows[ACCOUNT] == ("2030-10-01", "2030-10-18")
    assert _detail(p, f"import.sources.{ACCOUNT}") == joined(("bofa_card", 6, 0), ("ofx", 0, 3))
    assert _reconciles(p)
    assert _checks(p, "source.") == []                                      # its pattern matched a file


def test_a_source_whose_pattern_matches_no_file_warns_and_the_others_load(tmp_path):
    cfg = _config(tmp_path, {"sources": [TWO_SOURCES[0], {**TWO_SOURCES[1], "glob": "card-*.ofx"}]})
    _october(tmp_path / "src")
    p = _parsed(tmp_path, cfg)
    assert _checks(p, "source.") == [(f"source.{ACCOUNT}", "warn"), ("source.unmatched", "info")]
    assert _detail(p, f"source.{ACCOUNT}") == "no file matched glob 'card-*.ofx'"
    assert [r[0] for r in _rows(p)] == ["2030-10-01", "2030-10-02", "2030-10-02", "2030-10-04"]
    # the CSV's two rows from SINCE on are the OFX source's days, and it read nothing: no source counts them
    assert _checks(p, "import.sources") == [(f"import.sources.{ACCOUNT}", "warn")]
    assert _detail(p, f"import.sources.{ACCOUNT}") == joined(("bofa_card", 4, 2), ("ofx", 0, 0), unheld=2)


def test_an_account_none_of_whose_sources_matches_a_file_warns_once_and_has_no_window(tmp_path):
    cfg = _config(tmp_path, {"sources": TWO_SOURCES})
    p = _parsed(tmp_path, cfg)
    assert _checks(p, "") == [(f"source.{ACCOUNT}", "warn")]
    assert _detail(p, f"source.{ACCOUNT}") == "no file matched globs 'card-*.csv', 'card-*.qfx'"
    assert p.windows[ACCOUNT] == (None, None) and p.txns == []


def test_a_file_no_source_can_read_fails_its_check_and_the_other_source_still_loads(tmp_path):
    cfg = _config(tmp_path, {"sources": TWO_SOURCES})
    _october(tmp_path / "src")
    shutil.copy(SAMPLES / "ofx" / "two_statements.ofx", tmp_path / "src" / "card-2030-11.qfx")
    p = _parsed(tmp_path, cfg)
    assert _checks(p, "import.") == [(f"import.{ACCOUNT}.card-2030-11.qfx", "fail"),
                                     (f"import.balance_ignored.{ACCOUNT}", "info"),     # the download's card balance
                                     (f"import.sources.{ACCOUNT}", "info")]
    assert _detail(p, f"import.{ACCOUNT}.card-2030-11.qfx") == (
        f"Not read: {ofx.SEVERAL_ACCOUNTS}. Check that it is this account's own export, in the format its source "
        "names (accounts.yaml format: ofx).")
    assert _rows(p) == STORED
    assert _reconciles(p)
    # a source none of whose files could be read keeps nothing, and the other one loads as ever; the rows that one
    # leaves out in the unread source's days are counted by no source, and the join's check warns of them
    (tmp_path / "src" / "card-2030-10.qfx").unlink()
    p = _parsed(tmp_path / "again", cfg)
    assert _checks(p, "import.") == [(f"import.{ACCOUNT}.card-2030-11.qfx", "fail"),
                                     (f"import.sources.{ACCOUNT}", "warn")]
    assert _detail(p, f"import.sources.{ACCOUNT}") == joined(("bofa_card", 4, 2), ("ofx", 0, 0), unheld=2)
    assert [r[3] for r in _rows(p)] == ["card-2030-10.csv"] * 4


def test_a_file_not_read_is_named_by_its_path_in_the_statements_folder(tmp_path):
    """Two files of one name, one in a subfolder: the one not read fails its check under its path, so the check never
    names the file that was read"""
    cfg = _config(tmp_path, {"parser": "bofa_card", "glob": "**/c-*.csv"})
    _bofa(tmp_path / "src", "c-1.csv", [("10/08/2030", "SAMPLE SHOP", "-5.00")])
    (tmp_path / "src" / "2030" / "sub").mkdir(parents=True)
    (tmp_path / "src" / "2030" / "sub" / "c-1.csv").write_text("<html><body>Please sign in again</body></html>\n")
    p = _parsed(tmp_path, cfg)
    assert _checks(p, "import.") == [(f"import.{ACCOUNT}.2030/sub/c-1.csv", "fail")]
    assert _detail(p, f"import.{ACCOUNT}.2030/sub/c-1.csv").startswith("Not read: ")
    assert [r[2] for r in _rows(p)] == ["SAMPLE SHOP"]


def test_files_two_sources_match_are_read_once_for_the_first_and_fail_one_check_for_the_account(tmp_path):
    """The patterns differ, so the config is valid, but both match the 2030 exports: each is read once, by the first
    source and within its days, and one failing check counts and names them; the second source reads only its own
    file"""
    sources = [{"format": "bofa_card", "glob": "card-*.csv"},
               {"format": "bofa_card", "glob": "*-2030-1*.csv", "since": "2030-10-04"}]
    cfg = _config(tmp_path, {"sources": sources})
    src = tmp_path / "src"
    _bofa(src, "card-2030-10.csv", OCTOBER_CSV)                             # both patterns match these two
    _bofa(src, "card-2030-12.csv", [("09/20/2030", "SAMPLE PHARMACY", "-9.50")])
    _bofa(src, "new-2030-11.csv", [("11/02/2030", "SAMPLE BOOKSHOP", "-12.00")])
    p = _parsed(tmp_path, cfg)
    assert _checks(p, "import.") == [(f"import.matched_twice.{ACCOUNT}", "fail"), (f"import.sources.{ACCOUNT}", "warn")]
    assert _detail(p, f"import.matched_twice.{ACCOUNT}") == (
        "2 files match the file patterns of two of this account's sources: card-2030-10.csv, card-2030-12.csv. Each "
        "is read once, as the first source's, so its rows may sit with the wrong source: give each source a pattern "
        "that matches only its own files (accounts.yaml sources).")
    # read once, as the first source's files: their rows before 10-04 kept, the rest left out; the November file is the
    # second source's
    assert _rows(p) == [("2030-09-20", -9_50, "SAMPLE PHARMACY", "card-2030-12.csv"),
                        ("2030-10-01", -40_00, "WHOLEFDS SAMPLETOWN 00000", "card-2030-10.csv"),
                        ("2030-10-02", -4_00, "STARBUCKS STORE 00000", "card-2030-10.csv"),
                        ("2030-10-02", -4_00, "STARBUCKS STORE 00000", "card-2030-10.csv"),
                        ("2030-11-02", -12_00, "SAMPLE BOOKSHOP", "new-2030-11.csv")]
    assert [s.source_file for s in p.file_stats] == ["card-2030-10.csv", "card-2030-12.csv", "new-2030-11.csv"]
    # the second source read only its November row, so the October rows the first left out in its days are counted
    # by no source
    assert _detail(p, f"import.sources.{ACCOUNT}") == joined(("bofa_card", 4, 3), ("bofa_card", 1, 0), unheld=3)
    assert _reconciles(p)


def test_a_file_two_sources_match_that_the_first_cannot_read_fails_both_checks_each_in_its_own_words(tmp_path):
    sources = [{"format": "bofa_card", "glob": "card-*"}, {"format": "ofx", "glob": "*.qfx", "since": SINCE}]
    cfg = _config(tmp_path, {"sources": sources})
    _october(tmp_path / "src")                                              # the .qfx is the first source's too
    p = _parsed(tmp_path, cfg)
    assert _checks(p, "import.") == [(f"import.matched_twice.{ACCOUNT}", "fail"),
                                     (f"import.{ACCOUNT}.card-2030-10.qfx", "fail"),
                                     (f"import.sources.{ACCOUNT}", "warn")]
    assert _detail(p, f"import.{ACCOUNT}.card-2030-10.qfx").startswith("Not read: ")
    assert _detail(p, f"import.matched_twice.{ACCOUNT}").startswith(
        "1 file matches the file patterns of two of this account's sources: card-2030-10.qfx. Each is read once")


def test_each_files_balance_competes_whatever_its_source(tmp_path):
    """A checking account: the newest balance any of its files states is booked, from whichever source"""
    sources = [{"format": "ofx", "glob": "old-*.ofx"}, {"format": "ofx", "glob": "new-*.ofx", "since": "2030-10-15"}]
    cfg = _config(tmp_path, {"kind": "checking", "sources": sources})
    shutil.copy(SAMPLES / "ofx" / "bank_sgml.ofx", tmp_path / "src" / "old-2030-10.ofx")    # balance on 10-31
    _ofx(tmp_path / "src", "new-2030-10.ofx", [("20301020", "-5.00", "SAMPLE CAFE")])        # balance on 04-30
    p = _parsed(tmp_path, cfg)
    assert p.latest_balances == {ACCOUNT: ("2030-10-31", 4319_37)}           # the first source's file is newer
    (tmp_path / "src" / "old-2030-10.ofx").unlink()
    assert _parsed(tmp_path / "again", cfg).latest_balances == {ACCOUNT: ("2030-04-30", -100_00)}


# ------------------------------------------------------------------ with the bank feed

def _noon(day: str) -> int:
    """The epoch of noon UTC on `day`: the same date in the household's zone."""
    return int(dt.datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp())


FEED = {"id": "ACT-demo-card", "name": "Sample Card", "currency": "USD", "transactions": [],
        "balance": "-100.00", "balance-date": _noon("2030-10-31")}


def test_a_feed_cutover_drops_the_last_sources_rows_on_or_after_it_once_the_feed_reports_the_account(tmp_path):
    cutover = "2030-10-17"
    cfg = _config(tmp_path, {"sources": TWO_SOURCES, "simplefin_id": "ACT-demo-card", "simplefin_cutover": cutover},
                  feed=FEED)
    _october(tmp_path / "src")
    _bofa(tmp_path / "src", "card-2030-10-late.csv", [("10/20/2030", "SAMPLE BOOKSHOP", "-12.00")])
    p = _parsed(tmp_path, cfg)
    assert _rows(p) == [r for r in STORED if r[0] < cutover]
    assert _detail(p, f"simplefin.csv_clip.{ACCOUNT}") == (
        f"1 file rows on/after cutover {cutover} dropped (SimpleFIN owns that window)")
    # the newest file row a source holds in its own days: the CSV's row of 10-20 is in the OFX download's days
    assert p.file_clips == {ACCOUNT: (1, "2030-10-18")}
    # a source's counts are of the rows outside its own days; the rows the cutover drops are its own check's. The
    # CSV's row of 10-20 lies past the OFX download's last day but on the feed's: the feed holds it
    assert _checks(p, "import.sources") == [(f"import.sources.{ACCOUNT}", "info")]
    assert _detail(p, f"import.sources.{ACCOUNT}") == joined(("bofa_card", 4, 3), ("ofx", 1, 1))
    assert _reconciles(p)


def test_the_seam_check_counts_only_the_rows_the_cutover_leaves_kept(tmp_path):
    """Since 10-10, cutover 10-12, the feed reporting the account: the later source's rows of 10-12 and 10-20 are the
    feed's and dropped, so its purchase of 10-12 is kept by no source, and the earlier source's purchase of one amount
    on 10-08 is no double"""
    sources = [{"format": "bofa_card", "glob": "c-*.csv"}, {"format": "bofa_card", "glob": "d-*.csv",
                                                             "since": "2030-10-10"}]
    cfg = _config(tmp_path, {"sources": sources, "simplefin_id": "ACT-demo-card", "simplefin_cutover": "2030-10-12"},
                  feed=FEED)
    _bofa(tmp_path / "src", "c-1.csv", [("10/08/2030", "SAMPLE SHOP A", "-50.00"), ("10/01/2030", "SAMPLE OLD", "-1.00")])
    _bofa(tmp_path / "src", "d-1.csv", [("10/12/2030", "SAMPLE SHOP B", "-50.00"), ("10/20/2030", "SAMPLE NEW", "-2.00")])
    p = _parsed(tmp_path, cfg)
    assert [r[2] for r in _rows(p)] == ["SAMPLE OLD", "SAMPLE SHOP A"]
    assert _detail(p, f"simplefin.csv_clip.{ACCOUNT}").startswith("2 ")
    assert _detail(p, f"import.sources.{ACCOUNT}") == joined(("bofa_card", 2, 0), ("bofa_card", 0, 0))
    assert _checks(p, "import.sources_seam") == []
    assert _reconciles(p)


def test_a_source_starting_on_or_after_the_cutover_is_refused_and_a_cutover_with_balances_only_too(tmp_path):
    account = {"sources": TWO_SOURCES, "simplefin_id": "ACT-demo-card", "simplefin_cutover": "2030-10-03"}
    with pytest.raises(ConfigError) as refused:
        _config(tmp_path, account)
    assert [(p.path, p.message) for p in refused.value.problems] == [
        (f"accounts.{ACCOUNT}.sources[1].since", "on or after the account's simplefin_cutover (2030-10-03)")]
    # with balances only the feed brings no row, so a cutover would clip the files' rows for nothing to replace them
    with pytest.raises(ConfigError) as refused:
        _config(tmp_path / "balances", {**account, "simplefin_balances_only": True})
    assert [p.path for p in refused.value.problems] == [f"accounts.{ACCOUNT}.simplefin_cutover",
                                                        f"accounts.{ACCOUNT}.sources[1].since"]
    account = {"sources": TWO_SOURCES, "simplefin_id": "ACT-demo-card", "simplefin_balances_only": True}
    assert _config(tmp_path / "no-cutover", account).txn_sources(ACCOUNT)[1].since == SINCE


def test_without_the_feed_reporting_the_account_the_rows_after_the_cutover_are_kept(tmp_path):
    cfg = _config(tmp_path, {"sources": TWO_SOURCES, "simplefin_id": "ACT-demo-card",
                             "simplefin_cutover": "2030-10-17"})
    _october(tmp_path / "src")
    p = _parsed(tmp_path, cfg)
    assert _rows(p) == STORED
    # only the rows each source holds in its own days count as the files' rows after the cutover
    assert _detail(p, f"simplefin.csv_clip.{ACCOUNT}") == (
        "nothing clipped: 1 file row(s) on/after cutover 2030-10-17 kept, as the feed has no data for this account yet")

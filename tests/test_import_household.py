# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""A household that brings its history from bank files, end to end: built in tmp_path in the examples/minimal shape
(config/ and imports/ under FF_HOME), it reads a checking account with a shipped preset from that preset's sample, a
card from an OFX sample, a second card from two sources joined at a stated day (a preset's CSV exports until `since`,
OFX downloads from it, the two overlapping by a month), and a savings account with a preset of its own that
`python -m etl import map --answers` writes for a made-up layout. The config validates, the ingest fails no check,
each account stores the rows its files hold, the join says what each source kept, the savings account's balance is
booked and no card's is, a second ingest writes the same exports, and `import detect` names exactly one format for
each file. Also here: the worked example of docs/design/importers.md, its preset loaded as a shipped one is and the
file it shows read with it, as the document says.

Every file is invented: the shipped samples under tests/importers/samples/, and the rows written here, with merchants
from the starter rules and placeholder account numbers. The samples are dated in 2030, so the clock is set after them,
as a household ingests after its statements; the clock is not what is tested."""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from conftest import ROOT

from etl.__main__ import main
from etl.import_cli import preview
from etl.importers.csv_preset import load_preset

SAMPLES = ROOT / "tests" / "importers" / "samples"
EXAMPLE = ROOT / "examples" / "minimal"
DESIGN = ROOT / "docs" / "design" / "importers.md"
NOW = "2030-12-01T12:00:00"

CHECKING, CARD, JOINED, SAVINGS = "demo_checking_0000", "demo_card_0000", "demo_card_0001", "demo_savings_0000"
OWN = "sample_union"
SINCE = "2030-09-01"

# the second card's rows: August in its CSV exports only, September in both (the overlap), October in its OFX only
AUGUST = [("2030-08-04", -54_18, "TRADER JOE'S #000 SAMPLETOWN"), ("2030-08-11", -41_07, "SHELL OIL 00000000000"),
          ("2030-08-19", 900_00, "PAYMENT - THANK YOU"), ("2030-08-24", -18_20, "CHIPOTLE 0000")]
SEPTEMBER = [("2030-09-03", -27_40, "DOORDASH*SAMPLE KITCHEN"), ("2030-09-09", -6_45, "STARBUCKS STORE 00000"),
             ("2030-09-17", 750_00, "PAYMENT - THANK YOU"), ("2030-09-26", -89_95, "JIFFY LUBE #0000")]
OCTOBER = [("2030-10-02", -73_66, "WHOLEFDS SAMPLETOWN 00000"), ("2030-10-14", -112_00, "AMTRAK TICKETS"),
           ("2030-10-21", -32_99, "AUTOZONE 0000")]

# a made-up credit union's savings download: no bank's layout, oldest row first, a running balance on every row
SAVINGS_FILE = """\
Txn Date,Memo,Money Out,Money In,Balance After
2030-08-01,INTEREST PAID,,1.25,5001.25
2030-08-15,ONLINE TRANSFER FROM CHECKING,,500.00,5501.25
2030-09-01,INTEREST PAID,,1.38,5502.63
2030-09-20,ONLINE TRANSFER TO CHECKING,250.00,,5252.63
2030-10-01,INTEREST PAID,,1.31,5253.94
"""
# what `import map` is told about it
SAVINGS_ANSWERS = {"header": True, "date": "Txn Date", "debit": "Money Out", "credit": "Money In",
                   "description": ["Memo"], "balance": "Balance After", "newest_first": False, "kind": "bank",
                   "label": "Sample Credit Union savings"}

OFX_HEAD = """\
OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<CREDITCARDMSGSRSV1>
<CCSTMTTRNRS>
<TRNUID>0
<CCSTMTRS>
<CURDEF>USD
<CCACCTFROM>
<ACCTID>000012345678
</CCACCTFROM>
<BANKTRANLIST>
<DTSTART>20300901
<DTEND>20301031
"""
OFX_TAIL = """\
</BANKTRANLIST>
</CCSTMTRS>
</CCSTMTTRNRS>
</CREDITCARDMSGSRSV1>
</OFX>
"""


def card_csv(rows: list[tuple[str, int, str]]) -> str:
    """The rows in the Bank of America card download's layout, newest first, as the bank writes it"""
    lines = ["Posted Date,Reference Number,Payee,Address,Amount"]
    for n, (day, cents, name) in enumerate(sorted(rows, reverse=True)):
        y, m, d = day.split("-")
        sign = "-" if cents < 0 else ""
        lines.append(f"{m}/{d}/{y},{n + 1:023d},{name},SAMPLETOWN ST,{sign}{abs(cents) // 100}.{abs(cents) % 100:02d}")
    return "\n".join(lines) + "\n"


def card_ofx(rows: list[tuple[str, int, str]]) -> str:
    """The rows as an SGML card statement, one STMTTRN each, its FITID invented"""
    body = ""
    for n, (day, cents, name) in enumerate(rows):
        sign = "-" if cents < 0 else ""
        body += (f"<STMTTRN>\n<TRNTYPE>{'DEBIT' if cents < 0 else 'CREDIT'}\n<DTPOSTED>{day.replace('-', '')}\n"
                 f"<TRNAMT>{sign}{abs(cents) // 100}.{abs(cents) % 100:02d}\n<FITID>S{n + 1:04d}\n<NAME>{name}\n"
                 "</STMTTRN>\n")
    return OFX_HEAD + body + OFX_TAIL


def cli(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["etl", *args])
    return main()


def exported(home: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted((home / "data" / "exports").glob("*.json"))}


def report(home: Path) -> dict[str, dict]:
    checks = json.loads((home / "data" / "exports" / "quality_report.json").read_text("utf-8"))["checks"]
    return {c["name"]: c for c in checks}


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """The household: examples/minimal's household.yaml, four accounts, their statement files, and the savings
    account's own preset written by `import map` from its file"""
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    shutil.copy(EXAMPLE / "config" / "household.yaml", home / "config" / "household.yaml")
    imports = home / "imports"
    imports.mkdir()
    shutil.copy(SAMPLES / "schwab_checking" / "transactions.csv", imports / f"{CHECKING}-2030-07.csv")
    shutil.copy(SAMPLES / "ofx" / "card_sgml.qfx", imports / f"{CARD}-2030-10.qfx")
    (imports / f"{JOINED}-2030-08.csv").write_text(card_csv(AUGUST + SEPTEMBER), "utf-8")
    (imports / f"{JOINED}-2030-09.ofx").write_text(card_ofx(SEPTEMBER + OCTOBER), "utf-8")
    (imports / f"{SAVINGS}-2030.csv").write_text(SAVINGS_FILE, "utf-8")
    sample = {"institution": "Sample Bank", "owner": "Alex"}
    accounts = {
        CHECKING: {"name": "Sample Checking", **sample, "kind": "checking", "parser": "schwab_checking",
                   "glob": f"{CHECKING}-*.csv"},
        CARD: {"name": "Sample Card", **sample, "kind": "credit-card", "parser": "ofx", "glob": f"{CARD}-*.qfx"},
        JOINED: {"name": "Sample Travel Card", **sample, "kind": "credit-card", "sources": [
            {"format": "bofa_card", "glob": f"{JOINED}-*.csv"},
            {"format": "ofx", "glob": f"{JOINED}-*.ofx", "since": SINCE}]},
        SAVINGS: {"name": "Sample Savings", "institution": "Sample Credit Union", "owner": "Alex", "kind": "savings",
                  "parser": OWN, "glob": f"{SAVINGS}-*.csv"},
    }
    (home / "config" / "accounts.yaml").write_text(yaml.safe_dump({"version": 1, "accounts": accounts},
                                                                  sort_keys=False), "utf-8")
    monkeypatch.setenv("FF_HOME", str(home))
    monkeypatch.setenv("FF_NOW", NOW)
    answers = tmp_path / "answers.yaml"
    answers.write_text(yaml.safe_dump(SAVINGS_ANSWERS), "utf-8")
    assert cli(monkeypatch, "import", "map", str(imports / f"{SAVINGS}-2030.csv"), "--id", OWN,
               "--answers", str(answers)) == 0
    return home


def test_import_map_writes_the_households_own_preset_from_the_answers(home):
    written = load_preset(home / "config" / "presets" / f"{OWN}.yaml", household=True)
    assert (written.id, written.kind, written.label) == (OWN, "bank", "Sample Credit Union savings")
    assert written.header == ("Txn Date", "Memo", "Money Out", "Money In", "Balance After")
    assert (written.date_formats, written.debit, written.credit, written.balance, written.order) == (
        ("%Y-%m-%d",), "Money Out", "Money In", "Balance After", "oldest_first")
    assert (written.source, written.evidence) == ("", "")


def test_the_household_validates_and_ingests_with_no_failing_check(home, monkeypatch, capsys):
    capsys.readouterr()
    assert cli(monkeypatch, "validate") == 0
    assert capsys.readouterr().out == "config OK (2 files, 4 accounts, 1 preset)\n"
    assert cli(monkeypatch, "ingest") == 0
    checks = report(home)
    assert [name for name, c in checks.items() if c["status"] == "fail"] == []
    for account in (CHECKING, CARD, JOINED, SAVINGS):
        assert checks[f"reconcile.{account}.store_vs_files"]["status"] == "pass"
    # each account stores the rows its files hold: the joined card's August from its CSV and the rest from its OFX
    txns = json.loads((home / "data" / "exports" / "transactions.json").read_text("utf-8"))["transactions"]
    per_account = {a: sorted((t["date"], round(t["amount"] * 100), t["source_file"]) for t in txns if t["account"] == a)
                   for a in (CHECKING, CARD, JOINED, SAVINGS)}
    assert {a: len(rows) for a, rows in per_account.items()} == {CHECKING: 12, CARD: 3, JOINED: 11, SAVINGS: 5}
    assert per_account[JOINED] == sorted([(d, c, f"{JOINED}-2030-08.csv") for d, c, _ in AUGUST]
                                         + [(d, c, f"{JOINED}-2030-09.ofx") for d, c, _ in SEPTEMBER + OCTOBER])
    # the join says what each source kept and left out, as info: the OFX files hold every September day the CSV leaves
    assert checks[f"import.sources.{JOINED}"] == {
        "name": f"import.sources.{JOINED}", "status": "info",
        "detail": "Read from 2 sources, each holding its own days: bofa_card kept 4 rows and left out 4; ofx kept 7 "
                  "rows and left out 0. A row a source holds outside its own days is left out: the source before or "
                  "after it holds that day."}
    assert f"import.sources_seam.{JOINED}" not in checks             # the formats date every September row alike
    assert f"import.sources.{CHECKING}" not in checks                # one source, the short form: nothing to join


def test_the_savings_balance_is_booked_and_no_cards_balance_is(home, monkeypatch):
    assert cli(monkeypatch, "ingest") == 0
    checks = report(home)
    assert checks[f"import.rollforward.{SAVINGS}"]["status"] == "pass"
    # the OFX card statement states a balance, which a card does not take from a file
    assert checks[f"import.balance_ignored.{CARD}"]["status"] == "info"
    assert f"import.balance_ignored.{JOINED}" not in checks         # its statement files state none
    assert f"import.balance_ignored.{CHECKING}" not in checks       # the shipped checking preset reads no balance
    [snapshot] = json.loads((home / "data" / "exports" / "assets_timeseries.json").read_text("utf-8"))["snapshots"]
    rows = {r["account_id"]: (r["value"], r["as_of"]) for r in snapshot["rows"]}
    assert rows == {SAVINGS: (5253.94, "2030-10-01")}
    assert {"Sample Card", "Sample Travel Card"} <= set(snapshot["missing"])     # by name: never guessed


def test_a_second_ingest_writes_the_same_exports(home, monkeypatch):
    """Byte for byte, the manifest's generation stamp aside (the pinned clock makes even that one equal)"""
    assert cli(monkeypatch, "ingest") == 0
    first = exported(home)
    assert cli(monkeypatch, "ingest") == 0
    second = exported(home)
    assert list(first) == list(second) and len(first) == 14
    for files in (first, second):
        manifest = json.loads(files.pop("manifest.json"))
        manifest.pop("generated_at")
        files["manifest.json"] = json.dumps(manifest, sort_keys=True).encode()
    assert first == second


@pytest.mark.parametrize(("name", "fmt"), [(f"{CHECKING}-2030-07.csv", "schwab_checking"),
                                           (f"{CARD}-2030-10.qfx", "ofx"),
                                           (f"{JOINED}-2030-08.csv", "bofa_card"),
                                           (f"{JOINED}-2030-09.ofx", "ofx"),
                                           (f"{SAVINGS}-2030.csv", OWN)])
def test_import_detect_names_exactly_one_format_for_each_file(home, monkeypatch, capsys, name, fmt):
    capsys.readouterr()
    assert cli(monkeypatch, "import", "detect", str(home / "imports" / name)) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "1 format reads this file:"
    assert re.match(rf"  {fmt} \(", lines[1]) and len(lines) == 2


# ------------------------------------------------------------------ the design document's worked example

def fenced(text: str, tag: str) -> str:
    """The text of the one block fenced ```<tag> in the document"""
    blocks = re.findall(rf"^```{tag}\n(.*?)^```$", text, re.M | re.S)
    assert len(blocks) == 1, f"docs/design/importers.md holds {len(blocks)} blocks fenced ```{tag}"
    return blocks[0]


def test_the_design_documents_example_preset_loads_and_reads_its_file_as_the_document_says(tmp_path):
    """The example is a shipped preset in form: it loads with no allowance (a source, an evidence level), and
    `import preview` with it prints for the document's file exactly what the document shows"""
    text = DESIGN.read_text("utf-8")
    doc = yaml.safe_load(fenced(text, "yaml"))
    presets = tmp_path / "presets"
    presets.mkdir()
    path = presets / f"{doc['id']}.yaml"
    path.write_text(fenced(text, "yaml"), "utf-8")
    preset = load_preset(path)
    assert preset.evidence == "bank-documented" and preset.institution and preset.download
    statement = tmp_path / "activity.csv"
    statement.write_text(fenced(text, "csv"), "utf-8")
    lines: list[str] = []
    assert preview(str(statement), preset.id, {preset.id: preset}, out=lines.append) == 0
    assert "\n".join(lines) + "\n" == fenced(text, "text")

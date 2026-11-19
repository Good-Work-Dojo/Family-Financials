# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The CSV preset engine (etl/importers/csv_preset.py): one YAML preset is one statement format. A preset finds its
layout by a header line or a headerless shape and refuses a file that does not have it, in plain words that never
quote a row of the file; it reads dates by the patterns it states and skips, never guesses, a row whose date does not
fit; it reads a signed amount column as the file writes it or turned round, or a debit and a credit column; it joins and
squashes the description; it counts every row it skips, and refuses a file most of whose rows it cannot read; a row
of another width than the header, or a money cell in another notation, is never read; it tries its encodings in order;
and its balance column feeds the running-balance check. The shipped presets register as formats when the registry
loads, each stating where its layout comes from, and the uids each reads from its sample file are pinned in
tests/importers/presets.lock. Several tests here began as review probes of a file a bank or a person could produce.

Every file here is invented: the samples under tests/importers/samples/, the synthetic preset under
tests/importers/synthetic/ (a made-up headerless layout, no bank's format) and the files written into tmp_path. The
tests' own presets state `evidence: synthetic`, which only a load that allows it accepts.

Run as a script, this file prints the lock entries the lock lacks (a new preset's or sample's), to append to it; it
prints nothing and exits 1 when an entry the lock holds would change or go."""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import pytest
import yaml
from conftest import isolated_pipeline, write_household

from etl import pipeline
from etl.config import Config
from etl.import_cli import detect
from etl.importers import registry
from etl.importers.csv_preset import (
    COLUMNS_DO_NOT_FIT,
    MIXED_TEXT,
    NOT_TEXT,
    UNCLOSED_QUOTE,
    Preset,
    load_preset,
)
from etl.importers.registry import NotThisFormat
from etl.models import Txn
from etl.quality import reconcile
from etl.validate import ConfigError

ROOT = Path(__file__).resolve().parents[1]
IMPORTERS = ROOT / "tests" / "importers"
SAMPLES = IMPORTERS / "samples"
LOCK = IMPORTERS / "presets.lock"
SYNTHETIC = IMPORTERS / "synthetic" / "sample_headerless.yaml"
# the account the lock's uids are computed for: a uid hashes its account id
LOCK_ACCOUNT = "demo_lock_0000"
ACCOUNT = "demo_checking_0000"
CHANGED_IDENTITY = "a preset's rows changed identity: give the preset a new id"

# A synthetic header preset the tests vary: a made-up layout, no bank's format.
BASE = {
    "id": "sample_bank", "label": "Synthetic bank layout (tests only)", "kind": "bank",
    "source": "synthetic: invented for these tests; no bank's layout", "evidence": "synthetic",
    "detect": {"header": ["Date", "Description", "Amount"]},
    "date": {"column": "Date", "format": "%m/%d/%Y"},
    "amount": {"column": "Amount", "sign": "as_is"},
    "description": {"columns": ["Description"]},
}
CARD_HEADER = "Posted Date,Reference Number,Payee,Address,Amount\n"


# ------------------------------------------------------------------ helpers (and the lock's, which the script uses)

Entries = dict[tuple[str, str], list[str]]


def lock_blocks() -> Entries:
    """The lock entries of the shipped presets as they stand: (preset id, sample file name) -> the uid of every row the
    preset reads from that sample, in file order, with the occurrence index the pipeline gives each row."""
    blocks: Entries = {}
    for preset_id, preset in sorted(registry.presets().items()):
        for sample in sorted((SAMPLES / preset_id).glob("*.csv")):
            txns, _, _ = preset.parse(sample, LOCK_ACCOUNT)
            pipeline.assign_occurrence_seq(txns)
            blocks[(preset_id, sample.name)] = [t.uid for t in txns]
    return blocks


def render(entries: Entries) -> str:
    """Entries in the lock's form: `<id> <sample file>`, one uid a line, a blank line after each entry"""
    return "".join(f"{preset_id} {sample}\n" + "".join(f"{uid}\n" for uid in uids) + "\n"
                   for (preset_id, sample), uids in entries.items())


def lock_entries(text: str) -> Entries:
    """(preset id, sample file) -> its uids, from a lock's text; comment and blank lines are not entries"""
    entries: dict[tuple[str, str], list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if " " in line:
            preset_id, sample = line.split(" ", 1)
            current = entries.setdefault((preset_id, sample), [])
        elif current is not None:
            current.append(line)
    return entries


def changed_entries(locked: Entries, current: Entries) -> list[str]:
    """The entries the lock holds that the presets as they stand change or no longer give, one line each in words a
    maintainer acts on"""
    out = [f"{CHANGED_IDENTITY} ({k[0]} on {k[1]})" for k in sorted(locked.keys() & current.keys())
           if locked[k] != current[k]]
    out += [f"the lock lists {k[0]} on {k[1]}, which no shipped preset and sample give: remove its lines"
            for k in sorted(locked.keys() - current.keys())]
    return out


def lock_differences(locked: Entries, current: Entries) -> list[str]:
    """Every difference between the lock's entries and those the presets as they stand give"""
    return changed_entries(locked, current) + [f"the lock does not list {k[0]} on {k[1]}: append its lines to the lock"
                                               for k in sorted(current.keys() - locked.keys())]


def main() -> int:
    """Prints the entries the lock lacks, to append to it; prints nothing and exits 1, saying why on stderr, when an
    entry the lock holds would change or go"""
    locked, current = lock_entries(LOCK.read_text(encoding="utf-8")), lock_blocks()
    changed = changed_entries(locked, current)
    if changed:
        print("\n".join(changed), file=sys.stderr)
        return 1
    sys.stdout.write(render({k: v for k, v in current.items() if k not in locked}))
    return 0


def preset_from(tmp_path: Path, **changes) -> Preset:
    """BASE with `changes` (a key given None is left out), written as presets/<id>.yaml and loaded"""
    doc = {k: v for k, v in {**BASE, **changes}.items() if v is not None}
    d = tmp_path / "presets"
    d.mkdir(exist_ok=True)
    path = d / f"{doc['id']}.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return load_preset(path, allow_synthetic=True)


def synthetic_headerless() -> Preset:
    return load_preset(SYNTHETIC, allow_synthetic=True)


def csv_file(tmp_path: Path, text: str, name: str = "export.csv", encoding: str = "utf-8") -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


def read(preset: Preset, path: Path, account_id: str = ACCOUNT) -> tuple[list[Txn], object, dict]:
    """The preset's parse of `path`, its counts checked against the contract every format keeps: each row the file
    holds is read or skipped with a reason, and the signed total is that of exactly the rows read"""
    txns, stats, extra = preset.parse(path, account_id)
    assert stats.rows_total == stats.rows_parsed + stats.rows_skipped
    assert stats.rows_skipped == sum(stats.skip_reasons.values())
    assert stats.rows_parsed == len(txns)
    assert stats.sum_cents == sum(t.amount_cents for t in txns)
    assert stats.parser == preset.id and stats.source_file == path.name
    return txns, stats, extra


def refusal(preset: Preset, path: Path) -> str:
    with pytest.raises(NotThisFormat) as caught:
        preset.parse(path, ACCOUNT)
    return str(caught.value)


# ------------------------------------------------------------------ the shipped presets

def test_the_shipped_presets_are_formats_in_the_registry():
    shipped = registry.presets()
    assert set(shipped) == {"bofa_card", "capital_one_card", "discover_card", "schwab_checking", "wells_fargo"}
    for preset_id, preset in shipped.items():
        assert preset.id == preset_id
        assert registry.formats()[preset_id] == preset.parse
        assert pipeline.TXN_PARSERS[preset_id] == preset.parse


def test_each_shipped_preset_states_where_its_layout_comes_from_and_its_sign():
    for preset_id, preset in registry.presets().items():
        text = (registry.PRESETS_DIR / f"{preset_id}.yaml").read_text(encoding="utf-8")
        # none is sample-verified yet; widely-reported is the headerless starter bank's alone, whose layout several
        # public descriptions agree on and no definition under a licence this project can use states
        assert preset.evidence in {"bank-documented", "oss-definition"} or (
            preset_id == "wells_fargo" and preset.evidence == "widely-reported")
        assert preset.evidence != "synthetic"
        assert "https://" in preset.source
        assert preset.institution and preset.download, f"{preset_id}: the table of supported banks prints both"
        assert re.search(r"^#.*\bsign\b", text, re.M), f"{preset_id}: a comment says where its sign comes from"
        assert sorted(p.name for p in (SAMPLES / preset_id).glob("*.csv")), f"{preset_id} has a sample file"


def test_a_shipped_preset_that_does_not_load_stops_the_registry(tmp_path):
    (tmp_path / "broken_card.yaml").write_text(yaml.safe_dump(
        {**BASE, "evidence": "oss-definition", "id": "broken_card", "kind": "debit"}))
    before = registry.formats()
    with pytest.raises(ConfigError) as caught:
        registry.register_presets(tmp_path)
    assert [p.path for p in caught.value.problems] == ["kind"]
    assert registry.formats() == before


def test_a_synthetic_preset_loads_only_where_the_tests_allow_it(tmp_path):
    (tmp_path / "sample_bank.yaml").write_text(yaml.safe_dump(BASE))
    with pytest.raises(ConfigError) as caught:
        registry.register_presets(tmp_path)                 # a shipped preset never states synthetic
    [problem] = caught.value.problems
    assert (problem.path, problem.message, problem.fix) == (
        "evidence", "synthetic is the evidence of a preset the tests write for themselves",
        "write one of bank-documented, oss-definition, widely-reported, sample-verified")
    assert load_preset(tmp_path / "sample_bank.yaml", allow_synthetic=True).evidence == "synthetic"
    for level in ("bank-documented", "oss-definition", "widely-reported", "sample-verified"):
        (tmp_path / "sample_bank.yaml").write_text(yaml.safe_dump({**BASE, "evidence": level}))
        assert load_preset(tmp_path / "sample_bank.yaml").evidence == level


# Each shipped preset's sample file, how many rows it reads, and three of those rows as (date, signed cents,
# description): money out, money in, and a row only that layout writes so (a quoted comma, a check, a refund)
SHIPPED = {
    "bofa_card": ("activity.csv", 12, [("2030-03-28", -54_18, "TRADER JOE'S #000 SAMPLETOWN"),
                                       ("2030-03-22", 1250_00, "PAYMENT - THANK YOU"),
                                       ("2030-03-25", -18_20, "PANERA BREAD #000, SAMPLETOWN")]),
    "capital_one_card": ("transactions.csv", 12, [("2030-05-30", -47_92, "TRADER JOE'S #000 SAMPLETOWN"),
                                                  ("2030-05-24", 1100_00, "AUTOPAY PAYMENT - THANK YOU"),
                                                  ("2030-05-27", -16_80, "PANERA BREAD #000, SAMPLETOWN")]),
    "discover_card": ("activity.csv", 12, [("2030-06-28", -58_04, "TRADER JOE'S #000 SAMPLETOWN"),
                                           ("2030-06-22", 1325_00, "INTERNET PAYMENT - THANK YOU"),
                                           ("2030-06-21", 36_49, "AUTOZONE 0000 SAMPLETOWN")]),
    "schwab_checking": ("transactions.csv", 12, [("2030-07-18", -1150_00, "ONLINE PAYMENT TO SAMPLE CARD 0000"),
                                                 ("2030-07-15", 2875_00, "SAMPLE EMPLOYER INC PAYROLL"),
                                                 ("2030-07-12", -175_00, "CHECK 1052")]),
    "wells_fargo": ("activity.csv", 12, [("2030-04-29", -61_37, "TRADER JOE'S #000 SAMPLETOWN"),
                                         ("2030-04-26", 2650_00, "SAMPLE EMPLOYER INC PAYROLL 000000"),
                                         ("2030-04-17", -240_00, "CHECK # 2041")]),
}


def test_the_table_of_samples_names_every_shipped_preset():
    assert set(SHIPPED) == set(registry.presets())


@pytest.mark.parametrize("preset_id", sorted(SHIPPED))
def test_each_shipped_preset_reads_its_sample_and_detect_names_it_alone(preset_id):
    """Its layout is found; every line of the sample is read or skipped with a reason and the total is that of the rows
    read (read() checks both); the rows are as many as the table says and hold its three; and of every format the
    release has, `import detect` finds this one alone reads the sample"""
    name, count, spots = SHIPPED[preset_id]
    sample = SAMPLES / preset_id / name
    preset = registry.presets()[preset_id]
    txns, _, _ = read(preset, sample)
    got = [(t.date, t.amount_cents, t.description_raw) for t in txns]
    assert len(got) == count
    assert [spot for spot in spots if spot not in got] == []
    lines: list[str] = []
    assert detect(str(sample), {}, out=lines.append) == 0
    assert lines[1].startswith(f"  {preset_id} (preset, {preset.kind}): {count} rows, ")


@pytest.mark.parametrize(("preset_id", "other_id"), [(a, b) for a in sorted(SHIPPED) for b in sorted(SHIPPED) if a != b])
def test_no_shipped_preset_reads_another_ones_sample(preset_id, other_id):
    """Two presets never claim one file: each refuses the other's sample, or finds no row in it. Two banks that truly
    share a layout are one preset naming both."""
    sample = SAMPLES / other_id / SHIPPED[other_id][0]
    try:
        txns, _, _ = registry.presets()[preset_id].parse(sample, ACCOUNT)
    except NotThisFormat:
        return
    assert txns == []


def test_a_preset_may_state_its_institution_and_how_to_download_the_file(tmp_path):
    """The two lines the table of supported banks prints for a preset; a preset that leaves them out loads with them
    empty, and one that states them states text"""
    stated = preset_from(tmp_path, institution="  Sample Bank ", download="Activity, then Download, then CSV.")
    assert (stated.institution, stated.download) == ("Sample Bank", "Activity, then Download, then CSV.")
    plain = preset_from(tmp_path)
    assert (plain.institution, plain.download) == ("", "")
    with pytest.raises(ConfigError) as caught:
        preset_from(tmp_path, institution=["Sample Bank"], download=" ")
    assert [(p.path, p.message) for p in caught.value.problems] == [("download", "empty"), ("institution", "not text")]


def test_the_card_sample_reads_purchases_negative_and_payments_and_refunds_positive():
    preset = registry.presets()["bofa_card"]
    txns, stats, extra = read(preset, SAMPLES / "bofa_card" / "activity.csv", "demo_card_0000")
    got = [(t.date, t.description_raw, t.amount_cents) for t in txns]
    assert got[:5] == [("2030-03-28", "TRADER JOE'S #000 SAMPLETOWN", -54_18),
                       ("2030-03-27", "STARBUCKS STORE 00000", -6_45),
                       ("2030-03-27", "STARBUCKS STORE 00000", -6_45),
                       ("2030-03-25", "PANERA BREAD #000, SAMPLETOWN", -18_20),     # the quoted comma
                       ("2030-03-22", "PAYMENT - THANK YOU", 1250_00)]              # a payment is money in
    assert ("2030-03-18", "AUTOZONE 0000", 32_99) in got                           # a refund too
    assert ("2030-03-12", "DOORDASH*SAMPLE KITCHEN", -27_40) in got                # whitespace squashed
    assert "SAMPLE AUTHORIZATION HOLD" not in {t.description_raw for t in txns}     # no amount: skipped
    assert stats.skip_reasons == {"header": 1, "no-amount": 1}
    assert (stats.rows_total, stats.rows_parsed, stats.sum_cents) == (14, 12, 820_64)
    assert (stats.date_min, stats.date_max) == ("2030-03-01", "2030-03-28")
    assert all(t.account_id == "demo_card_0000" and t.source_file == "activity.csv" for t in txns)
    assert extra == {}                                     # no balance column: no balance


# ------------------------------------------------------------------ detect: the header

def test_a_card_preset_refuses_a_checking_export_naming_the_columns_it_lacks(tmp_path):
    path = csv_file(tmp_path, "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
                              "DEBIT,01/30/2030,SAMPLE PHARMACY,-20.00,DEBIT_CARD,4865.00,\n")
    words = refusal(registry.presets()["bofa_card"], path)
    assert words == "its header lacks the columns Posted Date, Reference Number and Payee that this format reads"


def test_last_years_layout_is_refused_by_the_column_it_lacks(tmp_path):
    path = csv_file(tmp_path, "Posted Date,Payee,Address,Amount\n03/28/2030,SAMPLE GROCER,SAMPLETOWN ST,-54.18\n")
    words = refusal(registry.presets()["bofa_card"], path)
    assert words == "its header lacks the column Reference Number that this format reads"


@pytest.mark.parametrize("text", [
    '"03/28/2030","-54.18","*","","TRADER JOE\'S #000 SAMPLETOWN"\n',                       # a headerless export
    "<!DOCTYPE html>\n<html><head><title>Sign in</title></head><body>Session expired</body></html>\n",
])
def test_a_file_with_no_header_line_is_refused_without_quoting_it(tmp_path, text):
    words = refusal(registry.presets()["bofa_card"], csv_file(tmp_path, text))
    assert words == ("its first line is not the header this format starts with, which names the columns Posted Date, "
                     "Reference Number, Payee and Amount")


def test_a_file_with_no_bytes_or_a_header_alone_reads_as_no_rows(tmp_path):
    """As in every native format: a file with no bytes, or blank lines only, is no rows and no refusal; a balance
    preset states no balance for it either."""
    preset = preset_from(tmp_path)
    for text in ("", "\n\r\n  \n"):
        txns, stats, extra = read(preset, csv_file(tmp_path, text, "empty.csv"))
        assert (txns, stats.rows_parsed, stats.skip_reasons.keys() <= {"blank-line"}, extra) == ([], 0, True, {})
    txns, stats, _ = read(preset, csv_file(tmp_path, "Date,Description,Amount\n", "header.csv"))
    assert txns == [] and stats.skip_reasons == {"header": 1}
    balanced = preset_from(tmp_path, order="newest_first", **BALANCE)
    assert read(balanced, csv_file(tmp_path, "", "empty.csv"))[2] == {}
    assert read(balanced, csv_file(tmp_path, "Date,Description,Amount,Balance\n", "header.csv"))[2] == {}


def test_a_file_with_no_bytes_under_a_preset_format_adds_no_check(tmp_path):
    """Through the pipeline: a card account on the shipped card preset with an empty file and a file of rows"""
    d = tmp_path / "cfg"
    d.mkdir()
    (tmp_path / "src").mkdir()
    account = {"name": "Sample Card", "institution": "Sample Bank", "owner": "Alex", "kind": "credit-card",
               "parser": "bofa_card", "glob": "card-*.csv"}
    (d / "accounts.yaml").write_text(yaml.safe_dump(
        {"source_dir": "src", "simplefin": {"store_dir": "simplefin"}, "accounts": {"demo_card_0000": account}}))
    write_household(d)
    (tmp_path / "src" / "card-1.csv").write_bytes(b"")
    (tmp_path / "src" / "card-2.csv").write_text(CARD_HEADER + "03/02/2030,1,SAMPLE CAFE,SAMPLETOWN ST,-4.00\n")
    p = _ingested(tmp_path, Config(d, source_dir=str(tmp_path / "src")))
    assert [c.name for c in p.checks if c.name.startswith("import.")] == []
    assert [(t.source_file, t.amount_cents) for t in p.txns] == [("card-2.csv", -4_00)]
    assert [(s.source_file, s.rows_total) for s in p.file_stats] == [("card-1.csv", 0), ("card-2.csv", 2)]


def test_the_header_is_matched_trimmed_and_case_folded_and_extra_columns_are_fine(tmp_path):
    path = csv_file(tmp_path, " amount ,Extra, DESCRIPTION,date\n-4.00,x,SAMPLE CAFE,01/02/2030\n"
                              "Amount,Extra,Description,Date\n-5.00,y,SAMPLE GROCER,01/03/2030\n")
    txns, stats, _ = read(preset_from(tmp_path), path)
    assert [(t.date, t.description_raw, t.amount_cents) for t in txns] == [
        ("2030-01-02", "SAMPLE CAFE", -4_00), ("2030-01-03", "SAMPLE GROCER", -5_00)]
    assert stats.skip_reasons == {"header": 1, "repeated-header": 1}


def test_search_rows_lets_that_many_lines_come_before_the_header(tmp_path):
    text = "Sample Bank account activity\n\nAccount ending 0000\nDate,Description,Amount\n01/02/2030,SAMPLE CAFE,-4.00\n"
    path = csv_file(tmp_path, text)
    txns, stats, _ = read(preset_from(tmp_path, search_rows=2), path)
    assert [t.description_raw for t in txns] == ["SAMPLE CAFE"]
    assert stats.skip_reasons == {"preamble": 2, "blank-line": 1, "header": 1}
    words = refusal(preset_from(tmp_path, search_rows=1), path)
    assert words == ("none of its first 2 lines is the header this format starts with, which names the columns Date, "
                     "Description and Amount")
    assert refusal(preset_from(tmp_path), path) == ("its first line is not the header this format starts with, which "
                                                    "names the columns Date, Description and Amount")


def test_a_header_holding_one_of_the_presets_columns_twice_is_refused(tmp_path):
    path = csv_file(tmp_path, "Date,Description,Amount,Amount\n03/01/2030,SAMPLE CAFE,5.00,-5.00\n")
    assert refusal(preset_from(tmp_path), path) == ("its header names the column Amount more than once, so this format "
                                                    "cannot tell which to read")


# ------------------------------------------------------------------ row width and quotes

@pytest.mark.parametrize("row", [
    "03/02/2030,2,CORNER CAFE,SUITE 5, 45,-20.00",        # an unquoted comma in the address shifts the amount
    '03/02/2030,14,"JOE"S PIZZA, 2",,-20.00',             # a quote inside a quoted field ends it early
    "03/02/2030,15,SAMPLE CAFE,-20.00",                   # a cell short: the amount sits under Address
])
def test_a_row_of_another_width_than_the_header_is_never_read(tmp_path, row):
    """Each would otherwise give a wrong amount or a wrong sign (+45.00 for a -20.00 purchase), or pass as a row with no
    amount; it is skipped as unreadable, counted, and the file's other rows read."""
    good = "".join(f"03/{d:02d}/2030,{d},SAMPLE SHOP {d},SAMPLETOWN ST,-1.00\n" for d in range(3, 6))
    path = csv_file(tmp_path, CARD_HEADER + "03/01/2030,1,ACME HARDWARE,\"100 MAIN ST, SUITE 5\",-12.34\n"
                    + row + "\n" + good)
    txns, stats, _ = read(registry.presets()["bofa_card"], path)
    assert [(t.description_raw, t.amount_cents) for t in txns] == [
        ("ACME HARDWARE", -12_34), ("SAMPLE SHOP 3", -1_00), ("SAMPLE SHOP 4", -1_00), ("SAMPLE SHOP 5", -1_00)]
    assert stats.skip_reasons == {"header": 1, "unparseable-row": 1}


def test_empty_cells_past_the_header_are_fine(tmp_path):
    """A bank that ends every row with a comma writes an empty cell past the header: nothing is lost"""
    path = csv_file(tmp_path, "Date,Description,Amount,\n03/01/2030,SAMPLE CAFE,-4.00,,\n03/02/2030,SAMPLE GROCER,-5.00\n")
    txns, stats, _ = read(preset_from(tmp_path), path)
    assert [t.amount_cents for t in txns] == [-4_00, -5_00] and stats.skip_reasons == {"header": 1}


@pytest.mark.parametrize("where", ["first", "mid-file"])
def test_a_quote_that_is_never_closed_refuses_the_file(tmp_path, where):
    """The CSV reader folds every line after an unclosed quote into one record: that record is unreadable and spans many
    lines, so the file is refused instead of losing the rows after it as one skipped row"""
    rows = "".join(f"03/{d:02d}/2030,{d},SHOP {d},SAMPLETOWN ST,-{d}.00\n" for d in range(2, 12))
    broken = '03/12/2030,1,"OPEN QUOTE CAFE,SAMPLETOWN ST,-1.00\n'
    text = CARD_HEADER + (broken + rows if where == "first" else rows + broken + rows)
    assert refusal(registry.presets()["bofa_card"], csv_file(tmp_path, text)) == UNCLOSED_QUOTE
    assert UNCLOSED_QUOTE == "a quote in it is never closed, so the lines after it run together"
    small = csv_file(tmp_path, 'Date,Description,Amount\n03/01/2030,"abc,1\n03/02/2030,def,2\n', "small.csv")
    assert refusal(preset_from(tmp_path), small) == UNCLOSED_QUOTE


def test_a_quoted_field_holding_line_breaks_reads(tmp_path):
    path = csv_file(tmp_path, CARD_HEADER + '03/01/2030,1,"SAMPLE\nCAFE","100 MAIN ST\nSUITE 5\nSAMPLETOWN",-4.00\n'
                                            "03/02/2030,2,SAMPLE GROCER,SAMPLETOWN ST,-5.00\n")
    txns, stats, _ = read(registry.presets()["bofa_card"], path)
    assert [(t.description_raw, t.amount_cents) for t in txns] == [("SAMPLE CAFE", -4_00), ("SAMPLE GROCER", -5_00)]
    assert stats.skip_reasons == {"header": 1}


def test_a_field_over_the_csv_readers_limit_is_a_file_that_is_not_csv(tmp_path):
    path = csv_file(tmp_path, 'Date,Description,Amount\n03/01/2030,"' + "x" * 200_000 + '",1\n')
    with pytest.raises(csv.Error) as caught:
        preset_from(tmp_path).parse(path, ACCOUNT)
    assert registry.refusal(caught.value) == "it is not CSV text this format can read"


def test_the_trailing_pattern_reads_the_first_1000_characters_of_a_row(tmp_path):
    preset = preset_from(tmp_path, skip={"trailing_rows_matching": "TOTAL$"})
    long_total = "x" * 996 + ",TOTAL"                      # 1,002 characters: the match would end past the first 1,000
    path = csv_file(tmp_path, f"Date,Description,Amount\n03/01/2030,SAMPLE CAFE,-4.00\n{long_total}\nGrand,TOTAL\n")
    txns, stats, _ = read(preset, path)
    assert [t.description_raw for t in txns] == ["SAMPLE CAFE"]
    # the long row is not taken for a trailing one, so it is read as a row (too short a one); the short one is
    assert stats.skip_reasons == {"header": 1, "unparseable-row": 1, "trailing-row": 1}


# ------------------------------------------------------------------ detect: headerless

def test_the_synthetic_headerless_sample_reads():
    preset = synthetic_headerless()
    txns, stats, extra = read(preset, SAMPLES / "sample_headerless" / "activity.csv")
    assert len(txns) == 12 and stats.skip_reasons == {}
    assert (txns[0].date, txns[0].description_raw, txns[0].amount_cents) == (
        "2030-03-28", "TRADER JOE'S #000 SAMPLETOWN", -54_18)
    assert [t.amount_cents for t in txns if t.amount_cents > 0] == [2400_00, 32_99]
    assert "PANERA BREAD #000, SAMPLETOWN" in {t.description_raw for t in txns}
    assert stats.sum_cents == 618_16 and extra == {}


@pytest.mark.parametrize(("first", "words"), [
    ('"03/28/2030","-54.18","*","","SAMPLE GROCER","x"', "its first line has 6 cells, and this format's lines have 5"),
    ('"03/28/2030","-54.18","","","SAMPLE GROCER"', 'its first line\'s cell 3 does not hold "*" as this format\'s '
                                                    "lines do"),
    ("Posted Date,Reference Number,Payee,Address,Amount", 'its first line\'s cell 3 does not hold "*" as this '
                                                          "format's lines do"),
])
def test_a_headerless_preset_refuses_a_file_of_another_shape(tmp_path, first, words):
    path = csv_file(tmp_path, first + '\n"03/27/2030","-6.45","*","","SAMPLE CAFE"\n')
    assert refusal(synthetic_headerless(), path) == words


def test_a_later_row_of_another_shape_is_skipped_and_an_empty_headerless_file_has_no_rows(tmp_path):
    path = csv_file(tmp_path, '"03/28/2030","-54.18","*","","SAMPLE GROCER"\n"03/27/2030","-6.45","*",""\n'
                              '\n"03/26/2030","-6.45","*","","SAMPLE CAFE"\n')
    txns, stats, _ = read(synthetic_headerless(), path)
    assert [t.description_raw for t in txns] == ["SAMPLE GROCER", "SAMPLE CAFE"]
    assert stats.skip_reasons == {"unparseable-row": 1, "blank-line": 1}
    txns, stats, _ = read(synthetic_headerless(), csv_file(tmp_path, "", "empty.csv"))
    assert txns == [] and stats.rows_total == 0


def test_every_row_of_a_headerless_file_must_hold_its_constant_cells(tmp_path):
    """The marker is checked on every row, not the first alone: a later five-cell row without it is no row of this
    layout, so it is skipped with its own reason and counted, never read; a file most of whose rows lack it is refused
    as one whose columns are laid out another way"""
    preset = registry.presets()["wells_fargo"]
    path = csv_file(tmp_path, '"03/28/2030","-54.18","*","","SAMPLE GROCER"\n'
                              '"03/27/2030","-6.45","","","SAMPLE CAFE"\n'
                              '"03/26/2030","-7.10"," * ","1001","SAMPLE BOOKSHOP"\n'
                              '"03/25/2030","-2.00","**","","SAMPLE PHARMACY"\n')
    txns, stats, _ = read(preset, path)
    assert [t.description_raw for t in txns] == ["SAMPLE GROCER", "SAMPLE BOOKSHOP"]       # compared trimmed
    assert stats.skip_reasons == {"constant-mismatch": 2}
    most = csv_file(tmp_path, '"03/28/2030","-54.18","*","","SAMPLE GROCER"\n' + "".join(
        f'"03/{d:02d}/2030","-1.00","","","SAMPLE SHOP {d}"\n' for d in range(20, 23)), "most.csv")
    assert refusal(preset, most) == COLUMNS_DO_NOT_FIT


def test_a_header_line_above_a_headerless_file_is_one_unreadable_row(tmp_path):
    """A header line of the right width with the marker in its place passes the first-line test; it has no date, so it
    is skipped as such, counted, and the rows under it read."""
    path = csv_file(tmp_path, "Date,Amount,*,Ref,Desc\n03/01/2030,1.00,*,,SAMPLE CAFE\n03/02/2030,2.00,*,,SAMPLE GROCER\n")
    txns, stats, _ = read(synthetic_headerless(), path)
    assert [t.description_raw for t in txns] == ["SAMPLE CAFE", "SAMPLE GROCER"]
    assert stats.skip_reasons == {"unparseable-date": 1}


# ------------------------------------------------------------------ amounts

def test_a_signed_amount_is_read_as_written_or_turned_round(tmp_path):
    path = csv_file(tmp_path, "Date,Description,Amount\n01/02/2030,SAMPLE CAFE,4.00\n01/03/2030,PAYMENT,-50.00\n")
    as_is, _, _ = read(preset_from(tmp_path), path)
    inverted, _, _ = read(preset_from(tmp_path, amount={"column": "Amount", "sign": "invert"}), path)
    assert [t.amount_cents for t in as_is] == [4_00, -50_00]
    assert [t.amount_cents for t in inverted] == [-4_00, 50_00]      # a card export whose purchase is positive


DEBIT_CREDIT = {"detect": {"header": ["Date", "Description", "Debit", "Credit"]},
                "amount": {"debit": "Debit", "credit": "Credit"}}


@pytest.mark.parametrize(("empty_amount", "skipped"), [
    (True, {"header": 1, "both-debit-and-credit": 1, "no-amount": 1, "signed-debit-or-credit": 1,
            "unparseable-row": 1}),
    (False, {"header": 1, "both-debit-and-credit": 1, "signed-debit-or-credit": 1, "unparseable-row": 2}),
])
def test_debit_is_money_out_and_credit_money_in(tmp_path, empty_amount, skipped):
    """Both cells holding an amount, or a signed value in either, cannot say which way the money went: skipped with a
    reason. A zero beside an amount is the empty side. A row with neither is skipped as having no amount when the
    preset allows that, else as a row it cannot read."""
    preset = preset_from(tmp_path, skip={"empty_amount": empty_amount}, **DEBIT_CREDIT)
    path = csv_file(tmp_path, "Date,Description,Debit,Credit\n"
                              "01/02/2030,SAMPLE GROCER,40.00,\n"
                              "01/03/2030,SAMPLE EMPLOYER PAYROLL,,\"2,400.00\"\n"
                              "01/04/2030,SAMPLE BOTH,5.00,5.00\n"
                              "01/05/2030,SAMPLE NEITHER,,\n"
                              "01/06/2030,SAMPLE SIGNED,-7.00,\n"
                              "01/07/2030,SAMPLE GARBLED,4x.00,\n"
                              "01/08/2030,SAMPLE ZERO CREDIT,12.00,0.00\n"
                              "01/09/2030,SAMPLE ZERO DEBIT,0,50.00\n")
    txns, stats, _ = read(preset, path)
    assert [(t.description_raw, t.amount_cents) for t in txns] == [
        ("SAMPLE GROCER", -40_00), ("SAMPLE EMPLOYER PAYROLL", 2400_00), ("SAMPLE ZERO CREDIT", -12_00),
        ("SAMPLE ZERO DEBIT", 50_00)]
    assert stats.skip_reasons == skipped


@pytest.mark.parametrize("rows", [
    "03/01/2030,SAMPLE GROCER,-12.00,\n03/02/2030,SAMPLE CAFE,-3.00,\n",          # debits written signed
    "03/01/2030,SAMPLE GROCER,12.00,4.00\n03/02/2030,SAMPLE CAFE,3.00,1.00\n",     # both columns filled
])
def test_a_file_whose_debit_and_credit_cells_mostly_cannot_be_read_is_refused(tmp_path, rows):
    """Never a file of silently skipped rows: the rule that refuses a file of unreadable dates counts these too"""
    path = csv_file(tmp_path, "Date,Description,Debit,Credit\n" + rows)
    assert refusal(preset_from(tmp_path, **DEBIT_CREDIT), path) == COLUMNS_DO_NOT_FIT
    assert COLUMNS_DO_NOT_FIT == ("most of its lines have no date or amount this format can read where it reads "
                                  "them: its columns are not laid out the way this format's are")


@pytest.mark.parametrize(("cell", "cents"), [
    ("1,234.56", 1234_56), ("1,234", 1234_00), ("$1,234.5", 1234_50), ("-$5.00", -5_00), ("(58.11)", -58_11),
    ("+44,496.74", 44496_74), ("12,50", None), ("1.234,56", None), ("12%", None), ("(-12.00)", None),
    ("12.00-", None), ("12.00 CR", None), ("1,23,456.00", None),
])
def test_a_money_cell_in_another_notation_is_never_read(tmp_path, cell, cents):
    """A decimal comma (12,50 is twelve and a half where it is written so) is not read as thousands; a percent, a sign
    inside parentheses or a trailing marker is no amount. Each such row is unparseable, counted, and the file's other
    rows read."""
    good = "".join(f"03/{d:02d}/2030,SAMPLE SHOP {d},-1.00\n" for d in range(2, 5))
    path = csv_file(tmp_path, f'Date,Description,Amount\n03/01/2030,SAMPLE CAFE,"{cell}"\n' + good)
    txns, stats, _ = read(preset_from(tmp_path), path)
    if cents is None:
        assert [t.description_raw for t in txns] == ["SAMPLE SHOP 2", "SAMPLE SHOP 3", "SAMPLE SHOP 4"]
        assert stats.skip_reasons == {"header": 1, "unparseable-row": 1}
    else:
        assert (txns[0].description_raw, txns[0].amount_cents) == ("SAMPLE CAFE", cents)


def test_a_decimal_comma_file_is_refused(tmp_path):
    path = csv_file(tmp_path, 'Date,Description,Amount\n03/01/2030,SAMPLE CAFE,"12,50"\n'
                              '03/02/2030,SAMPLE GROCER,"1.234,56"\n03/03/2030,SAMPLE SHOP,"-3,10"\n')
    assert refusal(preset_from(tmp_path), path) == COLUMNS_DO_NOT_FIT


# ------------------------------------------------------------------ the description

def test_the_description_joins_its_columns_and_squashes_whitespace(tmp_path):
    preset = preset_from(tmp_path, detect={"header": ["Date", "Description", "Memo", "Amount"]},
                         description={"columns": ["Description", "Memo"], "join": " - "})
    path = csv_file(tmp_path, "Date,Description,Memo,Amount\n"
                              '01/02/2030,"SAMPLE   GROCER","  aisle\n 3 ",-4.00\n'
                              "01/03/2030,SAMPLE CAFE,,-5.00\n"
                              "01/04/2030,,,-6.00\n")
    txns, _, _ = read(preset, path)
    assert [t.description_raw for t in txns] == ["SAMPLE GROCER - aisle 3", "SAMPLE CAFE", ""]
    spaced, _, _ = read(preset_from(tmp_path, detect={"header": ["Date", "Description", "Memo", "Amount"]},
                                    description={"columns": ["Description", "Memo"]}), path)
    assert spaced[0].description_raw == "SAMPLE GROCER aisle 3"                  # one space by default


def test_the_bank_type_and_category_columns_are_kept(tmp_path):
    preset = preset_from(tmp_path, detect={"header": ["Date", "Description", "Amount", "Type", "Category"]},
                         bank_type="Type", bank_category="Category")
    path = csv_file(tmp_path, "Date,Description,Amount,Type,Category\n01/02/2030,SAMPLE CAFE,-4.00, Sale ,Dining\n"
                              "01/03/2030,SAMPLE GROCER,-5.00,,\n")
    txns, _, _ = read(preset, path)
    assert [(t.bank_type, t.bank_category) for t in txns] == [("Sale", "Dining"), (None, None)]


# ------------------------------------------------------------------ skips

def test_pending_trailing_and_blank_rows_are_skipped_and_counted(tmp_path):
    preset = preset_from(tmp_path, detect={"header": ["Date", "Status", "Description", "Amount"]},
                         skip={"pending": {"column": "Status", "equals": "pending"},
                               "trailing_rows_matching": "(?i)total"})
    path = csv_file(tmp_path, "Date,Status,Description,Amount\n"
                              "01/02/2030,Posted,SAMPLE CAFE,-4.00\n"
                              "01/03/2030,Posted,TOTAL WINE SAMPLETOWN,-20.00\n"      # matches, and is not trailing
                              "01/04/2030, PENDING ,SAMPLE GROCER,-36.00\n"
                              "\n"
                              "01/05/2030,Posted,SAMPLE BOOKSHOP,-15.00\n"
                              "Total,,,-75.00\n"
                              "\n"
                              "Total pending,,,-36.00\n")
    txns, stats, _ = read(preset, path)
    assert [t.description_raw for t in txns] == ["SAMPLE CAFE", "TOTAL WINE SAMPLETOWN", "SAMPLE BOOKSHOP"]
    assert stats.skip_reasons == {"header": 1, "pending": 1, "blank-line": 2, "trailing-row": 2}
    assert stats.sum_cents == -39_00


# ------------------------------------------------------------------ dates

def test_dates_follow_the_stated_patterns_and_an_unreadable_one_is_skipped(tmp_path):
    preset = preset_from(tmp_path, date={"column": "Date", "format": ["%m/%d/%Y", "%Y-%m-%d"]})
    path = csv_file(tmp_path, "Date,Description,Amount\n1/2/2030,SAMPLE CAFE,-4.00\n2030-01-03,SAMPLE GROCER,-5.00\n"
                              "13/45/2030,SAMPLE BOOKSHOP,-6.00\n01/04/2030,SAMPLE PHARMACY,-7.00\n")
    txns, stats, _ = read(preset, path)
    assert [t.date for t in txns] == ["2030-01-02", "2030-01-03", "2030-01-04"]
    assert stats.skip_reasons == {"header": 1, "unparseable-date": 1}


def test_half_the_dates_unreadable_is_read_and_more_than_half_is_refused(tmp_path):
    preset = preset_from(tmp_path)
    half = csv_file(tmp_path, "Date,Description,Amount\n25.01.2030,A,-1.00\n26.01.2030,B,-1.00\n"
                              "01/27/2030,C,-1.00\n01/28/2030,D,-1.00\n", "half.csv")
    txns, stats, _ = read(preset, half)
    assert len(txns) == 2 and stats.skip_reasons["unparseable-date"] == 2
    most = csv_file(tmp_path, "Date,Description,Amount\n25/01/2030,A,-1.00\n26/01/2030,B,-1.00\n"
                              "01/02/2030,C,-1.00\n", "most.csv")                     # day first, in a month-first preset
    assert refusal(preset, most) == COLUMNS_DO_NOT_FIT


@pytest.mark.parametrize(("formats", "pair"), [
    (["%m/%d/%Y", "%d/%m/%Y"], "'%m/%d/%Y' and '%d/%m/%Y'"),     # 03/04/2030: March 4 or April 3
    (["%d.%m.%Y", "%Y-%m-%d", "%m.%d.%Y"], "'%d.%m.%Y' and '%m.%d.%Y'"),
])
def test_a_list_of_date_patterns_that_read_one_text_as_two_dates_is_refused(tmp_path, formats, pair):
    with pytest.raises(ConfigError) as caught:
        preset_from(tmp_path, date={"column": "Date", "format": formats})
    [problem] = caught.value.problems
    assert problem.path == "date.format" and problem.message.startswith(f"{pair} read the same text as two different "
                                                                        "dates (")
    assert problem.fix == "keep only the pattern this export writes"
    preset_from(tmp_path, date={"column": "Date", "format": ["%m/%d/%Y", "%Y-%m-%d", "%b %d, %Y"]})   # never confused


# ------------------------------------------------------------------ encodings and line ends

def test_a_cp1252_file_with_an_accented_merchant_reads_through_the_second_encoding(tmp_path):
    path = csv_file(tmp_path, "Date,Description,Amount\n01/02/2030,CAFÉ SAMPLE,-4.00\n", encoding="cp1252")
    txns, _, _ = read(preset_from(tmp_path), path)
    assert [t.description_raw for t in txns] == ["CAFÉ SAMPLE"]
    assert refusal(preset_from(tmp_path, encoding=["utf-8"]), path) == \
        "its bytes are not text in an encoding this format reads"


def test_a_byte_order_mark_is_dropped_before_the_header(tmp_path):
    path = csv_file(tmp_path, "﻿Date,Description,Amount\n01/02/2030,SAMPLE CAFE,-4.00\n")
    txns, _, _ = read(preset_from(tmp_path), path)
    assert [t.description_raw for t in txns] == ["SAMPLE CAFE"]


def test_a_utf16_file_is_refused_unless_the_preset_lists_utf16(tmp_path):
    path = csv_file(tmp_path, "Date,Description,Amount\r\n01/02/2030,SAMPLE CAFE,-4.00\r\n", encoding="utf-16")
    assert refusal(preset_from(tmp_path), path) == "its bytes are not text in an encoding this format reads"
    txns, _, _ = read(preset_from(tmp_path, encoding=["utf-16"]), path)
    assert [t.description_raw for t in txns] == ["SAMPLE CAFE"]


def test_the_encoding_is_judged_on_the_first_64_kb_and_a_later_byte_falls_to_the_next(tmp_path):
    """A character split by the 64 KB mark is still UTF-8; a cp1252 byte past the mark of an ASCII start sends the read
    back to the start in the next encoding, which reads that start the same; a preset with no next encoding refuses the
    file."""
    head = "Date,Description,Amount\n"

    def filler(n: int) -> str:
        return "".join(f"01/02/2030,SAMPLE CAFE {i:05d},-1.00\n" for i in range(n))     # 35 bytes a row

    start = head + filler(1871) + "01/03/2030,"                 # leaves a description well under 500 characters
    row = "X" * (65_535 - len(start)) + "É,-2.00\n"              # É's first byte ends the 64 KB, its second follows
    split = csv_file(tmp_path, start + row, "split.csv")
    assert split.read_bytes()[65_535:65_537] == "É".encode()
    txns, _, _ = read(preset_from(tmp_path), split)
    assert txns[-1].description_raw.endswith("XÉ")
    late = csv_file(tmp_path, head + filler(2000) + "01/04/2030,CAFÉ SAMPLE,-3.00\n", "late.csv", encoding="cp1252")
    assert late.stat().st_size > 65_536 + 1000
    txns, stats, _ = read(preset_from(tmp_path), late)
    assert txns[-1].description_raw == "CAFÉ SAMPLE" and stats.rows_parsed == 2001
    assert refusal(preset_from(tmp_path, encoding=["utf-8-sig"]), late) == MIXED_TEXT


def test_a_later_byte_never_changes_a_row_read_before_it(tmp_path):
    """A UTF-8 start with an accented merchant, a cp1252 byte past 64 KB: read again as cp1252 the merchant would become
    another description (another uid, so two overlapping exports would no longer pair), so the file is refused; the
    same start with no later byte reads as UTF-8."""
    start = CARD_HEADER + "03/01/2030,1,CAFÉ SAMPLE,SAMPLETOWN ST,-4.00\n" + "".join(
        f"03/02/2030,{i},FILLER STORE {i},SAMPLETOWN ST,-1.00\n" for i in range(1500))
    clean = tmp_path / "clean.csv"
    clean.write_bytes(start.encode("utf-8"))
    assert clean.stat().st_size > 65_536
    preset = registry.presets()["bofa_card"]
    txns, _, _ = read(preset, clean)
    assert txns[0].description_raw == "CAFÉ SAMPLE"
    stray = tmp_path / "stray.csv"
    stray.write_bytes(start.encode("utf-8") + "03/03/2030,9,CRÈME SHOP,SAMPLETOWN ST,-2.00\n".encode("cp1252"))
    assert refusal(preset, stray) == MIXED_TEXT
    assert MIXED_TEXT == ("its text is not in any one encoding this format reads: a part after its start is written in "
                          "another one")
    assert NOT_TEXT == "its bytes are not text in an encoding this format reads"


def test_carriage_return_line_ends_and_a_quoted_newline_read(tmp_path):
    path = csv_file(tmp_path, 'Date,Description,Amount\r01/02/2030,"SAMPLE\rCAFE",-4.00\r01/03/2030,SAMPLE GROCER,-5.00\r')
    txns, _, _ = read(preset_from(tmp_path), path)
    assert [t.description_raw for t in txns] == ["SAMPLE CAFE", "SAMPLE GROCER"]


# ------------------------------------------------------------------ the balance column and the running-balance check

BALANCE = {"detect": {"header": ["Date", "Description", "Amount", "Balance"]}, "balance": "Balance"}


def test_running_balances_come_in_the_order_rows_happened_and_the_newest_is_the_balance(tmp_path):
    newest_first = csv_file(tmp_path, "Date,Description,Amount,Balance\n"
                                      "01/05/2030,SAMPLE CAFE,-4.00,96.00\n"
                                      "01/05/2030,SAMPLE EMPLOYER PAYROLL,50.00,100.00\n"
                                      "01/02/2030,SAMPLE GROCER,-50.00,50.00\n", "newest.csv")
    _, _, extra = read(preset_from(tmp_path, order="newest_first", **BALANCE), newest_first)
    assert extra["running_balances"] == [("2030-01-02", -50_00, 50_00), ("2030-01-05", 50_00, 100_00),
                                         ("2030-01-05", -4_00, 96_00)]
    assert extra["latest_balance"] == ("2030-01-05", 96_00)     # of the newest day's rows, the latest in file order
    oldest_first = csv_file(tmp_path, "Date,Description,Amount,Balance\n"
                                      "01/02/2030,SAMPLE GROCER,-50.00,50.00\n"
                                      "01/05/2030,SAMPLE EMPLOYER PAYROLL,50.00,100.00\n"
                                      "01/05/2030,SAMPLE CAFE,-4.00,96.00\n", "oldest.csv")
    _, _, extra = read(preset_from(tmp_path, order="oldest_first", **BALANCE), oldest_first)
    assert extra["running_balances"][-1] == ("2030-01-05", -4_00, 96_00)
    assert extra["latest_balance"] == ("2030-01-05", 96_00)


def test_a_skipped_row_that_moved_the_balance_breaks_the_chain_and_one_that_did_not_does_not(tmp_path):
    preset = preset_from(tmp_path, order="oldest_first", detect={"header": ["Date", "Status", "Description", "Amount",
                                                                            "Balance"]},
                         balance="Balance", skip={"pending": {"column": "Status", "equals": "pending"},
                                                  "empty_amount": True})
    path = csv_file(tmp_path, "Date,Status,Description,Amount,Balance\n"
                              "01/02/2030,Posted,SAMPLE GROCER,-50.00,50.00\n"
                              "01/03/2030,Posted,SAMPLE NOTE,,50.00\n"
                              "01/04/2030,Pending,SAMPLE CAFE,-4.00,46.00\n"
                              "01/05/2030,Posted,SAMPLE BOOKSHOP,-6.00,40.00\n"
                              "01/06/2030,Posted,SAMPLE PHARMACY,-5.00,\n")
    _, _, extra = read(preset, path)
    assert extra["running_balances"] == [("2030-01-02", -50_00, 50_00), None, ("2030-01-05", -6_00, 40_00),
                                         ("2030-01-06", -5_00, None)]
    assert extra["latest_balance"] == ("2030-01-05", 40_00)


@pytest.mark.parametrize(("third", "chain", "status"), [
    ("03/03/2030,SAMPLE CAFE,-5.00,95.00", [("2030-03-03", -5_00, 95_00)], "pass"),
    ("03/03/2030,SAMPLE CAFE,-5.00,90.00", [("2030-03-03", -5_00, 90_00)], "fail"),
    ("03/03/2030,SAMPLE CAFE,-5.00,abc", [("2030-03-03", -5_00, None)], "pass"),      # a balance it cannot read
    ("2030-03-03,SAMPLE CAFE,-5.00,95.00", [None], "pass"),                         # a date it cannot read
])
def test_a_balance_or_date_that_cannot_be_read_restarts_the_chain_and_never_fails_it(tmp_path, third, chain, status):
    preset = preset_from(tmp_path, order="newest_first", **BALANCE)
    path = csv_file(tmp_path, f"Date,Description,Amount,Balance\n{third}\n03/02/2030,SAMPLE GROCER,-10.00,100.00\n"
                              "03/01/2030,SAMPLE EMPLOYER PAYROLL,10.00,110.00\n")
    _, _, extra = read(preset, path)
    assert extra["running_balances"] == [("2030-03-01", 10_00, 110_00), ("2030-03-02", -10_00, 100_00), *chain]
    assert reconcile.rollforward_check(ACCOUNT, [extra["running_balances"]]).status == status


def test_the_rollforward_check_passes_fails_by_date_and_counts_what_it_cannot_follow():
    chain = [("2030-01-02", -50_00, 50_00), ("2030-01-05", 50_00, 100_00), ("2030-01-05", -4_00, 96_00)]
    check = reconcile.rollforward_check(ACCOUNT, [chain])
    assert (check.name, check.status) == (f"import.rollforward.{ACCOUNT}", "pass")
    assert check.detail == "Each row's running balance is the one before it plus the row's amount (2 rows followed)."
    restarted = reconcile.rollforward_check(ACCOUNT, [[None, *chain[:2], None, chain[2]]])
    assert restarted.status == "pass"
    assert restarted.detail == ("Each row's running balance is the one before it plus the row's amount (1 row "
                                "followed). The check restarts after 2 rows it cannot follow (a skipped row that "
                                "moved the balance, or a row with no balance).")
    broken = [("2030-01-02", -50_00, 50_00), ("2030-01-04", -7_00, 41_00), ("2030-01-06", -1_00, 40_00),
              ("2030-01-09", -3_00, 36_00)]
    later = [("2030-02-01", -1_00, 10_00), ("2030-02-03", -1_00, 8_00)]       # a file read first, breaking later
    failed = reconcile.rollforward_check(ACCOUNT, [later, broken])
    assert (failed.name, failed.status) == (f"import.rollforward.{ACCOUNT}", "fail")
    assert failed.detail == ("The running balance in this account's statement files does not follow from its rows: it "
                             "first breaks on 2030-01-04, and 3 of the 4 rows checked break it. A row is missing or "
                             "altered in a file: download it again from the bank.")
    for amount in ("41", "43", "7.00", "50"):
        assert amount not in failed.name + failed.detail
    alone = reconcile.rollforward_check(ACCOUNT, [[("2030-01-02", -50_00, 50_00)], [None]])
    assert alone.status == "info"
    assert alone.detail == ("No row's running balance can be followed from the one before it: the files have too few "
                            "rows with a balance. The check restarts after 1 row it cannot follow (a skipped row that "
                            "moved the balance, or a row with no balance).")


def _balance_household(tmp_path: Path, kind: str, monkeypatch) -> Config:
    preset = preset_from(tmp_path, order="newest_first", **BALANCE)
    monkeypatch.setitem(pipeline.TXN_PARSERS, preset.id, preset.parse)
    d = tmp_path / "cfg"
    d.mkdir()
    (tmp_path / "src").mkdir()
    account = {"name": "Sample Account", "institution": "Sample Bank", "owner": "Alex", "kind": kind,
               "parser": preset.id, "glob": "sample-*.csv"}
    (d / "accounts.yaml").write_text(yaml.safe_dump(
        {"source_dir": "src", "simplefin": {"store_dir": "simplefin"}, "accounts": {ACCOUNT: account}}))
    write_household(d)
    return Config(d, source_dir=str(tmp_path / "src"))


def _ingested(tmp_path: Path, cfg: Config):
    p = isolated_pipeline(tmp_path / "state", [], cfg=cfg, windows={})
    p.parse_transactions()
    return p


def _checks(p, prefix: str) -> list[tuple[str, str]]:
    return [(c.name, c.status) for c in p.checks if c.name.startswith(prefix)]


def test_a_checking_accounts_files_raise_the_running_balance_check_and_book_the_newest_balance(tmp_path, monkeypatch):
    cfg = _balance_household(tmp_path, "checking", monkeypatch)
    (tmp_path / "src" / "sample-1.csv").write_text("Date,Description,Amount,Balance\n"
                                                   "01/05/2030,SAMPLE CAFE,-4.00,96.00\n"
                                                   "01/02/2030,SAMPLE GROCER,-50.00,100.00\n")
    (tmp_path / "src" / "sample-2.csv").write_text("Date,Description,Amount,Balance\n"
                                                   "02/09/2030,SAMPLE BOOKSHOP,-15.00,71.00\n"
                                                   "02/04/2030,SAMPLE PHARMACY,-11.00,85.00\n")
    p = _ingested(tmp_path, cfg)
    assert _checks(p, "import.") == [(f"import.rollforward.{ACCOUNT}", "fail")]
    [check] = [c for c in p.checks if c.name.startswith("import.rollforward.")]
    assert check.detail.startswith("The running balance in this account's statement files does not follow from its "
                                   "rows: it first breaks on 2030-02-09, and 1 of the 2 rows checked breaks it.")
    assert p.latest_balances == {ACCOUNT: ("2030-02-09", 71_00)}            # booked whatever the check says
    assert [t.description_raw for t in p.txns] == ["SAMPLE CAFE", "SAMPLE GROCER", "SAMPLE BOOKSHOP",
                                                   "SAMPLE PHARMACY"]


def test_a_card_accounts_file_balance_raises_no_running_balance_check(tmp_path, monkeypatch):
    """A card's running balance has its own sign and meaning, so it is ignored as a whole: no check follows it."""
    cfg = _balance_household(tmp_path, "credit-card", monkeypatch)
    (tmp_path / "src" / "sample-1.csv").write_text("Date,Description,Amount,Balance\n"
                                                   "01/05/2030,SAMPLE CAFE,-4.00,54.00\n"
                                                   "01/02/2030,SAMPLE GROCER,-50.00,50.00\n")
    p = _ingested(tmp_path, cfg)
    assert _checks(p, "import.") == [(f"import.balance_ignored.{ACCOUNT}", "info")]
    assert p.latest_balances == {}


def test_a_passing_chain_across_two_files(tmp_path, monkeypatch):
    cfg = _balance_household(tmp_path, "savings", monkeypatch)
    (tmp_path / "src" / "sample-1.csv").write_text("Date,Description,Amount,Balance\n"
                                                   "01/05/2030,SAMPLE CAFE,-4.00,96.00\n"
                                                   "01/02/2030,SAMPLE GROCER,-50.00,100.00\n")
    (tmp_path / "src" / "sample-2.csv").write_text("Date,Description,Amount,Balance\n"
                                                   "02/09/2030,SAMPLE BOOKSHOP,-15.00,70.00\n"
                                                   "02/04/2030,SAMPLE PHARMACY,-11.00,85.00\n")
    p = _ingested(tmp_path, cfg)
    [check] = [c for c in p.checks if c.name.startswith("import.rollforward.")]
    assert (check.status, check.detail) == ("pass", "Each row's running balance is the one before it plus the row's "
                                                    "amount (2 rows followed).")


# ------------------------------------------------------------------ loading a preset

def test_load_preset_reports_every_problem_at_once(tmp_path):
    path = tmp_path / "presets" / "sample_bad.yaml"
    path.parent.mkdir()
    path.write_text(yaml.safe_dump({
        "id": "sample_other", "label": "", "kind": "debit", "evidence": "a hunch", "colour": "blue",
        "encoding": ["utf-8", "no-such-codec"], "search_rows": -1,
        "detect": {"header": ["Date", "Amount"]},
        "date": {"column": "Date", "format": "%m/%d"},
        "amount": {"column": "Amount", "sign": "detect", "debit": "Amount"},
        "description": {"columns": ["Memo"]},
        "skip": {"trailing_rows_matching": "(unclosed", "empty_amount": "yes"},
        "balance": "Amount",
    }, sort_keys=False))
    with pytest.raises(ConfigError) as caught:
        load_preset(path)
    problems = caught.value.problems
    assert {p.file for p in problems} == {"presets/sample_bad.yaml"}
    assert [p.path for p in problems] == [
        "amount", "colour", "date.format", "description.columns[0]", "encoding[1]", "evidence", "id", "kind", "label",
        "order", "search_rows", "skip.empty_amount", "skip.trailing_rows_matching", "source"]
    assert all(p.message and p.fix for p in problems)


def test_load_preset_checks_a_headerless_presets_positions(tmp_path):
    path = tmp_path / "presets" / "sample_cells.yaml"
    path.parent.mkdir()
    path.write_text(yaml.safe_dump({**BASE, "id": "sample_cells", "search_rows": 1,
                                    "detect": {"headerless": {"columns": 3, "constant": {4: "*"}}},
                                    "date": {"column": 1, "format": "%m/%d/%Y"},
                                    "amount": {"column": "Amount", "sign": "as_is"},
                                    "description": {"columns": [0, 3]}}))
    with pytest.raises(ConfigError) as caught:
        load_preset(path, allow_synthetic=True)
    assert [p.path for p in caught.value.problems] == [
        "amount.column", "description.columns[0]", "detect.headerless.constant.4", "search_rows"]


def test_a_file_that_is_not_yaml_is_one_problem(tmp_path):
    path = tmp_path / "presets" / "sample_broken.yaml"
    path.parent.mkdir()
    path.write_text("id: [unclosed\n")
    with pytest.raises(ConfigError) as caught:
        load_preset(path)
    [problem] = caught.value.problems
    assert (problem.file, problem.path) == ("presets/sample_broken.yaml", "")
    assert problem.message.startswith("not valid YAML")


# ------------------------------------------------------------------ the lock

def test_the_lock_pins_the_uids_each_shipped_preset_reads_from_its_samples():
    differences = lock_differences(lock_entries(LOCK.read_text(encoding="utf-8")), lock_blocks())
    assert differences == [], "; ".join(differences)
    assert lock_entries(LOCK.read_text(encoding="utf-8"))           # it lists something


def test_a_changed_uid_says_to_give_the_preset_a_new_id():
    current = lock_blocks()
    (preset_id, sample), uids = next(iter(current.items()))
    edited = {**current, (preset_id, sample): ["tx_0000000000000000", *uids[1:]]}
    assert lock_differences(edited, current) == [f"{CHANGED_IDENTITY} ({preset_id} on {sample})"]
    dropped = lock_entries(render(current).replace(f"{preset_id} {sample}\n", "", 1))
    assert lock_differences(dropped, current) == [f"the lock does not list {preset_id} on {sample}: append its lines "
                                                  "to the lock"]
    assert lock_entries(render(current)) == current                 # the lock's form reads back as it was written


def test_the_script_prints_only_what_the_lock_lacks_and_fails_on_a_change(tmp_path, monkeypatch, capsys):
    """So a maintainer appends its output to the lock: an entry that would change is never printed for them to paste"""
    current = lock_blocks()
    (preset_id, sample), uids = next(iter(current.items()))
    monkeypatch.setattr(sys.modules[__name__], "LOCK", tmp_path / "presets.lock")
    lock = tmp_path / "presets.lock"
    lock.write_text("# a header\n\n" + render(current))
    assert main() == 0 and capsys.readouterr().out == ""
    lock.write_text("# a header\n\n" + render({(preset_id, "older.csv"): ["tx_0000000000000000"]}))
    assert main() == 1
    printed = capsys.readouterr()
    assert printed.out == "" and printed.err.startswith(f"the lock lists {preset_id} on older.csv")
    lock.write_text("# a header\n\n")
    assert main() == 0 and capsys.readouterr().out == render(current)
    lock.write_text(render({(preset_id, sample): ["tx_0000000000000000", *uids[1:]]}))
    assert main() == 1
    printed = capsys.readouterr()
    assert printed.out == "" and printed.err == f"{CHANGED_IDENTITY} ({preset_id} on {sample})\n"


if __name__ == "__main__":
    sys.exit(main())

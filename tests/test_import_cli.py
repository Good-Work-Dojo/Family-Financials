# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""`python -m etl import detect | preview | map` (etl/import_cli.py), and the household's own presets
(config/presets/<id>.yaml) they and the config share.

`detect` needs no household: it tries every format on a file, its extension never trusted, and prints the ones that
read it, or the nearest misses with their plain reasons and the next step; exit 0 only when exactly one reads it.
`preview` reads a file with one format and prints what it would give, writing nothing; with an account it also counts
the file's rows the household's stored data already holds, by the uid the pipeline gives each. `map` asks about a
file's columns (or reads the answers from a file), builds the preset through the loader that checks every preset, reads
the file with it, and only then writes presets/<id>.yaml and prints the accounts.yaml lines to add. A household preset
is a format name of that household only: usable as `parser:` and `format:`, read by the pipeline, and refused by
`validate` when its id is a format this release reads, another preset's id or not its file's name.

Every file here is invented: the samples under tests/importers/samples/ and the files written into tmp_path."""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from conftest import ROOT, write_household

from etl import import_cli, pipeline
from etl.__main__ import main
from etl.config import Config
from etl.import_cli import (
    BANKS_DOC,
    NATIVE_BANKS,
    Bank,
    bank_row,
    banks_document,
    detect,
    glob_for,
    map_layout,
    own_presets,
    preview,
)
from etl.importers import ofx, registry
from etl.importers.csv_preset import (
    _BLOCK_KEYS,
    _KEYS,
    COLUMNS_DO_NOT_FIT,
    build_preset,
    load_preset,
)
from etl.models import FileStats, Txn
from etl.money import fmt
from etl.validate import ConfigError, validate

SAMPLES = ROOT / "tests" / "importers" / "samples"
CARD_SAMPLE = SAMPLES / "bofa_card" / "activity.csv"
HEADERLESS_SAMPLE = SAMPLES / "sample_headerless" / "activity.csv"
SYNTHETIC = ROOT / "tests" / "importers" / "synthetic" / "sample_headerless.yaml"
DOC = ROOT / "docs" / "configuration.md"
CARD = "demo_card_0000"
# the account and bank numbers the OFX samples carry: never printed
OFX_NUMBERS = ("000012345678", "000087654321", "000099999")

# a household's own preset for the card sample's layout: the columns it reads, no source or evidence (its own file)
MY_CARD = {"id": "my_card", "label": "Sample card export", "kind": "card",
           "detect": {"header": ["Posted Date", "Payee", "Amount"]},
           "date": {"column": "Posted Date", "format": "%m/%d/%Y"},
           "amount": {"column": "Amount", "sign": "as_is"}, "description": {"columns": ["Payee"]},
           "skip": {"empty_amount": True}}
# the answers that describe the card sample to `map`
CARD_ANSWERS = {"header": True, "date": "Posted Date", "amount": "Amount", "positive_is_money_in": True,
                "skip_empty_amount": True, "description": ["Payee"], "kind": "card", "label": "Sample card export"}


# ------------------------------------------------------------------ helpers

class Lines:
    """An `out` that keeps every line printed"""

    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, text: str = "") -> None:
        self.lines.extend(str(text).splitlines() or [""])

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def squashed(lines: list[str]) -> list[str]:
    return [" ".join(line.split()) for line in lines]


def rows_of(txns) -> list[tuple[str, int, str]]:
    return [(t.date, t.amount_cents, t.description_raw) for t in txns]


def card_rows() -> list[tuple[str, int, str]]:
    txns, _, _ = registry.presets()["bofa_card"].parse(CARD_SAMPLE, CARD)
    return rows_of(txns)


def write_preset(config_dir: Path, doc: dict, name: str | None = None) -> Path:
    d = config_dir / "presets"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name or doc['id']}.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")
    return path


def household(tmp_path: Path, account: dict | None = None, *, statements: dict[str, Path] | None = None) -> Path:
    """A household in the FF_HOME layout at tmp_path/home: one card account (its source keys in `account`, default
    the card sample's format) and the statement files `statements` (name -> sample) in imports/"""
    home = tmp_path / "home"
    write_household(home / "config")
    meta = {"name": "Sample Card", "institution": "Sample Bank", "owner": "Alex", "kind": "credit-card",
            **(account or {"parser": "bofa_card", "glob": "card-*.csv"})}
    (home / "config" / "accounts.yaml").write_text(yaml.safe_dump({"version": 1, "accounts": {CARD: meta}},
                                                                  sort_keys=False), "utf-8")
    (home / "imports").mkdir()
    for name, sample in (statements or {}).items():
        shutil.copy(sample, home / "imports" / name)
    return home


def cli(monkeypatch, *args: str, **env: str) -> int:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["etl", *args])
    return main()


def tree(root: Path) -> dict[str, tuple[int, int]]:
    """Every file and folder under `root`: relative path -> (size, mtime in ns)"""
    return {str(p.relative_to(root)): (p.stat().st_size if p.is_file() else -1, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*"))}


def run_map(tmp_path: Path, sample: Path, answers: dict, preset_id: str = "my_card", *, force: bool = False,
            out: Lines | None = None) -> tuple[int, Path]:
    config_dir = tmp_path / "cfg"
    config_dir.mkdir(exist_ok=True)
    code = map_layout(str(sample), preset_id, config_dir, answers=answers, force=force, out=out or Lines())
    return code, config_dir / "presets" / f"{preset_id}.yaml"


# ------------------------------------------------------------------ detect

def test_detect_names_the_one_format_that_reads_the_card_sample():
    out = Lines()
    assert detect(str(CARD_SAMPLE), {}, out=out) == 0
    assert out.lines == ["1 format reads this file:", "  bofa_card (preset, card): 12 rows, 2030-03-01 to 2030-03-28"]


@pytest.mark.parametrize("sample", ["bank_sgml.ofx", "bank_xml.ofx", "card_sgml.qfx"])
def test_detect_finds_an_ofx_download_whatever_its_name_and_never_prints_its_account_number(tmp_path, sample):
    renamed = tmp_path / "download.csv"                       # the extension is never trusted
    shutil.copy(SAMPLES / "ofx" / sample, renamed)
    txns, _, _ = ofx.parse(renamed, CARD)
    first, last = min(t.date for t in txns), max(t.date for t in txns)
    out = Lines()
    assert detect(str(renamed), {}, out=out) == 0
    assert out.lines == ["1 format reads this file:", f"  ofx (OFX / QFX): {len(txns)} rows, {first} to {last}"]
    assert not any(number in out.text for number in OFX_NUMBERS)


def test_detect_lists_both_formats_that_read_a_file_and_says_the_account_decides(tmp_path):
    out = Lines()
    own = {"my_card": build_preset(MY_CARD, "presets/my_card.yaml", "my_card", household=True)}
    assert detect(str(CARD_SAMPLE), own, out=out) == 1
    assert out.lines == [
        "2 formats read this file:",
        "  bofa_card (preset, card): 12 rows, 2030-03-01 to 2030-03-28",
        "  my_card (your own preset, card): 12 rows, 2030-03-01 to 2030-03-28",
        "The account's format decides which one reads it: name one as its parser in accounts.yaml (or as a source's "
        "format)."]


def test_detect_through_the_command_tries_the_households_own_presets(tmp_path, monkeypatch, capsys):
    config_dir = tmp_path / "config"
    write_preset(config_dir, MY_CARD)
    assert cli(monkeypatch, "import", "detect", str(CARD_SAMPLE), FF_CONFIG_DIR=str(config_dir)) == 1
    assert "  my_card (your own preset, card): 12 rows, 2030-03-01 to 2030-03-28" in capsys.readouterr().out


def test_detect_needs_no_household(monkeypatch, capsys):
    assert cli(monkeypatch, "import", "detect", str(CARD_SAMPLE)) == 0       # no config directory anywhere
    assert capsys.readouterr().out.splitlines()[0] == "1 format reads this file:"


def test_a_broken_preset_of_the_households_is_left_out_with_its_problems_and_detect_goes_on(tmp_path, monkeypatch,
                                                                                         capsys):
    config_dir = tmp_path / "config"
    write_preset(config_dir, {**MY_CARD, "kind": "debit"})
    assert cli(monkeypatch, "import", "detect", str(CARD_SAMPLE), FF_CONFIG_DIR=str(config_dir)) == 0
    out = capsys.readouterr()
    assert out.out.splitlines() == ["1 format reads this file:",
                                    "  bofa_card (preset, card): 12 rows, 2030-03-01 to 2030-03-28"]
    assert out.err.splitlines() == ["Not tried, for these problems in your own presets:",
                                    "presets/my_card.yaml: kind: 'debit' is not one of them — write one of bank, card"]


def test_detect_on_a_page_saved_as_csv_names_the_nearest_misses_and_the_next_step(tmp_path, monkeypatch):
    (tmp_path / "my export.csv").write_text(
        "<html><head><title>Sign in</title></head><body>Your session has ended.</body></html>\n")
    monkeypatch.chdir(tmp_path)
    out = Lines()
    assert detect("my export.csv", {}, out=out) == 1                 # named as typed, never by its whole path
    assert out.lines[0] == "No format reads this file. The nearest:"
    assert len(out.lines) == 5
    assert all(re.fullmatch(r"  [a-z_]+ \([^)]+\): [a-z].*", line) for line in out.lines[1:4])
    assert out.lines[4] == ("To describe its layout, try: python -m etl import map 'my export.csv' --id NAME (a name "
                            "you choose for it, such as my_bank)")
    assert "Sign in" not in out.text and "session" not in out.text              # never a line of the file


def test_the_nearest_miss_is_the_format_that_found_its_layout_and_read_no_row():
    out = Lines()
    assert detect(str(SAMPLES / "ofx" / "two_statements.ofx"), {}, out=out) == 1
    assert out.lines[1] == f"  ofx (OFX / QFX): {ofx.SEVERAL_ACCOUNTS}"
    assert not any(number in out.text for number in OFX_NUMBERS)


def test_a_header_that_holds_most_of_a_presets_columns_is_nearer_than_one_that_holds_none(tmp_path):
    lacking = tmp_path / "export.csv"
    lacking.write_text("Posted Date,Reference Number,Payee,Total\n03/28/2030,1,SAMPLE CAFE,-4.50\n")
    out = Lines()
    assert detect(str(lacking), {}, out=out) == 1
    assert out.lines[1] == "  bofa_card (preset, card): its header lacks the column Amount that this format reads"


@pytest.mark.parametrize("content", [b"", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\x00" * 50])
def test_detect_on_an_empty_file_or_bytes_that_are_no_text_reads_nothing(tmp_path, content):
    junk = tmp_path / "junk.csv"
    junk.write_bytes(content)
    out = Lines()
    assert detect(str(junk), {}, out=out) == 1
    assert out.lines[0] == "No format reads this file. The nearest:"


def test_a_file_over_the_cap_or_a_folder_is_refused_before_any_format_opens_it(tmp_path):
    big = tmp_path / "big.csv"
    with open(big, "wb") as f:
        f.truncate(registry.MAX_FILE_BYTES + 1)
    out = Lines()
    assert detect(str(big), {}, out=out) == 1
    assert out.lines == [f"No format reads this file: {registry.TOO_LARGE}."]
    out = Lines()
    assert detect(str(tmp_path), {}, out=out) == 1
    assert out.lines == [f"No format reads this file: {registry.NOT_A_FILE}."]
    out = Lines()
    assert preview(str(big), "bofa_card", {}, out=out) == 1
    assert out.lines == [f"Not read as bofa_card: {registry.TOO_LARGE}."]
    assert run_map(tmp_path, big, CARD_ANSWERS)[0] == 1


def test_a_file_that_is_not_there_is_a_usage_error_naming_only_what_was_typed(monkeypatch, capsys):
    assert cli(monkeypatch, "import", "detect", "no-such-export.csv") == 2
    out = capsys.readouterr()
    assert out.err == "import detect: no-such-export.csv: no such file\n" and out.out == ""


def test_import_with_no_command_prints_the_four(monkeypatch, capsys):
    assert cli(monkeypatch, "import") == 2
    out = capsys.readouterr().out
    assert all(name in out for name in ("detect", "preview", "map", "banks"))


def test_the_import_commands_load_no_networked_module():
    code = ("import sys, etl.import_cli; "
            "print(sorted(m for m in ('etl.simplefin', 'etl._net') if m in sys.modules))")
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]"


# ------------------------------------------------------------------ preview

def test_preview_prints_the_rows_the_skips_and_the_first_five_and_last_two():
    out = Lines()
    assert preview(str(CARD_SAMPLE), "bofa_card", {}, out=out) == 0
    assert squashed(out.lines) == [
        "Read as bofa_card: 12 rows, 2030-03-01 to 2030-03-28.",
        "Skipped: 2 (header 1, no-amount 1).",
        "First 5 rows:",
        "2030-03-28 -$54.18 TRADER JOE'S #000 SAMPLETOWN",
        "2030-03-27 -$6.45 STARBUCKS STORE 00000",
        "2030-03-27 -$6.45 STARBUCKS STORE 00000",
        "2030-03-25 -$18.20 PANERA BREAD #000, SAMPLETOWN",
        "2030-03-22 $1,250.00 PAYMENT - THANK YOU",
        "Last 2 rows:",
        "2030-03-05 -$112.00 AMTRAK TICKETS",
        "2030-03-01 -$73.66 WHOLEFDS SAMPLETOWN 00000"]
    assert "820.64" not in out.text                                             # the file's sum is never printed


def test_preview_of_an_ofx_file_shows_its_balance_and_never_its_account_number():
    path = SAMPLES / "ofx" / "bank_sgml.ofx"
    _, _, extra = ofx.parse(path, CARD)
    day, cents = extra["latest_balance"]
    out = Lines()
    assert preview(str(path), "ofx", {}, out=out) == 0
    assert squashed(out.lines)[-1] == f"Balance stated: {fmt(cents)} on {day}."
    assert not any(number in out.text for number in OFX_NUMBERS)


def test_preview_with_a_format_that_refuses_the_file_prints_its_reason_and_exits_1():
    out = Lines()
    assert preview(str(CARD_SAMPLE), "ofx", {}, out=out) == 1
    assert out.lines == [f"Not read as ofx: {ofx.NOT_OFX}."]


def test_preview_reads_with_a_households_own_preset_and_refuses_an_unknown_format(monkeypatch, tmp_path, capsys):
    config_dir = tmp_path / "config"
    write_preset(config_dir, MY_CARD)
    assert cli(monkeypatch, "import", "preview", str(CARD_SAMPLE), "--format", "my_card",
               FF_CONFIG_DIR=str(config_dir)) == 0
    assert capsys.readouterr().out.splitlines()[0] == "Read as my_card: 12 rows, 2030-03-01 to 2030-03-28."
    assert cli(monkeypatch, "import", "preview", str(CARD_SAMPLE), "--format", "no_such_format") == 2
    err = capsys.readouterr().err
    assert err.startswith("import preview: unknown format 'no_such_format' — one of ") and "bofa_card" in err


def test_preview_shows_a_hostile_description_without_its_control_characters(tmp_path):
    """A native format stores a description's control characters as it always has: the preview writes each as its
    escape, never raw. A preset stores the description without them, and the preview shows it so."""
    path = tmp_path / "card.csv"
    path.write_text("Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
                    "03/28/2030,03/28/2030,\"SAMPLE \x1b[2J\x1b]0;owned\x07CAFE\",Food & Drink,Sale,-4.50,\n")
    out = Lines()
    assert preview(str(path), "chase_card", {}, out=out) == 0
    assert not any(ch in out.text for ch in "\x1b\x07")
    assert "SAMPLE \\x1b[2J\\x1b]0;owned\\x07CAFE" in out.text
    path = tmp_path / "preset.csv"
    path.write_text("Posted Date,Reference Number,Payee,Address,Amount\n"
                    "03/28/2030,1,\"SAMPLE \x1b[2J\x1b]0;owned\x07CAFE\",ST,-4.50\n")
    out = Lines()
    assert preview(str(path), "bofa_card", {}, out=out) == 0
    assert not any(ch in out.text for ch in "\x1b\x07")
    assert "SAMPLE [2J]0;ownedCAFE" in out.text and "\\x" not in out.text


def test_preview_with_an_account_and_no_database_says_so_and_writes_nothing(tmp_path, monkeypatch, capsys):
    home = household(tmp_path, statements={"card-2030-03.csv": CARD_SAMPLE})
    before = tree(tmp_path)
    assert cli(monkeypatch, "import", "preview", str(home / "imports" / "card-2030-03.csv"), "--format", "bofa_card",
               "--account", CARD, FF_HOME=str(home)) == 0
    assert capsys.readouterr().out.splitlines()[-1] == ("No stored data to compare with yet: this household has not "
                                                        "been ingested.")
    assert tree(tmp_path) == before


def test_preview_with_an_account_counts_the_rows_already_stored_by_uid_and_writes_nothing(tmp_path, monkeypatch,
                                                                                       capsys):
    home = household(tmp_path, statements={"card-2030-03.csv": CARD_SAMPLE})
    monkeypatch.setenv("FF_HOME", str(home))
    assert pipeline.run() == 0
    newer = tmp_path / "newer.csv"
    lines = CARD_SAMPLE.read_text().splitlines()
    # the header, a row stored already, one of the two coffees of the 27th (the first: its occurrence index is 0, as the
    # stored first one's), a row the stored file does not have, and the oldest stored row
    newer.write_text("\n".join([lines[0], lines[1], lines[2], "03/29/2030,00000000000000000000014,SAMPLE CAFE,ST,-4.50",
                                lines[-1]]) + "\n")
    capsys.readouterr()
    before = tree(tmp_path)
    assert cli(monkeypatch, "import", "preview", str(newer), "--format", "bofa_card", "--account", CARD) == 0
    assert capsys.readouterr().out.splitlines()[-1] == f"Already stored for {CARD}: 3 of the file's 4 rows."
    assert tree(tmp_path) == before
    # the other coffee of the 27th alone in a file is that file's first such row: occurrence index 0, the uid of the
    # stored first coffee
    single = tmp_path / "single.csv"
    single.write_text("\n".join([lines[0], lines[3]]) + "\n")
    assert cli(monkeypatch, "import", "preview", str(single), "--format", "bofa_card", "--account", CARD) == 0
    assert capsys.readouterr().out.splitlines()[-1] == f"Already stored for {CARD}: 1 of the file's 1 row."


def test_preview_with_an_account_the_household_lacks_is_a_usage_error(tmp_path, monkeypatch, capsys):
    home = household(tmp_path)
    assert cli(monkeypatch, "import", "preview", str(CARD_SAMPLE), "--format", "bofa_card", "--account", "nobody",
               FF_HOME=str(home)) == 2
    assert capsys.readouterr().err.startswith("import preview: unknown account 'nobody' — one of demo_card_0000")


def test_preview_with_an_account_refuses_a_config_with_problems_in_the_validators_words(tmp_path, monkeypatch, capsys):
    home = household(tmp_path)
    write_preset(home / "config", {**MY_CARD, "id": "bofa_card"}, name="bofa_card")
    assert cli(monkeypatch, "import", "preview", str(CARD_SAMPLE), "--format", "bofa_card", "--account", CARD,
               FF_HOME=str(home)) == 2
    assert capsys.readouterr().err.splitlines() == [str(p) for p in validate(home / "config")]


# ------------------------------------------------------------------ map

def test_map_with_answers_writes_a_preset_that_reads_the_sample_as_the_shipped_one_does(tmp_path):
    out = Lines()
    code, path = run_map(tmp_path, CARD_SAMPLE, CARD_ANSWERS, out=out)
    assert code == 0
    preset = load_preset(path, household=True)
    txns, stats, _ = preset.parse(CARD_SAMPLE, CARD)
    assert rows_of(txns) == card_rows()
    assert stats.skip_reasons == {"header": 1, "no-amount": 1}
    assert preset.header == ("Posted Date", "Payee", "Amount") and preset.date_formats == ("%m/%d/%Y",)
    assert (preset.kind, preset.sign, preset.label, preset.source, preset.evidence) == (
        "card", "as_is", "Sample card export", "", "")
    assert "Dates read as %m/%d/%Y: 03/28/2030 is 2030-03-28." in out.lines
    assert out.lines[-5:] == [
        "Wrote presets/my_card.yaml in your config folder.",
        "To read an account's files with it, give the account these two lines in accounts.yaml (its files go in the "
        "statements folder):",
        "    parser: my_card",
        '    glob: "activity*.csv"',
        "Then check the config: python -m etl validate"]


def test_a_mapped_preset_validates_as_an_accounts_parser_and_the_household_ingests_green(tmp_path, monkeypatch,
                                                                                       capsys):
    home = household(tmp_path, {"parser": "my_card", "glob": "activity*.csv"},
                     statements={"activity.csv": CARD_SAMPLE})
    answers = tmp_path / "answers.yaml"
    answers.write_text(yaml.safe_dump(CARD_ANSWERS))
    assert cli(monkeypatch, "import", "map", str(home / "imports" / "activity.csv"), "--id", "my_card",
               "--answers", str(answers), FF_HOME=str(home)) == 0
    assert (home / "config" / "presets" / "my_card.yaml").is_file()
    capsys.readouterr()
    assert cli(monkeypatch, "validate") == 0
    assert capsys.readouterr().out == "config OK (2 files, 1 accounts, 1 preset)\n"
    assert pipeline.run(strict=True) == 0
    exports = home / "data" / "exports"
    stored = json.loads((exports / "transactions.json").read_text("utf-8"))
    assert len(stored["transactions"]) == 12
    checks = json.loads((exports / "quality_report.json").read_text("utf-8"))["checks"]
    assert [c["name"] for c in checks if c["status"] == "fail"] == []


def test_map_refuses_an_ambiguous_date_without_an_explicit_answer(tmp_path):
    export = tmp_path / "export.csv"
    export.write_text("Date,Description,Amount\n03/04/2030,SAMPLE CAFE,-4.50\n05/06/2030,SAMPLE GROCER,-20.00\n")
    answers = {"header": True, "date": "Date", "amount": "Amount", "positive_is_money_in": True,
               "description": ["Description"], "kind": "bank"}
    with pytest.raises(ValueError) as refused:
        run_map(tmp_path, export, answers)
    words = str(refused.value)
    assert "%m/%d/%Y: 03/04/2030 is 2030-03-04" in words and "%d/%m/%Y: 03/04/2030 is 2030-04-03" in words
    assert "date_format" in words
    assert not (tmp_path / "cfg" / "presets").exists()
    code, path = run_map(tmp_path, export, {**answers, "date_format": "%d/%m/%Y"})
    assert code == 0 and load_preset(path, household=True).date_formats == ("%d/%m/%Y",)


def test_a_stated_date_pattern_must_read_every_date(tmp_path):
    with pytest.raises(ValueError, match="date_format: '%d/%m/%Y' does not read the date of line 2 "):
        run_map(tmp_path, CARD_SAMPLE, {**CARD_ANSWERS, "date_format": "%d/%m/%Y"})


def test_map_asks_on_a_terminal_and_never_infers_the_sign(tmp_path):
    replies = iter(["n", "1", "1", "2", "y", "5", "", "bank"])
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return next(replies)

    out = Lines()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    assert map_layout(str(HEADERLESS_SAMPLE), "my_bank", config_dir, answers=None, out=out, ask=ask) == 0
    assert any("positive amount money coming in" in p for p in prompts)
    shown = out.lines.index("Two of its rows:")
    assert squashed(out.lines[shown + 1:shown + 3]) == [
        "line 1: 03/28/2030 | -54.18 | * | | TRADER JOE'S #000 SAMPLETOWN",
        "line 2: 03/27/2030 | 2400.00 | * | | SAMPLE EMPLOYER PAYROLL PPD ID: 0000000000"]
    mine = load_preset(config_dir / "presets" / "my_bank.yaml", household=True)
    expected, _, _ = load_preset(SYNTHETIC, allow_synthetic=True).parse(HEADERLESS_SAMPLE, CARD)
    got, _, _ = mine.parse(HEADERLESS_SAMPLE, CARD)
    assert rows_of(got) == rows_of(expected) and mine.cells == 5 and mine.header is None


def test_on_a_terminal_an_ambiguous_date_is_asked_and_the_answer_kept(tmp_path):
    export = tmp_path / "export.csv"
    export.write_text("Date,Description,Amount\n03/04/2030,SAMPLE CAFE,-4.50\n05/06/2030,SAMPLE GROCER,-20.00\n")
    replies = iter(["y", "1", "2", "1", "3", "n", "2", "", "bank"])
    out = Lines()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    assert map_layout(str(export), "my_bank", config_dir, answers=None, out=out, ask=lambda _: next(replies)) == 0
    preset = load_preset(config_dir / "presets" / "my_bank.yaml", household=True)
    assert preset.date_formats == ("%d/%m/%Y",) and preset.sign == "invert"
    assert "  2  %d/%m/%Y: 03/04/2030 is 2030-04-03" in out.lines


def test_map_reads_a_debit_and_a_credit_column_and_a_running_balance(tmp_path):
    export = tmp_path / "export.csv"
    export.write_text("Date,Memo,Withdrawal,Deposit,Balance\n"
                      "2030-03-01,SAMPLE GROCER,20.00,,980.00\n"
                      "2030-03-02,SAMPLE EMPLOYER PAYROLL,,1000.00,1980.00\n")
    answers = {"header": True, "date": 1, "debit": "Withdrawal", "credit": "Deposit", "description": "Memo",
               "balance": "Balance", "newest_first": False, "kind": "bank"}
    code, path = run_map(tmp_path, export, answers)
    assert code == 0
    preset = load_preset(path, household=True)
    txns, _, extra = preset.parse(export, "demo_checking_0000")
    assert rows_of(txns) == [("2030-03-01", -2000, "SAMPLE GROCER"), ("2030-03-02", 100000, "SAMPLE EMPLOYER PAYROLL")]
    assert preset.order == "oldest_first" and extra["latest_balance"] == ("2030-03-02", 198000)


def test_map_refuses_a_shipped_name_an_existing_preset_without_force_and_a_name_that_is_no_format_name(tmp_path):
    with pytest.raises(ValueError, match="'bofa_card' is the name of a format this release reads"):
        run_map(tmp_path, CARD_SAMPLE, CARD_ANSWERS, "bofa_card")
    with pytest.raises(ValueError, match="not a format name"):
        run_map(tmp_path, CARD_SAMPLE, CARD_ANSWERS, "My Card")
    assert run_map(tmp_path, CARD_SAMPLE, CARD_ANSWERS)[0] == 0
    path = tmp_path / "cfg" / "presets" / "my_card.yaml"
    path.write_text("# kept\n" + path.read_text())
    with pytest.raises(ValueError, match="presets/my_card.yaml already exists"):
        run_map(tmp_path, CARD_SAMPLE, CARD_ANSWERS)
    assert path.read_text().startswith("# kept\n")
    assert run_map(tmp_path, CARD_SAMPLE, {**CARD_ANSWERS, "kind": "bank"}, force=True)[0] == 0
    assert load_preset(path, household=True).kind == "bank"


def test_map_writes_nothing_when_its_layout_cannot_read_the_file(tmp_path):
    out = Lines()
    code, path = run_map(tmp_path, CARD_SAMPLE, {**CARD_ANSWERS, "amount": "Address"}, out=out)
    assert code == 1 and not path.exists() and not path.parent.exists()
    assert out.lines[-2:] == [f"Not read with this layout: {COLUMNS_DO_NOT_FIT}.", "Nothing was written."]
    with pytest.raises(ValueError, match="no common date pattern reads every date in column 3"):
        run_map(tmp_path, CARD_SAMPLE, {**CARD_ANSWERS, "date": "Payee"})
    assert not path.parent.exists()


def test_map_writes_nothing_when_its_layout_reads_no_row(tmp_path):
    export = tmp_path / "export.csv"
    export.write_text("Date,Description,Amount\n03/14/2030,SAMPLE AUTHORIZATION HOLD,\n"
                      "03/15/2030,SAMPLE AUTHORIZATION HOLD,\n")
    out = Lines()
    code, path = run_map(tmp_path, export, {"header": True, "date": "Date", "amount": "Amount",
                                            "positive_is_money_in": True, "skip_empty_amount": True,
                                            "description": "Description", "kind": "bank"}, out=out)
    assert code == 1 and not path.parent.exists()
    assert out.lines[-2:] == [f"Not read with this layout: {import_cli.NO_ROW}.", "Nothing was written."]


@pytest.mark.parametrize("stated", [False, True])
def test_a_totals_line_with_no_date_is_left_to_the_trial_read(tmp_path, stated):
    export = tmp_path / "export.csv"
    export.write_text("Date,Description,Amount\n03/14/2030,SAMPLE CAFE,-4.50\n03/15/2030,SAMPLE GROCER,-20.00\n"
                      "03/16/2030,SAMPLE EMPLOYER PAYROLL,1000.00\n,Total,975.50\n")
    answers = {"header": True, "date": "Date", "amount": "Amount", "positive_is_money_in": True,
               "description": "Description", "kind": "bank", **({"date_format": "%m/%d/%Y"} if stated else {})}
    code, path = run_map(tmp_path, export, answers)
    assert code == 0
    txns, stats, _ = load_preset(path, household=True).parse(export, CARD)
    assert len(txns) == 3 and stats.skip_reasons == {"header": 1, "unparseable-date": 1}


def test_a_date_column_blank_in_every_row_is_refused(tmp_path):
    export = tmp_path / "export.csv"
    export.write_text("Posted,Date,Description,Amount\n,03/14/2030,SAMPLE CAFE,-4.50\n")
    with pytest.raises(ValueError, match="date: column 1 is blank in every row, so it holds no date"):
        run_map(tmp_path, export, {"header": True, "date": "Posted"})


@pytest.mark.parametrize("dates, both", [
    (("03.01.2030", "05.02.2030"), ("%m.%d.%Y: 03.01.2030 is 2030-03-01", "%d.%m.%Y: 03.01.2030 is 2030-01-03")),
    (("03-01-2030", "05-02-2030"), ("%m-%d-%Y: 03-01-2030 is 2030-03-01", "%d-%m-%Y: 03-01-2030 is 2030-01-03")),
])
def test_dates_of_any_separator_that_read_both_ways_are_never_picked_silently(tmp_path, dates, both):
    export = tmp_path / "export.csv"
    export.write_text("Date,Description,Amount\n" + "".join(f"{d},SAMPLE CAFE,-4.50\n" for d in dates))
    answers = {"header": True, "date": "Date", "amount": "Amount", "positive_is_money_in": True,
               "description": "Description", "kind": "bank"}
    with pytest.raises(ValueError) as refused:
        run_map(tmp_path, export, answers)
    assert all(reading in str(refused.value) for reading in both) and "date_format" in str(refused.value)


def test_every_numeric_date_pattern_offered_has_its_twin_the_other_way_round_unless_it_starts_with_the_year():
    """A date that starts with the day or the month is written either way round, so both are offered and asked about;
    one that starts with the year is the year, the month and the day everywhere, so it is read so without a question"""
    def twin(pattern: str) -> str:
        return pattern.replace("%m", "\0").replace("%d", "%m").replace("\0", "%d")
    numeric = [p for p in import_cli.DATE_FORMATS if "%m" in p and "%d" in p]
    year_first = [p for p in numeric if p.startswith("%Y")]
    assert sorted(year_first) == ["%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"]
    assert all(twin(p) in import_cli.DATE_FORMATS for p in numeric if p not in year_first)
    assert not any(twin(p) in import_cli.DATE_FORMATS for p in year_first)


def test_year_first_dates_whose_days_are_all_12_or_less_are_read_without_a_question(tmp_path):
    export = tmp_path / "export.csv"
    export.write_text("Date,Description,Amount\n2030-03-01,SAMPLE CAFE,-4.50\n2030-05-02,SAMPLE GROCER,-6.00\n")
    answers = {"header": True, "date": "Date", "amount": "Amount", "positive_is_money_in": True,
               "description": "Description", "kind": "bank"}
    out = Lines()
    code, path = run_map(tmp_path, export, answers, out=out)
    assert code == 0 and "Dates read as %Y-%m-%d: 2030-03-01 is 2030-03-01." in out.lines
    assert load_preset(path, household=True).date_formats == ("%Y-%m-%d",)


def test_a_file_that_cannot_be_opened_is_one_plain_line_and_exit_2_in_every_command(tmp_path, monkeypatch, capsys):
    """Every command refuses it before trying any format, so none prints half an answer first"""
    (tmp_path / "locked.csv").write_text(CARD_SAMPLE.read_text())
    (tmp_path / "answers.yaml").write_text(yaml.safe_dump(CARD_ANSWERS))
    (tmp_path / "config").mkdir()
    monkeypatch.chdir(tmp_path)
    real_open = open

    def locked(file, *args, **kwargs):
        if Path(file).name == "locked.csv":
            raise PermissionError(13, "Permission denied", str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(import_cli, "open", locked, raising=False)
    for command in (["detect", "locked.csv"], ["preview", "locked.csv", "--format", "bofa_card"],
                    ["map", "locked.csv", "--id", "my_card", "--answers", "answers.yaml"]):
        assert cli(monkeypatch, "import", *command, FF_CONFIG_DIR=str(tmp_path / "config")) == 2
        out = capsys.readouterr()
        assert (out.out, out.err) == ("", f"import {command[0]}: locked.csv: Permission denied\n")


def test_ctrl_c_at_a_map_prompt_writes_nothing_and_exits_130_without_a_traceback(tmp_path, monkeypatch, capsys):
    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    def interrupted(prompt: str = "") -> str:
        raise KeyboardInterrupt

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr(sys, "stdin", Terminal(""))
    monkeypatch.setattr("builtins.input", interrupted)
    assert cli(monkeypatch, "import", "map", str(CARD_SAMPLE), "--id", "my_card", FF_CONFIG_DIR=str(config_dir)) == 130
    assert capsys.readouterr().err == f"import map: {import_cli.STOPPED}\n"
    assert import_cli.STOPPED.endswith("nothing was written") and not (config_dir / "presets").exists()


def test_map_refuses_an_answer_it_does_not_know_and_a_missing_one(tmp_path):
    with pytest.raises(ValueError, match="colour"):
        run_map(tmp_path, CARD_SAMPLE, {**CARD_ANSWERS, "colour": "blue"})
    missing = {k: v for k, v in CARD_ANSWERS.items() if k != "kind"}
    with pytest.raises(ValueError, match="add kind: to them"):
        run_map(tmp_path, CARD_SAMPLE, missing)
    with pytest.raises(ValueError, match="date: 9 is not a column"):
        run_map(tmp_path, CARD_SAMPLE, {**CARD_ANSWERS, "date": 9})


def test_map_without_a_terminal_or_answers_asks_for_answers_and_exits_2(tmp_path, monkeypatch, capsys):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))          # not a terminal
    assert cli(monkeypatch, "import", "map", str(CARD_SAMPLE), "--id", "my_card", FF_CONFIG_DIR=str(config_dir)) == 2
    assert capsys.readouterr().err == ("import map: there is no terminal to ask on: pass the answers in a file with "
                                       "--answers FILE\n")
    assert not (config_dir / "presets").exists()


@pytest.mark.parametrize("name, glob", [("activity.csv", "activity*.csv"), ("card-2030-03.csv", "card-*.csv"),
                                        ("Sample1234_Activity_20301101.CSV", "Sample*_Activity_*.CSV"),
                                        ("2030-03.csv", "*.csv")])
def test_the_glob_suggested_stands_for_the_files_digits(name, glob):
    assert glob_for(name) == glob


# ------------------------------------------------------------------ the household's own presets in the config

def config_with(tmp_path: Path, presets: dict[str, dict | str], account: dict | None = None) -> Path:
    """A household config at tmp_path/config with the preset files `presets` (file name -> document, or raw text)"""
    d = tmp_path / "config"
    write_household(d)
    meta = {"name": "Sample Card", "institution": "Sample Bank", "owner": "Alex", "kind": "credit-card",
            **(account or {})}
    (d / "accounts.yaml").write_text(yaml.safe_dump({"source_dir": "src", "accounts": {CARD: meta}}), "utf-8")
    (tmp_path / "src").mkdir(exist_ok=True)
    (d / "presets").mkdir(exist_ok=True)
    for name, doc in presets.items():
        (d / "presets" / name).write_text(doc if isinstance(doc, str) else yaml.safe_dump(doc), "utf-8")
    return d


def test_a_household_preset_is_a_format_of_that_household_and_needs_no_source_or_evidence(tmp_path):
    d = config_with(tmp_path, {"my_card.yaml": MY_CARD}, {"parser": "my_card", "glob": "card-*.csv"})
    cfg = Config(d)
    assert list(cfg.presets) == ["my_card"] and "my_card" not in pipeline.TXN_PARSERS
    with pytest.raises(ConfigError) as refused:
        Config(config_with(tmp_path / "other", {}, {"parser": "my_card", "glob": "card-*.csv"}))
    assert [str(p) for p in refused.value.problems] == [
        f"accounts.yaml: accounts.{CARD}.parser: unknown parser 'my_card' — one of "
        f"{', '.join(pipeline.TXN_PARSERS)}"]


def test_the_unknown_format_words_name_the_households_presets_too(tmp_path):
    d = config_with(tmp_path, {"my_card.yaml": MY_CARD}, {"parser": "your_card", "glob": "card-*.csv"})
    [problem] = validate(d)
    assert problem.path == f"accounts.{CARD}.parser" and problem.fix.endswith(", my_card")


def test_validate_reports_every_household_preset_problem_with_its_file_and_key_path(tmp_path):
    d = config_with(tmp_path, {
        "bofa_card.yaml": {**MY_CARD, "id": "bofa_card"},                   # a format this release reads
        "a.yaml": {**MY_CARD, "id": "b"},                                    # not its file's name, and b's id
        "b.yaml": {**MY_CARD, "id": "b"},
        "c.yaml": {**MY_CARD, "id": "c", "kind": "debit", "date": None},     # problems of its own
        "d.yml": MY_CARD,                                                    # never read under that name
        "e.yaml": {**MY_CARD, "id": "e", "evidence": "synthetic"},          # the tests' own evidence only
        ".hidden.yaml": "not: [read",
    })
    assert [str(p) for p in validate(d)] == [
        "presets/a.yaml: id: 'b' is also the id of presets/b.yaml — give each preset an id of its own, and name its "
        "file after it",
        "presets/a.yaml: id: 'b' is not the file's name — name the file b.yaml, or write id: a",
        "presets/bofa_card.yaml: id: 'bofa_card' is the name of a format this release reads — give your preset an id "
        "of its own, and name its file after it",
        "presets/c.yaml: date: missing — write date as a mapping of its keys",
        "presets/c.yaml: kind: 'debit' is not one of them — write one of bank, card",
        "presets/d.yml: not read: a preset's file is named <id>.yaml — rename it d.yaml",
        "presets/e.yaml: evidence: synthetic is the evidence of a preset the tests write for themselves — write one of "
        "bank-documented, oss-definition, widely-reported, sample-verified"]


def test_an_account_naming_a_broken_preset_gets_the_presets_problem_only(tmp_path):
    d = config_with(tmp_path, {"my_card.yaml": {**MY_CARD, "kind": "debit"}},
                    {"parser": "my_card", "glob": "card-*.csv"})
    assert [str(p) for p in validate(d)] == ["presets/my_card.yaml: kind: 'debit' is not one of them — write one of "
                                             "bank, card"]


def test_a_presets_entry_that_is_no_folder_is_one_problem(tmp_path):
    d = config_with(tmp_path, {})
    (d / "presets").rmdir()
    (d / "presets").write_text("id: my_card\n")
    assert [str(p) for p in validate(d)] == [
        "presets: not a folder — make it a folder of preset files named <id>.yaml, or remove it"]


def test_the_pipeline_reads_a_household_preset_beside_a_patched_format(tmp_path, monkeypatch):
    """parse_transactions looks a format up in the household's own presets first and in TXN_PARSERS at the call, so a
    test's patched entry still reads"""
    def sample_format(path, account_id):
        stats = FileStats(source_file=path.name, parser="sample_format", rows_total=1)
        row = Txn(date="2030-03-02", account_id=account_id, description_raw="SAMPLE PATCHED ROW", amount_cents=-100,
                  source_file=path.name)
        stats.observe(row.date, row.amount_cents)
        return [row], stats, {}

    monkeypatch.setitem(pipeline.TXN_PARSERS, "sample_format", sample_format)
    d = config_with(tmp_path, {"my_card.yaml": MY_CARD},
                    {"sources": [{"format": "sample_format", "glob": "old-*.csv"},
                                 {"format": "my_card", "glob": "new-*.csv", "since": "2030-03-20"}]})
    (tmp_path / "src" / "old-2030-03.csv").write_text("anything\n")
    shutil.copy(CARD_SAMPLE, tmp_path / "src" / "new-2030-03.csv")
    p = pipeline.Pipeline(Config(d))
    p.parse_transactions()
    assert rows_of(p.txns) == [("2030-03-02", -100, "SAMPLE PATCHED ROW"),
                               *[r for r in card_rows() if r[0] >= "2030-03-20"]]
    assert [s.parser for s in p.file_stats] == ["sample_format", "my_card"]


def test_own_presets_reads_the_folder_beside_the_config_without_a_config(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    write_preset(config_dir, MY_CARD)
    monkeypatch.setenv("FF_CONFIG_DIR", str(config_dir))
    assert list(own_presets().presets) == ["my_card"]                   # no household.yaml or accounts.yaml needed


# ------------------------------------------------------------------ the document

def preset_section() -> list[str]:
    lines = DOC.read_text("utf-8").splitlines()
    start = lines.index("## Your own file layouts")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return lines[start:end]


def test_the_document_lists_the_keys_a_preset_has():
    rows = [m["path"] for line in preset_section() if (m := re.match(r"^\| `(?P<path>[^`]+)` \|", line))]
    known = {*_KEYS, *(f"{block}.{key}" for block, keys in _BLOCK_KEYS.items() for key in keys)}
    assert sorted(set(rows) - known) == [], "rows of the document that name no key a preset has"
    assert sorted(known - set(rows)) == [], "keys a preset has that the document does not list"
    assert len(rows) == len(set(rows))
    assert all(block.split(".")[0] in _KEYS for block in _BLOCK_KEYS)


def test_the_documents_example_preset_loads_as_a_households_own():
    section = preset_section()
    start = next(i for i, line in enumerate(section) if line == "```" and section[i + 1].startswith("id: "))
    end = section.index("```", start + 1)
    doc = yaml.safe_load("\n".join(section[start + 1:end]))
    preset = build_preset(doc, "presets/example.yaml", doc["id"], household=True)
    assert preset.source == "" and preset.evidence == ""


# ------------------------------------------------------------------ the table of supported banks

def table_rows(text: str) -> list[list[str]]:
    """The body rows of the document's table, each its cells (a `\\|` inside a cell is text, not a border)"""
    lines = [line for line in text.splitlines() if line.startswith("| ")]
    return [[cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", line)[1:-1]] for line in lines[1:]]


def test_the_committed_bank_table_is_the_one_the_formats_give():
    """docs/importing-banks.md is generated: a format, a preset or a preset's lines that change change it, and the
    committed file must be regenerated with them"""
    assert BANKS_DOC == ROOT / "docs" / "importing-banks.md"
    assert BANKS_DOC.read_text("utf-8") == banks_document(), \
        "the table of supported banks is out of date: regenerate it with python -m etl import banks --write"


def test_every_format_of_the_release_has_one_row_sorted_by_institution():
    text = banks_document()
    assert "\n| Institution | Account | Format | How to get the file | Evidence | Source |\n|---|" in text
    rows = table_rows(text)
    formats = [row[2].strip("`") for row in rows]
    assert sorted(formats) == sorted(pipeline.TXN_PARSERS)
    keys = [(row[0].casefold(), row[2]) for row in rows]
    assert keys == sorted(keys)
    assert {b.format for b in NATIVE_BANKS} == set(pipeline.TXN_PARSERS) - set(registry.presets())


def test_a_presets_row_holds_its_own_lines():
    rows = {row[2].strip("`"): row for row in table_rows(banks_document())}
    for preset_id, preset in registry.presets().items():
        account = {"bank": "bank account", "card": "credit card"}[preset.kind]
        assert rows[preset_id] == [preset.institution, account, f"`{preset_id}`", " ".join(preset.download.split()),
                                   preset.evidence, " ".join(preset.source.split())]


def test_a_cell_never_breaks_the_table():
    row = bank_row(Bank("Sample | Bank", "bank account", "sample_bank", "Open\nActivity |  then CSV", "built-in", "x"))
    assert row == "| Sample \\| Bank | bank account | `sample_bank` | Open Activity \\| then CSV | built-in | x |"
    assert table_rows("| head |\n" + row) == [["Sample | Bank", "bank account", "`sample_bank`",
                                                "Open Activity | then CSV", "built-in", "x"]]


def test_import_banks_prints_the_document_and_write_writes_it(monkeypatch, capsys, tmp_path):
    assert cli(monkeypatch, "import", "banks") == 0
    assert capsys.readouterr().out == banks_document()
    target = tmp_path / "importing-banks.md"
    monkeypatch.setattr(import_cli, "BANKS_DOC", target)
    assert cli(monkeypatch, "import", "banks", "--write") == 0
    assert target.read_text("utf-8") == banks_document()
    assert capsys.readouterr().out == "Wrote docs/importing-banks.md.\n"

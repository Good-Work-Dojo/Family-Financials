# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Hostile statement files against every format: each one is thrown at the three native formats, every shipped CSV
preset, the tests' two synthetic presets and the OFX reader, through Pipeline.parse_transactions on a one-account
household built in tmp_path. For every pair the outcome is one of three, stated per case: the file is read right (the
rows it holds, each with its sign), refused (its one `import.<account>.<file>` check fails with plain words that quote
nothing of the file, and it adds no row), or read with a row skipped for a stated reason. No exception leaves
parse_transactions, no case runs past a time bound, and no row is read with the wrong sign.

The inputs: another format's valid file (every format against every other's: the wrong-bank matrix); the right layout
with one column of its header renamed; Windows-1252 bytes with an accented merchant; UTF-16 with a byte-order mark; a
UTF-8 byte-order mark; CR and mixed line ends; a quoted field holding a newline; a trailing totals row; trailing blank
lines; an HTML error page saved under the format's extension; a file of no bytes (no rows and no failure, in every
format); NUL bytes only; random bytes; a sparse file over the size cap (refused by its size, its format's parse never
called, and never read by the test either: `truncate` gives it its size without writing a byte); a pipe and a folder
the glob matches; 50,000 rows (read in a few seconds); one line of 2 MB; descriptions that start with = + - @, hold
HTML and script tags, control characters, 10,000 characters, right-to-left and combining characters, bidirectional
controls and invisible format characters; dates in 1900 and 2999 and one that is no calendar day; amounts of 0, 0.001,
a billion, twenty digits, 1e9 and with currency signs.

A description is data: stored as the file has it after the format's own whitespace squash, formula-leading characters
and HTML included (the dashboard escapes what it shows). The three native formats store a description with only its
whitespace squashed, since a household's stored rows are known by it; the presets and OFX also remove control
characters (whitespace aside), the bidirectional controls and the invisible format characters, so a description reads
the way it is stored, and cut a description longer than 500 characters, counted in one info check per account
(registry.bounded_description).

Every file is generated here from invented rows: merchants from the starter rules, placeholder account numbers."""
from __future__ import annotations

import csv
import io
import os
import random
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml
from conftest import isolated_pipeline, write_household

from etl import pipeline
from etl.config import Config
from etl.importers import csv_preset, ofx, registry
from etl.importers.csv_preset import load_preset
from etl.pipeline import Pipeline

ACCOUNT = "demo_sample_0000"
SYNTHETIC_HEADERLESS = Path(__file__).parent / "importers" / "synthetic" / "sample_headerless.yaml"
# a synthetic header preset (a made-up layout, no bank's format), written for each case that reads with it
SAMPLE_BANK = {
    "id": "sample_bank", "label": "Synthetic bank layout (tests only)", "kind": "bank",
    "source": "synthetic: invented for these tests; no bank's layout", "evidence": "synthetic",
    "detect": {"header": ["Date", "Description", "Amount"]},
    "date": {"column": "Date", "format": "%m/%d/%Y"},
    "amount": {"column": "Amount", "sign": "as_is"},
    "description": {"columns": ["Description"]},
}
# how long one case may run: a reader that waits on a hostile file (a pipe opened for reading waits for a writer
# forever) fails its case instead of holding the suite
TIME_BOUND = 60
# how long 50,000 rows of one format may take to read
MANY_ROWS_SECONDS = 15
# the skip reasons of a row the file holds that is not read: a file read right has none of them
ROW_SKIPS = frozenset({"unparseable-row", "unparseable-date", "constant-mismatch", "no-amount", "both-debit-and-credit",
                       "signed-debit-or-credit", "pending", "trailing-row", "short-row", "outside-transaction-list",
                       "repeated-fitid", "blank-or-repeated-header"})
# words and figures of the files: a refusal quotes none of them
FILE_WORDS = ("TRADER", "STARBUCKS", "AUTOZONE", "JIFFY", "SAMPLETOWN", "CAFÉ", "54.18", "6.45", "32.99", "89.95",
              "000012345678", "000099999", "html")


class _Expired(BaseException):
    """A case ran past TIME_BOUND. Not an Exception, so the ingest's per-file guard cannot take it for a file's
    failure."""


@pytest.fixture(autouse=True)
def _time_bound():
    def expired(signum, frame):
        raise _Expired(f"the case ran past {TIME_BOUND} seconds")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, TIME_BOUND)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


# ------------------------------------------------------------------ rows, and each format's file of them

@dataclass(frozen=True)
class Row:
    day: date
    cents: int              # the signed amount the row means: negative is money out
    desc: str
    text: str | None = None  # the amount cell's text, written without its sign in place of the cents


BASE = (
    Row(date(2030, 3, 2), -54_18, "TRADER JOE'S #000 SAMPLETOWN"),
    Row(date(2030, 3, 5), -6_45, "STARBUCKS STORE 00000"),
    Row(date(2030, 3, 9), 32_99, "AUTOZONE 0000 RETURN"),
    Row(date(2030, 3, 12), -89_95, "JIFFY LUBE #0000"),
)


def _plain(cents: int) -> str:
    return f"{abs(cents) // 100}.{abs(cents) % 100:02d}"


def _grouped(cents: int) -> str:
    return f"{'-' if cents < 0 else ''}{abs(cents) // 100:,}.{abs(cents) % 100:02d}"


def _magnitude(r: Row, dollar: str = "") -> str:
    return r.text if r.text is not None else f"{dollar}{_plain(r.cents)}"


def _signed(r: Row, invert: bool = False) -> str:
    """The amount cell of a signed column: money out negative, or positive where the export turns signs round"""
    return ("-" if (r.cents < 0) != invert else "") + _magnitude(r)


def _us(d: date) -> str:
    return f"{d.month:02d}/{d.day:02d}/{d.year:04d}"


def _ymd(d: date) -> str:
    return f"{d.year:04d}{d.month:02d}{d.day:02d}"


def _csv(lines: list[list[str]], quote_all: bool = False) -> str:
    out = io.StringIO()
    csv.writer(out, lineterminator="\n", quoting=csv.QUOTE_ALL if quote_all else csv.QUOTE_MINIMAL).writerows(lines)
    return out.getvalue()


def _chase_checking(rows: list[Row]) -> str:
    lines = [["Details", "Posting Date", "Description", "Amount", "Type", "Balance", "Check or Slip #"]]
    balance = 1000_00
    for r in rows:
        balance += r.cents
        lines.append(["CREDIT" if r.cents > 0 else "DEBIT", _us(r.day), r.desc, _signed(r), "ACH_DEBIT",
                      ("-" if balance < 0 else "") + _plain(balance), ""])
    return _csv(lines)


def _chase_card(rows: list[Row]) -> str:
    return _csv([["Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo"]]
                + [[_us(r.day), _us(r.day), r.desc, "Shopping", "Return" if r.cents > 0 else "Sale", _signed(r), ""]
                   for r in rows])


def _bofa_bank(rows: list[Row]) -> str:
    days = [r.day for r in rows] or [date(2030, 3, 1)]
    first, last = _us(min(days)), _us(max(days))
    begin = 1000_00
    credits = sum(r.cents for r in rows if r.cents > 0)
    debits = sum(r.cents for r in rows if r.cents < 0)
    lines = [["Description", "", "Summary Amt."],
             [f"Beginning balance as of {first}", "", _grouped(begin)],
             ["Total credits", "", _grouped(credits)],
             ["Total debits", "", _grouped(debits)],
             [f"Ending balance as of {last}", "", _grouped(begin + credits + debits)],
             [],
             ["Date", "Description", "Amount", "Running Bal."],
             [first, f"Beginning balance as of {first}", "", _grouped(begin)]]
    balance = begin
    for r in rows:
        balance += r.cents
        lines.append([_us(r.day), r.desc, _signed(r), _grouped(balance)])
    return _csv(lines)


def _bofa_card(rows: list[Row]) -> str:
    return _csv([["Posted Date", "Reference Number", "Payee", "Address", "Amount"]]
                + [[_us(r.day), f"{i:023d}", r.desc, "SAMPLE CITY ST", _signed(r)] for i, r in enumerate(rows)])


def _capital_one_card(rows: list[Row]) -> str:
    lines = [["Transaction Date", "Posted Date", "Card No.", "Description", "Category", "Debit", "Credit"]]
    for r in rows:
        debit, credit = (_magnitude(r), "") if r.cents < 0 else ("", _magnitude(r))
        lines.append([r.day.isoformat(), r.day.isoformat(), "0000", r.desc, "Other", debit, credit])
    return _csv(lines)


def _discover_card(rows: list[Row]) -> str:
    return _csv([["Trans. Date", "Post Date", "Description", "Amount", "Category"]]
                + [[_us(r.day), _us(r.day), r.desc, _signed(r, invert=True), "Merchandise"] for r in rows])


def _schwab_checking(rows: list[Row]) -> str:
    lines = [["Date", "Status", "Type", "CheckNumber", "Description", "Withdrawal", "Deposit", "RunningBalance"]]
    balance = 1000_00
    for r in rows:
        balance += r.cents
        withdrawal, deposit = (_magnitude(r, "$"), "") if r.cents < 0 else ("", _magnitude(r, "$"))
        lines.append([_us(r.day), "Posted", "DEBIT" if r.cents < 0 else "CREDIT", "", r.desc, withdrawal, deposit,
                      "$" + _grouped(balance)])
    return _csv(lines)


def _headerless(rows: list[Row]) -> str:
    """The Wells Fargo download's layout, which the synthetic headerless preset shares: five quoted cells, an asterisk
    in the third"""
    return _csv([[_us(r.day), _signed(r), "*", "", r.desc] for r in rows], quote_all=True)


def _sample_bank(rows: list[Row]) -> str:
    return _csv([["Date", "Description", "Amount"]] + [[_us(r.day), r.desc, _signed(r)] for r in rows])


OFX_HEADER = ("OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\nENCODING:USASCII\nCHARSET:1252\n"
              "COMPRESSION:NONE\nOLDFILEUID:NONE\nNEWFILEUID:NONE\n\n")


def _escaped(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ofx(rows: list[Row]) -> str:
    """An SGML bank statement: one STMTTRN a row, the description its NAME"""
    days = [r.day for r in rows] or [date(2030, 3, 1)]
    first, last = _ymd(min(days)), _ymd(max(days))
    trns = "".join(f"<STMTTRN>\n<TRNTYPE>{'CREDIT' if r.cents > 0 else 'DEBIT'}\n<DTPOSTED>{_ymd(r.day)}\n"
                   f"<TRNAMT>{_signed(r)}\n<FITID>F{i:06d}\n<NAME>{_escaped(r.desc)}\n</STMTTRN>\n"
                   for i, r in enumerate(rows))
    return (f"{OFX_HEADER}<OFX>\n<BANKMSGSRSV1>\n<STMTTRNRS>\n<TRNUID>0\n<STATUS>\n<CODE>0\n<SEVERITY>INFO\n</STATUS>\n"
            f"<STMTRS>\n<CURDEF>USD\n<BANKACCTFROM>\n<BANKID>000099999\n<ACCTID>000012345678\n<ACCTTYPE>CHECKING\n"
            f"</BANKACCTFROM>\n<BANKTRANLIST>\n<DTSTART>{first}\n<DTEND>{last}\n{trns}</BANKTRANLIST>\n<LEDGERBAL>\n"
            f"<BALAMT>1000.00\n<DTASOF>{last}\n</LEDGERBAL>\n</STMTRS>\n</STMTTRNRS>\n</BANKMSGSRSV1>\n</OFX>\n")


@dataclass(frozen=True)
class Format:
    name: str                                   # the format an account's `parser:` names
    kind: str                                   # the kind of account it is read for
    family: str                                 # native, preset or ofx
    write: Callable[[list[Row]], str]           # the text of a file of these rows
    total: Callable[[str], list[str]] | None    # the cells of a trailing totals line for a total; none in OFX
    total_skip: str | None                      # the reason such a line is skipped
    rename: tuple[str, str] | None              # a column of its header and another name for it; none without one
    suffix: str = ".csv"


FORMATS: dict[str, Format] = {f.name: f for f in (
    Format("chase_checking", "checking", "native", _chase_checking, lambda t: ["Total", "", "", t, "", "", ""],
           "unparseable-row", ("Amount", "Amt")),
    Format("chase_card", "credit-card", "native", _chase_card, lambda t: ["Total", "", "", "", "", t, ""],
           "unparseable-row", ("Amount", "Amt")),
    Format("bofa_bank", "checking", "native", _bofa_bank, lambda t: ["Total", "", t, ""], "unparseable-row",
           ("Amount", "Amt")),
    Format("bofa_card", "credit-card", "preset", _bofa_card, lambda t: ["Total", "", "", "", t], "unparseable-date",
           ("Amount", "Amt")),
    Format("capital_one_card", "credit-card", "preset", _capital_one_card,
           lambda t: ["Total", "", "", "", "", t.lstrip("-"), ""], "unparseable-date", ("Debit", "Debits")),
    Format("discover_card", "credit-card", "preset", _discover_card, lambda t: ["Total", "", "", t, ""],
           "unparseable-date", ("Amount", "Amt")),
    Format("schwab_checking", "checking", "preset", _schwab_checking, lambda t: ["Total", "", "", "", "", "", "", ""],
           "unparseable-date", ("Withdrawal", "Withdrawals")),
    Format("wells_fargo", "checking", "preset", _headerless, lambda t: ["Total", t, "", "", ""], "constant-mismatch",
           None),
    Format("sample_headerless", "checking", "preset", _headerless, lambda t: ["Total", t, "", "", ""],
           "constant-mismatch", None),
    Format("sample_bank", "checking", "preset", _sample_bank, lambda t: ["Total", "", t], "unparseable-date",
           ("Amount", "Amt")),
    Format("ofx", "checking", "ofx", _ofx, None, None, ("<TRNAMT>", "<TRNAMOUNT>"), ".ofx"),
)}
NATIVE = ("chase_checking", "chase_card", "bofa_bank")
SYNTHETIC = ("sample_headerless", "sample_bank")


def test_every_format_of_the_release_is_in_the_table():
    assert set(FORMATS) == set(registry.formats()) | set(SYNTHETIC)
    assert {f.name for f in FORMATS.values() if f.family == "preset"} == set(registry.presets()) | set(SYNTHETIC)
    assert {f.name for f in FORMATS.values() if f.family == "native"} == set(NATIVE)


# ------------------------------------------------------------------ what a case expects, and the household

@dataclass(frozen=True)
class Expect:
    rows: tuple[tuple[str, int, str], ...] = ()     # (ISO date, signed cents, description) read, in file order
    refused: bool = False
    reason: str | None = None       # refused: the words that say why (None: any plain words); else a skip reason
    cut: int = 0                    # rows stored with their description cut (the account's one info check counts them)
    untouched: bool = False         # refused before its format's parse is called


def _rows(rows) -> tuple[tuple[str, int, str], ...]:
    return tuple((r.day.isoformat(), r.cents, r.desc) for r in rows)


def read(rows, cut: int = 0) -> Expect:
    return Expect(rows=_rows(rows), cut=cut)


def skipped(rows, reason: str) -> Expect:
    """read, with one row of the file skipped for `reason`"""
    return Expect(rows=_rows(rows), reason=reason)


def refused(reason: str | None = None, untouched: bool = False) -> Expect:
    return Expect(refused=True, reason=reason, untouched=untouched)


def _synthetic(name: str, tmp_path: Path) -> csv_preset.Preset:
    if name == "sample_headerless":
        return load_preset(SYNTHETIC_HEADERLESS, allow_synthetic=True)
    d = tmp_path / "synthetic"
    d.mkdir(exist_ok=True)
    (d / "sample_bank.yaml").write_text(yaml.safe_dump(SAMPLE_BANK, sort_keys=False), "utf-8")
    return load_preset(d / "sample_bank.yaml", allow_synthetic=True)


def _household(tmp_path: Path, fmt: Format, monkeypatch) -> tuple[Config, list[str]]:
    """One account reading `statement-*` files of tmp_path/src in `fmt`, and the list of the files its format's parse
    is called for"""
    if fmt.name in SYNTHETIC:
        monkeypatch.setitem(pipeline.TXN_PARSERS, fmt.name, _synthetic(fmt.name, tmp_path).parse)
    calls: list[str] = []
    parse = pipeline.TXN_PARSERS[fmt.name]

    def recorded(path, account_id):
        calls.append(path.name)
        return parse(path, account_id)
    monkeypatch.setitem(pipeline.TXN_PARSERS, fmt.name, recorded)
    d = tmp_path / "cfg"
    d.mkdir()
    (tmp_path / "src").mkdir()
    account = {"name": "Sample Account", "institution": "Sample Bank", "owner": "Alex", "kind": fmt.kind,
               "parser": fmt.name, "glob": "statement-*"}
    (d / "accounts.yaml").write_text(yaml.safe_dump({"source_dir": "src", "accounts": {ACCOUNT: account}}), "utf-8")
    write_household(d)
    return Config(d, source_dir=str(tmp_path / "src")), calls


def _parsed(tmp_path: Path, cfg: Config) -> Pipeline:
    p = isolated_pipeline(tmp_path / "state", [], cfg=cfg, windows={})
    p.parse_transactions()
    return p


def _not_read(reason: str, fmt: str) -> str:
    return (f"Not read: {reason}. Check that it is this account's own export, in the format the account names "
            f"(accounts.yaml parser: {fmt}).")


def _long_words(n: int) -> str:
    return (f"{n} row{'s' if n != 1 else ''} of this account's statement files {'have' if n != 1 else 'has'} a "
            f"description longer than 500 characters, stored cut to its first 500.")


def _assert_outcome(p: Pipeline, fmt: Format, path: Path, expect: Expect, calls: list[str]) -> None:
    checks = [(c.name, c.status, c.detail) for c in p.checks if c.name.startswith("import.")]
    rows = [(t.date, t.amount_cents, t.description_raw) for t in p.txns]
    stats = [s for s in p.file_stats if s.source_file == path.name]
    if expect.refused:
        [(status, detail)] = [(s, d) for n, s, d in checks if n == f"import.{ACCOUNT}.{path.name}"]
        assert status == "fail"
        if expect.reason is not None:
            assert detail == _not_read(expect.reason, fmt.name)
        assert detail.startswith("Not read: ") and detail.endswith(f"(accounts.yaml parser: {fmt.name}).")
        assert not any(word in detail for word in FILE_WORDS), detail
        assert str(path.parent) not in detail
        assert rows == [] and p.acct_file_sums == {ACCOUNT: 0} and p.latest_balances == {}
        if expect.untouched:
            assert calls == [] and stats == []
        return
    assert not any(status == "fail" for _, status, _ in checks), checks
    assert rows == list(expect.rows)
    [s] = stats
    assert (s.account, s.rows_parsed, s.sum_cents) == (ACCOUNT, len(rows), sum(cents for _, cents, _ in rows))
    assert s.rows_total == s.rows_parsed + s.rows_skipped and s.rows_skipped == sum(s.skip_reasons.values())
    unread = {r: n for r, n in s.skip_reasons.items() if r in ROW_SKIPS}
    assert unread == ({expect.reason: 1} if expect.reason else {}), s.skip_reasons
    long = [(status, detail) for name, status, detail in checks if name == f"import.long_description.{ACCOUNT}"]
    assert long == ([("info", _long_words(expect.cut))] if expect.cut else [])


# ------------------------------------------------------------------ the hostile inputs

@dataclass(frozen=True)
class Hostile:
    name: str
    make: Callable[[Format, Path], Expect]      # writes the file at the path and says what its format does with it
    applies: Callable[[Format], bool] = lambda fmt: True


INPUTS: list[Hostile] = []


def hostile(name: str, applies: Callable[[Format], bool] = lambda fmt: True):
    def register(make: Callable[[Format, Path], Expect]) -> Callable[[Format, Path], Expect]:
        INPUTS.append(Hostile(name, make, applies))
        return make
    return register


def _write(path: Path, fmt: Format, rows, encoding: str = "utf-8") -> None:
    path.write_bytes(fmt.write(list(rows)).encode(encoding))


@hostile("renamed-header-column", applies=lambda fmt: fmt.rename is not None)
def _renamed(fmt: Format, path: Path) -> Expect:
    old, new = fmt.rename
    path.write_text(fmt.write(list(BASE)).replace(old, new, -1 if fmt.family == "ofx" else 1), "utf-8")
    return refused({"chase_checking": registry.NOT_LAID_OUT,
                    "chase_card": "its first line is not the header this format starts with",
                    "bofa_bank": "none of its lines is the header line this format reads its rows under",
                    "ofx": ofx.NONE_READABLE}.get(fmt.name))


CAFE = Row(date(2030, 3, 15), -4_50, "CAFÉ SAMPLETOWN")


@hostile("cp1252-accented-merchant")
def _cp1252(fmt: Format, path: Path) -> Expect:
    """The native formats read UTF-8 only; the presets try Windows-1252 next, and the OFX header declares it"""
    _write(path, fmt, [*BASE, CAFE], "cp1252")
    return refused(registry.UNDECODABLE) if fmt.family == "native" else read([*BASE, CAFE])


@hostile("utf16-with-bom")
def _utf16(fmt: Format, path: Path) -> Expect:
    _write(path, fmt, BASE, "utf-16")
    return refused({"native": registry.UNDECODABLE, "preset": csv_preset.NOT_TEXT, "ofx": ofx.NOT_OFX}[fmt.family])


@hostile("utf8-bom")
def _utf8_bom(fmt: Format, path: Path) -> Expect:
    path.write_bytes(b"\xef\xbb\xbf" + fmt.write(list(BASE)).encode("utf-8"))
    return read(BASE)


@hostile("cr-line-ends")
def _cr(fmt: Format, path: Path) -> Expect:
    path.write_bytes(fmt.write(list(BASE)).replace("\n", "\r").encode("utf-8"))
    return read(BASE)


@hostile("mixed-line-ends")
def _mixed(fmt: Format, path: Path) -> Expect:
    lines = fmt.write(list(BASE)).split("\n")
    ends = ("\r\n", "\n", "\r")
    path.write_bytes("".join(line + ends[i % 3] for i, line in enumerate(lines[:-1])).encode("utf-8"))
    return read(BASE)


@hostile("quoted-newline")
def _quoted_newline(fmt: Format, path: Path) -> Expect:
    rows = [BASE[0], Row(BASE[1].day, BASE[1].cents, "STARBUCKS STORE\n00000"), *BASE[2:]]
    _write(path, fmt, rows)
    return read(BASE)                           # the line break is squashed to a space, as every whitespace run is


@hostile("trailing-totals-row")
def _totals(fmt: Format, path: Path) -> Expect:
    text = fmt.write(list(BASE))
    total = _plain(sum(r.cents for r in BASE))
    if fmt.total is None:                       # OFX: a line after the statement's end
        path.write_text(text + f"Total,-{total}\n", "utf-8")
        return read(BASE)
    path.write_text(text + _csv([fmt.total("-" + total)]), "utf-8")
    return skipped(BASE, fmt.total_skip)


@hostile("trailing-blank-lines")
def _blank_lines(fmt: Format, path: Path) -> Expect:
    path.write_text(fmt.write(list(BASE)) + "\n\n\n", "utf-8")
    return read(BASE)


@hostile("html-error-page")
def _html(fmt: Format, path: Path) -> Expect:
    path.write_text("<!DOCTYPE html>\n<html><head><title>Service unavailable</title></head>\n"
                    "<body><p>Please try again later.</p></body></html>\n", "utf-8")
    return refused(ofx.NOT_OFX if fmt.family == "ofx" else None)


@hostile("no-bytes")
def _empty(fmt: Format, path: Path) -> Expect:
    path.write_bytes(b"")
    return read([])


@hostile("nul-bytes-only")
def _nul(fmt: Format, path: Path) -> Expect:
    path.write_bytes(b"\x00" * 1024)
    return refused()


@hostile("random-bytes")
def _random(fmt: Format, path: Path) -> Expect:
    path.write_bytes(random.Random(20300101).randbytes(4096))
    return refused()


def _sparse(path: Path, size: int) -> None:
    """A file of `size` bytes none of which is written: the test never reads it either"""
    with open(path, "wb") as f:
        f.truncate(size)
    assert path.stat().st_blocks * 512 < 1_000_000


@hostile("sparse-just-over-the-cap")
def _over_cap(fmt: Format, path: Path) -> Expect:
    _sparse(path, registry.MAX_FILE_BYTES + 1)
    return refused(registry.TOO_LARGE, untouched=True)


@hostile("sparse-200-mb")
def _two_hundred_mb(fmt: Format, path: Path) -> Expect:
    _sparse(path, 200_000_000)
    return refused(registry.TOO_LARGE, untouched=True)


@hostile("pipe")
def _pipe(fmt: Format, path: Path) -> Expect:
    os.mkfifo(path)
    return refused(registry.NOT_A_FILE, untouched=True)


@hostile("folder")
def _folder(fmt: Format, path: Path) -> Expect:
    path.mkdir()
    return refused(registry.NOT_A_FILE, untouched=True)


@hostile("one-line-of-2-mb")
def _long_line(fmt: Format, path: Path) -> Expect:
    """A CSV reader stops at a field over its limit (128 KB): the file is refused. OFX reads the row, its description
    cut."""
    huge = Row(date(2030, 3, 20), -1_00, "SAMPLE " + "X" * 2_000_000)
    _write(path, fmt, [*BASE, huge])
    if fmt.family == "ofx":
        return read([*BASE, Row(huge.day, huge.cents, "SAMPLE " + "X" * 493)], cut=1)
    return refused(registry.NOT_CSV)


FORMULAS = ("=HYPERLINK(\"https://example.invalid\",\"SAMPLE\")", "+1+2 SAMPLE CAFE", "-SAMPLE GROCER",
            "@SUM(1,1) SAMPLE SHOP")


@hostile("formula-leading-descriptions")
def _formulas(fmt: Format, path: Path) -> Expect:
    rows = [*BASE, *(Row(date(2030, 3, 20 + i), -1_00 - i, text) for i, text in enumerate(FORMULAS))]
    _write(path, fmt, rows)
    return read(rows)


MARKUP = "<script>alert(1)</script><img src=x onerror=alert(2)> SAMPLE &amp; CAFE"


@hostile("html-and-script-in-a-description")
def _markup(fmt: Format, path: Path) -> Expect:
    rows = [*BASE, Row(date(2030, 3, 20), -1_00, MARKUP)]
    _write(path, fmt, rows)
    return read(rows)


CONTROL = ("SAMPLE\x01 CAFE\x07\x1b[0m TAB\tEND\x7f DEL\x85NEL\x9bCSI\N{LINE SEPARATOR}LS\N{PARAGRAPH SEPARATOR}PS"
           "\x1cFS\x0bVT\x0cFF")
# the native formats squash whitespace only (NEL and the line and paragraph separators are whitespace to it)
CONTROL_KEPT = "SAMPLE\x01 CAFE\x07\x1b[0m TAB END\x7f DEL NEL\x9bCSI LS PS FS VT FF"
# the presets and OFX first remove C0 but its whitespace, DEL and C1 but NEL; the squash then turns every whitespace
# character into a space, as it does in the native formats
CONTROL_REMOVED = "SAMPLE CAFE[0m TAB END DEL NELCSI LS PS FS VT FF"


@hostile("control-characters-in-a-description")
def _control(fmt: Format, path: Path) -> Expect:
    row = Row(date(2030, 3, 20), -1_00, CONTROL)
    _write(path, fmt, [*BASE, row])
    return read([*BASE, Row(row.day, row.cents, CONTROL_KEPT if fmt.family == "native" else CONTROL_REMOVED)])


LONG = "SAMPLE CAFE " + "X" * (10_000 - 12)


@hostile("ten-thousand-character-description")
def _ten_thousand(fmt: Format, path: Path) -> Expect:
    row = Row(date(2030, 3, 20), -1_00, LONG)
    _write(path, fmt, [*BASE, row])
    if fmt.family == "native":
        return read([*BASE, row])
    return read([*BASE, Row(row.day, row.cents, LONG[:500])], cut=1)


# Hebrew and Arabic words, a right-to-left override and its pop, an e with a combining acute accent, an emoji of two
# joined by a zero-width joiner and a Persian word written with a zero-width non-joiner
SCRIPTS = ("\N{HEBREW LETTER QOF}\N{HEBREW LETTER PE}\N{HEBREW LETTER HE} SAMPLE \N{RIGHT-TO-LEFT OVERRIDE}EFAC"
           "\N{POP DIRECTIONAL FORMATTING} CAFE\N{COMBINING ACUTE ACCENT} \N{ARABIC LETTER MEEM}\N{ARABIC LETTER QAF}"
           "\N{ARABIC LETTER HEH}\N{ARABIC LETTER ALEF MAKSURA} \N{WOMAN}\N{ZERO WIDTH JOINER}\N{PERSONAL COMPUTER} "
           "\N{ARABIC LETTER MEEM}\N{ARABIC LETTER FARSI YEH}\N{ZERO WIDTH NON-JOINER}\N{ARABIC LETTER REH}"
           "\N{ARABIC LETTER WAW}\N{ARABIC LETTER MEEM}")
# the presets and OFX remove the override and its pop, so the description reads the way it is stored; the joiners, the
# letters and the accent stay
SCRIPTS_BOUNDED = SCRIPTS.replace("\N{RIGHT-TO-LEFT OVERRIDE}", "").replace("\N{POP DIRECTIONAL FORMATTING}", "")


@hostile("right-to-left-and-combining-characters")
def _scripts(fmt: Format, path: Path) -> Expect:
    row = Row(date(2030, 3, 20), -1_00, SCRIPTS)
    _write(path, fmt, [*BASE, row])
    return read([*BASE, row if fmt.family == "native" else Row(row.day, row.cents, SCRIPTS_BOUNDED)])


# every bidirectional control (the embeddings, the overrides, the isolates and their pops; one override left open), the
# direction marks and the invisible format characters: the zero-width space, the word joiner, the invisible operators,
# the byte-order mark and the soft hyphen
INVISIBLE = ("SAMPLE\N{ZERO WIDTH SPACE}CAFE \N{LEFT-TO-RIGHT EMBEDDING}LRE\N{POP DIRECTIONAL FORMATTING} "
             "\N{RIGHT-TO-LEFT EMBEDDING}RLE\N{POP DIRECTIONAL FORMATTING} "
             "\N{LEFT-TO-RIGHT OVERRIDE}LRO\N{POP DIRECTIONAL FORMATTING} \N{RIGHT-TO-LEFT OVERRIDE}RLO "
             "\N{LEFT-TO-RIGHT ISOLATE}LRI\N{POP DIRECTIONAL ISOLATE} \N{RIGHT-TO-LEFT ISOLATE}RLI"
             "\N{POP DIRECTIONAL ISOLATE} \N{FIRST STRONG ISOLATE}FSI\N{POP DIRECTIONAL ISOLATE} "
             "LRM\N{LEFT-TO-RIGHT MARK} RLM\N{RIGHT-TO-LEFT MARK} ALM\N{ARABIC LETTER MARK} "
             "SAMPLE\N{WORD JOINER}SHOP SAMPLE\N{ZERO WIDTH NO-BREAK SPACE}STORE SOFT\N{SOFT HYPHEN}WARE "
             "X\N{FUNCTION APPLICATION}\N{INVISIBLE TIMES}\N{INVISIBLE SEPARATOR}\N{INVISIBLE PLUS}Y")
# the presets and OFX remove them all: two descriptions that look the same are stored the same
INVISIBLE_REMOVED = "SAMPLECAFE LRE RLE LRO RLO LRI RLI FSI LRM RLM ALM SAMPLESHOP SAMPLESTORE SOFTWARE XY"


@hostile("bidirectional-and-invisible-format-characters")
def _invisible(fmt: Format, path: Path) -> Expect:
    row = Row(date(2030, 3, 20), -1_00, INVISIBLE)
    _write(path, fmt, [*BASE, row])
    return read([*BASE, row if fmt.family == "native" else Row(row.day, row.cents, INVISIBLE_REMOVED)])


@hostile("dates-in-1900-and-2999")
def _far_dates(fmt: Format, path: Path) -> Expect:
    rows = [Row(date(1900, 1, 2), -5_00, "SAMPLE OLD SHOP"), *BASE, Row(date(2999, 12, 30), -6_00, "SAMPLE NEW SHOP")]
    _write(path, fmt, rows)
    return read(rows)


@hostile("a-date-that-is-no-calendar-day")
def _no_such_day(fmt: Format, path: Path) -> Expect:
    """February 30th is no date in any format: a native format skips its row as one it cannot read (etl/dates.py
    refuses the date), a preset's date patterns and OFX as a date they cannot read. The row's day is neither the
    file's first nor its last, which a statement's summary and an OFX list's bounds repeat."""
    text = fmt.write([*BASE, Row(date(2030, 3, 10), -1_00, "SAMPLE NO SUCH DAY")])
    bad = (text.replace("03/10/2030", "02/30/2030").replace("2030-03-10", "2030-02-30")
           .replace("20300310", "20300230"))
    assert bad != text
    path.write_text(bad, "utf-8")
    return skipped(BASE, "unparseable-row" if fmt.family == "native" else "unparseable-date")


def _amount(fmt: Format, path: Path, row: Row) -> None:
    _write(path, fmt, [*BASE, row])


@hostile("amount-zero")
def _zero(fmt: Format, path: Path) -> Expect:
    row = Row(date(2030, 3, 20), 0, "SAMPLE ZERO")
    _amount(fmt, path, row)
    return read([*BASE, row])


@hostile("amount-a-tenth-of-a-cent")
def _tenth_of_a_cent(fmt: Format, path: Path) -> Expect:
    """The native formats round to the cent; the presets and OFX read no fraction of a cent"""
    row = Row(date(2030, 3, 20), -1, "SAMPLE TENTH", text="0.001")
    _amount(fmt, path, row)
    if fmt.family == "native":
        return read([*BASE, Row(row.day, 0, row.desc)])
    return skipped(BASE, "unparseable-row")


@hostile("amount-a-billion")
def _billion(fmt: Format, path: Path) -> Expect:
    row = Row(date(2030, 3, 20), -1_000_000_000_00, "SAMPLE BIG SPEND")
    _amount(fmt, path, row)
    return read([*BASE, row])


@hostile("amount-of-twenty-digits")
def _twenty_digits(fmt: Format, path: Path) -> Expect:
    """An amount past 10^17 cents is no amount any ledger holds (and SQLite cannot store it): its row is skipped as
    unreadable in every format, and the rest of the file is read"""
    _amount(fmt, path, Row(date(2030, 3, 20), -1, "SAMPLE HUGE SPEND", text="99999999999999999999.00"))
    return skipped(BASE, "unparseable-row")


@hostile("amount-written-1e9")
def _exponent(fmt: Format, path: Path) -> Expect:
    _amount(fmt, path, Row(date(2030, 3, 20), -1, "SAMPLE EXPONENT", text="1e9"))
    return skipped(BASE, "unparseable-row")


@hostile("amount-with-a-dollar-sign")
def _dollar(fmt: Format, path: Path) -> Expect:
    row = Row(date(2030, 3, 20), -12_34, "SAMPLE DOLLAR", text="$12.34")
    _amount(fmt, path, row)
    return skipped(BASE, "unparseable-row") if fmt.family == "ofx" else read([*BASE, row])


@hostile("amount-with-a-euro-sign")
def _euro(fmt: Format, path: Path) -> Expect:
    _amount(fmt, path, Row(date(2030, 3, 20), -12_34, "SAMPLE EURO", text="€12.34"))
    return skipped(BASE, "unparseable-row")


CASES = [(h, f) for h in INPUTS for f in FORMATS.values() if h.applies(f)]


@pytest.mark.parametrize(("case", "fmt"), CASES, ids=[f"{h.name}-{f.name}" for h, f in CASES])
def test_a_hostile_file_is_read_right_refused_or_skipped_never_raised(tmp_path, monkeypatch, case, fmt):
    cfg, calls = _household(tmp_path, fmt, monkeypatch)
    path = tmp_path / "src" / f"statement-1{fmt.suffix}"
    expect = case.make(fmt, path)
    _assert_outcome(_parsed(tmp_path, cfg), fmt, path, expect, calls)


# every format refuses every other's file, but for the two that share one headerless layout
TWINS = {("wells_fargo", "sample_headerless"), ("sample_headerless", "wells_fargo")}
PAIRS = [(f, g) for f in FORMATS.values() for g in FORMATS.values() if f is not g]


@pytest.mark.parametrize(("fmt", "other"), PAIRS, ids=[f"{f.name}-reads-{g.name}" for f, g in PAIRS])
def test_another_formats_file_is_refused_never_read_with_a_wrong_sign(tmp_path, monkeypatch, fmt, other):
    cfg, calls = _household(tmp_path, fmt, monkeypatch)
    path = tmp_path / "src" / f"statement-1{other.suffix}"
    _write(path, other, BASE)
    expect = read(BASE) if (fmt.name, other.name) in TWINS else refused()
    _assert_outcome(_parsed(tmp_path, cfg), fmt, path, expect, calls)


def _many(n: int) -> list[Row]:
    start = date(2030, 1, 1)
    return [Row(start + timedelta(days=i % 365), (i % 9_000 + 100) * 100 if i % 5 == 0 else -((i * 7_919) % 50_000 + 1),
                f"SAMPLE STORE {i:05d}") for i in range(n)]


@pytest.mark.parametrize("fmt", FORMATS.values(), ids=list(FORMATS))
def test_fifty_thousand_rows_are_read_in_a_few_seconds(tmp_path, monkeypatch, fmt):
    cfg, calls = _household(tmp_path, fmt, monkeypatch)
    path = tmp_path / "src" / f"statement-1{fmt.suffix}"
    rows = _many(50_000)
    _write(path, fmt, rows)
    started = time.monotonic()
    p = _parsed(tmp_path, cfg)
    assert time.monotonic() - started < MANY_ROWS_SECONDS
    _assert_outcome(p, fmt, path, read(rows), calls)


# ------------------------------------------------------------------ the description bound

FORMAT_CHARACTERS = ("\N{SOFT HYPHEN}", "\N{ARABIC LETTER MARK}", "\N{ZERO WIDTH SPACE}", "\N{LEFT-TO-RIGHT MARK}",
                     "\N{RIGHT-TO-LEFT MARK}", *(chr(c) for c in range(0x202A, 0x202F)),
                     *(chr(c) for c in range(0x2060, 0x2065)), *(chr(c) for c in range(0x2066, 0x206A)),
                     "\N{ZERO WIDTH NO-BREAK SPACE}")


@pytest.mark.parametrize("char", [*(chr(c) for c in range(0x00, 0x09)), *(chr(c) for c in range(0x0E, 0x1C)),
                                  "\x7f", *(chr(c) for c in range(0x80, 0x85)), *(chr(c) for c in range(0x86, 0xA0)),
                                  *FORMAT_CHARACTERS],
                         ids=lambda c: f"U+{ord(c):04X}")
def test_a_control_or_invisible_format_character_is_removed_from_a_bounded_description(char):
    assert registry.bounded_description(f"SAMPLE{char}CAFE {char}SHOP") == ("SAMPLECAFE SHOP", False)


@pytest.mark.parametrize("char", ["\t", "\n", "\x0b", "\x0c", "\r", "\x1c", "\x1d", "\x1e", "\x1f", " ",
                                  "\x85", "\N{LINE SEPARATOR}", "\N{PARAGRAPH SEPARATOR}"],
                         ids=lambda c: f"U+{ord(c):04X}")
def test_whitespace_among_the_control_characters_is_squashed_to_a_space(char):
    """Whitespace is never removed, so the words it parts stay apart, as the native formats keep them"""
    assert registry.bounded_description(f"  SAMPLE{char}{char}CAFE{char}") == ("SAMPLE CAFE", False)


def test_a_bounded_description_keeps_what_is_not_a_control_or_invisible_format_character():
    """Formula-leading text, markup, the scripts' letters, a combining accent and the zero-width joiner and non-joiner,
    which emoji and scripts need"""
    text = f"{FORMULAS[0]} {MARKUP} {SCRIPTS_BOUNDED}"
    assert "\N{ZERO WIDTH JOINER}" in text and "\N{ZERO WIDTH NON-JOINER}" in text
    assert registry.bounded_description(text) == (text, False)


def test_a_description_over_500_characters_is_cut_to_its_first_500():
    assert registry.MAX_DESCRIPTION == 500
    assert registry.bounded_description("X" * 500) == ("X" * 500, False)
    assert registry.bounded_description("X" * 501) == ("X" * 500, True)
    # the length is the text's once its control characters are removed and its whitespace squashed; a space the cut
    # leaves at the end goes, as the squash leaves none
    assert registry.bounded_description("X" * 498 + "\x07" * 10 + "  Y") == ("X" * 498 + " Y", False)
    assert registry.bounded_description("X" * 499 + "\x07" * 10 + "  YZ") == ("X" * 499, True)


def test_the_long_description_check_counts_the_rows_an_account_stores_once(tmp_path, monkeypatch):
    """Two OFX downloads of one account repeat one row whose name is cut and add one each: three rows are stored with a
    cut description, and the account's one info check counts three."""
    fmt = FORMATS["ofx"]
    cfg, _ = _household(tmp_path, fmt, monkeypatch)
    long = [Row(date(2030, 3, 20 + i), -1_00 - i, f"SAMPLE {i} " + "X" * 600) for i in range(3)]
    _write(tmp_path / "src" / "statement-1.ofx", fmt, [*BASE, long[0], long[1]])
    _write(tmp_path / "src" / "statement-2.ofx", fmt, [long[1], long[2]])
    p = _parsed(tmp_path, cfg)
    assert sorted(t.description_raw for t in p.txns if len(t.description_raw) == 500) == [
        f"SAMPLE {i} " + "X" * (500 - len(f"SAMPLE {i} ")) for i in range(3)]
    assert [(c.name, c.status, c.detail) for c in p.checks if c.name.startswith("import.long_description.")] == [
        (f"import.long_description.{ACCOUNT}", "info", _long_words(3))]
    assert p.overlaps == {"statement-2.ofx": 1}


def test_one_row_cut_is_one_in_the_words(tmp_path, monkeypatch):
    fmt = FORMATS["bofa_card"]
    cfg, _ = _household(tmp_path, fmt, monkeypatch)
    _write(tmp_path / "src" / "statement-1.csv", fmt, [*BASE, Row(date(2030, 3, 20), -1_00, LONG)])
    p = _parsed(tmp_path, cfg)
    assert [c.detail for c in p.checks if c.name == f"import.long_description.{ACCOUNT}"] == [
        "1 row of this account's statement files has a description longer than 500 characters, stored cut to its "
        "first 500."]

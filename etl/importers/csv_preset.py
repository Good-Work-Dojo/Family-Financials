# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""CSV presets: one YAML file describes one statement layout, and is one format the registry holds under its id.

A preset is data. Its keys:
  id            the format name an account's `parser:` states: lowercase letters, digits and _, starting with a letter;
                the file is named <id>.yaml
  label         what a person calls the export, e.g. "Bank of America credit card"
  kind          bank or card: the kind of account the export comes from
  source        where the layout comes from: the bank's own help page, or an open-source project, its file and licence
  evidence      how far the layout is known: bank-documented (the bank's own help describes it), oss-definition (an
                open-source project under a licence that allows its use defines it), widely-reported (several
                independent public descriptions agree on it and none is a definition with such a licence) or
                sample-verified (run on a real export). A preset the tests write for themselves states synthetic,
                which load_preset accepts only when asked to (allow_synthetic). A household's own preset
                (config/presets/<id>.yaml, loaded with `household`) may leave source and evidence out: it is its
                own file, and either reads as empty text
  institution   the bank or card issuer the export comes from, e.g. "Bank of America"
  download      how a person gets the file from the bank, in one line. The table of supported banks
                (docs/importing-banks.md) prints both; every shipped preset states them, and any preset may leave them
                out, either reading as empty text
  encoding      the text encodings tried in order (default utf-8-sig, then cp1252)
  search_rows   with a header: how many lines may come before the header line (default 0; blank lines not counted)
  order         newest_first or oldest_first: the order of the file's rows, needed to follow a `balance` column
  detect        header: [names]  the header line holds each of these column names, compared trimmed and ignoring
                                 case; other columns may be there too
                headerless: {columns: N, constant: {position: text}}  no header line: the first row has exactly N
                                 cells, and each listed cell holds its text (trimmed); so must every later row, or
                                 it is skipped
  date          {column, format}: format a strptime pattern or a list of them, tried in order; a list in which one
                date written in one pattern reads as another date under another (03/04 as March 4 and as April 3) is
                refused. A card's preset names the posting date when the file has one, as the bank feed and OFX date
                a card's rows, so fewer purchases are in flight where two sources of one account meet
  amount        {column, sign}: one signed column; sign as_is (a positive amount is money in) or invert (a positive
                amount is money out, as on a card export whose purchases are positive). The sign is stated, never
                detected.
                {debit, credit}: two columns of unsigned amounts; a debit is money out (stored negative), a credit
                money in. A zero in one of them while the other holds an amount is read as empty
  description   {columns: [...], join}: the non-empty cells of these columns joined with `join` (default one space),
                stored through registry.bounded_description (control characters but whitespace, bidirectional
                controls and invisible format characters removed, every run of whitespace squashed to one space, cut
                to its first 500 characters); it may come out empty
  skip          pending: {column, equals}  a row whose cell holds this text (trimmed, ignoring case) is pending
                empty_amount: true         a row with no amount is skipped as having none (default false: such a row
                                           is one this format cannot read)
                trailing_rows_matching: a regular expression searched in the first 1,000 characters of a row's cells
                                           joined by commas; the rows at the end of the file that match it (a totals
                                           line) are skipped
  bank_type, bank_category, balance   optional columns: the bank's own type and category, and the running balance
A column is a name from `detect.header` in a header preset, and a position counted from 1 in a headerless one.

What a parse returns keeps the registry's contract (etl/importers/registry.py). Every row the CSV reader yields is
counted: read, or skipped with a reason (blank-line, preamble, header, repeated-header, constant-mismatch, pending,
unparseable-date, no-amount, both-debit-and-credit, signed-debit-or-credit, unparseable-row, trailing-row). Nothing is
guessed:
  * a row is as wide as the header line (to its last named column): one with fewer cells, or with a non-empty cell past
    it (an unquoted comma in a field), is unparseable-row; a headerless row has exactly its `columns` cells;
  * a headerless row whose constant cell does not hold its text is no row of the layout: constant-mismatch (the first
    row's refuses the file);
  * a money cell groups its digits in threes with commas, if it has commas, before an optional decimal point, with
    at most one sign or one pair of parentheses, and holds no fraction of a cent: `12,50` or `1.234,56` (a decimal
    comma), `12%` or `0.001` is unparseable-row;
  * a row whose date fits none of the patterns is unparseable-date;
  * when more than half of the rows that reach their date (pending rows aside) are unreadable (constant-mismatch,
    unparseable-date, unparseable-row, both-debit-and-credit, signed-debit-or-credit), the file is refused: its columns
    are not laid out the way the preset's are;
  * a record the CSV reader joined across several lines that is unreadable has a quote that is never closed: the file
    is refused, since every row after it is folded into it. A quoted field that holds a line break and reads is fine.
A file with no line but blank ones (no bytes at all) reads as no rows. `extra` holds `cut_descriptions`, the positions
of the rows read whose description was cut, when there is one; and when the preset names a `balance` column and the
file has a row
  latest_balance    (date, cents) of the newest row with a balance: the greatest date, and of that day's rows the
                    latest by the file's stated order
  running_balances  the rows in the order they happened (the file's order, turned round when newest_first), each
                    (date, amount_cents, balance_cents or None) for a row read, and None for a skipped row that
                    carried an amount: it moved the balance, so the running-balance check
                    (etl/quality/reconcile.py rollforward_check) restarts after it and counts it

The file is read as a stream. Its first 64 KB choose the encoding: the first in the preset's list that decodes them
and holds no NUL character (a text export never does; a UTF-16 file read byte by byte does). A byte past them that the
chosen encoding cannot decode sends the read back to the start in the next encoding that decodes the first 64 KB to the
very same text (as any of them does an ASCII start), so no row read before that byte changes; with none such, the file
is refused. A file that does not have the preset's layout raises NotThisFormat, whose words name what the format
expects and what the file has (how many cells, which of the preset's columns it lacks or holds twice), never a value
from the file."""
from __future__ import annotations

import codecs
import csv
import datetime as dt
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..models import FileStats, Txn
from ..money import parse_cents
from ..validate import (
    ConfigError,
    Problem,
    Problems,
    unbuildable_value_problem,
    unreadable_problem,
    yaml_problem,
)
from .registry import (
    DEFAULT_ENCODINGS,
    UNPARSEABLE_ROW,
    NotThisFormat,
    bounded_description,
    file_head,
    sniff,
)

KINDS = ("bank", "card")
EVIDENCE = ("bank-documented", "oss-definition", "widely-reported", "sample-verified")
# the evidence of a preset the tests write for themselves: never a shipped or a household's preset
SYNTHETIC = "synthetic"
ORDERS = ("newest_first", "oldest_first")
SIGNS = ("as_is", "invert")
# more lines than a bank puts above its header: a larger number is a mistake in the preset
MAX_SEARCH_ROWS = 50
# how much of a row the trailing-row pattern reads: a totals line is short, and a pattern must not run over a huge row
TRAILING_SCAN_CHARS = 1_000

NOT_TEXT = "its bytes are not text in an encoding this format reads"
MIXED_TEXT = ("its text is not in any one encoding this format reads: a part after its start is written in another "
              "one")
COLUMNS_DO_NOT_FIT = ("most of its lines have no date or amount this format can read where it reads them: its columns "
                      "are not laid out the way this format's are")
UNCLOSED_QUOTE = "a quote in it is never closed, so the lines after it run together"

# the skip reasons of a row that is in the file's body but cannot be read as this format reads a row
CONSTANT_MISMATCH = "constant-mismatch"
UNREADABLE = frozenset({CONSTANT_MISMATCH, "unparseable-date", UNPARSEABLE_ROW, "both-debit-and-credit",
                        "signed-debit-or-credit"})

_ID = re.compile(r"[a-z][a-z0-9_]*\Z")
_KEYS = ("id", "label", "kind", "source", "evidence", "institution", "download", "encoding", "search_rows", "order",
         "detect", "date", "amount", "description", "skip", "bank_type", "bank_category", "balance")
# the keys inside each block of a preset, by the block's key path (detect.headerless.constant is keyed by positions);
# docs/configuration.md lists _KEYS and these, and nothing else (tests/test_import_cli.py pins the two)
_BLOCK_KEYS: dict[str, tuple[str, ...]] = {
    "detect": ("header", "headerless"),
    "detect.headerless": ("columns", "constant"),
    "date": ("column", "format"),
    "amount": ("column", "sign", "debit", "credit"),
    "description": ("columns", "join"),
    "skip": ("pending", "empty_amount", "trailing_rows_matching"),
    "skip.pending": ("column", "equals"),
}
# a date every pattern must write and read back whole: a pattern without the year, the month or the day cannot
_PROBE = dt.datetime(2030, 11, 23)
# a date whose day could be a month: two patterns that read its text as two different dates confuse each other
_AMBIGUOUS_PROBE = dt.datetime(2030, 3, 4)
# what is left of a money cell once its sign or parentheses, its dollar sign and its spaces are taken away: digits
# grouped in threes by commas (or not grouped at all), then an optional decimal part
_MONEY = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d*)(?:\.\d+)?")


def money_cents(text: str) -> int | None:
    """A money cell's signed cents (money.parse_cents), None for an empty one. Raises ValueError for a cell that is not
    an amount written the way a statement writes one: commas that do not group the digits in threes before the
    decimal point (a decimal comma: `12,50`, `1.234,56`), a percent, a sign inside parentheses, a fraction of a cent
    (`0.001`: a statement never writes one, so the column is no amount of money and is never rounded into one)."""
    value = parse_cents(text)
    if value is None:
        return None
    core = "".join(text.split()).replace("$", "")
    if core.startswith("(") and core.endswith(")"):
        core = core[1:-1]
    elif core[:1] in ("-", "+"):
        core = core[1:]
    if not core or not _MONEY.fullmatch(core):
        raise ValueError("not an amount")
    if core.partition(".")[2][2:].strip("0"):
        raise ValueError("a fraction of a cent")
    return value

Column = str | int


def _fold(text: str) -> str:
    return text.strip().casefold()


def _blank(row: list[str]) -> bool:
    return all(not cell.strip() for cell in row)


def _listing(names: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'"""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


@dataclass(frozen=True)
class Preset:
    """One loaded preset (load_preset builds it). Columns are as the file states them: header names, or positions
    counted from 1."""
    id: str
    label: str
    kind: str
    source: str
    evidence: str
    institution: str
    download: str
    encodings: tuple[str, ...]
    header: tuple[str, ...] | None
    cells: int | None
    constants: tuple[tuple[int, str], ...]
    search_rows: int
    order: str | None
    date_column: Column
    date_formats: tuple[str, ...]
    amount_column: Column | None
    sign: str | None
    debit: Column | None
    credit: Column | None
    description_columns: tuple[Column, ...]
    join: str
    pending: tuple[Column, str] | None
    empty_amount: bool
    trailing: re.Pattern | None
    bank_type: Column | None
    bank_category: Column | None
    balance: Column | None

    # ------------------------------------------------------------------ reading a file

    def parse(self, path: Path, account_id: str) -> tuple[list[Txn], FileStats, dict]:
        """The rows of one file of this layout for `account_id` (the registry's contract)."""
        for encoding in self._encodings(path):
            try:
                with open(path, newline="", encoding=encoding) as f:
                    return self._read(csv.reader(f), path.name, account_id)
            except UnicodeError:
                continue
        raise NotThisFormat(MIXED_TEXT)

    def _encodings(self, path: Path) -> list[str]:
        """The encodings a read may use, in the preset's order, as the registry's sniff gives them for the file's first
        64 KB: the first that reads them as text with no NUL, then each later one that reads them to the very same
        text (a read that restarts in it after a later byte fails changes no row read before that byte)."""
        usable, _ = sniff(*file_head(path), self.encodings)
        if not usable:
            raise NotThisFormat(NOT_TEXT)
        return usable

    def _read(self, records, name: str, account_id: str) -> tuple[list[Txn], FileStats, dict]:
        """Reads the CSV reader `records` (its line_num tells how many lines a record spanned)."""
        stats = FileStats(source_file=name, parser=self.id)
        width = None
        at = None
        if self.header is not None:
            found = self._find_header(records, stats)
            if found is None:                       # no line but blank ones: no rows
                return [], stats, {}
            at, width = found
        col = self._resolver(at)
        reader = _RowReader(self, col, width, stats, name, account_id)
        # rows matching the trailing pattern, not yet known to be trailing: (index, row, spanned several lines)
        held: list[tuple[int, list[str], bool]] = []
        first = True
        line = records.line_num
        for i, row in enumerate(records):
            spanned = records.line_num - line > 1
            line = records.line_num
            stats.rows_total += 1
            if _blank(row):
                stats.skip("blank-line")
                continue
            if self.cells is not None and first:
                self._detect_shape(row)
            first = False
            if at is not None and self._is_header(row):
                stats.skip("repeated-header")
                continue
            if self.trailing is not None and self.trailing.search(",".join(row)[:TRAILING_SCAN_CHARS]):
                held.append((i, row, spanned))
                continue
            for j, earlier, earlier_spanned in held:
                self._row(reader, j, earlier, earlier_spanned)
            held.clear()
            self._row(reader, i, row, spanned)
        if held:
            stats.skip("trailing-row", len(held))
        if reader.unreadable * 2 > reader.body:
            raise NotThisFormat(COLUMNS_DO_NOT_FIT)
        return reader.txns, stats, reader.extra()

    @staticmethod
    def _row(reader: _RowReader, i: int, row: list[str], spanned: bool) -> None:
        """One body row; an unreadable one the CSV reader joined across lines has a quote that is never closed"""
        if reader.row(i, row) in UNREADABLE and spanned:
            raise NotThisFormat(UNCLOSED_QUOTE)

    def _find_header(self, rows: Iterator[list[str]],
                     stats: FileStats) -> tuple[dict[str, int], int] | None:
        """Reads up to the header line, counting the lines before it, and returns each header name's position and the
        header's width (up to its last non-empty cell); None for a file with no line but blank ones. Raises
        NotThisFormat when none of the first search_rows + 1 non-blank lines is the header, or the header holds one of
        the preset's columns twice."""
        wanted = [_fold(n) for n in self.header]
        examined = 0
        closest: list[str] | None = None            # the preset's columns the closest line lacks
        for row in rows:
            stats.rows_total += 1
            if _blank(row):
                stats.skip("blank-line")
                continue
            folded = [_fold(cell) for cell in row]
            missing = [n for n, w in zip(self.header, wanted, strict=True) if w not in folded]
            if not missing:
                twice = [n for n, w in zip(self.header, wanted, strict=True) if folded.count(w) > 1]
                if twice:
                    raise NotThisFormat(f"its header names the column{'s' if len(twice) > 1 else ''} "
                                        f"{_listing(twice)} more than once, so this format cannot tell which to read")
                stats.skip("header")
                width = max(pos for pos, cell in enumerate(folded) if cell) + 1
                return {w: folded.index(w) for w in wanted}, width
            examined += 1
            if closest is None or len(missing) < len(closest):
                closest = missing
            if examined > self.search_rows:
                break
            stats.skip("preamble")
        if not examined:
            return None
        raise NotThisFormat(self._header_miss(examined, closest))

    def _header_miss(self, examined: int, closest: list[str]) -> str:
        where = "its first line is not" if examined == 1 else f"none of its first {examined} lines is"
        if len(closest) == len(self.header):
            return (f"{where} the header this format starts with, which names the columns "
                    f"{_listing(list(self.header))}")
        lacks = f"the column{'s' if len(closest) > 1 else ''} {_listing(closest)}"
        if examined == 1:
            return f"its header lacks {lacks} that this format reads"
        return f"{where} the header this format starts with; the closest lacks {lacks} that this format reads"

    def _is_header(self, row: list[str]) -> bool:
        cells = {_fold(c) for c in row}
        return all(_fold(n) in cells for n in self.header)

    def _detect_shape(self, row: list[str]) -> None:
        """A headerless file's first row: exactly `cells` cells, each constant cell holding its text"""
        if len(row) != self.cells:
            raise NotThisFormat(f"its first line has {len(row)} cell{'s' if len(row) != 1 else ''}, and this "
                                f"format's lines have {self.cells}")
        for pos, text in self.constants:
            if row[pos - 1].strip() != text:
                raise NotThisFormat(f'its first line\'s cell {pos} does not hold "{text}" as this format\'s lines do')

    def _resolver(self, at: dict[str, int] | None) -> Callable[[Column | None], int | None]:
        """Column -> its 0-based position in a row (None stays None)"""
        def col(c: Column | None) -> int | None:
            if c is None:
                return None
            return at[_fold(c)] if at is not None else int(c) - 1
        return col


class _RowReader:
    """Reads the rows of one file after its header: the rows read, the skip counts, how many body rows there were and
    how many of them could not be read, and the running balances."""

    def __init__(self, preset: Preset, col: Callable[[Column | None], int | None], width: int | None,
                 stats: FileStats, name: str, account_id: str):
        self.p = preset
        self.width = width                          # a header preset's row width; None for a headerless one
        self.stats = stats
        self.name = name
        self.account_id = account_id
        self.txns: list[Txn] = []
        self.cut: list[int] = []                    # the positions in txns of the rows whose description was cut
        self.body = self.unreadable = 0             # rows that reached their date (pending ones aside), unreadable ones
        self.chain: list[tuple[str, int, int | None] | None] | None = [] if preset.balance is not None else None
        self.date_at = col(preset.date_column)
        self.amount_at = col(preset.amount_column)
        self.debit_at, self.credit_at = col(preset.debit), col(preset.credit)
        self.desc_at = [col(c) for c in preset.description_columns]
        self.pending_at = col(preset.pending[0]) if preset.pending else None
        self.type_at, self.category_at = col(preset.bank_type), col(preset.bank_category)
        self.balance_at = col(preset.balance)

    @staticmethod
    def _cell(row: list[str], at: int | None) -> str:
        return row[at] if at is not None and at < len(row) else ""

    def _skip(self, reason: str, row: list[str] | None = None) -> str:
        """Counts a skipped row and returns its reason; one that carried an amount (or may have: `row` None) breaks the
        running balance"""
        self.stats.skip(reason)
        if reason in UNREADABLE:
            self.unreadable += 1
        if self.chain is not None and (row is None or self._moves(row)):
            self.chain.append(None)
        return reason

    def _moves(self, row: list[str]) -> bool:
        """Whether a skipped row carries a non-zero amount, so it moved the balance; an amount cell that cannot be read
        may have"""
        cells = [self.amount_at] if self.amount_at is not None else [self.debit_at, self.credit_at]
        for at in cells:
            try:
                if money_cents(self._cell(row, at)):
                    return True
            except ValueError:
                return True
        return False

    def _misshapen(self, row: list[str]) -> bool:
        """A row of another width than the file's rows: a headerless row without exactly its cells, or a row with fewer
        cells than the header or a non-empty cell past it (an unquoted comma inside a field shifts every later cell)"""
        if self.width is None:
            return len(row) != self.p.cells
        return len(row) < self.width or any(cell.strip() for cell in row[self.width:])

    def _date(self, text: str) -> str | None:
        for fmt in self.p.date_formats:
            try:
                return dt.datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
        return None

    def _amount(self, row: list[str]) -> tuple[int | None, str | None]:
        """(amount, None), or (None, the reason the row is skipped). Raises ValueError for a cell that is no amount."""
        p = self.p
        if self.amount_at is not None:
            value = money_cents(self._cell(row, self.amount_at))
            if value is None:
                return None, "no-amount" if p.empty_amount else UNPARSEABLE_ROW
            return (-value if p.sign == "invert" else value), None
        debit = money_cents(self._cell(row, self.debit_at))
        credit = money_cents(self._cell(row, self.credit_at))
        if debit is not None and credit is not None:
            # a zero beside an amount is the empty side, as a bank that writes 0.00 in the unused column means it
            if debit == 0:
                debit = None
            elif credit == 0:
                credit = None
        if debit is None and credit is None:
            return None, "no-amount" if p.empty_amount else UNPARSEABLE_ROW
        if debit is not None and credit is not None:
            return None, "both-debit-and-credit"
        if (debit if debit is not None else credit) < 0:
            return None, "signed-debit-or-credit"
        return (-debit if debit is not None else credit), None

    def row(self, i: int, row: list[str]) -> str | None:
        """Reads one body row: None when it is read, else the reason it is skipped"""
        p = self.p
        if self._misshapen(row):                    # no cell of it can be trusted, its pending one neither
            self.body += 1
            return self._skip(UNPARSEABLE_ROW)
        if any(row[pos - 1].strip() != text for pos, text in p.constants):
            # a headerless row of the right width whose marker is not there: another layout's row, none of its cells
            # trusted
            self.body += 1
            return self._skip(CONSTANT_MISMATCH)
        if p.pending is not None and _fold(self._cell(row, self.pending_at)) == _fold(p.pending[1]):
            return self._skip("pending", row)
        self.body += 1
        date = self._date(self._cell(row, self.date_at).strip())
        if date is None:
            return self._skip("unparseable-date", row)
        try:
            amount, reason = self._amount(row)
        except ValueError:
            return self._skip(UNPARSEABLE_ROW)
        if reason is not None:
            return self._skip(reason, row)
        parts = (self._cell(row, at).strip() for at in self.desc_at)
        description, cut = bounded_description(p.join.join(part for part in parts if part))
        if cut:
            self.cut.append(len(self.txns))
        self.txns.append(Txn(date=date, account_id=self.account_id, description_raw=description,
                             amount_cents=amount, source_file=self.name,
                             bank_type=self._cell(row, self.type_at).strip() or None,
                             bank_category=self._cell(row, self.category_at).strip() or None, seq=i))
        self.stats.observe(date, amount)
        if self.chain is not None:
            try:
                balance = money_cents(self._cell(row, self.balance_at))
            except ValueError:
                balance = None
            self.chain.append((date, amount, balance))
        return None

    def extra(self) -> dict:
        out: dict = {"cut_descriptions": frozenset(self.cut)} if self.cut else {}
        if not self.chain:                          # no balance column, or no row that read or moved it
            return out
        chain = self.chain[::-1] if self.p.order == "newest_first" else self.chain
        latest = None
        for item in chain:
            if item is not None and item[2] is not None and (latest is None or item[0] >= latest[0]):
                latest = (item[0], item[2])
        return {**out, "latest_balance": latest, "running_balances": chain}


# ------------------------------------------------------------------ loading a preset

def load_preset(path: Path, file: str | None = None, *, allow_synthetic: bool = False,
                household: bool = False) -> Preset:
    """The preset in the YAML file `path` (read_preset_doc, then build_preset with the file's name as the id it must
    state). Every problem the file has is collected and raised at once as a ConfigError, each naming `file` (default:
    the file's folder and name, e.g. presets/<id>.yaml), the key path, what is wrong and how to fix it."""
    file = file or f"{path.parent.name}/{path.name}"
    return build_preset(read_preset_doc(path, file), file, path.stem, allow_synthetic=allow_synthetic,
                        household=household)


def read_preset_doc(path: Path, file: str):
    """The YAML document in the preset file `path`; a file that cannot be read or is not valid YAML raises a
    ConfigError of its one problem, naming `file`."""
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError([yaml_problem(file, e)]) from None
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError([unreadable_problem(file, e)]) from None
    except (ValueError, OverflowError) as e:
        raise ConfigError([unbuildable_value_problem(file, path, e)]) from None


def build_preset(doc, file: str, stem: str, *, allow_synthetic: bool = False, household: bool = False) -> Preset:
    """The preset a loaded YAML document describes, its file named `stem` (the id it must state). Every problem is
    collected and raised at once as a ConfigError naming `file`. `evidence: synthetic` is accepted only with
    `allow_synthetic` (the tests' own presets); a `household` preset (config/presets/) may leave source and evidence
    out. `python -m etl import map` builds the preset it writes through here, so it never writes one this refuses."""
    if not isinstance(doc, dict):
        raise ConfigError([Problem(file, "", "not a mapping of a preset's keys",
                                   "write id, label, kind, source, evidence, detect, date, amount and description")])
    problems = Problems()
    preset = _Builder(doc, file, stem, problems, allow_synthetic, household).build()
    if problems:
        raise ConfigError(problems)
    return preset


class _Builder:
    """Checks a preset document key by key, adding each problem to `problems`, and builds the Preset (meaningful only
    when no problem was found)."""

    def __init__(self, doc: dict, file: str, stem: str, problems: Problems, allow_synthetic: bool,
                 household: bool = False):
        self.doc, self.file, self.stem, self.problems = doc, file, stem, problems
        self.allow_synthetic = allow_synthetic
        self.household = household
        self.header: tuple[str, ...] | None = None
        self.cells: int | None = None
        self.detect_ok = False

    def add(self, path: str, message: str, fix: str) -> None:
        self.problems.add(Problem(self.file, path, message, fix))

    def unknown(self, mapping: dict, allowed: tuple[str, ...], prefix: str) -> None:
        for key in mapping:
            if key not in allowed:
                self.add(f"{prefix}{key}", "not a key a preset has", f"use one of {', '.join(allowed)}")

    def block_keys(self, mapping: dict, path: str) -> None:
        """The unknown keys of the block at key path `path` (_BLOCK_KEYS)"""
        self.unknown(mapping, _BLOCK_KEYS[path], f"{path}.")

    def mapping(self, value, path: str, required: bool = True) -> dict | None:
        if value is None and not required:
            return None
        if not isinstance(value, dict):
            self.add(path, "missing" if value is None else "not a mapping", f"write {path} as a mapping of its keys")
            return None
        return value

    def text(self, value, path: str, fix: str) -> str:
        if not isinstance(value, str) or not value.strip():
            self.add(path, "missing" if value is None else "empty" if isinstance(value, str) else "not text", fix)
            return ""
        return value.strip()

    def choice(self, value, path: str, options: tuple[str, ...]) -> str:
        if value not in options or not isinstance(value, str):
            self.add(path, "missing" if value is None else f"{value!r} is not one of them", f"write one of "
                                                                                          f"{', '.join(options)}")
            return ""
        return value

    def column(self, value, path: str) -> Column | None:
        """A column reference: a header name in a header preset, a position from 1 in a headerless one"""
        if not self.detect_ok:
            return None
        if self.header is not None:
            if not isinstance(value, str) or _fold(value) not in {_fold(n) for n in self.header}:
                self.add(path, "names no column of detect.header", "name one of detect.header's columns, or add the "
                                                                   "column there")
                return None
            return value.strip()
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= self.cells:
            self.add(path, "not a column position", f"write a position from 1 to {self.cells}")
            return None
        return value

    def build(self) -> Preset:
        doc = self.doc
        self.unknown(doc, _KEYS, "")
        preset_id = doc.get("id")
        if not isinstance(preset_id, str) or not _ID.match(preset_id):
            self.add("id", "missing" if preset_id is None else "not a format name",
                     "write lowercase letters, digits and _, starting with a letter")
            preset_id = ""
        elif preset_id != self.stem:
            self.add("id", f"{preset_id!r} is not the file's name", f"name the file {preset_id}.yaml, or write id: "
                                                                    f"{self.stem}")
        label = self.text(doc.get("label"), "label", "write what a person calls this export")
        kind = self.choice(doc.get("kind"), "kind", KINDS)
        # a household's own preset may leave out where its layout comes from and how far it is known
        own = self.household
        source = "" if own and doc.get("source") is None else self.text(
            doc.get("source"), "source", "write where the layout comes from: the bank's help page, or an open-source "
                                         "project, its file and its licence")
        if own and doc.get("evidence") is None:
            evidence = ""
        elif doc.get("evidence") == SYNTHETIC and not self.allow_synthetic:
            self.add("evidence", "synthetic is the evidence of a preset the tests write for themselves",
                     f"write one of {', '.join(EVIDENCE)}")
            evidence = ""
        else:
            evidence = self.choice(doc.get("evidence"), "evidence",
                                   (*EVIDENCE, SYNTHETIC) if self.allow_synthetic else EVIDENCE)
        institution = "" if doc.get("institution") is None else self.text(
            doc["institution"], "institution", "write the bank or card issuer the export comes from")
        download = "" if doc.get("download") is None else self.text(
            doc["download"], "download", "write in one line how to download the file from the bank")
        encodings = self.encodings(doc.get("encoding"))
        self.detect(doc.get("detect"))
        search_rows = self.search_rows(doc.get("search_rows"))
        order = None if doc.get("order") is None else self.choice(doc.get("order"), "order", ORDERS)
        date_column, date_formats = self.date(doc.get("date"))
        amount = self.amount(doc.get("amount"))
        description_columns, join = self.description(doc.get("description"))
        pending, empty_amount, trailing = self.skip(doc.get("skip"))
        extras = {key: None if doc.get(key) is None else self.column(doc[key], key)
                  for key in ("bank_type", "bank_category", "balance")}
        if extras["balance"] is not None and doc.get("order") is None:
            self.add("order", "missing: a running balance is followed in the order the rows happened",
                     f"write the file's row order, one of {', '.join(ORDERS)}")
        return Preset(id=preset_id, label=label, kind=kind, source=source, evidence=evidence, institution=institution,
                      download=download, encodings=encodings,
                      header=self.header, cells=self.cells, constants=self.constants, search_rows=search_rows,
                      order=order, date_column=date_column, date_formats=date_formats, **amount,
                      description_columns=description_columns, join=join, pending=pending, empty_amount=empty_amount,
                      trailing=trailing, **extras)

    def encodings(self, value) -> tuple[str, ...]:
        if value is None:
            return DEFAULT_ENCODINGS
        if not isinstance(value, list) or not value:
            self.add("encoding", "not a list", "write a list of encodings tried in order, e.g. [utf-8-sig, cp1252]")
            return ()
        out = []
        for n, name in enumerate(value):
            try:
                ok = isinstance(name, str) and getattr(codecs.lookup(name), "_is_text_encoding", True)
            except LookupError:
                ok = False
            if not ok:
                self.add(f"encoding[{n}]", f"{name!r} is not a text encoding Python knows",
                         "write an encoding name such as utf-8-sig, cp1252 or utf-16")
            else:
                out.append(name)
        return tuple(out)

    def detect(self, value) -> None:
        self.constants: tuple[tuple[int, str], ...] = ()
        detect = self.mapping(value, "detect")
        if detect is None:
            return
        self.block_keys(detect, "detect")
        if ("header" in detect) == ("headerless" in detect):
            self.add("detect", "states neither header nor headerless" if "header" not in detect else
                     "states both header and headerless", "state one: header: [its column names], or headerless: "
                                                          "{columns: N}")
            return
        if "header" in detect:
            names = detect["header"]
            if not isinstance(names, list) or not names or not all(isinstance(n, str) and n.strip() for n in names):
                self.add("detect.header", "not a list of column names", "list the header's column names this format "
                                                                        "reads")
                return
            if len({_fold(n) for n in names}) != len(names):
                self.add("detect.header", "names a column twice", "list each column once")
                return
            self.header = tuple(n.strip() for n in names)
            self.detect_ok = True
            return
        shape = self.mapping(detect["headerless"], "detect.headerless")
        if shape is None:
            return
        self.block_keys(shape, "detect.headerless")
        cells = shape.get("columns")
        if isinstance(cells, bool) or not isinstance(cells, int) or cells < 1:
            self.add("detect.headerless.columns", "not a number of cells", "write how many cells each row has")
            return
        self.cells = cells
        self.detect_ok = True
        constant = self.mapping(shape.get("constant"), "detect.headerless.constant", required=False) or {}
        out = []
        for pos, text in constant.items():
            if isinstance(pos, bool) or not isinstance(pos, int) or not 1 <= pos <= cells:
                self.add(f"detect.headerless.constant.{pos}", "not a column position",
                         f"key each constant cell by its position from 1 to {cells}")
            elif not isinstance(text, str) or not text.strip():
                self.add(f"detect.headerless.constant.{pos}", "not text", "write the text the cell always holds")
            else:
                out.append((pos, text.strip()))
        self.constants = tuple(sorted(out))

    def search_rows(self, value) -> int:
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SEARCH_ROWS:
            self.add("search_rows", "not a number of lines", f"write a whole number from 0 to {MAX_SEARCH_ROWS}")
            return 0
        if value and self.cells is not None:
            self.add("search_rows", "applies only to a preset with a header line", "leave it out")
        return value

    def date(self, value) -> tuple[Column | None, tuple[str, ...]]:
        date = self.mapping(value, "date")
        if date is None:
            return None, ()
        self.block_keys(date, "date")
        column = self.column(date.get("column"), "date.column")
        formats = date.get("format")
        formats = [formats] if isinstance(formats, str) else formats
        if not isinstance(formats, list) or not formats:
            self.add("date.format", "missing", "write a strptime pattern such as %m/%d/%Y, or a list of them")
            return column, ()
        for n, fmt in enumerate(formats):
            path = "date.format" if isinstance(date.get("format"), str) else f"date.format[{n}]"
            try:
                whole = isinstance(fmt, str) and dt.datetime.strptime(_PROBE.strftime(fmt), fmt).date() == _PROBE.date()
            except ValueError:
                whole = False
            if not whole:
                self.add(path, f"{fmt!r} does not write and read a whole date",
                         "write a strptime pattern with the day, the month and the year, such as %m/%d/%Y")
        patterns = [f for f in formats if isinstance(f, str)]
        self.confusable(patterns)
        return column, tuple(patterns)

    def confusable(self, patterns: list[str]) -> None:
        """Two patterns of a list that read one date's text as another date (03/04/2030 as March 4 under %m/%d/%Y and
        as April 3 under %d/%m/%Y): the order of the list would decide which date a row gets, a guess"""
        reported: set[frozenset[str]] = set()
        for a in patterns:
            try:
                text = _AMBIGUOUS_PROBE.strftime(a)
            except ValueError:
                continue
            for b in patterns:
                if b == a or frozenset((a, b)) in reported:
                    continue
                try:
                    read = dt.datetime.strptime(text, b)
                except ValueError:
                    continue
                if read.date() != _AMBIGUOUS_PROBE.date():
                    reported.add(frozenset((a, b)))
                    self.add("date.format", f"{a!r} and {b!r} read the same text as two different dates ({text} is "
                                            f"{_AMBIGUOUS_PROBE.date().isoformat()} under the first and "
                                            f"{read.date().isoformat()} under the second)",
                             "keep only the pattern this export writes")

    def amount(self, value) -> dict:
        out = {"amount_column": None, "sign": None, "debit": None, "credit": None}
        amount = self.mapping(value, "amount")
        if amount is None:
            return out
        self.block_keys(amount, "amount")
        one, two = "column" in amount or "sign" in amount, "debit" in amount or "credit" in amount
        if one == two:
            self.add("amount", "states both a signed column and debit and credit columns" if one else
                     "states no amount column", "state one: column with sign, or debit with credit")
            return out
        if one:
            out["amount_column"] = self.column(amount.get("column"), "amount.column")
            out["sign"] = self.choice(amount.get("sign"), "amount.sign", SIGNS)
            return out
        out["debit"] = self.column(amount.get("debit"), "amount.debit")
        out["credit"] = self.column(amount.get("credit"), "amount.credit")
        if out["debit"] is not None and out["debit"] == out["credit"]:
            self.add("amount.credit", "the same column as the debit", "name the column that holds the credits")
        return out

    def description(self, value) -> tuple[tuple[Column, ...], str]:
        description = self.mapping(value, "description")
        if description is None:
            return (), " "
        self.block_keys(description, "description")
        columns = description.get("columns")
        if not isinstance(columns, list) or not columns:
            self.add("description.columns", "not a list of columns", "list the columns the description is read from")
            columns = []
        out = tuple(self.column(c, f"description.columns[{n}]") for n, c in enumerate(columns))
        join = description.get("join", " ")
        if not isinstance(join, str):
            self.add("description.join", "not text", "write the text put between the columns, e.g. ' - '")
            join = " "
        return out, join

    def skip(self, value) -> tuple[tuple[Column, str] | None, bool, re.Pattern | None]:
        skip = self.mapping(value, "skip", required=False)
        if skip is None:
            return None, False, None
        self.block_keys(skip, "skip")
        pending = None
        if skip.get("pending") is not None:
            rule = self.mapping(skip["pending"], "skip.pending")
            if rule is not None:
                self.block_keys(rule, "skip.pending")
                column = self.column(rule.get("column"), "skip.pending.column")
                equals = self.text(rule.get("equals"), "skip.pending.equals", "write the text that marks a row "
                                                                              "pending, e.g. Pending")
                pending = (column, equals)
        empty_amount = skip.get("empty_amount", False)
        if not isinstance(empty_amount, bool):
            self.add("skip.empty_amount", "not true or false", "write true to skip a row with no amount, else false")
            empty_amount = False
        trailing = None
        if skip.get("trailing_rows_matching") is not None:
            pattern = skip["trailing_rows_matching"]
            try:
                trailing = re.compile(pattern) if isinstance(pattern, str) else None
            except re.error as e:
                self.add("skip.trailing_rows_matching", f"not a regular expression ({e})",
                         "write a regular expression that matches the totals line")
            else:
                if trailing is None:
                    self.add("skip.trailing_rows_matching", "not text", "write a regular expression")
        return pending, empty_amount, trailing

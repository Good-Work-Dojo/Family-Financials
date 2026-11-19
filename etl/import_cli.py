# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""`python -m etl import ...` — what a statement file is, what a format reads from it, how to describe a layout no
format of the release reads, and the banks the release reads. Offline: nothing here imports a networked module.

  import detect FILE
      Tries every format on the file — the release's (the built-in formats, the shipped presets, OFX / QFX) and the
      household's own presets (config/presets/, when a config folder is found; one with a problem is left out, saying
      so) — whatever the file's name or extension says: a format reads the file when its parse returns a row. Prints
      each format that reads it with its kind, its rows and their dates; when none does, the three nearest misses, each
      with its plain reason, and the next step. The nearest are the formats that found the file's layout and read no row
      of it, then the presets whose header names one of the file's first lines holds most of (a headerless preset: the
      first line has its number of cells), then the rest, each group by name. Exit 0 when exactly one format reads it,
      1 when none or several do. Needs no household.
  import preview FILE --format NAME [--account ID]
      Reads the file with that one format and prints the rows read, the lines skipped by reason, the dates, the first
      five and last two rows (date, amount, description: this is the household's own terminal) and a balance the file
      states, never the file's sum. With an account (the household's config must load) it also counts the file's rows
      the stored data already holds for that account, by the uid the pipeline gives each (its occurrence index counted
      in the file). Writes nothing. Exit 0 when the format reads the file, 1 when it refuses it (its reason printed).
  import map FILE --id NAME [--answers FILE] [--force]
      Asks about the file's columns on a terminal, or takes the answers from a YAML file (ANSWERS lists its keys):
      whether the first line is a header; the date column, offering every common pattern (DATE_FORMATS) that reads every
      date in it and asking whenever more than one does; one signed amount column and whether a positive amount is
      money coming in (never inferred), or a debit and a credit column; the description columns; a running-balance
      column, if any, and whether the newest row is first; bank or card. It builds the preset through the loader every
      preset passes (csv_preset.build_preset), reads the file with it, and only then writes <config dir>/presets/NAME.yaml
      (never over an existing one without --force) and prints the accounts.yaml lines to add. Exit 0 when written, 1
      when the file cannot be described or the layout described cannot read it (nothing written).
  import banks [--write]
      Prints the page of the banks this release reads a download of (docs/importing-banks.md): what a preset is, the
      OFX / QFX download and `map` for a bank the table lacks, then one row per format, sorted by institution — the
      built-in formats' rows from NATIVE_BANKS, each shipped preset's from its own file (institution, kind, download,
      evidence, source). --write writes the page into the tree instead; a test fails while the committed page differs.

A usage error (an unknown format or account, a file that is not there, a name that is taken, an answer missing or
unusable) raises ValueError and a config with problems ConfigError: `python -m etl` prints them and exits 2. A file the
ingest refuses before opening it (not a plain file, over the size cap: registry.file_refusal) is refused here too. The
words printed name the file only as it was typed, and never an OFX file's account or bank number, which the OFX reader
never keeps. A description or a cell is shown with its control characters written as escapes."""
from __future__ import annotations

import csv
import datetime as dt
import glob
import io
import json
import re
import shlex
import sqlite3
import sys
import unicodedata
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import Config, HouseholdPresets, Paths, household_presets
from .importers import csv_preset as engine
from .importers import ofx, registry
from .importers.csv_preset import Preset, build_preset
from .models import FileStats, Txn
from .money import fmt as dollars
from .pipeline import TXN_PARSERS, assign_occurrence_seq, format_parse
from .validate import PRESETS_DIR, ConfigError, missing_config_dir_problem

# the account a file is read for when no account is named: it seeds the rows' uids only, and is never printed
PROBE_ACCOUNT = "import_probe"
NEAREST = 3
HEAD_ROWS, TAIL_ROWS = 5, 2
DESCRIPTION_CHARS, CELL_CHARS = 80, 40
# the lines `map` learns a layout from: the first line and up to this many after it
SAMPLE_ROWS = 500

# the reason of a format that read the file and returned no row, no line of it unreadable (an empty file)
NO_ROW = "it holds no row this format reads"
# the refusals of a file whose layout the format found, none of whose rows it could read: the nearest misses
LAYOUT_FOUND = frozenset({registry.NO_ROWS, NO_ROW, engine.COLUMNS_DO_NOT_FIT, engine.UNCLOSED_QUOTE,
                          ofx.NONE_READABLE, ofx.ENDS_EARLY, ofx.SEVERAL_ACCOUNTS, ofx.SEVERAL_PERIODS,
                          ofx.SEVERAL_STATEMENTS, ofx.INVESTMENT, ofx.NO_STATEMENT, ofx.TOO_DEEP})

# the date patterns `map` offers, each tried on every date of the column chosen. Each numeric pattern that starts with
# the month or the day comes with its twin, the two the other way round, so dates whose days are all 12 or less fit
# both and are asked about, never read one way silently. A date that starts with the year is the year, the month and
# the day by universal convention, so a year-first pattern has no twin
DATE_FORMATS = ("%m/%d/%Y", "%d/%m/%Y", "%m/%d/%y", "%d/%m/%y", "%m-%d-%Y", "%d-%m-%Y", "%m.%d.%Y", "%d.%m.%Y",
                "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d-%b-%Y")
# what `map` says when a person stops it before the last question
STOPPED = "stopped before every question was answered: nothing was written"

# the keys of `map`'s answers file -> what each says, for the line that asks for a missing one
ANSWERS = {
    "header": "whether the first line is a header that names the columns",
    "date": "which column holds the date",
    "date_format": "which date pattern the export writes",
    "amount": "which column holds the signed amount",
    "debit": "which column holds the debits",
    "credit": "which column holds the credits",
    "positive_is_money_in": "whether a positive amount is money coming in",
    "skip_empty_amount": "whether a row with no amount is skipped as having none",
    "description": "which columns make the description",
    "balance": "which column holds the running balance",
    "newest_first": "whether the newest row is first",
    "kind": "whether the export is a bank account's or a card's",
    "label": "what you call the export",
}
_ID = re.compile(r"[a-z][a-z0-9_]*\Z")
_HEADER = """\
# {id}: a statement layout of this household's own, described with `python -m etl import map` from one of its files.
# An account reads files of this layout with `parser: {id}` (or a source's `format: {id}`) in accounts.yaml;
# docs/configuration.md, "Your own file layouts", lists every key.
"""


def _rows(n: int) -> str:
    return f"{n} row" if n == 1 else f"{n} rows"


def _shown(text: str, limit: int = DESCRIPTION_CHARS) -> str:
    """Text from a file as a terminal shows it safely: each control or format character (an escape sequence's start, a
    bell, a right-to-left override) written as its escape, and at most `limit` characters"""
    out = "".join(ch if unicodedata.category(ch) not in ("Cc", "Cf")
                  else f"\\x{ord(ch):02x}" if ord(ch) < 0x100 else f"\\u{ord(ch):04x}" for ch in text)
    return out if len(out) <= limit else out[:limit - 1] + "…"


class Stopped(Exception):
    """A person stopped `map` at a prompt (Ctrl-C): nothing was written. `python -m etl` prints its words and exits
    130, as a shell does for an interrupted command."""


def plain_error(error: OSError) -> str:
    """An OSError in plain words: the path as it was given and what the system says of it"""
    what = error.strerror or type(error).__name__
    return f"{error.filename}: {what}" if error.filename else what


# ------------------------------------------------------------------ what every command shares

def own_presets(paths: Paths | None = None) -> HouseholdPresets:
    """The household's own presets, found as `rules starter-diff` finds its files (Paths), without loading the config:
    none when there is no config folder."""
    paths = paths or Paths()
    if not paths.config_dir.is_dir():
        return HouseholdPresets({}, (), ())
    return household_presets(paths.presets_dir(), tuple(TXN_PARSERS))


def _statement(file: str) -> tuple[Path, str | None]:
    """The file as typed, and why it is refused before any format opens it (registry.file_refusal), or None. A path
    that cannot be looked at, or a plain file that cannot be opened, is a usage error, the same in every command: no
    format is tried on it, so no command says half of its answer first."""
    path = Path(file)
    try:
        refused = registry.file_refusal(path)
        if refused is None:
            with open(path, "rb"):          # a plain file only: opening a pipe would hold the command
                pass
        return path, refused
    except FileNotFoundError:
        raise ValueError(f"{file}: no such file") from None
    except OSError as e:
        raise ValueError(f"{file}: {e.strerror or 'it cannot be opened'}") from None


def _read(parse: registry.Parse, path: Path, account_id: str) -> tuple[list[Txn], FileStats, dict] | str:
    """What a parse reads from the file, or the plain reason it is refused: the parse's refusal, or, as in the ingest,
    NO_ROWS for a file with a line the format could not read and no row"""
    try:
        txns, stats, extra = parse(path, account_id)
    except Exception as e:                  # whatever a file provokes is its refusal, as in the ingest
        return registry.refusal(e)
    if not txns and stats.skip_reasons.get(registry.UNPARSEABLE_ROW):
        return registry.NO_ROWS
    return txns, stats, extra


def _span(txns: list[Txn]) -> str:
    dates = [t.date for t in txns]
    return f"{min(dates)} to {max(dates)}"


def _summary(name: str, txns: list[Txn], stats: FileStats, extra: dict, out) -> None:
    """The rows read, the lines skipped by reason, the dates, the first and last rows and a balance the file states"""
    out(f"Read as {name}: {_rows(len(txns))}, {_span(txns)}." if txns else f"Read as {name}: no rows.")
    skipped = stats.skip_reasons
    out(f"Skipped: {sum(skipped.values())} ({', '.join(f'{r} {n}' for r, n in sorted(skipped.items()))})."
        if skipped else "Skipped: none.")
    if len(txns) <= HEAD_ROWS + TAIL_ROWS:
        groups = [("Rows:", txns)] if txns else []
    else:
        groups = [(f"First {HEAD_ROWS} rows:", txns[:HEAD_ROWS]), (f"Last {TAIL_ROWS} rows:", txns[-TAIL_ROWS:])]
    width = max((len(dollars(t.amount_cents)) for _, rows in groups for t in rows), default=0)
    for title, rows in groups:
        out(title)
        for t in rows:
            out(f"  {t.date}  {dollars(t.amount_cents):>{width}}  {_shown(t.description_raw)}")
    balance = extra.get("latest_balance")
    if balance:
        out(f"Balance stated: {dollars(balance[1])} on {balance[0]}.")


# ------------------------------------------------------------------ detect

@dataclass(frozen=True)
class _Try:
    name: str
    kind: str
    txns: list[Txn]
    reason: str = ""


def _kind(name: str, own: Mapping[str, Preset]) -> str:
    if name in own:
        return f"your own preset, {own[name].kind}"
    shipped = registry.presets().get(name)
    if shipped is not None:
        return f"preset, {shipped.kind}"
    return "OFX / QFX" if name == ofx.FORMAT else "built-in"


def _first_lines(path: Path) -> list[list[str]]:
    """The file's first non-blank lines as CSV cells, as many as a preset may look at for its header: read in the
    encodings a preset tries by default, none when its start is no text in them or it cannot be read"""
    try:
        head, _ = registry.file_head(path)
    except OSError:
        return []
    # read as a head whatever the file's length: a broken byte at its very end never hides its first lines
    _, text = registry.sniff(head, False, registry.DEFAULT_ENCODINGS)
    if text is None:
        return []
    lines: list[list[str]] = []
    try:
        for row in csv.reader(io.StringIO(text)):
            if any(cell.strip() for cell in row):
                lines.append(row)
                if len(lines) > engine.MAX_SEARCH_ROWS:
                    break
    except csv.Error:
        pass
    return lines


def _share(preset: Preset, lines: list[list[str]]) -> float:
    """How much of a preset's layout the file's first lines show: the largest share of its header's names that one of
    the lines that may be its header holds; for a headerless preset, a half when the first line has its number of
    cells"""
    if preset.header is None:
        return 0.5 if lines and len(lines[0]) == preset.cells else 0.0
    wanted = {name.strip().casefold() for name in preset.header}
    return max((len(wanted & {cell.strip().casefold() for cell in row}) / len(wanted)
                for row in lines[:preset.search_rows + 1]), default=0.0)


def _distance(t: _Try, own: Mapping[str, Preset], lines: list[list[str]]) -> tuple:
    if t.reason in LAYOUT_FOUND:
        return 0, 0.0, t.name
    preset = own.get(t.name) or registry.presets().get(t.name)
    share = _share(preset, lines) if preset is not None else 0.0
    return (1 if share else 2), -share, t.name


def tries(path: Path, own: Mapping[str, Preset]) -> list[_Try]:
    """Every format of the release and every one of `own` (the household's presets) tried on the file, in name order:
    each with the rows it reads, or none and its reason. `detect` prints them; `init` proposes accounts from them."""
    out = []
    for name in sorted({*TXN_PARSERS, *own}):
        read = _read(format_parse(name, own), path, PROBE_ACCOUNT)
        if isinstance(read, str):
            out.append(_Try(name, _kind(name, own), [], read))
        else:
            out.append(_Try(name, _kind(name, own), read[0], "" if read[0] else NO_ROW))
    return out


def statement(file: str) -> tuple[Path, str | None]:
    """The file as typed and why it is refused before any format opens it, or None (a usage error raises
    ValueError): what every `import` command checks first."""
    return _statement(file)


def detect(file: str, own: Mapping[str, Preset], *, out=print) -> int:
    """`import detect`: every format of the release and every one of `own` (the household's presets) tried on the
    file. Exit 0 when exactly one reads it."""
    path, refused = _statement(file)
    if refused:
        out(f"No format reads this file: {refused}.")
        return 1
    tried = tries(path, own)
    readers = [t for t in tried if t.txns]
    if readers:
        n = len(readers)
        out(f"{n} format{'s' if n != 1 else ''} read{'s' if n == 1 else ''} this file:")
        for t in readers:
            out(f"  {t.name} ({t.kind}): {_rows(len(t.txns))}, {_span(t.txns)}")
        if n > 1:
            out("The account's format decides which one reads it: name one as its parser in accounts.yaml (or as a "
                "source's format).")
        return 0 if n == 1 else 1
    out("No format reads this file. The nearest:")
    lines = _first_lines(path)
    for t in sorted(tried, key=lambda t: _distance(t, own, lines))[:NEAREST]:
        out(f"  {t.name} ({t.kind}): {t.reason}")
    out(f"To describe its layout, try: python -m etl import map {shlex.quote(file)} --id NAME (a name you choose for "
        "it, such as my_bank)")
    return 1


def detect_command(file: str) -> int:
    """`python -m etl import detect FILE`: the household's presets that load are tried, and each problem that leaves
    one out is said first, on stderr"""
    found = own_presets()
    if found.problems:
        print("Not tried, for these problems in your own presets:", file=sys.stderr)
        for problem in found.problems:
            print(problem, file=sys.stderr)
    return detect(file, found.presets)


# ------------------------------------------------------------------ preview

def _parse_of(name: str, own: Mapping[str, Preset], broken: Collection[str]) -> registry.Parse:
    if name in own or name in TXN_PARSERS:
        return format_parse(name, own)
    if name in broken:
        raise ValueError(f"your preset {name} has problems, so it reads nothing yet: python -m etl validate lists them")
    raise ValueError(f"unknown format {name!r} — one of {', '.join(sorted({*TXN_PARSERS, *own}))}")


def _stored(cfg: Config, account: str, txns: list[Txn]) -> str:
    """How many of the file's rows the stored data holds for the account, by the uid the pipeline gives each; the
    database is opened read-only"""
    db_path = cfg.data_dir / "finance.db"
    if not db_path.is_file():
        return "No stored data to compare with yet: this household has not been ingested."
    assign_occurrence_seq(txns)
    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            stored = {uid for (uid,) in conn.execute("SELECT txn_uid FROM transactions WHERE account_id = ?",
                                                     (account,))}
        finally:
            conn.close()
    except sqlite3.Error:
        return "The stored data could not be read, so the file's rows are not compared with it."
    held = sum(t.uid in stored for t in txns)
    return f"Already stored for {account}: {held} of the file's {_rows(len(txns))}."


def preview(file: str, name: str, own: Mapping[str, Preset], *, broken: Collection[str] = (),
            account: str | None = None, cfg: Config | None = None, out=print) -> int:
    """`import preview`: the file read with the format `name` (one of the release's, or of `own`; `broken` are the
    household's presets that do not load). With `account`, `cfg` is the household's config."""
    parse = _parse_of(name, own, broken)
    if account is not None and (cfg is None or account not in cfg.accounts):
        known = ", ".join(cfg.accounts) if cfg is not None else "none"
        raise ValueError(f"unknown account {account!r} — one of {known}")
    path, refused = _statement(file)
    read = refused or _read(parse, path, account or PROBE_ACCOUNT)
    if isinstance(read, str):
        out(f"Not read as {name}: {read}.")
        return 1
    txns, stats, extra = read
    _summary(name, txns, stats, extra, out)
    if account is not None:
        out(_stored(cfg, account, txns))
    return 0


def preview_command(file: str, name: str, account: str | None) -> int:
    """`python -m etl import preview`: without an account the household's presets are found without its config; with
    one the config is loaded (a config with problems is refused in the validator's words)."""
    if account is None:
        found = own_presets()
        return preview(file, name, found.presets, broken=set(found.names) - set(found.presets))
    cfg = Config()
    return preview(file, name, cfg.presets, account=account, cfg=cfg)


# ------------------------------------------------------------------ map

@dataclass(frozen=True)
class _Sheet:
    """What `map` learns a layout from: the encoding the preset must state (None: the engine's default list reads the
    file), the first non-blank line and the non-blank lines after it, each (line number, cells)"""
    encoding: str | None
    first: tuple[int, list[str]]
    body: list[tuple[int, list[str]]]


def _sheet(path: Path) -> _Sheet | str:
    """The file's first lines read as CSV in the encodings the engine tries (UTF-16 too when the file starts with its
    byte-order mark), or the plain reason they cannot be"""
    head, whole = registry.file_head(path)
    encodings = [*registry.DEFAULT_ENCODINGS, *(["utf-16"] if head[:2] in (b"\xff\xfe", b"\xfe\xff") else [])]
    usable, _ = registry.sniff(head, whole, encodings)
    if not usable:
        return engine.NOT_TEXT
    encoding = usable[0]
    lines: list[tuple[int, list[str]]] = []
    try:
        with open(path, newline="", encoding=encoding) as f:
            reader = csv.reader(f)
            last = 0
            for row in reader:
                start, last = last + 1, reader.line_num
                if any(cell.strip() for cell in row):
                    lines.append((start, row))
                    if len(lines) > SAMPLE_ROWS:
                        break
    except (UnicodeError, csv.Error):
        pass                                # the lines before it are the sample; the trial read judges the whole file
    if not lines:
        return "it holds no line to learn its layout from"
    return _Sheet(None if encoding in registry.DEFAULT_ENCODINGS else encoding, lines[0], lines[1:])


def _cell(row: list[str], at: int) -> str:
    return row[at] if at < len(row) else ""


def _yes_no(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().casefold() in ("y", "yes", "true"):
        return True
    if isinstance(value, str) and value.strip().casefold() in ("n", "no", "false"):
        return False
    raise ValueError("answer yes or no")


def _kind_answer(value) -> str:
    if isinstance(value, str) and value.strip().casefold() in engine.KINDS:
        return value.strip().casefold()
    raise ValueError("answer bank or card")


def _two_columns(value) -> bool:
    """The answer to "one signed column (1), or a debit and a credit column (2)?": True for the two"""
    if value in ("1", "2"):
        return value == "2"
    raise ValueError("answer 1 or 2")


def _label(value) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError("write what you call the export")


def _column_reader(cells: list[str], header: bool) -> Callable[[object], int]:
    """Reads a column given by its number from 1 or, with a header, by its name; returns its index from 0. With a
    header the column must have a name no other column has: the preset finds its columns by name."""
    names = [cell.strip() for cell in cells]
    folded = [name.casefold() for name in names]
    hint = f"write a number from 1 to {len(names)}" + (", or a name from the header line" if header else "")

    def read(value) -> int:
        if (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, str)
                                                                         and value.strip().isdigit()):
            n = int(value)
            if not 1 <= n <= len(names):
                raise ValueError(f"{n} is not a column: {hint}")
            at = n - 1
        elif header and isinstance(value, str) and value.strip().casefold() in folded:
            at = folded.index(value.strip().casefold())
        else:
            raise ValueError(f"{value!r} is not a column: {hint}")
        if header and not names[at]:
            raise ValueError(f"column {at + 1} has no name in the header line, and this layout finds its columns by "
                             "name")
        if header and folded.count(folded[at]) > 1:
            raise ValueError(f"column {at + 1}'s name {names[at]!r} names another column too, so this layout could not "
                             "tell them apart")
        return at
    return read


def _columns_reader(column: Callable[[object], int]) -> Callable[[object], list[int]]:
    def read(value) -> list[int]:
        if isinstance(value, list):
            items = value
        elif isinstance(value, str):
            items = [v for v in value.split(",") if v.strip()]       # at a terminal: numbers separated by commas
        else:
            items = [value]
        if not items:
            raise ValueError("name at least one column")
        out = [column(v) for v in items]
        if len(set(out)) != len(out):
            raise ValueError("a column is named twice")
        return out
    return read


def _date(text: str, pattern: str) -> str | None:
    try:
        return dt.datetime.strptime(text, pattern).date().isoformat()
    except ValueError:
        return None


class _Asker:
    """One answer at a time, from the answers file (`given`) or from a person at a terminal (`ask`), each read by the
    same function: a missing or unreadable answer in the file is a usage error, at a terminal it is asked again."""

    def __init__(self, given: Mapping | None, ask: Callable[[str], str], out):
        self.given, self.ask, self.out = given, ask, out

    def has(self, key: str) -> bool:
        return self.given is not None and self.given.get(key) is not None

    def get(self, key: str, question: str, read: Callable, *, optional: bool = False):
        if self.given is not None:
            if self.given.get(key) is None:
                if optional:
                    return None
                raise ValueError(f"the answers do not say {ANSWERS[key]}: add {key}: to them")
            try:
                return read(self.given[key])
            except ValueError as e:
                raise ValueError(f"{key}: {e}") from None
        return self.terminal(question, read, optional=optional)

    def terminal(self, question: str, read: Callable, *, optional: bool = False):
        while True:
            try:
                reply = self.ask(f"{question} ").strip()
            except EOFError:
                raise ValueError(STOPPED) from None
            except KeyboardInterrupt:
                raise Stopped(STOPPED) from None
            if not reply and optional:
                return None
            try:
                return read(reply)
            except ValueError as e:
                self.out(f"  {e}")


def _dates(body: list[tuple[int, list[str]]], at: int) -> list[tuple[int, str]]:
    """The date cells of the column at `at` that hold something, each (line number, its text): a blank one (a totals
    line) is left to the trial read, which skips and counts it"""
    return [(line, text) for line, row in body if (text := _cell(row, at).strip())]


def _date_column(column: Callable[[object], int], body: list[tuple[int, list[str]]]) -> Callable[[object], int]:
    """Reads the date's column as `column` does, and refuses one that is blank in every row"""
    def read(value) -> int:
        at = column(value)
        if not _dates(body, at):
            raise ValueError(f"column {at + 1} is blank in every row, so it holds no date")
        return at
    return read


def _date_format(asker: _Asker, body: list[tuple[int, list[str]]], at: int, out) -> str:
    """The date pattern of the column at `at`: the one common pattern that reads every date in it, else the answer
    (a common one when several do, never chosen silently, or a pattern of the person's own when none does)"""
    cells = _dates(body, at)
    column = f"column {at + 1}"

    def stated(value) -> str:
        if not isinstance(value, str) or "%" not in value:
            raise ValueError("not a date pattern: write a strptime pattern such as %m/%d/%Y")
        for line, text in cells:
            if _date(text, value) is None:
                raise ValueError(f"{value!r} does not read the date of line {line} ({text!r})")
        return value

    if asker.has("date_format"):
        return asker.get("date_format", "", stated)
    fits = [p for p in DATE_FORMATS if all(_date(text, p) is not None for _, text in cells)]
    if len(fits) == 1:
        out(f"Dates read as {fits[0]}: {cells[0][1]} is {_date(cells[0][1], fits[0])}.")
        return fits[0]
    if not fits:
        unread = next(((line, text) for line, text in cells if not any(_date(text, p) for p in DATE_FORMATS)), None)
        where = f" (line {unread[0]}: {_shown(unread[1], CELL_CHARS)!r} fits none)" if unread else ""
        if asker.given is not None:
            raise ValueError(f"no common date pattern reads every date in {column}{where}: name the column that holds "
                             "the date, or state the pattern the export writes with date_format: in the answers")
        out(f"No common date pattern reads every date in {column}{where}.")
        return asker.terminal("Type the pattern this export writes, such as %d %B %Y:", stated)
    sample = next((text for _, text in cells if len({_date(text, p) for p in fits}) > 1), cells[0][1])
    readings = [f"{p}: {sample} is {_date(sample, p)}" for p in fits]
    if asker.given is not None:
        raise ValueError(f"more than one date pattern reads every date in {column} ({'; '.join(readings)}): say which "
                         "one the export writes with date_format: in the answers")
    out(f"{len(fits)} date patterns read every date in {column}:")
    for n, reading in enumerate(readings, 1):
        out(f"  {n}  {reading}")

    def chosen(value: str) -> str:
        if value.isdigit() and 1 <= int(value) <= len(fits):
            return fits[int(value) - 1]
        return stated(value)
    return asker.terminal("Which one does this export write? (its number, or a pattern of your own)", chosen)


def _money(text: str) -> int | None:
    try:
        return engine.money_cents(text)
    except ValueError:
        return None


def glob_for(name: str) -> str:
    """The file-name pattern suggested for files named like `name`: each run of digits (a date, a number) stands for any
    text, runs joined only by separators make one, and a name with no digit takes any text before its extension"""
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        stem, suffix = name, ""
    pattern = "*".join(glob.escape(part) for part in re.split(r"\d+", stem))
    pattern = re.sub(r"\*(?:[-_. ]*\*)+", "*", pattern)
    if "*" not in pattern:
        pattern += "*"
    return pattern + (f".{glob.escape(suffix)}" if suffix else "")


def map_layout(file: str, preset_id: str, config_dir: Path, *, answers: Mapping | None, force: bool = False,
               out=print, ask: Callable[[str], str] | None = None) -> int:
    """`import map`: asks (`ask`, default `input`, at a terminal) or reads `answers` (a mapping of ANSWERS' keys), then
    writes `config_dir`/presets/<preset_id>.yaml when the preset it built reads a row of the file."""
    if not isinstance(preset_id, str) or not _ID.match(preset_id):
        raise ValueError(f"{preset_id!r} is not a format name: write lowercase letters, digits and _, starting with a "
                         "letter")
    if preset_id in TXN_PARSERS:
        raise ValueError(f"{preset_id!r} is the name of a format this release reads: choose another name")
    shown_target = f"{PRESETS_DIR}/{preset_id}.yaml"
    target = config_dir / PRESETS_DIR / f"{preset_id}.yaml"
    if target.exists() and not force:
        raise ValueError(f"{shown_target} already exists in your config folder: choose another name, or pass --force "
                         "to replace it")
    if answers is not None:
        unknown = [str(k) for k in answers if k not in ANSWERS]
        if unknown:
            raise ValueError(f"the answers hold {', '.join(unknown)}, which map does not ask: use "
                             f"{', '.join(ANSWERS)}")
    path, refused = _statement(file)
    sheet = refused or _sheet(path)
    if isinstance(sheet, str):
        out(f"Not read: {sheet}.")
        return 1
    asker = _Asker(answers, ask or input, out)
    first = sheet.first[1]
    out("The first line's cells:")
    for n, cell in enumerate(first, 1):
        out(f"  {n}  {_shown(cell, CELL_CHARS)}")
    header = asker.get("header", "Is the first line a header that names the columns? [y/n]", _yes_no)
    body = sheet.body if header else [sheet.first, *sheet.body]
    if not body:
        out("Not read: it has no line below its header to learn its layout from.")
        return 1
    if header:
        out("Its columns, with the next line's cells beside them:")
        for n, name in enumerate(first, 1):
            out(f"  {n}  {_shown(name, CELL_CHARS)}  ({_shown(_cell(body[0][1], n - 1), CELL_CHARS)})")
    else:
        out("No header line: its columns are counted from 1, as above.")
    column = _column_reader(first, header)
    date_at = asker.get("date", "Which column holds the date? (its number)", _date_column(column, body))
    date_format = _date_format(asker, body, date_at, out)

    if answers is not None:
        signed, paired = asker.has("amount"), asker.has("debit") or asker.has("credit")
        if signed and paired:
            raise ValueError("the answers state amount: and debit: or credit:: keep amount: for one signed column, or "
                             "debit: and credit: for two")
        if not signed and not paired:
            raise ValueError("the answers do not say which column holds the amount: add amount: (one signed column) or "
                             "debit: and credit: to them")
    else:
        paired = asker.terminal("Is the amount one column with a sign (1), or a debit column and a credit column (2)?",
                                _two_columns)
    if not paired:
        amount_cols = [asker.get("amount", "Which column holds the amount? (its number)", column)]
        # a row that spends and a row that brings money in, when the sample has both, so the sign question has an answer
        # to see; the sign itself is never inferred from them
        signs = [(line, row, _money(_cell(row, amount_cols[0]))) for line, row in body]
        spent = next((r for r in signs if r[2] is not None and r[2] < 0), None)
        came = next((r for r in signs if r[2] is not None and r[2] > 0), None)
        shown = sorted([spent, came]) if spent and came else signs[:2]
        out("Two of its rows:" if len(shown) == 2 else "One of its rows:")
        for line, row, _ in shown:
            out(f"  line {line}: {' | '.join(_shown(cell) for cell in row)}")
        money_in = asker.get("positive_is_money_in", "In these rows, is a positive amount money coming in (a deposit, "
                                                     "a refund, a payment to a card)? [y/n]", _yes_no)
        amount = {"column": amount_cols[0], "sign": "as_is" if money_in else "invert"}
    else:
        debit_at = asker.get("debit", "Which column holds the debits (money out)? (its number)", column)

        def credit_column(value) -> int:
            at = column(value)
            if at == debit_at:
                raise ValueError("the same column as the debits: name the column that holds the credits")
            return at
        credit_at = asker.get("credit", "Which column holds the credits (money in)? (its number)", credit_column)
        amount_cols = [debit_at, credit_at]
        amount = {"debit": debit_at, "credit": credit_at}
    empties = sum(all(not _cell(row, at).strip() for at in amount_cols) for _, row in body)
    skip_empty = False
    if empties or asker.has("skip_empty_amount"):
        if empties:
            out(f"{empties} of its rows {'has' if empties == 1 else 'have'} no amount.")
        skip_empty = asker.get("skip_empty_amount", "Skip such rows as having none? (no: each is counted as a row this "
                                                    "layout cannot read) [y/n]", _yes_no)
    description = asker.get("description", "Which columns make the description? (their numbers, separated by commas)",
                            _columns_reader(column))
    balance_at = asker.get("balance", "Which column holds the running balance, if any? (its number, or Enter for none)",
                           column, optional=True)
    order = None
    if balance_at is not None:
        dates = _dates(body, date_at)
        newest = asker.get("newest_first", f"Is the newest row first? (the first row is dated "
                                           f"{_shown(dates[0][1], CELL_CHARS)}, the last "
                                           f"{_shown(dates[-1][1], CELL_CHARS)}) [y/n]", _yes_no)
        order = "newest_first" if newest else "oldest_first"
    kind = asker.get("kind", "Is this a bank account's export or a card's? [bank/card]", _kind_answer)
    # a person at a terminal is not asked for a label: the id stands for one until the file is edited
    label = asker.get("label", "", _label) if asker.has("label") else preset_id

    def ref(at: int) -> str | int:
        return first[at].strip() if header else at + 1

    used = sorted({date_at, *amount_cols, *description, *([balance_at] if balance_at is not None else [])})
    doc: dict = {"id": preset_id, "label": label, "kind": kind}
    if sheet.encoding:
        doc["encoding"] = [sheet.encoding]
    doc["detect"] = {"header": [ref(at) for at in used]} if header else {"headerless": {"columns": len(first)}}
    if order:
        doc["order"] = order
    doc["date"] = {"column": ref(date_at), "format": date_format}
    doc["amount"] = {k: (v if k == "sign" else ref(v)) for k, v in amount.items()}
    doc["description"] = {"columns": [ref(at) for at in description]}
    if skip_empty:
        doc["skip"] = {"empty_amount": True}
    if balance_at is not None:
        doc["balance"] = ref(balance_at)
    text = _HEADER.format(id=preset_id) + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    # what is written is what the loader checks: the text read back, through the one builder of every preset
    preset = build_preset(yaml.safe_load(text), shown_target, preset_id, household=True)
    read = _read(preset.parse, path, PROBE_ACCOUNT)
    if not isinstance(read, str) and not read[0]:
        read = NO_ROW                       # a layout that reads no row of the file it was described from is no layout
    if isinstance(read, str):
        out(f"Not read with this layout: {read}.")
        out("Nothing was written.")
        return 1
    _summary(preset_id, *read, out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, "utf-8")
    out(f"Wrote {shown_target} in your config folder.")
    out("To read an account's files with it, give the account these two lines in accounts.yaml (its files go in the "
        "statements folder):")
    out(f"    parser: {preset_id}")
    out(f"    glob: {json.dumps(glob_for(path.name))}")
    out("Then check the config: python -m etl validate")
    return 0


def load_answers(file: str) -> dict:
    """`map`'s answers file: a YAML mapping of ANSWERS' keys"""
    try:
        doc = yaml.safe_load(Path(file).read_text("utf-8"))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        raise ValueError(f"{file}: not valid YAML{where}") from None
    except UnicodeDecodeError:
        raise ValueError(f"{file}: not UTF-8 text") from None
    if not isinstance(doc, dict):
        raise ValueError(f"{file}: not a mapping of answers, one `key: value` line each ({', '.join(ANSWERS)})")
    return doc


# ------------------------------------------------------------------ banks

# The page that lists the banks this release reads a download of; `import banks --write` writes it
BANKS_DOC = Path(__file__).resolve().parents[1] / "docs" / "importing-banks.md"
BANKS_DOC_SHOWN = "docs/importing-banks.md"


@dataclass(frozen=True)
class Bank:
    """One row of the table of supported banks"""
    institution: str
    account: str
    format: str
    download: str
    evidence: str
    source: str


BUILT_IN = "built-in"
# The formats written as code, which no preset file describes, with their rows of the table
NATIVE_BANKS = (
    Bank("Bank of America", "bank account", "bofa_bank",
         "On the bank's website, open the account's activity and download its transactions as a CSV file.", BUILT_IN,
         "a reader of its own in this release, etl/importers/bofa_bank.py"),
    Bank("Chase", "bank account", "chase_checking",
         "On the bank's website, open the account's activity and download its transactions as a CSV file.", BUILT_IN,
         "a reader of its own in this release, etl/importers/chase_checking.py"),
    Bank("Chase", "credit card", "chase_card",
         "On the bank's website, open the card's activity and download its transactions as a CSV file.", BUILT_IN,
         "a reader of its own in this release, etl/importers/chase_card.py"),
    Bank("Any bank with an OFX or QFX download", "bank account or credit card", ofx.FORMAT,
         "Where the bank offers it, download the activity as an OFX or QFX (Quicken) file.", BUILT_IN,
         "the OFX specification, read in its SGML (1.x) and XML (2.x) forms by etl/importers/ofx.py"),
)
_ACCOUNT = {"bank": "bank account", "card": "credit card"}

_BANKS_INTRO = """\
# Banks this release reads

<!-- Generated by `python -m etl import banks --write` from the preset files in etl/importers/presets/ and the
built-in formats (etl/import_cli.py, NATIVE_BANKS). Edit those, not this page: a test fails while it differs. -->

An account's statement files are read by a *format*, which the account names as its `parser`, or a source's `format`,
in `accounts.yaml` ([configuration.md](configuration.md)). A format is built in, a reader written as code, or a
*preset*: a short file that describes one bank's CSV download (which columns hold the date, the amount and the
description, and which way the amount's sign runs), so no code is written for it. Each row says where its layout comes
from and how far it is known.

- **OFX or QFX.** Many banks and card issuers also offer their activity as an OFX or QFX (Quicken) download. The `ofx`
  format reads it whatever the bank, with no preset: use it when your bank is not in the table.
- **Any other CSV download.** `python -m etl import detect FILE` names the formats that read a file, or the nearest
  ones and why not; `python -m etl import map FILE --id NAME` asks about its columns and writes a preset of your own
  ([configuration.md](configuration.md), "Your own file layouts").

An institution the table does not list (American Express and Citi among them) is read through its OFX or QFX download
or a preset of your own.

The Evidence column says how far a layout is known: *bank-documented*, the bank's own help describes the columns;
*oss-definition*, an open-source project under a licence that allows its use defines them; *widely-reported*, several
independent public descriptions agree on them; *built-in*, a reader written as code in this release.

| Institution | Account | Format | How to get the file | Evidence | Source |
|---|---|---|---|---|---|
"""


def _table_cell(text: str) -> str:
    """Text as one table cell: on one line, every run of whitespace one space, a `|` escaped"""
    return " ".join(text.split()).replace("|", "\\|")


def bank_row(bank: Bank) -> str:
    cells = [_table_cell(bank.institution), _table_cell(bank.account), f"`{bank.format}`",
             _table_cell(bank.download), _table_cell(bank.evidence), _table_cell(bank.source)]
    return "| " + " | ".join(cells) + " |"


def banks() -> list[Bank]:
    """Every format of the release as a row of the table: the built-in ones and each shipped preset, sorted by
    institution, then by format name"""
    rows = list(NATIVE_BANKS) + [
        Bank(p.institution, _ACCOUNT[p.kind], p.id, p.download, p.evidence, p.source)
        for p in registry.presets().values()]
    return sorted(rows, key=lambda b: (b.institution.casefold(), b.format))


def banks_document() -> str:
    return _BANKS_INTRO + "".join(bank_row(b) + "\n" for b in banks())


def banks_command(write: bool, *, out=print) -> int:
    """`python -m etl import banks [--write]`: prints the page, or writes it into the tree"""
    text = banks_document()
    if not write:
        sys.stdout.write(text)
        return 0
    BANKS_DOC.write_text(text, "utf-8")
    out(f"Wrote {BANKS_DOC_SHOWN}.")
    return 0


def map_command(file: str, preset_id: str, *, answers_file: str | None, force: bool) -> int:
    """`python -m etl import map`: with no answers file it asks on the terminal, and refuses to run without one; the
    preset is written into the config folder Paths finds."""
    if answers_file is None and not sys.stdin.isatty():
        raise ValueError("there is no terminal to ask on: pass the answers in a file with --answers FILE")
    paths = Paths()
    if not paths.config_dir.is_dir():
        raise ConfigError([missing_config_dir_problem(paths.config_dir, paths.config_from)])
    answers = None if answers_file is None else load_answers(answers_file)
    return map_layout(file, preset_id, paths.config_dir, answers=answers, force=force)

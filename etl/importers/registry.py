# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The statement formats by name: a format name -> its parse, `parse(path, account_id) -> (txns, FileStats, extra)`.

A parse reads one file for one registry account. `txns` are the rows it read; `stats` counts every line of the file (of
an OFX file, every transaction) as parsed or skipped with a reason, and `stats.sum_cents` is the signed total of exactly
the rows returned; `extra` holds only keys the pipeline reads: `latest_balance` (an `(iso_date, cents)` the file
states, or None), `summary` (a statement's own beginning and ending balances and totals), `running_balances` (each
row's running balance in the order the rows happened, which the pipeline follows for a checking or savings account:
csv_preset.py says its shape), `fitid_rows` (an OFX file's transaction ids, each as a digest, with the rows each
names, which the pipeline joins over an account's files for its `import.ofx_conflict` warning: ofx.py) and
`cut_descriptions` (the positions in `txns` of the rows whose description bounded_description cut, which the pipeline
counts in one `import.long_description.<account>` info check). The occurrence index of each row (`Txn.seq`) is set by
the pipeline, never by a parse.

A description is data, stored as the file has it, formula-leading characters and markup included: the dashboard escapes
what it shows. The CSV presets and OFX store it through bounded_description, which removes control characters (but
whitespace), bidirectional controls and invisible format characters, squashes whitespace and cuts a description longer
than MAX_DESCRIPTION; the three native formats store theirs with only their whitespace squashed, since a
household's stored rows are known by their descriptions.

A module registers its formats when it loads (importing `etl.importers` loads the native ones and `ofx`), and this
module registers each shipped CSV preset (`presets/<id>.yaml`, read by csv_preset.py) under its id when it loads. The
pipeline builds its TXN_PARSERS table from `formats()`, the names an account's `parser:` may state. A file a format
cannot read is refused, never guessed at: its parse raises (`NotThisFormat` with plain words when it knows what is
wrong), and the pipeline turns that into one failing check for the file with `refusal`'s words."""
from __future__ import annotations

import codecs
import csv
import re
import stat
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from ..dates import squash
from ..models import FileStats, Txn

if TYPE_CHECKING:
    from .csv_preset import Preset

Parse = Callable[[Path, str], tuple[list[Txn], FileStats, dict]]

# The shipped CSV presets, one format each
PRESETS_DIR = Path(__file__).parent / "presets"

# A statement file larger than this is refused by its size, before it is opened: a decade of one account's rows is
# well under a tenth of it.
MAX_FILE_BYTES = 50_000_000

# The skip reason of a line a format expected to be a row and could not read (a date or an amount it cannot parse, a
# column it reads missing). A file with such a line and no row is not in the format its account names.
UNPARSEABLE_ROW = "unparseable-row"

# How much of a file the encoding sniff reads (its first 64 KB), and the encodings a preset and OFX try, in order, when
# nothing names others
SNIFF_BYTES = 65_536
DEFAULT_ENCODINGS = ("utf-8-sig", "cp1252")

# The most characters of a description bounded_description keeps: far more than any bank writes, so only a file made to
# hurt a reader (or broken) holds a longer one
MAX_DESCRIPTION = 500
# The characters bounded_description removes: the control characters but the whitespace among them, which the squash
# turns into a space as it does in every format (C0 but tab, line feed, vertical tab, form feed, carriage return and the
# four separators from 0x1C; DEL; C1 but next line), the bidirectional controls (the embeddings, the overrides, the
# isolates and their pops) and marks (left-to-right, right-to-left, the Arabic letter mark) and the invisible format
# characters (the soft hyphen, the zero-width space, the word joiner, the invisible operators and the byte-order mark).
# A description then reads the way it is stored: an unclosed override shows it reversed, and an invisible character
# makes two descriptions that look the same differ. The zero-width joiner and non-joiner stay: scripts and emoji need
# them.
_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1b\x7f-\x84\x86-\x9f\xad\N{ARABIC LETTER MARK}\N{ZERO WIDTH SPACE}"
                      r"\N{LEFT-TO-RIGHT MARK}\N{RIGHT-TO-LEFT MARK}"
                      r"\N{LEFT-TO-RIGHT EMBEDDING}-\N{RIGHT-TO-LEFT OVERRIDE}\N{WORD JOINER}-\N{INVISIBLE PLUS}"
                      r"\N{LEFT-TO-RIGHT ISOLATE}-\N{POP DIRECTIONAL ISOLATE}\N{ZERO WIDTH NO-BREAK SPACE}]")

# What a refused file looked like against what its format expects, as the end of "Not read: ..."
TOO_LARGE = f"it is over {MAX_FILE_BYTES // 1_000_000} MB, far more than a statement export holds"
NOT_A_FILE = "it is a folder, a pipe or a device, not a file"
NO_ROWS = "none of its lines has a date and an amount where this format reads them"
UNDECODABLE = "its bytes are not UTF-8 text, the encoding this format reads"
NOT_CSV = "it is not CSV text this format can read"
UNOPENABLE = "it could not be opened"
NOT_LAID_OUT = "a line in it is not laid out the way this format expects"

_FORMATS: dict[str, Parse] = {}
_PRESETS: dict[str, Preset] = {}


class NotThisFormat(ValueError):
    """Raised by a parse for a file that is not in its format. The message says, in plain words, what the file looked
    like against what the format expects, and is shown to the household as it stands: it never quotes a line of the
    file, an amount or a path."""


def register(name: str, parse: Parse) -> Parse:
    """Register `parse` under the format name `name`, and return it. A name holds one parse: registering another under
    a taken name raises ValueError; the same parse again changes nothing."""
    held = _FORMATS.get(name)
    if held is not None and held is not parse:
        raise ValueError(f"the format name {name!r} is already registered")
    _FORMATS[name] = parse
    return parse


def formats() -> dict[str, Parse]:
    """Every registered format by name, as a new dict: changing it leaves the registry as it is."""
    return dict(_FORMATS)


def presets() -> dict[str, Preset]:
    """Every shipped preset by its format name, as a new dict."""
    return dict(_PRESETS)


def register_presets(directory: Path) -> None:
    """Load every preset in `directory` (`*.yaml`) and register each under its id. They are all loaded before any is
    registered, so a preset that does not load raises its ConfigError and registers nothing: a shipped preset that
    does not load is a defect of the release, raised when this module loads."""
    # imported here, not at the top: the engine imports this module's names, all defined above this call
    from .csv_preset import load_preset

    loaded = [load_preset(path) for path in sorted(directory.glob("*.yaml"))]
    for preset in loaded:
        register(preset.id, preset.parse)
        _PRESETS[preset.id] = preset


def file_head(path: Path) -> tuple[bytes, bool]:
    """A file's first SNIFF_BYTES bytes, and whether they are the whole file"""
    with open(path, "rb") as f:
        head = f.read(SNIFF_BYTES)
        return head, len(head) < SNIFF_BYTES or not f.read(1)


def sniff(head: bytes, whole: bool, encodings: Iterable[str]) -> tuple[list[str], str | None]:
    """The encoding sniff every reader of statement text shares (the presets, OFX and the `import` commands). Of
    `encodings`, in order and each once: the first that decodes `head`, a file's first bytes (`whole`: the whole file,
    so it must decode to its very end), into text with no NUL character (a text export never holds one; a UTF-16 file
    read byte by byte does), then each later one that decodes it into the very same text, so a read that restarts in
    one of them after a later byte fails changes nothing read before that byte; and that text. ([], None) when no
    encoding gives one."""
    usable: list[str] = []
    first: str | None = None
    for encoding in dict.fromkeys(encodings):
        try:
            text = codecs.getincrementaldecoder(encoding)().decode(head, final=whole)
        except UnicodeError:
            continue
        if "\x00" in text:
            continue
        if first is None:
            first = text
        if text == first:
            usable.append(encoding)
    return usable, first


def without_controls(text: str) -> str:
    """`text` with the characters bounded_description removes taken out: control characters but whitespace,
    bidirectional controls and invisible format characters (_CONTROL)"""
    return _CONTROL.sub("", text)


def bounded_description(text: str) -> tuple[str, bool]:
    """A row's description as a CSV preset or OFX stores it, and whether it was cut: its control characters (but
    whitespace), bidirectional controls and invisible format characters removed (without_controls), every whitespace
    run squashed to one space, then cut to its first MAX_DESCRIPTION characters
    (a space the cut leaves at its end dropped, as the squash leaves none). Every other character is kept as the file
    has it."""
    squashed = squash(without_controls(text))
    if len(squashed) <= MAX_DESCRIPTION:
        return squashed, False
    return squashed[:MAX_DESCRIPTION].rstrip(), True


def file_refusal(path: Path) -> str | None:
    """Why a statement file is refused before any format opens it, or None: NOT_A_FILE for anything but a plain file (a
    folder, a pipe, a device, or a link to one: opening a pipe would hold the reader), TOO_LARGE for one over the size
    cap. The ingest and the `import` commands judge a file by it first. Raises OSError when the path cannot be looked
    at (it is not there, or a folder on the way is closed)."""
    found = path.stat()
    if not stat.S_ISREG(found.st_mode):
        return NOT_A_FILE
    if found.st_size > MAX_FILE_BYTES:
        return TOO_LARGE
    return None


def refusal(error: BaseException) -> str:
    """The plain words for a parse that raised `error`: a NotThisFormat's own message, else what the kind of error says
    of the file. The error's own text is never used: it can quote a line of the file, an amount or a path."""
    if isinstance(error, NotThisFormat):
        return str(error)
    if isinstance(error, UnicodeError):
        return UNDECODABLE
    if isinstance(error, csv.Error):
        return NOT_CSV
    if isinstance(error, OSError):
        return UNOPENABLE
    return NOT_LAID_OUT


register_presets(PRESETS_DIR)

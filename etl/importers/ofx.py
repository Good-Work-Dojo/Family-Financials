# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""OFX and QFX statement files: the "Quicken / Money download" most US banks and card issuers offer, read for one bank
or credit card account. One format, `ofx`, reads both: a QFX file is an OFX file with Intuit's own extra tags, and the
glob in a household's accounts.yaml decides which files an account reads.

Both dialects are read by one small tokenizer, never by an XML parser, so a hostile file's DTD or entities are never
processed:
  * OFX 1.x is SGML: a header block of KEY:VALUE lines, then tags where a leaf element is <TAG>value with no closing tag
    and an aggregate is <TAG>...</TAG>; line breaks are optional, so a whole file may be one line;
  * OFX 2.x is XML: an XML declaration and an <?OFX ...?> instruction, then every element closed.
The tokenizer reads from the file's <OFX> tag on and yields opens, leaves (a tag and its text, up to the next tag) and
closes. A tag the reader reads as a leaf (LEAVES) is one, with its text as its value, empty when it has none; a tag the
reader knows as an aggregate (AGGREGATES) is one whatever follows it; any other tag is a leaf when text follows it and
an aggregate when none does, and its text is never kept. A close of a leaf's name right after its text is its own;
a close with no open frame of its name is ignored; one with a frame ends every frame opened inside it too. &amp; &lt;
&gt; &quot; &apos; and numeric character references are decoded; any other &name; is left as written; a CDATA section
holding no < is its text as written. Comments, declarations and processing instructions are passed over. No token but
a comment (whose text is never kept) runs past a <, so a tag in the file is always read as a tag: a stray <! or <? never
swallows the tag after it.

A row is each <STMTTRN> in the <BANKTRANLIST> of the file's one statement response (<STMTRS> for a bank account,
<CCSTMTRS> for a card). It ends at its own </STMTTRN>, at the next row's <STMTTRN> or at the close of an aggregate
around it, as SGML lets a writer leave an end tag out; a file that ends inside its statement is refused. A row:
  date         the first eight characters of its first DTPOSTED, as YYYYMMDD, whatever time and zone follow them
               (20301031120000[-5:EST] is 2030-10-31): never shifted. A row whose DTPOSTED is missing or is not a date
               is skipped (unparseable-date), never dated by another field
  amount       TRNAMT as signed cents, as the file states it: in OFX a card purchase is negative and a payment
               positive, as on a bank account and in this app, so nothing is turned round or detected. A decimal point,
               or a decimal comma written as digits, a comma and two digits (-12,50) with no other separator; a
               fraction of a cent other than zeros is no amount. A row with no TRNAMT is no-amount, one with other text
               unparseable-row
  description  NAME, or the NAME of the row's PAYEE aggregate when it has one; MEMO is added after one space when it is
               not empty, is not inside NAME and NAME is not inside it (ignoring case, the characters
               bounded_description removes taken out and every whitespace run squashed); when NAME is inside MEMO it
               is MEMO. CHECKNUM is not part of it. It is stored through registry.bounded_description: cut to its
               first 500 characters when it is longer
  bank_type    TRNTYPE
FITID, the bank's id of a transaction, is never part of a row's identity. A row whose FITID, date, amount and
description repeat an earlier row of the file is dropped (repeated-fitid); a FITID naming two or more different rows
keeps every one of them. `fitid_rows` maps each FITID's SHA-256 digest (never the id: a bank may put account digits in
it) to the set of (date, cents, description) rows it names in the file; the pipeline joins the maps of an account's
files, so a bank's correction arriving in a later, overlapping download is found too, and warns.

Skipped and counted besides: each row of a pending list (<BANKTRANLISTP>, and any <STMTTRNP>) is pending; a row outside
a statement's transaction list is outside-transaction-list. `extra` holds `latest_balance`, the statement's <LEDGERBAL>
as (the first eight characters of its DTASOF, its BALAMT as signed cents), None without one (AVAILBAL is not read; a
card's is what is owed with the file's sign, and the pipeline books a file's balance only for a checking or savings
account), `fitid_rows`, and `cut_descriptions` (the positions of the rows read whose description was cut) when there is
one.

Refused with NotThisFormat in plain words: a file with no <OFX> tag in its first 64 KB (an HTML page saved as .qfx); one
whose bytes are in no encoding this format reads; one holding more than one statement response, in words that say
whether they are one account's (one statement period a file) or several accounts' (one account a file); an investment
statement (<INVSTMTRS>); an OFX file with no statement; one that ends before its statement does (rows may be missing);
one whose tags nest far deeper than a statement's; and a statement none of whose transactions is a row it can read (a
row outside its list included), so such a file never loads as no rows with its balance. A statement with no transaction
is no row and no failure, and so is a file of no bytes or white space only, as in every other format.

The account's identifiers and the credentials a file can carry (SECRETS: ACCTID, BANKID, BROKERID, USERID, USERPASS,
SESSCOOKIE, CLTCOOKIE and the like, Intuit's INTU.USERID too) never leave this module: none of them is a leaf the reader
reads, so the tokenizer never keeps their text, but for the statement's ACCTID and BANKID (IDENTITY), which reach the
reader only as a digest, to tell two statements of one account from two accounts'; the reader keeps nothing but the
leaves a row and the balance are read from, and no refusal names a value of the file.

Encoding: the header's ENCODING and CHARSET (1.x) or the XML declaration's encoding (2.x) name the first encoding tried,
a UTF-8 byte-order mark puts UTF-8 first whatever the header says, then UTF-8 and Windows-1252 follow. A header naming
a set of one byte a character (Windows-1252, ISO-8859-1) over bytes that are valid UTF-8 with a character of several
bytes in them is read as UTF-8: banks mislabel such files, and a single-byte reading of them gives other descriptions
than the same statement's XML download. The first encoding that reads the file's first 64 KB as text with no NUL
character is the encoding; another is used only when the whole file fails in it and that one reads the first 64 KB as
the very same text, so no row before the failing byte changes; with none, the file is refused. The file is read into
memory once: the pipeline refuses a file over the 50 MB cap before opening it, and this parse refuses one itself
without reading past the cap."""
from __future__ import annotations

import codecs
import datetime as dt
import hashlib
import os
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from ..dates import squash
from ..models import FileStats, Txn
from .registry import (
    DEFAULT_ENCODINGS,
    MAX_FILE_BYTES,
    SNIFF_BYTES,
    TOO_LARGE,
    UNPARSEABLE_ROW,
    NotThisFormat,
    bounded_description,
    register,
    sniff,
    without_controls,
)

FORMAT = "ofx"
# far more nested tags than any statement has (a handful of levels): a deeper file is not a statement
MAX_DEPTH = 1_000

NOT_OFX = "it is not an OFX or QFX statement download (nothing near its start opens an OFX block)"
NOT_TEXT = "its bytes are not text in an encoding this format reads"
MIXED_TEXT = "its text is not in any one encoding this format reads: a part after its start is written in another one"
SEVERAL_ACCOUNTS = "it holds statements for more than one account: download one account at a time"
SEVERAL_PERIODS = "it holds more than one statement: download one statement period at a time"
SEVERAL_STATEMENTS = "it holds more than one statement: download one account and one statement period at a time"
INVESTMENT = "it is an investment account's statement, which this format does not read"
NO_STATEMENT = "it holds no bank or credit card statement"
ENDS_EARLY = "it ends before its statement does, so rows may be missing: download it again"
TOO_DEEP = "its tags are nested far deeper than a statement's"
NONE_READABLE = "none of its transactions has a date and an amount this format can read"

STATEMENTS = frozenset({"STMTRS", "CCSTMTRS", "INVSTMTRS"})
ACCOUNT_FROM = frozenset({"BANKACCTFROM", "CCACCTFROM", "INVACCTFROM"})
ROWS = frozenset({"STMTTRN", "STMTTRNP"})
LIST, PENDING_LIST = "BANKTRANLIST", "BANKTRANLISTP"
# the aggregates of a statement file the reader knows: one of them opens an aggregate whatever text follows its tag
AGGREGATES = frozenset({
    "OFX", "SIGNONMSGSRSV1", "SONRS", "STATUS", "FI", "BANKMSGSRSV1", "CREDITCARDMSGSRSV1", "INVSTMTMSGSRSV1",
    "STMTTRNRS", "CCSTMTTRNRS", "INVSTMTTRNRS", *STATEMENTS, *ACCOUNT_FROM, LIST, PENDING_LIST, *ROWS, "PAYEE",
    "BANKACCTTO", "CCACCTTO", "CURRENCY", "ORIGCURRENCY", "IMAGEDATA", "LEDGERBAL", "AVAILBAL", "BALLIST", "BAL"})
ROW_LEAVES = frozenset({"TRNTYPE", "DTPOSTED", "TRNAMT", "FITID", "NAME", "MEMO"})
BALANCE_LEAVES = frozenset({"BALAMT", "DTASOF"})
# the leaves the reader reads, the only tags whose text the tokenizer keeps: one followed by no text is an empty leaf
LEAVES = ROW_LEAVES | BALANCE_LEAVES
# an account's identifiers and a user's credentials (a tag ending in .<one of them>, such as INTU.USERID, is one too):
# none is a leaf the reader reads, so their text is never kept
SECRETS = frozenset({"ACCTID", "BANKID", "BRANCHID", "BROKERID", "ACCTKEY", "USERID", "USERPASS", "NEWUSERPASS",
                     "USERKEY", "SESSCOOKIE", "CLTCOOKIE"})
# the statement's account: the tokenizer hands the reader a digest of their text, never the text, so that two statements
# of one account can be told from two accounts' statements
IDENTITY = frozenset({"ACCTID", "BANKID"})
# the encodings of one byte a character: a file declaring one over text that is valid UTF-8 with a character of several
# bytes in it is UTF-8 a bank labelled wrongly
SINGLE_BYTE = frozenset({"cp1252", "latin-1"})

_OFX_TAG = re.compile(r"<OFX[ \t\r\n]*>", re.IGNORECASE)
# No token but a comment (whose text is never kept) runs past a <: a declaration, an instruction or a CDATA section
# never swallows the tag after it, so a tag in the file is always read as a tag.
_TOKEN = re.compile(
    r"<!--.*?(?:-->|\Z)"                                               # a comment
    r"|<!\[CDATA\[(?P<cdata>[^<]*?)\]\]>"                              # a CDATA section holding no <: its text
    r"|<[!?][^<>]*>?"                                                  # a declaration or an instruction, up to a < or >
    r"|<(?P<close>/?)(?P<tag>[A-Za-z][\w.-]*)(?P<rest>(?:\s[^<>]*)?/?)>"  # a tag
    r"|(?P<text>[^<]+|<+(?![A-Za-z/!?])|<)",                           # text, and a run of < that starts no tag
    re.DOTALL)
_ENTITY = re.compile(r"&(?:(amp|lt|gt|quot|apos)|#([0-9]{1,7})|#[xX]([0-9A-Fa-f]{1,6}));")
_NAMED = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}
_YMD = re.compile(r"([0-9]{4})([0-9]{2})([0-9]{2})")
_POINT = re.compile(r"([+-]?)([0-9]{0,15})(?:\.([0-9]{0,15}))?")
_COMMA = re.compile(r"([+-]?)([0-9]{1,15}),([0-9]{2})")
_SGML_KEY = re.compile(r"\b(ENCODING|CHARSET):[ \t]*([^\s<]*)")
_XML_ENCODING = re.compile(r"<\?xml\b[^>]*?\bencoding\s*=\s*[\"']([^\"'<>]*)[\"']", re.IGNORECASE)
# a declared encoding's name, folded to lowercase letters and digits -> the codec read with; any other name declares
# nothing this format reads by (USASCII and NONE among them: the defaults read ASCII)
_CHARSETS = {"utf8": "utf-8-sig", "unicode": "utf-8-sig", "1252": "cp1252", "cp1252": "cp1252",
             "windows1252": "cp1252", "iso88591": "latin-1", "88591": "latin-1", "latin1": "latin-1",
             "isolatin1": "latin-1"}

OPEN, LEAF, CLOSE = "open", "leaf", "close"


def parse(path: Path, account_id: str) -> tuple[list[Txn], FileStats, dict]:
    """The rows of one OFX or QFX file for `account_id` (the registry's contract)."""
    with open(path, "rb") as f:
        if os.fstat(f.fileno()).st_size > MAX_FILE_BYTES:
            raise NotThisFormat(TOO_LARGE)
        data = f.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise NotThisFormat(TOO_LARGE)
    if not data.removeprefix(codecs.BOM_UTF8).strip():  # nothing but white space: no rows, as in every other format
        return [], FileStats(source_file=path.name, parser=FORMAT), {"latest_balance": None, "fitid_rows": {}}
    head = data[:SNIFF_BYTES].decode("latin-1")         # every byte is a character: the header is ASCII
    found = _OFX_TAG.search(head)
    if found is None:
        raise NotThisFormat(NOT_OFX)
    text = _text(data, head[:found.start()])
    start = _OFX_TAG.search(text)
    if start is None:                                    # an encoding reads ASCII as ASCII: never, kept as a guard
        raise NotThisFormat(NOT_OFX)
    reader = _Reader(path.name, account_id)
    for kind, tag, value in _events(text, start.start()):
        if kind == OPEN:
            reader.open(tag)
        elif kind == CLOSE:
            reader.close(tag)
        else:
            reader.leaf(tag, value)
    return reader.finish()


# ------------------------------------------------------------------ text

def _text(data: bytes, header: str) -> str:
    """The file's text, read in the encoding it declares (`header` is what precedes its <OFX> tag), else the defaults,
    by the rule the module's docstring states"""
    order: list[str] = []
    if data.startswith(codecs.BOM_UTF8):
        order.append("utf-8-sig")
    declared = _declared(header)
    if declared in SINGLE_BYTE and not data.isascii() and _utf8(data):
        declared = "utf-8-sig"
    if declared:
        order.append(declared)
    order += DEFAULT_ENCODINGS
    usable, _ = sniff(data[:SNIFF_BYTES], len(data) <= SNIFF_BYTES, order)
    if not usable:
        raise NotThisFormat(NOT_TEXT)
    for encoding in usable:
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    raise NotThisFormat(MIXED_TEXT)


def _utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _declared(header: str) -> str | None:
    """The codec the XML declaration's encoding, or the SGML header's ENCODING then CHARSET, names; None when none
    names one this format reads by"""
    def codec(name: str) -> str | None:
        return _CHARSETS.get(re.sub(r"[^0-9a-z]", "", name.casefold()))

    xml = _XML_ENCODING.search(header)
    if xml:
        return codec(xml.group(1))
    keys: dict[str, str] = {}
    for key, value in _SGML_KEY.findall(header):
        keys.setdefault(key, value)
    return codec(keys.get("ENCODING", "")) or codec(keys.get("CHARSET", ""))


def _entity(m: re.Match) -> str:
    if m.group(1):
        return _NAMED[m.group(1)]
    code = int(m.group(2)) if m.group(2) else int(m.group(3), 16)
    if code == 0 or 0xD800 <= code <= 0xDFFF or code > 0x10FFFF:        # no character: left as written
        return m.group(0)
    return chr(code)


def _decoded(text: str) -> str:
    return _ENTITY.sub(_entity, text) if "&" in text else text


def _held(tag: str, parts: list[str], texted: bool) -> tuple[str, str, str]:
    """The event of a tag once the text after it is read: `parts` is that text for one of LEAVES or IDENTITY (empty for
    any other), `texted` whether the text holds more than whitespace"""
    if tag in AGGREGATES:
        return OPEN, tag, ""
    if tag in LEAVES:
        return LEAF, tag, "".join(parts).strip()
    if tag in IDENTITY:
        value = "".join(parts).strip()
        return LEAF, tag, hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""
    return (LEAF, tag, "") if texted else (OPEN, tag, "")


def _events(text: str, start: int) -> Iterator[tuple[str, str, str]]:
    """(kind, TAG, value) for each element of `text` from `start`, tags upper-cased: (OPEN, tag, ""), (LEAF, tag,
    value) or (CLOSE, tag, ""). A leaf of LEAVES has its text stripped as its value, one of IDENTITY a digest of it;
    any other tag's text is never kept."""
    held: str | None = None            # the tag last opened, whose text is still being read
    parts: list[str] = []              # that text, kept for one of LEAVES or IDENTITY only
    texted = False                     # whether that text holds more than whitespace
    for m in _TOKEN.finditer(text, start):
        tag = m.group("tag")
        if tag is None:
            if held is not None:
                kept = held in LEAVES or held in IDENTITY
                piece = m.group("text")
                if piece is None:
                    piece = m.group("cdata")            # None for a comment, a declaration or an instruction
                elif kept:
                    piece = _decoded(piece)
                if piece and kept:
                    parts.append(piece)
                elif piece and not texted:
                    texted = not piece.isspace()
            continue
        tag = tag.upper()
        closing = m.group("close") == "/"
        if held is not None:
            event = _held(held, parts, texted)
            yield event
            held = None
            if closing and tag == event[1] and event[0] == LEAF:    # the leaf's own close: <TAG>value</TAG>
                continue
        if closing:
            yield CLOSE, tag, ""
        elif m.group("rest").endswith("/"):                         # <TAG/>: an element with nothing in it
            event = _held(tag, [], False)
            yield event
            if event[0] == OPEN:
                yield CLOSE, tag, ""
        else:
            held, parts, texted = tag, [], False
    if held is not None:
        yield _held(held, parts, texted)


# ------------------------------------------------------------------ values

def _date(text: str) -> str | None:
    """YYYYMMDD at the start of an OFX date-time, as an ISO date; None when it is not one"""
    m = _YMD.match(text)
    if m is None:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def _cents(text: str) -> int | None:
    """An OFX amount as signed cents; None when it is not one written the way the module's docstring states"""
    m = _COMMA.fullmatch(text) or _POINT.fullmatch(text)
    if m is None:
        return None
    sign, whole, fraction = m.group(1), m.group(2), m.group(3) or ""
    if not whole and not fraction:
        return None
    if fraction[2:].strip("0"):                         # a fraction of a cent
        return None
    cents = int(whole or "0") * 100 + int(fraction[:2].ljust(2, "0"))
    return -cents if sign == "-" else cents


def _description(name: str, memo: str) -> tuple[str, bool]:
    """A row's stored description from its NAME and MEMO, and whether it was cut"""
    name, memo = squash(without_controls(name)), squash(without_controls(memo))
    if not memo or memo.casefold() in name.casefold():
        return bounded_description(name)
    if name.casefold() in memo.casefold():
        return bounded_description(memo)
    return bounded_description(f"{name} {memo}")


# ------------------------------------------------------------------ the reader

class _Reader:
    """Follows the events of one file: the open frames, the row being read and the statement's ledger balance. Counts
    every row as read or skipped with a reason. An event costs constant time on average whatever the file nests (a
    frame is popped once), so a hostile file cannot make the read quadratic."""

    def __init__(self, name: str, account_id: str):
        self.name, self.account_id = name, account_id
        self.stats = FileStats(source_file=name, parser=FORMAT)
        self.txns: list[Txn] = []
        self.stack: list[str] = []                      # the tag of every open frame, innermost last
        self.open_tags: Counter[str] = Counter()        # how many frames of each tag are open
        self.known: list[int] = []                      # the depths of the open frames that are known aggregates
        # each statement's account: (a digest of its BANKID or "", of its ACCTID), None when it names none
        self.accounts: list[tuple[str, str] | None] = []
        self.account: dict[str, str] | None = None      # the statement's account-from aggregate being read
        self.account_at = 0
        self.row: dict[str, str] | None = None          # the row being read: the first leaf of each tag it reads
        self.row_at = 0                                 # its frame's depth
        self.row_pending = self.row_listed = False
        self.ledger: dict[str, str] | None = None       # the statement's LEDGERBAL being read
        self.ledger_at = 0
        self.balance: tuple[str, int] | None = None
        # each FITID's digest -> the different rows it names (date, cents, description)
        self.fitids: dict[str, set[tuple[str, int, str]]] = {}
        self.cut: set[int] = set()                      # the positions in txns of the rows whose description was cut
        self.unreadable = 0                             # rows that are no row of a statement's list, or unreadable

    def _parent(self) -> str | None:
        """The innermost open known aggregate (an unknown tag opened with no text may be an empty leaf: passed over)"""
        return self.stack[self.known[-1]] if self.known else None

    def _several(self) -> NotThisFormat:
        """The refusal of a file of two statements, by whether they are one account's"""
        first, second = self.accounts[0], self.accounts[1]
        if first is None or second is None:
            return NotThisFormat(SEVERAL_STATEMENTS)
        return NotThisFormat(SEVERAL_PERIODS if first == second else SEVERAL_ACCOUNTS)

    def open(self, tag: str) -> None:
        if tag in STATEMENTS:
            self.accounts.append(None)
            if tag == "INVSTMTRS":
                raise NotThisFormat(SEVERAL_ACCOUNTS if len(self.accounts) > 1 else INVESTMENT)
        if tag in ROWS:
            if self.row is not None:                    # SGML may leave a row's end tag out: the next row ends it
                self._unwind(self.row_at)
            parent = self._parent()
            self.row, self.row_at = {}, len(self.stack)
            self.row_pending = tag == "STMTTRNP" or parent == PENDING_LIST
            self.row_listed = parent in (LIST, PENDING_LIST) and any(self.open_tags[t] for t in STATEMENTS)
        elif tag == "LEDGERBAL" and self.ledger is None and self._parent() in STATEMENTS:
            self.ledger, self.ledger_at = {}, len(self.stack)
        elif tag in ACCOUNT_FROM and self.account is None and self._parent() in STATEMENTS:
            self.account, self.account_at = {}, len(self.stack)
        if len(self.stack) >= MAX_DEPTH:
            raise NotThisFormat(TOO_DEEP)
        if tag in AGGREGATES:
            self.known.append(len(self.stack))
        self.stack.append(tag)
        self.open_tags[tag] += 1

    def leaf(self, tag: str, value: str) -> None:
        if self.row is not None:
            if tag not in ROW_LEAVES:
                return
            inner = self.known[-1]                      # the row's frame is a known aggregate: never empty here
            if inner == self.row_at:
                self.row.setdefault(tag, value)
            elif tag == "NAME" and self.stack[inner] == "PAYEE" and self.known[-2] == self.row_at:
                self.row.setdefault("PAYEE.NAME", value)
        elif self.ledger is not None and tag in BALANCE_LEAVES and self.known[-1] == self.ledger_at:
            self.ledger.setdefault(tag, value)
        elif self.account is not None and tag in IDENTITY and self.known[-1] == self.account_at:
            self.account.setdefault(tag, value)

    def close(self, tag: str) -> None:
        """Ends the innermost open frame of `tag` and every frame opened inside it; a close with no open frame of its
        tag is ignored"""
        if self.open_tags[tag]:
            while self._pop() != tag:
                pass

    def _unwind(self, depth: int) -> None:
        """Ends every frame from `depth` on"""
        while len(self.stack) > depth:
            self._pop()

    def _pop(self) -> str:
        """Ends the innermost frame and returns its tag. A row, a ledger balance or an account ends with its frame,
        whatever ended it: SGML lets a writer leave an end tag out, and a file that ends inside its statement is
        refused whole. The second statement's end, or the end of its account, refuses the file."""
        tag = self.stack.pop()
        self.open_tags[tag] -= 1
        depth = len(self.stack)
        if self.known and self.known[-1] == depth:
            self.known.pop()
        if self.row is not None and depth == self.row_at:
            self._end_row()
        elif self.ledger is not None and depth == self.ledger_at:
            self._end_ledger()
        elif self.account is not None and depth == self.account_at:
            account, self.account = self.account, None
            if account.get("ACCTID"):
                self.accounts[-1] = (account.get("BANKID", ""), account["ACCTID"])
            if len(self.accounts) > 1:              # the second statement's account is known: which refusal it is
                raise self._several()
        if tag in STATEMENTS and len(self.accounts) > 1:
            raise self._several()
        return tag

    def _end_row(self) -> None:
        row, self.row = self.row, None
        self.stats.rows_total += 1
        if self.row_pending:
            reason = "pending"
        elif not self.row_listed:
            self.unreadable += 1
            reason = "outside-transaction-list"
        else:
            reason = self._read(row)
        if reason is not None:
            self.stats.skip(reason)

    def _read(self, row: dict[str, str]) -> str | None:
        """Reads a row of a statement's list: None when it is read, else why it is skipped"""
        date = _date(row.get("DTPOSTED", ""))
        text = row.get("TRNAMT", "")
        amount = _cents(text) if text else None
        if date is None or amount is None:
            self.unreadable += 1
            return "unparseable-date" if date is None else UNPARSEABLE_ROW if text else "no-amount"
        description, cut = _description(row.get("PAYEE.NAME", row.get("NAME", "")), row.get("MEMO", ""))
        fitid = row.get("FITID", "")
        if fitid:
            named = self.fitids.setdefault(hashlib.sha256(fitid.encode("utf-8")).hexdigest(), set())
            if (date, amount, description) in named:
                return "repeated-fitid"
            named.add((date, amount, description))
        if cut:
            self.cut.add(len(self.txns))
        self.txns.append(Txn(date=date, account_id=self.account_id, description_raw=description, amount_cents=amount,
                             source_file=self.name, bank_type=row.get("TRNTYPE") or None))
        self.stats.observe(date, amount)
        return None

    def _end_ledger(self) -> None:
        ledger, self.ledger = self.ledger, None
        if self.balance is not None:                    # the first ledger balance is the statement's
            return
        date = _date(ledger.get("DTASOF", ""))
        text = ledger.get("BALAMT", "")
        cents = _cents(text) if text else None
        if date is not None and cents is not None:
            self.balance = (date, cents)

    def finish(self) -> tuple[list[Txn], FileStats, dict]:
        if len(self.accounts) > 1:
            raise self._several()
        if any(self.open_tags[t] for t in STATEMENTS):
            raise NotThisFormat(ENDS_EARLY)
        self._unwind(0)
        if not self.accounts:
            raise NotThisFormat(NO_STATEMENT)
        if self.unreadable and not self.txns:
            raise NotThisFormat(NONE_READABLE)
        extra = {"latest_balance": self.balance, "fitid_rows": self.fitids}
        if self.cut:
            extra["cut_descriptions"] = frozenset(self.cut)
        return self.txns, self.stats, extra


register(FORMAT, parse)

#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Publish gate: proves that a tree about to be published holds nothing from a denylist you supply.

  publish_gate.py export --repo DIR --ref REF --out DIR --manifest FILE --denylist DIR
  publish_gate.py scan --tree DIR --denylist DIR [--parts b,c,d] [--json OUT]
  publish_gate.py images --denylist DIR --export-names FILE [--app-path PREFIX ...] [--base-layers FILE]
                         IMAGE [IMAGE ...]
  publish_gate.py label --denylist DIR       (reads tokens from stdin, one per line; prints one label per token)

Exit 0 clean, 1 findings, 2 usage or error. Output is one line per finding,
`<part> <kind> <path>[:<line>] [hmac:<label>] [×<count>]`, then `findings: N (accepted: M)`; warnings go to stderr.
A token finding's kind names the first way the token was found (see MODES); `counts` (in `--json`) gives its
occurrences in that file or member for each way, each occurrence counted once, and `count` their sum (for a generic
finding, the matches of that text). `line` is the line of the first occurrence in a text file of the scanned tree (null
elsewhere). Findings are kept per file on disk, so two files whose names redact alike stay two findings. The `--json`
summary adds occurrences, tokens loaded per class, matches allowed by allow-paths.tsv, accepted hits and token files
read.

A denylist token is never printed. A hit is named by its label, the first 12 hex characters of HMAC-SHA256 of the
folded token under the key in `<denylist>/hmac.key` (32 random bytes, mode 600, created on first use), so a short token
cannot be recovered from its label by guessing. In a printed path each matched stretch of a component is replaced by
`[hmac:<label of the token>]` (a token allowed for that path stays), or the whole component by
`[redacted:hmac:<label of the component>]` when that cannot be done safely, or the whole path likewise; control
characters in a path print escaped, and error messages carry only fixed text, counts, line numbers, denylist file
numbers and redacted paths. An unexpected exception prints its type, never its message.

Folding is NFKC, case folding and removal of zero-width characters and soft hyphens; a token's whitespace is also
collapsed to one space, while scanned text keeps its whitespace (so that positions keep their line numbers): a match
looks only at alphanumeric runs, and a token without any is searched in the text with its whitespace collapsed alike.
A token matches where the text's maximal alphanumeric runs equal the token's runs, in order,
so any non-alphanumeric characters may separate them and the match needs a non-alphanumeric neighbour (or an edge)
on both sides. Each text is also read in a split view, where a backslash escape (\\n \\t \\r \\f \\v \\" \\' \\\\ \\/)
reads as a space and a boundary falls between a lowercase letter or digit and an uppercase letter, and between an
uppercase run and an uppercase-lowercase pair (`alexandriaShare`, `HTMLQuill`), except inside an alphanumeric run
longer than MAX_CAMEL_RUN characters (base64 data, a minified bundle). A name or host token, or the local
part or domain of an email token, of at least 5 alphanumeric characters in one run is also found inside a longer
run, and the runs of a multi-run one joined together anywhere (kind `<class>-substring`). A last4 also matches after
a run of `x` that starts at a boundary (`x4821`, `XXXX4821`). File names are searched for name-like classes and for
last4 and date tokens, a date also in its `YYYYMMDD` form.

Parts:
  a  export: `git archive REF | tar -x` into an empty OUT, then structural checks against a JSON manifest with the
     keys allowed_extensions, allowed_bare_names, top_level, forbidden_paths and executables. A bare name (a file
     name with no extension) is allowed where an entry of allowed_bare_names places it: `NAME` anywhere, `/NAME` or
     `DIR/NAME` at that one path, `DIR/**/NAME` anywhere under DIR
  b  PNG tEXt, zTXt, iTXt and eXIf chunks
  c  denylist tokens in contents and names: in decoded text (UTF-8, BOM-marked UTF-16/32, cp1252, or the UTF-8 and
     UTF-16 runs of a binary), in its percent-, \\u- and &#-decoded forms, and as hex and base64 (standard and
     URL-safe). Archives (zip, tar, gzip, xz/lzma, bz2, and zstd where Python provides it), recognised by magic
     number (a zip or tar named only by its extension is probed; anything else named like an archive is read as it
     is), are opened and their members scanned as `outer!member`, in bounded memory
  d  emails outside the example domains, RFC1918 addresses, home-directory paths, and lockfile package sources
     outside the registries
  e  images: `docker save` into a private temporary directory, read from disk; layers, `docker inspect` env and
     labels and `docker history` are scanned with c and d (with --app-path prefixes, d and the vendor class only
     under them, and in env, labels and history: a merchant's name is often an ordinary word, which third-party
     files are full of), plus source maps, files whose basename is in --export-names, and layers the image manifest
     lists that are missing or are not archives. --base-layers names a file of layer diff IDs (`sha256:<hex>`, one
     a line) of the pinned base images: a layer of the image whose diff ID (`docker inspect`'s RootFS, in the order
     of the saved manifest's layers) is listed is upstream content and is not scanned, and the count of such
     layers goes to stderr

Denylist directory: `*.tsv` with lines `class<TAB>token`; `allow-vendors.txt` (one vendor per line, not denied);
`allow-paths.tsv` with `class<TAB>token<TAB>glob[<TAB>kind]` (that token is allowed in paths matching the glob, and
is not redacted in them; with a kind, only findings of exactly that kind, and in a printed path only matches of that
kind's file-name form); `accepted.tsv` with `label<TAB>path` (that hit is reported as accepted; `label` prints a
token's label, for a d finding the token is the matched text, and the path is written as on disk, or as printed when
no other file with a finding prints the same way; a member of an image layer may also be named without its layer,
`IMAGE!MEMBER`, which accepts that hit in whichever layer of that image holds it, so an acceptance outlives a
rebuild that changes the layer's digest);
`hmac.key`. Anything else in the directory (another file, a dotfile, a backup copy, a sub-directory) is an error, so
a word list saved under the wrong name cannot be silently skipped. Blank lines and `#` comments are ignored. Stdlib
only.
"""
from __future__ import annotations

import argparse
import base64
import bz2
import fnmatch
import gzip
import hashlib
import hmac
import io
import ipaddress
import json
import lzma
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import warnings
import zipfile
from bisect import bisect_right
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from itertools import accumulate, chain, compress, islice
from operator import add
from pathlib import Path
from urllib.parse import unquote_plus

try:  # zstd joins the standard library in Python 3.14
    from compression import zstd
except ImportError:
    zstd = None

CLASSES = ("identifier", "name", "email", "host", "amount", "date", "last4", "vendor")
VARIANT_CLASSES = frozenset({"identifier", "name", "email", "host"})
DATA_CLASSES = frozenset({"amount", "date", "last4"})
NAME_CLASSES = VARIANT_CLASSES | {"vendor"}
ALL_CLASSES = frozenset(CLASSES)
DATA_EXTS = frozenset({".yaml", ".yml", ".json", ".csv", ".tsv", ".ofx", ".qfx", ".txt", ".sql"})
LOCK_FILES = frozenset({"package-lock.json", "uv.lock"})
ARCHIVE_EXTS = {".zip": "zip", ".tar": "tar", ".gz": "gzip", ".tgz": "gzip", ".xz": "xz", ".txz": "xz",
                ".lzma": "xz", ".bz2": "bz2", ".tbz2": "bz2", ".zst": "zstd"}
STEMS = ((".tgz", ".tar"), (".txz", ".tar"), (".tbz2", ".tar"), (".gz", ""), (".xz", ""), (".lzma", ""),
         (".bz2", ""), (".zst", ""))
SUBSTRING_CLASSES = frozenset({"name", "host", "email"})
# the ways a token can be found, in precedence order: a token finding takes the kind of the first it was found in,
# and each occurrence is counted in the first that finds it
MODES = ("", "percent", "unicode-escape", "html-entity", "hex", "base64", "substring")
MIN_TOKEN = 3
MIN_ENCODED = 6          # shorter encoded forms match random hashes and blobs, so they are counted, not searched
MIN_SUBSTRING = 5        # a shorter name is too often a part of an ordinary word
MAX_CAMEL_RUN = 40       # a longer alphanumeric run gets no camel-case boundaries
CACHE_RUNS = 1 << 16     # how many distinct runs the substring search remembers before it starts again
MAX_FILE = 1_048_576
MAX_DEPTH = 8
CHUNK = 1 << 20          # content is read in pieces of this size, so memory does not grow with a member
SEARCH = 4096            # how far back from a cut to look for a separator byte
TAIL = 64 << 10          # how many consumed bytes a stream keeps, for the tar end check
HEAD = 512               # enough to recognise every supported archive
KEY_FILE = "hmac.key"
NOT_TOKEN_FILES = ("allow-paths.tsv", "accepted.tsv")
OTHER_FILES = ("allow-vendors.txt", KEY_FILE)    # the only entries of the denylist directory besides *.tsv
ALLOWED_EMAIL_DOMAINS = frozenset({"example.com", "example.org", "example.net", "users.noreply.github.com",
                                   "anthropic.com"})
LOCK_HOSTS = frozenset({"registry.npmjs.org", "files.pythonhosted.org", "pypi.org"})
HOME_IGNORED = frozenset({"user", "username", "you", "me", "name", "runner", "app", "node", "root"})
# 10/8, 172.16/12 and 192.168/16, written as integers so that this file passes its own private-ip rule
PRIVATE_NETS = tuple(ipaddress.IPv4Network(n) for n in ((10 << 24, 8), (172 << 24 | 16 << 16, 12),
                                                        (192 << 24 | 168 << 16, 16)))
MANIFEST_KEYS = ("allowed_extensions", "allowed_bare_names", "top_level", "forbidden_paths", "executables")
PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_TEXT = frozenset({b"tEXt", b"zTXt", b"iTXt", b"eXIf"})
INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠­﻿"))
INVISIBLE_CHAR = re.compile("[" + "".join(map(chr, INVISIBLE)) + "]")
URLSAFE = str.maketrans("+/", "-_")

RUN = re.compile(r"[^\W_]+")                     # maximal runs of str.isalnum() characters
SPACE = re.compile(r"\s+")
LINE_SPACE = re.compile(r"[^\S\n]+")             # whitespace other than a newline
_LOWER = "".join(re.escape(chr(c)) for c in range(0x2000) if chr(c).islower())
_UPPER = "".join(re.escape(chr(c)) for c in range(0x2000) if chr(c).isupper())
# where the split view puts a space: over a backslash escape, and at a camel-case boundary (a lowercase letter or digit
# then an uppercase letter; an uppercase letter then an uppercase-lowercase pair)
SPLIT = re.compile(rf"""\\[ntrfv"'\\/]|(?=[{_UPPER}])(?:(?<=[{_LOWER}0-9])|(?<=[{_UPPER}])(?=.[{_LOWER}]))""")
SPLIT_CUT = re.compile(f"({SPLIT.pattern})")         # the same, kept by re.split
# a run of x (a masked card number, folded) that starts at a boundary and ends where four digits end the run
MASK = re.compile(r"(?<![^\W_])x+(?=[0-9]{4}(?![^\W_]))")
ISO_DATE = re.compile(r"([0-9]{4})[-/.]([0-9]{2})[-/.]([0-9]{2})")
LONG_RUN = re.compile(r"[^\W_]{%d,}" % (MAX_CAMEL_RUN + 1))
LONG_SPLIT = re.compile(f"({LONG_RUN.pattern})")
BLANK = re.compile(rb"[\x00\t\n\r ]")
SEPARATOR = re.compile(rb"[\x00-\x2f\x3a-\x40\x5b-\x60\x7b-\x7f]")
# A binary is searched as runs of printable ASCII and, separately, of wider alphabets, so a stray byte that
# decodes to a letter next to a token cannot hide it.
ASCII_RUN = re.compile(rb"[\t\x20-\x7e]{4,}")
UTF16LE_ASCII = re.compile(rb"(?:[\t\x20-\x7e]\x00){4,}")
UTF16BE_ASCII = re.compile(rb"(?:\x00[\t\x20-\x7e]){4,}")
UTF8_RUN = re.compile(rb"(?:[\t\x20-\x7e]|[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}|[\xf0-\xf4][\x80-\xbf]{3}){4,}")
# UTF-16 code units for printable ASCII, Latin-1, Latin Extended, Greek, Cyrillic and general punctuation
UTF16LE_RUN = re.compile(rb"(?:[\t\x20-\x7e\xa0-\xff]\x00|[\x00-\xff][\x01\x04]|[\x00-\x4f]\x02|[\x70-\xff]\x03"
                         rb"|[\x10-\x27]\x20){4,}")
UTF16BE_RUN = re.compile(rb"(?:\x00[\t\x20-\x7e\xa0-\xff]|[\x01\x04][\x00-\xff]|\x02[\x00-\x4f]|\x03[\x70-\xff]"
                         rb"|\x20[\x10-\x27]){4,}")
HEX_RUN = re.compile(r"[0-9a-f]{%d,}" % MIN_ENCODED)
B64_RUN = re.compile(r"[A-Za-z0-9+/_-]{%d,}" % MIN_ENCODED)
PCT = re.compile(r"%[0-9A-Fa-f]{2}")
UESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
ENTITY = re.compile(r"&#(?:([0-9]{1,7})|[xX]([0-9a-fA-F]{1,6}));?")
ISO_T = re.compile(r"(?<=[0-9])t(?=[0-9])")      # the T of an ISO timestamp, so a date matches inside one
# the local part starts where a run of its characters starts, so a long run of dots is not rescanned per character
EMAIL = re.compile(r"(?<![a-z0-9._%+-])[a-z0-9._%+-]+@((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)
IPV4 = re.compile(r"(?<![0-9.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![0-9]|\.[0-9])")
# a path that starts at /home, /users or a WSL mount's /mnt/<drive>/users (not ./home, a relative directory), or a
# Windows profile directory
HOME = re.compile(r"(?:(?<![a-z0-9_.])/(?:home|users|mnt/[a-z]/users)/|\b[a-z]:(?:\\+|/)users(?:\\+|/))([a-z0-9._-]+)",
                  re.I)
NPM_SOURCE = re.compile(r'"resolved"\s*:\s*"([^"]*)"')
UV_SOURCE = re.compile(r"""\b(?:url|registry|git)\s*=\s*["']([^"']*)["']""")
URL_HOST = re.compile(r"[a-z][a-z0-9+.-]*://([^/\s?#\\]+)", re.I)


class GateError(Exception):
    """A failure the CLI reports with exit 2. Messages hold fixed text, counts, line numbers and redacted paths."""


class _Damage(Exception):
    """A read failure inside the archive at `owner` (None: a file of the scanned tree)."""

    def __init__(self, owner: str | None) -> None:
        super().__init__("damaged archive")
        self.owner = owner


@dataclass(frozen=True)
class Finding:
    part: str
    kind: str
    path: str
    token_hmac: str | None = None
    count: int = 1
    line: int | None = None
    counts: dict[str, int] = field(default_factory=lambda: {"": 1})     # occurrences by mode; their sum is count

    def render(self) -> str:
        return (f"{self.part} {self.kind} {self.path}" + (f":{self.line}" if self.line else "")
                + (f" hmac:{self.token_hmac}" if self.token_hmac else "")
                + (f" ×{self.count}" if self.count > 1 else ""))


def _normal(s: str) -> str:
    """fold() without the whitespace collapse: it keeps every newline, so a position in the result has a line"""
    if s.isascii():                          # NFKC and the invisible characters leave ASCII as it is
        return s.lower()
    if INVISIBLE_CHAR.search(s):
        s = s.translate(INVISIBLE)
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", s).casefold())


def fold(s: str) -> str:
    return SPACE.sub(" ", _normal(s))


def _hmac12(key: bytes, s: str) -> str:
    return hmac.new(key, s.encode("utf-8", "surrogateescape"), hashlib.sha256).hexdigest()[:12]


def _basename(path: str) -> str:
    return re.split(r"[/!]", path)[-1]


def _escape(path: str) -> str:
    """`path` with backslashes, control, format and line-separator characters escaped, so it stays one output line"""
    if path.isprintable() and "\\" not in path:
        return path
    out = []
    for ch in path:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif 0xDC80 <= o <= 0xDCFF:                  # an undecodable byte of a file name
            out.append(f"\\x{o - 0xDC00:02x}")
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp"):
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}.get(ch) or
                       (f"\\x{o:02x}" if o < 0x100 else f"\\u{o:04x}"))
        else:
            out.append(ch)
    return "".join(out)


# --- matchers -------------------------------------------------------------------------------------------------------

Spans = dict[tuple[str, str], list[tuple[int, int]]]    # (class, folded token) -> where in the text it occurs


def _run_starts(text: str, indices: set[int]) -> dict[int, int]:
    """Where each run of `text` whose index is in `indices` starts, in one pass that stops at the last of them"""
    out, runs, last = {}, RUN.finditer(text), 0
    for i in sorted(indices):
        out[i] = next(islice(runs, i - last, None)).start()
        last = i + 1
    return out


class Trie:
    """Tokens indexed by their alphanumeric runs. A token occurs wherever the text's maximal runs equal its runs in
    order, so one walk over the text's runs finds every hit; consecutive maximal runs always have a separator between
    them, and a maximal run always has a non-alphanumeric neighbour or an edge on each side."""

    def __init__(self) -> None:
        self.root: dict = {}
        self.bare: dict[str, set] = {}           # tokens with no alphanumeric character, found as substrings

    def add(self, token: str, payload: tuple[str, str]) -> None:
        runs = RUN.findall(token)
        if not runs:
            self.bare.setdefault(token, set()).add(payload)
            return
        node = self.root
        for r in runs:
            node = node.setdefault(r, {})
        node.setdefault(None, set()).add(payload)

    def find(self, text: str, runs: list[str] | None = None) -> Spans:
        """Every occurrence of every token in `text`; `runs` is RUN.findall(text) when the caller has it"""
        hits: Spans = {}
        if self.root:
            runs = RUN.findall(text) if runs is None else runs
            if not self.root.keys().isdisjoint(runs):
                found, n = [], len(runs)
                for i, r in enumerate(runs):
                    node, k = self.root.get(r), i
                    while node is not None:
                        if None in node:
                            found.append((node[None], i, k))
                        k += 1
                        node = node.get(runs[k]) if k < n else None
                if found:
                    at = _run_starts(text, {i for _, i, _ in found} | {k for _, _, k in found})
                    for payloads, i, k in found:
                        for p in payloads:
                            hits.setdefault(p, []).append((at[i], at[k] + len(runs[k])))
        if self.bare:
            text = LINE_SPACE.sub(" ", text)      # a token's whitespace is folded to one space; keep the newlines
            for tok, payloads in self.bare.items():
                at = text.find(tok)
                while at >= 0:
                    for p in payloads:
                        hits.setdefault(p, []).append((at, at + len(tok)))
                    at = text.find(tok, at + len(tok))
        return hits


class Grams:
    """Encoded forms, found anywhere inside a run of their alphabet: an encoded token normally sits inside a longer
    encoded blob, so no boundary applies. Each form is indexed by its first MIN_ENCODED characters, and the scan
    looks each window up in place, so memory does not grow with the run."""

    def __init__(self, run_re: re.Pattern) -> None:
        self.run_re, self.index = run_re, {}

    def add(self, form: str, payload: tuple[str, str]) -> None:
        self.index.setdefault(form[:MIN_ENCODED], {}).setdefault(form, set()).add(payload)

    def find(self, text: str) -> Spans:
        hits: dict[tuple[str, str], dict[int, int]] = {}
        index, w = self.index, MIN_ENCODED
        if not index:
            return {}
        for m in self.run_re.finditer(text):
            for k in range(m.start(), m.end() - w + 1):
                forms = index.get(text[k:k + w])
                if forms:
                    for form, payloads in forms.items():
                        if text.startswith(form, k):
                            for p in payloads:          # one occurrence per position, whichever form matched
                                at = hits.setdefault(p, {})
                                at[k] = max(at.get(k, 0), k + len(form))
        return {p: list(at.items()) for p, at in hits.items()}


class Substrings:
    """Forms found inside alphanumeric runs: a form marked `whole` also where it is the whole run, the others only
    inside a longer one. Each form is indexed by its first MIN_SUBSTRING characters. A scan looks at each distinct run
    once and remembers the answer, up to CACHE_RUNS runs of at most 64 characters, so a word that recurs costs one set
    lookup and memory stays bounded."""

    def __init__(self) -> None:
        self.index: dict[str, dict[str, dict[tuple[str, str], bool]]] = {}
        self.clean: set[str] = set()
        self.dirty: dict[str, list] = {}

    def add(self, form: str, payload: tuple[str, str], whole: bool) -> None:
        payloads = self.index.setdefault(form[:MIN_SUBSTRING], {}).setdefault(form, {})
        payloads[payload] = payloads.get(payload, False) or whole

    def _search(self, run: str) -> list[tuple[tuple[str, str], int, int]]:
        out, index, w = [], self.index, MIN_SUBSTRING
        for k in range(len(run) - w + 1):
            forms = index.get(run[k:k + w])
            if forms:
                for form, payloads in forms.items():
                    if run.startswith(form, k):
                        inside = len(form) < len(run)
                        out += [(p, k, k + len(form)) for p, whole in payloads.items() if whole or inside]
        return out

    def find(self, text: str, runs: list[str]) -> Spans:
        """Every occurrence in `text`, whose runs are `runs`"""
        if not self.index:
            return {}
        distinct = set(runs)
        found = {r: self.dirty[r] for r in distinct.intersection(self.dirty)}
        new = distinct - self.clean
        new.difference_update(found)
        if len(self.clean) > CACHE_RUNS or len(self.dirty) > CACHE_RUNS:
            self.clean.clear()
            self.dirty.clear()
        for r in new:
            hits = self._search(r) if len(r) >= MIN_SUBSTRING else []
            if hits:
                found[r] = hits
            if len(r) <= 64:
                if hits:
                    self.dirty[r] = hits
                else:
                    self.clean.add(r)
        if not found:
            return {}
        indices = list(compress(range(len(runs)), map(found.__contains__, runs)))   # the runs that hold a hit
        at = _run_starts(text, set(indices))
        out: Spans = {}
        for i in indices:
            for p, a, b in found[runs[i]]:
                out.setdefault(p, []).append((at[i] + a, at[i] + b))
        return out


def _encodings(form: str) -> Iterator[tuple[str, str]]:
    """Hex of the UTF-8 bytes (lower case: scanned text is case-folded, which also covers upper-case hex), and the
    base64 characters fixed by the token alone at each of the three byte alignments, standard and URL-safe."""
    data = form.encode("utf-8")
    yield "hex", data.hex()
    for align in range(3):
        enc = base64.b64encode(b"\0" * align + data).decode("ascii")
        core = enc[-(-8 * align // 6):8 * (align + len(data)) // 6]
        yield "base64", core
        yield "base64", core.translate(URLSAFE)


# --- text views -----------------------------------------------------------------------------------------------------

def _unescape_u(text: str) -> str:
    s = UESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)
    return s.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def _unescape_entities(text: str) -> str:
    def char(m: re.Match) -> str:
        cp = int(m.group(1)) if m.group(1) else int(m.group(2), 16)
        return chr(cp) if 0 < cp <= 0x10FFFF and not 0xD800 <= cp <= 0xDFFF else "�"
    return ENTITY.sub(char, text)


@dataclass
class Reading:
    """One decoded text of a piece and the views made from it: `folded`, the text folded; `split`, its split view
    folded (backslash escapes read as spaces, a space at each camel-case boundary), None when that adds nothing; and
    `decoded`, (mode, text, folded text) for its percent-, \\u- and &#-decoded forms. Folding keeps newlines, so a
    position in any view gives a line number. The split view is matched against the denylist only."""
    raw: str
    folded: str
    split: str | None
    decoded: list[tuple[str, str, str]]
    parts: list[str] = field(default_factory=list)      # _split_parts(raw), kept for _split_map


def _readings(texts: list[str]) -> list[Reading]:
    out = []
    for t in texts:
        decoded = []
        if PCT.search(t):
            decoded.append(("percent", unquote_plus(t)))
        if "\\u" in t:
            decoded.append(("unicode-escape", _unescape_u(t)))
        if "&#" in t:
            decoded.append(("html-entity", _unescape_entities(t)))
        parts = _split_parts(t) if SPLIT.search(t) else []
        split = _split_text(t, parts) if parts else t
        out.append(Reading(t, _normal(t), None if split is t else _normal(split),
                           [(mode, d, _normal(d)) for mode, d in decoded], parts))
    return out


def _split_parts(text: str) -> list[str]:
    """`text` cut around its alphanumeric runs longer than MAX_CAMEL_RUN (base64 data, a minified bundle): the odd
    items are those runs, which the split view leaves whole, since camel-case boundaries there would only chop noise
    into short words. An escape whose letter starts such a run is left alone too."""
    return LONG_SPLIT.split(text) if LONG_RUN.search(text) else [text]


def _split_text(text: str, parts: list[str]) -> str:
    """The split view of `text`, unfolded, from its _split_parts: each boundary becomes one space (`text` itself when
    there is none)"""
    split = SPLIT.sub(" ", text) if len(parts) == 1 else "".join(
        p if i % 2 else SPLIT.sub(" ", p) for i, p in enumerate(parts))
    return text if split == text else split


def _split_map(text: str, parts: list[str], folded: str, split: str) -> Callable[[int], int] | None:
    """A function from a position in `split` (the folded split view of `text`) to the position in `folded` (the
    folded `text`) of the same character; None when folding the stretches between boundaries one by one does not give
    both texts back (a boundary changed how a neighbouring character folds)"""
    segs, bounds = [""], []
    for i, part in enumerate(parts):
        if i % 2:
            segs[-1] += part
            continue
        items = SPLIT_CUT.split(part)             # stretch, boundary, stretch, ..., stretch
        segs[-1] += items[0]
        segs += items[2::2]
        bounds += items[1::2]
    fold_one = str.lower if text.isascii() else _normal
    segs, bounds = list(map(fold_one, segs)), list(map(fold_one, bounds))
    if "".join(chain.from_iterable(zip(segs, [*bounds, ""], strict=True))) != folded or " ".join(segs) != split:
        return None
    lengths = list(map(len, segs))
    s_at = list(accumulate(map((1).__add__, lengths[:-1]), initial=0))
    f_at = list(accumulate(map(add, lengths[:-1], map(len, bounds)), initial=0))

    def to_folded(q: int) -> int:
        i = bisect_right(s_at, q) - 1
        return f_at[i] + q - s_at[i]
    return to_folded


def _bom(head: bytes) -> str | None:
    for bom, enc in ((b"\xff\xfe\x00\x00", "utf-32-le"), (b"\x00\x00\xfe\xff", "utf-32-be"),
                     (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if head.startswith(bom):
            return enc
    return None


def _decode(piece: bytes, encoding: str | None) -> list[str]:
    """The texts of a piece: as its BOM says; as UTF-8; when not UTF-8, as UTF-8 with replacement and as cp1252;
    and for a binary (it holds NUL bytes), its runs of UTF-8 and of UTF-16 in either byte order, one text per kind of
    run. The texts of a piece are alternative readings of the same bytes, so a count takes the largest of them."""
    if encoding:
        return [piece.decode(encoding, "replace")]
    if b"\0" not in piece:
        try:
            return [piece.decode("utf-8")]
        except UnicodeDecodeError:
            return [piece.decode("utf-8", "replace"), piece.decode("cp1252", "replace")]
    texts = []
    for pattern, encoding in ((ASCII_RUN, "ascii"), (UTF8_RUN, "utf-8"), (UTF16LE_ASCII, "utf-16-le"),
                              (UTF16LE_RUN, "utf-16-le"), (UTF16BE_ASCII, "utf-16-be"), (UTF16BE_RUN, "utf-16-be")):
        text = "\n".join(m.group().decode(encoding, "replace") for m in pattern.finditer(piece))
        if text:
            texts.append(text)
    return texts


# --- the denylist ---------------------------------------------------------------------------------------------------

def _lines(path: Path, where: str, tabs: bool = False) -> Iterator[tuple[int, str]]:
    """(line number, stripped line) for each line that is not blank or a # comment; `where` names the file in errors.
    With `tabs`, a tab at either end is kept, so that an empty last column still counts as a column."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise GateError(f"cannot read {where}") from None
    for n, line in enumerate(text.split("\n"), 1):
        kept = line.strip(" \r\n\x0b\x0c") if tabs else line.strip()
        if line.strip() and not line.strip().startswith("#"):
            yield n, kept


def _load_key(d: Path) -> bytes:
    """The label key in `d`, created (32 random bytes, mode 600) when absent"""
    d = Path(d)
    if not d.is_dir():
        raise GateError("denylist directory not found")
    p = d / KEY_FILE
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    except OSError:
        raise GateError(f"cannot create {KEY_FILE} in the denylist directory") from None
    else:
        with os.fdopen(fd, "wb") as f:
            f.write(secrets.token_bytes(32))
    try:
        mode, key = os.stat(p).st_mode, p.read_bytes()
    except OSError:
        raise GateError(f"cannot read {KEY_FILE}") from None
    if mode & 0o077:
        raise GateError(f"{KEY_FILE} must be readable by its owner only (chmod 600)")
    if len(key) != 32:
        raise GateError(f"{KEY_FILE} must hold exactly 32 bytes")
    return key


def _kind(cls: str, mode: str, filename: bool = False) -> str:
    """The kind of a token finding: its class, the mode it was found in (none when plain), `filename` for a name"""
    return "-".join([cls, *([mode] if mode else []), *(["filename"] if filename else [])])


def _kinds(cls: str) -> set[str]:
    return {_kind(cls, mode, filename) for mode in MODES for filename in (False, True)}


def _substring_forms(cls: str, tok: str) -> Iterator[tuple[str, bool]]:
    """(form, whole) for each form of a folded token that is also searched inside runs: a single run of at least
    MIN_SUBSTRING characters inside a longer run (equal to the whole run it is an ordinary match), and the runs of a
    multi-run token joined together, there also as the whole run. The local part and the domain of an email each
    count as such a token."""
    for part in tok.rsplit("@", 1) if cls == "email" else [tok]:
        runs = RUN.findall(part)
        if len("".join(runs)) >= MIN_SUBSTRING:
            yield "".join(runs), len(runs) > 1


class Denylist:
    def __init__(self, entries: list[tuple[str, str]], key: bytes, allow_paths=(), accepted=(), short: int = 0,
                 files: int = 0) -> None:
        self.key, self.short, self.skipped, self.files = key, short, 0, files
        self.accepted = set(accepted)
        self.allow_paths: dict[tuple[str, str], list[tuple[str, str | None]]] = {}
        for cls, tok, glob, *kind in allow_paths:
            self.allow_paths.setdefault((cls, tok), []).append((glob, kind[0] if kind else None))
        # contents: everything but data classes, data classes; file names add the YYYYMMDD form of each date
        self.everywhere, self.data, self.compact = Trie(), Trie(), Trie()
        self.hex, self.b64 = Grams(HEX_RUN), Grams(B64_RUN)
        self.substrings = Substrings()
        self._redacted: dict[str, str] = {}
        longest = 0
        spellings: dict[tuple[str, str], set[str]] = {}
        loaded: set[tuple[str, str]] = set()
        for cls, spelling in entries:
            tok = fold(spelling)
            loaded.add((cls, tok))
            (self.data if cls in DATA_CLASSES else self.everywhere).add(tok, (cls, tok))
            if cls == "date" and (m := ISO_DATE.fullmatch(tok)):
                self.compact.add("".join(m.groups()), (cls, tok))
            if cls in SUBSTRING_CLASSES:
                for form, whole in _substring_forms(cls, tok):
                    self.substrings.add(form, (cls, tok), whole)
            longest = max(longest, len(tok))
            if cls in VARIANT_CLASSES:
                spellings.setdefault((cls, tok), set()).add(spelling)
        self.tokens_by_class = {c: sum(1 for cls, _ in loaded if cls == c) for c in CLASSES}
        for payload, forms in spellings.items():
            encoded = {e for s in forms for f in (s, s.lower(), s.upper(), s.title()) for e in _encodings(f)}
            for mode, form in encoded:
                if len(form) < MIN_ENCODED:
                    self.skipped += 1
                else:
                    (self.hex if mode == "hex" else self.b64).add(form, payload)
                    longest = max(longest, len(form))
        # pieces of a large member overlap by more than the longest form in its widest encoding (UTF-32)
        self.overlap = min(4 * longest + 64, CHUNK // 4)

    @classmethod
    def load(cls, d: Path) -> Denylist:
        d = Path(d)
        key = _load_key(d)
        try:
            listing = list(os.scandir(d))
            names = sorted(e.name for e in listing if e.is_file() and not e.name.startswith(".")
                           and (e.name.endswith(".tsv") or e.name in OTHER_FILES))
            unexpected = len(listing) - len(names)
        except OSError:
            raise GateError("cannot list the denylist directory") from None
        if unexpected:
            # counted, never named: a file name could itself be a token
            raise GateError(f"denylist directory holds {unexpected} unexpected entries (only *.tsv, "
                            "allow-vendors.txt and hmac.key belong there)")
        files = [d / n for n in names if n.endswith(".tsv") and n not in NOT_TOKEN_FILES]
        if not files:
            raise GateError("denylist directory holds no token files (*.tsv)")
        entries, short = [], 0
        for i, f in enumerate(files, 1):
            where = f"denylist file #{i}"        # a file name could itself be a token, so files go by number
            for n, line in _lines(f, where):
                fields = [x.strip() for x in line.split("\t")]
                if len(fields) != 2 or not fields[1]:
                    raise GateError(f"{where}, line {n}: expected class<TAB>token")
                if fields[0] not in CLASSES:
                    raise GateError(f"{where}, line {n}: unknown class")
                if len(fold(fields[1])) < MIN_TOKEN:
                    short += 1
                else:
                    entries.append((fields[0], unicodedata.normalize("NFKC", fields[1])))
        vendors = d / "allow-vendors.txt"
        allowed = {fold(line) for _, line in _lines(vendors, vendors.name)} if vendors.exists() else set()
        entries = [(c, s) for c, s in entries if not (c == "vendor" and fold(s) in allowed)]
        if not entries:
            raise GateError("denylist holds no usable tokens")
        allow_paths = []
        if (d / "allow-paths.tsv").exists():
            for n, line in _lines(d / "allow-paths.tsv", "allow-paths.tsv", tabs=True):
                fields = [x.strip() for x in line.split("\t")]
                if len(fields) not in (3, 4) or fields[0] not in CLASSES or not all(fields[1:]):
                    raise GateError(f"allow-paths.tsv, line {n}: expected class<TAB>token<TAB>glob[<TAB>kind]")
                if len(fields) == 4 and fields[3] not in _kinds(fields[0]):
                    raise GateError(f"allow-paths.tsv, line {n}: unknown kind for that class")
                allow_paths.append((fields[0], fold(fields[1]), *fields[2:]))
        accepted = []
        if (d / "accepted.tsv").exists():
            for n, line in _lines(d / "accepted.tsv", "accepted.tsv"):
                fields = [x.strip() for x in line.split("\t")]
                if len(fields) != 2 or not re.fullmatch(r"[0-9a-fA-F]{12}", fields[0]) or not fields[1]:
                    raise GateError(f"accepted.tsv, line {n}: expected label<TAB>path")
                accepted.append((fields[0].lower(), fields[1]))
        return cls(entries, key, allow_paths, accepted, short, len(files))

    def label(self, token: str) -> str:
        return _hmac12(self.key, fold(token))

    def warnings(self) -> list[str]:
        out = []
        if self.short:
            out.append(f"{self.short} denylist tokens shorter than {MIN_TOKEN} characters are ignored")
        if self.skipped:
            out.append(f"{self.skipped} encoded forms shorter than {MIN_ENCODED} characters are not searched")
        return out

    def _data(self, folded: str, filename: bool) -> Spans:
        """Data-class hits in a folded text: a T between digits (an ISO timestamp) is a boundary; a last4 also
        matches after a run of x; in a file name a date also matches as YYYYMMDD. Every step keeps positions."""
        text = ISO_T.sub(" ", folded)
        hits = self.data.find(text)
        if filename:
            for p, spans in self.compact.find(text).items():
                hits.setdefault(p, []).extend(spans)
        if MASK.search(text):
            masked = MASK.sub(lambda m: " " * len(m.group()), text)
            for p, spans in self.data.find(masked).items():
                if p[0] == "last4" and len(spans) > len(hits.get(p, ())):
                    hits[p] = spans
        return hits

    def _plain(self, folded: str, classes: frozenset, filename: bool, runs: list[str] | None = None) -> Spans:
        """The plain hits of the tokens of `classes` in one folded text"""
        hits = {p: spans for p, spans in self.everywhere.find(folded, runs).items() if p[0] in classes}
        if classes & DATA_CLASSES:
            for p, spans in self._data(folded, filename).items():
                if p[0] in classes:
                    hits.setdefault(p, []).extend(spans)
        return hits

    def _occurrences(self, r: Reading, classes: frozenset, filename: bool) -> tuple[dict, dict]:
        """(spans, extra) for one reading. spans: token -> mode -> set of (start, end), in the folded text for the
        plain, substring and hex modes and in the raw text for base64; the split view's hits join the plain ones
        through _split_map, or stay apart under "split" (in the split view's own positions) when it cannot map them.
        extra: token -> decoded mode -> (how many more plain hits that decoded text holds than the folded text, the
        decoded text, the start of its first hit)."""
        folded = r.folded
        runs = RUN.findall(folded)
        plain = self._plain(folded, classes, filename, runs)
        spans: dict = {p: {"": set(ss)} for p, ss in plain.items()}
        if r.split is not None:
            split = self._plain(r.split, classes, filename)
            to_folded = _split_map(r.raw, r.parts, folded, r.split) if split else None
            for p, ss in split.items():
                if to_folded is None:
                    spans.setdefault(p, {})["split"] = set(ss)
                else:
                    spans.setdefault(p, {}).setdefault("", set()).update(
                        (to_folded(a), to_folded(b - 1) + 1) for a, b in ss)
        found = []
        if classes & VARIANT_CLASSES:
            found += [("hex", self.hex.find(folded)), ("base64", self.b64.find(r.raw))]
        if classes & SUBSTRING_CLASSES:
            found.append(("substring", self.substrings.find(folded, runs)))
        for mode, hits in found:
            for p, ss in hits.items():
                if p[0] in classes:
                    spans.setdefault(p, {})[mode] = set(ss)
        extra: dict = {}
        for mode, _, text in r.decoded:
            for p, ss in self._plain(text, classes, filename).items():
                more = len({a for a, _ in ss}) - len({a for a, _ in plain.get(p, ())})
                if more > 0:
                    extra.setdefault(p, {})[mode] = (more, text, min(ss)[0])
        return spans, extra

    def detect(self, readings: list[Reading], classes: frozenset, filename: bool = False, lines: bool = False
               ) -> dict[tuple[str, str], dict[str, list]]:
        """(class, folded token) -> mode -> [occurrences, newlines before the first] for each token of `classes` in
        the readings of one piece; the newline count only when `lines` is set, and `filename` adds the file-name forms
        of data tokens. Each occurrence counts once, in the first mode of MODES that finds it: the plain and split
        views count their distinct starts together, a substring hit at a start they have is not counted again, and a
        decoded text counts only the plain hits it holds beyond the folded text. The readings are alternative
        decodings of the same bytes, so each mode keeps the largest count any of them gives."""
        out: dict = {}

        def put(p: tuple[str, str], mode: str, n: int, text: str, at: int) -> None:
            rec = out.setdefault(p, {}).setdefault(mode, [0, None])
            rec[0] = max(rec[0], n)
            if lines:
                nl = text.count("\n", 0, at)
                rec[1] = nl if rec[1] is None else min(rec[1], nl)
        for r in readings:
            spans, extra = self._occurrences(r, classes, filename)
            for p, modes in spans.items():
                starts = {m: sorted({a for a, _ in ss}) for m, ss in modes.items()}
                plain, split = starts.get("", []), starts.get("split", [])
                for text, at in ((r.folded, plain), (r.split, split)):
                    if at:
                        put(p, "", max(len(plain), len(split)), text, at[0])
                seen = set(plain)
                for mode, text in (("substring", r.folded), ("hex", r.folded), ("base64", r.raw)):
                    at = [a for a in starts.get(mode, ()) if mode != "substring" or a not in seen]
                    if at:
                        put(p, mode, len(at), text, at[0])
            for p, decoded in extra.items():
                for mode, (n, text, at) in decoded.items():
                    put(p, mode, n, text, at)
        return out

    def allowed(self, cls: str, tok: str, path: str, kind: str) -> bool:
        """Whether allow-paths.tsv allows a finding of `kind` for this token in `path`: a line without a kind allows
        every kind, a line with one only that kind"""
        return any((k is None or k == kind) and fnmatch.fnmatchcase(path, g)
                   for g, k in self.allow_paths.get((cls, tok), ()))

    def _shown(self, cls: str, tok: str, path: str, mode: str) -> bool:
        """Whether a match found in `mode` may print in `path`: a path component is a file name, so the match needs an
        allowance for that mode's file-name kind (or one without a kind)"""
        return self.allowed(cls, tok, path, _kind(cls, mode, True))

    def _denied(self, text: str, path: str) -> bool:
        """Whether `text`, read as a file name, holds a token that may not print in `path`"""
        return any(not self._shown(c, t, path, mode)
                   for (c, t), modes in self.detect(_readings([text]), ALL_CLASSES, filename=True).items()
                   for mode in modes)

    def _component(self, part: str, path: str) -> str:
        """One path component as printed: each stretch that matched a token replaced by that token's label, or the
        whole component by its own label when a match has no stretch here (it was found only through a percent-, \\u-
        or &#-decoded reading, so cutting other stretches could leave part of it standing), two tokens overlap, or the
        result still matches"""
        whole = f"[redacted:hmac:{_hmac12(self.key, part)}]"
        if not part.isascii():
            return whole                          # folding could move positions; an ASCII name folds to lower case
        spans, extra = self._occurrences(_readings([part])[0], ALL_CLASSES, True)
        if any(not self._shown(c, t, path, mode) for (c, t), decoded in extra.items() for mode in decoded):
            return whole
        stretches = set()
        for (c, t), modes in spans.items():
            for mode, ss in modes.items():
                if self._shown(c, t, path, "" if mode == "split" else mode):
                    continue
                if mode == "split":
                    return whole                  # positions in the split view did not map onto the name
                stretches |= {(a, b, self.label(t)) for a, b in ss}
        merged: list[list] = []
        for a, b, label in sorted(stretches):
            if merged and a < merged[-1][1]:
                if merged[-1][2] != label:
                    return whole                  # two tokens overlap: no one label covers the stretch
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b, label])
        out, pos = [], 0
        for a, b, label in merged:
            out += [part[pos:a], f"[hmac:{label}]"]
            pos = b
        shown = "".join(out) + part[pos:]
        return whole if self._denied(shown, path) else shown

    def redact(self, path: str) -> str:
        """`path` as printed: each component that holds a token not allowed for this path redacted (see _component),
        and the whole path replaced by its label when a token still shows across components"""
        if path not in self._redacted:
            shown = path
            if self._denied(path, path):
                shown = "".join(p if p in ("", "/", "!") or not self._denied(p, path) else self._component(p, path)
                                for p in re.split(r"([/!])", path))
                if self._denied(shown, path):
                    shown = f"[redacted:hmac:{_hmac12(self.key, path)}]"
            self._redacted[path] = shown
        return self._redacted[path]


# --- streams and archives -------------------------------------------------------------------------------------------

def _open(p: Path):
    return open(p, "rb")


class _Stream:
    """A forward-only reader over `f`: it can peek ahead, seeks forward by reading, keeps the last TAIL bytes it
    returned (for the tar end check), and turns any read failure into damage to the archive named `owner`."""

    def __init__(self, f, owner: str | None) -> None:
        self.f, self.owner, self.head, self.pos, self.tail = f, owner, b"", 0, b""

    def _raw(self, n: int) -> bytes:
        try:
            return self.f.read(n)
        except _Damage:
            raise
        except Exception:
            raise _Damage(self.owner) from None

    def peek(self, n: int) -> bytes:
        while len(self.head) < n:
            more = self._raw(n - len(self.head))
            if not more:
                break
            self.head += more
        return self.head[:n]

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            data, self.head = self.head + self._raw(-1), b""
        elif self.head:
            data, self.head = self.head[:n], self.head[n:]
            if len(data) < n:
                data += self._raw(n - len(data))
        else:
            data = self._raw(n)
        self.pos += len(data)
        self.tail = data[-TAIL:] if len(data) >= TAIL else (self.tail + data)[-TAIL:]
        return data

    def since(self, offset: int) -> bytes:
        """The bytes returned from `offset` on, while they are still in the tail"""
        n = self.pos - offset
        if n > len(self.tail):
            raise _Damage(self.owner)
        return self.tail[len(self.tail) - n:] if n > 0 else b""

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        target = offset if whence == 0 else self.pos + offset if whence == 1 else None
        if target is None:
            raise io.UnsupportedOperation("seek from the end")
        if target < self.pos:
            back = self.pos - target
            if back > len(self.tail):
                raise io.UnsupportedOperation("seek back beyond the kept tail")
            self.head, self.tail, self.pos = self.tail[-back:] + self.head, self.tail[:-back], target
        while self.pos < target and self.read(min(CHUNK, target - self.pos)):
            pass
        return self.pos

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True


def _cut(buf: bytes, pos: int, unit: int) -> int:
    """A cut at or before `pos`, aligned to `unit`: just after the last whitespace or NUL byte within SEARCH bytes,
    which no generic pattern spans (a multi-word token that does is whole in the overlap), else after any other
    separator byte, so that a piece's edge does not end a number or an address early"""
    window = buf[max(0, pos - SEARCH):pos][::-1]
    m = BLANK.search(window) or SEPARATOR.search(window)
    cut = pos - m.start() if m else pos
    return max(unit, cut - cut % unit)


def _pieces(f: _Stream, overlap: int, unit: int = 1) -> Iterator[tuple[bytes, int | None]]:
    """(piece, where the next piece starts in it) for the stream in pieces of at most CHUNK bytes, each overlapping
    the next by at least `overlap` bytes; the last piece has None"""
    buf = b""
    while True:
        while len(buf) <= CHUNK:
            more = f.read(CHUNK + 1 - len(buf))
            if not more:
                break
            buf += more
        if len(buf) <= CHUNK:
            if buf:
                yield buf, None
            return
        cut = _cut(buf, CHUNK, unit)
        keep = _cut(buf, cut - overlap, unit)
        yield buf[:cut], keep
        buf = buf[keep:]


def _spool(f: _Stream) -> tempfile.SpooledTemporaryFile:
    """A seekable copy of the stream: in memory up to CHUNK bytes, then in a private temporary file"""
    spool = tempfile.SpooledTemporaryFile(max_size=CHUNK)
    shutil.copyfileobj(f, spool, CHUNK)
    spool.seek(0)
    return spool


def _archive_kind(head: bytes) -> str | None:
    """The archive format by magic number; None for anything else"""
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    if head[:6] == b"\xfd7zXZ\x00" or head[:3] == b"\x5d\x00\x00":
        return "xz"
    if head[:3] == b"BZh" and len(head) > 3 and head[3:4] in b"123456789":
        return "bz2"
    if head[:4] == b"\x28\xb5\x2f\xfd":
        return "zstd"
    if head[257:262] == b"ustar":
        return "tar"
    return None


def _opens(kind: str, spool) -> bool:
    """Whether a zip or tar without its magic number at the start opens as one"""
    try:
        if kind == "zip":
            zipfile.ZipFile(spool).close()
        else:
            tarfile.open(fileobj=spool, mode="r:").close()
        return True
    except Exception:
        return False
    finally:
        spool.seek(0)


def _decompressor(kind: str, f: _Stream):
    return gzip.GzipFile(fileobj=f, mode="rb") if kind == "gzip" else _Decompressed(kind, f)


class _Decompressed:
    """The decompressed bytes of one or more concatenated xz/lzma, bz2 or zstd streams, read in bounded steps. Between
    and after streams only NUL padding may follow; anything else raises, where the standard readers would stop quietly
    at the end of the last stream and never look at what follows it."""

    def __init__(self, kind: str, f: _Stream) -> None:
        self.kind, self.f, self.out, self.done = kind, f, b"", False
        self.d = self._new()

    def _new(self):
        if self.kind == "xz":
            return lzma.LZMADecompressor()
        return bz2.BZ2Decompressor() if self.kind == "bz2" else zstd.ZstdDecompressor()

    def read(self, n: int = -1) -> bytes:
        while not self.done and (n is None or n < 0 or len(self.out) < n):
            self._step()
        if n is None or n < 0:
            data, self.out = self.out, b""
        else:
            data, self.out = self.out[:n], self.out[n:]
        return data

    def _step(self) -> None:
        if self.d.eof:
            data = self.d.unused_data.lstrip(b"\0")
            while not data:
                more = self.f.read(64 << 10)
                if not more:
                    self.done = True
                    return
                data = more.lstrip(b"\0")
            self.d = self._new()
        elif self.d.needs_input:
            data = self.f.read(64 << 10)
            if not data:
                raise EOFError("the compressed data ends inside a stream")
        else:
            data = b""
        self.out += self.d.decompress(data, CHUNK)


def _gzip_header(data: bytes) -> str:
    """The optional original file name and comment of a gzip header (ISO 8859-1)"""
    flags, pos, out = data[3], 10, []
    if flags & 4:
        pos += 2 + int.from_bytes(data[10:12], "little")
    for bit in (8, 16):
        if flags & bit:
            end = data.index(b"\0", pos)
            out.append(data[pos:end].decode("latin-1"))
            pos = end + 1
    return "\n".join(out)


def _stem(base: str) -> str:
    low = base.lower()
    for ext, repl in STEMS:
        if low.endswith(ext) and len(base) > len(ext):
            return base[:len(base) - len(ext)] + repl
    return base


def _member_dir(name: str) -> str:
    name = name.replace("\\", "/")
    while name.startswith(("./", "/")):
        name = name[2:] if name.startswith("./") else name[1:]
    return name.rstrip("/")


# --- the scan -------------------------------------------------------------------------------------------------------

# (part, class or kind, token) -> [kind, occurrences by mode, line]; part "allowed" holds the matches allow-paths.tsv
# allows, "c" a token finding (keyed by class: its kind follows from its modes), "d" a generic one (keyed by kind)
Tally = dict[tuple[str, str, str], list]


class Scanner:
    """Collects findings for one run. `deny` may be None only for part a. With `app_paths`, part d applies inside an
    image only to layer members under those prefixes (and to env, labels and history)."""

    def __init__(self, deny: Denylist | None, parts: set[str], export_names=frozenset(), app_paths=None) -> None:
        self.deny, self.parts, self.export_names = deny, set(parts), frozenset(export_names)
        self.app_paths = None if app_paths is None else tuple(_member_dir(p) for p in app_paths)
        # (part, kind, path on disk, label) -> [occurrences by mode, line of the first occurrence]. Keyed by the path
        # on disk: two files can print alike once their names are redacted.
        self.found: dict[tuple, list] = {}
        self.allowed = 0                          # matches that allow-paths.tsv allows where they are
        self.skip: set[str] = set()               # `<image>!<layer>` paths of base-image layers, not scanned
        self.layers: dict[str, str] = {}           # `<image>!<layer>!` -> `<image>!`, for layer-free acceptance

    def display(self, path: str) -> str:
        return _escape(self.deny.redact(path) if self.deny else path)

    def add(self, part: str, kind: str, path: str, token: str | None = None, counts: dict[str, int] | None = None,
            line: int | None = None) -> None:
        label = self.deny.label(token) if token is not None and self.deny else None
        rec = self.found.setdefault((part, kind, path, label), [{}, None])
        for mode, n in (counts or {"": 1}).items():
            rec[0][mode] = rec[0].get(mode, 0) + n
        if rec[1] is None:                        # pieces arrive in order, so the first line seen is the first
            rec[1] = line

    def _layer_free(self, path: str) -> str | None:
        """An image layer member's path without its layer (`IMAGE!MEMBER`), else None"""
        return next((image + path[len(prefix):] for prefix, image in self.layers.items() if path.startswith(prefix)),
                    None)

    def results(self) -> tuple[list[Finding], list[Finding]]:
        """(findings, accepted). An accepted.tsv line names a path on disk, which accepts that file's hit, or a
        printed path, which accepts it only when no other file with a finding prints the same way, or an image layer's
        member without its layer. A count can end at 0 only when a piece's shared tail was reported under another kind
        by the next piece, which counted it there."""
        shown = {path: self.display(path) for _, _, path, _ in self.found}
        owners: dict[str, set[str]] = {}
        for path, printed in shown.items():
            owners.setdefault(printed, set()).add(path)
        accepted = self.deny.accepted if self.deny else set()
        found, ok = [], []
        for (part, kind, path, label), (counts, line) in self.found.items():
            counts = {m: n for m, n in counts.items() if n > 0}
            f = Finding(part, kind, shown[path], label, sum(counts.values()), line, counts)
            hit = label is not None and ((label, path) in accepted
                                         or ((label, shown[path]) in accepted and len(owners[shown[path]]) == 1)
                                         or (label, self._layer_free(path)) in accepted)
            # a keyed label of the path on disk orders findings that print alike without revealing it
            order = (part, shown[path], kind, label or "", _hmac12(self.deny.key, path) if self.deny else path)
            (ok if hit else found).append((order, f))
        def first(item): return item[0]
        return [f for _, f in sorted(found, key=first)], [f for _, f in sorted(ok, key=first)]

    def _generic_here(self, name: str) -> bool:
        if self.app_paths is None:
            return True
        name = _member_dir(name)
        return any(name == p or name.startswith(p + "/") for p in self.app_paths)

    def scan_tree(self, tree: Path) -> list[Finding]:
        tree = Path(tree)
        if not tree.is_dir():
            raise GateError("tree is not a directory")

        def fail(err: OSError) -> None:
            where = Path(err.filename or tree).relative_to(tree).as_posix()
            raise GateError(f"cannot list {self.display(where)}")
        for root, dirs, files in os.walk(tree, onerror=fail):
            dirs.sort()
            for name in dirs + sorted(files):
                p = Path(root, name)
                rel = p.relative_to(tree).as_posix()
                self.scan_name(name, rel)
                mode = os.lstat(p).st_mode
                if stat.S_ISLNK(mode):
                    self.scan_text(os.readlink(p), rel, True)
                elif stat.S_ISREG(mode):
                    try:
                        with _open(p) as fh:
                            self.scan_stream(_Stream(fh, None), rel, 0, True, lines=True)
                    except (OSError, _Damage) as e:
                        if isinstance(e, _Damage) and e.owner is not None:
                            raise
                        raise GateError(f"cannot read {self.display(rel)}") from None
                elif not stat.S_ISDIR(mode):
                    self.add("c", "special-file", rel)
        return self.results()[0]

    def _vendor_here(self, generic: bool | None) -> bool:
        """Whether the vendor class is searched: everywhere, except outside the --app-path prefixes of an image"""
        return self.app_paths is None or generic is not False

    def scan_name(self, name: str, path: str, generic: bool | None = None) -> None:
        if "c" in self.parts:
            classes = NAME_CLASSES | {"last4", "date"}
            if not self._vendor_here(generic):
                classes -= {"vendor"}
            for (cls, tok), modes in self.deny.detect(_readings([name]), classes, filename=True).items():
                kept = {}
                for mode, (n, _) in modes.items():
                    if self.deny.allowed(cls, tok, path, _kind(cls, mode, True)):
                        self.allowed += n
                    else:
                        kept[mode] = n
                if kept:
                    self.add("c", _kind(cls, next(m for m in MODES if m in kept), True), path, tok, kept)

    def scan_text(self, raw: str, path: str, generic: bool | None) -> None:
        self.scan_texts([raw], path, generic)

    def scan_texts(self, texts: list[str], path: str, generic: bool | None) -> None:
        self._record(self._tally(_readings(texts), path, generic, None), path)

    def _tally(self, readings: list[Reading], path: str, generic: bool | None, line0: int | None) -> Tally:
        """The hits in one piece's readings; with `line0` (the newlines before the piece), each with its first line.
        A token is reported under the kind of the first of its modes that allow-paths.tsv does not allow, with its
        occurrences in each such mode."""
        out: Tally = {}
        base = _basename(path)
        if "c" in self.parts:
            data = os.path.splitext(base)[1].lower() in DATA_EXTS and base not in LOCK_FILES
            classes = NAME_CLASSES | (DATA_CLASSES if data else frozenset())
            if not self._vendor_here(generic):
                classes -= {"vendor"}
            for (cls, tok), modes in self.deny.detect(readings, classes, lines=line0 is not None).items():
                allowed = {m: rec for m, rec in modes.items() if self.deny.allowed(cls, tok, path, _kind(cls, m))}
                kept = {m: rec for m, rec in modes.items() if m not in allowed}
                if allowed:
                    out[("allowed", cls, tok)] = [None, {m: rec[0] for m, rec in allowed.items()}, None]
                if kept:
                    nls = [rec[1] for rec in kept.values() if rec[1] is not None]
                    out[("c", cls, tok)] = [_kind(cls, next(m for m in MODES if m in kept)),
                                            {m: rec[0] for m, rec in kept.items()},
                                            line0 + min(nls) + 1 if nls and line0 is not None else None]
        if "d" in self.parts and (self.app_paths is None if generic is None else generic):
            self._generic(readings, base, out, line0)
        return out

    def _record(self, tally: Tally, path: str) -> None:
        for (part, _, token), (kind, counts, line) in tally.items():
            if part == "allowed":
                self.allowed += sum(counts.values())
            else:
                self.add(part, kind, path, token, counts, line)

    def scan_stream(self, f: _Stream, path: str, depth: int, generic: bool | None, layer: bool = False,
                    lines: bool = False) -> None:
        """One file or member: opened when it is an archive, otherwise scanned in overlapping pieces. `generic` None
        means each member of the archive decides by its own name (the contents of an image layer); `lines` asks for
        line numbers (a file of the scanned tree)."""
        base = _basename(path)
        kind = _archive_kind(f.peek(HEAD))
        named = ARCHIVE_EXTS.get(os.path.splitext(base.lower())[1])
        # a zip with data before it, or an old tar, opens without the magic number, and so does an image's empty
        # layer (all zero blocks); anything else named like an archive holds no compressed data and is read as it is
        probe = named if named in ("zip", "tar") else "tar" if layer else None
        if kind is None and probe:
            with _spool(f) as spool:
                self._dispatch(probe if _opens(probe, spool) else None, _Stream(spool, f.owner), path, depth,
                               generic, layer, lines)
            return
        self._dispatch(kind, f, path, depth, generic, layer, lines)

    def _dispatch(self, kind: str | None, f: _Stream, path: str, depth: int, generic: bool | None, layer: bool,
                  lines: bool = False) -> None:
        base = _basename(path)
        if layer and kind is None:
            self.add("e", "archive-unsupported", path)
        if kind:
            self._archive(kind, f, path, depth, generic)
            return
        if "b" in self.parts and base.lower().endswith(".png"):
            with _spool(f) as spool:
                self._png(spool, path)
                spool.seek(0)
                self._content(_Stream(spool, f.owner), path, generic, lines)
            return
        self._content(f, path, generic, lines)

    def _content(self, f: _Stream, path: str, generic: bool | None, lines: bool = False) -> None:
        """Content in overlapping pieces. A match wholly inside the stretch two pieces share is read by both, so each
        piece but the last subtracts what that shared stretch alone holds: every match is counted once."""
        encoding = _bom(f.peek(4))
        unit = 4 if encoding in ("utf-32-le", "utf-32-be") else 2 if encoding else 1
        line0 = 0
        for piece, keep in _pieces(f, self.deny.overlap if self.deny else 64, unit):
            text = encoding is not None or b"\0" not in piece          # a binary has no lines
            tally = self._tally(_readings(_decode(piece, encoding)), path, generic, line0 if lines and text else None)
            if keep is not None:
                tail = self._tally(_readings(_decode(piece[keep:], encoding)), path, generic, None)
                for k, (_, counts, _) in tail.items():
                    if k in tally:                # per mode: the next piece counts these in the same modes
                        for mode, n in counts.items():
                            tally[k][1][mode] = tally[k][1].get(mode, 0) - n
                if lines:
                    head = piece[:keep]
                    line0 += head.decode(encoding, "replace").count("\n") if encoding else head.count(b"\n")
            self._record(tally, path)

    def _archive(self, kind: str, f: _Stream, path: str, depth: int, generic: bool | None, layers=None) -> None:
        if depth >= MAX_DEPTH:
            self.add("c", "archive-too-deep", path)
            return
        if kind == "zstd" and zstd is None:
            self.add("c", "archive-unsupported", path)
            return
        try:
            if kind == "zip":
                self._zip(f, path, depth, generic)
            elif kind == "tar":
                self._tar(f, path, depth, generic, layers)
            else:
                if kind == "gzip":
                    try:
                        self.scan_text(_gzip_header(f.peek(64 << 10)), path, generic)
                    except (IndexError, ValueError):
                        raise _Damage(path) from None
                payload = _Stream(_decompressor(kind, f), path)
                if payload.peek(HEAD)[257:262] == b"ustar":
                    self._tar(payload, path, depth, generic)
                else:
                    self._entry(_stem(_basename(path)), payload, "", path, depth, generic, named=False)
        except _Damage as e:
            if e.owner != path:
                raise
            self.add("c", "archive-error", path)

    def _tar(self, f: _Stream, path: str, depth: int, generic: bool | None, layers=None) -> None:
        try:
            tf = tarfile.open(fileobj=f, mode="r:")
        except _Damage:
            raise
        except Exception:
            raise _Damage(path) from None
        while True:
            try:
                m = tf.next()
                member = tf.extractfile(m) if m is not None and m.isfile() else None
            except _Damage:
                raise
            except Exception:
                raise _Damage(path) from None
            if m is None:
                break
            tf.members = []                          # a layer can hold many members; keep none
            meta = "\n".join([m.linkname, m.uname, m.gname, *(f"{k}={v}" for k, v in m.pax_headers.items())])
            self._entry(m.name, _Stream(member, path) if member else None, meta, path, depth, generic,
                        layer=layers is not None and m.name in layers)
        # tarfile stops quietly at a corrupt header after the first member, so all that follows must be padding
        if f.since(tf.offset).strip(b"\0"):
            raise _Damage(path)
        while block := f.read(CHUNK):
            if block.strip(b"\0"):
                raise _Damage(path)

    def _zip(self, f: _Stream, path: str, depth: int, generic: bool | None) -> None:
        with _spool(f) as spool:
            try:
                zf = zipfile.ZipFile(spool)
            except Exception:
                raise _Damage(path) from None
            with zf:
                self.scan_text("\n".join(_decode(zf.comment, None)), path, generic)
                for info in zf.infolist():
                    meta = "\n".join(_decode(info.comment, None) + _decode(info.extra, None))
                    if info.is_dir():
                        self._entry(info.filename, None, meta, path, depth, generic)
                        continue
                    try:
                        member = zf.open(info)
                    except Exception:
                        raise _Damage(path) from None
                    with member:
                        self._entry(info.filename, _Stream(member, path), meta, path, depth, generic)

    def _entry(self, name: str, f: _Stream | None, meta: str, apath: str, depth: int, generic: bool | None,
               layer: bool = False, named: bool = True) -> None:
        path = f"{apath}!{name}"
        if path in self.skip:
            return
        own = (self.app_paths is None) if layer else self._generic_here(name) if generic is None else generic
        if named:
            self.scan_name(name, path, own)
        # the image rules apply inside layers, not to the save archive's own manifest and config files
        if "e" in self.parts and depth >= 1:
            if name.endswith(".map"):
                self.add("e", "source-map", path)
            if name.rstrip("/").rsplit("/", 1)[-1] in self.export_names:
                self.add("e", "export-json", path)
        if meta.strip():
            self.scan_text(meta, path, own)
        if f is not None:
            self.scan_stream(f, path, depth + 1, None if layer else own, layer)

    def _png(self, f, path: str) -> None:
        if f.read(len(PNG_SIG)) != PNG_SIG:
            self.add("b", "png-malformed", path)
            return
        while True:
            header = f.read(8)
            if len(header) < 8:
                break
            ctype, left = header[4:], int.from_bytes(header[:4], "big") + 4
            while left and (got := len(f.read(min(left, CHUNK)))):
                left -= got
            if left:
                break
            if ctype in PNG_TEXT:
                self.add("b", "png-text", path)
            if ctype == b"IEND":
                if f.read(1):
                    self.add("b", "png-malformed", path)
                return
        self.add("b", "png-malformed", path)

    def _generic(self, readings: list[Reading], base: str, out: Tally, line0: int | None) -> None:
        """Part d's hits, into `out`: per text (each reading and its decoded forms) a count and the newlines before
        the first, then over those texts (other readings of the same bytes) the largest count and the earliest line"""
        best: dict[tuple[str, str], list] = {}

        def take(kind: str, value: str, text: str, at: int, seen: dict) -> None:
            rec = seen.setdefault((kind, value), [0, None])
            rec[0] += 1
            if rec[1] is None and line0 is not None:
                rec[1] = text.count("\n", 0, at)

        def merge(seen: dict) -> None:
            for key, (n, nl) in seen.items():
                rec = best.setdefault(key, [0, None])
                rec[0] = max(rec[0], n)
                rec[1] = nl if rec[1] is None or (nl is not None and nl < rec[1]) else rec[1]
        for mode, raw, folded in [("", r.raw, r.folded) for r in readings] + [d for r in readings for d in r.decoded]:
            seen: dict = {}
            for m in EMAIL.finditer(folded):
                if m.group(1) not in ALLOWED_EMAIL_DOMAINS:
                    take("email", m.group(0), folded, m.start(), seen)
            for m in IPV4.finditer(folded):
                octets = [int(g) for g in m.groups()]
                if max(octets) <= 255 and any(ipaddress.IPv4Address(".".join(map(str, octets))) in net
                                              for net in PRIVATE_NETS):
                    take("private-ip", m.group(0), folded, m.start(), seen)
            for m in HOME.finditer(folded):
                if m.group(1) not in HOME_IGNORED:
                    take("home-path", m.group(0), folded, m.start(), seen)
            if base in LOCK_FILES and not mode:
                # only the package sources: funding, homepage and bug-tracker URLs are metadata
                for m in (NPM_SOURCE if base == "package-lock.json" else UV_SOURCE).finditer(raw):
                    url = URL_HOST.match(m.group(1).strip())
                    host = fold(url.group(1)).rsplit("@", 1)[-1].split(":", 1)[0] if url else None
                    if host and host not in LOCK_HOSTS:
                        take("lock-host", host, raw, m.start(), seen)
            merge(seen)
        for (kind, value), (n, nl) in best.items():
            out[("d", kind, value)] = [kind, {"": n}, None if nl is None else line0 + nl + 1]


def scan_tree(tree: Path, deny: Denylist, parts: set[str]) -> list[Finding]:
    return Scanner(deny, parts).scan_tree(tree)


# --- part a: export -------------------------------------------------------------------------------------------------

def _manifest(doc) -> dict[str, set[str]]:
    if not isinstance(doc, dict) or set(doc) != set(MANIFEST_KEYS):
        raise GateError("manifest must be a JSON object with exactly the keys " + ", ".join(MANIFEST_KEYS))
    m = {}
    for k in MANIFEST_KEYS:
        if not isinstance(doc[k], list | tuple | set | frozenset) or not all(isinstance(x, str) and x for x in doc[k]):
            raise GateError(f"manifest {k} must be a list of non-empty strings")
        m[k] = set(doc[k])
    m["allowed_extensions"] = {e if e.startswith(".") else "." + e for e in m["allowed_extensions"]}
    m["forbidden_paths"] = {p.removeprefix("./").strip("/") for p in m["forbidden_paths"]}
    for entry in m["allowed_bare_names"]:
        parts = entry.removeprefix("/").split("/")
        deep = "**" in parts
        if not all(parts) or any("*" in p and p != "**" for p in parts) or (deep and (
                entry.startswith("/") or len(parts) < 3 or parts.count("**") > 1 or parts[-2] != "**")):
            raise GateError("manifest allowed_bare_names entries are NAME, /NAME, DIR/NAME or DIR/**/NAME")
    return m


def _bare_allowed(rel: str, entries: set[str]) -> bool:
    """Whether an entry of allowed_bare_names places the bare-named file `rel` (a path from the tree's top)"""
    name = rel.rsplit("/", 1)[-1]
    for entry in entries:
        if "/" not in entry:
            if name == entry:
                return True
        elif "/**/" in entry:
            under, last = entry.split("/**/")
            if name == last and rel.startswith(under + "/"):
                return True
        elif rel == entry.removeprefix("/"):
            return True
    return False


def load_manifest(path: Path) -> dict[str, set[str]]:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise GateError("manifest is unreadable or not JSON") from None
    return _manifest(doc)


def check_export_tree(out: Path, manifest, deny: Denylist | None = None, scanner: Scanner | None = None
                      ) -> list[Finding]:
    """The structural checks on an extracted tree; `manifest` is the parsed JSON document or load_manifest's result"""
    m = _manifest(manifest)
    sc = scanner or Scanner(deny, {"a"})
    out = Path(out)
    top = set(os.listdir(out))
    for name in sorted(top - m["top_level"]):
        sc.add("a", "top-level-extra", name)
    for name in sorted(m["top_level"] - top):
        sc.add("a", "top-level-missing", name)

    def fail(err: OSError) -> None:
        raise GateError("cannot list the exported tree")
    executables = set()
    for root, dirs, files in os.walk(out, onerror=fail):
        dirs.sort()
        for name in sorted(dirs + files):
            p = Path(root, name)
            rel, st = p.relative_to(out).as_posix(), os.lstat(p)
            if stat.S_ISDIR(st.st_mode):
                continue
            if any(rel == f or rel.startswith(f + "/") for f in m["forbidden_paths"]):
                sc.add("a", "forbidden-path", rel)
            if stat.S_ISLNK(st.st_mode):
                sc.add("a", "symlink", rel)
                continue
            if not stat.S_ISREG(st.st_mode):
                sc.add("a", "special-file", rel)
                continue
            if name == ".gitmodules":
                sc.add("a", "gitmodules", rel)
            if name == ".gitattributes" and b"export-subst" in p.read_bytes():
                sc.add("a", "export-subst", rel)
            if st.st_size > MAX_FILE:
                sc.add("a", "large-file", rel)
            ext = os.path.splitext(name)[1]
            if ext and ext not in m["allowed_extensions"]:
                sc.add("a", "extension", rel)
            if not ext and not _bare_allowed(rel, m["allowed_bare_names"]):
                sc.add("a", "bare-name", rel)
            if st.st_mode & 0o111:
                executables.add(rel)
    for rel in sorted(executables - m["executables"]):
        sc.add("a", "executable-extra", rel)
    for rel in sorted(m["executables"] - executables):
        sc.add("a", "executable-missing", rel)
    return sc.results()[0]


def _git(repo: Path, *args: str) -> bytes:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    except OSError:
        raise GateError("cannot run git") from None
    if p.returncode != 0:
        raise GateError(f"git {args[0]} failed (exit {p.returncode})")
    return p.stdout


def export(repo: Path, ref: str, out: Path, manifest_path: Path, deny: Denylist | None) -> Scanner:
    m = load_manifest(manifest_path)
    if ref.startswith("-"):
        raise GateError("the ref must not start with '-'")
    out = Path(out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise GateError("the output directory must be empty or absent")
    _git(repo, "rev-parse", "--verify", "--quiet", ref + "^{tree}")
    out.mkdir(parents=True, exist_ok=True)
    try:
        git = subprocess.Popen(["git", "-C", str(repo), "archive", "--format=tar", ref],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        tar = subprocess.Popen(["tar", "-x", "-C", str(out)], stdin=git.stdout,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        raise GateError("cannot run git or tar") from None
    git.stdout.close()
    if tar.wait() != 0 or git.wait() != 0:
        raise GateError(f"git archive | tar -x failed (exit {git.returncode}, {tar.returncode})")
    sc = Scanner(deny, {"a"})
    # export-subst expands commit metadata at archive time; an export-ignored .gitattributes still applies it
    for name in _git(repo, "ls-tree", "-r", "-z", "--name-only", ref).split(b"\0"):
        if name.rsplit(b"/", 1)[-1] == b".gitattributes":
            rel = name.decode("utf-8", "surrogateescape")
            if b"export-subst" in _git(repo, "cat-file", "blob", f"{ref}:{rel}"):
                sc.add("a", "export-subst", rel)
    info = Path(repo) / _git(repo, "rev-parse", "--git-path", "info/attributes").decode().strip()
    if info.is_file() and b"export-subst" in info.read_bytes():
        sc.add("a", "export-subst", ".git/info/attributes")
    check_export_tree(out, m, scanner=sc)
    return sc


# --- part e: images -------------------------------------------------------------------------------------------------

def _run_docker(argv: list[str]) -> bytes:
    p = subprocess.run(argv, capture_output=True)
    if p.returncode != 0:
        raise GateError(f"{argv[0]} {argv[1]} exited {p.returncode}")
    return p.stdout


def scan_images(images: list[str], deny: Denylist, export_names, run: Callable[[list[str]], bytes],
                app_paths=None, base_layers=frozenset()) -> Scanner:
    """`run(argv)` runs one docker command and returns its stdout; for `docker save -o PATH IMAGE` it must leave the
    image archive at PATH, inside a mode-700 temporary directory that is removed afterwards. A layer whose diff ID
    is in `base_layers` is not scanned (`Scanner.skip` lists them)."""
    sc = Scanner(deny, {"c", "d", "e"}, export_names, app_paths)

    def docker(*args: str) -> bytes:
        try:
            out = run(["docker", *args])
        except Exception:
            raise GateError(f"docker {args[0]} failed for image {sc.display(args[-1])}") from None
        if not isinstance(out, bytes):
            raise GateError(f"docker {args[0]} returned no bytes for image {sc.display(args[-1])}")
        return out
    work = tempfile.mkdtemp(prefix="publish-gate-")
    try:
        def inspect(image: str) -> dict:
            try:
                info = json.loads(docker("inspect", image))[0]
                if not isinstance(info["Config"], dict):
                    raise TypeError
                return info
            except (ValueError, LookupError, TypeError, AttributeError):
                raise GateError(f"docker inspect output for image {sc.display(image)} is not understood") from None
        for image in images:
            diff_ids = []
            if base_layers:
                try:
                    diff_ids = [str(d) for d in inspect(image)["RootFS"]["Layers"]]
                except (LookupError, TypeError):
                    raise GateError(f"docker inspect gives no layer diff IDs for image {sc.display(image)}") from None
            save = os.path.join(work, "image.tar")
            docker("save", "-o", save, image)
            try:
                os.chmod(save, 0o600)
                with tarfile.open(save, "r:") as tf:
                    names = set(tf.getnames())
                    doc = json.load(tf.extractfile("manifest.json"))
                order = [layer for entry in doc for layer in entry["Layers"]]
            except (OSError, ValueError, LookupError, TypeError, AttributeError, tarfile.TarError):
                raise GateError(f"the saved archive of image {sc.display(image)} has no readable manifest.json"
                                ) from None
            layers = set(order)
            if base_layers:
                if len(doc) != 1 or len(order) != len(diff_ids):
                    raise GateError(f"the layers of image {sc.display(image)} do not match its diff IDs")
                base = {layer for layer, diff in zip(order, diff_ids, strict=True) if diff in base_layers}
                sc.skip |= {f"{image}!{layer}" for layer in base}
                layers -= base
            sc.layers.update({f"{image}!{layer}!": f"{image}!" for layer in layers})
            for layer in sorted(layers - names):
                sc.add("e", "layer-missing", f"{image}!{layer}")
            with open(save, "rb") as fh:
                sc._archive("tar", _Stream(fh, image), image, 0, sc.app_paths is None, layers)
            os.unlink(save)
            try:
                config = inspect(image)["Config"]
                labels = config.get("Labels") or {}
                text = "\n".join([*(config.get("Env") or []), *(f"{k}={v}" for k, v in labels.items())])
            except (ValueError, LookupError, TypeError, AttributeError):
                raise GateError(f"docker inspect output for image {sc.display(image)} is not understood") from None
            sc.scan_text(text, f"{image}!docker-inspect", True)
            history = docker("history", "--no-trunc", image)
            sc.scan_texts(_decode(history, None), f"{image}!docker-history", True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return sc


# --- CLI ------------------------------------------------------------------------------------------------------------

def _report(sc: Scanner, json_out: str | None) -> int:
    findings, accepted = sc.results()
    for f in findings:
        print(f.render())
    for f in accepted:
        print(f"accepted {f.render()}")
    print(f"findings: {len(findings)} (accepted: {len(accepted)})")
    if json_out:
        doc = {"findings": [asdict(f) for f in findings], "accepted": [asdict(f) for f in accepted],
               "warnings": sc.deny.warnings() if sc.deny else [],
               "summary": {"findings": len(findings), "accepted": len(accepted),
                           "occurrences": sum(f.count for f in findings),
                           "tokens_by_class": sc.deny.tokens_by_class if sc.deny else {},
                           "allowed": sc.allowed, "denylist_files": sc.deny.files if sc.deny else 0}}
        Path(json_out).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return 1 if findings else 0


def _load(path: str) -> Denylist:
    deny = Denylist.load(Path(path))
    for w in deny.warnings():
        print(f"warning: {w}", file=sys.stderr)
    return deny


def _cmd_export(args, run) -> int:
    deny = _load(args.denylist)
    return _report(export(Path(args.repo), args.ref, Path(args.out), Path(args.manifest), deny), None)


def _cmd_scan(args, run) -> int:
    parts = set(args.parts.split(","))
    if not parts or not parts <= {"b", "c", "d"}:
        raise GateError("--parts takes a comma-separated subset of b, c, d")
    sc = Scanner(_load(args.denylist), parts)
    sc.scan_tree(Path(args.tree))
    return _report(sc, args.json)


def _cmd_images(args, run) -> int:
    deny = _load(args.denylist)
    try:
        names = {line.strip() for line in Path(args.export_names).read_text(encoding="utf-8").split("\n")
                 if line.strip() and not line.strip().startswith("#")}
    except (OSError, UnicodeDecodeError):
        raise GateError("cannot read the export names file") from None
    base = set()
    if args.base_layers:
        for n, line in _lines(Path(args.base_layers), "the base layers file"):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", line):
                raise GateError(f"the base layers file, line {n}: expected sha256:<64 hex digits>")
            base.add(line)
        if not base:
            raise GateError("the base layers file lists no layer")
    sc = scan_images(args.image, deny, names, run, args.app_path, frozenset(base))
    if base:
        print(f"base-image layers not scanned: {len(sc.skip)}", file=sys.stderr)
    return _report(sc, None)


def _cmd_label(args, run) -> int:
    key = _load_key(Path(args.denylist))
    for line in sys.stdin:
        if line.strip():
            print(_hmac12(key, fold(line.strip())))
    return 0


class _Parser(argparse.ArgumentParser):
    """An argument parser whose usage errors never repeat a value from the command line, which could be a token:
    only the messages that name options alone keep their text."""

    SAFE = re.compile(r"the following arguments are required: [-\w, /]+|argument [-\w/]+: expected (?:one|at least "
                      r"one) argument")

    def error(self, message: str):
        self.print_usage(sys.stderr)
        shown = message if self.SAFE.fullmatch(message) else "invalid arguments (their values are not repeated)"
        self.exit(2, f"{self.prog}: error: {shown}\n")


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="publish_gate.py", description="Prove a tree to be published holds nothing "
                                "from a denylist you supply. Exit 0 clean, 1 findings, 2 usage or error.")
    sub = p.add_subparsers(dest="command", required=True)
    e = sub.add_parser("export", help="git archive REF into an empty OUT and check its structure")
    e.add_argument("--repo", required=True)
    e.add_argument("--ref", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--manifest", required=True)
    e.add_argument("--denylist", required=True, help="the denylist, so printed paths are redacted")
    e.set_defaults(func=_cmd_export)
    s = sub.add_parser("scan", help="scan a tree for PNG text, denylist tokens and generic leaks")
    s.add_argument("--tree", required=True)
    s.add_argument("--denylist", required=True)
    s.add_argument("--parts", default="b,c,d")
    s.add_argument("--json")
    s.set_defaults(func=_cmd_scan)
    i = sub.add_parser("images", help="scan docker images")
    i.add_argument("--denylist", required=True)
    i.add_argument("--export-names", required=True)
    i.add_argument("--app-path", action="append",
                   help="limit the generic rules and the vendor class to layer members under this prefix")
    i.add_argument("--base-layers", help="a file of the base images' layer diff IDs, which are not scanned")
    i.add_argument("image", nargs="+")
    i.set_defaults(func=_cmd_images)
    lb = sub.add_parser("label", help="print the label of each token read from stdin")
    lb.add_argument("--denylist", required=True)
    lb.set_defaults(func=_cmd_label)
    return p


def _withheld(message, category, filename, lineno, file=None, line=None) -> None:
    print(f"warning: {category.__name__} (details withheld)", file=sys.stderr)


def main(argv: list[str] | None = None, run: Callable[[list[str]], bytes] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    with warnings.catch_warnings():
        warnings.showwarning = _withheld
        try:
            return args.func(args, run or _run_docker)
        except GateError as e:
            print(f"error: {e}", file=sys.stderr)
        except Exception as e:  # the message of an unexpected error can quote scanned content
            print(f"error: unexpected {type(e).__name__}; details withheld", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""What a page says to a person names no export file, no browser-storage key and no `make` or `python -m` command. A
household that installs the dashboard acts on what a page tells it in plain words; a config file or key may follow as a
hint in the code pill (`<Code>accounts.yaml</Code>`), never stand as the message, and the dashboard's own files, its
storage keys and the developer's commands are never the thing a person is told to look at or run.

How a string a person reads is told from an identifier. The scan reads the dashboard's source (`app/src`, test files and
the test support directory left out) with a small lexer that knows comments, strings, template strings, regular
expressions and JSX, and keeps:
  * JSX text: the words between a tag's `>` and the next `<` or `{`, which the page prints as they stand;
  * every string literal, template-string part and JSX attribute value that holds a space: a sentence, a label, a
    title, a tooltip. A literal with no space is an identifier: a fetch path, a gate's file list
    (`files={["accounts.json"]}`), a storage key's value, a `data-testid`, an import path.
Comments are dropped. In that text it looks for the export files (`EXPORT_FILES` in `app/src/data/types.ts`) and any
other `.json` file, the storage keys the app uses (each literal handed to a storage call, `localFlagStore` or a synced
document's `localKey`, as written there or through the constant that holds it; a key that is a plain word such as
"theme" cannot be told from prose and is left out), the words `localStorage` and `sessionStorage`, `make` followed by
one of the Makefile's targets, and `python -m`.

A config file named in such text (`accounts.yaml`, `household.yaml`, `assumptions.yaml`, any `.yaml`) must sit inside
the code pill, the hint after the plain sentence; anywhere else it is the message, which is a hit too. Config keys are
not matched: they are code-style words with no shape a scan can tell from prose, so the pill keeps them by review.

Reads files only: synthetic by construction, no data."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "src"
TEST_FILE = re.compile(r"\.test\.tsx?$")

# the tokens after which a `/` starts a regular expression and a `<` (in a .tsx file) starts a JSX element, not an operator
EXPR_START = set("(,=:[!&|?{};+-*%~^<>")
EXPR_WORDS = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw", "yield", "await",
              "instanceof", "default"}
IDENT = re.compile(r"[A-Za-z_$][\w$]*")
TAG = re.compile(r"[A-Za-z][\w.:-]*")
ATTR = re.compile(r"[A-Za-z_][\w:-]*")


@dataclass(frozen=True)
class Piece:
    """a run of text the source holds: `kind` is jsx (JSX text), attr (a JSX attribute's quoted value), str (a string
    literal) or tpl (a template string's text between interpolations); `in_code` says it sits inside a <Code> pill"""
    line: int
    kind: str
    text: str
    in_code: bool = False


class Lexer:
    """Splits a TS or TSX source into Pieces. It is a lexer, not a parser: it knows enough of the grammar to never read
    a comment, a regular expression or JSX text as code (an apostrophe in JSX text is a letter, not a quote)."""

    def __init__(self, src: str, jsx: bool) -> None:
        self.s, self.i, self.jsx = src, 0, jsx
        self.pieces: list[Piece] = []

    def line(self, at: int) -> int:
        return self.s.count("\n", 0, at) + 1

    def add(self, at: int, kind: str, text: str, in_code: bool = False) -> None:
        if text.strip():
            self.pieces.append(Piece(self.line(at), kind, text, in_code))

    def run(self) -> list[Piece]:
        self.code(until_brace=False)
        return self.pieces

    def code(self, until_brace: bool) -> None:
        s, depth, prev = self.s, 0, ""
        while self.i < len(s):
            c = s[self.i]
            if c.isspace():
                self.i += 1
            elif s.startswith("//", self.i):
                end = s.find("\n", self.i)
                self.i = len(s) if end < 0 else end
            elif s.startswith("/*", self.i):
                end = s.find("*/", self.i + 2)
                self.i = len(s) if end < 0 else end + 2
            elif c in "\"'":
                at = self.i
                self.add(at, "str", self.quoted(c, escapes=True))
                prev = "lit"
            elif c == "`":
                self.template()
                prev = "lit"
            elif c == "/" and (prev in EXPR_START or prev in EXPR_WORDS or prev == "") and self.regex():
                prev = "lit"
            elif c == "<" and self.jsx and (prev in EXPR_START or prev in EXPR_WORDS or prev == "") and self.element():
                prev = "lit"
            elif c == "{":
                depth += 1
                self.i += 1
                prev = "{"
            elif c == "}":
                self.i += 1
                if depth == 0 and until_brace:
                    return
                depth -= 1
                prev = "}"
            elif m := IDENT.match(s, self.i):
                prev = m.group(0)
                self.i = m.end()
            else:
                prev = c
                self.i += 1

    def quoted(self, q: str, escapes: bool) -> str:
        """the text of a quoted string at self.i (a JSX attribute's value has no escapes)"""
        s, out = self.s, []
        self.i += 1
        while self.i < len(s) and s[self.i] != q:
            if escapes and s[self.i] == "\\":
                out.append(s[self.i:self.i + 2])
                self.i += 2
            else:
                out.append(s[self.i])
                self.i += 1
        self.i += 1
        return "".join(out)

    def template(self) -> None:
        s, at, out = self.s, self.i, []
        self.i += 1
        while self.i < len(s):
            if s[self.i] == "\\":
                out.append(s[self.i:self.i + 2])
                self.i += 2
            elif s[self.i] == "`":
                self.i += 1
                break
            elif s.startswith("${", self.i):
                self.add(at, "tpl", "".join(out))
                out, self.i = [], self.i + 2
                self.code(until_brace=True)
                at = self.i
            else:
                out.append(s[self.i])
                self.i += 1
        self.add(at, "tpl", "".join(out))

    def regex(self) -> bool:
        """skips a regular expression at self.i; False (nothing consumed) when what follows cannot be one"""
        s, j, in_class = self.s, self.i + 1, False
        while j < len(s) and s[j] != "\n":
            if s[j] == "\\":
                j += 2
                continue
            if s[j] == "[":
                in_class = True
            elif s[j] == "]":
                in_class = False
            elif s[j] == "/" and not in_class:
                m = re.compile(r"[a-z]*").match(s, j + 1)
                self.i = m.end() if m else j + 1
                return True
            j += 1
        return False

    def element(self, in_code: bool = False) -> bool:
        """a JSX element or fragment at self.i, its children included; False (nothing consumed) for a `<` that opens a
        type parameter list (`<T,>(`) instead"""
        s, start = self.s, self.i
        self.i += 1
        tag = ""
        if s[self.i] != ">":
            m = TAG.match(s, self.i)
            if not m:
                self.i = start
                return False
            tag, self.i = m.group(0), m.end()
            if re.match(r"\s*(,|extends\b)", s[self.i:self.i + 12]):
                self.i = start
                return False
        inside = in_code or tag == "Code"
        while self.i < len(s):                                    # the attributes
            c = s[self.i]
            if c.isspace():
                self.i += 1
            elif s.startswith("/>", self.i):
                self.i += 2
                return True
            elif c == ">":
                self.i += 1
                break
            elif c == "{":
                self.i += 1
                self.code(until_brace=True)
            elif m := ATTR.match(s, self.i):
                name, self.i = m.group(0), m.end()
                while self.i < len(s) and s[self.i].isspace():
                    self.i += 1
                if s[self.i] == "=":
                    self.i += 1
                    while s[self.i].isspace():
                        self.i += 1
                    if s[self.i] in "\"'":
                        at = self.i
                        value = self.quoted(s[self.i], escapes=False)
                        if name not in ("className", "data-testid", "key", "id", "to", "href"):
                            self.add(at, "attr", value, inside)
                    elif s[self.i] == "{":
                        self.i += 1
                        self.code(until_brace=True)
            else:
                self.i += 1
        while self.i < len(s):                                    # the children
            stop = min((p for p in (s.find("<", self.i), s.find("{", self.i)) if p >= 0), default=len(s))
            self.add(self.i, "jsx", s[self.i:stop], inside)
            self.i = stop
            if self.i >= len(s):
                break
            if s[self.i] == "{":
                self.i += 1
                self.code(until_brace=True)
            elif s.startswith("</", self.i):
                end = s.find(">", self.i)
                self.i = len(s) if end < 0 else end + 1
                return True
            else:
                self.element(inside)
        return True


def pieces(src: str, jsx: bool) -> list[Piece]:
    return Lexer(src, jsx).run()


def readable(p: Piece) -> bool:
    """text a person may read: JSX text, or a literal that holds a space (a literal with none is an identifier)"""
    return p.kind == "jsx" or bool(re.search(r"\s", p.text.strip()))


def app_sources() -> list[Path]:
    return sorted(p for p in APP.rglob("*") if p.is_file() and p.suffix in (".ts", ".tsx")
                  and not TEST_FILE.search(p.name) and "test" not in p.relative_to(APP).parts[:1])


def export_files() -> list[str]:
    block = re.search(r"EXPORT_FILES = \[(.*?)\]", (APP / "data" / "types.ts").read_text(encoding="utf-8"), re.S)
    assert block, "app/src/data/types.ts declares EXPORT_FILES"
    return re.findall(r'"([^"]+\.json)"', block.group(1))


def storage_keys(sources: list[Path]) -> set[str]:
    """every key the app hands browser storage, read off its storage calls (a literal, or a constant holding one; a key
    named through lib/demo.ts's storageKey is read as the key it wraps)"""
    consts: dict[str, str] = {}
    used: set[str] = set()
    for p in sources:
        text = re.sub(r"\bstorageKey\(([^()]*)\)", r"\1", p.read_text(encoding="utf-8"))
        consts.update(re.findall(r'\bconst ([A-Z][A-Z0-9_]*) = "([^"]+)"', text))
        used.update(re.findall(r'(?:Storage\.(?:get|set|remove)Item|localFlagStore(?:<[^>(]*>)?)\(\s*("[^"]+"|[A-Z][A-Z0-9_]*)',
                               text))
        used.update(re.findall(r"\blocalKey:\s*([A-Z][A-Z0-9_]*)", text))
    keys = {u.strip('"') if u.startswith('"') else consts.get(u, "") for u in used}
    return {k for k in keys if k}


def make_targets() -> list[str]:
    return re.findall(r"^([a-z][\w-]*):", (ROOT / "Makefile").read_text(encoding="utf-8"), re.M)


def forbidden(sources: list[Path]) -> re.Pattern[str]:
    keys = sorted(k for k in storage_keys(sources) if re.search(r"[._-]", k))      # a plain word is prose's too
    words = [r"\b[\w-]+\.json\b", *(re.escape(k) for k in keys), r"\b(?:local|session)Storage\b",
             r"\bmake (?:" + "|".join(map(re.escape, make_targets())) + r")\b", r"\bpython3? -m\b"]
    return re.compile("|".join(f"(?:{w})" for w in words))


CONFIG_FILE = re.compile(r"\b[\w/-]+\.ya?ml\b")


def hits_in(src: str, jsx: bool, bad: re.Pattern[str]) -> list[tuple[int, str]]:
    """(line, what) for each readable piece that names a file, a key or a command, or names a config file outside the
    code pill"""
    out = []
    for p in pieces(src, jsx):
        if not readable(p):
            continue
        for m in bad.finditer(p.text):
            out.append((p.line, m.group(0)))
        if not p.in_code:
            for m in CONFIG_FILE.finditer(p.text):
                out.append((p.line, f"{m.group(0)} outside the code pill"))
    return out


def test_the_lexer_reads_jsx_text_strings_and_templates_and_skips_comments_and_regexes():
    src = """
      // make ingest in a comment
      /* accounts.json in a block comment */
      const re = /make refresh/g;
      const k = "accounts.json";
      const t = `run ${cmd} then make ingest`;
      export const X = () => <p title="see vendors.json">Don't run make fetch <Code>accounts.yaml</Code>{" "}it's {n}</p>;
      const f = <T,>(v: T): T => v;
      const s = 'one ' + "two";
    """
    got = [(p.kind, p.text.strip(), p.in_code) for p in pieces(src, jsx=True)]
    assert ("str", "accounts.json", False) in got
    assert ("tpl", "then make ingest", False) in got
    assert ("attr", "see vendors.json", False) in got
    assert ("jsx", "Don't run make fetch", False) in got
    assert ("jsx", "accounts.yaml", True) in got
    assert ("jsx", "it's", False) in got
    assert ("str", "one", False) in got and ("str", "two", False) in got
    assert not any("comment" in t or "refresh" in t for _, t, _ in got)


def test_the_scan_flags_files_keys_and_commands_and_leaves_hints_and_identifiers():
    bad = forbidden(app_sources())
    flagged = [
        '<p>The account list (accounts.json) did not load.</p>',
        '<p title="run make refresh to build it">x</p>',
        "const s = `Then make ingest and reload`;",
        '<li>saved in this browser only (localStorage <Code>efund-overrides</Code>)</li>',
        '<p>Run <Code>python -m etl validate</Code>.</p>',
        '<p>Set in config/vendor_rules.yaml (the <Code>split:</Code> key).</p>',
    ]
    for src in flagged:
        assert hits_in(src, True, bad), src
    quiet = [
        '<RequireData files={["accounts.json"]} what="The account list">x</RequireData>',
        'const KEY = "ff.privacy"; localStorage.getItem(KEY);',
        '<p>Mark the loan (<Code>mortgage: true</Code> in <Code>accounts.yaml</Code>), then re-run the ingest.</p>',
        "<p>Delete a saved view to make room; the rest make up the total.</p>",
        '<p data-testid="missing-data" className="text-xs">Nothing here.</p>',
    ]
    for src in quiet:
        assert hits_in(src, True, bad) == [], src


def test_the_scan_reads_a_real_corpus():
    sources = app_sources()
    names = {str(p.relative_to(APP)) for p in sources}
    assert len(sources) > 150
    assert {"App.tsx", "sections/Data.tsx", "sections/Balances.tsx", "components/RequireData.tsx"} <= names
    assert not any(n.startswith("test/") or ".test." in n for n in names)
    assert {"accounts.json", "transactions.json", "vendors.json"} <= set(export_files())
    assert {"ff.privacy", "efund-overrides", "ff.prefs.v1", "theme"} <= storage_keys(sources)
    assert {"ingest", "fetch", "refresh", "simplefin-claim", "backfill"} <= set(make_targets())
    readable_pieces = [p for s in sources for p in pieces(s.read_text(encoding="utf-8"), s.suffix == ".tsx") if readable(p)]
    assert sum(p.kind == "jsx" for p in readable_pieces) > 500 and sum(p.kind != "jsx" for p in readable_pieces) > 500


def check_details(src: str) -> list[tuple[int, str]]:
    """(line, text) of each string literal in a quality check's `detail=` expression: the words the Data page prints as
    the ETL wrote them"""
    out = []
    for m in re.finditer(r"\bdetail=", src):
        depth, i = 0, m.end()
        while i < len(src):                                  # the expression ends at the call's closing bracket or a comma
            c = src[i]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif c == "," and depth == 0:
                break
            elif c in "\"'":
                end = src.find(c, i + 1)
                out.append((src.count("\n", 0, i) + 1, src[i + 1:end]))
                i = end
            i += 1
    return out


def test_no_quality_check_detail_names_a_command_or_a_dashboard_file():
    bad = re.compile(r"\bmake (?:" + "|".join(map(re.escape, make_targets())) + r")\b|\bpython3? -m\b|`make\b"
                     r"|\b[\w-]+\.json\b|\bconfig/")
    files = sorted((ROOT / "etl").rglob("*.py"))
    assert sum(len(check_details(p.read_text(encoding="utf-8"))) for p in files) > 50
    assert check_details('x(detail=f"a {b} c " "d (e.yaml)", status="warn")') == [(1, "a {b} c "), (1, "d (e.yaml)")]
    found = [f"{p.relative_to(ROOT)}:{n}: {m.group(0)}" for p in files for n, text in check_details(p.read_text(encoding="utf-8"))
             for m in bad.finditer(text)]
    assert found == [], "a check's detail says what to do in words, a config key as a hint: " + "; ".join(found)


def test_no_page_names_a_file_a_storage_key_or_a_make_command():
    sources = app_sources()
    bad = forbidden(sources)
    found = [f"{p.relative_to(ROOT)}:{line}: {what}" for p in sources
             for line, what in hits_in(p.read_text(encoding="utf-8"), p.suffix == ".tsx", bad)]
    assert found == [], ("say what is missing and what to do in plain words, with a config file or key only as a hint "
                         "in <Code>: " + "; ".join(found))

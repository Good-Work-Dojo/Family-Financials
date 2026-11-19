#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The licence header every shippable source file carries: two comment lines in the file's own comment syntax,

    SPDX-License-Identifier: AGPL-3.0-only
    Additional terms: see NOTICE

placed where the file's format allows a comment without changing what it does: after a shebang (`#!`, and a Python
encoding line after it), after a Dockerfile's parser directives, after an HTML page's `<!doctype html>`, and
otherwise first (a comment above a Python module's docstring leaves the docstring the module's first statement).

Shippable sources are the files under app/ (outside node_modules and dist), deploy/, etl/, examples/, starter/, tests/,
tools/ (outside tools/private) and .github/, and ff, Makefile and compose.yaml at the top, of the kinds below
(STYLES). Data files carry none, LICENSE at the top covering them: JSON, CSV, OFX and QFX statements, images, the
web manifest, lockfiles and hash lists, Markdown, TOML and dotfiles, and the YAML fixtures under tests/.

Usage: tools/verify/spdx.py [--write] [ROOT]   ROOT defaults to the repository holding this script. Without --write it
lists each source that lacks the header (exit 1 when any does); with --write it adds the header to each, leaving every
other byte as it was. Reads and writes files only."""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LICENSE_ID = "SPDX-License-Identifier: AGPL-3.0-only"
TERMS = "Additional terms: see NOTICE"

SOURCE_DIRS = ("app", "deploy", "etl", "examples", "starter", "tests", "tools", ".github")
TOP_FILES = ("ff", "Makefile", "compose.yaml")
SKIP_PARTS = {"node_modules", "dist", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv"}
SKIP_PREFIXES = ("tools/private/", "tests/golden/", "tests/importers/synthetic/")


@dataclass(frozen=True)
class Style:
    """how a kind of file writes a comment line, and which leading lines must stay first"""
    open: str
    close: str = ""
    keep_first: re.Pattern[str] | None = None   # leading lines that stay above the header, while they match
    keep_most: int = 1                          # and at most this many of them

    def lines(self) -> list[str]:
        return [f"{self.open}{text}{self.close}" for text in (LICENSE_ID, TERMS)]


SHEBANG = re.compile(r"#!.*")
HASH = Style("# ", keep_first=SHEBANG)
PYTHON = Style("# ", keep_first=re.compile(r"#!.*|#.*coding[:=].*"), keep_most=2)
SLASHES = Style("// ", keep_first=SHEBANG)
DOCKERFILE = Style("# ", keep_first=re.compile(r"#\s*(syntax|escape|check)\s*=.*", re.I), keep_most=3)
CSS = Style("/* ", " */")
HTML = Style("<!-- ", " -->", keep_first=re.compile(r"<!doctype html>", re.I))

BY_SUFFIX = {".py": PYTHON, ".sh": HASH, ".yaml": HASH, ".yml": HASH, ".ts": SLASHES, ".tsx": SLASHES,
             ".js": SLASHES, ".css": CSS, ".html": HTML}
BY_NAME = {"ff": HASH, "Makefile": HASH, "Caddyfile": HASH}


def style_of(rel: str) -> Style | None:
    """the comment style of the file at `rel` (a path relative to the root), None for a file that takes no header"""
    name = rel.rsplit("/", 1)[-1]
    if name.startswith("Dockerfile"):
        return DOCKERFILE
    if name in BY_NAME:
        return BY_NAME[name]
    if name.startswith("."):
        return None
    return BY_SUFFIX.get(Path(name).suffix)


def sources(root: Path = ROOT) -> list[Path]:
    """every shippable source file under `root` that takes the header, in path order"""
    found = [root / name for name in TOP_FILES if (root / name).is_file()]
    for top in SOURCE_DIRS:
        base = root / top
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            rel = p.relative_to(root).as_posix()
            if p.is_file() and not SKIP_PARTS & set(p.relative_to(root).parts) \
                    and not rel.startswith(SKIP_PREFIXES) and style_of(rel) is not None:
                found.append(p)
    return sorted(found)


def split(text: str, style: Style) -> tuple[list[str], list[str]]:
    """the lines that stay above the header, and the rest"""
    lines = text.splitlines(keepends=True)
    n = 0
    while style.keep_first and n < min(len(lines), style.keep_most) \
            and style.keep_first.fullmatch(lines[n].rstrip("\r\n")):
        n += 1
    return lines[:n], lines[n:]


def read(p: Path) -> str:
    """a file's text with its line endings as they are"""
    return p.read_bytes().decode("utf-8")


def has_header(text: str, style: Style) -> bool:
    """the header sits in its place: after what must stay first, and above anything that must (a shebang or a doctype
    below it no longer works)"""
    _, rest = split(text, style)
    if [line.rstrip("\r\n") for line in rest[:2]] != style.lines():
        return False
    after = rest[2].rstrip("\r\n") if len(rest) > 2 else ""
    return not (style.keep_first and style.keep_first.fullmatch(after))


def with_header(text: str, style: Style) -> str:
    """`text` with the header in its place (unchanged when it is there already)"""
    if has_header(text, style):
        return text
    head, rest = split(text, style)
    if head and not head[-1].endswith("\n"):
        head[-1] += "\n"
    return "".join(head) + "".join(f"{line}\n" for line in style.lines()) + "".join(rest)


def missing(root: Path = ROOT) -> list[str]:
    """the shippable sources under `root` without the header, as paths relative to it"""
    out = []
    for p in sources(root):
        rel = p.relative_to(root).as_posix()
        if not has_header(read(p), style_of(rel)):
            out.append(rel)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check or add the licence header of every shippable source file.")
    ap.add_argument("--write", action="store_true", help="add the header where it is missing")
    ap.add_argument("root", nargs="?", type=Path, default=ROOT)
    args = ap.parse_args(argv)
    lacking = missing(args.root)
    if args.write:
        for rel in lacking:
            p = args.root / rel
            mode = p.stat().st_mode
            p.write_bytes(with_header(read(p), style_of(rel)).encode("utf-8"))
            p.chmod(mode)
        print(f"spdx: added the header to {len(lacking)} file(s)")
        return 0
    for rel in lacking:
        print(f"{rel}: no licence header ({LICENSE_ID}, {TERMS})")
    print(f"spdx: {len(sources(args.root)) - len(lacking)} of {len(sources(args.root))} sources carry the header")
    return 1 if lacking else 0


if __name__ == "__main__":
    sys.exit(main())

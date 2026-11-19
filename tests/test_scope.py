# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The scope scanner: `scan(root, banned, allowed)` lists every line of the tree on which a token of `banned` appears
in the code, tests, starter and example files, `pyproject.toml`, the context files or `docs/design/`. A list of
tokens is its caller's: a maintainer keeps one with their own tooling (identifiers a tree must not carry) and
scans with it. This file holds the scanner and what holds for any list: every root it scans exists, the scan reads the
real tree, and it never opens a secret.

A token matches case-insensitively and only with a non-alphanumeric character or the edge of the text on both sides,
so `_`, `-`, `.` and `/` are boundaries: `widget` matches `widget_key` and `etl/widget.py`, not `widgets`. A hit is
suppressed only where `allowed` lists that token for that file's path (repo-relative globs).

Stdlib only, so it runs two ways: under pytest in the ETL test image, which holds every root it scans, and on the
host as `python3 tests/test_scope.py`, which `tools/verify/ci.sh` runs before any image is built: it fails when a
root is missing and prints how many files the scan reads. Reads files only; no data."""
from __future__ import annotations

import fnmatch
import functools
import os
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = "tests/test_scope.py"

# pyproject.toml is scanned so a removed dependency cannot come back; uv.lock is not (it is derived from it). A
# household's config/ is its own, outside the tree, and is not scanned.
SCAN_ROOTS = ("etl", "app/src", "deploy", "tools/verify", "tests", "starter", "examples", "Makefile",
              "compose.yaml", "pyproject.toml", "README.md", "AGENTS.md", "app/AGENTS.md", "docs/design")
# Never opened: dependency and cache folders, and anything that holds secrets (a `.env` file, a credentials folder).
SKIP_DIRS = {"node_modules", "__pycache__", "credentials", "secrets"}


def holds_secrets(name: str) -> bool:
    return name == ".env" or name.startswith(".env.")

_EDGE = r"[^\W_]"                    # one alphanumeric character


@functools.lru_cache(maxsize=None)
def _patterns(banned: tuple[str, ...]) -> dict[str, re.Pattern[str]]:
    return {t: re.compile(rf"(?<!{_EDGE}){re.escape(t)}(?!{_EDGE})", re.IGNORECASE) for t in banned}


def hits_in(text: str, banned: Sequence[str]) -> list[tuple[int, str]]:
    """(1-based line, token) for each line a banned token appears on, in line order then the list's order"""
    banned = tuple(banned)
    folded = text.casefold()
    found = set()
    for token, pattern in _patterns(banned).items():
        if token.casefold() not in folded:
            continue
        for m in pattern.finditer(text):
            found.add((text.count("\n", 0, m.start()) + 1, token))
    order = {t: i for i, t in enumerate(banned)}
    return sorted(found, key=lambda hit: (hit[0], order[hit[1]]))


def is_allowed(token: str, rel: str, allowed: Mapping[str, Sequence[str]]) -> bool:
    return any(fnmatch.fnmatchcase(rel, glob) for glob in allowed.get(token, ()))


def scanned_files(root: Path) -> Iterator[tuple[str, str]]:
    """(repo-relative path, text) of every UTF-8 file under the roots that exist, this file excepted"""
    for name in SCAN_ROOTS:
        top = root / name
        if top.is_file():
            paths = [top]
        elif top.is_dir():
            paths = []
            for d, dirs, files in os.walk(top):
                dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
                paths.extend(Path(d) / f for f in sorted(files) if not holds_secrets(f))
        else:
            continue
        for p in paths:
            rel = p.relative_to(root).as_posix()
            if rel == SELF:
                continue
            try:
                yield rel, p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue


def scan(root: Path, banned: Sequence[str], allowed: Mapping[str, Sequence[str]] | None = None) -> list[str]:
    """"<path>:<line>: <token>" for every banned token outside its allowances"""
    allowed = allowed or {}
    out = []
    for rel, text in scanned_files(root):
        out.extend(f"{rel}:{n}: {token}" for n, token in hits_in(text, banned) if not is_allowed(token, rel, allowed))
    return out


def missing_roots(root: Path) -> list[str]:
    return [name for name in SCAN_ROOTS if not (root / name).exists()]


def main() -> int:
    missing = missing_roots(ROOT)
    if missing:
        print(f"scope: missing scan roots under {ROOT}: {', '.join(missing)}", file=sys.stderr)
        return 2
    files = sum(1 for _ in scanned_files(ROOT))
    print(f"scope: {len(SCAN_ROOTS)} roots, {files} files scanned")
    return 0


SAMPLE = ("widget", "old_feature", "card-old-panel", "OldThing", "docs/drafts", "legacy.yml", "pages/retired")


def test_matches_need_non_alphanumeric_boundaries():
    for text, token in (("from .parsers import widget", "widget"), ("x = WIDGET_KEY", "widget"),
                        ("old_feature:", "old_feature"), ('"card-old-panel"', "card-old-panel"),
                        (".github/workflows/legacy.yml", "legacy.yml"), ("see docs/drafts/plans", "docs/drafts"),
                        ("route /pages/retired?x=1", "pages/retired"), ("#/pages/retired", "pages/retired")):
        assert hits_in(text, SAMPLE) == [(1, token)], text
    for text in ("widgets", "Old Feature", "card_old_panel", "legacy_yml", "legacyxyml", "docs/draftsx",
                 "pages-retired", "my_pages/retireds"):
        assert hits_in(text, SAMPLE) == [], text
    assert hits_in("type OldThing = {}", SAMPLE) == [(1, "OldThing")]
    assert hits_in("one\ntwo widget\nthree WIDGET", SAMPLE) == [(2, "widget"), (3, "widget")]
    assert hits_in("widget old_feature", ("old_feature", "widget")) == [(1, "old_feature"), (1, "widget")]


def test_allowances_are_path_scoped(tmp_path):
    for rel in ("starter/transfer_rules.yaml", "etl/x.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("  - pattern: WIDGET\n", encoding="utf-8")
    allowed = {"widget": ("starter/transfer_rules.yaml",)}
    assert scan(tmp_path, SAMPLE, allowed) == ["etl/x.py:1: widget"]
    assert scan(tmp_path, SAMPLE) == ["etl/x.py:1: widget", "starter/transfer_rules.yaml:1: widget"]
    assert not is_allowed("old_feature", "starter/transfer_rules.yaml", allowed)   # an allowance covers its own token


def test_the_scan_reads_a_real_corpus():
    rels = [rel for rel, _ in scanned_files(ROOT)]
    assert len(rels) >= 300
    assert {"etl/pipeline.py", "app/src/App.tsx", "starter/vendor_rules.yaml"} <= set(rels)
    assert SELF not in rels


def test_secret_files_are_never_opened(tmp_path):
    for rel in ("deploy/.env", "deploy/.env.local", "deploy/credentials/token", "deploy/secrets/key", "etl/x.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("widget\n", encoding="utf-8")
    assert [rel for rel, _ in scanned_files(tmp_path)] == ["etl/x.py"]
    assert scan(tmp_path, SAMPLE) == ["etl/x.py:1: widget"]


def test_every_root_is_there():
    assert missing_roots(ROOT) == []


if __name__ == "__main__":
    sys.exit(main())

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The documents a newcomer and a contributor read point at what exists: every relative link and picture of the README,
the policy files, docs/ and the folder READMEs names a file of the tree, every `#anchor` of the README one of its
headings, and every `./ff` command the README shows is one `ff` takes; the README fetches `ff` and the example answers
file at this release's tag and names this release's images; and the heading the dashboard's balances page quotes is the README's.

The documents are in the ETL test image (.dockerignore lets the top-level Markdown files and docs/ in), so every pytest
run, the gate's included, runs these; a missing document FAILS."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from etl import install

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
# the shipped Markdown a reader follows links in: the top level, docs/, the folder READMEs and the context files
TOP_LEVEL = ("README.md", "AGENTS.md", "CHANGELOG.md", "CLA.md", "CODE_OF_CONDUCT.md", "CONTRIBUTING.md",
             "SECURITY.md", "THIRD_PARTY_NOTICES.md", "TRADEMARKS.md")
FOLDER_READMES = ("app/AGENTS.md", "deploy/README.md", "starter/README.md", "examples/minimal/README.md",
                  "tools/demo/README.md", "tools/maintainer/README.md", "tools/verify/README.md")


def documents() -> list[Path]:
    docs = [p for p in (ROOT / "docs").rglob("*.md") if "dev" not in p.relative_to(ROOT / "docs").parts]
    return sorted([ROOT / n for n in (*TOP_LEVEL, *FOLDER_READMES)] + docs)


_LINK = re.compile(r"\]\(([^)\s]+)\)|<img [^>]*src=\"([^\"]+)\"")
_FENCE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)


def links(text: str) -> list[str]:
    """The targets of a document's Markdown links and images and its <img> tags, outside fenced code."""
    return [a or b for a, b in _LINK.findall(_FENCE.sub("", text))]


def relative(target: str) -> bool:
    return not re.match(r"^[a-z][a-z0-9+.-]*:", target) and not target.startswith("#")


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation but `-` and `_` dropped, spaces as `-`."""
    text = re.sub(r"`", "", heading.strip().lower())
    return re.sub(r" ", "-", re.sub(r"[^\w\- ]", "", text))


def headings(text: str) -> list[str]:
    return [m[1] for m in re.finditer(r"^#{1,6} (.+?)\s*$", _FENCE.sub("", text), re.M)]


def test_every_document_is_there():
    assert [p for p in documents() if not p.is_file()] == []
    assert len(documents()) > len(TOP_LEVEL) + len(FOLDER_READMES)          # docs/ itself was read


@pytest.mark.parametrize("doc", documents(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_every_relative_link_names_a_file_of_the_tree(doc):
    broken = [t for t in links(doc.read_text("utf-8")) if relative(t)
              and not (doc.parent / t.split("#", 1)[0]).resolve().exists()]
    assert broken == [], f"{doc.relative_to(ROOT)}: links to nothing"


def test_every_anchor_of_the_readme_names_one_of_its_headings():
    text = README.read_text("utf-8")
    anchors = {t[1:] for t in links(text) if t.startswith("#")}
    assert anchors, "the README's contents list links its sections"
    assert sorted(anchors - {slug(h) for h in headings(text)}) == []


def test_the_link_checks_see_a_broken_link_and_a_broken_anchor(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text("# A heading, here\n[ok](doc.md) [gone](missing.md#x) [web](https://example.net) "
                   "<img src=\"gone.png\" alt=\"\">\n```\n[code](not-a-link.md)\n```\n[a](#a-heading-here) [b](#nowhere)\n")
    text = doc.read_text()
    assert [t for t in links(text) if relative(t) and not (tmp_path / t.split("#")[0]).exists()] == ["missing.md#x",
                                                                                                     "gone.png"]
    assert {t[1:] for t in links(text) if t.startswith("#")} - {slug(h) for h in headings(text)} == {"nowhere"}


def ff_commands() -> set[str]:
    """The commands ff's dispatch takes (its case labels)."""
    text = (ROOT / "ff").read_text("utf-8")
    body = text[text.index('case "$CMD" in\n  init)'):]
    return {c.strip() for line in re.findall(r"^  ([a-z |-]+)\)", body, re.M) for c in line.split("|")}


def test_every_ff_command_the_readme_shows_is_one_ff_takes():
    shown = set(re.findall(r"\./ff ([a-z-]+)", README.read_text("utf-8")))
    assert {"demo", "up", "init", "validate", "build", "backup", "restore", "upgrade"} <= shown
    assert sorted(shown - ff_commands()) == []


def test_the_readme_fetches_ff_and_the_answers_file_at_this_releases_tag_and_names_its_images():
    text = README.read_text("utf-8")
    assert f"/v{install.VERSION}/ff" in text
    assert f"ghcr.io/good-work-dojo/family-financials-etl:{install.VERSION}" in text
    # every file the README downloads from the repository comes from this release's tag, and exists in the tree
    raw = "https://raw.githubusercontent.com/Good-Work-Dojo/Family-Financials/"
    fetched = re.findall(re.escape(raw) + r"(\S+?)/(\S+)", text)
    assert {path for _, path in fetched} >= {"ff", "examples/answers.yaml"}
    assert sorted({tag for tag, _ in fetched}) == [f"v{install.VERSION}"]
    assert [path for _, path in fetched if not (ROOT / path).is_file()] == []


def test_the_heading_the_balances_page_quotes_is_the_readmes():
    page = (ROOT / "app" / "src" / "sections" / "Balances.tsx").read_text("utf-8")
    quoted = re.findall(r"in the README, under “([^”]+)”", page)
    assert len(quoted) == 1
    assert quoted[0] in headings(README.read_text("utf-8"))


def test_the_first_run_page_names_documents_that_exist():
    page = (ROOT / "app" / "src" / "sections" / "FirstRun.tsx").read_text("utf-8")
    named = re.findall(r"<Code>([\w./-]+\.md)</Code>", page)
    assert named and [n for n in named if not (ROOT / n).is_file()] == []

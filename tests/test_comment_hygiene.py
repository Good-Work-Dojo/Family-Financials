# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Source comments say what the code does and why, in the present tense. They do not say which phase,
task, decision id or review round put it there: that history lives in git, and an id like
"D8" points at a document the code outlives. A comment that needs a reason gives the reason.

Scans the non-test source of the ETL, the deploy scripts, the starter rule files, the shipped examples, the dashboard,
the demo household's generator, the verification harness, the maintainer's scripts (tools/maintainer, when the tree
holds it) and the GitHub settings (the workflows, Dependabot's settings, the issue forms). Test files are left out, except that the dashboard's
test titles (the first argument of `it(`, `test(` and `describe(`) are read too: a title says what the test checks.
The schema-version changelogs are held to the same rule. Reads files only: synthetic by construction, no data."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN = ("etl", "deploy", "starter", "examples", "app/src", "tools/demo", "tools/verify", "tools/maintainer", ".github")
SUFFIXES = {".py", ".ts", ".tsx", ".yaml", ".yml", ".sh"}
TEST_FILE = re.compile(r"(\.test\.tsx?|^test_.*\.py|^conftest\.py)$")

NARRATION = re.compile(r"Phase[- ]?[0-9]|Task [0-9]|fix wave|fix round|Review Focus|\bD[0-9]{1,2}\b|\bRF #|\bPR-[0-9]|amendment")
CHANGELOGS = {"etl/exports.py", "app/src/data/types.ts"}
# the word the samples below put before a number, kept apart so that no line of this file reads as such a comment
PHASE = "Phase"


def source_files(root: Path = ROOT) -> list[Path]:
    out = []
    for top in SCAN:
        for p in sorted((root / top).rglob("*")):
            if p.is_file() and p.suffix in SUFFIXES and not TEST_FILE.search(p.name) and "node_modules" not in p.parts:
                out.append(p)
    return out


def violations(text: str) -> list[int]:
    """1-based numbers of the lines that narrate the program's history"""
    return [n for n, line in enumerate(text.splitlines(), 1) if NARRATION.search(line)]


def test_the_scan_reads_a_real_corpus():
    files = source_files()
    assert len(files) > 150
    assert {"etl/pipeline.py", "app/src/App.tsx", "starter/vendor_rules.yaml", "deploy/export_diff.py",
            "examples/minimal/config/accounts.yaml", "tools/demo/generate.py", "tools/demo/behaviours/review.py",
            "tools/verify/ci.sh", "tools/verify/suite_summary.py", ".github/workflows/release.yml",
            ".github/dependabot.yml", ".github/ISSUE_TEMPLATE/bank-format.yml"} <= {str(p.relative_to(ROOT)) for p in files}
    assert CHANGELOGS <= {str(p.relative_to(ROOT)) for p in files}         # the schema changelogs are read like any source


def test_the_maintainers_scripts_are_read_when_the_tree_holds_them_and_their_absence_is_fine(tmp_path):
    assert source_files(tmp_path) == []
    script = tmp_path / "tools" / "maintainer" / "release.sh"
    script.parent.mkdir(parents=True)
    script.write_text(f"#!/bin/sh\n# {PHASE} 9 publishes\n", encoding="utf-8")
    assert source_files(tmp_path) == [script]
    assert violations(script.read_text(encoding="utf-8")) == [2]


def test_the_pattern_flags_history_and_leaves_ordinary_prose():
    for s in (f"# {PHASE} 4 adds this", "// Task 6's partition", "# (D8) splits", "# D12 wiring", "# fix wave 2",
              "# the fix round", "# RF #7", "// (PR-45)", "# amendment C4", f"# the {PHASE}-2 learner", "# Review Focus #3"):
        assert violations(s), s
    for s in ("# a phase of the moon", "# the task at hand", "# SD8 card", "# ID8", "# the fix is round", "# an amend", "# D"):
        assert not violations(s), s
    assert violations(f"#   5 — 2026-10-03: assumptions.json category_renames (program {PHASE} 4)") == [1]
    assert violations(f" *  3 = vendors.json ({PHASE} 1); 4 = recurring.json schedule keys") == [1]


def test_no_source_comment_narrates_the_program():
    found = []
    for p in source_files():
        rel = str(p.relative_to(ROOT))
        for n in violations(p.read_text(encoding="utf-8")):
            found.append(f"{rel}:{n}")
    assert found == [], "state the reason in plain words instead of the phase / task / decision id: " + ", ".join(found)


# A dashboard test's title says what the test checks, the way a comment says what the code does: no phase, task,
# decision or review id, and no review item's id in parentheses ("(B5)", "(D8: ...)").
TITLE_ID = re.compile(NARRATION.pattern + r"|\([A-Z]{1,2}[0-9]{1,2}\b")
TITLE_CALL = re.compile(r"\b(?:it|test|describe)\b(?:\.\w+)?")


def _after_group(text: str, i: int) -> int:
    """the index after the bracketed group opening at text[i] ("(", "[" or "{"), strings skipped"""
    depth, quote = 0, ""
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = ""
        elif c in "\"'`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def titles_in(text: str) -> list[tuple[int, str]]:
    """(line, title) of each `it(`, `test(` and `describe(` call's first argument when it is a string, the forms with a
    modifier (`it.skipIf(cond)("...")`, `it.each(table)("...")`) included"""
    out = []
    for m in TITLE_CALL.finditer(text):
        i = m.end()
        if m.group(0).count(".") and i < len(text) and text[i] == "(":
            i = _after_group(text, i)                       # the modifier's own arguments
        if i >= len(text) or text[i] != "(":
            continue
        j = i + 1
        while j < len(text) and text[j].isspace():
            j += 1
        if j >= len(text) or text[j] not in "\"'`":
            continue
        quote, k = text[j], j + 1
        while k < len(text) and text[k] != quote:
            k += 2 if text[k] == "\\" else 1
        out.append((text.count("\n", 0, j) + 1, text[j + 1:k]))
    return out


def dashboard_test_files() -> list[Path]:
    return sorted(p for p in (ROOT / "app" / "src").rglob("*") if p.is_file() and TEST_FILE.search(p.name)
                  and "node_modules" not in p.parts)


def test_the_title_reader_finds_every_call_form():
    src = """
      describe("the register (D8)", () => {
        it(
          "a title on the next line", () => {});
        it.skipIf(!HAVE_EXPORTS)("a modified one (RF #7)", () => {});
        it.each([[1, "a"], [2, "b"]])("%s each", () => {});
        test(`a template ${x}`, () => {});
        it(name, () => {});
      });
    """
    titles = [t for _, t in titles_in(src)]
    assert titles == ["the register (D8)", "a title on the next line", "a modified one (RF #7)", "%s each", "a template ${x}"]
    assert [t for t in titles if TITLE_ID.search(t)] == ["the register (D8)", "a modified one (RF #7)"]
    for ok in ("splits a purchase in two (a 60/40 split)", "the B-side of a pair", "card A1B2 is not an id", "a 3% band"):
        assert not TITLE_ID.search(ok), ok
    for bad in ("tags-only answers (B5)", "parts net into their day (D8: never double counted)", "a deploy (PR-123)"):
        assert TITLE_ID.search(bad), bad


def test_no_dashboard_test_title_carries_an_id():
    files = dashboard_test_files()
    assert len(files) > 50 and any(p.name == "smoke.test.tsx" for p in files)
    assert sum(len(titles_in(p.read_text(encoding="utf-8"))) for p in files) > 1000
    found = [f"{p.relative_to(ROOT)}:{n}: {t}" for p in files for n, t in titles_in(p.read_text(encoding="utf-8"))
             if TITLE_ID.search(t)]
    assert found == [], "say what the test checks instead of the id: " + "; ".join(found)

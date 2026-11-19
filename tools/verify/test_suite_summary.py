# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""tools/verify/suite_summary.py - the only thing printed after a test run over real exports: counts and failing test
titles, never a failure message (those quote the amounts the tests compared)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import suite_summary  # noqa: E402  (tools/verify is not a package)


def _report(*results: tuple[str, str, list[str]]) -> dict:
    return {"testResults": [{"assertionResults": [
        {"fullName": name, "status": status, "failureMessages": msgs} for name, status, msgs in results]}]}


FAILING = _report(
    ("net worth adds up", "passed", []),
    ("monthly spend matches", "failed", ["AssertionError: expected 1234.56 to equal 99"]),
    ("todo", "pending", []),
)


def test_summary_counts_and_titles_but_never_messages():
    text, failed = suite_summary.summarise(FAILING)
    assert failed == 1
    assert text.splitlines()[0] == "tests: 1 passed / 1 failed / 1 skipped / 3 total"
    assert "monthly spend matches" in text
    assert "1234.56" not in text


def test_main_exits_one_on_failure(tmp_path, capsys):
    path = tmp_path / "vitest.json"
    path.write_text(json.dumps(FAILING))
    assert suite_summary.main([str(path)]) == 1
    out = capsys.readouterr().out
    assert "monthly spend matches" in out and "1234.56" not in out


def test_main_exits_zero_when_everything_passes(tmp_path, capsys):
    path = tmp_path / "vitest.json"
    path.write_text(json.dumps(_report(("a", "passed", []), ("b", "passed", []))))
    assert suite_summary.main([str(path)]) == 0
    assert capsys.readouterr().out.strip() == "tests: 2 passed / 0 failed / 0 skipped / 2 total"


# One file loaded and passed; the other never loaded (an import or syntax error), so it has no assertion results and
# its message quotes what it choked on.
UNLOADED = {"testResults": [
    {"name": "/w/app/src/lib/sample.test.ts", "status": "passed", "message": "",
     "assertionResults": [{"fullName": "sample adds up", "status": "passed", "failureMessages": []}]},
    {"name": "/w/app/src/smoke.test.tsx", "status": "failed",
     "message": "SyntaxError: Unexpected token in demo-checking-0000 at 1234.56", "assertionResults": []},
]}


def test_a_file_that_failed_to_load_is_counted_and_named_by_path_only():
    text, failed = suite_summary.summarise(UNLOADED)
    assert failed == 1
    first = text.splitlines()[0]
    assert first.startswith("tests: 1 passed / 0 failed / 0 skipped / 1 total")
    assert "1 file" in first
    assert "FAILED FILE: /w/app/src/smoke.test.tsx" in text.splitlines()
    assert "sample.test.ts" not in text
    for leak in ("SyntaxError", "demo-checking-0000", "1234.56"):
        assert leak not in text


def test_main_exits_one_when_a_file_failed_to_load(tmp_path, capsys):
    path = tmp_path / "vitest.json"
    path.write_text(json.dumps(UNLOADED))
    assert suite_summary.main([str(path)]) == 1
    out = capsys.readouterr().out
    assert "smoke.test.tsx" in out and "1234.56" not in out


def test_no_skips_prints_the_gate_line_and_passes_only_with_every_test_run(tmp_path, capsys):
    path = tmp_path / "vitest.json"
    path.write_text(json.dumps(_report(("a", "passed", []), ("b", "passed", []))))
    assert suite_summary.main(["--no-skips", str(path)]) == 0
    assert capsys.readouterr().out.strip() == "vitest: 2 passed / 0 failed / 0 skipped"


def test_no_skips_fails_on_a_skipped_test_and_names_it():
    text, failed = suite_summary.gate(_report(("a", "passed", []), ("smoke route", "skipped", []), ("later", "todo", [])))
    assert failed == 2
    lines = text.splitlines()
    assert lines[0] == "vitest: 1 passed / 0 failed / 2 skipped"
    assert "SKIPPED: smoke route" in lines and "SKIPPED: later" in lines


def test_no_skips_fails_on_a_failure_a_broken_file_or_a_run_of_nothing():
    text, failed = suite_summary.gate(FAILING)
    assert failed == 2 and "FAILED: monthly spend matches" in text and "1234.56" not in text
    text, failed = suite_summary.gate(UNLOADED)
    assert failed == 1 and "FAILED FILE: /w/app/src/smoke.test.tsx" in text and "1234.56" not in text
    text, failed = suite_summary.gate({"testResults": []})
    assert failed == 1 and text == "vitest: 0 passed / 0 failed / 0 skipped / no test ran"


def test_matrix_names_the_profile_and_counts_the_known_gaps_among_the_passed(tmp_path, capsys):
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(_report(("/ — renders", "passed", []), ("/ — values (known gap)", "passed", []),
                                       ("/data — console (known gap)", "passed", []))))
    assert suite_summary.main(["--matrix", "tiny", str(path)]) == 0
    assert capsys.readouterr().out.strip() == "matrix tiny: 3 passed (2 known gaps) / 0 failed"


def test_matrix_fails_on_a_failure_a_skip_a_broken_file_or_a_run_of_nothing():
    text, failed = suite_summary.matrix(FAILING, "new")
    assert failed == 2
    lines = text.splitlines()
    assert lines[0] == "matrix new: 1 passed (0 known gaps) / 1 failed / 1 skipped"
    assert "FAILED: monthly spend matches" in lines and "SKIPPED: todo" in lines and "1234.56" not in text
    text, failed = suite_summary.matrix(UNLOADED, "full")
    assert failed == 1 and "FAILED FILE: /w/app/src/smoke.test.tsx" in text and "1234.56" not in text
    text, failed = suite_summary.matrix({"testResults": []}, "empty")
    assert failed == 1 and text == "matrix empty: 0 passed (0 known gaps) / 0 failed / no test ran"


def test_main_refuses_a_bad_usage(tmp_path):
    assert suite_summary.main([]) == 2
    assert suite_summary.main(["--no-skips"]) == 2
    assert suite_summary.main(["a.json", "b.json"]) == 2
    assert suite_summary.main(["--matrix", "tiny"]) == 2
    assert suite_summary.main(["--matrix"]) == 2


def test_a_failed_file_with_a_failing_test_is_counted_once():
    report = {"testResults": [{"name": "/w/a.test.ts", "status": "failed", "message": "boom 1234.56",
                               "assertionResults": [{"fullName": "x", "status": "failed", "failureMessages": ["9"]}]}]}
    text, failed = suite_summary.summarise(report)
    assert failed == 1
    assert "FAILED FILE" not in text and "1234.56" not in text

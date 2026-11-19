#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Summarise a vitest JSON report as counts plus the titles of failed tests.

Used when the app suite runs over real exports: the reporter's normal output quotes the values a failing assertion
compared, so it goes to a private file and only this summary is printed. Test titles come from source code and are safe
to show; `failureMessages` are never read. A test file that failed with no failing test (it never loaded, or a hook
threw) is listed by its path and counted as a failure; its `message` is never read either.

With --no-skips (the gate's run on the synthetic demo household, where every dashboard test must run) the line is
`vitest: P passed / F failed / S skipped`, each skipped test is named too, and a skipped test fails the gate as a
failed one does.

With --matrix PROFILE (the route matrix's run on one demo household) the line is
`matrix PROFILE: P passed (G known gaps) / F failed`, G being the passed tests the matrix titles "(known gap)" (an
expected failure that failed as expected); a skipped test is named and fails it as with --no-skips.

usage: tools/verify/suite_summary.py [--no-skips | --matrix PROFILE] <vitest.json>
       exit 0 all passed (none skipped), 1 otherwise, 2 on a usage error
"""
from __future__ import annotations

import functools
import json
import sys


def _counts(report: dict) -> tuple[int, list[str], list[str], int, list[str]]:
    """passed, failed titles, skipped titles, total, paths of files that failed outside any test"""
    files = report.get("testResults", [])
    results = [a for f in files for a in f.get("assertionResults", [])]
    passed = sum(1 for a in results if a.get("status") == "passed")
    failed = [a["fullName"] for a in results if a.get("status") == "failed"]
    skipped = [a["fullName"] for a in results if a.get("status") not in ("passed", "failed")]
    broken = [f.get("name") or "(unnamed file)" for f in files if f.get("status") == "failed"
              and not any(a.get("status") == "failed" for a in f.get("assertionResults", []))]
    return passed, failed, skipped, len(results), broken


def summarise(report: dict) -> tuple[str, int]:
    passed, failed, skipped, total, broken = _counts(report)
    line = f"tests: {passed} passed / {len(failed)} failed / {len(skipped)} skipped / {total} total"
    if broken:
        line += f" / {len(broken)} file(s) failed outside any test"
    lines = [line] + [f"FAILED: {name}" for name in failed] + [f"FAILED FILE: {path}" for path in broken]
    return "\n".join(lines), len(failed) + len(broken)


def gate(report: dict) -> tuple[str, int]:
    """The summary for a run in which every test must run: a skipped test counts against it like a failed one."""
    passed, failed, skipped, _, broken = _counts(report)
    line = f"vitest: {passed} passed / {len(failed)} failed / {len(skipped)} skipped"
    if broken:
        line += f" / {len(broken)} file(s) failed outside any test"
    if passed == 0 and not failed and not broken:
        line += " / no test ran"
    lines = ([line] + [f"FAILED: {name}" for name in failed] + [f"SKIPPED: {name}" for name in skipped]
             + [f"FAILED FILE: {path}" for path in broken])
    return "\n".join(lines), len(failed) + len(skipped) + len(broken) + (passed == 0)


KNOWN_GAP = "(known gap)"


def matrix(report: dict, profile: str) -> tuple[str, int]:
    """The summary of the route matrix on one household: as `gate`, with the known gaps counted among the passed."""
    passed, failed, skipped, _, broken = _counts(report)
    results = [a for f in report.get("testResults", []) for a in f.get("assertionResults", [])]
    gaps = sum(1 for a in results if a.get("status") == "passed" and a.get("fullName", "").endswith(KNOWN_GAP))
    line = f"matrix {profile}: {passed} passed ({gaps} known gaps) / {len(failed)} failed"
    if skipped:
        line += f" / {len(skipped)} skipped"
    if broken:
        line += f" / {len(broken)} file(s) failed outside any test"
    if passed == 0 and not failed and not broken:
        line += " / no test ran"
    lines = ([line] + [f"FAILED: {name}" for name in failed] + [f"SKIPPED: {name}" for name in skipped]
             + [f"FAILED FILE: {path}" for path in broken])
    return "\n".join(lines), len(failed) + len(skipped) + len(broken) + (passed == 0)


def main(argv: list[str]) -> int:
    if argv[:1] == ["--matrix"]:
        if len(argv) != 3:
            print(__doc__, file=sys.stderr)
            return 2
        profile, args = argv[1], argv[2:]
        summary = functools.partial(matrix, profile=profile)
    else:
        no_skips = argv[:1] == ["--no-skips"]
        args = argv[1:] if no_skips else argv
        summary = gate if no_skips else summarise
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    with open(args[0], encoding="utf-8") as fh:
        text, failed = summary(json.load(fh))
    print(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
# The verification gates in Docker (the host need not have uv or node): lint + pytest in the ETL image's test target
# (<prefix>-etl:<test tag>), the harness's own tests, then eslint + vitest + tsc in the web image's web-build stage
# (<prefix>-web:<test tag>), then the two runtime images (<prefix>-etl:rt, the ETL image's default target, and
# <prefix>-web:rt) are built and checked; the names, the label every build and container carries and the default
# scratch root are the harness's (tools/verify/harness.sh, harness_names.py: family-financials-*:test|rt, the label
# family-financials.test=1 and ~/ff-verify unless tools/private/harness.env or the environment says otherwise); every container that runs a check or a test runs with --network none (only the image builds, and the
# secret scan's image pulled by its digest, reach the network), except the stack stage's: without exports, last, the demo household's whole stack runs from the two
# runtime images (tools/verify/stack.sh: ./ff demo and ./ff up, the security matrix, a signed-in browser run whose
# Playwright package is installed from the network, then everything brought down; its line `stack: P/T`), and its
# failure, or anything it leaves behind, fails the gate. Image tags are the test tag and rt, never ci/latest. A household's
# config is never part of the tree or the image: in a git checkout (the tree is the top of its work tree) git must
# track no file under config/ (an untracked, ignored config/ of a developer's own is fine), a tree that is no checkout
# (an exported copy, wherever it is unpacked) must hold no config at all, and the built ETL image must hold nothing
# where its ETL looks for a config; any of them failing is exit 1.
# The runtime images' stage, with exports or without, prints one `images:` line per check and fails the gate on the
# first problem: the runtime ETL image holds no uv, git, make, curl or dev group and no tests, tools, docs, config,
# .git or .env, and its /opt/ff carries the tree's LICENSE, NOTICE, THIRD_PARTY_LICENSES and THIRD_PARTY_NOTICES.md;
# uid 12345 validates and ingests examples/minimal in it (FF_HOME read-only, its data/ world-writable);
# from a directory holding an etl package of its own, Python imports the image's (/opt/ff/etl) with HOME /tmp;
# supercronic runs; the image's tzdata holds UTC, America/New_York and Pacific/Kiritimati; in each of those zones
# deploy/make_crontab.py writes the scheduler's crontab from examples/minimal's household.yaml and supercronic -test
# accepts it; the web image's /srv/public is deploy/public exactly (its sign-in page's source link the default
# FF_SOURCE), its /usr/share/doc/family-financials the same four legal files, and no source map ships; each runtime
# image's OCI labels are the project's, not its base image's; the web image, as
# uid 12345, answers /healthz at http://127.0.0.1:8080 with its defaults and with a domain; Caddy validates and adapts
# deploy/Caddyfile with the image's defaults and with SITE_ADDRESS, FF_HOSTNAMES and TRUSTED_PROXIES set for a domain,
# without a warning but the one a site on the HTTP port earns, and each JSON passes tests/test_caddyfile.py
# (FF_CADDY_ADAPTED); the runtime dependencies' licences (tools/verify/licences.py, `licences:` lines: the npm closure
# and the runtime ETL image's Python distributions, each on the allow-list, THIRD_PARTY_NOTICES.md's generated
# block current, and THIRD_PARTY_LICENSES the npm packages' licence files as the web build image holds them); last,
# the two images' sizes. Then, with exports or without, gitleaks scans what the tree ships, and
# in a checkout its git history, for secrets (`secrets:` lines; a finding is exit 1). Before any image is built, every
# workflow passes tools/verify/check_workflows.py and actionlint, and the maintainer scripts (tools/maintainer/*.sh)
# pass shellcheck (each image pinned by digest, run with no network).
# Usage: tools/verify/ci.sh [EXPORTS_DIR]
#   EXPORTS_DIR  an exports dir (e.g. ~/ff-verify/head/data/exports) mounted read-only at the app's data/exports;
#                the dashboard run is then eslint, tsc and the contract, agg and route-matrix tests on it
#                (src/data/contract.test.ts src/lib/agg.test.ts src/matrix.test.tsx, FF_REQUIRE_EXPORTS=1), never the
#                smoke suite, which is written for the demo household `full`.
# Without EXPORTS_DIR the six demo households are generated (tools/verify/demo.sh), the main vitest run reads `full`
# (its exports and truth.json mounted read-only, FF_EXPORTS_DIR / FF_TRUTH / FF_REQUIRE_EXPORTS=1, FF_PROFILE=full), so
# every dashboard test runs, and the gate fails when vitest reports any skipped test (`vitest: P passed / F failed / S
# skipped`); then each profile's exports get one container that runs the contract and agg tests (`contract <profile>:
# OK`) and, when they pass, the route matrix with FF_PROFILE=<profile> (`matrix <profile>: P passed (G known gaps) / F
# failed`, G its expected failures, the rows of app/src/test/matrixGaps.ts; P is 99 on a household with data and 293 on
# `empty`, whose matrix runs three blocks: set up, waiting for data, the first-run state held off); a failed or skipped
# test fails the gate.
# Those six run in UTC; then the matrix alone runs twice more, `full` with FF_TEST_TZ=America/New_York and `tiny` with
# FF_TEST_TZ=Pacific/Kiritimati, each in its own container (`matrix <profile> (<zone>): …`, 24 tests more than the
# UTC lines: the zone check, the clock's five zone tests and the 18 clock-time tests run with it), a failure naming the
# profile and the zone.
# Then the demo site is built from `full` in the web test image and checked (tools/verify/demo_site.sh, `demo site:`
# lines; a problem fails the gate).
# That run's last two lines are the one a CI log is read for and `== … CI OK ==`:
#   gate: pytest P passed; vitest P passed / 0 failed / 0 skipped; contract N/6; matrix M/8; stack S/T
# pytest's count is the ETL suite's (tests/) own summary line (its output is shown as usual and kept in the run's temp
# dir; a last line with no `N passed` stops the gate, and so does one that counts a skipped, xfailed or xpassed test or
# an error); the harness's and the private tooling's pytest runs print their own summary lines above it, are not in the
# count and may skip what their environment lacks. vitest's is suite_summary.py's line for `full`, N counts the
# profiles whose contract passed and M the matrix runs that passed (six profiles, two zones), S/T is stack.sh's own
# (the matrix's cases that passed and the browser run, of all of them; a case recorded as a known product defect is
# counted in T only, listed on its own `known:` line, and fails nothing). Runs given EXPORTS_DIR
# print no gate line. The private tooling's step
# (ruff check over the mounted tools/private, a finding failing the gate, then its tests) is skipped only when
# tools/private is absent; a tools/private without a test_*.py fails the gate.
# Exports are treated as real: EXPORTS_DIR must resolve to a directory strictly inside the scratch root
# ($FF_SCRATCH, absolute and neither /, the home directory nor above it; default the harness's scratch directory,
# FF_HARNESS_SCRATCH, in the account's home directory from the account database, never $HOME), and FF_SUITE_LOG is required: an absolute path resolving strictly
# inside the scratch root, in an existing directory. The matrix runs as the household (FF_PROFILE=household: no known
# gap). vitest writes its JSON report to a private temp dir and its output to the mode-600 FF_SUITE_LOG, and only the
# counts, failing test titles and the paths of test files that failed outside any test (tools/verify/suite_summary.py)
# are printed.
# Env: FF_DEMO_EXPORTS=1  EXPORTS_DIR holds synthetic exports: the scratch-root and log rules are lifted and vitest
#                         prints as usual. The flag never overrides the location: an EXPORTS_DIR at or inside a
#                         real-data scratch root ($FF_SCRATCH, or the default root whatever FF_SCRATCH says), as spelt
#                         or as resolved, is refused, and so is one that holds such a root.
#      FF_PROFILE         with FF_DEMO_EXPORTS=1, the demo household EXPORTS_DIR holds (a plain name; default
#                         household); refused with real exports.
#      FF_REAL_EXPORTS=1  the default made explicit; it also requires EXPORTS_DIR.
set -euo pipefail
W="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=tools/verify/harness.sh
. "$W/tools/verify/harness.sh"
EXPORTS="${1:-}"
if [ "${FF_REAL_EXPORTS:-}" = "1" ]; then
  [ "${FF_DEMO_EXPORTS:-}" != "1" ] || { echo "FF_REAL_EXPORTS=1 and FF_DEMO_EXPORTS=1 contradict each other" >&2; exit 2; }
  [ -n "$EXPORTS" ] || { echo "FF_REAL_EXPORTS=1 needs EXPORTS_DIR" >&2; exit 2; }
fi
# The real-data scratch roots (harness.sh's harness_scratch_roots sets SCRATCH, the resolved scratch root, and
# DEFAULT_SCRATCH, the default root whatever FF_SCRATCH says; harness_refuse_real refuses a path at, inside or above
# either).
REAL=0
if [ -n "$EXPORTS" ] && [ "${FF_DEMO_EXPORTS:-}" != "1" ]; then REAL=1; fi
# The household the matrix reads its known gaps for, on exports given as the argument.
GIVEN_PROFILE=household
if [ -n "$EXPORTS" ] && [ -n "${FF_PROFILE:-}" ]; then
  [ "$REAL" = 0 ] || { echo "FF_PROFILE names a demo household: real exports always run as the household (FF_DEMO_EXPORTS=1 is for synthetic exports only)" >&2; exit 2; }
  [[ "$FF_PROFILE" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] \
    || { echo "FF_PROFILE must be a plain name such as tiny (got '$FF_PROFILE')" >&2; exit 2; }
  GIVEN_PROFILE="$FF_PROFILE"
fi
if [ "$REAL" = 1 ]; then
  [ -n "${FF_SUITE_LOG:-}" ] || { echo "exports are treated as real, so FF_SUITE_LOG must name a private file for the test output (FF_DEMO_EXPORTS=1 is for synthetic exports only)" >&2; exit 2; }
  case "$FF_SUITE_LOG" in /*) ;; *) echo "FF_SUITE_LOG must be an absolute path (got '$FF_SUITE_LOG')" >&2; exit 2 ;; esac
  harness_scratch_roots
  RESOLVED="$(realpath -e "$EXPORTS" 2>/dev/null)" && [ -d "$RESOLVED" ] \
    || { echo "EXPORTS_DIR $EXPORTS is not a directory" >&2; exit 2; }
  # Strictly inside: a descendant of the resolved root, never the root itself; symlinks are resolved first.
  case "$RESOLVED" in
    "$SCRATCH"/?*) ;;
    *) echo "EXPORTS_DIR must resolve strictly inside the scratch root $SCRATCH (got $RESOLVED); FF_DEMO_EXPORTS=1 is for synthetic exports only" >&2; exit 2 ;;
  esac
  EXPORTS="$RESOLVED"
  # The log holds the values failing assertions compared: it stays in the private scratch root, never the worktree.
  LOG="$(realpath -m "$FF_SUITE_LOG")"
  [ -d "$(dirname "$LOG")" ] || { echo "the directory of FF_SUITE_LOG does not exist ($(dirname "$LOG"))" >&2; exit 2; }
  case "$LOG" in
    "$SCRATCH"/?*) ;;
    *) echo "FF_SUITE_LOG must resolve strictly inside the scratch root $SCRATCH (got $LOG)" >&2; exit 2 ;;
  esac
  [ ! -d "$LOG" ] || { echo "FF_SUITE_LOG names a directory: $LOG" >&2; exit 2; }
  FF_SUITE_LOG="$LOG"
elif [ -n "$EXPORTS" ]; then
  # FF_DEMO_EXPORTS=1 never overrides the location: exports at or inside a real-data scratch root are real, whether the
  # path as resolved or as spelt (symlinks not followed: a link placed inside a root names that root) lies there, and a
  # directory above a root would mount the root with it.
  harness_scratch_roots
  RESOLVED="$(realpath -e "$EXPORTS" 2>/dev/null)" && [ -d "$RESOLVED" ] \
    || { echo "EXPORTS_DIR $EXPORTS is not a directory" >&2; exit 2; }
  harness_refuse_real EXPORTS_DIR "$RESOLVED" "$(realpath -m -s "$EXPORTS")"
  EXPORTS="$RESOLVED"
fi
if [ -n "$EXPORTS" ]; then
  [ -f "$EXPORTS/manifest.json" ] || { echo "no manifest.json in $EXPORTS" >&2; exit 2; }
fi
cd "$W"

# Host-side, before any image: the agent context files' byte budget (tests/test_docs_size.py checks the same in the ETL
# suite, so every pytest run does too) and their docs/design pointers (tests/test_documents.py checks the documents'
# links in the ETL suite as well).
echo "== context files: AGENTS.md + app/AGENTS.md byte budget, docs/design pointers =="
CTX=$(cat "$W/AGENTS.md" "$W/app/AGENTS.md" | wc -c)
[ "$CTX" -le 40000 ] || { echo "AGENTS.md + app/AGENTS.md = $CTX bytes, over the 40000-byte budget (tests/test_docs_size.py)" >&2; exit 1; }
for p in $(grep -ohE 'docs/design/[A-Za-z0-9_-]+\.md' "$W/AGENTS.md" "$W/app/AGENTS.md" | sort -u); do
  [ -f "$W/$p" ] || { echo "an AGENTS.md points at $p, which does not exist" >&2; exit 1; }
done
echo "context files: $CTX bytes of 40000; every docs/design pointer resolves"

echo "== workflows: every job on a GitHub-hosted runner, every action pinned =="
python3 tools/verify/check_workflows.py

# The scope scanner on the host too, before any image is built: every root it scans is there (the image's pytest run
# checks the same).
echo "== scope: every root the scope scanner reads is there =="
python3 tests/test_scope.py || { echo "tests/test_scope.py failed: restore the roots it names above" >&2; exit 1; }

# A household's config is its own and never ships. In a git checkout the check asks git, so a developer's own
# untracked config/ (git-ignored) is allowed; only a count is printed. A tree that is no checkout (an exported copy,
# `git archive | tar -x`) has no ignored files, since everything in it ships, so there `config` must not exist at all:
# no directory, file or link, dangling or not. The tree is a checkout only when it is itself the top of a work tree
# (compared as resolved paths; a linked worktree, whose .git is a file, is one): an exported copy unpacked somewhere
# inside another repository's work tree is still an export. git's own error is never printed.
echo "== config: no household config in the tree =="
TOP="$(git -C "$W" rev-parse --show-toplevel 2>/dev/null)" || TOP=""
if [ -n "$TOP" ] && [ "$(realpath -m "$TOP")" = "$(realpath -m "$W")" ]; then
  TRACKED="$(git -C "$W" ls-files -- config)" \
    || { echo "git ls-files failed in $W, so whether git tracks a file under config/ is unknown" >&2; exit 1; }
  [ -z "$TRACKED" ] \
    || { echo "git tracks $(printf '%s\n' "$TRACKED" | grep -c '') file(s) under config/: a household's config is never committed (git rm -r --cached config)" >&2; exit 1; }
  echo "config: git tracks no file under config/"
  REV="$(git -C "$W" rev-parse --short HEAD 2>/dev/null)" || REV="no commit yet"
else
  if [ -e "$W/config" ] || [ -L "$W/config" ]; then
    echo "$W is not a git checkout and holds config, which ships with everything else in it: an exported tree carries no household's config" >&2
    exit 1
  fi
  echo "config: not a git checkout; the tree holds no config/"
  REV="not a git checkout"
fi

# actionlint over every workflow (with shellcheck over their scripts), from its image pinned by digest (docker pulls it
# by that digest when it is absent), with no network, as the caller, under the harness's label; any finding fails.
ACTIONLINT=rhysd/actionlint:1.7.12@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667
echo "== workflows: actionlint =="
FLOWS=()
for f in "$W"/.github/workflows/*.yml "$W"/.github/workflows/*.yaml; do
  [ -f "$f" ] && FLOWS+=("/flows/$(basename "$f")")
done
if [ "${#FLOWS[@]}" -gt 0 ]; then
  docker run --rm --label "$FF_HARNESS_LABEL" --network none --user "$(id -u):$(id -g)" \
    -v "$W/.github/workflows:/flows:ro" "$ACTIONLINT" -no-color "${FLOWS[@]}" \
    || { echo "actionlint found a problem in a workflow, or could not read them (its lines above)" >&2; exit 1; }
  echo "workflows: actionlint finds nothing in ${#FLOWS[@]} workflow(s)"
else
  echo "workflows: no workflow for actionlint to read"
fi

# shellcheck over the maintainer scripts (tools/maintainer/*.sh), from its image pinned by digest (docker pulls it by
# that digest when it is absent), with no network, as the caller, under the harness's label; any finding fails.
SHELLCHECK=koalaman/shellcheck:v0.11.0@sha256:61862eba1fcf09a484ebcc6feea46f1782532571a34ed51fedf90dd25f925a8d
echo "== scripts: shellcheck =="
MAINTAINER=()
for f in "$W"/tools/maintainer/*.sh; do
  [ -f "$f" ] && MAINTAINER+=("/scripts/$(basename "$f")")
done
if [ "${#MAINTAINER[@]}" -gt 0 ]; then
  docker run --rm --label "$FF_HARNESS_LABEL" --network none --user "$(id -u):$(id -g)" \
    -v "$W/tools/maintainer:/scripts:ro" "$SHELLCHECK" "${MAINTAINER[@]}" \
    || { echo "shellcheck found a problem in a maintainer script, or could not read them (its lines above)" >&2; exit 1; }
  echo "scripts: shellcheck finds nothing in ${#MAINTAINER[@]} script(s)"
else
  echo "scripts: no maintainer script for shellcheck to read"
fi

# OUT is the run's private temp dir (pytest's output, the vitest and matrix reports) and DEMO, without an exports
# argument, the demo households (tools/verify/demo.sh); both are removed on every way out: a finished or failed run, an
# interrupt or a termination.
DEMO=""
OUT=""
cleanup() {
  [ -z "$DEMO" ] || rm -rf "$DEMO"
  [ -z "$OUT" ] || rm -rf "$OUT"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
OUT="$(mktemp -d)"; chmod 700 "$OUT"

# Each build carries the harness's label, and the containers run the image ID the build printed, never the tag, so a
# second run retagging the image meanwhile cannot change what this run tests. The tags are still applied for people.
image_id() {
  [[ "$1" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "docker build printed no image ID (got '$1')" >&2; exit 1; }
  printf '%s\n' "$1"
}
echo "== $(date -Is) build $HARNESS_ETL_TEST (the ETL image's test target) from $W ($REV) =="
BUILT="$(docker build -q --label "$FF_HARNESS_LABEL" --target test -f deploy/Dockerfile.etl -t "$HARNESS_ETL_TEST" .)" \
  || { echo "docker build of $HARNESS_ETL_TEST failed" >&2; exit 1; }
ETL_ID="$(image_id "$BUILT")"
echo "$ETL_ID"
# The image holds no config (.dockerignore): its ETL, asked where it looks when no variable names a place, finds
# nothing there. Compose mounts the household's config at run time, where FF_HOME names it.
echo "== the ETL image holds no config =="
FOUND="$(docker run --rm --label "$FF_HARNESS_LABEL" --network none "$ETL_ID" .venv/bin/python -c \
  'from etl.config import Paths; d = Paths().config_dir; print(d if d.exists() else "")')" \
  || { echo "could not ask the ETL image where its ETL looks for a config" >&2; exit 1; }
[ -z "$FOUND" ] \
  || { echo "the ETL image holds a config at $FOUND, where its ETL looks for one: .dockerignore must keep config/ out of the image" >&2; exit 1; }
echo "image: no config where the ETL looks for one"
echo "== ruff + pytest =="
# The output reaches the terminal as usual and a copy stays in OUT: pytest's last line, its own summary, is the gate
# line's count.
docker run --rm --label "$FF_HARNESS_LABEL" --network none --user root "$ETL_ID" sh -lc "uv run ruff check etl tests deploy tools && uv run pytest -q" \
  | tee "$OUT/pytest.out"
PYTEST_LAST="$(tail -n 1 "$OUT/pytest.out")"
PYTEST_PASSED="$(printf '%s\n' "$PYTEST_LAST" | sed -nE 's/^(.*, )?([0-9]+) passed(,| in ).*$/\2/p')"
[ -n "$PYTEST_PASSED" ] \
  || { echo "pytest printed no 'N passed' summary as its last line, so the gate cannot count what ran" >&2; exit 1; }
# Every test of the ETL suite runs in the gate: a summary that counts any skipped, xfailed or xpassed test, or an
# error, fails it with those counts. (The harness and private-tooling runs below keep their conditional skips.)
NOT_RUN=""
rest="$PYTEST_LAST"
not_run_re='([0-9]+) (skipped|xfailed|xpassed|errors?)(.*)$'
while [[ "$rest" =~ $not_run_re ]]; do
  NOT_RUN="${NOT_RUN:+$NOT_RUN, }${BASH_REMATCH[1]} ${BASH_REMATCH[2]}"
  rest="${BASH_REMATCH[3]}"
done
[ -z "$NOT_RUN" ] \
  || { echo "the ETL suite's summary reports $NOT_RUN: every test in tests/ must run and pass in the gate" >&2; exit 1; }
# The harness's own tests live beside it and are copied into the image, but the default pytest run collects tests/ only.
echo "== tools/verify tests =="
docker run --rm --label "$FF_HARNESS_LABEL" --network none --user root -e PYTHONDONTWRITEBYTECODE=1 "$ETL_ID" \
  sh -lc "uv run pytest -q -p no:cacheprovider tools/verify"
# The private tooling is not in the image: mount it read-only, lint it with the ruff rules the rest of the tree has
# (the run above never sees it), then run its tests; a ruff finding fails the gate. Only an absent tools/private skips
# the step (an exported copy of the tree leaves it out); a directory that is there must hold its tests, so tests
# renamed or moved away, or a directory left without its tooling, fail the gate instead of vanishing from it.
P="$W/tools/private"
if [ ! -e "$P" ] && [ ! -L "$P" ]; then
  echo "== tools/private tests: skipped, this tree has no tools/private =="
else
  echo "== tools/private: ruff + tests =="
  [ -d "$P" ] || { echo "tools/private exists but is no directory, so its tests cannot run" >&2; exit 1; }
  compgen -G "$P/*.py" >/dev/null \
    || { echo "tools/private holds no Python file: restore its tooling or remove the directory" >&2; exit 1; }
  compgen -G "$P/test_*.py" >/dev/null \
    || { echo "tools/private holds Python files but no test_*.py: its tests were renamed or moved, so none would run" >&2; exit 1; }
  docker run --rm --label "$FF_HARNESS_LABEL" --network none --user root -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$P:/app/finance-app/tools/private:ro" "$ETL_ID" \
    sh -lc "uv run ruff check tools/private && uv run pytest -q -p no:cacheprovider tools/private" \
    || { echo "tools/private: ruff check or its tests failed (output above)" >&2; exit 1; }
fi

# Without an exports argument the six demo households are generated and ingested (tools/verify/demo.sh) into DEMO,
# and the contract runs on each below.
PROFILES=(full new tiny empty no-investments no-income)
if [ -z "$EXPORTS" ]; then
  echo "== demo households: generate + ingest ${#PROFILES[@]} profiles offline =="
  DEMO="$(mktemp -d)"
  FF_ETL_IMAGE="$ETL_ID" bash "$W/tools/verify/demo.sh" "$DEMO" "${PROFILES[@]}" \
    || { echo "tools/verify/demo.sh failed" >&2; exit 1; }
fi

echo "== build $HARNESS_WEB_TEST (the web image's web-build stage: tsc --noEmit && vite build run inside it) =="
BUILT="$(docker build -q --label "$FF_HARNESS_LABEL" --target web-build -f deploy/Dockerfile.web -t "$HARNESS_WEB_TEST" .)" \
  || { echo "docker build of $HARNESS_WEB_TEST failed" >&2; exit 1; }
WEB_ID="$(image_id "$BUILT")"
echo "$WEB_ID"
MOUNT=()
# On exports given as the argument the dashboard tests are the ones written for any household's exports.
REDUCED="src/data/contract.test.ts src/lib/agg.test.ts src/matrix.test.tsx"
if [ -n "$EXPORTS" ]; then
  MOUNT=(-v "$EXPORTS:/app/finance-app/data/exports:ro" -e FF_REQUIRE_EXPORTS=1 -e "FF_PROFILE=$GIVEN_PROFILE")
  echo "== eslint + vitest ($REDUCED on the exports, as $GIVEN_PROFILE) + typecheck =="
else
  FULL="$DEMO/full"
  [ -f "$FULL/data/exports/manifest.json" ] && [ -f "$FULL/truth.json" ] \
    || { echo "the demo household full left no exports or no truth.json in $FULL" >&2; exit 1; }
  MOUNT=(-v "$FULL/data/exports:/exports:ro" -v "$FULL/truth.json:/truth.json:ro"
         -e FF_EXPORTS_DIR=/exports -e FF_TRUTH=/truth.json -e FF_REQUIRE_EXPORTS=1 -e FF_PROFILE=full)
  echo "== eslint + vitest + typecheck (the demo household full mounted: every dashboard test runs, none may skip) =="
fi
if [ "$REAL" = 1 ]; then
  # Real exports: a failing assertion quotes the amounts it compared, so the reporter's output never reaches the
  # terminal. eslint and tsc print no data; vitest's JSON report (in OUT) and output go to private files.
  install -m 600 /dev/null "$FF_SUITE_LOG"
  docker run --rm --label "$FF_HARNESS_LABEL" --network none "${MOUNT[@]}" "$WEB_ID" sh -lc "cd app && npm run lint && npm run typecheck"
  rc=0
  docker run --rm --label "$FF_HARNESS_LABEL" --network none "${MOUNT[@]}" -v "$OUT:/out" "$WEB_ID" \
    sh -lc "cd app && npx vitest run $REDUCED --reporter=json --reporter=default --outputFile=/out/vitest.json" >>"$FF_SUITE_LOG" 2>&1 || rc=$?
  [ -s "$OUT/vitest.json" ] || { echo "vitest wrote no report (exit $rc); output in $FF_SUITE_LOG" >&2; exit 1; }
  # vitest also exits non-zero for an error raised outside any test, which the JSON report does not carry: the default
  # reporter's account of it is in the private log, and the exit code is named here.
  vitest_rc=$rc
  python3 "$W/tools/verify/suite_summary.py" "$OUT/vitest.json" || rc=$?
  [ "$rc" -eq 0 ] || { echo "app tests FAILED on real exports (vitest exit $vitest_rc); output in $FF_SUITE_LOG" >&2; exit 1; }
elif [ -n "$EXPORTS" ]; then
  docker run --rm --label "$FF_HARNESS_LABEL" --network none "${MOUNT[@]}" "$WEB_ID" \
    sh -lc "cd app && npm run lint && npx vitest run $REDUCED && npm run typecheck"
else
  # The demo household is synthetic, so vitest prints as usual; its JSON report (in a temp dir, removed on exit) is
  # what the gate reads: every test must pass and none may be skipped.
  docker run --rm --label "$FF_HARNESS_LABEL" --network none "$WEB_ID" sh -lc "cd app && npm run lint"
  rc=0
  docker run --rm --label "$FF_HARNESS_LABEL" --network none "${MOUNT[@]}" -v "$OUT:/out" "$WEB_ID" \
    sh -lc "cd app && npx vitest run --reporter=default --reporter=json --outputFile=/out/vitest.json" || rc=$?
  [ -s "$OUT/vitest.json" ] || { echo "vitest wrote no report (exit $rc)" >&2; exit 1; }
  SUMMARY="$(python3 "$W/tools/verify/suite_summary.py" --no-skips "$OUT/vitest.json")" \
    || { printf '%s\n' "$SUMMARY"; echo "app tests FAILED or SKIPPED on the demo household full (vitest exit $rc)" >&2; exit 1; }
  printf '%s\n' "$SUMMARY"
  VITEST="$(printf '%s\n' "$SUMMARY" | sed -nE '1s/^vitest: ([0-9]+ passed \/ [0-9]+ failed \/ [0-9]+ skipped)$/\1/p')"
  [ -n "$VITEST" ] || { echo "suite_summary.py printed no 'vitest: P passed / F failed / S skipped' line" >&2; exit 1; }
  # vitest also exits non-zero for an error raised outside any test, which the report does not carry (its output is above)
  [ "$rc" -eq 0 ] || { echo "vitest exited $rc with every test passing: an error outside any test (see its output above)" >&2; exit 1; }
  docker run --rm --label "$FF_HARNESS_LABEL" --network none "$WEB_ID" sh -lc "cd app && npm run typecheck"
fi
if [ -n "$DEMO" ]; then
  # One container per profile: the contract and rollup cross-check, a marker once they pass, then the route matrix as
  # that profile, its JSON report in a directory of its own inside OUT. The matrix's output is synthetic and printed.
  echo "== contract + rollup cross-check + route matrix on every demo profile (exports required) =="
  CONTRACTS=0
  MATRICES=0
  for p in "${PROFILES[@]}"; do
    M="$OUT/matrix-$p"
    mkdir "$M"
    rc=0
    docker run --rm --label "$FF_HARNESS_LABEL" --network none -v "$DEMO/$p/data/exports:/exports:ro" -v "$M:/out" \
      -e FF_EXPORTS_DIR=/exports -e FF_REQUIRE_EXPORTS=1 -e "FF_PROFILE=$p" "$WEB_ID" \
      sh -lc "cd app && npx vitest run src/data/contract.test.ts src/lib/agg.test.ts && touch /out/contract.ok && npx vitest run src/matrix.test.tsx --reporter=default --reporter=json --outputFile=/out/matrix.json" \
      || rc=$?
    [ -f "$M/contract.ok" ] || { echo "contract $p: FAILED (exit $rc)" >&2; exit 1; }
    echo "contract $p: OK"
    CONTRACTS=$((CONTRACTS + 1))
    [ -s "$M/matrix.json" ] || { echo "matrix $p: FAILED: vitest wrote no report (exit $rc)" >&2; exit 1; }
    python3 "$W/tools/verify/suite_summary.py" --matrix "$p" "$M/matrix.json" \
      || { echo "matrix $p: FAILED or SKIPPED (vitest exit $rc)" >&2; exit 1; }
    # vitest also exits non-zero for an error raised outside any test, which the report does not carry (its output is above)
    [ "$rc" -eq 0 ] || { echo "matrix $p: vitest exited $rc with every test passing: an error outside any test (see its output above)" >&2; exit 1; }
    MATRICES=$((MATRICES + 1))
  done
  # The matrix once more on two households, each in a zone other than UTC (app/vite.config.ts reads FF_TEST_TZ when
  # vitest starts and sets the test process's TZ from it), one container each with the per-profile run's mounts:
  # `full` in a zone behind UTC, `tiny` in the one furthest ahead, so a page that takes the machine's local day for the
  # household's fails here. Each run includes src/test/demoClock.test.ts, which fails unless the process runs in the
  # zone asked for: a run whose zone did not take fails instead of passing in UTC; src/lib/clock.test.ts, the
  # household's day at fixed instants; and src/lib/clockTimes.test.ts, the household's clock times (fmtDateTime, the
  # ages, the schedule sentence) at fixed instants, neither of which may move with the process's zone (so these two
  # lines count those three files' tests more than the UTC ones).
  ZONE_RUNS=("full America/New_York" "tiny Pacific/Kiritimati")
  for run in "${ZONE_RUNS[@]}"; do
    read -r p zone <<<"$run"
    M="$OUT/zone-$p"
    mkdir "$M"
    rc=0
    docker run --rm --label "$FF_HARNESS_LABEL" --network none -v "$DEMO/$p/data/exports:/exports:ro" -v "$M:/out" \
      -e FF_EXPORTS_DIR=/exports -e FF_REQUIRE_EXPORTS=1 -e "FF_PROFILE=$p" -e "FF_TEST_TZ=$zone" "$WEB_ID" \
      sh -lc "cd app && npx vitest run src/matrix.test.tsx src/test/demoClock.test.ts src/lib/clock.test.ts src/lib/clockTimes.test.ts --reporter=default --reporter=json --outputFile=/out/matrix.json" \
      || rc=$?
    [ -s "$M/matrix.json" ] || { echo "matrix $p ($zone): FAILED: vitest wrote no report (exit $rc)" >&2; exit 1; }
    python3 "$W/tools/verify/suite_summary.py" --matrix "$p ($zone)" "$M/matrix.json" \
      || { echo "matrix $p ($zone): FAILED or SKIPPED (vitest exit $rc)" >&2; exit 1; }
    [ "$rc" -eq 0 ] || { echo "matrix $p ($zone): vitest exited $rc with every test passing: an error outside any test (see its output above)" >&2; exit 1; }
    MATRICES=$((MATRICES + 1))
  done
  # The demo site (tools/verify/demo_site.sh, what .github/workflows/pages.yml publishes): the dashboard built with
  # VITE_DEMO=1 in the web test image just built, with no network, over `full`'s exports and family documents, then
  # held to its checks (tools/verify/demo_site.py: relative addresses only, no server path or version.json, the
  # policy in the page, the banner, every export); its `demo site:` lines, and a failure fails the gate.
  echo "== demo site: the dashboard built in demo mode over the household full, and what it ships =="
  FF_WEB_IMAGE="$WEB_ID" bash "$W/tools/verify/demo_site.sh" "$OUT/demo-site" --exports "$DEMO/full/data/exports" \
    || { echo "the demo site FAILED (its lines above)" >&2; exit 1; }
fi

# The runtime images, built from the tree like the two above: the ETL image's default target (<prefix>-etl:rt) and the
# web server (<prefix>-web:rt). What each holds is listed as root; everything else runs as uid 12345, which owns no file
# in either image, as an install that starts them as its own user does. One `images:` line per check; the first
# problem fails the gate.
echo "== images: build and check the runtime ETL and web images =="
BUILT="$(docker build -q --label "$FF_HARNESS_LABEL" --target etl -f deploy/Dockerfile.etl -t "$HARNESS_ETL_RT" .)" \
  || { echo "docker build of $HARNESS_ETL_RT failed" >&2; exit 1; }
ETL_RT_ID="$(image_id "$BUILT")"
BUILT="$(docker build -q --label "$FF_HARNESS_LABEL" --target web -f deploy/Dockerfile.web -t "$HARNESS_WEB_RT" .)" \
  || { echo "docker build of $HARNESS_WEB_RT failed" >&2; exit 1; }
WEB_RT_ID="$(image_id "$BUILT")"
echo "$ETL_RT_ID $WEB_RT_ID"
AS_ROOT=(docker run --rm --label "$FF_HARNESS_LABEL" --network none --user root)
AS_ANYONE=(docker run --rm --label "$FF_HARNESS_LABEL" --network none --user 12345:12345)
# The licence, its notice and the third parties' notices and licence texts, as the tree holds them: each image must
# carry exactly these (the ETL image in /opt/ff, the web image in /usr/share/doc/family-financials).
LEGAL="$OUT/legal"
mkdir "$LEGAL"
for f in LICENSE NOTICE THIRD_PARTY_LICENSES THIRD_PARTY_NOTICES.md; do
  [ -f "$W/$f" ] || { echo "the tree has no $f, which both runtime images must carry" >&2; exit 1; }
  cp "$W/$f" "$LEGAL/$f"
done
chmod -R a+rX "$LEGAL"
# What the runtime ETL image must not hold, from a walk of its whole filesystem (one line each, exit 1 when any): uv,
# git, make or curl on PATH, the dev group (ruff, pytest), anything in /opt/ff besides compose.yaml, deploy,
# env.example, etl, starter, tools (which holds the demo household's generator alone) and the four legal files (each
# the tree's, byte for byte), a tests, docs or config directory or the harness's tools/verify or tools/private inside
# the code (/opt/ff, and an /app should one appear), a docs/dev or .git directory or a .env file anywhere.
RUNTIME_HOLDS='
import importlib.util, os, shutil, sys
bad = [f"{t} on PATH: {shutil.which(t)}" for t in ("uv", "git", "make", "curl", "ruff", "pytest") if shutil.which(t)]
bad += [f"the {m} module" for m in ("ruff", "pytest") if importlib.util.find_spec(m)]
legal = ["LICENSE", "NOTICE", "THIRD_PARTY_LICENSES", "THIRD_PARTY_NOTICES.md"]
top = sorted(os.listdir("/opt/ff")) if os.path.isdir("/opt/ff") else ["nothing"]
if top != sorted(legal + ["compose.yaml", "deploy", "env.example", "etl", "starter", "tools"]):
    bad.append("/opt/ff holds " + ", ".join(top) + ", not compose.yaml, deploy, env.example, etl, starter, tools and "
               + ", ".join(legal) + " alone")
for f in legal:
    p = os.path.join("/opt/ff", f)
    if os.path.isfile(p) and open(p, "rb").read() != open(os.path.join("/legal", f), "rb").read():
        bad.append(p + " is not the tree" + chr(39) + "s " + f)
tools = sorted(d for d in os.listdir("/opt/ff/tools") if d != "__pycache__") if os.path.isdir("/opt/ff/tools") else []
if tools != ["demo"]:
    bad.append("/opt/ff/tools holds " + (", ".join(tools) or "nothing") + ", not demo alone")
for root, dirs, files in os.walk("/"):
    if root == "/":
        dirs[:] = [d for d in dirs if d not in ("proc", "sys", "dev")]
    code = root.startswith(("/opt/ff", "/app"))
    for d in dirs:
        p = os.path.join(root, d)
        if d == ".git" or p.endswith("/docs/dev") or (code and (d in ("tests", "docs", "config")
                                                                 or (root.endswith("/tools")
                                                                     and d not in ("demo", "__pycache__")))):
            bad.append(p)
    bad += [os.path.join(root, f) for f in files if f == ".env" or f.startswith(".env.")]
if bad:
    print("\n".join(bad))
sys.exit(1 if bad else 0)
'
"${AS_ROOT[@]}" -v "$LEGAL:/legal:ro" "$ETL_RT_ID" python -c "$RUNTIME_HOLDS" \
  || { echo "the runtime ETL image holds what it must not, or lacks what it must (listed above)" >&2; exit 1; }
echo "images: the runtime ETL image holds no uv, git, make, curl or dev group, and no tests, harness tools, docs, config, .git or .env"
echo "images: the runtime ETL image's /opt/ff carries the tree's LICENSE, NOTICE, THIRD_PARTY_LICENSES and THIRD_PARTY_NOTICES.md"
# Every .pth file in the runtime ETL image, from a walk of its whole filesystem, against the allowlist: a .pth line
# that imports runs on every interpreter start (the ingest's and the store's too), so a dependency that ships a new
# one fails the gate until it is read and added here. Allowed: the venv's own (_virtualenv.pth, uv's) and ff.pth (the
# Dockerfile's: /opt/ff and /opt/ff/deploy on the path).
PTH_ALLOWED='/opt/venv/lib/python3.12/site-packages/_virtualenv.pth
/opt/venv/lib/python3.12/site-packages/ff.pth'
PTH_FOUND="$("${AS_ROOT[@]}" "$ETL_RT_ID" python -c '
import os
found = []
for root, dirs, files in os.walk("/"):
    if root == "/":
        dirs[:] = [d for d in dirs if d not in ("proc", "sys", "dev")]
    found += [os.path.join(root, f) for f in files if f.endswith(".pth")]
print("\n".join(sorted(found)))
')" || { echo "the runtime ETL image's .pth files could not be listed" >&2; exit 1; }
[ "$PTH_FOUND" = "$PTH_ALLOWED" ] \
  || { printf 'the runtime ETL image holds these .pth files:\n%s\nnot exactly the allowed:\n%s\n' "$PTH_FOUND" "$PTH_ALLOWED" >&2; exit 1; }
echo "images: the runtime ETL image's .pth files are the allowed two"
# examples/minimal, copied, read-only but for a world-writable data/: validated, then ingested, by uid 12345. The
# ingest's files are opened up afterwards so this run's cleanup can remove them.
[ -d "$W/examples/minimal" ] && [ -d "$W/deploy/public" ] \
  || { echo "examples/minimal or deploy/public is missing, so the runtime images cannot be checked" >&2; exit 1; }
MIN="$OUT/minimal"
cp -R "$W/examples/minimal" "$MIN"
chmod -R a+rX "$MIN"
install -d -m 777 "$MIN/data"
HOUSEHOLD=(-e FF_HOME=/h -v "$MIN:/h:ro" -v "$MIN/data:/h/data")
SAID="$("${AS_ANYONE[@]}" "${HOUSEHOLD[@]}" "$ETL_RT_ID" python -m etl validate)" \
  || { printf '%s\n' "$SAID"; echo "uid 12345 could not validate examples/minimal in the runtime ETL image (output above)" >&2; exit 1; }
case "$SAID" in
  "config OK"*) echo "images: uid 12345 validates examples/minimal in the runtime ETL image: $SAID" ;;
  *) printf '%s\n' "$SAID"; echo "python -m etl validate printed no 'config OK' (output above)" >&2; exit 1 ;;
esac
"${AS_ANYONE[@]}" "${HOUSEHOLD[@]}" "$ETL_RT_ID" \
  sh -c 'python -m etl ingest >/h/data/ingest.log 2>&1; rc=$?; find /h/data -mindepth 1 -exec chmod a+rwX {} +; exit $rc' \
  || { cat "$MIN/data/ingest.log" 2>/dev/null; echo "uid 12345 could not ingest examples/minimal in the runtime ETL image (output above)" >&2; exit 1; }
[ -f "$MIN/data/exports/manifest.json" ] \
  || { echo "the ingest of examples/minimal in the runtime ETL image wrote no manifest.json" >&2; exit 1; }
echo "images: uid 12345 ingests it into a world-writable data directory"
# Started in a directory that holds an etl package of its own, the image's code is what Python imports.
mkdir -p "$OUT/decoy/etl"
printf '%s\n' 'raise SystemExit("the etl package of the working directory was imported")' >"$OUT/decoy/etl/__init__.py"
chmod -R a+rX "$OUT/decoy"
SAID="$("${AS_ANYONE[@]}" -v "$OUT/decoy:/w:ro" -w /w "$ETL_RT_ID" python -c 'import etl, os; print(etl.__file__, os.environ["HOME"])')" \
  || { printf '%s\n' "$SAID"; echo "Python in the runtime ETL image failed to import etl from a directory holding one of its own (output above)" >&2; exit 1; }
[ "$SAID" = "/opt/ff/etl/__init__.py /tmp" ] \
  || { echo "from a directory holding an etl package, the runtime ETL image imported etl from, and had HOME at: $SAID (want /opt/ff/etl/__init__.py /tmp)" >&2; exit 1; }
echo "images: from a directory holding an etl package, uid 12345 imports the image's own (/opt/ff/etl), with HOME /tmp"
SAID="$("${AS_ANYONE[@]}" "$ETL_RT_ID" supercronic -version)" \
  || { printf '%s\n' "$SAID"; echo "supercronic does not run in the runtime ETL image (output above)" >&2; exit 1; }
echo "images: supercronic $SAID runs as uid 12345"
# The zones a household's timezone is read in: Python's zoneinfo and supercronic (which reads the zone files itself)
# both find the three the tests use in the image's tzdata.
TZ_ZONES="UTC America/New_York Pacific/Kiritimati"
"${AS_ANYONE[@]}" -e "ZONES=$TZ_ZONES" "$ETL_RT_ID" sh -c 'for z in $ZONES; do [ -f "/usr/share/zoneinfo/$z" ] || exit 1;
  done; python -c "import os, zoneinfo; [zoneinfo.ZoneInfo(z) for z in os.environ[\"ZONES\"].split()]"' \
  || { echo "the runtime ETL image's tzdata lacks one of $TZ_ZONES" >&2; exit 1; }
echo "images: the runtime ETL image's tzdata holds $TZ_ZONES, for Python and for supercronic"
# The scheduler's crontab as compose's start writes it (deploy/make_crontab.py, printing it instead of starting
# supercronic), from examples/minimal's household.yaml in each of those zones with a refresh at minute 25, read by
# supercronic itself (-test: the crontab is valid, its CRON_TZ zone known), as uid 12345.
for zone in $TZ_ZONES; do
  Z="$OUT/zone-${zone//\//-}"
  mkdir -p "$Z/config"
  { grep -v '^timezone:' "$W/examples/minimal/config/household.yaml"
    printf 'timezone: %s\nrefresh: {hours: [17, 1, 9], minute: 25}\n' "$zone"; } >"$Z/config/household.yaml"
  chmod -R a+rX "$Z"
  SAID="$("${AS_ANYONE[@]}" -e FF_CONFIG_DIR=/c -v "$Z/config:/c:ro" "$ETL_RT_ID" \
    sh -c 'python /opt/ff/deploy/make_crontab.py >/tmp/crontab && supercronic -test /tmp/crontab 2>&1 && cat /tmp/crontab')" \
    || { printf '%s\n' "$SAID"; echo "supercronic refused the crontab make_crontab.py wrote in $zone (output above)" >&2; exit 1; }
  WANT="CRON_TZ=$zone
25 1 * * * /opt/ff/deploy/refresh.sh
25 9 * * * /opt/ff/deploy/refresh.sh
25 17 * * * /opt/ff/deploy/refresh.sh"
  case "$SAID" in
    *"crontab is valid"*"$WANT") ;;
    *) printf '%s\n' "$SAID"; echo "the crontab for $zone is not the household's hours in its zone (output above)" >&2; exit 1 ;;
  esac
done
echo "images: make_crontab.py writes, as uid 12345, a crontab supercronic accepts in each of $TZ_ZONES"
# The web image: /srv/public is deploy/public's files and nothing else (built here with the default source, so the
# sign-in page's source link is the tree's own and the page byte for byte the tree's; deploy/source_link.sh changes
# that one attribute for a fork), /usr/share/doc/family-financials the four legal files and nothing else, and no source
# map ships.
"${AS_ROOT[@]}" -v "$W/deploy/public:/want:ro" -v "$LEGAL:/legal:ro" "$WEB_RT_ID" \
  sh -c 'diff -r /want /srv/public || exit 1; diff -r /legal /usr/share/doc/family-financials || exit 1
    maps="$(find /srv -name "*.map")"; [ -z "$maps" ] || { echo "$maps"; exit 1; }' \
  || { echo "the web image's /srv/public is not deploy/public's files, its /usr/share/doc/family-financials not the four legal files, or it ships a source map (above)" >&2; exit 1; }
echo "images: the web image's /srv/public is deploy/public exactly, its /usr/share/doc/family-financials the tree's LICENSE, NOTICE, THIRD_PARTY_LICENSES and THIRD_PARTY_NOTICES.md exactly, and no source map ships"
# Each runtime image names itself, not its base image: the title, vendor, address and documentation labels are the
# project's (the web image's base, Caddy, sets its own), with the licence and the default source.
for named in "$ETL_RT_ID|Family Financials ETL" "$WEB_RT_ID|Family Financials web"; do
  id="${named%%|*}"; title="${named#*|}"
  LABELS="$(docker image inspect --format '{{json .Config.Labels}}' "$id")" \
    || { echo "docker image inspect printed no labels for the runtime image $title" >&2; exit 1; }
  printf '%s' "$LABELS" | python3 -c '
import json, sys
title = sys.argv[1]
source = "https://github.com/Good-Work-Dojo/Family-Financials"
want = {"title": title, "vendor": "Family Financials", "source": source, "url": source, "documentation": source,
        "licenses": "AGPL-3.0-only"}
have = json.load(sys.stdin) or {}
bad = []
for k, v in want.items():
    got = have.get("org.opencontainers.image." + k)
    if got != v:
        bad.append(f"{k}: {got!r}, not {v!r}")
if bad:
    print("\n".join(bad))
sys.exit(1 if bad else 0)' "$title" \
    || { echo "the runtime image $title is not labelled as the project's (above)" >&2; exit 1; }
done
echo "images: each runtime image is labelled as the project's (Family Financials ETL, Family Financials web), not its base image's"
# compose.yaml's health check, with the image's defaults and with a domain (whose certificate cannot be had with no
# network: its port 8080 still answers the container's own address, never with a redirect to HTTPS)
for form in defaults domain; do
  VARS=()
  [ "$form" = defaults ] \
    || VARS=(-e SITE_ADDRESS=example.net -e XDG_DATA_HOME=/tmp/caddy/data -e XDG_CONFIG_HOME=/tmp/caddy/config)
  SAID="$("${AS_ANYONE[@]}" "${VARS[@]}" "$WEB_RT_ID" sh -c 'caddy run --config /etc/caddy/Caddyfile --adapter caddyfile >/tmp/caddy.log 2>&1 &
    for _ in $(seq 1 50); do wget -qO- http://127.0.0.1:8080/healthz 2>/dev/null && exit 0; sleep 0.2; done
    cat /tmp/caddy.log; exit 1')" \
    || { printf '%s\n' "$SAID"; echo "the web image, run as uid 12345 ($form), never answered /healthz on port 8080 (Caddy's log above)" >&2; exit 1; }
  [ "$SAID" = ok ] || { echo "the web image's /healthz ($form) answered '$SAID', not ok" >&2; exit 1; }
  echo "images: the web image, run as uid 12345 ($form), serves /healthz at http://127.0.0.1:8080"
done
# Caddy itself reads deploy/Caddyfile, with the variables at the image's defaults and set for a domain, with no network
# (validate obtains no certificate): it must accept it without a warning, and its JSON must pass tests/test_caddyfile.py.
# One warning is the design's own and allowed: the server on the HTTP port (8080, the http_port a domain's redirects and
# challenges need) serves plain HTTP, so Caddy says it applies no automatic HTTPS to it.
HTTP_ONLY='"msg":"server is listening only on the HTTP port, so no automatic HTTPS will be applied to this server"'
for form in defaults domain; do
  VARS=()
  [ "$form" = defaults ] \
    || VARS=(-e SITE_ADDRESS=example.net -e "FF_HOSTNAMES=dash.example.net alt.example.net" -e TRUSTED_PROXIES=192.0.2.0/24)
  CADDY=("${AS_ANYONE[@]}" "${VARS[@]}" "$WEB_RT_ID" caddy)
  "${CADDY[@]}" validate --config /etc/caddy/Caddyfile --adapter caddyfile >"$OUT/caddy-$form.log" 2>&1 \
    || { cat "$OUT/caddy-$form.log"; echo "caddy validate refused the web server's config ($form; output above)" >&2; exit 1; }
  { "${CADDY[@]}" adapt --config /etc/caddy/Caddyfile --adapter caddyfile >"$OUT/caddy-$form.json" 2>>"$OUT/caddy-$form.log" \
      && [ -s "$OUT/caddy-$form.json" ]; } \
    || { cat "$OUT/caddy-$form.log"; echo "caddy adapt could not read the web server's config ($form; output above)" >&2; exit 1; }
  if grep -E '"level":"(warn|error)"' "$OUT/caddy-$form.log" | grep -vF "$HTTP_ONLY"; then
    echo "Caddy warns about the web server's config ($form; the lines above)" >&2
    exit 1
  fi
  docker run --rm --label "$FF_HARNESS_LABEL" --network none --user root -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$OUT/caddy-$form.json:/caddy.json:ro" -e FF_CADDY_ADAPTED=/caddy.json "$ETL_ID" \
    sh -lc "uv run pytest -q -p no:cacheprovider tests/test_caddyfile.py" \
    || { echo "Caddy's JSON of the web server's config ($form) fails tests/test_caddyfile.py (output above)" >&2; exit 1; }
  echo "images: caddy validates and adapts the web server's config ($form) with no warning but the HTTP port's own, and its JSON passes tests/test_caddyfile.py"
done
# The runtime dependencies' licences (tools/verify/licences.py): the dashboard's npm closure from the lockfile and the
# Python distributions installed in the runtime ETL image just built, read inside it with no network, and the npm
# packages' licence files, read in the web image's build stage with no network, and the Go modules Caddy and
# supercronic are built from, read from each binary's build info as the runtime images hold it (copied out with no
# network) and checked against the texts kept in deploy/licences/go. A licence off the allow-list, one the check
# cannot read, a package with no licence file or a Go module with no kept text fails the gate, and so does a
# THIRD_PARTY_NOTICES.md whose generated block is not those closures, a kept text no program needs, or a
# THIRD_PARTY_LICENSES that is not those files' texts.
echo "== licences: the runtime dependencies, THIRD_PARTY_NOTICES.md and THIRD_PARTY_LICENSES =="
python3 "$W/tools/verify/licences.py" --image "$ETL_RT_ID" --web-image "$WEB_ID" --web-rt-image "$WEB_RT_ID" \
  --label "$FF_HARNESS_LABEL" \
  || { echo "the runtime dependencies' licence check failed (its lines above)" >&2; exit 1; }
SIZES=""
for named in "$HARNESS_ETL_RT $ETL_RT_ID" "$HARNESS_WEB_RT $WEB_RT_ID"; do
  read -r tag id <<<"$named"
  BYTES="$(docker image inspect --format '{{.Size}}' "$id")" && [[ "$BYTES" =~ ^[0-9]+$ ]] \
    || { echo "docker image inspect printed no size for $tag" >&2; exit 1; }
  SIZES="${SIZES:+$SIZES, }$tag $((BYTES / 1000000)) MB"
done
echo "images: $SIZES"

# The secret scan: gitleaks from its image pinned by digest (docker pulls it by that digest when it is absent), run as
# the caller with no network, over what the tree ships. In a checkout that is the files git tracks or would (tracked
# ones and untracked ones no ignore rule covers, as they stand in the work tree), copied into OUT, and then the history
# the checkout's .git holds; in a tree that is no checkout, the tree itself. .gitleaks.toml holds its rules
# (gitleaks's own) and each allowance with its reason. Findings are printed redacted; any fails the gate.
GITLEAKS=ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f
echo "== secrets: gitleaks over the tree =="
[ -f "$W/.gitleaks.toml" ] || { echo "the tree has no .gitleaks.toml, so the secret scan has no rules" >&2; exit 1; }
LEAKS=(docker run --rm --label "$FF_HARNESS_LABEL" --network none --user "$(id -u):$(id -g)")
LEAKS_ARGS=(--config /scan/.gitleaks.toml --redact --no-banner --no-color --exit-code 1)
if [ "$REV" != "not a git checkout" ]; then
  mkdir "$OUT/shipped"
  git -C "$W" ls-files -z --cached --others --exclude-standard \
    | while IFS= read -r -d '' f; do if [ -e "$W/$f" ] || [ -L "$W/$f" ]; then printf '%s\0' "$f"; fi; done \
    | tar -C "$W" --null -T - -cf - | tar -C "$OUT/shipped" -xf - \
    || { echo "the files git tracks could not be copied for the secret scan" >&2; exit 1; }
  "${LEAKS[@]}" -v "$OUT/shipped:/scan:ro" "$GITLEAKS" dir /scan "${LEAKS_ARGS[@]}" \
    || { echo "gitleaks found a secret in the tree's files, or could not scan them (its lines above)" >&2; exit 1; }
  echo "secrets: no secret in the files git tracks or would"
  if [ -d "$W/.git" ]; then
    "${LEAKS[@]}" -v "$W:/scan:ro" "$GITLEAKS" git /scan "${LEAKS_ARGS[@]}" \
      || { echo "gitleaks found a secret in the git history, or could not read it (its lines above)" >&2; exit 1; }
    echo "secrets: no secret in the git history"
  else
    echo "secrets: the history is not scanned here: this checkout's .git is no directory (a linked worktree)"
  fi
else
  "${LEAKS[@]}" -v "$W:/scan:ro" "$GITLEAKS" dir /scan "${LEAKS_ARGS[@]}" \
    || { echo "gitleaks found a secret in the tree, or could not scan it (its lines above)" >&2; exit 1; }
  echo "secrets: no secret in the tree"
fi

# The stack, without exports only (it runs the invented household, never one given): the runtime images just built,
# by their tags (compose names images, never IDs), brought up by the product's own ff, attacked by the security matrix
# and driven by a signed-in browser, then brought down; its last line is `stack: P/T` and its exit status the verdict.
if [ -n "$DEMO" ]; then
  echo "== $(date -Is) stack: the demo stack through ./ff, the security matrix and the browser run =="
  STACK_STARTED=$(date +%s)
  rc=0
  bash "$W/tools/verify/stack.sh" "$OUT/stack" | tee "$OUT/stack.out" || rc=$?
  STACK="$(sed -nE 's/^stack: ([0-9]+\/[0-9]+)$/\1/p' "$OUT/stack.out" | tail -n 1)"
  { [ "$rc" -eq 0 ] && [ -n "$STACK" ]; } || { echo "the stack test FAILED (exit $rc; its lines above)" >&2; exit 1; }
  echo "stack: the stage took $(( $(date +%s) - STACK_STARTED )) s"
  echo "gate: pytest $PYTEST_PASSED passed; vitest $VITEST; contract $CONTRACTS/${#PROFILES[@]}; matrix $MATRICES/$((${#PROFILES[@]} + ${#ZONE_RUNS[@]})); stack $STACK"
fi
echo "== $(date -Is) CI OK =="

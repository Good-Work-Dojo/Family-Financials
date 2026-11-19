#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
# Generates the synthetic demo households and ingests each one offline, in the ETL image.
# Usage: tools/verify/demo.sh <out-dir> [--end YYYY-MM-DD] [<profile>...]
#   <out-dir>   created when absent (once the image is found); must be an empty directory. Each profile lands in
#               <out-dir>/<profile> (an FF_HOME tree) with its exports in <out-dir>/<profile>/data/exports.
#   --end       the last day of the history (default 2026-10-31).
#   <profile>   any of the six demo profiles (default: all).
# Env: FF_ETL_IMAGE  the image (an image id) to run; default the harness's ETL test image (tools/verify/harness.sh), the
#                    ETL image's test target, which holds uv and the tree at /app/finance-app. Every container carries
#                    the harness's label.
# Whose code runs: with FF_ETL_IMAGE set (ci.sh passes the image it has just built from the tree) the image's, which is
# the tree's. Without it (run by hand) the image may be a build behind the tree, so the tree's etl/, tools/demo/,
# starter/ and deploy/ (the engine, the generator, the starter rule files it copies and the file reads the engine
# shares with the store: all that generate and ingest import or read from the tree) are mounted read-only over the
# image's copies; the image's virtualenv stays. The tree is the one holding this script (its real path, links resolved)
# and must hold pyproject.toml and the four directories, else exit 2. The first line says which: `demo: code from the worktree` or `demo: code from the image`.
# One container per profile runs generate and then ingest as the calling user, with no network: the image's
# virtualenv is read-only to every other uid, so HOME and the uv cache sit under /tmp. Prints one line per profile:
#   demo <profile>: N transactions, quality F fail / W warn
# A failing profile prints its failure and the remaining profiles still run. Exit 1 when any profile fails to generate
# or ingest, reports a failed check or leaves no usable exports; 2 on a usage error.
set -euo pipefail
ALL=(full new tiny empty no-investments no-income)
usage() { echo "usage: tools/verify/demo.sh <out-dir> [--end YYYY-MM-DD] [<profile>...]" >&2; exit 2; }
[ "$#" -ge 1 ] || usage
OUT="$1"
shift
END=2026-10-31
PROFILES=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --end) [ "$#" -ge 2 ] || usage; END="$2"; shift 2 ;;
    -*) usage ;;
    *) PROFILES+=("$1"); shift ;;
  esac
done
[ "${#PROFILES[@]}" -gt 0 ] || PROFILES=("${ALL[@]}")
for p in "${PROFILES[@]}"; do
  case " ${ALL[*]} " in *" $p "*) ;; *) echo "unknown profile '$p' (known: ${ALL[*]})" >&2; exit 2 ;; esac
done
[[ "$END" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && date -d "$END" +%F >/dev/null 2>&1 \
  && [ "$(date -d "$END" +%F)" = "$END" ] || { echo "--end '$END' is not a date (YYYY-MM-DD)" >&2; exit 2; }
if [ -e "$OUT" ] && [ ! -d "$OUT" ]; then echo "$OUT is not a directory" >&2; exit 2; fi
CODE=()
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
W="$(cd "$(dirname "$SELF")/../.." && pwd -P)"
if [ -z "${FF_ETL_IMAGE:-}" ]; then
  # --mount takes comma-separated fields, so a comma in the path would change what is mounted
  case "$W" in *,*) echo "demo.sh: the repository path contains a comma: $W" >&2; exit 2 ;; esac
  for f in pyproject.toml etl tools/demo starter deploy; do
    [ -e "$W/$f" ] || { echo "demo.sh: $W holds no $f, so there is no code to mount: run the copy in a repository, or" \
      "set FF_ETL_IMAGE to the image whose code to run" >&2; exit 2; }
  done
  for d in etl tools/demo starter deploy; do
    CODE+=(--mount "type=bind,source=$W/$d,target=/app/finance-app/$d,readonly")
  done
  FROM="the worktree"
else
  FROM="the image"
fi
[ -f "$W/tools/verify/harness.sh" ] || { echo "demo.sh: $W/tools/verify holds no harness.sh, the harness's names" >&2; exit 2; }
# shellcheck source=tools/verify/harness.sh
. "$W/tools/verify/harness.sh"
IMAGE="${FF_ETL_IMAGE:-$HARNESS_ETL_TEST}"
docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "$IMAGE missing - run tools/verify/ci.sh first" >&2; exit 2; }
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
[ -z "$(ls -A "$OUT")" ] || { echo "$OUT is not empty" >&2; exit 2; }

# The container's script: generate, take the clock to ingest at from what generate prints, ingest. Output is shown
# only when a step fails. The profile and the end date arrive as positional parameters.
CONTAINER='
set -eu
gen="$(uv run python -m tools.demo.generate --profile "$1" --end "$2" --out "/out/$1" 2>&1)" \
  || { echo "$gen"; exit 1; }
now="$(printf "%s\n" "$gen" | sed -n "s/^FF_NOW=//p")"
[ -n "$now" ] || { echo "generate printed no FF_NOW"; exit 1; }
ing="$(FF_HOME="/out/$1" FF_NOW="$now" uv run python -m etl ingest 2>&1)" || { echo "$ing"; exit 1; }
'
# Reads the profile's manifest and quality report and prints the summary line; exit 1 on a failed check.
SUMMARY='
import json, sys
profile, exports = sys.argv[1:3]
try:
    count = json.load(open(exports + "/manifest.json"))["transaction_count"]
    checks = json.load(open(exports + "/quality_report.json"))["checks"]
except (OSError, ValueError, KeyError, TypeError) as e:
    print(f"demo {profile}: no usable exports ({e.__class__.__name__})", file=sys.stderr)
    sys.exit(1)
fail = sum(1 for c in checks if c.get("status") == "fail")
warn = sum(1 for c in checks if c.get("status") == "warn")
print(f"demo {profile}: {count} transactions, quality {fail} fail / {warn} warn")
sys.exit(1 if fail else 0)
'
echo "demo: code from $FROM"
FAILED=0
for p in "${PROFILES[@]}"; do
  if ! docker run --rm --label "$FF_HARNESS_LABEL" --network none --user "$(id -u):$(id -g)" \
      -e HOME=/tmp -e UV_CACHE_DIR=/tmp/uv-cache-demo -v "$OUT:/out" "${CODE[@]}" -w /app/finance-app "$IMAGE" \
      sh -c "$CONTAINER" sh "$p" "$END"; then
    echo "demo $p: generate or ingest failed" >&2
    FAILED=1
    continue
  fi
  python3 -c "$SUMMARY" "$p" "$OUT/$p/data/exports" || { echo "demo $p: failed" >&2; FAILED=1; }
done
exit "$FAILED"

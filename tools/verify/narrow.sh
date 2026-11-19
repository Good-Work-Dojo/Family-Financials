#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
# Runs a focused test of the worktree's code in the images tools/verify/ci.sh last built (the harness's test images,
# tools/verify/harness.sh: <prefix>-etl:<test tag>, the ETL image's test target; <prefix>-web:<test tag>, the web
# image's web-build stage), with the worktree mounted read-only, no network and the harness's label. The repository
# root comes from this script's real path (links resolved), never from the
# path it was run by or the caller's directory, and must hold pyproject.toml and this very file.
# Usage: tools/verify/narrow.sh pytest <paths...>   paths relative to the repository root (the ETL test image)
#        tools/verify/narrow.sh vitest <args...>    paths (relative to app/) and vitest options (the web test image;
#                                                   only app/src is mounted)
# Env (vitest only): FF_EXPORTS_DIR  an absolute host path to a directory of SYNTHETIC exports (a demo household's
#                                    data/exports), mounted read-only at /exports
#                    FF_TRUTH        the generator's truth.json, mounted read-only at /truth.json; default truth.json two
#                                    directories above FF_EXPORTS_DIR (the FF_HOME root the generator wrote)
#                    FF_PROFILE      the demo household the exports belong to: a plain name such as `tiny` (letters,
#                                    digits, `_` and `-`, starting with a letter or digit), passed to the container
#                                    when set: the route matrix reads its known gaps by it
#                    FF_TEST_TZ      the process zone the tests run in: a zone name such as `America/New_York`
#                                    (starting with a letter; letters, digits, `_`, `+` and `-` in parts joined by
#                                    `/`), passed to the container when set: app/vite.config.ts sets the test
#                                    process's TZ from it, UTC when it is unset
#   With FF_EXPORTS_DIR the container gets FF_EXPORTS_DIR=/exports FF_TRUTH=/truth.json FF_REQUIRE_EXPORTS=1, so the
#   data-gated suites run instead of skipping. Exports mounted this way are synthetic by contract: a path at, inside or
#   above the real-data scratch root ($FF_SCRATCH, else the harness's scratch directory in the account's home directory
#   from the account database, never $HOME; the root ci.sh uses) is refused, and so is one at, inside or above that default root when
#   FF_SCRATCH points elsewhere, and a truth file in either.
# Exit 2 on a usage error, a missing image, a refused path, an FF_PROFILE or FF_TEST_TZ of another shape, or a script
# outside a repository; otherwise the test run's own status.
set -euo pipefail
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
W="$(cd "$(dirname "$SELF")/../.." && pwd -P)"
if [ ! -f "$W/pyproject.toml" ] || [ "$(readlink -f "$W/tools/verify/narrow.sh" 2>/dev/null)" != "$SELF" ]; then
  echo "narrow.sh: $SELF does not sit at tools/verify/ of a repository holding pyproject.toml" >&2
  exit 2
fi
# --mount takes comma-separated fields, so a comma in the path would change what is mounted.
case "$W" in *,*) echo "narrow.sh: the repository path contains a comma: $W" >&2; exit 2 ;; esac
# shellcheck source=tools/verify/harness.sh
. "$W/tools/verify/harness.sh"
usage() { echo "usage: tools/verify/narrow.sh pytest|vitest <paths...>" >&2; exit 2; }
[ "$#" -ge 2 ] || usage
KIND="$1"
shift
case "$KIND" in
  pytest) IMAGE="$HARNESS_ETL_TEST" ;;
  vitest) IMAGE="$HARNESS_WEB_TEST" ;;
  *) usage ;;
esac
# The real-data scratch roots exactly as ci.sh reads them (harness.sh's harness_scratch_roots: $FF_SCRATCH, else the
# harness's scratch directory in the account's home directory, and that default root whatever FF_SCRATCH says).
# synthetic_only refuses a resolved path ($2, named $1) at, inside or above either root (a directory above one would
# carry the real data into the container), or holding a comma (--mount takes comma-separated fields).
HARNESS_WHO=narrow.sh
synthetic_only() {
  harness_refuse_real "$1" "$2"
  case "$2" in *,*) echo "narrow.sh: $1 contains a comma: $2" >&2; exit 2 ;; esac
}
DATA=()
if [ "$KIND" = vitest ] && [ -n "${FF_EXPORTS_DIR:-}" ]; then
  case "$FF_EXPORTS_DIR" in /*) ;; *) echo "narrow.sh: FF_EXPORTS_DIR must be an absolute path (got '$FF_EXPORTS_DIR')" >&2; exit 2 ;; esac
  harness_scratch_roots
  EXPORTS="$(realpath -e "$FF_EXPORTS_DIR" 2>/dev/null)" && [ -d "$EXPORTS" ] \
    || { echo "narrow.sh: FF_EXPORTS_DIR $FF_EXPORTS_DIR is not a directory" >&2; exit 2; }
  synthetic_only FF_EXPORTS_DIR "$EXPORTS"
  [ -f "$EXPORTS/manifest.json" ] || { echo "narrow.sh: no manifest.json in $EXPORTS" >&2; exit 2; }
  RAW_TRUTH="${FF_TRUTH:-$(dirname "$(dirname "$EXPORTS")")/truth.json}"
  case "$RAW_TRUTH" in /*) ;; *) echo "narrow.sh: FF_TRUTH must be an absolute path (got '$RAW_TRUTH')" >&2; exit 2 ;; esac
  TRUTH="$(realpath -e "$RAW_TRUTH" 2>/dev/null)" && [ -f "$TRUTH" ] \
    || { echo "narrow.sh: no truth file at $RAW_TRUTH (set FF_TRUTH to the generator's truth.json)" >&2; exit 2; }
  synthetic_only FF_TRUTH "$TRUTH"
  DATA=(--mount "type=bind,source=$EXPORTS,target=/exports,readonly"
        --mount "type=bind,source=$TRUTH,target=/truth.json,readonly"
        -e FF_EXPORTS_DIR=/exports -e FF_TRUTH=/truth.json -e FF_REQUIRE_EXPORTS=1)
fi
if [ "$KIND" = vitest ] && [ -n "${FF_PROFILE:-}" ]; then
  [[ "$FF_PROFILE" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] \
    || { echo "narrow.sh: FF_PROFILE must be a plain name such as tiny (got '$FF_PROFILE')" >&2; exit 2; }
  DATA+=(-e "FF_PROFILE=$FF_PROFILE")
fi
if [ "$KIND" = vitest ] && [ -n "${FF_TEST_TZ:-}" ]; then
  [[ "$FF_TEST_TZ" =~ ^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$ ]] \
    || { echo "narrow.sh: FF_TEST_TZ must be a zone name such as America/New_York (got '$FF_TEST_TZ')" >&2; exit 2; }
  DATA+=(-e "FF_TEST_TZ=$FF_TEST_TZ")
fi
docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "$IMAGE missing - run tools/verify/ci.sh first" >&2; exit 2; }
# --mount, unlike -v, fails on a missing source instead of creating it.
if [ "$KIND" = pytest ]; then
  # The image installs the project from its own copy at /app/finance-app; PYTHONPATH puts the worktree first, so
  # etl and the deploy and tools helpers the tests import are the worktree's.
  exec docker run --rm --label "$FF_HARNESS_LABEL" --network none --user root \
    --mount "type=bind,source=$W,target=/w,readonly" -w /w -e PYTHONPATH=/w "$IMAGE" \
    uv run --project /app/finance-app pytest -q -p no:cacheprovider "$@"
fi
# app/src is mounted over the image's copy, so the sources under test are the worktree's; the vitest config,
# package.json and node_modules come from the image. The paths reach the container's shell as its positional
# parameters, never as text inside the command string.
exec docker run --rm --label "$FF_HARNESS_LABEL" --network none \
  --mount "type=bind,source=$W/app/src,target=/app/finance-app/app/src,readonly" "${DATA[@]}" "$IMAGE" \
  sh -lc 'cd app && npx vitest run "$@"' narrow "$@"

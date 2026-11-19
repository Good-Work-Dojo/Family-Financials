#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
# Builds the demo site: the dashboard built with VITE_DEMO=1 (app/src/lib/demo.ts) over the invented household `full`,
# a static site with relative addresses and no server behind it, which .github/workflows/pages.yml publishes.
# Usage: tools/verify/demo_site.sh <out-dir> [--exports DIR]
#   <out-dir>   created when absent; must be an empty directory. It ends holding public/ (the staged public directory:
#               the exports, the household's family documents, the icons and a relative manifest,
#               tools/verify/demo_site.py stage) and site/ (the built site, which tools/verify/demo_site.py check has
#               passed).
#   --exports   the exports of `full` generated at its blessed end, in the generator's layout (<home>/data/exports, its
#               family documents in <home>/state/family; ci.sh passes the ones it has just made); without
#               it, `full` is generated and ingested offline into <out-dir>/demo (tools/verify/demo.sh, at its default
#               end, the generator's blessed one). A path at, inside or above a real-data scratch root ($FF_SCRATCH, or
#               the harness's default root whatever FF_SCRATCH says), as given or as resolved, is refused, and so is
#               a family documents' folder beside it that resolves there.
# Env: FF_ETL_IMAGE  the image demo.sh generates `full` in (default: demo.sh's own, the harness's ETL test image)
#      FF_WEB_IMAGE  the image the site is built in, the web image's web-build stage, with the dashboard's code and
#                    its node_modules (default: the harness's web test image, tools/verify/harness.sh); its own code is
#                    what is built
#      VITE_SOURCE_URL  where the About card says the source is, when set (app/src/lib/product.ts)
# The build runs with no network, as root inside the container (vite writes node_modules/.vite-temp), and hands
# site/ back to the calling user; the container carries the harness's label. Prints the staging line, the build's
# summary and the check's line: `demo site: N files; …`. Exit 1 when generating, staging, building or the check fails;
# 2 on a usage error.
set -euo pipefail
usage() { echo "usage: tools/verify/demo_site.sh <out-dir> [--exports DIR]" >&2; exit 2; }
[ "$#" -ge 1 ] || usage
OUT="$1"
shift
EXPORTS=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --exports) [ "$#" -ge 2 ] || usage; EXPORTS="$2"; shift 2 ;;
    *) usage ;;
  esac
done
W="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd -P)"
# shellcheck source=tools/verify/harness.sh
. "$W/tools/verify/harness.sh"
# Exports given are an invented household's: the path, as given and as resolved, and the family documents' folder
# beside it, must lie neither at, inside nor above a real-data scratch root (harness.sh's harness_refuse_real; the
# site is published).
HARNESS_WHO=demo_site.sh
if [ -n "$EXPORTS" ]; then
  harness_scratch_roots
  SPELT="$(realpath -m -s "$EXPORTS")"
  RESOLVED="$(realpath -e "$EXPORTS" 2>/dev/null)" && [ -f "$RESOLVED/manifest.json" ] \
    || { echo "--exports names no exports directory with a manifest.json" >&2; exit 2; }
  harness_refuse_real --exports "$RESOLVED" "$SPELT"
  EXPORTS="$RESOLVED"
  FAMILY_SPELT="$(dirname "$(dirname "$EXPORTS")")/state/family"
  if [ -e "$FAMILY_SPELT" ] || [ -L "$FAMILY_SPELT" ]; then
    harness_refuse_real "the family documents' folder" "$(realpath -m "$FAMILY_SPELT")" "$FAMILY_SPELT"
  fi
fi
IMAGE="${FF_WEB_IMAGE:-$HARNESS_WEB_TEST}"
docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "$IMAGE missing - run tools/verify/ci.sh first" >&2; exit 2; }
if [ -e "$OUT" ] && [ ! -d "$OUT" ]; then echo "$OUT is not a directory" >&2; exit 2; fi
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd -P)"
[ -z "$(ls -A "$OUT")" ] || { echo "$OUT is not empty" >&2; exit 2; }

if [ -z "$EXPORTS" ]; then
  bash "$W/tools/verify/demo.sh" "$OUT/demo" full || { echo "demo site: generating the household full failed" >&2; exit 1; }
  EXPORTS="$OUT/demo/full/data/exports"
fi
# the household's family documents, where the generator writes them (<home>/state/family beside <home>/data/exports):
# the demo starts from them
STATE=()
FAMILY="$(dirname "$(dirname "$EXPORTS")")/state/family"
[ ! -d "$FAMILY" ] || STATE=(--state "$FAMILY")
python3 "$W/tools/verify/demo_site.py" stage "$OUT/public" --exports "$EXPORTS" "${STATE[@]}" --root "$W" \
  || { echo "demo site: staging the public directory failed" >&2; exit 1; }
SOURCE=()
[ -z "${VITE_SOURCE_URL:-}" ] || SOURCE=(-e "VITE_SOURCE_URL=$VITE_SOURCE_URL")
mkdir "$OUT/site"
docker run --rm --label "$FF_HARNESS_LABEL" --network none -v "$OUT/public:/public:ro" -v "$OUT/site:/out" \
  -e VITE_DEMO=1 -e FF_DEMO_PUBLIC=/public -e "OWNER=$(id -u):$(id -g)" "${SOURCE[@]}" "$IMAGE" \
  sh -c 'cd app && npx vite build --outDir /out --emptyOutDir --logLevel warn; rc=$?; chown -R "$OWNER" /out; exit $rc' \
  || { echo "demo site: the build failed (its output above)" >&2; exit 1; }
CHECK=()
[ -z "${VITE_SOURCE_URL:-}" ] || CHECK=(--source "$VITE_SOURCE_URL")
python3 "$W/tools/verify/demo_site.py" check "$OUT/site" --exports "$EXPORTS" "${STATE[@]}" --root "$W" "${CHECK[@]}" \
  || { echo "demo site: the built site fails its checks (the lines above)" >&2; exit 1; }

#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
# Screenshot every route of the dashboard against a scratch exports dir at 390x844 and
# 1280x900, dark and light, plus a per-route horizontal-overflow check. Every page is shot at the exports' ingest
# instant (its Date fixed at manifest.json generated_at; a manifest without one fails the run), so neither the shots
# nor the overflow report depend on the day they are taken, in the household's zone (assumptions.json
# household.timezone; UTC, with a line saying so, when it states none), once the fonts are ready (at most 5 s; later
# is an overflow entry). At the phone viewport each theme adds one shot of Home with the navigation drawer open,
# root_drawer__390x844__<theme>.png, and a route the driver's CLICK_SHOTS names is shot once more for each of its
# clicked shots, with their controls clicked (Settings with the "Add an asset" form open,
# settings_add_asset__<WxH>__<theme>.png, and with the AI categorization dialog open,
# settings_ai_dialog__<WxH>__<theme>.png): (routes + clicked shots) x 2 viewports x 2 themes + 2 PNGs (19 routes:
# 76 + 8 + 2 = 86).
# The static site answers the store documents the app reads at /api/* as an idle, healthy stack's store does
# (tools/verify/fixtures/, each tied to deploy/scenario_store.py by test_screenshots_driver.py): /api/prefs the empty
# preferences, /api/answers and /api/manual-assets the empty documents (or a demo household's own, FF_SHOT_STATE
# below), /api/refresh an idle status whose schedule is the default hours in the household's zone, /api/scenarios
# the synthetic second-house scenarios. Nothing is ever written to them (nginx serves files; a PUT is refused).
# Usage: tools/verify/screenshots.sh <label> <exports-dir>
#   <label>  matches ^[a-z0-9][a-z0-9._-]*$: it names the directories that are created and cleared
# Exports are treated as real: <exports-dir> must resolve strictly inside the scratch root ($FF_SCRATCH, default the
# harness's scratch directory, tools/verify/harness.sh, in the account's home directory from the account database,
# never $HOME), and every shot is taken with
# privacy mode on (FF_SHOT_PRIVATE=1 is forced), so the files at rest are masked. No PNG of real exports is opened:
# such a run is judged by its exit status and overflow.json only.
#   FF_DEMO_EXPORTS=1 marks synthetic exports: the scratch-root rule and forced masking are lifted and FF_SHOT_PRIVATE
#   is taken as given (1 shoots masked, 0 or unset unmasked; nothing else is accepted). The flag never overrides the
#   location: exports at or inside a real-data scratch root ($FF_SCRATCH, or the default root whatever FF_SCRATCH says),
#   as spelt or as resolved, are refused, and so are exports whose top-level JSON files resolve into one (they are
#   copied file by file, links followed).
#   FF_SHOT_OUT=<absolute dir>, with FF_DEMO_EXPORTS=1 only, takes the PNGs and overflow.json instead of
#   <scratch root>/<label>/shots: it must resolve (symlinks followed) outside the repository and outside both real-data
#   scratch roots - neither at, inside nor above any - and be absent (it is made once every check has passed) or an
#   empty directory the caller
#   owns and can write to; this script never clears a directory it was pointed at, so a non-empty one is refused. The
#   built SPA stays under <scratch root>/<label>/site. With real exports a set FF_SHOT_OUT, even an empty one, is
#   refused.
#   FF_SHOT_STATE=1, with FF_DEMO_EXPORTS=1 only, serves the state of the demo tree these exports belong to (the
#   generator's layout: <tree>/data/exports, <tree>/truth.json, <tree>/state/family): its answers and manual assets (the file
#   names are the driver's STATE_DOCUMENTS), each when present, are served as /api/answers and /api/manual-assets
#   instead of the empty documents. The variable takes no path - any other value is refused - so it can never name a
#   real household's state, and the tree must hold truth.json, the generator's mark. A tree with no state directory is
#   served the empty documents, with a line saying so. The state directory must resolve, as spelt and as resolved,
#   outside both real-data scratch roots (a household's state there is real data) and be no link; each file is copied
#   with its links followed, so one that does not resolve to a file, resolves into a root or outside the state
#   directory is refused, and so is one the store refuses (its own check, deploy/scenario_store.py). With real exports
#   a set FF_SHOT_STATE, even an empty one, is refused: their documents are always the empty ones. Every refusal exits
#   2, before anything is created.
#   FF_SHOT_VIEWPORTS="360x780 1100x800" overrides the two viewports for one run (the gate never sets it); every
#   viewport narrower than 768 (the app's phone cut) gets the drawer shots
#   FF_SHOT_QUERY="/spending/reports?measure=count&chart=table" shoots a route WITH a query (one per route, a route
#   the run shoots, never a REDIRECTS source); its PNG is <route>_query__... (the gate never sets it)
#   -> <scratch root>/<label>/shots/<route>__<WxH>__<theme>.png, root_drawer__<WxH>__<theme>.png and overflow.json
#      (or FF_SHOT_OUT/...)
# Usage: FF_SHOT_NOTHING_BUILT=1 tools/verify/screenshots.sh <label>
#   shoots the page a household sees before any export exists (sections/FirstRun.tsx in its "nothing built" state,
#   which the data loader shows when manifest.json is absent): the site serves the built dashboard, the five store
#   documents and no export at all. The mode takes the label alone - a second argument is refused - and never reads an
#   exports directory; the built site under <scratch root>/<label>/site is the only directory it serves, and a build
#   that carried an export file into it fails the run. The variable takes 1 and nothing else, and FF_SHOT_STATE (any
#   value), FF_SHOT_ROUTES and FF_SHOT_QUERY are refused with it. There is no household in it, so FF_SHOT_OUT is taken
#   (the same location rules as above) and FF_SHOT_PRIVATE is taken as given; FF_DEMO_EXPORTS is not read. The page is
#   shot at each viewport in both themes once its heading is on screen, at a fixed instant in UTC (no manifest names
#   an ingest instant or a zone): viewports x 2 themes PNGs, nothing_built__<WxH>__<theme>.png, and overflow.json,
#   where a 404 for an export file is what the page is about and every other problem counts as above.
# No login: nginx serves the built SPA plus the 14 exports and the five documents directly (no Caddy gate).
# The SPA is built from the WORKTREE's app/src inside the harness's web test image (the web-build stage), pinned
# by its ID for the whole run (node_modules, package.json and vite.config.ts come from the image), so no npm on the host.
# Exit 1 when: the PAGES manifest parses to 0 routes; a document is not served; the PNG count is not (routes + their
# clicked shots) x viewports (2 unless FF_SHOT_VIEWPORTS) x 2 themes, plus 2 per phone viewport when the run shoots
# Home; or overflow.json is not empty - any shot (a route, a clicked shot or the open drawer, at a viewport in a theme) that scrolls
# horizontally or has an element reaching past the viewport, threw a page error, made a request that failed or
# answered >= 400 (/api/* included), logged a console error (the page "rendered" a handled load error) or found the
# fonts not ready in time. An empty overflow.json is the gate rule, so overflow is a failure here, not a baseline;
# a FF_SHOT_ROUTES subset run applies the same rule to the routes it shoots (the drawer only when it shoots /).
set -euo pipefail
USAGE="usage: screenshots.sh <label> <exports-dir>, or FF_SHOT_NOTHING_BUILT=1 screenshots.sh <label>"
LABEL="${1:-}"
# FF_SHOT_NOTHING_BUILT=1: the page shown before any export exists, from the label alone (the header)
NOTHING_BUILT=""
if [ -n "${FF_SHOT_NOTHING_BUILT+set}" ]; then
  [ "$FF_SHOT_NOTHING_BUILT" = "1" ] \
    || { echo "FF_SHOT_NOTHING_BUILT takes 1 (shoot the page shown before any export exists), not '$FF_SHOT_NOTHING_BUILT'" >&2; exit 2; }
  NOTHING_BUILT=1
  [ -n "$LABEL" ] && [ "$#" -eq 1 ] \
    || { echo "FF_SHOT_NOTHING_BUILT=1 serves no exports and reads no directory: pass the label alone ($USAGE)" >&2; exit 2; }
  for v in FF_SHOT_STATE FF_SHOT_ROUTES FF_SHOT_QUERY; do
    if { [ "$v" = FF_SHOT_STATE ] && [ -n "${FF_SHOT_STATE+set}" ]; } || [ -n "${!v:-}" ]; then
      echo "$v is not taken with FF_SHOT_NOTHING_BUILT=1: the run shoots the one page shown before any export exists" >&2
      exit 2
    fi
  done
else
  [ -n "$LABEL" ] && [ -n "${2:-}" ] || { echo "$USAGE" >&2; exit 2; }
fi
[[ "$LABEL" =~ ^[a-z0-9][a-z0-9._-]*$ ]] \
  || { echo "label must match ^[a-z0-9][a-z0-9._-]*\$ (it names directories that are created and cleared)" >&2; exit 2; }
EXPORTS=""
SPELT=""
if [ -z "$NOTHING_BUILT" ]; then
  EXPORTS="$(realpath -e "$2" 2>/dev/null)" && [ -d "$EXPORTS" ] || { echo "no exports directory at $2" >&2; exit 2; }
  # the exports dir as spelt, symlinks not followed: a link placed inside a real-data root names that root too
  SPELT="$(realpath -m -s "$2")"
fi
W="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=tools/verify/harness.sh
. "$W/tools/verify/harness.sh"
HOME_DIR="$(getent passwd "$(id -u)" | cut -d: -f6)"
[ -n "$HOME_DIR" ] || { echo "cannot determine the home directory from the account database" >&2; exit 2; }
# The scratch root must be absolute and must be neither /, the home directory nor above it: shots are created and
# cleared under it.
V="${FF_SCRATCH:-$HOME_DIR/$FF_HARNESS_SCRATCH}"
case "$V" in /*) ;; *) echo "FF_SCRATCH must be an absolute path (got '$V')" >&2; exit 2 ;; esac
SCRATCH="$(realpath -m "$V")"
HOME_REAL="$(realpath -m "$HOME_DIR")"
if [ "$SCRATCH" = / ] || case "$HOME_REAL/" in "$SCRATCH"/*) true ;; *) false ;; esac; then
  echo "FF_SCRATCH must not be /, the home directory or a directory above it (got '$V')" >&2
  exit 2
fi
V="$SCRATCH"
# The default root holds real data whatever FF_SCRATCH says: both are real-data scratch roots.
DEFAULT_SCRATCH="$(realpath -m "$HOME_DIR/$FF_HARNESS_SCRATCH")"
# at_or_under A B: the resolved path A is B or lies under it (B may be /; the slash sits inside the quotes because a
# quoted expansion that comes out empty, `"${2%/}"/*`, matches nothing in a case pattern)
at_or_under() { case "$1/" in "${2%/}/"*) true ;; *) false ;; esac; }
STATE=""                                                       # the demo tree's state dir, resolved, with FF_SHOT_STATE=1
NO_STATE=""                                                    # 1: FF_SHOT_STATE=1 on a demo tree with no state dir
if [ -n "$NOTHING_BUILT" ]; then
  # no exports and no state: nothing of a household's is read, so the exports' rules have nothing to judge, and the
  # shots may leave the scratch root (FF_SHOT_OUT, checked below like a demo run's)
  :
elif [ "${FF_DEMO_EXPORTS:-}" = "1" ]; then
  # The flag never overrides the location: exports at or inside a real-data scratch root, as spelt or as resolved, are
  # real.
  for root in "$SCRATCH" "$DEFAULT_SCRATCH"; do
    for p in "$EXPORTS" "$SPELT"; do
      if at_or_under "$p" "$root"; then
        echo "the exports dir $p lies at or inside the real-data scratch root $root: FF_DEMO_EXPORTS=1 never makes exports there synthetic" >&2
        exit 2
      fi
    done
  done
  # The JSON files are copied one by one with their links followed, so none may resolve into a real-data root.
  for f in "$EXPORTS"/*.json; do
    [ -e "$f" ] || [ -L "$f" ] || continue                     # the pattern matched nothing
    TARGET="$(realpath -e "$f" 2>/dev/null)" || { echo "the exports file $f does not resolve to a file" >&2; exit 2; }
    for root in "$SCRATCH" "$DEFAULT_SCRATCH"; do
      if at_or_under "$TARGET" "$root"; then
        echo "the exports file $f resolves to $TARGET, at or inside the real-data scratch root $root: FF_DEMO_EXPORTS=1 never copies real data" >&2
        exit 2
      fi
    done
  done
  # FF_SHOT_STATE=1 serves the state that belongs to these demo exports, in the generator's tree: <tree>/data/exports,
  # <tree>/truth.json (its mark) and <tree>/state/family. The variable takes no path, so no value of it can name a real
  # household's state (a frozen copy of real inputs, a live store's folder: the store's shape exactly); a tree without
  # truth.json is no demo tree. The derived state directory then gets the exports' location rules: as resolved and as
  # spelt outside both real-data roots, no link itself, and each state file it serves, copied with its links followed,
  # resolving to a file inside that directory (so outside the roots) and holding a document the store takes.
  if [ -n "${FF_SHOT_STATE+set}" ]; then
    [ "$FF_SHOT_STATE" = "1" ] \
      || { echo "FF_SHOT_STATE takes 1 (serve the state of the demo tree these exports belong to), not '$FF_SHOT_STATE'" >&2; exit 2; }
    TREE="$(dirname "$(dirname "$EXPORTS")")"
    [ -f "$TREE/truth.json" ] \
      || { echo "FF_SHOT_STATE serves a demo household's own state: no truth.json beside these exports ($TREE)" >&2; exit 2; }
    DOCS="$TREE/state/family"                                  # where the generator writes the family documents
    if [ ! -e "$DOCS" ] && [ ! -L "$DOCS" ]; then
      NO_STATE=1                                               # a profile with no state: the empty documents
    else
      STATE="$(realpath -e "$DOCS" 2>/dev/null)" && [ -d "$STATE" ] \
        || { echo "FF_SHOT_STATE: the demo tree's state is not a directory: $DOCS" >&2; exit 2; }
      STATE_SPELT="$(realpath -m -s "$DOCS")"
      for root in "$SCRATCH" "$DEFAULT_SCRATCH"; do
        for p in "$STATE" "$STATE_SPELT"; do
          if at_or_under "$p" "$root"; then
            echo "FF_SHOT_STATE: the state dir $p lies at or inside the real-data scratch root $root: a household's state there is real data" >&2
            exit 2
          fi
        done
      done
      # the generator writes a real directory and no link: a link could serve another household's state from anywhere
      [ ! -L "$DOCS" ] \
        || { echo "FF_SHOT_STATE: the demo tree's state directory is a link: $DOCS (a demo tree's state is never one)" >&2; exit 2; }
      # the documents' file names come from the driver's table, the one place they are written
      STATE_FILES="$(FF_SHOT_VIEWPORTS="" FF_SHOT_PRIVATE="" python3 -B - "$W" <<'PY'
import sys
sys.path.insert(0, sys.argv[1] + "/tools/verify")
import screenshots_driver as d
print(" ".join(d.state_files()))
PY
)" && [ -n "$STATE_FILES" ] || { echo "FF_SHOT_STATE: the driver names no state document" >&2; exit 2; }
      for name in $STATE_FILES; do
        f="$STATE/$name"
        [ -e "$f" ] || [ -L "$f" ] || continue                 # the household has no such document
        TARGET="$(realpath -e "$f" 2>/dev/null)" && [ -f "$TARGET" ] \
          || { echo "FF_SHOT_STATE: the state file $f does not resolve to a file" >&2; exit 2; }
        for root in "$SCRATCH" "$DEFAULT_SCRATCH"; do
          if at_or_under "$TARGET" "$root"; then
            echo "FF_SHOT_STATE: the state file $f resolves to $TARGET, at or inside the real-data scratch root $root: real data is never served" >&2
            exit 2
          fi
        done
        # nor anywhere else outside the tree's own state: a link out of it could serve another household's document
        at_or_under "$TARGET" "$STATE" \
          || { echo "FF_SHOT_STATE: the state file $f resolves to $TARGET, outside the demo tree's state directory $STATE: a link out of it is never served" >&2; exit 2; }
      done
      # and each document there must be one the store takes (the driver runs the store's own check; the two variables
      # it reads on import are left to the count below, which names them)
      FF_SHOT_VIEWPORTS="" FF_SHOT_PRIVATE="" python3 -B - "$W" "$STATE" <<'PY' || exit 2
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/tools/verify")
import screenshots_driver as d
msg = d.state_problem(Path(sys.argv[2]))
if msg:
    print(msg, file=sys.stderr)
    sys.exit(2)
PY
    fi
  fi
else
  if [ -n "${FF_SHOT_OUT+set}" ]; then
    echo "FF_SHOT_OUT is for synthetic exports only (FF_DEMO_EXPORTS=1): shots of real exports stay under the scratch root $SCRATCH" >&2
    exit 2
  fi
  if [ -n "${FF_SHOT_STATE+set}" ]; then
    echo "FF_SHOT_STATE is for synthetic exports only (FF_DEMO_EXPORTS=1): real exports are shot with the empty answers and manual assets" >&2
    exit 2
  fi
  # Strictly inside: a descendant of the resolved root, never the root itself; symlinks are resolved first.
  case "$EXPORTS" in
    "$SCRATCH"/?*) ;;
    *) echo "the exports dir must resolve strictly inside the scratch root $SCRATCH (got $EXPORTS); FF_DEMO_EXPORTS=1 is for synthetic exports only" >&2; exit 2 ;;
  esac
  if [ "${FF_SHOT_PRIVATE:-}" != "1" ]; then
    echo "== exports are treated as real: FF_SHOT_PRIVATE=1 forced =="
  fi
  FF_SHOT_PRIVATE=1
fi
OUT="$V/$LABEL/shots"
MAKE_OUT=""
if [ -n "${FF_SHOT_OUT+set}" ]; then
  case "$FF_SHOT_OUT" in /*) ;; *) echo "FF_SHOT_OUT must be an absolute path (got '$FF_SHOT_OUT')" >&2; exit 2 ;; esac
  OUT="$(realpath -m "$FF_SHOT_OUT")"
  REPO="$(realpath -e "$W")"
  if at_or_under "$OUT" "$REPO" || at_or_under "$REPO" "$OUT"; then
    echo "FF_SHOT_OUT must lie outside the repository $REPO, neither at, inside nor above it (got $OUT)" >&2
    exit 2
  fi
  for root in "$SCRATCH" "$DEFAULT_SCRATCH"; do
    if at_or_under "$OUT" "$root" || at_or_under "$root" "$OUT"; then
      echo "FF_SHOT_OUT must lie outside the real-data scratch root $root, neither at, inside nor above it (got $OUT)" >&2
      exit 2
    fi
  done
  # The driver's container runs as root and hands /shots to the caller when it ends: the directory must be the
  # caller's own, writable, and empty. One that does not exist yet is made once every check below has passed, so a
  # refusal leaves nothing behind; the nearest directory that exists above it must take a new entry.
  if [ -e "$OUT" ]; then
    [ -d "$OUT" ] || { echo "FF_SHOT_OUT exists and is not a directory: $OUT" >&2; exit 2; }
    [ -O "$OUT" ] && [ -w "$OUT" ] \
      || { echo "FF_SHOT_OUT must be a directory you own and can write to: $OUT" >&2; exit 2; }
    [ -z "$(find "$OUT" -mindepth 1 -maxdepth 1 -print -quit)" ] \
      || { echo "FF_SHOT_OUT is not empty: $OUT (nothing here clears a directory it was pointed at)" >&2; exit 2; }
  else
    ABOVE="$(dirname "$OUT")"
    while [ ! -e "$ABOVE" ]; do ABOVE="$(dirname "$ABOVE")"; done
    [ -d "$ABOVE" ] && [ -w "$ABOVE" ] && [ -x "$ABOVE" ] \
      || { echo "FF_SHOT_OUT cannot be created: $OUT" >&2; exit 2; }
    MAKE_OUT=1
  fi
fi
SITE="$V/$LABEL/site"
PORT="${FF_SHOT_PORT:-18201}"
# ONE version drives both the image (whose bundled browsers must match) and the pip package.
PW="${FF_PW_VERSION:-1.46.0}"
PW_IMAGE="mcr.microsoft.com/playwright/python:v${PW}-jammy"
NGINX="$FF_HARNESS_PROJECT_PREFIX-shots-$LABEL-$$"
UIDGID="$(id -u):$(id -g)"
[ -n "$NOTHING_BUILT" ] || [ -f "$EXPORTS/manifest.json" ] || { echo "no manifest.json in $EXPORTS" >&2; exit 2; }
# The image is pinned by ID, so a ci.sh run retagging the web test image meanwhile cannot change what this run builds with.
WEB_ID="$(docker image inspect --format '{{.Id}}' "$HARNESS_WEB_TEST" 2>/dev/null)" && [ -n "$WEB_ID" ] \
  || { echo "$HARNESS_WEB_TEST missing - run tools/verify/ci.sh first" >&2; exit 2; }

# Parse the routes with the driver's own parser (it imports playwright lazily, so the host
# can import it) - fail before building anything if the PAGES manifest yields nothing. The count is the driver's own
# rule too (expected_shots: every route and each of its clicked shots at every viewport in both themes, plus the open
# drawer at each phone viewport in both themes when Home is shot; with FF_SHOT_NOTHING_BUILT=1, nothing_built_shots:
# the one page at every viewport in both themes).
# A malformed FF_SHOT_VIEWPORTS exits here, with the driver's own message, before anything is built.
COUNTS="$(FF_SHOT_VIEWPORTS="${FF_SHOT_VIEWPORTS:-}" FF_SHOT_PRIVATE="${FF_SHOT_PRIVATE:-}" python3 -B - "$W" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/tools/verify")
import screenshots_driver as d
routes = d.routes_from_app_tsx((Path(sys.argv[1]) / "app/src/App.tsx").read_text())
print(len(routes), d.expected_shots(routes, d.VIEWPORTS), len(d.VIEWPORTS) * len(d.THEMES), len(d.VIEWPORTS),
      len(d.drawer_viewports(d.VIEWPORTS)), d.nothing_built_shots(d.VIEWPORTS), len(d.click_shots(routes)),
      " ".join(d.VIEWPORTS))
PY
)"
read -r NROUTES EXPECT PER_ROUTE NVIEWPORTS PHONES NOTHING_EXPECT NCLICKED SHOT_VIEWPORTS <<<"$COUNTS"
[ "${NROUTES:-0}" -gt 0 ] || { echo "parsed 0 routes from the PAGES manifest in $W/app/src/App.tsx" >&2; exit 1; }
if [ -n "$NOTHING_BUILT" ]; then
  EXPECT="$NOTHING_EXPECT"
  echo "== the page shown before any export exists -> expecting $EXPECT screenshots ($NVIEWPORTS viewports x 2 themes) =="
else
  SHAPE="$NROUTES x $NVIEWPORTS viewports x 2 themes"
  if [ "$NCLICKED" -gt 0 ]; then
    SHAPE="$SHAPE, plus $NCLICKED shot(s) of a route with a control clicked x $NVIEWPORTS viewports x 2 themes"
  fi
  if [ "$PHONES" -gt 0 ]; then
    if [ "$PHONES" -eq 1 ]; then PV="1 phone viewport"; else PV="$PHONES phone viewports"; fi
    SHAPE="$SHAPE, plus the open drawer at $PV x 2 themes"
  fi
  echo "== $NROUTES routes -> expecting $EXPECT screenshots ($SHAPE) =="
fi
# say whether the shots are masked (a typo exited above, in the driver's own words)
if [ "${FF_SHOT_PRIVATE:-0}" = "1" ]; then
  echo "== privacy mode ON in every shot =="
elif [ -n "$NOTHING_BUILT" ]; then
  echo "== FF_SHOT_NOTHING_BUILT=1: no exports are served, so there is nothing to mask =="
else
  echo "== FF_DEMO_EXPORTS=1: synthetic exports, shots are unmasked =="
fi
# a run on non-standard viewports is not a gate run: say so (the driver treats a blank value as unset)
read -r -a _V <<<"${FF_SHOT_VIEWPORTS:-}"
if [ "${#_V[@]}" -gt 0 ]; then
  echo "== FF_SHOT_VIEWPORTS set: ${#_V[@]} viewport(s) [$SHOT_VIEWPORTS] x dark+light -> $PER_ROUTE PNGs per route (the gate shoots the two standard viewports) =="
fi
# optional subset while iterating on one hub: FF_SHOT_ROUTES="/spending /planning" (the gate shoots all)
if [ -n "${FF_SHOT_ROUTES:-}" ]; then
  read -r -a _R <<<"$FF_SHOT_ROUTES"; NROUTES=${#_R[@]}
  # whitespace only: the driver would treat it as "unset" and shoot every route while we expect 0
  [ "$NROUTES" -gt 0 ] || { echo "FF_SHOT_ROUTES is set but names no route (unset it to shoot everything)" >&2; exit 2; }
  # every entry must be one of the parsed routes, spelled as the PAGES pattern (the driver
  # enforces the same rule inside the container) - checked here, before the ~3 min SPA build; then the subset's count
  EXPECT="$(FF_SHOT_ROUTES="$FF_SHOT_ROUTES" FF_SHOT_VIEWPORTS="${FF_SHOT_VIEWPORTS:-}" python3 -B - "$W" <<'PY'
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/tools/verify")
import screenshots_driver as d
routes = os.environ["FF_SHOT_ROUTES"].split()
msg = d.refused_routes(routes, (Path(sys.argv[1]) / "app/src/App.tsx").read_text())
if msg:
    print(msg, file=sys.stderr)
    sys.exit(2)
print(d.expected_shots(routes, d.VIEWPORTS))
PY
)"
  echo "== FF_SHOT_ROUTES set: $NROUTES route(s) -> expecting $EXPECT screenshots =="
fi

# a query entry is checked here too, before the build, with the driver's own rule (exit 1 on a bad entry)
if [ -n "${FF_SHOT_QUERY:-}" ]; then
  FF_SHOT_QUERY="$FF_SHOT_QUERY" FF_SHOT_ROUTES="${FF_SHOT_ROUTES:-}" FF_SHOT_VIEWPORTS="${FF_SHOT_VIEWPORTS:-}" python3 -B - "$W" <<'PY'
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/tools/verify")
import screenshots_driver as d
src = (Path(sys.argv[1]) / "app/src/App.tsx").read_text()
shot = os.environ["FF_SHOT_ROUTES"].split() or d.routes_from_app_tsx(src)
q = d.queries_from_env(os.environ["FF_SHOT_QUERY"], src, shot)
print(f"== FF_SHOT_QUERY set: {len(q)} route(s) shot with a query, PNGs named <route>_query__... (the gate shoots none) ==")
PY
fi

if [ -n "$STATE" ]; then
  echo "== FF_SHOT_STATE=1: the demo household's own answers and manual assets are served where it has them =="
elif [ -n "$NO_STATE" ]; then
  echo "== FF_SHOT_STATE=1: the demo tree has no state directory: the empty answers and manual assets are served =="
fi

# Every check has passed: from here on directories are made.
if [ -n "$MAKE_OUT" ]; then
  mkdir -p "$OUT" 2>/dev/null || { echo "FF_SHOT_OUT cannot be created: $OUT" >&2; exit 2; }
fi
# Scratch root and label dir hold real-data screenshots: private from the start.
install -d -m 700 "$V" "$V/$LABEL"
mkdir -p "$OUT" "$SITE"
# only the label's own shots dir is cleared; FF_SHOT_OUT was checked empty above
[ -n "${FF_SHOT_OUT+set}" ] || rm -rf "$OUT"/*.png "$OUT/overflow.json"
echo "== $(date -Is) build SPA from $W/app/src inside $HARNESS_WEB_TEST ($WEB_ID) =="
# Runs as root (vite needs to write node_modules/.vite-temp) and chowns its output back
# to the host user before exiting, so the next run can rm -rf it without sudo.
docker run --rm --label "$FF_HARNESS_LABEL" \
  -v "$W/app/src:/app/finance-app/app/src:ro" \
  -v "$SITE:/out" \
  "$WEB_ID" sh -lc "cd app && npx vite build --outDir /out/dist --emptyOutDir && chown -R $UIDGID /out/dist"
if [ -n "$NOTHING_BUILT" ]; then
  # no export is copied, and none may have come with the build (vite copies its publicDir, the exports folder, into
  # dist): the page under test is the one the loader shows when manifest.json is absent
  BUILT_EXPORTS="$(python3 -B - "$W" "$SITE/dist" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/tools/verify")
import screenshots_driver as d
print(" ".join(name for name in d.EXPORT_FILES if (Path(sys.argv[2]) / name).exists()))
PY
)"
  [ -z "$BUILT_EXPORTS" ] \
    || { echo "the built site carries export files ($BUILT_EXPORTS): FF_SHOT_NOTHING_BUILT=1 serves none" >&2; exit 1; }
  echo "== FF_SHOT_NOTHING_BUILT=1: the site serves the built dashboard and no export =="
else
  cp "$EXPORTS"/*.json "$SITE/dist/"
fi
# The store's documents, as files nginx serves at /api/<name> (the driver's write_api): the empty preferences, the
# empty answers and manual assets (a demo household's own from FF_SHOT_STATE, copied), the idle refresh status with
# the household's zone (from the assumptions.json just copied; UTC without one) and the SYNTHETIC second-house
# scenarios (hand-written deal terms whose draws name the demo household's accounts), so Planning -> Second House
# draws its populated worksheet. Never a live store's scenarios/ dir; with real exports never a household's answers.
mkdir -p "$SITE/dist/api"
python3 -B - "$W" "$SITE/dist" "$STATE" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/tools/verify")
import screenshots_driver as d
site = Path(sys.argv[2])
zone = d.zone_of_file(site / "assumptions.json") or "UTC"
d.write_api(site / "api", Path(sys.argv[1]) / "tools/verify/fixtures", zone, Path(sys.argv[3]) if sys.argv[3] else None)
PY

cleanup() { docker rm -f "$NGINX" >/dev/null 2>&1 || true; }
trap cleanup EXIT
echo "== serve on 127.0.0.1:$PORT (container $NGINX) =="
docker run -d --rm --label "$FF_HARNESS_LABEL" --name "$NGINX" -p "127.0.0.1:$PORT:80" \
  -v "$SITE/dist:/usr/share/nginx/html:ro" nginx:alpine >/dev/null
# what the site must answer once nginx is up: the exports' manifest, or with no exports the page itself
PROBE=manifest.json
[ -z "$NOTHING_BUILT" ] || PROBE=index.html
for _ in $(seq 1 40); do
  curl -fsS -o /dev/null "http://127.0.0.1:$PORT/$PROBE" 2>/dev/null && break
  sleep 0.5
done
curl -fsS -o /dev/null "http://127.0.0.1:$PORT/$PROBE" || { echo "nginx never answered" >&2; exit 1; }
# each store document is served before anything is shot, and the synthetic scenarios hold at least one scenario
for doc in answers manual-assets prefs refresh scenarios; do
  curl -fsS -o /dev/null "http://127.0.0.1:$PORT/api/$doc" \
    || { echo "the static site does not serve /api/$doc" >&2; exit 1; }
done
[ "$(curl -fsS "http://127.0.0.1:$PORT/api/scenarios" | jq -r '.scenarios | length')" -ge 1 ] \
  || { echo "the synthetic scenarios fixture is not served at /api/scenarios" >&2; exit 1; }

echo "== drive chromium (playwright python $PW, image $PW_IMAGE) =="
# The tag pins the bundled browsers; that image does not carry the python package, so the
# same version is pip-installed (a newer release cannot find the image's chromium).
docker run --rm --label "$FF_HARNESS_LABEL" --ipc=host --network "container:$NGINX" \
  -v "$W/tools/verify/screenshots_driver.py:/drive.py:ro" \
  -v "$W/app/src/App.tsx:/App.tsx:ro" \
  -v "$OUT:/shots" \
  -e "PW_VERSION=$PW" \
  -e "FF_SHOT_ROUTES=${FF_SHOT_ROUTES:-}" \
  -e "FF_SHOT_VIEWPORTS=${FF_SHOT_VIEWPORTS:-}" \
  -e "FF_SHOT_PRIVATE=${FF_SHOT_PRIVATE:-}" \
  -e "FF_SHOT_QUERY=${FF_SHOT_QUERY:-}" \
  -e "FF_SHOT_NOTHING_BUILT=$NOTHING_BUILT" \
  "$PW_IMAGE" \
  sh -lc "pip install -q \"playwright==\$PW_VERSION\" \
          && python /drive.py --base http://127.0.0.1:80 --app-tsx /App.tsx --out /shots \
          ; rc=\$?; chown -R $UIDGID /shots; exit \$rc" \
  || { echo "the playwright driver failed (output above)" >&2; exit 1; }

N=$(find "$OUT" -maxdepth 1 -name '*.png' | wc -l)
python3 -B - "$OUT/overflow.json" "$N" "$EXPECT" <<'PY'
import json, sys
from collections import Counter

entries = json.load(open(sys.argv[1]))
n, expect = int(sys.argv[2]), int(sys.argv[3])
overflow = [e for e in entries if e["page"] or e["offenders"]]
errors = [e for e in entries if e["page_errors"]]
load = [e for e in entries if e.get("load_problems")]
print(f"{n} screenshots (expected {expect}); overflow.json: {len(entries)} entries "
      f"(overflow {len(overflow)}, page errors {len(errors)}, load problems {len(load)})")
failed = False
if n != expect:
    print(f"FAIL: {n} PNGs, expected {expect}", file=sys.stderr)
    failed = True


def shot(e: dict) -> str:
    """what was shot: the route, the route clicked (its shot's name), or Home with the drawer open"""
    return e["route"] + (" (open drawer)" if e.get("drawer") else f" ({e['clicked']})" if e.get("clicked") else "")


# route / viewport / theme (and URL path) only, never the page or console text
for e in overflow:
    print(f"FAIL: {shot(e)} {e['viewport']} {e['theme']}: horizontal overflow "
          f"({len(e['offenders'])} offending elements)", file=sys.stderr)
for e in errors:
    print(f"FAIL: {shot(e)} {e['viewport']} {e['theme']}: {len(e['page_errors'])} page error(s)", file=sys.stderr)
# one line per (shot, problem), counting the viewport/theme combos it hit (`fonts not ready` has no path)
hits = Counter((shot(e), p["kind"], p["path"]) for e in load for p in e["load_problems"])
for (route, kind, path), k in sorted(hits.items()):
    print(f"FAIL: {route}: {kind}{' ' + path if path else ''} (in {k} viewport/theme combos)", file=sys.stderr)
# the gate rule: overflow.json must be empty (every entry above is a failure)
if entries:
    failed = True
sys.exit(1 if failed else 0)
PY
echo "== $(date -Is) screenshots OK in $OUT ($N PNGs; overflow.json is empty) =="

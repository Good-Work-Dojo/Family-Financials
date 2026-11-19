# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
# The harness's names, for the tools/verify shell scripts, which source this file once W (the repository root) is
# set. The settings, their defaults and their shapes are harness_names.py's (its docstring has the table); the Python
# scripts read them there and test_harness_names.py holds the two to each other. tools/private/harness.env, when it
# exists, holds every setting: it is sourced in place of the environment's values, and a setting it leaves out or
# leaves empty is exit 2, so a line lost from it never falls back to a default label or scratch root. Without the
# file, each setting comes from the environment, an unset or empty one taking its default. A harness.env that is a
# link (dangling or not) or anything but a plain file is exit 2 too.
# Sets HARNESS_ETL_TEST and HARNESS_WEB_TEST (<prefix>-etl and -web at the test tag) and HARNESS_ETL_RT and
# HARNESS_WEB_RT (the same at rt); a setting of another shape is exit 2.
if [ -L "$W/tools/private/harness.env" ] || { [ -e "$W/tools/private/harness.env" ] && [ ! -f "$W/tools/private/harness.env" ]; }; then
  echo "tools/private/harness.env is a link or no plain file: the settings are read only from a plain file there" >&2
  exit 2
fi
if [ -f "$W/tools/private/harness.env" ]; then
  unset FF_HARNESS_IMAGE_PREFIX FF_HARNESS_TEST_TAG FF_HARNESS_LABEL FF_HARNESS_PROJECT_PREFIX FF_HARNESS_SCRATCH \
    FF_HARNESS_COMPOSE FF_HARNESS_COMPOSE_PORT_VAR
  # shellcheck source=/dev/null
  . "$W/tools/private/harness.env"
  for harness_key in FF_HARNESS_IMAGE_PREFIX FF_HARNESS_TEST_TAG FF_HARNESS_LABEL FF_HARNESS_PROJECT_PREFIX \
      FF_HARNESS_SCRATCH FF_HARNESS_COMPOSE FF_HARNESS_COMPOSE_PORT_VAR; do
    [ -n "${!harness_key:-}" ] || { echo "tools/private/harness.env sets no $harness_key, or an empty one: the file must name every setting (remove the file to use the defaults)" >&2; exit 2; }
  done
  unset harness_key
fi
: "${FF_HARNESS_IMAGE_PREFIX:=family-financials}"
: "${FF_HARNESS_TEST_TAG:=test}"
: "${FF_HARNESS_LABEL:=family-financials.test=1}"
: "${FF_HARNESS_PROJECT_PREFIX:=family-financials-test}"
: "${FF_HARNESS_SCRATCH:=ff-verify}"
: "${FF_HARNESS_COMPOSE:=}"
: "${FF_HARNESS_COMPOSE_PORT_VAR:=}"
harness_shape() {   # NAME VALUE PATTERN
  [[ "$2" =~ ^$3$ ]] || { echo "$1 is '$2', which is not of the form $3" >&2; exit 2; }
}
harness_shape FF_HARNESS_IMAGE_PREFIX "$FF_HARNESS_IMAGE_PREFIX" '[a-z0-9][a-z0-9._-]*'
harness_shape FF_HARNESS_TEST_TAG "$FF_HARNESS_TEST_TAG" '[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}'
harness_shape FF_HARNESS_LABEL "$FF_HARNESS_LABEL" '[A-Za-z0-9][A-Za-z0-9._-]*=[A-Za-z0-9._-]+'
harness_shape FF_HARNESS_PROJECT_PREFIX "$FF_HARNESS_PROJECT_PREFIX" '[a-z0-9][a-z0-9_-]*'
harness_shape FF_HARNESS_SCRATCH "$FF_HARNESS_SCRATCH" '[A-Za-z0-9_][A-Za-z0-9._-]*'
harness_shape FF_HARNESS_COMPOSE "$FF_HARNESS_COMPOSE" '([A-Za-z0-9_./-]+)?'
harness_shape FF_HARNESS_COMPOSE_PORT_VAR "$FF_HARNESS_COMPOSE_PORT_VAR" '([A-Z_][A-Z0-9_]*)?'
[ "$FF_HARNESS_TEST_TAG" != rt ] || { echo "FF_HARNESS_TEST_TAG may not be rt, the runtime images' tag" >&2; exit 2; }
# The real-data scratch roots, shared by the scripts that take a path from their caller (ci.sh, narrow.sh,
# demo_site.sh); each message starts with HARNESS_WHO and a colon when the caller sets it.
# harness_scratch_roots: sets SCRATCH to the resolved scratch root, $FF_SCRATCH, else the harness's scratch directory in
# the account's home directory (from the account database, never $HOME), which must be absolute and neither /, the home
# directory nor above it; and DEFAULT_SCRATCH to that default root, resolved, whatever FF_SCRATCH says (real data may
# sit there too). Exit 2 otherwise.
harness_scratch_roots() {
  local home raw who="${HARNESS_WHO:+$HARNESS_WHO: }"
  home="$(getent passwd "$(id -u)" | cut -d: -f6)"
  [ -n "$home" ] || { echo "${who}cannot determine the home directory from the account database" >&2; exit 2; }
  raw="${FF_SCRATCH:-$home/$FF_HARNESS_SCRATCH}"
  case "$raw" in /*) ;; *) echo "${who}FF_SCRATCH must be an absolute path (got '$raw')" >&2; exit 2 ;; esac
  SCRATCH="$(realpath -m "$raw")"
  DEFAULT_SCRATCH="$(realpath -m "$home/$FF_HARNESS_SCRATCH")"
  home="$(realpath -m "$home")"
  if [ "$SCRATCH" = / ] || case "$home/" in "$SCRATCH"/*) true ;; *) false ;; esac; then
    echo "${who}FF_SCRATCH must not be /, the home directory or a directory above it (got '$raw')" >&2
    exit 2
  fi
}
# harness_at_or_under A B: the resolved path A is B or lies under it (B may be /; the slash sits inside the quotes
# because a quoted expansion that comes out empty, `"${2%/}"/*`, matches nothing in a case pattern)
harness_at_or_under() { case "$1/" in "${2%/}/"*) true ;; *) false ;; esac; }
# harness_refuse_real NAME RESOLVED [SPELT…]: exit 2 when the path (as resolved, and as spelt with its links not
# followed: a link placed inside a root names that root) lies at or inside either real-data scratch root, or when the
# resolved path holds one (a directory above a root would carry the real data with it). harness_scratch_roots first.
harness_refuse_real() {
  local name="$1" resolved="$2" root p who="${HARNESS_WHO:+$HARNESS_WHO: }"
  shift
  for root in "$SCRATCH" "$DEFAULT_SCRATCH"; do
    for p in "$@"; do
      if harness_at_or_under "$p" "$root"; then
        echo "${who}$name $p lies at or inside the real-data scratch root $root; only synthetic data is read here" >&2
        exit 2
      fi
    done
    if harness_at_or_under "$root" "$resolved"; then
      echo "${who}$name $resolved holds the real-data scratch root $root; only synthetic data is read here" >&2
      exit 2
    fi
  done
}
HARNESS_ETL_TEST="$FF_HARNESS_IMAGE_PREFIX-etl:$FF_HARNESS_TEST_TAG"
HARNESS_WEB_TEST="$FF_HARNESS_IMAGE_PREFIX-web:$FF_HARNESS_TEST_TAG"
HARNESS_ETL_RT="$FF_HARNESS_IMAGE_PREFIX-etl:rt"
HARNESS_WEB_RT="$FF_HARNESS_IMAGE_PREFIX-web:rt"

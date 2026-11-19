#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
# How the project is being taken up, read from GitHub alone (the app and the demo site carry no analytics): stars,
# forks and watchers; the last 14 days' views and clones (total and unique), the top referrers and the most viewed
# paths; open and closed issues and pull requests; open and closed discussions when the repository has them. Every
# call is a read (gh api GET, and one GraphQL query); bash.
# Usage: tools/maintainer/adoption-report.sh [--repo OWNER/NAME] [--json]
#   --json  one JSON object instead of the text report
# Needs gh signed in (gh auth status); the traffic figures need push access to the repository and print as
# unavailable without it. Exit 0, 1 a read failed, 2 a usage error.
set -euo pipefail

REPO="Good-Work-Dojo/Family-Financials"
JSON=0
usage() {
  echo "usage: tools/maintainer/adoption-report.sh [--repo OWNER/NAME] [--json]" >&2
  exit 2
}
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) [ $# -ge 2 ] || usage; REPO="$2"; shift 2 ;;
    --json) JSON=1; shift ;;
    -h|--help) usage ;;
    *) echo "adoption-report: unknown argument '$1' (--help lists them)" >&2; exit 2 ;;
  esac
done
[[ "$REPO" =~ ^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$ ]] \
  || { echo "adoption-report: --repo takes OWNER/NAME (got '$REPO')" >&2; exit 2; }
for tool in gh python3; do
  command -v "$tool" >/dev/null 2>&1 || { echo "adoption-report: $tool is not installed" >&2; exit 2; }
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
chmod 700 "$TMP"

# read ENDPOINT FILE: a GET, its body in FILE; returns non-zero (FILE left empty) when it fails.
read_api() {
  gh api -X GET "$1" >"$2" 2>"$TMP/err" || { : >"$2"; return 1; }
}

read_api "repos/$REPO" "$TMP/repo.json" \
  || { echo "adoption-report: $REPO could not be read ($(tail -n 1 "$TMP/err")): check --repo and gh auth status" >&2; exit 1; }
for what in views clones popular/referrers popular/paths; do
  read_api "repos/$REPO/traffic/$what" "$TMP/traffic-${what#popular/}.json" || true
done
for kind in issue pr; do
  for state in open closed; do
    read_api "search/issues?q=repo:$REPO+is:$kind+is:$state&per_page=1" "$TMP/$kind-$state.json" \
      || { echo "adoption-report: the $state ${kind}s could not be counted ($(tail -n 1 "$TMP/err"))" >&2; exit 1; }
  done
done
: >"$TMP/discussions.json"
if python3 -I -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("has_discussions") else 1)' \
    "$TMP/repo.json"; then
  # $owner and $name are the GraphQL query's own variables, not the shell's
  # shellcheck disable=SC2016
  gh api graphql -f query='query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) {
      open: discussions(states: OPEN) { totalCount } closed: discussions(states: CLOSED) { totalCount } } }' \
    -f owner="${REPO%%/*}" -f name="${REPO#*/}" >"$TMP/discussions.json" 2>"$TMP/err" \
    || { echo "adoption-report: the discussions could not be counted ($(tail -n 1 "$TMP/err"))" >&2; exit 1; }
fi

python3 -I - "$TMP" "$REPO" "$JSON" <<'PY'
import json
import sys
from pathlib import Path

tmp, repo, as_json = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "1"


def load(name):
    text = (tmp / name).read_text(encoding="utf-8").strip()
    return json.loads(text) if text else None


r = load("repo.json")
views, clones = load("traffic-views.json"), load("traffic-clones.json")
referrers, paths = load("traffic-referrers.json"), load("traffic-paths.json")
discussions = load("discussions.json")
repository = ((discussions or {}).get("data") or {}).get("repository") or {}


def window(doc):
    return None if doc is None else {"total": doc.get("count", 0), "unique": doc.get("uniques", 0)}


def count(kind, state):
    return (load(f"{kind}-{state}.json") or {}).get("total_count", 0)


report = {
    "repository": repo,
    "stars": r.get("stargazers_count", 0),
    "forks": r.get("forks_count", 0),
    "watchers": r.get("subscribers_count", 0),
    "views_14_days": window(views),
    "clones_14_days": window(clones),
    "top_referrers": None if referrers is None else [
        {"referrer": x.get("referrer"), "total": x.get("count", 0), "unique": x.get("uniques", 0)} for x in referrers],
    "top_paths": None if paths is None else [
        {"path": x.get("path"), "total": x.get("count", 0), "unique": x.get("uniques", 0)} for x in paths],
    "issues": {"open": count("issue", "open"), "closed": count("issue", "closed")},
    "pull_requests": {"open": count("pr", "open"), "closed": count("pr", "closed")},
    "discussions": None if discussions is None else {
        "open": (repository.get("open") or {}).get("totalCount", 0),
        "closed": (repository.get("closed") or {}).get("totalCount", 0)},
}
if as_json:
    print(json.dumps(report, indent=2))
    sys.exit(0)


def pair(w):
    return "unavailable (needs push access)" if w is None else f"{w['total']} total, {w['unique']} unique"


lines = [repo,
         f"  stars {report['stars']}, forks {report['forks']}, watchers {report['watchers']}",
         f"  views (14 days)     {pair(report['views_14_days'])}",
         f"  clones (14 days)    {pair(report['clones_14_days'])}",
         f"  issues              {report['issues']['open']} open, {report['issues']['closed']} closed",
         f"  pull requests       {report['pull_requests']['open']} open, {report['pull_requests']['closed']} closed"]
d = report["discussions"]
lines.append("  discussions         " + ("not enabled" if d is None else f"{d['open']} open, {d['closed']} closed"))
for title, key, field in (("top referrers", "top_referrers", "referrer"), ("top paths", "top_paths", "path")):
    rows = report[key]
    if rows is None:
        lines.append(f"  {title}: unavailable (needs push access)")
    elif not rows:
        lines.append(f"  {title}: none")
    else:
        lines.append(f"  {title} (14 days)")
        lines += [f"    {x[field]}  {x['total']} total, {x['unique']} unique" for x in rows]
print("\n".join(lines))
PY

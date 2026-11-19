#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
# Run once by the maintainer, after the repository is made public, from a checkout of it (bash). In order, stopping at
# the first failure with what to do next:
#   1. the repository is public and its default branch's head is the expected commit (--expect-sha; the head is
#      printed when none is given); a private repository stops here, before anything changes;
#   2. Dependabot alerts and security updates, secret scanning and push protection, private vulnerability reporting
#      and CodeQL's default setup are switched on;
#   3. a branch ruleset on the default branch requires the checks of .github/workflows/ci.yml (its jobs' names, read
#      from the file, each reported by GitHub Actions) and forbids force-pushes and deletion; a ruleset of the same
#      name is brought in line in place;
#   4. the repository variable FF_PAGES_ENABLED is set to true, Pages is switched on with the Actions build type, the
#      github-pages environment lets v* tags deploy as well as the default branch (pages.yml also runs on a published
#      release) and pages.yml is run on the default branch;
#   5. the release of the version (CHANGELOG.md's first versioned section unless --version says) is created at the
#      head, its notes that section (tools/verify/release_names.py notes), and release.yml's run is awaited;
#   6. both images are pulled with an empty docker configuration, so without any sign-in, and must hold both
#      architectures; the publish gate (tools/publish_gate.py images) scans them with the given denylist and prints
#      counts and paths only. When the pull fails, the packages' visibility settings pages are printed;
#   7. the demo site answers 200 for its page and its manifest.json, and the page carries its content-security
#      policy in a <meta> tag.
# Every step reads the state first and changes nothing that is already as wanted, so running the script again is
# safe. --dry-run makes the reads and prints every call that would change something (and the pulls, the gate and the
# smoke test) without making it. No token is ever printed: gh holds its own.
# Usage: tools/maintainer/post-publish.sh [--repo OWNER/NAME] [--version vX.Y.Z] [--expect-sha SHA]
#          (--denylist DIR | --skip-gate) [--export-names FILE] [--timeout MINUTES] [--dry-run]
# Needs gh signed in (gh auth status) as an account with admin rights on the repository, docker, curl and python3.
# Exit 0 done, 1 a step failed (its line says what to do), 2 a usage error.
set -euo pipefail

W="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO="Good-Work-Dojo/Family-Financials"
TAG=""
EXPECT_SHA=""
DENYLIST=""
EXPORT_NAMES=""
SKIP_GATE=0
DRY=0
TIMEOUT=60
POLL=30
RULESET="default-branch"
VARIABLE="FF_PAGES_ENABLED"
# the GitHub Actions app: a required check counts only when GitHub Actions reports it
ACTIONS_APP=15368
# where the published images keep the project's own files: the gate's generic detectors read only these
APP_PATHS=(/opt/ff /srv /usr/share/doc/family-financials /etc/caddy)

usage() {
  cat >&2 <<'EOF'
usage: tools/maintainer/post-publish.sh [--repo OWNER/NAME] [--version vX.Y.Z] [--expect-sha SHA]
         (--denylist DIR | --skip-gate) [--export-names FILE] [--timeout MINUTES] [--dry-run]
EOF
  exit 2
}
refuse() { printf 'post-publish: %s\n' "$1" >&2; exit 2; }
fail() { printf 'post-publish: %s\n' "$1" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) [ $# -ge 2 ] || usage; REPO="$2"; shift 2 ;;
    --version) [ $# -ge 2 ] || usage; TAG="$2"; shift 2 ;;
    --expect-sha) [ $# -ge 2 ] || usage; EXPECT_SHA="$2"; shift 2 ;;
    --denylist) [ $# -ge 2 ] || usage; DENYLIST="$2"; shift 2 ;;
    --export-names) [ $# -ge 2 ] || usage; EXPORT_NAMES="$2"; shift 2 ;;
    --timeout) [ $# -ge 2 ] || usage; TIMEOUT="$2"; shift 2 ;;
    --skip-gate) SKIP_GATE=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage ;;
    *) refuse "unknown argument '$1' (--help lists them)" ;;
  esac
done

[[ "$REPO" =~ ^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$ ]] || refuse "--repo takes OWNER/NAME (got '$REPO')"
[[ "$TIMEOUT" =~ ^[1-9][0-9]{0,3}$ ]] || refuse "--timeout takes a number of minutes (got '$TIMEOUT')"
[ -z "$EXPECT_SHA" ] || [[ "$EXPECT_SHA" =~ ^[0-9a-f]{7,40}$ ]] \
  || refuse "--expect-sha takes a commit SHA in lower-case hex, 7 to 40 characters (got '$EXPECT_SHA')"
if [ "$SKIP_GATE" = 1 ]; then
  [ -z "$DENYLIST" ] || refuse "--denylist and --skip-gate contradict each other: give one"
else
  [ -n "$DENYLIST" ] || refuse "the publish gate needs the denylist: give --denylist DIR (or --skip-gate to leave the scan out)"
  [ -d "$DENYLIST" ] || refuse "the denylist $DENYLIST is not a directory"
fi
[ -z "$EXPORT_NAMES" ] || [ -f "$EXPORT_NAMES" ] || refuse "the export names file $EXPORT_NAMES does not exist"
for tool in gh docker curl python3; do
  command -v "$tool" >/dev/null 2>&1 || refuse "$tool is not installed: install it, then run this again"
done
OWNER="${REPO%%/*}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
chmod 700 "$TMP"

# The JSON and text reading the steps need, in Python's standard library: tool COMMAND ARGS...
TOOL="$(cat <<'PY'
import json
import re
import sys

cmd, args = sys.argv[1], sys.argv[2:]


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def runs(path, event, sha):
    return [r for r in load(path).get("workflow_runs") or [] if r.get("event") == event and r.get("head_sha") == sha]


if cmd == "get":  # FILE PATH: the value at a dotted path; empty when absent, true/false for a boolean
    value = load(args[0])
    for key in args[1].split("."):
        value = value.get(key) if isinstance(value, dict) else None
    print("" if value is None else str(value).lower() if isinstance(value, bool) else value)
elif cmd == "version":  # CHANGELOG: the tag of its first section headed by a version, `## [1.2.3]`
    with open(args[0], encoding="utf-8") as f:
        for line in f:
            m = re.match(r"## \[(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\]", line)
            if m:
                print("v" + m.group(1))
                break
elif cmd == "contexts":  # CI_YML: each job's check name, one a line: its `name:`, else its id
    jobs, in_jobs = [], False
    with open(args[0], encoding="utf-8") as f:
        for line in f.read().splitlines():
            if re.fullmatch(r"jobs:\s*(#.*)?", line):
                in_jobs = True
                continue
            if in_jobs and re.match(r"[^\s#]", line):
                in_jobs = False
            if not in_jobs:
                continue
            m = re.fullmatch(r"  ([A-Za-z_][A-Za-z0-9_-]*):\s*(#.*)?", line)
            if m:
                jobs.append([m.group(1), None])
                continue
            m = re.fullmatch(r"    name:\s*(.+?)\s*(#.*)?", line)
            if m and jobs and jobs[-1][1] is None:
                jobs[-1][1] = m.group(1).strip("'\"")
            if re.fullmatch(r"    strategy:.*", line):
                sys.exit("a job with a strategy reports one check per matrix entry")
    names = [name or job for job, name in jobs]
    if not names or any("${{" in n for n in names):
        sys.exit("no job, or a job named by an expression")
    print("\n".join(names))
elif cmd == "ruleset":  # NAME APP CONTEXT...: the ruleset's body
    print(json.dumps({
        "name": args[0], "target": "branch", "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"},
                  {"type": "required_status_checks", "parameters": {
                      "strict_required_status_checks_policy": False, "do_not_enforce_on_create": False,
                      "required_status_checks": [{"context": c, "integration_id": int(args[1])} for c in args[2:]]}}],
    }))
elif cmd == "ruleset-id":  # LIST NAME: the id of the repository's own ruleset of that name; empty when none
    print(next((str(r["id"]) for r in load(args[0]) if r.get("name") == args[1]
                and r.get("source_type", "Repository") == "Repository"), ""))
elif cmd == "same-ruleset":  # WANT HAVE: exit 0 when HAVE enforces exactly what WANT says
    def norm(r):
        rules = {}
        for rule in r.get("rules") or []:
            p = rule.get("parameters") or {}
            rules[rule.get("type")] = ((bool(p.get("strict_required_status_checks_policy")), sorted(
                (c.get("context"), c.get("integration_id")) for c in p.get("required_status_checks") or []))
                if rule.get("type") == "required_status_checks" else None)
        ref = (r.get("conditions") or {}).get("ref_name") or {}
        return (r.get("target"), r.get("enforcement"), sorted(ref.get("include") or []),
                sorted(ref.get("exclude") or []), sorted(rules.items(), key=lambda kv: str(kv[0])))
    sys.exit(0 if norm(load(args[0])) == norm(load(args[1])) else 1)
elif cmd == "has-policy":  # POLICIES NAME TYPE: exit 0 when a deployment policy names that branch or tag pattern
    found = any(p.get("name") == args[1] and (p.get("type") or "branch") == args[2]
                for p in load(args[0]).get("branch_policies") or [])
    sys.exit(0 if found else 1)
elif cmd == "latest-run":  # LIST EVENT SHA: "id status conclusion url" of the newest such run; empty when none
    found = runs(*args)
    if found:
        r = max(found, key=lambda r: r.get("id") or 0)
        print(r.get("id"), r.get("status"), r.get("conclusion") or "-", r.get("html_url") or "-")
elif cmd == "release-names":  # NAMES_OUTPUT: release_names.py's `key=value` lines as etl, web, version
    pairs = dict(line.split("=", 1) for line in open(args[0], encoding="utf-8").read().splitlines() if "=" in line)
    print(pairs["etl"], pairs["web"], pairs["version"])
elif cmd == "export-names":  # TYPES_TS: the dashboard's export file names (EXPORT_FILES), one a line
    with open(args[0], encoding="utf-8") as f:
        m = re.search(r"EXPORT_FILES\s*=\s*\[(.*?)\]", f.read(), re.S)
    names = re.findall(r'"([^"]+\.json)"', m.group(1)) if m else []
    if not names:
        sys.exit("no EXPORT_FILES list")
    print("\n".join(names))
elif cmd == "platforms":  # MANIFEST: the os/architecture pairs an image index holds, one a line
    doc = load(args[0])
    print("\n".join(sorted({f"{p.get('os')}/{p.get('architecture')}" for p in
                            (m.get("platform") or {} for m in doc.get("manifests") or [])})))
elif cmd == "csp":  # HTML: exit 0 when the page carries a content-security policy <meta> tag
    with open(args[0], encoding="utf-8", errors="replace") as f:
        page = f.read()
    sys.exit(0 if re.search(r"<meta\b[^>]*http-equiv\s*=\s*[\"']?content-security-policy", page, re.I) else 1)
else:
    sys.exit(f"unknown command {cmd}")
PY
)"
tool() { python3 -I -c "$TOOL" "$@"; }

show() { printf '+'; printf ' %q' "$@"; printf '\n'; }
step() { printf '\n== %s/7 %s ==\n' "$1" "$2"; }

# get ENDPOINT FILE: a read, its body in FILE. Returns 0, or 4 when the resource does not exist (HTTP 404); any other
# failure stops the script.
get() {
  show gh api -X GET "$1"
  if gh api -X GET "$1" >"$2" 2>"$TMP/err"; then return 0; fi
  if grep -q 'HTTP 404' "$TMP/err"; then return 4; fi
  fail "reading $1 failed ($(tail -n 1 "$TMP/err")): check that gh auth status shows an account with admin rights on $REPO, then run this again"
}

# change COMMAND...: a call that changes the repository: printed, and made unless this is a dry run.
change() {
  if [ "$DRY" = 1 ]; then printf 'would run:'; printf ' %q' "$@"; printf '\n'; return 0; fi
  show "$@"
  "$@" >"$TMP/out" 2>"$TMP/err" \
    || fail "$(tail -n 1 "$TMP/err"): fix what it says, then run this again (the steps already done are skipped)"
}

# change_json METHOD ENDPOINT JSON: a change whose request body is JSON.
change_json() {
  printf '%s\n' "$3" >"$TMP/body.json"
  if [ "$DRY" = 1 ]; then printf '  with body: %s\n' "$3"; fi
  change gh api -X "$1" "$2" --input "$TMP/body.json"
}

# wait_run WORKFLOW EVENT: waits, up to --timeout minutes, for the newest run of WORKFLOW on the head from EVENT to
# finish; anything but success stops the script.
wait_run() {
  local deadline=$((SECONDS + TIMEOUT * 60)) run="" id status conclusion url=""
  while :; do
    get "repos/$REPO/actions/workflows/$1/runs?event=$2&head_sha=$HEAD&per_page=20" "$TMP/runs.json" \
      || fail "the workflow $1 is not in $REPO: publish the tree that holds it, then run this again"
    run="$(tool latest-run "$TMP/runs.json" "$2" "$HEAD")"
    if [ -n "$run" ]; then
      read -r id status conclusion url <<<"$run"
      if [ "$status" = completed ]; then
        [ "$conclusion" = success ] \
          || fail "$1's run ended '$conclusion' ($url): read its log there, re-run it once the cause is fixed, then run this again"
        echo "$1: run $id succeeded ($url)"
        return 0
      fi
    fi
    [ "$SECONDS" -lt "$deadline" ] \
      || fail "$1's run on $HEAD did not finish within $TIMEOUT minutes${url:+ ($url)}: wait for it (or pass a longer --timeout), then run this again"
    sleep "$POLL"
  done
}

if [ "$DRY" = 1 ]; then echo "dry run: reads are made, and every change is printed instead of made"; fi

step 1 "the repository is public at the expected commit"
show gh auth status
gh auth status >/dev/null 2>&1 \
  || fail "gh is not signed in: run gh auth login as an account with admin rights on $REPO, then run this again"
get "repos/$REPO" "$TMP/repo.json" \
  || fail "$REPO does not exist or this account cannot see it: check --repo and gh auth status, then run this again"
VISIBILITY="$(tool get "$TMP/repo.json" visibility)"
[ "$VISIBILITY" = public ] \
  || fail "$REPO is ${VISIBILITY:-not public}: make it public (its Settings, Danger zone), then run this again; nothing was changed"
[ "$(tool get "$TMP/repo.json" permissions.admin)" = true ] \
  || fail "the account gh is signed in as has no admin rights on $REPO: sign in as an owner (gh auth login), then run this again"
OWNER_TYPE="$(tool get "$TMP/repo.json" owner.type)"
BRANCH="$(tool get "$TMP/repo.json" default_branch)"
get "repos/$REPO/branches/$BRANCH" "$TMP/branch.json" || fail "$REPO has no branch $BRANCH: push the tree, then run this again"
HEAD="$(tool get "$TMP/branch.json" commit.sha)"
[[ "$HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "the head of $BRANCH could not be read: run this again"
if [ -n "$EXPECT_SHA" ]; then
  case "$HEAD" in
    "$EXPECT_SHA"*) echo "$REPO is public; the head of $BRANCH is $HEAD, as expected" ;;
    *) fail "the head of $BRANCH is $HEAD, not $EXPECT_SHA: check what was pushed; nothing was changed" ;;
  esac
else
  echo "$REPO is public; the head of $BRANCH is $HEAD (pass --expect-sha $HEAD to require it)"
fi
if [ -z "$TAG" ]; then
  TAG="$(tool version "$W/CHANGELOG.md")" || true
  [ -n "$TAG" ] || fail "CHANGELOG.md has no section headed by a version: give --version vX.Y.Z"
fi
case "$TAG" in v*) ;; *) TAG="v$TAG" ;; esac
python3 -I "$W/tools/verify/release_names.py" names "$REPO" "$TAG" >"$TMP/names" \
  || fail "the version $TAG gives no release names (the line above): give --version vX.Y.Z"
read -r ETL_IMAGE WEB_IMAGE IMAGE_VERSION <<<"$(tool release-names "$TMP/names")"
PRERELEASE=()
case "$TAG" in *-*) PRERELEASE=(--prerelease) ;; esac
echo "release $TAG: $ETL_IMAGE:$IMAGE_VERSION and $WEB_IMAGE:$IMAGE_VERSION"

step 2 "security features"
if get "repos/$REPO/vulnerability-alerts" "$TMP/alerts"; then
  echo "Dependabot alerts: already on"
else
  change gh api -X PUT "repos/$REPO/vulnerability-alerts"
fi
rc=0; get "repos/$REPO/automated-security-fixes" "$TMP/fixes.json" || rc=$?
if [ "$rc" = 0 ] && [ "$(tool get "$TMP/fixes.json" enabled)" = true ]; then
  echo "Dependabot security updates: already on"
else
  change gh api -X PUT "repos/$REPO/automated-security-fixes"
fi
if [ "$(tool get "$TMP/repo.json" security_and_analysis.secret_scanning.status)" = enabled ] \
    && [ "$(tool get "$TMP/repo.json" security_and_analysis.secret_scanning_push_protection.status)" = enabled ]; then
  echo "secret scanning and push protection: already on"
else
  change_json PATCH "repos/$REPO" \
    '{"security_and_analysis":{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}}'
fi
rc=0; get "repos/$REPO/private-vulnerability-reporting" "$TMP/pvr.json" || rc=$?
if [ "$rc" = 0 ] && [ "$(tool get "$TMP/pvr.json" enabled)" = true ]; then
  echo "private vulnerability reporting: already on"
else
  change gh api -X PUT "repos/$REPO/private-vulnerability-reporting"
fi
rc=0; get "repos/$REPO/code-scanning/default-setup" "$TMP/codeql.json" || rc=$?
if [ "$rc" = 0 ] && [ "$(tool get "$TMP/codeql.json" state)" = configured ]; then
  echo "CodeQL default setup: already configured"
else
  change_json PATCH "repos/$REPO/code-scanning/default-setup" '{"state":"configured"}'
  echo "CodeQL's first analysis runs in the background"
fi

step 3 "the default branch's ruleset"
mapfile -t CONTEXTS < <(tool contexts "$W/.github/workflows/ci.yml")
[ "${#CONTEXTS[@]}" -gt 0 ] || fail "the jobs of .github/workflows/ci.yml could not be read: run this from a checkout of $REPO"
echo "required checks (from ci.yml): ${CONTEXTS[*]}"
WANT="$(tool ruleset "$RULESET" "$ACTIONS_APP" "${CONTEXTS[@]}")"
printf '%s\n' "$WANT" >"$TMP/want.json"
get "repos/$REPO/rulesets?includes_parents=false&per_page=100" "$TMP/rulesets.json" \
  || fail "the rulesets of $REPO could not be read: check the account's admin rights, then run this again"
RULESET_ID="$(tool ruleset-id "$TMP/rulesets.json" "$RULESET")"
if [ -z "$RULESET_ID" ]; then
  change_json POST "repos/$REPO/rulesets" "$WANT"
else
  get "repos/$REPO/rulesets/$RULESET_ID" "$TMP/have.json" || fail "the ruleset $RULESET vanished while read: run this again"
  if tool same-ruleset "$TMP/want.json" "$TMP/have.json"; then
    echo "ruleset $RULESET: already as wanted"
  else
    change_json PUT "repos/$REPO/rulesets/$RULESET_ID" "$WANT"
  fi
fi

step 4 "the demo site"
rc=0; get "repos/$REPO/actions/variables/$VARIABLE" "$TMP/variable.json" || rc=$?
if [ "$rc" = 4 ]; then
  change_json POST "repos/$REPO/actions/variables" "{\"name\":\"$VARIABLE\",\"value\":\"true\"}"
elif [ "$(tool get "$TMP/variable.json" value)" != true ]; then
  change_json PATCH "repos/$REPO/actions/variables/$VARIABLE" "{\"name\":\"$VARIABLE\",\"value\":\"true\"}"
else
  echo "$VARIABLE: already true"
fi
rc=0; get "repos/$REPO/pages" "$TMP/pages.json" || rc=$?
if [ "$rc" = 4 ]; then
  change_json POST "repos/$REPO/pages" '{"build_type":"workflow"}'
elif [ "$(tool get "$TMP/pages.json" build_type)" != workflow ]; then
  change_json PUT "repos/$REPO/pages" '{"build_type":"workflow"}'
else
  echo "Pages: already built by Actions"
fi
# pages.yml also runs on a published release, from its tag: the github-pages environment must let v* tags deploy as
# well as the default branch (an environment that lets every ref deploy needs nothing)
ENVIRONMENT="repos/$REPO/environments/github-pages"
rc=0; get "$ENVIRONMENT" "$TMP/environment.json" || rc=$?
if [ "$rc" = 0 ] && [ -z "$(tool get "$TMP/environment.json" deployment_branch_policy)" ]; then
  echo "github-pages environment: every branch and tag may deploy already"
else
  if [ "$rc" = 0 ] && [ "$(tool get "$TMP/environment.json" deployment_branch_policy.custom_branch_policies)" = true ]; then
    get "$ENVIRONMENT/deployment-branch-policies?per_page=100" "$TMP/policies.json" \
      || fail "the github-pages environment's deployment policies could not be read: run this again"
  else
    # absent, or protected branches only: switch to chosen branches and tags, then name the default branch too
    change_json PUT "$ENVIRONMENT" '{"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}'
    echo '{"branch_policies":[]}' >"$TMP/policies.json"
  fi
  for policy in "$BRANCH branch" "v* tag"; do
    read -r name type <<<"$policy"
    if tool has-policy "$TMP/policies.json" "$name" "$type"; then
      echo "github-pages environment: the $type $name may deploy already"
    else
      change_json POST "$ENVIRONMENT/deployment-branch-policies" "{\"name\":\"$name\",\"type\":\"$type\"}"
    fi
  done
fi
get "repos/$REPO/actions/workflows/pages.yml/runs?event=workflow_dispatch&head_sha=$HEAD&per_page=20" "$TMP/pages-runs.json" \
  || fail "the workflow pages.yml is not in $REPO: publish the tree that holds it, then run this again"
read -r _ PAGES_STATUS PAGES_CONCLUSION _ <<<"$(tool latest-run "$TMP/pages-runs.json" workflow_dispatch "$HEAD") - - - -"
if [ "$PAGES_CONCLUSION" = success ] || { [ "$PAGES_STATUS" != - ] && [ "$PAGES_STATUS" != completed ]; }; then
  echo "pages.yml: a run on $HEAD has succeeded or is under way"
else
  change_json POST "repos/$REPO/actions/workflows/pages.yml/dispatches" "{\"ref\":\"$BRANCH\"}"
fi

step 5 "the release $TAG"
python3 -I "$W/tools/verify/release_names.py" notes "$W/CHANGELOG.md" "$TAG" >"$TMP/notes.md" \
  || fail "CHANGELOG.md has no notes for $TAG (the line above): add its section, then run this again"
rc=0; get "repos/$REPO/releases/tags/$TAG" "$TMP/release.json" || rc=$?
RELEASED=0
if [ "$rc" = 0 ]; then
  get "repos/$REPO/commits/$TAG" "$TMP/tag.json" || fail "the tag $TAG could not be read: run this again"
  [ "$(tool get "$TMP/tag.json" sha)" = "$HEAD" ] \
    || fail "the release $TAG exists on another commit than the head $HEAD: delete that release and its tag, then run this again"
  echo "release $TAG: already published at $HEAD"
  RELEASED=1
else
  echo "notes: CHANGELOG.md's section for $TAG ($(grep -c '' "$TMP/notes.md") lines)"
  change gh release create "$TAG" --repo "$REPO" --target "$HEAD" --title "$TAG" --notes-file "$TMP/notes.md" \
    "${PRERELEASE[@]}"
  [ "$DRY" = 1 ] || RELEASED=1
fi
if [ "$RELEASED" = 1 ]; then
  wait_run release.yml release
else
  echo "would wait for release.yml's run on $HEAD"
fi

step 6 "the images are public and hold nothing from the denylist"
IMAGES=("$ETL_IMAGE:$IMAGE_VERSION" "$WEB_IMAGE:$IMAGE_VERSION")
# an empty docker configuration holds no sign-in and no credential helper, so these pulls are anonymous
ANON="$TMP/anonymous-docker"
mkdir -m 700 "$ANON"
package_links() {
  local kind=orgs image
  [ "$OWNER_TYPE" = Organization ] || kind=users
  for image in "$ETL_IMAGE" "$WEB_IMAGE"; do
    echo "  https://github.com/$kind/$OWNER/packages/container/${image#ghcr.io/*/}/settings"
  done
}
for image in "${IMAGES[@]}"; do
  if [ "$DRY" = 1 ]; then
    printf 'would run: DOCKER_CONFIG=<an empty directory> docker pull -q %s\n' "$image"
    printf 'would run: DOCKER_CONFIG=<an empty directory> docker manifest inspect %s\n' "$image"
    continue
  fi
  show env DOCKER_CONFIG="$ANON" docker pull -q "$image"
  show env DOCKER_CONFIG="$ANON" docker manifest inspect "$image"
  if ! DOCKER_CONFIG="$ANON" docker pull -q "$image" >/dev/null 2>"$TMP/err" \
      || ! DOCKER_CONFIG="$ANON" docker manifest inspect "$image" >"$TMP/manifest.json" 2>>"$TMP/err"; then
    printf 'post-publish: %s could not be pulled without signing in, so its package is not public yet (%s).\n' \
      "$image" "$(tail -n 1 "$TMP/err")" >&2
    echo "Open each package's settings, set its visibility to Public (Danger zone), then run this again:" >&2
    package_links >&2
    exit 1
  fi
  PLATFORMS="$(tool platforms "$TMP/manifest.json")"
  for platform in linux/amd64 linux/arm64; do
    grep -qx "$platform" <<<"$PLATFORMS" \
      || fail "$image holds no $platform image: re-run release.yml for $TAG (Actions, release), then run this again"
  done
  echo "$image: pulled anonymously, linux/amd64 and linux/arm64"
done
if [ "$SKIP_GATE" = 1 ]; then
  echo "publish gate: skipped (--skip-gate)"
else
  if [ -z "$EXPORT_NAMES" ]; then
    EXPORT_NAMES="$TMP/export-names"
    tool export-names "$W/app/src/data/types.ts" >"$EXPORT_NAMES" \
      || fail "the export file names could not be read from app/src/data/types.ts: give --export-names FILE"
  fi
  GATE=(python3 -I "$W/tools/publish_gate.py" images --denylist "$DENYLIST" --export-names "$EXPORT_NAMES")
  for prefix in "${APP_PATHS[@]}"; do GATE+=(--app-path "$prefix"); done
  GATE+=("${IMAGES[@]}")
  if [ "$DRY" = 1 ]; then
    printf 'would run:'; printf ' %q' "${GATE[@]}"; printf '\n'
  else
    show "${GATE[@]}"
    rc=0; "${GATE[@]}" || rc=$?
    case "$rc" in
      0) ;;
      1) printf 'post-publish: the publish gate found something in the published images (its lines above): make both packages private at once, then find its source before publishing again:\n' >&2
         package_links >&2
         exit 1 ;;
      *) fail "the publish gate could not scan the images (its error above): fix it, then run this again" ;;
    esac
  fi
fi

step 7 "the demo site answers"
if [ "$DRY" = 1 ]; then
  echo "would wait for pages.yml's run on $HEAD, then fetch the site's page and manifest.json"
else
  wait_run pages.yml workflow_dispatch
  get "repos/$REPO/pages" "$TMP/pages.json" || fail "Pages is not on for $REPO: run this again"
  SITE="$(tool get "$TMP/pages.json" html_url)"
  [ -n "$SITE" ] || fail "Pages has no address for $REPO yet: run this again in a few minutes"
  SITE="${SITE%/}/"
  ok=0
  for attempt in 1 2 3 4 5; do
    [ "$attempt" = 1 ] || sleep 15
    show curl -sS -L --max-time 30 -o "$TMP/page.html" -w '%{http_code}' "$SITE"
    PAGE_CODE="$(curl -sS -L --max-time 30 -o "$TMP/page.html" -w '%{http_code}' "$SITE" 2>/dev/null)" || PAGE_CODE=000
    show curl -sS -L --max-time 30 -o "$TMP/manifest-site.json" -w '%{http_code}' "${SITE}manifest.json"
    MANIFEST_CODE="$(curl -sS -L --max-time 30 -o "$TMP/manifest-site.json" -w '%{http_code}' "${SITE}manifest.json" 2>/dev/null)" \
      || MANIFEST_CODE=000
    if [ "$PAGE_CODE" = 200 ] && [ "$MANIFEST_CODE" = 200 ]; then ok=1; break; fi
  done
  [ "$ok" = 1 ] \
    || fail "the demo site at $SITE answered $PAGE_CODE for its page and $MANIFEST_CODE for manifest.json: check pages.yml's last run, then run this again"
  tool csp "$TMP/page.html" \
    || fail "the demo site's page carries no content-security policy <meta> tag: check the demo build (tools/verify/demo_site.sh), then run this again"
  echo "demo site: $SITE answers 200 for its page and manifest.json, and the page carries its content-security policy"
fi

if [ "$DRY" = 1 ]; then echo; echo "dry run: nothing was changed"; else echo; echo "done: $REPO is published as $TAG"; fi
# The one step no API call makes: the contributor agreement's app is installed by hand, once.
echo "by hand, once: install the CLA Assistant GitHub App on $REPO, pointed at a Gist holding CLA.md, so that it asks the author of each first pull request to sign"

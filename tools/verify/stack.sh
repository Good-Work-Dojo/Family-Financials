#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
# The stack test: the demo household's whole stack (compose.yaml: Caddy, the store, the two workers) brought up with
# the product's own `ff`, attacked with the security matrix and driven by a signed-in browser, then brought down.
# Usage: tools/verify/stack.sh [--smoke] <scratch dir>
#   --smoke        the small run for a machine that cannot afford the whole matrix (the arm64 CI job): demo, up,
#                  /healthz 200, a sign-in 204, one export 200 with the session and 401 without, then down. No
#                  browser, no matrix; the last line is `stack smoke: 5/5`. Without it the full run below happens.
#   <scratch dir>  an absolute path with no whitespace, absent or empty; it gets install/ (ff copied in: the demo
#                  household's install directory), bin/ and the run's outputs. The secrets in it are removed at the end.
# It builds nothing: it runs the runtime images ci.sh builds, the harness's <prefix>-etl:rt and <prefix>-web:rt
# (tools/verify/harness.sh; FF_IMAGE_ETL and FF_IMAGE_WEB name others). The compose project is
# <project prefix>-stack, and every container and network it starts itself carries the harness's label and is named
# <project prefix>-stack-…. In order:
#   1. ./ff demo, then the passcode it prints is taken from its output into a 0600 file (the log says only that it was
#      captured; the generator takes no seed: the household is the same, the passcode random). .env gets the stack's
#      port (FF_PORT, FF_PORT_TLS the next one) and FF_HOSTNAMES=dash.example.net (a listed name the matrix asks for).
#   2. ./ff up (preflight, then compose up --wait: every service healthy) with FF_COMPOSE set to what runs compose
#      here: plain `docker compose -p <project>` on the install directory, or, when the harness names a compose
#      wrapper (FF_HARNESS_COMPOSE), `<wrapper> stack`, run with FF_COMPOSE_FILE naming the compose file and the port
#      in FF_STACK_PORT and in the variable FF_HARNESS_COMPOSE_PORT_VAR names (whose value, when already set, is the
#      port). The port is FF_STACK_PORT (default 18216), on the loopback address. A docker on PATH in bin/ adds the
#      harness's label to every `docker run` ff makes.
#   3. The AI step switched on (stack_ai.py on, on the host's python3): an invented key, the refresher recreated from a
#      copy of the install's compose.yaml (compose.ai-test.yaml, beside it: a wrapper may take one compose file and
#      want its bind mounts under that file's directory) whose refresher alone holds the test seam, and the provider's
#      stand-in (tests/fake_llm.py) in a container sharing the refresher's network namespace, so it is the
#      refresher's own loopback; the scheduler is stopped meanwhile. Switched on through the store as the dashboard
#      does it, then checked: the vendors truth says pass are filed by AI, the stand-in heard exactly them.
#   4. The browser run (stack_browser.py) in the screenshot harness's pinned Playwright image (never pulled:
#      --pull never), sharing web's network namespace so the stack is http://127.0.0.1:8080 to it, as the screenshot
#      harness reaches its server; with AI on, a row it filed shows its chip.
#   5. The AI step switched off and its answers forgotten (stack_ai.py off): every export but the manifest and the AI
#      status is the AI-off export byte for byte; the stand-in, the key and the copy removed, the refresher and the
#      scheduler as they were.
#   6. The matrix (stack_matrix.py, on the host's python3): every case one line, then `stack matrix: P/T`. It stops
#      and starts the store, and removes the passcode last, so it runs after the browser.
#   7. Always, on any way out: compose down, the browser's container, the AI stand-in and the matrix's stand-ins
#      removed by name, the secrets, the compose copy and the captured passcode deleted, and no container or network
#      of the project left (else exit 1).
# The last lines: each `known:` case of the matrix, then `stack: P/T` (the matrix's cases, the AI steps, and the
# browser run as one). Exit 0 only when the matrix and the AI steps failed nothing, the browser run was clean and
# nothing was left behind.
set -euo pipefail
W="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SMOKE=0
if [ "${1:-}" = --smoke ]; then SMOKE=1; shift; fi
S="${1:-}"
case "$S" in /*) ;; *) echo "usage: stack.sh [--smoke] <absolute scratch dir>" >&2; exit 2 ;; esac
[[ "$S" =~ [[:space:]] ]] && { echo "stack.sh: the scratch dir's path may hold no whitespace" >&2; exit 2; }
if [ -e "$S" ] && [ -n "$(ls -A "$S")" ]; then echo "stack.sh: $S is not empty" >&2; exit 2; fi
STARTED=$(date +%s)
# shellcheck source=tools/verify/harness.sh
. "$W/tools/verify/harness.sh"
LABEL=stack
PROJECT="$FF_HARNESS_PROJECT_PREFIX-$LABEL"
ETL_IMAGE="${FF_IMAGE_ETL:-$HARNESS_ETL_RT}"
WEB_IMAGE="${FF_IMAGE_WEB:-$HARNESS_WEB_RT}"
PW="${FF_PW_VERSION:-1.46.0}"
# the browser's image, pinned by digest (the tag v1.46.0-jammy as read on 2026-11-18): pulled by digest when it is not
# here, never by tag
PW_IMAGE="${FF_PW_IMAGE:-mcr.microsoft.com/playwright/python@sha256:98e88016a5705def757564f70469e80161c4e4dbf787bbe35a710fabb70bb7da}"
REAL_DOCKER="$(command -v docker)" || { echo "stack.sh: docker is not on PATH" >&2; exit 2; }
for img in "$ETL_IMAGE" "$WEB_IMAGE"; do
  docker image inspect "$img" >/dev/null 2>&1 || { echo "stack.sh: the image $img is not built (ci.sh builds it)" >&2; exit 2; }
done
LEFT="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")"
[ -z "$LEFT" ] || { echo "stack.sh: containers of $PROJECT are already there; remove them first" >&2; exit 2; }

install -d -m 700 "$S" "$S/install" "$S/bin"
INSTALL="$S/install"
cp "$W/ff" "$INSTALL/ff"
chmod 755 "$INSTALL/ff"
printf '#!/bin/sh\n# docker, with the harness label on every container docker run starts\nif [ "${1:-}" = run ]; then shift; exec "%s" run --label %s "$@"; fi\nexec "%s" "$@"\n' \
  "$REAL_DOCKER" "$FF_HARNESS_LABEL" "$REAL_DOCKER" >"$S/bin/docker"
chmod 755 "$S/bin/docker"
export PATH="$S/bin:$PATH"
export FF_IMAGE_ETL="$ETL_IMAGE" FF_IMAGE_WEB="$WEB_IMAGE"

AI_FILE="$INSTALL/compose.ai-test.yaml"    # stack_ai.py writes it and removes it
if [ -n "$FF_HARNESS_COMPOSE" ]; then
  case "$FF_HARNESS_COMPOSE" in /*) WRAPPER="$FF_HARNESS_COMPOSE" ;; *) WRAPPER="$W/$FF_HARNESS_COMPOSE" ;; esac
  [ -f "$WRAPPER" ] && [ -x "$WRAPPER" ] \
    || { echo "stack.sh: the compose wrapper $WRAPPER (FF_HARNESS_COMPOSE) is not an executable file" >&2; exit 2; }
  PORT="${FF_STACK_PORT:-18216}"
  if [ -n "$FF_HARNESS_COMPOSE_PORT_VAR" ]; then
    PORT="${!FF_HARNESS_COMPOSE_PORT_VAR:-$PORT}"
    export "$FF_HARNESS_COMPOSE_PORT_VAR=$PORT"
  fi
  export FF_STACK_PORT="$PORT" FF_COMPOSE_FILE="$INSTALL/compose.yaml"
  export FF_COMPOSE="$WRAPPER $LABEL"
  AI_COMPOSE="env FF_COMPOSE_FILE=$AI_FILE $WRAPPER $LABEL"
  HOW="$FF_HARNESS_COMPOSE"
else
  PORT="${FF_STACK_PORT:-18216}"
  export FF_COMPOSE="docker compose -p $PROJECT --project-directory $INSTALL -f $INSTALL/compose.yaml"
  AI_COMPOSE="docker compose -p $PROJECT --project-directory $INSTALL -f $AI_FILE"
  HOW="docker compose"
fi
[[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 18200 ] && [ "$PORT" -le 18219 ] \
  || { echo "stack.sh: the port must be in 18200-18219 (got $PORT)" >&2; exit 2; }
BROWSER="$PROJECT-browser-$$"
STANDIN="$PROJECT-llm-$$"
RESULT=""   # the stage's lines (stack_matrix.py --combine), printed last of all

cleanup() {
  rc=$?
  set +e
  "$REAL_DOCKER" rm -f "$BROWSER" "$STANDIN" >/dev/null 2>&1
  if [ -f "$INSTALL/compose.yaml" ]; then
    $FF_COMPOSE down --volumes --remove-orphans >"$S/down.log" 2>&1 || { cat "$S/down.log"; rc=1; }
  fi
  rm -rf "$INSTALL/secrets" "$S/passcode" "$S/demo.out" "$AI_FILE"
  LEFT="$("$REAL_DOCKER" ps -aq --filter "label=com.docker.compose.project=$PROJECT")
$("$REAL_DOCKER" ps -aq --filter "label=$FF_HARNESS_LABEL" --filter "name=$PROJECT-")
$("$REAL_DOCKER" network ls -q --filter "label=com.docker.compose.project=$PROJECT")
$("$REAL_DOCKER" network ls -q --filter "label=$FF_HARNESS_LABEL" --filter "name=$PROJECT-")"
  if [ -n "$(printf '%s' "$LEFT" | tr -d '[:space:]')" ]; then
    echo "stack: left behind: $(printf '%s' "$LEFT" | tr '\n' ' ')" >&2
    rc=1
  else
    echo "stack: down; no container or network of $PROJECT is left"
  fi
  echo "stack: $(( $(date +%s) - STARTED )) s"
  [ -z "$RESULT" ] || printf '%s\n' "$RESULT"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "== stack: the demo household through ./ff ($HOW, 127.0.0.1:$PORT, images $ETL_IMAGE $WEB_IMAGE) =="
cd "$INSTALL"
rc=0
(umask 077; ./ff demo >"$S/demo.out" 2>&1) || rc=$?
sed '/^Sign in with: /d' "$S/demo.out"
[ "$rc" -eq 0 ] || { echo "stack: ./ff demo failed (exit $rc)" >&2; exit 1; }
(umask 077; sed -n 's/^Sign in with: //p' "$S/demo.out" >"$S/passcode")
[ -s "$S/passcode" ] || { echo "stack: ./ff demo printed no passcode" >&2; exit 1; }
END_DAY="$(sed -n 's/.*its history ending \([0-9]\{4\}-[0-9][0-9]-[0-9][0-9]\).*/\1/p' "$S/demo.out" | head -n 1)"
[ -n "$END_DAY" ] || { echo "stack: ./ff demo did not say the day its history ends" >&2; exit 1; }
rm -f "$S/demo.out"
echo "stack: passcode captured"
python3 - "$INSTALL/.env" "$PORT" <<'PY'
import os
import sys

path, port = sys.argv[1], int(sys.argv[2])
want = {"FF_PORT": str(port), "FF_PORT_TLS": str(port + 1), "FF_HOSTNAMES": "dash.example.net"}
lines, seen = [], set()
for line in open(path).read().splitlines():
    key = line.split("=", 1)[0]
    if key in want:
        line = f"{key}={want[key]}"
        seen.add(key)
    lines.append(line)
lines += [f"{k}={v}" for k, v in want.items() if k not in seen]
tmp = path + ".stack"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as fh:
    fh.write("\n".join(lines) + "\n")
os.replace(tmp, path)
PY
./ff up
WEB="$($FF_COMPOSE ps -q web)"
[ -n "$WEB" ] || { echo "stack: no web container after ./ff up" >&2; exit 1; }

if [ "$SMOKE" = 1 ]; then
  echo "== stack: the smoke run (127.0.0.1:$PORT) =="
  python3 -B - "$PORT" "$S/passcode" <<'PY'
import http.client
import json
import sys

port, passcode_file = int(sys.argv[1]), sys.argv[2]
host = f"127.0.0.1:{port}"
passcode = open(passcode_file).read().strip()
done = 0


def ask(method, path, headers=(), body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    conn.request(method, path, body=body, headers={"Host": host, "Accept": "*/*", **dict(headers)})
    r = conn.getresponse()
    r.read()
    out = (r.status, r.getheaders())
    conn.close()
    return out


def step(name, ok, got):
    global done
    if not ok:
        print(f"stack smoke: FAILED: {name} (got {got})", file=sys.stderr)
        sys.exit(1)
    done += 1
    print(f"stack smoke: {name}: {got}")


status, _ = ask("GET", "/healthz")
step("GET /healthz -> 200", status == 200, status)
body = json.dumps({"passcode": passcode}).encode()
status, headers = ask("POST", "/auth/login", {"Origin": f"http://{host}", "Content-Type": "application/json",
                                              "Content-Length": str(len(body))}, body)
step("sign in -> 204", status == 204, status)
session = next((v.split(";", 1)[0] for k, v in headers if k.lower() == "set-cookie" and v.startswith("ff_session=")), "")
step("the sign-in set a session cookie", bool(session), "a cookie" if session else "none")
status, _ = ask("GET", "/manifest.json", {"Cookie": session})
step("export with the session -> 200", status == 200, status)
status, _ = ask("GET", "/manifest.json")
step("export without it -> 401", status == 401, status)
print(f"stack smoke: {done}/5")
PY
  exit 0
fi

ai() {   # stack_ai.py PHASE, its lines added to ai.out; its exit status in arc
  python3 -B "$W/tools/verify/stack_ai.py" "$1" --port "$PORT" --passcode-file "$S/passcode" --compose "$FF_COMPOSE" \
    --ai-compose "$AI_COMPOSE" --install "$INSTALL" --work "$S/ai" --etl-image "$ETL_IMAGE" \
    --fake "$W/tests/fake_llm.py" --standin "$STANDIN" --end "$END_DAY" 2>&1 | tee -a "$S/ai.out" || arc=$?
}
arc=0
install -d -m 700 "$S/ai"
: >"$S/ai/ai-vendors.txt"   # the AI-filed vendors' names, which the on phase writes for the browser
echo "== stack: the AI step switched on (its provider a stand-in on the refresher's loopback) =="
ai on

echo "== stack: the browser (playwright python $PW, in web's network namespace) =="
docker image inspect "$PW_IMAGE" >/dev/null 2>&1 || "$REAL_DOCKER" pull -q "$PW_IMAGE" >/dev/null
brc=0
"$REAL_DOCKER" run --rm --pull never --label "$FF_HARNESS_LABEL" --name "$BROWSER" --network "container:$WEB" \
  --shm-size 1g -e PYTHONDONTWRITEBYTECODE=1 -e "PW_VERSION=$PW" \
  -v "$W/tools/verify/stack_browser.py:/stack_browser.py:ro" \
  -v "$W/tools/verify/screenshots_driver.py:/screenshots_driver.py:ro" \
  -v "$W/app/src/App.tsx:/App.tsx:ro" \
  -v "$S/passcode:/run/ff-passcode:ro" \
  -v "$S/ai/ai-vendors.txt:/run/ai-vendors.txt:ro" \
  "$PW_IMAGE" sh -c 'pip install -q --disable-pip-version-check --root-user-action=ignore "playwright==$PW_VERSION" \
    && python /stack_browser.py --base http://127.0.0.1:8080 --passcode-file /run/ff-passcode --app-tsx /App.tsx \
       --driver /screenshots_driver.py --ai-vendors-file /run/ai-vendors.txt' 2>&1 | tee "$S/browser.out" || brc=$?

echo "== stack: the AI step switched off, its answers forgotten =="
ai off

echo "== stack: the matrix =="
mrc=0
python3 -B "$W/tools/verify/stack_matrix.py" --port "$PORT" --passcode-file "$S/passcode" --compose "$FF_COMPOSE" \
  --ff "$INSTALL/ff" --etl-image "$ETL_IMAGE" --web-image "$WEB_IMAGE" 2>&1 | tee "$S/matrix.out" || mrc=$?

crc=0
RESULT="$(python3 -B "$W/tools/verify/stack_matrix.py" --combine "$S/matrix.out" "$S/browser.out" "$mrc" "$brc" \
  --ai "$S/ai.out" "$arc")" || crc=$?
exit "$crc"

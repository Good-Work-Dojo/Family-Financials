#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
# The arm64 smoke test, run by CI's arm64 job on GitHub's own arm runner (ubuntu-24.04-arm); it is not meant for any
# other machine and exits 2 anywhere uname -m is not aarch64. It builds the two runtime images natively under the
# harness's runtime tags (tools/verify/harness.sh; the ETL image picks its supercronic by TARGETARCH, so this is the
# build that proves the arm64 pin), checks what matters on arm (the image's architecture, supercronic, the etl package,
# caddy), then runs the small stack test (stack.sh --smoke: demo household, up, /healthz, sign-in, one export with and
# without the session, down). Last line: `arm64 smoke: OK`.
set -euo pipefail
W="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$W"
if [ "$(uname -m)" != aarch64 ]; then
  echo "arm_smoke.sh runs on an arm64 machine only (this one is $(uname -m)); CI's arm64 job runs it" >&2
  exit 2
fi
# shellcheck source=tools/verify/harness.sh
. "$W/tools/verify/harness.sh"

echo "== arm64: build the runtime ETL and web images natively =="
docker build --label "$FF_HARNESS_LABEL" --target etl -t "$HARNESS_ETL_RT" -f deploy/Dockerfile.etl . \
  || { echo "docker build of $HARNESS_ETL_RT failed" >&2; exit 1; }
docker build --label "$FF_HARNESS_LABEL" --target web -t "$HARNESS_WEB_RT" -f deploy/Dockerfile.web . \
  || { echo "docker build of $HARNESS_WEB_RT failed" >&2; exit 1; }

echo "== arm64: check the images =="
for tag in "$HARNESS_ETL_RT" "$HARNESS_WEB_RT"; do
  ARCH="$(docker image inspect -f '{{.Architecture}}' "$tag")"
  [ "$ARCH" = arm64 ] || { echo "$tag is $ARCH, not arm64" >&2; exit 1; }
  echo "arm64: $tag is $ARCH"
done
docker run --rm --label "$FF_HARNESS_LABEL" --network none "$HARNESS_ETL_RT" supercronic -version \
  || { echo "supercronic does not run in the arm64 ETL image" >&2; exit 1; }
docker run --rm --label "$FF_HARNESS_LABEL" --network none "$HARNESS_ETL_RT" python -c 'import etl; print("etl imports from", etl.__file__)' \
  || { echo "the etl package does not import in the arm64 ETL image" >&2; exit 1; }
docker run --rm --label "$FF_HARNESS_LABEL" --network none "$HARNESS_WEB_RT" caddy version \
  || { echo "caddy does not run in the arm64 web image" >&2; exit 1; }

echo "== arm64: the stack smoke test =="
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT
rc=0
bash "$W/tools/verify/stack.sh" --smoke "$SCRATCH/run" || rc=$?
[ "$rc" -eq 0 ] || { echo "the arm64 stack smoke test failed (exit $rc; its lines above)" >&2; exit 1; }

echo "arm64 smoke: OK"

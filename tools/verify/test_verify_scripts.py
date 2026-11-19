# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""ci.sh, export-diff.py, screenshots.sh and narrow.sh, run with a stub docker (plus curl, jq, git and, where a test
needs a home directory, getent) first on PATH.

Exports are treated as real unless FF_DEMO_EXPORTS=1: a real exports directory must resolve strictly inside the
scratch root (FF_SCRATCH, a temporary directory here), a synthetic one never at or inside a real-data root, and nothing
reaches docker until the arguments pass. ci.sh runs
from a copy laid out as a minimal repository, so its host-side checks see only sample files. Nothing here touches a real
Docker daemon or the network, and nothing writes into this repository (one test reads its copies of the demo profile
list and end date out of the files that hold them)."""
from __future__ import annotations

import ast
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tools.demo import generate, profiles

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import harness_names  # noqa: E402  (tools/verify is not a package)
import licences  # noqa: E402
import screenshots_driver as driver  # noqa: E402

# The harness's names (no FF_ variable reaches a script here): a copy laid out without tools/private (the ci.sh rig)
# takes the defaults; a script run where it sits takes this repository's settings (the defaults in the image, which
# holds no tools/private).
NEUTRAL = harness_names.load(HERE / "no-such-tree", {})
OWN = harness_names.load(ROOT, {})

EXPORT_DIFF = HERE / "export-diff.py"
SHOTS = HERE / "screenshots.sh"
NARROW = HERE / "narrow.sh"
ETL_ID = "sha256:" + "e" * 64
WEB_ID = "sha256:" + "f" * 64
# the runtime images ci.sh builds last (the ETL image's default target, the web server) and the containers it checks
# them with: what the ETL image holds, its .pth files, validate, ingest, the import from a directory with an etl of its own,
# supercronic, its tzdata, the scheduler's crontab in three zones, what the web image holds, then twice (the image's
# defaults, a domain) /healthz, and validate, adapt and tests/test_caddyfile.py twice; then the licence check's reads
# of the ETL image's distributions and of the npm packages' licence files in the web build image, and its copies of
# the two Go programs (Caddy, supercronic) out of the runtime images
ETL_RT_ID = "sha256:" + "a" * 64
WEB_RT_ID = "sha256:" + "b" * 64
RT_CHECKS = 23
# the dashboard's lockfile in the ci.sh rig: one runtime package, one dev-only
SAMPLE_LOCK = {"name": "app", "lockfileVersion": 3, "packages": {
    "": {"name": "app"}, "node_modules/left-pad": {"version": "1.3.0", "license": "MIT"},
    "node_modules/build-tool": {"version": "2.0.0", "license": "GPL-3.0-only", "dev": True}}}
# the runtime package's licence file, as the stub's web build image answers licences.py's read
SAMPLE_TEXTS = {"node_modules/left-pad": [["LICENSE", "The left-pad licence.\n"]]}
# the Go programs the stub's runtime images carry: one build info (the Go version and one module) for both, and the
# licence texts the rig's tree keeps for them
SAMPLE_GO_MODULES = b"0" * 16 + b"mod\texample.org/sample\tv1.0.0\t\n" + b"9" * 16
SAMPLE_GO_BINARY = (b"\x7fELF" + b"\0" * 60 + licences.BUILDINFO_MAGIC + bytes([8, 2]) + b"\0" * 16
                    + bytes([8]) + b"go1.27.3" + bytes([len(SAMPLE_GO_MODULES)]) + SAMPLE_GO_MODULES)
SAMPLE_GO_TEXTS = {"std@go1.27.3": {"LICENSE": "The Go licence.\n"},
                   "example.org/sample@v1.0.0": {"LICENSE": "The sample module's licence.\n"}}
GITLEAKS_PIN = re.compile(r"ghcr\.io/gitleaks/gitleaks:v[0-9.]+@sha256:[0-9a-f]{64}")
LEAK = "1234.56"
# the dashboard tests ci.sh runs on exports it is given: the contract, the rollups' cross-check and the route matrix
REDUCED = "src/data/contract.test.ts src/lib/agg.test.ts src/matrix.test.tsx"

PASSING_REPORT = {"testResults": [{"name": "/w/app/src/sample.test.ts", "status": "passed", "assertionResults": [
    {"fullName": "sample adds up", "status": "passed", "failureMessages": []}]}]}

# Records each call as one JSON line and plays the containers the scripts start: image ids for build / inspect, the
# vitest report (whose console output quotes an amount), the SPA build output and the screenshot driver's files.
DOCKER = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
ETL, WEB = "sha256:" + "e" * 64, "sha256:" + "f" * 64


def mounted(target):
    for i, a in enumerate(args[:-1]):
        if a == "-v" and args[i + 1].split(":")[1] == target:
            return Path(args[i + 1].split(":")[0])
    return None


etl = any("-etl:" in a for a in args)
if args[:1] == ["build"]:
    # the runtime targets print ids of their own, so a test can tell which image a container ran
    target = args[args.index("--target") + 1] if "--target" in args else ""
    print({"etl": "sha256:" + "a" * 64, "web": "sha256:" + "b" * 64}.get(target, ETL if etl else WEB))
    sys.exit(int(os.environ.get("STUB_BUILD_RC", "0")))
elif args[:2] == ["image", "inspect"]:
    if os.environ.get("STUB_NO_IMAGE"):
        sys.exit(1)
    if "{{json .Config.Labels}}" in args:
        # a runtime image's labels: the project's, or STUB_LABELS (the web image's then)
        title = "Family Financials ETL" if "sha256:" + "a" * 64 in args else "Family Financials web"
        source = "https://github.com/Good-Work-Dojo/Family-Financials"
        labels = {f"org.opencontainers.image.{k}": v for k, v in {
            "title": title, "vendor": "Family Financials", "source": source, "url": source, "documentation": source,
            "licenses": "AGPL-3.0-only", "version": "1.0.0", "description": "Family Financials: sample"}.items()}
        if os.environ.get("STUB_LABELS") and title.endswith("web"):
            labels.update(json.loads(os.environ["STUB_LABELS"]))
        print(json.dumps(labels))
    elif "{{.Size}}" in args:
        print(os.environ.get("STUB_SIZE", "209459471"))
    elif "--format" in args:
        print(ETL if etl else WEB)
elif args[:1] == ["run"]:
    script = args[-1]
    out, shots = mounted("/out"), mounted("/shots")
    if "--entrypoint" in args and args[args.index("--entrypoint") + 1] == "cat":
        # a Go program copied out of a runtime image, whose build info licences.py reads: STUB_GO_BINARY's bytes
        sys.stdout.buffer.write(Path(os.environ["STUB_GO_BINARY"]).read_bytes())
    elif any(a.startswith("ghcr.io/gitleaks/") for a in args):
        # the secret scan: no finding, unless STUB_LEAKS names the mode (dir or git) that finds one; the files a dir
        # scan was given are listed in STUB_LOG.scan
        if "dir" in args:
            scanned = mounted("/scan")
            with open(os.environ["STUB_LOG"] + ".scan", "w") as fh:
                fh.write(json.dumps(sorted(p.relative_to(scanned).as_posix() for p in scanned.rglob("*")
                                           if p.is_file())))
        if os.environ.get("STUB_LEAKS") in args:
            print("Finding:     REDACTED\nRuleID:      generic-api-key\nWRN leaks found: 1")
            sys.exit(1)
        print("INF no leaks found")
    elif any(a.startswith("rhysd/actionlint:") for a in args):
        # actionlint: no finding, unless STUB_ACTIONLINT_RC says otherwise
        if os.environ.get("STUB_ACTIONLINT_RC"):
            print("/flows/release.yml:3:1: a sample finding [syntax-check]")
            sys.exit(int(os.environ["STUB_ACTIONLINT_RC"]))
    elif any(a.startswith("koalaman/shellcheck:") for a in args):
        # shellcheck: no finding, unless STUB_SHELLCHECK_RC says otherwise
        if os.environ.get("STUB_SHELLCHECK_RC"):
            print("In /scripts/a.sh line 2: a sample finding")
            sys.exit(int(os.environ["STUB_SHELLCHECK_RC"]))
    elif "node" in args and args[args.index("node") + 1] == "-e":
        # the npm packages' licence files, as licences.py reads them in the web build image (STUB_NPM_TEXTS replaces
        # them)
        print(os.environ.get("STUB_NPM_TEXTS", json.dumps({"node_modules/left-pad": [["LICENSE", "The left-pad licence.\n"]]})))
    elif script == "--collect":
        # the runtime ETL image's distributions, as licences.py reads them (STUB_DISTS replaces them)
        print(os.environ.get("STUB_DISTS", "[]"))
    elif out and mounted("/exports") and "contract.test.ts" in script:
        # a demo profile's container (<demo>/<profile>/data/exports at /exports): the contract, its marker, the matrix
        profile = mounted("/exports").parts[-3]
        if os.environ.get("STUB_CONTRACT_FAIL") == profile:
            sys.exit(1)
        (out / "contract.ok").write_text("")
        if os.environ.get("STUB_MATRIX_NO_REPORT") != profile:
            status = {"STUB_MATRIX_FAIL": "failed", "STUB_MATRIX_SKIP": "skipped"}
            bad = next((s for k, s in status.items() if os.environ.get(k) == profile), None)
            results = [{"fullName": "/ — renders", "status": "passed"}, {"fullName": "/data — values (known gap)", "status": "passed"}]
            if bad:
                results.append({"fullName": "/accounts — renders", "status": bad})
            (out / "matrix.json").write_text(json.dumps({"testResults": [
                {"name": "/w/app/src/matrix.test.tsx", "status": "failed" if bad == "failed" else "passed", "assertionResults": results}]}))
        print("AssertionError: expected 1234.56 to equal 99")
        sys.exit(1 if os.environ.get("STUB_MATRIX_RC") == profile or os.environ.get("STUB_MATRIX_FAIL") == profile else 0)
    elif out and mounted("/exports") and "matrix.test.tsx" in script:
        # a zone run: the matrix alone on one profile, in the zone its FF_TEST_TZ names (STUB_ZONE_* name the zone)
        zone = next(a.split("=", 1)[1] for a in args if a.startswith("FF_TEST_TZ="))
        if os.environ.get("STUB_ZONE_NO_REPORT") != zone:
            results = [{"fullName": "/ — renders", "status": "passed"}, {"fullName": "/data — values (known gap)", "status": "passed"}]
            if os.environ.get("STUB_ZONE_FAIL") == zone:
                results.append({"fullName": "/cash-flow — renders", "status": "failed"})
            (out / "matrix.json").write_text(json.dumps({"testResults": [
                {"name": "/w/app/src/matrix.test.tsx", "status": "passed", "assertionResults": results}]}))
        sys.exit(1 if zone in (os.environ.get("STUB_ZONE_FAIL"), os.environ.get("STUB_ZONE_RC")) else 0)
    elif out and any("tools.demo.generate" in a for a in args):
        # the demo container: its last two arguments are the profile and the end date; it leaves the ingest's exports
        failing = os.environ.get("STUB_DEMO_FAIL") == args[-2]
        if os.environ.get("STUB_DEMO_RC") == args[-2]:
            sys.exit(1)
        exports = out / args[-2] / "data" / "exports"
        exports.mkdir(parents=True)
        if os.environ.get("STUB_DEMO_NO_TRUTH") != args[-2]:
            (out / args[-2] / "truth.json").write_text(json.dumps({"profile": args[-2]}))
        counted = os.environ.get("STUB_DEMO_NO_COUNT") != args[-2]
        files = {"files": ["manifest.json", "quality_report.json"]}
        (exports / "manifest.json").write_text(json.dumps({"transaction_count": 42, **files} if counted else files))
        (exports / "quality_report.json").write_text(json.dumps({"checks": [
            {"name": "a", "status": "pass"}, {"name": "b", "status": "warn"}, {"name": "c", "status": "warn"},
            {"name": "d", "status": "fail" if failing else "pass"}]}))
    elif out and "vitest" in script:
        (out / "vitest.json").write_text(os.environ["STUB_VITEST"])
        print("AssertionError: expected 1234.56 to equal 99")
        sys.exit(int(os.environ.get("STUB_VITEST_RC", "0")))
    elif out and mounted("/public") and "vite build" in script:
        # the demo site's build: the staged public directory, a page carrying the rig Caddyfile's policy less
        # frame-ancestors, the bundle with the rig's banner words, the theme script; STUB_DEMO_SITE_BAD makes the page
        # name an icon from the root
        import shutil
        shutil.copytree(mounted("/public"), out, dirs_exist_ok=True)
        icon = "/icons/icon.svg" if os.environ.get("STUB_DEMO_SITE_BAD") else "./icons/icon.svg"
        (out / "index.html").write_text(
            "<!doctype html><html><head><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'\">"
            f'<script src="./theme.js"></script><link rel="icon" href="{icon}"></head><body></body></html>')
        (out / "assets").mkdir(exist_ok=True)
        (out / "assets" / "index.js").write_text('const b="Demo: sample.";')
        (out / "theme.js").write_text("")
        sys.exit(int(os.environ.get("STUB_DEMO_BUILD_RC", "0")))
    elif out and "vite build" in script:
        (out / "dist").mkdir(parents=True, exist_ok=True)
        # a build whose publicDir held an export (STUB_DIST_EXPORT names the file vite would copy into dist)
        if os.environ.get("STUB_DIST_EXPORT"):
            (out / "dist" / os.environ["STUB_DIST_EXPORT"]).write_text("{}")
    elif shots:
        # the driver: STUB_SHOT_PNGS pictures (by default Home's four plus the open drawer in each theme) and the
        # overflow report STUB_OVERFLOW gives
        (shots / "overflow.json").write_text(os.environ.get("STUB_OVERFLOW", "[]"))
        for i in range(int(os.environ.get("STUB_SHOT_PNGS", "6"))):
            (shots / f"shot{i}.png").write_bytes(b"")
    elif "no:cacheprovider tools/" in script:
        # the harness's and the private tooling's own runs: each prints its summary, a skip included, which the gate
        # neither counts nor refuses; the private one starts with its ruff check (STUB_PRIVATE_RUFF_RC fails it)
        if "ruff check tools/private" in script:
            if os.environ.get("STUB_PRIVATE_RUFF_RC"):
                print("tools/private/helper.py:1:1: F401 `os` imported but unused")
                sys.exit(int(os.environ["STUB_PRIVATE_RUFF_RC"]))
            print("All checks passed!")
        print("4 passed, 1 skipped in 0.12s")
    elif "ruff check" in script:
        # the ETL suite: ruff, then pytest -q, whose last line is its summary (STUB_PYTEST_LAST replaces it)
        print("All checks passed!")
        print("." * 40 + " [100%]")
        print(os.environ.get("STUB_PYTEST_LAST", os.environ.get("STUB_PYTEST_PASSED", "123") + " passed in 4.56s"))
        sys.exit(int(os.environ.get("STUB_PYTEST_RC", "0")))
    elif "config_dir" in script:
        # ci.sh's image check: the place the image's ETL looks for a config, printed when something is there
        print(os.environ.get("STUB_IMAGE_CONFIG", ""))
        sys.exit(int(os.environ.get("STUB_IMAGE_CHECK_RC", "0")))
    # the runtime images' stage: each check answers as a sound image does unless its STUB_RT_* / STUB_CADDY_* says
    elif '.endswith(".pth")' in script:
        # the runtime ETL image's .pth files: the allowed two, or STUB_RT_PTH's list
        print(os.environ.get("STUB_RT_PTH", "/opt/venv/lib/python3.12/site-packages/_virtualenv.pth\n"
                                            "/opt/venv/lib/python3.12/site-packages/ff.pth"))
    elif "os.walk" in script:
        if os.environ.get("STUB_RT_HOLDS"):
            print(os.environ["STUB_RT_HOLDS"])
            sys.exit(1)
    elif args[-3:] == ["-m", "etl", "validate"]:
        print(os.environ.get("STUB_RT_VALIDATE", "config OK (2 files, 1 accounts)"))
    elif "etl ingest" in script:
        if not os.environ.get("STUB_RT_NO_MANIFEST"):
            (mounted("/h/data") / "exports").mkdir(parents=True)
            (mounted("/h/data") / "exports" / "manifest.json").write_text("{}")
    elif "etl.__file__" in script:
        print(os.environ.get("STUB_RT_IMPORTED", "/opt/ff/etl/__init__.py /tmp"))
    elif args[-1] == "-version":
        print("v0.2.51")
    elif "zoneinfo" in script:
        sys.exit(int(os.environ.get("STUB_RT_TZDATA_RC", "0")))
    elif "make_crontab.py" in script:
        # the scheduler's crontab for the household mounted at /c, then supercronic's word on it; STUB_RT_CRONTAB_RC
        # names a zone whose crontab supercronic refuses, STUB_RT_CRONTAB_WRONG one whose hours are not the household's
        zone = next(line.split(":", 1)[1].strip() for line in (mounted("/c") / "household.yaml").read_text().splitlines()
                    if line.startswith("timezone:"))
        if os.environ.get("STUB_RT_CRONTAB_RC") == zone:
            print(f'level=fatal msg="unknown time zone {zone}"')
            sys.exit(1)
        hours = (2, 9, 17) if os.environ.get("STUB_RT_CRONTAB_WRONG") == zone else (1, 9, 17)
        print('level=info msg="crontab is valid"')
        print(f"# the scheduled refresh\nCRON_TZ={zone}")
        print("\n".join(f"25 {h} * * * /opt/ff/deploy/refresh.sh" for h in hours))
    elif "/srv/public" in script:
        if os.environ.get("STUB_WEB_HOLDS"):
            print(os.environ["STUB_WEB_HOLDS"])
            sys.exit(1)
    elif "healthz" in script:
        print(os.environ.get("STUB_RT_HEALTHZ", "ok"))
    elif "caddy" in args:
        # validate, then adapt, each logging to stderr, as Caddy does the one warning of a site on the HTTP port;
        # STUB_CADDY_WARN adds another warning, STUB_CADDY_RC refuses
        print('{"level":"warn","ts":1.5,"logger":"http.auto_https","msg":"server is listening only on the HTTP port, '
              'so no automatic HTTPS will be applied to this server","server_name":"srv0"}', file=sys.stderr)
        if os.environ.get("STUB_CADDY_WARN"):
            print('{"level":"warn","msg":"a sample warning"}', file=sys.stderr)
        if "validate" in args:
            print("Valid configuration")
            sys.exit(int(os.environ.get("STUB_CADDY_RC", "0")))
        print('{"apps": {}}')
    elif "test_caddyfile.py" in script:
        sys.exit(int(os.environ.get("STUB_CADDY_TEST_RC", "0")))
    elif "-d" in args:
        print("sample-container")
    elif "vitest run" in script:
        print("AssertionError: expected 1234.56 to equal 99")
'''


def assert_stub_docker(bindir: Path, path: str) -> None:
    """Fails the test unless the docker a script would run is the stub: a rig never reaches a real daemon."""
    found = shutil.which("docker", path=path)
    assert found == str(bindir / "docker"), f"docker on PATH is {found}, not the stub in {bindir}"


class Rig:
    """A stub bin directory, a scratch root holding head exports, and exports outside it."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.log = tmp_path / "docker.calls"
        self.scratch = tmp_path / "scratch"
        self.exports = self.exports_at(self.scratch / "head" / "data" / "exports")
        self.outside = self.exports_at(tmp_path / "elsewhere" / "exports")
        self.suite_log = self.scratch / "head" / "suite.log"
        self.passwd_home = tmp_path / "passwd-home"
        self.go_binary = tmp_path / "go-binary"
        self.go_binary.write_bytes(SAMPLE_GO_BINARY)
        self.stub("docker", DOCKER)
        # curl records each URL it is asked for (one line each, in docker.calls.curl) and answers /api/scenarios
        self.stub("curl", '#!/usr/bin/env bash\nfor a in "$@"; do case "$a" in http*) echo "$a" >>"$STUB_LOG.curl" ;; esac; done\n'
                          'case "$*" in *"/api/scenarios"*) echo \'{"scenarios": [{}]}\' ;; esac\n')
        self.stub("jq", "#!/usr/bin/env bash\ncat >/dev/null\necho 1\n")
        # A checkout unless STUB_NOT_A_CHECKOUT=1, when every call fails as git does outside one. In a checkout
        # the secret scan's `git ls-files -z --cached --others …` lists STUB_SHIPPED (nothing by default), any other
        # `git ls-files` lists STUB_TRACKED (nothing by default) and exits STUB_LS_FILES_RC, `rev-parse
        # --is-inside-work-tree` prints true, `rev-parse --show-toplevel` prints STUB_TOPLEVEL (by default the
        # directory `-C` names: that directory is the top of its work tree) and any other call prints a hash.
        self.stub("git", '#!/usr/bin/env bash\n'
                         'if [ "${STUB_NOT_A_CHECKOUT:-}" = 1 ]; then\n'
                         '  echo "fatal: not a git repository (or any of the parent directories): .git" >&2; exit 128\n'
                         'fi\n'
                         'case " $* " in\n'
                         '  *" ls-files -z --cached --others "*) printf "%b" "${STUB_SHIPPED:-}" ;;\n'
                         '  *" ls-files "*) printf "%b" "${STUB_TRACKED:-}"; exit "${STUB_LS_FILES_RC:-0}" ;;\n'
                         '  *" --is-inside-work-tree "*) echo true ;;\n'
                         '  *" --show-toplevel "*) if [ -n "${STUB_TOPLEVEL:-}" ]; then echo "$STUB_TOPLEVEL";\n'
                         '    elif [ "$1" = -C ]; then echo "$2"; else pwd; fi ;;\n'
                         '  *) echo abc1234 ;;\nesac\n')
        getent = shutil.which("getent")
        self.stub("getent", "#!/usr/bin/env bash\n"
                            'if [ -z "${STUB_HOME:-}" ]; then exec ' + getent + ' "$@"; fi\n'
                            'echo "sample:x:$(id -u):$(id -g)::$STUB_HOME:/bin/bash"\n')

    @staticmethod
    def exports_at(d: Path) -> Path:
        d.mkdir(parents=True)
        (d / "manifest.json").write_text('{"schema_version": 1}')
        return d

    def stub(self, name: str, body: str) -> None:
        f = self.bin / name
        f.write_text(body)
        f.chmod(0o755)

    def run(self, cmd, env=None, cwd=None):
        full = {k: v for k, v in os.environ.items() if not k.startswith(("FF_", "STUB_"))}
        full.update({"PATH": f"{self.bin}:{os.environ['PATH']}", "STUB_LOG": str(self.log),
                     "FF_SCRATCH": str(self.scratch), "STUB_VITEST": json.dumps(PASSING_REPORT),
                     "STUB_GO_BINARY": str(self.go_binary)})
        for k, v in (env or {}).items():
            if v is None:
                full.pop(k, None)
            else:
                full[k] = str(v)
        assert_stub_docker(self.bin, full["PATH"])
        return subprocess.run(cmd, env=full, cwd=cwd or self.tmp, capture_output=True, text=True)

    def calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def runs(self) -> list[list[str]]:
        """the containers the scripts start from the images they build or name (the secret scan's aside)"""
        return [c for c in self.calls() if c[:1] == ["run"] and not _is_scan(c)]

    def scans(self) -> list[list[str]]:
        """the secret scan's containers"""
        return [c for c in self.calls() if c[:1] == ["run"] and _is_scan(c)]


def _is_scan(call: list[str]) -> bool:
    return any(a.startswith("ghcr.io/gitleaks/") for a in call)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


# ---------------------------------------------------------------- ci.sh


# Stands in for tests/test_scope.py, which would read this repository's whole tree: it prints a summary line and exits
# with STUB_SCOPE_RC.
SCOPE = '''import os
import sys

print("scope: 1 roots, 1 files scanned")
sys.exit(int(os.environ.get("STUB_SCOPE_RC", "0")))
'''


# Stands in for tools/verify/stack.sh, which brings a real stack up: it records its arguments (STUB_LOG.stack),
# prints a known case and its last line (STUB_STACK_LINE, `stack: 12/13` by default) and exits with STUB_STACK_RC.
STACK = '''#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$STUB_LOG.stack"
echo "ok sample case"
echo "known: sample: recorded (status 401, want 404)"
echo "${STUB_STACK_LINE-stack: 12/13}"
exit "${STUB_STACK_RC:-0}"
'''


@pytest.fixture
def ci(rig):
    """ci.sh copied into a minimal repository with sample context files, a stub scope test and a stub stack test."""
    repo = rig.tmp / "repo"
    (repo / "tools" / "verify").mkdir(parents=True)
    (repo / "app").mkdir()
    (repo / "tests").mkdir()
    for name in ("ci.sh", "demo.sh", "harness.sh", "check_workflows.py", "suite_summary.py", "licences.py",
                 "demo_site.sh", "demo_site.py"):
        shutil.copy(HERE / name, repo / "tools" / "verify" / name)
    # what the demo site's staging and checks read: the web server's policy, the icons and manifest, the source address
    # and the banner's words
    (repo / "deploy" / "public" / "icons").mkdir(parents=True)
    (repo / "deploy" / "public" / "icons" / "icon.svg").write_text("<svg/>")
    (repo / "deploy" / "public" / "site.webmanifest").write_text('{"start_url": "/", "icons": [{"src": "/icons/icon.svg"}]}')
    (repo / "deploy" / "Caddyfile").write_text("""\tContent-Security-Policy "default-src 'none'; frame-ancestors 'none'"\n""")
    (repo / "app" / "src" / "lib").mkdir(parents=True)
    (repo / "app" / "src" / "lib" / "product.ts").write_text('const DEFAULT_SOURCE_URL = "https://example.net/source";\n')
    (repo / "app" / "src" / "lib" / "demo.ts").write_text('const DEMO_BANNER_TEXT = "Demo: sample.";\n')
    # what the licence check reads (a one-package lockfile, the notices with their block for it and the stub image's
    # empty Python closure) and the secret scan's rules
    (repo / "app" / "package-lock.json").write_text(json.dumps(SAMPLE_LOCK))
    (repo / "THIRD_PARTY_NOTICES.md").write_text(
        "# Notices\n\n" + licences.block(licences.npm_closure(SAMPLE_LOCK), []) + "\n")
    # the licence texts the stub's web build image gives and the kept texts of the Go modules its runtime images' programs
    # are built from, and the program's own licence and notice, which the runtime images' checks compare each image's
    # copies with
    for key, files in SAMPLE_GO_TEXTS.items():
        (repo / licences.GO_DIR / key).mkdir(parents=True)
        for name, text in files.items():
            (repo / licences.GO_DIR / key / name).write_text(text)
    go = licences.go_modules({"caddy": SAMPLE_GO_BINARY, "supercronic": SAMPLE_GO_BINARY})
    (repo / "THIRD_PARTY_LICENSES").write_text(
        licences.texts_file(licences.npm_closure(SAMPLE_LOCK), SAMPLE_TEXTS, go, SAMPLE_GO_TEXTS))
    (repo / "LICENSE").write_text("Sample licence\n")
    (repo / "NOTICE").write_text("Sample notice\n")
    (repo / ".gitleaks.toml").write_text("[extend]\nuseDefault = true\n")
    (repo / "tools" / "verify" / "stack.sh").write_text(STACK)
    (repo / "tests" / "test_scope.py").write_text(SCOPE)
    (repo / "AGENTS.md").write_text("# Sample\n")
    (repo / "app" / "AGENTS.md").write_text("# Sample app\n")
    # what the runtime images' stage reads from the tree: the smallest household and the sign-in page's files
    (repo / "examples" / "minimal" / "config").mkdir(parents=True)
    (repo / "examples" / "minimal" / "config" / "household.yaml").write_text("version: 1\n")
    (repo / "deploy" / "public").mkdir(parents=True, exist_ok=True)
    (repo / "deploy" / "public" / "login.html").write_text("<!doctype html>\n")
    script = repo / "tools" / "verify" / "ci.sh"
    return lambda *args, **env: rig.run(["bash", str(script), *map(str, args)], env)


def test_ci_sh_without_exports_needs_no_suite_log(rig, ci):
    r = ci()
    assert r.returncode == 0, r.stderr
    assert "CI OK" in r.stdout
    assert "scope: 1 roots, 1 files scanned" in r.stdout


def test_ci_sh_stops_before_building_when_the_scope_test_fails(rig, ci):
    r = ci(STUB_SCOPE_RC="1")
    assert r.returncode == 1
    assert "CI OK" not in r.stdout
    assert "tests/test_scope.py failed" in r.stderr
    assert rig.calls() == []


ACTIONLINT_PIN = re.compile(r"rhysd/actionlint:[0-9.]+@sha256:[0-9a-f]{64}")
WORKFLOW = "on: push\njobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: true\n"


def test_ci_sh_runs_actionlint_offline_over_every_workflow_before_any_build_and_a_finding_fails(rig, ci):
    flows = rig.tmp / "repo" / ".github" / "workflows"
    flows.mkdir(parents=True)
    (flows / "a.yml").write_text(WORKFLOW)
    (flows / "b.yaml").write_text(WORKFLOW)
    (flows / "notes.txt").write_text("not a workflow\n")
    r = ci()
    assert r.returncode == 0, r.stderr
    assert "workflows: actionlint finds nothing in 2 workflow(s)" in r.stdout.splitlines()
    lint = rig.calls()[0]
    image = next(a for a in lint if a.startswith("rhysd/actionlint:"))
    assert ACTIONLINT_PIN.fullmatch(image), image
    assert lint[:2] == ["run", "--rm"] and lint[lint.index("--network") + 1] == "none"
    assert lint[lint.index("--label") + 1] == "family-financials.test=1"
    assert lint[lint.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert f"{flows}:/flows:ro" in lint
    assert lint[lint.index(image) + 1:] == ["-no-color", "/flows/a.yml", "/flows/b.yaml"]
    assert rig.calls()[1][:1] == ["build"]
    rig.log.unlink()
    r = ci(STUB_ACTIONLINT_RC="1")
    assert r.returncode == 1 and "actionlint found a problem in a workflow" in r.stderr
    assert "/flows/release.yml:3:1: a sample finding [syntax-check]" in r.stdout
    assert not [c for c in rig.calls() if c[:1] == ["build"]]


def test_ci_sh_says_so_when_there_is_no_workflow_for_actionlint(rig, ci):
    r = ci()
    assert r.returncode == 0 and "workflows: no workflow for actionlint to read" in r.stdout.splitlines()
    assert not any(a.startswith("rhysd/") for c in rig.calls() for a in c)


SHELLCHECK_PIN = re.compile(r"koalaman/shellcheck:v[0-9.]+@sha256:[0-9a-f]{64}")


def test_ci_sh_runs_shellcheck_offline_over_the_maintainer_scripts_before_any_build_and_a_finding_fails(rig, ci):
    scripts = rig.tmp / "repo" / "tools" / "maintainer"
    scripts.mkdir(parents=True)
    for name in ("a.sh", "b.sh"):
        (scripts / name).write_text("#!/usr/bin/env bash\ntrue\n")
    (scripts / "README.md").write_text("not a script\n")
    r = ci()
    assert r.returncode == 0, r.stderr
    assert "scripts: shellcheck finds nothing in 2 script(s)" in r.stdout.splitlines()
    calls = rig.calls()
    at = next(i for i, c in enumerate(calls) if any(SHELLCHECK_PIN.fullmatch(a) for a in c))
    check = calls[at]
    image = next(a for a in check if SHELLCHECK_PIN.fullmatch(a))
    assert check[:2] == ["run", "--rm"] and check[check.index("--network") + 1] == "none"
    assert check[check.index("--label") + 1] == "family-financials.test=1"
    assert check[check.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert f"{scripts}:/scripts:ro" in check
    assert check[check.index(image) + 1:] == ["/scripts/a.sh", "/scripts/b.sh"]
    assert calls[at + 1][:1] == ["build"]
    rig.log.unlink()
    r = ci(STUB_SHELLCHECK_RC="1")
    assert r.returncode == 1 and "shellcheck found a problem in a maintainer script" in r.stderr
    assert "In /scripts/a.sh line 2: a sample finding" in r.stdout
    assert not [c for c in rig.calls() if c[:1] == ["build"]]


def test_ci_sh_says_so_when_there_is_no_maintainer_script(rig, ci):
    r = ci()
    assert r.returncode == 0 and "scripts: no maintainer script for shellcheck to read" in r.stdout.splitlines()
    assert not any(a.startswith("koalaman/") for c in rig.calls() for a in c)


def test_ci_sh_builds_with_the_harness_label_and_runs_the_printed_image_ids(rig, ci):
    r = ci(rig.exports, FF_SUITE_LOG=rig.suite_log)
    assert r.returncode == 0, r.stderr
    builds = [c for c in rig.calls() if c[:1] == ["build"]]
    assert len(builds) == 4
    for call in builds:
        assert "-q" in call and "-t" in call
        assert call[call.index("--label") + 1] == "family-financials.test=1"
    runs = rig.runs()
    assert runs
    for call in runs:
        assert call[call.index("--label") + 1] == "family-financials.test=1", call
        assert any(i in call for i in (ETL_ID, WEB_ID, ETL_RT_ID, WEB_RT_ID)), call
        assert not any(a.startswith(("family-financials-etl:", "family-financials-web:")) for a in call), call


def test_ci_sh_builds_each_images_target_with_no_uid_baked_in(rig, ci):
    """The tests run in the ETL image's test target and the web image's web-build stage (the agent tags); the runtime
    images are its default target and the web server (the rt tags). No build carries a uid: each container gets its
    own from `docker run --user`."""
    r = ci()
    assert r.returncode == 0, r.stderr
    builds = [c for c in rig.calls() if c[:1] == ["build"]]
    assert [(c[c.index("--target") + 1], c[c.index("-f") + 1], c[c.index("-t") + 1]) for c in builds] == [
        ("test", "deploy/Dockerfile.etl", "family-financials-etl:test"),
        ("web-build", "deploy/Dockerfile.web", "family-financials-web:test"),
        ("etl", "deploy/Dockerfile.etl", "family-financials-etl:rt"),
        ("web", "deploy/Dockerfile.web", "family-financials-web:rt")]
    assert not any("--build-arg" in c for c in builds)


def test_ci_sh_refuses_a_tree_whose_git_tracks_a_file_under_config(rig, ci):
    """A household's config is never committed. A developer's own config/, untracked and ignored, is allowed: the check
    asks git what it tracks, never looks at the directory, and runs on the host before any image is built."""
    (rig.tmp / "repo" / "config").mkdir()
    (rig.tmp / "repo" / "config" / "accounts.yaml").write_text("accounts: {}\n")
    r = ci()
    assert r.returncode == 0, r.stderr
    assert "config: git tracks no file under config/" in r.stdout
    rig.log.unlink()
    r = ci(STUB_TRACKED=r"sample-a.yaml\nsample-b.yaml\n")             # two tracked paths; only their count is printed
    assert r.returncode == 1
    assert "git tracks 2 file(s) under config/" in r.stderr
    assert "CI OK" not in r.stdout and rig.calls() == []
    r = ci(STUB_LS_FILES_RC="128")
    assert r.returncode == 1 and "git ls-files" in r.stderr and rig.calls() == []


def test_ci_sh_in_a_tree_that_is_no_git_checkout_needs_no_config_there_and_no_git(rig, ci):
    """An exported tree (git archive | tar -x) has no ignored files: everything in it ships, so it must hold no config
    at all. git's own error never reaches the output, and the build line says what it could not name."""
    r = ci(STUB_NOT_A_CHECKOUT="1")
    assert r.returncode == 0, r.stderr
    assert "config: not a git checkout; the tree holds no config/" in r.stdout.splitlines()
    assert "fatal" not in r.stdout + r.stderr
    assert "(not a git checkout)" in next(line for line in r.stdout.splitlines() if "build family-financials-etl:test" in line)
    assert r.stdout.rstrip().splitlines()[-1].endswith("CI OK ==")


@pytest.mark.parametrize("case", ["an-empty-directory", "a-directory-with-files", "a-file", "a-link-to-a-directory",
                                  "a-dangling-link"])
def test_ci_sh_refuses_a_config_in_a_tree_that_is_no_git_checkout(rig, ci, case):
    config = rig.tmp / "repo" / "config"
    elsewhere = rig.tmp / "household-config"
    elsewhere.mkdir()
    if case == "an-empty-directory":
        config.mkdir()
    elif case == "a-directory-with-files":
        config.mkdir()
        (config / "accounts.yaml").write_text("accounts: {}\n")
    elif case == "a-file":
        config.write_text("")
    elif case == "a-link-to-a-directory":
        config.symlink_to(elsewhere)
    else:
        config.symlink_to(rig.tmp / "no-such-dir")
    r = ci(STUB_NOT_A_CHECKOUT="1")
    assert r.returncode == 1
    assert "is not a git checkout and holds config" in r.stderr
    assert "CI OK" not in r.stdout and rig.calls() == []


def test_ci_sh_treats_a_tree_inside_another_work_tree_as_an_export(rig, ci):
    """An exported tree unpacked inside another repository's work tree: git answers for that repository, whose top is
    not this tree, so the tree is no checkout and must hold no config at all."""
    outer = str(rig.tmp)                                            # the repository the exported tree sits in
    r = ci(STUB_TOPLEVEL=outer)
    assert r.returncode == 0, r.stderr
    assert "config: not a git checkout; the tree holds no config/" in r.stdout.splitlines()
    assert "(not a git checkout)" in next(line for line in r.stdout.splitlines() if "build family-financials-etl:test" in line)
    assert r.stdout.rstrip().splitlines()[-1].endswith("CI OK ==")
    rig.log.unlink()
    (rig.tmp / "repo" / "config").mkdir()
    (rig.tmp / "repo" / "config" / "accounts.yaml").write_text("accounts: {}\n")
    r = ci(STUB_TOPLEVEL=outer)
    assert r.returncode == 1
    assert "is not a git checkout and holds config" in r.stderr
    assert "CI OK" not in r.stdout and rig.calls() == []


def test_ci_sh_compares_the_work_tree_top_with_the_tree_as_resolved_paths(rig, ci):
    """A top git names through a link to the tree is the tree: it is a checkout, and its untracked config/ is allowed."""
    link = rig.tmp / "link-to-repo"
    link.symlink_to(rig.tmp / "repo")
    (rig.tmp / "repo" / "config").mkdir()
    r = ci(STUB_TOPLEVEL=str(link))
    assert r.returncode == 0, r.stderr
    assert "config: git tracks no file under config/" in r.stdout.splitlines()


def test_ci_sh_skips_the_private_tooling_tests_with_a_line_when_the_directory_is_absent(rig, ci):
    """An exported tree leaves tools/private out: the step says it skips and the gate goes on to CI OK."""
    r = ci()
    assert r.returncode == 0, r.stderr
    assert "== tools/private tests: skipped, this tree has no tools/private ==" in r.stdout.splitlines()
    assert not any("tools/private" in a for c in rig.runs() for a in c)
    assert r.stdout.rstrip().splitlines()[-1].endswith("CI OK ==")


@pytest.mark.parametrize("files", [("helper.py",), ("helper.py", "notes.txt"), ("tests_old.py",)])
def test_ci_sh_fails_when_tools_private_holds_python_but_no_test_file(rig, ci, files):
    """Tests that were renamed or moved never vanish from the gate unnoticed."""
    private = rig.tmp / "repo" / "tools" / "private"
    private.mkdir()
    for name in files:
        (private / name).write_text("")
    r = ci()
    assert r.returncode == 1
    (line,) = [x for x in r.stderr.splitlines() if "tools/private" in x]
    assert "no test_*.py" in line
    assert "CI OK" not in r.stdout
    assert not any("tools/private" in a for c in rig.runs() for a in c)


@pytest.mark.parametrize("files", [(), ("notes.txt",)])
def test_ci_sh_fails_when_tools_private_exists_with_no_python_file(rig, ci, files):
    private = rig.tmp / "repo" / "tools" / "private"
    private.mkdir()
    for name in files:
        (private / name).write_text("")
    r = ci()
    assert r.returncode == 1
    (line,) = [x for x in r.stderr.splitlines() if "tools/private" in x]
    assert "no Python file" in line
    assert "CI OK" not in r.stdout
    assert not any("tools/private" in a for c in rig.runs() for a in c)


def test_ci_sh_runs_the_private_tooling_tests_when_the_directory_holds_one(rig, ci):
    private = rig.tmp / "repo" / "tools" / "private"
    private.mkdir()
    (private / "helper.py").write_text("")
    (private / "test_sample.py").write_text("")
    r = ci()
    assert r.returncode == 0, r.stderr
    assert "== tools/private: ruff + tests ==" in r.stdout.splitlines() and "tools/private tests: skipped" not in r.stdout
    (call,) = [c for c in rig.runs() if any("tools/private" in a for a in c)]
    assert f"{private}:/app/finance-app/tools/private:ro" in call and ETL_ID in call
    # the ETL run's ruff never sees tools/private (it is not in the image): this step lints it first, same rules
    assert call[-1] == "uv run ruff check tools/private && uv run pytest -q -p no:cacheprovider tools/private"
    # the harness's and the private tooling's summaries are printed, and a skip in either is theirs to have
    assert r.stdout.splitlines().count("4 passed, 1 skipped in 0.12s") == 2


def test_ci_sh_fails_when_ruff_finds_something_in_tools_private(rig, ci):
    private = rig.tmp / "repo" / "tools" / "private"
    private.mkdir()
    (private / "helper.py").write_text("")
    (private / "test_sample.py").write_text("")
    r = ci(STUB_PRIVATE_RUFF_RC="1")
    assert r.returncode == 1
    assert "tools/private/helper.py:1:1: F401 `os` imported but unused" in r.stdout.splitlines()
    assert "tools/private: ruff check or its tests failed (output above)" in r.stderr.splitlines()
    assert "4 passed, 1 skipped in 0.12s" in r.stdout                 # the harness's own run, before this step
    assert r.stdout.count("4 passed, 1 skipped in 0.12s") == 1         # the private tests did not run
    assert "CI OK" not in r.stdout and not any(line.startswith("gate:") for line in r.stdout.splitlines())
    assert not any("vitest" in c[-1] or "tools.demo.generate" in " ".join(c) for c in rig.runs())


def test_ci_sh_asks_the_etl_image_where_it_looks_for_a_config_right_after_building_it(rig, ci):
    r = ci()
    assert r.returncode == 0, r.stderr
    assert "image: no config where the ETL looks for one" in r.stdout
    calls = rig.calls()
    check = calls[1]
    assert calls[0][:1] == ["build"] and check[:1] == ["run"] and "config_dir" in check[-1]
    assert ETL_ID in check and check[check.index("--label") + 1] == NEUTRAL.label
    assert check[check.index("--network") + 1] == "none"
    assert "ruff" in calls[2][-1]                                      # the unit gates follow the check


def test_ci_sh_runs_every_container_without_a_network(rig, ci):
    """The suites need no network, and the gate shows it: every container ci.sh starts (the image check, the ETL
    suite, the harness and private-tooling tests, the demo households, eslint, vitest, tsc, each profile's contract and
    matrix, the two zone runs, the demo site's build, the runtime images' thirteen checks) runs with --network none, with exports given or
    not; only the image builds reach the network."""
    private = rig.tmp / "repo" / "tools" / "private"
    private.mkdir()
    (private / "test_sample.py").write_text("")
    branches = (((), {}, 22 + RT_CHECKS), ((rig.exports,), {"FF_SUITE_LOG": rig.suite_log}, 6 + RT_CHECKS),
                ((rig.outside,), {"FF_DEMO_EXPORTS": "1"}, 5 + RT_CHECKS))
    for args, env, containers in branches:
        r = ci(*args, **env)
        assert r.returncode == 0, r.stderr
        runs = rig.runs()
        assert len(runs) == containers, [c[-1] for c in runs]
        for call in runs:
            assert "--network" in call and call[call.index("--network") + 1] == "none", call
        rig.log.unlink()


@pytest.mark.parametrize("case", ["a-config-in-the-image", "the-check-fails"])
def test_ci_sh_fails_when_the_etl_image_holds_a_config_or_cannot_be_asked(rig, ci, case):
    env, words = {"a-config-in-the-image": ({"STUB_IMAGE_CONFIG": "/app/finance-app/config"},
                                            "the ETL image holds a config at /app/finance-app/config"),
                  "the-check-fails": ({"STUB_IMAGE_CHECK_RC": "1"}, "could not ask the ETL image")}[case]
    r = ci(**env)
    assert r.returncode == 1
    assert words in r.stderr and "CI OK" not in r.stdout
    builds = [c for c in rig.calls() if c[:1] == ["build"]]
    (check,) = rig.runs()                                              # nothing runs after the check
    assert len(builds) == 1 and "config_dir" in check[-1]


def test_ci_sh_stops_when_a_build_fails_even_after_printing_an_id(rig, ci):
    r = ci(STUB_BUILD_RC="1")
    assert r.returncode != 0
    assert "CI OK" not in r.stdout
    assert [c[0] for c in rig.calls()] == ["build"]


def test_ci_sh_real_exports_print_counts_only_and_log_privately(rig, ci):
    log = rig.suite_log
    r = ci(rig.exports, FF_SUITE_LOG=log)
    assert r.returncode == 0, r.stderr
    assert "tests: 1 passed / 0 failed / 0 skipped / 1 total" in r.stdout
    assert LEAK not in r.stdout + r.stderr
    assert LEAK in log.read_text()
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    vitest = [c for c in rig.runs() if "vitest" in c[-1]]
    assert len(vitest) == 1
    assert f"{rig.exports}:/app/finance-app/data/exports:ro" in vitest[0]
    # the contract, agg and matrix tests only, required, as the household; never the smoke suite
    assert REDUCED in vitest[0][-1] and "smoke" not in vitest[0][-1] and "npm run test" not in vitest[0][-1]
    for env in ("FF_REQUIRE_EXPORTS=1", "FF_PROFILE=household"):
        assert vitest[0][vitest[0].index(env) - 1] == "-e"
    scripts = [c[-1] for c in rig.runs()]
    assert "cd app && npm run lint && npm run typecheck" in scripts


def test_ci_sh_real_exports_refuse_a_demo_profile(rig, ci):
    r = ci(rig.exports, FF_SUITE_LOG=rig.suite_log, FF_PROFILE="tiny")
    assert r.returncode == 2
    assert "FF_PROFILE" in r.stderr
    assert rig.calls() == []


def test_ci_sh_requires_a_suite_log_for_real_exports(rig, ci):
    r = ci(rig.exports)
    assert r.returncode == 2
    assert "FF_SUITE_LOG" in r.stderr
    assert rig.calls() == []


def test_ci_sh_refuses_a_relative_suite_log_before_building(rig, ci):
    cwd = rig.tmp / "cwd"
    cwd.mkdir()
    r = rig.run(["bash", str(rig.tmp / "repo" / "tools" / "verify" / "ci.sh"), str(rig.exports)],
                {"FF_REAL_EXPORTS": "1", "FF_SUITE_LOG": "suite.log"}, cwd=cwd)
    assert r.returncode == 2
    assert "FF_SUITE_LOG must be an absolute path" in r.stderr
    assert rig.calls() == []
    assert list(cwd.iterdir()) == []


@pytest.mark.parametrize("case", ["inside-the-repository", "beside-the-scratch-root", "the-root-itself",
                                  "a-symlink-leading-out"])
def test_ci_sh_refuses_a_suite_log_not_strictly_inside_the_scratch_root(rig, ci, case):
    link = rig.scratch / "head" / "suite.log"
    link.symlink_to(rig.tmp / "out.log")
    log = {"inside-the-repository": rig.tmp / "repo" / "suite.log", "beside-the-scratch-root": rig.tmp / "suite.log",
           "the-root-itself": rig.scratch, "a-symlink-leading-out": link}[case]
    r = ci(rig.exports, FF_SUITE_LOG=log)
    assert r.returncode == 2
    assert "FF_SUITE_LOG" in r.stderr
    assert rig.calls() == []
    assert not (rig.tmp / "repo" / "suite.log").exists() and not (rig.tmp / "out.log").exists()


def test_ci_sh_refuses_a_suite_log_whose_directory_is_missing(rig, ci):
    r = ci(rig.exports, FF_SUITE_LOG=rig.scratch / "missing" / "suite.log")
    assert r.returncode == 2
    assert "FF_SUITE_LOG" in r.stderr
    assert rig.calls() == []


def test_ci_sh_demo_exports_lift_the_suite_log_rule(rig, ci):
    r = ci(rig.outside, FF_DEMO_EXPORTS="1", FF_SUITE_LOG=rig.tmp / "repo" / "suite.log")
    assert r.returncode == 0, r.stderr


def _outside_cases(rig: Rig) -> dict[str, Path]:
    (rig.scratch / "manifest.json").write_text("{}")
    link = rig.scratch / "link"
    link.symlink_to(rig.outside)
    return {
        "outside": rig.outside,
        "the-root-itself": rig.scratch,
        "a-symlink-leading-out": link,
        "dot-dot-out": rig.scratch / "head" / ".." / ".." / "elsewhere" / "exports",
    }


@pytest.mark.parametrize("case", ["outside", "the-root-itself", "a-symlink-leading-out", "dot-dot-out"])
def test_ci_sh_refuses_exports_not_strictly_inside_the_scratch_root(rig, ci, case):
    r = ci(_outside_cases(rig)[case], FF_SUITE_LOG=rig.suite_log)
    assert r.returncode == 2
    assert "scratch root" in r.stderr
    assert rig.calls() == []


def _unsafe_roots(rig: Rig) -> dict[str, str]:
    """FF_SCRATCH values that are never a scratch root; each case's exports and log lie inside the value, so only the
    sanity check can refuse it."""
    rig.exports_at(rig.passwd_home / "sample" / "data" / "exports")
    return {"relative": "scratch", "filesystem-root": "/", "the-home": str(rig.passwd_home),
            "above-the-home": str(rig.passwd_home.parent)}


@pytest.mark.parametrize("case", ["relative", "filesystem-root", "the-home", "above-the-home"])
def test_ci_sh_refuses_an_unsafe_scratch_root(rig, ci, case):
    value = _unsafe_roots(rig)[case]
    exports = rig.exports if case in ("relative", "filesystem-root") else rig.passwd_home / "sample" / "data" / "exports"
    log = exports.parent / "suite.log"
    r = ci(exports, FF_SCRATCH=value, STUB_HOME=rig.passwd_home, FF_SUITE_LOG=log)
    assert r.returncode == 2
    assert "FF_SCRATCH" in r.stderr
    assert rig.calls() == []


def test_ci_sh_ff_real_exports_still_means_real(rig, ci):
    r = ci(rig.outside, FF_REAL_EXPORTS="1", FF_SUITE_LOG=rig.suite_log)
    assert r.returncode == 2 and "scratch root" in r.stderr
    r = ci(FF_REAL_EXPORTS="1", FF_SUITE_LOG=rig.suite_log)
    assert r.returncode == 2 and "needs EXPORTS_DIR" in r.stderr
    assert rig.calls() == []


def test_ci_sh_demo_exports_lift_both_rules(rig, ci):
    r = ci(rig.outside, FF_DEMO_EXPORTS="1")
    assert r.returncode == 0, r.stderr
    assert "CI OK" in r.stdout
    tests = [c for c in rig.runs() if "vitest run" in c[-1]]
    assert len(tests) == 1
    assert f"{rig.outside}:/app/finance-app/data/exports:ro" in tests[0]
    # lint, the contract, agg and matrix tests, tsc; printed as usual; the household unless FF_PROFILE names a demo one
    assert tests[0][-1] == f"cd app && npm run lint && npx vitest run {REDUCED} && npm run typecheck"
    assert "FF_PROFILE=household" in tests[0] and "FF_REQUIRE_EXPORTS=1" in tests[0]
    assert LEAK in r.stdout
    rig.log.unlink()
    r = ci(rig.outside, FF_DEMO_EXPORTS="1", FF_PROFILE="tiny")
    assert r.returncode == 0, r.stderr
    (call,) = [c for c in rig.runs() if "vitest run" in c[-1]]
    assert call[call.index("FF_PROFILE=tiny") - 1] == "-e"
    rig.log.unlink()
    r = ci(rig.outside, FF_DEMO_EXPORTS="1", FF_PROFILE="ti ny")
    assert r.returncode == 2 and "FF_PROFILE must be a plain name" in r.stderr
    assert rig.calls() == []


def _demo_in_a_real_root(rig: Rig, names: harness_names.Harness) -> dict[str, tuple[Path, dict]]:
    """Exports marked FF_DEMO_EXPORTS=1 that lie at or inside a real-data scratch root - the flag never overrides the
    location - as (the exports, extra env); the default root is `names`' scratch directory in the passwd home."""
    (rig.scratch / "manifest.json").write_text("{}")
    default = rig.exports_at(rig.passwd_home / names.scratch / "demo" / "data" / "exports")
    link = rig.tmp / "link-into-the-root"
    link.symlink_to(rig.exports)
    # spelt inside the root, resolving outside it: the path as given names the root
    placed = rig.scratch / "linked"
    placed.symlink_to(rig.outside)
    return {
        "inside-ff-scratch": (rig.exports, {}),
        "ff-scratch-itself": (rig.scratch, {}),
        "a-symlink-into-ff-scratch": (link, {}),
        "a-symlink-placed-inside-ff-scratch": (placed, {}),
        "inside-the-default-root-with-ff-scratch-elsewhere": (default, {"STUB_HOME": rig.passwd_home}),
    }


DEMO_IN_A_REAL_ROOT = ["inside-ff-scratch", "ff-scratch-itself", "a-symlink-into-ff-scratch",
                       "a-symlink-placed-inside-ff-scratch", "inside-the-default-root-with-ff-scratch-elsewhere"]


@pytest.mark.parametrize("case", DEMO_IN_A_REAL_ROOT)
def test_ci_sh_refuses_demo_exports_in_a_real_data_root(rig, ci, case):
    exports, env = _demo_in_a_real_root(rig, NEUTRAL)[case]
    r = ci(exports, FF_DEMO_EXPORTS="1", **env)
    assert r.returncode == 2
    assert "real-data scratch root" in r.stderr
    assert rig.calls() == []


@pytest.mark.parametrize("case", ["above-ff-scratch", "above-the-default-root-with-ff-scratch-elsewhere"])
def test_ci_sh_refuses_demo_exports_that_hold_a_real_data_root(rig, ci, case):
    """A directory above a root, a manifest at its top, would mount the root with it."""
    rig.passwd_home.mkdir()
    exports, env = {"above-ff-scratch": (rig.tmp, {}),
                    "above-the-default-root-with-ff-scratch-elsewhere": (rig.passwd_home,
                                                                          {"STUB_HOME": rig.passwd_home})}[case]
    (exports / "manifest.json").write_text("{}")
    r = ci(exports, FF_DEMO_EXPORTS="1", **env)
    assert r.returncode == 2
    assert "holds the real-data scratch root" in r.stderr
    assert rig.calls() == []


def test_ci_sh_refuses_real_and_demo_together(rig, ci):
    r = ci(rig.exports, FF_REAL_EXPORTS="1", FF_DEMO_EXPORTS="1", FF_SUITE_LOG=rig.suite_log)
    assert r.returncode == 2
    assert rig.calls() == []


def test_ci_sh_takes_the_scratch_root_from_passwd_not_home(rig, ci):
    real = rig.exports_at(rig.passwd_home / "ff-verify" / "head" / "data" / "exports")
    forged = rig.exports_at(rig.tmp / "forged" / "ff-verify" / "head" / "data" / "exports")
    env = {"FF_SCRATCH": None, "HOME": rig.tmp / "forged", "STUB_HOME": rig.passwd_home,
           "FF_SUITE_LOG": rig.passwd_home / "ff-verify" / "head" / "suite.log"}
    r = ci(forged, **env)
    assert r.returncode == 2 and "scratch root" in r.stderr
    assert rig.calls() == []
    r = ci(real, **env)
    assert r.returncode == 0, r.stderr


# a private settings file (invented values) and the private tooling the gate then lints and tests
SETTINGS = ("# a sample team's names\nFF_HARNESS_IMAGE_PREFIX=sample\nFF_HARNESS_TEST_TAG=agent\n"
            "FF_HARNESS_LABEL=team=sample\nFF_HARNESS_PROJECT_PREFIX='sample'\nFF_HARNESS_SCRATCH=\"sample-verify\"\n"
            "FF_HARNESS_COMPOSE=tools/team/compose.sh\nFF_HARNESS_COMPOSE_PORT_VAR=TEAM_PORT\n")


def _with_settings(rig: Rig, text: str = SETTINGS) -> None:
    private = rig.tmp / "repo" / "tools" / "private"
    private.mkdir()
    (private / "harness.env").write_text(text)
    (private / "helper.py").write_text("")
    (private / "test_helper.py").write_text("")


def test_ci_sh_takes_its_names_from_the_private_settings_when_the_tree_holds_them(rig, ci):
    """With tools/private/harness.env, every build is tagged and labelled, and every container labelled, by its names
    (the environment's are overruled); without it, by the defaults (the tests above)."""
    _with_settings(rig)
    r = ci(FF_HARNESS_LABEL="other=1", FF_HARNESS_IMAGE_PREFIX="other")
    assert r.returncode == 0, r.stderr
    builds = [c for c in rig.calls() if c[:1] == ["build"]]
    assert [c[c.index("-t") + 1] for c in builds] == ["sample-etl:agent", "sample-web:agent", "sample-etl:rt",
                                                      "sample-web:rt"]
    for call in builds + rig.runs():
        assert call[call.index("--label") + 1] == "team=sample", call
    assert "images: sample-etl:rt 209 MB, sample-web:rt 209 MB" in r.stdout.splitlines()


def test_ci_sh_refuses_settings_of_another_shape_before_anything_runs(rig, ci):
    _with_settings(rig, SETTINGS.replace("FF_HARNESS_LABEL=team=sample", "FF_HARNESS_LABEL=no-value"))
    r = ci()
    assert r.returncode == 2
    assert "FF_HARNESS_LABEL is 'no-value'" in r.stderr
    assert rig.calls() == []


def test_ci_sh_refuses_settings_that_leave_the_label_or_the_scratch_root_out_before_anything_runs(rig, ci):
    """An emptied or lost line never falls back to the neutral label or scratch root, whatever the environment says."""
    for text in (SETTINGS.replace("FF_HARNESS_LABEL=team=sample", "FF_HARNESS_LABEL="),
                 SETTINGS.replace('FF_HARNESS_SCRATCH="sample-verify"\n', "")):
        shutil.rmtree(rig.tmp / "repo" / "tools" / "private", ignore_errors=True)
        _with_settings(rig, text)
        r = ci(FF_HARNESS_LABEL="other=1", FF_HARNESS_SCRATCH="other-verify")
        assert r.returncode == 2 and "tools/private/harness.env sets no FF_HARNESS_" in r.stderr, r.stderr
        assert rig.calls() == []


def test_the_real_data_root_follows_the_scratch_setting(rig, ci):
    """The default scratch root is the setting's directory in the passwd home: real exports must lie inside it, and
    exports there are never synthetic; the default's directory is then an ordinary place."""
    _with_settings(rig)
    env = {"FF_SCRATCH": None, "STUB_HOME": rig.passwd_home}
    inside = rig.exports_at(rig.passwd_home / "sample-verify" / "head" / "data" / "exports")
    r = ci(inside, FF_SUITE_LOG=rig.passwd_home / "sample-verify" / "head" / "suite.log", **env)
    assert r.returncode == 0, r.stderr
    rig.log.unlink()
    r = ci(inside, FF_DEMO_EXPORTS="1", **env)
    assert r.returncode == 2 and "real-data scratch root" in r.stderr and rig.calls() == []
    elsewhere = rig.exports_at(rig.passwd_home / "ff-verify" / "demo" / "data" / "exports")
    r = ci(elsewhere, FF_SUITE_LOG=rig.passwd_home / "ff-verify" / "suite.log", **env)
    assert r.returncode == 2 and "strictly inside the scratch root" in r.stderr and rig.calls() == []
    r = ci(elsewhere, FF_DEMO_EXPORTS="1", **env)
    assert r.returncode == 0, r.stderr


def test_ci_runs_the_contract_and_the_matrix_on_every_profile_with_exports_required(rig, ci):
    r = ci()
    assert r.returncode == 0, r.stderr
    contract = [c for c in rig.runs() if "contract.test.ts" in c[-1]]
    assert len(contract) == len(PROFILES)
    lines = r.stdout.splitlines()
    for call, profile in zip(contract, PROFILES, strict=True):
        assert call[0:2] == ["run", "--rm"] and NEUTRAL.label in call
        assert WEB_ID in call
        mount = [a for a in call if a.endswith(":/exports:ro")]
        assert len(mount) == 1 and mount[0].endswith(f"/{profile}/data/exports:/exports:ro")
        for env in ("FF_EXPORTS_DIR=/exports", "FF_REQUIRE_EXPORTS=1", f"FF_PROFILE={profile}"):
            assert call[call.index(env) - 1] == "-e"
        # one container: the contract and agg tests, then (only when they pass) the matrix with its JSON report
        assert call[-1] == ("cd app && npx vitest run src/data/contract.test.ts src/lib/agg.test.ts && touch /out/contract.ok"
                            " && npx vitest run src/matrix.test.tsx --reporter=default --reporter=json --outputFile=/out/matrix.json")
        assert lines.index(f"contract {profile}: OK") < lines.index(f"matrix {profile}: 2 passed (1 known gaps) / 0 failed")
    # each profile's report lands in a directory of its own inside the run's private temp dir
    outs = [next(a.split(":")[0] for a in c if a.endswith(":/out")) for c in contract]
    assert len(set(outs)) == len(PROFILES) and all(o.endswith(f"/matrix-{p}") for o, p in zip(outs, PROFILES, strict=True))
    assert r.stdout.rstrip().splitlines()[-1].endswith("CI OK ==")
    # the demo is generated in the ETL image id, and with exports given none is generated at all
    assert all(ETL_ID in c for c in rig.runs() if any("tools.demo.generate" in a for a in c))
    rig.log.unlink()
    r = ci(rig.exports, FF_SUITE_LOG=rig.suite_log)
    assert r.returncode == 0, r.stderr
    assert not any("tools.demo.generate" in a or "contract.ok" in a or "FF_PROFILE=full" in a for c in rig.calls() for a in c)


def test_ci_names_the_profile_whose_contract_fails(rig, ci):
    r = ci(STUB_CONTRACT_FAIL="tiny")
    assert r.returncode == 1
    assert "contract tiny" in r.stderr and "CI OK" not in r.stdout
    assert "contract empty: OK" not in r.stdout and "matrix tiny" not in r.stdout


@pytest.mark.parametrize("case", ["failed", "skipped", "no-report", "error-outside-any-test"])
def test_ci_names_the_profile_whose_matrix_fails_or_skips(rig, ci, case):
    env = {"failed": {"STUB_MATRIX_FAIL": "tiny"}, "skipped": {"STUB_MATRIX_SKIP": "tiny"},
           "no-report": {"STUB_MATRIX_NO_REPORT": "tiny"}, "error-outside-any-test": {"STUB_MATRIX_RC": "tiny"}}[case]
    r = ci(**env)
    assert r.returncode == 1
    assert "CI OK" not in r.stdout
    assert "contract tiny: OK" in r.stdout and "contract empty: OK" not in r.stdout
    assert "matrix tiny" in r.stderr
    if case in ("failed", "skipped"):
        label = "FAILED" if case == "failed" else "SKIPPED"
        assert f"{label}: /accounts — renders" in r.stdout.splitlines()


# the matrix runs once more on two households, each in a zone of its own (the six runs above are in UTC)
ZONE_RUNS = [("full", "America/New_York"), ("tiny", "Pacific/Kiritimati")]


def _zone_runs(rig: Rig) -> list[list[str]]:
    return [c for c in rig.runs() if any(a.startswith("FF_TEST_TZ=") for a in c)]


def test_ci_runs_the_matrix_again_on_full_and_tiny_each_in_another_zone(rig, ci):
    r = ci()
    assert r.returncode == 0, r.stderr
    demo = _demo_dir(rig)
    calls = _zone_runs(rig)
    assert len(calls) == len(ZONE_RUNS)
    lines = r.stdout.splitlines()
    last_profile = lines.index("matrix no-income: 2 passed (1 known gaps) / 0 failed")
    for call, (profile, zone) in zip(calls, ZONE_RUNS, strict=True):
        assert call[0:2] == ["run", "--rm"] and call[call.index("--label") + 1] == NEUTRAL.label
        assert call[call.index("--network") + 1] == "none" and WEB_ID in call
        # the per-profile runs' mounts and variables, plus the zone
        assert f"{demo}/{profile}/data/exports:/exports:ro" in call
        for env in ("FF_EXPORTS_DIR=/exports", "FF_REQUIRE_EXPORTS=1", f"FF_PROFILE={profile}", f"FF_TEST_TZ={zone}"):
            assert call[call.index(env) - 1] == "-e"
        # the matrix, with the test that fails unless the process runs in the zone asked for (so a run whose zone did
        # not take fails instead of passing in UTC), the clock's tests (the household's day, whatever the process's
        # zone) and the clock times' (fmtDateTime, the ages, the schedule sentence), its report in a directory of its own
        assert call[-1] == ("cd app && npx vitest run src/matrix.test.tsx src/test/demoClock.test.ts src/lib/clock.test.ts"
                            " src/lib/clockTimes.test.ts --reporter=default --reporter=json --outputFile=/out/matrix.json")
        line = f"matrix {profile} ({zone}): 2 passed (1 known gaps) / 0 failed"
        assert last_profile < lines.index(line) < lines.index(GATE)
    # each vitest run's report in a directory of its own (the demo site's build, with its staged public directory, is
    # no vitest run)
    outs = [next(a.split(":")[0] for a in c if a.endswith(":/out")) for c in rig.runs()
            if WEB_ID in c and any(x.endswith(":/out") for x in c) and not any(x.endswith(":/public:ro") for x in c)]
    assert len(outs) == 1 + len(PROFILES) + len(ZONE_RUNS) == len(set(outs))
    # every other container runs in the image's own zone (vite.config.ts: UTC when FF_TEST_TZ is unset)
    assert not any(a.startswith("FF_TEST_TZ") for c in rig.runs() if c not in calls for a in c)
    # with exports given there is no zone run
    rig.log.unlink()
    assert ci(rig.exports, FF_SUITE_LOG=rig.suite_log).returncode == 0
    assert _zone_runs(rig) == []


@pytest.mark.parametrize("profile,zone", ZONE_RUNS)
@pytest.mark.parametrize("case", ["failed", "no-report", "error-outside-any-test"])
def test_ci_names_the_profile_and_zone_whose_zone_run_fails(rig, ci, profile, zone, case):
    env = {"failed": {"STUB_ZONE_FAIL": zone}, "no-report": {"STUB_ZONE_NO_REPORT": zone},
           "error-outside-any-test": {"STUB_ZONE_RC": zone}}[case]
    r = ci(**env)
    assert r.returncode == 1
    assert "CI OK" not in r.stdout and not any(line.startswith("gate:") for line in r.stdout.splitlines())
    assert f"matrix {profile} ({zone})" in r.stderr
    if case == "failed":
        assert "FAILED: /cash-flow — renders" in r.stdout.splitlines()
    # a failure in the first zone run stops the gate before the second
    assert len(_zone_runs(rig)) == ZONE_RUNS.index((profile, zone)) + 1


def test_ci_builds_the_demo_site_from_full_in_the_web_test_image_and_checks_what_it_ships(rig, ci):
    r = ci()
    assert r.returncode == 0, r.stderr
    [build] = [c for c in rig.runs() if any(a.endswith(":/public:ro") for a in c)]
    assert WEB_ID in build and build[build.index("--network") + 1] == "none"
    for env in ("VITE_DEMO=1", "FF_DEMO_PUBLIC=/public"):
        assert build[build.index(env) - 1] == "-e"
    public = Path(next(a for a in build if a.endswith(":/public:ro")).split(":")[0])
    assert public.name == "public" and public.parent.name == "demo-site", "inside the run's own temp dir"
    lines = r.stdout.splitlines()
    checked = next(i for i, line in enumerate(lines) if line.startswith("demo site: ") and " files; " in line)
    assert lines.index("matrix tiny (Pacific/Kiritimati): 2 passed (1 known gaps) / 0 failed") < checked
    assert checked < lines.index(GATE)
    # the staged exports are full's, as the demo household generated them
    assert any(line.startswith("demo site: staged ") for line in lines)
    # a site that names a file from the root fails the gate, with the check's line
    rig.log.unlink()
    r = ci(STUB_DEMO_SITE_BAD="1")
    assert r.returncode == 1
    assert "index.html: <link href='/icons/icon.svg'> is not relative to the page" in r.stderr
    assert "the demo site FAILED" in r.stderr and GATE not in r.stdout
    # and so does a build that fails
    rig.log.unlink()
    r = ci(STUB_DEMO_BUILD_RC="1")
    assert r.returncode == 1 and "demo site: the build failed" in r.stderr


DEMO_SITE = HERE / "demo_site.sh"


def _demo_site(rig: Rig, exports: Path | str, cwd: Path | None = None, **env) -> subprocess.CompletedProcess:
    return rig.run(["bash", str(DEMO_SITE), str(rig.tmp / "site-out"), "--exports", str(exports)], env, cwd=cwd)


def test_demo_site_refuses_exports_at_inside_or_above_a_real_data_root_before_anything_runs(rig):
    """The site is published, so the exports it is built from must be invented: a path at, inside or above either
    real-data scratch root (FF_SCRATCH, and the default root whatever FF_SCRATCH says), as given or as resolved, is
    exit 2 before docker is asked anything and before the output directory is made."""
    link_in = rig.tmp / "link-in"
    link_in.symlink_to(rig.exports)
    outside = rig.exports_at(rig.tmp / "invented" / "data" / "exports")
    (rig.scratch / "alias").symlink_to(outside.parent.parent)       # spelt inside the root, resolved outside it
    (rig.scratch / "manifest.json").write_text("{}")
    (rig.tmp / "manifest.json").write_text("{}")
    default = rig.exports_at(rig.passwd_home / OWN.scratch / "head" / "data" / "exports")
    elsewhere = rig.outside.parent
    relative = rig.exports.relative_to(rig.tmp).as_posix()             # scratch/head/data/exports, from rig.tmp
    upward = f"../{rig.scratch.name}/" + rig.exports.relative_to(rig.scratch).as_posix()  # from rig.tmp/elsewhere
    for exports, cwd, env in ((rig.exports, None, {}), (link_in, None, {}),
                              (rig.scratch / "alias" / "data" / "exports", None, {}), (rig.scratch, None, {}),
                              (default, None, {"STUB_HOME": rig.passwd_home}), (relative, rig.tmp, {}),
                              (upward, elsewhere, {}), ("./" + relative + "/.", rig.tmp, {})):
        r = _demo_site(rig, exports, cwd, **env)
        assert r.returncode == 2 and "real-data scratch root" in r.stderr, (exports, r.stderr)
        assert r.stderr.startswith("demo_site.sh: --exports ")
        assert rig.calls() == [] and not (rig.tmp / "site-out").exists()
    # a directory above a root: the root comes with it
    r = _demo_site(rig, rig.tmp)
    assert r.returncode == 2 and "holds the real-data scratch root" in r.stderr and rig.calls() == []


def test_demo_site_refuses_family_documents_beside_the_exports_that_resolve_into_a_real_data_root(rig):
    exports = rig.exports_at(rig.tmp / "invented" / "data" / "exports")
    (rig.scratch / "head" / "state" / "family").mkdir(parents=True)
    (exports.parent.parent / "state").symlink_to(rig.scratch / "head" / "state")
    r = _demo_site(rig, exports)
    assert r.returncode == 2 and "demo_site.sh: the family documents' folder" in r.stderr
    assert "real-data scratch root" in r.stderr and rig.calls() == []


def test_demo_site_takes_invented_exports_outside_the_roots(rig):
    exports = rig.exports_at(rig.tmp / "invented" / "data" / "exports")
    r = _demo_site(rig, exports, STUB_NO_IMAGE="1")
    assert r.returncode == 2 and "missing - run tools/verify/ci.sh first" in r.stderr, r.stderr
    assert "real-data" not in r.stderr


def test_ci_removes_its_demo_directory_on_success_and_on_failure(rig, ci):
    tmpdir = rig.tmp / "tmpdir"
    tmpdir.mkdir()
    r = ci(TMPDIR=tmpdir)
    assert r.returncode == 0, r.stderr
    assert list(tmpdir.iterdir()) == []
    for failing in ({"STUB_DEMO_FAIL": "tiny"}, {"STUB_CONTRACT_FAIL": "full"}, {"STUB_MATRIX_FAIL": "new"}):
        r = ci(TMPDIR=tmpdir, **failing)
        assert r.returncode == 1
        assert list(tmpdir.iterdir()) == []


def _demo_dir(rig: Rig) -> str:
    """the directory ci.sh generated the demo households into: the host side of the demo containers' /out"""
    (call,) = {tuple(c) for c in rig.runs() if any("tools.demo.generate" in a for a in c) and c[-2] == "full"}
    return next(a.split(":")[0] for a in call if a.endswith(":/out"))


def _report_with(*results: tuple[str, str]) -> str:
    return json.dumps({"testResults": [{"name": "/w/app/src/smoke.test.tsx", "status": "passed", "assertionResults": [
        {"fullName": name, "status": status, "failureMessages": []} for name, status in results]}]})


def test_ci_without_exports_runs_the_main_vitest_on_the_full_demo_household(rig, ci):
    r = ci()
    assert r.returncode == 0, r.stderr
    demo = _demo_dir(rig)
    (main,) = [c for c in rig.runs() if "vitest run" in c[-1] and "matrix.test.tsx" not in c[-1]]
    assert main[0:2] == ["run", "--rm"] and NEUTRAL.label in main and WEB_ID in main
    assert f"{demo}/full/data/exports:/exports:ro" in main and f"{demo}/full/truth.json:/truth.json:ro" in main
    for env in ("FF_EXPORTS_DIR=/exports", "FF_TRUTH=/truth.json", "FF_REQUIRE_EXPORTS=1", "FF_PROFILE=full"):
        assert main[main.index(env) - 1] == "-e"
    assert "--reporter=json" in main[-1] and "/out/vitest.json" in main[-1]
    assert "vitest: 1 passed / 0 failed / 0 skipped" in r.stdout.splitlines()
    # lint before and tsc after, each in its own container, the image's own sources only
    scripts = [c[-1] for c in rig.runs() if WEB_ID in c]
    assert scripts.index("cd app && npm run lint") < scripts.index(main[-1]) < scripts.index("cd app && npm run typecheck")
    assert not any("npm run test" in s for s in scripts)
    # the vitest run's output is synthetic, so it reaches the terminal
    assert LEAK in r.stdout


@pytest.mark.parametrize("status", ["skipped", "todo", "failed"])
def test_ci_without_exports_fails_when_a_dashboard_test_does_not_pass(rig, ci, status):
    r = ci(STUB_VITEST=_report_with(("a route renders", "passed"), ("a smoke test", status)))
    assert r.returncode == 1
    assert "CI OK" not in r.stdout
    assert "FAILED or SKIPPED on the demo household full" in r.stderr
    label = "FAILED" if status == "failed" else "SKIPPED"
    assert f"{label}: a smoke test" in r.stdout.splitlines()
    assert not any("contract.test.ts" in c[-1] for c in rig.runs())


def test_ci_without_exports_fails_on_an_error_outside_any_test_or_no_report(rig, ci):
    r = ci(STUB_VITEST_RC="1")
    assert r.returncode == 1 and "CI OK" not in r.stdout
    assert "vitest: 1 passed / 0 failed / 0 skipped" in r.stdout
    assert "an error outside any test" in r.stderr
    r = ci(STUB_VITEST="")
    assert r.returncode == 1 and "vitest wrote no report" in r.stderr


def test_ci_without_exports_refuses_a_demo_household_with_no_truth(rig, ci):
    r = ci(STUB_DEMO_NO_TRUTH="full")
    assert r.returncode == 1 and "CI OK" not in r.stdout
    assert "no truth.json" in r.stderr
    assert not any("vitest" in c[-1] for c in rig.runs())


# ---- the gate line: the one line a CI log is read for

GATE = "gate: pytest 123 passed; vitest 1 passed / 0 failed / 0 skipped; contract 6/6; matrix 8/8; stack 12/13"


def test_ci_without_exports_ends_with_the_gate_line_then_ci_ok(rig, ci):
    r = ci()
    assert r.returncode == 0, r.stderr
    lines = r.stdout.rstrip().splitlines()
    assert lines[-2] == GATE and lines[-1].endswith("CI OK ==")
    assert [line for line in lines if line.startswith("gate:")] == [GATE]
    # pytest's own output still reaches the terminal, its summary line included
    assert "123 passed in 4.56s" in lines and "All checks passed!" in lines


def test_ci_gate_line_takes_its_counts_from_the_run(rig, ci):
    report = _report_with(("a", "passed"), ("b", "passed"), ("c", "passed"))
    r = ci(STUB_PYTEST_PASSED="77", STUB_VITEST=report)
    assert r.returncode == 0, r.stderr
    assert r.stdout.rstrip().splitlines()[-2] == (
        "gate: pytest 77 passed; vitest 3 passed / 0 failed / 0 skipped; contract 6/6; matrix 8/8; stack 12/13")
    rig.log.unlink()
    # the count is pytest's `N passed`, wherever it stands in the summary; warnings and deselected tests pass
    r = ci(STUB_PYTEST_LAST="3 deselected, 45 passed, 2 warnings in 9.10s (0:00:09)")
    assert r.returncode == 0, r.stderr
    assert r.stdout.rstrip().splitlines()[-2].startswith("gate: pytest 45 passed; ")


def test_ci_runs_the_stack_test_last_in_a_fresh_directory_of_its_run_and_shows_its_lines(rig, ci):
    r = ci(STUB_STACK_LINE="stack: 190/192")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.rstrip().splitlines()
    assert lines[-2].endswith("; matrix 8/8; stack 190/192")
    # its known cases reach the log on their own lines, and the stage says how long it took
    assert "known: sample: recorded (status 401, want 404)" in lines
    assert any(re.fullmatch(r"stack: the stage took \d+ s", line) for line in lines)
    # it runs after the runtime images are checked, once, given an absolute directory inside the run's own temp dir
    # (removed with it)
    assert lines.index("known: sample: recorded (status 401, want 404)") > max(
        i for i, line in enumerate(lines) if line.startswith("images: "))
    (arg,) = Path(str(rig.log) + ".stack").read_text().splitlines()
    assert Path(arg).is_absolute() and Path(arg).name == "stack" and not Path(arg).parent.exists()


@pytest.mark.parametrize("env, words", [
    ({"STUB_STACK_RC": "1"}, "the stack test FAILED (exit 1"),
    ({"STUB_STACK_LINE": "stack: the matrix printed no summary"}, "the stack test FAILED (exit 0"),
    ({"STUB_STACK_LINE": ""}, "the stack test FAILED (exit 0"),
])
def test_a_failed_stack_test_or_one_without_its_line_fails_the_gate(rig, ci, env, words):
    r = ci(**env)
    assert r.returncode == 1 and words in r.stderr
    assert "CI OK" not in r.stdout and not any(line.startswith("gate:") for line in r.stdout.splitlines())


def test_the_stack_test_runs_only_without_exports(rig, ci):
    for args, env in (((rig.exports,), {"FF_SUITE_LOG": rig.suite_log}), ((rig.outside,), {"FF_DEMO_EXPORTS": "1"})):
        r = ci(*args, **env)
        assert r.returncode == 0, r.stderr
    assert not Path(str(rig.log) + ".stack").exists()


@pytest.mark.parametrize("last,counts", [
    ("120 passed, 3 skipped in 4.56s", "3 skipped"),
    ("45 passed, 1 xfailed in 1.00s", "1 xfailed"),
    ("45 passed, 2 xpassed in 1.00s", "2 xpassed"),
    ("1 xfailed, 45 passed, 3 xpassed, 2 warnings in 9.10s (0:00:09)", "1 xfailed, 3 xpassed"),
    ("45 passed, 1 error in 1.00s", "1 error"),
    ("45 passed, 12 skipped, 2 errors in 1.00s", "12 skipped, 2 errors"),
])
def test_ci_fails_when_the_etl_suite_skips_or_errs(rig, ci, last, counts):
    """Every test of tests/ runs in the gate: a summary counting a test that did not run and pass fails it, naming
    the counts, before anything else runs."""
    r = ci(STUB_PYTEST_LAST=last)
    assert r.returncode == 1
    assert f"the ETL suite's summary reports {counts}: every test in tests/ must run and pass in the gate" \
        in r.stderr.splitlines()
    assert "CI OK" not in r.stdout and not any(line.startswith("gate:") for line in r.stdout.splitlines())
    assert not any("tools/verify" in c[-1] for c in rig.runs())        # nothing runs after the ETL suite


@pytest.mark.parametrize("case", ["pytest", "demo", "vitest-failed", "vitest-skipped", "vitest-error-outside-any-test",
                                  "contract", "matrix-failed", "matrix-error-outside-any-test"])
def test_ci_prints_no_gate_line_when_a_step_fails(rig, ci, case):
    env = {"pytest": {"STUB_PYTEST_RC": "1", "STUB_PYTEST_LAST": "1 failed, 122 passed in 4.56s"},
           "demo": {"STUB_DEMO_FAIL": "tiny"},
           "vitest-failed": {"STUB_VITEST": _report_with(("a", "passed"), ("b", "failed"))},
           "vitest-skipped": {"STUB_VITEST": _report_with(("a", "passed"), ("b", "skipped"))},
           "vitest-error-outside-any-test": {"STUB_VITEST_RC": "1"},
           "contract": {"STUB_CONTRACT_FAIL": "no-income"},
           "matrix-failed": {"STUB_MATRIX_FAIL": "no-income"},
           "matrix-error-outside-any-test": {"STUB_MATRIX_RC": "no-income"}}[case]
    r = ci(**env)
    assert r.returncode == 1
    assert "CI OK" not in r.stdout
    assert not any(line.startswith("gate:") for line in r.stdout.splitlines())


@pytest.mark.parametrize("last", ["no tests ran in 0.01s", "3 xpassed in 0.10s", ""])
def test_ci_stops_when_pytest_prints_no_passed_count(rig, ci, last):
    r = ci(STUB_PYTEST_LAST=last)
    assert r.returncode == 1
    assert "pytest printed no 'N passed' summary" in r.stderr
    assert "CI OK" not in r.stdout and not any(line.startswith("gate:") for line in r.stdout.splitlines())
    assert not any("tools/verify" in c[-1] for c in rig.runs())        # nothing runs after the ETL suite


def test_ci_with_exports_prints_no_gate_line(rig, ci):
    for args, env in (((rig.exports,), {"FF_SUITE_LOG": rig.suite_log}), ((rig.outside,), {"FF_DEMO_EXPORTS": "1"})):
        r = ci(*args, **env)
        assert r.returncode == 0, r.stderr
        assert r.stdout.rstrip().splitlines()[-1].endswith("CI OK ==")
        assert not any(line.startswith("gate:") for line in r.stdout.splitlines())
        rig.log.unlink()


# ---- the runtime images: built after every test, then checked as root (what they hold) and as uid 12345

RT_LINES = [
    "images: the runtime ETL image holds no uv, git, make, curl or dev group, and no tests, harness tools, docs, config,"
    " .git or .env",
    "images: the runtime ETL image's /opt/ff carries the tree's LICENSE, NOTICE, THIRD_PARTY_LICENSES and "
    "THIRD_PARTY_NOTICES.md",
    "images: the runtime ETL image's .pth files are the allowed two",
    "images: uid 12345 validates examples/minimal in the runtime ETL image: config OK (2 files, 1 accounts)",
    "images: uid 12345 ingests it into a world-writable data directory",
    "images: from a directory holding an etl package, uid 12345 imports the image's own (/opt/ff/etl), with HOME /tmp",
    "images: supercronic v0.2.51 runs as uid 12345",
    "images: the runtime ETL image's tzdata holds UTC America/New_York Pacific/Kiritimati, for Python and for "
    "supercronic",
    "images: make_crontab.py writes, as uid 12345, a crontab supercronic accepts in each of UTC America/New_York "
    "Pacific/Kiritimati",
    "images: the web image's /srv/public is deploy/public exactly, its /usr/share/doc/family-financials the tree's "
    "LICENSE, NOTICE, THIRD_PARTY_LICENSES and THIRD_PARTY_NOTICES.md exactly, and no source map ships",
    "images: each runtime image is labelled as the project's (Family Financials ETL, Family Financials web), not its "
    "base image's",
    "images: the web image, run as uid 12345 (defaults), serves /healthz at http://127.0.0.1:8080",
    "images: the web image, run as uid 12345 (domain), serves /healthz at http://127.0.0.1:8080",
    "images: caddy validates and adapts the web server's config (defaults) with no warning but the HTTP port's own,"
    " and its JSON passes tests/test_caddyfile.py",
    "images: caddy validates and adapts the web server's config (domain) with no warning but the HTTP port's own,"
    " and its JSON passes tests/test_caddyfile.py",
    "images: family-financials-etl:rt 209 MB, family-financials-web:rt 209 MB",
]


def test_ci_checks_the_runtime_images_after_every_test_and_before_the_gate_line(rig, ci):
    r = ci()
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert [line for line in lines if line.startswith("images:")] == RT_LINES
    assert lines.index("matrix tiny (Pacific/Kiritimati): 2 passed (1 known gaps) / 0 failed") < lines.index(RT_LINES[0])
    assert lines.index(RT_LINES[-1]) < lines.index(GATE)
    runs = [c for c in rig.runs() if ETL_RT_ID in c or WEB_RT_ID in c]
    # tests/test_caddyfile.py runs twice in the ETL test image, and the npm licence files are read in the web build
    # image
    assert len(runs) == RT_CHECKS - 3
    for call in runs:
        holds = "os.walk" in call[-1] or "/srv/public" in call[-1]
        assert call[call.index("--user") + 1] == ("root" if holds else "12345:12345"), call
    # what each image holds is compared with the tree's four legal files, copied into the run's temp dir
    legal = [c for c in runs if any(a.endswith(":/legal:ro") for a in c)]
    assert len(legal) == 2 and "os.walk" in legal[0][-1]
    for call in legal:
        mount = next(a for a in call if a.endswith(":/legal:ro")).split(":")[0]
        assert mount.endswith("/legal") and not mount.startswith(str(rig.tmp / "repo"))
    assert "/usr/share/doc/family-financials" in legal[1][-1] and WEB_RT_ID in legal[1]
    labelled = [c for c in rig.calls() if c[:2] == ["image", "inspect"] and "{{json .Config.Labels}}" in c]
    assert [c[-1] for c in labelled] == [ETL_RT_ID, WEB_RT_ID]
    # the household: examples/minimal copied into the run's temp dir, mounted read-only but for its data/
    validate = next(c for c in runs if c[-3:] == ["-m", "etl", "validate"])
    home = next(a for a in validate if a.endswith(":/h:ro")).split(":")[0]
    assert ETL_RT_ID in validate and home.endswith("/minimal") and home != str(rig.tmp / "repo" / "examples" / "minimal")
    assert f"{home}/data:/h/data" in validate and validate[validate.index("FF_HOME=/h") - 1] == "-e"
    # the import runs in a directory that holds an etl package of its own
    decoy = next(c for c in runs if "etl.__file__" in c[-1])
    assert decoy[decoy.index("-w") + 1] == "/w" and any(a.endswith(":/w:ro") for a in decoy)
    # the health check, with the image's defaults, then for a domain with Caddy's own state where it may write it
    health = [c for c in runs if "healthz" in c[-1]]
    assert len(health) == 2 and all(WEB_RT_ID in c for c in health)
    assert not any(a.startswith("SITE_ADDRESS=") for a in health[0])
    for env in ("SITE_ADDRESS=example.net", "XDG_DATA_HOME=/tmp/caddy/data", "XDG_CONFIG_HOME=/tmp/caddy/config"):
        assert health[1][health[1].index(env) - 1] == "-e"
    # Caddy reads the config with the image's defaults, then set for a domain with two more names
    caddy = [c for c in runs if "caddy" in c and "validate" in c]
    assert len(caddy) == 2 and all(WEB_RT_ID in c for c in caddy)
    assert not any(a.startswith(("SITE_ADDRESS=", "FF_HOSTNAMES=", "TRUSTED_PROXIES=")) for a in caddy[0])
    for env in ("SITE_ADDRESS=example.net", "FF_HOSTNAMES=dash.example.net alt.example.net",
                "TRUSTED_PROXIES=192.0.2.0/24"):
        assert caddy[1][caddy[1].index(env) - 1] == "-e"
    adapted = [c for c in rig.runs() if "test_caddyfile.py" in c[-1]]
    assert len(adapted) == 2 and all(ETL_ID in c and "FF_CADDY_ADAPTED=/caddy.json" in c for c in adapted)
    # the scheduler's crontab: once per zone, from a copy of examples/minimal's household.yaml in that zone (the stub
    # answers with the zone it finds there), mounted read-only, then read by supercronic -test
    crontabs = [c for c in runs if "make_crontab.py" in c[-1]]
    assert len(crontabs) == 3 and all(ETL_RT_ID in c and c[c.index("FF_CONFIG_DIR=/c") - 1] == "-e" for c in crontabs)
    assert all(any(a.endswith(":/c:ro") and "/zone-" in a for a in c) and "supercronic -test" in c[-1] for c in crontabs)
    # with exports given the stage runs too
    rig.log.unlink()
    r = ci(rig.exports, FF_SUITE_LOG=rig.suite_log)
    assert r.returncode == 0, r.stderr
    assert [line for line in r.stdout.splitlines() if line.startswith("images:")] == RT_LINES


LICENCES_OK = ("licences: npm 1 runtime packages, python 0 distributions, go 2 modules in 2 programs; 0 refused; the "
               "notices' block is current; the licence texts are current")


def test_ci_checks_the_runtime_licences_in_the_runtime_etl_image_after_its_checks(rig, ci):
    r = ci()
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert lines.index(RT_LINES[-2]) < lines.index(LICENCES_OK) < lines.index(GATE)
    (read,) = [c for c in rig.runs() if c[-1] == "--collect"]
    assert ETL_RT_ID in read and read[read.index("--network") + 1] == "none"
    assert read[read.index("--label") + 1] == "family-financials.test=1"
    assert read[read.index("--user") + 1] == "12345:12345"
    (texts,) = [c for c in rig.runs() if "node" in c]
    assert WEB_ID in texts and texts[texts.index("--network") + 1] == "none" and "-v" not in texts
    assert texts[texts.index("--label") + 1] == "family-financials.test=1"
    assert texts[texts.index("--user") + 1] == "12345:12345" and texts[-1] == "node_modules/left-pad"
    copies = [c for c in rig.runs() if "--entrypoint" in c]
    assert [(c[-2], c[-1]) for c in copies] == [(WEB_RT_ID, "/usr/bin/caddy"), (ETL_RT_ID, "/usr/local/bin/supercronic")]
    for c in copies:
        assert c[c.index("--network") + 1] == "none" and c[c.index("--entrypoint") + 1] == "cat" and "-v" not in c
        assert c[c.index("--label") + 1] == "family-financials.test=1" and c[c.index("--user") + 1] == "12345:12345"
    rig.log.unlink()
    r = ci(rig.exports, FF_SUITE_LOG=rig.suite_log)
    assert r.returncode == 0 and LICENCES_OK in r.stdout.splitlines(), r.stderr


def test_a_go_module_without_a_kept_licence_text_fails_the_gate_naming_it(rig, ci):
    shutil.rmtree(rig.tmp / "repo" / licences.GO_DIR / "example.org")
    r = ci()
    assert r.returncode == 1 and "CI OK" not in r.stdout
    assert ("licences: go example.org/sample v1.0.0 (in caddy, supercronic): deploy/licences/go/example.org/"
            "sample@v1.0.0: no licence text is kept for it") in r.stdout
    assert "the runtime dependencies' licence check failed" in r.stderr


@pytest.mark.parametrize("name", ["LICENSE", "THIRD_PARTY_LICENSES"])
def test_a_tree_without_a_legal_file_the_images_must_carry_fails_before_they_are_checked(rig, ci, name):
    (rig.tmp / "repo" / name).unlink()
    r = ci()
    assert r.returncode == 1 and f"the tree has no {name}, which both runtime images must carry" in r.stderr
    assert not any("os.walk" in c[-1] for c in rig.runs())


def test_a_runtime_npm_package_without_a_licence_file_or_stale_licence_texts_fail_the_gate(rig, ci):
    r = ci(STUB_NPM_TEXTS=json.dumps({"node_modules/left-pad": []}))
    assert r.returncode == 1 and "licences: npm left-pad 1.3.0: its package holds no licence file" in r.stdout
    assert "CI OK" not in r.stdout
    rig.log.unlink()
    r = ci(STUB_NPM_TEXTS=json.dumps({"node_modules/left-pad": [["LICENSE", "Another text.\n"]]}))
    assert r.returncode == 1 and "THIRD_PARTY_LICENSES is not the runtime packages' licence texts" in r.stdout
    assert "the runtime dependencies' licence check failed" in r.stderr


@pytest.mark.parametrize("dists, words", [
    ([{"name": "copyleft", "version": "1.0", "license_expression": "GPL-3.0-only", "license": None, "classifiers": []}],
     "licences: python copyleft 1.0: GPL-3.0-only names GPL-3.0-only"),
    ([{"name": "mystery", "version": "0.1", "license_expression": None, "license": None, "classifiers": []}],
     "licences: python mystery 0.1: no licence the check can read"),
], ids=["gpl", "unknown"])
def test_a_refused_runtime_licence_fails_the_gate_naming_the_package(rig, ci, dists, words):
    r = ci(STUB_DISTS=json.dumps(dists))
    assert r.returncode == 1
    assert words in r.stdout and "the runtime dependencies' licence check failed" in r.stderr
    assert "CI OK" not in r.stdout and rig.scans() == [], "nothing runs after the failed check"


def test_stale_third_party_notices_fail_the_gate(rig, ci):
    (rig.tmp / "repo" / "THIRD_PARTY_NOTICES.md").write_text(f"# Notices\n\n{licences.BEGIN}\n{licences.END}\n")
    r = ci()
    assert r.returncode == 1 and "generated block is not the closures as they are" in r.stdout


def test_ci_scans_what_the_tree_ships_for_secrets_offline_from_the_pinned_image(rig, ci):
    """In a checkout: the files git lists (tracked, and untracked but not ignored), copied into the run's temp dir and
    mounted read-only, then the history when the checkout's .git is a directory; each run with no network, as the
    caller, under the harness's label, from gitleaks's image pinned by digest, with the tree's .gitleaks.toml."""
    repo = rig.tmp / "repo"
    (repo / "notes.txt").write_text("sample\n")
    (repo / "tools" / "verify" / "unlisted.txt").write_text("an ignored file\n")
    r = ci(STUB_SHIPPED=r"notes.txt\0gone.txt\0tools/verify/ci.sh\0")   # gone.txt: tracked, deleted from the work tree
    assert r.returncode == 0, r.stderr
    assert json.loads(Path(str(rig.log) + ".scan").read_text()) == ["notes.txt", "tools/verify/ci.sh"]
    lines = r.stdout.splitlines()
    assert lines.index("images: family-financials-etl:rt 209 MB, family-financials-web:rt 209 MB") \
        < lines.index("secrets: no secret in the files git tracks or would") < lines.index(GATE)
    assert "secrets: the history is not scanned here: this checkout's .git is no directory (a linked worktree)" in lines
    (scan,) = rig.scans()
    image = next(a for a in scan if a.startswith("ghcr.io/gitleaks/"))
    assert GITLEAKS_PIN.fullmatch(image), image
    assert scan[scan.index("--network") + 1] == "none" and scan[scan.index("--label") + 1] == "family-financials.test=1"
    assert scan[scan.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    mount = scan[scan.index("-v") + 1]
    assert mount.endswith(":/scan:ro") and mount.split(":")[0].endswith("/shipped")
    assert scan[scan.index(image) + 1:scan.index(image) + 3] == ["dir", "/scan"]
    assert scan[scan.index("--config") + 1] == "/scan/.gitleaks.toml" and "--redact" in scan
    rig.log.unlink()
    (repo / ".git").mkdir()
    r = ci()
    assert r.returncode == 0 and "secrets: no secret in the git history" in r.stdout.splitlines(), r.stderr
    _, history = rig.scans()
    assert history[history.index(image) + 1:history.index(image) + 3] == ["git", "/scan"]
    assert f"{repo}:/scan:ro" in history


@pytest.mark.parametrize("mode", ["dir", "git"])
def test_a_secret_found_in_the_tree_or_its_history_fails_the_gate(rig, ci, mode):
    (rig.tmp / "repo" / ".git").mkdir()
    r = ci(STUB_LEAKS=mode)
    assert r.returncode == 1 and "CI OK" not in r.stdout
    assert ("in the tree's files" if mode == "dir" else "in the git history") in r.stderr


def test_a_tree_that_is_no_checkout_is_scanned_as_it_is_and_one_without_rules_fails(rig, ci):
    r = ci(STUB_NOT_A_CHECKOUT="1")
    assert r.returncode == 0 and "secrets: no secret in the tree" in r.stdout.splitlines(), r.stderr
    (scan,) = rig.scans()
    assert f"{rig.tmp / 'repo'}:/scan:ro" in scan
    rig.log.unlink()
    (rig.tmp / "repo" / ".gitleaks.toml").unlink()
    r = ci()
    assert r.returncode == 1 and "the tree has no .gitleaks.toml" in r.stderr and rig.scans() == []


@pytest.mark.parametrize("env,words", [
    ({"STUB_RT_HOLDS": "/opt/ff/tests"}, "the runtime ETL image holds what it must not"),
    ({"STUB_RT_PTH": "/opt/venv/lib/python3.12/site-packages/_virtualenv.pth\n"
                     "/opt/venv/lib/python3.12/site-packages/evil.pth\n"
                     "/opt/venv/lib/python3.12/site-packages/ff.pth"}, "not exactly the allowed"),
    ({"STUB_RT_VALIDATE": "household.yaml: name: missing — add the household's name"}, "printed no 'config OK'"),
    ({"STUB_RT_NO_MANIFEST": "1"}, "wrote no manifest.json"),
    ({"STUB_RT_IMPORTED": "/w/etl/__init__.py /tmp"}, "(want /opt/ff/etl/__init__.py /tmp)"),
    ({"STUB_RT_IMPORTED": "/opt/ff/etl/__init__.py /root"}, "(want /opt/ff/etl/__init__.py /tmp)"),
    ({"STUB_WEB_HOLDS": "/srv/site/assets/index.js.map"}, "or it ships a source map"),
    ({"STUB_LABELS": json.dumps({"org.opencontainers.image.title": "Caddy"})},
     "the runtime image Family Financials web is not labelled as the project's"),
    ({"STUB_RT_HEALTHZ": "starting"}, "answered 'starting', not ok"),
    ({"STUB_CADDY_RC": "1"}, "caddy validate refused the web server's config (defaults"),
    ({"STUB_CADDY_WARN": "1"}, "Caddy warns about the web server's config (defaults"),
    ({"STUB_CADDY_TEST_RC": "1"}, "(defaults) fails tests/test_caddyfile.py"),
    ({"STUB_SIZE": "unknown"}, "printed no size for family-financials-etl:rt"),
    ({"STUB_RT_TZDATA_RC": "1"}, "the runtime ETL image's tzdata lacks one of UTC America/New_York Pacific/Kiritimati"),
    ({"STUB_RT_CRONTAB_RC": "Pacific/Kiritimati"},
     "supercronic refused the crontab make_crontab.py wrote in Pacific/Kiritimati"),
    ({"STUB_RT_CRONTAB_WRONG": "America/New_York"},
     "the crontab for America/New_York is not the household's hours in its zone"),
], ids=["holds-tests", "a-new-pth", "validate", "ingest", "imports-the-working-dirs-etl", "home", "source-map",
        "base-image-labels", "healthz",
        "caddy-refuses", "caddy-warns", "adapted-json", "size", "tzdata", "crontab-refused", "crontab-hours"])
def test_ci_fails_when_a_runtime_image_check_fails(rig, ci, env, words):
    r = ci(**env)
    assert r.returncode == 1
    assert words in r.stderr, r.stderr
    assert "CI OK" not in r.stdout and not any(line.startswith("gate:") for line in r.stdout.splitlines())


# ---------------------------------------------------------------- demo.sh

DEMO = HERE / "demo.sh"
PROFILES = ["full", "new", "tiny", "empty", "no-investments", "no-income"]
DEFAULT_END = "2026-10-31"                                            # demo.sh's --end when none is given


def demo(rig: Rig, *args, **env):
    return rig.run(["bash", str(DEMO), *map(str, args)], env)


def _the_one(path: str, pattern: str) -> str:
    """The one match of `pattern` (a line, with one group) in the text of `path` under the repository root."""
    found = re.findall(pattern, (ROOT / path).read_text("utf-8"), re.M)
    assert len(found) == 1, f"{path}: {len(found)} lines match {pattern}"
    return found[0]


def test_every_copy_of_the_profile_list_and_the_default_end_is_the_generators():
    """The generator owns the six profiles and the end its blessed hashes are generated to (tools/demo profiles.NAMES,
    generate.BLESSED_END); ci.sh, demo.sh, the matrix's gap table and the tests carry copies, each read here out of
    its file's text, so a seventh profile or a moved end fails the gate instead of leaving a copy on the old set."""
    names, end = list(profiles.NAMES), generate.BLESSED_END
    assert _the_one("tools/verify/ci.sh", r"^PROFILES=\(([^)]*)\)$").split() == names
    assert _the_one("tools/verify/demo.sh", r"^ALL=\(([^)]*)\)$").split() == names
    assert _the_one("tools/verify/demo.sh", r"^END=(\S+)$") == end.isoformat()
    gaps = _the_one("app/src/test/matrixGaps.ts", r"^export const DEMO_PROFILES = \[([^\]]*)\] as const;$")
    assert re.findall(r'"([^"]*)"', gaps) == names
    pinned = _the_one("tests/test_demo_profiles.py", r"^    assert profiles\.NAMES == (\(.*\))$")
    assert list(ast.literal_eval(pinned)) == names
    stated = _the_one("tests/conftest.py", r"^DEMO_END = date\(([0-9, ]+)\)$")
    assert [int(n) for n in stated.split(",")] == [end.year, end.month, end.day]
    assert PROFILES == names and DEFAULT_END == end.isoformat()


def test_demo_sh_generates_then_ingests_each_profile_offline_as_the_calling_user(rig):
    out = rig.tmp / "demo-out"
    r = demo(rig, out)
    assert r.returncode == 0, r.stderr
    for profile in PROFILES:
        assert f"demo {profile}: 42 transactions, quality 0 fail / 2 warn" in r.stdout
    runs = [c for c in rig.runs() if any("tools.demo.generate" in a for a in c)]
    assert len(runs) == 6
    for call, profile in zip(runs, PROFILES, strict=True):
        assert call[:2] == ["run", "--rm"] and call[call.index("--label") + 1] == OWN.label
        assert call[call.index("--network") + 1] == "none"
        assert call[call.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
        assert OWN.etl_test in call
        assert any(a.startswith("HOME=") for a in call)
        assert any(a.startswith("UV_CACHE_DIR=") for a in call)
        assert f"{out.resolve()}:/out" in call
        assert call[-2:] == [profile, DEFAULT_END]
        script = next(a for a in call if "tools.demo.generate" in a)
        assert "etl ingest" in script and "FF_HOME=" in script and "FF_NOW" in script
        assert script.index("tools.demo.generate") < script.index("etl ingest")


def test_demo_sh_takes_profiles_an_end_date_and_an_image_id(rig):
    out = rig.tmp / "demo-out"
    r = demo(rig, out, "--end", "2026-06-30", "tiny", "empty", FF_ETL_IMAGE=ETL_ID)
    assert r.returncode == 0, r.stderr
    runs = [c for c in rig.runs() if any("tools.demo.generate" in a for a in c)]
    assert [c[-2:] for c in runs] == [["tiny", "2026-06-30"], ["empty", "2026-06-30"]]
    assert all(ETL_ID in c for c in runs)


def test_demo_sh_refuses_a_non_empty_directory(rig):
    out = rig.tmp / "demo-out"
    out.mkdir()
    (out / "x").write_text("x")
    r = demo(rig, out)
    assert r.returncode == 2 and "not empty" in r.stderr
    assert rig.runs() == []


@pytest.mark.parametrize("args", [["o", "nope"], ["o", "--end", "20261031"], ["o", "--end", "2026-13-01"], []])
def test_demo_sh_refuses_bad_arguments_without_running_docker(rig, args):
    r = demo(rig, *[rig.tmp / a if a == "o" else a for a in args])
    assert r.returncode == 2
    assert rig.calls() == []


def test_demo_sh_refuses_an_out_dir_that_is_not_a_directory(rig):
    out = rig.tmp / "demo-out"
    out.write_text("x")
    r = demo(rig, out)
    assert r.returncode == 2 and r.stderr.strip() == f"{out} is not a directory"
    assert rig.runs() == [] and out.read_text() == "x"


def test_demo_sh_says_to_run_ci_sh_when_the_image_is_missing(rig):
    r = demo(rig, rig.tmp / "o", STUB_NO_IMAGE="1")
    assert r.returncode == 2 and "tools/verify/ci.sh" in r.stderr
    assert rig.runs() == []


def test_demo_sh_checks_the_image_before_creating_the_directory(rig):
    out = rig.tmp / "absent" / "demo-out"
    r = demo(rig, out, STUB_NO_IMAGE="1")
    assert r.returncode == 2 and "tools/verify/ci.sh" in r.stderr
    assert not out.exists() and not out.parent.exists()


def test_demo_sh_fails_when_a_profile_reports_a_failed_check(rig):
    r = demo(rig, rig.tmp / "o", STUB_DEMO_FAIL="tiny")
    assert r.returncode == 1
    assert "tiny" in r.stderr


def test_demo_sh_fails_when_a_container_fails_or_writes_no_manifest(rig):
    r = demo(rig, rig.tmp / "o", STUB_DEMO_RC="new")
    assert r.returncode == 1 and "new" in r.stderr


def test_demo_sh_goes_on_after_a_failing_profile_and_exits_1(rig):
    r = demo(rig, rig.tmp / "o", STUB_DEMO_RC="new", STUB_DEMO_FAIL="tiny")
    assert r.returncode == 1
    assert len([c for c in rig.runs() if any("tools.demo.generate" in a for a in c)]) == 6
    for profile in ("full", "empty", "no-investments", "no-income"):
        assert f"demo {profile}: 42 transactions, quality 0 fail / 2 warn" in r.stdout
    assert "demo tiny: 42 transactions, quality 1 fail / 2 warn" in r.stdout
    assert r.stderr.splitlines() == ["demo new: generate or ingest failed", "demo tiny: failed"]


def test_demo_sh_says_no_usable_exports_for_a_manifest_without_a_count(rig):
    r = demo(rig, rig.tmp / "o", "tiny", "empty", STUB_DEMO_NO_COUNT="tiny")
    assert r.returncode == 1
    assert "demo tiny: no usable exports (KeyError)" in r.stderr and "Traceback" not in r.stderr
    assert "demo empty: 42 transactions" in r.stdout


# what the generate-and-ingest command imports or reads from the tree: the engine, the generator, the starter rule
# files the generator copies into each household's config, and the file reads the engine shares with the store
CODE_DIRS = ("etl", "tools/demo", "starter", "deploy")


def _mounts(call: list[str]) -> list[str]:
    """the --mount specs of one docker call"""
    return [call[i + 1] for i, a in enumerate(call[:-1]) if a == "--mount"]


def _demo_runs(rig: Rig) -> list[list[str]]:
    return [c for c in rig.runs() if any("tools.demo.generate" in a for a in c)]


def test_demo_sh_run_by_hand_mounts_the_worktrees_code_read_only_over_the_images(rig):
    """Without FF_ETL_IMAGE (a person or an agent running it by hand) the image's code may be a build behind the tree,
    so every container gets the tree's engine, generator and starter files read-only over the image's copies; the
    image's virtualenv stays, and the first line says where the code came from."""
    r = demo(rig, rig.tmp / "o", "tiny", "empty")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0] == "demo: code from the worktree"
    want = [f"type=bind,source={ROOT / d},target=/app/finance-app/{d},readonly" for d in CODE_DIRS]
    runs = _demo_runs(rig)
    assert len(runs) == 2 and all(_mounts(c) == want for c in runs)
    assert all(OWN.etl_test in c for c in runs)
    assert not any(".venv" in m for c in runs for m in _mounts(c))


def test_demo_sh_given_an_image_mounts_no_code(rig):
    """With FF_ETL_IMAGE (ci.sh passes the image it has just built from the tree) the image is the tree's code."""
    r = demo(rig, rig.tmp / "o", "tiny", "empty", FF_ETL_IMAGE=ETL_ID)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0] == "demo: code from the image"
    runs = _demo_runs(rig)
    assert len(runs) == 2 and all(_mounts(c) == [] and ETL_ID in c for c in runs)


def test_demo_sh_run_by_hand_outside_a_repository_refuses_before_running_docker(rig):
    """By hand the code comes from the tree holding the script, so a copy that sits in no repository is exit 2."""
    lone = rig.tmp / "lone" / "tools" / "verify"
    lone.mkdir(parents=True)
    shutil.copy(DEMO, lone / "demo.sh")
    r = rig.run(["bash", str(lone / "demo.sh"), str(rig.tmp / "o"), "tiny"])
    assert r.returncode == 2 and "FF_ETL_IMAGE" in r.stderr
    assert rig.calls() == [] and not (rig.tmp / "o").exists()


# ---------------------------------------------------------------- export-diff.py


def diff(rig: Rig, *args, **env):
    return rig.run([sys.executable, str(EXPORT_DIFF), *map(str, args)], env)


@pytest.fixture
def base(rig):
    return rig.exports_at(rig.scratch / "base" / "data" / "exports")


def test_export_diff_mounts_labels_inside_the_scratch_root(rig, base):
    r = diff(rig, "base", "head", "--expect", "identical")
    assert r.returncode == 0, r.stderr
    (call,) = rig.calls()
    assert f"{base}:/mnt/base:ro" in call and f"{rig.exports}:/mnt/head:ro" in call
    assert call[call.index("--label") + 1] == OWN.label
    assert OWN.etl_test in call
    assert call[-1].endswith("--expect identical")


@pytest.mark.parametrize("side", ["base", "head"])
@pytest.mark.parametrize("case", ["outside", "the-root-itself", "a-symlink-leading-out", "dot-dot-out"])
def test_export_diff_refuses_a_dir_not_strictly_inside_the_scratch_root(rig, base, side, case):
    bad = _outside_cases(rig)[case]
    args = (bad, rig.exports) if side == "base" else (base, bad)
    r = diff(rig, *args, "--expect", "identical")
    assert r.returncode == 2
    assert "scratch root" in r.stderr
    assert rig.calls() == []


def test_export_diff_demo_exports_lift_the_rule(rig):
    r = diff(rig, rig.outside, rig.outside, "--expect", "identical", FF_DEMO_EXPORTS="1")
    assert r.returncode == 0, r.stderr
    assert len(rig.calls()) == 1


@pytest.mark.parametrize("flag", ["--show-rows", "--show", "--sh", "--show-rows=1"])
def test_export_diff_refuses_row_output_for_real_exports(rig, base, flag):
    r = diff(rig, "base", "head", "--expect", "identical", flag)
    assert r.returncode == 2
    assert rig.calls() == []


def test_export_diff_passes_row_output_through_for_demo_exports(rig, base):
    r = diff(rig, "base", "head", "--expect", "identical", "--show-rows", FF_DEMO_EXPORTS="1")
    assert r.returncode == 0, r.stderr
    assert rig.calls()[0][-1].endswith("--expect identical --show-rows")


def test_export_diff_usage_does_not_advertise_row_output(rig):
    r = diff(rig)
    assert r.returncode == 2
    assert "usage" in r.stderr and "show-rows" not in r.stderr
    assert rig.calls() == []


@pytest.mark.parametrize("case", ["relative", "filesystem-root", "the-home", "above-the-home"])
def test_export_diff_refuses_an_unsafe_scratch_root(rig, base, case):
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    value = {"relative": "scratch", "filesystem-root": "/", "the-home": str(home), "above-the-home": str(home.parent)}
    r = diff(rig, "base", "head", "--expect", "identical", FF_SCRATCH=value[case])
    assert r.returncode == 2
    assert "FF_SCRATCH" in r.stderr
    assert rig.calls() == []


def test_export_diff_without_a_config_dir_passes_no_vendor_rules(rig, base):
    assert diff(rig, "base", "head", "--expect", "identical").returncode == 0
    (call,) = rig.calls()
    assert "--vendor-rules" not in call[-1] and not any("/mnt/config" in a for a in call)


@pytest.mark.parametrize("form", ["separate", "joined", "before-the-policy"])
def test_export_diff_mounts_the_config_dir_read_only_for_its_vendor_rules(rig, base, form):
    cfg = rig.tmp / "sample-config"
    cfg.mkdir()
    (cfg / "vendor_rules.yaml").write_text("patterns: []\n")
    args = {"separate": ["--expect", "vendors-fallback-only", "--config-dir", cfg],
            "joined": ["--expect", "vendors-fallback-only", f"--config-dir={cfg}"],
            "before-the-policy": ["--config-dir", cfg, "--expect", "vendors-fallback-only"]}[form]
    r = diff(rig, "base", "head", *args)
    assert r.returncode == 0, r.stderr
    (call,) = rig.calls()
    assert call[call.index("--mount") + 1] == f"type=bind,source={cfg},target=/mnt/config,readonly"
    assert " --vendor-rules /mnt/config/vendor_rules.yaml " in call[-1]
    assert call[-1].endswith("--expect vendors-fallback-only") and "--config-dir" not in call[-1]


@pytest.mark.parametrize("expect", [["--expect", "vendors-fallback-only"], ["--expect=vendors-fallback-only"]])
def test_export_diff_says_the_vendor_policy_needs_a_config_dir(rig, base, expect):
    r = diff(rig, "base", "head", *expect)
    assert r.returncode == 2
    assert "vendors-fallback-only" in r.stderr and "--config-dir DIR" in r.stderr
    assert rig.calls() == []


@pytest.mark.parametrize("case", ["missing", "a-file", "comma", "no-value", "twice"])
def test_export_diff_refuses_a_config_dir_it_cannot_mount(rig, base, case):
    good = rig.tmp / "sample-config"
    good.mkdir()
    (rig.tmp / "a-file").write_text("")
    (rig.tmp / "a,b").mkdir()
    flag = {"missing": ["--config-dir", rig.tmp / "no-such-dir"], "a-file": ["--config-dir", rig.tmp / "a-file"],
            "comma": ["--config-dir", rig.tmp / "a,b"], "no-value": ["--config-dir"],
            "twice": ["--config-dir", good, "--config-dir", good]}[case]
    r = diff(rig, "base", "head", "--expect", "identical", *flag)
    assert r.returncode == 2
    assert "--config-dir" in r.stderr
    assert rig.calls() == []


def test_export_diff_ignores_a_forged_home(rig):
    forged = rig.tmp / "forged"
    rig.exports_at(forged / OWN.scratch / "head" / "data" / "exports")
    env = {"FF_SCRATCH": None, "HOME": forged}
    assert diff(rig, "head", "head", "--expect", "identical", **env).returncode == 2
    spelt = f"~/{OWN.scratch}/head"
    assert diff(rig, spelt, spelt, "--expect", "identical", **env).returncode == 2
    assert rig.calls() == []


# ---------------------------------------------------------------- screenshots.sh


# Home (the route the cases shoot unless they say otherwise) at two viewports in two themes, plus the open drawer at
# the phone width in each theme
HOME_SHOTS = 6


def shots(rig: Rig, label: str, exports, **env):
    env.setdefault("FF_SHOT_ROUTES", "/")
    return rig.run(["bash", str(SHOTS), label, str(exports)], env)


def driver_env(rig: Rig) -> list[str]:
    (call,) = [c for c in rig.runs() if any(a.endswith(":/shots") for a in c)]
    return [call[i + 1] for i, a in enumerate(call[:-1]) if a == "-e"]


@pytest.mark.parametrize("given", [None, "0"])
def test_screenshots_mask_real_exports_without_being_asked(rig, given):
    r = shots(rig, "head", rig.exports, FF_SHOT_PRIVATE=given)
    assert r.returncode == 0, r.stderr
    assert "FF_SHOT_PRIVATE=1" in driver_env(rig)
    assert len(list((rig.scratch / "head" / "shots").glob("*.png"))) == HOME_SHOTS


@pytest.mark.parametrize("case", ["outside", "the-root-itself", "a-symlink-leading-out", "dot-dot-out"])
def test_screenshots_refuse_exports_not_strictly_inside_the_scratch_root(rig, case):
    r = shots(rig, "sample", _outside_cases(rig)[case])
    assert r.returncode == 2
    assert "scratch root" in r.stderr
    assert rig.calls() == []
    assert not (rig.scratch / "sample").exists()


def test_screenshots_demo_exports_lift_both_rules(rig):
    r = shots(rig, "head", rig.outside, FF_DEMO_EXPORTS="1")
    assert r.returncode == 0, r.stderr
    assert "FF_SHOT_PRIVATE=" in driver_env(rig)


@pytest.mark.parametrize("with_out", [False, True])
@pytest.mark.parametrize("case", DEMO_IN_A_REAL_ROOT)
def test_screenshots_refuse_demo_exports_in_a_real_data_root(rig, case, with_out):
    exports, env = _demo_in_a_real_root(rig, OWN)[case]
    out = rig.tmp / "demo-out"
    if with_out:
        env["FF_SHOT_OUT"] = out
    r = shots(rig, "sample", exports, FF_DEMO_EXPORTS="1", **env)
    assert r.returncode == 2
    assert "real-data scratch root" in r.stderr
    assert rig.calls() == []
    assert not (rig.scratch / "sample").exists() and not out.exists()


@pytest.mark.parametrize("case", ["into-ff-scratch", "into-the-default-root-with-ff-scratch-elsewhere", "dangling"])
def test_screenshots_refuse_demo_exports_with_a_json_file_linked_into_a_real_data_root(rig, case):
    """The exports are copied file by file with their links followed: one JSON file that resolves into a root (or not
    at all) refuses the run before anything is copied or any docker call."""
    demo = rig.exports_at(rig.tmp / "demo-links" / "exports")
    default = rig.exports_at(rig.passwd_home / OWN.scratch / "head" / "data" / "exports")
    target, env, words = {
        "into-ff-scratch": (rig.exports / "manifest.json", {}, "real-data scratch root"),
        "into-the-default-root-with-ff-scratch-elsewhere": (default / "manifest.json", {"STUB_HOME": rig.passwd_home},
                                                           "real-data scratch root"),
        "dangling": (rig.tmp / "no-such.json", {}, "does not resolve"),
    }[case]
    (demo / "accounts.json").symlink_to(target)
    r = shots(rig, "sample", demo, FF_DEMO_EXPORTS="1", **env)
    assert r.returncode == 2
    assert words in r.stderr
    assert rig.calls() == []
    assert not (rig.scratch / "sample").exists()


@pytest.mark.parametrize("case", ["missing-manifest", "unknown-route"])
def test_demo_shots_leave_no_output_directory_when_a_later_check_refuses(rig, case):
    """FF_SHOT_OUT is made only once every check has passed."""
    out = rig.tmp / "new-shots" / "full"
    exports, env = {"missing-manifest": (rig.tmp / "no-manifest", {}),
                    "unknown-route": (rig.outside, {"FF_SHOT_ROUTES": "/no-such-route"})}[case]
    exports.mkdir(exist_ok=True)
    r = shots(rig, "sample", exports, FF_DEMO_EXPORTS="1", FF_SHOT_OUT=out, **env)
    assert r.returncode == 2
    assert ("no manifest.json" if case == "missing-manifest" else "not routes of the PAGES manifest") in r.stderr
    assert not out.parent.exists()
    assert rig.runs() == [] and not (rig.scratch / "sample").exists()


@pytest.mark.parametrize("label", ["", "Head", "../x", "a/b", ".hidden", "-x", "a b"])
def test_screenshots_refuse_a_label_that_is_not_a_plain_name(rig, label):
    before = sorted(rig.scratch.rglob("*"))
    r = shots(rig, label, rig.exports)
    assert r.returncode == 2
    assert rig.calls() == []
    assert sorted(rig.scratch.rglob("*")) == before


def test_screenshots_run_the_inspected_image_id(rig):
    assert shots(rig, "head", rig.exports).returncode == 0
    (build,) = [c for c in rig.runs() if any(a.endswith(":/out") for a in c)]
    assert WEB_ID in build and OWN.web_test not in build


@pytest.mark.parametrize("case", ["relative", "filesystem-root", "the-home", "above-the-home"])
def test_screenshots_refuse_an_unsafe_scratch_root(rig, case):
    value = _unsafe_roots(rig)[case]
    exports = rig.exports if case in ("relative", "filesystem-root") else rig.passwd_home / "sample" / "data" / "exports"
    r = shots(rig, "sample", exports, FF_SCRATCH=value, STUB_HOME=rig.passwd_home)
    assert r.returncode == 2
    assert "FF_SCRATCH" in r.stderr
    assert rig.calls() == []
    assert not (rig.passwd_home / "sample" / "shots").exists() and not (rig.scratch / "sample").exists()


def test_screenshots_take_the_scratch_root_from_passwd_not_home(rig):
    forged = rig.exports_at(rig.tmp / "forged" / OWN.scratch / "head" / "data" / "exports")
    env = {"FF_SCRATCH": None, "HOME": rig.tmp / "forged", "STUB_HOME": rig.passwd_home}
    r = shots(rig, "head", forged, **env)
    assert r.returncode == 2 and "scratch root" in r.stderr
    real = rig.exports_at(rig.passwd_home / OWN.scratch / "head" / "data" / "exports")
    r = shots(rig, "head", real, **env)
    assert r.returncode == 0, r.stderr
    assert len(list((rig.passwd_home / OWN.scratch / "head" / "shots").glob("*.png"))) == HOME_SHOTS
    assert not (rig.tmp / "forged" / OWN.scratch / "head" / "shots").exists()


def shots_mount(rig: Rig) -> Path:
    """The host directory the driver's container writes its PNGs and overflow.json into."""
    (call,) = [c for c in rig.runs() if any(a.endswith(":/shots") for a in c)]
    (volume,) = [a for a in call if a.endswith(":/shots")]
    return Path(volume.rsplit(":", 1)[0])


@pytest.mark.parametrize("real", [None, "0"])
def test_demo_shots_may_leave_the_scratch_root_and_real_ones_may_not(rig, real):
    out = rig.tmp / "demo-shots" / "full"                            # neither it nor its parent exists yet
    r = shots(rig, "head", rig.outside, FF_DEMO_EXPORTS="1", FF_SHOT_OUT=out)
    assert r.returncode == 0, r.stderr
    assert shots_mount(rig) == out
    assert len(list(out.glob("*.png"))) == HOME_SHOTS and (out / "overflow.json").exists()
    assert f"screenshots OK in {out} " in r.stdout
    assert not (rig.scratch / "head" / "shots").exists()            # the scratch root keeps only the built site
    assert (rig.scratch / "head" / "site").is_dir()

    rig.log.unlink()
    elsewhere = rig.tmp / "real-shots"
    r = shots(rig, "head", rig.exports, FF_DEMO_EXPORTS=real, FF_SHOT_OUT=elsewhere)
    assert r.returncode == 2
    assert "FF_SHOT_OUT" in r.stderr and "FF_DEMO_EXPORTS=1" in r.stderr
    assert rig.calls() == []
    assert not elsewhere.exists() and not (rig.scratch / "head" / "shots").exists()


def test_real_shots_refuse_even_an_empty_ff_shot_out(rig):
    r = shots(rig, "head", rig.exports, FF_SHOT_OUT="")
    # the real-exports refusal itself, not the demo branch's "must be absolute" that an empty value would also meet
    assert r.returncode == 2 and "FF_SHOT_OUT" in r.stderr and "FF_DEMO_EXPORTS=1" in r.stderr
    assert rig.calls() == []


def test_demo_shots_go_into_an_existing_empty_directory(rig):
    out = rig.tmp / "empty-shots"
    out.mkdir()
    r = shots(rig, "head", rig.outside, FF_DEMO_EXPORTS="1", FF_SHOT_OUT=out)
    assert r.returncode == 0, r.stderr
    assert shots_mount(rig) == out and len(list(out.glob("*.png"))) == HOME_SHOTS


def _unsafe_outs(rig: Rig) -> dict[str, tuple[dict, str, Path | None]]:
    """FF_SHOT_OUT values a demo run refuses: (extra env, the words the refusal must say, a path that must still not
    exist afterwards)."""
    empty_root = rig.tmp / "empty-root"
    empty_root.mkdir()
    full = rig.tmp / "full"
    full.mkdir()
    (full / "keep.png").write_bytes(b"")
    (rig.tmp / "file.txt").write_text("")
    link = rig.tmp / "link"
    link.symlink_to(rig.scratch / "via-link")
    in_repo = ROOT / "tools" / "verify" / "no-such-shots-dir"
    return {
        "relative": ({"FF_SHOT_OUT": "demo-shots"}, "absolute", rig.tmp / "demo-shots"),
        "empty": ({"FF_SHOT_OUT": ""}, "absolute", None),
        "inside-the-repository": ({"FF_SHOT_OUT": in_repo}, "repository", in_repo),
        "the-repository-itself": ({"FF_SHOT_OUT": ROOT}, "repository", None),
        "inside-the-scratch-root": ({"FF_SHOT_OUT": rig.scratch / "out"}, "scratch root", rig.scratch / "out"),
        "the-scratch-root-itself": ({"FF_SHOT_OUT": empty_root, "FF_SCRATCH": empty_root}, "scratch root",
                                    empty_root / "sample"),
        "above-the-scratch-root": ({"FF_SHOT_OUT": rig.tmp / "up", "FF_SCRATCH": rig.tmp / "up" / "root"},
                                   "scratch root", rig.tmp / "up"),
        "a-symlink-into-the-scratch-root": ({"FF_SHOT_OUT": link}, "scratch root", rig.scratch / "via-link"),
        "not-empty": ({"FF_SHOT_OUT": full}, "not empty", None),
        "a-file": ({"FF_SHOT_OUT": rig.tmp / "file.txt"}, "not a directory", None),
        # / is above the repository
        "the-filesystem-root": ({"FF_SHOT_OUT": "/"}, "repository", None),
        # the default root (the harness's scratch directory in the passwd home) holds real data whatever FF_SCRATCH says
        "inside-the-default-root-with-ff-scratch-elsewhere": (
            {"FF_SHOT_OUT": rig.passwd_home / OWN.scratch / "out", "STUB_HOME": rig.passwd_home}, "scratch root",
            rig.passwd_home / OWN.scratch),
        "above-the-default-root-with-ff-scratch-elsewhere": (
            {"FF_SHOT_OUT": rig.passwd_home, "STUB_HOME": rig.passwd_home}, "scratch root", rig.passwd_home),
    }


@pytest.mark.parametrize("case", ["relative", "empty", "inside-the-repository", "the-repository-itself",
                                  "inside-the-scratch-root", "the-scratch-root-itself", "above-the-scratch-root",
                                  "a-symlink-into-the-scratch-root", "not-empty", "a-file", "the-filesystem-root",
                                  "inside-the-default-root-with-ff-scratch-elsewhere",
                                  "above-the-default-root-with-ff-scratch-elsewhere"])
def test_demo_shots_refuse_an_output_directory_that_is_not_safe_to_fill(rig, case):
    env, words, absent = _unsafe_outs(rig)[case]
    r = shots(rig, "sample", rig.outside, FF_DEMO_EXPORTS="1", **env)
    assert r.returncode == 2
    assert "FF_SHOT_OUT" in r.stderr and words in r.stderr
    assert rig.calls() == []
    assert not (rig.scratch / "sample").exists()
    if absent is not None:
        assert not absent.exists()
    assert sorted(p.name for p in (rig.tmp / "full").iterdir()) == ["keep.png"]          # nothing cleared


def test_demo_shots_refuse_a_directory_the_caller_cannot_fill(rig):
    # The driver's container runs as root and hands /shots to the caller at the end, so a directory the caller does not
    # own, or cannot write to, is refused. Root writes through any mode, so as root (the gate's containers) the case is
    # a directory another uid owns; a caller other than root cannot make one of those, so there it is mode 555.
    out = rig.tmp / "not-the-callers"
    out.mkdir()
    if os.geteuid() == 0:
        os.chown(out, 4321, 4321)
    else:
        out.chmod(0o555)
    try:
        r = shots(rig, "sample", rig.outside, FF_DEMO_EXPORTS="1", FF_SHOT_OUT=out)
    finally:
        out.chmod(0o755)
    assert r.returncode == 2
    assert "FF_SHOT_OUT" in r.stderr and "own and can write" in r.stderr
    assert rig.calls() == []
    assert not (rig.scratch / "sample").exists() and list(out.iterdir()) == []


def test_demo_shots_refuse_an_output_directory_that_cannot_be_made(rig):
    # a regular file as the parent: no caller can create a directory there, root included (a mode-555 parent stops
    # only a caller other than root)
    (rig.tmp / "a-file").write_text("")
    r = shots(rig, "sample", rig.outside, FF_DEMO_EXPORTS="1", FF_SHOT_OUT=rig.tmp / "a-file" / "shots")
    assert r.returncode == 2
    assert "FF_SHOT_OUT" in r.stderr and "cannot be created" in r.stderr
    assert rig.calls() == []
    assert not (rig.scratch / "sample").exists()


# ---- what the static site serves at /api/*: the store's documents as an idle, healthy stack answers them

FIXTURES = HERE / "fixtures"
API_DOCS = ["answers", "manual-assets", "prefs", "refresh", "scenarios"]


def served_api(rig: Rig, label: str) -> dict[str, object]:
    api = rig.scratch / label / "site" / "dist" / "api"
    assert sorted(p.name for p in api.iterdir()) == API_DOCS
    assert not any(p.is_symlink() for p in api.iterdir())             # copied, never linked
    return {p.name: json.loads(p.read_text("utf-8")) for p in api.iterdir()}


def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text("utf-8"))


def idle_status(zone: str) -> dict:
    doc = fixture("refresh.idle.json")
    doc["schedule"]["timezone"] = zone
    return doc


def curls(rig: Rig) -> list[str]:
    log = Path(f"{rig.log}.curl")
    return log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("real", [True, False])
def test_screenshots_serve_the_five_store_documents_and_check_each_is_served(rig, real):
    r = shots(rig, "head", rig.exports) if real else shots(rig, "head", rig.outside, FF_DEMO_EXPORTS="1")
    assert r.returncode == 0, r.stderr
    docs = served_api(rig, "head")
    assert docs == {"answers": fixture("answers.empty.json"), "manual-assets": fixture("manual-assets.empty.json"),
                    "prefs": fixture("prefs.empty.json"), "refresh": idle_status("UTC"),
                    "scenarios": fixture("scenarios.synthetic.json")}
    asked = curls(rig)
    for name in API_DOCS:
        assert f"http://127.0.0.1:18201/api/{name}" in asked, name


@pytest.mark.parametrize("assumptions,zone", [
    ({"household": {"timezone": "America/New_York"}}, "America/New_York"),
    ({"household": {"timezone": "Pacific/Kiritimati"}}, "Pacific/Kiritimati"),
    ({"household": {}}, "UTC"),
    ({"household": {"timezone": "not a zone"}}, "UTC"),
    (None, "UTC"),                                                    # no assumptions.json at all
])
def test_the_served_refresh_status_names_the_households_zone(rig, assumptions, zone):
    if assumptions is not None:
        (rig.outside / "assumptions.json").write_text(json.dumps(assumptions))
    r = shots(rig, "head", rig.outside, FF_DEMO_EXPORTS="1")
    assert r.returncode == 0, r.stderr
    assert served_api(rig, "head")["refresh"] == idle_status(zone)


STAMP = "2026-10-30T12:00:00.000Z"
# one document of each kind the store takes (deploy/scenario_store.py validate_answers, validate_manual_assets)
ANSWERS_DOC = {"version": 1, "updated_at": STAMP, "answers": [
    {"key": "txn:sample", "scope": "txn", "txn_id": "sample", "answered_at": STAMP, "set": {"category": "Groceries"}}]}
MANUAL_DOC = {"version": 1, "updated_at": STAMP, "assets": [
    {"id": "sample-car", "name": "Car", "owner": "Joint", "asset_class": "Vehicle", "liquid": False, "updated_at": STAMP,
     "values": [{"as_of": "2026-10-01", "value": 12000}]}]}


def state_at(d: Path, answers: object = ANSWERS_DOC, manual: object = MANUAL_DOC) -> Path:
    """A household's state directory as the demo generator leaves one (either document may be left out: None)."""
    d.mkdir(parents=True)
    for name, doc in (("answers.json", answers), ("manual_assets.json", manual)):
        if doc is not None:
            (d / name).write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return d


FAMILY = Path("state") / "family"   # where the demo generator writes the family documents in its tree


def family_link(tree: Path, target: Path) -> None:
    """The tree's family documents folder made a link to `target` (its state/ parent a real folder)."""
    (tree / FAMILY).parent.mkdir(parents=True, exist_ok=True)
    (tree / FAMILY).symlink_to(target)


def demo_tree(d: Path, *, truth: bool = True, state: bool = True, **docs: object) -> Path:
    """A demo generator's tree at `d`, outside every real-data root: <tree>/data/exports (a manifest), <tree>/truth.json
    (the generator's mark) and <tree>/state/family with the household's documents (state_at; `docs` leaves one out or
    replaces it), each part left out when asked. Returns the exports directory, which the shots are given."""
    exports = Rig.exports_at(d / "data" / "exports")
    if truth:
        (d / "truth.json").write_text('{"profile": "full"}')
    if state:
        state_at(d / FAMILY, **docs)
    return exports


def test_demo_shots_serve_the_households_own_answers_and_manual_assets_from_its_state(rig):
    exports = demo_tree(rig.tmp / "demo-home")
    state = rig.tmp / "demo-home" / FAMILY
    r = shots(rig, "head", exports, FF_DEMO_EXPORTS="1", FF_SHOT_STATE="1")
    assert r.returncode == 0, r.stderr
    docs = served_api(rig, "head")
    assert docs["answers"] == ANSWERS_DOC and docs["manual-assets"] == MANUAL_DOC
    assert docs["prefs"] == fixture("prefs.empty.json") and docs["refresh"] == idle_status("UTC")
    # the state is read, never written
    assert sorted(p.name for p in state.iterdir()) == ["answers.json", "manual_assets.json"]
    assert json.loads((state / "answers.json").read_text()) == ANSWERS_DOC


@pytest.mark.parametrize("left_out", ["answers", "manual", "both"])
def test_a_state_document_the_household_lacks_is_served_empty(rig, left_out):
    exports = demo_tree(rig.tmp / "demo-home", answers=None if left_out in ("answers", "both") else ANSWERS_DOC,
                        manual=None if left_out in ("manual", "both") else MANUAL_DOC)
    r = shots(rig, "head", exports, FF_DEMO_EXPORTS="1", FF_SHOT_STATE="1")
    assert r.returncode == 0, r.stderr
    docs = served_api(rig, "head")
    assert docs["answers"] == (fixture("answers.empty.json") if left_out in ("answers", "both") else ANSWERS_DOC)
    assert docs["manual-assets"] == (fixture("manual-assets.empty.json") if left_out in ("manual", "both")
                                     else MANUAL_DOC)


def test_a_demo_tree_with_no_state_directory_is_served_the_empty_documents_with_a_line_saying_so(rig):
    exports = demo_tree(rig.tmp / "demo-home", state=False)
    r = shots(rig, "head", exports, FF_DEMO_EXPORTS="1", FF_SHOT_STATE="1")
    assert r.returncode == 0, r.stderr
    docs = served_api(rig, "head")
    assert docs["answers"] == fixture("answers.empty.json")
    assert docs["manual-assets"] == fixture("manual-assets.empty.json")
    assert sum("has no state directory" in line for line in r.stdout.splitlines()) == 1
    assert not (rig.tmp / "demo-home" / FAMILY).exists()


@pytest.mark.parametrize("name", ["answers.json", "manual_assets.json"])
def test_a_state_file_that_links_out_of_the_state_directory_is_refused(rig, name):
    """The generator writes no links: a state file that resolves outside the tree's own state directory - here to a
    document of the same shape elsewhere, outside every real-data root - could serve another household's state, so the
    run is refused, naming the file, before anything is created or any docker call."""
    exports = demo_tree(rig.tmp / "demo-home", answers=None, manual=None)
    (rig.tmp / "demo-home" / FAMILY / name).symlink_to(state_at(rig.tmp / "kept") / name)
    out = rig.tmp / "demo-out"
    r = shots(rig, "sample", exports, FF_DEMO_EXPORTS="1", FF_SHOT_STATE="1", FF_SHOT_OUT=out)
    assert r.returncode == 2
    assert "outside the demo tree's state directory" in r.stderr and name in r.stderr
    assert rig.calls() == [] and not (rig.scratch / "sample").exists() and not out.exists()


def test_a_state_file_that_links_inside_the_state_directory_is_served_as_a_copy_of_what_it_names(rig):
    exports = demo_tree(rig.tmp / "demo-home", manual=None)
    state = rig.tmp / "demo-home" / FAMILY
    (state / "kept").mkdir()
    (state / "kept" / "assets.json").write_text(json.dumps(MANUAL_DOC))
    (state / "manual_assets.json").symlink_to(state / "kept" / "assets.json")
    r = shots(rig, "head", exports, FF_DEMO_EXPORTS="1", FF_SHOT_STATE="1")
    assert r.returncode == 0, r.stderr
    assert served_api(rig, "head")["manual-assets"] == MANUAL_DOC


def test_demo_shots_refuse_a_state_directory_that_is_a_link(rig):
    """The generator writes a real directory: a `state` that is a link - here to a household's state outside every
    real-data root - is refused before anything is created or any docker call."""
    exports = demo_tree(rig.tmp / "demo-home", state=False)
    family_link(rig.tmp / "demo-home", state_at(rig.tmp / "kept-state"))
    out = rig.tmp / "demo-out"
    r = shots(rig, "sample", exports, FF_DEMO_EXPORTS="1", FF_SHOT_STATE="1", FF_SHOT_OUT=out)
    assert r.returncode == 2
    assert "FF_SHOT_STATE: the demo tree's state directory is a link" in r.stderr
    assert rig.calls() == [] and not (rig.scratch / "sample").exists() and not out.exists()


@pytest.mark.parametrize("value", ["1", ""])
@pytest.mark.parametrize("demo", [None, "0"])
def test_real_shots_refuse_ff_shot_state_even_an_empty_one(rig, value, demo):
    r = shots(rig, "head", rig.exports, FF_DEMO_EXPORTS=demo, FF_SHOT_STATE=value)
    assert r.returncode == 2
    assert "FF_SHOT_STATE" in r.stderr and "FF_DEMO_EXPORTS=1" in r.stderr
    assert rig.calls() == [] and not (rig.scratch / "head" / "site").exists()


@pytest.mark.parametrize("value", ["a-path", "a-real-looking-state", "true", "", "0", "yes", " 1", "1 "])
def test_demo_shots_take_ff_shot_state_1_and_no_other_value(rig, value):
    """The variable takes no path: a household's state elsewhere (a frozen copy of real inputs, a live store's folder)
    has the store's shape, so no value of it may name one. Anything but 1 is refused, naming the accepted value."""
    exports = demo_tree(rig.tmp / "demo-home")
    elsewhere = state_at(rig.tmp / "kept-state")
    given = {"a-path": rig.tmp / "demo-home" / FAMILY, "a-real-looking-state": elsewhere}.get(value, value)
    out = rig.tmp / "demo-out"
    r = shots(rig, "sample", exports, FF_DEMO_EXPORTS="1", FF_SHOT_STATE=given, FF_SHOT_OUT=out)
    assert r.returncode == 2
    assert "FF_SHOT_STATE takes 1" in r.stderr
    assert rig.calls() == [] and not (rig.scratch / "sample").exists() and not out.exists()


def test_demo_shots_refuse_ff_shot_state_for_exports_with_no_truth_beside_them(rig):
    """truth.json is the generator's mark: a tree without it (a real household's, whatever its state holds) is not a
    demo tree, so its state is never served."""
    for exports in (demo_tree(rig.tmp / "no-truth", truth=False), rig.outside):
        out = rig.tmp / "demo-out"
        r = shots(rig, "sample", exports, FF_DEMO_EXPORTS="1", FF_SHOT_STATE="1", FF_SHOT_OUT=out)
        assert r.returncode == 2
        assert "FF_SHOT_STATE serves a demo household's own state: no truth.json beside these exports" in r.stderr
        assert rig.calls() == [] and not (rig.scratch / "sample").exists() and not out.exists()
    # the control: the same tree with its mark is served
    (rig.tmp / "no-truth" / "truth.json").write_text("{}")
    assert shots(rig, "head", rig.tmp / "no-truth" / "data" / "exports", FF_DEMO_EXPORTS="1",
                 FF_SHOT_STATE="1").returncode == 0


def _state_in_a_real_root(rig: Rig) -> dict[str, tuple[Path, dict]]:
    """Demo trees whose state directory is a link that resolves at or inside a real-data scratch root, as (the tree's
    exports, extra env): a household's state there is real data."""
    def tree(name: str, target: Path) -> Path:
        exports = demo_tree(rig.tmp / name, state=False)
        family_link(rig.tmp / name, target)
        return exports

    return {
        "a-link-into-ff-scratch": (tree("into", state_at(rig.scratch / "demo" / "state")), {}),
        "a-link-to-ff-scratch-itself": (tree("itself", rig.scratch), {}),
        "a-link-into-the-default-root-with-ff-scratch-elsewhere": (
            tree("default", state_at(rig.passwd_home / OWN.scratch / "demo" / "state")), {"STUB_HOME": rig.passwd_home}),
    }


STATE_IN_A_REAL_ROOT = ["a-link-into-ff-scratch", "a-link-to-ff-scratch-itself",
                        "a-link-into-the-default-root-with-ff-scratch-elsewhere"]


@pytest.mark.parametrize("with_out", [False, True])
@pytest.mark.parametrize("case", STATE_IN_A_REAL_ROOT)
def test_demo_shots_refuse_a_state_directory_in_a_real_data_root(rig, case, with_out):
    exports, env = _state_in_a_real_root(rig)[case]
    out = rig.tmp / "demo-out"
    if with_out:
        env["FF_SHOT_OUT"] = out
    r = shots(rig, "sample", exports, FF_DEMO_EXPORTS="1", FF_SHOT_STATE="1", **env)
    assert r.returncode == 2
    assert "FF_SHOT_STATE" in r.stderr and "real-data scratch root" in r.stderr
    assert rig.calls() == [] and not (rig.scratch / "sample").exists() and not out.exists()


@pytest.mark.parametrize("name", ["answers.json", "manual_assets.json"])
@pytest.mark.parametrize("case", ["into-ff-scratch", "into-the-default-root-with-ff-scratch-elsewhere", "dangling",
                                  "a-directory"])
def test_demo_shots_refuse_a_state_file_linked_into_a_real_data_root(rig, name, case):
    """The state files are copied with their links followed: one that resolves into a root (or to no file) refuses
    the run before anything is created or any docker call."""
    exports = demo_tree(rig.tmp / "demo-home", answers=None, manual=None)
    real = state_at(rig.scratch / "head" / "state")
    default = state_at(rig.passwd_home / OWN.scratch / "head" / "state")
    target, env, words = {
        "into-ff-scratch": (real / name, {}, "real-data scratch root"),
        "into-the-default-root-with-ff-scratch-elsewhere": (default / name, {"STUB_HOME": rig.passwd_home},
                                                           "real-data scratch root"),
        "dangling": (rig.tmp / "no-such.json", {}, "does not resolve to a file"),
        "a-directory": (rig.tmp / "demo-home" / "data", {}, "does not resolve to a file"),
    }[case]
    (rig.tmp / "demo-home" / FAMILY / name).symlink_to(target)
    out = rig.tmp / "demo-out"
    r = shots(rig, "sample", exports, FF_DEMO_EXPORTS="1", FF_SHOT_STATE="1", FF_SHOT_OUT=out, **env)
    assert r.returncode == 2
    assert words in r.stderr and name in r.stderr
    assert rig.calls() == [] and not (rig.scratch / "sample").exists() and not out.exists()


@pytest.mark.parametrize("case", ["a-file", "a-dangling-link"])
def test_demo_shots_refuse_a_state_that_is_not_a_directory(rig, case):
    exports = demo_tree(rig.tmp / "demo-home", state=False)
    state = rig.tmp / "demo-home" / FAMILY
    state.parent.mkdir(parents=True)
    if case == "a-file":
        state.write_text("")
    else:
        state.symlink_to(rig.tmp / "no-such-state")
    r = shots(rig, "sample", exports, FF_DEMO_EXPORTS="1", FF_SHOT_STATE="1")
    assert r.returncode == 2
    assert "FF_SHOT_STATE: the demo tree's state is not a directory" in r.stderr
    assert rig.calls() == [] and not (rig.scratch / "sample").exists()


@pytest.mark.parametrize("name,content,why", [
    ("answers.json", "[]", "document must be a JSON object"),
    ("manual_assets.json", "[]", "document must be a JSON object"),
    ("manual_assets.json", json.dumps({**MANUAL_DOC, "updated_at": "x"}), "updated_at must be"),
    ("answers.json", json.dumps(ANSWERS_DOC).replace('"answers": [', '"note": NaN, "answers": ['), "not finite"),
])
def test_demo_shots_refuse_a_state_document_the_store_refuses(rig, name, content, why):
    exports = demo_tree(rig.tmp / "demo-home", **{"answers" if name == "answers.json" else "manual": content})
    out = rig.tmp / "demo-out"
    r = shots(rig, "sample", exports, FF_DEMO_EXPORTS="1", FF_SHOT_STATE="1", FF_SHOT_OUT=out)
    assert r.returncode == 2
    assert name in r.stderr and why in r.stderr
    assert rig.calls() == [] and not (rig.scratch / "sample").exists() and not out.exists()


# ---- the count: every route at every viewport in both themes, Settings again with its add form and with its AI
# dialog open (the driver's CLICK_SHOTS) likewise, plus the open drawer at a phone width in each theme

ALL_ROUTES = len(driver.routes_from_app_tsx((ROOT / "app" / "src" / "App.tsx").read_text("utf-8")))


def test_a_full_run_expects_every_route_at_both_viewports_and_themes_plus_two_drawer_shots(rig):
    expect = (ALL_ROUTES + 2) * 4 + 2
    r = shots(rig, "head", rig.outside, FF_DEMO_EXPORTS="1", FF_SHOT_ROUTES=None, STUB_SHOT_PNGS=expect)
    assert r.returncode == 0, r.stderr
    assert (f"== {ALL_ROUTES} routes -> expecting {expect} screenshots ({ALL_ROUTES} x 2 viewports x 2 themes,"
            " plus 2 shot(s) of a route with a control clicked x 2 viewports x 2 themes,"
            " plus the open drawer at 1 phone viewport x 2 themes) ==") in r.stdout.splitlines()
    assert f"{expect} screenshots (expected {expect}); overflow.json: 0 entries" in r.stdout
    rig.log.unlink()
    r = shots(rig, "head", rig.outside, FF_DEMO_EXPORTS="1", FF_SHOT_ROUTES=None, STUB_SHOT_PNGS=ALL_ROUTES * 4)
    assert r.returncode == 1
    assert f"FAIL: {ALL_ROUTES * 4} PNGs, expected {expect}" in r.stderr


@pytest.mark.parametrize("routes,viewports,expect", [
    ("/", None, 6),
    ("/spending", None, 4),                                           # no Home, no drawer
    ("/spending /", None, 10),
    ("/", "360x780 1100x800", 6),                                     # 360 is a phone width
    ("/", "1100x800 1280x900", 4),                                    # no phone width, no drawer
    ("/", "360x780 390x844", 8),
    ("/settings", None, 12),                                          # Settings, then its add form and AI dialog open
])
def test_a_subset_expects_the_drawer_only_when_it_shoots_home_at_a_phone_width(rig, routes, viewports, expect):
    r = shots(rig, "head", rig.outside, FF_DEMO_EXPORTS="1", FF_SHOT_ROUTES=routes, FF_SHOT_VIEWPORTS=viewports,
              STUB_SHOT_PNGS=expect)
    assert r.returncode == 0, r.stderr
    assert f"-> expecting {expect} screenshots" in r.stdout


def test_overflow_lines_name_the_drawer_and_fonts_that_were_not_ready(rig):
    entries = [
        {"route": "/", "drawer": True, "query": False, "viewport": "390x844", "theme": "dark", "page": True,
         "offenders": ["div.x"], "page_errors": [], "load_problems": []},
        {"route": "/spending", "query": False, "viewport": "1280x900", "theme": "light", "page": False,
         "offenders": [], "page_errors": [], "load_problems": [{"kind": "fonts not ready", "path": ""}]},
        {"route": "/", "drawer": True, "query": False, "viewport": "390x844", "theme": "light", "page": False,
         "offenders": [], "page_errors": [], "load_problems": [{"kind": "http 404", "path": "/api/prefs"}]},
    ]
    r = shots(rig, "head", rig.outside, FF_DEMO_EXPORTS="1", STUB_OVERFLOW=json.dumps(entries))
    assert r.returncode == 1
    err = r.stderr.splitlines()
    assert "FAIL: / (open drawer) 390x844 dark: horizontal overflow (1 offending elements)" in err
    assert "FAIL: /spending: fonts not ready (in 1 viewport/theme combos)" in err
    assert "FAIL: / (open drawer): http 404 /api/prefs (in 1 viewport/theme combos)" in err


# ---- FF_SHOT_NOTHING_BUILT=1: the page shown before any export exists, from the label alone

# the one page at two viewports in two themes
NOTHING_SHOTS = 4


def nothing_built(rig: Rig, label: str, *more, **env):
    env.setdefault("FF_SHOT_NOTHING_BUILT", "1")
    env.setdefault("STUB_SHOT_PNGS", NOTHING_SHOTS)
    return rig.run(["bash", str(SHOTS), label, *map(str, more)], env)


def curl_urls(rig: Rig) -> list[str]:
    curl = Path(f"{rig.log}.curl")
    return curl.read_text().splitlines() if curl.exists() else []


def test_nothing_built_shots_serve_the_dashboard_with_no_export_and_read_no_directory(rig):
    out = rig.tmp / "nothing-built" / "shots"
    r = nothing_built(rig, "first", FF_SHOT_OUT=out)
    assert r.returncode == 0, r.stderr
    assert "== the page shown before any export exists -> expecting 4 screenshots (2 viewports x 2 themes) ==" in r.stdout
    assert "FF_SHOT_NOTHING_BUILT=1" in driver_env(rig)
    assert shots_mount(rig) == out and len(list(out.glob("*.png"))) == NOTHING_SHOTS and (out / "overflow.json").exists()
    dist = rig.scratch / "first" / "site" / "dist"
    assert sorted(p.name for p in dist.iterdir()) == ["api"], "the site holds the build and the store documents only"
    assert sorted(p.name for p in (dist / "api").iterdir()) == ["answers", "manual-assets", "prefs", "refresh", "scenarios"]
    urls = curl_urls(rig)
    assert any(u.endswith("/index.html") for u in urls) and not any("manifest.json" in u for u in urls)
    # no other directory was made: only the label's site under the scratch root, and the shots outside it
    assert sorted(p.name for p in (rig.scratch / "first").iterdir()) == ["site"]


def test_nothing_built_shots_stay_under_the_scratch_root_without_ff_shot_out(rig):
    r = nothing_built(rig, "first")
    assert r.returncode == 0, r.stderr
    assert len(list((rig.scratch / "first" / "shots").glob("*.png"))) == NOTHING_SHOTS


@pytest.mark.parametrize("case", ["an-exports-dir", "a-real-exports-dir", "two-more"])
def test_nothing_built_takes_the_label_alone(rig, case):
    more = {"an-exports-dir": [rig.outside], "a-real-exports-dir": [rig.exports],
            "two-more": [rig.outside, rig.outside]}[case]
    r = nothing_built(rig, "first", *more)
    assert r.returncode == 2
    assert "pass the label alone" in r.stderr
    assert rig.calls() == [] and not (rig.scratch / "first").exists()


@pytest.mark.parametrize("value", ["", "0", "yes", "true", "/some/exports"])
def test_nothing_built_takes_1_and_nothing_else(rig, value):
    r = nothing_built(rig, "first", FF_SHOT_NOTHING_BUILT=value)
    assert r.returncode == 2
    assert "FF_SHOT_NOTHING_BUILT takes 1" in r.stderr
    assert rig.calls() == [] and not (rig.scratch / "first").exists()


@pytest.mark.parametrize("var,value", [("FF_SHOT_STATE", "1"), ("FF_SHOT_STATE", ""), ("FF_SHOT_ROUTES", "/"),
                                       ("FF_SHOT_QUERY", "/data?card=privacy-mode")])
def test_nothing_built_refuses_what_belongs_to_exports_or_routes(rig, var, value):
    r = nothing_built(rig, "first", **{var: value})
    assert r.returncode == 2
    assert f"{var} is not taken with FF_SHOT_NOTHING_BUILT=1" in r.stderr
    assert rig.calls() == [] and not (rig.scratch / "first").exists()


@pytest.mark.parametrize("case", ["inside-ff-scratch", "the-default-root", "the-repository"])
def test_nothing_built_shots_never_land_in_a_real_data_root_or_the_repository(rig, case):
    out, env, words = {
        "inside-ff-scratch": (rig.scratch / "elsewhere", {}, "real-data scratch root"),
        "the-default-root": (rig.passwd_home / OWN.scratch / "x", {"STUB_HOME": rig.passwd_home}, "real-data scratch root"),
        "the-repository": (ROOT / "nothing-built-shots", {}, "outside the repository"),
    }[case]
    r = nothing_built(rig, "first", FF_SHOT_OUT=out, **env)
    assert r.returncode == 2
    assert words in r.stderr
    assert rig.calls() == [] and not out.exists() and not (rig.scratch / "first").exists()


def test_nothing_built_fails_when_the_build_carried_an_export_into_the_site(rig):
    r = nothing_built(rig, "first", FF_SHOT_OUT=rig.tmp / "nb-out", STUB_DIST_EXPORT="manifest.json")
    assert r.returncode == 1
    assert "the built site carries export files (manifest.json)" in r.stderr
    assert not any(any(a.endswith(":/shots") for a in c) for c in rig.runs()), "nothing is shot"


def test_nothing_built_counts_its_four_pictures(rig):
    r = nothing_built(rig, "first", FF_SHOT_OUT=rig.tmp / "nb-out", STUB_SHOT_PNGS=6)
    assert r.returncode == 1
    assert "FAIL: 6 PNGs, expected 4" in r.stderr
    rig.log.unlink()
    r = nothing_built(rig, "second", FF_SHOT_OUT=rig.tmp / "nb-out-2", FF_SHOT_VIEWPORTS="390x844", STUB_SHOT_PNGS=2)
    assert r.returncode == 0, r.stderr
    assert "-> expecting 2 screenshots (1 viewports x 2 themes)" in r.stdout


def test_nothing_built_reports_a_problem_by_the_page_it_shot(rig):
    entries = [{"route": "(nothing built)", "query": False, "viewport": "390x844", "theme": "dark", "page": False,
                "offenders": [], "page_errors": [], "load_problems": [{"kind": "http 404", "path": "/api/prefs"}]}]
    r = nothing_built(rig, "first", FF_SHOT_OUT=rig.tmp / "nb-out", STUB_OVERFLOW=json.dumps(entries))
    assert r.returncode == 1
    assert "FAIL: (nothing built): http 404 /api/prefs (in 1 viewport/theme combos)" in r.stderr.splitlines()


# ---------------------------------------------------------------- narrow.sh


def narrow(rig: Rig, *args, **env):
    return rig.run(["bash", str(NARROW), *args], env)


def pytest_run(root: Path) -> list[str]:
    return [
        "run", "--rm", "--label", OWN.label, "--network", "none", "--user", "root",
        "--mount", f"type=bind,source={root},target=/w,readonly", "-w", "/w", "-e", "PYTHONPATH=/w",
        OWN.etl_test, "uv", "run", "--project", "/app/finance-app", "pytest", "-q", "-p", "no:cacheprovider",
    ]


def test_narrow_pytest_runs_the_etl_image_on_the_read_only_worktree(rig):
    r = narrow(rig, "pytest", "tests/test_sample.py", "tools/verify")
    assert r.returncode == 0, r.stderr
    assert rig.runs() == [pytest_run(ROOT) + ["tests/test_sample.py", "tools/verify"]]
    assert "-v" not in rig.runs()[0]


def test_narrow_takes_the_root_from_the_script_not_the_link_it_was_run_by(rig):
    link = rig.tmp / "elsewhere" / "bin" / "narrow"
    link.parent.mkdir(parents=True)
    link.symlink_to(NARROW)
    for kind in ("pytest", "vitest"):
        r = rig.run(["bash", str(link), kind, "x"], cwd=rig.tmp / "elsewhere")
        assert r.returncode == 0, r.stderr
    pytest_call, vitest_call = rig.runs()
    assert pytest_call[:-1] == pytest_run(ROOT)
    assert f"type=bind,source={ROOT}/app/src,target=/app/finance-app/app/src,readonly" in vitest_call


@pytest.mark.parametrize("layout", ["no-pyproject", "not-under-tools-verify"])
def test_narrow_refuses_a_copy_that_does_not_sit_in_a_repository(rig, layout):
    tree = rig.tmp / "tree"
    where = tree / "tools" / "verify" if layout == "no-pyproject" else tree / "a" / "b"
    where.mkdir(parents=True)
    if layout == "not-under-tools-verify":
        (tree / "pyproject.toml").write_text("")
    shutil.copy(NARROW, where / "narrow.sh")
    r = rig.run(["bash", str(where / "narrow.sh"), "pytest", "x"])
    assert r.returncode == 2
    assert "repository" in r.stderr
    assert rig.calls() == []


# Plays `docker run` for the ETL image inside the image itself: the bind targets become their host sources in the
# working directory and in -e values, and the command after the image runs here, against the image's own installed
# copy of the project, as it would in the container.
REPLAY_DOCKER = r'''#!/usr/bin/env python3
import os
import sys

args = sys.argv[1:]
if args[:2] == ["image", "inspect"]:
    sys.exit(0)
image = next(i for i, a in enumerate(args) if "-etl:" in a)
mounts, env, workdir = {}, {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}, "/"
i = 1
while i < image:
    flag, value = args[i], args[i + 1] if i + 1 < image else ""
    if flag == "--mount":
        spec = dict(part.partition("=")[::2] for part in value.split(","))
        mounts[spec["target"]] = spec["source"]
    if flag in ("--mount", "-w", "-e", "--label", "--user", "--network"):
        if flag == "-w":
            workdir = value
        elif flag == "-e":
            key, _, val = value.partition("=")
            env[key] = val
        i += 2
    else:
        i += 1


def host(path):
    for target, source in mounts.items():
        if path == target or path.startswith(target + "/"):
            return source + path[len(target):]
    return path


env = {k: host(v) for k, v in env.items()}
os.chdir(host(workdir))
os.execvpe(args[image + 1], args[image + 1:], env)
'''


def test_narrow_pytest_imports_the_worktrees_etl_not_the_images(tmp_path):
    """The image installs the project from /app/finance-app; a worktree module the image's copy lacks must win."""
    if shutil.which("uv") is None or not Path("/app/finance-app/etl/__init__.py").is_file():
        pytest.skip("needs the ETL image's installed project")
    tree = tmp_path / "tree"
    (tree / "tools" / "verify").mkdir(parents=True)
    shutil.copy(NARROW, tree / "tools" / "verify" / "narrow.sh")
    shutil.copy(HERE / "harness.sh", tree / "tools" / "verify" / "harness.sh")
    (tree / "pyproject.toml").write_text("")
    (tree / "etl").mkdir()
    (tree / "etl" / "__init__.py").write_text("")
    (tree / "etl" / "worktree_marker.py").write_text("def marker():\n    return 'worktree'\n")
    (tree / "probe").mkdir()
    (tree / "probe" / "test_marker.py").write_text(
        "from etl.worktree_marker import marker\n\n\ndef test_marker():\n    assert marker() == 'worktree'\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text(REPLAY_DOCKER)
    (bindir / "docker").chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PATH"] = f"{bindir}:{os.environ['PATH']}"
    assert_stub_docker(bindir, env["PATH"])
    r = subprocess.run(["bash", str(tree / "tools" / "verify" / "narrow.sh"), "pytest", "probe/test_marker.py"],
                       env=env, cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 passed" in r.stdout


def test_narrow_vitest_mounts_app_src_and_passes_paths_as_arguments(rig, tmp_path):
    paths = ["src/lib/a b.test.ts", "src/$(touch pwned).test.ts", "src/'q';x.test.ts"]
    r = narrow(rig, "vitest", *paths)
    assert r.returncode == 0, r.stderr
    (call,) = rig.runs()
    image = call.index(OWN.web_test)
    assert call[:image] == ["run", "--rm", "--label", OWN.label, "--network", "none", "--mount",
                            f"type=bind,source={ROOT}/app/src,target=/app/finance-app/app/src,readonly"]
    shell = call[image + 1:]
    assert shell[:2] == ["sh", "-lc"]
    # Play the container's shell (without the login profile) with a stub npx that records what it received.
    work = tmp_path / "container"
    (work / "app").mkdir(parents=True)
    rig.stub("npx", '#!/usr/bin/env python3\nimport json, os, sys\n'
                    'open(os.environ["NPX_LOG"], "w").write(json.dumps([os.getcwd()] + sys.argv[1:]))\n')
    npx_log = tmp_path / "npx.json"
    env = {**os.environ, "PATH": f"{rig.bin}:{os.environ['PATH']}", "NPX_LOG": str(npx_log)}
    subprocess.run(["sh", "-c", *shell[2:]], cwd=work, env=env, check=True)
    assert json.loads(npx_log.read_text()) == [str(work / "app"), "vitest", "run", *paths]
    assert not list(work.rglob("pwned"))


def demo_home(rig: Rig, name: str = "demo") -> Path:
    """A generator run's FF_HOME tree outside the scratch root: data/exports with a manifest, and truth.json."""
    home = rig.tmp / name / "full"
    rig.exports_at(home / "data" / "exports")
    (home / "truth.json").write_text('{"profile": "full"}')
    return home


def vitest_prefix() -> list[str]:
    return ["run", "--rm", "--label", OWN.label, "--network", "none", "--mount",
            f"type=bind,source={ROOT}/app/src,target=/app/finance-app/app/src,readonly"]


def test_narrow_vitest_mounts_synthetic_exports_and_their_truth_when_asked(rig):
    home = demo_home(rig)
    r = narrow(rig, "vitest", "src/smoke.test.tsx", "-t", "a title", FF_EXPORTS_DIR=home / "data" / "exports")
    assert r.returncode == 0, r.stderr
    (call,) = rig.runs()
    image = call.index(OWN.web_test)
    assert call[:image] == vitest_prefix() + [
        "--mount", f"type=bind,source={home}/data/exports,target=/exports,readonly",
        "--mount", f"type=bind,source={home}/truth.json,target=/truth.json,readonly",
        "-e", "FF_EXPORTS_DIR=/exports", "-e", "FF_TRUTH=/truth.json", "-e", "FF_REQUIRE_EXPORTS=1"]
    assert call[-3:] == ["src/smoke.test.tsx", "-t", "a title"]


def test_narrow_vitest_takes_ff_truth_over_the_default_and_resolves_links(rig):
    home = demo_home(rig)
    other = rig.tmp / "other-truth.json"
    other.write_text("{}")
    link = rig.tmp / "exports-link"
    link.symlink_to(home / "data" / "exports")
    r = narrow(rig, "vitest", "x", FF_EXPORTS_DIR=link, FF_TRUTH=other)
    assert r.returncode == 0, r.stderr
    (call,) = rig.runs()
    assert f"type=bind,source={home}/data/exports,target=/exports,readonly" in call
    assert f"type=bind,source={other},target=/truth.json,readonly" in call


def test_narrow_vitest_passes_ff_profile_to_the_container(rig):
    home = demo_home(rig)
    r = narrow(rig, "vitest", "src/matrix.test.tsx", FF_EXPORTS_DIR=home / "data" / "exports", FF_PROFILE="no-income")
    assert r.returncode == 0, r.stderr
    (call,) = rig.runs()
    image = call.index(OWN.web_test)
    assert call[image - 6:image] == ["-e", "FF_TRUTH=/truth.json", "-e", "FF_REQUIRE_EXPORTS=1", "-e", "FF_PROFILE=no-income"]
    rig.log.unlink()
    r = narrow(rig, "vitest", "src/matrix.test.tsx", FF_PROFILE="tiny")
    assert r.returncode == 0, r.stderr
    (call,) = rig.runs()
    assert call[:call.index(OWN.web_test)] == vitest_prefix() + ["-e", "FF_PROFILE=tiny"]


def test_narrow_vitest_passes_ff_test_tz_to_the_container_when_set(rig):
    home = demo_home(rig)
    r = narrow(rig, "vitest", "src/matrix.test.tsx", FF_EXPORTS_DIR=home / "data" / "exports", FF_PROFILE="full",
               FF_TEST_TZ="America/New_York")
    assert r.returncode == 0, r.stderr
    (call,) = rig.runs()
    image = call.index(OWN.web_test)
    assert call[image - 4:image] == ["-e", "FF_PROFILE=full", "-e", "FF_TEST_TZ=America/New_York"]
    for zone in ("Pacific/Kiritimati", "UTC", "Etc/GMT+5", "America/Argentina/Buenos_Aires"):
        rig.log.unlink()
        r = narrow(rig, "vitest", "x", FF_TEST_TZ=zone)
        assert r.returncode == 0, r.stderr
        (call,) = rig.runs()
        assert call[:call.index(OWN.web_test)] == vitest_prefix() + ["-e", f"FF_TEST_TZ={zone}"]
    # unset or empty: nothing is passed, and the tests run in UTC (app/vite.config.ts)
    for env in ({"FF_TEST_TZ": None}, {"FF_TEST_TZ": ""}):
        rig.log.unlink()
        r = narrow(rig, "vitest", "x", **env)
        assert r.returncode == 0, r.stderr
        (call,) = rig.runs()
        assert not any(a.startswith("FF_TEST_TZ") for a in call)


@pytest.mark.parametrize("value", ["New York", "a;b", "-x", "../UTC", "/UTC", "UTC,x", "$(id)", "UTC/"])
def test_narrow_vitest_refuses_an_ff_test_tz_that_is_not_a_zone_name(rig, value):
    r = narrow(rig, "vitest", "x", FF_TEST_TZ=value)
    assert r.returncode == 2
    assert "FF_TEST_TZ must be a zone name" in r.stderr
    assert rig.calls() == []


def test_narrow_pytest_ignores_ff_test_tz(rig):
    r = narrow(rig, "pytest", "tests/test_sample.py", FF_TEST_TZ="America/New_York")
    assert r.returncode == 0, r.stderr
    assert rig.runs() == [pytest_run(ROOT) + ["tests/test_sample.py"]]


@pytest.mark.parametrize("value", ["ti ny", "a;b", "-x", "../full", "full,x", "$(id)"])
def test_narrow_vitest_refuses_an_ff_profile_that_is_not_a_plain_name(rig, value):
    r = narrow(rig, "vitest", "x", FF_PROFILE=value)
    assert r.returncode == 2
    assert "FF_PROFILE must be a plain name" in r.stderr
    assert rig.calls() == []


def test_narrow_pytest_ignores_ff_exports_dir_and_ff_profile(rig):
    home = demo_home(rig)
    r = narrow(rig, "pytest", "tests/test_sample.py", FF_EXPORTS_DIR=home / "data" / "exports", FF_PROFILE="full")
    assert r.returncode == 0, r.stderr
    assert rig.runs() == [pytest_run(ROOT) + ["tests/test_sample.py"]]


def _real_data_cases(rig: Rig) -> dict[str, dict[str, Path]]:
    """FF_EXPORTS_DIR / FF_TRUTH values that reach the real-data scratch root: inside it, the root itself, through a
    link, a directory above it, and a truth file inside it beside synthetic exports; and, with FF_SCRATCH pointing
    elsewhere, exports or a truth file under the default root (the harness's scratch directory in the passwd home), or a directory above
    that root."""
    home = demo_home(rig)
    (rig.scratch / "manifest.json").write_text("{}")
    (rig.scratch / "head" / "truth.json").write_text("{}")
    link = rig.tmp / "link-in"
    link.symlink_to(rig.exports)
    default = rig.exports_at(rig.passwd_home / OWN.scratch / "head" / "data" / "exports")
    (default.parent.parent / "truth.json").write_text("{}")
    default_link = rig.tmp / "link-to-default"
    default_link.symlink_to(default)
    return {
        "inside": {"FF_EXPORTS_DIR": rig.exports},
        "the-root-itself": {"FF_EXPORTS_DIR": rig.scratch, "FF_TRUTH": home / "truth.json"},
        "a-symlink-leading-in": {"FF_EXPORTS_DIR": link, "FF_TRUTH": home / "truth.json"},
        "above-the-root": {"FF_EXPORTS_DIR": rig.tmp, "FF_TRUTH": home / "truth.json"},
        "truth-inside": {"FF_EXPORTS_DIR": home / "data" / "exports", "FF_TRUTH": rig.scratch / "head" / "truth.json"},
        "under-the-default-root-with-ff-scratch-elsewhere": {"FF_EXPORTS_DIR": default, "STUB_HOME": rig.passwd_home},
        # the passwd home holds the default root and lies outside FF_SCRATCH, so only the default-root rule refuses it
        "above-the-default-root-with-ff-scratch-elsewhere": {"FF_EXPORTS_DIR": rig.passwd_home,
                                                             "FF_TRUTH": home / "truth.json", "STUB_HOME": rig.passwd_home},
        "a-link-into-the-default-root": {"FF_EXPORTS_DIR": default_link, "FF_TRUTH": home / "truth.json",
                                         "STUB_HOME": rig.passwd_home},
        "truth-under-the-default-root": {"FF_EXPORTS_DIR": home / "data" / "exports",
                                         "FF_TRUTH": default.parent.parent / "truth.json", "STUB_HOME": rig.passwd_home},
    }


@pytest.mark.parametrize("case", ["inside", "the-root-itself", "a-symlink-leading-in", "above-the-root",
                                  "truth-inside", "under-the-default-root-with-ff-scratch-elsewhere",
                                  "above-the-default-root-with-ff-scratch-elsewhere", "a-link-into-the-default-root",
                                  "truth-under-the-default-root"])
def test_narrow_vitest_refuses_exports_or_truth_from_the_real_data_scratch_root(rig, case):
    env = _real_data_cases(rig)[case]
    r = narrow(rig, "vitest", "x", **env)
    assert r.returncode == 2
    assert "real-data scratch root" in r.stderr
    assert rig.calls() == []


def test_narrow_vitest_refuses_the_filesystem_root_as_exports(rig):
    """/ holds every real-data root; the location rule itself refuses it, before any manifest is looked for."""
    r = narrow(rig, "vitest", "x", FF_EXPORTS_DIR="/")
    assert r.returncode == 2
    assert "FF_EXPORTS_DIR / holds the real-data scratch root" in r.stderr
    assert rig.calls() == []


def test_narrow_vitest_takes_the_scratch_root_from_passwd_not_home(rig):
    passwd_root = rig.passwd_home / OWN.scratch
    real = rig.exports_at(passwd_root / "head" / "data" / "exports")
    (real.parent.parent / "truth.json").write_text("{}")
    env = {"FF_SCRATCH": None, "HOME": rig.tmp / "forged", "STUB_HOME": rig.passwd_home}
    r = narrow(rig, "vitest", "x", FF_EXPORTS_DIR=real, **env)
    assert r.returncode == 2 and "real-data scratch root" in r.stderr
    assert rig.calls() == []
    home = demo_home(rig, f"forged/{OWN.scratch}")
    r = narrow(rig, "vitest", "x", FF_EXPORTS_DIR=home / "data" / "exports", **env)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("case", ["relative", "filesystem-root", "the-home", "above-the-home"])
def test_narrow_vitest_refuses_an_unsafe_scratch_root(rig, case):
    home = demo_home(rig)
    value = {"relative": "scratch", "filesystem-root": "/", "the-home": str(rig.passwd_home),
             "above-the-home": str(rig.passwd_home.parent)}[case]
    r = narrow(rig, "vitest", "x", FF_EXPORTS_DIR=home / "data" / "exports", FF_SCRATCH=value,
               STUB_HOME=rig.passwd_home)
    assert r.returncode == 2
    assert "FF_SCRATCH" in r.stderr
    assert rig.calls() == []


def test_narrow_vitest_refuses_what_is_not_a_synthetic_exports_dir(rig):
    home = demo_home(rig)
    exports = home / "data" / "exports"
    (rig.tmp / "no-manifest").mkdir()
    cases = [
        ({"FF_EXPORTS_DIR": "demo/full/data/exports"}, "must be an absolute path"),
        ({"FF_EXPORTS_DIR": rig.tmp / "missing"}, "is not a directory"),
        ({"FF_EXPORTS_DIR": rig.tmp / "no-manifest", "FF_TRUTH": home / "truth.json"}, "no manifest.json"),
        ({"FF_EXPORTS_DIR": exports, "FF_TRUTH": "truth.json"}, "FF_TRUTH must be an absolute path"),
        ({"FF_EXPORTS_DIR": exports, "FF_TRUTH": rig.tmp / "missing.json"}, "no truth file"),
    ]
    for env, message in cases:
        r = narrow(rig, "vitest", "x", **env)
        assert r.returncode == 2, env
        assert message in r.stderr, (env, r.stderr)
    (home / "truth.json").unlink()
    r = narrow(rig, "vitest", "x", FF_EXPORTS_DIR=exports)
    assert r.returncode == 2 and "no truth file" in r.stderr
    comma = rig.exports_at(rig.tmp / "a,b" / "exports")
    r = narrow(rig, "vitest", "x", FF_EXPORTS_DIR=comma, FF_TRUTH=home / "data" / "exports" / "manifest.json")
    assert r.returncode == 2 and "comma" in r.stderr
    assert rig.calls() == []


@pytest.mark.parametrize("args", [[], ["pytest"], ["vitest"], ["jest", "src/a.test.ts"], ["--help"]])
def test_narrow_refuses_an_unknown_runner_or_no_paths(rig, args):
    r = narrow(rig, *args)
    assert r.returncode == 2
    assert "usage" in r.stderr
    assert rig.calls() == []


@pytest.mark.parametrize("kind", ["pytest", "vitest"])
def test_narrow_says_to_run_ci_sh_when_the_image_is_missing(rig, kind):
    r = narrow(rig, kind, "x", STUB_NO_IMAGE="1")
    assert r.returncode == 2
    assert "tools/verify/ci.sh" in r.stderr
    assert rig.runs() == []

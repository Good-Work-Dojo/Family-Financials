# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""tools/maintainer/post-publish.sh and adoption-report.sh, run against a fake `gh`, `docker` and `curl` on PATH that
log every call to a file and answer from canned JSON; the publish gate is the real one, scanning what the fake
`docker save` writes. Each test runs the scripts from a copy of the files they read, in its own temporary directory,
so a test can change ci.yml or CHANGELOG.md; nothing reaches GitHub or a registry."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from conftest import ROOT

REPO = "Example-Org/Example-App"
HEAD = "3f" * 20
SITE = "https://example-org.github.io/Example-App/"
ACTIONS_APP = 15368
TOKEN = "Quillfeatherzanthe"   # an invented denylist token
SCRIPTS = ("tools/maintainer/post-publish.sh", "tools/maintainer/adoption-report.sh")
TREE_FILES = (*SCRIPTS, "tools/verify/release_names.py", "tools/publish_gate.py", "CHANGELOG.md",
              ".github/workflows/ci.yml", "app/src/data/types.ts")

FAKE_GH = r'''
import json, os, sys
argv = sys.argv[1:]
scenario = json.load(open(os.environ["FAKE_SCENARIO"]))
entry = {"tool": "gh", "argv": argv}


def log():
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(json.dumps(entry) + "\n")


def answer(key):
    """a key's answer; a list is answered in order, its last item from then on"""
    ans = scenario["answers"][key]
    if not isinstance(ans, list):
        return ans
    seen_path = os.environ["FAKE_LOG"] + ".seen"
    seen = json.load(open(seen_path)) if os.path.exists(seen_path) else {}
    n = seen.get(key, 0)
    seen[key] = n + 1
    json.dump(seen, open(seen_path, "w"))
    return ans[min(n, len(ans) - 1)]


if argv[:2] == ["auth", "status"]:
    log()
    sys.exit(0)
if argv[:2] == ["release", "create"]:
    entry.update(method="CREATE", endpoint="release " + argv[2],
                 notes=open(argv[argv.index("--notes-file") + 1]).read())
    log()
    sys.exit(0)
if argv[:1] == ["api"]:
    method, body, endpoint, fields, i = None, None, None, {}, 1
    while i < len(argv):
        a = argv[i]
        if a == "-X":
            method, i = argv[i + 1], i + 2
        elif a == "--input":
            body, i = json.load(open(argv[i + 1])), i + 2
        elif a in ("-f", "-F"):
            k, v = argv[i + 1].split("=", 1)
            fields[k], i = v, i + 2
        elif a.startswith("-"):
            i += 1
        else:
            endpoint, i = endpoint or a, i + 1
    method = method or ("POST" if fields or body is not None else "GET")
    entry.update(method=method, endpoint=endpoint, body=body, fields=fields)
    log()
    key = f"{method} {endpoint}"
    if key not in scenario["answers"]:
        if method == "GET" or endpoint == "graphql":
            print(f"fake gh: no answer for {key}", file=sys.stderr)
            sys.exit(3)
        sys.exit(0)
    ans = answer(key)
    if ans.get("status", 200) >= 400:
        print(f"gh: {ans.get('message', 'Not Found')} (HTTP {ans['status']})", file=sys.stderr)
        sys.exit(1)
    if "body" in ans:
        print(json.dumps(ans["body"]))
    sys.exit(0)
print("fake gh: unexpected call", file=sys.stderr)
sys.exit(3)
'''

FAKE_DOCKER = r'''
import io, json, os, sys, tarfile
argv = sys.argv[1:]
scenario = json.load(open(os.environ["FAKE_SCENARIO"])).get("docker", {})
cfg = os.environ.get("DOCKER_CONFIG")
entry = {"tool": "docker", "argv": argv, "docker_config": cfg,
         "config_entries": sorted(os.listdir(cfg)) if cfg and os.path.isdir(cfg) else None}
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps(entry) + "\n")
if argv[0] == "pull":
    if scenario.get("pull", 0):
        print("Error response from daemon: denied", file=sys.stderr)
    sys.exit(scenario.get("pull", 0))
if argv[:2] == ["manifest", "inspect"]:
    print(json.dumps({"manifests": [{"platform": {"os": "linux", "architecture": a}}
                                    for a in scenario.get("arches", ["amd64", "arm64"])]
                      + [{"platform": {"os": "unknown", "architecture": "unknown"}}]}))
elif argv[0] == "save":
    data = json.dumps([{"Config": "config.json", "Layers": []}]).encode()
    with tarfile.open(argv[argv.index("-o") + 1], "w") as tf:
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
elif argv[0] == "inspect":
    print(json.dumps([{"Config": {"Env": ["PATH=/usr/bin"], "Labels": {"org.opencontainers.image.title": "app"}}}]))
elif argv[0] == "history":
    print(scenario.get("history", "IMAGE CREATED CREATED BY SIZE"))
'''

FAKE_CURL = r'''
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"tool": "curl", "argv": argv}) + "\n")
ans = json.load(open(os.environ["FAKE_SCENARIO"])).get("curl", {}).get(argv[-1], {"code": 404, "body": ""})
with open(argv[argv.index("-o") + 1], "w") as f:
    f.write(ans["body"])
print(ans["code"], end="")
'''

PAGE = ('<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src \'self\'">'
        '<title>Demo</title></head><body></body></html>')


def ci_contexts(tree: Path) -> list[str]:
    jobs = yaml.safe_load((tree / ".github/workflows/ci.yml").read_text("utf-8"))["jobs"]
    return [job.get("name") or job_id for job_id, job in jobs.items()]


def run_of(event: str, conclusion: str = "success", status: str = "completed", run_id: int = 41) -> dict:
    return {"workflow_runs": [{"id": run_id, "status": status, "conclusion": conclusion, "event": event,
                               "head_sha": HEAD, "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}"}]}


def wanted_ruleset(contexts: list[str]) -> dict:
    """the ruleset as GitHub returns it once it holds what the script asks for"""
    return {"id": 7, "name": "default-branch", "target": "branch", "source_type": "Repository", "source": REPO,
            "enforcement": "active", "bypass_actors": [],
            "conditions": {"ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]}},
            "rules": [{"type": "required_status_checks", "parameters": {
                          "strict_required_status_checks_policy": False, "do_not_enforce_on_create": False,
                          "required_status_checks": [{"context": c, "integration_id": ACTIONS_APP}
                                                     for c in reversed(contexts)]}},
                      {"type": "non_fast_forward"}, {"type": "deletion"}]}


def scenario(tree: Path, done: bool = False, tag: str = "v1.0.0", visibility: str = "public") -> dict:
    """the answers of a repository just made public (`done=False`) or of one this script already finished"""
    on = "enabled" if done else "disabled"
    r = f"repos/{REPO}"
    pages = {"body": {"build_type": "workflow", "html_url": SITE}}
    answers = {
        f"GET {r}": {"body": {"visibility": visibility, "permissions": {"admin": True}, "default_branch": "main",
                              "owner": {"type": "Organization"}, "has_discussions": False,
                              "security_and_analysis": {"secret_scanning": {"status": on},
                                                        "secret_scanning_push_protection": {"status": on}}}},
        f"GET {r}/branches/main": {"body": {"commit": {"sha": HEAD}}},
        f"GET {r}/vulnerability-alerts": {} if done else {"status": 404},
        f"GET {r}/automated-security-fixes": {"body": {"enabled": True, "paused": False}} if done else {"status": 404},
        f"GET {r}/private-vulnerability-reporting": {"body": {"enabled": done}},
        f"GET {r}/code-scanning/default-setup": {"body": {"state": "configured" if done else "not-configured"}},
        f"GET {r}/rulesets?includes_parents=false&per_page=100": {"body": [
            {"id": 7, "name": "default-branch", "source_type": "Repository"}] if done else []},
        f"GET {r}/rulesets/7": {"body": wanted_ruleset(ci_contexts(tree))},
        f"GET {r}/actions/variables/FF_PAGES_ENABLED": {"body": {"name": "FF_PAGES_ENABLED", "value": "true"}}
        if done else {"status": 404},
        f"GET {r}/pages": pages if done else [{"status": 404}, pages],
        f"GET {r}/environments/github-pages": {"body": {"name": "github-pages", "deployment_branch_policy": {
            "protected_branches": False, "custom_branch_policies": True}}},
        f"GET {r}/environments/github-pages/deployment-branch-policies?per_page=100": {"body": {
            "total_count": 2 if done else 1,
            "branch_policies": [{"id": 1, "name": "main", "type": "branch"}]
            + ([{"id": 2, "name": "v*", "type": "tag"}] if done else [])}},
        f"GET {r}/actions/workflows/pages.yml/runs?event=workflow_dispatch&head_sha={HEAD}&per_page=20":
            {"body": run_of("workflow_dispatch", run_id=40)} if done
            else [{"body": {"workflow_runs": []}}, {"body": run_of("workflow_dispatch", run_id=40)}],
        f"GET {r}/releases/tags/{tag}": {"body": {"tag_name": tag}} if done else {"status": 404},
        f"GET {r}/commits/{tag}": {"body": {"sha": HEAD}},
        f"GET {r}/actions/workflows/release.yml/runs?event=release&head_sha={HEAD}&per_page=20":
            {"body": run_of("release")},
    }
    return {"answers": answers, "docker": {},
            "curl": {SITE: {"code": 200, "body": PAGE}, SITE + "manifest.json": {"code": 200, "body": "{}"}}}


class Rig:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.tree = tmp / "tree"
        for rel in TREE_FILES:
            (self.tree / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, self.tree / rel)
        self.bin = tmp / "bin"
        self.bin.mkdir()
        for name, body in (("gh", FAKE_GH), ("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
            p = self.bin / name
            p.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
            p.chmod(0o755)
        self.denylist = tmp / "denylist"
        self.denylist.mkdir()
        (self.denylist / "manual.tsv").write_text(f"name\t{TOKEN}\n", encoding="utf-8")
        self.log = tmp / "calls.jsonl"
        self.scenario_file = tmp / "scenario.json"
        (tmp / "home").mkdir()
        (tmp / "tmp").mkdir()
        self.scenario = scenario(self.tree)

    def run(self, *args: str, script: str = SCRIPTS[0]) -> subprocess.CompletedProcess:
        self.scenario_file.write_text(json.dumps(self.scenario), encoding="utf-8")
        for p in (self.log, Path(str(self.log) + ".seen")):
            p.unlink(missing_ok=True)
        env = {"PATH": f"{self.bin}:/usr/local/bin:/usr/bin:/bin", "HOME": str(self.tmp / "home"),
               "TMPDIR": str(self.tmp / "tmp"), "FAKE_LOG": str(self.log), "FAKE_SCENARIO": str(self.scenario_file),
               "GH_TOKEN": "invented-gh-token-value"}
        return subprocess.run([str(self.tree / script), *args], env=env, capture_output=True, text=True,
                              timeout=120, check=False)

    def publish(self, *args: str) -> subprocess.CompletedProcess:
        return self.run("--repo", REPO, "--denylist", str(self.denylist), *args)

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text("utf-8").splitlines()]

    def changes(self) -> list[str]:
        """every gh call that changes the repository, as `METHOD endpoint`"""
        return [f"{c['method']} {c['endpoint']}" for c in self.calls()
                if c["tool"] == "gh" and c.get("method") not in (None, "GET")]

    def tools(self, tool: str, verb: str | None = None) -> list[dict]:
        return [c for c in self.calls() if c["tool"] == tool and (verb is None or c["argv"][0] == verb)]


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


R = f"repos/{REPO}"
FIRST_RUN_CHANGES = [
    f"PUT {R}/vulnerability-alerts", f"PUT {R}/automated-security-fixes", f"PATCH {R}",
    f"PUT {R}/private-vulnerability-reporting", f"PATCH {R}/code-scanning/default-setup", f"POST {R}/rulesets",
    f"POST {R}/actions/variables", f"POST {R}/pages", f"POST {R}/environments/github-pages/deployment-branch-policies",
    f"POST {R}/actions/workflows/pages.yml/dispatches", "CREATE release v1.0.0",
]
ENV = f"{R}/environments/github-pages"


def test_the_scripts_are_executable_bash_and_name_no_private_path_or_token():
    for rel in SCRIPTS:
        p = ROOT / rel
        text = p.read_text("utf-8")
        assert p.stat().st_mode & stat.S_IXUSR, rel
        assert text.startswith("#!/usr/bin/env bash\n") and "set -euo pipefail" in text, rel
        for word in ("/home/", "GH_TOKEN", "GITHUB_TOKEN", "--force", "set -x"):
            assert word not in text, (rel, word)


def test_a_first_run_makes_each_change_in_order_then_pulls_scans_and_smoke_tests(rig):
    r = rig.publish()
    assert r.returncode == 0, r.stderr
    assert rig.changes() == FIRST_RUN_CHANGES
    order = [c["tool"] if c["tool"] != "gh" else f"{c.get('method', 'auth')} {c.get('endpoint', '')}"
             for c in rig.calls()]
    assert order[:3] == ["auth ", f"GET {R}", f"GET {R}/branches/main"]
    last_change = order.index("CREATE release v1.0.0")
    first_docker, first_curl = order.index("docker"), order.index("curl")
    assert last_change < first_docker < first_curl
    pulls = [c["argv"][-1] for c in rig.tools("docker", "pull")]
    assert pulls == ["ghcr.io/example-org/example-app-etl:1.0.0", "ghcr.io/example-org/example-app-web:1.0.0"]
    saves = [c["argv"][-1] for c in rig.tools("docker", "save")]
    assert saves == pulls, "the gate scans both pulled images"
    assert "findings: 0 (accepted: 0)" in r.stdout
    assert [c["argv"][-1] for c in rig.tools("curl")] == [SITE, SITE + "manifest.json"]
    assert "invented-gh-token" not in r.stdout + r.stderr


def test_a_dry_run_reads_but_changes_pulls_and_fetches_nothing(rig):
    r = rig.publish("--dry-run")
    assert r.returncode == 0, r.stderr
    assert rig.changes() == []
    assert rig.tools("docker") == [] and rig.tools("curl") == []
    assert {c["method"] for c in rig.calls() if c["tool"] == "gh" and "method" in c} == {"GET"}
    for shown in (f"would run: gh api -X PUT {R}/vulnerability-alerts", "would run: gh release create v1.0.0",
                  f"would run: gh api -X POST {R}/rulesets", "docker pull -q ghcr.io/example-org/example-app-etl:1.0.0",
                  "docker manifest inspect ghcr.io/example-org/example-app-web:1.0.0", "tools/publish_gate.py images"):
        assert shown in r.stdout, shown
    assert "nothing was changed" in r.stdout


CLA_REMINDER = ("by hand, once: install the CLA Assistant GitHub App on Example-Org/Example-App, pointed at a Gist "
                "holding CLA.md, so that it asks the author of each first pull request to sign")


@pytest.mark.parametrize("args", [(), ("--dry-run",)], ids=["run", "dry-run"])
def test_the_last_line_reminds_the_maintainer_to_install_the_cla_app_by_hand(rig, args):
    r = rig.publish(*args)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[-1] == CLA_REMINDER
    calls = json.dumps(rig.calls()).lower()
    assert "cla.md" not in calls and "cla-assistant" not in calls and "/gists" not in calls   # a reminder, no call


def test_a_second_run_finds_everything_done_and_changes_nothing(rig):
    rig.scenario = scenario(rig.tree, done=True)
    r = rig.publish()
    assert r.returncode == 0, r.stderr
    assert rig.changes() == []
    for said in ("Dependabot alerts: already on", "ruleset default-branch: already as wanted",
                 "FF_PAGES_ENABLED: already true", "release v1.0.0: already published"):
        assert said in r.stdout, said
    assert len(rig.tools("docker", "pull")) == 2 and len(rig.tools("curl")) == 2


def test_a_private_repository_stops_before_anything_changes(rig):
    rig.scenario = scenario(rig.tree, visibility="private")
    r = rig.publish()
    assert r.returncode == 1
    assert "is private: make it public" in r.stderr
    assert rig.changes() == [] and rig.tools("docker") == []
    assert [c.get("endpoint") for c in rig.calls() if c["tool"] == "gh"] == [None, R]


def test_an_unexpected_head_stops_before_anything_changes(rig):
    r = rig.publish("--expect-sha", "0" * 40)
    assert r.returncode == 1 and f"is {HEAD}, not {'0' * 40}" in r.stderr
    assert rig.changes() == []
    assert rig.publish("--expect-sha", HEAD[:12]).returncode == 0


@pytest.mark.parametrize("args", [(), ("--denylist", "/nonexistent/denylist"),
                                  ("--skip-gate", "--denylist", "/nonexistent/denylist")])
def test_without_a_usable_denylist_nothing_runs(rig, args):
    r = rig.run("--repo", REPO, *args)
    assert r.returncode == 2 and r.stderr.startswith("post-publish: ")
    assert rig.calls() == []


def test_skipping_the_gate_still_pulls_but_scans_nothing(rig):
    r = rig.run("--repo", REPO, "--skip-gate")
    assert r.returncode == 0, r.stderr
    assert len(rig.tools("docker", "pull")) == 2 and rig.tools("docker", "save") == []
    assert "publish gate: skipped" in r.stdout


def test_the_ruleset_requires_the_jobs_of_ci_yml_by_their_names(rig):
    (rig.tree / ".github/workflows/ci.yml").write_text(
        "name: ci\non: [push]\njobs:\n  lint:\n    runs-on: ubuntu-24.04\n    steps:\n      - name: not a check\n"
        "        run: 'true'\n  build:\n    name: Build it\n    runs-on: ubuntu-24.04\n    steps:\n"
        "      - run: 'true'\n", encoding="utf-8")
    r = rig.publish()
    assert r.returncode == 0, r.stderr
    [body] = [c["body"] for c in rig.calls() if c.get("endpoint") == f"{R}/rulesets" and c["method"] == "POST"]
    checks = body["rules"][2]["parameters"]["required_status_checks"]
    assert [c["context"] for c in checks] == ["lint", "Build it"] == ci_contexts(rig.tree)
    assert {c["integration_id"] for c in checks} == {ACTIONS_APP}
    assert [rule["type"] for rule in body["rules"]] == ["deletion", "non_fast_forward", "required_status_checks"]
    assert body["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]


def test_the_shipped_ci_yml_gives_its_own_jobs(rig):
    assert rig.publish().returncode == 0
    [body] = [c["body"] for c in rig.calls() if c.get("endpoint") == f"{R}/rulesets"]
    checks = [c["context"] for c in body["rules"][2]["parameters"]["required_status_checks"]]
    assert checks == ci_contexts(rig.tree) and checks


def test_a_ruleset_of_the_same_name_that_differs_is_updated_in_place(rig):
    stale = wanted_ruleset(["an-old-job"])
    rig.scenario["answers"][f"GET {R}/rulesets?includes_parents=false&per_page=100"] = {
        "body": [{"id": 9, "name": "default-branch", "source_type": "Repository"},
                 {"id": 3, "name": "default-branch", "source_type": "Organization"}]}
    rig.scenario["answers"][f"GET {R}/rulesets/9"] = {"body": stale}
    r = rig.publish()
    assert r.returncode == 0, r.stderr
    assert f"PUT {R}/rulesets/9" in rig.changes() and f"POST {R}/rulesets" not in rig.changes()


def environment_changes(rig: Rig) -> list[tuple[str, dict | None]]:
    """every change to the github-pages environment, as (`METHOD endpoint`, body)"""
    return [(f"{c['method']} {c['endpoint']}", c["body"]) for c in rig.calls()
            if c["tool"] == "gh" and c.get("method") not in (None, "GET") and c["endpoint"].startswith(ENV)]


def test_a_pages_environment_without_the_tag_policy_gets_it(rig):
    r = rig.publish()
    assert r.returncode == 0, r.stderr
    assert environment_changes(rig) == [(f"POST {ENV}/deployment-branch-policies", {"name": "v*", "type": "tag"})]
    assert "the branch main may deploy already" in r.stdout


def test_a_pages_environment_with_the_tag_policy_is_left_alone(rig):
    rig.scenario = scenario(rig.tree, done=True)
    r = rig.publish()
    assert r.returncode == 0, r.stderr
    assert environment_changes(rig) == []
    assert "the tag v* may deploy already" in r.stdout


def test_a_dry_run_prints_the_tag_policy_without_adding_it(rig):
    r = rig.publish("--dry-run")
    assert r.returncode == 0, r.stderr
    assert environment_changes(rig) == []
    assert f"would run: gh api -X POST {ENV}/deployment-branch-policies" in r.stdout
    assert '"name":"v*","type":"tag"' in r.stdout


@pytest.mark.parametrize("environment", [{"status": 404}, {"body": {"name": "github-pages", "deployment_branch_policy": {
    "protected_branches": True, "custom_branch_policies": False}}}], ids=["absent", "protected-branches-only"])
def test_a_pages_environment_limited_to_protected_branches_moves_to_chosen_refs_keeping_the_default_branch(
        rig, environment):
    rig.scenario["answers"][f"GET {ENV}"] = environment
    del rig.scenario["answers"][f"GET {ENV}/deployment-branch-policies?per_page=100"]
    r = rig.publish()
    assert r.returncode == 0, r.stderr
    assert environment_changes(rig) == [
        (f"PUT {ENV}", {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}),
        (f"POST {ENV}/deployment-branch-policies", {"name": "main", "type": "branch"}),
        (f"POST {ENV}/deployment-branch-policies", {"name": "v*", "type": "tag"})]


def test_a_pages_environment_open_to_every_ref_is_left_alone(rig):
    rig.scenario["answers"][f"GET {ENV}"] = {"body": {"name": "github-pages", "deployment_branch_policy": None}}
    r = rig.publish()
    assert r.returncode == 0, r.stderr
    assert environment_changes(rig) == []
    assert "every branch and tag may deploy already" in r.stdout


def test_the_release_is_the_changelogs_first_versioned_section_with_its_notes(rig):
    (rig.tree / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n- Not yet.\n\n## [2.3.4] - 2026-01-01\n\n### Added\n- An invented thing.\n"
        "\n## [2.3.3] - 2025-12-01\n\n- Older.\n\n[2.3.4]: https://example.net/2.3.4\n", encoding="utf-8")
    rig.scenario = scenario(rig.tree, tag="v2.3.4")
    r = rig.publish()
    assert r.returncode == 0, r.stderr
    [create] = [c for c in rig.calls() if c.get("method") == "CREATE"]
    assert create["notes"] == "### Added\n- An invented thing.\n"
    argv = create["argv"]
    assert argv[:3] == ["release", "create", "v2.3.4"] and argv[argv.index("--target") + 1] == HEAD
    assert "--prerelease" not in argv
    assert [c["argv"][-1] for c in rig.tools("docker", "pull")][0] == "ghcr.io/example-org/example-app-etl:2.3.4"


def test_the_anonymous_pull_uses_an_empty_docker_configuration(rig):
    assert rig.publish().returncode == 0
    for c in rig.tools("docker", "pull") + [c for c in rig.tools("docker") if c["argv"][0] == "manifest"]:
        assert c["docker_config"] and c["config_entries"] == [], c
        assert not c["docker_config"].startswith(str(rig.tmp / "home"))


def test_a_failed_pull_prints_the_package_settings_and_stops(rig):
    rig.scenario["docker"] = {"pull": 1}
    r = rig.publish()
    assert r.returncode == 1
    for name in ("example-app-etl", "example-app-web"):
        assert f"https://github.com/orgs/Example-Org/packages/container/{name}/settings" in r.stderr
    assert rig.tools("docker", "save") == [] and rig.tools("curl") == []


def test_an_image_without_both_architectures_stops(rig):
    rig.scenario["docker"] = {"arches": ["amd64"]}
    r = rig.publish()
    assert r.returncode == 1 and "holds no linux/arm64 image" in r.stderr
    assert rig.tools("curl") == []


def test_a_gate_finding_stops_the_script_and_never_prints_the_token(rig):
    rig.scenario["docker"] = {"history": f"IMAGE CREATED CREATED BY\nabc 1 day ago RUN echo {TOKEN}"}
    r = rig.publish()
    assert r.returncode == 1
    assert "findings: 2" in r.stdout and "make both packages private" in r.stderr
    assert TOKEN.lower() not in (r.stdout + r.stderr).lower()
    assert rig.tools("curl") == []


def test_a_failed_release_run_stops_before_the_images(rig):
    rig.scenario["answers"][f"GET {R}/actions/workflows/release.yml/runs?event=release&head_sha={HEAD}&per_page=20"] = {
        "body": run_of("release", conclusion="failure")}
    r = rig.publish()
    assert r.returncode == 1 and "release.yml's run ended 'failure'" in r.stderr
    assert f"https://github.com/{REPO}/actions/runs/41" in r.stderr
    assert rig.tools("docker") == []


def test_a_demo_page_without_its_policy_fails_the_smoke_test(rig):
    rig.scenario["curl"][SITE] = {"code": 200, "body": "<!doctype html><title>Demo</title>"}
    r = rig.publish()
    assert r.returncode == 1 and "content-security policy" in r.stderr


def adoption_scenario(traffic: bool = True) -> dict:
    q = f"search/issues?q=repo:{REPO}"
    answers = {
        f"GET {R}": {"body": {"stargazers_count": 12, "forks_count": 3, "subscribers_count": 4,
                              "watchers_count": 12, "has_discussions": True}},
        f"GET {q}+is:issue+is:open&per_page=1": {"body": {"total_count": 2}},
        f"GET {q}+is:issue+is:closed&per_page=1": {"body": {"total_count": 5}},
        f"GET {q}+is:pr+is:open&per_page=1": {"body": {"total_count": 1}},
        f"GET {q}+is:pr+is:closed&per_page=1": {"body": {"total_count": 6}},
        "POST graphql": {"body": {"data": {"repository": {"open": {"totalCount": 7}, "closed": {"totalCount": 8}}}}},
    }
    for what, body in (("views", {"count": 40, "uniques": 11}), ("clones", {"count": 9, "uniques": 5}),
                       ("popular/referrers", [{"referrer": "example.net", "count": 20, "uniques": 8}]),
                       ("popular/paths", [{"path": f"/{REPO}", "title": "app", "count": 30, "uniques": 10}])):
        answers[f"GET {R}/traffic/{what}"] = {"body": body} if traffic else {"status": 403, "message": "Forbidden"}
    return {"answers": answers}


def test_the_adoption_report_reads_only_and_counts_everything(rig):
    rig.scenario = adoption_scenario()
    r = rig.run("--repo", REPO, script=SCRIPTS[1])
    assert r.returncode == 0, r.stderr
    for line in ("stars 12, forks 3, watchers 4", "views (14 days)     40 total, 11 unique",
                 "clones (14 days)    9 total, 5 unique", "issues              2 open, 5 closed",
                 "pull requests       1 open, 6 closed", "discussions         7 open, 8 closed",
                 "example.net  20 total, 8 unique", f"/{REPO}  30 total, 10 unique"):
        assert line in r.stdout, line
    for c in rig.calls():
        assert c["method"] == "GET" or (c["endpoint"] == "graphql" and c["fields"]["query"].startswith("query")
                                        and "mutation" not in c["fields"]["query"]), c


def test_the_adoption_report_as_json_and_without_traffic_access(rig):
    rig.scenario = adoption_scenario(traffic=False)
    r = rig.run("--repo", REPO, "--json", script=SCRIPTS[1])
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    assert doc["stars"] == 12 and doc["watchers"] == 4 and doc["issues"] == {"open": 2, "closed": 5}
    assert doc["pull_requests"] == {"open": 1, "closed": 6} and doc["discussions"] == {"open": 7, "closed": 8}
    assert doc["views_14_days"] is None and doc["top_referrers"] is None
    text = rig.run("--repo", REPO, script=SCRIPTS[1]).stdout
    assert "views (14 days)     unavailable (needs push access)" in text


def test_the_maintainer_readme_names_every_flag():
    readme = (ROOT / "tools/maintainer/README.md").read_text("utf-8")
    for flag in ("--repo", "--version", "--expect-sha", "--denylist", "--skip-gate", "--export-names", "--timeout",
                 "--dry-run", "--json", "gh auth status"):
        assert flag in readme, flag
    assert os.sep + "home" + os.sep not in readme

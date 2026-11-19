# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""What a release publishes (release_names.py, which .github/workflows/release.yml runs): the image names come from the
repository lower-cased, so the public repository and a fork publish under different names; a full release
is tagged with its version, <major>.<minor> and latest unless a higher full release is already published, a prerelease
with its version alone; the notes are CHANGELOG.md's section for the version, a prerelease's falling back to the
version it leads to; each image's labels and annotations are the project's, and a build's digest is read from its
metadata file or refused. Also the workflow's shape: the triggers, the least privilege of every job, one native job per
architecture pushing by digest from the tag's commit, GitHub's own actions alone, each pinned by commit, a BuildKit
pinned by digest, and the registry token on standard input alone; its prepare, build and sign-in scripts run in bash
against stub gh, git, docker and python3; and that the demo site's workflow deploys only while the repository variable
says so. Reads files, and runs those scripts in a temporary directory."""
from __future__ import annotations

import io
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import release_names as rn  # noqa: E402  (tools/verify is not a package)

RELEASE = ROOT / ".github" / "workflows" / "release.yml"


@pytest.mark.parametrize("repository, tag, prerelease, etl, version, tags", [
    ("Good-Work-Dojo/Family-Financials", "v1.0.0", False,
     "ghcr.io/good-work-dojo/family-financials-etl", "1.0.0", ("1.0.0", "1.0", "latest")),
    ("Example-Org/Family-Financials-fork", "v1.0.0-rc.1", True,
     "ghcr.io/example-org/family-financials-fork-etl", "1.0.0-rc.1", ("1.0.0-rc.1",)),
    ("Example-Org/Family-Financials-fork", "v1.0.0-rc.1", False,
     "ghcr.io/example-org/family-financials-fork-etl", "1.0.0-rc.1", ("1.0.0-rc.1",)),
    ("Good-Work-Dojo/Family-Financials", "v1.2.0", True,
     "ghcr.io/good-work-dojo/family-financials-etl", "1.2.0", ("1.2.0",)),
    ("Someone/My.Fork_2", "v10.20.30", False, "ghcr.io/someone/my.fork_2-etl", "10.20.30",
     ("10.20.30", "10.20", "latest")),
], ids=["public-release", "fork-candidate","a-pre-release-part-is-a-prerelease-whatever-the-flag",
        "a-release-marked-prerelease", "a-fork"])
def test_the_names_and_tags_come_from_the_repository_lower_cased_and_the_tag(repository, tag, prerelease, etl,
                                                                             version, tags):
    r = rn.names(repository, tag, prerelease)
    assert (r.etl, r.web) == (etl, etl[:-len("-etl")] + "-web")
    assert r.version == version and r.tags == tags
    assert r.etl == r.etl.lower()


def test_a_forks_candidate_never_takes_the_public_names():
    fork, public = rn.names("Example-Org/Family-Financials-fork", "v1.0.0-rc.1"), \
        rn.names("Good-Work-Dojo/Family-Financials", "v1.0.0")
    assert fork.etl == "ghcr.io/example-org/family-financials-fork-etl"
    assert fork.web == "ghcr.io/example-org/family-financials-fork-web"
    assert {fork.etl, fork.web}.isdisjoint({public.etl, public.web})


@pytest.mark.parametrize("tag", ["1.0.0", "v1.0", "v1.0.0.0", "v01.0.0", "v1.0.0-", "v1.0.0+build.1", "v1.0.0-rc 1",
                                 "latest", "v1.0.0\n", ""])
def test_a_tag_of_another_form_is_refused(tag):
    with pytest.raises(ValueError, match="not of the form v1.2.3"):
        rn.names("Good-Work-Dojo/Family-Financials", tag)


@pytest.mark.parametrize("repository", ["Family-Financials", "a/b/c", "-owner/name", "owner/na me", "owner/", ""])
def test_a_repository_of_another_form_is_refused(repository):
    with pytest.raises(ValueError, match="owner/name"):
        rn.names(repository, "v1.0.0")


CHANGELOG = """# Changelog

Intro.

## [Unreleased]

- Something not yet released.

## [1.1.0-rc.2] - 2030-02-01

- A candidate's own notes.

## [1.0.0] - Unreleased

The first release.

### Added

- A thing.

[1.0.0]: https://example.net/releases/tag/v1.0.0
"""


@pytest.mark.parametrize("tag, first", [
    ("v1.0.0", "The first release."), ("v1.0.0-rc.1", "The first release."),
    ("v1.1.0-rc.2", "- A candidate's own notes.")])
def test_the_notes_are_the_changelogs_section_for_the_version_or_the_one_a_prerelease_leads_to(tag, first):
    text = rn.notes(CHANGELOG, tag)
    assert text.splitlines()[0] == first and text.endswith("\n") and not text.endswith("\n\n")
    assert not [line for line in text.splitlines() if line.startswith("## ")] and "https://example.net" not in text, \
        "up to the next section or the link definitions"


def test_the_notes_of_1_0_0_come_from_this_repositorys_changelog_for_the_release_and_its_candidate():
    changelog = (ROOT / "CHANGELOG.md").read_text("utf-8")
    final, candidate = rn.notes(changelog, "v1.0.0"), rn.notes(changelog, "v1.0.0-rc.1")
    assert final == candidate
    assert final.startswith("The first public release.") and "### Added" in final
    assert re.search(r"^## \[1\.0\.0\]$", changelog, re.M), "the heading carries no date, whatever day it is tagged"


@pytest.mark.parametrize("changelog, words", [
    ("# Changelog\n\n## [0.9.0]\n\n- Old.\n", "no section"),
    ("# Changelog\n\n## [1.0.0]\n\n## [0.9.0]\n\n- Old.\n", "is empty"),
])
def test_a_changelog_without_the_versions_section_or_with_an_empty_one_is_refused(changelog, words):
    with pytest.raises(ValueError, match=words):
        rn.notes(changelog, "v1.0.0")


def test_the_command_prints_the_outputs_and_a_bad_argument_is_exit_2(tmp_path, capsys):
    assert rn.main(["names", "Example-Org/Family-Financials-fork", "v1.0.0-rc.1", "--prerelease"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "etl=ghcr.io/example-org/family-financials-fork-etl", "web=ghcr.io/example-org/family-financials-fork-web",
        "version=1.0.0-rc.1", "tags=1.0.0-rc.1"]
    assert rn.main(["names", "Good-Work-Dojo/Family-Financials", "v1.0.0"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "tags=1.0.0 1.0 latest"
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    assert rn.main(["notes", str(tmp_path / "CHANGELOG.md"), "v1.0.0"]) == 0
    assert capsys.readouterr().out.startswith("The first release.\n")
    for argv in (["names", "x", "v1.0.0"], ["names", "a/b", "v1"], ["names", "a/b", "v1.0.0", "--other"],
                 ["notes", str(tmp_path / "missing.md"), "v1.0.0"], ["other"], []):
        assert rn.main(argv) == 2, argv
        assert capsys.readouterr().err.startswith(("release:", "usage:"))


# ---------------------------------------------------------------- the workflow


def workflow() -> dict:
    flow = yaml.safe_load(RELEASE.read_text("utf-8"))
    flow["on"] = flow.pop(True, flow.get("on"))     # YAML 1.1 reads the key `on` as true
    return flow


def test_the_release_runs_on_a_published_release_or_by_hand_for_a_tag():
    on = workflow()["on"]
    assert on["release"] == {"types": ["published"]}
    assert on["workflow_dispatch"]["inputs"]["tag"]["required"] is True
    assert set(on) == {"release", "workflow_dispatch"}


def test_every_job_reads_the_repository_and_only_the_build_and_join_jobs_write_packages():
    flow = workflow()
    assert flow["permissions"] == {}
    jobs = flow["jobs"]
    assert set(jobs) == {"prepare", "build-amd64", "build-arm64", "merge"}
    assert jobs["prepare"]["permissions"] == {"contents": "read"}
    for name in ("build-amd64", "build-arm64", "merge"):
        assert jobs[name]["permissions"] == {"contents": "read", "packages": "write"}, name
    for name, job in jobs.items():
        assert 0 < job["timeout-minutes"] <= 45, name
    assert "contents: write" not in RELEASE.read_text("utf-8")


def join_step() -> dict:
    return next(s for s in workflow()["jobs"]["merge"]["steps"] if "imagetools create" in s.get("run", ""))


def build_steps(job: dict) -> list[dict]:
    return [s for s in job["steps"] if "docker buildx build" in s.get("run", "")]


def test_each_architecture_builds_both_images_natively_and_pushes_them_by_digest():
    jobs = workflow()["jobs"]
    scripts = set()
    for name, runner, platform in (("build-amd64", "ubuntu-24.04", "linux/amd64"),
                                   ("build-arm64", "ubuntu-24.04-arm", "linux/arm64")):
        job = jobs[name]
        assert job["runs-on"] == runner and job["needs"] == "prepare"
        builds = build_steps(job)
        assert [(b["id"], b["env"]["KIND"]) for b in builds] == [("etl", "etl"), ("web", "web")]
        for b in builds:
            image = b["id"]
            assert b["env"] == {
                "KIND": image, "PLATFORM": platform, "IMAGE": f"${{{{ needs.prepare.outputs.{image} }}}}",
                "FF_VERSION": "${{ needs.prepare.outputs.version }}", "FF_SOURCE": "${{ needs.prepare.outputs.source }}",
                "REVISION": "${{ needs.prepare.outputs.sha }}", "CREATED": "${{ needs.prepare.outputs.created }}"}
            run = b["run"]
            assert '--file "deploy/Dockerfile.$KIND" --target "$KIND" --platform "$PLATFORM"' in run
            assert '--output "type=image,name=$IMAGE,push-by-digest=true,name-canonical=true,push=true"' in run
            assert '--build-arg "FF_VERSION=$FF_VERSION" --build-arg "FF_SOURCE=$FF_SOURCE"' in run
            assert not re.search(r"--tag\b| -t \"|--push\b|--load\b", run), "no tag on a per-architecture push"
            assert "tools/verify/release_names.py digest" in run and "--metadata-file" in run
            scripts.add(run)
        assert job["outputs"] == {"etl": "${{ steps.etl.outputs.digest }}", "web": "${{ steps.web.outputs.digest }}"}
    assert len(scripts) == 1, "the four build steps run one script; only their env differs"
    assert jobs["merge"]["needs"] == ["prepare", "build-amd64", "build-arm64"]
    assert "docker buildx imagetools create" in join_step()["run"]
    assert "QEMU" not in RELEASE.read_text("utf-8").upper()


def test_the_names_and_notes_come_from_the_script_and_each_image_is_labelled_as_the_projects():
    prepare = workflow()["jobs"]["prepare"]
    names = next(s for s in prepare["steps"] if s.get("id") == "names")
    assert "tools/verify/release_names.py names" in names["run"]
    assert "tools/verify/release_names.py notes CHANGELOG.md" in names["run"]
    assert names["env"]["REPOSITORY"] == "${{ github.repository }}"
    for job in ("build-amd64", "build-arm64"):
        for b in build_steps(workflow()["jobs"][job]):
            assert ('python3 tools/verify/release_names.py labels "$KIND" "$FF_SOURCE" "$FF_VERSION" "$REVISION" '
                    '"$CREATED"') in b["run"]
            assert '"${meta[@]}"' in b["run"]


def test_every_build_checks_out_the_tags_commit_and_the_index_carries_it_as_its_revision():
    jobs = workflow()["jobs"]
    assert jobs["prepare"]["outputs"]["sha"] == "${{ steps.names.outputs.sha }}"
    assert jobs["prepare"]["outputs"]["created"] == "${{ steps.names.outputs.created }}"
    assert "git rev-parse HEAD" in next(s for s in jobs["prepare"]["steps"] if s.get("id") == "names")["run"]
    for name in ("build-amd64", "build-arm64"):
        (checkout,) = [s for s in jobs[name]["steps"] if s.get("uses", "").startswith("actions/checkout@")]
        assert checkout["with"]["ref"] == "${{ needs.prepare.outputs.sha }}", name
    join = join_step()
    assert join["env"]["REVISION"] == "${{ needs.prepare.outputs.sha }}"
    assert '--annotation "index:org.opencontainers.image.revision=$REVISION"' in join["run"]
    assert "github.sha" not in RELEASE.read_text("utf-8")


@pytest.mark.parametrize("tag, highest, tags", [
    ("v1.0.5", "v1.2.0", ("1.0.5",)),
    ("v1.1.9", "v1.2.0", ("1.1.9",)),
    ("v1.2.0", "v1.2.0", ("1.2.0", "1.2", "latest")),
    ("v1.2.1", "v1.2.0", ("1.2.1", "1.2", "latest")),
    ("v2.0.0", "v1.10.0", ("2.0.0", "2.0", "latest")),
    ("v1.10.0", "v1.9.0", ("1.10.0", "1.10", "latest")),
    ("v1.3.0-rc.1", "v1.2.0", ("1.3.0-rc.1",)),
], ids=["an-older-line", "an-older-minor", "the-highest-itself", "a-higher-patch", "a-new-major",
        "minors-compare-as-numbers", "a-prerelease-alone"])
def test_minor_and_latest_go_only_to_a_release_at_or_above_the_highest_published(tag, highest, tags):
    assert rn.names("Good-Work-Dojo/Family-Financials", tag, highest_full=highest).tags == tags


def test_the_highest_full_release_is_read_from_the_published_tags():
    assert rn.highest(["v1.2.0", "v1.10.0", "v1.9.9", "v2.0.0-rc.1", "nightly", " v1.10.0 ", ""]) == "v1.10.0"
    assert rn.highest(["v1.0.0-rc.1", "other"]) is None and rn.highest([]) is None
    with pytest.raises(ValueError, match="highest full release"):
        rn.names("a/b", "v1.0.0", highest_full="v1.1.0-rc.1")


def test_the_command_takes_the_highest_full_release_and_reads_it_from_standard_input(capsys, monkeypatch):
    assert rn.main(["names", "a/b", "v1.0.5", "--highest-full", "v1.2.0"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "tags=1.0.5"
    assert rn.main(["names", "a/b", "v1.0.0-rc.2", "--highest-full", "v0.9.0", "--prerelease"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "tags=1.0.0-rc.2"
    for argv in (["names", "a/b", "v1.0.0", "--highest-full"], ["names", "a/b", "v1.0.0", "--highest-full", "v1",
                                                                "--prerelease"], ["highest", "extra"]):
        assert rn.main(argv) == 2, argv
        capsys.readouterr()
    monkeypatch.setattr(rn.sys, "stdin", io.StringIO("v1.0.0\nv1.2.0\nv1.3.0-rc.1\n"))
    assert rn.main(["highest"]) == 0 and capsys.readouterr().out == "v1.2.0\n"
    monkeypatch.setattr(rn.sys, "stdin", io.StringIO(""))
    assert rn.main(["highest"]) == 0 and capsys.readouterr().out == ""


# the prepare step's script run in bash: gh answers whether a release is a prerelease (STUB_PRERELEASE) and lists the
# published full releases (STUB_RELEASED); git gives the tag's commit; python3 records each call and runs the real
# interpreter, so the names and notes are the script's own
STEP_GH = """#!/usr/bin/env bash
echo "gh $*" >>"$STUB_LOG"
case "$1 $2" in
  "release view") echo "$STUB_PRERELEASE" ;;
  "release list") printf '%b' "$STUB_RELEASED" ;;
  *) exit 3 ;;
esac
"""
STEP_GIT = """#!/usr/bin/env bash
echo "git $*" >>"$STUB_LOG"
[ "$*" = "rev-parse HEAD" ] && echo 3f3f3f3f3f3f3f3f3f3f3f3f3f3f3f3f3f3f3f3f
[ "$*" = "show -s --format=%ct HEAD" ] && echo 1893456000
exit 0
"""


def run_prepare(tmp_path: Path, event: str, prerelease: str = "", view: str = "", released: str = "",
                tag: str = "v1.0.0") -> tuple[subprocess.CompletedProcess, list[str], dict[str, str]]:
    step = next(s for s in workflow()["jobs"]["prepare"]["steps"] if s.get("id") == "names")
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in (("gh", STEP_GH), ("git", STEP_GIT),
                       ("python3", f'#!/usr/bin/env bash\necho "python3 $*" >>"$STUB_LOG"\nexec {sys.executable} "$@"\n')):
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    log, out, summary = tmp_path / "calls", tmp_path / "output", tmp_path / "summary"
    for f in (log, out, summary):
        f.write_text("")
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "STUB_LOG": str(log), "STUB_PRERELEASE": view, "STUB_RELEASED": released,
           "RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary),
           "EVENT": event, "GH_TOKEN": "sample", "REPOSITORY": "Good-Work-Dojo/Family-Financials",
           "SOURCE": "https://github.com/Good-Work-Dojo/Family-Financials", "TAG": tag, "PRERELEASE": prerelease}
    assert set(step["env"]) <= set(env), "the test gives the step every variable the workflow does"
    r = subprocess.run(["bash", "-c", step["run"]], env=env, cwd=ROOT, capture_output=True, text=True, timeout=60)
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    return r, log.read_text().splitlines(), outputs


@pytest.mark.parametrize("event, prerelease, view, tags", [
    ("release", "true", "", "1.0.0"),
    ("release", "false", "", "1.0.0 1.0 latest"),
    ("workflow_dispatch", "", "true", "1.0.0"),
    ("workflow_dispatch", "", "false", "1.0.0 1.0 latest"),
], ids=["a-published-prerelease", "a-published-release", "by-hand-a-prerelease", "by-hand-a-release"])
def test_the_prepare_step_reads_whether_the_release_is_a_prerelease(tmp_path, event, prerelease, view, tags):
    r, calls, outputs = run_prepare(tmp_path, event, prerelease, view, released="v0.9.0\nv1.0.0\n")
    assert r.returncode == 0, r.stderr
    viewed = [c for c in calls if c.startswith("gh release view")]
    assert viewed == (["gh release view v1.0.0 --repo Good-Work-Dojo/Family-Financials --json isPrerelease --jq "
                       ".isPrerelease"] if event == "workflow_dispatch" else [])
    (named,) = [c for c in calls if " names " in c]
    assert ("--prerelease" in named) == (tags == "1.0.0") and "--highest-full v1.0.0" in named
    assert outputs["tags"] == tags and outputs["version"] == "1.0.0"
    assert outputs["sha"] == "3f" * 20 and "ref" not in outputs
    assert outputs["created"] == "1893456000" and outputs["source"] == \
        "https://github.com/Good-Work-Dojo/Family-Financials"


def test_the_prepare_step_keeps_minor_and_latest_on_the_highest_line(tmp_path):
    r, calls, outputs = run_prepare(tmp_path, "release", "false", released="v1.0.0\nv1.2.0\n")
    assert r.returncode == 0, r.stderr
    assert outputs["tags"] == "1.0.0"
    r, calls, outputs = run_prepare(tmp_path, "release", "false", released="")
    assert r.returncode == 0 and outputs["tags"] == "1.0.0 1.0 latest", "the first release has nothing above it"
    assert not any("--highest-full" in c for c in calls if " names " in c)


def test_every_action_is_pinned_by_commit_with_its_version_beside_it():
    lines = [line for line in RELEASE.read_text("utf-8").splitlines() if "uses:" in line]
    assert lines
    for line in lines:
        assert re.fullmatch(r" +(- )?uses: [a-z-]+/[a-z-]+@[0-9a-f]{40} # v[0-9]+\.[0-9]+\.[0-9]+", line), line


def test_the_demo_site_deploys_only_while_the_repository_variable_says_so_with_the_pages_token_alone():
    flow = yaml.safe_load((ROOT / ".github" / "workflows" / "pages.yml").read_text("utf-8"))
    assert flow["permissions"] == {}
    jobs = flow["jobs"]
    for name in ("build", "deploy"):
        assert jobs[name]["if"] == "vars.FF_PAGES_ENABLED == 'true'", name
    assert jobs["build"]["permissions"] == {"contents": "read"}
    assert jobs["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}


# ---------------------------------------------------------------- GitHub's own actions alone, docker's own commands

SOURCE = "https://github.com/Good-Work-Dojo/Family-Financials"
REVISION = "3f" * 20


def test_every_workflow_uses_githubs_own_actions_alone_each_pinned_by_commit():
    flows = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    assert RELEASE in flows
    for flow in flows:
        for line in flow.read_text("utf-8").splitlines():
            if "uses:" in line:
                assert re.fullmatch(r" +(- )?uses: actions/[a-z-]+@[0-9a-f]{40} # v[0-9]+\.[0-9]+\.[0-9]+", line), \
                    f"{flow.name}: {line}"


def test_the_labels_are_the_ones_the_project_declares_for_each_image():
    created = "1893456000"    # 2030-01-01T00:00:00Z
    common = {
        "org.opencontainers.image.vendor": "Family Financials",
        "org.opencontainers.image.licenses": "AGPL-3.0-only",
        "org.opencontainers.image.source": SOURCE,
        "org.opencontainers.image.url": SOURCE,
        "org.opencontainers.image.documentation": SOURCE,
        "org.opencontainers.image.version": "1.0.0-rc.1",
        "org.opencontainers.image.revision": REVISION,
        "org.opencontainers.image.created": "2030-01-01T00:00:00Z",
    }
    assert rn.labels("etl", SOURCE, "1.0.0-rc.1", REVISION, created) == {
        "org.opencontainers.image.title": "Family Financials ETL",
        "org.opencontainers.image.description":
            "Family Financials: the offline ingest, the family store, the refresh worker and the scheduler",
        **common}
    assert rn.labels("web", SOURCE, "1.0.0-rc.1", REVISION, created) == {
        "org.opencontainers.image.title": "Family Financials web",
        "org.opencontainers.image.description":
            "Family Financials: the dashboard and its sign-in page, served by Caddy",
        **common}


def test_the_index_carries_the_same_keys_as_each_image_but_its_creation_time():
    meta = rn.labels("etl", SOURCE, "1.0.0", REVISION, "0")
    run = join_step()["run"]
    index = set(re.findall(r'--annotation "index:(org\.opencontainers\.image\.[a-z]+)=', run))
    assert index == set(meta) - {"org.opencontainers.image.created"}
    for key in ("org.opencontainers.image.vendor", "org.opencontainers.image.licenses"):
        assert f'--annotation "index:{key}={meta[key]}"' in run, key
    for name in ("etl", "web"):
        title, description = rn.IMAGES[name]
        assert f'"{title}"' in run and f'"{description}"' in run, name


def test_the_build_arguments_give_each_key_as_a_label_and_a_manifest_annotation():
    meta = rn.labels("web", SOURCE, "1.0.0", REVISION, "1893456000")
    args = rn.build_arguments(meta)
    assert args == [f"--label={k}={v}" for k, v in meta.items()] + \
        [f"--annotation=manifest:{k}={v}" for k, v in meta.items()]
    assert len(args) == 20 and all("\n" not in a for a in args)


@pytest.mark.parametrize("image, source, version, revision, created, words", [
    ("api", SOURCE, "1.0.0", REVISION, "0", "not one of etl, web"),
    ("etl", "http://github.com/a/b", "1.0.0", REVISION, "0", "https://host/owner/name"),
    ("etl", "https://github.com/a/b c", "1.0.0", REVISION, "0", "https://host/owner/name"),
    ("etl", "https://github.com/a/b\n--push", "1.0.0", REVISION, "0", "https://host/owner/name"),
    ("etl", SOURCE, "v1.0.0", REVISION, "0", "version"),
    ("etl", SOURCE, "1.0.0", "3f" * 19, "0", "40 hex"),
    ("etl", SOURCE, "1.0.0", "3F" * 20, "0", "40 hex"),
    ("etl", SOURCE, "1.0.0", REVISION, "-1", "seconds since the epoch"),
    ("etl", SOURCE, "1.0.0", REVISION, "2030-01-01", "seconds since the epoch"),
    ("etl", SOURCE, "1.0.0", REVISION, "", "seconds since the epoch"),
])
def test_labels_from_an_argument_of_another_form_are_refused(image, source, version, revision, created, words):
    with pytest.raises(ValueError, match=words):
        rn.labels(image, source, version, revision, created)


def test_the_digest_is_read_from_the_build_metadata_and_anything_else_is_refused():
    good = "sha256:" + "ab" * 32
    assert rn.digest(f'{{"containerimage.digest": "{good}", "image.name": "x"}}') == good
    for text in ("", "[]", "{}", '{"containerimage.digest": null}', f'{{"containerimage.digest": "{good}x"}}',
                 '{"containerimage.digest": "sha256:abc"}', f'{{"containerimage.digest": "{good.upper()}"}}',
                 f'{{"containerimage.config.digest": "{good}"}}'):
        with pytest.raises(ValueError, match="no image digest"):
            rn.digest(text)


def test_the_command_prints_the_build_arguments_one_a_line_and_the_digest(tmp_path, capsys):
    assert rn.main(["labels", "etl", SOURCE, "1.0.0", REVISION, "1893456000"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == rn.build_arguments(rn.labels("etl", SOURCE, "1.0.0", REVISION, "1893456000"))
    good = "sha256:" + "cd" * 32
    (tmp_path / "m.json").write_text(f'{{"containerimage.digest": "{good}"}}')
    assert rn.main(["digest", str(tmp_path / "m.json")]) == 0 and capsys.readouterr().out == good + "\n"
    (tmp_path / "bad.json").write_text("{}")
    for argv in (["digest", str(tmp_path / "bad.json")], ["digest", str(tmp_path / "missing.json")],
                 ["labels", "etl", SOURCE, "1.0.0", REVISION], ["labels", "etl", SOURCE, "1.0.0", "x", "0"]):
        assert rn.main(argv) == 2, argv
        assert capsys.readouterr().err.startswith(("release:", "usage:"))


def test_the_builder_runs_buildkit_pinned_by_digest_in_its_own_container():
    for name in ("build-amd64", "build-arm64"):
        steps = workflow()["jobs"][name]["steps"]
        (builder,) = [s for s in steps if "docker buildx create" in s.get("run", "")]
        assert re.fullmatch(r"moby/buildkit@sha256:[0-9a-f]{64}", builder["env"]["BUILDKIT"])
        assert ('docker buildx create --name release --driver docker-container --driver-opt "image=$BUILDKIT" --use'
                in builder["run"])
        assert steps.index(builder) < steps.index(build_steps(workflow()["jobs"][name])[0])
    assert re.search(r"BUILDKIT: moby/buildkit@sha256:[0-9a-f]{64} # v[0-9]+\.[0-9]+\.[0-9]+\n",
                     RELEASE.read_text("utf-8"))


def test_every_registry_job_signs_in_with_the_token_on_standard_input_and_signs_out_at_the_end():
    for name in ("build-amd64", "build-arm64", "merge"):
        steps = workflow()["jobs"][name]["steps"]
        logins = [s for s in steps if "docker login" in s.get("run", "")]
        assert len(logins) == 1, name
        (login,) = logins
        assert login["env"] == {"REGISTRY_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
        assert login["run"].splitlines()[-1] == \
            'printf \'%s\' "$REGISTRY_TOKEN" | docker login ghcr.io --username "$GITHUB_ACTOR" --password-stdin'
        assert steps[-1] == {"name": "Sign out of the registry", "if": "always()", "run": "docker logout ghcr.io"}
        first_docker = next(i for i, s in enumerate(steps) if "docker " in s.get("run", "") and
                            "docker buildx create" not in s["run"] and "docker buildx inspect" not in s["run"])
        assert steps.index(login) == first_docker, name
    text = RELEASE.read_text("utf-8")
    assert text.count("secrets.GITHUB_TOKEN") == 3 and "--password " not in text and "-p " not in text


def test_every_script_reads_its_inputs_from_its_environment_alone():
    for name, job in workflow()["jobs"].items():
        for step in job["steps"]:
            if "run" in step:
                assert "${{" not in step["run"], f"{name}: {step.get('name') or step.get('id')}"


# the build step's script run in bash: docker records its arguments and writes the metadata file the build would;
# python3 is the real interpreter, so the labels and the digest are the script's own
STEP_DOCKER = """#!/usr/bin/env bash
printf '%s\\n' "$@" >"$STUB_ARGS"
prev=
for a in "$@"; do
  if [ "$prev" = --metadata-file ]; then printf '%s' "$STUB_METADATA" >"$a"; fi
  prev="$a"
done
"""


def run_build(tmp_path: Path, step: dict, metadata: str) -> tuple[subprocess.CompletedProcess, list[str], str]:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "docker").write_text(STEP_DOCKER)
    (bindir / "docker").chmod(0o755)
    args, out = tmp_path / "args", tmp_path / "output"
    args.write_text("")
    out.write_text("")
    values = {"IMAGE": f"ghcr.io/good-work-dojo/family-financials-{step['env']['KIND']}", "FF_VERSION": "1.0.0-rc.1",
              "FF_SOURCE": SOURCE, "REVISION": REVISION, "CREATED": "1893456000"}
    env = {"PATH": f"{bindir}:{Path(sys.executable).parent}:/usr/bin:/bin", "STUB_ARGS": str(args),
           "STUB_METADATA": metadata, "RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(out),
           **{k: values.get(k, v) for k, v in step["env"].items()}}
    r = subprocess.run(["bash", "-c", step["run"]], env=env, cwd=ROOT, capture_output=True, text=True, timeout=60)
    return r, args.read_text().splitlines(), out.read_text()


@pytest.mark.parametrize("job, step_id", [("build-amd64", "etl"), ("build-arm64", "web")])
def test_the_build_step_pushes_by_digest_with_the_labels_and_gives_the_digest(tmp_path, job, step_id):
    step = next(s for s in build_steps(workflow()["jobs"][job]) if s["id"] == step_id)
    good = "sha256:" + "ef" * 32
    r, argv, out = run_build(tmp_path, step, f'{{"containerimage.digest": "{good}"}}')
    assert r.returncode == 0, r.stderr
    image = f"ghcr.io/good-work-dojo/family-financials-{step_id}"
    meta = rn.build_arguments(rn.labels(step_id, SOURCE, "1.0.0-rc.1", REVISION, "1893456000"))
    assert argv == ["buildx", "build", "--file", f"deploy/Dockerfile.{step_id}", "--target", step_id,
                    "--platform", step["env"]["PLATFORM"], "--build-arg", "FF_VERSION=1.0.0-rc.1",
                    "--build-arg", f"FF_SOURCE={SOURCE}", *meta,
                    "--output", f"type=image,name={image},push-by-digest=true,name-canonical=true,push=true",
                    "--metadata-file", str(tmp_path / f"{step_id}.json"), "."]
    assert "org.opencontainers.image.created=2030-01-01T00:00:00Z" in " ".join(argv)
    assert out == f"digest={good}\n"


@pytest.mark.parametrize("metadata", ["{}", '{"containerimage.digest": "sha256:short"}', "not json"])
def test_the_build_step_fails_without_a_digest_and_gives_none(tmp_path, metadata):
    step = build_steps(workflow()["jobs"]["build-amd64"])[0]
    r, argv, out = run_build(tmp_path, step, metadata)
    assert r.returncode != 0 and "no image digest" in r.stderr
    assert argv[:2] == ["buildx", "build"] and out == ""


def test_the_sign_in_passes_the_token_on_standard_input_and_never_as_an_argument(tmp_path):
    (login,) = [s for s in workflow()["jobs"]["build-arm64"]["steps"] if "docker login" in s.get("run", "")]
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text('#!/usr/bin/env bash\nprintf \'%s\\n\' "$@" >"$STUB_ARGS"\ncat >"$STUB_STDIN"\n')
    (bindir / "docker").chmod(0o755)
    token = "sample-token-not-a-secret"
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "STUB_ARGS": str(tmp_path / "args"),
           "STUB_STDIN": str(tmp_path / "stdin"), "REGISTRY_TOKEN": token, "GITHUB_ACTOR": "example-actor"}
    r = subprocess.run(["bash", "-c", login["run"]], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "args").read_text().splitlines() == [
        "login", "ghcr.io", "--username", "example-actor", "--password-stdin"]
    assert (tmp_path / "stdin").read_text() == token
    assert token not in r.stdout + r.stderr

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The harness's names: harness_names.py (the Python scripts') and harness.sh (the shell scripts') read the same
settings file, the environment and the same defaults alike, refuse the same shapes, and stack.sh names its project,
its containers, its label and its compose wrapper from them. Runs in temporary trees with stub programs; nothing
reaches Docker."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import harness_names as hn  # noqa: E402  (tools/verify is not a package)

FIELDS = {"FF_HARNESS_IMAGE_PREFIX": "image_prefix", "FF_HARNESS_TEST_TAG": "test_tag", "FF_HARNESS_LABEL": "label",
          "FF_HARNESS_PROJECT_PREFIX": "project_prefix", "FF_HARNESS_SCRATCH": "scratch",
          "FF_HARNESS_COMPOSE": "compose", "FF_HARNESS_COMPOSE_PORT_VAR": "compose_port_var"}
IMAGES = {"HARNESS_ETL_TEST": "etl_test", "HARNESS_WEB_TEST": "web_test", "HARNESS_ETL_RT": "etl_rt",
          "HARNESS_WEB_RT": "web_rt"}
# invented names a team might use, in each form a line may take
SAMPLE = ("# a sample team's names\n\nFF_HARNESS_IMAGE_PREFIX=sample\nFF_HARNESS_TEST_TAG='agent'\n"
          "FF_HARNESS_LABEL=team=sample\nFF_HARNESS_PROJECT_PREFIX=\"sample\"\nFF_HARNESS_SCRATCH=sample-verify\n"
          "FF_HARNESS_COMPOSE=tools/team/compose.sh\nFF_HARNESS_COMPOSE_PORT_VAR=TEAM_PORT\n")


def tree(tmp_path: Path, settings: str | None = None) -> Path:
    """A repository holding the harness's two readers and, given its text, a settings file."""
    root = tmp_path / "repo"
    (root / "tools" / "verify").mkdir(parents=True)
    for name in ("harness.sh", "harness_names.py"):
        shutil.copy(HERE / name, root / "tools" / "verify" / name)
    if settings is not None:
        (root / "tools" / "private").mkdir()
        (root / "tools" / "private" / "harness.env").write_text(settings)
    return root


def clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FF_", "HARNESS_"))}
    env.update(extra)
    return env


def shell(root: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    """harness.sh sourced in bash, each name it sets printed as NAME=value."""
    names = " ".join([*FIELDS, *IMAGES])
    script = f'W="$1"; set -u; . "$W/tools/verify/harness.sh"; for v in {names}; do printf "%s=%s\\n" "$v" "${{!v}}"; done'
    return subprocess.run(["bash", "-c", script, "harness", str(root)], env=env, capture_output=True, text=True)


def python(root: Path, env: dict[str, str]) -> dict[str, str]:
    h = hn.load(root, env)
    return {**{k: getattr(h, f) for k, f in FIELDS.items()}, **{k: getattr(h, f) for k, f in IMAGES.items()}}


def agree(root: Path, env: dict[str, str]) -> dict[str, str]:
    """The names both readers give, failing the test when they differ."""
    r = shell(root, env)
    assert r.returncode == 0, r.stderr
    got = dict(line.split("=", 1) for line in r.stdout.splitlines())
    assert got == python(root, env)
    return got


def test_without_a_settings_file_the_names_are_the_defaults(tmp_path):
    assert agree(tree(tmp_path), clean_env()) == {
        "FF_HARNESS_IMAGE_PREFIX": "family-financials", "FF_HARNESS_TEST_TAG": "test",
        "FF_HARNESS_LABEL": "family-financials.test=1", "FF_HARNESS_PROJECT_PREFIX": "family-financials-test",
        "FF_HARNESS_SCRATCH": "ff-verify", "FF_HARNESS_COMPOSE": "", "FF_HARNESS_COMPOSE_PORT_VAR": "",
        "HARNESS_ETL_TEST": "family-financials-etl:test", "HARNESS_WEB_TEST": "family-financials-web:test",
        "HARNESS_ETL_RT": "family-financials-etl:rt", "HARNESS_WEB_RT": "family-financials-web:rt"}


def test_a_settings_file_names_everything_and_wins_over_the_environment(tmp_path):
    env = clean_env(FF_HARNESS_LABEL="other=1", FF_HARNESS_SCRATCH="other-verify")
    got = agree(tree(tmp_path, SAMPLE), env)
    assert got == {
        "FF_HARNESS_IMAGE_PREFIX": "sample", "FF_HARNESS_TEST_TAG": "agent", "FF_HARNESS_LABEL": "team=sample",
        "FF_HARNESS_PROJECT_PREFIX": "sample", "FF_HARNESS_SCRATCH": "sample-verify",
        "FF_HARNESS_COMPOSE": "tools/team/compose.sh", "FF_HARNESS_COMPOSE_PORT_VAR": "TEAM_PORT",
        "HARNESS_ETL_TEST": "sample-etl:agent", "HARNESS_WEB_TEST": "sample-web:agent",
        "HARNESS_ETL_RT": "sample-etl:rt", "HARNESS_WEB_RT": "sample-web:rt"}


def test_without_a_settings_file_the_environment_names_and_an_empty_value_is_the_default(tmp_path):
    got = agree(tree(tmp_path), clean_env(FF_HARNESS_IMAGE_PREFIX="mine", FF_HARNESS_SCRATCH=""))
    assert (got["HARNESS_ETL_RT"], got["FF_HARNESS_SCRATCH"]) == ("mine-etl:rt", "ff-verify")
    assert agree(tree(tmp_path / "b"), clean_env(FF_HARNESS_IMAGE_PREFIX=""))["HARNESS_ETL_RT"] == \
        "family-financials-etl:rt"


def _sample_with(key: str, line: str | None) -> str:
    """SAMPLE with the line that sets `key` replaced by `line` (None: the line removed)"""
    lines = [line if ln.startswith(f"{key}=") else ln for ln in SAMPLE.splitlines()]
    return "".join(f"{ln}\n" for ln in lines if ln is not None)


@pytest.mark.parametrize("key", list(FIELDS))
@pytest.mark.parametrize("how", ["removed", "emptied"])
def test_a_settings_file_that_leaves_a_setting_out_or_empty_is_refused_by_both_whatever_the_environment(
        tmp_path, key, how):
    """A lost line in the file never falls back to the environment or a default: on a host whose label and scratch
    root keep its containers and real data apart, that fallback would escape both."""
    root = tree(tmp_path, _sample_with(key, None if how == "removed" else f"{key}="))
    assert (f"\n{key}=" in (root / "tools" / "private" / "harness.env").read_text()) is (how == "emptied")
    env = clean_env(**{key: "kept-from-the-environment"})
    r = shell(root, env)
    assert r.returncode == 2 and f"sets no {key}" in r.stderr and r.stdout == ""
    with pytest.raises(ValueError, match=f"sets no {key}"):
        hn.load(root, env)


@pytest.mark.parametrize("kind", ["a link to a full file", "a dangling link", "a directory"])
def test_a_settings_file_that_is_a_link_or_no_plain_file_is_refused_by_both_whatever_the_environment(tmp_path, kind):
    """The settings are read only from a plain file: a link, dangling or not, could be retargeted or lost without the
    tree changing, and anything else that is no file would otherwise fall back to the environment and the defaults."""
    root = tree(tmp_path)
    settings = root / "tools" / "private" / "harness.env"
    settings.parent.mkdir()
    if kind == "a link to a full file":
        (tmp_path / "elsewhere.env").write_text(SAMPLE)
        settings.symlink_to(tmp_path / "elsewhere.env")
    elif kind == "a dangling link":
        settings.symlink_to(tmp_path / "missing.env")
    else:
        settings.mkdir()
    env = clean_env(FF_HARNESS_LABEL="kept=from-the-environment")
    r = shell(root, env)
    assert r.returncode == 2 and "tools/private/harness.env" in r.stderr and "plain file" in r.stderr \
        and r.stdout == "", r.stderr
    with pytest.raises(ValueError, match="plain file"):
        hn.load(root, env)


@pytest.mark.parametrize("key, value", [
    ("FF_HARNESS_LABEL", "no-value"), ("FF_HARNESS_LABEL", "a=b=c"), ("FF_HARNESS_IMAGE_PREFIX", "Upper"),
    ("FF_HARNESS_IMAGE_PREFIX", "-dash"), ("FF_HARNESS_TEST_TAG", "rt"), ("FF_HARNESS_TEST_TAG", ".dot"),
    ("FF_HARNESS_PROJECT_PREFIX", "a.b"), ("FF_HARNESS_SCRATCH", ".."), ("FF_HARNESS_SCRATCH", "a/b"),
    ("FF_HARNESS_COMPOSE", "a:b"), ("FF_HARNESS_COMPOSE_PORT_VAR", "lower")])
@pytest.mark.parametrize("where", ["file", "environment"])
def test_a_setting_of_another_shape_is_refused_by_both(tmp_path, key, value, where):
    settings = _sample_with(key, f"{key}='{value}'") if where == "file" else None
    env = clean_env(**({key: value} if where == "environment" else {}))
    root = tree(tmp_path, settings)
    r = shell(root, env)
    assert r.returncode == 2 and key in r.stderr and r.stdout == ""
    with pytest.raises(ValueError, match=key):
        hn.load(root, env)


@pytest.mark.parametrize("line", ["FF_HARNESS_LABEL=$(id)", "FF_HARNESS_LABEL=`id`", "export FF_HARNESS_LABEL=a=b",
                                  "FF_HARNESS_LABEL = a=b", "FF_HARNESS_LABEL=a=b # note", "FF_HARNESS_LABEL='a=b",
                                  "FF_HARNESS_LABLE=a=b", "OTHER=1"])
def test_python_refuses_a_line_a_shell_could_read_otherwise_or_an_unknown_name(tmp_path, line):
    with pytest.raises(ValueError, match="line 2"):
        hn.load(tree(tmp_path, f"# names\n{line}\n"), clean_env())


# ------------------------------------------------------------------ stack.sh's names, before anything starts

# docker: records each call; answers `image inspect` as present and lists nothing
DOCKER = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_LOG"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
"""
# ff: records what stack.sh hands it (its compose prefix, images, port variables, those STUB_KEEP names and the
# docker it puts first on PATH), then fails `demo`, so the run goes straight to its cleanup
FF = """#!/usr/bin/env python3
import json, os, shutil, sys
keep = ["FF_COMPOSE", "FF_COMPOSE_FILE", "FF_IMAGE_ETL", "FF_IMAGE_WEB", "FF_STACK_PORT"]
keep += [k for k in os.environ["STUB_KEEP"].split(",") if k]
seen = {k: os.environ.get(k) for k in keep}
seen["docker"] = open(shutil.which("docker")).read()
with open(os.environ["STUB_LOG"] + ".ff", "w") as fh:
    json.dump(seen, fh)
sys.exit(1)
"""


def stack_dry_run(root: Path, tmp_path: Path, keep: tuple[str, ...] = (), **env: str
                  ) -> tuple[subprocess.CompletedProcess, dict, list]:
    """tools/verify/stack.sh copied into `root`, with its ff replaced by the recorder above and a stub docker first on
    PATH: what ff was handed (the variables `keep` names too), and every docker call stack.sh made (the image checks,
    the left-over check, the cleanup)."""
    for name in ("stack.sh", "harness.sh"):
        shutil.copy(HERE / name, root / "tools" / "verify" / name)
    (root / "ff").write_text(FF)
    (root / "ff").chmod(0o755)
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "docker").write_text(DOCKER)
    (stub / "docker").chmod(0o755)
    log = tmp_path / "docker.calls"
    full = {k: v for k, v in clean_env().items() if k not in keep}
    full.update(PATH=f"{stub}:{os.environ['PATH']}", STUB_LOG=str(log), STUB_KEEP=",".join(keep), **env)
    r = subprocess.run(["bash", str(root / "tools" / "verify" / "stack.sh"), str(tmp_path / "run")], env=full,
                       capture_output=True, text=True)
    seen = json.loads(Path(str(log) + ".ff").read_text()) if Path(str(log) + ".ff").exists() else {}
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return r, seen, calls


def test_stack_sh_names_its_project_containers_and_label_from_the_defaults(tmp_path):
    root = tree(tmp_path)
    r, seen, calls = stack_dry_run(root, tmp_path)
    assert r.returncode == 1 and "./ff demo failed" in r.stderr
    install = tmp_path / "run" / "install"
    assert seen["FF_IMAGE_ETL"] == "family-financials-etl:rt" and seen["FF_IMAGE_WEB"] == "family-financials-web:rt"
    assert seen["FF_COMPOSE"] == (f"docker compose -p family-financials-test-stack --project-directory {install} "
                                  f"-f {install}/compose.yaml")
    assert seen["FF_STACK_PORT"] is None and seen["FF_COMPOSE_FILE"] is None
    assert "run --label family-financials.test=1 " in seen["docker"]
    assert ["image", "inspect", "family-financials-etl:rt"] in calls
    assert ["ps", "-aq", "--filter", "label=com.docker.compose.project=family-financials-test-stack"] in calls
    assert ["ps", "-aq", "--filter", "label=family-financials.test=1", "--filter",
            "name=family-financials-test-stack-"] in calls
    assert ["network", "ls", "-q", "--filter", "label=family-financials.test=1", "--filter",
            "name=family-financials-test-stack-"] in calls
    (removed,) = [c for c in calls if c[:2] == ["rm", "-f"]]
    assert [n.rsplit("-", 1)[0] for n in removed[2:]] == ["family-financials-test-stack-browser",
                                                          "family-financials-test-stack-llm"]
    assert "127.0.0.1:18216" in r.stdout


def test_stack_sh_runs_compose_through_the_wrapper_the_settings_name_with_its_port(tmp_path):
    root = tree(tmp_path, SAMPLE)
    wrapper = root / "tools" / "team" / "compose.sh"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\nexit 0\n")
    wrapper.chmod(0o755)
    r, seen, calls = stack_dry_run(root, tmp_path, ("TEAM_PORT",), TEAM_PORT="18217")
    assert r.returncode == 1 and "./ff demo failed" in r.stderr
    install = tmp_path / "run" / "install"
    assert seen == {"FF_COMPOSE": f"{wrapper} stack", "FF_COMPOSE_FILE": f"{install}/compose.yaml",
                    "FF_IMAGE_ETL": "sample-etl:rt", "FF_IMAGE_WEB": "sample-web:rt", "FF_STACK_PORT": "18217",
                    "TEAM_PORT": "18217", "docker": seen["docker"]}
    assert "run --label team=sample " in seen["docker"]
    assert ["ps", "-aq", "--filter", "label=team=sample", "--filter", "name=sample-stack-"] in calls
    assert "(tools/team/compose.sh, 127.0.0.1:18217, images sample-etl:rt sample-web:rt)" in r.stdout


def test_stack_sh_gives_a_wrapper_the_stack_port_when_its_own_variable_is_unset(tmp_path):
    root = tree(tmp_path, SAMPLE)
    wrapper = root / "tools" / "team" / "compose.sh"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\nexit 0\n")
    wrapper.chmod(0o755)
    r, seen, _ = stack_dry_run(root, tmp_path, ("TEAM_PORT",), FF_STACK_PORT="18203")
    assert (seen["FF_STACK_PORT"], seen["TEAM_PORT"]) == ("18203", "18203")


def test_stack_sh_refuses_a_wrapper_that_is_not_there(tmp_path):
    r, seen, calls = stack_dry_run(tree(tmp_path, SAMPLE), tmp_path)
    assert r.returncode == 2 and "FF_HARNESS_COMPOSE" in r.stderr
    assert seen == {}
    assert not any(c[:2] == ["rm", "-f"] for c in calls)

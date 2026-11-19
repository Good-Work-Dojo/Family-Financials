# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The `ff` script, run as a person runs it (copied alone into an install directory) with a stub `docker` first on
PATH, and stubs for `id`, `uname`, `getenforce`, `ip` and `ss`.

Its refusals, each one line and nothing run: root, the install directory equal to the home directory or /, a Windows
drive, a path Docker cannot mount, a 32-bit ARM machine, no Docker, a Compose older than the pinned minimum, a Docker
it cannot reach. Its notes, one line each, for rootless Docker, a remapped user namespace and SELinux enforcing. Its
dispatch, by exact argv: the ETL image's `python -m etl` as the invoking user with the install at /ff and no network
(init alone has one), the image from the environment, else .env, else compose.yaml's default; compose always on the
install directory (--project-directory), or the whole prefix FF_COMPOSE gives. `up` runs preflight in the image first
and never compose when it refuses. `upgrade`, with the stub running the real `python -m etl` for the image's
commands: a failed pull or a new release that refuses the config puts the previous settings back and restarts
nothing; a failure after the new images started keeps them and says how to go back."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from conftest import ROOT
from ff_helpers import AS_USER_MAIN, USER

from etl import stack_cli

FF = ROOT / "ff"
# the ETL image ff runs when nothing names one, and the one `upgrade --to 9.9.9` moves the settings to
DEFAULT_ETL = stack_cli.DEFAULT_IMAGES["FF_IMAGE_ETL"]
NEW_ETL = stack_cli.retag(DEFAULT_ETL, "9.9.9")

DOCKER = r'''#!/usr/bin/env python3
"""The stub docker: records each call as a JSON line in STUB_LOG and plays what ff asks of Docker."""
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
env = os.environ
if args[:2] == ["compose", "version"]:
    print(env.get("STUB_COMPOSE_VERSION", "2.29.1"))
    sys.exit(0)
if args[:1] == ["info"]:
    if env.get("STUB_INFO_FAIL"):
        sys.exit(1)
    print(env.get("STUB_SECURITY", '["name=seccomp,profile=builtin"]'))
    sys.exit(0)
if args[:1] == ["compose"]:
    with open(os.environ["STUB_LOG"] + ".images", "a") as fh:
        fh.write(f"{env.get('FF_IMAGE_ETL', '-')} {env.get('FF_IMAGE_WEB', '-')}\n")
    rest = [a for a in args[1:]]
    while rest and rest[0] in ("--project-directory", "-f"):
        rest = rest[2:]
    sub = rest[0] if rest else ""
    if sub == "ps":
        print(env.get("STUB_PS", ""))
    if sub == env.get("STUB_COMPOSE_FAIL"):
        sys.exit(1)
    sys.exit(0)
if args[:1] == ["run"]:
    i, opts = 1, {"-e": [], "-v": []}
    while args[i].startswith("-"):
        if args[i] in ("--user", "--network", "-v", "-w", "-e"):
            if args[i] in ("-e", "-v"):
                opts[args[i]].append(args[i + 1])
            else:
                opts[args[i]] = args[i + 1]
            i += 2
        else:
            i += 1
    image, command = args[i], args[i + 1:]
    etl = command[3:] if command[:3] == ["python", "-m", "etl"] else []
    if etl and env.get("STUB_FAIL") == f"{image} {etl[0]}":
        sys.exit(int(env.get("STUB_FAIL_RC", "1")))
    if not env.get("STUB_REAL_ETL"):
        print(env.get("STUB_RUN_OUT", ""), end="")
        sys.exit(int(env.get("STUB_RUN_RC", "0")))
    # the image's python -m etl, run by the suite's own interpreter with the install's real path as FF_HOME, as the
    # invoking user the tests stand in (etl/install.py's two seams)
    host = next(v.rsplit(":/ff:", 1)[0] for v in opts["-v"] if v.rsplit(":", 1)[0].endswith(":/ff"))
    child = {k: v for k, v in env.items() if not k.startswith("FF_")}
    for item in opts["-e"]:
        key, eq, value = item.partition("=")
        if eq:
            child[key] = value
        elif key in env:
            child[key] = env[key]
    child["FF_HOME"] = host
    workdir = host + opts["-w"][len("/ff"):]
    sys.exit(subprocess.run([sys.executable, "-c", env["STUB_BOOT"], *etl], env=child, cwd=workdir).returncode)
sys.exit(0)
'''


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(body)
    path.chmod(0o755)


@pytest.fixture
def rig(tmp_path):
    """An install directory holding only ff, a home directory beside it, and the stubs."""
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    shutil.copyfile(FF, install_dir / "ff")
    (install_dir / "ff").chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "docker", DOCKER.replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1))
    _stub(bin_dir, "id", '#!/bin/sh\ncase "$1" in -u) echo "${STUB_UID:-1000}" ;; -g) echo 1000 ;; esac\n')
    _stub(bin_dir, "uname", '#!/bin/sh\necho "${STUB_ARCH:-x86_64}"\n')
    _stub(bin_dir, "ip", '#!/bin/sh\necho "2: eth0    inet 198.51.100.7/24 scope global eth0"\n')
    _stub(bin_dir, "ss", '#!/bin/sh\necho "LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*"\n')
    (tmp_path / "person").mkdir()
    return Rig(install_dir, bin_dir, tmp_path)


class Rig:
    def __init__(self, home: Path, bin_dir: Path, tmp: Path):
        self.home, self.bin, self.tmp = home, bin_dir, tmp
        self.log = tmp / "docker.log"

    def run(self, *args: str, cwd: Path | None = None, stdin: str | None = None, **env: str):
        self.log.unlink(missing_ok=True)
        full = {"PATH": f"{self.bin}:/usr/local/bin:/usr/bin:/bin", "HOME": str(self.tmp / "person"),
                "STUB_LOG": str(self.log), "STUB_AS_UID": str(USER), "STUB_BOOT": AS_USER_MAIN,
                "PYTHONPATH": str(ROOT), **env}
        return subprocess.run(["sh", str(self.home / "ff"), *args], capture_output=True, text=True, env=full,
                              cwd=cwd or self.home, input=stdin if stdin is not None else "")

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def runs(self) -> list[list[str]]:
        return [c for c in self.calls() if c[:1] == ["run"]]

    def composes(self) -> list[list[str]]:
        return [c for c in self.calls() if c[:1] == ["compose"] and c[1:2] != ["version"]]


def _etl_argv(home: Path, *args: str, network: str = "none", image: str = DEFAULT_ETL, workdir: str = "/ff",
              mount: str = "rw", answers: Path | None = None):
    extra = ["-v", f"{answers}:/run/ff-answers.yaml:ro"] if answers else []
    return ["run", "--rm", "-i", "--user", "1000:1000", "--network", network, "-v", f"{home}:/ff:{mount}", *extra,
            "-w", workdir, "-e", "FF_HOME=/ff", "-e", f"FF_HOST_HOME={home}", "-e", "FF_IMAGE_ETL", "-e",
            "FF_IMAGE_WEB", "-e", "FF_HOST_ADDRESSES", "-e", "FF_HOST_LISTENERS", "-e", "FF_STACK_PS", image, "python",
            "-m", "etl", *args]


# ------------------------------------------------------------------ the script itself

def test_the_script_is_small_posix_and_holds_no_test_seam():
    text = FF.read_text("utf-8")
    assert len(text.splitlines()) <= 150
    assert text.startswith("#!/bin/sh\n") and "set -eu" in text
    assert "FF_SIMPLEFIN_TEST_ORIGIN" not in text and "TEST_ORIGIN" not in text
    assert subprocess.run(["sh", "-n", str(FF)]).returncode == 0
    assert os.stat(FF).st_mode & stat.S_IXUSR


def test_nothing_in_the_script_runs_compose_without_the_install_directory():
    lines = [line for line in FF.read_text("utf-8").splitlines() if not line.lstrip().startswith("#")]
    composes = [line for line in lines if re.match(r"\s*(\S+=\S+\s+)*docker compose\b", line)
                or re.search(r"\$\(\s*docker compose\b", line) and "version" not in line]
    assert composes == ['    FF_IMAGE_ETL="$ETL" FF_IMAGE_WEB="$WEB" docker compose --project-directory "$FF_HOME" -f '
                        '"$FF_HOME/compose.yaml" "$@"']
    # the one other way compose runs is FF_COMPOSE's whole prefix, in the same function, with the same images
    assert [line.strip() for line in lines if "$FF_COMPOSE" in line] == [
        'FF_IMAGE_ETL="$ETL" FF_IMAGE_WEB="$WEB" $FF_COMPOSE "$@"']


def test_the_compose_minimum_is_stated_once():
    text = FF.read_text("utf-8")
    assert len(re.findall(r"2\.20", text)) == 1 and "COMPOSE_MIN=2.20" in text


# ------------------------------------------------------------------ refusals

@pytest.mark.parametrize("setup, said", [
    ({"STUB_UID": "0"}, "do not run ff as root"),
    ({"STUB_ARCH": "armv7l"}, "32-bit ARM"),
    ({"STUB_COMPOSE_VERSION": "2.19.3"}, "Docker Compose v2 is needed at 2.20 or newer (found: 2.19.3)"),
    ({"STUB_COMPOSE_VERSION": ""}, "(found: none)"),
    ({"STUB_INFO_FAIL": "1"}, "Docker is not running, or you may not use it"),
])
def test_each_refusal_is_one_line_and_runs_nothing(rig, setup, said):
    r = rig.run("validate", **setup)
    assert r.returncode == 2
    assert r.stderr.strip().startswith("ff: ") and said in r.stderr and len(r.stderr.strip().splitlines()) == 1
    assert rig.runs() == [] and rig.composes() == []


def test_without_docker_it_says_to_install_it(rig):
    (rig.bin / "docker").unlink()
    r = rig.run("validate", PATH=f"{rig.bin}:/usr/bin:/bin")
    assert r.returncode == 2 and "Docker is not installed" in r.stderr


def test_the_home_directory_itself_is_refused(rig):
    r = rig.run("validate", HOME=str(rig.home))
    assert r.returncode == 2 and "cannot be your home directory itself" in r.stderr and rig.runs() == []


def test_the_root_directory_is_refused(rig):
    r = rig.run("validate", FF_HOME="/")
    assert r.returncode == 2 and "cannot be /" in r.stderr


def test_a_path_docker_cannot_mount_is_refused(rig):
    odd = rig.tmp / "a:b"
    odd.mkdir()
    r = rig.run("validate", FF_HOME=str(odd))
    assert r.returncode == 2 and "holds a :, a , or a line break, which Docker cannot mount" in r.stderr


@pytest.mark.skipif(not os.access("/mnt", os.W_OK) or Path("/mnt/c").exists(),
                    reason="needs a writable /mnt without a c/ to stand in for a Windows drive")
def test_a_windows_drive_is_refused(rig):
    drive = Path("/mnt/c/ff-test-install")
    drive.mkdir(parents=True)
    try:
        r = rig.run("validate", FF_HOME=str(drive))
        assert r.returncode == 2 and "on a Windows drive" in r.stderr and rig.runs() == []
    finally:
        shutil.rmtree("/mnt/c")


def test_an_install_directory_that_does_not_exist_is_refused(rig):
    r = rig.run("validate", FF_HOME=str(rig.tmp / "nowhere"))
    assert r.returncode == 2 and "does not exist" in r.stderr


# ------------------------------------------------------------------ notes

@pytest.mark.parametrize("security, said", [('["name=seccomp","name=rootless"]', "Docker runs rootless"),
                                            ('["name=userns"]', "remaps user ids")])
def test_rootless_and_remapped_docker_get_one_note_and_the_command_still_runs(rig, security, said):
    r = rig.run("validate", STUB_SECURITY=security)
    assert r.returncode == 0
    notes = [line for line in r.stderr.splitlines() if line.startswith("ff: note:")]
    assert len(notes) == 1 and said in notes[0]
    assert len(rig.runs()) == 1


def test_selinux_enforcing_gets_one_note(rig):
    _stub(rig.bin, "getenforce", "#!/bin/sh\necho Enforcing\n")
    r = rig.run("validate")
    assert r.returncode == 0
    assert [line for line in r.stderr.splitlines() if "SELinux" in line] == [
        f"ff: note: SELinux is enforcing, so the services may be refused the install's folders until they are "
        f"labelled for containers (chcon -R -t container_file_t {rig.home}); ff does not relabel them"]


def test_a_plain_docker_gets_no_note(rig):
    r = rig.run("validate")
    assert r.returncode == 0 and r.stderr == ""


# ------------------------------------------------------------------ dispatch

@pytest.mark.parametrize("args, mount", [(("validate",), "ro"), (("doctor",), "ro"),
                                         (("passcode", "add", "alex", "--stdin"), "rw"),
                                         (("import", "detect", "imports/a.csv"), "rw"), (("rules", "starter-diff"), "rw"),
                                         (("backup",), "rw"), (("restore", "backups/ff-x.tar.gz", "--force"), "rw"),
                                         (("demo",), "rw")])
def test_the_image_commands_run_as_you_with_the_install_at_ff_and_no_network(rig, args, mount):
    r = rig.run(*args)
    assert r.returncode == 0, r.stderr
    assert rig.runs() == [_etl_argv(rig.home, *args, mount=mount)]


def test_init_has_a_network_only_to_connect_a_bank_feed_and_its_answers_file_is_mounted_read_only(rig):
    person = rig.tmp / "person"
    feed = person / "answers.yaml"
    feed.write_text("household: {}\nfeed:\n  token: sample\n")
    assert rig.run("init", "--answers", str(feed), "--force").returncode == 0
    assert rig.runs() == [_etl_argv(rig.home, "init", "--answers", "/run/ff-answers.yaml", "--force",
                                    network="bridge", answers=feed)]
    files = person / "files.yaml"
    files.write_text("household: {}\n# feed: none yet\n")
    assert rig.run("init", f"--answers={files}", cwd=person).returncode == 0
    assert rig.runs() == [_etl_argv(rig.home, "init", "--answers", "/run/ff-answers.yaml", "--offline",
                                    answers=files)]
    assert rig.run("init").returncode == 0            # no terminal to ask on: no network either
    assert rig.runs() == [_etl_argv(rig.home, "init", "--offline")]


@pytest.mark.parametrize("args, network", [(("ai", "preview"), "none"), (("ai", "reset"), "none"),
                                           (("ai", "run"), "bridge"), (("ai", "run", "--refresh"), "bridge")])
def test_the_ai_commands_have_a_network_only_to_run_the_ai_step(rig, args, network):
    r = rig.run(*args)
    assert r.returncode == 0, r.stderr
    assert rig.runs() == [_etl_argv(rig.home, *args, network=network)]


@pytest.mark.parametrize("args", [("ai",), ("ai", "send"), ("ai", "--refresh")])
def test_ai_refuses_what_it_does_not_take(rig, args):
    r = rig.run(*args)
    assert r.returncode == 2 and "ai takes preview, run or reset" in r.stderr and rig.runs() == []


@pytest.mark.parametrize("args, said", [(("--answers",), "--answers needs the answers file"),
                                        (("--answers", "nowhere.yaml"), "there is no answers file at nowhere.yaml"),
                                        (("--token", "x"), "init takes --answers FILE and --force")])
def test_init_refuses_what_it_does_not_take(rig, args, said):
    r = rig.run("init", *args)
    assert r.returncode == 2 and said in r.stderr and rig.runs() == []


def test_an_answers_file_docker_cannot_mount_is_refused(rig):
    odd = rig.tmp / "a:b"
    odd.mkdir()
    (odd / "answers.yaml").write_text("feed: {}\n")
    r = rig.run("init", "--answers", str(odd / "answers.yaml"))
    assert r.returncode == 2 and "Docker cannot mount" in r.stderr and rig.runs() == []


def test_the_working_directory_follows_you_inside_the_install(rig):
    (rig.home / "imports").mkdir()
    assert rig.run("import", "detect", "a.csv", cwd=rig.home / "imports").returncode == 0
    assert rig.runs() == [_etl_argv(rig.home, "import", "detect", "a.csv", workdir="/ff/imports", mount="rw")]
    assert rig.run("validate", cwd=rig.tmp).returncode == 0
    assert rig.runs()[0][rig.runs()[0].index("-w") + 1] == "/ff"


def test_the_image_comes_from_the_environment_then_the_settings_then_composes_default(rig):
    rig.run("validate")
    assert rig.runs()[0][-5] == DEFAULT_ETL
    (rig.home / ".env").write_text("# settings\nFF_IMAGE_ETL=family-financials-etl:1.0.0\n")
    rig.run("validate")
    assert rig.runs()[0][-5] == "family-financials-etl:1.0.0"
    rig.run("validate", FF_IMAGE_ETL="family-financials-etl:2.0.0")
    assert rig.runs()[0][-5] == "family-financials-etl:2.0.0"
    text = (ROOT / "compose.yaml").read_text()
    for key, default in stack_cli.DEFAULT_IMAGES.items():
        assert re.search(rf"\$\{{{key}:-([^}}]+)\}}", text).group(1) == default
        assert re.search(rf"\bDEFAULT_{key[-3:]}={re.escape(default)}\s", FF.read_text())


def test_compose_is_handed_the_images_ff_resolved(rig):
    (rig.home / ".env").write_text("FF_IMAGE_WEB=family-financials-web:1.0.0\n")
    rig.run("ps", FF_IMAGE_ETL="family-financials-etl:2.0.0")
    assert (rig.tmp / "docker.log.images").read_text() == "family-financials-etl:2.0.0 family-financials-web:1.0.0\n"


@pytest.mark.parametrize("setup", [{"FF_IMAGE_ETL": "--privileged"}, {"FF_IMAGE_WEB": "family-financials-web:rt -v /:/h"},
                                   {".env": "FF_IMAGE_WEB=-v\n"}])
def test_an_image_that_is_no_image_name_is_refused(rig, setup):
    if ".env" in setup:
        (rig.home / ".env").write_text(setup.pop(".env"))
    r = rig.run("up", **setup)
    assert r.returncode == 2 and "is not an image name" in r.stderr
    assert rig.runs() == [] and rig.composes() == []


@pytest.mark.parametrize("key", ["FF_BIND", "FF_PORT", "FF_PORT_TLS", "FF_UID", "FF_GID", "SITE_ADDRESS",
                                 "FF_HOSTNAMES", "TRUSTED_PROXIES"])
@pytest.mark.parametrize("command", ["up", "upgrade", "build"])
def test_a_deployment_setting_in_the_environment_stops_what_would_start_containers(rig, key, command):
    r = rig.run(command, **{key: ""})
    assert r.returncode == 2
    assert f"{key} is set in your environment, where compose would read it over .env: set it in .env instead" \
        in r.stderr
    assert rig.runs() == [] and rig.composes() == []


def test_a_deployment_setting_in_the_environment_does_not_stop_the_other_commands(rig):
    assert rig.run("ps", FF_PORT="18080").returncode == 0
    assert rig.run("validate", FF_BIND="0.0.0.0").returncode == 0


@pytest.mark.parametrize("home, said", [("-x", "cannot start with - or hold a line break"),
                                        ("odd\nname", "cannot start with - or hold a line break")])
def test_an_install_directory_named_like_an_option_or_with_a_line_break_is_refused(rig, home, said):
    r = rig.run("validate", FF_HOME=home)
    assert r.returncode == 2 and said in r.stderr and rig.runs() == []


def test_cdpath_never_moves_the_install_directory(rig):
    decoy = rig.tmp / "decoy" / "install"
    decoy.mkdir(parents=True)
    assert rig.run("validate", cwd=rig.tmp, FF_HOME="install", CDPATH=str(rig.tmp / "decoy")).returncode == 0
    assert rig.runs()[0][rig.runs()[0].index("-v") + 1] == f"{rig.home}:/ff:ro"


def test_a_terminal_is_asked_for_only_when_stdin_is_one(rig):
    rig.run("validate")
    assert "-t" not in rig.runs()[0]


def _compose_prefix(home: Path) -> list[str]:
    return ["compose", "--project-directory", str(home), "-f", f"{home}/compose.yaml"]


@pytest.mark.parametrize("args, sub", [(("down",), ["down"]), (("build",), ["build"]), (("logs", "web"), ["logs", "web"]),
                                       (("ps",), ["ps"]),
                                       (("refresh",), ["exec", "refresher", "/opt/ff/deploy/refresh.sh"])])
def test_the_stack_commands_run_compose_on_the_install_directory(rig, args, sub):
    r = rig.run(*args)
    assert r.returncode == 0, r.stderr
    assert rig.composes() == [_compose_prefix(rig.home) + sub]
    assert rig.runs() == []


def test_down_never_removes_volumes(rig):
    rig.run("down", "-v")
    assert rig.composes() == [_compose_prefix(rig.home) + ["down"]]


def test_up_runs_preflight_in_the_image_then_compose_and_says_where_to_open_it(rig):
    r = rig.run("up", STUB_RUN_OUT="config OK (2 files, 1 accounts)\naddress: http://127.0.0.1:8080\n")
    assert r.returncode == 0, r.stderr
    assert rig.runs() == [_etl_argv(rig.home, "preflight", mount="rw")]
    assert rig.composes() == [_compose_prefix(rig.home) + ["ps", "--format", "json"],
                              _compose_prefix(rig.home) + ["up", "-d", "--wait"]]
    assert r.stdout == "config OK (2 files, 1 accounts)\n"
    assert r.stderr.strip().endswith("the dashboard is up: open http://127.0.0.1:8080")


def test_up_passes_this_computers_addresses_to_preflight(rig):
    _stub(rig.bin, "docker", (rig.bin / "docker").read_text().replace(
        'print(env.get("STUB_RUN_OUT", ""), end="")',
        'print("address: " + env.get("FF_HOST_ADDRESSES", "none"))'))
    r = rig.run("up")
    assert "inet 198.51.100.7/24" in r.stderr


def test_a_preflight_that_refuses_starts_nothing(rig):
    r = rig.run("up", STUB_RUN_RC="2", STUB_RUN_OUT="config OK\n")
    assert r.returncode == 1
    assert [c[5] for c in rig.composes()] == ["ps"]           # compose is read, never started


def test_ff_compose_replaces_the_whole_prefix(rig):
    _stub(rig.bin, "wrapper", '#!/bin/sh\necho "$@" >> "$STUB_LOG.wrapper"\n')
    r = rig.run("up", FF_COMPOSE="wrapper sample-label", STUB_RUN_OUT="address: http://127.0.0.1:8080\n")
    assert r.returncode == 0, r.stderr
    assert (rig.tmp / "docker.log.wrapper").read_text() == "sample-label ps --format json\nsample-label up -d --wait\n"
    assert rig.composes() == []


def test_doctor_is_given_the_ports_in_use_and_the_stacks_state(rig):
    _stub(rig.bin, "docker", (rig.bin / "docker").read_text().replace(
        'print(env.get("STUB_RUN_OUT", ""), end="")',
        'print(env.get("FF_HOST_LISTENERS", "-") + "|" + env.get("FF_STACK_PS", "-"))'))
    r = rig.run("doctor", STUB_PS='{"Service":"web","State":"running"}')
    assert r.stdout.strip() == 'LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*|{"Service":"web","State":"running"}'
    assert rig.composes() == [_compose_prefix(rig.home) + ["ps", "--format", "json"]]


def test_an_unknown_command_prints_the_usage(rig):
    r = rig.run("frobnicate")
    assert r.returncode == 2 and "Usage: ./ff init" in r.stderr and rig.runs() == []


# ------------------------------------------------------------------ upgrade, with the image's real commands

@pytest.fixture
def installed(rig):
    """The rig's install made ready by the image's own commands: examples/minimal, settings, a passcode."""
    home = rig.home
    for name in ("config", "imports"):
        shutil.copytree(ROOT / "examples" / "minimal" / name, home / name)
    shutil.copyfile(ROOT / "compose.yaml", home / "compose.yaml")
    (home / ".env").write_text(f"FF_UID={USER}\nFF_GID={USER}\nFF_BIND=127.0.0.1\nFF_PORT=8080\n")
    r = rig.run("passcode", "add", "alex", "--stdin", stdin="correct-horse-sample\n", STUB_REAL_ETL="1")
    assert r.returncode == 0, r.stdout + r.stderr
    return rig


def _upgrade(rig, **env):
    return rig.run("upgrade", "--to", "9.9.9", STUB_REAL_ETL="1", **env)


def _sequence(rig) -> list[str]:
    out = []
    for c in rig.calls():
        if c[:1] == ["run"]:
            i = c.index("python")
            out.append(f"{c[i - 1]} {' '.join(c[i + 3:])}")
        elif c[:1] == ["compose"] and c[1:2] != ["version"]:
            out.append("compose " + " ".join(c[5:]))
    return out


def test_an_upgrade_backs_up_pulls_checks_restarts_ingests_and_checks_again(installed):
    r = _upgrade(installed)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _sequence(installed) == [
        f"{DEFAULT_ETL} upgrade-plan start --to 9.9.9", "compose pull", "compose ps --format json",
        f"{NEW_ETL} preflight", "compose up -d --wait", "compose exec refresher /opt/ff/deploy/refresh.sh ingest",
        "compose ps --format json", f"{NEW_ETL} doctor", f"{NEW_ETL} upgrade-plan done"]
    assert len(list((installed.home / "backups").glob("ff-*.tar.gz"))) == 1
    assert f"FF_IMAGE_ETL={NEW_ETL}" in (installed.home / ".env").read_text()


def test_a_pull_that_fails_restarts_nothing_and_puts_the_previous_settings_back(installed):
    before = (installed.home / ".env").read_bytes()
    r = _upgrade(installed, STUB_COMPOSE_FAIL="pull")
    assert r.returncode == 1
    assert _sequence(installed) == [f"{DEFAULT_ETL} upgrade-plan start --to 9.9.9", "compose pull",
                                    f"{DEFAULT_ETL} upgrade-plan failed pull"]
    assert (installed.home / ".env").read_bytes() == before
    assert "Nothing was restarted" in r.stdout


def test_a_release_that_refuses_the_config_restarts_nothing_and_says_so(installed):
    before = (installed.home / ".env").read_bytes()
    r = _upgrade(installed, STUB_FAIL=f"{NEW_ETL} preflight", STUB_FAIL_RC="2")
    assert r.returncode == 1
    assert _sequence(installed) == [f"{DEFAULT_ETL} upgrade-plan start --to 9.9.9", "compose pull",
                                    "compose ps --format json", f"{NEW_ETL} preflight",
                                    f"{DEFAULT_ETL} upgrade-plan failed validate"]
    assert (installed.home / ".env").read_bytes() == before
    assert "the new release refuses this config" in r.stdout


@pytest.mark.parametrize("fail, stage", [({"STUB_COMPOSE_FAIL": "up"}, "up"), ({"STUB_COMPOSE_FAIL": "exec"}, "ingest"),
                                         ({"STUB_FAIL": f"{NEW_ETL} doctor"}, "doctor")])
def test_a_failure_after_the_new_images_started_says_how_to_go_back(installed, fail, stage):
    r = _upgrade(installed, **fail)
    assert r.returncode == 1
    assert _sequence(installed)[-1] == f"{DEFAULT_ETL} upgrade-plan failed {stage}"
    assert "cp .env.previous .env, then ./ff up" in r.stdout
    assert f"FF_IMAGE_ETL={NEW_ETL}" in (installed.home / ".env").read_text()
    assert (installed.home / ".env.previous").is_file()


def test_an_upgrade_whose_backup_fails_changes_nothing(installed):
    before = (installed.home / ".env").read_bytes()
    r = _upgrade(installed, STUB_FAIL=f"{DEFAULT_ETL} upgrade-plan", STUB_FAIL_RC="2")
    assert r.returncode == 2
    assert _sequence(installed) == [f"{DEFAULT_ETL} upgrade-plan start --to 9.9.9"]
    assert (installed.home / ".env").read_bytes() == before


def test_the_passcode_reached_the_image_on_stdin_never_in_argv(installed):
    assert all("correct-horse-sample" not in " ".join(c) for c in installed.calls())
    assert (installed.home / "secrets" / "web" / "passcodes").read_text().startswith("alex:scrypt:")


def test_demo_through_the_script_makes_a_household_the_image_validates(rig):
    r = rig.run("demo", STUB_REAL_ETL="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Sign in with: " in r.stdout and r.stdout.rstrip().splitlines()[-1].startswith("Now run ./ff up")
    v = rig.run("validate", STUB_REAL_ETL="1")
    assert v.returncode == 0 and v.stdout.startswith("config OK"), v.stdout + v.stderr
    assert yaml.safe_load((rig.home / "config" / "household.yaml").read_text())["timezone"] == "America/New_York"

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""What the install commands share (etl/install.py): the directory table made as the invoking user, a private
directory open to others tightened, another user's directory refused with the command that hands it back, uid 0
refused; the settings file edited without touching a line it does not set; the release's version and image defaults
as the files that state them say."""
from __future__ import annotations

import json
import os
import re
import stat
import tomllib

import pytest
import yaml
from conftest import ROOT
from ff_helpers import USER, as_user, new_home

from etl import install, stack_cli
from etl.install import Refused


@pytest.fixture
def home(tmp_path, monkeypatch):
    return new_home(tmp_path, monkeypatch)


def test_every_directory_is_made_with_its_mode(home):
    made = install.ensure_dirs(home, out=lambda _: None)
    assert made == [d.path for d in install.DIRS]
    for d in install.DIRS:
        assert stat.S_IMODE(os.stat(home / d.path).st_mode) == d.mode, d.path
    assert install.ensure_dirs(home, out=lambda _: None) == []


def test_the_table_is_what_compose_mounts_and_the_brief_lists():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text("utf-8"))
    mounted = {v["source"].removeprefix("./") for s in compose["services"].values() for v in s.get("volumes", [])}
    assert mounted <= set(install.BY_PATH)
    # every one is the owner's alone: each holds the household's finances or a secret
    assert {d.path: d.mode for d in install.DIRS} == {
        "config": 0o700, "secrets": 0o700, "secrets/web": 0o700, "secrets/feed": 0o700, "secrets/ai": 0o700,
        "secrets/alerts": 0o700, "imports": 0o700, "store": 0o700, "state": 0o700, "state/family": 0o700,
        "state/auth": 0o700, "state/refresh": 0o700, "state/engine": 0o700, "state/caddy": 0o700, "data": 0o700,
        "data/exports": 0o700, "backups": 0o700}
    assert install.ROOT_MODE == 0o700


def test_a_directory_open_to_others_is_tightened_saying_so(home):
    install.ensure_dirs(home, out=lambda _: None)
    os.chmod(home, 0o755)
    for path, mode in (("secrets/feed", 0o755), ("state/auth", 0o750), ("config", 0o775), ("imports", 0o755),
                       ("data", 0o755), ("data/exports", 0o711)):
        os.chmod(home / path, mode)
    said = []
    install.ensure_dirs(home, out=said.append)
    assert stat.S_IMODE(os.stat(home).st_mode) == 0o700
    for path in ("secrets/feed", "state/auth", "config", "imports", "data", "data/exports"):
        assert stat.S_IMODE(os.stat(home / path).st_mode) == 0o700, path
    assert len(said) == 7 and all("was open to others" in s for s in said)
    assert said[0] == f"{home} was open to others (0755): it is now 0700"


def test_under_umask_000_every_directory_is_made_the_owners_alone(home):
    old = os.umask(0)
    try:
        install.ensure_dirs(home, out=lambda _: None)
    finally:
        os.umask(old)
    assert [p for p in (home, *home.rglob("*")) if os.stat(p).st_mode & 0o077] == []


def test_a_directory_another_user_owns_is_refused_with_the_command_that_gives_it_back(home, monkeypatch):
    install.ensure_dirs(home, out=lambda _: None)
    as_user(monkeypatch, others={home / "config": 0})
    monkeypatch.setenv("FF_HOST_HOME", "/srv/ff")
    with pytest.raises(Refused) as caught:
        install.ensure_dirs(home, out=lambda _: None)
    assert str(caught.value) == (f"/srv/ff/config belongs to uid 0, not to you (uid {USER}): give it back with "
                                 f"sudo chown -R {USER}:{USER} /srv/ff/config")


def test_an_install_directory_owned_by_root_is_refused_before_anything_is_made(home, monkeypatch):
    as_user(monkeypatch, others={home: 0})
    with pytest.raises(Refused, match="belongs to uid 0"):
        install.ensure_dirs(home, out=lambda _: None)
    assert list(home.iterdir()) == []


def test_a_link_where_a_directory_belongs_is_refused(home, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (home / "secrets").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(Refused, match="is a link"):
        install.ensure_dirs(home, out=lambda _: None)
    assert list((tmp_path / "elsewhere").iterdir()) == []


def test_root_is_refused(home, monkeypatch):
    monkeypatch.setattr(install, "invoking_ids", lambda: (0, 0))
    with pytest.raises(Refused, match="runs as root"):
        install.ensure_dirs(home, out=lambda _: None)


def test_the_settings_file_keeps_its_comments_and_unknown_keys(home):
    (home / ".env").write_text("# mine\n  # indented\n\nFF_BIND=127.0.0.1\nMY_OWN=keep me\nFF_PORT=9090\n")
    install.write_env(home, {"FF_PORT": "18080", "FF_UID": "1000"})
    assert (home / ".env").read_text() == ("# mine\n  # indented\n\nFF_BIND=127.0.0.1\nMY_OWN=keep me\nFF_PORT=18080\n"
                                           "FF_UID=1000\n")
    assert stat.S_IMODE(os.stat(home / ".env").st_mode) == 0o600
    assert install.read_env(home) == {"FF_BIND": "127.0.0.1", "MY_OWN": "keep me", "FF_PORT": "18080", "FF_UID": "1000"}


def test_a_new_settings_file_is_the_template_with_the_values_set(home):
    install.write_env(home, {"FF_BIND": "0.0.0.0"})
    text = (home / ".env").read_text()
    template = (ROOT / ".env.example").read_text()
    assert text == template.replace("FF_BIND=127.0.0.1", "FF_BIND=0.0.0.0")
    assert install.env_keys() == list(install.parse_env(template))


# each a line compose reads otherwise than a plain KEY=value reader would
UNREADABLE = {
    "export": "export FF_BIND=0.0.0.0",
    "spaces around =": "FF_BIND = 0.0.0.0",
    "a space before the key": " FF_BIND=0.0.0.0",
    "a lower-case key": "ff_bind=0.0.0.0",
    "a vertical tab": "FF_BIND=127.0.0.1\vFF_PORT=80",
    "a form feed": "FF_HOSTNAMES=a\fFF_BIND=0.0.0.0",
    "a line separator": "FF_HOSTNAMES=a FF_BIND=0.0.0.0",
    "a paragraph separator": "FF_HOSTNAMES=a FF_BIND=0.0.0.0",
    "a carriage return": "FF_BIND=127.0.0.1\r",
    "a tab": "FF_BIND=127.0.0.1\t",
    "single quotes": "FF_BIND='0.0.0.0'",
    "double quotes": 'FF_BIND="0.0.0.0"',
    "a dollar": "FF_BIND=${HOST}",
    "a backtick": "FF_BIND=`hostname`",
    "an inline comment": "FF_PORT=8080 # mine",
    "a trailing space": "FF_PORT=8080 ",
    "a lone vertical tab": "\v",
}


@pytest.mark.parametrize("case", sorted(UNREADABLE))
def test_a_settings_line_compose_reads_otherwise_is_refused_by_its_number(home, case):
    (home / ".env").write_text(f"# settings\nFF_UID=1000\n{UNREADABLE[case]}\n")
    with pytest.raises(Refused, match=r"^\.env line 3"):
        install.read_env(home)
    before = (home / ".env").read_bytes()
    with pytest.raises(Refused):
        install.write_env(home, {"FF_GID": "1000"})
    assert (home / ".env").read_bytes() == before


@pytest.mark.parametrize("value", ["a\nFF_UID=0", "a\vb", "a b", "'x'", "$HOME", "`id`", "x #y", "x "])
def test_a_value_compose_would_read_otherwise_is_never_written(home, value):
    with pytest.raises(Refused, match="cannot be written"):
        install.write_env(home, {"SITE_ADDRESS": value})
    assert not (home / ".env").exists()


def test_a_value_may_hold_spaces_between_names(home):
    install.write_env(home, {"FF_HOSTNAMES": "dash.example.net other.example.net"})
    assert install.read_env(home)["FF_HOSTNAMES"] == "dash.example.net other.example.net"


def test_a_write_that_fails_at_the_rename_leaves_no_temporary_file(home, monkeypatch):
    def refused(src, dst):
        raise OSError(18, "Invalid cross-device link")
    monkeypatch.setattr(install.os, "replace", refused)
    with pytest.raises(OSError):
        install.write_atomic(home / "x.yaml", b"data", 0o644)
    assert list(home.iterdir()) == []


def test_the_settings_name_the_invoking_user_and_the_images_the_environment_gives(home, monkeypatch):
    monkeypatch.setenv("FF_IMAGE_ETL", "family-financials-etl:9.9")
    values = install.base_env(home, {"FF_PORT": "8080"})
    assert values == {"FF_PORT": "8080", "FF_UID": str(USER), "FF_GID": str(USER),
                      "FF_IMAGE_ETL": "family-financials-etl:9.9"}


def test_the_version_is_the_projects():
    """One release version: the ETL's constant, pyproject.toml and its lock entry, the dashboard's package.json and
    its lock file."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    assert install.VERSION == project["version"]
    locked = tomllib.loads((ROOT / "uv.lock").read_text("utf-8"))["package"]
    assert [p["version"] for p in locked if p["name"] == project["name"]] == [install.VERSION]
    app = json.loads((ROOT / "app" / "package.json").read_text("utf-8"))
    app_lock = json.loads((ROOT / "app" / "package-lock.json").read_text("utf-8"))
    assert app["version"] == app_lock["version"] == app_lock["packages"][""]["version"] == install.VERSION


def test_the_default_images_are_the_published_ones_at_the_version_wherever_they_are_named():
    """compose.yaml, ff and stack_cli name the same two images: the project's packages on GitHub's registry at this
    release's version, which a checkout's `ff build` tags too. .env.example, which names no host but an example one
    (tests/test_branding.py), leaves both empty and says the default is the release's published image."""
    want = {key: f"ghcr.io/good-work-dojo/family-financials-{part}:{install.VERSION}"
            for key, part in (("FF_IMAGE_ETL", "etl"), ("FF_IMAGE_WEB", "web"))}
    assert stack_cli.DEFAULT_IMAGES == want
    text = (ROOT / "compose.yaml").read_text("utf-8")
    ff = (ROOT / "ff").read_text("utf-8")
    example = (ROOT / ".env.example").read_text("utf-8").splitlines()
    assert re.search(r"^DEFAULT_ETL=\S+ DEFAULT_WEB=\S+\s", ff, re.M)
    for key, image in want.items():
        assert f"${{{key}:-{image}}}" in text
        assert re.search(rf"\bDEFAULT_{key[-3:]}={re.escape(image)}\s", ff), key
        comment = example[example.index(f"{key}=") - 1]
        assert comment.startswith(f"# {key}:") and "compose.yaml's default, this release's published" in comment, key
    defaults = re.findall(r"\$\{FF_IMAGE_(?:ETL|WEB):-([^}]+)\}", text)
    assert set(defaults) == set(want.values())


@pytest.mark.parametrize("dockerfile, title", [("Dockerfile.etl", "Family Financials ETL"),
                                               ("Dockerfile.web", "Family Financials web")])
def test_each_runtime_image_says_its_name_source_licence_and_version(dockerfile, title):
    """The final stage of each Dockerfile (the image a release publishes) carries the OCI labels, each in place of a
    base image's own: its title and vendor, the public repository (a build argument) as its source, address and
    documentation, the licence, a description and the release version (a build argument whose default is this
    release's)."""
    text = (ROOT / "deploy" / dockerfile).read_text("utf-8")
    final = re.split(r"^(?=FROM )", text, flags=re.M)[-1]
    assert re.search(r"^ARG FF_SOURCE=https://github\.com/Good-Work-Dojo/Family-Financials$", final, re.M)
    assert re.search(rf"^ARG FF_VERSION={re.escape(install.VERSION)}$", final, re.M)
    labels = dict(re.findall(r'org\.opencontainers\.image\.([a-z]+)="([^"]*)"', final))
    assert sorted(labels) == ["description", "documentation", "licenses", "source", "title", "url", "vendor",
                              "version"], "every label the Caddy base image sets, and more"
    assert (labels["title"], labels["vendor"]) == (title, "Family Financials")
    assert labels["source"] == labels["url"] == labels["documentation"] == "${FF_SOURCE}"
    assert (labels["licenses"], labels["version"]) == ("AGPL-3.0-only", "${FF_VERSION}")
    assert labels["description"].startswith("Family Financials: ")
    assert "org.opencontainers" not in text[: len(text) - len(final)], "only the published stage is labelled"


@pytest.mark.parametrize("site, domain", [("example.net", True), ("dash.example.net", True), (":8080", False),
                                          ("192.0.2.10", False), ("http://example.net", False),
                                          ("example.net:8443", False), ("localhost", False), ("", False)])
def test_a_site_address_is_a_domain_only_as_a_dotted_name(site, domain):
    assert install.is_domain(site) is domain


def test_the_address_follows_the_settings():
    assert install.address({}) == "http://127.0.0.1:8080"
    assert install.address({"FF_BIND": "192.0.2.10", "FF_PORT": "18080"}) == "http://192.0.2.10:18080"
    assert install.address({"FF_BIND": "0.0.0.0"}) == "http://<this computer's address>:8080"
    assert install.address({"SITE_ADDRESS": "example.net", "FF_PORT_TLS": "443"}) == "https://example.net"


def test_shown_names_a_path_as_the_host_does(home, monkeypatch):
    assert install.shown(home / "config") == str(home / "config")
    monkeypatch.setenv("FF_HOST_HOME", "/srv/ff")
    assert install.shown(home / "config") == "/srv/ff/config"
    assert install.shown(home) == "/srv/ff"
    assert install.shown("/etc/elsewhere") == "/etc/elsewhere"

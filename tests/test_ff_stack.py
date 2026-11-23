# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The Python halves of `./ff up` and `./ff upgrade` (etl/stack_cli.py). `preflight` makes the missing directories as
the invoking user, copies compose.yaml in, and refuses — before compose runs — settings for root or another user, no
settings at all, a config the validator refuses, an install nobody could sign in to, and plain HTTP on a bind that
reaches an address the internet can reach; it ends with the address. `upgrade-plan start` backs up and keeps the
settings as .env.previous (moving the images to a release with --to); `failed` before the new images ran puts the
previous settings back, after it says what runs and how to go back.

Every address here is from the documentation ranges, which `ipaddress` counts as not global: the tests that need a
public address stand one in by treating 192.0.2.0/24 as global."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import stat
import tarfile
from pathlib import Path

import pytest
from ff_helpers import USER, ready_home

from etl import install, stack_cli
from etl.exports import SCHEMA_VERSION
from etl.install import Refused

STAND_IN = ipaddress.ip_network("192.0.2.0/24")
IP_ADDR = ("1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever\n"
           "2: eth0    inet 192.0.2.10/24 brd 192.0.2.255 scope global eth0\\       valid_lft forever\n"
           "2: eth0    inet6 2001:db8::10/64 scope global\\       valid_lft forever\n")
PRIVATE_ONLY = "1: lo    inet 127.0.0.1/8 scope host lo\n2: eth0    inet 198.51.100.7/24 scope global eth0\n"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    return ready_home(tmp_path, monkeypatch)


@pytest.fixture
def stand_in_public(monkeypatch):
    """192.0.2.0/24 counts as an address the internet can reach."""
    real = ipaddress.IPv4Address.is_global

    def is_global(self):
        return self in STAND_IN or real.fget(self)
    monkeypatch.setattr(ipaddress.IPv4Address, "is_global", property(is_global))


def _preflight(capsys) -> tuple[int, list[str], str]:
    rc = stack_cli.run("preflight")
    captured = capsys.readouterr()
    return rc, captured.out.splitlines(), captured.err


def test_an_install_in_order_ends_with_its_address(home, capsys):
    rc, out, err = _preflight(capsys)
    assert rc == 0, err
    assert out[-1] == "address: http://127.0.0.1:8080"
    assert any(line.startswith("config OK") for line in out)


MARKER = Path("state") / "refresh" / "ingest_wanted"


def _exports(home: Path, schema: int) -> None:
    (home / "data" / "exports" / "manifest.json").write_text(json.dumps({"schema_version": schema}), "utf-8")


@pytest.mark.parametrize("exports, asked", [(None, True), (SCHEMA_VERSION - 1, True), (SCHEMA_VERSION, False),
                                            (SCHEMA_VERSION + 1, False), ("unreadable", True)])
def test_an_install_with_no_data_or_data_an_older_release_wrote_asks_the_refresher_for_an_ingest(
        home, capsys, exports, asked):
    if exports == "unreadable":
        (home / "data" / "exports" / "manifest.json").write_text("{", "utf-8")
    elif exports is not None:
        _exports(home, exports)
    rc, out, err = _preflight(capsys)
    assert rc == 0, err
    assert (home / MARKER).is_file() is asked
    said = [line for line in out if "is built" in line]
    assert said == (["the dashboard's data is built about a minute after the services start"] if asked else [])


def test_a_refused_preflight_asks_for_no_ingest(home, capsys):
    (home / "secrets" / "web" / "passcodes").unlink()
    rc, _, _ = _preflight(capsys)
    assert rc == 2 and not (home / MARKER).exists()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


RELEASE_COMPOSE = (install.ROOT / "compose.yaml").read_bytes()
OLD_COMPOSE = b"# an earlier release's compose.yaml\n" + RELEASE_COMPOSE


def test_the_shipped_compose_files_include_this_releases():
    assert _sha(RELEASE_COMPOSE) in install.SHIPPED_COMPOSE
    assert all(len(h) == 64 and int(h, 16) >= 0 for h in install.SHIPPED_COMPOSE)


def test_this_releases_compose_file_is_left_as_it_is(home, capsys):
    rc, out, err = _preflight(capsys)
    assert rc == 0, err
    assert (home / "compose.yaml").read_bytes() == RELEASE_COMPOSE
    assert not (home / "compose.yaml.previous").exists() and not (home / "compose.yaml.new").exists()
    assert not [line for line in out if "compose.yaml" in line]


def test_an_earlier_releases_compose_file_is_replaced_and_kept_as_previous(home, capsys, monkeypatch):
    monkeypatch.setattr(install, "SHIPPED_COMPOSE", install.SHIPPED_COMPOSE | {_sha(OLD_COMPOSE)})
    (home / "compose.yaml").write_bytes(OLD_COMPOSE)
    rc, out, err = _preflight(capsys)
    assert rc == 0, err
    assert (home / "compose.yaml").read_bytes() == RELEASE_COMPOSE
    assert (home / "compose.yaml.previous").read_bytes() == OLD_COMPOSE
    assert "compose.yaml is this release's now: the one before is kept as compose.yaml.previous" in out


def test_a_compose_file_changed_by_hand_is_kept_and_this_releases_written_beside_it(home, capsys):
    mine = RELEASE_COMPOSE + b"# a change of our own\n"
    (home / "compose.yaml").write_bytes(mine)
    rc, out, err = _preflight(capsys)
    assert rc == 0, err
    assert (home / "compose.yaml").read_bytes() == mine
    assert (home / "compose.yaml.new").read_bytes() == RELEASE_COMPOSE
    notes = [line for line in out if "compose.yaml" in line]
    assert len(notes) == 1 and notes[0].startswith("note: ") and "compose.yaml.new" in notes[0]


def test_a_refused_preflight_leaves_compose_yaml_as_it_is(home, capsys, monkeypatch):
    monkeypatch.setattr(install, "SHIPPED_COMPOSE", install.SHIPPED_COMPOSE | {_sha(OLD_COMPOSE)})
    (home / "compose.yaml").write_bytes(OLD_COMPOSE)
    (home / "secrets" / "web" / "passcodes").unlink()
    rc, _, _ = _preflight(capsys)
    assert rc == 2 and (home / "compose.yaml").read_bytes() == OLD_COMPOSE
    assert not (home / "compose.yaml.previous").exists()


def test_missing_directories_are_made_as_the_user_before_compose(home, capsys):
    (home / "state" / "caddy").rmdir()
    (home / "data" / "exports").rmdir()
    os.remove(home / "compose.yaml")
    rc, out, err = _preflight(capsys)
    assert rc == 0, err
    assert stat.S_IMODE(os.stat(home / "state" / "caddy").st_mode) == 0o700
    assert (home / "data" / "exports").is_dir() and (home / "compose.yaml").is_file()


@pytest.mark.parametrize("uid, said", [("0", "would run as root"), ("1", f"but you are {USER}")])
def test_settings_for_root_or_another_user_stop_it(home, capsys, uid, said):
    install.write_env(home, {"FF_UID": uid})
    rc, _, err = _preflight(capsys)
    assert rc == 2 and said in err


def test_a_settings_line_compose_reads_otherwise_stops_it(home, capsys):
    with open(home / ".env", "a") as f:
        f.write("FF_BIND = 0.0.0.0\n")
    rc, _, err = _preflight(capsys)
    assert rc == 2 and ".env line " in err and "is not KEY=value" in err


def test_no_settings_file_stops_it(home, capsys):
    (home / ".env").unlink()
    rc, _, err = _preflight(capsys)
    assert rc == 2 and "run ./ff init" in err


def test_a_config_the_validator_refuses_stops_it_with_its_lines(home, capsys):
    (home / "config" / "household.yaml").write_text("version: 1\nmembers: []\njoint_label: Joint\ntimezone: UTC\n")
    rc, out, err = _preflight(capsys)
    assert rc == 2
    lines = err.splitlines()
    assert lines[0].startswith("household.yaml: ") and lines[-1].endswith("then ./ff up")


def test_nobody_could_sign_in_without_a_passcode(home, capsys):
    (home / "secrets" / "web" / "passcodes").unlink()
    rc, _, err = _preflight(capsys)
    assert rc == 2 and "nobody could sign in: run ./ff passcode add <name>" in err


def test_the_whole_network_over_plain_http_is_refused_when_this_computer_has_a_public_address(
        home, capsys, monkeypatch, stand_in_public):
    install.write_env(home, {"FF_BIND": "0.0.0.0"})
    monkeypatch.setenv("FF_HOST_ADDRESSES", IP_ADDR)
    rc, _, err = _preflight(capsys)
    assert rc == 2 and "192.0.2.10" in err and "the internet can reach" in err
    install.write_env(home, {"SITE_ADDRESS": "example.net"})             # a domain: HTTPS
    rc, out, _ = _preflight(capsys)
    assert rc == 0 and out[-1] == "address: https://example.net:8443"


def test_a_public_bind_itself_is_refused(home, capsys, stand_in_public):
    install.write_env(home, {"FF_BIND": "192.0.2.10"})
    rc, _, err = _preflight(capsys)
    assert rc == 2 and "an address the internet can reach" in err


@pytest.mark.parametrize("hostname_i", [False, True])
def test_the_home_network_alone_is_allowed_with_a_note(home, capsys, monkeypatch, hostname_i):
    install.write_env(home, {"FF_BIND": "0.0.0.0"})
    monkeypatch.setenv("FF_HOST_ADDRESSES", "198.51.100.7 fe80::1" if hostname_i else PRIVATE_ONLY)
    rc, out, _ = _preflight(capsys)
    assert rc == 0
    assert any(line.startswith("note: the dashboard serves your home network over plain HTTP") for line in out)


def test_addresses_that_could_not_be_read_warn_instead_of_refusing(home, capsys, monkeypatch, stand_in_public):
    install.write_env(home, {"FF_BIND": "0.0.0.0"})
    monkeypatch.setenv("FF_HOST_ADDRESSES", "unknown")
    rc, out, _ = _preflight(capsys)
    assert rc == 0
    assert any("could not be read" in line for line in out)


@pytest.mark.parametrize("listening, said", [("LISTEN 0 4096 0.0.0.0:8080 0.0.0.0:*", "port 8080 is taken"),
                                             ("LISTEN 0 4096 [::]:8443 [::]:*", "port 8443 is taken")])
def test_a_port_another_program_holds_stops_it_before_anything_starts(home, capsys, monkeypatch, listening, said):
    monkeypatch.setenv("FF_HOST_LISTENERS", listening + "\n")
    monkeypatch.setenv("FF_STACK_PS", "")
    rc, _, err = _preflight(capsys)
    assert rc == 2 and said in err and "then ./ff up" in err
    monkeypatch.setenv("FF_STACK_PS", '{"Service": "web", "State": "running", "Health": "healthy"}')
    assert _preflight(capsys)[0] == 0                       # this stack's own web server holds them


def test_the_host_addresses_are_read_from_either_tool():
    assert [str(a) for a in stack_cli.host_addresses(IP_ADDR)] == ["127.0.0.1", "192.0.2.10", "2001:db8::10"]
    assert [str(a) for a in stack_cli.host_addresses("198.51.100.7 2001:db8::7 \n")] == ["198.51.100.7",
                                                                                         "2001:db8::7"]


def test_preflight_as_root_is_refused(home, capsys, monkeypatch):
    monkeypatch.setattr(install, "invoking_ids", lambda: (0, 0))
    rc, _, err = _preflight(capsys)
    assert rc == 2 and "runs as root" in err


# ------------------------------------------------------------------ upgrade

def test_upgrade_start_backs_up_and_keeps_the_settings(home, capsys):
    before = (home / ".env").read_bytes()
    assert stack_cli.run("start") == 0
    assert (home / ".env.previous").read_bytes() == before == (home / ".env").read_bytes()
    assert stat.S_IMODE(os.stat(home / ".env.previous").st_mode) == 0o600
    backups = list((home / "backups").glob("ff-*.tar.gz"))
    assert len(backups) == 1
    with tarfile.open(backups[0]) as tar:
        assert "/".join(("config", "household.yaml")) in tar.getnames()


def test_upgrade_start_to_a_tag_moves_both_images(home, capsys):
    install.write_env(home, {"FF_IMAGE_WEB": "registry.example.net/ff/web:1.0.0"})
    assert stack_cli.run("start", to="1.1.0") == 0
    env = install.read_env(home)
    assert env["FF_IMAGE_ETL"] == "ghcr.io/good-work-dojo/family-financials-etl:1.1.0"
    assert env["FF_IMAGE_WEB"] == "registry.example.net/ff/web:1.1.0"
    assert install.read_env(home, install.ENV_PREVIOUS)["FF_IMAGE_WEB"] == "registry.example.net/ff/web:1.0.0"


def test_upgrade_start_to_a_tag_refuses_an_image_the_environment_pins(home, capsys, monkeypatch):
    monkeypatch.setenv("FF_IMAGE_ETL", "family-financials-etl:1.0.0")
    assert stack_cli.run("start", to="1.1.0") == 2
    assert "set in your environment" in capsys.readouterr().err
    assert not (home / ".env.previous").exists() and not list((home / "backups").glob("*"))


@pytest.mark.parametrize("stage", ["pull", "validate"])
def test_a_failure_before_the_new_images_ran_puts_the_previous_settings_back(home, capsys, stage):
    assert stack_cli.run("start", to="9.9.9") == 0
    assert install.read_env(home)["FF_IMAGE_ETL"] == "ghcr.io/good-work-dojo/family-financials-etl:9.9.9"
    assert stack_cli.run("failed", stage=stage) == 0
    assert (home / ".env").read_bytes() == (home / ".env.previous").read_bytes()
    out = capsys.readouterr().out
    assert "Nothing was restarted" in out and "the release it ran before" in out


@pytest.mark.parametrize("stage", ["up", "ingest", "doctor"])
def test_a_failure_after_the_new_images_ran_says_what_runs_and_how_to_go_back(home, capsys, stage):
    assert stack_cli.run("start", to="9.9.9") == 0
    capsys.readouterr()
    assert stack_cli.run("failed", stage=stage) == 0
    assert install.read_env(home)["FF_IMAGE_ETL"] == "ghcr.io/good-work-dojo/family-financials-etl:9.9.9"
    out = capsys.readouterr().out
    backup = next((home / "backups").glob("ff-*.tar.gz"))
    assert "The stack now runs the new images" in out
    assert "cp .env.previous .env, then ./ff up" in out and f"./ff restore {backup} --force" in out
    assert "./ff refresh" in out and "compose.yaml" not in out      # this upgrade replaced no compose.yaml


def test_the_way_back_puts_back_the_compose_file_this_upgrade_replaced(home, capsys, monkeypatch):
    monkeypatch.setattr(install, "SHIPPED_COMPOSE", install.SHIPPED_COMPOSE | {_sha(OLD_COMPOSE)})
    (home / "compose.yaml").write_bytes(OLD_COMPOSE)
    assert stack_cli.run("start", to="9.9.9") == 0
    assert _preflight(capsys)[0] == 0
    assert stack_cli.run("failed", stage="up") == 0
    assert "cp compose.yaml.previous compose.yaml" in capsys.readouterr().out


@pytest.mark.parametrize("image, tag, want", [
    ("family-financials-etl:rt", "1.2.0", "family-financials-etl:1.2.0"),
    ("ghcr.io/good-work-dojo/family-financials-etl:1.0.0", "1.2.0", "ghcr.io/good-work-dojo/family-financials-etl:1.2.0"),
    ("registry.example.net:5000/ff/etl:1.0", "1.1", "registry.example.net:5000/ff/etl:1.1"),
    ("registry.example.net:5000/ff/etl", "1.1", "registry.example.net:5000/ff/etl:1.1"),
    ("ghcr.example.net/ff/etl:1.0@sha256:" + "a" * 64, "1.1", "ghcr.example.net/ff/etl:1.1"),
])
def test_retag(image, tag, want):
    assert stack_cli.retag(image, tag) == want


def test_a_tag_that_is_no_tag_is_refused(home, capsys):
    assert stack_cli.run("start", to="1.0 && rm") == 2
    assert not (home / ".env.previous").exists()


def test_refused_carries_one_line(home):
    with pytest.raises(Refused):
        stack_cli.upgrade_start("bad tag", out=lambda _: None)

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""`python -m etl doctor` (etl/doctor_cli.py) on an install with each fault a person or Docker can leave: the install
directory owned by root, a directory missing (Docker would make it root-owned), a passcodes file others may read, a
feed credential its group may read, data/ not writable, the settings for root or another user, a port another program
holds, a service that is not healthy, a config the validator refuses. Each is a FAIL line naming the fix, and the exit
is 1; an install in order is all `ok` with exit 0; doctor changes nothing, and never prints a secret."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

import pytest
from ff_helpers import PASSCODE, USER, as_user, ready_home

from etl import doctor_cli, install


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = ready_home(tmp_path, monkeypatch)
    (home / "data" / "exports" / "manifest.json").write_text("{}")
    return home


def _doctor(**env) -> tuple[int, list[str]]:
    lines: list[str] = []
    for key, value in env.items():
        os.environ[key] = value
    try:
        rc = doctor_cli.run(out=lines.append, now=dt.datetime.now(dt.UTC))
    finally:
        for key in env:
            os.environ.pop(key, None)
    return rc, lines


def _fails(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("FAIL")]


def _tree(root: Path) -> dict[str, tuple]:
    out = {}
    for p in sorted(root.rglob("*")):
        st = os.lstat(p)
        out[str(p.relative_to(root))] = (st.st_mode, st.st_size, hashlib.sha256(p.read_bytes()).hexdigest()
                                         if p.is_file() else None)
    return out


def test_an_install_in_order_is_all_ok(home):
    rc, lines = _doctor()
    assert rc == 0, "\n".join(lines)
    assert _fails(lines) == []
    assert all(line.split()[0] in ("ok", "warn") for line in lines[:-1])
    assert lines[-1] == "everything checked is in order"
    assert any(line.startswith("ok    the config validates") for line in lines)
    assert any(line.startswith("ok    1 passcode set up") for line in lines)
    assert any(line.startswith("ok    no bank feed is set up") for line in lines)


def test_doctor_changes_nothing_and_never_prints_a_secret(home):
    feed = home / "secrets" / "feed" / "simplefin_access_url"
    feed.write_text("https://user:sample-secret@bridge.example.net/simplefin")
    os.chmod(feed, 0o644)
    os.chmod(home / "secrets" / "web" / "passcodes", 0o644)
    before = _tree(home)
    rc, lines = _doctor()
    assert rc == 1
    assert _tree(home) == before
    text = "\n".join(lines)
    assert "sample-secret" not in text and PASSCODE not in text and "scrypt" not in text


def test_the_install_directory_owned_by_root_names_the_chown(home, monkeypatch):
    as_user(monkeypatch, others={home: 0})
    monkeypatch.setenv("FF_HOST_HOME", "/srv/ff")
    rc, lines = _doctor()
    assert rc == 1
    assert (f"FAIL  the install directory is not yours — /srv/ff belongs to uid 0, not to you (uid {USER}): give it "
            f"back with sudo chown -R {USER}:{USER} /srv/ff") in lines


def test_a_directory_docker_made_as_root_names_the_chown(home, monkeypatch):
    as_user(monkeypatch, others={home / "config": 0})
    rc, lines = _doctor()
    assert rc == 1
    assert f"FAIL  {home}/config (the household's YAML) belongs to uid 0 — sudo chown -R {USER}:{USER} {home}/config" \
        in lines


def test_a_missing_directory_is_named_with_what_makes_it(home):
    (home / "data" / "exports" / "manifest.json").unlink()
    (home / "data" / "exports").rmdir()
    (home / "state" / "caddy").rmdir()
    rc, lines = _doctor()
    assert rc == 1
    assert "FAIL  missing: state/caddy, data/exports — ./ff up (or ./ff init) makes them as you" in lines


def test_a_passcodes_file_others_may_read_fails_with_chmod(home):
    path = home / "secrets" / "web" / "passcodes"
    os.chmod(path, 0o644)
    rc, lines = _doctor()
    assert rc == 1
    assert f"FAIL  the passcodes file {path} may be read by others (0644) — chmod 600 {path}" in lines


def test_no_passcode_means_nobody_can_sign_in(home):
    (home / "secrets" / "web" / "passcodes").write_text("not a passcode line\n")
    rc, lines = _doctor()
    assert rc == 1
    assert any(line.startswith("FAIL  the passcodes file holds no usable passcode") for line in lines)
    (home / "secrets" / "web" / "passcodes").unlink()
    rc, lines = _doctor()
    assert "FAIL  no passcode is set up: nobody can sign in — ./ff passcode add <name>" in lines


def test_a_feed_credential_its_group_may_read_fails_with_chmod(home):
    cred = home / "secrets" / "feed" / "simplefin_access_url"
    cred.write_text("https://user:sample-secret@bridge.example.net/simplefin")
    os.chmod(cred, 0o640)
    rc, lines = _doctor()
    assert rc == 1
    assert f"FAIL  the bank feed's credential {cred} may be read by others (0640) — chmod 600 {cred}" in lines
    os.chmod(cred, 0o600)
    rc, lines = _doctor()
    assert "ok    the bank feed's credential is there (mode 600)" in lines
    assert any(line.startswith("warn  the bank feed has no snapshot yet") for line in lines)


def test_an_old_snapshot_warns_against_the_schedule(home):
    cred = home / "secrets" / "feed" / "simplefin_access_url"
    cred.write_text("https://user:sample-secret@bridge.example.net/simplefin")
    os.chmod(cred, 0o600)
    snaps = home / "store" / "simplefin" / "snapshots"
    snaps.mkdir(parents=True)
    old = dt.datetime.now(dt.UTC) - dt.timedelta(hours=40)
    (snaps / f"accounts-{old:%Y%m%dT%H%M%SZ}.json").write_text("{}")
    rc, lines = _doctor()
    assert rc == 0
    assert any(line.startswith("warn  the bank feed was last fetched 40 hours ago") for line in lines)


def test_an_alert_credential_others_may_read_fails(home):
    token = home / "secrets" / "alerts" / "telegram_token"
    token.write_text("0000:sample")
    os.chmod(token, 0o604)
    rc, lines = _doctor()
    assert rc == 1
    assert f"FAIL  the alert credential {token} may be read by others (0604) — chmod 600 {token}" in lines


def test_data_that_is_not_writable_fails(home):
    os.chmod(home / "data", 0o555)
    rc, lines = _doctor()
    assert rc == 1
    assert any(line.startswith(f"FAIL  {home}/data (the database and the dashboard's data) is not writable")
               for line in lines)


def test_a_private_directory_open_to_others_fails_with_chmod(home):
    os.chmod(home / "state" / "auth", 0o755)
    rc, lines = _doctor()
    assert f"FAIL  {home}/state/auth (the sign-in sessions) is open to others (0755) — chmod 700 {home}/state/auth" \
        in lines


@pytest.mark.parametrize("path, holds", [("config", "the household's YAML"), ("imports", "the statement files"),
                                         ("data", "the database and the dashboard's data")])
def test_the_households_finances_open_to_others_fail_with_chmod(home, path, holds):
    os.chmod(home / path, 0o755)
    rc, lines = _doctor()
    assert rc == 1
    assert f"FAIL  {home}/{path} ({holds}) is open to others (0755) — chmod 700 {home}/{path}" in lines


def test_an_install_directory_others_can_open_fails_with_chmod(home):
    os.chmod(home, 0o711)
    rc, lines = _doctor()
    assert rc == 1
    assert (f"FAIL  the install directory {home} is open to others (0711): they could read the household's finances "
            f"— chmod 700 {home}") in lines


@pytest.mark.parametrize("uid, said", [("0", "FAIL  FF_UID in .env is 0: the services would run as root"),
                                       ("1", "FAIL  FF_UID in .env is 1, not yours")])
def test_settings_for_root_or_another_user_fail(home, uid, said):
    install.write_env(home, {"FF_UID": uid})
    rc, lines = _doctor()
    assert rc == 1
    assert any(line.startswith(said) for line in lines)


def test_no_settings_file_fails(home):
    (home / ".env").unlink()
    rc, lines = _doctor()
    assert "FAIL  no settings file (.env) — ./ff init writes it" in lines


def test_plain_http_on_the_network_warns(home):
    install.write_env(home, {"FF_BIND": "0.0.0.0"})
    rc, lines = _doctor()
    assert rc == 0
    assert any(line.startswith("warn  the dashboard serves the network on 0.0.0.0 over plain HTTP") for line in lines)
    install.write_env(home, {"SITE_ADDRESS": "example.net"})
    assert not any("plain HTTP" in line for line in _doctor()[1])


def test_a_config_the_validator_refuses_prints_its_lines(home):
    (home / "config" / "household.yaml").write_text("version: 1\nmembers: []\njoint_label: Joint\ntimezone: Nowhere\n")
    rc, lines = _doctor()
    assert rc == 1
    i = next(i for i, line in enumerate(lines) if line.startswith("FAIL  the config has"))
    assert lines[i + 1].startswith("      household.yaml: ")


def test_statement_files_no_account_reads_are_counted(home):
    (home / "imports" / "stray-2025.csv").write_text("a,b\n")
    rc, lines = _doctor()
    assert any(line.startswith("warn  1 statement file in imports/ no account reads") for line in lines)


LISTENING = "LISTEN 0 4096 127.0.0.1:8080 0.0.0.0:*\nLISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\n"


def test_a_port_another_program_holds_fails(home):
    rc, lines = _doctor(FF_HOST_LISTENERS=LISTENING, FF_STACK_PS="")
    assert rc == 1
    assert "FAIL  port 8080 is taken by another program — set another FF_PORT in .env" in lines
    rc, lines = _doctor(FF_HOST_LISTENERS="LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\n", FF_STACK_PS="")
    assert "ok    port 8080 is free" in lines and "ok    port 8443 is free" in lines
    assert "ok    the stack is not running (./ff up starts it)" in lines
    rc, lines = _doctor(FF_HOST_LISTENERS="LISTEN 0 4096 [::]:8443 [::]:*\n", FF_STACK_PS="")
    assert rc == 1 and "FAIL  port 8443 is taken by another program — set another FF_PORT_TLS in .env" in lines
    rc, lines = _doctor(FF_HOST_LISTENERS="unknown", FF_STACK_PS="")
    assert rc == 0 and any(line.startswith("warn  whether port 8080 is free could not be checked") for line in lines)


def _ps(*services: tuple[str, str, str]) -> str:
    return "\n".join(json.dumps({"Service": s, "State": st, "Health": h}) for s, st, h in services)


def test_the_stacks_services_are_reported_and_its_port_is_its_own(home):
    ps = _ps(("web", "running", "healthy"), ("store", "running", "healthy"), ("scheduler", "running", "healthy"),
             ("refresher", "running", "unhealthy"))
    rc, lines = _doctor(FF_HOST_LISTENERS=LISTENING, FF_STACK_PS=ps)
    assert rc == 1
    assert "ok    the ports are held by this stack's web server" in lines
    assert "ok    web is running and healthy" in lines
    assert "FAIL  refresher is running (unhealthy) — ./ff logs refresher says why" in lines
    # compose's older JSON array form reads the same
    array = json.dumps([json.loads(line) for line in ps.splitlines()])
    assert _doctor(FF_HOST_LISTENERS=LISTENING, FF_STACK_PS=array)[1] == lines


def test_a_linked_secrets_folder_fails_and_what_lies_behind_it_is_never_listed(home, tmp_path):
    behind = tmp_path / "behind"
    behind.mkdir()
    for name in ("telegram_token", "telegram_chat_id"):
        (behind / name).write_text("0000:sample")
        os.chmod(behind / name, 0o644)
    (home / "secrets" / "alerts").rmdir()
    (home / "secrets" / "alerts").symlink_to(behind)
    rc, lines = _doctor()
    assert rc == 1
    assert (f"FAIL  {home}/secrets/alerts (the alerts' credentials) is a link — it must not be one: move it aside, then "
            "./ff up makes the folder") in lines
    # one line for the folder, none for what lies behind it
    assert [line for line in lines if "(the alert credential's place)" in line] == [
        f"FAIL  {home}/secrets/alerts (the alert credential's place) is a link — it must not be one: move it aside "
        "and set it up again"]
    assert not any("telegram" in line or str(behind) in line for line in lines)


@pytest.mark.parametrize("which", ["passcodes", "simplefin_access_url"])
def test_a_linked_secret_file_fails_without_being_read(home, tmp_path, which):
    target = tmp_path / "elsewhere"
    target.write_text("https://user:sample-secret@bridge.example.net/simplefin\n")
    os.chmod(target, 0o600)
    path = home / "secrets" / ("web" if which == "passcodes" else "feed") / which
    path.unlink(missing_ok=True)
    path.symlink_to(target)
    rc, lines = _doctor()
    assert rc == 1
    assert any(line.startswith(f"FAIL  {path} (") and "is a link — it must not be one" in line for line in lines)
    assert not any("sample-secret" in line for line in lines)


def test_a_settings_line_compose_reads_otherwise_fails_with_its_number(home):
    with open(home / ".env", "a") as f:
        f.write("export FF_BIND=0.0.0.0\n")
    rc, lines = _doctor()
    assert rc == 1
    assert any(line.startswith("FAIL  .env line ") and "is not KEY=value" in line for line in lines)


def test_doctor_as_root_fails(home, monkeypatch):
    monkeypatch.setattr(install, "invoking_ids", lambda: (0, 0))
    rc, lines = _doctor()
    assert rc == 1 and lines[0].startswith("FAIL  doctor runs as root")


def test_every_line_reads_status_what_and_fix(home):
    os.chmod(home / "secrets" / "web" / "passcodes", 0o644)
    install.write_env(home, {"FF_BIND": "0.0.0.0"})
    _, lines = _doctor()
    for line in lines[:-1]:
        status = line[:6]
        assert status in ("ok    ", "warn  ", "FAIL  ") or line.startswith("      "), line
        if status in ("warn  ", "FAIL  "):
            assert " — " in line, line

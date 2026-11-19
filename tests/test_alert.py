# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""deploy/alert.py — one alert of the data refresh, through the channels set up as files in the alert secrets.

Every request goes to tests/fake_alerts.py on 127.0.0.1, reached through the module's one test seam; the credentials
are invented."""
from __future__ import annotations

import io
import os
import sys
import threading
import time
from pathlib import Path

import alert  # tests/conftest.py puts deploy/ on sys.path
import fake_alerts
import pytest
from conftest import ROOT, write_household
from fake_alerts import CHAT, TOKEN


@pytest.fixture()
def hook(monkeypatch):
    with fake_alerts.serve() as h:
        monkeypatch.setenv(alert.TEST_ORIGIN_VARIABLE, h.origin)
        yield h


@pytest.fixture()
def folder(tmp_path, monkeypatch) -> Path:
    """The alert secrets' folder, empty, and a household named "The Sample household"."""
    d = tmp_path / "alerts"
    d.mkdir(mode=0o700)
    monkeypatch.setenv("FF_SECRETS_ALERTS_DIR", str(d))
    write_household(tmp_path / "config", name="The Sample household")
    monkeypatch.setenv("FF_CONFIG_DIR", str(tmp_path / "config"))
    return d


def secret(folder: Path, name: str, text: str, mode: int = 0o600) -> Path:
    path = folder / name
    path.write_text(text)
    path.chmod(mode)
    return path


def telegram(folder: Path) -> None:
    secret(folder, alert.TOKEN_FILE, TOKEN + "\n")
    secret(folder, alert.CHAT_FILE, CHAT)


def test_with_no_channel_nothing_is_sent_and_nothing_said(folder, hook, capsys, monkeypatch):
    """No file in the folder is NO_CHANNEL with nothing printed; the variables an older setup used are no channel."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT)
    monkeypatch.setenv("ALERT_URL", f"{hook.origin}/hook")
    assert alert.main(["the refresh failed"]) == alert.NO_CHANNEL
    assert hook.received == [] and capsys.readouterr() == ("", "")


def test_the_webhook_gets_the_message_as_json_prefixed_with_the_households_name(folder, hook, capsys):
    secret(folder, alert.URL_FILE, f"{hook.origin}/hook/0000\n")
    assert alert.main(["🔴 data refresh FAILED"]) == 0
    [got] = hook.received
    assert (got.path, got.content_type, got.text) == ("/hook/0000", "application/json",
                                                      "The Sample household: 🔴 data refresh FAILED")
    assert capsys.readouterr().err == ""


def test_telegram_gets_the_message_and_the_chat_at_its_bots_send_message(folder, hook):
    telegram(folder)
    assert alert.main(["a test"]) == 0
    [got] = hook.received
    assert (got.path, got.content_type, got.text, got.chat_id) == (
        f"/bot{TOKEN}/sendMessage", "application/x-www-form-urlencoded", "The Sample household: a test", CHAT)


def test_both_channels_get_it_and_one_that_fails_does_not_stop_the_other(folder, hook, capsys):
    telegram(folder)
    secret(folder, alert.URL_FILE, f"{hook.origin}/hook")
    assert alert.main(["both"]) == 0
    assert sorted(r.path for r in hook.received) == sorted([f"/bot{TOKEN}/sendMessage", "/hook"])
    hook.status = 500
    assert alert.main(["both again"]) == 1
    err = capsys.readouterr().err.splitlines()
    assert err == ["alert: Telegram did not take the alert: it answered HTTP 500",
                   "alert: the webhook did not take the alert: it answered HTTP 500"]


def test_the_message_may_come_on_standard_input(folder, hook, monkeypatch):
    secret(folder, alert.URL_FILE, f"{hook.origin}/hook")
    monkeypatch.setattr(sys, "stdin", io.StringIO("from\nstandard input\n"))
    assert alert.main([]) == 0
    assert hook.texts() == ["The Sample household: from standard input"]
    monkeypatch.setattr(sys, "stdin", io.StringIO("  \n"))
    assert alert.main([]) == 2


@pytest.mark.parametrize("household, prefix", [
    ({"name": "The Sample household"}, "The Sample household"),
    ({"surname": "Sample"}, "Sample"),
    ({"name": "  The\nSample \x1b household  "}, "The Sample household"),
    ({}, alert.PRODUCT),
    (None, alert.PRODUCT),
])
def test_the_prefix_is_the_households_display_name_else_the_products(tmp_path, household, prefix):
    config = tmp_path / "config"
    if household is not None:
        write_household(config, **household)
    assert alert.household_name(config) == prefix


def test_an_unreadable_household_yaml_still_lets_the_alert_go(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "household.yaml").write_text("name: [unclosed\n")
    assert alert.household_name(config) == alert.PRODUCT


@pytest.mark.parametrize("make, says", [
    (lambda f: secret(f, alert.URL_FILE, "http://example.net/hook", 0o640), "can be read by other users (mode 0o640)"),
    (lambda f: secret(f, alert.URL_FILE, "http://example.net/hook", 0o604), "chmod 600"),
    (lambda f: secret(f, alert.URL_FILE, ""), "is empty: write the credential in it, or remove the file"),
    (lambda f: secret(f, alert.URL_FILE, "x" * (alert.MAX_SECRET_BYTES + 1)), "is larger than a credential"),
    (lambda f: (f / alert.URL_FILE).symlink_to(secret(f.parent, "elsewhere", "http://example.net/hook")),
     "is a link: put the credential in the file itself"),
    (lambda f: (f / alert.URL_FILE).mkdir(), "is not a file"),
    (lambda f: os.mkfifo(f / alert.URL_FILE, 0o600), "is not a file"),
])
def test_a_credential_file_it_will_not_use_fails_its_channel_with_a_plain_line(folder, hook, capsys, make, says):
    """Refused at once (a FIFO is never waited on), nothing sent, exit 1, and the line names the file and the fix,
    never what the file holds."""
    make(folder)
    started = time.monotonic()
    assert alert.main(["the refresh failed"]) == 1
    assert time.monotonic() - started < 5
    [line] = capsys.readouterr().err.splitlines()
    assert line.startswith(f"alert: the webhook did not take the alert: {folder / alert.URL_FILE} ") and says in line
    assert "example.net" not in line and hook.received == []


def test_telegram_needs_both_of_its_files_and_a_token_of_its_own_shape(folder, hook, capsys):
    secret(folder, alert.TOKEN_FILE, TOKEN)
    assert alert.main(["x"]) == 1
    assert "it needs both telegram_token and telegram_chat_id" in capsys.readouterr().err
    secret(folder, alert.CHAT_FILE, CHAT)
    secret(folder, alert.TOKEN_FILE, "123456:ABC/../../elsewhere?x=")
    assert alert.main(["x"]) == 1
    err = capsys.readouterr().err
    assert "holds characters a bot token never has" in err and "elsewhere" not in err
    assert hook.received == []


def test_a_webhook_address_must_be_https_or_the_seams_own(folder, hook, capsys):
    """Plain HTTP is refused unless it is the seam's exact origin: another port, another loopback name, an address
    with a user, or the seam unset, and nothing is sent."""
    port = int(hook.origin.rsplit(":", 1)[1])
    for url in (f"http://127.0.0.1:{port + 1}/hook", f"http://localhost:{port}/hook",
                f"http://user:pw@127.0.0.1:{port}/hook", "ftp://example.net/hook", "not an address"):
        secret(folder, alert.URL_FILE, url)
        assert alert.main(["x"]) == 1, url
        assert "does not hold an https address" in capsys.readouterr().err, url
    assert hook.received == []
    assert alert._allowed("https://hooks.example.net/x") and not alert._allowed("https:///x")


@pytest.mark.parametrize("value", ["", "http://127.0.0.1", "http://127.0.0.2:8000", "http://localhost:8000",
                                   "https://127.0.0.1:8000", "http://127.0.0.1:99999", "http://127.0.0.1:8000/"])
def test_the_seam_opens_nothing_unless_it_is_exactly_a_loopback_origin(monkeypatch, value):
    monkeypatch.setenv(alert.TEST_ORIGIN_VARIABLE, value)
    assert alert._seam() is None
    assert not alert._allowed("http://127.0.0.1:8000/hook")


def test_without_the_seam_telegram_is_reached_over_https_only(monkeypatch):
    monkeypatch.delenv(alert.TEST_ORIGIN_VARIABLE, raising=False)
    assert alert._seam() is None and alert.TELEGRAM.startswith("https://")
    assert not alert._allowed("http://api.telegram.org/bot1/sendMessage")


def test_a_redirect_is_never_followed(folder, hook, capsys):
    """A 30x fails the channel, and the address it names is never asked anything."""
    with fake_alerts.serve() as elsewhere:
        hook.redirect = f"{elsewhere.origin}/stolen"
        telegram(folder)
        assert alert.main(["x"]) == 1
        assert "it answered HTTP 302" in capsys.readouterr().err
        assert len(hook.received) == 1 and elsewhere.received == []


def test_a_channel_that_never_answers_is_given_up_after_the_timeout(folder, monkeypatch, capsys):
    """A server that accepts the connection and never answers: the channel fails after TIMEOUT_S (shortened here)."""
    import socket
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    monkeypatch.setenv(alert.TEST_ORIGIN_VARIABLE, origin)
    monkeypatch.setattr(alert, "TIMEOUT_S", 1)
    secret(folder, alert.URL_FILE, f"{origin}/hook")
    try:
        started = time.monotonic()
        assert alert.main(["x"]) == 1
        assert time.monotonic() - started < 5
        assert "no answer within 1 s" in capsys.readouterr().err
    finally:
        listener.close()


def test_the_ten_second_timeout_is_the_one_given():
    assert alert.TIMEOUT_S == 10


def test_no_credential_is_ever_printed(folder, hook, capsys):
    """Whatever fails, the lines name files and statuses: never the token, the chat or the webhook's address."""
    telegram(folder)
    secret(folder, alert.URL_FILE, f"{hook.origin}/hook/s3cr3t-path")
    hook.status = 403
    assert alert.main(["x"]) == 1
    out = capsys.readouterr()
    for credential in (TOKEN, CHAT, "s3cr3t-path"):
        assert credential not in out.out + out.err


def test_the_seam_variable_is_read_in_one_place_and_named_in_no_image_compose_file_or_document():
    sources = {p.relative_to(ROOT).as_posix(): p.read_text("utf-8")
               for d in ("etl", "deploy") for p in (ROOT / d).rglob("*")
               if p.is_file() and (p.suffix in (".py", ".sh", ".md") or p.name.startswith("Dockerfile"))}
    assert {n for n, text in sources.items() if alert.TEST_ORIGIN_VARIABLE in text} == {"deploy/alert.py"}
    module = sources["deploy/alert.py"]
    assert module.count("os.environ") == module.count("os.environ.get(TEST_ORIGIN_VARIABLE") == 1
    documents = [p for p in (ROOT / "docs").rglob("*.md") if "dev" not in p.relative_to(ROOT / "docs").parts]
    elsewhere = [ROOT / "compose.yaml", ROOT / ".env.example", ROOT / "README.md", *documents]
    assert [p for p in elsewhere if p.is_file() and alert.TEST_ORIGIN_VARIABLE in p.read_text("utf-8")] == []


def test_it_is_stdlib_until_it_finds_its_folders():
    """The module itself imports only the standard library: the ETL and the YAML reader are loaded where the folders
    and the household's name are found."""
    text = (ROOT / "deploy" / "alert.py").read_text("utf-8")
    top = [line for line in text.splitlines() if line.startswith(("import ", "from "))]
    assert all(line.split()[1].split(".")[0] in sys.stdlib_module_names or line == "from __future__ import annotations"
               for line in top), top


def test_concurrent_alerts_each_go_out_once(folder, hook):
    """Two runs alerting at once (the scheduler's and the refresher's) each send their own message once."""
    secret(folder, alert.URL_FILE, f"{hook.origin}/hook")
    threads = [threading.Thread(target=alert.main, args=([f"run {i}"],)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(hook.texts()) == ["The Sample household: run 0", "The Sample household: run 1"]

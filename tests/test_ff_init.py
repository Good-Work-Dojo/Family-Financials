# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""`python -m etl init` (etl/setup_cli.py): the setup conversation, from an answers file and on a scripted terminal,
against the fake SimpleFIN Bridge on 127.0.0.1 (tests/fake_bridge.py, reached through the feed module's test seam,
which only these tests set).

The single-use setup token is spent once: the credential file exists, empty and 0600, before the claim is sent, and
holds the access URL (0600) before the first account request; a claim that fails removes the empty file and says
whether the token may still be used; a run stopped after the claim resumes at the accounts without a token. Whatever
is on disk is kept (household.yaml and accounts.yaml are replaced only with --force), an answers file others may read
or missing a key is refused before anything is written, and every household it writes passes the validator."""
from __future__ import annotations

import datetime as dt
import errno
import os
import signal
import stat
import time
import urllib.parse
import zoneinfo
from pathlib import Path

import fake_bridge
import pytest
import yaml
from fake_bridge import Account
from ff_helpers import PASSCODE, SAMPLE_CSV, USER, answers, new_home, write_answers

from etl import install, setup_cli
from etl import simplefin as sf
from etl.config import Config
from etl.setup_cli import run

DAY = 86_400
PROXIES = ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY")
CHECKING, CARD, LOAN = "ACT-fake-0001", "ACT-fake-0002", "ACT-fake-0003"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    return new_home(tmp_path, monkeypatch)


@pytest.fixture
def bridge(monkeypatch):
    for name in PROXIES:
        monkeypatch.delenv(name, raising=False)
    with fake_bridge.serve() as running:
        monkeypatch.setenv(sf.TEST_ORIGIN_VARIABLE, running.origin)
        now = int(time.time())
        running.accounts = [
            Account(CHECKING, "Sample Feed Checking 0000", "1234.56", now - DAY, transactions=[
                {"id": "TRN-0001", "posted": now - 5 * DAY, "amount": "-54.18", "description": "TRADER JOE'S #000"},
                {"id": "TRN-0002", "posted": now - 40 * DAY, "amount": "-6.45", "description": "STARBUCKS 00000"}]),
            Account(CARD, "Sample Visa Card 1111", "-310.00", now - DAY, transactions=[
                {"id": "TRN-0003", "posted": now - 3 * DAY, "amount": "-310.00", "description": "SAMPLE STORE"}]),
            Account(LOAN, "Sample Home Mortgage 2222", "-250000.00", now - DAY),
        ]
        yield running


def _run(home: Path, doc: dict | None = None, *, force: bool = False, **kw) -> int:
    file = write_answers(home.parent / "answers.yaml", doc) if doc is not None else None
    return run(str(file) if file else None, force=force, out=kw.pop("out", lambda _: None), **kw)


def _cfg(home: Path) -> Config:
    return Config(check_paths=True)


def _yaml(path: Path):
    return yaml.safe_load(path.read_text("utf-8"))


FEED_ANSWERS = {"accounts": {CHECKING: {"kind": "checking", "owner": "Joint"},
                             "Sample Visa Card 1111": {"kind": "credit-card", "owner": "Alex"},
                             LOAN: {"kind": "loan", "owner": "Joint", "mortgage": True}}}


# ------------------------------------------------------------------ answers files, and what they write

@pytest.mark.parametrize("case", ["files-only-none", "statement-file", "home-network", "no-children", "home-buying"])
def test_every_household_an_answers_file_writes_passes_the_validator(home, case, capsys):
    doc = answers()
    if case == "statement-file":
        (home / "imports").mkdir()
        (home / "imports" / SAMPLE_CSV.name).write_bytes(SAMPLE_CSV.read_bytes())
        doc["files"] = {"demo_checking_*.csv": {"kind": "checking", "owner": "Alex", "name": "Sample Checking",
                                                "institution": "Sample Bank"}}
    if case == "home-network":
        doc["listen"] = {"network": True, "port": 18_080}
    if case == "no-children":
        del doc["household"]["children"]
    if case == "home-buying":
        doc["modules"] = {"home_buying": True}
    assert _run(home, doc) == 0, capsys.readouterr().err
    cfg = _cfg(home)
    household = _yaml(home / "config" / "household.yaml")
    assert household["name"] == "The Samples" and household["timezone"] == "America/New_York"
    assert 0 <= household["refresh"]["minute"] <= 59 and "hours" not in household["refresh"]
    assert household["modules"] == {"home_buying": case == "home-buying"}
    assert [m["name"] for m in household["members"]] == ["Alex", "Sam"] + ([] if case == "no-children" else ["Robin"])
    assert len(cfg.accounts) == (1 if case == "statement-file" else 0)
    if case == "statement-file":
        assert cfg.accounts["sample_checking"]["parser"] == "chase_checking"
    env = install.read_env(home)
    assert (env["FF_BIND"], env["FF_PORT"]) == (("0.0.0.0", "18080") if case == "home-network" else ("127.0.0.1", "8080"))
    assert (env["FF_UID"], env["FF_GID"]) == (str(USER), str(USER))
    assert stat.S_IMODE(os.stat(home / ".env").st_mode) == 0o600
    assert set(install.env_keys()) <= set(env)
    assert (home / "compose.yaml").read_bytes() == (install.ROOT / "compose.yaml").read_bytes()
    starters = sorted(p.name for p in (install.ROOT / "starter").glob("*.yaml"))
    assert all((home / "config" / name).is_file() for name in starters)
    passcodes = home / "secrets" / "web" / "passcodes"
    assert stat.S_IMODE(os.stat(passcodes).st_mode) == 0o600
    assert passcodes.read_text().startswith("alex:scrypt:") and PASSCODE not in passcodes.read_text()
    for d in install.DIRS:
        assert stat.S_IMODE(os.stat(home / d.path).st_mode) == d.mode, d.path


EXAMPLE_ANSWERS = install.ROOT / "examples" / "answers.yaml"


def test_the_shipped_example_answers_set_up_a_household_offline_that_validates(home, bridge, capsys):
    """examples/answers.yaml, copied outside the install as the README says (mode 600), sets up the invented household
    with no network (it has no feed: block, so ./ff gives init none) and the config it writes validates."""
    text = EXAMPLE_ANSWERS.read_text("utf-8")
    assert "\nfeed:" not in text and "\nfiles:" not in text          # both blocks stay examples, commented out
    copy = home.parent / "ff-answers.yaml"
    copy.write_text(text, "utf-8")
    os.chmod(copy, 0o600)
    # left unedited, its sample passcode is refused and no passcode is set up
    assert run(str(copy), offline=True, out=lambda _line: None) != 0
    assert "sample passcode" in capsys.readouterr().err
    assert not (home / "secrets" / "web" / "passcodes").exists()
    # edited as the README says, it sets up the household
    copy.write_text(text.replace("replace-this-sample-passcode", "a-passcode-of-our-own"), "utf-8")
    out: list[str] = []
    assert run(str(copy), offline=True, out=out.append) == 0, capsys.readouterr().err
    assert out[-1:] != [] and any(line.startswith("config OK") for line in out)
    assert bridge.received == []
    household = _yaml(home / "config" / "household.yaml")
    assert (household["name"], household["timezone"]) == ("The Samples", "America/New_York")
    assert [m["name"] for m in household["members"]] == ["Alex", "Sam", "Robin"]
    assert _cfg(home).accounts == {}
    assert (home / "secrets" / "web" / "passcodes").read_text().startswith("alex:scrypt:")


def test_the_written_files_carry_a_header_and_keep_a_stable_key_order(home):
    assert _run(home, answers()) == 0
    text = (home / "config" / "household.yaml").read_text("utf-8")
    assert text.startswith("# Who the household is")
    assert list(_yaml(home / "config" / "household.yaml")) == ["version", "name", "members", "joint_label", "timezone",
                                                              "modules", "refresh"]
    assert (home / "config" / "accounts.yaml").read_text("utf-8").startswith("# The household's accounts")


@pytest.mark.parametrize("key", ["household.name", "household.adults", "household.timezone"])
def test_an_answers_file_missing_a_key_names_it_and_writes_no_household(home, key, capsys):
    doc = answers()
    del doc["household"][key.split(".")[1]]
    assert _run(home, doc) == 2
    assert f"the answers file has no {key}" in capsys.readouterr().err
    assert not (home / "config" / "household.yaml").exists() and not (home / ".env").exists()


@pytest.mark.parametrize("passcode, said", [("seven77", "at least 8 characters"),
                                            ("123456789", "digits only needs at least 10")])
def test_a_passcode_the_rules_refuse_stops_the_run_before_anything_is_written(home, passcode, said, capsys):
    assert _run(home, answers(passcode=passcode)) == 2
    assert said in capsys.readouterr().err
    assert not (home / "config" / "household.yaml").exists()


def test_a_passcode_written_as_a_number_is_refused(home, capsys):
    assert _run(home, answers(passcode=12345678901)) == 2
    assert "write it as text" in capsys.readouterr().err


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644])
def test_an_answers_file_others_may_read_is_refused(home, mode, capsys):
    file = write_answers(home.parent / "answers.yaml", answers(), mode)
    assert run(str(file), out=lambda _: None) == 2
    err = capsys.readouterr().err
    assert "may be read by others" in err and "chmod 600" in err and PASSCODE not in err
    assert not (home / "config").exists()


def test_a_bad_zone_a_port_out_of_range_and_a_joint_label_that_is_a_person_are_refused(home, capsys):
    for change, said in ((("household", "timezone", "Mars/Olympus"), "not a time zone name"),
                         (("household", "timezone", "localtime"), "not a time zone name"),
                         (("household", "joint_label", "sam"), "is a person's name")):
        doc = answers()
        doc[change[0]][change[1]] = change[2]
        assert _run(home, doc) == 2
        assert said in capsys.readouterr().err
    assert not (home / "config" / "household.yaml").exists()
    doc = answers()
    doc["listen"]["port"] = 80
    assert _run(home, doc) == 2
    assert "from 1024 to 65535" in capsys.readouterr().err
    assert not (home / ".env").exists()


def test_what_is_on_disk_is_kept_and_force_asks_again(home, capsys):
    (home / "config").mkdir()
    (home / "config" / "household.yaml").write_text(
        "version: 1\nname: Kept\nmembers:\n  - {name: Robin, role: adult}\njoint_label: Ours\ntimezone: UTC\n")
    (home / "config" / "accounts.yaml").write_text("version: 1\naccounts: {}\n# kept\n")
    before = {n: (home / "config" / n).read_bytes() for n in ("household.yaml", "accounts.yaml")}
    assert _run(home, answers()) == 0
    assert {n: (home / "config" / n).read_bytes() for n in before} == before
    passcodes = (home / "secrets" / "web" / "passcodes").read_text()
    assert passcodes.startswith("robin:")
    assert _run(home, answers(), force=True) == 0
    assert _yaml(home / "config" / "household.yaml")["name"] == "The Samples"
    assert "# kept" not in (home / "config" / "accounts.yaml").read_text()
    assert (home / "secrets" / "web" / "passcodes").read_text() == passcodes      # a passcode is never replaced


def test_a_run_again_asks_nothing_already_answered(home):
    assert _run(home, answers()) == 0
    first = {p: p.read_bytes() for p in (home / "config").iterdir()} | {home / ".env": (home / ".env").read_bytes()}
    assert _run(home, {"household": {}}) == 0           # nothing left to ask: no key is needed
    assert {p: p.read_bytes() for p in first} == first


# ------------------------------------------------------------------ the bank feed

def test_with_a_token_the_credential_is_there_0600_before_the_claim_and_holds_the_url_before_the_first_account_call(
        home, bridge, monkeypatch):
    seen = []
    real = sf._request
    cred = home / "secrets" / "feed" / "simplefin_access_url"

    def request(url, method="GET", data=None):
        st = os.stat(cred)
        seen.append((method, stat.S_IMODE(st.st_mode), st.st_size))
        return real(url, method=method, data=data)
    monkeypatch.setattr(sf, "_request", request)
    doc = answers(feed={"token": bridge.setup_token(), **FEED_ANSWERS})
    assert _run(home, doc) == 0
    assert seen[0] == ("POST", 0o600, 0)
    assert seen[1][0] == "GET" and seen[1][1] == 0o600 and seen[1][2] > 0
    assert len(seen) == 2
    assert urllib.parse.urlsplit(cred.read_text()).username in {r.user for r in bridge.gets()}
    assert stat.S_IMODE(os.stat(cred).st_mode) == 0o600
    # the snapshot fetched to list the accounts is kept: the household's first
    assert len(list((home / "store" / "simplefin" / "snapshots").glob("accounts-*.json"))) == 1


def test_the_feed_accounts_become_accounts_with_their_kind_owner_and_mortgage(home, bridge):
    assert _run(home, answers(feed={"token": bridge.setup_token(), **FEED_ANSWERS})) == 0
    accounts = _cfg(home).accounts
    by_feed = {m["simplefin_id"]: (aid, m) for aid, m in accounts.items()}
    assert by_feed[CHECKING][1]["kind"] == "checking" and by_feed[CHECKING][1]["owner"] == "Joint"
    assert by_feed[CARD][1]["kind"] == "credit-card" and by_feed[CARD][1]["owner"] == "Alex"
    assert by_feed[LOAN][1]["mortgage"] is True and by_feed[LOAN][1]["institution"] == "Sample Bank"
    assert not any("simplefin_cutover" in m or "simplefin_balances_only" in m for m in accounts.values())


def test_a_feed_account_given_skip_is_left_out_and_one_left_unanswered_is_named(home, bridge, capsys):
    doc = answers(feed={"token": bridge.setup_token(), "accounts": {CHECKING: "skip", CARD: "skip"}})
    assert _run(home, doc) == 2
    err = capsys.readouterr().err
    assert "Sample Home Mortgage 2222" in err and "stays connected" in err
    doc["feed"]["accounts"][LOAN] = {"kind": "loan", "owner": "Joint"}
    assert _run(home, doc) == 0
    assert [m["simplefin_id"] for m in _cfg(home).accounts.values()] == [LOAN]
    assert len([r for r in bridge.received if r.method == "POST"]) == 1


def test_files_and_the_feed_together_get_a_cutover_the_day_after_the_feeds_earliest_transaction(home, bridge):
    (home / "imports").mkdir()
    (home / "imports" / SAMPLE_CSV.name).write_bytes(SAMPLE_CSV.read_bytes())
    doc = answers(feed={"token": bridge.setup_token(), **FEED_ANSWERS},
                  files={SAMPLE_CSV.name: {"account": CHECKING}})
    assert _run(home, doc) == 0
    account = next(m for m in _cfg(home).accounts.values() if m.get("simplefin_id") == CHECKING)
    earliest = min(t["posted"] for t in bridge.accounts[0].transactions)
    day = dt.datetime.fromtimestamp(earliest, zoneinfo.ZoneInfo("America/New_York")).date()
    assert account["simplefin_cutover"] == (day + dt.timedelta(days=1)).isoformat()
    assert account["parser"] == "chase_checking" and account["glob"] == "demo_checking_*.csv"
    assert "simplefin_balances_only" not in account


def test_a_run_stopped_after_the_claim_resumes_at_the_accounts_without_spending_a_token(home, bridge, monkeypatch,
                                                                                       capsys):
    doc = answers(feed={"token": bridge.setup_token(), **FEED_ANSWERS})
    real = sf.fetch

    def interrupted(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(sf, "fetch", interrupted)
    assert _run(home, doc) == 130
    assert "run ./ff init again" in capsys.readouterr().err
    cred = home / "secrets" / "feed" / "simplefin_access_url"
    assert cred.read_text().startswith("http://") and stat.S_IMODE(os.stat(cred).st_mode) == 0o600
    assert not (home / "config" / "accounts.yaml").exists()
    monkeypatch.setattr(sf, "fetch", real)
    out = []
    assert _run(home, doc, out=out.append) == 0
    assert any("already connected" in line for line in out)
    assert any("setup token in the answers file is not used" in line for line in out)
    assert len([r for r in bridge.received if r.method == "POST"]) == 1
    assert len(_cfg(home).accounts) == 3


def test_a_token_already_claimed_leaves_no_credential_and_says_a_new_one_is_needed(home, bridge, capsys):
    token = bridge.setup_token()
    sf.exchange(token)                                          # spent elsewhere first
    assert _run(home, answers(feed={"token": token, **FEED_ANSWERS})) == 2
    err = capsys.readouterr().err
    assert "already claimed" in err and "make a new setup token" in err and token not in err
    assert not (home / "secrets" / "feed" / "simplefin_access_url").exists()
    assert not (home / "config" / "accounts.yaml").exists()


def test_a_bridge_that_is_down_leaves_no_credential_and_says_the_token_may_still_be_unspent(home, monkeypatch,
                                                                                          capsys):
    for name in PROXIES:
        monkeypatch.delenv(name, raising=False)
    with fake_bridge.serve() as gone:
        token = gone.setup_token()
        monkeypatch.setenv(sf.TEST_ORIGIN_VARIABLE, gone.origin)
    assert _run(home, answers(feed={"token": token, **FEED_ANSWERS})) == 2
    err = capsys.readouterr().err
    assert "may still be unspent" in err and token not in err
    assert not (home / "secrets" / "feed" / "simplefin_access_url").exists()


def test_a_write_that_fails_after_the_claim_says_the_token_is_spent_without_a_traceback(home, bridge, monkeypatch,
                                                                                       capsys):
    def full(fd, data):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(setup_cli, "_write_url", full)
    token = bridge.setup_token()
    assert _run(home, answers(feed={"token": token, **FEED_ANSWERS})) == 2
    err = capsys.readouterr().err
    assert err == ("init: the setup token was used, but its access could not be saved (No space left on device): the "
                   "token cannot be used again; make a new setup token at the bank feed's provider, then run init "
                   "again\n")
    assert not (home / "secrets" / "feed" / "simplefin_access_url").exists()
    with pytest.raises(SystemExit):
        sf.exchange(token)                                  # spent at the bridge: no second claim succeeds


def test_ctrl_c_right_after_the_claim_still_saves_the_access(home, bridge, monkeypatch, capsys):
    real = sf.exchange

    def exchanged_then_interrupted(token):
        url = real(token)
        os.kill(os.getpid(), signal.SIGINT)                 # pressed as the answer arrives
        return url
    monkeypatch.setattr(sf, "exchange", exchanged_then_interrupted)
    assert _run(home, answers(feed={"token": bridge.setup_token(), **FEED_ANSWERS})) == 130
    err = capsys.readouterr().err
    cred = home / "secrets" / "feed" / "simplefin_access_url"
    assert cred.read_text().startswith("http://") and stat.S_IMODE(os.stat(cred).st_mode) == 0o600
    assert "finishing the bank feed's connection first" in err
    assert f"the setup token was used, and its access is saved in {cred}" in err
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    monkeypatch.setattr(sf, "exchange", real)
    assert _run(home, answers(feed={"token": "spent", **FEED_ANSWERS})) == 0     # resumes, no token needed
    assert len([r for r in bridge.received if r.method == "POST"]) == 1


def test_a_system_error_after_the_claim_says_where_the_access_is(home, bridge, monkeypatch, capsys):
    real = setup_cli._write_yaml

    def full(path, header, doc):
        if path.name == "accounts.yaml":
            raise OSError(errno.ENOSPC, "No space left on device", str(path))
        real(path, header, doc)
    monkeypatch.setattr(setup_cli, "_write_yaml", full)
    assert _run(home, answers(feed={"token": bridge.setup_token(), **FEED_ANSWERS})) == 2
    err = capsys.readouterr().err
    assert "No space left on device" in err and "Traceback" not in err
    assert "the setup token was used, and its access is saved in" in err


def test_offline_no_token_is_asked_or_taken(home, bridge, monkeypatch, capsys):
    out = []
    assert _run(home, answers(), offline=True, out=out.append) == 0
    assert any("the bank feed is not connected now" in line for line in out)
    assert bridge.received == []
    other = new_home(home.parent, monkeypatch, "other")
    assert _run(other, answers(feed={"token": bridge.setup_token(), **FEED_ANSWERS}), offline=True) == 2
    assert "this run was not given the network" in capsys.readouterr().err
    assert bridge.received == [] and not (other / "secrets" / "feed" / "simplefin_access_url").exists()


def test_offline_a_connection_without_a_snapshot_cannot_be_listed(home, bridge, monkeypatch, capsys):
    install.ensure_dirs(home, out=lambda _: None)
    cred = home / "secrets" / "feed" / "simplefin_access_url"
    cred.write_text(bridge.credential()[2])
    os.chmod(cred, 0o600)
    assert _run(home, answers(feed=FEED_ANSWERS), offline=True) == 2
    assert "needs the network" in capsys.readouterr().err
    assert _run(home, answers(feed=FEED_ANSWERS)) == 0     # with the network it lists them (one fetch)
    (home / "config" / "accounts.yaml").unlink()
    assert _run(home, answers(feed=FEED_ANSWERS), offline=True) == 0     # the snapshot is read again offline
    assert len(bridge.gets()) == 1


def test_an_empty_credential_left_by_a_claim_that_never_finished_is_removed_and_the_token_asked_again(home, bridge):
    install.ensure_dirs(home, out=lambda _: None)
    cred = home / "secrets" / "feed" / "simplefin_access_url"
    cred.touch(mode=0o600)
    out = []
    assert _run(home, answers(feed={"token": bridge.setup_token(), **FEED_ANSWERS}), out=out.append) == 0
    assert any("empty bank feed credential" in line for line in out)
    assert cred.read_text().startswith("http://")


def test_a_token_pasted_twice_is_spent_once(home, bridge):
    token = bridge.setup_token()
    doc = answers(feed={"token": token, **FEED_ANSWERS})
    assert _run(home, doc) == 0
    assert _run(home, doc, force=True) == 0
    assert len([r for r in bridge.received if r.method == "POST"]) == 1
    assert len(bridge.gets()) == 1                    # the second run reads the stored snapshot again


def test_the_token_and_the_passcode_are_never_printed(home, bridge, capsys):
    token = bridge.setup_token()
    out = []
    assert _run(home, answers(feed={"token": token, **FEED_ANSWERS}), out=out.append) == 0
    captured = capsys.readouterr()
    said = "\n".join(out) + captured.out + captured.err
    url = (home / "secrets" / "feed" / "simplefin_access_url").read_text()
    for secret in (token, PASSCODE, url, urllib.parse.urlsplit(url).password):
        assert secret not in said


# ------------------------------------------------------------------ on a terminal

def test_the_conversation_on_a_terminal_writes_the_same_household(home, bridge, monkeypatch):
    from etl import passcode_cli
    (home / "imports").mkdir()
    (home / "imports" / SAMPLE_CSV.name).write_bytes(SAMPLE_CSV.read_bytes())
    typed = iter([
        "The Samples", "Alex, Sam", "", "", "Mars/Olympus", "America/New_York", "n",   # household (a bad zone again)
        "2", "",                                                                        # home network, port 8080
        "1", "",                                    # checking, Joint (Enter)
        "3", "1",                                   # credit card, Alex
        "6",                                        # the loan: skipped
        "1",                                        # the files are the feed's checking account
    ])
    secrets = iter([bridge.setup_token(), PASSCODE, PASSCODE])
    monkeypatch.setattr(passcode_cli.getpass, "getpass", lambda prompt="": next(secrets))
    monkeypatch.setattr(passcode_cli.sys.stdin, "isatty", lambda: True)
    out = []
    rc = run(None, out=out.append, ask=lambda q: next(typed), secret=lambda q: next(secrets))
    assert rc == 0, "\n".join(out)
    assert next(typed, None) is None and next(secrets, None) is None
    assert any("not a time zone name" in line for line in out)
    assert any(setup_cli.NETWORK_WARNING == line for line in out)
    accounts = _cfg(home).accounts
    assert sorted(m["kind"] for m in accounts.values()) == ["checking", "credit-card"]
    checking = next(m for m in accounts.values() if m["kind"] == "checking")
    assert checking["owner"] == "Joint" and checking["parser"] == "chase_checking" and "simplefin_cutover" in checking
    assert install.read_env(home)["FF_BIND"] == "0.0.0.0"


def test_without_a_terminal_or_an_answers_file_it_says_how_to_run(home, monkeypatch, capsys):
    monkeypatch.setattr(setup_cli.sys.stdin, "isatty", lambda: False)
    assert run(None) == 2
    assert "--answers" in capsys.readouterr().err


def test_ctrl_c_at_a_prompt_keeps_what_was_answered(home, capsys):
    typed = iter(["The Samples", "Alex"])

    def ask(q):
        try:
            return next(typed)
        except StopIteration:
            raise KeyboardInterrupt from None
    assert run(None, out=lambda _: None, ask=ask) == 130
    assert "run ./ff init again" in capsys.readouterr().err
    assert not (home / "config" / "household.yaml").exists()


# ------------------------------------------------------------------ pieces

@pytest.mark.parametrize("name, label", [("Alex", "alex"), ("Zoë Ann", "zoe-ann"), ("李", "family")])
def test_the_passcode_label_is_the_first_adults_name_in_the_label_alphabet(name, label):
    assert setup_cli.label_for(name) == label


@pytest.mark.parametrize("name, kind", [("Sample Feed Checking 0000", "checking"), ("Everyday Savings", "savings"),
                                        ("Sample Visa Card 1111", "credit-card"), ("Roth IRA", "brokerage"),
                                        ("Sample Home Mortgage", "loan"), ("Account 0000", "checking")])
def test_a_feed_accounts_kind_is_proposed_from_its_name(name, kind):
    assert setup_cli._hinted_kind(name, "Sample Bank") == kind

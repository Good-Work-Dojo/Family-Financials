# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The bank feed's module (etl/simplefin.py) against a SimpleFIN Bridge on 127.0.0.1 (tests/fake_bridge.py), through
its real claim, fetch and list_accounts and its one test seam (FF_SIMPLEFIN_TEST_ORIGIN, which lets exactly one loopback
origin be reached over plain HTTP and nothing else).

A claim stores its credential at mode 600 before anything uses it and never overwrites one without force; a second
claim of a token fails in one line. A fetch asks for protocol version 2 and never for pending rows; with no snapshot
stored it asks for 90 days, later for the scheduled 35, and an explicit window always wins; it stores the answer byte
for byte as a new snapshot named for the time it was fetched. A 402 and a 403 are each one line naming neither the
credential nor the access URL; an errlist entry is the `WARN simplefin` line deploy/refresh.sh keys on; a stall is
retried by the module's policy and then fails in its one line. A redirect is never followed and the seam's plain-HTTP
request never goes through a proxy the environment names (a second listener sees no request), while an https request
keeps that proxy. A claim URL the module refuses is named by its host alone. The stored snapshot is read by the feed's
reader, and an offline ingest of a household built on it opens no socket. Everything here is invented: ids, names and
hosts."""
from __future__ import annotations

import base64
import datetime as dt
import json
import shutil
import socket
import ssl
import sys
import time
import urllib.parse
from pathlib import Path

import fake_bridge
import pytest
import yaml
from conftest import ROOT
from fake_bridge import Account

from etl import pipeline
from etl import simplefin as sf
from etl.__main__ import main
from etl.config import Config, FeedConfig
from etl.parsers import simplefin as feed_parser

FEED, FEED_ID = "demo_feed_0000", "ACT-fake-0001"
DAY = 86_400
PROXIES = ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY")


@pytest.fixture
def bridge(monkeypatch):
    """The fake bridge, running for the test, with the seam set to its origin and no proxy in the way"""
    for name in PROXIES:
        monkeypatch.delenv(name, raising=False)
    with fake_bridge.serve() as running:
        monkeypatch.setenv(sf.TEST_ORIGIN_VARIABLE, running.origin)
        yield running


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """The smallest household plus a checking account the bank feed reports, with FF_HOME at it"""
    home = tmp_path / "home"
    shutil.copytree(ROOT / "examples" / "minimal", home)
    accounts = home / "config" / "accounts.yaml"
    doc = yaml.safe_load(accounts.read_text("utf-8"))
    doc["accounts"][FEED] = {"name": "Sample Feed Checking", "institution": "Sample Bank", "owner": "Alex",
                             "kind": "checking", "simplefin_id": FEED_ID}
    accounts.write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")
    monkeypatch.setenv("FF_HOME", str(home))
    return home


def _feed(bridge: fake_bridge.FakeBridge, now: int) -> None:
    """One account: rows 5, 20, 60 and 120 days old and a pending one"""
    bridge.accounts = [Account(FEED_ID, "Sample Feed Checking 0000", "1234.56", now - DAY, transactions=[
        {"id": "TRN-0001", "posted": now - 5 * DAY, "transacted_at": now - 6 * DAY, "amount": "-54.18",
         "description": "TRADER JOE'S #000 SAMPLETOWN"},
        {"id": "TRN-0002", "posted": now - 20 * DAY, "amount": "-6.45", "description": "STARBUCKS STORE 00000"},
        {"id": "TRN-0003", "posted": now - 60 * DAY, "amount": "2500.00", "description": "SAMPLE EMPLOYER PAYROLL"},
        {"id": "TRN-0004", "posted": now - 120 * DAY, "amount": "-89.95", "description": "JIFFY LUBE #0000"},
        {"id": "TRN-0005", "posted": 0, "pending": True, "transacted_at": now - DAY, "amount": "-12.00",
         "description": "SAMPLE PENDING CAFE"},
    ])]


def _claimed(bridge: fake_bridge.FakeBridge) -> tuple[FeedConfig, str, str]:
    """The household's feed config with a credential claimed from the bridge, its user and password, and the feed"""
    cfg = FeedConfig()
    sf.claim(cfg, bridge.setup_token())
    parts = urllib.parse.urlsplit(cfg.simplefin_credential_path.read_text())
    _feed(bridge, int(time.time()))
    return cfg, parts.username, parts.password


def _ids(body: bytes) -> list[list[str]]:
    return [[t["id"] for t in a.get("transactions", [])] for a in json.loads(body)["accounts"]]


def _never_opened(monkeypatch) -> None:
    def opened(*args, **kwargs):
        raise AssertionError("a request was sent")
    for seam in (False, True):
        monkeypatch.setattr(sf._opener(seam=seam), "open", opened)


# ------------------------------------------------------------------ the seam

def test_without_the_seam_plain_http_is_refused_before_anything_is_sent(bridge, home, monkeypatch):
    monkeypatch.delenv(sf.TEST_ORIGIN_VARIABLE)
    cfg = FeedConfig()
    with pytest.raises(SystemExit) as caught:
        sf._request(f"{bridge.origin}/simplefin/accounts")
    assert str(caught.value) == "SimpleFIN is reached over https only: refusing 127.0.0.1"
    with pytest.raises(SystemExit) as caught:
        sf.claim(cfg, bridge.setup_token())
    assert str(caught.value).startswith("Decoded claim URL looks wrong: ")
    user, password, url = bridge.credential()
    sf.write_access_url(cfg.simplefin_credential_path, url)
    with pytest.raises(SystemExit) as caught:
        sf.fetch(cfg)
    assert str(caught.value) == "SimpleFIN is reached over https only: refusing 127.0.0.1"
    assert password not in str(caught.value) and user not in str(caught.value)
    assert bridge.received == []


@pytest.mark.parametrize("address", [
    "http://127.0.0.1:{other}/simplefin/accounts",            # another port
    "http://localhost:{port}/simplefin/accounts",             # the loopback host by name
    "http://127.0.0.2:{port}/simplefin/accounts",             # another loopback address
    "http://[::1]:{port}/simplefin/accounts",
    "http://10.0.0.1:{port}/simplefin/accounts",              # no loopback address at all
    "http://127.0.0.1:{port}@10.0.0.1/simplefin/accounts",    # the origin written as a user and password
    "http://127.0.0.1/simplefin/accounts",                    # no port
    "ftp://127.0.0.1:{port}/simplefin/accounts",
], ids=["port", "localhost", "127.0.0.2", "ipv6", "lan", "userinfo", "no-port", "ftp"])
def test_the_seam_lets_through_its_one_origin_and_nothing_else(bridge, monkeypatch, address):
    port = int(bridge.origin.rsplit(":", 1)[1])
    url = address.format(port=port, other=port + 1 if port < 65535 else port - 1)
    _never_opened(monkeypatch)
    with pytest.raises(SystemExit) as caught:
        sf._request(url)
    assert str(caught.value).startswith("SimpleFIN is reached over https only: refusing ")
    assert bridge.received == []


@pytest.mark.parametrize("origin", [
    "http://10.0.0.1:8080", "http://192.168.0.10:8080", "http://localhost:8080", "http://127.0.0.2:8080",
    "http://[::1]:8080", "http://0.0.0.0:8080", "http://127.0.0.1", "http://127.0.0.1:8080/simplefin",
    "http://user@127.0.0.1:8080", "http://127.0.0.1:99999", "127.0.0.1:8080", "http://127.0.0.1:8080 ",
])
def test_the_variable_cannot_allow_any_origin_but_a_loopback_one(monkeypatch, origin):
    """A value that is not exactly http://127.0.0.1:<port> opens nothing: a request to the origin it names is refused
    as any plain http one is"""
    monkeypatch.setenv(sf.TEST_ORIGIN_VARIABLE, origin)
    _never_opened(monkeypatch)
    target = origin.strip() if origin.strip().startswith("http://") else f"http://{origin}"
    with pytest.raises(SystemExit) as caught:
        sf._request(f"{target}/simplefin/accounts")
    assert str(caught.value).startswith("SimpleFIN is reached over https only: refusing ")


def test_https_is_reached_whatever_the_variable_says(monkeypatch):
    """The seam adds one origin; it takes nothing away. Every request goes through the module's one opener, whose TLS
    is verified as always"""
    seen = []
    opener = sf._opener()
    assert sf._opener() is opener
    [https] = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]
    assert (https._context.verify_mode, https._context.check_hostname) == (ssl.CERT_REQUIRED, True)

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    def opened(req, timeout=None):
        seen.append((req.full_url, req.get_header("Authorization")))
        return Answer()
    monkeypatch.setattr(opener, "open", opened)
    for value in (None, "http://127.0.0.1:8080"):
        if value is None:
            monkeypatch.delenv(sf.TEST_ORIGIN_VARIABLE, raising=False)
        else:
            monkeypatch.setenv(sf.TEST_ORIGIN_VARIABLE, value)
        assert sf._request("https://u:p@bridge.example.invalid/simplefin/accounts") == b"{}"
    assert seen == [("https://bridge.example.invalid/simplefin/accounts", sf._basic_auth("u", "p"))] * 2


@pytest.fixture
def elsewhere():
    """A second listener on 127.0.0.1 at another port: where a followed redirect or a proxy would send a request"""
    with fake_bridge.serve() as running:
        yield running


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("target", ["another-origin", "its-own-origin"])
def test_a_redirect_is_never_followed_and_the_credential_goes_nowhere_else(bridge, elsewhere, home, capsys, status,
                                                                          target):
    """A 30x to the claim or to /accounts fails the request in one line, never retried, and nothing is sent to where
    it points, not even to the bridge's own origin"""
    origin = elsewhere.origin if target == "another-origin" else bridge.origin
    cfg = FeedConfig()
    token = bridge.setup_token()
    bridge.redirect(f"{origin}/simplefin/claim/elsewhere", status)
    with pytest.raises(SystemExit) as caught:
        sf.claim(cfg, token)
    assert str(caught.value) == (f"SimpleFIN answered POST /simplefin/claim with a redirect (HTTP {status}), which "
                                 f"is not followed")
    assert not cfg.simplefin_credential_path.exists()
    user, password, url = bridge.credential()
    sf.write_access_url(cfg.simplefin_credential_path, url)
    bridge.redirect(f"{origin}/simplefin/accounts", status)
    with pytest.raises(SystemExit) as caught:
        sf.fetch(cfg)
    assert str(caught.value) == (f"SimpleFIN answered GET /simplefin/accounts with a redirect (HTTP {status}), which "
                                 f"is not followed")
    assert [(r.method, r.path.rsplit("/", 1)[0], r.user) for r in bridge.received] == [
        ("POST", "/simplefin/claim", None), ("GET", "/simplefin", user)]
    assert elsewhere.received == [] and cfg.find_simplefin_snapshots() == []
    printed = capsys.readouterr()
    assert password not in str(caught.value) + printed.out + printed.err


def test_an_https_request_keeps_the_proxy_the_environment_names(monkeypatch):
    """A household behind a proxy reaches the bridge through it: the https opener holds the environment's proxy, as
    urllib's own default does, while the seam's opener holds none"""
    for name in PROXIES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.invalid:3128")
    sf._built.cache_clear()             # the openers are built while the variable is set, as in a process that has it
    try:
        [https] = [h for h in sf._opener().handlers if isinstance(h, urllib.request.ProxyHandler)]
        # the seam's empty proxy handler opens nothing, so the opener holds none: no request of it is proxied
        seam = [h for h in sf._opener(seam=True).handlers if isinstance(h, urllib.request.ProxyHandler)]
    finally:
        sf._built.cache_clear()
    assert https.proxies.get("https") == "http://proxy.example.invalid:3128"
    assert seam == []


def test_a_proxy_the_environment_names_is_not_used_for_the_seam(bridge, elsewhere, home, monkeypatch):
    """With every proxy variable naming another listener, the claim and the fetch still go to the seam's origin alone:
    a plain-HTTP request and its credential never pass through a proxy"""
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, elsewhere.origin)
    # the openers are built while the variables are set, as at the start of a process that has them (a proxy handler
    # reads them when it is built)
    sf._built.cache_clear()
    try:
        cfg, user, _ = _claimed(bridge)
        sf.fetch(cfg)
    finally:
        sf._built.cache_clear()
    assert [(r.method, r.user) for r in bridge.received] == [("POST", None), ("GET", user)]
    assert elsewhere.received == []


def test_the_variable_is_read_in_one_place():
    sources = {p.relative_to(ROOT).as_posix(): p.read_text("utf-8")
               for d in ("etl", "deploy") for p in (ROOT / d).rglob("*.py")}
    assert {name for name, text in sources.items() if sf.TEST_ORIGIN_VARIABLE in text} == {"etl/simplefin.py"}
    module = sources["etl/simplefin.py"]
    assert module.count("os.environ") == module.count("os.environ.get(TEST_ORIGIN_VARIABLE") == 1


# ------------------------------------------------------------------ claim

def test_claim_stores_the_credential_at_600_before_anything_uses_it_and_never_overwrites_it(bridge, home, monkeypatch,
                                                                                            capsys):
    cfg = FeedConfig()
    at_write = []
    write = sf.write_access_url

    def recorded(path, url):
        at_write.append(list(bridge.received))
        write(path, url)
        at_write.append(oct(path.stat().st_mode & 0o777))
    monkeypatch.setattr(sf, "write_access_url", recorded)
    assert sf.claim(cfg, bridge.setup_token()) == cfg.simplefin_credential_path
    received, mode = at_write
    assert [(r.method, r.path.startswith("/simplefin/claim/")) for r in received] == [("POST", True)]
    assert mode == "0o600" and bridge.received == received            # a claim never uses the access URL
    url = cfg.simplefin_credential_path.read_text()
    user, password = urllib.parse.urlsplit(url).username, urllib.parse.urlsplit(url).password
    out = capsys.readouterr()
    assert out.out == f"Claimed. Access URL (127.0.0.1) stored at {cfg.simplefin_credential_path} (mode 600).\n"
    assert password not in out.out + out.err
    # a stored credential is never overwritten without force, and the new token is not spent
    with pytest.raises(SystemExit) as caught:
        sf.claim(cfg, bridge.setup_token())
    assert str(caught.value).startswith(f"Credential already exists at {cfg.simplefin_credential_path}. ")
    assert "\n" not in str(caught.value) and len(bridge.received) == 1
    assert cfg.simplefin_credential_path.read_text() == url
    # the first request that uses it is the fetch's, as the claimed user
    _feed(bridge, int(time.time()))
    sf.fetch(cfg)
    assert [r.user for r in bridge.gets()] == [user]


def test_a_second_claim_of_one_token_fails_in_one_line_and_keeps_the_credential(bridge, home, capsys):
    cfg = FeedConfig()
    token = bridge.setup_token()
    sf.claim(cfg, token)
    stored = cfg.simplefin_credential_path.read_text()
    with pytest.raises(SystemExit) as caught:
        sf.claim(cfg, token, force=True)
    claim_id = urllib.parse.urlsplit(base64.b64decode(token).decode()).path.rsplit("/", 1)[1]
    # the message names the claim's path without its last segment, the token's own id
    assert str(caught.value) == "SimpleFIN HTTP 403 for POST /simplefin/claim: Forbidden"
    assert claim_id not in str(caught.value)
    assert [r.method for r in bridge.received] == ["POST", "POST"]     # a claim is never retried
    assert cfg.simplefin_credential_path.read_text() == stored
    assert urllib.parse.urlsplit(stored).password not in capsys.readouterr().out


@pytest.mark.parametrize("claim_url", [
    "http://fakeuser:fake-pw@bridge.example.invalid/simplefin/claim/FAKESETUPTOKEN0000",
    "ftp://fakeuser:fake-pw@bridge.example.invalid/claim/FAKESETUPTOKEN0000",
    "https:///simplefin/claim/FAKESETUPTOKEN0000",
], ids=["http", "ftp", "no-host"])
def test_a_claim_url_the_module_refuses_is_named_by_its_host_alone(home, monkeypatch, capsys, claim_url):
    """A setup token that decodes to an address the module does not reach: refused before anything is sent, in one
    line naming the host only, never the token (still unspent) nor a user or password"""
    monkeypatch.delenv(sf.TEST_ORIGIN_VARIABLE, raising=False)
    _never_opened(monkeypatch)
    cfg = FeedConfig()
    with pytest.raises(SystemExit) as caught:
        sf.claim(cfg, base64.b64encode(claim_url.encode()).decode())
    host = urllib.parse.urlsplit(claim_url).hostname
    assert str(caught.value) == f"Decoded claim URL looks wrong: {host or 'an address with no host'}"
    printed = capsys.readouterr()
    for secret in ("FAKESETUPTOKEN0000", "fakeuser", "fake-pw"):
        assert secret not in str(caught.value) + printed.out + printed.err
    assert not cfg.simplefin_credential_path.exists()


def test_an_access_url_the_seam_does_not_allow_is_refused_without_its_password(bridge, home, monkeypatch):
    """The claim answers an access URL on another origin: refused, and the words carry no user or password"""
    cfg = FeedConfig()
    token = bridge.setup_token()
    monkeypatch.setattr(bridge, "_access_url", lambda user, password: f"http://{user}:{password}@localhost:1/simplefin")
    with pytest.raises(SystemExit) as caught:
        sf.claim(cfg, token)
    assert str(caught.value) == "Claim did not return an access URL: 'http://localhost:1/simplefin'"
    assert not cfg.simplefin_credential_path.exists()


# ------------------------------------------------------------------ fetch

def test_fetch_stores_the_answer_byte_for_byte_as_a_new_snapshot_named_for_its_time(bridge, home):
    cfg, _, _ = _claimed(bridge)
    before = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    out = sf.fetch(cfg)
    after = dt.datetime.now(dt.timezone.utc)
    assert out.parent == cfg.simplefin_snapshots_dir and out.read_bytes() == bridge.sent[-1]
    stamp = dt.datetime.strptime(out.name, "accounts-%Y%m%dT%H%M%SZ.json").replace(tzinfo=dt.timezone.utc)
    assert before <= stamp <= after
    # another fetch at the same second is another file, and the first is never touched
    first = out.read_bytes()
    again = sf.fetch(cfg, end=stamp)
    assert again.name == out.name.replace(".json", "-1.json") and again.read_bytes() == bridge.sent[-1]
    assert out.read_bytes() == first and set(cfg.find_simplefin_snapshots()) == {out, again}


def test_every_request_asks_for_version_2_and_the_window_the_store_calls_for(bridge, home):
    """No snapshot stored: 90 days. One stored: the scheduled 35. --days 10: ten. Pending rows are never asked for."""
    cfg, _, _ = _claimed(bridge)
    sf.fetch(cfg)
    sf.fetch(cfg)
    sf.fetch(cfg, days=10)
    gets = bridge.gets()
    assert [(r.query["version"], int(r.query["end-date"][0]) - int(r.query["start-date"][0])) for r in gets] == [
        (["2"], 90 * DAY), (["2"], 35 * DAY), (["2"], 10 * DAY)]
    assert all(set(r.query) == {"version", "start-date", "end-date"} for r in gets)
    assert [_ids(body) for body in bridge.sent] == [[["TRN-0001", "TRN-0002", "TRN-0003"]],
                                                    [["TRN-0001", "TRN-0002"]], [["TRN-0001"]]]


def test_a_window_over_90_days_is_never_asked_for(bridge, home, capsys):
    cfg, _, _ = _claimed(bridge)
    sf.fetch(cfg, days=120)
    [get] = bridge.gets()
    assert int(get.query["end-date"][0]) - int(get.query["start-date"][0]) == 90 * DAY
    assert "WARN simplefin" not in capsys.readouterr().err


def test_the_cli_leaves_the_window_to_the_module_unless_days_is_given(home, monkeypatch):
    windows = []
    monkeypatch.setattr(sf, "fetch", lambda cfg, days=None, end=None: windows.append(days))
    for argv in (["simplefin", "fetch"], ["simplefin", "fetch", "--days", "10"]):
        monkeypatch.setattr(sys, "argv", ["etl", *argv])
        assert main() == 0
    assert windows == [None, 10]


@pytest.mark.parametrize("case", ["wrong-password", "revoked", "lapsed"])
def test_402_and_403_are_one_line_with_no_credential_in_it(bridge, home, capsys, case):
    cfg = FeedConfig()
    user, password, url = bridge.credential()
    if case == "wrong-password":
        url = url.replace(password, "fake-wrong-password")
    elif case == "revoked":
        bridge.revoke(user)
    else:
        bridge.lapse(user)
    sf.write_access_url(cfg.simplefin_credential_path, url)
    _feed(bridge, int(time.time()))
    with pytest.raises(SystemExit) as caught:
        sf.fetch(cfg)
    assert str(caught.value) == ("SimpleFIN HTTP 402 for GET /simplefin/accounts: Payment required" if case == "lapsed"
                                 else "SimpleFIN HTTP 403 for GET /simplefin/accounts: Authentication failed")
    assert len(bridge.gets()) == 1                                    # never retried
    assert cfg.find_simplefin_snapshots() == []
    out = capsys.readouterr()
    for secret in (password, "fake-wrong-password", url):
        assert secret not in str(caught.value) + out.out + out.err


def test_an_errlist_entry_is_the_warn_line_the_refresh_script_keys_on(bridge, home, capsys):
    cfg, _, password = _claimed(bridge)
    capsys.readouterr()
    bridge.errlist = [{"code": "con.auth", "msg": "Sample Bank needs you to sign in again", "conn_id": "CON-fake-0001"}]
    bridge.accounts.append(Account("ACT-fake-0002", "Sample Savings 0000", "10.00", int(time.time()), failing=True))
    out = sf.fetch(cfg)
    printed = capsys.readouterr()
    # each names its connection in the version-1 wording the refresh's re-auth alert reads its names from
    assert printed.err.splitlines() == [
        "  WARN simplefin: Connection to Sample Bank may need attention. [act.failed] Failed to get all transactions. "
        "Try again later. ACT-fake-0002",
        "  WARN simplefin: Connection to Sample Bank may need attention. [con.auth] Sample Bank needs you to sign in "
        "again CON-fake-0001"]
    assert out.read_bytes() == bridge.sent[-1]                         # the answer is still stored
    assert password not in printed.out + printed.err


def test_a_stall_is_retried_by_the_modules_policy_then_fails_in_its_one_line(bridge, home, capsys, monkeypatch):
    cfg, _, _ = _claimed(bridge)
    capsys.readouterr()
    monkeypatch.setattr(sf, "TIMEOUT", 0.3)
    waits = []
    monkeypatch.setattr(sf.time, "sleep", waits.append)
    bridge.hold(30, 3)
    with pytest.raises(SystemExit) as caught:
        sf.fetch(cfg)
    assert str(caught.value) == "SimpleFIN request timed out after 0.3s for GET /simplefin/accounts (3 attempts)"
    assert waits == list(sf._RETRY_WAITS) and len(bridge.gets()) == 3
    err = capsys.readouterr().err
    assert err.count("  INFO simplefin: GET /simplefin/accounts failed (TimeoutError); retrying in ") == 2
    assert "WARN simplefin" not in err and cfg.find_simplefin_snapshots() == []


def test_one_stall_costs_one_retry(bridge, home, capsys, monkeypatch):
    cfg, _, _ = _claimed(bridge)
    monkeypatch.setattr(sf, "TIMEOUT", 0.3)
    monkeypatch.setattr(sf.time, "sleep", lambda seconds: None)
    bridge.hold(30, 1)
    out = sf.fetch(cfg)
    assert len(bridge.gets()) == 2 and out.read_bytes() == bridge.sent[-1]


def test_a_rate_limit_answer_costs_one_request_and_is_not_retried(bridge, home, capsys, monkeypatch):
    """The bridge's request budget is the scarce thing: a 429 is not asked again within seconds; the next scheduled
    refresh asks."""
    cfg, _, _ = _claimed(bridge)
    monkeypatch.setattr(sf.time, "sleep", lambda seconds: pytest.fail("a rate-limited request was retried"))
    bridge.answer(429, b"Too many requests", requests=3)
    with pytest.raises(SystemExit) as caught:
        sf.fetch(cfg)
    assert str(caught.value) == "SimpleFIN HTTP 429 for GET /simplefin/accounts: Too many requests"
    assert len(bridge.gets()) == 1 and cfg.find_simplefin_snapshots() == []


def test_an_answer_with_no_account_is_not_stored_and_the_next_fetch_still_asks_for_90_days(bridge, home, capsys):
    """A day-one re-authentication answers its errors and no account: the errors are printed, nothing is stored, and
    the next fetch, which has still no answer holding an account, asks for the whole 90 days."""
    cfg, _, _ = _claimed(bridge)
    capsys.readouterr()
    empty = {"errlist": [{"code": "con.auth", "msg": "Sign in again", "conn_id": "CON-fake-0001"}], "accounts": []}
    bridge.answer(200, json.dumps(empty).encode())
    assert sf.fetch(cfg) is None
    printed = capsys.readouterr()
    assert printed.err.splitlines() == [
        "  WARN simplefin: Connection to CON-fake-0001 may need attention. [con.auth] Sign in again CON-fake-0001"]
    assert printed.out == "Fetched 0 accounts (90d window): nothing stored\n"
    assert cfg.find_simplefin_snapshots() == []
    out = sf.fetch(cfg)
    assert out.read_bytes() == bridge.sent[-1]
    assert [int(r.query["end-date"][0]) - int(r.query["start-date"][0]) for r in bridge.gets()] == [90 * DAY, 90 * DAY]


@pytest.mark.parametrize("stored", ['{"errlist": [], "accounts": []}', '{"accounts": ['],
                         ids=["no-account", "damaged"])
def test_a_stored_answer_holding_no_account_does_not_shorten_the_window(bridge, home, stored):
    """The 90-or-35-day choice looks for a stored answer that holds an account: one stored with none (by an earlier
    release) or a damaged one leaves the next fetch at 90 days; once an answer with an account is stored, 35."""
    cfg, _, _ = _claimed(bridge)
    cfg.simplefin_snapshots_dir.mkdir(parents=True, exist_ok=True)
    (cfg.simplefin_snapshots_dir / "accounts-20300101T000000Z.json").write_text(stored)
    sf.fetch(cfg)
    sf.fetch(cfg)
    assert [int(r.query["end-date"][0]) - int(r.query["start-date"][0]) for r in bridge.gets()] == [90 * DAY, 35 * DAY]


@pytest.mark.parametrize("body", [b'{"accounts": null}', b'{"accounts": "x"}', b'{"accounts": ["x"]}', b"[]",
                                  b'{"errlist": []}'],
                         ids=["null", "text", "list-of-text", "a-list", "no-accounts-key"])
def test_an_answer_not_laid_out_as_the_protocols_is_refused_in_one_line_and_not_stored(bridge, home, body):
    cfg, _, _ = _claimed(bridge)
    bridge.answer(200, body)
    with pytest.raises(SystemExit) as caught:
        sf.fetch(cfg)
    assert str(caught.value) == "SimpleFIN answered without a list of accounts; not storing."
    assert cfg.find_simplefin_snapshots() == []


def test_a_write_that_fails_part_way_leaves_no_snapshot_and_no_stray_file(bridge, home, monkeypatch):
    """The answer is written to a hidden file and linked under its name only once it is whole: a full disk or a kill
    mid-write never leaves a half-written answer for every later ingest to stumble on."""
    cfg, _, _ = _claimed(bridge)

    def full_disk(fd):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(sf.os, "fsync", full_disk)
    with pytest.raises(SystemExit) as caught:
        sf.fetch(cfg)
    assert str(caught.value) == "SimpleFIN answer could not be stored (No space left on device); nothing was stored."
    assert list(cfg.simplefin_snapshots_dir.iterdir()) == []


# ------------------------------------------------------------------ the protocol as the fake answers it

def _raw(cfg: FeedConfig, **query: str) -> dict:
    """The bridge's answer to an /accounts request of `query`, through the module's own transport"""
    url = cfg.simplefin_credential_path.read_text()
    return json.loads(sf._request(f"{url}/accounts?{urllib.parse.urlencode(query)}"))


def test_the_fake_answers_the_protocol_the_module_reads(bridge, home):
    """version=2 changes the error shape and names the connection; pending rows come only when asked for; balances
    only leaves every row out; a window over 90 days is answered for its last 90 with an entry saying so (the fake's
    choice: the protocol states no limit)"""
    cfg, _, _ = _claimed(bridge)
    now = int(time.time())
    window = {"start-date": str(now - 30 * DAY), "end-date": str(now)}
    v1 = _raw(cfg, **window)
    assert set(v1) == {"errors", "accounts"} and v1["accounts"][0]["org"]["name"] == "Sample Bank"
    v2 = _raw(cfg, version="2", **window)
    assert set(v2) == {"errlist", "connections", "accounts"} and "org" not in v2["accounts"][0]
    assert v2["accounts"][0]["conn_id"] == v2["connections"][0]["conn_id"]
    assert [t["id"] for t in v2["accounts"][0]["transactions"]] == ["TRN-0001", "TRN-0002"]
    pending = _raw(cfg, version="2", pending="1", **window)
    assert [t["id"] for t in pending["accounts"][0]["transactions"]] == ["TRN-0001", "TRN-0002", "TRN-0005"]
    assert "transactions" not in _raw(cfg, version="2", **{"balances-only": "1"}, **window)["accounts"][0]
    wide = _raw(cfg, version="2", **{"start-date": str(now - 200 * DAY), "end-date": str(now)})
    assert [t["id"] for t in wide["accounts"][0]["transactions"]] == ["TRN-0001", "TRN-0002", "TRN-0003"]
    assert wide["errlist"] == [{"code": "gen.", "msg": fake_bridge.CLAMPED}]
    assert _raw(cfg, **{"start-date": str(now - 200 * DAY), "end-date": str(now)})["errors"] == [fake_bridge.CLAMPED]


# ------------------------------------------------------------------ what the store then feeds

def test_list_accounts_names_the_connection_of_a_version_2_snapshot(bridge, home, capsys):
    cfg, _, _ = _claimed(bridge)
    sf.fetch(cfg)
    capsys.readouterr()
    sf.list_accounts(cfg)
    [row] = [line for line in capsys.readouterr().out.splitlines() if line.startswith(FEED_ID)]
    assert row.split() == [FEED_ID, "Sample", "Bank", "Sample", "Feed", "Checking", "0000", "1234.56", FEED,
                           "(mapped)"]


def test_list_accounts_says_what_to_run_in_an_install_and_reads_the_newest_snapshot_it_can(bridge, home, capsys):
    cfg, _, _ = _claimed(bridge)
    with pytest.raises(SystemExit) as stop:
        sf.list_accounts(cfg)
    assert "./ff refresh" in str(stop.value) and "python -m etl" not in str(stop.value)
    good = sf.fetch(cfg)
    (good.parent / "accounts-29990101T000000Z.json").write_text("{not json", "utf-8")
    capsys.readouterr()
    sf.list_accounts(cfg)
    out = capsys.readouterr().out
    assert good.name in out and [line for line in out.splitlines() if line.startswith(FEED_ID)]
    assert "./ff init --feed" in out and "make ingest" not in out


def test_the_stored_snapshot_is_read_by_the_feeds_reader_and_an_offline_ingest_opens_no_socket(bridge, home,
                                                                                              monkeypatch):
    cfg, _, _ = _claimed(bridge)
    snapshot = sf.fetch(cfg)
    full = Config(home / "config")
    txns, balances, _, stats, _, _ = feed_parser.parse_snapshot(
        snapshot, full.simplefin_map(), full.simplefin_cutovers(), full.accounts, full.simplefin_tz,
        full.simplefin_ignore())
    assert sorted((t.account_id, t.amount_cents, t.description_raw) for t in txns) == [
        (FEED, -54_18, "TRADER JOE'S #000 SAMPLETOWN"), (FEED, -6_45, "STARBUCKS STORE 00000"),
        (FEED, 2500_00, "SAMPLE EMPLOYER PAYROLL")]
    assert [(b.account_id, b.balance_cents) for b in balances] == [(FEED, 1234_56)]
    opened = []

    def no_socket(*args, **kwargs):
        opened.append(args)
        raise AssertionError("the ingest opened a socket")
    with monkeypatch.context() as patch:
        patch.setattr(socket, "socket", no_socket)
        assert pipeline.run() == 0
    assert opened == []
    exported = json.loads((home / "data" / "exports" / "transactions.json").read_text("utf-8"))
    assert sorted(t["description"] for t in exported["transactions"] if t["account"] == FEED) == [
        "SAMPLE EMPLOYER PAYROLL", "STARBUCKS STORE 00000", "TRADER JOE'S #000 SAMPLETOWN"]

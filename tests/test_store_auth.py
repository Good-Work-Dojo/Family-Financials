# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The store's sign-in over real HTTP on a loopback port the system picks (deploy/scenario_store.py with deploy/auth.py):
/auth/login, /auth/logout, /auth/check, /auth/status and /healthz, the session every /api/* route asks for, the Origin
rule on every write, the throttle as a client meets it, and that no secret reaches the store's log. The in-process
server checks passcodes with a cheap counting hasher; the started-by-path store (its own process, as the container runs
it) uses real scrypt. Every passcode is an obvious invention and every address a documentation range."""
from __future__ import annotations

import datetime as dt
import hashlib
import http.client
import importlib.util
import json
import os
import re
import select
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import auth  # tests/conftest.py puts deploy/ on sys.path
import pytest
import refresh_state as rs

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("scenario_store_signin", ROOT / "deploy" / "scenario_store.py")
store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(store)

PASSCODE = "correct-horse-battery"
WRONG = "not-the-passcode"
HOME = "192.0.2.10"
STRANGER = "198.51.100.7"
EPOCH = "1970-01-01T00:00:00Z"
STAMP = "2026-11-01T00:00:00Z"


class CountingHasher:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, secret: bytes, salt: bytes) -> bytes:
        self.calls += 1
        return hashlib.sha256(salt + secret).digest()


class FakeClock:
    def __init__(self) -> None:
        self.t = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC).timestamp()

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def hasher():
    return CountingHasher()


@pytest.fixture()
def tree(tmp_path, hasher):
    """A stack's state and web secrets: one passcode, labelled alex."""
    web = tmp_path / "secrets" / "web"
    web.mkdir(parents=True)
    (web / "passcodes").write_text(auth.hash_passcode("alex", PASSCODE, hasher=hasher) + "\n")
    hasher.calls = 0
    (tmp_path / "state" / "refresh").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def slept():
    return []


@pytest.fixture()
def server(tree, clock, hasher, slept):
    h = store.ScenarioHandler
    h.store_dir = tree / "state"
    h.refresh_dir = tree / "state" / "refresh"
    h.clock = clock
    h.auth_required = True
    h.hostnames = ("example.net",)              # the name the tests' X-FF-Host gives, beyond the host rule's own
    h.signin = store.SignIn(tree / "secrets" / "web", tree / "state" / "auth", now=clock(), hasher=hasher,
                            sleep=slept.append, warn=lambda m: None)
    srv = store.StoreServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def own_origin(srv) -> str:
    return f"http://127.0.0.1:{srv.server_address[1]}"


def call(srv, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res, data


def login(srv, passcode: str, *, form: bool = False, headers: dict | None = None):
    if form:
        body = urlencode({"passcode": passcode}).encode()
        base = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/html,application/xhtml+xml"}
    else:
        body = json.dumps({"passcode": passcode}).encode()
        base = {"Content-Type": "application/json", "Accept": "*/*"}
    return call(srv, "POST", "/auth/login", body, {"Origin": own_origin(srv), **base, **(headers or {})})


def set_cookies(res) -> dict[str, str]:
    """name -> the whole Set-Cookie line, for each cookie the answer sets."""
    return {line.split("=", 1)[0]: line for line in res.headers.get_all("Set-Cookie") or []}


def value(line: str) -> str:
    return line.split(";", 1)[0].split("=", 1)[1]


def session_of(res) -> str:
    return value(set_cookies(res)["ff_session"])


def cookie_header(**cookies: str) -> dict:
    return {"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}


def check(srv, headers: dict | None = None):
    return call(srv, "GET", "/auth/check", headers=headers)


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ---------------------------------------------------------------- signing in

def test_a_json_sign_in_answers_204_with_a_session_and_a_device_cookie(server, tree):
    res, data = login(server, PASSCODE)
    assert res.status == 204 and data == b""
    assert res.getheader("Cache-Control") == "no-store"
    cookies = set_cookies(res)
    assert set(cookies) == {"ff_session", "ff_device"}
    for name, line in cookies.items():
        assert re.fullmatch(rf"{name}=[A-Za-z0-9_-]{{43}}; Path=/; HttpOnly; SameSite=Lax; Max-Age=34560000", line)
    token = value(cookies["ff_session"])
    saved = (tree / "state" / "auth" / "sessions.json").read_text()
    assert hashlib.sha256(token.encode()).hexdigest() in saved and token not in saved
    assert stat.S_IMODE((tree / "state" / "auth").stat().st_mode) == 0o700
    for name in ("sessions.json", "devices.json"):
        assert stat.S_IMODE((tree / "state" / "auth" / name).stat().st_mode) == 0o600


def test_a_form_sign_in_goes_home_with_the_cookies(server):
    res, _ = login(server, PASSCODE, form=True)
    assert res.status == 303 and res.getheader("Location") == "/"
    assert set(set_cookies(res)) == {"ff_session", "ff_device"}


@pytest.mark.parametrize("scheme,secure", [("https", True), ("http", False), (None, False)])
def test_the_cookies_are_secure_exactly_when_the_request_came_over_tls(server, scheme, secure):
    headers = {"X-FF-Host": "example.net", "Origin": f"{scheme or 'http'}://example.net"}
    if scheme:
        headers["X-FF-Scheme"] = scheme
    res, _ = login(server, PASSCODE, headers=headers)
    assert res.status == 204
    for line in set_cookies(res).values():
        assert line.endswith("; Secure") is secure


def test_a_known_device_keeps_its_cookie_and_an_unknown_one_gets_a_new_one(server):
    first, _ = login(server, PASSCODE)
    device = value(set_cookies(first)["ff_device"])
    again, _ = login(server, PASSCODE, headers=cookie_header(ff_device=device))
    assert again.status == 204 and set(set_cookies(again)) == {"ff_session"}
    forged, _ = login(server, PASSCODE, headers=cookie_header(ff_device="A" * 43))
    assert set(set_cookies(forged)) == {"ff_session", "ff_device"}


def test_signing_in_again_ends_the_session_the_browser_held(server):
    old = session_of(login(server, PASSCODE)[0])
    new = session_of(login(server, PASSCODE, headers=cookie_header(ff_session=old))[0])
    assert check(server, cookie_header(ff_session=old))[0].status == 401
    assert check(server, cookie_header(ff_session=new))[0].status == 204


def test_every_failed_sign_in_gets_one_message_and_costs_one_hash(server, hasher):
    port = server.server_address[1]
    bodies = [json.dumps({"passcode": WRONG}).encode(), b"{not json", b"[]", json.dumps({"other": 1}).encode(),
              json.dumps({"passcode": 12345678}).encode(), json.dumps({"passcode": PASSCODE + " "}).encode()]
    answers = set()
    for k, body in enumerate(bodies):
        before = hasher.calls
        res, data = call(server, "POST", "/auth/login", body, {
            "Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json", "X-FF-Client": f"192.0.2.{k}"})
        assert res.status == 401 and res.getheader("Set-Cookie") is None, body
        assert hasher.calls == before + 1, body
        answers.add(data)
    assert answers == {json.dumps({"error": store.SIGN_IN_FAILED}).encode()}
    for body in (urlencode({"passcode": WRONG}).encode(), b"\xff\xfenot=a&form", b"", b"passcode=%ff"):
        res, _ = call(server, "POST", "/auth/login", body, {
            "Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html", "X-FF-Client": "192.0.2.50"})
        assert res.status == 303 and res.getheader("Location") == "/login?e=1", body
        assert res.getheader("Set-Cookie") is None


def test_not_set_up_fails_like_a_wrong_passcode_and_the_status_says_so(server, tree, hasher):
    assert json.loads(call(server, "GET", "/auth/status")[1]) == {"configured": True}
    (tree / "secrets" / "web" / "passcodes").unlink()
    before = hasher.calls
    res, data = login(server, PASSCODE)
    assert res.status == 401 and json.loads(data) == {"error": store.SIGN_IN_FAILED}
    assert hasher.calls == before + 1
    assert json.loads(call(server, "GET", "/auth/status")[1]) == {"configured": False}


def test_a_passcode_file_changed_while_running_is_read_again(server, tree, hasher):
    (tree / "secrets" / "web" / "passcodes").write_text(
        auth.hash_passcode("blair", "another-passcode", hasher=hasher) + "\n")
    assert login(server, PASSCODE)[0].status == 401
    assert login(server, "another-passcode")[0].status == 204


@pytest.mark.parametrize("body,content_type,status", [
    (b"x" * (auth.MAX_LOGIN_BYTES + 1), "application/json", 413),
    (b"passcode=correct-horse-battery", "text/plain", 415),
    (b"passcode=correct-horse-battery", None, 415),
])
def test_a_sign_in_body_too_large_or_of_another_type_is_refused_unhashed(server, hasher, body, content_type, status):
    headers = {"Origin": own_origin(server)}
    if content_type:
        headers["Content-Type"] = content_type
    res, _ = call(server, "POST", "/auth/login", body, headers)
    assert res.status == status and hasher.calls == 0 and res.getheader("Set-Cookie") is None


def test_a_sign_in_without_a_length_is_refused(server, hasher):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    conn.putrequest("POST", "/auth/login")
    conn.putheader("Origin", own_origin(server))
    conn.putheader("Content-Type", "application/json")
    conn.endheaders()
    res = conn.getresponse()
    assert res.status == 411 and res.getheader("Connection") == "close" and hasher.calls == 0
    conn.close()


# ---------------------------------------------------------------- /auth/check, /auth/status, /healthz

def test_check_answers_204_with_a_session_302_for_a_page_and_401_otherwise(server):
    token = session_of(login(server, PASSCODE)[0])
    res, data = check(server, {**cookie_header(ff_session=token), "X-Forwarded-Method": "GET",
                               "X-Forwarded-Uri": "/api/prefs"})
    assert res.status == 204 and data == b"" and res.getheader("Cache-Control") == "no-store"
    page = {"X-Forwarded-Method": "GET", "X-Forwarded-Uri": "/settings", "Sec-Fetch-Mode": "navigate",
            "Accept": "text/html"}
    res, _ = check(server, page)
    assert res.status == 302 and res.getheader("Location") == "/login"
    res, _ = check(server, {**page, "X-Forwarded-Uri": "/api/prefs"})
    assert res.status == 401 and res.getheader("Cache-Control") == "no-store"
    res, _ = check(server, {**page, "X-Forwarded-Uri": "/exports/accounts.json"})
    assert res.status == 401
    assert check(server)[0].status == 401
    assert check(server, {**page, **cookie_header(ff_session="A" * 43)})[0].status == 302


def test_status_and_health_need_no_session(server):
    res, data = call(server, "GET", "/healthz")
    assert res.status == 200 and data == b"ok\n"
    res, data = call(server, "GET", "/auth/status")
    assert res.status == 200 and json.loads(data) == {"configured": True}


# ---------------------------------------------------------------- signing out

def test_sign_out_ends_the_session_server_side_and_a_copied_cookie_stops_working(server, tree):
    token = session_of(login(server, PASSCODE)[0])
    stolen = cookie_header(ff_session=token)
    assert check(server, stolen)[0].status == 204
    res, _ = call(server, "POST", "/auth/logout", b"", {**stolen, "Origin": own_origin(server)})
    assert res.status == 204
    assert set_cookies(res)["ff_session"] == "ff_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
    assert check(server, stolen)[0].status == 401
    assert call(server, "GET", "/api/prefs", headers=stolen)[0].status == 401
    assert hashlib.sha256(token.encode()).hexdigest() not in (tree / "state" / "auth" / "sessions.json").read_text()
    again, _ = call(server, "POST", "/auth/logout", b"", {**stolen, "Origin": own_origin(server)})
    assert again.status == 204
    form, _ = call(server, "POST", "/auth/logout", b"", {"Origin": own_origin(server), "Accept": "text/html",
                                                         "Content-Type": "application/x-www-form-urlencoded"})
    assert form.status == 303 and form.getheader("Location") == "/login"


# ---------------------------------------------------------------- the throttle, as a browser meets it

def test_a_locked_address_answers_429_and_the_households_browser_still_gets_in(server, clock, hasher):
    first, _ = login(server, PASSCODE, headers={"X-FF-Client": HOME})
    device = value(set_cookies(first)["ff_device"])
    for _ in range(5):
        assert login(server, WRONG, headers={"X-FF-Client": HOME})[0].status == 401
    hashed = hasher.calls
    res, data = login(server, PASSCODE, headers={"X-FF-Client": HOME})     # right passcode, locked address
    assert res.status == 429 and res.getheader("Retry-After") == "60"
    assert json.loads(data)["error"] == store.SIGN_IN_WAIT and res.getheader("Set-Cookie") is None
    res, _ = login(server, PASSCODE, form=True, headers={"X-FF-Client": HOME})
    assert res.status == 303 and res.getheader("Location") == "/login?e=2"
    res, _ = login(server, PASSCODE, headers={"X-FF-Client": HOME, "X-Forwarded-For": "198.51.100.99"})
    assert res.status == 429                                                 # X-Forwarded-For is never read
    assert hasher.calls == hashed                                            # a locked attempt is not hashed
    res, _ = login(server, PASSCODE, headers={"X-FF-Client": HOME, **cookie_header(ff_device=device)})
    assert res.status == 204                                                 # the household's browser
    assert login(server, PASSCODE, headers={"X-FF-Client": STRANGER})[0].status == 204
    clock.advance(60)
    assert login(server, PASSCODE, headers={"X-FF-Client": HOME})[0].status == 204   # the lock has ended


def test_without_x_ff_client_the_peer_address_is_the_key(server):
    for _ in range(5):
        login(server, WRONG)
    assert login(server, PASSCODE)[0].status == 429
    assert login(server, PASSCODE, headers={"X-FF-Client": STRANGER})[0].status == 204


def test_a_refused_cross_site_attempt_costs_nothing(server, hasher):
    for _ in range(10):
        res, _ = login(server, WRONG, headers={"Origin": "http://attacker.example"})
        assert res.status == 403
    assert hasher.calls == 0
    assert login(server, PASSCODE)[0].status == 204


def test_a_flood_slows_and_serialises_strangers_and_a_right_passcode_still_gets_in(server, slept, hasher):
    for k in range(31):
        assert login(server, WRONG, headers={"X-FF-Client": f"2001:db8:{k:x}::1"})[0].status == 401
    assert slept == []                                        # 31 failures in ten minutes: the flood starts now
    hashed = hasher.calls
    res, _ = login(server, PASSCODE, headers={"X-FF-Client": STRANGER})
    assert res.status == 204 and slept == [3] and hasher.calls == hashed + 1
    device = value(set_cookies(res)["ff_device"])
    assert login(server, PASSCODE, headers={"X-FF-Client": HOME, **cookie_header(ff_device=device)})[0].status == 204
    assert slept == [3]                                       # a known device does not wait


class Gate:
    """A hashing slot the test opens: it counts the attempts that reach it, holds them until opened, then lets them
    through one at a time."""

    def __init__(self) -> None:
        self.arrived = threading.Semaphore(0)
        self.opened = threading.Event()
        self.one = threading.Lock()

    def __enter__(self):
        self.arrived.release()
        assert self.opened.wait(10)
        self.one.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.one.release()


def test_attempts_queued_behind_a_failure_see_it_and_the_lock_stops_them_unhashed(tree, clock, hasher):
    signin = store.SignIn(tree / "secrets" / "web", tree / "state" / "auth", now=clock(), hasher=hasher,
                          warn=lambda m: None)
    gate = Gate()
    signin.other_slots = gate
    key = auth.address_key(STRANGER)
    outcomes: list[store.Attempt] = []
    threads = [threading.Thread(target=lambda: outcomes.append(signin.attempt(WRONG, key, None, clock)))
               for _ in range(10)]
    for t in threads:
        t.start()
    for _ in threads:                                        # all ten passed the first check and wait for a slot
        assert gate.arrived.acquire(timeout=10)
    gate.opened.set()
    for t in threads:
        t.join(10)
    assert hasher.calls == 5
    assert sum(o.label is None and o.refused is None for o in outcomes) == 5
    assert sum(isinstance(o.refused, auth.Locked) for o in outcomes) == 5


# ---------------------------------------------------------------- the Origin rule on every write

def documents() -> dict[str, dict]:
    return {
        "/api/scenarios": {"version": 1, "updated_at": STAMP, "scenarios": []},
        "/api/answers": {"version": 1, "updated_at": STAMP, "answers": []},
        "/api/prefs": {"version": 1, "updated_at": STAMP, "budgets": {"Groceries": 400}},
        "/api/manual-assets": {"version": 1, "updated_at": EPOCH, "assets": []},
    }


WRITES = [*(("PUT", path) for path in documents()), ("POST", "/api/refresh"), ("POST", "/api/refresh ingest"),
          ("POST", "/api/refresh ai"), ("POST", "/api/refresh ai_reset"), ("POST", "/auth/login"),
          ("POST", "/auth/logout")]


def write_request(method: str, target: str) -> tuple[str, bytes, dict]:
    if target.startswith("/api/refresh "):
        return "/api/refresh", json.dumps({"mode": target.split()[1]}).encode(), {}
    if target == "/auth/login":
        return target, json.dumps({"passcode": PASSCODE}).encode(), {"Content-Type": "application/json"}
    if target in documents():
        return target, json.dumps(documents()[target]).encode(), {}
    return target, b"", {}


@pytest.mark.parametrize("method,target", WRITES)
def test_the_origin_rule_guards_every_write(server, tree, hasher, method, target):
    token = session_of(login(server, PASSCODE)[0])
    path, body, extra = write_request(method, target)
    signed_in = {**cookie_header(ff_session=token), **extra}
    before, sessions = snapshot(tree / "state"), len(store.ScenarioHandler.signin.sessions)
    hashed = hasher.calls
    for origin in (None, "http://attacker.example", f"https://127.0.0.1:{server.server_address[1]}", "null",
                   f"http://127.0.0.1:{server.server_address[1]}/"):
        headers = dict(signed_in)
        if origin is not None:
            headers["Origin"] = origin
        res, data = call(server, method, path, body, headers)
        assert res.status == 403, (origin, data)
        assert res.getheader("Content-Type").startswith("text/plain") and res.getheader("Set-Cookie") is None
    assert snapshot(tree / "state") == before and hasher.calls == hashed
    assert len(store.ScenarioHandler.signin.sessions) == sessions
    assert check(server, cookie_header(ff_session=token))[0].status == 204        # a refused sign-out ended nothing
    res, data = call(server, method, path, body, {**signed_in, "Origin": own_origin(server)})
    assert 200 <= res.status < 400, data


def test_the_origin_rule_compares_with_the_scheme_and_host_caddy_saw(server):
    token = session_of(login(server, PASSCODE)[0])
    body = json.dumps(documents()["/api/prefs"]).encode()
    seen = {**cookie_header(ff_session=token), "X-FF-Scheme": "https", "X-FF-Host": "Example.NET:8443"}
    assert call(server, "PUT", "/api/prefs", body, {**seen, "Origin": "https://example.net:8443"})[0].status == 200
    for origin in ("https://example.net", "http://example.net:8443", "https://example.org:8443"):
        assert call(server, "PUT", "/api/prefs", body, {**seen, "Origin": origin})[0].status == 403, origin
    default_port = {**seen, "X-FF-Host": "example.net"}
    assert call(server, "PUT", "/api/prefs", body, {**default_port, "Origin": "https://example.net:443"})[0].status == 200


# ---------------------------------------------------------------- the API asks for a session

API = ["/api/scenarios", "/api/answers", "/api/prefs", "/api/manual-assets", "/api/refresh"]


def test_every_api_route_needs_a_session_when_sign_in_is_required(server, tree, clock):
    for path in API:
        res, _ = call(server, "GET", path)
        assert res.status == 401 and res.getheader("Cache-Control") == "no-store", path
    before = snapshot(tree / "state")
    for path, doc in documents().items():
        res, _ = call(server, "PUT", path, json.dumps(doc).encode(), {"Origin": own_origin(server)})
        assert res.status == 401 and res.getheader("Connection") == "close", path
    assert call(server, "POST", "/api/refresh", b"", {"Origin": own_origin(server)})[0].status == 401
    for mode in ("ingest", "ai", "ai_reset"):
        body = json.dumps({"mode": mode}).encode()
        assert call(server, "POST", "/api/refresh", body, {"Origin": own_origin(server)})[0].status == 401, mode
    assert snapshot(tree / "state") == before
    token = session_of(login(server, PASSCODE)[0])
    for path in API:
        assert call(server, "GET", path, headers=cookie_header(ff_session=token))[0].status == 200, path
    clock.advance(30 * 86400)                                                  # thirty days unseen
    assert call(server, "GET", "/api/prefs", headers=cookie_header(ff_session=token))[0].status == 401


def test_with_sign_in_off_the_api_answers_without_a_session(server):
    store.ScenarioHandler.auth_required = False
    for path in API:
        assert call(server, "GET", path)[0].status == 200, path
    body = json.dumps(documents()["/api/prefs"]).encode()
    assert call(server, "PUT", "/api/prefs", body, {"Origin": own_origin(server)})[0].status == 200
    assert call(server, "PUT", "/api/prefs", body)[0].status == 403                # the Origin rule still holds


# ---------------------------------------------------------------- saving the sessions

def only_passcode(signin) -> str:
    """The fingerprint of the one passcode line the tree holds."""
    (fingerprint,) = signin.live_passcodes()
    return fingerprint


def test_the_timer_saves_a_changed_table_at_most_every_fifteen_minutes(tree, clock, hasher):
    signin = store.SignIn(tree / "secrets" / "web", tree / "state" / "auth", now=clock(), hasher=hasher,
                          warn=lambda m: None)
    token, _ = signin.open_session(clock(), "alex", only_passcode(signin), [], None)
    saved = tree / "state" / "auth" / "sessions.json"
    first = saved.read_text()
    clock.advance(600)
    seen = clock()
    assert signin.session(seen, [token]) == "alex"                             # last_seen moves, in memory
    signin.flush_if_due(clock())
    assert saved.read_text() == first                                          # ten minutes after the last save
    clock.advance(299)
    signin.flush_if_due(clock())
    assert saved.read_text() == first
    clock.advance(1)
    signin.flush_if_due(clock())                                               # fifteen minutes after it
    assert saved.read_text() != first and json.loads(saved.read_text())["sessions"][
        hashlib.sha256(token.encode()).hexdigest()]["last_seen"] == seen
    third = saved.read_text()
    clock.advance(3600)
    signin.flush_if_due(clock())                                               # nothing changed since
    assert saved.read_text() == third


def test_a_session_table_that_cannot_be_saved_still_signs_in(tree, clock, hasher):
    blocked = tree / "state" / "blocked"
    blocked.write_text("a file where the folder would go")
    said: list[str] = []
    signin = store.SignIn(tree / "secrets" / "web", blocked / "auth", now=clock(), hasher=hasher, warn=said.append)
    token, _ = signin.open_session(clock(), "alex", only_passcode(signin), [], None)
    assert signin.session(clock(), [token]) == "alex"
    assert any("could not be saved" in line for line in said)


def test_the_auth_folder_and_the_parents_it_makes_are_private_whatever_the_umask(tree, clock, hasher):
    said: list[str] = []
    folder = tree / "fresh" / "state" / "auth"
    old = os.umask(0)
    try:
        signin = store.SignIn(tree / "secrets" / "web", folder, now=clock(), hasher=hasher, warn=said.append)
        signin.open_session(clock(), "alex", only_passcode(signin), [], None)
    finally:
        os.umask(old)
    for made in (folder, folder.parent, folder.parent.parent):
        assert stat.S_IMODE(made.stat().st_mode) == 0o700, made
    assert not any("0700" in line for line in said)                             # nothing was tightened


def test_a_wider_auth_folder_is_tightened_and_said_so_once(tree, clock, hasher):
    folder = tree / "state" / "auth"
    folder.mkdir(mode=0o755)
    folder.chmod(0o755)
    said: list[str] = []
    signin = store.SignIn(tree / "secrets" / "web", folder, now=clock(), hasher=hasher, warn=said.append)
    signin.open_session(clock(), "alex", only_passcode(signin), [], None)
    signin.open_session(clock(), "alex", only_passcode(signin), [], None)
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert len([line for line in said if "0700" in line]) == 1


def test_a_sign_out_that_ends_no_session_writes_nothing(server, tree):
    token = session_of(login(server, PASSCODE)[0])
    assert check(server, cookie_header(ff_session=token))[0].status == 204     # the table has changed in memory
    saved = tree / "state" / "auth" / "sessions.json"
    before = (saved.stat().st_ino, saved.read_bytes())
    for cookies in ({}, cookie_header(ff_session="A" * 43)):
        res, _ = call(server, "POST", "/auth/logout", b"", {**cookies, "Origin": own_origin(server)})
        assert res.status == 204
    assert (saved.stat().st_ino, saved.read_bytes()) == before
    call(server, "POST", "/auth/logout", b"", {**cookie_header(ff_session=token), "Origin": own_origin(server)})
    assert saved.stat().st_ino != before[0]


# ---------------------------------------------------------------- a passcode removed or changed

def write_passcodes(tree: Path, *lines: str) -> None:
    """Replace the passcodes file as the passcode command does (a new file renamed over the old one)."""
    auth.write_private(tree / "secrets" / "web" / "passcodes", "".join(line + "\n" for line in lines).encode())


def test_removing_or_changing_a_passcode_signs_out_its_browsers_and_no_others(server, tree, hasher, clock):
    alex = (tree / "secrets" / "web" / "passcodes").read_text().strip()
    blair = auth.hash_passcode("blair", "another-passcode", hasher=hasher)
    write_passcodes(tree, alex, blair)
    alex_cookie = cookie_header(ff_session=session_of(login(server, PASSCODE)[0]))
    blair_cookie = cookie_header(ff_session=session_of(login(server, "another-passcode")[0]))
    for cookie in (alex_cookie, blair_cookie):
        assert check(server, cookie)[0].status == 204
    write_passcodes(tree, blair)                                                # alex's passcode removed
    assert call(server, "GET", "/api/prefs", headers=alex_cookie)[0].status == 401
    assert check(server, alex_cookie)[0].status == 401
    assert call(server, "GET", "/api/prefs", headers=blair_cookie)[0].status == 200
    write_passcodes(tree, alex, blair)
    assert check(server, alex_cookie)[0].status == 401                          # ended, even with the line back
    write_passcodes(tree, alex, auth.hash_passcode("blair", "a-new-passcode", hasher=hasher))   # blair's changed
    assert call(server, "GET", "/api/prefs", headers=blair_cookie)[0].status == 401
    assert check(server, blair_cookie)[0].status == 401
    assert login(server, "another-passcode")[0].status == 401
    assert login(server, "a-new-passcode")[0].status == 204


def test_the_timer_drops_and_saves_the_sessions_of_a_removed_passcode(tree, clock, hasher):
    blair = auth.hash_passcode("blair", "another-passcode", hasher=hasher)
    alex = (tree / "secrets" / "web" / "passcodes").read_text().strip()
    write_passcodes(tree, alex, blair)
    signin = store.SignIn(tree / "secrets" / "web", tree / "state" / "auth", now=clock(), hasher=hasher,
                          warn=lambda m: None)
    by_label = {auth.parse_line(line).label: auth.passcode_fingerprint(auth.parse_line(line)) for line in (alex, blair)}
    alex_token, _ = signin.open_session(clock(), "alex", by_label["alex"], [], None)
    signin.open_session(clock(), "blair", by_label["blair"], [], None)
    write_passcodes(tree, blair)
    clock.advance(store.SAVE_EVERY_S)
    signin.flush_if_due(clock())
    saved = json.loads((tree / "state" / "auth" / "sessions.json").read_text())["sessions"]
    assert [record["label"] for record in saved.values()] == ["blair"]
    assert hashlib.sha256(alex_token.encode()).hexdigest() not in saved


# ---------------------------------------------------------------- a sign-in flood cannot grow the threads

class HeldHasher:
    """Hashes WRONG only once the test opens it, counting how many are inside at once; any other passcode at once."""

    def __init__(self) -> None:
        self.opened = threading.Event()
        self.inside = 0
        self.most = 0
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, secret: bytes, salt: bytes) -> bytes:
        with self._lock:
            self.calls += 1
            self.inside += 1
            self.most = max(self.most, self.inside)
        try:
            if secret == WRONG.encode():
                assert self.opened.wait(20)
            return hashlib.sha256(salt + secret).digest()
        finally:
            with self._lock:
                self.inside -= 1


def test_a_flood_of_a_hundred_strangers_fills_the_section_and_the_rest_are_refused_at_once(tree, clock):
    held = HeldHasher()
    write_passcodes(tree, auth.hash_passcode("alex", PASSCODE, hasher=held))
    held.calls = 0
    signin = store.SignIn(tree / "secrets" / "web", tree / "state" / "auth", now=clock(), hasher=held,
                          sleep=lambda seconds: None, warn=lambda m: None)
    asked: list[tuple] = []
    section = signin.section

    class NeverWaitedOn:
        """The section, recording how each attempt asks for a place."""

        def acquire(self, *args, **kwargs):
            asked.append((args, kwargs))
            return section.acquire(*args, **kwargs)

        def release(self):
            section.release()

    signin.section = NeverWaitedOn()
    outcomes: list[store.Attempt] = []
    lock = threading.Lock()

    def stranger(k: int) -> None:
        outcome = signin.attempt(WRONG, auth.address_key(f"2001:db8:{k:x}::1"), None, clock)
        with lock:
            outcomes.append(outcome)

    def until(condition) -> None:
        deadline = time.monotonic() + 20
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.01)

    threads = [threading.Thread(target=stranger, args=(k,)) for k in range(100)]
    for t in threads:
        t.start()
    until(lambda: len(outcomes) >= 100 - store.SIGN_IN_SECTION and held.calls >= store.HASH_SLOTS)
    assert len(outcomes) == 68 and all(o.busy and o.refused == auth.Wait(store.BUSY_RETRY_S) for o in outcomes)
    assert held.calls == store.HASH_SLOTS                     # the rest of the section waits for a hashing slot
    household = signin.attempt(PASSCODE, auth.address_key(HOME), "d" * 64, clock)
    assert household.label == "alex"                          # a known device never enters the section
    assert len(signin.throttle.state.recent) == 0             # nothing counted yet
    held.opened.set()
    for t in threads:
        t.join(20)
    failed = [o for o in outcomes if not o.busy]
    assert len(failed) == store.SIGN_IN_SECTION and all(o.label is None and o.refused is None for o in failed)
    assert len(asked) == 100 and all(how == ((), {"blocking": False}) for how in asked)   # never waited on
    assert held.most <= store.HASH_SLOTS + 1                  # two hashing slots, and the household's own


def _read_to_close(s: socket.socket) -> bytes:
    """Everything the store sends on a connection before it closes it (one request per connection): its head and body
    may arrive in separate reads on a busy machine."""
    chunks = []
    while chunk := s.recv(4096):
        chunks.append(chunk)
    return b"".join(chunks)


def test_requests_that_hold_their_body_back_take_at_most_the_slots_and_the_rest_are_answered_at_once(server):
    """A hundred sign-ins that send their headers and never their body: REQUEST_SLOTS of them wait (each on a thread,
    for its body), the others are answered 503 at once and their threads end; the health and session checks still
    answer, and once the held ones go, a sign-in gets in."""
    port = server.server_address[1]
    head = (f"POST /auth/login HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: {own_origin(server)}\r\n"
            "Content-Type: application/json\r\nContent-Length: 100\r\n\r\n").encode()
    before = threading.active_count()
    socks = []
    try:
        for _ in range(100):
            s = socket.create_connection(("127.0.0.1", port), timeout=10)
            s.sendall(head)
            socks.append(s)
        answered: dict[socket.socket, bytes] = {}
        deadline = time.monotonic() + 10
        while len(answered) < 100 - store.REQUEST_SLOTS and time.monotonic() < deadline:
            ready, _, _ = select.select([s for s in socks if s not in answered], [], [], 0.2)
            for s in ready:
                answered[s] = _read_to_close(s)
        time.sleep(0.3)                                       # nothing more is answered: the rest wait for a body
        ready, _, _ = select.select([s for s in socks if s not in answered], [], [], 0)
        assert ready == []
        assert len(answered) == 100 - store.REQUEST_SLOTS
        for raw in answered.values():
            assert raw.startswith(b"HTTP/1.0 503") and b"Retry-After: 1\r\n" in raw
            assert json.loads(raw.split(b"\r\n\r\n", 1)[1]) == {"error": store.BUSY}
        assert threading.active_count() - before <= store.REQUEST_SLOTS + 2
        res, data = call(server, "GET", "/healthz")
        assert res.status == 200
        res, _ = call(server, "GET", "/auth/check", headers={"Accept": "application/json"})
        assert res.status == 401
        res, _ = call(server, "GET", "/api/prefs")
        assert res.status == 503 and res.getheader("Retry-After") == "1"
    finally:
        for s in socks:
            s.close()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        res, _ = login(server, PASSCODE, headers={"X-FF-Client": HOME})
        if res.status != 503:
            break
        time.sleep(0.05)
    assert res.status == 204


def test_a_full_section_is_an_immediate_429_with_retry_after_unhashed_and_uncounted(server, clock, hasher, slept):
    signin = store.ScenarioHandler.signin
    signin.section = threading.BoundedSemaphore(0)                             # every place taken
    res, data = login(server, PASSCODE, headers={"X-FF-Client": HOME})       # even the right passcode
    assert res.status == 429 and res.getheader("Retry-After") == "3"
    assert json.loads(data) == {"error": store.SIGN_IN_WAIT, "retry_after_s": 3}
    assert res.getheader("Set-Cookie") is None
    res, _ = login(server, PASSCODE, form=True, headers={"X-FF-Client": STRANGER})
    assert res.status == 303 and res.getheader("Location") == "/login?e=2"
    assert hasher.calls == 0 and slept == [] and len(signin.throttle.state.recent) == 0
    device = signin.devices.add(clock())                                       # a browser that signed in before
    res, _ = login(server, PASSCODE, headers={"X-FF-Client": HOME, **cookie_header(ff_device=device)})
    assert res.status == 204


def test_the_store_listens_on_loopback_with_a_deep_queue_and_names_no_python_version(server):
    assert store.parse_args([]).host == "127.0.0.1"
    assert store.StoreServer.request_queue_size == 128
    res, _ = call(server, "GET", "/healthz")
    assert res.getheader("Server") == "ff-store"
    assert "python" not in (res.getheader("Server") or "").lower()


# ---------------------------------------------------------------- the host rule

@pytest.mark.parametrize("method,path", [("GET", "/api/prefs"), ("GET", "/auth/check"), ("GET", "/auth/status"),
                                         ("POST", "/auth/login"), ("POST", "/auth/logout"), ("PUT", "/api/prefs"),
                                         ("POST", "/api/refresh"), ("DELETE", "/api/prefs"), ("GET", "/nowhere")])
def test_an_unknown_host_is_421_everywhere_but_health(server, hasher, method, path):
    token = session_of(login(server, PASSCODE)[0])
    hashed = hasher.calls
    for host in ("attacker.example", "localhost.attacker.example", "evil.local.example", "localhost."):
        headers = {**cookie_header(ff_session=token), "X-FF-Host": host, "Origin": f"http://{host}",
                   "Content-Type": "application/json"}
        res, data = call(server, method, path, b"{}" if method != "GET" else None, headers)
        assert res.status == 421 and data == store.UNKNOWN_HOST.encode(), (host, data)
        assert res.getheader("Set-Cookie") is None
    assert hasher.calls == hashed
    assert call(server, "GET", "/healthz", headers={"X-FF-Host": "attacker.example"})[0].status == 200


@pytest.mark.parametrize("host", ["192.0.2.10:8080", "[2001:db8::1]:8080", "localhost:8080", "kitchen.local",
                                  "example.net", "EXAMPLE.NET:80"])
def test_a_known_host_passes(server, host):
    assert call(server, "GET", "/auth/status", headers={"X-FF-Host": host})[0].status == 200


def test_https_exempts_no_host_and_only_sign_in_off_steps_the_rule_aside(server):
    # X-FF-Scheme may be a trusted proxy's word (a tunnel that holds the certificate), so it says nothing of the name
    for scheme in ("https", "http"):
        res, _ = call(server, "GET", "/auth/status", headers={"X-FF-Host": "dash.example.org", "X-FF-Scheme": scheme})
        assert res.status == 421, scheme
    store.ScenarioHandler.auth_required = False
    assert call(server, "GET", "/api/prefs", headers={"X-FF-Host": "dash.example.org"})[0].status == 200


def test_the_sites_own_name_joins_the_names_the_host_rule_takes(monkeypatch):
    monkeypatch.setenv("SITE_ADDRESS", "https://dash.example.net:8443")
    monkeypatch.setenv("FF_HOSTNAMES", "alt.example.net")
    assert store.allowed_names(store.parse_args([])) == ("alt.example.net", "dash.example.net")
    assert store.allowed_names(store.parse_args(["--site-address", ":8080", "--hostnames", ""])) == ()
    assert store.allowed_names(store.parse_args(["--site-address", "alt.example.net"])) == ("alt.example.net",)
    monkeypatch.delenv("SITE_ADDRESS")
    monkeypatch.delenv("FF_HOSTNAMES")
    assert store.parse_args([]).site_address == "" and store.allowed_names(store.parse_args([])) == ()


# ---------------------------------------------------------------- one request per connection

def exchange(srv, raw: bytes) -> bytes:
    """`raw` written to a fresh connection, and everything the store sends back until it closes the connection"""
    with socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=10) as s:
        s.sendall(raw)
        out = b""
        while chunk := s.recv(65536):
            out += chunk
    return out


def status_lines(answer: bytes) -> list[bytes]:
    return re.findall(rb"^HTTP/1\.[01] \d{3}[^\r\n]*", answer, re.MULTILINE)


@pytest.mark.parametrize("method,path,body,headers", [
    ("GET", "/healthz", None, {}),
    ("GET", "/auth/status", None, {}),
    ("GET", "/api/prefs", None, {}),
    ("GET", "/nowhere", None, {}),
    ("GET", "/auth/status", None, {"X-FF-Host": "attacker.example"}),
    ("POST", "/auth/logout", b"", {"Accept": "text/html"}),
    ("PUT", "/api/prefs", b"{}", {}),
    ("DELETE", "/api/prefs", None, {}),
])
def test_every_answer_ends_its_connection(server, method, path, body, headers):
    res, _ = call(server, method, path, body, {"Origin": own_origin(server), **headers})
    assert res.getheader("Connection") == "close" and res.headers.get_all("Connection") == ["close"], res.status


def test_two_requests_on_one_connection_get_one_answer(server, capsys):
    capsys.readouterr()
    answer = exchange(server, b"GET /auth/status HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
                              b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    assert len(status_lines(answer)) == 1 and b'{"configured": true}' in answer and b"ok\n" not in answer
    log = [line for line in capsys.readouterr().err.splitlines() if " /" in line]
    assert log == ["scenario_store: GET /auth/status 200"], log


def test_a_get_whose_body_is_a_whole_second_request_is_refused_and_the_second_never_runs(server, hasher, capsys):
    """The reviewer's case: a GET carrying, as its body, a sign-in written by the client (its own X-FF-Client, Origin
    and passcode). The store refuses the GET unread and ends the connection, so the sign-in is never read, hashed or
    counted, and no cookie is set."""
    capsys.readouterr()
    inner = json.dumps({"passcode": PASSCODE}).encode()
    smuggled = (b"POST /auth/login HTTP/1.1\r\nHost: 127.0.0.1\r\nX-FF-Client: 203.0.113.9\r\n"
                + f"Origin: {own_origin(server)}\r\n".encode()
                + b"Content-Type: application/json\r\nAccept: */*\r\n"
                + f"Content-Length: {len(inner)}\r\n\r\n".encode() + inner)
    for framing in (f"Content-Length: {len(smuggled)}\r\n".encode(),
                    b"Transfer-Encoding: chunked\r\n"):
        body = smuggled if framing.startswith(b"Content") else f"{len(smuggled):x}\r\n".encode() + smuggled + b"\r\n0\r\n\r\n"
        answer = exchange(server, b"GET /auth/status HTTP/1.1\r\nHost: 127.0.0.1\r\n" + framing + b"\r\n" + body)
        assert status_lines(answer) == [b"HTTP/1.0 400 Bad Request"], answer[:200]
        assert answer.endswith(store.READ_WITH_BODY.encode()) and b"Set-Cookie" not in answer
    assert hasher.calls == 0 and len(store.ScenarioHandler.signin.throttle.state.recent) == 0
    log = [line for line in capsys.readouterr().err.splitlines() if " /" in line or "signed in" in line]
    assert log == ["scenario_store: GET /auth/status 400"] * 2, log


@pytest.mark.parametrize("framing", ["Content-Length: 5", "Transfer-Encoding: chunked", "Content-Length:  7 "])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_a_get_or_head_that_says_it_carries_a_body_is_400_unread(server, method, framing):
    answer = exchange(server, f"{method} /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n{framing}\r\n\r\nhello".encode())
    assert status_lines(answer) == [b"HTTP/1.0 400 Bad Request"]


def test_a_get_with_an_empty_body_is_answered(server):
    assert status_lines(exchange(server, b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\n\r\n")) \
        == [b"HTTP/1.0 200 OK"]


def test_sign_out_tells_the_browser_to_clear_its_cache(server):
    token = session_of(login(server, PASSCODE)[0])
    for accept in ("*/*", "text/html"):
        res, _ = call(server, "POST", "/auth/logout", b"",
                      {"Origin": own_origin(server), "Accept": accept, **cookie_header(ff_session=token)})
        assert res.status in (204, 303) and res.getheader("Clear-Site-Data") == '"cache"', accept


# ---------------------------------------------------------------- a Content-Length that is no number

@pytest.mark.parametrize("method,path", [("PUT", "/api/prefs"), ("POST", "/api/refresh"), ("POST", "/auth/login"),
                                         ("POST", "/auth/logout")])
def test_a_length_of_another_scripts_digit_is_400_not_a_crash(server, method, path):
    token = session_of(login(server, PASSCODE)[0])
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    conn.putrequest(method, path)
    conn.putheader("Origin", own_origin(server))
    conn.putheader("Cookie", f"ff_session={token}")
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", "²")
    conn.endheaders()
    res = conn.getresponse()
    assert res.status == 400 and res.getheader("Connection") == "close"
    conn.close()
    assert call(server, "GET", "/healthz")[0].status == 200


@pytest.mark.parametrize("value,length", [(None, None), ("0", 0), ("1024", 1024), ("²", ValueError),
                                          ("٣", ValueError), ("-1", ValueError), (" 12", ValueError),
                                          ("", ValueError)])
def test_a_content_length_is_ascii_digits_only(value, length):
    if length is ValueError:
        with pytest.raises(ValueError):
            store.content_length(value)          # "٣", an Arabic-Indic three, is a digit int() reads as 3
    else:
        assert store.content_length(value) == length


def test_a_logged_path_prints_no_control_character():
    assert store.printable("/a\x1b[31mb\x85\n") == "/a\\x1b[31mb\\x85\\n"
    assert store.printable("/plain/café") == "/plain/café"


# ---------------------------------------------------------------- the store as the container starts it

STORE_SCRIPT = ROOT / "deploy" / "scenario_store.py"


def start(home: Path, *args: str, **env: str) -> tuple[subprocess.Popen, int]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("FF_")}
    proc = subprocess.Popen([sys.executable, str(STORE_SCRIPT), "--host", "127.0.0.1", "--port", "0", *args],
                            cwd=home.parent, stderr=subprocess.PIPE, text=True,
                            env={**clean, "TZ": "UTC", "FF_HOME": str(home), **env})
    line = proc.stderr.readline()
    found = re.search(r" on 127\.0\.0\.1:(\d+) ", line)
    if not found:
        proc.kill()
        raise AssertionError(f"the store did not start: {line!r}")
    return proc, int(found.group(1))


def finish(proc: subprocess.Popen) -> str:
    """Stop the store as docker stop does and return the rest of what it printed."""
    proc.terminate()
    _, rest = proc.communicate(timeout=10)
    assert proc.returncode == 0
    return rest


class Port:
    def __init__(self, port: int) -> None:
        self.server_address = ("127.0.0.1", port)


@pytest.fixture()
def home(tmp_path):
    """An FF_HOME with one passcode hashed by real scrypt."""
    home = tmp_path / "home"
    web = home / "secrets" / "web"
    web.mkdir(parents=True)
    line = auth.hash_passcode("alex", PASSCODE)
    (web / "passcodes").write_text(line + "\n")
    return home


def test_no_secret_reaches_the_stores_log(home):
    line = (home / "secrets" / "web" / "passcodes").read_text().strip()
    proc, port = start(home)
    srv = Port(port)
    try:
        ok, _ = login(srv, PASSCODE)
        assert ok.status == 204
        token, device = session_of(ok), value(set_cookies(ok)["ff_device"])
        assert login(srv, WRONG, form=True)[0].status == 303
        signed = cookie_header(ff_session=token, ff_device=device)
        assert check(srv, {**signed, "X-Forwarded-Uri": f"/login?passcode={PASSCODE}"})[0].status == 204
        assert call(srv, "GET", f"/api/prefs?passcode={PASSCODE}", headers=signed)[0].status == 200
        body = json.dumps(documents()["/api/prefs"]).encode()
        assert call(srv, "PUT", "/api/prefs", body, {**signed, "Origin": own_origin(srv)})[0].status == 200
        call(srv, "GET", f"/auth/login?passcode={PASSCODE}", headers=signed)
        call(srv, "GET", f"/login?passcode={PASSCODE}&e=1", headers=signed)
        assert call(srv, "POST", "/auth/logout", b"", {**signed, "Origin": own_origin(srv)})[0].status == 204
    finally:
        log = finish(proc)
    salt, digest = line.split(":")[5:]
    for secret in (PASSCODE, WRONG, token, device, hashlib.sha256(token.encode()).hexdigest(),
                   hashlib.sha256(device.encode()).hexdigest(), salt, digest, "passcode=", "Cookie", "ff_session"):
        assert secret not in log, secret
    requests = [ln for ln in log.splitlines() if re.match(r"scenario_store: (GET|POST|PUT) ", ln)]
    assert requests and not any("?" in ln for ln in requests)
    assert "alex signed in" in log and "failed" in log


def test_sessions_survive_a_restart_in_private_files(home):
    proc, port = start(home)
    try:
        ok, _ = login(Port(port), PASSCODE)
        token = session_of(ok)
    finally:
        finish(proc)
    folder = home / "state" / "auth"
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert stat.S_IMODE((folder / "sessions.json").stat().st_mode) == 0o600
    proc, port = start(home)
    try:
        assert check(Port(port), cookie_header(ff_session=token))[0].status == 204
    finally:
        finish(proc)


def test_the_names_beyond_the_host_rules_own_come_from_ff_hostnames_or_the_argument(home):
    for args, env in (((), {"FF_HOSTNAMES": "dash.example.net other.example.net"}),
                      (("--hostnames", "dash.example.net"), {"FF_HOSTNAMES": "unused.example.net"})):
        proc, port = start(home, *args, **env)

        def ask(host: str, port: int = port) -> int:
            return call(Port(port), "GET", "/auth/status", headers={"X-FF-Host": host})[0].status

        try:
            assert ask("Dash.Example.NET:8080") == 200
            assert ask("192.0.2.10") == 200
            assert ask("attacker.example") == 421
            assert ask("unused.example.net") == 421
        finally:
            finish(proc)


def test_a_control_character_in_a_path_is_logged_escaped(home):
    proc, port = start(home)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=10) as conn:
            conn.sendall(b"GET /x\x1b[31mred\x7f HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            while conn.recv(4096):
                pass
    finally:
        log = finish(proc)
    assert "\x1b" not in log and "\x7f" not in log
    assert "GET /x\\x1b[31mred\\x7f 404" in log


def test_sign_in_is_required_unless_turned_off_which_says_so_once(home):
    proc, port = start(home)
    try:
        assert call(Port(port), "GET", "/api/prefs")[0].status == 401
    finally:
        log = finish(proc)
    assert "--auth off" not in log
    proc, port = start(home, "--auth", "off")
    try:
        assert call(Port(port), "GET", "/api/prefs")[0].status == 200
    finally:
        log = finish(proc)
    assert len([ln for ln in log.splitlines() if "--auth off" in ln]) == 1


def test_the_store_takes_its_sign_in_directories_from_arguments(tmp_path, home):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "web").mkdir(parents=True)
    (elsewhere / "web" / "passcodes").write_text(auth.hash_passcode("blair", "another-passcode") + "\n")
    proc, port = start(home, "--secrets-web-dir", str(elsewhere / "web"), "--state-auth-dir", str(elsewhere / "auth"))
    try:
        assert login(Port(port), PASSCODE)[0].status == 401
        assert login(Port(port), "another-passcode")[0].status == 204
    finally:
        finish(proc)
    assert (elsewhere / "auth" / "sessions.json").exists() and not (home / "state" / "auth").exists()


def test_the_deploy_side_places_the_sign_in_directories_as_paths_does(tmp_path, monkeypatch):
    from etl.config import Paths
    monkeypatch.chdir(tmp_path)
    for name in ("FF_HOME", "FF_SECRETS_WEB_DIR", "FF_STATE_AUTH_DIR"):
        monkeypatch.delenv(name, raising=False)
    # the legacy places differ by design: the store's are the present containers' mounts, Paths' sit beside the config
    # directory as the state and the data do (inside the repository, in a checkout)
    assert Paths(Path("/app/finance-app/config")).secrets_web_dir() == Path("/app/finance-app/secrets/web")
    assert Paths(Path("/app/finance-app/config")).state_auth_dir() == Path("/app/finance-app/state/auth")
    assert (rs.LOCATIONS["secrets_web"].legacy, rs.LOCATIONS["auth"].legacy) == ("/app/secrets/web", "/app/state/auth")
    monkeypatch.setenv("FF_HOME", "home")
    paths = Paths(tmp_path / "legacy" / "config")
    assert rs.locate("secrets_web") == paths.secrets_web_dir()
    assert rs.locate("auth") == paths.state_auth_dir()
    monkeypatch.setenv("FF_SECRETS_WEB_DIR", "named-web")
    monkeypatch.setenv("FF_STATE_AUTH_DIR", "named-auth")
    assert rs.locate("secrets_web") == Paths(tmp_path / "legacy" / "config").secrets_web_dir() == tmp_path / "named-web"
    assert rs.locate("auth") == Paths(tmp_path / "legacy" / "config").state_auth_dir() == tmp_path / "named-auth"

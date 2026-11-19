# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""A SimpleFIN Bridge on 127.0.0.1 for the feed's tests: a stdlib HTTP server at an ephemeral port, run in a thread for
as long as `serve()` is held (a test's fixture holds it), and reached by etl/simplefin.py only through its one test
seam, FF_SIMPLEFIN_TEST_ORIGIN set to this server's origin. Nothing it does leaves the loopback interface.

What it answers follows the public protocol (https://www.simplefin.org/protocol.html) and the Bridge's developer guide;
where they say nothing, the behaviour is this fake's own choice, marked so:
  POST /simplefin/claim/<id>  a setup token is the base64 of its claim URL. The first POST answers an access URL with
                              basic-auth userinfo; any later one, and an id never issued, 403 (single use).
  GET  /simplefin/accounts    basic auth: no credential or a wrong one 403 (also a revoked one); a credential whose
                              subscription lapsed 402 "Payment required".
      start-date, end-date    a posted row is answered when start-date <= posted < end-date. A window longer than 90
                              days is answered for its last 90 days with an error entry saying so: the protocol states
                              no limit and the guide says only "90 days at a time", so the clamp and its entry are the
                              fake's choice.
      pending=1               pending rows (posted 0) too, those whose transacted_at is in the window (the fake's
                              choice); without it none.
      balances-only=1         no account carries transactions.
      account=<id>            only those accounts (repeatable).
      version=2               the v2 shape: `errlist` objects ({code, msg, conn_id?, account_id?}), `connections`, and
                              each account's `conn_id` with no `org` (v2 deprecates it: the fake leaves it out). Without
                              it the v1 shape: `errors` strings and each account's `org`.
  An account marked failing is answered with its balance, no rows and an act.failed entry. `hold(seconds, requests)`
  holds that many of the next /accounts requests that long (or until the server stops) before answering each: a stall
  past the client's timeout. `redirect(location, status)` answers every later request, the claim's too, with that 30x
  status and a Location header and nothing else (a test's choice: the protocol states no redirect).

Every request is recorded in `received` (method, path, query and the basic-auth user, never the password), and every
/accounts body sent in `sent`. All data is invented: placeholder ids, example.invalid hosts."""
from __future__ import annotations

import base64
import binascii
import contextlib
import json
import secrets
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

WINDOW_SECONDS = 90 * 86_400
CLAMPED = "The date range was longer than 90 days: only its last 90 days are returned."
CONNECTION = {"conn_id": "CON-fake-0001", "name": "Sample Bank", "org_id": "ORG-fake-0001",
              "org_url": "https://bank.example.invalid", "sfin_url": "https://bridge.example.invalid/simplefin"}
ORG = {"name": "Sample Bank", "domain": "bank.example.invalid"}


@dataclass(frozen=True)
class Received:
    method: str
    path: str
    query: dict[str, list[str]]
    user: str | None            # the basic-auth user the request carried, None without one


@dataclass
class Account:
    id: str
    name: str
    balance: str
    balance_date: int
    transactions: list[dict] = field(default_factory=list)
    failing: bool = False       # answered with no rows and an act.failed entry


class FakeBridge:
    """The bridge's state, which a test sets and reads: `accounts`, `errlist` (connection errors every answer carries,
    as {code, msg, conn_id}), `hold`, `redirect`, `lapse(user)` and `revoke(user)`."""

    def __init__(self, port: int):
        self.origin = f"http://127.0.0.1:{port}"
        self.accounts: list[Account] = []
        self.errlist: list[dict] = []
        self._stall = 0.0                           # how long a held request waits
        self._stalls = 0                            # how many of the next /accounts requests are held
        self.received: list[Received] = []
        self.sent: list[bytes] = []
        self._claims: dict[str, bool] = {}          # claim id -> claimed
        self._credentials: dict[str, str] = {}      # user -> password
        self._lapsed: set[str] = set()
        self._redirect: tuple[int, str] | None = None   # (status, location) every request is answered with
        self._lock = threading.Lock()
        self._stopping = threading.Event()

    # ---------------------------------------------------------------- what a test does

    def setup_token(self) -> str:
        """A new single-use setup token: the base64 of its claim URL"""
        claim_id = secrets.token_hex(8)
        with self._lock:
            self._claims[claim_id] = False
        return base64.b64encode(f"{self.origin}/simplefin/claim/{claim_id}".encode()).decode()

    def credential(self) -> tuple[str, str, str]:
        """A credential issued without a claim: (user, password, access URL)"""
        user, password = self._issue()
        return user, password, self._access_url(user, password)

    def hold(self, seconds: float, requests: int) -> None:
        """Hold the next `requests` /accounts requests `seconds` each before answering"""
        with self._lock:
            self._stall, self._stalls = seconds, requests

    def redirect(self, location: str, status: int = 302) -> None:
        """Answer every later request with `status` and a Location header naming `location`"""
        with self._lock:
            self._redirect = (status, location)

    def lapse(self, user: str) -> None:
        self._lapsed.add(user)

    def revoke(self, user: str) -> None:
        with self._lock:
            self._credentials.pop(user, None)

    def gets(self) -> list[Received]:
        return [r for r in self.received if r.method == "GET"]

    def stop(self) -> None:
        self._stopping.set()

    # ---------------------------------------------------------------- answering

    def _issue(self) -> tuple[str, str]:
        user, password = f"fakeuser{secrets.token_hex(4)}", f"fake-bridge-secret-{secrets.token_hex(16)}"
        with self._lock:
            self._credentials[user] = password
        return user, password

    def _access_url(self, user: str, password: str) -> str:
        return f"http://{user}:{password}@{self.origin.removeprefix('http://')}/simplefin"

    def handle(self, h: BaseHTTPRequestHandler) -> None:
        parts = urlsplit(h.path)
        query = parse_qs(parts.query, keep_blank_values=True)
        user, password = _basic_auth(h.headers.get("Authorization"))
        length = int(h.headers.get("Content-Length") or 0)
        if length:
            h.rfile.read(length)
        with self._lock:
            self.received.append(Received(h.command, parts.path, query, user))
            redirect = self._redirect
        if redirect is not None:
            _send(h, redirect[0], b"", location=redirect[1])
        elif h.command == "POST" and parts.path.startswith("/simplefin/claim/"):
            self._claim(h, parts.path.removeprefix("/simplefin/claim/"))
        elif h.command == "GET" and parts.path == "/simplefin/accounts":
            self._accounts(h, query, user, password)
        else:
            _send(h, 404, b"Not found")

    def _claim(self, h: BaseHTTPRequestHandler, claim_id: str) -> None:
        with self._lock:
            fresh = self._claims.get(claim_id) is False
            if fresh:
                self._claims[claim_id] = True
        if not fresh:
            _send(h, 403, b"Forbidden")
            return
        _send(h, 200, self._access_url(*self._issue()).encode())

    def _accounts(self, h: BaseHTTPRequestHandler, query: dict[str, list[str]], user: str | None,
                  password: str | None) -> None:
        with self._lock:
            known = user is not None and self._credentials.get(user) == password
        if not known:
            _send(h, 403, b"Authentication failed")
            return
        if user in self._lapsed:
            _send(h, 402, b"Payment required")
            return
        with self._lock:
            held = self._stalls > 0
            self._stalls -= held
        if held:
            self._stopping.wait(self._stall)
        body = json.dumps(self._answer(query)).encode()
        with self._lock:
            self.sent.append(body)
        with contextlib.suppress(OSError):             # a client that timed out has gone
            _send(h, 200, body, "application/json")

    def _answer(self, query: dict[str, list[str]]) -> dict:
        def one(name: str) -> str | None:
            return query[name][-1] if query.get(name) else None

        v2 = one("version") == "2"
        start = int(one("start-date")) if one("start-date") else None
        end = int(one("end-date")) if one("end-date") else None
        errors: list[dict] = []
        if start is not None and end is not None and end - start > WINDOW_SECONDS:
            start = end - WINDOW_SECONDS
            errors.append({"code": "gen.", "msg": CLAMPED})
        pending, balances_only = one("pending") == "1", one("balances-only") == "1"
        wanted = set(query.get("account", []))
        accounts = []
        for a in self.accounts:
            if wanted and a.id not in wanted:
                continue
            doc: dict = {"id": a.id, "name": a.name, "currency": "USD", "balance": a.balance,
                         "available-balance": a.balance, "balance-date": a.balance_date}
            if v2:
                doc["conn_id"] = CONNECTION["conn_id"]
            else:
                doc["org"] = dict(ORG)
            if a.failing:
                errors.append({"code": "act.failed", "msg": "Failed to get all transactions. Try again later.",
                               "conn_id": CONNECTION["conn_id"], "account_id": a.id})
            if not balances_only:
                doc["transactions"] = [] if a.failing else [
                    t for t in a.transactions if _in_window(t, start, end, pending)]
            accounts.append(doc)
        errors += self.errlist
        if v2:
            return {"errlist": errors, "connections": [dict(CONNECTION)], "accounts": accounts}
        return {"errors": [e["msg"] for e in errors], "accounts": accounts}


def _in_window(t: dict, start: int | None, end: int | None, pending: bool) -> bool:
    if t.get("pending"):
        if not pending:
            return False
        when = t.get("transacted_at") or 0
    else:
        when = t["posted"]
    return (start is None or when >= start) and (end is None or when < end)


def _basic_auth(header: str | None) -> tuple[str | None, str | None]:
    if not header or not header.startswith("Basic "):
        return None, None
    try:
        user, _, password = base64.b64decode(header.removeprefix("Basic ")).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None, None
    return user, password


def _send(h: BaseHTTPRequestHandler, status: int, body: bytes, kind: str = "text/plain",
          location: str | None = None) -> None:
    h.send_response(status)
    if location is not None:
        h.send_header("Location", location)
    h.send_header("Content-Type", kind)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):                   # quiet: a test's output holds only what the client prints
        pass

    def do_GET(self):
        self.server.bridge.handle(self)

    def do_POST(self):
        self.server.bridge.handle(self)


@contextlib.contextmanager
def serve() -> Iterator[FakeBridge]:
    """A FakeBridge listening on 127.0.0.1 at a port the system picks, until the block ends: then a held answer is
    let go, the server stops and its thread is joined"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    bridge = FakeBridge(server.server_address[1])
    server.bridge = bridge
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield bridge
    finally:
        bridge.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

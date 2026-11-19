#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The stack's security matrix: requests sent to the running demo stack (deploy/Caddyfile in front of the store,
compose.yaml), each answered as the design says, one line each (`ok <name>`, `FAIL <name>: <what>`, or
`known: <name>: <why> (<what>)` for a product defect recorded and left to be decided), then `stack matrix: P/T`
(P the cases that passed, T every case, the known ones included). Exit 0 when no case FAILs.

Stdlib only; it runs on the host's python3 (tools/verify/stack.sh starts it once the stack is up). Every request is
written byte for byte on a raw socket (http.client would normalise a path, a Host or a header), one connection each
unless a case puts several requests on one.

What it checks, in order (each a table of cases, or a probe that sends several requests and reads the store's log):
  - the sign-in itself: the right passcode with client-sent X-FF-* headers gets a session and a device cookie that are
    HttpOnly and SameSite=Lax and not Secure (the request came over plain HTTP whatever the client says); a form
    sign-in is a 303 to /; another site's Origin, no Origin, a client X-FF-Host or X-Forwarded-Proto never pass the
    Origin rule (it judges by the real Host and scheme)
  - without a session: every spelling of a protected path (case, dot segments, encoded slashes, double slashes, a
    .json under a public prefix, HEAD, OPTIONS, Range, a WebSocket upgrade, client X-Forwarded-* headers) is a 302 to
    /login for a page load or a 401, never its content; the public set is 200 and holds nothing protected; the store's
    own /auth/ paths are 404; a Host the dashboard does not answer to is 421 with nothing proxied
  - with a session: the exports, the store's API, a PUT whose body the store receives, the refusals (another site's
    Origin 403, a body over 2 MB 413, a method a route does not take 405, an export that does not exist 404)
  - after sign-out, and with the static cookie of the sign-in this one replaced: 401 or 302, an export's ETag no help
  - smuggling: a GET whose body holds a whole second request, and a second request on the same connection: the body
    is never read as a request (the answers on the connection, and the store's own log)
  - the throttle: six wrong passcodes from one address with rotating X-Forwarded-For and X-FF-Client: the sixth is
    429 with Retry-After; a flood of 100 parallel wrong passcodes while a browser with a device cookie signs in: it
    gets in within 2 seconds
  - the store stopped: the proxy's own 502 still carries the security headers; started again, a session survives;
    100 sign-ins holding their body back leave a signed-in request answering within 2 seconds
  - the store publishes no port; Caddy alone behind a trusted proxy (TRUSTED_PROXIES): X-Forwarded-For read right to
    left, the scheme from X-Forwarded-Proto, and from an untrusted peer neither (stand-in upstream, own network); and
    the real store behind such a Caddy, flooded from 140 client keys past its global failure count: strangers wait
    about 3 seconds or are refused busy while a browser with a device cookie signs in within 2
  - last, removing a passcode ends the sessions it opened and no other's, and adding it again revives none.
Every answer from the stack must carry the security headers (CSP, X-Frame-Options DENY, nosniff, Referrer-Policy) and
no Server header.

Usage: stack_matrix.py --port P --passcode-file F --compose "<compose command prefix>" --ff <install>/ff
                       --etl-image I --web-image I
       stack_matrix.py --probe          (inside a container: requests as JSON on stdin, answers as JSON on stdout)
       stack_matrix.py --combine MATRIX_OUT BROWSER_OUT MATRIX_RC BROWSER_RC [--ai AI_OUT AI_RC]
                                        (the stage's line: `stack: P/T`, the browser run counted as one case, and
                                        the AI steps' cases with --ai: stack_ai.py's two phases)
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

# ---------------------------------------------------------------- raw HTTP


@dataclass
class Response:
    status: int
    reason: str
    headers: list[tuple[str, str]]
    body: bytes

    def header(self, name: str) -> str | None:
        name = name.lower()
        return next((v for k, v in self.headers if k == name), None)

    def all(self, name: str) -> list[str]:
        name = name.lower()
        return [v for k, v in self.headers if k == name]


def build(method: str, path: str, headers: Sequence[tuple[str, str]], body: bytes = b"",
          version: str = "HTTP/1.1") -> bytes:
    """A request exactly as written: the request line, the headers in order, a blank line, the body."""
    lines = [f"{method} {path} {version}", *(f"{k}: {v}" for k, v in headers)]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


def _dechunk(data: bytes, pos: int) -> tuple[bytes, int] | None:
    """A chunked body starting at `pos`: (the body, where the next message starts), or None while it is incomplete."""
    body = b""
    while True:
        eol = data.find(b"\r\n", pos)
        if eol < 0:
            return None
        size = int(data[pos:eol].split(b";", 1)[0].strip() or b"0", 16)
        pos = eol + 2
        if size == 0:
            end = data.find(b"\r\n", pos)
            while end >= 0 and end != pos:             # trailer lines, then the blank one
                pos = end + 2
                end = data.find(b"\r\n", pos)
            return (body, end + 2) if end >= 0 else None
        if len(data) < pos + size + 2:
            return None
        body += data[pos:pos + size]
        pos += size + 2


_STATUS = re.compile(r"HTTP/\d\.\d (\d{3})(?: (.*))?$")


def parse_responses(data: bytes, methods: Sequence[str], eof: bool) -> list[Response]:
    """Every complete response `data` holds, in order; `methods[i]` is the i-th request's method (a HEAD answer has
    no body). A response that says no length ends at the connection's end (`eof`)."""
    out: list[Response] = []
    pos = 0
    while pos < len(data):
        end = data.find(b"\r\n\r\n", pos)
        if end < 0:
            break
        head = data[pos:end].decode("latin-1").split("\r\n")
        m = _STATUS.match(head[0])
        if m is None:
            raise ValueError(f"not a status line: {head[0][:60]!r}")
        status = int(m.group(1))
        headers = []
        for line in head[1:]:
            key, _, value = line.partition(":")
            headers.append((key.strip().lower(), value.strip()))
        named = dict(headers)
        start = end + 4
        method = methods[len(out)] if len(out) < len(methods) else "GET"
        if method == "HEAD" or status in (204, 304) or 100 <= status < 200:
            body, nxt = b"", start
        elif "chunked" in named.get("transfer-encoding", "").lower():
            got = _dechunk(data, start)
            if got is None:
                break
            body, nxt = got
        elif "content-length" in named:
            n = int(named["content-length"])
            if len(data) - start < n:
                break
            body, nxt = data[start:start + n], start + n
        elif eof:
            body, nxt = data[start:], len(data)
        else:
            break
        out.append(Response(status, m.group(2) or "", headers, body))
        pos = nxt
        if status == 101:
            break
    return out


def exchange(addr: tuple[str, int], raw: bytes, methods: Sequence[str], expect: int,
             timeout: float = 15.0) -> list[Response]:
    """Sends `raw` on one new connection (from a thread of its own, so a server that answers before it has read a
    large body is still heard) and reads until `expect` responses are complete, the server closes, or `timeout`."""
    sock = socket.create_connection(addr, timeout=timeout)

    def send() -> None:
        try:
            sock.sendall(raw)
        except OSError:
            pass                                          # the server answered and closed before reading it all

    sender = threading.Thread(target=send, daemon=True)
    sender.start()
    data, eof = b"", False
    deadline = time.monotonic() + timeout
    try:
        while True:
            rest = deadline - time.monotonic()
            if rest <= 0:
                break
            sock.settimeout(rest)
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                break
            except OSError:
                eof = True
                break
            if not chunk:
                eof = True
                break
            data += chunk
            if len(parse_responses(data, methods, False)) >= expect:
                break
        return parse_responses(data, methods, eof)
    finally:
        sock.close()
        sender.join(timeout=1)


# ---------------------------------------------------------------- what every answer must and must not carry

SECURITY_HEADERS: dict[str, Callable[[str], bool]] = {
    "content-security-policy": lambda v: "default-src 'none'" in v and "frame-ancestors 'none'" in v,
    "x-frame-options": lambda v: v == "DENY",
    "x-content-type-options": lambda v: v == "nosniff",
    "referrer-policy": lambda v: v == "same-origin",
}
# what only a signed-in browser may see: the dashboard's page and bundle, and the exports' keys
PROTECTED_MARKERS = (b'id="root"', b"/assets/", b'"schema_version"', b'"generated_at"', b'"transactions"')


def header_problems(r: Response) -> list[str]:
    out = []
    for name, ok in SECURITY_HEADERS.items():
        value = r.header(name)
        if value is None:
            out.append(f"no {name}")
        elif not ok(value):
            out.append(f"{name} is {value[:60]!r}")
    if r.header("server") is not None:
        out.append(f"Server: {r.header('server')}")
    return out


def protected_problems(r: Response) -> list[str]:
    return [f"the body holds {m.decode()}" for m in PROTECTED_MARKERS if m in r.body]


# ---------------------------------------------------------------- the cases

NONE, SESSION, LOGGED_OUT, STATIC, DEVICE = "none", "session", "logged_out", "static", "device"
NAV = (("Accept", "text/html,application/xhtml+xml"), ("Sec-Fetch-Mode", "navigate"))
FETCH = (("Accept", "*/*"),)


@dataclass
class Ctx:
    """What the cases share: where the stack is, the cookies each state sends, and what the run learns."""
    addr: tuple[str, int]
    host: str                                     # the Host the stack's own address gives
    passcode: str = ""
    cookies: dict[str, str] = field(default_factory=dict)
    asset: str = "/assets/index.js"              # one of the bundle's files, read from the signed-in page
    etag: str = ""
    compose: list[str] = field(default_factory=list)
    ff: str = ""
    etl_image: str = ""
    web_image: str = ""
    send: Callable[..., list[Response]] = exchange

    @property
    def origin(self) -> str:
        return f"http://{self.host}"


@dataclass(frozen=True)
class Case:
    """One request: its method, path (as sent), headers, cookie state, the statuses it may get and an extra check
    (the response and the run's context -> a problem, or None). `host` None sends the stack's own Host, "" none."""
    name: str
    path: str = "/"
    method: str = "GET"
    headers: tuple[tuple[str, str], ...] = ()
    cookie: str = NONE
    expect: frozenset[int] = frozenset({200})
    check: Callable[[Response, Ctx], str | None] | None = None
    body: bytes = b""
    host: str | None = None
    version: str = "HTTP/1.1"
    known: str = ""


@dataclass(frozen=True)
class Probe:
    """Several requests, or anything else a table row cannot say: `run(ctx)` -> the problems (none: it passed)."""
    name: str
    run: Callable[[Ctx], list[str]]
    known: str = ""


@dataclass(frozen=True)
class Outcome:
    result: str                                   # ok | FAIL | known
    name: str
    detail: str = ""

    def line(self) -> str:
        if self.result == "ok":
            return f"ok {self.name}"
        if self.result == "known":
            return f"known: {self.name}: {self.detail}"
        return f"FAIL {self.name}: {self.detail}"


def expect(*codes: int) -> frozenset[int]:
    return frozenset(codes)


def request_bytes(case: Case, ctx: Ctx) -> bytes:
    headers: list[tuple[str, str]] = []
    if case.host is None:
        headers.append(("Host", ctx.host))
    elif case.host:
        headers.append(("Host", case.host))
    headers += case.headers
    if case.cookie != NONE:
        headers.append(("Cookie", ctx.cookies.get(case.cookie, "")))
    if case.body or case.method in ("POST", "PUT"):
        headers.append(("Content-Length", str(len(case.body))))
    headers.append(("Connection", "close"))
    return build(case.method, case.path, headers, case.body, case.version)


def case_problems(case: Case, ctx: Ctx) -> list[str]:
    answers = ctx.send(ctx.addr, request_bytes(case, ctx), [case.method], 1)
    if not answers:
        return ["no answer"]
    r = answers[0]
    problems = []
    if r.status not in case.expect:
        problems.append(f"status {r.status}, want {'/'.join(map(str, sorted(case.expect)))}")
    problems += header_problems(r)
    if r.status == 200 and case.cookie != SESSION:
        problems += protected_problems(r)
    if case.check is not None:
        extra = case.check(r, ctx)
        if extra:
            problems.append(extra)
    return problems


def run_one(item: Case | Probe, ctx: Ctx) -> Outcome:
    try:
        problems = case_problems(item, ctx) if isinstance(item, Case) else item.run(ctx)
    except Exception as e:  # noqa: BLE001 (a case that raises is a failed case, and the run goes on)
        problems = [f"{type(e).__name__}: {e}"]
    if not problems:
        return Outcome("ok", item.name)
    if item.known:
        return Outcome("known", item.name, f"{item.known} ({'; '.join(problems)})")
    return Outcome("FAIL", item.name, "; ".join(problems))


def summary(outcomes: Sequence[Outcome]) -> str:
    return f"stack matrix: {sum(o.result == 'ok' for o in outcomes)}/{len(outcomes)}"


# ---------------------------------------------------------------- checks a case can carry

def location(want: str) -> Callable[[Response, Ctx], str | None]:
    def check(r: Response, ctx: Ctx) -> str | None:
        if r.status in (301, 302, 303, 307, 308) and r.header("location") != want:
            return f"Location {r.header('location')!r}, want {want!r}"
        return None
    return check


def body_is(want: bytes) -> Callable[[Response, Ctx], str | None]:
    return lambda r, ctx: None if r.body.strip() == want else f"body {r.body[:40]!r}, want {want!r}"


def body_holds(want: bytes) -> Callable[[Response, Ctx], str | None]:
    return lambda r, ctx: None if want in r.body else f"the body lacks {want!r}"


def json_field(key: str, want: object) -> Callable[[Response, Ctx], str | None]:
    def check(r: Response, ctx: Ctx) -> str | None:
        try:
            got = json.loads(r.body).get(key)
        except (ValueError, AttributeError):
            return f"the body is not a JSON object: {r.body[:40]!r}"
        return None if got == want else f"{key} is {got!r}, want {want!r}"
    return check


def json_type(r: Response, ctx: Ctx) -> str | None:
    kind = (r.header("content-type") or "").split(";")[0].strip()
    return None if kind == "application/json" else f"Content-Type {kind!r}, want application/json"


def all_of(*checks: Callable[[Response, Ctx], str | None]) -> Callable[[Response, Ctx], str | None]:
    def check(r: Response, ctx: Ctx) -> str | None:
        found = [p for c in checks if (p := c(r, ctx))]
        return "; ".join(found) or None
    return check


def protected(path: str, cookie: str = NONE, label: str = "no session", method: str = "GET",
              headers: tuple[tuple[str, str], ...] = ()) -> list[Case]:
    """A protected path asked for as a page load (302 to /login; a path the store reads as an API or an export, 401)
    and as a fetch (401)."""
    return [Case(f"{label}: {method} {path} as a page load -> 302 to /login or 401", path, method, NAV + headers,
                 cookie, expect(302, 401), location("/login")),
            Case(f"{label}: {method} {path} as a fetch -> 401", path, method, FETCH + headers, cookie, expect(401))]


# Product behaviour that differs from what the security review expected, recorded and left to be decided (each such
# case prints a `known:` line and neither passes nor fails the stage). None at present.


def no_session_cases(ctx: Ctx) -> list[Case]:
    paths = ["/", "/index.html", "/theme.js", ctx.asset, "/manifest.json", "/accounts.json", "/version.json",
             "/data/exports/x.json", "/exports/../x", "//api/prefs", "/api%2Fprefs", "/API/prefs", "/api/", "/api/prefs",
             "/api/refresh", "/no-such-page", "/icons/../login.html", "/icons/%2e%2e/accounts.json",
             "/icons/%2E%2E/data/x.json", "/icons/../data/x.json", "/icons/..%2fapi%2fprefs", "/icons/%2e%2e/api/prefs",
             "/icons//../api/prefs", "/login/../api/prefs", "/login/", "/login;x", "/login.", "/login.js/..",
             "/healthz/..", "/login2", "/ICONS/icon.svg", "/icons/missing.png", "/icons/x.json", "/login.html"]
    cases = [c for p in paths for c in protected(p)]
    origin = (("Origin", ctx.origin),)
    cases += [
        Case("no session: HEAD / -> 401", "/", "HEAD", FETCH, expect=expect(401)),
        Case("no session: OPTIONS /api/prefs -> 401", "/api/prefs", "OPTIONS", FETCH, expect=expect(401)),
        Case("no session: GET / with Range -> 401", "/", "GET", FETCH + (("Range", "bytes=0-10"),), expect=expect(401)),
        Case("no session: GET /manifest.json with Range -> 401", "/manifest.json", "GET",
             FETCH + (("Range", "bytes=0-10"),), expect=expect(401)),
        Case("no session: a WebSocket upgrade of / -> 401, never 101", "/", "GET",
             FETCH + (("Connection", "Upgrade"), ("Upgrade", "websocket"), ("Sec-WebSocket-Version", "13"),
                      ("Sec-WebSocket-Key", "dGhlIHNhbXBsZSBub25jZQ==")), expect=expect(401)),
        Case("no session: GET /api/prefs with client X-Forwarded-Method/Uri and Accept text/html -> 401, never 302",
             "/api/prefs", "GET", (("Accept", "text/html"), ("X-Forwarded-Method", "GET"), ("X-Forwarded-Uri", "/")),
             expect=expect(401)),
        Case("no session: client X-Forwarded-For/Host/Proto on /manifest.json -> 401", "/manifest.json", "GET",
             FETCH + (("X-Forwarded-For", "127.0.0.1"), ("X-Forwarded-Host", "localhost"),
                      ("X-Forwarded-Proto", "https"), ("X-FF-Client", "127.0.0.1")), expect=expect(401)),
        Case("no session: PUT /api/prefs with this site's Origin -> 401", "/api/prefs", "PUT",
             FETCH + origin + (("Content-Type", "application/json"),), body=b'{"version": 1}', expect=expect(401)),
        Case("no session: POST /api/refresh with this site's Origin -> 401", "/api/refresh", "POST",
             FETCH + origin + (("Content-Type", "application/json"),), body=b"{}", expect=expect(401)),
        # public: 200 and nothing protected in it
        Case("no session: GET /login -> 200, the sign-in page", "/login", check=body_holds(b'id="sign-in"')),
        Case("no session: GET /login?e=1 -> 200", "/login?e=1", check=body_holds(b'id="sign-in"')),
        Case("no session: GET //login -> 200", "//login", check=body_holds(b'id="sign-in"')),
        Case("no session: GET /LOGIN -> 200", "/LOGIN", check=body_holds(b'id="sign-in"')),
        Case("no session: GET /login.css -> 200", "/login.css"),
        Case("no session: GET /login.js -> 200", "/login.js"),
        Case("no session: GET /site.webmanifest -> 200", "/site.webmanifest"),
        Case("no session: GET /icons/icon.svg -> 200", "/icons/icon.svg"),
        Case("no session: GET /healthz -> 200 ok", "/healthz", check=body_is(b"ok")),
        Case("no session: GET /auth/status -> 200 JSON, set up", "/auth/status", "GET", FETCH,
             check=all_of(json_type, json_field("configured", True))),
        # never reachable from outside
        Case("no session: GET /icons/ -> 401 or 404, no listing", "/icons/", expect=expect(401, 404),
             check=lambda r, c: None if b"<a href" not in r.body else "a listing"),
        *[Case(f"no session: GET {p} -> 404", p, expect=expect(404))
          for p in ("/auth/check", "/auth/Check", "/AUTH/check", "/auth//check", "/auth/login/../check",
                    "/auth/login/", "/auth/check?x", "/auth", "/auth/anything")],
        Case("no session: POST /auth/LOGIN -> 404", "/auth/LOGIN", "POST",
             FETCH + origin + (("Content-Type", "application/json"),),
             body=json.dumps({"passcode": "not-the-passcode"}).encode(), expect=expect(404)),
        Case("no session: POST /login.js -> 405 (Caddy's own error answer)", "/login.js", "POST", FETCH,
             body=b"x", expect=expect(405)),
        Case("no session: GET /login with a body -> 400", "/login", "GET", FETCH, body=b"x", expect=expect(400)),
    ]
    return cases


def host_cases(ctx: Ctx) -> list[Case]:
    port = ctx.addr[1]
    refused = ["localhost.evil.example", "127.0.0.1.evil.example", "dash.example.net.", "evil.example.net",
               f"evil.example.net:{port}"]
    cases = [Case(f"host {h}: GET {p} -> 421", p, headers=FETCH, host=h, expect=expect(421))
             for h in refused for p in ("/", "/login", "/healthz")]
    cases += [
        Case("host: HTTP/1.0 with no Host -> 421", "/login", headers=FETCH, host="", version="HTTP/1.0",
             expect=expect(421)),
        Case("host [::1]evil -> 400 or 421", "/login", headers=FETCH, host="[::1]evil", expect=expect(400, 421)),
    ]
    served = [f"LOCALHOST:{port}", f"localhost:{port}", "DASH.EXAMPLE.NET", "dash.example.net", f"[::1]:{port}",
              "nas.local", f"127.0.0.1:{port}"]
    cases += [Case(f"host {h}: GET /login -> 200", "/login", headers=FETCH, host=h) for h in served]
    cases += [Case(f"host {h}: GET /auth/status -> 200 (the store's host rule agrees)", "/auth/status",
                   headers=FETCH, host=h) for h in served]
    return cases


def signed_in_cases(ctx: Ctx) -> list[Case]:
    origin = (("Origin", ctx.origin),)
    big = b'{"version": 1, "pad": "' + b"x" * (2_500_000) + b'"}'
    cached = all_of(json_type, lambda r, c: None if "private" in (r.header("cache-control") or "")
                    else f"Cache-Control {r.header('cache-control')!r}",
                    lambda r, c: None if r.header("etag") else "no ETag")
    return [
        Case("session: GET / -> 200, the dashboard", "/", headers=NAV, cookie=SESSION, check=body_holds(b'id="root"')),
        Case("session: GET /no-such-page -> 200, the dashboard", "/no-such-page", headers=NAV, cookie=SESSION,
             check=body_holds(b'id="root"')),
        Case("session: GET /manifest.json -> 200 JSON, private", "/manifest.json", headers=FETCH, cookie=SESSION,
             check=all_of(cached, body_holds(b'"schema_version"'))),
        Case("session: GET /accounts.json -> 200 JSON", "/accounts.json", headers=FETCH, cookie=SESSION,
             check=json_type),
        Case("session: GET /version.json -> 200, never stored", "/version.json", headers=FETCH, cookie=SESSION,
             check=lambda r, c: None if r.header("cache-control") == "no-store"
             else f"Cache-Control {r.header('cache-control')!r}"),
        Case("session: GET /api/prefs -> 200 JSON", "/api/prefs", headers=FETCH, cookie=SESSION, check=json_type),
        Case("session: GET /api/refresh -> 200 JSON", "/api/refresh", headers=FETCH, cookie=SESSION, check=json_type),
        Case(f"session: GET {ctx.asset} -> 200", ctx.asset, headers=FETCH, cookie=SESSION),
        Case("session: GET /no-such.json -> 404", "/no-such.json", headers=FETCH, cookie=SESSION,
             expect=expect(404)),
        Case("session: PUT /api/prefs with another site's Origin -> 403", "/api/prefs", "PUT",
             FETCH + (("Origin", "http://evil.example.net"), ("Content-Type", "application/json")),
             cookie=SESSION, body=b'{"version": 1}', expect=expect(403)),
        Case("session: PUT /api/prefs with no Origin -> 403", "/api/prefs", "PUT",
             FETCH + (("Content-Type", "application/json"),), cookie=SESSION, body=b'{"version": 1}',
             expect=expect(403)),
        Case("session: PUT /api/prefs with a body over 2 MB -> 413", "/api/prefs", "PUT",
             FETCH + origin + (("Content-Type", "application/json"),), cookie=SESSION, body=big, expect=expect(413)),
        Case("session: DELETE /api/prefs -> 405", "/api/prefs", "DELETE", FETCH + origin, cookie=SESSION,
             expect=expect(405)),
        Case("session: a WebSocket upgrade of / -> never 101", "/", "GET",
             FETCH + (("Connection", "Upgrade"), ("Upgrade", "websocket"), ("Sec-WebSocket-Version", "13"),
                      ("Sec-WebSocket-Key", "dGhlIHNhbXBsZSBub25jZQ==")), cookie=SESSION, expect=expect(200)),
        Case("session: GET /auth/check -> 404", "/auth/check", headers=FETCH, cookie=SESSION, expect=expect(404)),
        Case("session: GET /icons/ -> the dashboard or 404, no listing", "/icons/", headers=NAV, cookie=SESSION,
             expect=expect(200, 404), check=lambda r, c: None if r.status == 404 or b'id="root"' in r.body
             else "a 200 that is not the dashboard"),
    ]


def signed_out_cases(ctx: Ctx) -> list[Case]:
    cases = []
    for state, label in ((LOGGED_OUT, "after sign-out"), (STATIC, "the old static cookie")):
        for path in ("/manifest.json", "/api/prefs", "/"):
            cases += protected(path, state, label)
    cases.append(Case("after sign-out: GET /manifest.json with If-None-Match of its ETag -> 401, not 304",
                      "/manifest.json", headers=FETCH + (("If-None-Match", ctx.etag or '"none"'),),
                      cookie=LOGGED_OUT, expect=expect(401)))
    return cases


def stopped_store_cases(ctx: Ctx) -> list[Case]:
    return [
        Case("store stopped: GET / as a page load -> 502", "/", headers=NAV, expect=expect(502)),
        Case("store stopped: GET /auth/status -> 502", "/auth/status", headers=FETCH, expect=expect(502)),
        Case("store stopped: GET /api/prefs with a session -> 502", "/api/prefs", headers=FETCH, cookie=SESSION,
             expect=expect(502)),
    ]


# ---------------------------------------------------------------- probes: the sign-in, the store's log, the throttle

def login(ctx: Ctx, passcode: str, *, headers: Sequence[tuple[str, str]] = (), cookie: str = "",
          form: bool = False, origin: str | None = None, host: str | None = None) -> Response | None:
    if form:
        body = ("passcode=" + "".join(c if c.isalnum() or c in "-_." else f"%{ord(c):02X}" for c in passcode)).encode()
        kind, accept = "application/x-www-form-urlencoded", "text/html"
    else:
        body, kind, accept = json.dumps({"passcode": passcode}).encode(), "application/json", "application/json"
    sent = [("Host", host or ctx.host), ("Accept", accept), ("Content-Type", kind),
            ("Content-Length", str(len(body)))]
    if origin is None:
        origin = ctx.origin
    if origin:
        sent.append(("Origin", origin))
    if cookie:
        sent.append(("Cookie", cookie))
    sent += [*headers, ("Connection", "close")]
    answers = ctx.send(ctx.addr, build("POST", "/auth/login", sent, body), ["POST"], 1)
    return answers[0] if answers else None


def cookie_of(r: Response, name: str) -> str | None:
    """The Set-Cookie line that sets `name`, whole."""
    return next((c for c in r.all("set-cookie") if c.split("=", 1)[0].strip() == name), None)


def pair(set_cookie: str | None) -> str:
    return set_cookie.split(";", 1)[0].strip() if set_cookie else ""


def sign_in_probe(ctx: Ctx) -> list[str]:
    """The right passcode, with client-sent X-FF-Scheme and X-Forwarded-Proto saying https: 204, a session cookie and a
    device cookie, both HttpOnly, SameSite=Lax and not Secure. The cookies become the run's valid session."""
    r = login(ctx, ctx.passcode, headers=(("X-FF-Scheme", "https"), ("X-Forwarded-Proto", "https"),
                                          ("X-FF-Client", "198.51.100.200")))
    if r is None:
        return ["no answer"]
    problems = [] if r.status == 204 else [f"status {r.status}, want 204"]
    problems += header_problems(r)
    session, device = cookie_of(r, "ff_session"), cookie_of(r, "ff_device")
    for name, line in (("ff_session", session), ("ff_device", device)):
        if not line:
            problems.append(f"no {name} cookie")
            continue
        attrs = [a.strip().lower() for a in line.split(";")[1:]]
        if "httponly" not in attrs or "samesite=lax" not in attrs:
            problems.append(f"{name} is not HttpOnly; SameSite=Lax")
        if "secure" in attrs:
            problems.append(f"{name} is Secure over plain HTTP (a client's X-FF-Scheme was believed)")
    ctx.cookies[SESSION] = pair(session)
    ctx.cookies[DEVICE] = pair(device)
    return problems


def form_sign_in_probe(ctx: Ctx) -> list[str]:
    """A form sign-in (the login page's own post) is a 303 to / with a session cookie: the session the sign-out
    probe ends."""
    r = login(ctx, ctx.passcode, form=True)
    if r is None:
        return ["no answer"]
    problems = [] if r.status == 303 and r.header("location") == "/" else [
        f"status {r.status} Location {r.header('location')!r}, want 303 to /"]
    problems += header_problems(r)
    session = cookie_of(r, "ff_session")
    if not session:
        problems.append("no ff_session cookie")
    ctx.cookies["to_sign_out"] = pair(session)
    return problems


def origin_probes() -> list[Probe]:
    def refused(name: str, origin: str, headers: tuple[tuple[str, str], ...] = ()) -> Probe:
        def run(ctx: Ctx) -> list[str]:
            r = login(ctx, ctx.passcode, origin=origin.format(host=ctx.host), headers=headers)
            if r is None:
                return ["no answer"]
            out = [] if r.status == 403 else [f"status {r.status}, want 403"]
            if r.all("set-cookie"):
                out.append("a cookie was set")
            return out + header_problems(r)
        return Probe(name, run)

    return [
        refused("sign-in with another site's Origin -> 403", origin="http://evil.example.net"),
        refused("sign-in with no Origin -> 403", origin=""),
        refused("sign-in with a client X-FF-Host and that host's Origin -> 403 (the real Host decides)",
                origin="http://evil.example.net", headers=(("X-FF-Host", "evil.example.net"),
                                                           ("X-Forwarded-Host", "evil.example.net"))),
        refused("sign-in with client X-Forwarded-Proto/X-FF-Scheme https and an https Origin -> 403 (the real scheme "
                "decides)", origin="https://{host}", headers=(("X-Forwarded-Proto", "https"), ("X-FF-Scheme", "https"))),
    ]


def first_asset(ctx: Ctx) -> list[str]:
    """The signed-in page names its bundle: one of those files is the asset the no-session cases ask for."""
    answers = ctx.send(ctx.addr, build("GET", "/", [("Host", ctx.host), ("Cookie", ctx.cookies.get(SESSION, "")),
                                                    *NAV, ("Connection", "close")]), ["GET"], 1)
    if not answers or answers[0].status != 200:
        return [f"the signed-in page did not load ({answers[0].status if answers else 'no answer'})"]
    m = re.search(rb'(/assets/[A-Za-z0-9._-]+\.js)', answers[0].body)
    if m is None:
        return ["the signed-in page names no /assets/*.js"]
    ctx.asset = m.group(1).decode()
    return []


def sign_out_probe(ctx: Ctx) -> list[str]:
    """Before: the session reads an export (its ETag kept). Sign-out: 204, the cookie cleared, the cache cleared. The
    cookie is then the run's logged-out one."""
    cookie = ctx.cookies.get("to_sign_out", "")
    pre = ctx.send(ctx.addr, build("GET", "/manifest.json", [("Host", ctx.host), ("Cookie", cookie), *FETCH,
                                                              ("Connection", "close")]), ["GET"], 1)
    problems = []
    if not pre or pre[0].status != 200:
        problems.append(f"before sign-out the export was {pre[0].status if pre else 'no answer'}, want 200")
    else:
        ctx.etag = pre[0].header("etag") or ""
    answers = ctx.send(ctx.addr, build("POST", "/auth/logout", [
        ("Host", ctx.host), ("Origin", ctx.origin), ("Cookie", cookie), ("Accept", "application/json"),
        ("Content-Length", "0"), ("Connection", "close")]), ["POST"], 1)
    if not answers:
        return problems + ["sign-out: no answer"]
    r = answers[0]
    if r.status != 204:
        problems.append(f"sign-out status {r.status}, want 204")
    cleared = cookie_of(r, "ff_session") or ""
    if "max-age=0" not in cleared.lower():
        problems.append("sign-out does not clear the session cookie")
    if r.header("clear-site-data") != '"cache"':
        problems.append("sign-out does not clear the cache")
    ctx.cookies[LOGGED_OUT] = cookie
    return problems + header_problems(r)


def compose(ctx: Ctx, *args: str, check: bool = True) -> str:
    done = subprocess.run([*ctx.compose, *args], capture_output=True, text=True, timeout=180)
    if check and done.returncode != 0:
        raise RuntimeError(f"compose {' '.join(args)} exited {done.returncode}: {done.stderr.strip()[-300:]}")
    return done.stdout


def store_log(ctx: Ctx) -> list[str]:
    """The store's own log lines (method, path, status; the sign-in's who), without compose's prefix."""
    return [line.split("| ", 1)[-1].strip() for line in compose(ctx, "logs", "--no-color", "store").splitlines()
            if line.strip()]


def log_window(ctx: Ctx, run: Callable[[], list[str]]) -> tuple[list[str], list[str]]:
    """(run's problems, the store's log lines written meanwhile). The log is read again after a moment, so a line the
    store wrote just before the run ended is in it."""
    before = len(store_log(ctx))
    problems = run()
    time.sleep(1.0)
    return problems, store_log(ctx)[before:]


def smuggled_get(path: str) -> Callable[[Ctx], bytes]:
    return lambda ctx: build("GET", path, [("Host", ctx.host)])


def smuggled_sign_in(ctx: Ctx) -> bytes:
    """A whole sign-in with the right passcode: were it read as a request, it would set a cookie."""
    body = json.dumps({"passcode": ctx.passcode}).encode()
    return build("POST", "/auth/login", [("Host", ctx.host), ("Origin", ctx.origin),
                                         ("Content-Type", "application/json"), ("Content-Length", str(len(body)))],
                 body)


def smuggle_probe(outer: str, inner: Callable[[Ctx], bytes], chunked: bool,
                  forbidden: Sequence[str]) -> Callable[[Ctx], list[str]]:
    """GET `outer` whose body is `inner` (a whole request), then GET /auth/status on the same connection: the first is
    400, no answer on the connection is `ok` or sets a cookie, a second answer (if any) is the status JSON, and the
    store's log names none of `forbidden` (nothing of the body was asked of it)."""
    def run(ctx: Ctx) -> list[str]:
        inner_req = inner(ctx)
        if chunked:
            framing = [("Transfer-Encoding", "chunked")]
            body = f"{len(inner_req):x}\r\n".encode() + inner_req + b"\r\n0\r\n\r\n"
        else:
            framing = [("Content-Length", str(len(inner_req)))]
            body = inner_req
        first = build("GET", outer, [("Host", ctx.host), *FETCH, *framing], body)
        second = build("GET", "/auth/status", [("Host", ctx.host), *FETCH, ("Connection", "close")])

        def send() -> list[str]:
            answers = ctx.send(ctx.addr, first + second, ["GET", "GET", "GET"], 3, timeout=8)
            out = []
            if not answers:
                return ["no answer"]
            if answers[0].status != 400:
                out.append(f"the GET with a body got {answers[0].status}, want 400")
            for r in answers:
                out += header_problems(r)
                if r.body.strip() == b"ok":
                    out.append("an answer on the connection is `ok`: the body was read as a request")
                if r.all("set-cookie"):
                    out.append("an answer on the connection set a cookie: the body was read as a sign-in")
            for r in answers[1:2]:
                if r.status != 200 or b'"configured"' not in r.body:
                    out.append(f"the second answer is {r.status} {r.body[:40]!r}, not the status JSON")
            if len(answers) > 2:
                out.append(f"{len(answers)} answers to two requests")
            return out

        problems, lines = log_window(ctx, send)
        for line in lines:
            if any(f in line for f in forbidden):
                problems.append(f"the store's log says {line!r}")
        return problems
    return run


def smuggling_probes() -> list[Probe]:
    probe = smuggled_get("/api/smuggled-probe")
    signed = ("POST /auth/login", "GET /auth/login", "signed in", "sign-in failed")
    return [
        Probe("smuggling: GET /auth/status whose body is GET /healthz, then GET /auth/status on one connection",
              smuggle_probe("/auth/status", smuggled_get("/healthz"), False, ("GET /auth/status",))),
        Probe("smuggling: GET /auth/status whose body is GET /api/smuggled-probe: the store never sees it",
              smuggle_probe("/auth/status", probe, False, ("smuggled-probe", "GET /auth/status"))),
        Probe("smuggling: GET /auth/status whose chunked body is GET /api/smuggled-probe: the store never sees it",
              smuggle_probe("/auth/status", probe, True, ("smuggled-probe", "GET /auth/status"))),
        Probe("smuggling: GET /auth/login whose body is a sign-in with the right passcode: no sign-in is processed",
              smuggle_probe("/auth/login", smuggled_sign_in, False, signed)),
        Probe("smuggling: GET /auth/login whose chunked body is a sign-in: no sign-in is processed",
              smuggle_probe("/auth/login", smuggled_sign_in, True, signed)),
    ]


def unknown_host_sign_in(ctx: Ctx) -> list[str]:
    """POST /auth/login and GET /healthz with a Host the dashboard does not answer to: 421, and the store is not
    asked."""
    def run() -> list[str]:
        out = []
        r = login(ctx, ctx.passcode, host="evil.example.net", origin="http://evil.example.net")
        if r is None or r.status != 421:
            out.append(f"sign-in got {r.status if r else 'no answer'}, want 421")
        elif r.all("set-cookie"):
            out.append("a cookie was set")
        return out
    problems, lines = log_window(ctx, run)
    return problems + [f"the store's log says {line!r}" for line in lines if "/auth/login" in line
                       or "signed in" in line]


def throttle_probe(ctx: Ctx) -> list[str]:
    """Six wrong passcodes from this address, each with another X-Forwarded-For and X-FF-Client: the first five are
    401, the sixth 429 with a Retry-After of whole seconds (the address Caddy saw is the throttle's key, never one the
    client wrote)."""
    statuses, problems = [], []
    for k in range(1, 7):
        r = login(ctx, f"wrong-passcode-{k}", headers=(("X-Forwarded-For", f"198.51.100.{k}"),
                                                      ("X-FF-Client", f"198.51.100.{100 + k}"),
                                                      ("X-Real-IP", f"198.51.100.{k}")))
        statuses.append(r.status if r else 0)
        if r is not None:
            problems += header_problems(r)
        if k == 6 and r is not None:
            retry = r.header("retry-after") or ""
            if not retry.isdigit() or int(retry) < 1:
                problems.append(f"the 6th has Retry-After {retry!r}")
    if statuses != [401] * 5 + [429]:
        problems.append(f"statuses {statuses}, want five 401 then 429")
    return sorted(set(problems), key=problems.index)


def stop_store(ctx: Ctx) -> list[str]:
    compose(ctx, "stop", "store")
    return []


def start_store(ctx: Ctx) -> list[str]:
    compose(ctx, "up", "-d", "--no-build", "--wait", "store")
    return []


def session_survives(ctx: Ctx) -> list[str]:
    answers = ctx.send(ctx.addr, build("GET", "/api/prefs", [("Host", ctx.host), ("Cookie", ctx.cookies[SESSION]),
                                                             *FETCH, ("Connection", "close")]), ["GET"], 1)
    return [] if answers and answers[0].status == 200 else [
        f"after the store's restart the session got {answers[0].status if answers else 'no answer'}"]


def put_reaches_store(ctx: Ctx) -> list[str]:
    """Signed in, a PUT's body passes forward_auth to the store: what it stored comes back, and a GET reads it."""
    marker = uuid.uuid4().hex
    get = build("GET", "/api/prefs", [("Host", ctx.host), ("Cookie", ctx.cookies[SESSION]), *FETCH,
                                       ("Connection", "close")])
    answers = ctx.send(ctx.addr, get, ["GET"], 1)
    if not answers or answers[0].status != 200:
        return ["could not read the prefs first"]
    doc = json.loads(answers[0].body)
    doc["stack_probe"] = marker
    body = json.dumps(doc).encode()
    put = build("PUT", "/api/prefs", [("Host", ctx.host), ("Origin", ctx.origin), ("Cookie", ctx.cookies[SESSION]),
                                       ("Content-Type", "application/json"), ("Content-Length", str(len(body))),
                                       *FETCH, ("Connection", "close")], body)
    answers = ctx.send(ctx.addr, put, ["PUT"], 1)
    if not answers or answers[0].status != 200 or marker.encode() not in answers[0].body:
        return [f"the PUT got {answers[0].status if answers else 'no answer'} without the stored body"]
    again = ctx.send(ctx.addr, get, ["GET"], 1)
    if not again or marker.encode() not in again[0].body:
        return ["a GET after the PUT does not read what was stored"]
    return header_problems(answers[0])


def flood_probe(ctx: Ctx) -> list[str]:
    """100 parallel wrong passcodes with no device cookie from this address while a browser holding a device cookie
    signs in with the right one: it gets 204 within 2 seconds; the flood's answers are all 401 or 429."""
    flood: list[int] = []
    lock = threading.Lock()

    def wrong(i: int) -> None:
        try:
            r = login(ctx, f"flood-wrong-{i}")
            code = r.status if r else 0
        except OSError:
            code = -1
        with lock:
            flood.append(code)

    threads = [threading.Thread(target=wrong, args=(i,), daemon=True) for i in range(100)]
    for t in threads:
        t.start()
    time.sleep(0.3)
    started = time.monotonic()
    r = login(ctx, ctx.passcode, cookie=ctx.cookies.get(DEVICE, ""))
    took = time.monotonic() - started
    for t in threads:
        t.join(timeout=60)
    problems = []
    if r is None or r.status != 204:
        problems.append(f"the household's browser got {r.status if r else 'no answer'}, want 204")
    elif took > 2.0:
        problems.append(f"the household's browser waited {took:.1f} s (over 2)")
    else:
        ctx.cookies["flood_session"] = pair(cookie_of(r, "ff_session"))
    odd = sorted({c for c in flood if c not in (401, 429)})
    if len(flood) != 100 or odd:
        problems.append(f"{len(flood)} flood answers, statuses outside 401/429: {odd}")
    print(f"  flood: {flood.count(401)} x 401, {flood.count(429)} x 429; the household's browser in {took:.2f} s")
    return problems


SLOW_BODIES = 100         # more than the store's own request slots (deploy/scenario_store.py REQUEST_SLOTS, 64)


def slow_bodies_probe(ctx: Ctx) -> list[str]:
    """SLOW_BODIES sign-ins that send their headers and hold their body back: Caddy reads each body before the store
    sees the request (request_buffers), so they hold Caddy, not the store's threads, and a signed-in GET /api/prefs
    still answers 200 within 2 seconds."""
    head = build("POST", "/auth/login", [("Host", ctx.host), ("Origin", ctx.origin), ("Accept", "application/json"),
                                         ("Content-Type", "application/json"), ("Content-Length", "100")])
    socks: list[socket.socket] = []
    try:
        for _ in range(SLOW_BODIES):
            s = socket.create_connection(ctx.addr, timeout=10)
            s.sendall(head)
            socks.append(s)
        time.sleep(0.5)
        started = time.monotonic()
        answers = ctx.send(ctx.addr, build("GET", "/api/prefs", [("Host", ctx.host), ("Cookie", ctx.cookies[SESSION]),
                                                                 *FETCH, ("Connection", "close")]), ["GET"], 1)
        took = time.monotonic() - started
    finally:
        for s in socks:
            s.close()
    print(f"  slow bodies: {len(socks)} held; GET /api/prefs in {took:.2f} s")
    if not answers or answers[0].status != 200:
        return [f"GET /api/prefs got {answers[0].status if answers else 'no answer'}, want 200"]
    return [f"GET /api/prefs took {took:.1f} s (over 2)"] if took > 2.0 else []


def no_published_store(ctx: Ctx) -> list[str]:
    """The store publishes no port; web publishes the stack's one port on the loopback address."""
    text = compose(ctx, "ps", "--format", "json").strip()
    rows = json.loads(text) if text.startswith("[") else [json.loads(line) for line in text.splitlines() if line]
    by = {row.get("Service"): row for row in rows}
    problems = []
    if "store" not in by:
        return ["no store service in ps"]
    store_ports = [p for p in by["store"].get("Publishers") or [] if p.get("PublishedPort")]
    if store_ports:
        problems.append(f"the store publishes {store_ports}")
    web_ports = [(p.get("URL"), p.get("PublishedPort")) for p in by.get("web", {}).get("Publishers") or []
                 if p.get("PublishedPort")]
    if not web_ports or any(url != "127.0.0.1" or port != ctx.addr[1] for url, port in web_ports):
        problems.append(f"web publishes {web_ports}, want 127.0.0.1:{ctx.addr[1]} alone")
    return problems


# invented passcodes for the passcode probe's second label and for the first label added again
SECOND_PASSCODE = "second-sample-passcode"
RENEWED_PASSCODE = "renewed-sample-passcode"


def _ff_passcode(ctx: Ctx, *args: str, passcode: str | None = None) -> str | None:
    """`ff passcode ARGS` in the install (a new passcode on standard input); None when it worked, else what it said."""
    done = subprocess.run([ctx.ff, "passcode", *args], capture_output=True, text=True, timeout=120,
                          cwd=os.path.dirname(ctx.ff), input=f"{passcode}\n" if passcode is not None else "")
    return None if done.returncode == 0 else f"ff passcode {args[0]} exited {done.returncode}: " \
                                             f"{done.stderr.strip()[-200:]}"


def _prefs_status(ctx: Ctx, cookie: str) -> int:
    answers = ctx.send(ctx.addr, build("GET", "/api/prefs", [("Host", ctx.host), ("Cookie", cookie), *FETCH,
                                                             ("Connection", "close")]), ["GET"], 1)
    return answers[0].status if answers else 0


def passcode_removed(ctx: Ctx) -> list[str]:
    """Removing a passcode (./ff passcode remove) ends every session it opened and no other: with a second label signed
    in, removing the first ends the first's sessions (the valid one and the flood's) and the second's goes on; the
    first label added again with a new passcode does not bring its old sessions back; with both removed, the store
    says sign-in is not set up."""
    problems: list[str] = []
    said = _ff_passcode(ctx, "add", "second", "--stdin", passcode=SECOND_PASSCODE)
    if said:
        return [said]
    # through the device cookie: this address has been locked by the throttle probe
    r = login(ctx, SECOND_PASSCODE, cookie=ctx.cookies.get(DEVICE, ""))
    second = pair(cookie_of(r, "ff_session")) if r is not None and r.status == 204 else ""
    if not second:
        return [f"signing in with the second label got {r.status if r else 'no answer'}, want 204"]
    said = _ff_passcode(ctx, "remove", "demo")
    if said:
        return [said]
    for state in (SESSION, "flood_session"):
        status = _prefs_status(ctx, ctx.cookies.get(state, ""))
        if status != 401:
            problems.append(f"after the first label's removal the {state} cookie got {status}, want 401")
    status = _prefs_status(ctx, second)
    if status != 200:
        problems.append(f"after the first label's removal the second label's session got {status}, want 200")
    said = _ff_passcode(ctx, "add", "demo", "--stdin", passcode=RENEWED_PASSCODE)
    if said:
        return [*problems, said]
    status = _prefs_status(ctx, ctx.cookies.get(SESSION, ""))
    if status != 401:
        problems.append(f"the first label added again brought its old session back ({status}, want 401)")
    for label in ("second", "demo"):
        said = _ff_passcode(ctx, "remove", label)
        if said:
            return [*problems, said]
    answers = ctx.send(ctx.addr, build("GET", "/auth/status", [("Host", ctx.host), *FETCH, ("Connection", "close")]),
                       ["GET"], 1)
    still = json_field("configured", False)(answers[0], ctx) if answers else "no answer"
    if still:
        problems.append(f"the store's sign-in status: {still}")
    return problems


# ---------------------------------------------------------------- Caddy alone behind a trusted proxy

ECHO = r'''
import http.server, json
class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        body = json.dumps({k: self.headers.get(k) for k in ("X-FF-Client", "X-FF-Scheme", "X-FF-Host")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    do_GET = do_POST = answer
    def log_message(self, *a):
        pass
http.server.ThreadingHTTPServer(("0.0.0.0", 8123), H).serve_forever()
'''


def probe_main(stdin: str) -> int:
    """Inside a container on the stand-in's network: each request {target, method, path, headers} sent to the target
    container's Caddy at its own address (a Host that passes the 421), once its /healthz answers; prints the answers
    as JSON ({status, body} each)."""
    reqs = json.loads(stdin)
    out = []
    ready: dict[str, str] = {}
    for req in reqs:
        target = req["target"]
        if target not in ready:
            ip = socket.gethostbyname(target)
            for _ in range(100):
                try:
                    got = exchange((ip, 8080), build("GET", "/healthz", [("Host", ip), ("Connection", "close")]),
                                   ["GET"], 1, timeout=2)
                    if got and got[0].status == 200:
                        break
                except OSError:
                    pass
                time.sleep(0.1)
            ready[target] = ip
        ip = ready[target]
        body = req.get("body", "").encode()
        headers = [("Host", f"{ip}:8080"), *[tuple(h) for h in req["headers"]], ("Content-Length", str(len(body))),
                   ("Connection", "close")]
        got = exchange((ip, 8080), build(req["method"], req["path"], headers, body), [req["method"]], 1, timeout=10)
        out.append({"status": got[0].status if got else 0, "body": got[0].body.decode("utf-8", "replace") if got
                    else "", "ip": ip})
    print(json.dumps(out))
    return 0


@functools.cache
def harness():
    """The harness's names (harness_names.py beside this file), read on the host alone: the probe and the flood run
    this file by itself in a container."""
    import harness_names
    return harness_names.load()


def _docker(*args: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
    done = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120, **kw)
    if check and done.returncode != 0:
        raise RuntimeError(f"docker {args[0]} exited {done.returncode}: {done.stderr.strip()[-300:]}")
    return done


def trusted_proxy_outcomes(ctx: Ctx, script: str) -> list[Outcome]:
    """Caddy alone (the web image) twice on a network of its own: `web` trusting the network's subnet
    (TRUSTED_PROXIES), `web2` trusting another range; upstream a stand-in that echoes the X-FF-* headers it receives;
    requests from a third container on the same network. Everything is labelled and removed by its exact name."""
    net = f"{harness().project_prefix}-stack-{os.getpid()}"
    names = [f"{net}-echo", f"{net}-web", f"{net}-web2"]
    reqs: list[dict] = []
    for k in range(1, 7):
        reqs.append({"target": names[1], "method": "POST", "path": "/auth/login",
                     "headers": [["X-Forwarded-For", f"198.51.100.{k}, 192.0.2.77"], ["Content-Type", "application/json"]],
                     "body": '{"passcode": "wrong-passcode"}'})
    reqs += [
        {"target": names[1], "method": "GET", "path": "/auth/status", "headers": [["X-Forwarded-For", "198.51.100.9"]]},
        {"target": names[1], "method": "GET", "path": "/auth/status", "headers": [["X-Forwarded-Proto", "https"]]},
        {"target": names[1], "method": "GET", "path": "/auth/status", "headers": [["X-Forwarded-Proto", "http"]]},
        {"target": names[1], "method": "GET", "path": "/auth/status",
         "headers": [["X-Forwarded-Proto", "HTTPS, https"]]},
        {"target": names[1], "method": "GET", "path": "/auth/status",
         "headers": [["X-FF-Client", "192.0.2.99"], ["X-FF-Scheme", "https"], ["X-FF-Host", "evil.example.net"]]},
        {"target": names[2], "method": "GET", "path": "/auth/status",
         "headers": [["X-Forwarded-For", "198.51.100.9"], ["X-Forwarded-Proto", "https"]]},
    ]
    try:
        _docker("network", "create", "--label", harness().label, "--internal", net)
        subnet = _docker("network", "inspect", "-f", "{{range .IPAM.Config}}{{.Subnet}} {{end}}", net).stdout.split()
        subnet = [s for s in subnet if "." in s][:1]
        if not subnet:
            raise RuntimeError("the network has no IPv4 subnet")
        user = ["--user", "12345:12345"]
        _docker("run", "-d", "--rm", "--label", harness().label, "--name", names[0], "--network", net, *user,
                ctx.etl_image, "python", "-c", ECHO)
        for name, trusted in ((names[1], subnet[0]), (names[2], "198.51.100.0/24")):
            _docker("run", "-d", "--rm", "--label", harness().label, "--name", name, "--network", net, *user,
                    "-e", f"TRUSTED_PROXIES={trusted}", "-e", f"FF_STORE_UPSTREAM={names[0]}:8123",
                    "-e", "XDG_DATA_HOME=/tmp/caddy/data", "-e", "XDG_CONFIG_HOME=/tmp/caddy/config",
                    ctx.web_image, "caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
        done = _docker("run", "--rm", "-i", "--label", harness().label, "--network", net, *user,
                       "-v", f"{script}:/probe.py:ro", ctx.etl_image, "python", "/probe.py", "--probe",
                       input=json.dumps(reqs))
        answers = json.loads(done.stdout)
    except Exception as e:  # noqa: BLE001
        return [Outcome("FAIL", "trusted proxy: Caddy alone with a stand-in store", f"{type(e).__name__}: {e}")]
    finally:
        for name in names:
            _docker("rm", "-f", name, check=False)
        _docker("network", "rm", net, check=False)
    return trusted_proxy_verdicts(answers)


def trusted_proxy_verdicts(answers: list[dict]) -> list[Outcome]:
    """The echoed headers judged: answers in the order trusted_proxy_outcomes sends its requests."""
    def echoed(i: int) -> dict:
        a = answers[i]
        return json.loads(a["body"]) if a["status"] == 200 else {"status": a["status"]}

    def verdict(name: str, problems: list[str]) -> Outcome:
        return Outcome("FAIL", name, "; ".join(problems)) if problems else Outcome("ok", name)

    out = []
    keys = [echoed(i).get("X-FF-Client") for i in range(6)]
    out.append(verdict("trusted proxy: six sign-ins with X-Forwarded-For 198.51.100.<k>, 192.0.2.77 all key on "
                       "192.0.2.77 (the right-most untrusted address), so the sixth is locked",
                       [] if keys == ["192.0.2.77"] * 6 else [f"the store was told {keys}"]))
    one = echoed(6).get("X-FF-Client")
    out.append(verdict("trusted proxy: X-Forwarded-For from the trusted peer names the client",
                       [] if one == "198.51.100.9" else [f"the store was told {one!r}"]))
    for i, want, what in ((7, "https", "https"), (8, "http", "http"), (9, "http", "'HTTPS, https'")):
        got = echoed(i).get("X-FF-Scheme")
        out.append(verdict(f"trusted proxy: X-Forwarded-Proto {what} -> the scheme is {want}",
                           [] if got == want else [f"the store was told {got!r}"]))
    peer = answers[10]
    e = echoed(10)
    problems = []
    if e.get("X-FF-Client") == "192.0.2.99" or e.get("X-FF-Scheme") != "http":
        problems.append(f"client-sent X-FF-* reached the store: {e}")
    if e.get("X-FF-Host") != f"{peer['ip']}:8080":
        problems.append(f"X-FF-Host is {e.get('X-FF-Host')!r}, not the Host the client used")
    out.append(verdict("trusted proxy: client-sent X-FF-Client/Scheme/Host are replaced by Caddy's own", problems))
    e = echoed(11)
    out.append(verdict("untrusted peer: X-Forwarded-For and X-Forwarded-Proto are not believed",
                       [] if e.get("X-FF-Scheme") == "http" and e.get("X-FF-Client") not in (None, "198.51.100.9")
                       else [f"the store was told {e}"]))
    return out


# ---------------------------------------------------------------- the global slowdown, behind a trusted proxy

FLOOD_PASSCODE = "rig-sample-passcode"     # an invented passcode, for the rig's own store alone
FLOOD_FIRST = 40                            # wrong passcodes, more than the slowdown's 30, from 40 client keys
FLOOD_SECOND = 100                          # then these in parallel, from 100 more, while the household signs in
HOUSEHOLD_KEY = "198.51.100.250"


def flood_main(stdin: str) -> int:
    """Inside a container on the rig's network: a browser signs in and keeps its device cookie; FLOOD_FIRST wrong
    passcodes (each from an X-Forwarded-For of its own, which the rig's Caddy believes) take the store past its global
    failure count; then FLOOD_SECOND wrong ones in parallel, again each from a key of its own, while the browser signs
    in with its device cookie. Prints the statuses and how long each took, as JSON."""
    req = json.loads(stdin)
    ip = socket.gethostbyname(req["target"])
    for _ in range(100):
        try:
            got = exchange((ip, 8080), build("GET", "/healthz", [("Host", ip), ("Connection", "close")]), ["GET"], 1,
                           timeout=2)
            if got and got[0].status == 200:
                break
        except OSError:
            pass
        time.sleep(0.1)
    host = f"{ip}:8080"

    def attempt(passcode: str, key: str, cookie: str = "") -> tuple[int, float, str]:
        body = json.dumps({"passcode": passcode}).encode()
        headers = [("Host", host), ("Origin", f"http://{host}"), ("Accept", "application/json"),
                   ("Content-Type", "application/json"), ("Content-Length", str(len(body))),
                   ("X-Forwarded-For", key), *((("Cookie", cookie),) if cookie else ()), ("Connection", "close")]
        started = time.monotonic()
        try:
            got = exchange((ip, 8080), build("POST", "/auth/login", headers, body), ["POST"], 1, timeout=30)
        except OSError:
            got = []
        took = time.monotonic() - started
        return (got[0].status if got else 0), took, pair(cookie_of(got[0], "ff_device")) if got else ""

    first_status, _, device = attempt(req["passcode"], HOUSEHOLD_KEY)

    def wave(count: int, prefix: str, out: list) -> list[threading.Thread]:
        lock = threading.Lock()

        def one(k: int) -> None:
            status, took, _ = attempt(f"flood-wrong-{prefix}-{k}", f"{prefix}.{k + 1}")
            with lock:
                out.append([status, round(took, 2)])
        threads = [threading.Thread(target=one, args=(k,), daemon=True) for k in range(count)]
        for t in threads:
            t.start()
        return threads

    first: list = []
    for batch in ("192.0.2", "198.51.100"):          # in two halves, each inside the sign-in section's 32 places
        for t in wave(FLOOD_FIRST // 2, batch, first):
            t.join(timeout=60)
    second: list = []
    threads = wave(FLOOD_SECOND, "203.0.113", second)
    time.sleep(0.3)
    household, took, _ = attempt(req["passcode"], HOUSEHOLD_KEY, f"{device}" if device else "")
    for t in threads:
        t.join(timeout=60)
    print(json.dumps({"first_sign_in": first_status, "device": bool(device), "first_wave": first,
                      "second_wave": second, "household": household, "household_took": round(took, 2)}))
    return 0


def global_flood_outcomes(ctx: Ctx, script: str, *, store_code: str | None = None) -> list[Outcome]:
    """The store's global slowdown, which one client address never reaches (its own lock comes after five): the real
    store (the ETL image's, its passcodes and state in a temporary folder of its own) behind Caddy (the web image)
    trusting the rig network's proxies, and flood_main in a third container. `store_code` mounts another
    scenario_store.py over the image's (a mutation run by hand). Everything is labelled and removed by its exact
    name."""
    net = f"{harness().project_prefix}-stack-flood-{os.getpid()}"
    names = [f"{net}-store", f"{net}-web"]
    home = tempfile.mkdtemp(prefix="stack-flood-")
    try:
        for part in ("secrets/web", "state/family", "state/auth", "state/refresh"):
            os.makedirs(os.path.join(home, part), mode=0o700)
        auth = _deploy_auth()
        fd = os.open(os.path.join(home, "secrets", "web", "passcodes"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(auth.hash_passcode("rig", FLOOD_PASSCODE) + "\n")
        _docker("network", "create", "--label", harness().label, "--internal", net)
        subnet = _docker("network", "inspect", "-f", "{{range .IPAM.Config}}{{.Subnet}} {{end}}", net).stdout.split()
        subnet = [s for s in subnet if "." in s][:1]
        if not subnet:
            raise RuntimeError("the network has no IPv4 subnet")
        user = ["--user", f"{os.getuid()}:{os.getgid()}"]
        code = ["-v", f"{store_code}:/opt/ff/deploy/scenario_store.py:ro"] if store_code else []
        _docker("run", "-d", "--rm", "--label", harness().label, "--name", names[0], "--network", net, *user,
                "-e", "FF_HOME=/ff", "-v", f"{home}:/ff", *code, ctx.etl_image, "python",
                "/opt/ff/deploy/scenario_store.py", "--host", "0.0.0.0", "--port", "8123", "--auth", "required")
        _docker("run", "-d", "--rm", "--label", harness().label, "--name", names[1], "--network", net,
                "--user", "12345:12345", "-e", f"TRUSTED_PROXIES={subnet[0]}", "-e", f"FF_STORE_UPSTREAM={names[0]}:8123",
                "-e", "XDG_DATA_HOME=/tmp/caddy/data", "-e", "XDG_CONFIG_HOME=/tmp/caddy/config",
                ctx.web_image, "caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
        done = _docker("run", "--rm", "-i", "--label", harness().label, "--network", net, "--user", "12345:12345",
                       "-v", f"{script}:/probe.py:ro", ctx.etl_image, "python", "/probe.py", "--flood",
                       input=json.dumps({"target": names[1], "passcode": FLOOD_PASSCODE}))
        result = json.loads(done.stdout)
    except Exception as e:  # noqa: BLE001
        return [Outcome("FAIL", "global slowdown: the real store behind a trusted proxy", f"{type(e).__name__}: {e}")]
    finally:
        for name in names:
            _docker("rm", "-f", name, check=False)
        _docker("network", "rm", net, check=False)
        shutil.rmtree(home, ignore_errors=True)
    return flood_verdicts(result)


def _deploy_auth():
    """deploy/auth.py beside this tree, loaded by its path (stdlib only), for the rig's passcode line."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "deploy", "auth.py")
    spec = importlib.util.spec_from_file_location("ff_stack_auth", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ff_stack_auth"] = module
    spec.loader.exec_module(module)
    return module


def flood_verdicts(result: dict) -> list[Outcome]:
    """flood_main's answers judged: the first wave all refused; in the second (past the global failure count) some
    attempts answered only after the slowdown's wait (at least 2.5 s) and some refused at once as busy (429), none
    signed in; the household's browser, with its device cookie, signed in within 2 seconds."""
    name = ("global slowdown: past 30 failures from 140 client keys, strangers wait about 3 s or are refused busy; "
            "a browser with a device cookie signs in within 2 s")
    problems = []
    if result.get("first_sign_in") != 204 or not result.get("device"):
        problems.append(f"the browser's first sign-in got {result.get('first_sign_in')} and no device cookie")
    first, second = result.get("first_wave", []), result.get("second_wave", [])
    if len(first) != FLOOD_FIRST or any(s not in (401, 429) for s, _ in first):
        problems.append(f"the first wave: {sorted({s for s, _ in first})} over {len(first)} answers")
    slowed = [t for s, t in second if s == 401 and t >= 2.5]
    busy = [s for s, _ in second if s == 429]
    if len(second) != FLOOD_SECOND or any(s not in (401, 429) for s, _ in second):
        problems.append(f"the second wave: {sorted({s for s, _ in second})} over {len(second)} answers")
    if not slowed:
        problems.append("no stranger waited for the slowdown (none answered 401 after 2.5 s or more)")
    if not busy:
        problems.append("no stranger was refused as busy (429)")
    if result.get("household") != 204:
        problems.append(f"the household's browser got {result.get('household')}, want 204")
    elif result.get("household_took", 99) > 2.0:
        problems.append(f"the household's browser waited {result.get('household_took')} s (over 2)")
    print(f"  global slowdown: {len(slowed)} strangers waited, {len(busy)} refused busy; the household's browser in "
          f"{result.get('household_took')} s")
    return [Outcome("FAIL", name, "; ".join(problems)) if problems else Outcome("ok", name)]


# ---------------------------------------------------------------- the stage's line

_MATRIX = re.compile(r"^stack matrix: (\d+)/(\d+)$", re.M)
_BROWSER = re.compile(r"^stack browser: (\d+) routes, 0 violations$", re.M)
_AI = re.compile(r"^stack ai (on|off): (\d+)/(\d+)$", re.M)


def combine(matrix_out: str, browser_out: str, matrix_rc: int, browser_rc: int, ai_out: str | None = None,
            ai_rc: int = 0) -> tuple[str, bool]:
    """The stage's lines (the known cases, each on its own line, then `stack: P/T`, the browser run one case, the AI
    steps' cases added when `ai_out` is given) and whether the stage passed: the matrix printed its summary and failed
    nothing, the browser run printed its clean line and exited 0, and the AI steps (when given) printed both phases'
    summaries, failed nothing and exited 0."""
    m = _MATRIX.findall(matrix_out)
    known = [line for line in matrix_out.splitlines() if line.startswith("known: ")]
    failed = [line for line in matrix_out.splitlines() if line.startswith("FAIL ")]
    browser_ok = browser_rc == 0 and _BROWSER.search(browser_out) is not None
    if not m:
        return "stack: the matrix printed no summary", False
    passed, total = map(int, m[-1])
    ai_ok = True
    if ai_out is not None:
        phases = {phase: (int(p), int(t)) for phase, p, t in _AI.findall(ai_out)}
        if sorted(phases) != ["off", "on"]:
            return "stack: the AI steps printed no summary of each phase", False
        passed += sum(p for p, _ in phases.values())
        total += sum(t for _, t in phases.values())
        ai_ok = ai_rc == 0 and not any(line.startswith("FAIL ") for line in ai_out.splitlines())
    lines = [*known, f"stack: {passed + browser_ok}/{total + 1}"]
    return "\n".join(lines), matrix_rc == 0 and not failed and browser_ok and ai_ok


# ---------------------------------------------------------------- the run

def plan(ctx: Ctx, script: str) -> list[Case | Probe | Callable[[], list[Outcome]]]:
    """Every case in the order they run (each phase's table is built when it is reached, from what the run knows by
    then: the bundle's file name, the export's ETag)."""
    return [
        Probe("sign-in: the right passcode with client X-FF-Scheme https -> 204, HttpOnly SameSite=Lax cookies, not "
              "Secure", sign_in_probe),
        Probe("sign-in: a form post -> 303 to / with a session", form_sign_in_probe),
        *origin_probes(),
        Probe("the signed-in page names its bundle", first_asset),
        lambda: [run_one(c, ctx) for c in no_session_cases(ctx)],
        lambda: [run_one(c, ctx) for c in signed_in_cases(ctx)],
        Probe("session: PUT /api/prefs passes forward_auth with its body; the store stores it", put_reaches_store),
        Probe("sign-out: 204, the session cookie and the cache cleared", sign_out_probe),
        lambda: [run_one(c, ctx) for c in signed_out_cases(ctx)],
        *smuggling_probes(),
        lambda: [run_one(c, ctx) for c in host_cases(ctx)],
        Probe("host evil.example.net: POST /auth/login -> 421, the store not asked", unknown_host_sign_in),
        Probe("throttle: six wrong passcodes, rotating X-Forwarded-For and X-FF-Client -> five 401, then 429 with "
              "Retry-After", throttle_probe),
        Probe("the store stopped", stop_store),
        lambda: [run_one(c, ctx) for c in stopped_store_cases(ctx)],
        Probe("the store started again and healthy", start_store),
        Probe("a session survives the store's restart", session_survives),
        Probe(f"slow bodies: {SLOW_BODIES} sign-ins holding their body; a signed-in GET /api/prefs answers 200 "
              "within 2 s", slow_bodies_probe),
        Probe("flood: 100 parallel wrong passcodes; a browser with a device cookie signs in within 2 s", flood_probe),
        Probe("the store publishes no port; web only 127.0.0.1:<port>", no_published_store),
        lambda: trusted_proxy_outcomes(ctx, script),
        lambda: global_flood_outcomes(ctx, script),
        Probe("removing a passcode ends the sessions it opened, and only those; adding it again brings none back",
              passcode_removed),
    ]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--flood", action="store_true")
    ap.add_argument("--combine", nargs=4, metavar=("MATRIX_OUT", "BROWSER_OUT", "MATRIX_RC", "BROWSER_RC"))
    ap.add_argument("--ai", nargs=2, metavar=("AI_OUT", "AI_RC"))
    ap.add_argument("--port", type=int)
    ap.add_argument("--passcode-file")
    ap.add_argument("--compose", default="")
    ap.add_argument("--ff", default="")
    ap.add_argument("--etl-image", default="", help="default: the harness's runtime ETL image")
    ap.add_argument("--web-image", default="", help="default: the harness's runtime web image")
    args = ap.parse_args(argv)
    if args.probe:
        return probe_main(sys.stdin.read())
    if args.flood:
        return flood_main(sys.stdin.read())
    if args.combine:
        mo, bo, mrc, brc = args.combine
        ai_out, ai_rc = None, 0
        if args.ai:
            try:
                with open(args.ai[0]) as fa:
                    ai_out = fa.read()
            except FileNotFoundError:
                ai_out = ""
            ai_rc = int(args.ai[1])
        with open(mo) as fm, open(bo) as fb:
            text, ok = combine(fm.read(), fb.read(), int(mrc), int(brc), ai_out, ai_rc)
        print(text)
        return 0 if ok else 1
    if not (args.port and args.passcode_file and args.compose and args.ff):
        ap.error("--port, --passcode-file, --compose and --ff are required")
    with open(args.passcode_file) as fh:
        passcode = fh.read().strip()
    ctx = Ctx(("127.0.0.1", args.port), f"127.0.0.1:{args.port}", passcode=passcode,
              compose=args.compose.split(), ff=args.ff, etl_image=args.etl_image or harness().etl_rt,
              web_image=args.web_image or harness().web_rt)
    ctx.cookies[STATIC] = "ff_auth=" + "s" * 43
    outcomes: list[Outcome] = []
    for step in plan(ctx, os.path.abspath(__file__)):
        got = [run_one(step, ctx)] if isinstance(step, (Case, Probe)) else step()
        for o in got:
            print(o.line(), flush=True)
        outcomes += got
    print(summary(outcomes))
    return 1 if any(o.result == "FAIL" for o in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())

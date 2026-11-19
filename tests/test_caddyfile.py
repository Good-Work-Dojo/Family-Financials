# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The web server's config (deploy/Caddyfile), read as a tree of directives: what it serves without a session (exactly
the sign-in page, its two files, the icons, the web manifest and the health check, plus the sign-in itself, which the
store guards), that a Host it does not know is refused before any of it and a GET or HEAD carrying a body right after,
that everything else sits behind the one forward_auth, that every request passed to the store carries the three headers
the store trusts (deploy/auth.py) set by this server and stripped from the client's, on a connection of its own, the
scheme a trusted proxy may vouch for, the response headers (the server's own error answers included), the ports a
domain is served on, and that nothing of the static-cookie gate is left. When FF_CADDY_ADAPTED names Caddy's own JSON of
the file (`caddy adapt`, which tools/verify/ci.sh runs with the image's defaults and with a domain), the same properties
are also checked on what Caddy made of it.

Reads files only; no network, no Caddy."""
from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from conftest import ROOT

CADDYFILE = ROOT / "deploy" / "Caddyfile"
UPSTREAM = "{$FF_STORE_UPSTREAM:store:8123}"
SITE = ["{$SITE_ADDRESS::8080},", "http://127.0.0.1:8080"]
# the content-security policy the dashboard is built for: script, style, images, fonts, fetches and the manifest from
# its own origin only, React's style attributes, no inline script or style block, no frames, no foreign form target
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; manifest-src 'self'; form-action 'self'; base-uri 'none'; "
       "frame-ancestors 'none'")
STORE_HEADERS = {"X-FF-Client": "{client_ip}", "X-FF-Scheme": "{http.vars.ff_scheme}", "X-FF-Host": "{hostport}"}
# what Caddy makes of each in its JSON
STORE_HEADERS_JSON = {"x-ff-client": "{http.vars.client_ip}", "x-ff-scheme": "{http.vars.ff_scheme}",
                      "x-ff-host": "{http.request.hostport}"}
PUBLIC = {"/login", "/login.css", "/login.js", "/icons/*", "/site.webmanifest", "/healthz"}
SIGN_IN = {"/auth/login", "/auth/logout", "/auth/status"}
STRIPPED = ["-X-FF-Client", "-X-FF-Scheme", "-X-FF-Host", "-X-Forwarded-For", "-X-Forwarded-Proto",
            "-X-Forwarded-Host", "-X-Forwarded-Method", "-X-Forwarded-Uri"]
NO_BODY = "A GET or HEAD request carries no body."


@dataclass
class Node:
    """one directive: its words (the first is its name) and the block that follows it, if any"""
    words: list[str]
    line: int
    children: list[Node] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.words[0]

    @property
    def args(self) -> list[str]:
        return self.words[1:]

    def find(self, name: str) -> list[Node]:
        return [c for c in self.children if c.name == name]

    def one(self, name: str) -> Node:
        found = self.find(name)
        assert len(found) == 1, f"line {self.line}: {len(found)} {name!r} in {self.words}"
        return found[0]

    def walk(self):
        for c in self.children:
            yield c
            yield from c.walk()


def parse(text: str) -> Node:
    """The Caddyfile as a tree: one directive a line, a `{` ending the line opening its block and a `}` alone closing
    it; `#` starts a comment outside quotes; a quoted word is one word without its quotes."""
    root = Node(["<file>"], 0)
    stack = [root]
    for n, raw in enumerate(text.splitlines(), 1):
        lexer = shlex.shlex(raw, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        words = list(lexer)
        if not words:
            continue
        if words == ["}"]:
            stack.pop()
            assert len(stack) >= 1, f"line {n}: a `}}` with no block open"
            continue
        opens = words[-1] == "{"
        # a block with no words before it is the global options
        node = Node((words[:-1] if opens else words) or ["<global>"], n)
        stack[-1].children.append(node)
        if opens:
            stack.append(node)
    assert len(stack) == 1, "every block is closed"
    return root


TEXT = CADDYFILE.read_text("utf-8")
TREE = parse(TEXT)


def site() -> Node:
    assert [c.name for c in TREE.children] == ["<global>", "(security_headers)", SITE[0]], \
        "the global options, the security headers' snippet, then one site block"
    s = TREE.children[2]
    assert s.words == SITE, "the site on SITE_ADDRESS (plain HTTP on 8080 by default), and the container's own address"
    return s


def route() -> Node:
    s = site()
    assert [c.name for c in s.children] == ["route", "handle_errors"], \
        "the site is one route block (its steps run in the order written), then the server's own error answers"
    return s.children[0]


def snippet() -> Node:
    return TREE.children[1]


def matchers(block: Node) -> dict[str, Node]:
    """the named matchers a block defines, by name"""
    return {c.name: c for c in block.children if c.name.startswith("@")}


def paths_of(block: Node, ref: str) -> list[str]:
    """the paths a handle's matcher names: a named matcher's `path` words, or a path given inline"""
    if not ref.startswith("@"):
        return [ref]
    m = matchers(block)[ref]
    return m.args[1:] if m.args and m.args[0] == "path" else [w for c in m.find("path") for w in c.args]


def handles(block: Node) -> list[Node]:
    return block.find("handle")


def adapted() -> dict | None:
    """Caddy's own JSON of the file, when the gate hands one in (FF_CADDY_ADAPTED)"""
    path = os.environ.get("FF_CADDY_ADAPTED")
    return json.loads(Path(path).read_text("utf-8")) if path else None


def objects(value):
    """every JSON object in `value`, at any depth"""
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from objects(v)
    elif isinstance(value, list):
        for v in value:
            yield from objects(v)


def test_the_parser_reads_blocks_quotes_and_comments():
    t = parse('a {\n\t# note\n\tb "x y" {$V:d} # tail\n\t@m {\n\t\tpath /p\n\t}\n}\n')
    assert [(c.words, [(g.words, [h.words for h in g.children]) for g in c.children]) for c in t.children] == [
        (["a"], [(["b", "x y", "{$V:d}"], []), (["@m"], [["path", "/p"]])])]


def test_the_global_options_set_no_admin_api_the_ports_the_server_timeouts_and_the_trusted_proxies():
    first = TREE.children[0]
    assert first.name == "<global>", "the file starts with the global options"
    assert first.one("admin").args == ["off"]
    assert first.one("http_port").args == ["8080"] and first.one("https_port").args == ["8443"]
    servers = first.one("servers")
    assert servers.one("trusted_proxies").args == ["static", "{$TRUSTED_PROXIES}"]
    assert servers.one("trusted_proxies_strict").args == [], "X-Forwarded-For read right to left"
    timeouts = {c.name: c.args for c in servers.one("timeouts").children}
    assert timeouts == {"read_header": ["10s"], "read_body": ["15s"], "idle": ["2m"]}
    assert "auto_https" not in {c.name for c in first.children}, "a domain SITE_ADDRESS gets automatic HTTPS"


def test_a_host_this_server_does_not_know_is_refused_before_anything_else():
    r = route()
    names = [c.name for c in r.children]
    refuse = next(i for i, c in enumerate(r.children) if c.name == "respond" and c.args[:1] == ["@unknown_host"])
    assert r.children[refuse].args[-1] == "421"
    assert [n for n in names[:refuse] if not n.startswith("@")] == ["import"], \
        "only the response headers are set before the host check"
    assert r.children[0].args == ["security_headers"]
    m = matchers(r)["@unknown_host"]
    nots = [c.args for c in m.find("not")]
    assert nots[0] == ["protocol", "https"]
    assert nots[1][:2] == ["vars_regexp", "{http.request.host}"]
    assert nots[2] == ["host", "{$FF_HOSTNAMES}", "no-name.invalid"]
    assert len(nots) == 3 and len(m.children) == 3


@pytest.mark.parametrize("host, known", [
    ("localhost", True), ("LOCALHOST", True), ("nas.local", True), ("kitchen.nas.local", True),
    ("192.0.2.10", True), ("198.51.100.7", True), ("2001:db8::1", True), ("[2001:db8::1]", True), ("::1", True),
    ("example.net", False), ("dash.example.net", False), ("localhost.example.net", False), ("local", False),
    ("192.0.2.10.example.net", False), ("nas.local.example.net", False), ("", False),
])
def test_the_hosts_known_without_a_listing(host, known):
    """the host check's pattern, as Caddy's RE2 reads it (the constructs it uses read the same in Python's re)"""
    pattern = matchers(route())["@unknown_host"].find("not")[1].args[2]
    assert bool(re.fullmatch(pattern, host)) is known


def test_a_get_or_head_carrying_a_body_is_refused_right_after_the_host_check():
    r = route()
    after = [c for c in r.children if not c.name.startswith("@")][2:4]
    assert [(c.name, c.args) for c in after] == [("respond", ["@read_with_length", NO_BODY, "400"]),
                                                 ("respond", ["@read_chunked", NO_BODY, "400"])]
    assert all(c.one("close").args == [] for c in after), "and the connection is closed"
    m = matchers(r)
    assert [(c.name, c.args) for c in m["@read_with_length"].children] == [
        ("method", ["GET", "HEAD"]), ("header", ["Content-Length", "*"]), ("not", ["header", "Content-Length", "0"])]
    assert [(c.name, c.args) for c in m["@read_chunked"].children] == [
        ("method", ["GET", "HEAD"]), ("header", ["Transfer-Encoding", "*"])]


@pytest.mark.parametrize("site_address, beside_a_domain", [
    ("example.net", True), ("dash.example.net", True), (":8080", False), ("", False), ("http://dash.example.net", False),
    ("dash.example.net:8443", False), ("192.0.2.10:8080", False), ("[2001:db8::1]:8080", False),
])
def test_plain_http_beside_a_domain_answers_the_health_check_and_redirects_everything_else(site_address,
                                                                                          beside_a_domain):
    """With a domain in SITE_ADDRESS (a name, no colon) the plain-HTTP side (the container's own address, port 8080)
    answers /healthz and sends every other request to the site's name over HTTPS, before anything is proxied or
    served; with an address (`:8080`, the default), a scheme or a port it never matches. Read as Caddy's RE2 reads the
    pattern (the construct reads the same in Python's re)."""
    r = route()
    nodes = [c for c in r.children if not c.name.startswith("@")]
    redirect = next(i for i, c in enumerate(nodes) if c.name == "redir")
    assert [c.name for c in nodes[:redirect]] == ["import", "respond", "respond", "respond"], \
        "after the headers, the host check and the GET-with-body refusals; before anything else"
    assert nodes[redirect].args == ["@plain_beside_https", "https://{env.SITE_ADDRESS}{uri}", "308"]
    m = matchers(r)["@plain_beside_https"]
    assert [(c.name, c.args) for c in m.children] == [
        ("protocol", ["http"]), ("not", ["path", "/healthz"]), ("vars_regexp", ["{env.SITE_ADDRESS}", "^[^:]+$"])]
    assert bool(re.fullmatch(m.one("vars_regexp").args[1], site_address)) is beside_a_domain


def test_the_scheme_is_the_connections_unless_a_trusted_proxy_says_exactly_https_or_http():
    r = route()
    steps = [(c.name, c.args) for c in r.children if c.name == "vars"]
    assert steps == [("vars", ["ff_scheme", "{scheme}"]), ("vars", ["@proxied_https", "ff_scheme", "https"]),
                     ("vars", ["@proxied_http", "ff_scheme", "http"])]
    for scheme in ("https", "http"):
        assert [(c.name, c.args) for c in matchers(r)[f"@proxied_{scheme}"].children] == [
            ("vars", ["{http.vars.trusted_proxy}", "true"]), ("header", ["X-Forwarded-Proto", scheme])]
    order = [c.name for c in r.children]
    assert max(i for i, n in enumerate(order) if n == "vars") < order.index("request_header"), \
        "the proxy's word is read before the client's X-Forwarded-* headers are dropped"


def test_the_response_headers_are_set_on_every_answer_and_hsts_only_over_tls():
    sn = snippet()
    first, tls = sn.children[0], sn.children[2]
    assert first.name == "header" and first.args == []
    set_ = {c.name: c.args for c in first.children}
    assert set_ == {"Content-Security-Policy": [CSP], "Referrer-Policy": ["same-origin"],
                    "X-Content-Type-Options": ["nosniff"], "X-Frame-Options": ["DENY"],
                    "Permissions-Policy": ["camera=(), microphone=(), geolocation=()"], "-Server": [], "-Via": [],
                    "defer": []}
    assert matchers(sn)["@tls"].args == ["protocol", "https"]
    assert tls.name == "header" and tls.args == ["@tls"]
    assert {c.name: c.args for c in tls.children} == {"Strict-Transport-Security": ["max-age=31536000"], "defer": []}
    assert [n for n in TREE.walk() if n.name == "Strict-Transport-Security"] == [tls.children[0]]
    # the server's own error answers carry them too, with the status in words
    errors = site().children[1]
    assert [(c.name, c.args) for c in errors.children] == [
        ("import", ["security_headers"]), ("respond", ["{err.status_code} {err.status_text}", "{err.status_code}"])]
    assert [n.args for n in TREE.walk() if n.name == "import"] == [["security_headers"], ["security_headers"]]


def test_the_headers_the_store_trusts_are_stripped_from_the_client_before_anything_is_proxied():
    r = route()
    assert [c.args[0] for c in r.find("request_header")] == STRIPPED
    first_handle = next(i for i, c in enumerate(r.children) if c.name == "handle")
    assert all(i < first_handle for i, c in enumerate(r.children) if c.name == "request_header")
    assert r.one("request_body").one("max_size").args == ["2MB"]


def proxies() -> list[Node]:
    return [n for n in TREE.walk() if n.name in ("reverse_proxy", "forward_auth")]


def test_every_request_to_the_store_carries_the_three_headers_this_server_sets_on_a_connection_of_its_own():
    found = proxies()
    assert len(found) == 3, "the sign-in, forward_auth's question and the API"
    for p in found:
        assert p.args == [UPSTREAM], f"line {p.line}"
        set_ = {c.args[0]: c.args[1] for c in p.find("header_up")}
        assert set_ == STORE_HEADERS, f"line {p.line}"
        # one request per connection: nothing one request leaves unread is ever read as the next one's
        assert p.one("transport").args == ["http"] and p.one("transport").one("keepalive").args == ["off"]


def test_the_public_set_is_exactly_the_sign_in_page_its_files_the_icons_the_manifest_and_the_health_check():
    r = route()
    *before, protected = handles(r)
    assert protected.args == [], "the last handle is the one behind forward_auth, for every other path"
    public, store, refused = set(), set(), set()
    for h in before:
        assert len(h.args) == 1, f"line {h.line}: every handle before the protected one names its paths"
        body = [c.name for c in h.children]
        paths = paths_of(r, h.args[0])
        if "file_server" in body:
            assert h.one("root").args == ["*", "/srv/public"], f"line {h.line}: public files come from /srv/public"
            public.update(paths)
        elif body == ["respond"] and h.children[0].args == ["ok", "200"]:
            public.update(paths)
        elif body == ["reverse_proxy"]:
            store.update(paths)
        elif body == ["respond"] and h.children[0].args == ["404"]:
            refused.update(paths)
        else:
            pytest.fail(f"line {h.line}: a handle outside forward_auth that does something else: {body}")
    assert public == PUBLIC
    assert store == SIGN_IN
    assert refused == {"/auth", "/auth/*"}, "/auth/check and the store's other paths are never reachable from outside"
    # each public path exactly: /login is the page itself, and an icon only where the file exists in /srv/public
    login = next(h for h in before if h.args == ["@login"])
    assert login.one("rewrite").args == ["*", "/login.html"]
    icons = matchers(r)["@icons"]
    assert icons.one("path").args == ["/icons/*"] and icons.one("file").one("root").args == ["/srv/public"]
    # nothing at the route's own level serves or answers but the host check, the GET-with-body refusals and, beside a
    # domain, plain HTTP's redirect to it
    assert {c.name for c in r.children if not c.name.startswith("@")} == {
        "import", "respond", "redir", "vars", "request_header", "request_body", "encode", "handle"}
    assert [c.args[0] for c in r.find("respond")] == ["@unknown_host", "@read_with_length", "@read_chunked"]
    assert [c.args[0] for c in r.find("redir")] == ["@plain_beside_https"]
    assert not [n for n in TREE.walk() if n.name in ("browse", "precompressed")], "no listing, no precompressed files"


def test_everything_else_sits_behind_the_one_forward_auth():
    found = [n for n in TREE.walk() if n.name == "forward_auth"]
    assert len(found) == 1
    protected = handles(route())[-1]
    assert protected.children[0] is found[0], "forward_auth comes first in the protected handle"
    assert found[0].one("uri").args == ["/auth/check"]
    assert not found[0].find("copy_headers"), "the store's answer carries nothing for the request to take on"
    inner = matchers(protected)
    assert inner["@api"].args == ["path", "/api/*"]
    assert inner["@version"].args == ["path", "/version.json"]
    assert [c.args for c in inner["@exports"].children] == [["*.json"], ["path", "/version.json", "/api/*"]]
    assert [c.name for c in inner["@exports"].children] == ["path", "not"]
    by_ref = {h.args[0] if h.args else "": h for h in handles(protected)}
    assert set(by_ref) == {"@api", "@version", "@exports", ""}
    assert [c.name for c in by_ref["@api"].children] == ["reverse_proxy"]
    assert by_ref["@version"].one("root").args == ["*", "/srv/site"]
    assert by_ref["@version"].one("header").args == ["Cache-Control", "no-store"]
    assert by_ref["@exports"].one("root").args == ["*", "/srv/exports"]
    assert by_ref["@exports"].one("header").args == ["Cache-Control", "private, no-cache"], \
        "revalidated every load, and never kept by a cache shared with others"
    spa = by_ref[""]
    assert spa.one("root").args == ["*", "/srv/site"]
    assert spa.one("route").one("try_files").args == ["{path}", "/index.html"]
    assert matchers(spa.one("route"))["@shell"].args == ["path", "/", "/index.html", "/theme.js"]


def _size(text: str) -> int:
    """A Caddyfile size (`2KiB`, `2MB`) in bytes, as Caddy reads it (KiB 1024, MB 1,000,000)."""
    m = re.fullmatch(r"(\d+)(KiB|MiB|KB|MB)", text)
    assert m, text
    return int(m.group(1)) * {"KiB": 1024, "MiB": 1024 ** 2, "KB": 1000, "MB": 1000 ** 2}[m.group(2)]


def test_every_body_the_store_takes_is_read_whole_here_before_the_store_sees_it():
    """A client that holds its body back holds Caddy, not one of the store's threads: the sign-in's proxy buffers
    more than the store reads of a sign-in, the API's as much as the request body limit lets in; forward_auth's
    question carries no body."""
    # deploy/ is on sys.path (tests/conftest.py)
    import auth
    import scenario_store
    sign_in = handles(route())[[h.args for h in handles(route())].index(["@sign_in"])].one("reverse_proxy")
    # the API's proxy: the one inside the protected handle
    api_proxy = {h.args[0] if h.args else "": h for h in handles(handles(route())[-1])}["@api"].one("reverse_proxy")
    largest = max(scenario_store.MAX_BYTES, scenario_store.MAX_ANSWERS_BYTES, scenario_store.MAX_PREFS_BYTES)
    assert _size(sign_in.one("request_buffers").args[0]) >= auth.MAX_LOGIN_BYTES
    assert _size(api_proxy.one("request_buffers").args[0]) == _size(route().one("request_body").one("max_size").args[0])
    assert _size(api_proxy.one("request_buffers").args[0]) >= largest
    by_size = sorted(_size(p.one("request_buffers").args[0]) for p in (sign_in, api_proxy))
    assert not [n for n in TREE.walk() if n.name == "forward_auth"][0].find("request_buffers")
    doc = adapted()
    if doc is not None:
        for steps in site_routes(doc):
            buffers = sorted(h.get("request_buffers") or 0 for h in objects(steps) if h.get("handler") == "reverse_proxy")
            assert buffers == [0, by_size[0], by_size[1]], "forward_auth's none, the sign-in's, the API's"


def test_nothing_of_the_static_cookie_gate_is_left():
    assert "header_regexp" not in TEXT
    assert "FF_AUTH_TOKEN" not in TEXT
    assert "ff_auth" not in TEXT
    # the old page's path is no public path: /login is (the rewrite to the file behind it is no matcher)
    matched = {w for n in TREE.walk() if n.name == "path" or (n.name.startswith("@") and n.args[:1] == ["path"])
               for w in n.words[1:] if w.startswith("/")}
    assert "/login" in matched and "/login.html" not in matched


def site_routes(doc: dict) -> list[list[dict]]:
    """each server's list of the site's steps: the routes list that holds the host check"""
    found = []
    for server in doc["apps"]["http"]["servers"].values():
        lists = [o["routes"] for o in objects(server.get("routes", [])) if isinstance(o.get("routes"), list)]
        mine = [rs for rs in lists if any(h.get("status_code") == 421 for r in rs for h in r.get("handle", []))]
        assert len(mine) == 1, "one list of steps per server holds the host check"
        found.append(mine[0])
    return found


def test_caddys_own_reading_of_the_file_when_the_gate_hands_one_in():
    """With FF_CADDY_ADAPTED (the gate's `caddy adapt`, with the defaults and with a domain), the properties above on
    Caddy's JSON; without it, on the text the other tests read (each check runs once either way)."""
    doc = adapted()
    if doc is None:
        assert len([n for n in TREE.walk() if n.name == "forward_auth"]) == 1 and CSP in TEXT
        return
    text = json.dumps(doc)
    assert "header_regexp" not in text and "FF_AUTH_TOKEN" not in text
    http = doc["apps"]["http"]
    assert (http.get("http_port"), http.get("https_port")) == (8080, 8443)
    servers = http["servers"]
    for name, server in servers.items():
        assert server.get("trusted_proxies_strict") == 1, name
        assert (server.get("trusted_proxies") or {}).get("source") == "static", name
    # plain HTTP on 8080 answers the container's own address in either form: the whole site (`:8080`), or the
    # address alone beside a domain's redirect
    [plain] = [s for s in servers.values() if s["listen"] == [":8080"]]
    hosts = [m.get("host") for r in plain["routes"] for m in r.get("match", [])]
    assert hosts in ([], [["127.0.0.1"]]), hosts
    for steps in site_routes(doc):
        kinds = [[h["handler"] for h in r["handle"]] for r in steps]
        refuse = next(i for i, r in enumerate(steps) if any(h.get("status_code") == 421 for h in r["handle"]))
        assert all(k == ["headers"] for k in kinds[:refuse]), "only the response headers come before the host check"
        assert all("response" in h and "request" not in h for r in steps[:refuse] for h in r["handle"])
        [check] = steps[refuse]["match"]
        assert list(check) == ["not"] and len(check["not"]) == 3, "one matcher set: none of the three known kinds"
        assert check["not"][0] == {"protocol": "https"}
        assert "no-name.invalid" in check["not"][2]["host"]
        for at, header in ((refuse + 1, "Content-Length"), (refuse + 2, "Transfer-Encoding")):
            [refusal] = steps[at]["handle"]
            assert (refusal["handler"], refusal["status_code"], refusal.get("close")) == ("static_response", 400, True)
            [m] = steps[at]["match"]
            assert m["method"] == ["GET", "HEAD"] and header in m["header"]
        assert not any(h["handler"] == "reverse_proxy" for r in steps[:refuse + 3] for h in r["handle"])
        # beside a domain, plain HTTP answers the health check and redirects the rest to the site's name over HTTPS
        [redirect] = steps[refuse + 3]["handle"]
        assert (redirect["handler"], redirect["status_code"]) == ("static_response", 308)
        assert redirect["headers"]["Location"] == ["https://{env.SITE_ADDRESS}{http.request.uri}"]
        assert steps[refuse + 3]["match"] == [{"protocol": "http", "not": [{"path": ["/healthz"]}], "vars_regexp": {
            "{env.SITE_ADDRESS}": {"name": "plain_beside_https", "pattern": "^[^:]+$"}}}]
        # every public route serves /srv/public, and only it
        for r in steps:
            paths = [p for m in r.get("match", []) for p in m.get("path", [])]
            if set(paths) & PUBLIC and not set(paths) & {"/healthz"}:
                roots = [h.get("root") for h in objects(r) if h.get("handler") == "vars" and "root" in h]
                assert roots == ["/srv/public"], paths
    for server in servers.values():
        errors = (server.get("errors") or {}).get("routes") or []
        csp = [o["set"]["Content-Security-Policy"] for o in objects(errors) if "Content-Security-Policy" in o.get("set", {})]
        assert csp == [[CSP]], "the server's own error answers carry the policy"
        assert any("Server" in o.get("delete", []) for o in objects(errors))
    file_servers = [o for o in objects(doc) if o.get("handler") == "file_server"]
    assert file_servers and not any("browse" in f or "precompressed" in f for f in file_servers)
    handlers = [o for o in objects(doc) if o.get("handler") == "reverse_proxy"]
    auth = [h for h in handlers if (h.get("rewrite") or {}).get("uri") == "/auth/check"]
    assert len(auth) == len(site_routes(doc)), "one forward_auth in the site"
    assert len(handlers) == 3 * len(site_routes(doc))
    for h in handlers:
        set_ = {k.lower(): v for k, v in (((h.get("headers") or {}).get("request") or {}).get("set") or {}).items()}
        for name, placeholder in STORE_HEADERS_JSON.items():
            assert set_.get(name) == [placeholder], (name, set_.get(name))
        assert h["transport"].get("keep_alive", {}).get("enabled") is False, "a connection of its own per request"
    for h in auth:
        ok = [r for r in h["handle_response"] if r.get("match", {}).get("status_code") == [2]]
        assert ok and not [o for o in objects(ok) if o.get("handler") == "headers"], \
            "a session lets the request through and copies no header from the store's answer onto it"
    csp = {o["Content-Security-Policy"][0] for o in objects(doc) if "Content-Security-Policy" in o}
    assert csp == {CSP}

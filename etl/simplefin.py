# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""SimpleFIN Bridge client: claim a setup token, fetch account snapshots, list
accounts.

This is the project's ONLY networked component. It is never imported by the
deterministic, offline pipeline (`etl.pipeline`) — it only writes durable raw
snapshots that the pipeline later reads. Run via `python -m etl simplefin ...`.

Design constraints (Bridge):
  * <=24 requests/day; data refreshes ~once/24h; 90-day max window per request;
    `start-date=0` burns the request budget. So: ONE all-accounts request per run,
    an explicit non-zero start, capped at 90 days. A stalled GET is retried up to
    twice (`_RETRY_WAITS`), so a run costs 1 request normally and at most 3 in the
    worst case — 9/day across the three scheduled refreshes, still well under 24.
    Permanent failures (4xx) are never retried, so a revoked credential costs one.
  * Every request asks for protocol version 2 (`errlist` errors, `connections`).
    The first fetch, with no snapshot stored, asks for the full 90 days; later ones
    for 35. Pending rows are never asked for: the feed's reader skips them.
  * The Access URL embeds HTTP Basic credentials — a bearer secret. Stored chmod
    600; we refuse to read it if group/other can. No message prints it, nor its
    user or password.
  * Only https is reached, its certificate verified. The one exception is the test
    seam, TEST_ORIGIN_VARIABLE: set to exactly http://127.0.0.1:<port>, it lets
    that one loopback origin be reached over plain HTTP (the tests' fake bridge);
    any other value opens nothing.
  * Every request goes through the module's opener (_opener), which follows no
    redirect: a 30x answer fails the request in one line, so the credential is
    never sent to an address _allowed has not judged. An https request goes
    through the proxy the environment names (https_proxy and the rest), as
    urllib's own default does, so a household behind a proxy reaches the bridge;
    the plain-HTTP seam's request goes through none, so no proxy variable can
    route it, or its credential, through another host.
  * No message prints a setup token, which may be unspent: a claim URL refused
    before the claim is named by its host alone, a failed claim by its path
    without the token's segment.
  * Snapshots are append-only and immutable (one fetch = one file); the union of
    all snapshots over time IS the balance/holdings history. An answer holding no
    account is not stored, and one not laid out as the protocol's is refused; each
    file is written whole before it takes its name.
"""
from __future__ import annotations

import base64
import datetime as dt
import functools
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ._net import TIMEOUT, USER_AGENT, now_utc, ssl_context

_MAX_WINDOW_DAYS = 90  # Bridge hard limit per /accounts request; the first fetch asks for all of it
_DEFAULT_DAYS = 35  # the look-back of a fetch once the store holds a snapshot
_RETRY_WAITS = (5, 15)  # backoff before each retry of an idempotent GET (3 attempts)
_VERSION = "2"  # the protocol version every /accounts request asks for
# the Bridge sits behind Cloudflare, which bans the default "Python-urllib" UA
# (error 1010); present a normal browser UA so requests are accepted

# The test seam: the one variable that lets a plain-HTTP origin be reached, read in _allowed alone. Only an exact
# http://127.0.0.1:<port> opens anything, and only that origin; production never sets it.
TEST_ORIGIN_VARIABLE = "FF_SIMPLEFIN_TEST_ORIGIN"
_LOOPBACK_ORIGIN = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})")


def _allowed(url: str) -> bool:
    """Whether this module may request `url`: an https URL with a host, or a plain http one whose host and port are
    those of the loopback origin TEST_ORIGIN_VARIABLE states exactly (another host, another port, localhost by name
    or any address but 127.0.0.1 is refused, and so is every http URL while the variable states nothing such)"""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme == "https":
        return bool(parts.hostname)
    origin = _LOOPBACK_ORIGIN.fullmatch(os.environ.get(TEST_ORIGIN_VARIABLE, ""))
    return (origin is not None and int(origin.group(1)) <= 65535 and parts.scheme == "http"
            and parts.hostname == "127.0.0.1" and port == int(origin.group(1)))


def _host(url: str) -> str:
    """How a refused URL is named: its host alone, never its user, password or path (a claim URL's path holds the
    setup token)"""
    try:
        return urllib.parse.urlsplit(url).hostname or "an address with no host"
    except ValueError:
        return "an address that cannot be read"


def _refusal(url: str) -> str:
    """The one line refusing a URL _allowed does not allow, naming its host alone"""
    return f"SimpleFIN is reached over https only: refusing {_host(url)}"


def _redacted(text: str) -> str:
    """`text` with the user and password of any URL in it taken out"""
    return re.sub(r"//[^/\s]*@", "//", text)


# --------------------------------------------------------------- credential I/O

def write_access_url(cred_path: Path, url: str) -> None:
    """Write the Access URL with 0600 from creation (never briefly world-readable)."""
    cred_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(cred_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, url.strip().encode())
    finally:
        os.close(fd)
    os.chmod(cred_path, 0o600)


def read_access_url(cred_path: Path) -> str:
    if not cred_path.exists():
        raise SystemExit(
            f"No SimpleFIN credential at {cred_path}.\n"
            f"Run: python -m etl simplefin claim --token <SETUP_TOKEN>")
    mode = cred_path.stat().st_mode
    if mode & 0o077:
        raise SystemExit(
            f"Refusing to read {cred_path}: it is group/other-accessible "
            f"(mode {oct(mode & 0o777)}). Run: chmod 600 {cred_path}")
    try:
        return cred_path.read_text().strip()
    except (OSError, UnicodeDecodeError) as e:
        raise SystemExit(
            f"Cannot read {cred_path} ({getattr(e, 'strerror', None) or type(e).__name__}): "
            f"make it a file this user can read, mode 600") from None


# ------------------------------------------------------------------------ HTTP

def _basic_auth(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect: the 30x answer is raised as the HTTPError it is, so no request (and no Authorization
    header) is ever sent to the address it names"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def _opener(seam: bool = False) -> urllib.request.OpenerDirector:
    """The opener a request goes through: TLS verified and no redirect followed. An https request's (`seam` false)
    keeps the default proxy handler, which reads https_proxy and the rest from the environment when it is built; the
    plain-HTTP seam's (`seam` true) has an empty one in its place, so it uses no proxy. Each is built once (_built)."""
    return _built(bool(seam))


@functools.cache
def _built(seam: bool) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = [_NoRedirect(), urllib.request.HTTPSHandler(context=ssl_context())]
    if seam:
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def _transient(exc: Exception) -> bool:
    """Is this worth another attempt? HTTPError is a URLError subclass, so it is
    tested first: a 4xx (bad/revoked credential) is permanent and retrying it would
    spend request budget to fail three times instead of once. A 429 is the bridge
    saying the budget is spent: asking again within seconds spends more of it, so
    it is left to the next scheduled refresh."""
    if isinstance(exc, urllib.error.HTTPError):
        return 500 <= exc.code < 600
    return isinstance(exc, (TimeoutError, urllib.error.URLError, http.client.HTTPException))


def _fatal(exc: Exception, method: str, path: str, attempts: int) -> SystemExit:
    """Render a transport failure as the module's one-line SystemExit. A read
    timeout arrives as a bare TimeoutError — urllib only wraps *connect* errors in
    URLError — so it needs its own arm or it escapes as a raw traceback."""
    if isinstance(exc, urllib.error.HTTPError) and 300 <= exc.code < 400:
        return SystemExit(f"SimpleFIN answered {method} {path} with a redirect (HTTP {exc.code}), which is not followed")
    if isinstance(exc, urllib.error.HTTPError):
        body = exc.read().decode(errors="replace")[:500]
        return SystemExit(f"SimpleFIN HTTP {exc.code} for {method} {path}: {body}")
    if isinstance(exc, TimeoutError):
        tries = "1 attempt" if attempts == 1 else f"{attempts} attempts"
        return SystemExit(
            f"SimpleFIN request timed out after {TIMEOUT}s for {method} {path} ({tries})")
    if isinstance(exc, urllib.error.URLError):
        return SystemExit(f"SimpleFIN request failed: {exc.reason}")
    return SystemExit(f"SimpleFIN request failed: {exc}")


def _request(url: str, method: str = "GET", data: bytes | None = None) -> bytes:
    """HTTP request; Basic-auth header derived from any userinfo in the URL.

    Idempotent GETs are retried through transient failures. The Bridge stalls past
    the timeout on roughly 1 run in 20 — it accepts the connection and then never
    answers — and with one attempt per run that blip costs a whole refresh cycle of
    stale balances. A POST is never retried: `claim` spends a single-use setup
    token, so a replay could burn it for nothing. A URL _allowed refuses is never
    requested, and a redirect is never followed (_opener).
    """
    if not _allowed(url):
        raise SystemExit(_refusal(url))
    parts = urllib.parse.urlsplit(url)
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if parts.username:
        headers["Authorization"] = _basic_auth(
            urllib.parse.unquote(parts.username),
            urllib.parse.unquote(parts.password or ""))
        netloc = parts.hostname or ""
        if parts.port:
            netloc += f":{parts.port}"
        url = urllib.parse.urlunsplit(
            (parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    # a POST is the claim, whose last path segment is the setup token, which may be unspent after a timeout: its
    # failure message names the path without it
    shown = (parts.path.rstrip("/").rpartition("/")[0] or "/") if method == "POST" else parts.path
    waits = _RETRY_WAITS if method == "GET" else ()
    for attempt in range(len(waits) + 1):
        try:
            with _opener(seam=parts.scheme == "http").open(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException) as e:
            if attempt == len(waits) or not _transient(e):
                raise _fatal(e, method, shown, attempt + 1) from e
            # NOT "WARN simplefin" — deploy/refresh.sh treats that prefix as a
            # connection needing re-auth and would alert on a retry we recovered from.
            print(f"  INFO simplefin: {method} {shown} failed ({type(e).__name__}); "
                  f"retrying in {waits[attempt]}s "
                  f"(attempt {attempt + 2} of {len(waits) + 1})", file=sys.stderr)
            time.sleep(waits[attempt])
    raise AssertionError("unreachable")  # pragma: no cover


# ----------------------------------------------------------------------- claim

def exchange(setup_token: str) -> str:
    """The Access URL a one-time base64 setup token is exchanged for: one POST, never retried (the token is single
    use). Anything that stops it is the module's one-line SystemExit, whose cause is the HTTP error when the bridge
    answered one; nothing is written and no message holds the token or the URL."""
    try:
        claim_url = base64.b64decode(setup_token.strip()).decode().strip()
    except Exception as e:
        raise SystemExit(f"Setup token is not valid base64: {e}") from e
    if not _allowed(claim_url):
        raise SystemExit(f"Decoded claim URL looks wrong: {_host(claim_url)}")
    access_url = _request(claim_url, method="POST", data=b"").decode().strip()
    if not _allowed(access_url):
        raise SystemExit(f"Claim did not return an access URL: {_redacted(access_url)[:120]!r}")
    return access_url


def claim(cfg, setup_token: str, force: bool = False) -> Path:
    """Exchange a one-time base64 setup token for the permanent Access URL."""
    cred = cfg.simplefin_credential_path
    if cred.exists() and not force:
        raise SystemExit(
            f"Credential already exists at {cred}. Pass --force to overwrite "
            f"(needs a fresh setup token — claim tokens are single-use).")
    access_url = exchange(setup_token)
    write_access_url(cred, access_url)
    host = urllib.parse.urlsplit(access_url).hostname
    print(f"Claimed. Access URL ({host}) stored at {cred} (mode 600).")
    return cred


# ----------------------------------------------------------------------- fetch

def fetch(cfg, days: int | None = None, end: dt.datetime | None = None) -> Path | None:
    """One all-accounts request over a rolling window; append a raw snapshot.

    `days` is the transaction look-back (balances + holdings are always current),
    capped at the Bridge's 90-day per-request limit. None (the command's default)
    asks for the full 90 days while the store holds no snapshot, so the first fetch
    reaches as far back as the Bridge allows, and for 35 after: the scheduled
    refresh's window. An explicit `days` always wins (the one-time backfill asks
    for 90 to fill the CSV->today gap). The request asks for protocol version 2
    and never for pending rows.

    An answer that is no JSON object holding a list of account objects is refused
    in one line and not stored. One holding no account (a day-one re-auth answers
    its errors alone) prints its errors and is not stored either: None is returned.
    The answer is written whole to a hidden file first and linked under its name
    once it is on disk (_store), so a full disk or a kill mid-write never leaves a
    half-written snapshot for every later ingest to read.
    """
    access_url = read_access_url(cfg.simplefin_credential_path)
    if days is None:
        days = _DEFAULT_DAYS if _any_holds_accounts(cfg.find_simplefin_snapshots()) else _MAX_WINDOW_DAYS
    days = max(1, min(days, _MAX_WINDOW_DAYS))
    end_dt = end or now_utc()
    start = int((end_dt - dt.timedelta(days=days)).timestamp())  # never 0
    end_epoch = int(end_dt.timestamp())
    q = urllib.parse.urlencode({"start-date": start, "end-date": end_epoch, "version": _VERSION})
    body = _request(f"{access_url.rstrip('/')}/accounts?{q}")
    try:
        doc = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise SystemExit(f"SimpleFIN returned non-JSON ({e}); not storing.") from e
    if not isinstance(doc, dict) or not _is_account_list(doc.get("accounts")):
        raise SystemExit("SimpleFIN answered without a list of accounts; not storing.")
    _warn_errors(doc)
    if not doc["accounts"]:
        print(f"Fetched 0 accounts ({days}d window): nothing stored")
        return None
    out = _store(cfg.simplefin_snapshots_dir, end_dt.strftime("%Y%m%dT%H%M%SZ"), body)
    print(f"Fetched {len(doc['accounts'])} accounts ({days}d window) -> {out.name}")
    return out


def _is_account_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(a, dict) for a in value)


def _any_holds_accounts(snapshots: list[Path]) -> bool:
    """Whether any stored snapshot holds an account, newest first (usually the first one read answers): one stored with
    none, or one that cannot be read, does not count, so the next fetch still asks for the whole 90 days"""
    for path in reversed(snapshots):
        try:
            doc = json.loads(path.read_bytes())
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("accounts"), list) and doc["accounts"]:
            return True
    return False


def _store(folder: Path, stamp: str, body: bytes) -> Path:
    """Write `body` as a new snapshot named for `stamp`: whole to a hidden temporary file in `folder` (flushed to disk),
    then hard-linked under the first free name (`accounts-<stamp>.json`, else `accounts-<stamp>-<n>.json`: snapshots are
    append-only, so a same-second file is never overwritten) and the temporary name removed. On a file system that
    holds no hard link the whole file is renamed onto the free name instead. A failure leaves no snapshot and no
    temporary file, and is the module's one-line SystemExit."""
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".accounts-{stamp}.{os.getpid()}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            view = memoryview(body)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        n = 0
        while True:
            out = folder / (f"accounts-{stamp}.json" if n == 0 else f"accounts-{stamp}-{n}.json")
            try:
                os.link(tmp, out)
                break
            except FileExistsError:
                n += 1
            except OSError:
                if out.exists():
                    n += 1
                    continue
                os.replace(tmp, out)
                break
    except OSError as e:
        raise SystemExit(f"SimpleFIN answer could not be stored ({e.strerror or e}); nothing was stored.") from e
    finally:
        tmp.unlink(missing_ok=True)
    return out


def _warn_errors(doc: dict) -> None:
    """One `WARN simplefin` line for each entry of the version-1 `errors` and the
    version-2 `errlist`, either list read in either shape: a string, or an object
    {code, msg, conn_id?, account_id?}. A disconnected institution or a needed
    re-auth shows up here. An object that names a connection is worded as version 1
    words it, `Connection to <name> may need attention.`, the name read from the
    answer's `connections` (else the connection's id): deploy/refresh.sh reads the
    names its re-auth alert gives from that wording. Each line's whitespace is
    squashed, so an entry is one line whatever its text holds."""
    names = {c.get("conn_id"): c.get("name") for c in _listed(doc.get("connections"))
             if isinstance(c, dict) and isinstance(c.get("conn_id"), str)}
    for e in [*_listed(doc.get("errors")), *_listed(doc.get("errlist"))]:
        if isinstance(e, str):
            line = e
        elif isinstance(e, dict):
            line = f"[{e.get('code') or '?'}] {e.get('msg') or ''} {e.get('account_id') or e.get('conn_id') or ''}"
            conn = e.get("conn_id")
            if isinstance(conn, str) and conn:
                line = f"Connection to {names.get(conn) or conn} may need attention. {line}"
        else:
            continue
        print(f"  WARN simplefin: {' '.join(line.split())}", file=sys.stderr)


def _listed(value) -> list:
    return value if isinstance(value, list) else []


# --------------------------------------------------------------- list-accounts

def list_accounts(cfg) -> None:
    """Print the newest readable snapshot's accounts to help fill simplefin_id/cutover
    (`./ff simplefin list-accounts`). The institution is a version-1 account's `org`,
    else the name of the connection a version-2 account's `conn_id` names."""
    found = None
    for snap in reversed(cfg.find_simplefin_snapshots()):
        try:
            doc = json.loads(snap.read_bytes())
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and _is_account_list(doc.get("accounts")):
            found = snap
            break
    if found is None:
        raise SystemExit("The bank feed has no snapshot yet: the next refresh fetches one (./ff refresh, while the "
                         "dashboard runs), and ./ff init --feed connects a bank feed when none is.")
    snap = found
    mapped = cfg.simplefin_map()  # sfid -> account_id
    connections = {c.get("conn_id"): c for c in doc.get("connections") or [] if isinstance(c, dict)}
    print(f"# SimpleFIN accounts in {snap.name}\n")
    hdr = (f"{'simplefin_id':<26}  {'org':<16}  {'name':<30}  "
           f"{'balance':>13}  mapped / suggested")
    print(hdr)
    print("-" * len(hdr))
    for a in doc.get("accounts", []):
        sfid = a.get("id", "")
        org = ((a.get("org") or {}).get("name") or (a.get("org") or {}).get("domain")
               or (connections.get(a.get("conn_id")) or {}).get("name") or "")
        name = a.get("name", "")
        bal = str(a.get("balance", ""))
        tag = (f"{mapped[sfid]} (mapped)" if sfid in mapped
               else f"unmapped -> suggest: {_suggest(cfg, org, name)}")
        print(f"{sfid:<26}  {org:<16.16}  {name:<30.30}  {bal:>13}  {tag}")
    print("\nAn unmapped account: ./ff init --feed asks which of your accounts it is and adds it to "
          "config/accounts.yaml; or put its id in that account's simplefin_id (with a simplefin_cutover when the "
          "account has statement files; an old id moves to simplefin_id_history), then ./ff validate and "
          "./ff refresh.")


def _suggest(cfg, org: str, name: str) -> str:
    """Best-effort registry-id guess by institution + last-3/4 digits."""
    m = re.search(r"(\d{3,4})\b", name)
    last = m.group(1) if m else None
    blob = f"{org} {name}".lower()
    best = "?"
    for account_id, meta in cfg.accounts.items():
        toks = [t for t in (meta.get("institution") or "").lower().split() if len(t) > 3]
        if any(t in blob for t in toks):
            best = account_id
            if last and last in account_id:
                return account_id
    return best

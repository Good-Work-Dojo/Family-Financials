# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Family store: GET/PUT for the dashboard's JSON documents, atomic writes.
Plus the sign-in (/auth/login, /auth/logout, /auth/check, /auth/status) and the
data-refresh control endpoint (GET/POST /api/refresh).

Four documents live in the durable state directory (--dir; see STORES):
  /api/scenarios     — second-house scenario list (verbatim last-write-wins)
  /api/answers       — review-inbox categorization answers (merged by key on
                       PUT, so two family members answering at once never erase
                       each other; the ETL reads the file read-only on ingest)
  /api/prefs         — in-app budgets/target mix/targets/projection/payroll
                       preferences, the modules switched on and the AI consent,
                       plus the saved views / reports lists (last-write-wins per
                       doc, except that a list section or a section kept whole
                       that a PUT omits is kept)
  /api/manual-assets — the assets entered in the app with their dated values
                       (merged by asset id on PUT, a removal kept as a
                       tombstone; the ETL reads the file read-only on ingest)

Reachable only through the compose network (Caddy passes /api/* and /auth/*
on; the container publishes no ports), and it trusts only the headers Caddy
sets (deploy/auth.py's docstring has them, and the sign-in's rules). The
sign-in: POST /auth/login checks a passcode against the web secrets' passcodes
file (scrypt hashes, read again when it changes) under the throttle and opens a
session (cookie ff_session; a first sign-in also gets the ff_device cookie);
POST /auth/logout ends it; GET /auth/check is what Caddy's forward_auth asks
(204 / 302 to /login / 401); GET /auth/status says whether any passcode is set
up; GET /healthz answers ok. Sessions and devices are kept in memory and saved
to the auth state directory (SignIn). Every /api/* route asks for a live session
as well (a 401 without one), unless the store was started with --auth off,
which is for tests and local development only. Every write — a document's PUT,
the refresh POST, sign-in and sign-out — must carry an Origin equal to the
request's own (a 403, nothing written, otherwise). A request for a host the
dashboard does not answer to is a 421 on every route but /healthz (the host
rule, auth.host_allowed; --hostnames lists the names beyond its own, and the
site's own name from --site-address joins them). Each connection carries one
request: every answer says Connection: close, and a GET or HEAD that says it
carries a body is a 400 with none of it read, so no body is ever read as a
request of its own. Sign-out also tells the browser to clear its cache. Removing
or changing a passcode ends the sessions it opened. Sign-in attempts without a
known device share a bounded section (SIGN_IN_SECTION; a full one is an
immediate 429), so a flood cannot grow the store's threads; and at most
REQUEST_SLOTS requests of any kind are handled at once past their headers (one
more is an immediate 503), the health and session checks aside, so requests
that hold their body back cannot take every thread either. It listens on
127.0.0.1 unless told otherwise, and its Server header names no Python version.
The request log names the method, the path and the status: never a query
string, a header or a body.
Stdlib only: do not add dependencies (this rides the ETL image without touching
pyproject/uv.lock).

Verbatim stores never stamp or compare timestamps; clients reconcile via the
document's own `updated_at`. The answers store merges by answer `key` (newer
`answered_at` wins, no stamp kept past the store's clock) and the manual-assets
store by asset `id` (newer `updated_at` wins), each echoing the merged doc; the prefs store is verbatim
except for one rule: a list section (`saved_filters`, `reports`) or a section
kept whole (`target_mix`, `modules`, `ai`) the incoming doc OMITS is kept from
the stored doc, because a tab on an older bundle does not know the section and
its PUT must not erase it (a section the PUT carries replaces the stored one:
`[]` clears a list, `null` the target mix, `{}` the modules).

/api/refresh is not a document. POST rate-limits (deploy/refresh_state.py) and
then drops a request file in the shared refresh directory; the `refresher`
container picks it up and runs deploy/refresh.sh. That indirection is the
point: this process terminates user HTTP, so it must never be the one holding
the SimpleFIN credential or the ETL write mounts. GET reports the run's state
alongside the rate-limit budget, so the button can render itself truthfully,
plus what the last refresh's fetch learned of the bank feed (its errors, the
connections to sign in to again, or that there was no feed and it read the
files alone), the refresh schedule and the state of an ingest without a fetch.
The schedule, and the household's zone every daily limit counts its days in,
come from the mailbox's schedule.json (refresh_state.read_schedule), which the
scheduler writes from household.yaml as it starts: this process cannot read
the config. Until there is one the days are UTC's and the view reports no
schedule. That ingest is asked for by touching the ingest marker in the same dir
(refresh_state.INGEST_MARKER): POST {"mode": "ingest"} does, and so does a
manual-assets save that changed the stored document; it spends none of the
refresh budget, and the refresher runs it. POST {"mode": "ai"} (the household
switched the AI step on) and {"mode": "ai_reset"} (it asked for the AI step's
answers to be forgotten) touch an AI marker as well, which the refresher's
ingest-only run answers by doing that first. This process only touches the
markers and reads status.json, never writes it.

Where its directories are: the argument, else the location's own variable, else
its place under FF_HOME, else its default place (refresh_state.locate, the one
table of the deploy side; the ETL resolves the same variables):

  argument            variable               under FF_HOME    neither
  --dir               FF_STATE_FAMILY_DIR    state/family/    /app/scenarios
                      (then FF_STATE_DIR: the state in one folder)
  --refresh-dir       FF_STATE_REFRESH_DIR   state/refresh/   none: /api/refresh answers 503
  --secrets-web-dir   FF_SECRETS_WEB_DIR     secrets/web/     /app/secrets/web
  --state-auth-dir    FF_STATE_AUTH_DIR      state/auth/      /app/state/auth
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import math
import os
import re
import secrets
import signal
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, urlsplit

import auth  # deploy/ is sys.path[0] when run by path
import refresh_state as rs
import safe_files

API_PATH = "/api/scenarios"
# The dashboard's "Refresh now" button. Deliberately NOT a STORES document: it
# is a rate-limited command, not a last-write-wins doc, and the process that
# actually runs the ETL (the refresher) reads the request file from a shared
# dir rather than being reachable over HTTP at all.
REFRESH_PATH = "/api/refresh"
MAX_REFRESH_BYTES = 1024
# POST /api/refresh with {"mode": "ingest"}: an ingest without a fetch; {"mode": "ai"}: the AI step, then an ingest (the
# household switched it on); {"mode": "ai_reset"}: the AI step's stored answers forgotten, then an ingest. A body with
# no mode asks for a refresh.
INGEST_MODE = "ingest"
AI_MODE = "ai"
AI_RESET_MODE = "ai_reset"
MODES = (INGEST_MODE, AI_MODE, AI_RESET_MODE)
# each AI mode's marker (refresh_state.AI_MARKERS)
AI_MODE_MARKERS = {AI_MODE: "run", AI_RESET_MODE: "reset"}
MAX_BYTES = 256 * 1024
# The answers doc's own body cap, and the only bound on vendor-scope answers (cap_answers never drops one): about
# 4,800 of them fit beside a full MAX_ANSWERS one-off answers, so one per vendor stays far inside it. Nothing in
# front of the store caps a body lower (deploy/Caddyfile's request_body limit, 2 MB, is above it).
MAX_ANSWERS_BYTES = 1024 * 1024
# A document PUT over its store's cap but under this is read (and discarded) before the 413 goes out: Caddy streams
# the body to us, and a reply to a half-read request reaches the browser as a 502, which the client retries forever
# as "unreachable" instead of reporting the refusal. Past this the 413 goes out unread (the connection ends with it,
# as it does after every answer).
MAX_DRAIN_BYTES = 4 * 1024 * 1024
MAX_PREFS_BYTES = 64 * 1024
# prefs list sections (saved views, saved reports): the entries are client-owned, but each one is
# bounded so ~50 of them cannot fill MAX_PREFS_BYTES on their own. The dashboard's own cap is 2 KB per
# entry (app/src/lib/prefs.ts MAX_SAVED_ENTRY_CHARS); this one is twice that, so it only ever refuses
# what no client of the current bundle writes (non-ASCII names, a later bundle's extra fields).
PREFS_LIST_SECTIONS = ("saved_filters", "reports")
MAX_SAVED_ENTRY_BYTES = 4096
# the family's target mix (prefs.target_mix): an object of percentages (app/src/lib/targetMix.ts: stocks, bonds, cash,
# alts, totalling 100 - the client checks the total) or null. Bounded here so the section stays a handful of numbers.
MAX_TARGET_MIX_KEYS = 8
MAX_TARGET_MIX_KEY_LEN = 32
# prefs sections a PUT that omits them keeps whole from the stored doc (each an object when kept): the target mix, the
# modules switched on (prefs.modules: module id -> on) and the AI consent (prefs.ai). A tab on a bundle that predates a
# section never sends it, so its PUT must not erase what a newer tab saved.
PREFS_KEPT_WHOLE = ("target_mix", "modules", "ai")
MAX_MODULES = 16
MAX_MODULE_ID_LEN = 40
AI_KEYS = frozenset({"enabled", "consent_policy", "consent_scope", "consented_at"})
# the longest consent scope kept (who receives the vendor names and which accounts: etl/ai/policy.py SCOPE_MAX)
AI_SCOPE_MAX = 200
# The assets entered in the app (/api/manual-assets; etl/manual_assets.py reads the file on ingest and checks each
# asset against the household: its owner, class and name). The store checks the shape only. Live assets and values per
# asset are refused past their cap, never evicted (an asset dropped to make room would vanish from net worth); a
# removal is a tombstone (the entry with deleted_at), kept so a stale tab cannot bring the asset back, at most
# MAX_MANUAL_TOMBSTONES of them and none removed more than TOMBSTONE_KEEP_DAYS before a merge.
# The body cap holds a document at every count's cap with realistic field lengths (about 340 KB: 50 assets of 120
# values with 80-character names and 12-digit values, beside 200 removals) and stays under the engine's loader limit
# (etl/manual_assets.py MAX_BYTES, 1 MiB), so whatever the store writes is read.
MAX_MANUAL_BYTES = 512 * 1024
MAX_MANUAL_ASSETS = 50
MAX_MANUAL_VALUES = 120
MAX_MANUAL_TOMBSTONES = 200
TOMBSTONE_KEEP_DAYS = 90
# how far ahead of the store's clock an asset's stamp may be: a device clock merely off passes, one far ahead is refused
MAX_CLOCK_AHEAD_S = 24 * 3600
# what a removal is stored with: enough to beat an older copy of the asset and to expire, and no values to keep
TOMBSTONE_KEYS = ("id", "name", "updated_at", "deleted_at")
MAX_MANUAL_NAME_LEN = 80          # the string as sent, padding included
MAX_MANUAL_LABEL_LEN = 80         # an owner or an asset class: which ones the household has is the engine's check
# no name, owner or asset class holds one of these (has_control): a line break or an escape sequence would reach every
# page and the database as it is
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MAX_MANUAL_ID_LEN = 64
MAX_MANUAL_VALUE = 1e12           # dollars either way (the cents of a larger one overflow the database's integers)
# containers deep, the document itself the first: the known shape is five; a key the store does not know passes
# verbatim, but nested no deeper than the engine's loader is sure to read back (it reads a document nested past its
# parser as not JSON at all)
MAX_MANUAL_DEPTH = 32
# An asset's id: runs of letters or digits, of any script, joined by single dashes, lowercase, at most
# MAX_MANUAL_ID_LEN characters. Every config entry's id (etl/networth.py manual_slug of its name) is one, so an app
# asset can carry it to override that entry; etl/manual_assets.py is_id is the same rule.
MANUAL_ID = re.compile(r"[^\W_]+(?:-[^\W_]+)*")
# a day as etl/validate.py iso_date reads one, so the store refuses no day the engine reads
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
# An asset's updated_at and deleted_at: the UTC stamp the dashboard writes (Date.toISOString), always with
# milliseconds. One fixed width makes string order time order; a mix of forms would not ("...T10:00:00.001Z" sorts
# below "...T10:00:00Z").
_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z")
_STAMP_RULE = "a UTC timestamp written YYYY-MM-DDTHH:MM:SS.sssZ"
# the sign-in's routes, and the page a signed-out browser is sent to (Caddy serves it)
AUTH_LOGIN = "/auth/login"
AUTH_LOGOUT = "/auth/logout"
AUTH_CHECK = "/auth/check"
AUTH_STATUS = "/auth/status"
HEALTHZ = "/healthz"
LOGIN_PAGE = "/login"
FORM_TYPE = "application/x-www-form-urlencoded"
JSON_TYPE = "application/json"
# One message for every failed sign-in, whatever the reason (a wrong passcode, none set up, a body that holds none),
# and one for every refused one; a form post gets the sign-in page back with e=1 or e=2 instead.
SIGN_IN_FAILED = "That passcode did not work."
SIGN_IN_WAIT = "Too many tries: wait a little, then try again."
SIGNED_OUT = "Signed out: sign in again."
CROSS_SITE = "This request came from another site, so nothing was changed.\n"
UNKNOWN_HOST = "This dashboard does not answer to that address.\n"
READ_WITH_BODY = "A GET or HEAD request carries no body.\n"
# how often main()'s timer saves a changed session table (sign-in and sign-out save at once)
SAVE_EVERY_S = 15 * 60
# passcode hashes run at once, for attempts with a known device and for the others apart: scrypt takes 32 MiB each,
# so a burst of attempts cannot take the memory, and a flood without a device never holds a household browser's slot
HASH_SLOTS = 2
# Sign-in attempts without a known device that may be inside the wait-and-hash section at once (each holds a thread
# for up to SLOW_WAIT_S and its place in the queue). One more is refused at once (429, Retry-After BUSY_RETRY_S), not
# hashed and not counted, so a flood cannot grow the store's threads; a known device never enters the section.
SIGN_IN_SECTION = 32
BUSY_RETRY_S = 3
# connections the listening socket holds while every handler is busy (the system's default is 5)
LISTEN_QUEUE = 128
# Requests handled at once past their headers (the proxy in front buffers each body whole, so a slow client holds
# Caddy, not the store; this bounds the store's threads if anything reaches it otherwise). One more is answered 503
# with Retry-After BUSY_RETRY_REQUEST_S at once. The container may start 256 processes and threads.
REQUEST_SLOTS = 64
BUSY_RETRY_REQUEST_S = 1
BUSY = "The dashboard is busy: try again in a moment."
# the paths that take no request slot: they read no body and never wait, so a full house never blocks them
UNSLOTTED = frozenset({HEALTHZ, AUTH_CHECK})
# the Server header: a name, never the Python version
SERVER_NAME = "ff-store"
MAX_SCENARIOS = 5
# one-off (txn-scope) answers kept; vendor-scope answers are never dropped (cap_answers)
MAX_ANSWERS = 500
MAX_NOTE_LEN = 500
ANSWER_SET_KEYS = {"category", "subcategory", "tags", "needs_review"}
# a never-written store must lose last-write-wins against any client copy
# (app/src/lib/scenarioStore.ts EPOCH_ISO — keep in sync)
EPOCH_ISO = "1970-01-01T00:00:00Z"

_write_lock = threading.Lock()


def empty_store() -> dict:
    return {"version": 1, "updated_at": EPOCH_ISO, "scenarios": []}


def empty_answers() -> dict:
    return {"version": 1, "updated_at": EPOCH_ISO, "answers": []}


def empty_prefs() -> dict:
    return {"version": 1, "updated_at": EPOCH_ISO}


def empty_manual_assets() -> dict:
    return {"version": 1, "updated_at": EPOCH_ISO, "assets": []}


def validate_store(body: bytes) -> dict:
    """Parse + shape-check a scenarios PUT body; raises ValueError with a reason.
    Permissive about extra keys (forward compat) — the client owns the schema;
    size is enforced by the handler before the body is read."""
    doc = _parse_doc(body)
    scenarios = doc.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("scenarios must be a list")
    if len(scenarios) > MAX_SCENARIOS:
        raise ValueError(f"at most {MAX_SCENARIOS} scenarios")
    return doc


class _NotFinite(ValueError):
    """A number JSON cannot carry, met while parsing."""


def _refuse_constant(name: str):
    raise _NotFinite(name)


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):          # 1e400 parses to infinity
        raise _NotFinite(text)
    return value


def encodes_as_utf8(doc: object) -> bool:
    """Every string of `doc`, keys included, is text UTF-8 can carry: none holds a lone surrogate, which JSON can spell
    ("\\ud800", or those bytes raw) but no database or browser takes back. Walked without recursion, so a document
    nested as deep as the parser allows is walked too. etl/validate.py encodes_as_utf8 is the same rule."""
    stack = [doc]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            try:
                node.encode("utf-8")
            except UnicodeEncodeError:
                return False
        elif isinstance(node, dict):
            stack.extend(node.keys())
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return True


def _parse_doc(body: bytes) -> dict:
    """Every document's envelope: JSON (nested no deeper than the parser reads), with no number JSON cannot carry
    (NaN, Infinity, -Infinity or one too large for a float) and no text UTF-8 cannot (encodes_as_utf8) anywhere, keys
    and sections the store does not know included — a stored document must be one the browser and the engine can
    read back — then an object of version 1 with a string updated_at."""
    try:
        doc = json.loads(body, parse_constant=_refuse_constant, parse_float=_finite_float)
    except _NotFinite:
        raise ValueError("the document holds a number that is not finite (NaN or infinity)") from None
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as e:   # RecursionError: nested past the parser
        raise ValueError(f"body is not valid JSON: {e}") from e
    if not encodes_as_utf8(doc):
        raise ValueError("the document holds text that is not valid Unicode")
    if not isinstance(doc, dict):
        raise ValueError("document must be a JSON object")
    if doc.get("version") != 1:
        raise ValueError("version must be 1")
    if not isinstance(doc.get("updated_at"), str):
        raise ValueError("updated_at must be a string")
    return doc


def validate_answers(body: bytes) -> dict:
    """Review-inbox answers. Strict about the `set` whitelist — the UI must
    never be able to retype transactions (transfer/cc-payment reclassification
    stays a curated config/overrides.yaml act)."""
    doc = _parse_doc(body)
    answers = doc.get("answers")
    if not isinstance(answers, list):
        raise ValueError("answers must be a list")
    seen: set[str] = set()
    one_off = 0
    for a in answers:
        if not isinstance(a, dict):
            raise ValueError("each answer must be an object")
        key, scope = a.get("key"), a.get("scope")
        if not isinstance(key, str) or not key:
            raise ValueError("answer key must be a non-empty string")
        if key in seen:
            raise ValueError(f"duplicate answer key {key!r}")
        seen.add(key)
        if scope == "txn":
            target = a.get("txn_id")
            if not isinstance(target, str) or not target:
                raise ValueError(f"{key!r}: txn answers need txn_id")
            one_off += 1
        elif scope == "vendor":
            target = a.get("vendor")
            if not isinstance(target, str) or not target:
                raise ValueError(f"{key!r}: vendor answers need vendor")
        else:
            raise ValueError(f"{key!r}: scope must be 'txn' or 'vendor'")
        if key != f"{scope}:{target}":
            raise ValueError(f"{key!r}: key must equal '<scope>:<target>'")
        if not isinstance(a.get("answered_at"), str):
            raise ValueError(f"{key!r}: answered_at must be a string")
        s = a.get("set")
        if not isinstance(s, dict) or not s:
            raise ValueError(f"{key!r}: set must be a non-empty object")
        unknown = set(s) - ANSWER_SET_KEYS
        if unknown:
            raise ValueError(f"{key!r}: set keys not allowed: {sorted(unknown)}")
        if "category" in s and not isinstance(s["category"], str):
            raise ValueError(f"{key!r}: category must be a string")
        if "subcategory" in s and not (s["subcategory"] is None
                                       or isinstance(s["subcategory"], str)):
            raise ValueError(f"{key!r}: subcategory must be a string or null")
        if "tags" in s and not (isinstance(s["tags"], list)
                                and all(isinstance(t, str) for t in s["tags"])):
            raise ValueError(f"{key!r}: tags must be a list of strings")
        if "needs_review" in s and not isinstance(s["needs_review"], bool):
            raise ValueError(f"{key!r}: needs_review must be a boolean")
        note = a.get("note")
        if note is not None and (not isinstance(note, str) or len(note) > MAX_NOTE_LEN):
            raise ValueError(f"{key!r}: note must be a string ≤ {MAX_NOTE_LEN} chars")
    if one_off > MAX_ANSWERS:
        raise ValueError(f"at most {MAX_ANSWERS} one-off (txn) answers")
    return doc


def _is_one_off(a: dict) -> bool:
    """Everything the cap may drop: a txn-scope answer (any scope but vendor, so a hand-edited stored entry the
    validator would refuse stays bounded too)."""
    return a.get("scope") != "vendor"


def cap_answers(answers: list[dict], limit: int = MAX_ANSWERS) -> list[dict]:
    """`answers` (keys unique) in their own order, without the oldest one-off (txn-scope) answers past `limit` of
    them: oldest `answered_at` first, then by key, stamps and keys compared as strings. Vendor-scope answers are
    never dropped and do not count toward the cap — each is a standing rule for every row of its vendor (never
    learned for an excluded, split or explicitly categorized vendor, so a drop would silently undo it); the only
    bound on them is the body cap (MAX_ANSWERS_BYTES, a 413 the dashboard reports). app/src/lib/answers.ts
    capAnswers is the same rule; app/src/lib/answers.eviction.fixture.json pins the two together."""
    one_off = [a for a in answers if _is_one_off(a)]
    if len(one_off) <= limit:
        return answers
    oldest_first = sorted(one_off, key=lambda a: (str(a.get("answered_at", "")), a["key"]))
    drop = {a["key"] for a in oldest_first[:len(one_off) - limit]}
    return [a for a in answers if a["key"] not in drop]


def server_stamp(now: float) -> str:
    """The store's clock (epoch seconds) as the stamp the dashboard writes: UTC, with milliseconds."""
    return dt.datetime.fromtimestamp(now, dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _not_after(stamp: object, limit: str) -> str:
    """`stamp` as the merge orders it (a string), or `limit` when it would sort after it."""
    text = str(stamp)
    return limit if text > limit else text


def _answered_by(a: dict, limit: str) -> dict:
    """The answer `a` as it is, or a copy answered at `limit` when its answered_at would sort after it."""
    return {**a, "answered_at": limit} if _answered_at(a) > limit else a


def _answered_at(a: dict) -> str:
    """An answer's stamp as the merge orders it: its text (a missing one sorts first)."""
    return str(a.get("answered_at", ""))


def _incoming_wins(new: str, old: str, limit: str) -> bool:
    """Whether an incoming answer stamped `new` replaces the stored one stamped `old`, the store's clock being `limit`.
    The newer stamp wins and the incoming answer takes a tie, except that a stored stamp ahead of the clock loses to an
    incoming one that is not: a hand edit, or a device that ran fast before this store clamped its stamps, never holds
    its row against a real re-answer. Two stamps ahead of the clock (a store whose clock lags its clients') keep their
    own order; an incoming stamp ahead of a stored one that is not wins, as the newer."""
    return (old > limit >= new) or new >= old


def merge_answers(current: object, incoming: dict, now: float) -> dict:
    """Union by answer key, newer answered_at wins per key (ISO strings compare
    lexicographically; ties go to the incoming doc). Prevents two family
    members' concurrent whole-document PUTs from erasing each other's answers.
    Stored oldest first (answered_at, key); a merge past MAX_ANSWERS one-off answers drops what cap_answers
    drops (never a vendor-scope answer).

    Nothing is stored dated after the store's clock (`now`, epoch seconds): an answered_at and the document's
    updated_at that would sort after it (the merge's own string order) are written at it, the stored ones included, so
    a device whose clock runs ahead cannot win the merges of its rows until real time passes its stamps. Which answer
    a key keeps is decided first, by _incoming_wins. A stored document that is no object, or whose `answers` is no
    list (a hand edit), merges as an empty one, with one stderr line."""
    limit = server_stamp(now)
    if not isinstance(current, dict):
        print(f"scenario_store: answers.json holds no document ({type(current).__name__}): merged as an empty one",
              file=sys.stderr, flush=True)
        current = empty_answers()
    stored = current.get("answers", [])
    if not isinstance(stored, list):
        print(f"scenario_store: answers.json holds no answers list ({type(stored).__name__}): merged as none",
              file=sys.stderr, flush=True)
        stored = []
    by_key: dict[str, dict] = {}
    for a in stored:
        if isinstance(a, dict) and isinstance(a.get("key"), str):
            by_key[a["key"]] = a
    for a in incoming.get("answers", []):
        prev = by_key.get(a["key"])
        if prev is None or _incoming_wins(_answered_at(a), _answered_at(prev), limit):
            by_key[a["key"]] = a
    kept = [_answered_by(a, limit) for a in by_key.values()]
    merged = cap_answers(sorted(kept, key=lambda a: (_answered_at(a), a["key"])))
    return {"version": 1,
            "updated_at": max(_not_after(current.get("updated_at", EPOCH_ISO), limit),
                              _not_after(incoming.get("updated_at", EPOCH_ISO), limit)),
            "answers": merged}


def _check_target_mix(mix: object) -> None:
    """null, or an object of at most MAX_TARGET_MIX_KEYS short names, each a number from 0 to 100 and never a bool. The
    range check also refuses the bare NaN / Infinity tokens json.loads accepts (NaN fails every comparison)."""
    if mix is None:
        return
    if not isinstance(mix, dict):
        raise ValueError("target_mix must be an object or null")
    if len(mix) > MAX_TARGET_MIX_KEYS:
        raise ValueError(f"target_mix has more than {MAX_TARGET_MIX_KEYS} entries")
    for key, pct in mix.items():
        if len(key) > MAX_TARGET_MIX_KEY_LEN:
            raise ValueError(f"target_mix key over {MAX_TARGET_MIX_KEY_LEN} characters")
        if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 100:
            raise ValueError(f"target_mix[{key!r}] must be a number from 0 to 100")


def _check_modules(modules: object) -> None:
    """An object of at most MAX_MODULES module ids (each at most MAX_MODULE_ID_LEN characters), each true or false. The
    client knows which ids exist; an id it does not know is ignored there."""
    if not isinstance(modules, dict):
        raise ValueError("modules must be an object")
    if len(modules) > MAX_MODULES:
        raise ValueError(f"modules has more than {MAX_MODULES} entries")
    for key, on in modules.items():
        if len(key) > MAX_MODULE_ID_LEN:
            raise ValueError(f"modules key over {MAX_MODULE_ID_LEN} characters")
        if not isinstance(on, bool):
            raise ValueError(f"modules[{key!r}] must be true or false")


def _check_ai(ai: object) -> None:
    """The AI consent: an object with exactly AI_KEYS — enabled true or false, consent_policy the version of the policy
    consented to (a whole number, never a bool) or null, consent_scope the scope consented to (who receives the vendor
    names and which accounts: a string of at most AI_SCOPE_MAX characters) or null, consented_at a string or null."""
    if not isinstance(ai, dict) or set(ai) != AI_KEYS:
        raise ValueError("ai must be an object with exactly enabled, consent_policy, consent_scope and consented_at")
    scope = ai["consent_scope"]
    if scope is not None and (not isinstance(scope, str) or len(scope) > AI_SCOPE_MAX):
        raise ValueError(f"ai.consent_scope must be text of at most {AI_SCOPE_MAX} characters or null")
    if not isinstance(ai["enabled"], bool):
        raise ValueError("ai.enabled must be true or false")
    policy = ai["consent_policy"]
    if policy is not None and (isinstance(policy, bool) or not isinstance(policy, int)):
        raise ValueError("ai.consent_policy must be a whole number or null")
    if ai["consented_at"] is not None and not isinstance(ai["consented_at"], str):
        raise ValueError("ai.consented_at must be a string or null")


# the shape check of each section in PREFS_KEPT_WHOLE
_KEPT_WHOLE_CHECKS = {"target_mix": _check_target_mix, "modules": _check_modules, "ai": _check_ai}


def validate_prefs(body: bytes) -> dict:
    """Prefs are client-owned; check only the envelope, that the known sections are objects
    (budgets, targets, projections, payroll) or lists (saved_filters, reports), that the sections kept
    whole have their shape (the target mix null or a small object of percentages, the modules an object
    of switches, the AI consent its four keys), and that no list entry is over MAX_SAVED_ENTRY_BYTES.
    The entries themselves pass verbatim — the client validates them. Unknown sections pass too
    (a newer bundle's). The doc's own size is enforced by the handler."""
    doc = _parse_doc(body)
    for section in ("budgets", "targets", "projections", "payroll"):
        if section in doc and not isinstance(doc[section], dict):
            raise ValueError(f"{section} must be an object")
    for section in PREFS_KEPT_WHOLE:
        if section in doc:
            _KEPT_WHOLE_CHECKS[section](doc[section])
    for section in PREFS_LIST_SECTIONS:
        if section not in doc:
            continue
        if not isinstance(doc[section], list):
            raise ValueError(f"{section} must be a list")
        for i, entry in enumerate(doc[section]):
            size = len(json.dumps(entry, separators=(",", ":"),
                                  ensure_ascii=False).encode("utf-8"))
            if size > MAX_SAVED_ENTRY_BYTES:
                raise ValueError(f"{section}[{i}] is {size} bytes (over {MAX_SAVED_ENTRY_BYTES})")
    return doc


def _kept_whole(section: str, value: object) -> bool:
    """A stored value of a PREFS_KEPT_WHOLE section worth keeping: an object that passes the section's own check."""
    if not isinstance(value, dict):
        return False
    try:
        _KEPT_WHOLE_CHECKS[section](value)
    except ValueError:
        return False
    return True


def merge_prefs(current: dict, incoming: dict) -> dict:
    """The incoming doc wins whole (last-write-wins), except that a list section or a section kept whole
    (PREFS_KEPT_WHOLE) it omits is kept from the stored doc: an older bundle does not know saved_filters /
    reports / target_mix / modules / ai, and its PUT would otherwise delete the family's saved views or
    target mix, switch a module off or withdraw the AI consent; the current bundle omits a list or the
    target mix it did not change (app/src/data/usePrefsStore.ts PREFS_SYNC), so a stale tab cannot either.
    A stored value of the wrong shape is not carried. A section present in the incoming doc — including
    an explicit `[]`, `{}` or `null` — replaces the stored one."""
    merged = dict(incoming)
    if isinstance(current, dict):
        for section in PREFS_LIST_SECTIONS:
            if section not in incoming and isinstance(current.get(section), list):
                merged[section] = current[section]
        for section in PREFS_KEPT_WHOLE:
            if section not in incoming and _kept_whole(section, current.get(section)):
                merged[section] = current[section]
    return merged


def is_manual_id(value: object) -> bool:
    """A manual asset's id: MANUAL_ID, at most MAX_MANUAL_ID_LEN characters, and its own lowercase."""
    return (isinstance(value, str) and len(value) <= MAX_MANUAL_ID_LEN and value == value.lower()
            and MANUAL_ID.fullmatch(value) is not None)


def _is_day(value: object) -> bool:
    """A day written YYYY-MM-DD that names a real date."""
    if not isinstance(value, str) or not _ISO_DAY.fullmatch(value):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _stamp_time(value: object) -> float | None:
    """The epoch seconds of an asset's stamp (_STAMP, naming a real instant), else None."""
    if not isinstance(value, str) or not _STAMP.fullmatch(value):
        return None
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.UTC).timestamp()
    except ValueError:
        return None


def _is_amount(value: object) -> bool:
    """Dollars within MAX_MANUAL_VALUE either way: never a bool, and NaN and infinity fail the range."""
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and -MAX_MANUAL_VALUE <= value <= MAX_MANUAL_VALUE)


def _depth(doc: object) -> int:
    """How many containers deep `doc` nests (a scalar 0, an empty list 1), walked without recursion."""
    deepest, stack = 0, [(doc, 1)]
    while stack:
        node, level = stack.pop()
        if isinstance(node, dict | list):
            deepest = max(deepest, level)
            stack.extend((child, level + 1) for child in (node.values() if isinstance(node, dict) else node))
    return deepest


def _is_label(value: object) -> bool:
    return isinstance(value, str) and value.strip() != "" and len(value) <= MAX_MANUAL_LABEL_LEN


def has_control(value: str) -> bool:
    """A C0 control character (U+0000 to U+001F: a line break, a tab, an escape) or DEL. etl/manual_assets.py
    has_control is the same rule."""
    return _CONTROL.search(value) is not None


def _asset_problem(a: object, place: int) -> str | None:
    """Why the store refuses the document's asset at `place`, or None. Shape only: an object with an id (is_manual_id),
    a name of at most MAX_MANUAL_NAME_LEN characters as sent and not all spaces, an owner and an asset class
    (non-empty, at most MAX_MANUAL_LABEL_LEN characters), none of those three holding a control character
    (has_control), liquid true or false, acquired absent, null or a day, updated_at a stamp
    (_STAMP), deleted_at absent, null or a stamp, and values a list of at most MAX_MANUAL_VALUES {as_of: a day, value:
    _is_amount} — at least one for a live asset. A tombstone (deleted_at set) needs only its id, name and stamps (the
    form the merge stores, TOMBSTONE_KEYS): an owner, class, flag or values it carries are checked, and it may carry no
    values, or no list at all. A key the store does not know passes verbatim, as on an answer."""
    where = f"assets[{place}]"
    if not isinstance(a, dict):
        return f"{where} must be an object"
    if not is_manual_id(a.get("id")):
        return (f"{where}: id must be 1 to {MAX_MANUAL_ID_LEN} lowercase letters or digits, in runs joined by single "
                "dashes")
    who = repr(a["id"])
    name = a.get("name")
    if not isinstance(name, str) or name.strip() == "" or len(name) > MAX_MANUAL_NAME_LEN:
        return f"{who}: name must be 1 to {MAX_MANUAL_NAME_LEN} characters, not all spaces"
    if has_control(name):
        return f"{who}: name holds a control character"
    removal = a.get("deleted_at") is not None     # its owner, class and flag are needed only while it is live
    for key in ("owner", "asset_class"):
        if removal and key not in a:
            continue
        if not _is_label(a.get(key)):
            return f"{who}: {key} must be a non-empty string of at most {MAX_MANUAL_LABEL_LEN} characters"
        if has_control(a[key]):
            return f"{who}: {key} holds a control character"
    if not (removal and "liquid" not in a) and not isinstance(a.get("liquid"), bool):
        return f"{who}: liquid must be true or false"
    if a.get("acquired") is not None and not _is_day(a["acquired"]):
        return f"{who}: acquired must be null or a day written YYYY-MM-DD"
    if _stamp_time(a.get("updated_at")) is None:
        return f"{who}: updated_at must be {_STAMP_RULE}"
    deleted = a.get("deleted_at")
    if deleted is not None and _stamp_time(deleted) is None:
        return f"{who}: deleted_at must be null or {_STAMP_RULE}"
    if deleted is not None and "values" not in a:
        return None
    values = a.get("values")
    if not isinstance(values, list):
        return f"{who}: values must be a list"
    if not values and deleted is None:
        return f"{who}: a live asset needs at least one value"
    if len(values) > MAX_MANUAL_VALUES:
        return f"{who}: at most {MAX_MANUAL_VALUES} values per asset"
    for j, v in enumerate(values):
        at = f"{who}: values[{j}]"
        if not isinstance(v, dict):
            return f"{at} must be an object"
        if not _is_day(v.get("as_of")):
            return f"{at}.as_of must be a day written YYYY-MM-DD"
        if not _is_amount(v.get("value")):
            return f"{at}.value must be a number from -1e12 to 1e12"
    return None


def validate_manual_assets(body: bytes) -> dict:
    """The manual-assets document's shape (the engine checks the meaning against the household): the envelope with
    version exactly 1 (never a bool), a list of assets each passing _asset_problem with unique ids, at most
    MAX_MANUAL_ASSETS of them live (a tombstone does not count, and how many tombstones there are is the merge's to
    trim) and no nesting past MAX_MANUAL_DEPTH. (_parse_doc has already refused a number JSON cannot carry and text
    that is not valid Unicode.)"""
    doc = _parse_doc(body)
    if type(doc["version"]) is not int:        # _parse_doc lets a JSON true or 1.0 through as equal to 1
        raise ValueError("version must be 1")
    if doc["updated_at"] != EPOCH_ISO and _stamp_time(doc["updated_at"]) is None:
        # the merge keeps the newer of the two documents' stamps, so any other string could win it for ever
        raise ValueError(f"updated_at must be {_STAMP_RULE}, or the empty document's {EPOCH_ISO}")
    if _depth(doc) > MAX_MANUAL_DEPTH:
        raise ValueError(f"the document is nested more than {MAX_MANUAL_DEPTH} levels deep")
    assets = doc.get("assets")
    if not isinstance(assets, list):
        raise ValueError("assets must be a list")
    seen: set[str] = set()
    live = 0
    for place, a in enumerate(assets):
        problem = _asset_problem(a, place)
        if problem is not None:
            raise ValueError(problem)
        if a["id"] in seen:
            raise ValueError(f"duplicate asset id {a['id']!r}")
        seen.add(a["id"])
        live += a.get("deleted_at") is None
    if live > MAX_MANUAL_ASSETS:
        raise ValueError(f"at most {MAX_MANUAL_ASSETS} assets (one removed in the app does not count)")
    return doc


class MergeRefused(ValueError):
    """A merge's own refusal of what the PUT holds (the handler answers 400; nothing is written). Kept apart from every
    other ValueError, so a fault in the stored file is never reported to a client as its own mistake."""


def _keep_newer(by_id: dict[str, dict], a: dict) -> None:
    prev = by_id.get(a["id"])
    if prev is None or a["updated_at"] >= prev["updated_at"]:
        by_id[a["id"]] = a


def merge_manual_assets(current: dict, incoming: dict, now: float) -> dict:
    """Union by asset id; per id the entry with the newer updated_at wins whole, a tie going to the incoming doc (the
    stamps compare as strings, which their one width makes their time order), so two tabs saving at once never erase
    each other's assets. A removal is an entry like any other (a tombstone, with deleted_at), stored with
    TOMBSTONE_KEYS only: it beats an older copy of the asset from either side, and only then is it dropped when its
    updated_at (the stamp the merge goes by; deleted_at only marks it as a removal) is more than TOMBSTONE_KEEP_DAYS
    before `now` (epoch seconds); past MAX_MANUAL_TOMBSTONES the removals stamped longest ago go first. Stored sorted by id, the document's updated_at the newer of the two. A stored entry the store
    would refuse (a hand edit) is left out with a stderr line, and a stored doc that is not one merges as nothing.
    Raises MergeRefused (a 400; nothing is written and nothing is evicted) when more than MAX_MANUAL_ASSETS live assets
    would be left once merged, or when an incoming asset's updated_at or deleted_at, or the incoming document's
    updated_at, is more than MAX_CLOCK_AHEAD_S ahead of `now`: a device whose clock runs ahead would otherwise win
    every later merge, and its removals would never expire. A stamp the store already holds is no device's clock: an
    incoming asset whose updated_at and deleted_at both equal the stored entry's of its id, and a document updated_at
    equal to the stored document's, pass however far ahead they are (else one entry stamped ahead by a hand edit, or
    by a server clock that once ran ahead, would make every whole-document save from every tab a 400)."""
    kept: list[dict] = []
    stored = current.get("assets") if isinstance(current, dict) else None
    for place, a in enumerate(stored if isinstance(stored, list) else ()):
        problem = _asset_problem(a, place)
        if problem is None:
            kept.append(a)
        else:
            print(f"scenario_store: manual_assets.json holds an entry the store would refuse, left out: {problem}",
                  file=sys.stderr, flush=True)
    held = {a["id"]: (a.get("updated_at"), a.get("deleted_at")) for a in kept}
    limit = now + MAX_CLOCK_AHEAD_S
    for a in incoming["assets"]:
        if held.get(a["id"]) == (a.get("updated_at"), a.get("deleted_at")):
            continue                                  # the stored entry's own stamps, echoed back
        if any((t := _stamp_time(a.get(k))) is not None and t > limit for k in ("updated_at", "deleted_at")):
            raise MergeRefused(f"{a['id']!r}: an asset's timestamp is in the future: check this device's clock")
    stored_at = current.get("updated_at") if isinstance(current, dict) else None
    if (incoming.get("updated_at") != stored_at and (t := _stamp_time(incoming.get("updated_at"))) is not None
            and t > limit):
        raise MergeRefused("the document's updated_at is in the future: check this device's clock")
    by_id: dict[str, dict] = {}
    for a in kept:
        _keep_newer(by_id, a)
    for a in incoming["assets"]:
        _keep_newer(by_id, a)
    cutoff = now - TOMBSTONE_KEEP_DAYS * 86400
    live = [a for a in by_id.values() if a.get("deleted_at") is None]
    # expiry and the cap's order go by updated_at, the stamp merges are decided by: deleted_at only marks a removal
    removed = sorted(({k: a[k] for k in TOMBSTONE_KEYS} for a in by_id.values()
                      if a.get("deleted_at") is not None and _stamp_time(a["updated_at"]) >= cutoff),
                     key=lambda a: (a["updated_at"], a["id"]))[-MAX_MANUAL_TOMBSTONES:]
    if len(live) > MAX_MANUAL_ASSETS:          # what the document holds, as validation refuses it: a 400, not a 413
        raise MergeRefused(f"at most {MAX_MANUAL_ASSETS} assets: combined with what the store already holds the "
                         f"document would have {len(live)}, so nothing was written")
    stored_at = str(current.get("updated_at", EPOCH_ISO)) if isinstance(current, dict) else EPOCH_ISO
    return {"version": 1, "updated_at": max(stored_at, str(incoming.get("updated_at", EPOCH_ISO))),
            "assets": sorted(live + removed, key=lambda a: a["id"])}


class StoreSpec:
    """One persisted document: filename inside --dir, validator, size cap,
    empty-doc factory, and an optional read-merge-write PUT semantics;
    `merge_takes_now` hands the merge the request's time as well (epoch
    seconds), for a document whose entries expire; `wants_ingest` marks a
    document the ingest reads to build its exports, so a save that changed it
    asks for an ingest (merge_and_write)."""

    def __init__(self, filename: str, validate, max_bytes: int, empty,
                 merge=None, *, merge_takes_now: bool = False, wants_ingest: bool = False):
        self.filename = filename
        self.validate = validate
        self.max_bytes = max_bytes
        self.empty = empty
        self.merge = merge
        self.merge_takes_now = merge_takes_now
        self.wants_ingest = wants_ingest


STORES: dict[str, StoreSpec] = {
    "/api/scenarios": StoreSpec("scenarios.json", validate_store, MAX_BYTES, empty_store),
    "/api/answers": StoreSpec("answers.json", validate_answers, MAX_ANSWERS_BYTES, empty_answers,
                              merge=merge_answers, merge_takes_now=True),
    "/api/prefs": StoreSpec("prefs.json", validate_prefs, MAX_PREFS_BYTES, empty_prefs,
                            merge=merge_prefs),
    # a dash and no .json suffix: deploy/Caddyfile's *.json matcher would take an /api/....json path. Net worth reads
    # the document only at an ingest, so a changed one asks for an ingest (the answers wait for the next refresh's,
    # as they always have, and the prefs need none).
    "/api/manual-assets": StoreSpec("manual_assets.json", validate_manual_assets, MAX_MANUAL_BYTES,
                                    empty_manual_assets, merge_manual_assets, merge_takes_now=True,
                                    wants_ingest=True),
}


def read_store(path: Path, empty=empty_store) -> dict:
    """Missing file -> empty doc. Corrupt file -> loud stderr + empty doc
    (self-heal; atomic writes make this near-impossible; should it happen,
    restore the file from your own backup). Corrupt is
    anything the parser refuses: not UTF-8, not JSON, nested past the parser, or
    any other ValueError while reading it (a hand-edited integer past the
    interpreter's digit limit), so GET serves the empty doc and a PUT merges
    with nothing instead of failing on the stored file. Read only as the
    regular file at the name (safe_files: a link, a FIFO or a folder there,
    which a worker sharing the folder could have put, is never followed or
    waited on) and served empty the same way."""
    try:
        return json.loads(safe_files.read_regular(path).decode("utf-8"))
    except FileNotFoundError:
        return empty()
    except (ValueError, RecursionError, OSError) as e:   # ValueError covers JSON and UTF-8 decode errors
        print(f"scenario_store: corrupt/unreadable {path}: {e} — serving empty doc",
              file=sys.stderr, flush=True)
        return empty()


def _say(message: str) -> None:
    print(f"scenario_store: {message}", file=sys.stderr, flush=True)


def _read_text(path: Path) -> str | None:
    """A saved table's text: None when there is no file, "" (which loads as a damaged table) when it cannot be read."""
    try:
        return path.read_text("utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return ""


def submitted_passcode(body: bytes, content_type: str) -> str:
    """The passcode a sign-in body carries, as typed: the `passcode` field of a form (FORM_TYPE) or of a JSON object
    (JSON_TYPE). "" when it carries none — a body that is not UTF-8, not of its type, or whose passcode is not text —
    which fails as a wrong passcode does."""
    try:
        text = body.decode("utf-8")
        if content_type == JSON_TYPE:
            doc = json.loads(text)
            given = doc.get("passcode") if isinstance(doc, dict) else None
        else:
            given = parse_qs(text, keep_blank_values=True, errors="strict", max_num_fields=8).get("passcode", [""])[0]
    except (ValueError, RecursionError):   # UnicodeDecodeError and JSONDecodeError are ValueErrors
        return ""
    return given if isinstance(given, str) else ""


class Attempt(NamedTuple):
    """A sign-in attempt's outcome: the label of the passcode it matched (None: it failed) and that line's fingerprint,
    or the throttle's refusal, in which case it was not checked; `busy` when the refusal is the full sign-in section's.
    (No dataclass here: this file is also loaded by path without a module entry, where a dataclass cannot be built.)"""
    label: str | None = None
    refused: auth.Wait | auth.Locked | None = None
    passcode: str | None = None
    busy: bool = False


def private_dir(folder: Path, warn) -> None:
    """Make `folder` and any missing parent 0700, whatever the umask, and tighten `folder` to 0700 when it is open to
    others, said in one line through `warn`."""
    missing = []
    probe = folder
    while not probe.exists():
        missing.append(probe)
        probe = probe.parent
    for made in reversed(missing):
        with contextlib.suppress(FileExistsError):
            os.mkdir(made, 0o700)
    mode = stat.S_IMODE(folder.stat().st_mode)
    if mode & 0o077:
        folder.chmod(0o700)
        warn(f"the sign-in folder {folder} was open to others ({mode:04o}): it is now 0700")


class SignIn:
    """The store's sign-in state, by deploy/auth.py's rules: the passcodes file in `secrets_dir` (read again when it
    changes; a session lives only while the line that opened it does), the sessions and known devices (loaded from
    `auth_dir` at start, kept in memory, saved to sessions.json and devices.json there — 0600, the directory and any
    parent it makes 0700, a wider directory tightened — at every sign-in, at a sign-out that ended a session, at
    shutdown, and by main()'s timer when changed, at most every SAVE_EVERY_S), and the throttle (memory only). A save
    that fails is a stderr line, never a failed sign-in: the sessions then last until the store restarts. `hasher`,
    `token`, `sleep` and `section` (the sign-in section's size) are seams for the tests. Thread-safe."""

    def __init__(self, secrets_dir: Path, auth_dir: Path, *, now: float, hasher=auth.scrypt,
                 token=secrets.token_urlsafe, sleep=time.sleep, warn=_say, section: int = SIGN_IN_SECTION) -> None:
        self.secrets_dir, self.auth_dir = Path(secrets_dir), Path(auth_dir)
        self.passcodes = auth.PasscodeFile(self.secrets_dir / auth.PASSCODES_FILE, warn=warn)
        self.sessions = auth.Sessions.load(_read_text(self.auth_dir / auth.SESSIONS_FILE), now, token=token, warn=warn)
        self.devices = auth.Devices.load(_read_text(self.auth_dir / auth.DEVICES_FILE), token=token, warn=warn)
        self.throttle = auth.Throttle()
        self.hasher, self.sleep, self._warn = hasher, sleep, warn
        self.saved_at = now
        self._lock = threading.Lock()                       # the sessions, the devices and their files
        self._slow = threading.Lock()                       # slow attempts are hashed one at a time
        # the attempts without a known device that may wait and hash at once (SIGN_IN_SECTION; never blocked on)
        self.section = threading.BoundedSemaphore(section)
        # the hashing slots (HASH_SLOTS each) for attempts with a known device and for the others
        self.device_slots = threading.BoundedSemaphore(HASH_SLOTS)
        self.other_slots = threading.BoundedSemaphore(HASH_SLOTS)

    def configured(self) -> bool:
        """Whether any passcode is set up."""
        return bool(self.passcodes.lines())

    def live_passcodes(self) -> set[str]:
        """The fingerprints of the passcodes file's current lines (read again only when the file changed)."""
        return {auth.passcode_fingerprint(line) for line in self.passcodes.lines()}

    def session(self, now: float, tokens: list[str]) -> str | None:
        """The label of the live session one of `tokens` opens (its last_seen moved to `now`), else None. A session
        whose passcode line is no longer in the file has ended."""
        live = self.live_passcodes()
        with self._lock:
            for token in tokens[:4]:
                label = self.sessions.check(now, token, live)
                if label is not None:
                    return label
        return None

    def device(self, tokens: list[str]) -> str | None:
        """The key of the known device one of `tokens` names, else None."""
        with self._lock:
            for token in tokens[:4]:
                key = self.devices.known(token)
                if key is not None:
                    return key
        return None

    def attempt(self, passcode: str, address: str, device: str | None, clock) -> Attempt:
        """Check `passcode` for a client at `address` (an address key) carrying the known device `device` or none,
        under the throttle. A refused attempt is not hashed. An attempt without a known device then enters the sign-in
        section (SIGN_IN_SECTION at once, never waited for): when it is full the attempt is refused as busy, unhashed
        and uncounted; a known device's attempt never enters it, so the household gets in during a flood. An allowed
        attempt takes a hashing slot and is decided again inside it, so attempts queued behind a failure see that
        failure (at most HASH_SLOTS of one address's attempts can pass the limit together); a slow one (no known
        device, in a flood) first waits SLOW_WAIT_S and is then hashed one at a time. The outcome is recorded before
        the slot is let go."""
        decision = self.throttle.check(clock(), address, device)
        if not isinstance(decision, auth.Allow):
            return Attempt(refused=decision)
        if device is not None:
            return self._checked(passcode, address, device, decision, clock)
        if not self.section.acquire(blocking=False):
            return Attempt(refused=auth.Wait(BUSY_RETRY_S), busy=True)
        try:
            return self._checked(passcode, address, None, decision, clock)
        finally:
            self.section.release()

    def _checked(self, passcode: str, address: str, device: str | None, decision, clock) -> Attempt:
        waited = False
        while isinstance(decision, auth.Allow):
            if device is None and decision.slow and not waited:
                self.sleep(auth.SLOW_WAIT_S)
                waited = True
            slots = self.device_slots if device is not None else self.other_slots
            with self._slow if waited else contextlib.nullcontext(), slots:
                decision = self.throttle.check(clock(), address, device)
                if isinstance(decision, auth.Allow) and (waited or not decision.slow):
                    line = auth.match(self.passcodes.lines(), passcode, hasher=self.hasher)
                    self.throttle.record(clock(), address, device, ok=line is not None)
                    if line is None:
                        return Attempt()
                    return Attempt(label=line.label, passcode=auth.passcode_fingerprint(line))
            # refused now, or the flood began while this attempt waited for its slot: decide again outside it
        return Attempt(refused=decision)

    def open_session(self, now: float, label: str, passcode: str, old_tokens: list[str],
                     device: str | None) -> tuple[str, str | None]:
        """A session for `label`, opened by the passcode line whose fingerprint is `passcode` (the sessions
        `old_tokens` open end: a browser holds one), and saved at once; returns its token and, when the browser
        carries no known device, a new device's token."""
        with self._lock:
            for token in old_tokens[:4]:
                self.sessions.drop(token)
            token = self.sessions.create(now, label, passcode)
            new_device = None
            if device is None:
                new_device = self.devices.add(now)
            else:
                self.devices.use(device, now)
            self._save(now)
        return token, new_device

    def close_sessions(self, now: float, tokens: list[str]) -> None:
        """End the sessions `tokens` open (sign-out); saved at once when one was ended (a sign-out that ends nothing
        writes nothing, so a stranger cannot make the store write by asking)."""
        with self._lock:
            dropped = [self.sessions.drop(token) for token in tokens[:4]]
            if any(dropped):
                self._save(now)

    def flush_if_due(self, now: float) -> None:
        """main()'s timer: drop the sessions that ended (by their limits, or because their passcode line is gone) and
        save what changed, once SAVE_EVERY_S have passed since the last save."""
        live = self.live_passcodes()
        with self._lock:
            self.sessions.prune(now, live)
            if (self.sessions.changed or self.devices.changed) and now - self.saved_at >= SAVE_EVERY_S:
                self._save(now)

    def save(self, now: float) -> None:
        """Save what changed (at shutdown)."""
        with self._lock:
            if self.sessions.changed or self.devices.changed:
                self._save(now)

    def _save(self, now: float) -> None:
        """Write the tables that changed. The caller holds the lock."""
        try:
            private_dir(self.auth_dir, self._warn)
            if self.sessions.changed:
                auth.write_private(self.auth_dir / auth.SESSIONS_FILE, self.sessions.dump().encode("utf-8"))
                self.sessions.changed = False
            if self.devices.changed:
                auth.write_private(self.auth_dir / auth.DEVICES_FILE, self.devices.dump().encode("utf-8"))
                self.devices.changed = False
        except OSError as e:
            self._warn(f"the sign-in sessions could not be saved in {self.auth_dir} ({e.strerror}): "
                       "they last until the store restarts")
        self.saved_at = now


def _write_bytes(path: Path, body: bytes) -> None:
    """Atomic: a new file in the same dir, fsynced, renamed over the name
    (safe_files.write_atomic: whatever the name holds, a link included, is
    replaced and never written through). Caller must hold _write_lock."""
    safe_files.write_atomic(path, body)


def write_store(path: Path, body: bytes) -> None:
    """Verbatim write, serialized so concurrent PUTs each land as one coherent doc."""
    with _write_lock:
        _write_bytes(path, body)


def refresh_mode(body: bytes) -> str | None:
    """What a POST /api/refresh body asks for: None (a refresh) for an empty or whitespace-only body and for a JSON
    object with no `mode` (`{}` included); the mode for a JSON object whose `mode` is one of MODES ("ingest", "ai",
    "ai_reset"). Everything else raises ValueError, which the handler answers with a 400 that queues nothing and
    spends nothing: a body that is not UTF-8, not JSON, or JSON that is not an object (`"ingest"`, `[]`, `7`, `null`),
    and an object whose `mode` is anything else. A body the store cannot read must never spend the day's refresh
    budget."""
    if not body.strip():
        return None
    try:
        doc = json.loads(body)
    except (ValueError, RecursionError) as e:   # not UTF-8 (UnicodeDecodeError is a ValueError) or not JSON
        raise ValueError(f'the body is not a JSON object: send none for a refresh, or {{"mode": "{INGEST_MODE}"}} '
                         "for an ingest") from e
    if not isinstance(doc, dict):
        raise ValueError(f'the body is not a JSON object: send none for a refresh, or {{"mode": "{INGEST_MODE}"}} '
                         "for an ingest")
    if "mode" not in doc:
        return None
    if isinstance(doc["mode"], str) and doc["mode"] in MODES:
        return doc["mode"]
    raise ValueError(f'unknown mode: the modes are {", ".join(repr(m) for m in MODES)}, and a body with no mode asks '
                     "for a refresh")


def _iso_or_none(value) -> str | None:
    """An epoch-seconds field of status.json as the UTC ISO string the browser reads, or None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return rs.utc_iso(value)
        except (OverflowError, OSError, ValueError):   # NaN, or a time no calendar holds
            return None
    return None


class MergedTooLarge(MergeRefused):
    """The document as it would be stored is over its store's byte cap: nothing is written (the handler answers 413;
    a merge's other refusals, MergeRefused, are a 400)."""


def _stored_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def merge_and_write(path: Path, incoming: dict, spec: "StoreSpec", now: float,
                    refresh_dir: Path | None = None) -> bytes:
    """Read-merge-write under one lock (the whole read-modify-write must be
    atomic across threads) and return the merged bytes to echo. The cap is
    checked again AFTER the merge: a merge can add back what the PUT left out
    (merge_prefs keeps the stored lists), and a stored doc over the cap would
    make every later full PUT of it a 413. `now` (epoch seconds) reaches a
    merge whose spec takes it.

    For a document the ingest reads (`wants_ingest`), a write that changed the
    stored bytes then touches the ingest marker in `refresh_dir` at `now`, after
    the document is in place, so an ingest that starts after the touch reads
    it; an identical re-save touches nothing, and neither does any save with no
    refresh dir configured. A marker that cannot be touched is a stderr line,
    not a failed save: the next refresh's ingest reads the document anyway."""
    with _write_lock:
        before = _stored_bytes(path) if spec.wants_ingest else None
        current = read_store(path, spec.empty)
        merged = spec.merge(current, incoming, now) if spec.merge_takes_now else spec.merge(current, incoming)
        body = json.dumps(merged).encode("utf-8")
        if len(body) > spec.max_bytes:
            raise MergedTooLarge(f"the document would be {len(body)} bytes as stored; the limit is {spec.max_bytes}: "
                                 "nothing was written")
        _write_bytes(path, body)
        if spec.wants_ingest and refresh_dir is not None and body != before:
            try:
                rs.touch_ingest_marker(refresh_dir, now)
            except OSError as e:
                print(f"scenario_store: could not ask for an ingest after saving {path.name}: {e}",
                      file=sys.stderr, flush=True)
    return body


def content_length(value: str | None) -> int | None:
    """A Content-Length header's value: None when there is none; raises ValueError unless it is ASCII digits only (a
    digit of another script, such as "²", passes str.isdigit and fails int)."""
    if value is None:
        return None
    if not (value.isascii() and value.isdigit()):
        raise ValueError("Content-Length must be a whole number of bytes")
    return int(value)


def printable(text: str) -> str:
    """`text` for a log line: every character that does not print (a control character, a C1 control from the raw
    request bytes) written as an escape, so a path cannot forge or break a line."""
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in text)


class StoreServer(ThreadingHTTPServer):
    """The store's server: a deeper listen queue than the system's default of 5, so a burst waits rather than being
    reset, and at most REQUEST_SLOTS requests handled at once past their headers (ScenarioHandler.parse_request), so
    requests that hold their body back cannot take every thread the container may start."""
    request_queue_size = LISTEN_QUEUE

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request_slots = threading.BoundedSemaphore(REQUEST_SLOTS)


def announces_body(headers) -> bool:
    """A request says it carries a body: a Content-Length other than 0, or any Transfer-Encoding."""
    if headers.get_all("Transfer-Encoding"):
        return True
    return any(value.strip() != "0" for value in headers.get_all("Content-Length") or [])


class ScenarioHandler(BaseHTTPRequestHandler):
    # One request per connection: every answer says Connection: close (end_headers) and the connection ends after it,
    # so nothing a request leaves unread (a body sent with a GET, the rest of a refused upload) is ever read as a request
    # of its own. HTTP/1.0 keeps http.server from holding a connection open; the proxy opens one per request
    # (deploy/Caddyfile, keepalive off), which costs nothing on the compose network.
    protocol_version = "HTTP/1.0"
    timeout = 30                    # a stalled client must not pin a thread forever
    server_version = SERVER_NAME    # the Server header names the store, never the Python version
    sys_version = ""
    store_dir: Path                 # injected by main() / tests
    signin: SignIn | None = None    # the sign-in; None = sign-in and sign-out answer 503 and no session exists
    auth_required: bool = True      # --auth: every /api/* route asks for a live session (off: tests only)
    hostnames: tuple[str, ...] = () # the names the host rule takes beyond its own (auth.host_allowed): --hostnames
                                    # and the site's own name (--site-address, auth.site_names)
    refresh_dir: Path | None = None # the refresh directory; None = /api/refresh off (503)
    clock = staticmethod(time.time) # injectable so the cap is testable

    # The request path is only ever a *lookup key* into STORES — it never
    # touches the filesystem, so there is no traversal surface.
    def _spec(self) -> StoreSpec | None:
        return STORES.get(urlsplit(self.path).path)

    def _spec_path(self, spec: StoreSpec) -> Path:
        return self.store_dir / spec.filename

    def _discard(self, n: int) -> None:
        """Read and drop `n` request-body bytes in chunks (an over-cap document body, see MAX_DRAIN_BYTES)."""
        while n > 0:
            chunk = self.rfile.read(min(n, 64 * 1024))
            if not chunk:
                return
            n -= len(chunk)

    def send_header(self, keyword: str, value: str) -> None:
        # the one Connection header is end_headers' own (http.server's error page adds one too)
        if keyword.lower() != "connection":
            super().send_header(keyword, value)

    def end_headers(self) -> None:
        super().send_header("Connection", "close")
        self.close_connection = True
        super().end_headers()

    _slot: threading.BoundedSemaphore | None = None     # the request slot this request holds, released in finish()

    def parse_request(self) -> bool:
        """http.server's own reading of the request line and headers, then two rules of the store's: a GET or HEAD that
        says it carries a body is answered 400 without a byte of it read, and the connection ends with that answer;
        and a request takes one of the server's REQUEST_SLOTS before anything reads its body or acts on it, or is
        answered 503 (Retry-After 1) at once. The health check and the session check (UNSLOTTED) take none: they read
        no body and wait on nothing, so they answer even while every slot is held."""
        if not super().parse_request():
            return False
        if self.command in ("GET", "HEAD") and announces_body(self.headers):
            self._send_plain(400, READ_WITH_BODY)
            return False
        slots = getattr(self.server, "request_slots", None)
        if slots is not None and urlsplit(self.path).path not in UNSLOTTED:
            if not slots.acquire(blocking=False):
                self._send(503, {"error": BUSY}, extra_headers={"Retry-After": str(BUSY_RETRY_REQUEST_S)})
                return False
            self._slot = slots
        return True

    def finish(self) -> None:
        try:
            super().finish()
        finally:
            if self._slot is not None:
                self._slot.release()
                self._slot = None

    def _send(self, status: int, doc: dict, *, raw: bytes | None = None, extra_headers: dict | None = None) -> None:
        body = raw if raw is not None else json.dumps(doc).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")   # balances change daily; scenarios must too
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_plain(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status: int, headers: list[tuple[str, str]] = ()) -> None:
        """An answer with no body (204, a redirect), `headers` (Set-Cookie may repeat) and never stored."""
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        if status != 204:                    # a 204 carries no Content-Length
            self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    # ---------------------------------------------------------- what the request says about itself

    def _cookies(self, name: str) -> list[str]:
        return auth.cookie_values("; ".join(self.headers.get_all("Cookie") or []), name)

    def _scheme(self) -> str:
        return auth.request_scheme(self.headers.get("X-FF-Scheme"))

    def _same_origin(self) -> bool:
        """The Origin rule, the one check of every write: an Origin header naming the request's own scheme, host and
        port as Caddy saw them (X-FF-Scheme, X-FF-Host; deploy/auth.py says what stands in for a missing one)."""
        host = self.headers.get("X-FF-Host") or self.headers.get("Host")
        return auth.origin_allowed(self.headers.get("Origin"), self._scheme(), host)

    def _refused_host(self) -> bool:
        """The host rule (auth.host_allowed, the proxy's, applied again against DNS rebinding): a request whose host
        (X-FF-Host, else Host) is not one the dashboard answers to gets a 421 and nothing else is done, whatever its
        scheme (X-FF-Scheme may be a trusted proxy's word, so https says nothing of the name); a store started with
        --auth off skips the rule."""
        if not self.auth_required:
            return False
        if auth.host_allowed(self.headers.get("X-FF-Host") or self.headers.get("Host"), self.hostnames):
            return False
        self._send_plain(421, UNKNOWN_HOST)
        return True

    def _wants_page(self) -> bool:
        """A browser's form post (it asks for HTML back), answered with a redirect rather than a status."""
        return "text/html" in (self.headers.get("Accept") or "").lower()

    def _signed_in(self) -> bool:
        return (self.signin is not None
                and self.signin.session(self.clock(), self._cookies(auth.SESSION_COOKIE)) is not None)

    def _may_use_api(self) -> bool:
        """An /api/* route's own check: a live session, unless the store runs with --auth off. Else a 401 goes out and
        the route does nothing."""
        if not self.auth_required or self._signed_in():
            return True
        self._send(401, {"error": SIGNED_OUT})
        return False

    # ---------------------------------------------------------- the request log

    def version_string(self) -> str:
        return SERVER_NAME

    def log_request(self, code="-", size="-") -> None:
        # the method, the path and the status only: never a query string, a header (a cookie) or a body (a passcode),
        # and every character that does not print escaped
        path = urlsplit(self.path).path if isinstance(getattr(self, "path", None), str) else "-"
        _say(f"{printable(self.command or '-')} {printable(path)} {int(code) if isinstance(code, int) else code}")

    def log_error(self, fmt: str, *args) -> None:
        # http.server's own messages quote the raw request line, which may hold a query string: say only that one came
        _say("a request could not be read or timed out")

    def log_message(self, fmt: str, *args) -> None:
        _say("a request was logged without its details")

    # ---------------------------------------------------------- GET

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        path = urlsplit(self.path).path
        if path == HEALTHZ:
            self._send_plain(200, "ok\n")
            return
        if self._refused_host():
            return
        if path == AUTH_CHECK:
            self._handle_check()
            return
        if path == AUTH_STATUS:
            self._send(200, {"configured": self.signin is not None and self.signin.configured()})
            return
        # Exact match, checked before the STORES lookup: /api/refresh/1 and
        # /api/refreshx keep 404ing, same discipline as the document routes.
        if path == REFRESH_PATH:
            if not self._may_use_api():
                return
            if self.refresh_dir is None:
                self._send(503, {"error": "refresh not configured"})
                return
            self._send(200, self._refresh_view())
            return
        spec = self._spec()
        if spec is None:
            self._send(404, {"error": "not found"})
            return
        if not self._may_use_api():
            return
        self._send(200, read_store(self._spec_path(spec), spec.empty))

    def _handle_check(self) -> None:
        """Caddy's forward_auth question about the request it holds (auth.check_answer)."""
        status = auth.check_answer(signed_in=self._signed_in(), method=self.headers.get("X-Forwarded-Method"),
                                   uri=self.headers.get("X-Forwarded-Uri"), accept=self.headers.get("Accept"),
                                   fetch_mode=self.headers.get("Sec-Fetch-Mode"))
        if status == 204:
            self._send_empty(204)
        elif status == 302:
            self._send_empty(302, [("Location", LOGIN_PAGE)])
        else:
            self._send(401, {"error": SIGNED_OUT})

    # ---------------------------------------------------------- /api/refresh

    def _zone(self) -> str:
        """The household's zone, which every daily limit counts its days in: the mailbox's schedule's, else UTC's
        (rs.household_zone), read at each request."""
        return rs.household_zone(self.refresh_dir)

    def _refresh_view(self) -> dict:
        """Status of the data refresh AND whether the caller may start one.

        Both in one response on purpose: without the rate-limit fields the
        dashboard would have to POST to discover it is blocked, i.e. it could
        not render a truthful disabled button."""
        d = self.refresh_dir
        now = self.clock()
        schedule = rs.read_schedule(d)
        zone = schedule.timezone if schedule is not None else rs.DEFAULT_ZONE
        status = rs.read_status(d)
        request = rs.read_request(d)
        lock = d / rs.LOCK_FILE
        running, ingest_only = self._lock_holder(status, lock)
        q_age = rs.queued_age(request, status, now)

        if running:
            state = "running"
        elif q_age is not None:
            state = ("worker_unavailable" if q_age > rs.WORKER_STALE_S and not ingest_only
                     else "queued")
        elif rs.is_stalled(status, lock, now):
            state = "stalled"
        else:
            state = status.get("state") or "idle"

        # the nonce a caller would be told to watch: the claimed one while a run
        # holds the lock, otherwise the one still sitting in the queue
        in_flight = (status.get("request_nonce") if running
                     else (request or {}).get("nonce") if q_age is not None else None)
        dec = rs.decide(now=now, tz=zone, manual_runs=rs.load_ledger(d),
                        last_run_at=rs.last_started_at(status), running=running,
                        nonce_in_flight=in_flight, queued_age_s=q_age, worker_busy=ingest_only)
        return {
            "state": state,
            "trigger": status.get("trigger"),
            "request_nonce": status.get("request_nonce"),
            "queued_nonce": (request or {}).get("nonce") if q_age is not None else None,
            "started_at": status.get("started_at_iso"),
            "finished_at": status.get("finished_at_iso"),
            "rc": status.get("rc"),
            "steps_failed": status.get("steps_failed") or [],
            "warnings": status.get("warnings") or [],
            # what the last refresh's fetch learned of the bank feed: true when there was no feed and the run read the
            # files alone; the errors the feed reported, each "<connection>: <message>"; the connections whose bank
            # the household must sign in to again at the feed provider (refresh_state.feed_report)
            "files_only": status.get("files_only") is True,
            "feed_errors": _texts(status.get("feed_errors"), rs.MAX_FEED_ERRORS),
            "reauth_needed": _texts(status.get("reauth_needed"), rs.MAX_FEED_ERRORS),
            "can_refresh": dec.allow,
            "reason": dec.reason,
            "retry_after_s": dec.retry_after_s,
            "used_today": dec.used_today,
            "limit_per_day": dec.limit_per_day,
            "last_refresh_at": (rs.utc_iso(dec.last_refresh_at)
                                if dec.last_refresh_at is not None else None),
            # the scheduled refresh's times on the household's clock, so the dashboard need not print a schedule of its
            # own; null until the scheduler has written one (the dashboard says it is not known yet)
            "schedule": ({"times": schedule.times(), "timezone": schedule.timezone}
                         if schedule is not None else None),
            # The ingest without a fetch. It has no nonce: an ingest a client
            # asked for has ended when `wanted` is false AND `last_finished_at`
            # is at or after `last_started_at` (`last_rc` 0 when it succeeded);
            # while `wanted` is true it is waiting or, after an ingest that was
            # stopped or found dead, being run again (refresh_state's docstring).
            "ingest": {
                "wanted": rs.ingest_wanted(rs.marker_time(d), status),
                "last_started_at": _iso_or_none(status.get("ingest_started_at")),
                "last_finished_at": _iso_or_none(status.get("ingest_finished_at")),
                "last_rc": (status.get("ingest_rc") if type(status.get("ingest_rc")) is int
                            else None),
            },
        }

    @staticmethod
    def _lock_holder(status: dict, lock: Path) -> tuple[bool, bool]:
        """(a refresh is running, an ingest-only run is) — liveness from the
        lock, never from the file's contents: the kernel drops an flock when the
        holder dies, so a SIGKILLed refresh can't leave the button disabled
        forever. An ingest-only run holds the same lock but is no refresh: the
        refresher marks it in status.json for the whole time it holds the lock
        (rs.ingest_only_in_flight), so `state` keeps the last refresh's, and a
        refresh asked for meanwhile queues behind it rather than attaching to
        the last refresh's nonce, even before the run's first write of its own
        and after its ingest has ended. A mark left with the lock free reads as
        nothing."""
        held = rs.is_running(lock)
        ingest_only = held and rs.ingest_only_in_flight(status)
        return held and not ingest_only, ingest_only

    def _handle_refresh_post(self) -> None:
        if self.refresh_dir is None:
            self._send(503, {"error": "refresh not configured"})
            return
        # an optional body says the mode (an ingest without a fetch)
        body = b""
        try:
            length = content_length(self.headers.get("Content-Length"))
        except ValueError as e:
            self._send(400, {"error": str(e)})
            return
        if length is not None:
            if length > MAX_REFRESH_BYTES:
                self._send(413, {"error": f"body over {MAX_REFRESH_BYTES} bytes"})
                return
            body = self.rfile.read(length)
        try:
            mode = refresh_mode(body)
        except ValueError as e:
            self._send(400, {"error": str(e)})
            return
        if mode is not None:
            self._handle_ingest_post(mode)
            return

        d, now = self.refresh_dir, self.clock()
        # check -> record -> enqueue in ONE critical section (the same lock that
        # makes the answers read-merge-write atomic). Two family members mashing
        # the button, or one double-click, must not both get through.
        with _write_lock:
            status = rs.read_status(d)
            request = rs.read_request(d)
            ledger = rs.load_ledger(d)
            q_age = rs.queued_age(request, status, now)
            running, ingest_only = self._lock_holder(status, d / rs.LOCK_FILE)
            dec = rs.decide(
                now=now, tz=self._zone(), manual_runs=ledger,
                last_run_at=rs.last_started_at(status), running=running,
                nonce_in_flight=(status.get("request_nonce") if running
                                 else (request or {}).get("nonce")),
                queued_age_s=q_age, worker_busy=ingest_only)
            nonce = ""
            if dec.allow:
                nonce = rs.new_nonce()
                try:
                    # Ledger first: if the request write then fails the caller is
                    # charged for a run that never happened (a 15-minute wait),
                    # which is the safer way round than a run nobody charged for.
                    rs.save_ledger(d, rs.prune(ledger + [now], now))
                    rs.write_request(d, nonce=nonce, now=now)
                except OSError as e:
                    print(f"scenario_store: refresh enqueue failed: {e}",
                          file=sys.stderr, flush=True)
                    self._send(500, {"error": "enqueue failed"})
                    return

        common = {"reason": dec.reason, "used_today": dec.used_today,
                  "limit_per_day": dec.limit_per_day}
        if dec.allow:
            # decide() reports the count BEFORE this request; we just spent one
            self._send(202, {**common, "accepted": True, "nonce": nonce,
                             "queued_at": rs.utc_iso(now),
                             "used_today": dec.used_today + 1})
        elif dec.reason in ("min_gap", "daily_cap"):
            # retry_after_s, not an absolute time: seconds are immune to the
            # client clock skew an ISO timestamp would expose the UI to.
            self._send(429, {"accepted": False, **common,
                             "retry_after_s": dec.retry_after_s,
                             "last_refresh_at": (rs.utc_iso(dec.last_refresh_at)
                                                 if dec.last_refresh_at else None)},
                       extra_headers={"Retry-After": str(dec.retry_after_s)})
        elif dec.reason == "worker_unavailable":
            self._send(503, {"accepted": False, **common,
                             "queued_age_s": int(q_age or 0)})
        else:   # already_running — attach to the run in flight, no budget spent
            self._send(200, {"accepted": False, **common,
                             "nonce": dec.nonce_in_flight})

    def _handle_ingest_post(self, mode: str = INGEST_MODE) -> None:
        """An ingest without a fetch (INGEST_MODE), or one that first runs the
        AI step (AI_MODE) or forgets its stored answers (AI_RESET_MODE): touch
        the marker (an AI mode's own marker, then the ingest marker:
        refresh_state.touch_ai_marker) and say so (202). No request file, no
        ledger entry and no nonce — it spends none of the refresh budget and
        moves no gap between refreshes; the refresher runs it once the marker
        has settled, and the AI step keeps to its own caps. Past
        MAX_INGEST_PER_DAY ingest-only runs today (read from status.json, never
        written here) it is a 429 until local midnight and nothing is touched.
        The store holds no AI key and never reads or writes the AI step's
        store: it only asks."""
        d, now, zone = self.refresh_dir, self.clock(), self._zone()
        if rs.ingest_runs_today(rs.read_status(d), now, zone) >= rs.MAX_INGEST_PER_DAY:
            retry = rs.seconds_to_local_midnight(now, zone)
            self._send(429, {"accepted": False, "reason": "ingest_cap", "retry_after_s": retry},
                       extra_headers={"Retry-After": str(retry)})
            return
        try:
            if mode in AI_MODE_MARKERS:
                rs.touch_ai_marker(d, AI_MODE_MARKERS[mode], now)
            else:
                rs.touch_ingest_marker(d, now)
        except OSError as e:
            print(f"scenario_store: ingest request failed: {e}", file=sys.stderr, flush=True)
            self._send(500, {"error": "ingest request failed"})
            return
        self._send(202, {"accepted": True, "mode": mode})

    def do_PUT(self) -> None:  # noqa: N802
        if self._refused_host():
            return
        spec = self._spec()
        if spec is None:
            self._send(404, {"error": "not found"})
            return
        if not self._same_origin():
            self._send_plain(403, CROSS_SITE)
            return
        if not self._may_use_api():
            return
        length = self._required_length()
        if length is None:
            return
        if length > spec.max_bytes:
            # read first when it is not too much, so the proxy, still sending it, relays the 413 rather than a 502
            if length <= MAX_DRAIN_BYTES:
                self._discard(length)
            self._send(413, {"error": f"body over {spec.max_bytes} bytes"})
            return
        body = self.rfile.read(length)
        try:
            doc = spec.validate(body)
        except ValueError as e:
            self._send(400, {"error": str(e)})
            return
        try:
            if spec.merge is not None:
                body = merge_and_write(self._spec_path(spec), doc, spec, self.clock(), self.refresh_dir)
            else:
                write_store(self._spec_path(spec), body)
        except MergedTooLarge as e:
            self._send(413, {"error": str(e)})       # the body WAS read: the connection stays usable
            return
        except MergeRefused as e:                    # a merge's own refusal of what the PUT holds
            self._send(400, {"error": str(e)})
            return
        except OSError as e:
            print(f"scenario_store: write failed: {e}", file=sys.stderr, flush=True)
            self._send(500, {"error": "write failed"})
            return
        self._send(200, {}, raw=body)   # echo what was stored (merged doc for answers, prefs, manual assets)

    # ---------------------------------------------------------- the sign-in

    def _required_length(self) -> int | None:
        """The request's Content-Length (content_length); None once a refusal has gone out: none given is 411, one
        that is not ASCII digits 400, the body left unread (the connection ends with the answer)."""
        try:
            length = content_length(self.headers.get("Content-Length"))
        except ValueError as e:
            self._send(400, {"error": str(e)})
            return None
        if length is None:
            self._send(411, {"error": "Content-Length required"})
        return length

    def _small_body(self) -> bytes | None:
        """A sign-in or sign-out body, at most auth.MAX_LOGIN_BYTES; None once a refusal has gone out (no length:
        411, not a number: 400, too long: 413, each with the body left unread)."""
        length = self._required_length()
        if length is None:
            return None
        if length > auth.MAX_LOGIN_BYTES:
            self._send(413, {"error": f"body over {auth.MAX_LOGIN_BYTES} bytes"})
            return None
        return self.rfile.read(length)

    def _handle_login(self) -> None:
        """POST /auth/login (the Origin rule already passed): a form or JSON body's passcode, under the throttle.
        Signed in: the session cookie (and a device cookie for a browser without a known one), 204, or 303 to / for a
        form post. Failed, for any reason: 401 with SIGN_IN_FAILED, or 303 to /login?e=1. Refused by the throttle:
        429 with Retry-After, or 303 to /login?e=2. The log says who (the label, the address key), never what."""
        signin = self.signin
        if signin is None:
            self._send(503, {"error": "sign-in not configured"})
            return
        body = self._small_body()
        if body is None:
            return
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type not in (FORM_TYPE, JSON_TYPE):
            self._send(415, {"error": f"send the passcode as {FORM_TYPE} or {JSON_TYPE}"})
            return
        page = self._wants_page()
        address = auth.address_key(self.headers.get("X-FF-Client") or self.client_address[0])
        device = signin.device(self._cookies(auth.DEVICE_COOKIE))
        outcome = signin.attempt(submitted_passcode(body, content_type), address, device, self.clock)
        if outcome.refused is not None:
            retry = outcome.refused.retry_after(self.clock())
            why = "too many sign-ins at once" if outcome.busy else "too many failures"
            _say(f"a sign-in from {address} was refused for {retry} s: {why}")
            if page:
                self._send_empty(303, [("Location", f"{LOGIN_PAGE}?e=2")])
            else:
                self._send(429, {"error": SIGN_IN_WAIT, "retry_after_s": retry},
                           extra_headers={"Retry-After": str(retry)})
            return
        if outcome.label is None:
            _say(f"a sign-in failed from {address}")
            if page:
                self._send_empty(303, [("Location", f"{LOGIN_PAGE}?e=1")])
            else:
                self._send(401, {"error": SIGN_IN_FAILED})
            return
        secure = self._scheme() == "https"
        token, new_device = signin.open_session(self.clock(), outcome.label, outcome.passcode,
                                                self._cookies(auth.SESSION_COOKIE), device)
        cookies = [("Set-Cookie", auth.session_cookie(token, secure=secure))]
        if new_device is not None:
            cookies.append(("Set-Cookie", auth.device_cookie(new_device, secure=secure)))
        _say(f"{outcome.label} signed in from {address}")
        if page:
            self._send_empty(303, [("Location", "/"), *cookies])
        else:
            self._send_empty(204, cookies)

    def _handle_logout(self) -> None:
        """POST /auth/logout (the Origin rule already passed): the session the browser holds ends, server side, and
        its cookie is cleared; 204, or 303 to /login for a form post. Without a session it answers the same."""
        if self._small_body() is None:
            return
        if self.signin is not None:
            self.signin.close_sessions(self.clock(), self._cookies(auth.SESSION_COOKIE))
        cleared = ("Set-Cookie", auth.cleared_session_cookie(secure=self._scheme() == "https"))
        # and the browser drops what it cached of the dashboard (the exports above all), so a shared computer keeps none
        forget = ("Clear-Site-Data", '"cache"')
        if self._wants_page():
            self._send_empty(303, [("Location", LOGIN_PAGE), cleared, forget])
        else:
            self._send_empty(204, [cleared, forget])

    def do_POST(self) -> None:  # noqa: N802
        if self._refused_host():
            return
        path = urlsplit(self.path).path
        if path in STORES:
            self._send(405, {"error": "use PUT"})
            return
        if path not in (AUTH_LOGIN, AUTH_LOGOUT, REFRESH_PATH):
            self._send(404, {"error": "not found"})
            return
        if not self._same_origin():
            self._send_plain(403, CROSS_SITE)
            return
        if path == AUTH_LOGIN:
            self._handle_login()
        elif path == AUTH_LOGOUT:
            self._handle_logout()
        elif self._may_use_api():
            self._handle_refresh_post()

    def do_DELETE(self) -> None:  # noqa: N802
        if self._refused_host():
            return
        self._send(405, {"error": "method not allowed"})


def _texts(value, limit: int) -> list[str]:
    """A list of text from status.json as the refresh view passes it on: its strings, the first `limit` of them; what
    is no list is none."""
    return [v for v in value if isinstance(v, str)][:limit] if isinstance(value, list) else []


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Family dashboard store")
    ap.add_argument("--host", default="127.0.0.1",
                    help="the address to listen on (default 127.0.0.1; the container passes its own, on the compose "
                         "network only)")
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--dir", default=None,
                    help="directory holding the store files (scenarios.json, "
                         "answers.json, prefs.json, manual_assets.json); default: "
                         "FF_STATE_FAMILY_DIR, else FF_STATE_DIR, else FF_HOME's state/family/, "
                         "else /app/scenarios")
    # deprecated alias from the single-file era; its parent becomes --dir
    ap.add_argument("--file", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--refresh-dir", default=None,
                    help="shared dir the refresher watches; default: FF_STATE_REFRESH_DIR, "
                         "else FF_HOME's state/refresh/. With none, /api/refresh fails "
                         "closed with 503.")
    ap.add_argument("--auth", choices=("required", "off"), default="required",
                    help="required (the default): every /api/* route asks for a live session. off: they answer "
                         "without one; for tests and local development only, never a stack anyone can reach")
    ap.add_argument("--secrets-web-dir", default=None,
                    help="the folder holding the passcodes file; default: FF_SECRETS_WEB_DIR, else FF_HOME's "
                         "secrets/web/, else /app/secrets/web")
    ap.add_argument("--state-auth-dir", default=None,
                    help="where the sessions and devices are saved (made at the first sign-in); default: "
                         "FF_STATE_AUTH_DIR, else FF_HOME's state/auth/, else /app/state/auth")
    ap.add_argument("--hostnames", default=os.environ.get("FF_HOSTNAMES", ""),
                    help="the names, separated by spaces, the dashboard answers to besides an IP address, localhost "
                         "and a name ending .local (default: FF_HOSTNAMES, else none); any other host is a 421")
    ap.add_argument("--site-address", default=os.environ.get("SITE_ADDRESS", ""),
                    help="the web server's site address (default: SITE_ADDRESS): when it names a host, such as "
                         "example.net or https://example.net:8443, that name is one the dashboard answers to too")
    return ap.parse_args(argv)


def allowed_names(args: argparse.Namespace) -> tuple[str, ...]:
    """the names the host rule takes beyond its own: --hostnames, then the site's own name (auth.site_names)"""
    return tuple(dict.fromkeys([*args.hostnames.split(), *auth.site_names(args.site_address)]))


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    store_dir = rs.locate("documents", args.dir or (str(Path(args.file).parent) if args.file else None))
    secrets_dir = rs.locate("secrets_web", args.secrets_web_dir)
    auth_dir = rs.locate("auth", args.state_auth_dir)

    ScenarioHandler.store_dir = store_dir
    ScenarioHandler.store_dir.mkdir(parents=True, exist_ok=True)
    ScenarioHandler.auth_required = args.auth == "required"
    ScenarioHandler.hostnames = allowed_names(args)

    refresh = "disabled"
    refresh_dir = rs.locate("refresh", args.refresh_dir, fallback=False)
    if refresh_dir is not None:
        refresh_dir.mkdir(parents=True, exist_ok=True)
        ScenarioHandler.refresh_dir = refresh_dir
        # A bind source docker created itself is root-owned: reads keep working
        # and every write 500s. Say so at startup instead of at first click.
        refresh = f"enabled -> {refresh_dir}" if os.access(refresh_dir, os.W_OK) \
            else f"NOT WRITABLE by uid {os.getuid()} (500s) -> {refresh_dir}"

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))   # fast `docker stop`; the finally below still saves
    server = StoreServer((args.host, args.port), ScenarioHandler)
    # the port bound, which --port 0 leaves to the system; this line comes first (the tests read the port from it)
    print(f"scenario_store: serving {', '.join(STORES)} on {args.host}:{server.server_address[1]} "
          f"-> {store_dir}; sign-in {'required' if ScenarioHandler.auth_required else 'OFF'}; refresh {refresh}",
          file=sys.stderr, flush=True)
    # Loaded after the server is bound and before it answers: a request waits in the listen queue meanwhile.
    signin = SignIn(secrets_dir, auth_dir, now=time.time())
    ScenarioHandler.signin = signin
    count = len(signin.passcodes.lines())
    if count:
        _say(f"sign-in: {count} passcode{'s' if count != 1 else ''} in {secrets_dir}; "
             f"{len(signin.sessions)} session{'s' if len(signin.sessions) != 1 else ''} kept in {auth_dir}")
    else:
        _say(f"sign-in is not set up: no passcode in {secrets_dir / auth.PASSCODES_FILE}, so nobody can sign in")
    if not ScenarioHandler.auth_required:
        _say("WARNING: started with --auth off: /api/* answers without a session. For tests and local development "
             "only, never a stack anyone can reach")

    stop = threading.Event()

    def save_now_and_then() -> None:
        while not stop.wait(60):
            signin.flush_if_due(time.time())

    threading.Thread(target=save_now_and_then, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        stop.set()
        signin.save(time.time())


if __name__ == "__main__":
    main()

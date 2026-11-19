# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Sign-in for the family store (deploy/scenario_store.py): passcode lines, sessions, known devices, the throttle,
cookie strings, the Origin rule, the host rule and the answer /auth/check gives.

Pure and stdlib-only. Clocks are arguments (`now`, epoch seconds) and randomness is a seam (`token`, called as
`token(32)`, secrets.token_urlsafe by default; `salt` and `hasher` for the passcode hash); the only I/O is on a path a
caller hands in (PasscodeFile, write_private) and the one stderr line a damaged file earns. It imports nothing of the
project: the store runs it by path with the system python, and `python -m etl passcode` (etl/passcode_cli.py) loads
this same file by path, so the rules for a passcode line live here once.

The headers the store trusts
----------------------------
The store listens on the compose network only and is reached through Caddy, which sets these headers on every request
it passes on and removes any copy a client sent. The store never reads X-Forwarded-For.

  header              what it says                                         when it is missing
  X-FF-Client         the client's address as Caddy saw it (an IP          the socket's peer address
                      literal): the throttle's key (address_key)
  X-FF-Scheme         `https` when the request reached Caddy over TLS, or   http (any value but https is http)
                      a trusted proxy in front of Caddy (TRUSTED_PROXIES)
                      said exactly https in X-Forwarded-Proto; else
                      `http`: the cookies' Secure flag, the Origin rule
  X-FF-Host           the host and port the browser used (its Host         the request's own Host header
                      header as Caddy received it): the Origin rule
                      and the host rule
  X-Forwarded-Method  /auth/check only: the method of the request that     the answer is 401, never a redirect
                      Caddy asks about (forward_auth sets it)
  X-Forwarded-Uri     /auth/check only: that request's path and query      the answer is 401, never a redirect
  Accept,             /auth/check only: that request's own headers,        not a page load: 401
  Sec-Fetch-Mode      which forward_auth passes on unchanged

The host rule (host_allowed), the proxy's own applied again by the store against DNS rebinding: the host part of
X-FF-Host (else Host) must be an IP literal, `localhost`, a name ending `.local`, or one of the names the store is given
(--hostnames, else FF_HOSTNAMES; none by default) or the site's own name (site_names of --site-address, else
SITE_ADDRESS: its host when that is a name), compared without regard to case; anything else is a 421 on every route but
/healthz, whatever the scheme (X-FF-Scheme may be a trusted proxy's word, so https says nothing of the name). A store
started with --auth off skips the rule.

The store keeps no connection open: one request per connection, every answer with Connection: close, and a GET or HEAD
that says it carries a body is refused unread, so a body is never read as a request of its own (the proxy, too, opens a
connection per request and refuses such a GET).

Passcodes
---------
secrets/web/passcodes holds one line per passcode, `label:scrypt:15:8:1:<salt>:<hash>`: a label of 1 to 32 of
a-z 0-9 _ -, then scrypt's cost (N = 2**15, r = 8, p = 1, maxmem 64 MiB), a 16-byte salt and the 32-byte hash, both
unpadded URL-safe base64. Blank lines are ignored; any other line that is not of that shape is skipped and reported
once on stderr by its line number, never its content; at most MAX_PASSCODES lines are used. PasscodeFile reads the
file again when its mtime, size or inode changes. match() hashes the typed passcode once for every usable line and
compares each result in constant time, so its time does not depend on which line matched; with no usable line it
still hashes once, so "not set up" costs what "wrong" costs. A passcode (passcode_problem) has at least 8 characters,
or 10 when all are digits, and is taken as typed: no character is refused and none is trimmed, but it is put in
Unicode normal form C before it is hashed or checked, so the same characters typed on two devices match.

Sessions and devices
--------------------
A session's token is 32 random bytes, URL-safe; only its SHA-256 is kept, with when it was created and last seen, the
label of the passcode that opened it and that passcode line's fingerprint (passcode_fingerprint: a SHA-256 of the
label and the salt, never of the hash). It ends after SESSION_IDLE_S unseen or SESSION_ABSOLUTE_S after sign-in, and
at most MAX_SESSIONS are kept (a new one drops the least recently seen). It also ends as soon as no line of the
passcodes file has its fingerprint: removing a passcode, or changing it (a new line has a new salt), signs out every
browser that passcode signed in, and leaves the other passcodes' sessions alone. check() refreshes last_seen in memory
only; the store saves the table (dump/load) as it says. A device token marks a browser that has signed in before: the
store keeps the hashes of at most MAX_DEVICES (the least recently used dropped). It carries no authority but the
throttle's exemption from the address lock.

The throttle
------------
Throttle.decide is pure: (now, address key, known device or None, state) -> Allow | Wait | Locked, the shape of
refresh_state.decide; record() applies an attempt's outcome. Per address key (an IPv4 address, an IPv6 /64, or one
shared key for an address that does not parse): ADDRESS_FAILURES failures lock it for LOCK_FIRST_S, each further lock
twice as long up to LOCK_MAX_S, and a key with no failure for ADDRESS_FORGET_S starts afresh; a success clears the
count towards the next lock. A request carrying a known device is never address-locked and its failures do not count
against its address: it has its own budget of DEVICE_FAILURES per DEVICE_WINDOW_S (Wait). More than GLOBAL_FAILURES
failures in the last GLOBAL_WINDOW_S, from everyone, make attempts without a known device slow (Allow(slow=True)):
the store waits SLOW_WAIT_S before hashing them and hashes them one at a time; a correct passcode still succeeds once
it is checked. A locked or waiting attempt is not hashed and is not a failure. The state is in memory only, so a
restart forgets every lock (the store's restart is rare and slower than a lock), and it is bounded: at most
MAX_THROTTLE_KEYS address keys and as many devices, the least recently failed that is not locked dropped first (so
fresh keys cannot push a locked address out), a locked one only when every key is locked.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import sys
import threading
import unicodedata
from collections import OrderedDict, deque
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

# ---------------------------------------------------------------- passcodes

PASSCODES_FILE = "passcodes"
MAX_PASSCODES = 8
LABEL = re.compile(r"[a-z0-9_-]{1,32}")
SCRYPT_N_LOG2, SCRYPT_R, SCRYPT_P = 15, 8, 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
SALT_BYTES, HASH_BYTES = 16, 32
MIN_PASSCODE_CHARS = 8
MIN_DIGITS_PASSCODE_CHARS = 10
# passcodes printed in the repository's own examples: anyone can read them, so none is ever accepted
SAMPLE_PASSCODES = frozenset({"replace-this-sample-passcode"})
# the sign-in body's cap: a passcode must fit in it however the page sends it (form-encoded or as JSON)
MAX_LOGIN_BYTES = 1024
_B64 = re.compile(r"[A-Za-z0-9_-]+")
# hashed when no line is usable, so a check that cannot succeed costs what a wrong passcode costs
_DUMMY_SALT = bytes(SALT_BYTES)

Hasher = Callable[[bytes, bytes], bytes]


def stderr_line(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def scrypt(secret: bytes, salt: bytes) -> bytes:
    """The passcode hash: scrypt at the line format's one cost."""
    return hashlib.scrypt(secret, salt=salt, n=2**SCRYPT_N_LOG2, r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM,
                          dklen=HASH_BYTES)


def b64(raw: bytes) -> str:
    """URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def unb64(text: str, size: int) -> bytes | None:
    """The `size` bytes `text` spells in unpadded URL-safe base64 (the one spelling b64 writes), else None."""
    if not _B64.fullmatch(text):
        return None
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError:
        return None
    return raw if len(raw) == size and b64(raw) == text else None


@dataclass(frozen=True)
class PasscodeLine:
    label: str
    salt: bytes
    digest: bytes


def normal_form(passcode: str) -> str:
    """The passcode as it is hashed and checked: Unicode normal form C (a decomposed "é" and a composed one are one)."""
    return unicodedata.normalize("NFC", passcode)


def passcode_fingerprint(line: PasscodeLine) -> str:
    """What a session keeps of the passcode line that opened it: a SHA-256 of the label and the salt (never of the
    hash), in hex. A new passcode for the label has a new salt, so a new fingerprint."""
    return hashlib.sha256(line.label.encode("ascii") + b":" + line.salt).hexdigest()


def label_problem(label: str) -> str | None:
    """Why `label` cannot name a passcode, or None."""
    if LABEL.fullmatch(label):
        return None
    return "a label is 1 to 32 characters, each a lowercase letter, a digit, - or _"


def passcode_problem(passcode: str) -> str | None:
    """Why `passcode` cannot be a sign-in passcode, or None: at least MIN_PASSCODE_CHARS characters, or
    MIN_DIGITS_PASSCODE_CHARS when every one is a digit (of any script), text a browser can send, and short enough to
    fit the sign-in body however the page sends it, and not a passcode the repository's examples print, all of it
    judged in normal form C. Nothing else is asked of it."""
    passcode = normal_form(passcode)
    try:
        passcode.encode("utf-8")
    except UnicodeEncodeError:
        return "the passcode holds characters a browser cannot send: type it again"
    if passcode.strip().casefold() in SAMPLE_PASSCODES:
        return "that is the example file's sample passcode, which anyone can read: choose one of your own"
    if passcode.isdecimal() and len(passcode) < MIN_DIGITS_PASSCODE_CHARS:
        return f"a passcode of digits only needs at least {MIN_DIGITS_PASSCODE_CHARS} of them"
    if len(passcode) < MIN_PASSCODE_CHARS:
        return f"a passcode needs at least {MIN_PASSCODE_CHARS} characters"
    sent = max(len(urlencode({"passcode": passcode})),
               len(json.dumps({"passcode": passcode}).encode("utf-8")),
               len(json.dumps({"passcode": passcode}, ensure_ascii=False).encode("utf-8")))
    if sent > MAX_LOGIN_BYTES:
        return "the passcode is too long to sign in with: choose a shorter one"
    return None


def hash_passcode(label: str, passcode: str, *, salt: bytes | None = None, hasher: Hasher = scrypt) -> str:
    """The passcode file's line for `label` and `passcode` (in normal form C; without a line ending), under a fresh
    random salt unless one is given."""
    problem = label_problem(label)
    if problem:
        raise ValueError(problem)
    salt = secrets.token_bytes(SALT_BYTES) if salt is None else salt
    digest = hasher(normal_form(passcode).encode("utf-8"), salt)
    return f"{label}:scrypt:{SCRYPT_N_LOG2}:{SCRYPT_R}:{SCRYPT_P}:{b64(salt)}:{b64(digest)}"


def parse_line(text: str) -> PasscodeLine | None:
    """One line of the passcode file (surrounding whitespace aside), or None when it is not of the line's shape."""
    parts = text.strip().split(":")
    if len(parts) != 7:
        return None
    label, kind, n, r, p, salt_text, digest_text = parts
    if not LABEL.fullmatch(label) or kind != "scrypt" or (n, r, p) != (str(SCRYPT_N_LOG2), str(SCRYPT_R),
                                                                        str(SCRYPT_P)):
        return None
    salt, digest = unb64(salt_text, SALT_BYTES), unb64(digest_text, HASH_BYTES)
    if salt is None or digest is None:
        return None
    return PasscodeLine(label, salt, digest)


def parse_file(data: bytes) -> tuple[list[PasscodeLine], list[str]]:
    """The usable lines of a passcode file's bytes (at most MAX_PASSCODES) and a sentence for each problem: a line that
    is neither blank nor a passcode line, by its number only, and lines past the limit."""
    usable: list[PasscodeLine] = []
    problems: list[str] = []
    for number, raw in enumerate(data.split(b"\n"), 1):
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            text = None
        if text is not None and not text.strip():
            continue
        line = parse_line(text) if text is not None else None
        if line is None:
            problems.append(f"line {number} of the passcodes file is not a passcode line: it is ignored")
        else:
            usable.append(line)
    if len(usable) > MAX_PASSCODES:
        problems.append(f"the passcodes file holds {len(usable)} passcodes: only the first {MAX_PASSCODES} are used")
        usable = usable[:MAX_PASSCODES]
    return usable, problems


def match(lines: Iterable[str | PasscodeLine], passcode: str, *, hasher: Hasher = scrypt) -> PasscodeLine | None:
    """The first line `passcode` matches, else None. Every usable line (parse_line; the first MAX_PASSCODES of them)
    is hashed and compared with hmac.compare_digest, whichever one matches; with none usable the passcode is hashed
    once under a dummy salt. The passcode is compared as typed, in normal form C."""
    usable = [line if isinstance(line, PasscodeLine) else parse_line(line) for line in lines]
    usable = [line for line in usable if line is not None][:MAX_PASSCODES]
    secret = normal_form(passcode).encode("utf-8", "surrogatepass")
    if not usable:
        hasher(secret, _DUMMY_SALT)
        return None
    found = None
    for line in usable:
        same = hmac.compare_digest(hasher(secret, line.salt), line.digest)
        if same and found is None:
            found = line
    return found


def verify(lines: Iterable[str | PasscodeLine], passcode: str, *, hasher: Hasher = scrypt) -> str | None:
    """The label of the line `passcode` matches (match), else None."""
    line = match(lines, passcode, hasher=hasher)
    return line.label if line is not None else None


class PasscodeFile:
    """The passcode file at `path`, read again whenever its mtime, size or inode changes; a missing file is no
    passcode. Each version's problems (parse_file) are reported once, through `warn`. Thread-safe."""

    def __init__(self, path: Path, *, warn: Callable[[str], None] = stderr_line) -> None:
        self.path = Path(path)
        self._warn = warn
        self._seen: object = ()
        self._lines: list[PasscodeLine] = []
        self._lock = threading.Lock()

    def lines(self) -> list[PasscodeLine]:
        with self._lock:
            try:
                st = os.stat(self.path)
                seen: object = (st.st_mtime_ns, st.st_size, st.st_ino)
            except FileNotFoundError:
                seen = None
            except OSError as e:
                seen = ("unreadable", e.errno)
            if seen == self._seen:
                return self._lines
            self._seen = seen
            self._lines = []
            if seen is None:
                return self._lines
            try:
                data = self.path.read_bytes()
            except OSError as e:
                self._warn(f"the passcodes file cannot be read ({e.strerror}): nobody can sign in")
                return self._lines
            self._lines, problems = parse_file(data)
            for problem in problems:
                self._warn(problem)
            return self._lines


# ---------------------------------------------------------------- sessions and devices

SESSIONS_FILE = "sessions.json"
DEVICES_FILE = "devices.json"
SESSION_COOKIE = "ff_session"
DEVICE_COOKIE = "ff_device"
TOKEN_BYTES = 32
COOKIE_MAX_AGE = 400 * 86400          # 34,560,000 s: browsers cap a cookie's lifetime at 400 days
SESSION_IDLE_S = 30 * 86400
SESSION_ABSOLUTE_S = 400 * 86400
MAX_SESSIONS = 50
MAX_DEVICES = 200
# what a token can be: the URL-safe alphabet, of a length that bounds the hashing
_TOKEN = re.compile(r"[A-Za-z0-9_-]{20,128}")
_KEY = re.compile(r"[0-9a-f]{64}")

TokenSource = Callable[[int], str]


def token_key(token: str) -> str:
    """What the store keeps of a token: its SHA-256, in hex."""
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _is_time(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


SESSION_KEYS = frozenset({"created", "last_seen", "label", "passcode"})


class Sessions:
    """The sign-in sessions: token hash -> {created, last_seen (epoch seconds), label, passcode (the fingerprint of the
    line that opened it)}. Not thread-safe: the store holds its own lock around it. `changed` is set by anything a save
    should write (last_seen included) and cleared by the store once it has written dump()."""

    def __init__(self, records: dict[str, dict] | None = None, *, token: TokenSource = secrets.token_urlsafe) -> None:
        self._records: dict[str, dict] = dict(records or {})
        self._token = token
        self.changed = False

    def __len__(self) -> int:
        return len(self._records)

    @staticmethod
    def _alive(record: dict, now: float) -> bool:
        return now - record["last_seen"] < SESSION_IDLE_S and now - record["created"] < SESSION_ABSOLUTE_S

    def create(self, now: float, label: str, passcode: str) -> str:
        """A new session for `label`, opened by the passcode line whose fingerprint is `passcode`; returns its token.
        Ended sessions go, and past MAX_SESSIONS the least recently seen others do."""
        token = self._token(TOKEN_BYTES)
        key = token_key(token)
        self._records[key] = {"created": now, "last_seen": now, "label": label, "passcode": passcode}
        self.prune(now)
        while len(self._records) > MAX_SESSIONS:
            oldest = min((k for k in self._records if k != key), key=lambda k: (self._records[k]["last_seen"], k))
            del self._records[oldest]
        self.changed = True
        return token

    def check(self, now: float, token: object, passcodes: Collection[str]) -> str | None:
        """The label of the live session `token` opens, its last_seen moved to `now`; else None. A session is live
        while it is within its limits and its passcode's fingerprint is one of `passcodes` (the current lines'); an
        ended one is dropped."""
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            return None
        key = token_key(token)
        record = self._records.get(key)
        if record is None:
            return None
        if not self._alive(record, now) or record.get("passcode") not in passcodes:
            del self._records[key]
            self.changed = True
            return None
        record["last_seen"] = max(record["last_seen"], now)
        self.changed = True
        return record["label"]

    def drop(self, token: object) -> bool:
        """End the session `token` opens (sign-out); whether there was one."""
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            return False
        if self._records.pop(token_key(token), None) is None:
            return False
        self.changed = True
        return True

    def prune(self, now: float, passcodes: Collection[str] | None = None) -> int:
        """Drop every ended session, and with `passcodes` (the current lines' fingerprints) every session no current
        line opened; how many went."""
        ended = [k for k, record in self._records.items()
                 if not self._alive(record, now) or (passcodes is not None and record.get("passcode") not in passcodes)]
        for k in ended:
            del self._records[k]
        if ended:
            self.changed = True
        return len(ended)

    def dump(self) -> str:
        return json.dumps({"version": 1, "sessions": self._records}, sort_keys=True)

    @classmethod
    def load(cls, text: str | None, now: float, *, token: TokenSource = secrets.token_urlsafe,
             warn: Callable[[str], None] = stderr_line) -> Sessions:
        """The table `text` (dump's form) holds, ended sessions left out and at most MAX_SESSIONS kept. No text, or a
        text that is not exactly that form (a record without its passcode's fingerprint included), is no session, said
        in one line through `warn`."""
        if text is None:
            warn("no saved sign-in sessions: starting with none")
            return cls(token=token)
        try:
            doc = json.loads(text)
            records = doc["sessions"] if isinstance(doc, dict) and doc.get("version") == 1 else None
            if not isinstance(records, dict) or not all(
                    isinstance(k, str) and _KEY.fullmatch(k) and isinstance(r, dict)
                    and set(r) == SESSION_KEYS and _is_time(r["created"]) and _is_time(r["last_seen"])
                    and isinstance(r["label"], str) and isinstance(r["passcode"], str)
                    and _KEY.fullmatch(r["passcode"]) for k, r in records.items()):
                raise ValueError("not a sessions table")
        except (ValueError, KeyError, TypeError, RecursionError):
            warn("the saved sign-in sessions cannot be read: starting with none, so everyone signs in again")
            return cls(token=token)
        sessions = cls(records, token=token)
        sessions.prune(now)
        while len(sessions._records) > MAX_SESSIONS:
            oldest = min(sessions._records, key=lambda k: (sessions._records[k]["last_seen"], k))
            del sessions._records[oldest]
        sessions.changed = False
        return sessions


class Devices:
    """The browsers that have signed in before: token hash -> when last used to sign in (epoch seconds), at most
    MAX_DEVICES. Not thread-safe (the store's lock covers it); `changed` as in Sessions."""

    def __init__(self, records: dict[str, float] | None = None, *, token: TokenSource = secrets.token_urlsafe) -> None:
        self._records: dict[str, float] = dict(records or {})
        self._token = token
        self.changed = False

    def __len__(self) -> int:
        return len(self._records)

    def known(self, token: object) -> str | None:
        """The key of the known device `token` names, else None."""
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            return None
        key = token_key(token)
        return key if key in self._records else None

    def add(self, now: float) -> str:
        """A new device; returns its token. Past MAX_DEVICES the least recently used goes."""
        token = self._token(TOKEN_BYTES)
        key = token_key(token)
        self._records[key] = now
        while len(self._records) > MAX_DEVICES:
            oldest = min((k for k in self._records if k != key), key=lambda k: (self._records[k], k))
            del self._records[oldest]
        self.changed = True
        return token

    def use(self, key: str, now: float) -> None:
        """The device `key` signed in at `now`."""
        if key in self._records:
            self._records[key] = max(self._records[key], now)
            self.changed = True

    def dump(self) -> str:
        return json.dumps({"version": 1, "devices": self._records}, sort_keys=True)

    @classmethod
    def load(cls, text: str | None, *, token: TokenSource = secrets.token_urlsafe,
             warn: Callable[[str], None] = stderr_line) -> Devices:
        """The devices `text` (dump's form) holds, at most MAX_DEVICES; anything else is none, said in one line."""
        if text is None:
            warn("no saved devices: every browser counts as new until it signs in")
            return cls(token=token)
        try:
            doc = json.loads(text)
            records = doc["devices"] if isinstance(doc, dict) and doc.get("version") == 1 else None
            if not isinstance(records, dict) or not all(
                    isinstance(k, str) and _KEY.fullmatch(k) and _is_time(v) for k, v in records.items()):
                raise ValueError("not a devices table")
        except (ValueError, KeyError, TypeError, RecursionError):
            warn("the saved devices cannot be read: every browser counts as new until it signs in")
            return cls(token=token)
        newest = sorted(records.items(), key=lambda kv: (kv[1], kv[0]))[-MAX_DEVICES:]
        return cls(dict(newest), token=token)


# ---------------------------------------------------------------- the throttle

ADDRESS_FAILURES = 5
LOCK_FIRST_S = 60
LOCK_MAX_S = 15 * 60
ADDRESS_FORGET_S = 24 * 3600
DEVICE_FAILURES = 10
DEVICE_WINDOW_S = 15 * 60
GLOBAL_FAILURES = 30
GLOBAL_WINDOW_S = 10 * 60
SLOW_WAIT_S = 3
MAX_THROTTLE_KEYS = 10_000
UNKNOWN_ADDRESS = "unknown"


@dataclass(frozen=True)
class Allow:
    """Hash the passcode; `slow`: wait SLOW_WAIT_S first and hash one such attempt at a time."""
    slow: bool = False


@dataclass(frozen=True)
class Wait:
    """A known device has spent its budget: try again in `seconds`."""
    seconds: int

    def retry_after(self, now: float) -> int:
        return self.seconds


@dataclass(frozen=True)
class Locked:
    """The address is locked until `until` (epoch seconds)."""
    until: float

    def retry_after(self, now: float) -> int:
        return max(1, math.ceil(self.until - now))


Decision = Allow | Wait | Locked


@dataclass
class AddressRecord:
    failures: int = 0          # since the last lock or success
    locks: int = 0             # how many times it has been locked since it was last forgotten
    until: float = 0.0         # locked until
    last: float = 0.0          # its last failure


@dataclass
class ThrottleState:
    addresses: OrderedDict[str, AddressRecord] = field(default_factory=OrderedDict)
    # a known device's newest failures, at most DEVICE_FAILURES of them
    devices: OrderedDict[str, deque] = field(default_factory=OrderedDict)
    # everyone's newest failures: one more than GLOBAL_FAILURES is all the slow rule reads
    recent: deque = field(default_factory=lambda: deque(maxlen=GLOBAL_FAILURES + 1))


def address_key(address: str | None) -> str:
    """The throttle's key for a client address: an IPv4 address (an IPv4-mapped IPv6 one as IPv4), an IPv6 address's
    /64, or UNKNOWN_ADDRESS for anything that is not an IP literal."""
    value = (address or "").strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return UNKNOWN_ADDRESS
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network((int(ip) >> 64 << 64, 64)))
    return str(ip)


def _forgotten(record: AddressRecord, now: float) -> bool:
    return now - record.last >= ADDRESS_FORGET_S


class Throttle:
    """The sign-in throttle (the module docstring has the rules). decide() is pure; check() and record() hold a lock
    so the store's threads may share one Throttle."""

    def __init__(self, state: ThrottleState | None = None) -> None:
        self.state = state or ThrottleState()
        self._lock = threading.Lock()

    @staticmethod
    def decide(now: float, address: str, device: str | None, state: ThrottleState) -> Decision:
        """May an attempt from `address` (an address_key), carrying the known device `device` or none, be hashed?
        Reads `state`, changes nothing."""
        if device is not None:
            failures = state.devices.get(device)
            if failures is not None and len(failures) >= DEVICE_FAILURES and now - failures[0] < DEVICE_WINDOW_S:
                return Wait(max(1, math.ceil(failures[0] + DEVICE_WINDOW_S - now)))
            return Allow(slow=False)
        record = state.addresses.get(address)
        if record is not None and not _forgotten(record, now) and record.until > now:
            return Locked(record.until)
        recent = state.recent
        return Allow(slow=len(recent) > GLOBAL_FAILURES and now - recent[0] < GLOBAL_WINDOW_S)

    @staticmethod
    def apply(now: float, address: str, device: str | None, ok: bool, state: ThrottleState) -> None:
        """Record a hashed attempt's outcome in `state`."""
        if ok:
            if device is not None:
                state.devices.pop(device, None)
            elif address in state.addresses:
                state.addresses[address].failures = 0
            return
        state.recent.append(now)
        if device is not None:
            failures = state.devices.pop(device, None) or deque(maxlen=DEVICE_FAILURES)
            failures.append(now)
            state.devices[device] = failures
            while len(state.devices) > MAX_THROTTLE_KEYS:
                state.devices.popitem(last=False)
            return
        record = state.addresses.pop(address, None)
        if record is None or _forgotten(record, now):
            record = AddressRecord()
        record.failures += 1
        record.last = now
        if record.failures >= ADDRESS_FAILURES:
            record.locks += 1
            record.failures = 0
            record.until = now + min(LOCK_FIRST_S << min(record.locks - 1, 16), LOCK_MAX_S)
        state.addresses[address] = record
        while len(state.addresses) > MAX_THROTTLE_KEYS:
            # the least recently failed key that is not locked, so new keys never push a locked address out
            unlocked = next((k for k, r in state.addresses.items() if r.until <= now), None)
            if unlocked is None:
                state.addresses.popitem(last=False)
            else:
                del state.addresses[unlocked]

    def check(self, now: float, address: str, device: str | None) -> Decision:
        with self._lock:
            return self.decide(now, address, device, self.state)

    def record(self, now: float, address: str, device: str | None, ok: bool) -> None:
        with self._lock:
            self.apply(now, address, device, ok, self.state)


# ---------------------------------------------------------------- cookies, the Origin rule, /auth/check

def _cookie(name: str, value: str, max_age: int, secure: bool) -> str:
    text = f"{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
    return text + "; Secure" if secure else text


def session_cookie(token: str, *, secure: bool) -> str:
    return _cookie(SESSION_COOKIE, token, COOKIE_MAX_AGE, secure)


def device_cookie(token: str, *, secure: bool) -> str:
    return _cookie(DEVICE_COOKIE, token, COOKIE_MAX_AGE, secure)


def cleared_session_cookie(*, secure: bool) -> str:
    return _cookie(SESSION_COOKIE, "", 0, secure)


def cookie_values(header: str | None, name: str) -> list[str]:
    """Every value the Cookie header gives the cookie `name`, in order."""
    values = []
    for part in (header or "").split(";"):
        key, sep, value = part.partition("=")
        if sep and key.strip() == name:
            values.append(value.strip())
    return values


def request_scheme(value: str | None) -> str:
    """X-FF-Scheme as the store reads it: https, or http for any other value or none."""
    return "https" if (value or "").strip().lower() == "https" else "http"


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(text: str) -> tuple[str, str, int] | None:
    """(scheme, lower-case host, port with the scheme's default filled in) of an origin `scheme://host[:port]`, else
    None: anything with a path, a query, a fragment or a user, or a port out of range."""
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    if parts.path or parts.query or parts.fragment or "@" in parts.netloc:
        return None
    return parts.scheme, parts.hostname.lower(), port if port is not None else _DEFAULT_PORTS[parts.scheme]


def origin_allowed(origin: str | None, scheme: str, host: str | None) -> bool:
    """The Origin rule for a write: `origin` (the Origin header) is present and names the same scheme, host and port as
    the request itself, `scheme://host` (X-FF-Scheme and X-FF-Host, or their fallbacks)."""
    if not origin or not host:
        return False
    sent, own = _origin(origin.strip()), _origin(f"{scheme}://{host.strip()}")
    return sent is not None and sent == own


_LOCAL_NAME = re.compile(r"(?:[a-z0-9-]+\.)+local")


def host_allowed(host: str | None, allowed: Collection[str] = ()) -> bool:
    """The host rule (the proxy's, applied again by the store): the host part of `host` (a Host header's value: a
    name or an IP literal, IPv6 in brackets, then an optional port of digits) is an IP literal, `localhost`, a name
    ending `.local`, or one of `allowed`, compared without regard to case. An empty host, a port that is not ASCII
    digits (an empty one is allowed, as in a URL), and a trailing dot (which the proxy's rule does not take either) are
    refused."""
    text = (host or "").strip().lower()

    def port_ok(port: str) -> bool:
        return port == "" or (port.isascii() and port.isdigit())

    if text.startswith("["):
        name, bracket, rest = text[1:].partition("]")
        if not bracket or (rest and not (rest.startswith(":") and port_ok(rest[1:]))):
            return False
        try:
            return isinstance(ipaddress.ip_address(name), ipaddress.IPv6Address)
        except ValueError:
            return False
    if text.count(":") > 1:                      # an IPv6 literal without brackets carries no port
        try:
            ipaddress.ip_address(text)
            return True
        except ValueError:
            return False
    name, _, port = text.partition(":")
    if not name or not port_ok(port):
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    return name == "localhost" or _LOCAL_NAME.fullmatch(name) is not None or name in {a.lower() for a in allowed}


_NAME = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*")


def site_names(site_address: str | None) -> tuple[str, ...]:
    """The names a site address serves (the web server's SITE_ADDRESS: one address, or several separated by commas or
    spaces): each one's host, lower-cased, when it is a name (`example.net`, `example.net:8443`, `https://example.net`,
    `http://example.net:8080/`). An address that names no host (`:8080`), an IP address, a wildcard and nothing at all
    add none."""
    names: list[str] = []
    for part in re.split(r"[\s,]+", (site_address or "").strip().lower()):
        rest = part.split("://", 1)[1] if "://" in part else part
        rest = rest.split("/", 1)[0]
        if rest.startswith("[") or rest.count(":") > 1:          # an IPv6 address, with or without its port
            continue
        host = rest.partition(":")[0]
        try:
            ipaddress.ip_address(host)
            continue
        except ValueError:
            pass
        if _NAME.fullmatch(host) and host not in names:
            names.append(host)
    return tuple(names)


def check_answer(*, signed_in: bool, method: str | None, uri: str | None, accept: str | None,
                 fetch_mode: str | None) -> int:
    """/auth/check's status: 204 with a live session; else 302 to the sign-in page for a page load (a GET, outside
    /api/ and *.json, that navigates or asks for HTML); else 401. The forwarded path is judged decoded, lower-cased and
    with repeated slashes folded, so no spelling of an API path is taken for a page."""
    if signed_in:
        return 204
    if (method or "").upper() != "GET" or uri is None:
        return 401
    path = re.sub(r"/+", "/", unquote(uri.split("#", 1)[0].split("?", 1)[0])).lower()
    if path == "/api" or path.startswith("/api/") or path.endswith(".json"):
        return 401
    page_load = (fetch_mode or "").strip().lower() == "navigate" or "text/html" in (accept or "").lower()
    return 302 if page_load else 401


# ---------------------------------------------------------------- private files

def write_private(path: Path, data: bytes) -> None:
    """Replace `path` with `data` atomically: `<name>.tmp` beside it is created 0600 before a byte is written, synced,
    then renamed over the file, which so ends 0600 whatever it was before."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with contextlib.suppress(FileNotFoundError):
        os.unlink(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    os.close(fd)
    os.replace(tmp, path)

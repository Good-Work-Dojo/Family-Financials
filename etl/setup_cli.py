# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""`python -m etl init [--answers FILE] [--force | --feed]` — the setup conversation that turns an empty install
directory into a household: who it is, where the dashboard listens, the bank feed or the statement files, the optional
pages, a passcode. `./ff init` runs it as the invoking user, the one command but a refresh that may reach the network
(the bank feed's bridge, only when a setup token is given).

On a terminal it asks; with `--answers FILE` it reads a YAML file of the same answers (below) and asks nothing. It is
safe to run again: whatever is already on disk is kept and not asked again — household.yaml, the listen settings in
.env, accounts.yaml, a passcode, the bank feed's credential — so a run stopped part way finishes where it stopped.
`--force` asks again for household.yaml and accounts.yaml and replaces them, each copied first to <name>.previous
(0600); nothing else is ever overwritten.

`--feed` connects a bank feed to a household set up already and runs that step alone (`feed_step`): each feed account
accounts.yaml does not name becomes one of the household's accounts that no feed id names yet, or one whose feed ids
the feed no longer lists (`feed.accounts:` entry `{account: <its id>}`; it gets simplefin_id, its old one moved to
simplefin_id_history, and a simplefin_cutover when it has statement files and none yet), a new account, or is
skipped. The new lines are added to accounts.yaml in place, every other line kept (the file as it was is
accounts.yaml.previous); household.yaml is never touched.

The steps, each written as soon as it is answered:
  1. household.yaml: the household's name, the adults and children, the name for what is held jointly, the time
     zone, the Home buying pages on or off, and a random minute past the hour for the scheduled refresh.
  2. .env: this computer only (127.0.0.1) or the home network (0.0.0.0), the port, the invoking user's uid and gid.
  3. The bank feed: a setup token now, or later. The credential file is created empty, mode 0600, before the token
     is spent; the access URL is then written and synced into that same open file, Ctrl-C held until it is (a write
     that fails after the claim says the token is spent), and the feed's accounts are read in one fetch (the
     snapshot is kept: it is the household's first). Each feed account is given a kind and an owner, or skipped. A
     run stopped after the claim finds the credential and goes on at the accounts without asking for a token.
     `--offline` (what ./ff passes when the run has no network: the person said no bank feed now, or the answers
     file has no feed: block) asks for no token and takes none.
  4. Statement files already in imports/: each is read by the formats `import detect` tries, grouped by format and
     name pattern, and each group becomes an account (or one of the feed's accounts, which then gets a
     simplefin_cutover of the feed's earliest transaction day plus one), or is skipped. With neither, accounts.yaml
     holds no account yet.
  5. A passcode, asked twice without echo, labelled with the first adult's name.
  6. The starter rule files the config lacks, compose.yaml when the install has none, then `validate`.

The answers file is a SECRETS FILE: it may hold the passcode and the bank feed's setup token. It is refused when its
group or others may read it; keep it 0600 OUTSIDE the install directory (a backup would otherwise carry it; ./ff
mounts it read-only into the run) and delete it once the setup has run. Its keys:

  household:
    name: The Samples                 # required (unless household.yaml exists)
    adults: [Alex, Sam]               # required: at least one
    children: [Robin]                 # optional
    joint_label: Joint                # optional, default Joint
    timezone: America/New_York        # required: an IANA zone name
  modules: {home_buying: false}       # optional, default off
  listen: {network: false, port: 8080}   # optional: network true serves the home network
  feed:                               # optional: leave it out to use statement files only
    token: <the setup token>
    accounts:                         # every account the feed lists, by its feed id or its name
      ACT-0001: {kind: checking, owner: Joint}
      Sample Card 0000: {kind: credit-card, owner: Alex}
      ACT-0003: skip
      ACT-0004: {kind: loan, owner: Joint, mortgage: true}
  files:                              # every group of statement files in imports/, by its pattern or a file's name
    "sample_checking_*.csv": {kind: checking, owner: Joint, name: Sample Checking, institution: Sample Bank}
    "sample_card_*.csv": {account: ACT-0002}     # the feed account these files also hold
    "old_*.csv": skip
  passcode: <the passcode>            # required unless a passcode exists

Exit 0 when the config validates at the end, 1 when it does not (its lines printed), 2 on a refusal (one line), 130
when stopped at a prompt. The token, the access URL and the passcode are never printed, logged or put in argv."""
from __future__ import annotations

import contextlib
import datetime as dt
import getpass
import json
import os
import random
import re
import signal
import stat
import sys
import threading
import unicodedata
import zoneinfo
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import clock, import_cli, install
from . import simplefin as feed
from .config import Paths
from .install import Refused
from .models import ACCOUNT_KINDS

MISSING = object()
HOUSEHOLD_HEADER = ("# Who the household is, written by ./ff init. Every key is described in docs/configuration.md;\n"
                    "# ./ff validate checks this file after any change.\n")
ACCOUNTS_HEADER = ("# The household's accounts, written by ./ff init. Every key is described in docs/configuration.md;\n"
                   "# ./ff import detect <file> says which format reads a statement file, ./ff validate checks this file.\n")
KIND_WORDS = {"checking": "checking", "savings": "savings", "credit-card": "credit card", "brokerage": "brokerage",
              "loan": "loan"}
SKIP = "skip"
# the hints a feed account's name and institution give its kind, the first match winning
_KIND_HINTS = (
    ("credit-card", re.compile(r"\b(card|visa|mastercard|amex|american express|discover|credit)\b", re.I)),
    ("loan", re.compile(r"\b(loan|mortgage|heloc|lending)\b", re.I)),
    ("brokerage", re.compile(r"\b(brokerage|invest\w*|401\s?k?|403b|ira|roth|retire\w*|trading|stocks?|hsa)\b", re.I)),
    ("savings", re.compile(r"\b(savings?|money market|cd|certificate)\b", re.I)),
    ("checking", re.compile(r"\b(checking|chk|spending)\b", re.I)),
)
_MORTGAGE_HINT = re.compile(r"\b(mortgage|home loan)\b", re.I)
NETWORK_WARNING = ("Other devices then reach it over plain HTTP: the passcode and the sign-in cross your network "
                   "unencrypted unless you serve it under a domain of your own (SITE_ADDRESS in .env).")


class Stopped(Exception):
    """Ctrl-C or the end of input at a prompt."""


# ------------------------------------------------------------------ the conversation

class Conversation:
    """Each question answered from the answers file (`answers`, a mapping) or asked on the terminal. A reader turns
    what was typed or written into the value, raising ValueError with what is wrong; on the terminal the question is
    asked again, from a file it is a refusal naming the key."""

    def __init__(self, answers: dict | None, *, ask: Callable[[str], str] | None = None,
                 secret: Callable[[str], str] | None = None, out: Callable[[str], None] = print):
        self.answers = answers
        self.ask = ask or input
        self.secret_ask = secret or getpass.getpass
        self.out = out

    def given(self, key: str):
        node = self.answers
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return MISSING
            node = node[part]
        return node

    def value(self, key: str, question: str, read: Callable, *, default=MISSING, what: str = ""):
        if self.answers is not None:
            given = self.given(key)
            if given is MISSING or given is None:
                if default is not MISSING:
                    return default
                raise Refused(f"the answers file has no {key}: add it ({what})")
            try:
                return read(given)
            except ValueError as e:
                raise Refused(f"the answers file's {key}: {e}") from None
        while True:
            try:
                raw = self.ask(f"{question} ").strip()
            except (KeyboardInterrupt, EOFError):
                raise Stopped() from None
            if not raw and default is not MISSING:
                return default
            try:
                return read(raw)
            except ValueError as e:
                self.out(f"  {e}")

    def choice(self, key: str, question: str, options: list[tuple[object, str]], *, default=MISSING,
               what: str = ""):
        """One of `options` (value, words): by its number or its words on the terminal, by its value in a file."""
        def read(raw):
            if isinstance(raw, str) and raw.isdigit() and 1 <= int(raw) <= len(options):
                return options[int(raw) - 1][0]
            for value, words in options:
                if raw == value or (isinstance(raw, str) and raw.casefold() in (str(value).casefold(),
                                                                                 words.casefold())):
                    return value
            raise ValueError(f"choose one of: {', '.join(str(v) for v, _ in options)}")
        if self.answers is None:
            self.out(question)
            for i, (value, words) in enumerate(options, 1):
                mark = " (Enter)" if default is not MISSING and value == default else ""
                self.out(f"  {i}) {words}{mark}")
            question = "Your choice:"
        return self.value(key, question, read, default=default, what=what)

    def yes(self, key: str, question: str, *, default: bool) -> bool:
        def read(raw):
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str) and raw.casefold() in ("y", "yes", "true", "on"):
                return True
            if isinstance(raw, str) and raw.casefold() in ("n", "no", "false", "off"):
                return False
            raise ValueError("answer yes or no")
        return self.value(key, f"{question} [{'Y/n' if default else 'y/N'}]", read, default=default)

    def secret(self, key: str, question: str) -> str | None:
        """A secret typed without echo (None for an empty answer), or the file's value (None when it is absent)."""
        if self.answers is not None:
            given = self.given(key)
            if given is MISSING or given is None or given == "":
                return None
            if not isinstance(given, str):
                raise Refused(f"the answers file's {key}: expected text")
            return given.strip()
        try:
            typed = self.secret_ask(f"{question} ").strip()
        except (KeyboardInterrupt, EOFError):
            raise Stopped() from None
        return typed or None


def _text(raw, *, what: str) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"expected text, {what}")
    text = raw.strip()
    if not text:
        raise ValueError(f"it cannot be empty: {what}")
    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in text):
        raise ValueError("it holds a control character")
    if len(text) > 80:
        raise ValueError("keep it under 80 characters")
    return text


def _names(raw, *, least: int) -> list[str]:
    if isinstance(raw, str):
        items = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, list):
        items = [_text(item, what="a name") for item in raw]
    else:
        raise ValueError("expected names separated by commas, or a list")
    names = [_text(item, what="a name") for item in items]
    if len(names) < least:
        raise ValueError("give at least one name")
    seen: set[str] = set()
    for name in names:
        if name.casefold() in seen:
            raise ValueError(f"{name} is listed twice: give each person a name of their own")
        seen.add(name.casefold())
    return names


def _zone(raw) -> str:
    zone = _text(raw, what="such as America/New_York")
    if zone not in zoneinfo.available_timezones() or zone == "localtime":
        raise ValueError(f"{zone} is not a time zone name: write one such as America/New_York, Europe/Paris or UTC")
    return zone


def _port(raw) -> int:
    try:
        port = int(raw) if not isinstance(raw, bool) else None
    except (TypeError, ValueError):
        port = None
    if port is None or not 1024 <= port <= 65535:
        raise ValueError("a port is a whole number from 1024 to 65535, such as 8080")
    return port


def label_for(name: str) -> str:
    """A passcode label from a person's name: lowercase letters, digits, - and _ (accents dropped), else `family`."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    label = re.sub(r"[^a-z0-9_-]+", "", plain.replace(" ", "-"))[:32]
    return label or "family"


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "_", plain).strip("_")[:48] or "account"


# ------------------------------------------------------------------ the steps

def read_answers(file: str) -> dict:
    """The answers file: a YAML mapping that only its owner may read (it may hold a passcode and a setup token)."""
    path = Path(file)
    try:
        st = os.stat(path)
    except FileNotFoundError:
        raise Refused(f"{file}: no such file") from None
    if not stat.S_ISREG(st.st_mode):
        raise Refused(f"{file}: not a file")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise Refused(f"{file} may be read by others ({stat.S_IMODE(st.st_mode):04o}), and an answers file may hold "
                      f"a passcode and a setup token: make it yours alone with chmod 600 {file}, then run init again")
    try:
        doc = yaml.safe_load(path.read_text("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError):
        raise Refused(f"{file}: not a YAML file of answers") from None
    if not isinstance(doc, dict):
        raise Refused(f"{file}: not a mapping of answers (household, listen, feed, files, modules, passcode)")
    return doc


def _write_yaml(path: Path, header: str, doc: dict) -> None:
    text = header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False)
    install.write_atomic(path, text.encode("utf-8"), 0o644)


def keep_previous(path: Path, *, out) -> None:
    """A config file about to be replaced, copied first to <name>.previous (0600, over an older copy), said in one
    line; nothing when there is no such file."""
    if not path.is_file():
        return
    previous = path.with_name(f"{path.name}.previous")
    install.write_atomic(previous, path.read_bytes(), 0o600)
    out(f"{path.name} as it was is kept as {install.shown(previous)}")


def household_step(conv: Conversation, config_dir: Path, *, force: bool, out) -> dict:
    """household.yaml: kept when it is there (without --force), else asked and written."""
    path = config_dir / "household.yaml"
    if path.exists() and not force:
        try:
            doc = yaml.safe_load(path.read_text("utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError, OSError):
            doc = None
        if not isinstance(doc, dict):
            raise Refused(f"{install.shown(path)} is there but cannot be read: correct it (./ff validate says how), "
                          "or run init --force to answer its questions again")
        out(f"household.yaml is kept as it is ({install.shown(path)})")
        return doc
    if conv.answers is None:
        conv.out("About your household")
    name = conv.value("household.name", "Its name, shown on the dashboard (such as The Samples):",
                      lambda r: _text(r, what="the household's name"), what="the household's name")
    adults = conv.value("household.adults", "The adults' names, separated by commas:",
                        lambda r: _names(r, least=1), what="the adults' names")
    def children_of(raw):
        found = _names(raw, least=0)
        taken = {a.casefold() for a in adults}
        for child in found:
            if child.casefold() in taken:
                raise ValueError(f"{child} is named among the adults too: give each person one name")
        return found
    children = conv.value("household.children", "The children's names, separated by commas (Enter for none):",
                          children_of, default=[])
    def joint_of(raw):
        label = _text(raw, what="such as Joint")
        if label.casefold() in {p.casefold() for p in (*adults, *children)}:
            raise ValueError(f"{label} is a person's name: choose another word, such as Joint")
        return label
    joint = conv.value("household.joint_label", "What you call what you hold together [Joint]:", joint_of,
                       default="Joint")
    zone = conv.value("household.timezone", "Your time zone, by its name (such as America/New_York):", _zone,
                      what="an IANA zone name such as America/New_York")
    home_buying = conv.yes("modules.home_buying", "Turn on the Home buying pages?", default=False)
    doc = {
        "version": 1,
        "name": name,
        "members": [{"name": a, "role": "adult"} for a in adults] + [{"name": c, "role": "child"} for c in children],
        "joint_label": joint,
        "timezone": zone,
        "modules": {"home_buying": home_buying},
        # a minute of the household's own, so installs do not all reach the bank feed at once
        "refresh": {"minute": random.SystemRandom().randrange(60)},
    }
    keep_previous(path, out=out)
    _write_yaml(path, HOUSEHOLD_HEADER, doc)
    out(f"wrote {install.shown(path)}")
    return doc


def listen_step(conv: Conversation, root: Path, *, out) -> dict:
    """.env: where the dashboard listens (kept when the file sets it), the invoking user's ids, the images given."""
    current = install.read_env(root) or {}
    if current.get("FF_BIND") and current.get("FF_PORT"):
        out(f"where the dashboard listens is kept as .env sets it ({current['FF_BIND']}, port {current['FF_PORT']})")
        chosen = {}
    else:
        network = conv.choice("listen.network", "Who should reach the dashboard?",
                              [(False, "this computer only"), (True, "other devices on my home network")],
                              default=False)
        if network:
            out(NETWORK_WARNING)
        port = conv.value("listen.port", f"The port it listens on [{install.DEFAULT_PORT}]:", _port,
                          default=install.DEFAULT_PORT)
        chosen = {"FF_BIND": install.EVERYWHERE if network else install.LOOPBACK, "FF_PORT": str(port)}
    values = install.base_env(root, chosen)
    keep = {k: v for k, v in values.items() if k in chosen or not current.get(k)}
    if keep or not (root / install.ENV_FILE).exists():
        install.write_env(root, keep)
        out(f"wrote the settings to {install.shown(root / install.ENV_FILE)}")
    return {**current, **keep}


# ------------------------------------------------------------------ the bank feed

@dataclass(frozen=True)
class FeedPlaces:
    """Where the feed module reads and writes (the attributes its claim and fetch take from a config)."""
    simplefin_credential_path: Path
    simplefin_snapshots_dir: Path

    def find_simplefin_snapshots(self) -> list[Path]:
        d = self.simplefin_snapshots_dir
        return sorted(d.glob("accounts-*.json")) if d.is_dir() else []


@dataclass
class FeedAccount:
    id: str
    name: str
    institution: str
    balance: str
    first_day: dt.date | None
    kind: str
    mortgage: bool


def feed_places(root: Path) -> FeedPlaces:
    paths = Paths()
    store = paths.store_dir({})
    return FeedPlaces(paths.credential_path(store), store / "snapshots")


def claim_failure(error: SystemExit) -> str:
    """The plain words for a claim that did not give an access URL, and whether to try the token again."""
    cause = error.__cause__
    text = str(error)
    if getattr(cause, "code", None) == 403:
        return ("the bridge says this setup token was already claimed: make a new setup token at the bridge, "
                "then run init again")
    if text.startswith("Setup token is not valid base64") or text.startswith("Decoded claim URL looks wrong"):
        return "that is not a bank feed setup token: copy it again from the bridge, then run init again"
    return (f"the bank feed could not be connected ({text}): the setup token may still be unspent, so run init again "
            "with it; if the bridge then says it was already claimed, make a new one")


SPENT = ("the setup token was used, but its access could not be saved ({why}): the token cannot be used again; make "
         "a new setup token at the bank feed's provider, then run init again")


def _write_url(fd: int, data: bytes) -> None:
    """Every byte of `data` into the open credential file, then synced to the disk."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]
    os.fsync(fd)


@contextlib.contextmanager
def _ctrl_c_after():
    """Ctrl-C held until the block ends (in the main thread): the claim spends the token, and only the write that
    follows keeps what it was exchanged for, so the two finish together; a Ctrl-C pressed meanwhile is raised after."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    pressed = []

    def later(signum, frame):
        if not pressed:
            print("init: finishing the bank feed's connection first", file=sys.stderr, flush=True)
        pressed.append(signum)
    previous = signal.signal(signal.SIGINT, later)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
    if pressed:
        raise KeyboardInterrupt


def connect(places: FeedPlaces, token: str, *, out) -> Path:
    """Spend the setup token: the credential file is created empty and 0600 first (so a folder that cannot take it
    stops this before the token is spent), the token is claimed, and the access URL is written and synced into that
    same open file, with Ctrl-C held until it is. A claim that fails removes the empty file and says whether the token
    may still be used; a write that fails after the claim says the token is spent. Returns where the access is."""
    cred = places.simplefin_credential_path
    try:
        fd = os.open(cred, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError as e:
        raise Refused(f"the bank feed's credential cannot be created at {install.shown(cred)} "
                      f"({e.strerror}): nothing was spent; correct that folder, then run init again") from None
    saved = False
    try:
        with _ctrl_c_after():
            try:
                url = feed.exchange(token)
            except SystemExit as e:
                raise Refused(claim_failure(e)) from None
            try:
                _write_url(fd, url.encode("utf-8"))
            except OSError as e:
                raise Refused(SPENT.format(why=e.strerror or type(e).__name__)) from None
            saved = True
    finally:
        os.close(fd)
        if not saved:
            cred.unlink(missing_ok=True)
    out(f"the bank feed is connected: its access is stored in {install.shown(cred)} (mode 600)")
    return cred


def _hinted_kind(*texts: str) -> str:
    blob = " ".join(texts)
    for kind, pattern in _KIND_HINTS:
        if pattern.search(blob):
            return kind
    return "checking"


def read_feed(places: FeedPlaces, zone: str, *, out, fresh: bool = False) -> tuple[list[FeedAccount], dt.date]:
    """The feed's accounts from its newest snapshot, fetched once when none is stored yet or `fresh` asks for one (the
    module's own fetch: the full window a store without a snapshot asks for, kept as the household's first snapshot),
    and the day it was taken on the household's clock."""
    snaps = places.find_simplefin_snapshots()
    if snaps and not fresh:
        snap = snaps[-1]
        out("the bank feed's accounts are read from its newest snapshot (no new request)")
    else:
        try:
            snap = feed.fetch(places)
        except SystemExit as e:
            raise Refused(f"the bank feed is connected, but its accounts could not be read ({e}): run init again "
                          "later; the connection is kept, so no new setup token is needed") from None
        if snap is None:
            raise Refused("the bank feed is connected but lists no account yet: connect your banks at SimpleFIN "
                          "Bridge, then run init again; the connection is kept, so no new setup token is needed")
    try:
        doc = json.loads(snap.read_bytes())
    except (OSError, ValueError):
        raise Refused(f"the bank feed's snapshot {install.shown(snap)} cannot be read: move it aside and run init "
                      "again") from None
    tz = zoneinfo.ZoneInfo(zone)
    stamp = re.search(r"accounts-(\d{8}T\d{6}Z)", snap.name)
    taken = (dt.datetime.strptime(stamp.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC).astimezone(tz).date()
             if stamp else clock.wall_utc().astimezone(tz).date())
    connections = {c.get("conn_id"): c for c in doc.get("connections") or [] if isinstance(c, dict)}
    found = []
    for a in doc.get("accounts") or []:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str):
            continue
        org = a.get("org") if isinstance(a.get("org"), dict) else {}
        institution = (org.get("name") or (connections.get(a.get("conn_id")) or {}).get("name") or "Bank feed")
        name = str(a.get("name") or a["id"])
        posted = [t.get("posted") for t in a.get("transactions") or []
                  if isinstance(t, dict) and isinstance(t.get("posted"), int) and t.get("posted") > 0
                  and not t.get("pending")]
        first = dt.datetime.fromtimestamp(min(posted), dt.UTC).astimezone(tz).date() if posted else None
        found.append(FeedAccount(a["id"], name, str(institution), str(a.get("balance", "")), first,
                                 _hinted_kind(name, str(institution)), bool(_MORTGAGE_HINT.search(name))))
    return found, taken


# ------------------------------------------------------------------ statement files

@dataclass
class FileGroup:
    glob: str
    format: str
    kind: str                       # the account kind the format suggests
    institution: str | None
    files: list[str] = field(default_factory=list)
    days: list[str] = field(default_factory=list)


def _format_facts(name: str, own) -> tuple[str, str | None]:
    """The account kind a format suggests and the institution it names, if any."""
    from .importers import registry
    preset = own.get(name) or registry.presets().get(name)
    if preset is not None:
        return ("credit-card" if preset.kind == "card" else "checking"), (preset.institution or None)
    native = next((b for b in import_cli.NATIVE_BANKS if b.format == name), None)
    if native is not None and native.account == "credit card":
        return "credit-card", native.institution
    if native is not None and native.account == "bank account":
        return "checking", native.institution
    return "checking", None


def file_groups(imports: Path, *, out) -> list[FileGroup]:
    """The statement files in the folder (its top level, dot files aside), each read by every format `import detect`
    tries, grouped by the format that reads it and the name pattern `import map` suggests. A file no format reads, or
    several do, is said and left out."""
    own = import_cli.own_presets().presets
    groups: dict[tuple[str, str], FileGroup] = {}
    try:
        entries = sorted(p for p in imports.iterdir() if not p.name.startswith("."))
    except OSError:
        return []
    for path in entries:
        try:
            _, refused = import_cli.statement(str(path))
        except ValueError:
            continue
        if refused:
            out(f"  {path.name}: not read ({refused})")
            continue
        readers = [t for t in import_cli.tries(path, own) if t.txns]
        if len(readers) != 1:
            names = ", ".join(t.name for t in readers)
            out(f"  {path.name}: " + (f"read by several formats ({names}): ./ff import detect imports/{path.name} "
                                       "says which, then name it in accounts.yaml" if readers else
                                       f"no format reads it: ./ff import detect imports/{path.name} says why"))
            continue
        t = readers[0]
        pattern = import_cli.glob_for(path.name)
        kind, institution = _format_facts(t.name, own)
        group = groups.setdefault((t.name, pattern), FileGroup(pattern, t.name, kind, institution))
        group.files.append(path.name)
        group.days += [x.date for x in t.txns]
    return list(groups.values())


# ------------------------------------------------------------------ accounts.yaml

def _owners(household: dict) -> list[str]:
    members = household.get("members") if isinstance(household.get("members"), list) else []
    adults = [m["name"] for m in members if isinstance(m, dict) and m.get("role") == "adult" and m.get("name")]
    children = [m["name"] for m in members if isinstance(m, dict) and m.get("role") == "child" and m.get("name")]
    return [*adults, str(household.get("joint_label") or "Joint"), *children]


def _kind_options(*, skip: str | None) -> list[tuple[object, str]]:
    options: list[tuple[object, str]] = [(k, KIND_WORDS[k]) for k in ACCOUNT_KINDS]
    return options + ([(SKIP, skip)] if skip else [])


def _entry_choice(conv: Conversation, key: str, names: list[str]):
    """The answers file's choice for one feed account or file group: by any of `names`; MISSING when none."""
    block = conv.given(key)
    if not isinstance(block, dict):
        return MISSING
    for name in names:
        if name in block:
            return block[name]
    return MISSING


def _owner_of(raw, owners: list[str]) -> str:
    if isinstance(raw, str):
        for owner in owners:
            if raw.casefold() == owner.casefold():
                return owner
    raise ValueError(f"the owner is one of: {', '.join(owners)}")


def _kind_of(raw) -> str:
    if isinstance(raw, str):
        for kind, words in KIND_WORDS.items():
            if raw.casefold() in (kind, words):
                return kind
    raise ValueError(f"the kind is one of: {', '.join(ACCOUNT_KINDS)}")


class _Ids:
    """Account ids and names, each given once."""

    def __init__(self):
        self.ids: set[str] = set()
        self.names: set[str] = set()

    def id(self, text: str) -> str:
        base = slug(text)
        candidate, n = base, 2
        while candidate in self.ids:
            candidate, n = f"{base}_{n}", n + 1
        self.ids.add(candidate)
        return candidate

    def name(self, text: str) -> str:
        candidate, n = text, 2
        while candidate.casefold() in self.names:
            candidate, n = f"{text} ({n})", n + 1
        self.names.add(candidate.casefold())
        return candidate


def map_feed(conv: Conversation, accounts: list[FeedAccount], owners: list[str], joint: str, ids: _Ids, *,
             out, existing: dict[str, str] | None = None, attached: dict[str, str] | None = None,
             relinked: Collection[str] = (), mortgage_taken: bool = False) -> dict[str, tuple[str, dict]]:
    """Each feed account given a kind and an owner, or skipped: {feed id: (account id, accounts.yaml entry)}. With
    `existing` ({account id: name}, the household's accounts the feed reads under no id it lists), a feed account may
    instead be one of those, each taken once: `attached` gets {feed id: account id}. On a terminal "a new account" is
    the default answer, except while one of `relinked` (accounts whose feed id the feed no longer lists) is offered:
    then there is none, as such an account is likely the one the feed lists anew."""
    chosen: dict[str, tuple[str, dict]] = {}
    free = dict(existing or {})
    for i, a in enumerate(accounts, 1):
        key_names = [a.id, a.name]
        if conv.answers is not None:
            given = _entry_choice(conv, "feed.accounts", key_names)
            if given is MISSING:
                raise Refused(f"the answers file says nothing about the bank feed account {a.name} ({a.id}): add it "
                              "under feed.accounts as {kind: ..., owner: ...}"
                              + (", {account: <an account of yours>}" if existing is not None else "")
                              + " or skip; the bank feed stays connected, so run init again with it")
            if given == SKIP:
                continue
            if existing is not None and isinstance(given, dict) and "account" in given:
                target = str(given["account"])
                if target not in free:
                    raise Refused(f"the answers file's feed.accounts entry for {a.name}: {target} is not an account "
                                  "of yours that the bank feed does not read yet (or another feed account took it)")
                del free[target]
                if attached is not None:
                    attached[a.id] = target
                continue
            if not isinstance(given, dict):
                raise Refused(f"the answers file's feed.accounts entry for {a.name}: write {{kind: ..., owner: ...}} "
                              "or skip")
            try:
                kind, owner = _kind_of(given.get("kind")), _owner_of(given.get("owner"), owners)
            except ValueError as e:
                raise Refused(f"the answers file's feed.accounts entry for {a.name}: {e}") from None
            mortgage = bool(given.get("mortgage")) and kind == "loan"
        else:
            out(f"Bank feed account {i} of {len(accounts)}: {a.name} at {a.institution} (balance {a.balance})")
            if free:
                options = [*((aid, f"your {name} ({aid})" + (", which the bank feed listed under another id"
                                                              if aid in relinked else ""))
                             for aid, name in free.items()),
                           ("new", "a new account"), (SKIP, "skip this account")]
                picked = conv.choice("", "Which account is it?", options,
                                     default=MISSING if relinked and set(relinked) & free.keys() else "new")
                if picked == SKIP:
                    continue
                if picked != "new":
                    del free[picked]
                    if attached is not None:
                        attached[a.id] = picked
                    continue
            kind = conv.choice("", "What kind of account is it?", _kind_options(skip="skip this account"),
                               default=a.kind)
            if kind == SKIP:
                continue
            owner = conv.choice("", "Whose is it?", [(o, o) for o in owners], default=joint)
            mortgage = (kind == "loan" and not mortgage_taken
                        and conv.yes("", "Is it the home's mortgage?", default=a.mortgage))
        if mortgage and mortgage_taken:
            raise Refused(f"the answers file marks more than one mortgage ({a.name} too): one loan at most is the "
                          "home's mortgage")
        mortgage_taken = mortgage_taken or mortgage
        name = ids.name(a.name)
        entry = {"name": name, "institution": a.institution, "owner": owner, "kind": kind, "simplefin_id": a.id}
        if mortgage:
            entry["mortgage"] = True
        chosen[a.id] = (ids.id(name), entry)
    return chosen


def map_files(conv: Conversation, groups: list[FileGroup], feed_entries: dict[str, tuple[str, dict]],
              feed_accounts: list[FeedAccount], owners: list[str], joint: str, ids: _Ids, *,
              out) -> tuple[dict, dict]:
    """Each file group: an account of its own, the files of one of the feed's accounts (which then needs a cutover),
    or skipped. Returns the new accounts {id: entry} and {feed id: (format, glob)} for the feed's."""
    new: dict[str, dict] = {}
    attached: dict[str, tuple[str, str]] = {}
    by_feed = {a.id: a for a in feed_accounts}
    names = {fid: e["name"] for fid, (_, e) in feed_entries.items()}
    for g in groups:
        span = f"{min(g.days)} to {max(g.days)}" if g.days else "no rows"
        words = f"{len(g.files)} file{'s' if len(g.files) != 1 else ''}"
        free = [(fid, name) for fid, name in names.items() if fid not in attached]
        if conv.answers is not None:
            given = _entry_choice(conv, "files", [g.glob, *g.files])
            if given is MISSING:
                raise Refused(f"the answers file says nothing about the statement files {g.glob} ({words}, read as "
                              f"{g.format}): add files: {g.glob!r} as {{kind: ..., owner: ...}}, {{account: <a feed "
                              "account>}} or skip")
            if given == SKIP:
                continue
            if not isinstance(given, dict):
                raise Refused(f"the answers file's files entry for {g.glob}: write {{kind: ..., owner: ...}}, "
                              "{account: ...} or skip")
            if "account" in given:
                target = next((fid for fid, name in names.items()
                               if given["account"] in (fid, name, by_feed[fid].name)), None)
                if target is None or target in attached:
                    raise Refused(f"the answers file's files entry for {g.glob}: {given['account']!r} is not a bank "
                                  "feed account chosen above (or already holds files)")
                attached[target] = (g.format, g.glob)
                continue
            try:
                kind = _kind_of(given.get("kind", g.kind))
                owner = _owner_of(given.get("owner"), owners)
                name = _text(given.get("name", f"{g.institution or 'My bank'} {KIND_WORDS[kind]}"),
                             what="the account's name")
                institution = _text(given.get("institution", g.institution or "My bank"), what="the bank's name")
            except ValueError as e:
                raise Refused(f"the answers file's files entry for {g.glob}: {e}") from None
        else:
            out(f"Statement files {g.glob}: {words}, read as {g.format}, {span}")
            options = [*((fid, f"the bank feed's {name}") for fid, name in free), ("new", "an account of their own"),
                       (SKIP, "skip these files")]
            picked = conv.choice("", "Which account are they?", options, default="new")
            if picked == SKIP:
                continue
            if picked != "new":
                attached[picked] = (g.format, g.glob)
                continue
            kind = conv.choice("", "What kind of account?", _kind_options(skip=None), default=g.kind)
            owner = conv.choice("", "Whose is it?", [(o, o) for o in owners], default=joint)
            institution = conv.value("", f"The bank's name [{g.institution or 'My bank'}]:",
                                     lambda r: _text(r, what="the bank's name"), default=g.institution or "My bank")
            default_name = f"{institution} {KIND_WORDS[kind]}"
            name = conv.value("", f"The account's name [{default_name}]:",
                              lambda r: _text(r, what="the account's name"), default=default_name)
        new[ids.id(name)] = {"name": ids.name(name), "institution": institution, "owner": owner, "kind": kind,
                             "parser": g.format, "glob": g.glob}
    return new, attached


def accounts_step(conv: Conversation, root: Path, config_dir: Path, household: dict, *, force: bool, out,
                  offline: bool = False) -> None:
    """accounts.yaml: kept when it is there (without --force), else built from the bank feed and the files. Offline
    (the run has no network), no token is asked or taken, and a connection with no snapshot yet cannot be listed."""
    path = config_dir / "accounts.yaml"
    if path.exists() and not force:
        out(f"accounts.yaml is kept as it is ({install.shown(path)})")
        return
    places = feed_places(root)
    connected = connection(conv, places, offline=offline, out=out) is not None
    owners = _owners(household)
    joint = str(household.get("joint_label") or "Joint")
    ids = _Ids()
    feed_accounts: list[FeedAccount] = []
    entries: dict[str, tuple[str, dict]] = {}
    taken = None
    if connected:
        feed_accounts, taken = read_feed(places, str(household.get("timezone")), out=out)
        out(f"the bank feed lists {len(feed_accounts)} account{'s' if len(feed_accounts) != 1 else ''}")
        entries = map_feed(conv, feed_accounts, owners, joint, ids, out=out)
    imports = Paths().imports_dir({}) or root / "imports"
    groups = file_groups(imports, out=out)
    if not groups and conv.answers is None and not connected:
        if conv.yes("", f"Do you have statement files to import now? Put them in {install.shown(imports)} first",
                    default=False):
            groups = file_groups(imports, out=out)
            if not groups:
                out("no statement file was found there")
    new, attached = (map_files(conv, groups, entries, feed_accounts, owners, joint, ids, out=out) if groups
                     else ({}, {}))
    first_days = {a.id: a.first_day for a in feed_accounts}
    accounts: dict[str, dict] = {}
    for fid, (account_id, entry) in entries.items():
        if fid in attached:
            # the feed owns the account's rows from the day after its earliest one (the window's first day may be
            # partial); the statement files own the days before
            fmt, pattern = attached[fid]
            day = first_days.get(fid) or taken
            entry = {**entry, "parser": fmt, "glob": pattern,
                     "simplefin_cutover": (day + dt.timedelta(days=1)).isoformat()}
        accounts[account_id] = entry
    accounts.update(new)
    keep_previous(path, out=out)
    _write_yaml(path, ACCOUNTS_HEADER, {"version": 1, "accounts": accounts})
    out(f"wrote {install.shown(path)} with {len(accounts)} account{'s' if len(accounts) != 1 else ''}")
    if not accounts:
        out("no account yet: put statement files in imports/ (./ff import detect <file> says what each is) and name "
            "them in config/accounts.yaml, or connect a bank feed with ./ff init --feed")


def connection(conv: Conversation, places: FeedPlaces, *, offline: bool, out) -> bool | None:
    """The bank feed's connection: the stored one (False), one made now with a setup token (True), or None when there
    is none. An empty credential a claim left unfinished is removed first. Offline, no token is asked or taken, and a
    stored connection with no snapshot yet is refused (its accounts cannot be listed)."""
    cred = places.simplefin_credential_path
    if cred.is_file() and cred.stat().st_size == 0:
        cred.unlink()
        out("an earlier setup left an empty bank feed credential (its claim did not finish): it is removed")
    given_token = conv.answers is not None and conv.given("feed.token") not in (MISSING, None, "")
    if cred.exists():
        out("the bank feed is already connected: its accounts are listed again, and no setup token is needed")
        if given_token:
            out("the setup token in the answers file is not used: the bank feed is already connected")
        if offline and not places.find_simplefin_snapshots():
            raise Refused("listing the bank feed's accounts needs the network, which this run was not given: run "
                          "./ff init again and say yes to connecting the bank feed (or keep the feed: block in the "
                          "answers file)")
        return False
    if offline:
        if given_token:
            raise Refused("the answers file holds a setup token, but this run was not given the network: ./ff gives "
                          "init the network when the answers file has a feed: block")
        out("the bank feed is not connected now: to connect it later, run ./ff init --feed")
        return None
    token = conv.secret("feed.token", "Paste your bank feed's setup token, or press Enter to add it later:")
    if not token:
        return None
    connect(places, token, out=out)
    return True


# ------------------------------------------------------------------ --feed: a bank feed for a household set up already

def _read_doc(path: Path) -> tuple[str, dict]:
    """A config file's text and its mapping, or Refused saying what to do."""
    try:
        text = path.read_text("utf-8")
    except FileNotFoundError:
        raise Refused("there is no household to connect a bank feed to yet: run ./ff init first") from None
    except (OSError, UnicodeDecodeError):
        text = None
    try:
        doc = yaml.safe_load(text) if text is not None else None
    except yaml.YAMLError:
        doc = None
    if not isinstance(doc, dict):
        raise Refused(f"{install.shown(path)} cannot be read: correct it (./ff validate says how), then run ./ff init "
                      "--feed again")
    return text, doc


def _feed_ids(entry: dict) -> list[str]:
    history = entry.get("simplefin_id_history")
    return [str(x) for x in (entry.get("simplefin_id"), *(history if isinstance(history, list) else ())) if x]


def last_file_days(config_dir: Path, wanted: list[str]) -> dict[str, dt.date]:
    """The newest day of each `wanted` account's statement-file rows, as an ingest keeps them: its files read with
    their formats, each source's rows in its own days, a file read for another account left out. When the ETL cannot
    load the config, the data end the last export states for the account instead. An account with no such day is
    left out."""
    from .config import Config
    from .pipeline import format_parse, read_statement
    from .validate import ConfigError
    try:
        cfg = Config(config_dir)
    except ConfigError:
        return _exported_ends(wanted)
    readers = cfg.statement_file_readers()
    out: dict[str, dt.date] = {}
    for aid in wanted:
        sources = cfg.txn_sources(aid) if aid in cfg.accounts else []
        for k, source in enumerate(sources):
            start, end = source.since, sources[k + 1].since if k + 1 < len(sources) else None
            parse = format_parse(source.format, cfg.presets)
            for path in cfg.find_sources(source.glob):
                if readers.get(path, [aid])[0] != aid:
                    continue
                read = read_statement(parse, path, aid)[0]
                days = [t.date for t in read[0] if not (start and t.date < start) and not (end and t.date >= end)] \
                    if read is not None else []
                if days:
                    day = dt.date.fromisoformat(max(days))
                    out[aid] = max(out.get(aid, day), day)
    return out


def _exported_ends(wanted: list[str]) -> dict[str, dt.date]:
    """Each `wanted` account's `txn_data_end` in the last export's accounts.json, where it states one."""
    try:
        doc = json.loads((Paths().data_dir() / "exports" / "accounts.json").read_bytes())
        rows = {a["id"]: a.get("txn_data_end") for a in doc["accounts"] if isinstance(a, dict)}
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    out = {}
    for aid in wanted:
        try:
            out[aid] = dt.date.fromisoformat(rows[aid])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _source_problem(name: str, entry: dict, cutover: str) -> str | None:
    """The refusal's words when a cutover on `cutover` would leave one of the account's later sources no day of its
    own (validate refuses a `since` on or after the cutover), else None."""
    sources = entry.get("sources")
    for i, source in enumerate(sources[1:] if isinstance(sources, list) else [], 2):
        since = source.get("since") if isinstance(source, dict) else None
        if since is not None and str(since) >= cutover:
            return (f"{name}: the bank feed would own its days from {cutover}, but source {i} of its statement files "
                    f"starts on {since}, so that source would keep no row: give it an earlier since in accounts.yaml "
                    "or remove it, then run ./ff init --feed again; accounts.yaml is as it was, and the bank feed "
                    "stays connected")
    return None


def feed_step(conv: Conversation, root: Path, config_dir: Path, *, out, offline: bool = False) -> None:
    """`init --feed`: the bank feed connected to a household set up already (with a setup token, unless it is
    connected), and each feed account accounts.yaml does not name yet made one of the household's accounts that no
    feed id names or whose feed ids the feed no longer lists, a bank re-linked under a new id (which gets the feed's
    id, its old one moved to its simplefin_id_history, and when it has statement files a simplefin_cutover the day after the
    later of the feed's earliest transaction and the files' last row: refused, writing nothing, when a later source's
    since would fall on or after it), a new account, or skipped. household.yaml and every other entry are left as they
    are: the new lines are added to accounts.yaml where they belong, the file as it was kept as accounts.yaml.previous."""
    _, household = _read_doc(config_dir / "household.yaml")
    path = config_dir / "accounts.yaml"
    text, doc = _read_doc(path)
    if not isinstance(doc.get("accounts") or {}, dict):
        raise Refused(f"{install.shown(path)}: accounts is not a mapping of accounts: correct it (./ff validate says "
                      "how), then run ./ff init --feed again")
    registry = doc.get("accounts") or {}
    accounts = {str(aid): e for aid, e in registry.items() if isinstance(e, dict)}
    places = feed_places(root)
    made = connection(conv, places, offline=offline, out=out)
    if made is None:
        out("no bank feed was connected: accounts.yaml is as it was")
        return
    feed_accounts, taken = read_feed(places, str(household.get("timezone")), out=out, fresh=made)
    ignored = (doc.get("simplefin") or {}).get("ignore") if isinstance(doc.get("simplefin"), dict) else None
    known = {fid for e in accounts.values() for fid in _feed_ids(e)} | {str(x) for x in ignored or ()}
    unknown = [a for a in feed_accounts if a.id not in known]
    if not unknown:
        out("every account the bank feed lists is in accounts.yaml already: nothing to add")
        return
    out(f"the bank feed lists {len(unknown)} account{'s' if len(unknown) != 1 else ''} accounts.yaml does not name")
    ids = _Ids()
    ids.ids.update(str(aid) for aid in registry)
    ids.names.update(str(e.get("name", "")).casefold() for e in accounts.values())
    # the accounts a feed account may be: those no feed id names, and those whose feed ids the feed no longer lists (a
    # bank re-linked under a new id)
    listed = {a.id for a in feed_accounts}
    relinked = {aid for aid, e in accounts.items() if _feed_ids(e) and not listed & set(_feed_ids(e))}
    free = {aid: str(e.get("name") or aid) for aid, e in accounts.items() if not _feed_ids(e) or aid in relinked}
    attached: dict[str, str] = {}
    entries = map_feed(conv, unknown, _owners(household), str(household.get("joint_label") or "Joint"), ids,
                       out=out, existing=free, attached=attached, relinked=relinked,
                       mortgage_taken=any(e.get("mortgage") is True for e in accounts.values()))
    first_days = {a.id: a.first_day for a in feed_accounts}
    cut = {aid for aid in attached.values()
           if (accounts[aid].get("parser") or accounts[aid].get("glob") or accounts[aid].get("sources"))
           and not accounts[aid].get("simplefin_cutover") and not accounts[aid].get("simplefin_balances_only")}
    file_days = last_file_days(config_dir, sorted(cut)) if cut else {}
    adds: dict[str, dict] = {}
    for fid, aid in attached.items():
        entry, add = accounts[aid], {"simplefin_id": fid}
        if aid in relinked and entry.get("simplefin_id"):
            # its snapshots under the old id are still read, as the account's
            history = entry.get("simplefin_id_history")
            add["simplefin_id_history"] = [*(history if isinstance(history, list) else ()), entry["simplefin_id"]]
            out(f"{free[aid]}: the bank feed's new id for it is {fid}; the old one, {entry['simplefin_id']}, is kept in "
                "its simplefin_id_history")
        if aid in cut:
            # the files own the days before the cutover, the feed the days from it: the day after the later of the
            # feed's first day (its window's first day may be partial) and the files' last, so no row the files hold
            # is dropped
            day = first_days.get(fid) or taken
            add["simplefin_cutover"] = (max(day, file_days.get(aid, day)) + dt.timedelta(days=1)).isoformat()
            problem = _source_problem(free[aid], entry, add["simplefin_cutover"])
            if problem:
                raise Refused(problem)
            out(f"{free[aid]}: its statement files own the days before {add['simplefin_cutover']}, the bank feed the "
                "days from it")
        adds[aid] = add
    new = dict(entries.values())
    if not adds and not new:
        out("no bank feed account was added: accounts.yaml is as it was")
        return
    expected = yaml.safe_load(text)
    expected["accounts"] = expected.get("accounts") or {}
    for aid, add in adds.items():
        expected["accounts"][aid].update(add)
    expected["accounts"].update(new)
    merged = merged_accounts_text(text, adds, new)
    try:
        exact = merged is not None and yaml.safe_load(merged) == expected
    except yaml.YAMLError:
        exact = False
    keep_previous(path, out=out)
    if exact:
        install.write_atomic(path, merged.encode("utf-8"), stat.S_IMODE(path.stat().st_mode))
    else:
        _write_yaml(path, ACCOUNTS_HEADER, expected)
        out("accounts.yaml could not be added to line by line, so it was written again whole, its comments left out")
    out(f"accounts.yaml: the bank feed reads {len(adds)} of your accounts and {len(new)} new "
        f"one{'s' if len(new) != 1 else ''}; every other line is as it was")


def _content(line: str) -> bool:
    text = line.strip()
    return bool(text) and not text.startswith("#")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


_KEY = re.compile(r"""("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s#'"{\[\-?][^:#]*?)\s*:(?=\s|$)(.*)$""")
_HEAD = re.compile(r"accounts:(\s*\{\s*\})?\s*(#.*)?")


def _dumped(doc: dict, indent: int) -> list[str]:
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return [" " * indent + line + "\n" for line in text.splitlines()]


def merged_accounts_text(text: str, attach: dict[str, dict], append: dict[str, dict]) -> str | None:
    """accounts.yaml's text with `attach`'s keys added at the end of those block-style entries (a key the entry has
    already written where it stands, its old value's lines replaced) and `append`'s entries added at the end of the
    `accounts:` block, every other line as it was; None when the file is not in a shape this adds to line by line (a
    flow-style entry or registry, tabs, an entry not found). The caller checks that the result reads as the whole
    rewrite would."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    heads = [i for i, line in enumerate(lines) if _HEAD.fullmatch(line.rstrip("\n"))]
    if len(heads) != 1 or "\t" in text:
        return None
    h = heads[0]
    head = _HEAD.fullmatch(lines[h].rstrip("\n"))
    end = next((j for j in range(h + 1, len(lines)) if _content(lines[j]) and _indent(lines[j]) == 0), len(lines))
    body = [j for j in range(h + 1, end) if _content(lines[j])]
    if head.group(1) and body:
        return None
    n = _indent(lines[body[0]]) if body else 2
    children: dict[str, int] = {}
    for j in body:
        if _indent(lines[j]) < n:
            return None
        if _indent(lines[j]) == n:
            m = _KEY.fullmatch(lines[j].rstrip("\n")[n:])
            if m is None:
                return None
            try:
                children[str(yaml.safe_load(m.group(1)))] = j
            except yaml.YAMLError:
                return None
    inserts: dict[int, list[str]] = {}
    replace: dict[int, list[str]] = {}
    drop: set[int] = set()
    for aid, add in attach.items():
        j = children.get(aid)
        if j is None or _content(_KEY.fullmatch(lines[j].rstrip("\n")[n:]).group(2)):
            return None
        stop = next((k for k in body if k > j and _indent(lines[k]) <= n), end)
        inner = [k for k in body if j < k < stop]
        if not inner or _indent(lines[inner[0]]) <= n:
            return None
        m = _indent(lines[inner[0]])
        # the entry's own keys, each with its value's lines: the key's line and those below it up to the next key
        keys: dict[str, list[int]] = {}
        for k in inner:
            found = _KEY.fullmatch(lines[k].rstrip("\n")[m:]) if _indent(lines[k]) == m else None
            if found is not None:
                try:
                    keys[str(yaml.safe_load(found.group(1)))] = [k]
                except yaml.YAMLError:
                    return None
            elif _indent(lines[k]) < m or not keys:
                return None
            else:
                keys[next(reversed(keys))].append(k)
        new = {}
        for key, value in add.items():
            if key in keys:
                replace[keys[key][0]] = _dumped({key: value}, m)
                drop.update(keys[key][1:])
            else:
                new[key] = value
        if new:
            inserts.setdefault(inner[-1], []).extend(_dumped(new, m))
    if append:
        inserts.setdefault(body[-1] if body else h, []).extend(_dumped(append, n))
    out = []
    for i, line in enumerate(lines):
        if i == h and head.group(1):
            line = "accounts:" + (f"  {head.group(2)}" if head.group(2) else "") + "\n"
        if i in replace:
            out.extend(replace[i])
        elif i not in drop:
            out.append(line)
        out.extend(inserts.get(i, []))
    return "".join(out)


def passcode_step(conv: Conversation, root: Path, household: dict, *, out) -> None:
    """A passcode, unless one is set up already."""
    from . import passcode_cli
    path = Paths().secrets_web_dir() / passcode_cli.auth.PASSCODES_FILE
    if usable_passcodes(path):
        out("a passcode is already set up (./ff passcode add <name> adds another)")
        return
    owners = _owners(household)
    label = label_for(owners[0]) if owners else "family"
    old = os.umask(0o077)
    try:
        if conv.answers is not None:
            given = conv.given("passcode")
            if given is MISSING or given is None or given == "":
                raise Refused("the answers file has no passcode: add one (at least 8 characters, or 10 digits)")
            if not isinstance(given, str):
                raise Refused("the answers file's passcode: write it as text, in quotes")
            try:
                passcode_cli.add(label, given=given)
            except passcode_cli.Refused as e:
                raise Refused(f"the answers file's passcode: {e}") from None
            return
        out(f"Choose a passcode to sign in with: at least 8 characters, or 10 digits (it is labelled {label})")
        while True:
            try:
                passcode_cli.add(label)
                return
            except passcode_cli.Stopped:
                raise Stopped() from None
            except passcode_cli.Refused as e:
                out(f"  {e}")
    finally:
        os.umask(old)


def precheck(conv: Conversation) -> None:
    """What an answers file must hold that a later step would only find missing after the bank feed's token is spent:
    a usable passcode when none is set up."""
    from . import passcode_cli
    if usable_passcodes(Paths().secrets_web_dir() / passcode_cli.auth.PASSCODES_FILE):
        return
    given = conv.given("passcode")
    if given is MISSING or given is None or given == "":
        raise Refused("the answers file has no passcode: add one (at least 8 characters, or 10 digits)")
    problem = passcode_cli.auth.passcode_problem(given) if isinstance(given, str) else "write it as text, in quotes"
    if problem:
        raise Refused(f"the answers file's passcode: {problem}")


def usable_passcodes(path: Path) -> int:
    """How many usable lines the passcodes file holds (0 when there is none or it cannot be read)."""
    from . import passcode_cli
    try:
        return len(passcode_cli.auth.parse_file(path.read_bytes())[0])
    except OSError:
        return 0


def validate_now(*, out) -> int:
    """`validate` on what is on disk: 0 and its OK line, or 1 and its lines."""
    from .config import Config
    from .validate import ConfigError
    try:
        cfg = Config(check_paths=True)
    except ConfigError as e:
        for problem in e.problems:
            out(str(problem))
        return 1
    out(f"config OK ({len(cfg.files)} files, {len(cfg.accounts)} accounts)")
    return 0


def _holds_bytes(path: Path | None) -> bool:
    try:
        return path is not None and path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _said_saved(cred: Path | None, before: bool) -> None:
    """After a run stopped by Ctrl-C or a system error: when this run spent a setup token, where its access is."""
    if not before and _holds_bytes(cred):
        print(f"init: the setup token was used, and its access is saved in {install.shown(cred)}: run ./ff init "
              "again to go on, no new token is needed", file=sys.stderr)


def run(answers_file: str | None = None, *, force: bool = False, out: Callable[[str], None] = print,
        ask: Callable[[str], str] | None = None, secret: Callable[[str], str] | None = None,
        offline: bool = False, feed: bool = False) -> int:
    """`init`: the steps in order (with `feed`, the bank feed's step alone), then validate and what to do next."""
    cred, connected_before = None, False
    try:
        if feed and force:
            raise Refused("--feed connects a bank feed to the household as it is, and --force asks for the household "
                          "again: give one of them")
        root = install.home()
        cred = feed_places(root).simplefin_credential_path
        connected_before = _holds_bytes(cred)
        answers = read_answers(answers_file) if answers_file else None
        if answers is None and ask is None and not sys.stdin.isatty():
            raise Refused("there is no terminal to ask on: run ./ff init in a terminal, or give --answers FILE")
        conv = Conversation(answers, ask=ask, secret=secret, out=out)
        install.ensure_dirs(root, out=out)
        config_dir = Paths().config_dir
        if feed:
            feed_step(conv, root, config_dir, out=out, offline=offline)
            done = ("Done. The bank feed is read at the next refresh: ./ff refresh runs one now (./ff up first when "
                    "the dashboard is not running).")
        else:
            if answers is not None:
                precheck(conv)
            household = household_step(conv, config_dir, force=force, out=out)
            env = listen_step(conv, root, out=out)
            accounts_step(conv, root, config_dir, household, force=force, out=out, offline=offline)
            passcode_step(conv, root, household, out=out)
            install.copy_starters(config_dir, Paths().starter_dir(), out=out)
            install.ensure_compose(root, out=out)
            done = ("Done. Next: ./ff up starts the dashboard and builds its data; then open " + install.address(env)
                    + " and sign in with your passcode.")
    except Refused as e:
        print(f"init: {e}", file=sys.stderr)
        return 2
    except (Stopped, KeyboardInterrupt) as e:
        print(f"init: {e or 'stopped'}: what was answered is kept; run ./ff init again to go on", file=sys.stderr)
        _said_saved(cred, connected_before)
        return 130
    except OSError as e:
        print(f"init: {e.filename or ''} {e.strerror or type(e).__name__}".replace("  ", " ").strip(),
              file=sys.stderr)
        _said_saved(cred, connected_before)
        return 2
    if validate_now(out=out):
        out("The config has the problems above: correct them, then run ./ff validate")
        return 1
    out(done)
    return 0

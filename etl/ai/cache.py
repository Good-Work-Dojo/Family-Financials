# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The AI step's own files and the household's consent: the stored answers, the run log, the run lock and the check of
what a provider answered. Offline: nothing here opens a socket or reads the clock (a time is always passed in), so
the ingest may import it.

The store is `store/ai/` (config.Paths.ai_store_dir), a folder only the AI step writes, made mode 0700:

  vendor_categories.json  the answers (CACHE_NAME), mode 0600, written whole, link-proof and atomically:
                            {"version": 1, "policy": <policy version>, "provider": ..., "model": ...,
                             "entries": {<vendor key>: {"category": <name> | null, "subcategory": <name> | null,
                                                        "confidence": <0..1>, "asked_at": <UTC stamp>}}}
                          A null category is the model saying it does not know (it answered "none"): stored, so the
                          vendor is not asked again, and never applied. Every valid answer is stored with its
                          confidence; which are applied is the ingest's decision (household.yaml ai.confidence).
                          The header names the policy, provider and model the entries were asked under: a run under
                          another one starts the file afresh.
  runs.jsonl              the run log (RUNS_NAME), mode 0600: one JSON line per run that sent a request, written before
                          the request leaves (so a run is counted against the day's cap even when it never returns)
                          and completed with its outcome after. Each line: time, provider, model, policy, outcome,
                          the counts (sent, answered, dropped by reason, cached) and the vendor keys sent: the
                          household's own data on its own disk. Only counts ever reach a command's output. The log
                          keeps its newest LOG_KEEP_BYTES.
  run.lock                held (flock) for the whole of a run or a reset, so two never overlap; made mode 0600.

`check_answer` is the one gate between a provider's answer and the store: the answer must be a JSON object holding
`answers`, a list; each item is kept only when it is an object of exactly vendor, category, subcategory (or no
subcategory) and confidence, its vendor one of the keys sent (exactly), its (category, subcategory) a pair of the
answer set (or the category "none"), its confidence a number from 0 to 1, and it is the first item for its vendor.
Everything else is dropped and counted by reason; nothing it holds is ever printed or stored.

`consent` reads the family's prefs document (state/family/prefs.json, which the store writes and the AI step only
reads, link-proof) for `ai`: on only when `enabled` is true, `consent_policy` is the current policy version and
`consent_scope` the current scope (policy.consent_scope: who receives the names, which accounts are asked about).
`consented_policy` gives that version alone and `last_run` the newest run's counts, for the ingest's status file.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import errno
import fcntl
import json
import math
import os
import re
import stat
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .. import files
from . import policy as policy_mod

CACHE_NAME = "vendor_categories.json"
RUNS_NAME = "runs.jsonl"
LOCK_NAME = "run.lock"
VERSION = 1
FILE_MODE = 0o600
DIR_MODE = 0o700
LOG_KEEP_BYTES = 1024 * 1024
PREFS_LIMIT = 1024 * 1024

# the reasons an answer's item is dropped, in the order they are tested
DROP_REASONS = ("shape", "not_asked", "duplicate", "category", "confidence")
# a run log line's `outcome`: "asked" while the request is out, "answered" once its answers are stored, "not_stored"
# when they could not be, or the class a failed request ended in (providers.FAILURES; tests pin the two lists)
RUN_OUTCOMES = ("asked", "answered", "not_stored", "rejected_key", "rate_limited", "unavailable", "timed_out",
                "deadline", "refused", "malformed", "request_refused", "redirect", "failed")
NONE_CATEGORY = "none"

ENTRY_KEYS = frozenset({"category", "subcategory", "confidence", "asked_at"})
# a time as `stamp` writes it
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_ITEM_KEYS = frozenset({"vendor", "category", "subcategory", "confidence"})


class MalformedAnswer(ValueError):
    """The answer as a whole is not the object asked for: nothing of it is used."""


class StoreRefused(OSError):
    """The store folder, or a file in it, is something other than what the AI step writes there (a link, a FIFO, a
    folder at a file's name): nothing is read from it or written to it."""


@dataclass
class Cache:
    """The stored answers and the policy, provider and model they were asked under."""
    policy: int
    provider: str
    model: str
    entries: dict[str, dict] = field(default_factory=dict)

    def under(self, policy: int, provider: str, model: str) -> bool:
        return (self.policy, self.provider, self.model) == (policy, provider, model)


@dataclass
class Checked:
    """What check_answer kept (vendor key -> category, subcategory, confidence) and dropped (reason -> count)."""
    kept: dict[str, tuple[str | None, str | None, float]]
    dropped: Counter


# ------------------------------------------------------------------------------------------------- the answer check

def _pairs(answer_set: Iterable[tuple[str, Sequence[str]]]) -> frozenset[tuple[str, str | None]]:
    """The (category, subcategory) pairs an answer may name: each category with each of its subcategories, a category
    that lists none with no subcategory."""
    pairs: set[tuple[str, str | None]] = set()
    for category, subs in answer_set:
        if subs:
            pairs.update((category, s) for s in subs)
        else:
            pairs.add((category, None))
    return frozenset(pairs)


def _confidence(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and 0.0 <= value <= 1.0 else None


def check_answer(text: str, sent: Sequence[str], answer_set) -> Checked:
    """The items of a provider's answer `text` that may be stored (the module docstring says which), and the count
    of those dropped per reason. MalformedAnswer when the answer is not a JSON object holding a list `answers`."""
    try:
        doc = json.loads(text)
    except (TypeError, ValueError, RecursionError):
        raise MalformedAnswer("not JSON") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("answers"), list):
        raise MalformedAnswer("not an object holding a list of answers")
    asked = frozenset(sent)
    pairs = _pairs(answer_set)
    kept: dict[str, tuple[str | None, str | None, float]] = {}
    seen: set[str] = set()
    dropped: Counter = Counter()
    for item in doc["answers"]:
        if (not isinstance(item, dict) or not ({"vendor", "category", "confidence"} <= set(item) <= _ITEM_KEYS)
                or not isinstance(item["vendor"], str) or not isinstance(item["category"], str)
                or not isinstance(item.get("subcategory"), (str, type(None)))):
            dropped["shape"] += 1
            continue
        vendor, category, sub = item["vendor"], item["category"], item.get("subcategory")
        if vendor not in asked:
            dropped["not_asked"] += 1
            continue
        if vendor in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(vendor)
        if (category, sub) in pairs:
            pair: tuple[str | None, str | None] = (category, sub)
        elif category == NONE_CATEGORY and sub is None:
            pair = (None, None)
        else:
            dropped["category"] += 1
            continue
        confidence = _confidence(item["confidence"])
        if confidence is None:
            dropped["confidence"] += 1
            continue
        kept[vendor] = (*pair, confidence)
    return Checked(kept, dropped)


# --------------------------------------------------------------------------------------------------------- the store

def ready(store_dir: Path) -> Path:
    """The store folder, made (mode 0700) when absent. StoreRefused when it is a link or no folder at all: nothing is
    written through one."""
    store_dir = Path(store_dir)
    store_dir.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileExistsError):
        os.mkdir(store_dir, DIR_MODE)
    if not stat.S_ISDIR(os.lstat(store_dir).st_mode):
        raise StoreRefused(errno.EINVAL, "not a folder", str(store_dir))
    return store_dir


@contextlib.contextmanager
def locked(store_dir: Path):
    """The store's run lock, held for the block, or None when another run or reset holds it (never waited for)."""
    fd = files.open_lock(Path(store_dir) / LOCK_NAME, mode=FILE_MODE)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
            return
        yield fd
    finally:
        os.close(fd)


def _entry(value) -> dict | None:
    """A stored entry as it is kept, or None when it is not one."""
    if not isinstance(value, dict) or set(value) != ENTRY_KEYS:
        return None
    category, sub, at = value["category"], value["subcategory"], value["asked_at"]
    if not isinstance(category, (str, type(None))) or not isinstance(sub, (str, type(None))):
        return None
    if (category is None and sub is not None) or category == "" or sub == "" or not isinstance(at, str):
        return None
    confidence = _confidence(value["confidence"])
    if confidence is None:
        return None
    return {"category": category, "subcategory": sub, "confidence": confidence, "asked_at": at}


def read(path: Path) -> tuple[Cache | None, str | None]:
    """The stored answers at `path` (None when there are none) and a problem when the file could not be used (its
    entries that are not entries are left out, and said). Never raises: a missing file is no answers, a damaged one
    or a link, a FIFO or a folder at its name is no answers plus the problem."""
    try:
        raw = files.read_regular(path)
    except FileNotFoundError:
        return None, None
    except OSError as e:
        return None, f"the stored answers could not be read ({e.strerror or type(e).__name__})"
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None, "the stored answers are not readable JSON"
    if (not isinstance(doc, dict) or doc.get("version") != VERSION or isinstance(doc.get("policy"), bool)
            or not isinstance(doc.get("policy"), int) or not isinstance(doc.get("provider"), str)
            or not isinstance(doc.get("model"), str) or not isinstance(doc.get("entries"), dict)):
        return None, "the stored answers are not an answers file"
    entries = {}
    for key, value in doc["entries"].items():
        entry = _entry(value)
        if isinstance(key, str) and key and entry is not None:
            entries[key] = entry
    left_out = len(doc["entries"]) - len(entries)
    problem = f"{left_out} stored answers were not answers and are left out" if left_out else None
    return Cache(doc["policy"], doc["provider"], doc["model"], entries), problem


def serialise(cache: Cache) -> bytes:
    """The answers file's bytes: JSON, keys sorted, so the same answers give the same file."""
    doc = {"version": VERSION, "policy": cache.policy, "provider": cache.provider, "model": cache.model,
           "entries": cache.entries}
    return (json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def write(store_dir: Path, cache: Cache) -> None:
    """Write the answers file whole: link-proof, atomic, mode 0600."""
    files.write_atomic(Path(store_dir) / CACHE_NAME, serialise(cache), mode=FILE_MODE)


def forget(store_dir: Path) -> int:
    """Remove the answers file (a link at its name is removed, never followed); the run log is kept. The number of
    answers that were stored."""
    path = Path(store_dir) / CACHE_NAME
    cache, _ = read(path)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    return len(cache.entries) if cache else 0


def stamp(when: dt.datetime) -> str:
    """A UTC time as the store writes it: seconds, `Z`."""
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------------------------------------------- the run log

def _log_lines(store_dir: Path) -> list[bytes]:
    """The run log's lines. StoreRefused when something other than a regular file is at its name."""
    try:
        raw = files.read_regular(Path(store_dir) / RUNS_NAME)
    except FileNotFoundError:
        return []
    except OSError as e:
        raise StoreRefused(errno.EINVAL, e.strerror or files.NOT_REGULAR, str(Path(store_dir) / RUNS_NAME)) from e
    return [line for line in raw.split(b"\n") if line.strip()]


def runs_on(store_dir: Path, day: dt.date, zone: dt.tzinfo) -> int:
    """How many runs the log records on `day`, a day of the household's zone. A line that is not a run's is not
    counted. StoreRefused when the log cannot be read."""
    count = 0
    for line in _log_lines(store_dir):
        try:
            record = json.loads(line)
            when = dt.datetime.fromisoformat(record["time"])
        except (ValueError, TypeError, KeyError, RecursionError):
            continue
        if when.tzinfo is not None and when.astimezone(zone).date() == day:
            count += 1
    return count


def log_run(store_dir: Path, record: dict, *, replace_last: bool = False) -> None:
    """Add a run's line to the log (`replace_last`: in place of the last line, the same run's, now with its outcome),
    keeping its newest LOG_KEEP_BYTES; written whole, link-proof, atomic, mode 0600."""
    lines = _log_lines(store_dir)
    if replace_last and lines:
        lines.pop()
    lines.append(json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    kept: list[bytes] = []
    size = 0
    for line in reversed(lines):
        size += len(line) + 1
        if kept and size > LOG_KEEP_BYTES:
            break
        kept.append(line)
    files.write_atomic(Path(store_dir) / RUNS_NAME, b"".join(line + b"\n" for line in reversed(kept)),
                       mode=FILE_MODE)


# ---------------------------------------------------------------------------------------------------------- consent

# the keys of the prefs document's `ai` section (deploy/scenario_store.py AI_KEYS; tests/test_ai_providers.py pins the
# two equal)
CONSENT_KEYS = frozenset({"enabled", "consent_policy", "consent_scope", "consented_at"})

def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _prefs_ai(prefs_path: Path) -> dict | None:
    """The prefs document's `ai` section, or None when there is none, the document cannot be read (never through a
    link), or the section is not of the shape the family store keeps (deploy/scenario_store.py _check_ai) and the
    dashboard reads: exactly enabled (true or false), consent_policy (a whole number, never a bool, or null),
    consent_scope (a string of at most policy.SCOPE_MAX characters, or null) and consented_at (a string or null). So a
    hand-edited section reads the same everywhere."""
    try:
        doc = json.loads(files.read_regular(prefs_path, limit=PREFS_LIMIT).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    ai = doc.get("ai") if isinstance(doc, dict) else None
    if not isinstance(ai, dict) or set(ai) != CONSENT_KEYS or not isinstance(ai["enabled"], bool) \
            or not (ai["consent_policy"] is None or _whole(ai["consent_policy"])) \
            or not (ai["consent_scope"] is None or (isinstance(ai["consent_scope"], str)
                                                    and len(ai["consent_scope"]) <= policy_mod.SCOPE_MAX)) \
            or not (ai["consented_at"] is None or isinstance(ai["consented_at"], str)):
        return None
    return ai


def _policy_of(ai: dict | None) -> int | None:
    policy = ai.get("consent_policy") if ai is not None else None
    return policy if _whole(policy) else None


def consent(prefs_path: Path, policy_version: int, scope: str) -> str:
    """The household's AI consent from its prefs document: "on" (enabled, given to `policy_version` and `scope`),
    "stale" (enabled, given to another version or another scope: what is sent, or who receives it, or which accounts
    are asked about, has changed since) or "off" (not enabled, no document, or one that cannot be read or whose `ai`
    section is malformed). Never raises; never follows a link."""
    ai = _prefs_ai(prefs_path)
    if ai is None or ai.get("enabled") is not True:
        return "off"
    policy = _policy_of(ai)
    if policy is None:
        return "off"
    return "on" if policy == policy_version and ai["consent_scope"] == scope else "stale"


def consented_policy(prefs_path: Path) -> int | None:
    """The policy version the household last agreed to (the prefs' `ai.consent_policy`), switched on or not; None
    when it states none, or the document cannot be read. Never raises; never follows a link."""
    return _policy_of(_prefs_ai(prefs_path))


def last_run(store_dir: Path) -> dict | None:
    """The run log's newest run, as counts only: {at, outcome, sent, answered, cached}; None when the log is absent,
    holds no run, or is something other than a regular file. Never raises; never follows a link."""
    try:
        lines = _log_lines(store_dir)
    except OSError:
        return None
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(record, dict) or not isinstance(record.get("time"), str) \
                or not _STAMP.fullmatch(record["time"]) or not isinstance(record.get("outcome"), str):
            continue
        counts = {k: record.get(k) for k in ("sent", "answered", "cached")}
        if record["outcome"] not in RUN_OUTCOMES \
                or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in counts.values()):
            continue
        return {"at": record["time"], "outcome": record["outcome"], **counts}
    return None

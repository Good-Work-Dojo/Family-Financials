# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""`python -m etl ai preview|run|reset` — what the AI step would send, sending it, and forgetting its answers.

`preview` shows exactly what the AI step would send, and how much it keeps back, with no key and no network. It makes
its own offline pass of the pipeline over the live inputs (parse and enrich, as `python -m etl rules` does, with the
family's answers, learned rules and overrides on, so the candidates are the rows an ingest leaves uncategorized),
selects the candidates (etl/ai/candidates.py), withholds what is never sent and prints: a short header, the provider
and model the household's config names, the policy version, the number of vendors withheld for each reason, the
caps, and the request body itself (etl/ai/request.py `serialise` of `build_request`), byte for byte the body a run
would send for these vendors. A run sends at most `ai.caps.vendors_per_run` of them in one request, each request the
same body over a part of the same sorted list. Nothing is written anywhere and nothing is sent.

`run` asks the household's provider (etl/ai/providers.py, loaded only here) about those vendors and stores the
answers it may keep (etl/ai/cache.py). It sends nothing unless the household switched the AI step on in the app for
the current policy version (the family's prefs document, `ai`) and a key is stored (the AI secrets folder's api_key);
and nothing past its caps: at most `ai.caps.runs_per_day` requests on a day of the household's zone, counted from the
run log, each about at most `ai.caps.vendors_per_run` vendors not answered already (`refresh` asks about answered
ones again, the oldest answers first). One request per run, its body build_request's over the chosen vendors in the
preview's order. It prints counts, never a vendor; from the refresh (`from_refresh`) it prints nothing while the step
is off and one line otherwise. Its exit status: 0 when it stored answers or had nothing to do (off, a cap reached, no
vendor to ask, another run in progress), 3 when the provider rejected the key, 1 for any other failure (nothing is
stored then).

`reset` forgets the stored answers and keeps the run log.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import Paths
from ..models import Txn
from . import cache
from . import policy as policy_mod
from .candidates import candidates, withhold
from .request import (
    NOTHING_TO_CHOOSE,
    NothingToAsk,
    answer_set,
    build_request,
    serialise,
)

# how each withholding reason reads in the preview's table
REASON_WORDS = {
    "name": "hold a household name",
    "digits": "hold a number of three or more digits, or a date",
    "address": "look like an e-mail or web address",
    "length": f"are longer than {policy_mod.MAX_KEY_LENGTH} characters",
    "seen": "have fewer than {min_seen} uncategorized purchases",
}

# how each reason an answer's item is dropped reads in a run's output
DROP_WORDS = {
    "shape": "were not an answer of the shape asked for",
    "not_asked": "named a vendor that was not asked about",
    "duplicate": "answered a vendor a second time",
    "category": "named a category or subcategory outside the household's list",
    "confidence": "gave no confidence from 0 to 1",
}

# what a run prints when the AI step is off, by the consent's state
OFF_WORDS = {
    "off": "AI step is off: it is not switched on in the app. Nothing was sent.",
    "stale": "AI step is off: what it sends, the service it sends to or the accounts it asks about have changed "
             "since it was switched on; switch it on again in the app to agree to them. Nothing was sent.",
}

PREFS_NAME = "prefs.json"
EXIT_KEY_REJECTED = 3


def body_for(cfg, txns: list[Txn]) -> tuple[dict | None, dict[str, int], int]:
    """The request body for an enriched pipeline's rows (None when the answer set is empty: NothingToAsk), the
    vendors withheld per reason and the candidate rows' count."""
    found = candidates(cfg, txns)
    sendable, withheld = withhold(found, cfg)
    tokens = policy_mod.household_tokens(cfg)
    try:
        body = build_request(sendable, answer_set(cfg), policy_mod.CURRENT, tokens=tokens)
    except NothingToAsk:
        body = None
    return body, withheld, len(found)


def report(cfg, txns: list[Txn], *, out: Callable[[str], object] = print) -> int:
    """Print the preview for an enriched pipeline's rows (the module docstring says what). With an empty answer set
    it says so in one line and prints no request: none would be made."""
    body, withheld, rows = body_for(cfg, txns)
    ai = cfg.ai
    accounts = "every account" if ai.accounts == "all" else "the credit-card accounts"
    out("What the AI step would send. This command sends nothing and needs no key.")
    for note in getattr(cfg, "notes", ()):
        out(note)
    out(f"provider: {ai.provider}")
    out(f"model: {ai.model or 'none set'}")
    if ai.base_url:
        out(f"server: {ai.base_url}")
    out(f"policy version: {policy_mod.POLICY_VERSION}")
    out(f"uncategorized purchases asked about: {rows} on {accounts}")
    out(f"vendors that would be sent: {len(body['vendors']) if body is not None else 0}")
    out("vendors kept back, and why:")
    for reason in policy_mod.REASONS:
        words = REASON_WORDS[reason].format(min_seen=ai.min_seen)
        out(f"  {withheld[reason]:>5}  {words}")
    out(f"each run asks about at most {ai.vendors_per_run} vendors, at most {ai.runs_per_day} runs a day")
    if body is None:
        out(NOTHING_TO_CHOOSE)
        return 0
    out("These category names are sent too:")
    for entry in body["answer_set"]:
        subs = ", ".join(entry["subcategories"])
        out(f"  {entry['category']}" + (f": {subs}" if subs else ""))
    out("the request, exactly as it would be sent:")
    out(serialise(body))
    return 0


def _enriched(cfg) -> list[Txn]:
    """The rows of an offline pipeline pass over the live inputs (the pass `python -m etl rules` makes, imported when
    a command runs), without the stored answers applied: the step asks about the rows the household's own rules and
    answers leave to the fallback, answered before or not."""
    from ..rules_cli import parsed_pipeline

    p = parsed_pipeline(cfg)
    p.ai_apply = False
    p.enrich()
    return p.txns


def preview(cfg, *, out: Callable[[str], object] = print) -> int:
    """`python -m etl ai preview`: an offline pipeline pass over the live inputs, then `report`."""
    return report(cfg, _enriched(cfg), out=out)


# ------------------------------------------------------------------------------------------------------------- run

def prefs_path(cfg) -> Path:
    """The family's prefs document, beside its answers (both in the family documents' folder)."""
    return Path(cfg.answers_path).with_name(PREFS_NAME)


def choose(sendable: Sequence[str], known: dict[str, dict], limit: int, refresh: bool) -> list[str]:
    """The vendors a run asks about, in `sendable`'s order: at most `limit` of those not in `known` (the stored
    answers), and with `refresh`, after those, the answered ones, the oldest answer first."""
    order = [v for v in sendable if v not in known]
    if refresh:
        order += sorted((v for v in sendable if v in known), key=lambda v: (str(known[v].get("asked_at")), v))
    picked = set(order[:max(limit, 0)])
    return [v for v in sendable if v in picked]


def _zone(cfg) -> dt.tzinfo:
    try:
        return ZoneInfo(cfg.timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        return dt.timezone.utc


def _failure_line(outcome, providers) -> str:
    """The one line a failed request prints: its class in plain words, the HTTP status when there was one, never the
    provider's text."""
    words = {
        "rejected_key": "the provider rejected the key",
        "rate_limited": "the provider is limiting requests; the next run asks again",
        "unavailable": "the provider could not be reached or failed",
        "timed_out": f"the provider did not answer within {providers.TIMEOUT:g} seconds "
                     f"({providers.MAX_RETRIES + 1} attempts)",
        "deadline": f"the provider took longer than {providers.DEADLINE:g} seconds in all, so the run stopped waiting",
        "refused": "the model declined to answer",
        "malformed": "the provider's answer was not the list asked for",
        "request_refused": "the provider refused the request",
        "redirect": "the provider answered with a redirect, which is not followed",
        "failed": "the request failed",
    }[outcome.kind]
    if outcome.status is not None:
        words += f" (HTTP {outcome.status})"
    elif outcome.error is not None:
        words += f" ({outcome.error})"
    return f"AI step: {words}. Nothing was stored."


def run(cfg, *, refresh: bool = False, from_refresh: bool = False, now: dt.datetime | None = None,
        txns: list[Txn] | None = None, out: Callable[[str], object] = print) -> int:
    """`python -m etl ai run` (the module docstring says what). `now` (UTC) and `txns` (an enriched pass's rows) are
    for the tests; by default the clock and an offline pass over the live inputs."""
    # the networked module, loaded by a run alone
    from . import providers

    state = cache.consent(prefs_path(cfg), policy_mod.POLICY_VERSION, policy_mod.consent_scope(cfg.ai))
    if state != "on":
        if not from_refresh:
            out(OFF_WORDS[state])
        return 0
    paths = Paths(cfg.dir)
    key_path = paths.secrets_ai_dir() / providers.KEY_NAME
    try:
        key = providers.read_key(key_path)
    except providers.KeyRefused as e:
        out(f"AI step: the key file {key_path} is not used: {e}. Nothing was sent.")
        return 1
    if key is None:
        if not from_refresh:
            out("AI step is off: no key is stored. Nothing was sent.")
        return 0
    try:
        where = providers.endpoint(cfg.ai.provider, cfg.ai.base_url)
    except providers.EndpointRefused as e:
        out(f"AI step: {e}. Nothing was sent.")
        return 1
    store = paths.ai_store_dir()
    try:
        cache.ready(store)
    except OSError:
        out(f"AI step: the AI store {store} is not a folder this step can write. Nothing was sent.")
        return 1
    with cache.locked(store) as held:
        if held is None:
            out("AI step: another AI run is in progress. Nothing was sent.")
            return 0
        return _run_held(cfg, providers, key, where, store, refresh=refresh, from_refresh=from_refresh,
                         now=now or providers.current_time(), txns=txns, out=out)


def _run_held(cfg, providers, key: str, where, store: Path, *, refresh: bool, from_refresh: bool,
              now: dt.datetime, txns: list[Txn] | None, out: Callable[[str], object]) -> int:
    """A run, holding the store's lock: the cap, the vendors, the request, the answers."""
    ai = cfg.ai
    zone = _zone(cfg)
    try:
        today = cache.runs_on(store, now.astimezone(zone).date(), zone)
    except OSError:
        out("AI step: the run log cannot be read, so today's runs cannot be counted. Nothing was sent.")
        return 1
    if today >= ai.runs_per_day:
        out(f"AI step: {today} runs today already, the most a day allows ({ai.runs_per_day}). Nothing was sent.")
        return 0
    answers = answer_set(cfg)
    if not answers:
        out(f"AI step: {NOTHING_TO_CHOOSE} Nothing was sent.")
        return 0
    found = candidates(cfg, _enriched(cfg) if txns is None else txns)
    sendable, _ = withhold(found, cfg)
    stored, problem = cache.read(store / cache.CACHE_NAME)
    if problem is not None and not from_refresh:
        out(f"AI step: {problem}; a run stores its answers afresh.")
    current = stored if stored is not None and stored.under(policy_mod.POLICY_VERSION, ai.provider, ai.model) else None
    known = current.entries if current is not None else {}
    chosen = choose(sendable, known, ai.vendors_per_run, refresh)
    if not chosen:
        out("AI step: every vendor it may ask about is answered already. Nothing was sent." if sendable
            else "AI step: there is no vendor to ask about. Nothing was sent.")
        return 0
    try:
        body = build_request(chosen, answers, policy_mod.CURRENT, tokens=policy_mod.household_tokens(cfg))
    except NothingToAsk:
        out(f"AI step: {NOTHING_TO_CHOOSE} Nothing was sent.")
        return 0
    except ValueError:
        out("AI step: a name that may not be sent reached the request. Nothing was sent.")
        return 1
    # the household's data took a while to read: the switch is read again just before anything leaves, so a family
    # member who switched the step off meanwhile is heard (and no run is counted)
    state = cache.consent(prefs_path(cfg), policy_mod.POLICY_VERSION, policy_mod.consent_scope(ai))
    if state != "on":
        if not from_refresh:
            out(OFF_WORDS[state])
        return 0
    record = {"time": cache.stamp(now), "provider": ai.provider, "model": ai.model,
              "policy": policy_mod.POLICY_VERSION, "outcome": "asked", "sent": len(chosen), "answered": 0,
              "dropped": {}, "cached": 0, "vendors": list(chosen)}
    try:
        cache.log_run(store, record)
    except OSError:
        out("AI step: the run log cannot be written. Nothing was sent.")
        return 1

    outcome = providers.ask(ai.provider, ai.model, where, key, body)
    checked = None
    if outcome.kind == "answered":
        try:
            checked = cache.check_answer(outcome.text, chosen, answers)
        except cache.MalformedAnswer:
            outcome = providers.Outcome("malformed")
    if checked is None:
        _log_outcome(store, {**record, "outcome": outcome.kind, "status": outcome.status}, out)
        out(_failure_line(outcome, providers))
        return EXIT_KEY_REJECTED if outcome.kind == "rejected_key" else 1

    asked_at = cache.stamp(now)
    entries = dict(known)
    for vendor, (category, sub, confidence) in checked.kept.items():
        entries[vendor] = {"category": category, "subcategory": sub, "confidence": confidence, "asked_at": asked_at}
    dropped = {r: checked.dropped[r] for r in cache.DROP_REASONS if checked.dropped[r]}
    done = {**record, "answered": len(checked.kept) + sum(dropped.values()), "dropped": dropped}
    try:
        cache.write(store, cache.Cache(policy_mod.POLICY_VERSION, ai.provider, ai.model, entries))
    except OSError:
        _log_outcome(store, {**done, "outcome": "not_stored"}, out)
        out("AI step: the answers could not be stored. Nothing was stored.")
        return 1
    _log_outcome(store, {**done, "outcome": "answered", "cached": len(checked.kept)}, out)
    unanswered = len(chosen) - len(checked.kept)
    out(f"AI step: asked about {len(chosen)} vendors; {len(checked.kept)} answers stored, "
        f"{sum(dropped.values())} dropped, {unanswered} vendors left unanswered.")
    for reason, n in dropped.items() if not from_refresh else ():
        out(f"  {n:>5}  {DROP_WORDS[reason]}")
    return 0


def _log_outcome(store: Path, record: dict, out: Callable[[str], object]) -> None:
    """Complete the run's log line with its outcome; a failure to is said, and the line already written still counts
    the run."""
    try:
        cache.log_run(store, record, replace_last=True)
    except OSError:
        out("AI step: the run log could not be completed.")


# ----------------------------------------------------------------------------------------------------------- reset

def reset(store: Path, *, out: Callable[[str], object] = print) -> int:
    """`python -m etl ai reset`: forget the stored answers (the run log is kept)."""
    try:
        cache.ready(store)
    except OSError:
        out(f"AI step: the AI store {store} is not a folder this step can write. Nothing was changed.")
        return 1
    with cache.locked(store) as held:
        if held is None:
            out("AI step: an AI run is in progress; try again when it ends. Nothing was changed.")
            return 1
        try:
            n = cache.forget(store)
        except OSError:
            out("AI step: the stored answers could not be removed.")
            return 1
    out(f"AI step: {n} stored answers forgotten." if n else "AI step: no answers were stored.")
    return 0

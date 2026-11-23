# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Pipeline orchestration: parse -> enrich -> reconcile -> persist -> export."""
from __future__ import annotations

import bisect
import contextlib
import datetime as dt
import fcntl
import functools
import json
import os
import time
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from . import clock, db, exports, networth
from . import manual_assets as manual_assets_mod
from .config import Config, Source
from .dates import days_between, snapshot_fetched_at
from .enrich import ai as ai_mod
from .enrich import answers as answers_mod
from .enrich import learned as learned_mod
from .enrich import overrides as overrides_mod
from .enrich import recurring as recurring_mod
from .enrich import renames as renames_mod
from .enrich import splits as splits_mod
from .enrich import transfers as transfers_mod
from .enrich import vendor_registry
from .enrich.categorize import Categorizer, set_internal
from .enrich.income import IncomeIdentifier, detect_income_phases
from .enrich.vendors import VendorNormalizer
from .importers import registry
from .models import (
    ASSET_CLASSES,
    CASH_KINDS,
    CASHFLOW_KINDS,
    INTERNAL_TYPES,
    LIABILITY_CLASSES,
    RESERVED,
    SPEND_TYPES,
    AssetRow,
    BalanceObs,
    FileStats,
    Holding,
    OpenQuestion,
    QualityCheck,
    Txn,
)
from .money import fmt
from .parsers import simplefin as simplefin_parser
from .quality import reconcile

if TYPE_CHECKING:
    from .importers.csv_preset import Preset

# How far a SimpleFIN account's own balance-date may trail the newest balance anywhere
# before we say so. Healthy connections lag 0-2 days, now and then a few more; an
# aggregator connection stuck on "needs attention" keeps re-sending its last balance
# with the old date, for weeks when nothing flags it.
_SIMPLEFIN_BALANCE_STALE_DAYS = 5

# The statement formats of this release by name: a plain dict copied from the importers' registry when this module
# loads. Config takes these names, and the household's own presets (Config.presets), as the ones an account's
# `parser:` may name; format_parse looks a name up in the household's presets, then here, when a file is read, so a test
# patches a format here (monkeypatch.setitem) and the registry stays as it is.
TXN_PARSERS = registry.formats()

# The most file names the `source.unmatched` and `import.matched_twice` checks name (they count them all)
_UNMATCHED_NAMED = 5

# How many days either side of a source's start day the seam check looks for a purchase two formats date apart: a card
# purchase usually posts within a few days of the day it was made
_SEAM_DAYS = 5

# household.yaml member role -> the role assumptions.json household.people gives it
EXPORTED_ROLES = {"adult": "owner", "child": "kid"}

# The assumptions.yaml keys assumptions.json carries under `thresholds`, by name: the transfer-matching windows, the
# review thresholds and the cancelled subscriptions. The export names every other key it carries under its own name,
# so a key in neither place is not exported at all.
THRESHOLD_KEYS = ("match_window_bdays", "venmo_match_window_bdays", "venmo_review_threshold",
                  "uncategorized_review_threshold", "unknown_credit_review_threshold", "cancelled_subscriptions")


def format_parse(name: str, own: Mapping[str, Preset]) -> registry.Parse:
    """The parse of the statement format `name`: the household's own preset of that id (`own`, Config.presets), else
    TXN_PARSERS' entry, read at the call so a format a test patched in is found. A valid config's own presets never
    take a TXN_PARSERS name (validate refuses such an id), so the order decides nothing for it."""
    preset = own.get(name)
    return preset.parse if preset is not None else TXN_PARSERS[name]


def read_statement(parse: registry.Parse, path: Path,
                   account_id: str) -> tuple[tuple[list[Txn], FileStats, dict] | None, str | None, FileStats | None]:
    """One statement file read with a format: `((txns, stats, extra), None, None)` when the format reads it, else
    `(None, reason, stats)`: the file is not a plain file or is over the size cap (judged before it is opened), the
    parse raises, or it has a line the format could not read and no row at all (then `stats` holds what the parse
    counted; otherwise None)."""
    try:
        reason = registry.file_refusal(path)
        if reason is None:
            txns, stats, extra = parse(path, account_id)
    except Exception as e:              # any error a file provokes is that file's failure, never the ingest's
        reason = registry.refusal(e)
    if reason is not None:
        return None, reason, None
    if txns or stats.rows_parsed or not stats.skip_reasons.get(registry.UNPARSEABLE_ROW):
        return (txns, stats, extra), None, None
    return None, registry.NO_ROWS, stats


def assign_occurrence_seq(txns: list[Txn]) -> None:
    """uid disambiguator = nth occurrence of (date, amount, description) within
    one file — stable across exports that order rows differently, so cross-file
    dedup of overlapping exports works; identical same-day rows stay distinct."""
    occ: dict[tuple, int] = {}
    for t in txns:
        key = (t.date, t.amount_cents, t.description_raw)
        t.seq = occ.get(key, 0)
        occ[key] = t.seq + 1


def _no_file_matched(sources: list[Source]) -> str:
    """The `source.<account>` warning's words for the sources whose patterns match no file."""
    if len(sources) == 1:
        return f"no file matched glob {sources[0].glob!r}"
    return "no file matched globs " + ", ".join(repr(s.glob) for s in sources)


def _rows(n: int) -> str:
    return f"{n} row" if n == 1 else f"{n} rows"


def _sources_joined(sources: list[Source], kept: list[int], outside: list[int], unheld: int) -> str:
    """The `import.sources.<account>` words: for each source in order, its format, the rows it kept (stored) and the
    rows it left out as dated in another source's days; then either that the other source holds those days or, with
    `unheld` rows left out on days the source holding them has no file for (_unheld), that no source counts them and
    what to do. One source is said as one: it leaves nothing out. Counts only: no file, row or amount is named."""
    if len(sources) == 1:
        return f"Read from one source: {sources[0].format} kept {_rows(kept[0])}."
    each = "; ".join(f"{s.format} kept {_rows(k)} and left out {o}"
                     for s, k, o in zip(sources, kept, outside, strict=True))
    head = f"Read from {len(sources)} sources, each holding its own days: {each}."
    if not unheld:
        return f"{head} A row a source holds outside its own days is left out: the source before or after it holds that day."
    one = unheld == 1
    return (f"{head} {unheld} of the rows left out {'falls' if one else 'fall'} on days the source holding those days "
            f"has no file for, so no source counts {'it' if one else 'them'}: add the missing files, or move since "
            "(accounts.yaml sources).")


def _unheld(sources: list[Source], spans: list[tuple[str, str] | None], outside: list[list[tuple[str, int]]],
            feed_from: str | None) -> int:
    """How many rows a source left out fall on a day the source holding that day did not read: outside the span from
    the first to the last row date that source's files hold (all of their rows), or any day when it read none. Such a
    row is counted by no source, unless it is dated on or after `feed_from`, the cutover of a feed that reports the
    account, which holds those days."""
    starts = [s.since for s in sources[1:]]
    n = 0
    for rows in outside:
        for day, _ in rows:
            if feed_from is not None and day >= feed_from:
                continue
            span = spans[bisect.bisect_right(starts, day)]
            n += span is None or not span[0] <= day <= span[1]
    return n


def _seam(sources: list[Source], inside: list[list[tuple[str, int]]],
          outside: list[list[tuple[str, int]]]) -> list[tuple[str, int, int]]:
    """Where two sources meet, the rows their formats likely date apart: one format may date a purchase by the day it
    was made and the other by the day it posted, so a purchase made before a source's start day and posted on or after
    it is kept by both sources, or (the sources the other way round) by neither. At each start day, within _SEAM_DAYS
    either side, rows of one signed amount are paired one to one: first each row the earlier source keeps with a copy
    the later one leaves out, and each row the later one keeps with a copy the earlier one leaves out (the ordinary
    overlap, a copy on any day of that side); then what is left of the two sources' kept rows pairs as a likely double
    and what is left of their left-out rows as a likely loss. Returns (start day, doubles, losses) for each start day
    with either. `inside` holds the (date, cents) rows each source keeps in its own days (none the feed's cutover
    drops), `outside` the rows it holds outside them."""
    def near(rows: list[tuple[str, int]], start: str, before: bool) -> Counter:
        """The signed amounts of `rows` dated within _SEAM_DAYS before `start` (or on and after it), counted"""
        return Counter(cents for day, cents in rows
                       if (day < start) == before and abs(days_between(day, start)) <= _SEAM_DAYS)

    out = []
    for k in range(1, len(sources)):
        start = sources[k].since
        kept_before, left_after = near(inside[k - 1], start, True), near(outside[k - 1], start, False)
        left_before, kept_after = near(outside[k], start, True), near(inside[k], start, False)
        doubles = losses = 0
        for cents in kept_before | left_after | left_before | kept_after:
            doubles += min(max(0, kept_before[cents] - left_before[cents]), max(0, kept_after[cents] - left_after[cents]))
            losses += min(max(0, left_after[cents] - kept_after[cents]), max(0, left_before[cents] - kept_before[cents]))
        if doubles or losses:
            out.append((start, doubles, losses))
    return out


def _seam_words(found: list[tuple[str, int, int]]) -> str:
    """The `import.sources_seam.<account>` words: per start day, how many rows may be counted twice and how many
    nowhere, and what to do. Counts and days only: no amount and no description."""
    def counts(day: str, doubles: int, losses: int) -> str:
        parts = [f"{_rows(doubles)} that both sources keep may be counted twice" if doubles else "",
                 f"{_rows(losses)} that both leave out may be counted nowhere" if losses else ""]
        return f"around {day}, " + " and ".join(p for p in parts if p)
    days = "that day" if len(found) == 1 else "those days"
    return ("Where this account's sources meet, the two formats seem to date a purchase differently, the purchase day "
            f"in one and the posting day in the other: {'; '.join(counts(*f) for f in found)}. Look at this account's "
            f"rows around {days}, and move since to a day with no purchase in flight, or fix a row with an override "
            "(accounts.yaml sources, overrides.yaml).")


def categorizer_for(cfg: Config, learned: dict[str, dict] | None = None) -> Categorizer:
    """The one place a Categorizer is built from a Config — Pipeline.enrich and the offline
    `python -m etl rules` tooling both call it, so a future constructor argument cannot make the
    CLI's rulebook diverge from the ingest's. `learned` is learned.as_rules(doc) (precedence step
    4); the CLI passes none: it works on the bare rulebook."""
    account_tags = {a: m.get("tags") or [] for a, m in cfg.accounts.items()}   # `tags:` written with no value is none
    return Categorizer(cfg.category_rules, cfg.bank_category_map, cfg.vendor_rules,
                       cfg.assumptions, account_tags, owner_of=cfg.owner_of(), learned=learned)


class Pipeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.txns: list[Txn] = []
        self.asset_rows: list[AssetRow] = []
        self.holdings: list[Holding] = []
        # produced by later stages (declared here so no getattr guards needed)
        self.transfer_matches: list = []
        self.uncat_share: float = 0.0
        self.unmapped: list = []
        self.month_sums: dict = {}
        self.ledger_drift: dict[str, dict] = {}   # account_id -> ledger-drift payload (accounts.json)
        self.current_snapshot_date: str | None = None
        # net worth's per-account series map (etl/networth.py), built once in parse_assets; and per snapshot date
        # its coverage and the names of the series it misses (assets_timeseries.json `coverage`, `missing`)
        self.networth_series: networth.SeriesMap = {}
        self.snapshot_coverage: dict[str, tuple[float, list[str]]] = {}
        # the manual assets net worth counts (etl/manual_assets.py: the config's entries and the assets entered in the
        # app), set in parse_assets; accounts.json `manual_assets` and manifest.json setup.manual_assets
        self.manual_assets: list[manual_assets_mod.ManualAsset] = []
        # the config entries an app asset hides (manual_assets.overridden), accounts.json `manual_overridden`
        self.manual_overridden: list[manual_assets_mod.ManualAsset] = []
        self.file_stats: list[FileStats] = []
        # every statement file an account's glob matched, each once whichever accounts matched it (manifest.json
        # setup.import_files)
        self.import_files: set[Path] = set()
        # each statement file with the accounts that read it, the one it is read for first
        # (Config.statement_file_readers), set in parse_transactions
        self._readers: dict[Path, list[str]] = {}
        self.checks: list[QualityCheck] = []
        self.questions: list[OpenQuestion] = []
        self.latest_balances: dict[str, tuple[str, int]] = {}
        self.overlaps: dict[str, int] = {}
        self.windows: dict[str, tuple[str | None, str | None]] = {}
        self.mortgage_included: dict[str, bool] = {}
        self.income_phases: list[dict] = []
        self.recurring_rows: list[dict] = []
        # learned vendor rules: the doc enrich() derived, this pass's history events (stdout
        # only), and whether persist_learned() wrote the file
        self.learned_doc: dict | None = None
        self.learned_events: list[dict] = []
        self.learned_written = False
        # newest transaction date, never past the ingest day, set once at the end of enrich(): the "today" of every
        # data-relative window (recurring, vendors.json TTM)
        self.data_end: str | None = None
        self.acct_file_sums: dict[str, int] = {}
        self.acct_dedup_sums: dict[str, int] = {}
        # account id -> (the file rows parse_transactions dropped at its cutover, the newest file row's date)
        self.file_clips: dict[str, tuple[int, str]] = {}
        self.balance_obs: list[BalanceObs] = []
        self.simplefin_holding_accounts: set[str] = set()
        # account_ids that reported a balance in the NEWEST snapshot (= "live this run")
        self.simplefin_live_accounts: set[str] = set()
        # did we actually read a newest snapshot? distinguishes "the feed dropped this
        # account" from "there are no snapshots at all" (fresh clone / CSV-only era),
        # which must not fire a per-account staleness warn for every mapped account
        self.simplefin_snapshot_read = False
        # the stored answers the reader could not read (their file names): how old the data is skips them
        self.simplefin_unreadable: set[str] = set()
        # account_id -> (as_of, balance_cents), latest SimpleFIN balance across snapshots
        # (all kinds, unlike latest_balances: the file balances plus a cash account's newer feed one)
        self.simplefin_latest_bal: dict[str, tuple[str, int]] = {}
        # the export files this run wrote, by name, in the order written (manifest.json `files`)
        self.exported: list[str] = []
        # the AI step's stored answers (etl/enrich/ai.py): whether enrich applies them (`python -m etl ai`'s own pass
        # does not: it asks about the rows the household's rules leave to the fallback), what applying them did, and
        # ai_status.json, set at the end of enrich
        self.ai_apply = True
        self.ai_applied = ai_mod.Applied()
        self.ai_status: dict | None = None

    @functools.cached_property
    def ingested_at(self) -> dt.datetime:
        """The ingest's one read of the household's wall clock (household.yaml `timezone`), taken when a stage first
        needs it: manifest.json `generated_at`, and its day (ingest_day)."""
        return clock.now(self.cfg.timezone)

    @property
    def ingest_day(self) -> str:
        """The ingest day (the household's), which bounds every "today" the run derives from the data — data_end, the
        current snapshot with no account point — as the dashboard bounds its own by the same day."""
        return self.ingested_at.date().isoformat()

    # ------------------------------------------------------------------ txns

    def _feed_observed(self, wanted: set[str]) -> set[str]:
        """The accounts of `wanted` that the feed store's snapshots report a transaction or a balance for, read with
        the feed's own parser (each snapshot's rows are dropped once counted); the scan stops once every wanted account
        is seen."""
        cfg = self.cfg
        sfid_map = cfg.simplefin_map()
        seen: set[str] = set()
        if not wanted or not sfid_map:
            return seen
        cutovers = cfg.simplefin_cutovers()
        for snap in cfg.find_simplefin_snapshots():
            try:
                txns, balances, _, _, _, raw_min = simplefin_parser.parse_snapshot(
                    snap, sfid_map, cutovers, cfg.accounts, cfg.simplefin_tz, cfg.simplefin_ignore())
            except Exception:       # a stored answer the reader cannot read: parse_simplefin fails its check
                continue
            seen |= {t.account_id for t in txns} | {b.account_id for b in balances} | set(raw_min)
            if wanted <= seen:
                break
        return seen & wanted

    def parse_transactions(self) -> None:
        cutovers = self.cfg.simplefin_cutovers()
        # A file row on or after an account's cutover belongs to the feed and is dropped — but only once the feed has
        # delivered something for that account (a transaction or a balance): until then the files are all the data
        # there is, and clipping would silently lose those rows.
        observed = self._feed_observed({a for a in cutovers if self.cfg.txn_sources(a)})
        # a file two accounts' patterns match is read once, for the first account listed whose format reads it
        # (Config.statement_file_readers)
        self._readers = self.cfg.statement_file_readers()
        for account_id, meta in self.cfg.accounts.items():
            sources = self.cfg.txn_sources(account_id)
            if not sources:
                # SimpleFIN-only account (no CSV source); handled by parse_simplefin
                self.windows[account_id] = (None, None)
                continue
            joined = self.cfg.joins_sources(account_id)
            cutover = cutovers.get(account_id)
            files_of, unmatched = self._source_files(account_id, sources)
            if unmatched:
                self.checks.append(QualityCheck(
                    name=f"source.{account_id}", status="warn", detail=_no_file_matched(unmatched)))
            if not any(files_of):
                self.windows[account_id] = (None, None)
                continue
            seen_uids: set[str] = set()      # every uid stored for the account so far
            acct_txns: list[Txn] = []
            clip = cutover is not None and account_id in observed
            clipped = late = 0               # file rows on/after the cutover: dropped / all of them
            last_file_date = None            # the newest file row in its source's own days, the cutover's included
            balance_ignored = False          # a file stated a balance this account's kind does not take from a file
            chains: list[list] = []          # each file's running balances, followed for a checking or savings account
            # each bank transaction id's digest (OFX files) -> the different rows the account keeps under it, across
            # all its files: one id naming two of them is a row the bank changed, perhaps counted twice
            fitids: dict[str, set[tuple[str, int, str]]] = {}
            long_cut = 0                     # rows stored with their description cut (registry.bounded_description)
            kept = [0] * len(sources)        # per source: the rows stored, and the rows dated outside its own days
            outside = [0] * len(sources)
            # with sources, per source: the rows it keeps in its own days (a row the cutover drops is the feed's, not
            # among them) and its rows outside its days, by uid (a row two files repeat is one), as (date, cents), and
            # the first and last row date its files hold: what the join's checks compare
            rows_in: list[dict[str, tuple[str, int]]] = [{} for _ in sources]
            rows_out: list[dict[str, tuple[str, int]]] = [{} for _ in sources]
            spans: list[tuple[str, str] | None] = [None] * len(sources)
            # an account with files reconciles even when none of them could be read: nothing read, nothing stored
            self.acct_file_sums.setdefault(account_id, 0)
            for k, (source, files) in enumerate(zip(sources, files_of, strict=True)):
                parser = format_parse(source.format, self.cfg.presets)
                # a source holds the days from its own start day up to the next source's: rows outside them are the
                # other sources' (the short form's one source holds every day)
                start, end = source.since, sources[k + 1].since if k + 1 < len(sources) else None
                for path in files:
                    read = self._read_file(parser, source.format, path, account_id, in_sources=joined)
                    if read is None:
                        continue
                    txns, stats, extra = read
                    self.file_stats.append(stats)
                    # the occurrence index counts the whole file, as a format alone reads it, so a row's uid does not
                    # depend on which source holds its day
                    assign_occurrence_seq(txns)
                    self.acct_file_sums[account_id] += stats.sum_cents
                    dups = 0
                    left_out: set[tuple[str, int, str]] = set()     # rows of the file the account does not keep
                    cut = extra.get("cut_descriptions", frozenset())
                    if joined and txns:
                        first, last = min(t.date for t in txns), max(t.date for t in txns)
                        spans[k] = (min(first, spans[k][0]), max(last, spans[k][1])) if spans[k] else (first, last)
                    for i, t in enumerate(txns):
                        if (start and t.date < start) or (end and t.date >= end):
                            # another source holds this day: removed before store, so reconciliation stays balanced
                            outside[k] += 1
                            self.acct_dedup_sums[account_id] = (
                                self.acct_dedup_sums.get(account_id, 0) + t.amount_cents)
                            left_out.add((t.date, t.amount_cents, t.description_raw))
                            if joined:
                                rows_out[k][t.uid] = (t.date, t.amount_cents)
                            continue
                        last_file_date = max(last_file_date or t.date, t.date)
                        if cutover and t.date >= cutover:
                            late += 1
                        if clip and t.date >= cutover:
                            # frozen CSV archive ends at the cutover; SimpleFIN takes over.
                            # treat as removed-before-store so reconciliation stays balanced
                            clipped += 1
                            self.acct_dedup_sums[account_id] = (
                                self.acct_dedup_sums.get(account_id, 0) + t.amount_cents)
                            left_out.add((t.date, t.amount_cents, t.description_raw))
                            continue
                        if joined:
                            # a row the source keeps, as the seam check counts it: the cutover's rows are the feed's
                            rows_in[k][t.uid] = (t.date, t.amount_cents)
                        if t.uid in seen_uids:
                            # an overlapping re-export repeats the row: stored once
                            dups += 1
                            self.acct_dedup_sums[account_id] = (
                                self.acct_dedup_sums.get(account_id, 0) + t.amount_cents)
                            continue
                        seen_uids.add(t.uid)
                        acct_txns.append(t)
                        kept[k] += 1
                        long_cut += i in cut
                    if dups:
                        self.overlaps[path.name] = dups
                    # a file's balance, its statement totals and its running balances are judged on all of its rows,
                    # whichever source holds their days
                    balance = extra.get("latest_balance")
                    if balance and meta["kind"] not in CASH_KINDS:
                        # a card's, a loan's or a brokerage's file balance has its own sign and meaning: only a
                        # checking or savings account takes its balance from a file
                        balance_ignored = True
                    elif balance:
                        prev = self.latest_balances.get(account_id)
                        if prev is None or balance[0] > prev[0]:
                            self.latest_balances[account_id] = balance
                    if extra.get("summary"):
                        self.checks.extend(reconcile.statement_summary_check(
                            account_id, extra["summary"], txns))
                    if extra.get("running_balances") is not None and meta["kind"] in CASH_KINDS:
                        chains.append(extra["running_balances"])
                    # a row with its date, amount and description in two files is one row (its uid stores it once)
                    for digest, named in extra.get("fitid_rows", {}).items():
                        if named - left_out:
                            fitids.setdefault(digest, set()).update(named - left_out)
            if chains:
                self.checks.append(reconcile.rollforward_check(account_id, chains))
            shared = sum(len(named) > 1 for named in fitids.values())
            if shared:
                # every such row is kept: the words give the count, never an id or a file's name
                self.checks.append(QualityCheck(
                    name=f"import.ofx_conflict.{account_id}", status="warn",
                    detail=f"{shared} of the bank's transaction ids in this account's statement files "
                           f"{'names' if shared == 1 else 'each name'} more than one row (another date, amount or "
                           f"description). Every such row is kept, so a transaction the bank corrected in a later "
                           f"download may be counted twice: look at those rows on this account's page, and keep the "
                           f"stale one out of spending and income with an override that sets its type to transfer "
                           f"(overrides.yaml)."))
            if long_cut:
                # the rows are kept and counted, never named: the words give how many
                self.checks.append(QualityCheck(
                    name=f"import.long_description.{account_id}", status="info",
                    detail=f"{_rows(long_cut)} of this account's statement files "
                           f"{'has' if long_cut == 1 else 'have'} a description longer than "
                           f"{registry.MAX_DESCRIPTION} characters, stored cut to its first {registry.MAX_DESCRIPTION}."))
            if balance_ignored:
                self.checks.append(QualityCheck(
                    name=f"import.balance_ignored.{account_id}", status="info",
                    detail="This account's statement files state a balance, which is not counted: a balance is read "
                           "from a file only for a checking or savings account. This account's balance comes from "
                           "the bank feed when it has one."))
            if clipped:
                self.checks.append(QualityCheck(
                    name=f"simplefin.csv_clip.{account_id}", status="info",
                    detail=f"{clipped} file rows on/after cutover {cutover} dropped "
                           f"(SimpleFIN owns that window)"))
                self.file_clips[account_id] = (clipped, last_file_date)
            elif late:
                self.checks.append(QualityCheck(
                    name=f"simplefin.csv_clip.{account_id}", status="info",
                    detail=f"nothing clipped: {late} file row(s) on/after cutover {cutover} kept, as the feed has no "
                           "data for this account yet"))
            if joined:
                inside, left = [list(r.values()) for r in rows_in], [list(r.values()) for r in rows_out]
                unheld = _unheld(sources, spans, left, cutover if clip else None)
                self.checks.append(QualityCheck(
                    name=f"import.sources.{account_id}", status="warn" if unheld else "info",
                    detail=_sources_joined(sources, kept, outside, unheld)))
                seam = _seam(sources, inside, left)
                if seam:
                    # the join stays a clip by date: which rows are one purchase is never guessed, only pointed at
                    self.checks.append(QualityCheck(
                        name=f"import.sources_seam.{account_id}", status="warn", detail=_seam_words(seam)))
            self.txns.extend(acct_txns)
            dates = sorted(t.date for t in acct_txns)
            self.windows[account_id] = (dates[0], dates[-1]) if dates else (None, None)
        self._unmatched_check()

    def _source_files(self, account_id: str, sources: list[Source]) -> tuple[list[list[Path]], list[Source]]:
        """Each of the account's sources' statement files (Config.find_sources, in source order), and the sources whose
        pattern matches no file. Every file found is one of the run's statement files (`import_files`). A file the
        patterns of two sources match is read once, as the first one's (its rows may then sit with the wrong source):
        such files fail one check for the account, `import.matched_twice.<account>`, counted and the first few named.
        A file read for an account listed earlier (`_readers`) is left out: when this account's format reads it too,
        such files fail one check for this account, `import.matched_by_accounts.<account>`, counted and the first few
        named with the account that reads each; a file this account's format refuses is left out with no check."""
        files_of: list[list[Path]] = []
        unmatched: list[Source] = []
        claimed: set[Path] = set()
        twice: set[str] = set()
        taken: dict[str, str] = {}          # a file another account reads, by its name in the folder -> that account
        for source in sources:
            found = self.cfg.find_sources(source.glob)
            if not found:
                unmatched.append(source)
            for p in found:
                readers = self._readers.get(p, [account_id])
                if readers[0] != account_id and account_id in readers:
                    taken[self._in_folder(p)] = readers[0]
            found = [p for p in found if self._readers.get(p, [account_id])[0] == account_id]
            files_of.append([p for p in found if p not in claimed])
            twice.update(self._in_folder(p) for p in found if p in claimed)
            claimed.update(found)
        if taken:
            names = sorted(taken)
            n = len(names)
            shown = ", ".join(f"{f} ({self.cfg.accounts[taken[f]]['name']})" for f in names[:_UNMATCHED_NAMED])
            more = f", and {n - _UNMATCHED_NAMED} more" if n > _UNMATCHED_NAMED else ""
            key = "sources" if self.cfg.joins_sources(account_id) else "glob"
            self.checks.append(QualityCheck(
                name=f"import.matched_by_accounts.{account_id}", status="fail",
                detail=f"{n} file{'s' if n != 1 else ''} this account's file pattern matches "
                       f"{'are' if n != 1 else 'is'} read for another account: {shown}{more}. A statement file is "
                       f"read for one account only, the first listed whose pattern matches it and whose format reads "
                       f"it, so this account leaves "
                       f"{'them' if n != 1 else 'it'} out: give each account a file pattern that matches only its own "
                       f"files (accounts.yaml {key})."))
        if twice:
            names = sorted(twice)
            n = len(names)
            more = f", and {n - _UNMATCHED_NAMED} more" if n > _UNMATCHED_NAMED else ""
            self.checks.append(QualityCheck(
                name=f"import.matched_twice.{account_id}", status="fail",
                detail=f"{n} file{'s' if n != 1 else ''} {'match' if n != 1 else 'matches'} the file patterns of two of "
                       f"this account's sources: {', '.join(names[:_UNMATCHED_NAMED])}{more}. Each is read once, as "
                       f"the first source's, so its rows may sit with the wrong source: give each source a pattern "
                       f"that matches only its own files (accounts.yaml sources)."))
        self.import_files.update(claimed)
        return files_of, unmatched

    def _in_folder(self, path: Path) -> str:
        """A statement file as a check names it: its path in the statements folder, so two files of one name in two
        folders are told apart (its name alone for a file outside the folder)"""
        try:
            return path.relative_to(self.cfg.source_dir).as_posix()
        except (TypeError, ValueError):
            return path.name

    def _read_file(self, parse: registry.Parse, fmt_name: str, path: Path, account_id: str, *,
                   in_sources: bool = False) -> tuple[list[Txn], FileStats, dict] | None:
        """One statement file read with its account's format: `(txns, stats, extra)`, the stats naming the account.
        None when the file cannot be read: it is not a plain file (a folder, a pipe, a device, or a link to one: opening a
        pipe would hold the ingest) or it is over the size cap, both judged before it is opened; the parse raises; or it
        has a line the format could not read and no row at all. Then one failing check `import.<account>.<file>`, the
        file named by its path in the statements folder (_in_folder), says what the file looked like in plain words (never the error's own text) and which format read it (the
        account's `parser:`, or with `in_sources` its source's `format:`), the stats of a file the parse did count are
        kept, and the file adds no row, no sum and no balance."""
        read, reason, counted = read_statement(parse, path, account_id)
        if read is not None:
            read[1].account = account_id
            return read
        if counted is not None:
            counted.account = account_id
            self.file_stats.append(counted)
        named =(f"its source names (accounts.yaml format: {fmt_name})" if in_sources
                 else f"the account names (accounts.yaml parser: {fmt_name})")
        self.checks.append(QualityCheck(
            name=f"import.{account_id}.{self._in_folder(path)}", status="fail",
            detail=f"Not read: {reason}. Check that it is this account's own export, in the format {named}."))
        return None

    def _unmatched_check(self) -> None:
        """`source.unmatched` (info): the files no account's glob matched, in the statements folder and in each folder
        an account's glob looks in (Config.unmatched_sources), counted, the first few named; no check when there are
        none."""
        names = self.cfg.unmatched_sources(self.import_files)
        if not names:
            return
        n = len(names)
        shown = ", ".join(names[:_UNMATCHED_NAMED])
        more = f", and {n - _UNMATCHED_NAMED} more" if n > _UNMATCHED_NAMED else ""
        self.checks.append(QualityCheck(
            name="source.unmatched", status="info",
            detail=f"{n} file{'s' if n != 1 else ''} in the statements folder {'are' if n != 1 else 'is'} read by no "
                   f"account: {shown}{more}. A statement file is read once its account's file pattern matches it "
                   "(accounts.yaml glob)."))

    def parse_simplefin(self) -> None:
        """Read all durable SimpleFIN snapshots: append continuing transactions
        (deduped by stable uid across re-fetches), build the balance timeseries,
        and capture current investment holdings. Inert when nothing is configured
        — the pipeline then behaves exactly as the CSV-only era."""
        cfg = self.cfg
        # seed the balance timeseries with each account's CSV-derived latest balance
        bal_by_key: dict[tuple, BalanceObs] = {}
        for account_id, (date, cents) in self.latest_balances.items():
            if date is not None:
                bal_by_key[(account_id, date, "csv")] = BalanceObs(
                    account_id=account_id, as_of=date, balance_cents=cents, source="csv")

        sfid_map = cfg.simplefin_map()
        snaps = cfg.find_simplefin_snapshots()
        if sfid_map and not snaps:
            self.checks.append(QualityCheck(
                name="simplefin.snapshots", status="warn",
                detail="Accounts are connected to the bank feed but no pull is stored yet: refresh to fetch one."))
        if sfid_map and snaps:
            cutovers = cfg.simplefin_cutovers()
            # Cross-snapshot dedup by SimpleFIN's own transaction id, LATEST
            # snapshot winning: the feed mutates descriptors between fetches
            # (pending→posted, date tokens masked to XXXXXX), so two snapshots
            # can carry the same transaction with different content-hash uids —
            # keying on sf_tid keeps exactly one, at its settled wording.
            sf_txns: dict[str, dict[str, Txn]] = defaultdict(dict)
            seen_checks: set[str] = set()   # dedupe per-account warns across snapshots
            latest_bal: dict[str, tuple[str, int]] = {}
            earliest_raw: dict[str, str] = {}   # earliest unfiltered txn date per acct
            sf_min: dict[str, str] = {}
            sf_max: dict[str, str] = {}
            n_txn = n_read = 0
            newest: tuple[list[Holding], list[BalanceObs]] | None = None   # the newest snapshot read
            for snap in snaps:
                try:
                    txns, balances, holdings, stats, checks, raw_min = (
                        simplefin_parser.parse_snapshot(snap, sfid_map, cutovers,
                                                        cfg.accounts, cfg.simplefin_tz,
                                                        cfg.simplefin_ignore()))
                except Exception:   # any error a stored answer provokes is that answer's failure, never the ingest's
                    self.simplefin_unreadable.add(snap.name)
                    self.checks.append(QualityCheck(
                        name=f"simplefin.snapshot_unreadable.{snap.name}", status="fail",
                        detail=f"Not read: the bank feed's stored answer {snap.name} is damaged or not laid out as the "
                               "feed's answers are, so it adds nothing; the other stored answers are read. Remove "
                               "that one file from the bank feed's store: the answers fetched before and after it "
                               "overlap it, so they keep most of its days."))
                    continue
                n_read += 1
                newest = (holdings, balances)
                self.file_stats.append(stats)
                for a, d in raw_min.items():
                    if a not in earliest_raw or d < earliest_raw[a]:
                        earliest_raw[a] = d
                for c in checks:
                    if c.name not in seen_checks:
                        seen_checks.add(c.name)
                        self.checks.append(c)
                for t in txns:
                    sf_txns[t.account_id][t.sf_tid or t.uid] = t   # later snapshot wins
                for b in balances:
                    bal_by_key[(b.account_id, b.as_of, b.source)] = b
                    # a later file's report of the same balance date replaces the earlier one, as in the series map
                    prev = latest_bal.get(b.account_id)
                    if prev is None or b.as_of >= prev[0]:
                        latest_bal[b.account_id] = (b.as_of, b.balance_cents)
            if newest is not None:   # the newest snapshot read = current positions
                holdings, balances = newest
                self.holdings.extend(holdings)
                self.simplefin_holding_accounts = {h.account for h in holdings}
                # accounts that reported a balance in the newest snapshot are
                # "live this run": only a live margin account is broken into its
                # positions, and _simplefin_staleness_checks warns for the rest
                self.simplefin_live_accounts = {b.account_id for b in balances}
                self.simplefin_snapshot_read = True
            # flatten the deduped feed transactions (insertion order preserved;
            # replaced versions keep their slot) and account them
            for account_id, by_tid in sf_txns.items():
                for t in by_tid.values():
                    self.txns.append(t)
                    n_txn += 1
                    self.acct_file_sums[account_id] = (
                        self.acct_file_sums.get(account_id, 0) + t.amount_cents)
                    sf_min[account_id] = min(sf_min.get(account_id, t.date), t.date)
                    sf_max[account_id] = max(sf_max.get(account_id, t.date), t.date)
            # extend coverage windows so enrichment / coverage see SimpleFIN dates
            for account_id in set(sf_min) | set(sf_max):
                cur = self.windows.get(account_id, (None, None))
                start = min([d for d in (cur[0], sf_min.get(account_id)) if d], default=None)
                end = max([d for d in (cur[1], sf_max.get(account_id)) if d], default=None)
                self.windows[account_id] = (start, end)
            # the newest feed balance per account (all kinds): the feed checks read its date, and an
            # account with no net-worth series takes it as its latest balance (_latest_balance_all);
            # net worth reads balance_obs instead
            self.simplefin_latest_bal = dict(latest_bal)
            # a fresher feed balance replaces a checking or savings account's file
            # balance in latest_balances
            for account_id, (date, cents) in latest_bal.items():
                if cfg.accounts[account_id]["kind"] in CASH_KINDS:
                    prev = self.latest_balances.get(account_id)
                    if prev is None or date > prev[0]:
                        self.latest_balances[account_id] = (date, cents)
            # The seam at each cutover (earliest_raw: per account id, the feed's earliest transaction, one dated before
            # the cutover included): a gap when that transaction comes after the cutover, so the days between are in
            # no source; an overlap when parse_transactions dropped file rows on or after the cutover while the feed
            # has no transaction on or before the files' last row, so nothing replaces them.
            for account_id, cutover in cutovers.items():
                first = earliest_raw.get(account_id)
                if first and first > cutover:
                    self.checks.append(QualityCheck(
                        name=f"simplefin.cutover_gap.{account_id}", status="warn",
                        detail=f"the feed's earliest transaction is {first}, {days_between(cutover, first)} day(s) "
                               f"after the cutover {cutover}: no source covers the days between"))
                clip = self.file_clips.get(account_id)
                if clip and (first is None or first > clip[1]):
                    self.checks.append(QualityCheck(
                        name=f"simplefin.cutover_overlap.{account_id}", status="warn",
                        detail=f"{clip[0]} file row(s) from the cutover {cutover} to {clip[1]} were dropped, and the "
                               f"feed's earliest transaction is {first or 'not yet there'}: nothing replaces them"))
            self.checks.append(QualityCheck(
                name="simplefin.ingested", status="info",
                detail=f"{n_read} snapshots, {n_txn} transactions, "
                       f"{len(self.simplefin_holding_accounts)} accounts with holdings"))
        self.balance_obs = list(bal_by_key.values())

    def enrich(self) -> None:
        cfg = self.cfg
        normalizer = VendorNormalizer(cfg.vendor_rules)
        for t in self.txns:
            if not t.vendor:   # a vendor set before this stage is kept
                t.vendor, _ = normalizer.normalize(t.description_raw)

        bank_ids = {a for a, m in cfg.accounts.items() if m["kind"] in CASH_KINDS}
        # the engine's venmoish heuristics are for BANK and card descriptors; rows of
        # any other kind of account are never judged by them
        bank_and_cards = {a for a, m in cfg.accounts.items() if m["kind"] in CASHFLOW_KINDS}

        engine = transfers_mod.TransferEngine.from_config(cfg, self.windows, bank_and_cards)
        result = engine.phase_a(self.txns)

        income = IncomeIdentifier(cfg.category_rules, cfg.assumptions, bank_ids, members=(*cfg.adults, *cfg.children))
        income.apply(self.txns)

        # Review-inbox answers and the learned rules are loaded BEFORE the Categorizer: the
        # learned set is precedence step 4 and must be known during Categorizer.apply.
        # Types are NOT final here: the pairing phases' retyping and release, set_internal and the
        # overrides all run after this point, so the learner's spend-row filter is best-effort. A txn
        # answer on a row that ends up internal still counts as learning evidence but never applies
        # (answers.py refuses internal rows); one on a row a `kinds:` rule typed is no evidence even
        # when the row is released later, though it still applies to that row. The answers themselves
        # are applied after the pairing phases.
        answers_doc, answer_checks = answers_mod.load(cfg.answers_path)
        # renames: stored answers and the prior learned doc follow a vendor or category rename
        # without a rewrite (each renamer is a no-op when its list is empty; the dashboard applies
        # the same category map from assumptions.json category_renames)
        answers_doc = cfg.vendor_renamer.apply_to_answers(cfg.category_renamer.apply_to_answers(answers_doc))
        self.checks.extend(answer_checks)
        prior_learned, learned_checks = learned_mod.load(cfg.learned_path)
        prior_learned = cfg.vendor_renamer.apply_to_learned(cfg.category_renamer.apply_to_learned(prior_learned))
        self.checks.extend(learned_checks)       # the load warn lives in the report even after persist_learned heals the file
        taxonomy = cfg.taxonomy
        self.checks.extend(renames_mod.unknown_references(answers_doc, cfg.overrides, prior_learned, taxonomy))
        self.learned_doc, derive_checks = learned_mod.derive(
            self.txns, answers_doc, prior_learned,
            learn_excluded=vendor_registry.learning_excluded(cfg.vendor_rules),
            explicit_vendor_cats=learned_mod.explicit_vendor_categories(cfg.vendor_rules),
            taxonomy=taxonomy)
        self.learned_events = self.learned_doc["history"][len(prior_learned.get("history") or []):]
        self.checks.extend(derive_checks)

        categorizer = categorizer_for(cfg, learned=learned_mod.as_rules(self.learned_doc))
        categorizer.apply(self.txns, normalizer.clean)

        engine.phase_b(self.txns, result)
        # a row a `kinds:` rule typed that phase B left unpaired is put back as no rule had typed it and goes through
        # the income and category stages it skipped; the generic phase may then pair it like any other row, and sends
        # it to review when it does not. A payment a card received stays internal (TransferEngine._payment_received):
        # released it would be a positive purchase, which spending nets against; the generic phase may still pair it
        released = engine.release_unpaired(self.txns, result)
        income.apply(released)
        categorizer.apply(released, normalizer.clean)
        # the generic phase leaves alone what the family answered (the answers apply further down) and a fallback
        # credit the categorizer would file had it reached it
        engine.phase_generic(self.txns, result, answered=answers_mod.valid_entries(answers_doc),
                             placed=lambda t: categorizer.places(t, normalizer.clean))
        engine.phase_c(self.txns, result)
        # rows re-typed internal during matching need their category re-synced
        for t in self.txns:
            if t.type in INTERNAL_TYPES:
                set_internal(t)
        # the AI step's stored answers, applied to the purchases nothing above filed: after every pairing phase, so no
        # pairing (nor the categorizer's `places`, which the generic phase asks) reads an AI category, and before the
        # answers and the overrides, which re-stamp what they reach. Off, or with no stored answer, it touches nothing
        if self.ai_apply:
            self.ai_applied = ai_mod.apply(cfg, self.txns)
        # rule-driven splits: after every stage that pairs or retypes rows and before the answers, so a
        # child is never a pairing candidate and vendor-scope answers can skip children (answers._answerable)
        self.txns, split_checks = splits_mod.apply(self.txns, cfg.split_rules)
        self.checks.extend(split_checks)
        # review-inbox answers from the dashboard (vendor scope, then txn scope; loaded above) …
        self.checks.extend(answers_mod.apply(self.txns, answers_doc))
        # … then family-knowledge YAML overrides win over everything above
        self.checks.extend(overrides_mod.apply(self.txns, cfg.overrides))
        # an override may retype a split parent out of spending: its children go with it
        self.txns, undone = splits_mod.unsplit_retyped(self.txns)
        if undone:
            self.checks.append(QualityCheck(
                name="splits.unsplit", status="info",
                detail=f"{undone} split purchase{'s' if undone != 1 else ''} left whole: an override retyped "
                       f"{'the purchase' if undone == 1 else 'the purchases'} out of spending (overrides.yaml)."))
        # report answers a config override beat, so the app can show "conflict"
        self.checks.extend(answers_mod.verify(self.txns, answers_doc))
        # every stage that files a row has run: what the AI step's answers filed is final (ai_status.json)
        self.ai_status = ai_mod.status(cfg, self.txns, self.ai_applied)
        ai_check = ai_mod.check(self.ai_status, self.ai_applied)
        if ai_check is not None:
            self.checks.append(ai_check)
        self.transfer_matches = result.matches
        self.questions.extend(result.open_questions)

        self.income_phases = detect_income_phases(self.txns, cfg.owners)
        newest = max((t.date for t in self.txns), default=None)
        # a row the bank dated ahead (a scheduled payment, a pending charge's expected settlement) is no day of data:
        # the data reaches the ingest day at most, as the dashboard's dataThrough says
        self.data_end = None if newest is None else min(newest, self.ingest_day)
        if self.data_end:
            self.recurring_rows = recurring_mod.detect(
                self.txns, self.data_end,
                cfg.assumptions.get("cancelled_subscriptions") or ())

    # ----------------------------------------------------------------- assets

    def _simplefin_staleness_checks(self) -> list[QualityCheck]:
        """A mapped account that reported no balance in the newest snapshot has gone
        stale — its connection auth-reset, was re-linked under a new id, or the feed
        dropped it. Net worth carries such an account at its last balance however old
        (etl/networth.py), so the total does not drop, but the figure stops moving with
        the account; this warn is what makes that loud, and says since when.

        Only runs once a snapshot has actually been read — no snapshots at all is a
        different condition, reported once as `simplefin.snapshots`."""
        if not self.simplefin_snapshot_read:
            return []
        checks: list[QualityCheck] = []
        for account_id, meta in sorted(self.cfg.accounts.items()):
            if not meta.get("simplefin_id"):
                continue
            if account_id in self.simplefin_live_accounts:
                continue
            known = self.simplefin_latest_bal.get(account_id)
            last = f"last seen {known[0]}" if known else "never seen in any snapshot"
            series = self.networth_series.get(account_id)
            if series and series.points:
                effect = f"net worth carries its last balance, from {series.points[-1][0]}, until it reports again"
            elif known:
                effect = "net worth does not count it"
            else:
                effect = "net worth has no balance of it to carry"
            checks.append(QualityCheck(
                name=f"simplefin.stale.{account_id}", status="warn",
                detail=f"{meta['name']} is connected to the bank feed but reported no balance "
                       f"in the newest pull ({last}); {effect}. If the bank re-linked it under a new id, point the "
                       f"account at the new id and keep the old one (simplefin_id_history in accounts.yaml); "
                       f"otherwise refresh."))
        return checks

    def _simplefin_balance_age_checks(self) -> list[QualityCheck]:
        """A mapped account that DID report in the newest snapshot, but whose own
        balance-date has stopped moving. `_simplefin_staleness_checks` cannot see this:
        a connection stuck on "needs attention" upstream keeps re-sending its last
        balance with the old balance-date, so the account looks live and its frozen
        value is booked as current. That once hid a brokerage's frozen balance.

        Clock-free (tests/test_e2e.py hashes exports across two ingests): age is
        measured against the newest date anywhere in the balance series."""
        if not self.simplefin_snapshot_read:
            return []
        newest = max((b.as_of for b in self.balance_obs), default=None)
        if not newest:
            return []
        checks: list[QualityCheck] = []
        for account_id, meta in sorted(self.cfg.accounts.items()):
            if not meta.get("simplefin_id"):
                continue
            if account_id not in self.simplefin_live_accounts:
                continue   # not reporting at all: the checks above already warn
            as_of = self.simplefin_latest_bal.get(account_id, (None, 0))[0]
            age = days_between(as_of, newest) if as_of else None
            if age is not None and age > _SIMPLEFIN_BALANCE_STALE_DAYS:
                checks.append(QualityCheck(
                    name=f"simplefin.balance_stale.{account_id}", status="warn",
                    detail=f"{meta['name']}: SimpleFIN still reports it, but its balance "
                           f"is dated {as_of}, {age} days behind the rest of the data "
                           f"({newest}) — the connection is likely stuck upstream (check "
                           f"SimpleFIN Bridge for a re-auth prompt). The value shown is "
                           f"that old balance"))
        return checks

    def _holdings_coverage_checks(self) -> list[QualityCheck]:
        """Self-contained coverage tripwire for live SimpleFIN brokerages. Net worth books
        each account's authoritative SimpleFIN `balance`; its holdings should decompose
        ~all of it. Flags a material gap (overvalued/stale holdings, unflagged margin, or a
        dropped holdings feed) using only live feed data. The balance is the account's latest
        (_latest_balance_all), the one the snapshot books."""
        margin_ids = self.cfg.margin_account_ids()
        sf_holdings: dict[str, int] = defaultdict(int)
        for h in self.holdings:
            sf_holdings[h.account_id] += h.value_cents
        latest = self._latest_balance_all()
        checks: list[QualityCheck] = []
        for account_id, meta in self.cfg.accounts.items():
            if (meta.get("kind") != "brokerage"
                    or account_id not in self.simplefin_live_accounts):
                continue
            sf = latest.get(account_id)
            held = sf_holdings.get(account_id)
            if not sf or not held:     # no balance, or a balance-only feed (no holdings)
                continue
            checks.append(reconcile.holdings_coverage(
                account_id, meta["name"], balance_cents=sf[1], holdings_cents=held,
                is_margin=account_id in margin_ids))
        return checks

    def parse_assets(self) -> None:
        """The net-worth snapshots: reads the store's document of the assets entered in the app (manual_assets.load,
        leniently: a damaged one is a warning and counts nothing) and merges it with the family's `manual_entries`
        into the effective list (manual_assets.effective, a warning per asset left out), builds the series map once
        (etl/networth.py) from the balance series and that list, and reads it on every date `networth.snapshot_dates`
        names — each month end before the current date, then the current date (the newest account observation; with
        none, the newest manual value on or before the ingest day, else that day). Only
        the current snapshot is read with the newest feed file's holdings and live accounts, so only it breaks a live
        margin account into its positions; a month end holds that account as one row at its balance. `asset_rows`
        holds every snapshot's rows, each stamped with its date; `mortgage_included` and `snapshot_coverage` have one
        entry per date. Runs after parse_simplefin, which finds the holdings. No point in any series is no snapshot."""
        cfg = self.cfg
        doc, checks = manual_assets_mod.load(cfg.manual_assets_path)
        self.checks.extend(checks)
        entries = cfg.accounts_cfg.get("manual_entries") or []
        self.manual_assets, checks = manual_assets_mod.effective(
            doc, entries, cfg.owners, ASSET_CLASSES, LIABILITY_CLASSES, [m["name"] for m in cfg.accounts.values()])
        self.manual_overridden = manual_assets_mod.overridden(self.manual_assets, entries)
        self.checks.extend(checks)
        series = self.networth_series = networth.build_series(
            self.balance_obs, cfg.accounts, self.manual_assets,
            include_credit_cards=cfg.net_worth_options["include_credit_cards"])
        dates = networth.snapshot_dates(series, self.ingest_day)
        if not dates:
            return
        current, mortgage_id = dates[-1], cfg.mortgage_account_id()
        for day in dates:
            snap = (networth.snapshot_at(series, day, holdings=self.holdings,
                                         live=frozenset(self.simplefin_live_accounts))
                    if day == current else networth.snapshot_at(series, day))
            self.mortgage_included[day] = networth.mortgage_included(snap, mortgage_id)
            self.snapshot_coverage[day] = (snap.coverage, networth.missing_names(series, snap))
            self.asset_rows.extend(networth.asset_rows(snap))
        self.current_snapshot_date = current

    # ---------------------------------------------------------------- quality

    def finalize_quality(self) -> None:
        # per-account reconciliation: the signed sum of every transaction that
        # reached the store must equal the raw files' parsed sums (net of
        # cross-file dedup) — guarantees nothing was dropped or double-counted
        # between parsing and persistence
        store_sums: dict[str, int] = defaultdict(int)
        for t in self.txns:
            if not t.is_parent:          # a split parent is display-only; its children carry the amount
                store_sums[t.account_id] += t.amount_cents
        for account_id, file_sum in sorted(self.acct_file_sums.items()):
            expected = file_sum - self.acct_dedup_sums.get(account_id, 0)
            got = store_sums.get(account_id, 0)
            self.checks.append(QualityCheck(
                name=f"reconcile.{account_id}.store_vs_files",
                status="pass" if got == expected else "fail",
                detail=f"store sum {fmt(got)} vs raw-file sum {fmt(expected)}"))
        self.checks.append(splits_mod.sum_identity(self.txns))
        self.checks.extend(self._simplefin_staleness_checks())
        self.checks.extend(self._simplefin_balance_age_checks())
        self.checks.extend(self._holdings_coverage_checks())
        self.checks.extend(reconcile.coverage(self.windows))
        self.checks.extend(self._ledger_drift_checks())
        share, check = reconcile.uncategorized_share(self.txns)
        self.checks.append(check)
        self.uncat_share = share
        known = {e["vendor"] for e in self.cfg.vendor_rules.get("patterns", [])}
        self.unmapped = reconcile.unmapped_vendors(self.txns, known)
        self.month_sums = reconcile.account_month_sums(self.txns)

    def _ledger_drift_checks(self) -> list[QualityCheck]:
        """Ledger-drift eligibility: cash-like kinds with a SimpleFIN id and at least one feed row — NOT
        keyed on simplefin_cutover (feed-only cards have none; brokerages have one but their
        balance moves with the market). The sum covers every row of the account
        (reconcile.ledger_drift_inputs), not feed rows only. Fills self.ledger_drift for accounts.json."""
        inputs = reconcile.ledger_drift_inputs(self.txns)
        checks: list[QualityCheck] = []
        self.ledger_drift = {}
        for account_id, m in self.cfg.accounts.items():
            rows, has_feed = inputs.get(account_id, ([], False))
            if m["kind"] not in CASHFLOW_KINDS or not m.get("simplefin_id") \
                    or not has_feed:
                continue
            res = reconcile.ledger_drift(account_id, m["name"], self.balance_obs, rows)
            if res is None:
                continue
            check, payload = res
            checks.append(check)
            self.ledger_drift[account_id] = payload
        return checks

    # ---------------------------------------------------------------- persist

    def _latest_balance_all(self) -> dict[str, tuple[str, int]]:
        """Every account's latest (as_of, balance cents), which the DB `accounts` table, accounts.json and the
        holdings-coverage check read. An account with a net-worth series takes its last point (networth.build_series:
        the newest balance date; on it a file balance beats a feed report, and a newer feed file's report beats an
        older one's), so it is the balance the snapshot books and the daily line ends on. Any other account (a card
        while cards are out of net worth) takes its newest feed balance (of two reports of one balance date, the newer
        file's), else its file balance, as does every account before parse_assets builds the map."""
        out = {**self.latest_balances, **self.simplefin_latest_bal}
        for row_id, s in self.networth_series.items():
            if not s.manual and s.points:
                out[row_id] = s.points[-1]
        return out

    def persist_learned(self) -> None:
        """scenarios/learned_rules.json — the ETL's only state file outside data/ (a damaged one is
        first copied to learned_rules.json.corrupt, an unreadable one is never written over). Runs
        after finalize_quality so every check is known, and only when none failed (a failing
        ingest must not bake its state in; the skip is said so in `learned_rules.not_saved`, naming
        the failing checks); compares rules only; a write failure is one warn in the quality
        report, never an abort — a read-only mount cannot take the ingest down."""
        if self.learned_doc is None:
            return
        failed = [c.name for c in self.checks if c.status == "fail"]
        if failed:
            self.checks.append(QualityCheck(
                name="learned_rules.not_saved", status="info",
                detail=f"Learned rules were not saved this run: {len(failed)} check{'s' if len(failed) != 1 else ''} "
                       f"failed ({', '.join(failed[:10])}{', ...' if len(failed) > 10 else ''}); they apply now and "
                       "are saved once an ingest passes."))
            return
        self.learned_written, checks = learned_mod.write_if_changed(self.cfg.learned_path, self.learned_doc)
        self.checks.extend(checks)

    def persist(self) -> None:
        cfg = self.cfg
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        db_path = cfg.data_dir / "finance.db"
        conn = db.build(db_path)
        owner_of = cfg.owner_of()
        latest_bal = self._latest_balance_all()

        conn.executemany(
            "INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(a, m["name"], m["institution"], m["owner"], m["kind"],
              self.windows.get(a, (None, None))[0], self.windows.get(a, (None, None))[1],
              sum(1 for t in self.txns if t.account_id == a and not t.is_parent),
              latest_bal.get(a, (None, None))[1],
              latest_bal.get(a, (None, None))[0])
             for a, m in cfg.accounts.items()])
        db.insert_txns(conn, self.txns, owner_of)
        db.insert_assets(conn, self.asset_rows)
        db.insert_holdings(conn, self.holdings)
        db.insert_balances(conn, self.balance_obs)
        conn.executemany("INSERT INTO transfer_matches VALUES (?,?,?,?,?)",
                         self.transfer_matches)
        conn.executemany(
            """INSERT INTO recurring VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(r["vendor"], r["account_id"], r["category"], r["cadence"],
              r["typical_amount_cents"], r["last_amount_cents"], r["amount_mode"],
              r["occurrences"], r["first_seen"], r["last_seen"], r["next_expected"],
              int(r["active"]), r["annualized_cents"],
              json.dumps(r["price_changes"])) for r in self.recurring_rows])
        db.insert_file_stats(conn, self.file_stats)
        db.insert_quality(conn, self.checks)
        db.insert_questions(conn, self.questions)
        db.finalize(conn, db_path)

    def _export_json(self, path: Path, doc) -> None:
        """Write one export file and note its name for manifest.json `files`."""
        exports.write_json(path, doc)
        self.exported.append(path.name)

    def export(self) -> None:
        out = self.cfg.exports_dir
        self.exported = []
        self._export_ledger(out)
        self._export_accounts(out)
        self._export_assumptions(out)
        # written by every ingest, the AI step on or off (what the Settings card reads)
        self._export_json(out / "ai_status.json", exports.export_ai_status(
            self.ai_status or ai_mod.status(self.cfg, self.txns, self.ai_applied)))
        exports.write_json(out / "manifest.json", exports.export_manifest(
            # The household's wall clock (household.yaml `timezone`, whatever zone the process runs in) with that
            # zone's UTC offset, as YYYY-MM-DDTHH:MM:SS±HH:MM. Its first 19 characters are the plain wall clock every
            # display slice reads; with the offset it is an instant, which lib/answers.ts compares against an
            # answer's `answered_at` (UTC, with a Z) — a wall clock without its offset would be hours off there.
            generated_at=self.ingested_at.isoformat(timespec="seconds"),
            # When the data was pulled: the newest snapshot's own stamp (UTC, with a Z, so the browser can parse it
            # anywhere). Unlike `generated_at` it does not move when an ingest re-exports the same snapshots, after a
            # failed fetch or a deploy. Always present; null when the store holds no snapshots yet.
            simplefin_fetched_at=self.last_fetch(self.cfg.find_simplefin_snapshots()),
            transaction_count=splits_mod.count_rows(self.txns),
            current_snapshot_date=self.current_snapshot_date,
            setup=self._setup(),
            # The files this run wrote, never what the folder holds: a file a run of an older contract left there is
            # neither listed nor removed. export_manifest adds manifest.json itself.
            files=self.exported,
        ))

    def _setup(self) -> dict:
        """manifest.json `setup`: what the household sets up, whether or not data has arrived — the registry's
        accounts, whether one names a feed id (a current or an earlier one; no snapshot needed), the statement files
        parse_transactions found for the accounts' globs, and the manual assets net worth counts (the effective list:
        the config's entries and the assets entered in the app)."""
        cfg = self.cfg
        return {"accounts": len(cfg.accounts), "feed": bool(cfg.simplefin_map()),
                "import_files": len(self.import_files), "manual_assets": len(self.manual_assets)}

    def current_total_cents(self) -> int | None:
        """The current snapshot's net worth in cents (its rows' sum), or None when there is no snapshot: then the net
        worth is not known yet, which a total of 0 would hide."""
        if self.current_snapshot_date is None:
            return None
        return sum(r.value_cents for r in self.asset_rows if r.snapshot_date == self.current_snapshot_date)

    def last_fetch(self, snapshots: list[Path]) -> str | None:
        """The newest readable snapshot's own filename stamp: one parse_simplefin could not read says nothing of how
        old the data is. The list is sorted lexically, which is chronological for these names — and stays so for the
        `-1` collision suffix, since '-' (0x2D) sorts before '.' (0x2E)."""
        readable = [s for s in snapshots if s.name not in self.simplefin_unreadable]
        return snapshot_fetched_at(readable[-1].name) if readable else None

    def _export_ledger(self, out: Path) -> None:
        """Transactions, assets, holdings, balances, cashflow, recurring, quality."""
        cfg = self.cfg
        owner_of = cfg.owner_of()
        names = {a: m["name"] for a, m in cfg.accounts.items()}
        cashflow_ids = [a for a, m in cfg.accounts.items() if m["kind"] in CASHFLOW_KINDS]

        self._export_json(out / "transactions.json",
                          exports.export_transactions(self.txns, owner_of, names))
        self._export_json(out / "assets_timeseries.json",
                          exports.export_assets_timeseries(
                              self.asset_rows, self.mortgage_included, self.snapshot_coverage))
        self._export_json(out / "holdings.json", exports.export_holdings(self.holdings))
        self._export_json(out / "balances_timeseries.json",
                          exports.export_balances_timeseries(self.balance_obs, cfg.accounts))
        self._export_json(out / "networth_daily.json",
                          exports.export_networth_daily(self.networth_series, self.current_total_cents(),
                                                        self.current_snapshot_date))
        self._export_json(out / "cashflow_monthly.json",
                          exports.export_cashflow_monthly(self.txns, self.windows, cashflow_ids,
                                                          min_share=cfg.coverage_min_share, today=self.ingest_day))
        self._export_json(out / "recurring.json", {"items": [
            {**{k: v for k, v in r.items()
                if not k.endswith("_cents") and k != "price_changes"},
             "typical_amount": r["typical_amount_cents"] / 100,
             "last_amount": r["last_amount_cents"] / 100,
             "annualized": r["annualized_cents"] / 100,
             "monthly_equivalent": round(r["annualized_cents"] / 1200, 2),
             "price_changes": [{"date": c["date"], "from": c["from_cents"] / 100,
                                "to": c["to_cents"] / 100} for c in r["price_changes"]]}
            for r in self.recurring_rows]})
        self._export_json(out / "vendors.json", exports.export_vendors(
            vendor_registry.build(self.txns, cfg.vendor_rules, self.learned_doc, self.data_end)))
        self._export_json(out / "quality_report.json",
                          exports.export_quality(self.file_stats, self.checks,
                                                 self.month_sums, self.unmapped,
                                                 self.overlaps))
        # unmatched-internal rows are data-gap notes (a counterpart export omits
        # the other leg; already excluded from spending) — not actionable
        # questions, so they export separately and don't inflate the open count
        def question_dict(q):
            return {"topic": q.topic, "question": q.question, "context": q.context}
        self._export_json(out / "open_questions.json", {
            "questions": [question_dict(q) for q in self.questions
                          if q.topic != "unmatched-internal"],
            "data_gaps": [question_dict(q) for q in self.questions
                          if q.topic == "unmatched-internal"]})
    def _export_accounts(self, out: Path) -> None:
        cfg = self.cfg
        latest_bal_all = self._latest_balance_all()   # all kinds; matches the DB
        self._export_json(out / "accounts.json", {"accounts": [
            {"id": a, "name": m["name"], "institution": m["institution"],
             "owner": m["owner"], "kind": m["kind"],
             # the class the account's rows take (stated, else Mortgage for the mortgage loan, else the kind's default)
             "asset_class": cfg.asset_class_of(a),
             "txn_data_start": self.windows.get(a, (None, None))[0],
             "txn_data_end": self.windows.get(a, (None, None))[1],
             "latest_balance": (latest_bal_all[a][1] / 100
                                if a in latest_bal_all else None),
             "latest_balance_as_of": (latest_bal_all[a][0]
                                      if a in latest_bal_all else None),
             "ledger_drift": exports.export_ledger_drift(self.ledger_drift.get(a))}
            for a, m in cfg.accounts.items()],
            "manual_assets": exports.export_manual_assets(self.manual_assets),
            "manual_overridden": exports.export_manual_assets(self.manual_overridden)})

    def _mortgage_block(self) -> dict | None:
        """assumptions.json `mortgage`: None when no account is `mortgage: true`. Otherwise that account's id; its
        balance (positive dollars owed) and the balance's date from its own row in the current snapshot, both None
        without one — a hand-stated Mortgage-class row never stands in for it; its loan terms (Config.loan_terms, the
        rate as `rate_pct`), each None when absent; and `schedule`, its row at every snapshot date it has one (its
        last point on or before the date, carried), oldest first, as {date: the snapshot date, principal_balance}."""
        cfg = self.cfg
        mort_id = cfg.mortgage_account_id()
        if mort_id is None:
            return None
        rows = sorted((r for r in self.asset_rows if r.account_id == mort_id), key=lambda r: r.snapshot_date)
        current = next((r for r in rows if r.snapshot_date == self.current_snapshot_date), None)
        terms = cfg.loan_terms(mort_id)
        return {
            "account_id": mort_id,
            "balance": abs(current.value_cents) / 100 if current else None,
            "balance_as_of": current.as_of if current else None,
            "rate_pct": terms["rate"],
            **{k: terms[k] for k in ("monthly_payment", "monthly_escrow", "escrow_balance", "origination",
                                     "term_months")},
            "schedule": [{"date": r.snapshot_date, "principal_balance": abs(r.value_cents) / 100} for r in rows]}

    def _export_assumptions(self, out: Path) -> None:
        cfg = self.cfg
        margin_id = next(iter(cfg.margin_account_ids()), None)
        # household.yaml's members, in file order, as the dashboard names them: an adult is an "owner", a child a
        # "kid"; birth_year only when the member has one
        people = [{"name": m["name"], "role": EXPORTED_ROLES[m["role"]],
                   **({"birth_year": m["birth_year"]} if "birth_year" in m else {})} for m in cfg.members]
        stated = {k: v for k, v in (cfg.assumptions.get("household") or {}).items() if k != "people"}
        household = {"name": cfg.household_name,          # household.yaml `name`, else the surname, else "Household"
                     "timezone": cfg.timezone,            # household.yaml `timezone`, an IANA zone name
                     "people": people,
                     # assumptions.yaml `household:` (margin APR, retirement accounts) passes through in its own order
                     **stated,
                     # a key it leaves out is exported empty: no margin rate is estimated, no retirement account assumed
                     **{k: v for k, v in (("margin_apr", None), ("retirement_accounts", {})) if stated.get(k) is None},
                     # derived here so the app never carries the brokerage's name
                     "margin_account": cfg.accounts[margin_id]["name"] if margin_id else None}
        projections = cfg.assumptions.get("projections") or {}
        self._export_json(out / "assumptions.json", {
            "projections": exports.export_projections(projections),
            "projections_stated": exports.export_projections_stated(projections),
            "targets": cfg.assumptions.get("targets", {}),
            "budgets": cfg.assumptions.get("budgets", {}),
            "household": household,
            # household.yaml `modules`, exactly as stated: a module it leaves out is not listed
            "modules": dict(cfg.modules),
            "category_renames": exports.export_category_renames(cfg.category_renames),
            # in assumptions.yaml's own key order
            "thresholds": {k: v for k, v in cfg.assumptions.items() if k in THRESHOLD_KEYS},
            "taxonomy": cfg.taxonomy,
            "reserved": dict(RESERVED),
            # household.yaml net_worth: whether the snapshot counts credit cards (their rows are Other Debt)
            "net_worth": {"include_credit_cards": bool(cfg.net_worth_options["include_credit_cards"])},
            "owners": list(cfg.owners),
            "income_phases": [
                {**p, "total": p["total_cents"] / 100,
                 "avg_monthly": p["avg_monthly_cents"] / 100}
                for p in self.income_phases],
            "mortgage": self._mortgage_block(),
            "account_windows": {a: {"start": w[0], "end": w[1]}
                                for a, w in self.windows.items()},
            "asset_classes": exports.export_asset_classes(),
        })

    # ---------------------------------------------------------------- summary

    def print_summary(self) -> None:
        """The console summary (stdout only, never an export). Its spending line totals the calendar year
        `summary_year` picks: the last full year in the data, or, when every transaction falls in one year, that
        year labelled "(partial year)"; with no transactions there is no spending line."""
        txns = self.txns
        year = summary_year(txns)
        cur = [r for r in self.asset_rows
               if r.snapshot_date == self.current_snapshot_date]
        nw = self.current_total_cents()
        by_vendor: dict[str, int] = defaultdict(int)
        for t in txns:
            if t.type in SPEND_TYPES and t.amount_cents < 0 and t.vendor and not t.is_parent:
                by_vendor[t.vendor] += -t.amount_cents
        top = sorted(by_vendor.items(), key=lambda kv: -kv[1])[:10]

        fails = [c for c in self.checks if c.status == "fail"]
        warns = [c for c in self.checks if c.status == "warn"]
        print("\n=== Ingest summary ===")
        # the manifest's count (splits.count_rows: a split purchase counted once, as its parts), then what the splits made
        parents = sum(1 for t in txns if t.is_parent)
        parts = sum(1 for t in txns if t.parent_id is not None)
        split_note = (f" ({parents} purchase{'s' if parents != 1 else ''} split into {parts} part{'s' if parts != 1 else ''})"
                      if parents else "")
        print(f"transactions: {splits_mod.count_rows(txns)}{split_note}  |  asset rows: {len(cur)} in the current snapshot"
              f" ({len(self.snapshot_coverage)} snapshots)  |  holdings: {len(self.holdings)}")
        if year:
            y, complete = year
            spend = -sum(t.amount_cents for t in txns
                         if t.type in SPEND_TYPES and t.date[:4] == y and not t.is_parent)
            print(f"{y} spending{'' if complete else ' (partial year)'}: {fmt(spend)}")
        print(f"current net worth ({self.current_snapshot_date}): {fmt(nw)}" if nw is not None
              else "current net worth: not available yet (no balance reported)")
        print(f"uncategorized spend share: {self.uncat_share:.1%}")
        n_gaps = sum(q.topic == "unmatched-internal" for q in self.questions)
        print(f"quality: {len(fails)} fail / {len(warns)} warn "
              f"/ {len(self.checks)} checks  |  open questions: "
              f"{len(self.questions) - n_gaps} (+{n_gaps} data gaps)")
        for c in fails:
            print(f"  FAIL {c.name}: {c.detail}")
        for c in warns:
            print(f"  warn {c.name}: {c.detail}")
        n_learned = len((self.learned_doc or {}).get("rules", []))
        print(f"learned rules: {n_learned} active"
              + (" (learned_rules.json written)" if self.learned_written else ""))
        for e in self.learned_events:      # per-pass events: stdout + the file's history, never an export
            print(f"  {e['event']} {e['vendor']} -> {e['category']}")
        print("top vendors:")
        for v, s in top:
            print(f"  {fmt(s):>12}  {v}")


def summary_year(txns: list[Txn]) -> tuple[str, bool] | None:
    """The calendar year the ingest summary totals spending for, and whether it is complete. A year is complete once
    the data runs past it (its newest transaction is dated in a later year), so the pick is the newest year that has
    transactions and lies before the newest transaction's own year. When every transaction falls in one year, that
    year, not complete; None when there are none."""
    years = {t.date[:4] for t in txns}
    if not years:
        return None
    newest = max(years)
    earlier = [y for y in years if y < newest]
    return (max(earlier), True) if earlier else (newest, False)


# how long an ingest waits for another one to end before it gives up (run)
INGEST_LOCK_WAIT_S = 600.0


class IngestBusy(RuntimeError):
    """Another ingest held the lock past the wait: this one did not start (`python -m etl` says so in one line)."""


@contextlib.contextmanager
def ingest_lock(path: Path, wait_s: float = INGEST_LOCK_WAIT_S):
    """Serialize the whole ingest across processes.

    An ingest starts from the scheduler's timed refresh, from the refresh
    runner (the dashboard's Refresh now) or from a manual run (`make ingest`,
    `python -m etl ingest`). A manual run never passes through
    deploy/refresh.sh's lock, so the guard lives here, around the ingest
    itself. Two concurrent ingests corrupt the database: db.build() unlinks a
    shared `finance.db.tmp` that the other run still holds open, and
    db.finalize() re-derives that path by NAME — so the loser promotes the
    winner's half-written file over finance.db. The exports can likewise end
    up a mix of two runs, each file individually atomic but the set incoherent.

    flock, never fcntl.lockf: deploy/refresh.sh takes its own flock(1) and the
    two lock families do not interoperate. The lock file lives in data/, which
    is bind-mounted into every ETL container — never /tmp, which is per-
    container and would make this silently a no-op across services.

    Bounded and non-blocking rather than a plain blocking flock: a run that
    waits forever behind a wedged ingest is worse than one that fails."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + wait_s
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise IngestBusy(
                        f"another ingest has held {path} for over {wait_s:.0f}s — "
                        "refusing to run a second one concurrently") from None
                time.sleep(0.5)
        yield
    finally:
        os.close(fd)   # closing releases the lock, including on an exception


def run(source_dir: str | None = None, strict: bool = False, *, cfg: Config | None = None) -> int:
    """One ingest. `cfg` is a Config already built and checked (`python -m etl ingest` hands over the one it
    validated, so the config files are read once); without it one is built here, with `source_dir`."""
    clock.check()      # a pinned clock that is no date and time stops an in-process run before it writes anything
    if cfg is None:
        cfg = Config(source_dir=source_dir)
    with ingest_lock(cfg.data_dir / ".ingest.lock", wait_s=INGEST_LOCK_WAIT_S):
        p = Pipeline(cfg)
        p.parse_transactions()
        p.parse_simplefin()
        p.enrich()
        p.parse_assets()
        p.finalize_quality()
        p.persist_learned()
        p.persist()
        p.export()
        p.print_summary()
    if strict and any(c.status == "fail" for c in p.checks):
        return 1
    return 0

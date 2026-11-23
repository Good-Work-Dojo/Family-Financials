# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Parse a SimpleFIN /accounts snapshot (JSON) into the pipeline's domain objects.

One snapshot holds many accounts. Each SimpleFIN account id maps to a registry
account id (via accounts.yaml `simplefin_id`); we then emit, per account:
  * Txn rows    — posted, non-pending, USD, on/after the account's cutover
  * BalanceObs  — one per account (balance / available / derived credit limit)
  * Holding     — investment accounts' holdings[] (symbol/shares/value/cost basis)

Pure & offline — no network, no clock. uid stability across daily re-fetches comes
from deriving Txn.seq from SimpleFIN's stable per-transaction id (so the same txn
seen in 30 snapshots collapses to one via the pipeline's existing uid dedup).

Dates: SimpleFIN posts epochs; banks date rows in local time, so every epoch is
converted in the household's zone (`tz`, household.yaml `timezone`) to match the
CSV side at the cutover seam rather than drifting a day under UTC. The same zone
dates holdings whose account carries no balance date: the snapshot's fetch stamp
(a UTC time in its file name) read as a local date. An unknown zone raises.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..dates import snapshot_fetched_at, squash
from ..importers.registry import MAX_DESCRIPTION
from ..models import (
    ACCOUNT_KINDS,
    CASHFLOW_KINDS,
    FEES_CATEGORY,
    INCOME_CATEGORY,
    BalanceObs,
    FileStats,
    Holding,
    QualityCheck,
    Txn,
    account_asset_class,
)
from ..money import parse_cents

# the kinds whose rows are not day-to-day money (a brokerage, a loan): fed by the feed alone, such an account is a
# balance feed and its transactions are not read
_BALANCES_ONLY_KINDS = frozenset(ACCOUNT_KINDS) - CASHFLOW_KINDS

# Brokerage cash events recognised by their wording: the feed gives a brokerage's rows no type, so these patterns
# type each event from its description. A buy or a sell is an internal portfolio move — of shares (the first pattern) or an option leg opened or
# closed, whose wording names a call or a put and no shares (the second); a dividend is income; margin interest is a
# fee; and card cashback swept into the brokerage is income, new money from the card issuer, whose own side books it
# as a statement credit rather than a transaction. Left untyped, each would default to a purchase: a dividend would
# drop out of income (income = income + dividend + interest), margin interest would sit in uncategorized spending and
# a cashback credit, a positive purchase, would subtract from spending. The income rules cannot type these rows: the
# IncomeIdentifier reads bank accounts only, and a brokerage is none.
_TRADE_RE = re.compile(r"\b(buy|sell|bought|sold)\b.*\bshares?\b", re.I)
_OPTION_RE = re.compile(r"\b(buy|sell|bought|sold)\b.*\b(call|put)s?\b.*\bto (open|close)\b", re.I)
_DIVIDEND_RE = re.compile(r"\bdividend\b", re.I)
_MARGIN_INT_RE = re.compile(r"monthly margin used|margin interest", re.I)
_CASHBACK_RE = re.compile(r"\bcash ?back rewards?\b", re.I)


def _zone(tz: str) -> dt.tzinfo:
    """The IANA zone named `tz`. An unknown name raises ValueError, and so do a value that is not text and
    `localtime` (whatever zone the machine is set to): a date drawn in a zone nobody chose is a wrong date, so there
    is no fallback (Config refuses such a household zone at load)."""
    refused = ValueError(f"unknown time zone {tz!r} — use an IANA name such as Europe/Paris")
    if not isinstance(tz, str) or tz == "localtime":
        raise refused
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError) as e:
        raise refused from e


def _iso(epoch, zone: dt.tzinfo) -> str | None:
    """The local date, in `zone`, of a feed epoch: a whole or fractional number of seconds, or text holding one (some
    bridges send "1700000000.0"). None for no value (empty, 0) and for anything else: a truth value, a container, text
    that is no number, a value that is not finite or lies past the years a date can hold."""
    if not epoch or isinstance(epoch, bool):
        return None
    try:
        seconds = int(float(epoch)) if isinstance(epoch, str) else int(epoch)
        return dt.datetime.fromtimestamp(seconds, zone).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _bad_epoch(value) -> bool:
    """A date field the feed filled with something _iso cannot read (an absent or empty one is no date, not a bad one)"""
    return value not in (None, "", 0)


def _text(value) -> str:
    """A text field as text: a number the feed sent in its place is written out; absent, null or a container is empty"""
    if isinstance(value, str):
        return value
    return str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else ""


def _snap_date(path: Path, zone: dt.tzinfo) -> str | None:
    """The local date, in `zone`, of the snapshot's fetch stamp (a UTC time in its file name); None for a name
    without one."""
    stamp = snapshot_fetched_at(path.name)
    if stamp is None:
        return None
    return dt.datetime.fromisoformat(stamp).astimezone(zone).date().isoformat()


def _is_usd(currency) -> bool:
    # SimpleFIN uses ISO "USD"; some bridges send a currency URL ending in /USD.
    # Empty => USD. Match the trailing code exactly to avoid e.g. "AUD" slipping.
    if not currency:
        return True
    code = str(currency).rstrip("/").rsplit("/", 1)[-1].upper()
    return code == "USD"


def _seq_from_id(txn_id: str) -> int:
    return int(hashlib.sha1(txn_id.encode()).hexdigest()[:8], 16)


def _cents(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return parse_cents(value)
    except ValueError:
        return None


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _derive_limit(meta: dict, balance_cents, available_cents, extra) -> int | None:
    """Credit limit for utilization. Prefer an explicit `extra` limit; else
    available-credit + amount-owed; else a manual `credit_limit` from config."""
    if meta.get("kind") != "credit-card":
        return None
    if isinstance(extra, dict):
        for k in ("credit-limit", "credit_limit", "limit", "credit-line"):
            c = _cents(extra.get(k))
            if c is not None:
                return c
    # available credit = limit - amount owed; amount owed = max(0, -balance) since
    # cards report debt as negative (a credit/overpaid balance owes nothing). Only
    # when available is genuinely reported (> 0): many issuers send 0 here, which
    # would otherwise collapse the limit to |balance| (a false 100%).
    if available_cents and available_cents > 0 and balance_cents is not None:
        return available_cents + max(0, -balance_cents)
    if meta.get("credit_limit") is not None:
        return round(float(meta["credit_limit"]) * 100)
    return None


def parse_snapshot(path: Path, sfid_to_account: dict[str, str],
                   cutovers: dict[str, str], account_meta: dict[str, dict],
                   tz: str, ignore: set[str] = frozenset()
                   ) -> tuple[list[Txn], list[BalanceObs], list[Holding],
                              FileStats, list[QualityCheck], dict[str, str]]:
    """Returns (txns, balances, holdings, stats, checks, earliest_raw) where
    earliest_raw maps account_id -> the earliest *unfiltered* txn date in this
    snapshot, used by the pipeline to detect a cutover gap across all snapshots.
    `tz` is the household's IANA zone; every date is drawn in it, and an unknown
    name raises ValueError before the file is read.

    A file that is no JSON object, or whose `accounts` is there and no list,
    raises ValueError (the pipeline makes it that snapshot's failing check). Inside it, an account,
    a row or a holding of the wrong shape is skipped with a warn
    (`simplefin.bad_account.<file>`, `simplefin.bad_row.<account>`), and so is a
    row whose date is unreadable (`simplefin.bad_date.<account>`); an unreadable
    balance date warns too (`simplefin.bad_balance_date.<account>`) and books no
    balance point."""
    zone = _zone(tz)
    stats = FileStats(source_file=path.name, parser="simplefin")
    txns: list[Txn] = []
    balances: list[BalanceObs] = []
    holdings: list[Holding] = []
    checks: list[QualityCheck] = []
    earliest_raw: dict[str, str] = {}
    seen_accounts: set[str] = set()
    snap_date = _snap_date(path, zone)

    def _is_historical(a: dict) -> bool:
        """A `simplefin_id_history` id — a prior, replaced connection to the same
        institution. Old snapshots map it normally; within ONE snapshot the current
        connection's entry must win, so these sort last and dedupe away silently."""
        registry = sfid_to_account.get(a.get("id"))
        return bool(registry) and a.get("id") in (
            account_meta[registry].get("simplefin_id_history") or ())

    def _warn_once(name: str, detail: str) -> None:
        if not any(c.name == name for c in checks):
            checks.append(QualityCheck(name=name, status="warn", detail=detail))

    doc = json.loads(path.read_text())
    if not isinstance(doc, dict) or not isinstance(doc.get("accounts", []), list):
        raise ValueError("not a JSON object holding a list of accounts")
    entries = []
    for acct in doc.get("accounts", []):
        if isinstance(acct, dict) and (acct.get("id") is None or isinstance(acct.get("id"), str)):
            entries.append(acct)
        else:
            _warn_once(f"simplefin.bad_account.{path.name}",
                       f"An account in the bank feed's stored answer {path.name} is not laid out as the feed's "
                       f"accounts are, so it was skipped; the answer's other accounts are read.")
    for acct in sorted(entries, key=_is_historical):
        sfid = acct.get("id")
        account_id = sfid_to_account.get(sfid)
        if not account_id:
            if sfid in ignore:        # expected unmapped (duplicate twin / dead account)
                continue
            org = acct.get("org")
            org = org.get("name", "?") if isinstance(org, dict) else (org if isinstance(org, str) else "?")
            checks.append(QualityCheck(
                name=f"simplefin.unmapped.{sfid}", status="warn",
                detail=f"{org} / {acct.get('name', '?')} "
                       f"(balance {acct.get('balance', '?')}) matches no configured account: add it with this feed "
                       f"id (simplefin_id in accounts.yaml), or leave it if the household does not track it."))
            continue
        if account_id in seen_accounts:
            if _is_historical(acct):  # expected twin during a connection replacement
                continue
            checks.append(QualityCheck(
                name=f"simplefin.duplicate.{account_id}", status="warn",
                detail=f"SimpleFIN id {sfid} maps to {account_id} which already "
                       f"appeared in {path.name}; the later copy was skipped"))
            continue
        seen_accounts.add(account_id)
        meta = account_meta[account_id]
        if not _is_usd(acct.get("currency")):
            checks.append(QualityCheck(
                name=f"simplefin.currency.{account_id}", status="warn",
                detail=f"non-USD currency {acct.get('currency')!r}; account skipped "
                       f"(no FX conversion)"))
            continue

        # ---- balance observation (all kinds) -----------------------------
        bal_date = _iso(acct.get("balance-date"), zone)
        if bal_date is None and _bad_epoch(acct.get("balance-date")):
            _warn_once(f"simplefin.bad_balance_date.{account_id}",
                       "The bank feed sent this account's balance with a date that is no date, so that balance was "
                       "skipped; the next answer with a readable date counts.")
        raw_bal = acct.get("balance")
        balance_cents = _cents(raw_bal)
        if balance_cents is None and raw_bal not in (None, ""):
            checks.append(QualityCheck(
                name=f"simplefin.bad_balance.{account_id}", status="warn",
                detail=f"unparseable balance {raw_bal!r}; balance point skipped"))
        if balance_cents is not None and meta.get("kind") == "loan":
            # a loan is owed: stored negative whatever sign the feed uses, so every reader of the balance (the
            # latest-balance maps, the balance series, the daily line) sees one sign; a card keeps the feed's
            balance_cents = -abs(balance_cents)
        available_cents = _cents(acct.get("available-balance"))
        limit_cents = _derive_limit(meta, balance_cents, available_cents,
                                    acct.get("extra"))
        if bal_date and balance_cents is not None:
            balances.append(BalanceObs(
                account_id=account_id, as_of=bal_date, balance_cents=balance_cents,
                available_cents=available_cents, limit_cents=limit_cents,
                source="simplefin"))

        # ---- transactions ------------------------------------------------
        # A brokerage/loan account fed ONLY by SimpleFIN (no CSV parser, no cutover) is
        # a pure asset feed for net worth — skip its transactions: dividend
        # reinvestments and contributions are internal moves, not spending, and would
        # pollute the spend/vendor/uncategorized metrics. A brokerage with a cutover and
        # spending cards still import. Overridable per-account via simplefin_balances_only.
        balances_only = meta.get("simplefin_balances_only")
        if balances_only is None:
            balances_only = (meta.get("kind") in _BALANCES_ONLY_KINDS
                             and not meta.get("parser")
                             and not meta.get("simplefin_cutover"))
        cutover = cutovers.get(account_id)
        bad_row = (f"simplefin.bad_row.{account_id}",
                   "The bank feed sent a row or a holding of this account that is not laid out as the feed's are, so "
                   "it was skipped; the account's other rows are read.")
        rows = acct.get("transactions")
        if rows is not None and not isinstance(rows, list):
            _warn_once(*bad_row)
        for txn in (() if balances_only or not isinstance(rows, list) else rows):
            stats.rows_total += 1
            if not isinstance(txn, dict):
                _warn_once(*bad_row)
                stats.skip("unparseable-row"); continue
            if txn.get("pending"):
                stats.skip("pending"); continue
            when = txn.get("posted") or txn.get("transacted_at")
            date = _iso(when, zone)
            if not date:
                if _bad_epoch(when):
                    _warn_once(f"simplefin.bad_date.{account_id}",
                               "The bank feed sent a row of this account with a date that is no date, so it was "
                               "skipped.")
                stats.skip("no-date"); continue
            prev = earliest_raw.get(account_id)   # pre-cutover earliest, for gap check
            if prev is None or date < prev:
                earliest_raw[account_id] = date
            if cutover and date < cutover:        # CSV owns < cutover
                stats.skip("before-cutover"); continue
            raw_amt = txn.get("amount")
            amount = _cents(raw_amt)
            if amount is None:
                if raw_amt not in (None, ""):
                    checks.append(QualityCheck(
                        name=f"simplefin.bad_amount.{account_id}", status="warn",
                        detail=f"unparseable amount {raw_amt!r} on {date}; skipped"))
                stats.skip("no-amount"); continue
            desc = squash(_text(txn.get("description")))
            payee = squash(_text(txn.get("payee")))
            if payee and payee.lower() not in desc.lower():
                desc = f"{payee} — {desc}" if desc else payee
            desc = desc or "(no description)"
            tid = str(txn.get("id") or f"{date}|{amount}|{desc}")
            t = Txn(date=date, account_id=account_id, description_raw=desc,
                    amount_cents=amount, source_file=path.name,
                    bank_type="simplefin", seq=_seq_from_id(tid), sf_tid=tid)
            extra = txn.get("extra")
            cat = extra.get("category") if isinstance(extra, dict) else None
            if cat:
                t.bank_category = str(cat)
            if meta.get("kind") == "brokerage":
                # the wording is read in the description's first characters, as a statement file's is stored: the
                # patterns' unbounded gaps would cost minutes on a feed description thousands of characters long, and
                # the description itself (the row's uid) is kept whole
                head = desc[:MAX_DESCRIPTION]
                if _TRADE_RE.search(head) or _OPTION_RE.search(head):
                    t.type = "investment"      # money moving within the brokerage
                    t.is_internal = True
                elif _DIVIDEND_RE.search(head):       # a cash dividend: income
                    t.type, t.category, t.subcategory = "dividend", INCOME_CATEGORY, "Dividends"
                elif _MARGIN_INT_RE.search(head):     # interest charged on a margin balance
                    t.type, t.category, t.subcategory = "fee", FEES_CATEGORY, "Margin Interest"
                elif _CASHBACK_RE.search(head):       # card rewards swept in — new money
                    t.type, t.category, t.subcategory = "income", INCOME_CATEGORY, "Reimbursement"
            txns.append(t)
            stats.observe(date, amount)

        # ---- investment holdings ----------------------------------------
        # every holding of an account takes the account's one class (Config.asset_class_of reads the same entry)
        held = acct.get("holdings") or []
        if not isinstance(held, list):
            _warn_once(*bad_row)
            held = []
        for h in held:
            if not isinstance(h, dict):
                _warn_once(*bad_row)
                continue
            value = _cents(h.get("market_value"))
            if value is None:
                continue
            qty = _float(h.get("shares"))
            cost = _cents(h.get("cost_basis"))
            if not cost:    # SimpleFIN often sends 0; reconstruct from avg cost/share
                pp = _float(h.get("purchase_price"))
                if pp and qty:
                    cost = round(pp * qty * 100)
            holdings.append(Holding(
                account=meta["name"], institution=meta["institution"],
                owner=meta["owner"], symbol=(_text(h.get("symbol")) or None),
                name=(_text(h.get("description")) or _text(h.get("symbol")) or "?"),
                asset_class=account_asset_class(meta),
                quantity=qty, value_cents=value,
                cost_basis_cents=cost, as_of=(bal_date or snap_date or ""),
                liquid=True, notes="simplefin", account_id=account_id))

    return txns, balances, holdings, stats, checks, earliest_raw

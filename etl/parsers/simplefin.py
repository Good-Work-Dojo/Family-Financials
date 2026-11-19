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
    if not epoch:
        return None
    return dt.datetime.fromtimestamp(int(epoch), zone).date().isoformat()


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
    name raises ValueError before the file is read."""
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

    doc = json.loads(path.read_text())
    for acct in sorted(doc.get("accounts", []), key=_is_historical):
        sfid = acct.get("id")
        account_id = sfid_to_account.get(sfid)
        if not account_id:
            if sfid in ignore:        # expected unmapped (duplicate twin / dead account)
                continue
            org = (acct.get("org") or {}).get("name", "?")
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
        for txn in (() if balances_only else acct.get("transactions", [])):
            stats.rows_total += 1
            if txn.get("pending"):
                stats.skip("pending"); continue
            date = _iso(txn.get("posted") or txn.get("transacted_at"), zone)
            if not date:
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
            desc = squash(txn.get("description") or "")
            payee = squash(txn.get("payee") or "")
            if payee and payee.lower() not in desc.lower():
                desc = f"{payee} — {desc}" if desc else payee
            desc = desc or "(no description)"
            tid = str(txn.get("id") or f"{date}|{amount}|{desc}")
            t = Txn(date=date, account_id=account_id, description_raw=desc,
                    amount_cents=amount, source_file=path.name,
                    bank_type="simplefin", seq=_seq_from_id(tid), sf_tid=tid)
            cat = (txn.get("extra") or {}).get("category")
            if cat:
                t.bank_category = str(cat)
            if meta.get("kind") == "brokerage":
                if _TRADE_RE.search(desc) or _OPTION_RE.search(desc):
                    t.type = "investment"      # money moving within the brokerage
                    t.is_internal = True
                elif _DIVIDEND_RE.search(desc):       # a cash dividend: income
                    t.type, t.category, t.subcategory = "dividend", INCOME_CATEGORY, "Dividends"
                elif _MARGIN_INT_RE.search(desc):     # interest charged on a margin balance
                    t.type, t.category, t.subcategory = "fee", FEES_CATEGORY, "Margin Interest"
                elif _CASHBACK_RE.search(desc):       # card rewards swept in — new money
                    t.type, t.category, t.subcategory = "income", INCOME_CATEGORY, "Reimbursement"
            txns.append(t)
            stats.observe(date, amount)

        # ---- investment holdings ----------------------------------------
        # every holding of an account takes the account's one class (Config.asset_class_of reads the same entry)
        for h in acct.get("holdings") or []:
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
                owner=meta["owner"], symbol=(h.get("symbol") or None),
                name=(h.get("description") or h.get("symbol") or "?"),
                asset_class=account_asset_class(meta),
                quantity=qty, value_cents=value,
                cost_basis_cents=cost, as_of=(bal_date or snap_date or ""),
                liquid=True, notes="simplefin", account_id=account_id))

    return txns, balances, holdings, stats, checks, earliest_raw

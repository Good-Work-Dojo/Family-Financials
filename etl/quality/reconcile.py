# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Reconciliation & data-quality checks."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from ..dates import month_of
from ..models import SPEND_TYPES, UNCATEGORIZED_SUB, BalanceObs, QualityCheck, Txn
from ..money import fmt


def statement_summary_check(account_id: str, summary: dict, txns: list[Txn]) -> list[QualityCheck]:
    """A parser that returns `extra["summary"]` hands over the totals its statement prints. Checks the parsed
    rows against them: total credits, total debits and, when both balances are given, the roll-forward."""
    checks = []
    credits = sum(t.amount_cents for t in txns if t.amount_cents > 0)
    debits = sum(t.amount_cents for t in txns if t.amount_cents < 0)
    for key, got in (("total_credits", credits), ("total_debits", debits)):
        want = summary.get(key)
        if want is None:
            continue
        ok = want == got
        checks.append(QualityCheck(
            name=f"{account_id}.{key}",
            status="pass" if ok else "fail",
            detail=f"statement says {fmt(want)}, parsed rows sum to {fmt(got)}"))
    beg, end = summary.get("beginning_balance"), summary.get("ending_balance")
    if beg is not None and end is not None:
        net = sum(t.amount_cents for t in txns)
        ok = beg + net == end
        checks.append(QualityCheck(
            name=f"{account_id}.balance_rollforward",
            status="pass" if ok else "fail",
            detail=f"beginning {fmt(beg)} + net {fmt(net)} vs ending {fmt(end)}"))
    return checks


def rollforward_check(account_id: str,
                      chains: list[list[tuple[str, int, int | None] | None]]) -> QualityCheck:
    """`import.rollforward.<account>`: does each row's running balance in the account's statement files equal the one
    before it plus the row's amount? `chains` holds one list per file (a format's `running_balances`): its rows in the
    order they happened, each (date, amount, balance or None) for a row read and None for a skipped row that moved the
    balance. Such a row, or a row with no balance, cannot be followed: the check restarts after it and counts it.

    Fails with the date of the first row that breaks the chain (the earliest across the files) and how many of the rows
    checked break it; passes when at least one row was checked and none breaks; `info` when no row could be checked.
    The words never state an amount or a balance."""
    checked = broke = unfollowed = 0
    first: str | None = None
    for chain in chains:
        before: int | None = None
        for item in chain:
            if item is None or item[2] is None:
                unfollowed += 1
                before = None
                continue
            date, amount, balance = item
            if before is not None:
                checked += 1
                if before + amount != balance:
                    broke += 1
                    first = date if first is None else min(first, date)
            before = balance
    restarts = (f" The check restarts after {unfollowed} row{'s' if unfollowed != 1 else ''} it cannot follow (a "
                f"skipped row that moved the balance, or a row with no balance).") if unfollowed else ""
    name = f"import.rollforward.{account_id}"
    if broke:
        return QualityCheck(
            name=name, status="fail",
            detail=f"The running balance in this account's statement files does not follow from its rows: it first "
                   f"breaks on {first}, and {broke} of the {checked} rows checked {'breaks' if broke == 1 else 'break'} "
                   f"it. A row is missing or altered in a file: download it again from the bank.")
    if checked:
        return QualityCheck(
            name=name, status="pass",
            detail=f"Each row's running balance is the one before it plus the row's amount ({checked} "
                   f"row{'s' if checked != 1 else ''} followed).{restarts}")
    return QualityCheck(
        name=name, status="info",
        detail=f"No row's running balance can be followed from the one before it: the files have too few rows with a "
               f"balance.{restarts}")


def ledger_drift_inputs(txns: list[Txn]) -> dict[str, tuple[list[Txn], bool]]:
    """Per account: every stored row and whether any of them came from the feed. The drift window sums
    ALL of an account's rows between the two balance dates — around a simplefin_cutover the CSV owns
    the days before it and the feed the days from it, so feed rows alone undercounted `got` for the
    ~2 weeks a cutover sat inside the window. Eligibility
    (at least one feed row) is the caller's, from the flag. Split parents are skipped: their
    children carry the amounts."""
    rows: dict[str, list[Txn]] = defaultdict(list)
    has_feed: dict[str, bool] = defaultdict(bool)
    for t in txns:
        if t.is_parent:          # a split parent is display-only — its children carry the amounts
            continue
        rows[t.account_id].append(t)
        has_feed[t.account_id] |= t.bank_type == "simplefin"
    return {a: (rows[a], has_feed[a]) for a in rows}


def ledger_drift(account_id: str, name: str, obs: list[BalanceObs], rows: list[Txn],
                 min_span_days: int = 7) -> tuple[QualityCheck, dict] | None:
    """Did the feed's own balance move by what its posted rows sum to? Compares the newest
    SimpleFIN observation d2 with the newest one at least `min_span_days` earlier d1:
    expected = bal(d2) - bal(d1), got = sum of EVERY one of this account's rows (CSV and feed: one
    ledger per account) with d1 < date <= d2, drift = got - expected. Always `info`: SimpleFIN balances include pending
    items the parser skips, so drift ~= the pending total on most runs. Clock-free. None without
    two qualifying obs."""
    mine = sorted((b for b in obs if b.account_id == account_id and b.source == "simplefin"),
                  key=lambda b: b.as_of)
    if len(mine) < 2:
        return None
    newest = mine[-1]
    cutoff = (date.fromisoformat(newest.as_of) - timedelta(days=min_span_days)).isoformat()
    older = [b for b in mine if b.as_of <= cutoff]
    if not older:
        return None
    start = older[-1]
    d1, d2 = start.as_of, newest.as_of
    expected = newest.balance_cents - start.balance_cents
    got = sum(t.amount_cents for t in rows if t.account_id == account_id and d1 < t.date <= d2)
    drift = got - expected
    check = QualityCheck(
        name=f"reconcile.{account_id}.ledger_drift", status="info",
        detail=f"{name}: ledger moved {fmt(got)} vs feed balance moved {fmt(expected)} over {d1}→{d2} "
               f"(drift {fmt(drift)}; pending items are the usual cause)")
    return check, {"from": d1, "to": d2, "expected_cents": expected, "got_cents": got, "drift_cents": drift}


def holdings_coverage(account_id: str, name: str, balance_cents: int,
                      holdings_cents: int, is_margin: bool = False) -> QualityCheck:
    """Self-contained coverage tripwire for a live SimpleFIN brokerage. Net worth books
    the broker's own `balance` (authoritative); the holdings should decompose ~all of it.
    The remainder (balance − holdings) is an uninvested cash sweep when positive or a
    margin loan when negative. Warn only when holdings materially EXCEED the balance on a
    non-margin account (overvalued/stale holdings or undisclosed margin we aren't booking)
    or cover less than half of it (a dropped/partial holdings feed). Uses only live feed
    data: the account's balance and its holdings."""
    cn = f"simplefin.holdings_coverage.{account_id}"
    remainder = balance_cents - holdings_cents          # +cash sweep / -margin loan
    pct = (remainder / balance_cents * 100) if balance_cents else 0
    base = (f"{name}: holdings {fmt(holdings_cents)} vs balance {fmt(balance_cents)}; "
            f"remainder {fmt(remainder)} ({pct:+.1f}%)")
    if is_margin:
        status, note = "info", "margin loan, booked live"
    elif remainder < -100 and pct <= -1.0:
        status, note = "warn", "holdings exceed balance — stale/overvalued holdings or unflagged margin"
    elif pct >= 50.0 and balance_cents > 500_00:
        status, note = "warn", "holdings cover <half the balance — feed may have dropped holdings"
    elif abs(pct) < 1.0:
        status, note = "info", "holdings ≈ balance (fully invested)"
    else:
        status, note = "info", "uninvested cash sweep within balance"
    return QualityCheck(name=cn, status=status, detail=f"{base} — {note}")


def account_month_sums(txns: list[Txn]) -> dict:
    """Per-account-per-month signed sums — the raw-vs-parsed eyeball table."""
    sums: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t in txns:
        if t.is_parent:          # a split parent is display-only
            continue
        sums[t.account_id][month_of(t.date)] += t.amount_cents
    return {a: dict(sorted(m.items())) for a, m in sorted(sums.items())}


def coverage(txn_windows: dict[str, tuple[str | None, str | None]]) -> list[QualityCheck]:
    checks = []
    spans = [(a, w) for a, w in sorted(txn_windows.items()) if w[0]]
    if not spans:
        return checks
    overall_start = min(w[0] for _, w in spans)
    overall_end = max(w[1] for _, w in spans)
    for account_id, (start, end) in spans:
        gaps = []
        if start > overall_start:
            gaps.append(f"no data before {start}")
        if end < overall_end:
            gaps.append(f"no data after {end}")
        checks.append(QualityCheck(
            name=f"coverage.{account_id}",
            status="info" if gaps else "pass",
            detail=f"{start} → {end}" + (f" ({'; '.join(gaps)})" if gaps else " (full range)")))
    return checks


def unmapped_vendors(txns: list[Txn], known: set[str], top_n: int = 25) -> list[tuple[str, int, int]]:
    """Fallback-named vendors ranked by |spend| — feeds config iteration.
    Rows a family fact already resolved (override/answer) don't rank: there is
    no config left to iterate on for them."""
    agg: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for t in txns:
        if t.type in SPEND_TYPES and t.vendor and t.vendor not in known \
                and not t.family_fact and not t.is_parent:
            agg[t.vendor][0] += abs(t.amount_cents)
            agg[t.vendor][1] += 1
    ranked = sorted(agg.items(), key=lambda kv: -kv[1][0])
    return [(v, s[0], s[1]) for v, s in ranked[:top_n]]


def uncategorized_share(txns: list[Txn]) -> tuple[float, QualityCheck]:
    spend = sum(-t.amount_cents for t in txns
                if t.type in SPEND_TYPES and t.amount_cents < 0 and not t.is_parent)
    uncat = sum(-t.amount_cents for t in txns
                if t.type in SPEND_TYPES and t.amount_cents < 0 and not t.is_parent
                and t.subcategory == UNCATEGORIZED_SUB)
    share = (uncat / spend) if spend else 0.0
    status = "pass" if share < 0.05 else "warn"
    return share, QualityCheck(
        name="categorization.uncategorized_share", status=status,
        detail=f"{share:.1%} of gross spending is Uncategorized ({fmt(uncat)} of {fmt(spend)})")

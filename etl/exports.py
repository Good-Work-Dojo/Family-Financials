# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""JSON export layer: everything the dashboard consumes lives in data/exports/.
Amounts exported as dollars (floats, 2dp); dates ISO."""
from __future__ import annotations

import bisect
import json
import os
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from . import manual_assets, networth
from .dates import month_of
from .models import (
    ASSET_CLASSES,
    INCOME_OTHER_SUB,
    INCOME_TYPES,
    LIABILITY_CLASSES,
    SPEND_TYPES,
    AssetRow,
    BalanceObs,
    Holding,
    QualityCheck,
    Txn,
    account_asset_class,
)


def _d(cents: int | None) -> float | None:
    return None if cents is None else round(cents / 100.0, 2)


EXPORT_MODE = 0o644      # inside the owner's 0700 data folder: the web server reads it as the same user


def write_json(path: Path, obj, *, durable: bool = False) -> None:
    """Atomic, and never through a link: a new file made beside the name (O_EXCL, so nothing already there is opened),
    filled, then renamed over the name, which replaces a link planted there instead of writing through it — the web
    server serves the exports folder and follows links, so a link at an export's name could hand it another file of its
    own. The exports are derived (the next ingest rebuilds them), so they keep the plain behaviour. `durable=True` is
    for state that outlives its inputs: the new file is fsynced before the rename, and the directory after it
    (best-effort - not every filesystem or mount allows it), so a power cut can leave the old file or the new one,
    never a torn or empty one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), EXPORT_MODE)
            f.write(data)
            if durable:
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    if durable:
        try:
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass


# Export contract version. Bump on ANY change to an export's shape — a new, removed or
# renamed key or file. The app pins the value it was built against
# (app/src/data/types.ts EXPECTED_SCHEMA_VERSION) and contract.test.ts asserts equality,
# so a frontend and a set of exports that disagree fail their gate. A key or file is removed or renamed only in a
# major release. Each version after the baseline gets a line here: the date and what changed.
#   1–22 — versions before 1.0.
#  23 — the 1.0 baseline.
SCHEMA_VERSION = 23

# ai_status.json's keys, in the order written (etl/enrich/ai.py status says what each holds)
AI_STATUS_KEYS = ("configured", "enabled", "provider", "model", "endpoint_host", "policy_version", "scope",
                  "consent_policy", "caps", "last_run", "answers_stored", "applied", "skipped", "preview")

# The projection defaults the dashboard starts from, for each assumptions.yaml `projections` key a config leaves out:
# yearly returns, inflation, the withdrawal rate and the two tax rates, all in percent. The state income tax is 0
# until a household sets its own state's rate. docs/configuration.md and starter/assumptions.yaml state the same
# values (tests/test_minimal_household.py, tests/test_starter.py).
PROJECTION_DEFAULTS: Mapping[str, object] = MappingProxyType({
    "return_scenarios_pct": (5, 7, 9),
    "inflation_pct": 3.0,
    "withdrawal_rate_pct": 4.0,
    "ltcg_pct": 15.0,
    "state_income_tax_pct": 0.0,
})


def export_projections(stated: Mapping) -> dict:
    """assumptions.json `projections`: every key assumptions.yaml states, in its order and as written, then each
    PROJECTION_DEFAULTS key it leaves out at its default, in the table's order. A key written as null counts as left
    out and takes its default in its own place."""
    out = dict(stated)
    for key, default in PROJECTION_DEFAULTS.items():
        if out.get(key) is None:
            out[key] = list(default) if isinstance(default, tuple) else default
    return out


def export_projections_stated(stated: Mapping) -> list[str]:
    """assumptions.json `projections_stated`: the `projections` keys assumptions.yaml states, sorted — the ones
    `projections` carries as written rather than at a default. A key written as null is not stated."""
    return sorted(k for k, v in stated.items() if v is not None)


def export_asset_classes() -> dict:
    """assumptions.json `asset_classes`: every class the engine knows (ASSET_CLASSES), the assets then the debts
    (LIABILITY_CLASSES), each list in ASSET_CLASSES order."""
    return {"assets": [c for c in ASSET_CLASSES if c not in LIABILITY_CLASSES],
            "liabilities": [c for c in ASSET_CLASSES if c in LIABILITY_CLASSES]}


def export_manifest(*, generated_at: str, simplefin_fetched_at: str | None,
                    transaction_count: int, current_snapshot_date: str | None,
                    setup: Mapping[str, int | bool], files: list[str]) -> dict:
    """manifest.json. `setup` is what the household has configured, whatever data has arrived: `accounts` (the
    registry's size), `feed` (some account names a feed id), `import_files` (the statement files the accounts' globs
    match, each once) and `manual_assets` (the hand-stated values net worth counts, accounts.json `manual_assets`)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "simplefin_fetched_at": simplefin_fetched_at,
        "transaction_count": transaction_count,
        "current_snapshot_date": current_snapshot_date,
        "setup": {k: setup[k] for k in ("accounts", "feed", "import_files", "manual_assets")},
        # The files the run wrote, manifest.json itself included: the caller lists what it
        # wrote BEFORE this file is written. contract.test.ts asserts manifest.files equals
        # the schema'd list exactly, so a missing self-entry fails the first ingest into an
        # empty dir (a fresh clone, a restore, `make clean`). Hence a union here.
        "files": sorted(set(files) | {"manifest.json"}),
    }


def export_transactions(txns: list[Txn], owner_of: dict[str, str],
                        account_names: dict[str, str]) -> dict:
    return {
        "count": len(txns),
        "transactions": [{
            "id": t.uid, "date": t.date, "account": t.account_id,
            "account_name": account_names[t.account_id],
            "owner": owner_of[t.account_id],
            "description": t.description_raw, "vendor": t.vendor,
            "category": t.category, "subcategory": t.subcategory,
            "category_source": t.category_source,
            "amount": _d(t.amount_cents), "type": t.type,
            "is_internal": t.is_internal,
            "transfer_group_id": t.transfer_group_id,
            "tags": t.tags, "needs_review": t.needs_review,
            "earner": t.earner,
            "parent_id": t.parent_id, "is_parent": t.is_parent,
            "source_file": t.source_file,
        } for t in sorted(txns, key=lambda t: (t.date, t.account_id, t.uid))],
    }


def export_assets_timeseries(asset_rows: list[AssetRow], mortgage_included: dict[str, bool],
                             coverage: Mapping[str, tuple[float, Sequence[str]]]) -> dict:
    """One snapshot per `snapshot_date` of the rows. `coverage` maps a date to (the share of net-worth series with a
    row, the display names of those without one), exported as `coverage` (3 dp) and `missing` (sorted). The rows are
    exported largest first, and the snapshot's `source` is the first exported row's. `assets_total` sums the
    positive rows and `liabilities_total` the negative ones (a negative number), so the two add up to `total`. A row
    carries `manual_id`, the manual asset it is (null for an account's row), and `carried_back`, true for a manual
    asset before its first value, held at that value (its `as_of` is that value's date)."""
    by_date: dict[str, list[AssetRow]] = defaultdict(list)
    for r in asset_rows:
        by_date[r.snapshot_date].append(r)
    snapshots = []
    for date in sorted(by_date):
        rows = sorted(by_date[date], key=lambda r: -abs(r.value_cents))
        by_owner: dict[str, int] = defaultdict(int)
        by_class: dict[str, int] = defaultdict(int)
        for r in rows:
            by_owner[r.owner] += r.value_cents
            by_class[r.asset_class] += r.value_cents
        assets = sum(r.value_cents for r in rows if r.value_cents > 0)
        liabilities = sum(r.value_cents for r in rows if r.value_cents < 0)
        total = assets + liabilities
        house_car = sum(r.value_cents for r in rows if r.asset_class in ("House", "Car"))
        share, missing = coverage[date]
        snapshots.append({
            "snapshot_date": date,
            "source": rows[0].source,
            "total": _d(total),
            "assets_total": _d(assets),
            "liabilities_total": _d(liabilities),
            "total_excl_house_car": _d(total - house_car),
            "mortgage_included": mortgage_included.get(date, False),
            "liquid_total": _d(sum(r.value_cents for r in rows if r.liquid)),
            "by_owner": {k: _d(v) for k, v in sorted(by_owner.items())},
            "by_class": {k: _d(v) for k, v in sorted(by_class.items())},
            "coverage": round(share, 3),
            "missing": sorted(missing),
            "rows": [{
                "account": r.account, "account_id": r.account_id,
                "institution": r.institution, "owner": r.owner,
                "asset_class": r.asset_class, "value": _d(r.value_cents),
                "liquid": r.liquid, "as_of": r.as_of, "as_of_assumed": r.as_of_assumed,
                "needs_review": r.needs_review, "source": r.source,
                "manual_id": r.manual_id, "carried_back": r.carried_back,
            } for r in rows],
        })
    return {"snapshots": snapshots}


def export_manual_assets(assets: Sequence[manual_assets.ManualAsset]) -> list[dict]:
    """accounts.json `manual_assets` and `manual_overridden`, one shape: the effective list (manual_assets.effective,
    in its order), or the config entries an app asset hides (manual_assets.overridden), each asset's id,
    name, institution (null when it names none), owner, class, liquidity, acquisition day (null when it states none),
    dated values oldest first, each `{as_of, value}` in dollars, and origin (`app` or `config`)."""
    return [{"id": a.id, "name": a.name, "institution": a.institution, "owner": a.owner, "asset_class": a.asset_class,
             "liquid": a.liquid, "acquired": None if a.acquired is None else a.acquired.isoformat(),
             "values": [{"as_of": day.isoformat(), "value": _d(cents)} for day, cents in a.values],
             "origin": a.origin} for a in assets]


def export_holdings(holdings: list[Holding]) -> dict:
    return {"holdings": [{
        "account": h.account, "account_id": h.account_id,
        "institution": h.institution, "owner": h.owner,
        "symbol": h.symbol, "name": h.name, "asset_class": h.asset_class,
        "quantity": h.quantity, "value": _d(h.value_cents),
        "cost_basis": _d(h.cost_basis_cents), "as_of": h.as_of,
        "liquid": h.liquid, "notes": h.notes,
    } for h in holdings]}


def export_balances_timeseries(balance_obs: list[BalanceObs],
                               account_meta: dict[str, dict]) -> dict:
    """Per-account balance history. Balances are stored signed as the institution
    reports them (credit cards negative = owed); utilization = |balance|/limit. Each account's `asset_class` is the
    class its rows take (models.account_asset_class, which Config.asset_class_of reads), never null."""
    by_acct: dict[str, list[BalanceObs]] = defaultdict(list)
    for b in balance_obs:
        by_acct[b.account_id].append(b)
    series = {}
    for account_id in sorted(by_acct):
        pts = []
        for b in sorted(by_acct[account_id], key=lambda b: (b.as_of, b.source)):
            util = (round(abs(b.balance_cents) / b.limit_cents, 4)
                    if b.limit_cents else None)
            pts.append({"date": b.as_of, "balance": _d(b.balance_cents),
                        "available": _d(b.available_cents), "limit": _d(b.limit_cents),
                        "utilization": util, "source": b.source})
        series[account_id] = pts
    accounts = {a: {"name": account_meta[a]["name"],
                    "institution": account_meta[a]["institution"],
                    "owner": account_meta[a]["owner"], "kind": account_meta[a]["kind"],
                    "asset_class": account_asset_class(account_meta[a])}
                for a in sorted(by_acct) if a in account_meta}
    return {"series": series, "accounts": accounts}


def export_networth_daily(series: networth.SeriesMap, current_total_cents: int | None,
                          current_snapshot_date: str | None) -> dict:
    """The daily net-worth line (networth.daily, read from the series map the snapshots read): per point its `date`,
    `networth` in dollars, `coverage` (3 dp, a partly covered day holds an account not yet observed flat at its first
    balance) and `source`, a fixed "simplefin". `current_total` and `current_snapshot_date` are the current
    snapshot's, carried beside the line; no point reads them. With no snapshot both are null: the net worth is not
    known yet, which a total of 0 would hide."""
    return {"series": [{"date": day, "networth": _d(cents), "coverage": round(coverage, 3), "source": "simplefin"}
                       for day, cents, coverage in networth.daily(series)],
            "current_total": _d(current_total_cents), "current_snapshot_date": current_snapshot_date}


# assumptions.yaml `coverage.min_share` when the household writes none (Config.coverage_min_share): the share of the
# cash-flow accounts with history whose history must have begun by a month for it to count in rates, baselines and
# averages
DEFAULT_COVERAGE_MIN_SHARE = 0.6


def in_coverage(months: Sequence[str], first_months: Sequence[str | None], min_share: float, *,
                waiting: int = 0) -> list[bool]:
    """Per month of `months` (YYYY-MM, in order), whether it is in coverage: of the cash-flow accounts with history
    (each one's first transaction month in `first_months`; None for an account with no transaction, which counts
    nowhere; and `waiting` more, whose history has not begun yet: they count in the whole and in no month), at least
    `min_share` have their first transaction in that month or earlier. An account that has begun never stops counting,
    so the flags are false up to a floor and true from it on, and neither a quiet account nor a thin newest month moves
    the floor. The share is compared exactly as written (Decimal of its text): 7 of 25 accounts meet 0.28, which
    0.28 * 25 in binary floating point (7.000000000000001) would refuse. No account that has begun: no month is in
    coverage."""
    starts = sorted(m for m in first_months if m is not None)
    if not starts:
        return [False] * len(months)
    needed = Decimal(str(min_share)) * (len(starts) + waiting)
    return [bisect.bisect_right(starts, month) >= needed for month in months]


def export_cashflow_monthly(txns: list[Txn],
                            windows: dict[str, tuple[str | None, str | None]],
                            cashflow_account_ids: list[str], *,
                            min_share: float, today: str | None = None) -> dict:
    """Per month: income, spend, invested, the savings rate, spend by category and income by subcategory, plus
    coverage: `accounts_covered` (the cash-flow accounts whose transaction window includes the month), `full_coverage`
    and `in_coverage` (the in_coverage rule at `min_share`, over each such account's first transaction month; an
    account whose first transaction is dated after `today`, the ingest day, has not begun until that day arrives, so
    the floor is never a month after the ingest day's; the pipeline always states `today`). A month
    is fully covered when every cash-flow account with any transaction covers it, so an account with no transaction
    at all (a certificate that only reports a balance) holds no month back, as it counts nowhere in `in_coverage`,
    while an account whose history begins later holds the months before it back; while no cash-flow account has a
    transaction no month is fully covered (their data is still to come), and a household with no cash-flow account
    at all has nothing to wait for, so every month is. `min_share` has no default: the
    caller states the household's share (Config.coverage_min_share, which falls back to DEFAULT_COVERAGE_MIN_SHARE).
    Top level: the cash-flow accounts, those with no transaction included, and `coverage_floor`, the first month in
    coverage, or null when no month is."""
    months: dict[str, dict] = defaultdict(lambda: {
        "income_cents": 0, "spend_cents": 0, "invested_cents": 0,
        "by_category": defaultdict(lambda: defaultdict(int)),
        "income_by_subcategory": defaultdict(int)})
    for t in txns:
        if t.is_parent:          # a split parent is display-only: the parts carry the spend and the categories
            continue
        m = months[month_of(t.date)]
        if t.type in INCOME_TYPES:
            m["income_cents"] += t.amount_cents
            m["income_by_subcategory"][t.subcategory or INCOME_OTHER_SUB] += t.amount_cents
        elif t.type in SPEND_TYPES:
            m["spend_cents"] -= t.amount_cents     # net of refunds
            m["by_category"][t.category or "?"][t.subcategory or "—"] += -t.amount_cents
        elif t.type == "investment" and t.account_id in cashflow_account_ids:
            m["invested_cents"] += -t.amount_cents

    # the cash-flow accounts with any transaction, in their listed order, each with its window's first and last month
    spans: dict[str, tuple[str, str]] = {}
    for a in cashflow_account_ids:
        first, last = windows.get(a) or (None, None)
        if first:
            spans[a] = (first[:7], last[:7])
    out = []
    for month in sorted(months):
        m = months[month]
        covered = [a for a, (first, last) in spans.items() if first <= month <= last]
        income, spend = m["income_cents"], m["spend_cents"]
        out.append({
            "month": month,
            "income": _d(income), "spend": _d(spend),
            "invested": _d(m["invested_cents"]),
            "savings_rate": round((income - spend) / income, 4) if income > 0 else None,
            "by_category": {c: {"total": _d(sum(subs.values())),
                                "by_subcategory": {s: _d(v) for s, v in sorted(subs.items())}}
                            for c, subs in sorted(m["by_category"].items())},
            "income_by_subcategory": {s: _d(v) for s, v
                                      in sorted(m["income_by_subcategory"].items())},
            "accounts_covered": covered,
            "full_coverage": len(covered) == len(spans) and (bool(spans) or not cashflow_account_ids),
        })
    ahead = {a for a in spans if today is not None and windows[a][0] > today}
    flags = in_coverage([m["month"] for m in out], [first for a, (first, _) in spans.items() if a not in ahead],
                        min_share, waiting=len(ahead))
    for m, flag in zip(out, flags, strict=True):
        m["in_coverage"] = flag
    floor = next((m["month"] for m in out if m["in_coverage"]), None)
    return {"months": out, "cashflow_accounts": cashflow_account_ids, "coverage_floor": floor}


def export_quality(file_stats, checks: list[QualityCheck], month_sums: dict,
                   unmapped: list[tuple[str, int, int]], overlaps: dict) -> dict:
    return {
        "files": [{
            "source_file": s.source_file, "parser": s.parser, "account": s.account,
            "rows_total": s.rows_total, "rows_parsed": s.rows_parsed,
            "rows_skipped": s.rows_skipped, "skip_reasons": s.skip_reasons,
            "duplicates_removed": s.duplicates_removed,
            "date_min": s.date_min, "date_max": s.date_max,
            "parsed_sum": _d(s.sum_cents),
        } for s in file_stats],
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail}
                   for c in checks],
        "account_month_sums": {a: {m: _d(v) for m, v in ms.items()}
                               for a, ms in month_sums.items()},
        "top_unmapped_vendors": [{"vendor": v, "spend": _d(s), "txns": n}
                                 for v, s, n in unmapped],
        "cross_file_duplicate_uids": overlaps,
    }


def export_vendors(rows: list[dict]) -> dict:
    """vendors.json: the vendor_registry rows, cents -> dollars, in registry order."""
    return {"count": len(rows), "vendors": [{
        "vendor": r["vendor"], "has_rule": r["has_rule"],
        "category": r["category"], "subcategory": r["subcategory"],
        "category_source": r["category_source"], "learn": r["learn"],
        "txns": r["txns"], "spend_ttm": _d(r["spend_ttm_cents"]),
        "first_seen": r["first_seen"], "last_seen": r["last_seen"],
    } for r in rows]}


def export_ai_status(status: Mapping) -> dict:
    """ai_status.json: etl/enrich/ai.py `status`, its keys in AI_STATUS_KEYS order (a missing one is a KeyError: the
    file is always whole)."""
    return {k: status[k] for k in AI_STATUS_KEYS}


def export_ledger_drift(payload: dict | None) -> dict | None:
    """accounts.json ledger_drift: the two SimpleFIN balance dates compared and
    expected / got / drift in dollars; None when not eligible or too little balance history."""
    if payload is None:
        return None
    return {"from": payload["from"], "to": payload["to"], "expected": _d(payload["expected_cents"]),
            "got": _d(payload["got_cents"]), "drift": _d(payload["drift_cents"])}


def export_category_renames(entries: list[dict] | None) -> list[dict]:
    """assumptions.json category_renames: the category_rules.yaml `renames:` list, normalized so the
    dashboard can mirror CategoryRenamer.map (app/src/lib/categoryRenames.ts). A `from` carries
    `subcategory` exactly when the YAML entry named one (a PAIR rename; the value may be null), a
    `to` carries it only when set. Nothing else about the entries is interpreted here."""
    out: list[dict] = []
    for r in entries or []:
        src, dst = r["from"], r["to"]
        frm: dict = {"category": src["category"]}
        if "subcategory" in src:
            frm["subcategory"] = src["subcategory"]
        to: dict = {"category": dst["category"]}
        if dst.get("subcategory") is not None:
            to["subcategory"] = dst["subcategory"]
        out.append({"from": frm, "to": to})
    return out

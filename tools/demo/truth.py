# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""truth.json: what the exports of a generated tree must equal, built from the plan and the record of what was
written — never by running the engine. Each row's figures are the `Expect` its behaviour declared; which rows the
engine stores, and under which uid, follows from the files and snapshots that list them:

- a statement file's rows are read file by file in name order; a row on or after the cutover of an account some
  snapshot reports is dropped (clipped), and a row an earlier file already listed (same uid) is a duplicate, stored
  once under the uid of the first file that lists it (two different ledger rows under one uid are refused);
- a feed row is stored once from the snapshots that list it, unless it is pending or dated before the account's
  cutover.

A split purchase (`Expect.split`) is declared as its parent, in no money total, and one part per leg under the uid
the engine gives a part; every count and total reads the parts. The pairs, the open questions and the gaps in the
data are read from the same declarations (`Expect.pair`, `question`, `gap`); the pending rows, which no export lists,
from the ledger, and how many of them each snapshot lists (the engine skips and counts them per file) from the record
of what was written. What the household knows beyond its rows is read from the plan as its behaviours declared it: the
regular charges it pays, the answers it gave (the rows they reach and those an override outranks), the vendors they
taught the engine, and the split rule it wrote. What the household has set up (its accounts, whether one names a feed
id, its statement files, its stated values) is counted from the config documents and the files written.

The balance sheet is what a person keeping the household's books writes down from the same reports: each account's
balance as the bank last reported it on or before a day (a feed snapshot's balance, pending rows included, or a
statement's own balance), each hand-stated value — an accounts.yaml entry or an asset the household entered in the
app, which replaces an entry of its id — at its value in force that day (its last value dated on or before it; before
its first value, at that same figure; nothing at all before the day it says it was acquired), a debt against the
total. Net worth's history starts in the month an account first reports a balance; a month end before an account's
first report lists it as missing, and an account the feed stopped reporting stays at its last report."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction
from typing import Any

from . import config_writer
from . import household as hh
from .dates import business_days_between, month_end, month_starts
from .ledger import Row, cents_of
from .plan import AI_REASONS, AI_WITHHELD, WINDOW_KEYS, Answer, Plan, manual_id
from .sources import Written
from .uids import feed_uid, file_uids, part_uid

# the document's shape: tests/demo_checks.py and app/src/test/truth.ts each read only the version they are written
# for, so a change to the shape bumps it here and in both readers
VERSION = 1
INTERNAL_CATEGORY = "Internal"
SPEND_TYPES = ("purchase", "fee")
INCOME_TYPES = ("income", "dividend", "interest")
CASHFLOW_KINDS = ("checking", "savings", "credit-card")
CARD_KIND = "credit-card"
MARGIN_CLASS, CASH_CLASS = "Margin", "Cash"
# what a statement file of each format says about the balance: the running balance after its newest row, the
# statement's ending balance on its last day, or nothing
FILE_BALANCE = {"chase_checking": "newest_row", "bofa_bank": "statement_end", "chase_card": None}
P2P_KEYS = ("debit", "credit", "zelle")   # the venmo_patterns wordings of a person-to-person leg
LEVEL_CENTS = 10_000_000              # net worth's milestones: every $100,000
DRIFT_SPAN_DAYS = 7                   # the drift compares two feed balances at least this many days apart
FALLBACK = ("Cash & Misc", "Uncategorized")   # where the fallback files a purchase no rule places
AI_MIN_SEEN = 2                       # household.yaml ai.min_seen's default: the rows a vendor needs to be asked about
AI_NOT_CANDIDATE = tuple(r for r in AI_REASONS if r not in AI_WITHHELD)


class _Stored:
    """The rows the engine stores, by uid, and the row -> uid map (by identity: two rows may be equal in every field
    on an account the feed does not number)."""

    def __init__(self) -> None:
        self.rows: dict[str, Row] = {}
        self.uid_of: dict[int, str] = {}
        self.seam: dict[str, dict] = {}

    def add(self, uid: str, row: Row) -> None:
        if uid in self.rows:
            raise ValueError(f"{row.account}: two rows share the uid {uid} ({row.date} {row.desc!r})")
        self.rows[uid] = row
        self.uid_of[id(row)] = uid


def stored_rows(plan: Plan, written: Written) -> _Stored:
    out = _Stored()
    for source in plan.profile.accounts:
        account, cutover = source.account, plan.cutover(source.account)
        clip = cutover is not None and written.reported(account)
        seen: set[str] = set()
        clipped, duplicates = 0, {}
        for f in written.files_of(account):
            repeats = 0
            for row, uid in zip(f.rows, file_uids(account, list(f.rows)), strict=True):
                if clip and row.date >= cutover:
                    clipped += 1
                elif uid in seen:
                    if out.rows[uid] is not row:
                        raise ValueError(f"{account}: two different rows share the uid {uid} ({row.date} "
                                         f"{row.desc!r}, in {f.name} and an earlier file)")
                    repeats += 1
                else:
                    seen.add(uid)
                    out.add(uid, row)
            if repeats:
                duplicates[f.name] = repeats
        for row in written.feed.get(account, ()):
            if cutover is None or row.date >= cutover:
                out.add(feed_uid(account, row), row)
        if cutover is not None:
            out.seam[account] = {"cutover": cutover.isoformat(), "clipped": clipped, "duplicates": duplicates}
    return out


@dataclass(frozen=True)
class _Line:
    """One row truth declares: a stored row, or one part of a stored split purchase (its parent's row, its own
    cents). `figures` is the row's entry in truth.json."""
    row: Row
    cents: int
    figures: dict

    @property
    def money(self) -> bool:
        """Does the line carry money? Every line but a split purchase's parent, whose parts carry its amount."""
        return not self.figures["is_parent"]


def _split_legs(row: Row) -> tuple[tuple[str, str | None, int], ...]:
    """A split purchase's declared legs, refused unless the purchase is spending nobody reviews and the legs are at
    least two, each of the purchase's sign, adding up to it exactly."""
    e, legs = row.expect, row.expect.split
    if e.type not in SPEND_TYPES or e.internal or e.needs_review or len(legs) < 2 or sum(c for *_, c in legs) != \
            row.cents or any(c == 0 or (c > 0) != (row.cents > 0) for *_, c in legs):
        raise ValueError(f"{row.account}: a split purchase is unreviewed spending whose two or more parts, each of its "
                         f"sign, add up to it ({row.date} {row.desc!r})")
    return legs


def _lines(stored: _Stored) -> dict[str, _Line]:
    """Every row truth declares, by uid: each stored row as its `Expect` says, and for a split purchase the parent
    (never flagged, in no money total) followed by one part per leg (`source` split, the leg's category and cents,
    the parent's vendor), each under the uid the engine gives a part."""
    out: dict[str, _Line] = {}
    for uid, row in stored.rows.items():
        e = row.expect
        if e is None:
            raise ValueError(f"{row.account}: a stored row declares no expectation ({row.date} {row.desc!r})")
        figures = {"type": e.type, "category": e.category, "subcategory": e.subcategory, "source": e.source,
                   "vendor": e.vendor, "needs_review": e.needs_review, "internal": e.internal, "earner": e.earner,
                   "is_parent": e.split is not None, "parent": None}
        out[uid] = _Line(row, row.cents, figures)
        if e.split is None:
            continue
        for index, (category, subcategory, cents) in enumerate(_split_legs(row), start=1):
            out[part_uid(uid, index)] = _Line(row, cents, {
                **figures, "category": category, "subcategory": subcategory, "source": "split", "needs_review": False,
                "is_parent": False, "parent": uid})
    return out


def _months(lines: dict[str, _Line], accounts: dict[str, dict]) -> dict[str, dict]:
    """Per month with a stored money row: income, spending (in total and by category) and what checking, savings and
    the cards sent to investments, and `full_coverage` — whether every cash-flow account with a stored row had started
    and not yet stopped transacting that month (its window, from its first stored row's month to its last's, holds the
    month). An account with no row covers no month and holds none back, but while no cash-flow account has a row no
    month is fully covered; a household with no cash-flow account misses none."""
    windows = [(w["first"], w["last"]) for a, w in accounts.items() if hh.ACCOUNTS[a].kind in CASHFLOW_KINDS]
    started = [(first, last) for first, last in windows if first is not None]
    months: dict[str, dict] = defaultdict(lambda: {"income_cents": 0, "spend_cents": 0, "by_category": {},
                                                   "invested_cents": 0})
    for line in lines.values():
        if not line.money:
            continue
        m, f = months[line.row.date.isoformat()[:7]], line.figures
        if f["type"] in INCOME_TYPES:
            m["income_cents"] += line.cents
        elif f["type"] in SPEND_TYPES:
            m["spend_cents"] -= line.cents
            m["by_category"][f["category"]] = m["by_category"].get(f["category"], 0) - line.cents
        elif f["type"] == "investment" and hh.ACCOUNTS[line.row.account].kind in CASHFLOW_KINDS:
            m["invested_cents"] -= line.cents
    for month, m in months.items():
        m["full_coverage"] = (bool(started) or not windows) and all(first[:7] <= month <= last[:7]
                                                                    for first, last in started)
    return {k: months[k] for k in sorted(months)}


def _accounts(plan: Plan, lines: dict[str, _Line]) -> dict[str, dict]:
    """Per account its money rows (parts and ordinary rows, never a parent: what the manifest counts), their sum and
    the days of the first and the last."""
    out = {}
    for source in plan.profile.accounts:
        mine = sorted((x for x in lines.values() if x.money and x.row.account == source.account),
                      key=lambda x: x.row.date)
        out[source.account] = {"rows": len(mine), "sum_cents": sum(x.cents for x in mine),
                               "first": mine[0].row.date.isoformat() if mine else None,
                               "last": mine[-1].row.date.isoformat() if mine else None}
    return out


def _income_phases(rows: dict[str, Row]) -> list[dict]:
    """One phase per (earner, employer) over the income deposits an income rule names both for, in the household's
    member order, then by first deposit, then by employer."""
    groups: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for row in rows.values():
        e = row.expect
        if e.type == "income" and e.category == "Income" and e.earner and e.employer:
            groups[(e.earner, e.employer)].append(row)
    rank = {m.name: i for i, m in enumerate(hh.MEMBERS)}
    phases = [{"earner": earner, "employer": employer, "first": min(r.date for r in g).isoformat(),
               "last": max(r.date for r in g).isoformat(), "deposits": len(g), "total_cents": sum(r.cents for r in g)}
              for (earner, employer), g in groups.items()]
    return sorted(phases, key=lambda p: (rank.get(p["earner"], len(rank)), p["earner"], p["first"], p["employer"]))


def _coverage_floor(plan: Plan, rows: dict[str, Row]) -> str | None:
    """The rule as the household reads it: rates start in the first month (of the months with data) by whose end
    enough of the cash-flow accounts that have data have had their first row — at least `coverage.min_share` of
    them, the share read exactly as the config writes it."""
    share = Fraction(str(config_writer.assumptions(plan)["coverage"]["min_share"]))
    first_month = {account: min(r.date for r in rows.values() if r.account == account).strftime("%Y-%m")
                   for account in {r.account for r in rows.values()}
                   if hh.ACCOUNTS[account].kind in CASHFLOW_KINDS}
    total = len(first_month)
    for month in sorted({r.date.strftime("%Y-%m") for r in rows.values()}):
        opened = sum(1 for first in first_month.values() if first <= month)
        if total and opened * share.denominator >= share.numerator * total:
            return month
    return None


def _person_to_person(row: Row, patterns: dict) -> bool:
    """Does the row's descriptor carry the payment-app or Zelle wording of the generated transfer_rules.yaml (case
    ignored)? A pair's Zelle legs name the household itself, so none of them is a payment to someone else."""
    return any(re.search(patterns[key], row.desc, re.IGNORECASE) for key in P2P_KEYS if patterns.get(key))


def _pairs(plan: Plan, rows: dict[str, Row], uid_of: dict[int, str]) -> list[dict]:
    """Each pair the rows declare (`Expect.pair`): its type and the uids of its outgoing and incoming legs, in the
    order of the outgoing leg's date, then its uid. Both legs must be stored, opposite, on two accounts, expect the
    same internal type under the internal category, and lie within the pairing window of the generated
    assumptions.yaml that applies: the payment-app one when a leg is person-to-person, the bank one otherwise."""
    docs = config_writer.documents(plan)
    bank, p2p = (docs["assumptions.yaml"][key] for key in WINDOW_KEYS)
    patterns = docs["transfer_rules.yaml"].get("venmo_patterns") or {}
    legs: dict[str, list[Row]] = defaultdict(list)
    for row in rows.values():
        if row.expect.pair is not None:
            legs[row.expect.pair].append(row)
    out = []
    for key, pair in legs.items():
        if len(pair) != 2:
            raise ValueError(f"pair {key!r}: {len(pair)} stored legs, not two")
        a, b = sorted(pair, key=lambda r: r.cents)
        ea, eb = a.expect, b.expect
        if (a.cents + b.cents != 0 or a.account == b.account or ea.type != eb.type or not (ea.internal and eb.internal)
                or not ea.category == eb.category == INTERNAL_CATEGORY):
            raise ValueError(f"pair {key!r}: its legs are not two opposite internal rows of one type on two accounts")
        window = p2p if _person_to_person(a, patterns) or _person_to_person(b, patterns) else bank
        if business_days_between(a.date, b.date) > window:
            raise ValueError(f"pair {key!r}: its legs ({a.date}, {b.date}) lie further apart than the pairing "
                             f"window of {window} business days")
        out.append((a.date, uid_of[id(a)], {"type": ea.type, "out": uid_of[id(a)], "into": uid_of[id(b)]}))
    return [p for _, _, p in sorted(out, key=lambda p: (p[0], p[1]))]


def _data_gaps(rows: dict[str, Row]) -> list[dict]:
    """One gap per (account, counter hint) over the rows that declare one (`Expect.gap`), in that order: the rows'
    count and their net amount. The hint names an account whose stored rows begin on or before each gap row's day
    and end on or after it: only inside that account's data is a missing leg a gap in the data."""
    days: dict[str, list[date]] = defaultdict(list)
    for row in rows.values():
        days[row.account].append(row.date)
    groups: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for row in rows.values():
        if row.expect.gap is not None:
            if not row.expect.internal:
                raise ValueError(f"{row.account}: a gap in the data is an internal row ({row.date} {row.desc!r})")
            covered = days.get(row.expect.gap)
            if not covered or not min(covered) <= row.date <= max(covered):
                raise ValueError(f"{row.account}: a gap row on {row.date} lies outside the data window of "
                                 f"{row.expect.gap} ({row.desc!r})")
            groups[(row.account, row.expect.gap)].append(row)
    return [{"account": account, "hint": hint, "rows": len(g), "net_cents": sum(r.cents for r in g)}
            for (account, hint), g in sorted(groups.items())]


def _pending(plan: Plan) -> dict[str, dict]:
    """Per account with pending rows: how many and their sum. The feed's balance holds them; no export lists one."""
    out = {}
    for account in plan.ledger.accounts():
        rows = [r for r in plan.ledger.rows(account) if r.pending]
        if rows:
            out[account] = {"rows": len(rows), "cents": sum(r.cents for r in rows)}
    return out


# ------------------------------------------------------------------ what the household knows and answered

def _recurring(plan: Plan) -> list[dict]:
    """The regular charges the household declares it pays, sorted by vendor, then account."""
    return sorted(({"vendor": r.vendor, "account": r.account, "cadence": r.cadence, "amount_mode": r.amount_mode,
                    "active": r.active, "price_changes": r.price_changes} for r in plan.recurring),
                  key=lambda r: (r["vendor"], r["account"]))


def _answers(plan: Plan, stored: _Stored) -> dict:
    """What the family's answers reach, as each answer declares it: `applied_rows`, the stored rows they reach (a
    row answer its row, a vendor answer the purchases its behaviour names), and `conflicts`, the keys of the answers
    an override outranks, sorted."""
    return {"applied_rows": sum(id(row) in stored.uid_of for a in plan.answers for row in a.reaches),
            "conflicts": sorted(_handle(a, stored) for a in plan.answers if a.outranked)}


def _learned(plan: Plan) -> list[dict]:
    return sorted(({"vendor": r.vendor, "category": r.category, "subcategory": r.subcategory, "scope": r.scope}
                   for r in plan.learned), key=lambda r: r["vendor"].casefold())


def _vendors(plan: Plan) -> dict[str, dict]:
    """vendors.json's row for each vendor the answers teach: the learned category, as the learned rule files it."""
    return {r.vendor: {"category": r.category, "subcategory": r.subcategory, "category_source": "learned"}
            for r in plan.learned}


def _splits(docs: dict, lines: dict[str, _Line]) -> dict | None:
    """The household's split rule (vendor_rules.yaml, a pattern with `split:`): its vendor, how many purchases it
    splits and its legs, each with its category, subcategory and `pct` or `remainder`. None without one."""
    entries = [e for e in docs["vendor_rules.yaml"].get("patterns") or () if e.get("split")]
    if not entries:
        return None
    if len(entries) > 1:
        raise ValueError("truth declares one split vendor")
    [entry] = entries
    return {"vendor": entry["vendor"],
            "purchases": sum(1 for x in lines.values() if x.figures["is_parent"] and x.figures["vendor"] == entry["vendor"]),
            "legs": [{"category": leg["category"], "subcategory": leg.get("subcategory"),
                      **({"pct": leg["pct"]} if "pct" in leg else {"remainder": leg["remainder"]})}
                     for leg in entry["split"]]}


# ------------------------------------------------------------------ the balance sheet

@dataclass(frozen=True)
class _Series:
    """One line of the balance sheet: an account's reported balances by day, or a stated value's dated values and,
    when the entry states it, the day it was acquired."""
    name: str
    asset_class: str
    account: str | None               # None for a stated value
    points: dict[date, int]
    acquired: date | None = None
    manual_id: str | None = None      # a stated value's id


def _reports(plan: Plan, written: Written, account: str, kind: str) -> dict[date, int]:
    """The balances reported for the account: each snapshot that lists it (its balance that day, pending rows
    included) and its statements' own balance (the newest one, which beats a snapshot's of the same day). A loan is
    a debt, which the ledger holds below zero; one it holds at zero or above is refused."""
    points = {s.day: plan.ledger.balance(account, s.day, with_pending=True)
              for s in written.snapshots if account in s.accounts}
    source = plan.sources(account)
    files = written.files_of(account)
    says = FILE_BALANCE[source.files.parser] if source.files is not None else None
    if says == "newest_row" and any(f.rows for f in files):
        day = max(r.date for f in files for r in f.rows)
        points[day] = plan.ledger.balance(account, day)
    elif says == "statement_end" and files:
        day = max(e.last for e in plan.exports(account))
        points[day] = plan.ledger.balance(account, day)
    if kind == "loan" and any(c >= 0 for c in points.values()):
        raise ValueError(f"{account}: a loan's reported balance is a debt, below zero; the ledger holds "
                         f"{max(points.values())}")
    return dict(sorted(points.items()))


def _series(plan: Plan, written: Written, docs: dict) -> list[_Series]:
    """Every account the balance sheet counts (cards when the household counts them), in profile order, under the
    asset class accounts.yaml states for it, then the stated values (_manual_assets)."""
    accounts = docs["accounts.yaml"]["accounts"]
    cards = (docs["household.yaml"].get("net_worth") or {}).get("include_credit_cards", True)
    out = []
    for source in plan.profile.accounts:
        entry = accounts[source.account]
        if entry["kind"] == CARD_KIND and not cards:
            continue
        if not entry.get("asset_class"):
            raise ValueError(f"{source.account}: accounts.yaml states no asset_class for it")
        out.append(_Series(entry["name"], entry["asset_class"], source.account,
                           _reports(plan, written, source.account, entry["kind"])))
    for a in _manual_assets(docs, plan):
        out.append(_Series(a["name"], a["asset_class"], None,
                           {date.fromisoformat(v["as_of"]): v["value_cents"] for v in a["values"]},
                           date.fromisoformat(a["acquired"]) if a["acquired"] else None, a["id"]))
    return out


def _last(points: dict[date, int], day: date) -> tuple[date, int] | None:
    before = [d for d in points if d <= day]
    return (max(before), points[max(before)]) if before else None


def _stated(s: _Series, day: date) -> tuple[date, int] | None:
    """A stated value on `day`: its last value on or before the day, its first one before that (the household owned
    it then too, at the earliest figure it gave), and None — not held, so neither a line nor missing — before the day
    it was acquired."""
    if s.acquired is not None and day < s.acquired:
        return None
    return _last(s.points, day) or min(s.points.items())


def _manual_assets(docs: dict, plan: Plan) -> list[dict]:
    """The hand-stated values, sorted by name and then id: each accounts.yaml entry under its id (manual_id of its
    name), with its holder, owner, class, liquidity, acquisition day (null when the entry states none) and its one
    value, from the config; and each asset the household entered in the app (`plan.manual_assets`), with no holder and
    no acquisition day and its dated values, from the app — an asset of an entry's id in place of that entry."""
    app = [{"id": a.id, "name": a.name, "institution": None, "owner": a.owner, "asset_class": a.asset_class,
            "liquid": a.liquid, "acquired": None,
            "values": [{"as_of": day.isoformat(), "value_cents": cents} for day, cents in a.values], "origin": "app"}
           for a in plan.manual_assets]
    taken = {a["id"] for a in app}
    config = [{"id": manual_id(e["account"]), "name": e["account"], "institution": e["institution"],
               "owner": e["owner"], "asset_class": e["asset_class"], "liquid": bool(e.get("liquid", False)),
               "acquired": e.get("acquired"), "values": [{"as_of": e["as_of"], "value_cents": cents_of(e["value"])}],
               "origin": "config"}
              for e in docs["accounts.yaml"].get("manual_entries") or ()]
    out = [*app, *(a for a in config if a["id"] not in taken)]
    names = [a["name"].casefold() for a in out]
    if len(set(names)) != len(names):
        raise ValueError(f"two hand-stated values share a name: {sorted(names)}")
    return sorted(out, key=lambda a: (a["name"].casefold(), a["id"]))


def _snapshot_dates(series: list[_Series]) -> list[date]:
    """Every month end from the month an account first reports a balance while it is before the newest report, then
    the newest report's day. A stated value starts no month; with no account reported, the newest stated value's day
    is the one date."""
    reported = [d for s in series if s.account for d in s.points]
    if not reported:
        stated = [d for s in series for d in s.points]
        return [max(stated)] if stated else []
    current = max(reported)
    return [*(month_end(m) for m in month_starts(min(reported), current) if month_end(m) < current), current]


def _live_positions(plan: Plan, written: Written) -> dict[str, tuple]:
    """Per account with a portfolio that the newest snapshot reports, the positions it lists."""
    if not written.snapshots:
        return {}
    newest = written.snapshots[-1]
    return {a: p.positions(plan.ledger.balance(a, newest.day, with_pending=True))
            for a, p in plan.portfolios.items() if a in newest.accounts}


def _snapshot(day: date, series: list[_Series], positions: dict[str, tuple], mortgage: str | None) -> dict:
    """The balance sheet on `day`: each account's last report on or before it, else the account is missing, and each
    stated value as `_stated` gives it (never missing), also listed as a manual row: its id, value, the date of the
    value it is held at and whether it is carried back (that date after the day). On the current day a brokerage whose
    positions the newest snapshot lists shows them at their gross value, and the rest of its balance apart: a margin
    loan when negative, cash when positive."""
    rows: list[tuple[str, int]] = []
    manual: list[dict] = []
    missing, booked = [], set()
    for s in series:
        if s.account is None:
            point = _stated(s, day)
            if point is not None:
                rows.append((s.asset_class, point[1]))
                manual.append({"id": s.manual_id, "value_cents": point[1], "as_of": point[0].isoformat(),
                               "carried_back": point[0] > day})
            continue
        point = _last(s.points, day)
        if point is None:
            missing.append(s.name)
            continue
        booked.add(s.account)
        held = positions.get(s.account)
        if held:
            gross = sum(p.value_cents for p in held)
            rows.append((s.asset_class, gross))
            if point[1] != gross:
                rows.append((MARGIN_CLASS if point[1] < gross else CASH_CLASS, point[1] - gross))
        else:
            rows.append((s.asset_class, point[1]))
    by_class: dict[str, int] = defaultdict(int)
    for cls, cents in rows:
        by_class[cls] += cents
    assets = sum(c for _, c in rows if c > 0)
    liabilities = sum(c for _, c in rows if c < 0)
    return {"date": day.isoformat(), "total_cents": assets + liabilities, "assets_cents": assets,
            "liabilities_cents": liabilities, "by_class": dict(sorted(by_class.items())), "missing": sorted(missing),
            "mortgage_included": mortgage is None or mortgage in booked,
            "manual_rows": sorted(manual, key=lambda r: r["id"])}


def _daily_points(series: list[_Series]) -> dict[str, int]:
    """The daily line on each day within the account reports' span (the first report to the last) on which a stated
    value takes a value or is acquired: every account with a report at its last report on or before the day, else at
    its first, held flat backwards, plus every stated value held that day (_stated). A day outside the span has no
    point; no account report, no line."""
    accounts = [s for s in series if s.account and s.points]
    if not accounts:
        return {}
    reported = {d for s in accounts for d in s.points}
    first, last = min(reported), max(reported)
    days = sorted({d for s in series if s.account is None
                   for d in (*s.points, *(() if s.acquired is None else (s.acquired,))) if first <= d <= last})
    out = {}
    for day in days:
        cents = sum((_last(s.points, day) or min(s.points.items()))[1] for s in accounts)
        cents += sum(p[1] for s in series if s.account is None and (p := _stated(s, day)) is not None)
        out[day.isoformat()] = cents
    return out


def _levels(first: int, last: int) -> list[int]:
    """The $100,000 milestones net worth passes going from `first` to `last` (either way), in cents."""
    lo, hi = sorted((first, last))
    return [k * LEVEL_CENTS for k in range(lo // LEVEL_CENTS + 1, hi // LEVEL_CENTS + 1)]


def _mortgage(docs: dict, series: list[_Series], dates: list[date]) -> dict | None:
    """The mortgage as the household's papers state it (its loan terms) and as the lender reports it: its balance
    on the current day and at every snapshot date it has a report by."""
    accounts = docs["accounts.yaml"]["accounts"]
    account = next((a for a, e in accounts.items() if e.get("mortgage")), None)
    if account is None:
        return None
    loan = accounts[account].get("loan") or {}
    points = next(s.points for s in series if s.account == account)
    at = {d: _last(points, d) for d in dates}
    current = at[dates[-1]] if dates else None

    def money(value) -> int | None:
        return None if value is None else cents_of(value)

    origination = loan.get("origination") or {}
    return {"account": account,
            "balance_cents": -current[1] if current else None,
            "balance_as_of": current[0].isoformat() if current else None,
            "rate_pct": loan.get("rate"), "monthly_payment_cents": money(loan.get("monthly_payment")),
            "monthly_escrow_cents": money(loan.get("monthly_escrow")),
            "escrow_balance_cents": money(loan.get("escrow_balance")),
            "origination": {"date": origination.get("date"), "amount_cents": money(origination.get("amount"))}
            if origination else None,
            "term_months": loan.get("term_months"),
            "schedule": [{"date": d.isoformat(), "principal_balance_cents": -p[1]} for d, p in at.items() if p]}


def _balance_sheet(plan: Plan, written: Written) -> dict:
    docs = config_writer.documents(plan)
    series = _series(plan, written, docs)
    dates = _snapshot_dates(series)
    positions = _live_positions(plan, written)
    mortgage = next((a for a, e in docs["accounts.yaml"]["accounts"].items() if e.get("mortgage")), None)
    snapshots = [_snapshot(d, series, positions if d == dates[-1] else {}, mortgage) for d in dates]
    month_ends = snapshots[:-1]
    comparable = [s for s in month_ends if not s["missing"]]
    reported = sorted({d for s in series if s.account for d in s.points})
    daily_last = None
    if reported:
        # the daily line's last point: every account at its last report on or before the newest report day, and
        # every stated value held that day at its figure for the day (_stated)
        last = reported[-1]
        on_last = [_last(s.points, last) if s.account else _stated(s, last) for s in series if s.points]
        daily_last = {"date": last.isoformat(), "total_cents": sum(p[1] for p in on_last if p is not None)}
    return {
        "snapshots": snapshots,
        "comparable_month_ends": [s["date"] for s in comparable],
        "partial_month_ends": [s["date"] for s in month_ends if s["missing"]],
        "daily_last": daily_last,
        "daily_points": _daily_points(series),
        # with no snapshot the net worth is not known yet: null, never 0
        "current_total_cents": snapshots[-1]["total_cents"] if snapshots else None,
        "levels_crossed": _levels(comparable[0]["total_cents"], comparable[-1]["total_cents"]) if comparable else [],
        "holdings": [{"account": a, "symbol": p.symbol, "value_cents": p.value_cents}
                     for a, held in positions.items() for p in held],
        "mortgage": _mortgage(docs, series, dates),
    }


# ------------------------------------------------------------------ the quality report

def _ledger_drift(plan: Plan, written: Written, stored: _Stored) -> dict[str, int]:
    """Per everyday account the feed reports with rows of its own: the stored rows between its newest feed balance
    and the newest one at least DRIFT_SPAN_DAYS before it, less what the balance moved over those days. The feed's
    balance holds pending rows and no stored row does, so the figure is minus the pending rows of those days (a
    pending purchase makes it positive)."""
    out = {}
    for source in plan.profile.accounts:
        account, cutover = source.account, plan.cutover(source.account)
        if not source.feed or hh.ACCOUNTS[account].kind not in CASHFLOW_KINDS or not any(
                cutover is None or r.date >= cutover for r in written.feed.get(account, ())):
            continue
        days = [s.day for s in written.snapshots if account in s.accounts]
        older = [d for d in days if d <= days[-1] - timedelta(days=DRIFT_SPAN_DAYS)] if days else []
        if not older:
            continue
        first, last = older[-1], days[-1]
        moved = (plan.ledger.balance(account, last, with_pending=True)
                 - plan.ledger.balance(account, first, with_pending=True))
        rows = sum(r.cents for r in stored.rows.values() if r.account == account and first < r.date <= last)
        out[account] = rows - moved
    return out


def _checks(plan: Plan, written: Written, accounts: dict[str, dict], drift: dict[str, int],
            holdings: list[dict], docs: dict, answers: dict) -> dict[str, str]:
    """Every check the ingest reports with a status other than pass: the warnings — an account the feed stopped
    reporting, a feed account nothing maps — and the notes: the feed read, statement rows the feed took over, the
    learned rules (however many), each account whose rows do not span the household's whole history, each ledger
    drift, each margin brokerage's holdings, the split rule's purchases, the answers applied and those an override
    outranked, and the overrides applied."""
    out: dict[str, str] = {"learned_rules.active": "info"}
    if any(e.get("split") for e in docs["vendor_rules.yaml"].get("patterns") or ()):
        out["splits.applied"] = "info"
    if plan.answers:
        out["answers.applied"] = "info"
    if answers["conflicts"]:
        out["answers.conflict"] = "info"
    if docs["overrides.yaml"]:
        out["overrides.applied"] = "info"
    if written.snapshots:
        out["simplefin.ingested"] = "info"
        newest = written.snapshots[-1].accounts
        for source in plan.profile.accounts:
            if source.feed and source.account not in newest:
                out[f"simplefin.stale.{source.account}"] = "warn"
        for u in plan.unmapped:
            out[f"simplefin.unmapped.{u.feed_id}"] = "warn"
    for source in plan.profile.accounts:
        cutover = plan.cutover(source.account)
        if cutover is not None and any(r.date >= cutover for f in written.files_of(source.account) for r in f.rows):
            out[f"simplefin.csv_clip.{source.account}"] = "info"
    spans = {a: (w["first"], w["last"]) for a, w in accounts.items() if w["first"]}
    if spans:
        first, last = min(f for f, _ in spans.values()), max(la for _, la in spans.values())
        for account, (f, la) in spans.items():
            if f > first or la < last:
                out[f"coverage.{account}"] = "info"
    for account in drift:
        out[f"reconcile.{account}.ledger_drift"] = "info"
    for account in sorted({h["account"] for h in holdings}):
        if plan.portfolios[account].loan_cents == 0:
            raise ValueError(f"{account}: only a margin account's holdings are declared")
        out[f"simplefin.holdings_coverage.{account}"] = "info"
    return dict(sorted(out.items()))


def _ai(plan: Plan, docs: dict, lines: dict[str, _Line]) -> dict:
    """What the AI step would make of the household's rows, from the vendors the behaviours declared (`declare_ai`):
    `sendable`, the vendors it asks about; `withheld`, each vendor it keeps back with the policy's reason; `not_candidate`,
    the vendors whose rows it never considers (a payment app's, an internal row's) with why; `rows`, every declared
    vendor's stored rows by uid; and `preview`, ai_status.json's counts (the vendors asked about, and how many each
    reason keeps back). A candidate-shaped row — a purchase the fallback filed on a card, the accounts the step reads
    by default — whose vendor nobody declared is refused, as is a declaration its rows contradict: a vendor asked about
    or kept back for its name or digits needs two rows or more, one kept back as seen too few times fewer."""
    kinds = {k: v["kind"] for k, v in docs["accounts.yaml"]["accounts"].items()}
    rows: dict[str, list[str]] = defaultdict(list)
    for uid, line in lines.items():
        f = line.figures
        shaped = (f["type"] == "purchase" and f["source"] == "fallback" and (f["category"], f["subcategory"]) == FALLBACK
                  and kinds.get(line.row.account) == CARD_KIND and not f["is_parent"] and f["parent"] is None)
        if f["vendor"] in plan.ai:
            rows[f["vendor"]].append(uid)
        elif shaped:
            raise ValueError(f"{line.row.account}: {f['vendor']!r} is a vendor the fallback files on a card, which the "
                             "plan does not declare for the AI step")
    sendable, withheld, never = [], {}, {}
    for vendor, reason in sorted(plan.ai.items()):
        seen = len(rows[vendor])
        if (reason == "seen") != (seen < AI_MIN_SEEN) and reason not in AI_NOT_CANDIDATE:
            raise ValueError(f"{vendor}: declared {reason or 'sendable'} with {seen} row(s)")
        key = vendor.casefold()
        if reason is None:
            sendable.append(key)
        elif reason in AI_NOT_CANDIDATE:
            never[key] = reason
        else:
            withheld[key] = reason
    return {"sendable": sorted(sendable), "withheld": dict(sorted(withheld.items())),
            "not_candidate": dict(sorted(never.items())),
            "rows": {vendor.casefold(): sorted(uids) for vendor, uids in sorted(rows.items())},
            "preview": {"vendors": len(sendable),
                        "withheld": {r: sum(v == r for v in withheld.values()) for r in AI_WITHHELD}}}


def _setup(plan: Plan, docs: dict, written: Written) -> dict:
    """What the household has set up, whether or not data has arrived: the generated config's accounts, whether one
    names a feed id, the statement files written for them (each named for its account's glob) and the values stated
    by hand, in the config or in the app."""
    accounts = docs["accounts.yaml"]["accounts"]
    return {"accounts": len(accounts),
            "feed": any(e.get("simplefin_id") or e.get("simplefin_id_history") for e in accounts.values()),
            "import_files": len(written.files),
            "manual_assets": len(_manual_assets(docs, plan))}


def _handle(value: object, stored: _Stored) -> Any:
    """A planted handle as truth.json names it: a row's stored uid (a pending row, which is never stored, by the id
    the feed lists it under), a review answer by its key, a date's ISO text, an account id, a rule name, a vendor, a
    feed id or a manual asset's id as it is, an amount in cents as it is, a tuple or list of those as a list, and the
    module switches (a mapping of module ids to true or false) as they are."""
    if isinstance(value, dict):
        if not all(isinstance(k, str) and isinstance(v, bool) for k, v in value.items()):
            raise ValueError(f"a planted mapping is module switches, a module id -> true or false, got {value!r}")
        return dict(sorted(value.items()))
    if isinstance(value, Answer):
        if isinstance(value.target, Row):
            return f"txn:{_handle(value.target, stored)}"
        return f"vendor:{value.target}"
    if isinstance(value, Row) and value.pending:
        if not value.feed_id:
            raise ValueError(f"{value.account}: a planted pending row has no feed id ({value.date} {value.desc!r})")
        return value.feed_id
    if isinstance(value, Row):
        uid = stored.uid_of.get(id(value))
        if uid is None:
            raise ValueError(f"{value.account}: a planted row is not stored ({value.date} {value.desc!r})")
        return uid
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [_handle(v, stored) for v in value]
    if isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    raise ValueError(f"a planted handle is a row, a date, a name, cents or a list of them, got {value!r}")


def build(plan: Plan, written: Written) -> dict:
    stored = stored_rows(plan, written)
    rows = stored.rows
    lines = _lines(stored)
    figures = {uid: line.figures for uid, line in lines.items()}
    accounts = _accounts(plan, lines)
    sheet = _balance_sheet(plan, written)
    drift = _ledger_drift(plan, written, stored)
    docs = config_writer.documents(plan)
    answers = _answers(plan, stored)
    return {
        "version": VERSION,
        "profile": plan.profile.name,
        "end": plan.end.isoformat(),
        "household": {"name": hh.NAME, "surname": hh.SURNAME, "joint_label": hh.JOINT, "timezone": hh.TIMEZONE,
                      "members": [{"name": m.name, "role": m.role, "birth_year": m.birth_year} for m in hh.MEMBERS]},
        "accounts": {s.account: {"kind": a.kind, "name": a.name, "institution": a.institution, "owner": a.owner}
                     for s in plan.profile.accounts for a in (hh.ACCOUNTS[s.account],)},
        "planted": {handle: _handle(value, stored) for handle, value in plan.planted.items()},
        "figures": {
            "setup": _setup(plan, docs, written),
            "manual_assets": _manual_assets(docs, plan),
            "rows": figures,
            "accounts": accounts,
            "months": _months(lines, accounts),
            "income_phases": _income_phases(rows),
            "coverage_floor": _coverage_floor(plan, rows),
            "seam": stored.seam,
            "pairs": _pairs(plan, rows, stored.uid_of),
            "internal_rows": sum(f["internal"] for f in figures.values()),
            "open_questions": sorted({r.expect.question for r in rows.values() if r.expect.question is not None}),
            "data_gaps": _data_gaps(rows),
            "pending": _pending(plan),
            "pending_listed": {s.name: s.pending for s in written.snapshots},
            "review": sorted(uid for uid, f in figures.items() if f["needs_review"]),
            **sheet,
            "ledger_drift": drift,
            "checks": _checks(plan, written, accounts, drift, sheet["holdings"], docs, answers),
            "recurring": _recurring(plan),
            "answers": answers,
            "learned": _learned(plan),
            "vendors": _vendors(plan),
            "splits": _splits(docs, lines),
            "ai": _ai(plan, docs, lines),
        },
    }

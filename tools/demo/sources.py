# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The inputs the generator writes from a plan besides the config: each account's statement files under `imports/`,
the feed snapshots under `store/simplefin/snapshots/` and a record of the rows each of them lists, which truth reads
to know what the engine will store; then, under `state/family/` (where the store keeps them), the family's review
answers, which name stored rows, and the assets it entered in the app."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from . import household as hh
from .ledger import FUTURE_DAYS, Row
from .plan import SNAPSHOT_HOUR, WINDOW_DAYS, Plan, UnmappedFeed
from .writers import bofa_bank, chase_card, chase_checking, feed, plain, state

WRITERS = {"chase_checking": chase_checking, "chase_card": chase_card, "bofa_bank": bofa_bank}
BALANCE_FEED_KINDS = ("brokerage", "loan")   # fed by the feed alone, such an account reports its balance and no rows


@dataclass(frozen=True)
class StatementFile:
    account: str
    name: str
    rows: tuple[Row, ...]              # in the order the file lists them


@dataclass(frozen=True)
class Snapshot:
    day: date
    accounts: frozenset[str]           # the registry accounts it reports
    name: str = ""                     # its file name
    pending: int = 0                   # the pending rows it lists, which the engine skips


@dataclass(frozen=True)
class Written:
    files: tuple[StatementFile, ...]   # every statement file, sorted by name
    snapshots: tuple[Snapshot, ...]    # the snapshots, oldest first
    feed: dict[str, tuple[Row, ...]]   # per feed account: each posted row any snapshot lists, once, oldest first

    def files_of(self, account: str) -> list[StatementFile]:
        return [f for f in self.files if f.account == account]

    def reported(self, account: str) -> bool:
        """Does any snapshot report the account? Only then does the engine let the feed own its days from the
        cutover on."""
        return any(account in s.accounts for s in self.snapshots)


def feed_accounts(plan: Plan) -> list[feed.FeedAccount]:
    """The profile's accounts the feed reports, in profile order, as every snapshot lists them (holdings aside: the
    newest snapshot alone lists those). A brokerage or a loan the feed alone reports carries no transactions list."""
    return [feed.FeedAccount(s.account, hh.ACCOUNTS[s.account].feed_id, hh.ACCOUNTS[s.account].name,
                             hh.ACCOUNTS[s.account].institution, credit_limit_cents=s.feed_limit_cents,
                             transactions=not (s.files is None and hh.ACCOUNTS[s.account].kind in BALANCE_FEED_KINDS))
            for s in plan.profile.accounts if s.feed]


def holdings(plan: Plan, account: str, day: date) -> tuple[feed.Holding, ...]:
    """The holdings the snapshot taken on `day` lists for the account: its portfolio's positions, read from its
    balance that day (the feed's own, pending rows included)."""
    return tuple(feed.Holding(p.symbol, p.name, p.shares, p.value_cents, p.cost_cents)
                 for p in plan.portfolios[account].positions(plan.ledger.balance(account, day, with_pending=True)))


def _unmapped(entry: UnmappedFeed, zone: ZoneInfo) -> dict:
    as_of = int(datetime.combine(entry.as_of, time(12), tzinfo=zone).timestamp())
    return {"id": entry.feed_id, "name": entry.name, "org": {"name": entry.org}, "currency": "USD",
            "balance": plain(entry.balance_cents), "available-balance": plain(entry.balance_cents),
            "balance-date": as_of, "transactions": []}


def write_statements(plan: Plan, imports: Path) -> tuple[StatementFile, ...]:
    out = []
    for source in plan.profile.accounts:
        for export in plan.exports(source.account):
            writer = WRITERS[source.files.parser]
            writer.write(imports / export.name, source.account, plan.ledger, export.first, export.last)
            out.append(StatementFile(source.account, export.name,
                                     tuple(writer.listed(plan.ledger, source.account, export.first, export.last))))
    return tuple(sorted(out, key=lambda f: f.name))


def write_snapshots(plan: Plan, store: Path, omit: frozenset[str] = frozenset()) -> tuple[
        tuple[Snapshot, ...], dict[str, tuple[Row, ...]]]:
    """One snapshot per snapshot day at SNAPSHOT_HOUR in the household's zone, listing every feed account but those
    in `omit`, those the feed has not connected yet (`plan.connected`) and those it has stopped reporting
    (`plan.stale`), then every unmapped account of the plan; the newest lists each portfolio's holdings, and the one on
    the plan's end also lists the rows dated in the FUTURE_DAYS after it, as a bank lists a payment it has already
    dated ahead. Returns the snapshots (each with its file name and how many pending rows it lists) and, per feed
    account, the posted rows they list (a row in several is the same row)."""
    specs, zone = feed_accounts(plan), ZoneInfo(hh.TIMEZONE)
    unmapped = tuple(_unmapped(u, zone) for u in plan.unmapped)
    seen: dict[str, dict[str, Row]] = {s.account: {} for s in specs}
    snapshots = []
    days = plan.snapshot_days()
    for i, day in enumerate(days):
        ahead = FUTURE_DAYS if day == plan.end else 0
        left_out = omit | {a for a, n in plan.stale.items() if i >= len(days) - n} | {
            s.account for s in specs if day < plan.connected(s.account)}
        today = [replace(s, holdings=holdings(plan, s.account, day))
                 if i == len(days) - 1 and s.account in plan.portfolios else s for s in specs]
        path = feed.write_snapshot(store, datetime.combine(day, time(SNAPSHOT_HOUR), tzinfo=zone), today,
                                   plan.ledger, zone=hh.TIMEZONE, window_days=WINDOW_DAYS, ahead_days=ahead,
                                   omit=left_out, unmapped=unmapped)
        listed = [s for s in specs if s.account not in left_out]
        pending = 0
        for spec in listed:
            for row in feed.listed(plan.ledger, spec.account, day, WINDOW_DAYS, ahead) if spec.transactions else ():
                if row.pending:
                    pending += 1
                else:
                    seen[spec.account][row.feed_id] = row
        snapshots.append(Snapshot(day, frozenset(s.account for s in listed), path.name, pending))
    return tuple(snapshots), {a: tuple(sorted(rows.values(), key=lambda r: r.date)) for a, rows in seen.items()}


def check_seam(plan: Plan, written: Written) -> None:
    """Every account with files and the feed that a snapshot reports meets the feed early enough: its earliest posted
    feed row is on or before the cutover (no days fall between the two sources) and on or before the files' newest
    row (the rows the cutover drops are replaced). ValueError names the account otherwise."""
    for source in plan.profile.accounts:
        cutover = plan.cutover(source.account)
        if cutover is None or not written.reported(source.account):
            continue
        rows = written.feed[source.account]
        newest = max((r.date for f in written.files_of(source.account) for r in f.rows), default=cutover)
        if not rows or rows[0].date > min(cutover, newest):
            raise ValueError(f"{source.account}: the feed's earliest row ({rows[0].date if rows else 'none'}) comes "
                             f"after the cutover {cutover} or the files' newest row {newest}")


def answer_documents(plan: Plan, uid_of: dict[int, str]) -> list[dict]:
    """The family's answers as the answers file holds them, oldest first (then by key): a row answer names the
    row's stored uid (`uid_of`, by the posted row's identity, as truth numbers the rows), a vendor answer the
    vendor. A row answer whose row is not stored is refused: the engine could never apply it."""
    docs = []
    for a in plan.answers:
        if isinstance(a.target, Row):
            uid = uid_of.get(id(a.target))
            if uid is None:
                raise ValueError(f"{a.target.account}: an answered row is not stored ({a.target.date} "
                                 f"{a.target.desc!r})")
            docs.append(state.row_answer(uid, a.set, a.answered_at))
        else:
            docs.append(state.vendor_answer(a.target, a.set, a.answered_at))
    return sorted(docs, key=lambda d: (d["answered_at"], d["key"]))


def write_answers(plan: Plan, home: Path, uid_of: dict[int, str]) -> Path | None:
    """`state/family/answers.json` under the FF_HOME tree `home` when the family answered anything (stamped with its
    newest answer's instant); None, nothing written, otherwise."""
    if not plan.answers:
        return None
    path = home / "state" / "family" / "answers.json"
    state.write_answers(path, answer_documents(plan, uid_of), max(a.answered_at for a in plan.answers))
    return path


def manual_asset_documents(plan: Plan) -> list[dict]:
    """The assets the household entered in the app as the store's file holds them, in the order it entered them."""
    return [state.manual_asset(a.id, a.name, a.owner, a.asset_class, a.liquid, a.values, a.updated_at)
            for a in plan.manual_assets]


def write_manual_assets(plan: Plan, home: Path) -> Path | None:
    """`state/family/manual_assets.json` under the FF_HOME tree `home` when the household entered an asset in the app
    (stamped with its newest asset's instant, as the store's merge stamps it); None, nothing written, otherwise."""
    if not plan.manual_assets:
        return None
    path = home / "state" / "family" / "manual_assets.json"
    state.write_manual_assets(path, manual_asset_documents(plan), max(a.updated_at for a in plan.manual_assets))
    return path


def write(plan: Plan, home: Path) -> Written:
    """The statement files and the snapshots of `plan` under the FF_HOME tree `home`."""
    files = write_statements(plan, home / "imports")
    snapshots, rows = write_snapshots(plan, home / "store" / "simplefin")
    written = Written(files, snapshots, rows)
    check_seam(plan, written)
    return written

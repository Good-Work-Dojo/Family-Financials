# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""What one generator run works on: a profile taken to an end date, the dates that follow from it, the ledger its
behaviours post into, the config entries they add and the rows they plant for the tests to find.

A behaviour is `fn(plan) -> None`. It posts rows through `plan.post` / `plan.move` (each row carries the `Expect` the
engine must produce for it), adds the config entries that make the engine see those rows that way, and records its
`planted` handles. It draws only from streams it names itself (`plan.stream`), so adding a behaviour moves no other
behaviour's rows, and it takes dates from `plan.start` / `plan.end`, never from a draw.

A row whose amount must find no other row of the opposite amount near it is declared alone: each leg of a move and
each posted row that is internal or that nothing files (the rows the engine's matchers read) by `move` and `post`
themselves, any other row with `plan.alone`; `build` checks every declaration once all behaviours have run.

Besides rows, a behaviour may state what the feed reports beyond balances and rows: the portfolio a brokerage holds
(`hold`), an account the feed stops reporting before the end (`stop_reporting`) and a feed account no registry entry
maps (`report_unmapped`). The snapshot writer reads them when it writes, and truth when it declares.

And what the household knows and did beyond its rows: the regular charges it pays (`declare_recurring`), the review
answers it gave in the app (`answer`, written to the answers file), the vendors those answers taught the engine
(`learns`), its own overrides (`override`, overrides.yaml) and the assets it entered in the app (`enter`, written to
the manual-assets file), and what the AI step makes of the vendors no rule places (`declare_ai`). The optional parts of
the app its household.yaml switches on are the profile's `modules`, planted for every profile as `modules`."""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, NamedTuple
from zoneinfo import ZoneInfo

from . import household as hh
from . import streams
from .dates import business_days_between, month_end, month_starts, shift_months
from .ledger import Ledger, Row, half_up

if TYPE_CHECKING:
    from .profiles import AccountSources, Export, Profile

SNAPSHOT_HOUR = 17          # local time of every feed snapshot
INGEST_HOUR = 18            # local time the household ingests the end day's data, an hour after its last snapshot
CADENCES = ("weekly", "biweekly", "monthly", "quarterly", "annual")     # the recurring detector's bands
AMOUNT_MODES = ("fixed", "variable")
LEARNED_SCOPES = ("txn", "vendor")
# why the AI step does not ask about a vendor: the policy's reasons it keeps a candidate back for (etl/ai/policy.py
# REASONS, in its order), then the rows that are no candidate at all
AI_WITHHELD = ("name", "digits", "address", "length", "seen")
AI_REASONS = (*AI_WITHHELD, "payment_app", "internal")
WINDOW_DAYS = 45            # the days of transactions each snapshot carries
SHARES = Decimal("0.001")   # the feed's share quantities, to three decimals
# the pairing windows of the generated assumptions.yaml, in business days: a bank transfer's and a payment-app leg's
WINDOW_KEYS = ("match_window_bdays", "venmo_match_window_bdays")


@dataclass(frozen=True)
class Fund:
    """One fund of a portfolio: its symbol and name, its percent of the gross position value and the price a share is
    valued at."""
    symbol: str
    name: str
    percent: int
    price_cents: int


@dataclass(frozen=True)
class Position:
    """One holding as the feed lists it: the fund, its shares (text, three decimals), value and cost basis."""
    symbol: str
    name: str
    shares: str
    value_cents: int
    cost_cents: int


@dataclass(frozen=True)
class Portfolio:
    """What a brokerage holds, read from its balance on a day: the gross position value is the balance plus the
    margin loan the balance nets out (`loan_cents`), split among the funds by percent, the last fund taking the
    rounding remainder so the values add up to the gross exactly; a fund's shares are its value at its price and its
    cost basis is `cost_percent` percent of its value, both rounded half up."""
    funds: tuple[Fund, ...]
    loan_cents: int = 0
    cost_percent: int = 100

    def __post_init__(self) -> None:
        if not self.funds or any(f.percent <= 0 or f.price_cents <= 0 for f in self.funds) \
                or sum(f.percent for f in self.funds) != 100:
            raise ValueError("a portfolio's funds have positive percents adding up to 100 and positive prices")
        if self.loan_cents < 0 or not 0 < self.cost_percent <= 100:
            raise ValueError("a portfolio's margin loan is 0 or more and its cost basis a percent of its value")

    def positions(self, balance_cents: int) -> tuple[Position, ...]:
        gross = balance_cents + self.loan_cents
        if gross <= 0:
            raise ValueError(f"a balance of {balance_cents} with a margin loan of {self.loan_cents} holds nothing")
        values = [half_up(gross * f.percent, 100) for f in self.funds[:-1]]
        values.append(gross - sum(values))
        return tuple(Position(f.symbol, f.name,
                              str((Decimal(v) / Decimal(f.price_cents)).quantize(SHARES, rounding=ROUND_HALF_UP)),
                              v, half_up(v * self.cost_percent, 100))
                     for f, v in zip(self.funds, values, strict=True))


@dataclass(frozen=True)
class UnmappedFeed:
    """A feed account no registry entry maps, listed in every snapshot: its feed id, name and institution, and the
    balance it reports as of the day it was last updated."""
    feed_id: str
    name: str
    org: str
    balance_cents: int
    as_of: date


@dataclass(frozen=True)
class Recurring:
    """A regular charge the household knows it pays, in the recurring detector's terms: the vendor and the account,
    how often (one of CADENCES) and how much (fixed or variable), whether it is still paid (neither cancelled nor
    lapsed) and how many times its price changed."""
    vendor: str
    account: str
    cadence: str
    amount_mode: str
    active: bool = True
    price_changes: int = 0

    def __post_init__(self) -> None:
        if self.cadence not in CADENCES or self.amount_mode not in AMOUNT_MODES or self.price_changes < 0:
            raise ValueError(f"{self.vendor}: a recurring charge has a cadence of {CADENCES}, an amount mode of "
                             f"{AMOUNT_MODES} and a count of price changes")


class Answer(NamedTuple):
    """A review answer the family gave in the app: `target` is a posted row (that row) or a vendor name (every
    purchase of the vendor), `set` what the answer files them under, `answered_at` when (zone-aware). `reaches` is
    the rows it files — a row answer its row, a vendor answer the purchases its behaviour names — and `outranked`
    whether an override the household wrote files one of them otherwise (the answer then conflicts)."""
    target: Row | str
    set: dict
    answered_at: datetime
    reaches: tuple[Row, ...] = ()
    outranked: bool = False


def manual_id(name: str) -> str:
    """The id of a hand-stated value named `name`, the rule the engine gives a config entry and an asset entered in
    the app must follow: the name lowercased, every run of characters other than letters and digits one dash, none
    at either end."""
    return re.sub(r"[\W_]+", "-", name.lower()).strip("-")


@dataclass(frozen=True)
class AppAsset:
    """An asset the household entered in the app (the store's manual-assets file): its id (manual_id's form), name,
    owner, class and liquidity, its dated values `(day, cents)` (one or more, the days ascending, one value a day) and
    the zone-aware instant it was last edited. It states no day it was acquired: before its first value it is held at
    that value."""
    id: str
    name: str
    owner: str
    asset_class: str
    liquid: bool
    values: tuple[tuple[date, int], ...]
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.id or manual_id(self.id) != self.id or len(self.id) > 64:
            raise ValueError(f"{self.id!r}: an asset's id is lowercase letters or digits in runs joined by dashes")
        days = [d for d, _ in self.values]
        if not days or days != sorted(set(days)) or any(isinstance(c, bool) or not isinstance(c, int)
                                                        for _, c in self.values):
            raise ValueError(f"{self.id}: an asset has one or more values in cents, one a day, oldest first")
        if self.updated_at.tzinfo is None:
            raise ValueError(f"{self.id}: an asset is stamped with a zone-aware instant")


@dataclass(frozen=True)
class Learned:
    """A vendor the family's answers teach the engine: the category and subcategory it files the vendor's other
    purchases under, and whether three row answers (`txn`) or one answer for the vendor (`vendor`) taught it."""
    vendor: str
    category: str
    subcategory: str | None
    scope: str

    def __post_init__(self) -> None:
        if self.scope not in LEARNED_SCOPES:
            raise ValueError(f"{self.vendor}: a learned vendor's scope is one of {LEARNED_SCOPES}, not {self.scope!r}")


@dataclass
class Plan:
    profile: Profile
    end: date
    start: date                        # the first day of the month `history_months` before the end's month (or
                                       # `history_days` - 1 days before the end)
    feed_start: date                   # the first day of the month `feed_months` before it (or `start`): the feed's
                                       # first connection
    ledger: Ledger
    config: dict[str, Any] = field(default_factory=dict)       # config file name -> entries merged over the starter
    # handle -> a Row, rows, an account id, a date, cents, or the module switches (a module id -> on)
    planted: dict[str, object] = field(default_factory=dict)
    portfolios: dict[str, Portfolio] = field(default_factory=dict)    # account -> what the newest snapshot lists
    stale: dict[str, int] = field(default_factory=dict)        # account -> how many of the last snapshots omit it
    unmapped: list[UnmappedFeed] = field(default_factory=list)
    recurring: list[Recurring] = field(default_factory=list)   # the regular charges, as the household declares them
    answers: list[Answer] = field(default_factory=list)        # the review answers, in the order they were given
    learned: list[Learned] = field(default_factory=list)       # the vendors those answers teach
    manual_assets: list[AppAsset] = field(default_factory=list)    # the assets entered in the app, in that order
    # vendor -> what the AI step makes of it: None (asked about) or why not (AI_REASONS)
    ai: dict[str, str | None] = field(default_factory=dict)
    _behaviour: str | None = field(default=None, repr=False)   # the name of the behaviour `build` is running
    _feed_rows: dict[tuple[str, str], int] = field(default_factory=dict, repr=False)
    _feed_keys: set[str] = field(default_factory=set, repr=False)
    _alone: list[tuple[Row, ...]] = field(default_factory=list, repr=False)
    _streams: dict[str, str | None] = field(default_factory=dict, repr=False)   # stream name -> its behaviour

    # ------------------------------------------------------------------ the profile's accounts

    def stream(self, name: str) -> random.Random:
        """The random stream `name` of the profile's seed: the same draws on every run, whatever else draws. A
        stream belongs to the behaviour that first asks for it; another behaviour asking for it is refused."""
        owner = self._streams.setdefault(name, self._behaviour)
        if owner != self._behaviour:
            raise ValueError(f"stream {name!r} belongs to {owner}, and {self._behaviour} asks for it too")
        return streams.stream(self.profile.stream_seed, name)

    def has(self, account: str) -> bool:
        return any(s.account == account for s in self.profile.accounts)

    def sources(self, account: str) -> AccountSources:
        for s in self.profile.accounts:
            if s.account == account:
                return s
        raise KeyError(f"{account}: not an account of the {self.profile.name} profile")

    def opened(self, account: str) -> date:
        """The day the account's history starts: for an account with files, the plan's start, or the first day of
        the month `opens_after_months` after it (never after the end's month); for one only the feed reports, the day
        the feed connects it (its history is what the feed delivers)."""
        s = self.sources(account)
        if s.files is None:
            return self.connected(account)
        if not s.opens_after_months:
            return self.start
        return min(shift_months(self.start, s.opens_after_months), self.end.replace(day=1))

    def connected(self, account: str) -> date | None:
        """The day the feed connects the account, which every snapshot from then on reports: the feed's start, or
        the first day of the month `feed_after_months` after it (never after the end's month); None when the feed
        does not report it."""
        s = self.sources(account)
        if not s.feed:
            return None
        if not s.feed_after_months:
            return self.feed_start
        return min(shift_months(self.feed_start, s.feed_after_months), self.end.replace(day=1))

    def cutover(self, account: str) -> date | None:
        """The first day the feed owns, for an account with statement files and the feed (the day the feed connects
        it); else None."""
        s = self.sources(account)
        return self.connected(account) if s.files is not None and s.feed else None

    def exports(self, account: str) -> tuple[Export, ...]:
        """The account's statement files, sorted by name; none for a feed-only account."""
        s = self.sources(account)
        return () if s.files is None else s.files.exports(account, self.opened(account), self.cutover(account),
                                                          self.end)

    def snapshot_days(self) -> tuple[date, ...]:
        """The days a feed snapshot is taken (at SNAPSHOT_HOUR local), from the profile's schedule: the last day of
        every month from the feed's first month while it is not after the end (when monthly), and every step-th day
        counting back from the end across its recent days, the end included. Each day once, oldest first, none
        before the feed's start."""
        if not any(s.feed for s in self.profile.accounts):
            return ()
        schedule = self.profile.schedule
        days = {self.end - timedelta(days=k) for k in range(0, schedule.recent_days + 1, schedule.step)}
        if schedule.monthly:
            days |= {month_end(m) for m in month_starts(self.feed_start, self.end) if month_end(m) <= self.end}
        return tuple(sorted(d for d in days if d >= self.feed_start))

    # ------------------------------------------------------------------ what behaviours do

    def open(self, account: str, opening_cents: int) -> None:
        """Open, on the day it opens, an account whose profile leaves its opening balance to the behaviour that
        works it out (`opening_cents` None)."""
        if self.sources(account).opening_cents is not None:
            raise ValueError(f"{account}: its profile states its opening balance; a behaviour does not")
        self.ledger.open(account, hh.ACCOUNTS[account].kind, opening_cents, self.opened(account))

    def hold(self, account: str, portfolio: Portfolio) -> None:
        """The feed lists `portfolio`'s positions for the account in the newest snapshot, read from its balance
        that day."""
        if not self.sources(account).feed or account in self.portfolios:
            raise ValueError(f"{account}: a portfolio is held once, by an account the feed reports")
        self.portfolios[account] = portfolio

    def reporting_days(self, account: str) -> tuple[date, ...]:
        """The snapshot days from the day the feed connects the account on (none when the feed does not report it):
        the snapshots that list it unless it stops reporting."""
        connected = self.connected(account)
        return () if connected is None else tuple(d for d in self.snapshot_days() if d >= connected)

    def stop_reporting(self, account: str, snapshots: int) -> None:
        """The feed leaves the account out of its last `snapshots` snapshots (it stops reporting it); at least one
        snapshot from its connection on, before them, reports it."""
        days = len(self.reporting_days(account))
        if account in self.stale or not 0 < snapshots < days:
            raise ValueError(f"{account}: the feed stops reporting an account it reports once, leaving it out of "
                             f"from 1 to {days - 1} of the {days} snapshots from its connection on, not {snapshots}")
        self.stale[account] = snapshots

    def report_unmapped(self, entry: UnmappedFeed) -> None:
        """Every snapshot also lists `entry`, a feed account the registry does not map."""
        if entry.feed_id in {hh.ACCOUNTS[s.account].feed_id for s in self.profile.accounts} \
                or entry.feed_id in {u.feed_id for u in self.unmapped}:
            raise ValueError(f"{entry.feed_id}: an unmapped feed account has an id of its own")
        self.unmapped.append(entry)

    def declare_recurring(self, item: Recurring) -> None:
        """The household pays `item` regularly: the recurring detector must list it, as declared. A vendor and
        account are declared once."""
        if any((r.vendor, r.account) == (item.vendor, item.account) for r in self.recurring):
            raise ValueError(f"{item.vendor} on {item.account}: a recurring charge is declared once")
        self.recurring.append(item)

    def declare_ai(self, vendor: str, reason: str | None) -> None:
        """What the AI step makes of `vendor` (as the engine names it): None, it is asked about; a reason it keeps the
        vendor back for (the policy's `name`, `digits`, `seen`); or why its rows are no candidate at all
        (`payment_app`, `internal`). A vendor is declared once."""
        if reason is not None and reason not in AI_REASONS:
            raise ValueError(f"{vendor}: {reason!r} is not one of {', '.join(AI_REASONS)}")
        if vendor in self.ai:
            raise ValueError(f"{vendor}: the AI step's view of a vendor is declared once")
        self.ai[vendor] = reason

    def ingest_at(self) -> datetime:
        """The instant the household ingests the end day's data: the end at INGEST_HOUR in its zone."""
        return datetime.combine(self.end, time(INGEST_HOUR), tzinfo=ZoneInfo(hh.TIMEZONE))

    def answer(self, target: Row | str, set_: dict, answered_at: datetime, *, reaches: tuple[Row, ...] = (),
               outranked: bool = False) -> Answer:
        """The family answers `target` (a posted row, or a vendor for every purchase of it) with `set_` at
        `answered_at`: a zone-aware instant before the ingest and, for a row, on a later day than the row's in the
        household's zone. A row answer reaches its row; a vendor answer reaches the posted purchases of the vendor
        given as `reaches`. `outranked`: an override files a row it reaches otherwise. Refused otherwise; a row is
        answered once."""
        if answered_at.tzinfo is None or not answered_at < self.ingest_at():
            raise ValueError(f"an answer is given at a zone-aware instant before the ingest ({self.ingest_at()}), "
                             f"not {answered_at}")
        if isinstance(target, Row):
            if reaches:
                raise ValueError(f"{target.account}: a row answer reaches its own row only")
            if any(a.target is target for a in self.answers):
                raise ValueError(f"{target.account}: a row is answered once ({target.date} {target.desc!r})")
            reaches = (target,)
        elif not (isinstance(target, str) and target.strip()):
            raise ValueError(f"an answer names a posted row or a vendor, not {target!r}")
        for row in reaches:
            if not any(r is row for r in self.ledger.rows(row.account)) or row.pending:
                raise ValueError(f"{row.account}: an answer reaches posted rows of the plan ({row.date} {row.desc!r})")
            if isinstance(target, str) and (row.expect is None or (row.expect.vendor or "").casefold()
                                            != target.casefold()):
                raise ValueError(f"{row.account}: a vendor answer reaches purchases of {target} only ({row.desc!r})")
        if isinstance(target, Row) and not answered_at.astimezone(ZoneInfo(hh.TIMEZONE)).date() > target.date:
            raise ValueError(f"{target.account}: a row is answered on a later day than its own ({target.date})")
        answer = Answer(target, dict(set_), answered_at, tuple(reaches), outranked)
        self.answers.append(answer)
        return answer

    def learns(self, learned: Learned) -> None:
        """The family's answers teach the engine `learned` (declared once per vendor)."""
        if any(r.vendor.casefold() == learned.vendor.casefold() for r in self.learned):
            raise ValueError(f"{learned.vendor}: a learned vendor is declared once")
        self.learned.append(learned)

    def enter(self, asset: AppAsset) -> None:
        """The household enters `asset` in the app, last editing it before the ingest; refused for an id or a name
        (ignoring case) already entered, a name one of the profile's accounts has, or a value dated after the end."""
        names = {a.name.casefold() for a in self.manual_assets} | {
            hh.ACCOUNTS[s.account].name.casefold() for s in self.profile.accounts}
        if any(a.id == asset.id for a in self.manual_assets) or asset.name.casefold() in names:
            raise ValueError(f"{asset.id}: an asset entered in the app has an id and a name of its own")
        if not asset.updated_at < self.ingest_at() or asset.values[-1][0] > self.end:
            raise ValueError(f"{asset.id}: an asset is entered before the ingest ({self.ingest_at()}), its values "
                             f"dated on or before the end")
        self.manual_assets.append(asset)

    def override(self, match: dict, set_: dict, note: str) -> None:
        """One overrides.yaml entry: the rows `match` names take `set_`, whatever filed them before."""
        self.config.setdefault("overrides.yaml", []).append({"match": dict(match), "set": dict(set_), "note": note})

    def post(self, row: Row, *, key: str | None = None) -> Row:
        """Post money to or from outside the household; returns the row as posted (with its feed id, named by `key`
        when given: see `_ready`). A row the engine keeps internal, or files by nothing (the fallback), is declared
        alone: the matchers read such rows."""
        posted = self.ledger.post(self._ready(row, key))
        if posted.expect is not None and (posted.expect.internal or posted.expect.source == "fallback"):
            self.alone(posted)
        return posted

    def move(self, out: Row, into: Row, *, alone: bool = True, key: str | None = None) -> tuple[Row, Row]:
        """Post money between two of the household's accounts (Ledger.move), both legs' feed ids named by `key`
        when given (see `_ready`); the two legs are declared alone, each the other's only rival, unless `alone` is
        False (a pair meant to be ambiguous)."""
        pair = self.ledger.move(self._ready(out, key), self._ready(into, key))
        if alone:
            self.alone(*pair)
        return pair

    def assumption(self, key: str) -> Any:
        """A key of the generated assumptions.yaml as the plan stands (the starter's, the plan's additions over it)."""
        # imported here, as config_writer imports this module
        from . import config_writer
        return config_writer.assumptions(self)[key]

    def rival_bdays(self) -> int:
        """The widest pairing window of the generated assumptions.yaml, in business days."""
        return max(self.assumption(key) for key in WINDOW_KEYS)

    def add(self, file: str, key: str, entries: list) -> None:
        """Append `entries` to the list `key` of config file `file`'s additions."""
        self.config.setdefault(file, {}).setdefault(key, []).extend(entries)

    def put(self, file: str, key: str, value: dict) -> None:
        """Merge the mapping `value` into the mapping `key` of config file `file`'s additions; a key already put is
        refused."""
        target = self.config.setdefault(file, {}).setdefault(key, {})
        twice = sorted(set(target) & set(value))
        if twice:
            raise ValueError(f"{file}: {key}: {', '.join(twice)} put twice")
        target.update(value)

    def plant(self, handle: str, value: object) -> None:
        if handle in self.planted:
            raise ValueError(f"handle {handle!r} is planted twice")
        self.planted[handle] = value

    def alone(self, *rows: Row) -> None:
        """Declare that each of `rows` (as posted) has no other row of the opposite amount, pending rows aside, on
        another account within the widest pairing window (`rival_bdays`) but the other rows given: one row has
        none, the two legs of a pair have each other. The engine pairs such legs only when each is the other's only
        candidate, and pairs two rows nothing explains when they are; `build` checks every declaration at the end."""
        self._alone.append(rows)

    def rivals(self, row: Row, bdays: int | None = None) -> list[Row]:
        """The posted rows of the opposite amount on another account within `bdays` business days of `row` (the
        widest pairing window when None)."""
        window = self.rival_bdays() if bdays is None else bdays
        return [r for a in self.ledger.accounts() if a != row.account for r in self.ledger.rows(a)
                if not r.pending and r.cents == -row.cents
                and business_days_between(r.date, row.date) <= window]

    def check_alone(self) -> None:
        """Every `alone` declaration holds; ValueError names the row and its rivals otherwise."""
        window = self.rival_bdays()
        for rows in self._alone:
            for row in rows:
                rivals = self.rivals(row, window)
                partners = [r for r in rows if r is not row and r.account != row.account and r.cents == -row.cents]
                if len(rivals) != len(partners) or any(not any(r is p for p in partners) for r in rivals):
                    found = ", ".join(f"{r.account} {r.date} {r.desc!r}" for r in rivals) or "none"
                    raise ValueError(f"{row.account}: {row.date} {row.desc!r} ({row.cents}) must have exactly its "
                                     f"own partners as rivals within {window} business days; it has: {found}")

    def _ready(self, row: Row, key: str | None = None) -> Row:
        """The row with its feed id `<account's feed id>-<behaviour>-<n>`: the n-th row the running behaviour posts
        on that account, so an id never depends on a draw, stays the same in every snapshot, and moves with no
        other behaviour's rows. A row posted with a `key` takes `<account's feed id>-<behaviour>-<key>` instead: a
        settlement names its rows by their month, since whether a month has one depends on the balances it reads,
        and a month that has none must move no other month's id. A key is not a bare number (that is a count's
        place) and is used once per account and behaviour."""
        if not row.pending and row.expect is None:
            raise ValueError(f"{row.account}: a row declares what the engine must make of it ({row.date} {row.desc!r})")
        source = self.sources(row.account)
        if not source.feed or row.feed_id is not None:
            return row
        if self._behaviour is None:
            raise ValueError(f"{row.account}: a feed row is posted outside a behaviour ({row.date} {row.desc!r})")
        prefix = f"{hh.ACCOUNTS[row.account].feed_id}-{self._behaviour}"
        if key is not None:
            feed_id = f"{prefix}-{key}"
            if not key or key.isdigit() or feed_id in self._feed_keys:
                raise ValueError(f"{row.account}: the feed key {key!r} is empty, a number or used twice")
            self._feed_keys.add(feed_id)
            return replace(row, feed_id=feed_id)
        count = (row.account, self._behaviour)
        self._feed_rows[count] = self._feed_rows.get(count, 0) + 1
        return replace(row, feed_id=f"{prefix}-{self._feed_rows[count]}")


def build(profile: Profile, end: date) -> Plan:
    """The plan of `profile` ending on `end`: its module switches planted as `modules`, its accounts opened (one whose
    opening balance the profile leaves to a behaviour by that behaviour), its behaviours run in order and then its
    settlements in order (each under its function's name, unique across the two), every `alone` declaration and the
    ledger checked."""
    steps = (*profile.behaviours, *profile.settlements)
    names = [b.__name__ for b in steps]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise ValueError(f"{profile.name}: behaviours share a name: {', '.join(twice)}")
    if profile.history_days is not None:
        start = feed_start = end - timedelta(days=profile.history_days - 1)
    else:
        start = shift_months(end, -profile.history_months)
        feed_start = shift_months(end, -profile.feed_months)
    plan = Plan(profile, end, start, feed_start, Ledger(start, end))
    plan.plant("modules", {module: True for module in profile.modules})
    for source in profile.accounts:
        if source.opening_cents is not None:
            plan.ledger.open(source.account, hh.ACCOUNTS[source.account].kind, source.opening_cents,
                             plan.opened(source.account))
    for behaviour, name in zip(steps, names, strict=True):
        plan._behaviour = name
        try:
            behaviour(plan)
        finally:
            plan._behaviour = None
    unopened = [s.account for s in profile.accounts if s.account not in plan.ledger.accounts()]
    if unopened:
        raise ValueError(f"{profile.name}: no behaviour opened {', '.join(unopened)}, whose opening balance the "
                         "profile leaves to one")
    plan.check_alone()
    plan.ledger.check()
    return plan

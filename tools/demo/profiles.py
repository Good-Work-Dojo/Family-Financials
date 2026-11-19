# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Profiles: which of the household's accounts exist, when each opens, where each one's rows come from (statement
files cut into exports, the bank feed, or both with a cutover between them), the days the feed is fetched on, the
behaviours that post the history and the seed their random streams start from."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import TYPE_CHECKING

from . import household as hh
from .behaviours import (
    ai,
    balance_sheet,
    edges,
    income,
    p2p,
    paths,
    review,
    seam,
    spending,
    subscriptions,
    transfers,
)
from .dates import month_end, month_starts, shift_months

if TYPE_CHECKING:
    from .plan import Plan

PARSERS = ("chase_checking", "chase_card", "bofa_bank")
CUTS = ("spans", "years", "months")


@dataclass(frozen=True)
class Export:
    """One statement file: its name and the days it covers, both included."""
    account: str
    name: str
    first: date
    last: date


@dataclass(frozen=True)
class Files:
    """An account's statement files: the format they are written in and how its history is cut into exports.

    `spans`: exports of `span_months` months from the history's start, each starting `overlap_months` before the
    previous one ends; `years`: one export per calendar year; both reach `past_cutover_days` past the cutover (else to
    the end). `months`: one statement per calendar month up to the cutover's month (else to the end's), the one
    `reexport_months_before` months before that month written a second time under another name."""
    parser: str
    cut: str
    past_cutover_days: int = 0
    overlap_months: int = 0
    reexport_months_before: int | None = None
    span_months: int = 6

    def __post_init__(self) -> None:
        if self.parser not in PARSERS or self.cut not in CUTS:
            raise ValueError(f"files: parser {self.parser!r} / cut {self.cut!r} is not one of {PARSERS} / {CUTS}")
        if self.span_months < 1:
            raise ValueError(f"files: span_months {self.span_months} must be 1 or more")
        if not 0 <= self.overlap_months < self.span_months:
            raise ValueError(f"files: overlap_months {self.overlap_months} must be from 0 to less than an export's "
                             f"{self.span_months} months")

    def exports(self, account: str, start: date, cutover: date | None, end: date) -> tuple[Export, ...]:
        """The exports, sorted by name (the order the engine reads them in). An overlap of two exports must lie
        before the cutover, and a re-exported month inside the files' months, else ValueError."""
        if self.cut == "months":
            out = [Export(account, f"{account}-{m:%Y-%m}.csv", m, min(month_end(m), end))
                   for m in month_starts(start, cutover or end)]
            if self.reexport_months_before is not None:
                again = shift_months(cutover or end, -self.reexport_months_before)
                twice = [e for e in out if e.first == again]
                if not twice:
                    raise ValueError(f"{account}: the re-export month {again:%Y-%m} is outside the files' months "
                                     f"{out[0].first:%Y-%m} to {out[-1].first:%Y-%m}" if out else
                                     f"{account}: the re-export month {again:%Y-%m} has no files to repeat")
                out.append(Export(account, f"{account}-{again:%Y-%m}_reexport.csv", twice[0].first, twice[0].last))
        else:
            last = min(end, cutover + timedelta(days=self.past_cutover_days)) if cutover else end
            out = self._years(account, start, last) if self.cut == "years" else self._spans(account, start, last)
            for a, b in zip(out, out[1:], strict=False):
                if b.first <= a.last and cutover is not None and a.last >= cutover:
                    raise ValueError(f"{account}: exports {a.name} and {b.name} overlap on or after the cutover")
        return tuple(sorted(out, key=lambda e: e.name))

    @staticmethod
    def _years(account: str, start: date, last: date) -> list[Export]:
        return [Export(account, f"{account}-{year}.csv", max(start, date(year, 1, 1)), min(last, date(year, 12, 31)))
                for year in range(start.year, last.year + 1)]

    def _spans(self, account: str, start: date, last: date) -> list[Export]:
        out, first = [], start
        while True:
            stop = min(shift_months(first, self.span_months) - timedelta(days=1), last)
            out.append(Export(account, f"{account}-{first.isoformat()}_{stop.isoformat()}.csv", first, stop))
            if stop >= last:
                return out
            first = shift_months(first, self.span_months - self.overlap_months)


@dataclass(frozen=True)
class AccountSources:
    """One account of a profile: its balance the day before it opens (None when a behaviour works it out and opens
    the account, as the mortgage's behaviour does from the loan's schedule), its statement files and whether the feed
    reports it (with the credit limit it reports for a card). The feed connects the account `feed_after_months`
    months after the feed's start (the plan's `feed_start`) and reports it in every snapshot from then on; an account
    with both files and the feed has its cutover on that day. An account with files opens `opens_after_months` months
    after the history starts (its first file starts then); one the feed alone reports opens when the feed connects
    it."""
    account: str
    opening_cents: int | None
    files: Files | None = None
    feed: bool = False
    feed_limit_cents: int | None = None
    opens_after_months: int = 0
    feed_after_months: int = 0

    def __post_init__(self) -> None:
        if self.account not in hh.ACCOUNTS:
            raise ValueError(f"{self.account}: not one of the household's accounts")
        if self.files is None and not self.feed:
            raise ValueError(f"{self.account}: an account needs statement files, the feed or both")
        if self.opens_after_months < 0 or (self.files is None and self.opens_after_months):
            raise ValueError(f"{self.account}: opens_after_months is a count of months for an account with files")
        if self.feed_after_months < 0 or (not self.feed and self.feed_after_months):
            raise ValueError(f"{self.account}: feed_after_months is a count of months for an account the feed reports")


@dataclass(frozen=True)
class Schedule:
    """The days the feed is fetched on: the last day of every month from the connection's month (when `monthly`),
    and every `step`-th day counting back from the end across the last `recent_days` days, the end included."""
    monthly: bool = True
    recent_days: int = 21
    step: int = 3

    def __post_init__(self) -> None:
        if self.recent_days < 0 or self.step < 1:
            raise ValueError(f"schedule: recent_days {self.recent_days} must be 0 or more and step {self.step} 1 or "
                             "more")


@dataclass(frozen=True)
class Profile:
    """A profile. `behaviours` post the history in order; `settlements` run after every one of them, in order, and
    are the behaviours that read balances the others decide (a card payment pays what the card owed, the move to
    savings sweeps what checking holds above its cushion), so an ordinary behaviour is appended to `behaviours` and
    needs no place among them. Its random streams are seeded with `seed`, its own name when None, so a profile that
    is another without some feature can share that one's rows by naming it. `modules` are the optional parts of the
    app its household.yaml switches on (`modules:`, written only when there is one)."""
    name: str
    history_months: int                # the history starts this many months before the end's month
    feed_months: int                   # the feed starts this many months before the end's month
    accounts: tuple[AccountSources, ...]
    behaviours: tuple[Callable[[Plan], None], ...]
    settlements: tuple[Callable[[Plan], None], ...] = ()
    schedule: Schedule = Schedule()
    seed: str | None = None
    # when set, the history and the feed both start this many days before the end, the end included, in place of the
    # two counts of months (which are then 0)
    history_days: int | None = None
    modules: tuple[str, ...] = ()
    # household.yaml's `ai:` block as (key, value) pairs, written only when there is one: which provider and model
    # the AI step would use. No key file and no consent are ever written, so the step stays off.
    ai: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if len(set(self.modules)) != len(self.modules):
            raise ValueError(f"{self.name}: a module is switched on once")
        if self.history_days is not None and (self.history_days < 1 or self.history_months or self.feed_months):
            raise ValueError(f"{self.name}: a history of {self.history_days} days is a day or more, and replaces "
                             "the counts of months (both 0)")
        if self.history_days is not None:
            late = [a.account for a in self.accounts if a.opens_after_months or a.feed_after_months]
            if late:
                raise ValueError(f"{self.name}: a history of {self.history_days} days has no month an account "
                                 f"could open or connect after ({', '.join(late)})")

    @property
    def stream_seed(self) -> str:
        return self.name if self.seed is None else self.seed


# the balance-sheet accounts join the feed this many months after the everyday ones, so the month ends between hold
# the everyday accounts and the home only: net worth's first month ends are partial
BALANCE_SHEET_JOINS_AFTER = 3

FULL = Profile(
    name="full",
    history_months=27,
    feed_months=22,
    accounts=(
        # four-month exports overlapping by a month, so the two before the cutover share one
        AccountSources(hh.CHECKING, 1_261_237, Files("chase_checking", "spans", past_cutover_days=42,
                                                     overlap_months=1, span_months=4), feed=True),
        AccountSources(hh.SAVINGS, 2_415_000, Files("bofa_bank", "months", reexport_months_before=3), feed=True),
        # Alex's card opens three months in, so the first months have two of the four cash-flow accounts: the
        # coverage floor lands after the first month with data
        AccountSources(hh.CARD_ALEX, -128_416, Files("chase_card", "years", past_cutover_days=42), feed=True,
                       opens_after_months=3),
        AccountSources(hh.CARD_SAM, 0, feed=True, feed_limit_cents=800_000),
        # its positions less the margin loan; the brokerage's returns outrun the 401(k)'s over the comparable month
        # ends, so how the two openings divide the invested money sets how far net worth grows between them
        AccountSources(hh.BROKERAGE, 20_000_000, feed=True, feed_after_months=BALANCE_SHEET_JOINS_AFTER),
        AccountSources(hh.RETIREMENT, 27_300_000, feed=True, feed_after_months=BALANCE_SHEET_JOINS_AFTER),
        # the loan opens at its schedule's balance, which its behaviour works out
        AccountSources(hh.MORTGAGE, None, feed=True, feed_after_months=BALANCE_SHEET_JOINS_AFTER),
        AccountSources(hh.CHILD_SAVINGS, 185_000, feed=True, feed_after_months=BALANCE_SHEET_JOINS_AFTER),
        AccountSources(hh.CERTIFICATE, 2_500_000, feed=True, feed_after_months=BALANCE_SHEET_JOINS_AFTER),
    ),
    schedule=Schedule(monthly=True, recent_days=21, step=3),
    behaviours=(*income.INCOME, spending.everyday, seam.twins, paths.paths, paths.occasional, p2p.p2p,
                edges.pending_rows, edges.future_row, edges.data_gap, edges.open_question, balance_sheet.housing,
                balance_sheet.investing, balance_sheet.goals, balance_sheet.allowance, balance_sheet.certificate,
                balance_sheet.feed_edges, subscriptions.subscriptions, review.split_vendor, review.learned_vendor,
                review.vendor_answer, review.conflict, transfers.store_card, balance_sheet.car, ai.ai_vendors),
    settlements=(transfers.card_payments, transfers.transfers),
    # the household weighs buying a second house: Home buying is on (no other profile switches a module)
    modules=("home_buying",),
    # the AI step configured with the defaults (the engine's default model: tests/test_demo_full.py pins the two
    # equal, as the generator imports nothing of the engine), and off: its vendors are on Sam's card (behaviours/ai.py)
    ai=(("provider", "anthropic"), ("model", "claude-opus-5-5")),
)

# A household that connected its bank feed five days ago and has nothing else: checking, Alex's card and the
# brokerage, reported by the feed alone, one snapshot a day; the behaviours of `full` that fit five days run.
NEW = replace(
    FULL, name="new", history_months=0, feed_months=0, history_days=5,
    accounts=tuple(AccountSources(s.account, s.opening_cents, feed=True, feed_limit_cents=s.feed_limit_cents)
                   for s in FULL.accounts if s.account in (hh.CHECKING, hh.CARD_ALEX, hh.BROKERAGE)),
    schedule=Schedule(monthly=False, recent_days=4, step=1), modules=(), ai=(),
)

# One checking account read from a statement file per month, the end's month and the one before; no feed. Income and
# everyday spending only, filed by the starter's rules (and the income rules the pay brings).
TINY = Profile(
    name="tiny",
    history_months=1,
    feed_months=0,
    accounts=(AccountSources(hh.CHECKING, 382_519, Files("chase_checking", "months")),),
    behaviours=(*income.INCOME, spending.everyday),
)

# The household and no account: nothing to read.
EMPTY = Profile(name="empty", history_months=0, feed_months=0, accounts=(), behaviours=())

# `full` without the brokerage and the 401(k): what invests leaves with them (the contributions and their rule, the
# targets, the retirement accounts); every other row is `full`'s, the move to savings following checking's balance.
NO_INVESTMENTS = replace(FULL, name="no-investments", seed="full", modules=(),
                         accounts=tuple(s for s in FULL.accounts if s.account not in (hh.BROKERAGE, hh.RETIREMENT)))

# `full` without pay or the unknown deposit (their income rules go with them; savings interest stays). Checking opens
# with what the spending of the whole history needs, and with no income it moves nothing to savings: a sweep down to
# the cushion would leave it unable to pay the next month.
NO_INCOME_CHECKING_CENTS = 22_500_000         # the history's spending from checking is about $204,000
NO_INCOME = replace(
    FULL, name="no-income", seed="full", modules=(),
    accounts=tuple(replace(s, opening_cents=NO_INCOME_CHECKING_CENTS) if s.account == hh.CHECKING else s
                   for s in FULL.accounts),
    behaviours=tuple(b for b in FULL.behaviours if b not in (income.pay, income.unknown_credit)),
    settlements=tuple(s for s in FULL.settlements if s is not transfers.transfers),
)

NAMES = ("full", "new", "tiny", "empty", "no-investments", "no-income")
PROFILES: dict[str, Profile] = {p.name: p for p in (FULL, NEW, TINY, EMPTY, NO_INVESTMENTS, NO_INCOME)}


def get(name: str) -> Profile:
    """The profile called `name`; KeyError for any other name."""
    try:
        return PROFILES[name]
    except KeyError:
        raise KeyError(f"unknown profile {name!r} (known: {', '.join(PROFILES)})") from None

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The demo generator's frame on profiles of other shapes than `full`: short and feed-only histories, a schedule of
snapshots of the profile's own, an account the feed never reports, and the refusals that keep a profile honest. These
read the plan, the written files and truth, except the cases of hand-stated values the standard profiles never reach
(an app asset that replaces a config entry, a home value on the day the balance sheet connects or on a month end),
which generate a tree, ingest it and compare its exports with its truth. Last, the shapes the dashboard's test suites
need of the `full` household's exports. Everything here is invented."""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import demo_checks
import pytest
from conftest import DEMO_END, DEMO_NOW, ROOT, ingest_demo

from tools.demo import generate, profiles, sources, truth
from tools.demo import household as hh
from tools.demo import plan as plan_mod
from tools.demo.behaviours import (
    balance_sheet,
    edges,
    paths,
    review,
    spending,
    subscriptions,
    transfers,
)
from tools.demo.dates import month_end, shift_months
from tools.demo.ledger import Expect, Row
from tools.demo.profiles import AccountSources, Files, Schedule

FULL = profiles.FULL
CHECKING_FILES = Files("chase_checking", "spans", past_cutover_days=42, overlap_months=1)


def opening(account: str) -> int:
    return next(s.opening_cents for s in FULL.accounts if s.account == account)


# handle -> (account, descriptor text, cents or None): the rows a handle's feature posts. A handle is planted exactly
# when its feature's rows are in the ledger.
FEATURE_ROWS = {
    "income.alex_first": (hh.CHECKING, "QUILLWORKS DESIGN PAYROLL", None),
    "income.alex_second": (hh.CHECKING, "LARKSPUR ROBOTICS", None),
    "income.sam": (hh.CHECKING, "HARBOR CITY SCHOOLS", None),
    "income.unknown_credit": (hh.CHECKING, "REMOTE ONLINE DEPOSIT", None),
    "income.refund": (hh.CHECKING, "RETURN", None),
    "income.interest": (hh.SAVINGS, "INTEREST EARNED", None),
    "seam.twins": (hh.CHECKING, "PANERA", None),
    "paths.vendor_rule.below": (hh.CARD_ALEX, "BARNES & NOBLE", None),
    "paths.vendor_rule.above": (hh.CARD_ALEX, "BEST BUY", None),
    "paths.keyword.below": (hh.CHECKING, "CEDAR HOLLOW DAYCARE", None),
    "paths.bank_label.below": (hh.CARD_ALEX, paths.BISTRO, None),
    "paths.fallback.below": (hh.CHECKING, paths.GENERAL_STORE, None),
    "zelle.own_pair": (hh.CHECKING, "ZELLE PAYMENT TO ALEX", None),
    "zelle.third_party": (hh.CHECKING, "JORDAN EXAMPLE", None),
    "p2p.venmo_above": (hh.CHECKING, "VENMO", -22_640),
    "p2p.venmo_below": (hh.CHECKING, "VENMO", -4_860),
    "pending.rows": (hh.CARD_SAM, "TARGET T-2214", None),
    "future.uid": (hh.CHECKING, "WALGREENS", None),
    "data_gap.uid": (hh.CHECKING, edges.GAP_DESC, None),
    "open_question.uid": (hh.CHECKING, edges.QUESTION_DESC, None),
    "card_payment.pair": (hh.CHECKING, "CREDIT CRD EPAY", None),
    "transfer.pair": (hh.CHECKING, transfers.TO_SAVINGS, None),
    "transfer.unpaired_kind_leg": (hh.CHECKING, "LAKESIDE OUTFITTERS", None),
    "mortgage.payment": (hh.CHECKING, "EXAMPLE HOME LOANS", None),
    "car.purchase": (hh.CHECKING, balance_sheet.DEALER.desc, None),
    "child.pair": (hh.CHECKING, f"TRANSFER TO SAV XXXXXX{hh.CHILD_SAVINGS[-4:]}", None),
    **{f"subscriptions.{s.handle}": (s.account, s.merchant.desc, None) for s in subscriptions.SUBSCRIPTIONS},
    "split.purchase": (hh.CARD_ALEX, review.WHOLESALE.desc, None),
    "learned.answered": (hh.CHECKING, review.GARDEN.desc, None),
    "vendor_answer.vendor": (hh.CARD_SAM, review.PEDIATRICS.desc, None),
    "conflict.row": (hh.CHECKING, review.GALA.desc, None),
}
# handles planted exactly when the profile holds the account
ACCOUNT_HANDLES = {"mortgage.account": hh.MORTGAGE, "home.entry": hh.MORTGAGE, "brokerage.account": hh.BROKERAGE,
                   "brokerage.symbols": hh.BROKERAGE, "margin.loan_cents": hh.BROKERAGE,
                   "retirement.account": hh.RETIREMENT, "child.account": hh.CHILD_SAVINGS}
# handles planted when what the feed reports holds them: the stale account needs a snapshot from its connection on
# that reports it before the two that leave it out; the unmapped account, any snapshot; Sam's card's limit, the feed
# reporting the card
FEED_HANDLES = ("stale.account", "unmapped.feed_id", "card.limit_cents")
# handles planted together with another: (handle, its companion)
COMPANIONS = (("seam.account", "seam.twins"), ("seam.cutover", "seam.twins"),
              ("paths.keyword.above", "paths.keyword.below"), ("paths.bank_label.above", "paths.bank_label.below"),
              ("paths.fallback.above", "paths.fallback.below"), ("future.date", "future.uid"),
              ("data_gap.account", "data_gap.uid"), ("data_gap.hint", "data_gap.uid"),
              ("open_question.rule", "open_question.uid"), ("split.vendor", "split.purchase"),
              ("learned.vendor", "learned.answered"), ("learned.unanswered", "learned.answered"),
              ("conflict.key", "conflict.row"), ("car.entry", "car.purchase"))
# planted for every profile: the module switches its household.yaml states (none unless the profile names one)
PROFILE_HANDLES = ("modules",)

SHAPES = {
    # two months of checking's statement files and nothing else
    "files-2": lambda end: replace(FULL, name="files-2", history_months=2, feed_months=0, accounts=(
        AccountSources(hh.CHECKING, opening(hh.CHECKING), CHECKING_FILES),)),
    # the feed alone, connected on the first of the end's month: checking and Sam's card
    "feed-only": lambda end: replace(FULL, name="feed-only", history_months=0, feed_months=0, accounts=(
        AccountSources(hh.CHECKING, opening(hh.CHECKING), feed=True),
        AccountSources(hh.CARD_SAM, 0, feed=True, feed_limit_cents=800_000))),
    # every account of `full` over a year, the feed for its last six months; checking's exports are half-years, so
    # the two before the cutover share a month (four-month ones would overlap past it)
    "year": lambda end: replace(FULL, name="year", history_months=12, feed_months=6, accounts=tuple(
        replace(s, files=CHECKING_FILES) if s.account == hh.CHECKING else s for s in FULL.accounts)),
    # every account of `full` reported by the feed alone, connected on the first of the end's month
    "feed-all": lambda end: replace(FULL, name="feed-all", history_months=0, feed_months=0, accounts=tuple(
        AccountSources(s.account, s.opening_cents, feed=True, feed_limit_cents=s.feed_limit_cents)
        for s in FULL.accounts)),
}
CASES = [("files-2", DEMO_END), ("feed-only", date(2026, 10, 1)), ("feed-only", date(2026, 10, 5)),
         ("feed-only", date(2026, 10, 12)), ("year", DEMO_END), ("feed-all", date(2026, 10, 1)),
         ("feed-all", date(2026, 10, 5)), ("feed-all", date(2026, 10, 12)), ("feed-all", DEMO_END)]


@pytest.mark.parametrize(("shape", "end"), CASES, ids=[f"{s}-{e}" for s, e in CASES])
def test_the_full_behaviours_run_on_short_and_feed_only_shapes(shape, end, tmp_path):
    profile = SHAPES[shape](end)
    plan = plan_mod.build(profile, end)                       # every behaviour runs, the ledger checks
    rows = [r for a in plan.ledger.accounts() for r in plan.ledger.rows(a)]
    for handle, (account, text, cents) in FEATURE_ROWS.items():
        posted = any(r.account == account and text in r.desc and (cents is None or r.cents == cents) for r in rows)
        assert (handle in plan.planted) == posted, handle
    for handle, companion in COMPANIONS:
        assert (handle in plan.planted) == (companion in plan.planted), handle
    for handle, account in ACCOUNT_HANDLES.items():
        assert (handle in plan.planted) == plan.has(account), handle
    days = plan.snapshot_days()
    reporting = [d for d in days if plan.has(hh.RETIREMENT) and d >= plan.connected(hh.RETIREMENT)]
    assert ("stale.account" in plan.planted) == (len(reporting) > balance_sheet.STALE_SNAPSHOTS) == (
        hh.RETIREMENT in plan.stale)
    assert ("unmapped.feed_id" in plan.planted) == bool(days) == bool(plan.unmapped)
    assert ("card.limit_cents" in plan.planted) == (plan.has(hh.CARD_SAM) and plan.sources(hh.CARD_SAM).feed
                                                    and plan.sources(hh.CARD_SAM).feed_limit_cents is not None)
    assert set(plan.planted) <= set(FEATURE_ROWS) | {h for h, _ in COMPANIONS} | set(ACCOUNT_HANDLES) | set(
        FEED_HANDLES) | set(PROFILE_HANDLES)
    assert plan.planted["modules"] == {m: True for m in profile.modules}
    # a config entry comes with its feature only
    vendors = [p["vendor"] for p in plan.config.get("vendor_rules.yaml", {}).get("patterns", [])]
    assert ("Example Home Loans" in vendors) == ("mortgage.payment" in plan.planted)
    assert (balance_sheet.DEALER.vendor in vendors) == ("car.purchase" in plan.planted)
    keys = plan.config.get("accounts.yaml", {})
    assert (hh.MORTGAGE in keys.get("accounts", {})) == bool(plan.manual_assets) == plan.has(hh.MORTGAGE)
    assert bool(keys.get("manual_entries")) == ("car.entry" in plan.planted)
    # the home's values: the history's first day, then those of a year and a month before the end that fall after it
    if plan.manual_assets:
        [home] = plan.manual_assets
        days = [d for d, _ in home.values]
        assert days[0] == plan.start and days == sorted(set(days)) and days[-1] <= plan.end
        assert home.values[-1][1] == balance_sheet.HOME_VALUES_CENTS[-1]
    assert (hh.BROKERAGE in keys.get("accounts", {})) == (hh.BROKERAGE in plan.portfolios) == plan.has(hh.BROKERAGE)
    contributions = [r for r in rows if r.desc == balance_sheet.CONTRIBUTION]
    rule_names = [r["name"] for r in plan.config.get("transfer_rules.yaml", {}).get("rules", [])]
    assert ("brokerage-contribution" in rule_names) == bool(contributions)
    assert all(r.account == hh.CHECKING and plan.has(hh.BROKERAGE) for r in contributions)
    household = plan.config.get("assumptions.yaml", {}).get("household", {})
    assert ("margin_apr" in household) == plan.has(hh.BROKERAGE)
    assert ("retirement_accounts" in household) == plan.has(hh.RETIREMENT)
    rules = plan.config.get("category_rules.yaml", {})
    assert (paths.DAYCARE_RULE in rules.get("keyword_rules", [])) == ("paths.keyword.below" in plan.planted)
    assert ("venmo_patterns" in plan.config.get("transfer_rules.yaml", {})) == any(
        h in plan.planted for h in ("zelle.own_pair", "zelle.third_party", "p2p.venmo_above", "p2p.venmo_below"))
    payrolls = [r["employer"] for r in rules.get("income_rules", []) if "employer" in r]
    assert sorted(payrolls) == sorted({e for h, e in (("income.alex_first", hh.QUILLWORKS),
                                                      ("income.alex_second", hh.LARKSPUR),
                                                      ("income.sam", hh.HARBOR_CITY)) if h in plan.planted})
    cancelled = plan.config.get("assumptions.yaml", {}).get("cancelled_subscriptions", [])
    assert bool(cancelled) == ("subscriptions.cancelled" in plan.planted)
    splits = [p for p in plan.config.get("vendor_rules.yaml", {}).get("patterns", []) if "split" in p]
    assert bool(splits) == ("split.vendor" in plan.planted)
    assert bool(plan.config.get("overrides.yaml")) == ("conflict.row" in plan.planted)
    assert bool(plan.answers) == any(h in plan.planted for h in ("learned.answered", "vendor_answer.vendor",
                                                                  "conflict.row"))
    # every regular stream the plan declares is posted: a subscription the window cannot hold is not declared
    for item in plan.recurring:
        assert sum(r.account == item.account and r.expect is not None and r.expect.vendor == item.vendor
                   for r in rows) >= 3, item.vendor
    # the tree writes and its truth names every planted handle
    doc = truth.build(plan, sources.write(plan, tmp_path))
    assert set(doc["planted"]) == set(plan.planted)
    if shape == "year":                                       # Alex changed jobs before this window opened
        assert "income.alex_first" not in plan.planted and "income.alex_second" in plan.planted
        assert len(plan.planted) >= 25
    if shape == "feed-only":
        assert "future.uid" in plan.planted and "seam.twins" not in plan.planted


def test_a_profile_takes_its_own_snapshot_schedule():
    daily = replace(FULL, name="daily", schedule=Schedule(monthly=False, recent_days=4, step=1))
    plan = plan_mod.build(daily, DEMO_END)
    assert plan.snapshot_days() == tuple(DEMO_END - timedelta(days=k) for k in (4, 3, 2, 1, 0))
    days = plan_mod.build(FULL, DEMO_END).snapshot_days()
    # every month end from the feed's first month before the end's month, then the end's last 21 days every third
    assert len(days) == FULL.feed_months + 8
    assert days[-8:] == tuple(DEMO_END - timedelta(days=k) for k in range(21, -1, -3))


def test_file_rows_past_the_cutover_stay_when_the_feed_never_reports_the_account(tmp_path):
    plan = plan_mod.build(FULL, DEMO_END)
    card, cutover = hh.CARD_ALEX, plan.cutover(hh.CARD_ALEX)
    files = sources.write_statements(plan, tmp_path / "imports")
    snapshots, feed_rows = sources.write_snapshots(plan, tmp_path / "store", omit=frozenset({card}))
    written = sources.Written(files, snapshots, feed_rows)
    assert snapshots and all(card not in s.accounts and hh.CHECKING in s.accounts for s in snapshots)
    assert feed_rows.get(card, ()) == ()
    stored = truth.stored_rows(plan, written)
    mine = [r for r in stored.rows.values() if r.account == card]
    past = [r for r in mine if r.date >= cutover]
    assert past and max(r.date for r in past) == max(e.last for e in plan.exports(card))
    assert stored.seam[card]["clipped"] == 0
    # the accounts the feed does report are clipped as before
    assert stored.seam[hh.CHECKING]["clipped"] > 0


def test_an_account_the_feed_connects_too_late_to_stop_reporting_is_reported_to_the_end(tmp_path):
    """The 401(k) connects on the first of the end's month, and only the snapshot on the end (the 2nd) comes after:
    there is no report to carry, so the feed never stops reporting it — no stale account, no handle, and the last
    snapshot lists it. Counting every snapshot instead would leave it out of all of them."""
    end = date(2026, 10, 2)
    late = replace(FULL, name="late", accounts=tuple(replace(s, feed_after_months=30) if s.account == hh.RETIREMENT
                                                     else s for s in FULL.accounts))
    plan = plan_mod.build(late, end)
    # thirty months after the feed's start is after the end: the connection is capped at the first of the end's month
    assert plan.connected(hh.RETIREMENT) == date(2026, 10, 1) and len(plan.snapshot_days()) > 3
    assert "stale.account" not in plan.planted and hh.RETIREMENT not in plan.stale
    written = sources.write(plan, tmp_path)
    assert hh.RETIREMENT in written.snapshots[-1].accounts
    checks = truth.build(plan, written)["figures"]["checks"]
    assert not [n for n in checks if n.startswith("simplefin.stale.")]
    # with a snapshot to spare before the two it leaves out, the full profile's 401(k) is stale as before, and it alone
    assert plan_mod.build(FULL, DEMO_END).stale == {hh.RETIREMENT: balance_sheet.STALE_SNAPSHOTS}


@pytest.fixture(scope="module")
def full_plan() -> plan_mod.Plan:
    return plan_mod.build(FULL, DEMO_END)


ZONE = ZoneInfo(hh.TIMEZONE)
CHARITY = {"category": "Giving", "subcategory": "Charity"}
NEVER_POSTED = Row(hh.CHECKING, date(2026, 6, 1), -1_000, "NEVER POSTED",
                   expect=Expect("purchase", "Cash & Misc", "Uncategorized", "fallback", "Never Posted"))


def unanswered(plan: plan_mod.Plan) -> Row:
    """A posted row of checking nobody answered."""
    answered = {id(a.target) for a in plan.answers}
    return next(r for r in plan.ledger.rows(hh.CHECKING) if not r.pending and id(r) not in answered)


def answered(plan: plan_mod.Plan) -> Row:
    return next(a.target for a in plan.answers if isinstance(a.target, Row))


def pending(plan: plan_mod.Plan) -> Row:
    return next(r for a in plan.ledger.accounts() for r in plan.ledger.rows(a) if r.pending)


def evening_after(row: Row) -> datetime:
    return datetime.combine(row.date + timedelta(days=1), time(20), tzinfo=ZONE)


def answer_on_its_own_day(plan: plan_mod.Plan) -> None:
    row = unanswered(plan)
    plan.answer(row, CHARITY, datetime.combine(row.date, time(23, 59), tzinfo=ZONE))


REFUSALS = {
    "answer-at-a-naive-instant": (lambda p: p.answer(unanswered(p), CHARITY, datetime(2026, 1, 1, 12)), "zone-aware"),
    "answer-at-the-ingest": (lambda p: p.answer(unanswered(p), CHARITY, p.ingest_at()), "before the ingest"),
    "answer-after-the-ingest": (lambda p: p.answer(unanswered(p), CHARITY, p.ingest_at() + timedelta(hours=1)),
                                "before the ingest"),
    "answer-on-the-rows-own-day": (answer_on_its_own_day, "later day"),
    "answer-an-unposted-row": (lambda p: p.answer(NEVER_POSTED, CHARITY, evening_after(NEVER_POSTED)), "posted"),
    "answer-a-pending-row": (lambda p: p.answer(pending(p), CHARITY, p.ingest_at() - timedelta(minutes=1)),
                             "posted"),
    "answer-a-row-twice": (lambda p: p.answer(answered(p), CHARITY, evening_after(answered(p))), "answered once"),
    "vendor-answer-reaching-another-vendor": (lambda p: p.answer(review.PEDIATRICS.vendor, CHARITY, p.ingest_at()
                                                                 - timedelta(days=1), reaches=(unanswered(p),)),
                                              "purchases of"),
    "recurring-declared-twice": (lambda p: p.declare_recurring(p.recurring[0]), "declared once"),
    "learned-vendor-declared-twice": (lambda p: p.learns(p.learned[0]), "declared once"),
    "split-share-on-half-a-cent": (lambda p: review.wholesale_legs(-12_345), "half a cent"),
    "answer-of-a-row-never-stored": (lambda p: sources.answer_documents(p, {}), "not stored"),
    "subscription-weekly": (lambda p: subscriptions.Subscription("x", subscriptions.NETFLIX, hh.CARD_ALEX, "weekly",
                                                                 9, 1_549), "monthly or annual"),
    "subscription-on-the-29th": (lambda p: subscriptions.Subscription("x", subscriptions.NETFLIX, hh.CARD_ALEX,
                                                                      "monthly", 29, 1_549), "monthly or annual"),
    "subscription-for-nothing": (lambda p: subscriptions.Subscription("x", subscriptions.NETFLIX, hh.CARD_ALEX,
                                                                      "monthly", 9, 0), "positive price"),
    "subscription-raised-by-under-one-percent": (lambda p: subscriptions.Subscription(
        "x", subscriptions.NETFLIX, hh.CARD_ALEX, "monthly", 9, 1_549, raised_cents=1_564), "one percent"),
    "feed-after-months-without-the-feed": (lambda p: AccountSources(hh.CHECKING, 0, CHECKING_FILES,
                                                                    feed_after_months=1), "feed_after_months"),
    "feed-after-months-before-the-feed": (lambda p: AccountSources(hh.CHECKING, 0, feed=True, feed_after_months=-1),
                                          "feed_after_months"),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_what_the_frame_refuses(full_plan, case):
    """Each refusal of the answers, the household's declarations, the split's legs, the answers file, a
    subscription and an account's sources, on the full plan; a refused call changes nothing."""
    call, match = REFUSALS[case]
    before = (len(full_plan.answers), len(full_plan.recurring), len(full_plan.learned))
    with pytest.raises(ValueError, match=match):
        call(full_plan)
    assert (len(full_plan.answers), len(full_plan.recurring), len(full_plan.learned)) == before


def test_two_different_rows_under_one_uid_are_refused():
    plan = plan_mod.build(FULL, DEMO_END)
    day = plan.start + timedelta(days=3)
    expect = Expect("purchase", "Groceries", None, "vendor_rule", "Trader Joe's")
    one, other = (Row(hh.CHECKING, day, -1_234, "TRADER JOE'S #552", expect=expect) for _ in range(2))
    assert one == other and one is not other                  # equal in every field, two ledger rows
    written = sources.Written((sources.StatementFile(hh.CHECKING, "a.csv", (one,)),
                               sources.StatementFile(hh.CHECKING, "b.csv", (other,))), (), {})
    with pytest.raises(ValueError, match="uid"):
        truth.stored_rows(plan, written)


@pytest.mark.parametrize(("files", "match"), [
    (lambda: Files("chase_checking", "spans", overlap_months=6), "overlap"),
    (lambda: Files("chase_checking", "spans", overlap_months=-1), "overlap"),
    (lambda: Files("chase_checking", "spans", overlap_months=4, span_months=4), "overlap"),
    (lambda: Files("chase_checking", "spans", span_months=0), "span_months"),
    (lambda: Files("bofa_bank", "months", reexport_months_before=40).exports(
        hh.SAVINGS, date(2025, 1, 1), date(2026, 1, 1), DEMO_END), "re-export"),
    (lambda: Files("bofa_bank", "months", reexport_months_before=-2).exports(
        hh.SAVINGS, date(2025, 1, 1), date(2026, 1, 1), DEMO_END), "re-export"),
], ids=["overlap-as-long-as-an-export", "negative-overlap", "overlap-as-long-as-a-short-export", "empty-span",
        "reexport-before-the-files", "reexport-after-them"])
def test_files_refuse_an_endless_overlap_and_a_reexport_outside_their_range(files, match):
    with pytest.raises(ValueError, match=match):
        files()


def test_settlements_run_after_every_behaviour():
    """An ordinary behaviour appended to `full` that spends on Sam's card: the next month's card payment, a
    settlement, pays what the card owed with that purchase in it, without the behaviour being placed anywhere."""
    day = date(2026, 6, 17)
    cents = 12_345

    def splurge(plan):
        if plan.has(hh.CARD_SAM):
            plan.post(Row(hh.CARD_SAM, day, -cents, "TALLGRASS GENERAL STORE",
                          expect=Expect("purchase", "Cash & Misc", "Uncategorized", "fallback",
                                        "Tallgrass General Store")))

    assert transfers.card_payments not in FULL.behaviours and transfers.card_payments in FULL.settlements
    more = plan_mod.build(replace(FULL, behaviours=(*FULL.behaviours, splurge)), DEMO_END)
    base = plan_mod.build(FULL, DEMO_END)

    def payment(plan) -> int:
        july = [r for r in plan.ledger.rows(hh.CHECKING, date(2026, 7, 1), date(2026, 7, 31))
                if r.desc == transfers.PAYMENT_SENT.format(last4=hh.CARD_SAM[-4:])]
        assert len(july) == 1
        return -july[0].cents

    owed = -more.ledger.balance(hh.CARD_SAM, date(2026, 6, 30))
    assert payment(more) == owed == payment(base) + cents
    # a behaviour and a settlement may not share a name
    with pytest.raises(ValueError, match="card_payments"):
        plan_mod.build(replace(FULL, behaviours=(*FULL.behaviours, transfers.card_payments)), DEMO_END)


def test_a_settlement_rows_feed_id_is_its_month():
    """A refund large enough to leave Sam's card in credit at a month's end skips the next payment: every other
    month's payment keeps its feed id (and with it its uid), which names the month, not a count of payments."""
    refund_day = date(2026, 3, 10)

    def refund(plan):
        plan.post(spending.ALDI.purchase(hh.CARD_SAM, refund_day, 123_457, card=True))

    def payments(plan) -> dict[str, str]:
        out = {}
        for account in (hh.CHECKING, hh.CARD_SAM):
            for r in plan.ledger.rows(account):
                if r.feed_id and "-card_payments-" in r.feed_id and (
                        account == hh.CARD_SAM or r.desc == transfers.PAYMENT_SENT.format(last4=hh.CARD_SAM[-4:])):
                    out[r.feed_id] = r.date.isoformat()[:7]
        return out

    base = payments(plan_mod.build(FULL, DEMO_END))
    more = payments(plan_mod.build(replace(FULL, behaviours=(*FULL.behaviours, refund)), DEMO_END))
    skipped = set(base) - set(more)
    assert skipped and set(more) < set(base)                  # some months have no payment, no other id moved
    assert all(more[f] == base[f] for f in more)              # and each id still names its own month's payment
    assert all(month in feed_id for feed_id, month in base.items())
    assert max(base[f] for f in skipped) < max(more.values())     # payments resume after the skipped months


def test_truth_carries_a_stated_value_dated_after_the_newest_report_back(tmp_path):
    """A value stated after the newest report is held at that value on every earlier day: each of truth's snapshots
    and its daily line's last point gain it, and no snapshot lists it as missing."""
    def later(plan):
        plan.add("accounts.yaml", "manual_entries", [{
            "account": "Boat", "institution": "Harbor County Assessor", "owner": hh.JOINT, "asset_class": "Car",
            "value": 12_000.0, "as_of": (plan.end + timedelta(days=3)).isoformat()}])

    base_plan = plan_mod.build(FULL, DEMO_END)
    base = truth.build(base_plan, sources.write(base_plan, tmp_path / "base"))["figures"]
    plan = plan_mod.build(replace(FULL, behaviours=(*FULL.behaviours, later)), DEMO_END)
    got = truth.build(plan, sources.write(plan, tmp_path / "later"))["figures"]
    assert [s["date"] for s in got["snapshots"]] == [s["date"] for s in base["snapshots"]]
    for s, s0 in zip(got["snapshots"], base["snapshots"], strict=True):
        assert (s["total_cents"], s["missing"]) == (s0["total_cents"] + 1_200_000, s0["missing"]), s["date"]
        assert s["by_class"]["Car"] == s0["by_class"].get("Car", 0) + 1_200_000, s["date"]
    assert got["daily_last"]["total_cents"] == base["daily_last"]["total_cents"] + 1_200_000
    assert [a["id"] for a in got["manual_assets"]] == ["boat", "family-home", "sedan"]


def ingested(plan: plan_mod.Plan, tmp_path: Path) -> tuple[Path, dict]:
    """`plan`'s tree written as the generator writes one, ingested at the end day's ingest instant: (its exports, its
    truth)."""
    home = tmp_path / "home"
    doc = generate.write_tree(plan, home)
    return ingest_demo(home, DEMO_NOW), doc


def numbers(doc: object, path: str = "") -> list[tuple[str, float]]:
    """Every number in a JSON document, with where it sits."""
    if isinstance(doc, dict):
        return [n for k, v in doc.items() for n in numbers(v, f"{path}.{k}")]
    if isinstance(doc, list):
        return [n for i, v in enumerate(doc) for n in numbers(v, f"{path}[{i}]")]
    return [(path, doc)] if isinstance(doc, (int, float)) and not isinstance(doc, bool) else []


APP_CAR_CENTS = (1_950_000, 1_875_000)          # the car's values as the household enters them in the app


def test_an_app_asset_with_a_config_entrys_id_replaces_that_entry(tmp_path):
    """The household enters the car in the app under its accounts.yaml entry's id, with two values of its own: truth
    lists one asset of that id, from the app, at the app's values (stating no acquisition day, so it is carried back
    before its first value); the engine agrees on every figure, and the entry's value is nowhere in the exports but
    in accounts.json `manual_overridden`, which lists the entry the app asset hides."""
    stated: list[dict] = []

    def car_in_app(plan):
        [entry] = plan.config["accounts.yaml"]["manual_entries"]
        stated.append(entry)
        first = date.fromisoformat(entry["as_of"])
        values = ((first, APP_CAR_CENTS[0]), (shift_months(first, 3), APP_CAR_CENTS[1]))
        plan.enter(plan_mod.AppAsset(plan_mod.manual_id(entry["account"]), entry["account"], entry["owner"],
                                     entry["asset_class"], False, values,
                                     datetime.combine(values[-1][0], time(12), tzinfo=ZONE)))

    plan = plan_mod.build(replace(FULL, behaviours=(*FULL.behaviours, car_in_app)), DEMO_END)
    exports, doc = ingested(plan, tmp_path)
    [entry] = stated
    car_id = plan.planted["car.entry"]
    declared = [a for a in doc["figures"]["manual_assets"] if a["id"] == car_id]
    assert [(a["origin"], a["acquired"], [v["value_cents"] for v in a["values"]]) for a in declared] == [
        ("app", None, list(APP_CAR_CENTS))]
    assert demo_checks.mismatches(exports, doc) == []
    exported = json.loads((exports / "accounts.json").read_text("utf-8"))["manual_assets"]
    assert [a["origin"] for a in exported if a["id"] == car_id] == ["app"]
    assert entry["value"] not in [c / 100 for c in APP_CAR_CENTS]
    found = [(f.name, where) for f in sorted(exports.glob("*.json"))
             for where, n in numbers(json.loads(f.read_text("utf-8"))) if n == entry["value"]]
    assert found == [("accounts.json", ".manual_overridden[0].values[0].value")]
    hidden = json.loads((exports / "accounts.json").read_text("utf-8"))["manual_overridden"]
    assert [(a["id"], a["origin"], a["name"]) for a in hidden] == [(car_id, "config", entry["account"])]


@pytest.mark.parametrize("where", ["connection-day", "month-end"])
def test_a_home_value_on_the_day_the_balance_sheet_connects_or_on_its_month_end(tmp_path, monkeypatch, where):
    """The home's middle value moved onto the day the feed connects the balance-sheet accounts, or onto that month's
    end (their first report, a snapshot date and a point of the line): truth and the engine agree on every snapshot,
    its manual rows and the daily line."""
    home_values = balance_sheet.home_values

    def moved(plan):
        first, middle, last = home_values(plan)
        connected = plan.connected(hh.MORTGAGE)
        return first, (connected if where == "connection-day" else month_end(connected), middle[1]), last

    monkeypatch.setattr(balance_sheet, "home_values", moved)
    plan = plan_mod.build(FULL, DEMO_END)
    connected = plan.connected(hh.MORTGAGE)
    assert all(plan.connected(a) == connected for a in (hh.BROKERAGE, hh.RETIREMENT, hh.CHILD_SAVINGS, hh.CERTIFICATE))
    [home] = plan.manual_assets
    day = home.values[1][0]
    assert day == (connected if where == "connection-day" else month_end(connected))
    exports, doc = ingested(plan, tmp_path)
    assert day.isoformat() in doc["figures"]["daily_points"]
    if where == "month-end":
        assert day.isoformat() in [s["date"] for s in doc["figures"]["snapshots"]]
        assert day in plan.snapshot_days()                         # the balance sheet's first report
    assert demo_checks.mismatches(exports, doc) == []


def test_truth_refuses_a_loan_the_ledger_holds_as_an_asset(tmp_path):
    def windfall(plan):
        plan.ledger.adjust(hh.MORTGAGE, date(2026, 6, 1), 50_000_000)

    plan = plan_mod.build(replace(FULL, behaviours=(*FULL.behaviours, windfall)), DEMO_END)
    with pytest.raises(ValueError, match=hh.MORTGAGE):
        truth.build(plan, sources.write(plan, tmp_path))


MOVE_DAY = date(2026, 6, 8)                     # a Monday in the feed's months


def internal(vendor: str, pair: str | None = None, gap: str | None = None) -> Expect:
    return Expect("transfer", "Internal", "transfer", "internal", vendor, internal=True, pair=pair, gap=gap)


def test_a_move_whose_amount_has_a_rival_is_refused_without_the_behaviour_asking():
    """A move declares its legs alone itself, and so does a posted row that is internal or that nothing files (the
    engine's matcher reads both); `alone=False` takes a pair out on purpose."""
    cents = 31_337

    def refund(plan):            # the rival: a grocer's refund of the same amount on Sam's card two days later
        plan.post(spending.TRADER_JOES.purchase(hh.CARD_SAM, MOVE_DAY + timedelta(days=2), cents, card=True))

    def move(plan, **flag):
        plan.move(Row(hh.CHECKING, MOVE_DAY, -cents, transfers.TO_SAVINGS, expect=internal("Online Transfer To Sav",
                                                                                           "rival")),
                  Row(hh.SAVINGS, MOVE_DAY, cents, transfers.FROM_CHECKING,
                      expect=internal("Online Transfer From Chk", "rival")), **flag)

    def asked(plan):
        move(plan)

    def unasked(plan):
        move(plan, alone=False)

    def outflow(plan):                              # a row nothing files, with the refund as its opposite
        plan.post(Row(hh.CHECKING, MOVE_DAY, -cents, "CORNER KIOSK",
                      expect=Expect("purchase", "Cash & Misc", "Uncategorized", "fallback", "Corner Kiosk")))

    for behaviour in (asked, outflow):
        with pytest.raises(ValueError, match="rivals"):
            plan_mod.build(replace(FULL, behaviours=(*FULL.behaviours, refund, behaviour)), DEMO_END)
    plan_mod.build(replace(FULL, behaviours=(*FULL.behaviours, refund, unasked)), DEMO_END)


@pytest.mark.parametrize(("wording", "refused"), [
    ((transfers.TO_SAVINGS, transfers.FROM_CHECKING), True),
    (("ZELLE PAYMENT TO ALEX SAMPLE 40404040", "ZELLE PAYMENT FROM ALEX SAMPLE 40404040"), False),
], ids=["bank-transfer", "person-to-person"])
def test_a_pair_further_apart_than_its_window_is_refused(tmp_path, wording, refused):
    """Legs four business days apart: past the bank window (match_window_bdays, 3), inside the person-to-person one
    (venmo_match_window_bdays, 5), both as the generated assumptions.yaml states them."""
    out, into = wording

    def far(plan):
        plan.move(Row(hh.CHECKING, MOVE_DAY, -27_183, out, expect=internal("Out", "far")),
                  Row(hh.SAVINGS, MOVE_DAY + timedelta(days=4), 27_183, into, expect=internal("Into", "far")))

    plan = plan_mod.build(replace(FULL, behaviours=(*FULL.behaviours, far)), DEMO_END)
    written = sources.write(plan, tmp_path)
    if refused:
        with pytest.raises(ValueError, match="window"):
            truth.build(plan, written)
        return
    pairs = truth.build(plan, written)["figures"]["pairs"]
    stored = truth.stored_rows(plan, written)
    legs = sorted(stored.uid_of[id(r)] for a in (hh.CHECKING, hh.SAVINGS) for r in plan.ledger.rows(a)
                  if abs(r.cents) == 27_183)
    assert len(legs) == 2 and legs in [sorted([p["out"], p["into"]]) for p in pairs]


def test_a_gap_outside_the_hinted_accounts_data_is_refused(tmp_path):
    """A gap in the data is a leg on a day the hinted account's own rows cover: Sam's card has none before the feed."""
    def early_gap(plan):
        plan.post(Row(hh.CHECKING, plan.start + timedelta(days=36), -41_421, "BRANCH XFER TO CARD",
                      expect=internal("Branch Xfer To Card", gap=hh.CARD_SAM)))

    plan = plan_mod.build(replace(FULL, behaviours=(*FULL.behaviours, early_gap)), DEMO_END)
    with pytest.raises(ValueError, match="window|data"):
        truth.build(plan, sources.write(plan, tmp_path))


@pytest.mark.parametrize(("share", "floor"), [(0.75, "2024-10"), (0.755, "2024-12")])
def test_a_coverage_share_that_is_not_a_whole_percent_is_read_exactly(tmp_path, share, floor):
    """Four everyday accounts with rows, first in 2024-07, 2024-07, 2024-10 and 2024-12: three of them are 75 percent,
    so a share of 0.755 needs the fourth."""
    def coverage(plan):
        plan.put("assumptions.yaml", "coverage", {"min_share": share})

    base = replace(FULL, accounts=tuple(s for s in FULL.accounts
                                        if s.account in (hh.CHECKING, hh.SAVINGS, hh.CARD_ALEX, hh.CARD_SAM)))
    plan = plan_mod.build(replace(base, behaviours=(*FULL.behaviours, coverage)), DEMO_END)
    assert truth.build(plan, sources.write(plan, tmp_path))["figures"]["coverage_floor"] == floor


def test_a_stream_belongs_to_one_behaviour_and_a_seed_shares_another_profiles_rows():
    def first(plan):
        plan.stream("shared").random()

    def second(plan):
        plan.stream("shared").random()

    with pytest.raises(ValueError, match="shared"):
        plan_mod.build(replace(FULL, name="clash", behaviours=(first, second)), DEMO_END)
    full, twin, other = (
        [(r.account, r.date, r.cents, r.desc) for a in p.ledger.accounts() for r in p.ledger.rows(a)]
        for p in (plan_mod.build(profile, DEMO_END) for profile in (
            FULL, replace(FULL, name="full-twin", seed="full"), replace(FULL, name="full-twin"))))
    assert twin == full != other
    # a profile seeds its streams with its own name unless it names another's
    assert (FULL.stream_seed, replace(FULL, name="x").stream_seed) == ("full", "x")


# ------------------------------------------------------------------ what the dashboard's suites need of `full`

SOURCES_SHOWN = ("vendor_rule", "keyword", "bank_label", "learned", "answer", "split", "fallback")
EVERYDAY = ("Housing", "Utilities", "Groceries", "Transport", "Health", "Insurance")


def test_the_shapes_the_dashboard_suites_need(demo_exports):
    """The contract between the `full` household and the dashboard's test suites, read from the exports alone: each
    assertion is one shape a suite renders, and its message names the need."""
    def load(name: str) -> dict:
        return json.loads((demo_exports.exports / name).read_text("utf-8"))

    txns = load("transactions.json")["transactions"]
    money = [t for t in txns if not t["is_parent"]]
    accounts = load("accounts.json")["accounts"]
    assumptions = load("assumptions.json")
    months = load("cashflow_monthly.json")["months"]
    full = [m for m in months if m["month"] < demo_exports.truth["end"][:7]]     # every month before the end's
    snapshots = load("assets_timeseries.json")["snapshots"]
    holdings = load("holdings.json")["holdings"]
    reserved = assumptions["reserved"]
    spending = set(assumptions["taxonomy"]) - {reserved["income_category"], reserved["internal_category"],
                                               reserved["fees_category"], reserved["fallback_category"]}
    names = {a["name"] for a in accounts} | {r["account"] for s in snapshots for r in s["rows"]}
    sources = Counter(t["category_source"] for t in txns)

    assert any(a["txn_data_start"] and a["ledger_drift"] for a in accounts), \
        "account page: an account with transactions and a ledger-drift figure"
    assert spending <= {t["category"] for t in money if t["type"] == "purchase" and t["amount"] < 0}, \
        "categories: a purchase in every spending category of the taxonomy, Subscriptions included"
    assert any(i["active"] and i["schedule"] for i in load("recurring.json")["items"]), \
        "recurring and forecast: an active recurring item with a schedule"
    assert len([m for m in full if m["in_coverage"]]) >= 12, \
        "trends and baselines: at least 12 full months in coverage before the newest month"
    ends_month = next(m for m in months if m["month"] == demo_exports.truth["end"][:7])
    assert len([m for m in months if m["full_coverage"]]) >= 12 and ends_month["full_coverage"] and any(
        not m["full_coverage"] for m in full), \
        "cash flow: at least 12 months every cash-flow account covers, a month before them an account does not cover " \
        "yet, and a newest month every account covers (Home's net-worth change carries no incomplete-data line)"
    assert all(any(v > 0 for s, v in m["income_by_subcategory"].items() if s != reserved["income_other_sub"])
               for m in full[-3:]), \
        "income: a positive income subcategory other than the catch-all in each of the newest three full months"
    assert [-r["value"] for r in snapshots[-1]["rows"] if r["asset_class"] == "Mortgage"] == [
        assumptions["mortgage"]["balance"]], \
        "mortgage and planning: one Mortgage row in the newest snapshot, equal to the mortgage block's balance"
    assert holdings and all(h["account_id"] for h in holdings), \
        "investments: holdings, each naming its account"
    adults = [p for p in assumptions["household"]["people"] if p["role"] == "owner"]
    assert len(adults) == 2 and all(p.get("birth_year") for p in adults), \
        "planning: two adults, each with a birth year"
    assert all(len([c for c in EVERYDAY if m["by_category"].get(c, {}).get("total", 0) > 0]) >= 2
               for m in full[-6:]), \
        "budgets and the essentials: two everyday categories with spending in each of the last six full months"
    assert any(re.search(r"\d\.\d{2}\b", g["context"]) for g in load("open_questions.json")["data_gaps"]), \
        "data page: a data gap whose context holds an amount with two decimals"
    assert any(c["status"] == "warn" for c in load("quality_report.json")["checks"]), \
        "data page: at least one check that warns"
    assert set(assumptions["household"]["retirement_accounts"]) & names, \
        "planning: household.retirement_accounts names an account that exists"
    assert len([s for s in snapshots[:-1] if not s["missing"]]) >= 18, \
        "net worth history: at least 18 month-end snapshots with nothing missing"
    assert {t["id"] for t in txns if t["is_parent"]} & {t["parent_id"] for t in txns}, \
        "register: a split purchase with its parts"
    assert all(sources[s] for s in SOURCES_SHOWN), \
        "register and review: rows filed by every way the engine files one: " + ", ".join(SOURCES_SHOWN)


# the second-house scenarios tools/verify/screenshots.sh serves beside a household's exports
SCENARIOS_FIXTURE = ROOT / "tools" / "verify" / "fixtures" / "scenarios.synthetic.json"
OVERDRAW_MARGIN = 5_000             # dollars past checking's balance: a small change to the generator keeps it an overdraw


def cash_needed(inputs: dict) -> float:
    """The worksheet's cash at and after closing (`cashNeeded`, app/src/lib/secondHouse.ts): the down payment, the
    closing costs and the costs after moving in."""
    return inputs["price"] * inputs["downPct"] / 100 + inputs["closingCosts"] + inputs["postMoveInCosts"]


def test_the_screenshot_scenarios_open_fully_funded_and_overdraw_checking_in_the_second_alone(demo_exports):
    """The page opens on the fixture's first scenario, which every shot of `full` shows as the household's plan, so it
    is fully funded: its draws add up to the cash it needs and each fits within its account's exported balance. The
    second scenario's checking draw is the one overdraw (the ⚠ the scenario tabs and the comparison show on every
    shot): it exceeds checking's balance by a clear margin, while its draws still add up to its cash needed, so the
    overdraw is its only verdict."""
    balance = {a["id"]: a["latest_balance"]
               for a in json.loads((demo_exports.exports / "accounts.json").read_text("utf-8"))["accounts"]}
    scenarios = json.loads(SCENARIOS_FIXTURE.read_text("utf-8"))["scenarios"]
    for s in scenarios[:2]:
        assert sum(s["draws"].values()) == cash_needed(s["inputs"]), f"{s['name']}: the draws add up to the cash needed"
    draw = scenarios[1]["draws"].get(hh.CHECKING, 0)
    assert draw >= balance[hh.CHECKING] + OVERDRAW_MARGIN, (
        f"the second scenario must draw at least ${OVERDRAW_MARGIN:,} more from {hh.CHECKING} than its exported "
        f"balance of ${balance[hh.CHECKING]:,.2f} at {DEMO_END}; it draws ${draw:,}")
    over = [f"{s['name']}: {account} draws ${amount:,} of ${balance[account]:,.2f}"
            for i, s in enumerate(scenarios) for account, amount in s["draws"].items()
            if (i, account) != (1, hh.CHECKING) and amount > balance[account]]
    assert over == [], "every other draw fits within its account's exported balance"

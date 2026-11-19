# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""etl/quality/reconcile.ledger_drift: the two newest SimpleFIN balances >= 7 days apart vs
every row of the account (CSV and feed) between them. Always `info` (pending items the
parser skips make drift ~= the pending total on most runs). Synthetic rows only."""
from __future__ import annotations

from conftest import make_txn

from etl.models import BalanceObs
from etl.quality import reconcile


def obs(day, cents, account="card", source="simplefin"):
    return BalanceObs(account_id=account, as_of=f"2026-09-{day:02d}", balance_cents=cents, source=source)


def feed(day, cents, account="card"):
    return make_txn(date=f"2026-09-{day:02d}", account=account, desc="FEED ROW", cents=cents, bank_type="simplefin")


def test_card_arithmetic_negative_balances_and_the_half_open_window():
    balances = [obs(1, -50000), obs(5, -52000), obs(10, -65000)]          # the 5th is < 7 days before the 10th
    rows = [feed(1, -4000), feed(2, -10000), feed(9, -6000), feed(10, -1500), feed(11, -9999)]   # 1st excluded, 10th included, 11th after
    res = reconcile.ledger_drift("card", "Synth Card", balances, rows)
    assert res is not None
    check, payload = res
    assert check.name == "reconcile.card.ledger_drift" and check.status == "info"
    assert payload == {"from": "2026-09-01", "to": "2026-09-10", "expected_cents": -15000,
                       "got_cents": -17500, "drift_cents": -2500}
    assert "2026-09-01→2026-09-10" in check.detail and "pending items" in check.detail
    assert "Synth Card" in check.detail and "FEED ROW" not in check.detail


def test_checking_account_with_deposits_and_a_large_drift_is_still_info():
    balances = [obs(1, 100000, "chk"), obs(20, 120000, "chk")]
    rows = [feed(5, 50000, "chk"), feed(6, -25000, "chk"), feed(15, 900000, "chk")]    # an unposted-looking $9k lands anyway
    check, payload = reconcile.ledger_drift("chk", "Synth Checking", balances, rows)
    assert (payload["expected_cents"], payload["got_cents"], payload["drift_cents"]) == (20000, 925000, 905000)
    assert check.status == "info"                                           # never warn


def test_none_without_two_simplefin_observations_seven_days_apart():
    assert reconcile.ledger_drift("card", "C", [obs(10, -1)], [feed(9, -1)]) is None
    assert reconcile.ledger_drift("card", "C", [obs(5, -1), obs(10, -2)], [feed(9, -1)]) is None      # 5 days
    assert reconcile.ledger_drift("card", "C", [obs(1, -1, source="csv"), obs(10, -2)], [feed(9, -1)]) is None   # csv obs do not count
    assert reconcile.ledger_drift("card", "C", [obs(3, -1), obs(10, -2)], [feed(9, -1)]) is not None   # exactly 7 days


def test_other_accounts_observations_and_rows_are_ignored():
    balances = [obs(1, 0), obs(10, -1000), obs(1, 0, "other"), obs(10, -99999, "other")]
    rows = [feed(5, -1000), feed(5, -77777, "other")]
    _, payload = reconcile.ledger_drift("card", "C", balances, rows)
    assert payload["expected_cents"] == -1000 and payload["got_cents"] == -1000 and payload["drift_cents"] == 0


def test_picks_the_newest_pair_not_the_widest():
    balances = [obs(1, 0), obs(8, -100), obs(20, -300)]          # 20th vs 8th (>= 7 days), not vs 1st
    _, payload = reconcile.ledger_drift("card", "C", balances, [])
    assert (payload["from"], payload["to"], payload["expected_cents"]) == ("2026-09-08", "2026-09-20", -200)


def test_rows_before_a_cutover_inside_the_window_count_toward_got():
    """The CSV owns the days before a simplefin_cutover and the feed the days from it — both are this
    account's ledger. Summing feed rows alone undercounted `got` for the ~2 weeks a cutover sat inside
    (d1, d2]. A CSV row ON d1 stays out (half-open window)."""
    balances = [obs(1, 0), obs(10, -30000)]
    rows = [make_txn(date="2026-09-03", account="card", desc="CSV ROW", cents=-10000),      # bank_type None: a CSV row
            make_txn(date="2026-09-01", account="card", desc="CSV ROW", cents=-77777),      # on d1: excluded
            feed(8, -20000)]
    _, payload = reconcile.ledger_drift("card", "Synth Card", balances, rows)
    assert (payload["expected_cents"], payload["got_cents"], payload["drift_cents"]) == (-30000, -30000, 0)


def test_ledger_drift_inputs_groups_every_row_and_flags_the_accounts_the_feed_reaches():
    rows = [feed(5, -1, "a"), make_txn(date="2026-09-06", account="a", desc="X", cents=-2),
            make_txn(date="2026-09-06", account="b", desc="X", cents=-3)]
    inputs = reconcile.ledger_drift_inputs(rows)
    assert len(inputs["a"][0]) == 2 and inputs["a"][1] is True
    assert len(inputs["b"][0]) == 1 and inputs["b"][1] is False
    assert reconcile.ledger_drift_inputs([]) == {}


def split_card_purchase(cents=-30000, parts=((-21000, "Groceries", None), (-9000, "Cash & Misc", "Uncategorized"))):
    """a feed purchase on `card` split by rule: the parent flagged (its own category is the fallback the
    rulebook gave it — display only), two children with the parts"""
    parent = make_txn(date="2026-09-05", account="card", desc="SYNTH MART", cents=cents, bank_type="simplefin",
                      vendor="Synth Mart", category="Cash & Misc", subcategory="Uncategorized")
    parent.is_parent = True
    kids = [make_txn(date="2026-09-05", account="card", desc="SYNTH MART", cents=c, bank_type="simplefin",
                     vendor="Synth Mart", category=cat, subcategory=sub, category_source="split",
                     parent_id=parent.uid, split_index=i)
            for i, (c, cat, sub) in enumerate(parts, start=1)]
    return parent, kids


def test_ledger_drift_inputs_skips_split_parents_so_a_split_purchase_counts_once():
    parent, kids = split_card_purchase()
    rows, has_feed = reconcile.ledger_drift_inputs([parent, *kids])["card"]
    assert [t.amount_cents for t in rows] == [-21000, -9000] and has_feed is True
    _, payload = reconcile.ledger_drift("card", "C", [obs(1, 0), obs(10, -30000)], rows)
    assert (payload["got_cents"], payload["drift_cents"]) == (-30000, 0)       # the parent on top would read -60000


def test_month_sums_uncategorized_share_and_unmapped_vendors_count_a_split_purchase_once():
    parent, kids = split_card_purchase()
    rows = [parent, *kids]
    assert reconcile.account_month_sums(rows) == {"card": {"2026-09": -30000}}
    share, check = reconcile.uncategorized_share(rows)
    assert share == 9000 / 30000 and "30.0%" in check.detail                      # the parent's own Uncategorized never counts
    assert reconcile.unmapped_vendors(rows, known=set()) == [("Synth Mart", 30000, 2)]   # the parts; never the parent on top

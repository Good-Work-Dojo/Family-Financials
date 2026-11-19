# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Cross-file dedup correctness: uids must be stable when a refreshed export
re-orders rows, while identical same-day rows within one file stay distinct."""
import dataclasses
import hashlib

import pytest
from conftest import make_txn

from etl.pipeline import assign_occurrence_seq


def tx(date, desc, cents):
    return make_txn(date=date, desc=desc, cents=cents, source_file="f.csv")


def uids(txns):
    assign_occurrence_seq(txns)
    return {t.uid for t in txns}


def test_uids_stable_across_row_ordering():
    rows = [tx("2025-01-05", "STORE A", -100), tx("2025-02-10", "STORE B", -250),
            tx("2025-03-15", "PAYROLL", 700000)]
    oldest_first = uids(list(rows))
    newest_first = uids(list(reversed(rows)))
    assert oldest_first == newest_first


def test_identical_same_day_rows_stay_distinct():
    # e.g. two identical deposits on one day: same date, amount, description
    rows = [tx("2030-08-21", "PAYROLL DEPOSIT EXAMPLE CO", 310000),
            tx("2030-08-21", "PAYROLL DEPOSIT EXAMPLE CO", 310000)]
    assert len(uids(rows)) == 2


def test_overlapping_exports_dedup_to_one_set():
    old_export = [tx("2025-01-05", "STORE A", -100), tx("2025-01-06", "STORE B", -50)]
    new_export = [tx("2025-01-06", "STORE B", -50), tx("2025-01-05", "STORE A", -100),
                  tx("2025-01-07", "STORE C", -75)]   # newer file, reordered + 1 new
    u_old, u_new = uids(old_export), uids(new_export)
    assert u_old < u_new            # strict subset: old rows all dedup away
    assert len(u_new - u_old) == 1  # only the genuinely new row survives


def test_non_split_uids_are_the_phase_4_hash_exactly():
    """split_index 0 (every ordinary row) hashes account|date|amount|description|seq and nothing
    more: a changed uid would orphan every stored txn answer and every transfer_matches row."""
    t = tx("2025-01-05", "STORE A", -100)
    assign_occurrence_seq([t])
    key = f"{t.account_id}|{t.date}|{t.amount_cents}|{t.description_raw}|{t.seq}"
    assert t.uid == "tx_" + hashlib.sha1(key.encode()).hexdigest()[:16]
    assert (t.split_index, t.parent_id, t.is_parent) == (0, None, False)


def test_an_ordinary_uid_is_frozen_as_a_literal():
    """the test above recomputes the hash with the same formula it checks; this literal cannot drift with it
    (sha1 of 'a|2025-01-05|-100|STORE A|0') - it is what every stored answer and transfer_matches row names"""
    t = make_txn(account="a", date="2025-01-05", desc="STORE A", cents=-100)
    assert t.seq == 0
    assert t.uid == "tx_0a37cfbee8a04cb9"


def test_split_children_get_distinct_uids_even_with_equal_amounts_and_never_the_parents():
    parent = tx("2025-01-05", "STORE A", -300)
    twin = tx("2025-01-05", "STORE A", -150)          # an ordinary same-day row with a child's amount
    assign_occurrence_seq([parent, twin])
    kids = [dataclasses.replace(parent, amount_cents=-150, parent_id=parent.uid, split_index=i) for i in (1, 2)]
    assert len({parent.uid, twin.uid, *(k.uid for k in kids)}) == 4     # 50/50 legs differ only by split_index
    assert all(k.parent_id == parent.uid for k in kids)
    # a child hashes its PARENT's uid plus its index, so re-tuning a leg's percentage
    # (a different amount) keeps the part's uid, and with it any answer stored against it
    assert kids[0].uid == "tx_" + hashlib.sha1(f"{parent.uid}|s1".encode()).hexdigest()[:16]
    assert dataclasses.replace(kids[0], amount_cents=-90).uid == kids[0].uid
    # the ordinary hash is untouched: the same row with no split fields IS the twin
    assert dataclasses.replace(kids[0], split_index=0, parent_id=None).uid == twin.uid


def test_two_identical_looking_parents_get_distinct_child_uids():
    """two purchases of one store on one day for one amount (told apart only by seq) split into
    four children that must not collide on the txn_uid primary key"""
    a, b = tx("2025-01-05", "STORE A", -300), tx("2025-01-05", "STORE A", -300)
    assign_occurrence_seq([a, b])
    assert a.uid != b.uid
    kids = [dataclasses.replace(p, amount_cents=-150, parent_id=p.uid, split_index=i) for p in (a, b) for i in (1, 2)]
    assert len({k.uid for k in kids}) == 4
    assert not {k.uid for k in kids} & {a.uid, b.uid}


def test_children_of_different_parents_with_an_equal_leg_never_collide():
    """C4's reason to exist: two same-day, same-account, same-descriptor parents of DIFFERENT amounts
    are each the first of their kind (seq 0 both), so a leg of one and a leg of the other that share
    an amount and an index agree on every field the pre-C4 hash read (account|date|amount|desc|seq|s<n>);
    only the parent's uid tells them apart"""
    a, b = tx("2025-01-05", "STORE A", -300), tx("2025-01-05", "STORE A", -200)
    assign_occurrence_seq([a, b])
    assert a.seq == b.seq == 0 and a.uid != b.uid
    a1 = dataclasses.replace(a, amount_cents=-150, parent_id=a.uid, split_index=1)    # 50/50 of 300
    b1 = dataclasses.replace(b, amount_cents=-150, parent_id=b.uid, split_index=1)    # 75/25 of 200
    a2 = dataclasses.replace(a, amount_cents=-150, parent_id=a.uid, split_index=2)
    b2 = dataclasses.replace(b, amount_cents=-50, parent_id=b.uid, split_index=2)
    same = ("account_id", "date", "description_raw", "amount_cents", "seq", "split_index")
    assert [getattr(a1, f) for f in same] == [getattr(b1, f) for f in same]           # indistinguishable but for the parent
    assert len({a1.uid, a2.uid, b1.uid, b2.uid, a.uid, b.uid}) == 6


def test_a_split_index_without_a_parent_id_is_refused():
    orphan = tx("2025-01-05", "STORE A", -150)
    orphan.split_index = 1
    with pytest.raises(ValueError, match="split_index"):
        _ = orphan.uid      # the property raising is the assertion


def test_a_parent_id_without_a_split_index_is_refused():
    """the symmetric slip: a child that lost its index would hash like (and collide with) the ordinary row it was copied from"""
    lost = tx("2025-01-05", "STORE A", -150)
    lost.parent_id = "tx_0000000000000000"
    with pytest.raises(ValueError, match="parent_id"):
        _ = lost.uid

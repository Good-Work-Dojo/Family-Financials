# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Per-transaction override mechanism (config/overrides.yaml), on synthetic rows; the accounts are the demo
household's. Everything here is invented."""
import pytest
from conftest import load_demo, make_txn

from etl.enrich.overrides import apply
from tools.demo import household as hh


def tx(date, desc, cents, account=hh.CHECKING):
    return make_txn(date=date, desc=desc, cents=cents, account=account,
                    source_file="f.csv", category="Cash & Misc",
                    subcategory="Checks", needs_review=True)


def test_match_requires_all_fields_and_applies_set():
    rows = [tx("2030-02-11", "CHECK 4821", -1162500),
            tx("2030-02-11", "CHECK 4821", -100),       # amount mismatch
            tx("2030-02-11", "CHECK 9036", -1162500)]   # contains mismatch
    checks = apply(rows, [{
        "match": {"contains": "check 4821", "amount": -11625.00, "account": hh.CHECKING},
        "set": {"type": "transfer", "category": "Internal",
                "subcategory": "transfer", "needs_review": False},
    }])
    assert rows[0].type == "transfer" and rows[0].is_internal
    assert rows[0].subcategory == "transfer"
    assert not rows[0].needs_review
    assert rows[1].type == "purchase" and rows[2].type == "purchase"
    assert [c for c in checks if c.status == "warn"] == []


def test_tags_merge_not_replace():
    t = tx("2030-03-18", "CHECK 5307", -287500)
    t.tags = ["existing"]
    apply([t], [{"match": {"contains": "CHECK 5307"},
                 "set": {"category": "Housing", "tags": ["home"]}}])
    assert t.tags == ["existing", "home"]
    assert t.category == "Housing"
    assert t.subcategory == "Checks"     # untouched fields stay


def test_zero_match_entry_warns():
    checks = apply([tx("2030-01-07", "SOMETHING", -100)], [{
        "match": {"contains": "VANISHED DESCRIPTOR"},
        "set": {"category": "Housing"}, "note": "the porch repair",
    }])
    warns = [c for c in checks if c.status == "warn"]
    assert len(warns) == 1
    assert "the porch repair" in warns[0].detail


def test_family_fact_marks_and_excludes_from_unmapped_ranking():
    # A row an override (or answer — shared apply_set) resolved is a settled family
    # fact: it must stop ranking in top_unmapped_vendors, which otherwise resurfaces
    # answered items (a Zelle payment to a named person, a check) as if still unexplained.
    from etl.quality import reconcile

    resolved = tx("2030-04-09", "Zelle payment to Dana Synth 40417730215", -312500)
    resolved.vendor = "Zelle payment to Dana Synth"
    open_row = tx("2030-04-22", "CHECK 6214", -86000)
    open_row.vendor = "Check 6214"
    apply([resolved, open_row], [{
        "match": {"contains": "Zelle payment to Dana Synth"},
        "set": {"category": "Travel", "subcategory": "Lodging", "needs_review": False},
    }])
    assert resolved.family_fact and not open_row.family_fact
    ranked = reconcile.unmapped_vendors([resolved, open_row], known=set())
    assert [v for v, _s, _n in ranked] == ["Check 6214"]


def test_a_tags_only_set_is_not_a_family_fact_so_the_row_keeps_ranking_as_unmapped():
    # the dashboard's tag editor writes { tags } and nothing else: it says nothing about the row's category,
    # so the row is still a vendor nobody has explained and must keep appearing in the unmapped ranking
    from etl.enrich import answers
    from etl.quality import reconcile

    tagged, categorized, retyped = (tx("2030-05-06", f"CHECK {n}", -91500) for n in (7101, 7102, 7103))
    for t, v in ((tagged, "Check 7101"), (categorized, "Check 7102"), (retyped, "Check 7103")):
        t.vendor = v
    apply([tagged, categorized, retyped], [
        {"match": {"contains": "CHECK 7101"}, "set": {"tags": ["kids"]}},
        {"match": {"contains": "CHECK 7102"}, "set": {"category": "Housing", "tags": ["home"]}},    # tags AND a category
        {"match": {"contains": "CHECK 7103"}, "set": {"type": "transfer"}},
    ])
    assert tagged.tags == ["kids"] and not tagged.family_fact
    assert categorized.family_fact and retyped.family_fact
    ranked = [v for v, _s, _n in reconcile.unmapped_vendors([tagged, categorized, retyped], known=set())]
    assert ranked == ["Check 7101"]
    # the same through a review-inbox answer (the shared apply_set), txn and vendor scope
    a, b = tx("2030-05-07", "CHECK 7110", -4200), tx("2030-05-07", "CHECK 7111", -4200)
    a.vendor, b.vendor = "Check 7110", "Check 7111"
    answers.apply([a, b], {"answers": [
        {"key": f"txn:{a.uid}", "scope": "txn", "txn_id": a.uid, "set": {"tags": ["trip"]}, "answered_at": "2026-07-16T10:00:00Z"},
        {"key": f"txn:{b.uid}", "scope": "txn", "txn_id": b.uid, "set": {"tags": ["trip"], "needs_review": False},
         "answered_at": "2026-07-16T10:00:00Z"}]})
    assert not a.family_fact and b.family_fact                         # needs_review:false is an answer to the review question


def test_the_demo_overrides_file_is_valid():
    entries = load_demo("overrides.yaml")
    assert entries                                       # the demo plants an override
    for entry in entries:
        assert "match" in entry and "set" in entry
        assert entry["match"], "empty match would hit every transaction"


def test_a_uid_or_amount_override_on_a_split_parent_is_logged_and_a_vendor_override_repaints_the_parts():
    parent = make_txn(desc="SYNTHMART", cents=-30000, vendor="Synth Mart", category="Groceries", account=hh.CHECKING)
    parent.is_parent = True
    kids = [make_txn(desc="SYNTHMART", cents=c, vendor="Synth Mart", category=cat, category_source="split", account=hh.CHECKING,
                     parent_id=parent.uid, split_index=i) for i, (c, cat) in enumerate(((-21000, "Groceries"), (-9000, "Shopping")), start=1)]
    rows = [parent, *kids]
    checks = apply(rows, [{"match": {"uid": parent.uid}, "set": {"category": "Pets"}},
                          {"match": {"amount": -300.0}, "set": {"category": "Pets"}}])
    assert parent.category == "Pets" and [k.category for k in kids] == ["Groceries", "Shopping"]
    logged = [c for c in checks if c.name == "overrides.on_split_parent"]
    assert len(logged) == 1 and logged[0].status == "warn" and logged[0].detail.startswith("2 override hit(s) landed on split parents")
    assert "2 of them set a category the parts do not follow" in logged[0].detail
    assert "a uid or amount match reaches the parent alone" in logged[0].detail and "also repaints the parts" in logged[0].detail
    checks = apply(rows, [{"match": {"vendor": "Synth Mart"}, "set": {"category": "Pets"}}])
    assert [k.category for k in kids] == ["Pets", "Pets"] and all(k.parent_id == parent.uid for k in kids)   # an explicit override overrides a split
    logged = [c for c in checks if c.name == "overrides.on_split_parent"]
    assert len(logged) == 1 and logged[0].detail.startswith("1 override hit(s) landed on split parents")   # the parent took the hit too (display only), the parts did not count
    assert logged[0].status == "info" and "do not follow" not in logged[0].detail                         # ... and the parts followed it: nothing stranded
    assert not any(c.name == "overrides.on_split_parent" for c in apply(kids, [{"match": {"vendor": "Synth Mart"}, "set": {"category": "Pets"}}]))


def split_pair():
    parent = make_txn(desc="SYNTHMART", cents=-30000, vendor="Synth Mart", category="Groceries", account=hh.CHECKING)
    parent.is_parent = True
    kids = [make_txn(desc="SYNTHMART", cents=c, vendor="Synth Mart", category=cat, category_source="split", account=hh.CHECKING,
                     parent_id=parent.uid, split_index=i) for i, (c, cat) in enumerate(((-21000, "Groceries"), (-9000, "Shopping")), start=1)]
    return parent, kids


def parent_check(rows, match, target):
    logged = [c for c in apply(rows, [{"match": match, "set": target}]) if c.name == "overrides.on_split_parent"]
    assert len(logged) == 1
    return logged[0]


@pytest.mark.parametrize("target,status", [
    ({"tags": ["kids"]}, "info"),                              # nothing about the category
    ({"needs_review": True}, "info"),
    ({"category": "Pets"}, "warn"),                            # a uid match reaches the parent alone: display-only, stranded
    ({"subcategory": "Toys"}, "warn"),                         # a subcategory alone strands the same way
    ({"category": "Pets", "subcategory": "Toys"}, "warn"),
    ({"type": "transfer"}, "info"),                            # a retype un-splits the parent (splits.unsplit_retyped)
    ({"type": "transfer", "category": "Internal"}, "info"),    # ... whatever else the set carries
])
def test_a_uid_override_on_a_split_parent_warns_only_when_it_strands_a_category(target, status):
    parent, kids = split_pair()
    logged = parent_check([parent, *kids], {"uid": parent.uid}, target)
    assert logged.status == status, target
    assert ("1 of them set a category the parts do not follow" in logged.detail) == (status == "warn"), target


def test_an_override_whose_match_also_reaches_the_parts_is_info_even_when_it_sets_a_category():
    for match in ({"vendor": "Synth Mart"}, {"contains": "synthmart"}, {"account": hh.CHECKING}, {"date": "2025-06-15"}):
        parent, kids = split_pair()
        logged = parent_check([parent, *kids], match, {"category": "Pets", "subcategory": "Toys"})
        assert logged.status == "info" and "do not follow" not in logged.detail, match
        assert [k.category for k in kids] == ["Pets", "Pets"]                  # the parts did follow


def test_strandedness_is_judged_by_whether_the_entry_hit_the_parts_not_by_the_match_keys():
    # uid + vendor reaches the parent alone (the parts have their own uids) although `vendor` alone would reach the parts
    parent, kids = split_pair()
    logged = parent_check([parent, *kids], {"uid": parent.uid, "vendor": "Synth Mart"}, {"category": "Pets"})
    assert logged.status == "warn" and [k.category for k in kids] == ["Groceries", "Shopping"]
    # judged per parent: an entry that reaches two parents AND their parts strands neither, whatever the set says
    a, a_kids = split_pair()
    b = make_txn(desc="SYNTHMART", cents=-50000, vendor="Synth Mart", category="Groceries", account=hh.CARD_ALEX)
    b.is_parent = True
    b_kids = [make_txn(desc="SYNTHMART", cents=c, vendor="Synth Mart", category="Shopping", category_source="split",
                       account=hh.CARD_ALEX, parent_id=b.uid, split_index=i) for i, c in enumerate((-30000, -20000), start=1)]
    only_a = parent_check([a, *a_kids, b, *b_kids], {"vendor": "Synth Mart", "account": hh.CHECKING}, {"category": "Pets"})
    assert only_a.status == "info" and only_a.detail.startswith("1 override hit(s)")      # A and its parts matched, B did not
    both = parent_check([a, *a_kids, b, *b_kids], {"vendor": "Synth Mart"}, {"category": "Pets"})
    assert both.status == "info" and both.detail.startswith("2 override hit(s)")

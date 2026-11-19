# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Income phases come from data. An income rule may carry an `employer:` label; IncomeIdentifier records it on the row
it types (a transient attribute), and detect_income_phases draws one phase per (earner, employer), from the first to
the last such deposit. Also the payment-app cash-out mapping (transfer_rules.yaml `venmo_patterns.cashout_income`),
which files an unmatched cash-out credit under a configured income subcategory, and its checks at load. Synthetic
rules, rows and config dirs; invented names and employers."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
import yaml
from conftest import isolated_pipeline, make_txn, write_household

from etl import db, exports
from etl.config import Config
from etl.enrich.income import IncomeIdentifier, detect_income_phases
from etl.enrich.transfers import TransferEngine
from etl.models import INCOME_CATEGORY, INCOME_OTHER_SUB, RESERVED_TAXONOMY

CHK, SAV, OTHER = "demo_checking_0000", "demo_savings_0000", "demo_checking_1111"
OWNERS = ("Alex", "Sam", "Joint")
PHASE_KEYS = {"person", "phase", "start", "end", "deposits", "total_cents", "avg_monthly_cents"}

ACME = {"pattern": "^ACME CORP PAYROLL", "subcategory": "Salary - Alex", "earner": "Alex", "employer": "Acme Corp"}
ACME_BONUS = {"pattern": "^ACME CORP BONUS", "subcategory": "Bonus", "earner": "Alex", "employer": "Acme Corp"}
GLOBEX = {"pattern": "^GLOBEX PAYROLL", "subcategory": "Salary - Sam", "earner": "Sam", "employer": "Globex"}
GLOBEX_CONSULTING = {"pattern": "^GLOBEX CONSULTING", "subcategory": "Consulting", "earner": "Alex",
                     "employer": "Globex"}


def typed(rules: list[dict], *deposits: tuple[str, str, int], members: tuple[str, ...] = ()):
    """(date, descriptor, cents) deposits into one checking account, typed by IncomeIdentifier with `rules` and the
    household `members` an earner may be derived as."""
    txns = [make_txn(date=d, account=CHK, desc=desc, cents=cents) for d, desc, cents in deposits]
    IncomeIdentifier({"income_rules": rules}, {}, {CHK}, members=members).apply(txns)
    return txns


def spans(phases: list[dict]) -> list[tuple]:
    return [(p["person"], p["phase"], p["start"], p["end"], p["deposits"]) for p in phases]


# ------------------------------------------------------------------ the employer label on the row

def test_the_matched_rule_s_employer_is_recorded_on_the_row():
    pension = {"pattern": "^SAMPLE PENSION", "subcategory": INCOME_OTHER_SUB, "earner": "Sam"}
    acme, pens, unknown = typed([ACME, pension], ("2030-01-15", "ACME CORP PAYROLL", 300_000),
                                ("2030-01-20", "SAMPLE PENSION", 90_000), ("2030-01-25", "MYSTERY DEPOSIT", 80_000))
    assert (acme.type, acme.subcategory, acme.earner, acme.employer) == ("income", "Salary - Alex", "Alex", "Acme Corp")
    assert (pens.type, pens.earner, pens.employer) == ("income", "Sam", None)       # a rule without the label
    assert (unknown.type, unknown.subcategory, unknown.employer) == ("income", INCOME_OTHER_SUB, None)   # the fallback


@pytest.mark.parametrize("subcategory, earner", [("Salary - Alex", "Alex"), ("Consulting - Robin ", "Robin"),
                                                 ("Salary - Bonus Pool", None), ("Salary - alex", None),
                                                 ("Salary", None)],
                         ids=["member", "member-trimmed", "no-member", "case-differs", "no-dash"])
def test_an_earner_is_derived_from_the_subcategory_only_when_it_names_a_member(subcategory, earner):
    rule = {"pattern": "^SAMPLE PAYROLL", "subcategory": subcategory}
    t = make_txn(date="2030-01-15", account=CHK, desc="SAMPLE PAYROLL 0001", cents=300_000)
    IncomeIdentifier({"income_rules": [rule]}, {}, {CHK}, members=("Alex", "Sam", "Robin")).apply([t])
    assert (t.type, t.subcategory, t.earner) == ("income", subcategory, earner)


def test_without_members_no_earner_is_derived_and_a_rule_s_own_earner_still_wins():
    rows = typed([{"pattern": "^SAMPLE PAYROLL", "subcategory": "Salary - Alex"},
                  {"pattern": "^SAMPLE BONUS", "subcategory": "Salary - Alex", "earner": "Sam"}],
                 ("2030-01-15", "SAMPLE PAYROLL 0001", 300_000), ("2030-01-16", "SAMPLE BONUS 0001", 10_000))
    assert [t.earner for t in rows] == [None, "Sam"]


def test_the_pipeline_derives_earners_from_the_household_s_members(tmp_path):
    d = tmp_path / "cfg"
    write_household(d, adults=("Alex",), children=("Robin",))
    (d / "accounts.yaml").write_text(yaml.safe_dump({"accounts": {CHK: {
        "name": "Sample Checking", "institution": "Sample Bank", "owner": "Joint", "kind": "checking"}}}), "utf-8")
    (d / "category_rules.yaml").write_text(yaml.safe_dump({
        "taxonomy": {**{c: list(s) for c, s in RESERVED_TAXONOMY.items()},
                     INCOME_CATEGORY: [INCOME_OTHER_SUB, "Salary - Robin", "Salary - Bonus Pool"]},
        "income_rules": [{"pattern": "^SAMPLE PAYROLL", "subcategory": "Salary - Robin"},
                         {"pattern": "^SAMPLE BONUS", "subcategory": "Salary - Bonus Pool"}]}), "utf-8")
    rows = [make_txn(date="2030-01-15", account=CHK, desc="SAMPLE PAYROLL 0001", cents=300_000),
            make_txn(date="2030-01-16", account=CHK, desc="SAMPLE BONUS 0001", cents=300_000)]
    p = isolated_pipeline(tmp_path / "state", rows, cfg=Config(d), windows={CHK: ("2030-01-01", "2030-01-31")})
    p.enrich()
    assert [t.earner for t in rows] == ["Robin", None]


def test_the_employer_is_transient_it_reaches_no_uid_export_row_db_row_or_equality(tmp_path):
    (t,) = typed([ACME], ("2030-01-15", "ACME CORP PAYROLL", 300_000))
    twin = dataclasses.replace(t, employer=None)
    assert t.employer == "Acme Corp"
    assert t == twin and t.uid == twin.uid                                        # compare=False: not identity
    assert "employer" not in repr(t)
    owner, name = {CHK: "Joint"}, {CHK: "Sample Checking"}
    row = exports.export_transactions([t], owner, name)["transactions"][0]
    assert row == exports.export_transactions([twin], owner, name)["transactions"][0] and "employer" not in row
    conn = db.build(tmp_path / "finance.db")
    conn.execute("INSERT INTO accounts (account_id, name, institution, owner, kind) "
                 f"VALUES ('{CHK}', 'Sample Checking', 'Sample Bank', 'Joint', 'checking')")
    db.insert_txns(conn, [t], owner)
    assert "employer" not in {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}


# ------------------------------------------------------------------ detect_income_phases

def test_one_phase_per_earner_and_employer():
    sam_at_acme = {**ACME, "pattern": "^ACME CORP SAM", "subcategory": "Salary - Sam", "earner": "Sam"}
    txns = typed([ACME, GLOBEX, sam_at_acme], ("2030-01-15", "ACME CORP PAYROLL", 300_000),
                 ("2030-02-15", "GLOBEX PAYROLL 1", 200_000), ("2030-03-15", "ACME CORP PAYROLL", 300_000),
                 ("2030-04-15", "ACME CORP SAM", 100_000))
    phases = detect_income_phases(txns, OWNERS)
    # the same employer label for two people is two phases
    assert spans(phases) == [("Alex", "Acme Corp", "2030-01-15", "2030-03-15", 2),
                             ("Sam", "Globex", "2030-02-15", "2030-02-15", 1),
                             ("Sam", "Acme Corp", "2030-04-15", "2030-04-15", 1)]
    assert all(set(p) == PHASE_KEYS for p in phases)


def test_two_rules_that_share_an_employer_label_make_one_phase():
    txns = typed([ACME, ACME_BONUS], ("2030-01-15", "ACME CORP PAYROLL", 300_000),
                 ("2030-06-30", "ACME CORP BONUS", 50_000), ("2030-03-15", "ACME CORP PAYROLL", 300_000))
    (phase,) = detect_income_phases(txns, OWNERS)
    assert spans([phase]) == [("Alex", "Acme Corp", "2030-01-15", "2030-06-30", 3)]
    assert phase["total_cents"] == 300_000 + 50_000 + 300_000


def test_two_employers_at_once_give_one_person_two_overlapping_phases():
    txns = typed([ACME, GLOBEX_CONSULTING], ("2030-01-15", "ACME CORP PAYROLL", 300_000),
                 ("2030-03-10", "GLOBEX CONSULTING", 40_000), ("2030-04-10", "GLOBEX CONSULTING", 40_000),
                 ("2030-06-15", "ACME CORP PAYROLL", 300_000))
    acme, globex = detect_income_phases(txns, OWNERS)
    assert spans([acme, globex]) == [("Alex", "Acme Corp", "2030-01-15", "2030-06-15", 2),
                                     ("Alex", "Globex", "2030-03-10", "2030-04-10", 2)]
    assert acme["start"] < globex["start"] <= globex["end"] < acme["end"]


def test_a_rule_without_an_employer_label_gives_no_phase():
    unlabelled = {k: v for k, v in ACME.items() if k != "employer"}
    txns = typed([unlabelled], ("2030-01-15", "ACME CORP PAYROLL", 300_000), ("2030-02-15", "ACME CORP PAYROLL", 300_000))
    assert [(t.type, t.earner) for t in txns] == [("income", "Alex")] * 2
    assert detect_income_phases(txns, OWNERS) == []


def test_a_matched_row_without_an_earner_gives_no_phase():
    # no earner: key and a subcategory with no " - person" suffix: the row has an employer but nobody earned it
    no_earner = {"pattern": "^INITECH PAYROLL", "subcategory": "Salary", "employer": "Initech"}
    txns = typed([no_earner], ("2030-01-15", "INITECH PAYROLL", 300_000))
    assert (txns[0].earner, txns[0].employer) == (None, "Initech")
    assert detect_income_phases(txns, OWNERS) == []


def test_a_row_the_label_typed_but_a_later_stage_moved_out_of_income_gives_no_phase():
    txns = typed([ACME], ("2030-01-15", "ACME CORP PAYROLL", 300_000), ("2030-02-15", "ACME CORP PAYROLL", 300_000))
    txns[1].type, txns[1].category = "transfer", "Internal"         # e.g. an override retyped it
    assert spans(detect_income_phases(txns, OWNERS)) == [("Alex", "Acme Corp", "2030-01-15", "2030-01-15", 1)]


def test_a_split_parent_and_its_parts_count_as_no_deposit():
    labelled = dict(type="income", category=INCOME_CATEGORY, earner="Alex", employer="Acme Corp")
    parent = make_txn(date="2030-01-15", account=CHK, desc="ACME CORP PAYROLL", cents=300_000, is_parent=True, **labelled)
    parts = [make_txn(date="2030-01-15", account=CHK, desc="ACME CORP PAYROLL", cents=cents, parent_id=parent.uid,
                      split_index=i, **labelled) for i, cents in enumerate((200_000, 100_000), start=1)]
    assert detect_income_phases([parent, *parts], OWNERS) == []


def test_deposits_total_and_monthly_average_over_a_three_month_span():
    txns = typed([ACME], ("2030-01-31", "ACME CORP PAYROLL", 250_000), ("2030-02-15", "ACME CORP PAYROLL", 250_000),
                 ("2030-03-01", "ACME CORP PAYROLL", 250_001))
    (p,) = detect_income_phases(txns, OWNERS)
    assert (p["start"], p["end"], p["deposits"]) == ("2030-01-31", "2030-03-01", 3)
    assert p["total_cents"] == 250_000 + 250_000 + 250_001
    # January to March: three calendar months, the first and the last both counted; whole cents, rounded down
    assert p["avg_monthly_cents"] == (250_000 + 250_000 + 250_001) // 3
    # across a year end: December to February is three months too
    txns = typed([ACME], ("2030-12-20", "ACME CORP PAYROLL", 100_000), ("2031-02-05", "ACME CORP PAYROLL", 200_000))
    (p,) = detect_income_phases(txns, OWNERS)
    assert (p["total_cents"], p["avg_monthly_cents"]) == (100_000 + 200_000, (100_000 + 200_000) // 3)
    # one deposit: a one-month span
    (p,) = detect_income_phases(typed([ACME], ("2030-05-15", "ACME CORP PAYROLL", 123_457)), OWNERS)
    assert p["avg_monthly_cents"] == 123_457


def test_phases_are_ordered_by_owner_order_then_start_then_employer():
    rules = [ACME, GLOBEX_CONSULTING, GLOBEX,
             # the earner from the subcategory's " - person" suffix
             {"pattern": "^INITECH PAYROLL", "subcategory": "Consulting - Sam", "employer": "Initech"},
             # earners the owner list does not hold come last, by name
             {"pattern": "^HOOLI ROBIN", "subcategory": "Salary - Robin", "employer": "Hooli"},
             {"pattern": "^HOOLI CASEY", "subcategory": "Salary - Casey", "employer": "Hooli"}]
    txns = typed(rules, ("2030-01-01", "HOOLI ROBIN", 1_000), ("2030-01-01", "HOOLI CASEY", 1_000),
                 ("2030-01-15", "GLOBEX CONSULTING", 1_000), ("2030-01-15", "ACME CORP PAYROLL", 1_000),
                 ("2030-03-15", "GLOBEX PAYROLL", 1_000), ("2030-02-15", "INITECH PAYROLL", 1_000),
                 members=("Alex", "Sam", "Robin", "Casey"))
    phases = detect_income_phases(txns, ("Sam", "Alex", "Joint"))
    assert [(p["person"], p["phase"]) for p in phases] == [
        ("Sam", "Initech"), ("Sam", "Globex"),                  # Sam is the first owner; Initech starts first
        ("Alex", "Acme Corp"), ("Alex", "Globex"),              # one start date: by employer
        ("Casey", "Hooli"), ("Robin", "Hooli")]
    assert detect_income_phases([], OWNERS) == []


# ------------------------------------------------------------------ the cash-out mapping

def _engine(cashout: list[dict] | None):
    vp = {"debit": "VENMO PAYMENT", "credit": "CASHOUT", "zelle": "ZELLE"}
    if cashout is not None:
        vp["cashout_income"] = cashout
    window = ("2030-01-01", "2030-12-31")
    return TransferEngine([], {"rules": [], "venmo_patterns": vp}, {}, {a: window for a in (CHK, SAV, OTHER)},
                          {CHK, SAV, OTHER})


def cashed_out(cashout: list[dict] | None, account: str, **fields):
    """an unmatched cash-out credit on `account`, through phases A to C"""
    t = make_txn(desc="VENMO CASHOUT 1234", cents=50_000, account=account, date="2030-03-02", **fields)
    eng = _engine(cashout)
    res = eng.phase_a([t])
    eng.phase_b([t], res)
    eng.phase_c([t], res)
    return t


def test_a_cash_out_on_a_listed_account_takes_the_configured_subcategory():
    mapping = [{"accounts": [CHK, SAV], "subcategory": "Consulting - Sam"}]
    for account in (CHK, SAV):
        t = cashed_out(mapping, account)
        assert (t.type, t.category, t.subcategory, t.category_source) == \
            ("income", INCOME_CATEGORY, "Consulting - Sam", "type_default")
        assert t.needs_review and t.earner is None            # no earner: in the entry, none is set
    # an earner a row already carries is left alone by an entry without one
    assert cashed_out(mapping, CHK, earner="Alex").earner == "Alex"


def test_a_cash_out_entry_with_an_earner_sets_it():
    t = cashed_out([{"accounts": [CHK], "subcategory": "Consulting - Sam", "earner": "Sam"}], CHK)
    assert (t.subcategory, t.earner) == ("Consulting - Sam", "Sam")


def test_a_cash_out_elsewhere_takes_the_income_catch_all():
    mapping = [{"accounts": [CHK], "subcategory": "Consulting - Sam", "earner": "Sam"}]
    for cashout in (mapping, [], None):
        t = cashed_out(cashout, OTHER)
        assert (t.type, t.category, t.subcategory, t.earner) == ("income", INCOME_CATEGORY, INCOME_OTHER_SUB, None)


def test_the_first_entry_listing_the_account_decides():
    mapping = [{"accounts": [SAV], "subcategory": "Bonus"},
               {"accounts": [CHK], "subcategory": "Consulting - Sam"},
               {"accounts": [CHK, SAV], "subcategory": "Salary - Alex", "earner": "Alex"}]
    assert (cashed_out(mapping, CHK).subcategory, cashed_out(mapping, SAV).subcategory) == ("Consulting - Sam", "Bonus")


def test_a_cash_out_carries_no_employer():
    # a row an income rule labelled and the cash-out path then filed is the cash-out path's: it starts no phase
    t = cashed_out([{"accounts": [CHK], "subcategory": "Consulting - Sam", "earner": "Sam"}], CHK,
                   type="income", employer="Acme Corp")
    assert (t.subcategory, t.employer) == ("Consulting - Sam", None)


# ------------------------------------------------------------------ the config: load checks and the pipeline

TAXONOMY = {**{c: list(subs) for c, subs in RESERVED_TAXONOMY.items()},
            INCOME_CATEGORY: [*RESERVED_TAXONOMY[INCOME_CATEGORY], "Salary - Alex", "Salary - Sam", "Consulting - Sam",
                              "Bonus"]}
_ACCOUNT = {"name": "Sample Checking", "institution": "Sample Bank", "owner": "Joint", "kind": "checking"}


def _config_dir(tmp_path: Path, cashout=None, *, adults=("Alex", "Sam")) -> Path:
    d = tmp_path / "cfg"
    accounts = {CHK: dict(_ACCOUNT), SAV: {**_ACCOUNT, "name": "Sample Savings", "kind": "savings"}}
    vp = {"debit": r"VENMO\s+PAYMENT", "credit": r"VENMO\s+CASHOUT", "zelle": "ZELLE"}
    if cashout is not None:
        vp["cashout_income"] = cashout
    docs = {"accounts.yaml": {"accounts": accounts},
            "category_rules.yaml": {"taxonomy": TAXONOMY, "income_rules": [ACME, GLOBEX]},
            "transfer_rules.yaml": {"rules": [], "venmo_patterns": vp}}
    write_household(d, adults=adults)
    for name, doc in docs.items():
        (d / name).write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")
    return d


def test_a_valid_cash_out_mapping_loads(tmp_path):
    mapping = [{"accounts": [CHK, SAV], "subcategory": "Consulting - Sam", "earner": "Sam"}]
    assert Config(_config_dir(tmp_path, mapping)).transfer_rules["venmo_patterns"]["cashout_income"] == mapping


@pytest.mark.parametrize("mapping, key, problem", [
    ([{"accounts": [CHK, "demo_missing_9999"], "subcategory": "Consulting - Sam"}], "", "'demo_missing_9999'"),
    ([{"accounts": [CHK], "subcategory": "Consulting - Robin"}], "", "'Consulting - Robin'"),
    ([{"accounts": [CHK], "subcategory": "Uncategorized"}], "", "'Uncategorized'"),       # a name outside Income
    ([{"accounts": [CHK], "subcategory": "Consulting - Sam", "earner": "Robin"}], r"\.earner", "'Robin'"),
    ([{"accounts": [], "subcategory": "Consulting - Sam"}], "", "accounts"),
    ([{"subcategory": "Consulting - Sam"}], "", "accounts"),
    ([{"accounts": [CHK]}], "", "subcategory"),
    (["Consulting - Sam"], "", "mapping"),
])
def test_a_cash_out_mapping_naming_what_the_config_lacks_is_refused_at_load(tmp_path, mapping, key, problem):
    with pytest.raises(ValueError, match=rf"^transfer_rules\.yaml: venmo_patterns\.cashout_income\[0\]{key}: ") as e:
        Config(_config_dir(tmp_path, mapping))
    assert problem in str(e.value)


def test_a_cash_out_mapping_that_is_not_a_list_is_refused_at_load(tmp_path):
    with pytest.raises(ValueError, match=r"^transfer_rules\.yaml: venmo_patterns\.cashout_income: expected a list"):
        Config(_config_dir(tmp_path, {"accounts": [CHK], "subcategory": "Consulting - Sam"}))


def test_the_pipeline_exports_the_phases_in_the_household_s_owner_order(tmp_path):
    # Sam is the household's first adult; Alex's phase starts earlier and sorts first by name, and still comes second
    cfg = Config(_config_dir(tmp_path, [{"accounts": [CHK], "subcategory": "Consulting - Sam", "earner": "Sam"}],
                             adults=("Sam", "Alex")))
    rows = [make_txn(date="2030-01-15", account=CHK, desc="ACME CORP PAYROLL", cents=300_000),
            make_txn(date="2030-03-15", account=CHK, desc="ACME CORP PAYROLL", cents=300_000),
            make_txn(date="2030-02-15", account=SAV, desc="GLOBEX PAYROLL", cents=200_000),
            # filed by the cash-out mapping, with an earner: still no phase, since no income rule labelled it
            make_txn(date="2030-02-20", account=CHK, desc="VENMO CASHOUT 1234", cents=20_000)]
    window = ("2030-01-01", "2030-12-31")
    p = isolated_pipeline(tmp_path / "state", rows, cfg=cfg, windows={CHK: window, SAV: window})
    p.enrich()
    assert (rows[3].subcategory, rows[3].earner, rows[3].employer) == ("Consulting - Sam", "Sam", None)
    out = tmp_path / "exports"
    out.mkdir()
    p._export_assumptions(out)
    phases = json.loads((out / "assumptions.json").read_text("utf-8"))["income_phases"]
    assert [{k: p[k] for k in ("person", "phase", "start", "end", "deposits", "total", "avg_monthly")}
            for p in phases] == [
        {"person": "Sam", "phase": "Globex", "start": "2030-02-15", "end": "2030-02-15", "deposits": 1,
         "total": 2_000.0, "avg_monthly": 2_000.0},
        {"person": "Alex", "phase": "Acme Corp", "start": "2030-01-15", "end": "2030-03-15", "deposits": 2,
         "total": 6_000.0, "avg_monthly": 2_000.0}]

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The three statement-file writers, each read back through the engine's own parser."""
from __future__ import annotations

from datetime import date

import pytest

from etl.importers import bofa_bank, chase_card, chase_checking
from etl.quality.reconcile import statement_summary_check
from tools.demo.ledger import Ledger, Row
from tools.demo.writers import bofa_bank as write_bofa
from tools.demo.writers import chase_card as write_card
from tools.demo.writers import chase_checking as write_checking

CHECKING = "demo_checking_0101"
CARD = "demo_card_0102"
SAVINGS = "demo_savings_0103"


def D(day: int, month: int = 1) -> date:
    return date(2030, month, day)


@pytest.fixture
def led() -> Ledger:
    led = Ledger(D(1), D(31))
    led.open(CHECKING, "checking", 500_000)
    led.open(CARD, "credit-card", -20_000)
    led.open(SAVINGS, "savings", 100_000)
    led.post(Row(CHECKING, D(2), 250_000, "PAYROLL ACME CORP"))
    led.post(Row(CHECKING, D(3), -150_000, "MONTHLY RENT", bank_type="ACH_DEBIT"))
    led.post(Row(CHECKING, D(5), -8_500, "GROCERY STORE", bank_type="DEBIT_CARD"))
    led.move(Row(CHECKING, D(15), -100_000, "TRANSFER TO SAVINGS"), Row(SAVINGS, D(16), 100_000, "TRANSFER FROM CHECKING"))
    led.post(Row(SAVINGS, D(20), 1_234, "INTEREST PAID"))
    led.post(Row(SAVINGS, D(25), -123_456, "WITHDRAWAL"))
    led.post(Row(CARD, D(8), -4_599, "ACME STORE", bank_category="Shopping"))
    led.post(Row(CARD, D(9), -1_234, "ACME GAS", bank_category="Gas", bank_type="Sale"))
    led.post(Row(CARD, D(10), 999, "ACME STORE", bank_type="Return"))
    led.move(Row(CHECKING, D(12), -20_000, "CARD PAYMENT"), Row(CARD, D(13), 20_000, "PAYMENT THANK YOU"))
    led.post(Row(CHECKING, D(30), -500, "COFFEE SHOP", bank_type="DEBIT_CARD"))
    led.post(Row(CHECKING, D(30), -2_000, "PHARMACY", bank_type="DEBIT_CARD"))        # the later posting of the day
    led.post(Row(CHECKING, D(31), -999, "PENDING SHOP", pending=True))
    led.check()
    return led


def checking_type(r: Row) -> str:
    return r.bank_type or ("ACH_CREDIT" if r.cents > 0 else "ACH_DEBIT")


def card_type(r: Row) -> str:
    return r.bank_type or ("Return" if r.cents > 0 else "Sale")


LAYOUTS = [
    pytest.param(write_checking, chase_checking, CHECKING, checking_type, id="chase_checking"),
    pytest.param(write_card, chase_card, CARD, card_type, id="chase_card"),
    pytest.param(write_bofa, bofa_bank, SAVINGS, lambda r: None, id="bofa_bank"),
]


@pytest.mark.parametrize(("writer", "parser", "account", "kind"), LAYOUTS)
def test_each_layout_round_trips_dates_cents_and_descriptions(tmp_path, led, writer, parser, account, kind):
    path = tmp_path / "out" / "file.csv"
    first, last = D(1), D(31)
    led.post(Row(account, D(21), -777, 'ACME, "THE" COFFEE CO'))
    n = writer.write(path, account, led, first, last)
    txns, stats, _ = parser.parse(path, account)
    want =[r for r in led.rows(account, first, last) if not r.pending]
    assert n == len(want) == len(txns) == stats.rows_parsed
    assert sorted((t.date, t.amount_cents, t.description_raw) for t in txns) == sorted(
        (r.date.isoformat(), r.cents, r.desc) for r in want)
    assert sorted((t.date, t.amount_cents, t.bank_type) for t in txns) == sorted(
        (r.date.isoformat(), r.cents, kind(r)) for r in want)
    text = path.read_text("utf-8")
    assert "PENDING SHOP" not in text and "\r" not in text and str(tmp_path) not in text


@pytest.mark.parametrize(("writer", "parser", "account", "kind"), LAYOUTS)
def test_listed_is_the_rows_of_the_file_in_its_order(tmp_path, led, writer, parser, account, kind):
    path = tmp_path / "file.csv"
    writer.write(path, account, led, D(2), D(30))
    txns = parser.parse(path, account)[0]
    assert [(r.date.isoformat(), r.cents, r.desc) for r in writer.listed(led, account, D(2), D(30))] == [
        (t.date, t.amount_cents, t.description_raw) for t in txns]


def test_checking_latest_balance_is_the_ledger_end_of_day_balance(tmp_path, led):
    path = tmp_path / "checking.csv"
    write_checking.write(path, CHECKING, led, D(1), D(31))
    lines = path.read_text("utf-8").splitlines()
    assert lines[0] == "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #"
    assert lines[1] == "DEBIT,01/30/2030,PHARMACY,-20.00,DEBIT_CARD,%s," % _plain(led.balance(CHECKING, D(30)))
    assert lines[2].startswith("DEBIT,01/30/2030,COFFEE SHOP,-5.00,")            # the earlier posting follows it
    assert lines[-1] == "CREDIT,01/02/2030,PAYROLL ACME CORP,2500.00,ACH_CREDIT,%s," % _plain(
        led.balance(CHECKING, D(2)))
    _, _, extra = chase_checking.parse(path, CHECKING)
    assert extra["latest_balance"] == ("2030-01-30", led.balance(CHECKING, D(30)))
    # a file that stops earlier carries the balance as of its own last day
    early = tmp_path / "early.csv"
    write_checking.write(early, CHECKING, led, D(1), D(14))
    assert chase_checking.parse(early, CHECKING)[2]["latest_balance"] == ("2030-01-12", led.balance(CHECKING, D(12)))


def _plain(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}{abs(cents) // 100}.{abs(cents) % 100:02d}"


def test_card_file_carries_the_bank_category_and_no_balance(tmp_path, led):
    path = tmp_path / "card.csv"
    write_card.write(path, CARD, led, D(1), D(31))
    lines = path.read_text("utf-8").splitlines()
    assert lines[0] == "Transaction Date,Post Date,Description,Category,Type,Amount,Memo"
    assert lines[1] == "01/08/2030,01/08/2030,ACME STORE,Shopping,Sale,-45.99,"
    txns, _, extra = chase_card.parse(path, CARD)
    assert extra == {}
    assert [(t.date, t.bank_category, t.bank_type) for t in txns] == [
        ("2030-01-08", "Shopping", "Sale"), ("2030-01-09", "Gas", "Sale"),
        ("2030-01-10", None, "Return"), ("2030-01-13", None, "Return")]


def test_savings_summary_passes_the_three_statement_checks(tmp_path, led):
    path = tmp_path / "savings.csv"
    write_bofa.write(path, SAVINGS, led, D(1), D(31))
    text = path.read_text("utf-8")
    assert text.startswith('Description,,Summary Amt.\nBeginning balance as of 01/01/2030,,"1,000.00"\n')
    assert '"-1,234.56"' in text
    txns, _, extra = bofa_bank.parse(path, SAVINGS)
    checks = statement_summary_check(SAVINGS, extra["summary"], txns)
    assert [c.status for c in checks] == ["pass", "pass", "pass"]
    assert extra["summary"]["beginning_balance"] == 100_000
    assert extra["summary"]["ending_balance"] == led.balance(SAVINGS, D(31))
    assert extra["latest_balance"] == ("2030-01-31", led.balance(SAVINGS, D(31)))


def test_an_overlapping_savings_export_has_its_own_true_summary(tmp_path, led):
    wide, narrow = tmp_path / "wide.csv", tmp_path / "narrow.csv"
    write_bofa.write(wide, SAVINGS, led, D(1), D(20))
    write_bofa.write(narrow, SAVINGS, led, D(20), D(31))
    for path, beginning in ((wide, 100_000), (narrow, 200_000)):
        txns, _, extra = bofa_bank.parse(path, SAVINGS)
        assert [c.status for c in statement_summary_check(SAVINGS, extra["summary"], txns)] == ["pass"] * 3
        assert extra["summary"]["beginning_balance"] == beginning == led.balance(SAVINGS, path_day(path))
    narrow_summary = bofa_bank.parse(narrow, SAVINGS)[2]["summary"]
    assert narrow_summary["beginning_balance_date"] == "2030-01-20"
    assert narrow_summary["total_credits"] == 1_234 and narrow_summary["total_debits"] == -123_456


def path_day(path) -> date:
    """The day before the file's first day: the 1st of January 2030 is no day before, so the opening stands."""
    return D(19) if path.name == "narrow.csv" else D(1)


@pytest.mark.parametrize(("writer", "parser", "account"), [
    pytest.param(write_checking, chase_checking, CHECKING, id="chase_checking"),
    pytest.param(write_card, chase_card, CARD, id="chase_card"),
    pytest.param(write_bofa, bofa_bank, SAVINGS, id="bofa_bank"),
])
def test_two_identical_rows_on_one_day_are_two_rows_in_a_file(tmp_path, led, writer, parser, account):
    led.post(Row(account, D(22), -350, "ACME COFFEE"))
    led.post(Row(account, D(22), -350, "ACME COFFEE"))
    path = tmp_path / "twice.csv"
    assert writer.write(path, account, led, D(22), D(22)) == 2
    txns, _, _ = parser.parse(path, account)
    assert [(t.date, t.amount_cents, t.description_raw) for t in txns] == [("2030-01-22", -350, "ACME COFFEE")] * 2
    assert len({t.uid for t in _with_seq(txns)}) == 2


def _with_seq(txns):
    from etl.pipeline import assign_occurrence_seq
    assign_occurrence_seq(txns)
    return txns


@pytest.mark.parametrize(("writer", "parser", "account", "first", "last"), [
    pytest.param(write_checking, chase_checking, CHECKING, D(6), D(8), id="chase_checking"),
    pytest.param(write_card, chase_card, CARD, D(1), D(5), id="chase_card"),
    pytest.param(write_bofa, bofa_bank, SAVINGS, D(2), D(3), id="bofa_bank"),
])
def test_an_empty_range_writes_a_file_the_parser_reads_as_no_rows(tmp_path, led, writer, parser, account, first, last):
    path = tmp_path / "empty.csv"
    assert writer.write(path, account, led, first, last) == 0
    txns, stats, extra = parser.parse(path, account)
    assert txns == [] and stats.rows_parsed == 0
    if parser is bofa_bank:
        summary = extra["summary"]
        assert summary["beginning_balance"] == summary["ending_balance"] == 100_000
        assert summary["total_credits"] == summary["total_debits"] == 0


def test_checking_first_row_of_the_newest_day_carries_an_adjustment_made_that_day(tmp_path, led):
    led.adjust(CHECKING, D(30), 12_345)                                      # moves the balance, prints no row
    path = tmp_path / "adjusted.csv"
    write_checking.write(path, CHECKING, led, D(1), D(31))
    first = path.read_text("utf-8").splitlines()[1]
    assert first.startswith("DEBIT,01/30/2030,PHARMACY,")
    assert first.split(",")[5] == _plain(led.balance(CHECKING, D(30)))
    assert chase_checking.parse(path, CHECKING)[2]["latest_balance"] == ("2030-01-30", led.balance(CHECKING, D(30)))


def test_a_type_or_category_holding_a_comma_does_not_shift_columns(tmp_path, led):
    led.post(Row(CARD, D(20), -1_500, "ACME DINER", bank_category="Food, Drink", bank_type="Sale, Special"))
    path = tmp_path / "card.csv"
    write_card.write(path, CARD, led, D(1), D(31))
    txns, _, _ = chase_card.parse(path, CARD)
    (t,) = [t for t in txns if t.description_raw == "ACME DINER"]
    assert (t.bank_category, t.amount_cents) == ("Food, Drink", -1_500)
    led.post(Row(CHECKING, D(20), -1_500, "ACME DINER", bank_type="DEBIT, CARD"))
    out = tmp_path / "checking.csv"
    write_checking.write(out, CHECKING, led, D(1), D(31))
    (t,) = [t for t in chase_checking.parse(out, CHECKING)[0] if t.description_raw == "ACME DINER"]
    assert (t.bank_type, t.amount_cents) == ("DEBIT, CARD", -1_500)


@pytest.mark.parametrize(("writer", "account"), [
    pytest.param(write_checking, CHECKING, id="chase_checking"),
    pytest.param(write_card, CARD, id="chase_card"),
    pytest.param(write_bofa, SAVINGS, id="bofa_bank"),
])
def test_a_writer_refuses_a_range_that_ends_before_it_starts(tmp_path, led, writer, account):
    with pytest.raises(ValueError, match="before"):
        writer.write(tmp_path / "backwards.csv", account, led, D(10), D(9))
    assert not (tmp_path / "backwards.csv").exists()


def test_savings_refuses_an_adjustment_inside_the_written_range(tmp_path, led):
    led.adjust(SAVINGS, D(10), 500)
    with pytest.raises(ValueError, match="adjustment"):
        write_bofa.write(tmp_path / "s.csv", SAVINGS, led, D(1), D(31))
    write_bofa.write(tmp_path / "ok.csv", SAVINGS, led, D(11), D(31))        # outside the range it is fine

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The demo household's ledger and random streams: invented accounts only, pure standard library."""
from __future__ import annotations

from datetime import date

import pytest

from tools.demo import streams
from tools.demo.ledger import Expect, Ledger, Row, half_up

CHECKING = "demo_checking_0101"
SAVINGS = "demo_savings_0102"


def D(day: int, month: int = 1) -> date:
    return date(2030, month, day)


def ledger(opened: date | None = None) -> Ledger:
    led = Ledger(D(1), D(31))
    led.open(CHECKING, "checking", 100_000, opened)
    led.open(SAVINGS, "savings", 50_000)
    return led


def row(account: str, day: int, cents: int, desc: str = "ACME COFFEE", **kw) -> Row:
    return Row(account, D(day), cents, desc, **kw)


def test_a_move_must_balance():
    led = ledger()
    out, into = led.move(row(CHECKING, 5, -20_000, "TRANSFER TO SAVINGS"),
                         row(SAVINGS, 6, 20_000, "TRANSFER FROM CHECKING"))
    assert (out.cents, into.cents) == (-20_000, 20_000)
    assert led.rows(SAVINGS)[0].date == D(6)                     # a transfer may land a day later
    with pytest.raises(ValueError, match="opposite"):
        led.move(row(CHECKING, 7, -20_000), row(SAVINGS, 7, 19_999))
    with pytest.raises(ValueError, match="negative"):
        led.move(row(CHECKING, 7, 20_000), row(SAVINGS, 7, -20_000))
    with pytest.raises(ValueError, match="differ"):
        led.move(row(CHECKING, 7, -20_000), row(CHECKING, 7, 20_000))
    assert len(led.rows(CHECKING)) == 1 and len(led.rows(SAVINGS)) == 1   # a refused move posts neither leg
    led.check()


def test_rows_are_refused_when_they_do_not_belong_in_the_ledger():
    led = ledger(opened=D(10))
    with pytest.raises(ValueError, match="never opened"):
        led.post(row("demo_checking_0999", 12, -100))
    with pytest.raises(ValueError, match="zero"):
        led.post(row(CHECKING, 12, 0))
    with pytest.raises(ValueError, match="opened"):
        led.post(row(CHECKING, 9, -100))                          # before the account existed
    with pytest.raises(ValueError, match="range"):
        led.post(Row(CHECKING, date(2029, 12, 31), -100, "ACME COFFEE"))
    led.post(Row(CHECKING, D(7, 2), -100, "ACME COFFEE"))        # up to seven days past the end
    with pytest.raises(ValueError, match="range"):
        led.post(Row(CHECKING, D(8, 2), -100, "ACME COFFEE"))
    with pytest.raises(ValueError, match="never opened"):
        led.move(row(CHECKING, 12, -100), row("demo_checking_0999", 12, 100))
    with pytest.raises(ValueError, match="zero"):
        led.move(row(CHECKING, 12, 0), row(SAVINGS, 12, 0))
    with pytest.raises(ValueError, match="already"):
        led.open(CHECKING, "checking", 0)


def test_balance_is_opening_plus_rows_and_adjustments_at_end_of_day():
    led = ledger()
    led.post(row(CHECKING, 5, -200_000))
    led.post(row(CHECKING, 5, 50_000, "PAYROLL ACME CORP"))
    led.adjust(CHECKING, D(10), 300)
    assert led.balance(CHECKING, D(1)) == 100_000
    assert led.balance(CHECKING, D(4)) == 100_000
    assert led.balance(CHECKING, D(5)) == 100_000 - 200_000 + 50_000      # both rows of the day count
    assert led.balance(CHECKING, D(9)) == -50_000
    assert led.balance(CHECKING, D(10)) == -50_000 + 300
    assert led.balance(CHECKING, D(31)) == -49_700
    assert led.balance(CHECKING, D(1, 2)) == -49_700
    assert led.balance(SAVINGS, D(31)) == 50_000                  # another account is untouched


def test_pending_rows_count_only_when_asked():
    led = ledger()
    led.post(row(CHECKING, 6, -700, pending=True))
    led.post(row(CHECKING, 6, -300))
    assert led.balance(CHECKING, D(6)) == 100_000 - 300
    assert led.balance(CHECKING, D(6), with_pending=True) == 100_000 - 1_000
    assert led.balance(CHECKING, D(5), with_pending=True) == 100_000
    assert [r.pending for r in led.rows(CHECKING)] == [True, False]       # rows() lists both
    led.check()


def test_rows_keep_posting_order_within_a_day():
    led = ledger()
    led.post(row(CHECKING, 9, -100, "LATE DAY"))
    led.post(row(CHECKING, 3, -200, "FIRST ONE"))
    led.post(row(CHECKING, 3, -300, "SECOND ONE"))
    led.post(row(CHECKING, 3, -400, "THIRD ONE"))
    assert [r.desc for r in led.rows(CHECKING)] == ["FIRST ONE", "SECOND ONE", "THIRD ONE", "LATE DAY"]
    assert [r.desc for r in led.rows(CHECKING, D(3), D(3))] == ["FIRST ONE", "SECOND ONE", "THIRD ONE"]
    assert [r.desc for r in led.rows(CHECKING, D(4), D(9))] == ["LATE DAY"]        # both bounds are inclusive
    assert [r.desc for r in led.rows(CHECKING, last=D(3))] == ["FIRST ONE", "SECOND ONE", "THIRD ONE"]


def test_a_stream_depends_on_profile_and_name_only():
    def draws(profile: str, name: str, between: bool = False) -> list[float]:
        s = streams.stream(profile, name)
        out = [s.random()]
        if between:
            streams.stream("demo", "groceries").random()          # a stream made and used in between
        return out + [s.random() for _ in range(2)]

    first = draws("demo", "payroll")
    assert draws("demo", "payroll") == first
    assert draws("demo", "payroll", between=True) == first
    assert draws("other", "payroll") != first
    assert draws("demo", "rent") != first
    assert streams.stream("demo", "payroll").random() == 0.17461457123400903      # sha256 seeding, not hash()


def test_check_names_the_cash_account_and_the_day_it_goes_negative():
    led = ledger()
    led.post(row(CHECKING, 5, -90_000))
    led.check()
    led.post(row(CHECKING, 9, -20_000, "BIG BILL"))
    led.post(row(CHECKING, 12, 500_000, "PAYROLL ACME CORP"))      # back above zero later: the first day is named
    with pytest.raises(ValueError, match=CHECKING) as err:
        led.check()
    assert "2030-01-09" in str(err.value) and SAVINGS not in str(err.value)
    led2 = ledger()
    led2.post(row(SAVINGS, 3, -60_000))
    with pytest.raises(ValueError, match=SAVINGS):
        led2.check()
    led3 = ledger()
    led3.post(row(CHECKING, 4, -100_001, pending=True))            # a pending row is not yet in the balance
    led3.adjust(SAVINGS, D(6), -50_000)                           # exactly zero is fine
    led3.check()


def test_check_passes_a_card_that_owes_and_a_loan():
    led = ledger()
    led.open("demo_card_0103", "credit-card", -20_000)
    led.open("demo_loan_0104", "loan", -25_000_000)
    led.post(row("demo_card_0103", 5, -9_000))
    led.adjust("demo_loan_0104", D(10), 40_000)
    led.check()


def test_a_row_refuses_a_description_the_parsers_would_rewrite():
    for bad in (" ACME COFFEE", "ACME COFFEE ", "ACME  COFFEE", "ACME\tCOFFEE", "ACME\nCOFFEE", ""):
        with pytest.raises(ValueError, match="description"):
            Row(CHECKING, D(5), -100, bad)
    assert Row(CHECKING, D(5), -100, 'ACME, "THE" COFFEE CO').desc == 'ACME, "THE" COFFEE CO'


def test_open_refuses_an_unknown_kind_and_move_refuses_mixed_pending_legs():
    led = ledger()
    with pytest.raises(ValueError, match="kind"):
        led.open("demo_card_0103", "card", 0)
    for kind in ("checking", "savings", "credit-card", "brokerage", "loan"):
        led.open(f"demo_{kind}_0200", kind, 0)
    with pytest.raises(ValueError, match="pending"):
        led.move(row(CHECKING, 5, -1_000, pending=True), row(SAVINGS, 5, 1_000))
    assert led.rows(CHECKING) == [] and led.rows(SAVINGS) == []


@pytest.mark.parametrize(("numerator", "denominator", "rounded"), [
    (5, 2, 3), (-5, 2, -3), (7, 2, 4), (-7, 2, -4), (4, 3, 1), (-4, 3, -1), (5, 3, 2), (-5, 3, -2), (0, 7, 0),
    (1_234_550, 100, 12_346), (-1_234_550, 100, -12_346), (1_234_549, 100, 12_345)])
def test_money_rounds_a_half_up_in_integers(numerator, denominator, rounded):
    """The generator's one rounding rule: the nearest whole number, a half away from zero (as Decimal's ROUND_HALF_UP
    rounds the loan's figures), never Python's half-to-even round()."""
    assert half_up(numerator, denominator) == rounded
    with pytest.raises(ValueError):
        half_up(numerator, -denominator)


def test_expect_and_row_are_frozen_values():
    e = Expect("purchase", "Food", "Groceries", "keyword", "Acme Grocer", pair=None,
               split=(("Food", "Groceries", 900), ("Home", "Supplies", 100)))
    r = Row(CHECKING, D(5), -1_000, "ACME GROCER", expect=e)
    assert r.expect is e and r.pending is False and r.feed_id is None and r.plant is None
    with pytest.raises(AttributeError):
        r.cents = 5                                               # type: ignore[misc]
    with pytest.raises(AttributeError):
        e.type = "income"                                         # type: ignore[misc]

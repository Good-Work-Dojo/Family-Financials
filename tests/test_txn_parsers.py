# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Parser tests on synthetic lookalike fixtures (no real data)."""
import importlib
import textwrap
from pathlib import Path

import pytest

from etl import importers, parsers, pipeline
from etl.importers import bofa_bank, chase_card, chase_checking, registry


def write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(textwrap.dedent(content))
    return p


def test_chase_checking_multiline_quoted_row(tmp_path):
    # mimics a bank row: quoted field with embedded newline + blank balance
    content = ('Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n'
               'DEBIT,03/14/2030,"POS DEBIT     EXAMPLE.COM   +18001234567  IL'
               '         \n             1234",-22.75,MISC_DEBIT, ,,\n'
               'CREDIT,01/08/2030,"Online Transfer from CHK ...9999 '
               'transaction#: 123",1800.00,ACCT_XFER,1800.00,,\n')
    p = tmp_path / "chase.CSV"
    p.write_text(content)
    txns, stats, extra = chase_checking.parse(p, "demo_checking_0000")
    assert stats.rows_parsed == 2
    assert txns[0].amount_cents == -2275
    assert "\n" not in txns[0].description_raw      # padding collapsed
    assert txns[1].bank_type == "ACCT_XFER"
    assert extra["latest_balance"] == ("2030-01-08", 180000)


def test_a_chase_checking_row_whose_balance_is_unreadable_is_read_without_its_balance(tmp_path):
    """The Balance cell is no part of a row's identity: a row whose cell holds no amount is stored with the same uid
    it has when the cell is readable, and the file's latest balance comes from the other rows."""
    header = "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
    rows = ("DEBIT,03/14/2030,SAMPLE GROCER,-22.75,MISC_DEBIT,{balance},,\n"
            "CREDIT,03/08/2030,SAMPLE PAYROLL,1800.00,ACH_CREDIT,1800.00,,\n")
    unreadable = write(tmp_path, "unreadable.csv", header + rows.format(balance="n.a."))
    readable = write(tmp_path, "readable.csv", header + rows.format(balance="1777.25"))
    txns, stats, extra = chase_checking.parse(unreadable, "demo_checking_0000")
    assert stats.rows_parsed == 2 and not stats.skip_reasons
    assert extra["latest_balance"] == ("2030-03-08", 180000)
    same, _, _ = chase_checking.parse(readable, "demo_checking_0000")
    for t in (*txns, *same):
        t.source_file = "statement.csv"
    pipeline.assign_occurrence_seq(txns)
    pipeline.assign_occurrence_seq(same)
    assert [t.uid for t in txns] == [t.uid for t in same]


def test_chase_card_repeated_header_and_types(tmp_path):
    p = write(tmp_path, "card.csv", """\
        Transaction Date,Post Date,Description,Category,Type,Amount,Memo
        Transaction Date,Post Date,Description,Category,Type,Amount,Memo
        05/25/2024,05/27/2024,COFFEE PLACE,Food & Drink,Sale,-5.50,
        06/01/2024,06/01/2024,Payment Thank You-Mobile,,Payment,500.00,
        06/02/2024,06/03/2024,RETURN STORE,Shopping,Return,12.00,
        """)
    txns, stats, _ = chase_card.parse(p, "demo_card_0000")
    assert stats.rows_parsed == 3
    assert stats.skip_reasons.get("blank-or-repeated-header") == 1
    assert txns[0].bank_category == "Food & Drink"
    assert txns[1].bank_type == "Payment"


def test_bofa_bank_statement_style(tmp_path):
    p = write(tmp_path, "bofa.csv", """\
        Description,,Summary Amt.
        Beginning balance as of 12/04/2024,,"2,904.71"
        Total credits,,"1,500.00"
        Total debits,,"-1,000.00"
        Ending balance as of 06/04/2026,,"3,404.71"

        Date,Description,Amount,Running Bal.
        12/04/2024,"Beginning balance as of 12/04/2024",,"2,904.71"
        12/10/2024,"EMPLOYER DES:PAYMENT ID:1 INDN:JANE",1500.00,"4,404.71"
        01/15/2025,"GROCERY STORE PURCHASE","-1,000.00","3,404.71"
        """)
    txns, stats, extra = bofa_bank.parse(p, "demo_bank_0000")
    assert stats.rows_parsed == 2
    assert stats.skip_reasons.get("pseudo-row-no-amount") == 1
    s = extra["summary"]
    assert s["beginning_balance"] == 290471
    assert s["ending_balance"] == 340471
    assert s["total_credits"] == 150000
    assert s["total_debits"] == -100000
    assert extra["latest_balance"] == ("2026-06-04", 340471)
    # the built-in reconciliation must hold on this fixture
    assert sum(t.amount_cents for t in txns) == s["ending_balance"] - s["beginning_balance"]


def test_file_stats_account_for_every_row(tmp_path):
    # the reconciliation contract every parser keeps: each data row is parsed or skipped with a
    # reason, and sum_cents is the signed total of exactly the rows returned
    p = write(tmp_path, "sample_card.csv", """\
        Transaction Date,Post Date,Description,Category,Type,Amount,Memo
        01/04/2030,01/05/2030,SAMPLE CAFE,Food & Drink,Sale,-4.25,
        ,,,,,,
        1/2/2030,1/3/2030,SAMPLE STORE,Shopping,Sale,(12.00),
        01/09/2030,01/09/2030,SAMPLE STORE REFUND,Shopping,Return,+3.10,
        01/10/2030,01/10/2030,SAMPLE NO AMOUNT,Shopping,Sale,,
        01/11/2030,01/11/2030,SAMPLE BAD AMOUNT,Shopping,Sale,n0t-money,
        """)
    txns, stats, _ = chase_card.parse(p, "demo_card_0000")
    assert [t.amount_cents for t in txns] == [-425, -1200, 310]
    assert stats.rows_total == 6
    assert stats.rows_parsed == len(txns) == 3
    assert stats.rows_skipped == 3 == sum(stats.skip_reasons.values())
    assert stats.skip_reasons == {"blank-or-repeated-header": 1, "no-amount": 1, "unparseable-row": 1}
    assert stats.sum_cents == sum(t.amount_cents for t in txns) == -1315
    assert (stats.date_min, stats.date_max) == ("2030-01-02", "2030-01-09")


def test_the_parser_registry_is_exactly_the_native_formats():
    # This pins which file formats exist, so no format arrives unnoticed under an institution's name. A new format is
    # added here on purpose, with its module: every module
    # of etl/importers/ is imported (a module registers its formats when it loads) and each format the registry then
    # holds is listed with the module that registered it.
    native = {"chase_checking": "chase_checking", "chase_card": "chase_card", "bofa_bank": "bofa_bank",
              "ofx": "ofx",                                 # OFX and QFX statement downloads
              # a shipped preset is read by the preset engine
              "bofa_card": "csv_preset", "capital_one_card": "csv_preset", "discover_card": "csv_preset",
              "schwab_checking": "csv_preset", "wells_fargo": "csv_preset"}
    for p in sorted(Path(importers.__file__).parent.glob("*.py")):
        if p.stem != "__init__":
            importlib.import_module(f"etl.importers.{p.stem}")
    registered = {name: parse.__module__.removeprefix("etl.importers.") for name, parse in registry.formats().items()}
    assert registered == native
    assert set(pipeline.TXN_PARSERS) == set(native)
    modules = {p.stem for p in Path(parsers.__file__).parent.glob("*.py") if p.stem != "__init__"}
    assert modules == {"simplefin"}                      # the bank feed's reader, no statement format


CHECKING_SAMPLE = """\
    Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #
    DEBIT,01/30/2030,SAMPLE PHARMACY,-20.00,DEBIT_CARD,4865.00,
    """


@pytest.mark.parametrize("header", [
    "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #",    # a checking export
    "Transaction Date,Post Date,Description,Category,Type,Debit,Credit",        # no Amount
    "Transaction Date,Post Date,Merchant,Category,Type,Amount,Memo",            # no Description
    "Date,Post Date,Description,Category,Type,Amount,Memo",                     # no Transaction Date
])
def test_a_card_file_without_the_columns_the_format_reads_is_not_this_format(tmp_path, header):
    p = tmp_path / "card.csv"
    p.write_text(header + "\n01/30/2030,01/31/2030,SAMPLE PHARMACY,Health,Sale,-20.00,\n")
    with pytest.raises(registry.NotThisFormat) as caught:
        chase_card.parse(p, "demo_card_0000")
    assert str(caught.value) == "its first line is not the header this format starts with"
    p = write(tmp_path, "checking.csv", CHECKING_SAMPLE)
    with pytest.raises(registry.NotThisFormat):
        chase_card.parse(p, "demo_card_0000")
    p.write_text(header + "\n")                                                 # the first line alone is judged too
    with pytest.raises(registry.NotThisFormat):
        chase_card.parse(p, "demo_card_0000")


@pytest.mark.parametrize("content", [
    "",                                                                         # no bytes
    "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n",       # the header alone
    "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n\n\n",   # the header and blank lines
])
def test_a_card_file_with_no_row_reads_as_no_rows(tmp_path, content):
    p = tmp_path / "card.csv"
    p.write_text(content)
    txns, stats, extra = chase_card.parse(p, "demo_card_0000")
    assert (txns, stats.rows_total, stats.rows_parsed, stats.skip_reasons, extra) == ([], 0, 0, {}, {})


def test_a_card_header_with_more_columns_in_another_order_reads_as_before(tmp_path):
    p = write(tmp_path, "card.csv", """\
        Memo,Amount,Type,Category,Description,Post Date,Transaction Date,Card
        ,-5.50,Sale,Food & Drink,SAMPLE CAFE,05/27/2024,05/25/2024,0000
        ,bad,Sale,Food & Drink,SAMPLE BAD AMOUNT,05/27/2024,05/26/2024,0000
        """)
    txns, stats, _ = chase_card.parse(p, "demo_card_0000")
    assert [(t.date, t.description_raw, t.amount_cents, t.bank_type, t.bank_category, t.seq) for t in txns] == [
        ("2024-05-25", "SAMPLE CAFE", -550, "Sale", "Food & Drink", 0)]
    assert stats.skip_reasons == {"unparseable-row": 1}


def test_a_checking_file_whose_first_line_is_another_header_is_not_this_format(tmp_path):
    p = write(tmp_path, "card.csv", """\
        Transaction Date,Post Date,Description,Category,Type,Amount,Memo
        01/04/2030,01/05/2030,SAMPLE CAFE,Food & Drink,Sale,-4.25,
        """)
    with pytest.raises(registry.NotThisFormat) as caught:
        chase_checking.parse(p, "demo_checking_0000")
    assert str(caught.value) == "its first line is not the header this format starts with"


@pytest.mark.parametrize("parser", [chase_checking, chase_card, bofa_bank], ids=lambda m: m.__name__.rsplit(".", 1)[1])
def test_a_file_with_no_bytes_reads_as_no_rows_in_every_native_format(tmp_path, parser):
    p = tmp_path / "empty.csv"
    p.write_text("")
    txns, stats, extra = parser.parse(p, "demo_checking_0000")
    assert (txns, stats.rows_total, stats.rows_parsed, stats.skip_reasons, stats.sum_cents) == ([], 0, 0, {}, 0)
    assert extra.get("latest_balance") is None and not extra.get("summary")


BANK_STATEMENT = """\
    Description,,Summary Amt.
    Beginning balance as of 01/01/2030,,"2,904.71"
    Total credits,,"0.00"
    Total debits,,"0.00"
    Ending balance as of 01/31/2030,,"2,904.71"

    Date,Description,Amount,Running Bal.
    01/01/2030,"Beginning balance as of 01/01/2030",,"2,904.71"
    """

NOT_BANK_STATEMENTS = {
    "a checking export": CHECKING_SAMPLE,
    "a card export": """\
        Transaction Date,Post Date,Description,Category,Type,Amount,Memo
        01/04/2030,01/05/2030,SAMPLE CAFE,Food & Drink,Sale,-4.25,
        """,
    "an error page": """\
        <!DOCTYPE html>
        <html><head><title>Service unavailable</title></head>
        <body><p>Please try again later.</p></body></html>
        """,
    "its summary without the header line": BANK_STATEMENT.split("\n\n")[0] + "\n",
}


@pytest.mark.parametrize("content", NOT_BANK_STATEMENTS.values(), ids=NOT_BANK_STATEMENTS.keys())
def test_a_file_with_no_header_line_is_not_a_bank_statement(tmp_path, content):
    p = write(tmp_path, "statement.csv", content)
    with pytest.raises(registry.NotThisFormat) as caught:
        bofa_bank.parse(p, "demo_savings_0000")
    assert str(caught.value) == "none of its lines is the header line this format reads its rows under"


BANK_STATEMENT_ROWS = BANK_STATEMENT + """\
    01/05/2030,"SAMPLE CAFE",-4.25,"2,900.46"
    01/09/2030,"SAMPLE EMPLOYER PAYROLL","1,500.00","4,400.46"
    """


@pytest.mark.parametrize("header", [
    "Date,Description,Amount,Running Bal",                      # no final dot
    "date,description,amount,running bal.",                     # another case
    "DATE,DESCRIPTION,AMOUNT,RUNNING BAL",
    "Date,Description,Amount,Running  Bal.",                    # a doubled inner space
    '" Date ",Description ,Amount,"Running Bal. "',             # padded cells
    "Date,Description,Amount,Running Bal.,Memo",                # a fifth column
    "Date,Description,Amount,Running Bal.,,",                   # empty cells after the last
], ids=["no-dot", "lower-case", "upper-case-no-dot", "doubled-space", "padded", "fifth-column", "trailing-empties"])
def test_a_bank_statement_header_reads_the_rows_whatever_its_case_spacing_final_dot_or_extra_columns(tmp_path,
                                                                                                    header):
    """Each of the header's four cells is compared with its whitespace collapsed and its case folded, the final dot
    optional, and columns after the fourth allowed: the rows read are the plain header's"""
    (tmp_path / "plain").mkdir()
    (tmp_path / "variant").mkdir()
    plain = write(tmp_path / "plain", "statement.csv", BANK_STATEMENT_ROWS)
    variant = write(tmp_path / "variant", "statement.csv",
                    BANK_STATEMENT_ROWS.replace("Date,Description,Amount,Running Bal.", header, 1))
    assert variant.read_text().count(header) == 1
    read = bofa_bank.parse(plain, "demo_savings_0000")
    assert [(t.date, t.amount_cents, t.description_raw) for t in read[0]] == [
        ("2030-01-05", -425, "SAMPLE CAFE"), ("2030-01-09", 1500_00, "SAMPLE EMPLOYER PAYROLL")]
    assert bofa_bank.parse(variant, "demo_savings_0000") == read


@pytest.mark.parametrize("header", [
    "Date,Description,Amount",                                  # three columns
    "Date,Description,Amount,Running Balance",                  # another word
    "Date,Amount,Description,Running Bal.",                     # another order
    "Posted Date,Description,Amount,Running Bal.",
])
def test_a_header_that_is_not_the_bank_statements_is_still_not_this_format(tmp_path, header):
    p = write(tmp_path, "statement.csv", BANK_STATEMENT_ROWS.replace("Date,Description,Amount,Running Bal.", header, 1))
    with pytest.raises(registry.NotThisFormat):
        bofa_bank.parse(p, "demo_savings_0000")


def test_a_bank_statement_of_a_month_with_no_transaction_reads_as_no_rows(tmp_path):
    p = write(tmp_path, "statement.csv", BANK_STATEMENT)
    txns, stats, extra = bofa_bank.parse(p, "demo_savings_0000")
    assert (txns, stats.rows_parsed) == ([], 0)
    assert stats.skip_reasons == {"summary-row": 4, "preamble": 1, "blank-line": 1, "header": 1,
                                  "pseudo-row-no-amount": 1}
    assert extra["latest_balance"] == ("2030-01-31", 290471)
    p.write_text("\n\n")                                     # blank lines only: nothing to judge, no rows
    assert bofa_bank.parse(p, "demo_savings_0000")[0] == []

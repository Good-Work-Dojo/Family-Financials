# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The OFX / QFX importer (etl/importers/ofx.py): the "Quicken / Money download" most US banks offer, read by an
in-house tokenizer in both dialects, SGML 1.x (leaf tags left unclosed, line breaks optional) and XML 2.x. A row is a
transaction of one bank or card statement's transaction list: its date is the first eight characters of DTPOSTED,
never shifted by the zone written after them; its amount is TRNAMT as the file signs it (a decimal comma accepted in
one form only); its description is NAME (or the payee's) with MEMO added unless one holds the other; a row whose end
tag a writer left out ends where the next one starts. A pending list is skipped and counted; an exact repeat of a
transaction id is dropped and counted, while one id on two different rows, in one file or across an account's files,
keeps both and raises the account's warning, and the id is never part of a row's identity; the ledger balance is the
file's balance. A file of several statements (in words saying whether they are one account's), an investment
statement, a file that ends early, a statement none of whose rows can be read and a file that is not OFX are refused
in plain words, an empty file is no rows, hostile shapes never crash the read, and no account number, bank id or user
credential leaves the parser.

Every file here is invented: the samples under tests/importers/samples/ofx/ and the files written into tmp_path."""
from __future__ import annotations

import codecs
import dataclasses
import hashlib
import logging
import shutil
import traceback
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from conftest import ROOT, isolated_pipeline, write_household

from etl import pipeline
from etl.config import Config
from etl.importers import ofx, registry
from etl.importers.registry import NotThisFormat
from etl.models import Txn

SAMPLES = ROOT / "tests" / "importers" / "samples" / "ofx"
EXAMPLE = ROOT / "examples" / "minimal"
ACCOUNT = "demo_checking_0000"
# The invented account numbers, bank id, broker id and user id the samples and the files below carry: none may reach a
# row, a count, a balance, a check, a message or a log line.
SECRETS = ("000012345678", "000087654321", "0000123412341234", "000099999", "sampleuser0001", "broker.sample.example",
           "SAMPLEPASS0001", "SAMPLECOOKIE0001", "SAMPLECLIENT0001")

HEADER = ("OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\nENCODING:USASCII\nCHARSET:1252\nCOMPRESSION:NONE\n"
          "OLDFILEUID:NONE\nNEWFILEUID:NONE\n\n")
UTF8_HEADER = HEADER.replace("ENCODING:USASCII", "ENCODING:UTF-8").replace("CHARSET:1252", "CHARSET:NONE")
PLAIN_HEADER = HEADER.replace("CHARSET:1252", "CHARSET:NONE")         # declares no character set
BALANCE = "<LEDGERBAL><BALAMT>100.00<DTASOF>20301031</LEDGERBAL>"


# ------------------------------------------------------------------ helpers

def statement(transactions: str, *, header: str = HEADER, balance: str = BALANCE, tail: str = "</OFX>\n") -> str:
    """An SGML bank statement holding `transactions` (STMTTRN blocks) in its transaction list"""
    return (f"{header}<OFX>\n<BANKMSGSRSV1><STMTTRNRS><TRNUID>0\n<STMTRS><CURDEF>USD\n<BANKACCTFROM><BANKID>000099999\n"
            f"<ACCTID>000012345678\n<ACCTTYPE>CHECKING\n</BANKACCTFROM>\n<BANKTRANLIST><DTSTART>20301001\n"
            f"<DTEND>20301031\n{transactions}</BANKTRANLIST>\n{balance}\n</STMTRS></STMTTRNRS></BANKMSGSRSV1>\n{tail}")


def trn(date: str | None = "20301005", amount: str | None = "-5.00", name: str | None = "ACME COFFEE", *,
        memo: str | None = None, fitid: str | None = None, more: str = "") -> str:
    """One SGML transaction; a field given None is left out, `more` is written after the others"""
    fields = (("TRNTYPE", "DEBIT"), ("DTPOSTED", date), ("TRNAMT", amount), ("FITID", fitid), ("NAME", name),
              ("MEMO", memo))
    body = "".join(f"<{tag}>{value}\n" for tag, value in fields if value is not None)
    return f"<STMTTRN>{body}{more}</STMTTRN>\n"


def ofx_file(tmp_path: Path, text: str, name: str = "statement.ofx", encoding: str = "utf-8") -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


def read(path: Path, account_id: str = ACCOUNT) -> tuple[list[Txn], object, dict]:
    """The parse of `path`, its counts checked against the contract every format keeps: each transaction is read or
    skipped with a reason, and the signed total is that of exactly the rows read"""
    txns, stats, extra = ofx.parse(path, account_id)
    assert stats.rows_total == stats.rows_parsed + stats.rows_skipped
    assert stats.rows_skipped == sum(stats.skip_reasons.values())
    assert stats.rows_parsed == len(txns)
    assert stats.sum_cents == sum(t.amount_cents for t in txns)
    assert (stats.parser, stats.source_file) == ("ofx", path.name)
    # the positions of the rows whose description was cut, only when there is one
    cut = extra.get("cut_descriptions")
    assert set(extra) == {"latest_balance", "fitid_rows", *(["cut_descriptions"] if cut is not None else [])}
    assert cut is None or (cut and all(0 <= i < len(txns) for i in cut))
    assert all(t.account_id == account_id and t.source_file == path.name for t in txns)
    return txns, stats, extra


def digest(fitid: str) -> str:
    """How the parse names a bank transaction id: never as written (a bank may put account digits in it)"""
    return hashlib.sha256(fitid.encode("utf-8")).hexdigest()


def shared(extra: dict) -> int:
    """The number of bank transaction ids of a parse that name two or more different rows"""
    return sum(len(named) > 1 for named in extra["fitid_rows"].values())


def rows(txns: list[Txn]) -> list[tuple[str, int, str, str | None]]:
    return [(t.date, t.amount_cents, t.description_raw, t.bank_type) for t in txns]


def refusal(path: Path) -> str:
    with pytest.raises(NotThisFormat) as caught:
        ofx.parse(path, ACCOUNT)
    return str(caught.value)


def strings(value: object) -> Iterator[str]:
    """Every string reachable from `value`: each field of a dataclass, each key and item of a container, the text of
    every other value, and the repr of each"""
    yield repr(value)
    if dataclasses.is_dataclass(value):
        for f in dataclasses.fields(value):
            yield from strings(getattr(value, f.name))
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from strings(k)
            yield from strings(v)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for v in value:
            yield from strings(v)
    else:
        yield str(value)


# ------------------------------------------------------------------ the format

def test_ofx_is_one_format_in_the_registry():
    assert registry.formats()["ofx"] is ofx.parse
    assert pipeline.TXN_PARSERS["ofx"] is ofx.parse


def test_an_sgml_bank_statement_reads_each_transaction_as_the_file_signs_it():
    txns, stats, extra = read(SAMPLES / "bank_sgml.ofx")
    assert rows(txns) == [
        ("2030-10-03", -54_18, "TRADER JOE'S #000", "DEBIT"),                 # MEMO repeats NAME: said once
        ("2030-10-12", -6_45, "STARBUCKS STORE 00000 SAMPLETOWN ST", "DEBIT"),
        ("2030-10-20", -120_00, "CHECK 1001", "CHECK"),                      # the check number is not part of it
        ("2030-10-31", 2500_00, "Sample Employer Payroll", "DIRECTDEP"),      # NAME inside MEMO: MEMO
    ]
    assert all(t.bank_category is None and t.seq == 0 for t in txns)        # the pipeline numbers occurrences
    assert (stats.rows_total, stats.skip_reasons) == (4, {})
    assert (stats.date_min, stats.date_max) == ("2030-10-03", "2030-10-31")
    assert extra["latest_balance"] == ("2030-10-31", 4319_37)                  # the ledger balance
    # each bank transaction id, as a digest, with the rows it names
    assert extra["fitid_rows"] == {
        digest("2030100300001"): {("2030-10-03", -54_18, "TRADER JOE'S #000")},
        digest("2030101200001"): {("2030-10-12", -6_45, "STARBUCKS STORE 00000 SAMPLETOWN ST")},
        digest("2030102000001"): {("2030-10-20", -120_00, "CHECK 1001")},
        digest("2030103100001"): {("2030-10-31", 2500_00, "Sample Employer Payroll")}}


def test_the_same_statement_on_one_line_reads_the_same(tmp_path):
    many = read(SAMPLES / "bank_sgml.ofx")
    one = read(SAMPLES / "bank_sgml_one_line.ofx")
    assert rows(one[0]) == rows(many[0]) and one[2] == many[2]
    # the whole file one line, its header too
    flat = (SAMPLES / "bank_sgml.ofx").read_text(encoding="utf-8").replace("\n", "")
    whole = read(ofx_file(tmp_path, flat))
    assert rows(whole[0]) == rows(many[0]) and whole[2] == many[2]


@pytest.mark.parametrize("line_end", ["\r", "\r\n"])
def test_cr_and_crlf_line_ends_read_like_any_others(tmp_path, line_end):
    text = (SAMPLES / "bank_sgml.ofx").read_text(encoding="utf-8").replace("\n", line_end)
    txns, _, extra = read(ofx_file(tmp_path, text))
    assert rows(txns) == rows(read(SAMPLES / "bank_sgml.ofx")[0])
    assert extra["latest_balance"] == ("2030-10-31", 4319_37)


def test_an_xml_statement_reads_the_same_way():
    txns, stats, extra = read(SAMPLES / "bank_xml.ofx")
    assert rows(txns) == [
        ("2030-10-05", -12_50, "PANERA BREAD #000 SAMPLETOWN", "DEBIT"),     # a decimal comma; NAME inside MEMO
        ("2030-10-09", -41_07, "SHELL OIL & GAS", "DEBIT"),                   # an entity decoded; an empty MEMO
        ("2030-10-31", 1_23, "SAMPLE BANK INTEREST", "INT"),                  # the payee's NAME; a plus sign
    ]
    assert (stats.rows_total, stats.skip_reasons) == (3, {})
    assert extra["latest_balance"] == ("2030-10-31", 1000_00)
    assert set(extra["fitid_rows"]) == {digest("X-0001"), digest("X-0002"), digest("X-0003")} and shared(extra) == 0


def test_a_card_statement_keeps_the_files_signs_purchases_out_and_payments_in():
    txns, _, extra = read(SAMPLES / "card_sgml.qfx")
    assert rows(txns) == [("2030-10-04", -18_20, "CHIPOTLE 0000", "DEBIT"),
                          ("2030-10-15", 1250_00, "PAYMENT - THANK YOU", "CREDIT"),
                          ("2030-10-18", 32_99, "AUTOZONE 0000 RETURN", "CREDIT")]
    assert extra["latest_balance"] == ("2030-10-31", -845_12)               # what is owed, signed as the file has it


# ------------------------------------------------------------------ dates, amounts, descriptions

@pytest.mark.parametrize("stamp", ["20301031", "20301031120000", "20301031120000[-5:EST]", "20301031235959.000[0:GMT]",
                                   "20301031000000.000[+12:NZST]", "20301031235959[-12]"])
def test_the_date_is_the_first_eight_characters_of_dtposted_never_shifted_by_its_zone(tmp_path, stamp):
    txns, _, _ = read(ofx_file(tmp_path, statement(trn(date=stamp))))
    assert [t.date for t in txns] == ["2030-10-31"]


@pytest.mark.parametrize("stamp", [None, "", "2030-10-31", "20301331", "20300230", "2030103", "OCT 31 2030"])
def test_a_transaction_without_a_date_it_can_read_is_skipped_never_dated_otherwise(tmp_path, stamp):
    bad = trn(date=stamp, more="<DTUSER>20301001\n")                         # the user date is never used instead
    txns, stats, _ = read(ofx_file(tmp_path, statement(bad + trn(date="20301002", name="SAMPLE GROCER"))))
    assert rows(txns) == [("2030-10-02", -5_00, "SAMPLE GROCER", "DEBIT")]
    assert stats.skip_reasons == {"unparseable-date": 1}


def test_a_second_dtposted_in_one_transaction_is_ignored_the_first_wins(tmp_path):
    later = "<DTPOSTED>20301120\n<TRNAMT>-99.00\n<NAME>SAMPLE OTHER\n"
    txns, _, _ = read(ofx_file(tmp_path, statement(trn(more=later))))
    assert rows(txns) == [("2030-10-05", -5_00, "ACME COFFEE", "DEBIT")]


@pytest.mark.parametrize(("text", "cents"), [
    ("-12.50", -12_50), ("12.5", 12_50), ("+3", 3_00), (".75", 75), ("7.", 7_00), ("-12,50", -12_50), ("12,05", 12_05),
    ("-12.500", -12_50), ("0.00", 0), ("-0", 0)])
def test_trnamt_is_signed_cents_with_a_decimal_point_or_a_decimal_comma(tmp_path, text, cents):
    txns, _, _ = read(ofx_file(tmp_path, statement(trn(amount=text))))
    assert [t.amount_cents for t in txns] == [cents]


@pytest.mark.parametrize("text", ["1.234,56", "1,234.56", "12,5", "12,345", "-12,50,00", "12.345", "-", "+", ".", "12%",
                                  "$12.00", "1 000.00", "(12.00)", "--5", "12-", "1e3", "١٢", "9" * 5_000])
def test_any_other_amount_is_a_skipped_row_never_a_guess(tmp_path, text):
    path = ofx_file(tmp_path, statement(trn(amount=text) + trn(date="20301006", amount="-1.00", name="SAMPLE GROCER"),
                                        header=UTF8_HEADER))
    txns, stats, _ = read(path)
    assert rows(txns) == [("2030-10-06", -1_00, "SAMPLE GROCER", "DEBIT")]
    assert stats.skip_reasons == {registry.UNPARSEABLE_ROW: 1}


def test_a_transaction_without_an_amount_is_skipped_and_counted(tmp_path):
    text = statement(trn(amount=None) + trn(amount="", date="20301006") + trn(date="20301007", name="SAMPLE GROCER"))
    txns, stats, _ = read(ofx_file(tmp_path, text))
    assert rows(txns) == [("2030-10-07", -5_00, "SAMPLE GROCER", "DEBIT")]
    assert stats.skip_reasons == {"no-amount": 2}


@pytest.mark.parametrize(("name", "memo", "description"), [
    ("ACME COFFEE", None, "ACME COFFEE"),
    ("ACME COFFEE", "", "ACME COFFEE"),
    ("ACME COFFEE", "acme coffee", "ACME COFFEE"),                       # MEMO repeats NAME in another case
    ("ACME COFFEE #12", "Coffee", "ACME COFFEE #12"),                       # MEMO inside NAME
    ("ACME", "Acme Coffee Sampletown", "Acme Coffee Sampletown"),           # NAME inside MEMO: MEMO
    ("ACME  COFFEE ", " SAMPLETOWN \t ST", "ACME COFFEE SAMPLETOWN ST"),   # neither: joined, whitespace squashed
    ("ACME COFFEE", "ACME   coffee", "ACME COFFEE"),                       # compared squashed
    ("", "ACME COFFEE", "ACME COFFEE"),
    (None, "ACME COFFEE", "ACME COFFEE"),
    (None, None, ""),
])
def test_memo_is_added_to_name_unless_one_holds_the_other(tmp_path, name, memo, description):
    txns, _, _ = read(ofx_file(tmp_path, statement(trn(name=name, memo=memo))))
    assert [t.description_raw for t in txns] == [description]


def test_a_payee_aggregate_names_the_row(tmp_path):
    payee = ("<PAYEE><NAME>SAMPLE UTILITY CO\n<ADDR1>1 SAMPLE ST\n<CITY>SAMPLETOWN\n<STATE>ST\n<POSTALCODE>00000\n"
             "<PHONE>000-000-0000\n</PAYEE>\n")
    txns, _, _ = read(ofx_file(tmp_path, statement(trn(name=None, memo="OCT BILL", more=payee))))
    assert [t.description_raw for t in txns] == ["SAMPLE UTILITY CO OCT BILL"]


def test_entities_are_decoded_and_any_other_ampersand_is_left_as_written(tmp_path):
    name = ("A&amp;B &lt;C&gt; &quot;D&quot; &apos;E&apos; &#233;&#xE9;&#XE9; &nbsp; &copy; &#0; &#xD800; &#1114112; "
            "&#99999999; R&D &")
    txns, _, _ = read(ofx_file(tmp_path, statement(trn(name=name), header=UTF8_HEADER)))
    assert [t.description_raw for t in txns] == [
        "A&B <C> \"D\" 'E' ééé &nbsp; &copy; &#0; &#xD800; &#1114112; &#99999999; R&D &"]


def test_a_less_than_sign_that_starts_no_tag_is_text(tmp_path):
    names = trn(name="A < B") + trn(date="20301006", name="A <<< B") + trn(date="20301007", name="SAMPLE <3")
    txns, _, _ = read(ofx_file(tmp_path, statement(names)))
    assert [t.description_raw for t in txns] == ["A < B", "A <<< B", "SAMPLE <3"]


def test_a_comment_inside_a_value_is_passed_over_and_cdata_with_no_tag_in_it_is_its_text_as_written(tmp_path):
    names = (trn(name="ACME<!-- a comment --> COFFEE") + trn(date="20301006", name="<![CDATA[R&amp;D LAB]]> SAMPLE")
             + trn(date="20301007", name="SAMPLE <![CDATA[<ACCTID>000012345678]]>"))     # a tag in it is a tag
    txns, stats, extra = read(ofx_file(tmp_path, statement(names)))
    assert [t.description_raw for t in txns] == ["ACME COFFEE", "R&amp;D LAB SAMPLE", "SAMPLE"]
    assert not any("000012345678" in s for s in strings((txns, stats, extra)))


def test_a_declaration_or_an_instruction_never_reaches_past_the_next_tag(tmp_path):
    """Neither swallows the tag after it: an account number's tag stays a tag, a row's amount and a row's close stay
    the row's"""
    text = statement(trn(name="X <?pi<ACCTID>000012345678")
                     + trn(date="20301006", amount=None, name="SAMPLE GROCER <!<TRNAMT>-7.00")
                     + "<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20301007<TRNAMT>-2.00<NAME>SAMPLE PHARMACY"
                     + "<MEMO>SEE <!</STMTTRN>")
    txns, stats, extra = read(ofx_file(tmp_path, text))
    assert rows(txns) == [("2030-10-05", -5_00, "X", "DEBIT"), ("2030-10-06", -7_00, "SAMPLE GROCER", "DEBIT"),
                          ("2030-10-07", -2_00, "SAMPLE PHARMACY SEE", "DEBIT")]
    assert (stats.rows_total, stats.skip_reasons) == (3, {})
    assert not any("000012345678" in s for s in strings((txns, stats, extra)))


def test_a_name_of_ten_thousand_characters_is_stored_cut_to_its_first_500(tmp_path):
    name = "ACME COFFEE " + "X" * (10_000 - 12)
    txns, _, extra = read(ofx_file(tmp_path, statement(trn(name=name))))
    assert [t.description_raw for t in txns] == [name[:500]]
    assert extra["cut_descriptions"] == {0}


# ------------------------------------------------------------------ text encodings

@pytest.mark.parametrize(("header", "encoding"), [
    (HEADER, "cp1252"),                                                     # CHARSET:1252
    (HEADER.replace("CHARSET:1252", "CHARSET:ISO-8859-1"), "latin-1"),
    (UTF8_HEADER, "utf-8"),                                                 # ENCODING:UTF-8
    (PLAIN_HEADER, "utf-8"),                                                # none declared: UTF-8 first
    (PLAIN_HEADER, "cp1252"),                                               # then Windows-1252
])
def test_an_accented_merchant_reads_in_the_encoding_the_header_declares_or_the_defaults(tmp_path, header, encoding):
    path = ofx_file(tmp_path, statement(trn(name="CAFÉ SAMPLETOWN"), header=header), encoding=encoding)
    assert [t.description_raw for t in read(path)[0]] == ["CAFÉ SAMPLETOWN"]


@pytest.mark.parametrize("dialect", ["sgml", "xml"])
def test_a_declared_encoding_wins_over_the_default_order(tmp_path, dialect):
    """The byte 80 is the euro sign in Windows-1252, the default for bytes that are not UTF-8, and a control character
    in ISO-8859-1, which a stored description leaves out"""
    if dialect == "sgml":
        declared = statement(trn(name="SAMPLE \x80 SHOP"), header=HEADER.replace("CHARSET:1252", "CHARSET:ISO-8859-1"))
        plain = statement(trn(name="SAMPLE \x80 SHOP"), header=PLAIN_HEADER)
    else:
        xml = (SAMPLES / "bank_xml.ofx").read_text(encoding="utf-8").replace("SHELL OIL &amp; GAS", "SAMPLE \x80 SHOP")
        declared = xml.replace('encoding="UTF-8"', 'encoding="ISO-8859-1"')
        plain = xml.replace(' encoding="UTF-8"', "")
    named = [t.description_raw for t in read(ofx_file(tmp_path, declared, "declared.ofx", "latin-1"))[0]]
    assert "SAMPLE SHOP" in named
    assert "SAMPLE € SHOP" in [t.description_raw for t in read(ofx_file(tmp_path, plain, "plain.ofx", "latin-1"))[0]]


@pytest.mark.parametrize("header", [HEADER, HEADER.replace("CHARSET:1252", "CHARSET:ISO-8859-1"),
                                    '<?xml version="1.0" encoding="windows-1252"?>\n'])
def test_a_single_byte_set_declared_over_utf8_text_reads_as_utf8(tmp_path, header):
    """A bank that labels a UTF-8 file Windows-1252: a Windows-1252 reading of a multi-byte character is two wrong
    characters, and the same statement's XML download would describe the row otherwise"""
    text = statement(trn(name="CAFÉ SAMPLETOWN", memo="Crème"), header=header)
    assert [t.description_raw for t in read(ofx_file(tmp_path, text, "mislabelled.ofx"))[0]] == [
        "CAFÉ SAMPLETOWN Crème"]
    # true single-byte text under the same header reads in the set it declares
    path = ofx_file(tmp_path, text, "single-byte.ofx", "latin-1")
    assert [t.description_raw for t in read(path)[0]] == ["CAFÉ SAMPLETOWN Crème"]


@pytest.mark.parametrize("header", [UTF8_HEADER, HEADER])
def test_a_byte_order_mark_before_the_header_is_read_past(tmp_path, header):
    """The mark says UTF-8 whatever the header declares"""
    path = tmp_path / "statement.qfx"
    path.write_bytes(codecs.BOM_UTF8 + statement(trn(name="CAFÉ SAMPLETOWN"), header=header).encode("utf-8"))
    txns, _, extra = read(path)
    assert [t.description_raw for t in txns] == ["CAFÉ SAMPLETOWN"]
    assert extra["latest_balance"] == ("2030-10-31", 100_00)


def test_a_byte_past_the_start_that_neither_encoding_reads_refuses_the_file(tmp_path):
    padding = trn(name="ACME COFFEE", memo="X" * 70_000)                   # the first 64 KB are plain ASCII
    path = tmp_path / "statement.ofx"
    path.write_bytes(statement(padding + trn(date="20301006", name="SAMPLE \x81 GROCER"), header=PLAIN_HEADER)
                     .encode("latin-1"))
    assert refusal(path) == ofx.MIXED_TEXT


def test_text_past_the_start_is_reread_only_in_an_encoding_that_reads_the_start_the_same(tmp_path):
    padding = trn(name="ACME COFFEE", memo="X" * 70_000)
    late = trn(date="20301006", name="SAMPLE CAFÉ").encode("cp1252")
    # an ASCII start: UTF-8 and Windows-1252 read it alike, so a Windows-1252 byte past it reads in Windows-1252
    plain = statement(padding + "@@", header=PLAIN_HEADER).encode("ascii").split(b"@@")
    path = tmp_path / "ascii-start.ofx"
    path.write_bytes(plain[0] + late + plain[1])
    assert [t.description_raw for t in read(path)[0]] == [("ACME COFFEE " + "X" * 70_000)[:500], "SAMPLE CAFÉ"]
    # a UTF-8 start with an accented letter: Windows-1252 would read the start otherwise, so the file is refused
    accented = statement(trn(name="CAFÉ SAMPLETOWN") + padding + "@@", header=PLAIN_HEADER).encode("utf-8").split(b"@@")
    path = tmp_path / "utf8-start.ofx"
    path.write_bytes(accented[0] + late + accented[1])
    assert refusal(path) == ofx.MIXED_TEXT


def test_a_file_in_no_encoding_this_format_reads_is_refused(tmp_path):
    path = tmp_path / "statement.ofx"
    path.write_bytes(statement(trn(name="ACME COFFEE"), header=UTF8_HEADER).encode("utf-16-le"))
    assert refusal(path) == ofx.NOT_OFX                                      # no OFX block a byte-wise read can find
    path.write_bytes(b"<OFX>\x00" + statement(trn()).encode("utf-8"))
    assert refusal(path) == ofx.NOT_TEXT


# ------------------------------------------------------------------ transaction ids

def test_an_exact_repeat_of_a_transaction_id_is_dropped_and_counted(tmp_path):
    once = trn(fitid="F1")
    txns, stats, extra = read(ofx_file(tmp_path, statement(once + once + trn(date="20301006", fitid="F2", name="X"))))
    assert rows(txns) == [("2030-10-05", -5_00, "ACME COFFEE", "DEBIT"), ("2030-10-06", -5_00, "X", "DEBIT")]
    assert (stats.rows_total, stats.skip_reasons) == (3, {"repeated-fitid": 1})
    assert extra["fitid_rows"] == {digest("F1"): {("2030-10-05", -5_00, "ACME COFFEE")},
                                   digest("F2"): {("2030-10-06", -5_00, "X")}}


def test_one_transaction_id_on_different_rows_keeps_every_row_and_names_them_all(tmp_path):
    text = statement(trn(fitid="F1") + trn(fitid="F1", amount="-6.00") + trn(fitid="F1", date="20301006")
                     + trn(fitid="F1") + trn(fitid="F2", name="X") + trn(fitid="F2", name="Y") + trn(fitid="F3")
                     + trn() + trn())                                       # no id: never called a repeat
    txns, stats, extra = read(ofx_file(tmp_path, text))
    assert len(txns) == 8 and stats.skip_reasons == {"repeated-fitid": 1}
    assert extra["fitid_rows"][digest("F1")] == {("2030-10-05", -5_00, "ACME COFFEE"),
                                                 ("2030-10-05", -6_00, "ACME COFFEE"),
                                                 ("2030-10-06", -5_00, "ACME COFFEE")}
    assert len(extra["fitid_rows"]) == 3 and shared(extra) == 2              # F1 and F2
    assert not any("F1" in s or "F2" in s for s in strings((txns, stats, extra)))


def test_the_transaction_id_is_not_part_of_a_rows_identity(tmp_path):
    first = read(ofx_file(tmp_path, statement(trn(fitid="F1") + trn(fitid="F2")), "a.ofx"))[0]
    second = read(ofx_file(tmp_path, statement(trn(fitid="G7") + trn()), "b.ofx"))[0]
    for txns in (first, second):
        pipeline.assign_occurrence_seq(txns)
    assert [t.uid for t in first] == [t.uid for t in second]
    assert len({t.uid for t in first}) == 2


# ------------------------------------------------------------------ pending, empty, refused

def test_a_pending_list_is_skipped_and_counted():
    txns, stats, extra = read(SAMPLES / "pending.ofx")
    assert rows(txns) == [("2030-11-02", -9_99, "INSTACART", "DEBIT")]
    assert (stats.rows_total, stats.skip_reasons) == (3, {"pending": 2})
    assert extra["latest_balance"] == ("2030-11-07", 500_00)                 # not the pending list's as-of date


def test_a_transaction_in_a_pending_list_is_pending_whatever_its_tag(tmp_path):
    pending = "<BANKTRANLISTP><DTASOF>20301031\n" + trn(date="20301030", name="SAMPLE GROCER") + "</BANKTRANLISTP>\n"
    txns, stats, _ = read(ofx_file(tmp_path, statement(trn(), balance=pending + BALANCE)))
    assert rows(txns) == [("2030-10-05", -5_00, "ACME COFFEE", "DEBIT")]
    assert stats.skip_reasons == {"pending": 1}


def test_a_statement_with_no_transactions_is_no_rows_and_no_failure(tmp_path):
    txns, stats, extra = read(ofx_file(tmp_path, statement("")))
    assert (txns, stats.rows_total, stats.rows_skipped) == ([], 0, 0)
    assert extra == {"latest_balance": ("2030-10-31", 100_00), "fitid_rows": {}}
    txns, _, extra = read(ofx_file(tmp_path, statement("", balance=""), "no-balance.ofx"))
    assert (txns, extra["latest_balance"]) == ([], None)


@pytest.mark.parametrize("content", [b"", b"\r\n \n\t", codecs.BOM_UTF8, codecs.BOM_UTF8 + b"\n"])
def test_an_empty_file_is_no_rows_and_no_failure(tmp_path, content):
    path = tmp_path / "statement.qfx"
    path.write_bytes(content)
    txns, stats, extra = read(path)
    assert (txns, stats.rows_total, extra) == ([], 0, {"latest_balance": None, "fitid_rows": {}})


def _second_statement(account_from: str) -> str:
    return (f"<STMTTRNRS><TRNUID>2<STMTRS><CURDEF>USD{account_from}<BANKTRANLIST><DTSTART>20301101<DTEND>20301130"
            f"{trn(date='20301105')}</BANKTRANLIST></STMTRS></STMTTRNRS>")


def test_two_statements_in_one_file_are_refused_in_plain_words(tmp_path):
    assert ofx.SEVERAL_ACCOUNTS == "it holds statements for more than one account: download one account at a time"
    assert refusal(SAMPLES / "two_statements.ofx") == ofx.SEVERAL_ACCOUNTS
    card = (SAMPLES / "card_sgml.qfx").read_text(encoding="utf-8").split("<OFX>")[1].split("</OFX>")[0]
    bank = (SAMPLES / "bank_sgml.ofx").read_text(encoding="utf-8").replace("</OFX>", card + "</OFX>")
    assert refusal(ofx_file(tmp_path, bank)) == ofx.SEVERAL_ACCOUNTS         # a bank and a card statement
    one = statement(trn())
    other_bank = "<BANKACCTFROM><BANKID>000088888<ACCTID>000012345678<ACCTTYPE>CHECKING</BANKACCTFROM>"
    other = one.replace("</BANKMSGSRSV1>", _second_statement(other_bank) + "</BANKMSGSRSV1>")
    assert refusal(ofx_file(tmp_path, other, "other-bank.ofx")) == ofx.SEVERAL_ACCOUNTS     # one number, two banks


@pytest.mark.parametrize("account_from", [
    "<BANKACCTFROM><BANKID>000099999\n<ACCTID>000012345678\n<ACCTTYPE>CHECKING\n</BANKACCTFROM>",
    "<BANKACCTFROM><BANKID>000099999</BANKID><ACCTID> 000012345678 </ACCTID><ACCTTYPE>CHECKING</ACCTTYPE>"
    "</BANKACCTFROM>",
])
def test_two_statements_of_one_account_are_refused_as_two_periods(tmp_path, account_from):
    assert ofx.SEVERAL_PERIODS == "it holds more than one statement: download one statement period at a time"
    text = statement(trn()).replace("</BANKMSGSRSV1>", _second_statement(account_from) + "</BANKMSGSRSV1>")
    assert refusal(ofx_file(tmp_path, text)) == ofx.SEVERAL_PERIODS
    card = (SAMPLES / "card_sgml.qfx").read_text(encoding="utf-8")
    again = card.split("<CCSTMTTRNRS>")[1].split("</CCSTMTTRNRS>")[0]
    twice = card.replace("</CCSTMTTRNRS>", f"</CCSTMTTRNRS><CCSTMTTRNRS>{again}</CCSTMTTRNRS>", 1)
    assert refusal(ofx_file(tmp_path, twice, "card-twice.qfx")) == ofx.SEVERAL_PERIODS


def test_two_statements_one_of_which_names_no_account_are_refused_without_naming_either_cause(tmp_path):
    text = statement(trn()).replace("</BANKMSGSRSV1>", _second_statement("") + "</BANKMSGSRSV1>")
    assert refusal(ofx_file(tmp_path, text)) == ofx.SEVERAL_STATEMENTS


def test_an_investment_statement_is_refused(tmp_path):
    text = (HEADER + "<OFX><INVSTMTMSGSRSV1><INVSTMTTRNRS><TRNUID>0<INVSTMTRS><DTASOF>20301031<CURDEF>USD<INVACCTFROM>"
            "<BROKERID>broker.sample.example<ACCTID>000012345678</INVACCTFROM><INVTRANLIST><DTSTART>20301001"
            "<DTEND>20301031</INVTRANLIST></INVSTMTRS></INVSTMTTRNRS></INVSTMTMSGSRSV1></OFX>")
    assert refusal(ofx_file(tmp_path, text)) == ofx.INVESTMENT


def test_an_ofx_file_without_a_statement_is_refused(tmp_path):
    text = (HEADER + "<OFX><SIGNONMSGSRSV1><SONRS><STATUS><CODE>15500<SEVERITY>ERROR</STATUS><DTSERVER>20301101"
            "<LANGUAGE>ENG</SONRS></SIGNONMSGSRSV1></OFX>")
    assert refusal(ofx_file(tmp_path, text)) == ofx.NO_STATEMENT


@pytest.mark.parametrize("content", [
    b"<!DOCTYPE html>\n<html><head><title>Sign in</title></head><body><p>Your session has expired.</p></body></html>\n",
    b"Date,Description,Amount\n10/05/2030,ACME COFFEE,-5.00\n",
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00",
    b"OFXHEADER:100\nDATA:OFXSGML\n\n" + b" " * 70_000 + b"<OFX></OFX>",     # no OFX block in its first 64 KB
])
def test_a_file_that_is_not_ofx_is_refused(tmp_path, content):
    path = tmp_path / "statement.qfx"
    path.write_bytes(content)
    assert refusal(path) == ofx.NOT_OFX


def test_a_file_that_ends_before_its_statement_does_is_refused(tmp_path):
    whole = statement(trn() + trn(date="20301006", amount="-123.45", name="SAMPLE GROCER"))
    inside = whole[: whole.index("-123.45") + 3]                             # cut inside an amount: -12
    assert refusal(ofx_file(tmp_path, inside)) == ofx.ENDS_EARLY
    between = whole[: whole.index("</STMTTRN>") + len("</STMTTRN>")]       # cut between two transactions
    assert refusal(ofx_file(tmp_path, between)) == ofx.ENDS_EARLY
    # a file whose statement is whole and whose wrappers are cut reads every row
    after = whole[: whole.index("</STMTRS>") + len("</STMTRS>")]
    assert len(read(ofx_file(tmp_path, after))[0]) == 2


def test_tag_soup_reads_every_transaction(tmp_path):
    soup = ("</NAME></SAMPLE>\n" + trn() + "</STMTTRN></MEMO>\n"                # closes with no open: ignored
            + "<!-- a comment --><?sample instruction?>\n"
            + "<STMTTRN><DTPOSTED>20301006<TRNAMT>-2.00<NAME>SAMPLE GROCER\n"     # no end tag: the next row ends it
            + trn(date="20301007", amount="-3.00", name="SAMPLE PHARMACY <B>")
            + "<STMTTRN><DTPOSTED>20301008<TRNAMT>-4.00<NAME>SAMPLE BOOKSHOP<SIC>\n")   # ended by the list's close
    txns, stats, _ = read(ofx_file(tmp_path, statement(soup)))
    assert rows(txns) == [("2030-10-05", -5_00, "ACME COFFEE", "DEBIT"), ("2030-10-06", -2_00, "SAMPLE GROCER", None),
                          ("2030-10-07", -3_00, "SAMPLE PHARMACY", "DEBIT"),
                          ("2030-10-08", -4_00, "SAMPLE BOOKSHOP", None)]
    assert (stats.rows_total, stats.skip_reasons) == (4, {})


def test_a_writer_that_leaves_out_every_rows_end_tag_reads_every_row(tmp_path):
    """SGML lets a writer leave an end tag out: a row ends where the next one starts, or where its list ends"""
    lax = (trn().replace("</STMTTRN>", "")
           + trn(date="20301006", amount="-3.00", name="SAMPLE GROCER", fitid="2").replace("</STMTTRN>", ""))
    txns, stats, extra = read(ofx_file(tmp_path, statement(lax)))
    assert rows(txns) == [("2030-10-05", -5_00, "ACME COFFEE", "DEBIT"),
                          ("2030-10-06", -3_00, "SAMPLE GROCER", "DEBIT")]
    assert (stats.rows_total, stats.skip_reasons) == (2, {})
    assert extra["latest_balance"] == ("2030-10-31", 100_00)


def test_a_transaction_outside_a_statements_list_is_counted_never_read(tmp_path):
    stray = statement(trn(), tail="</OFX>\n").replace("</BANKTRANLIST>", "</BANKTRANLIST>" + trn(date="20301009"))
    txns, stats, _ = read(ofx_file(tmp_path, stray))
    assert rows(txns) == [("2030-10-05", -5_00, "ACME COFFEE", "DEBIT")]
    assert stats.skip_reasons == {"outside-transaction-list": 1}


def test_a_statement_whose_rows_all_sit_outside_a_transaction_list_is_refused(tmp_path):
    """Never zero rows with the file's balance booked"""
    listless = statement("").replace("<BANKTRANLIST><DTSTART>20301001\n<DTEND>20301031\n</BANKTRANLIST>",
                                     trn() + trn(date="20301006", fitid="2"))
    assert listless != statement("")
    assert refusal(ofx_file(tmp_path, listless)) == ofx.NONE_READABLE


def test_a_statement_none_of_whose_transactions_can_be_read_is_refused(tmp_path):
    text = statement(trn(amount="abc") + trn(date="2030-10-06") + trn(amount=None))
    assert refusal(ofx_file(tmp_path, text)) == ofx.NONE_READABLE


def test_a_file_over_the_size_cap_is_refused_before_it_is_read(tmp_path):
    path = tmp_path / "statement.ofx"
    with open(path, "wb") as f:
        f.write(statement(trn()).encode("ascii"))
        f.seek(registry.MAX_FILE_BYTES)
        f.write(b" ")
    assert refusal(path) == registry.TOO_LARGE


def test_tags_nested_far_deeper_than_a_statements_are_refused(tmp_path):
    assert refusal(ofx_file(tmp_path, statement(trn(more="<SAMPLE>" * (ofx.MAX_DEPTH + 1))))) == ofx.TOO_DEEP
    deep = statement(trn(more="<SAMPLE>" * (ofx.MAX_DEPTH - 20)))           # empty tags closed with their row
    assert len(read(ofx_file(tmp_path, deep, "deep.ofx"))[0]) == 1


def test_the_refusals_are_plain_words_that_never_quote_the_file():
    for words in (ofx.NOT_OFX, ofx.NOT_TEXT, ofx.MIXED_TEXT, ofx.SEVERAL_ACCOUNTS, ofx.SEVERAL_PERIODS,
                  ofx.SEVERAL_STATEMENTS, ofx.INVESTMENT, ofx.NO_STATEMENT, ofx.ENDS_EARLY, ofx.TOO_DEEP,
                  ofx.NONE_READABLE):
        assert words and words == words.strip() and words[0].islower() and not words.endswith(".")
        assert not any(c in words for c in "<>&") and "OFXHEADER" not in words


# ------------------------------------------------------------------ the account number never leaves the parser

def test_no_identifier_or_credential_tag_is_a_leaf_whose_text_the_reader_keeps():
    assert {"ACCTID", "BANKID", "BROKERID", "USERID", "USERPASS", "SESSCOOKIE", "CLTCOOKIE"} <= ofx.SECRETS
    assert not ofx.SECRETS & ofx.LEAVES
    assert not any(tag.rpartition(".")[2] in ofx.SECRETS for tag in ofx.LEAVES)
    # an account's number and its bank's id reach the reader only as a digest, to tell one account's statements apart
    # from two accounts'
    assert ofx.IDENTITY <= ofx.SECRETS
    text = (SAMPLES / "bank_sgml.ofx").read_text(encoding="utf-8")
    events = list(ofx._events(text, text.index("<OFX>")))
    assert ("leaf", "ACCTID", digest("000012345678")) in events
    assert not any(s in value for _, _, value in events for s in SECRETS)


def secret_tags_file(tmp_path: Path) -> Path:
    """A statement carrying every identifier and credential tag, some where a reader might take them for a row's"""
    signon = ("<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS><DTSERVER>20301101<USERID>sampleuser0001"
              "<USERPASS>SAMPLEPASS0001<SESSCOOKIE>SAMPLECOOKIE0001<CLTCOOKIE>SAMPLECLIENT0001"
              "<INTU.USERID>sampleuser0001<LANGUAGE>ENG</SONRS></SIGNONMSGSRSV1>")
    to = "<BANKACCTTO><BANKID>000099999<ACCTID>000087654321<ACCTTYPE>SAVINGS</BANKACCTTO>"
    hidden = ("<NAME><ACCTID>000012345678<MEMO></MEMO><BROKERID>broker.sample.example<USERID></USERID>"
              "<CLTCOOKIE>SAMPLECLIENT0001</CLTCOOKIE>")
    text = statement(trn(fitid="F1", more=to) + trn(date="20301006", name=None, more=hidden))
    return ofx_file(tmp_path, text.replace("<OFX>", "<OFX>" + signon, 1), "secret-tags.ofx")


def test_no_account_number_bank_id_or_credential_leaves_the_parser(tmp_path, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    secret = secret_tags_file(tmp_path)
    whole = (SAMPLES / "bank_sgml.ofx").read_text(encoding="utf-8")
    cut = ofx_file(tmp_path, whole[: whole.index("<NAME>STARBUCKS")], "cut.ofx")
    periods = statement(trn()).replace("</BANKMSGSRSV1>", _second_statement(
        "<BANKACCTFROM><BANKID>000099999<ACCTID>000012345678</BANKACCTFROM>") + "</BANKMSGSRSV1>")
    files = [*sorted(SAMPLES.iterdir()), secret, cut,
             ofx_file(tmp_path, statement(trn(amount="000012345678x")), "amount.ofx"),
             ofx_file(tmp_path, statement(trn(name="X <?pi<ACCTID>000012345678")), "declaration.ofx"),
             ofx_file(tmp_path, statement(trn(name="X <![CDATA[<ACCTID>000012345678]]>")), "cdata.ofx"),
             ofx_file(tmp_path, statement(trn().replace("</STMTTRN>", "<ACCTID>000012345678")), "lax.ofx"),
             ofx_file(tmp_path, periods, "periods.ofx")]
    texts = "".join(p.read_text(encoding="utf-8") for p in files)
    assert all(s in texts for s in SECRETS)                                 # the files do carry every one of them
    seen: list[str] = []
    refused = read_any = 0
    for path in files:
        try:
            result = ofx.parse(path, ACCOUNT)
        except NotThisFormat as e:
            refused += 1
            seen += [str(e), repr(e), "".join(traceback.format_exception(e))]
        else:
            read_any += 1
            seen += list(strings(result))
    assert refused >= 4 and read_any >= 8
    out = capsys.readouterr()
    seen += [out.out, out.err, caplog.text]
    for s in SECRETS:
        assert not any(s in text for text in seen), s
    # the identifiers' tags themselves read as nothing: the rows are the transactions'
    txns, _, _ = read(secret)
    assert rows(txns) == [("2030-10-05", -5_00, "ACME COFFEE", "DEBIT"), ("2030-10-06", -5_00, "", "DEBIT")]


def test_no_account_number_reaches_the_store_the_exports_or_the_output_of_an_ingest(tmp_path, monkeypatch, capsys,
                                                                                      caplog):
    home = tmp_path / "home"
    shutil.copytree(EXAMPLE, home)
    for csv_file in (home / "imports").glob("*.csv"):
        csv_file.unlink()
    accounts_path = home / "config" / "accounts.yaml"
    accounts = yaml.safe_load(accounts_path.read_text(encoding="utf-8"))
    accounts["accounts"][ACCOUNT].update(parser="ofx", glob="*.*fx")
    accounts_path.write_text(yaml.safe_dump(accounts, sort_keys=False), encoding="utf-8")
    for name in ("bank_sgml.ofx", "two_statements.ofx"):
        shutil.copy(SAMPLES / name, home / "imports" / name)
    shutil.copy(secret_tags_file(tmp_path), home / "imports" / "secret-tags.ofx")
    monkeypatch.setenv("FF_HOME", str(home))
    caplog.set_level(logging.DEBUG)
    assert pipeline.run() == 0
    written = [p for p in sorted(home.rglob("*")) if p.is_file() and "imports" not in p.relative_to(home).parts]
    names = {p.name for p in written}
    assert {"finance.db", "transactions.json", "quality_report.json"} <= names
    exported = (home / "data" / "exports" / "transactions.json").read_text(encoding="utf-8")
    assert "STARBUCKS STORE 00000 SAMPLETOWN ST" in exported                 # the rows are there
    quality = (home / "data" / "exports" / "quality_report.json").read_text(encoding="utf-8")
    assert ofx.SEVERAL_ACCOUNTS in quality                                  # and the refused file's check
    out = capsys.readouterr()
    for s in SECRETS:
        assert not any(s.encode() in p.read_bytes() for p in written), s
        assert s not in out.out + out.err + caplog.text, s


# ------------------------------------------------------------------ in the pipeline

def _household(tmp_path: Path, kind: str, files: dict | None = None) -> Config:
    """One account of `kind` read from tmp_path/src: its OFX files `sample-*.ofx`, or the source keys `files`"""
    d = tmp_path / "cfg"
    d.mkdir()
    (tmp_path / "src").mkdir()
    account = {"name": "Sample Account", "institution": "Sample Bank", "owner": "Alex", "kind": kind,
               **(files or {"parser": "ofx", "glob": "sample-*.ofx"})}
    (d / "accounts.yaml").write_text(yaml.safe_dump(
        {"source_dir": "src", "simplefin": {"store_dir": "simplefin"}, "accounts": {ACCOUNT: account}}))
    write_household(d)
    return Config(d, source_dir=str(tmp_path / "src"))


def _ingested(tmp_path: Path, cfg: Config):
    p = isolated_pipeline(tmp_path / "state", [], cfg=cfg, windows={})
    p.parse_transactions()
    return p


def _checks(p, prefix: str) -> list[tuple[str, str]]:
    return [(c.name, c.status) for c in p.checks if c.name.startswith(prefix)]


def _conflict(p) -> str | None:
    """The detail of the account's warning that a bank transaction id names two different rows, None without one"""
    found = [c for c in p.checks if c.name == f"import.ofx_conflict.{ACCOUNT}"]
    assert all(c.status == "warn" for c in found) and len(found) <= 1
    return found[0].detail if found else None


def _shared(n: int) -> str:
    return (f"{n} of the bank's transaction ids in this account's statement files {'names' if n == 1 else 'each name'} "
            "more than one row (another date, amount or description). Every such row is kept, so a transaction the "
            "bank corrected in a later download may be counted twice: look at those rows on this account's page, and "
            "keep the stale one out of spending and income with an override that sets its type to transfer "
            "(overrides.yaml).")


def test_ids_given_to_different_rows_raise_one_warning_for_the_account_with_their_count(tmp_path):
    cfg = _household(tmp_path, "checking")
    (tmp_path / "src" / "sample-1.ofx").write_text(statement(trn(fitid="F1") + trn(fitid="F1", amount="-6.00")))
    (tmp_path / "src" / "sample-2.ofx").write_text(statement(
        trn(date="20301105", fitid="F1") + trn(date="20301106", fitid="F1") + trn(date="20301107", fitid="F9")
        + trn(date="20301108", fitid="F9", name="SAMPLE GROCER")))
    p = _ingested(tmp_path, cfg)
    assert _checks(p, "import.") == [(f"import.ofx_conflict.{ACCOUNT}", "warn")]
    assert _conflict(p) == _shared(2)                                          # F1 across both files, and F9
    assert len(p.txns) == 6                                                  # every row kept
    assert not any(s in _conflict(p) for s in ("F1", "F9", "sample-1", "sample-2", ".ofx"))


def test_one_id_given_to_different_rows_is_one_in_the_words(tmp_path):
    cfg = _household(tmp_path, "checking")
    (tmp_path / "src" / "sample-1.ofx").write_text(statement(trn(fitid="F1") + trn(fitid="F1", amount="-6.00")))
    assert _conflict(_ingested(tmp_path, cfg)) == _shared(1)
    assert _shared(1).startswith("1 of the bank's transaction ids in this account's statement files names more than ")


def test_a_correction_in_a_later_download_is_found_across_files(tmp_path):
    """The bank corrects a transaction it sent before: the later download names it by the same id"""
    cfg = _household(tmp_path, "checking")
    (tmp_path / "src" / "sample-1.ofx").write_text(statement(trn(fitid="F1", amount="-12.50")))
    (tmp_path / "src" / "sample-2.ofx").write_text(statement(trn(fitid="F1", amount="-13.50", date="20301006")))
    p = _ingested(tmp_path, cfg)
    assert _conflict(p) == _shared(1)
    assert sorted(t.amount_cents for t in p.txns) == [-13_50, -12_50]


def test_the_same_pair_in_two_overlapping_downloads_counts_once(tmp_path):
    cfg = _household(tmp_path, "checking")
    pair = statement(trn(fitid="F1") + trn(fitid="F1", amount="-6.00"))
    (tmp_path / "src" / "sample-1.ofx").write_text(pair)
    (tmp_path / "src" / "sample-2.ofx").write_text(pair)
    p = _ingested(tmp_path, cfg)
    assert _conflict(p) == _shared(1)
    assert len(p.txns) == 2                                                  # the second file's copies are repeats


def test_an_exact_repeat_across_downloads_is_no_conflict(tmp_path):
    """The rows' identity stores it once: nothing for the warning"""
    cfg = _household(tmp_path, "checking")
    (tmp_path / "src" / "sample-1.ofx").write_text(statement(trn(fitid="F1") + trn(fitid="F2", date="20301006")))
    (tmp_path / "src" / "sample-2.ofx").write_text(statement(trn(fitid="F2", date="20301006")
                                                             + trn(fitid="F3", date="20301007")))
    p = _ingested(tmp_path, cfg)
    assert _conflict(p) is None and _checks(p, "import.") == []
    assert len(p.txns) == 3


def test_no_warning_when_no_id_names_two_rows(tmp_path):
    cfg = _household(tmp_path, "checking")
    (tmp_path / "src" / "sample-1.ofx").write_text(statement(trn(fitid="F1") + trn(fitid="F1")))
    assert _checks(_ingested(tmp_path, cfg), "import.") == []


def test_a_row_another_source_holds_is_no_conflict(tmp_path):
    """A row left out because another source holds its day is not stored, so it cannot be counted twice"""
    sources = [{"format": "ofx", "glob": "early-*.ofx"}, {"format": "ofx", "glob": "late-*.ofx", "since": "2030-10-15"}]
    cfg = _household(tmp_path, "checking", {"sources": sources})
    (tmp_path / "src" / "early-1.ofx").write_text(statement(trn(fitid="F1", date="20301020")))     # the later's day
    (tmp_path / "src" / "late-1.ofx").write_text(statement(trn(fitid="F1", date="20301020", amount="-6.00")))
    p = _ingested(tmp_path, cfg)
    assert _conflict(p) is None
    assert [t.amount_cents for t in p.txns] == [-6_00]
    # the same two rows both in the later source's days are counted
    (tmp_path / "src" / "early-1.ofx").unlink()
    (tmp_path / "src" / "late-2.ofx").write_text(statement(trn(fitid="F1", date="20301020")))
    assert _conflict(_ingested(tmp_path / "again", cfg)) == _shared(1)


def test_a_checking_account_books_the_files_ledger_balance(tmp_path):
    cfg = _household(tmp_path, "checking")
    shutil.copy(SAMPLES / "bank_sgml.ofx", tmp_path / "src" / "sample-1.ofx")
    p = _ingested(tmp_path, cfg)
    assert p.latest_balances == {ACCOUNT: ("2030-10-31", 4319_37)}
    assert [t.description_raw for t in p.txns][:2] == ["TRADER JOE'S #000", "STARBUCKS STORE 00000 SAMPLETOWN ST"]


def test_a_card_accounts_ledger_balance_is_not_booked(tmp_path):
    cfg = _household(tmp_path, "credit-card")
    shutil.copy(SAMPLES / "card_sgml.qfx", tmp_path / "src" / "sample-1.ofx")
    p = _ingested(tmp_path, cfg)
    assert p.latest_balances == {}
    assert _checks(p, "import.") == [(f"import.balance_ignored.{ACCOUNT}", "info")]
    assert [t.amount_cents for t in p.txns] == [-18_20, 1250_00, 32_99]


def test_a_refused_ofx_file_fails_its_one_check_and_the_ingest_goes_on(tmp_path):
    cfg = _household(tmp_path, "checking")
    shutil.copy(SAMPLES / "bank_sgml.ofx", tmp_path / "src" / "sample-1.ofx")
    shutil.copy(SAMPLES / "two_statements.ofx", tmp_path / "src" / "sample-2.ofx")
    p = _ingested(tmp_path, cfg)
    assert _checks(p, "import.") == [(f"import.{ACCOUNT}.sample-2.ofx", "fail")]
    [check] = [c for c in p.checks if c.name.startswith("import.")]
    assert check.detail.startswith(f"Not read: {ofx.SEVERAL_ACCOUNTS}. ")
    assert len(p.txns) == 4

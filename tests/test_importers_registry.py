# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The importers' registry: a format name -> its parse, which the pipeline's TXN_PARSERS table is built from. The three
native formats register themselves under the names a household's accounts.yaml `parser:` states, and each shipped
preset under its id; a name holds one parse; the table the pipeline reads is a plain copy a test can patch. Synthetic
throughout."""
from __future__ import annotations

import csv
import io

import pytest

from etl import pipeline
from etl.importers import bofa_bank, chase_card, chase_checking, ofx, registry

# the native formats, the OFX reader and the shipped presets (tests/test_csv_presets.py pins which presets ship)
NATIVE = {"chase_checking": chase_checking.parse, "chase_card": chase_card.parse, "bofa_bank": bofa_bank.parse,
          "ofx": ofx.parse, **{name: preset.parse for name, preset in registry.presets().items()}}


def test_the_registry_holds_the_native_formats_under_their_names():
    assert registry.formats() == NATIVE
    assert set(NATIVE) == {"chase_checking", "chase_card", "bofa_bank", "ofx", "bofa_card", "capital_one_card",
                           "discover_card", "schwab_checking", "wells_fargo"}


def test_formats_hands_out_a_copy():
    got = registry.formats()
    got["sample_format"] = chase_card.parse
    del got["chase_card"]
    assert registry.formats() == NATIVE


def test_a_name_holds_one_parse():
    def other(path, account_id):
        raise AssertionError("never called")

    with pytest.raises(ValueError, match="chase_card"):
        registry.register("chase_card", other)
    registry.register("chase_card", chase_card.parse)      # the same parse again changes nothing
    assert registry.formats() == NATIVE


def test_the_pipeline_table_is_a_plain_copy_of_the_registry():
    assert type(pipeline.TXN_PARSERS) is dict
    assert pipeline.TXN_PARSERS == registry.formats()


def test_a_patch_of_the_pipeline_table_leaves_the_registry_alone(monkeypatch):
    def sample(path, account_id):
        raise AssertionError("never called")

    monkeypatch.setitem(pipeline.TXN_PARSERS, "chase_card", sample)
    monkeypatch.setitem(pipeline.TXN_PARSERS, "sample_format", sample)
    assert registry.formats() == NATIVE


@pytest.mark.parametrize(("error", "reason"), [
    (registry.NotThisFormat("its first line is not the header this format starts with"),
     "its first line is not the header this format starts with"),
    (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
     "its bytes are not UTF-8 text, the encoding this format reads"),
    (FileNotFoundError(2, "No such file or directory", "/elsewhere/x.csv"), "it could not be opened"),
    (ValueError("unparseable date '13/45/2030' in -12.00"), "a line in it is not laid out the way this format expects"),
    (KeyError("Amount"), "a line in it is not laid out the way this format expects"),
])
def test_a_refusal_says_what_the_file_looked_like_in_plain_words(error, reason):
    """The words never repeat the exception's own text: it can hold a path outside the statements folder, a line of the
    file or an amount."""
    assert registry.refusal(error) == reason


def test_a_csv_error_is_a_file_that_is_not_csv_text():
    with pytest.raises(csv.Error) as caught:
        list(csv.reader(io.StringIO("a," + "x" * (csv.field_size_limit() + 1) + "\n")))
    assert registry.refusal(caught.value) == "it is not CSV text this format can read"


def test_the_sniff_decodes_a_whole_file_to_its_end_and_a_head_only_as_a_head():
    """A file whose last byte begins a UTF-8 character and ends none (a Windows-1252 E acute): the whole file is no
    UTF-8, so the sniff reads it in Windows-1252; the head of a longer file may stop inside a character, so there UTF-8
    still reads it"""
    data = b"03/01/2030,SAMPLE CAF\xc9"
    assert registry.sniff(data, True, registry.DEFAULT_ENCODINGS) == (
        ["cp1252"], "03/01/2030,SAMPLE CAF\N{LATIN CAPITAL LETTER E WITH ACUTE}")
    assert registry.sniff(data, False, registry.DEFAULT_ENCODINGS) == (["utf-8-sig"], "03/01/2030,SAMPLE CAF")


def test_the_sniff_takes_a_later_encoding_only_when_it_reads_the_very_same_text_and_never_text_with_a_nul():
    ascii_text = b"Date,Description,Amount\n"
    assert registry.sniff(ascii_text, True, ["utf-8-sig", "cp1252", "utf-8-sig"]) == (
        ["utf-8-sig", "cp1252"], ascii_text.decode())
    assert registry.sniff("CAF\N{LATIN CAPITAL LETTER E WITH ACUTE}".encode(), True, registry.DEFAULT_ENCODINGS)[0] == [
        "utf-8-sig"]
    assert registry.sniff("Date".encode("utf-16-le"), True, registry.DEFAULT_ENCODINGS) == ([], None)
    assert registry.sniff(b"\xff\xfeD\x00", True, [*registry.DEFAULT_ENCODINGS, "utf-16"]) == (["utf-16"], "D")

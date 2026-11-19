# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The documentation's pictures (docs/images/*.png): each is a PNG of at most 1 MiB that carries image data and colour
facts only, so no text, time or camera chunk travels with it (a screenshot tool's software name, a capture time)."""
from __future__ import annotations

import pytest
from conftest import ROOT

IMAGES = ROOT / "docs" / "images"
MAX_BYTES = 1024 * 1024
SIGNATURE = b"\x89PNG\r\n\x1a\n"
# the chunks that describe the pixels alone; anything else (tEXt, zTXt, iTXt, tIME, eXIf, iCCP's profile name...) is out
IMAGE_CHUNKS = frozenset({"IHDR", "PLTE", "tRNS", "sRGB", "gAMA", "cHRM", "pHYs", "IDAT", "IEND"})
# the pictures the README shows
PICTURES = ("home.png", "income-statement-dark.png", "second-house.png", "home-phone.png", "settings-ai-dark.png")


def chunk_kinds(data: bytes) -> list[str]:
    """The chunk types of a PNG, in order; ValueError when it is not one or runs past its end."""
    if data[:8] != SIGNATURE:
        raise ValueError("not a PNG")
    pos, kinds = 8, []
    while pos < len(data):
        if pos + 12 > len(data):
            raise ValueError("a chunk runs past the end")
        length = int.from_bytes(data[pos:pos + 4], "big")
        kinds.append(data[pos + 4:pos + 8].decode("latin-1"))
        pos += 12 + length
    if pos != len(data) or kinds[-1:] != ["IEND"]:
        raise ValueError("no IEND at the end")
    return kinds


def picture_problems(name: str, data: bytes) -> list[str]:
    """What keeps a file from being one of the pictures: over the size cap, not a PNG, or a chunk other than image
    data and colour facts."""
    problems = []
    if len(data) > MAX_BYTES:
        problems.append(f"{name}: {len(data)} bytes, over {MAX_BYTES}")
    try:
        kinds = chunk_kinds(data)
    except ValueError as e:
        return [*problems, f"{name}: {e}"]
    if kinds[0] != "IHDR" or "IDAT" not in kinds:
        problems.append(f"{name}: no IHDR first or no IDAT")
    if not set(kinds) <= IMAGE_CHUNKS:
        problems.append(f"{name}: {sorted(set(kinds) - IMAGE_CHUNKS)}")
    return problems


def test_the_readme_pictures_are_there_and_nothing_else_is():
    assert sorted(p.name for p in IMAGES.iterdir()) == sorted(PICTURES)


@pytest.mark.parametrize("name", PICTURES)
def test_each_picture_is_a_png_of_at_most_one_mebibyte_with_image_chunks_only(name):
    assert picture_problems(name, (IMAGES / name).read_bytes()) == []


def test_the_check_refuses_a_text_chunk_and_a_truncated_file():
    data = (IMAGES / PICTURES[0]).read_bytes()
    body = b"Software\x00a screenshot tool"
    text = len(body).to_bytes(4, "big") + b"tEXt" + body + b"\x00\x00\x00\x00"
    with_text = data[:33] + text + data[33:]                     # after the signature and IHDR
    assert "tEXt" in chunk_kinds(with_text) and not set(chunk_kinds(with_text)) <= IMAGE_CHUNKS
    assert picture_problems("with-text.png", with_text) == ["with-text.png: ['tEXt']"]
    with pytest.raises(ValueError):
        chunk_kinds(data[:-6])
    assert picture_problems("cut.png", data[:-6]) != []


def test_the_check_refuses_a_picture_over_one_mebibyte():
    data = (IMAGES / PICTURES[0]).read_bytes()
    body = bytes(MAX_BYTES)
    big = data[:-12] + len(body).to_bytes(4, "big") + b"IDAT" + body + b"\x00\x00\x00\x00" + data[-12:]
    assert chunk_kinds(big)[-2:] == ["IDAT", "IEND"]               # a well-formed PNG, only too large
    assert picture_problems("big.png", big) == [f"big.png: {len(big)} bytes, over {MAX_BYTES}"]

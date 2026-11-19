#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Render the app icon PNGs from deploy/public/icons/icon.svg.

The host has no image toolchain on purpose, so rasterising happens in the
Playwright image (pulled if absent) — headless Chromium renders the SVG exactly
as Safari will. Run after editing icon.svg, then commit the PNGs:

    python3 deploy/render_icons.py

Nothing in CI or the Docker build calls this; the PNGs are committed artefacts.
They live under deploy/public/ rather than a Vite public dir because publicDir is
pointed at the ETL's JSON exports, and because the sign-in page shows them before
any session: Dockerfile.web copies deploy/public/ to /srv/public/, the one
directory the web server serves without a session.

Two Chromium quirks shaped this script, both of which silently produced
half-transparent icons before they were pinned down:

  * A window smaller than roughly 400px only rasterises part of its canvas. The
    artwork is therefore drawn at its true size in a large window and the
    top-left square is cropped out here, rather than sizing the window to match.
  * The screenshot always comes back RGBA. Since the artwork is full-bleed and
    verified opaque, it is re-encoded as RGB — iOS renders any transparent
    region of an apple-touch-icon as solid black.
"""

from __future__ import annotations

import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import zlib

IMAGE = "mcr.microsoft.com/playwright:v1.49.0-jammy"
ICONS = pathlib.Path(__file__).resolve().parent / "public" / "icons"
SOURCE = ICONS / "icon.svg"

# Comfortably above both the largest icon and Chromium's small-window threshold.
WINDOW = 1024

# name -> pixel size. apple-touch-icon is the iOS home-screen tile; 192/512 back
# the web manifest; 32/16 are the browser-tab fallbacks for the SVG favicon.
TARGETS = {
    "apple-touch-icon": 180,
    "icon-192": 192,
    "icon-512": 512,
    "favicon-32": 32,
    "favicon-16": 16,
}

PAGE = """<!doctype html><meta charset="utf-8">
<style>
  html, body {{ margin: 0; padding: 0; background: transparent; }}
  #frame {{ width: {size}px; height: {size}px; }}
  #frame svg {{ display: block; width: 100%; height: 100%; }}
</style>
<div id="frame">{svg}</div>
"""


def decode_rgba(path: pathlib.Path) -> tuple[int, int, list[bytes]]:
    """Decode an 8-bit RGBA PNG to unfiltered scanlines. No Pillow on the host."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        sys.exit(f"{path.name}: not a PNG")

    pos, idat = 8, b""
    width = height = depth = ctype = None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        if tag == b"IHDR":
            width, height, depth, ctype = struct.unpack(">IIBB", data[pos + 8:pos + 18])
        elif tag == b"IDAT":
            idat += data[pos + 8:pos + 8 + length]
        pos += 12 + length

    if (depth, ctype) != (8, 6):
        sys.exit(f"{path.name}: expected 8-bit RGBA, got depth={depth} colour-type={ctype}")

    raw = zlib.decompress(idat)
    stride, prev, p, rows = width * 4, bytearray(width * 4), 0, []
    for _ in range(height):
        f = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        for i in range(stride):
            a = line[i - 4] if i >= 4 else 0
            b = prev[i]
            c = prev[i - 4] if i >= 4 else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        rows.append(bytes(line))
        prev = line
    return width, height, rows


def write_rgb_png(path: pathlib.Path, size: int, rgba_rows: list[bytes]) -> None:
    """Write the top-left size x size square as an opaque RGB PNG."""
    def chunk(tag: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + tag + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
        )

    raw = bytearray()
    for y in range(size):
        row = rgba_rows[y]
        raw.append(0)  # filter: none
        for x in range(size):
            raw += row[x * 4:x * 4 + 3]

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def render(work: pathlib.Path, name: str, size: int, svg: str) -> None:
    # Rasterise the vector at its true target size — sharper than rendering big
    # and downscaling, which is what an image library would have done.
    (work / "page.html").write_text(PAGE.format(size=size, svg=svg))
    shot = work / "shot.png"
    shot.unlink(missing_ok=True)

    chrome = (
        "exec $(ls -d /ms-playwright/chromium-*/chrome-linux/chrome | head -1) "
        "--headless=new --no-sandbox --disable-gpu --hide-scrollbars "
        "--default-background-color=00000000 --user-data-dir=/w/.chrome "
        "--run-all-compositor-stages-before-draw "
        f"--force-device-scale-factor=1 --window-size={WINDOW},{WINDOW} "
        "--virtual-time-budget=4000 --screenshot=/w/shot.png file:///w/page.html"
    )
    subprocess.run(
        ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
         "-v", f"{work}:/w", "-w", "/w", IMAGE, "sh", "-c", chrome],
        check=True,
        capture_output=True,
    )

    width, height, rows = decode_rgba(shot)
    if width < size or height < size:
        sys.exit(f"{name}: screenshot {width}x{height} is smaller than the {size}px icon")

    transparent = sum(
        1 for y in range(size) for x in range(size) if rows[y][x * 4 + 3] != 255
    )
    if transparent:
        sys.exit(
            f"{name}: {transparent} of {size * size} pixels are not fully opaque. "
            "The artwork is full-bleed, so Chromium clipped the raster."
        )

    out = ICONS / f"{name}.png"
    write_rgb_png(out, size, rows)
    print(f"{name}.png  {size}x{size}  {out.stat().st_size:>6} bytes")


def main() -> None:
    if not SOURCE.exists():
        sys.exit(f"missing source artwork: {SOURCE}")

    svg = SOURCE.read_text()
    with tempfile.TemporaryDirectory() as tmp:
        for name, size in TARGETS.items():
            render(pathlib.Path(tmp), name, size, svg)


if __name__ == "__main__":
    main()

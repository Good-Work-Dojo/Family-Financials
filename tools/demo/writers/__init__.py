# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Writers of the statement-file layouts the ETL parses. Each module has `write(path, account, ledger, first, last)`
returning the number of rows written; a pending row is never written. These helpers format the shared field grammar."""
from __future__ import annotations

from datetime import date
from pathlib import Path


def check_range(first: date, last: date) -> None:
    if last < first:
        raise ValueError(f"the range ends {last} before it starts {first}")


def us_date(day: date) -> str:
    """MM/DD/YYYY, zero-padded."""
    return f"{day.month:02d}/{day.day:02d}/{day.year:04d}"


def plain(cents: int) -> str:
    """-20.00: no thousands separator."""
    return f"{'-' if cents < 0 else ''}{abs(cents) // 100}.{abs(cents) % 100:02d}"


def grouped(cents: int) -> str:
    """-1,234.56: thousands separators."""
    return f"{'-' if cents < 0 else ''}{abs(cents) // 100:,}.{abs(cents) % 100:02d}"


def field(text: str) -> str:
    """A CSV field, quoted only when it has to be."""
    if any(c in text for c in ',"\n\r'):
        return '"' + text.replace('"', '""') + '"'
    return text


def write_lines(path: Path, lines: list[str]) -> None:
    """The lines, newline-terminated, as UTF-8; missing parent directories are made."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("".join(line + "\n" for line in lines))

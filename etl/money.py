# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Money parsing and arithmetic. All amounts are integer cents internally."""
from __future__ import annotations

import re

_CLEAN_RE = re.compile(r"[$,+%\s]")


def parse_cents(raw: str | float | int | None) -> int | None:
    """Parse a money string like '$1,234.56', '+$1,234.56', '(58.11)', '-58.11' to cents.

    Returns None for empty/blank values. Raises ValueError on garbage.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return round(raw * 100)
    s = raw.strip()
    if not s or s in {"--", "n/a", "N/A"}:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    s = _CLEAN_RE.sub("", s)
    if not s or s == "-":
        return None
    if s.startswith("-"):
        negative = True
        s = s[1:]
    if not re.fullmatch(r"\d*(\.\d+)?", s) or s == ".":
        raise ValueError(f"unparseable money value: {raw!r}")
    whole, _, frac = s.partition(".")
    # string math up to 2 decimals; round half-up on the third digit
    cents = int(whole or "0") * 100 + int((frac + "00")[:2])
    if len(frac) > 2 and frac[2] >= "5":
        cents += 1
    return -cents if negative else cents


def fmt(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}${c // 100:,}.{c % 100:02d}"

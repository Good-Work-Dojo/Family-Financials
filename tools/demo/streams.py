# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Named random streams. A stream is seeded from its profile and name alone, so adding or removing one behaviour never
changes the draws of another, and nothing depends on PYTHONHASHSEED or on how many streams exist."""
from __future__ import annotations

import hashlib
import random


def stream(profile: str, name: str) -> random.Random:
    """The generator for behaviour `name` of `profile`: the same pair always gives the same draws."""
    digest = hashlib.sha256(f"{profile}:{name}".encode()).digest()
    return random.Random(int.from_bytes(digest, "big"))

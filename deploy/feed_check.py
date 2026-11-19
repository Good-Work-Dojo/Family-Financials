# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Whether the household has a bank feed to fetch: exit 0 when anything is at the feed credential's name, NO_FEED when
nothing is. refresh.sh runs it before the fetch: on NO_FEED it skips the fetch and ingests the household's files alone
(no failure streak, no alert about the feed); on 0 it fetches, and a credential that cannot be read, or that other
users may read, fails the fetch with the fetch's own line (etl/simplefin.py read_access_url). Any other exit (this check
itself failing) is read as a feed, so a broken check never stops the fetch without a word.

The name is the one the fetch reads (etl.config Paths.credential_path): FF_SECRETS_DIR's feed/simplefin_access_url,
else FF_HOME's secrets/feed/simplefin_access_url, else credentials/access_url in the developer layout's feed store, which
accounts.yaml `simplefin.store_dir` may move (read leniently: a broken accounts.yaml leaves the default place). It
opens no credential."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

from etl.config import Paths

NO_FEED = 3


def _accounts(paths: Paths) -> dict:
    """What of accounts.yaml the developer layout's feed store place reads: its `simplefin.store_dir` when that is text, else
    nothing (the default place)."""
    try:
        with open(paths.config_dir / "accounts.yaml", encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except (OSError, UnicodeDecodeError, ValueError, OverflowError, yaml.YAMLError):
        return {}
    block = doc.get("simplefin") if isinstance(doc, dict) else None
    store = block.get("store_dir") if isinstance(block, dict) else None
    return {"simplefin": {"store_dir": store}} if isinstance(store, str) and store.strip() else {}


def credential_path(paths: Paths | None = None) -> Path:
    """Where the fetch looks for the feed's credential."""
    paths = paths or Paths()
    return paths.credential_path(paths.store_dir(_accounts(paths)))


def main() -> int:
    return 0 if os.path.lexists(credential_path()) else NO_FEED


if __name__ == "__main__":
    sys.exit(main())

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The agent context files stay inside the budget an agent can actually read.

Some agents (Codex among them) read their own instruction files and then every AGENTS.md from the git root down to
their working directory as one chain, and silently drop whatever lies past a fixed byte limit. This repo's share of
that limit - AGENTS.md + app/AGENTS.md together - is 40,000 bytes, leaving room for what the agent reads first. Past
it, an agent working in app/ loses the END of app/AGENTS.md without a word, which is why both files open with their
hard rules. Detail belongs in docs/design/*.md, reached through the index at the end of each AGENTS.md.

Both files are in the ETL test image, so every pytest run in it, CI's included, runs
this. A missing file FAILS rather than skips: a later .dockerignore line must not switch the budget off unnoticed. Reads
two files; no data."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUDGET = 40_000                      # bytes, AGENTS.md + app/AGENTS.md together
FILES = ("AGENTS.md", "app/AGENTS.md")


def chain_bytes(root: Path = ROOT) -> dict[str, int]:
    """bytes on disk (UTF-8, what Codex counts) of each context file"""
    return {name: (root / name).stat().st_size for name in FILES}


def over_budget(sizes: dict[str, int], budget: int = BUDGET) -> str | None:
    """None when the chain fits, else the message the test fails with"""
    total = sum(sizes.values())
    if total <= budget:
        return None
    parts = ", ".join(f"{k} {v:,}" for k, v in sizes.items())
    return (f"AGENTS.md + app/AGENTS.md = {total:,} bytes ({parts}), over the {budget:,}-byte budget by "
            f"{total - budget:,}: move detail into docs/design/*.md and leave a one-line pointer")


def test_both_context_files_are_in_the_tree():
    missing = [name for name in FILES if not (ROOT / name).is_file()]
    assert missing == [], f"{missing} not found under {ROOT}: the size budget cannot be checked (.dockerignore?)"


def test_the_agent_context_chain_fits_the_budget():
    assert over_budget(chain_bytes()) is None, over_budget(chain_bytes())


def test_the_check_counts_bytes_and_fails_one_byte_over(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "AGENTS.md").write_text("é" * 10, encoding="utf-8")          # 20 bytes, 10 characters
    (tmp_path / "app" / "AGENTS.md").write_text("x" * 30, encoding="utf-8")
    sizes = chain_bytes(tmp_path)
    assert sizes == {"AGENTS.md": 20, "app/AGENTS.md": 30}
    assert over_budget(sizes, budget=50) is None
    msg = over_budget(sizes, budget=49)
    assert msg is not None and "50 bytes" in msg and "by 1:" in msg

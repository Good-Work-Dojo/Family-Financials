# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""tests/conftest.py's isolation helpers: `demo_copy` edits a copy, `isolate` / `isolated_pipeline` move every
state path the pipeline reads or writes into the test's tmp dir, and `state_changes` (behind the session-wide
`_repo_state_untouched` guard) reports a write into the repo's data/ or scenarios/ by path. Synthetic rows on the
demo config only."""
from __future__ import annotations

import json
import os

from conftest import (
    DEMO_CONFIG_DIR,
    DEMO_TREE,
    DEMO_TREE_DIRS,
    ROOT,
    answer,
    demo_config,
    demo_copy,
    isolate,
    isolated_pipeline,
    make_txn,
    state_changes,
    state_fingerprint,
)

from etl.config import Config
from tools.demo import household as hh

WINDOWS = {hh.CARD_ALEX: ("2026-01-01", "2026-12-31")}


def test_demo_copy_edits_the_copy_and_never_the_demo_config(tmp_path):
    before = (DEMO_CONFIG_DIR / "vendor_rules.yaml").read_bytes()
    d = demo_copy(tmp_path, {"vendor_rules.yaml": lambda doc: doc["patterns"].append(
        {"pattern": "SYNTHISOLATE", "vendor": "Synth Isolate"})})
    assert d == tmp_path / "config" and (d / "accounts.yaml").is_file()
    assert "synth isolate" in {e["vendor"].casefold() for e in Config(d).vendor_rules["patterns"]}
    assert (DEMO_CONFIG_DIR / "vendor_rules.yaml").read_bytes() == before
    assert demo_copy(tmp_path / "plain").joinpath("vendor_rules.yaml").read_bytes() == before   # no edits: a copy


def test_isolate_moves_every_state_path_and_writes_only_what_it_is_given(tmp_path):
    cfg = isolate(demo_config(), tmp_path / "run")
    paths = (cfg.answers_path, cfg.learned_path, cfg.manual_assets_path, cfg.data_dir, cfg.exports_dir)
    assert all((tmp_path / "run") in p.parents for p in paths)
    assert not any(p.exists() for p in paths)
    row = make_txn(desc="SYNTH ROW", account=hh.CARD_ALEX)
    a = answer("txn", row, {"category": "Dining"}, "2026-07-01T00:00:00Z")
    prior = {"version": 1, "updated_at": "2026-07-01T00:00:00Z", "history": [], "rules": []}
    cfg2 = isolate(demo_config(), tmp_path / "two", answers=[a], answers_updated_at="2026-07-02T00:00:00Z",
                   prior=prior)
    assert json.loads(cfg2.answers_path.read_text("utf-8")) == {"version": 1, "updated_at": "2026-07-02T00:00:00Z",
                                                                "answers": [a]}
    assert json.loads(cfg2.learned_path.read_text("utf-8")) == prior


def test_answer_builds_both_scopes_in_the_stores_shape():
    row = make_txn(desc="SYNTH ROW")
    assert answer("txn", row, {"tags": ["kids"]}, "T") == {
        "key": f"txn:{row.uid}", "scope": "txn", "txn_id": row.uid, "set": {"tags": ["kids"]}, "answered_at": "T"}
    assert answer("txn", "tx_1", {}, "T")["txn_id"] == "tx_1"
    assert answer("vendor", "Synth Co", {"category": "Pets"}, "T") == {
        "key": "vendor:Synth Co", "scope": "vendor", "vendor": "Synth Co", "set": {"category": "Pets"}, "answered_at": "T"}


def test_an_isolated_pipeline_enriches_learns_persists_and_exports_inside_tmp_only(tmp_path):
    """End to end on synthetic rows: three agreeing answers teach a vendor, and persist_learned, persist and
    export all land under tmp_path. The demo config resolves its state inside the shared demo tree, so that tree's
    config/, scenarios/ and data/ (where an unisolated pipeline would write) are byte-for-byte what they were, and so
    are the repo's own state dirs."""
    before = state_fingerprint()
    demo_before = state_fingerprint(DEMO_TREE, DEMO_TREE_DIRS)
    rows = [make_txn(desc="SYNTH ISOLATED SHOP #1234", cents=-2500, account=hh.CARD_ALEX,
                     date=f"2026-03-{1 + i:02d}") for i in range(4)]
    p = isolated_pipeline(tmp_path, rows, cfg=demo_config(), windows=WINDOWS,
                          answers=[answer("txn", rows[i], {"category": "Dining", "subcategory": "Coffee"},
                                          f"2026-07-01T0{i}:00:00Z") for i in range(3)])
    assert p.txns is rows                                                     # the same list: tests read their Txns back
    p.enrich()
    p.acct_file_sums = {hh.CARD_ALEX: -10000}
    p.finalize_quality()
    p.persist_learned()
    p.persist()
    p.export()
    assert p.learned_written and [r["vendor"] for r in json.loads(p.cfg.learned_path.read_text("utf-8"))["rules"]] \
        == ["Synth Isolated Shop"]
    assert (tmp_path / "data" / "finance.db").is_file() and (tmp_path / "data" / "exports" / "manifest.json").is_file()
    assert state_changes(before, state_fingerprint()) == []
    assert state_changes(demo_before, state_fingerprint(DEMO_TREE, DEMO_TREE_DIRS)) == []


def test_state_changes_names_every_kind_of_write_by_path_only(tmp_path):
    root = tmp_path / "repo"
    (root / "scenarios").mkdir(parents=True)
    (root / "scenarios" / "answers.json").write_text("{}")
    before = state_fingerprint(root)
    assert before["data"] is None and state_changes(before, state_fingerprint(root)) == []
    (root / "data" / "exports").mkdir(parents=True)
    (root / "scenarios" / "learned_rules.json").write_text("secret 123")
    assert state_changes(before, state_fingerprint(root)) == ["data/ created", "scenarios/learned_rules.json added"]
    mid = state_fingerprint(root)
    (root / "scenarios" / "answers.json").write_text('{"answers": []}')
    os.utime(root / "scenarios" / "answers.json", ns=(1, 1))
    (root / "scenarios" / "learned_rules.json").unlink()
    got = state_changes(mid, state_fingerprint(root))
    assert got == ["scenarios/answers.json rewritten", "scenarios/learned_rules.json removed"]
    assert not any("secret" in g for g in got)


def test_the_guard_watches_the_repos_own_state_dirs():
    assert set(state_fingerprint()) == {"data", "scenarios"}
    assert state_fingerprint() == state_fingerprint(ROOT)

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""`python -m etl rules preview|overlaps` on a synthetic pipeline: the demo household's config with one generic
keyword rule added, a SimpleFIN snapshot of made-up rows for one account, an empty CSV source folder, no answers (the
pattern of tests/test_simplefin.py). Nothing here reads real data or writes anywhere."""
from __future__ import annotations

import datetime as dt
import json
import sys

import pytest
import yaml
from conftest import (
    DEMO_CONFIG_DIR,
    ROOT,
    demo_config,
    demo_copy,
    isolate,
    make_txn,
    write_household,
)

from etl import rules_cli
from etl.__main__ import main as etl_main
from etl.config import Config
from etl.enrich.splits import compile_split_rules
from etl.models import CATEGORY_SOURCES
from etl.validate import validate
from tools.demo import household as hh
from tools.demo.behaviours import paths

CARD = hh.CARD_ALEX
# a generic keyword rule for a food word, ahead of the demo's own: what the candidates below compete with
CAFE_RULE = {"pattern": r"\bCAFE\b", "category": "Dining"}


def epoch(date_str: str) -> int:
    y, m, d = map(int, date_str.split("-"))
    return int(dt.datetime(y, m, d, 12, tzinfo=dt.timezone.utc).timestamp())


def txn(tid, date, amount, desc):
    return {"id": tid, "posted": epoch(date), "amount": amount, "description": desc}


ROWS = [
    txn("c1", "2026-07-01", "-12.00", "TUESDAY CAFE"),      # the fixture's rules: Dining/- (the generic food rule)
    txn("c2", "2026-08-01", "-13.00", "TUESDAY CAFE"),
    txn("c3", "2026-09-01", "-14.00", "TUESDAY CAFE"),
    txn("c4", "2026-07-05", "-40.00", "ZZZ UNKNOWN SHOP"),          # fallback Cash & Misc/Uncategorized
    txn("c5", "2026-08-05", "-41.00", "ZZZ UNKNOWN SHOP"),
    txn("c6", "2026-07-09", "-20.00", paths.DAYCARE),               # the demo's keyword rule: Kids/Childcare,
                                                                    # untouched by candidates
    txn("c7", "2026-07-20", "500.00", "Payment Thank You-Mobile"),  # transfer rule -> cc-payment -> Internal
]


@pytest.fixture
def cfg(tmp_path):
    cfg = Config(demo_copy(tmp_path, {"category_rules.yaml": lambda doc: doc["keyword_rules"].insert(0, CAFE_RULE)}))
    cfg.source_dir = tmp_path / "src"                    # no CSVs: every CSV account warns, zero rows
    cfg.source_dir.mkdir()
    isolate(cfg, tmp_path)                               # answers.json, learned_rules.json and data/ absent - and
                                                         # learned_rules.json and data/ must still be absent afterwards
    cfg.simplefin_snapshots_dir = tmp_path / "snaps"
    cfg.simplefin_snapshots_dir.mkdir()
    for m in cfg.accounts.values():                      # one mapped account only
        m.pop("simplefin_id", None)
        m.pop("simplefin_id_history", None)
        m.pop("simplefin_cutover", None)
    cfg.accounts[CARD]["simplefin_id"] = "SF-CARD"
    cfg.accounts[CARD]["simplefin_cutover"] = "2026-01-01"
    snap = {"accounts": [{"id": "SF-CARD", "name": "Card", "org": {"name": hh.CARD_ISSUER}, "currency": "USD",
                          "balance": "-100.00", "balance-date": epoch("2026-09-10"),
                          "transactions": ROWS}], "errlist": []}
    (cfg.simplefin_snapshots_dir / "accounts-20260910T000000Z.json").write_text(json.dumps(snap))
    return cfg


def test_parsed_pipeline_reads_the_synthetic_rows_and_writes_nothing(cfg):
    p = rules_cli.parsed_pipeline(cfg)
    assert sorted(t.description_raw for t in p.txns)[:2] == [paths.DAYCARE, "Payment Thank You-Mobile"]
    assert len(p.txns) == 7
    p.enrich()
    by = {t.description_raw: t for t in p.txns}
    assert (by["TUESDAY CAFE"].category, by["TUESDAY CAFE"].subcategory) == ("Dining", None)
    assert by["ZZZ UNKNOWN SHOP"].category_source == "fallback"
    assert (by[paths.DAYCARE].category, by[paths.DAYCARE].subcategory) == ("Kids", "Childcare")
    assert not cfg.data_dir.exists()
    assert not cfg.learned_path.exists()               # enrich() derives but never writes the learned file


def test_internal_rows_carry_internal_source_after_a_synthetic_run(cfg):
    p = rules_cli.parsed_pipeline(cfg)
    p.enrich()
    internal = [t for t in p.txns if t.category == "Internal"]
    assert [t.description_raw for t in internal] == ["Payment Thank You-Mobile"]
    assert all(t.category_source == "internal" for t in internal)
    assert all(t.category_source in CATEGORY_SOURCES for t in p.txns)
    scanned = sorted({t.description_raw for t in p.txns if rules_cli.reaches_keyword_scan(t)})
    assert scanned == [paths.DAYCARE, "TUESDAY CAFE", "ZZZ UNKNOWN SHOP"]


def test_reaches_keyword_scan_excludes_what_an_earlier_stage_already_categorized():
    scanned = {"keyword", "bank_label", "fallback", "answer", "override", None}
    for src in scanned:       # judged by the scan (answers / overrides re-stamp a row afterwards)
        assert rules_cli.reaches_keyword_scan(make_txn(category_source=src)), src
    for src in ("parser", "vendor_rule", "learned", "internal"):
        assert not rules_cli.reaches_keyword_scan(make_txn(category_source=src)), src
    for typ in ("transfer", "cc-payment", "investment", "income", "dividend", "interest", "fee"):
        assert not rules_cli.reaches_keyword_scan(make_txn(type=typ)), typ
    assert not rules_cli.reaches_keyword_scan(make_txn(is_internal=True))


def test_a_split_part_never_reaches_the_keyword_scan_but_its_parent_does():
    """A part copies its parent's description (and carries the "split" stamp, which no earlier stage
    excludes), so only the parent is a row the scan judged"""
    parent = make_txn(desc="SYNTH MART 1", cents=-30000, vendor="Synth Mart", category_source="keyword")
    parent.is_parent = True
    part = make_txn(desc="SYNTH MART 1", cents=-21000, vendor="Synth Mart", category="Groceries",
                    category_source="split", parent_id=parent.uid, split_index=1)
    assert rules_cli.reaches_keyword_scan(parent) is True
    assert rules_cli.reaches_keyword_scan(part) is False


def test_preview_reports_hits_and_matrix_without_descriptors(cfg, tmp_path, capsys):
    cand = tmp_path / "cand.yaml"
    cand.write_text(yaml.safe_dump([
        # legacy shape, no stage: appended last in `default`, wins because nothing earlier matches ZZZ
        {"pattern": "ZZZ UNKNOWN", "category": "Shopping"},
        # structured, stage pre: outranks the generic Dining rule the fixture's rulebook already has
        {"name": "cafe-restaurants", "stage": "pre",
         "all": [{"field": "description", "op": "contains", "value": "TUESDAY CAFE"}],
         "set": {"category": "Dining", "subcategory": "Restaurants"}},
        # the same intent WITHOUT a stage is shadowed by the earlier generic rule: zero hits
        {"pattern": "TUESDAY CAFE", "category": "Dining", "subcategory": "Restaurants"},
    ]))
    assert rules_cli.preview(cfg, cand) == 0
    out = capsys.readouterr().out
    assert "preview: 3 candidate rule(s) over 7 rows" in out
    assert "preview:0  'ZZZ UNKNOWN'  stage=default  effective hits: 2" in out
    assert "cafe-restaurants  <all of 1 conditions>  stage=pre  effective hits: 3" in out
    assert "preview:2  'TUESDAY CAFE'  stage=default  effective hits: 0" in out
    assert "category changes (old -> new): rows" in out
    assert out.index("Dining/- -> Dining/Restaurants: 3") < out.index("Cash & Misc/Uncategorized -> Shopping/-: 2")
    assert "CANTINA" not in out and "2026-07-01" not in out          # aggregates only
    assert not cfg.data_dir.exists()                                 # nothing written
    assert not cfg.learned_path.exists()


def test_preview_counts_owner_conditioned_candidates_like_the_pipeline_does(cfg, tmp_path, capsys):
    """categorizer_for must pass owner_of= as Pipeline.enrich does, or an owner-conditioned
    candidate would change categories in the run yet show 0 hits in the report."""
    owner = cfg.owner_of()[CARD]
    cand = tmp_path / "cand.yaml"
    cand.write_text(yaml.safe_dump([
        {"name": "owned-card-zzz", "stage": "pre",
         "all": [{"field": "owner", "op": "is", "value": owner},
                 {"field": "description", "op": "contains", "value": "ZZZ UNKNOWN"}],
         "set": {"category": "Shopping"}}]))
    assert rules_cli.preview(cfg, cand) == 0
    out = capsys.readouterr().out
    assert "owned-card-zzz  <all of 2 conditions>  stage=pre  effective hits: 2" in out
    assert "Cash & Misc/Uncategorized -> Shopping/-: 2" in out


def test_preview_hits_are_effective_the_familys_override_still_beats_a_candidate(cfg, tmp_path, capsys):
    """preview runs with the family's answers/overrides on (its matrix is the EFFECTIVE change), so a
    row an override re-stamps after the keyword scan is not an effective hit of the candidate."""
    cfg.overrides = [{"match": {"contains": "ZZZ UNKNOWN"}, "set": {"category": "Pets", "subcategory": None}}]
    cand = tmp_path / "cand.yaml"
    cand.write_text(yaml.safe_dump([{"pattern": "ZZZ UNKNOWN", "category": "Shopping"}]))
    assert rules_cli.preview(cfg, cand) == 0
    out = capsys.readouterr().out
    assert "preview:0  'ZZZ UNKNOWN'  stage=default  effective hits: 0" in out
    assert "category changes: none" in out


def test_preview_show_rows_prints_descriptors_and_no_change_is_said(cfg, tmp_path, capsys):
    cand = tmp_path / "cand.yaml"
    cand.write_text(yaml.safe_dump({"keyword_rules": [{"stage": "pre", "pattern": "ZZZ UNKNOWN", "category": "Shopping"}]}))
    assert rules_cli.preview(cfg, cand, show_rows=True) == 0
    out = capsys.readouterr().out
    assert "rows (--show-rows, on-box only):" in out and "ZZZ UNKNOWN SHOP" in out
    assert "2026-07-05 -40.00" in out
    noop = tmp_path / "noop.yaml"
    noop.write_text(yaml.safe_dump([{"pattern": "NEVERMATCHES", "category": "Pets"}]))
    assert rules_cli.preview(cfg, noop) == 0
    out = capsys.readouterr().out
    assert "effective hits: 0" in out and "category changes: none" in out


def test_preview_rejects_bad_candidates_before_touching_the_data(cfg, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump([{"pattern": "X", "category": "Dinning"}]))
    with pytest.raises(ValueError, match=r"^preview\[0\]: category 'Dinning' not in taxonomy"):
        rules_cli.preview(cfg, bad)
    typo = tmp_path / "typo.yaml"           # a typo'd owner would otherwise preview as zero hits
    typo.write_text(yaml.safe_dump([{"all": [{"field": "owner", "op": "is", "value": "Alexx"}],
                                     "set": {"category": "Dining"}}]))
    with pytest.raises(ValueError, match=r"preview\[0\]: unknown owner 'Alexx'"):
        rules_cli.preview(cfg, typo)
    empty = tmp_path / "empty.yaml"
    empty.write_text("[]\n")
    with pytest.raises(ValueError, match="expected a list of rule entries"):
        rules_cli.preview(cfg, empty)
    broken = tmp_path / "broken.yaml"
    broken.write_text("- { pattern: 'X', category: [\n")
    with pytest.raises(ValueError):
        rules_cli.preview(cfg, broken)


def test_overlaps_reports_zero_hit_and_shadowed_rules(cfg, capsys):
    cfg.category_rules = {**cfg.category_rules, "keyword_rules": [
        {"pattern": "CAFE", "category": "Dining"},
        {"name": "tuesday-cafe", "pattern": "TUESDAY CAFE", "category": "Dining", "subcategory": "Restaurants"},
        {"name": "never", "pattern": "NEVERMATCHES", "category": "Pets"},
        {"pattern": "ZZZ UNKNOWN", "category": "Shopping"},
    ]}
    assert rules_cli.overlaps(cfg) == 0
    out = capsys.readouterr().out
    assert "rules overlaps: 4 keyword rules, 6 rows reached the keyword scan" in out
    assert "zero hits (1):\n  never  'NEVERMATCHES'\n" in out
    assert "shadowed (1):\n  tuesday-cafe  'TUESDAY CAFE'  -> always beaten by keyword_rules:0 (3 rows)" in out
    assert "keyword_rules:3" not in out          # a rule that wins is listed nowhere
    assert "CANTINA" not in out


def test_overlaps_orders_hits_by_stage_not_file_order(cfg, capsys):
    cfg.category_rules = {**cfg.category_rules, "keyword_rules": [
        {"name": "generic-cafe", "pattern": "CAFE", "category": "Dining"},
        {"name": "late-pre-cafe", "stage": "pre", "pattern": "TUESDAY CAFE",
         "category": "Dining", "subcategory": "Restaurants"},      # later in the file, but stage pre
    ]}
    assert rules_cli.overlaps(cfg) == 0
    out = capsys.readouterr().out
    assert "rules overlaps: 2 keyword rules, 6 rows reached the keyword scan" in out
    assert "zero hits (0):" in out
    assert "shadowed (1):\n  generic-cafe  'CAFE'  -> always beaten by late-pre-cafe (3 rows)" in out
    assert "\n  late-pre-cafe  " not in out          # the rule that wins is listed nowhere as a problem


def test_overlaps_ignores_a_retype_only_override_so_a_firing_rule_is_not_zero_hit(cfg, capsys):
    """A retype-only override leaves category_source="keyword" but makes the row internal; computed
    on the effective run that dropped it from the scan and listed the firing rule as dead."""
    cfg.category_rules = {**cfg.category_rules, "keyword_rules": [
        {"name": "zzz-shop", "pattern": "ZZZ UNKNOWN", "category": "Shopping"}]}
    cfg.overrides = [{"match": {"contains": "ZZZ UNKNOWN"}, "set": {"type": "transfer"}}]
    assert rules_cli.overlaps(cfg) == 0
    out = capsys.readouterr().out
    assert "rules overlaps: 1 keyword rules, 6 rows reached the keyword scan" in out
    assert "zero hits (0):" in out and "zzz-shop" not in out
    assert cfg.overrides and cfg.answers_path.name == "answers.json"      # the caller's cfg is left as it was


@pytest.mark.parametrize("restamp", ["override", "answer"])
def test_overlaps_ignores_restamps_so_a_rule_a_vendor_rule_pre_empts_is_listed_dead(cfg, tmp_path, restamp):
    """A vendor rule with a category claims the ZZZ rows before the keyword scan ever sees them, so the
    keyword rule on the same text is dead. An override / answer that re-stamps those rows afterwards
    used to put them back in the scan and hide that."""
    cfg.vendor_rules = {**cfg.vendor_rules, "patterns": [
        *(cfg.vendor_rules.get("patterns") or []),
        {"pattern": "ZZZ UNKNOWN", "vendor": "Zzz Shop", "category": "Shopping"}]}
    cfg.category_rules = {**cfg.category_rules, "keyword_rules": [
        {"name": "dead-zzz", "pattern": "ZZZ UNKNOWN", "category": "Pets"}]}
    if restamp == "override":
        cfg.overrides = [{"match": {"contains": "ZZZ UNKNOWN"}, "set": {"category": "Pets"}}]
    else:
        cfg.answers_path.write_text(json.dumps({"answers": [
            {"scope": "vendor", "vendor": "Zzz Shop", "set": {"category": "Pets"}}]}))
    out = []
    assert rules_cli.overlaps(cfg, out=out.append) == 0
    assert "zero hits (1):" in out and "  dead-zzz  'ZZZ UNKNOWN'" in out
    assert "shadowed (0):" in out


def test_main_wiring_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FF_CONFIG_DIR", str(DEMO_CONFIG_DIR))         # the config the command loads
    monkeypatch.setattr(sys, "argv", ["etl", "rules"])
    assert etl_main() == 2
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump([{"pattern": "X", "category": "Dinning"}]))
    monkeypatch.setattr(sys, "argv", ["etl", "rules", "preview", "--file", str(bad)])
    assert etl_main() == 2
    assert "Dinning" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", ["etl", "rules", "preview", "--file", str(tmp_path / "missing.yaml")])
    assert etl_main() == 2


def test_bare_rulebook_ignores_learned_rules_and_the_scan_filter_excludes_them():
    cfg = demo_config()
    bare = rules_cli.rulebook_only(cfg)
    assert not bare.learned_path.exists() and not bare.answers_path.exists()
    assert bare.learned_path != cfg.learned_path
    t = make_txn(desc="X", cents=-100, category="Dining", category_source="learned")
    assert rules_cli.reaches_keyword_scan(t) is False


def _teach_cafe(cfg):
    """Three txn-scope answers on the CAFE rows: enough for Pipeline.enrich to promote the vendor."""
    cafes = sorted((t for t in rules_cli.parsed_pipeline(cfg).txns if t.description_raw == "TUESDAY CAFE"),
                   key=lambda t: t.date)
    assert len(cafes) == 3
    cfg.answers_path.write_text(json.dumps({"version": 1, "answers": [
        {"key": f"txn:{t.uid}", "scope": "txn", "txn_id": t.uid,
         "set": {"category": "Dining", "subcategory": "Restaurants"}, "answered_at": f"2026-09-0{i + 1}T00:00:00Z"}
        for i, t in enumerate(cafes)]}), "utf-8")


def _stat(path):
    return path.read_bytes(), path.stat().st_mtime_ns


def test_preview_and_overlaps_never_create_or_touch_the_learned_file(cfg, tmp_path, capsys):
    """Both commands call enrich(), which derives learned rules in memory; the write lives in
    Pipeline.persist_learned, which a read-only command must never reach."""
    _teach_cafe(cfg)
    live = rules_cli.parsed_pipeline(cfg)
    live.enrich()
    assert [r["vendor"] for r in live.learned_doc["rules"]] == ["Tuesday Cafe"]     # there IS something to write
    cand = tmp_path / "cand.yaml"
    cand.write_text(yaml.safe_dump([{"pattern": "ZZZ UNKNOWN", "category": "Shopping"}]))
    assert rules_cli.preview(cfg, cand) == 0
    assert rules_cli.overlaps(cfg) == 0
    assert not cfg.learned_path.exists() and not cfg.data_dir.exists()
    # …and a file that is already there stays byte-identical, mtime included
    cfg.learned_path.write_text(json.dumps({"version": 1, "updated_at": "2026-07-01T00:00:00Z", "history": [], "rules": [
        {"vendor": "Synth Elsewhere", "category": "Pets", "subcategory": None, "learned_at": "2026-07-01T00:00:00Z",
         "last_confirmed_at": "2026-07-01T00:00:00Z",
         "evidence": {"scope": "txn", "n_agree": 3, "n_total": 3, "answer_keys": []}}]}), "utf-8")
    before = _stat(cfg.learned_path)
    assert rules_cli.preview(cfg, cand) == 0
    assert rules_cli.overlaps(cfg) == 0
    assert _stat(cfg.learned_path) == before
    capsys.readouterr()


def test_overlaps_scans_the_bare_rulebook_even_when_a_learned_file_exists(cfg, capsys):
    """A learned rule claims its vendor's rows before the keyword scan; overlaps must not let that
    hide the keyword rules those rows would otherwise exercise (6 rows reach the scan, as without it)."""
    cfg.category_rules = {**cfg.category_rules, "keyword_rules": [
        {"name": "cafe-rule", "pattern": "TUESDAY CAFE", "category": "Dining", "subcategory": "Restaurants"}]}
    cfg.learned_path.write_text(json.dumps({"version": 1, "updated_at": "2026-07-01T00:00:00Z", "history": [], "rules": [
        {"vendor": "Tuesday Cafe", "category": "Pets", "subcategory": None,
         "learned_at": "2026-07-01T00:00:00Z", "last_confirmed_at": "2026-07-01T00:00:00Z",
         "evidence": {"scope": "txn", "n_agree": 3, "n_total": 3, "answer_keys": []}}]}), "utf-8")
    out = []
    assert rules_cli.overlaps(cfg, out=out.append) == 0
    assert "rules overlaps: 1 keyword rules, 6 rows reached the keyword scan" in out
    assert "zero hits (0):" in out                    # cafe-rule matched its three rows: the learned rule was off


ZZZ_SPLIT = {"pattern": "ZZZ UNKNOWN", "vendor": "Zzz Shop",
             "split": [{"pct": 60, "category": "Shopping"},
                       {"remainder": True, "category": "Cash & Misc", "subcategory": "Uncategorized"}]}


def _with_split_vendor(cfg):
    """the two ZZZ rows become split purchases (two parts each): 7 purchases, 11 rows after enrich"""
    cfg.vendor_rules = {**cfg.vendor_rules, "patterns": [*(cfg.vendor_rules.get("patterns") or []), ZZZ_SPLIT]}
    cfg.split_rules = compile_split_rules({"patterns": [ZZZ_SPLIT]})        # the compiled view enrichment reads
    p = rules_cli.parsed_pipeline(cfg)
    p.enrich()
    assert (len(p.txns), sum(t.is_parent for t in p.txns), sum(t.parent_id is not None for t in p.txns)) == (11, 2, 4)


def test_overlaps_counts_a_split_purchase_once_its_parent_reaches_the_scan_and_its_parts_do_not(cfg):
    """C6 end to end: a keyword rule on the split vendor's descriptor wins on the two PARENTS; the four parts
    copy that descriptor and would double-count both the scan and the rule's hits if they were admitted"""
    _with_split_vendor(cfg)
    cfg.category_rules = {**cfg.category_rules, "keyword_rules": [
        {"name": "zzz-shop", "pattern": "ZZZ UNKNOWN", "category": "Shopping"},
        {"name": "zzz-late", "pattern": "ZZZ", "category": "Pets"}]}
    out = []
    assert rules_cli.overlaps(cfg, out=out.append) == 0
    assert "rules overlaps: 2 keyword rules, 6 rows reached the keyword scan" in out      # 2 parents + 3 cafe + 1 daycare; parts would make 10
    assert "zero hits (0):" in out
    assert "shadowed (1):" in out and "  zzz-late  'ZZZ'  -> always beaten by zzz-shop (2 rows)" in out     # 2, not 6


def test_preview_counts_purchases_never_the_parts_of_a_split(cfg, tmp_path, capsys):
    """C6 end to end: the candidate re-categorizes the split vendor's two parents (display category), and the
    report counts 7 rows, 2 hits and 2 matrix moves — the 4 parts copy the descriptor but are not counted"""
    _with_split_vendor(cfg)
    cand = tmp_path / "cand.yaml"
    cand.write_text(yaml.safe_dump([{"pattern": "ZZZ UNKNOWN", "category": "Pets"}]))
    assert rules_cli.preview(cfg, cand) == 0
    out = capsys.readouterr().out
    assert "preview: 1 candidate rule(s) over 7 rows" in out
    assert "preview:0  'ZZZ UNKNOWN'  stage=default  effective hits: 2" in out
    assert "Cash & Misc/Uncategorized -> Pets/-: 2" in out
    assert [ln for ln in out.splitlines() if ln.startswith("  ") and " -> " in ln] == \
        ["  Cash & Misc/Uncategorized -> Pets/-: 2"]                           # one matrix cell, no part-driven extras


def test_preview_skips_parts_even_when_one_carries_a_keyword_stamp_and_a_moved_label(cfg, tmp_path, capsys, monkeypatch):
    """The pipeline never stamps a part "keyword" (children copy "split" and the Categorizer ran before them),
    so the parts filter is also pinned directly: a stub pipeline hands preview a part that WOULD be a hit and a
    matrix move if it were counted, beside a purchase that is both"""
    def rows(label_category):
        buy = make_txn(desc="ZZZ UNKNOWN SHOP", cents=-30000, vendor="Zzz Shop", category=label_category,
                       subcategory=None, category_source="keyword", date="2026-07-05")
        part = make_txn(desc="ZZZ UNKNOWN SHOP", cents=-21000, vendor="Zzz Shop", category=label_category,
                        subcategory=None, category_source="keyword", date="2026-07-05",
                        parent_id=buy.uid, split_index=1)
        return [buy, part]

    class Stub:
        def __init__(self, label_category):
            self.txns = rows(label_category)

        def enrich(self):
            pass

    stubs = iter([Stub("Cash & Misc"), Stub("Pets")])               # base, then head (the candidate applied)
    monkeypatch.setattr(rules_cli, "parsed_pipeline", lambda _cfg: next(stubs))
    cand = tmp_path / "cand.yaml"
    cand.write_text(yaml.safe_dump([{"pattern": "ZZZ UNKNOWN", "category": "Pets"}]))
    assert rules_cli.preview(cfg, cand) == 0
    out = capsys.readouterr().out
    assert "preview: 1 candidate rule(s) over 1 rows" in out
    assert "effective hits: 1" in out and "Cash & Misc/- -> Pets/-: 1" in out


def test_preview_names_the_candidates_file_index_for_a_rule_name_a_live_rule_already_has(cfg, tmp_path):
    # the candidates ride behind the live rules for the preview run, where a clash used to surface as
    # `keyword_rules[<index in the combined list>]: duplicate rule name`; the index must be the file's
    cfg.category_rules = {**cfg.category_rules, "keyword_rules": [
        {"name": "zzz-live", "pattern": "ZZZ UNKNOWN", "category": "Shopping"},
        {"pattern": "CAFE", "category": "Dining"}]}
    clash = tmp_path / "clash.yaml"
    clash.write_text(yaml.safe_dump([{"name": "fresh", "pattern": "A", "category": "Pets"},
                                     {"name": "zzz-live", "pattern": "B", "category": "Pets"}]))
    with pytest.raises(ValueError, match=r"^preview\[1\]: duplicate rule name 'zzz-live' \(also a live keyword_rules entry \[0\]\)"):
        rules_cli.preview(cfg, clash)
    autoname = tmp_path / "auto.yaml"
    autoname.write_text(yaml.safe_dump([{"name": "keyword_rules:1", "pattern": "A", "category": "Pets"}]))   # a live rule's auto name
    with pytest.raises(ValueError, match=r"^preview\[0\]: duplicate rule name 'keyword_rules:1' \(also a live keyword_rules entry \[1\]\)"):
        rules_cli.preview(cfg, autoname)
    forged = tmp_path / "forged.yaml"          # the live list has 2 entries, so the nameless candidate 1 is keyword_rules:3
    forged.write_text(yaml.safe_dump([{"name": "keyword_rules:3", "pattern": "A", "category": "Pets"},
                                      {"pattern": "B", "category": "Pets"}]))
    with pytest.raises(ValueError, match=r"^preview\[1\]: duplicate rule name 'keyword_rules:3' \(also candidate \[0\]\)"):
        rules_cli.preview(cfg, forged)
    ok = tmp_path / "ok.yaml"
    ok.write_text(yaml.safe_dump([{"name": "fresh", "pattern": "NEVERMATCHES", "category": "Pets"}]))
    assert rules_cli.preview(cfg, ok, out=lambda *_: None) == 0


def test_preview_refuses_a_candidate_key_the_config_would_refuse(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config"
    write_household(config)
    (config / "accounts.yaml").write_text(yaml.safe_dump({"version": 1, "accounts": {"demo_checking_0000": {
        "name": "Demo Checking", "institution": "Sample Bank", "owner": "Alex", "kind": "checking", "parser": "chase_checking",
        "glob": "demo_checking_0000-*.csv"}}}))
    for name in ("category_rules.yaml", "vendor_rules.yaml"):
        (config / name).write_text((ROOT / "starter" / name).read_text("utf-8"))
    (tmp_path / "imports").mkdir()
    cand = tmp_path / "cand.yaml"
    rule = {"name": "acme", "any": [{"field": "description", "op": "contains", "value": "ACME", "flag": 1}],
            "set": {"category": "Dining", "colour": "red"}}
    cand.write_text(yaml.safe_dump([rule]))
    monkeypatch.setenv("FF_CONFIG_DIR", str(config))
    monkeypatch.setenv("FF_IMPORTS_DIR", str(tmp_path / "imports"))
    monkeypatch.setattr(sys, "argv", ["etl", "rules", "preview", "--file", str(cand)])
    assert etl_main() == 2
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2 and all(line.startswith("category_rules.yaml: keyword_rules[0]") for line in lines)
    # the same entry pasted into the file draws exactly these lines from the validator
    doc = yaml.safe_load((config / "category_rules.yaml").read_text("utf-8"))
    doc["keyword_rules"] = [rule, *doc["keyword_rules"]]
    (config / "category_rules.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    assert sorted(str(p) for p in validate(config, source_dir=str(tmp_path / "imports"))
                  if "keyword_rules[0]" in str(p)) == sorted(lines)

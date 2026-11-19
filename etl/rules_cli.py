# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""`python -m etl rules ...` — offline, read-only rule tooling.

  rules preview --file CANDIDATES.yaml [--show-rows] [--source-dir DIR]
      Parse the live inputs (CSV source folder + SimpleFIN snapshots), enrich once as the
      rulebook stands and once with the candidate entries appended to keyword_rules (each at
      its declared stage, default `default`), then print per-candidate EFFECTIVE hits (rows the
      candidate wins in the keyword scan whose final provenance is still `keyword`: the family's
      answers, learned rules and overrides stay on, so a row one of them claims or re-stamps is
      not counted)
      and the (old category -> new category): n matrix, which is likewise the effective change.
      --show-rows adds date / amount / descriptor per changed row, for eyeballing on the box only.
  rules overlaps [--source-dir DIR]
      For the live rulebook: keyword rules that match no row at all, and rules every one of
      whose matches is taken by an earlier rule (so they can never win), naming the winner.
      Computed on a run with answers, learned rules and overrides switched OFF, so a row's
      final category_source is the Categorizer's own stamp: a retype-only override cannot drop a
      row from the scan, nor can an answer / override put a vendor-rule-claimed row back into it,
      and a learned rule cannot hide the keyword rules its vendor's rows would exercise.

Neither command takes the ingest lock, writes under data/ or scenarios/, or touches the
network. `preview` refuses a candidate with a key category_rules.yaml would refuse (the validator's own lines).
Candidates: a YAML list of rule entries (legacy or structured), or a mapping with a
`keyword_rules:` list. Output is aggregates only (rule names, config pattern text, category
labels, counts) unless --show-rows is given.
"""
from __future__ import annotations

import copy
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from . import validate as validate_mod
from .config import Config
from .enrich import rules as rules_mod
from .enrich.vendors import VendorNormalizer
from .models import INCOME_TYPES, INTERNAL_TYPES, Txn
from .pipeline import Pipeline, categorizer_for


def load_candidates(path: Path) -> list[dict]:
    try:
        doc = yaml.safe_load(path.read_text("utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: not valid YAML ({e})") from e
    entries = doc.get("keyword_rules") if isinstance(doc, dict) else doc
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: expected a list of rule entries (or a keyword_rules: list)")
    return entries


def parsed_pipeline(cfg: Config) -> Pipeline:
    """A Pipeline after the two parse stages — the state Pipeline.enrich expects. Reads only."""
    p = Pipeline(cfg)
    p.parse_transactions()
    p.parse_simplefin()
    return p


def reaches_keyword_scan(t: Txn) -> bool:
    """Rows the keyword scan actually judged: purchases no parser / vendor rule / learned rule /
    type rule had already categorized. Reads the final stamp, so it is only exact on a run with
    answers and overrides off (they re-stamp rows and retype them) — `overlaps` runs that way.
    A split part never does: it copies its parent's description, and the parent is the row the
    scan judged."""
    return (t.parent_id is None
            and t.type not in INTERNAL_TYPES and t.type not in INCOME_TYPES and t.type != "fee"
            and not t.is_internal
            and t.category_source not in ("parser", "vendor_rule", "learned", "internal"))


def _label(t: Txn) -> str:
    return f"{t.category}/{t.subcategory or '-'}"


def _pattern(r: rules_mod.Rule) -> str:
    c = r.conditions[0]
    if len(r.conditions) == 1 and c.field == "description" and c.op == "matches":
        return repr(c.value.pattern)
    return f"<{r.conditions_op} of {len(r.conditions)} conditions>"


def rulebook_only(cfg: Config) -> Config:
    """A shallow copy of `cfg` with the family's answers, learned rules and YAML overrides switched
    off (both files point at nothing, overrides empty). Nothing is written either way."""
    c = copy.copy(cfg)
    c.answers_path = Path("/nonexistent-rules-overlaps/answers.json")   # answers.load: missing -> none
    c.learned_path = Path("/nonexistent-rules-overlaps/learned_rules.json")   # learned.load: missing -> none
    c.overrides = []
    return c


def check_names_free(live: list[dict], candidates: list[dict]) -> None:
    """A candidate's rule name must not already belong to a live keyword rule. The candidates are
    appended to the live list for the preview run, where a clash would surface as `keyword_rules[<index
    in the combined list>]: duplicate rule name`; this names the index in the candidates file instead.
    A nameless candidate is judged by the auto name it gets in the combined list."""
    taken = {r.name: f"a live keyword_rules entry [{i}]"
             for i, r in enumerate(rules_mod.compile_rules(live, source="keyword_rules"))}
    for i, entry in enumerate(candidates):
        name = str(entry["name"]) if entry.get("name") else f"keyword_rules:{len(live) + i}"
        if name in taken:
            raise ValueError(f"preview[{i}]: duplicate rule name {name!r} (also {taken[name]})")
        taken[name] = f"candidate [{i}]"


def preview(cfg: Config, candidates_path: Path, *, show_rows: bool = False, out=print) -> int:
    candidates = load_candidates(candidates_path)
    # the checks the file validator runs on category_rules.yaml keyword_rules, so a candidate the config would refuse
    # is refused here with the same line (the index is the candidates file's)
    problems = [p for i, entry in enumerate(candidates) for p in validate_mod.keyword_rule_problems(i, entry)]
    if problems:
        raise validate_mod.ConfigError(problems)
    compiled = rules_mod.compile_rules(candidates, source="preview")
    check_names_free(list(cfg.category_rules.get("keyword_rules", []) or []), candidates)
    rules_mod.validate_taxonomy(compiled, cfg.taxonomy)
    rules_mod.validate_references(compiled, owners=cfg.owners, accounts=cfg.accounts)

    base = parsed_pipeline(cfg)
    base.enrich()
    before = {t.uid: _label(t) for t in base.txns}

    cfg2 = copy.copy(cfg)                      # same files and snapshots, one more rulebook entry each
    live = list(cfg.category_rules.get("keyword_rules", []) or [])
    cfg2.category_rules = {**cfg.category_rules, "keyword_rules": live + candidates}
    head = parsed_pipeline(cfg2)
    head.enrich()
    cat = categorizer_for(cfg2)
    clean = VendorNormalizer(cfg2.vendor_rules).clean

    # a split part copies its parent's description, so only non-part rows are judged and counted —
    # the parent is the row the keyword scan saw, and a purchase moves in the matrix once
    rows_seen = [t for t in head.txns if t.parent_id is None]
    hits: Counter[int] = Counter()             # candidate position -> effective hits (rows it wins)
    for t in rows_seen:
        if t.category_source == "keyword":
            r = cat.first_hit(t, clean)
            if r is not None and r.index >= len(live):
                hits[r.index - len(live)] += 1
    matrix: Counter[tuple[str, str]] = Counter()
    rows: list[str] = []
    for t in sorted(rows_seen, key=lambda t: (t.date, t.account_id, t.uid)):
        old, new = before.get(t.uid), _label(t)
        if old is not None and old != new:
            matrix[(old, new)] += 1
            rows.append(f"{t.date} {t.amount_cents / 100:.2f} {t.description_raw!r}: {old} -> {new}")

    out(f"preview: {len(compiled)} candidate rule(s) over {len(rows_seen)} rows")
    for i, r in enumerate(compiled):
        out(f"  {r.name}  {_pattern(r)}  stage={r.stage}  effective hits: {hits[i]}")
    if matrix:
        out("category changes (old -> new): rows")
        for (old, new), n in sorted(matrix.items(), key=lambda kv: (-kv[1], kv[0])):
            out(f"  {old} -> {new}: {n}")
    else:
        out("category changes: none")
    if show_rows and rows:
        out("rows (--show-rows, on-box only):")
        for line in rows:
            out("  " + line)
    return 0


def overlaps(cfg: Config, *, out=print) -> int:
    cfg = rulebook_only(cfg)                   # final category_source == the Categorizer's own stamp
    p = parsed_pipeline(cfg)
    p.enrich()
    cat = categorizer_for(cfg)
    clean = VendorNormalizer(cfg.vendor_rules).clean
    scanned = [t for t in p.txns if reaches_keyword_scan(t)]
    matched: Counter[str] = Counter()
    won: Counter[str] = Counter()
    beaten_by: dict[str, Counter[str]] = defaultdict(Counter)
    for t in scanned:
        hits = cat.all_hits(t, clean)
        if not hits:
            continue
        won[hits[0].name] += 1
        for r in hits:
            matched[r.name] += 1
        for r in hits[1:]:
            beaten_by[r.name][hits[0].name] += 1
    zero = [r for r in cat.rules if matched[r.name] == 0]
    shadowed = [r for r in cat.rules if matched[r.name] and won[r.name] == 0]
    out(f"rules overlaps: {len(cat.rules)} keyword rules, {len(scanned)} rows reached the keyword scan")
    out(f"zero hits ({len(zero)}):")
    for r in zero:
        out(f"  {r.name}  {_pattern(r)}")
    out(f"shadowed ({len(shadowed)}):")
    for r in shadowed:
        by, n = beaten_by[r.name].most_common(1)[0]
        out(f"  {r.name}  {_pattern(r)}  -> always beaten by {by} ({n} rows)")
    return 0

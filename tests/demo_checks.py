# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Every comparison between the exports of an ingested demo tree and the truth its generator wrote beside it
(tools/demo, truth.json). One function per section of truth, named after it, returns one line per difference, each
starting with the section's name; `mismatches` is all of them in order. Set-like sections report a difference in both
directions: something exported that truth lacks, and something truth declares that is not exported.

A row is identified by its uid, which the engine derives from its account, date, amount and description, so those four
are compared through the uid and not field by field.

Only the export files and the truth document are read: no calendar, no profile and no end date is assumed, and a
section truth lacks is compared as empty. The document's `version` must be VERSION, the one these comparisons are
written for (tools/demo/truth.py writes it), else `version` reports it. Everything compared here is invented."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path

# truth's name for a row field -> the transactions.json key it is compared with
FIELDS = (("type", "type"), ("category", "category"), ("subcategory", "subcategory"),
          ("source", "category_source"), ("vendor", "vendor"), ("needs_review", "needs_review"),
          ("internal", "is_internal"), ("earner", "earner"), ("is_parent", "is_parent"), ("parent", "parent_id"))
CLIPPED = re.compile(r"^(\d+) file rows on/after cutover (\S+) dropped")
GAP = re.compile(r"^(\d+) internal rows in (\S+) \(counterparty (\S+)\) have no matching row")
GAP_NET = re.compile(r", net (-?[\d,]+\.\d{2})$")
APPLIED = re.compile(r"^(\d+) transactions adjusted by (\d+) review-inbox answers")
CONFLICTS = re.compile(r"review answers: (.*); remove the override if the answer should win")
LEARNED = re.compile(r"^(\d+) learned vendor rule\(s\) active[^:;]*(?:: ([^;]*))?")
SPLITS = re.compile(r"^(\d+) split rule\(s\): (.+): (\d+) purchase\(s\) split into (\d+) parts$")
VERSION = 1                 # the truth.json version these comparisons understand


def load(exports: Path, name: str) -> dict:
    return json.loads((exports / name).read_text("utf-8"))


def cents(dollars: float | None) -> int | None:
    return None if dollars is None else round(dollars * 100)


def figures(truth: dict, key: str, empty):
    """Truth's figure `key`, or `empty` when truth has no such section (or holds null)."""
    value = (truth.get("figures") or {}).get(key)
    return empty if value is None else value


def _both_ways(section: str, exported, declared, label: Callable[[object], str] = repr) -> list[str]:
    """One line per item exported but not declared and per item declared but not exported (as multisets; a mapping
    counts its keys)."""
    got, want = Counter(list(exported)), Counter(list(declared))
    return ([f"{section}: exported but not in truth: {label(x)}" for x in sorted((got - want).elements(), key=repr)]
            + [f"{section}: in truth but not exported: {label(x)}" for x in sorted((want - got).elements(), key=repr)])


def _differs(section: str, where: str, exported, declared) -> list[str]:
    return [] if exported == declared else [f"{section}: {where}: exported {exported!r}, truth {declared!r}"]


def _checks(exports: Path) -> dict[str, dict]:
    return {c["name"]: c for c in load(exports, "quality_report.json")["checks"]}


# ------------------------------------------------------------------ rows and accounts

def version(exports: Path, truth: dict) -> list[str]:
    """One line when truth is of a version these comparisons are not written for (or states none)."""
    got = truth.get("version")
    return [] if got == VERSION else [f"version: truth.json is version {got!r}; these checks read version {VERSION}"]


def household(exports: Path, truth: dict) -> list[str]:
    """Who the household is (its display name and its time zone) and which optional parts of the app its
    household.yaml switches (assumptions.json `modules` against truth's planted `modules`, none when truth names
    none)."""
    doc, want = load(exports, "assumptions.json"), truth.get("household") or {}
    out = [line for key in ("name", "timezone")
           for line in _differs("household", key, doc["household"].get(key), want.get(key))]
    return out + _differs("household", "modules", doc.get("modules"), (truth.get("planted") or {}).get("modules", {}))


def setup(exports: Path, truth: dict) -> list[str]:
    """What is configured: the accounts, whether one has a feed id, the statement files and the hand-stated values, key
    by key — one line per count that differs, and per key only one side has."""
    got, want = load(exports, "manifest.json").get("setup") or {}, figures(truth, "setup", {})
    return _both_ways("setup", got, want) + [line for key in sorted(set(got) & set(want))
                                             for line in _differs("setup", key, got[key], want[key])]


def rows(exports: Path, truth: dict) -> list[str]:
    """Every row truth declares is exported with every field truth names, and every exported row is declared."""
    stored = {t["id"]: t for t in load(exports, "transactions.json")["transactions"]}
    want = figures(truth, "rows", {})
    out = []
    for uid in sorted(set(stored) | set(want)):
        if uid not in want:
            out.append(f"rows: {uid}: stored but not in truth ({stored[uid]['date']} {stored[uid]['description']!r})")
        elif uid not in stored:
            out.append(f"rows: {uid}: in truth but not stored")
        else:
            out += [f"rows: {uid}: {key}: stored {stored[uid][key]!r}, truth {want[uid][field]!r}"
                    for field, key in FIELDS if stored[uid][key] != want[uid][field]]
    return out


def accounts(exports: Path, truth: dict) -> list[str]:
    """Per account: its money rows (split parents aside) and their sum, and its transaction window."""
    mine: dict[str, list[dict]] = defaultdict(list)
    for t in load(exports, "transactions.json")["transactions"]:
        if not t["is_parent"]:
            mine[t["account"]].append(t)
    windows = {a["id"]: a for a in load(exports, "accounts.json")["accounts"]}
    want = figures(truth, "accounts", {})
    out = _both_ways("accounts", windows, want)
    for account in sorted(set(windows) & set(want)):
        w, got = want[account], mine.get(account, [])
        out += _differs("accounts", f"{account}: rows", len(got), w["rows"])
        out += _differs("accounts", f"{account}: sum_cents", sum(cents(t["amount"]) for t in got), w["sum_cents"])
        out += _differs("accounts", f"{account}: window", (windows[account]["txn_data_start"],
                                                           windows[account]["txn_data_end"]), (w["first"], w["last"]))
    return out


# ------------------------------------------------------------------ cash flow

def months(exports: Path, truth: dict) -> list[str]:
    """Per month: income, spending, investing, spending by category and whether every cash-flow account with a row
    covers it."""
    got = {m["month"]: m for m in load(exports, "cashflow_monthly.json")["months"]}
    want = figures(truth, "months", {})
    out = _both_ways("months", got, want)
    for month in sorted(set(got) & set(want)):
        m, w = got[month], want[month]
        for key in ("income", "spend", "invested"):
            out += _differs("months", f"{month}: {key}", cents(m[key]), w[f"{key}_cents"])
        by_category = {c: cents(v["total"]) for c, v in m["by_category"].items()}
        for category in sorted(set(by_category) | set(w["by_category"])):
            out += _differs("months", f"{month}: by_category: {category}", by_category.get(category),
                            w["by_category"].get(category))
        out += _differs("months", f"{month}: full_coverage", m["full_coverage"], w.get("full_coverage"))
    return out


def coverage_floor(exports: Path, truth: dict) -> list[str]:
    """The first month in coverage, and every month in coverage exactly from it on."""
    doc = load(exports, "cashflow_monthly.json")
    floor = figures(truth, "coverage_floor", None)
    out = _differs("coverage_floor", "floor", doc["coverage_floor"], floor)
    for m in doc["months"]:
        out += _differs("coverage_floor", f"{m['month']}: in_coverage", m["in_coverage"],
                        floor is not None and m["month"] >= floor)
    return out


def income_phases(exports: Path, truth: dict) -> list[str]:
    got = [(p["person"], p["phase"], p["start"], p["end"], p["deposits"], cents(p["total"]))
           for p in load(exports, "assumptions.json")["income_phases"]]
    want = [(p["earner"], p["employer"], p["first"], p["last"], p["deposits"], p["total_cents"])
            for p in figures(truth, "income_phases", [])]
    return _differs("income_phases", "phases", got, want)


# ------------------------------------------------------------------ the seam, pairs, questions and pending rows

def seam(exports: Path, truth: dict) -> list[str]:
    """The file rows each export repeats from an earlier one, and the rows the cutover clips, per account."""
    report = load(exports, "quality_report.json")
    want = figures(truth, "seam", {})
    out = _both_ways("seam: duplicates", report["cross_file_duplicate_uids"].items(),
                     [(f, n) for s in want.values() for f, n in s["duplicates"].items()])
    clipped = []
    for name, check in _checks(exports).items():
        found = CLIPPED.match(check["detail"]) if name.startswith("simplefin.csv_clip.") else None
        if found:
            clipped.append((name.removeprefix("simplefin.csv_clip."), int(found[1]), found[2]))
    return out + _both_ways("seam: clipped", clipped,
                            [(a, s["clipped"], s["cutover"]) for a, s in want.items() if s["clipped"]])


def pairs(exports: Path, truth: dict) -> list[str]:
    """Each transfer group holds two rows, and the groups are truth's pairs (type, outgoing leg, incoming leg); the
    internal rows are as many as truth says."""
    txns = load(exports, "transactions.json")["transactions"]
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in txns:
        if t["transfer_group_id"] is not None:
            groups[t["transfer_group_id"]].append(t)
    out, exported = [], []
    for group, legs in sorted(groups.items()):
        if len(legs) != 2:
            out.append(f"pairs: group {group} holds {len(legs)} rows")
            continue
        a, b = sorted(legs, key=lambda t: t["amount"])
        exported.append((a["type"], a["id"], b["id"]))
    out += _both_ways("pairs", exported, [(p["type"], p["out"], p["into"]) for p in figures(truth, "pairs", [])])
    return out + _differs("pairs", "internal rows", sum(t["is_internal"] for t in txns),
                          figures(truth, "internal_rows", 0))


def open_questions(exports: Path, truth: dict) -> list[str]:
    """The questions' topics, and each gap in the data as (account, hint, rows, net cents)."""
    doc = load(exports, "open_questions.json")
    out = _both_ways("open_questions", [q["topic"] for q in doc["questions"]], figures(truth, "open_questions", []))
    gaps = []
    for g in doc["data_gaps"]:
        head, net = GAP.match(g["question"]), GAP_NET.search(g["context"])
        if not (head and net):
            out.append(f"open_questions: a data gap reads otherwise: {g['question']!r} {g['context']!r}")
            continue
        gaps.append((head[2], head[3], int(head[1]), round(float(net[1].replace(",", "")) * 100)))
    return out + _both_ways("open_questions: data_gaps", gaps, [(g["account"], g["hint"], g["rows"], g["net_cents"])
                                                                for g in figures(truth, "data_gaps", [])])


def pending(exports: Path, truth: dict) -> list[str]:
    """The pending rows each feed snapshot lists, which the engine skips and counts per file."""
    files = [(f["source_file"], f.get("skip_reasons", {}).get("pending", 0))
             for f in load(exports, "quality_report.json")["files"] if f["parser"] == "simplefin"]
    return _both_ways("pending", files, figures(truth, "pending_listed", {}).items())


# ------------------------------------------------------------------ net worth

def snapshots(exports: Path, truth: dict) -> list[str]:
    """Per snapshot date: the totals, the classes, what is missing, whether the mortgage is subtracted, and the rows of
    the hand-stated values (by id: the value, the date of the value it is held at and whether it is carried back)."""
    got = {s["snapshot_date"]: s for s in load(exports, "assets_timeseries.json")["snapshots"]}
    want = {s["date"]: s for s in figures(truth, "snapshots", [])}
    out = _both_ways("snapshots", got, want)
    for day in sorted(set(got) & set(want)):
        g, w = got[day], want[day]
        for key, cents_key in (("total", "total_cents"), ("assets_total", "assets_cents"),
                               ("liabilities_total", "liabilities_cents")):
            out += _differs("snapshots", f"{day}: {key}", cents(g[key]), w[cents_key])
        out += _differs("snapshots", f"{day}: by_class", {c: cents(v) for c, v in g["by_class"].items()},
                        w["by_class"])
        out += _differs("snapshots", f"{day}: missing", g["missing"], w["missing"])
        out += _differs("snapshots", f"{day}: mortgage_included", g["mortgage_included"], w["mortgage_included"])
        manual = sorted(({"id": r["manual_id"], "value_cents": cents(r["value"]), "as_of": r["as_of"],
                          "carried_back": r["carried_back"]} for r in g["rows"] if r["manual_id"] is not None),
                        key=lambda r: r["id"])
        out += _differs("snapshots", f"{day}: manual_rows", manual, w.get("manual_rows", []))
    return out


def manual_assets(exports: Path, truth: dict) -> list[str]:
    """The hand-stated values net worth counts (accounts.json `manual_assets`: the config's entries and the assets
    entered in the app): the same ids in the same order, and per id its name, holder, owner, class, liquidity,
    acquisition day, dated values and origin."""
    got = {a["id"]: {**a, "values": [{"as_of": v["as_of"], "value_cents": cents(v["value"])} for v in a["values"]]}
           for a in load(exports, "accounts.json").get("manual_assets") or []}
    want = {w["id"]: w for w in figures(truth, "manual_assets", [])}
    out = _both_ways("manual_assets", got, want) + _differs("manual_assets", "order", list(got), list(want))
    return out + [line for asset_id in sorted(set(got) & set(want))
                  for key in sorted(set(got[asset_id]) | set(want[asset_id]))
                  for line in _differs("manual_assets", f"{asset_id}: {key}", got[asset_id].get(key),
                                       want[asset_id].get(key))]


def daily_line(exports: Path, truth: dict) -> list[str]:
    """The daily line's last point, its point on each month end that misses nothing and on each day a hand-stated
    value takes a value or is acquired within the accounts' span (truth's daily_points), and the current total (null
    with no snapshot)."""
    doc = load(exports, "networth_daily.json")
    line = {p["date"]: cents(p["networth"]) for p in doc["series"]}
    last = doc["series"][-1] if doc["series"] else None
    out = _differs("daily_line", "last point", {"date": last["date"], "total_cents": cents(last["networth"])}
                   if last else None, figures(truth, "daily_last", None))
    totals = {s["date"]: s["total_cents"] for s in figures(truth, "snapshots", [])}
    for day in figures(truth, "comparable_month_ends", []):
        out += _differs("daily_line", f"{day}", line.get(day), totals.get(day))
    for day, total in sorted(figures(truth, "daily_points", {}).items()):
        out += _differs("daily_line", f"{day}", line.get(day), total)
    out += _differs("daily_line", "current total", cents(doc["current_total"]),
                    figures(truth, "current_total_cents", None))
    newest = figures(truth, "snapshots", [])
    return out + _differs("daily_line", "current snapshot date", doc["current_snapshot_date"],
                          newest[-1]["date"] if newest else None)


def holdings(exports: Path, truth: dict) -> list[str]:
    return _both_ways("holdings", [(h["account_id"], h["symbol"], cents(h["value"]))
                                   for h in load(exports, "holdings.json")["holdings"]],
                      [(h["account"], h["symbol"], h["value_cents"]) for h in figures(truth, "holdings", [])])


def mortgage(exports: Path, truth: dict) -> list[str]:
    """The mortgage block: the lender's balance, the loan's stated terms and the balance at every snapshot."""
    block, want = load(exports, "assumptions.json")["mortgage"], figures(truth, "mortgage", None)
    if block is None or want is None:
        return _differs("mortgage", "block", block, want)
    origination = block["origination"]
    got = {"account": block["account_id"], "balance_cents": cents(block["balance"]),
           "balance_as_of": block["balance_as_of"], "rate_pct": block["rate_pct"],
           "monthly_payment_cents": cents(block["monthly_payment"]),
           "monthly_escrow_cents": cents(block["monthly_escrow"]),
           "escrow_balance_cents": cents(block["escrow_balance"]),
           "origination": {"date": origination["date"], "amount_cents": cents(origination["amount"])}
           if origination else None,
           "term_months": block["term_months"],
           "schedule": [{"date": p["date"], "principal_balance_cents": cents(p["principal_balance"])}
                        for p in block["schedule"]]}
    return [line for key in sorted(set(got) | set(want)) for line in
            _differs("mortgage", key, got.get(key), want.get(key))]


# ------------------------------------------------------------------ the quality report

def ledger_drift(exports: Path, truth: dict) -> list[str]:
    got = [(a["id"], cents(a["ledger_drift"]["drift"])) for a in load(exports, "accounts.json")["accounts"]
           if a["ledger_drift"]]
    return _both_ways("ledger_drift", got, figures(truth, "ledger_drift", {}).items())


def checks(exports: Path, truth: dict) -> list[str]:
    """The checks whose status is not pass, by name and status. A name can repeat (one check per statement file), so
    every exported check is listed, not one per name."""
    got = [(c["name"], c["status"]) for c in load(exports, "quality_report.json")["checks"] if c["status"] != "pass"]
    return _both_ways("checks", got, figures(truth, "checks", {}).items())


# ------------------------------------------------------------------ what the household knows and answered

def recurring(exports: Path, truth: dict) -> list[str]:
    got = [(i["vendor"], i["account_id"], i["cadence"], i["amount_mode"], i["active"], len(i["price_changes"]))
           for i in load(exports, "recurring.json")["items"]]
    want = [(r["vendor"], r["account"], r["cadence"], r["amount_mode"], r["active"], r["price_changes"])
            for r in figures(truth, "recurring", [])]
    return _both_ways("recurring", got, want)


def vendors(exports: Path, truth: dict) -> list[str]:
    """The vendors filed by a learned rule, with the category it files them under."""
    got = [(v["vendor"], v["category"], v["subcategory"], v["category_source"])
           for v in load(exports, "vendors.json")["vendors"] if v["category_source"] == "learned"]
    want = [(vendor, w["category"], w["subcategory"], w["category_source"])
            for vendor, w in figures(truth, "vendors", {}).items()]
    return _both_ways("vendors", got, want)


def answers(exports: Path, truth: dict) -> list[str]:
    """The rows the answers adjust, the answers an override outranks, and the vendors the answers teach."""
    found = _checks(exports)
    want = figures(truth, "answers", {})
    applied = found.get("answers.applied")
    head = APPLIED.match(applied["detail"]) if applied else None
    out = _differs("answers", "applied rows", int(head[1]) if head else 0, want.get("applied_rows", 0))
    conflict = found.get("answers.conflict")
    keys = CONFLICTS.search(conflict["detail"]) if conflict else None
    out += _both_ways("answers: conflicts", keys[1].split(", ") if keys else [], want.get("conflicts", []))
    learned = figures(truth, "learned", [])
    active = found.get("learned_rules.active")
    count = LEARNED.match(active["detail"]) if active else None
    out += _differs("answers", "learned rules", int(count[1]) if count else 0, len(learned))
    listed = [n for n in (count[2] or "").split(", ") if n] if count else []
    names = [n for n in listed if not n.startswith("+")]
    declared = {r["vendor"] for r in learned}
    out += [f"answers: learned rule not in truth: {n!r}" for n in names if n not in declared]
    if len(names) == len(listed):         # a "+N more" tail hides names, so a missing one is only seen without it
        out += [f"answers: learned rule in truth but not named by the check: {n!r}"
                for n in sorted(declared - set(names))]
    return out


def split(exports: Path, truth: dict) -> list[str]:
    """The split rule's vendor and purchases, and each purchase's parts: one per leg, under the leg's category, the
    `pct` leg that percent of the purchase to the nearest cent and the remainder leg what is left."""
    want = figures(truth, "splits", None)
    txns = load(exports, "transactions.json")["transactions"]
    parents = {t["id"]: t for t in txns if t["is_parent"]}
    parts: dict[str, list[dict]] = defaultdict(list)
    for t in txns:
        if t["parent_id"] is not None:
            parts[t["parent_id"]].append(t)
    applied = _checks(exports).get("splits.applied")
    if want is None:
        return (_both_ways("split: parents", parents, []) + _both_ways("split: parts", parts, [])
                + _differs("split", "splits.applied", applied is not None, False))
    out = _both_ways("split: vendors", [t["vendor"] for t in parents.values()], [want["vendor"]] * want["purchases"])
    out += _both_ways("split: parents with parts", parts, parents)
    head = SPLITS.match(applied["detail"]) if applied else None
    out += _differs("split", "splits.applied", (int(head[1]), head[2], int(head[3]), int(head[4])) if head else None,
                    (1, want["vendor"], want["purchases"], want["purchases"] * len(want["legs"])))
    for uid in sorted(set(parents) & set(parts)):
        total, mine = cents(parents[uid]["amount"]), parts[uid]
        out += _both_ways(f"split: {uid}: parts", [(p["category"], p["subcategory"]) for p in mine],
                          [(leg["category"], leg["subcategory"]) for leg in want["legs"]])
        share = {(p["category"], p["subcategory"]): cents(p["amount"]) for p in mine}
        for leg in want["legs"]:
            got = share.get((leg["category"], leg["subcategory"]))
            if got is None:
                continue
            if "pct" in leg and abs(got * 100 - total * leg["pct"]) > 50:
                out.append(f"split: {uid}: {leg['category']}: {got} is not {leg['pct']} percent of {total}")
            if leg.get("remainder") and got != total - sum(c for k, c in share.items()
                                                           if k != (leg["category"], leg["subcategory"])):
                out.append(f"split: {uid}: {leg['category']}: {got} is not what the other parts leave of {total}")
    return out


def ai(exports: Path, truth: dict) -> list[str]:
    """The AI step as a demo household leaves it: off (no key, no consent), so nothing is filed by it and no answer
    is stored; and its preview counts what truth declares — the vendors it would ask about, and how many each reason
    keeps back. A household truth declares no AI vendors for previews none."""
    doc = load(exports, "ai_status.json")
    want = (figures(truth, "ai", {}).get("preview")
            or {"vendors": 0, "withheld": dict.fromkeys(doc["preview"]["withheld"], 0)})
    filed = [t["id"] for t in load(exports, "transactions.json")["transactions"] if t["category_source"] == "ai"]
    return (_differs("ai", "preview", doc["preview"], want)
            + _differs("ai", "enabled, configured, answers stored", (doc["enabled"], doc["configured"],
                                                                     doc["answers_stored"]), (False, False, False))
            + _differs("ai", "applied", doc["applied"], {"vendors": 0, "rows": 0})
            + _both_ways("ai: rows filed by it", filed, []))


SECTIONS = (version, household, setup, rows, accounts, months, coverage_floor, income_phases, seam, pairs, open_questions,
            pending, snapshots, manual_assets, daily_line, holdings, mortgage, ledger_drift, checks, recurring, vendors,
            answers, split, ai)


def mismatches(exports_dir: Path, truth: dict) -> list[str]:
    """Every difference between the exports in `exports_dir` and `truth`, section by section; [] when they agree."""
    return [line for section in SECTIONS for line in section(exports_dir, truth)]

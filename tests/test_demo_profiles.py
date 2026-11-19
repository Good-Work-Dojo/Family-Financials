# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The demo generator's six profiles, each generated to the pinned end and ingested: every one validates, ingests
with no failed check and equals its truth in every section (`demo_checks.mismatches`); the small ones are what they
say; the two profiles seeded as `full` keep its rows. Everything here is invented."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple

import demo_checks
import pytest
import yaml
from conftest import DEMO_END, DEMO_NOW, ROOT, ingest_demo

from etl.__main__ import main as etl_main
from tools.demo import generate, profiles
from tools.demo import household as hh
from tools.demo import plan as plan_mod
from tools.demo.dates import (
    business_day_on_or_before,
    last_business_day,
    month_end,
    shift_months,
)

# the export files the dashboard loads (app/src/data/types.ts EXPORT_FILES)
EXPORT_FILES = sorted(["manifest.json", "transactions.json", "accounts.json", "assets_timeseries.json", "holdings.json",
                       "cashflow_monthly.json", "recurring.json", "quality_report.json", "open_questions.json",
                       "assumptions.json", "balances_timeseries.json", "networth_daily.json", "vendors.json",
                       "ai_status.json"])


class Generated(NamedTuple):
    home: Path          # the tree as the generator wrote it
    exports: Path       # the exports of one ingest of a copy of it
    truth: dict


def generated(tmp_path_factory, name: str, end: date = DEMO_END) -> Generated:
    """The profile generated to `end` and a copy of it ingested at the generator's ingest time that day."""
    home = tmp_path_factory.mktemp(f"{name}-{end}") / "home"
    truth = generate.run(name, end, home)
    ingested = home.parent / "ingested"
    shutil.copytree(home, ingested)
    return Generated(home, ingest_demo(ingested, f"{end.isoformat()}T{generate.INGEST_TIME}"), truth)


@pytest.fixture(scope="module")
def tree(tmp_path_factory, demo_home, demo_exports):
    """A profile generated to the pinned end and ingested, once per module: `full` is the session's own."""
    made = {"full": Generated(demo_home.path, demo_exports.exports, demo_home.truth)}

    def get(name: str) -> Generated:
        if name not in made:
            made[name] = generated(tmp_path_factory, name)
        return made[name]
    return get


def load(exports: Path, name: str) -> dict:
    return json.loads((exports / name).read_text("utf-8"))


# ------------------------------------------------------------------ every profile

def test_the_profiles_are_six_in_order():
    assert profiles.NAMES == ("full", "new", "tiny", "empty", "no-investments", "no-income")
    assert tuple(profiles.PROFILES) == profiles.NAMES
    assert DEMO_NOW == f"{DEMO_END.isoformat()}T{generate.INGEST_TIME}"


@pytest.mark.parametrize("name", profiles.NAMES)
def test_each_profile_generates_validates_and_ingests_clean(tree, name, monkeypatch, capsys):
    t = tree(name)
    capsys.readouterr()                         # the ingest's own summary, when this test ran it
    monkeypatch.setenv("FF_HOME", str(t.home))
    monkeypatch.setattr(sys, "argv", ["etl", "validate"])
    assert etl_main() == 0
    assert capsys.readouterr().out == f"config OK (8 files, {len(t.truth['accounts'])} accounts)\n"
    checks = load(t.exports, "quality_report.json")["checks"]
    assert [c["name"] for c in checks if c["status"] == "fail"] == []
    assert sorted(p.name for p in t.exports.glob("*.json")) == EXPORT_FILES
    assert demo_checks.mismatches(t.exports, t.truth) == []
    # complete figures: every section `full`'s truth has, and the handles of features `full` has too
    full = tree("full").truth
    assert set(t.truth["figures"]) == set(full["figures"])
    assert set(t.truth["planted"]) <= set(full["planted"])
    ids = {x["id"] for x in load(t.exports, "transactions.json")["transactions"]}
    for handle, value in t.truth["planted"].items():
        for v in value if isinstance(value, list) else [value]:
            assert not (isinstance(v, str) and v.startswith("tx_")) or v in ids, handle


# ------------------------------------------------------------------ the small ones

def snapshot_files(home: Path) -> list[Path]:
    return sorted((home / "store" / "simplefin" / "snapshots").glob("accounts-*.json"))


def test_the_small_profiles_are_what_they_say(tree):
    # new: three accounts the feed connected five days ago, nothing else
    new = tree("new")
    end = date.fromisoformat(new.truth["end"])
    assert sorted(new.truth["accounts"]) == sorted([hh.CHECKING, hh.CARD_ALEX, hh.BROKERAGE])
    snaps = snapshot_files(new.home)
    assert len(snaps) == 5 and not (new.home / "imports").exists()
    days = sorted(p["date"] for points in load(new.exports, "balances_timeseries.json")["series"].values()
                  for p in points)
    assert sorted(set(days)) == [(end - timedelta(days=k)).isoformat() for k in range(4, -1, -1)]
    future = new.truth["planted"].get("future.uid")
    txns = load(new.exports, "transactions.json")["transactions"]
    assert txns and all((end - timedelta(days=4)).isoformat() <= t["date"] <= end.isoformat()
                        for t in txns if t["id"] != future)
    assert load(new.exports, "holdings.json")["holdings"]
    # tiny: one checking account read from two months of statement files, no feed
    tiny = tree("tiny")
    end = date.fromisoformat(tiny.truth["end"])
    assert list(tiny.truth["accounts"]) == [hh.CHECKING]
    assert not (tiny.home / "store").exists() and len(list((tiny.home / "imports").iterdir())) == 2
    months = {t["date"][:7] for t in load(tiny.exports, "transactions.json")["transactions"]}
    assert months == {shift_months(end, -1).isoformat()[:7], end.isoformat()[:7]}
    assert load(tiny.exports, "manifest.json")["simplefin_fetched_at"] is None
    # empty: a household and no account, no file, no snapshot, no row; every export there and of full's shape
    empty = tree("empty")
    assert empty.truth["accounts"] == {} and sorted(p.name for p in empty.home.iterdir()) == ["config", "truth.json"]
    full = tree("full")
    for name in EXPORT_FILES:
        doc = load(empty.exports, name)
        assert set(doc) == set(load(full.exports, name)), name
    assert load(empty.exports, "transactions.json") == {"count": 0, "transactions": []}
    assert load(empty.exports, "accounts.json") == {"accounts": [], "manual_assets": [], "manual_overridden": []}
    assert set(load(empty.exports, "assumptions.json")) == set(load(full.exports, "assumptions.json"))


def test_manifest_setup_counts_what_is_configured(tree):
    """manifest.setup says what the household has configured, whether or not data has arrived: the registry's
    accounts, whether one names a feed id, the statement files the accounts' globs match and the hand-stated values."""
    def setup(name: str) -> dict:
        return load(tree(name).exports, "manifest.json")["setup"]

    def files(name: str) -> int:
        return len(list((tree(name).home / "imports").iterdir()))

    assert setup("empty") == {"accounts": 0, "feed": False, "import_files": 0, "manual_assets": 0}
    assert setup("tiny") == {"accounts": 1, "feed": False, "import_files": files("tiny"), "manual_assets": 0}
    assert setup("new") == {"accounts": 3, "feed": True, "import_files": 0, "manual_assets": 0}
    # the home entered in the app and the car accounts.yaml states
    assert setup("full") == {"accounts": 9, "feed": True, "import_files": files("full"), "manual_assets": 2}
    assert setup("no-investments")["manual_assets"] == setup("no-income")["manual_assets"] == 2
    assert files("tiny") > 0 and files("full") > 0
    # the empty household's net worth is unknown, not $0
    daily = load(tree("empty").exports, "networth_daily.json")
    assert (daily["current_total"], daily["current_snapshot_date"]) == (None, None)


@pytest.mark.parametrize("name", profiles.NAMES)
def test_only_full_has_the_module_on(tree, name):
    """`full` switches Home buying on in household.yaml; every other profile's household.yaml has no `modules` key
    at all, so its exports state no switch. The planted handle says the same, and the home and the car are in the
    three profiles with the mortgage: no file of assets entered in the app, and no manual entry, anywhere else."""
    t = tree(name)
    household = yaml.safe_load((t.home / "config" / "household.yaml").read_text("utf-8"))
    want = {"home_buying": True} if name == "full" else {}
    assert household.get("modules", {}) == want and ("modules" in household) == (name == "full")
    assert load(t.exports, "assumptions.json")["modules"] == t.truth["planted"]["modules"] == want
    held = name in ("full", "no-investments", "no-income")
    accounts = yaml.safe_load((t.home / "config" / "accounts.yaml").read_text("utf-8"))
    assert (t.home / "state" / "family" / "manual_assets.json").is_file() == held == bool(accounts.get("manual_entries"))
    assert [(a["asset_class"], a["origin"]) for a in load(t.exports, "accounts.json")["manual_assets"]] == (
        [("House", "app"), ("Car", "config")] if held else [])


def paydays(first: date, last: date) -> list[date]:
    """The household's paydays from `first` to `last`: the business day on or before each 15th and the last
    business day of each month (Sam is paid on the second, Alex on both)."""
    days = []
    for month in sorted({first.replace(day=1), last.replace(day=1)}):
        mid, final = business_day_on_or_before(month.replace(day=15)), last_business_day(month)
        days += [mid, final, final]
    return sorted(d for d in days if first <= d <= last)


NEW_ENDS = {"a-monday": date(2027, 3, 8), "a-friday": date(2027, 3, 12), "a-saturday": date(2027, 3, 13),
            "a-sunday": date(2027, 3, 14), "the-1st": date(2027, 4, 1), "the-15th": date(2027, 4, 15),
            "a-month-end": date(2027, 4, 30)}


@pytest.mark.parametrize("end", NEW_ENDS.values(), ids=NEW_ENDS.keys())
def test_the_new_household_at_ends_across_the_week_and_the_month(tmp_path_factory, end):
    """The behaviours of `full` that fit five days run and the rest skip: a payday inside the window is a deposit
    the income rules recognise, and there is no other income."""
    t = generated(tmp_path_factory, "new", end)
    assert demo_checks.mismatches(t.exports, t.truth) == []
    income = [x for x in load(t.exports, "transactions.json")["transactions"] if x["type"] == "income"]
    assert sorted(date.fromisoformat(x["date"]) for x in income) == paydays(end - timedelta(days=4), end)
    for x in income:
        assert (x["category"], x["subcategory"], x["category_source"], x["needs_review"]) == (
            "Income", "Salary", "type_default", False) and x["earner"] in (hh.ALEX, hh.SAM), x["id"]


# ------------------------------------------------------------------ the profiles seeded as full

@pytest.mark.parametrize("name", ["no-investments", "no-income"])
def test_profiles_seeded_alike_share_their_rows(name):
    """Every row the profile posts on an account is, by date, amount and descriptor, a row `full` posts there —
    except the settlements' rows, which follow the balances the left-out features change."""
    profile = profiles.get(name)
    assert profile.seed == "full" and profile.stream_seed == profiles.FULL.stream_seed

    def rows(p: profiles.Profile) -> dict[str, Counter]:
        plan = plan_mod.build(p, DEMO_END)
        settled = tuple(f"-{s.__name__}-" for s in p.settlements)
        return {a: Counter((r.date, r.cents, r.desc) for r in plan.ledger.rows(a)
                           if not (r.feed_id and any(s in r.feed_id for s in settled)))
                for a in plan.ledger.accounts()}

    mine, full = rows(profile), rows(profiles.FULL)
    assert set(mine) <= set(full)
    for account, counted in mine.items():
        assert not counted - full[account], account
    assert sum(c.total() for c in mine.values()) > 1_000


# ------------------------------------------------------------------ the end-date sweep

SWEEP_ENDS = sorted({
    *(month_end(shift_months(date(2026, 11, 1), k)) for k in range(12)),   # every month end, Nov 2026 to Oct 2027
    date(2028, 2, 29), date(2026, 12, 31), date(2027, 1, 1),               # a leap day; a year's last and first day
    date(2027, 5, 1), date(2027, 5, 2),                                    # a month whose 1st is a Saturday
    *(date(2027, 3, 11) + timedelta(days=k) for k in range(7)),             # a week in mid-month
})


def test_the_sweep_holds_every_named_edge():
    month_ends = [d for d in SWEEP_ENDS if (d + timedelta(days=1)).day == 1 and d.year * 12 + d.month in range(
        2026 * 12 + 11, 2027 * 12 + 11)]
    saturday_firsts = [d for d in SWEEP_ENDS if d.day == 1 and d.weekday() == 5]
    week = [d for d in SWEEP_ENDS if 10 <= d.day <= 20]
    assert len(month_ends) == 12 and date(2028, 2, 29) in SWEEP_ENDS
    assert {date(2026, 12, 31), date(2027, 1, 1)} <= set(SWEEP_ENDS)
    assert saturday_firsts and saturday_firsts[0] + timedelta(days=1) in SWEEP_ENDS
    assert len(week) == 7 and (week[-1] - week[0]).days == 6
    assert len(SWEEP_ENDS) == 23


@pytest.mark.parametrize("end", SWEEP_ENDS, ids=str)
@pytest.mark.parametrize("name", ["full", "tiny"])
def test_end_date_sweep(tmp_path, monkeypatch, capsys, name, end):
    """Generated to each end, validated, ingested at that end's pinned clock: no failed check, and the exports equal
    truth in every section."""
    home = tmp_path / "home"
    truth = generate.run(name, end, home)
    monkeypatch.setenv("FF_HOME", str(home))
    monkeypatch.setattr(sys, "argv", ["etl", "validate"])
    assert etl_main() == 0 and capsys.readouterr().out.startswith("config OK")
    exports = ingest_demo(home, f"{end.isoformat()}T{generate.INGEST_TIME}")
    checks = load(exports, "quality_report.json")["checks"]
    assert [c["name"] for c in checks if c["status"] == "fail"] == []
    assert demo_checks.mismatches(exports, truth) == []


# ------------------------------------------------------------------ determinism

def test_generation_is_deterministic(tmp_path):
    """Each profile generated twice by the command in its own process, under two hash seeds and into two
    directories: the inputs hash alike, and no generated file holds either directory's path."""
    runs = {}
    for name in profiles.NAMES:
        for seed in ("1", "2"):
            out = tmp_path / f"seed-{seed}" / name
            runs[name, seed] = out, subprocess.Popen(
                [sys.executable, "-m", "tools.demo.generate", "--profile", name, "--end", DEMO_END.isoformat(),
                 "--out", str(out)], cwd=ROOT, env=dict(os.environ, PYTHONHASHSEED=seed),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for (name, seed), (_, process) in runs.items():
        _, err = process.communicate(timeout=300)
        assert process.returncode == 0, (name, seed, err.decode())
    paths = [str(tmp_path / f"seed-{seed}").encode() for seed in ("1", "2")]
    for name in profiles.NAMES:
        one, two = runs[name, "1"][0], runs[name, "2"][0]
        assert generate.input_hash(one) == generate.input_hash(two), name
        for path in (p for tree in (one, two) for p in tree.rglob("*") if p.is_file()):
            data = path.read_bytes()
            assert not any(p in data for p in paths), path


def test_the_input_hash_reads_the_generated_inputs_only(demo_home, demo_exports, tmp_path):
    """What an ingest writes beside the inputs (data/, the learned rules under state/engine/) never moves the hash; a
    byte of any input does."""
    ingested = demo_exports.exports.parents[1]
    assert (ingested / "state" / "engine" / "learned_rules.json").exists() and (ingested / "data").is_dir()
    assert generate.input_hash(ingested) == generate.input_hash(demo_home.path)
    for name in ("answers.json", "manual_assets.json"):
        copy = tmp_path / name
        shutil.copytree(demo_home.path, copy)
        state = copy / "state" / "family" / name
        state.write_bytes(state.read_bytes() + b" ")
        assert generate.input_hash(copy) != generate.input_hash(demo_home.path), name


# ------------------------------------------------------------------ the blessed hashes

BLESSED = ROOT / "tools" / "demo" / "inputs.sha256"


def test_the_blessed_hashes_are_current(tmp_path, capsys):
    """`--check` regenerates the six at the blessed end and finds every hash the file blesses; with one line of a
    copy altered, it fails and names that profile alone."""
    assert generate.BLESSED_END == DEMO_END
    lines = BLESSED.read_text("utf-8").splitlines()
    assert [line.split("  ")[1] for line in lines] == list(profiles.NAMES)
    assert generate.main(["--check"]) == 0
    assert capsys.readouterr().err == ""
    digest, name = lines[2].split("  ")
    lines[2] = f"{digest[::-1]}  {name}"
    altered = tmp_path / "inputs.sha256"
    altered.write_text("\n".join(lines) + "\n", "utf-8")
    assert generate.main(["--check", "--hashes", str(altered)]) == 1
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1 and f" {name}: " in err[0] and digest in err[0]


def test_the_command_checks_blesses_and_refuses(tmp_path, capsys):
    """`--bless` writes one `<hash>  <profile>` line per profile, in profile order, to the file `--hashes` names (at
    the blessed end and with this generator, the blessed file itself); the command refuses a mix of its two modes,
    a mode with generating arguments, generating without all three and a hash file that is not there."""
    hashes = tmp_path / "inputs.sha256"
    assert generate.main(["--bless", "--hashes", str(hashes)]) == 0
    assert capsys.readouterr().out == hashes.read_text("utf-8")
    assert hashes.read_bytes() == BLESSED.read_bytes()
    assert [len(line.split("  ")[0]) for line in hashes.read_text("utf-8").splitlines()] == [64] * 6
    missing = tmp_path / "absent.sha256"
    for args, says in ((["--check", "--bless"], "--bless"),
                       (["--check", "--profile", "full"], "--profile"),
                       (["--bless", "--end", "2026-10-31", "--hashes", str(hashes)], "--end"),
                       (["--profile", "full", "--end", "2026-10-31"], "--out"),
                       (["--hashes", str(hashes), "--profile", "full", "--end", "2026-10-31", "--out",
                         str(tmp_path / "tree")], "--hashes"),
                       (["--check", "--hashes", str(missing)], str(missing))):
        assert generate.main(args) == 2, args
        captured = capsys.readouterr()
        assert captured.out == "" and len(captured.err.splitlines()) == 1 and says in captured.err, args
    assert not (tmp_path / "tree").exists() and not missing.exists()
    assert hashes.read_bytes() == BLESSED.read_bytes()


def test_check_skips_blank_lines_and_names_the_profile_that_fails_to_generate(tmp_path, capsys, monkeypatch):
    """A blank line in the hash file is no profile; a profile that raises while generating is one stderr line naming
    it and exit 2, the code of a refusal, never a traceback and never the exit 1 of a hash that differs."""
    spaced = tmp_path / "spaced.sha256"
    spaced.write_text("\n" + BLESSED.read_text("utf-8").replace("\n", "\n\n"), "utf-8")
    assert generate.main(["--check", "--hashes", str(spaced)]) == 0
    capsys.readouterr()
    real = generate.run

    def failing(profile, end, out):
        if profile == "tiny":
            raise RuntimeError("boom")
        return real(profile, end, out)

    monkeypatch.setattr(generate, "run", failing)
    assert generate.main(["--check"]) == 2
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1 and "tiny" in err[0] and "boom" in err[0]


def test_a_history_in_days_refuses_an_account_that_opens_or_connects_later():
    files = profiles.Files("chase_checking", "months")
    for late in ({"files": files, "feed": False, "opens_after_months": 1}, {"feed_after_months": 1}):
        account = replace(profiles.NEW.accounts[0], **late)
        with pytest.raises(ValueError, match="days has no month an account could open or connect"):
            replace(profiles.NEW, accounts=(account, *profiles.NEW.accounts[1:]))

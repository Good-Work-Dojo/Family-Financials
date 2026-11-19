# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""End to end on the generated demo household (tools/demo, the `full` profile at DEMO_END): determinism of a full
ingest and the global invariants of its exports, with every count taken from the truth the generator wrote beside the
tree (tests/demo_checks.py compares every section of it). Nothing is skipped and every input is generated here.
Everything here is invented.

The ingests beyond the session's own (`demo_exports`) run on copies of the generated tree inside one target: a fresh
pytest temp dir, or FF_E2E_DIR when set, which keeps the trees for a look afterwards. A target that is, or sits inside,
the repo's or a live container's data/ or scenarios/ is refused (`refusal`): the live ETL containers mount their
data/ and scenarios/ at /app/finance-app/{data,scenarios}, so a bare `pytest` there must never write into them."""
import datetime as dt
import json
import os
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import demo_checks
import pytest
from conftest import DEMO_NOW, ingest_demo

from etl import dates, pipeline
from etl.models import INTERNAL_CATEGORY
from tools.demo import household as hh

ROOT = Path(__file__).resolve().parent.parent
# the tree in the ETL image's test target, where a container's data/ and scenarios/ may be mounts of a live household's
CONTAINER_ROOT = Path("/app/finance-app")


def refusal(work: Path, root: Path = ROOT) -> str | None:
    """Why an e2e run into `work` (its data/ and scenarios/) must not happen, else None: the target
    is, or sits inside, the repo's or a live container's data/ or scenarios/."""
    live = [p.resolve() for p in (root / "data", root / "scenarios",
                                  CONTAINER_ROOT / "data", CONTAINER_ROOT / "scenarios")]
    for target in (work / "data", work / "scenarios"):
        t = target.resolve()
        hit = next((p for p in live if t == p or p in t.parents), None)
        if hit is not None:
            return (f"refusing: the e2e run would write {t}, at or inside {hit} (the repo's or a live "
                    "container's own state) - set FF_E2E_DIR to a scratch dir")
    return None


def e2e_dir(raw: str | None, make_tmp) -> Path:
    """The run's target: FF_E2E_DIR when set (and not refused), else a fresh temp dir from `make_tmp` - never
    the repo's own data/, so a container whose /app/finance-app/data is the live mount is safe by default."""
    raw = (raw or "").strip()
    w = Path(raw).resolve() if raw else make_tmp()
    why = refusal(w)
    if why:
        pytest.fail(why, pytrace=False)
    return w


@pytest.fixture(scope="module")
def work(tmp_path_factory) -> Path:
    w = e2e_dir(os.environ.get("FF_E2E_DIR"), lambda: tmp_path_factory.mktemp("e2e"))
    w.mkdir(parents=True, exist_ok=True)
    return w


def tree_copy(demo_home, work: Path) -> Path:
    """A copy of the generated tree in a directory of its own inside `work`."""
    home = Path(tempfile.mkdtemp(prefix="demo-", dir=work)) / "home"
    shutil.copytree(demo_home.path, home)
    return home


def ingest_at_wall_clock(home: Path) -> Path:
    """Ingest the tree `home` in place with FF_HOME at it and no pinned clock; return its exports directory."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("FF_HOME", str(home))
        mp.delenv("FF_NOW", raising=False)
        assert pipeline.run() == 0
    return home / "data" / "exports"


def export_files(exports: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(exports.glob("*.json"))}


def load(exports: Path, name: str) -> dict:
    return json.loads((exports / name).read_text("utf-8"))


def test_determinism(demo_home, demo_exports, work):
    # two ingests under the pinned clock: every export file byte for byte, manifest.json included
    pinned = export_files(demo_exports.exports)
    again = export_files(ingest_demo(tree_copy(demo_home, work), DEMO_NOW))
    assert pinned and "manifest.json" in pinned and set(pinned) == set(again)
    assert pinned == again
    # two ingests at the wall clock: identical but for the manifest's generation stamp
    first, second = (export_files(ingest_at_wall_clock(tree_copy(demo_home, work))) for _ in range(2))
    manifests = [json.loads(files.pop("manifest.json")) for files in (first, second)]
    assert first and first == second
    stamped = json.loads(pinned["manifest.json"])
    for m in manifests:
        assert set(m) == set(stamped)
        assert m.pop("generated_at") != stamped["generated_at"]      # the control: no pinned clock reached it
    assert manifests[0] == manifests[1]


def test_invariants(demo_exports):
    truth, figures = demo_exports.truth, demo_exports.truth["figures"]
    conn = sqlite3.connect(demo_exports.exports.parent / "finance.db")
    q = lambda sql, *args: conn.execute(sql, args).fetchone()[0]

    # no transfer/cc-payment row contributes to any spending category
    assert q("SELECT COUNT(*) FROM transactions WHERE type IN ('transfer','cc-payment') AND category != ?",
             INTERNAL_CATEGORY) == 0
    # every pair is two rows of opposite amounts on two accounts, and the pairs are as many as truth's
    groups: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for group, account, cents in conn.execute("SELECT transfer_group_id, account_id, amount_cents FROM transactions "
                                              "WHERE transfer_group_id IS NOT NULL"):
        groups[group].append((account, cents))
    assert groups and len(groups) == len(figures["pairs"])
    for group, legs in groups.items():
        assert len(legs) == 2 and legs[0][1] == -legs[1][1] != 0 and legs[0][0] != legs[1][0], group
    # the mortgage liability is one row in the current snapshot: the mortgage account's feed balance
    current = q("SELECT MAX(snapshot_date) FROM assets")
    assert current == figures["snapshots"][-1]["date"]
    assert conn.execute("SELECT account_id, source, value_cents FROM assets WHERE asset_class='Mortgage' "
                        "AND snapshot_date = ?", (current,)).fetchall() == [
        (figures["mortgage"]["account"], "simplefin_balance", -figures["mortgage"]["balance_cents"])]
    # each planted pay run: its paychecks are as many, and sum to as much, as truth's income phase says
    phases = {(p["earner"], p["employer"]): p for p in figures["income_phases"]}
    planted = {"income.alex_first": (hh.ALEX, hh.QUILLWORKS), "income.alex_second": (hh.ALEX, hh.LARKSPUR),
               "income.sam": (hh.SAM, hh.HARBOR_CITY)}
    assert set(planted.values()) == set(phases)
    for handle, key in planted.items():
        first, last = (q("SELECT date FROM transactions WHERE txn_uid = ?", uid) for uid in truth["planted"][handle])
        phase = phases[key]
        assert (first, last) == (phase["first"], phase["last"]), handle
        count, total = conn.execute("SELECT COUNT(*), SUM(amount_cents) FROM transactions WHERE type = 'income' "
                                    "AND earner = ? AND date BETWEEN ? AND ?", (key[0], first, last)).fetchone()
        assert (count, total) == (phase["deposits"], phase["total_cents"]), handle
    conn.close()
    assert demo_checks.mismatches(demo_exports.exports, truth) == []


def test_export_shapes(demo_exports):
    exports, figures = demo_exports.exports, demo_exports.truth["figures"]
    txj = load(exports, "transactions.json")
    assert txj["count"] == len(txj["transactions"]) == len(figures["rows"]) > 0
    att = load(exports, "assets_timeseries.json")
    # one snapshot per month end before the current date, then the current one, oldest first
    days = [s["snapshot_date"] for s in att["snapshots"]]
    assert days and days == sorted(set(days)) == [s["date"] for s in figures["snapshots"]]
    manifest = load(exports, "manifest.json")
    assert days[-1] == manifest["current_snapshot_date"]
    cur = att["snapshots"][-1]
    assert cur["mortgage_included"] is figures["snapshots"][-1]["mortgage_included"]
    assert all("as_of" in r for r in cur["rows"])
    cf = load(exports, "cashflow_monthly.json")
    assert [m["month"] for m in cf["months"] if not m["full_coverage"]] == \
        [month for month, m in sorted(figures["months"].items()) if not m["full_coverage"]]


def test_manifest_timestamps(demo_home, demo_exports):
    """The timestamps the dashboard reads, and the difference between them —
    getting these wrong is invisible in the UI until someone trusts a stale
    number."""
    m = load(demo_exports.exports, "manifest.json")

    # generated_at is the ingest time as a real INSTANT (local wall clock plus
    # its offset): the pinned clock in the household's zone. app/src/lib/answers.ts
    # compares it against an answer's UTC `answered_at`; without the offset that
    # comparison silently loses hours. The first 19 chars stay the naive form every
    # slice-based display already depends on.
    zone = ZoneInfo(demo_home.truth["household"]["timezone"])
    assert m["generated_at"] == dt.datetime.fromisoformat(DEMO_NOW).replace(tzinfo=zone).isoformat()
    gen = dt.datetime.fromisoformat(m["generated_at"])
    assert gen.tzinfo is not None, m["generated_at"]
    assert m["generated_at"][:19] == DEMO_NOW and m["generated_at"][10] == "T"

    # The feed stamp says when data was PULLED, always UTC with a Z, and it
    # must match the newest snapshot actually on disk — this is the value the
    # freshness pill ages, so a drift here shows the user a comfortable lie.
    key = "simplefin_fetched_at"
    assert key in m, f"{key} missing from the manifest"
    snaps = sorted((demo_home.path / "store" / "simplefin" / "snapshots").glob("accounts-*.json"))
    assert snaps
    assert m[key].endswith("Z"), m[key]
    assert m[key] == dates.snapshot_fetched_at(snaps[-1].name)
    assert dt.datetime.fromisoformat(m[key].replace("Z", "+00:00")) <= gen


# ------------------------------------------------------------- the target

def test_the_default_target_is_a_fresh_temp_dir_never_the_repos_data(tmp_path):
    made = []
    assert e2e_dir(None, lambda: made.append(1) or tmp_path / "t") == tmp_path / "t"
    assert e2e_dir("  ", lambda: tmp_path / "u") == tmp_path / "u" and made == [1]


def test_ff_e2e_dir_is_honoured_and_no_temp_dir_is_made(tmp_path):
    assert e2e_dir(str(tmp_path / "scratch"), lambda: pytest.fail("made a temp dir")) == tmp_path / "scratch"


@pytest.mark.parametrize("target", ["/app/finance-app", "/app/finance-app/data", "/app/finance-app/scenarios/x"])
def test_a_live_container_state_dir_is_refused_wherever_the_repo_is(target, tmp_path):
    # with the repo elsewhere (the host), the container paths alone must refuse; inside an image they are the repo too
    assert (refusal(Path(target), root=tmp_path / "repo") or "").startswith("refusing: the e2e run would write")
    with pytest.raises(pytest.fail.Exception, match="refusing: the e2e run would write"):
        e2e_dir(target, lambda: pytest.fail("made a temp dir"))


@pytest.mark.parametrize("sub", ["", "data", "scenarios/deeper"])
def test_the_repos_own_state_dirs_are_refused(sub):
    with pytest.raises(pytest.fail.Exception, match="FF_E2E_DIR to a scratch dir"):
        e2e_dir(str(ROOT / sub), lambda: pytest.fail("made a temp dir"))


def test_refusal_is_about_the_two_state_dirs_only(tmp_path):
    repo = tmp_path / "repo"
    assert refusal(repo, root=repo) is not None
    assert refusal(repo / "data" / "x", root=repo) is not None
    assert refusal(tmp_path / "scratch", root=repo) is None
    assert refusal(repo / "elsewhere", root=repo) is None          # a sibling of data/ inside the repo writes elsewhere/data

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""One pinned clock. The engine's wall-clock reads that reach a file go through etl/clock.py, which reads FF_NOW:
unset it is the real clock in the requested zone; set it is that instant, so an ingest of generated data is
reproducible to the byte. The demo generator (tools/demo) reads no clock at all: every date comes from its --end.
Everything here builds on the invented minimal household or on synthetic rows."""
from __future__ import annotations

import ast
import datetime as dt
import json
import shutil
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from conftest import ROOT

from etl import clock, pipeline
from etl.__main__ import main
from etl.enrich import learned
from etl.validate import ConfigError

EXAMPLE = ROOT / "examples" / "minimal"
PINNED = "2030-01-31T18:00:00"


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "home"
    shutil.copytree(EXAMPLE, d)
    monkeypatch.setenv("FF_HOME", str(d))
    return d


def cli(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["etl", *args])
    return main()


def test_without_the_variable_the_clock_is_the_wall_clock_in_the_zone(monkeypatch):
    monkeypatch.delenv("FF_NOW", raising=False)
    zone = ZoneInfo("Pacific/Auckland")
    before = dt.datetime.now(zone)
    got = clock.now("Pacific/Auckland")
    after = dt.datetime.now(zone)
    assert before <= got <= after
    assert got.utcoffset() == before.utcoffset() and got.tzinfo is not None


def test_a_naive_value_is_read_in_the_household_zone(monkeypatch):
    monkeypatch.setenv("FF_NOW", "2030-07-01T18:00:00")
    got = clock.now("America/New_York")
    assert got.isoformat() == "2030-07-01T18:00:00-04:00"
    assert clock.now("Asia/Kolkata").isoformat() == "2030-07-01T18:00:00+05:30"
    monkeypatch.setenv("FF_NOW", "2030-07-01 18:00")               # a space and no seconds read the same way
    assert clock.now("America/New_York").isoformat() == "2030-07-01T18:00:00-04:00"


@pytest.mark.parametrize("value", ["2030-07-01T22:00:00Z", "2030-07-01T22:00:00+00:00", "2030-07-01T23:30:00+01:30"])
def test_a_value_with_an_offset_is_that_instant(monkeypatch, value):
    monkeypatch.setenv("FF_NOW", value)
    got = clock.now("America/New_York")
    assert got == dt.datetime(2030, 7, 1, 22, tzinfo=dt.UTC)
    assert got.isoformat() == "2030-07-01T18:00:00-04:00"           # converted to the zone asked for


@pytest.mark.parametrize("value", ["", "  "])
def test_an_empty_value_is_the_wall_clock(monkeypatch, value):
    monkeypatch.setenv("FF_NOW", value)
    clock.check()
    before = dt.datetime.now(ZoneInfo("UTC"))
    got = clock.now("UTC")
    assert before <= got <= dt.datetime.now(ZoneInfo("UTC"))


@pytest.mark.parametrize("value", ["2030-03-10T02:30:00", "2030-11-03T01:30:00"])
def test_a_naive_value_in_a_dst_gap_or_fold_is_deterministic(monkeypatch, value):
    monkeypatch.setenv("FF_NOW", value)
    first, second = clock.now("America/New_York"), clock.now("America/New_York")
    assert first == second and first.isoformat()[:19] == value


@pytest.mark.parametrize("value", ["2030-07-01", "tomorrow", "2030-13-01T00:00:00", "18:00",
                                   "0001-01-01T00:00:00Z"])
def test_a_bad_value_stops_the_ingest_with_one_line(home, monkeypatch, capsys, value):
    monkeypatch.setenv("FF_NOW", value)
    assert cli(monkeypatch, "ingest") == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == (f"FF_NOW: '{value}' is not a date and time — write it like 2026-10-31T18:00:00\n")
    assert not (home / "data").exists()                              # nothing was created, not even the data dir


def test_a_bad_value_stops_an_in_process_run_before_anything_is_written(home, monkeypatch):
    monkeypatch.setenv("FF_NOW", "tomorrow")
    with pytest.raises(ConfigError):
        pipeline.run()
    assert not (home / "data").exists()


def test_two_pinned_ingests_are_byte_identical_manifest_included(tmp_path, monkeypatch):
    monkeypatch.setenv("FF_NOW", PINNED)
    outs = []
    for name in ("a", "b"):
        d = tmp_path / name
        shutil.copytree(EXAMPLE, d)
        monkeypatch.setenv("FF_HOME", str(d))
        assert pipeline.run() == 0
        outs.append(d / "data" / "exports")
    files_a = sorted(p.name for p in outs[0].iterdir())
    assert files_a and files_a == sorted(p.name for p in outs[1].iterdir())
    for name in files_a:
        assert (outs[0] / name).read_bytes() == (outs[1] / name).read_bytes(), name
    stamp = json.loads((outs[0] / "manifest.json").read_text("utf-8"))["generated_at"]
    assert stamp == "2030-01-31T18:00:00-05:00"                      # the household zone's wall clock and offset
    assert stamp[:19] == PINNED


def test_the_learned_rules_stamp_follows_the_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("FF_NOW", "2030-07-01T18:00:00-04:00")        # an offset: the stamp is that instant in UTC
    path = tmp_path / "learned_rules.json"
    doc = {**learned.empty_doc(), "rules": [{"vendor": "Acme", "category": "Shopping", "subcategory": None,
                                              "count": 3}]}
    written, checks = learned.write_if_changed(path, doc)
    assert written and checks == []
    assert json.loads(path.read_text("utf-8"))["updated_at"] == "2030-07-01T22:00:00Z"


TIME_READS = ("time", "time_ns", "localtime", "gmtime")


def _wall_clock_reads(tree: ast.AST) -> list[tuple[int, str]]:
    """Every call or reference to a wall-clock accessor: now / today / utcnow, and time.time / time_ns / localtime /
    gmtime through any alias of the time module or a from-import."""
    aliases = {"time"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(a.asname or a.name for a in node.names if a.name == "time")
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "clock":
                continue                     # clock.now, the pinned clock itself
            if node.attr in ("now", "today", "utcnow"):
                hits.append((node.lineno, node.attr))
            elif node.attr in TIME_READS and isinstance(node.value, ast.Name) and node.value.id in aliases:
                hits.append((node.lineno, f"time.{node.attr}"))
        elif isinstance(node, ast.ImportFrom) and node.module == "time":
            hits.extend((node.lineno, f"from time import {a.name}") for a in node.names if a.name in TIME_READS)
    return sorted(hits)


def _clock_accessor_users(tree: ast.AST) -> bool:
    """Whether the module calls or references clock.now or clock.utc_stamp."""
    return any(isinstance(n, ast.Attribute) and n.attr in ("now", "utc_stamp")
               and isinstance(n.value, ast.Name) and n.value.id == "clock" for n in ast.walk(tree))


def test_only_the_clock_reads_the_wall_clock():
    """Outside clock.py and the networked _net.py (the feed fetch, which the offline pipeline does not import) no
    module reads the wall clock; time.monotonic is a duration, not a date, and is not one of the accessors. The scan
    covers the whole package, and exactly the pipeline and the learned-rules module read the pinned clock."""
    offenders = []
    scanned = []
    users = []
    for path in sorted((ROOT / "etl").rglob("*.py")):
        rel = path.relative_to(ROOT / "etl")
        if rel.as_posix() in ("clock.py", "_net.py"):
            continue
        tree = ast.parse(path.read_text("utf-8"))
        scanned.append(rel.as_posix())
        if _clock_accessor_users(tree):
            users.append(rel.as_posix())
        offenders += [f"{rel}:{line} {what}" for line, what in _wall_clock_reads(tree)]
    assert len(scanned) > 20
    assert "pipeline.py" in scanned and "enrich/learned.py" in scanned
    assert offenders == []
    assert users == ["enrich/learned.py", "pipeline.py"]


def test_the_demo_generator_takes_every_date_from_its_end():
    """tools/demo builds a household that ends on the --end it is given, and no module of it reads the wall clock: the
    same profile and end give the same bytes on any day."""
    offenders = []
    scanned = []
    for path in sorted((ROOT / "tools" / "demo").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        scanned.append(rel)
        offenders += [f"{rel}:{line} {what}" for line, what in _wall_clock_reads(ast.parse(path.read_text("utf-8")))]
    assert len(scanned) > 10
    assert {"tools/demo/generate.py", "tools/demo/plan.py", "tools/demo/behaviours/review.py"} <= set(scanned)
    assert offenders == []


def test_the_scan_sees_what_it_scans_for():
    tree = ast.parse("import datetime as dt, time\ndt.datetime.now()\ndt.date.today()\ntime.time()\n"
                     "x = dt.datetime.utcnow\n# dt.datetime.now()\ns = 'time.time()'\ntime.monotonic()\n")
    assert [what for _, what in _wall_clock_reads(tree)] == ["now", "today", "time.time", "utcnow"]
    more = ast.parse("import time as t\nt.time_ns()\nt.localtime()\nt.gmtime()\nt.time()\nt.sleep(1)\n"
                     "from time import time_ns, monotonic\n")
    assert [what for _, what in _wall_clock_reads(more)] == [
        "time.time_ns", "time.localtime", "time.gmtime", "time.time", "from time import time_ns"]

# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""`python -m etl demo` (etl/demo_cli.py): an empty install directory becomes the invented Sample household, ingested,
with a passcode printed once and stored as a hash, the settings and compose.yaml written, and "Now run ./ff up" last;
a directory holding anything else is refused without --force, and a household's files are never written over."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import ROOT
from ff_helpers import AS_USER_MAIN, USER, new_home

from etl import demo_cli, install, passcode_cli
from etl.config import Config

# after the demo household's last snapshot hour on its clock (17:00 in New York), so its history ends that day
NOW = dt.datetime(2026, 10, 31, 23, 30, tzinfo=dt.UTC)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    return new_home(tmp_path, monkeypatch)


def test_an_empty_directory_becomes_the_demo_household_ready_to_start(home, capsys):
    (home / "ff").write_text("#!/bin/sh\n")
    out = []
    assert demo_cli.run(out=out.append, now=NOW) == 0
    phrase = next(line for line in out if line.startswith("Sign in with: ")).removeprefix("Sign in with: ")
    assert re.fullmatch(r"[a-z]+(-[a-z]+){3}", phrase) and all(w in demo_cli.WORDS for w in phrase.split("-"))
    assert sum(phrase in line for line in out) == 1
    assert out[-1].startswith("Now run ./ff up")
    captured = capsys.readouterr()
    assert phrase not in captured.out + captured.err
    passcodes = (home / "secrets" / "web" / "passcodes").read_text()
    assert passcodes.startswith("demo:scrypt:") and phrase not in passcodes
    assert stat.S_IMODE(os.stat(home / "secrets" / "web" / "passcodes").st_mode) == 0o600
    line = passcode_cli.auth.parse_line(passcodes.strip())
    assert passcode_cli.HASHER(phrase.encode(), line.salt) == line.digest
    cfg = Config(check_paths=True)
    assert len(cfg.accounts) == 9
    manifest = json.loads((home / "data" / "exports" / "manifest.json").read_text())
    assert manifest["transaction_count"] > 1000
    assert (home / "store" / "simplefin" / "snapshots").is_dir()
    assert (home / "state" / "family" / "answers.json").is_file()
    assert not (home / "truth.json").exists() and not list(home.glob(".demo-*"))
    env = install.read_env(home)
    assert (env["FF_UID"], env["FF_BIND"], env["FF_PORT"]) == (str(USER), "127.0.0.1", "8080")
    assert (home / "compose.yaml").is_file()
    for d in install.DIRS:
        assert stat.S_IMODE(os.stat(home / d.path).st_mode) == d.mode, d.path


def test_the_demos_planted_warnings_are_its_two_bank_feed_ones_and_it_says_so(home):
    out = []
    assert demo_cli.run(out=out.append, now=NOW) == 0
    report = json.loads((home / "data" / "exports" / "quality_report.json").read_text())
    warned = sorted(c["name"].rsplit(".", 1)[0] for c in report["checks"] if c["status"] == "warn")
    assert warned == ["simplefin.stale", "simplefin.unmapped"]
    assert demo_cli.PLANTED_WARNINGS in out
    assert out.index(demo_cli.PLANTED_WARNINGS) < next(i for i, line in enumerate(out) if line.startswith("Sign in"))
    readme = " ".join((install.ROOT / "README.md").read_text("utf-8").split()).lower()
    assert "the two warnings its ingest prints are part of the demo" in readme


def test_doctor_on_the_demo_says_its_feed_is_invented_instead_of_warning_of_no_stored_access(home):
    """The demo's accounts name bank feed ids with no access stored, by design: doctor says what they are and warns of
    nothing there; the same accounts under another household's name are warned of."""
    from etl import doctor_cli
    assert demo_cli.run(out=lambda _: None, now=NOW) == 0
    lines: list[str] = []
    doctor_cli.run(out=lines.append, now=NOW)
    assert not any("no bank feed access is stored" in line for line in lines)
    assert "ok    the demo household's bank feed is invented: its accounts show the snapshots it came with" in lines
    household = home / "config" / "household.yaml"
    household.write_text(household.read_text("utf-8").replace("The Sample household", "The Example household"),
                         "utf-8")
    lines.clear()
    doctor_cli.run(out=lines.append, now=NOW)
    assert any(line.startswith("warn  ") and "no bank feed access is stored" in line for line in lines)


def test_the_demo_made_under_umask_000_is_written_with_the_tables_modes(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("FF_")}
    env.update(FF_HOME=str(home), STUB_AS_UID=str(USER), PYTHONPATH=str(ROOT))
    r = subprocess.run([sys.executable, "-c", AS_USER_MAIN, "demo"], env=env, capture_output=True, text=True,
                       preexec_fn=lambda: os.umask(0))
    assert r.returncode == 0, r.stdout + r.stderr
    loose = [str(p.relative_to(home)) for p in home.rglob("*") if os.lstat(p).st_mode & 0o022]
    assert loose == []
    assert stat.S_IMODE(os.stat(home / "config" / "household.yaml").st_mode) == 0o644
    assert stat.S_IMODE(os.stat(home / "data" / "exports" / "manifest.json").st_mode) == 0o644
    assert stat.S_IMODE(os.stat(home / "data" / "finance.db").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(home / "secrets" / "web" / "passcodes").st_mode) == 0o600
    # nothing in it a group or others can reach: the install directory and each of its folders are the owner's alone
    assert stat.S_IMODE(os.stat(home).st_mode) == install.ROOT_MODE
    for d in install.DIRS:
        assert stat.S_IMODE(os.stat(home / d.path).st_mode) == d.mode, d.path


def test_the_history_ends_on_the_households_last_finished_day():
    morning = dt.datetime(2026, 10, 31, 14, 0, tzinfo=dt.UTC)       # 10:00 in New York
    evening = dt.datetime(2026, 10, 31, 22, 30, tzinfo=dt.UTC)      # 18:30 in New York
    assert demo_cli._end_day("America/New_York", 17, morning) == dt.date(2026, 10, 30)
    assert demo_cli._end_day("America/New_York", 17, evening) == dt.date(2026, 10, 31)


def test_a_directory_holding_other_files_is_refused_without_force(home, capsys):
    (home / "notes.txt").write_text("mine\n")
    assert demo_cli.run(out=lambda _: None, now=NOW) == 2
    assert "is not empty (notes.txt)" in capsys.readouterr().err
    assert sorted(p.name for p in home.iterdir()) == ["notes.txt"]


def test_the_settings_compose_yaml_and_ff_are_allowed(home, monkeypatch):
    for name in ("ff", "compose.yaml"):
        (home / name).write_text("x\n")
    install.write_env(home, {"FF_PORT": "18080"})
    monkeypatch.setattr(demo_cli, "_generator", lambda: (_ for _ in ()).throw(install.Refused("stop here")))
    assert demo_cli.run(out=lambda _: None, now=NOW) == 2       # got past the emptiness check


def test_force_never_writes_over_a_households_files(home, capsys):
    (home / "notes.txt").write_text("mine\n")
    (home / "config").mkdir()
    (home / "config" / "household.yaml").write_text("mine\n")
    assert demo_cli.run(force=True, out=lambda _: None, now=NOW) == 2
    assert "never writes over a household's" in capsys.readouterr().err
    assert (home / "config" / "household.yaml").read_text() == "mine\n"


def test_force_makes_it_beside_other_files(home):
    (home / "notes.txt").write_text("mine\n")
    out = []
    assert demo_cli.run(force=True, out=out.append, now=NOW) == 0
    assert (home / "notes.txt").read_text() == "mine\n"
    assert out[-1].startswith("Now run ./ff up")


def test_the_settings_already_there_are_kept(home):
    install.write_env(home, {"FF_PORT": "18080", "FF_IMAGE_ETL": "family-financials-etl:local"})
    assert demo_cli.run(out=lambda _: None, now=NOW) == 0
    env = install.read_env(home)
    assert env["FF_PORT"] == "18080" and env["FF_IMAGE_ETL"] == "family-financials-etl:local"
    assert env["FF_UID"] == str(USER)


def test_passphrases_are_four_distinct_draws():
    assert len({demo_cli.passphrase() for _ in range(50)}) == 50
    assert len(demo_cli.WORDS) == len(set(demo_cli.WORDS)) >= 200

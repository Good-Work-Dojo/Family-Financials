# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""A container that can write a folder another one reads can put a link, a FIFO or a folder at any name in it. The
store and the workers share the refresh mailbox, the workers read the family documents the store writes, and the
legacy layout keeps the learned rules beside those documents. Every such read and write is made at the name itself: a
planted link is never followed (its target is left as it was, and never read or copied), a FIFO is never waited on,
and a folder is never written into."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import refresh_runner as runner  # deploy/ is on sys.path (tests/conftest.py)
import refresh_state as rs
import scenario_store as store
from conftest import ROOT, hold_lock

from etl import pipeline

SECRET = b"SECRET-FEED-ACCESS-URL-0000"
NOW = 1_786_000_000.0


def secret_file(where: Path) -> Path:
    where.mkdir(parents=True, exist_ok=True)
    path = where / "simplefin_access_url"
    path.write_bytes(SECRET)
    return path


def promptly(fn, *args, seconds: float = 20):
    """fn(*args) in a thread that must end within `seconds`: its result, or the exception it raised."""
    out: list = []

    def call():
        try:
            out.append(fn(*args))
        except Exception as e:  # noqa: BLE001 - returned for the test to inspect
            out.append(e)

    t = threading.Thread(target=call, daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), "it waited on the path"
    return out[0]


# ---------------------------------------------------------------- the mailbox


@pytest.mark.parametrize("name, write", [
    (rs.STATUS_FILE, lambda d: rs.write_status(d, state="done")),
    (rs.REQUEST_FILE, lambda d: rs.write_request(d, nonce="n1", now=NOW)),
    (rs.LEDGER_FILE, lambda d: rs.save_ledger(d, [NOW])),
    (rs.SCHEDULE_FILE, lambda d: rs.write_schedule(d, rs.Schedule("UTC", (1, 9, 17), 0))),
])
def test_a_write_to_the_mailbox_replaces_a_planted_link_and_never_writes_through_it(tmp_path, name, write):
    target = secret_file(tmp_path / "secrets")
    d = tmp_path / "refresh"
    d.mkdir()
    (d / name).symlink_to(target)
    write(d)
    assert target.read_bytes() == SECRET
    assert not (d / name).is_symlink() and SECRET not in (d / name).read_bytes()
    assert sorted(p.name for p in d.iterdir()) == [name], "no temporary file left"


def test_a_read_of_the_mailbox_never_follows_a_link(tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"state": "done", "request_nonce": "n9"}))
    d = tmp_path / "refresh"
    d.mkdir()
    (d / rs.STATUS_FILE).symlink_to(target)
    (d / rs.REQUEST_FILE).symlink_to(target)
    assert rs.read_status(d) == {} and rs.read_request(d) is None
    schedule = tmp_path / "schedule-elsewhere.json"
    schedule.write_text(json.dumps({"timezone": "Pacific/Kiritimati", "hours": [1], "minute": 0}))
    (d / rs.SCHEDULE_FILE).symlink_to(schedule)
    assert rs.read_schedule(d) is None and rs.household_zone(d) == "UTC"


@pytest.mark.parametrize("kind", ["fifo", "folder"])
def test_a_fifo_or_a_folder_in_the_mailbox_reads_as_nothing_at_once(tmp_path, kind):
    d = tmp_path / "refresh"
    d.mkdir()
    for name in (rs.STATUS_FILE, rs.REQUEST_FILE, rs.SCHEDULE_FILE):
        os.mkfifo(d / name) if kind == "fifo" else (d / name).mkdir()
    assert promptly(rs.read_status, d) == {} and promptly(rs.read_request, d) is None
    assert promptly(rs.marker_time, d) is None and promptly(rs.read_schedule, d) is None


def test_the_ingest_marker_is_never_touched_through_a_link(tmp_path):
    target = secret_file(tmp_path / "secrets")
    os.utime(target, (1_700_000_000, 1_700_000_000))
    d = tmp_path / "refresh"
    d.mkdir()
    (d / rs.INGEST_MARKER).symlink_to(target)
    with pytest.raises(OSError):
        rs.touch_ingest_marker(d, NOW)
    assert target.stat().st_mtime == 1_700_000_000 and target.read_bytes() == SECRET
    assert rs.marker_time(d) is None, "a link is no marker"


def test_the_stores_probe_never_follows_a_link_at_the_runs_liveness(tmp_path):
    """A lock held on the link's target would read as a run in flight if the probe followed the link."""
    target = tmp_path / "elsewhere.lock"
    target.touch()
    d = tmp_path / "refresh"
    d.mkdir()
    (d / rs.LOCK_FILE).symlink_to(target)
    holder = hold_lock(target)
    try:
        assert rs.is_running(d / rs.LOCK_FILE) is False
    finally:
        holder.kill()
        holder.wait()


def test_a_run_goes_on_without_a_liveness_file_that_is_no_regular_file(tmp_path):
    """The refresh lock (the engine's) keeps runs apart; a link at the mailbox's liveness name only leaves the store's
    view the poorer, and is neither followed nor locked."""
    target = secret_file(tmp_path / "secrets")
    mailbox, engine = tmp_path / "refresh", tmp_path / "engine"
    mailbox.mkdir()
    (mailbox / rs.LOCK_FILE).symlink_to(target)
    ran = tmp_path / "ran"
    # during the run: is the link's target locked by anyone? (it is, if the worker opened the link)
    probe = (f"import fcntl; f = open('{target}'); "
             "print('locked' if fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB) is not None else 'free')")
    stub = tmp_path / "stub.sh"
    stub.write_text(f"#!/bin/sh\n{sys.executable} -c \"{probe}\" > {ran} 2>&1\n")
    stub.chmod(0o755)
    nonce = "00000000000000a1"                       # of the shape the store writes
    rs.write_request(mailbox, nonce=nonce, now=NOW - 5)
    assert runner.claim_and_run(mailbox, cmd=str(stub), snapshots=tmp_path / "snapshots", now=NOW,
                                engine=engine) == nonce
    assert ran.read_text().strip() == "free", "the worker took a lock through the link"
    assert target.read_bytes() == SECRET and (mailbox / rs.LOCK_FILE).is_symlink()


def test_the_refresh_lock_is_never_opened_through_a_link(tmp_path):
    target = secret_file(tmp_path / "secrets")
    mailbox, engine = tmp_path / "refresh", tmp_path / "engine"
    mailbox.mkdir()
    engine.mkdir()
    (engine / rs.LOCK_FILE).symlink_to(target)
    with pytest.raises(OSError):
        with rs.try_lock(engine / rs.LOCK_FILE, mailbox / rs.LOCK_FILE):
            pass
    assert target.read_bytes() == SECRET


def test_the_lock_helper_refuses_a_link_and_starts_nothing(tmp_path):
    target = secret_file(tmp_path / "secrets")
    (tmp_path / "engine").mkdir()
    (tmp_path / "engine" / rs.LOCK_FILE).symlink_to(target)
    ran = tmp_path / "ran"
    r = subprocess.run([sys.executable, rs.__file__, "hold-lock", "--lock", str(tmp_path / "engine" / rs.LOCK_FILE),
                        "--", "touch", str(ran)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "not a regular file" in r.stderr
    assert not ran.exists() and target.read_bytes() == SECRET


# ---------------------------------------------------------------- the store's documents


def test_the_store_never_reads_or_writes_a_family_document_through_a_link(tmp_path):
    target = secret_file(tmp_path / "secrets")
    path = tmp_path / "family" / "answers.json"
    path.parent.mkdir()
    path.symlink_to(target)
    assert store.read_store(path, store.empty_answers) == store.empty_answers()
    store.write_store(path, b'{"answers": []}')
    assert target.read_bytes() == SECRET and not path.is_symlink() and path.read_bytes() == b'{"answers": []}'


def test_a_fifo_at_a_family_documents_name_is_served_empty_at_once(tmp_path):
    path = tmp_path / "answers.json"
    os.mkfifo(path)
    assert promptly(store.read_store, path, store.empty_answers) == store.empty_answers()


# ---------------------------------------------------------------- a whole ingest


def test_an_ingest_never_reads_or_copies_what_a_planted_link_names_and_never_waits_on_a_fifo(tmp_path, monkeypatch):
    """The minimal household under FF_HOME, with the learned rules' name linked to the feed credential and a FIFO at
    the review answers' name: the ingest ends, no file under state/ or data/ holds the credential's bytes, and the
    quality report says the learned rules' file is not a regular file."""
    home = tmp_path / "home"
    shutil.copytree(ROOT / "examples" / "minimal", home)
    target = secret_file(home / "secrets" / "feed")
    (home / "state" / "engine").mkdir(parents=True)
    (home / "state" / "family").mkdir()
    (home / "state" / "engine" / "learned_rules.json").symlink_to(target)
    os.mkfifo(home / "state" / "family" / "answers.json")
    monkeypatch.setenv("FF_HOME", str(home))
    assert promptly(pipeline.run, seconds=120) == 0
    for root in (home / "state", home / "data"):
        for p in root.rglob("*"):
            if p.is_file() and not p.is_symlink():
                assert SECRET not in p.read_bytes(), p
    assert target.read_bytes() == SECRET
    checks = {c["name"]: c for c in json.loads((home / "data" / "exports" / "quality_report.json").read_text())["checks"]}
    assert checks["learned_rules.load"]["status"] == "warn"
    assert "not a regular file" in checks["learned_rules.load"]["detail"]
    assert checks["answers.load"]["status"] == "warn"

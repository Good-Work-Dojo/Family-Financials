# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""etl.pipeline.ingest_lock — the guard that stops two ingests from racing.

Worth its own file because the failure it prevents is silent: db.finalize()
re-derives finance.db.tmp by name, so the loser of a race promotes the winner's
half-written database over the real one and nothing errors.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from etl.pipeline import ingest_lock


def hold(path: Path) -> subprocess.Popen:
    """Hold the same flock from another process, returning only once it's held."""
    code = ("import fcntl,sys;f=open(sys.argv[1],'w');fcntl.flock(f,fcntl.LOCK_EX);"
            "sys.stdout.write('x');sys.stdout.flush();sys.stdin.read()")
    p = subprocess.Popen([sys.executable, "-c", code, str(path)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert p.stdout.read(1) == b"x"
    return p


def test_lock_is_acquired_and_released(tmp_path):
    lock = tmp_path / "sub" / ".ingest.lock"
    with ingest_lock(lock):
        assert lock.exists()
    with ingest_lock(lock):      # released on exit, so a second run gets it
        pass


def test_a_second_ingest_gives_up_instead_of_corrupting_the_db(tmp_path):
    lock = tmp_path / ".ingest.lock"
    holder = hold(lock)
    try:
        with pytest.raises(RuntimeError, match="refusing to run a second one"):
            with ingest_lock(lock, wait_s=0):
                pytest.fail("must not enter the body while another run holds it")
    finally:
        holder.kill()
        holder.wait()
    with ingest_lock(lock, wait_s=0):   # holder died -> kernel released it
        pass


def test_the_lock_is_released_when_the_body_raises(tmp_path):
    lock = tmp_path / ".ingest.lock"
    with pytest.raises(ValueError):
        with ingest_lock(lock):
            raise ValueError("ingest blew up")
    with ingest_lock(lock, wait_s=0):
        pass

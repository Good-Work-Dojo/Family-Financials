# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""deploy/safe_files.py: the reads and writes that never go through a link, for the files two containers share (the
refresh mailbox, the family documents) and the ones a worker keeps. A planted link, FIFO or folder at a file's name is
refused or replaced, never followed, and nothing waits on it."""
from __future__ import annotations

import fcntl
import os
import stat
import threading

import pytest
import safe_files as sf  # deploy/ is on sys.path (tests/conftest.py)

from etl import files as etl_files

SECRET = b"the feed's access URL, which no write may reach"


def secret(tmp_path):
    path = tmp_path / "secret"
    path.write_bytes(SECRET)
    return path


def promptly(fn, *args):
    """fn(*args) in a thread that must end within 10 s: (result, exception)."""
    out: list = []

    def call():
        try:
            out.append((fn(*args), None))
        except Exception as e:  # noqa: BLE001 - the test inspects it
            out.append((None, e))

    t = threading.Thread(target=call, daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive(), "it waited on the path"
    return out[0]


def test_a_regular_file_is_read_whole(tmp_path):
    (tmp_path / "doc.json").write_bytes(b'{"a": 1}')
    assert sf.read_regular(tmp_path / "doc.json") == b'{"a": 1}'


def test_nothing_there_is_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        sf.read_regular(tmp_path / "doc.json")


@pytest.mark.parametrize("kind", ["link", "dangling-link", "fifo", "folder"])
def test_what_is_no_regular_file_is_refused_at_once_and_never_followed(tmp_path, kind):
    path = tmp_path / "doc.json"
    if kind == "link":
        path.symlink_to(secret(tmp_path))
    elif kind == "dangling-link":
        path.symlink_to(tmp_path / "gone")
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.mkdir()
    result, error = promptly(sf.read_regular, path)
    assert result is None and isinstance(error, OSError) and not isinstance(error, FileNotFoundError)
    assert error.strerror == sf.NOT_REGULAR


def test_a_file_over_the_cap_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(sf, "MAX_BYTES", 8)
    (tmp_path / "doc.json").write_bytes(b"123456789")
    with pytest.raises(OSError) as e:
        sf.read_regular(tmp_path / "doc.json")
    assert e.value.strerror == sf.TOO_LARGE
    (tmp_path / "doc.json").write_bytes(b"12345678")
    assert sf.read_regular(tmp_path / "doc.json") == b"12345678"
    assert sf.read_regular(tmp_path / "doc.json", limit=8) == b"12345678"


def test_the_cap_is_sixteen_mib():
    assert sf.MAX_BYTES == 16 * 1024 * 1024


@pytest.mark.parametrize("kind", ["link", "dangling-link", "regular"])
def test_a_write_replaces_what_is_at_the_name_and_never_writes_through_it(tmp_path, kind):
    target = secret(tmp_path)
    path = tmp_path / "status.json"
    if kind == "link":
        path.symlink_to(target)
    elif kind == "dangling-link":
        path.symlink_to(tmp_path / "made-by-a-link")
    else:
        path.write_bytes(b"old")
    sf.write_atomic(path, b'{"state": "done"}')
    assert target.read_bytes() == SECRET and not (tmp_path / "made-by-a-link").exists()
    assert not path.is_symlink() and path.read_bytes() == b'{"state": "done"}'
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(["secret", "status.json"]), "no temporary file left"


def test_a_write_over_a_folder_fails_and_leaves_it(tmp_path):
    (tmp_path / "status.json").mkdir()
    (tmp_path / "status.json" / "inside").write_text("kept")
    with pytest.raises(OSError):
        sf.write_atomic(tmp_path / "status.json", b"{}")
    assert (tmp_path / "status.json" / "inside").read_text() == "kept"
    assert [p.name for p in tmp_path.iterdir()] == ["status.json"], "no temporary file left"


def test_a_written_file_has_the_mode_a_plain_open_gives(tmp_path):
    old = os.umask(0o027)
    try:
        sf.write_atomic(tmp_path / "doc.json", b"{}")
    finally:
        os.umask(old)
    assert stat.S_IMODE((tmp_path / "doc.json").stat().st_mode) == 0o640


def test_a_write_given_a_mode_has_that_mode_whatever_the_umask(tmp_path):
    old = os.umask(0o002)
    try:
        sf.write_atomic(tmp_path / "private.json", b"{}", mode=0o600)
    finally:
        os.umask(old)
    assert stat.S_IMODE((tmp_path / "private.json").stat().st_mode) == 0o600


def test_a_lock_file_given_a_mode_is_made_with_it(tmp_path):
    os.close(sf.open_lock(tmp_path / "private.lock", mode=0o600))
    os.close(sf.open_lock(tmp_path / "shared.lock"))
    assert stat.S_IMODE((tmp_path / "private.lock").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "shared.lock").stat().st_mode) == 0o644 & ~os.umask(os.umask(0))


def test_a_lock_file_is_made_when_absent_and_never_opened_through_a_link(tmp_path):
    fd = sf.open_lock(tmp_path / "refresh.lock")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)
    assert (tmp_path / "refresh.lock").read_bytes() == b""
    target = secret(tmp_path)
    (tmp_path / "linked.lock").symlink_to(target)
    os.mkfifo(tmp_path / "fifo.lock")
    for name in ("linked.lock", "fifo.lock"):
        result, error = promptly(sf.open_lock, tmp_path / name)
        assert result is None and isinstance(error, OSError) and error.strerror == sf.NOT_REGULAR, name
    assert target.read_bytes() == SECRET


def test_a_touch_sets_the_time_of_the_file_itself_and_refuses_a_link(tmp_path):
    sf.touch(tmp_path / "ingest_wanted", 1_786_000_002)
    assert (tmp_path / "ingest_wanted").stat().st_mtime == 1_786_000_002
    assert sf.regular_mtime(tmp_path / "ingest_wanted") == 1_786_000_002
    target = secret(tmp_path)
    before = target.stat().st_mtime
    (tmp_path / "linked").symlink_to(target)
    with pytest.raises(OSError):
        sf.touch(tmp_path / "linked", 1_786_000_002)
    assert target.stat().st_mtime == before and target.read_bytes() == SECRET
    assert sf.regular_mtime(tmp_path / "linked") is None and sf.regular_mtime(tmp_path / "absent") is None
    os.mkfifo(tmp_path / "fifo")
    result, error = promptly(sf.touch, tmp_path / "fifo", 1_786_000_002)
    assert isinstance(error, OSError)


def test_the_etl_uses_the_one_module_the_deploy_programs_use():
    assert (etl_files.NOT_REGULAR, etl_files.TOO_LARGE, etl_files.MAX_BYTES) == (sf.NOT_REGULAR, sf.TOO_LARGE,
                                                                                sf.MAX_BYTES)
    for name in ("read_regular", "write_atomic"):
        assert getattr(etl_files, name).__code__.co_filename == getattr(sf, name).__code__.co_filename, name

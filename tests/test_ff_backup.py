# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""`python -m etl backup` and `restore` (etl/backup_cli.py): a round trip gives back the household's own files with
the install table's modes; the archive is 0600, holds the credentials only when asked and never the sessions, Caddy's
files, the mailbox, the data or the refresh's log and locks; a restore over a household's files needs --force; and a
hostile archive (a path that climbs out, a link, an absolute path, a member outside what a backup holds, a device, a
hard link, more members than a backup has) is refused whole, before anything is written."""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import shutil
import stat
import tarfile
from pathlib import Path

import pytest
from ff_helpers import new_home

from etl import backup_cli, install

NOW = dt.datetime(2026, 11, 17, 18, 0, 5, tzinfo=dt.UTC)
INSTALL_CONFIG = "config"       # the install's config folder (a temporary tree's, never the repository's)


def cfg(rel: str) -> str:
    """A path under the install's config folder, as an archive member names it."""
    return f"{INSTALL_CONFIG}/{rel}"


def _household(home: Path) -> dict[str, bytes]:
    """A household's files, and what each holds."""
    install.ensure_dirs(home, out=lambda _: None)
    files = {
        cfg("household.yaml"): b"version: 1\nname: The Samples\n",
        cfg("presets/my_bank.yaml"): b"id: my_bank\n",
        "state/family/answers.json": b'{"answers": []}\n',
        "state/engine/learned_rules.json": b"{}\n",
        "store/simplefin/snapshots/accounts-20261117T170000Z.json": b'{"accounts": []}\n',
        "secrets/feed/simplefin_access_url": b"https://user:sample-secret@bridge.example.net/simplefin",
        "secrets/web/passcodes": b"alex:scrypt:15:8:1:x:y\n",
    }
    never = {
        "state/engine/fetch.log": b"log\n", "state/engine/fetch.log.1": b"old log\n",
        "state/engine/refresh.lock": b"", "state/engine/heartbeat": b"",
        "state/auth/sessions.json": b"{}", "state/caddy/data/x": b"x", "state/refresh/status.json": b"{}",
        "data/exports/manifest.json": b"{}", "imports/sample.csv": b"a,b\n",
    }
    for rel, data in {**files, **never}.items():
        path = home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return files


def _members(archive: Path) -> list[str]:
    with tarfile.open(archive) as tar:
        return [m.name for m in tar.getmembers() if m.isfile()]


def test_a_backup_holds_the_households_files_and_nothing_a_restore_must_not_bring_back(tmp_path, monkeypatch):
    home = new_home(tmp_path, monkeypatch)
    files = _household(home)
    archive = backup_cli.backup(out=lambda _: None, now=NOW)
    assert archive == home / "backups" / "ff-20261117T180005Z.tar.gz"
    assert stat.S_IMODE(os.stat(archive).st_mode) == 0o600
    names = _members(archive)
    assert sorted(names) == sorted(["ff-backup.json", *(f for f in files if not f.startswith("secrets/"))])
    with tarfile.open(archive) as tar:
        manifest = json.load(tar.extractfile("ff-backup.json"))
        assert all(m.uid == 0 and m.uname == "" for m in tar.getmembers())
    assert manifest["app_version"] == install.VERSION and manifest["secrets"] is False
    assert manifest["included"] == ["config", "state/family", "state/engine", "store"]
    assert not list((home / "backups").glob(".*"))


def test_include_secrets_adds_the_credentials(tmp_path, monkeypatch):
    home = new_home(tmp_path, monkeypatch)
    files = _household(home)
    archive = backup_cli.backup(include_secrets=True, out=lambda _: None, now=NOW)
    assert sorted(_members(archive)) == sorted(["ff-backup.json", *files])
    assert stat.S_IMODE(os.stat(archive).st_mode) == 0o600


def test_a_link_in_the_tree_is_left_out_and_named(tmp_path, monkeypatch):
    home = new_home(tmp_path, monkeypatch)
    _household(home)
    (home / "state" / "family" / "prefs.json").symlink_to(home / "secrets" / "feed" / "simplefin_access_url")
    said = []
    archive = backup_cli.backup(out=said.append, now=NOW)
    assert "state/family/prefs.json" not in _members(archive)
    assert "left out state/family/prefs.json: not a regular file" in said
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "snapshot.json").write_text("{}")
    (home / "store" / "simplefin" / "snapshots" / "accounts-20261117T170000Z.json").unlink()
    shutil.rmtree(home / "store")
    (home / "store").symlink_to(elsewhere)
    said = []
    archive = backup_cli.backup(out=said.append, now=NOW + dt.timedelta(seconds=1))
    assert not [n for n in _members(archive) if n.startswith("store")]
    assert "left out store: a link, not the install's own folder" in said


def test_a_round_trip_gives_the_files_back_with_the_tables_modes(tmp_path, monkeypatch):
    home = new_home(tmp_path, monkeypatch)
    files = _household(home)
    archive = backup_cli.backup(include_secrets=True, out=lambda _: None, now=NOW)
    target = new_home(tmp_path, monkeypatch, "restored")
    install.ensure_dirs(target, out=lambda _: None)
    copy = target / "backups" / archive.name
    copy.write_bytes(archive.read_bytes())
    said = []
    assert backup_cli.restore(str(copy), out=said.append) == 0
    for rel, data in files.items():
        assert (target / rel).read_bytes() == data, rel
        want = 0o644 if rel.startswith(cfg("")) else 0o600
        assert stat.S_IMODE(os.stat(target / rel).st_mode) == want, rel
    assert stat.S_IMODE(os.stat(target / "config" / "presets").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(target / "store" / "simplefin").st_mode) == 0o700
    assert said[-1] == "Now run ./ff up: the first refresh rebuilds the dashboard's data"
    assert not (target / "state" / "engine" / "fetch.log").exists()


def test_a_restore_over_a_households_files_needs_force(tmp_path, monkeypatch, capsys):
    home = new_home(tmp_path, monkeypatch)
    _household(home)
    archive = backup_cli.backup(out=lambda _: None, now=NOW)
    (home / "config" / "household.yaml").write_text("changed\n")
    assert backup_cli.run("restore", file=str(archive)) == 2
    assert "restore with --force" in capsys.readouterr().err
    assert (home / "config" / "household.yaml").read_text() == "changed\n"
    assert backup_cli.run("restore", file=str(archive), force=True) == 0
    assert (home / "config" / "household.yaml").read_text() == "version: 1\nname: The Samples\n"


# ------------------------------------------------------------------ hostile archives

def _hostile(path: Path, *members: tarfile.TarInfo, manifest: bool = True) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        if manifest:
            data = b'{"format": 1}'
            info = tarfile.TarInfo("ff-backup.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for m in members:
            tar.addfile(m, io.BytesIO(b"x" * m.size) if m.isreg() else None)
    return path


def _file(name: str, size: int = 1) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    return info


def _link(name: str, target: str, kind=tarfile.SYMTYPE) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type, info.linkname = kind, target
    return info


def _device(name: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type, info.devmajor, info.devminor = tarfile.CHRTYPE, 1, 3
    return info


def _snapshot(root: Path) -> dict[str, bytes | None]:
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() and not p.is_symlink() else None)
            for p in sorted(root.rglob("*"))}


HOSTILE = {
    "traversal": [_file(cfg("x.yaml")), _file(cfg("../../escaped"))],
    "symlink": [_file(cfg("x.yaml")), _link(cfg("presets"), "/etc"), _file(cfg("presets/passwd"))],
    "absolute": [_file(cfg("x.yaml")), _file("/tmp/ff-escaped")],
    "outside": [_file(cfg("x.yaml")), _file("data/exports/manifest.json")],
    "auth": [_file("state/auth/sessions.json")],
    "hardlink": [_file(cfg("x.yaml")), _link(cfg("y.yaml"), cfg("x.yaml"), tarfile.LNKTYPE)],
    "device": [_device("store/null")],
    "dot": [_file("./" + cfg("x.yaml"))],
}


@pytest.mark.parametrize("case", sorted(HOSTILE))
def test_a_hostile_archive_is_refused_whole_before_anything_is_written(tmp_path, monkeypatch, capsys, case):
    home = new_home(tmp_path, monkeypatch)
    install.ensure_dirs(home, out=lambda _: None)
    archive = _hostile(home / "backups" / "hostile.tar.gz", *HOSTILE[case])
    before = _snapshot(tmp_path)
    assert backup_cli.run("restore", file=str(archive)) == 2
    assert "nothing was restored" in capsys.readouterr().err
    assert _snapshot(tmp_path) == before
    assert not Path("/tmp/ff-escaped").exists()


# members each fine alone, refused together: written in this order, the first would land before the second failed
TOGETHER = {
    "a file where config/ belongs": [_file("store/WRITTEN_BEFORE"), _file(INSTALL_CONFIG)],
    "a file that is another member's folder": [_file("store/WRITTEN_BEFORE"), _file(cfg("a")), _file(cfg("a/b"))],
    "a file where state/ belongs": [_file("store/WRITTEN_BEFORE"), _file("state")],
    "a file where state/family belongs": [_file("store/WRITTEN_BEFORE"), _file("state/family")],
    "a name twice": [_file("store/WRITTEN_BEFORE"), _file(cfg("d.yaml"), 6), _file(cfg("d.yaml"), 7)],
    "a line break in a name": [_file("store/WRITTEN_BEFORE"), _file(cfg("a\nb.yaml"))],
}


@pytest.mark.parametrize("case", sorted(TOGETHER))
def test_members_that_conflict_with_each_other_write_nothing(tmp_path, monkeypatch, capsys, case):
    home = new_home(tmp_path, monkeypatch)
    install.ensure_dirs(home, out=lambda _: None)
    archive = _hostile(home / "backups" / "together.tar.gz", *TOGETHER[case])
    before = _snapshot(tmp_path)
    assert backup_cli.run("restore", file=str(archive)) == 2
    assert "nothing was restored" in capsys.readouterr().err
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("on_disk, member, said", [
    ("link folder", "store/simplefin/x.json", "is a link"),
    ("link file", "store/simplefin/x.json", "is a link"),
    ("file folder", "store/simplefin/x.json", "is a file where the backup has a folder"),
    ("folder file", "store/simplefin", "is not a file"),
])
def test_what_stands_on_disk_in_a_members_way_refuses_the_archive_before_a_byte_is_written(
        tmp_path, monkeypatch, capsys, on_disk, member, said):
    home = new_home(tmp_path, monkeypatch)
    install.ensure_dirs(home, out=lambda _: None)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if on_disk == "link folder":
        (home / "store" / "simplefin").symlink_to(elsewhere)
    elif on_disk == "link file":
        (home / "store" / "simplefin").mkdir()
        (home / "store" / "simplefin" / "x.json").symlink_to(elsewhere / "target")
    elif on_disk == "file folder":
        (home / "store" / "simplefin").write_text("in the way\n")
    else:
        (home / "store" / "simplefin").mkdir()
    archive = _hostile(home / "backups" / "b.tar.gz", _file(cfg("first.yaml")), _file(member))
    before = _snapshot(tmp_path)
    assert backup_cli.run("restore", file=str(archive), force=True) == 2
    err = capsys.readouterr().err
    assert said in err and "nothing was restored" in err
    assert _snapshot(tmp_path) == before
    assert list(elsewhere.iterdir()) == []


def test_a_large_member_is_copied_in_chunks(tmp_path, monkeypatch):
    home = new_home(tmp_path, monkeypatch)
    install.ensure_dirs(home, out=lambda _: None)
    size = 24 * 1024 * 1024
    archive = home / "backups" / "large.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = b'{"format": 1}'
        info = tarfile.TarInfo("ff-backup.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo("store/simplefin/snapshots/large.json")
        info.size = size
        tar.addfile(info, io.BytesIO(bytes(size)))
    reads = []
    real = tarfile.ExFileObject.read

    def read(self, n=-1):
        reads.append(n)
        return real(self, n)
    monkeypatch.setattr(tarfile.ExFileObject, "read", read)
    assert backup_cli.restore(str(archive), out=lambda _: None) == 0
    assert (home / "store" / "simplefin" / "snapshots" / "large.json").stat().st_size == size
    member_reads = [n for n in reads if n != -1 or len(reads) == 1]
    assert member_reads and all(0 < n <= 1024 * 1024 for n in member_reads)
    assert len(member_reads) >= size // (1024 * 1024)


def test_a_restore_that_would_leave_too_little_space_is_refused(tmp_path, monkeypatch, capsys):
    home = new_home(tmp_path, monkeypatch)
    _household(home)
    archive = backup_cli.backup(out=lambda _: None, now=NOW)
    target = new_home(tmp_path, monkeypatch, "restored")
    install.ensure_dirs(target, out=lambda _: None)
    copy = target / "backups" / archive.name
    copy.write_bytes(archive.read_bytes())
    usage = backup_cli.shutil.disk_usage(target)
    monkeypatch.setattr(backup_cli.shutil, "disk_usage",
                        lambda p: usage._replace(free=backup_cli.MIN_FREE_BYTES + 10))
    assert backup_cli.run("restore", file=str(copy)) == 2
    err = capsys.readouterr().err
    assert "would leave less than 512 MiB free" in err and "nothing was restored" in err
    assert list((target / "config").iterdir()) == []


def test_a_secret_put_in_config_is_left_out_unless_secrets_are_asked_for(tmp_path, monkeypatch):
    home = new_home(tmp_path, monkeypatch)
    _household(home)
    (home / "config" / "answers.yaml").write_text("passcode: sample\n")
    private = home / "config" / "notes.yaml"
    private.write_text("x: 1\n")
    os.chmod(private, 0o600)
    said = []
    archive = backup_cli.backup(out=said.append, now=NOW)
    names = _members(archive)
    assert cfg("answers.yaml") not in names and cfg("notes.yaml") not in names and cfg("household.yaml") in names
    assert sum(line.startswith("left out config/") and "--include-secrets keeps it" in line for line in said) == 2
    archive = backup_cli.backup(include_secrets=True, out=lambda _: None, now=NOW + dt.timedelta(seconds=1))
    assert {cfg("answers.yaml"), cfg("notes.yaml")} <= set(_members(archive))


def test_an_archive_with_more_members_than_a_backup_has_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(backup_cli, "MAX_MEMBERS", 50)
    home = new_home(tmp_path, monkeypatch)
    install.ensure_dirs(home, out=lambda _: None)
    archive = _hostile(home / "backups" / "bomb.tar.gz", *(_file(f"store/f{i:03d}") for i in range(60)))
    before = _snapshot(tmp_path)
    assert backup_cli.run("restore", file=str(archive)) == 2
    assert "more than 50 members" in capsys.readouterr().err
    assert _snapshot(tmp_path) == before


def test_an_archive_that_unpacks_past_the_bound_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(backup_cli, "MAX_TOTAL_BYTES", 1000)
    home = new_home(tmp_path, monkeypatch)
    install.ensure_dirs(home, out=lambda _: None)
    archive = _hostile(home / "backups" / "big.tar.gz", _file("store/a", 600), _file("store/b", 600))
    assert backup_cli.run("restore", file=str(archive)) == 2
    assert "unpacks to more" in capsys.readouterr().err
    assert list((home / "store").iterdir()) == []


def test_an_archive_without_the_manifest_is_not_a_backup(tmp_path, monkeypatch, capsys):
    home = new_home(tmp_path, monkeypatch)
    install.ensure_dirs(home, out=lambda _: None)
    archive = _hostile(home / "backups" / "plain.tar.gz", _file(cfg("x.yaml")), manifest=False)
    assert backup_cli.run("restore", file=str(archive)) == 2
    assert "no backup manifest" in capsys.readouterr().err
    assert not (home / "config" / "x.yaml").exists()


def test_a_file_that_is_no_archive_is_refused(tmp_path, monkeypatch, capsys):
    home = new_home(tmp_path, monkeypatch)
    install.ensure_dirs(home, out=lambda _: None)
    (home / "backups" / "x.tar.gz").write_bytes(b"not a tar")
    assert backup_cli.run("restore", file=str(home / "backups" / "x.tar.gz")) == 2
    assert "cannot be read" in capsys.readouterr().err


def test_backup_as_root_is_refused(tmp_path, monkeypatch, capsys):
    new_home(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "invoking_ids", lambda: (0, 0))
    assert backup_cli.run("backup") == 2
    assert "runs as root" in capsys.readouterr().err


def test_member_problems_are_named(tmp_path):
    assert backup_cli._member_problem(_file(cfg("../x"))) is not None
    assert backup_cli._member_problem(_file(cfg("a.yaml"))) is None
    assert backup_cli._member_problem(_file("secrets/feed/simplefin_access_url")) is None
    assert backup_cli._member_problem(_file("secrets/other/x")) is not None
    assert backup_cli._member_problem(_file("state/refresh/request.json")) is not None

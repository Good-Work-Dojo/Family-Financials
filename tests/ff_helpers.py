# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""What the install commands' tests share (etl/install.py and the commands built on it): an install directory with
FF_HOME at it, run as an invoking user who is not root, a cheap passcode hash, and the smallest household that
validates.

The suite runs as root inside the test image, and the commands refuse root and compare every directory's owner with
the invoking user's. `as_user` makes the commands see USER: the invoking ids are USER's, and a file the test process
owns reads as USER's (`install._lstat`), so what the commands make themselves passes as theirs; `others` names the
paths that read as another uid's (root-owned directories without needing a second real user)."""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import yaml
from conftest import ROOT

from etl import install, passcode_cli

REAL_UID = os.geteuid()
USER = 4242 if REAL_UID == 0 else REAL_UID
MINIMAL = ROOT / "examples" / "minimal"
SAMPLE_CSV = MINIMAL / "imports" / "demo_checking_0000-2025-01.csv"
# an invented passcode, used by every test that needs one
PASSCODE = "correct-horse-sample"


# `python -m etl` as a separate process (its own `__main__`, umask included) run as the user STUB_AS_UID names, through
# the same two seams as `as_user`: python -c AS_USER_MAIN <the etl arguments>
AS_USER_MAIN = (
    "import os, sys\n"
    "import etl.install as i\n"
    "user = int(os.environ['STUB_AS_UID']); real = os.geteuid(); lstat = os.lstat\n"
    "i.invoking_ids = lambda: (user, user)\n"
    "def seen(p):\n"
    "    st = lstat(p); f = list(st[:10]); f[4] = user if st.st_uid == real else st.st_uid\n"
    "    return os.stat_result(f)\n"
    "i._lstat = seen\n"
    "import runpy; sys.argv = ['etl', *sys.argv[1:]]; runpy.run_module('etl', run_name='__main__')\n")


def with_uid(st: os.stat_result, uid: int) -> os.stat_result:
    fields = list(st[:10])
    fields[4] = uid
    return os.stat_result(fields)


def as_user(monkeypatch, others: dict[Path, int] | None = None) -> None:
    """The commands run as USER; each path in `others` reads as owned by its uid."""
    named = {Path(p): uid for p, uid in (others or {}).items()}
    monkeypatch.setattr(install, "invoking_ids", lambda: (USER, USER))
    real = os.lstat

    def lstat(path):
        st = real(path)
        uid = named.get(Path(path))
        return with_uid(st, uid if uid is not None else USER if st.st_uid == REAL_UID else st.st_uid)
    monkeypatch.setattr(install, "_lstat", lstat)


def cheap_hash(monkeypatch) -> None:
    monkeypatch.setattr(passcode_cli, "HASHER", lambda secret, salt: hashlib.sha256(salt + secret).digest())


def new_home(tmp_path: Path, monkeypatch, name: str = "home") -> Path:
    """An empty install directory with FF_HOME at it, used as USER with the cheap hash."""
    home = tmp_path / name
    home.mkdir()
    monkeypatch.setenv("FF_HOME", str(home))
    monkeypatch.delenv("FF_HOST_HOME", raising=False)
    for key in ("FF_IMAGE_ETL", "FF_IMAGE_WEB", "FF_HOST_ADDRESSES", "FF_HOST_LISTENERS", "FF_STACK_PS"):
        monkeypatch.delenv(key, raising=False)
    as_user(monkeypatch)
    cheap_hash(monkeypatch)
    return home


def ready_home(tmp_path: Path, monkeypatch, name: str = "home") -> Path:
    """An install that `preflight` and `doctor` find in order: every directory, examples/minimal's household, its
    statement file, the settings for USER and one passcode."""
    home = new_home(tmp_path, monkeypatch, name)
    install.ensure_dirs(home, out=lambda _: None)
    for f in (MINIMAL / "config").iterdir():
        shutil.copyfile(f, home / "config" / f.name)
    shutil.copyfile(SAMPLE_CSV, home / "imports" / SAMPLE_CSV.name)
    install.write_env(home, install.base_env(home, {"FF_BIND": "127.0.0.1", "FF_PORT": "8080"}))
    shutil.copyfile(ROOT / "compose.yaml", home / "compose.yaml")
    old = os.umask(0o077)
    try:
        passcode_cli.add("alex", given=PASSCODE)
    finally:
        os.umask(old)
    return home


def write_answers(path: Path, doc: dict, mode: int = 0o600) -> Path:
    path.write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")
    os.chmod(path, mode)
    return path


def answers(**changes) -> dict:
    """An answers file's document for an invented household of two adults and a child, without a bank feed."""
    doc = {
        "household": {"name": "The Samples", "adults": ["Alex", "Sam"], "children": ["Robin"],
                      "joint_label": "Joint", "timezone": "America/New_York"},
        "modules": {"home_buying": False},
        "listen": {"network": False, "port": 8080},
        "passcode": PASSCODE,
    }
    doc.update(changes)
    return doc

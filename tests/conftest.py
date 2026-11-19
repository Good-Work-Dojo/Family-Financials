# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Shared test builders. make_txn is the keyword-only superset factory; each
test file keeps a thin wrapper pinning ITS historical defaults, so no assertion
ever sees a different Txn than before the consolidation (Txn.uid hashes
account|date|amount|description|seq — the wrapper defaults are part of test
identity and must stay pinned exactly). The same holds for `answer` (one review
answer in the store's shape).

The demo config. `DEMO_CONFIG_DIR` is the demo household's config (the `full` profile, tools/demo), written once
per process outside the repository; `load_demo` parses one of its files, `demo_copy` copies it for a test to edit and
`demo_config` loads it. A test that needs a valid config reads this one.

State isolation. A Pipeline reads scenarios/answers.json and scenarios/learned_rules.json and writes
learned_rules.json, data/ and data/exports/ - beside its config directory when nobody repoints them: in the
shared demo tree (DEMO_TREE) for `demo_config()`. So:
  * `demo_copy` is the one way a test edits config (a throwaway copy, never the original);
  * `isolate` / `isolated_pipeline` are the one way a test builds a Pipeline that enriches, persists,
    exports or runs: every one of those four paths lands in the test's tmp dir in one call, so a
    forgotten line cannot reach the developer's scenarios/, the repo's data/ or the shared demo tree;
  * `_repo_state_untouched` (autouse, session) fails the run if the repo's data/ or scenarios/, or the demo
    tree's config/, scenarios/ or data/, changed while the tests ran - the net under both. A bare
    `Pipeline(demo_config())` that only calls an `_export_*` method into a tmp dir (or only parses) touches
    none of them and needs no isolation."""
from __future__ import annotations

import atexit
import copy
import functools
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import yaml

from etl import config as config_mod
from etl import pipeline
from etl.config import PATH_VARIABLES, Config
from etl.models import Txn
from etl.pipeline import Pipeline
from tools.demo.household import TIMEZONE

ROOT = Path(__file__).resolve().parent.parent

# deploy/ holds standalone scripts, not an installed package. scenario_store.py
# does `import refresh_state`, which only resolves because the interpreter runs
# it by path (sys.path[0] = deploy/). Under pytest that never happens, so put
# deploy/ on the path here rather than in every test file that loads one.
sys.path.insert(0, str(ROOT / "deploy"))
# tools/publish_gate.py is a standalone script too; its tests import it by name.
sys.path.insert(0, str(ROOT / "tools"))


def hold_lock(path: Path) -> subprocess.Popen:
    """Hold an exclusive flock on `path` from ANOTHER process, and don't return until
    it provably has it: the child writes a byte once locked, so there is no
    sleep-polling and no under-load race with the child's exec. Callers own cleanup
    (`p.kill(); p.wait()`). The shared form of the holder that several test files
    still define locally — new tests should use this one."""
    code = ("import fcntl,sys;f=open(sys.argv[1],'w');fcntl.flock(f,fcntl.LOCK_EX);"
            "sys.stdout.write('x');sys.stdout.flush();sys.stdin.read()")
    p = subprocess.Popen([sys.executable, "-c", code, str(path)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert p.stdout.read(1) == b"x"
    return p


def make_txn(*, date="2025-06-15", account="demo_checking_0000", desc="X", cents=-1000,
             source_file="t.csv", bank_type=None, bank_category=None,
             type="purchase", vendor=None, category=None, subcategory=None,
             needs_review=False, **fields) -> Txn:
    """Superset Txn builder (union of every test file's historical builder).
    Extra enrichment fields (tags / is_internal / earner / seq / …) pass through
    **fields with an existence check so typos fail loudly."""
    t = Txn(date=date, account_id=account, description_raw=desc,
            amount_cents=cents, source_file=source_file,
            bank_type=bank_type, bank_category=bank_category)
    t.type = type
    t.vendor, t.category, t.subcategory = vendor, category, subcategory
    t.needs_review = needs_review
    for k, v in fields.items():
        if not hasattr(t, k):
            raise TypeError(f"Txn has no field {k!r}")
        setattr(t, k, v)
    return t


# ------------------------------------------------------------------ review answers

def answer(scope: str, target: Txn | str, set_: dict, answered_at: str) -> dict:
    """One review answer in the store's shape (scenarios/answers.json, deploy/scenario_store.py):
    scope "txn" names a Txn (or its uid), scope "vendor" a vendor name."""
    if scope == "txn":
        uid = target if isinstance(target, str) else target.uid
        return {"key": f"txn:{uid}", "scope": "txn", "txn_id": uid, "set": dict(set_), "answered_at": answered_at}
    if scope == "vendor":
        return {"key": f"vendor:{target}", "scope": "vendor", "vendor": target, "set": dict(set_),
                "answered_at": answered_at}
    raise ValueError(f"unknown answer scope {scope!r}")


def answers_doc(*entries: dict, updated_at: str = "2026-07-16T10:00:00Z") -> dict:
    """An answers.json document holding `entries`."""
    return {"version": 1, "updated_at": updated_at, "answers": list(entries)}


# ------------------------------------------------------------------ config + pipeline isolation

def write_household(d: Path, *, adults: Iterable[str] = ("Alex", "Sam"), children: Iterable[str] = (),
                    joint: str | None = "Joint", timezone: str | None = TIMEZONE, surname: str | None = None,
                    **extra: Any) -> Path:
    """Write household.yaml into the synthetic config dir `d` (created if absent) and return its path: `adults`
    then `children` as members without birth years, `joint` as the joint label, `timezone`, and `surname` when
    given. Each `extra` key is written as given and replaces a default (`members=[...]` writes members with birth
    years or a malformed list); a None value writes the key as null."""
    doc: dict[str, Any] = {
        "version": 1,
        "members": [{"name": n, "role": "adult"} for n in adults] + [{"name": n, "role": "child"} for n in children],
        "joint_label": joint,
        "timezone": timezone,
    }
    if surname is not None:
        doc["surname"] = surname
    doc.update(extra)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "household.yaml"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), "utf-8")
    return path


def isolate(cfg: Config, tmp_path: Path, *, answers: Iterable[dict] | None = None,
            answers_updated_at: str = "2026-07-16T10:00:00Z", prior: dict | None = None) -> Config:
    """Point every state path the pipeline reads or writes into tmp_path - answers.json, learned_rules.json,
    manual_assets.json, data/ and data/exports/ - and return `cfg`. `answers` (when given, even empty) is written as
    the answers doc; `prior` (when given) as the learned doc already on disk. Absent files mean no answers / nothing
    learned / no asset entered in the app. Nothing else of `cfg` changes."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg.answers_path = tmp_path / "answers.json"
    cfg.learned_path = tmp_path / "learned_rules.json"
    cfg.manual_assets_path = tmp_path / "manual_assets.json"
    cfg.data_dir = tmp_path / "data"
    cfg.exports_dir = cfg.data_dir / "exports"
    if answers is not None:
        cfg.answers_path.write_text(json.dumps(answers_doc(*answers, updated_at=answers_updated_at)), "utf-8")
    if prior is not None:
        cfg.learned_path.write_text(json.dumps(prior), "utf-8")
    return cfg


def isolated_pipeline(tmp_path: Path, rows: list[Txn], *, cfg: Config | None = None,
                      windows: dict[str, tuple[str, str]], answers: Iterable[dict] | None = (),
                      answers_updated_at: str = "2026-07-16T10:00:00Z", prior: dict | None = None) -> Pipeline:
    """A Pipeline over synthetic `rows` (the same list object, so a test can read its Txns back) with
    `cfg` (default: the demo config) isolated into tmp_path by `isolate`, and the coverage `windows`
    each test file pins."""
    cfg = isolate(cfg or demo_config(), tmp_path, answers=answers,
                  answers_updated_at=answers_updated_at, prior=prior)
    p = Pipeline(cfg)
    p.windows = dict(windows)
    p.txns = rows
    return p


# ------------------------------------------------------------------ the environment

@pytest.fixture(scope="session", autouse=True)
def _no_path_variables():
    """No variable that relocates the ETL's files (`PATH_VARIABLES`) reaches any test, whatever the shell running the
    suite has set: every Config a test builds finds its files where the test says. `FF_NOW`, the pinned clock, goes
    too, so a shell's pinned time never reaches a test. Session-scoped so the module- and session-scoped fixtures
    that build a Config see the cleared environment too; a test that wants a variable sets it with monkeypatch."""
    with pytest.MonkeyPatch.context() as mp:
        for name in (*PATH_VARIABLES, "FF_NOW"):
            mp.delenv(name, raising=False)
        yield


# The place the ETL looks for a config when no argument and no variable names one, as etl/config.py defines it, read
# before the guard below moves it.
_DEFAULT_CONFIG_PLACE: Path = vars(config_mod)["CONFIG_DIR"]


@pytest.fixture(scope="session", autouse=True)
def _no_default_config(tmp_path_factory):
    """For the whole run the default config place (config/ beside the etl package) is a directory that does not
    exist, so a test that calls main(), Paths(), Config() or FeedConfig() with nothing set never reads a developer's
    own, git-ignored config/. The one test that pins the real place asks for `default_config_place`."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config_mod, "CONFIG_DIR", tmp_path_factory.mktemp("no-default-config") / "config")
        yield


@pytest.fixture
def default_config_place(monkeypatch) -> Path:
    """The real default config place, put back for one test."""
    monkeypatch.setattr(config_mod, "CONFIG_DIR", _DEFAULT_CONFIG_PLACE)
    return _DEFAULT_CONFIG_PLACE


# ------------------------------------------------------------------ the demo household

# The day the demo household is generated to, and the pinned clock its ingest runs at: an hour after the day's last
# feed snapshot.
DEMO_END = date(2026, 10, 31)
DEMO_NOW = "2026-10-31T18:00:00"


def _write_demo_config() -> Path:
    """The `full` profile's config at DEMO_END, written into the `config` directory of a tree under a fresh temporary directory that
    is removed when the interpreter exits. Every test's Config resolves in the legacy layout (no path variable reaches
    a test), so accounts.yaml gains the one key that layout needs and the generator, which writes the FF_HOME layout,
    leaves out: `source_dir`, naming the empty `imports/` beside the config. The layout's other locations (state,
    data, the feed store) resolve inside the temporary directory too."""
    from tools.demo import config_writer, profiles
    temp = Path(tempfile.mkdtemp(prefix="ff-demo-config-"))
    atexit.register(shutil.rmtree, temp, ignore_errors=True)
    home = temp / "home"
    config_writer.write(profiles.get("full"), DEMO_END, home / "config")
    (home / "imports").mkdir()
    with open(home / "config" / "accounts.yaml", "a", encoding="utf-8") as f:
        f.write("source_dir: imports\n")
    return home / "config"


DEMO_CONFIG_DIR: Path = _write_demo_config()


@functools.cache
def _demo_doc(name: str) -> Any:
    """One demo config file parsed, kept for load_demo to copy and never handed out itself."""
    return yaml.safe_load((DEMO_CONFIG_DIR / name).read_text("utf-8"))


def load_demo(name: str) -> Any:
    """The parsed YAML of one file of the demo config, a fresh copy on every call: a test may change what it gets
    without reaching any other test."""
    return copy.deepcopy(_demo_doc(name))


def demo_copy(tmp_path: Path, edits: dict[str, Callable[[Any], None]] | None = None) -> Path:
    """A throwaway copy of the demo config at tmp_path/config, each YAML file in `edits` parsed, handed to its
    function (which mutates the doc in place) and written back. Returns the copy's dir, for Config(dir)."""
    d = tmp_path / "config"
    shutil.copytree(DEMO_CONFIG_DIR, d)
    for file, mutate in (edits or {}).items():
        doc = yaml.safe_load((d / file).read_text("utf-8"))
        mutate(doc)
        (d / file).write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), "utf-8")
    return d


def demo_config() -> Config:
    """The demo config loaded: a new Config on every call, so a test may repoint or change it."""
    return Config(DEMO_CONFIG_DIR)


class DemoHome(NamedTuple):
    path: Path          # the FF_HOME tree the generator wrote: config/, imports/, store/, truth.json
    truth: dict         # what the exports of that tree must equal (truth.json, as generate.run returned it)


class DemoExports(NamedTuple):
    exports: Path       # the exports directory of one ingest of a copy of the tree
    truth: dict


@pytest.fixture(scope="session")
def demo_home(tmp_path_factory) -> DemoHome:
    """The `full` profile generated to DEMO_END, once per session, outside the repository."""
    # imported here, so a session that never asks for the demo never loads it
    from tools.demo import generate
    home = tmp_path_factory.mktemp("demo") / "home"
    return DemoHome(home, generate.run("full", DEMO_END, home))


def ingest_demo(home: Path, now: str) -> Path:
    """Ingest the demo tree `home` in place, in-process, with FF_HOME at it and FF_NOW at `now` for the run only;
    return its exports directory."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("FF_HOME", str(home))
        mp.setenv("FF_NOW", now)
        assert pipeline.run() == 0
    return home / "data" / "exports"


@pytest.fixture(scope="session")
def demo_exports(demo_home: DemoHome, tmp_path_factory) -> DemoExports:
    """A copy of the demo tree ingested once (`ingest_demo` at DEMO_NOW); the generated tree itself stays as the
    generator wrote it."""
    home = tmp_path_factory.mktemp("demo-ingest") / "home"
    shutil.copytree(demo_home.path, home)
    return DemoExports(ingest_demo(home, DEMO_NOW), demo_home.truth)


# ------------------------------------------------------------------ the net under both

STATE_DIRS = ("data", "scenarios")
# The shared demo config's tree, which a test may read and never write: the config itself, and the state and data
# directories a Config of it resolves to in the legacy layout (beside the config directory).
DEMO_TREE = DEMO_CONFIG_DIR.parent
DEMO_TREE_DIRS = ("config", "scenarios", "data")


def state_fingerprint(root: Path = ROOT, dirs: Iterable[str] = STATE_DIRS) -> dict[str, object]:
    """Per directory of `dirs` under `root` (the repo's state dirs by default): None when absent, else every file
    under it as relpath -> (size, mtime_ns)."""
    out: dict[str, object] = {}
    for name in dirs:
        d = root / name
        out[name] = None if not d.exists() else {
            str(f.relative_to(root)): (f.stat().st_size, f.stat().st_mtime_ns)
            for f in sorted(d.rglob("*")) if f.is_file()}
    return out


def state_changes(before: dict[str, object], after: dict[str, object]) -> list[str]:
    """What changed between two fingerprints of the same directories, as relative paths only (never a file's
    content)."""
    changes: list[str] = []
    for name in before:
        b, a = before.get(name), after.get(name)
        if b is None and a is not None:
            changes.append(f"{name}/ created")
        elif b is not None and a is None:
            changes.append(f"{name}/ removed")
        elif b is not None and a is not None:
            changes += [f"{p} {'added' if p not in b else 'removed' if p not in a else 'rewritten'}"
                        for p in sorted(set(b) | set(a)) if b.get(p) != a.get(p)]
    return changes


@pytest.fixture(scope="session", autouse=True)
def _repo_state_untouched():
    """Fails the run when any test wrote into the repo's data/ or scenarios/, or into the shared demo config's tree
    (DEMO_TREE_DIRS under DEMO_TREE: a `demo_config()` that is not isolated resolves its answers, learned rules, data/
    and exports there, where every later test would read them). Names the paths only."""
    before = state_fingerprint()
    demo_before = state_fingerprint(DEMO_TREE, DEMO_TREE_DIRS)
    yield
    changed = state_changes(before, state_fingerprint())
    changed += [f"the demo config's tree: {c}"
                for c in state_changes(demo_before, state_fingerprint(DEMO_TREE, DEMO_TREE_DIRS))]
    assert not changed, ("a test wrote into the repo's own state or the shared demo config - build pipelines with "
                         "conftest.isolate / isolated_pipeline, and edit a demo_copy: " + ", ".join(changed))

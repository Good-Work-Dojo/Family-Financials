# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Where Config finds its files. Every location is resolved in one order: the constructor argument (where Config takes
one), then the location's own environment variable, then the FF_HOME layout, then the legacy layout beside the config
directory. With no config directory at all, every command that reads one says in one line where it looked and what to
set. The stack's own locations (secrets, sessions, the refresh's state, Caddy's state) follow the same order, and the
store and the refresh worker, which resolve theirs without the ETL, give the same answer. The ingest reads one Config
and lists in its manifest what it wrote. Synthetic config trees in tmp_path only.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest
import refresh_state as rs  # tests/conftest.py puts deploy/ on sys.path
import yaml
from conftest import ROOT, demo_copy, write_household

from etl import config as config_mod
from etl import pipeline
from etl.__main__ import main
from etl.config import PATH_VARIABLES, Config, FeedConfig, Paths
from etl.validate import ConfigError

_CHECKING = {"name": "Sample Checking", "institution": "Sample Bank", "owner": "Alex", "kind": "checking"}
_FILE_SOURCE = {"parser": "chase_checking", "glob": "sample-checking-*.csv"}
_ACCOUNTS = {"source_dir": "../statements", "simplefin": {"store_dir": "../feed-store"},
             "accounts": {"demo_checking_0000": {**_CHECKING, **_FILE_SOURCE}}}
_MISSING_SOURCE_DIR = "accounts.yaml: source_dir: missing — set it, or set FF_IMPORTS_DIR or FF_HOME"

# each attribute's place under FF_HOME
HOME_LAYOUT = {
    "dir": "config",
    "source_dir": "imports",
    "simplefin_dir": "store/simplefin",
    "simplefin_snapshots_dir": "store/simplefin/snapshots",
    "simplefin_credential_path": "secrets/feed/simplefin_access_url",
    "answers_path": "state/family/answers.json",
    "learned_path": "state/engine/learned_rules.json",
    "manual_assets_path": "state/family/manual_assets.json",
    "data_dir": "data",
    "exports_dir": "data/exports",
}
ATTRIBUTES = tuple(HOME_LAYOUT)

# location -> (its variable, each attribute it sets with that attribute's place under the variable's directory,
#              the Config keyword argument that sets it, or None)
LOCATIONS = {
    "config": ("FF_CONFIG_DIR", {"dir": ""}, "config_dir"),
    "imports": ("FF_IMPORTS_DIR", {"source_dir": ""}, "source_dir"),
    "store": ("FF_STORE_DIR", {"simplefin_dir": "simplefin", "simplefin_snapshots_dir": "simplefin/snapshots"}, None),
    "secrets": ("FF_SECRETS_DIR", {"simplefin_credential_path": "feed/simplefin_access_url"}, None),
    "state": ("FF_STATE_DIR", {"answers_path": "answers.json", "learned_path": "learned_rules.json",
                               "manual_assets_path": "manual_assets.json"}, None),
    "family": ("FF_STATE_FAMILY_DIR", {"answers_path": "answers.json", "manual_assets_path": "manual_assets.json"},
               None),
    "engine": ("FF_STATE_ENGINE_DIR", {"learned_path": "learned_rules.json"}, None),
    "data": ("FF_DATA_DIR", {"data_dir": "", "exports_dir": "exports"}, None),
}
SOURCES = ("argument", "variable", "home", "legacy")
CASES = [(loc, src) for loc in LOCATIONS for src in SOURCES if src != "argument" or LOCATIONS[loc][2]]


@pytest.fixture(autouse=True)
def _no_path_variables(monkeypatch):
    for name in PATH_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _write_config(d: Path, accounts: dict | None = None) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "accounts.yaml").write_text(yaml.safe_dump(_ACCOUNTS if accounts is None else accounts), "utf-8")
    write_household(d)
    return d


def _legacy(cfg_dir: Path, accounts: dict) -> dict[str, Path]:
    """The legacy layout, written as the expressions Config has always used: the statements folder and the feed
    store from accounts.yaml relative to the config directory's parent, the credential inside the store, the state
    in scenarios/ and the data in data/ beside the config directory."""
    base = cfg_dir.parent
    store = (base / (accounts.get("simplefin", {}) or {}).get("store_dir", "../simplefin")).resolve()
    return {
        "dir": cfg_dir,
        "source_dir": (base / accounts["source_dir"]).resolve(),
        "simplefin_dir": store,
        "simplefin_snapshots_dir": store / "snapshots",
        "simplefin_credential_path": store / "credentials" / "access_url",
        "answers_path": base / "scenarios" / "answers.json",
        "learned_path": base / "scenarios" / "learned_rules.json",
        "manual_assets_path": base / "scenarios" / "manual_assets.json",
        "data_dir": base / "data",
        "exports_dir": base / "data" / "exports",
    }


def _paths(cfg: Config) -> dict[str, Path]:
    return {a: getattr(cfg, a) for a in ATTRIBUTES}


@pytest.mark.parametrize("location, source", CASES)
def test_each_location_takes_the_first_source_that_is_set(tmp_path, monkeypatch, location, source):
    """Every source below the one under test is set as well, so each case also proves what that source beats; the
    other locations follow their own order (the FF_HOME layout when it is set, else the legacy layout)."""
    variable, own, keyword = LOCATIONS[location]
    legacy_cfg = _write_config(tmp_path / "legacy" / "config")
    home = tmp_path / "home"
    _write_config(home / "config")
    by_variable, by_argument = tmp_path / "by-variable", tmp_path / "by-argument"
    if location == "config":
        _write_config(by_variable)
        _write_config(by_argument)
    if source != "legacy":
        monkeypatch.setenv("FF_HOME", str(home))
    if source in ("argument", "variable"):
        monkeypatch.setenv(variable, str(by_variable))
    kwargs = {} if location == "config" else {"config_dir": legacy_cfg}
    if source == "argument":
        kwargs[keyword] = by_argument if keyword == "config_dir" else str(by_argument)
    # the config's last source is the default directory: here the synthetic legacy config stands in for it
    monkeypatch.setattr(config_mod, "CONFIG_DIR", legacy_cfg)

    cfg = Config(**kwargs)

    if source == "legacy":
        expected = _legacy(legacy_cfg, _ACCOUNTS)
    else:
        expected = {a: home / rel for a, rel in HOME_LAYOUT.items()}
        if location != "config":
            expected["dir"] = legacy_cfg
        for attr, rel in own.items():
            if source == "variable":
                expected[attr] = by_variable / rel
            elif source == "argument":
                expected[attr] = by_argument
    assert _paths(cfg) == expected
    assert all(p.is_absolute() for p in _paths(cfg).values())


def test_ff_home_alone_gives_the_home_layout(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write_config(home / "config", {"accounts": _ACCOUNTS["accounts"]})   # no source_dir key: not needed here
    monkeypatch.setenv("FF_HOME", str(home))
    assert _paths(Config()) == {a: home / rel for a, rel in HOME_LAYOUT.items()}


def test_a_per_path_variable_beats_ff_home_and_leaves_the_rest_to_it(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write_config(home / "config")
    monkeypatch.setenv("FF_HOME", str(home))
    for variable in ("FF_IMPORTS_DIR", "FF_STORE_DIR", "FF_SECRETS_DIR", "FF_STATE_DIR", "FF_DATA_DIR"):
        monkeypatch.setenv(variable, str(tmp_path / variable.lower()))
    cfg = Config()
    assert cfg.dir == home / "config"
    assert cfg.source_dir == tmp_path / "ff_imports_dir"
    assert cfg.simplefin_dir == tmp_path / "ff_store_dir" / "simplefin"
    assert cfg.simplefin_credential_path == tmp_path / "ff_secrets_dir" / "feed" / "simplefin_access_url"
    assert cfg.answers_path == tmp_path / "ff_state_dir" / "answers.json"
    assert cfg.exports_dir == tmp_path / "ff_data_dir" / "exports"


def test_the_legacy_layout_of_a_copied_config_is_unchanged(tmp_path):
    """The demo config, copied into tmp_path: every path sits where today's expressions put it, beside the copy (its
    values are read from the copy, never written here)."""
    d = demo_copy(tmp_path)
    accounts = yaml.safe_load((d / "accounts.yaml").read_text("utf-8"))
    assert _paths(Config(d)) == _legacy(d, accounts)


# The one line a missing config directory gives: where it was looked for, and advice that matches what named that
# place. The default place names both variables; a variable or an argument is named itself.
NO_CONFIG = ("{}: no config directory here — set FF_HOME to your household's folder (the one that holds config/) or "
             "FF_CONFIG_DIR to its config folder; examples/minimal is the smallest household that runs")
NAMED_BY = {
    "FF_CONFIG_DIR": "{}: FF_CONFIG_DIR names no directory — correct FF_CONFIG_DIR or unset it",
    "FF_HOME": "{}: FF_HOME names a folder with no config/ in it — correct FF_HOME or unset it",
    "argument": "{}: the config directory given is not a directory — give one that holds household.yaml and "
                "accounts.yaml",
}


def _nowhere(tmp_path: Path, monkeypatch) -> Path:
    """No argument, no variable and no directory at the legacy place: the legacy place points into tmp_path."""
    missing = tmp_path / "app" / "config"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", missing)
    return missing


def test_config_with_no_directory_says_where_it_looked(tmp_path, monkeypatch):
    missing = _nowhere(tmp_path, monkeypatch)
    for build in (Config, FeedConfig):
        with pytest.raises(ConfigError) as e:
            build()
        assert str(e.value).splitlines() == [NO_CONFIG.format(missing)]
    assert not (tmp_path / "app").exists()
    assert Paths().config_from == "default"


@pytest.mark.parametrize("variable", ["FF_CONFIG_DIR", "FF_HOME"])
def test_a_variable_naming_no_directory_names_that_variable(tmp_path, monkeypatch, variable):
    _nowhere(tmp_path, monkeypatch)
    monkeypatch.setenv(variable, str(tmp_path / "elsewhere"))
    looked = tmp_path / "elsewhere" / "config" if variable == "FF_HOME" else tmp_path / "elsewhere"
    for build in (Config, FeedConfig):
        with pytest.raises(ConfigError) as e:
            build()
        assert str(e.value).splitlines() == [NAMED_BY[variable].format(looked)]
    assert Paths().config_from == variable


def test_ff_config_dir_beside_ff_home_names_ff_config_dir(tmp_path, monkeypatch):
    """FF_CONFIG_DIR wins over FF_HOME's config/, so the advice names it even where FF_HOME holds a config."""
    _nowhere(tmp_path, monkeypatch)
    _write_config(tmp_path / "home" / "config")
    monkeypatch.setenv("FF_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FF_CONFIG_DIR", str(tmp_path / "elsewhere"))
    with pytest.raises(ConfigError) as e:
        Config()
    assert str(e.value).splitlines() == [NAMED_BY["FF_CONFIG_DIR"].format(tmp_path / "elsewhere")]


@pytest.mark.parametrize("given", ["missing", "a-file"])
def test_an_argument_naming_no_directory_names_the_argument(tmp_path, monkeypatch, given):
    """An argument wins over every variable, so the advice names it even where FF_CONFIG_DIR is set."""
    _nowhere(tmp_path, monkeypatch)
    monkeypatch.setenv("FF_CONFIG_DIR", str(_write_config(tmp_path / "by-variable")))
    path = tmp_path / given
    if given == "a-file":
        path.write_text("")
    for build in (Config, FeedConfig):
        with pytest.raises(ConfigError) as e:
            build(path)
        assert str(e.value).splitlines() == [NAMED_BY["argument"].format(path)]
    assert Paths(path).config_from == "argument"


def test_with_nothing_set_the_config_is_looked_for_beside_the_etl_package(default_config_place):
    """With no argument and no variable naming a place, the ETL looks for config/ beside the etl package: a checkout's
    own config/ (compose names the install's through FF_HOME instead)."""
    beside_etl = Path(config_mod.__file__).resolve().parents[1] / "config"
    paths = Paths()
    assert (paths.config_dir, paths.config_from) == (beside_etl, "default")


def test_no_test_reaches_the_default_place_unless_it_asks():
    """For every other test the default place is a directory that does not exist (conftest), so a call with nothing
    set never reads a developer's own, git-ignored config/."""
    beside_etl = Path(config_mod.__file__).resolve().parents[1] / "config"
    assert Paths().config_dir != beside_etl and not Paths().config_dir.exists()


@pytest.mark.parametrize("named_by", ["the-default-place", "FF_CONFIG_DIR"])
@pytest.mark.parametrize("args, code, stream", [
    (("validate",), 1, "out"),
    (("ingest",), 2, "err"),
    (("rules", "overlaps"), 2, "err"),
    (("rules", "preview", "--file", "candidates.yaml"), 2, "err"),
    (("rules", "starter-diff"), 2, "err"),
    (("simplefin", "list-accounts"), 2, "err"),
], ids=["validate", "ingest", "rules-overlaps", "rules-preview", "rules-starter-diff", "simplefin"])
def test_every_command_that_reads_the_config_prints_that_line_and_writes_nothing(tmp_path, monkeypatch, capsys, args,
                                                                                 code, stream, named_by):
    """Each command keeps the exit path of an invalid config: validate prints to stdout and exits 1, the others print
    to stderr and exit 2, before anything is read or written. The line's advice follows what named the place."""
    missing, line = _nowhere(tmp_path, monkeypatch), NO_CONFIG
    if named_by == "FF_CONFIG_DIR":
        missing, line = tmp_path / "named", NAMED_BY["FF_CONFIG_DIR"]
        monkeypatch.setenv("FF_CONFIG_DIR", str(missing))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["etl", *args])
    assert main() == code
    out = capsys.readouterr()
    printed, other = (out.out, out.err) if stream == "out" else (out.err, out.out)
    assert printed.splitlines() == [line.format(missing)] and other == ""
    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_ff_config_dir_alone_puts_every_other_location_beside_that_directory(tmp_path, monkeypatch):
    cfg_dir = _write_config(tmp_path / "elsewhere" / "config")
    monkeypatch.setenv("FF_CONFIG_DIR", str(cfg_dir))
    assert _paths(Config()) == _legacy(cfg_dir, _ACCOUNTS)


def test_ff_secrets_dir_alone_moves_only_the_feed_credential(tmp_path, monkeypatch):
    cfg_dir = _write_config(tmp_path / "legacy" / "config")
    monkeypatch.setenv("FF_SECRETS_DIR", str(tmp_path / "secrets"))
    expected = _legacy(cfg_dir, _ACCOUNTS)
    expected["simplefin_credential_path"] = tmp_path / "secrets" / "feed" / "simplefin_access_url"
    assert _paths(Config(cfg_dir)) == expected


def test_the_credential_stays_inside_a_relocated_feed_store_without_ff_secrets_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FF_STORE_DIR", str(tmp_path / "store"))
    cfg = Config(_write_config(tmp_path / "legacy" / "config"))
    assert cfg.simplefin_dir == tmp_path / "store" / "simplefin"
    assert cfg.simplefin_credential_path == tmp_path / "store" / "simplefin" / "credentials" / "access_url"


def test_a_relative_variable_is_taken_from_the_current_directory(tmp_path, monkeypatch):
    _write_config(tmp_path / "home" / "config")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FF_HOME", "home")
    monkeypatch.setenv("FF_DATA_DIR", "elsewhere/../data-here")
    cfg = Config()
    assert cfg.dir == tmp_path / "home" / "config"
    assert cfg.answers_path == tmp_path / "home" / "state" / "family" / "answers.json"
    assert cfg.data_dir == tmp_path / "data-here"
    assert all(p.is_absolute() for p in _paths(cfg).values())


def test_an_empty_variable_counts_as_unset(tmp_path, monkeypatch):
    d = _write_config(tmp_path / "legacy" / "config")
    for name in PATH_VARIABLES:
        monkeypatch.setenv(name, "")
    assert _paths(Config(d)) == _legacy(d, _ACCOUNTS)


def test_the_variables_are_the_sixteen_documented_names():
    assert PATH_VARIABLES == ("FF_HOME", "FF_CONFIG_DIR", "FF_IMPORTS_DIR", "FF_STORE_DIR", "FF_SECRETS_DIR",
                              "FF_STATE_DIR", "FF_DATA_DIR", "FF_STARTER_DIR", *STACK_VARIABLES,
                              "FF_STATE_FAMILY_DIR", "FF_STATE_ENGINE_DIR")


def test_the_state_splits_by_who_writes_only_under_ff_home_or_its_own_variables(tmp_path, monkeypatch):
    """The family documents and the engine's learned rules are two folders under FF_HOME (state/family/,
    state/engine/) or where their own variables name; FF_STATE_DIR, which comes after those variables and before
    FF_HOME, keeps them in one folder, as the legacy scenarios/ does, so a layout that names it moves nothing."""
    legacy_cfg = _write_config(tmp_path / "legacy" / "config")
    monkeypatch.setenv("FF_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FF_STATE_DIR", str(tmp_path / "one"))
    cfg = Config(legacy_cfg)
    assert {cfg.answers_path.parent, cfg.learned_path.parent, cfg.manual_assets_path.parent} == {tmp_path / "one"}
    monkeypatch.setenv("FF_STATE_FAMILY_DIR", str(tmp_path / "family"))
    cfg = Config(legacy_cfg)
    assert (cfg.answers_path.parent, cfg.manual_assets_path.parent) == (tmp_path / "family",) * 2
    assert cfg.learned_path == tmp_path / "one" / "learned_rules.json"
    monkeypatch.setenv("FF_STATE_ENGINE_DIR", str(tmp_path / "engine"))
    assert Config(legacy_cfg).learned_path == tmp_path / "engine" / "learned_rules.json"
    monkeypatch.delenv("FF_STATE_DIR")
    monkeypatch.delenv("FF_STATE_FAMILY_DIR")
    assert Config(legacy_cfg).answers_path == tmp_path / "home" / "state" / "family" / "answers.json"


# ---------------------------------------------------------------- the stack's own locations

# accessor -> (its variable, its place under FF_HOME, its legacy place beside the config directory, in the folder that
# holds config/, as the state and the data are: the repository, in a checkout)
STACK_LOCATIONS = {
    "secrets_web_dir": ("FF_SECRETS_WEB_DIR", "secrets/web", "secrets/web"),
    "secrets_ai_dir": ("FF_SECRETS_AI_DIR", "secrets/ai", "secrets/ai"),
    "secrets_alerts_dir": ("FF_SECRETS_ALERTS_DIR", "secrets/alerts", "secrets/alerts"),
    "state_auth_dir": ("FF_STATE_AUTH_DIR", "state/auth", "state/auth"),
    "state_refresh_dir": ("FF_STATE_REFRESH_DIR", "state/refresh", "var/refresh"),
    "state_caddy_dir": ("FF_STATE_CADDY_DIR", "state/caddy", "state/caddy"),
}
STACK_VARIABLES = tuple(v for v, _, _ in STACK_LOCATIONS.values())


@pytest.mark.parametrize("source", ["variable", "home", "legacy"])
@pytest.mark.parametrize("accessor", list(STACK_LOCATIONS))
def test_each_stack_location_takes_the_first_source_that_is_set(tmp_path, monkeypatch, accessor, source):
    """Every source below the one under test is set as well, so each case also proves what that source beats; the
    other stack variables are set too and move nothing but their own location."""
    variable, under_home, legacy = STACK_LOCATIONS[accessor]
    cfg_dir = tmp_path / "root" / "finance-app" / "config"
    home, named = tmp_path / "home", tmp_path / "named"
    if source != "legacy":
        monkeypatch.setenv("FF_HOME", str(home))
    if source == "variable":
        monkeypatch.setenv(variable, str(named))
    for other in STACK_VARIABLES:
        if other != variable:
            monkeypatch.setenv(other, str(tmp_path / other.lower()))
    expected = {"variable": named, "home": home / under_home,
                "legacy": tmp_path / "root" / "finance-app" / legacy}[source]
    found = getattr(Paths(cfg_dir), accessor)()
    assert found == expected and found.is_absolute()


def test_in_a_checkout_the_stack_locations_stay_inside_the_repository_and_the_refresh_is_the_dev_stores():
    """With no FF_HOME and no variable, a development checkout (its config/ at the top of the repository) keeps every
    stack location inside the repository, and the refresh state at var/refresh/, where `make scenario-store` (run at
    the top of the repository) puts the development store's."""
    repo = ROOT
    paths = Paths(repo / "config")
    for accessor in STACK_LOCATIONS:
        assert getattr(paths, accessor)().is_relative_to(repo), accessor
    assert paths.state_refresh_dir() == repo / "var" / "refresh"


def test_the_stack_locations_create_nothing(tmp_path, monkeypatch):
    for home in (False, True):
        if home:
            monkeypatch.setenv("FF_HOME", str(tmp_path / "home"))
        paths = Paths(tmp_path / "legacy" / "config")
        for accessor in STACK_LOCATIONS:
            getattr(paths, accessor)()
    assert list(tmp_path.iterdir()) == []


# The store and the refresh worker cannot import etl (the store's container holds no ETL code path): they resolve
# their directories in deploy/refresh_state.py. Under the same variables the two give one answer. Their legacy places
# are the present containers' mounts, and only the feed's snapshots coincide with Paths' there (the config at
# /app/finance-app/config): the family documents are /app/scenarios in the store and scenarios/ beside the config in
# the ETL, two mounts of one host directory; the refresh state is /app/var/refresh for the store and the worker, and
# var/refresh/ beside the config for Paths, the development checkout's place (no ETL step reads it in the containers).
TWINS = {
    "documents": lambda p: p.state_family_dir(),
    "refresh": lambda p: p.state_refresh_dir(),
    "snapshots": lambda p: p.store_dir({}) / "snapshots",
}


def test_the_store_and_the_etl_agree_on_the_family_documents_when_one_folder_holds_the_state(tmp_path, monkeypatch):
    """FF_STATE_DIR (the state in one folder, beside FF_HOME or not) names the family documents' folder for both."""
    monkeypatch.setenv("FF_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FF_STATE_DIR", str(tmp_path / "one"))
    assert rs.locate("documents") == Paths(tmp_path / "legacy" / "config").state_family_dir() == tmp_path / "one"


@pytest.mark.parametrize("source", ["variable", "home"])
@pytest.mark.parametrize("name", list(TWINS))
def test_the_deploy_side_resolves_each_shared_directory_as_paths_does(tmp_path, monkeypatch, name, source):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FF_HOME", "home")                                   # relative: both take it from the cwd
    if source == "variable":
        monkeypatch.setenv(rs.LOCATIONS[name].variable, "named")
    assert rs.locate(name) == TWINS[name](Paths(tmp_path / "legacy" / "config"))


def test_in_the_present_containers_the_legacy_snapshots_coincide():
    assert TWINS["snapshots"](Paths(Path("/app/finance-app/config"))) == Path(rs.LOCATIONS["snapshots"].legacy)


# ---------------------------------------------------------------- the ingest's own reads and writes

def _minimal_home(tmp_path: Path, monkeypatch) -> Path:
    """A copy of examples/minimal, FF_HOME at it."""
    home = tmp_path / "home"
    shutil.copytree(ROOT / "examples" / "minimal", home)
    monkeypatch.setenv("FF_HOME", str(home))
    return home


def test_ingest_builds_its_config_once_and_the_run_reads_the_one_it_checked(tmp_path, monkeypatch):
    """`python -m etl ingest` checks the config (the statements folder included) before anything is written; the run
    then reads that same Config instead of loading the files a second time."""
    _minimal_home(tmp_path, monkeypatch)
    built: list[bool] = []
    real = Config.__init__

    def counting(self, *args, **kwargs):
        built.append(kwargs.get("check_paths", False))
        real(self, *args, **kwargs)

    monkeypatch.setattr(Config, "__init__", counting)
    monkeypatch.setattr(sys, "argv", ["etl", "ingest"])
    assert main() == 0
    assert built == [True]


def test_a_refused_config_still_stops_the_ingest_with_every_problem_and_writes_nothing(tmp_path, monkeypatch, capsys):
    home = _minimal_home(tmp_path, monkeypatch)
    (home / "config" / "household.yaml").write_text("members: 7\n", "utf-8")
    monkeypatch.setattr(sys, "argv", ["etl", "validate"])
    assert main() == 1
    expected = capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["etl", "ingest"])
    assert main() == 2
    out = capsys.readouterr()
    assert out.err == expected and out.out == "" and expected.count("\n") > 1
    assert not (home / "data").exists() and not (home / "state").exists()


def _manifest_files(home: Path) -> list[str]:
    return json.loads((home / "data" / "exports" / "manifest.json").read_text("utf-8"))["files"]


def test_the_manifest_lists_the_files_this_run_wrote_and_leaves_an_older_one_alone(tmp_path, monkeypatch):
    """An export file a run of an older contract left in the folder is neither listed nor removed; a folder holding
    exactly this run's files gives the manifest it always gave (every file in it, sorted, itself included)."""
    home = _minimal_home(tmp_path, monkeypatch)
    assert pipeline.run() == 0
    exports = home / "data" / "exports"
    fresh = _manifest_files(home)
    assert fresh == sorted(p.name for p in exports.glob("*.json")) and "manifest.json" in fresh
    stale = exports / "budget_old.json"
    stale.write_text('{"left": "by an older run"}', "utf-8")
    assert pipeline.run() == 0
    assert _manifest_files(home) == fresh
    assert stale.read_text("utf-8") == '{"left": "by an older run"}'
    stale.unlink()
    assert pipeline.run() == 0
    assert _manifest_files(home) == fresh == sorted(p.name for p in exports.glob("*.json"))


def test_a_positional_config_dir_and_a_string_one_still_work(tmp_path):
    d = _write_config(tmp_path / "legacy" / "config")
    assert Config(d).dir == d
    assert Config(str(d), source_dir=str(tmp_path / "src")).source_dir == tmp_path / "src"


def test_every_path_attribute_stays_assignable(tmp_path):
    cfg = Config(_write_config(tmp_path / "legacy" / "config"))
    for attr in ATTRIBUTES:
        setattr(cfg, attr, tmp_path / "assigned" / attr)
    assert _paths(cfg) == {a: tmp_path / "assigned" / a for a in ATTRIBUTES}


@pytest.mark.parametrize("home", [False, True])
def test_config_creates_no_directory(tmp_path, monkeypatch, home):
    root = tmp_path / "root"
    _write_config(root / "config")
    if home:
        monkeypatch.setenv("FF_HOME", str(root))
    before = sorted(tmp_path.rglob("*"))
    Config(None if home else root / "config")
    assert sorted(tmp_path.rglob("*")) == before


# ---------------------------------------------------------------- accounts.yaml source_dir

def _without_source_dir(**accounts) -> dict:
    return {"accounts": accounts or {"demo_checking_0000": {**_CHECKING, **_FILE_SOURCE}}}


def test_a_missing_source_dir_is_an_error_in_the_legacy_layout_when_a_file_source_needs_it(tmp_path):
    d = _write_config(tmp_path / "legacy" / "config", _without_source_dir())
    with pytest.raises(ValueError, match=f"^{re.escape(_MISSING_SOURCE_DIR)}$"):
        Config(d)


def test_a_feed_only_household_needs_no_source_dir(tmp_path):
    feed_only = {"demo_checking_0000": {**_CHECKING, "simplefin_id": "demo-feed-0000"},
                 "demo_savings_0000": {**_CHECKING, "kind": "savings", "name": "Sample Savings"}}
    cfg = Config(_write_config(tmp_path / "legacy" / "config", _without_source_dir(**feed_only)))
    assert cfg.source_dir is None
    assert cfg.data_dir == tmp_path / "legacy" / "data"


@pytest.mark.parametrize("how", ["FF_IMPORTS_DIR", "FF_HOME", "argument"])
def test_a_missing_source_dir_is_fine_when_the_statements_come_from_elsewhere(tmp_path, monkeypatch, how):
    d = _write_config(tmp_path / "legacy" / "config", _without_source_dir())
    statements = tmp_path / "statements-here"
    if how == "argument":
        cfg = Config(d, source_dir=str(statements))
    else:
        monkeypatch.setenv(how, str(statements))
        cfg = Config(d)
    assert cfg.source_dir == (statements / "imports" if how == "FF_HOME" else statements)


def test_rules_still_switches_off_the_state_files_a_variable_names(tmp_path, monkeypatch):
    from etl.rules_cli import rulebook_only

    state = tmp_path / "state"
    state.mkdir()
    (state / "answers.json").write_text("{}", "utf-8")
    monkeypatch.setenv("FF_STATE_DIR", str(state))
    cfg = Config(_write_config(tmp_path / "legacy" / "config"))
    assert cfg.answers_path == state / "answers.json"
    off = rulebook_only(cfg)
    assert not off.answers_path.exists() and not off.learned_path.exists()
    assert cfg.answers_path == state / "answers.json"     # the original is left alone


def test_the_cli_source_dir_flag_beats_ff_imports_dir(tmp_path, monkeypatch):
    """`python -m etl ingest --source-dir X` reaches Config as its source_dir argument through the module-level name
    pipeline.run builds with; the run stops right after Config is built, before anything is read or written."""

    class Built(Exception):
        pass

    seen = {}

    class Probe(Config):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            seen["cfg"] = self
            raise Built

    monkeypatch.setenv("FF_CONFIG_DIR", str(_write_config(tmp_path / "cfg")))
    monkeypatch.setenv("FF_IMPORTS_DIR", str(tmp_path / "from-variable"))
    monkeypatch.setattr(pipeline, "Config", Probe)
    monkeypatch.setattr(sys, "argv", ["etl", "ingest", "--source-dir", str(tmp_path / "from-flag")])
    with pytest.raises(Built):
        main()
    assert seen["cfg"].source_dir == tmp_path / "from-flag"

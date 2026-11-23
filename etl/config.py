# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Load and validate the YAML config files."""
from __future__ import annotations

import datetime as dt
import os
import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import yaml

from . import validate as validate_mod
from .enrich import renames as renames_mod
from .enrich import rules as rules_mod
from .enrich import splits as splits_mod
from .enrich import transfers as transfers_mod
from .exports import DEFAULT_COVERAGE_MIN_SHARE
from .models import (
    ACCOUNT_KINDS,
    ASSET_CLASSES,
    FALLBACK_CATEGORY,
    INCOME_CATEGORY,
    P2P_UNKNOWN_SUB,
    RESERVED_TAXONOMY,
    account_asset_class,
)
from .networth import manual_entry_problems
from .validate import (
    CONFIG_FILES,
    LIST_FILES,
    PRESETS_DIR,
    REQUIRED_FILES,
    ConfigError,
    Problem,
    Problems,
    Roster,
    earner_problem,
    is_number,
    is_text,
    iso_date,
)

if TYPE_CHECKING:
    from .importers.csv_preset import Preset

# config/ beside the etl package: where Config() looks when no argument and no variable names a directory (the
# developer layout: a source checkout with config/ beside it). The repository ships none; compose mounts the
# household's there.
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# The environment variables that move the install's files: FF_HOME holds every location in one tree (config/,
# imports/, store/simplefin/ and store/ai/, secrets/{feed,web,ai,alerts}/, state/ with family/, engine/, auth/, refresh/ and caddy/
# in it, data/) and each of the others moves one location (Paths).
PATH_VARIABLES = ("FF_HOME", "FF_CONFIG_DIR", "FF_IMPORTS_DIR", "FF_STORE_DIR", "FF_SECRETS_DIR", "FF_STATE_DIR",
                  "FF_DATA_DIR", "FF_STARTER_DIR", "FF_SECRETS_WEB_DIR", "FF_SECRETS_AI_DIR", "FF_SECRETS_ALERTS_DIR",
                  "FF_STATE_AUTH_DIR", "FF_STATE_REFRESH_DIR", "FF_STATE_CADDY_DIR", "FF_STATE_FAMILY_DIR",
                  "FF_STATE_ENGINE_DIR")

# the rule files a new household copies from: beside the etl package
STARTER_DIR = Path(__file__).resolve().parent.parent / "starter"


def _env_dir(name: str) -> Path | None:
    """The variable's directory as an absolute path (a relative value is taken from the current directory), or None
    when it is unset or empty."""
    value = os.environ.get(name, "")
    return Path(os.path.abspath(value)) if value else None


def _require_config_dir(paths: Paths) -> None:
    """A config directory that does not exist is one ConfigError saying where it was looked for and what named that
    place, never a missing-file problem for each required file: nothing else of a config can be checked without it."""
    if not paths.config_dir.is_dir():
        raise ConfigError([validate_mod.missing_config_dir_problem(paths.config_dir, paths.config_from)])


@dataclass(frozen=True)
class Source:
    """One source of an account's statement files (Config.txn_sources): the format its files are read with, the
    file-name pattern that finds them in the statements folder, and the first day it holds as ISO text (None for an
    account's first source, which holds every day before the next one's)."""
    format: str
    glob: str
    since: str | None = None


@dataclass(frozen=True)
class AiSettings:
    """household.yaml `ai` as the AI step reads it (Config.ai): each key the file states, else its default
    (validate.AI_PROVIDERS, AI_DEFAULT_MODEL, AI_ACCOUNTS and AI_NUMBERS). `model` is None only with openai and no
    model, which the validator refuses."""
    provider: str = validate_mod.AI_PROVIDERS[0]
    model: str | None = validate_mod.AI_DEFAULT_MODEL
    base_url: str | None = None
    accounts: str = validate_mod.AI_ACCOUNTS[0]
    min_seen: int = validate_mod.AI_NUMBERS["min_seen"][0]
    confidence: float = validate_mod.AI_NUMBERS["confidence"][0]
    vendors_per_run: int = validate_mod.AI_NUMBERS["caps.vendors_per_run"][0]
    runs_per_day: int = validate_mod.AI_NUMBERS["caps.runs_per_day"][0]
    never_send: tuple[str, ...] = ()


def ai_settings(doc) -> AiSettings:
    """The AiSettings of a household.yaml document. Lenient, as Config reads every block before it raises: a value of
    the wrong type reads as left out (validate.ai_problems reports it)."""
    block = doc.get("ai") if isinstance(doc, dict) else None
    if not isinstance(block, dict):
        return AiSettings()
    caps = block.get("caps") if isinstance(block.get("caps"), dict) else {}
    provider = block.get("provider")
    provider = provider if isinstance(provider, str) and provider in validate_mod.AI_PROVIDERS else AiSettings.provider
    model = block.get("model") if is_text(block.get("model")) else None
    if model is None and provider == "anthropic":
        model = validate_mod.AI_DEFAULT_MODEL
    accounts = block.get("accounts")

    def number(where: dict, key: str, path: str):
        default, _, _, whole = validate_mod.AI_NUMBERS[path]
        v = where.get(key)
        ok = isinstance(v, int) and not isinstance(v, bool) if whole else is_number(v)
        return v if ok else default

    return AiSettings(
        provider=provider, model=model,
        base_url=block.get("base_url") if is_text(block.get("base_url")) else None,
        accounts=accounts if isinstance(accounts, str) and accounts in validate_mod.AI_ACCOUNTS
        else AiSettings.accounts,
        min_seen=number(block, "min_seen", "min_seen"),
        confidence=float(number(block, "confidence", "confidence")),
        vendors_per_run=number(caps, "vendors_per_run", "caps.vendors_per_run"),
        runs_per_day=number(caps, "runs_per_day", "caps.runs_per_day"),
        never_send=tuple(w for w in block.get("never_send") or () if is_text(w))
        if isinstance(block.get("never_send"), list) else ())


def _names_files(meta) -> bool:
    """Whether an account entry reads statement files: a `parser:` and a `glob:`, or a `sources:` list with an entry
    (as Config.txn_sources has it)."""
    return isinstance(meta, dict) and bool((meta.get("parser") and meta.get("glob"))
                                           or (isinstance(meta.get("sources"), list) and meta["sources"]))


def _has_file_source(accounts_cfg: dict) -> bool:
    """Whether any account reads statement files (_names_files)."""
    accounts = accounts_cfg.get("accounts")
    return any(_names_files(m) for m in (accounts.values() if isinstance(accounts, dict) else ()))


class Paths:
    """Where the ETL's files live. Each location is taken from the first of: a Config argument (the config directory
    and the statements folder have one), the location's own variable, the FF_HOME layout, and the developer layout
    beside the config directory (a source checkout with config/ beside it), where every file is found with no variable
    set. An empty variable counts
    as unset. Nothing is created here: each directory is made by the code that first writes into it, and a config
    directory that does not exist is Config's one problem (validate.missing_config_dir_problem).

      location          variable               under FF_HOME                        developer layout
      config            FF_CONFIG_DIR          config/                              config/ beside the etl package
      statements        FF_IMPORTS_DIR         imports/                             accounts.yaml source_dir *
      feed store        FF_STORE_DIR **        store/simplefin/                     accounts.yaml simplefin.store_dir *
      AI store          FF_STORE_DIR **        store/ai/                            store/ai/ *
      feed credential   FF_SECRETS_DIR **      secrets/feed/simplefin_access_url    <feed store>/credentials/access_url
      state             FF_STATE_DIR           state/                               scenarios/ *
      family documents  FF_STATE_FAMILY_DIR †  state/family/                        scenarios/ *
      the engine's own  FF_STATE_ENGINE_DIR †  state/engine/                        scenarios/ *
      data              FF_DATA_DIR            data/                                data/ *
      web secrets       FF_SECRETS_WEB_DIR     secrets/web/                         secrets/web/ *
      AI secrets        FF_SECRETS_AI_DIR      secrets/ai/                          secrets/ai/ *
      alert secrets     FF_SECRETS_ALERTS_DIR  secrets/alerts/                      secrets/alerts/ *
      sessions          FF_STATE_AUTH_DIR      state/auth/                          state/auth/ *
      refresh state     FF_STATE_REFRESH_DIR   state/refresh/                       var/refresh/ * ***
      Caddy's state     FF_STATE_CADDY_DIR     state/caddy/                         state/caddy/ *

    *   relative to the parent of the directory the config was loaded from: in a checkout, the repository, so nothing
        resolves outside it.
    **  a root: the store is <root>/simplefin, the AI store <root>/ai and the credential
        <root>/feed/simplefin_access_url.
    *** the directory the Makefile's development store (`make scenario-store`) is given. The containers' own last
        resort places (/app/var/refresh and the rest) are the store's and the worker's, in deploy/refresh_state.py
        LOCATIONS.
    †   state/ is split by who may write: the family documents (answers, manual assets: the store writes them, the ETL
        only reads) and the engine's own (learned_rules.json, which only the ETL writes). FF_STATE_DIR, when set,
        names the one folder that holds both, as the developer layout's scenarios/ does; it comes after the location's own
        variable and before FF_HOME.

    `config_from` names what gave the config directory — "argument", "FF_CONFIG_DIR", "FF_HOME" or "default" — so the
    line for a missing one advises correcting what named it."""

    def __init__(self, config_dir: Path | str | None = None):
        self.home = _env_dir("FF_HOME")
        named = _env_dir("FF_CONFIG_DIR")
        if config_dir is not None:
            self.config_dir, self.config_from = Path(config_dir), "argument"
        elif named:
            self.config_dir, self.config_from = named, "FF_CONFIG_DIR"
        elif self.home:
            self.config_dir, self.config_from = self.home / "config", "FF_HOME"
        else:
            self.config_dir, self.config_from = CONFIG_DIR, "default"
        self.base = self.config_dir.parent    # the root of the developer layout

    def _located(self, variable: str, under_home: str) -> Path | None:
        """The location's variable, else its place under FF_HOME, else None (the developer layout decides)."""
        return _env_dir(variable) or (self.home / under_home if self.home else None)

    def imports_dir(self, accounts_cfg: dict, source_dir: str | None = None) -> Path | None:
        """The statements folder. accounts.yaml `source_dir` is read only in the developer layout, where a household
        whose accounts read no statement file may leave it out (None: there is nothing to find)."""
        if source_dir:
            return Path(source_dir)
        located = self._located("FF_IMPORTS_DIR", "imports")
        if located:
            return located
        if accounts_cfg.get("source_dir") is None:
            if _has_file_source(accounts_cfg):
                raise ValueError("accounts.yaml: source_dir: missing — set it, or set FF_IMPORTS_DIR or FF_HOME")
            return None
        return (self.base / accounts_cfg["source_dir"]).resolve()

    def imports_from_accounts_yaml(self, source_dir: str | None = None) -> bool:
        """Whether the statements folder is accounts.yaml's `source_dir` (the developer layout): no argument, variable or
        FF_HOME names it."""
        return not source_dir and self._located("FF_IMPORTS_DIR", "imports") is None

    def store_dir(self, accounts_cfg: dict) -> Path:
        """The feed's snapshot store: kept outside data/, so it survives `make clean` and the DB rebuilds from it. A
        null `simplefin:` block or `store_dir` reads as absent."""
        root = _env_dir("FF_STORE_DIR")
        if root:
            return root / "simplefin"
        if self.home:
            return self.home / "store" / "simplefin"
        sf = accounts_cfg.get("simplefin") or {}
        return (self.base / (sf.get("store_dir") or "../simplefin")).resolve()

    def ai_store_dir(self) -> Path:
        """The AI step's store: its answers and its run log, which only `python -m etl ai` writes and the ingest reads.
        Beside the feed's store under FF_STORE_DIR's root or FF_HOME; in the developer layout store/ai/ beside the config
        directory."""
        root = _env_dir("FF_STORE_DIR")
        if root:
            return root / "ai"
        if self.home:
            return self.home / "store" / "ai"
        return self.base / "store" / "ai"

    def credential_path(self, store_dir: Path) -> Path:
        """The feed's access URL. Without FF_SECRETS_DIR or FF_HOME it stays inside the feed store, wherever that is."""
        root = _env_dir("FF_SECRETS_DIR")
        if root:
            return root / "feed" / "simplefin_access_url"
        if self.home:
            return self.home / "secrets" / "feed" / "simplefin_access_url"
        return store_dir / "credentials" / "access_url"

    def state_dir(self) -> Path:
        """The state's root: FF_STATE_DIR's one folder, FF_HOME's state/ or the developer layout's scenarios/."""
        return self._located("FF_STATE_DIR", "state") or self.base / "scenarios"

    def _state_part(self, variable: str, part: str) -> Path:
        """A part of the state (the table's †): its variable, else FF_STATE_DIR (the one folder), else FF_HOME's
        state/<part>, else the developer layout's scenarios/."""
        return (_env_dir(variable) or _env_dir("FF_STATE_DIR")
                or (self.home / "state" / part if self.home else None) or self.base / "scenarios")

    def state_family_dir(self) -> Path:
        """The family documents the store writes and the ETL only reads: the review answers and the manual assets."""
        return self._state_part("FF_STATE_FAMILY_DIR", "family")

    def state_engine_dir(self) -> Path:
        """What only the ETL writes there: the learned rules (and the copy kept of a damaged file)."""
        return self._state_part("FF_STATE_ENGINE_DIR", "engine")

    def starter_dir(self, given: Path | str | None = None) -> Path:
        """The starter rule files `rules starter-diff` compares with: the argument, else FF_STARTER_DIR, else
        `starter/` beside the etl package."""
        return Path(given) if given else _env_dir("FF_STARTER_DIR") or STARTER_DIR

    def data_dir(self) -> Path:
        """The database and the exports."""
        return self._located("FF_DATA_DIR", "data") or self.base / "data"

    def presets_dir(self) -> Path:
        """The household's own statement layouts: the presets/ folder inside the config directory, wherever that is."""
        return self.config_dir / PRESETS_DIR

    def _stack_dir(self, variable: str, under_home: str, in_checkout: str) -> Path:
        """One of the stack's own locations: its variable, else its place under FF_HOME, else `in_checkout` (its place
        in the developer layout) beside the config directory, as the state and the data are (the table's *)."""
        return self._located(variable, under_home) or self.base / in_checkout

    def secrets_web_dir(self) -> Path:
        """The sign-in's secrets: the passcodes the store checks."""
        return self._stack_dir("FF_SECRETS_WEB_DIR", "secrets/web", "secrets/web")

    def secrets_ai_dir(self) -> Path:
        """The AI step's credential."""
        return self._stack_dir("FF_SECRETS_AI_DIR", "secrets/ai", "secrets/ai")

    def secrets_alerts_dir(self) -> Path:
        """The failure alerts' credentials."""
        return self._stack_dir("FF_SECRETS_ALERTS_DIR", "secrets/alerts", "secrets/alerts")

    def state_auth_dir(self) -> Path:
        """The store's sessions and devices."""
        return self._stack_dir("FF_STATE_AUTH_DIR", "state/auth", "state/auth")

    def state_refresh_dir(self) -> Path:
        """The refresh's state: request, status, lock, markers, the failure streak, the re-auth set and the log."""
        return self._stack_dir("FF_STATE_REFRESH_DIR", "state/refresh", "var/refresh")

    def state_caddy_dir(self) -> Path:
        """Caddy's own data and config."""
        return self._stack_dir("FF_STATE_CADDY_DIR", "state/caddy", "state/caddy")


@dataclass(frozen=True)
class HouseholdPresets:
    """What a household's presets folder holds (household_presets): the presets that load, by id; the names its preset
    files give (each file's name without .yaml, the formats of the release aside), which an account may name whether
    or not its file loads, so a broken preset is reported once, in its own file; and the problems found."""
    presets: dict[str, Preset]
    names: tuple[str, ...]
    problems: tuple[Problem, ...]


_OWN_ID_FIX = "give each preset an id of its own, and name its file after it"


def household_presets(folder: Path, taken: Collection[str]) -> HouseholdPresets:
    """The household's own CSV presets: each `*.yaml` file in `folder` (Paths.presets_dir) loaded as a shipped preset is
    (csv_preset.build_preset), except that it may leave `source` and `evidence` out. A file whose name starts with a dot
    is passed over. Problems, each naming presets/<file>, its key path, what is wrong and the fix: every one a preset
    file has; an id that is one of `taken` (the formats of the release: an account could not tell the two apart); an id
    two files state (on each file not named after it, whose name is already its own problem); a file named .yml or
    another spelling of .yaml, which would otherwise never be read; and a `presets` that is no folder or cannot be
    listed. A preset with a problem is left out of `presets`. No folder at all is no preset and no problem."""
    # imported at the call: the bank-feed commands load this module and never read a preset
    from .importers.csv_preset import build_preset, read_preset_doc

    problems = Problems()
    if not folder.exists():
        return HouseholdPresets({}, (), ())
    try:
        entries = sorted(folder.iterdir()) if folder.is_dir() else None
    except OSError as e:
        return HouseholdPresets({}, (), (Problem(PRESETS_DIR, "", f"could not be read ({e.strerror or e})",
                                                 "make it a folder this account can read"),))
    if entries is None:
        return HouseholdPresets({}, (), (Problem(PRESETS_DIR, "", "not a folder", "make it a folder of preset files "
                                                                                    "named <id>.yaml, or remove it"),))
    loaded: list[tuple[str, Path, object, Preset | None]] = []      # (file, path, the id it states, the preset)
    for path in entries:
        name = path.name
        if name.startswith("."):
            continue
        file = f"{PRESETS_DIR}/{name}"
        if path.suffix.casefold() in (".yml", ".yaml") and path.suffix != ".yaml":
            problems.add(Problem(file, "", "not read: a preset's file is named <id>.yaml", f"rename it {path.stem}.yaml"))
            continue
        if path.suffix != ".yaml":
            continue
        try:
            doc = read_preset_doc(path, file)
        except ConfigError as e:
            problems.extend(e.problems)
            loaded.append((file, path, None, None))
            continue
        stated = doc.get("id") if isinstance(doc, dict) else None
        try:
            preset = build_preset(doc, file, path.stem, household=True)
        except ConfigError as e:
            problems.extend(e.problems)
            preset = None
        loaded.append((file, path, stated, preset))
    by_id: dict[str, list[str]] = {}
    for file, _, stated, _ in loaded:
        if isinstance(stated, str):
            by_id.setdefault(stated, []).append(file)
    presets: dict[str, Preset] = {}
    for file, path, stated, preset in loaded:
        own_id = stated if isinstance(stated, str) else path.stem
        if own_id in taken:
            problems.add(Problem(file, "id", f"{own_id!r} is the name of a format this release reads",
                                 "give your preset an id of its own, and name its file after it"))
            continue
        others = [f for f in by_id.get(own_id, ()) if f != file]
        if others and own_id != path.stem:
            problems.add(Problem(file, "id", f"{own_id!r} is also the id of {', '.join(others)}", _OWN_ID_FIX))
            continue
        if preset is not None:
            presets[preset.id] = preset
    names = tuple(sorted({path.stem for _, path, _, _ in loaded} - set(taken)))
    return HouseholdPresets(presets, names, tuple(problems))

# household.yaml member roles; Config.owners lists the adults, then the joint label, then the children
MEMBER_ROLES = ("adult", "child")
# the birth years household.yaml takes
BIRTH_YEARS = range(1900, 2101)


def _registered_parsers() -> tuple[str, ...]:
    """The statement formats of the release an account's `parser:` may name, besides the household's own presets: the
    pipeline's TXN_PARSERS, the importers' registry (etl/importers/registry.py) as the pipeline copied it, with any
    format a test patched in. The pipeline imports this module, so the table is read when a Config is built, never when
    this module loads."""
    from .pipeline import TXN_PARSERS

    return tuple(TXN_PARSERS)


def _hidden(relative: Path) -> bool:
    """Whether a path in the statements folder is hidden, or inside a hidden folder: a part starting with a dot."""
    return any(part.startswith(".") for part in relative.parts)


def _readable_as(path: Path, test) -> bool:
    """`test(path)` (Path.is_file, Path.is_dir), False when the path cannot be looked at (a permission refused)."""
    try:
        return test(path)
    except OSError:
        return False


# the fixes of the problems the rule, rename and split compilers report (their messages name the entry and what is
# wrong in it)
_FIX_RULE = "correct the entry; docs/design/categorization-and-learned-rules.md describes the rule shapes"
_FIX_RENAME = "correct the entry; docs/design/renames.md describes renames"
_FIX_SPLIT = "correct the split; docs/design/splits.md describes split legs"
# A compiler names its entries after the list it was handed (`vendor_rules[3]`, `vendor_rules.renames[0]`,
# `category_rules.renames[0]`): the key path that entry has in its file.
_COMPILER_PATHS = (("vendor_rules.renames", "renames"), ("category_rules.renames", "renames"),
                   ("vendor_rules[", "patterns["))


def _collect(problems: Problems, file: str, fix: str, texts: list[str]) -> None:
    """The problems a collecting compiler appended (`<location>: <what>`, some with their own ` — <fix>`), recorded in
    `file` with the location as its key path and `fix` where they carry none."""
    for text in texts:
        for old, new in _COMPILER_PATHS:
            if text.startswith(old):
                text = new + text[len(old):]
                break
        problems.add(f"{file}: {text}" if " — " in text else f"{file}: {text} — {fix}")


def _date_at(node, steps: list[str]) -> None:
    """Below `node`, each value at the key path `steps` (`[]` any item of a list, `<name>` any key) that YAML built as
    a date becomes its ISO text, in place; a step the document does not have is skipped."""
    head, rest = steps[0], steps[1:]
    if head == "[]":
        items = list(enumerate(node)) if isinstance(node, list) else []
    elif not isinstance(node, dict):
        return
    elif head.startswith("<"):
        items = list(node.items())
    else:
        items = [(head, node[head])] if head in node else []
    for key, child in items:
        if rest:
            _date_at(child, rest)
        elif isinstance(child, dt.date) and not isinstance(child, dt.datetime):
            node[key] = child.isoformat()


def _dates_as_text(docs: dict[str, object]) -> None:
    """Every date-typed key of the registry (validate.KEYS) in the documents of `docs` (file -> its loaded document)
    holds ISO text afterwards, however the household wrote the day: an unquoted one is a date to the YAML loader, and
    what a later stage reads, or an export passes through, is text either way. Run on a valid config only."""
    for key in validate_mod.KEYS:
        if key.type == "date" and key.file in docs:
            _date_at(docs[key.file], re.findall(r"\[\]|[^.\[\]]+", key.path))


def _household_shape_problems(doc) -> list[Problem | str]:
    """household.yaml's top-level keys, each `household.yaml: <key>: <what> — <fix>`. `members` is checked by
    _member_problems, `timezone` by validate.timezone_problems."""
    if not isinstance(doc, dict):
        return ["household.yaml: top level: expected a mapping — write version, members, joint_label and "
                "timezone as `key: value` lines"]
    out: list[Problem | str] = [*validate_mod.version_problems("household.yaml", doc)]
    for key in ("name", "surname"):
        if key in doc and doc[key] is not None and not is_text(doc[key]):
            out.append(f"household.yaml: {key}: expected text — write a name, or leave the key out")
    fix = "the owner name used for jointly held accounts, e.g. Joint"
    if doc.get("joint_label") is None:
        out.append(f"household.yaml: joint_label: missing — set {fix}")
    elif not is_text(doc["joint_label"]):
        out.append(f"household.yaml: joint_label: expected text — set {fix}")
    if doc.get("modules") is not None and not isinstance(doc["modules"], dict):
        out.append("household.yaml: modules: expected a mapping — write `modules: {}`")
    nw = doc.get("net_worth")
    if nw is not None and not isinstance(nw, dict):
        out.append("household.yaml: net_worth: expected a mapping — e.g. `net_worth: {include_credit_cards: true}`")
    elif isinstance(nw, dict) and nw.get("include_credit_cards") is not None \
            and not isinstance(nw["include_credit_cards"], bool):
        out.append("household.yaml: net_worth.include_credit_cards: expected true or false — or leave it out (true)")
    return out


def _member_problems(members, joint_label) -> tuple[list[Problem | str], list[dict] | None, set[str]]:
    """household.yaml `members`: a list of {name, role: adult|child, birth_year?} with distinct names (compared
    casefolded, as rule conditions compare owners) written without spaces at their ends, at least one adult, no name
    equal to the joint label, and each birth year a whole year in BIRTH_YEARS. Returns the problems, the well-formed
    members (a mapping with a text name, written trimmed, that no earlier member has, and a known role; None when
    `members` is no list) and the names of the other members that have one: each such member's problem says that the
    keys naming it are not checked until it is fixed."""
    if members is None:
        return ["household.yaml: members: missing — list the people, e.g. `- {name: Alex, role: adult}`"], None, set()
    if not isinstance(members, list):
        return (["household.yaml: members: expected a list — one `- {name: ..., role: adult|child}` line per person"],
                None, set())
    out: list[Problem | str] = []
    seen: set[str] = set()
    good, unchecked = [], set()
    for i, m in enumerate(members):
        where = f"members[{i}]"
        if not isinstance(m, dict):
            out.append(f"household.yaml: {where}: expected a mapping — write `{{name: ..., role: adult|child}}`")
            continue
        name = m.get("name")
        bad: list[tuple[str, str, str]] = []           # (key path, what, fix): what makes the member unusable
        if not is_text(name):
            out.append(f"household.yaml: {where}.name: missing — give every member a name")
            name = None
        else:
            if name.casefold() in seen:
                bad.append((f"{where}.name", f"{name!r} is listed twice", "give each member a distinct name"))
            seen.add(name.casefold())
            if name != name.strip():
                bad.append((f"{where}.name", f"{name!r} has spaces at its start or end", f"write it as {name.strip()!r}"))
        if m.get("role") not in MEMBER_ROLES:
            bad.append((f"{where}.role", f"unknown role {m.get('role')!r}", f"one of: {', '.join(MEMBER_ROLES)}"))
        note = f" (until it is fixed, owner and earner keys naming {name.strip()!r} are not checked)" if name else ""
        out.extend(Problem("household.yaml", path, what, fix + note) for path, what, fix in bad)
        if name and bad:
            unchecked |= {name, name.strip()}
        elif name:
            good.append(m)
        year = m.get("birth_year")
        if year is not None and (isinstance(year, bool) or not isinstance(year, int) or year not in BIRTH_YEARS):
            out.append(f"household.yaml: {where}.birth_year: expected a year such as 1990 — or leave the key out")
    if not any(isinstance(m, dict) and m.get("role") == "adult" for m in members):
        out.append("household.yaml: members: no adult — at least one member needs `role: adult`")
    if is_text(joint_label) and joint_label.casefold() in seen:
        out.append(f"household.yaml: joint_label: {joint_label!r} is also a member's name — "
                   "use a label no member has, e.g. Joint")
    return out, good, unchecked


def _unknown_owner(file: str, path: str, who, owners: tuple[str, ...]) -> Problem:
    return Problem(file, path, f"unknown owner {who!r}", f"one of: {', '.join(owners)}")


def _account_owner_problems(accounts_cfg: dict, roster: Roster) -> list[Problem]:
    """accounts.yaml: every account's and every manual entry's `owner`."""
    out = []
    for a, meta in (accounts_cfg.get("accounts") or {}).items():
        if isinstance(meta, dict) and "owner" in meta and not roster.knows_owner(meta["owner"]):
            out.append(_unknown_owner("accounts.yaml", f"accounts.{a}.owner", meta["owner"], roster.owners))
    for i, e in enumerate(accounts_cfg.get("manual_entries") or []):
        if isinstance(e, dict) and "owner" in e and not roster.knows_owner(e["owner"]):
            out.append(_unknown_owner("accounts.yaml", f"manual_entries[{i}].owner", e["owner"], roster.owners))
    return out


def _account_kind_problems(accounts_cfg: dict) -> list[str]:
    """accounts.yaml: every account's `kind` is one of ACCOUNT_KINDS — an unknown kind would book nothing anywhere."""
    out = []
    for a, meta in (accounts_cfg.get("accounts") or {}).items():
        if not isinstance(meta, dict):
            continue
        kind = meta.get("kind")
        if kind is None:
            out.append(f"accounts.yaml: accounts.{a}.kind: missing — one of {', '.join(ACCOUNT_KINDS)}")
        elif kind not in ACCOUNT_KINDS:
            out.append(f"accounts.yaml: accounts.{a}.kind: unknown kind {kind!r} — one of {', '.join(ACCOUNT_KINDS)}")
    return out


def _unknown_class(path: str, cls) -> str:
    return f"accounts.yaml: {path}: unknown class {cls!r} — one of {', '.join(ASSET_CLASSES)}"


def _asset_class_problems(accounts_cfg: dict) -> list[str]:
    """accounts.yaml: each account's and each manual entry's `asset_class` is one of ASSET_CLASSES."""
    out = []
    for a, meta in (accounts_cfg.get("accounts") or {}).items():
        if isinstance(meta, dict) and meta.get("asset_class") is not None and meta["asset_class"] not in ASSET_CLASSES:
            out.append(_unknown_class(f"accounts.{a}.asset_class", meta["asset_class"]))
    for i, e in enumerate(accounts_cfg.get("manual_entries") or []):
        if isinstance(e, dict) and "asset_class" in e and e["asset_class"] not in ASSET_CLASSES:
            out.append(_unknown_class(f"manual_entries[{i}].asset_class", e["asset_class"]))
    return out


def effective_taxonomy(category_rules: dict) -> dict:
    """category_rules.yaml `taxonomy:` as written, a category written with no value holding no subcategory ([]); with
    no file, no key or an empty one, the reserved names alone (models.RESERVED_TAXONOMY), so a household with no
    rulebook still files every row under a known name. A taxonomy that is no mapping is returned as it is (the load
    refuses it)."""
    taxonomy = category_rules.get("taxonomy")
    if not taxonomy:
        return {category: list(subs) for category, subs in RESERVED_TAXONOMY.items()}
    if not isinstance(taxonomy, dict):
        return taxonomy
    return {category: [] if subs is None else subs for category, subs in taxonomy.items()}


def _reserved_taxonomy_problems(category_rules: dict) -> list[str]:
    """A taxonomy the household writes holds every reserved category (models.RESERVED), and each reserved
    subcategory under its own category: the engine files rows there whatever the rules say."""
    taxonomy = category_rules.get("taxonomy")
    if not taxonomy:
        return []
    if not isinstance(taxonomy, dict):
        return ["category_rules.yaml: taxonomy: expected a mapping of category -> subcategories — write one "
                "`Category: [Subcategory, ...]` line per category"]
    out = []
    for category, subs in RESERVED_TAXONOMY.items():
        if category not in taxonomy:
            out.append(f"category_rules.yaml: taxonomy: missing reserved category '{category}' — add it "
                       "(the engine files rows there)")
            continue
        listed = taxonomy[category] if isinstance(taxonomy[category], list) else []
        out.extend(f"category_rules.yaml: taxonomy: missing reserved subcategory '{category} / {sub}' — add it "
                   "(the engine files rows there)" for sub in subs if sub not in listed)
    return out


def _reserved_rename_problems(category_rules: dict) -> list[str]:
    """No category rename (category_rules.yaml `renames:`) moves a reserved category, or a reserved subcategory
    out of its category: the engine would go on filing rows under the old name. An entry of the wrong shape is
    left to CategoryRenamer, which refuses it."""
    renames = category_rules.get("renames")
    out = []
    for i, r in enumerate(renames if isinstance(renames, list) else []):
        src = r.get("from") if isinstance(r, dict) else None
        if not isinstance(src, dict) or not isinstance(src.get("category"), str):
            continue
        category = src["category"]
        if "subcategory" not in src:
            name = category if category in RESERVED_TAXONOMY else None
        else:
            sub = src["subcategory"]
            name = f"{category} / {sub}" if sub in RESERVED_TAXONOMY.get(category, ()) else None
        if name is not None:
            out.append(f"category_rules.yaml: renames[{i}]: '{name}' is reserved and cannot be renamed — "
                       "the engine files rows there")
    return out


def _retirement_owner_problems(assumptions: dict, roster: Roster) -> list[Problem]:
    """assumptions.yaml `household.retirement_accounts`: account name -> the owner it belongs to."""
    hh = assumptions.get("household")
    accounts = hh.get("retirement_accounts") if isinstance(hh, dict) else None
    return [_unknown_owner("assumptions.yaml", f"household.retirement_accounts.{name}", who, roster.owners)
            for name, who in (accounts.items() if isinstance(accounts, dict) else ())
            if not roster.knows_owner(who)]


def _coverage_problems(assumptions: dict) -> list[str]:
    """assumptions.yaml `coverage`: an optional mapping whose optional `min_share` is a number above 0 and at most 1
    (a null block or key reads as absent: DEFAULT_COVERAGE_MIN_SHARE)."""
    block = assumptions.get("coverage")
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["assumptions.yaml: coverage: expected a mapping — write `coverage: {min_share: 0.6}`, "
                "or leave the block out"]
    share = block.get("min_share")
    if share is None:
        return []
    if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share <= 1:
        return ["assumptions.yaml: coverage.min_share: expected a number above 0 and at most 1 — write a share of "
                f"the cash-flow accounts such as 0.6, or leave the key out (the default is {DEFAULT_COVERAGE_MIN_SHARE})"]
    return []


def _generic_min_amount_problems(assumptions: dict) -> list[str]:
    """assumptions.yaml `generic_min_amount`: the smallest amount, in dollars, the generic transfer matcher pairs — a
    finite number of 0 or more (a null key reads as absent: transfers.DEFAULT_GENERIC_MIN_AMOUNT)."""
    value = assumptions.get("generic_min_amount")
    if value is None or (is_number(value) and value >= 0):
        return []
    return ["assumptions.yaml: generic_min_amount: expected a number of dollars, 0 or more — write the smallest "
            f"amount the generic matcher pairs, or leave the key out (the default is "
            f"{transfers_mod.DEFAULT_GENERIC_MIN_AMOUNT:.2f})"]


# accounts.yaml `loan:` keys, the terms a loan account may state (Config.loan_terms); `rate` is a percent
LOAN_KEYS = ("rate", "monthly_payment", "monthly_escrow", "escrow_balance", "origination", "term_months")
LOAN_MONEY_KEYS = ("monthly_payment", "monthly_escrow", "escrow_balance")
ORIGINATION_KEYS = ("date", "amount")


def _origination_problems(where: str, block) -> list[str]:
    """A `loan.origination` block: a mapping of `date` (an ISO date) and `amount` (dollars above 0), either absent."""
    if not isinstance(block, dict):
        return [f"{where}: expected a mapping — write `origination: {{date: 2030-01-15, amount: 250000}}`, or leave "
                "it out"]
    out = [f"{where}.{k}: unknown key — one of {', '.join(ORIGINATION_KEYS)}" for k in block if k not in ORIGINATION_KEYS]
    if block.get("date") is not None and iso_date(block["date"]) is None:
        out.append(f"{where}.date: expected an ISO date — write the day the loan was made as YYYY-MM-DD")
    amount = block.get("amount")
    if amount is not None and not (is_number(amount) and amount > 0):
        out.append(f"{where}.amount: expected an amount above 0 — write the dollars borrowed as a number")
    return out


def _loan_problems(accounts_cfg: dict) -> list[str]:
    """accounts.yaml: an account's optional `loan:` block (a null block or key reads as absent) sits on a `kind: loan`
    account and holds only LOAN_KEYS: `rate` a percent in [0, 100), the money keys numbers of 0 or more,
    `term_months` a positive whole number, `origination` per _origination_problems. Booleans are no numbers, and
    neither is an infinite or not-a-number value."""
    out = []
    for a, meta in (accounts_cfg.get("accounts") or {}).items():
        if not isinstance(meta, dict) or meta.get("loan") is None:
            continue
        where, block = f"accounts.yaml: accounts.{a}.loan", meta["loan"]
        if meta.get("kind") != "loan":
            out.append(f"{where}: only a loan account has loan terms — remove the block, or set kind: loan")
            continue
        if not isinstance(block, dict):
            out.append(f"{where}: expected a mapping — write the terms as `key: value` lines under loan:, e.g. "
                       "`rate: 6.5`")
            continue
        out.extend(f"{where}.{k}: unknown key — one of {', '.join(LOAN_KEYS)}" for k in block if k not in LOAN_KEYS)
        rate = block.get("rate")
        if rate is not None and not (is_number(rate) and 0 <= rate < 100):
            out.append(f"{where}.rate: expected a percent from 0 up to 100 — write the yearly interest rate, e.g. 6.5 "
                       "for 6.5 %")
        for k in LOAN_MONEY_KEYS:
            v = block.get(k)
            if v is not None and not (is_number(v) and v >= 0):
                out.append(f"{where}.{k}: expected an amount of 0 or more — write dollars as a number, e.g. 2000")
        term = block.get("term_months")
        if term is not None and (isinstance(term, bool) or not isinstance(term, int) or term <= 0):
            out.append(f"{where}.term_months: expected a positive whole number — the loan's length in months, e.g. 360")
        if block.get("origination") is not None:
            out.extend(_origination_problems(f"{where}.origination", block["origination"]))
    return out


def _cashout_income_problems(transfer_rules: dict, accounts: dict, taxonomy: dict, roster: Roster) -> list[str]:
    """transfer_rules.yaml `venmo_patterns.cashout_income`: a list of {accounts, subcategory, earner?} — registry
    account ids, an income subcategory of the taxonomy and a member of the household — that files an unmatched
    payment-app cash-out credit on those accounts (TransferEngine._finalize_venmo)."""
    vp = transfer_rules.get("venmo_patterns")
    entries = vp.get("cashout_income") if isinstance(vp, dict) else None
    where = "transfer_rules.yaml: venmo_patterns.cashout_income"
    if entries is None:
        return []
    if not isinstance(entries, list):
        return [f"{where}: expected a list — one `- {{accounts: [...], subcategory: ...}}` entry per group of accounts"]
    income_subs = taxonomy.get(INCOME_CATEGORY) or []
    out = []
    for i, e in enumerate(entries):
        at = f"{where}[{i}]"
        if not isinstance(e, dict):
            out.append(f"{at}: expected a mapping — write `{{accounts: [...], subcategory: ..., earner: ...}}`")
            continue
        listed = e.get("accounts")
        if not isinstance(listed, list) or not listed:
            out.append(f"{at}: accounts: expected a list of account ids — list the accounts.yaml ids it files")
        else:
            out.extend(f"{at}: account {a!r} is not in accounts.yaml — use a registry account id"
                       for a in listed if not isinstance(a, str) or a not in accounts)
        sub = e.get("subcategory")
        if sub is None:
            out.append(f"{at}: subcategory: missing — name an {INCOME_CATEGORY} subcategory of the taxonomy")
        elif sub not in income_subs:
            out.append(f"{at}: subcategory {sub!r} is not under {INCOME_CATEGORY} in the taxonomy — name one listed "
                       f"under taxonomy.{INCOME_CATEGORY} in category_rules.yaml, or add it there")
        if e.get("earner") is not None:
            found = earner_problem("transfer_rules.yaml", f"venmo_patterns.cashout_income[{i}].earner", e["earner"],
                                   roster)
            out.extend([str(found)] if found else [])
    return out


class Config:
    def __init__(self, config_dir: Path | str | None = None, source_dir: str | None = None, *,
                 paths: Paths | None = None, parser_names: Collection[str] | None = None, check_paths: bool = False):
        """`config_dir` and `source_dir` (the statements folder) win over every variable; every other location is
        resolved by Paths (`paths`, when given, instead of Paths(config_dir)). Every file is loaded and checked as far
        as it can be, and every problem found is raised at once as a ConfigError: a file that is missing (when
        required), unreadable or not valid YAML is itself one, and the checks that need it are skipped. A null
        top-level block reads as absent. `parser_names` are the statement formats of the release an account's `parser:`
        may name (default: the pipeline's TXN_PARSERS); the household's own presets (config/presets/, `presets`) are
        formats of this household besides, each refused when its id is one of those. `check_paths` also checks that
        the statements folder accounts.yaml `source_dir` names exists (validate and `python -m etl ingest` ask for
        it). The path attributes are plain and writable: tests and `rules` repoint them."""
        paths = paths or Paths(config_dir)
        self.dir = paths.config_dir
        _require_config_dir(paths)
        problems = Problems()
        raw = {name: self._read(name, problems) for name in CONFIG_FILES}
        # the files found and loaded, for `python -m etl validate`'s summary
        self.files: tuple[str, ...] = tuple(n for n in CONFIG_FILES
                                            if raw[n] is not None and (self.dir / n).exists())
        roster = self._read_household(raw["household.yaml"], problems)
        parsers = _registered_parsers() if parser_names is None else tuple(parser_names)
        own = household_presets(paths.presets_dir(), parsers)
        problems.extend(own.problems)
        # the household's own presets by id, which Pipeline reads a file with before TXN_PARSERS (format_parse)
        self.presets: dict[str, Preset] = own.presets
        registry = self._read_accounts(raw["accounts.yaml"], problems, roster=roster,
                                       parser_names=(*parsers, *own.names))
        docs = {}
        for name in CONFIG_FILES[2:]:
            found, docs[name] = validate_mod.rule_file_problems(name, raw[name])
            problems.extend(found)
        self.assumptions = docs["assumptions.yaml"] or {}
        self.transfer_rules = docs["transfer_rules.yaml"] or {}
        self.category_rules = docs["category_rules.yaml"] or {}
        self.bank_category_map = docs["bank_category_map.yaml"] or {}
        self.vendor_rules = docs["vendor_rules.yaml"] or {}
        raw_overrides = docs["overrides.yaml"] or []
        # the names the engine files rows under are in the taxonomy and keep their names
        taxonomy = None
        if docs["category_rules.yaml"] is not None:
            problems.extend([*_reserved_taxonomy_problems(self.category_rules),
                             *_reserved_rename_problems(self.category_rules)])
            taxonomy = self._usable_taxonomy(problems)
        account_ids = None if registry is None else [a for a in registry if isinstance(a, str)]
        # what each rule file holds beyond its compilers' checks (validate.py); an entry with an unknown key is left
        # out of its compiler
        skip_rules = skip_category_renames = skip_patterns = skip_vendor_renames = set()
        if docs["category_rules.yaml"] is not None:
            found, skip_rules, skip_category_renames = validate_mod.category_rules_problems(
                self.category_rules, taxonomy=taxonomy, roster=roster, account_ids=account_ids)
            problems.extend(found)
        if docs["vendor_rules.yaml"] is not None:
            found, skip_patterns, skip_vendor_renames = validate_mod.vendor_rules_problems(self.vendor_rules)
            problems.extend(found)
        self._validate_rules(problems, roster=roster, account_ids=account_ids, taxonomy=taxonomy,
                             category_rules_ok=docs["category_rules.yaml"] is not None,
                             vendor_rules_ok=docs["vendor_rules.yaml"] is not None,
                             skip_rules=skip_rules, skip_patterns=skip_patterns)
        # Renames: one entry in config and every stored reference follows at load — the
        # answers doc is renamed in Pipeline.enrich right after answers.load; everything
        # Config owns is renamed here, and the list itself is exported (assumptions.json
        # category_renames) so the dashboard's answers overlay and category-keyed prefs apply
        # the same map. The files on disk are never rewritten.
        found = []
        self.vendor_renamer = renames_mod.VendorRenamer(self.vendor_rules.get("renames"), problems=found,
                                                        skip=skip_vendor_renames)
        _collect(problems, "vendor_rules.yaml", _FIX_RENAME, found)
        found = []
        self.category_renamer = None if taxonomy is None else renames_mod.CategoryRenamer(
            self.category_rules.get("renames"), taxonomy, problems=found, skip=skip_category_renames)
        _collect(problems, "category_rules.yaml", _FIX_RENAME, found)
        self.category_renames: list[dict] = list(self.category_rules.get("renames") or [])   # validated above; exported as assumptions.json category_renames
        problems.extend(self._renamed_pattern_vendor_problems())
        # every account's kind and class is a known one, loan terms sit on a loan account in their shape, each manual
        # entry has a name of its own (_read_accounts), every owner name the files use is one household.yaml defines
        # (rule conditions: _validate_rules), the transfer rules compile (each scoped, typed and hinted in a known
        # form: the checks compile_transfer_rules makes when the engine is built) and the cash-out mapping names
        # registry accounts and an income subcategory, the bank labels and the overrides name the taxonomy, and the
        # assumptions are typed and in range. A check whose inputs did not load is skipped: the owners when
        # household.yaml's `members` is no list with an adult or its joint label is broken (a name only a malformed
        # member has is not checked: Roster), the account ids when accounts.yaml's `accounts:` is, the taxonomy when
        # category_rules.yaml's is.
        if roster is not None:
            problems.extend(_retirement_owner_problems(self.assumptions, roster))
        if registry is not None and raw["transfer_rules.yaml"] is not None:
            problems.extend(transfers_mod.transfer_rule_problems(raw["transfer_rules.yaml"], registry))
            if roster is not None and taxonomy is not None and docs["transfer_rules.yaml"] is not None:
                problems.extend(_cashout_income_problems(self.transfer_rules, registry, taxonomy, roster))
        if docs["transfer_rules.yaml"] is not None:
            problems.extend(validate_mod.transfer_rules_problems(self.transfer_rules))
        if docs["bank_category_map.yaml"] is not None:
            problems.extend(validate_mod.bank_map_problems(self.bank_category_map, taxonomy))
        if docs["overrides.yaml"] is not None:
            rename = self.category_renamer.map if self.category_renamer is not None else (lambda c, s: (c, s))
            problems.extend(validate_mod.override_problems(raw_overrides, taxonomy=taxonomy, roster=roster,
                                                           account_ids=account_ids, rename=rename))
        if isinstance(raw["assumptions.yaml"], dict):
            problems.extend([*_coverage_problems(self.assumptions),
                             *_generic_min_amount_problems(self.assumptions)])
            renamed = {r[0] for r in self.category_renamer.rules} if self.category_renamer is not None else set()
            problems.extend(validate_mod.assumptions_problems(
                self.assumptions, taxonomy=taxonomy, account_names=self._account_names(registry), renamed_from=renamed))
        # Learning flags: validated here, read by enrich/vendor_registry.py and the
        # learner (enrich/learned.py). Normalized to a sorted, renamed exclude list.
        learning = self.vendor_rules.get("learning") or {}
        exclude = learning.get("exclude") if isinstance(learning, dict) else None
        if (not isinstance(learning, dict) or not isinstance(exclude or [], list)
                or not all(isinstance(n, str) and n for n in exclude or [])):
            problems.add("vendor_rules.yaml", "learning", "must be a mapping whose exclude: is a list of vendor names",
                         "write `learning: {exclude: [Vendor Name, ...]}`, or leave it out")
        # None only for a household whose accounts read no statement file and that names no folder
        self.source_dir: Path | None = self._statements_dir(paths, source_dir, problems, check_paths=check_paths)
        if problems:
            raise ConfigError(problems)

        _dates_as_text({"accounts.yaml": self.accounts_cfg, "assumptions.yaml": self.assumptions,
                        "overrides.yaml": raw_overrides})
        self.overrides = self.category_renamer.apply_to_overrides(raw_overrides)
        self.vendor_rules["learning"] = {**learning, "exclude": self.vendor_renamer.apply_to_names(exclude or [])}
        cancelled = self.assumptions.get("cancelled_subscriptions")
        if cancelled:
            self.assumptions["cancelled_subscriptions"] = self.vendor_renamer.apply_to_cancelled(cancelled)

        family, engine = paths.state_family_dir(), paths.state_engine_dir()
        # Review-inbox answers written by the dashboard (scenario_store.py /api/answers), which the ingest only
        # reads. Dev: <repo>/scenarios/answers.json; in the containers the install's state/family/, which the store
        # writes and the two workers mount read-only (compose.yaml). Missing file = no answers (enrich/answers.py).
        self.answers_path = family / "answers.json"
        # Learned vendor rules the ETL itself writes (etl/enrich/learned.py). Runtime STATE, so
        # it is loaded leniently there and never here: Config raises on invalid config and is
        # also built by the network-fetch commands — a torn or hostile file must not reach them.
        # In the containers: state/engine/, which only the two workers mount.
        self.learned_path = engine / "learned_rules.json"
        # The manual assets entered in the app, which the store writes and the ETL only reads (etl/manual_assets.py):
        # loaded leniently in the pipeline and never here, like the answers.
        self.manual_assets_path = family / "manual_assets.json"
        self.data_dir = paths.data_dir()
        self.exports_dir = self.data_dir / "exports"
        self._set_feed_paths(paths)

    def _set_feed_paths(self, paths: Paths) -> None:
        # SimpleFIN durable store — in the developer layout a sibling of the repo (like source_dir),
        # so it survives `make clean` (rm -rf data/) and the from-scratch DB rebuild.
        self.simplefin_dir = paths.store_dir(self.accounts_cfg)
        self.simplefin_snapshots_dir = self.simplefin_dir / "snapshots"
        self.simplefin_credential_path = paths.credential_path(self.simplefin_dir)
        # epochs from SimpleFIN are converted to dates in the household's zone (banks date rows
        # in local time; matches the CSV side at the cutover seam)
        self.simplefin_tz = self.timezone

    def _read(self, name: str, problems: Problems):
        """The file's document. An optional file that is missing reads as empty ({}, overrides.yaml []); a required
        one that is missing, and any file that cannot be read or is not valid YAML (a value the parser cannot build,
        such as an unquoted date that does not exist, included), is a problem and gives None."""
        if not (self.dir / name).exists():
            if name in REQUIRED_FILES:
                problems.add(validate_mod.missing_file_problem(name, self.dir))
                return None
            return [] if name in LIST_FILES else {}
        try:
            doc = self._load(name)
        except yaml.YAMLError as e:
            problems.add(validate_mod.yaml_problem(name, e))
            return None
        except (OSError, UnicodeDecodeError) as e:
            problems.add(validate_mod.unreadable_problem(name, e))
            return None
        except (ValueError, OverflowError) as e:
            problems.add(validate_mod.unbuildable_value_problem(name, self.dir / name, e))
            return None
        return [] if name in LIST_FILES and doc == {} else doc

    def _read_household(self, doc, problems: Problems) -> Roster | None:
        """household.yaml, the second required file: who the household is. Its problems go to `problems`; returns the
        Roster the other files' owner and earner names are checked against — read from the well-formed members — when
        `members` is a list holding an adult and the joint label is usable, else None (those names are then not
        checked)."""
        self.timezone = doc.get("timezone") if isinstance(doc, dict) else None
        self.ai: AiSettings = ai_settings(doc)
        # lines `validate` and the AI preview print without refusing the config (validate.ai_notes)
        self.notes: list[str] = validate_mod.ai_notes(doc) if isinstance(doc, dict) else []
        if doc is None:
            return None
        problems.extend(_household_shape_problems(doc))
        if not isinstance(doc, dict):
            return None
        problems.extend(validate_mod.household_problems(doc))
        found, good, unchecked = _member_problems(doc.get("members"), doc.get("joint_label"))
        problems.extend(found)
        joint = doc.get("joint_label")
        members = doc["members"] if good is not None else []
        names = {m["name"].casefold() for m in members if isinstance(m, dict) and is_text(m.get("name"))}
        # a list with no adult, or no usable joint label, leaves every owner name unchecked: its problem says why, and
        # one more line says what that leaves unchecked
        if (good is None or not any(isinstance(m, dict) and m.get("role") == "adult" for m in members)
                or not is_text(joint) or joint.casefold() in names):
            problems.add("household.yaml", "members", "owner and earner references in the other files are not checked "
                         "until this is fixed", "give household.yaml at least one adult and a joint label no member has")
            return None
        self.members: tuple[dict, ...] = tuple(
            {"name": m["name"], "role": m["role"],
             **({"birth_year": m["birth_year"]} if m.get("birth_year") is not None else {})}
            for m in good)
        self.adults: tuple[str, ...] = tuple(m["name"] for m in self.members if m["role"] == "adult")
        self.children: tuple[str, ...] = tuple(m["name"] for m in self.members if m["role"] == "child")
        self.joint_label: str = doc["joint_label"]
        self.surname: str | None = doc.get("surname")
        self.household_name: str = doc.get("name") or self.surname or "Household"
        # household.yaml `modules` as it states them (each a known module, checked above): a module it leaves out is
        # absent here, not off, so the dashboard can tell the two apart (assumptions.json `modules`)
        modules = doc.get("modules")
        self.modules: dict[str, bool] = dict(modules) if isinstance(modules, dict) else {}
        net_worth = doc.get("net_worth") if isinstance(doc.get("net_worth"), dict) else {}
        cards = net_worth.get("include_credit_cards")           # written with no value: left out, so counted
        self.net_worth_options: dict = {"include_credit_cards": True if cards is None else cards}
        return Roster(self.owners, (*self.adults, *self.children), frozenset(unchecked))

    def _read_accounts(self, doc, problems: Problems, *, roster: Roster | None,
                       parser_names: Collection[str] | None, feed_only: bool = False) -> dict | None:
        """accounts.yaml, the first required file: sets `accounts_cfg` (its null top-level blocks read as absent) and
        returns the `accounts:` registry, or None when that block is unusable. Its problems go to `problems`: the
        ones validate.accounts_problems finds and the checks below; owners are checked when `roster` is
        given. `feed_only` keeps the checks of what the bank-feed commands read (FeedConfig)."""
        self.accounts_cfg = {k: v for k, v in doc.items() if v is not None} if isinstance(doc, dict) else {}
        if doc is None:
            return None
        if not isinstance(doc, dict):
            problems.add("accounts.yaml", "", "top level: expected a mapping",
                         "write the file as `key: value` lines, starting with accounts:")
            return None
        problems.extend(validate_mod.accounts_problems(doc, parser_names, feed_only=feed_only))
        registry = doc.get("accounts") if isinstance(doc.get("accounts"), dict) else None
        manual = doc.get("manual_entries") if isinstance(doc.get("manual_entries"), list) and not feed_only else []
        view = {"accounts": registry or {}, "manual_entries": manual}
        problems.extend([*_account_kind_problems(view), *_asset_class_problems(view), *_loan_problems(view)])
        if not feed_only:
            problems.extend(manual_entry_problems(manual))
            if roster is not None:
                problems.extend(_account_owner_problems(view, roster))
        return registry

    def _statements_dir(self, paths: Paths, source_dir: str | None, problems: Problems, *,
                        check_paths: bool) -> Path | None:
        """The statements folder (Paths.imports_dir), or None when none is needed or it cannot be resolved (then a
        problem says why). With `check_paths`, a folder accounts.yaml `source_dir` names must exist when an account
        reads statement files."""
        legacy = paths.imports_from_accounts_yaml(source_dir)
        if legacy and not (self.accounts_cfg.get("source_dir") is None or is_text(self.accounts_cfg["source_dir"])):
            return None                              # not text: validate.accounts_problems says so
        try:
            found = paths.imports_dir(self.accounts_cfg, source_dir)
        except ValueError as e:
            problems.add(str(e))
            return None
        accounts = self.accounts_cfg.get("accounts")
        reads_files = isinstance(accounts, dict) and any(isinstance(m, dict) and (m.get("parser") or m.get("sources"))
                                                         for m in accounts.values())
        if check_paths and legacy and found is not None and reads_files and not found.is_dir():
            problems.add(validate_mod.statements_dir_problem(found))
        return found

    @property
    def owners(self) -> tuple[str, ...]:
        """Every name an `owner` or `earner` may take, in this order: the adults, the joint label, the children
        (each group in household.yaml order). Exported as assumptions.json `owners`."""
        return (*self.adults, self.joint_label, *self.children)

    @property
    def coverage_min_share(self) -> float:
        """assumptions.yaml `coverage.min_share` (checked at load), else DEFAULT_COVERAGE_MIN_SHARE: the share of the
        cash-flow accounts with history whose history must have begun by a month for it to be in coverage
        (cashflow_monthly.json, exports.in_coverage)."""
        share = (self.assumptions.get("coverage") or {}).get("min_share")
        return DEFAULT_COVERAGE_MIN_SHARE if share is None else float(share)

    @property
    def taxonomy(self) -> dict:
        """The taxonomy every reader checks against and the export carries: category_rules.yaml `taxonomy:`, or the
        reserved names alone when the household writes none (effective_taxonomy)."""
        return effective_taxonomy(self.category_rules)

    def _load(self, name: str, *, required: bool = False) -> dict:
        """The file's mapping; `{}` for an empty file or, unless `required`, a missing one."""
        path = self.dir / name
        if not path.exists():
            if required:
                raise FileNotFoundError(f"required config file {name} not found in {self.dir}")
            return {}
        with open(path) as f:
            return yaml.safe_load(f) or {}

    def _validate_rules(self, problems: Problems, *, roster: Roster | None, account_ids: list[str] | None,
                        taxonomy: dict | None, category_rules_ok: bool, vendor_rules_ok: bool,
                        skip_rules: Collection[int] = (), skip_patterns: Collection[int] = ()) -> None:
        """Refuse at load a malformed or off-taxonomy rule: a typo'd field/op/stage would otherwise never match, and a
        typo'd category would mint a new bucket in the dashboard. Vendor patterns must stay `pattern:`-shaped — the
        VendorNormalizer has only the descriptor to match on, so a structured vendor entry could never be applied.
        Every compiler collects: each bad keyword rule, vendor pattern and split reports its problem and is left out
        of the checks after it (their taxonomy, their owner and account references, the catch-all's place), as are
        the entries in `skip_rules` / `skip_patterns`, whose unknown keys are already reported. A check whose inputs
        did not load (the owners, the registry, the taxonomy, a file) is skipped."""
        keyword = vendor = None
        if category_rules_ok:
            found: list[str] = []
            keyword = rules_mod.compile_rules(self.category_rules.get("keyword_rules") or [], source="keyword_rules",
                                              problems=found, skip=skip_rules)
            _collect(problems, "category_rules.yaml", _FIX_RULE, found)
        patterns = self.vendor_rules.get("patterns") or []
        shaped = {i for i, e in enumerate(patterns) if not isinstance(e, dict) or "pattern" not in e}
        problems.extend(Problem("vendor_rules.yaml", f"patterns[{i}]", "vendor patterns need a pattern: (structured "
                                "conditions are keyword-rule only)", "give the entry a pattern:, or move it to "
                                "category_rules.yaml keyword_rules") for i in sorted(shaped))
        left_out = shaped | set(skip_patterns)
        if vendor_rules_ok:
            found = []
            vendor = rules_mod.compile_rules(patterns, source="vendor_rules", problems=found, skip=left_out)
            _collect(problems, "vendor_rules.yaml", _FIX_RULE, found)
            left_out = set(range(len(patterns))) - {r.index for r in vendor}
        for file, compiled in (("category_rules.yaml", keyword), ("vendor_rules.yaml", vendor)):
            if compiled is None:
                continue
            found = []
            if taxonomy is not None:
                rules_mod.validate_taxonomy(compiled, taxonomy, problems=found)
            if roster is not None and account_ids is not None:
                rules_mod.validate_references(compiled, owners=(*roster.owners, *sorted(roster.unchecked)),
                                              accounts=account_ids, problems=found)
            if compiled is keyword:
                found += rules_mod.last_in_stage_problems(compiled, FALLBACK_CATEGORY, P2P_UNKNOWN_SUB)
            _collect(problems, file, _FIX_RULE, found)
        # split legs: shape and taxonomy at load; Pipeline.enrich hands this compiled view to splits.apply
        self.split_rules = None
        if vendor_rules_ok and taxonomy is not None:
            found = []
            self.split_rules = splits_mod.compile_split_rules(self.vendor_rules, taxonomy=taxonomy, problems=found,
                                                              skip=left_out)
            _collect(problems, "vendor_rules.yaml", _FIX_SPLIT, found)

    def _usable_taxonomy(self, problems: Problems) -> dict | None:
        """The taxonomy the rule, rename, split and cash-out checks read, or None when it is no mapping (reported by
        _reserved_taxonomy_problems), a category's name is one YAML read as a number or its subcategories are no list
        of text (both reported here; a null list reads as none)."""
        taxonomy = self.taxonomy
        if not isinstance(taxonomy, dict):
            return None
        numeric = [c for c in taxonomy if validate_mod.read_as_number(c)]
        problems.extend(Problem("category_rules.yaml", f"taxonomy.{c}", "a category name YAML read as a number",
                                validate_mod.quote_fix(c)) for c in numeric)
        bad = [c for c, subs in taxonomy.items()
               if subs is not None and not (isinstance(subs, list) and all(is_text(s) for s in subs))]
        problems.extend(Problem("category_rules.yaml", f"taxonomy.{c}", "expected a list of subcategories",
                                f"write `{c}: [Subcategory, ...]`, or `{c}: []`") for c in bad)
        return None if bad or numeric else taxonomy

    def _account_names(self, registry: dict | None) -> set | None:
        """The names an assumptions.yaml retirement account may take: each registry account's `name` and each manual
        entry's `account`; None when the registry is unusable (the names are then not checked)."""
        if registry is None:
            return None
        manual = self.accounts_cfg.get("manual_entries")
        return ({m.get("name") for m in registry.values() if isinstance(m, dict) and isinstance(m.get("name"), str)}
                | {e.get("account") for e in (manual if isinstance(manual, list) else ())
                   if isinstance(e, dict) and isinstance(e.get("account"), str)})

    def _renamed_pattern_vendor_problems(self) -> list[Problem]:
        """A patterns entry keeps emitting its own `vendor:` string, so renaming that string would orphan the stored
        references instead of moving them: reword the pattern's vendor: instead. A rename entry of another shape is
        the VendorRenamer's problem."""
        live = {e["vendor"].casefold() for e in self.vendor_rules.get("patterns") or []
                if isinstance(e, dict) and isinstance(e.get("vendor"), str)}
        renames = self.vendor_rules.get("renames")
        return [Problem("vendor_rules.yaml", f"renames[{i}]",
                        f"from {r['from']!r} is a vendor_rules.patterns vendor: its rows keep that spelling, so the "
                        "rename would orphan stored references", "change the pattern's vendor: instead")
                for i, r in enumerate(renames if isinstance(renames, list) else [])
                if isinstance(r, dict) and isinstance(r.get("from"), str) and r["from"].casefold() in live]

    @property
    def accounts(self) -> dict:
        return self.accounts_cfg["accounts"]

    def txn_sources(self, account_id: str) -> list[Source]:
        """The sources of the account's statement files, oldest first: its accounts.yaml `sources:` (checked at load:
        each a known format and a pattern, every one after the first with a later start day), or its `parser:` and
        `glob:` as one source; empty for an account with neither — a feed-only account, read by
        Pipeline.parse_simplefin instead."""
        meta = self.accounts[account_id]
        if meta.get("sources"):
            return [Source(e["format"], e["glob"], e.get("since")) for e in meta["sources"]]
        if not meta.get("parser") or not meta.get("glob"):
            return []
        return [Source(meta["parser"], meta["glob"])]

    def joins_sources(self, account_id: str) -> bool:
        """Whether the account names its statement files as accounts.yaml `sources:` rather than `parser:` and `glob:`
        (the pipeline reports how it joined them)."""
        return bool(self.accounts[account_id].get("sources"))

    def find_sources(self, glob: str) -> list[Path]:
        """All files in source_dir matching the glob, excluding skip patterns."""
        return [p for p in sorted(self.source_dir.glob(glob)) if not self._skipped(p)]

    def statement_file_readers(self) -> dict[Path, list[str]]:
        """Each statement file an account's patterns match, with the accounts that read it in accounts.yaml order, the
        first the one it is read for. A file one account's patterns match is that account's, unread here. A file
        several accounts' patterns match is read with each one's format (its first source whose pattern matches it):
        the accounts whose format reads it, or the first of them all when none does (its ingest then fails that
        account's import check for the file). Every other account whose pattern matches it leaves it out:
        Pipeline.parse_transactions fails one check for each that reads it too, and `validate` prints a note."""
        if self.source_dir is None:
            return {}
        matched: dict[Path, dict[str, str]] = {}       # file -> {account: the format its first matching source names}
        for account_id in self.accounts:
            for source in self.txn_sources(account_id):
                for path in self.find_sources(source.glob):
                    matched.setdefault(path, {}).setdefault(account_id, source.format)
        from .pipeline import format_parse, read_statement
        readers: dict[Path, list[str]] = {}
        for path, formats in matched.items():
            accounts = list(formats)
            if len(accounts) > 1:
                accounts = [a for a, fmt in formats.items()
                            if read_statement(format_parse(fmt, self.presets), path, a)[0] is not None] or accounts[:1]
            readers[path] = accounts
        return readers

    def _skipped(self, path: Path) -> bool:
        """Whether an accounts.yaml `skip_files` pattern names the file: it is never read."""
        return any(path.match(s) for s in self.accounts_cfg.get("skip_files") or [])

    def unmatched_sources(self, matched: Collection[Path]) -> list[str]:
        """The files of the statements folder that no account's glob matched (`matched`: the files find_sources found
        for the accounts' globs), by their path in the folder as a glob names them, sorted. Searched: the folder itself
        and each folder an account's glob looks in (the glob's folder part, a pattern of its own: `**` reaches every
        subfolder); a folder no glob looks in is not. Left out: a file a `skip_files` pattern names, and a hidden file
        or one in a hidden folder (a name starting with a dot), which is never a statement: a hidden folder is passed
        over before it is listed. A folder that cannot be listed (a lost+found, a folder at mode 000) and an entry that
        cannot be looked at are passed over too, as the globs pass them over: the scan never stops an ingest."""
        root = self.source_dir
        if root is None:
            return []
        folders = {root}
        for account_id in self.accounts:
            for source in self.txn_sources(account_id):
                part = PurePosixPath(source.glob).parent
                if part.parts and ".." not in part.parts:
                    try:
                        folders.update(d for d in root.glob(str(part)) if _readable_as(d, Path.is_dir))
                    except OSError:
                        pass
        matched = set(matched)
        out = []
        for folder in folders:
            if _hidden(folder.relative_to(root)):
                continue
            try:
                entries = list(folder.iterdir())
            except OSError:
                continue
            for p in entries:
                rel = p.relative_to(root)
                if p not in matched and not _hidden(rel) and not self._skipped(p) and _readable_as(p, Path.is_file):
                    out.append(rel.as_posix())
        return sorted(out)

    # --------------------------------------------------------------- simplefin

    def margin_account_ids(self) -> list[str]:
        """Accounts flagged margin: true (their negative remainder is a margin
        loan), in accounts.yaml order so "the" margin account is deterministic."""
        return [a for a, m in self.accounts.items() if m.get("margin")]

    @property
    def household(self) -> dict:
        """What the transfer engine knows of the household: `owners` (the adults) and `surname` from
        household.yaml, and from accounts.yaml `household:` `own_name_tokens` (Zelle "us" detection), derived
        from the adults + surname when not given explicitly. The counter-hint groups are transfer_rules.yaml
        `groups:`."""
        hh = dict(self.accounts_cfg.get("household") or {})
        owners = [str(o) for o in self.adults]
        surname = self.surname
        hh["owners"], hh["surname"] = owners, surname
        if hh.get("own_name_tokens") is None:               # absent or null: derived
            hh["own_name_tokens"] = [n.upper() for n in (*owners, *([surname] if surname else []))]
        # normalize an explicit list too: the matcher compares against UPPER-cased descriptors,
        # so a hand-written mixed-case token would otherwise silently never match
        hh["own_name_tokens"] = [str(n).upper() for n in hh["own_name_tokens"] or []]
        return hh

    def mortgage_account_id(self) -> str | None:
        """The `mortgage: true` registry account (kind loan), or None."""
        return next((a for a, m in self.accounts.items() if m.get("mortgage")), None)

    def loan_terms(self, account_id: str) -> dict:
        """The account's accounts.yaml `loan:` terms (shape checked at load), every LOAN_KEYS key present: `rate` (a
        percent), the money keys in dollars, `term_months`, and `origination` as {date (ISO text), amount}. An absent
        or null term is None; `origination` is None when neither of its keys is set. An id that is not in
        accounts.yaml raises KeyError."""
        block = self.accounts[account_id].get("loan") or {}
        orig = block.get("origination") or {}
        date, amount = orig.get("date"), orig.get("amount")
        return {"rate": block.get("rate"),
                **{k: block.get(k) for k in LOAN_MONEY_KEYS},
                "origination": (None if date is None and amount is None
                                else {"date": None if date is None else iso_date(date), "amount": amount}),
                "term_months": block.get("term_months")}

    def asset_class_of(self, account_id: str) -> str:
        """The class the registry account's rows take: its `asset_class`, else "Mortgage" for the `mortgage: true`
        loan, else its kind's default (models.account_asset_class). An id that is not in accounts.yaml raises
        KeyError."""
        return account_asset_class(self.accounts[account_id])

    def owner_of(self) -> dict[str, str]:
        """Registry account id -> owner, for persistence and exports."""
        return {a: m["owner"] for a, m in self.accounts.items()}

    def simplefin_map(self) -> dict[str, str]:
        """SimpleFIN opaque account id -> registry account id, for mapped accounts.
        Includes `simplefin_id_history` ids (prior connections to the same
        institution, replaced after a delete+reconnect) so the old snapshots keep
        feeding the account's balance history."""
        out: dict[str, str] = {}
        for a, m in self.accounts.items():
            for sfid in (m.get("simplefin_id"), *(m.get("simplefin_id_history") or ())):
                if sfid:
                    out[sfid] = a
        return out

    def simplefin_cutovers(self) -> dict[str, str]:
        """Registry account id -> cutover date (CSV owns < cutover, SimpleFIN >=)."""
        return {a: str(m["simplefin_cutover"]) for a, m in self.accounts.items()
                if m.get("simplefin_cutover")}

    def simplefin_ignore(self) -> set[str]:
        """SimpleFIN ids to NOT warn about when unmapped — duplicate twin ids (an
        account mapped under its other id) and deliberately-skipped dead accounts."""
        sf = self.accounts_cfg.get("simplefin") or {}
        return set(sf.get("ignore") or [])

    def find_simplefin_snapshots(self) -> list[Path]:
        """All snapshot JSON files, sorted chronologically (UTC-basic-ISO names sort
        lexically = chronologically)."""
        d = self.simplefin_snapshots_dir
        return sorted(d.glob("accounts-*.json")) if d.exists() else []


class FeedConfig(Config):
    """The config the bank-feed commands build (`python -m etl simplefin claim|fetch|list-accounts`): accounts.yaml's
    `accounts:` registry with its feed ids and its `simplefin:` block, household.yaml's time zone, and the feed store
    and credential paths — each checked as Config checks it, and nothing else, so a problem in a rule file (or in a
    manual entry) never blocks a fetch. Raises ConfigError with the problems of those parts."""

    def __init__(self, config_dir: Path | str | None = None, *, paths: Paths | None = None):
        paths = paths or Paths(config_dir)
        self.dir = paths.config_dir
        _require_config_dir(paths)
        problems = Problems()
        household = self._read("household.yaml", problems)
        if household is not None and not isinstance(household, dict):
            problems.extend(_household_shape_problems(household))
        self.timezone = household.get("timezone") if isinstance(household, dict) else None
        if isinstance(household, dict):
            problems.extend(validate_mod.timezone_problems(household))
        self._read_accounts(self._read("accounts.yaml", problems), problems, roster=None, parser_names=None,
                            feed_only=True)
        if problems:
            raise ConfigError(problems)
        self._set_feed_paths(paths)

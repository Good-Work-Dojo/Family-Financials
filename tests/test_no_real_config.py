# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""No test reads the repository's own config directory: every test that needs a valid config builds on the demo
household (`conftest.DEMO_CONFIG_DIR`). The scan below fails when a test file names the repository's `config/`,
so no test depends on a household's own config."""
from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

from conftest import DEMO_CONFIG_DIR, ROOT, isolated_pipeline

SELF = "tests/test_no_real_config.py"

# Names a test may not use: each one reads or copies the repository's own config.
REMOVED_NAMES = {"CONFIG_DIR", "load_config", "config_copy", "registry_account", "registry_default"}

# What a file may keep, exactly: the multiset of (kind, detail) its scan finds. `dir` is the expression
# ROOT / "config", `path` a string constant (or an f-string's leading constant) starting with `config/`, with the
# constant as the detail. A file that holds one more match, or one fewer, fails.
EXCEPTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    # a synthetic file name inside a sample the gate must flag
    "tests/test_publish_gate.py": (("path", "config/hosts.yaml"),),
}
# The only file where a call `Config()` with no argument and no keyword is allowed: it points the default place at a
# temporary directory first.
BARE_CONFIG_CALL_FILE = "tests/test_paths.py"

_DIR_TEXT = re.compile(r"""ROOT\s*/\s*["']config["']|ROOT\.joinpath\(\s*["']config["']""")


def _test_files() -> list[Path]:
    files = [*ROOT.glob("tests/**/*.py"), *ROOT.glob("tools/**/test_*.py")]
    return sorted(f for f in files if "__pycache__" not in f.parts)


def _is_config_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == "config"


def _is_config_path(value: object) -> bool:
    return isinstance(value, str) and value.startswith("config/")


def _matches(path: Path) -> dict[tuple[str, int], str]:
    """(kind, line) -> detail of every place the file names the repository's config: the expression ROOT / "config",
    a string constant starting with `config/` (an f-string counts by its leading constant), a removed helper's name,
    and a call `Config()` with no argument and no keyword."""
    text = path.read_text("utf-8")
    found: dict[tuple[str, int], str] = {}
    for m in _DIR_TEXT.finditer(text):
        found[("dir", text.count("\n", 0, m.start()) + 1)] = "ROOT / config"
    for node in ast.walk(ast.parse(text, str(path))):
        if isinstance(node, ast.Name) and node.id in REMOVED_NAMES:
            found[("name", node.lineno)] = node.id
        elif isinstance(node, ast.Attribute) and node.attr in REMOVED_NAMES:
            found[("name", node.lineno)] = node.attr
        elif isinstance(node, ast.alias) and node.name in REMOVED_NAMES:
            found[("name", node.lineno)] = node.name
        elif isinstance(node, ast.arg) and node.arg in REMOVED_NAMES:
            found[("name", node.lineno)] = node.arg
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in REMOVED_NAMES:
            found[("name", node.lineno)] = node.name
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if isinstance(node.left, ast.Name) and node.left.id == "ROOT" and _is_config_literal(node.right):
                found[("dir", node.lineno)] = "ROOT / config"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "joinpath":
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "ROOT" \
                    and node.args and _is_config_literal(node.args[0]):
                found[("dir", node.lineno)] = "ROOT / config"
        elif isinstance(node, ast.Call) and not node.args and not node.keywords and (
                (isinstance(node.func, ast.Name) and node.func.id == "Config")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "Config")):
            found[("bare Config()", node.lineno)] = "Config()"
        elif isinstance(node, ast.Constant) and _is_config_path(node.value):
            found[("path", node.lineno)] = node.value       # an f-string's leading constant is visited here too
    return found


def test_no_test_names_the_repository_config_directory():
    files = _test_files()
    assert len(files) > 60, f"the scan found only {len(files)} files"
    offenders: list[str] = []
    seen_files: set[str] = set()
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        if rel == SELF:
            continue
        matches = _matches(f)
        if rel == BARE_CONFIG_CALL_FILE:
            matches = {k: v for k, v in matches.items() if k[0] != "bare Config()"}
        if not matches:
            continue
        held = Counter((kind, detail) for (kind, _), detail in matches.items())
        if rel in EXCEPTIONS and held == Counter(EXCEPTIONS[rel]):
            seen_files.add(rel)
            continue
        allowed = Counter(EXCEPTIONS.get(rel, ()))
        for (kind, line), detail in sorted(matches.items(), key=lambda kv: kv[0][1]):
            if allowed[(kind, detail)] > 0:
                allowed[(kind, detail)] -= 1
            else:
                offenders.append(f"{rel}:{line} ({kind}: {detail})")
        if rel in EXCEPTIONS:
            seen_files.add(rel)
    assert not offenders, "tests name the repository's config - build on the demo household: " + ", ".join(offenders)
    unused = sorted(rel for rel in EXCEPTIONS if rel not in seen_files)
    assert not unused, f"an exception no longer matches anything, remove it: {', '.join(unused)}"
    for rel, pinned in EXCEPTIONS.items():
        held = Counter((k, d) for (k, _), d in _matches(ROOT / rel).items() if k != "bare Config()")
        assert held == Counter(pinned), f"{rel} no longer holds exactly what its exception pins: {sorted(held.elements())}"


def test_isolated_pipeline_defaults_to_the_demo_config(tmp_path):
    p = isolated_pipeline(tmp_path, [], windows={})
    assert p.cfg.dir == DEMO_CONFIG_DIR
    assert p.cfg.answers_path == tmp_path / "answers.json"
    assert p.cfg.data_dir == tmp_path / "data"

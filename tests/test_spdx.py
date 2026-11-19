# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Every shippable source file carries the licence header (tools/verify/spdx.py: `SPDX-License-Identifier:
AGPL-3.0-only` and `Additional terms: see NOTICE`, in the file's own comment syntax), and the header never moves what
must come first: a shebang stays line 1, a Python module's docstring stays its first statement, an HTML page's
`<!doctype html>` stays first, a Dockerfile's parser directives stay above it. Data files carry none. Reads the tree's
files and writes only temporary copies."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from conftest import ROOT

sys.path.insert(0, str(ROOT / "tools" / "verify"))
import spdx  # noqa: E402  (tools/verify is not a package)

HEADER = [spdx.LICENSE_ID, spdx.TERMS]


def test_every_shippable_source_carries_the_header():
    assert spdx.missing(ROOT) == [], "run tools/verify/spdx.py --write"


def test_the_scan_reads_every_kind_of_source_and_no_data_file():
    found = {p.relative_to(ROOT).as_posix() for p in spdx.sources(ROOT)}
    assert len(found) > 500
    for rel in ("ff", "Makefile", "compose.yaml", "etl/pipeline.py", "app/src/App.tsx", "app/src/lib/product.ts",
                "app/build/theme.js", "app/index.html", "app/src/index.css", "deploy/public/login.html",
                "deploy/public/login.css", "deploy/public/login.js", "deploy/Caddyfile", "deploy/Dockerfile.etl",
                "deploy/Dockerfile.web", "deploy/refresh.sh", "starter/vendor_rules.yaml",
                "etl/importers/presets/bofa_card.yaml", "examples/minimal/config/household.yaml",
                "tools/verify/ci.sh", "tools/verify/spdx.py", "tests/test_spdx.py"):
        assert rel in found, rel
    for p in found:
        assert not p.startswith(("tools/private/", "docs/")) and "node_modules" not in p and "/dist/" not in p, p
        assert Path(p).suffix not in {".json", ".csv", ".ofx", ".qfx", ".png", ".svg", ".webmanifest", ".lock",
                                      ".sha256", ".md", ".toml"}, p
    assert "tests/golden/starter_cases.yaml" not in found, "a test fixture is data"


@pytest.mark.parametrize("rel", ["ff", "deploy/refresh.sh", "tools/verify/ci.sh", "tools/verify/spdx.py"])
def test_a_script_keeps_its_shebang_first(rel):
    lines = (ROOT / rel).read_text("utf-8").splitlines()
    assert lines[0].startswith("#!") and lines[1:3] == [f"# {h}" for h in HEADER]


def _is_string(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def displaced_docstring(text: str) -> bool:
    """A module whose second statement is a bare string: something above it took the docstring's place."""
    body = ast.parse(text).body
    return len(body) > 1 and _is_string(body[1]) and not _is_string(body[0])


def test_every_python_source_keeps_its_docstring_the_first_statement():
    """A module that had a docstring still has it: the header is a comment, never a statement, so no module's second
    statement is a bare string a header pushed down."""
    checked = 0
    for p in spdx.sources(ROOT):
        if p.suffix != ".py":
            continue
        text = p.read_text("utf-8")
        assert not displaced_docstring(text), f"{p}: a bare string is its second statement, not its first"
        checked += ast.get_docstring(ast.parse(text)) is not None
        assert HEADER[0] in "\n".join(text.split("\n", 3)[:3]), p
    assert checked > 150


@pytest.mark.parametrize("text, displaced", [
    ('"""Doc."""\nX = 1\n', False),
    ("# a comment\n" + '"""Doc."""\n', False),
    ('X = 1\n"""Doc."""\n', True),
    ('from os import path\n"""Doc."""\nY = 2\n', True),
    ("X = 1\nY = 2\n", False),
])
def test_a_module_whose_docstring_a_statement_pushed_down_is_found(text, displaced):
    assert displaced_docstring(text) is displaced


@pytest.mark.parametrize("rel", ["app/index.html", "deploy/public/login.html"])
def test_a_page_keeps_its_doctype_first(rel):
    lines = (ROOT / rel).read_text("utf-8").splitlines()
    assert lines[0].lower() == "<!doctype html>"
    assert lines[1:3] == [f"<!-- {h} -->" for h in HEADER]


def test_a_file_without_the_header_fails_and_the_write_adds_it_in_its_place(tmp_path):
    files = {
        "tools/verify/run.sh": "#!/usr/bin/env bash\nset -eu\n",
        "etl/mod.py": '#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n"""Doc."""\nX = 1\n',
        "etl/plain.py": '"""Doc."""\n',
        "app/src/a.ts": "export const a = 1;\n",
        "app/src/b.tsx": "// @vitest-environment jsdom\nexport {};\n",
        "app/src/c.css": '@import "x";\n',
        "app/index.html": "<!DOCTYPE html>\n<html></html>\n",
        "deploy/Dockerfile.x": "# syntax=docker/dockerfile:1\nFROM scratch\n",
        "deploy/Caddyfile": ":8080\n",
        "starter/x.yaml": "a: 1\r\nb: 2\r\n",
        "Makefile": "all:\n\ttrue",
        "app/src/data.json": "{}\n",
        "tests/golden/cases.yaml": "a: 1\n",
        "tools/private/x.py": "X = 1\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(text.encode())
    (tmp_path / "tools/verify/run.sh").chmod(0o755)
    takes = sorted(set(files) - {"app/src/data.json", "tests/golden/cases.yaml", "tools/private/x.py"})
    assert spdx.missing(tmp_path) == takes
    assert spdx.main([str(tmp_path)]) == 1
    assert spdx.main(["--write", str(tmp_path)]) == 0
    assert spdx.missing(tmp_path) == []
    assert spdx.main([str(tmp_path)]) == 0
    got = {rel: (tmp_path / rel).read_bytes().decode() for rel in files}
    hash_header = "".join(f"# {h}\n" for h in HEADER)
    assert got["tools/verify/run.sh"] == "#!/usr/bin/env bash\n" + hash_header + "set -eu\n"
    assert (tmp_path / "tools/verify/run.sh").stat().st_mode & 0o777 == 0o755, "the mode is kept"
    assert got["etl/mod.py"] == '#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n' + hash_header + '"""Doc."""\nX = 1\n'
    assert ast.get_docstring(ast.parse(got["etl/mod.py"])) == "Doc."
    assert got["etl/plain.py"] == hash_header + '"""Doc."""\n'
    assert got["app/src/a.ts"] == "".join(f"// {h}\n" for h in HEADER) + "export const a = 1;\n"
    assert got["app/src/c.css"] == "".join(f"/* {h} */\n" for h in HEADER) + '@import "x";\n'
    assert got["app/index.html"] == "<!DOCTYPE html>\n" + "".join(f"<!-- {h} -->\n" for h in HEADER) + "<html></html>\n"
    assert got["deploy/Dockerfile.x"] == "# syntax=docker/dockerfile:1\n" + hash_header + "FROM scratch\n"
    assert got["starter/x.yaml"] == hash_header + "a: 1\r\nb: 2\r\n", "every other byte as it was"
    assert got["Makefile"] == hash_header + "all:\n\ttrue"
    for rel in ("app/src/data.json", "tests/golden/cases.yaml", "tools/private/x.py"):
        assert got[rel] == files[rel], rel
    again = {rel: (tmp_path / rel).read_bytes() for rel in files}
    assert spdx.main(["--write", str(tmp_path)]) == 0
    assert {rel: (tmp_path / rel).read_bytes() for rel in files} == again, "a second pass changes nothing"


def test_a_header_in_the_wrong_place_or_half_of_one_is_no_header(tmp_path):
    hash_header = "".join(f"# {h}\n" for h in HEADER)
    cases = {
        "a.sh": hash_header + "#!/bin/sh\n",                   # above the shebang, which then is no shebang
        "b.py": '"""Doc."""\n' + hash_header,                  # below the docstring
        "c.yaml": f"# {spdx.LICENSE_ID}\nx: 1\n",              # the terms line missing
        "d.ts": f"// {spdx.LICENSE_ID.replace('-only', '-or-later')}\n// {spdx.TERMS}\n",
    }
    for rel, text in cases.items():
        assert not spdx.has_header(text, spdx.style_of(rel)), rel

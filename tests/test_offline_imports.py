# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The offline path stays offline: in a fresh interpreter, importing the pipeline and running an ingest of the minimal
example household, and of the demo's `full` household, loads no module that talks to a network — no standard-library client (urllib.request, http.client,
ssl, socket), no third-party one (requests, httpx, httpx2, anthropic, openai, certifi's bundle) and none of the project's
networked modules (the bank feed's fetcher, its network helpers, the AI step's providers). The AI step's offline
modules, which the ingest may import, load none of them either. A fresh interpreter because the test process itself
has imported everything at some point; the run writes only inside its own temporary household."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import DEMO_NOW, ROOT

from etl.config import PATH_VARIABLES

NETWORKED = ("urllib.request", "http.client", "ssl", "socket", "requests", "httpx", "httpx2", "anthropic", "openai",
             "certifi", "etl.simplefin", "etl._net", "etl.ai.providers")

# what the child runs: argv[1] is the household's FF_HOME; it prints the exit code and the networked modules loaded
INGEST = """
import json, os, sys
os.environ["FF_HOME"] = sys.argv[1]
import etl.pipeline
rc = etl.pipeline.run()
print(json.dumps({"rc": rc, "loaded": sorted(m for m in sys.argv[2:] if m in sys.modules)}))
"""

IMPORTS = """
import json, sys
import etl.pipeline, etl.ai, etl.ai.policy, etl.ai.candidates, etl.ai.request, etl.ai.cache, etl.ai.cli
print(json.dumps({"rc": 0, "loaded": sorted(m for m in sys.argv[2:] if m in sys.modules)}))
"""


def _child(code: str, home: Path, now: str | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in PATH_VARIABLES and k != "FF_NOW"}
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    if now is not None:
        env["FF_NOW"] = now
    done = subprocess.run([sys.executable, "-c", code, str(home), *NETWORKED], cwd=home, env=env,
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def _minimal(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    shutil.copytree(ROOT / "examples" / "minimal", home)
    return home


def test_an_ingest_of_the_minimal_household_loads_no_networked_module(tmp_path):
    home = _minimal(tmp_path)
    result = _child(INGEST, home)
    assert result == {"rc": 0, "loaded": []}
    assert (home / "data" / "exports" / "manifest.json").exists()        # the run did ingest


def test_an_ingest_of_the_full_demo_household_loads_no_networked_module(tmp_path, demo_home):
    """The demo's `full` household reaches every reader the minimal one does not: the bank feed's snapshots, the
    household's own presets, OFX files, splits and manual assets."""
    home = tmp_path / "home"
    shutil.copytree(demo_home.path, home)
    assert _child(INGEST, home, now=DEMO_NOW) == {"rc": 0, "loaded": []}
    assert (home / "data" / "exports" / "manifest.json").exists()


def test_the_ai_steps_offline_modules_load_no_networked_module(tmp_path):
    assert _child(IMPORTS, _minimal(tmp_path)) == {"rc": 0, "loaded": []}


def test_the_check_sees_a_networked_module_when_one_is_loaded(tmp_path):
    """The child's report is not blind: a module it is told to look for and that is loaded is reported."""
    code = INGEST.replace("import etl.pipeline\n", "import etl.pipeline, http.client\n")
    assert "http.client" in _child(code, _minimal(tmp_path))["loaded"]

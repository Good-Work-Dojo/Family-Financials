# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The reads and writes that never go through a link (deploy/safe_files.py's docstring says why and how), shared
with the store and the refresh worker: that one module, loaded by its path, since the image and every checkout carry
deploy/ beside etl/ and the deploy programs never import etl."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SAFE_FILES_PATH = Path(__file__).resolve().parent.parent / "deploy" / "safe_files.py"


def _load():
    name = "ff_deploy_safe_files"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SAFE_FILES_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


_safe = _load()
read_regular = _safe.read_regular
write_atomic = _safe.write_atomic
open_lock = _safe.open_lock
NOT_REGULAR = _safe.NOT_REGULAR
TOO_LARGE = _safe.TOO_LARGE
MAX_BYTES = _safe.MAX_BYTES

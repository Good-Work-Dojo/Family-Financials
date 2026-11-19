# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The harness's names: the images it builds and runs, the label on everything it starts, the compose project and
container prefix, the default scratch root, and the wrapper the stack test runs compose through.

One settings file, tools/private/harness.env at the repository root, sets them when it exists (plain `KEY=value`
lines, a value optionally in single or double quotes, `#` comment lines); the shell scripts source it through
tools/verify/harness.sh, the Python scripts read it here, and test_harness_names.py holds the two to each other. A
file that exists names every setting, each with a value, and the environment is not read: a setting it leaves out or
leaves empty raises ValueError, so a lost line never falls back to a default label or scratch root; so does a
harness.env that is a link (dangling or not) or anything but a plain file. Without the file
each setting comes from the environment, and an unset or empty one is the default:

  FF_HARNESS_IMAGE_PREFIX      family-financials         the images: <prefix>-etl and <prefix>-web
  FF_HARNESS_TEST_TAG          test                      the test images' tag (the runtime images' is rt)
  FF_HARNESS_LABEL             family-financials.test=1  the label (key=value) on every container, image and network
                                                         the harness's own docker calls make (with no compose
                                                         wrapper, the stack's compose objects carry only compose's
                                                         project label)
  FF_HARNESS_PROJECT_PREFIX    family-financials-test    the stack's compose project (<prefix>-stack) and the names of
                                                         the containers and networks the harness starts itself
  FF_HARNESS_SCRATCH           ff-verify                 the default scratch root: this directory in the account's
                                                         home directory
  FF_HARNESS_COMPOSE           (empty: docker compose)   a program the stack test runs compose through, a path
                                                         relative to the repository root or absolute
  FF_HARNESS_COMPOSE_PORT_VAR  (empty)                   the variable that program reads the stack's port from

A value of another shape, an unknown FF_HARNESS_ key or a line of another shape in the file raises ValueError."""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_FILE = Path("tools") / "private" / "harness.env"
RUNTIME_TAG = "rt"

# each setting's default and the shape its value must have (harness.sh checks the same)
DEFAULTS = {
    "FF_HARNESS_IMAGE_PREFIX": "family-financials",
    "FF_HARNESS_TEST_TAG": "test",
    "FF_HARNESS_LABEL": "family-financials.test=1",
    "FF_HARNESS_PROJECT_PREFIX": "family-financials-test",
    "FF_HARNESS_SCRATCH": "ff-verify",
    "FF_HARNESS_COMPOSE": "",
    "FF_HARNESS_COMPOSE_PORT_VAR": "",
}
SHAPES = {
    "FF_HARNESS_IMAGE_PREFIX": r"[a-z0-9][a-z0-9._-]*",
    "FF_HARNESS_TEST_TAG": r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}",
    "FF_HARNESS_LABEL": r"[A-Za-z0-9][A-Za-z0-9._-]*=[A-Za-z0-9._-]+",
    "FF_HARNESS_PROJECT_PREFIX": r"[a-z0-9][a-z0-9_-]*",
    "FF_HARNESS_SCRATCH": r"[A-Za-z0-9_][A-Za-z0-9._-]*",
    "FF_HARNESS_COMPOSE": r"([A-Za-z0-9_./-]+)?",
    "FF_HARNESS_COMPOSE_PORT_VAR": r"([A-Z_][A-Z0-9_]*)?",
}
# what a line of the settings file may be: a comment, blank, or one assignment whose value a shell and this reader
# read alike (no expansion, escape or whitespace)
_VALUE = r"[A-Za-z0-9._/=:@+-]*"
_LINE = re.compile(rf"([A-Z][A-Z0-9_]*)=(?:'({_VALUE})'|\"({_VALUE})\"|({_VALUE}))")


@dataclass(frozen=True)
class Harness:
    image_prefix: str
    test_tag: str
    label: str
    project_prefix: str
    scratch: str
    compose: str
    compose_port_var: str

    @property
    def etl_test(self) -> str:
        return f"{self.image_prefix}-etl:{self.test_tag}"

    @property
    def web_test(self) -> str:
        return f"{self.image_prefix}-web:{self.test_tag}"

    @property
    def etl_rt(self) -> str:
        return f"{self.image_prefix}-etl:{RUNTIME_TAG}"

    @property
    def web_rt(self) -> str:
        return f"{self.image_prefix}-web:{RUNTIME_TAG}"


def read_file(path: Path) -> dict[str, str]:
    """The settings a file assigns, in order (a later line wins, as in a shell)."""
    found: dict[str, str] = {}
    for n, line in enumerate(path.read_text("utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        m = _LINE.fullmatch(line)
        if not m:
            raise ValueError(f"{path} line {n} is not a plain KEY=value assignment")
        if m.group(1) not in DEFAULTS:
            raise ValueError(f"{path} line {n} sets {m.group(1)}, which is not a harness setting")
        found[m.group(1)] = next(v for v in m.groups()[1:] if v is not None)
    return found


def load(root: Path = ROOT, environ: Mapping[str, str] | None = None) -> Harness:
    """The harness's names for the repository at `root`, from its settings file, the environment and the defaults."""
    environ = os.environ if environ is None else environ
    path = root / SETTINGS_FILE
    values = {}
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"{SETTINGS_FILE} is a link or no plain file: the settings are read only from a plain file "
                         "there")
    if path.is_file():
        assigned = read_file(path)
        for key in DEFAULTS:
            if not assigned.get(key):
                raise ValueError(f"{SETTINGS_FILE} sets no {key}, or an empty one: the file must name every setting "
                                 "(remove the file to use the defaults)")
        chosen = assigned
    else:
        chosen = {key: environ.get(key, "") for key in DEFAULTS}
    for key, default in DEFAULTS.items():
        value = chosen[key] or default
        if not re.fullmatch(SHAPES[key], value):
            raise ValueError(f"{key} is {value!r}, which is not of the form {SHAPES[key]}")
        values[key] = value
    if values["FF_HARNESS_TEST_TAG"] == RUNTIME_TAG:
        raise ValueError(f"FF_HARNESS_TEST_TAG may not be {RUNTIME_TAG}, the runtime images' tag")
    return Harness(*(values[key] for key in DEFAULTS))
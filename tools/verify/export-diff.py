#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Thin wrapper: run deploy/export_diff.py INSIDE the ETL image with the two export dirs
mounted read-only.

usage: tools/verify/export-diff.py <base> <head> --expect <policy> [--allow-removed SPEC]...
                                   [--allow-changed SPEC]... [--config-dir DIR]
       --allow-removed is taken under removals and refactor, --allow-changed under refactor only
       (the module's docstring has the grammar).
       --config-dir DIR  a household's config directory, mounted read-only: its vendor_rules.yaml
       supplies the patterns vendor set and the renames vendors-fallback-only reads (that policy
       needs it; no other reads it). Without it no rules file is passed.
       <base>/<head> may be a bare label (base | head | final -> <scratch root>/<label>),
       <scratch root>/<label>, <scratch root>/<label>/data or the exports dir itself; the first
       dir containing manifest.json wins. The scratch root is $FF_SCRATCH (absolute; never /,
       the home directory or above it), default the harness's scratch directory (harness_names.py)
       in the account's home directory (from the account database, never $HOME).

Exports are treated as real: each dir must resolve strictly inside the scratch root, and only
aggregates are printed. FF_DEMO_EXPORTS=1 marks synthetic exports and lifts both rules.

Exit codes pass through: 0 within policy, 1 outside policy, 2 usage, 3 fetched-at mismatch.
The module that runs is the one baked into $FF_ETL_IMAGE (default the harness's ETL test image)
by the last ci.sh build — after editing deploy/export_diff.py, rebuild first. The container
carries the harness's label.
The image holds no config, so the vendor rules come only from --config-dir.
"""
import os
import pwd
import shlex
import subprocess
import sys

import harness_names

ROW_OUTPUT = "--show-rows"
CONFIG_DIR_FLAG = "--config-dir"
# where the config directory is mounted, and the one policy that reads its vendor rules
CONFIG_MOUNT = "/mnt/config"
VENDOR_POLICY = "vendors-fallback-only"


def names() -> harness_names.Harness:
    """The harness's names; a settings value of another shape is a usage error."""
    try:
        return harness_names.load()
    except ValueError as e:
        print(f"export-diff: {e}", file=sys.stderr)
        raise SystemExit(2) from None


def home() -> str:
    return pwd.getpwuid(os.getuid()).pw_dir


def expand(p: str) -> str:
    """A leading ~ means the account's home directory, never $HOME."""
    return home() + p[1:] if p == "~" or p.startswith("~/") else p


def scratch_root() -> str:
    """$FF_SCRATCH or the harness's scratch directory in the home directory; absolute, and never /, the home directory
    or above it."""
    raw = os.environ.get("FF_SCRATCH") or os.path.join(home(), names().scratch)
    if not os.path.isabs(raw):
        print(f"export-diff: FF_SCRATCH must be an absolute path (got {raw!r})", file=sys.stderr)
        raise SystemExit(2)
    root, own = os.path.realpath(raw), os.path.realpath(home())
    if root == os.sep or own == root or own.startswith(root + os.sep):
        print(f"export-diff: FF_SCRATCH must not be /, the home directory or a directory above it (got {raw!r})",
              file=sys.stderr)
        raise SystemExit(2)
    return root


def asks_for_rows(arg: str) -> bool:
    """True for the row-output option or any prefix of it argparse would accept as an abbreviation."""
    name = arg.split("=", 1)[0]
    return len(name) > 2 and ROW_OUTPUT.startswith(name)


def strictly_inside(path: str, root: str) -> bool:
    """A descendant of the resolved root, never the root itself; symlinks are resolved first."""
    return os.path.realpath(path).startswith(os.path.realpath(root) + os.sep)


def usage_error(message: str) -> SystemExit:
    print(f"export-diff: {message}", file=sys.stderr)
    return SystemExit(2)


def take_config_dir(args: list[str]) -> tuple[str | None, list[str]]:
    """The resolved --config-dir DIR (or --config-dir=DIR), given at most once, and the other arguments in their order.
    DIR must be an existing directory whose path holds no comma (docker --mount splits on commas)."""
    found: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == CONFIG_DIR_FLAG:
            if i + 1 >= len(args):
                raise usage_error(f"{CONFIG_DIR_FLAG} needs a directory")
            found.append(args[i + 1])
            i += 2
            continue
        if a.startswith(CONFIG_DIR_FLAG + "="):
            found.append(a.split("=", 1)[1])
        else:
            rest.append(a)
        i += 1
    if len(found) > 1:
        raise usage_error(f"{CONFIG_DIR_FLAG} is given more than once")
    if not found:
        return None, rest
    d = os.path.realpath(expand(found[0]))
    if not os.path.isdir(d):
        raise usage_error(f"{CONFIG_DIR_FLAG} {found[0]} is not a directory")
    if "," in d:
        raise usage_error(f"{CONFIG_DIR_FLAG} {d} contains a comma (docker --mount splits on commas)")
    return d, rest


def policy_of(args: list[str]) -> str | None:
    """The --expect value as written (`--expect P` or `--expect=P`), else None (the module reports a missing one)."""
    for i, a in enumerate(args):
        if a == "--expect" and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--expect="):
            return a.split("=", 1)[1]
    return None


def exports_dir(p: str, scratch: str) -> str:
    roots = [os.path.abspath(expand(p))]
    if os.sep not in p:  # a bare label such as `base`: also look under the scratch root
        roots.append(os.path.join(scratch, p))
    for root in roots:
        for cand in (root, os.path.join(root, "data", "exports"), os.path.join(root, "exports")):
            if os.path.isfile(os.path.join(cand, "manifest.json")):
                return cand
    # exit 2 (usage), never sys.exit(str)'s status 1, which would read as "outside policy"
    print(f"export-diff: no manifest.json under {' or '.join(roots)}", file=sys.stderr)
    raise SystemExit(2)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0].startswith("-"):
        print(__doc__, file=sys.stderr)
        return 2
    real = os.environ.get("FF_DEMO_EXPORTS") != "1"
    if real and any(asks_for_rows(a) for a in argv):
        print("export-diff: exports are treated as real, so row-level output is refused "
              "(FF_DEMO_EXPORTS=1 is for synthetic exports only)", file=sys.stderr)
        return 2
    config, passed = take_config_dir(argv[2:])
    if config is None and policy_of(passed) == VENDOR_POLICY:
        raise usage_error(f"{VENDOR_POLICY} reads the patterns and renames of a config's vendor_rules.yaml: pass "
                          f"{CONFIG_DIR_FLAG} DIR")
    scratch = scratch_root()  # checked even for FF_DEMO_EXPORTS=1: bare labels resolve under it
    dirs = [exports_dir(argv[0], scratch), exports_dir(argv[1], scratch)]
    if real:
        for side, d in zip(("base", "head"), dirs, strict=True):
            if not strictly_inside(d, scratch):
                print(f"export-diff: {side} {os.path.realpath(d)} does not resolve strictly inside the scratch root "
                      f"{os.path.realpath(scratch)} (FF_DEMO_EXPORTS=1 is for synthetic exports only)", file=sys.stderr)
                return 2
    base, head = (os.path.realpath(d) for d in dirs)
    rules, mount = [], []
    if config is not None:
        rules = ["--vendor-rules", f"{CONFIG_MOUNT}/vendor_rules.yaml"]
        mount = ["--mount", f"type=bind,source={config},target={CONFIG_MOUNT},readonly"]
    inner = ("cd /app/finance-app && exec .venv/bin/python deploy/export_diff.py "
             + shlex.join(["/mnt/base", "/mnt/head", *rules, *passed]))
    cmd = ["docker", "run", "--rm", "--label", names().label, "--user", "1000:1000",
           "-v", f"{base}:/mnt/base:ro", "-v", f"{head}:/mnt/head:ro", *mount,
           os.environ.get("FF_ETL_IMAGE", names().etl_test), "sh", "-lc", inner]
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

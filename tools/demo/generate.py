# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Generate a demo household's FF_HOME tree and the truth its exports must equal.

    python -m tools.demo.generate --profile full --end 2026-10-31 --out <dir>

writes `<dir>/config/`, `<dir>/imports/`, `<dir>/store/simplefin/snapshots/`, `<dir>/state/family/answers.json`
(when the family answered anything; never the learned rules, which the ingest writes in `state/engine/`),
`<dir>/state/family/manual_assets.json` (when it entered an asset in the app) and `<dir>/truth.json`, then prints the FF_NOW to ingest it at (an hour after the end
day's last snapshot) and the paths it wrote, relative to `<dir>`. An unknown profile, an end that is not a date, or a
`<dir>` that exists and is not empty: one line on stderr, exit 2, nothing written. Nothing reads the wall clock, and
two runs write identical bytes.

    python -m tools.demo.generate --check [--hashes <file>]
    python -m tools.demo.generate --bless [--hashes <file>]

regenerate every profile at BLESSED_END into a temporary directory and hash its inputs (`input_hash`): `--check`
compares the hashes with the file (default tools/demo/inputs.sha256, one `<hash>  <profile>` line per profile) and
exits 1 with one stderr line per profile that differs (exit 2 and one line naming the profile when one fails to
generate); `--bless` rewrites the file and prints it. A change to what
the generator writes changes a hash: re-bless with the change, in the same commit."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

from . import config_writer, profiles, sources, truth
from . import plan as plan_mod

INGEST_TIME = f"{plan_mod.INGEST_HOUR:02d}:00:00"
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
INPUT_DIRS = ("config", "imports", "store", "state")      # what the generator writes for the engine to read
WRITTEN_BY_THE_INGEST = ("state/engine/learned_rules.json",)   # beside the inputs, never one of them
BLESSED_END = date(2026, 10, 31)                          # the end every blessed hash is generated to
HASHES = Path(__file__).with_name("inputs.sha256")


def input_hash(out: Path) -> str:
    """The sha256 of the generated inputs under `out`: every file under config/, imports/, store/ and state/ (the
    learned rules aside: the ingest writes them) and truth.json, each as its path relative to `out`, its size and its
    bytes, in path order. data/, what an ingest writes, never counts."""
    files = {p.relative_to(out).as_posix(): p for d in INPUT_DIRS if (out / d).is_dir()
             for p in (out / d).rglob("*") if p.is_file()}
    files["truth.json"] = out / "truth.json"
    digest = hashlib.sha256()
    for name in sorted(set(files) - set(WRITTEN_BY_THE_INGEST)):
        data = files[name].read_bytes()
        digest.update(f"{name}\0{len(data)}\0".encode() + data)
    return digest.hexdigest()


def run(profile: str, end: date, out: Path) -> dict:
    """Write the tree of `profile` ending on `end` into `out` (absent or empty) and return its truth."""
    chosen = profiles.get(profile)
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} exists and is not empty")
    return write_tree(plan_mod.build(chosen, end), out)


def write_tree(plan: plan_mod.Plan, out: Path) -> dict:
    """Write the tree of a built plan into `out` (the config, the statement files and snapshots, the family's answers
    and manual assets, truth.json) and return its truth."""
    config_writer.write_plan(plan, out / "config")
    written = sources.write(plan, out)
    # a row answer names the row's stored uid, which the files and snapshots just written decide
    sources.write_answers(plan, out, truth.stored_rows(plan, written).uid_of)
    sources.write_manual_assets(plan, out)
    doc = truth.build(plan, written)
    (out / "truth.json").write_text(json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8", newline="")
    return doc


class GenerationError(Exception):
    """A profile that failed to generate while its hash was being taken; the message names the profile."""


def hashes(end: date = BLESSED_END) -> dict[str, str]:
    """Each profile's input hash, generated to `end` in a temporary directory, in profile order."""
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name in profiles.NAMES:
            try:
                run(name, end, Path(tmp) / name)
            except Exception as e:
                raise GenerationError(f"{name}: generating failed: {type(e).__name__}: {e}") from e
            out[name] = input_hash(Path(tmp) / name)
    return out


def check(path: Path) -> list[str]:
    """One line per profile whose inputs hash otherwise than the file `path` blesses (or that it does not name), and
    per name it blesses that no profile has; [] when every hash is the blessed one."""
    blessed = {}
    for line in path.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        blessed[name] = digest
    now = hashes()
    return ([f"{name}: the inputs hash {digest}, blessed {blessed.get(name, 'nothing')}"
             for name, digest in now.items() if blessed.get(name) != digest]
            + [f"{name}: blessed, and no profile has that name" for name in blessed if name not in now])


def bless(path: Path) -> str:
    """Write every profile's input hash to `path`, one `<hash>  <profile>` line each, and return the text."""
    text = "".join(f"{digest}  {name}\n" for name, digest in hashes().items())
    path.write_text(text, encoding="utf-8", newline="")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.demo.generate", description=__doc__.splitlines()[0])
    parser.add_argument("--profile")
    parser.add_argument("--end", help="the last day of the history, YYYY-MM-DD")
    parser.add_argument("--out", type=Path, help="the directory to write the tree into")
    parser.add_argument("--check", action="store_true", help="compare every profile's input hash with the file's")
    parser.add_argument("--bless", action="store_true", help="rewrite the file with every profile's input hash")
    parser.add_argument("--hashes", type=Path, help=f"the hash file (default {HASHES.name} beside this module)")
    args = parser.parse_args(argv)
    given = [f"--{key}" for key in ("profile", "end", "out") if getattr(args, key) is not None]
    if args.check or args.bless:
        return _hash_command(args, given)
    if args.hashes is not None:
        return _refuse("--hashes goes with --check or --bless")
    if len(given) < 3:
        return _refuse("generating a tree needs --profile, --end and --out")
    if args.profile not in profiles.PROFILES:
        return _refuse(f"unknown profile {args.profile!r} (known: {', '.join(profiles.PROFILES)})")
    try:
        if not DATE.fullmatch(args.end):          # fromisoformat alone also takes 20261031 and week dates
            raise ValueError(args.end)
        end = date.fromisoformat(args.end)
    except ValueError:
        return _refuse(f"--end {args.end!r} is not a date (YYYY-MM-DD)")
    if args.out.exists() and (not args.out.is_dir() or any(args.out.iterdir())):
        return _refuse(f"{args.out} exists and is not an empty directory")
    run(args.profile, end, args.out)
    print(f"FF_NOW={end.isoformat()}T{INGEST_TIME}")
    for path in sorted(p for p in args.out.rglob("*") if p.is_file()):
        print(path.relative_to(args.out))
    return 0


def _hash_command(args: argparse.Namespace, given: list[str]) -> int:
    if args.check and args.bless:
        return _refuse("--check and --bless are two commands: give one")
    mode = "--check" if args.check else "--bless"
    if given:
        return _refuse(f"{mode} takes no {', '.join(given)}: it generates every profile at {BLESSED_END}")
    path = args.hashes or HASHES
    try:
        if args.bless:
            print(bless(path), end="")
            return 0
        if not path.is_file():
            return _refuse(f"{path} is not a hash file to check against")
        differ = check(path)
    except GenerationError as e:
        return _refuse(str(e))
    for line in differ:
        print(f"generate: {line}", file=sys.stderr)
    if not differ:
        print(f"the inputs of {len(profiles.NAMES)} profiles at {BLESSED_END} hash as {path} blesses")
    return 1 if differ else 0


def _refuse(message: str) -> int:
    print(f"generate: {message}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

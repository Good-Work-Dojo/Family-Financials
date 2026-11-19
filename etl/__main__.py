# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""CLI:
  python -m etl ingest [--source-dir DIR] [--strict]
  python -m etl validate [--source-dir DIR]                         (offline, read-only)
  python -m etl simplefin claim --token <SETUP_TOKEN> [--force]
  python -m etl simplefin fetch [--days N]
  python -m etl simplefin list-accounts
  python -m etl rules preview --file CANDIDATES.yaml [--show-rows]   (offline, read-only)
  python -m etl rules overlaps
  python -m etl rules starter-diff [--starter DIR]                   (offline, read-only; reads the YAML as it is)
  python -m etl ai preview [--source-dir DIR]                         (offline, writes nothing; etl/ai/cli.py)
  python -m etl ai run [--refresh] [--from-refresh] [--source-dir DIR]  (asks the AI provider; etl/ai/providers.py)
  python -m etl ai reset                                             (forgets the stored AI answers)
  python -m etl import detect FILE                                   (offline, writes nothing; needs no household)
  python -m etl import preview FILE --format NAME [--account ID]     (offline, writes nothing)
  python -m etl import map FILE --id NAME [--answers FILE] [--force] (offline; writes config/presets/NAME.yaml)
  python -m etl import banks [--write]                               (offline; --write writes docs/importing-banks.md)
  python -m etl passcode add LABEL [--stdin] | remove LABEL | list   (the sign-in passcodes; etl/passcode_cli.py)
  python -m etl init [--answers FILE] [--force]                     (the setup conversation; etl/setup_cli.py)
  python -m etl demo [--force]                                      (the invented household; etl/demo_cli.py)
  python -m etl doctor                                              (read-only; etl/doctor_cli.py)
  python -m etl backup [--include-secrets] | restore FILE [--force]  (etl/backup_cli.py)
  python -m etl preflight                                           (before `ff up`; etl/stack_cli.py)
  python -m etl upgrade-plan start [--to TAG] | failed STAGE | done   (the Python half of `ff upgrade`)

`validate` prints every problem of the config (exit 1) or `config OK (N files, M accounts)` (exit 0), with the count
of the household's own presets (config/presets/) when it has any, then any note that refuses nothing (validate.ai_notes). `ingest`,
`rules`, `ai preview` and `ai run` refuse a config with problems, printing the same lines to stderr and exiting 2 before anything is written; the
`simplefin` commands check only what a fetch reads (config.FeedConfig) and refuse the same way. With no config
directory at all, each prints one line instead (validate.missing_config_dir_problem): where it looked, and what to
correct (the variable or argument that named the place) or to set.

`import` finds out what a statement file is before an account trusts it (etl/import_cli.py): `detect` tries every
format on it (exit 0 when exactly one reads it), `preview` prints what one format reads from it and, with an account,
how many of its rows are stored already (that needs the config), and `map` describes a layout no format reads as the
household's own preset, asking on a terminal or reading `--answers`; `banks` prints the table of the banks the release
reads a download of, which `--write` writes into docs/. A usage error exits 2 with one line.

`passcode` keeps the web secrets folder's passcodes file (hashes only; never printed): `add` asks twice without echo,
or reads one line with `--stdin`, and refuses a passcode under 8 characters (10 when all digits); `list` prints the
labels. A refusal exits 2 with one line and writes nothing.

The install's own commands are what `./ff` runs in the image as the invoking user, with FF_HOME the install
directory (etl/install.py holds what they share: its directories and their modes, the settings file). `init` and
`demo` make a household, `doctor` checks an install without changing it (exit 1 on a failed check), `backup` and
`restore` move the household's own files to and from one archive, `preflight` readies an install for compose (and
refuses one that cannot start safely), `upgrade-plan` is the Python half of an upgrade. Each prints plain lines and
exits 2 with one line on a refusal, never with a traceback.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable
from pathlib import Path

# --source-dir is Config's source_dir argument, so it wins over FF_IMPORTS_DIR and FF_HOME (etl/config.py Paths)
SOURCE_DIR_HELP = "the read-only statements folder (wins over FF_IMPORTS_DIR and FF_HOME)"


def _print_problems(problems: Iterable, stream) -> None:
    for problem in problems:
        print(problem, file=stream)


def main() -> int:
    ap = argparse.ArgumentParser(prog="etl", description="Family Financials ETL: statements and bank feed in, local database and JSON exports out")
    sub = ap.add_subparsers(dest="cmd")

    ing = sub.add_parser("ingest", help="re-ingest everything (default)")
    ing.add_argument("--source-dir", default=None, help=SOURCE_DIR_HELP)
    ing.add_argument("--strict", action="store_true",
                     help="exit non-zero if any quality check fails")

    va = sub.add_parser("validate", help="check the config files and print every problem (offline, read-only)")
    va.add_argument("--source-dir", default=None, help=SOURCE_DIR_HELP)

    sf = sub.add_parser("simplefin", help="SimpleFIN Bridge (network)")
    sfsub = sf.add_subparsers(dest="sfcmd")
    cl = sfsub.add_parser("claim", help="exchange a setup token for the access URL")
    cl.add_argument("--token", required=True, help="base64 SimpleFIN setup token")
    cl.add_argument("--force", action="store_true",
                    help="overwrite an existing stored credential")
    fe = sfsub.add_parser("fetch", help="fetch one all-accounts snapshot")
    fe.add_argument("--days", type=int, default=None,
                    help="transaction look-back window (<=90; default 90 while no snapshot is stored, then 35)")
    sfsub.add_parser("list-accounts", help="list the newest snapshot's accounts")

    ru = sub.add_parser("rules", help="categorization rule tooling (offline, read-only)")
    rusub = ru.add_subparsers(dest="rucmd")
    pv = rusub.add_parser("preview", help="dry-run candidate keyword rules against the live data")
    pv.add_argument("--file", required=True,
                    help="YAML: a list of rule entries, or a mapping with keyword_rules: [...]")
    pv.add_argument("--show-rows", action="store_true",
                    help="print date/amount/descriptor per changed row (on-box eyeballing only)")
    pv.add_argument("--source-dir", default=None, help=SOURCE_DIR_HELP)
    ov = rusub.add_parser("overlaps", help="zero-hit and shadowed keyword rules in the live rulebook")
    ov.add_argument("--source-dir", default=None, help=SOURCE_DIR_HELP)
    sd = rusub.add_parser("starter-diff", help="starter entries your config files lack (offline, read-only)")
    sd.add_argument("--starter", default=None,
                    help="the starter directory (default: FF_STARTER_DIR, else starter/ beside the etl package)")

    ai = sub.add_parser("ai", help="the optional AI step that suggests categories for uncategorized vendors")
    aisub = ai.add_subparsers(dest="aicmd")
    ap_ = aisub.add_parser("preview", help="print exactly what would be sent and what is kept back (offline, "
                                           "writes nothing, needs no key)")
    ap_.add_argument("--source-dir", default=None, help=SOURCE_DIR_HELP)
    ar = aisub.add_parser("run", help="ask the provider about the vendors no rule places and store its answers (only "
                                      "when switched on in the app and a key is stored; within the caps)")
    ar.add_argument("--source-dir", default=None, help=SOURCE_DIR_HELP)
    ar.add_argument("--refresh", action="store_true", help="ask about vendors answered already too, oldest first")
    ar.add_argument("--from-refresh", action="store_true",
                    help="the refresh's own step: nothing printed while the step is off, one line otherwise")
    aisub.add_parser("reset", help="forget the stored answers (the run log is kept)")

    im = sub.add_parser("import", help="what a statement file is, and your own file layouts (offline)")
    imsub = im.add_subparsers(dest="imcmd")
    de = imsub.add_parser("detect", help="which formats read a file, or the nearest ones and why not (writes nothing)")
    de.add_argument("file", help="the statement file")
    pr = imsub.add_parser("preview", help="what one format reads from a file (writes nothing)")
    pr.add_argument("file", help="the statement file")
    pr.add_argument("--format", required=True, help="the format to read it with, as an account's parser: names it")
    pr.add_argument("--account", default=None,
                    help="an account id: also count the file's rows already stored for it (needs the config)")
    ma = imsub.add_parser("map", help="describe a file's layout as your own preset, config/presets/NAME.yaml")
    ma.add_argument("file", help="the statement file")
    ma.add_argument("--id", required=True, help="the new format's name: lowercase letters, digits and _")
    ma.add_argument("--answers", default=None, help="a YAML file of the answers, to run without a terminal")
    ma.add_argument("--force", action="store_true", help="replace a preset of that name")
    ba = imsub.add_parser("banks", help="the table of the banks this release reads a download of")
    ba.add_argument("--write", action="store_true", help="write it to docs/importing-banks.md instead of printing it")

    pc = sub.add_parser("passcode", help="the sign-in passcodes (stored as hashes, never printed)")
    pcsub = pc.add_subparsers(dest="pccmd")
    pa = pcsub.add_parser("add", help="add a passcode, or replace the one of that label")
    pa.add_argument("label", help="who it is for: 1 to 32 lowercase letters, digits, - or _")
    pa.add_argument("--stdin", action="store_true", help="read the passcode as one line of standard input")
    rm = pcsub.add_parser("remove", help="remove the passcode of a label")
    rm.add_argument("label")
    pcsub.add_parser("list", help="the labels of the passcodes")

    it = sub.add_parser("init", help="set up a household: the conversation, or --answers FILE")
    it.add_argument("--answers", default=None,
                    help="a YAML file of the answers (it may hold secrets: it must be readable by you alone)")
    it.add_argument("--force", action="store_true", help="ask again for household.yaml and accounts.yaml")
    it.add_argument("--offline", action="store_true",
                    help="the run has no network: ask for no bank feed setup token (./ff passes it)")
    dm = sub.add_parser("demo", help="the invented Sample household in an empty install directory")
    dm.add_argument("--force", action="store_true", help="make it although the directory holds other files")
    sub.add_parser("doctor", help="check the install and say how to fix each problem (read-only)")
    bk = sub.add_parser("backup", help="the household's files in backups/ff-<stamp>.tar.gz")
    bk.add_argument("--include-secrets", action="store_true", help="the credentials too")
    rs = sub.add_parser("restore", help="a backup back into the install")
    rs.add_argument("file", help="the backup, a file inside the install directory")
    rs.add_argument("--force", action="store_true", help="replace the files config/ and store/ already hold")
    sub.add_parser("preflight", help="ready the install for `ff up`, or say why it cannot start")
    up = sub.add_parser("upgrade-plan", help="the steps of `ff upgrade` that run in the image")
    upsub = up.add_subparsers(dest="upcmd")
    st = upsub.add_parser("start", help="back up, keep the settings as .env.previous, move to a release")
    st.add_argument("--to", default=None, help="the release's tag")
    fa = upsub.add_parser("failed", help="where an upgrade stopped and how to go back")
    fa.add_argument("stage", choices=["pull", "validate", "up", "ingest", "doctor"])
    upsub.add_parser("done", help="the upgrade finished")

    args = ap.parse_args()

    if args.cmd == "init":
        from . import setup_cli
        return setup_cli.run(args.answers, force=args.force, offline=args.offline)
    if args.cmd == "demo":
        from . import demo_cli
        return demo_cli.run(force=args.force)
    if args.cmd == "doctor":
        from . import doctor_cli
        return doctor_cli.run()
    if args.cmd in ("backup", "restore"):
        from . import backup_cli
        return backup_cli.run(args.cmd, file=getattr(args, "file", None),
                              include_secrets=getattr(args, "include_secrets", False),
                              force=getattr(args, "force", False))
    if args.cmd == "preflight":
        from . import stack_cli
        return stack_cli.run("preflight")
    if args.cmd == "upgrade-plan":
        if args.upcmd not in ("start", "failed", "done"):
            up.print_help()
            return 2
        from . import stack_cli
        return stack_cli.run(args.upcmd, stage=getattr(args, "stage", None), to=getattr(args, "to", None))

    if args.cmd in (None, "ingest"):
        from . import clock, pipeline
        from .validate import ConfigError
        try:
            clock.check()          # a pinned clock that is no date and time, before the lock or any directory
        except ConfigError as e:
            _print_problems(e.problems, sys.stderr)
            return 2
        source_dir = getattr(args, "source_dir", None)
        # every problem, the statements folder included, before the run opens the database or writes anything (the
        # check `validate` makes); the run then reads this Config, so the files are loaded once
        try:
            cfg = pipeline.Config(source_dir=source_dir, check_paths=True)
        except ConfigError as e:
            _print_problems(e.problems, sys.stderr)
            return 2
        return pipeline.run(strict=getattr(args, "strict", False), cfg=cfg)

    if args.cmd == "validate":
        from .config import Config
        from .validate import ConfigError
        try:
            cfg = Config(source_dir=args.source_dir, check_paths=True)
        except ConfigError as e:
            _print_problems(e.problems, sys.stdout)
            return 1
        presets = len(cfg.presets)
        own = f", {presets} preset{'s' if presets != 1 else ''}" if presets else ""
        print(f"config OK ({len(cfg.files)} files, {len(cfg.accounts)} accounts{own})")
        for note in cfg.notes:
            print(note)
        return 0

    if args.cmd == "simplefin":
        if args.sfcmd not in ("claim", "fetch", "list-accounts"):
            sf.print_help()
            return 2
        from . import simplefin as sf_mod
        from .config import FeedConfig
        from .validate import ConfigError
        try:
            cfg = FeedConfig()
        except ConfigError as e:
            _print_problems(e.problems, sys.stderr)
            return 2
        if args.sfcmd == "claim":
            sf_mod.claim(cfg, args.token, force=args.force)
        elif args.sfcmd == "fetch":
            sf_mod.fetch(cfg, days=args.days)
        else:
            sf_mod.list_accounts(cfg)
        return 0

    if args.cmd == "rules":
        from . import rules_cli
        from .config import Config
        from .validate import ConfigError
        if args.rucmd not in ("preview", "overlaps", "starter-diff"):
            ru.print_help()
            return 2
        if args.rucmd == "starter-diff":
            # the files are read as they are: a config the validator would refuse still diffs
            from . import starter_diff
            from .config import Paths
            from .validate import missing_config_dir_problem
            paths = Paths()
            if not paths.config_dir.is_dir():
                print(missing_config_dir_problem(paths.config_dir, paths.config_from), file=sys.stderr)
                return 2
            try:
                starter_diff.report(starter_diff.diff(paths.config_dir, paths.starter_dir(args.starter)))
            except OSError as e:
                print(f"rules starter-diff: {e}", file=sys.stderr)
                return 2
            return 0
        try:
            cfg = Config(source_dir=args.source_dir)
        except ConfigError as e:
            _print_problems(e.problems, sys.stderr)
            return 2
        try:
            if args.rucmd == "preview":
                return rules_cli.preview(cfg, Path(args.file), show_rows=args.show_rows)
            return rules_cli.overlaps(cfg)
        except ConfigError as e:
            # a candidate rule the config would refuse: the validator's own lines
            _print_problems(e.problems, sys.stderr)
            return 2
        except (OSError, ValueError) as e:
            # a missing candidates file, bad YAML, a malformed or off-taxonomy candidate rule: usage, exit 2 — never
            # a traceback
            print(f"rules {args.rucmd}: {e}", file=sys.stderr)
            return 2

    if args.cmd == "ai":
        if args.aicmd not in ("preview", "run", "reset"):
            ai.print_help()
            return 2
        from .ai import cli as ai_cli
        from .config import Config, Paths
        from .validate import ConfigError
        if args.aicmd == "reset":
            return ai_cli.reset(Paths().ai_store_dir())
        try:
            cfg = Config(source_dir=args.source_dir)
        except ConfigError as e:
            _print_problems(e.problems, sys.stderr)
            return 2
        if args.aicmd == "run":
            return ai_cli.run(cfg, refresh=args.refresh, from_refresh=args.from_refresh)
        return ai_cli.preview(cfg)

    if args.cmd == "import":
        if args.imcmd not in ("detect", "preview", "map", "banks"):
            im.print_help()
            return 2
        from . import import_cli
        from .validate import ConfigError
        try:
            if args.imcmd == "banks":
                return import_cli.banks_command(args.write)
            if args.imcmd == "detect":
                return import_cli.detect_command(args.file)
            if args.imcmd == "preview":
                return import_cli.preview_command(args.file, args.format, args.account)
            return import_cli.map_command(args.file, args.id, answers_file=args.answers, force=args.force)
        except ConfigError as e:
            # the config the command read has problems: the validator's own lines
            _print_problems(e.problems, sys.stderr)
            return 2
        except OSError as e:
            print(f"import {args.imcmd}: {import_cli.plain_error(e)}", file=sys.stderr)
            return 2
        except ValueError as e:
            # an unknown format or account, a file that is not there, a taken name, an answer missing or unusable:
            # usage, exit 2 — never a traceback
            print(f"import {args.imcmd}: {e}", file=sys.stderr)
            return 2
        except import_cli.Stopped as e:
            # Ctrl-C at a `map` prompt: nothing was written, and no traceback
            print(f"import {args.imcmd}: {e}", file=sys.stderr)
            return 130

    if args.cmd == "passcode":
        if args.pccmd not in ("add", "remove", "list"):
            pc.print_help()
            return 2
        from . import passcode_cli
        return passcode_cli.run(args.pccmd, getattr(args, "label", None), from_stdin=getattr(args, "stdin", False))

    ap.print_help()
    return 2


if __name__ == "__main__":
    # what the commands create is readable by its owner's group and others unless it is a secret, whatever umask the
    # caller (or a container) runs with: each secrets path sets its own tighter mode
    os.umask(0o022)
    sys.exit(main())

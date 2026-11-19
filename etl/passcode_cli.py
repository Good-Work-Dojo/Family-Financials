# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""`python -m etl passcode ...` — the sign-in passcodes, in the web secrets folder's `passcodes` file (Paths
secrets_web_dir: FF_SECRETS_WEB_DIR, else FF_HOME's secrets/web/).

  passcode add LABEL [--stdin]   ask for the passcode twice without echo (or read one line from standard input, its
                                 line ending dropped; never from a terminal, where it would show), check it (at least
                                 8 characters, or 10 when all are digits) and store its scrypt hash under LABEL; a
                                 taken label is replaced in place, which signs out the browsers its old passcode
                                 signed in
  passcode remove LABEL          drop LABEL's passcode, which signs out the browsers it signed in
  passcode list                  the labels, one per line

The file's format, the hash and the passcode rules are deploy/auth.py's, the module the store checks a sign-in with,
loaded here by path (the store runs with the system python and cannot import etl, so the one implementation lives
beside it). Each command runs under umask 077: the folders it creates are 0700 and the file is written 0600,
atomically, and a secrets folder open to others is tightened to 0700 first; every other line of the file is kept byte
for byte. Nothing prints a passcode or a hash. Exit 0 when done, 2 on a usage error or a refused passcode (nothing is
written), 130 when stopped at a prompt.
"""
from __future__ import annotations

import getpass
import importlib.util
import os
import stat
import sys
from pathlib import Path

from .config import Paths

AUTH_PATH = Path(__file__).resolve().parent.parent / "deploy" / "auth.py"


def _load_auth():
    """deploy/auth.py as a module of its own name (registered first: its dataclasses look their module up)."""
    name = "ff_deploy_auth"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, AUTH_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


auth = _load_auth()
# the passcode hash, a seam the tests replace with a cheap one
HASHER = auth.scrypt


class Stopped(Exception):
    """Ctrl-C or the end of input at a prompt: nothing was written."""


class Refused(ValueError):
    """A usage error or a passcode the rules refuse: the one line says what and what to do."""


def _err(message: str) -> None:
    print(f"passcode: {message}", file=sys.stderr)


def _file(paths: Paths | None = None) -> Path:
    return (paths or Paths()).secrets_web_dir() / auth.PASSCODES_FILE


def _read(path: Path) -> list[bytes]:
    """The file's lines, each with its own ending; none when there is no file."""
    try:
        return path.read_bytes().splitlines(keepends=True)
    except FileNotFoundError:
        return []


def _label_of(raw: bytes) -> str | None:
    """The label of a well-formed line, else None."""
    try:
        line = auth.parse_line(raw.decode("ascii"))
    except UnicodeDecodeError:
        return None
    return line.label if line is not None else None


def _report_problems(raw_lines: list[bytes]) -> None:
    _, problems = auth.parse_file(b"".join(raw_lines))
    for problem in problems:
        _err(problem)


def _tighten(folder: Path) -> None:
    """A secrets folder that exists and is open to anyone but its owner becomes 0700, said in one line."""
    try:
        mode = stat.S_IMODE(folder.stat().st_mode)
    except FileNotFoundError:
        return
    if mode & 0o077:
        folder.chmod(0o700)
        _err(f"the secrets folder {folder} was open to others ({mode:04o}): it is now 0700")


def _write(path: Path, raw_lines: list[bytes]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)     # the command's umask makes the parents 0700 too
    auth.write_private(path, b"".join(raw_lines))


SIGNED_OUT = "every browser signed in with that passcode is signed out"


def _typed(label: str, from_stdin: bool) -> str:
    """The passcode for `label`: one line of standard input without its line ending, or asked twice on the terminal."""
    if from_stdin:
        if sys.stdin.isatty():
            raise Refused("--stdin on a terminal would show the passcode as it is typed: run the command without "
                          "--stdin to be asked for it without echo")
        line = sys.stdin.readline()
        return line[:-2] if line.endswith("\r\n") else line[:-1] if line.endswith("\n") else line
    if not sys.stdin.isatty():
        raise Refused("there is no terminal to ask on: pass --stdin to read the passcode from standard input")
    try:
        first = getpass.getpass(f"New passcode for {label}: ")
        again = getpass.getpass("The same passcode again: ")
    except (KeyboardInterrupt, EOFError):
        raise Stopped("stopped: nothing was changed") from None
    if first != again:
        raise Refused("the two entries differ: nothing was changed, try again")
    return first


def add(label: str, *, from_stdin: bool = False, paths: Paths | None = None, given: str | None = None) -> int:
    """Add or replace `label`'s passcode: `given` (a caller that already holds it, such as `init` reading its answers
    file or `demo` making one), else read as `from_stdin` says."""
    problem = auth.label_problem(label)
    if problem:
        raise Refused(problem)
    path = _file(paths)
    raw_lines = _read(path)
    _report_problems(raw_lines)
    taken = [i for i, raw in enumerate(raw_lines) if _label_of(raw) == label]
    usable = sum(_label_of(raw) is not None for raw in raw_lines)
    if not taken and usable >= auth.MAX_PASSCODES:
        raise Refused(f"there are already {auth.MAX_PASSCODES} passcodes, the most sign-in reads: remove one first")
    passcode = given if given is not None else _typed(label, from_stdin)
    problem = auth.passcode_problem(passcode)
    if problem:
        raise Refused(f"{problem}: nothing was changed")
    line = (auth.hash_passcode(label, passcode, hasher=HASHER) + "\n").encode("ascii")
    if taken:
        print(f"replacing the passcode labelled {label}")
        ending = raw_lines[taken[0]][len(raw_lines[taken[0]].rstrip(b"\r\n")):] or b"\n"
        raw_lines[taken[0]] = line[:-1] + ending
        raw_lines = [raw for i, raw in enumerate(raw_lines) if i not in taken[1:]]
    else:
        if raw_lines and not raw_lines[-1].endswith(b"\n"):
            raw_lines[-1] += b"\n"
        raw_lines.append(line)
    _write(path, raw_lines)
    print(f"passcode {label} {'replaced' if taken else 'added'}: it signs in from the next attempt")
    if taken:
        print(f"the old passcode no longer works: {SIGNED_OUT}")
    return 0


def remove(label: str, *, paths: Paths | None = None) -> int:
    path = _file(paths)
    raw_lines = _read(path)
    _report_problems(raw_lines)
    kept = [raw for raw in raw_lines if _label_of(raw) != label]
    if len(kept) == len(raw_lines):
        raise Refused(f"no passcode is labelled {label}: `passcode list` shows the labels")
    _write(path, kept)
    print(f"passcode {label} removed: {SIGNED_OUT}")
    if not any(_label_of(raw) for raw in kept):
        print("no passcode is left: nobody can sign in until one is added")
    return 0


def list_labels(*, paths: Paths | None = None) -> int:
    raw_lines = _read(_file(paths))
    _report_problems(raw_lines)
    labels = [label for raw in raw_lines if (label := _label_of(raw))]
    for label in labels[:auth.MAX_PASSCODES]:
        print(label)
    if not labels:
        _err("no passcode is set up: nobody can sign in until one is added")
    return 0


def run(command: str, label: str | None, *, from_stdin: bool = False) -> int:
    """One `passcode` command with its exit status; a refusal or a file error is one line on stderr. It runs under
    umask 077 (restored after), so whatever it creates is its owner's alone, and a secrets folder open to others is
    first tightened to 0700."""
    old_umask = os.umask(0o077)
    try:
        _tighten(_file().parent)
        if command == "add":
            return add(label, from_stdin=from_stdin)
        if command == "remove":
            return remove(label)
        return list_labels()
    except Refused as e:
        _err(str(e))
        return 2
    except Stopped as e:
        _err(str(e))
        return 130
    except OSError as e:
        what = e.strerror or type(e).__name__
        _err(f"{e.filename}: {what}" if e.filename else what)
        return 2
    finally:
        os.umask(old_umask)

#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The runtime dependencies' licences: every package the shipped program runs with must be under a licence on the
allow-list, and THIRD_PARTY_NOTICES.md's generated block must list exactly those packages.

The two runtime closures:
  npm     the dashboard's: every package in app/package-lock.json that is not marked dev (or devOptional), with the
          licence the lockfile records for it.
  python  the ETL image's: every distribution installed in the runtime ETL image, read inside it with no network
          (`docker run --network none IMAGE python licences.py --collect`, this file mounted read-only): its
          License-Expression, else its License field when that is an SPDX expression, else its licence classifiers
          when each names one licence and version (MIT, MPL 2.0, ISC, PSF, Unlicense, CC0). A distribution none of
          those names has an unknown licence.
The allow-list (ALLOWED) is MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, 0BSD, PSF-2.0, MPL-2.0, Unlicense and
CC0-1.0, and any AND / OR compound of them: every licence an expression names must be on it (an OR naming a GPL fails
too, so the package is read before it is taken). Anything else, GPL, AGPL, LGPL and SSPL included, or a licence the
check cannot read, fails with the package named. The base images' operating-system layers (Debian's packages in the
Python image, Caddy's Alpine) are out of scope: they are separate programs the images carry, not code the program
links; Caddy and supercronic themselves are credited by hand in THIRD_PARTY_NOTICES.md, and their Go code's licence
texts ship in THIRD_PARTY_LICENSES (below).

The dashboard's bundle carries the npm packages' code without their licence files, so THIRD_PARTY_LICENSES (which
both images carry beside LICENSE and NOTICE) holds each runtime npm package's licence texts: every file at the top of
its folder named LICENSE, LICENCE, COPYING or NOTICE (any case, any suffix) and every such file in its `licenses/`
folder, read from the packages as installed in the web image's build stage, with no network (`docker run --network
none WEB_IMAGE node -e …`). A package with no such file fails, and so does a THIRD_PARTY_LICENSES that is not those
texts. The Python distributions keep their licence files in their own metadata in the ETL image.

The two programs written in Go that the images carry, Caddy (the web image's /usr/bin/caddy) and supercronic (the
ETL image's /usr/local/bin/supercronic), are built from Go modules and the Go standard library, whose licence texts
THIRD_PARTY_LICENSES holds too, after the npm packages'. Each binary is copied out of its runtime image with no network
and its embedded build info read here (no Go toolchain needed): the Go version, its main module (unless it is a
`(devel)` build wrapper, as Caddy's image's is: Caddy itself is one of its dependencies) and every dependency, a
replacement standing for what it replaces. Each module's licence files (LICENSE, LICENCE, COPYING, NOTICE and PATENTS,
any case, any suffix, at the top of the module) at the exact version are kept in the tree under deploy/licences/go/
<module>@<version>/, so the check needs no network: a module with no text there fails, naming it and its program, and
so does a folder there for a module no program is built from. `--write` maintains that folder, the only step that
reaches the network: it reads the missing modules' licence files from the Go module proxy (proxy.golang.org, only the
byte ranges of each module's zip that hold them) and the standard library's from the Go repository at the version's
tag, and removes the folders no program needs. In THIRD_PARTY_LICENSES a text several modules ship alike (the Apache
licence, most often) is printed once, under every module that ships it, so the file stays small.

Usage: tools/verify/licences.py [--image IMAGE] [--web-image IMAGE] [--web-rt-image IMAGE] [--label KEY=VALUE]
                                [--write]
  --image         the runtime ETL image (an ID or a tag; default the harness's <prefix>-etl:rt,
                  tools/verify/harness_names.py)
  --web-image     the web image's build stage, where the dashboard's packages are installed (default the harness's
                  <prefix>-web:<test tag>)
  --web-rt-image  the runtime web image, which carries Caddy (default the harness's <prefix>-web:rt)
  --label         the label their containers carry (default the harness's)
  --write         rewrite THIRD_PARTY_NOTICES.md's generated block, deploy/licences/go and THIRD_PARTY_LICENSES from
                  what was read, instead of checking them
Prints one `licences:` line per finding and a summary; exit 1 when a licence is refused or the block or the texts are
not current, 2 when an image or a binary's build info cannot be read. `--collect` (inside the ETL image) prints the
installed distributions as JSON."""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCKFILE = Path("app") / "package-lock.json"
NOTICES = Path("THIRD_PARTY_NOTICES.md")
TEXTS = Path("THIRD_PARTY_LICENSES")
# where the web image's build stage installs the dashboard's packages (deploy/Dockerfile.web)
APP_IN_WEB_BUILD = "/app/finance-app/app"
# run by node in that stage, in that folder, with each package's lockfile path as an argument: prints
# {path: [[file, text], …]}, the package's licence files in name order
NPM_READ = r"""
const fs = require("fs"), path = require("path");
const NAME = /^(licen[cs]e|copying|notice)([.-].*)?$/i;
const out = {};
for (const pkg of process.argv.slice(1)) {
  const files = [];
  const read = (dir, prefix) => {
    if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) return;
    for (const f of fs.readdirSync(dir).sort()) {
      const full = path.join(dir, f);
      if (NAME.test(f) && fs.lstatSync(full).isFile()) files.push([prefix + f, fs.readFileSync(full, "utf8")]);
    }
  };
  read(pkg, "");
  read(path.join(pkg, "licenses"), "licenses/");
  out[pkg] = files;
}
process.stdout.write(JSON.stringify(out));
"""
TEXTS_HEAD = """\
The licence texts of the third-party packages the dashboard is built from, as each package ships them (line endings
and trailing spaces normalised). Generated by tools/verify/licences.py --write from the dashboard's runtime packages
(app/package-lock.json); do not edit by hand. THIRD_PARTY_NOTICES.md lists every runtime dependency with its
licence; the Python distributions in the ETL image keep their licence files in their own *.dist-info directories
(/opt/venv/lib/python3.12/site-packages/). After them, the texts of the Go modules and the Go standard library that
Caddy and supercronic are built from.
"""
GO_HEAD = """\
The programs the images carry that are written in Go: Caddy (the web image's /usr/bin/caddy) and supercronic (the
ETL image's /usr/local/bin/supercronic). Below, the licence texts of the Go standard library and of every Go module
compiled into them, at the version each binary's build info names, as each module ships them (kept in
deploy/licences/go by tools/verify/licences.py --write). A text several modules ship alike is printed once, under
every module that ships it.
"""
# where the vendored Go licence texts live, one folder per module: <module path>@<version>/<file>
GO_DIR = Path("deploy") / "licences" / "go"
# the programs written in Go: (name, the runtime image that carries it, its path there)
BINARIES = (("caddy", "web", "/usr/bin/caddy"), ("supercronic", "etl", "/usr/local/bin/supercronic"))
GO_FILE = re.compile(r"(licen[cs]e|copying|notice|patents)([.-].*)?", re.I)
# the Go standard library, compiled into every binary: its module path here, its texts the Go repository's
GO_STD = "std"
GO_STD_FILES = ("LICENSE", "PATENTS")
GO_PROXY = "https://proxy.golang.org"
GO_SOURCE = "https://raw.githubusercontent.com/golang/go"
BUILDINFO_MAGIC = b"\xff Go buildinf:"
RULE = "=" * 80
BEGIN = "<!-- BEGIN GENERATED by tools/verify/licences.py --write: do not edit by hand -->"
END = "<!-- END GENERATED -->"

ALLOWED = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD", "PSF-2.0", "MPL-2.0",
                     "Unlicense", "CC0-1.0"})
# classifiers that name one licence and its version; one without a version (Apache Software License, BSD License)
# names none
CLASSIFIERS = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: OSI Approved :: The Unlicense (Unlicense)": "Unlicense",
    "License :: CC0 1.0 Universal (CC0 1.0) Public Domain Dedication": "CC0-1.0",
}
COPYLEFT = re.compile(r"^(A|L)?GPL|^SSPL", re.I)
_TOKEN = re.compile(r"\s*(\(|\)|[A-Za-z0-9.+-]+)")


@dataclass(frozen=True)
class Package:
    ecosystem: str          # npm or python
    name: str
    version: str
    licence: str | None     # an SPDX expression, None when none could be read
    path: str = field(default="", compare=False)    # an npm package's folder, as the lockfile names it


def parse_expression(expr: str) -> list[str] | None:
    """the licence ids an SPDX expression names (AND, OR and parentheses; a WITH exception is not taken), None when
    it is no expression of that form"""
    tokens, pos = [], 0
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m:
            return None
        tokens.append(m.group(1))
        pos = m.end()
        if not expr[pos:].strip():
            break
    ids, depth, want_id = [], 0, True
    for t in tokens:
        if t == "(" and want_id:
            depth += 1
        elif t == ")" and not want_id and depth:
            depth -= 1
        elif t in ("AND", "OR") and not want_id:
            want_id = True
        elif want_id and t not in ("(", ")", "AND", "OR", "WITH") and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]*", t):
            ids.append(t)
            want_id = False
        else:
            return None
    return ids if ids and depth == 0 and not want_id else None


def verdict(p: Package) -> str | None:
    """None when the package's licence is allowed, else what is wrong, in words"""
    if not p.licence:
        return "no licence the check can read: read the package and state it, or drop the dependency"
    ids = parse_expression(p.licence)
    if ids is None:
        return f"'{p.licence}' is no SPDX expression the check can read"
    copyleft = [i for i in ids if COPYLEFT.search(i)]
    if copyleft:
        return f"{p.licence} names {', '.join(copyleft)}, a copyleft licence the project does not take at run time"
    other = [i for i in ids if i not in ALLOWED]
    if other:
        return f"{p.licence} names {', '.join(other)}, which is not on the allow-list"
    return None


def npm_closure(lock: dict) -> list[Package]:
    """the lockfile's packages that are not dev-only, as installed (its `packages`, lockfile version 2 or 3)"""
    if lock.get("lockfileVersion") not in (2, 3) or not isinstance(lock.get("packages"), dict):
        raise ValueError("app/package-lock.json is no lockfile of version 2 or 3")
    out = []
    for path, entry in lock["packages"].items():
        if not path or entry.get("dev") or entry.get("devOptional") or entry.get("link"):
            continue
        name = entry.get("name") or path.rsplit("node_modules/", 1)[-1]
        licence = entry.get("license")
        out.append(Package("npm", name, str(entry.get("version", "")), licence if isinstance(licence, str) and licence
                           else None, path))
    return sorted(out, key=lambda p: (p.name.lower(), p.version))


def python_licence(dist: dict) -> str | None:
    """a distribution's licence as an SPDX expression, from what --collect read"""
    if dist.get("license_expression"):
        return dist["license_expression"].strip()
    field = (dist.get("license") or "").strip()
    if field and "\n" not in field and parse_expression(field) is not None \
            and all(i in ALLOWED or COPYLEFT.search(i) for i in parse_expression(field)):
        return field
    named = sorted({CLASSIFIERS[c] for c in dist.get("classifiers", []) if c in CLASSIFIERS})
    unnamed = [c for c in dist.get("classifiers", []) if c.startswith("License ::") and c not in CLASSIFIERS]
    if len(named) == 1 and not unnamed:
        return named[0]
    return None


def python_closure(dists: list[dict]) -> list[Package]:
    return sorted((Package("python", d["name"], d["version"], python_licence(d)) for d in dists),
                  key=lambda p: (p.name.lower(), p.version))


def collect() -> list[dict]:
    """the distributions installed in this interpreter's environment (run inside the runtime image)"""
    import importlib.metadata as md
    out = []
    for d in md.distributions():
        meta = d.metadata
        out.append({"name": meta["Name"], "version": meta["Version"],
                    "license_expression": meta.get("License-Expression"), "license": meta.get("License"),
                    "classifiers": [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]})
    return sorted(out, key=lambda d: d["name"].lower())


def read_image(image: str, label: str) -> list[dict]:
    """--collect run inside `image` with no network, as an unprivileged uid"""
    here = Path(__file__).resolve()
    r = subprocess.run(["docker", "run", "--rm", "--label", label, "--network", "none", "--user", "12345:12345",
                        "-v", f"{here}:/tmp/tools/verify/licences.py:ro", image, "python",
                        "/tmp/tools/verify/licences.py", "--collect"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"the runtime ETL image {image} could not list its distributions: {r.stderr.strip()}")
    return json.loads(r.stdout)


def read_npm_texts(image: str, label: str, packages: list[Package]) -> dict[str, list[list[str]]]:
    """NPM_READ run in the web image's build stage with no network, as an unprivileged uid: each package's licence
    files, by its lockfile path"""
    paths = [p.path for p in packages]
    for path in paths:
        if not re.fullmatch(r"node_modules/[A-Za-z0-9@._/-]+", path) or ".." in path.split("/"):
            raise RuntimeError(f"the lockfile names a package folder of another form: {path!r}")
    r = subprocess.run(["docker", "run", "--rm", "--label", label, "--network", "none", "--user", "12345:12345",
                        "-w", APP_IN_WEB_BUILD, image, "node", "-e", NPM_READ, *paths],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"the web build image {image} could not read the packages' licence files: "
                           f"{r.stderr.strip()}")
    return json.loads(r.stdout)


@dataclass(frozen=True)
class GoModule:
    path: str                   # the module path, or GO_STD for the standard library
    version: str                # a module version, or the Go version (go1.27.3) for the standard library
    programs: tuple[str, ...]   # the binaries built from it, in name order

    @property
    def key(self) -> str:
        """its folder under GO_DIR"""
        return f"{self.path}@{self.version}"

    def title(self) -> str:
        what = f"the Go standard library {self.version}" if self.path == GO_STD else f"{self.path} {self.version}"
        return f"{what} (in {', '.join(self.programs)})"


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if b < 0x80:
            return value, pos
        shift += 7
    raise ValueError("the build info ends inside a length")


def go_build_info(data: bytes) -> tuple[str, list[tuple[str, str]]]:
    """(the Go version, [(module path, version)]) a Go binary's embedded build info names: its main module unless it is
    a `(devel)` build wrapper, then every dependency, a replacement (`=>`) standing for the module it replaces. Reads
    the format Go 1.18 and later writes (the strings inline after the 32-byte header, the module text between two
    16-byte sentinels)."""
    at = 0
    while (i := data.find(BUILDINFO_MAGIC, at)) >= 0:
        at = i + 1
        if len(data) < i + 32 or not data[i + 15] & 2:
            continue
        try:
            n, pos = _varint(data, i + 32)
            version = data[pos:pos + n].decode("ascii")
            n, pos = _varint(data, pos + n)
            mod = data[pos:pos + n]
            if len(mod) >= 33 and mod[-17:-16] == b"\n":
                mod = mod[16:-16]
            text = mod.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if not re.fullmatch(r"go1\.\d+(\.\d+)?", version):
            continue
        modules: list[tuple[str, str]] = []
        for line in text.splitlines():
            parts = line.split("\t")
            if parts[0] == "mod" and len(parts) >= 3 and parts[2] != "(devel)":
                modules.append((parts[1], parts[2]))
            elif parts[0] == "dep" and len(parts) >= 3:
                modules.append((parts[1], parts[2]))
            elif parts[0] == "=>" and len(parts) >= 3 and modules:
                modules[-1] = (parts[1], parts[2])
        return version, modules
    raise ValueError("no Go build info (a binary built by Go 1.18 or later) was found")


def go_modules(binaries: dict[str, bytes]) -> list[GoModule]:
    """every module, and the standard library, the binaries are built from, each once with the programs that hold
    it, in path and version order"""
    found: dict[tuple[str, str], set[str]] = {}
    for program, data in binaries.items():
        try:
            go, modules = go_build_info(data)
        except ValueError as e:
            raise ValueError(f"{program}: {e}") from None
        for key in [(GO_STD, go), *modules]:
            found.setdefault(key, set()).add(program)
    return [GoModule(path, version, tuple(sorted(programs)))
            for (path, version), programs in sorted(found.items())]


def vendored(root: Path) -> dict[str, dict[str, str]]:
    """the Go licence texts kept in the tree: {<module>@<version>: {file name: text}}; a folder with no file maps to
    an empty dict"""
    base = root / GO_DIR
    out: dict[str, dict[str, str]] = {}
    if not base.is_dir():
        return out
    for d in sorted(p for p in base.rglob("*@*") if p.is_dir()):
        out[d.relative_to(base).as_posix()] = {f.name: f.read_text("utf-8") for f in sorted(d.iterdir())
                                               if f.is_file()}
    return out


def _escape(s: str) -> str:
    """the Go module proxy's case encoding: each capital letter as ! and its lower case"""
    return re.sub(r"[A-Z]", lambda m: "!" + m.group(0).lower(), s)


class _Ranges(io.RawIOBase):
    """a remote file read by HTTP byte ranges, so zipfile reads a module zip's directory and its licence files only"""

    def __init__(self, url: str) -> None:
        self.url, self.pos = url, 0
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60) as r:
            self.size = int(r.headers["Content-Length"])

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = {0: 0, 1: self.pos, 2: self.size}[whence] + offset
        return self.pos

    def read(self, n: int = -1) -> bytes:
        end = self.size if n is None or n < 0 else min(self.size, self.pos + n)
        if end <= self.pos:
            return b""
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={self.pos}-{end - 1}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
            if r.status != 206:
                data = data[self.pos:end]
        self.pos += len(data)
        return data

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


def fetch_go_texts(module: GoModule) -> dict[str, str]:
    """a module's licence files at its version, from the network (only `--write` calls this)"""
    if module.path == GO_STD:
        out = {}
        for name in GO_STD_FILES:
            with urllib.request.urlopen(f"{GO_SOURCE}/{module.version}/{name}", timeout=60) as r:
                out[name] = r.read().decode("utf-8")
        return out
    url = f"{GO_PROXY}/{_escape(module.path)}/@v/{_escape(module.version)}.zip"
    prefix = f"{module.path}@{module.version}/"
    with zipfile.ZipFile(_Ranges(url)) as z:
        return {name[len(prefix):]: z.read(name).decode("utf-8", errors="replace") for name in sorted(z.namelist())
                if name.startswith(prefix) and "/" not in name[len(prefix):]
                and GO_FILE.fullmatch(name[len(prefix):])}


def read_binary(image: str, path: str, label: str) -> bytes:
    """a file of `image`, copied out with no network, as an unprivileged uid"""
    r = subprocess.run(["docker", "run", "--rm", "--label", label, "--network", "none", "--user", "12345:12345",
                        "--entrypoint", "cat", image, path], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"the image {image} could not give {path}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def _normalised(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return "\n".join(lines)


def texts_file(npm: list[Package], texts: dict[str, list[list[str]]], go: list[GoModule] = (),
               go_texts: dict[str, dict[str, str]] | None = None) -> str:
    """THIRD_PARTY_LICENSES as the packages' licence files give it, then the Go modules' (each distinct text once,
    headed by every module and file that ships it, in the order of the first of them)"""
    parts = [TEXTS_HEAD]
    for p in npm:
        for name, text in sorted(texts.get(p.path, []), key=lambda f: f[0]):
            parts += [RULE, f"{p.name} {p.version} ({p.licence or 'unknown'}): {name}", RULE, "", _normalised(text), ""]
    if go:
        shared: dict[str, list[str]] = {}
        for m in sorted(go, key=lambda m: (m.path != GO_STD, m.path, m.version)):
            for name, text in sorted((go_texts or {}).get(m.key, {}).items()):
                shared.setdefault(_normalised(text), []).append(f"{m.title()}: {name}")
        parts += [RULE, GO_HEAD]
        for text, heads in shared.items():
            parts += [RULE, *heads, RULE, "", text, ""]
    return "\n".join(parts)


def block(npm: list[Package], python: list[Package]) -> str:
    """THIRD_PARTY_NOTICES.md's generated block, its markers included"""
    lines = [BEGIN, ""]
    for title, pkgs in (("The dashboard (npm, from app/package-lock.json)", npm),
                        ("The ETL image (Python, as installed in the runtime image)", python)):
        lines += [f"### {title}", "", "| Package | Version | Licence |", "|---|---|---|"]
        lines += [f"| {p.name} | {p.version} | {p.licence or 'unknown'} |" for p in pkgs]
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


def current_block(text: str) -> str | None:
    start, end = text.find(BEGIN), text.find(END)
    if start < 0 or end < start or text.count(BEGIN) != 1 or text.count(END) != 1:
        return None
    return text[start:end + len(END)]


def check(root: Path, dists: list[dict], texts: dict[str, list[list[str]]], write: bool = False,
          binaries: dict[str, bytes] | None = None, fetch=fetch_go_texts) -> tuple[int, list[str]]:
    """(exit status, lines to print) for the tree at `root`, the ETL image's distributions, the npm packages'
    licence files and the Go binaries the images carry ({program: its bytes}); `fetch(module)` gives a module's
    licence files when `write` adds a missing one"""
    npm = npm_closure(json.loads((root / LOCKFILE).read_text("utf-8")))
    python = python_closure(dists)
    go = go_modules(binaries or {})
    lines, refused = [], 0
    for p in [*npm, *python]:
        why = verdict(p)
        if why:
            refused += 1
            lines.append(f"licences: {p.ecosystem} {p.name} {p.version}: {why}")
    for p in npm:
        if not texts.get(p.path):
            refused += 1
            lines.append(f"licences: npm {p.name} {p.version}: its package holds no licence file, so its licence "
                         "text cannot ship with it: find the text and add it, or drop the dependency")
    go_texts, go_stale = vendored(root), False
    wanted = {m.key for m in go}
    for key in sorted(set(go_texts) - wanted):
        if write:
            shutil.rmtree(root / GO_DIR / key)
            del go_texts[key]
            lines.append(f"licences: {GO_DIR / key} removed (no program is built from it)")
        else:
            go_stale = True
            lines.append(f"licences: {GO_DIR / key} is the text of a module no program in the images is built from: "
                         "run tools/verify/licences.py --write and read the change")
    for m in go:
        if go_texts.get(m.key):
            continue
        found = {}
        if write:
            try:
                found = fetch(m)
            except (OSError, ValueError, zipfile.BadZipFile) as e:
                refused += 1
                lines.append(f"licences: go {m.title()}: its licence files could not be fetched "
                             f"({type(e).__name__}): run --write again")
                continue
            for name, text in found.items():
                (root / GO_DIR / m.key).mkdir(parents=True, exist_ok=True)
                (root / GO_DIR / m.key / name).write_text(text, "utf-8")
            if found:
                go_texts[m.key] = found
                lines.append(f"licences: {GO_DIR / m.key} written")
                continue
            why = "the module ships no licence file: find its text and add it there, or drop the dependency"
        else:
            why = "no licence text is kept for it: run tools/verify/licences.py --write and read the change"
        refused += 1
        lines.append(f"licences: go {m.title()}: {GO_DIR / m.key}: {why}")
    texts_path, want_texts = root / TEXTS, texts_file(npm, texts, go, go_texts)
    texts_stale = False
    if not texts_path.is_file() or texts_path.read_text("utf-8") != want_texts:
        if write:
            texts_path.write_text(want_texts, "utf-8")
            lines.append(f"licences: {TEXTS} rewritten")
        else:
            lines.append(f"licences: {TEXTS} is not the runtime packages' licence texts as they are: run "
                         "tools/verify/licences.py --write and read the change")
            texts_stale = True
    path = root / NOTICES
    want = block(npm, python)
    text = path.read_text("utf-8") if path.is_file() else ""
    have = current_block(text)
    stale = False
    if have is None:
        lines.append(f"licences: {NOTICES} has no generated block (one {BEGIN} line and one {END} line)")
        stale = True
    elif have != want:
        if write:
            path.write_text(text.replace(have, want), "utf-8")
            lines.append(f"licences: {NOTICES}'s generated block rewritten")
        else:
            lines.append(f"licences: {NOTICES}'s generated block is not the closures as they are: run "
                         "tools/verify/licences.py --write and read the change")
            stale = True
    programs = sorted({p for m in go for p in m.programs})
    lines.append(f"licences: npm {len(npm)} runtime packages, python {len(python)} distributions, go "
                 f"{len(go)} modules in {len(programs)} programs; {refused} refused; the notices' block "
                 f"{'is stale' if stale else 'is current'}; the licence texts "
                 f"{'are stale' if texts_stale or go_stale else 'are current'}")
    return (1 if refused or stale or texts_stale or go_stale else 0), lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check the runtime dependencies' licences.")
    ap.add_argument("--collect", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--image")
    ap.add_argument("--web-image")
    ap.add_argument("--web-rt-image")
    ap.add_argument("--label")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    if args.collect:
        print(json.dumps(collect()))
        return 0
    if not args.image or not args.web_image or not args.web_rt_image or not args.label:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import harness_names
        names = harness_names.load()
        args.image = args.image or names.etl_rt
        args.web_image = args.web_image or names.web_test
        args.web_rt_image = args.web_rt_image or names.web_rt
        args.label = args.label or names.label
    images = {"etl": args.image, "web": args.web_rt_image}
    try:
        dists = read_image(args.image, args.label)
        npm = npm_closure(json.loads((ROOT / LOCKFILE).read_text("utf-8")))
        texts = read_npm_texts(args.web_image, args.label, npm)
        binaries = {name: read_binary(images[image], path, args.label) for name, image, path in BINARIES}
        go_modules(binaries)
    except (RuntimeError, ValueError) as e:     # a JSONDecodeError is a ValueError
        print(f"licences: {e}", file=sys.stderr)
        return 2
    rc, lines = check(ROOT, dists, texts, write=args.write, binaries=binaries)
    print("\n".join(lines))
    return rc


if __name__ == "__main__":
    sys.exit(main())

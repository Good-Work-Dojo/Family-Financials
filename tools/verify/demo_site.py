#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The demo site: its staged public directory, and the checks its built output must pass.

The demo site is the dashboard built with VITE_DEMO=1 (app/src/lib/demo.ts) over the invented household `full`: a
static site with no server behind it, published by .github/workflows/pages.yml and built by tools/verify/demo_site.sh,
which calls the two commands here.

stage PUBLIC --exports DIR [--state DIR]
    Makes PUBLIC (absent, or an empty directory) the build's public directory: every top-level *.json of the exports
    directory, deploy/public/icons/*, and site.webmanifest made from deploy/public/site.webmanifest with every address
    relative to the page (start_url and scope "./", each icon beside it), so the site works under any sub-path. Under
    family/, where the demo build reads the documents it starts from (app/src/data/useSyncedDoc.ts storeUrl), the four
    family documents (deploy/scenario_store.py STORES): each one --state (the household's state/family) holds, and the
    store's empty document for any other. A link among the files to copy is refused.

check SITE --exports DIR [--state DIR]
    Holds the built site to what a static demo must be, one line per problem (exit 1 when any):
    - index.html carries the web server's content-security policy (deploy/Caddyfile's Content-Security-Policy, less
      frame-ancestors, which a <meta> cannot carry) in a <meta http-equiv> ahead of every script and stylesheet;
    - no href or src in the page, the scripts or the stylesheets is absolute (a path from the root, another origin, a
      scheme), but a link to the program's source (app/src/lib/product.ts DEFAULT_SOURCE_URL, or --source URL); every
      relative one in the page names a file the site holds;
    - no page, script or stylesheet names a server path (/api, /auth, /login) or version.json, and the site publishes
      no version.json;
    - the banner's words (app/src/lib/demo.ts DEMO_BANNER_TEXT) are in a script;
    - every export is there, byte for byte, the icons and the family documents too, theme.js, and a manifest whose
      addresses are relative.

Options: --root W, the repository the rules are read from (default: the one holding this file).
usage: tools/verify/demo_site.py stage PUBLIC --exports DIR [--state DIR] [--root W]
       tools/verify/demo_site.py check SITE --exports DIR [--state DIR] [--root W] [--source URL]
       exit 0 done (stage) or every check passed (check), 1 a problem, 2 a usage error
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

# a string or url() in a script, a stylesheet or the page that starts with a server path the demo has none of
SERVER_PATH = re.compile(r"""["'`(]\s*\.{0,2}/(?:api|auth|login)(?:[/"'`?#)]|$)""")
VERSION_FILE = re.compile(r"version\.json")
# an href or src given a literal value in a script or a stylesheet, and that value (JSX compiles to `href:"…"`)
ATTR_LITERAL = re.compile(r"""\b(?:href|src)\s*[:=]\s*["'`]([^"'`]*)["'`]""")
CSS_URL = re.compile(r"""url\(\s*["']?([^"')\s]+)""")
# an address that is not relative to the page: from the root, protocol-relative, or with a scheme (data: included)
ABSOLUTE = re.compile(r"^(?:/|[A-Za-z][A-Za-z0-9+.-]*:)")
TEXT_SUFFIXES = (".html", ".js", ".mjs", ".css", ".webmanifest")
# the family store's documents (deploy/scenario_store.py STORES: its file in the state directory) by the name the demo
# build reads each under, beside the page (app/src/data/useSyncedDoc.ts storeUrl), and the document the store serves
# while it holds none (its empty_* functions)
EPOCH = "1970-01-01T00:00:00Z"
FAMILY = {"scenarios.json": "family/scenarios.json", "answers.json": "family/answers.json",
          "prefs.json": "family/prefs.json", "manual_assets.json": "family/manual-assets.json"}
EMPTY = {"scenarios.json": {"version": 1, "updated_at": EPOCH, "scenarios": []},
         "answers.json": {"version": 1, "updated_at": EPOCH, "answers": []},
         "prefs.json": {"version": 1, "updated_at": EPOCH},
         "manual_assets.json": {"version": 1, "updated_at": EPOCH, "assets": []}}


def caddy_policy(caddyfile: str) -> str:
    """the Content-Security-Policy header's value in the Caddyfile (exactly one)"""
    found = re.findall(r'^\s*Content-Security-Policy\s+"([^"]+)"', caddyfile, re.M)
    if len(found) != 1:
        raise ValueError(f"deploy/Caddyfile sets Content-Security-Policy {len(found)} times, not once")
    return found[0]


def demo_policy(policy: str) -> str:
    """the policy less frame-ancestors (a policy in a <meta> cannot carry it), directives as the header spells them"""
    kept = [d.strip() for d in policy.split(";") if d.strip() and d.split()[0] != "frame-ancestors"]
    return "; ".join(kept)


def _ts_string(path: Path, name: str) -> str:
    """a `const NAME = "…"` string constant of a TypeScript file (a plain double-quoted literal)"""
    m = re.search(rf'\bconst {name}\b[^=]*=\s*"((?:[^"\\]|\\.)*)"', path.read_text("utf-8"))
    if not m:
        raise ValueError(f"{path.name} holds no string constant {name}")
    return json.loads(f'"{m.group(1)}"')


def relative_manifest(doc: dict) -> dict:
    """the web manifest with every address relative to itself (it sits beside the page)"""
    out = dict(doc, start_url="./", scope="./")
    out["icons"] = [dict(icon, src=icon["src"].lstrip("/")) for icon in doc.get("icons", [])]
    return out


def _plain_files(directory: Path, pattern: str) -> list[Path]:
    """the files `pattern` matches at the top of `directory`; a link among them is refused"""
    found = sorted(directory.glob(pattern))
    links = [p.name for p in found if p.is_symlink()]
    if links:
        raise ValueError(f"{directory} holds links, not files: {', '.join(links)}")
    return [p for p in found if p.is_file()]


def _family(state: Path | None) -> list[tuple[Path, str]]:
    """the family documents the household's state directory holds, each with the name the site serves it under"""
    if state is None:
        return []
    held = {p.name: p for p in _plain_files(state, "*.json")}
    return [(held[name], served) for name, served in FAMILY.items() if name in held]


def stage(public: Path, exports: Path, root: Path, state: Path | None = None) -> list[str]:
    """fills `public` (absent or empty) and returns the names it holds"""
    if public.exists() and (not public.is_dir() or any(public.iterdir())):
        raise ValueError(f"{public} is not an empty directory")
    files = _plain_files(exports, "*.json")
    if not any(p.name == "manifest.json" for p in files):
        raise ValueError(f"{exports} holds no manifest.json")
    icons = _plain_files(root / "deploy" / "public" / "icons", "*")
    manifest = json.loads((root / "deploy" / "public" / "site.webmanifest").read_text("utf-8"))
    family = _family(state)
    (public / "icons").mkdir(parents=True)
    for p in files:
        shutil.copyfile(p, public / p.name)
    for p in icons:
        shutil.copyfile(p, public / "icons" / p.name)
    (public / "family").mkdir()
    held = {p.name for p, _ in family}
    for p, served in family:
        shutil.copyfile(p, public / served)
    # a document the household's store does not hold is served as the store serves it then, so no read finds nothing
    for name, served in FAMILY.items():
        if name not in held:
            (public / served).write_text(json.dumps(EMPTY[name]) + "\n", "utf-8")
    (public / "site.webmanifest").write_text(json.dumps(relative_manifest(manifest), indent=2) + "\n", "utf-8")
    return sorted(str(p.relative_to(public)) for p in public.rglob("*") if p.is_file())


class _Page(HTMLParser):
    """every element's tag and attributes in document order"""

    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, {k: v or "" for k, v in attrs}))

    handle_startendtag = handle_starttag


def _absolute(ref: str, source: str) -> bool:
    """`ref` is not relative to the page, and is no address within the program's source (`source`)"""
    return bool(ABSOLUTE.match(ref)) and not (ref == source or ref.startswith(source.rstrip("/") + "/"))


def check(site: Path, exports: Path, root: Path, source: str | None = None, state: Path | None = None) -> list[str]:
    """the problems with the built demo site, one line each (none: it passes)"""
    problems: list[str] = []
    source = source or _ts_string(root / "app" / "src" / "lib" / "product.ts", "DEFAULT_SOURCE_URL")
    banner = _ts_string(root / "app" / "src" / "lib" / "demo.ts", "DEMO_BANNER_TEXT")
    policy = demo_policy(caddy_policy((root / "deploy" / "Caddyfile").read_text("utf-8")))
    index = site / "index.html"
    if not index.is_file():
        return [f"{site} holds no index.html"]

    page = _Page()
    page.feed(index.read_text("utf-8"))
    tags = [t for t, _ in page.elements]
    csp = [i for i, (t, a) in enumerate(page.elements)
           if t == "meta" and a.get("http-equiv", "").lower() == "content-security-policy"]
    if len(csp) != 1:
        problems.append(f"index.html carries {len(csp)} content-security-policy <meta> tags, not one")
    else:
        got = page.elements[csp[0]][1].get("content", "")
        if got != policy:
            problems.append(f"index.html's content-security policy is {got!r}, not the web server's less frame-ancestors"
                            f" ({policy!r})")
        first = next((i for i, t in enumerate(tags) if t in ("script", "link")), len(tags))
        if csp[0] > first:
            problems.append("index.html's content-security policy comes after a script or a stylesheet it must govern")
    for tag, attrs in page.elements:
        for name in ("href", "src"):
            ref = attrs.get(name)
            if ref is None:
                continue
            if _absolute(ref, source) or ref.startswith("//"):
                problems.append(f"index.html: <{tag} {name}={ref!r}> is not relative to the page")
            elif not (site / ref.split("#")[0].split("?")[0]).resolve().is_file():
                problems.append(f"index.html: <{tag} {name}={ref!r}> names no file the site holds")

    texts = sorted(p for p in site.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES)
    banner_seen = False
    for p in texts:
        rel = p.relative_to(site).as_posix()
        text = p.read_text("utf-8", errors="replace")
        for m in SERVER_PATH.finditer(text):
            problems.append(f"{rel} names a server path: {m.group(0).strip()!r}")
        if VERSION_FILE.search(text):
            problems.append(f"{rel} names version.json")
        if p.suffix in (".js", ".mjs", ".css"):
            refs = ATTR_LITERAL.findall(text) + (CSS_URL.findall(text) if p.suffix == ".css" else [])
            for ref in refs:
                if (_absolute(ref, source) or ref.startswith("//")) and not ref.startswith("data:"):
                    problems.append(f"{rel}: an href or src {ref!r} is not relative to the page")
            banner_seen = banner_seen or (p.suffix != ".css" and banner in text)
    if not banner_seen:
        problems.append(f"no script carries the demo banner's words ({banner!r})")
    if (site / "version.json").exists():
        problems.append("the site publishes version.json")

    for p in _plain_files(exports, "*.json"):
        built = site / p.name
        if not built.is_file():
            problems.append(f"the export {p.name} is not in the site")
        elif built.read_bytes() != p.read_bytes():
            problems.append(f"the site's {p.name} is not the export's")
    for p, served in _family(state):
        built = site / served
        if not built.is_file() or built.read_bytes() != p.read_bytes():
            problems.append(f"the family document {p.name} is not in the site as {served}")
    for served in FAMILY.values():
        if not (site / served).is_file():
            problems.append(f"the site holds no {served}, which the demo reads")
    for p in _plain_files(root / "deploy" / "public" / "icons", "*"):
        built = site / "icons" / p.name
        if not built.is_file() or built.read_bytes() != p.read_bytes():
            problems.append(f"the icon {p.name} is not in the site as deploy/public/icons holds it")
    if not (site / "theme.js").is_file():
        problems.append("the site holds no theme.js")
    try:
        manifest = json.loads((site / "site.webmanifest").read_text("utf-8"))
    except (OSError, ValueError):
        problems.append("the site holds no readable site.webmanifest")
    else:
        refs = [manifest.get("start_url", ""), manifest.get("scope", "")] + [i.get("src", "") for i in manifest.get("icons", [])]
        for ref in refs:
            if not isinstance(ref, str) or _absolute(ref, "\0") or ref.startswith("//"):
                problems.append(f"site.webmanifest: {ref!r} is not relative")
    problems += _unexpected(site, exports, root)
    return problems


def _exports_listed(exports: Path) -> set[str] | None:
    """the files the exports' manifest.json says the ingest wrote (its `files`), None when it says none"""
    try:
        files = json.loads((exports / "manifest.json").read_text("utf-8")).get("files")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(files, list) or not all(isinstance(f, str) and "/" not in f for f in files):
        return None
    return set(files)


def _unexpected(site: Path, exports: Path, root: Path) -> list[str]:
    """every file of the site outside what a demo site publishes: the page, theme.js, the manifest, the bundle's scripts
    and stylesheets under assets/, the icons, the four family documents and the exports the manifest lists"""
    listed = _exports_listed(exports)
    if listed is None:
        return ["the exports' manifest.json lists no files, so what the site may publish is unknown"]
    allowed = {"index.html", "theme.js", "site.webmanifest", *FAMILY.values(), *listed}
    allowed |= {f"icons/{p.name}" for p in _plain_files(root / "deploy" / "public" / "icons", "*")}
    out = []
    for p in sorted(site.rglob("*")):
        rel = p.relative_to(site).as_posix()
        if p.is_symlink():
            out.append(f"{rel} is a link, which a published site never holds")
        elif not p.is_file():
            continue
        elif rel.endswith(".map"):
            out.append(f"{rel} is a source map, which the site never publishes")
        elif rel not in allowed and not re.fullmatch(r"assets/[^/]+\.(?:js|css)", rel):
            out.append(f"{rel} is not a file a demo site publishes")
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="demo_site.py", description="Stage or check the demo site.")
    parser.add_argument("command", choices=("stage", "check"))
    parser.add_argument("dir", type=Path)
    parser.add_argument("--exports", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source")
    parser.add_argument("--state", type=Path)
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    if not args.exports.is_dir():
        print(f"no exports directory at {args.exports}", file=sys.stderr)
        return 2
    if args.state is not None and not args.state.is_dir():
        print(f"no state directory at {args.state}", file=sys.stderr)
        return 2
    try:
        if args.command == "stage":
            names = stage(args.dir, args.exports, args.root, args.state)
            print(f"demo site: staged {len(names)} files in {args.dir}")
            return 0
        if not args.dir.is_dir():
            print(f"no built site at {args.dir}", file=sys.stderr)
            return 2
        problems = check(args.dir, args.exports, args.root, args.source, args.state)
    except (OSError, ValueError) as e:
        print(f"demo site: {e}", file=sys.stderr)
        return 1
    for line in problems:
        print(f"demo site: {line}", file=sys.stderr)
    if problems:
        return 1
    files = sum(1 for p in args.dir.rglob("*") if p.is_file())
    print(f"demo site: {files} files; relative addresses only, no server path, the policy in the page, the banner and "
          "every export")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

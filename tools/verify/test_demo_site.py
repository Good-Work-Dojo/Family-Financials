# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""tools/verify/demo_site.py: the demo site's staged public directory, and the checks its built output must pass, on
a synthetic repository and a synthetic built site (each defect alone must fail the check), plus the real tree's own
rules (the page's policy constant against the Caddyfile's)."""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import demo_site  # noqa: E402  (tools/verify is not a package)

SOURCE = "https://github.com/example/ledger"
BANNER = "Demo: a sample household. Changes stay here."
HEADER = ("default-src 'none'; script-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
POLICY = "default-src 'none'; script-src 'self'; img-src 'self' data:; base-uri 'none'"
MANIFEST = {"name": "Sample", "start_url": "/", "scope": "/",
            "icons": [{"src": "/icons/icon.png", "sizes": "192x192"}, {"src": "/icons/icon.svg", "sizes": "any"}]}


@pytest.fixture
def root(tmp_path):
    """a repository holding what the rules are read from: the Caddyfile's policy, the icons and manifest, the source
    address and the banner's words"""
    r = tmp_path / "repo"
    (r / "deploy" / "public" / "icons").mkdir(parents=True)
    (r / "deploy" / "Caddyfile").write_text(f'(h) {{\n\theader {{\n\t\tContent-Security-Policy "{HEADER}"\n\t}}\n}}\n')
    (r / "deploy" / "public" / "icons" / "icon.png").write_bytes(b"\x89PNG sample")
    (r / "deploy" / "public" / "icons" / "icon.svg").write_text("<svg/>")
    (r / "deploy" / "public" / "site.webmanifest").write_text(json.dumps(MANIFEST))
    (r / "app" / "src" / "lib").mkdir(parents=True)
    (r / "app" / "src" / "lib" / "product.ts").write_text(f'export const DEFAULT_SOURCE_URL = "{SOURCE}";\n')
    (r / "app" / "src" / "lib" / "demo.ts").write_text(f'export const DEMO_BANNER_TEXT = "{BANNER}";\n')
    return r


@pytest.fixture
def exports(tmp_path):
    e = tmp_path / "exports"
    e.mkdir()
    (e / "manifest.json").write_text('{"generated_at": "2026-10-31T20:00:00-04:00", '
                                     '"files": ["manifest.json", "transactions.json"]}')
    (e / "transactions.json").write_text("[]")
    return e


def _page(meta: str = f'<meta http-equiv="Content-Security-Policy" content="{POLICY}">',
          body: str = '<script src="./theme.js"></script><script type="module" src="./assets/index.js"></script>'
                      '<link rel="manifest" href="./site.webmanifest"><link rel="icon" href="./icons/icon.svg">') -> str:
    return f"<!doctype html><html><head>{meta}<meta charset=\"UTF-8\">{body}</head><body></body></html>"


@pytest.fixture
def site(tmp_path, root, exports):
    """a built site that passes: staged as the script stages it, then the page, the bundle and the theme script"""
    s = tmp_path / "site"
    demo_site.stage(s, exports, root)
    (s / "assets").mkdir()
    (s / "index.html").write_text(_page())
    (s / "assets" / "index.js").write_text(
        f'const b="{BANNER}";fetch(`./${{n}}`);const a={{href:"{SOURCE}/blob/main/LICENSE"}};'
        'const x="http://www.w3.org/2000/svg";const k="secrets/ai/api_key";')
    (s / "assets" / "index.css").write_text(".a{background:url(./icons/icon.svg)}")
    (s / "theme.js").write_text("try{}catch{}\n")
    return s


def problems(site: Path, exports: Path, root: Path) -> list[str]:
    return demo_site.check(site, exports, root)


def test_staging_copies_the_exports_and_icons_and_makes_every_manifest_address_relative(tmp_path, root, exports):
    public = tmp_path / "public"
    names = demo_site.stage(public, exports, root)
    assert names == ["family/answers.json", "family/manual-assets.json", "family/prefs.json", "family/scenarios.json",
                     "icons/icon.png", "icons/icon.svg", "manifest.json", "site.webmanifest", "transactions.json"]
    assert json.loads((public / "family" / "answers.json").read_text()) \
        == {"version": 1, "updated_at": "1970-01-01T00:00:00Z", "answers": []}, "the store's empty document"
    assert (public / "manifest.json").read_bytes() == (exports / "manifest.json").read_bytes()
    doc = json.loads((public / "site.webmanifest").read_text())
    assert (doc["start_url"], doc["scope"]) == ("./", "./")
    assert [i["src"] for i in doc["icons"]] == ["icons/icon.png", "icons/icon.svg"]
    assert doc["name"] == "Sample" and doc["icons"][0]["sizes"] == "192x192"


def test_staging_puts_the_family_documents_the_state_holds_where_the_demo_build_reads_them(tmp_path, root, exports, site):
    state = tmp_path / "state"
    state.mkdir()
    (state / "manual_assets.json").write_text('{"version": 1, "assets": []}')
    (state / "answers.json").write_text('{"version": 1, "answers": []}')
    (state / "notes.txt").write_text("not a document")
    public = tmp_path / "public"
    names = demo_site.stage(public, exports, root, state)
    assert [n for n in names if n.startswith("family/")] \
        == ["family/answers.json", "family/manual-assets.json", "family/prefs.json", "family/scenarios.json"]
    assert (public / "family" / "manual-assets.json").read_bytes() == (state / "manual_assets.json").read_bytes()
    assert json.loads((public / "family" / "prefs.json").read_text())["updated_at"] == "1970-01-01T00:00:00Z"
    got = demo_site.check(site, exports, root, state=state)
    assert sum("family document" in line for line in got) == 2, "a site built without them has the empty ones"
    shutil.copytree(public / "family", site / "family", dirs_exist_ok=True)
    assert demo_site.check(site, exports, root, state=state) == []
    (site / "family" / "answers.json").write_text("{}")
    got = demo_site.check(site, exports, root, state=state)
    assert "the family document answers.json is not in the site as family/answers.json" in got
    assert not any("manual_assets.json" in line for line in got)


def test_staging_refuses_a_directory_with_files_a_link_among_the_exports_and_exports_without_a_manifest(
        tmp_path, root, exports):
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "x").write_text("")
    with pytest.raises(ValueError, match="not an empty directory"):
        demo_site.stage(busy, exports, root)
    (exports / "linked.json").symlink_to(exports / "manifest.json")
    with pytest.raises(ValueError, match="links"):
        demo_site.stage(tmp_path / "p1", exports, root)
    (exports / "linked.json").unlink()
    (exports / "manifest.json").unlink()
    with pytest.raises(ValueError, match="no manifest.json"):
        demo_site.stage(tmp_path / "p2", exports, root)


def test_a_sound_demo_site_passes(site, exports, root):
    assert problems(site, exports, root) == []


def _break(site: Path, name: str, old: str, new: str) -> None:
    p = site / name
    text = p.read_text()
    assert old in text, f"{old!r} is not in {name}"
    p.write_text(text.replace(old, new))


# each defect alone, and the words of the line it must cause
DEFECTS = {
    "an absolute href in the page": (lambda s: _break(s, "index.html", 'href="./icons/icon.svg"', 'href="/icons/icon.svg"'),
                                     "is not relative to the page"),
    "another origin in the page": (lambda s: _break(s, "index.html", 'src="./theme.js"', 'src="https://cdn.example.net/t.js"'),
                                   "is not relative to the page"),
    "a protocol-relative src": (lambda s: _break(s, "index.html", 'src="./theme.js"', 'src="//cdn.example.net/t.js"'),
                                "is not relative to the page"),
    "a relative href to nothing": (lambda s: (s / "theme.js").unlink(), "names no file the site holds"),
    "no policy": (lambda s: _break(s, "index.html", f'<meta http-equiv="Content-Security-Policy" content="{POLICY}">', ""),
                  "0 content-security-policy <meta> tags"),
    "the header's policy, frame-ancestors and all": (lambda s: _break(s, "index.html", f'content="{POLICY}"', f'content="{HEADER}"'),
                                                     "not the web server's less frame-ancestors"),
    "the policy after the scripts": (lambda s: (s / "index.html").write_text(_page(
        meta="", body='<script src="./theme.js"></script>'
                      f'<meta http-equiv="Content-Security-Policy" content="{POLICY}">')),
                                     "comes after a script or a stylesheet"),
    "a store path in a script": (lambda s: _break(s, "assets/index.js", "fetch(`./${n}`)", 'fetch("./api/prefs")'),
                                 "names a server path"),
    "a store path as a template": (lambda s: _break(s, "assets/index.js", "fetch(`./${n}`)", "fetch(`./api/${n}`)"),
                                   "names a server path"),
    "the sign-out path": (lambda s: _break(s, "assets/index.js", "fetch(`./${n}`)", 'fetch("/auth/logout")'),
                          "names a server path"),
    "the sign-in page": (lambda s: _break(s, "assets/index.js", "fetch(`./${n}`)", 'const l="/login"'),
                         "names a server path"),
    "the build poll": (lambda s: _break(s, "assets/index.js", "fetch(`./${n}`)", 'fetch("./version.json")'),
                       "names version.json"),
    "a published version.json": (lambda s: (s / "version.json").write_text("{}"), "publishes version.json"),
    "an absolute href in a script": (lambda s: _break(s, "assets/index.js", f'href:"{SOURCE}/blob/main/LICENSE"', 'href:"/login.css"'),
                                     "is not relative to the page"),
    "a look-alike of the source": (lambda s: _break(s, "assets/index.js", f'href:"{SOURCE}/blob', f'href:"{SOURCE}-copy/blob'),
                                   "is not relative to the page"),
    "an absolute url() in a stylesheet": (lambda s: _break(s, "assets/index.css", "url(./icons", "url(/icons"),
                                          "is not relative to the page"),
    "no banner": (lambda s: _break(s, "assets/index.js", BANNER, "Something else"), "demo banner's words"),
    "a missing export": (lambda s: (s / "transactions.json").unlink(), "transactions.json is not in the site"),
    "a changed export": (lambda s: (s / "manifest.json").write_text("{}"), "manifest.json is not the export's"),
    "a missing icon": (lambda s: (s / "icons" / "icon.png").unlink(), "icon.png is not in the site"),
    "an absolute manifest address": (lambda s: _break(s, "site.webmanifest", '"start_url": "./"', '"start_url": "/"'),
                                     "site.webmanifest: '/' is not relative"),
    "no manifest": (lambda s: (s / "site.webmanifest").unlink(), "no readable site.webmanifest"),
    "a missing family document": (lambda s: (s / "family" / "prefs.json").unlink(), "holds no family/prefs.json"),
    "a stray file": (lambda s: (s / "notes.txt").write_text("x"), "notes.txt is not a file a demo site publishes"),
    "a stray json beside the exports": (lambda s: (s / "extra.json").write_text("{}"),
                                        "extra.json is not a file a demo site publishes"),
    "a fifth family document": (lambda s: (s / "family" / "other.json").write_text("{}"),
                                "family/other.json is not a file a demo site publishes"),
    "an asset of another kind": (lambda s: (s / "assets" / "font.woff2").write_bytes(b"x"),
                                 "assets/font.woff2 is not a file a demo site publishes"),
    "a nested asset": (lambda s: ((s / "assets" / "deep").mkdir(), (s / "assets" / "deep" / "x.js").write_text("")),
                       "assets/deep/x.js is not a file a demo site publishes"),
    "a source map": (lambda s: (s / "assets" / "index.js.map").write_text("{}"),
                     "assets/index.js.map is a source map, which the site never publishes"),
    "a link": (lambda s: (s / "assets" / "linked.js").symlink_to(s / "theme.js"), "assets/linked.js is a link"),
}


def test_a_site_is_held_to_the_files_the_exports_manifest_lists(site, exports, root):
    (exports / "manifest.json").write_text('{"generated_at": "2026-10-31T20:00:00-04:00"}')
    assert "the exports' manifest.json lists no files, so what the site may publish is unknown" in \
        problems(site, exports, root)


@pytest.mark.parametrize("defect", DEFECTS)
def test_each_defect_alone_fails_the_check_with_its_line(site, exports, root, defect):
    make, words = DEFECTS[defect]
    make(site)
    got = problems(site, exports, root)
    assert got and any(words in line for line in got), got


def test_the_command_line_stages_then_checks_and_says_so(tmp_path, root, exports, capsys, site):
    assert demo_site.main(["check", str(site), "--exports", str(exports), "--root", str(root)]) == 0
    assert capsys.readouterr().out.startswith("demo site: 13 files;")
    (site / "version.json").write_text("{}")
    assert demo_site.main(["check", str(site), "--exports", str(exports), "--root", str(root)]) == 1
    assert "demo site: the site publishes version.json" in capsys.readouterr().err
    assert demo_site.main(["stage", str(tmp_path / "p"), "--exports", str(exports), "--root", str(root)]) == 0
    assert demo_site.main(["stage", str(tmp_path / "p"), "--exports", str(tmp_path / "none"), "--root", str(root)]) == 2
    assert demo_site.main(["nonsense", str(tmp_path)]) == 2


def test_the_demo_pages_policy_is_the_web_servers_less_frame_ancestors():
    """app/build/demoPage.ts's constant, which the demo build writes into its page, against deploy/Caddyfile's header"""
    header = demo_site.caddy_policy((ROOT / "deploy" / "Caddyfile").read_text("utf-8"))
    assert "frame-ancestors" in header
    source = (ROOT / "app" / "build" / "demoPage.ts").read_text("utf-8")
    block = re.search(r"DEMO_CSP = (.*?);\n", source, re.S)
    assert block, "demoPage.ts holds DEMO_CSP"
    constant = "".join(re.findall(r'"([^"]*)"', block.group(1)))
    assert constant == demo_site.demo_policy(header)
    assert "frame-ancestors" not in constant


def test_the_rules_are_read_from_the_tree():
    """the banner's words and the source address the check reads are the app's own constants"""
    assert demo_site._ts_string(ROOT / "app" / "src" / "lib" / "demo.ts", "DEMO_BANNER_TEXT") \
        == "Demo: an invented household. Changes stay in this browser."
    assert demo_site._ts_string(ROOT / "app" / "src" / "lib" / "product.ts", "DEFAULT_SOURCE_URL").startswith("https://")

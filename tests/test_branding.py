# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Every shipped asset names the product, Family Financials, and nothing else: the login page, the dashboard's page, the
web manifest, the icons, the failure alert's prefix and the package's description and authors. The example environment
file holds placeholders only (example domains, the documentation and loopback address ranges, no secret, no other
host named anywhere in it), each line explained;
the icon artwork is plain shapes with no metadata, comment or raster, and the PNGs carry no text chunk. Reads the
shipped files and runs refresh.sh's test alert to a webhook on 127.0.0.1 that records what it was sent (nothing leaves
the machine)."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import tomllib
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import alert  # deploy/ is on sys.path (tests/conftest.py)
import fake_alerts
import pytest
import render_icons  # deploy/ is on sys.path (tests/conftest.py): the PNGs' names and sizes
from conftest import ROOT

PRODUCT = "Family Financials"
SHORT_NAME = "Financials"          # the home-screen label where a short one is needed (12 characters at most)
ALERT_PREFIX = f"{PRODUCT}: "

BUILT = {"node_modules", "dist"}    # installed packages and build output: not shipped sources


def shipped(base: Path, pattern: str) -> list[Path]:
    """Every file under `base` matching `pattern` at any depth, outside installed packages and build output, so a page
    or an icon added later joins the checks."""
    return sorted(p for p in base.rglob(pattern) if not BUILT & set(p.relative_to(base).parts))


SHIPPED_HTML = sorted([*shipped(ROOT / "deploy", "*.html"), *shipped(ROOT / "app", "*.html")])
MANIFESTS = shipped(ROOT / "deploy", "*.webmanifest")
ICONS = ROOT / "deploy" / "public" / "icons"
SVG_ICONS = shipped(ICONS, "*.svg")
PNG_ICONS = shipped(ICONS, "*.png")
ENV_EXAMPLE = ROOT / ".env.example"

# the meta tags by which a page names itself (an installed app's label, a link preview's title)
NAMING_META = {"apple-mobile-web-app-title", "application-name", "og:title", "og:site_name", "twitter:title"}


class PageNames(HTMLParser):
    """What an HTML page calls itself: the text of each <title> and <h1>, and each naming meta tag's content"""

    def __init__(self) -> None:
        super().__init__()
        self.titles: list[str] = []
        self.headings: list[str] = []
        self.meta: dict[str, list[str]] = {}
        self._open: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag in ("title", "h1"):
            self._open, self._text = tag, []
        elif tag == "meta" and (a.get("name") or a.get("property")) in NAMING_META:
            self.meta.setdefault(a.get("name") or a.get("property") or "", []).append(a.get("content") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == self._open:
            text = " ".join("".join(self._text).split())
            (self.titles if tag == "title" else self.headings).append(text)
            self._open = None

    def handle_data(self, data: str) -> None:
        if self._open:
            self._text.append(data)


def names_of(page: Path) -> PageNames:
    parser = PageNames()
    parser.feed(page.read_text("utf-8"))
    return parser


def test_the_shipped_pages_are_the_login_page_and_the_dashboards():
    assert [p.relative_to(ROOT).as_posix() for p in SHIPPED_HTML] == ["app/index.html", "deploy/public/login.html"]
    assert [p.relative_to(ROOT).as_posix() for p in MANIFESTS] == ["deploy/public/site.webmanifest"]


def test_the_login_page_is_titled_and_headed_with_the_product_and_installs_under_its_name():
    names = names_of(ROOT / "deploy" / "public" / "login.html")
    assert names.titles == [PRODUCT]
    assert names.headings == [PRODUCT]
    assert names.meta == {"apple-mobile-web-app-title": [PRODUCT]}


def test_the_dashboards_page_is_titled_with_the_product_and_installs_under_its_name():
    names = names_of(ROOT / "app" / "index.html")
    assert names.titles == [PRODUCT]
    assert names.headings == []                  # the app draws its own header
    assert names.meta == {"apple-mobile-web-app-title": [PRODUCT]}


@pytest.mark.parametrize("page", SHIPPED_HTML, ids=lambda p: p.name)
def test_no_shipped_page_calls_itself_anything_but_the_product(page):
    names = names_of(page)
    said = [*names.titles, *names.headings, *(c for cs in names.meta.values() for c in cs)]
    assert said and set(said) == {PRODUCT}


def test_the_web_manifest_names_the_product_and_a_short_label_that_fits_a_home_screen():
    doc = json.loads((ROOT / "deploy" / "public" / "site.webmanifest").read_text("utf-8"))
    assert (doc["name"], doc["short_name"]) == (PRODUCT, SHORT_NAME)
    assert len(doc["short_name"]) <= 12


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.name)
def test_no_manifest_names_anything_but_the_product(manifest):
    doc = json.loads(manifest.read_text("utf-8"))
    naming = {k: doc[k] for k in ("name", "short_name", "description") if k in doc}
    assert naming == {"name": PRODUCT, "short_name": SHORT_NAME}


SVG = "{http://www.w3.org/2000/svg}"
# what icon artwork is made of: shapes, gradients, groups and its title
SHAPES = {"svg", "title", "defs", "linearGradient", "radialGradient", "stop", "g", "path", "rect", "circle", "ellipse",
          "line", "polyline", "polygon", "clipPath", "mask", "use"}


@pytest.mark.parametrize("icon", SVG_ICONS, ids=lambda p: p.name)
def test_each_svg_icon_is_titled_with_the_product_and_drawn_from_plain_shapes(icon):
    text = icon.read_text("utf-8")
    root = ET.fromstring(text)
    assert root.tag == f"{SVG}svg"
    assert [t.text for t in root.iter(f"{SVG}title")] == [PRODUCT]
    assert root.get("aria-label") == PRODUCT
    tags = {el.tag for el in root.iter()}
    assert all(t.startswith(SVG) for t in tags), "every element is an SVG element (no editor namespace)"
    assert {t.removeprefix(SVG) for t in tags} <= SHAPES, "no metadata, text or raster element"
    assert re.findall(r"xmlns(?::\w+)?=", text) == ["xmlns="], "the SVG namespace is the only one declared"
    assert "<!--" not in text and "<?" not in text, "no comment and no processing instruction"
    values = [v for el in root.iter() for v in el.attrib.values()]
    assert not any(v.strip().startswith("data:") for v in values), "no embedded data URI"


def test_the_png_icons_are_the_rendered_sizes_with_no_text_chunks():
    want = {f"{name}.png": size for name, size in render_icons.TARGETS.items()}
    assert sorted(p.name for p in PNG_ICONS) == sorted(want)
    for png in PNG_ICONS:
        data = png.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", png.name
        pos, kinds, size = 8, set(), None
        while pos < len(data):
            length = int.from_bytes(data[pos:pos + 4], "big")
            kind = data[pos + 4:pos + 8].decode("ascii")
            if kind == "IHDR":
                size = (int.from_bytes(data[pos + 8:pos + 12], "big"), int.from_bytes(data[pos + 12:pos + 16], "big"))
            kinds.add(kind)
            pos += 12 + length
        assert size == (want[png.name], want[png.name]), png.name
        assert kinds == {"IHDR", "IDAT", "IEND"}, f"{png.name}: image data only"


def test_the_failure_alert_is_prefixed_with_the_product_when_the_household_names_none(tmp_path):
    """refresh.sh's test alert, sent to a webhook on 127.0.0.1 (tests/fake_alerts.py) that the alert secrets set up,
    for a config whose household.yaml names no household: the message starts with the product's name and a colon, and
    the name is all the prefix holds (a household's own name takes its place: tests/test_alert.py)."""
    (tmp_path / "simplefin").mkdir()
    alerts, config = tmp_path / "alerts", tmp_path / "config"
    alerts.mkdir()
    config.mkdir()
    assert alert.PRODUCT == PRODUCT
    with fake_alerts.serve() as hook:
        url = alerts / alert.URL_FILE
        url.write_text(f"{hook.origin}/ping")
        url.chmod(0o600)
        r = subprocess.run(
            [str(ROOT / "deploy" / "refresh.sh"), "test-alert"], capture_output=True, text=True, timeout=30,
            env={**os.environ, "FF_ROOT": str(tmp_path), "FF_APP": str(ROOT), "FF_CONFIG_DIR": str(config),
                 "FF_SECRETS_ALERTS_DIR": str(alerts), alert.TEST_ORIGIN_VARIABLE: hook.origin})
    assert r.returncode == 0, r.stderr
    [sent] = hook.received
    assert sent.path == "/ping"
    assert sent.text.startswith(ALERT_PREFIX)
    assert sent.text.split(": ", 1)[0] == PRODUCT
    assert "alert delivered" in r.stdout


def test_the_package_is_described_and_authored_as_the_product():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    assert project["description"].split(":", 1)[0] == f"{PRODUCT} ETL"
    assert project["authors"] == [{"name": f"{PRODUCT} contributors"}]
    assert "maintainers" not in project


def env_example() -> tuple[list[str], dict[str, str]]:
    lines = ENV_EXAMPLE.read_text("utf-8").splitlines()
    values = dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))
    return lines, values


def test_the_example_environment_holds_placeholders_and_no_secret():
    """Deployment settings only: the credentials are files under secrets/ and the zone is household.yaml's."""
    _, values = env_example()
    assert values == {"FF_BIND": "127.0.0.1", "FF_PORT": "8080", "FF_PORT_TLS": "8443", "FF_UID": "1000",
                      "FF_GID": "1000", "SITE_ADDRESS": ":8080", "FF_HOSTNAMES": "", "TRUSTED_PROXIES": "",
                      "FF_IMAGE_WEB": "", "FF_IMAGE_ETL": ""}


def test_each_example_setting_says_in_a_comment_what_it_is_for():
    lines, _ = env_example()
    for n, line in enumerate(lines):
        if line and not line.startswith("#"):
            assert n > 0 and lines[n - 1].startswith("# "), f"{line.split('=')[0]}: no comment line directly above"


DOCUMENTATION_NETS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
LOOPBACK = ipaddress.ip_network("127.0.0.0/8")
# example.org / .com / .net and their subdomains, and any name under the reserved top-level `example`
EXAMPLE_DOMAIN = re.compile(r"^(?:(?:[a-z0-9-]+\.)*example\.(?:org|com|net)|(?:[a-z0-9-]+\.)+example)$")
# a dotted name read as a host wherever it stands (a URL, a bare name in a comment, an address after an @): labels of
# letters, digits and dashes, the last at least two letters, so neither "e.g." nor an address is one
HOST_LIKE = re.compile(r"(?<![\w.-])(?:[a-z0-9-]+\.)+[a-z]{2,}(?![\w-])", re.IGNORECASE)


def repository_file_names() -> set[str]:
    """the names of the repository's files (a config file the comments name, such as household.yaml, is no host)"""
    names: set[str] = set()
    for _, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in BUILT | {".git"}]
        names.update(files)
    return names


def other_hosts(text: str, files: set[str]) -> list[str]:
    """the host-like names in `text` that are neither an example domain nor the name of a repository file"""
    return [m.group(0) for m in HOST_LIKE.finditer(text)
            if not (EXAMPLE_DOMAIN.match(m.group(0).lower()) or m.group(0) in files)]


def test_the_example_environment_names_only_example_hosts_and_documentation_addresses():
    text = ENV_EXAMPLE.read_text("utf-8")
    hosts = re.findall(r"https?://([^/\s:)]+)", text)
    addresses = re.findall(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])", text)
    assert hosts and addresses, "the file shows an example URL and an example address"
    for host in hosts:
        assert EXAMPLE_DOMAIN.match(host) or re.fullmatch(r"[\d.]+", host), host
    for a in addresses:
        ip = ipaddress.ip_address(a)
        # 0.0.0.0, "every address of this machine" (what a domain's install listens on), names no machine at all
        assert ip in LOOPBACK or any(ip in net for net in DOCUMENTATION_NETS) or a == "0.0.0.0", a
    assert any(EXAMPLE_DOMAIN.match(m.group(0)) for m in HOST_LIKE.finditer(text)), "the file names an example host"
    assert other_hosts(text, repository_file_names()) == []


@pytest.mark.parametrize("line, caught", [
    ("# reach it at mybox.lan from the house", ["mybox.lan"]),
    ("# alerts go to ops@alerts.example.net", []),
    ("# or a tunnel such as tunnel.example", []),
    ("# copy household.yaml first, e.g. from examples/", []),
])
def test_a_bare_host_in_a_comment_is_read_as_one(line, caught):
    assert other_hosts(line, repository_file_names()) == caught

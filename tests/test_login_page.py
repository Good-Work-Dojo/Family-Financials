# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The sign-in page (deploy/public/) and the dashboard's page shell (app/index.html) as the content-security policy
needs them (deploy/Caddyfile): no inline script, no style block, no style attribute and no inline event handler, every
script and stylesheet a file of the page's own. The sign-in page itself: one passcode field a password manager and a
phone keyboard treat as one, labelled, with no length or character limit (any passcode the store accepts can be typed);
a form that posts without JavaScript; the product's name and nothing of a household; a footer with the copyright line,
the licence and the source link, in the About card's words; and a public directory that holds only the page, its two
files, the icons and the web manifest. The web image's build points the footer's source link at its own source
(deploy/source_link.sh) and changes nothing else.

Reads files, and runs deploy/source_link.sh on temporary copies."""
from __future__ import annotations

import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from conftest import ROOT

PUBLIC = ROOT / "deploy" / "public"
LOGIN = PUBLIC / "login.html"
INDEX = ROOT / "app" / "index.html"
ABOUT = ROOT / "app" / "src" / "sections" / "settings" / "AboutCard.tsx"
SOURCE_LINK = ROOT / "deploy" / "source_link.sh"
PRODUCT = "Family Financials"


class Page(HTMLParser):
    """every element's tag and attributes in document order, the text inside each <script> and <style>, and the text a
    person reads"""

    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self.bodies: dict[str, list[str]] = {"script": [], "style": []}
        self.text: list[str] = []
        self._in: str | None = None

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))
        if tag in self.bodies:
            self._in = tag
            self.bodies[tag].append("")

    def handle_endtag(self, tag):
        if tag == self._in:
            self._in = None

    def handle_data(self, data):
        if self._in:
            self.bodies[self._in][-1] += data
        else:
            self.text.append(data)

    def all(self, tag: str) -> list[dict[str, str | None]]:
        return [a for t, a in self.elements if t == tag]


def read(path: Path) -> Page:
    page = Page()
    page.feed(path.read_text("utf-8"))
    page.close()
    return page


def own(path: Path, ref: str | None) -> bool:
    """`ref` names one of the page's own files: from the site's root on the sign-in page (served at /login), relative
    to the page on the dashboard's (served at /, and the demo site under any sub-path)"""
    return (ref or "").startswith("/" if path == LOGIN else "./") and not (ref or "").startswith("//")


@pytest.mark.parametrize("path", [LOGIN, INDEX], ids=["login", "index"])
def test_the_page_holds_no_inline_code_the_policy_would_refuse(path):
    page = read(path)
    assert page.bodies["style"] == [], "no style block"
    assert page.all("script"), "the page loads its script from a file"
    for attrs, body in zip(page.all("script"), page.bodies["script"], strict=True):
        assert own(path, attrs.get("src")), f"a script from the page's own files: {attrs}"
        assert body.strip() == "", "no inline script"
    for tag, attrs in page.elements:
        assert "style" not in attrs, f"<{tag}> has a style attribute"
        assert not [a for a in attrs if a.startswith("on")], f"<{tag}> has an inline event handler"
    for link in page.all("link"):
        assert own(path, link["href"]), link


def test_the_page_shell_loads_the_theme_from_a_file_before_the_bundle():
    scripts = read(INDEX).all("script")
    assert [s.get("src") for s in scripts] == ["./theme.js", "./src/main.tsx"]
    assert "type" not in scripts[0] and "defer" not in scripts[0] and "async" not in scripts[0]
    assert scripts[1].get("type") == "module"


def test_the_sign_in_page_loads_only_its_own_public_files():
    page = read(LOGIN)
    loaded = [a["src"] for a in page.all("script")] + [a["href"] for a in page.all("link")] \
        + [a["src"] for a in page.all("img")]
    for ref in loaded:
        assert ref in ("/login.js", "/login.css", "/site.webmanifest") or ref.startswith("/icons/"), ref
        assert (PUBLIC / ref.lstrip("/")).is_file(), f"{ref} is in deploy/public"


def test_the_sign_in_form_posts_one_labelled_passcode_field_that_takes_any_passcode():
    page = read(LOGIN)
    [form] = page.all("form")
    assert (form["method"].lower(), form["action"]) == ("post", "/auth/login"), "it posts without JavaScript"
    [field] = page.all("input")
    assert field["name"] == "passcode" and field["type"] == "password"
    assert field["autocomplete"] == "current-password" and field["autocapitalize"] == "off"
    for limit in ("maxlength", "minlength", "pattern", "inputmode", "size"):
        assert limit not in field, f"no {limit}: a passcode of any length and any characters can be typed"
    assert [a["for"] for a in page.all("label")] == [field["id"]], "a visible label names the field"
    [button] = page.all("button")
    assert button.get("type") == "submit"
    assert field.get("aria-describedby") == "message"
    assert [a for a in page.all("p") if a.get("id") == "message"], "the message the field is described by"


def about_line(name: str) -> str:
    """a string constant of Settings' About card (app/src/sections/settings/AboutCard.tsx), its \\u escapes read"""
    raw = re.search(rf'^export const {name} = "([^"\\]*(?:\\u[0-9a-fA-F]{{4}}[^"\\]*)*)";$', ABOUT.read_text("utf-8"),
                    re.M).group(1)
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), raw)


def test_the_sign_in_page_names_the_product_and_nothing_of_a_household():
    page = read(LOGIN)
    assert read(LOGIN).all("html")[0]["lang"] == "en"
    assert any(a.get("name") == "viewport" for a in page.all("meta"))
    words = " ".join(" ".join(page.text).split())
    legal = f"{about_line('COPYRIGHT')} {about_line('LICENCE_LINE')} Source code"
    assert words == f"{PRODUCT} {PRODUCT} Passcode Sign in {legal}", \
        "the title, the heading, the form's own words and the footer's"
    raw = LOGIN.read_text("utf-8") + (PUBLIC / "login.js").read_text("utf-8")
    assert not re.search(r"""\w\.json["'?#/]|ff-build|version""", raw), "no export and no version before signing in"


class Footer(HTMLParser):
    """the text of each <p> inside the page's <footer>, the link each holds, and the elements after the footer"""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self.links: list[str | None] = []
        self.targets: list[tuple[str | None, str | None] | None] = []
        self.depth = 0
        self.after: list[str] = []
        self.closed = False

    def handle_starttag(self, tag, attrs):
        if tag == "footer":
            self.depth += 1
        elif self.depth and tag == "p":
            self.lines.append("")
            self.links.append(None)
            self.targets.append(None)
        elif self.depth and tag == "a":
            self.links[-1] = dict(attrs).get("href")
            self.targets[-1] = (dict(attrs).get("target"), dict(attrs).get("rel"))
        elif self.closed:
            self.after.append(tag)

    def handle_endtag(self, tag):
        if tag == "footer":
            self.depth -= 1
            self.closed = True

    def handle_data(self, data):
        if self.depth and self.lines:
            self.lines[-1] += data


def test_the_sign_in_page_says_whose_the_program_is_its_licence_and_where_its_source_is():
    """The footer keeps the three lines NOTICE asks for, in the About card's words: the copyright (the line NOTICE
    names), free software under the AGPL with no warranty, and a link to the source at the address the dashboard links
    to by default and the web image is labelled with."""
    footer = Footer()
    footer.feed(LOGIN.read_text("utf-8"))
    copyright_line, licence = about_line("COPYRIGHT"), about_line("LICENCE_LINE")
    assert [" ".join(t.split()) for t in footer.lines] == [copyright_line, licence, "Source code"]
    assert re.fullmatch(r"© 2026 \S.+", copyright_line) and "@" not in copyright_line
    notice = " ".join((ROOT / "NOTICE").read_text("utf-8").split())
    assert f'"{copyright_line}"' in notice, "the attribution line NOTICE states"
    assert "in the Appropriate Legal Notices (section 0) its interactive user interfaces display — in this program " \
           "the About screen (Settings, About) and the sign-in page —" in notice, "the term names where the line stays"
    assert "AGPL 3.0" in licence and "no warranty" in licence
    source = re.search(r'^export const DEFAULT_SOURCE_URL = "([^"]+)";$',
                       (ROOT / "app" / "src" / "lib" / "product.ts").read_text("utf-8"), re.M).group(1)
    web = (ROOT / "deploy" / "Dockerfile.web").read_text("utf-8")
    assert footer.links == [None, None, source]
    assert footer.targets == [None, None, ("_blank", "noopener noreferrer")], "it opens beside the page, unlinked"
    assert set(re.findall(r"^ARG FF_SOURCE=(\S+)$", web, re.M)) == {source}
    assert re.findall(r"^default=(\S+)$", SOURCE_LINK.read_text("utf-8"), re.M) == [source]
    assert footer.after == [], "the footer is the page's last element"
    css = (PUBLIC / "login.css").read_text("utf-8")
    assert re.search(r"\n\.legal \{[^}]*max-width: 22rem;", css), "as wide as the card at most"
    assert re.search(r"\n\.legal a:focus-visible \{[^}]*outline:", css), "the link shows its focus"


def source_link(tmp_path: Path, url: str, page: str | None = None) -> tuple[subprocess.CompletedProcess, str, str]:
    """deploy/source_link.sh run on a copy of the sign-in page (or `page`): its result, the page before and after"""
    copy = tmp_path / "login.html"
    before = LOGIN.read_text("utf-8") if page is None else page
    copy.write_text(before, "utf-8")
    r = subprocess.run(["sh", str(SOURCE_LINK), str(copy), url], capture_output=True, text=True)
    return r, before, copy.read_text("utf-8")


def test_a_build_points_the_sign_in_pages_source_link_at_its_own_source_and_changes_nothing_else(tmp_path):
    fork = "https://example.net/someone/fork"
    r, before, after = source_link(tmp_path, fork)
    assert r.returncode == 0, r.stderr
    footer = Footer()
    footer.feed(after)
    assert footer.links == [None, None, fork]
    default = re.search(r'^export const DEFAULT_SOURCE_URL = "([^"]+)";$',
                        (ROOT / "app" / "src" / "lib" / "product.ts").read_text("utf-8"), re.M).group(1)
    assert after == before.replace(f'href="{default}"', f'href="{fork}"'), "that one attribute alone"
    r, before, after = source_link(tmp_path, default)
    assert r.returncode == 0 and after == before, "the repository's own build leaves the page as it is"


@pytest.mark.parametrize("url, words", [
    ("ftp://example.net/fork", "an http or https address"), ("https://", "an http or https address"),
    ('https://example.net/"onmouseover=x', "only letters"), ("https://example.net/a b", "only letters"),
    ("https://example.net/a&b", "only letters"), ("https://example.net/a|b", "only letters"),
    ("https://example.net/<x>", "only letters")])
def test_a_source_address_that_could_not_stand_in_the_link_as_it_is_fails_the_build(tmp_path, url, words):
    r, before, after = source_link(tmp_path, url)
    assert r.returncode == 1 and words in r.stderr and after == before


def test_a_page_without_the_repositorys_link_exactly_once_fails_the_build(tmp_path):
    link = '<a href="https://github.com/Good-Work-Dojo/Family-Financials">x</a>'
    for page in ("<p>no link</p>\n", link + link + "\n"):
        r, before, after = source_link(tmp_path, "https://example.net/fork", page)
        assert r.returncode == 1 and "not once" in r.stderr and after == before


def test_the_sign_in_script_asks_the_store_one_thing_and_says_each_answer_in_words():
    js = (PUBLIC / "login.js").read_text("utf-8")
    assert re.findall(r"""["'`](/[^"'`]*)["'`]""", js) == ["/auth/status"], "the one thing it asks, nothing else"
    assert "That passcode did not work. Try again." in js            # ?e=1, the same words for every reason
    assert "Too many attempts. Try again in a few minutes." in js    # ?e=2
    assert "Sign-in is not set up yet." in js
    assert re.search(r"\{ code: \"ff passcode add\" \}", js), "the command the person runs, shown as a command"
    assert "innerHTML" not in js and "eval(" not in js


def test_the_sign_in_page_follows_the_device_light_or_dark_and_fits_a_phone():
    css = (PUBLIC / "login.css").read_text("utf-8")
    assert "@media (prefers-color-scheme: light)" in css
    assert "@import" not in css and not re.search(r"url\(", css), "nothing loaded from the stylesheet"
    field = re.search(r"\ninput \{(.*?)\n\}", css, re.S).group(1)
    assert re.search(r"font-size: 1rem;", field), "16px, so a phone does not zoom in on the field"
    assert "max-width: 22rem" in css and "width: 100%" in css and "padding: 16px" in css


def test_the_public_directory_holds_only_the_sign_in_page_its_files_the_icons_and_the_manifest():
    files = sorted(p.relative_to(PUBLIC).as_posix() for p in PUBLIC.rglob("*") if p.is_file())
    icons = sorted(f for f in files if f.startswith("icons/"))
    assert sorted(set(files) - set(icons)) == ["login.css", "login.html", "login.js", "site.webmanifest"]
    assert icons == ["icons/apple-touch-icon.png", "icons/favicon-16.png", "icons/favicon-32.png", "icons/icon-192.png",
                     "icons/icon-512.png", "icons/icon.svg"]
    assert not (ROOT / "deploy" / "login.html").exists() and not (ROOT / "deploy" / "icons").exists()

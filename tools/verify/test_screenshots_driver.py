# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""tools/verify/screenshots_driver.py - FF_SHOT_VIEWPORTS and FF_SHOT_PRIVATE parsing. The gate never sets
FF_SHOT_VIEWPORTS (two standard viewports) and screenshots.sh forces FF_SHOT_PRIVATE=1 for real exports; a carry that
needs a tablet or a 360px shot, or an unmasked run of synthetic exports, sets one, and a typo
must stop the run before the SPA is built rather than shoot the wrong thing. Also the id the account route is shot
with (accounts.json stood in for), the browser's zone, the fonts wait, the open drawer's shot and the count, and the
store documents screenshots.sh serves, each tied to what deploy/scenario_store.py answers. No browser, no playwright
import: a page, a context and a browser are stood in for.

Lives beside the driver, NOT under tests/: the harness is tooling, not product code, so the main run collects only
tests/. tools/verify/ci.sh runs this file as its own step:
  docker run --rm --user root <the ETL test image> sh -lc "uv run pytest -q -p no:cacheprovider tools/verify" """
from __future__ import annotations

import importlib
import importlib.util
import inspect
import io
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.demo import household

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import screenshots_driver as driver  # noqa: E402  (tools/verify is not a package)

STANDARD = {"390x844": (390, 844), "1280x900": (1280, 900)}


@pytest.mark.parametrize("raw", [None, "", " ", "  \t\n "])
def test_unset_or_blank_is_the_two_standard_viewports(raw):
    assert driver.viewports_from_env(raw) == STANDARD


def test_the_defaults_are_a_copy_not_the_shared_dict():
    got = driver.viewports_from_env(None)
    got["1x1"] = (1, 1)
    assert driver.viewports_from_env(None) == STANDARD
    assert driver.DEFAULT_VIEWPORTS == STANDARD


def test_a_list_overrides_the_pair_and_keeps_its_order_and_spelling():
    got = driver.viewports_from_env("360x780 1100x800")
    assert got == {"360x780": (360, 780), "1100x800": (1100, 800)}
    assert list(got) == ["360x780", "1100x800"]                     # PNG names follow these keys
    assert list(driver.viewports_from_env("1280x900 390x844")) == ["1280x900", "390x844"]


def test_any_whitespace_separates_and_one_token_is_a_list_of_one():
    assert driver.viewports_from_env("  1024x768 \t 1280x900\n") == {"1024x768": (1024, 768), "1280x900": (1280, 900)}
    assert driver.viewports_from_env("1024x768") == {"1024x768": (1024, 768)}


@pytest.mark.parametrize("bad", [
    "390", "1100x", "x800", "abc", "390x844,1280x900", "390X844", "1x2", "12345x800", "390x84", "-390x844", "390x844px",
])
def test_a_malformed_entry_is_refused_by_name(bad):
    with pytest.raises(SystemExit) as exc:
        driver.viewports_from_env(bad)
    assert repr(bad) in str(exc.value)
    assert "FF_SHOT_VIEWPORTS" in str(exc.value)


def test_one_bad_entry_among_good_ones_refuses_the_whole_run():
    with pytest.raises(SystemExit) as exc:
        driver.viewports_from_env("360x780 oops 1100x800")
    assert "'oops'" in str(exc.value)


def test_pngs_per_route_follow_the_viewport_count():
    assert len(driver.viewports_from_env(None)) * len(driver.THEMES) == 4              # the gate: routes x 4
    assert len(driver.viewports_from_env("390x844 1024x768 1280x900")) * len(driver.THEMES) == 6


def test_the_module_reads_the_env_once_at_import(monkeypatch):
    monkeypatch.setenv("FF_SHOT_VIEWPORTS", "360x780 1100x800")
    try:
        assert importlib.reload(driver).VIEWPORTS == {"360x780": (360, 780), "1100x800": (1100, 800)}
        monkeypatch.setenv("FF_SHOT_VIEWPORTS", "nope")
        with pytest.raises(SystemExit):                              # screenshots.sh imports it on the host: refused before the build
            importlib.reload(driver)
    finally:
        monkeypatch.delenv("FF_SHOT_VIEWPORTS")
        assert importlib.reload(driver).VIEWPORTS == STANDARD


@pytest.mark.parametrize("raw", [None, "", " ", "0", " 0 "])
def test_unset_blank_or_zero_is_the_normal_unmasked_run(raw):
    assert driver.private_from_env(raw) is False


@pytest.mark.parametrize("raw", ["1", " 1 "])
def test_one_is_the_masked_run(raw):
    assert driver.private_from_env(raw) is True


@pytest.mark.parametrize("bad", ["true", "yes", "on", "2", "01", "private"])
def test_anything_else_is_refused_by_name_never_guessed(bad):
    # a typo must not quietly give an unmasked set of PNGs that look like a private run
    with pytest.raises(SystemExit) as exc:
        driver.private_from_env(bad)
    assert repr(bad) in str(exc.value)
    assert "FF_SHOT_PRIVATE" in str(exc.value)


@pytest.mark.parametrize("w,h", [(390, 844), (1280, 900), (360, 780)])
def test_every_context_emulates_reduced_motion_and_runs_in_the_households_zone(w, h):
    # EChart turns animation off under prefers-reduced-motion: reduce; without this a light-theme line chart
    # was captured half-drawn (the PNG reviewer's "ends earlier than dark")
    assert driver.context_options(w, h, "America/New_York") == {
        "viewport": {"width": w, "height": h}, "device_scale_factor": 1, "reduced_motion": "reduce",
        "timezone_id": "America/New_York"}


def test_the_init_script_writes_the_theme_and_only_a_masked_run_writes_the_privacy_flag():
    off = driver.init_script("dark", False)
    on = driver.init_script("light", True)
    assert "localStorage.setItem('theme', 'dark')" in off
    assert "ff.privacy" not in off
    assert "localStorage.setItem('theme', 'light')" in on
    assert "localStorage.setItem('ff.privacy', '1')" in on


@pytest.mark.parametrize("private", [False, True])
def test_the_init_script_is_plain_javascript_with_no_leftover_template_syntax(private):
    # the shape that was wrong once: a Python f-string writing `if ($true)` (a stray `$` before the placeholder)
    js = driver.init_script("dark", private)
    assert "$" not in js and "{{" not in js and "}}" not in js and "True" not in js
    assert js.startswith("try {") and js.endswith("} catch (e) {}")
    assert js.count("{") == js.count("}") and js.count("(") == js.count(")")


def test_the_module_reads_the_private_flag_once_at_import(monkeypatch):
    monkeypatch.setenv("FF_SHOT_PRIVATE", "1")
    try:
        assert importlib.reload(driver).PRIVATE is True
        monkeypatch.setenv("FF_SHOT_PRIVATE", "nope")
        with pytest.raises(SystemExit):                              # screenshots.sh imports it on the host: refused before the build
            importlib.reload(driver)
    finally:
        monkeypatch.delenv("FF_SHOT_PRIVATE")
        assert importlib.reload(driver).PRIVATE is False


# ---------------------------------------------------------------- FF_SHOT_QUERY

SRC = '''
export const PAGES = [
  { to: "/", label: "Home" },
  { to: "/spending", label: "Spending", hub: { tabs: [{ path: "transactions" }, { path: "reports" }] } },
  { to: "/accounts", label: "Accounts", hub: { extra: [{ path: ":id" }] } },
];
export const REDIRECTS = [["/life", "/cash-flow"], ["/recurring", "/spending/recurring"]];
'''
ALL = driver.routes_from_app_tsx(SRC)


def test_the_synthetic_manifest_parses_as_the_real_one_does():
    assert ALL == ["/", "/spending", "/spending/transactions", "/spending/reports", "/accounts", "/accounts/:id"]


@pytest.mark.parametrize("raw", [None, "", " ", "\t\n"])
def test_unset_or_blank_shoots_no_query(raw):
    assert driver.queries_from_env(raw, SRC, ALL) == {}


def test_each_entry_maps_its_route_to_its_query_and_keeps_the_query_verbatim():
    got = driver.queries_from_env("/spending/reports?measure=count&chart=table  /accounts/:id?from=2026-03-01&to=",
                                  SRC, ALL)
    assert got == {"/spending/reports": "measure=count&chart=table", "/accounts/:id": "from=2026-03-01&to="}


@pytest.mark.parametrize("entry,why", [
    ("/spending", "is not <route>?<query>"),
    ("/spending?", "is not <route>?<query>"),
    ("?x=1", "names no route of the PAGES manifest"),
    ("/nope?x=1", "names no route of the PAGES manifest"),
    ("/spending/?x=1", "names no route of the PAGES manifest"),
    ("/accounts/acct_123?x=1", "names no route of the PAGES manifest"),        # a real id in place of :id
    ("/life?pl=1", "names a REDIRECTS source"),
    ("/recurring?x=1", "names a REDIRECTS source"),
    ("/spending?x=1#frag", "carries a '#'"),
    ("/spending?a=1&&b=2", "is not a well-formed k=v&k=v query"),
    ("/spending?flag", "is not a well-formed k=v&k=v query"),
])
def test_a_bad_entry_is_refused_by_name_never_shot_without_its_query(entry, why):
    with pytest.raises(SystemExit) as exc:
        driver.queries_from_env(f"/spending/reports?chart=table {entry}", SRC, ALL)
    assert f"FF_SHOT_QUERY: {entry!r} {why}" in str(exc.value)


def test_one_query_per_route_and_only_for_a_route_the_run_shoots():
    with pytest.raises(SystemExit, match=r"'/spending\?b=2' repeats a route"):
        driver.queries_from_env("/spending?a=1 /spending?b=2", SRC, ALL)
    with pytest.raises(SystemExit, match=r"'/spending/reports\?a=1' names a route this run does not shoot"):
        driver.queries_from_env("/spending/reports?a=1", SRC, ["/spending"])
    assert driver.queries_from_env("/spending?a=1", SRC, ["/spending"]) == {"/spending": "a=1"}


def test_a_query_shot_is_named_apart_from_the_plain_one_and_never_carries_the_query_text():
    plain = driver.shot_name("/spending/transactions", None, "390x844", "dark")
    queried = driver.shot_name("/spending/transactions", "vendors=Synth+Co&from=2026-03-01", "390x844", "dark")
    assert plain == "spending_transactions__390x844__dark.png"
    assert queried == "spending_transactions_query__390x844__dark.png"
    assert driver.shot_name("/accounts/:id", "x=1", "1280x900", "light") == "accounts_id_query__1280x900__light.png"
    assert "Synth" not in queried and "2026" not in queried


def test_the_query_follows_the_hash_path():
    assert driver.shot_url("http://127.0.0.1:80", "/spending/reports", None) == "http://127.0.0.1:80/#/spending/reports"
    assert driver.shot_url("http://127.0.0.1:80", "/accounts/acct_1", "from=2026-03-01") == \
        "http://127.0.0.1:80/#/accounts/acct_1?from=2026-03-01"


# ---------------------------------------------------------------- the account route

BASE = "http://127.0.0.1:80"


def serve_accounts(monkeypatch, accounts: list[dict]) -> list[str]:
    """Stands in for the one request resolve_param makes, the served accounts.json; returns the URLs it was asked."""
    asked: list[str] = []

    def urlopen(url, timeout):
        asked.append(url)
        return io.BytesIO(json.dumps({"accounts": accounts}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return asked


@pytest.mark.parametrize("accounts,shot", [
    # a feed-only account (no ledger) before one with transactions: the one with transactions
    ([{"id": "acct_feed", "txn_data_end": None}, {"id": "acct_ledger", "txn_data_end": "2026-10-31"},
      {"id": "acct_later", "txn_data_end": "2026-10-30"}], "acct_ledger"),
    # no account has transactions: the first account at all
    ([{"id": "acct_feed", "txn_data_end": None}, {"id": "acct_other"}], "acct_feed"),
    # no account: the id `none`, which the app answers with its unknown-account card
    ([], "none"),
])
def test_the_account_route_falls_back_when_no_account_has_transactions(monkeypatch, accounts, shot):
    asked = serve_accounts(monkeypatch, accounts)
    assert driver.resolve_param("/accounts/:id", BASE, None) == f"/accounts/{shot}"
    assert asked == [f"{BASE}/accounts.json"]


def test_a_given_account_id_or_a_route_without_one_reads_nothing(monkeypatch):
    asked = serve_accounts(monkeypatch, [])
    assert driver.resolve_param("/accounts/:id", BASE, "acct_given") == "/accounts/acct_given"
    assert driver.resolve_param("/accounts", BASE, None) == "/accounts"
    assert asked == []


# ---------------------------------------------------------------- the pages' clock

INGEST = datetime(2026, 10, 31, 22, 0, tzinfo=timezone.utc)                # 18:00 in the demo household's zone


@pytest.mark.parametrize("stamp", ["2026-10-31T18:00:00-04:00", "2026-10-31T22:00:00Z", "2026-10-31T22:00:00+00:00",
                                   "2026-11-01T07:00:00+09:00"])
def test_the_ingest_instant_is_generated_at_with_its_offset(stamp):
    at = driver.ingest_instant({"schema_version": 18, "generated_at": stamp}, "manifest.json")
    assert at == INGEST and at.utcoffset() is not None


def test_the_ingest_instant_keeps_a_fraction_of_a_second():
    assert driver.ingest_instant({"generated_at": "2026-10-31T22:00:00.250Z"}, "m") == INGEST.replace(microsecond=250000)


@pytest.mark.parametrize("manifest", [
    {}, {"generated_at": None}, {"generated_at": 1793484000}, {"generated_at": ""}, {"generated_at": "yesterday"},
    {"generated_at": "2026-10-31"}, {"generated_at": "2026-10-31T18:00:00"}, {"generated_at": "2026-13-31T18:00:00Z"},
    [], "not a manifest",
])
def test_a_manifest_without_a_usable_generated_at_stops_the_run_naming_the_file_and_the_key(manifest):
    # never a fall back to the real clock: the shots would depend on the day they are taken
    with pytest.raises(SystemExit) as exc:
        driver.ingest_instant(manifest, "http://127.0.0.1:80/manifest.json")
    assert "http://127.0.0.1:80/manifest.json" in str(exc.value) and "generated_at" in str(exc.value)


def test_the_served_manifest_gives_the_instant(monkeypatch):
    asked: list[str] = []

    def urlopen(url, timeout):
        asked.append(url)
        return io.BytesIO(json.dumps({"generated_at": "2026-10-31T18:00:00-04:00"}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert driver.served_ingest_instant(BASE) == INGEST
    assert asked == [f"{BASE}/manifest.json"]


class FakeLocator:
    def __init__(self, log: list, role: str, name: str, exact: bool):
        self.log, self.role, self.name, self.exact = log, role, name, exact

    def click(self):
        self.log.append(("click", self.role, self.name, self.exact))


class FakePage:
    """What the driver touches of a Playwright page, recording the calls that matter in its context's log. `fonts` is
    what the bounded wait for document.fonts.ready answers (False: they were not ready within its timeout)."""

    def __init__(self, log: list, fonts: bool = True, missing: tuple[str, ...] = ()):
        self.log = log
        self.fonts = fonts
        self.missing = missing                                       # selectors whose control is not there to click
        self.clock = self                                            # page.clock.set_fixed_time(...)

    def set_fixed_time(self, time):
        self.log.append(("clock", time))

    def on(self, event, handler):
        pass

    def goto(self, url, wait_until):
        self.log.append(("goto", url))

    def get_by_role(self, role, name, exact):
        return FakeLocator(self.log, role, name, exact)

    def click(self, selector, timeout=None):
        self.log.append(("click", selector, timeout))
        if selector in self.missing:
            raise TimeoutError(f"Timeout {timeout}ms exceeded waiting for {selector}")

    def wait_for_function(self, js, timeout):
        if js == driver.DRAWER_OPEN:
            self.log.append(("drawer open",))

    def wait_for_selector(self, selector, timeout):
        self.log.append(("ready", selector))

    def wait_for_load_state(self, state):
        pass

    def wait_for_timeout(self, ms):
        pass

    def screenshot(self, path, full_page):
        self.log.append(("screenshot", Path(path).name, full_page))

    def evaluate(self, js, arg=None):
        if js == driver.FONTS_READY:
            self.log.append(("fonts",))
            return self.fonts
        if js == driver.SCROLL_TOP:
            self.log.append(("scroll top",))
            return None
        assert js == driver.OVERFLOW_JS
        self.log.append(("overflow", arg))                           # the argument: skip the off-canvas drawer?
        return {"page": False, "offenders": []}

    def close(self):
        self.log.append(("close",))


class FakeContext:
    """A context; `refuses` are the zone ids its pages cannot be made in (Chromium takes any well-formed zone id at
    new_context and refuses an unknown one when the first page is made)."""

    def __init__(self, fonts: bool = True, options: dict | None = None, refuses: tuple[str, ...] = (),
                 missing: tuple[str, ...] = ()):
        self.log: list = []
        self.fonts = fonts
        self.options = options or {}
        self.refuses = refuses
        self.missing = missing

    def new_page(self):
        if self.options.get("timezone_id") in self.refuses:
            raise RuntimeError(f"Invalid timezone ID: {self.options['timezone_id']}")
        self.log.append(("new_page",))
        return FakePage(self.log, self.fonts, self.missing)

    def add_init_script(self, js):
        self.log.append(("init", js))

    def close(self):
        self.log.append(("context closed",))


def route_shot(route: str, vname: str = "390x844", theme: str = "dark") -> list[tuple]:
    """The steps of one route's shot: a new page, its clock fixed, the route opened, the fonts waited for, the full
    page shot, the overflow check with the off-canvas drawer left out, the page closed."""
    return [("new_page",), ("clock", INGEST), ("goto", f"{BASE}/#{route}"), ("fonts",),
            ("screenshot", driver.shot_name(route, None, vname, theme), True), ("overflow", True), ("close",)]


def test_every_page_the_driver_opens_has_its_clock_fixed_before_it_loads(tmp_path):
    ctx = FakeContext()
    for route in ("/", "/spending", "/planning/second-house"):
        assert driver.shoot_route(ctx, INGEST, BASE, route, None, None, tmp_path, "390x844", "dark") is None
    assert ctx.log == [step for route in ("/", "/spending", "/planning/second-house") for step in route_shot(route)]
    # the instant reaches Playwright as an aware datetime, which it sends as that instant's epoch milliseconds
    assert all(step[1].utcoffset() is not None for step in ctx.log if step[0] == "clock")
    # open_page is the one place the driver opens a page to shoot; the zone probe opens one it closes at once
    assert Path(driver.__file__).read_text().count(".new_page(") == 2
    assert inspect.getsource(driver.open_page).count(".new_page(") == 1
    assert inspect.getsource(driver.accepted_zone).count(".new_page(") == 1


def test_a_shot_is_taken_once_the_fonts_are_ready_and_the_wait_is_bounded():
    # document.fonts.ready raced against a timer: a font that never loads cannot hang the run
    assert "document.fonts.ready" in driver.FONTS_READY and "setTimeout" in driver.FONTS_READY
    assert str(driver.FONTS_TIMEOUT_MS) in driver.FONTS_READY and driver.FONTS_TIMEOUT_MS == 5000


def test_fonts_not_ready_in_time_are_an_entry_and_the_page_is_still_shot(tmp_path):
    ctx = FakeContext(fonts=False)
    entry = driver.shoot_route(ctx, INGEST, BASE, "/spending", None, None, tmp_path, "1280x900", "light")
    assert ctx.log == route_shot("/spending", "1280x900", "light")
    assert entry is not None and entry["load_problems"] == [{"kind": "fonts not ready", "path": ""}]
    assert (entry["route"], entry["viewport"], entry["theme"]) == ("/spending", "1280x900", "light")
    assert entry["page"] is False and entry["offenders"] == [] and entry["page_errors"] == []


# ---------------------------------------------------------------- the open drawer

def test_the_drawer_is_opened_by_its_button_on_home_and_shot_open(tmp_path):
    ctx = FakeContext()
    assert driver.shoot_drawer(ctx, INGEST, BASE, tmp_path, "390x844", "light") is None
    assert ctx.log == [
        ("new_page",), ("clock", INGEST), ("goto", f"{BASE}/#/"),
        ("click", "button", "Open menu", True),                     # by its accessible name, never by coordinates
        ("drawer open",), ("fonts",),
        # the drawer and its backdrop are fixed to the screen: the shot is the screen, not the page under it
        ("screenshot", "root_drawer__390x844__light.png", False),
        # and the overflow check reads the drawer too: open, it is what the shot shows
        ("overflow", False), ("close",)]


def test_the_overflow_check_leaves_the_drawer_out_only_when_asked():
    """The check takes one argument: a route's shot passes true, so the off-canvas drawer (the app's one <aside>) is
    not an offender; the open drawer's shot passes false, so the drawer's own content is checked like the page's."""
    js = driver.OVERFLOW_JS.strip()
    assert js.startswith("(skipDrawer) =>")
    assert "if (skipDrawer && el.closest('aside')) continue;" in js
    assert js.count("aside") == 1


def test_the_drawer_shot_reports_what_a_route_shot_reports(tmp_path):
    ctx = FakeContext(fonts=False)
    entry = driver.shoot_drawer(ctx, INGEST, BASE, tmp_path, "390x844", "dark")
    assert entry is not None and entry["drawer"] is True and entry["route"] == "/"
    assert entry["load_problems"] == [{"kind": "fonts not ready", "path": ""}]


def test_the_button_the_driver_clicks_is_the_one_that_opens_the_apps_drawer():
    bar = (ROOT / "app" / "src" / "components" / "MobileTopBar.tsx").read_text("utf-8")
    assert re.search(r'<button onClick=\{onOpenMenu\} aria-label="' + re.escape(driver.DRAWER_BUTTON) + '"', bar)
    app = (ROOT / "app" / "src" / "App.tsx").read_text("utf-8")
    assert "onOpenMenu={() => setNavOpen(true)}" in app
    # the drawer is the app's one <aside>, and the open check reads where it sits on screen
    assert app.count("<aside") == 1 and "aside" in driver.DRAWER_OPEN


@pytest.mark.parametrize("viewports,phones", [
    (STANDARD, ["390x844"]),
    ({"360x780": (360, 780), "767x900": (767, 900), "768x1024": (768, 1024), "1100x800": (1100, 800)},
     ["360x780", "767x900"]),                                       # below the app's 768px cut the sidebar is a drawer
    ({"1024x768": (1024, 768)}, []),
])
def test_the_drawer_is_shot_at_each_phone_viewport(viewports, phones):
    assert driver.drawer_viewports(viewports) == phones


def test_the_drawer_shot_is_named_apart_from_every_route():
    assert driver.drawer_name("390x844", "dark") == "root_drawer__390x844__dark.png"
    src = (ROOT / "app" / "src" / "App.tsx").read_text("utf-8")
    stems = {driver.slug(r) for r in driver.routes_from_app_tsx(src)}
    assert "root_drawer" not in stems and "root" in stems


def test_the_count_is_every_route_at_every_viewport_and_theme_plus_the_drawer_once_per_theme_at_a_phone_width():
    src = (ROOT / "app" / "src" / "App.tsx").read_text("utf-8")
    routes = driver.routes_from_app_tsx(src)
    # every route, Settings twice more clicked, the drawer: 19 routes -> 76 + 8 + 2
    assert driver.expected_shots(routes, STANDARD) == (len(routes) + 2) * 4 + 2
    assert driver.expected_shots(["/", "/spending"], STANDARD) == 2 * 4 + 2
    # no drawer without Home in the run, and none without a phone viewport
    assert driver.expected_shots(["/spending", "/accounts"], STANDARD) == 2 * 4
    assert driver.expected_shots(["/"], {"1280x900": (1280, 900)}) == 2
    assert driver.expected_shots(["/"], {"360x780": (360, 780), "390x844": (390, 844)}) == 4 + 4


class FakeBrowser:
    """new_context records its options; a zone in `refuses` is refused where Chromium refuses an unknown id: the
    context is made, its first page is not."""

    def __init__(self, refuses: tuple[str, ...] = ()):
        self.refuses = refuses
        self.contexts: list[FakeContext] = []

    def new_context(self, **options):
        ctx = FakeContext(options=options, refuses=self.refuses)
        self.contexts.append(ctx)
        return ctx


def shots_of(browser: FakeBrowser) -> list[str]:
    return [step[1] for ctx in browser.contexts for step in ctx.log if step[0] == "screenshot"]


def test_every_context_runs_in_the_zone_and_the_drawer_follows_the_routes_of_each_phone_context(tmp_path):
    browser = FakeBrowser()
    overflow = driver.shoot_all(browser, INGEST, "America/New_York", BASE, ["/", "/spending"], {}, None, tmp_path,
                                STANDARD, private=False)
    assert overflow == []
    assert [c.options for c in browser.contexts] == [
        driver.context_options(w, h, "America/New_York") for _ in driver.THEMES for (w, h) in STANDARD.values()]
    names = shots_of(browser)
    assert len(names) == driver.expected_shots(["/", "/spending"], STANDARD) == 10
    # in each phone context the drawer comes after the routes, on its own page, before the context closes
    phone = browser.contexts[0].log
    shot_steps = [s[1] for s in phone if s[0] == "screenshot"]
    assert shot_steps == ["root__390x844__dark.png", "spending__390x844__dark.png", "root_drawer__390x844__dark.png"]
    assert phone[-1] == ("context closed",)
    assert not any("drawer" in s for s in shots_of(SimpleNamespace(contexts=[browser.contexts[1]])))


def test_a_run_without_home_shoots_no_drawer(tmp_path):
    browser = FakeBrowser()
    driver.shoot_all(browser, INGEST, "UTC", BASE, ["/spending"], {}, None, tmp_path, STANDARD, private=False)
    assert len(shots_of(browser)) == 4 and not any("drawer" in n for n in shots_of(browser))


# ---------------------------------------------------------------- a route shot again with its controls clicked

def test_the_click_table_is_settings_with_the_add_form_and_the_ai_dialog_open_and_names_controls_the_app_has():
    assert driver.CLICK_SHOTS == {"/settings": (("add_asset", ('[data-testid="manual-assets-add"]',)),
                                                ("ai_dialog", ('[data-testid="ai-open-dialog"]',)))}
    src = (ROOT / "app" / "src" / "App.tsx").read_text("utf-8")
    routes = driver.routes_from_app_tsx(src)
    assert set(driver.CLICK_SHOTS) <= set(routes), "a clicked shot belongs to a route of the PAGES manifest"
    settings = ROOT / "app" / "src" / "sections" / "settings"
    # each selector is its button's own test id: one button carries it, and the words it shows follow before the button
    # closes (the AI card's button says what it opens in every state: "See what would be sent", "Turn on…", …)
    for file, test_id, words in (("ManualAssetsCard.tsx", "manual-assets-add", ("Add an asset",)),
                                 ("AiCard.tsx", "ai-open-dialog", ("{openLabel}",))):
        card = (settings / file).read_text("utf-8")
        assert card.count(f'data-testid="{test_id}"') == 1, test_id
        at = card.index(f'data-testid="{test_id}"')
        opened, closed = card.rfind("<button", 0, at), card.index("</button>", at)
        assert card.rfind("</button>", opened, at) < 0, f"{test_id}: the test id sits on the button that opens before it"
        assert all(w in card[at:closed] for w in words), test_id
    # the AI card's button is there whatever the card's state, a household with no key stored included (the demo's)
    ai = (settings / "AiCard.tsx").read_text("utf-8")
    assert '"See what would be sent"' in ai and '"Turn on…"' in ai


def test_a_clicked_shot_is_named_apart_from_every_route_and_from_the_drawer():
    assert driver.click_shot_name("/settings", "add_asset", "390x844", "light") == "settings_add_asset__390x844__light.png"
    src = (ROOT / "app" / "src" / "App.tsx").read_text("utf-8")
    stems = {driver.slug(r) for r in driver.routes_from_app_tsx(src)} | {"root_drawer"}
    names = [f"{driver.slug(r)}_{name}" for r, shots in driver.CLICK_SHOTS.items() for name, _ in shots]
    assert names == ["settings_add_asset", "settings_ai_dialog"]
    assert not set(names) & stems and len(set(names)) == len(names)


def test_the_clicked_shot_opens_the_route_clicks_each_control_in_order_and_is_checked_as_a_route_is(tmp_path):
    ctx = FakeContext()
    assert driver.shoot_clicked(ctx, INGEST, BASE, "/settings", "open", ("#one", "#two"), None, tmp_path,
                                "390x844", "dark") is None
    assert ctx.log == [
        ("new_page",), ("clock", INGEST), ("goto", f"{BASE}/#/settings"),
        # in the table's order, after the page is ready, each bounded by the short click timeout
        ("click", "#one", driver.CLICK_TIMEOUT_MS), ("click", "#two", driver.CLICK_TIMEOUT_MS),
        # a click that moved focus scrolled the window: back to the top, so the sticky bar is drawn at the page's top
        ("scroll top",),
        ("fonts",), ("screenshot", "settings_open__390x844__dark.png", True),   # the whole page, as a route's
        ("overflow", True), ("close",)]
    late = FakeContext(fonts=False)
    entry = driver.shoot_clicked(late, INGEST, BASE, "/settings", "open", ("#one",), None, tmp_path, "1280x900", "light")
    assert entry is not None and entry["clicked"] == "open" and entry["route"] == "/settings"
    assert entry["load_problems"] == [{"kind": "fonts not ready", "path": ""}]


def test_a_control_that_is_not_there_fails_fast_as_a_problem_of_its_shot_and_the_run_goes_on(tmp_path):
    # a short timeout, never Playwright's 30 seconds
    assert 0 < driver.CLICK_TIMEOUT_MS <= 5000
    ctx = FakeContext(missing=("#gone",))
    entry = driver.shoot_clicked(ctx, INGEST, BASE, "/settings", "open", ("#gone", "#after"), None, tmp_path,
                                 "390x844", "light")
    # the failed click is the shot's load problem (overflow.json gets the entry, so the run exits 1), the clicks after it
    # are not tried, and the page is still shot and closed
    assert entry is not None and entry["clicked"] == "open"
    assert entry["load_problems"] == [{"kind": "click failed", "path": "#gone"}]
    steps = [s[0] for s in ctx.log]
    assert ("click", "#after", driver.CLICK_TIMEOUT_MS) not in ctx.log
    assert steps[-4:] == ["fonts", "screenshot", "overflow", "close"]
    # and the run goes on: a whole run with the control gone still shoots every route, the drawer and the clicked shots
    browser = FakeBrowser()
    browser.new_context = lambda **options: browser.contexts.append(  # type: ignore[method-assign]
        FakeContext(options=options, missing=tuple(s for _, sels in driver.CLICK_SHOTS["/settings"] for s in sels))) \
        or browser.contexts[-1]
    overflow = driver.shoot_all(browser, INGEST, "UTC", BASE, ["/", "/settings"], {}, None, tmp_path, STANDARD,
                                private=False)
    assert len(shots_of(browser)) == driver.expected_shots(["/", "/settings"], STANDARD)
    assert [e["clicked"] for e in overflow] == ["add_asset", "ai_dialog"] * 4
    assert all(e["load_problems"][0]["kind"] == "click failed" for e in overflow)


def test_a_run_shooting_settings_shoots_it_clicked_after_the_routes_and_before_the_drawer_and_counts_it(tmp_path):
    browser = FakeBrowser()
    assert driver.shoot_all(browser, INGEST, "UTC", BASE, ["/", "/settings"], {}, None, tmp_path, STANDARD,
                            private=False) == []
    names = shots_of(browser)
    assert len(names) == driver.expected_shots(["/", "/settings"], STANDARD) == 2 * 4 + 8 + 2
    assert [s[1] for s in browser.contexts[0].log if s[0] == "screenshot"] == [
        "root__390x844__dark.png", "settings__390x844__dark.png", "settings_add_asset__390x844__dark.png",
        "settings_ai_dialog__390x844__dark.png", "root_drawer__390x844__dark.png"]
    # every viewport and theme has each
    for shot in ("add_asset", "ai_dialog"):
        assert sorted(n for n in names if f"_{shot}__" in n) == sorted(
            f"settings_{shot}__{v}__{t}.png" for t in driver.THEMES for v in STANDARD)
    # a run that does not shoot Settings clicks nothing
    other = FakeBrowser()
    driver.shoot_all(other, INGEST, "UTC", BASE, ["/spending"], {}, None, tmp_path, STANDARD, private=False)
    assert not any(step[0] == "click" for c in other.contexts for step in c.log)
    assert driver.expected_shots(["/spending"], STANDARD) == 4


# ---------------------------------------------------------------- the page shown before any export exists

@pytest.mark.parametrize("raw,on", [(None, False), ("", False), ("  ", False), ("1", True), (" 1 ", True)])
def test_the_nothing_built_mode_is_on_for_1_alone(raw, on):
    assert driver.nothing_built_from_env(raw) is on


@pytest.mark.parametrize("bad", ["0", "yes", "true", "2", "/some/exports"])
def test_any_other_nothing_built_value_is_refused_by_name(bad):
    with pytest.raises(SystemExit) as exc:
        driver.nothing_built_from_env(bad)
    assert "FF_SHOT_NOTHING_BUILT" in str(exc.value) and repr(bad) in str(exc.value)


def test_the_export_files_are_the_ones_the_dashboard_loads():
    src = (ROOT / "app" / "src" / "data" / "types.ts").read_text("utf-8")
    block = src[src.index("export const EXPORT_FILES = ["):]
    block = block[:block.index("] as const;")]
    assert tuple(re.findall(r'"([a-z_]+\.json)"', block)) == driver.EXPORT_FILES
    assert len(driver.EXPORT_FILES) == 14 and driver.EXPORT_FILES[0] == "manifest.json"


def test_the_page_waited_for_is_the_first_run_page_with_nothing_built():
    """The heading the run waits for: FirstRun's frame carries its state, and the shell outside the routes mounts it
    in the "nothing built" one; the page has one h1."""
    first_run = (ROOT / "app" / "src" / "sections" / "FirstRun.tsx").read_text("utf-8")
    assert 'data-first-run={state}' in first_run and first_run.count("<h1") == 1
    assert '<FirstRun state="nothing-built"' in (ROOT / "app" / "src" / "App.tsx").read_text("utf-8")
    assert driver.NOTHING_BUILT_READY == '[data-first-run="nothing-built"] h1'


@pytest.mark.parametrize("problem,expected", [
    ({"kind": "http 404", "path": "/manifest.json"}, True),
    ({"kind": "http 404", "path": "/vendors.json"}, True),
    ({"kind": "http 404", "path": "/api/prefs"}, False),               # a store document the site must serve
    ({"kind": "http 404", "path": "/version.json"}, False),            # the build's own file
    ({"kind": "http 500", "path": "/manifest.json"}, False),           # a failure, not an absence
    ({"kind": "requestfailed", "path": "/manifest.json"}, False),
    ({"kind": "console error", "path": "/assets/index.js"}, False),
    ({"kind": "fonts not ready", "path": ""}, False),
])
def test_only_an_absent_export_is_the_pages_premise(problem, expected):
    assert driver.absent_export(problem) is expected


def test_the_page_is_opened_at_the_root_and_shot_once_its_heading_is_on_screen(tmp_path):
    ctx = FakeContext()
    assert driver.shoot_nothing_built(ctx, BASE, tmp_path, "390x844", "dark") is None
    assert ctx.log == [
        ("new_page",), ("clock", driver.NOTHING_BUILT_AT), ("goto", f"{BASE}/#/"),
        ("ready", driver.NOTHING_BUILT_READY),                       # in place of the data-ready signal
        ("fonts",), ("screenshot", "nothing_built__390x844__dark.png", True), ("overflow", True), ("close",)]
    # a fixed, aware instant: the shot never depends on the day it is taken
    assert driver.NOTHING_BUILT_AT.utcoffset() is not None


def test_an_absent_export_makes_no_entry_and_every_other_problem_does(tmp_path):
    absent = [{"kind": "http 404", "path": f"/{name}"} for name in driver.EXPORT_FILES]
    entry = {"route": driver.NOTHING_BUILT_ROUTE, "query": False, "viewport": "390x844", "theme": "dark"}
    assert driver._shoot(FakePage([]), tmp_path / "a.png", False, [], list(absent), entry,
                         expected=driver.absent_export) is None
    other = {"kind": "http 404", "path": "/api/answers"}
    got = driver._shoot(FakePage([]), tmp_path / "b.png", False, [], [*absent, other], entry, expected=driver.absent_export)
    assert got is not None and got["load_problems"] == [other] and got["route"] == "(nothing built)"
    # a route's shot keeps every 404 it meets
    assert driver._shoot(FakePage([]), tmp_path / "c.png", False, [], list(absent[:1]), entry)["load_problems"] == absent[:1]


def test_the_mode_shoots_the_one_page_at_every_viewport_in_both_themes_in_utc(tmp_path):
    browser = FakeBrowser()
    assert driver.shoot_nothing_built_all(browser, BASE, tmp_path, STANDARD, private=False) == []
    assert [c.options for c in browser.contexts] == [
        driver.context_options(w, h, "UTC") for _ in driver.THEMES for (w, h) in STANDARD.values()]
    names = shots_of(browser)
    assert names == [driver.nothing_built_name(v, t) for t in driver.THEMES for v in STANDARD]
    assert len(names) == driver.nothing_built_shots(STANDARD) == 4
    assert all(ctx.log[-1] == ("context closed",) for ctx in browser.contexts)
    # its PNGs are named apart from every route's
    src = (ROOT / "app" / "src" / "App.tsx").read_text("utf-8")
    assert "nothing_built" not in {driver.slug(r) for r in driver.routes_from_app_tsx(src)}


# ---------------------------------------------------------------- the browser's zone

@pytest.mark.parametrize("zone", ["America/New_York", "Pacific/Kiritimati", "UTC", "America/Argentina/Buenos_Aires",
                                  "Etc/GMT+5", "Europe/Isle_of_Man", "America/Port-au-Prince"])
def test_the_households_zone_is_assumptions_household_timezone(zone):
    assert driver.household_zone({"household": {"timezone": zone}, "owners": []}) == zone


@pytest.mark.parametrize("assumptions", [
    {}, {"household": None}, {"household": {}}, {"household": {"timezone": None}}, {"household": {"timezone": ""}},
    {"household": {"timezone": 5}}, {"household": {"timezone": "New York"}}, {"household": {"timezone": "../x"}},
    {"household": {"timezone": "America/New_York\n"}}, {"household": {"timezone": "/UTC"}},
    {"household": {"timezone": "America//New_York"}}, {"household": {"timezone": "A" * 65}},
    {"household": {"timezone": "America/New_York'"}}, [], "not assumptions", None,
])
def test_an_absent_or_malformed_zone_is_none(assumptions):
    assert driver.household_zone(assumptions) is None


def serve(monkeypatch, files: dict[str, object]) -> list[str]:
    """Stands in for the site: each name in `files` answers its JSON, any other a 404."""
    asked: list[str] = []

    def urlopen(url, timeout):
        asked.append(url)
        name = url.rsplit("/", 1)[1]
        if name not in files:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return io.BytesIO(json.dumps(files[name]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return asked


def test_the_served_assumptions_give_the_zone_and_a_site_without_them_none(monkeypatch):
    asked = serve(monkeypatch, {"assumptions.json": {"household": {"timezone": "America/New_York"}}})
    assert driver.served_zone(BASE) == "America/New_York"
    assert asked == [f"{BASE}/assumptions.json"]
    serve(monkeypatch, {})
    assert driver.served_zone(BASE) is None


def test_the_zone_of_an_exports_file_and_of_one_that_is_absent_or_not_json(tmp_path):
    f = tmp_path / "assumptions.json"
    assert driver.zone_of_file(f) is None
    f.write_text("{ not json")
    assert driver.zone_of_file(f) is None
    f.write_text(json.dumps({"household": {"timezone": "Pacific/Kiritimati"}}))
    assert driver.zone_of_file(f) == "Pacific/Kiritimati"


def test_a_zone_playwright_accepts_is_kept(capsys):
    browser = FakeBrowser()
    assert driver.accepted_zone(browser, "America/New_York", private=False) == "America/New_York"
    assert "America/New_York" in capsys.readouterr().out
    # the probe: a context in the zone, a page made in it (where Chromium refuses an unknown zone), both closed
    (probe,) = browser.contexts
    assert probe.options == {"timezone_id": "America/New_York"}
    assert probe.log == [("new_page",), ("close",), ("context closed",)]


def test_a_zone_refused_when_the_page_is_made_falls_back_to_utc_and_closes_the_probe(capsys):
    browser = FakeBrowser(refuses=("Mars/Olympus_Mons",))
    assert driver.accepted_zone(browser, "Mars/Olympus_Mons", private=False) == "UTC"
    (probe,) = browser.contexts
    assert probe.log == [("context closed",)]                        # no page was made, and the context is not left open
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and "refuses" in lines[0] and "UTC" in lines[0]
    # the run goes on in UTC: every context of the shots is made in it, and its pages are made
    driver.shoot_all(browser, INGEST, "UTC", BASE, ["/spending"], {}, None, Path("/nonexistent"), STANDARD, private=False)
    assert all(c.options["timezone_id"] == "UTC" for c in browser.contexts[1:]) and len(shots_of(browser)) == 4


def test_a_masked_run_does_not_print_the_zone(capsys):
    assert driver.accepted_zone(FakeBrowser(), "America/New_York", private=True) == "America/New_York"
    out = capsys.readouterr().out
    assert "household's zone" in out and "America/New_York" not in out


@pytest.mark.parametrize("zone,words", [(None, "states no usable household.timezone"),
                                        ("Mars/Olympus_Mons", "Playwright refuses")])
def test_no_zone_or_one_playwright_refuses_is_utc_with_one_line(capsys, zone, words):
    browser = FakeBrowser(refuses=("Mars/Olympus_Mons",))
    assert driver.accepted_zone(browser, zone, private=False) == "UTC"
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and words in lines[0] and "UTC" in lines[0]


# ---------------------------------------------------------------- the store documents screenshots.sh serves

FIXTURES = HERE / "fixtures"


def load_store():
    """deploy/scenario_store.py, loaded by path as its own tests do (it imports refresh_state from deploy/)."""
    sys.path.insert(0, str(ROOT / "deploy"))
    spec = importlib.util.spec_from_file_location("scenario_store_for_shots", ROOT / "deploy" / "scenario_store.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("fixture,empty", [("prefs.empty.json", "empty_prefs"), ("answers.empty.json", "empty_answers"),
                                           ("manual-assets.empty.json", "empty_manual_assets")])
def test_each_empty_document_is_the_one_the_store_serves_before_anything_is_saved(fixture, empty):
    assert json.loads((FIXTURES / fixture).read_text("utf-8")) == getattr(load_store(), empty)()


def test_the_idle_refresh_status_is_what_the_store_answers_for_a_stack_that_has_not_refreshed(tmp_path):
    """Every key GET /api/refresh carries, for a refresh dir holding only the schedule the scheduler writes there (no
    run, no request, no ledger, no marker): the default hours in the household's zone; the fixture's zone is the one
    screenshots.sh fills in."""
    store = load_store()
    # deploy/, on the path load_store put it on
    import refresh_state as rs

    rs.write_schedule(tmp_path, default_schedule(rs, "America/New_York"))
    before = sorted(p.name for p in tmp_path.iterdir())
    handler = SimpleNamespace(refresh_dir=tmp_path, clock=lambda: INGEST.timestamp(),
                              _lock_holder=store.ScenarioHandler._lock_holder)
    view = store.ScenarioHandler._refresh_view(handler)
    template = json.loads((FIXTURES / "refresh.idle.json").read_text("utf-8"))
    assert driver.refresh_status(template, "America/New_York") == view
    assert view["state"] == "idle" and view["can_refresh"] is True and view["ingest"]["wanted"] is False
    assert sorted(p.name for p in tmp_path.iterdir()) == before == [rs.SCHEDULE_FILE]   # the view only reads


def default_schedule(rs, zone: str):
    """The schedule the scheduler writes for a household.yaml that states no `refresh:` (deploy/make_crontab.py)."""
    from etl import validate
    return rs.Schedule(zone, tuple(validate.DEFAULT_REFRESH_HOURS), validate.DEFAULT_REFRESH_MINUTE)


def test_the_idle_status_schedule_is_the_default_one():
    """The fixture's times are those of a household that states no hours of its own."""
    load_store()
    import refresh_state as rs
    template = json.loads((FIXTURES / "refresh.idle.json").read_text("utf-8"))
    assert template["schedule"]["times"] == default_schedule(rs, "UTC").times() == ["01:00", "09:00", "17:00"]


def test_the_refresh_status_takes_the_zone_and_leaves_the_template_alone():
    template = json.loads((FIXTURES / "refresh.idle.json").read_text("utf-8"))
    assert template["schedule"]["timezone"] == driver.ZONE_PLACEHOLDER
    doc = driver.refresh_status(template, "Pacific/Kiritimati")
    assert doc["schedule"]["timezone"] == "Pacific/Kiritimati"
    assert template["schedule"]["timezone"] == driver.ZONE_PLACEHOLDER
    with pytest.raises(SystemExit, match="placeholder"):
        driver.refresh_status({**template, "schedule": {"times": [], "timezone": "UTC"}}, "UTC")


def test_the_site_gets_the_five_documents_from_the_fixtures_without_a_state(tmp_path):
    api = tmp_path / "api"
    api.mkdir()
    driver.write_api(api, FIXTURES, "America/New_York", None)
    assert sorted(p.name for p in api.iterdir()) == sorted(driver.API_DOCUMENTS)
    store = load_store()
    assert json.loads((api / "prefs").read_text()) == store.empty_prefs()
    assert json.loads((api / "answers").read_text()) == store.empty_answers()
    assert json.loads((api / "manual-assets").read_text()) == store.empty_manual_assets()
    assert (api / "scenarios").read_bytes() == (FIXTURES / "scenarios.synthetic.json").read_bytes()
    assert json.loads((api / "refresh").read_text())["schedule"]["timezone"] == "America/New_York"
    assert not any(p.is_symlink() for p in api.iterdir())


def state_dir(tmp_path: Path, **docs: object) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    for name, doc in docs.items():
        (d / f"{name}.json").write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return d


# one document of each kind the store takes (validate_answers, validate_manual_assets)
STAMP = "2026-10-30T12:00:00.000Z"
ANSWERS = {"version": 1, "updated_at": STAMP, "answers": [{"key": "txn:sample", "scope": "txn", "txn_id": "sample",
                                                           "answered_at": STAMP, "set": {"category": "Groceries"}}]}
MANUAL = {"version": 1, "updated_at": STAMP, "assets": [{
    "id": "sample-car", "name": "Car", "owner": "Joint", "asset_class": "Vehicle", "liquid": False, "updated_at": STAMP,
    "values": [{"as_of": "2026-10-01", "value": 12000}]}]}


def test_a_households_state_gives_its_answers_and_manual_assets_copied_not_linked(tmp_path):
    state = state_dir(tmp_path, answers=ANSWERS, manual_assets=MANUAL)
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(MANUAL))
    (state / "manual_assets.json").unlink()
    (state / "manual_assets.json").symlink_to(target)
    api = tmp_path / "api"
    api.mkdir()
    driver.write_api(api, FIXTURES, "UTC", state)
    assert (api / "answers").read_bytes() == (state / "answers.json").read_bytes()
    assert (api / "manual-assets").read_bytes() == target.read_bytes() and not (api / "manual-assets").is_symlink()


@pytest.mark.parametrize("present", ["answers", "manual_assets"])
def test_a_state_document_the_household_lacks_is_served_empty(tmp_path, present):
    state = state_dir(tmp_path, **{present: ANSWERS if present == "answers" else MANUAL})
    api = tmp_path / "api"
    api.mkdir()
    driver.write_api(api, FIXTURES, "UTC", state)
    store = load_store()
    answers, manual = json.loads((api / "answers").read_text()), json.loads((api / "manual-assets").read_text())
    assert (answers, manual) == ((ANSWERS, store.empty_manual_assets()) if present == "answers"
                                 else (store.empty_answers(), MANUAL))


def test_a_state_without_either_document_or_with_good_ones_has_no_problem(tmp_path):
    assert driver.state_problem(state_dir(tmp_path)) is None
    (tmp_path / "state" / "answers.json").write_text(json.dumps(ANSWERS))
    (tmp_path / "state" / "manual_assets.json").write_text(json.dumps(MANUAL))
    assert driver.state_problem(tmp_path / "state") is None


def test_each_state_document_is_checked_by_the_stores_own_validator():
    store = load_store()
    assert {f: v for f, v in driver.STATE_DOCUMENTS.values()} == {
        "answers.json": "validate_answers", "manual_assets.json": "validate_manual_assets"}
    assert driver.state_files() == ["answers.json", "manual_assets.json"]
    # screenshots.sh takes the names from here: no code line of it spells one (a comment may)
    code = [line for line in (HERE / "screenshots.sh").read_text("utf-8").splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code if "answers.json" in line or "manual_assets.json" in line]
    for _, check in driver.STATE_DOCUMENTS.values():
        assert callable(getattr(store, check))
    # and the documents the cases below start from are ones the store takes
    store.validate_answers(json.dumps(ANSWERS).encode())
    store.validate_manual_assets(json.dumps(MANUAL).encode())


# what the store refuses, each as one document's text: the envelope (JSON, an object, version 1, a string stamp, its
# list), a number JSON cannot carry, and what only the manual assets' check knows (a stamp that is no UTC time, an asset
# with no value)
BAD_STATE = {
    "answers": [
        "{ not json", "[]", '{"version": 2, "updated_at": "x", "answers": []}', '{"version": 1, "answers": []}',
        '{"version": 1, "updated_at": "x"}', '{"version": 1, "updated_at": "x", "answers": {}}',
        '{"version": 1, "updated_at": "x", "answers": [], "note": NaN}',
        json.dumps({**ANSWERS, "answers": [{**ANSWERS["answers"][0], "scope": "everyone"}]}),
    ],
    "manual_assets": [
        "{ not json", "[]", '{"version": 2, "updated_at": "x", "assets": []}', '{"version": 1, "assets": []}',
        json.dumps({**MANUAL, "assets": {}}), json.dumps({**MANUAL, "updated_at": "x"}),
        json.dumps(MANUAL).replace("12000", "NaN"), json.dumps({**MANUAL, "assets": [{**MANUAL["assets"][0], "values": []}]}),
    ],
}


@pytest.mark.parametrize("name,content", [(n, c) for n, cs in BAD_STATE.items() for c in cs])
def test_a_state_document_the_store_refuses_is_refused_with_the_stores_own_reason(tmp_path, name, content):
    state = state_dir(tmp_path, **{name: content})
    with pytest.raises(ValueError) as refused:
        getattr(load_store(), "validate_answers" if name == "answers" else "validate_manual_assets")(content.encode())
    msg = driver.state_problem(state)
    assert msg is not None and f"{name}.json" in msg and str(refused.value) in msg


# ---------------------------------------------------------------- the scenarios fixture

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "scenarios.synthetic.json"
# the seventeen knobs the second-house worksheet reads, `SecondHouseInputs` in app/src/lib/secondHouse.ts: the app's
# store fills a key it does not find from its defaults, so a misspelled key would be dropped without a word
SECOND_HOUSE_INPUTS = {
    "price", "downPct", "ratePct", "closingCosts", "postMoveInCosts", "taxPctYr", "insuranceYr", "hoaMo", "utilitiesMo",
    "maintPctYr", "nightlyRate", "occupancyPct", "nightsPerMonth", "platformFeePct", "mgmtFeePct", "cleaningPerStay",
    "staysPerMonth",
}


def test_the_scenarios_fixture_names_only_demo_accounts():
    doc = json.loads(FIXTURE.read_text())
    assert doc["version"] == 1                                       # the only version the app's store parses
    scenarios = doc["scenarios"]
    assert 2 <= len(scenarios) <= 5                                  # the app keeps at most five
    assert len({s["id"] for s in scenarios}) == len({s["name"] for s in scenarios}) == len(scenarios)
    children = {m.name for m in household.MEMBERS if m.role == "child"}
    for s in scenarios:
        assert set(s["inputs"]) == SECOND_HOUSE_INPUTS, f"{s['name']}: inputs are not the worksheet's seventeen knobs"
        assert all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in s["inputs"].values())
        assert s["draws"], f"{s['name']} draws from nothing"
        for account_id, amount in s["draws"].items():
            assert account_id in household.ACCOUNTS, f"{s['name']}: {account_id} is not a demo account"
            # money a household would move to buy a house: its checking, savings and brokerage, never a card, a
            # loan, the 401(k) or a child's savings
            a = household.ACCOUNTS[account_id]
            assert a.kind in ("checking", "savings", "brokerage"), f"{s['name']}: {account_id} is a {a.kind}"
            assert a.asset_class != "Retirement" and a.owner not in children, f"{s['name']}: {account_id}"
            assert isinstance(amount, int) and amount > 0
    # the page opens on the first scenario, a funded plan; the second's checking draw is the overdraw every run shows
    assert household.CHECKING in scenarios[1]["draws"]

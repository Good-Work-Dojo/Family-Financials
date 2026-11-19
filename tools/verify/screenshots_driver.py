#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Playwright driver for tools/verify/screenshots.sh - runs INSIDE the playwright image.

Parses the PAGES manifest out of app/src/App.tsx (every `to:` page and each hub's `path:`
tabs and extras; a `:id` extra is screenshotted with the first account id in the served
accounts.json that has transactions, else the first account, else `none`, under the pattern's name). FF_SHOT_ROUTES (space-
separated) or --routes overrides that list for a quick subset, but every entry must be one of
the parsed routes, spelled exactly as the PAGES pattern (`/accounts/:id`, not a real id): a
REDIRECTS source, a typo, a trailing slash or query (`/mortgage/`, `/spending?x=1`) or a repeat
is refused with exit 2, never shot.
FF_SHOT_QUERY="<route>?<query> [...]" shoots a route WITH a query (`/spending/reports?measure=count&chart=table`,
`/spending/transactions?categories=Groceries&from=2026-03-01`): the route part follows the same exact-pattern rule
(a REDIRECTS source is still refused), each route takes at most one query, it must be a route the run shoots, and
the PNG gets a `_query` suffix on the route's stem - never the query text itself. The gate never sets it.
Opens every route in a fresh page at 390x844 and 1280x900 (FF_SHOT_VIEWPORTS="WxH [WxH...]"
overrides the pair for one run; the gate never sets it) in dark and light (light =
localStorage.theme set before the page's own inline script reads it; FF_SHOT_PRIVATE=1 also sets
localStorage['ff.privacy'] so the whole run is masked - the gate never sets that either),
screenshots the full page at the exports' ingest instant (every page's `Date` is fixed, before it loads, at the served
manifest.json `generated_at`, timers running), so a shot does not depend on the day it is taken - a manifest without a
usable `generated_at` stops the run. The browser runs in the household's zone, the served assumptions.json
`household.timezone` (UTC, with one line saying so, when the key is absent, malformed or a zone Playwright refuses: the
probe makes a page in it, where Chromium refuses an unknown one), so
a clock time or "today" reads as it does in the household. Each shot waits for `document.fonts.ready` first, for at
most FONTS_TIMEOUT_MS. At each phone viewport (narrower than the app's 768px cut) in each theme, after the routes and
when the run shoots Home, the driver opens Home, opens the navigation drawer by its menu button's accessible name and
shoots the screen with the drawer open (`root_drawer__<WxH>__<theme>.png`). A route CLICK_SHOTS names is shot once
more for each of its clicked shots at each viewport in each theme after their controls are clicked, in order (Settings
with the "Add an asset" form open, `settings_add_asset__<WxH>__<theme>.png`, and with the AI categorization dialog
open, `settings_ai_dialog__<WxH>__<theme>.png`): the one place the driver clicks before a route's shot; a control
not there and enabled within CLICK_TIMEOUT_MS is a "click failed" problem of that shot, and the run goes on. So a run shoots
(routes + clicked shots) x viewports x themes PNGs plus one per phone viewport and theme (expected_shots).
It writes overflow.json: one entry per shot (a route, a clicked shot or the open drawer, at a viewport in a theme) whose document
scrolls horizontally or has an element reaching past the viewport - ignoring elements inside overflow-x:auto|scroll|
hidden ancestors and, in a route's shot, the off-canvas drawer (<aside>), which the open drawer's shot checks - plus
any uncaught page errors and any "load problem": a
response with status >= 400, a failed request, a console `error` message (a page that "rendered" a handled load error
would otherwise screenshot as clean) or fonts that were not ready in time (`fonts not ready`). `/api/*` is no
exception: screenshots.sh serves the five store documents the app reads there (write_api), so a request the static
site cannot answer is a load problem like any other.
Empty list = clean. screenshots.sh turns every entry into a non-zero exit.
FF_SHOT_NOTHING_BUILT=1 shoots, instead of the routes, the page a household sees before any export exists (the site
serves no export): the dashboard's root at each viewport in both themes, once that page's heading is on screen
(NOTHING_BUILT_READY, in place of the data-ready signal), its clock fixed at NOTHING_BUILT_AT in UTC
(nothing_built_name PNGs, nothing_built_shots of them); a 404 for one of the EXPORT_FILES is the page's premise, never an
entry, and every other problem is one as above.

playwright is imported inside main(), so the host can import this module for routes_from_app_tsx() and
expected_shots() (screenshots.sh uses them to know the expected PNG count) and for the documents it serves (the
state documents' names, STATE_DOCUMENTS, and their check, state_problem, which loads deploy/scenario_store.py).
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

DEFAULT_VIEWPORTS = {"390x844": (390, 844), "1280x900": (1280, 900)}
THEMES = ("dark", "light")
# Below the app's phone/desktop cut (Tailwind's md, 768px) the sidebar is an off-canvas drawer behind the top bar's
# menu button: a viewport narrower than this is a phone, and its shots include the open drawer.
PHONE_CUT = 768


def viewports_from_env(raw: str | None) -> dict[str, tuple[int, int]]:
    """FF_SHOT_VIEWPORTS="360x780 1100x800" -> {"360x780": (360, 780), ...}; unset or blank -> the two
    standard viewports (the gate never sets it; a carry that needs a tablet or a 360px shot does)."""
    tokens = (raw or "").split()
    if not tokens:
        return dict(DEFAULT_VIEWPORTS)
    out: dict[str, tuple[int, int]] = {}
    for tok in tokens:
        m = re.fullmatch(r"(\d{3,4})x(\d{3,4})", tok)
        if not m:
            raise SystemExit(f"FF_SHOT_VIEWPORTS: {tok!r} is not <width>x<height>")
        out[tok] = (int(m.group(1)), int(m.group(2)))
    return out


VIEWPORTS = viewports_from_env(os.environ.get("FF_SHOT_VIEWPORTS"))


def private_from_env(raw: str | None) -> bool:
    """FF_SHOT_PRIVATE=1 shoots with privacy mode ON (every dollar amount masked, charts blurred). screenshots.sh forces
    it unless FF_DEMO_EXPORTS=1 marks the exports synthetic, so PNGs of real exports are masked at rest (and never
    opened). Unset, blank or 0 is the unmasked run; anything else is refused rather than guessed - a typo like `true`
    must not quietly produce an unmasked set of "private" PNGs."""
    v = (raw or "").strip()
    if v in ("", "0"):
        return False
    if v == "1":
        return True
    raise SystemExit(f"FF_SHOT_PRIVATE: {raw!r} is not 1 (masked run) or 0 / unset (normal run)")


PRIVATE = private_from_env(os.environ.get("FF_SHOT_PRIVATE"))


def nothing_built_from_env(raw: str | None) -> bool:
    """FF_SHOT_NOTHING_BUILT=1 shoots the page a household sees before any export exists (screenshots.sh serves the
    built dashboard with no export), instead of the routes. Unset or blank is the usual run; anything else is refused
    rather than guessed."""
    v = (raw or "").strip()
    if v == "":
        return False
    if v == "1":
        return True
    raise SystemExit(f"FF_SHOT_NOTHING_BUILT: {raw!r} is not 1 (the page shown before any export exists) or unset")


NOTHING_BUILT = nothing_built_from_env(os.environ.get("FF_SHOT_NOTHING_BUILT"))


def context_options(w: int, h: int, zone: str) -> dict:
    """One browser context per theme x viewport, in the household's zone (accepted_zone). `reduced_motion="reduce"`:
    EChart turns chart animation off under prefers-reduced-motion, so a chart is shot in its final state, never
    half-drawn (a light-theme line once was)."""
    return {"viewport": {"width": w, "height": h}, "device_scale_factor": 1, "reduced_motion": "reduce",
            "timezone_id": zone}


def drawer_viewports(viewports: dict[str, tuple[int, int]]) -> list[str]:
    """The viewports, in their order, at which the navigation drawer is shot open: the phones (narrower than
    PHONE_CUT)."""
    return [name for name, (w, _) in viewports.items() if w < PHONE_CUT]


# The shots a route needs a control clicked for, which no URL can open: route -> its clicked shots, each (the PNG's
# name inside its stem, the controls to click in order, each a selector Playwright takes). A run that shoots the route
# also shoots each of them, at every viewport in both themes: Settings with the manual-assets editor's "Add an asset"
# form open, and with the AI categorization card's dialog of what would be sent open (neither is otherwise pictured).
# The one place clicks are made before a route's shot.
CLICK_SHOTS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "/settings": (("add_asset", ('[data-testid="manual-assets-add"]',)),
                  ("ai_dialog", ('[data-testid="ai-open-dialog"]',))),
}


# what a clicked shot runs once its clicks are made: the window back at the top of the page
SCROLL_TOP = "() => window.scrollTo(0, 0)"
# how long a clicked shot waits for its control to be there and enabled before the click counts as failed (the page is
# loaded by then: open_page waited for it)
CLICK_TIMEOUT_MS = 3000


def click_shots(routes: list[str]) -> list[tuple[str, str, tuple[str, ...]]]:
    """The clicked shots a run of `routes` takes, in route order and each route's in the table's: (route, name,
    selectors)."""
    return [(r, name, selectors) for r in routes for name, selectors in CLICK_SHOTS.get(r, ())]


def expected_shots(routes: list[str], viewports: dict[str, tuple[int, int]]) -> int:
    """The PNGs a run writes: each route at each viewport in each theme, each clicked shot of those routes (CLICK_SHOTS)
    likewise, plus, when the run shoots Home, the open drawer at each phone viewport in each theme (with today's 19
    routes and the two standard viewports, 76 + 8 + 2)."""
    drawers = len(drawer_viewports(viewports)) * len(THEMES) if "/" in routes else 0
    return (len(routes) + len(click_shots(routes))) * len(viewports) * len(THEMES) + drawers


def nothing_built_shots(viewports: dict[str, tuple[int, int]]) -> int:
    """The PNGs a FF_SHOT_NOTHING_BUILT=1 run writes: the one page at each viewport in each theme (it has no shell, so
    no drawer)."""
    return len(viewports) * len(THEMES)


# ---------------------------------------------------------------- the browser's zone

# an IANA zone id: an area and its locations, each part letters, digits, `_`, `+` or `-` (America/Port-au-Prince,
# Etc/GMT+5), the first starting with a letter; anything else is not passed to the browser
_ZONE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)*")
MAX_ZONE_LEN = 64


def household_zone(assumptions: object) -> str | None:
    """assumptions.json `household.timezone` when it is a zone id (_ZONE, at most MAX_ZONE_LEN characters), else
    None: absent, not a string, or not shaped like one."""
    household = assumptions.get("household") if isinstance(assumptions, dict) else None
    zone = household.get("timezone") if isinstance(household, dict) else None
    if isinstance(zone, str) and len(zone) <= MAX_ZONE_LEN and _ZONE.fullmatch(zone):
        return zone
    return None


def zone_of_file(path: Path) -> str | None:
    """household_zone of an assumptions.json on disk; None when the file is absent or not JSON (screenshots.sh names
    the zone of the refresh schedule it serves with it)."""
    try:
        return household_zone(json.loads(path.read_text("utf-8")))
    except (OSError, ValueError):
        return None


def served_zone(base: str) -> str | None:
    """household_zone of the assumptions.json the site serves; None when it serves none or one that is not JSON."""
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(f"{base}/assumptions.json", timeout=10) as r:
            return household_zone(json.load(r))
    except (urllib.error.HTTPError, ValueError):
        return None


def accepted_zone(browser, zone: str | None, private: bool) -> str:
    """The zone every context runs in: `zone` when Playwright takes it, else UTC, with one line saying which. The probe
    makes a context AND a page with the zone and closes both: Chromium takes any well-formed id when the context is
    made and refuses one it does not know only when a page is. A masked run does not print the zone it runs in (it
    belongs to a household)."""
    if zone is None:
        print("assumptions.json states no usable household.timezone: the browser runs in UTC")
        return "UTC"
    named = "" if private else f" {zone}"
    try:
        probe = browser.new_context(timezone_id=zone)
        try:
            probe.new_page().close()
        finally:
            probe.close()
    except Exception:  # Playwright's own error for a zone id it does not know
        print(f"Playwright refuses the household's zone{named}: the browser runs in UTC")
        return "UTC"
    print(f"the browser runs in the household's zone{named} (assumptions.json household.timezone)")
    return zone


def init_script(theme: str, private: bool) -> str:
    """What every page runs before its own scripts: the theme (index.html's inline script reads it before first paint)
    and, for a masked run, the privacy flag (lib/privacy.ts reads it when the app's module loads). Plain JS with no
    template placeholders left in it - the flag is written or not, never `if ($true)`."""
    sets = [f"localStorage.setItem('theme', '{theme}');"]
    if private:
        sets.append("localStorage.setItem('ff.privacy', '1');")
    return "try { " + " ".join(sets) + " } catch (e) {}"

# loading placeholders: the Skeleton components carry aria-busy; older builds print text
STILL_LOADING = ("() => !!document.querySelector('[aria-busy=\"true\"]')"
                 " || /Loading(\\u2026| data)/.test(document.body.innerText)")

# document.fonts.ready, raced against a timer so a font that never loads cannot hang the run: true when the fonts were
# ready in time, false after FONTS_TIMEOUT_MS (the shot is then taken and its entry says `fonts not ready`)
FONTS_TIMEOUT_MS = 5000
FONTS_READY = ("() => Promise.race([document.fonts.ready.then(() => true),"
               f" new Promise((done) => setTimeout(() => done(false), {FONTS_TIMEOUT_MS}))])")

# the phone's menu button (components/MobileTopBar.tsx), found by its accessible name, and the drawer it opens: the
# app's one <aside>, open once it has slid in to the screen's left edge (it sits off-canvas, a negative left, while
# closed and while it slides)
DRAWER_BUTTON = "Open menu"
DRAWER_OPEN = ("() => { const a = document.querySelector('aside');"
               " return !!a && Math.abs(a.getBoundingClientRect().left) < 0.5; }")

# The overflow check. Its one argument says whether to leave the navigation drawer (the app's one <aside>) out: a
# route's shot does, since the drawer waits off-canvas by design; the open drawer's shot does not, since the drawer is
# then what the shot shows.
OVERFLOW_JS = """
(skipDrawer) => {
  const doc = document.documentElement;
  const out = { page: doc.scrollWidth > doc.clientWidth + 1, offenders: [] };
  const clipped = (el) => {
    for (let p = el.parentElement; p; p = p.parentElement) {
      const ox = getComputedStyle(p).overflowX;
      if (ox === 'auto' || ox === 'scroll' || ox === 'hidden') return true;
    }
    return false;
  };
  const sel = (el) => {
    const id = el.id ? '#' + el.id : '';
    const cls = typeof el.className === 'string' && el.className.trim()
      ? '.' + el.className.trim().split(/\\s+/).slice(0, 3).join('.') : '';
    return el.tagName.toLowerCase() + id + cls;
  };
  for (const el of document.body.querySelectorAll('*')) {
    if (skipDrawer && el.closest('aside')) continue;   // the closed drawer lives off-canvas by design
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (r.right > doc.clientWidth + 1 && !clipped(el)) out.offenders.push(sel(el));
    if (out.offenders.length >= 10) break;
  }
  return out;
}
"""


def routes_from_app_tsx(src: str) -> list[str]:
    """Every `{ to: "/x", ... }` entry of `const PAGES`, plus `<to>/<path>` for each hub tab."""
    start = src.index("const PAGES")
    block = src[start:src.index("];", start)]
    routes: list[str] = []
    for m in re.finditer(r'\{\s*to:\s*"([^"]+)"(.*?)(?=\n\s*\{\s*to:|\Z)', block, re.S):
        to, body = m.group(1), m.group(2)
        routes.append(to)
        for t in re.finditer(r'path:\s*"([^"]+)"', body):
            routes.append(f"{to.rstrip('/')}/{t.group(1)}")
    return routes


def redirect_sources_from_app_tsx(src: str) -> list[str]:
    """Every `from` of `export const REDIRECTS = [["/from", "/to"], ...]` - never screenshotted."""
    start = src.find("const REDIRECTS")
    if start < 0:
        return []
    block = src[start:src.index("];", start)]
    return re.findall(r'\[\s*"([^"]+)"\s*,\s*"[^"]+"\s*\]', block)


def refused_routes(requested: list[str], src: str) -> str | None:
    """The refusal message when `requested` is not a duplicate-free subset of the parsed PAGES
    routes (exact string match, so `/mortgage/`, `/spending?x=1` and a real account id in place
    of `:id` all miss), else None. screenshots.sh runs the same check on the host before it
    builds the SPA, so a typo fails in a second instead of after the build."""
    known = routes_from_app_tsx(src)
    unknown = [r for r in requested if r not in known]
    repeated = sorted({r for r in requested if requested.count(r) > 1})
    if not unknown and not repeated:
        return None
    parts: list[str] = []
    if unknown:
        parts.append(f"not routes of the PAGES manifest: {unknown}")
        redirected = [r for r in unknown if r in set(redirect_sources_from_app_tsx(src))]
        if redirected:
            parts.append(f"{redirected} are REDIRECTS sources, which are never screenshotted")
    if repeated:
        parts.append(f"listed more than once: {repeated}")
    return ("refusing: " + "; ".join(parts)
            + "\nvalid routes (exact patterns): " + " ".join(known))


def queries_from_env(raw: str | None, src: str, shot: list[str]) -> dict[str, str]:
    """FF_SHOT_QUERY="/spending/reports?measure=count&chart=table /spending?categories=Dining" -> {route: query}.
    Unset or blank -> {} (the gate). Each entry is `<route>?<query>`: the route must be one of the PAGES patterns,
    spelled exactly (a REDIRECTS source is refused by name, as FF_SHOT_ROUTES does) and one this run shoots; the
    query must be a non-empty, well-formed `k=v&k=v` string with no `#`; a route takes one query. Anything else
    is refused rather than shot without its query."""
    out: dict[str, str] = {}
    known = routes_from_app_tsx(src)
    redirects = set(redirect_sources_from_app_tsx(src))
    for tok in (raw or "").split():
        route, sep, query = tok.partition("?")
        why = None
        if not sep or not query:
            why = "is not <route>?<query>"
        elif route not in known:
            why = ("names a REDIRECTS source, which is never screenshotted" if route in redirects
                   else "names no route of the PAGES manifest (exact patterns, e.g. /accounts/:id)")
        elif route not in shot:
            why = "names a route this run does not shoot (see FF_SHOT_ROUTES)"
        elif route in out:
            why = "repeats a route (one query per route)"
        elif "#" in query:
            why = "carries a '#'"
        else:
            try:
                parse_qsl(query, keep_blank_values=True, strict_parsing=True)
            except ValueError:
                why = "is not a well-formed k=v&k=v query"
        if why:
            raise SystemExit(f"FF_SHOT_QUERY: {tok!r} {why}")
        out[route] = query
    return out


def shot_name(route: str, query: str | None, vname: str, theme: str) -> str:
    """The PNG file name: the route pattern's stem, `_query` when the run shot it with a query (the query text
    never reaches a file name - it may hold a vendor or a date range), the viewport and the theme."""
    return f"{slug(route)}{'_query' if query else ''}__{vname}__{theme}.png"


def drawer_name(vname: str, theme: str) -> str:
    """The open drawer's PNG: Home's stem with `_drawer`, which no route's stem is."""
    return f"{slug('/')}_drawer__{vname}__{theme}.png"


def click_shot_name(route: str, name: str, vname: str, theme: str) -> str:
    """A clicked shot's PNG: the route's stem with `_<name>` (CLICK_SHOTS), which no route's stem is."""
    return f"{slug(route)}_{name}__{vname}__{theme}.png"


def shot_url(base: str, resolved: str, query: str | None) -> str:
    """HashRouter URL of a resolved route, with its query after the hash path when there is one."""
    return f"{base}/#{resolved}" + (f"?{query}" if query else "")


# the id `/accounts/:id` is shot with when the household has no account: the app answers it with its unknown-account card
NO_ACCOUNT = "none"


def resolve_param(route: str, base: str, account_id: str | None) -> str:
    """`/accounts/:id` -> an account id: --account-id, else from the served accounts.json the first account that has
    transactions (a feed-only account has an empty ledger), else the first account at all, else NO_ACCOUNT, so a
    household with no account still shoots the route (the unknown-account card)."""
    if ":id" not in route:
        return route
    if account_id is None:
        import urllib.request
        with urllib.request.urlopen(f"{base}/accounts.json", timeout=10) as r:
            accounts = json.load(r)["accounts"]
        account_id = next((a["id"] for a in accounts if a.get("txn_data_end") is not None),
                          accounts[0]["id"] if accounts else NO_ACCOUNT)
    return route.replace(":id", account_id)


def ingest_instant(manifest: object, where: str) -> datetime:
    """The instant every page is shot at: manifest.json `generated_at`, the ingest time, an ISO time with its UTC offset
    (`Z` included). A manifest (`where` names it) without one that parses - no key, not a string, no offset, not a time -
    stops the run naming the file and the key: falling back to the real clock would make the shots depend on the day
    they are taken."""
    raw = manifest.get("generated_at") if isinstance(manifest, dict) else None
    why = None
    if not isinstance(raw, str) or not raw:
        why = "is missing or not a string"
    else:
        try:
            # Python before 3.11 reads no `Z`
            at = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
        except ValueError:
            why = "is not an ISO time"
        else:
            if at.utcoffset() is None:
                why = "has no UTC offset"
    if why:
        raise SystemExit(f"{where}: generated_at {why} (got {str(raw)[:40]!r}); the shots are taken at the exports' "
                         "ingest instant, so it is required")
    return at


def served_ingest_instant(base: str) -> datetime:
    """ingest_instant of the manifest.json the site serves."""
    import urllib.request
    with urllib.request.urlopen(f"{base}/manifest.json", timeout=10) as r:
        return ingest_instant(json.load(r), f"{base}/manifest.json")


def url_path(url: str) -> str:
    return urlsplit(url).path or "/"


def watch_load_problems(page, problems: list[dict]) -> None:
    """Record failed/4xx+ requests (the store documents at /api/* included: the site serves them) and console errors
    on `page` into `problems`."""
    def on_response(r) -> None:
        if r.status >= 400:
            problems.append({"kind": f"http {r.status}", "path": url_path(r.url)})

    def on_failed(req) -> None:
        problems.append({"kind": "requestfailed", "path": url_path(req.url)})

    def on_console(msg) -> None:
        # Chromium logs "Failed to load resource" for every 4xx/5xx, which on_response records with its URL, so
        # only genuine script/console errors are added here.
        if msg.type == "error" and not msg.text.startswith("Failed to load resource"):
            problems.append({"kind": "console error", "path": url_path(msg.location.get("url", "")),
                             "text": msg.text[:160]})

    page.on("response", on_response)
    page.on("requestfailed", on_failed)
    page.on("console", on_console)


def dedupe(problems: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for p in problems:
        key = (p["kind"], p["path"])
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out[:20]


def slug(route: str) -> str:
    """PNG-name stem of a route PATTERN (`/accounts/:id` -> `accounts_id`): a real account id
    never reaches a file name."""
    return "root" if route == "/" else route.strip("/").replace(":", "").replace("/", "_")


def open_page(ctx, at: datetime, url: str, ready: str | None = None):
    """A new page of `ctx` with its `Date` fixed at `at` before it loads (timers keep running), watched for page
    errors and load problems, opened at `url` and waited on until nothing is loading - or, with `ready` (a CSS
    selector), until that element is on screen: (page, errors, problems). The one place the driver opens a page."""
    errors: list[str] = []
    problems: list[dict] = []
    page = ctx.new_page()
    page.clock.set_fixed_time(at)
    page.on("pageerror", lambda e: errors.append(str(e)))
    watch_load_problems(page, problems)
    page.goto(url, wait_until="networkidle")
    if ready is None:
        page.wait_for_function(f"() => !({STILL_LOADING})()", timeout=30000)
    else:
        page.wait_for_selector(ready, timeout=30000)
    page.wait_for_load_state("networkidle")
    return page, errors, problems


def _shoot(page, shot: Path, drawer: bool, errors: list[str], problems: list[dict], entry: dict,
           expected=None) -> dict | None:
    """Lets lazy chunks and chart layout settle, waits for the fonts (bounded), screenshots `page` into `shot` (the
    whole page, or the screen for the open `drawer`), checks it for overflow (the drawer left out of a route's shot,
    checked in its own) and closes it: the overflow.json entry (`entry` plus what was found), or None when it is
    clean. `expected` (a load problem -> bool) names the load problems that are what the shot is about, which are
    left out of the entry."""
    page.wait_for_timeout(400)
    if not page.evaluate(FONTS_READY):
        problems.append({"kind": "fonts not ready", "path": ""})
    page.screenshot(path=str(shot), full_page=not drawer)
    res = page.evaluate(OVERFLOW_JS, not drawer)
    load = dedupe([p for p in problems if expected is None or not expected(p)])
    bad = res["page"] or res["offenders"] or errors or load
    print(f"{shot.name}  {'OVERFLOW/ERROR' if bad else 'ok'}")
    page.close()
    if not bad:
        return None
    return {**entry, **res, "page_errors": errors[:10], "load_problems": load}


def shoot_route(ctx, at: datetime, base: str, route: str, query: str | None, account_id: str | None, out: Path,
                vname: str, theme: str) -> dict | None:
    """Opens `route` (open_page), screenshots the whole page into `out` and returns its overflow.json entry, or None
    when it is clean."""
    page, errors, problems = open_page(ctx, at, shot_url(base, resolve_param(route, base, account_id), query))
    return _shoot(page, out / shot_name(route, query, vname, theme), False, errors, problems,
                  {"route": route, "query": query is not None, "viewport": vname, "theme": theme})


def shoot_clicked(ctx, at: datetime, base: str, route: str, name: str, selectors: tuple[str, ...], account_id: str | None,
                  out: Path, vname: str, theme: str) -> dict | None:
    """Opens `route` (open_page, with no query), clicks each of `selectors` in order (CLICK_SHOTS), screenshots the
    whole page into `out` and checks it as a route's shot is checked: its overflow.json entry (`clicked`: the shot's
    name), or None."""
    page, errors, problems = open_page(ctx, at, shot_url(base, resolve_param(route, base, account_id), None))
    for selector in selectors:
        # a control that is missing or disabled fails within CLICK_TIMEOUT_MS: a problem of this shot (so the run exits
        # 1), the page shot as it stands, the run going on to the next shot - never a 30-second wait and a crashed run
        try:
            page.click(selector, timeout=CLICK_TIMEOUT_MS)
        except Exception:  # Playwright's own timeout or "not enabled" error
            problems.append({"kind": "click failed", "path": selector})
            break
    # a click that moves focus scrolls the window to it, and a full-page shot of a scrolled window draws the sticky top
    # bar where the window stands, over the middle of the page: the shot is taken from the top, as a route's is
    page.evaluate(SCROLL_TOP)
    return _shoot(page, out / click_shot_name(route, name, vname, theme), False, errors, problems,
                  {"route": route, "clicked": name, "query": False, "viewport": vname, "theme": theme})


def shoot_drawer(ctx, at: datetime, base: str, out: Path, vname: str, theme: str) -> dict | None:
    """Opens Home (open_page), opens the navigation drawer with the menu button (by its accessible name, never by a
    position), waits until it has slid in and screenshots the screen (the drawer and its backdrop are fixed to it;
    a full-page shot would add the page under them) into `out`, checking the drawer's own content for overflow with the
    page's: its overflow.json entry (`drawer: true`), or None."""
    page, errors, problems = open_page(ctx, at, shot_url(base, "/", None))
    page.get_by_role("button", name=DRAWER_BUTTON, exact=True).click()
    page.wait_for_function(DRAWER_OPEN, timeout=10000)
    return _shoot(page, out / drawer_name(vname, theme), True, errors, problems,
                  {"route": "/", "drawer": True, "query": False, "viewport": vname, "theme": theme})


def shoot_all(browser, at: datetime, zone: str, base: str, routes: list[str], queries: dict[str, str],
              account_id: str | None, out: Path, viewports: dict[str, tuple[int, int]], private: bool) -> list[dict]:
    """Every shot of a run, one context per theme x viewport in `zone`: the routes, then their clicked shots
    (CLICK_SHOTS), then, at a phone viewport of a run that shoots Home, the open drawer. Returns the overflow.json
    entries."""
    overflow: list[dict] = []
    phones = drawer_viewports(viewports) if "/" in routes else []
    for theme in THEMES:
        for vname, (w, h) in viewports.items():
            ctx = browser.new_context(**context_options(w, h, zone))
            ctx.add_init_script(init_script(theme, private))
            entries = [shoot_route(ctx, at, base, route, queries.get(route), account_id, out, vname, theme)
                       for route in routes]
            entries += [shoot_clicked(ctx, at, base, route, name, selectors, account_id, out, vname, theme)
                        for route, name, selectors in click_shots(routes)]
            if vname in phones:
                entries.append(shoot_drawer(ctx, at, base, out, vname, theme))
            overflow.extend(e for e in entries if e)
            ctx.close()
    return overflow


# ---------------------------------------------------------------- the page shown before any export exists

# The 14 export files the dashboard's loader asks for (app/src/data/types.ts EXPORT_FILES; test_screenshots_driver.py
# pins the two lists equal). With none served, each answers 404, which is the state the page is about.
EXPORT_FILES = ("manifest.json", "transactions.json", "accounts.json", "assets_timeseries.json", "holdings.json",
                "cashflow_monthly.json", "recurring.json", "quality_report.json", "open_questions.json",
                "assumptions.json", "balances_timeseries.json", "networth_daily.json", "vendors.json",
                "ai_status.json")
# The instant the page is shot at: no manifest names an ingest instant, and the page shows no date, but its clock is
# fixed all the same so a shot never depends on the day it is taken. The browser runs in UTC (no export names a zone).
NOTHING_BUILT_AT = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
NOTHING_BUILT_ZONE = "UTC"
# what the run waits for instead of the data-ready signal: the heading of the first-run page in its "nothing built"
# state (app/src/sections/FirstRun.tsx: data-first-run="nothing-built", one h1)
NOTHING_BUILT_READY = '[data-first-run="nothing-built"] h1'
# the route an overflow.json entry of the mode names (no route of the app: the page stands outside its routes)
NOTHING_BUILT_ROUTE = "(nothing built)"


def nothing_built_name(vname: str, theme: str) -> str:
    """The PNG of the page shown before any export exists, which no route's stem is."""
    return f"nothing_built__{vname}__{theme}.png"


def absent_export(problem: dict) -> bool:
    """A load problem that is the page's own premise: an export file the site does not have, answered 404."""
    return problem.get("kind") == "http 404" and problem.get("path") in {f"/{name}" for name in EXPORT_FILES}


def shoot_nothing_built(ctx, base: str, out: Path, vname: str, theme: str) -> dict | None:
    """Opens the dashboard with no export served (open_page, waiting for the page's heading), screenshots the whole
    page into `out` and returns its overflow.json entry, or None when it is clean; a 404 for an export file is the
    page's premise and no entry (absent_export), every other problem counts."""
    page, errors, problems = open_page(ctx, NOTHING_BUILT_AT, shot_url(base, "/", None), ready=NOTHING_BUILT_READY)
    return _shoot(page, out / nothing_built_name(vname, theme), False, errors, problems,
                  {"route": NOTHING_BUILT_ROUTE, "query": False, "viewport": vname, "theme": theme},
                  expected=absent_export)


def shoot_nothing_built_all(browser, base: str, out: Path, viewports: dict[str, tuple[int, int]],
                            private: bool) -> list[dict]:
    """Every shot of a FF_SHOT_NOTHING_BUILT=1 run: the one page in one context per theme x viewport, in UTC. Returns
    the overflow.json entries."""
    overflow: list[dict] = []
    for theme in THEMES:
        for vname, (w, h) in viewports.items():
            ctx = browser.new_context(**context_options(w, h, NOTHING_BUILT_ZONE))
            ctx.add_init_script(init_script(theme, private))
            entry = shoot_nothing_built(ctx, base, out, vname, theme)
            if entry:
                overflow.append(entry)
            ctx.close()
    return overflow


# ---------------------------------------------------------------- the store documents the site serves

# The site answers the five documents the app reads at /api/* as an idle, healthy stack's store would
# (deploy/scenario_store.py; tools/verify/test_screenshots_driver.py ties each fixture to it): its empty preferences,
# answers and manual assets (or a demo household's own, from its state directory), the idle refresh status in the
# household's zone, and the synthetic second-house scenarios.
API_DOCUMENTS = ("answers", "manual-assets", "prefs", "refresh", "scenarios")
API_FIXTURES = {"answers": "answers.empty.json", "manual-assets": "manual-assets.empty.json",
                "prefs": "prefs.empty.json", "scenarios": "scenarios.synthetic.json"}
REFRESH_FIXTURE = "refresh.idle.json"
# what in REFRESH_FIXTURE the household's zone replaces: the zone its schedule's times are read in
ZONE_PLACEHOLDER = "@household-timezone@"
# a demo household's state documents (the generator's <tree>/state/): the file each is read from and the store's own
# check of that document (deploy/scenario_store.py). screenshots.sh takes the file names from here.
STATE_DOCUMENTS = {"answers": ("answers.json", "validate_answers"),
                   "manual-assets": ("manual_assets.json", "validate_manual_assets")}


def refresh_status(template: dict, zone: str) -> dict:
    """The idle refresh status with the schedule's zone filled in (a copy; the template is left alone)."""
    doc = copy.deepcopy(template)
    schedule = doc.get("schedule") if isinstance(doc, dict) else None
    if not isinstance(schedule, dict) or schedule.get("timezone") != ZONE_PLACEHOLDER:
        raise SystemExit(f"{REFRESH_FIXTURE}: schedule.timezone is not the zone placeholder {ZONE_PLACEHOLDER!r}")
    schedule["timezone"] = zone
    return doc


def state_files() -> list[str]:
    """The file names of the state documents, in STATE_DOCUMENTS order."""
    return [name for name, _ in STATE_DOCUMENTS.values()]


def _store():
    """deploy/scenario_store.py, loaded by path from the repository this module sits in (it imports refresh_state from
    deploy/): the host-side check runs the store's own validation, never a copy of it."""
    deploy = Path(__file__).resolve().parent.parent.parent / "deploy"
    if str(deploy) not in sys.path:
        sys.path.insert(0, str(deploy))
    spec = importlib.util.spec_from_file_location("scenario_store_for_shots", deploy / "scenario_store.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def state_problem(state: Path) -> str | None:
    """Why a household's state directory cannot be served, or None: each state document it holds must be one the
    store takes (its own check, STATE_DOCUMENTS), and a refusal names the file and gives the store's reason. A document
    it lacks is served empty."""
    store = None
    for name, check in STATE_DOCUMENTS.values():
        f = state / name
        if not f.exists():
            continue
        store = store or _store()
        try:
            getattr(store, check)(f.read_bytes())
        except ValueError as e:
            return f"FF_SHOT_STATE: {f}: the store refuses it: {e}"
    return None


def write_api(api: Path, fixtures: Path, zone: str, state: Path | None) -> None:
    """Writes the five documents into the site's `api` directory, each a file of its own (copied, links followed:
    never a link): a household state's answers and manual assets when `state` holds them, else the empty ones."""
    for name, fixture in API_FIXTURES.items():
        source = fixtures / fixture
        if state is not None and name in STATE_DOCUMENTS and (state / STATE_DOCUMENTS[name][0]).exists():
            source = state / STATE_DOCUMENTS[name][0]
        shutil.copyfile(source, api / name)
    template = json.loads((fixtures / REFRESH_FIXTURE).read_text("utf-8"))
    (api / "refresh").write_text(json.dumps(refresh_status(template, zone), indent=1) + "\n", "utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="e.g. http://127.0.0.1:80")
    ap.add_argument("--app-tsx", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--routes", nargs="*", help="override the parsed route list")
    ap.add_argument("--account-id", help="id for :id routes (default: first with transactions in accounts.json)")
    a = ap.parse_args()
    if NOTHING_BUILT:
        return main_nothing_built(a.base, a.out)
    src = a.app_tsx.read_text()
    env_routes = (os.environ.get("FF_SHOT_ROUTES") or "").split()
    routes = a.routes or env_routes or routes_from_app_tsx(src)
    if not routes:
        print("no routes parsed from the PAGES manifest", file=sys.stderr)
        return 2
    refusal = refused_routes(routes, src)
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
    queries = queries_from_env(os.environ.get("FF_SHOT_QUERY"), src, routes)
    at = served_ingest_instant(a.base)
    zone = served_zone(a.base)
    from playwright.sync_api import sync_playwright
    a.out.mkdir(parents=True, exist_ok=True)
    print(f"every page's clock is fixed at the exports' ingest instant {at.isoformat()}")
    if PRIVATE:
        print("FF_SHOT_PRIVATE=1: privacy mode ON in every shot (amounts masked, charts blurred)")
    if queries:
        print(f"FF_SHOT_QUERY: {len(queries)} route(s) shot with a query: {' '.join(sorted(queries))}")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(chromium_sandbox=False)
        zone = accepted_zone(browser, zone, PRIVATE)
        overflow = shoot_all(browser, at, zone, a.base, routes, queries, a.account_id, a.out, VIEWPORTS, PRIVATE)
        browser.close()
    (a.out / "overflow.json").write_text(json.dumps(overflow, indent=1))
    clicked = len(click_shots(routes)) * len(VIEWPORTS) * len(THEMES)
    drawers = expected_shots(routes, VIEWPORTS) - len(routes) * len(VIEWPORTS) * len(THEMES) - clicked
    print(f"{len(routes)} routes x {len(VIEWPORTS)} viewports x {len(THEMES)} themes + {clicked} clicked + {drawers} of "
          f"the open drawer; {len(overflow)} overflow/error entries -> overflow.json")
    return 0


def main_nothing_built(base: str, out: Path) -> int:
    """FF_SHOT_NOTHING_BUILT=1: the page shown before any export exists, at every viewport in both themes, into
    `out` with its overflow.json. Reads no export and no route list."""
    from playwright.sync_api import sync_playwright
    out.mkdir(parents=True, exist_ok=True)
    print(f"FF_SHOT_NOTHING_BUILT=1: no export is served; the page's clock is fixed at {NOTHING_BUILT_AT.isoformat()} "
          f"and the browser runs in {NOTHING_BUILT_ZONE}")
    if PRIVATE:
        print("FF_SHOT_PRIVATE=1: privacy mode ON in every shot (amounts masked, charts blurred)")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(chromium_sandbox=False)
        overflow = shoot_nothing_built_all(browser, base, out, VIEWPORTS, PRIVATE)
        browser.close()
    (out / "overflow.json").write_text(json.dumps(overflow, indent=1))
    print(f"the page shown before any export exists x {len(VIEWPORTS)} viewports x {len(THEMES)} themes; "
          f"{len(overflow)} overflow/error entries -> overflow.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

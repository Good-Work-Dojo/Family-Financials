#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The stack's browser run: headless Chromium (Playwright, in the screenshot harness's pinned image) against the real
stack, Caddy in front of the store, as a household's browser uses it.

It opens the dashboard (a page load without a session goes to the sign-in page), types the passcode into the sign-in
page and submits the form, then visits every route of the dashboard (the screenshot driver's list, parsed from
app/src/App.tsx: every page, every hub tab, `/accounts/:id` with the household's first account that has
transactions), hovers a chart on Home, exports the transactions' CSV (the download must start, and no text cell of its
first row may begin like a formula), with --ai-vendors-file (the AI step is on: stack_ai.py) opens the register
filtered to the rows filed by AI (each must carry the AI chip on a row of a vendor the file names, and the sidebar must
say where vendor names go), switches the theme and back, and signs out from Settings (the page goes to the sign-in
page and the exports answer 401).

It fails on: any `securitypolicyviolation` event (an init script on every document reports each one), any console
error, any page error, any request to an origin other than the stack's own (data: and blob: URLs are the page's own),
any answer of 400 or more while signed in (a 401 above all), and any step that does not happen. Prints one line per
problem, then `stack browser: N routes, 0 violations` (or `stack browser: N routes, K problems`); exit 0 only when
clean. playwright is imported inside main(), so the host can import this module for its pure helpers.

Usage (inside mcr.microsoft.com/playwright/python, with playwright pip-installed at the image's version):
  stack_browser.py --base http://127.0.0.1:8080 --passcode-file F --app-tsx App.tsx --driver screenshots_driver.py
                   [--ai-vendors-file F]
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import re
import sys
from urllib.parse import urlsplit

# reported from every document, before its own scripts run, through a function the context exposes
CSP_LISTENER = """
document.addEventListener('securitypolicyviolation', (e) => {
  try { window.__ffCspViolation(`${e.violatedDirective} blocked ${e.blockedURI || 'inline'} in ${e.sourceFile || e.documentURI}`); }
  catch (err) {}
});
"""
# the run's control: an inline script, which the policy (script-src 'self') refuses
PLANT = "() => { const s = document.createElement('script'); s.textContent = 'window.__planted = 1'; " \
        "document.head.appendChild(s); }"


def control_heard(heard: list[str]) -> bool:
    """The planted script was reported as a violation of script-src and as a console error."""
    return (any(h.startswith("CSP violation: script-src") for h in heard)
            and any(h.startswith("console error:") for h in heard))


# the characters a spreadsheet reads as the start of a formula (app/src/lib/csv.ts FORMULA_START)
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def foreign(url: str, base: str) -> bool:
    """A request to another origin: anything but data:, blob: and about: URLs that is not the stack's own origin."""
    scheme = urlsplit(url).scheme
    if scheme in ("data", "blob", "about"):
        return False
    return origin_of(url) != origin_of(base)


def formula_cells(row: list[str]) -> list[str]:
    """The text cells of a CSV row that begin like a formula (a number, `-12.5` included, is a number, not text)."""
    return [c for c in row if not NUMBER.match(c) and c.startswith(FORMULA_START)]


def first_data_row(text: str) -> list[str]:
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    return rows[1] if len(rows) > 1 else []


# the AI chip's title (app/src/lib/categorySource.ts sourceChip), and the text of the row an element sits in
AI_CHIP_TITLE = "Filed by AI"
# the sidebar's line while the step is on with a key, for the demo's provider (app/src/lib/aiCard.ts localOnlyLine)
AI_SIDEBAR = "local-only · vendor names go to Anthropic for sorting"
ROW_TEXT = "e => (e.closest('tr') || e.closest('[role=row]') || e.closest('li') || e.parentElement).textContent"


def ai_rows_shown(rows: list[str], vendors: list[str]) -> bool:
    """At least one AI chip, and every row holding one names a vendor the AI step filed."""
    return bool(rows) and all(any(v in text for v in vendors) for text in rows)


def result_line(routes: int, problems: list[str]) -> str:
    return f"stack browser: {routes} routes, " + ("0 violations" if not problems else f"{len(problems)} problems")


def load_driver(path: str):
    spec = importlib.util.spec_from_file_location("screenshots_driver", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the stack's browser run")
    ap.add_argument("--base", required=True)
    ap.add_argument("--passcode-file", required=True)
    ap.add_argument("--app-tsx", required=True)
    ap.add_argument("--driver", required=True)
    ap.add_argument("--ai-vendors-file")
    args = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright

    driver = load_driver(args.driver)
    with open(args.app_tsx) as fh:
        routes = driver.routes_from_app_tsx(fh.read())
    with open(args.passcode_file) as fh:
        passcode = fh.read().strip()
    base = args.base.rstrip("/")
    problems: list[str] = []
    planted: list[str] = []                     # what the listeners heard while the control was planted
    state = {"signed_in": False, "planting": False}
    visited = 0

    def problem(text: str) -> None:
        if state["planting"]:
            planted.append(text)
        elif text not in problems:
            problems.append(text)
            print(f"FAIL {text}", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True,
                                  reduced_motion="reduce")
        ctx.expose_function("__ffCspViolation", lambda text: problem(f"CSP violation: {text}"))
        ctx.add_init_script(CSP_LISTENER)

        def on_request(req) -> None:
            if foreign(req.url, base):
                problem(f"a request to another origin: {origin_of(req.url)}")

        def on_response(r) -> None:
            if state["signed_in"] and r.status >= 400:
                problem(f"{r.status} for {urlsplit(r.url).path} while signed in")

        ctx.on("request", on_request)
        ctx.on("response", on_response)

        def watch(page) -> None:
            page.on("pageerror", lambda e: problem(f"page error: {str(e)[:160]}"))
            page.on("console", lambda m: m.type == "error" and problem(f"console error: {m.text[:160]}"))

        ctx.on("page", watch)

        def settle(page) -> None:
            page.wait_for_load_state("networkidle")
            page.wait_for_function(f"() => !({driver.STILL_LOADING})()", timeout=30000)
            page.wait_for_timeout(300)

        # sign in through the real sign-in page
        page = ctx.new_page()
        page.goto(base + "/", wait_until="networkidle")
        if urlsplit(page.url).path != "/login":
            problem(f"a page load without a session reached {urlsplit(page.url).path}, not /login")
        # The control: an inline script planted in the sign-in page breaks the policy, and both listeners must hear it
        # (a run whose listeners hear nothing would otherwise pass as clean).
        state["planting"] = True
        page.evaluate(PLANT)
        page.wait_for_timeout(500)
        state["planting"] = False
        if not control_heard(planted):
            problem(f"the listeners did not hear a planted policy violation (heard: {planted})")
        else:
            print("control: an inline script planted in the sign-in page was reported (violation and console error)")
        page.fill("#passcode", passcode)
        with page.expect_navigation(wait_until="networkidle"):
            page.click("#sign-in button[type=submit]")
        if urlsplit(page.url).path != "/" or not any(c["name"] == "ff_session" for c in ctx.cookies()):
            problem(f"signing in left the page at {urlsplit(page.url).path} without a session")
        else:
            print("signed in through the sign-in page")
        state["signed_in"] = True
        settle(page)
        page.close()

        # every route, each in a page of its own; Home's chart hovered
        accounts = ctx.request.get(base + "/accounts.json").json().get("accounts", [])
        account = next((a["id"] for a in accounts if a.get("txn_data_end") is not None),
                       accounts[0]["id"] if accounts else driver.NO_ACCOUNT)
        for route in routes:
            page = ctx.new_page()
            page.goto(driver.shot_url(base, driver.resolve_param(route, base, account), None),
                      wait_until="networkidle")
            settle(page)
            visited += 1
            if route == "/":
                charts = page.locator("canvas")
                if charts.count() == 0:
                    problem("Home shows no chart to hover")
                else:
                    charts.first.hover()
                    page.wait_for_timeout(500)
                    print(f"hovered Home's first chart ({charts.count()} on the page)")
            page.close()
        print(f"visited {visited} routes")

        # the transactions' CSV
        page = ctx.new_page()
        page.goto(driver.shot_url(base, "/spending/transactions", None), wait_until="networkidle")
        settle(page)
        with page.expect_download(timeout=15000) as download:
            page.get_by_role("button", name=re.compile(r"Export CSV")).first.click()
        path = download.value.path()
        name = download.value.suggested_filename
        with open(path, encoding="utf-8") as fh:
            row = first_data_row(fh.read())
        if not name.endswith(".csv") or not row:
            problem(f"the CSV export gave {name!r} with no data row")
        if formula_cells(row):
            problem(f"the CSV's first row has {len(formula_cells(row))} text cell(s) that begin like a formula")
        elif row:
            print(f"exported the transactions' CSV: its first row has {len(row)} cells, none begins like a formula")

        # with the AI step on, the rows it filed: the register filtered to them shows each with the AI chip, and the
        # sidebar says where vendor names go
        if args.ai_vendors_file:
            with open(args.ai_vendors_file) as fh:
                vendors = [line.strip() for line in fh if line.strip()]
            ai = ctx.new_page()
            ai.goto(driver.shot_url(base, "/spending/transactions", "sources=ai"), wait_until="networkidle")
            settle(ai)
            chips = ai.locator(f'[title="{AI_CHIP_TITLE}"]')
            rows = [chips.nth(i).evaluate(ROW_TEXT) for i in range(chips.count())]
            line = ai.get_by_test_id("local-only-line").first.text_content() or ""
            if not vendors:
                problem("the AI step filed no row to look for")
            elif not ai_rows_shown(rows, vendors):
                problem(f"the register filtered to AI rows shows {len(rows)} AI chip(s), none on an AI-filed vendor's row")
            elif line != AI_SIDEBAR:
                problem(f"with AI on the sidebar says {line!r}, not {AI_SIDEBAR!r}")
            else:
                print(f"AI: the register shows {len(rows)} row(s) filed by AI with the AI chip; the sidebar says "
                      "the vendor names go to Anthropic")
            ai.close()

        # the theme, there and back
        page.get_by_role("button", name="Light mode").click()
        page.wait_for_function("() => document.documentElement.classList.contains('light')", timeout=5000)
        page.get_by_role("button", name="Dark mode").click()
        page.wait_for_function("() => document.documentElement.classList.contains('dark')", timeout=5000)
        print("switched the theme to light and back")
        page.close()

        # sign out from Settings
        page = ctx.new_page()
        page.goto(driver.shot_url(base, "/settings", None), wait_until="networkidle")
        settle(page)
        state["signed_in"] = False
        with page.expect_navigation(wait_until="networkidle"):
            page.get_by_role("button", name="Sign out", exact=True).click()
        if urlsplit(page.url).path != "/login":
            problem(f"signing out left the page at {urlsplit(page.url).path}, not /login")
        after = ctx.request.get(base + "/manifest.json")
        if after.status != 401:
            problem(f"after signing out an export answers {after.status}, not 401")
        else:
            print("signed out from Settings: the page is the sign-in page and an export answers 401")
        page.close()
        browser.close()

    print(result_line(visited, problems))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())

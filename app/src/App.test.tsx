// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import {
  closedRoutes, documentTitle, firstRunShownAt, GATED_ROUTES, moduleOf, NAV, navFor, navItemCls, PAGES, pagesFor,
  PALETTE, paletteFor, PARAM_REDIRECTS, REDIRECTS, RedirectKeepSearch, ROUTE_PATTERNS, routeOpenIn, titleFor,
} from "./App";
import { CAPABILITIES } from "./capabilities";
import { MANUAL_ASSETS_PATH } from "./components/ManualAssetsLink";
import PageMoved from "./components/PageMoved";
import { ALLOCATION_FALLBACK, ALLOCATION_ROUTES, allocationRoute } from "./lib/allocationRoutes";
import { FIRST_RUN_KEEPS } from "./lib/firstRun";
import { MODULE_IDS } from "./lib/modules";
import { isScrollTarget } from "./lib/scrollTargets";
import { ELSEWHERE } from "./sections/settings/ElsewhereCard";
import { withoutComments } from "./test/sourceText";

afterEach(cleanup);

const routes = new Set(PAGES.flatMap((p) =>
  [p.to, ...(p.hub?.tabs ?? []).map((t) => `${p.to}/${t.path}`)]));

describe("App manifest surfaces", () => {
  it("every palette entry points at a page or a hub tab, never a redirect source or a hub extra", () => {
    const sources = new Set(REDIRECTS.map(([from]) => from));
    for (const e of PALETTE) {
      expect(routes.has(e.to), e.label).toBe(true);
      expect(sources.has(e.to), e.label).toBe(false);
    }
  });
  it("hub tabs read 'Hub · Tab' and each destination is listed once", () => {
    const labels = PALETTE.map((e) => e.label);
    expect(labels).toContain("Accounts · Balances");
    expect(labels).toContain("Planning · House");
    expect(labels).not.toContain("Accounts");
    expect(labels).toContain("Trends");                // a flat page is listed by its bare label
    expect(new Set(PALETTE.map((e) => e.to)).size).toBe(PALETTE.length);
  });
  // Home's allocation donut opens a page per asset class through router paths, which the hash-link scan below
  // cannot see: each destination must be a page or a hub tab
  it("every allocation-donut destination is a live route", () => {
    const dest = Object.entries(ALLOCATION_ROUTES);
    expect(dest.length).toBeGreaterThan(0);
    for (const [cls, to] of dest) expect(routes.has(to), `${cls} -> ${to}`).toBe(true);
  });
  // a slice whose page can be off leads elsewhere meanwhile: the House slice to Balances while House is off
  it("a slice whose page can be off has a fallback that always shows, and leads there while its page is off", () => {
    for (const [cls, to] of Object.entries(ALLOCATION_ROUTES)) {
      const fallback = ALLOCATION_FALLBACK[cls];
      expect(moduleOf(to) === null || fallback !== undefined, `${cls} -> ${to} can be off and has no fallback`).toBe(true);
      if (fallback === undefined) continue;
      expect(routes.has(fallback) && moduleOf(fallback) === null, `${cls}'s fallback ${fallback} is a page that always shows`).toBe(true);
    }
    expect(Object.keys(ALLOCATION_FALLBACK).every((cls) => cls in ALLOCATION_ROUTES)).toBe(true);
    expect(allocationRoute("House", routeOpenIn([]))).toBe("/planning/home");
    expect(allocationRoute("House", routeOpenIn(["/planning/home"]))).toBe("/accounts");
    expect(allocationRoute("Cash", routeOpenIn(["/planning/home"]))).toBe("/accounts");
    expect(allocationRoute("Stocks/ETF", routeOpenIn(["/planning/home", "/planning/second-house"]))).toBe("/accounts/investments");
    expect(allocationRoute("Unknown class", routeOpenIn([]))).toBeUndefined();
  });
  // Settings' Elsewhere card links through a table the link scan below cannot see: each entry names a live route, the
  // place title that route has, and a card that a `?card=` URL may land on
  it("every Elsewhere link of Settings names a live route by its place title, and a card a URL may land on", () => {
    expect(ELSEWHERE.length).toBe(5);
    for (const e of ELSEWHERE) {
      expect(routes.has(e.route), `${e.label} -> ${e.route}`).toBe(true);
      expect(e.page, `${e.label}: the page's place title`).toBe(titleFor(e.route));
      expect(isScrollTarget(e.card), `${e.label}: ${e.card} is a scroll target`).toBe(true);
    }
    expect(new Set(ELSEWHERE.map((e) => e.card)).size, "each lands on its own card").toBe(ELSEWHERE.length);
  });
  // the pages that need a value entered by hand link to Settings' Manual assets card through a constant the scan
  // cannot see: it names a live route that is never switched off or replaced, and a card a URL may land on
  it("the link to the Manual assets card names a live route that always shows, and a card a URL may land on", () => {
    const [route, query] = MANUAL_ASSETS_PATH.split("?");
    expect(routes.has(route), route).toBe(true);
    expect(moduleOf(route), "never switched off").toBeNull();
    expect(FIRST_RUN_KEEPS.includes(route), "never replaced by the first-run page").toBe(true);
    expect(isScrollTarget(new URLSearchParams(query).get("card"))).toBe(true);
  });
  it("NAV hides navHidden pages; the palette keeps them", () => {
    for (const p of PAGES.filter((p) => p.navHidden)) {
      expect(NAV.some((n) => n.to === p.to), p.to).toBe(false);
      expect(PALETTE.some((e) => e.to === p.to), p.to).toBe(true);
    }
    expect(NAV.length).toBe(PAGES.filter((p) => !p.navHidden).length);
  });
  it("titleFor names the hub and the tab", () => {
    expect(titleFor("/")).toBe("Home");
    expect(titleFor("/spending")).toBe("Spending");
    expect(titleFor("/spending/recurring")).toBe("Spending · Recurring");
    expect(titleFor("/spending/reports")).toBe("Spending · Reports");
    expect(titleFor("/cash-flow/budget")).toBe("Cash Flow · Budget");
    expect(titleFor("/planning/second-house")).toBe("Planning · Second house");
    expect(titleFor("/planning/home")).toBe("Planning · House");        // the tab is "House"; its path is still /planning/home
    expect(titleFor("/accounts/liabilities")).toBe("Accounts · Liabilities");
    expect(titleFor("/accounts/chk_1")).toBe("Accounts");   // with no accounts to look it up in, a hub extra is named by its hub alone
    expect(titleFor("/review")).toBe("Review");
    expect(titleFor("/data")).toBe("Data");
    expect(titleFor("/nowhere")).toBe("Family Financials");
  });
  const ACCOUNTS = [{ id: "chk_1", name: "Everyday Checking ...1234" }, { id: "a b", name: "Spaced Id" }];
  it("an account's page is titled with the account's own name, not 'Accounts'", () => {
    expect(titleFor("/accounts/chk_1", ACCOUNTS)).toBe("Everyday Checking ...1234");
    expect(titleFor("/accounts/a%20b", ACCOUNTS)).toBe("Spaced Id");                       // the router decodes the param
    expect(titleFor("/accounts/no-such", ACCOUNTS)).toBe("Accounts");                      // an unknown id falls back to the hub
    expect(titleFor("/accounts/%E0%A4%A", ACCOUNTS)).toBe("Accounts");                     // a malformed escape never throws
    expect(titleFor("/accounts/investments", ACCOUNTS)).toBe("Accounts · Investments");    // a tab wins over a param
    expect(titleFor("/accounts", ACCOUNTS)).toBe("Accounts");
    expect(titleFor("/spending/chk_1", ACCOUNTS)).toBe("Spending");                        // only the account route is named by an account
  });
  it("the browser tab carries the place, then the app; Home and an unknown path are the app's name", () => {
    expect(documentTitle("/")).toBe("Family Financials");
    expect(documentTitle("/spending/transactions")).toBe("Spending · Transactions · Family Financials");
    expect(documentTitle("/accounts/chk_1", ACCOUNTS)).toBe("Everyday Checking ...1234 · Family Financials");
    expect(documentTitle("/no-such-page")).toBe("Family Financials");
    // every page gets its own title, so a tab and a Back-history entry tell them apart
    const titles = PALETTE.map((e) => documentTitle(e.to));
    expect(new Set(titles).size).toBe(titles.length);
  });
  it("while the first-run page stands in, the top bar and the browser tab name its state, on every route it replaces", () => {
    for (const path of ["/", "/cash-flow/budget", "/accounts/chk_1", "/planning/second-house", "/review"]) {
      expect(titleFor(path, ACCOUNTS, "setup"), path).toBe("Set up");
      expect(documentTitle(path, ACCOUNTS, "setup"), path).toBe("Set up · Family Financials");
      expect(documentTitle(path, ACCOUNTS, "waiting"), path).toBe("Waiting for data · Family Financials");
    }
    // Data and Settings stay their own pages, and a path no page owns its catch-all
    expect(documentTitle("/data", [], "setup")).toBe("Data · Family Financials");
    expect(titleFor("/settings", [], "waiting")).toBe("Settings");
    expect(documentTitle("/no-such-page", [], "setup")).toBe("Family Financials");
    expect(documentTitle("/cash-flow/budget", [], null), "a household with data").toBe("Cash Flow · Budget · Family Financials");
  });
  it("ROUTE_PATTERNS is every page, tab and extra; firstRunShownAt reads the household's state on the ones it replaces", () => {
    expect(ROUTE_PATTERNS).toEqual(PAGES.flatMap((p) => [p.to, ...(p.hub?.tabs ?? []).map((t) => `${p.to}/${t.path}`),
      ...(p.hub?.extra ?? []).map((x) => `${p.to}/${x.path}`)]));
    for (const keep of FIRST_RUN_KEEPS) expect(ROUTE_PATTERNS.includes(keep), `${keep} is a route`).toBe(true);
    expect(firstRunShownAt("/accounts/investments", "waiting")).toBe("waiting");
    expect(firstRunShownAt("/data", "waiting")).toBeNull();
    expect(firstRunShownAt("/", null)).toBeNull();
  });
  it("the palette lists Trends and Settings, each by its own route", () => {
    expect(PALETTE.some((e) => e.to === "/trends" && e.label === "Trends")).toBe(true);
    expect(PALETTE.some((e) => e.to === "/settings" && e.label === "Settings")).toBe(true);
  });
  it("the public app has no legacy redirects or aliases", () => {
    expect(REDIRECTS).toEqual([]);
    expect(PARAM_REDIRECTS).toEqual({});
    for (const p of PAGES) {
      expect("aliases" in p, `${p.to} carries aliases`).toBe(false);
      expect(p.hub !== undefined && "indexAliases" in p.hub, `${p.to} hub carries indexAliases`).toBe(false);
      for (const t of p.hub?.tabs ?? []) expect("aliases" in t, `${p.to}/${t.path} carries aliases`).toBe(false);
    }
  });
  it("hub tabs are sentence case, and Spending's read Explore, Transactions, Recurring, Reports in that order", () => {
    for (const p of PAGES) {
      for (const label of [p.hub?.indexLabel, ...(p.hub?.tabs ?? []).map((t) => t.label)]) {
        if (label) expect(label, `${p.label}: "${label}"`).not.toMatch(/\s[A-Z]/);   // a capital after a space: "Second House", "Income Statement"
      }
    }
    const spending = PAGES.find((p) => p.to === "/spending")!.hub!;
    expect([spending.indexLabel, ...spending.tabs.map((t) => t.label)]).toEqual(["Explore", "Transactions", "Recurring", "Reports"]);
    expect(PAGES.find((p) => p.to === "/planning")!.hub!.tabs.map((t) => [t.label, t.path]))
      .toEqual([["House", "home"], ["Second house", "second-house"]]);
  });
  it("Review is rendered but hidden from the sidebar; the sidebar is the eight top-level pages", () => {
    expect(PAGES.filter((p) => p.navHidden).map((p) => p.to).sort()).toEqual(["/review"]);
    expect(NAV.map((n) => n.label)).toEqual(["Home", "Cash Flow", "Spending", "Accounts", "Planning", "Trends", "Data", "Settings"]);
  });
  it("sidebar rows (hubs and the Review link share navItemCls) are 44px on a phone and fill when active", () => {
    for (const active of [true, false]) {
      const cls = navItemCls(active).split(/\s+/);
      expect(cls).toContain("py-3");            // 12px + 12px + 20px line = 44px below md
      expect(cls).toContain("md:py-1.5");
    }
    expect(navItemCls(true)).toContain("bg-zinc-800");
    expect(navItemCls(false)).not.toContain("bg-zinc-800");
  });
  it("the Life Report is gone as a page, and Data is a flat page the palette lists by its bare label", () => {
    expect(PAGES.some((p) => p.to === "/life"), "/life is not a page").toBe(false);
    const data = PAGES.find((p) => p.to === "/data");
    expect(data?.hub === undefined && data?.page !== undefined, "Data is a flat page").toBe(true);
    expect(PALETTE.filter((e) => e.to === "/data").map((e) => e.label)).toEqual(["Data"]);
  });
  it("every redirect lands on a rendered page or hub tab, never on another redirect", () => {
    const sources = new Set(REDIRECTS.map(([from]) => from));
    expect(REDIRECTS.length).toBe(sources.size);   // one entry per source
    for (const [from, to] of REDIRECTS) {
      expect(routes.has(to), `${from} -> ${to}`).toBe(true);
      expect(sources.has(to), `${from} -> ${to}`).toBe(false);
    }
  });
});

describe("switchable pages", () => {
  const ON = { home_buying: true }, OFF = { home_buying: false };
  const HAS = { mortgage: true }, LACKS = { mortgage: false };
  const routesIn = (pages: ReturnType<typeof pagesFor>) => pages.flatMap((p) => [p.to,
    ...(p.hub?.tabs ?? []).map((t) => `${p.to}/${t.path}`), ...(p.hub?.extra ?? []).map((x) => `${p.to}/${x.path}`)]);

  it("Second house belongs to Home buying and House needs a mortgage; no other route is gated", () => {
    expect(moduleOf("/planning/second-house")).toEqual({ module: "home_buying" });
    expect(moduleOf("/planning/home")).toEqual({ needs: "mortgage" });
    expect(GATED_ROUTES).toEqual(["/planning/home", "/planning/second-house"]);
    for (const r of ROUTE_PATTERNS.filter((x) => !GATED_ROUTES.includes(x))) expect(moduleOf(r), r).toBeNull();
    expect(moduleOf("/nowhere")).toBeNull();
    // every module has a page, and every gate names a known module
    for (const id of MODULE_IDS) expect(GATED_ROUTES.some((r) => moduleOf(r)?.module === id), id).toBe(true);
  });

  it("the closed routes: the module's while it is off, House's without a mortgage", () => {
    expect(closedRoutes(ON, HAS)).toEqual([]);
    expect(closedRoutes(OFF, HAS)).toEqual(["/planning/second-house"]);
    expect(closedRoutes(ON, LACKS)).toEqual(["/planning/home"]);
    expect(closedRoutes(OFF, LACKS)).toEqual(["/planning/home", "/planning/second-house"]);
  });

  it("pagesFor leaves out exactly the closed routes, so the sidebar, ⌘K and the tab bars never list one", () => {
    for (const modules of [ON, OFF]) {
      for (const facts of [HAS, LACKS]) {
        const shown = pagesFor(modules, facts);
        const closed = closedRoutes(modules, facts);
        expect(routesIn(shown)).toEqual(ROUTE_PATTERNS.filter((r) => !closed.includes(r)));
        const listed = paletteFor(shown).map((e) => e.to);
        for (const r of closed) expect(listed.includes(r), `⌘K lists ${r}`).toBe(false);
        // the sidebar lists hubs, never a tab: Planning stays, with Projections alone when both are off
        expect(navFor(shown)).toEqual(NAV);
      }
    }
    expect(paletteFor(pagesFor(OFF, LACKS)).filter((e) => e.to.startsWith("/planning")).map((e) => e.label))
      .toEqual(["Planning · Projections"]);
    expect(paletteFor(pagesFor(ON, HAS))).toEqual(PALETTE);
    expect(PAGES.find((p) => p.to === "/planning")!.hub!.tabs, "PAGES itself stays complete").toHaveLength(2);
  });

  it("the module-off page's one manifest row is pinned to a module's page, and is no stand-in", () => {
    const rows = CAPABILITIES.filter((c) => c.when === "module-off");
    expect(rows.map((c) => c.id)).toEqual(["card-module-off"]);
    for (const c of rows) {
      expect(moduleOf(c.route)?.module, `${c.id} is pinned to a module's page`).toBeDefined();
      expect(c.insteadOf, `${c.id} is no stand-in: the page it is on replaces the route's whole page`).toBeUndefined();
      expect(c.firstRun).toBeUndefined();
    }
  });

  it("routeOpenIn answers for a path, with its query or hash, by the route patterns closed", () => {
    const open = routeOpenIn(["/planning/home"]);
    expect(open("/planning/home")).toBe(false);
    expect(open("/planning/home?card=x")).toBe(false);
    expect(open("/planning/home#top")).toBe(false);
    expect(open("/planning")).toBe(true);
    expect(open("/planning/second-house")).toBe(true);
    expect(open("/accounts/liabilities")).toBe(true);
    expect(routeOpenIn([])("/planning/home")).toBe(true);
  });
});

// Every in-app `"#/…"` link in the SOURCE (node:fs, like capabilities.test.ts) must name a live
// route pattern and no redirect source, so a page that moves cannot leave a link behind that only
// works through a redirect (or, for a deleted route, not at all). Router-path literals - `navigate("/x")`,
// `to="/x"`, `to={`/x/${id}`}` - are scanned the same way. Test files are skipped.
const SRC = dirname(fileURLToPath(import.meta.url));
function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? sourceFiles(join(dir, e.name))
    : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [join(dir, e.name)] : []);
}
/** link targets whose route is not built yet. An entry here must NOT be a live route: the day the
 *  route lands the test below tells you to delete the entry. */
const NOT_BUILT_YET = new Set<string>();

describe("in-app links (#/ hrefs and router paths)", () => {
  const livePatterns = PAGES.flatMap((p) => [
    p.to,
    ...(p.hub?.tabs ?? []).map((t) => `${p.to}/${t.path}`),
    ...(p.hub?.extra ?? []).map((x) => `${p.to}/${x.path}`),
  ]);
  // `:id`-style segments match any single segment; `${…}` interpolations are such a segment
  const matcherOf = (pat: string) =>
    new RegExp("^" + pat.split("/").map((seg) =>
      seg.startsWith(":") ? "[^/]+" : seg.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("/") + "$");
  const matchers = livePatterns.map(matcherOf);
  const isLive = (path: string) => matchers.some((m) => m.test(path));
  const sources = new Set(REDIRECTS.map(([from]) => from));

  const files = sourceFiles(SRC);
  // hash hrefs AND router-path literals: navigate("/x"), to="/x", to={`/x/${id}`} (an object `to={{…}}` or a
  // variable is not a literal and stays the author's responsibility - the smoke test covers those)
  const ROUTER_LITERALS = [/\bnavigate\(\s*["'`](\/[^"'`?#]*)/g, /\bto=["'](\/[^"'?#]*)/g, /\bto=\{\s*[`"'](\/[^"'`?#]*)/g];
  /** the link paths a piece of source names; the query/hash fragment is already excluded by the classes above, and
   *  `${expr}` reads as a one-segment param */
  const linksIn = (src: string): string[] => [...[...src.matchAll(/["'`]#(\/[^"'`?#]*)/g)].map((m) => m[1]),
    ...ROUTER_LITERALS.flatMap((re) => [...src.matchAll(re)].map((m) => m[1]))].map((p) => p.replace(/\$\{[^}]*\}/g, ":x"));
  const links = files.flatMap((f) =>
    linksIn(readFileSync(f, "utf8")).map((path) => ({ file: f.slice(SRC.length + 1), path })));

  it("scans a non-trivial corpus, so an empty read cannot pass", () => {
    expect(files.length).toBeGreaterThan(50);
    expect(links.length).toBeGreaterThan(20);
  });
  it("the router-path patterns match what they claim, so a pattern with no hit in today's source is still proven", () => {
    const grab = (re: RegExp, src: string) => [...src.matchAll(re)].map((m) => m[1]);
    const [nav, toStr, toExpr] = ROUTER_LITERALS;
    expect(grab(nav, 'navigate("/accounts/x?y=1"); navigate(`/spending/${id}`); navigate(-1); onNavigate("/no")')).toEqual(["/accounts/x", "/spending/${id}"]);
    expect(grab(toStr, '<Link to="/data" /><NavLink to=\'/recurring\' end /><Navigate to="/home?a=1" replace /> <X href="/no" />')).toEqual(["/data", "/recurring", "/home"]);
    expect(grab(toExpr, "<Link to={`/accounts/${a.id}`} /><Link to={'/data'} /><Link to={{ pathname: '/no' }} /><Link to={where} />")).toEqual(["/accounts/${a.id}", "/data"]);
    // and the scan reads them out of real source: the hrefs the account pages link by
    expect(links.some((l) => l.file === "sections/Data.tsx" && l.path === "/accounts/:x")).toBe(true);
  });
  it("every link names a live route pattern and never a redirect source", () => {
    for (const { file, path } of links) {
      if (NOT_BUILT_YET.has(path)) continue;
      expect(sources.has(path), `${file}: "#${path}" is a REDIRECTS source`).toBe(false);
      expect(isLive(path), `${file}: "#${path}" is not a route in PAGES`).toBe(true);
    }
  });
  it("NOT_BUILT_YET holds only routes that really do not exist yet, and a link still uses each", () => {
    const used = new Set(links.map((l) => l.path));
    for (const path of NOT_BUILT_YET) {
      expect(isLive(path), `${path} is a route now - delete it from NOT_BUILT_YET`).toBe(false);
      expect(used.has(path), `${path} is linked nowhere - delete it from NOT_BUILT_YET`).toBe(true);
    }
  });

  // A link into a page that may not show goes through <GatedLink to>, which is plain text while the page is off, and so
  // does a mention of the place in the app's "Hub → Tab" words: any other literal link into a gated route, and any such
  // mention outside a GatedLink, would lead into a page that is off
  const gated = GATED_ROUTES.map(matcherOf);
  const gatedPlaces = PAGES.flatMap((p) => (p.hub?.tabs ?? [])
    .filter((t) => GATED_ROUTES.includes(`${p.to}/${t.path}`)).map((t) => `${p.label} → ${t.label}`));
  /** `src` without the `to` value of each GatedLink and WhileShown tag, and nothing else of the tag: a link nested in
   *  one of its props (a `whenOff` element) stays to be read. Only a `to` before any `<` or `>` in the tag is the
   *  tag's own; one written after a nested element is left, and read like any other link. */
  const withoutGatedTos = (src: string) =>
    src.replace(/(<(?:GatedLink|WhileShown)\b[^<>]*?\s)to=\{?(["'`])[^"'`]*\2\}?/g, "$1");
  /** the paths of gated routes `src` links to other than as the `to` of a GatedLink or of a WhileShown (its words) */
  const ungatedLinks = (src: string) =>
    linksIn(withoutGatedTos(src)).filter((path) => gated.some((m) => m.test(path)));
  /** the gated places `src` names in words (→ or ->) other than as a GatedLink's text, comments aside */
  const bareMentions = (src: string) => {
    const text = withoutComments(src).replace(/<GatedLink\b[^>]*>[\s\S]*?<\/GatedLink>/g, "");
    return gatedPlaces.filter((place) => text.includes(place) || text.includes(place.replace(" → ", " -> ")));
  };
  // Any string the application writes that IS a gated path leads there sooner or later — a constant handed to
  // navigate(), an object `to`, a template filled in at run time — so every quoted string ("…", '…' or `…`) is read
  // too: a gated path after an optional "#", ended by "?", "#" or the closing quote, and a template whose `${…}`
  // segments could make one, comments aside. Allowed: the own `to` of a GatedLink or a WhileShown, the manifest
  // (capabilities.ts), the donut's table (lib/allocationRoutes.ts, checked above), App.tsx's PAGES literal (it names
  // every route) and the test support under test/.
  const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const gatedSources = GATED_ROUTES.map((pat) => pat.split("/").map((seg) => (seg.startsWith(":") ? "[^/]+" : esc(seg))).join("/"));
  /** files whose gated paths are their own business */
  const gatedPathsAllowed = (file: string) =>
    file === "capabilities.ts" || file === "lib/allocationRoutes.ts" || file.startsWith("test/");
  /** App.tsx's source without its PAGES literal (from `export const PAGES` to the `];` that ends it) */
  const withoutPages = (src: string) => src.replace(/^export const PAGES\b[\s\S]*?^\];/m, "");
  /** the gated paths `src` writes as strings, as written (a template's `${…}` kept), comments aside */
  const gatedStrings = (src: string): string[] => {
    const text = withoutGatedTos(withoutComments(src));
    const plain = gatedSources.flatMap((re) =>
      [...text.matchAll(new RegExp(`(["'\`])#?(${re})(?=[?#]|\\1)`, "g"))].map((m) => m[2]));
    const templates = [...text.matchAll(/`([^`]*\$\{[^`]*)`/g)].map((m) => m[1]).filter((t) => {
      // a path with no `${…}` before its query is the plain rule's
      const path = t.replace(/^#/, "").replace(/\$\{[^}]*\}/g, "\u0000").split(/[?#]/)[0];
      if (!path.startsWith("/") || !path.includes("\u0000")) return false;
      const could = new RegExp("^" + path.split("\u0000").map(esc).join("[^/]+") + "$");
      return GATED_ROUTES.some((route) => could.test(route));
    });
    return [...plain, ...templates];
  };
  it("no literal link leads into a gated route, and no words name one, but through GatedLink", () => {
    expect(gatedPlaces).toEqual(["Planning → House", "Planning → Second house"]);
    // the scan sees every link form and both spellings, and lets a GatedLink and a comment through
    expect(ungatedLinks('<a href="#/planning/home" className="x">what-ifs</a>')).toEqual(["/planning/home"]);
    expect(ungatedLinks('navigate("/planning/second-house"); <Link to="/planning/home?card=x" />'))
      .toEqual(["/planning/second-house", "/planning/home"]);
    expect(ungatedLinks('<GatedLink to="/planning/home" className="x">what-ifs →</GatedLink> <a href="#/planning">x</a>')).toEqual([]);
    expect(ungatedLinks('<WhileShown to="/planning/home">the what-if</WhileShown>')).toEqual([]);
    expect(ungatedLinks('<GatedLink to={"/planning/home"}>x</GatedLink>'), "a braced to is the tag's own too").toEqual([]);
    // a link nested in the tag's own props is read: only the tag's `to` is allowed
    expect(ungatedLinks('<GatedLink to="/accounts" whenOff={<Link to="/planning/home">x</Link>}>y</GatedLink>'))
      .toEqual(["/planning/home"]);
    expect(ungatedLinks('<GatedLink whenOff={<a href="#/planning/second-house">x</a>} to="/accounts">y</GatedLink>'))
      .toEqual(["/planning/second-house"]);
    expect(bareMentions("<p>The what-if lives in Planning → House.</p>")).toEqual(["Planning → House"]);
    expect(bareMentions("const s = `see Planning -> Second house`;")).toEqual(["Planning → Second house"]);
    expect(bareMentions('<p>in <GatedLink to="/planning/home" className="x">Planning → House</GatedLink>.</p>')).toEqual([]);
    expect(bareMentions("// the same figure as Planning → House\n/* and Planning → Second house */")).toEqual([]);
    const hits = files.flatMap((f) => {
      const src = readFileSync(f, "utf8");
      return [...ungatedLinks(src), ...bareMentions(src)].map((hit) => `${f.slice(SRC.length + 1)}: ${hit}`);
    });
    expect(hits).toEqual([]);
    // the corpus does link into the gated routes (through GatedLink), so a scan that read nothing cannot pass
    expect(links.filter((l) => gated.some((m) => m.test(l.path))).length).toBeGreaterThan(1);
  });
  it("no string the application writes is a gated path, but in the places allowed", () => {
    // a constant, an object `to`, a template, a hash link with a query, every quote
    expect(gatedStrings('const HOUSE = "/planning/home"; navigate(HOUSE);')).toEqual(["/planning/home"]);
    expect(gatedStrings("<Link to={{ pathname: '/planning/second-house', search: q }} />")).toEqual(["/planning/second-house"]);
    expect(gatedStrings("navigate(`/planning/${tab}`)")).toEqual(["/planning/${tab}"]);
    expect(gatedStrings("const at = `#/planning/home?card=${id}`;")).toEqual(["/planning/home"]);
    expect(gatedStrings(`const a = "#/planning/second-house#top";`)).toEqual(["/planning/second-house"]);
    // what is no gated path stays quiet: another route, a longer path, a path inside a sentence, a template that cannot
    // make one, and a comment
    expect(gatedStrings('const a = "/planning"; const b = "/planning/home-ish"; const c = "see /planning/home";'
      + " const d = `/accounts/${id}`; const e = `/planning/${a}/${b}`; // navigate(\"/planning/home\")")).toEqual([]);
    expect(gatedStrings("<p>the household's own page</p> <Link to=\"/planning/home\" />"), "an apostrophe in words hides nothing")
      .toEqual(["/planning/home"]);
    // the allowed places: the tags of GatedLink and WhileShown, the manifest, the donut's table, test/, PAGES
    expect(gatedStrings('<GatedLink to="/planning/home" className="x">what-ifs →</GatedLink>'
      + '<WhileShown to="/planning/home">words</WhileShown>')).toEqual([]);
    expect(gatedStrings('<GatedLink to={"/planning/home"}>x</GatedLink>'), "a braced to is the tag's own too").toEqual([]);
    // only the tag's own `to`: a gated path nested in its `whenOff` is read
    expect(gatedStrings('<GatedLink to="/accounts" whenOff={<Link to="/planning/home">x</Link>}>y</GatedLink>'))
      .toEqual(["/planning/home"]);
    expect(["capabilities.ts", "lib/allocationRoutes.ts", "test/matrixChecks.ts"].every(gatedPathsAllowed)).toBe(true);
    expect(["sections/Tools.tsx", "App.tsx", "lib/modules.ts"].some(gatedPathsAllowed)).toBe(false);
    expect(gatedStrings(withoutPages('export const PAGES: PageDef[] = [\n  { to: "/planning/home", label: "x" },\n];\n'
      + 'const late = "/planning/second-house";'))).toEqual(["/planning/second-house"]);
    const read = (file: string) => readFileSync(join(SRC, file), "utf8");
    expect(gatedStrings(read("capabilities.ts")).length > 0 && gatedStrings(read("lib/allocationRoutes.ts")).length > 0,
      "the allowed files do hold gated paths, so the scan reads them").toBe(true);
    const hits = files.map((f) => f.slice(SRC.length + 1)).filter((file) => !gatedPathsAllowed(file)).flatMap((file) => {
      const src = file === "App.tsx" ? withoutPages(read(file)) : read(file);
      return gatedStrings(src).map((hit) => `${file}: ${hit}`);
    });
    expect(hits).toEqual([]);
    expect(withoutPages(read("App.tsx")).includes("export const PAGES"), "the PAGES literal is cut out whole").toBe(false);
  });
});

/** prints where it landed: pathname + search */
function Landed() {
  const { pathname, search } = useLocation();
  return <div data-testid="landed">{pathname}{search}</div>;
}
function visit(initial: string, byParam?: Record<string, string>) {
  cleanup();   // one mount at a time: several visits per `it`
  render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/old" element={<RedirectKeepSearch to="/new" byParam={byParam} />} />
        <Route path="*" element={<Landed />} />
      </Routes>
    </MemoryRouter>);
  return screen.getByTestId("landed").textContent;
}

describe("the catch-all route", () => {
  const app = readFileSync(join(SRC, "App.tsx"), "utf8");
  it("is the last route, after every page and redirect, and is no PAGES entry", () => {
    const at = app.indexOf('<Route path="*"');
    expect(at, "a catch-all route").toBeGreaterThan(-1);
    expect(app.indexOf('<Route path="*"', at + 1), "exactly one").toBe(-1);
    expect(app.indexOf("REDIRECTS.map(") < at && app.indexOf("PAGES.map((p) => pageRoute(") < at).toBe(true);
    expect(app.slice(at, app.indexOf("</Routes>")).match(/<Route /g)?.length, "nothing after it").toBe(1);
    // the smoke route loop, the capability manifest and the screenshot driver all read PAGES
    expect(PAGES.some((p) => p.to.includes("*") || (p.hub?.tabs ?? []).some((t) => t.path.includes("*")))).toBe(false);
    expect(PALETTE.some((p) => p.to.includes("*"))).toBe(false);
  });
  it("renders the page-moved card with a way Home and a way to search", () => {
    let searched = 0;
    render(
      <MemoryRouter initialEntries={["/an-old-bookmark"]}>
        <Routes>
          <Route path="/" element={<div data-testid="home" />} />
          <Route path="*" element={<PageMoved onSearch={() => { searched++; }} />} />
        </Routes>
      </MemoryRouter>);
    expect(screen.getByRole("heading", { name: "Page not found" })).toBeDefined();
    expect(screen.getByText("There is no page at this address.")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: /Find a page/ }));
    expect(searched).toBe(1);
    fireEvent.click(screen.getByRole("link", { name: /Go to Home/ }));
    expect(screen.getByTestId("home")).toBeDefined();
  });
});

describe("RedirectKeepSearch", () => {
  it("lands on the target with the whole query intact", () => {
    expect(visit("/old?keep=1&x=a")).toBe("/new?keep=1&x=a");
    expect(visit("/old")).toBe("/new");
  });
  it("sends a request carrying a PARAM_REDIRECTS key to that key's target, query intact", () => {
    const byParam = { pl: "/new/by-pl" };
    expect(visit("/old?pl=3m", byParam)).toBe("/new/by-pl?pl=3m");
    expect(visit("/old?other=1", byParam)).toBe("/new?other=1");
    expect(visit("/old", byParam)).toBe("/new");
  });
});

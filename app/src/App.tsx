// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { type ComponentType, lazy, type ReactNode, Suspense, useEffect, useMemo, useState } from "react";
import { HashRouter, matchPath, Navigate, NavLink, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { hubTestId, pageTestId } from "./capabilities";
import CommandPalette from "./components/CommandPalette";
import { RefreshModal, RefreshPill, RefreshProvider, RefreshUpdatedStrip } from "./components/DataRefresh";
import DemoBanner from "./components/DemoBanner";
import PageMoved from "./components/PageMoved";
import HouseholdName from "./components/HouseholdName";
import HubShell from "./components/HubShell";
import MobileTopBar from "./components/MobileTopBar";
import NavDrawer from "./components/NavDrawer";
import ModuleOff from "./components/ModuleOff";
import SectionBoundary from "./components/SectionBoundary";
import Skeleton from "./components/Skeleton";
import type { RouteTab } from "./components/Tabs";
import TouchTitles from "./components/TouchTitles";
import { DataProvider, type LookOutcome, useData } from "./data/DataContext";
import { ManualAssetsDraftProvider } from "./data/useManualAssetsDraft";
import type { AccountMeta } from "./data/types";
import { useModules } from "./data/useModules";
import { FILTER_PARAM_KEYS } from "./filters/filters";
import { localOnlyLine } from "./lib/aiCard";
import { isReviewCandidate } from "./lib/answers";
import { storageKey } from "./lib/demo";
import { THEME_KEY } from "./lib/demoKeys";
import { setChartTheme } from "./lib/echarts";
import { FIRST_RUN_TITLE, firstRunState, type FirstRunState, replacedInFirstRun } from "./lib/firstRun";
import { countOf } from "./lib/format";
import { localFlagStore } from "./lib/localFlagStore";
import { type Facts, type Gate, gateOpen, type Modules, OpenRoutesProvider, type RouteOpen } from "./lib/modules";
import { usePointerWords } from "./lib/pointerWords";
import { PRODUCT_NAME } from "./lib/product";
import { usePrivacy, usePrivacyHotkey } from "./lib/usePrivacy";
import { useVocab } from "./lib/useVocab";

type Theme = "dark" | "light";
// the inline script in index.html applies the saved class before first paint;
// the html class is the source of truth (lib/echarts.ts self-initializes from
// it too, so chart tokens always agree). The store only remembers the choice for the next visit.
const initialTheme: Theme =
  document.documentElement.classList.contains("light") ? "light" : "dark";
const savedTheme = localFlagStore<Theme>(storageKey(THEME_KEY), (raw) => (raw === "light" ? "light" : "dark"), (t) => t);

const Trends = lazy(() => import("./sections/Trends"));
const Home = lazy(() => import("./sections/Home"));
const CashFlow = lazy(() => import("./sections/CashFlow"));
const IncomeStatement = lazy(() => import("./sections/IncomeStatement"));
const Budget = lazy(() => import("./sections/Budget"));
const Spending = lazy(() => import("./sections/Spending"));
const Transactions = lazy(() => import("./sections/Transactions"));
const Reports = lazy(() => import("./sections/Reports"));
const Recurring = lazy(() => import("./sections/Recurring"));
const Investments = lazy(() => import("./sections/Investments"));
const Balances = lazy(() => import("./sections/Balances"));
const Liabilities = lazy(() => import("./sections/Liabilities"));
const AccountPage = lazy(() => import("./sections/AccountPage"));
const Mortgage = lazy(() => import("./sections/Mortgage"));
const SecondHouse = lazy(() => import("./sections/SecondHouse"));
const Data = lazy(() => import("./sections/Data"));
const Settings = lazy(() => import("./sections/Settings"));
const Tools = lazy(() => import("./sections/Tools"));
const Review = lazy(() => import("./sections/Review"));
const FirstRun = lazy(() => import("./sections/FirstRun"));

// The single route manifest: sidebar NAV, the ⌘K palette, the mobile title, the <Route> tree
// and the hub tab bars all derive from this — add a page (or a hub tab) here and every surface
// picks it up. REDIRECTS below is the (empty) redirect table. tools/verify/screenshots_driver.py parses this
// block with a regex: keep one `{ to: "…"` per line and `];` only at the end of the array.
/** `aliases` (here and on a flat page or hub index): other names ⌘K finds a destination by, matched with its label and
 *  never shown. No page uses them today. `module` / `needs` (here and on a page, lib/modules.ts Gate): the page shows
 *  only while its module is on and the household has what it needs; otherwise its route shows components/ModuleOff.tsx
 *  and nothing leads into it (pagesFor). */
export interface PageTab extends Gate {
  path: string; label: string; icon: string; page: ComponentType; keepSearch?: RouteTab["keepSearch"]; aliases?: string[];
}
/** what a title needs to know about an account (the `:id` route is named by it) */
export type AccountName = Pick<AccountMeta, "id" | "name">;
export interface HubDef {
  /** label of the index tab (the hub's own route) */
  indexLabel: string;
  index: ComponentType;
  indexKeepSearch?: RouteTab["keepSearch"];
  indexAliases?: string[];
  tabs: PageTab[];
  /** routes rendered under the hub but never listed anywhere (nav, palette, tabs) — e.g. ":id". `title` names the
   *  route from its param for the top bar and the browser tab (undefined: the hub's own label) */
  extra?: { path: string; page: ComponentType; title?: (param: string, accounts: readonly AccountName[]) => string | undefined }[];
}
export interface PageDef extends Gate {
  to: string; label: string; icon: string;
  /** a flat page's component; a hub has none (HubShell is generic) */
  page?: ComponentType;
  hub?: HubDef;
  /** rendered and palette-listed but absent from the sidebar NAV (Review: footer badge) */
  navHidden?: true;
  /** ⌘K-only alternative names for a flat page (a hub's are on `indexAliases` and its tabs) */
  aliases?: string[];
}

export const PAGES: PageDef[] = [
  { to: "/", label: "Home", icon: "◉", page: Home },
  { to: "/cash-flow", label: "Cash Flow", icon: "⇅",
    hub: { indexLabel: "Overview", index: CashFlow, tabs: [
      { path: "income-statement", label: "Income statement", icon: "⇆", page: IncomeStatement },
      { path: "budget", label: "Budget", icon: "▦", page: Budget },
    ] } },
  { to: "/spending", label: "Spending", icon: "🛒",
    hub: { indexLabel: "Explore", index: Spending, indexKeepSearch: FILTER_PARAM_KEYS, tabs: [
      { path: "transactions", label: "Transactions", icon: "☰", page: Transactions, keepSearch: FILTER_PARAM_KEYS },
      { path: "recurring", label: "Recurring", icon: "↻", page: Recurring },
      { path: "reports", label: "Reports", icon: "▤", page: Reports, keepSearch: FILTER_PARAM_KEYS },
    ] } },
  { to: "/accounts", label: "Accounts", icon: "🏦",
    hub: { indexLabel: "Balances", index: Balances, tabs: [
      { path: "investments", label: "Investments", icon: "📈", page: Investments },
      { path: "liabilities", label: "Liabilities", icon: "💳", page: Liabilities },
    ], extra: [{ path: ":id", page: AccountPage, title: (id, accounts) => accounts.find((a) => a.id === id)?.name }] } },
  { to: "/planning", label: "Planning", icon: "🔮",
    hub: { indexLabel: "Projections", index: Tools, tabs: [
      // the tab is "House" so it never collides with the Home page; its path (/planning/home) is a bookmark and stays
      { path: "home", label: "House", icon: "🏠", page: Mortgage, needs: "mortgage" },
      { path: "second-house", label: "Second house", icon: "🏡", page: SecondHouse, module: "home_buying" },
    ] } },
  { to: "/trends", label: "Trends", icon: "📉", page: Trends },
  { to: "/data", label: "Data", icon: "⚙", page: Data },
  { to: "/settings", label: "Settings", icon: "🔧", page: Settings },
  // rendered, palette-listed, reachable from the sidebar footer badge — not a nav item
  { to: "/review", label: "Review", icon: "✓", page: Review, navHidden: true },
];

/** the sidebar's entries for `pages` (the shell passes pagesFor's): every page but the navHidden ones */
export const navFor = (pages: readonly PageDef[]) =>
  pages.filter((p) => !p.navHidden).map(({ to, label, icon }) => ({ to, label, icon }));
/** the sidebar with every page shown */
export const NAV = navFor(PAGES);

/** ⌘K entries for `pages` (the shell passes pagesFor's): flat pages (navHidden ones too) by label; every hub tab — the
 *  index tab included — as "Hub · Tab", so an "Overview"-style label never collides across hubs and each
 *  destination appears exactly once. Hub extras (param routes) are never listed. */
export function paletteFor(pages: readonly PageDef[]): { to: string; label: string; icon: string; aliases?: string[] }[] {
  return pages.flatMap((p) => {
    if (!p.hub) return [{ to: p.to, label: p.label, icon: p.icon, aliases: p.aliases }];
    return [
      { to: p.to, label: `${p.label} · ${p.hub.indexLabel}`, icon: p.icon, aliases: p.hub.indexAliases },
      ...p.hub.tabs.map((t) => ({ to: `${p.to}/${t.path}`, label: `${p.label} · ${t.label}`, icon: t.icon, aliases: t.aliases })),
    ];
  });
}
/** ⌘K with every page shown */
export const PALETTE = paletteFor(PAGES);

/** the title of a place, or null when no page owns the path: "Hub · Tab" on a tab, an account's own name on its page
 *  (`accounts` names it), the page label on a hub index, an unnamed extra or a flat page. */
function placeTitle(pathname: string, accounts: readonly AccountName[]): string | null {
  for (const p of PAGES) {
    const inside = p.to === "/" ? pathname === "/" : pathname === p.to || pathname.startsWith(`${p.to}/`);
    if (!inside) continue;
    const tab = p.hub?.tabs.find((t) => pathname === `${p.to}/${t.path}`);
    if (tab) return `${p.label} · ${tab.label}`;
    // a param route (/accounts/:id) is named by what its param points at; an unknown param falls back to the hub
    const rest = pathname.slice(p.to.length + 1);
    if (rest && !rest.includes("/")) {
      let param = rest;
      try { param = decodeURIComponent(rest); } catch { /* a malformed escape: look it up as written */ }
      for (const x of p.hub?.extra ?? []) {
        const named = x.path.startsWith(":") ? x.title?.(param, accounts) : undefined;
        if (named) return named;
      }
    }
    return p.label;
  }
  return null;
}

/** a page's routes, its own and its hub's tabs and extras, as patterns (`/accounts/:id` stays one) */
const routesOf = (p: PageDef): string[] => [p.to, ...(p.hub?.tabs ?? []).map((t) => `${p.to}/${t.path}`),
  ...(p.hub?.extra ?? []).map((x) => `${p.to}/${x.path}`)];
/** every route PAGES renders, as a pattern, in PAGES order */
export const ROUTE_PATTERNS: readonly string[] = PAGES.flatMap(routesOf);

const gateIn = ({ module, needs }: Gate): Gate => ({ ...(module && { module }), ...(needs && { needs }) });
/** the gate of `route`, one of `p`'s route patterns: the page's, and the tab's on a hub tab; null when it has none */
function gateWithin(p: PageDef, route: string): Gate | null {
  const tab = p.hub?.tabs.find((t) => `${p.to}/${t.path}` === route);
  const gate = { ...gateIn(p), ...(tab && gateIn(tab)) };
  return gate.module || gate.needs ? gate : null;
}
/** the gate of a route pattern (lib/modules.ts Gate): its page's, and its tab's on a hub tab; null for a route every
 *  household has */
export function moduleOf(route: string): Gate | null {
  const p = PAGES.find((x) => routesOf(x).includes(route));
  return p ? gateWithin(p, route) : null;
}
/** every route pattern a page is gated on */
export const GATED_ROUTES: readonly string[] = ROUTE_PATTERNS.filter((r) => moduleOf(r) !== null);

/** the route patterns whose page does not show for a household with `modules` and `facts`: each shows
 *  components/ModuleOff.tsx in place of its page */
export const closedRoutes = (modules: Modules, facts: Facts): string[] =>
  GATED_ROUTES.filter((r) => !gateOpen(moduleOf(r)!, modules, facts));

/** PAGES as a household with `modules` and `facts` sees them: without a page or a hub tab whose gate is closed. The
 *  sidebar (navFor), ⌘K (paletteFor), the hubs' tab bars and every in-app link (lib/modules.ts useRouteOpen) read it,
 *  so nothing leads into a page that does not show; the <Route> tree keeps every route. */
export function pagesFor(modules: Modules, facts: Facts): PageDef[] {
  const open = (g: Gate) => gateOpen(g, modules, facts);
  return PAGES.filter(open).map((p) => (p.hub ? { ...p, hub: { ...p.hub, tabs: p.hub.tabs.filter(open) } } : p));
}

/** whether an in-app path (a query or a hash after it ignored) leads to a page that shows, given the routes that do
 *  not (`closed`, closedRoutes) */
export const routeOpenIn = (closed: readonly string[]): RouteOpen => (path) => {
  const pathname = path.split(/[?#]/)[0];
  return !closed.some((r) => matchPath(r, pathname) !== null);
};

/** the first-run state the page at `pathname` shows: the household's (`firstRun`, lib/firstRun.ts) on a route the
 *  first-run page stands in for, null on Data, on Settings, on a path no page owns and for a household with data */
export function firstRunShownAt(pathname: string, firstRun: FirstRunState | null): FirstRunState | null {
  if (firstRun === null) return null;
  const route = ROUTE_PATTERNS.find((r) => matchPath(r, pathname) !== null);
  return route !== undefined && replacedInFirstRun(route) ? firstRun : null;
}

/** mobile top-bar title (and the browser tab's): see placeTitle; a path nobody owns reads "Family Financials", and the
 *  first-run page is named by its state ("Set up") wherever it stands in */
export function titleFor(pathname: string, accounts: readonly AccountName[] = [],
  firstRun: FirstRunState | null = null): string {
  const shown = firstRunShownAt(pathname, firstRun);
  if (shown !== null) return FIRST_RUN_TITLE[shown];
  return placeTitle(pathname, accounts) ?? PRODUCT_NAME;
}

/** the browser tab's title: the place, then the app ("Spending · Transactions · Family Financials"), so tabs and
 *  Back-history entries tell the pages apart. Home and an unknown path are the app's own name; the first-run page is
 *  its state, then the app ("Set up · Family Financials"), on Home too. No money in it. */
export function documentTitle(pathname: string, accounts: readonly AccountName[] = [],
  firstRun: FirstRunState | null = null): string {
  const base = PRODUCT_NAME;
  const shown = firstRunShownAt(pathname, firstRun);
  if (shown !== null) return `${FIRST_RUN_TITLE[shown]} · ${base}`;
  const place = pathname === "/" ? null : placeTitle(pathname, accounts);
  return place === null ? base : `${place} · ${base}`;
}

/** redirects from one path to another, rendered below the generated routes with the query preserved. The table is
 *  empty: an unknown path shows the catch-all page. App.test.tsx refuses an in-app hash link that points at a `from`.
 *  The screenshot driver never shoots these. */
export const REDIRECTS: [from: string, to: string][] = [];
/** a redirect whose target depends on a query key being present: { [from]: { [key]: to } }; empty with REDIRECTS */
export const PARAM_REDIRECTS: Record<string, Record<string, string>> = {};

export function RedirectKeepSearch({ to, byParam }: { to: string; byParam?: Record<string, string> }) {
  const { search } = useLocation();
  const sp = new URLSearchParams(search);
  const hit = byParam ? Object.entries(byParam).find(([key]) => sp.has(key)) : undefined;
  return <Navigate to={{ pathname: hit ? hit[1] : to, search }} replace />;
}

/** a sidebar row - the top-level pages and the Review link at the foot share it, so they cannot drift
 *  apart in height (44px on a phone: 12 + 20 + 12) or in how "you are here" looks */
export function navItemCls(isActive: boolean): string {
  return `flex items-center rounded-lg px-3 py-3 text-sm md:py-1.5 ${isActive
    ? "bg-zinc-800 font-medium text-zinc-100"
    : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"}`;
}

/** a hub's tab bar: its index tab, then each tab whose route is not `closed` */
function hubTabs(p: PageDef, closed: readonly string[]): RouteTab[] {
  const hub = p.hub!;
  return [
    // an extra route (/accounts/:id) is part of the index tab's section: Balances stays highlighted
    { to: p.to, label: hub.indexLabel, end: true, keepSearch: hub.indexKeepSearch,
      activeOn: (hub.extra ?? []).map((x) => `${p.to}/${x.path}`) },
    ...hub.tabs.filter((t) => !closed.includes(`${p.to}/${t.path}`))
      .map((t) => ({ to: `${p.to}/${t.path}`, label: t.label, keepSearch: t.keepSearch })),
  ];
}

/** `p`'s routes. `firstRun` is the household's first-run state (lib/firstRun.ts, null: the app): while it is set, every
 *  route the first-run page stands in for renders that page in place of its own, at the same address, inside the
 *  route's boundary and page testid, and a hub all of whose routes it stands in for drops its shell (h1 and tab bar),
 *  so the first-run page's h1 is the only one. `closed` are the routes whose page does not show (closedRoutes): each
 *  renders components/ModuleOff.tsx in its place the same way, the first-run page winning where both would stand in;
 *  a hub's tab bar leaves them out, and on one the hub drops its h1, so the module-off page's is the only one. */
export function pageRoute(p: PageDef, pathname: string, firstRun: FirstRunState | null = null,
  closed: readonly string[] = []) {
  const { to, page: Page, hub } = p;
  // every route element — the hub shell AND each index/tab/extra — gets its own boundary;
  // resetKey clears a caught error when the path changes (no remount of the shell). The
  // display:contents wrapper stamps the page/hub testid the smoke test asserts.
  const guard = (el: ReactNode, testId: string) => (
    <SectionBoundary resetKey={pathname}>
      <div className="contents" data-testid={testId}>{el}</div>
    </SectionBoundary>
  );
  const firstRunAt = (route: string) => firstRun !== null && replacedInFirstRun(route);
  /** the route's own page (named `label`), or the first-run page or the module-off page while one stands in for it */
  const page = (route: string, label: string, el: ReactNode) => {
    if (firstRunAt(route)) return <FirstRun state={firstRun!} />;
    const gate = closed.includes(route) ? gateWithin(p, route) : null;
    return gate ? <ModuleOff label={label} gate={gate} /> : el;
  };
  if (!hub) {
    if (!Page) throw new Error(`PAGES entry ${to} has neither page nor hub`);
    return <Route key={to} path={to} element={guard(page(to, p.label, <Page />), pageTestId(to))} />;
  }
  const Index = hub.index;
  // the route of this hub the address is on (a tab before an extra: /accounts/investments is no account id)
  const here = routesOf(p).find((r) => matchPath(r, pathname) !== null);
  const offHere = here !== undefined && closed.includes(here) && !firstRunAt(here);
  const shell = firstRun !== null && routesOf(p).every(replacedInFirstRun) ? <Outlet />
    : <HubShell title={p.label} tabs={hubTabs(p, closed)} heading={!offHere} />;
  return (
    <Route key={to} path={to} element={guard(shell, hubTestId(to))}>
      <Route index element={guard(page(to, p.label, <Index />), pageTestId(to))} />
      {hub.tabs.map(({ path, label, page: Tab }) => (
        <Route key={path} path={path}
          element={guard(page(`${to}/${path}`, label, <Tab />), pageTestId(`${to}/${path}`))} />
      ))}
      {(hub.extra ?? []).map(({ path, page: Extra }) => (
        <Route key={path} path={path} element={guard(page(`${to}/${path}`, p.label, <Extra />), pageTestId(`${to}/${path}`))} />
      ))}
    </Route>
  );
}

function Shell() {
  const { data, missing } = useData();
  const { pathname } = useLocation();
  // inbox badge: transactions that still want a category (answers already
  // applied via the overlay, so answering one drops the count immediately)
  const vocab = useVocab();
  const reviewCount = useMemo(
    () => data.transactions.filter((t) => isReviewCandidate(vocab, t)).length,
    [vocab, data.transactions]);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  // privacy mode: the money formatters mask while it is on; Alt+Shift+P toggles it from anywhere
  const [privacy, setPrivacy] = usePrivacy();
  // the way to jump anywhere: the top bar's 🔍 button for a finger on a phone, else ⌘K (lib/pointerWords.ts)
  const { searchButton } = usePointerWords();
  usePrivacyHotkey();
  const [navOpen, setNavOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [refreshOpen, setRefreshOpen] = useState(false);
  const toggleTheme = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.classList.toggle("light", next === "light");
    document.documentElement.classList.toggle("dark", next === "dark");
    savedTheme.set(next);          // a storage that refuses the write must not break the toggle
    setChartTheme(next);   // before the keyed remount re-renders the charts
    setTheme(next);
  };

  // a household whose exports hold nothing yet sees the first-run page in place of every page but Data and Settings
  const firstRun = useMemo(() => firstRunState(data.manifest, data), [data]);
  // the pages this household sees: a page whose module is off, or that needs what the household lacks, keeps its route
  // (the module-off page) and is left out of the sidebar, ⌘K, its hub's tab bar and every in-app link
  const { modules, facts } = useModules();
  const closed = useMemo(() => closedRoutes(modules, facts), [modules, facts]);
  const shown = useMemo(() => pagesFor(modules, facts), [modules, facts]);
  const nav = useMemo(() => navFor(shown), [shown]);
  const palette = useMemo(() => paletteFor(shown), [shown]);
  const routeOpen = useMemo(() => routeOpenIn(closed), [closed]);
  // current page title for the mobile top bar: "Hub · Tab" inside a hub, an account's name on its page, the first-run
  // state where that page stands in
  const title = titleFor(pathname, data.accounts, firstRun);
  // and for the browser tab / Back history, which would otherwise read "Family Financials" on every page
  useEffect(() => { document.title = documentTitle(pathname, data.accounts, firstRun); }, [pathname, data.accounts, firstRun]);

  // Esc closes the mobile nav drawer - unless a dialog above it already took this Escape: the focus trap
  // (useFocusTrap) preventDefaults the Escape it consumes, so ⌘K over the open drawer closes the palette
  // on the first Escape and the drawer on the next, not both at once. At phone width the drawer's own trap
  // (components/NavDrawer.tsx) closes it first; this covers a drawer left open across a widened window
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !e.defaultPrevented && setNavOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen]);

  return (
    <div className="flex min-h-screen">
      {/* Sidebar — a sticky rail on desktop, an off-canvas drawer on mobile (inert while closed, a dialog while open) */}
      <NavDrawer open={navOpen} onClose={() => setNavOpen(false)}
        className={`fixed inset-y-0 left-0 z-50 flex h-screen w-64 max-w-[80vw] shrink-0 flex-col border-r border-zinc-800 bg-zinc-950 p-3 transition-transform duration-200 print:hidden md:sticky md:inset-y-auto md:left-auto md:top-0 md:z-auto md:w-52 md:max-w-none md:translate-x-0 md:transition-none ${
          navOpen ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="mb-4 px-2">
          {/* the product's name, and the household's own under it; the household's name is never part of the browser
              tab's title */}
          <div className="mb-1">
            <div className="text-sm font-bold text-zinc-100">{PRODUCT_NAME}</div>
            <HouseholdName name={data.assumptions.household.name} />
          </div>
          {/* with a bank feed the pill says when it was last FETCHED (the INGEST time advances on every deploy and even
              when the fetch failed); with none, when the dashboard was last rebuilt */}
          <RefreshPill onClick={() => { setNavOpen(false); setRefreshOpen(true); }} />
        </div>
        <nav aria-label="Main" className="flex flex-col gap-0.5">
          {nav.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}
              onClick={() => setNavOpen(false)} className={({ isActive }) => navItemCls(isActive)}>
              <span className="mr-2 text-xs">{n.icon}</span>{n.label}
            </NavLink>
          ))}
        </nav>
        <button onClick={toggleTheme}
          className="mt-2 rounded-lg px-3 py-3 text-left text-sm text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200 md:py-1.5">
          <span className="mr-2 text-xs">{theme === "dark" ? "◐" : "◑"}</span>
          {theme === "dark" ? "Light mode" : "Dark mode"}
        </button>
        <button onClick={() => setPrivacy(!privacy)} aria-pressed={privacy} data-testid="privacy-toggle-nav"
          title="Hide every dollar amount on screen (Alt+Shift+P). CSV exports stay unmasked."
          className="rounded-lg px-3 py-3 text-left text-sm text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200 md:py-1.5">
          <span className="mr-2 text-xs">{privacy ? "◉" : "◌"}</span>{privacy ? "Show amounts" : "Hide amounts"}
        </button>
        <div className="mt-auto space-y-2">
          {/* the same item as the nav above (height, padding, active fill), just pinned to the foot */}
          <NavLink to="/review" onClick={() => setNavOpen(false)} className={({ isActive }) => navItemCls(isActive)}>
            <span className="mr-2 text-xs">✓</span>Review
            {reviewCount > 0 && (
              <span role="img" aria-label={`${reviewCount} to review`}
                className="ml-auto rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-amber-300">
                {reviewCount}
              </span>
            )}
          </NavLink>
          <div className="px-3 text-[10px] leading-relaxed text-faint">
            {/* one per purchase: a split purchase counts once */}
            {countOf(data.purchases.length, "transaction")}
            {/* a failed open_questions.json is a [] placeholder — never print its "0" */}
            {!missing.has("open_questions.json") && <> · {countOf(data.questions.length, "open question")}</>}
            <br />
            {searchButton ? "tap 🔍 to jump anywhere" : "⌘K to jump anywhere"}
            <br /><span data-testid="local-only-line">{localOnlyLine(data.aiStatus)}</span>
          </div>
        </div>
      </NavDrawer>
      {/* Drawer backdrop (mobile only) */}
      {navOpen && (
        <div className="fixed inset-0 z-40 bg-black/60 md:hidden" aria-hidden="true"
          onClick={() => setNavOpen(false)} />
      )}
      <CommandPalette nav={palette} open={paletteOpen} onOpenChange={setPaletteOpen} />
      {/* a finger's way to what a title alone says: a tap shows it */}
      <TouchTitles />
      <RefreshModal open={refreshOpen} onOpenChange={setRefreshOpen} />
      {/* the top bar is outside the keyed <main>: a privacy toggle there must keep its focus, and a header is not main content */}
      <div className="min-w-0 grow">
        {/* the demo build's banner, above every page and in the flow (nothing in any other build) */}
        <DemoBanner />
        {/* Mobile top bar — hamburger (carrying the review count), current page, freshness, privacy eye, jump-to search */}
        <MobileTopBar title={title} reviewCount={reviewCount} privacy={privacy} menuOpen={navOpen}
          onTogglePrivacy={() => setPrivacy(!privacy)} onOpenMenu={() => setNavOpen(true)}
          onOpenSearch={() => setPaletteOpen(true)}
          refresh={<RefreshPill onClick={() => setRefreshOpen(true)} bubble={false} />} notice={<RefreshUpdatedStrip />} />
        {/* key forces the chart tree to remount on theme change so every memoized ECharts option rebuilds with the
            swapped color tokens - and on a privacy toggle, so every option rebuilds with masked (or unmasked) axes */}
        {/* focusable by script alone (tabIndex -1): the module-off page hands focus here once the page it stood in for
            has taken its place */}
        <main key={`${theme}:${privacy ? "private" : "open"}`} tabIndex={-1} className="outline-none print:p-0">
          {/* a link into a page that may not show (GatedLink, Home's allocation slices) asks routeOpen whether it does */}
          <OpenRoutesProvider value={routeOpen}>
            <div className="p-3 sm:p-5 print:p-0">
              <Suspense fallback={<Skeleton variant="page" />}>
                <Routes>
                  {PAGES.map((p) => pageRoute(p, pathname, firstRun, closed))}
                  {/* pre-merge paths live on in bookmarks and old deep links */}
                  {REDIRECTS.map(([from, to]) => (
                    <Route key={from} path={from}
                      element={<RedirectKeepSearch to={to} byParam={PARAM_REDIRECTS[from]} />} />
                  ))}
                  {/* anything else (a stale bookmark, a typo) gets a card, not a blank page; deliberately
                      not a PAGES entry, so nav, ⌘K, the smoke loop and the screenshot driver never see it */}
                  <Route path="*" element={<PageMoved onSearch={() => setPaletteOpen(true)} />} />
                </Routes>
              </Suspense>
            </div>
          </OpenRoutesProvider>
        </main>
      </div>
    </div>
  );
}

/** what the app shows before any export exists (the loader's NothingBuiltError): the first-run page with every step
 *  still to take, outside the shell, since there is no data to build a sidebar or a title from. The provider looks for
 *  the exports again by itself (`quietLooks`: how many of those looks found nothing built); `checkAgain` looks at once
 *  and resolves to what it found. */
function NothingBuilt({ checkAgain, quietLooks }: { checkAgain: () => Promise<LookOutcome>; quietLooks: number }) {
  useEffect(() => { document.title = `${FIRST_RUN_TITLE["nothing-built"]} · ${PRODUCT_NAME}`; }, []);
  return (
    <main className="min-h-screen p-3 sm:p-5">
      <p className="mx-auto mb-3 max-w-2xl px-1 text-sm font-bold text-zinc-100">{PRODUCT_NAME}</p>
      <Suspense fallback={<Skeleton variant="page" />}>
        <FirstRun state="nothing-built" onCheckAgain={checkAgain} quietLooks={quietLooks} />
      </Suspense>
    </main>
  );
}

export default function App() {
  return (
    <HashRouter>
      <DataProvider nothingBuilt={(checkAgain, quietLooks) => <NothingBuilt checkAgain={checkAgain} quietLooks={quietLooks} />}>
        {/* one refresh watcher for the session: the pill's modal and the Data page share it */}
        <RefreshProvider>
          {/* the hand-entered assets' unsaved draft outlives a visit to another page */}
          <ManualAssetsDraftProvider>
            <Shell />
          </ManualAssetsDraftProvider>
        </RefreshProvider>
      </DataProvider>
    </HashRouter>
  );
}

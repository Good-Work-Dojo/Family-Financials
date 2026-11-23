// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// Mounts every route against a household's exports (charts stubbed out, since jsdom has no canvas) and asserts the
// capability manifest: each route shows its page testid and every card pinned to it (capabilities.ts), and none pinned
// elsewhere. Routes come from App.tsx's PAGES/REDIRECTS, never a hand list. The gate runs it on the demo household
// `full` (tools/demo): the names a test needs come from the generator's truth.json (test/truth.ts), never a literal,
// every figure from the exports or truth, and the clock is pinned at the ingest instant (test/demoClock.ts). A test
// whose expectation comes from the data first asserts that its subject is there, so it cannot pass on nothing. The
// suite runs on other households' data too, so a failing assertion never prints the rendered DOM: element queries use
// boolean asserts and getElementError is stripped to its message.
import { act, cleanup, configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import dayjs from "dayjs";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { closedRoutes, moduleOf, navItemCls, PAGES, REDIRECTS } from "./App";
import { CAPABILITIES, type Capability, pageTestId } from "./capabilities";
import { EXPORT_FILES, type CategoryRename, type ReservedNames, type Snapshot } from "./data/types";
import { presetRange } from "./filters/dateRange";
import { spendingLink } from "./filters/filters";
import { ANSWERS_LOCAL_KEY, isReviewCandidate as isReviewCandidateIn } from "./lib/answers";
import { categoryVocab, SHIPPED_RESERVED } from "./lib/categories";
import { TREEMAP_TILE_STAGE, tuneForMobile } from "./lib/echarts";
import { coverageFloor, dataFirstDay, dataThrough, monthInProgress, nextMonth, prevMonth, savingsRate } from "./lib/agg";
import { monthlyThrough } from "./lib/balanceCharts";
import { clock } from "./lib/clock";
import { comparableSnapshots, onOneFooting } from "./lib/comparableSnapshots";
import { EFUND_OVERRIDES_KEY } from "./lib/efundOverrides";
import { firstRunState } from "./lib/firstRun";
import { emergencySpendRows, emergencyWindow } from "./lib/emergency";
import { checkLabel } from "./lib/checkLabels";
import { fmtDate, fmtMonth, fmtMonthAxis, fmtPct, fmtUSD, fmtUSD0 } from "./lib/format";
import { incomeStreams, recentMonths } from "./lib/lifeSummary";
import { effectiveModules, factsOf } from "./lib/modules";
import { MANUAL_ASSETS_LOCAL_KEY } from "./lib/manualAssets";
import { isPrivate, MASK, PRIVACY_KEY, setPrivate } from "./lib/privacy";
import { PREFS_LOCAL_KEY } from "./lib/prefs";
import { ACTIVE_KEY as SCENARIO_ACTIVE_KEY, LOCAL_KEY as SCENARIOS_LOCAL_KEY } from "./lib/scenarioStore";
import { knownThroughFor, scheduleStatus } from "./lib/schedules";
import { SCROLL_TARGETS } from "./sections/planning/ForwardLookStrip";
import { ELSEWHERE } from "./sections/settings/ElsewhereCard";
import { freezeAtIngest, ingestInstant, thaw } from "./test/demoClock";
import { EXPORTS_DIR, HAVE_EXPORTS, requireExports } from "./test/exports";
import { missingCards } from "./test/matrixChecks";
import { optionHits, raw, STATIC_OK_PARTS, STATIC_SEEN, textHits } from "./test/moneyScan";
import { loadTruth, type Truth, truthPath } from "./test/truth";

// `clickName` / `clickValue` are what a click on any stubbed chart reports as the clicked series
// name / data point (a calendar cell reports its [date, spend] pair)
// `options` records every option object a stubbed chart was handed (an `it` clears it first), so a
// test can run the real phone tuning (tuneForMobile) over the option a page actually builds
// `pairs` records each option together with the `tableData` the same chart was handed (the "view data" table),
// so an `it` can prove the table is the chart's own data
const chart = vi.hoisted(() => ({
  clickName: "", clickValue: undefined as unknown, options: [] as any[],
  pairs: [] as { option: any; table: { columns: string[]; rows: (string | number | null)[][]; note?: string; csvName?: string; label?: string } | undefined }[],
}));
vi.mock("./components/EChart", () => ({
  default: ({ height, onClick, option, tableData }: { height?: number; option?: unknown; tableData?: any; onClick?: (p: { name: string; value?: unknown }) => void }) => {
    chart.options.push(option);
    chart.pairs.push({ option, table: tableData });
    return (
      <div data-testid="chart" style={{ height }}
        onClick={() => onClick?.({ name: chart.clickName, value: chart.clickValue })} />
    );
  },
}));
// jsdom has no matchMedia; Spending asks useIsMobile() for its chart label density. Desktop unless
// an `it` flips it (the phone cut is max-width: 767px, lib/useMediaQuery.ts).
const media = vi.hoisted(() => ({ phone: false }));
// The register is virtualized and jsdom has no layout, so the real virtualizer draws no rows: the per-route pass and
// the register `it`s read the counts the page shows at rest. An `it` that must OPEN a row sets `rows.all` before it
// mounts (and clears it after): the stand-in then draws every row, as the register's own unit tests do. The real hook
// is always called first, so the hook order of a mounted table never changes.
const rows = vi.hoisted(() => ({ all: false }));
vi.mock("@tanstack/react-virtual", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-virtual")>();
  const real = actual.useVirtualizer;
  return {
    ...actual,
    useVirtualizer: (opts: Parameters<typeof real>[0]) => {
      const v = real(opts);
      if (!rows.all) return v;
      return {
        getTotalSize: () => opts.count * 40,
        getVirtualItems: () => Array.from({ length: opts.count }, (_, index) => ({ index, start: index * 40, end: index * 40 + 40, size: 40, key: index, lane: 0 })),
        measureElement: () => {}, scrollToIndex: () => {},
      } as unknown as typeof v;
    },
  };
});

requireExports();
const DIR = EXPORTS_DIR + "/";
// The suite is written for the demo household `full`: it runs on exports that come with the generator's truth.json.
// A household's own exports (no truth beside them) skip it as no exports do, unless FF_REQUIRE_EXPORTS=1, under which
// a missing truth file is a load error.
const HAVE_DATA = HAVE_EXPORTS && (process.env.FF_REQUIRE_EXPORTS === "1" || existsSync(truthPath()));
const json = (name: string) => JSON.parse(readFileSync(DIR + name, "utf8"));
// the app's own category vocabulary, read through the served export's reserved names and renames (none today), as
// the pages read it (an export without either key gets loadAppData's default)
let served: ReturnType<typeof categoryVocab> | undefined;
const servedVocab = (a: { category_renames?: CategoryRename[]; reserved?: ReservedNames }) =>
  categoryVocab({ category_renames: a.category_renames ?? [], reserved: a.reserved ?? SHIPPED_RESERVED });
const vocab = () => (served ??= servedVocab(json("assumptions.json")));
const isReviewCandidate = (t: Parameters<typeof isReviewCandidateIn>[1]) => isReviewCandidateIn(vocab(), t);
/** The export's rows the way `loadAppData` hands them to every sum - the category view: a split parent is
 *  display-only and in no total, so an expectation computed from the raw file must skip it, or it drifts from
 *  the page the day a split rule has rows. */
const categoryView = (rows: any[]): any[] => rows.filter((t) => !t.is_parent);
// the generator's truth for the household: the names the suite needs (planted handles, the household, its accounts)
// and a few figures. Read once and only when the exports are present: without them every data test is skipped.
const TRUTH: Truth | null = HAVE_DATA ? loadTruth() : null;
const truth = (): Truth => TRUTH ?? loadTruth();
const planted = <K extends keyof Truth["planted"]>(handle: K): Truth["planted"][K] => truth().planted[handle];

/** Existence before comparison: the list a data-derived expectation reads is non-empty, or the test fails here
 *  instead of passing on nothing. `what` names the subject, never a value. */
function some(list: any, what: string): any[];                  // a parsed export's list (typed `any`) stays `any`
function some<T>(list: readonly T[], what: string): T[];
function some(list: readonly unknown[], what: string): unknown[] {
  expect(Array.isArray(list) && list.length > 0, `the exports hold ${what}`).toBe(true);
  return [...list];
}
/** the exported account a planted handle names (asserted to exist: its kind, name and owner agree with truth) */
function plantedAccount(handle: "seam.account" | "mortgage.account" | "brokerage.account" | "child.account" | "data_gap.account"): any {
  const id = planted(handle);
  const row = json("accounts.json").accounts.find((a: { id: string }) => a.id === id);
  const told = truth().accounts[id];
  expect(row !== undefined && told !== undefined && row.name === told.name && row.kind === told.kind && row.owner === told.owner,
    `${handle} is an exported account, as truth describes it`).toBe(true);
  return row;
}
/** a test that depends on which way the data goes asserts the way the household takes, so it cannot slip silently
 *  into the other branch on another run */
const branch = (taken: boolean, what: string) => expect(taken, `the household takes the branch: ${what}`).toBe(true);
/** the served exports' month in progress, by the one rule every page reads (lib/agg.ts monthInProgress); the household
 *  has one */
const exportsInProgress = (): string => {
  const m = monthInProgress({ transactions: categoryView(json("transactions.json").transactions),
    cashflow: json("cashflow_monthly.json").months, manifest: json("manifest.json") });
  expect(m !== null, "the exports hold a month in progress").toBe(true);
  return m!;
};
/** the emergency fund's spending rows as Planning builds them: the exports' rows over its window (lib/emergency.ts) */
const exportsEmergencyRows = () => {
  const txns = categoryView(json("transactions.json").transactions);
  return emergencySpendRows(vocab(), txns, emergencyWindow(json("cashflow_monthly.json").months, exportsInProgress(), dataFirstDay(txns)));
};
/** the export's snapshots split as the loader splits them: the comparable ones (every page's history) and the partial
 *  month ends left out. The household has partial month ends, and the split is truth's. */
function snapshotSplit(doc = json("assets_timeseries.json")): { comparable: any[]; partial: any[] } {
  const split = comparableSnapshots(some(doc.snapshots, "net-worth snapshots"));
  const dates = (xs: { snapshot_date: string }[]) => xs.map((s) => s.snapshot_date).join();
  expect(truth().figures.partial_month_ends.length > 0 && dates(split.partial) === truth().figures.partial_month_ends.join(),
    "the partial month ends are truth's (and there are some)").toBe(true);
  expect(dates(split.comparable.slice(0, -1)) === truth().figures.comparable_month_ends.join(), "the comparable month ends are truth's").toBe(true);
  return split;
}
/** the ways a snapshot date can be printed: ISO in a dense table, in words everywhere else */
const dateForms = (iso: string) => [iso, fmtDate(iso)];
/** none of the partial month ends is printed in `text` */
const noPartialDate = (text: string, partial: { snapshot_date: string }[]) =>
  partial.every((s) => dateForms(s.snapshot_date).every((f) => !text.includes(f)));

/** every page, hub tab and hub extra route (patterns: `/accounts/:id` stays a pattern here) */
function routesFromPages(): string[] {
  const out: string[] = [];
  for (const p of PAGES) {
    out.push(p.to);
    for (const t of p.hub?.tabs ?? []) out.push(`${p.to}/${t.path}`);
    for (const x of p.hub?.extra ?? []) out.push(`${p.to}/${x.path}`);
  }
  return out;
}
const ROUTE_PATTERNS = routesFromPages();
// A raw ISO date or year-month a person would read ("2030-10-31", "2030-10"): never glued to a word, a dot, a slash or a
// hyphen (a file name, an id) and not the start of an instant (a "T" follows). `isoDateHits` reads an element's text
// and its title, aria-label, placeholder and alt, leaving out the Transactions register (a dense register keeps the ISO
// column), and the charts' titles and series names; a hit names where it is, its digits redacted. Months go through
// fmtMonth as dates go through fmtDate: a pattern that knew only whole dates let "basis: 2030-04 → 2030-09" through.
const ISO_DATE = /(?<![\w./-])(?:19|20)\d{2}-(?:0[1-9]|1[0-2])(?:-\d{2})?(?![\w/]|-\d)/;
function isoDateHits(root: HTMLElement, options: any[]): string[] {
  const copy = root.cloneNode(true) as HTMLElement;
  copy.querySelectorAll('[role="table"][aria-label="Transactions"]').forEach((e) => e.remove());
  const where = (el: Element) => el.closest("[data-testid]")?.getAttribute("data-testid") ?? el.tagName.toLowerCase();
  const out: string[] = [];
  for (const el of [copy, ...copy.querySelectorAll("*")]) {
    const own = [...el.childNodes].filter((c) => c.nodeType === Node.TEXT_NODE).map((c) => c.textContent ?? "").join("");
    if (ISO_DATE.test(own)) out.push(`${where(el)}: text`);
    for (const a of ["title", "aria-label", "placeholder", "alt"]) {
      if (ISO_DATE.test(el.getAttribute(a) ?? "")) out.push(`${where(el)}: ${a}`);
    }
  }
  for (const o of options) {
    const titles = [o?.title].flat().filter(Boolean).flatMap((t: any) => [t.text, t.subtext]);
    const names = [o?.series].flat().filter(Boolean).map((s: any) => s.name);
    for (const s of [...titles, ...names]) if (typeof s === "string" && ISO_DATE.test(s)) out.push(`chart: ${s.replace(/\d/g, "#")}`);
  }
  return out;
}
// The rendered half of tests/test_page_copy.py: that scan reads the source and takes a literal with no space for an
// identifier, so a file name rendered on its own ({"accounts.json"}) would pass it. Here a page's rendered text and its
// title, aria-label, placeholder and alt name no export file, no storage key the app uses and no `make` target; a hit
// names where it is.
const MAKE_TARGETS: string[] = readFileSync(resolve(__dirname, "..", "..", "Makefile"), "utf8").match(/^[a-z][\w-]*(?=:)/gm) ?? [];
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const FILE_KEY_COMMAND = new RegExp([...EXPORT_FILES, PRIVACY_KEY, PREFS_LOCAL_KEY, MANUAL_ASSETS_LOCAL_KEY, ANSWERS_LOCAL_KEY,
  EFUND_OVERRIDES_KEY, SCENARIOS_LOCAL_KEY, SCENARIO_ACTIVE_KEY].map(escapeRe).join("|")
  + String.raw`|\bmake (?:${MAKE_TARGETS.map(escapeRe).join("|")})\b`);
function fileKeyHits(root: HTMLElement): string[] {
  const where = (el: Element) => el.closest("[data-testid]")?.getAttribute("data-testid") ?? el.tagName.toLowerCase();
  const out: string[] = [];
  for (const el of [root, ...root.querySelectorAll("*")]) {
    const own = [...el.childNodes].filter((c) => c.nodeType === Node.TEXT_NODE).map((c) => c.textContent ?? "").join("");
    const m = FILE_KEY_COMMAND.exec(own);
    if (m) out.push(`${where(el)}: text "${m[0]}"`);
    for (const a of ["title", "aria-label", "placeholder", "alt"]) {
      const am = FILE_KEY_COMMAND.exec(el.getAttribute(a) ?? "");
      if (am) out.push(`${where(el)}: ${a} "${am[0]}"`);
    }
  }
  // text split across elements ("make" in one, "ingest" in the next) still reads as one sentence
  const whole = FILE_KEY_COMMAND.exec(root.textContent ?? "");
  if (whole && out.length === 0) out.push(`page text "${whole[0]}"`);
  return out;
}
// the account id for `:id` routes: the household's joint checking (truth's `seam.account`: statement files, then the
// feed), an account with transactions and a ledger-drift figure, read once and only when the exports are present
const ACCOUNT_ID: string = TRUTH?.planted["seam.account"] ?? "none";
// the Reports tests' explicit window start: the first day of truth's coverage floor (the month by which most cash-flow
// accounts have data), a day inside the history with many monthly periods after it
const REPORT_FROM: string = TRUTH?.figures.coverage_floor ? `${TRUTH.figures.coverage_floor}-01` : "none";
/** REPORT_FROM is a day of the history with at least two whole months after its own before the end's month */
const reportWindow = (): string => {
  const months = Object.keys(truth().figures.months).sort();
  const start = REPORT_FROM.slice(0, 7);
  expect(months.includes(start) && months.filter((m) => m > start && m < truth().end.slice(0, 7)).length >= 2,
    "the report window starts inside the history, two whole months or more before the end").toBe(true);
  return REPORT_FROM;
};
const realize = (route: string) => route.replace(":id", ACCOUNT_ID);

// routes whose page (or one card of it) gates on recurring.json show the missing-data card by design. Exact in both
// directions: the "missing optional export" test asserts the card on every route listed here and
// that no other route shows one, and the manifest `it` below refuses an entry that is no route.
const RECURRING_GATED = new Set(["/", "/spending/recurring", "/planning"]);

beforeAll(() => {
  // every page reads the household's ingest instant as "now" (Date only: timers, and so Testing Library's waits, stay real)
  if (HAVE_DATA) freezeAtIngest();
  configure({ getElementError: (message) => new Error(message ?? "") });   // no DOM dump on a miss
  vi.stubGlobal("matchMedia", (query: string) => ({
    // the phone flag is a phone-width window: below md, and below the register's own lg / xl cuts too
    matches: media.phone && /max-width: (767|1023|1279)px/.test(query), media: query,
    addEventListener() {}, removeEventListener() {},
  }));
  (globalThis as any).ResizeObserver = class {
    observe() {} unobserve() {} disconnect() {}
  };
  vi.stubGlobal("fetch", async (url: string) => {
    const name = String(url).replace(/^\.\//, "");
    const body = readFileSync(DIR + name, "utf8");
    return new Response(body, { status: 200 });
  });
});

afterEach(cleanup);
afterAll(thaw);

// vitest's default 5s per-test timeout is shorter than the waits inside these tests, so every
// `it` that mounts a route carries an explicit timeout.
const ROUTE_TIMEOUT = 30_000;
const LOOP_TIMEOUT = 120_000;

/** the /api/scenarios document two `it`s serve: two saved second-house scenarios (synthetic, round
 *  numbers, no draws), so the worksheet renders against the real accounts without a running store */
const SYNTHETIC_SCENARIOS = (() => {
  const scenario = (id: string, name: string, price: number) => ({
    id, name, created_at: "2026-07-01T00:00:00Z", updated_at: "2026-07-01T00:00:00Z",
    inputs: { price }, draws: {} });
  return {
    version: 1, updated_at: "2026-07-01T00:00:00Z",
    scenarios: [scenario("t1", "Beach house", 650_000), scenario("t2", "Hill cabin", 400_000)],
  };
})();

async function mount(hash: string) {
  window.location.hash = hash;
  const { default: App } = await import("./App");
  render(<App />);
}

/** assert a query matched nothing without letting a failure print the matched element */
const absent = (el: Element | null, what: string) => expect(el === null, what).toBe(true);

/** the route settled: its page root is on screen, no skeleton, no boundary card */
async function settled(route: string) {
  await screen.findByTestId(pageTestId(route), {}, { timeout: 8000 });
  await waitFor(() => absent(document.querySelector('[aria-busy="true"]'), "still loading"), { timeout: 8000 });
  absent(screen.queryByText(/failed to render/), "a section fell back to the boundary card");
}

type Table = NonNullable<(typeof chart.pairs)[number]["table"]>;
/** the stubbed chart whose option satisfies `pick`: the option and the table it was handed (the table must exist) */
function chartWith(pick: (o: any) => boolean): { option: any; table: Table } {
  const hit = chart.pairs.find((p) => p.option && pick(p.option));
  expect(hit !== undefined, "the chart was built").toBe(true);
  expect(hit!.table !== undefined, "the chart carries a view-data table").toBe(true);
  expect(typeof hit!.table!.label === "string" && hit!.table!.label.length > 0, "the table names its chart (the toggle's accessible name)").toBe(true);
  return { option: hit!.option, table: hit!.table! };
}
/** one column of a table, keyed by its first cell (the category or date) */
function column(t: Table, name: string): Map<string, string | number | null> {
  const j = t.columns.indexOf(name);
  expect(j > 0, `the table has a ${name} column`).toBe(true);
  return new Map(t.rows.map((r) => [String(r[0]), r[j]]));
}
/** the cash-flow chart's greyed months: its income series' un-named mark-area bands (the named ones are pay phases) */
function greyMonths(income: any): Set<string> {
  return new Set<string>(((income.markArea?.data ?? []) as any[][]).filter((d) => d[0].name === undefined).map((d) => d[0].xAxis));
}
/** a series' points keyed like a table's rows: [date, v] pairs by date, a plain array by its category (a null point is a gap) */
function pointsOf(s: any, cats?: string[]): Map<string, number> {
  return new Map<string, number>((s.data as any[])
    .map((d, i): [string, number | null] => (Array.isArray(d) ? [String(d[0]), d[1]] : [cats![i], d]))
    .filter((e): e is [string, number] => e[1] !== null && e[1] !== undefined));
}
/** the table column named like `s` holds exactly the series' points - none missing, none extra, none invented */
function expectColumnIsSeries(t: Table, s: any, cats?: string[], tableName = s.name): void {
  const col = column(t, tableName);
  const pts = pointsOf(s, cats);
  const listed = [...col.entries()].filter(([, v]) => v !== null);
  const same = listed.length === pts.size && listed.every(([k, v]) => pts.get(k) === v);
  expect(same, `${tableName}: the table's cells are the series' points`).toBe(true);
}

/** a manifest row by id — an `it` mounts the row's own route, never a hand-typed lookup */
function rowFor(id: string): Capability {
  const row = CAPABILITIES.find((c) => c.id === id);
  expect(row !== undefined, `${id} is not in capabilities.ts`).toBe(true);
  return row!;
}
/** a `when` row: the dedicated `it`s below record each one they assert, and the last `it` of the
 *  file refuses a `when` row nobody asserted (the per-route loop skips them by design) */
const ASSERTED_WHEN = new Set<string>();
function whenRow(id: string): Capability {
  const row = rowFor(id);
  expect(row.when !== undefined, `${id} has no \`when\` in capabilities.ts`).toBe(true);
  return row;
}

describe.skipIf(!HAVE_DATA)("every route renders against the household's data", () => {
  it("the suite's clock is the ingest instant", () => {
    // whatever the machine's clock says: every "today" a page computes is the ingest day
    expect(new Date().toISOString(), "new Date() is the manifest's generated_at").toBe(ingestInstant().toISOString());
    expect(Date.now(), "Date.now() is the manifest's generated_at").toBe(ingestInstant().getTime());
  });

  it("the household is the demo generator's full profile, and its exports carry the accounts truth names", () => {
    expect(truth().profile, "the suite's expectations are written for the full household").toBe("full");
    const ids = some(json("accounts.json").accounts, "accounts").map((a: { id: string }) => a.id).sort();
    expect(ids.join() === Object.keys(truth().accounts).sort().join(), "the exported accounts are truth's").toBe(true);
  });

  it("the household has data: it is in no first-run state, and Home is its own page", async () => {
    const view = { transactions: categoryView(json("transactions.json").transactions),
      assets: { snapshots: json("assets_timeseries.json").snapshots }, balances: json("balances_timeseries.json") };
    expect(firstRunState(json("manifest.json"), view) === null, "the exports put the household in a first-run state").toBe(true);
    await mount("#/");
    await settled("/");
    absent(screen.queryByTestId("first-run"), "the first-run page stands in for Home");
  }, ROUTE_TIMEOUT);

  it("the household has every module on and a mortgage: every page shows, none is switched off", async () => {
    const assumptions = json("assumptions.json");
    const closed = closedRoutes(effectiveModules(assumptions.modules ?? {}, {}), factsOf(assumptions));
    expect(closed.length === 0, `the exports leave pages off: ${closed.join(", ")}`).toBe(true);
    const gated = ROUTE_PATTERNS.find((r) => moduleOf(r)?.module !== undefined);
    expect(gated !== undefined, "a module's page to open").toBe(true);
    await mount(`#${gated}`);
    await settled(gated!);
    absent(screen.queryByTestId("module-off"), `the module-off page stands in for ${gated}`);
  }, ROUTE_TIMEOUT);

  for (const route of ROUTE_PATTERNS) {
    const pinned = CAPABILITIES.filter((c) => c.route === route && !c.when);
    const elsewhere = CAPABILITIES.filter((c) => c.route !== route && !c.when);
    it(`renders ${route} with its ${pinned.length} capabilities`, async () => {
      // an empty pin list would leave only the page testid to check
      expect(CAPABILITIES.some((c) => c.route === route), `no manifest row for ${route}`).toBe(true);
      if (route.includes(":id")) plantedAccount("seam.account");
      await mount(`#${realize(route)}`);
      await settled(route);
      // with every export present, RequireData is invisible
      absent(screen.queryByTestId("missing-data"), "a RequireData gate is showing");
      for (const c of pinned) await screen.findByTestId(c.id, {}, { timeout: 8000 });
      // a move is a move, not a copy: nothing pinned to another route renders here
      // (boolean asserts: a failure must not dump the rendered DOM into the log)
      for (const c of elsewhere) {
        expect(screen.queryByTestId(c.id) === null, `${c.id} is pinned to another route`).toBe(true);
      }
      // the page's outline: its h1 first (the hub's or the page's own), and no heading more than one level below the one
      // before it (a card's title is an h2 under the h1)
      const levels = [...document.querySelectorAll("h1, h2, h3, h4, h5, h6")].map((h) => Number(h.tagName[1]));
      expect(levels[0] === 1, `${route}: the first heading is the h1 (${levels.join(" ")})`).toBe(true);
      expect(levels.every((l, i) => i === 0 || l <= levels[i - 1] + 1), `${route}: a heading skips a level (${levels.join(" ")})`)
        .toBe(true);
    }, ROUTE_TIMEOUT);
  }

  // ?pl= re-scopes the income statement's sankey + P&L (default window is TTM)
  it("the income statement re-scopes with ?pl= and defaults to the trailing 12 months", async () => {
    const route = "/cash-flow/income-statement";
    const sankeyText = async (hash: string) => {
      await mount(hash);
      await settled(route);
      await screen.findByTestId("card-pnl", {}, { timeout: 8000 });
      const text = (await screen.findByTestId("card-sankey", {}, { timeout: 8000 })).textContent ?? "";
      cleanup();
      return text;
    };
    // (boolean asserts: a failure must not dump the card's real dollar figures into the log)
    expect(/trailing 12 months/.test(await sankeyText(`#${route}`)), "default window").toBe(true);
    expect(/trailing 3 months/.test(await sankeyText(`#${route}?pl=3m`)), "?pl=3m window").toBe(true);
    // an unknown value is not a window: it falls back to the default instead of crashing
    expect(/trailing 12 months/.test(await sankeyText(`#${route}?pl=bogus`)), "?pl=bogus falls back to 12m").toBe(true);
  }, ROUTE_TIMEOUT);

  // the P&L's calendar-year column is the last calendar year whose twelve months the raw export holds, none of them
  // after the last full month - worked out here from the file, and absent when no year is whole
  it("the income statement's calendar-year column is the last full calendar year in the data", async () => {
    const route = "/cash-flow/income-statement";
    const months: string[] = some(json("cashflow_monthly.json").months, "cash-flow months").map((m: { month: string }) => m.month);
    const lastFull = prevMonth(exportsInProgress());
    const have = new Set(months);
    const whole = [...new Set(months.map((m) => m.slice(0, 4)))].filter((y) => `${y}-12` <= lastFull
      && Array.from({ length: 12 }, (_, i) => `${y}-${String(i + 1).padStart(2, "0")}`).every((m) => have.has(m)));
    const expected = whole.length > 0 ? whole.sort()[whole.length - 1] : null;
    branch(expected !== null, "the history holds a whole calendar year");
    await mount(`#${route}`);
    await settled(route);
    const card = await screen.findByTestId("card-pnl", {}, { timeout: 8000 });
    // a header is "<label><n> mo": its first text node is the label (boolean asserts: no figure reaches the log)
    const labels = within(card).getAllByRole("columnheader").map((h) => h.firstChild?.textContent ?? "");
    const years = labels.filter((l) => /^\d{4}$/.test(l));
    expect(expected === null ? years.length === 0 : years.length === 1 && years[0] === expected,
      "one calendar-year column, the last year the data holds whole").toBe(true);
  }, ROUTE_TIMEOUT);

  // no full month of cash flow: the income statement says so on its own card, not through the
  // error boundary (settled() refuses a "failed to render" card)
  it("income statement: with no cash-flow months it explains itself instead of crashing", async () => {
    const base = stubServe("cashflow_monthly.json", () => ({ months: [] }));
    try {
      await mount("#/cash-flow/income-statement");
      await settled("/cash-flow/income-statement");
      const card = await screen.findByTestId("card-sankey", {}, { timeout: 8000 });
      expect(/no full month of cash-flow data yet/.test(card.textContent ?? ""), "empty-state title").toBe(true);
      // the P&L keeps its card and says what it waits for: no table without a window
      const pnl = await screen.findByTestId("card-pnl", {}, { timeout: 8000 });
      expect(pnl.querySelector("table") === null, "no P&L table without a window").toBe(true);
      expect(/^No full month of cash flow yet\./.test(within(pnl).getByTestId("empty-state").textContent ?? ""), "the P&L's line").toBe(true);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // a ledger whose only months are the month in progress and the month a row was dated ahead into has no FULL month either
  it("income statement: a ledger holding only the month in progress (and a month dated ahead) shows the same empty state", async () => {
    const inProgress = exportsInProgress();
    const base = stubServe("cashflow_monthly.json", () => {
      const doc = json("cashflow_monthly.json");
      return { ...doc, months: doc.months.filter((m: { month: string }) => m.month >= inProgress) };
    });
    try {
      await mount("#/cash-flow/income-statement");
      await settled("/cash-flow/income-statement");
      const card = await screen.findByTestId("card-sankey", {}, { timeout: 8000 });
      expect(/no full month of cash-flow data yet/.test(card.textContent ?? ""), "empty-state title").toBe(true);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  it("the net-worth headline is the latest snapshot total, formatted", async () => {
    const snaps = some(json("assets_timeseries.json").snapshots, "net-worth snapshots");
    const latest = snaps[snaps.length - 1];
    await mount("#/");
    await settled("/");
    expect(screen.getAllByText(fmtUSD0(latest.total)).length).toBeGreaterThan(0);
  }, ROUTE_TIMEOUT);

  // The Net worth hero carries the 12-month delta, and the history chart card carries the per-snapshot table behind a
  // closed <details>. Home shows the snapshots comparable with the newest (lib/comparableSnapshots.ts): the partial month
  // ends, before every account reported, are on no chip, in no table row and in no † decision.
  it("home: the hero carries the 12-month delta and the snapshot table starts collapsed", async () => {
    const { comparable, partial } = snapshotSplit();
    await mount("#/");
    await settled("/");
    const kpis = await screen.findByTestId("kpis-home", {}, { timeout: 8000 });
    expect(/ vs [A-Z][a-z]{2} /.test(kpis.textContent ?? ""), "net worth sub-line names the prior snapshot").toBe(true);
    const history = await screen.findByTestId("card-networth-history", {}, { timeout: 8000 });
    const table = history.querySelector("details");
    expect(table !== null, "a snapshot <details> in the history card").toBe(true);
    expect(table!.open, "collapsed by default").toBe(false);
    expect((table!.querySelector("summary")?.textContent ?? "")
      === `${comparable.length} snapshots since ${comparable[0].snapshot_date.slice(0, 4)} — show the table`,
    "the summary counts the comparable snapshots, from the first one's year").toBe(true);
    expect(table!.querySelectorAll("tbody tr").length === comparable.length, "a table row per comparable snapshot").toBe(true);
    // a † snapshot omits the mortgage liability, so its net worth is too HIGH and the delta UNDERstates growth. The †
    // decision reads the snapshots on the page: the household's snapshots that leave the mortgage out are partial month
    // ends, so none is shown and there is no † and no footnote at all (the † paths are driven below)
    const preMortgage = comparable.some((s) => !s.mortgage_included);
    branch(!preMortgage && partial.some((s) => !s.mortgage_included),
      "the snapshots without the mortgage are partial month ends; every comparable one subtracts it");
    const page = document.body.textContent ?? "";
    expect(/real growth since those snapshots is better than the deltas show/.test(page), "no dagger footnote direction").toBe(false);
    expect(/overstate growth/.test(page), "no inverted dagger claim").toBe(false);
    absent(screen.queryByTestId("dagger-footnote"), "no dagger footnote when every snapshot shown subtracts the mortgage");
    expect(/†/.test(screen.getByTestId("card-snapshot-deltas").textContent ?? ""), "no † on any delta chip").toBe(false);
    expect(/don't subtract/.test(page), "no pre-mortgage note anywhere on Home").toBe(false);
    expect(noPartialDate(page, partial), "no partial month end is printed on Home").toBe(true);
  }, ROUTE_TIMEOUT);

  // A † marks a delta whose two snapshots disagree on the mortgage - and only those. Rewound (?snap=) to
  // a pre-mortgage snapshot, the marked chips are the ones that DO subtract it, the delta OVERstates
  // growth, and the footnote says so; the savings rate, which does not rewind, says it is the latest.
  // The page's snapshots are the comparable ones, so the pre-mortgage snapshots the test needs are made inside that set
  // (assetsWithPreMortgage); the partial month ends stay in the served file and off every chip.
  it("home: rewound to a pre-mortgage snapshot, the daggers mark the other side and say 'overstates'", async () => {
    const doc = assetsWithPreMortgage();
    const { comparable: snaps, partial } = snapshotSplit(doc);
    const pre = [...snaps].reverse().find((s) => !s.mortgage_included);
    expect(pre !== undefined && snaps.some((s) => s.mortgage_included), "the page's snapshots hold both kinds").toBe(true);
    const base = stubServe("assets_timeseries.json", () => doc);
    try {
      // the chips run newest first; the newest six show until "Show all" is pressed
      const chipsOf = () => {
        const strip = screen.getByTestId("card-snapshot-deltas");
        const more = within(strip).queryByRole("button", { name: /^Show all \d+$/ });
        if (more !== null) fireEvent.click(more);
        return [...strip.querySelectorAll("li")] as HTMLElement[];
      };
      const chipText = (chips: HTMLElement[]) => chips.map((c) => `${c.textContent ?? ""} ${c.title}`).join(" ");
      // latest view: † exactly on the pre-mortgage chips, footnote says "better than the deltas show"
      await mount("#/");
      await settled("/");
      const latestChips = chipsOf();
      const expectLatest = snaps.slice(0, -1).reverse().map((s) => !s.mortgage_included);
      expect(latestChips.length === snaps.length - 1, "latest: one chip per other comparable snapshot").toBe(true);
      expect(noPartialDate(chipText(latestChips), partial), "latest: no chip for a partial month end").toBe(true);
      expect(latestChips.map((c) => (c.textContent ?? "").includes("†")).join() === expectLatest.join(),
        "latest: † on each pre-mortgage chip only").toBe(true);
      expect(/better than the deltas show/.test(screen.getByTestId("dagger-footnote").textContent ?? ""), "latest footnote").toBe(true);
      expect(/· latest/.test(screen.getByTestId("kpi-savings-rate-ttm").textContent ?? ""), "no 'latest' tag at latest").toBe(false);
      cleanup();
      // rewound: † exactly on the chips that include the mortgage, titled with the reversed direction
      await mount(`#/?snap=${pre!.snapshot_date}`);
      await settled("/");
      const chips = chipsOf();
      const others = snaps.filter((s) => s.snapshot_date !== pre!.snapshot_date).reverse();
      expect(chips.length === others.length, "one chip per other snapshot").toBe(true);
      expect(noPartialDate(chipText(chips), partial), "rewound: no chip for a partial month end").toBe(true);
      expect(chips.map((c) => (c.textContent ?? "").includes("†")).join() === others.map((s) => s.mortgage_included).join(),
        "rewound: † on each mortgage-including chip only").toBe(true);
      expect(chips.filter((c) => (c.textContent ?? "").includes("†")).every((c) => /overstates real growth/.test(c.title)),
        "rewound titles say the delta overstates").toBe(true);
      const foot = screen.getByTestId("dagger-footnote").textContent ?? "";
      expect(/overstate real growth/.test(foot) && !/better than the deltas show/.test(foot), "rewound footnote direction").toBe(true);
      expect(/· latest/.test(screen.getByTestId("kpi-savings-rate-ttm").textContent ?? ""), "the savings rate says it stays on latest").toBe(true);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // Home keeps the data-quality card as a signal only: the warn list behind its toggle and a link to
  // Data. The questions, gaps, uncategorized line and learned rules live on Data (asserted there).
  // Two synthetic checks (one warn, one fail) join the real report, so the list is never empty and
  // the assertion cannot rot with what today's ingest happens to flag.
  // a count and its noun agree: one flagged check is "1 warning", one question "1 open question" - on Home's data-quality
  // line and in the sidebar footer (synthetic: every real check passes but one, and the questions are one synthetic row)
  it("home and the sidebar say '1 warning' and '1 open question', never '1 warnings' / '1 open questions'", async () => {
    const report = json("quality_report.json");
    report.checks = [...report.checks.map((c: { status: string }) => ({ ...c, status: "pass" })),
      { ...report.checks[0], name: "synthetic.warn_check", status: "warn", detail: "synthetic" }];
    const questions = { questions: [{ topic: "synthetic", question: "A synthetic question?", context: "synthetic" }], data_gaps: [] };
    const base = stubServe("quality_report.json", () => report);
    stubServe("open_questions.json", () => questions);
    try {
      await mount("#/");
      await settled("/");
      const card = await screen.findByTestId("card-data-quality", {}, { timeout: 8000 });
      const title = card.querySelector("h2")?.textContent ?? "";
      expect(/^Data quality — 1 warning · 1 open question · \d+ to review$/.test(title), "Home's line agrees its nouns").toBe(true);
      const footer = document.querySelector("aside")?.textContent ?? "";
      expect(/ 1 open question(?!s)/.test(footer) && /[\d,]+ transactions/.test(footer), "the sidebar footer agrees its nouns").toBe(true);
      expect(/\b1 (warnings|open questions|transactions|rows)\b/.test(document.body.textContent ?? ""), "no '1 <plural>' anywhere on Home").toBe(false);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  it("home: the data-quality card lists every flagged check by name, counts them as Data does, and points at Data", async () => {
    const report = json("quality_report.json");
    const check = (name: string, status: string) => ({ ...report.checks[0], name, status, detail: "synthetic, latest 2026-07-10" });
    report.checks = [...report.checks, check("synthetic.warn_check", "warn"), check("synthetic.fail_check", "fail")];
    const flagged: string[] = report.checks
      .filter((c: { status: string }) => c.status === "warn" || c.status === "fail")
      .map((c: { name: string }) => c.name);
    expect(flagged.length >= 2, "the synthetic checks are flagged").toBe(true);
    const base = stubServe("quality_report.json", () => report);
    try {
      await mount("#/");
      await settled("/");
      const card = await screen.findByTestId("card-data-quality", {}, { timeout: 8000 });
      // (boolean asserts: a failure must not dump the card's real warning text)
      expect(card.querySelector('a[href="#/data"]') === null, "collapsed by default: no body, no link").toBe(true);
      // (failure messages name a check by its index: real check names carry account-id slugs). A check is listed by its words
      // (checkLabel), its code in the tooltip - never the code as the row's text
      const accounts = json("accounts.json").accounts;
      const titled = (root: Element, name: string) => [...root.querySelectorAll("[title]")].some((e) => e.getAttribute("title") === name);
      flagged.forEach((name, i) => expect(titled(card, name), `flagged check #${i} hidden before the toggle`).toBe(false));
      fireEvent.click(within(card).getByRole("button", { name: "show" }));
      flagged.forEach((name, i) => expect(titled(card, name) && card.textContent?.includes(checkLabel(name, accounts)), `flagged check #${i} listed once expanded, by its label`).toBe(true));
      flagged.forEach((name, i) => expect(card.textContent?.includes(name), `flagged check #${i}'s code is not the row's text`).toBe(false));
      expect(card.querySelector('a[href="#/data"]') !== null, "a link to Data once expanded").toBe(true);
      // a check's detail is ETL prose: its ISO date reads the way the rest of the app words dates
      expect((card.textContent ?? "").includes(`synthetic, latest ${fmtDate("2026-07-10")}`) && !(card.textContent ?? "").includes("2026-07-10"), "Home words a warning's date").toBe(true);
      expect(card.querySelector("ol") === null, "the open-questions list left for Data").toBe(true);
      expect(card.querySelector("details") === null, "the data-gaps expander left for Data").toBe(true);
      expect(screen.queryByTestId("learned-rules") === null, "the learned rules left for Data").toBe(true);
      expect(/Uncategorized spending:/.test(card.textContent ?? ""), "the uncategorized line left for Data").toBe(false);
      const homeCount = Number(/Data quality — (\d+) warnings?/.exec(card.textContent ?? "")?.[1]);
      expect(homeCount, "Home's count is the flagged checks").toBe(flagged.length);
      // ...and the card's title carries the to-review count: the sidebar badge's number
      const homeReview = Number(/· (\d+) to review/.exec(card.textContent ?? "")?.[1]);
      const homePill = document.querySelector('aside a[href="#/review"] [role="img"]');
      expect(homePill !== null, "the badge pill").toBe(true);
      expect(homeReview, "Home's to-review count is the badge's number").toBe(
        Number(/^(\d+) to review$/.exec(homePill!.getAttribute("aria-label") ?? "")?.[1]));
      cleanup();
      // ...and Data's quality report counts the same checks
      await mount("#/data");
      await settled("/data");
      const quality = await screen.findByTestId("card-quality-report", {}, { timeout: 8000 });
      const dataCount = Number(/Quality report — (\d+) warnings? of \d+ checks/.exec(quality.textContent ?? "")?.[1]);
      expect(dataCount, "Data's count equals Home's").toBe(homeCount);
      expect((quality.textContent ?? "").includes(`synthetic, latest ${fmtDate("2026-07-10")}`) && !(quality.textContent ?? "").includes("2026-07-10"), "Data words a warning's date").toBe(true);
      flagged.forEach((name, i) => expect(titled(quality, name) && quality.textContent?.includes(checkLabel(name, accounts)), `flagged check #${i} on the quality report, by its label`).toBe(true));
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // A row is amber when the account's transactions stop before the last full month (the month before the data's last
  // day of data). Every account of the household reaches it, so the order is driven by serving one listed after the
  // everyday accounts (truth's child savings account) with its transactions ending in their first month: it alone is
  // amber, and it leads.
  it("the Data page's account-freshness links are 40px tap targets on a phone, in an uncapped list, stale rows first", async () => {
    const lagging = plantedAccount("child.account");
    const accounts = some(json("accounts.json").accounts, "accounts");
    const behind = `${prevMonth(exportsInProgress())}-01`;
    branch(accounts.every((a: { txn_data_end: string | null }) => a.txn_data_end === null || a.txn_data_end >= behind),
      "no account's transactions stop before the last full month");
    expect(lagging.txn_data_start !== null && lagging.txn_data_start < behind, "the lagging account's first month is before the last full one").toBe(true);
    expect(accounts.findIndex((a: { id: string }) => a.id === lagging.id) > 0, "the lagging account is not listed first").toBe(true);
    const base = stubServe("accounts.json", () => ({ ...json("accounts.json"), accounts: accounts.map((a: { id: string; txn_data_start: string | null }) =>
      (a.id === lagging.id ? { ...a, txn_data_end: a.txn_data_start } : a)) }));
    try {
      await mount("#/data");
      await settled("/data");
      const card = await screen.findByTestId("card-account-freshness", {}, { timeout: 8000 });
      const links = [...card.querySelectorAll("a")];
      expect(links.length === accounts.length, "one link per account").toBe(true);
      expect(links.every((a) => a.className.split(/\s+/).includes("min-h-[40px]")), "40px tap targets").toBe(true);
      // no inner scroll box at ANY width: at 1280 a capped list cut the stale row off below its fold with no cue
      const list = card.querySelector("ul")!.className.split(/\s+/);
      expect(list.some((c) => /max-h-|overflow-y-/.test(c)), "no nested scroll box").toBe(false);
      const items = [...card.querySelectorAll("li")];
      const amber = items.map((li) => li.querySelector(".text-amber-400") !== null);
      expect(amber.filter(Boolean).length === 1 && amber[0] && (items[0].textContent ?? "").includes(lagging.name),
        "the lagging account alone is amber, and it leads").toBe(true);
      expect(amber.lastIndexOf(true) < amber.indexOf(false), "every amber row leads").toBe(true);
      expect((card.textContent ?? "").includes("amber = transactions stop before the last full month (1 listed first)"), "the footnote counts it").toBe(true);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // The month ends the history leaves out are listed, newest first, each with the accounts it has no balance for that the
  // newest snapshot has (truth's partial month ends, their names off the export), at most five, then a count.
  it("the Data page lists each month end the history leaves out and what it misses", async () => {
    const { comparable, partial } = snapshotSplit();
    const newest = comparable[comparable.length - 1];
    const want = [...partial].reverse().map((s: { snapshot_date: string; missing: string[] }) => {
      const misses = s.missing.filter((n) => !newest.missing.includes(n));
      return `${fmtDate(s.snapshot_date)}: no balance yet for ${misses.slice(0, 5).join(", ")}`
        + (misses.length > 5 ? ` and ${misses.length - 5} more` : "");
    });
    await mount("#/data");
    await settled("/data");
    const caveats = await screen.findByTestId("card-caveats", {}, { timeout: 8000 });
    const items = [...within(caveats).getByTestId("caveat-left-out").querySelectorAll("li")].map((li) => li.textContent ?? "");
    expect(items.join("|") === want.join("|"), "each left-out month end, newest first, with what it misses").toBe(true);
    branch(newest.missing.length === 0, "the newest snapshot misses no account");
    expect(within(caveats).queryByTestId("caveat-never-reported") === null, "so no account is said to be waiting").toBe(true);
  }, ROUTE_TIMEOUT);

  // What the last ingest read: each statement file by its own name and the bank feed as one source over its pulls, read
  // off the quality report's per-file counts; no export file is named. Every source of the household read rows, so the
  // card is one summary line and the table waits behind its disclosure.
  it("the Data page says what the last ingest read, in one line, and file by file behind it", async () => {
    const files: { source_file: string; parser: string; rows_parsed: number }[] = some(json("quality_report.json").files, "per-file counts");
    const statements = files.filter((f) => f.parser !== "simplefin");
    const pulls = files.length - statements.length;
    branch(statements.length > 0 && pulls > 0 && files.every((f) => f.rows_parsed > 0), "statement files and feed pulls, each with rows");
    await mount("#/data");
    await settled("/data");
    const card = await screen.findByTestId("card-ingest-read", {}, { timeout: 8000 });
    const summary = within(card).getByTestId("ingest-read-summary");
    // no row total: overlapping bank-feed pulls carry the same rows, so a sum would count them more than once
    expect((summary.textContent ?? "").startsWith(`${statements.length + 1} sources read; none came back empty.`),
      "the summary line counts the sources").toBe(true);
    expect(within(card).queryByRole("table") === null, "the table waits behind the disclosure").toBe(true);
    fireEvent.click(within(summary).getByRole("button", { name: "Show the sources" }));
    const heads = [...card.querySelectorAll("tbody th")].map((th) => th.textContent ?? "");
    expect(heads.length === statements.length + 1, "one row per statement file and one for the bank feed").toBe(true);
    expect(statements.every((f) => heads.some((h) => h.startsWith(f.source_file))), "each statement file by its own name").toBe(true);
    expect(heads.includes(`Bank feed · ${pulls} pulls`), "the bank feed as one source, its pulls counted").toBe(true);
    expect(/\.json\b/.test(card.textContent ?? ""), "no export file is named").toBe(false);
  }, ROUTE_TIMEOUT);

  it("the Data page lists the learned rules", async () => {
    await mount("#/data");
    await settled("/data");
    const card = await screen.findByTestId("card-learned-rules", {}, { timeout: 8000 });
    // "None yet" or "N vendors" - both honest, and the card title is the only heading; the household's answers teach
    // a vendor, so it is the list, naming that vendor (boolean asserts: the list names vendors)
    const text = within(card).getByTestId("learned-rules").textContent ?? "";
    expect(/^(None yet — |\d+ vendors? categorized from your confirmed answers)/.test(text), "learned-rules content").toBe(true);
    branch(/^\d+ vendors? categorized/.test(text) && text.includes(planted("learned.vendor")), "the answers taught a vendor, and the list names it");
    expect(/Learned rules:/.test(card.textContent ?? ""), "no second heading under the card title").toBe(false);
  }, ROUTE_TIMEOUT);

  // The refresh readout is the modal's body rendered inline. Nothing here may POST: the button is
  // never clicked, and the stub refuses anything that is not the status GET.
  it("the Data page shows the refresh readout inline and only ever reads the refresh status", async () => {
    const base = globalThis.fetch;
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (String(url).replace(/^\.\//, "") === "api/refresh") {
        calls.push(init?.method ?? "GET");
        return new Response(JSON.stringify({ error: "refresh is not configured" }), { status: 503 });
      }
      return base(url as string, init);
    });
    try {
      await mount("#/data");
      await settled("/data");
      const card = await screen.findByTestId("card-freshness", {}, { timeout: 8000 });
      const text = card.textContent ?? "";
      // the feed's row only for a household with a feed (the manifest's setup says whether)
      const feed: boolean = json("manifest.json").setup.feed;
      for (const row of [...(feed ? ["Last feed fetch"] : []), "Dashboard rebuilt", "Transactions through", "Asset snapshot"]) {
        expect(text.includes(row), `the ${row} row`).toBe(true);
      }
      expect(text.includes("Last feed fetch"), "no feed row without a feed").toBe(feed);
      expect(within(card).queryByRole("dialog") === null, "inline, not a modal").toBe(true);
      await waitFor(() => expect(/No refresh service answers here \(refresh is not configured\)/.test(card.textContent ?? ""),
        "the server's own reason is shown").toBe(true), { timeout: 8000 });
      expect(within(card).queryByRole("button", { name: /Refresh now|Rebuild from files/ }) === null,
        "no refresh service: nothing to press").toBe(true);
      expect(calls.length > 0 && calls.every((m) => m === "GET"), "only the status GET was made").toBe(true);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // "Transactions through" printed the newest transaction date, which can be tomorrow (a pending charge's expected
  // settlement is a future-dated row). It is the ingest day at most, and counts what is dated beyond it. The expected
  // string is computed here from the export itself. The freshness card's footnote says "no account lags", not "(none)".
  it("the Data page's 'Transactions through' stops at the ingest day, and the freshness footnote is plain", async () => {
    const dates: string[] = some(categoryView(json("transactions.json").transactions), "transactions").map((t: { date: string }) => t.date);
    const newest = dates.reduce((m, d) => (d > m ? d : m), "0000");
    const ingestDay: string = json("manifest.json").generated_at.slice(0, 10);
    const through = newest > ingestDay ? ingestDay : newest;
    const later = dates.filter((d) => d > through).length;
    branch(later > 0 && newest === planted("future.date"), "a row is dated after the ingest day (truth's future-dated row is the newest)");
    await mount("#/data");
    await settled("/data");
    const fresh = await screen.findByTestId("card-freshness", {}, { timeout: 8000 });
    expect((fresh.textContent ?? "").includes(`Transactions through${fmtDate(through)}`), "the line reads the clamped date").toBe(true);
    // rows dated beyond it are counted on a line of their own, naming the latest date, so an account ending on that date is explained
    const ahead = `Dated ahead of that${later} row${later === 1 ? "" : "s"}, latest ${fmtDate(newest)}`;
    expect((fresh.textContent ?? "").includes(ahead), "the rows dated ahead are named").toBe(later > 0);
    const acct = await screen.findByTestId("card-account-freshness", {}, { timeout: 8000 });
    const text = acct.textContent ?? "";
    expect(text.includes("(none)"), "no '(none)' in the footnote").toBe(false);
    expect(/amber = transactions stop before the last full month(?: \(\d+ listed first\)| — no account lags)/.test(text), "plain footnote").toBe(true);
  }, ROUTE_TIMEOUT);

  // the footnote's other branch: with every account's transactions up to date, "(none)" used to read as an error code
  it("the freshness footnote says 'no account lags' when every account is up to date", async () => {
    const doc = json("accounts.json");
    doc.accounts = some(doc.accounts, "accounts").map((a: Record<string, unknown>) => ({ ...a, txn_data_end: a.txn_data_end === null ? null : "2999-12-31" }));
    const base = stubServe("accounts.json", () => doc);
    try {
      await mount("#/data");
      await settled("/data");
      const acct = await screen.findByTestId("card-account-freshness", {}, { timeout: 8000 });
      const text = acct.textContent ?? "";
      expect(text.includes("amber = transactions stop before the last full month — no account lags"), "plain wording").toBe(true);
      expect(text.includes("(none)"), "no '(none)'").toBe(false);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // each Data card is gated on the export it reads; the page keeps everything the missing file does
  // not feed (the freshness readout, the rules, the assumptions, the caveats)
  it("a missing quality / questions / accounts export gates only its own Data card", async () => {
    const base = stubMissing(["quality_report.json", "open_questions.json", "accounts.json"]);
    try {
      await mount("#/data");
      await settled("/data");
      await waitFor(() => expect(screen.getAllByTestId("missing-data").length).toBeGreaterThanOrEqual(3), { timeout: 8000 });
      const gates = screen.getAllByTestId("missing-data").map((c) => c.textContent ?? "");
      for (const what of ["the quality report", "the open questions", "the account list"]) {
        expect(gates.some((t) => t.includes(`${what} could not be loaded`)), `says ${what} could not be loaded`).toBe(true);
      }
      expect(gates.some((t) => /\.json/.test(t)), "a missing-data card names a file").toBe(false);
      for (const id of ["card-quality-report", "card-ingest-read", "card-open-questions", "card-account-freshness"]) {
        expect(screen.queryByTestId(id) === null, `${id} must not render without its export`).toBe(true);
      }
      for (const id of ["card-freshness", "card-learned-rules", "card-assumptions-in-force",
        "card-projection-defaults", "card-caveats"]) {
        expect(screen.queryByTestId(id) !== null, `${id} survives`).toBe(true);
      }
      expect(/\d+ open questions?|\d+ warnings?/.test(document.body.textContent ?? ""), "no fabricated count").toBe(false);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // the per-route pass exercises second-house's empty state (the api/scenarios fetch fails ->
  // localStorage fallback, no scenarios); this exercises the full worksheet
  it("second-house worksheet renders two saved scenarios against the household's accounts", async () => {
    const firstAccount = some(json("accounts.json").accounts, "accounts")[0].name;
    const base = stubServe("api/scenarios", () => SYNTHETIC_SCENARIOS);
    try {
      const rows = CAPABILITIES.filter((x) => x.when === "scenario");
      expect(rows.length > 0, "no `scenario` rows in capabilities.ts").toBe(true);
      const route = rows[0].route;
      expect(rows.every((r) => r.route === route), "scenario rows share one route").toBe(true);
      await mount(`#${route}`);
      await settled(route);
      await waitFor(() => expect(screen.getAllByText(/Beach house/).length).toBeGreaterThan(0), { timeout: 8000 });
      for (const c of rows) {
        await screen.findByTestId(c.id, {}, { timeout: 8000 });
        ASSERTED_WHEN.add(c.id);
      }
      // live account rows from the household's accounts.json export
      expect(screen.queryAllByText(firstAccount).length > 0, "the first account is a funding row").toBe(true);
      // the funding plan folds its Before column by the CARD's width (a container query), not the
      // viewport's: at xl it is a half-width column of the page and a viewport rule left "After" clipped
      const plan = screen.getByTestId("card-funding-plan");
      const scroller = plan.querySelector("table")!.parentElement!;
      expect(scroller.className.split(/\s+/).includes("@container"), "the table's scroller is the container").toBe(true);
      const before = [...plan.querySelectorAll("th")].find((th) => th.textContent === "Before")!;
      const beforeCls = before.className.split(/\s+/);
      expect(beforeCls.includes("@xl:table-cell") && beforeCls.includes("hidden"), "Before is a wide-card column").toBe(true);
      expect(beforeCls.some((c) => c.startsWith("md:") || c.startsWith("lg:") || c.startsWith("xl:")),
        "no viewport breakpoint on Before").toBe(false);
      // and below the `@sm` container width (a phone) the account name and the After figure give way and the Draw input
      // WIDENS to hold an 8-digit amount ("99,999,999" at 16px is ~100px; the old 80px box showed "99,999,9"), so Draw and After
      // stay on screen (classes: jsdom has no CSS; the 390 screenshot measures it against the rig's 99999999 fixture)
      const firstRow = plan.querySelector("tbody tr")!;
      const nameCell = firstRow.querySelector("td")!;
      const drawInput = firstRow.querySelector("input")!;
      const afterCell = [...firstRow.querySelectorAll("td")].pop()!;
      expect(nameCell.className.split(/\s+/).includes("@max-sm:max-w-[6rem]"), "the account name narrows in a phone-width card").toBe(true);
      const drawCls = drawInput.className.split(/\s+/);
      expect(drawCls.includes("@max-sm:w-28") && !drawCls.includes("@max-sm:w-20"), "the Draw input is 112px in a phone-width card (8 digits fit)").toBe(true);
      expect(afterCell.className.split(/\s+/).includes("@max-sm:pr-1"), "the After gutter tightens in a phone-width card").toBe(true);
      expect(afterCell.className.split(/\s+/).includes("@max-sm:text-xs"), "and the After figure is 12px, so a 9-digit balance still fits beside it").toBe(true);
      // the narrow name column WRAPS a name (two lines on a phone) instead of cutting it: two accounts of one bank
      // (two card names that differ only after "...") truncated to the same prefix
      const nameCls = (nameCell.firstElementChild as HTMLElement).className.split(/\s+/);
      expect(!nameCls.includes("truncate") && nameCls.includes("[overflow-wrap:anywhere]"), "the account name wraps, never truncates").toBe(true);
      expect(nameCls.includes("@max-sm:text-xs"), "12px in a phone-width card, so most names take two lines").toBe(true);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // the `drill` when: the stubbed chart reports the series name a test sets, so a click on the
  // top-vendors card's chart opens the panel exactly as the real chart's bar click does
  it("the vendor drill opens from a click on the top-vendors chart", async () => {
    const drill = whenRow("card-vendor-drill");
    const vendor = categoryView(json("transactions.json").transactions)
      .find((t: { type: string; vendor: string | null }) => t.type === "purchase" && t.vendor)?.vendor;
    expect(typeof vendor === "string", "a purchase vendor to drill into").toBe(true);
    chart.clickName = vendor;
    try {
      await mount(`#${drill.route}`);
      await settled(drill.route);
      const card = await screen.findByTestId("card-top-vendors", {}, { timeout: 8000 });
      fireEvent.click(within(card).getByTestId("chart"));
      await screen.findByTestId(drill.id, {}, { timeout: 8000 });
      ASSERTED_WHEN.add(drill.id);
    } finally {
      chart.clickName = "";
    }
  }, ROUTE_TIMEOUT);

  /** the household's monthly subscription (truth), asserted to be an active recurring item of the export */
  const subscription = (): string => {
    const vendor = planted("subscriptions.monthly");
    expect(some(json("recurring.json").items, "recurring items")
      .some((r: { vendor: string; active: boolean }) => r.vendor === vendor && r.active), "the monthly subscription is an active recurring item").toBe(true);
    return vendor;
  };
  /** the vendor button of `vendor` in the recurring card's phone list (`ul`) or table */
  const vendorButtonIn = (card: Element, container: "ul" | "table", vendor: string) => Array.from(card.querySelectorAll(`${container} button`))
    .find((b) => b.className.includes("font-medium") && b.textContent === vendor);

  // the same panel opens from Recurring's vendor buttons (DOM buttons, no chart) - in the
  // phone list and in the desktop table alike (jsdom applies no media query, so both are mounted)
  it("the vendor drill also opens from a recurring vendor button, in the phone list and in the table", async () => {
    const vendor = subscription();
    const route = rowFor("card-recurring-table").route;
    for (const container of ["ul", "table"] as const) {
      await mount(`#${route}`);
      await settled(route);
      const card = await screen.findByTestId("card-recurring-table", {}, { timeout: 8000 });
      // (boolean asserts: a role miss would print the accessible names - the household's vendor names)
      const vendorButton = vendorButtonIn(card, container, vendor);
      expect(vendorButton !== undefined, `the subscription's vendor button in the ${container}`).toBe(true);
      fireEvent.click(vendorButton!);
      await screen.findByTestId("card-vendor-drill", {}, { timeout: 8000 });
      cleanup();
    }
  }, ROUTE_TIMEOUT);

  // Recurring has no FilterBar to filter in place, so its drill's "filter to vendor" used to just close
  // the drill: it now opens that vendor's rows on Transactions (and is a 40px tap target on a phone)
  it("Recurring's vendor drill 'filter to vendor' opens that vendor on the Transactions tab", async () => {
    const vendor = subscription();
    await mount("#/spending/recurring");
    await settled("/spending/recurring");
    const card = await screen.findByTestId("card-recurring-table", {}, { timeout: 8000 });
    const vendorButton = vendorButtonIn(card, "table", vendor);
    expect(vendorButton !== undefined, "the subscription's vendor button").toBe(true);
    fireEvent.click(vendorButton!);
    const drill = await screen.findByTestId("card-vendor-drill", {}, { timeout: 8000 });
    const filter = within(drill).getByRole("button", { name: "filter to vendor" });
    expect(filter.className.split(/\s+/).includes("min-h-[40px]"), "a 40px tap target on a phone").toBe(true);
    fireEvent.click(filter);
    await settled("/spending/transactions");
    await screen.findByTestId("card-transactions", {}, { timeout: 8000 });
    const sp = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
    expect(window.location.hash.startsWith("#/spending/transactions?"), "on the Transactions tab").toBe(true);
    expect(sp.get("vendors") === vendor, "filtered to the drilled vendor").toBe(true);
    expect(screen.queryByTestId("card-vendor-drill") === null, "the drill closed").toBe(true);
  }, ROUTE_TIMEOUT);

  // Explore's FilterBar count gets a way to those exact rows: the link carries the filters to the
  // Transactions tab and marks the hand-over, so even a cleared (all-time) state lands on the same N
  it("Explore's 'view these N transactions' link lands on the same N rows on Transactions", async () => {
    // the FilterBar's "N of M transactions" line (a regex over the span, never printed)
    const countOf = () => {
      const span = Array.from(document.querySelectorAll("span"))
        .find((x) => /^[\d,]+ of [\d,]+ transactions?/.test(x.textContent ?? ""));
      return Number((/^([\d,]+)/.exec(span?.textContent ?? "")?.[1] ?? "-1").replace(/,/g, ""));
    };
    for (const clear of [false, true]) {
      await mount("#/spending?categories=Dining");
      await settled("/spending");
      if (clear) fireEvent.click(screen.getByText("Clear all"));
      await quiet();
      const link = Array.from(document.querySelectorAll("a")).find((a) => /^view these [\d,]+ transactions →$/.test(a.textContent ?? ""));
      expect(link !== undefined, "the link beside the count").toBe(true);
      const n = countOf();
      expect(Number((/[\d,]+/.exec(link!.textContent ?? "")?.[0] ?? "").replace(/,/g, "")) === n, "it names the count").toBe(true);
      expect(link!.getAttribute("href")!.startsWith("#/spending/transactions"), "it targets the Transactions tab").toBe(true);
      fireEvent.click(link!);
      await settled("/spending/transactions");
      await quiet();
      expect(countOf() === n, `${clear ? "cleared" : "filtered"}: the same rows on Transactions`).toBe(true);
      cleanup();
    }
  }, ROUTE_TIMEOUT);

  // the phone list is for thumbs: the vendor button and the txns link are 40px tap targets
  it("the Recurring phone list's vendor button and txns link are 44px tap targets", async () => {
    subscription();
    await mount("#/spending/recurring");
    await settled("/spending/recurring");
    const card = await screen.findByTestId("card-recurring-table", {}, { timeout: 8000 });
    const first = card.querySelector("ul li");
    expect(first !== null, "a phone-list row").toBe(true);
    const vendor = first!.querySelector("button");
    const txns = Array.from(first!.querySelectorAll("a")).find((a) => /txns/.test(a.textContent ?? ""));
    expect(vendor?.className.split(/\s+/).includes("min-h-11"), "vendor button").toBe(true);
    expect(["min-h-11", "min-w-11"].every((c) => txns?.className.split(/\s+/).includes(c)), "txns link").toBe(true);
  }, ROUTE_TIMEOUT);

  /** the mortgage's recurring item: the series of the planted mortgage payment (its vendor on its account), a promoted
   *  monthly schedule */
  const mortgageItem = (): any => {
    const payment = json("transactions.json").transactions.find((t: { id: string }) => t.id === planted("mortgage.payment"));
    expect(payment !== undefined, "the planted mortgage payment is an exported row").toBe(true);
    const item = some(json("recurring.json").items, "recurring items")
      .find((r: { vendor: string; account_id: string }) => r.vendor === payment.vendor && r.account_id === payment.account);
    expect(item !== undefined && item.cadence === "monthly" && item.schedule === true, "the mortgage is a promoted monthly schedule").toBe(true);
    return item;
  };
  /** the first Monday-to-Friday of the month after `day`'s */
  const firstBusinessDayAfter = (day: string): string => {
    let d = dayjs(day).add(1, "month").startOf("month");
    while (d.day() === 0 || d.day() === 6) d = d.add(1, "day");
    return d.format("YYYY-MM-DD");
  };

  // the household pays its mortgage on the first business day of every month, so the next payment is expected on the
  // next month's first business day - not a day count after the last one - and on the household's last day it is
  // upcoming, on Recurring and on Home alike: not due, not late
  it("the mortgage is expected on the first business day after the data's end, and is not late on its last day", async () => {
    const item = mortgageItem();
    const expected = firstBusinessDayAfter(truth().end);
    expect(item.next_expected === expected && item.expected_dates[0] === expected,
      "expected on the first business day of the month after the end").toBe(true);
    const today = clock.today(json("assumptions.json").household.timezone);
    expect(today === truth().end, "the suite's day is the household's last day").toBe(true);
    expect(scheduleStatus(item, today, knownThroughFor(json("accounts.json").accounts, item.account_id)) === "upcoming",
      "upcoming: not due, missed or stale").toBe(true);
    const chip = `upcoming ${fmtDate(expected)}`;
    for (const [route, id] of [["/spending/recurring", "card-recurring-table"], ["/", "card-next-30-days"]] as const) {
      await mount(`#${route}`);
      await settled(route);
      const card = await screen.findByTestId(id, {}, { timeout: 8000 });
      const rows = [...card.querySelectorAll("li, tr")].filter((r) => (r.textContent ?? "").includes(item.vendor));
      expect(rows.length > 0 && rows.every((r) => (r.textContent ?? "").includes(chip)), `${id}: the mortgage's row reads upcoming`).toBe(true);
      cleanup();
    }
  }, ROUTE_TIMEOUT);

  // a monthly item's monthly figure is its own amount, not its amount scaled from a day count
  it("a monthly item's monthly figure is its amount, in the export and in Recurring's table", async () => {
    const monthly = some(json("recurring.json").items.filter((r: { cadence: string }) => r.cadence === "monthly"), "monthly recurring items");
    expect(monthly.every((r: { monthly_equivalent: number; typical_amount: number }) => r.monthly_equivalent === r.typical_amount),
      "every monthly item's monthly figure is its amount").toBe(true);
    const item = mortgageItem();
    await mount("#/spending/recurring");
    await settled("/spending/recurring");
    const card = await screen.findByTestId("card-recurring-table", {}, { timeout: 8000 });
    const row = [...card.querySelectorAll("tr")].find((r) => r.querySelector("button")?.textContent === item.vendor);
    expect(row !== undefined, "the mortgage's table row").toBe(true);
    const cells = [...row!.querySelectorAll("td")].map((td) => td.textContent);
    expect(cells[2] === fmtUSD(item.typical_amount) && cells[3] === fmtUSD(item.typical_amount),
      "its Typical and Monthly eq. columns print one amount").toBe(true);
  }, ROUTE_TIMEOUT);

  // the next-12-months chart: twelve bars, the first the month after the month in progress, a monthly bill in each
  it("the obligations chart has twelve bars from the month after the month in progress, the mortgage in every one", async () => {
    const item = mortgageItem();
    chart.options = [];
    await mount("#/spending/recurring");
    await settled("/spending/recurring");
    await screen.findByTestId("card-obligations", {}, { timeout: 8000 });
    const option = chart.options.find((o) => o?.series?.[0]?.name === "Routine");
    expect(option !== undefined, "the obligations chart was built").toBe(true);
    const months: string[] = option.xAxis.data;
    expect(months.length === 12 && months[0] === nextMonth(exportsInProgress()), "twelve months, from the one after the month in progress").toBe(true);
    expect((option.series[0].data as number[]).every((v) => v >= Math.floor(item.typical_amount)), "the monthly mortgage is in every bar").toBe(true);
  }, ROUTE_TIMEOUT);

  // Spending's FilterBar state is the URL; Explore and Transactions share it (keepSearch), Recurring
  // reads ?from/&to/&win as its own window and must not inherit Explore's filters
  it("the Spending filter state survives Explore -> Transactions and does not reach Recurring", async () => {
    await mount("#/spending?categories=Dining");
    await settled("/spending");
    const tabs = screen.queryByRole("navigation", { name: "Spending sections" });
    expect(tabs !== null, "the Spending tab bar").toBe(true);
    const tab = (name: string) => within(tabs!).queryByRole("link", { name });
    expect(tab("Transactions")?.getAttribute("href"), "Transactions carries the filters")
      .toBe("#/spending/transactions?categories=Dining");
    expect(tab("Recurring")?.getAttribute("href"), "Recurring carries nothing").toBe("#/spending/recurring");
    fireEvent.click(tab("Transactions")!);
    await settled("/spending/transactions");
    expect(window.location.hash).toBe("#/spending/transactions?categories=Dining");
    await screen.findByTestId("card-transactions");
    // and back: Explore keeps the same filters
    const nav = screen.queryByRole("navigation", { name: "Spending sections" });
    expect(nav !== null, "the tab bar on Transactions").toBe(true);
    expect(within(nav!).queryByRole("link", { name: "Explore" })?.getAttribute("href"), "Explore carries the filters")
      .toBe("#/spending?categories=Dining");
  }, ROUTE_TIMEOUT);

  /** let any mount effect run (a default that should NOT fire must have had its chance) */
  const quiet = () => act(async () => { await new Promise((r) => setTimeout(r, 50)); });
  const spendingTab = (name: string) => {
    const nav = screen.queryByRole("navigation", { name: "Spending sections" });
    expect(nav !== null, "the Spending tab bar").toBe(true);
    const link = within(nav!).queryByRole("link", { name });
    expect(link !== null, `the ${name} tab`).toBe(true);
    return link!;
  };
  const buttonLabelled = (label: string) => {
    const b = Array.from(document.querySelectorAll("button")).find((x) => x.textContent === label);
    expect(b !== undefined, `a "${label}" button`).toBe(true);
    return b!;
  };

  // The six-month default is for a FRESH entry. A cleared filter bar and the All preset both have an
  // empty query, and neither may be mistaken for a fresh visit by the tab it is handed to.
  it("a cleared Explore stays cleared across the switch to Transactions", async () => {
    await mount("#/spending?categories=Dining");
    await settled("/spending");
    fireEvent.click(screen.getByText("Clear all"));
    await waitFor(() => expect(spendingTab("Transactions").getAttribute("href")).toBe("#/spending/transactions"));
    fireEvent.click(spendingTab("Transactions"));
    await settled("/spending/transactions");
    await quiet();
    expect(window.location.hash, "no default window on a cleared state").toBe("#/spending/transactions");
  }, ROUTE_TIMEOUT);

  it("the All preset on Explore stays all-time across the switch to Transactions", async () => {
    await mount("#/spending?from=2025-01-01");
    await settled("/spending");
    fireEvent.click(buttonLabelled("All"));
    await waitFor(() => expect(spendingTab("Transactions").getAttribute("href")).toBe("#/spending/transactions"));
    fireEvent.click(spendingTab("Transactions"));
    await settled("/spending/transactions");
    await quiet();
    expect(window.location.hash, "no default window after All").toBe("#/spending/transactions");
    // and back again: Explore does not re-default either
    fireEvent.click(spendingTab("Explore"));
    await settled("/spending");
    await quiet();
    expect(window.location.hash, "Explore keeps all-time").toBe("#/spending");
  }, ROUTE_TIMEOUT);

  // Recurring's presets live in `win`, which is not a filter key: it must stay behind, and Transactions
  // (entered from a tab that shares nothing) is a fresh visit that gets the default window
  it("a Recurring preset does not follow into Transactions, which opens on the default window", async () => {
    await mount("#/spending/recurring");
    await settled("/spending/recurring");
    fireEvent.click(buttonLabelled("12M"));
    await waitFor(() => expect(window.location.hash).toBe("#/spending/recurring?win=12m"));
    expect(spendingTab("Transactions").getAttribute("href"), "win is not carried").toBe("#/spending/transactions");
    expect(spendingTab("Explore").getAttribute("href"), "win is not carried").toBe("#/spending");
    fireEvent.click(spendingTab("Transactions"));
    await settled("/spending/transactions");
    await waitFor(() => expect(/^#\/spending\/transactions\?from=\d{4}-\d{2}-\d{2}$/.test(window.location.hash),
      "the default window and no win").toBe(true), { timeout: 8000 });
  }, ROUTE_TIMEOUT);

  // ...whereas a custom Recurring window is plain from/to, which the filter bar reads too
  it("a custom Recurring window (from/to) follows into Explore and Transactions", async () => {
    await mount("#/spending/recurring?from=2025-01-01&to=2025-02-01");
    await settled("/spending/recurring");
    expect(spendingTab("Transactions").getAttribute("href")).toBe("#/spending/transactions?from=2025-01-01&to=2025-02-01");
    expect(spendingTab("Explore").getAttribute("href")).toBe("#/spending?from=2025-01-01&to=2025-02-01");
  }, ROUTE_TIMEOUT);

  // a bare URL on either filter tab lands on the same six-month window (useSpendingDefaults), and
  // the Transactions register is a tall scroller (viewport-relative), not Explore's old 480px box
  it("a bare Explore or Transactions URL defaults to the same last-six-months window", async () => {
    const froms: string[] = [];
    for (const route of ["/spending", "/spending/transactions"]) {
      await mount(`#${route}`);
      await settled(route);
      await waitFor(() => expect(/^#\/spending(\/transactions)?\?from=\d{4}-\d{2}-\d{2}$/.test(window.location.hash),
        `${route} writes its default window`).toBe(true), { timeout: 8000 });
      froms.push(window.location.hash.split("from=")[1]);
      cleanup();
    }
    expect(froms[0], "both tabs default to the same window").toBe(froms[1]);
    expect(froms[0], "the default is a real date").toMatch(/^\d{4}-\d{2}-\d{2}$/);
  }, ROUTE_TIMEOUT);

  /** Serve a synthetic family prefs doc (these saved views) in place of the missing /api; the device's own
   *  mirror is cleared so the server copy wins the newer-of merge. Undo with vi.stubGlobal("fetch", base). */
  function stubSavedViews(saved_filters: unknown[]) {
    localStorage.removeItem(PREFS_LOCAL_KEY);
    return stubServe("api/prefs", () => ({
      version: 1, updated_at: "2026-01-02T00:00:00Z", budgets: {}, targets: {}, projections: {}, payroll: { phases: [] },
      saved_filters,
    }));
  }
  const SYN_AT = "2026-01-01T00:00:00Z";
  const SYN_VIEWS = [
    { id: 7, name: null }, "junk",                                                       // malformed: warned, dropped
    { id: "stale-1", name: "Synthetic stale view", query: "categories=No+Such+Category+Zzz", created_at: SYN_AT },
    { id: "ok-1", name: "Synthetic all-time view", query: "internal=1", created_at: SYN_AT },
    { id: "empty-1", name: "Synthetic empty view", query: "", created_at: SYN_AT },        // no filters at all: all-time
    { id: "dated-1", name: "Synthetic dated view", query: "from=2025-02-01", created_at: SYN_AT },
  ];

  // a saved view from an older schema must not crash the app. A malformed entry is
  // warned about and dropped; a well-formed view whose category left the data is STALE: it stays in the
  // doc, is listed on Data with its reason, and is offered nowhere else.
  it("transactions: a malformed and a stale saved view never break the register; the stale one is listed on Data, not in ⌘K", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const base = stubSavedViews(SYN_VIEWS);
    try {
      await mount("#/spending/transactions");
      await settled("/spending/transactions");
      await screen.findByTestId("card-transactions", {}, { timeout: 8000 });
      expect(warn.mock.calls.length >= 2, "both malformed entries warned").toBe(true);
      // the FilterBar's menu: the valid views, a count of the stale one, never the stale one itself
      fireEvent.click(await screen.findByTestId("filterbar-views", {}, { timeout: 8000 }));
      const menuList = await screen.findByRole("list", { name: "Saved views" });
      const menuText = menuList.textContent ?? "";
      expect(/Synthetic all-time view/.test(menuText) && /Synthetic dated view/.test(menuText), "the valid views are in the menu").toBe(true);
      expect(/Synthetic stale view/.test(menuText), "the stale view is not in the menu").toBe(false);
      expect(/1 stale view — see Data/.test(menuText), "the menu counts it and points at Data").toBe(true);
      fireEvent.keyDown(window, { key: "k", metaKey: true });
      const list = await screen.findByRole("listbox", {}, { timeout: 8000 });
      expect(/Synthetic all-time view/.test(list.textContent ?? ""), "the valid view is offered").toBe(true);
      expect(/Synthetic stale view/.test(list.textContent ?? ""), "the stale view is not").toBe(false);
      fireEvent.keyDown(document, { key: "Escape" });
      cleanup();
      await mount("#/data");
      await settled("/data");
      const card = await screen.findByTestId("card-saved-views", {}, { timeout: 8000 });
      const text = card.textContent ?? "";
      expect(/Synthetic stale view/.test(text) && /no longer in the data/.test(text), "stale view listed with its reason").toBe(true);
      expect(/4 of 50/.test(text), "the stale view still counts: it is still in the doc").toBe(true);
      expect(screen.getByRole("link", { name: "Synthetic all-time view" }).getAttribute("href")).toBe("#/spending/transactions?internal=1");
      expect(screen.queryByRole("link", { name: "Synthetic stale view" }) === null, "a stale view's NAME is not a link").toBe(true);
      // ...but it opens as saved: its filters, on the Transactions tab, with the marker (no default window written)
      const open = screen.getByRole("link", { name: "open as saved: stale view Synthetic stale view" });
      expect(open.getAttribute("href")).toBe("#/spending/transactions?categories=No+Such+Category+Zzz");
      fireEvent.click(open);
      await settled("/spending/transactions");
      await screen.findByTestId("card-transactions", {}, { timeout: 8000 });
      await quiet();
      expect(window.location.hash).toBe("#/spending/transactions?categories=No+Such+Category+Zzz");
      expect(screen.queryByText(/^0 of [\d,]+ transactions?/) !== null, "a deleted category matches no rows, said honestly").toBe(true);
    } finally {
      vi.stubGlobal("fetch", base);
      warn.mockRestore();
      localStorage.removeItem(PREFS_LOCAL_KEY);
    }
  }, ROUTE_TIMEOUT);

  // A saved view opens exactly as saved. The six-month default is for a fresh entry, and a view with no
  // filter keys looks fresh, so every link to a saved view carries the CARRIED_SEARCH marker: Data's list,
  // ⌘K and the FilterBar's menu are three writers of it, each pinned here against its own surface.
  it("a saved view opens exactly as saved: a view with no date window opens all-time, one with `from` opens with it", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const base = stubSavedViews(SYN_VIEWS);
    const lands = async (hash: string) => {
      await settled("/spending/transactions");
      await screen.findByTestId("card-transactions", {}, { timeout: 8000 });
      await quiet();                                    // a default that should not fire has had its chance
      expect(window.location.hash).toBe(hash);
      cleanup();
    };
    try {
      // Data's list
      await mount("#/data");
      await settled("/data");
      fireEvent.click(await screen.findByRole("link", { name: "Synthetic empty view" }));
      await lands("#/spending/transactions");
      await mount("#/data");
      await settled("/data");
      fireEvent.click(await screen.findByRole("link", { name: "Synthetic dated view" }));
      await lands("#/spending/transactions?from=2025-02-01");
      // ⌘K
      await mount("#/data");
      await settled("/data");
      fireEvent.keyDown(window, { key: "k", metaKey: true });
      const input = await screen.findByRole("combobox", {}, { timeout: 8000 });
      fireEvent.change(input, { target: { value: "synthetic empty" } });
      fireEvent.keyDown(input, { key: "Enter" });
      await lands("#/spending/transactions");
      // the FilterBar's menu, from the other tab (a different route, so the page mounts fresh)
      await mount("#/spending?from=2025-01-01");
      await settled("/spending");
      fireEvent.click(await screen.findByTestId("filterbar-views", {}, { timeout: 8000 }));
      fireEvent.click(screen.getByRole("link", { name: "Synthetic empty view" }));
      await lands("#/spending/transactions");
    } finally {
      vi.stubGlobal("fetch", base);
      warn.mockRestore();
      localStorage.removeItem(PREFS_LOCAL_KEY);
    }
  }, ROUTE_TIMEOUT * 2);

  // "★ Save view" exists while a filter is active; saving keeps the query under the typed name in the
  // family doc (here the device's mirror: the rig has no /api), and the views menu then offers it
  it("transactions: Save view stores the filter query under a name, and the views menu offers it", async () => {
    localStorage.removeItem(PREFS_LOCAL_KEY);
    try {
      await mount("#/spending/transactions?q=synthetic&from=2025-01-01");
      await settled("/spending/transactions");
      await screen.findByTestId("card-transactions", {}, { timeout: 8000 });
      expect(screen.queryByTestId("filterbar-views") === null, "no saved views yet, so no menu").toBe(true);
      fireEvent.click(await screen.findByTestId("filterbar-save-view", {}, { timeout: 8000 }));
      const input = screen.getByLabelText("View name");
      fireEvent.change(input, { target: { value: "Synthetic saved view" } });
      fireEvent.click(within(input.closest("form")!).getByRole("button", { name: "Save" }));
      // the save box's own confirmation, not merely some status region (the page keeps an empty one mounted for bulk saves)
      const done = await screen.findByText(/^saved ✓ \((⌘K|search) and Data\)$/, {}, { timeout: 8000 });
      expect(done.getAttribute("role")).toBe("status");
      const stored = JSON.parse(localStorage.getItem(PREFS_LOCAL_KEY) ?? "{}");
      expect(stored.saved_filters?.length, "one saved view").toBe(1);
      expect(stored.saved_filters?.[0]?.name).toBe("Synthetic saved view");
      expect(stored.saved_filters?.[0]?.query).toBe("from=2025-01-01&q=synthetic");
      fireEvent.click(await screen.findByTestId("filterbar-views", {}, { timeout: 8000 }));
      expect(screen.getByRole("link", { name: "Synthetic saved view" }).getAttribute("href"))
        .toBe("#/spending/transactions?from=2025-01-01&q=synthetic");
    } finally {
      localStorage.removeItem(PREFS_LOCAL_KEY);
    }
  }, ROUTE_TIMEOUT);

  // ---- Spending -> Reports. The per-route loop above already finds its two cards; these cover what
  // only a real route can: the spec in the URL, the table being the chart's own series, the tab bar's carry,
  // a malformed saved report, and a saved report opening exactly as saved.
  it("reports: a spec in the URL renders the builder and the result card with the spec in its title", async () => {
    await mount("#/spending/reports?categories=Dining&groupBy=vendor&interval=quarter&measure=count");
    await settled("/spending/reports");
    await screen.findByTestId("card-report-builder", {}, { timeout: 8000 });
    const result = await screen.findByTestId("card-report-result", {}, { timeout: 8000 });
    expect(/transactions \(count\) by vendor, per quarter/.test(result.textContent ?? ""), "the title names the spec").toBe(true);
    expect((screen.getByLabelText("Group by") as HTMLSelectElement).value).toBe("vendor");
  }, ROUTE_TIMEOUT);

  it("reports: the chart's table is its own series (a silent period a gap in both, a partial period starred in both), and the table view is the same table", async () => {
    chart.pairs.length = 0;
    // an explicit window (no default is written, so the data never changes under the chart) and a stacked chart by category
    // per month (the bare tab is a table by vendor per quarter)
    const from = reportWindow();
    await mount(`#/spending/reports?from=${from}&groupBy=category&interval=month&chart=stacked`);
    await settled("/spending/reports");
    const rep = chartWith((o) => o.series?.[0]?.stack === "report");
    const periods = rep.table.rows.map((r) => String(r[0]));
    const labels: string[] = rep.option.xAxis.data;
    expect(rep.table.columns[0] === "Period" && periods.length === labels.length && periods.length >= 3, "one row per period, three or more").toBe(true);
    expect(rep.option.series.length > 0, "a band per category with spending in the window").toBe(true);
    expect(rep.table.columns.slice(1).join() === rep.option.series.map((s: any) => s.name).join(), "one column per group, in order").toBe(true);
    for (const s of rep.option.series) expectColumnIsSeries(rep.table, s, periods);
    expect(labels[labels.length - 1].endsWith("*") === periods[periods.length - 1].endsWith("*"), "the newest period is starred in the chart exactly when the table stars it").toBe(true);
    expect(labels[0].endsWith("*") === periods[0].endsWith("*"), "the first period likewise").toBe(true);
    expect(labels.slice(1, -1).every((l) => !l.endsWith("*")) && periods.slice(1, -1).every((l) => !l.endsWith("*")), "only the two edges can be partial").toBe(true);
    expect(periods.every((p) => !/\d{4}-\d{2}/.test(p)), "the table names its months in words, never a raw year-month").toBe(true);
    // the same report as a table: no chart, one table whose header is the table's columns
    cleanup();
    chart.pairs.length = 0;
    await mount(`#/spending/reports?from=${from}&groupBy=category&interval=month&chart=table`);
    await settled("/spending/reports");
    await screen.findByTestId("card-report-result", {}, { timeout: 8000 });
    expect(chart.pairs.length === 0, "the table view draws no chart").toBe(true);
    const heads = [...screen.getByRole("table").querySelectorAll("thead th")].map((h) => h.textContent);
    expect(heads.join() === rep.table.columns.join(), "the table view has the chart's own columns").toBe(true);
  }, ROUTE_TIMEOUT);

  it("reports: the Spending tab bar carries the filter keys into Reports and never a report key out of it", async () => {
    await mount("#/spending?categories=Dining");
    await settled("/spending");
    expect(spendingTab("Reports").getAttribute("href"), "Reports carries the filters").toBe("#/spending/reports?categories=Dining");
    fireEvent.click(spendingTab("Reports"));
    await settled("/spending/reports");
    await screen.findByTestId("card-report-result", {}, { timeout: 8000 });
    await quiet();
    expect(window.location.hash).toBe("#/spending/reports?categories=Dining");
    fireEvent.change(screen.getByLabelText("Group by"), { target: { value: "account" } });
    await waitFor(() => expect(window.location.hash).toBe("#/spending/reports?categories=Dining&groupBy=account&interval=month&measure=spend&chart=table"));
    expect(spendingTab("Transactions").getAttribute("href"), "a report key stays behind").toBe("#/spending/transactions?categories=Dining");
    expect(spendingTab("Explore").getAttribute("href")).toBe("#/spending?categories=Dining");
    expect(spendingTab("Recurring").getAttribute("href")).toBe("#/spending/recurring");
    expect(spendingTab("Reports").getAttribute("aria-current"), "Reports is the current tab").toBe("page");
  }, ROUTE_TIMEOUT);

  it("reports: a malformed saved report never breaks the tab; the valid one is listed on Data", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    localStorage.removeItem(PREFS_LOCAL_KEY);
    const base = stubServe("api/prefs", () => ({
      version: 1, updated_at: "2026-01-02T00:00:00Z", budgets: {}, targets: {}, projections: {}, payroll: { phases: [] }, saved_filters: [],
      reports: ["junk", { id: "bad", name: "Synthetic bad report", spec: { filters: "", groupBy: "merchant", interval: "month", measure: "spend", chart: "stacked" }, created_at: "2026-01-01T00:00:00Z" },
        { id: "ok", name: "Synthetic vendor report", spec: { filters: "internal=1", groupBy: "vendor", interval: "quarter", measure: "count", chart: "table" }, created_at: "2026-01-01T00:00:00Z" }],
    }));
    try {
      await mount("#/spending/reports");
      await settled("/spending/reports");
      await screen.findByTestId("card-report-result", {}, { timeout: 8000 });
      expect(warn.mock.calls.length >= 2, "both malformed entries warned").toBe(true);
      cleanup();
      await mount("#/data");
      await settled("/data");
      const link = await screen.findByRole("link", { name: "Synthetic vendor report" }, { timeout: 8000 });
      expect(link.getAttribute("href")).toBe("#/spending/reports?internal=1&groupBy=vendor&interval=quarter&measure=count&chart=table");
      expect(screen.queryByText(/Synthetic bad report/) === null, "the malformed report is gone").toBe(true);
    } finally { vi.stubGlobal("fetch", base); warn.mockRestore(); localStorage.removeItem(PREFS_LOCAL_KEY); }
  }, ROUTE_TIMEOUT);

  // A saved report opens exactly as saved. The six-month default is for a fresh entry, and a report with no
  // filter keys looks fresh, so every link to a saved report carries the CARRIED_SEARCH marker: Data's list and
  // ⌘K are two writers of it, each pinned here against its own surface (the Reports tab's own list is a third,
  // pinned in Reports.test.tsx - a click inside the same route never remounts, so only a unit test can see it).
  it("a saved report opens exactly as saved: one with no date window opens all-time, one with `from` opens with it", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    localStorage.removeItem(PREFS_LOCAL_KEY);
    const base = stubServe("api/prefs", () => ({
      version: 1, updated_at: "2026-01-02T00:00:00Z", budgets: {}, targets: {}, projections: {}, payroll: { phases: [] }, saved_filters: [],
      reports: [
        { id: "all-1", name: "Synthetic all-time report", spec: { filters: "", groupBy: "owner", interval: "quarter", measure: "count", chart: "table" }, created_at: SYN_AT },
        { id: "dated-1", name: "Synthetic dated report", spec: { filters: "from=2025-02-01", groupBy: "category", interval: "month", measure: "spend", chart: "stacked" }, created_at: SYN_AT },
        { id: "stale-1", name: "Synthetic stale report", spec: { filters: "categories=No+Such+Category+Zzz", groupBy: "category", interval: "month", measure: "spend", chart: "stacked" }, created_at: SYN_AT },
      ],
    }));
    const lands = async (hash: string) => {
      await settled("/spending/reports");
      await screen.findByTestId("card-report-result", {}, { timeout: 8000 });
      await quiet();                                    // a default that should not fire has had its chance
      expect(window.location.hash).toBe(hash);
      cleanup();
    };
    try {
      await mount("#/data");
      await settled("/data");
      expect(/Synthetic stale report/.test(screen.getByTestId("card-saved-views").textContent ?? "") && /no longer in the data/.test(screen.getByTestId("card-saved-views").textContent ?? ""),
        "the stale report is named on Data with its reason").toBe(true);
      fireEvent.click(await screen.findByRole("link", { name: "Synthetic all-time report" }));
      await lands("#/spending/reports?groupBy=owner&interval=quarter&measure=count&chart=table");
      await mount("#/data");
      await settled("/data");
      fireEvent.click(await screen.findByRole("link", { name: "Synthetic dated report" }));
      await lands("#/spending/reports?from=2025-02-01&groupBy=category&interval=month&measure=spend&chart=stacked");
      // ⌘K: the valid report is offered, the stale one is not
      await mount("#/data");
      await settled("/data");
      fireEvent.keyDown(window, { key: "k", metaKey: true });
      const input = await screen.findByRole("combobox", {}, { timeout: 8000 });
      fireEvent.change(input, { target: { value: "synthetic stale" } });
      expect(screen.getByText("no matches") !== null && within(screen.getByRole("listbox")).queryByText(/Synthetic stale report/) === null, "a stale report is not in ⌘K").toBe(true);
      fireEvent.change(input, { target: { value: "synthetic all-time" } });
      fireEvent.keyDown(input, { key: "Enter" });
      await lands("#/spending/reports?groupBy=owner&interval=quarter&measure=count&chart=table");
    } finally {
      vi.stubGlobal("fetch", base);
      warn.mockRestore();
      localStorage.removeItem(PREFS_LOCAL_KEY);
    }
  }, ROUTE_TIMEOUT * 2);

  it("the Transactions tab holds the register at viewport height and Explore no longer has it", async () => {
    await mount("#/spending/transactions");
    await settled("/spending/transactions");
    const card = await screen.findByTestId("card-transactions", {}, { timeout: 8000 });
    const scroller = card.querySelector<HTMLElement>(".overflow-y-auto");
    expect(scroller !== null, "the table body scroller").toBe(true);
    expect(scroller!.style.maxHeight, "viewport-relative height with a floor").toBe("max(20rem, calc(100vh - 20rem))");
    cleanup();
    await mount("#/spending");
    await settled("/spending");
    expect(screen.queryByTestId("card-transactions") === null, "no register on Explore").toBe(true);
  }, ROUTE_TIMEOUT);

  // The jsdom virtualizer renders no register rows (a 0px scroller), so the bulk flow itself is a component
  // test (sections/Transactions.test.tsx); against the household's data this pins what the page shows at rest: a
  // select-all box over its rows, no bulk bar, no editor.
  it("transactions at rest: a select-all box over the household's rows, and no bulk bar or editor until something is ticked", async () => {
    await mount("#/spending/transactions");
    await settled("/spending/transactions");
    await screen.findByTestId("card-transactions", {}, { timeout: 8000 });
    // the default window holds rows: a purchase dated on or after the window's start
    await waitFor(() => expect(/[?&]from=\d{4}-\d{2}-\d{2}/.test(window.location.hash), "the default window is written").toBe(true), { timeout: 8000 });
    const from = /from=(\d{4}-\d{2}-\d{2})/.exec(window.location.hash)![1];
    some(json("transactions.json").transactions.filter((t: { date: string; is_parent: boolean }) => !t.is_parent && t.date >= from),
      "rows in the default window");
    const all = screen.queryByRole("checkbox", { name: /^Select (every row|the first \d+ rows) shown$/ }) as HTMLInputElement | null;
    expect(all !== null, "the header select-all box").toBe(true);
    expect(all!.disabled, "rows to select, so it is on").toBe(false);
    expect(all!.checked, "nothing selected at rest").toBe(false);
    expect(screen.queryByTestId("bulk-bar") === null, "no bulk bar at rest").toBe(true);
    expect(screen.queryByRole("button", { name: /Categorize or tag/ }) === null, "no bulk action at rest").toBe(true);
    expect(screen.queryByPlaceholderText("add tag…") === null, "no tag editor mounted unprompted").toBe(true);
  }, ROUTE_TIMEOUT);

  // the seeded split is in the live data only when its vendor has rows in the default window, so the
  // register's handling of a split purchase is pinned on synthetic rows: one split (-300 = -210 Groceries +
  // -90 Shopping) and one ordinary row. jsdom's virtualizer draws no rows, so the counts the page shows
  // at rest are the proof: transactions (a split purchase once) in the FilterBar and the footer, parts in the CSV count,
  // no Review badge.
  // The parent is flagged needs_review in "Cash & Misc / Uncategorized" on purpose: were it ever to reach the
  // category view it WOULD be a review candidate, so the badge assertion can fail.
  it("transactions: a split purchase counts once in the FilterBar and the footer, its parts in the CSV count, and never in the Review badge", async () => {
    const base_ = synthTxn({ date: "2031-01-15", vendor: "Synthetic Split Mart", description: "SYNTHETIC SPLIT MART", type: "purchase",
      is_internal: false, needs_review: false, tags: [], transfer_group_id: null, earner: null, category_source: "keyword" });
    const parent = { ...base_, id: "syn-split-p", amount: -300, category: "Cash & Misc", subcategory: "Uncategorized", needs_review: true,
      parent_id: null, is_parent: true };
    const part = (id: string, amount: number, category: string, subcategory: string | null) =>
      ({ ...base_, id, amount, category, subcategory, category_source: "split", parent_id: parent.id, is_parent: false });
    const plain = { ...base_, id: "syn-plain", date: "2031-01-16", vendor: "Synthetic Plain Shop", amount: -40, category: "Dining",
      subcategory: null, parent_id: null, is_parent: false };
    const base = stubServe("transactions.json", () => ({ count: 4, transactions: [
      parent, part("syn-split-a", -210, "Groceries", null), part("syn-split-b", -90, "Shopping", "Home Goods"), plain] }));
    try {
      await mount("#/spending/transactions");
      await settled("/spending/transactions");
      const card = await screen.findByTestId("card-transactions", {}, { timeout: 8000 });
      const count = Array.from(document.querySelectorAll("span")).find((x) => /^[\d,]+ of [\d,]+ transactions/.test(x.textContent ?? ""));
      expect(/^2 of 2 transactions/.test(count?.textContent ?? ""), "the FilterBar counts the split purchase once").toBe(true);
      expect(/Export CSV: 3 rows \(2 transactions\)/.test(card.textContent ?? ""), "the CSV counts the parts, and names the transactions they are").toBe(true);
      // the footer's own div (its text opens with the count; the aside's would glue the Review badge's digits on)
      const footer = Array.from(document.querySelectorAll("aside div")).find((d) => /^[\d,]+ transactions/.test(d.textContent ?? ""));
      expect(/^2 transactions/.test(footer?.textContent ?? ""), "the footer counts the split purchase once").toBe(true);
      expect(screen.queryByRole("img", { name: /to review/ }) === null, "a parent is never a review candidate").toBe(true);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // On the household's own rows: the filter bar's "N of M transactions" and the CSV button count the same rows - the
  // button counts the split parts as rows and names the same N transactions beside them - and the bar's M is the
  // sidebar's total under the same word. The count holds income as well as purchases, so neither may say "purchases".
  it("transactions: the filter bar's transactions are the ones the CSV button names beside its rows, and the sidebar's total", async () => {
    const rows_ = categoryView(json("transactions.json").transactions);
    some(rows_.filter((t: { parent_id: string | null }) => t.parent_id !== null), "split parts");
    const through = dataThrough(rows_.reduce((m: string, t: { date: string }) => (t.date > m ? t.date : m), "0000"),
      json("manifest.json").generated_at);
    const from = presetRange("6m", through).from!;
    branch(rows_.some((t: { date: string; type: string }) => t.date >= from && t.type === "income"),
      "the default window holds income, so its transactions are more than its purchases");
    await mount("#/spending/transactions");
    await settled("/spending/transactions");
    const card = await screen.findByTestId("card-transactions", {}, { timeout: 8000 });
    const bar = Array.from(document.querySelectorAll("span")).find((x) => /^[\d,]+ of [\d,]+ transactions/.test(x.textContent ?? ""));
    const [n, m] = (/^([\d,]+) of ([\d,]+) transactions/.exec(bar?.textContent ?? "") ?? ["", "", ""]).slice(1)
      .map((s) => Number(s.replace(/,/g, "")));
    const button = /Export CSV: ([\d,]+) rows?(?: \(([\d,]+) transactions?\))?/.exec(card.textContent ?? "");
    expect(button !== null && n > 0, "the bar's count and the button's").toBe(true);
    const [rows, transactions] = [button![1], button![2] ?? button![1]].map((s) => Number(s.replace(/,/g, "")));
    branch(rows > transactions, "the default window holds a split purchase, so its parts are more rows than transactions");
    expect(transactions === n, "the button names the bar's transactions").toBe(true);
    const footer = Array.from(document.querySelectorAll("aside div")).find((d) => /^[\d,]+ transactions/.test(d.textContent ?? ""));
    expect(Number((/^([\d,]+)/.exec(footer?.textContent ?? "")?.[1] ?? "").replace(/,/g, "")) === m,
      "the bar's M is the sidebar's total, under the same word").toBe(true);
    expect(/\d purchases\b/.test(document.body.textContent ?? ""), "no count on the tab says purchases").toBe(false);
  }, ROUTE_TIMEOUT);

  // a "view transactions" link (spendingLink's default target) must land on the table with its
  // filters applied: a window with no matches proves the filters, the empty count proves the table
  it("a 'view transactions' deep link lands on the table with its filters applied", async () => {
    const link = spendingLink({ vendors: ["Nobody Real"], from: "2001-01-01", to: "2001-01-02" });
    expect(link.startsWith("#/spending/transactions?"), "the default target is the table").toBe(true);
    await mount(link);
    await settled("/spending/transactions");
    await screen.findByTestId("card-transactions");
    expect(screen.queryByText(/^0 of [\d,]+ transactions?/) !== null, "the filter bar counts 0 matches").toBe(true);
  }, ROUTE_TIMEOUT);

  // Explore's calendar: a day click is a "show me that day" - it goes to the table, keeping the
  // filters the heatmap was drawn from (its cell values are the filtered spend)
  it("a calendar-day click on Explore opens that day on the Transactions tab", async () => {
    chart.clickValue = ["2025-03-04", 42];
    try {
      await mount("#/spending?categories=Dining");
      await settled("/spending");
      const card = await screen.findByTestId("card-spend-calendar", {}, { timeout: 8000 });
      fireEvent.click(within(card).getByTestId("chart"));
      await settled("/spending/transactions");
      expect(window.location.hash).toBe("#/spending/transactions?categories=Dining&from=2025-03-04&to=2025-03-04");
      await screen.findByTestId("card-transactions");
    } finally {
      chart.clickValue = undefined;
    }
  }, ROUTE_TIMEOUT);

  // a treemap click filters first (the chart re-renders around the one category), and only a second
  // click zooms into its subcategories - the caption says so; the donut has no zoom
  it("the treemap caption describes its two-step click and the donut's does not", async () => {
    await mount("#/spending?categories=Dining");
    await settled("/spending");
    const card = await screen.findByTestId("card-category-breakdown", {}, { timeout: 8000 });
    expect(/click again to zoom into its subcategories/.test(card.textContent ?? ""), "treemap caption").toBe(true);
    fireEvent.click(within(card).getByText("Donut"));
    expect(/zoom/.test(card.textContent ?? ""), "donut caption").toBe(false);
  }, ROUTE_TIMEOUT);

  // the treemap used to print a cell's label truncated to a single letter when the cell was a sliver; a cell
  // under 3% of its level is now unlabelled (its tooltip still names it)
  it("spending: the treemap leaves a cell under 3% of its level unlabelled instead of truncating it to a letter", async () => {
    chart.options.length = 0;
    await mount("#/spending");
    await settled("/spending");
    const opt = chart.options.find((o) => o?.series?.[0]?.type === "treemap");
    expect(opt !== undefined, "the treemap option was built").toBe(true);
    const f = opt.series[0].label.formatter;
    expect(f({ name: "Tiny", value: 1, treePathInfo: [{ value: 1000 }, { value: 1 }] }) === "", "tiny: no label").toBe(true);
    expect(/^Big/.test(f({ name: "Big", value: 500, treePathInfo: [{ value: 1000 }, { value: 500 }] })), "big: labelled").toBe(true);
    // one level down the sliver is measured against its own category, not the grand total
    expect(/^Mid/.test(f({ name: "Mid", value: 40, treePathInfo: [{ value: 1000 }, { value: 100 }, { value: 40 }] })),
      "40 of its category's 100 is a real cell").toBe(true);
    expect(f({ name: "Sliver", value: 2, treePathInfo: [{ value: 1000 }, { value: 100 }, { value: 2 }] }) === "",
      "2 of its category's 100 is a sliver").toBe(true);
    expect(f({ name: "Solo", value: 5 }) === "", "no tree path: measured against the grand total, and it does not throw").toBe(true);
    // ...and a real cell is labelled only when its WHOLE label fits the tile ECharts laid out (lib/echarts.ts records
    // each box under the series id the option names): a phone-sized 40px tile gets nothing, never "Gr…" or "$4…"
    expect(opt.series[0].id === "explore-breakdown", "the treemap names its series").toBe(true);
    expect(opt.series[0].drillDownIcon === "", "ECharts' own drill icon is off (the label carries it)").toBe(true);
    const boxes = [{ width: 40, height: 40 }, { width: 300, height: 120 }];
    TREEMAP_TILE_STAGE.reset({ id: opt.series[0].id, getData: () => ({
      each: (cb: (i: number) => void) => boxes.forEach((_, i) => cb(i)), getItemLayout: (i: number) => boxes[i] }) });
    const big = { name: "Groceries", value: 500, treePathInfo: [{ value: 1000 }, { value: 500 }] };
    expect(f({ ...big, dataIndex: 0 }) === "", "a 40px tile: no label at all").toBe(true);
    expect(/^Groceries\n/.test(f({ ...big, dataIndex: 1 })), "a 300px tile: name and value").toBe(true);
  }, ROUTE_TIMEOUT);

  // "view data" under the eight main charts. The table is built in the same memo as the option from the
  // same arrays; these prove it on the real exports - every cell is the chart's own point, no 0 is invented.
  it("spending: the stacked chart's and the top-vendors chart's tables are their own series", async () => {
    chart.pairs.length = 0;
    await mount("#/spending");
    await settled("/spending");
    const stack = chartWith((o) => o.series?.[0]?.stack === "spend");
    const months: string[] = stack.option.xAxis.data;
    expect(months.length >= 2 && stack.option.series.length > 0, "the default window draws two months or more, with bands").toBe(true);
    // one row per month; a month the window covers only in part says so in its row label (as the Reports table does)
    const rowMonth = (r: (string | number | null)[]) => String(r[0]).replace(/ \(partial\)$/, "");
    expect(stack.table.columns[0] === "Month" && stack.table.rows.map(rowMonth).join() === months.join(), "one row per month").toBe(true);
    expect(stack.table.rows.slice(1, -1).every((r) => !String(r[0]).endsWith("(partial)")), "only the first and last month can be partial").toBe(true);
    expect(stack.table.columns.slice(1).join() === stack.option.series.map((s: any) => s.name).join(), "one column per band, in order").toBe(true);
    // the series' points are keyed by the table's own row labels (a partial month's reads "2026-10 (partial)")
    for (const s of stack.option.series) expectColumnIsSeries(stack.table, s, stack.table.rows.map((r) => String(r[0])));
    const vend = chartWith((o) => o.yAxis?.type === "category" && o.yAxis?.inverse === true);
    expect(stack.table.label !== vend.table.label, "two charts on a page have two distinct toggle names").toBe(true);
    const names: string[] = some(vend.option.yAxis.data, "vendors on the top-vendors chart");
    expect(vend.table.columns.join() === "Vendor,Spend", "vendor columns").toBe(true);
    expect(vend.table.rows.map((r) => r[0]).join("|") === names.join("|"), "one row per drawn vendor, same order").toBe(true);
    expect(vend.table.rows.every((r, i) => r[1] === vend.option.series[0].data[i]), "each vendor's total is the bar's value").toBe(true);
  }, ROUTE_TIMEOUT);

  it("cash flow: the main chart's table is its income / spending / invested months, the raw savings rate and each month's coverage", async () => {
    chart.pairs.length = 0;
    await mount("#/cash-flow");
    await settled("/cash-flow");
    const main = chartWith((o) => o.series?.[0]?.name === "Income");
    const months: string[] = some(main.option.xAxis.data, "cash-flow months on the main chart");
    expect(main.table.columns.join() === "Month,Income,Spending,Invested,Savings rate,Coverage", "columns").toBe(true);
    // one row per month; the month in progress (the chart's last, starred) says so in words
    const inProgress = exportsInProgress();
    branch(months[months.length - 1] === inProgress, "the chart's default window ends on the month in progress");
    const labels = months.map((m) => (m === inProgress ? `${m} (in progress)` : m));
    expect(main.table.rows.map((r) => r[0]).join() === labels.join(), "one row per month").toBe(true);
    const [inc, spend, inv, rate] = main.option.series;
    expectColumnIsSeries(main.table, inc, labels);
    expectColumnIsSeries(main.table, inv, labels);
    // spending is drawn downward (negative bars) and listed as the positive amount it is
    expectColumnIsSeries(main.table, { ...spend, data: spend.data.map((v: number) => -v) }, labels, "Spending");
    // the rate is the raw ratio (a number, so the CSV keeps it as one); a month with no income has none and its cell is null
    expectColumnIsSeries(main.table, rate, labels, "Savings rate");
    const rateCol = column(main.table, "Savings rate");
    expect(labels.every((m, i) => rateCol.get(m) === rate.data[i]), "the rate column is the line's own values, nulls included").toBe(true);
    // the month in progress keeps its bars and has no rate point (no rate counts it): the line ends on the last full month
    const exported = json("cashflow_monthly.json").months.find((m: { month: string }) => m.month === inProgress);
    branch(exported?.savings_rate != null, "the export holds a rate for the month in progress");
    const last = months.length - 1;
    expect(rate.data[last] === null && inc.data[last] === exported.income, "its income bar stays and its rate point is gone").toBe(true);
    expect(rate.data[last - 1] !== null, "the line ends on the last full month").toBe(true);
    // ... and it reads as a percentage while the money columns read as dollars (the column-aware formatter)
    const fmt = (main.table as any).format as ((v: number, c: number) => string) | undefined;
    expect(fmt !== undefined && fmt(0.123, 4) === fmtPct(0.123) && fmt(1234.5, 1) === fmtUSD0(1234.5), "format: ratio column vs money columns").toBe(true);
    // coverage is the greyed-month shading in words: the markArea's un-named bands are exactly the incomplete months
    // (the whole history's test below holds both kinds of month)
    const grey = greyMonths(inc);
    const cov = column(main.table, "Coverage");
    expect(months.every((m, i) => cov.get(labels[i]) === (grey.has(m) ? "incomplete" : "complete")), "coverage matches the greyed months").toBe(true);
    // the note names what the table leaves out, and the dashed total-rate line only when the chart draws it
    const dashed = main.option.series.some((x: any) => /Total savings rate/.test(x.name ?? ""));
    expect(/positive amount/.test(main.table.note ?? "") && /Incomplete months/.test(main.table.note ?? ""), "the note names the sign and the coverage").toBe(true);
    expect(/total savings rate/.test(main.table.note ?? "") === dashed, "the dashed line is named exactly when it is drawn").toBe(true);
  }, ROUTE_TIMEOUT);

  // The whole history's first months come before every cash-flow account has begun, and its newest months are covered by
  // all: the greyed bands are exactly the export's incomplete months, and the table's Coverage column says the same.
  it("cash flow: over the whole history the greyed months are the incomplete ones, in the table's Coverage column too", async () => {
    chart.pairs.length = 0;
    await mount("#/cash-flow?win=all");
    await settled("/cash-flow");
    const main = chartWith((o) => o.series?.[0]?.name === "Income");
    const months: string[] = some(main.option.xAxis.data, "cash-flow months on the main chart");
    const grey = greyMonths(main.option.series[0]);
    branch(grey.size > 0 && grey.size < months.length, "the chart holds complete months and an incomplete one");
    const exported = new Map<string, boolean>(json("cashflow_monthly.json").months.map(
      (m: { month: string; full_coverage: boolean }) => [m.month, m.full_coverage]));
    expect(months.every((m) => grey.has(m) === (exported.get(m) === false)), "the greyed months are the export's incomplete ones").toBe(true);
    const cov = column(main.table, "Coverage");
    const label = (r: (string | number | null)[]) => String(r[0]).replace(/ \(in progress\)$/, "");
    expect(main.table.rows.every((r) => cov.get(String(r[0])) === (grey.has(label(r)) ? "incomplete" : "complete")),
      "coverage matches the greyed months").toBe(true);
  }, ROUTE_TIMEOUT);

  // The best and the worst month are the shown window's: the full months of the default window, read off the export. The
  // household's worst month of all lies before that window, so a tile that read the whole history would name another.
  it("cash flow: the best and worst month are the window's own full months, and the tiles say so", async () => {
    const inProgress = exportsInProgress();
    const through = dataThrough(categoryView(json("transactions.json").transactions)
      .reduce((m: string, t: { date: string }) => (t.date > m ? t.date : m), "0000"), json("manifest.json").generated_at);
    const from = presetRange("6m", through, "month").from!.slice(0, 7);
    type Month = { month: string; savings_rate: number | null };
    const full: Month[] = json("cashflow_monthly.json").months
      .filter((m: { month: string; in_coverage: boolean }) => m.in_coverage && m.month < inProgress);
    const ranked = (ms: Month[]): { month: string; savings_rate: number }[] => ms
      .flatMap((m) => (m.savings_rate === null ? [] : [{ month: m.month, savings_rate: m.savings_rate }]))
      .sort((a, b) => b.savings_rate - a.savings_rate);
    const shown = ranked(full.filter((m) => m.month >= from));
    const history = ranked(full);
    expect(shown.length >= 2, "two rated full months or more in the default window").toBe(true);
    branch(history[history.length - 1].month < from, "the history's worst month lies before the default window");
    await mount("#/cash-flow");
    await settled("/cash-flow");
    const tiles = [...(await screen.findByTestId("kpis-cashflow", {}, { timeout: 8000 })).children].map((t) => t.textContent ?? "");
    const best = shown[0], worst = shown[shown.length - 1];
    // a rate below zero is said as what it is: spent more than came in, by that share of the income
    const saved = (rate: number) => (rate < 0 ? `spent ${fmtPct(-rate)} more than came in` : `${fmtPct(rate)} saved`);
    expect(tiles[2] === `Best month${fmtMonth(best.month)}${saved(best.savings_rate)} · in this window`, "the window's best month").toBe(true);
    expect(tiles[3] === `Worst month${fmtMonth(worst.month)}${saved(worst.savings_rate)} · in this window`, "the window's worst month").toBe(true);
  }, ROUTE_TIMEOUT);

  // The history lists the snapshots the page draws: the comparable ones (lib/comparableSnapshots.ts), every row and
  // every point read off the file, the partial month ends nowhere.
  it("home: the net-worth history's table lists the snapshots it draws (a class a snapshot lacks is a gap, not $0) and the waterfall's its five-ish columns", async () => {
    const { comparable: file, partial } = snapshotSplit() as { comparable: Snapshot[]; partial: { snapshot_date: string }[] };
    // the history sets the newest snapshot beside month ends on their footing (a live margin account net of its loan):
    // the month ends are the file's own, the newest folded as the app folds it
    const classOf = new Map<string, string>(json("accounts.json").accounts.map((a: { id: string; asset_class: string }) => [a.id, a.asset_class]));
    const footed = onOneFooting(file, (id) => classOf.get(id));
    const raw = footed.snaps;
    expect(raw.slice(0, -1).every((s, i) => s === file[i]), "the month ends are the file's own").toBe(true);
    chart.pairs.length = 0;
    await mount("#/");
    await settled("/");
    await screen.findByTestId("card-decomposition", {}, { timeout: 8000 });
    /** every band's column is the file's own value where the snapshot has the key and a gap where it does not,
     *  while the chart's stacked series carries 0 in exactly those gaps (and the file's value elsewhere); the table's
     *  rows are the comparable snapshots, in order. Returns how many gaps it checked. */
    const expectBandsAreSnapshots = (nw: { option: any; table: Table }, group: "by_class" | "by_owner"): number => {
      const bands = nw.option.series.filter((x: any) => x.stack);
      expect(bands.length > 0 && nw.table.columns.length === bands.length + 2, "a column per band plus the total").toBe(true);
      const dates = nw.table.rows.map((r) => String(r[0]));
      expect(dates.join() === raw.map((s) => s.snapshot_date).join(), `${group}: a row per comparable snapshot, oldest first`).toBe(true);
      expect(noPartialDate(dates.join(" "), partial), `${group}: no row for a partial month end`).toBe(true);
      let gaps = 0;
      for (const b of bands) {
        const col = column(nw.table, b.name);
        const pts = pointsOf(b);
        expect(noPartialDate([...pts.keys()].join(" "), partial), `${b.name}: no point at a partial month end`).toBe(true);
        const ok = raw.every((snap) => {
          const has = b.name in snap[group];
          if (!has) gaps++;
          return col.get(snap.snapshot_date) === (has ? snap[group][b.name] : null)
            && pts.get(snap.snapshot_date) === (has ? snap[group][b.name] : 0);
        });
        expect(ok, `${b.name}: a gap where the snapshot has no row, the file's value elsewhere (the chart stacks 0 in the gaps)`).toBe(true);
      }
      // the mortgage clause names the month the mortgage data starts, and only while some snapshot shown pre-dates it
      const preMortgage = raw.some((snap) => !snap.mortgage_included);
      expect(/no rows in that snapshot/.test(nw.table.note ?? ""), "the note explains the dash").toBe(true);
      expect(/the mortgage is not booked before [A-Z][a-z]{2} \d{4} \(†\)/.test(nw.table.note ?? ""), "the note's mortgage clause follows the data").toBe(preMortgage);
      expect(/mortgage/.test(nw.table.note ?? ""), "no mortgage clause once every snapshot subtracts it").toBe(preMortgage);
      return gaps;
    };
    const nw = chartWith((o) => o.series?.some?.((s: any) => s?.name === "Net worth (snapshots)"));
    const total = nw.option.series.find((s: any) => s.name === "Net worth (snapshots)");
    expectColumnIsSeries(nw.table, total, undefined, "Net worth");
    // a class some comparable snapshots lack, so the gap path is exercised
    branch(expectBandsAreSnapshots(nw, "by_class") > 0, "a comparable snapshot lacks a class the newest has");
    const daily = nw.option.series.find((s: any) => /daily/i.test(s.name));
    expect(daily === undefined || (nw.table.columns.every((c) => !/daily/i.test(c)) && /Snapshots only/.test(nw.table.note ?? "")),
      "the dense daily line is not listed, and the table says so").toBe(true);
    // the by-owner view lists the same way
    chart.pairs.length = 0;
    fireEvent.click(within(screen.getByTestId("card-networth-history")).getByText("by owner"));
    const byOwner = chartWith((o) => o.series?.some?.((s: any) => s?.name === "Net worth (snapshots)"));
    expect(byOwner.table.columns.slice(1, -1).join() !== nw.table.columns.slice(1, -1).join(), "the owner view has its own columns").toBe(true);
    expectBandsAreSnapshots(byOwner, "by_owner");
    expect((screen.queryByTestId("networth-history-footing") !== null) === footed.folded,
      "the history says its last point is on the month ends' footing exactly when it is").toBe(true);
    // the page's other chart: the waterfall
    const wf = chartWith((o) => o.series?.some?.((s: any) => s?.stack === "wf"));
    expect(wf.table.label !== nw.table.label, "the page's two charts have two distinct toggle names").toBe(true);
    expect(wf.table.columns.join() === "Component,Amount", "waterfall columns (Start and Now are levels, so not 'Change')").toBe(true);
    const bars: { value: number }[] = wf.option.series[1].data;
    expect(bars.length >= 3 && wf.table.rows.length === bars.length, "a row per column of the waterfall: Start, a change, Now").toBe(true);
    expect(wf.table.rows.every((r, i) => typeof r[1] === "number" && Math.abs(r[1] as number) === bars[i].value),
      "each row's magnitude is its bar").toBe(true);
    expect(wf.table.rows.every((r, i) => r[0] === wf.option.xAxis.data[i]), "each row carries its column's name").toBe(true);
  }, ROUTE_TIMEOUT);

  // Trends' liquidity lines are the snapshots the page draws: the comparable ones, the partial month ends left out.
  it("investments, planning and trends: the balance, retirement and liquidity tables are their charts' own lines", async () => {
    const { comparable, partial } = snapshotSplit();
    some(Object.keys(json("assumptions.json").household.retirement_accounts ?? {}), "retirement accounts to project");
    some(Object.keys(json("balances_timeseries.json").series), "balance histories");
    chart.pairs.length = 0;
    await mount("#/accounts/investments");
    await settled("/accounts/investments");
    await screen.findByTestId("card-investment-balances", {}, { timeout: 8000 });
    const inv = chartWith((o) => o.series?.length > 0 && o.series.every((s: any) => s.type === "line" && Array.isArray(s.data?.[0])));
    expect(inv.table.columns.slice(1).join() === inv.option.series.map((s: any) => s.name).join(), "a column per account line").toBe(true);
    for (const s of inv.option.series) expectColumnIsSeries(inv.table, s);
    cleanup();

    chart.pairs.length = 0;
    await mount("#/planning");
    await settled("/planning");
    await screen.findByTestId("card-retirement", {}, { timeout: 8000 });
    const ret = chartWith((o) => /age$/.test(o.xAxis?.name ?? ""));
    expect(Array.isArray(ret.option.series) && ret.option.series.length > 0, "the retirement chart draws a growth run").toBe(true);
    const ages: string[] = ret.option.xAxis.data;
    expect(ret.table.columns[0] === "Age" && ret.table.rows.map((r) => r[0]).join() === ages.join(), "a row per age").toBe(true);
    // the legend's bare "5%" is "5% return" in the table; the FIRE line keeps its name
    const asHeader = (n: string) => (/^\d+(\.\d+)?%$/.test(n) ? `${n} return` : n);
    expect(ret.table.columns.slice(1).join() === ret.option.series.map((s: any) => asHeader(s.name)).join(), "growth runs (and the FIRE line when drawn)").toBe(true);
    for (const s of ret.option.series) expectColumnIsSeries(ret.table, s, ages, asHeader(s.name));
    cleanup();

    // Trends' liquidity card lists the liquid dollars and the assets per snapshot beside the share of assets it draws, and
    // draws the dollars on its other view (Home's net-worth chart is the one home of net worth over time)
    chart.pairs.length = 0;
    await mount("#/trends");
    await settled("/trends");
    const liqCard = await screen.findByTestId("card-trend-liquidity", {}, { timeout: 8000 });
    expect(document.querySelector("h1")?.textContent === "Trends", "a flat page prints its own heading").toBe(true);
    const tr = chartWith((o) => o.series?.[0]?.name === "Liquid share of assets");
    expect(tr.table.columns.join() === "Date,Liquid assets,Assets,Liquid share", "liquidity columns").toBe(true);
    expectColumnIsSeries(tr.table, tr.option.series[0], undefined, "Liquid share");
    type Row = { account_id: string | null; asset_class: string; value: number; liquid: boolean };
    const snaps: { snapshot_date: string; liquid_total: number; assets_total: number; rows: Row[] }[] = comparable;
    const liquidCol = column(tr.table, "Liquid assets"), assetsCol = column(tr.table, "Assets");
    expect(tr.table.rows.map((r) => String(r[0])).join() === snaps.map((s) => s.snapshot_date).join(), "a row per comparable snapshot, oldest first").toBe(true);
    // the code's definition: the liquid rows above zero over the rows above zero, every point; the newest drawn on the
    // month ends' footing, an account the snapshot holds as positions beside a debt row (a live margin account) taken as
    // the one row at their sum every month end holds for it
    const cents = (v: number) => Math.round(v * 100);
    const DEBT = new Set(["Mortgage", "Margin", "Other Debt"]);
    const onFooting = (rows: Row[]): Row[] => {
      const split = new Set(rows.filter((r) => r.account_id && DEBT.has(r.asset_class)).map((r) => r.account_id)
        .filter((id) => rows.some((r) => r.account_id === id && !DEBT.has(r.asset_class))));
      const merged = [...split].map((id) => {
        const own = rows.filter((r) => r.account_id === id);
        const lead = own.filter((r) => !DEBT.has(r.asset_class)).sort((a, b) => b.value - a.value)[0];
        return { ...lead, value: own.reduce((c, r) => c + cents(r.value), 0) / 100 };
      });
      return [...rows.filter((r) => !split.has(r.account_id)), ...merged];
    };
    const sumCents = (rows: Row[]) => rows.reduce((c, r) => c + cents(r.value), 0);
    expect(snaps.length > 1 && snaps.every((s, i) => {
      const rows = i === snaps.length - 1 ? onFooting(s.rows) : s.rows;
      return cents(Number(liquidCol.get(s.snapshot_date))) === sumCents(rows.filter((r) => r.liquid && r.value > 0))
        && cents(Number(assetsCol.get(s.snapshot_date))) === sumCents(rows.filter((r) => r.value > 0));
    }), "every comparable snapshot's liquid assets and assets are the rows above zero, the newest on the month ends' footing").toBe(true);
    expect(snaps.every((s) => cents(s.liquid_total) - sumCents(s.rows.filter((r) => r.liquid && r.value > 0))
      === sumCents(s.rows.filter((r) => r.liquid && r.value < 0))),
    "the export's liquid_total differs from the liquid assets by the liquid rows below zero alone").toBe(true);
    const folded = snaps.length > 1 && onFooting(snaps[snaps.length - 1].rows).length !== snaps[snaps.length - 1].rows.length;
    expect((within(liqCard).queryByTestId("trend-liquidity-footing") !== null) === folded,
      "the card says the last point is on the month ends' footing exactly when it is").toBe(true);
    const shareCol = column(tr.table, "Liquid share");
    expect(snaps.every((s) => Number(shareCol.get(s.snapshot_date) ?? 2) <= 1), "a share of assets never passes 100%").toBe(true);
    expect(noPartialDate(tr.table.rows.map((r) => String(r[0])).join(" ") + (liqCard.textContent ?? ""), partial),
      "no partial month end in the liquidity table or card").toBe(true);
    chart.pairs.length = 0;
    fireEvent.click(within(liqCard).getByRole("button", { name: "dollars" }));
    const dollars = chartWith((o) => o.series?.[0]?.name === "Liquid assets");
    expectColumnIsSeries(dollars.table, dollars.option.series[0]);
  }, ROUTE_TIMEOUT);

  // the calendar spans the months of the window that hold spending: at most 12 on a desktop and 3 on a phone (drawn on
  // its side, a 44px row per week, so a day is a finger's target), its first and last month each holding a day it
  // colours, and its title names them
  // Cash Flow's money axis shares the rate axis' grid only when it costs nothing: in the default six months the income is
  // taller than any spending is deep, and $0 sits on the line of 0%; a window holding a month whose spending is deeper
  // than any income is tall takes even steps of its own, its top the first step at or above the tallest bar
  it("Cash Flow's money axis shares the rate axis' grid only when the bars need the room anyway", async () => {
    const axesAt = async (hash: string) => {
      chart.options.length = 0;
      await mount(hash);
      await settled("/cash-flow");
      const o = chart.options.filter((x) => x?.series?.[0]?.name === "Income").pop();
      cleanup();
      const drawn = (o.series.slice(0, 3) as { data: number[] }[]).flatMap((s) => s.data);
      return { money: o.yAxis[0], rate: o.yAxis[1], up: Math.max(0, ...drawn), down: Math.max(0, ...drawn.map((v) => -v)) };
    };
    const zeroAt = (a: { min: number; max: number }) => (0 - a.min) / (a.max - a.min);
    const six = await axesAt("#/cash-flow");
    expect(six.up >= six.down && Math.abs(zeroAt(six.money) - zeroAt(six.rate)) < 1e-9, "6M: one grid, $0 on 0%").toBe(true);
    const deepest = await axesAt("#/cash-flow?win=all");
    expect(deepest.up < deepest.down, "All holds a month whose spending is deeper than any income is tall").toBe(true);
    expect(deepest.money.max >= deepest.up && deepest.money.max - deepest.money.interval < deepest.up,
      "All: the money axis reaches no further above zero than the tallest bar needs").toBe(true);
    expect(deepest.money.min <= -deepest.down, "no bar clipped").toBe(true);
  }, ROUTE_TIMEOUT);

  // A balance chart draws every balance its history holds (a card's balance really is low just after its payment), and
  // where histories start with one balance a month and turn frequent a line under the chart says so, on a date one of
  // them turns on (lib/balanceCharts.ts monthlyThrough)
  it("every balance chart draws every balance the history holds, and says where the spacing changes", async () => {
    const exported = json("balances_timeseries.json").series as Record<string, { date: string }[]>;
    const turns = new Set(Object.values(exported).map(monthlyThrough).filter((d): d is string => d !== null).map(fmtDate));
    const spacingLine = /^(?:One balance a month through|Some accounts have one balance a month in their early history, the latest through) (.+, \d{4}); more often since\.$/;
    expect(Object.keys(exported).length > 0, "the exports hold balance series").toBe(true);
    const idByName = new Map((json("accounts.json").accounts as { id: string; name: string }[]).map((a) => [a.name, a.id]));
    for (const route of ["/accounts", "/accounts/investments", "/accounts/liabilities"]) {
      chart.options.length = 0;
      await mount(`#${route}`);
      await settled(route);
      const lines = chart.options.flatMap((o) => (o?.xAxis?.type === "time" ? o.series : []) as { name: string; data: unknown[] }[])
        .filter((s) => idByName.has(s.name));
      expect(lines.length > 0, `${route}: balance lines drawn`).toBe(true);
      for (const s of lines) {
        expect(s.data.length === exported[idByName.get(s.name)!].length, `${route}: a line draws every point its history holds`).toBe(true);
      }
      const notes = screen.queryAllByTestId("balance-spacing").map((n) => n.textContent ?? "");
      expect(notes.length > 0 && notes.every((t) => turns.has(spacingLine.exec(t)?.[1] ?? "")),
        `${route}: the spacing line, on a date a history turns on`).toBe(true);
      cleanup();
    }
  }, ROUTE_TIMEOUT);

  it("the Explore calendar spans the window's months with spending, at most 12 on a desktop and 3 on a phone", async () => {
    const shown = async (all: boolean) => {
      chart.options.length = 0;
      // a filter in the URL is a deep link: all time, unless it names a window
      await mount(`#/spending?categories=Dining${all ? "" : "&win=6m"}`);
      await settled("/spending");
      const card = await screen.findByTestId("card-spend-calendar", {}, { timeout: 8000 });
      const text = card.textContent ?? "";
      const o = chart.options.filter((x) => x?.calendar).pop();
      cleanup();
      const [from, to] = o.calendar.range as [string, string];
      const days = (o.series[0].data as [string, number][]).map(([d]) => d);
      const months = (Number(to.slice(0, 4)) - Number(from.slice(0, 4))) * 12 + Number(to.slice(5, 7)) - Number(from.slice(5, 7)) + 1;
      return { text, o, months, from, to, days };
    };
    for (const [phone, cap] of [[false, 12], [true, 3]] as const) {
      media.phone = phone;
      try {
        for (const all of [false, true]) {
          const c = await shown(all);
          const where = `${phone ? "phone" : "desktop"}${all ? ", All" : ", 6M"}`;
          expect(c.months >= 1 && c.months <= cap, `${where}: at most ${cap} months`).toBe(true);
          expect(c.days.some((d) => d.slice(0, 7) === c.from.slice(0, 7)) && c.days.some((d) => d.slice(0, 7) === c.to.slice(0, 7)),
            `${where}: the first and the last month each hold spending`).toBe(true);
          expect(c.text.includes(fmtMonth(c.from.slice(0, 7))) && c.text.includes(fmtMonth(c.to.slice(0, 7))), `${where}: the title names them`)
            .toBe(true);
          // the scale names the amounts at its two ends
          expect(Array.isArray(c.o.visualMap.text) && c.o.visualMap.text.length === 2, `${where}: the scale's two ends are labelled`).toBe(true);
          // every calendar label takes the theme's text color (ECharts' default year label is a fixed light grey that
          // vanished on a light card)
          expect(["dayLabel", "monthLabel", "yearLabel"].every((k) => typeof c.o.calendar[k]?.color === "string"),
            `${where}: day, month and year labels are themed`).toBe(true);
          // a phone's calendar stands on its side, a 44px row per week
          expect(phone ? c.o.calendar.orient === "vertical" && c.o.calendar.cellSize[1] === 44 : c.o.calendar.orient === undefined,
            `${where}: ${phone ? "on its side" : "across"}`).toBe(true);
        }
      } finally {
        media.phone = false;
      }
    }
  }, ROUTE_TIMEOUT);

  // "missed" and "data stale" do not occur on the household's data, so a synthetic recurring.json + accounts.json
  // (dated years ago - the verdicts cannot rot) pins both chips, in the phone list and the table
  it("a missed and a data-stale schedule render their chips (never seen on the household's data)", async () => {
    const rec = (vendor: string, account_id: string) => ({
      vendor, account_id, category: "Subscriptions", cadence: "monthly", amount_mode: "fixed", occurrences: 12,
      first_seen: "2019-01-05", last_seen: "2020-01-05", next_expected: "2020-02-05", active: true,
      typical_amount: 12, last_amount: 12, annualized: 144, monthly_equivalent: 12, price_changes: [],
      schedule: true, expected_dates: ["2020-02-05", "2020-03-05", "2020-04-05"], grace_days: 6,
      confidence: 1, median_gap_days: 31,
    });
    const owner: string = json("assumptions.json").owners[0];   // any owner the household has
    const acct = (id: string, txn_data_end: string) => ({
      id, name: `Synthetic ${id}`, institution: "Test", owner, kind: "checking", asset_class: "Cash",
      txn_data_start: "2019-01-01", txn_data_end, latest_balance: null, latest_balance_as_of: null, ledger_drift: null,
    });
    const base = stubServe("recurring.json", () => ({ items: [rec("Synthetic Gym", "syn_a"), rec("Synthetic Stale", "syn_b")] }));
    stubServe("accounts.json", () => ({ accounts: [acct("syn_a", "2020-03-01"), acct("syn_b", "2020-02-06")] }));
    try {
      await mount("#/spending/recurring");
      await settled("/spending/recurring");
      // one chip in the phone list and one in the table: 2 each (jsdom applies no media query)
      expect(screen.getAllByText("missed · expected Feb 5, 2020").length, "missed chip").toBe(2);
      expect(screen.getAllByText("data stale").length, "data-stale chip").toBe(2);
      expect(screen.getAllByText(/missed only/).length, "the missed-only pill").toBeGreaterThan(0);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // Phone pin thinning is opt-in per series (tuneForMobile reads `phoneMaxPins`). Home's net-worth line is the one chart
  // that draws the $100K milestone pins (the newest three crossings of the comparable snapshots), and it opts in.
  it("phone pin thinning: Home's net-worth line thins to its newest pin", async () => {
    snapshotSplit();
    const levels = truth().figures.levels_crossed;
    branch(levels.length > 1, "net worth crosses several $100K levels across the comparable month ends");
    const pinned = () => chart.options.flatMap((o) => (Array.isArray(o?.series) ? o.series : []))
      .filter((s: any) => s?.markPoint?.data?.length > 0);
    chart.options.length = 0;
    await mount("#/");
    await settled("/");
    const home = pinned();
    expect(home.map((s: any) => s.name), "the series on Home that draw pins").toEqual(["Net worth (snapshots)"]);
    expect(home[0].phoneMaxPins).toBe(1);
    // truth's levels_crossed and the app's pins are two definitions that agree on this household: truth lists the $100K
    // levels between the first and the last comparable month end; the app (lib/milestones.ts milestoneCrossings) pins
    // each level's first upward crossing across the comparable snapshots, the current snapshot included
    expect(home[0].markPoint.data.length === Math.min(3, levels.length), "Home draws a pin per level crossed, the newest three, on a desktop").toBe(true);
    const homeOption = chart.options.find((o) => o?.series?.includes(home[0]));
    const tunedHome: any = tuneForMobile(homeOption);
    expect(tunedHome.series.find((s: any) => s.name === "Net worth (snapshots)").markPoint.data).toHaveLength(1);
    // the pins' labels overhang the pin's head onto the card: a purple outline keeps them readable on a light card
    expect(typeof home[0].markPoint.label.textBorderColor === "string" && home[0].markPoint.label.textBorderWidth >= 1,
      "the pin labels carry an outline").toBe(true);
  }, ROUTE_TIMEOUT);

  // At 390px the waterfall showed 3 of its 5 names and clipped "Now (…". On a phone every column
  // keeps its name: interval 0 and hideOverlap off survive the phone tuning, each name is two short
  // lines, and the Now/Start edges carry a month instead of a full date.
  it("home: at phone width the waterfall names every column, in short lines", async () => {
    expect(snapshotSplit().comparable.length >= 2, "two comparable snapshots or more: a start and a now").toBe(true);
    media.phone = true;
    chart.options.length = 0;
    try {
      await mount("#/");
      await settled("/");
      await screen.findByTestId("card-decomposition", {}, { timeout: 8000 });
      const wf = chart.options.find((o) => o?.series?.some?.((s: any) => s?.stack === "wf"));
      expect(wf !== undefined, "the waterfall option was built").toBe(true);
      const tuned: any = tuneForMobile(wf);
      const names: string[] = tuned.xAxis.data;
      expect(names.length >= 3 && names.length === wf.series[1].data.length, "one name per column").toBe(true);
      expect(tuned.xAxis.axisLabel.interval === 0 && tuned.xAxis.axisLabel.hideOverlap === false,
        "no label is thinned or hidden").toBe(true);
      expect(names.every((n) => n.split("\n").every((l) => l.length <= 8)), "every line is short").toBe(true);
      expect(names[0].startsWith("Start\n") && names[names.length - 1].startsWith("Now\n"), "the edges").toBe(true);
    } finally {
      media.phone = false;
    }
  }, ROUTE_TIMEOUT);

  /** the assets export with both kinds of snapshot among those a page shows (the comparable ones): the file itself when
   *  they hold both, else the file with its two oldest comparable snapshots turned pre-mortgage the way the ETL books
   *  one (no Mortgage row or class, its balance back in the totals, the mortgage account in `missing`,
   *  `mortgage_included: false`), which keeps them comparable, so the † paths stay driven while every comparable
   *  snapshot subtracts the mortgage. The partial month ends are served unchanged and stay off the page. */
  function assetsWithPreMortgage(): any {
    const doc = json("assets_timeseries.json");
    const { comparable } = comparableSnapshots(some(doc.snapshots, "net-worth snapshots"));
    if (comparable.some((s) => !s.mortgage_included) && comparable.some((s) => s.mortgage_included)) return doc;
    const turn = new Set(comparable.slice(0, Math.min(2, comparable.length - 1)).map((s) => s.snapshot_date));
    const turned = (s: any) => {
      const owed: any[] = s.rows.filter((r: any) => r.asset_class === "Mortgage");
      const sum = owed.reduce((a, r) => a + r.value, 0);
      const byOwner = { ...s.by_owner };
      for (const r of owed) byOwner[r.owner] -= r.value;
      return { ...s, mortgage_included: false, total: s.total - sum, total_excl_house_car: s.total_excl_house_car - sum,
        by_owner: byOwner, by_class: Object.fromEntries(Object.entries(s.by_class).filter(([k]) => k !== "Mortgage")),
        rows: s.rows.filter((r: any) => r.asset_class !== "Mortgage"),
        missing: [...new Set([...s.missing, ...owed.map((r) => r.account)])].sort() };
    };
    return { ...doc, snapshots: doc.snapshots.map((s: any) => (turn.has(s.snapshot_date) ? turned(s) : s)) };
  }
  /** Serve the real exports, except `name`, whose body is `body()` (a synthetic payload). */
  function stubServe(name: string, body: () => unknown) {
    const base = globalThis.fetch;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (String(url).replace(/^\.\//, "") === name) return new Response(JSON.stringify(body()), { status: 200 });
      return base(url as string, init);
    });
    return base;
  }
  /** a valid transaction row (a real one's keys) with `over` applied — the synthetic payloads' unit. The base is an
   *  ORDINARY row: the export's first row may be a split parent, and a synthetic row must not inherit `is_parent` */
  const synthTxn = (over: Record<string, unknown>) => ({
    ...json("transactions.json").transactions.find((t: { is_parent?: boolean; parent_id?: string | null }) => !t.is_parent && !t.parent_id),
    ...over });

  // Home's "next 30 days" chips come from scheduleStatus. These rows sit years before the ingest
  // clock, so the verdicts cannot rot: an overdue schedule on an account whose data reaches its
  // deadline is "missed"; on an account with no data horizon it is "data stale", never "missed".
  it("home: next 30 days shows the missed chip first, then the data-stale chip", async () => {
    expect(plantedAccount("seam.account").txn_data_end !== null, "the missed schedule's account has a data horizon").toBe(true);
    const synth = (vendor: string, account_id: string) => ({
      vendor, account_id, category: "Subscriptions", cadence: "monthly", amount_mode: "fixed", occurrences: 12,
      first_seen: "2019-01-05", last_seen: "2019-12-05", next_expected: "2020-01-05", active: true,
      typical_amount: 20, last_amount: 20, annualized: 240, monthly_equivalent: 20, price_changes: [],
      schedule: true, expected_dates: ["2020-01-05", "2020-02-05", "2020-03-05"], grace_days: 6,
      confidence: 1, median_gap_days: 31,
    });
    const base = stubServe("recurring.json", () => ({
      items: [synth("Synthetic Stale", "no-such-account"), synth("Synthetic Missed", ACCOUNT_ID)],
    }));
    try {
      await mount("#/");
      await settled("/");
      const card = await screen.findByTestId("card-next-30-days", {}, { timeout: 8000 });
      const rows = Array.from(card.querySelectorAll("li")).map((li) => li.textContent ?? "");
      expect(rows.length, "two rows").toBe(2);
      expect(/Synthetic Missed/.test(rows[0]) && /missed · expected/.test(rows[0]), "missed row first").toBe(true);
      expect(/Synthetic Stale/.test(rows[1]) && /data stale/.test(rows[1]), "data-stale row second").toBe(true);
      expect(/\d+ more →/.test(card.textContent ?? ""), "two rows are not capped").toBe(false);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // 14 overdue schedules on an account with no data horizon (all "data stale", dated years ago, so
  // the verdict cannot rot): the list stops at 12 rows and says how many it hid; the total sums the
  // rows listed, each schedule once, and is worded as the "next charge of each", never as a month's spend
  it("home: next 30 days caps at 12 rows, says how many are hidden and words the total honestly", async () => {
    const synth = (n: number) => ({
      vendor: `Synthetic ${String(n).padStart(2, "0")}`, account_id: "no-such-account", category: "Subscriptions",
      cadence: "weekly", amount_mode: "fixed", occurrences: 12, first_seen: "2019-01-05", last_seen: "2019-12-05",
      next_expected: "2020-01-05", active: true, typical_amount: 20, last_amount: 20, annualized: 1040,
      monthly_equivalent: 87, price_changes: [], schedule: true,
      expected_dates: ["2020-01-05", "2020-01-12", "2020-01-19"], grace_days: 3, confidence: 1, median_gap_days: 7,
    });
    const base = stubServe("recurring.json", () => ({ items: Array.from({ length: 14 }, (_, i) => synth(i + 1)) }));
    try {
      await mount("#/");
      await settled("/");
      const card = await screen.findByTestId("card-next-30-days", {}, { timeout: 8000 });
      expect(card.querySelectorAll("li").length, "12 rows shown").toBe(12);
      const text = card.textContent ?? "";
      expect(/2 more →/.test(text), "2 more →").toBe(true);
      expect(/= the next charge of each of the 12 schedules listed, not the 2 more/.test(text), "total wording").toBe(true);
      expect(/≈ \$240 /.test(text), "total counts each listed schedule once").toBe(true);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // BudgetCard used to render nothing when no full month existed; on its own tab that would be a
  // blank page, so it says why instead (and the payroll tip still follows it)
  it("budget: with no cash-flow months the check explains itself instead of vanishing", async () => {
    const base = stubServe("cashflow_monthly.json", () => ({ months: [] }));
    try {
      await mount("#/cash-flow/budget");
      await settled("/cash-flow/budget");
      const card = await screen.findByTestId("card-budget", {}, { timeout: 8000 });
      expect(/no full month of cash-flow data yet/.test(card.textContent ?? ""), "empty-state title").toBe(true);
      expect(within(card).queryByRole("button", { name: "Edit budgets" }) !== null, "the editor stays reachable").toBe(true);
      // the split keeps its card and says it waits for the grid: no chart without one
      const split = await screen.findByTestId("card-fixed-vs-discretionary", {}, { timeout: 8000 });
      expect(within(split).queryByTestId("chart") === null, "no chart without a grid").toBe(true);
      expect(within(split).queryByTestId("not-enough-history") !== null, "the split's line").toBe(true);
      await screen.findByTestId("card-payroll", {}, { timeout: 8000 });
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // the grid's columns are FULL months: the month in progress (the ETL is still filling it) must never be a column, nor
  // feed a baseline, and neither may a month a row was dated ahead into. On this household the ingest runs on the last
  // day of its month and the bank dated one row (truth's future-dated row) into the next month: the export keeps a month
  // for that row, the ingest month is still in progress (lib/agg.ts monthInProgress), and the newest column is the month
  // before it. Boolean asserts on header labels only.
  it("budget: the grid's columns are the last six full months - neither the month in progress nor a month dated ahead appears", async () => {
    await mount("#/cash-flow/budget");
    await settled("/cash-flow/budget");
    const card = await screen.findByTestId("card-budget", {}, { timeout: 8000 });
    const months: string[] = json("cashflow_monthly.json").months.map((m: { month: string }) => m.month);
    const ingestMonth = String(json("manifest.json").generated_at).slice(0, 7);
    const ahead = String(planted("future.date")).slice(0, 7);
    branch(ingestMonth === truth().end.slice(0, 7) && ahead > ingestMonth && months[months.length - 1] === ahead,
      "the ingest month is the data's end, and the export's newest month is the one a row was dated ahead into");
    const inProgress = exportsInProgress(), lastFull = prevMonth(inProgress);
    expect(inProgress === ingestMonth, "the ingest month is in progress, not the month dated ahead into").toBe(true);
    branch(months.filter((m) => m < inProgress).length >= 7, "seven full cash-flow months or more before it");
    const heads = within(card).getAllByRole("columnheader").map((h) => h.textContent);
    expect(heads.length === 8, "Category + six months + Trend").toBe(true);
    expect(heads.includes(fmtMonth(inProgress)), "the month in progress is not a column").toBe(false);
    expect(heads.includes(fmtMonth(ahead)), "the month a row was dated ahead into is not a column").toBe(false);
    expect(heads[heads.length - 2] === fmtMonth(lastFull), "the newest column is the last full month").toBe(true);
    expect(/last 6 full months/.test(card.textContent ?? ""), "the title names the span").toBe(true);
  }, ROUTE_TIMEOUT);

  // A month after the month in progress has not begun: no chart axis, series, view-data table or column on the pages
  // that count by month shows it, nor the vendor drill, and Cash Flow and Reports star the month in progress where they
  // draw it. On this household one row (truth's future-dated row) is dated into the month after the ingest month, and the
  // export keeps a cash-flow month for it.
  it("no chart, table or vendor drill shows a month that has not begun; Cash Flow and Reports star the month in progress", async () => {
    const inProgress = exportsInProgress();
    const ahead = String(planted("future.date")).slice(0, 7);
    branch(ahead > inProgress && json("cashflow_monthly.json").months.some((m: { month: string }) => m.month === ahead),
      "the export holds a month for the row dated ahead, after the month in progress");
    const words = [ahead, fmtMonth(ahead), fmtMonthAxis(ahead)];
    const names = (s: string) => words.some((w) => s.startsWith(w) || s.includes(` ${w}`) || s === w);
    const seen = new Map<string, { option: any; table: Table | undefined }[]>();
    // Reports as a chart (its bare tab is a table, which no stubbed chart records)
    const pages: [string, string][] = [["/cash-flow", ""], ["/cash-flow/budget", ""], ["/cash-flow/income-statement", ""],
      ["/trends", ""], ["/spending", ""], ["/spending/reports", "?groupBy=category&interval=month&measure=spend&chart=stacked"]];
    for (const [route, query] of pages) {
      chart.pairs.length = 0;
      await mount(`#${route}${query}`);
      await settled(route);
      for (const { option, table } of chart.pairs) {
        const axes = [option?.xAxis].flat().flatMap((a: any) => (Array.isArray(a?.data) ? a.data : [])).map(String);
        expect(axes.some(names), `${route}: no axis label names the month dated ahead`).toBe(false);
        const points = [option?.series].flat().flatMap((s: any) => (Array.isArray(s?.data) ? s.data : []))
          .filter((d: unknown) => Array.isArray(d)).map((d: unknown[]) => String(d[0]));
        expect(points.some(names), `${route}: no point is dated in the month dated ahead`).toBe(false);
        expect((table?.rows ?? []).some((r) => names(String(r[0]))), `${route}: no view-data row names it`).toBe(false);
      }
      const heads = [...document.querySelectorAll('[role="columnheader"], th')].map((h) => (h.textContent ?? "").trim());
      expect(heads.some(names), `${route}: no column names it`).toBe(false);
      seen.set(route, [...chart.pairs]);
      cleanup();
    }
    const cashFlow = seen.get("/cash-flow")!.find((p) => p.option?.series?.some((s: { name?: string }) => s.name === "Savings rate"));
    expect(cashFlow !== undefined, "Cash Flow's main chart was built").toBe(true);
    const months: string[] = cashFlow!.option.xAxis.data;
    expect(months[months.length - 1] === inProgress, "Cash Flow's chart ends on the month in progress").toBe(true);
    expect(String(cashFlow!.option.xAxis.axisLabel.formatter(inProgress)).endsWith("*"), "and stars it").toBe(true);
    expect(months.slice(0, -1).every((m) => !String(cashFlow!.option.xAxis.axisLabel.formatter(m)).endsWith("*")), "and no other month").toBe(true);
    expect(cashFlow!.table!.rows[cashFlow!.table!.rows.length - 1][0] === `${inProgress} (in progress)`, "its table says so").toBe(true);
    const report = seen.get("/spending/reports")!.find((p) => p.option?.series?.some((s: { stack?: string }) => s.stack === "report"));
    expect(report !== undefined, "the report chart was built").toBe(true);
    const periods: string[] = report!.table!.rows.map((r) => String(r[0]));
    expect(periods[periods.length - 1] === `${fmtMonth(inProgress)}*`, "Reports end on the month in progress, starred").toBe(true);
    // the vendor drill of the row dated ahead: no bar, recent row or "last seen" after the month in progress, and a line
    // that says one charge is left out
    const future = String(planted("future.date"));
    const aheadRows = categoryView(json("transactions.json").transactions).filter((t: { date: string }) => t.date === future);
    branch(aheadRows.length === 1 && typeof aheadRows[0].vendor === "string", "one row is dated ahead, and it has a vendor");
    chart.clickName = aheadRows[0].vendor;
    chart.options.length = 0;
    try {
      await mount("#/spending");
      await settled("/spending");
      fireEvent.click(within(await screen.findByTestId("card-top-vendors", {}, { timeout: 8000 })).getByTestId("chart"));
      const drill = await screen.findByTestId("card-vendor-drill", {}, { timeout: 8000 });
      // (a vendor whose only charge is the one dated ahead has no chart at all: nothing is counted yet)
      const monthly = chart.options.filter((o) => o?.series?.length === 1 && Array.isArray(o?.xAxis?.data)
        && o.xAxis.data.length > 0 && o.xAxis.data.every((m: unknown) => /^\d{4}-\d{2}$/.test(String(m))));
      expect(monthly.every((o) => o.xAxis.data.every((m: string) => m <= inProgress)), "the drill draws no month after the month in progress").toBe(true);
      expect((drill.textContent ?? "").includes(future), "no recent row is the one dated ahead").toBe(false);
      expect((drill.textContent ?? "").includes(fmtDate(future)), "nor is it the vendor's last seen day").toBe(false);
      expect(/^1 charge dated ahead/.test(within(drill).getByTestId("vendor-drill-ahead").textContent ?? ""), "the drill says it left one out").toBe(true);
    } finally {
      chart.clickName = "";
    }
  }, ROUTE_TIMEOUT);

  // the tip used to say "the savings rate above" - on the Budget tab there is none; the savings rate
  // lives on the Overview, so the tip links there (no payroll facts are saved in a fresh jsdom)
  it("budget: the payroll tip points at the Overview's savings rate with a live link", async () => {
    await mount("#/cash-flow/budget");
    await settled("/cash-flow/budget");
    const tip = await screen.findByTestId("card-payroll", {}, { timeout: 8000 });
    expect(/savings rate above/.test(tip.textContent ?? ""), "no 'above' on a tab with no savings rate").toBe(false);
    const link = tip.querySelector("a");
    expect(link?.getAttribute("href"), "tip link").toBe("#/cash-flow");
  }, ROUTE_TIMEOUT);

  // the `data` whens: cards that exist only for particular content, built here from a synthetic
  // payload so the assertion cannot rot with the household's real data
  it("review: an answer a config override outranked lands in the conflicts card", async () => {
    // overlay rule: the export is NEWER than the answer and the row's category differs -> conflicted
    const row = synthTxn({ id: "synth-conflict-1", type: "purchase", is_internal: false,
      needs_review: true, category: "Cash & Misc", subcategory: "Uncategorized" });
    const base = stubServe("transactions.json", () => {
      const doc = json("transactions.json");
      doc.transactions = [...doc.transactions, row];
      return doc;
    });
    stubServe("api/answers", () => ({
      version: 1, updated_at: "2020-01-02T00:00:00Z",
      answers: [{ key: `txn:${row.id}`, scope: "txn", txn_id: row.id,
        set: { category: "Groceries", subcategory: null }, answered_at: "2020-01-01T00:00:00Z" }],
    }));
    try {
      const conflicts = whenRow("card-review-conflicts");
      await mount(`#${conflicts.route}`);
      await settled(conflicts.route);
      await screen.findByTestId(conflicts.id, {}, { timeout: 8000 });
      ASSERTED_WHEN.add(conflicts.id);
    } finally {
      vi.stubGlobal("fetch", base);
      localStorage.removeItem(ANSWERS_LOCAL_KEY);       // the store mirrors its doc locally
    }
  }, ROUTE_TIMEOUT);

  // an empty inbox sends the family to the register to fix a misfiled row, so it links the table
  it("review: the all-caught-up note links the Transactions tab", async () => {
    const base = stubServe("transactions.json", () => {
      const doc = json("transactions.json");
      doc.transactions = doc.transactions.filter((t: Parameters<typeof isReviewCandidate>[0]) => !isReviewCandidate(t));
      return doc;
    });
    try {
      await mount("#/review");
      await settled("/review");
      const card = await screen.findByTestId("card-review-inbox", {}, { timeout: 8000 });
      expect(/All caught up/.test(card.textContent ?? ""), "the empty inbox").toBe(true);
      expect(card.querySelector('a[href="#/spending/transactions"]') !== null, "a link to the Transactions tab").toBe(true);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // Review is not a nav item: it is the sidebar footer's badge link, with a count pill that a
  // screen reader hears as "N to review". A synthetic needs_review row guarantees a pill whatever
  // the household's real inbox holds today.
  it("the sidebar footer links Review with a labelled count pill; the nav list does not", async () => {
    const row = synthTxn({ id: "synth-review-pill-1", type: "purchase", is_internal: false,
      needs_review: true, category: "Cash & Misc", subcategory: "Uncategorized" });
    const base = stubServe("transactions.json", () => {
      const doc = json("transactions.json");
      doc.transactions = [...doc.transactions, row];
      return doc;
    });
    try {
      await mount("#/");
      await settled("/");
      const aside = document.querySelector("aside");
      expect(aside !== null, "the sidebar").toBe(true);
      const link = aside!.querySelector<HTMLElement>('a[href="#/review"]');
      expect(link !== null, "a Review link in the sidebar").toBe(true);
      expect(aside!.querySelector('nav a[href="#/review"]') === null, "Review is not a nav-list item").toBe(true);
      const pill = within(link!).getByRole("img", { name: /^\d+ to review$/ });
      expect(pill.getAttribute("aria-label"), "pill label").toBe(`${pill.textContent} to review`);
      // the same row as the hubs above it: the phone tap height, not a shorter footer link
      const hubLink = aside!.querySelector<HTMLElement>('nav a[href="#/cash-flow"]');
      for (const cls of ["py-3", "md:py-1.5", "px-3"]) {
        expect(link!.className.split(/\s+/).includes(cls), `Review link has ${cls}`).toBe(true);
        expect(hubLink!.className.split(/\s+/).includes(cls), `hub link has ${cls}`).toBe(true);
      }
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // ...and when you are on Review the link wears the nav's active fill, not just a brighter label
  it("the sidebar Review link gets the nav's active treatment on /review", async () => {
    await mount("#/review");
    await settled("/review");
    const link = document.querySelector<HTMLElement>('aside a[href="#/review"]');
    expect(link !== null, "a Review link in the sidebar").toBe(true);
    for (const cls of navItemCls(true).split(/\s+/)) {
      expect(link!.className.split(/\s+/).includes(cls), `active Review link has ${cls}`).toBe(true);
    }
  }, ROUTE_TIMEOUT);

  // Data's "N transactions to review" and the sidebar badge are one number (isReviewCandidate). The
  // synthetic rows pull the old needs_review-only count apart from it: a needs_review purchase (both
  // count it), a needs_review transfer (not reviewable: the old count included it) and unflagged
  // Cash & Misc "Uncategorized" purchases (reviewable: the old count missed them) - as many of
  // those as it takes to outweigh whatever gap the real rows have, so the two rules differ by at
  // least one whatever today's ingest holds.
  it("Data's to-review count is the sidebar Review badge's number", async () => {
    const doc = json("transactions.json");
    some(categoryView(doc.transactions), "transactions");
    const flaggedOnly = (ts: { needs_review: boolean }[]) => ts.filter((t) => t.needs_review).length;
    // the page counts the category view: a split parent is never a review candidate
    const realGap = categoryView(doc.transactions).filter(isReviewCandidate).length - flaggedOnly(categoryView(doc.transactions));
    const uncategorized = Array.from({ length: Math.abs(realGap) + 2 }, (_, i) =>
      synthTxn({ id: `synth-count-u${i}`, type: "purchase", is_internal: false, needs_review: false,
        category: "Cash & Misc", subcategory: "Uncategorized" }));
    const rows = [
      synthTxn({ id: "synth-count-1", type: "purchase", is_internal: false, needs_review: true,
        category: "Groceries", subcategory: null }),
      synthTxn({ id: "synth-count-2", type: "transfer", is_internal: true, needs_review: true }),
      ...uncategorized,
    ];
    // the synthetic rows alone tell the two rules apart (data-independent)...
    expect(rows.filter(isReviewCandidate).length - flaggedOnly(rows) === uncategorized.length - 1,
      "the synthetic rows split the rules").toBe(true);
    doc.transactions = [...doc.transactions, ...rows];
    const expected = categoryView(doc.transactions).filter(isReviewCandidate).length;
    // ...and outweigh the real rows' gap, so the page cannot pass by counting needs_review
    expect(expected - flaggedOnly(categoryView(doc.transactions)) >= 1, "the two rules differ on the served data").toBe(true);
    const base = stubServe("transactions.json", () => doc);
    try {
      await mount("#/data");
      await settled("/data");
      const card = await screen.findByTestId("card-open-questions", {}, { timeout: 8000 });
      // the card's first paragraph (the title's "— 4" would otherwise glue onto the count)
      const onData = Number(/^(\d+) transactions? to review/.exec(card.querySelector("p")?.textContent ?? "")?.[1]);
      const pill = document.querySelector('aside a[href="#/review"] [role="img"]');
      expect(pill !== null, "the badge pill").toBe(true);
      const onBadge = Number(/^(\d+) to review$/.exec(pill!.getAttribute("aria-label") ?? "")?.[1]);
      expect(onData, "Data matches the badge").toBe(onBadge);
      expect(onData, "both are the reviewable-row count").toBe(expected);
      expect(card.querySelector('a[href="#/review"]') !== null, "the link to Review stays").toBe(true);
      // the uncategorized line opens the same inbox (it used to open the whole Cash & Misc category, already-categorized rows included)
      const uncatLine = [...card.querySelectorAll("p")].find((p) => /^Uncategorized spending:/.test(p.textContent ?? ""));
      expect(uncatLine !== undefined, "the uncategorized line is shown").toBe(true);
      expect(uncatLine!.querySelector("a")?.getAttribute("href"), "its link opens the Review inbox").toBe("#/review");
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // Explore never compares an in-progress month with a whole one, unlabelled, and never charts a month that has not
  // begun. The month in progress is the earlier of the newest month holding a row and the ingest month. The compare ends
  // on the data's last COMPLETE day - the ingest day is still coming in, as on Home's "this month so far" - or the
  // filter's To: a month cut short, or in progress, is set against the same days of the older month, and the title says
  // so in words ("the first N days of each"). The monthly chart ends at the month in progress - a row dated into the next
  // month counts nowhere until that month begins - and stars its newest month when it is in progress or the To date cuts
  // it, as Reports stars a partial period. Computed here from the export itself, independently of lib/agg. On this
  // household the ingest runs on its month's last day and one row (truth's future-dated row) is dated into the next month.
  it("explore: the month in progress is compared on its complete days and starred, and no month after it is charted", async () => {
    const dates: string[] = some(categoryView(json("transactions.json").transactions), "transactions").map((t: { date: string }) => t.date);
    const newest = dates.reduce((m, d) => (d > m ? d : m), "0000");
    const ingestDay = json("manifest.json").generated_at.slice(0, 10);
    const maxDate = newest > ingestDay ? ingestDay : newest;
    const inProgress = newest.slice(0, 7) < ingestDay.slice(0, 7) ? newest.slice(0, 7) : ingestDay.slice(0, 7);
    const dayOf = (iso: string) => Number(iso.slice(8, 10));
    const lastDayOf = (ym: string) => { const [y, mo] = ym.split("-").map(Number); return new Date(y, mo, 0).getDate(); };
    const inPart = (iso: string) => Number(iso.slice(8, 10)) < lastDayOf(iso.slice(0, 7));
    branch(!inPart(maxDate) && maxDate === ingestDay && newest === planted("future.date") && newest.slice(0, 7) > inProgress,
      "the data ends on its month's last day, the ingest day, with a row dated into the next month");
    /** the compare names `end`'s month (month-to-date when it is in progress or `end` cuts it), and the chart ends at
     *  `reach`'s month, starred when that month is in progress or `reach` cuts it */
    const expectExplore = async (hash: string, end: string, reach: string) => {
      chart.options = []; chart.pairs = [];
      await mount(hash);
      await settled("/spending");
      const card = await screen.findByTestId("card-period-compare", {}, { timeout: 8000 });
      const title = card.querySelector("h2, h3")?.textContent ?? "";
      expect(title.startsWith(`Month-over-month: ${fmtMonth(end.slice(0, 7))} vs `), `${hash}: the title names the compared month, in words`).toBe(true);
      expect(/\b\d{4}-\d{2}\b/.test(title), "no ISO month in the title").toBe(false);
      const heads = [...card.querySelectorAll("thead th")].map((th) => th.textContent ?? "");
      expect(heads[1], "the column head is the same month in words").toBe(fmtMonth(end.slice(0, 7)));
      expect(heads.some((h) => /\b\d{4}-\d{2}\b/.test(h)), "no ISO month in a column head").toBe(false);
      const partOf = (iso: string) => inPart(iso) || iso.slice(0, 7) === inProgress;
      if (partOf(end)) expect(title.includes(`, the first ${dayOf(end)} days of `), `${hash}: the title says how many days of each month`).toBe(true);
      else expect(/the first|MTD/.test(title), `${hash}: two whole months are named, nothing more`).toBe(false);
      const monthly = chartWith((o) => o.series?.some((s: { stack?: string }) => s.stack === "spend"));
      const axis: string[] = monthly.option.xAxis.data;
      const lastMonth = axis[axis.length - 1];
      expect(lastMonth, `${hash}: the chart ends at the month its counted rows reach`).toBe(reach.slice(0, 7));
      expect(axis.every((m) => m <= inProgress), `${hash}: no month after the month in progress is charted`).toBe(true);
      expect(monthly.table.rows.every((r: (string | number | null)[]) => String(r[0]).slice(0, 7) <= inProgress),
        `${hash}: nor listed in its view-data table`).toBe(true);
      expect(String(monthly.option.xAxis.axisLabel.formatter(lastMonth)).endsWith("*"), `${hash}: a month covered in part is starred`).toBe(partOf(reach));
      expect(monthly.table.rows[monthly.table.rows.length - 1][0] === `${lastMonth} (partial)`, `${hash}: its view-data row says partial`).toBe(partOf(reach));
      cleanup();
    };
    // the data's end: the compare stops the day before the ingest day (that day is still coming in) and sets those days
    // of the month in progress against the same days of the month before; the chart ends at that month, starred, and the
    // month the row was dated ahead into is no column
    const dayBefore = (iso: string) => { const d = new Date(`${iso}T00:00:00Z`); d.setUTCDate(d.getUTCDate() - 1); return d.toISOString().slice(0, 10); };
    await expectExplore("#/spending", dayBefore(maxDate), maxDate);
    // a To date in the middle of the month in progress: month-to-date, and that month starred
    const mid = `${inProgress}-15`;
    await expectExplore(`#/spending?from=${reportWindow()}&to=${mid}`, mid, mid);
    // a To date on the last day of the month before: a whole month, neither month-to-date nor starred
    const before = prevMonth(inProgress);
    const whole = `${before}-${String(lastDayOf(before)).padStart(2, "0")}`;
    await expectExplore(`#/spending?from=${reportWindow()}&to=${whole}`, whole, whole);
  }, ROUTE_TIMEOUT);

  // One "data through" day anchors every window (Indexes.dataThrough): on this household (the ingest on its month's last
  // day, one row dated into the next month) Explore's and the register's six-month default and Cash Flow's 6M start on the
  // same day, and the filter bar's spend is the charts' own total, with the row dated ahead named apart.
  it("explore, the register and Cash Flow open six-month windows from the same day; the bar's spend is the charts' total", async () => {
    const ingestDay = String(json("manifest.json").generated_at).slice(0, 10);
    branch(String(planted("future.date")) > ingestDay, "a row is dated after the ingest day");
    const fromOf = () => new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("from") ?? "";
    chart.pairs.length = 0;
    await mount("#/spending");
    await settled("/spending");
    await waitFor(() => expect(fromOf() !== "", "the six-month default is written").toBe(true), { timeout: 8000 });
    const explore = fromOf();
    // the chart as last drawn, once it follows the window (it follows the filters a beat behind, and its first draw can
    // come before the default window is written)
    const lastDrawn = () => chart.pairs.filter((p) => p.option?.series?.some((s: { stack?: string }) => s.stack === "spend")).at(-1);
    await waitFor(() => expect(lastDrawn()?.option.xAxis.data[0] === explore.slice(0, 7), "the chart is the six-month window's").toBe(true),
      { timeout: 8000 });
    const monthly = lastDrawn()!;
    const chartTotal = monthly.option.series.reduce((s: number, x: { data: number[] }) => s + x.data.reduce((a, v) => a + v, 0), 0);
    const bar = screen.getByTestId("filterbar");
    expect(/· spend ([^\s·]+)/.exec(bar.textContent ?? "")?.[1] === fmtUSD0(chartTotal), "the bar's spend is the monthly chart's total").toBe(true);
    expect(within(bar).getByTestId("filterbar-ahead").textContent === " · 1 dated into a month not begun, not in the spend or a report yet",
      "and it names the purchase dated into a month not begun").toBe(true);
    cleanup();
    await mount("#/spending/transactions");
    await settled("/spending/transactions");
    await waitFor(() => expect(fromOf() !== "", "the register's default is written").toBe(true), { timeout: 8000 });
    expect(fromOf() === explore, "the register opens on Explore's window").toBe(true);
    cleanup();
    chart.pairs.length = 0;
    await mount("#/cash-flow");
    await settled("/cash-flow");
    const main = chartWith((o) => o.series?.[0]?.name === "Income");
    expect(`${main.option.xAxis.data[0]}-01` === explore, "Cash Flow's 6M starts on the same day").toBe(true);
    expect(main.option.xAxis.data.length === 6, "six months that hold data").toBe(true);
  }, ROUTE_TIMEOUT);

  // The register lists the row the bank dated into the month after the ingest (truth's future-dated row): it is a real
  // row, marked "dated ahead" beside its date, and the default sort (Date, newest first) orders by the date shown, so it
  // leads. No other row is marked.
  it("transactions: the row dated ahead is listed, marked, and leads the newest-first register by its date", async () => {
    const ahead = String(planted("future.date"));
    const ingestDay = String(json("manifest.json").generated_at).slice(0, 10);
    branch(ahead > ingestDay, "a row is dated after the ingest day");
    rows.all = true;
    try {
      await mount("#/spending/transactions");
      await settled("/spending/transactions");
      const body = [...document.querySelectorAll('[role="row"][aria-expanded]')];
      const dates = body.map((r) => /\d{4}-\d{2}-\d{2}/.exec(r.textContent ?? "")?.[0] ?? "");
      expect(dates.length > 0 && dates.every((d) => d !== ""), "every row shows its date").toBe(true);
      const marked = body.map((r) => /dated ahead/.test(r.textContent ?? ""));
      expect(marked.every((m, i) => m === dates[i] > ingestDay), "exactly the rows dated after the ingest day are marked").toBe(true);
      const at = dates.indexOf(ahead);
      branch(dates.includes(ingestDay), "the ingest day holds rows");
      expect(at === 0, "the newest date leads").toBe(true);
      expect(dates.every((d, i) => i === 0 || d <= dates[i - 1]), "every row is in the order of the date it shows").toBe(true);
    } finally {
      rows.all = false;
    }
  }, ROUTE_TIMEOUT);

  /** The debt in net worth as the export states it: the newest snapshot's debt classes owed (a negative balance), in the
   *  order mortgage, margin, other debt, lower-cased and joined, and what they sum to. The household counts its cards in
   *  net worth (`net_worth.include_credit_cards`) and the snapshot holds their rows (accounts of kind credit-card), so
   *  card balances are among the classes and the note names them: a class line holding only cards is "credit cards". */
  const debtInNetWorth = () => {
    const snaps = some(json("assets_timeseries.json").snapshots, "net-worth snapshots");
    const cur = snaps[snaps.length - 1];
    const classes = ["Mortgage", "Margin", "Other Debt"].filter((k) => (cur.by_class[k] ?? 0) < 0);
    const cards = new Set(json("accounts.json").accounts.filter((a: { kind: string }) => a.kind === "credit-card").map((a: { id: string }) => a.id));
    const includeCards: boolean = json("assumptions.json").net_worth.include_credit_cards;
    branch(includeCards && cur.rows.some((r: { account_id: string | null }) => r.account_id !== null && cards.has(r.account_id)),
      "the household counts its cards in net worth, and the newest snapshot holds their rows");
    expect(classes.length > 0, "the newest snapshot owes a debt class").toBe(true);
    // a class's line is named for the credit cards when its owing rows are all cards, says so when some are
    const line = (k: string) => {
      const owing = cur.rows.filter((r: { asset_class: string; value: number }) => r.asset_class === k && r.value <= 0);
      const n = owing.filter((r: { account_id: string | null }) => r.account_id !== null && cards.has(r.account_id)).length;
      return n === 0 ? k.toLowerCase() : n === owing.length ? "credit cards" : `${k.toLowerCase()}, credit cards included`;
    };
    const named = classes.map(line).join(" + ");
    return { cur, includeCards, owed: classes.reduce((s, k) => s - cur.by_class[k], 0),
      note: /credit cards/.test(named) ? named : `${named} · credit cards included` };
  };

  // Home's Debt tile is the DEBT IN NET WORTH: the snapshot's debt classes owed, labelled so, built from the snapshot's
  // negative debt classes and saying whether card balances are among them, linking Liabilities (whose Total debt is every
  // debt). It names classes, never an account, so it cannot name a liability the snapshot does not hold or an account of
  // the household's own. Expectations are read off the export; the accounts it must not name are truth's brokerage and
  // lender.
  it("home: the Debt tile is the debt in net worth - the classes owed, whether cards are in it, linking Liabilities", async () => {
    const { note } = debtInNetWorth();
    const unnamed = [plantedAccount("brokerage.account"), plantedAccount("mortgage.account")]
      .flatMap((a) => [a.name, truth().accounts[a.id].institution]);
    await mount("#/");
    await settled("/");
    const kpis = await screen.findByTestId("kpis-home", {}, { timeout: 8000 });
    const text = kpis.textContent ?? "";
    expect(text.includes("Debt in net worth"), "the tile says what it is").toBe(true);
    expect(text.includes(note), "the classes owed, and that cards are among them").toBe(true);
    const tile = [...kpis.querySelectorAll("a")].find((a) => (a.textContent ?? "").includes("Debt in net worth"));
    expect(tile?.getAttribute("href"), "the tile opens Liabilities").toBe("#/accounts/liabilities");
    expect(unnamed.every((n) => !(tile!.textContent ?? "").includes(n)), "the tile names no account or institution of the household's brokerage or lender").toBe(true);
  }, ROUTE_TIMEOUT);

  // Accounts -> Liabilities, Planning -> House and Accounts -> Balances (and Home's Debt) each print the mortgage, all from
  // one mortgageBalance: assumptions.json `mortgage.balance`, the loan account's own row in the current snapshot.
  // The expected figure is read here from the exports, and the snapshot's row is checked to agree with it, independently of
  // lib/lifeSummary; each page has its own `it`, so a failing title names the page.
  /** the one mortgage balance every page must print, its source in words, and the debt in net worth, read off the
   *  exports (the mortgage account is truth's) */
  const oneMortgage = () => {
    const { cur, owed, note, includeCards } = debtInNetWorth();
    const mg = json("assumptions.json").mortgage;
    expect(mg !== null && typeof mg.balance === "number", "the exports name a mortgage account with a balance").toBe(true);
    expect(mg.account_id === plantedAccount("mortgage.account").id, "the mortgage block is truth's mortgage account").toBe(true);
    const rows = cur.rows.filter((r: { asset_class: string }) => r.asset_class === "Mortgage");
    expect(rows.length, "the served snapshot holds exactly one Mortgage row").toBe(1);
    expect(rows[0].account_id === mg.account_id && Math.abs(rows[0].value) === mg.balance,
      "the block's balance is that row, the mortgage account's own").toBe(true);
    const mortgage: number = mg.balance;
    return { mortgage, want: fmtUSD0(mortgage), label: "from the bank feed", owed, note, includeCards };
  };
  it("ONE mortgage balance on Liabilities: the tile, the card (named by its source) and the total's relation to the net-worth debt", async () => {
    const { want, label, owed } = oneMortgage();
    await mount("#/accounts/liabilities");
    await settled("/accounts/liabilities");
    expect((await screen.findByTestId("kpi-mortgage-balance", {}, { timeout: 8000 })).textContent).toContain(want);
    const card = screen.getByTestId("card-mortgage-summary").textContent ?? "";
    expect(card.includes(`Balance${want}`) && card.includes(label), "the card's balance, named by its source").toBe(true);
    // with the cards in net worth, Total debt IS the net-worth debt (the card balances counted once, as a class of it):
    // the same figure, and no "+ card balances" on top
    const total = screen.getByTestId("kpi-total-debt").textContent ?? "";
    expect(total.includes(fmtUSD0(owed)) && !total.includes("= net-worth debt + card balances"),
      "the total is the net-worth debt, the cards counted once").toBe(true);
  }, ROUTE_TIMEOUT);
  it("ONE mortgage balance on Planning -> Home: the principal balance", async () => {
    const { want } = oneMortgage();
    await mount("#/planning/home");
    await settled("/planning/home");
    expect((await screen.findByTestId("kpis-mortgage", {}, { timeout: 8000 })).textContent ?? "").toContain(`Principal balance${want}`);
  }, ROUTE_TIMEOUT);
  it("ONE mortgage balance on Balances: the balance sheet's Mortgage line, the loan row in the current balances and the liabilities note", async () => {
    const { mortgage, note } = oneMortgage();
    await mount("#/accounts");
    await settled("/accounts");
    expect((await screen.findByTestId("card-balance-sheet-liabilities", {}, { timeout: 8000 })).textContent ?? "").toContain(fmtUSD0(-mortgage));
    expect(screen.getByTestId("card-current-balances").textContent ?? "").toContain(fmtUSD0(-mortgage));
    expect(screen.getByTestId("liabilities-note").textContent ?? "").toContain(note);
  }, ROUTE_TIMEOUT);
  it("ONE mortgage balance on Home: the debt in net worth is that mortgage plus the other debt classes owed", async () => {
    const { mortgage, owed } = oneMortgage();
    const { cur } = debtInNetWorth();
    // the snapshot's own Mortgage class is that one balance, so the tile is the mortgage plus every other class owed
    expect(Math.abs(cur.by_class.Mortgage) === mortgage && owed > mortgage, "the mortgage and another debt class are owed").toBe(true);
    await mount("#/");
    await settled("/");
    expect(((await screen.findByTestId("kpis-home", {}, { timeout: 8000 })).textContent ?? "").includes(fmtUSD0(-owed)),
      "Home's debt in net worth").toBe(true);
  }, ROUTE_TIMEOUT);

  // Accounts -> Balances' "Cash on hand", Planning's "Liquid cash today" and Second House's "today" cash were three sums
  // (the bank feed's cash accounts; the snapshot's liquid Cash rows, twice - once inline). One definition now. The
  // expected figure is computed here from the export's newest snapshot, independently of lib/lifeSummary.
  it("Balances, Planning and Second House print one cash figure: the snapshot's liquid Cash rows", async () => {
    const snaps = some(json("assets_timeseries.json").snapshots, "net-worth snapshots");
    const cash = some(snaps[snaps.length - 1].rows
      .filter((r: { asset_class: string; liquid: boolean }) => r.asset_class === "Cash" && r.liquid), "liquid Cash rows in the newest snapshot")
      .reduce((sum: number, r: { value: number }) => sum + r.value, 0);
    const want = fmtUSD0(cash);
    await mount("#/accounts");
    await settled("/accounts");
    const kpis = await screen.findByTestId("kpis-balances", {}, { timeout: 8000 });
    expect((kpis.textContent ?? "").includes(`Cash on hand${want}`), "Balances' Cash on hand").toBe(true);
    cleanup();
    await mount("#/planning");
    await settled("/planning");
    const forecast = await screen.findByTestId("card-forecast", {}, { timeout: 8000 });
    expect((forecast.textContent ?? "").includes(`Liquid cash today${want}`), "Planning's Liquid cash today").toBe(true);
    cleanup();
    const base = stubServe("api/scenarios", () => SYNTHETIC_SCENARIOS);
    try {
      await mount("#/planning/second-house");
      await settled("/planning/second-house");
      const guard = await screen.findByTestId("kpis-guardrails", {}, { timeout: 8000 });
      expect((guard.textContent ?? "").includes(`${want} today`), "Second House's cash today").toBe(true);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // A split rule moves money across categories and the essentials basis; the register alone cannot say why. Data lists
  // the rules in force (vendor, each leg's share computed from the parts, how many purchases), with no amounts; the pages
  // a rule moved carry a one-line footnote. Everything is computed here from the export, independently of lib/splits.
  it("data: the split-rules card lists each rule with its legs' shares and counts, and carries no amount", async () => {
    const rows: any[] = json("transactions.json").transactions;
    const vendor = planted("split.vendor");
    const mine = some(rows.filter((t) => t.is_parent && t.vendor === vendor), "split purchases of truth's split vendor");
    const ids = new Set(mine.map((t) => t.id));
    const parts = some(rows.filter((t) => ids.has(t.parent_id)), "their parts");
    const total = parts.reduce((s, t) => s + Math.abs(t.amount), 0);
    // a leg is a (category, subcategory) pair: one category can hold several legs (Shopping / Electronics and / Home Goods)
    const legName = (t: any) => `${t.category}${t.subcategory ? ` / ${t.subcategory}` : ""}`;
    const legTotal = (leg: string) => parts.filter((t) => legName(t) === leg).reduce((s, t) => s + Math.abs(t.amount), 0);
    const legs = [...new Set(parts.map(legName))];
    setPrivate(true);                              // the card holds no amount, so privacy mode must leave it readable
    try {
      const row = whenRow("card-split-rules");
      await mount(`#${row.route}`);
      await settled(row.route);
      const card = await screen.findByTestId(row.id, {}, { timeout: 8000 });
      const text = card.textContent ?? "";
      expect(text.includes(vendor), "names the vendor").toBe(true);
      expect(text.includes(`${mine.length.toLocaleString("en-US")} transaction`), "counts the split purchases").toBe(true);
      for (const leg of legs) {
        expect(text.includes(`${Math.round(legTotal(leg) / total * 100)}% ${leg}`), "a leg with its share of the parts").toBe(true);
      }
      expect(/\$|•/.test(text), "no dollar amount and no mask in the card").toBe(false);
      ASSERTED_WHEN.add(row.id);
    } finally {
      setPrivate(false);
    }
  }, ROUTE_TIMEOUT);

  it("the pages a split rule moved carry the footnote: Budget, Income statement, Explore, Reports (by category) and the emergency-fund card", async () => {
    some(json("transactions.json").transactions.filter((t: { is_parent: boolean; vendor: string | null }) => t.is_parent
      && t.vendor === planted("split.vendor")), "split purchases of truth's split vendor");
    // Reports carries it grouped by category (the bare tab groups by vendor, where a split purchase is one row)
    const query: Record<string, string> = { "/spending/reports": "?groupBy=category" };
    for (const route of ["/cash-flow/budget", "/cash-flow/income-statement", "/spending", "/spending/reports", "/planning"]) {
      await mount(`#${route}${query[route] ?? ""}`);
      await settled(route);
      await waitFor(() => expect(screen.getAllByTestId("split-note").length, `${route} carries one footnote`).toBe(1), { timeout: 8000 });
      const note = screen.getByTestId("split-note");
      expect(/^Split rule: .* of each .* order\./.test(note.textContent ?? ""), `${route}: the note names shares and the vendor`).toBe(true);
      expect(note.querySelector("a")?.getAttribute("href"), `${route}: the note points at Data`).toBe("#/data");
      cleanup();
    }
  }, LOOP_TIMEOUT);

  // the projection defaults left Planning for Data: the page that used to hold the editor says where
  it("planning: the footnote links Data, where the projection defaults are edited", async () => {
    await mount("#/planning");
    await settled("/planning");
    const page = await screen.findByTestId("page-planning", {}, { timeout: 8000 });
    expect(page.querySelector('a[href="#/data"]') !== null, "a live link to Data").toBe(true);
    expect(screen.queryByTestId("card-projection-defaults") === null, "the editor is not here any more").toBe(true);
  }, ROUTE_TIMEOUT);

  // The forward-look KPIs scroll to a card by element id; a rename on either side would turn the click
  // into a silent no-op (ForwardLookStrip leaves a missing target alone), so the real page is asserted
  // to carry both ids, with the margin that keeps the card's top edge under the 48px sticky header.
  it("planning: the forward-look KPIs' scroll targets exist on the page and clear the sticky header", async () => {
    await mount("#/planning");
    await settled("/planning");
    await screen.findByTestId("kpi-cash-runway", {}, { timeout: 8000 });
    for (const [what, id] of Object.entries({ runway: SCROLL_TARGETS.runway, bearCase: SCROLL_TARGETS.bearCase })) {
      const el = document.getElementById(id);
      expect(el !== null, `${what}: #${id} is on the Projections tab`).toBe(true);
      expect(el!.className.split(/\s+/).includes("scroll-mt-14"), `${what}: #${id} has scroll-mt-14`).toBe(true);
    }
    expect(document.getElementById(SCROLL_TARGETS.runway)!.getAttribute("data-testid")).toBe("card-emergency-fund");
    expect(document.getElementById(SCROLL_TARGETS.bearCase)!.getAttribute("data-testid")).toBe("card-market-shock");
  }, ROUTE_TIMEOUT);

  // Settings' Elsewhere links land on cards of other pages by element id through `?card=` (lib/scrollTargets.ts): a
  // renamed id or a page that stops reading the key would leave the link on the page's top, so each link is followed
  // on the real pages, to the card it names, with focus on that card's heading
  it("settings: each Elsewhere link opens its page on its card, with focus on the card's heading", async () => {
    const asked: string[] = [];
    const before = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) { asked.push(this.id); };
    try {
      for (const { label, route, card } of ELSEWHERE) {
        cleanup();
        await mount("#/settings");
        await settled("/settings");
        fireEvent.click(within(screen.getByTestId("card-elsewhere")).getByRole("link", { name: label }));
        await settled(route);
        await waitFor(() => expect(asked.includes(card), `${label}: the page scrolled to #${card}`).toBe(true), { timeout: 8000 });
        const target = document.getElementById(card);
        expect(target !== null && target.className.split(/\s+/).includes("scroll-mt-14"), `${label}: #${card} clears the sticky header`)
          .toBe(true);
        expect(document.activeElement === target!.querySelector("h2"), `${label}: focus is on the card's heading`).toBe(true);
      }
    } finally {
      Element.prototype.scrollIntoView = before;
    }
  }, LOOP_TIMEOUT);

  // The runway KPI and the emergency-fund gauge read the same device-local toggles: with the largest
  // spending row flipped, both divide by the same essentials; with every row off, both say "—" (the
  // gauge drops its needle) instead of the KPI using the defaults or the gauge printing 0.0 mo.
  it("planning: the runway KPI and the emergency-fund gauge apply the same essential toggles", async () => {
    const rows = exportsEmergencyRows();
    expect(rows.length > 1, "the household has two emergency-fund rows or more").toBe(true);
    const essentialsOf = (el: Element) => /÷ (\S+)\/mo essentials/.exec(el.textContent ?? "")?.[1] ?? "?";
    try {
      // 1) flip the largest row: both must show the same (non-default) essentials
      localStorage.setItem(EFUND_OVERRIDES_KEY, JSON.stringify({ [rows[0].key]: !rows[0].essential }));
      await mount("#/planning");
      await settled("/planning");
      const kpi = await screen.findByTestId("kpi-cash-runway", {}, { timeout: 8000 });
      const gauge = await screen.findByTestId("card-emergency-fund", {}, { timeout: 8000 });
      const kpiEss = essentialsOf(kpi), gaugeEss = essentialsOf(gauge);
      expect(kpiEss !== "?" && kpiEss === gaugeEss, "the KPI divides by the gauge's essentials").toBe(true);
      cleanup();
      localStorage.removeItem(EFUND_OVERRIDES_KEY);
      await mount("#/planning");
      await settled("/planning");
      const kpiDefault = essentialsOf(await screen.findByTestId("kpi-cash-runway", {}, { timeout: 8000 }));
      expect(kpiDefault !== kpiEss, "the toggle actually moved the KPI").toBe(true);
      cleanup();
      // 2) every row off: "—" on both, the gauge with no needle
      localStorage.setItem(EFUND_OVERRIDES_KEY, JSON.stringify(Object.fromEntries(rows.map((r) => [r.key, false]))));
      chart.options.length = 0;
      await mount("#/planning");
      await settled("/planning");
      const kpiOff = await screen.findByTestId("kpi-cash-runway", {}, { timeout: 8000 });
      expect(kpiOff.querySelector("div.tabular-nums")?.textContent === "—", "KPI reads —").toBe(true);
      const gaugeOff = await screen.findByTestId("card-emergency-fund", {}, { timeout: 8000 });
      expect(/= — months/.test(gaugeOff.textContent ?? ""), "the gauge line reads — months").toBe(true);
      expect(/0\.0 months/.test(gaugeOff.textContent ?? ""), "never 0.0 months").toBe(false);
      const opt = chart.options.find((o) => o?.series?.[0]?.type === "gauge");
      expect(opt !== undefined, "the gauge option was built").toBe(true);
      expect(opt.series[0].detail.formatter(0) === "—", "the dial reads —").toBe(true);
      expect(opt.series[0].pointer.show === false, "no needle parked on 0").toBe(true);
    } finally {
      localStorage.removeItem(EFUND_OVERRIDES_KEY);
    }
  }, ROUTE_TIMEOUT);

  // One definition of "essential" per device: with every row toggled off, the forecast floor reads —
  // (never "clear" over a zero floor), and the Second House cushion names the toggled basis and reads — too.
  it("planning + second house: every essential row toggled off reads — on the forecast floor and says so on the cushion", async () => {
    const rows = exportsEmergencyRows();
    const allOff = JSON.stringify(Object.fromEntries(some(rows, "emergency-fund rows").map((r) => [r.key, false])));
    localStorage.setItem(EFUND_OVERRIDES_KEY, allOff);
    try {
      await mount("#/planning");
      await settled("/planning");
      const forecast = await screen.findByTestId("card-forecast", {}, { timeout: 8000 });
      expect(/Cash floor check\s*—/.test(forecast.textContent ?? ""), "the floor check reads —").toBe(true);
      expect(/clear/.test(forecast.textContent ?? ""), "never 'clear' over a zero floor").toBe(false);
      cleanup();
      const scenarios = stubServe("api/scenarios", () => SYNTHETIC_SCENARIOS);
      try {
        await mount("#/planning/second-house");
        await settled("/planning/second-house");
        const guard = await screen.findByTestId("kpis-guardrails", {}, { timeout: 8000 });
        expect(/no row marked essential on this device/.test(guard.textContent ?? ""), "the cushion names the toggled basis").toBe(true);
        expect(/Essentials cushion after purchase\s*—/.test(guard.textContent ?? ""), "cushion reads — with nothing essential").toBe(true);
      } finally { vi.stubGlobal("fetch", scenarios); }
    } finally {
      localStorage.removeItem(EFUND_OVERRIDES_KEY);
    }
  }, ROUTE_TIMEOUT);

  // The ticked rows are the headline essentials figure: a phone showed 9 of 18 inside a capped scroll
  // box with no cue. Every row renders, in a list with no height cap, each name clipped with an
  // ellipsis and its full text in a title.
  it("planning: the emergency-fund list shows every row, uncapped, with full names in titles", async () => {
    const rows = exportsEmergencyRows();
    some(rows, "emergency-fund rows");
    await mount("#/planning");
    await settled("/planning");
    const list = await screen.findByTestId("efund-rows", {}, { timeout: 8000 });
    expect(list.querySelectorAll("label").length === rows.length, "one row per spending row").toBe(true);
    const cls = list.className.split(/\s+/);
    expect(cls.some((c) => /^max-h-/.test(c) || /overflow-y-(auto|scroll)/.test(c)), "no capped scroll box").toBe(false);
    const labels = [...list.querySelectorAll("label")];
    expect(labels.every((l, i) => l.getAttribute("title") === `${rows[i].category} / ${rows[i].subcategory}`),
      "each row's full name is in its title").toBe(true);
    expect(labels.every((l) => l.querySelector(".truncate") !== null), "long names end in an ellipsis").toBe(true);
  }, ROUTE_TIMEOUT);

  // A regression pin: a retirement account the feed reports only a balance for (no
  // positions) projected from $0. A synthetic mapped account + a synthetic balance row stand in for
  // it, so the pin does not depend on what today's household holds.
  it("planning: a balance-only retirement account is counted in its owner's total and named in the footnote", async () => {
    // an adult of the household (truth), an owner in the export's people
    const owner = some(truth().household.members.filter((m) => m.role === "adult"), "adults").map((m) => m.name)
      .find((n) => json("assumptions.json").household.people.some((p: { name: string; role: string }) => p.name === n && p.role === "owner"));
    expect(owner !== undefined, "an adult of the household is an owner in the export").toBe(true);
    const ACCT = "Synthetic balance-only 401k";
    const first = stubServe("assumptions.json", () => {
      const doc = json("assumptions.json");
      doc.household.retirement_accounts = { ...doc.household.retirement_accounts, [ACCT]: owner };
      return doc;
    });
    stubServe("assets_timeseries.json", () => {
      const doc = json("assets_timeseries.json");
      const last = doc.snapshots[doc.snapshots.length - 1];
      last.rows = [...last.rows, { ...last.rows[0], account: ACCT, account_id: null, asset_class: "Retirement",
        value: 777_777, liquid: false, source: "simplefin_balance", as_of: last.snapshot_date }];
      return doc;
    });
    try {
      await mount("#/planning");
      await settled("/planning");
      const card = await screen.findByTestId("card-retirement", {}, { timeout: 8000 });
      expect((card.textContent ?? "").includes(`${ACCT}: counted from the feed's balance`), "the footnote names the account").toBe(true);
      // the owner's "retirement today" tile includes it (their own accounts only add to it)
      const label = within(card).getByText(`${owner} retirement today`);
      const shown = Number((label.nextElementSibling?.textContent ?? "").replace(/[^\d]/g, ""));
      expect(shown >= 777_777, "the owner's retirement total includes the balance-only account").toBe(true);
      expect((label.parentElement?.textContent ?? "").includes(ACCT), "the tile lists the account").toBe(true);
    } finally {
      vi.stubGlobal("fetch", first);
    }
  }, ROUTE_TIMEOUT);

  it("theme toggle flips the html class and survives the keyed remount", async () => {
    await mount("#/cash-flow");
    const btn = await screen.findByText(/Light mode/, {}, { timeout: 8000 });
    btn.click();
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(localStorage.getItem("theme")).toBe("light");
    await settled("/cash-flow");
    (await screen.findByText(/Dark mode/)).click();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem("theme")).toBe("dark");
  }, ROUTE_TIMEOUT);

  // a storage that refuses the write (Safari private mode, a full quota) must not break the toggle
  it("theme toggle still works when the storage refuses the write", async () => {
    const refuse = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
    try {
      await mount("#/cash-flow");
      const btn = await screen.findByText(/Light mode/, {}, { timeout: 8000 });
      btn.click();
      expect(document.documentElement.classList.contains("light"), "the theme flipped anyway").toBe(true);
      await settled("/cash-flow");
      (await screen.findByText(/Dark mode/)).click();
      expect(document.documentElement.classList.contains("dark")).toBe(true);
    } finally {
      refuse.mockRestore();
    }
  }, ROUTE_TIMEOUT);

  /** Serve the real exports except `gone`, which 404s. Returns the real fetch to restore. */
  function stubMissing(gone: string[]) {
    const base = globalThis.fetch;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (gone.includes(String(url).replace(/^\.\//, ""))) return new Response("gone", { status: 404 });
      return base(url as string, init);
    });
    return base;
  }

  it("a missing optional export degrades to a missing-data card, never a blank app", async () => {
    const base = stubMissing(["recurring.json"]);
    try {
      for (const route of ROUTE_PATTERNS) {
        await mount(`#${realize(route)}`);
        if (RECURRING_GATED.has(route)) {
          // Home gates one card on it (next 30 days); look at every missing-data card a page shows
          const cards = await screen.findAllByTestId("missing-data", {}, { timeout: 8000 });
          expect(cards.some((c) => /the recurring charges could not be loaded/.test(c.textContent ?? "")),
            `${route} says the recurring charges could not be loaded`).toBe(true);
        } else {
          await screen.findByTestId(pageTestId(route), {}, { timeout: 8000 });
        }
        await waitFor(() => absent(document.querySelector('[aria-busy="true"]'), "still loading"), { timeout: 8000 });
        if (route === "/") {
          // gating that card must not take the rest of Home down with it (boolean asserts: no DOM dump)
          expect(screen.queryByTestId("kpis-home") !== null, "/ still renders its hero row").toBe(true);
          expect(screen.queryByTestId("card-networth-history") !== null, "/ still renders the history chart").toBe(true);
        }
        if (route === "/planning") {
          // the strip gates ONE card on it (the lumpy-bills total): its siblings and the projections stay
          expect(screen.queryByTestId("kpi-big-bills") === null, "/planning shows no lumpy-bills total without recurring.json").toBe(true);
          expect(screen.queryByTestId("kpi-cash-runway") !== null, "/planning still renders the runway KPI").toBe(true);
          expect(screen.queryByTestId("kpi-bear-case") !== null, "/planning still renders the bear-case KPI").toBe(true);
          expect(screen.queryByTestId("card-retirement") !== null, "/planning still renders the retirement projection").toBe(true);
        }
        if (!RECURRING_GATED.has(route)) {
          // the set is exact: a route outside it must not gate on recurring.json
          // (boolean assert: a failure must not dump the card's text)
          const gated = screen.queryAllByTestId("missing-data")
            .some((c) => /the recurring charges could not be loaded/.test(c.textContent ?? ""));
          expect(gated, `${route} shows a recurring.json missing-data card but is not in RECURRING_GATED`).toBe(false);
        }
        absent(screen.queryByText(/failed to render/), `${route}: a section fell back to the boundary card`);
        cleanup();
      }
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, LOOP_TIMEOUT);

  // Accounts: each card is gated on the export it reads and survives the others' absence
  // (boolean asserts throughout: a failure must not dump the rendered DOM)
  it("a missing balances_timeseries.json gates only the balance-history cards on the Accounts tabs", async () => {
    debtInNetWorth();                                       // the household counts its cards in net worth
    const base = stubMissing(["balances_timeseries.json"]);
    try {
      const cases: [route: string, kept: string[], gone: string[]][] = [
        // the Illiquid KPI and the balance sheet read the asset snapshot, not the balance history
        ["/accounts", ["kpis-balances", "kpi-illiquid", "card-liquidity", "card-balance-sheet-assets",
          "card-balance-sheet-liabilities"], ["card-current-balances", "card-cash-balances"]],
        // the debt row (total, mortgage, margin) reads the snapshot and assumptions, so it stays, and with the cards in net
        // worth so does the card tile, which reads the snapshot's card rows; what reads the history (available credit,
        // the card charts) goes
        ["/accounts/liabilities", ["kpis-liabilities", "kpi-total-debt", "kpi-mortgage-balance", "kpi-margin-debt",
          "card-mortgage-summary", "kpi-card-debt"],
          ["kpi-available-credit", "card-card-balances", "card-utilization"]],
        ["/accounts/investments", ["kpis-investments", "card-concentration", "card-holdings"], ["card-investment-balances"]],
        // the account page: the header, the drift card (it reads accounts.json) and the register stay; the
        // history card is gated, and the register's footnote must not call the missing file "no observation"
        ["/accounts/:id", ["card-account-header", "card-ledger-drift", "card-account-transactions"], ["card-account-balance-history"]],
      ];
      for (const [route, kept, gone] of cases) {
        await mount(`#${realize(route)}`);
        await settled(route);
        const cards = await screen.findAllByTestId("missing-data", {}, { timeout: 8000 });
        expect(cards.some((c) => /the balance history could not be loaded/.test(c.textContent ?? "")),
          `${route} says the balance history could not be loaded`).toBe(true);
        for (const id of kept) {
          expect(screen.queryByTestId(id) !== null, `${route} keeps ${id}`).toBe(true);
        }
        for (const id of gone) {
          expect(screen.queryByTestId(id) === null, `${route} still renders ${id}`).toBe(true);
        }
        cleanup();
      }
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // the balances table links each account's page; the Balances tab is the current one there (aria-current)
  it("balances: an account's name links its page; the Balances tab stays current there", async () => {
    some(json("accounts.json").accounts.filter((a: { latest_balance: number | null }) => a.latest_balance !== null), "accounts with a balance");
    await mount("#/accounts");
    await settled("/accounts");
    const table = await screen.findByTestId("card-current-balances", {}, { timeout: 8000 });
    const link = within(table).getAllByRole("link")[0];
    expect(/^#\/accounts\/[^/?]+$/.test(link.getAttribute("href") ?? ""), "the first account name links its page").toBe(true);
    // the phone cards: a link beside the history toggle, never inside it (invalid HTML), each toggle named
    expect(table.querySelectorAll("button a").length === 0, "no link inside a button").toBe(true);
    const toggles = within(table).queryAllByRole("button", { name: /balance history for/ });
    expect(toggles.length > 0 && toggles.every((b) => b.hasAttribute("aria-expanded") && b.hasAttribute("aria-controls")),
      "every history toggle carries aria-expanded and aria-controls").toBe(true);
    fireEvent.click(link);
    await settled("/accounts/:id");
    await screen.findByTestId("card-ledger-drift", {}, { timeout: 8000 });
    const current = screen.getByRole("navigation", { name: /Accounts sections/ }).querySelector('a[aria-current="page"]');
    expect(current?.textContent === "Balances", "the Balances tab is current on an account page").toBe(true);
  }, ROUTE_TIMEOUT);

  // the account-values chart reads balances_timeseries.json only: Investments' own page gate
  // (holdings) must not take it down
  it("a missing holdings.json gates the Investments page but not its account-values chart", async () => {
    const base = stubMissing(["holdings.json"]);
    try {
      await mount("#/accounts/investments");
      await settled("/accounts/investments");
      const cards = await screen.findAllByTestId("missing-data", {}, { timeout: 8000 });
      expect(cards.some((c) => /the investment holdings could not be loaded/.test(c.textContent ?? "")),
        "says the investment holdings could not be loaded").toBe(true);
      expect(screen.queryByTestId("kpis-investments") === null, "the page body is gated").toBe(true);
      await screen.findByTestId("card-investment-balances", {}, { timeout: 8000 });
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // an empty balance history is a state of its own: the page explains it and the Illiquid KPI,
  // which reads the asset snapshot, still shows
  it("balances: an empty balance history explains itself and keeps the Illiquid KPI and the balance sheet", async () => {
    const base = stubServe("balances_timeseries.json", () => ({ series: {}, accounts: {} }));
    try {
      await mount("#/accounts");
      await settled("/accounts");
      const empty = await screen.findByTestId("card-current-balances", {}, { timeout: 8000 });
      expect(/No balance history yet/.test(empty.textContent ?? ""), "empty-state title").toBe(true);
      expect(screen.queryByTestId("kpi-illiquid") !== null, "Illiquid KPI").toBe(true);
      expect(screen.queryByTestId("card-liquidity") !== null, "Liquidity card").toBe(true);
      expect(screen.queryByTestId("card-balance-sheet-assets") !== null, "balance sheet").toBe(true);
      expect(/Cash on hand|Accounts tracked/.test(screen.getByTestId("kpis-balances").textContent ?? ""),
        "no cash / account-count KPI over an empty history").toBe(false);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // Liabilities with an empty history: nothing the history feeds (a green "$0 / 0 cards" would be a fabricated total),
  // an explanation instead, and the mortgage card (it reads assumptions) stays. With the cards in net worth the card
  // balances are the snapshot's card rows, so the card tile and the total answer from those, counting the cards they read.
  // The one card that says there is no card history stands in for the two card charts (its row is `when: "data"` and
  // names them in `insteadOf`, so the route matrix excuses them only while it is on screen).
  it("liabilities: an empty balance history explains itself instead of printing a $0 card total", async () => {
    const { cur } = debtInNetWorth();
    const cards = some(json("accounts.json").accounts.filter((a: { kind: string; id: string }) => a.kind === "credit-card"
      && cur.rows.some((r: { account_id: string | null }) => r.account_id === a.id)), "card rows in the newest snapshot");
    const owedOnCards = -cur.rows.filter((r: { account_id: string | null }) => cards.some((a) => a.id === r.account_id))
      .reduce((s: number, r: { value: number }) => s + r.value, 0);
    const base = stubServe("balances_timeseries.json", () => ({ series: {}, accounts: {} }));
    try {
      const row = whenRow("card-no-credit-cards");
      await mount(`#${row.route}`);
      await settled(row.route);
      const empty = await screen.findByTestId(row.id, {}, { timeout: 8000 });
      expect(/No credit-card balance history yet/.test(empty.textContent ?? ""), "the empty-state line").toBe(true);
      for (const id of row.insteadOf ?? []) {
        expect(screen.queryByTestId(id) === null, `${id} must not render over an empty history`).toBe(true);
      }
      expect(missingCards(document.body, row.route).length === 0, "the stand-in excuses the cards it replaces, and only those").toBe(true);
      // available credit reads the history: no figure, and the tile says so
      const available = screen.getByTestId("kpi-available-credit").textContent ?? "";
      expect(available === "Available credit—No credit-card balance history yet", "the available-credit tile says the history is empty").toBe(true);
      // the debt row answers for what it knows and names what it does not
      for (const id of ["kpis-liabilities", "kpi-total-debt", "kpi-mortgage-balance", "kpi-margin-debt", "kpi-card-debt"]) {
        expect(screen.queryByTestId(id) !== null, `${id} still renders`).toBe(true);
      }
      const tile = screen.getByTestId("kpi-card-debt").textContent ?? "";
      expect(tile.includes(fmtUSD0(owedOnCards)) && tile.includes(`${cards.length} credit card${cards.length === 1 ? "" : "s"}`),
        "the card tile is the snapshot's card rows, and counts those cards").toBe(true);
      expect(/credit-card balances not available/.test(screen.getByTestId("kpi-total-debt").textContent ?? ""), "the total knows the card balances").toBe(false);
      expect(screen.queryByTestId("card-mortgage-summary") !== null, "mortgage summary").toBe(true);
      ASSERTED_WHEN.add(row.id);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // On the household's own history, which holds its cards with their limits, the two card charts (ordinary rows, which
  // the per-route loop finds too) draw their charts, after the mortgage card, with no stand-in beside them.
  it("liabilities: the household's card history draws the card-balance and utilization charts", async () => {
    const rows = ["card-card-balances", "card-utilization"].map(rowFor);
    some(Object.values(json("balances_timeseries.json").accounts as Record<string, { kind: string }>)
      .filter((a) => a.kind === "credit-card"), "credit-card accounts in the balance history");
    await mount(`#${rows[0].route}`);
    await settled(rows[0].route);
    for (const row of rows) {
      const card = await screen.findByTestId(row.id, {}, { timeout: 8000 });
      expect(within(card).queryByTestId("chart") !== null, `${row.id} draws its chart`).toBe(true);
    }
    expect(screen.queryByTestId("card-no-credit-cards") === null, "no stand-in beside the charts").toBe(true);
    inOrder(["card-mortgage-summary", "card-card-balances", "card-utilization"]);
  }, ROUTE_TIMEOUT);

  // a stale bookmark or a typo, at the top level or under a hub, gets the page-moved card (a way Home,
  // a way into ⌘K) instead of a blank page; the shell around it keeps working
  it("an unknown path renders the page-moved card, and its search button opens ⌘K", async () => {
    for (const path of ["/no-such-page", "/cash-flow/no-such-tab", "/spending/no-such-part"]) {
      await mount(`#${path}`);
      await screen.findByTestId("page-moved", {}, { timeout: 8000 });
      expect(document.querySelector('main a[href="#/"]') !== null, `${path}: a link Home`).toBe(true);
      expect(document.querySelector('aside nav[aria-label="Main"]') !== null, `${path}: the labelled sidebar still renders`).toBe(true);
      cleanup();
    }
    await mount("#/no-such-page");
    await screen.findByTestId("page-moved", {}, { timeout: 8000 });
    fireEvent.click(screen.getByRole("button", { name: /Find a page/ }));
    await screen.findByRole("combobox", {}, { timeout: 8000 });
  }, ROUTE_TIMEOUT);

  // The mobile nav drawer closes on Escape from a window listener, and the focus trap (document listener)
  // preventDefaults the Escape it consumes: with ⌘K opened over the drawer, the first Escape closes the
  // palette only and the next one the drawer - never both on one key
  it("the nav drawer under ⌘K: one Escape closes the palette only, the next closes the drawer", async () => {
    await mount("#/");
    await settled("/");
    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    const aside = document.querySelector("aside")!;
    const drawerOpen = () => aside.className.split(/\s+/).includes("translate-x-0");
    expect(drawerOpen(), "the drawer opened").toBe(true);
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await screen.findByRole("combobox", {}, { timeout: 8000 });
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("combobox") === null, "the palette closed").toBe(true));
    expect(drawerOpen(), "...and the drawer under it is still open").toBe(true);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(drawerOpen(), "the next Escape closes the drawer").toBe(false);
  }, ROUTE_TIMEOUT);

  // At phone width the closed drawer is inert (off the canvas is not closed for a keyboard or a screen reader); the menu
  // button says it is expanded while the drawer is open, the open drawer takes focus, and Escape gives focus back
  it("the phone's nav drawer: inert while closed, focus in while open, back on the menu button once closed", async () => {
    media.phone = true;
    try {
      await mount("#/");
      await settled("/");
      const aside = document.querySelector("aside")!;
      const button = screen.getByRole("button", { name: "Open menu" });
      expect(aside.hasAttribute("inert") && button.getAttribute("aria-expanded") === "false", "closed: inert").toBe(true);
      button.focus();
      fireEvent.click(button);
      expect(!aside.hasAttribute("inert") && button.getAttribute("aria-expanded") === "true", "open: not inert").toBe(true);
      expect(aside.contains(document.activeElement), "focus moved into the drawer").toBe(true);
      fireEvent.keyDown(document, { key: "Escape" });
      expect(aside.hasAttribute("inert") && document.activeElement === button, "closed again, focus on the menu button").toBe(true);
    } finally {
      media.phone = false;
    }
  }, ROUTE_TIMEOUT);

  // /accounts/:id for an id nobody owns: the page says so and offers the way back
  it("a deep link to an unknown account renders the unknown-account card", async () => {
    await mount("#/accounts/not-a-real-id");
    await settled("/accounts/:id");
    expect((await screen.findByTestId("card-account-header", {}, { timeout: 8000 })).textContent).toMatch(/Unknown account/);
    expect(screen.queryByTestId("card-account-transactions") === null, "no register for an unknown id").toBe(true);
  }, ROUTE_TIMEOUT);

  // the donut's slice -> page map is made of router paths (not "#/" links), which App.test.tsx's
  // source scan cannot see: the mortgage slice must land on the live Liabilities tab
  it("home: the allocation donut's Mortgage slice opens Accounts -> Liabilities", async () => {
    oneMortgage();                                           // the newest snapshot holds the mortgage's row
    chart.clickName = "Mortgage";
    try {
      await mount("#/");
      await settled("/");
      const card = await screen.findByTestId("card-allocation-donut", {}, { timeout: 8000 });
      fireEvent.click(within(card).getByTestId("chart"));
      await settled("/accounts/liabilities");
      expect(window.location.hash).toBe("#/accounts/liabilities");
      await screen.findByTestId("card-mortgage-summary", {}, { timeout: 8000 });
    } finally {
      chart.clickName = "";
    }
  }, ROUTE_TIMEOUT);

  it("missing question / quality / daily-net-worth exports never print a fabricated 0", async () => {
    const base = stubMissing(["open_questions.json", "quality_report.json", "networth_daily.json"]);
    try {
      await mount("#/");
      await waitFor(() => expect(screen.getAllByTestId("missing-data").length).toBeGreaterThanOrEqual(2),
                    { timeout: 8000 });
      // (boolean asserts: a failure must not dump the rendered DOM into the log)
      const text = document.body.textContent ?? "";
      expect(/the daily net-worth line could not be loaded/.test(text), "the daily-line card says what is missing").toBe(true);
      expect(/the open questions could not be loaded/.test(text), "the quality card says what is missing").toBe(true);
      expect(/\d+ open questions?/.test(text), "no open-questions count").toBe(false);
      expect(/\d+ warnings?/.test(text), "no warnings count").toBe(false);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // Removing a RequireData gate keeps every other test green (the page renders its zero-data
  // state), so each Cash Flow tab is pinned to the gate by name
  it("a missing cashflow_monthly.json gates all three Cash Flow tabs and never shows a budget check", async () => {
    const base = stubMissing(["cashflow_monthly.json"]);
    try {
      for (const route of ["/cash-flow", "/cash-flow/income-statement", "/cash-flow/budget"]) {
        await mount(`#${route}`);
        const cards = await screen.findAllByTestId("missing-data", {}, { timeout: 8000 });
        expect(cards.some((c) => /the monthly cash flow could not be loaded/.test(c.textContent ?? "")),
          `${route} says the monthly cash flow could not be loaded`).toBe(true);
        await waitFor(() => absent(document.querySelector('[aria-busy="true"]'), "still loading"), { timeout: 8000 });
        absent(screen.queryByText(/failed to render/), `${route}: a section fell back to the boundary card`);
        // the gate replaces the page: none of its cards (nor the empty-state ones) may render
        for (const id of ["card-budget", "card-fixed-vs-discretionary", "card-payroll", "card-sankey", "card-pnl", "card-cashflow-main"]) {
          expect(screen.queryByTestId(id) === null, `${route} still renders ${id}`).toBe(true);
        }
        expect(/no full month of cash-flow data yet/.test(document.body.textContent ?? ""), `${route}: empty-state title`).toBe(false);
        cleanup();
      }
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // the burn KPI and the income chart carry the month their trailing window ends on (Life's footer used to). The income
  // chart's view-data table ends on each stream's 3-full-month average: the figures the separate streams list showed.
  it("cash flow overview: the burn KPI and the income chart name the full month they run through; the chart's table ends on the streams' averages", async () => {
    chart.pairs.length = 0;
    await mount("#/cash-flow");
    await settled("/cash-flow");
    const burn = await screen.findByTestId("kpi-monthly-burn", {}, { timeout: 8000 });
    const streams = await screen.findByTestId("card-income-by-source", {}, { timeout: 8000 });
    expect(/through [A-Z][a-z]{2} \d{4}/.test(burn.textContent ?? ""), "burn scope stamp").toBe(true);
    expect(/through [A-Z][a-z]{2} \d{4}/.test(streams.textContent ?? ""), "streams scope stamp").toBe(true);
    absent(screen.queryByTestId("card-income-streams"), "the separate streams list is gone");
    const inc = chartWith((o) => o.series?.length > 0 && o.series.every((s: any) => s.stack === "income"));
    expect(inc.table.columns.slice(1).join() === inc.option.series.map((s: any) => s.name).join(), "a column per stream band").toBe(true);
    for (const s of inc.option.series) {
      const col = inc.table.columns.indexOf(s.name);
      expect(s.data.every((v: number, i: number) => inc.table.rows[i][col] === v), `${s.name}: the month rows are the band`).toBe(true);
    }
    const avgRow = inc.table.rows[inc.table.rows.length - 1];
    expect(inc.table.rows.length === inc.option.series[0].data.length + 1, "one row per month, then the average").toBe(true);
    expect(/^3-month average, \d{4}-\d{2} to \d{4}-\d{2}$/.test(String(avgRow[0])), "the last row is the 3-month average, named by its months").toBe(true);
    expect(inc.table.columns[0] === "Period", "the first column names months and the average alike").toBe(true);
    // every stream with income in the window carries its average, as the streams list computed it (the default window
    // holds the 3 newest full months, so that is every stream the list showed)
    const expected = incomeStreams(json("cashflow_monthly.json").months, exportsInProgress(), 3, Number.POSITIVE_INFINITY);
    expect(expected.length > 0 && expected.every((x) => avgRow[inc.table.columns.indexOf(x.name)] === x.monthly),
      "every stream's average, as the streams list computed it").toBe(true);
    expect(inc.option.series.every((s: any) => s.data.some((v: number) => v !== 0)), "no band is empty in the window").toBe(true);
    // payroll and budgets stay discoverable from the Overview
    expect(document.querySelector('a[href="#/cash-flow/budget"]') !== null, "a link to the Budget tab").toBe(true);
  }, ROUTE_TIMEOUT);

  // Home's "This month so far" against the raw export, computed here independently: the category view's purchases and
  // fees (refunds netted) from the 1st of the data's month through its last COMPLETE day (the ingest day is still coming
  // in), the median of the six months before cut at the same day, the top three categories, and a link to those days.
  it("home: this month so far is the month's spend, typical pace and top categories through the last complete day, and links to those days", async () => {
    const rows = some(categoryView(json("transactions.json").transactions), "transactions");
    const generated: string = json("manifest.json").generated_at;
    const end = dataThrough(rows.reduce((m: string, t: any) => (t.date > m ? t.date : m), ""), generated);
    const dayBefore = (iso: string) => { const d = new Date(`${iso}T00:00:00Z`); d.setUTCDate(d.getUTCDate() - 1); return d.toISOString().slice(0, 10); };
    const through = end === generated.slice(0, 10) ? dayBefore(end) : end;
    // an ingest on the 1st has no complete day of the month yet (the card's empty state, pinned in its unit tests); the
    // household's ingest day is later in its month, so the card has a month so far
    branch(through.slice(0, 7) === end.slice(0, 7), "the last complete day is in the data's last month");
    await mount("#/");
    await settled("/");
    const card = await screen.findByTestId("card-month-to-date", {}, { timeout: 8000 });
    expect(within(card).queryByTestId("mtd-empty") === null, "no empty state with a month so far").toBe(true);
    const month = through.slice(0, 7), day = Number(through.slice(8, 10)), first = `${month}-01`;
    const spendCents = (from: string, to: string) => rows
      .filter((t: any) => (t.type === "purchase" || t.type === "fee") && t.date >= from && t.date <= to)
      .reduce((s: number, t: any) => s - Math.round(t.amount * 100), 0);
    expect(within(card).getByTestId("mtd-spend").textContent === fmtUSD0(spendCents(first, through) / 100), "the month-to-date spend").toBe(true);
    expect((card.textContent ?? "").includes(`through ${fmtDate(through)}`), "the day it runs through").toBe(true);
    // the typical pace: each of the six months before, inside the coverage floor, covered whole and with data, cut at the same day
    const dataStart = rows.reduce((m: string, t: any) => (t.date < m ? t.date : m), "9999-12-31");
    const alive = new Set(rows.map((t: any) => t.date.slice(0, 7)));
    const priors: number[] = [];
    const floor = coverageFloor(json("cashflow_monthly.json").months);
    let [y, mo] = month.split("-").map(Number);
    for (let i = 0; i < 6; i++) {
      mo -= 1; if (mo === 0) { mo = 12; y -= 1; }
      const m = `${y}-${String(mo).padStart(2, "0")}`;
      if (floor === null || m < floor || `${m}-01` < dataStart || !alive.has(m)) continue;
      const last = Math.min(day, new Date(Date.UTC(y, mo, 0)).getUTCDate());
      priors.push(spendCents(`${m}-01`, `${m}-${String(last).padStart(2, "0")}`) / 100);
    }
    const sorted = [...priors].sort((a, b) => a - b), mid = Math.floor(sorted.length / 2);
    const typical = sorted.length === 0 ? null : sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
    branch(typical !== null && priors.length === 6, "six prior months inside the coverage floor give a typical pace");
    const typicalLine = [...card.querySelectorAll("p")].find((p) => /^typical by the /.test(p.textContent ?? ""))?.textContent ?? null;
    expect(typical === null ? typicalLine === null : typicalLine !== null && typicalLine.includes(`: ${fmtUSD0(typical)} — the median of ${priors.length}`),
      "the typical figure is the median of the prior months by the same day").toBe(true);
    // the top three categories so far, by net spend above zero
    const byCat = new Map<string, number>();
    for (const t of rows) {
      if (!(t.type === "purchase" || t.type === "fee") || t.date < first || t.date > through) continue;
      const c = t.category ?? "Uncategorized";
      byCat.set(c, (byCat.get(c) ?? 0) - Math.round(t.amount * 100));
    }
    const top = some([...byCat].filter(([, c]) => c > 0), "categories with spending this month so far")
      .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).slice(0, 3)
      .map(([c, v]) => `${c}${fmtUSD0(v / 100)}`);
    const shown = [...(card.querySelector('[data-testid="mtd-top"]')?.querySelectorAll("li") ?? [])].map((li) => li.textContent);
    expect(shown.length === top.length && shown.every((x, i) => x === top[i]), "the top three categories").toBe(true);
    const link = [...card.querySelectorAll("a")].find((a) => /This month’s transactions/.test(a.textContent ?? ""));
    expect(link?.getAttribute("href") === `#/spending/transactions?from=${first}&to=${through}`, "the transactions link").toBe(true);
    expect(within(card).getByTestId("mtd-pace").textContent !== "", "a pace sentence").toBe(true);
    // the card sits after the snapshot strip and before the waterfall (the owner's order for the rest of Home is kept)
    const order = ["card-snapshot-deltas", "card-month-to-date", "card-decomposition", "card-networth-history", "card-next-30-days"]
      .map((id) => screen.getByTestId(id));
    expect(order.every((el, i) => i === 0 || (order[i - 1].compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0),
      "Home's cards keep their order, with the new card after the strip").toBe(true);
  }, ROUTE_TIMEOUT);

  // Home's TTM KPI links to Cash Flow, so the two must be the same number: both read the trailing 12
  // FULL months before the month in progress (lib/agg.ts fullMonths). (Boolean/equality asserts on formatted strings:
  // no DOM dump.)
  it("home's TTM savings rate equals Cash Flow's 12-month cash-basis rate", async () => {
    const expected = savingsRate(recentMonths(some(json("cashflow_monthly.json").months, "cash-flow months"), exportsInProgress(), 12));
    expect(expected !== null, "twelve full months give a rate").toBe(true);
    await mount("#/");
    await settled("/");
    const home = await screen.findByTestId("kpi-savings-rate-ttm", {}, { timeout: 8000 });
    const homeValue = home.querySelector("div.tabular-nums")?.textContent ?? "";
    expect(homeValue === fmtPct(expected, 0), "Home shows the full-month TTM rate").toBe(true);
    cleanup();
    await mount("#/cash-flow");
    await settled("/cash-flow");
    const cf = await screen.findByTestId("kpi-savings-rate-12m", {}, { timeout: 8000 });
    const cfValue = cf.querySelector("div.tabular-nums")?.textContent ?? "";
    // the cash-basis rate is the value, or (with payroll facts) the sub-line's "cash basis …"
    const cashBasis = (cf.textContent ?? "").includes("cash basis " + fmtPct(expected))
      || cfValue === fmtPct(expected);
    expect(cashBasis, "Cash Flow's 12-month cash-basis rate is Home's").toBe(true);
    expect(fmtPct(expected, 0) === homeValue, "the same number, to Home's precision").toBe(true);
  }, ROUTE_TIMEOUT);

  // An account name is a link to its page wherever it is printed, and it looks like one at rest (colour + underline).
  it("account names link their page: Data's freshness list, Investments' holdings and Second House's funding plan", async () => {
    const ids = new Set<string>(some(json("accounts.json").accounts, "accounts").map((a: { id: string }) => a.id));
    some(json("holdings.json").holdings, "holdings");
    const linked = (root: Element) => [...root.querySelectorAll('a[href^="#/accounts/"]')];
    const idOf = (a: Element) => decodeURIComponent((a.getAttribute("href") ?? "").slice("#/accounts/".length));
    const styled = (a: Element) => { const c = [a, ...a.querySelectorAll("*")].map((e) => e.className).join(" "); return /\btext-blue-\d+\b/.test(c) && /\bunderline\b/.test(c); };
    await mount("#/data");
    await settled("/data");
    const fresh = linked(await screen.findByTestId("card-account-freshness", {}, { timeout: 8000 }));
    expect(fresh.length, "one link per account in the freshness list").toBe(ids.size);
    expect(fresh.every((a) => ids.has(idOf(a))), "each link names a real account").toBe(true);
    expect(fresh.every(styled), "each looks like a link at rest").toBe(true);
    cleanup();
    await mount("#/accounts/investments");
    await settled("/accounts/investments");
    const held = linked(await screen.findByTestId("card-holdings", {}, { timeout: 8000 }));
    expect(held.length > 0, "the holdings name their accounts as links").toBe(true);
    expect(held.every((a) => ids.has(idOf(a)) && styled(a)), "real accounts, styled as links").toBe(true);
    cleanup();
    const base = stubServe("api/scenarios", () => SYNTHETIC_SCENARIOS);
    try {
      await mount("#/planning/second-house");
      await settled("/planning/second-house");
      const plan = await screen.findByTestId("card-funding-plan", {}, { timeout: 8000 });
      const rows = plan.querySelectorAll("tbody tr").length;
      expect(rows > 0 && linked(plan).length === rows, "every funding-plan row names its account as a link").toBe(true);
      expect(linked(plan).every((a) => ids.has(idOf(a)) && styled(a))).toBe(true);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // Check ids are codes ("reconcile.<id>.ledger_drift"): the quality report and the account page show words, the code in a tooltip
  it("data: the quality report names every check in words, with its code in the tooltip", async () => {
    const names: string[] = some(json("quality_report.json").checks, "quality checks").map((c: { name: string }) => c.name);
    await mount("#/data");
    await settled("/data");
    const card = await screen.findByTestId("card-quality-report", {}, { timeout: 8000 });
    const shown = [...card.querySelectorAll("li > span[title]")];
    expect(shown.length, "one labelled row per check in the full list").toBe(names.length);
    expect(shown.every((e) => names.includes(e.getAttribute("title") ?? "")), "each tooltip is a real check id").toBe(true);
    const coded = shown.filter((e) => /[._]/.test(e.getAttribute("title") ?? "") && e.textContent === e.getAttribute("title"));
    expect(coded.length, "no row prints its code as its text").toBe(0);
    expect(shown.every((e) => !/[a-z]_[a-z]/.test(e.textContent ?? "")), "and no underscore code leaks into a label").toBe(true);
  }, ROUTE_TIMEOUT);

  // One piece of advice, or none: with nothing saved the target mix is a SUGGESTION, shown with its basis and compared side by
  // side with no verdict, no drift, no dollars to move and no colour of advice; dragging previews without saving. With no
  // asset-class target stated, their card says so and where to set them, drifting against nothing (the next covers the day
  // some are). The household's config states asset-class targets, so assumptions.json is served without them.
  it("investments: the target mix is a suggestion until saved, and no asset-class target means none to drift against", async () => {
    const assumptions = json("assumptions.json");
    expect(Object.keys(assumptions.targets ?? {}).length > 0, "the household seeds asset-class targets (served here without them)").toBe(true);
    expect(assumptions.household.people.some((p: { role: string; birth_year?: number }) => p.role === "owner" && p.birth_year),
      "an adult with a birth year: the suggestion's age").toBe(true);
    localStorage.removeItem(PREFS_LOCAL_KEY);
    const base = stubServe("assumptions.json", () => ({ ...json("assumptions.json"), targets: {} }));
    try {
      await mount("#/accounts/investments");
      await settled("/accounts/investments");
      const card = await screen.findByTestId("card-target-mix", {}, { timeout: 8000 });
      const text = () => card.textContent ?? "";
      expect(card.querySelector("h2")?.textContent, "one card, one name").toBe("Target mix");
      expect(/Suggestion — not saved/.test(text()) && /Nothing is saved yet/.test(text()), "it says the suggestion is not saved").toBe(true);
      const basis = within(card).getByTestId("target-mix-basis").textContent ?? "";
      expect(/^A suggestion, not the household's target: a common rule of thumb for age \d+ .*\(Stocks \d+% · Bonds \d+% · Cash \d+% · Alternatives \d+%\)/
        .test(basis), "the suggestion, its basis, in the tab's words").toBe(true);
      expect(within(card).queryByTestId("target-mix-summary") === null && !/to move|pts|rebalance/i.test(text()),
        "no verdict, drift or dollars to move").toBe(true);
      // (the store's own amber notice, when the family store is unreachable, is a status, not advice: the figures are checked)
      expect([...card.querySelectorAll("table, table *, [data-testid='target-mix-basis']")]
        .some((e) => /text-(?:emerald|amber|red|orange)-\d00/.test(e.getAttribute("class") ?? "")), "no colour of advice").toBe(false);
      expect(/what-if|\balts\b/i.test(text()), "no second vocabulary").toBe(false);
      expect(["stocks", "bonds", "cash", "alts"].every((k) => within(card).queryByTestId(`target-mix-row-${k}`) !== null), "a row per bucket").toBe(true);
      expect(within(card).getAllByRole("slider").length, "stocks, cash and alternatives have sliders; bonds take the rest").toBe(3);
      const drift = screen.getByTestId("card-class-drift");
      expect(drift.querySelector("table") === null && /^No asset-class targets are stated/.test(within(drift).getByTestId("empty-state").textContent ?? ""),
        "no asset-class target stated: their card says so and drifts against nothing").toBe(true);

      const stocks = within(card).getByRole("slider", { name: "Stocks target" }) as HTMLInputElement;
      const bonds = within(card).getByTestId("target-mix-bonds");
      const was = { stocks: Number(stocks.value), bonds: Number(bonds.textContent) };
      const step = was.stocks >= 10 ? -10 : 10;
      fireEvent.change(stocks, { target: { value: String(was.stocks + step) } });
      expect(Number(stocks.value), "the slider moved").toBe(was.stocks + step);
      expect(Number(bonds.textContent), "bonds take the rest").toBe(was.bonds - step);
      expect(/Unsaved changes/.test(text()), "unsaved changes are marked").toBe(true);
      expect(localStorage.getItem(PREFS_LOCAL_KEY) === null || !/target_mix":\{/.test(localStorage.getItem(PREFS_LOCAL_KEY) ?? ""),
        "a drag writes nothing").toBe(true);
      fireEvent.click(within(card).getByRole("button", { name: "Revert" }));
      expect(Number(stocks.value), "Revert goes back to the starting mix").toBe(was.stocks);
      expect(/Unsaved changes/.test(text())).toBe(false);
    } finally {
      vi.stubGlobal("fetch", base);
      localStorage.removeItem(PREFS_LOCAL_KEY);
    }
  }, ROUTE_TIMEOUT);

  // The household's own asset-class targets (% of net worth) drive the one card that advises, under the target mix; targets
  // saved in the app replace the configuration's and the card says which it uses. Served as a synthetic prefs doc.
  it("investments: asset-class targets saved in the app drive their card under the target mix, which says where they come from", async () => {
    localStorage.removeItem(PREFS_LOCAL_KEY);
    const base = stubServe("api/prefs", () => ({
      version: 1, updated_at: "2026-01-02T00:00:00Z", budgets: {}, targets: { "Stocks/ETF": 40, Retirement: 30 },
      projections: {}, payroll: { phases: [] }, saved_filters: [], reports: [],
    }));
    try {
      await mount("#/accounts/investments");
      await settled("/accounts/investments");
      await screen.findByTestId("card-class-drift", {}, { timeout: 8000 });
      const legacy = () => screen.getByTestId("card-class-drift");
      await waitFor(() => expect(legacy().querySelectorAll("tbody tr").length, "one row per saved target").toBe(2), { timeout: 8000 });
      expect(legacy().querySelector("h2")?.textContent, "its heading names the base").toBe("Asset-class targets (% of net worth)");
      // what the targets leave, from the newest snapshot: the classes held with no target and the debts, as shares of net
      // worth today
      const snaps = json("assets_timeseries.json").snapshots;
      const newest = snaps[snaps.length - 1];
      const byClass: Record<string, number> = newest.by_class;
      const untargeted = Object.entries(byClass).filter(([k, v]) => v > 0 && !["Stocks/ETF", "Retirement"].includes(k));
      const debts = Object.values(byClass).filter((v) => v < 0).reduce((s, v) => s + v, 0);
      const today = [...(untargeted.length ? [`the classes with no target (${untargeted.map(([k]) => k).join(", ")}) are `
        + `${fmtPct(untargeted.reduce((s, [, v]) => s + v, 0) / newest.total)} of it`] : []),
        ...(debts < 0 ? [`the debts ${fmtPct(debts / newest.total)}`] : [])];
      expect(today.length > 0, "the household holds more than the two targeted classes").toBe(true);
      expect(within(legacy()).getByTestId("class-drift-source").textContent,
        "the saved targets, what they add up to and what they leave, with no push toward 100")
        .toBe(`The household's own targets, saved in the app for the whole family. They add up to 70% of net worth, leaving 30% `
          + `for what has no target: today ${today.join(" and ")}.`);
      inOrder(["card-target-mix", "card-class-drift", "card-holdings"]);
    } finally {
      vi.stubGlobal("fetch", base);
      localStorage.removeItem(PREFS_LOCAL_KEY);
    }
  }, ROUTE_TIMEOUT);

  // Page order, asserted on document position so a card cannot drift back: the answer a page exists for comes before the detail.
  const inOrder = (ids: string[]) => {
    const els = ids.map((id) => screen.queryByTestId(id));
    const missing = ids.filter((_, i) => els[i] === null);
    expect(missing.length === 0, `present: ${missing.join(", ")}`).toBe(true);
    for (let i = 0; i + 1 < els.length; i++) {
      expect((els[i]!.compareDocumentPosition(els[i + 1]!) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0, `${ids[i]} comes before ${ids[i + 1]}`).toBe(true);
    }
  };

  it("investments: KPIs, concentration, the target mix, holdings, THEN balances over time", async () => {
    some(json("holdings.json").holdings, "holdings");
    await mount("#/accounts/investments");
    await settled("/accounts/investments");
    await screen.findByTestId("card-investment-balances", {}, { timeout: 8000 });
    inOrder(["kpis-investments", "card-concentration", "card-target-mix", "card-holdings", "card-investment-balances"]);
    // the footnote that reconciles this page with Balances
    expect(/valued here from its positions/.test(screen.getByTestId("card-holdings").textContent ?? ""), "holdings-priced vs feed balance, in a footnote").toBe(true);
    // phone: the as-of chip is its own line under the account name (a full-basis flex item that wraps), inline from md up
    const chips = screen.getAllByTestId("holdings-asof");
    expect(chips.length > 0 && chips.every((c) => /\border-last\b/.test(c.className) && /\bbasis-full\b/.test(c.className) && /\bmd:order-none\b/.test(c.className)),
      "every holdings chip drops under the name on a phone").toBe(true);
    expect(chips.every((c) => c.closest("summary") !== null && c.previousElementSibling?.className.includes("tabular-nums")), "after the value").toBe(true);
  }, ROUTE_TIMEOUT);

  it("second house: the guardrails sit right under the four KPI cards, before the funding plan and the charts", async () => {
    const base = stubServe("api/scenarios", () => SYNTHETIC_SCENARIOS);
    try {
      await mount("#/planning/second-house");
      await settled("/planning/second-house");
      await screen.findByTestId("kpis-guardrails", {}, { timeout: 8000 });
      inOrder(["card-deal-inputs", "kpis-second-house", "kpis-guardrails", "card-funding-plan", "card-rental-model", "card-rate-sensitivity"]);
      // a phone's 8-digit amounts fit the currency boxes (112px at 16px type); from sm up they stay the compact 80px
      const boxes = [...screen.getByTestId("card-deal-inputs").querySelectorAll<HTMLInputElement>("input[inputmode=decimal]")];
      expect(boxes.length > 0 && boxes.every((b) => /\bw-(28|32)\b/.test(b.className)), "no currency box narrower than w-28 on a phone").toBe(true);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  it("home: the snapshot-delta chips sit under a heading, the newest six first, the rest behind one control, and wrap", async () => {
    await mount("#/");
    await settled("/");
    const strip = await screen.findByTestId("card-snapshot-deltas", {}, { timeout: 8000 });
    expect(within(strip).queryByRole("heading", { level: 2 }) !== null, "the strip has a heading").toBe(true);
    const list = strip.querySelector("ul");
    expect(list !== null && /\bflex-wrap\b/.test(list.className) && !/overflow-x-auto/.test(list.className), "the chips wrap").toBe(true);
    const others = snapshotSplit(json("assets_timeseries.json")).comparable.length - 1;
    const datesShown = () => [...strip.querySelectorAll("li")].map((c) => new Date(/vs ([^:†]+)/.exec(c.textContent ?? "")?.[1] ?? "").getTime());
    expect(others > 6 && datesShown().length === 6, "the newest six by default").toBe(true);
    fireEvent.click(within(strip).getByRole("button", { name: `Show all ${others}` }));
    const dates = datesShown();
    expect(dates.length === others && dates.every((d, i) => i === 0 || d < dates[i - 1]), "all of them, the newest first").toBe(true);
  }, ROUTE_TIMEOUT);

  // one label per metric: the 12-month savings rate reads "Savings rate (12 mo)" on Home and Cash Flow, and "Monthly burn"
  // says what it is an average of
  it("one name for the savings rate on Home and Cash Flow, and 'Monthly burn' says it is an average monthly spend", async () => {
    await mount("#/");
    await settled("/");
    expect(((await screen.findByTestId("kpi-savings-rate-ttm", {}, { timeout: 8000 })).textContent ?? "").includes("Savings rate (12 mo)"), "Home's label").toBe(true);
    expect(/TTM/.test(document.body.textContent ?? ""), "no TTM jargon on Home").toBe(false);
    cleanup();
    await mount("#/cash-flow");
    await settled("/cash-flow");
    const kpis = (await screen.findByTestId("kpis-cashflow", {}, { timeout: 8000 })).textContent ?? "";
    expect(kpis.includes("Savings rate (3 mo)"), "the 3-month rate").toBe(true);
    expect(kpis.includes("12 mo"), "the 12-month rate says 12 mo").toBe(true);
    expect(/TTM|trailing 12 mo|trailing 3 mo/.test(kpis.replace(/trailing 6 full months/, "")), "no second name for it").toBe(false);
    expect((screen.getByTestId("kpi-monthly-burn").textContent ?? "").includes("avg monthly spend"), "burn names what it averages").toBe(true);
  }, ROUTE_TIMEOUT);

  // and the window pickers say 12M / YTD / All the same way wherever a window is chosen: the shared preset words
  it("period toggles use the shared preset words: Home's decomposition and the income statement read 12M / YTD / All", async () => {
    const labels = (root: Element) => [...root.querySelectorAll("button")].map((b) => b.textContent);
    await mount("#/");
    await settled("/");
    const deco = await screen.findByTestId("card-decomposition", {}, { timeout: 8000 });
    expect(labels(deco).filter((l) => ["12M", "YTD", "All", "1y", "all"].includes(l ?? ""))).toEqual(["12M", "YTD", "All"]);
    cleanup();
    await mount("#/cash-flow/income-statement");
    await settled("/cash-flow/income-statement");
    const pnl = await screen.findByTestId("card-sankey", {}, { timeout: 8000 });
    const toggle = pnl.parentElement!.querySelector(".inline-flex.rounded-lg")!;
    expect(labels(toggle)).toEqual(["3M", "6M", "12M", "YTD"]);
  }, ROUTE_TIMEOUT);

  // The shell: the phone top bar carries the review count (the sidebar badge's number) and a privacy eye that works
  // in one tap; the browser tab is titled with the place, and an account's page with the account's name.
  it("the shell: the top bar shows the review count and toggles privacy; document.title names the place and the account", async () => {
    await mount("#/spending/transactions");
    await settled("/spending/transactions");
    await waitFor(() => expect(document.title).toBe("Spending · Transactions · Family Financials"));
    const chip = document.querySelector('[data-testid="topbar-review-count"]');
    const badge = document.querySelector('aside [role="img"][aria-label$="to review"]');
    expect(chip !== null && badge !== null, "the top bar and the sidebar both show a review count").toBe(true);
    expect(chip!.getAttribute("aria-label") === badge!.getAttribute("aria-label"), "the same number").toBe(true);
    const eye = screen.getByTestId("privacy-toggle-bar");
    try {
      fireEvent.click(eye);
      expect(isPrivate(), "one tap hides amounts").toBe(true);
      expect(screen.getByTestId("privacy-toggle-bar").getAttribute("aria-pressed")).toBe("true");
      expect(document.activeElement === screen.getByTestId("privacy-toggle-bar") || document.activeElement === document.body,
        "the toggle is not rebuilt away from under the finger (it sits outside the keyed <main>)").toBe(true);
      fireEvent.click(screen.getByTestId("privacy-toggle-bar"));
      expect(isPrivate(), "and a second tap shows them").toBe(false);
    } finally {
      setPrivate(false);
    }
    cleanup();
    const acct = plantedAccount("seam.account");
    await mount(`#/accounts/${acct.id}`);
    await settled("/accounts/:id");
    await waitFor(() => expect(document.title).toBe(`${acct.name} · Family Financials`));
    expect(((document.querySelector("header") as HTMLElement).textContent ?? "").includes(acct.name), "the phone top bar names the account").toBe(true);
    cleanup();
    await mount("#/");
    await settled("/");
    await waitFor(() => expect(document.title).toBe("Family Financials"));
  }, ROUTE_TIMEOUT);

  // The sidebar header (on a phone, the drawer the menu button opens) names the product and, under it, the household
  // by the name its exports carry. A name is not an amount: privacy mode leaves it readable. The phone top bar keeps
  // the page's own title, and the browser tab never carries the household's name.
  it("the shell: the sidebar header shows the household's name under the product's, in the phone drawer and in privacy mode too", async () => {
    const name: string = json("assumptions.json").household.name;
    expect(typeof name === "string" && name.trim() === name && name.length > 0, "the demo household's exports name it").toBe(true);
    await mount("#/");
    await settled("/");
    const aside = document.querySelector("aside") as HTMLElement;
    const shown = within(aside).getByTestId("household-name");
    expect(shown.textContent === name && shown.getAttribute("title") === name, "the export's name, its full text in title").toBe(true);
    expect(shown.previousElementSibling?.textContent === "Family Financials", "under the product's name").toBe(true);
    expect(shown.classList.contains("truncate"), "a long name ends in an ellipsis").toBe(true);
    const bar = document.querySelector("header") as HTMLElement;
    expect((bar.textContent ?? "").includes("Home") && !(bar.textContent ?? "").includes(name),
      "the phone top bar shows the page's title and not the household's name").toBe(true);
    expect(document.title, "the browser tab is the product's").toBe("Family Financials");
    // on a phone the sidebar is the drawer behind the top bar's menu button
    expect(aside.classList.contains("translate-x-0"), "the drawer starts closed").toBe(false);
    fireEvent.click(within(bar).getByRole("button", { name: "Open menu" }));
    expect(aside.classList.contains("translate-x-0"), "the menu button opens the drawer").toBe(true);
    expect(within(aside).getByTestId("household-name").textContent === name, "the open drawer's header names the household").toBe(true);
    try {
      fireEvent.click(screen.getByTestId("privacy-toggle-nav"));
      expect(isPrivate(), "privacy mode is on").toBe(true);
      const masked = within(document.querySelector("aside") as HTMLElement).getByTestId("household-name");
      expect(masked.textContent === name && masked.getAttribute("title") === name, "privacy mode leaves the name readable").toBe(true);
    } finally {
      setPrivate(false);
    }
  }, ROUTE_TIMEOUT);

  it("the shell: a long household name keeps its full text in title; an export that states no name shows none", async () => {
    const long = "A household whose name runs far past the width of the sidebar header";
    const first = stubServe("assumptions.json", () => {
      const doc = json("assumptions.json");
      doc.household.name = long;
      return doc;
    });
    try {
      await mount("#/");
      await settled("/");
      const el = within(document.querySelector("aside") as HTMLElement).getByTestId("household-name");
      expect(el.textContent === long && el.getAttribute("title") === long && el.classList.contains("truncate"),
        "the long name in full, cut by an ellipsis, its full text in title").toBe(true);
      cleanup();
      vi.stubGlobal("fetch", first);
      // an empty name is none stated
      stubServe("assumptions.json", () => {
        const doc = json("assumptions.json");
        doc.household.name = "";
        return doc;
      });
      await mount("#/");
      await settled("/");
      absent(document.querySelector('[data-testid="household-name"]'), "no name, and no placeholder in its place");
      expect((document.querySelector("aside")?.textContent ?? "").includes("Family Financials"), "the product's name stays").toBe(true);
    } finally {
      vi.stubGlobal("fetch", first);
    }
  }, ROUTE_TIMEOUT);

  it("a missing vendors.json says so on the Data page instead of printing 'none yet'", async () => {
    const base = stubMissing(["vendors.json"]);
    try {
      await mount("#/data");
      const card = await screen.findByTestId("card-learned-rules", {}, { timeout: 8000 });
      const gone = await within(card).findByTestId("missing-data");
      expect(gone.textContent).toMatch(/The learned-rules list is unavailable/);
      expect(gone.textContent).toMatch(/the vendor list could not be loaded/);
      expect(within(card).queryByTestId("learned-rules") === null, "no learned-rules list").toBe(true);
      expect(/None yet|categorized from your confirmed answers/.test(card.textContent ?? ""), "no fabricated learned-rules text").toBe(false);
    } finally {
      vi.stubGlobal("fetch", base);
    }
  }, ROUTE_TIMEOUT);

  // Privacy mode (4.5). The four money formatters mask, so every KPI, table, axis, tooltip and label goes dark together;
  // these two `it`s are the net under that: named money surfaces first, then every route, text and attributes and
  // every chart option. They set the module flag with setPrivate (privacy.ts caches the flag - read from storage once, at import -
  // so seeding localStorage afterwards would change nothing).
  it("privacy mode: no dollar figure survives on Home, the account page or the budget grid; the register says amounts are hidden and CSV says it is unmasked; the hotkey brings them back", async () => {
    setPrivate(true);
    // a dollar figure ("$1,234") in text; boolean asserts only, so a failure never prints the household's numbers
    const money = /\$\d/;
    try {
      await mount("#/");
      await settled("/");
      expect(money.test((await screen.findByTestId("kpis-home", {}, { timeout: 8000 })).textContent ?? ""), "Home KPIs masked").toBe(false);
      expect(money.test((await screen.findByTestId("card-next-30-days", {}, { timeout: 8000 })).textContent ?? ""), "next-30-days total masked").toBe(false);
      expect((screen.getByTestId("kpis-home").textContent ?? "").includes(MASK), "the KPIs show the mask, not blanks").toBe(true);
      cleanup();
      await mount(`#${realize("/accounts/:id")}`);
      await settled("/accounts/:id");
      expect(money.test((await screen.findByTestId("card-ledger-drift", {}, { timeout: 8000 })).textContent ?? ""), "drift card masked").toBe(false);
      // jsdom's virtualizer draws no register rows here, so the rows' own mask is pinned in TxnTable.test; the card says so
      expect(/amounts hidden/.test((await screen.findByTestId("card-account-transactions", {}, { timeout: 8000 })).textContent ?? ""), "the register says amounts are hidden").toBe(true);
      cleanup();
      await mount("#/cash-flow/budget");
      await settled("/cash-flow/budget");
      expect(money.test((await screen.findByTestId("card-budget", {}, { timeout: 8000 })).textContent ?? ""), "budget grid masked").toBe(false);
      expect(money.test((await screen.findByTestId("card-fixed-vs-discretionary", {}, { timeout: 8000 })).textContent ?? ""), "fixed-vs-discretionary masked").toBe(false);
      cleanup();
      await mount("#/spending/transactions");
      await settled("/spending/transactions");
      expect(screen.getByRole("button", { name: /Export CSV/ }).getAttribute("title")).toMatch(/never masked/);
      expect(screen.getByText(/real amounts, never masked/) !== null, "the CSV notice is visible text").toBe(true);
      expect(/amounts hidden/.test((await screen.findByTestId("card-transactions", {}, { timeout: 8000 })).textContent ?? ""), "the register toolbar says amounts are hidden").toBe(true);
      expect(screen.getByTestId("privacy-toggle-nav").getAttribute("aria-pressed")).toBe("true");     // the sidebar's; the phone top bar has its own (privacy-toggle-bar)
      cleanup();
      // the hotkey: Alt+Shift+P unmasks, and the keyed <main> rebuilds every memoized chart option
      await mount("#/");
      await settled("/");
      await screen.findByTestId("kpis-home", {}, { timeout: 8000 });
      const before = new Set(chart.options);
      const drawn = chart.options.length;
      expect(before.size > 0, "Home drew charts").toBe(true);
      act(() => { window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyP", altKey: true, shiftKey: true, bubbles: true, cancelable: true })); });
      expect(isPrivate(), "the hotkey turned privacy mode off").toBe(false);
      await waitFor(() => expect(money.test(screen.getByTestId("kpis-home").textContent ?? ""), "KPIs unmasked again").toBe(true));
      // every option handed to a chart after the toggle is a NEW object: none is a stale memo from before it
      const after = chart.options.slice(drawn);
      expect(after.length > 0 && after.every((o) => !before.has(o)), "every chart option was rebuilt on the toggle").toBe(true);
      expect(screen.getByTestId("privacy-toggle-nav").getAttribute("aria-pressed")).toBe("false");
      expect(screen.getByTestId("privacy-toggle-bar").getAttribute("aria-pressed")).toBe("false");
    } finally {
      setPrivate(false);
    }
  }, ROUTE_TIMEOUT);

  // The net under the formatters: every route's text and attributes, and every chart option, while private. It is
  // written independently of lib/privacy.ts (its own patterns), and it proves it is not vacuous: the same scans, run
  // with privacy OFF on Home and Data, must find money (a net that cannot see amounts passes everything).
  describe("the privacy net over every route", () => {
    // the scanners (AMOUNTS, STATIC_OK, textHits, optionHits, raw) live in test/moneyScan.ts, shared with the matrix
    // The recorded scans that ran to the end, and those that threw: "every STATIC_OK alternative matches" reads what
    // they recorded, so it first checks that every scan of a full run finished (a run filtered to that test, or one
    // whose scan threw, would report live alternatives as dead).
    const SCANS = { routes: 0, surfaces: 0, threw: 0 };
    // The same pass reads each route for a raw ISO date ("2030-10-31") where a person reads it: a sentence, a tooltip, a
    // table cell, a chart's title or a series name - dates are printed through fmtDate (or a zone-aware sibling). The
    // Transactions register is left out: a dense register keeps the ISO column. "no route prints a raw ISO date" reads
    // what this records.
    const ISO_DATES: string[] = [];
    // and for a file, a storage key or a command named in what it renders (fileKeyHits)
    const NAMED: string[] = [];
    const scanRoute = async (route: string, stats: { formatters: number; chars: number }): Promise<string[]> => {
      chart.options.length = 0;
      chart.pairs.length = 0;
      try {
        await mount(`#${realize(route)}`);
        await settled(route);
        for (const c of CAPABILITIES.filter((x) => x.route === route && !x.when)) await screen.findByTestId(c.id, {}, { timeout: 8000 });
        stats.chars += document.body.textContent?.length ?? 0;
        const hits = [...textHits(document.body, true), ...chart.options.flatMap((o) => optionHits(route, o, stats))].map((h) => `${route}: ${h}`);
        ISO_DATES.push(...isoDateHits(document.body, chart.options).map((h) => `${route}: ${h}`));
        NAMED.push(...fileKeyHits(document.body).map((h) => `${route}: ${h}`));
        cleanup();
        SCANS.routes++;
        return hits;
      } catch (e) {
        SCANS.threw++;
        throw e;
      }
    };

    it("the scanners themselves: an amount at the END of an element is not hidden by the next element's first letter or digit; a percent token is the only exemption", () => {
      const scan = (ui: React.ReactElement) => { const r = render(ui); const hits = textHits(r.container); r.unmount(); return hits; };
      // the data-gap shape: the context is the last text of its row, and the next card's words follow it directly
      expect(scan(<ul><li>first<span> \u2014 2025-01-02 \u2192 2025-01-02, net 1,234.56</span>Uncategorized spending</li></ul>).length, "glued to a letter").toBeGreaterThan(0);
      expect(scan(<p><span>net 123.45</span>3 internal rows</p>).length, "glued to a digit").toBeGreaterThan(0);
      expect(scan(<div><section><ul><li>row <span>net -1,234.56</span></li></ul></section><section>Learned rules</section></div>).length, "the LAST row of a card").toBeGreaterThan(0);
      expect(scan(<div><span>$</span><span>1,234</span></div>).length, "an amount split across sibling elements").toBeGreaterThan(0);
      // what is not an amount stays quiet: a count split across text nodes of one element, a percent, a date, masked text
      expect(scan(<button>{"\u2b07 Export CSV ("}{"1,151"}{" rows)"}</button>)).toEqual([]);
      expect(scan(<p>{"Data through May \u00b7 1,234 open questions"}</p>), "the sidebar's grouped count").toEqual([]);
      expect(scan(<h2>{"Data quality \u2014 1,050 warnings \u00b7 1 open question"}</h2>), "Home's grouped count").toEqual([]);
      expect(scan(<p><span>12.50%</span>Uncategorized <span>2026-05</span> <span>\u2022\u2022\u2022\u2022\u2022</span></p>)).toEqual([]);
      // a whole number in a box (a budget baseline as a placeholder) is a shown figure; a slider's position, a date, a
      // rate box, a small count and the mask are not
      expect(scan(<div><input placeholder="1234" readOnly /></div>).length, "a whole-number placeholder").toBeGreaterThan(0);
      expect(scan(<div><input value="250" readOnly /></div>).length, "a whole-number value").toBeGreaterThan(0);
      expect(scan(<div><input type="range" value="1500" readOnly /><input type="date" value="2026-01-02" readOnly />
        <label>Rate %<input value="275" readOnly /></label><input value="35" readOnly /><input placeholder={"\u2022\u2022\u2022\u2022\u2022"} readOnly /></div>)).toEqual([]);
      // an amount seeded unrounded with one decimal ("2746.4", a number's spelling of 2,746.40) is seen in a slider and a
      // box alike; a rate's "6.5", "6.125" and "7" in a "%" label, and a lone "6.125" or "7", are not
      expect(scan(<label>Monthly $<input type="range" min="500" max="6000" step="50" value="2746.4" readOnly /></label>), "a slider's one-decimal amount")
        .toEqual(["input[value] ####.#"]);
      expect(scan(<div><input value="45.5" readOnly /></div>).length, "a box's one-decimal amount").toBeGreaterThan(0);
      expect(scan(<div><label>Rate (APR) %<input type="range" min="2" max="10" step="0.005" value="6.5" readOnly />
        <input value="6.125" readOnly /></label><label>Return %<input value="7" readOnly /></label>
        <input value="6.125" readOnly /><input type="range" value="7" readOnly /></div>)).toEqual([]);
      expect(raw("$123,456 (12.5%)"), "a percent in the string must not excuse an amount").toBe(true);
      expect(raw("123,456 (12.5%)")).toBe(true);
      expect(raw("$123.5K \u00b7 12%")).toBe(true);
      expect(raw("123456%")).toBe(false);
      expect(raw("12.5%")).toBe(false);
      expect(raw("\u2022\u2022\u2022\u2022\u2022 (12.5%)")).toBe(false);
    });

    it("sees money when privacy mode is off (a net that cannot see amounts would pass everything): text, attributes and chart formatters", async () => {
      // the data-gap list holds the unmatched internal leg of truth's account, its context an unprefixed amount
      const gaps = some(json("open_questions.json").data_gaps, "data gaps");
      expect(gaps.some((g: { question: string; context: string }) => g.question.includes(plantedAccount("data_gap.account").name)
        && /\d\.\d\d\b/.test(g.context)), "a data gap names truth's account and states an amount").toBe(true);
      const stats = { formatters: 0, chars: 0 };
      const home = await scanRoute("/", stats);
      const data = await scanRoute("/data", stats);
      expect(home.some((h) => h.startsWith("/: text")), "Home's text shows $ figures").toBe(true);
      expect(home.some((h) => /option .*prints a raw amount/.test(h)), "Home's chart formatters print raw amounts").toBe(true);
      // the data-gap contexts: `net 1,234.56`, no dollar sign - the unprefixed patterns must see them
      expect(data.some((h) => /text ...\S* ?[-−]?#,###\.##|net [-−]?#+\.##|net [-−]?#,###/.test(h)), "Data's unprefixed data-gap amounts are seen").toBe(true);
      expect(stats.formatters > 0 && stats.chars > 1000, "the scan read the pages' content").toBe(true);
    }, LOOP_TIMEOUT);

    it("privacy mode: no route leaks an amount in its text, its attributes or its chart options", async () => {
      setPrivate(true);
      const leaks: string[] = [];
      const stats = { formatters: 0, chars: 0 };
      try {
        for (const route of ROUTE_PATTERNS) leaks.push(...(await scanRoute(route, stats)));
      } finally {
        setPrivate(false);
      }
      expect(stats.formatters, "formatters were actually evaluated").toBeGreaterThan(20);
      expect([...new Set(leaks)], "money that would show through privacy mode").toEqual([]);
    }, LOOP_TIMEOUT * 3);

    // The per-route pass scans each route's default render only. These surfaces exist only after a click or with
    // stored data - the `when` setups of the dedicated `it`s above, the register's open rows and its editor, the
    // budget editor, and the report builder's count and table variants - so they get a scan of their own. `money`
    // marks the ones that show amounts: the privacy-off control must find money on each of them (a surface that
    // never opened would pass anything). A count chart prints counts through its formatters by design, so its scan
    // is text and attributes only (`charts: false`); the same counts in the table variant are scanned in full.
    type Surface = { name: string; route: string; at: string; money: boolean; charts?: false; rows?: true;
      serve?: [string, () => unknown][]; open?: () => Promise<void>; shown: () => boolean };
    const SPLIT_AT = "2031-01-15";       // years after any real row: the synthetic split purchase is the newest
    const splitRows = () => {
      const base_ = synthTxn({ date: SPLIT_AT, vendor: "Synthetic Split Mart", description: "SYNTHETIC SPLIT MART", type: "purchase",
        is_internal: false, needs_review: false, tags: [], transfer_group_id: null, earner: null, category_source: "keyword" });
      const parent = { ...base_, id: "syn-split-p", amount: -300, category: "Cash & Misc", subcategory: "Uncategorized",
        parent_id: null, is_parent: true };
      const part = (id: string, amount: number, category: string, subcategory: string | null) =>
        ({ ...base_, id, amount, category, subcategory, category_source: "split", parent_id: parent.id, is_parent: false });
      return { count: 3, transactions: [parent, part("syn-split-a", -210, "Groceries", null), part("syn-split-b", -90, "Shopping", "Home Goods")] };
    };
    const registerRow = (pick: RegExp): HTMLElement => {
      const row = [...document.querySelectorAll<HTMLElement>('[role="row"][aria-expanded]')].find((r) => pick.test(r.textContent ?? ""));
      expect(row !== undefined, "the register drew the row").toBe(true);
      return row!;
    };
    const SURFACES: Surface[] = [
      { name: "vendor drill", route: "/spending", at: "/spending", money: true,
        open: async () => {
          chart.clickName = categoryView(json("transactions.json").transactions)
            .find((t: { type: string; vendor: string | null }) => t.type === "purchase" && t.vendor)?.vendor ?? "";
          fireEvent.click(within(await screen.findByTestId("card-top-vendors", {}, { timeout: 8000 })).getByTestId("chart"));
          await screen.findByTestId("card-vendor-drill", {}, { timeout: 8000 });
        },
        shown: () => screen.queryByTestId("card-vendor-drill") !== null },
      { name: "register: an open split purchase", route: "/spending/transactions", at: "/spending/transactions?from=2031-01-01",
        money: true, rows: true, serve: [["transactions.json", splitRows]],
        open: async () => { fireEvent.click(registerRow(/Synthetic Split Mart/)); },
        shown: () => document.querySelector('[data-testid="split-parts"] li') !== null },
      { name: "register: an open split part and its category editor", route: "/spending/transactions",
        at: "/spending/transactions?from=2031-01-01&categories=Groceries", money: true, rows: true, serve: [["transactions.json", splitRows]],
        open: async () => {
          fireEvent.click(registerRow(/split of/));
          fireEvent.click(screen.getByRole("button", { name: "Edit category" }));
        },
        shown: () => new RegExp(`part of a .+ purchase on ${fmtDate("2031-01-15")}`).test(document.body.textContent ?? "")
          && screen.queryByRole("button", { name: "Close editor" }) !== null },
      { name: "budget editor", route: "/cash-flow/budget", at: "/cash-flow/budget", money: true,
        open: async () => {
          fireEvent.click(within(await screen.findByTestId("card-budget", {}, { timeout: 8000 })).getByRole("button", { name: "Edit budgets" }));
        },
        shown: () => /Edit monthly budgets/.test(screen.queryByTestId("card-budget")?.textContent ?? "") },
      { name: "second house with saved scenarios", route: "/planning/second-house", at: "/planning/second-house", money: true,
        serve: [["api/scenarios", () => SYNTHETIC_SCENARIOS]],
        open: async () => { await screen.findByTestId("card-funding-plan", {}, { timeout: 8000 }); },
        shown: () => CAPABILITIES.filter((c) => c.when === "scenario").every((c) => screen.queryByTestId(c.id) !== null) },
      { name: "investments: the asset-class targets saved in the app", route: "/accounts/investments",
        at: "/accounts/investments", money: true,
        serve: [["api/prefs", () => ({ version: 1, updated_at: "2026-01-02T00:00:00Z", budgets: {}, targets: { "Stocks/ETF": 40, Retirement: 30 },
          projections: {}, payroll: { phases: [] }, saved_filters: [], reports: [] })]],
        shown: () => screen.queryByTestId("card-class-drift") !== null },
      // the family store serving the household's own app-entered assets (the exports' list), so the editor opens on one
      { name: "settings: a manual asset's editor, its amount boxes open", route: "/settings", at: "/settings", money: true,
        serve: [["api/manual-assets", () => ({ version: 1, updated_at: "2026-01-02T00:00:00.000Z",
          assets: json("accounts.json").manual_assets.filter((a: { origin: string }) => a.origin === "app")
            .map(({ id, name, owner, asset_class, liquid, acquired, values }: Record<string, unknown>) =>
              ({ id, name, owner, asset_class, liquid, acquired, values, updated_at: "2026-01-02T00:00:00.000Z" })) })]],
        open: async () => {
          const card = await screen.findByTestId("card-manual-assets", {}, { timeout: 8000 });
          fireEvent.click((await within(card).findAllByRole("button", { name: /^Edit (?!a copy)/ }, { timeout: 8000 }))[0]);
        },
        shown: () => screen.queryByTestId("card-manual-assets")?.querySelector("fieldset input[inputmode='decimal']") != null },
      { name: "reports: the spend table", route: "/spending/reports", at: `/spending/reports?from=${REPORT_FROM}&chart=table`, money: true,
        shown: () => screen.queryByTestId("card-report-result")?.querySelector("table") != null },
      { name: "reports: counts as a chart", route: "/spending/reports", at: `/spending/reports?from=${REPORT_FROM}&measure=count`,
        money: false, charts: false,
        shown: () => /\(count\)/.test(screen.queryByTestId("card-report-result")?.textContent ?? "") },
      { name: "reports: counts as a table", route: "/spending/reports", at: `/spending/reports?from=${REPORT_FROM}&measure=count&chart=table`,
        money: false,
        shown: () => screen.queryByTestId("card-report-result")?.querySelector("table") != null },
    ];
    const scanSurface = async (s: Surface, stats: { formatters: number; chars: number }): Promise<string[]> => {
      chart.options.length = 0;
      chart.pairs.length = 0;
      const base = globalThis.fetch;
      for (const [name, body] of s.serve ?? []) stubServe(name, body);
      rows.all = s.rows === true;
      try {
        await mount(`#${s.at}`);
        await settled(s.route);
        await s.open?.();
        await waitFor(() => expect(s.shown(), `${s.name}: the surface is on screen`).toBe(true), { timeout: 8000 });
        stats.chars += document.body.textContent?.length ?? 0;
        const charts = s.charts === false ? [] : chart.options.flatMap((o) => optionHits(s.route, o, stats));
        const hits = [...textHits(document.body, true), ...charts].map((h) => `${s.name}: ${h}`);
        // a surface shown only after an interaction (the report as a table, an open editor) is read for raw ISO dates
        // too: the route pass above never sees it
        ISO_DATES.push(...isoDateHits(document.body, chart.options).map((h) => `${s.name}: ${h}`));
        SCANS.surfaces++;
        return hits;
      } catch (e) {
        SCANS.threw++;
        throw e;
      } finally {
        cleanup();
        rows.all = false;
        chart.clickName = "";
        vi.stubGlobal("fetch", base);
      }
    };

    it("sees money on every conditional surface that shows amounts when privacy mode is off (each one really opened)", async () => {
      reportWindow();
      some(categoryView(json("transactions.json").transactions).filter((t: { type: string; vendor: string | null }) => t.type === "purchase" && t.vendor),
        "purchases with a vendor (the drill)");
      const stats = { formatters: 0, chars: 0 };
      for (const s of SURFACES) {
        const hits = await scanSurface(s, stats);
        if (s.money) expect(hits.length > 0, `${s.name}: the unmasked surface shows money`).toBe(true);
      }
    }, LOOP_TIMEOUT * 2);

    it("privacy mode: no conditional surface leaks an amount - the drill, the register's open rows and editor, the budget editor, the asset-class targets, second house, a manual asset's editor, a picked year, the report variants", async () => {
      reportWindow();
      setPrivate(true);
      const leaks: string[] = [];
      const stats = { formatters: 0, chars: 0 };
      try {
        for (const s of SURFACES) leaks.push(...(await scanSurface(s, stats)));
      } finally {
        setPrivate(false);
      }
      expect(stats.formatters, "formatters were actually evaluated").toBeGreaterThan(10);
      expect([...new Set(leaks)], "money that would show through privacy mode").toEqual([]);
    }, LOOP_TIMEOUT * 2);

    // after every scan above (vitest runs a file's tests in order): an allowance nothing on any route or surface needs is
    // a hole kept open for nothing, and the next figure that happens to fit it would pass unseen
    it("every STATIC_OK alternative matches on some route", () => {
      expect(SCANS.threw === 0, `${SCANS.threw} route or surface scan(s) threw before finishing (their tests fail above), `
        + "so an alternative only those scans print cannot be told from a dead one").toBe(true);
      // a full run scans Home and Data with privacy off, then every route; and every surface twice (privacy off, then on)
      const routes = 2 + ROUTE_PATTERNS.length;
      const surfaces = 2 * SURFACES.length;
      expect(SCANS.routes >= routes && SCANS.surfaces >= surfaces, "run this test together with the route and surface scans: "
        + `it saw ${SCANS.routes} of ${routes} route scans and ${SCANS.surfaces} of ${surfaces} surface scans`).toBe(true);
      const dead = STATIC_OK_PARTS.filter((_, i) => !STATIC_SEEN.has(i));
      expect(dead, "STATIC_OK alternatives no scanned route or surface printed").toEqual([]);
    });

    it("the raw-date pattern finds a date or a year-month a person reads, and no file name, id or instant", () => {
      for (const s of ["as of 2030-10-31", "basis: 2030-04 → 2030-09", "2030-10 (partial)", "(2030-12)"]) expect(ISO_DATE.test(s), s).toBe(true);
      for (const s of ["statement_2030-10.csv", "2030-10-31T18:00:00", "id-2030-10", "2030-13", "2030-10/11", "v2030-10"]) {
        expect(ISO_DATE.test(s), s).toBe(false);
      }
    });
    it("no route prints a raw ISO date in a sentence, a tooltip, a table cell or a chart title", () => {
      expect(SCANS.routes >= ROUTE_PATTERNS.length && SCANS.threw === 0, "run this test together with the route scans").toBe(true);
      expect([...new Set(ISO_DATES)], "dates a person reads go through fmtDate").toEqual([]);
    });
    it("no route renders an export file's name, a storage key the app uses or a make command", () => {
      expect(SCANS.routes >= ROUTE_PATTERNS.length && SCANS.threw === 0, "run this test together with the route scans").toBe(true);
      expect([...new Set(NAMED)], "say it in words; a config key only as a hint").toEqual([]);
    });
    it("the rendered-name scan sees a file, a key and a command alone or in a sentence, and leaves plain words", () => {
      const scan = (ui: React.ReactElement) => { const r = render(ui); const hits = fileKeyHits(r.container); r.unmount(); return hits; };
      expect(MAKE_TARGETS).toEqual(expect.arrayContaining(["ingest", "fetch", "refresh", "simplefin-claim"]));
      expect(scan(<p>{"accounts.json"}</p>).length, "a file name rendered on its own").toBe(1);
      expect(scan(<p>saved under {EFUND_OVERRIDES_KEY}</p>).length).toBe(1);
      expect(scan(<span title="run make refresh">x</span>).length).toBe(1);
      expect(scan(<p>run <code>make</code> <code>ingest</code></p>).length, "split across elements").toBe(1);
      expect(scan(<p>Make room, make up the rest; re-run the ingest (<code>accounts.yaml</code>).</p>)).toEqual([]);
    });
    it("the date scan sees a raw ISO date in text, a title and a chart title, and leaves the register, a file name and an instant", () => {
      const scan = (ui: React.ReactElement, options: any[] = []) => {
        const r = render(ui); const hits = isoDateHits(r.container, options); r.unmount(); return hits;
      };
      expect(scan(<p>Through 2030-10-31.</p>).length).toBe(1);
      expect(scan(<span title="as of 2030-10-31">x</span>).length).toBe(1);
      expect(scan(<div />, [{ title: { text: "Spend since 2030-10-31" } }]).length).toBe(1);
      expect(scan(<div role="table" aria-label="Transactions"><div role="cell">2030-10-31</div></div>)).toEqual([]);
      expect(scan(<p>checking-2030-10-31.csv · 2030-10-31T05:00:00Z · Oct 31, 2030 · <input type="date" value="2030-10-31" readOnly /></p>)).toEqual([]);
    });
  });

  // A configured rename (category_rules.yaml `renames:`) changes the names the exports carry. The app's own category
  // literals - the essentials, the uncategorized pile, the money movements - must follow it, so the
  // same household exported under other names reads the same on every page that leans on them. Here every category the
  // app names is renamed (and four subcategories), served from the household's export through the stubbed fetch.
  describe("a category rename moves no figure", () => {
    const CATS = ["Transport", "Groceries", "Cash & Misc", "Internal", "Income", "Dining", "Subscriptions", "Fees & Interest",
      "Housing", "Utilities", "Health", "Insurance", "Pets", "Debt", "Kids", "Taxes", "Entertainment", "Shopping"];
    // the two largest income streams of the newest three months, by the export's own names (neither the reserved
    // catch-all nor Dividends, which is renamed below anyway): the streams the income chart draws first
    const STREAMS: string[] = HAVE_DATA ? (() => {
      const total = new Map<string, number>();
      for (const m of json("cashflow_monthly.json").months.slice(-3) as { income_by_subcategory: Record<string, number> }[]) {
        for (const [s, v] of Object.entries(m.income_by_subcategory)) total.set(s, (total.get(s) ?? 0) + v);
      }
      const other = (json("assumptions.json").reserved ?? SHIPPED_RESERVED).income_other_sub;
      return [...total].filter(([s, v]) => v > 0 && s !== other && s !== "Dividends")
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 2).map(([s]) => s);
    })() : [];
    const PAIRS: [string, string, string][] = [["Cash & Misc", "ATM", "Cash Machine"], ["Cash & Misc", "Checks", "Paper Checks"],
      ["Transport", "Car Payment", "Auto Loan"], ["Housing", "Mortgage", "Home Loan"],
      ["Fees & Interest", "Margin Interest", "Margin Cost"],
      ...STREAMS.map((s, i): [string, string, string] => ["Income", s, `Renamed stream ${i + 1}`]),
      ["Income", "Dividends", "Dividend Income"]];
    const newName = (c: string) => `${c} X`;
    // the entries as the ETL would take them: the pair renames first (the first matching entry wins), then each whole category
    const RENAMES = [
      ...PAIRS.map(([c, sub, to]) => ({ from: { category: c, subcategory: sub }, to: { category: newName(c), subcategory: to } })),
      ...CATS.map((c) => ({ from: { category: c }, to: { category: newName(c) } })),
    ];
    const rename = (c: string | null, sub: string | null): [string | null, string | null] => {
      if (c === null || !CATS.includes(c)) return [c, sub];
      const pair = PAIRS.find(([pc, ps]) => pc === c && ps === sub);
      return [newName(c), pair ? pair[2] : sub];
    };
    const renamedRows = (rows: { category: string | null; subcategory: string | null }[]) =>
      rows.map((t) => { const [category, subcategory] = rename(t.category, t.subcategory); return { ...t, category, subcategory }; });

    /** serve the export as the ETL would write it after RENAMES: the rows, the monthly by-category totals, the recurring
     *  items and the taxonomy carry the new names, and assumptions.json lists the renames */
    function serveRenamed() {
      const base = stubServe("transactions.json", () => {
        const doc = json("transactions.json");
        doc.transactions = renamedRows(doc.transactions);
        return doc;
      });
      stubServe("cashflow_monthly.json", () => {
        const doc = json("cashflow_monthly.json");
        for (const m of doc.months) {
          const by: Record<string, unknown> = {};
          for (const [c, v] of Object.entries(m.by_category)) by[CATS.includes(c) ? newName(c) : c] = v;
          m.by_category = by;
          // the income streams are subcategories of Income, keyed by their sub name alone
          const inc: Record<string, unknown> = {};
          for (const [k, v] of Object.entries(m.income_by_subcategory)) inc[PAIRS.find(([pc, ps]) => pc === "Income" && ps === k)?.[2] ?? k] = v;
          m.income_by_subcategory = inc;
        }
        return doc;
      });
      stubServe("recurring.json", () => {
        const doc = json("recurring.json");
        doc.items = doc.items.map((r: { category: string | null }) => ({ ...r, category: r.category !== null && CATS.includes(r.category) ? newName(r.category) : r.category }));
        return doc;
      });
      stubServe("assumptions.json", () => {
        const doc = json("assumptions.json");
        doc.category_renames = RENAMES;
        const tax: Record<string, string[]> = {};
        for (const [c, subs] of Object.entries(doc.taxonomy as Record<string, string[]>)) tax[CATS.includes(c) ? newName(c) : c] = subs;
        doc.taxonomy = tax;
        return doc;
      });
      return base;
    }

    /** the numbers a page shows that lean on the category names, read from the DOM (names are not in them) */
    async function figures(route: string): Promise<Record<string, string>> {
      await mount(`#${route}`);
      await settled(route);
      const txt = (id: string) => document.querySelector(`[data-testid="${id}"]`)?.textContent ?? null;
      const out: Record<string, string> = {};
      const pill = document.querySelector('aside a[href="#/review"] [role="img"]');
      out.badge = pill?.getAttribute("aria-label") ?? "";
      if (route === "/planning") {
        await screen.findByTestId("kpi-cash-runway", {}, { timeout: 8000 });
        out.runway = txt("kpi-cash-runway") ?? "";
        out.forecast = txt("card-forecast") ?? "";
        out.efund = (document.querySelector('[data-testid="efund-essentials"] summary')?.textContent ?? "").replace(/ — .*/, "");
        out.fire = [...document.querySelectorAll('[data-testid^="kpi-"]')].map((e) => e.textContent).join("|");
      } else if (route === "/data") {
        const card = await screen.findByTestId("card-open-questions", {}, { timeout: 8000 });
        out.review = card.querySelector("p")?.textContent ?? "";
        out.uncat = ([...card.querySelectorAll("p")].find((p) => /^Uncategorized spending:/.test(p.textContent ?? ""))?.textContent ?? "").replace(/sit in .* without/, "sit in X without");
      } else if (route === "/") {
        out.kpis = [...document.querySelectorAll('[data-testid^="kpi-"]')].map((e) => e.textContent).join("|");
        out.dq = [...document.querySelectorAll("h2, h3")].map((h) => h.textContent ?? "").filter((t) => /to review/.test(t)).join("|");
      } else if (route === "/cash-flow/budget") {
        const split = chart.pairs.find((p) => String(p.option?.series?.[0]?.name ?? "").startsWith("Fixed"));
        expect(split !== undefined, "the fixed-vs-discretionary chart was built").toBe(true);
        out.split = JSON.stringify(split!.option.series.map((x: { data: number[] }) => x.data));
      } else if (route === "/planning/second-house") {
        out.guard = (await screen.findByTestId("kpis-guardrails", {}, { timeout: 8000 })).textContent ?? "";
      } else if (route === "/cash-flow") {
        // the income chart's streams, in order, with their colours: ordering and colouring go by the stream, not the spelling
        const stack = chart.pairs.find((p) => p.option?.series?.[0]?.stack === "income");
        expect(stack !== undefined, "the income chart was built").toBe(true);
        const streams: [string, string | null][] = stack!.option.series.map((x: { name: string; color?: string }) => [back(x.name), x.color ?? null]);
        out.streams = JSON.stringify(streams);
        // not only the same before and after: the renamed streams really are drawn and coloured (a lookup that finds nothing in both would agree)
        const named = streams.filter(([n]) => STREAMS.includes(n));
        expect(named.length > 0 && named.every(([, color]) => color !== null), `${route}: the renamed income streams are coloured`).toBe(true);
      } else if (route === "/review") {
        await screen.findByTestId("card-review-inbox", {}, { timeout: 8000 });
        out.inbox = document.querySelector("h1")?.parentElement?.querySelector("p")?.textContent?.replace(/\s+/g, " ") ?? "";
        out.pills = [...document.querySelectorAll("button")].map((b) => b.textContent ?? "").filter((t) => /^(All|Venmo & Zelle|Cash & checks|Deposits|Other)\s*\d+/.test(t)).join("|");
      }
      // ...and the whole page, read back under the original names: only the renamed words may differ
      const raw = document.body.textContent ?? "";
      seenNew = CATS.some((c) => raw.includes(newName(c)));
      out.page = back(raw);
      return out;
    }
    let seenNew = false;                      // did the last page print a renamed word (so the rename really reached it)
    const back = (text: string) => {
      let t = text;
      for (const [, sub, to] of PAIRS) t = t.split(to).join(sub);
      for (const c of CATS) t = t.split(newName(c)).join(c);
      return t;
    };

    const ROUTES = ["/planning", "/data", "/", "/cash-flow", "/cash-flow/budget", "/planning/second-house", "/accounts/investments",
      "/trends", "/review"];
    for (const route of ROUTES) {
      it(`${route}: the same household under renamed categories reads the same`, async () => {
        // the renames reach the household's rows: income streams to rename, and rows in the renamed categories
        some(STREAMS, "income streams in the newest three months");
        some(json("transactions.json").transactions.filter((t: { category: string | null }) => t.category !== null && CATS.includes(t.category)),
          "rows in the renamed categories");
        const scenarios = stubServe("api/scenarios", () => SYNTHETIC_SCENARIOS);
        try {
          chart.pairs.length = 0;
          const before = await figures(route);
          cleanup();
          chart.pairs.length = 0;
          const base = serveRenamed();
          try {
            const after = await figures(route);
            if (["/planning", "/cash-flow/budget", "/review", "/data"].includes(route))
              expect(seenNew, `${route} prints a renamed category, so the rename reached the page`).toBe(true);
            if (route === "/data") expect((document.body.textContent ?? "").includes(`sit in ${newName("Cash & Misc")} without`), "Data names the pile by its new name").toBe(true);
            expect(Object.keys(before).length > 0 && Object.values(before).some((v) => v.length > 0), "the page showed figures").toBe(true);
            const mask = (t: string) => t.replace(/\d/g, "#");        // a failure names where the text moved, never an amount
            for (const k of Object.keys(before)) {
              const at = [...before[k]].findIndex((ch, i) => ch !== after[k][i]);
              expect(after[k] === before[k], `${route} ${k} moved under the rename at ${at}: "${mask(before[k].slice(Math.max(0, at - 30), at + 30))}" vs "${mask(after[k].slice(Math.max(0, at - 30), at + 30))}"`).toBe(true);
            }
          } finally { vi.stubGlobal("fetch", base); }
        } finally { vi.stubGlobal("fetch", scenarios); }
      }, ROUTE_TIMEOUT);
    }
  });
});

// Last on purpose (master: a trailing `it("every capability has a route")`). Most of these need no
// exports, so they live outside the skipIf and also run in CI without a data mount. The per-route
// loop proves each manifest row renders; these refuse the drift it cannot see — a route with no
// rows (its loop `it` would pass on the page testid alone), a `when` row no `it` asserted, and a
// row left pointing at a route that moved or was deleted.
describe("capability manifest", () => {
  it("no redirect source is also a page", () => {
    const routes = new Set(ROUTE_PATTERNS);
    for (const [from] of REDIRECTS) expect(routes.has(from), from).toBe(false);
  });
  it("RECURRING_GATED names only real routes", () => {
    const routes = new Set(ROUTE_PATTERNS);
    for (const r of RECURRING_GATED) expect(routes.has(r), `${r} is not a route`).toBe(true);
  });
  it("every route has at least one manifest row", () => {
    const redirects = new Set(REDIRECTS.map(([from]) => from));
    for (const route of ROUTE_PATTERNS.filter((r) => !redirects.has(r))) {
      expect(CAPABILITIES.some((c) => c.route === route), `no manifest row for ${route}`).toBe(true);
    }
  });
  // after every `when` it above: vitest runs a file's tests in order. A first-run row shows only while a household's
  // exports hold nothing, which this household's never do (asserted above): the route matrix's cards check holds each
  // one to every route the first-run page stands in for, on the demo household that has nothing, in both states. So
  // does a module-off row: this household has every page on (asserted above), and the matrix holds the module-off
  // card to every route switched off on the demo households that have one.
  it.skipIf(!HAVE_DATA)("every `when` capability was asserted by a dedicated it", () => {
    for (const c of CAPABILITIES.filter((x) => x.when && x.when !== "first-run" && x.when !== "module-off")) {
      expect(ASSERTED_WHEN.has(c.id), `${c.id} (when: ${c.when}) was never asserted`).toBe(true);
    }
  });
  it("every capability has a route", () => {
    const routes = new Set(ROUTE_PATTERNS);
    for (const c of CAPABILITIES) expect(routes.has(c.route), `${c.id} -> ${c.route}`).toBe(true);
  });
});

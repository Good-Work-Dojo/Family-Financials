// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// The route matrix: every route of PAGES mounted on whatever exports it is given, with five checks each
// (test/matrixChecks.ts): it renders, it prints no broken value, privacy mode leaves no money on it, it logs no
// console.error, every card its manifest rows promise is on it. The gate runs it on each demo household (FF_PROFILE=<profile>, tools/verify/ci.sh) and on a
// household's own exports (FF_PROFILE unset: `household`). It reads the exports alone, no truth.json, so it holds for
// any household, and the clock stands at their ingest instant. A (profile, route, check) a page does not pass yet is a
// row of test/matrixGaps.ts and runs as a test that the check fails with the message fragment its row names, which
// fails once the page passes or fails another way. One describe per route, one test per check: a gap row names exactly
// what fails. Each route is mounted twice in its describe's beforeAll, privacy off then on, and the tests read what the
// two mounts recorded; nothing waits unbounded, so a route that never settles is a `renders` failure, not a hang. A
// failure quotes a few characters around a broken value and the money scan's redacted hits (digits become #), at most
// five of each, never the page.
// A household whose exports hold nothing sees the first-run page in place of every page but Data and Settings
// (lib/firstRun.ts), and each block of routes ends with one test that every route shows the first-run page in the
// household's state where it stands in, and its own page everywhere else (a demo household's state is named below). The
// demo household `empty` runs three blocks: its exports as they are (set up); the same with two accounts configured
// (waiting for data); and with the first-run state held off, which is what its pages look like with no data, the empty
// states a household with transactions and no balance still meets. A gap row of `empty` applies to all three.
// A page switched off (its module off, or a need the household lacks: App.tsx closedRoutes) shows the module-off page
// at its address, which the five checks read like any page; each block also ends with one test that the module-off
// page shows on exactly the routes switched off for the household (named below for each demo household) where the
// first-run page does not stand in. The family store is unreachable here, so the modules are the config's seed.
import { cleanup, configure, render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import App, { closedRoutes, PAGES } from "./App";
import { pageTestId } from "./capabilities";
import { loadAppData } from "./data/DataContext";
import { type FirstRunState, firstRunState, replacedInFirstRun } from "./lib/firstRun";
import { effectiveModules, factsOf } from "./lib/modules";
import { setPrivate } from "./lib/privacy";
import { freezeAtIngest, thaw } from "./test/demoClock";
import { EXPORTS_DIR, HAVE_EXPORTS, readExport, requireExports } from "./test/exports";
import {
  brokenValues, cardsFailure, chartValues, firstRunShown, listed, matrixRoutes, missingCards, moduleOffShown, moneyHits,
  renderProblem, settleRoute, strayCards, throwSites, watchConsoleErrors,
} from "./test/matrixChecks";
import { DEMO_PROFILES, GAPS, MATRIX_CHECKS, type MatrixCheck } from "./test/matrixGaps";

// the first-run state as the app reads it, which a block can hold off: then the app shows its pages whatever the
// exports hold, as it did for a household with nothing before the first-run page existed
const firstRun = vi.hoisted(() => ({ heldOff: false }));
vi.mock("./lib/firstRun", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./lib/firstRun")>();
  return {
    ...actual,
    firstRunState: (...args: Parameters<typeof actual.firstRunState>) => (firstRun.heldOff ? null : actual.firstRunState(...args)),
  };
});

// jsdom has no canvas: a chart is a stub that records the option it was handed, which the money scan's formatter pass
// and the values check's text pass read
const chart = vi.hoisted(() => ({ options: [] as unknown[] }));
vi.mock("./components/EChart", () => ({
  default: ({ height, option }: { height?: number; option?: unknown }) => {
    chart.options.push(option);
    return <div data-testid="chart" style={{ height }} />;
  },
}));
// jsdom has no layout, so the real virtualizer draws no row of the register: the stand-in draws its first 25 rows (a
// screenful), as the register's own unit tests draw theirs. The real hook is still called first, so hook order is the same.
vi.mock("@tanstack/react-virtual", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-virtual")>();
  const real = actual.useVirtualizer;
  return {
    ...actual,
    useVirtualizer: (opts: Parameters<typeof real>[0]) => {
      real(opts);
      return {
        getTotalSize: () => opts.count * 40,
        getVirtualItems: () => Array.from({ length: Math.min(opts.count, 25) },
          (_, index) => ({ index, start: index * 40, end: index * 40 + 40, size: 40, key: index, lane: 0 })),
        measureElement: () => {}, scrollToIndex: () => {},
      } as unknown as ReturnType<typeof real>;
    },
  };
});

requireExports();
/** the household the exports belong to: a demo profile's name, or `household` for a family's own exports */
const PROFILE = process.env.FF_PROFILE || "household";
const ROUTES = matrixRoutes(PAGES);
/** the id `/accounts/:id` is mounted with: the first exported account, or `none` (the unknown-account card) without one */
const ACCOUNT_ID: string = (() => {
  if (!HAVE_EXPORTS) return "none";
  try {
    return readExport<{ accounts?: { id?: unknown }[] }>("accounts.json").accounts?.map((a) => a.id)
      .find((id): id is string => typeof id === "string" && id !== "") ?? "none";
  } catch {
    return "none";   // no readable accounts.json: the page answers an unknown id
  }
})();
const realize = (route: string) => route.replace(":id", encodeURIComponent(ACCOUNT_ID));

/** how long a mount may take to settle; the describe's beforeAll allows two mounts and their slack */
const SETTLE_MS = 10_000;
const ROUTE_MS = 3 * SETTLE_MS;

/** what one mount of a route recorded: why it did not render (null: it did), its broken values, the manifest's cards
 *  missing from it, its own cards on screen where another page stands in for it, the first-run state it shows and
 *  whether it shows the module-off page (the privacy-off mount only: the page's and its charts' text, the page's test
 *  ids), its money hits (the privacy-on mount only) and the console.error calls made while it mounted and settled */
interface Visit {
  problem: string | null; values: string[]; cards: string[]; stray: string[]; firstRun: FirstRunState | null;
  moduleOff: boolean; money: string[]; errors: string[];
}
const NOT_MOUNTED: Visit = {
  problem: "not mounted", values: [], cards: [], stray: [], firstRun: null, moduleOff: false, money: [], errors: [],
};

/** mounts `route` (privacy on or off, a cleared local storage), settles it and records what the checks read */
async function visit(route: string, privately: boolean): Promise<Visit> {
  chart.options.length = 0;
  try { localStorage.clear(); } catch { /* a storage that cannot be cleared leaves the last route's flags */ }
  setPrivate(privately);
  const pageId = pageTestId(route);
  const watch = watchConsoleErrors();
  const seen: Visit = { ...NOT_MOUNTED, problem: null };
  try {
    window.location.hash = `#${realize(route)}`;
    render(<App />);
    const problem = (await settleRoute(pageId, SETTLE_MS)) ?? renderProblem(document.body, pageId);
    // a boundary's card prints the error's message alone: add where it was thrown, from what the boundary logged
    const sites = problem?.includes("error-boundary card") ? throwSites(watch.lines()) : [];
    seen.problem = sites.length ? `${problem} (${sites.join("; ")})` : problem;
    if (privately) seen.money = moneyHits(document.body, route, chart.options);
    else {
      seen.values = [...brokenValues(document.body, document.title), ...chartValues(chart.options)];
      seen.cards = missingCards(document.body, route);
      seen.stray = strayCards(document.body, route);
      seen.firstRun = firstRunShown(document.body);
      seen.moduleOff = moduleOffShown(document.body);
    }
  } catch (e) {
    seen.problem = `mounting threw: ${e instanceof Error ? e.message : String(e)}`;
  } finally {
    try {
      cleanup();
    } finally {
      seen.errors = watch.stop();   // console.error is given back even when the unmount throws
      setPrivate(false);
    }
  }
  return seen;
}

/** one check of a route; when the gap table lists it for this household, a test that the check fails with the
 *  fragment its row names: a fixed page (nothing thrown) and a page that now fails another way both fail it */
function check(route: string, name: MatrixCheck, body: () => void) {
  const gap = GAPS.find((g) => g.profile === PROFILE && g.route === route && g.check === name);
  if (!gap) {
    it(`${route} — ${name}`, body);
    return;
  }
  it(`${route} — ${name} (known gap)`, () => {
    expect(body, `a known gap of ${PROFILE}: the check must fail with a message containing "${gap.expect}" `
      + "(remove the row of a page that passes; rewrite the row of one that fails another way)").toThrow(gap.expect);
  });
}

it("every gap names a real profile, route and check, once, with a one-line reason and fragment, and none is full's", () => {
  const oneLine = (s: string) => s.trim() !== "" && !/\n/.test(s);
  const bad = GAPS.filter((g) => !(DEMO_PROFILES as readonly string[]).includes(g.profile) || g.profile === "full"
    || !ROUTES.includes(g.route) || !MATRIX_CHECKS.includes(g.check) || !oneLine(g.reason) || !oneLine(g.expect));
  expect(bad.map((g) => `${g.profile} ${g.route} ${g.check}`), "gap rows naming no demo profile but full, route or check, or no one-line reason and fragment").toEqual([]);
  const keys = GAPS.map((g) => `${g.profile} ${g.route} ${g.check}`);
  expect(keys.filter((k, i) => keys.indexOf(k) !== i), "gap rows listed twice").toEqual([]);
});

it("the household is a family's own or one of the demo profiles", () => {
  expect(PROFILE === "household" || (DEMO_PROFILES as readonly string[]).includes(PROFILE),
    `FF_PROFILE names no demo profile (got ${PROFILE})`).toBe(true);
});

/** the first-run state each demo household's exports put it in: only `empty` has nothing */
const FIRST_RUN_OF: Readonly<Record<string, FirstRunState | null>> = {
  full: null, new: null, tiny: null, empty: "setup", "no-investments": null, "no-income": null,
};

/** the routes each demo household switches off: `full` has Home buying on and a mortgage, the others leave the module
 *  off, and `new`, `tiny` and `empty` have no mortgage account */
const BOTH = ["/planning/home", "/planning/second-house"];
const CLOSED_OF: Readonly<Record<string, readonly string[]>> = {
  full: [], new: BOTH, tiny: BOTH, empty: BOTH, "no-investments": ["/planning/second-house"],
  "no-income": ["/planning/second-house"],
};

/** one pass of every route over the exports: `manifest` rewrites manifest.json as it is served, `heldOff` holds the
 *  first-run state off, and `expect` is the first-run state the household must be in (undefined: a family's own
 *  exports, whatever they give) */
interface Block { title: string; manifest?: (m: Record<string, any>) => Record<string, any>; heldOff?: boolean; expect?: FirstRunState | null }
const BLOCKS: Block[] = [
  { title: "the route matrix on the household's exports", expect: PROFILE === "household" ? undefined : FIRST_RUN_OF[PROFILE] },
  ...(PROFILE !== "empty" ? [] : [
    { title: "the route matrix on the same exports with two accounts configured (waiting for data)",
      manifest: (m: Record<string, any>) => ({ ...m, setup: { ...m.setup, accounts: 2 } }), expect: "waiting" as const },
    { title: "the route matrix on the same exports with the first-run state held off (the pages with no data)",
      heldOff: true, expect: null },
  ]),
];

describe.skipIf(!HAVE_EXPORTS)("the route matrix", () => {
  beforeAll(() => {
    freezeAtIngest();
    configure({ getElementError: (message) => new Error(message ?? "") });   // no DOM dump on a miss
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: false, media: query, addEventListener() {}, removeEventListener() {},
    }));
    (globalThis as any).ResizeObserver = class {
      observe() {} unobserve() {} disconnect() {}
    };
  });
  afterAll(thaw);

  for (const block of BLOCKS) describe(block.title, () => matrixBlock(block));
});

/** every route of the block, five checks each, then the first-run state the household is in and where it shows */
function matrixBlock(block: Block) {
  /** each route's privacy-off visit, for the block's first-run test */
  const opened = new Map<string, Visit>();
  beforeAll(() => {
    firstRun.heldOff = block.heldOff ?? false;
    // the exports directory stands in for the server; anything else (the family store's api/*) is unreachable
    vi.stubGlobal("fetch", async (url: string) => {
      const name = String(url).replace(/^\.\//, "");
      const body = readFileSync(join(EXPORTS_DIR, name), "utf8");
      const served = name === "manifest.json" && block.manifest ? JSON.stringify(block.manifest(JSON.parse(body))) : body;
      return new Response(served, { status: 200 });
    });
  });
  afterAll(() => { firstRun.heldOff = false; });

  for (const route of ROUTES) {
    describe(route, () => {
      let open: Visit = NOT_MOUNTED;
      let masked: Visit = open;
      beforeAll(async () => {
        open = await visit(route, false);
        masked = await visit(route, true);
        opened.set(route, open);
      }, ROUTE_MS);

      check(route, "renders", () => {
        expect(open.problem === null, `${route} does not render: ${open.problem}`).toBe(true);
      });
      check(route, "values", () => {
        expect(open.problem === null, `${route} did not render, so no value was checked (see its renders check)`).toBe(true);
        expect(open.values.length === 0, `${route} prints broken values: ${listed(open.values)}`).toBe(true);
      });
      check(route, "private", () => {
        expect(masked.problem === null, `${route} did not render with privacy on, so nothing was scanned: ${masked.problem}`).toBe(true);
        expect(masked.money.length === 0, `${route} shows money with privacy on: ${listed(masked.money)}`).toBe(true);
      });
      check(route, "console", () => {
        const errors = [...open.errors.map((e) => `privacy off: ${e}`), ...masked.errors.map((e) => `privacy on: ${e}`)];
        const distinct = [...new Set(errors)].map((e) => `${e}${errors.filter((x) => x === e).length > 1 ? ` (×${errors.filter((x) => x === e).length})` : ""}`);
        expect(errors.length === 0, `${route} logs ${errors.length} console.error call(s):\n  ${distinct.join("\n  ")}`).toBe(true);
      });
      check(route, "cards", () => {
        const failure = cardsFailure(route, open.problem, open.cards, open.stray);
        expect(failure === null, failure ?? "").toBe(true);
      });
    });
  }

  const named = block.expect === undefined ? "the household's first-run state" : `first-run state ${block.expect ?? "none"}`;
  it(`${named}, shown on every route the first-run page stands in for and on no other`, async () => {
    const { data } = await loadAppData();
    const state = firstRunState(data.manifest, data);
    if (block.expect !== undefined) expect(state, `the household's first-run state`).toBe(block.expect);
    const wrong = ROUTES.filter((r) => opened.get(r)?.firstRun !== (state !== null && replacedInFirstRun(r) ? state : null));
    expect(wrong, `routes that do not show ${state === null ? "their own page" : `the ${state} page where it stands in`}`).toEqual([]);
  });

  it("the module-off page, shown on every route switched off for the household that the first-run page leaves, and on no other", async () => {
    const { data } = await loadAppData();
    const closed = closedRoutes(effectiveModules(data.assumptions.modules, {}), factsOf(data.assumptions));
    if (PROFILE !== "household") expect(closed, `the routes ${PROFILE} switches off`).toEqual(CLOSED_OF[PROFILE]);
    const state = firstRunState(data.manifest, data);
    const want = ROUTES.filter((r) => closed.includes(r) && !(state !== null && replacedInFirstRun(r)));
    expect(ROUTES.filter((r) => opened.get(r)?.moduleOff), "the routes that show the module-off page").toEqual(want);
  });
}

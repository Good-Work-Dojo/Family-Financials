// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// The route matrix's five checks (test/matrixChecks.ts) on hand-built DOM: what each one finds and what it lets pass.
// The boundary card, the failed load, the unknown-account card, the first-run page and the module-off page are the app's
// own components, so a change to how one marks itself fails here.
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CAPABILITIES, type Capability } from "../capabilities";
import ModuleOff from "../components/ModuleOff";
import SectionBoundary from "../components/SectionBoundary";
import { DataProvider } from "../data/DataContext";
import type { Setup } from "../data/types";
import { fmtUSD } from "../lib/format";
import type { Gate } from "../lib/modules";
import { setPrivate } from "../lib/privacy";
import { UnknownAccount } from "../sections/AccountPage";
import FirstRun from "../sections/FirstRun";
import {
  brokenValues, cardsFailure, chartValues, excusedIds, firstRunRows, firstRunShown, listed, missingCards, moduleOffRows,
  moduleOffShown, moneyHits, pageIsReplaced, renderProblem, settledOn, settleRoute, strayCards, throwSites,
  watchConsoleErrors,
} from "./matrixChecks";

// the first-run page reads what is configured from the data; nothing else this file renders reads it
const configured = vi.hoisted(() => ({ setup: { accounts: 2, feed: false, import_files: 0, manual_assets: 0 } as Setup }));
vi.mock("../data/DataContext", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../data/DataContext")>()),
  useData: () => ({ data: { manifest: { setup: configured.setup } } }),
}));
// the module-off page reads the household's modules: every one off, and no mortgage
vi.mock("../data/useModules", () => ({
  useModules: () => ({ modules: { home_buying: false }, facts: { mortgage: false }, setModule: () => {} }),
}));

afterEach(() => {
  cleanup();
  setPrivate(false);
  vi.unstubAllGlobals();
});

const root = () => document.body;

describe("renders", () => {
  it("a page root on screen, nothing loading: settled and rendered", () => {
    render(<div data-testid="page-x"><p>content</p></div>);
    expect(settledOn(root(), "page-x")).toBe(true);
    expect(renderProblem(root(), "page-x")).toBeNull();
  });

  it("a skeleton on screen is not settled; a page root that never comes is named", () => {
    render(<div><div data-testid="page-x" /><div role="status" aria-busy="true" /></div>);
    expect(settledOn(root(), "page-x")).toBe(false);
    cleanup();
    render(<div data-testid="hub-x">the hub shell alone</div>);
    expect(settledOn(root(), "page-x")).toBe(false);
    expect(renderProblem(root(), "page-x")).toBe("the page root page-x is not on screen");
  });

  it("the error-boundary card pageRoute() wraps a page in is found, with the error it caught", () => {
    const Crash = (): never => { throw new Error("cannot read the snapshot"); };
    const watch = watchConsoleErrors();   // React and the boundary report the crash
    try {
      render(<SectionBoundary resetKey="/x"><div data-testid="page-x"><Crash /></div></SectionBoundary>);
    } finally {
      watch.stop();
    }
    expect(settledOn(root(), "page-x"), "the card is a settled state").toBe(true);
    expect(renderProblem(root(), "page-x")).toBe("1 error-boundary card(s): cannot read the snapshot");
  });

  it("the failed first load (DataProvider's full-page error) is found, with the loader's error", async () => {
    vi.stubGlobal("fetch", async (url: string) => (String(url).endsWith("manifest.json")
      ? new Response("{}", { status: 500 }) : Promise.reject(new Error("offline"))));
    render(<DataProvider><div data-testid="page-x" /></DataProvider>);
    await screen.findByRole("alert", {}, { timeout: 5000 });
    await waitFor(() => expect(settledOn(root(), "page-x")).toBe(true));
    expect(renderProblem(root(), "page-x")).toMatch(/^the data failed to load: .*manifest\.json: HTTP 500/);
  });
});

describe("settling", () => {
  /** a page root holding a skeleton, in the document as the app's would be; `busy` toggles the skeleton */
  const page = () => {
    const el = document.createElement("div");
    el.dataset.testid = "page-x";
    const skeleton = document.createElement("div");
    skeleton.setAttribute("aria-busy", "true");
    el.append(skeleton);
    document.body.append(el);
    return { busy: (on: boolean) => (on ? skeleton.setAttribute("aria-busy", "true") : skeleton.removeAttribute("aria-busy")) };
  };

  it("a page that settles late, then shows a skeleton again, is given what is left of the bound, with Date frozen", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });   // as the matrix runs: Date stands at one instant, timers are real
    try {
      const p = page();
      setTimeout(() => {
        p.busy(false);                                   // settled at 1.2 s of the 1.5 s bound...
        setTimeout(() => p.busy(true), 10);              // ...and loading again for good
      }, 1200);
      const started = performance.now();
      const problem = await settleRoute("page-x", 1500, 100);
      const took = performance.now() - started;
      expect(problem).toMatch(/^still loading after \d/);
      expect(took < 1500 + 100 + 300, `the two waits took ${Math.round(took)} ms of a 1500 ms bound`).toBe(true);
    } finally {
      vi.useRealTimers();
      document.body.replaceChildren();
    }
  });

  it("a page that settles and stays settled is null; one that never settles says so", async () => {
    try {
      const p = page();
      setTimeout(() => p.busy(false), 50);
      expect(await settleRoute("page-x", 1000, 100)).toBeNull();
      p.busy(true);
      expect(await settleRoute("page-x", 200, 50)).toMatch(/^still loading after \d/);
      document.body.replaceChildren();
      expect(await settleRoute("page-x", 200, 50)).toMatch(/^no page root, error card or load failure after \d/);
    } finally {
      document.body.replaceChildren();
    }
  });
});

describe("values", () => {
  const found = (ui: React.ReactElement, title = "") => { const r = render(ui); const out = brokenValues(r.container, title); r.unmount(); return out; };

  it("finds a broken value in the text and in each scanned attribute, a box's value and the document's title", () => {
    expect(found(<p>You saved NaN% of your income this month</p>), "a few characters either side").toEqual(['text: NaN in "You saved NaN% of your in…"']);
    expect(found(<span title="NaN%">x</span>), "a NaN in an attribute").toEqual(['span[title]: NaN in "NaN%"']);
    expect(found(<button aria-label="Open Invalid Date">x</button>)).toEqual(['button[aria-label]: Invalid Date in "Open Invalid Date"']);
    expect(found(<input placeholder="undefined" readOnly />).length).toBe(1);
    expect(found(<img alt="chart of Infinity" />).length).toBe(1);
    expect(found(<div role="slider" aria-valuetext="−Infinity mo" />).length).toBe(1);
    expect(found(<input value="NaN" readOnly />)).toEqual(['input.value: NaN in "NaN"']);
    expect(found(<p>ok</p>, "undefined · Family Financials")).toEqual(['document.title: undefined in "undefined · Family Fi…"']);
  });

  it("finds each broken form: NaN with a currency mark or a unit, undefined, Infinity and ∞, a 0000- date, the 9999 sentinel", () => {
    for (const s of ["$NaN", "−$NaNK", "Dec NaN", "Invalid Date", "undefined months", "Sam's undefined", "$Infinity", "Infinity%",
      "Infinity mo", "−Infinity", "Infinity", "$∞", "∞ mo", "since 0000-01-01", "through 9999-12-31", "9999-12-31", "9999",
      "Dec 9999", "through … 9999-12-31", "• Dec 9999"]) {
      expect(found(<p>{s}</p>).length, s).toBe(1);
    }
  });

  it("an element's value at its end is not glued to the next element's first letter", () => {
    expect(found(<p><span>NaN</span><span>days</span></p>).length).toBe(1);
    expect(found(<p><span>cover undefined</span><b>Savings</b></p>).length).toBe(1);
  });

  it("lets years, grouped numbers, ids, names and the app's own prose pass", () => {
    for (const s of ["1999", "since 1999", "$99,999", "99,999 rows", "x9999y", "ACT-demo-9999", "acct.9999", "2026-01-01",
      "BANANA BREAD CO", "Nancy", "Nanaimo", "Infinite scroll", "INFINITY POOLS", "UNDEFINED", "undefinedness",
      "no essential row ticked — the floor is undefined (toggles in the gauge below)", "10000-step",
      // the register's descriptors and vendor names: a card's or a reference's number, and a brand
      "CARD ****9999", "INVOICE #9999", "STORE (9999)", "ACME −9999", "Infinity Insurance", "Infinity Auto Glass",
      // a masked number: an ellipsis or bullets before the digits, or a run of bullets and one space
      "ACCOUNT …9999", "card •9999", "card ••••9999", "card •••• 9999", "card • 9999"]) {
      expect(found(<p>{s}</p>), s).toEqual([]);
    }
  });

  it("finds a broken value in a chart option's text, naming the recorded option and the path; a clean option passes", () => {
    const clean = {
      title: { text: "Spending by month" }, legend: { data: ["Groceries", "Dining"] },
      xAxis: { type: "category", data: ["Sep 2026", "Oct 2026"] }, yAxis: { type: "value" },
      series: [{ name: "Groceries", type: "bar", data: [1, 2], label: { formatter: (v: number) => String(v) } }],
    };
    const broken = { xAxis: { type: "category", data: ["Sep 2026", "Invalid Date"] }, series: [{ name: "NaN", type: "bar", data: [1] }] };
    expect(chartValues([clean])).toEqual([]);
    expect(chartValues([clean, broken])).toEqual([
      'chart 2 xAxis.data[1]: Invalid Date in "Invalid Date"',
      'chart 2 series[0].name: NaN in "NaN"',
    ]);
    expect(chartValues([broken, clean, broken]), "a re-render's repeat is reported once, at the first option").toEqual([
      'chart 1 xAxis.data[1]: Invalid Date in "Invalid Date"',
      'chart 1 series[0].name: NaN in "NaN"',
    ]);
    expect(chartValues([{ tooltip: { formatter: () => "NaN" } }]), "a function is the private check's, never called here").toEqual([]);
  });

  it("a failure lists five findings, then how many more", () => {
    expect(listed(["a", "b"])).toBe("a; b");
    expect(listed(["a", "b", "c", "d", "e"])).toBe("a; b; c; d; e");
    expect(listed(["a", "b", "c", "d", "e", "f", "g"])).toBe("a; b; c; d; e; +2 more");
  });
});

describe("private", () => {
  const hits = (ui: React.ReactElement, options: unknown[] = []) => {
    const r = render(ui); const out = moneyHits(r.container, "/x", options); r.unmount(); return out;
  };

  it("finds an amount printed past the mask, in text, in an attribute and in a chart option", () => {
    setPrivate(true);
    expect(hits(<p>Spent {"$1,234"} this month</p>).length, "a hand-formatted $1,234").toBeGreaterThan(0);
    expect(hits(<span title="1,234.56 owed">x</span>).length, "an amount in an attribute").toBeGreaterThan(0);
    expect(hits(<p>ok</p>, [{ series: [{ type: "bar", label: { show: true, formatter: "{c}" } }] }]).length, "a raw {c} label").toBeGreaterThan(0);
    expect(hits(<p>ok</p>, [{ yAxis: { type: "value", axisLabel: { formatter: (v: number) => `$${v}` } } }]).length,
      "an axis formatter that ignores the mask").toBeGreaterThan(0);
  });

  it("lets the masked formatters, a percent, a date and a count pass", () => {
    setPrivate(true);
    expect(hits(<p>Spent {fmtUSD(1234)} · 12.5% · 2026-05-01 · 3 accounts</p>)).toEqual([]);
    // the grouped counts the sidebar and Home print before these nouns
    expect(hits(<p>Data through May · 1,234 open questions</p>)).toEqual([]);
    expect(hits(<p>Data quality — 1,050 warnings · 1 open question</p>)).toEqual([]);
    expect(hits(<p>ok</p>, [{ yAxis: { type: "value", axisLabel: { formatter: (v: number) => fmtUSD(v) } },
      tooltip: { valueFormatter: (v: number) => fmtUSD(v) } }])).toEqual([]);
  });
});

describe("cards", () => {
  const rows: Capability[] = [
    { id: "card-a", route: "/x" }, { id: "kpis-b", route: "/x" }, { id: "card-c", route: "/x" },
    { id: "card-only-some", route: "/x", when: "data" }, { id: "card-elsewhere", route: "/y" },
  ];
  const missing = (ui: React.ReactElement, route = "/x") => {
    const r = render(ui); const out = missingCards(r.container, route, rows); r.unmount(); return out;
  };

  it("names each card of the route that is not on screen, by id, in manifest order", () => {
    expect(missing(<div data-testid="page-x"><section data-testid="card-a" /></div>)).toEqual(["kpis-b", "card-c"]);
    expect(missing(<div data-testid="page-x"><div data-testid="card-c" /><div data-testid="kpis-b" /><div data-testid="card-a" /></div>))
      .toEqual([]);
    expect(missing(<div data-testid="page-x" />), "a card is found by its test id, not by a heading").toEqual(["card-a", "kpis-b", "card-c"]);
  });

  it("a `when` row may be absent, and another route's row is not looked for", () => {
    const all = <div data-testid="page-x"><div data-testid="card-a" /><div data-testid="kpis-b" /><div data-testid="card-c" /></div>;
    expect(missing(all), "card-only-some and card-elsewhere are absent").toEqual([]);
    expect(missing(<div data-testid="page-y" />, "/y")).toEqual(["card-elsewhere"]);
  });

  it("the unknown-account card replaces the account page: nothing is looked for there, and only there", () => {
    const unknown = (
      <MemoryRouter>
        <div data-testid="page-accounts-id"><UnknownAccount id="none" /></div>
      </MemoryRouter>);
    const r = render(unknown);
    expect(pageIsReplaced(r.container, "/accounts/:id")).toBe(true);
    expect(missingCards(r.container, "/accounts/:id"), "the manifest's own account rows").toEqual([]);
    expect(pageIsReplaced(r.container, "/accounts"), "a replacement stands in on its own routes only").toBe(false);
    r.unmount();
    const page = render(<div data-testid="page-accounts-id"><section data-testid="card-account-header" /></div>);
    expect(pageIsReplaced(page.container, "/accounts/:id")).toBe(false);
    expect(missingCards(page.container, "/accounts/:id"), "an account's own page is held to its rows")
      .toEqual(CAPABILITIES.filter((c) => c.route === "/accounts/:id" && !c.when && c.id !== "card-account-header").map((c) => c.id));
    page.unmount();
  });

  describe("a stand-in", () => {
    const withStandIn: Capability[] = [
      { id: "card-a", route: "/x" }, { id: "card-chart-1", route: "/x" }, { id: "card-chart-2", route: "/x" },
      { id: "card-none-yet", route: "/x", when: "data", insteadOf: ["card-chart-1", "card-chart-2"] },
      { id: "card-other-none", route: "/y", when: "data", insteadOf: ["card-a"] },
    ];
    const missingOf = (ui: React.ReactElement, route = "/x") => {
      const r = render(ui); const out = missingCards(r.container, route, withStandIn); r.unmount(); return out;
    };
    it("on screen, excuses every row it names", () => {
      expect(missingOf(<div><div data-testid="card-a" /><div data-testid="card-none-yet" /></div>)).toEqual([]);
    });
    it("absent, excuses nothing: the rows it names must be there", () => {
      expect(missingOf(<div><div data-testid="card-a" /><div data-testid="card-chart-1" /><div data-testid="card-chart-2" /></div>))
        .toEqual([]);
      expect(missingOf(<div><div data-testid="card-a" /><div data-testid="card-chart-2" /></div>)).toEqual(["card-chart-1"]);
    });
    it("neither the stand-in nor what it replaces on screen: both replaced rows are missing", () => {
      expect(missingOf(<div><div data-testid="card-a" /></div>)).toEqual(["card-chart-1", "card-chart-2"]);
    });
    it("excuses only rows of its own route, and only while it is on screen there", () => {
      // card-other-none names card-a, but it belongs to /y and is not on this page
      expect(missingOf(<div><div data-testid="card-none-yet" /></div>)).toEqual(["card-a"]);
      const r = render(<div><div data-testid="card-none-yet" /><div data-testid="card-other-none" /></div>);
      expect([...excusedIds(r.container, "/x", withStandIn)]).toEqual(["card-chart-1", "card-chart-2"]);
      expect([...excusedIds(r.container, "/y", withStandIn)]).toEqual(["card-a"]);
      r.unmount();
    });
  });

  it("fails a visit that did not render, names what is missing (five, then how many more), passes a complete page", () => {
    expect(cardsFailure("/x", "the page root page-x is not on screen", [])).toBe(
      "/x did not render, so no card was looked for (see its renders check)");
    expect(cardsFailure("/x", null, [])).toBeNull();
    expect(cardsFailure("/x", null, ["card-a", "card-b"])).toBe("/x lacks 2 card(s) of its manifest rows: card-a; card-b");
    expect(cardsFailure("/x", null, ["a", "b", "c", "d", "e", "f"])).toBe("/x lacks 6 card(s) of its manifest rows: a; b; c; d; e; +1 more");
  });

  it("names the route's own cards shown where the first-run page stands in, after what is missing", () => {
    expect(cardsFailure("/x", null, [], ["kpis-home"])).toBe("/x shows 1 card(s) of its own page where another stands in: kpis-home");
    expect(cardsFailure("/x", null, ["card-first-run"], ["kpis-home"])).toBe("/x lacks 1 card(s) of its manifest rows: "
      + "card-first-run; /x shows 1 card(s) of its own page where another stands in: kpis-home");
  });

  // the first-run page is the app's own component, so a change to how it marks itself or to its cards fails here
  describe("the first-run page", () => {
    const ids = (state: "setup" | "waiting") => firstRunRows(state).map((c) => c.id);
    const shown = (state: "setup" | "waiting", extra: React.ReactNode = null) =>
      render(<MemoryRouter><div data-testid="page-x"><FirstRun state={state} />{extra}</div></MemoryRouter>);

    it("its rows: the heading card in both states, the steps to set up, the summary while waiting", () => {
      expect(ids("setup")).toEqual(["card-first-run", "card-first-run-steps"]);
      expect(ids("waiting")).toEqual(["card-first-run", "card-first-run-summary"]);
    });

    it("stands in on every route but Data and Settings, and is read in the state it shows", () => {
      const r = shown("setup");
      expect(firstRunShown(r.container)).toBe("setup");
      for (const route of ["/", "/cash-flow", "/accounts/:id", "/review"]) expect(pageIsReplaced(r.container, route), route).toBe(true);
      for (const route of ["/data", "/settings"]) expect(pageIsReplaced(r.container, route), route).toBe(false);
      r.unmount();
      const w = shown("waiting");
      expect(firstRunShown(w.container)).toBe("waiting");
      w.unmount();
      const none = render(<div data-testid="page-x" />);
      expect(firstRunShown(none.container)).toBeNull();
      expect(pageIsReplaced(none.container, "/")).toBe(false);
      none.unmount();
    });

    it("a replaced route owes the page's cards in its state and none of its own", () => {
      for (const state of ["setup", "waiting"] as const) {
        const r = shown(state);
        expect(missingCards(r.container, "/cash-flow"), `${state}: every card of the page is there`).toEqual([]);
        expect(missingCards(r.container, "/"), `${state}: Home's own rows are not looked for`).toEqual([]);
        expect(strayCards(r.container, "/cash-flow")).toEqual([]);
        r.unmount();
      }
      // a first-run marker whose cards are missing, in the state it claims
      const bare = render(<div data-testid="first-run" data-first-run="waiting" />);
      expect(missingCards(bare.container, "/cash-flow")).toEqual(["card-first-run", "card-first-run-summary"]);
      bare.unmount();
    });

    it("a card of the route's own page under the first-run page is stray; on Data and Settings the page's own rows apply", () => {
      const r = shown("setup", <><section data-testid="kpis-home" /><section data-testid="card-freshness" /></>);
      expect(strayCards(r.container, "/")).toEqual(["kpis-home"]);
      expect(strayCards(r.container, "/data"), "Data is never stood in for").toEqual([]);
      const dataRows = CAPABILITIES.filter((c) => c.route === "/data" && !c.when).map((c) => c.id);
      expect(missingCards(r.container, "/data"), "Data is held to its own rows").toEqual(dataRows.filter((id) => id !== "card-freshness"));
      r.unmount();
    });
  });

  // the module-off page is the app's own component too
  describe("the module-off page", () => {
    const shown = (gate: Gate, extra: React.ReactNode = null) =>
      render(<MemoryRouter><div data-testid="page-x"><ModuleOff label="A page" gate={gate} />{extra}</div></MemoryRouter>);

    it("its one row is the module-off card", () => {
      expect(moduleOffRows().map((c) => c.id)).toEqual(["card-module-off"]);
    });
    it("stands in on the gated routes only, for a module and for a need alike", () => {
      for (const gate of [{ module: "home_buying" }, { needs: "mortgage" }] as const) {
        const r = shown(gate);
        expect(moduleOffShown(r.container)).toBe(true);
        for (const route of ["/planning/home", "/planning/second-house"]) expect(pageIsReplaced(r.container, route), route).toBe(true);
        for (const route of ["/planning", "/accounts/liabilities"]) expect(pageIsReplaced(r.container, route), route).toBe(false);
        r.unmount();
      }
      const none = render(<div data-testid="page-x" />);
      expect(moduleOffShown(none.container)).toBe(false);
      none.unmount();
    });
    it("a replaced route owes the module-off card and none of its own; a card of its own page there is stray", () => {
      const r = shown({ module: "home_buying" });
      expect(missingCards(r.container, "/planning/second-house"), "the worksheet's own rows are not looked for").toEqual([]);
      expect(missingCards(r.container, "/planning/home"), "nor House's").toEqual([]);
      expect(strayCards(r.container, "/planning/second-house")).toEqual([]);
      r.unmount();
      const bare = render(<div data-testid="module-off" />);
      expect(missingCards(bare.container, "/planning/home"), "a marker without its card").toEqual(["card-module-off"]);
      bare.unmount();
      const stray = shown({ needs: "mortgage" }, <section data-testid="kpis-mortgage" />);
      expect(strayCards(stray.container, "/planning/home")).toEqual(["kpis-mortgage"]);
      stray.unmount();
    });
    it("where the page shows, its own rows are owed, and the module-off card is not", () => {
      const r = render(<div data-testid="page-x"><section data-testid="kpis-mortgage" /></div>);
      const own = CAPABILITIES.filter((c) => c.route === "/planning/home" && !c.when).map((c) => c.id);
      expect(missingCards(r.container, "/planning/home")).toEqual(own.filter((id) => id !== "kpis-mortgage"));
      expect(missingCards(r.container, "/planning/second-house"), "the module-off card is a `when` row")
        .toEqual(CAPABILITIES.filter((c) => c.route === "/planning/second-house" && !c.when).map((c) => c.id));
      expect(strayCards(r.container, "/planning/home")).toEqual([]);
      r.unmount();
    });
  });
});

describe("console", () => {
  it("records a React warning and a component's console.error, one line each, then gives console.error back", () => {
    const original = console.error;
    const watch = watchConsoleErrors();
    render(<ul>{["a", "a"].map((k) => <li key={k}>{k}</li>)}</ul>);
    console.error("section failed to render:", new Error("bad row"));
    const seen = watch.stop();
    expect(console.error, "console.error is given back").toBe(original);
    expect(seen.length).toBe(2);
    expect(seen[0]).toMatch(/^Warning: Encountered two children with the same key/);
    expect(seen[1], "an Error is its name and message, never its stack").toBe("section failed to render: Error: bad row");
    expect(seen.every((s) => !s.includes("\n") && s.length <= 300)).toBe(true);
  });

  it("lines() reads what was recorded so far and leaves console.error watched", () => {
    const original = console.error;
    const watch = watchConsoleErrors();
    console.error("first");
    expect(watch.lines()).toEqual(["first"]);
    expect(console.error, "still watched").not.toBe(original);
    console.error("second");
    expect(watch.stop()).toEqual(["first", "second"]);
  });

  it("the boundary's log gives where a crash it caught was thrown: component and app frame, once each", () => {
    const logged = [
      "TypeError: Cannot read properties of undefined (reading 'rows') at cardRows (src/lib/lifeSummary.ts:333:14)",
      "section failed to render: TypeError: Cannot read properties of undefined (reading 'rows') (at src/lib/lifeSummary.ts:333:14) at Liabilities (src/sections/Liabilities.tsx:23:62) at div",
      "section failed to render: TypeError: Cannot read properties of undefined (reading 'rows') (at src/lib/lifeSummary.ts:333:14) at Liabilities (src/sections/Liabilities.tsx:23:62) at div",
      "section failed to render: Error: no frame of the app's own",
    ];
    expect(throwSites(logged)).toEqual(["thrown in Liabilities at src/lib/lifeSummary.ts:333:14"]);
    expect(throwSites(["The above error occurred in the <Tools> component"])).toEqual([]);
  });

  it("names the app frame an error was thrown at", () => {
    const watch = watchConsoleErrors();
    const e = new Error("no snapshot");
    e.stack = "Error: no snapshot\n    at cardRows (/app/finance-app/app/src/lib/lifeSummary.ts:333:14)\n    at x (/w/node_modules/pkg/src/a.js:1:1)";
    console.error(e);
    expect(watch.stop()).toEqual(["Error: no snapshot (at src/lib/lifeSummary.ts:333:14)"]);
  });
});

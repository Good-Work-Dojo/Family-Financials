// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// The switchable pages in the running app: a page whose module is off, or that needs what the household lacks, keeps
// its address and shows the module-off page there, and the sidebar, ⌘K and its hub's tab bar leave it out; "Turn on
// Home buying" switches the module on for the family and the page shows at once, with no reload of the exports and no
// ingest, and while the family's preferences are still on their way no page says the module is off. The
// exports are a hand-built household (lib/__fixtures__.ts) and the planning pages are stand-ins: what is under test
// is the gate, not the pages.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EPOCH_ISO } from "./data/useSyncedDoc";
import { appDataFactory, mortgageFactory, snapFactory } from "./lib/__fixtures__";

vi.mock("./sections/Tools", () => ({ default: () => <p>projections page</p> }));
vi.mock("./sections/Mortgage", () => ({ default: () => <p>house page</p> }));
vi.mock("./sections/SecondHouse", () => ({ default: () => <p>second house page</p> }));
import App from "./App";

const base = appDataFactory(snapFactory())();
interface Call { method: string; name: string; body: unknown }

/** the exports of a household whose config states `modules` and that has a mortgage account or not, and a family
 *  store holding `prefs`; every request is recorded */
function serve({ modules = {}, mortgage = false, prefs = {}, holdPrefs }: {
  modules?: Record<string, boolean>; mortgage?: boolean; prefs?: Record<string, unknown>;
  /** the store's answer to the preferences' first read waits for this */
  holdPrefs?: Promise<void>;
}) {
  const calls: Call[] = [];
  let stored: Record<string, unknown> = { version: 1, updated_at: EPOCH_ISO, ...prefs };
  const exports: Record<string, unknown> = {
    "manifest.json": base.manifest,
    "transactions.json": { count: 0, transactions: [] },
    "assets_timeseries.json": base.assets,
    "assumptions.json": { ...base.assumptions, modules, mortgage: mortgage ? mortgageFactory() : null },
    "api/answers": { version: 1, updated_at: EPOCH_ISO, answers: [] },
  };
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const name = String(url).replace(/^\.\//, "");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ method, name, body });
    if (name === "api/prefs") {
      if (method === "GET" && holdPrefs) await holdPrefs;
      if (method === "PUT") stored = { ...stored, ...(body as Record<string, unknown>) };
      return new Response(JSON.stringify(stored), { status: 200 });
    }
    return name in exports ? new Response(JSON.stringify(exports[name]), { status: 200 })
      : new Response("Not Found", { status: 404 });
  });
  return calls;
}

async function open(path: string) {
  window.location.hash = `#${path}`;
  render(<App />);
  await screen.findByRole("navigation", { name: "Main" });
}
const tabBar = () => screen.queryByRole("navigation", { name: "Planning sections" });
const tabs = () => within(tabBar()!).getAllByRole("link").map((a) => a.textContent);
const h1s = () => screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent);
/** the page entries ⌘K lists, opened from the keyboard and closed again */
function paletteEntries(): string[] {
  fireEvent.keyDown(window, { key: "k", ctrlKey: true });
  const listed = within(screen.getByRole("listbox")).getAllByRole("option").map((o) => o.textContent ?? "");
  fireEvent.keyDown(window, { key: "k", ctrlKey: true });
  return listed.filter((t) => t.endsWith("page")).map((t) => t.replace(/^\S+ /, "").replace(/page$/, ""));
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.location.hash = ""; });

describe("a module that is off", () => {
  it("its address shows the module-off page, and nothing leads into it: no tab, no ⌘K entry", async () => {
    serve({});
    await open("/planning/second-house");
    const off = await screen.findByTestId("module-off");
    expect(off.getAttribute("data-gate")).toBe("home_buying");
    expect(screen.queryByText("second house page")).toBeNull();
    expect(h1s(), "the page's own name is the one h1").toEqual(["Second house"]);
    expect(tabBar(), "Planning with one page to show draws no tab bar").toBeNull();
    expect(screen.getByRole("link", { name: /Planning/ }), "the sidebar still lists Planning").toBeDefined();
    const pages = paletteEntries();
    expect(pages).toContain("Planning · Projections");
    expect(pages.some((p) => p.includes("Second house") || p.includes("House")), pages.join()).toBe(false);
  });

  it("Turn on shows the page at once: the module is the family's preference, with no reload and no ingest", async () => {
    const calls = serve({});
    await open("/planning/second-house");
    await screen.findByTestId("module-off");
    const loads = calls.filter((c) => c.name.endsWith(".json")).length;
    const button = screen.getByRole("button", { name: "Turn on Home buying" });
    button.focus();
    fireEvent.click(button);
    await screen.findByText("second house page");
    expect(screen.queryByTestId("module-off")).toBeNull();
    // the button went with the page it was on: focus is on the page's main landmark, not dropped to the document
    await waitFor(() => expect(document.activeElement, "focus is handed on").toBe(screen.getByRole("main")));
    expect(tabs(), "the tab bar shows the page").toEqual(["Projections", "Second house"]);
    expect(h1s()).toEqual(["Planning"]);
    expect(paletteEntries()).toContain("Planning · Second house");
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.name === "api/prefs")).toBe(true), { timeout: 3000 });
    const put = calls.find((c) => c.method === "PUT")!;
    expect((put.body as { modules?: unknown }).modules).toEqual({ home_buying: true });
    expect(calls.filter((c) => c.name.endsWith(".json")).length, "no export was read again").toBe(loads);
    expect(calls.some((c) => c.name.startsWith("api/refresh") && c.method !== "GET"), "no refresh or ingest was asked for").toBe(false);
  });

  it("the module-off page says what the page is for and links to Settings; a module switched on shows its page", async () => {
    serve({});
    await open("/planning/second-house");
    const card = await screen.findByTestId("card-module-off");
    expect(card.textContent).toBe("Second house"
      + "This page works through buying another home — its costs, how you would fund it and the cash it would leave — "
      + "and is part of Home buying, which is off.Turn on Home buyingSettings");
    const button = within(card).getByRole("button", { name: "Turn on Home buying" });
    expect(within(card).getAllByRole("button"), "one button, named for its module").toHaveLength(1);
    expect(button.getAttribute("aria-describedby"), "and described by what the page is for").toBe(card.querySelector("p")?.id);
    expect(within(card).getByRole("link", { name: "Settings" }).getAttribute("href")).toBe("#/settings");
    expect(card.querySelector("svg, canvas, [data-testid='chart']"), "no chart").toBeNull();
  });

  it("while the family's preferences are on their way, a bookmark to a module switched on in them never says it is off", async () => {
    let arrive = () => {};
    const held = new Promise<void>((resolve) => { arrive = resolve; });
    serve({ prefs: { modules: { home_buying: true } }, holdPrefs: held });
    // record whether the module-off page is ever drawn, from the first render on
    let drawnOff = false;
    const watch = new MutationObserver(() => { drawnOff ||= document.querySelector('[data-testid="module-off"]') !== null; });
    watch.observe(document.body, { childList: true, subtree: true });
    try {
      window.location.hash = "#/planning/second-house";
      render(<App />);
      // the exports load; the preferences do not: the page is the loading skeleton, not a verdict
      await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).not.toBeNull());
      await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
      expect(document.querySelector('[aria-busy="true"]'), "still loading").not.toBeNull();
      expect(screen.queryByTestId("module-off")).toBeNull();
      await act(async () => { arrive(); });
      await screen.findByText("second house page");
      expect(drawnOff, "the module-off page was never drawn").toBe(false);
    } finally {
      watch.disconnect();
    }
  });

  it("the config's seed switches it on; the family's false switches it off again", async () => {
    serve({ modules: { home_buying: true } });
    await open("/planning/second-house");
    await screen.findByText("second house page");
    cleanup();
    localStorage.clear();
    serve({ modules: { home_buying: true }, prefs: { modules: { home_buying: false } } });
    await open("/planning/second-house");
    await screen.findByTestId("module-off");
  });
});

describe("a page that needs a mortgage", () => {
  it("with no mortgage account: its address says what brings it, with no button, and nothing leads into it", async () => {
    serve({ modules: { home_buying: true } });
    await open("/planning/home");
    const off = await screen.findByTestId("module-off");
    expect(off.getAttribute("data-gate")).toBe("mortgage");
    expect(screen.getByTestId("card-module-off").textContent).toBe("House"
      + "This page follows the mortgage: its path to payoff, the home's equity and whether extra payments beat investing."
      + "It appears after the next ingest once an account is marked as the mortgage (mortgage: true in accounts.yaml).");
    expect(within(off).queryByRole("button"), "nothing to turn on").toBeNull();
    expect([...off.querySelectorAll("code")].map((c) => c.textContent)).toEqual(["mortgage: true", "accounts.yaml"]);
    expect(tabs(), "the module's page is on, the House tab is not").toEqual(["Projections", "Second house"]);
    expect(h1s()).toEqual(["House"]);
    expect(paletteEntries().filter((p) => p.startsWith("Planning"))).toEqual(["Planning · Projections", "Planning · Second house"]);
  });

  it("with one: the page shows, with its tab and its ⌘K entry", async () => {
    serve({ mortgage: true });
    await open("/planning/home");
    await screen.findByText("house page");
    expect(tabs(), "a second tab to show: the bar is back").toEqual(["Projections", "House"]);
    expect(paletteEntries().filter((p) => p.startsWith("Planning"))).toEqual(["Planning · Projections", "Planning · House"]);
    await act(async () => { window.location.hash = "#/planning/second-house"; });
    await screen.findByTestId("module-off");
    expect(tabs(), "on the module-off page the bar stays, with neither of its tabs current").toEqual(["Projections", "House"]);
    expect(within(tabBar()!).queryByRole("link", { current: "page" })).toBeNull();
    expect(h1s(), "the hub's h1 gives way to the page's").toEqual(["Second house"]);
  });
});

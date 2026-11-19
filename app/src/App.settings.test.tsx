// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// Settings in the running app: a module's switch is the family's preference, saved to the store and read back by every
// page at once (its hub's tab bar and ⌘K gain or lose its page with no reload), and the button under it clears it;
// each Elsewhere link opens its page on its card, scrolled to and with focus on the card's heading. The exports are a
// hand-built household (lib/__fixtures__.ts) and the planning pages are stand-ins: what is under test is the switch
// and the landing, not those pages.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AI_STATUS_NONE, type AiStatus } from "./data/types";
import { EPOCH_ISO } from "./data/useSyncedDoc";
import { appDataFactory, snapFactory } from "./lib/__fixtures__";
import { SCROLL_TARGETS } from "./lib/scrollTargets";

vi.mock("./sections/Tools", () => ({ default: () => <p>projections page</p> }));
vi.mock("./sections/Mortgage", () => ({ default: () => <p>house page</p> }));
vi.mock("./sections/SecondHouse", () => ({ default: () => <p>second house page</p> }));
// jsdom draws no chart: the pages an Elsewhere link opens hold charts beside the cards it lands on
vi.mock("./components/EChart", () => ({ default: () => <div data-testid="chart" /> }));
import App from "./App";

const base = appDataFactory(snapFactory())();
interface Call { method: string; name: string; body: unknown }

/** the exports of a household whose config states `modules`, and a family store holding `prefs`; every request is
 *  recorded, and a PUT of the preferences is kept as the store keeps it */
function serve({ modules = {}, prefs = {}, aiStatus }: {
  modules?: Record<string, boolean>; prefs?: Record<string, unknown>; aiStatus?: AiStatus;
} = {}) {
  const calls: Call[] = [];
  let stored: Record<string, unknown> = { version: 1, updated_at: EPOCH_ISO, ...prefs };
  const exports: Record<string, unknown> = {
    ...(aiStatus ? { "ai_status.json": aiStatus } : {}),
    "manifest.json": base.manifest,
    "transactions.json": { count: 0, transactions: [] },
    "assets_timeseries.json": base.assets,
    "assumptions.json": { ...base.assumptions, modules },
    "cashflow_monthly.json": { months: base.cashflow, cashflow_accounts: ["chk"] },
    "holdings.json": { holdings: [] },
    "api/answers": { version: 1, updated_at: EPOCH_ISO, answers: [] },
  };
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const name = String(url).replace(/^\.\//, "");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ method, name, body });
    if (name === "api/prefs") {
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
/** the page entries ⌘K lists, opened from the keyboard and closed again */
function paletteEntries(): string[] {
  fireEvent.keyDown(window, { key: "k", ctrlKey: true });
  const listed = within(screen.getByRole("listbox")).getAllByRole("option").map((o) => o.textContent ?? "");
  fireEvent.keyDown(window, { key: "k", ctrlKey: true });
  return listed.filter((t) => t.endsWith("page")).map((t) => t.replace(/^\S+ /, "").replace(/page$/, ""));
}
const puts = (calls: Call[]) => calls.filter((c) => c.method === "PUT" && c.name === "api/prefs");
const lastModules = (calls: Call[]) => (puts(calls).at(-1)?.body as { modules?: unknown } | undefined)?.modules;

const scrolled = vi.fn();
beforeEach(() => {
  localStorage.clear();
  scrolled.mockReset();
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  // jsdom has no layout engine: record which element was asked to scroll
  Element.prototype.scrollIntoView = function (this: Element) { scrolled(this.id); };
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.location.hash = ""; });

describe("Settings → Modules, in the running app", () => {
  it("the switch round-trips through the preferences store, the app follows at once, and the config's setting comes back", async () => {
    const calls = serve({});
    await open("/settings");
    const sw = await screen.findByRole("switch", { name: "Home buying" });
    const source = () => within(screen.getByTestId("card-modules")).getByTestId("module-source").textContent;
    expect(sw.getAttribute("aria-checked")).toBe("false");
    expect(source()).toBe("Off (the default)");
    expect(paletteEntries().some((p) => p.includes("Second house"))).toBe(false);
    const loads = calls.filter((c) => c.name.endsWith(".json")).length;

    fireEvent.click(sw);
    await waitFor(() => expect(screen.getByRole("switch", { name: "Home buying" }).getAttribute("aria-checked")).toBe("true"));
    expect(source()).toBe("On for the whole family, switched in the dashboard");
    expect(paletteEntries(), "⌘K gains the module's page at once").toContain("Planning · Second house");
    await waitFor(() => expect(lastModules(calls)).toEqual({ home_buying: true }), { timeout: 3000 });

    fireEvent.click(screen.getByRole("button", { name: "Go back to the default (off) for Home buying" }));
    await waitFor(() => expect(source()).toBe("Off (the default)"));
    expect(screen.getByRole("switch", { name: "Home buying" }).getAttribute("aria-checked")).toBe("false");
    expect(document.activeElement, "focus is on the switch").toBe(screen.getByRole("switch", { name: "Home buying" }));
    expect(paletteEntries().some((p) => p.includes("Second house")), "and loses it again").toBe(false);
    await waitFor(() => expect(lastModules(calls), "the store's copy is cleared, not set to off").toEqual({}), { timeout: 3000 });
    expect(calls.filter((c) => c.name.endsWith(".json")).length, "no export was read again").toBe(loads);
    expect(calls.some((c) => c.name.startsWith("api/refresh") && c.method !== "GET"), "no refresh or ingest was asked for").toBe(false);
  });

  it("a module the config switches on: the family's off wins, and clearing it follows the config again", async () => {
    const calls = serve({ modules: { home_buying: true } });
    await open("/settings");
    const source = () => within(screen.getByTestId("card-modules")).getByTestId("module-source").textContent;
    await waitFor(() => expect(source()).toBe("On in the household's config (modules: in household.yaml)"));
    fireEvent.click(screen.getByRole("switch", { name: "Home buying" }));
    await waitFor(() => expect(source()).toBe("Off for the whole family, switched in the dashboard"));
    await waitFor(() => expect(lastModules(calls)).toEqual({ home_buying: false }), { timeout: 3000 });
    fireEvent.click(screen.getByRole("button", { name: "Use the config's setting (on) for Home buying" }));
    await waitFor(() => expect(source()).toBe("On in the household's config (modules: in household.yaml)"));
    expect(screen.getByRole("switch", { name: "Home buying" }).getAttribute("aria-checked")).toBe("true");
  });

  it("a switch saved by another device reads as the family's, with the way back to the config", async () => {
    serve({ prefs: { modules: { home_buying: true } } });
    await open("/settings");
    await waitFor(() => expect(within(screen.getByTestId("card-modules")).getByTestId("module-source").textContent)
      .toBe("On for the whole family, switched in the dashboard"));
    expect(screen.getByRole("button", { name: "Go back to the default (off) for Home buying" })).toBeDefined();
  });
});

describe("Settings → Elsewhere, in the running app", () => {
  const landings: [string, string, string, string][] = [
    ["Privacy mode", "card-privacy", SCROLL_TARGETS.privacy, "page-data"],
    ["Projection defaults", "card-projection-defaults", SCROLL_TARGETS.projectionDefaults, "page-data"],
    ["Saved views and reports", "card-saved-views", SCROLL_TARGETS.savedViews, "page-data"],
    ["Budget", "card-budget", SCROLL_TARGETS.budget, "page-cash-flow-budget"],
    ["Target mix", "card-target-mix", SCROLL_TARGETS.targetMix, "page-accounts-investments"],
  ];
  it.each(landings)("%s opens its page on its card: scrolled to, with focus on the card's heading", async (label, testId, id, page) => {
    serve({});
    await open("/settings");
    fireEvent.click(within(await screen.findByTestId("card-elsewhere")).getByRole("link", { name: label }));
    await screen.findByTestId(page, {}, { timeout: 5000 });
    const target = await screen.findByTestId(testId);
    await waitFor(() => expect(scrolled).toHaveBeenCalledWith(id));
    expect(target.id).toBe(id);
    expect(document.activeElement, "focus is on the card's heading").toBe(target.querySelector("h2"));
    expect(window.location.hash).toBe(`#${page === "page-data" ? "/data" : page === "page-cash-flow-budget"
      ? "/cash-flow/budget" : "/accounts/investments"}?card=${id}`);
  });
});

describe("where the household's data goes, in the running app", () => {
  const ON: AiStatus = { ...AI_STATUS_NONE, configured: true, enabled: true, provider: "anthropic",
    model: "claude-opus-5-5", policy_version: 2, scope: "anthropic||cards", consent_policy: 2 };
  const line = () => screen.getByTestId("local-only-line").textContent;
  const promise = () => within(screen.getByTestId("card-about")).getByTestId("about-promise");

  it("with AI categorization off, the sidebar and About say nothing leaves", async () => {
    serve({ aiStatus: { ...ON, enabled: false } });
    await open("/settings");
    await screen.findByTestId("card-about");
    expect(line()).toBe("local-only · no outside request");
    expect(promise().textContent).toBe("The dashboard makes no outside request: your data goes nowhere else.");
    expect(within(promise()).queryByRole("link")).toBeNull();
  });

  it("with it on, both say the vendor names go to the provider, and About links to the AI card", async () => {
    serve({ aiStatus: ON });
    await open("/settings");
    await waitFor(() => expect(line()).toBe("local-only · vendor names go to Anthropic for sorting"));
    expect(promise().textContent).toBe("The dashboard makes no outside request, but AI categorization is on: the names "
      + "of vendors your rules have not sorted go to Anthropic for sorting. Nothing else is sent.");
    fireEvent.click(within(promise()).getByRole("link", { name: "AI categorization" }));
    await waitFor(() => expect(scrolled).toHaveBeenCalledWith(SCROLL_TARGETS.aiCategorization));
    const target = screen.getByTestId("card-ai-categorization");
    expect(target.id).toBe(SCROLL_TARGETS.aiCategorization);
    expect(document.activeElement, "focus is on the card's heading").toBe(target.querySelector("h2"));
  });
});

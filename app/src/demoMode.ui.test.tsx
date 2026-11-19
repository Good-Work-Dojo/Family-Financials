// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AppData } from "./data/types";
import { appDataFactory, snapFactory } from "./lib/__fixtures__";
import { MANUAL_ASSETS_LOCAL_KEY } from "./lib/manualAssets";
import { emptyPrefs, type PrefsDoc } from "./lib/prefs";

// The demo build (lib/demo.ts), with the page's data as a household's exports would give it.
vi.mock("./lib/demo", () => ({ IS_DEMO: true, DEMO_BANNER_TEXT: "Demo: an invented household. Changes stay in this browser.",
  storageKey: (key: string) => `ff-demo:${key}` }));
const base = appDataFactory(snapFactory())();
let data: AppData = base;
const prefs = { doc: emptyPrefs() as PrefsDoc, localOnly: false, refused: null as number | null, setModule: vi.fn(),
  setAi: vi.fn() };
vi.mock("./data/DataContext", () => ({
  useData: () => ({ data, missing: new Set<string>(), prefs, indexes: { dataRange: null }, reload: async () => {} }),
}));

import DemoBanner, { demoBannerClosed } from "./components/DemoBanner";
import { RefreshModal, RefreshPanel, RefreshPill, RefreshProvider } from "./components/DataRefresh";
import { DEMO_NO_REFRESH } from "./data/useRefresh";
import { ManualAssetsDraftProvider } from "./data/useManualAssetsDraft";
import Settings from "./sections/Settings";
import { AI_DEMO } from "./sections/settings/AiCard";

/** every request the page made, and the family documents the site was built with (by file name beside the page) */
let asked: string[] = [];
let built: Record<string, unknown> = {};
/** the requests that are not a read of a family document the site was built with */
const sent = () => asked.filter((u) => !/^\.\/family\/[a-z-]+\.json$/.test(u));
beforeEach(() => {
  data = base;
  localStorage.clear();
  demoBannerClosed.resetMemory();
  asked = [];
  built = {};
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    asked.push(init?.method && init.method !== "GET" ? `${init.method} ${url}` : String(url));
    const name = String(url).replace(/^\.\//, "");
    return name in built ? new Response(JSON.stringify(built[name]), { status: 200 }) : new Response("{}", { status: 404 });
  });
});
afterEach(() => {
  // whatever the test did, this browser holds nothing but under the demo's prefix: a Pages site shares its origin
  const keys = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i) ?? "");
  expect(keys.filter((k) => !k.startsWith("ff-demo:")), "every stored key carries the demo's prefix").toEqual([]);
  cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks();
});

const settings = () => render(
  <MemoryRouter><RefreshProvider><ManualAssetsDraftProvider><Settings /></ManualAssetsDraftProvider></RefreshProvider>
  </MemoryRouter>);
const pause = () => act(async () => { await new Promise((r) => setTimeout(r, 30)); });

describe("the demo build hides what needs a server", () => {
  it("Settings: no Sign out button and no AI switches, each card saying why in plain words; nothing is asked of a server", async () => {
    settings();
    await pause();
    const signOut = screen.getByTestId("card-sign-out");
    expect(within(signOut).queryByRole("button"), "no Sign out button").toBeNull();
    expect(within(signOut).getByTestId("sign-out-demo").textContent).toMatch(/^The demo has no sign-in\./);
    const ai = screen.getByTestId("card-ai-categorization");
    expect(within(ai).queryByRole("button"), "no switch, no preview, no Forget").toBeNull();
    expect(within(ai).getByTestId("ai-demo").textContent).toBe(AI_DEMO);
    expect(within(ai).getByText(/sent to an AI service, which suggests a category/), "the card still explains it").toBeDefined();
    expect(sent()).toEqual([]);
  });

  it("Settings: an asset the household's store held when the site was built reads as saved, not as one the store lost", async () => {
    const home = { id: "family-home", name: "Home", owner: data.assumptions.owners[0], asset_class: "House", liquid: false,
      values: [{ as_of: "2026-01-01", value: 500000 }], updated_at: "2026-06-01T00:00:00.000Z" };
    data = { ...base, manualAssets: [{ ...home, institution: null, acquired: null, origin: "app",
      values: [{ as_of: "2026-01-01", value: 500000 }] }] };
    built["family/manual-assets.json"] = { version: 1, updated_at: "2026-06-01T00:00:00.000Z", assets: [home] };
    settings();
    await pause();
    const row = await screen.findByTestId("manual-asset-family-home");
    expect(row.textContent).toContain("entered in this dashboard");
    expect(row.textContent).not.toMatch(/no longer holds it/);
    expect(asked).toEqual(expect.arrayContaining(["./family/manual-assets.json"]));
    expect(sent()).toEqual([]);
  });

  it("Settings: a manual asset saved in the demo stays in this browser and the card says the demo is never rebuilt", async () => {
    settings();
    await pause();
    fireEvent.click(screen.getByRole("button", { name: "Add an asset" }));
    const box = document.activeElement as HTMLInputElement;
    const fields = within(box.closest("fieldset")!);
    fireEvent.change(box, { target: { value: "Family car" } });
    fireEvent.change(fields.getByLabelText("Owner"), { target: { value: data.assumptions.owners[0] } });
    fireEvent.change(fields.getByLabelText("Class"), { target: { value: "Car" } });
    const amount = fields.getByLabelText("Value 1");
    fireEvent.focus(amount);
    fireEvent.change(amount, { target: { value: "18000" } });
    fireEvent.blur(amount);
    fireEvent.click(within(screen.getByTestId("card-manual-assets")).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByTestId("manual-assets-status").textContent)
      .toBe("Saved in this browser. The demo is never rebuilt, so net worth does not count it."));
    const kept = JSON.parse(localStorage.getItem(MANUAL_ASSETS_LOCAL_KEY) ?? "null");
    expect(kept.assets.map((a: { name: string }) => a.name)).toEqual(["Family car"]);
    expect(sent(), "the save is sent nowhere").toEqual([]);
  });

  it("the refresh: no pill, no dialog, and the Data page's panel is one line", async () => {
    render(<MemoryRouter><RefreshProvider>
      <div data-testid="pill"><RefreshPill onClick={() => {}} /></div>
      <RefreshModal open onOpenChange={() => {}} />
      <RefreshPanel />
    </RefreshProvider></MemoryRouter>);
    await pause();
    expect(screen.getByTestId("pill").childElementCount, "no pill").toBe(0);
    expect(screen.queryByRole("dialog"), "no refresh dialog").toBeNull();
    expect(screen.getByTestId("refresh-demo").textContent).toBe(DEMO_NO_REFRESH);
    expect(screen.queryByRole("button"), "nothing to press").toBeNull();
    expect(asked).toEqual([]);                                     // the refresh reads nothing at all
  });
});

describe("the demo banner", () => {
  it("says what the site is as a note above the page, and once closed stays closed in this browser", () => {
    const first = render(<DemoBanner />);
    const note = screen.getByRole("note", { name: "About this demo" });
    expect(within(note).getByText("Demo: an invented household. Changes stay in this browser.")).toBeDefined();
    fireEvent.click(within(note).getByRole("button", { name: "Close the demo notice" }));
    expect(screen.queryByRole("note")).toBeNull();
    first.unmount();
    render(<DemoBanner />);
    expect(screen.queryByRole("note"), "a later page in this browser shows it no more").toBeNull();
  });

  it("closes for the page view when the browser keeps no storage, and never throws", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("SecurityError"); });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("SecurityError"); });
    render(<DemoBanner />);
    fireEvent.click(screen.getByRole("button", { name: "Close the demo notice" }));
    expect(screen.queryByRole("note")).toBeNull();
  });
});

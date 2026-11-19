// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { Transaction } from "../data/types";
import { SHIPPED_RESERVED } from "../lib/categories";
import { emptyPrefs } from "../lib/prefs";
import { txFactory } from "../lib/__fixtures__";
import Transactions from "./Transactions";
import { Where, whereSearch } from "../test/routerProbe";

// The real FilterBar and the real register over a stand-in store that behaves like the answers overlay: a tags-only bulk answer
// lands on the rows, and the tag list is what the rows carry (DataContext's indexes.tagList). Nothing here is mocked between the
// UI controls and the rows, so a control that is not wired to the URL, or a chip that is not drawn, fails.
const store = vi.hoisted(() => ({ txns: [] as Transaction[], answerTxns: undefined as unknown as (ids: string[], set: { tags?: string[] }) => void }));
vi.mock("../data/DataContext", () => ({
  useData: () => ({
    data: { transactions: store.txns, purchases: store.txns, accounts: [], splitParents: new Map(),
      manifest: { generated_at: "2026-01-31T20:00:00-05:00" }, cashflow: [],
      assumptions: { taxonomy: { Dining: ["Coffee"] }, owners: ["Joint"], category_renames: [], reserved: SHIPPED_RESERVED },
      vendors: [] },
    indexes: { dataRange: { first: "2025-01-01", last: "2026-01-31" }, dataThrough: "2026-01-31", vendorList: [], categoryList: ["Dining"],
      childrenByParent: new Map(),
      tagList: [...new Set(store.txns.flatMap((t) => t.tags))].sort() },
    missing: new Set<string>(), overlay: { pendingIds: new Set<string>(), conflictedKeys: new Set<string>() },
    prefs: { doc: emptyPrefs(), saveFilter: () => true },
    answers: { doc: { answers: [] }, answerTxns: (ids: string[], set: { tags?: string[] }) => store.answerTxns(ids, set),
               answerTxn: () => {}, answerVendor: () => {} },
  }),
}));
vi.mock("@tanstack/react-virtual", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-virtual")>()),
  useVirtualizer: (opts: { count: number }) => ({
    getTotalSize: () => opts.count * 40,
    getVirtualItems: () => Array.from({ length: opts.count }, (_, index) => ({ index, start: index * 40, key: index })),
    measureElement: () => {}, scrollToIndex: () => {},
  }),
}));

beforeAll(() => {
  (globalThis as any).ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  vi.stubGlobal("matchMedia", (query: string) => ({      // a 1280px desktop: the full grid, the full filter bar
    get matches() { const m = /max-width: (\d+)px/.exec(query); return !!m && 1280 <= Number(m[1]); },
    media: query, addEventListener() {}, removeEventListener() {},
  }));
});
afterAll(() => { vi.unstubAllGlobals(); });

const tx = txFactory({ type: "purchase", category: "Dining", subcategory: "Coffee" });
beforeEach(() => {
  store.txns = [
    tx({ id: "a", date: "2026-01-03", vendor: "Alpha", amount: -30 }),
    tx({ id: "b", date: "2026-01-02", vendor: "Bravo", amount: -10 }),
    tx({ id: "c", date: "2026-01-01", vendor: "Charlie", amount: -20 }),
  ];
  // the overlay: a tags-only answer adds its tags to those rows (new objects, as the overlay makes them)
  store.answerTxns = (ids, set) => {
    store.txns = store.txns.map((t) => (ids.includes(t.id) ? { ...t, tags: [...new Set([...t.tags, ...(set.tags ?? [])])] } : t));
  };
});
afterEach(cleanup);

// `from=` is a filter key, so this is not a fresh visit and the 6-month default stays out of the way
const mount = (search = "") => render(
  <MemoryRouter initialEntries={[`/spending/transactions?from=2020-01-01${search}`]}><Transactions /><Where /></MemoryRouter>);
const search = whereSearch;
const vendors = () => screen.getAllByRole("row").filter((r) => r.hasAttribute("aria-expanded"))
  .map((r) => /Alpha|Bravo|Charlie/.exec(r.textContent ?? "")![0]).sort();
const box = (vendor: string) => screen.getByRole("checkbox", { name: new RegExp(`^Select ${vendor},`) });

describe("tags: add one in the register, then find the rows again through the filter bar", () => {
  it("round trip: tag two rows, see 'Tags' appear, pick the tag, see only those rows with their chips, clear it", () => {
    mount();
    expect(screen.queryByRole("button", { name: /^Tags/ }), "nothing is tagged yet: nothing to filter by").toBeNull();
    // tag Alpha and Charlie through the bulk editor
    fireEvent.click(box("Alpha"));
    fireEvent.click(box("Charlie"));
    fireEvent.click(screen.getByRole("button", { name: /Categorize or tag/ }));
    const input = screen.getByPlaceholderText("add tag…");
    fireEvent.change(input, { target: { value: "trip" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    // the save said so (a live region, so a screen reader hears it) and the filter bar now offers the tag
    const status = screen.getByTestId("bulk-saved");
    expect(status.getAttribute("role")).toBe("status");
    expect(status.textContent).toBe("Saved 2 answers — permanent at the next ingest");
    expect(vendors()).toEqual(["Alpha", "Bravo", "Charlie"]);
    const rowOf = (v: string) => screen.getAllByRole("row").find((r) => new RegExp(v).test(r.textContent ?? ""))!;
    expect(within(rowOf("Alpha")).getByText("trip").textContent, "a chip on the tagged row").toBe("tag trip");
    expect(within(rowOf("Charlie")).getByText("trip")).toBeDefined();
    expect(within(rowOf("Bravo")).queryByText("trip"), "none on the untagged one").toBeNull();
    // pick it: the URL carries it, the register narrows to the tagged rows, the bar shows it as a chip
    fireEvent.click(screen.getByRole("button", { name: /^Tags/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: "trip" }));
    expect(search().get("tags")).toBe("trip");
    expect(search().get("from"), "the other filters stay").toBe("2020-01-01");
    expect(vendors()).toEqual(["Alpha", "Charlie"]);
    expect(screen.getByRole("button", { name: /^Tags · 1/ })).toBeDefined();
    fireEvent.click(within(screen.getByText("tag: trip").closest("span")!).getByRole("button"));
    expect(search().has("tags")).toBe(false);
    expect(vendors()).toEqual(["Alpha", "Bravo", "Charlie"]);
  });

  it("a link that names a tag (?tags=) opens with those rows and the control showing the selection", () => {
    store.txns = store.txns.map((t) => (t.id === "b" ? { ...t, tags: ["kids"] } : t));
    mount("&tags=kids");
    expect(vendors()).toEqual(["Bravo"]);
    expect(screen.getByRole("button", { name: /^Tags · 1/ })).toBeDefined();
  });

  it("with the filters narrowed to nothing the register says so and 'Clear all filters' brings the rows back", () => {
    mount("&vendors=Nobody");
    expect(vendors()).toEqual([]);
    expect(screen.getByTestId("register-empty").textContent).toContain("No transactions match these filters.");
    expect((screen.getByRole("button", { name: /Export CSV: 0 rows$/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Clear all filters" }));
    expect(search().has("vendors")).toBe(false);
    expect(search().has("from"), "clear is all of them").toBe(false);
    expect(vendors()).toEqual(["Alpha", "Bravo", "Charlie"]);
    expect(screen.queryByTestId("register-empty")).toBeNull();
  });

  it("the bulk-save status is a live region that is in the page before the save, and empty until then", () => {
    mount();
    const status = screen.getByTestId("bulk-saved");
    expect(status.getAttribute("role")).toBe("status");
    expect(status.textContent).toBe("");
  });
});

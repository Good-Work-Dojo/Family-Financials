// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { presetRange } from "../filters/dateRange";
import { applyFilters, EMPTY_FILTERS, type Filters, serializeFilters } from "../filters/filters";
import { txFactory } from "../lib/__fixtures__";
import { uuid4 } from "../lib/ids";
import { MASK, setPrivate } from "../lib/privacy";
import { emptyPrefs, MAX_SAVED_ENTRY_CHARS, type SavedFilter, tryWithSaved, withSaved } from "../lib/prefs";

// the save preview must not read the clock (it renders on every keystroke): any read of the app's one clock
// (lib/clock.ts) while the box renders is a failure (see "the save preview reads no clock")
const reads = vi.hoisted(() => ({ calls: 0 }));
vi.mock("../lib/clock", async (orig) => {
  const { clock } = await orig<typeof import("../lib/clock")>();
  const counted = <A extends unknown[], R>(read: (...a: A) => R) => (...a: A): R => { reads.calls++; return read(...a); };
  return { clock: { now: counted(clock.now), nowISO: counted(clock.nowISO), today: counted(clock.today) } };
});

let owners: string[] = [];
let txnOwners: string[] = [];
// each transaction's category source, type and split parent, by position (a keyword-filed purchase of its own unless set)
let txnSources: string[] = [];
let txnTypes: string[] = [];
let txnParents: (string | null)[] = [];
let purchasesList: unknown[] = [];
let accountList: { id: string; name: string }[] = [];
let tagList: string[] = [];
let savedFilters: SavedFilter[] = [];
// the data's date range; null for a household with no transaction yet
let dataRange: { first: string; last: string } | null = { first: "2025-01-01", last: "2026-06-30" };
// the data's last day never past the ingest day (indexes.dataThrough): the range's last unless a test dates a row ahead
let through: string | null = null;
// manifest.generated_at: the ingest ran on Jun 30, 2026 unless a test moves it
const JUNE_30 = "2026-06-30T20:00:00-04:00";
let generatedAt = JUNE_30;
const saveFilter = vi.fn((_name: string, _query: string) => true);
vi.mock("../data/DataContext", () => ({
  useData: () => ({
    // the ingest ran on Jun 30, 2026, the data's last day: June is the month in progress
    data: {
      transactions: txnOwners.map((owner, i) => ({ owner, date: "2026-06-30", type: txnTypes[i] ?? "purchase",
        category_source: txnSources[i] ?? "keyword", parent_id: txnParents[i] ?? null })),
      purchases: purchasesList,
      accounts: accountList,
      assumptions: { owners, category_renames: [] },
      cashflow: [], manifest: { generated_at: generatedAt },
    },
    indexes: { dataRange, dataThrough: through ?? dataRange?.last ?? null, vendorList: [], categoryList: ["Dining", "Groceries"], tagList },
    missing: new Set<string>(),
    prefs: { doc: { ...emptyPrefs(), saved_filters: savedFilters }, saveFilter },
  }),
}));

import FilterBar from "./FilterBar";
import { Where, where } from "../test/routerProbe";

beforeEach(() => {
  savedFilters = []; accountList = []; tagList = []; saveFilter.mockClear(); saveFilter.mockReturnValue(true);
  txnSources = []; txnTypes = []; txnParents = [];
});
afterEach(() => { cleanup(); setPrivate(false); });

function ownerOptions(): string[] {
  render(<FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={[]} />);
  fireEvent.click(screen.getByText(/^Owner/));
  // the dropdown lists one checkbox label per option, in order
  return screen.getAllByRole("checkbox")
    .map((c) => c.closest("label")?.textContent ?? "")
    .filter((t) => owners.includes(t));
}

describe("FilterBar owner facet", () => {
  it("offers assumptions.owners that appear on transactions, in assumptions order", () => {
    owners = ["Pat", "Sam", "Joint", "Kid"];
    txnOwners = ["Joint", "Pat", "Pat", "Sam"];   // no transaction belongs to Kid
    expect(ownerOptions()).toEqual(["Pat", "Sam", "Joint"]);
  });

  it("offers the one owner of a household with one adult and a joint label", () => {
    owners = ["Alex", "Joint"];
    txnOwners = ["Alex"];
    expect(ownerOptions()).toEqual(["Alex"]);
  });
});

describe("FilterBar sources facet", () => {
  const options = () => {
    fireEvent.click(screen.getByRole("button", { name: /^Filed by/ }));
    return screen.getAllByRole("checkbox").map((c) => c.closest("label")?.textContent ?? "").filter((t) => /\(\d+\)$/.test(t));
  };
  beforeEach(() => {
    owners = ["Joint"];
    // two AI rows, a split purchase's two parts (one transaction), a fallback row, and a hidden transfer filed by AI's
    // group would never be: a transfer is internal, filed by the household's rules
    txnOwners = ["Joint", "Joint", "Joint", "Joint", "Joint", "Joint"];
    txnSources = ["ai", "ai", "split", "split", "fallback", "internal"];
    txnParents = [null, null, "p1", "p1", null, null];
    txnTypes = ["purchase", "purchase", "purchase", "purchase", "purchase", "transfer"];
  });
  it("offers the groups the data holds, in the facet's order, each with its count of transactions", () => {
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={[]} /></MemoryRouter>);
    expect(options()).toEqual(["Your rules (1)", "AI (2)", "Not yet sorted (1)"]);
  });
  it("counts the transfers too while they are shown", () => {
    render(<MemoryRouter><FilterBar filters={{ ...EMPTY_FILTERS, includeInternal: true }} update={() => {}} clear={() => {}}
      filtered={[]} /></MemoryRouter>);
    expect(options()).toEqual(["Your rules (2)", "AI (2)", "Not yet sorted (1)"]);
  });
  it("a pick writes the group's id; a chosen group shows as a removable chip, and one the data lacks stays offered", () => {
    const update = vi.fn();
    render(<MemoryRouter><FilterBar filters={{ ...EMPTY_FILTERS, sources: ["overrides"] }} update={update} clear={() => {}}
      filtered={[]} /></MemoryRouter>);
    expect(options()).toEqual(["Your rules (1)", "Overrides (0)", "AI (2)", "Not yet sorted (1)"]);
    fireEvent.click(screen.getByRole("checkbox", { name: "AI (2)" }));
    expect(update).toHaveBeenLastCalledWith({ sources: ["overrides", "ai"] });
    expect(screen.getByText("filed by: Overrides")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Remove the filter filed by: Overrides" }));
    expect(update).toHaveBeenLastCalledWith({ sources: [] });
  });
});

// The window Spending opens on is six months (useSpendingDefaults), and it used to match no chip: nothing was highlighted on arrival.
describe("FilterBar period chips", () => {
  // the period chips are the bar's first five buttons (the direction toggle has an "All" of its own)
  const chips = () => screen.getAllByRole("button").slice(0, 5);
  const active = (b: HTMLElement) => b.className.includes("bg-zinc-800");
  it("offers 3M, 6M, 12M, YTD and All, in the shared words", () => {
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={[]} /></MemoryRouter>);
    expect(chips().map((b) => b.textContent)).toEqual(["3M", "6M", "12M", "YTD", "All"]);
  });
  it("the default six-month window lights the 6M chip on arrival, and no other", () => {
    const from = presetRange("6m", "2026-06-30").from;
    render(<MemoryRouter><FilterBar filters={{ ...EMPTY_FILTERS, from }} update={() => {}} clear={() => {}} filtered={[]} /></MemoryRouter>);
    expect(chips().map(active)).toEqual([false, true, false, false, false]);
  });
  it("a cleared window is All, a twelve-month one is 12M, and a custom one lights nothing", () => {
    const { rerender } = render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={[]} /></MemoryRouter>);
    expect(chips().map(active)).toEqual([false, false, false, false, true]);
    rerender(<MemoryRouter><FilterBar filters={{ ...EMPTY_FILTERS, from: presetRange("12m", "2026-06-30").from }} update={() => {}} clear={() => {}} filtered={[]} /></MemoryRouter>);
    expect(chips().map(active)).toEqual([false, false, true, false, false]);
    rerender(<MemoryRouter><FilterBar filters={{ ...EMPTY_FILTERS, from: "2025-11-03" }} update={() => {}} clear={() => {}} filtered={[]} /></MemoryRouter>);
    expect(chips().some(active)).toBe(false);
  });
  it("clicking 6M asks for the same window the default opens on", () => {
    const update = vi.fn();
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={update} clear={() => {}} filtered={[]} /></MemoryRouter>);
    fireEvent.click(chips()[1]);
    expect(chips()[1].textContent).toBe("6M");
    expect(update).toHaveBeenCalledWith({ from: presetRange("6m", "2026-06-30").from, to: undefined });
  });
});

// A household with no transaction yet has no newest date to count a window back from: the chips that count back cannot
// be resolved, so they are offered disabled with a title that says why and a click on one asks for nothing. All needs no
// anchor: it stays enabled, lit while no window is set, and drops a window that arrived in the URL.
describe("FilterBar period chips with no transaction yet", () => {
  const chips = () => screen.getAllByRole("button").slice(0, 5);
  const lit = (b: HTMLElement) => b.className.includes("bg-zinc-800");
  beforeEach(() => { dataRange = null; });
  afterEach(() => { dataRange = { first: "2025-01-01", last: "2026-06-30" }; vi.unstubAllGlobals(); });
  it("every chip but All is disabled and says why; All is enabled and lit with no window set", () => {
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={[]} /></MemoryRouter>);
    expect(chips().map((b) => b.textContent)).toEqual(["3M", "6M", "12M", "YTD", "All"]);
    for (const b of chips().slice(0, 4)) {
      expect((b as HTMLButtonElement).disabled, b.textContent!).toBe(true);
      expect(b.title, b.textContent!).toBe("No transactions yet");
      expect(lit(b), b.textContent!).toBe(false);
    }
    const all = chips()[4] as HTMLButtonElement;
    expect([all.disabled, all.title, lit(all)]).toEqual([false, "", true]);
  });
  it("a click on a chip that counts back asks for nothing; All asks for no window", () => {
    const update = vi.fn();
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={update} clear={() => {}} filtered={[]} /></MemoryRouter>);
    for (const b of chips().slice(0, 4)) fireEvent.click(b);
    expect(update).not.toHaveBeenCalled();
    fireEvent.click(chips()[4]);
    expect(update).toHaveBeenCalledWith({ from: undefined, to: undefined });
  });
  it("a window that arrived in the URL leaves All unlit, and All drops it", () => {
    const update = vi.fn();
    render(<MemoryRouter><FilterBar filters={{ ...EMPTY_FILTERS, from: "2026-01-05" }} update={update} clear={() => {}}
      filtered={[]} /></MemoryRouter>);
    expect(lit(chips()[4])).toBe(false);
    fireEvent.click(chips()[4]);
    expect(update).toHaveBeenCalledWith({ from: undefined, to: undefined });
  });
  it("the phone's summary says All, never a date it cannot know", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("max-width: 767px"), media: query, addEventListener: () => {}, removeEventListener: () => {} }));
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={[]} /></MemoryRouter>);
    expect(screen.getByTestId("filterbar-toggle").textContent).toContain("All · no filters");
    expect(screen.getByTestId("filterbar-toggle").textContent).not.toMatch(/Invalid|NaN/);
  });
});

const view = (id: string, name: string, query: string): SavedFilter => ({ id, name, query, created_at: "2026-01-01T00:00:00Z" });
// a start date that is no preset's (YTD would be saved as a rolling `win=ytd`)
const ACTIVE: Filters = { ...EMPTY_FILTERS, categories: ["Dining"], from: "2025-11-03" };
const bar = (filters: Filters = ACTIVE) => (
  <MemoryRouter><FilterBar filters={filters} update={() => {}} clear={() => {}} filtered={[]} /></MemoryRouter>
);

describe("FilterBar save view", () => {
  it("offers Save view only while a filter is active", () => {
    const { rerender } = render(bar(EMPTY_FILTERS));
    expect(screen.queryByTestId("filterbar-save-view")).toBeNull();
    rerender(bar());
    expect(screen.getByTestId("filterbar-save-view")).toBeDefined();
  });

  it("saves the current filters' query under the typed name, then says so", () => {
    render(bar());
    fireEvent.click(screen.getByTestId("filterbar-save-view"));
    fireEvent.change(screen.getByLabelText("View name"), { target: { value: "Dining this year" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(saveFilter).toHaveBeenCalledTimes(1);
    expect(saveFilter).toHaveBeenCalledWith("Dining this year", "categories=Dining&from=2025-11-03");   // dates the user picked stay dates
    expect(screen.getByRole("status").textContent).toContain("saved");
    expect(screen.queryByLabelText("View name")).toBeNull();            // back to the link
    expect(screen.getByTestId("filterbar-save-view")).toBeDefined();
  });

  it("a blank name cannot be saved, and cancel (or Escape) closes the box without saving", () => {
    render(bar());
    fireEvent.click(screen.getByTestId("filterbar-save-view"));
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("View name"), { target: { value: "   " } });
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "cancel" }));
    expect(screen.queryByLabelText("View name")).toBeNull();
    fireEvent.click(screen.getByTestId("filterbar-save-view"));
    fireEvent.keyDown(screen.getByLabelText("View name"), { key: "Escape" });
    expect(screen.queryByLabelText("View name")).toBeNull();
    expect(saveFilter).not.toHaveBeenCalled();
  });

  it("the name box is a 16px phone input with a length limit", () => {
    render(bar());
    fireEvent.click(screen.getByTestId("filterbar-save-view"));
    const input = screen.getByLabelText("View name") as HTMLInputElement;
    expect(input.className).toContain("text-base sm:text-xs");
    expect(input.maxLength).toBe(80);
  });

  it("at the 50-view cap it says so and will not save", () => {
    savedFilters = Array.from({ length: 50 }, (_, i) => view(`v${i}`, `v${i}`, ""));
    render(bar());
    fireEvent.click(screen.getByTestId("filterbar-save-view"));
    fireEvent.change(screen.getByLabelText("View name"), { target: { value: "one more" } });
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/50 views saved — delete one on Data first/)).toBeDefined();
  });

  it("the save preview reads no clock: it measures a constant stamp as long as a real one", () => {
    reads.calls = 0;
    render(bar());
    fireEvent.click(screen.getByTestId("filterbar-save-view"));
    fireEvent.change(screen.getByLabelText("View name"), { target: { value: "a name" } });
    fireEvent.change(screen.getByLabelText("View name"), { target: { value: "a longer name" } });
    expect(reads.calls, "the clock is not read while the box renders").toBe(0);
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("filters too long to store say so as soon as the box opens, and cannot be saved", () => {
    render(bar({ ...ACTIVE, q: "x".repeat(MAX_SAVED_ENTRY_CHARS) }));
    fireEvent.click(screen.getByTestId("filterbar-save-view"));
    fireEvent.change(screen.getByLabelText("View name"), { target: { value: "huge" } });
    expect(screen.getByRole("alert").textContent).toContain("too long");
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    expect(saveFilter).not.toHaveBeenCalled();
  });

  it("the pre-check agrees with the real save at the byte boundary: an id as long as a uuid, not a short placeholder", () => {
    // the longest q a REAL save (a fresh uuid id) accepts, found with the same op the store runs
    const real = (n: number) => typeof tryWithSaved(emptyPrefs(), "saved_filters",
      { id: uuid4(), name: "x", query: serializeFilters({ ...ACTIVE, q: "x".repeat(n) }).toString() }, new Date().toISOString()) !== "string";
    let n = 1500;                              // comfortably under the 2 KB entry cap
    while (real(n + 1)) n++;
    expect(real(n) && !real(n + 1), "found the boundary").toBe(true);
    const box = (q: string) => {
      render(bar({ ...ACTIVE, q }));
      fireEvent.click(screen.getByTestId("filterbar-save-view"));
      fireEvent.change(screen.getByLabelText("View name"), { target: { value: "x" } });
    };
    box("x".repeat(n));
    expect(screen.queryByRole("alert"), "the longest entry the real save takes is not refused in advance").toBeNull();
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(false);
    cleanup();
    box("x".repeat(n + 1));
    expect(screen.getByRole("alert").textContent, "one more character is refused in advance, as the real save would").toContain("too long");
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("a full prefs doc says 'Settings are full' (not the view count), names the way out, and cannot be saved", () => {
    let doc = emptyPrefs();
    for (let i = 0; i < 50; i++) {
      const next = withSaved(doc, "saved_filters", { id: `b${i}`, name: `b${i}`, query: "q=" + "x".repeat(1500) }, "2026-01-01T00:00:00Z");
      if (!next) break;
      doc = next;
    }
    expect(doc.saved_filters.length, "the doc budget, not the 50-view cap, is what stops it").toBeLessThan(50);
    // top it up with the smallest views that fit, so nothing more does (an entry is refused only when IT does not fit)
    for (let i = 0; i < 200; i++) {
      const next = withSaved(doc, "saved_filters", { id: `s${i}`, name: "s", query: "" }, "2026-01-01T00:00:00Z");
      if (!next) break;
      doc = next;
    }
    expect(doc.saved_filters.length).toBeLessThan(50);
    savedFilters = doc.saved_filters;
    render(bar());
    fireEvent.click(screen.getByTestId("filterbar-save-view"));
    fireEvent.change(screen.getByLabelText("View name"), { target: { value: "one more" } });
    expect(screen.getByRole("alert").textContent).toBe("Settings are full — delete a view on Data first");
    expect(screen.queryByText(/views saved/)).toBeNull();
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("a save the store refuses after the fact (the list changed under us) keeps the box open and says so", () => {
    saveFilter.mockReturnValue(false);
    render(bar());
    fireEvent.click(screen.getByTestId("filterbar-save-view"));
    fireEvent.change(screen.getByLabelText("View name"), { target: { value: "raced" } });
    fireEvent.submit(screen.getByLabelText("View name").closest("form")!);
    expect(screen.getByRole("alert").textContent).toContain("couldn't save");
    expect(screen.queryByRole("status") === null, "never claims it saved").toBe(true);
    expect(screen.getByLabelText("View name")).toBeDefined();
  });
});


describe("FilterBar saved views menu", () => {
  it("is absent until the family has saved something", () => {
    render(bar());
    expect(screen.queryByTestId("filterbar-views")).toBeNull();
  });

  it("lists the valid views and not a stale one, and counts the stale ones with a link to Data", () => {
    savedFilters = [view("a", "Coffee runs", "categories=Dining"), view("s", "Gone", "categories=Nightlife"),
                    view("t", "Odd", "bogus=1")];
    render(bar());
    const button = screen.getByTestId("filterbar-views");
    expect(button.textContent).toContain("1");                            // one valid view
    expect(screen.queryByRole("list", { name: "Saved views" })).toBeNull();   // closed until opened
    fireEvent.click(button);
    const list = screen.getByRole("list", { name: "Saved views" });
    expect(within(list).getByRole("link", { name: "Coffee runs" })).toBeDefined();
    expect(within(list).queryByText("Gone")).toBeNull();
    expect(within(list).getByRole("link", { name: /2 stale views — see Data/ }).getAttribute("href")).toBe("/data");
  });

  it("opens a view on the Transactions tab with its query and the carried-search marker", () => {
    savedFilters = [view("a", "Coffee runs", "categories=Dining")];
    render(<MemoryRouter><FilterBar filters={ACTIVE} update={() => {}} clear={() => {}} filtered={[]} /><Where /></MemoryRouter>);
    fireEvent.click(screen.getByTestId("filterbar-views"));
    fireEvent.click(screen.getByRole("link", { name: "Coffee runs" }));
    expect(where()).toBe(
      '/spending/transactions?categories=Dining|{"carriedSearch":true}');
    expect(screen.queryByRole("list", { name: "Saved views" })).toBeNull();   // the menu closes on a pick
  });

  it("Escape closes the menu", () => {
    savedFilters = [view("a", "Coffee runs", "categories=Dining")];
    render(bar());
    fireEvent.click(screen.getByTestId("filterbar-views"));
    fireEvent.keyDown(screen.getByRole("link", { name: "Coffee runs" }), { key: "Escape" });
    expect(screen.queryByRole("list", { name: "Saved views" })).toBeNull();
  });

  it("anchors to the filter bar's row, not to its own button: left edge and width follow the row (never past a phone's edge)", () => {
    savedFilters = [view("a", "Coffee runs", "categories=Dining")];
    render(bar());
    fireEvent.click(screen.getByTestId("filterbar-views"));
    const list = screen.getByRole("list", { name: "Saved views" });
    // the nearest positioned ancestor is the row that also holds the count and the Save view link: a
    // `relative` on the menu's own wrapper (the old structure) would make the left edge the button's
    const anchor = list.closest(".relative")!;
    expect(anchor.contains(screen.getByTestId("filterbar-save-view")), "the anchor is the whole row").toBe(true);
    expect(anchor.textContent).toMatch(/\d+ of [\d,]+ transactions?/);
    expect(list.parentElement!.className, "the menu's own wrapper is not the anchor").not.toContain("relative");
    expect(list.className).toContain("left-0");
    expect(list.className).toContain("w-[min(18rem,100%)]");
    expect(list.className, "no viewport-relative width that ignores the offset").not.toContain("100vw");
  });

  it("is a disclosure, not an ARIA menu: aria-expanded follows it, aria-haspopup is not claimed", () => {
    savedFilters = [view("a", "Coffee runs", "categories=Dining")];
    render(bar());
    const button = screen.getByTestId("filterbar-views");
    expect(button.hasAttribute("aria-haspopup")).toBe(false);
    expect(button.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(button.getAttribute("aria-controls")).toBe(screen.getByRole("list", { name: "Saved views" }).id);
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("false");
  });

  it("closes when focus moves outside it, stays open while focus moves inside it", () => {
    savedFilters = [view("a", "Coffee runs", "categories=Dining"), view("b", "Tacos", "q=taco")];
    render(<div><button type="button">elsewhere</button>{bar()}</div>);
    fireEvent.click(screen.getByTestId("filterbar-views"));
    act(() => screen.getByRole("link", { name: "Coffee runs" }).focus());
    act(() => screen.getByRole("link", { name: "Tacos" }).focus());
    expect(screen.queryByRole("list", { name: "Saved views" }), "focus inside keeps it open").not.toBeNull();
    act(() => screen.getByRole("button", { name: "elsewhere" }).focus());
    expect(screen.queryByRole("list", { name: "Saved views" }), "focus outside closes it").toBeNull();
  });

  it("Escape from anywhere inside closes it and hands focus back to the button", () => {
    savedFilters = [view("a", "Coffee runs", "categories=Dining")];
    render(bar());
    const button = screen.getByTestId("filterbar-views");
    fireEvent.click(button);
    const link = screen.getByRole("link", { name: "Coffee runs" });
    link.focus();
    fireEvent.keyDown(link, { key: "Escape" });
    expect(screen.queryByRole("list", { name: "Saved views" })).toBeNull();
    expect(document.activeElement).toBe(button);
    fireEvent.click(button);
    fireEvent.keyDown(screen.getByRole("list", { name: "Saved views" }), { key: "Escape" });   // the list itself too
    expect(screen.queryByRole("list", { name: "Saved views" })).toBeNull();
  });

  it("a click outside closes it", () => {
    savedFilters = [view("a", "Coffee runs", "categories=Dining")];
    render(bar());
    fireEvent.click(screen.getByTestId("filterbar-views"));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("list", { name: "Saved views" })).toBeNull();
  });

  it("a view saved with no date window opens all-time: an empty-query view still carries the marker", () => {
    savedFilters = [view("all", "Everything", "")];
    render(<MemoryRouter><FilterBar filters={ACTIVE} update={() => {}} clear={() => {}} filtered={[]} /><Where /></MemoryRouter>);
    fireEvent.click(screen.getByTestId("filterbar-views"));
    fireEvent.click(screen.getByRole("link", { name: "Everything" }));
    expect(where()).toBe('/spending/transactions|{"carriedSearch":true}');
  });
});

// privacy mode: an amount filter's bounds are amounts; an empty box has nothing to hide and keeps its "min $" hint
describe("FilterBar amount bounds under privacy mode", () => {
  const withAmounts = (f: Partial<Filters>) => render(<MemoryRouter><FilterBar filters={{ ...EMPTY_FILTERS, ...f }} update={() => {}} clear={() => {}} filtered={[]} /></MemoryRouter>);
  it("masks a set bound at rest (real on focus) and leaves an empty one as it is", () => {
    setPrivate(true);
    withAmounts({ amountMin: 500 });
    expect((screen.getByDisplayValue(MASK) as HTMLInputElement).readOnly).toBe(true);
    expect(screen.queryByDisplayValue("500")).toBeNull();
    expect((screen.getByPlaceholderText("max $") as HTMLInputElement).value).toBe("");
    fireEvent.focus(screen.getByDisplayValue(MASK));
    expect(screen.getByDisplayValue("500")).toBeDefined();
  });
  it("keeps the two bounds apart while masked: each box is named Minimum / Maximum amount", () => {
    setPrivate(true);
    withAmounts({ amountMin: 500, amountMax: 900 });
    const min = screen.getByLabelText("Minimum amount") as HTMLInputElement;
    const max = screen.getByLabelText("Maximum amount") as HTMLInputElement;
    expect([min.value, max.value]).toEqual([MASK, MASK]);
    expect(min).not.toBe(max);
    expect(min.title).toMatch(/^min \$/);
    expect(max.title).toMatch(/^max \$/);
  });
  it("shows the bound with privacy off", () => {
    withAmounts({ amountMin: 500 });
    expect(screen.getByDisplayValue("500")).toBeDefined();
  });
});

// The line counts transactions of every type the filters pass - the sidebar prints the same total under the same word -
// with a split purchase counted once however many of its parts pass.
describe("the count line counts transactions, a split purchase once", () => {
  const tx = txFactory({ vendor: "Synth Mart" });
  // a -300 split (-210 + -90) and an ordinary -40 purchase; the filter keeps every part
  const filtered = [tx({ id: "a", amount: -210, parent_id: "p" }), tx({ id: "b", amount: -90, parent_id: "p" }), tx({ id: "o", amount: -40 })];
  afterEach(() => { purchasesList = []; });
  it("a split purchase counts once in N and once in M, and the line calls them transactions; the spend is the parts'", () => {
    purchasesList = [tx({ id: "p", is_parent: true, amount: -300 }), tx({ id: "o", amount: -40 })];
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={filtered} /></MemoryRouter>);
    expect(screen.getByText(/2 of 2 transactions · spend \$340/)).toBeDefined();
    expect(screen.queryByText(/purchases/), "income and transfers are counted too: the line never calls them purchases").toBeNull();
  });
  // a purchase split three ways with every part passing the filter: three rows of the category view, ONE transaction
  it("a 3-part split reads \"1 of 1 transaction\" (not 3 of 1): N and M count the purchase once however many parts pass", () => {
    const parts = [tx({ id: "a", amount: -50, parent_id: "p" }), tx({ id: "b", amount: -30, parent_id: "p" }), tx({ id: "c", amount: -20, parent_id: "p" })];
    purchasesList = [tx({ id: "p", is_parent: true, amount: -100 })];
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={parts} /></MemoryRouter>);
    expect(screen.getByText(/1 of 1 transaction · spend \$100/)).toBeDefined();
    cleanup();
    // two of the three parts pass a category filter: still one transaction, never "2 of 1"
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={parts.slice(0, 2)} /></MemoryRouter>);
    expect(screen.getByText(/1 of 1 transaction · spend \$80/)).toBeDefined();
  });
  // the spend counts the day of the last ingest, whose rows are still coming in; Home's "this month so far" stops the day
  // before it: the line says where the spend reaches, so the two figures read as what they are
  it("a spend that reaches the ingest day says so; one that stops before it says nothing more", () => {
    const rows = [tx({ id: "m", date: "2026-06-10", amount: -100 }), tx({ id: "t", date: "2026-06-30", amount: -40 })];
    purchasesList = rows;
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={rows} /></MemoryRouter>);
    expect(screen.getByTestId("filterbar-ingest-day").textContent).toBe(" through Jun 30, 2026, a day still coming in");
    cleanup();
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={rows.slice(0, 1)} /></MemoryRouter>);
    expect(screen.queryByTestId("filterbar-ingest-day")).toBeNull();
  });
  // the spend is what the charts and reports under the bar total: the rows up to the month in progress. A row the bank
  // dated into a month that has not begun is in the selection (counted, and listed in the register) but in no total.
  it("a row dated into a month not begun is counted in the selection, not in the spend, and the line says how many there are", () => {
    const rows = [tx({ id: "m", date: "2026-06-10", amount: -100 }), tx({ id: "n", date: "2026-06-20", amount: -40 })];
    const ahead = tx({ id: "x", date: "2026-07-02", amount: -999 });         // the ingest ran on Jun 30
    purchasesList = [...rows, ahead];
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={[...rows, ahead]} /></MemoryRouter>);
    expect(screen.getByText(/3 of 3 transactions · spend \$140$/), "the spend leaves the row dated ahead out").toBeDefined();
    expect(screen.getByTestId("filterbar-ahead").textContent).toBe(" · 1 dated into a month not begun, not in the spend or a report yet");
    cleanup();
    purchasesList = rows;
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={rows} /></MemoryRouter>);
    expect(screen.getByText(/2 of 2 transactions · spend \$140$/)).toBeDefined();
    expect(screen.queryByTestId("filterbar-ahead"), "no note without such a row").toBeNull();
  });
  it("a selection with no spending row names no $0 spend: the line says there is none", () => {
    purchasesList = [tx({ id: "m", date: "2026-06-10", amount: -100 })];
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={[]} /></MemoryRouter>);
    expect(screen.getByText(/^0 of 1 transaction · no spending$/)).toBeDefined();
    expect(screen.queryByText(/spend \$0/)).toBeNull();
    cleanup();
    // income alone: it is a transaction the line counts (no purchase), and there is still no spending
    const pay = tx({ id: "p", date: "2026-06-12", amount: 3_000, type: "income", category: "Income" });
    purchasesList = [pay];
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={[pay]} /></MemoryRouter>);
    expect(screen.getByText(/^1 of 1 transaction · no spending$/)).toBeDefined();
  });
  // The register marks every row dated after the ingest day; the note counts only those in a month that has not begun.
  // A row dated a few days ahead inside the month in progress is marked there and is in the spend here, never in the note.
  describe("with a row of each kind (the ingest ran mid-month)", () => {
    beforeEach(() => { generatedAt = "2026-06-20T20:00:00-04:00"; txnOwners = ["Alex"]; });
    afterEach(() => { generatedAt = JUNE_30; });
    const inMonth = tx({ id: "soon", date: "2026-06-25", amount: -60 });       // dated ahead, inside June
    const notBegun = tx({ id: "july", date: "2026-07-02", amount: -999 });     // dated into July, which has not begun
    const today = tx({ id: "t", date: "2026-06-12", amount: -40 });

    it("the note says 1, and the row dated ahead inside the month in progress is in the spend", () => {
      purchasesList = [today, inMonth, notBegun];
      render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}}
        filtered={[today, inMonth, notBegun]} /></MemoryRouter>);
      expect(screen.getByText(/3 of 3 transactions · spend \$100$/), "the June row dated ahead is in the spend").toBeDefined();
      expect(screen.getByTestId("filterbar-ahead").textContent).toBe(" · 1 dated into a month not begun, not in the spend or a report yet");
    });

    it("a split purchase dated into a month not begun counts once, as the transactions beside it do", () => {
      const parts = [tx({ id: "pa", date: "2026-07-03", amount: -70, parent_id: "p" }),
        tx({ id: "pb", date: "2026-07-03", amount: -30, parent_id: "p" })];
      purchasesList = [today, tx({ id: "p", date: "2026-07-03", amount: -100, is_parent: true })];
      render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}}
        filtered={[today, ...parts]} /></MemoryRouter>);
      expect(screen.getByText(/2 of 2 transactions · spend \$40$/)).toBeDefined();
      expect(screen.getByTestId("filterbar-ahead").textContent).toBe(" · 1 dated into a month not begun, not in the spend or a report yet");
    });
  });
  it("the \"view these N transactions\" link says the same N as the count line, not the number of parts", () => {
    purchasesList = [tx({ id: "p", is_parent: true, amount: -300 }), tx({ id: "o", amount: -40 })];
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={filtered}
      rowsPath="/spending/transactions" /></MemoryRouter>);
    expect(screen.getByRole("link", { name: /view these 2 transactions/ })).toBeDefined();
  });
});

// Tags can be added in the register (row and bulk) and used to be findable only through a hand-typed ?tags=.
describe("FilterBar tags filter", () => {
  const bar2 = (filters: Partial<Filters>, update = vi.fn()) => ({
    update, ui: render(<MemoryRouter><FilterBar filters={{ ...EMPTY_FILTERS, ...filters }} update={update} clear={() => {}} filtered={[]} /></MemoryRouter>),
  });
  it("offers a Tags control once any row carries a tag, listing every tag, and selecting one asks for { tags }", () => {
    tagList = ["kids", "trip"];
    const { update } = bar2({});
    fireEvent.click(screen.getByRole("button", { name: /^Tags/ }));
    const menu = screen.getByPlaceholderText("search…").parentElement!;      // the open dropdown (the bar's own review / internal boxes are not in it)
    expect(within(menu).getAllByRole("checkbox").map((c) => c.closest("label")?.textContent)).toEqual(["kids", "trip"]);
    fireEvent.click(screen.getByRole("checkbox", { name: "trip" }));
    expect(update).toHaveBeenCalledWith({ tags: ["trip"] });
  });
  it("is absent while nothing is tagged (an empty list to pick from is noise)", () => {
    bar2({});
    expect(screen.queryByRole("button", { name: /^Tags/ })).toBeNull();
  });
  it("a selected tag is a removable chip and counts on the control, even when no row carries it any more", () => {
    tagList = ["kids"];
    const { update } = bar2({ tags: ["kids", "gone"] });
    expect(screen.getByRole("button", { name: /^Tags · 2/ })).toBeDefined();
    fireEvent.click(within(screen.getByText("tag: gone").closest("span")!).getByRole("button"));
    expect(update).toHaveBeenCalledWith({ tags: ["kids"] });
    expect(screen.getByText("tag: kids")).toBeDefined();
  });
});

describe("FilterBar account chip", () => {
  it("names the account, not its id; an id the registry does not know stays as it is", () => {
    accountList = [{ id: "demo_checking_0101", name: "Joint Checking …0101" }];
    render(<MemoryRouter><FilterBar filters={{ ...EMPTY_FILTERS, accounts: ["demo_checking_0101", "old_9"] }} update={() => {}} clear={() => {}} filtered={[]} /></MemoryRouter>);
    expect(screen.getByText("acct: Joint Checking …0101")).toBeDefined();
    expect(screen.queryByText("acct: demo_checking_0101")).toBeNull();
    expect(screen.getByText("acct: old_9")).toBeDefined();
  });
});

// A view saved from a fresh visit used to freeze the six-month `from` the page wrote itself - an absolute date nobody chose.
// A window that is a preset is now stored as a rolling `win=<preset>`; dates the user picked are stored as the exact days.
describe("FilterBar save view and the date window", () => {
  const saveFrom = (filters: Filters) => {
    render(<MemoryRouter><FilterBar filters={filters} update={() => {}} clear={() => {}} filtered={[]} /></MemoryRouter>);
    fireEvent.click(screen.getByTestId("filterbar-save-view"));
    fireEvent.change(screen.getByLabelText("View name"), { target: { value: "Dining" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
  };
  const AUTO = presetRange("6m", "2026-06-30").from;
  it("saving on the automatic six months stores no `from` but win=6m: the view keeps rolling", () => {
    saveFrom({ ...EMPTY_FILTERS, categories: ["Dining"], from: AUTO });
    expect(saveFilter).toHaveBeenCalledWith("Dining", "categories=Dining&win=6m");
  });
  it("saving on 12M stores win=12m, and on YTD win=ytd", () => {
    saveFrom({ ...EMPTY_FILTERS, categories: ["Dining"], from: presetRange("12m", "2026-06-30").from });
    expect(saveFilter).toHaveBeenLastCalledWith("Dining", "categories=Dining&win=12m");
    cleanup();
    saveFrom({ ...EMPTY_FILTERS, categories: ["Dining"], from: presetRange("ytd", "2026-06-30").from });
    expect(saveFilter).toHaveBeenLastCalledWith("Dining", "categories=Dining&win=ytd");
  });
  it("dates the user picked are stored as the exact days: another start date, or an end date", () => {
    saveFrom({ ...EMPTY_FILTERS, categories: ["Dining"], from: "2025-11-03" });
    expect(saveFilter).toHaveBeenLastCalledWith("Dining", "categories=Dining&from=2025-11-03");
    cleanup();
    saveFrom({ ...EMPTY_FILTERS, categories: ["Dining"], from: AUTO, to: "2026-05-31" });
    expect(saveFilter).toHaveBeenLastCalledWith("Dining", `categories=Dining&from=${AUTO}&to=2026-05-31`);
  });
  it("All time stores no window at all", () => {
    saveFrom({ ...EMPTY_FILTERS, categories: ["Dining"] });
    expect(saveFilter).toHaveBeenLastCalledWith("Dining", "categories=Dining");
  });
});

// A fresh visit has only the page's own six months on: nothing the user chose, so nothing worth naming a view.
describe("FilterBar offers Save view for something the user chose", () => {
  const AUTO = presetRange("6m", "2026-06-30").from;
  const withFilters = (f: Partial<Filters>) =>
    render(<MemoryRouter><FilterBar filters={{ ...EMPTY_FILTERS, ...f }} update={() => {}} clear={() => {}} filtered={[]} /></MemoryRouter>);
  it("not on a fresh visit (the automatic window alone), though the bar still offers Clear all", () => {
    withFilters({ from: AUTO });
    expect(screen.queryByTestId("filterbar-save-view")).toBeNull();
    expect(screen.getByRole("button", { name: "Clear all" })).toBeDefined();
  });
  it("yes once a filter is on, or the window is one the user chose (12M, dates)", () => {
    withFilters({ from: AUTO, categories: ["Dining"] });
    expect(screen.getByTestId("filterbar-save-view")).toBeDefined();
    cleanup();
    withFilters({ from: presetRange("12m", "2026-06-30").from });
    expect(screen.getByTestId("filterbar-save-view")).toBeDefined();
    cleanup();
    withFilters({ from: AUTO, to: "2026-05-31" });
    expect(screen.getByTestId("filterbar-save-view")).toBeDefined();
  });
  it("not on All time with nothing else on", () => {
    withFilters({});
    expect(screen.queryByTestId("filterbar-save-view")).toBeNull();
  });
});

// Below md the whole bar was ~40% of a phone's first screen.
describe("FilterBar on a phone collapses to one summary line", () => {
  function stubMedia(phone: boolean) {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: phone && query.includes("max-width: 767px"), media: query, addEventListener: () => {}, removeEventListener: () => {},
    }));
  }
  afterEach(() => { vi.unstubAllGlobals(); });
  const AUTO = presetRange("6m", "2026-06-30").from;
  const phoneBar = (over: Partial<Filters> = {}, update = vi.fn(), clear = vi.fn()) => {
    render(<MemoryRouter><FilterBar filters={{ ...EMPTY_FILTERS, from: AUTO, categories: ["Dining"], q: "taco", ...over }}
      update={update} clear={clear} filtered={[]} /></MemoryRouter>);
    return { update, clear };
  };
  const toggle = () => screen.getByTestId("filterbar-toggle");

  it("shows the window and how many other filters are on, with every control hidden until the line is opened", () => {
    stubMedia(true);
    phoneBar();
    expect(toggle().textContent).toContain("6M · 2 filters");
    expect(toggle().tagName).toBe("BUTTON");
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    expect(toggle().getAttribute("aria-controls")).toBe(screen.getByTestId("filterbar-controls").id);
    expect(screen.getByTestId("filterbar-controls").hasAttribute("hidden")).toBe(true);
    expect(screen.queryByRole("button", { name: /^Category/ }), "a hidden control is out of reach, for a screen reader too").toBeNull();
    expect(screen.queryByRole("textbox", { name: /search description/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Clear all" })).toBeNull();
    expect(screen.getByText(/0 of 0 transactions/).closest("[hidden]"), "the count stays on the first screen").toBeNull();
  });

  it("opens the existing controls, each reachable and still wired, and closes again", () => {
    stubMedia(true);
    const { update, clear } = phoneBar();
    fireEvent.click(toggle());
    expect(toggle().getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("filterbar-controls").hasAttribute("hidden")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "6M" }));
    expect(update).toHaveBeenCalledWith({ from: AUTO, to: undefined });
    fireEvent.change(screen.getByPlaceholderText("search description / vendor…"), { target: { value: "tacos" } });
    expect(update).toHaveBeenLastCalledWith({ q: "tacos" });
    expect(screen.getByRole("button", { name: /^Category · 1/ })).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(clear).toHaveBeenCalledTimes(1);
    fireEvent.click(toggle());
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("textbox", { name: /search description/ })).toBeNull();
  });

  it("a lone account filter is named by the account's NAME, not its id", () => {
    stubMedia(true);
    accountList = [{ id: "demo_checking_0101", name: "Joint Checking" }];
    phoneBar({ categories: [], q: undefined, accounts: ["demo_checking_0101"] });
    expect(toggle().textContent).toContain("6M · Joint Checking");
    expect(toggle().textContent).not.toContain("demo_checking_0101");
  });

  it("opening and closing the bar changes no filter: the filters live in the URL, the disclosure is only the view of them", () => {
    stubMedia(true);
    const { update, clear } = phoneBar();
    fireEvent.click(toggle());
    fireEvent.click(toggle());
    fireEvent.click(toggle());
    expect(update).not.toHaveBeenCalled();
    expect(clear).not.toHaveBeenCalled();
    expect(toggle().textContent, "and the summary still reads the same filters").toContain("6M · 2 filters");
  });

  it("with exactly one other filter the summary names it; two or more are counted", () => {
    stubMedia(true);
    phoneBar({ categories: ["Dining"], q: undefined });
    expect(toggle().textContent).toContain("6M · Dining");
    expect(toggle().textContent).not.toContain("1 filter");
    cleanup();
    phoneBar({ categories: ["Dining", "Groceries"], q: undefined });
    expect(toggle().textContent, "one facet, two values").toContain("6M · Dining +1");
    cleanup();
    phoneBar({ categories: [], q: "taco tuesday" });
    expect(toggle().textContent).toContain("6M · “taco tuesday”");
    cleanup();
    phoneBar({ categories: ["Dining"], q: "taco" });
    expect(toggle().textContent, "two filters: counted, not named").toContain("6M · 2 filters");
    expect(toggle().textContent).not.toContain("Dining");
  });

  it("the summary follows the URL's filters: no filters, a chosen window, an All window", () => {
    stubMedia(true);
    phoneBar({ categories: [], q: undefined });
    expect(toggle().textContent).toContain("6M · no filters");
    cleanup();
    phoneBar({ categories: [], q: undefined, from: "2026-01-03", to: "2026-03-09" });
    expect(toggle().textContent).toContain("Jan 3, 2026 – Mar 9, 2026 · no filters");
    cleanup();
    phoneBar({ from: undefined, categories: [], q: undefined, vendors: ["V"] });
    expect(toggle().textContent).toContain("All · V");
  });

  it("is sticky only while collapsed (an open bar scrolls away with the page)", () => {
    stubMedia(true);
    phoneBar();
    expect(screen.getByTestId("filterbar").className).toContain("sticky");
    fireEvent.click(toggle());
    expect(screen.getByTestId("filterbar").className).not.toContain("sticky");
  });

  it("on a desktop there is no toggle and every control is in place, as before", () => {
    stubMedia(false);
    phoneBar();
    expect(screen.queryByTestId("filterbar-toggle")).toBeNull();
    expect(screen.getByTestId("filterbar-controls").hasAttribute("hidden")).toBe(false);
    expect(screen.getByPlaceholderText("search description / vendor…")).toBeDefined();
    expect(screen.getByTestId("filterbar").className).toContain("sticky");
  });
});

// From md up the menu belongs under the button that opened it; on a phone it keeps the row's left edge.
describe("FilterBar saved views menu: alignment under the button on a wide screen", () => {
  afterEach(() => { vi.restoreAllMocks(); });
  const measured = (button: number, row: number, menu: number) => {
    vi.spyOn(HTMLElement.prototype, "offsetLeft", "get").mockImplementation(function (this: HTMLElement) {
      return this.dataset.testid === "filterbar-views" ? button : 0;
    });
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) {
      return this.className.includes("relative") ? row : 0;
    });
    vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(function (this: HTMLElement) {
      return this.id === "filterbar-views-list" ? menu : 0;
    });
    vi.spyOn(HTMLElement.prototype, "offsetParent", "get").mockImplementation(function (this: HTMLElement) {
      return this.closest(".relative");
    });
  };
  const open = () => {
    savedFilters = [view("a", "Coffee runs", "categories=Dining")];
    render(bar());
    fireEvent.click(screen.getByTestId("filterbar-views"));
    return screen.getByRole("list", { name: "Saved views" });
  };
  it("opens under the button (its left edge), not at the row's left edge", () => {
    measured(420, 1000, 288);
    expect(open().style.left).toBe("420px");
  });
  it("is pulled back left as far as it needs to stay inside the row", () => {
    measured(900, 1000, 288);
    expect(open().style.left).toBe("712px");
  });
  it("never goes left of the row", () => {
    measured(10, 200, 288);
    expect(open().style.left).toBe("0px");
  });
  it("keeps the row's left edge on a phone (no inline offset)", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("max-width: 767px"), media: query, addEventListener: () => {}, removeEventListener: () => {} }));
    measured(420, 1000, 288);
    savedFilters = [view("a", "Coffee runs", "categories=Dining")];
    render(bar());
    fireEvent.click(screen.getByTestId("filterbar-toggle"));
    fireEvent.click(screen.getByTestId("filterbar-views"));
    expect(screen.getByRole("list", { name: "Saved views" }).style.left).toBe("");
    vi.unstubAllGlobals();
  });
});

// An open end of the window shows the day it stands for, and closing it at that day changes nothing: the newest (or
// oldest) day among the rows the open window lets in, a row the bank dated ahead of the data's last day included.
describe("FilterBar: an open end shows the last day the window really includes", () => {
  afterEach(() => { dataRange = { first: "2025-01-01", last: "2026-06-30" }; through = null; });
  it("the To box shows the newest row's day, past the data's last day; typing it in keeps the same rows", () => {
    dataRange = { first: "2025-01-01", last: "2026-07-02" };
    through = "2026-06-30";
    const tx = txFactory({ type: "purchase" });
    const rows = [tx({ date: "2025-01-01" }), tx({ date: "2026-06-29" }), tx({ date: "2026-06-30" }), tx({ date: "2026-07-02" })];
    const open: Filters = { ...EMPTY_FILTERS, from: "2026-01-01" };
    render(<MemoryRouter><FilterBar filters={open} update={() => {}} clear={() => {}} filtered={[]} /></MemoryRouter>);
    const shownTo = (screen.getByLabelText("to") as HTMLInputElement).value;
    expect(shownTo).toBe("2026-07-02");
    expect(applyFilters(rows, { ...open, to: shownTo })).toEqual(applyFilters(rows, open));
    // the data's last day, which the box used to show, would leave the row dated ahead out
    expect(applyFilters(rows, { ...open, to: "2026-06-30" }).length).toBe(applyFilters(rows, open).length - 1);
  });
  it("the From box of an all-time window shows the oldest row's day; typing it in keeps the same rows", () => {
    const tx = txFactory({ type: "purchase" });
    const rows = [tx({ date: "2025-01-01" }), tx({ date: "2026-06-30" })];
    render(<MemoryRouter><FilterBar filters={EMPTY_FILTERS} update={() => {}} clear={() => {}} filtered={[]} /></MemoryRouter>);
    const shownFrom = (screen.getByLabelText("from") as HTMLInputElement).value;
    expect(shownFrom).toBe("2025-01-01");
    expect(applyFilters(rows, { ...EMPTY_FILTERS, from: shownFrom })).toEqual(applyFilters(rows, EMPTY_FILTERS));
  });
});

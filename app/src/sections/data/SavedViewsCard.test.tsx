// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CategoryRename } from "../../data/types";
import { __resetFilterWarnings } from "../../filters/filters";
import {
  emptyPrefs, MAX_PREFS_DOC_BYTES, type PrefsDoc, type SavedFilter, type SavedReport, withoutSaved, withSaved,
} from "../../lib/prefs";
import { DEFAULT_SPEC } from "../../lib/reportSpec";

let views: SavedFilter[] = [];
let reports: SavedReport[] = [];
let docExtra: Partial<PrefsDoc> = {};
let renames: CategoryRename[] = [];
let localOnly = false;
let refused: number | null = null;
const removeSavedFilter = vi.fn();
const renameSavedFilter = vi.fn((_id: string, _name: string) => true);
const removeReport = vi.fn();
const renameReport = vi.fn((_id: string, _name: string) => true);
// whether the household has data yet: without any, the first-run page stands in for Spending's pages
let firstRun = false;
vi.mock("../../data/DataContext", () => ({
  useData: () => ({
    indexes: { categoryList: ["Dining", "Groceries"] },
    data: { assumptions: { category_renames: renames }, transactions: firstRun ? [] : [{ id: "t" }], assets: { snapshots: [] },
      balances: { series: {} }, manifest: { setup: { accounts: 1, feed: false, import_files: 0, manual_assets: 0 } } },
    prefs: { doc: { ...emptyPrefs(), saved_filters: views, reports, ...docExtra }, localOnly, refused,
      removeSavedFilter, renameSavedFilter, removeReport, renameReport },
  }),
}));

// the rename check is a preview (it renders on every keystroke): any read of the app's one clock (lib/clock.ts) while
// it renders is a failure
const reads = vi.hoisted(() => ({ calls: 0 }));
vi.mock("../../lib/clock", async (orig) => {
  const { clock } = await orig<typeof import("../../lib/clock")>();
  const counted = <A extends unknown[], R>(read: (...a: A) => R) => (...a: A): R => { reads.calls++; return read(...a); };
  return { clock: { now: counted(clock.now), nowISO: counted(clock.nowISO), today: counted(clock.today) } };
});

import SavedViewsCard from "./SavedViewsCard";
import { Where, where } from "../../test/routerProbe";

const view = (id: string, name: string, query: string): SavedFilter => ({ id, name, query, created_at: "2026-01-01T00:00:00Z" });
const report = (id: string, name: string, filters: string, over: Partial<SavedReport["spec"]> = {}): SavedReport =>
  ({ id, name, spec: { ...DEFAULT_SPEC, filters, ...over }, created_at: "2026-01-01T00:00:00Z" });
beforeEach(() => {
  views = []; reports = []; docExtra = {}; renames = []; localOnly = false; refused = null;
  removeSavedFilter.mockClear(); renameSavedFilter.mockClear(); renameSavedFilter.mockReturnValue(true);
  removeReport.mockClear(); renameReport.mockClear(); renameReport.mockReturnValue(true);
});
afterEach(cleanup);

const card = () => render(<MemoryRouter><SavedViewsCard /><Where /></MemoryRouter>);

// A 40px ✕ right beside "rename" invites a mistap: room between the two guards against it, not a confirm step (the
// owner's rule: none).
describe("Data → Saved views: the ✕ is set apart from rename", () => {
  it("rename and ✕ are the row's end-aligned pair, with a gap before the ✕ that rename does not have", () => {
    views = [view("a", "Coffee runs", "categories=Dining")];
    card();
    const rename = screen.getByRole("button", { name: "Rename saved view Coffee runs" });
    const del = screen.getByRole("button", { name: "Delete saved view Coffee runs" });
    expect(del.parentElement, "one group").toBe(rename.parentElement);
    expect(del.parentElement!.className, "pushed to the row's end").toContain("ml-auto");
    expect(del.previousElementSibling, "the ✕ comes last, after rename").toBe(rename);
    expect(del.className, "room before the ✕ on a phone").toMatch(/(^|\s)ml-5(\s|$)/);
    expect(del.className, "and on a desktop").toContain("md:ml-4");
    expect(rename.className).not.toMatch(/\bml-/);
  });
  it("still deletes on ONE tap - no confirm step was added", () => {
    views = [view("a", "Coffee runs", "categories=Dining")];
    card();
    fireEvent.click(screen.getByRole("button", { name: "Delete saved view Coffee runs" }));
    expect(removeSavedFilter).toHaveBeenCalledWith("a");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByText(/are you sure|confirm/i)).toBeNull();
  });
  it("the card says a view saved on a preset rolls with the data, and dates you picked stay fixed", () => {
    views = [view("a", "Coffee runs", "categories=Dining")];
    card();
    const text = (screen.getByTestId("card-saved-views").textContent ?? "").replace(/\s+/g, " ");
    expect(text).toContain("saved on a preset window (3M, 6M, 12M, YTD - the automatic last 6 months included) rolls with the data");
    expect(text).toContain("Dates you picked yourself are saved as the exact days");
  });
  it("a rolling view is valid (never stale) and its row says so; an unknown `win` is ignored with a warning, not flagged stale", () => {
    __resetFilterWarnings();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    views = [view("a", "Rolling", "categories=Dining&win=6m"), view("b", "Junk", "categories=Dining&win=bogus"), view("c", "Fixed", "from=2025-11-03")];
    card();
    const c = screen.getByTestId("card-saved-views");
    expect(c.textContent, "no row is flagged stale").not.toMatch(/\d stale (view|report)|stale:/);
    expect(screen.getByRole("link", { name: "Rolling" }).getAttribute("href")).toBe("/spending/transactions?categories=Dining&win=6m");
    expect(within(c).getByText("1 filter · rolling 6M")).toBeDefined();
    expect(within(c).getAllByText("1 filter"), "the junk one and the fixed one: no rolling note").toHaveLength(2);
    expect(warn.mock.calls.filter((x: unknown[]) => String(x[0]).includes("?win=bogus"))).toHaveLength(1);
    warn.mockRestore();
  });
});

describe("Data → Saved views", () => {
  it("says so, and points at the Transactions tab and the Reports tab, when the family has saved nothing", () => {
    card();
    const c = screen.getByTestId("card-saved-views");
    expect(c.textContent).toContain("Saved views & reports — 0 of 50 views · 0 of 50 reports");
    expect(c.textContent).toContain("No saved views or reports yet");
    expect(c.textContent).toContain("Save report");
    expect(within(c).getByRole("link", { name: "Transactions" }).getAttribute("href")).toBe("/spending/transactions");
    expect(within(c).getByRole("link", { name: "Spending → Reports" }).getAttribute("href")).toBe("/spending/reports");
  });
  it("before the household's first data it points at no page the first-run page stands in for", () => {
    firstRun = true;
    try {
      card();
      const c = screen.getByTestId("card-saved-views");
      expect(c.textContent).toContain("No saved views or reports yet.");
      expect(within(c).queryByRole("link"), "no link to a page that shows the set-up page").toBeNull();
      expect(c.textContent).not.toMatch(/Filter the|Save report/);
    } finally {
      firstRun = false;
    }
  });

  it("lists a valid view as a link that opens it with the carried-search marker, and counts its filters", () => {
    views = [view("a", "Coffee runs", "categories=Dining&from=2026-01-01")];
    card();
    const link = screen.getByRole("link", { name: "Coffee runs" });
    expect(link.getAttribute("href")).toBe("/spending/transactions?categories=Dining&from=2026-01-01");
    expect(screen.getByText("2 filters")).toBeDefined();
    fireEvent.click(link);
    expect(where()).toBe(
      '/spending/transactions?categories=Dining&from=2026-01-01|{"carriedSearch":true}');
  });

  it("an empty-query view is a valid all-time view and opens with the marker too", () => {
    views = [view("e", "Everything", "")];
    card();
    expect(screen.getByText("0 filters")).toBeDefined();
    fireEvent.click(screen.getByRole("link", { name: "Everything" }));
    expect(where()).toBe('/spending/transactions|{"carriedSearch":true}');
  });

  it("names a stale view with its reason, keeps it deletable, and does not link it", () => {
    views = [view("a", "Coffee runs", "categories=Dining"), view("s", "Late-night", "categories=Nightlife"),
             view("t", "Odd", "bogus=1")];
    card();
    const c = screen.getByTestId("card-saved-views");
    expect(c.textContent).toContain("Saved views & reports — 3 of 50 views");   // stale views count against the cap
    expect(c.textContent).toContain('stale: category "Nightlife" is no longer in the data');
    expect(c.textContent).toContain('stale: unknown filter key "bogus"');
    expect(c.textContent).toContain("2 stale views");
    expect(screen.queryByRole("link", { name: "Late-night" })).toBeNull();        // the NAME is not a link
    expect(screen.getByRole("link", { name: "Coffee runs" })).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Delete saved view Late-night" }));
    expect(removeSavedFilter).toHaveBeenCalledWith("s");
  });

  it("the stale note names the way into the palette: 'search' for a finger on a phone (the top bar's 🔍), else ⌘K", () => {
    views = [view("s", "Late-night", "categories=Nightlife")];
    reports = [report("r", "Gone report", "categories=Nightlife")];
    card();
    expect(screen.getByTestId("card-saved-views").textContent).toContain("not offered in the filter bar or ⌘K");
    expect(screen.getByTestId("card-saved-views").textContent).toContain("not offered on the Reports tab or in ⌘K");
    cleanup();
    const media = (phone: boolean) => vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query === "(pointer: coarse)" || (phone && query.includes("max-width")), media: query,
      addEventListener: () => {}, removeEventListener: () => {} }));
    try {
      media(true);
      card();
      const text = screen.getByTestId("card-saved-views").textContent ?? "";
      expect(text).toContain("not offered in the filter bar or search");
      expect(text).toContain("not offered on the Reports tab or in search");
      expect(text).not.toContain("⌘K");
      cleanup();
      media(false);                                                  // a finger on a tablet: no 🔍 button there
      card();
      expect(screen.getByTestId("card-saved-views").textContent).toContain("not offered in the filter bar or ⌘K");
    } finally { vi.unstubAllGlobals(); }
  });
  it("a stale view can be opened as saved: its filters with unknown keys dropped, the marker carried, still named stale", () => {
    views = [view("s", "Late-night", "categories=Nightlife&min=abc&q=taco"), view("t", "Odd", "bogus=1&q=x")];
    __resetFilterWarnings();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});           // the stored `min=abc` is warned about, once
    card();
    const open = screen.getByRole("link", { name: "open as saved: stale view Late-night" });
    expect(open.textContent).toBe("open as saved");
    // label in name (WCAG 2.5.3): the accessible name STARTS with the visible words, so "click open as saved" works
    expect(open.getAttribute("aria-label")!.startsWith(open.textContent!)).toBe(true);
    expect(open.getAttribute("href")).toBe("/spending/transactions?categories=Nightlife&q=taco");
    expect(open.className).toContain("min-h-[40px]");
    expect(screen.getByRole("link", { name: "open as saved: stale view Odd" }).getAttribute("href"))
      .toBe("/spending/transactions?q=x");                                          // the unknown key is dropped, the rest kept
    expect(screen.getByTestId("card-saved-views").textContent).toContain('stale: category "Nightlife" is no longer in the data');
    expect(warn.mock.calls.map((c) => String(c[0])).filter((m) => m.includes("?min=abc"))).toHaveLength(1);
    warn.mockRestore();
    fireEvent.click(open);
    expect(where()).toBe(
      '/spending/transactions?categories=Nightlife&q=taco|{"carriedSearch":true}');
  });

  it("a valid view has no 'open as saved' link (its name is the link)", () => {
    views = [view("a", "Coffee runs", "categories=Dining")];
    card();
    expect(screen.queryByText("open as saved")).toBeNull();
  });

  it("a renamed category still opens, under the new name", () => {
    renames = [{ from: { category: "Eating Out" }, to: { category: "Dining" } }];
    views = [view("a", "Old name", "categories=Eating+Out")];
    card();
    expect(screen.getByRole("link", { name: "Old name" }).getAttribute("href")).toBe("/spending/transactions?categories=Dining");
  });

  it("deletes a valid view by its id; the ✕ is a 40px tap target below md", () => {
    views = [view("a", "Coffee runs", "categories=Dining")];
    card();
    const del = screen.getByRole("button", { name: "Delete saved view Coffee runs" });
    expect(del.className).toContain("min-h-[40px]");
    expect(del.className).toContain("md:min-h-0");
    fireEvent.click(del);
    expect(removeSavedFilter).toHaveBeenCalledWith("a");
  });

  it("renames in place: Enter saves the new name, Escape and cancel leave it alone", () => {
    views = [view("a", "Coffee runs", "categories=Dining")];
    card();
    fireEvent.click(screen.getByRole("button", { name: "Rename saved view Coffee runs" }));
    const input = screen.getByRole("textbox", { name: "New name for saved view Coffee runs" }) as HTMLInputElement;
    expect(input.value).toBe("Coffee runs");
    expect(input.className).toContain("text-base sm:text-xs");           // 16px on a phone
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(renameSavedFilter).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Rename saved view Coffee runs" }));
    fireEvent.click(screen.getByRole("button", { name: "cancel" }));
    expect(screen.queryByRole("textbox")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Rename saved view Coffee runs" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Espresso" } });
    fireEvent.submit(screen.getByRole("textbox").closest("form")!);
    expect(renameSavedFilter).toHaveBeenCalledWith("a", "Espresso");
    expect(screen.queryByRole("textbox")).toBeNull();                       // back to the row
  });

  it("a stale view can be renamed too, and a refused rename stays open and says so", () => {
    views = [view("s", "Late-night", "categories=Nightlife")];
    renameSavedFilter.mockReturnValue(false);
    card();
    fireEvent.click(screen.getByRole("button", { name: "Rename saved view Late-night" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Nights" } });
    fireEvent.submit(screen.getByRole("textbox").closest("form")!);
    expect(screen.getByRole("alert").textContent).toContain("cannot be saved");
    expect(screen.getByRole("textbox")).toBeDefined();
  });

  it("a long name wraps instead of truncating, in a valid row and a stale one (an 80-char name once pushed the phone page wider)", () => {
    views = [view("a", "x".repeat(80), "q=x"), view("s", "y".repeat(80), "categories=Nightlife")];
    card();
    for (const name of ["x".repeat(80), "y".repeat(80)]) {
      const el = screen.getByText(name);
      expect(el.className).toContain("[overflow-wrap:anywhere]");
      expect(el.className).not.toContain("truncate");
      expect(el.className).toContain("min-w-0");
    }
  });

  it("a rename the full prefs doc cannot hold says 'Settings are full', not 'cannot be saved', and offers no Save", () => {
    let doc = emptyPrefs();
    for (let i = 0; i < 50; i++) {
      const next = withSaved(doc, "saved_filters", { id: `b${i}`, name: `b${i}`, query: "q=" + "x".repeat(1500) }, "2026-01-01T00:00:00Z");
      if (!next) break;
      doc = next;
    }
    // the doc as the card reads it, topped up to ten bytes under the cap by a budget whose key pads it to the byte: a
    // longer name has nowhere to go, and a shorter one (one byte less, the preview's longer stamp four more) still fits
    const shown = (budgets: Record<string, number>): PrefsDoc =>
      ({ ...emptyPrefs(), saved_filters: doc.saved_filters, reports: [], budgets });
    const bytes = (d: PrefsDoc) => new TextEncoder().encode(JSON.stringify(d)).length;
    if (MAX_PREFS_DOC_BYTES - bytes(shown({ "": 0 })) < 10) {
      doc = withoutSaved(doc, "saved_filters", doc.saved_filters[doc.saved_filters.length - 1].id, "2026-01-01T00:00:00Z");
    }
    const pad = "p".repeat(MAX_PREFS_DOC_BYTES - 10 - bytes(shown({ "": 0 })));
    expect(bytes(shown({ [pad]: 0 }))).toBe(MAX_PREFS_DOC_BYTES - 10);
    views = doc.saved_filters;
    docExtra = { budgets: { [pad]: 0 } };
    card();
    fireEvent.click(screen.getByRole("button", { name: "Rename saved view b0" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "a much longer name than before ".repeat(2) } });
    expect(screen.getByRole("alert").textContent).toBe("Settings are full — delete a view on Data first");   // saveRefusalText: the one string
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "b" } });          // a shorter name fits again
    expect(screen.queryByRole("alert")).toBeNull();
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("the rename check reads no clock, however many keys are typed (its stamp is the preview's)", () => {
    views = [view("v1", "Coffee runs", "q=coffee")];
    card();
    reads.calls = 0;
    fireEvent.click(screen.getByRole("button", { name: "Rename saved view Coffee runs" }));
    for (const value of ["E", "Es", "Espresso"]) fireEvent.change(screen.getByRole("textbox"), { target: { value } });
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled, "the check ran: the name fits").toBe(false);
    expect(reads.calls).toBe(0);
  });

  it("warns that the store is unreachable when edits stay in this browser", () => {
    localOnly = true;
    card();
    expect(screen.getByTestId("card-saved-views").textContent).toContain("saved views and reports are saved in this browser only");
  });
});

describe("Data → Saved reports", () => {
  const spec = { groupBy: "vendor", interval: "month", measure: "count", chart: "line" } as const;

  it("lists a valid report as a link to the Reports tab with its spec, opened with the carried-search marker, and describes it", () => {
    reports = [report("r1", "Dining by vendor", "categories=Dining&from=2026-01-01", spec)];
    card();
    const c = screen.getByTestId("card-saved-views");
    expect(c.textContent).toContain("0 of 50 views · 1 of 50 reports");
    const link = screen.getByRole("link", { name: "Dining by vendor" });
    expect(link.getAttribute("href")).toBe("/spending/reports?categories=Dining&from=2026-01-01&groupBy=vendor&interval=month&measure=count&chart=line");
    expect(c.textContent).toContain("transactions (count) by vendor, per month · 2 filters");
    fireEvent.click(link);
    expect(where()).toBe(
      '/spending/reports?categories=Dining&from=2026-01-01&groupBy=vendor&interval=month&measure=count&chart=line|{"carriedSearch":true}');
  });

  it("a report with no filters is a valid all-time report and opens with the marker too (no window, not the six-month default)", () => {
    reports = [report("r0", "Everything", "")];
    card();
    expect(screen.getByText("spending by vendor, per month · 0 filters")).toBeDefined();
    fireEvent.click(screen.getByRole("link", { name: "Everything" }));
    expect(where()).toBe('/spending/reports?groupBy=vendor&interval=month&measure=spend&chart=table|{"carriedSearch":true}');
  });

  it("views and reports sit in their own lists under their own headings; the empty-state sentence is gone once either exists", () => {
    views = [view("a", "Coffee runs", "categories=Dining")];
    reports = [report("r1", "Dining by vendor", "")];
    card();
    const c = screen.getByTestId("card-saved-views");
    // one level under the card's own h2
    expect(within(c).getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["Views", "Reports"]);
    expect(c.textContent).not.toContain("No saved views or reports yet");
    expect(screen.getByRole("link", { name: "Coffee runs" }).getAttribute("href")).toBe("/spending/transactions?categories=Dining");
  });

  it("a renamed category is rewritten inside a report; a deleted one makes it stale with the reason, still deletable, never a name link", () => {
    renames = [{ from: { category: "Eating Out" }, to: { category: "Dining" } }];
    reports = [report("a", "Old name", "categories=Eating+Out"), report("s", "Late-night", "categories=Nightlife", { groupBy: "owner" })];
    card();
    expect(screen.getByRole("link", { name: "Old name" }).getAttribute("href"))
      .toBe("/spending/reports?categories=Dining&groupBy=vendor&interval=month&measure=spend&chart=table");
    const c = screen.getByTestId("card-saved-views");
    expect(c.textContent).toContain('stale: category "Nightlife" is no longer in the data');
    expect(c.textContent).toContain("1 stale report — not offered on the Reports tab or in ⌘K");
    expect(screen.queryByRole("link", { name: "Late-night" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Delete saved report Late-night" }));
    expect(removeReport).toHaveBeenCalledWith("s");
    expect(removeSavedFilter).not.toHaveBeenCalled();
    const open = screen.getByRole("link", { name: "open as saved: stale report Late-night" });
    expect(open.getAttribute("href")).toBe("/spending/reports?categories=Nightlife&groupBy=owner&interval=month&measure=spend&chart=table");
    fireEvent.click(open);
    expect(where()).toBe('/spending/reports?categories=Nightlife&groupBy=owner&interval=month&measure=spend&chart=table|{"carriedSearch":true}');
  });

  it("deletes a valid report by its id, and the ✕ is a 40px tap target below md", () => {
    reports = [report("r1", "Dining by vendor", "")];
    card();
    const del = screen.getByRole("button", { name: "Delete saved report Dining by vendor" });
    expect(del.className).toContain("min-h-[40px]");
    fireEvent.click(del);
    expect(removeReport).toHaveBeenCalledWith("r1");
  });

  it("renames a report in place through renameReport (never the views' method); a refused rename stays open and says so", () => {
    reports = [report("r1", "Dining by vendor", "")];
    card();
    fireEvent.click(screen.getByRole("button", { name: "Rename saved report Dining by vendor" }));
    const input = screen.getByRole("textbox", { name: "New name for saved report Dining by vendor" }) as HTMLInputElement;
    expect(input.className).toContain("text-base sm:text-xs");
    fireEvent.change(input, { target: { value: "Vendors" } });
    fireEvent.submit(input.closest("form")!);
    expect(renameReport).toHaveBeenCalledWith("r1", "Vendors");
    expect(renameSavedFilter).not.toHaveBeenCalled();
    renameReport.mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "Rename saved report Dining by vendor" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Nope" } });
    fireEvent.submit(screen.getByRole("textbox").closest("form")!);
    expect(screen.getByRole("alert").textContent).toContain("cannot be saved");
  });

  it("a report rename the full prefs doc cannot hold says 'Settings are full — delete a report on Data first', and offers no Save", () => {
    // a fixed spec, so the byte arithmetic below never moves with the Reports default
    const FILL_SPEC = { filters: "", groupBy: "category", interval: "month", measure: "spend", chart: "stacked" } as const;
    let doc = emptyPrefs();
    for (let i = 0; i < 50; i++) {
      const next = withSaved(doc, "reports", { id: `b${i}`, name: `b${i}`, spec: { ...FILL_SPEC, filters: "q=" + "x".repeat(1500) } }, "2026-01-01T00:00:00Z");
      if (!next) break;
      doc = next;
    }
    for (let i = 0; i < 200; i++) {
      const next = withSaved(doc, "reports", { id: `s${i}`, name: "s", spec: FILL_SPEC }, "2026-01-01T00:00:00Z");
      if (!next) break;
      doc = next;
    }
    // a report is bigger than a view, so the top-up can leave a gap a rename would fit in: close it with a pad
    // in another section (budgets), leaving 5 bytes, so any longer name has nowhere to go
    const bytes = (d: PrefsDoc) => new TextEncoder().encode(JSON.stringify(d)).length;
    docExtra = { budgets: { ["p".repeat(MAX_PREFS_DOC_BYTES - bytes(doc) - 9)]: 1 } };
    expect(MAX_PREFS_DOC_BYTES - bytes({ ...doc, ...docExtra })).toBe(5);
    reports = doc.reports;
    card();
    fireEvent.click(screen.getByRole("button", { name: "Rename saved report b0" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "a much longer name than before ".repeat(2) } });
    expect(screen.getByRole("alert").textContent).toBe("Settings are full — delete a report on Data first");
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "b" } });          // a shorter name fits again
    expect(screen.queryByRole("alert")).toBeNull();
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("a long report name wraps instead of pushing the row wider", () => {
    reports = [report("r1", "z".repeat(80), "")];
    card();
    const el = screen.getByText("z".repeat(80));
    expect(el.className).toContain("[overflow-wrap:anywhere]");
    expect(el.className).not.toContain("truncate");
  });
});

describe("SavedViewsCard when edits stay in this browser", () => {
  const notice = () => screen.getByText(/saved in this browser only/).closest("p")!.textContent;
  it("an unreachable store says so", () => {
    localOnly = true;
    render(<MemoryRouter><SavedViewsCard /></MemoryRouter>);
    expect(notice()).toMatch(/^Store unreachable — saved views and reports are saved in this browser only for now\.$/);
  });
  it("a save the store refused as too large says that, and what makes room", () => {
    localOnly = true; refused = 413;
    render(<MemoryRouter><SavedViewsCard /></MemoryRouter>);
    expect(notice()).toMatch(/The family store refused the last save as too large — saved views and reports are saved in this browser only/);
    expect(notice()).toMatch(/Delete a saved view or report to make room/);
    expect(notice()).not.toMatch(/unreachable/);
  });
});

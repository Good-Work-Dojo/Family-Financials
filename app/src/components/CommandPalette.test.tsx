// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

let extraViews: { id: string; name: string; query: string; created_at: string }[] = [];
let reports: { id: string; name: string; spec: unknown; created_at: string }[] = [];
// the data's date range; null for a household with no transaction yet
let dataRange: { first: string; last: string } | null = { first: "2025-01-01", last: "2026-07-20" };
vi.mock("../data/DataContext", () => ({
  useData: () => ({
    indexes: {
      categoryList: ["Dining"], vendorList: ["Vendor A"], dataRange, dataThrough: dataRange?.last ?? null,
    },
    prefs: { doc: { saved_filters: [
      { id: "a", name: "Coffee runs", query: "categories=Dining", created_at: "x" },
      { id: "s", name: "Gone", query: "categories=Nightlife", created_at: "x" },
      { id: "e", name: "Everything", query: "", created_at: "x" },
      ...extraViews], reports } },
    data: {
      assumptions: { category_renames: [] },
      accounts: [
        { id: "chk_1", name: "Everyday Checking ...1234", institution: "First Bank" },
        { id: "card_9", name: "Travel Card ...9876", institution: "Big Card Co" },
      ],
    },
  }),
}));
import CommandPalette, { spendTo } from "./CommandPalette";
import { Where, where } from "../test/routerProbe";

afterEach(() => { cleanup(); extraViews = []; reports = []; });

describe("⌘K spending jumps", () => {
  it("land on the Transactions tab with a six-month window and the patch", () => {
    const to = spendTo({ categories: ["Dining"] }, "2026-07-20");
    expect(to.startsWith("/spending/transactions?")).toBe(true);
    const sp = new URLSearchParams(to.split("?")[1]);
    expect(sp.get("categories")).toBe("Dining");
    expect(sp.get("from")).toBe("2026-01-20");
    expect(sp.get("to")).toBeNull();
  });
  it("with no data date the jump carries the patch and no date window (never a window from a missing date)", () => {
    const to = spendTo({ categories: ["Dining"] }, null);
    expect(to).toBe("/spending/transactions?categories=Dining");
    expect(spendTo({ vendors: ["Vendor A"] }, null)).toBe("/spending/transactions?vendors=Vendor+A");
  });
  it("a category or vendor found with no transaction yet opens with no window, and its hint promises none", () => {
    dataRange = null;
    try {
      render(<MemoryRouter><CommandPalette open onOpenChange={() => {}} nav={NAV} /><Where /></MemoryRouter>);
      fireEvent.change(screen.getByRole("combobox"), { target: { value: "dining" } });
      const opt = within(screen.getByRole("listbox")).getAllByRole("option").find((o) => o.textContent?.includes("Dining"))!;
      expect(opt.textContent).toContain("category");
      expect(opt.textContent).not.toContain("last 6 mo");
      fireEvent.click(opt);
      expect(where()).toBe("/spending/transactions?categories=Dining|null");
    } finally {
      dataRange = { first: "2025-01-01", last: "2026-07-20" };
    }
  });
});

const NAV = [{ to: "/", label: "Home", icon: "◉" }, { to: "/data", label: "Data", icon: "⚙" }];
const palette = (props: Partial<Parameters<typeof CommandPalette>[0]> = {}) => (
  <MemoryRouter><CommandPalette open onOpenChange={() => {}} nav={NAV} {...props} /></MemoryRouter>
);

describe("⌘K is a combobox over a listbox", () => {
  it("names its keys (↑↓ ⏎ esc) to a keyboard and not to a finger", () => {
    render(palette());
    expect(screen.getByTestId("palette-keys").textContent).toBe("↑↓ navigate · ⏎ open · esc close");
    cleanup();
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query === "(pointer: coarse)", media: query,
      addEventListener: () => {}, removeEventListener: () => {} }));
    try {
      render(palette());
      expect(screen.queryByTestId("palette-keys")).toBeNull();
    } finally { vi.unstubAllGlobals(); }
  });

  it("exposes the roles and moves aria-activedescendant with the arrow keys", () => {
    render(palette());
    const input = screen.getByRole("combobox");
    expect(input.getAttribute("aria-expanded")).toBe("true");
    expect(input.getAttribute("aria-controls")).toBe("palette-listbox");
    expect(input.getAttribute("aria-autocomplete")).toBe("list");
    const options = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(options[0].getAttribute("aria-selected")).toBe("true");
    expect(input.getAttribute("aria-activedescendant")).toBe(options[0].id);
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(within(screen.getByRole("listbox")).getAllByRole("option")[1].getAttribute("aria-selected")).toBe("true");
    expect(input.getAttribute("aria-activedescendant")).toBe("palette-opt-1");
  });

  it("the dialog is modal, named, and the options are not wrapped in buttons", () => {
    render(palette());
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-label")).toBeTruthy();
    expect(within(screen.getByRole("listbox")).queryAllByRole("button")).toHaveLength(0);
  });

  it("an empty result set has no active descendant and an explicit empty row", () => {
    render(palette());
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "zzzzzz" } });
    expect(input.hasAttribute("aria-activedescendant")).toBe(false);
    expect(within(screen.getByRole("listbox")).queryAllByRole("option")).toHaveLength(0);
    expect(screen.getByText("no matches")).toBeDefined();
  });

  it("Enter opens the active option and closes the palette", () => {
    const onOpenChange = vi.fn();
    render(palette({ onOpenChange }));
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "ArrowDown" });
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("focuses the input on open, closes on Escape, and gives focus back to the opener", () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const onOpenChange = vi.fn();
    const { rerender } = render(palette({ onOpenChange }));
    expect(document.activeElement).toBe(screen.getByRole("combobox"));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
    rerender(palette({ open: false, onOpenChange }));
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it("reopening starts from an empty query with the first option active", () => {
    const { rerender } = render(palette());
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "dat" } });
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "ArrowDown" });
    rerender(palette({ open: false }));
    rerender(palette({ open: true }));
    const input = screen.getByRole("combobox") as HTMLInputElement;
    expect(input.value).toBe("");
    expect(input.getAttribute("aria-activedescendant")).toBe("palette-opt-0");
  });
});


describe("⌘K saved views", () => {
  it("lists valid saved views first, with their hint, and never a stale one", () => {
    render(palette());
    const opts = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(opts[0].textContent).toContain("★ Coffee runs");
    expect(opts[0].textContent).toContain("saved view");
    expect(opts[1].textContent).toContain("★ Everything");
    expect(opts[2].textContent).toContain("Home");                // the pages follow the views
    expect(screen.queryByText(/Gone/)).toBeNull();
  });

  it("finds a saved view by name among the matches, ahead of pages", () => {
    render(palette());
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "coffee" } });
    const opts = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(opts).toHaveLength(1);
    expect(opts[0].textContent).toContain("★ Coffee runs");
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "gone" } });
    expect(screen.getByText("no matches")).toBeDefined();         // the stale view is not searchable either
  });

  it("opens a view on Transactions with its query and the carried-search marker", () => {
    render(<MemoryRouter><CommandPalette open onOpenChange={() => {}} nav={NAV} /><Where /></MemoryRouter>);
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    expect(where()).toBe(
      '/spending/transactions?categories=Dining|{"carriedSearch":true}');
  });

  it("an empty-query view opens all-time: the marker is what stops the six-month default", () => {
    render(<MemoryRouter><CommandPalette open onOpenChange={() => {}} nav={NAV} /><Where /></MemoryRouter>);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "everything" } });
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    expect(where()).toBe('/spending/transactions|{"carriedSearch":true}');
  });

  it("a page or a category jump carries no marker (their links name their own dates)", () => {
    render(<MemoryRouter><CommandPalette open onOpenChange={() => {}} nav={NAV} /><Where /></MemoryRouter>);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "data" } });
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    expect(where()).toBe("/data|null");
  });
});

describe("⌘K saved reports", () => {
  const spec = { filters: "categories=Dining", groupBy: "vendor", interval: "quarter", measure: "count", chart: "table" };
  const report = (id: string, name: string, over: Record<string, unknown> = {}) => ({ id, name, spec: { ...spec, ...over }, created_at: "x" });

  it("lists valid reports after the views and before the pages, with their hint, and never a stale one", () => {
    reports = [report("r1", "Dining by vendor"), report("rs", "Late-night report", { filters: "categories=Nightlife" })];
    render(palette());
    const opts = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(opts[2].textContent).toContain("★ Dining by vendor");     // two views come first, then the report
    expect(opts[2].textContent).toContain("saved report");
    expect(opts[3].textContent).toContain("Home");
    expect(screen.queryByText(/Late-night report/)).toBeNull();
  });
  it("finds a report by name, and not a stale one", () => {
    reports = [report("r1", "Dining by vendor"), report("rs", "Late-night report", { filters: "categories=Nightlife" })];
    render(palette());
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "by vendor" } });
    const opts = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(opts).toHaveLength(1);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "late-night" } });
    expect(screen.getByText("no matches")).toBeDefined();
  });
  it("opens a report on the Reports tab with its filters, its report keys and the carried-search marker", () => {
    reports = [report("r1", "Dining by vendor")];
    render(<MemoryRouter><CommandPalette open onOpenChange={() => {}} nav={NAV} /><Where /></MemoryRouter>);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "by vendor" } });
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    expect(where()).toBe('/spending/reports?categories=Dining&groupBy=vendor&interval=quarter&measure=count&chart=table|{"carriedSearch":true}');
  });
  it("a report saved with no filters at all opens all-time: the marker is what stops the six-month default", () => {
    reports = [report("r2", "Everything", { filters: "", groupBy: "category", interval: "month", measure: "spend", chart: "stacked" })];
    render(<MemoryRouter><CommandPalette open onOpenChange={() => {}} nav={NAV} /><Where /></MemoryRouter>);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "everything" } });
    const opts = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(opts.map((o) => o.textContent)).toEqual([expect.stringContaining("saved view"), expect.stringContaining("saved report")]);
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "ArrowDown" });          // the view of the same name comes first; pick the report
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    expect(where()).toBe('/spending/reports?groupBy=category&interval=month&measure=spend&chart=stacked|{"carriedSearch":true}');
  });
});

describe("⌘K list keys", () => {
  it("two saved views with the same name and query render without a duplicate-key warning (the key is the id)", () => {
    extraViews = [
      { id: "d1", name: "Twin", query: "categories=Dining", created_at: "x" },
      { id: "d2", name: "Twin", query: "categories=Dining", created_at: "x" }];
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    render(palette());
    const twins = within(screen.getByRole("listbox")).getAllByRole("option").filter((o) => o.textContent?.includes("★ Twin"));
    expect(twins).toHaveLength(2);
    expect(error, "no 'same key' warning").not.toHaveBeenCalled();
    error.mockRestore();
  });
});

// A page's aliases (former names such as Tools, Real Estate, Life, Overview) still find it, an account name opens the
// account's own page, and both are found by what they are called, not only by the palette's "Hub · Tab" label.
describe("⌘K finds pages by their old names and accounts by name", () => {
  const NAV2 = [
    { to: "/", label: "Home", icon: "◉", aliases: ["Overview", "Life"] },
    { to: "/cash-flow", label: "Cash Flow · Overview", icon: "⇅" },
    { to: "/planning", label: "Planning · Projections", icon: "🔮", aliases: ["Tools", "What-ifs"] },
    { to: "/planning/home", label: "Planning · House", icon: "🏠", aliases: ["Home", "Mortgage", "Real Estate"] },
  ];
  const type = (q: string) => fireEvent.change(screen.getByRole("combobox"), { target: { value: q } });
  const options = () => within(screen.getByRole("listbox")).getAllByRole("option").map((o) => o.textContent ?? "");

  it('either alias of a page ("tools", "what-ifs") lands on it, though its label has neither word', () => {
    render(palette({ nav: NAV2 }));
    type("tools");
    expect(options()).toHaveLength(1);
    expect(options()[0]).toContain("Planning · Projections");
    type("what-ifs");
    expect(options()[0]).toContain("Planning · Projections");
  });
  it('"real estate" finds the House tab, and "life" the Home page', () => {
    render(palette({ nav: NAV2 }));
    type("real estate");
    expect(options()[0]).toContain("Planning · House");
    type("life");
    expect(options()[0]).toContain("Home");
  });
  it('"overview" lists the old net-worth page (Home) before Cash Flow · Overview, which has the word in its label', () => {
    render(palette({ nav: NAV2 }));
    type("overview");
    const o = options();
    expect(o[0]).toContain("Home");
    expect(o[1]).toContain("Cash Flow · Overview");
  });
  it("an alias is matched, never shown", () => {
    render(palette({ nav: NAV2 }));
    type("tools");
    expect(options()[0]).not.toContain("Tools");
  });
  it("an account name finds the account, hints what it is, and opens /accounts/:id", () => {
    render(<MemoryRouter><CommandPalette open onOpenChange={() => {}} nav={NAV2} /><Where /></MemoryRouter>);
    type("travel card");
    const o = options();
    expect(o).toHaveLength(1);
    expect(o[0]).toContain("Travel Card ...9876");
    expect(o[0]).toContain("account");
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    expect(where()).toBe("/accounts/card_9|null");
  });
  it("accounts come after the pages and before the categories and vendors", () => {
    render(palette({ nav: [...NAV2, { to: "/data", label: "Data", icon: "⚙" }] }));
    type("d");                                   // Data, Everyday Checking, Travel Card, Dining and Vendor A all contain a "d"
    const o = options();
    const at = (s: string) => o.findIndex((t) => t.includes(s));
    expect(at("Data")).toBeLessThan(at("Everyday Checking"));
    expect(at("Travel Card")).toBeLessThan(at("Dining"));
    expect(at("Dining")).toBeLessThan(at("Vendor A"));
  });
});

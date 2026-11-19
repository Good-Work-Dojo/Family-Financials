// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// Explore with nothing to chart: every chart card keeps its frame and heading and says one line in place of empty axes,
// a white block, a row of zero bars or a calendar of empty days - what is missing, and the one thing that brings it.
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NO_TRANSACTIONS, NO_TRANSACTIONS_YET } from "../components/NoData";
import type { AppData } from "../data/types";
import { firstWeekData, txFactory } from "../lib/__fixtures__";

const state = vi.hoisted(() => ({ data: null as unknown, charts: [] as unknown[],
  clicks: new Map<unknown, (params: unknown) => void>() }));
vi.mock("../data/DataContext", async (importOriginal) => {
  const real = await importOriginal<typeof import("../data/DataContext")>();
  // one index per data object, as the provider keeps it
  const indexes = new WeakMap<object, ReturnType<typeof real.buildIndexes>>();
  const indexOf = (d: AppData) => {
    if (!indexes.has(d)) indexes.set(d, real.buildIndexes(d.transactions, d.purchases, d.manifest.generated_at));
    return indexes.get(d)!;
  };
  return { ...real, useData: () => ({ data: state.data, indexes: indexOf(state.data as AppData), missing: new Set<string>() }) };
});
vi.mock("../components/EChart", () => ({
  default: (p: { option: unknown; onClick?: (params: unknown) => void }) => {
    state.charts.push(p.option);
    if (p.onClick) state.clicks.set(p.option, p.onClick);
    return <div data-testid="chart" />;
  },
}));
// the filter bar has its own suite (components/FilterBar.test.tsx)
vi.mock("../components/FilterBar", () => ({ default: () => null }));

import Spending from "./Spending";

afterEach(() => { cleanup(); state.charts = []; state.clicks.clear(); });

const CARDS = ["card-spend-by-category", "card-top-vendors", "card-category-breakdown", "card-period-compare",
  "card-weekday-spend", "card-spend-calendar"];
/** Explore at `search` (a date in it, so the six-month default stays out of the way) */
const at = (data: AppData, search: string) => {
  state.data = data;
  return render(<MemoryRouter initialEntries={[`/spending?${search}`]}><Spending /></MemoryRouter>);
};
const NO_SPENDING = "No spending matches these dates and filters. Widen the dates or clear a filter.";

describe("Explore with nothing to chart", () => {
  it("a window with no spending in it: each chart card keeps its heading and says so in one line, with no chart", () => {
    at(firstWeekData(), "from=2026-01-01&to=2026-01-31");
    for (const id of CARDS) {
      const card = screen.getByTestId(id);
      expect(card.querySelector("h2")?.textContent, id).not.toBe("");
      expect(within(card).queryByTestId("chart"), `${id} draws no chart`).toBeNull();
      expect(within(card).getByTestId("empty-state").textContent, id).toBe(NO_SPENDING);
      expect(card.querySelector("table"), `${id} has no empty table`).toBeNull();
    }
    expect(state.charts, "no chart option is built for nothing").toEqual([]);
  });
  it("no transaction at all: the first card says the long line, the others the short one", () => {
    at({ ...firstWeekData(), transactions: [], purchases: [], cashflow: [] }, "from=2026-01-01");
    expect(within(screen.getByTestId(CARDS[0])).getByTestId("no-transactions").textContent).toBe(NO_TRANSACTIONS);
    for (const id of CARDS.slice(1)) {
      expect(within(screen.getByTestId(id)).getByTestId("no-transactions").textContent, id).toBe(NO_TRANSACTIONS_YET);
      expect(within(screen.getByTestId(id)).queryByTestId("chart"), id).toBeNull();
    }
    expect((document.body.textContent ?? "").split(NO_TRANSACTIONS).length - 1, "the long line once").toBe(1);
  });
  it("a window with spending: every chart is drawn and no card says it is empty", () => {
    at(firstWeekData(), "from=2026-06-01");
    for (const id of CARDS.filter((c) => c !== "card-period-compare" && c !== "card-weekday-spend")) {
      expect(within(screen.getByTestId(id)).getByTestId("chart"), id).toBeDefined();
    }
    expect(screen.queryByTestId("empty-state")).toBeNull();
  });
  it("a category that stopped two years before the window's end: the calendar draws its last months, never 'no spending'", () => {
    const base = firstWeekData();
    const old = txFactory({ account: "chk", account_name: "Checking", category: "Dining", subcategory: "Coffee", vendor: "Old Cafe" });
    const rows = [old({ date: "2024-03-02", amount: -12 }), old({ date: "2024-05-20", amount: -18 })];
    const data = { ...base, transactions: [...rows, ...base.transactions.filter((t) => t.category !== "Dining")],
      purchases: [...rows, ...base.purchases.filter((t) => t.category !== "Dining")] };
    at(data, "categories=Dining&from=2024-01-01");
    const card = screen.getByTestId("card-spend-calendar");
    expect(within(card).queryByTestId("empty-state")).toBeNull();
    expect(within(card).getByTestId("chart")).toBeDefined();
    expect(card.querySelector("h2")?.textContent).toMatch(/^Daily spend calendar \(Mar 2024 – May 2024\)/);
    const cal = (state.charts.filter((o: any) => o.calendar).pop() as any).calendar;
    expect(cal.range).toEqual(["2024-03-01", "2024-05-31"]);
  });
  it("the treemap's breadcrumb sits in a strip under the tiles, never over them, at any chart height", () => {
    at(firstWeekData(), "from=2026-06-01");
    const tree = (state.charts.find((o: any) => o.series?.[0]?.type === "treemap") as any).series[0];
    // the tiles' box is the chart less its edges (no width or height of its own, which ECharts would centre at 80%)
    expect([tree.width, tree.height, tree.top]).toEqual([null, null, 0]);
    expect(tree.breadcrumb.show).toBe(true);
    expect(tree.bottom).toBeGreaterThan(tree.breadcrumb.bottom + tree.breadcrumb.height);
    // the root crumb names where you are: the series' name, which a label rule of the tiles no longer hides
    expect(tree.name).toBe("All categories");
  });
  it("the treemap draws a new window at once: its tiles never keep the last window's sizes under the new amounts", () => {
    at(firstWeekData(), "from=2026-06-01");
    const tree = (state.charts.find((o: any) => o.series?.[0]?.type === "treemap") as any).series[0];
    expect(tree.animation, "a treemap's own default animates a new layout for 900ms while its labels change at once")
      .toBe(false);
    // a tile's area is its value, the value its label prints
    const dining = tree.data.find((d: { name: string }) => d.name === "Dining");
    expect(dining.value).toBe(65);
    expect(tree.label.formatter({ name: "Dining", value: dining.value, dataIndex: undefined, treePathInfo: [{ value: 115 }, dining] }))
      .toBe("Dining\n$65");
  });
  it("the calendar says a blank day had no spending, and its tooltip names a day as a date", () => {
    at(firstWeekData(), "from=2026-06-01");
    expect(screen.getByTestId("calendar-blank").textContent).toBe("A blank day had no spending; the scale colours the days that had some.");
    const cal = state.charts.filter((o: any) => o.calendar).pop() as any;
    expect(cal.tooltip.formatter({ value: ["2026-06-09", 65] })).toBe("Jun 9, 2026: $65");
  });
  it("a tile too small for a label is still named: its tooltip gives its name and amount, and a tap filters to it", () => {
    at(firstWeekData(), "from=2026-06-01");
    const option = state.charts.find((o: any) => o.series?.[0]?.type === "treemap") as any;
    expect(option.tooltip.valueFormatter(65)).toBe("$65");
    act(() => state.clicks.get(option)!({ name: "Dining", treePathInfo: [{ name: "All categories" }, { name: "Dining" }] }));
    const after = (state.charts.filter((o: any) => o.series?.[0]?.type === "treemap").pop() as any).series[0];
    expect(after.data.map((d: { name: string; value: number }) => [d.name, d.value]), "the tapped category alone, at its amount")
      .toEqual([["Dining", 65]]);
  });
  it("the weekday card waits for four weeks of data in the window: the first week's four days are no weekday averages", () => {
    // the data begins Sat Jun 6 and reaches Tue Jun 9 (the ingest ran Jun 10): Sat, Sun, Mon and Tue once each
    at(firstWeekData(), "from=2026-06-01");
    const card = screen.getByTestId("card-weekday-spend");
    expect(within(card).queryByTestId("chart")).toBeNull();
    expect(state.charts.some((o: any) => o.xAxis?.data?.[0] === "Sun"), "no weekday bars are built").toBe(false);
    expect(within(card).getByTestId("not-enough-history").textContent).toBe("Not enough history yet — the averages need 4 weeks "
      + "of data in this window, so each weekday is seen four times; it holds 4 days.");
  });
  it("with four weeks of data in the window the weekday card draws each weekday's average over its own days", () => {
    // one more row on Tue May 12: the window's days run May 12 to Jun 9, 29 days, every weekday four times (Tuesday five)
    const d = firstWeekData();
    const may = { ...d.transactions[0], id: "may", date: "2026-05-12", amount: -50 };
    at({ ...d, transactions: [may, ...d.transactions], purchases: [may, ...d.purchases] }, "from=2026-05-01");
    const card = screen.getByTestId("card-weekday-spend");
    expect(within(card).getByTestId("chart")).toBeDefined();
    const bars = state.charts.filter((o: any) => o.xAxis?.data?.[0] === "Sun").pop() as any;
    expect(bars.series[0].data.every((x: { value: number | null }) => x.value !== null), "every weekday has days").toBe(true);
    expect(bars.series[0].data[2].value, "Tuesday: May 12's 50 and Jun 9's 65 over its five Tuesdays").toBe(23);
  });
  it("a month set against one that ends before the data begins: not enough history, never a column of $0", () => {
    // the first-week household's data begins Jun 6: June set against May has nothing on May's side
    at(firstWeekData(), "from=2026-06-01");
    const card = screen.getByTestId("card-period-compare");
    expect(card.querySelector("table")).toBeNull();
    expect(card.querySelector("h2")?.textContent, "the title names no comparison").toBe("Month-over-month");
    expect(within(card).getByTestId("not-enough-history").textContent)
      .toBe("Not enough history yet — the data begins Jun 6, 2026, after May 2026, the month Jun 2026 would be set against.");
  });
  it("a month set against one the data holds: the table", () => {
    // June 1-9 is set against May 1-9 (month to date), which the row of May 4 is in
    const d = firstWeekData();
    const may = { ...d.transactions[0], id: "may", date: "2026-05-04" };
    at({ ...d, transactions: [may, ...d.transactions], purchases: [may, ...d.purchases] }, "from=2026-05-01");
    expect(screen.getByTestId("card-period-compare").querySelector("table")).not.toBeNull();
    expect(screen.getByTestId("card-period-compare").querySelector("h2")?.textContent).toMatch(/^Month-over-month: Jun 2026 vs May 2026/);
  });
});

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BudgetGrid } from "../lib/budgets";
import { vocabAssumptions } from "../lib/__fixtures__";
import { categoryVocab } from "../lib/categories";
import { MASK, setPrivate } from "../lib/privacy";
import { NO_TRANSACTIONS_YET, NOT_ENOUGH_HISTORY } from "./NoData";

const V = categoryVocab(vocabAssumptions());
const chart = vi.hoisted(() => ({ options: [] as any[] }));
vi.mock("./EChart", () => ({
  default: ({ option }: { option: unknown }) => { chart.options.push(option); return <div data-testid="chart" />; },
}));
import { BudgetGridTable, FixedVsDiscretionaryCard, trendWord } from "./BudgetGrid";

beforeEach(() => { chart.options = []; });
afterEach(() => { cleanup(); setPrivate(false); });
const cell = (month: string, baseline: number, actual: number) => ({ month, baseline, actual, variance: actual - baseline });
const grid: BudgetGrid = {
  months: ["2025-12", "2026-01"],
  rows: [
    { category: "Dining", source: "auto", cells: [cell("2025-12", 600, 900), cell("2026-01", 650, 1300)], slope: 400 },
    { category: "Groceries", source: "set", cells: [cell("2025-12", 900, 880), cell("2026-01", 900, 910)], slope: 30 },
  ],
  fixedVsDiscretionary: [{ month: "2025-12", fixed: 2400, discretionary: 900 }, { month: "2026-01", fixed: 2500, discretionary: 1300 }],
  toggledRows: 0,
};
const link = (c: string, m: string) => `#/spending/transactions?categories=${c}&from=${m}-01`;
// the Category cell is a <th scope="row"> (a row header, not a "cell"), so cells[0] is the first month
const cellsOf = (name: RegExp) => within(screen.getByRole("row", { name })).getAllByRole("cell");

describe("BudgetGridTable", () => {
  it("one column per month plus trend, one row per category, cells show the actual with the baseline in the title", () => {
    render(<BudgetGridTable grid={grid} linkFor={link} />);
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["Category", "Dec 2025", "Jan 2026", "Trend"]);
    const dining = screen.getByRole("row", { name: /Dining/ });
    const cells = within(dining).getAllByRole("cell");
    expect(cells).toHaveLength(3);                                  // Dec, Jan, Trend
    expect(cells[1].textContent).toContain("$1,300");
    expect(cells[1].getAttribute("title")).toBe("baseline $650 · +$650 over");
    expect(cells[1].className).toMatch(/red/);                      // over budget
    expect(cellsOf(/Groceries/)[0].className).toMatch(/emerald/);   // under
    expect(within(dining).getByRole("link").getAttribute("href")).toBe("#/spending/transactions?categories=Dining&from=2026-01-01");
    expect(screen.getByText("pinned")).toBeDefined();               // the pinned badge, in the words the Budget card uses
    expect(screen.queryByText("set")).toBeNull();
  });

  it("shows the signed variance as a second, muted line under each actual - visible, not hover-only", () => {
    render(<BudgetGridTable grid={grid} linkFor={link} />);
    const line = (c: Element) => c.querySelector('[aria-hidden="true"]');
    expect(line(cellsOf(/Dining/)[1])!.textContent).toBe("+$650");           // over: signed with +
    expect(line(cellsOf(/Groceries/)[0])!.textContent).toBe("−$20");        // under: signed with −, not colour alone
    // muted as fine print (the faint token, at least 4.5:1 in both themes: lib/contrast.test.ts), the amount keeps the colour
    expect(line(cellsOf(/Dining/)[1])!.className).toMatch(/\btext-faint\b/);
    expect(cellsOf(/Dining/)[1].className).toMatch(/red/);
    // the amount and its variance are both in the cell's text for everyone, and spoken once in words
    expect(cellsOf(/Dining/)[1].textContent).toContain("$1,300");
    expect(cellsOf(/Dining/)[1].textContent).toContain("(+$650 over)");
    expect(cellsOf(/Groceries/)[0].textContent).toContain("($20 under)");
  });

  it("a zero variance reads $0 (not a signed zero), and the dash cell has no variance line at all", () => {
    const g: BudgetGrid = { ...grid, rows: [
      { category: "Pledge", source: "set", slope: 0, cells: [cell("2025-12", 100, 100), cell("2026-01", 0, 0)] },
      { category: "Gifts", source: "auto", slope: 0, cells: [cell("2025-12", 0, 0), cell("2026-01", 0, 120)] },
    ] };
    render(<BudgetGridTable grid={g} linkFor={link} />);
    expect(cellsOf(/Pledge/)[0].querySelector('[aria-hidden="true"]')!.textContent).toBe("$0");
    expect(cellsOf(/Gifts/)[0].querySelector('[aria-hidden="true"]') === null, "the — cell shows no variance").toBe(true);
    expect(cellsOf(/Gifts/)[1].querySelector('[aria-hidden="true"]')!.textContent).toBe("+$120");
  });

  it("names the trend in words for a screen reader (rising / flat / falling)", () => {
    const falling = { category: "Travel", source: "auto" as const, slope: -300,
      cells: [cell("2025-12", 500, 1500), cell("2026-01", 500, 900)] };
    render(<BudgetGridTable grid={{ ...grid, rows: [...grid.rows, falling] }} linkFor={() => "#/spending/transactions"} />);
    expect(within(screen.getByRole("row", { name: /Dining/ })).getByText("rising")).toBeDefined();
    expect(within(screen.getByRole("row", { name: /Groceries/ })).getByText("flat")).toBeDefined();
    expect(within(screen.getByRole("row", { name: /Travel/ })).getByText("falling")).toBeDefined();
  });

  it("trendWord compares the slope with the row's own mean: 5% a month and under is flat", () => {
    const row = (slope: number, ...actuals: number[]) =>
      ({ category: "X", source: "auto" as const, slope, cells: actuals.map((a, i) => cell(`2026-0${i + 1}`, 0, a)) });
    expect(trendWord(row(49, 1000, 1000))).toBe("flat");
    expect(trendWord(row(51, 1000, 1000))).toBe("rising");
    expect(trendWord(row(-51, 1000, 1000))).toBe("falling");
    expect(trendWord(row(10, 0, 0)), "no spend at all: nothing to trend").toBe("flat");
  });

  it("a cell with no baseline and no spend reads '—', not a made-up $0 — but a pinned $0 budget is a real $0", () => {
    const g: BudgetGrid = { ...grid, rows: [
      { category: "Gifts", source: "auto", slope: 0, cells: [cell("2025-12", 0, 0), cell("2026-01", 0, 120)] },
      { category: "Pledge", source: "set", slope: 0, cells: [cell("2025-12", 0, 0), cell("2026-01", 0, 0)] },
    ] };
    render(<BudgetGridTable grid={g} linkFor={link} />);
    const gifts = cellsOf(/Gifts/);
    expect(gifts[0].textContent).toBe("—");
    expect(gifts[0].getAttribute("title")).toBe("no baseline and nothing spent");
    expect(gifts[1].textContent).toContain("$120");                 // new category, real spend against a typical $0
    expect(gifts[1].getAttribute("title")).toBe("baseline $0 · +$120 over");
    expect(cellsOf(/Pledge/)[0].textContent).toContain("$0");
  });

  it("the category link is a 40px tap target on a phone (and plain text-height from md)", () => {
    render(<BudgetGridTable grid={grid} linkFor={link} />);
    const cls = within(screen.getByRole("row", { name: /Dining/ })).getByRole("link").className.split(/\s+/);
    expect(cls).toContain("min-h-[40px]");
    expect(cls).toContain("md:min-h-0");
  });

  it("opens on the top 15 categories and a control shows every one - nothing is hidden behind a note", () => {
    const many = Array.from({ length: 18 }, (_, i) =>
      ({ category: `Cat ${i}`, source: "auto" as const, slope: 0, cells: [cell("2025-12", 100, 100), cell("2026-01", 100, 100)] }));
    render(<BudgetGridTable grid={{ ...grid, rows: many }} linkFor={link} />);
    expect(screen.getAllByRole("row")).toHaveLength(1 + 15);        // header + 15
    expect(screen.queryByText("Cat 15") === null).toBe(true);
    const more = screen.getByRole("button", { name: "Show all 18 categories" });
    expect(more.getAttribute("aria-expanded")).toBe("false");
    expect(more.className).toMatch(/min-h-\[40px\]/);              // a 40px tap target on a phone
    fireEvent.click(more);
    expect(screen.getAllByRole("row")).toHaveLength(1 + 18);
    expect(screen.getByText("Cat 17")).toBeDefined();
    const less = screen.getByRole("button", { name: "Show the top 15" });
    expect(less.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(less);
    expect(screen.getAllByRole("row")).toHaveLength(1 + 15);
  });

  it("15 categories or fewer have no control at all", () => {
    render(<BudgetGridTable grid={grid} linkFor={link} />);
    expect(screen.queryByRole("button") === null).toBe(true);
  });

  it("the table's caption says one full month in the singular", () => {
    const one: BudgetGrid = { ...grid, months: ["2026-01"], rows: grid.rows.map((r) => ({ ...r, cells: [r.cells[1]] })) };
    const { container, rerender } = render(<BudgetGridTable grid={one} linkFor={link} />);
    expect(container.querySelector("caption")!.textContent).toContain("the last full month");
    expect(container.querySelector("caption")!.textContent).not.toMatch(/full months/);
    rerender(<BudgetGridTable grid={grid} linkFor={link} />);
    expect(container.querySelector("caption")!.textContent).toContain("the last 2 full months");
  });

  it("scrolls inside its own box rather than widening the page", () => {
    const { container } = render(<BudgetGridTable grid={grid} linkFor={link} />);
    expect(container.querySelector(".overflow-x-auto table") !== null, "the table sits in a TableScroll").toBe(true);
  });
});

describe("FixedVsDiscretionaryCard", () => {
  it("renders a chart of the two stacked series, rounded to whole dollars, one bar per month", () => {
    render(<FixedVsDiscretionaryCard vocab={V} grid={{ ...grid, fixedVsDiscretionary: [
      { month: "2025-12", fixed: 2400.4, discretionary: 899.6 }, { month: "2026-01", fixed: 2500, discretionary: 1300 }] }} />);
    expect(screen.getByTestId("card-fixed-vs-discretionary")).toBeDefined();
    expect(screen.getByTestId("chart")).toBeDefined();
    const opt = chart.options.at(-1);
    expect(opt.xAxis.data).toEqual(["2025-12", "2026-01"]);
    expect(opt.series.map((s: any) => [s.name, s.type, s.stack])).toEqual([
      ["Fixed (essential by default)", "bar", "burn"], ["Discretionary", "bar", "burn"]]);
    expect(opt.series[0].data).toEqual([2400, 2500]);
    expect(opt.series[1].data).toEqual([900, 1300]);
  });

  it("says what fixed means, from the emergency-fund list itself — not a hand-copied one", () => {
    render(<FixedVsDiscretionaryCard vocab={V} grid={grid} />);
    const note = screen.getByText(/essential by default/);
    const { categories, pairs } = V.essentialLabels();
    for (const c of categories) expect(note.textContent, c).toContain(c);
    for (const p of pairs) expect(note.textContent, p).toContain(p);
  });

  it("says how many rows Planning's toggles actually moved: none, or N rows (singular and plural)", () => {
    const { rerender } = render(<FixedVsDiscretionaryCard vocab={V} grid={grid} />);
    expect(screen.getByText(/Toggle rows on Planning/)).toBeDefined();
    rerender(<FixedVsDiscretionaryCard vocab={V} grid={{ ...grid, toggledRows: 1 }} />);
    expect(screen.getByText("1 row toggled on Planning's emergency-fund card (this device only) moves between fixed and discretionary here.")).toBeDefined();
    rerender(<FixedVsDiscretionaryCard vocab={V} grid={{ ...grid, toggledRows: 3 }} />);
    expect(screen.getByText(/3 rows toggled on Planning.*move between fixed and discretionary/)).toBeDefined();
  });

  describe("with nothing to draw, one line in the order of what is missing", () => {
    const split = () => screen.getByTestId("card-fixed-vs-discretionary");
    const shows = (line: string) => {
      expect(split().textContent).toBe(`Fixed vs discretionary spend${line}`);
      expect(within(split()).queryByTestId("chart")).toBeNull();
      expect(chart.options).toEqual([]);
    };
    it("no transaction at all: the shared no-transactions line, short (the budget card above it says it in full)", () => {
      render(<FixedVsDiscretionaryCard vocab={V} grid={{ months: [], rows: [], fixedVsDiscretionary: [], toggledRows: 0 }} noTransactions />);
      shows(NO_TRANSACTIONS_YET);
    });
    it("transactions but no full month yet: not enough history", () => {
      render(<FixedVsDiscretionaryCard vocab={V} grid={{ months: [], rows: [], fixedVsDiscretionary: [], toggledRows: 0 }} />);
      shows(`${NOT_ENOUGH_HISTORY} — fixed and discretionary spend appear month by month with the budget grid above, once two full months are in.`);
    });
    it("full months with no spending in them", () => {
      render(<FixedVsDiscretionaryCard vocab={V} grid={{ ...grid, rows: [], fixedVsDiscretionary: [
        { month: "2025-12", fixed: 0, discretionary: 0 }, { month: "2026-01", fixed: 0, discretionary: 0 }] }} />);
      shows("No spending in these months yet.");
    });
    it("a split that exists is drawn even when no category reaches the budget grid's floor (no row)", () => {
      render(<FixedVsDiscretionaryCard vocab={V} grid={{ ...grid, rows: [], fixedVsDiscretionary: [
        { month: "2025-12", fixed: 12, discretionary: 30 }, { month: "2026-01", fixed: 0, discretionary: 0 }] }} />);
      expect(within(split()).getByTestId("chart")).toBeDefined();
      expect(chart.options.at(-1).series[1].data).toEqual([30, 0]);
    });
  });

  it("names the fixed series by what it is: 'essential by default' only while no toggle changes it", () => {
    const names = (g: BudgetGrid) => {
      chart.options = [];
      const { unmount } = render(<FixedVsDiscretionaryCard vocab={V} grid={g} />);
      const n = chart.options.at(-1).series.map((x: any) => x.name);
      unmount();
      return n;
    };
    expect(names(grid)).toEqual(["Fixed (essential by default)", "Discretionary"]);
    expect(names({ ...grid, toggledRows: 2 })).toEqual(["Fixed (essential as marked)", "Discretionary"]);
  });
});

// privacy mode: the grid is the money surface with the most amounts on it - cell, variance line, spoken text, title
describe("the budget grid under privacy mode", () => {
  it("masks every amount in a cell, its signed variance line, its spoken variance and its baseline title; the month labels stay", () => {
    setPrivate(true);
    render(<BudgetGridTable grid={grid} linkFor={link} />);
    const text = screen.getByRole("table").textContent ?? "";
    expect(/\$\d/.test(text), "no dollar figure in the table text").toBe(false);
    expect(text.includes(MASK)).toBe(true);
    expect(cellsOf(/Dining/)[1].getAttribute("title")).toBe(`baseline ${MASK} \u00b7 ${MASK} over`);
    const titles = [...screen.getByRole("table").querySelectorAll("[title]")].map((e) => e.getAttribute("title") ?? "");
    expect(titles.some((t) => /\$\d/.test(t)), "no dollar figure in a title").toBe(false);
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Category", "Dec 2025", "Jan 2026", "Trend"]);
  });
  it("the fixed-vs-discretionary chart prints its axis and tooltip through the masking formatters", () => {
    setPrivate(true);
    render(<FixedVsDiscretionaryCard vocab={V} grid={grid} />);
    const option = chart.options[chart.options.length - 1];
    expect(option.yAxis.axisLabel.formatter(2500)).toBe(MASK);
    expect(option.tooltip.valueFormatter(2500)).toBe(MASK);
    expect(option.tooltip.valueFormatter(undefined), "a gap is a dash, not an amount").toBe("\u2014");
    expect(/\$\d/.test(screen.getByTestId("card-fixed-vs-discretionary").textContent ?? "")).toBe(false);
  });
});

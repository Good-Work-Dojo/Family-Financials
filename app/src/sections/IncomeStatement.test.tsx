// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// The household P&L's columns follow the data: the selected window, the last full calendar year the cash-flow months
// hold (absent when no year is whole) and the year to date. Synthetic months only.
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AppData, CashflowMonth } from "../data/types";
import { acctFactory, appDataFactory, cfmFactory, snapFactory } from "../lib/__fixtures__";
import { nextMonth } from "../lib/agg";
import { fmtUSD0 } from "../lib/format";

vi.mock("../components/EChart", () => ({ default: () => <div data-testid="chart" /> }));
const base = appDataFactory(snapFactory({}))({});
let data: AppData = base;
vi.mock("../data/DataContext", () => ({
  useData: () => ({ data, missing: new Set<string>(),
    indexes: { dataRange: { first: "2029-01-01", last: "2031-01-15" }, childrenByParent: new Map() } }),
}));

import IncomeStatement from "./IncomeStatement";

afterEach(() => { cleanup(); data = base; });
const cfm = cfmFactory({ income: 10000, spend: 8000, savings_rate: 0.2 });
/** one month each from `from` through `to` (the last is the month in progress), in coverage unless listed */
const months = (from: string, to: string, out: string[] = []): CashflowMonth[] => {
  const ms: CashflowMonth[] = [];
  for (let m = from; m <= to; m = nextMonth(m)) ms.push(cfm({ month: m, in_coverage: !out.includes(m) }));
  return ms;
};
/** a household holding `cashflow` whose ingest ran in its last month, so that month is the month in progress */
const holding = (cashflow: CashflowMonth[]): AppData => ({ ...base, cashflow,
  manifest: { ...base.manifest, generated_at: `${cashflow[cashflow.length - 1].month}-15T09:00:00-05:00` } });
/** the P&L's column labels: a header's first text node (the month count follows in its own span) */
const columns = () => {
  render(<MemoryRouter initialEntries={["/cash-flow/income-statement"]}><IncomeStatement /></MemoryRouter>);
  return within(screen.getByTestId("card-pnl")).getAllByRole("columnheader")
    .map((h) => h.firstChild?.textContent ?? "").filter((l) => l !== "");
};

describe("Income statement: the calendar-year column follows the data", () => {
  it("is the one full calendar year the data holds", () => {
    data = holding(months("2029-01", "2030-06"));       // 2029 whole; June 2030 is the month in progress
    expect(columns()).toEqual(["Trailing 12 mo", "2029", "2030 YTD"]);
  });
  it("is the LAST full year when the data holds more than one", () => {
    data = holding(months("2028-01", "2030-04"));
    expect(columns()).toEqual(["Trailing 12 mo", "2029", "2030 YTD"]);
  });
  it("is absent when no calendar year is whole", () => {
    data = holding(months("2029-02", "2030-06"));       // January 2029 is missing
    expect(columns()).toEqual(["Trailing 12 mo", "2030 YTD"]);
  });
  it("a year whose December is the month in progress is not whole yet", () => {
    data = holding(months("2028-06", "2029-12"));       // December 2029 is still being filled
    expect(columns()).toEqual(["Trailing 12 mo", "2029 YTD"]);
  });
  it("a year whose December holds only a row dated ahead is not whole either: that month has not begun", () => {
    // the ingest ran in November 2029 and the bank dated one row into December
    data = { ...holding(months("2028-06", "2029-12")),
      manifest: { ...base.manifest, generated_at: "2029-11-30T20:00:00-05:00" } };
    expect(columns()).toEqual(["Trailing 12 mo", "2029 YTD"]);
    const ytd = within(screen.getByTestId("card-pnl")).getAllByRole("columnheader").find((h) => h.firstChild?.textContent === "2029 YTD");
    expect(ytd?.textContent, "January to October: November is in progress").toBe("2029 YTD10 mo");
  });
});

describe("Income statement with no full month", () => {
  it("the graph's card says it waits for a full month, and the P&L keeps its card with its own line and no table", () => {
    data = { ...base, cashflow: [] };
    render(<MemoryRouter initialEntries={["/cash-flow/income-statement"]}><IncomeStatement /></MemoryRouter>);
    expect(screen.getByTestId("card-sankey").textContent).toBe("Income statement — no full month of cash-flow data yet"
      + "The money-flow graph covers full months only; it appears once a full month of account coverage exists.");
    const pnl = screen.getByTestId("card-pnl");
    expect(pnl.textContent).toBe("Household P&L"
      + "No full month of cash flow yet. Income, spending and savings per window appear here once a full month of account "
      + "coverage exists.");
    expect(pnl.querySelector("table")).toBeNull();
    expect(screen.queryByTestId("chart")).toBeNull();
  });
});

describe("Income statement: the coverage note follows the data", () => {
  const note = () => {
    render(<MemoryRouter initialEntries={["/cash-flow/income-statement"]}><IncomeStatement /></MemoryRouter>);
    return screen.getByTestId("card-pnl").textContent ?? "";
  };
  it("names the first month in coverage", () => {
    data = holding(months("2029-01", "2030-06", ["2029-01", "2029-02"]));
    expect(note()).toContain("Months before account coverage (before Mar 2029) excluded from rates.");
  });
});

// A row that sums nothing is left out: "Other categories" only beside a category the table does not list, "Invested"
// only for a household that has an investment account or a transfer into one in these windows.
describe("Income statement: no row sums nothing", () => {
  const rowNames = () => {
    render(<MemoryRouter initialEntries={["/cash-flow/income-statement"]}><IncomeStatement /></MemoryRouter>);
    return [...screen.getByTestId("card-pnl").querySelectorAll("tbody tr")].map((r) => r.querySelector("td")?.textContent);
  };
  const byCat = (n: number) => Object.fromEntries(Array.from({ length: n }, (_, i) => [`Cat ${i + 1}`, { total: 100 * (i + 1), by_subcategory: {} }]));
  const withCats = (n: number, invested = 0) => holding(months("2030-01", "2030-06").map((m) => ({ ...m, by_category: byCat(n), invested })));
  it("nine categories and no investment account: no Other row and no Invested row", () => {
    data = withCats(9);
    const rows = rowNames();
    expect(rows).not.toContain("Other categories");
    expect(rows).not.toContain("Invested (transfers in)");
    expect(rows).toContain("Total spending");
  });
  it("ten categories: the tenth is in the Other row", () => {
    data = withCats(10);
    expect(rowNames()).toContain("Other categories");
  });
  it("an investment account: the Invested row, a true $0 included", () => {
    data = { ...withCats(3), accounts: [acctFactory()({ id: "brk", kind: "brokerage", asset_class: "Stocks/ETF" })] };
    expect(rowNames()).toContain("Invested (transfers in)");
  });
  it("money moved into an investment with no such account listed: the Invested row", () => {
    data = withCats(3, 500);
    expect(rowNames()).toContain("Invested (transfers in)");
  });
});

// The P&L reads the full months only (the coverage months before the month in progress): a month before the coverage
// floor is in no column, a window the data holds fewer months of is labelled with the months it has, and with no month
// in coverage there is no window at all.
describe("Income statement: the windows read the full months only", () => {
  const header = (label: string) => within(screen.getByTestId("card-pnl")).getAllByRole("columnheader")
    .find((h) => h.firstChild?.textContent === label)?.textContent;
  it("no month in coverage: the empty state, no table and no figure, however many months the export holds", () => {
    data = holding(months("2029-01", "2030-06").map((m) => ({ ...m, in_coverage: false })));
    render(<MemoryRouter initialEntries={["/cash-flow/income-statement"]}><IncomeStatement /></MemoryRouter>);
    const pnl = screen.getByTestId("card-pnl");
    expect(pnl.querySelector("table")).toBeNull();
    expect(pnl.textContent).not.toMatch(/\$\d/);
    expect(screen.getByTestId("card-sankey").textContent).toMatch(/no full month of cash-flow data yet/);
  });
  it("a year whose first months are before the coverage floor is no calendar-year column, and no column counts them", () => {
    data = holding(months("2029-01", "2030-06", ["2029-01", "2029-02"]));     // the floor is Mar 2029
    expect(columns()).toEqual(["Trailing 12 mo", "2030 YTD"]);
    expect(header("Trailing 12 mo"), "the label counts the months, so no line under it counts them again").toBe("Trailing 12 mo");
    expect(header("2030 YTD"), "a year's column says its months under its label").toBe("2030 YTD5 mo");
  });
  it("one full month: the window is labelled with its one month, the title says one month, and one column stands for both", () => {
    data = holding(months("2030-05", "2030-06"));                             // May full, June in progress
    expect(columns()).toEqual(["Trailing 1 mo"]);
    expect(header("Trailing 1 mo"), "the months said once, while 12M is selected").toBe("Trailing 1 mo");
    expect(screen.getByTestId("card-sankey").querySelector("h2")?.textContent).toBe("Where the money goes — trailing 1 month ($10,000 in)");
  });
  it("a window of 3 over three full months keeps its own label", () => {
    data = holding(months("2030-03", "2030-06"));
    render(<MemoryRouter initialEntries={["/cash-flow/income-statement?pl=3m"]}><IncomeStatement /></MemoryRouter>);
    expect(within(screen.getByTestId("card-pnl")).getAllByRole("columnheader")[1].textContent).toBe("Trailing 3 mo");
    expect(screen.getByTestId("card-sankey").querySelector("h2")?.textContent).toBe("Where the money goes — trailing 3 months ($30,000 in)");
  });
});

// Interest as the only income (the no-income demo household's shape): the P&L prints no rate below −100%; its row reads
// "—" and the line under the table gives each such column's shortfall in dollars. Net savings below zero is red.
describe("Income statement when spending is over twice the income", () => {
  const rateRow = () => [...screen.getByTestId("pnl-savings-rate").querySelectorAll("td")].slice(1).map((c) => c.textContent);
  const netCells = () => [...within(screen.getByTestId("card-pnl")).getByText("Net savings").closest("tr")!.querySelectorAll("td")].slice(1);
  const mount = () => render(<MemoryRouter initialEntries={["/cash-flow/income-statement"]}><IncomeStatement /></MemoryRouter>);
  it("the rate row is \"—\" and the line under the table says the shortfall a month; net savings is not green", () => {
    data = holding(months("2029-01", "2030-06").map((m) => ({ ...m, income: 185, spend: 7_900, savings_rate: (185 - 7_900) / 185 })));
    mount();
    expect(rateRow()).toEqual(["—", "—", "—"]);
    expect(screen.getByTestId("pnl-shortfall").textContent).toBe(`Savings rate — where income did not cover spending: ${fmtUSD0(185 - 7_900)} a month `
      + `(Trailing 12 mo), ${fmtUSD0(185 - 7_900)} a month (2029), ${fmtUSD0(185 - 7_900)} a month (2030 YTD).`);
    expect(screen.getByTestId("card-pnl").textContent).not.toMatch(/\d%/);
    for (const c of netCells()) {
      expect(c.className).toContain("text-red-400");
      expect(c.className).not.toContain("text-emerald-400");
    }
  });
  it("at −100% the rate prints and no line is added; a positive net savings stays green", () => {
    data = holding(months("2029-01", "2030-06").map((m) => ({ ...m, income: 185, spend: 370, savings_rate: -1 })));
    mount();
    expect(rateRow()).toEqual(["−100%", "−100%", "−100%"]);
    expect(screen.queryByTestId("pnl-shortfall")).toBeNull();
    cleanup();
    data = holding(months("2029-01", "2030-06"));
    mount();
    for (const c of netCells()) expect(c.className).toContain("text-emerald-400");
  });
});

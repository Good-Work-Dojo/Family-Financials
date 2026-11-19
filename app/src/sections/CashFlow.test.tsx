// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NO_TRANSACTIONS, NO_TRANSACTIONS_YET } from "../components/NoData";
import type { IncomePhase, Transaction } from "../data/types";
import { cfmFactory, txFactory } from "../lib/__fixtures__";
import { SHIPPED_RESERVED } from "../lib/categories";
import { CHART_TOTAL_RATE, setChartTheme } from "../lib/echarts";
import { fmtPct, fmtUSD0 } from "../lib/format";
import { incomeStreams } from "../lib/lifeSummary";
import type { PayrollPhase } from "../lib/prefs";
import { STREAM_PALETTE } from "../lib/tokens";

// synthetic months: a consulting stream that ends in 2025 and a salary that starts in 2026, so an earlier window holds a
// stream the newest months do not, and the newest months hold one the earlier window does not
const cfm = cfmFactory({ accounts_covered: ["a", "b", "c", "d", "e"] });
const months = [
  ...["2025-01", "2025-02", "2025-03"].map((month) => cfm({ month, income_by_subcategory: { "Consulting - Sam": 9000, Bonus: 0 } })),
  ...["2026-04", "2026-05", "2026-06", "2026-07"].map((month) => cfm({ month, income_by_subcategory: { "Salary - Alex": 12000 } })),
];
// the ingest ran in July 2026, the newest month holding a row: July is the month in progress
const data = { cashflow: months, transactions: [] as Transaction[],
  manifest: { generated_at: "2026-07-21T09:00:00-05:00" },
  assumptions: { income_phases: [] as IncomePhase[], category_renames: [], reserved: SHIPPED_RESERVED } };
// the data's date range; a test sets it to null for a household with no transaction yet
const RANGE = { first: "2025-01-01", last: "2026-07-20" };
let dataRange: typeof RANGE | null = RANGE;
// the payroll facts entered in the app (prefs): none unless a test enters some
let payrollPhases: PayrollPhase[] = [];
vi.mock("../data/DataContext", () => ({
  useData: () => ({ data, prefs: { doc: { payroll: { phases: payrollPhases } } }, indexes: { dataRange, dataThrough: dataRange?.last ?? null },
    missing: new Set() }),
}));
const charts = vi.hoisted(() => ({ pairs: [] as { option: any; table: any }[] }));
vi.mock("../components/EChart", () => ({
  default: ({ option, tableData }: { option: any; tableData?: any }) => { charts.pairs.push({ option, table: tableData }); return <div data-testid="chart" />; },
}));
import CashFlow from "./CashFlow";

afterEach(() => { cleanup(); charts.pairs.length = 0; data.cashflow = months; dataRange = RANGE; payrollPhases = []; });
const income = () => charts.pairs.filter((p) => p.option?.series?.[0]?.stack === "income").pop()!;
const mount = (search: string) => render(<MemoryRouter initialEntries={[`/cash-flow${search}`]}><CashFlow /></MemoryRouter>);

// With no transaction yet the page hands its date bar the reason no preset that counts back resolves: those chips are
// disabled and say why, and All, which needs no anchor date, stays enabled and lit.
describe("Cash Flow: the period chips with no transaction yet", () => {
  it("6M, 12M and 24M are disabled with the reason; All is enabled and lit", () => {
    dataRange = null;
    data.cashflow = [];
    mount("");
    const chip = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;
    for (const name of ["6M", "12M", "24M"]) expect([chip(name).disabled, chip(name).title], name).toEqual([true, "No transactions yet"]);
    expect([chip("All").disabled, chip("All").title, chip("All").className.includes("bg-zinc-800")]).toEqual([false, "", true]);
  });
});

describe("Cash Flow: income by source", () => {
  it("a window that ends before the newest months draws only the streams with income in it - no empty legend entry", () => {
    mount("?from=2025-01-01&to=2025-03-31");
    const { option, table } = income();
    expect(option.series.map((s: any) => s.name), "Bonus is all zero; the 2026 salary has nothing in the window").toEqual(["Consulting - Sam"]);
    expect(table.columns).toEqual(["Period", "Consulting - Sam"]);
    expect(table.rows.map((r: any[]) => r[0])).toEqual(["2025-01", "2025-02", "2025-03", "3-month average, 2026-04 to 2026-06"]);
    expect(table.rows[3][1], "the consulting stream earned nothing in those 3 months").toBe(0);
  });

  it("the default window ends on the 3-month average of every stream, labelled with the months it averages", () => {
    mount("");
    const { option, table } = income();
    expect(option.series.map((s: any) => s.name)).toEqual(["Salary - Alex"]);
    const last = table.rows[table.rows.length - 1];
    expect(table.columns[0]).toBe("Period");
    expect(last[0]).toBe("3-month average, 2026-04 to 2026-06");
    const avg = incomeStreams(months, "2026-07", 3, Number.POSITIVE_INFINITY);
    expect(last[1]).toBe(avg.find((s) => s.name === "Salary - Alex")!.monthly);
    expect(screen.getByTestId("card-income-by-source").textContent).toContain("over the 3 full months through Jun 2026");
  });

  it("the streams are the data's subcategories, largest total over the window first, each coloured by its place", () => {
    // five streams; by name they would run Bonus, Consulting, Gifts, Interest, Salary, and by their best month Bonus
    // would lead - by total over the window Salary does. Gifts and Interest tie, so they go by name.
    data.cashflow = ["2026-04", "2026-05", "2026-06"].map((month, i) => cfm({ month, income_by_subcategory: {
      "Salary - Alex": 10000, "Consulting - Sam": 4000, Bonus: i === 1 ? 15000 : 0, "Gifts Received": 100, Interest: 100 } }));
    mount("?from=2026-04-01&to=2026-06-30");
    const { option, table } = income();
    const order = ["Salary - Alex", "Bonus", "Consulting - Sam", "Gifts Received", "Interest"];
    expect(option.series.map((s: any) => s.name), "totals 30000, 15000, 12000, 300, 300").toEqual(order);
    expect(table.columns).toEqual(["Period", ...order]);
    const colors = option.series.map((s: any) => s.color);
    expect(colors).toEqual(STREAM_PALETTE.slice(0, 5));
    expect(new Set(colors).size, "five streams, five colours").toBe(5);
  });
});

// The coverage line counts a month's reporting accounts out of the cash-flow accounts with any transaction - the ones full
// coverage waits for, the union of the months' accounts_covered. An account that never transacts (a certificate that
// only reports a balance) is in no month's list and holds no month back, so it is in neither number.
describe("Cash Flow: the monthly chart's coverage tooltip", () => {
  const main = () => charts.pairs.filter((p) => p.option?.series?.[0]?.name === "Income" && p.option?.tooltip?.formatter).pop()!;
  const tip = (dataIndex: number) => main().option.tooltip.formatter([{ dataIndex, marker: "", seriesName: "Income", value: 1 }]);
  it("counts the month's reporting accounts out of the cash-flow accounts with any transaction", () => {
    // five accounts transact; e begins in June
    data.cashflow = [cfm({ month: "2026-05", full_coverage: false, accounts_covered: ["a", "b", "c", "d"] }), cfm({ month: "2026-06" })];
    mount("?from=2026-05-01&to=2026-06-30");
    expect(tip(0)).toContain("incomplete account coverage (4/5 accounts)");
    expect(tip(1), "a fully covered month carries no warning").not.toContain("incomplete account coverage");
  });
  it("the denominator follows the data, never a fixed count", () => {
    data.cashflow = [cfm({ month: "2026-05", full_coverage: false, accounts_covered: ["a"] }),
      cfm({ month: "2026-06", accounts_covered: ["a", "b", "c"] })];
    mount("?from=2026-05-01&to=2026-06-30");
    expect(tip(0)).toContain("(1/3 accounts)");
  });
});

// The month in progress keeps its income and spending bars (starred) but has no savings-rate point: the footnote says no
// rate counts it, and on a household a few days old that point was the only rate on the page.
describe("Cash Flow: the month in progress on the monthly chart", () => {
  const main = () => charts.pairs.filter((p) => p.option?.series?.[0]?.name === "Income" && p.option?.tooltip?.formatter).pop()!;
  it("draws its bars and no rate point; the rate line ends on the last full month, and its table row has no rate", () => {
    data.cashflow = [cfm({ month: "2026-05", savings_rate: 0.3 }), cfm({ month: "2026-06", savings_rate: 0.1 }),
      cfm({ month: "2026-07", income: 9000, spend: 500, savings_rate: 0.9444 })];
    mount("?from=2026-05-01&to=2026-07-31");
    const { option, table } = main();
    const series = (name: string) => option.series.find((s: any) => s.name === name).data;
    expect(series("Income")).toEqual([20000, 20000, 9000]);
    expect(series("Spending"), "the bars stay").toEqual([-15000, -15000, -500]);
    expect(series("Savings rate"), "no point for July").toEqual([0.3, 0.1, null]);
    const rate = table.columns.indexOf("Savings rate");
    expect(table.rows.map((r: any[]) => [r[0], r[rate]])).toEqual([["2026-05", 0.3], ["2026-06", 0.1], ["2026-07 (in progress)", null]]);
    expect(option.tooltip.formatter([{ dataIndex: 2, marker: "", seriesName: "Savings rate", value: null }]),
      "the tooltip reads no rate for it").toContain("Savings rate: —");
  });
  it("the control: a window that ends before the month in progress draws a rate for every month", () => {
    data.cashflow = [cfm({ month: "2026-05", savings_rate: 0.3 }), cfm({ month: "2026-06", savings_rate: 0.1 }),
      cfm({ month: "2026-07", savings_rate: 0.9 })];
    mount("?from=2026-05-01&to=2026-06-30");
    expect(main().option.series.find((s: any) => s.name === "Savings rate").data).toEqual([0.3, 0.1]);
  });
  it("with payroll facts entered, the dashed total-rate line has no point for the month in progress either", () => {
    payrollPhases = [{ person: "Alex", from: "2026-01", to: null, gross_annual: 120_000, employee_401k_pct: 6, employer_match_pct: 4 }];
    data.cashflow = [cfm({ month: "2026-05" }), cfm({ month: "2026-06" }), cfm({ month: "2026-07", income: 9000, spend: 500 })];
    mount("?from=2026-05-01&to=2026-07-31");
    const total = main().option.series.find((s: any) => /Total savings rate/.test(s.name)).data;
    expect(total.slice(0, 2).every((r: number | null) => typeof r === "number"), "a total rate for each full month").toBe(true);
    expect(total[2], "none for July").toBeNull();
  });
  it("the dashed total-rate line is the theme's teal (3:1 on a card in both themes), never the old light-failing green", () => {
    setChartTheme("light");
    try {
      payrollPhases = [{ person: "Alex", from: "2026-01", to: null, gross_annual: 120_000, employee_401k_pct: 6, employer_match_pct: 4 }];
      data.cashflow = [cfm({ month: "2026-05" }), cfm({ month: "2026-06" })];
      mount("?from=2026-05-01&to=2026-06-30");
      const total = main().option.series.find((s: any) => /Total savings rate/.test(s.name));
      expect(total.color).toBe(CHART_TOTAL_RATE);
      expect(total.color).not.toBe("#34d399");
      expect(total.lineStyle.type).toBe("dashed");
    } finally {
      setChartTheme("dark");
    }
  });
});

// The footnote under the monthly chart says what the newest month is: still filling in when it is the ingest month, and
// when it is the month just before the ingest month (on the ingest month's first days its rows are still arriving); where
// the data ends only when a whole month lies between it and the ingest month - statement files not yet brought up to
// date have no bills "still coming in".
describe("Cash Flow: the footnote on the newest month", () => {
  const note = () => screen.getByTestId("card-cashflow-main").textContent ?? "";
  afterEach(() => { data.manifest = { generated_at: "2026-07-21T09:00:00-05:00" }; });
  it("the ingest month: still in progress, its bills still coming in", () => {
    data.cashflow = [cfm({ month: "2026-06" }), cfm({ month: "2026-07" })];
    mount("?from=2026-06-01&to=2026-07-31");
    expect(note()).toContain("* Jul 2026 is still in progress: its bills are still coming in, so no rate above counts it.");
  });
  it("the month just before the ingest month, on the 1st with nothing of the new month posted yet: the ordinary wording", () => {
    data.cashflow = [cfm({ month: "2026-05" }), cfm({ month: "2026-06" })];
    data.manifest = { generated_at: "2026-07-01T06:00:00-05:00" };
    mount("?from=2026-05-01&to=2026-06-30");
    expect(note()).toContain("* Jun 2026 is still in progress: its bills are still coming in, so no rate above counts it.");
    expect(note()).not.toContain("The data ends in");
    const rows = charts.pairs.filter((p) => p.option?.series?.[0]?.name === "Income" && p.option?.tooltip?.formatter).pop()!.table.rows;
    expect(rows.map((r: any[]) => r[0])).toEqual(["2026-05", "2026-06 (in progress)"]);
  });
  it("data that ends two months before the ingest: the data ends there and later transactions have not been read", () => {
    data.cashflow = [cfm({ month: "2026-04" }), cfm({ month: "2026-05" })];
    data.manifest = { generated_at: "2026-07-21T09:00:00-05:00" };
    mount("?from=2026-04-01&to=2026-05-31");
    expect(note()).toContain("* The data ends in May 2026: later transactions have not been read yet, so no rate above counts that month.");
    expect(note()).not.toContain("still coming in");
    const rows = charts.pairs.filter((p) => p.option?.series?.[0]?.name === "Income" && p.option?.tooltip?.formatter).pop()!.table.rows;
    expect(rows.map((r: any[]) => r[0]), "the table names it for what it is").toEqual(["2026-04", "2026-05 (partial)"]);
  });
});

// A household with no cash-flow month in the window: the two chart cards keep their frame and heading and say one line in
// place of empty axes, and no chart is built for nothing.
describe("Cash Flow's charts with no month", () => {
  const main = () => charts.pairs.filter((p) => p.option?.series?.[0]?.name === "Income" && p.option?.tooltip?.formatter).pop()!;
  const phase: IncomePhase = { person: "Alex", phase: "Synth Corp", start: "2025-06-01", end: "2026-12-31",
    deposits: 1, total: 1, avg_monthly: 1, total_cents: 100, avg_monthly_cents: 100 };
  const line = (id: string) => screen.getByTestId(id).querySelector("[data-testid=empty-state], [data-testid=no-transactions]")?.textContent;
  afterEach(() => { data.assumptions.income_phases = []; data.transactions = []; });
  it("no transaction at all: the main card says the long line, the income card the short one, and no chart is drawn", () => {
    data.cashflow = [];
    data.assumptions.income_phases = [phase];
    mount("");
    expect(line("card-cashflow-main")).toBe(NO_TRANSACTIONS);
    expect(line("card-income-by-source")).toBe(NO_TRANSACTIONS_YET);
    expect(screen.queryByTestId("chart")).toBeNull();
    expect(charts.pairs, "no chart option is handed over").toEqual([]);
  });
  it("transactions and a window that holds none of their months: both cards say to pick a longer window", () => {
    data.transactions = [txFactory()({ date: "2026-06-10" })];
    mount("?from=2024-01-01&to=2024-06-30");
    for (const id of ["card-cashflow-main", "card-income-by-source"]) {
      expect(line(id), id).toBe("No month of cash flow in this window. Pick a longer one.");
      expect(screen.getByTestId(id).querySelector("h2")?.textContent, id).not.toBe("");
    }
    expect(screen.queryByTestId("chart")).toBeNull();
  });
  it("transactions and no month in coverage: the main card says what brings one, with the key as a hint", () => {
    data.transactions = [txFactory()({ date: "2026-06-10" })];
    data.cashflow = months.map((m) => ({ ...m, in_coverage: false }));
    mount("");
    expect(line("card-cashflow-main")).toBe("No month of cash flow is in coverage yet: a month counts once enough of the "
      + "cash-flow accounts have their first transaction (coverage.min_share in assumptions.yaml).");
    expect(line("card-income-by-source")).toBe("No month in coverage yet.");
  });
  it("the control: with months in the window the same phase draws its band, from the window's first month", () => {
    data.assumptions.income_phases = [phase];
    mount("?from=2026-04-01&to=2026-07-31");
    const bands = main().option.series[0].markArea.data;
    expect(bands).toHaveLength(1);
    expect([bands[0][0].xAxis, bands[0][1].xAxis]).toEqual(["2026-04", "2026-12"]);
  });
});

// Each trailing tile is labelled with the full months it reads; with no full month it is unknown.
describe("Cash Flow's tiles on a thin household", () => {
  const tiles = () => [...screen.getByTestId("kpis-cashflow").children].map((t) => t.textContent ?? "");
  it("one full month: the short rate says 1 mo, the twelve-month rate is not that figure again, the burn says one month", () => {
    data.cashflow = [cfm({ month: "2026-06" }), cfm({ month: "2026-07" })];       // June full, July in progress
    mount("");
    const [r3, r12, best, worst, burn] = tiles();
    expect(r3).toMatch(/^Savings rate \(1 mo\)/);
    expect(r12, "the same month's rate is not shown twice").toBe("Savings rate (12 mo)—Not enough history yet");
    expect(burn).toMatch(/avg monthly spend, trailing 1 full month through Jun 2026$/);
    expect(tiles().join(" "), "no window it does not have").not.toMatch(/\(3 mo\)|6 full months/);
    // one month is no best and no worst: not compared with itself
    expect([best, worst]).toEqual(["Best month—Not enough history yet", "Worst month—Not enough history yet"]);
  });
  it("three full months: the twelve-month tile would hold the same three, so it waits; four: it shows its four", () => {
    data.cashflow = ["2026-04", "2026-05", "2026-06", "2026-07"].map((month) => cfm({ month }));
    mount("");
    expect(tiles()[1]).toBe("Savings rate (12 mo)—Not enough history yet");
    cleanup();
    data.cashflow = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07"].map((month) => cfm({ month }));
    mount("");
    expect(tiles()[0]).toMatch(/^Savings rate \(3 mo\)/);
    expect(tiles()[1]).toMatch(/^Savings rate \(4 mo\)25\.0%/);
  });
  it("two full months: the best and the worst are named", () => {
    data.cashflow = [cfm({ month: "2026-05", savings_rate: 0.3 }), cfm({ month: "2026-06", savings_rate: 0.1 }),
      cfm({ month: "2026-07" })];
    mount("");
    const [, , best, worst] = tiles();
    expect([best, worst]).toEqual(["Best monthMay 202630.0% saved · in this window", "Worst monthJun 202610.0% saved · in this window"]);
  });
  it("two full months and one with income: no best and no worst, and the line says why", () => {
    data.cashflow = [cfm({ month: "2026-05", savings_rate: 0.3 }), cfm({ month: "2026-06", income: 0, savings_rate: null }),
      cfm({ month: "2026-07" })];
    mount("");
    const [, , best, worst] = tiles();
    expect([best, worst]).toEqual(["Best month—fewer than two months in this window with income to rate",
      "Worst month—fewer than two months in this window with income to rate"]);
  });
  it("no full month: every trailing tile is \"—\" with not enough history yet, the rates in Home's words", () => {
    data.cashflow = [cfm({ month: "2026-07" })];                                   // the month in progress alone
    mount("");
    expect(tiles()).toEqual(["Savings rate (3 mo)—Not enough history yet: no full month of cash flow",
      "Savings rate (12 mo)—Not enough history yet: no full month of cash flow",
      "Best month—Not enough history yet", "Worst month—Not enough history yet",
      "Monthly burn—Not enough history yet — no full month of spending"]);
  });
});

// Interest as the only income (the no-income demo household's shape): spending is many times the income, so no rate is
// printed as a percentage; each tile reads "—" with the shortfall in dollars, in the words Projections uses.
describe("Cash Flow's tiles when spending is over twice the income", () => {
  const tiles = () => [...screen.getByTestId("kpis-cashflow").children].map((t) => t.textContent ?? "");
  const poor = (month: string, spend: number) => cfm({ month, income: 185, spend, savings_rate: (185 - spend) / 185 });
  it("the rates are \"—\" and the dollars; the best and the worst month say their own month's shortfall", () => {
    data.cashflow = [poor("2026-04", 7_000), poor("2026-05", 7_900), poor("2026-06", 6_500), poor("2026-07", 6_000)];
    mount("");
    const [r3, , best, worst] = tiles();
    expect(r3).toBe(`Savings rate (3 mo)—income did not cover spending: ${fmtUSD0(185 - (7_000 + 7_900 + 6_500) / 3)} a month · full months through Jun 2026`);
    expect([best, worst]).toEqual([`Best monthJun 2026income did not cover spending: ${fmtUSD0(185 - 6_500)} · in this window`,
      `Worst monthMay 2026income did not cover spending: ${fmtUSD0(185 - 7_900)} · in this window`]);
    expect(tiles().join(" "), "no percentage below −100% anywhere").not.toMatch(/\d%/);
  });
  it("at −100% the rate prints; just below, it does not", () => {
    data.cashflow = [poor("2026-05", 370), poor("2026-06", 370), cfm({ month: "2026-07" })];
    mount("");
    expect(tiles()[0]).toMatch(/^Savings rate \(2 mo\)−100\.0%/);
    cleanup();
    data.cashflow = [poor("2026-05", 371), poor("2026-06", 371), cfm({ month: "2026-07" })];
    mount("");
    expect(tiles()[0]).toMatch(/^Savings rate \(2 mo\)—income did not cover spending/);
  });
});

// The best and the worst month are the window's: the full months of the date range the page shows. The trailing rates
// beside them keep the whole history, whatever the window.
describe("Cash Flow's best and worst month follow the selected window", () => {
  const tiles = () => [...screen.getByTestId("kpis-cashflow").children].map((t) => t.textContent ?? "");
  // January's rate is the best of the history and February's the worst; the spring months sit between them
  const year = () => [cfm({ month: "2026-01", savings_rate: 0.6 }), cfm({ month: "2026-02", savings_rate: -0.2 }),
    cfm({ month: "2026-03", savings_rate: 0.3 }), cfm({ month: "2026-04", savings_rate: 0.1 }),
    cfm({ month: "2026-05", savings_rate: 0.4 }), cfm({ month: "2026-06", savings_rate: 0.2 }),
    cfm({ month: "2026-07", savings_rate: 0.95 })];                     // the month in progress: never best
  it("changing the window changes the two tiles; the trailing rates do not move", () => {
    data.cashflow = year();
    mount("?from=2026-01-01&to=2026-03-31");
    const winter = tiles();
    // a negative rate is no saving: the worst month says it spent more than came in, and by how much of the income
    expect([winter[2], winter[3]]).toEqual(["Best monthJan 202660.0% saved · in this window",
      "Worst monthFeb 2026spent 20.0% more than came in · in this window"]);
    cleanup();
    mount("?from=2026-04-01&to=2026-07-31");
    const spring = tiles();
    expect([spring[2], spring[3]], "July is in progress, so it is in neither")
      .toEqual(["Best monthMay 202640.0% saved · in this window", "Worst monthApr 202610.0% saved · in this window"]);
    expect([spring[0], spring[1]], "the trailing tiles read the whole history either way").toEqual([winter[0], winter[1]]);
  });
  it("each tile links the month it names", () => {
    data.cashflow = year();
    mount("?from=2026-04-01&to=2026-06-30");
    const [, , best, worst] = [...screen.getByTestId("kpis-cashflow").children] as HTMLAnchorElement[];
    expect(best.getAttribute("href")).toContain("from=2026-05-01");
    expect(worst.getAttribute("href")).toContain("from=2026-04-01");
  });
  it("a window with one full month: \"—\", and the line says the window holds fewer than two", () => {
    data.cashflow = year();
    mount("?from=2026-06-01&to=2026-07-31");
    const [, , best, worst] = tiles();
    expect([best, worst]).toEqual(["Best month—fewer than two full months in this window",
      "Worst month—fewer than two full months in this window"]);
  });
});

// The monthly chart's right axis is the savings rate: even steps from end to end, never ECharts' own split of a fixed
// range (100%, 90%, 60%, 30%, 0%, −30%, −50%). Its legend lists what the chart draws: the rate line has no point while
// the window holds no full month (only the month in progress, which no rate counts), so it has no entry then.
// The month boxes' open ends show the first and last months the open window draws; closing either at the month shown
// draws the same months.
describe("Cash Flow: an open end shows the last month the window really includes", () => {
  const drawn = () => charts.pairs.filter((p) => p.option?.series?.[0]?.name === "Income").pop()!.option.xAxis.data as string[];
  it("All: From shows the first month drawn and To the month in progress; typing either in draws the same months", () => {
    mount("?win=all");
    const [from, to] = [(screen.getByLabelText("from") as HTMLInputElement).value, (screen.getByLabelText("to") as HTMLInputElement).value];
    const open = drawn();
    expect([from, to]).toEqual([open[0], open[open.length - 1]]);
    cleanup();
    mount(`?from=${from}-01&to=${to}-31`);
    expect(drawn()).toEqual(open);
  });
});

// A savings rate below the rate axis' foot (−50%) is drawn AT the foot as a downward triangle, its tooltip keeps the real
// rate, and one line under the chart says so; the axis keeps −50% to 100%. And the two axes share one grid: $0 on the
// line of 0%, six even steps on each, no bar clipped.
describe("Cash Flow: a rate below the axis, and one grid for both axes", () => {
  const main = () => charts.pairs.filter((p) => p.option?.series?.[0]?.name === "Income").pop()!.option;
  const fullMonthsAt = (rates: (number | null)[]) => rates.map((savings_rate, i) =>
    cfm({ month: `2026-0${i + 3}`, income: 4_000, spend: 6_000, invested: 500, savings_rate }));
  it("a month at −135% is drawn at −50% as a downward triangle; its tooltip says −135.0%; one line says so", () => {
    data.cashflow = fullMonthsAt([0.4, -1.35, 0.3, 0.2, null]);
    mount("?win=all");
    const rate = main().series.find((s: { name: string }) => s.name === "Savings rate");
    expect(rate.data[0]).toBe(0.4);
    expect(rate.data[1]).toEqual({ value: -0.5, symbol: "triangle", symbolRotate: 180, symbolSize: 9 });
    const tip = main().tooltip.formatter([{ dataIndex: 1, seriesName: "Savings rate", marker: "", value: -0.5 }]);
    expect(tip).toContain("Savings rate: −135.0%");
    expect(main().yAxis[1]).toMatchObject({ min: -0.5, max: 1 });
    expect(screen.getAllByTestId("cashflow-rate-pinned").map((n) => n.textContent))
      .toEqual(["A month below −50% is drawn at the bottom of the chart; its tooltip has the rate."]);
  });
  it("no month below the foot: nothing pinned and no line", () => {
    data.cashflow = fullMonthsAt([0.4, -0.5, 0.3, 0.2, null]);
    mount("?win=all");
    expect(main().series.find((s: { name: string }) => s.name === "Savings rate").data[1]).toBe(-0.5);
    expect(screen.queryByTestId("cashflow-rate-pinned")).toBeNull();
  });
  it("a household with no income to speak of: every rated month below the foot, so no rate line, no legend entry and no "
    + "rate axis; the tooltip keeps each month's rate and one line says why", () => {
    data.cashflow = fullMonthsAt([-40, -55.2, -38, -61, null]);
    mount("?win=all");
    expect(main().series.map((s: { name: string }) => s.name)).toEqual(["Income", "Spending", "Invested"]);
    expect(main().legend.data).not.toContain("Savings rate");
    expect(main().yAxis[1].show, "no axis for a line not drawn").toBe(false);
    const tip = main().tooltip.formatter([{ dataIndex: 1, seriesName: "Income", marker: "", value: 4_000 }]);
    expect(tip).toContain(`Savings rate: ${fmtPct(-55.2)}`);
    expect(screen.getByTestId("cashflow-rate-undrawn").textContent).toBe("Every month's savings rate here is below −50%, "
      + "so the line is not drawn; each month's tooltip has its rate.");
    expect(screen.queryByTestId("cashflow-rate-pinned"), "no pins drawn, so no pin line").toBeNull();
  });
  it("the control: one rated month above the foot keeps the line, its pins and the pin line, and no undrawn line", () => {
    data.cashflow = fullMonthsAt([-40, -55.2, 0.1, -61, null]);
    mount("?win=all");
    expect(main().series.some((s: { name: string }) => s.name === "Savings rate")).toBe(true);
    expect(main().yAxis[1].show).toBe(true);
    expect(screen.getByTestId("cashflow-rate-pinned")).toBeTruthy();
    expect(screen.queryByTestId("cashflow-rate-undrawn")).toBeNull();
  });
  it("the tallest income at least as tall as the deepest spending: the money axis shares the rate axis' grid", () => {
    data.cashflow = months2;
    mount("?win=all");
    const [money, rate] = main().yAxis as { min: number; max: number; interval: number }[];
    expect((0 - money.min) / (money.max - money.min)).toBeCloseTo((0 - rate.min) / (rate.max - rate.min), 10);
    expect((money.max - money.min) / money.interval).toBe(6);
    const drawn = main().series.slice(0, 3).flatMap((s: { data: number[] }) => s.data);
    expect(Math.max(...drawn) <= money.max && Math.min(...drawn) >= money.min).toBe(true);
  });
  it("spending deeper than any income is tall: the money axis takes even steps of its own, its top the first one above the tallest bar", () => {
    // a month of $27,700 spending beside $12,000 of income: the shared grid would put $0 a third up and leave the top empty
    data.cashflow = [...months2.slice(0, 2), cfm({ month: "2026-05", income: 12_000, spend: 27_700, invested: 1_250 })];
    mount("?win=all");
    const [money, rate] = main().yAxis as { min: number; max: number; interval: number }[];
    const drawn = main().series.slice(0, 3).flatMap((s: { data: number[] }) => s.data);
    const [tallest, deepest] = [Math.max(...drawn), Math.min(...drawn)];
    expect(money.max >= tallest && money.max - money.interval < tallest, "the top is the first step at or above the tallest bar").toBe(true);
    expect(money.min <= deepest && money.min + money.interval > deepest, "the foot the first step at or below the deepest").toBe(true);
    expect(Number.isInteger((money.max - money.min) / money.interval) && (money.max - money.min) / money.interval <= 6).toBe(true);
    expect(rate).toMatchObject({ min: -0.5, max: 1, interval: 0.25 });
  });
});
const months2 = ["2026-03", "2026-04", "2026-05"].map((month) => cfm({ month, income: 13_000, spend: 6_300, invested: 1_250 }));

describe("Cash Flow: the monthly chart's rate axis and legend", () => {
  const main = () => charts.pairs.filter((p) => p.option?.series?.[0]?.name === "Income").pop()!.option;
  it("the right axis runs −50% to 100% in even 25-point steps, its labels whole percents", () => {
    mount("");
    const rate = main().yAxis[1];
    const steps = (rate.max - rate.min) / rate.interval;
    expect(Number.isInteger(steps)).toBe(true);
    expect(Array.from({ length: steps + 1 }, (_, i) => rate.axisLabel.formatter(rate.min + i * rate.interval)))
      .toEqual(["−50%", "−25%", "0%", "25%", "50%", "75%", "100%"]);
  });
  it("with full months the rate line is drawn and listed, and its axis shown", () => {
    mount("");
    expect(main().legend.data).toEqual(["Income", "Spending", "Savings rate"]);
    expect(main().yAxis[1].show).toBe(true);
  });
  it("with only the month in progress the rate line draws nothing: no legend entry and no rate axis; its tooltip keeps a dash", () => {
    mount("?from=2026-07-01&to=2026-07-31");
    const o = main();
    expect(o.series.find((s: { name: string }) => s.name === "Savings rate").data).toEqual([null]);
    expect(o.legend.data).toEqual(["Income", "Spending"]);
    expect(o.yAxis[1].show, "an axis with no line on it is not shown").toBe(false);
  });
  it("nothing invested in any month draws no bar, so the legend has no Invested entry; a month with some lists it", () => {
    mount("");
    expect(main().series.find((s: { name: string }) => s.name === "Invested").data.every((v: number) => v === 0)).toBe(true);
    expect(main().legend.data).not.toContain("Invested");
    cleanup();
    data.cashflow = months.map((m, i) => (i === months.length - 2 ? { ...m, invested: 500 } : m));
    mount("");
    expect(main().legend.data).toEqual(["Income", "Spending", "Invested", "Savings rate"]);
  });
});

describe("Cash Flow's income by source with months and no income", () => {
  it("says there is no income in the window's months, with no chart", () => {
    data.cashflow = [cfm({ month: "2026-06", income: 0, income_by_subcategory: {} }),
      cfm({ month: "2026-07", income: 0, income_by_subcategory: {} })];
    data.transactions = [txFactory()({ date: "2026-06-10" })];
    try {
      mount("");
      const card = screen.getByTestId("card-income-by-source");
      expect(card.querySelector("[data-testid=empty-state]")?.textContent).toBe("No income in the months of this window.");
      expect(card.querySelector("[data-testid=chart]")).toBeNull();
    } finally { data.transactions = []; }
  });
});

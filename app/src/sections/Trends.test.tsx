// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// Trends' cards on the households the app must cope with: the liquidity card's share is of assets and its note says so,
// the cash-flow cards name their real windows, and a card with nothing to draw says why.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NO_BALANCES, NO_BALANCES_YET, NO_TRANSACTIONS, NO_TRANSACTIONS_YET } from "../components/NoData";
import type { AppData, AssetRow, Snapshot } from "../data/types";
import { appDataFactory, firstWeekData, mortgageFactory, oneFullMonthData, snapFactory } from "../lib/__fixtures__";
import { CHART_AXIS, CHART_SPLIT } from "../lib/echarts";

// the chart is stubbed (no canvas) and records every option it was handed
const charts = vi.hoisted(() => ({ options: [] as any[] }));
vi.mock("../components/EChart", () => ({
  default: (p: { option: unknown }) => { charts.options.push(p.option); return <div data-testid="chart" />; },
}));
const snap = snapFactory({});
const base = appDataFactory(snap)({});
/** a row of `account` (the class, unless named) as an export holds it */
const row = (asset_class: string, value: number, over: Partial<AssetRow> = {}): AssetRow => ({ account: asset_class,
  account_id: asset_class, institution: "B", owner: "Joint", asset_class, value, liquid: asset_class !== "House",
  as_of: "2026-06-01", as_of_assumed: false, needs_review: false, source: "bank_balance", manual_id: null,
  carried_back: false, ...over });
/** a snapshot whose rows are one per class of `by_class` */
const classSnap = (over: Partial<Snapshot> & { by_class: Record<string, number> }): Snapshot =>
  snap({ ...over, rows: Object.entries(over.by_class).map(([k, v]) => row(k, v)) });
let data: AppData = base;
let missing = new Set<string>();
vi.mock("../data/DataContext", () => ({ useData: () => ({ data, missing }) }));

import Trends from "./Trends";

afterEach(() => { cleanup(); data = base; missing = new Set<string>(); charts.options = []; });
const note = () => {
  render(<MemoryRouter><Trends /></MemoryRouter>);
  return screen.getByTestId("card-trend-liquidity").textContent ?? "";
};
const withSnapshots = (firstIncluded: boolean): AppData => ({
  ...base,
  assets: { snapshots: [
    snap({ snapshot_date: "2019-05-01", mortgage_included: firstIncluded }),
    snap({ snapshot_date: "2022-07-25" }),
  ] },
  assumptions: { ...base.assumptions, mortgage: mortgageFactory({ schedule: [
    { date: "2020-06-30", principal_balance: 1 },
  ] }) },
});

describe("Trends: the liquidity note", () => {
  it("says the share is of assets, so a snapshot that pre-dates the mortgage data needs no note: assets hold no debt", () => {
    data = withSnapshots(false);
    const text = note();
    expect(text).toContain("liquid = cash and investment accounts other than retirement and 529, plus hand-entered assets marked liquid, "
      + "as a share of everything owned (debts are not subtracted), per snapshot");
    expect(text).not.toMatch(/mortgage/);
    expect(screen.getByTestId("card-trend-liquidity").querySelector("h2")?.textContent).toBe("Liquidity — liquid share of assets");
  });
  it("the share is of the snapshot's assets, never of net worth: debts cannot push it past 100%", () => {
    data = { ...base, assets: { snapshots: [snap({ total: 200, assets_total: 600, liabilities_total: -400, liquid_total: 500,
      by_class: { Cash: 500, Car: 100, "Other Debt": -400 } })] } };
    render(<MemoryRouter><Trends /></MemoryRouter>);
    const share = charts.options.find((o) => o.series?.[0]?.name === "Liquid share of assets");
    expect(share.series[0].data).toEqual([["2026-06-01", 0.8333]]);
  });
});

// Like with like over time: a month end holds a margin account net of its loan, the newest snapshot gross (positions and
// the loan); the last point of each snapshot series is drawn on the month ends' footing, and the card says so
describe("Trends: the newest snapshot on the month ends' footing", () => {
  const monthEnd = snap({ snapshot_date: "2026-05-31", total: 600_000, assets_total: 600_000, liabilities_total: 0,
    liquid_total: 400_000, by_class: { "Stocks/ETF": 300_000, Cash: 100_000, House: 200_000 },
    rows: [row("Stocks/ETF", 300_000, { account: "Broker", account_id: "brk", source: "simplefin_balance" }), row("Cash", 100_000),
      row("House", 200_000)] });
  const live = snap({ snapshot_date: "2026-06-15", total: 607_500, assets_total: 620_000, liabilities_total: -12_500,
    liquid_total: 420_000, by_class: { "Stocks/ETF": 320_000, Margin: -12_500, Cash: 100_000, House: 200_000 },
    rows: [row("Stocks/ETF", 320_000, { account: "Broker", account_id: "brk", source: "positions" }),
      row("Margin", -12_500, { account: "Broker Margin", account_id: "brk", liquid: false, source: "simplefin_balance" }),
      row("Cash", 100_000), row("House", 200_000)] });
  const lastShare = () => charts.options.find((o) => o.series?.[0]?.name === "Liquid share of assets").series[0].data.at(-1);
  const lastMix = () => {
    const alloc = charts.options.find((o) => o.yAxis?.max === 100);
    return Object.fromEntries(alloc.series.map((s: any) => [s.name, s.data.at(-1)[1]]));
  };
  it("a live margin account: the last points are the folded figures, and both cards say why", () => {
    data = { ...base, assets: { snapshots: [monthEnd, live] } };
    render(<MemoryRouter><Trends /></MemoryRouter>);
    // folded: the account at 307,500 (its net), so assets 607,500 of which 407,500 liquid - not 420,000 of 620,000
    expect(lastShare()).toEqual(["2026-06-15", +(407_500 / 607_500).toFixed(4)]);
    expect(lastMix()).toEqual({ "Stocks/ETF": 50.6, House: 32.9, Cash: 16.5 });
    const note = "Month ends hold a margin account net of its loan; the last point is drawn the same way, so it can differ from "
      + "today's figures on Home and Balances, which count the positions and the loan separately.";
    expect(screen.getByTestId("trend-liquidity-footing").textContent).toBe(note);
    expect(screen.getByTestId("trend-allocation-footing").textContent).toBe(note);
  });
  it("no margin account: the last points are the snapshot's own, and no card says anything about it", () => {
    const plain = { ...live, total: 620_000, liabilities_total: 0, by_class: { "Stocks/ETF": 320_000, Cash: 100_000, House: 200_000 },
      rows: live.rows.filter((r) => r.asset_class !== "Margin") };
    data = { ...base, assets: { snapshots: [monthEnd, plain] } };
    render(<MemoryRouter><Trends /></MemoryRouter>);
    expect(lastShare()).toEqual(["2026-06-15", +(420_000 / 620_000).toFixed(4)]);
    expect(screen.queryByTestId("trend-liquidity-footing")).toBeNull();
    expect(screen.queryByTestId("trend-allocation-footing")).toBeNull();
  });
});

describe("Trends: how far back the trends reach follows the data", () => {
  const reach = () => {
    render(<MemoryRouter><Trends /></MemoryRouter>);
    return screen.getByText(/Everything here is recomputed from full history on load/).textContent ?? "";
  };
  it("names the coverage floor, the last full month and the first snapshot's year", () => {
    const two = withSnapshots(true);
    data = { ...two, cashflow: two.cashflow.map((m) => ({ ...m, accounts_covered: ["chk", "card"] })) };
    // May 2026 holds the newest row and is before the ingest month, so it is the month in progress
    expect(reach()).toContain("recomputed from full history on load — cash-flow trends start Apr 2026 (when multi-account "
      + "coverage begins) and end Apr 2026, the last full month; snapshot trends reach back to 2019.");
  });
  it("one cash-flow account (one checking file): the start is where the data begins, never multi-account coverage", () => {
    data = withSnapshots(true);
    expect(data.cashflow.every((m) => m.accounts_covered.length === 1)).toBe(true);
    const text = reach();
    expect(text).toContain("cash-flow trends start Apr 2026 (when the data begins) and end Apr 2026");
    expect(text).not.toMatch(/multi-account/);
  });
  it("a month a row was dated ahead into is no end: the lines still end on the last full month", () => {
    const ahead = { ...base.cashflow[1], month: "2026-07" };
    data = { ...withSnapshots(true), cashflow: [...base.cashflow, ahead],
      manifest: { ...base.manifest, generated_at: "2026-06-30T20:00:00-05:00" } };
    // June is the ingest month, so it is in progress; May is the last full month and July has not begun
    expect(reach()).toContain("and end May 2026, the last full month;");
  });
  it("no full month yet: the start is named and no end", () => {
    data = { ...withSnapshots(true), cashflow: [base.cashflow[0]], manifest: { ...base.manifest, generated_at: "2026-04-20T09:00:00-05:00" } };
    const text = reach();
    expect(text).toContain("cash-flow trends start Apr 2026 (when the data begins);");
    expect(text).not.toMatch(/the last full month/);
  });
  it("one snapshot and no month in coverage: both clauses are left out, never a blank year or month", () => {
    data = { ...base, cashflow: base.cashflow.map((m) => ({ ...m, in_coverage: false })) };
    const text = reach();
    expect(text).toMatch(/recomputed from full history on load\.$/);
    expect(text).not.toMatch(/reach back|trends start|null|undefined/);
  });
});

// The footer names only what this household has: the daily line and the pins where Home draws them, the holdings
// concentration with an investment account or holdings, the mortgage summary with a mortgage account.
describe("Trends: the footer names what the household has", () => {
  const footer = () => {
    render(<MemoryRouter><Trends /></MemoryRouter>);
    return screen.getByText(/Everything here is recomputed from full history on load/).textContent ?? "";
  };
  const holding = { account: "Broker", account_id: null, institution: "Broker", owner: "Joint", symbol: "VTI", name: "Fund",
    asset_class: "Stocks/ETF", quantity: 1, value: 1_000, cost_basis: null, as_of: "2026-06-01", liquid: true, notes: null };
  it("a first-week household: Home and Cash Flow, no pins, no holdings, no mortgage", () => {
    data = firstWeekData();
    const text = footer();
    expect(text).toMatch(/^Net worth over every snapshot, with the daily line, is on Home; income by stream, month by month, is on Cash Flow → Overview\./);
    expect(text).not.toMatch(/pins|Holdings concentration|mortgage summary|current-only/);
  });
  it("holdings and a mortgage account: both named, with their pages", () => {
    data = { ...withSnapshots(true), holdings: [holding] };
    const text = footer();
    expect(text).toMatch(/Holdings concentration and the mortgage summary are current-only data \(Accounts and Planning\) and deliberately have no invented history\.$/);
  });
  it("holdings and no mortgage account: the holdings alone", () => {
    data = { ...base, holdings: [holding] };
    expect(footer()).toMatch(/Holdings concentration is current-only data \(Accounts\) and deliberately has no invented history\.$/);
  });
  it("pins where Home draws them: a $100K crossing between two snapshots", () => {
    data = { ...base, assets: { snapshots: [snap({ snapshot_date: "2026-05-01", total: 90_000 }), snap({ snapshot_date: "2026-06-01", total: 110_000 })] } };
    expect(footer()).toMatch(/^Net worth over every snapshot, with the milestone pins, is on Home;/);
  });
});

// A chart with no rows keeps its card and heading and says one line; a chart of one point draws it and says so.
describe("Trends' charts on a thin household", () => {
  const line = (id: string, testId: string) => within(screen.getByTestId(id)).getByTestId(testId).textContent;
  const chartIn = (id: string) => within(screen.getByTestId(id)).queryByTestId("chart");
  it("no full month: the savings and burn cards say there is not enough history, with no chart", () => {
    data = firstWeekData();
    render(<MemoryRouter><Trends /></MemoryRouter>);
    for (const id of ["card-trend-savings", "card-trend-burn"]) {
      expect(chartIn(id), id).toBeNull();
      expect(line(id, "not-enough-history"), id).toMatch(/^Not enough history yet — .*once a full month is in\.$/);
    }
  });
  it("one full month: savings and burn draw their one point and say the line comes with the second", () => {
    data = oneFullMonthData();
    render(<MemoryRouter><Trends /></MemoryRouter>);
    for (const id of ["card-trend-savings", "card-trend-burn"]) {
      expect(chartIn(id), id).not.toBeNull();
      expect(line(id, "not-enough-history"), id).toBe("Not enough history yet — one full month so far; the line draws once a second one is in.");
    }
  });
  it("one snapshot: the allocation and the liquidity draw their point and say the line comes with the second", () => {
    data = oneFullMonthData();
    render(<MemoryRouter><Trends /></MemoryRouter>);
    for (const id of ["card-trend-allocation", "card-trend-liquidity"]) {
      expect(chartIn(id), id).not.toBeNull();
      expect(line(id, "not-enough-history"), id).toBe("Not enough history yet — one snapshot so far; the line draws once a second one exists.");
    }
  });
  it("no transaction and no snapshot: each card keeps its heading and says one line, the long ones once", () => {
    data = { ...base, transactions: [], cashflow: [], assets: { snapshots: [] } };
    render(<MemoryRouter><Trends /></MemoryRouter>);
    expect(line("card-trend-savings", "no-transactions")).toBe(NO_TRANSACTIONS);
    expect(line("card-trend-burn", "no-transactions")).toBe(NO_TRANSACTIONS_YET);
    expect(line("card-trend-allocation", "no-balances")).toBe(NO_BALANCES);
    expect(line("card-trend-liquidity", "no-balances")).toBe(NO_BALANCES_YET);
    for (const id of ["card-trend-savings", "card-trend-burn", "card-trend-allocation", "card-trend-liquidity"]) {
      expect(chartIn(id), id).toBeNull();
      expect(screen.getByTestId(id).querySelector("h2")?.textContent, id).not.toBe("");
    }
  });
  it("a newest snapshot with no class above zero: the allocation says so of the newest, even when an older one held some", () => {
    // the mix is drawn for the newest snapshot's classes: an older snapshot's cash is no class it could draw
    data = { ...base, assets: { snapshots: [snap({ snapshot_date: "2026-05-01", total: 900, by_class: { Cash: 900 } }),
      snap({ snapshot_date: "2026-06-01", total: -500, by_class: { "Other Debt": -500 } })] } };
    render(<MemoryRouter><Trends /></MemoryRouter>);
    expect(chartIn("card-trend-allocation")).toBeNull();
    expect(line("card-trend-allocation", "empty-state")).toBe("No asset above zero in the newest snapshot, so there is no mix to draw.");
  });
  it("one snapshot with no asset above zero: the share view says why in one line, and no second line", () => {
    data = { ...base, assets: { snapshots: [snap({ total: -1_500, assets_total: 0, liabilities_total: -1_500, liquid_total: 0,
      by_class: { "Other Debt": -1_500 } })] } };
    render(<MemoryRouter><Trends /></MemoryRouter>);
    const card = screen.getByTestId("card-trend-liquidity");
    expect(chartIn("card-trend-liquidity")).toBeNull();
    expect(within(card).getByTestId("empty-state").textContent).toBe("No snapshot has an asset above zero, so there is no "
      + "share to draw; the dollars view shows the liquid assets.");
    expect(within(card).queryByTestId("not-enough-history"), "no line about a point that is not drawn").toBeNull();
  });
  it("one snapshot: the allocation plots each class at its own share, not stacked on the others", () => {
    data = { ...base, assets: { snapshots: [classSnap({ total: 1_000, by_class: { Cash: 90, "Stocks/ETF": 910 } })] } };
    render(<MemoryRouter><Trends /></MemoryRouter>);
    const alloc = charts.options.find((o) => o.yAxis?.max === 100);
    expect(alloc.series.map((s: any) => [s.name, s.data[0][1], s.stack, s.areaStyle])).toEqual([
      ["Stocks/ETF", 91, undefined, undefined], ["Cash", 9, undefined, undefined]]);
  });
  it("two snapshots: the allocation stacks its classes as areas", () => {
    data = { ...base, assets: { snapshots: [classSnap({ snapshot_date: "2026-05-01", total: 1_000, by_class: { Cash: 100, "Stocks/ETF": 900 } }),
      classSnap({ snapshot_date: "2026-06-01", total: 1_000, by_class: { Cash: 90, "Stocks/ETF": 910 } })] } };
    render(<MemoryRouter><Trends /></MemoryRouter>);
    const alloc = charts.options.find((o) => o.yAxis?.max === 100);
    expect(alloc.series.every((s: any) => s.stack === "alloc" && s.areaStyle !== undefined)).toBe(true);
  });
  it("full months with no income: the savings rate says there is none to draw", () => {
    data = { ...base, cashflow: base.cashflow.map((m) => ({ ...m, income: 0, savings_rate: null })) };
    render(<MemoryRouter><Trends /></MemoryRouter>);
    expect(chartIn("card-trend-savings")).toBeNull();
    expect(line("card-trend-savings", "empty-state")).toBe("No income in the full months, so there is no savings rate to draw.");
  });
  it("snapshots that never hold an asset above zero: the share view says so, the dollars view draws the liquid assets", () => {
    data = { ...base, assets: { snapshots: [snap({ snapshot_date: "2026-05-01", total: -800, assets_total: 0, liabilities_total: -800,
      liquid_total: 0, by_class: { "Other Debt": -800 } }),
      snap({ snapshot_date: "2026-06-01", total: -1_100, assets_total: 0, liabilities_total: -1_100, liquid_total: 0,
        by_class: { "Other Debt": -1_100 } })] } };
    render(<MemoryRouter><Trends /></MemoryRouter>);
    const card = screen.getByTestId("card-trend-liquidity");
    expect(chartIn("card-trend-liquidity")).toBeNull();
    expect(within(card).getByTestId("empty-state").textContent).toMatch(/^No snapshot has an asset above zero/);
    fireEvent.click(within(card).getByRole("button", { name: "dollars" }));
    expect(chartIn("card-trend-liquidity")).not.toBeNull();
    expect(within(card).queryByTestId("empty-state")).toBeNull();
  });
});

// Every chart on the page draws its axes and grid lines in the theme's colours, as every other page's charts do: an axis
// with no split-line colour of its own takes ECharts' light default, which glares on the dark theme. The liquidity line
// is a level that never nears zero, so its axis is scaled to it; the burn's filled area and the stacked mix keep zero.
describe("Trends: axes and grid lines in the theme's colours, levels scaled to their line", () => {
  const twoSnaps = () => ({ ...base, assets: { snapshots: [
    classSnap({ snapshot_date: "2026-05-01", total: 1_000, assets_total: 1_000, liquid_total: 300, by_class: { Cash: 300, House: 700 } }),
    classSnap({ snapshot_date: "2026-06-01", total: 1_100, assets_total: 1_100, liquid_total: 320, by_class: { Cash: 320, House: 780 } })] } });
  it("each value axis draws its split lines in the chart hairline, each category axis its line in the axis colour", () => {
    data = twoSnaps();
    render(<MemoryRouter><Trends /></MemoryRouter>);
    fireEvent.click(within(screen.getByTestId("card-trend-liquidity")).getByRole("button", { name: "dollars" }));
    expect(charts.options.length).toBeGreaterThanOrEqual(5);
    for (const o of charts.options) {
      expect(o.yAxis.splitLine?.lineStyle?.color, `${o.series[0].name}: split lines`).toBe(CHART_SPLIT);
      expect(o.xAxis.axisLine?.lineStyle?.color, `${o.series[0].name}: axis line`).toBe(CHART_AXIS);
    }
  });
  it("the liquidity lines are scaled to their data; the burn's area and the allocation mix keep their zero", () => {
    data = twoSnaps();
    render(<MemoryRouter><Trends /></MemoryRouter>);
    fireEvent.click(within(screen.getByTestId("card-trend-liquidity")).getByRole("button", { name: "dollars" }));
    const named = (n: string) => charts.options.filter((o) => o.series[0].name.startsWith(n)).pop();
    expect(named("Liquid share").yAxis.scale).toBe(true);
    expect(named("Liquid assets").yAxis.scale).toBe(true);
    // a scaled axis' finest step is one its labels tell apart: whole percents, and $1s under $1K of liquid assets
    expect(named("Liquid share").yAxis.minInterval).toBe(0.01);
    expect(named("Liquid assets").yAxis.minInterval).toBe(1);
    // a share never reads past 100% or below 0%, however ECharts would pad a lone point
    expect([named("Liquid share").yAxis.min({ min: 1 }), named("Liquid share").yAxis.max({ max: 1 })]).toEqual([undefined, 1]);
    expect(named("Monthly burn").yAxis.scale).toBeUndefined();
    expect(charts.options.find((o) => o.yAxis?.max === 100).yAxis.scale).toBeUndefined();
  });
});

// The two cash-flow cards name the window their points really read: the trailing 3 and 6 months once the data holds them,
// the full months it has before that.
describe("Trends: the cash-flow cards name their real windows", () => {
  const titles = () => {
    render(<MemoryRouter><Trends /></MemoryRouter>);
    return ["card-trend-savings", "card-trend-burn"].map((id) => screen.getByTestId(id).querySelector("h2")?.textContent);
  };
  const seriesNames = () => charts.options.filter((o) => o.xAxis?.type === "category").map((o) => o.series[0].name);
  it("one full month: one full month in each title and series name, never three or six", () => {
    data = oneFullMonthData();
    expect(titles()).toEqual(["Savings rate — trailing 1 full month", "Monthly burn — average of the trailing 1 full month"]);
    expect(seriesNames()).toEqual(["Savings rate (trailing 1 mo)", "Monthly burn (trailing 1 mo avg)"]);
  });
  it("seven full months: the trailing 3 months and the 6-month average", () => {
    const months = ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"];
    data = { ...base, cashflow: months.map((month) => ({ ...base.cashflow[0], month })) };
    expect(titles()).toEqual(["Savings rate — trailing 3 months", "Monthly burn — trailing 6-month average"]);
    expect(seriesNames()).toEqual(["Savings rate (trailing 3 mo)", "Monthly burn (trailing 6 mo avg)"]);
  });
  it("no full month: the titles name no window", () => {
    data = firstWeekData();
    expect(titles()).toEqual(["Savings rate", "Monthly burn"]);
  });
});

describe("Trends' footer with one snapshot and no daily line", () => {
  it("names no net-worth chart on Home, which draws none", () => {
    data = oneFullMonthData();
    render(<MemoryRouter><Trends /></MemoryRouter>);
    const text = screen.getByText(/Everything here is recomputed from full history on load/).textContent ?? "";
    expect(text).toMatch(/^Income by stream, month by month, is on Cash Flow → Overview\. Everything here/);
    expect(text).not.toMatch(/Net worth over every snapshot/);
  });
});

describe("Trends: the page heading", () => {
  it("renders a level-1 heading", () => {
    render(<MemoryRouter><Trends /></MemoryRouter>);
    expect(screen.getByRole("heading", { level: 1, name: "Trends" })).toBeDefined();
  });
  it("still renders it when cashflow_monthly.json is missing", () => {
    missing = new Set(["cashflow_monthly.json"]);
    render(<MemoryRouter><Trends /></MemoryRouter>);
    expect(screen.getByRole("heading", { level: 1, name: "Trends" })).toBeDefined();
  });
});

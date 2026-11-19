// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// Home's net-worth decomposition compares two asset snapshots. A household with a single snapshot (any new one) has
// nothing to compare yet: the card stays on the page and says so, instead of vanishing from it.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import dayjs from "dayjs";
import type { AppData, AssetRow } from "../data/types";
import {
  acctFactory, appDataFactory, balancesOnlyData, belowZeroSnapshot, firstWeekData, mortgageFactory, oneFullMonthData,
  recFactory, snapFactory,
} from "../lib/__fixtures__";
import { clock } from "../lib/clock";
import { fmtDate } from "../lib/format";
import { OpenRoutesProvider } from "../lib/modules";
import { CHART_GAIN, setChartTheme } from "../lib/echarts";
import { NEGATIVE, OWNER_PALETTE, POSITIVE, UNKNOWN_COLOR } from "../lib/tokens";
import { Where, where } from "../test/routerProbe";

// the chart is stubbed (no canvas) and records every option it was handed
const charts = vi.hoisted(() => ({ options: [] as any[], clicks: [] as ((p: { name: string }) => void)[] }));
vi.mock("../components/EChart", () => ({
  default: (p: { option: unknown; onClick?: (p: { name: string }) => void }) => {
    charts.options.push(p.option);
    if (p.onClick) charts.clicks.push(p.onClick);
    return <div data-testid="chart" />;
  },
}));
const snap = snapFactory({});
const base = appDataFactory(snap)({});
/** a snapshot row of `value` in `asset_class`, held by `owner`: the allocation donut divides these */
const row = (asset_class: string, value: number, owner = "Joint", over: Partial<AssetRow> = {}): AssetRow => ({
  account: `${asset_class} of ${owner}`, account_id: null, institution: "Sample Bank", owner, asset_class, value,
  liquid: asset_class === "Cash" || asset_class === "Stocks/ETF", as_of: "2030-06-01", as_of_assumed: false,
  needs_review: false, source: "simplefin_balance", manual_id: null, carried_back: false, ...over,
});
let data: AppData = base;
vi.mock("../data/DataContext", () => ({
  useData: () => ({
    data, missing: new Set<string>(),
    indexes: { dataRange: null, dataThrough: null },
    prefs: { doc: { payroll: { phases: [] } } },
  }),
}));

import Home from "./Home";

afterEach(() => { cleanup(); data = base; charts.options = []; charts.clicks = []; });
const mount = () => render(<MemoryRouter><Home /></MemoryRouter>);
const withSnapshots = (dates: string[]): AppData => ({
  ...base, assets: { snapshots: dates.map((d, i) => snap({ snapshot_date: d, total: 2_000_000 + i * 100_000 })) },
});

describe("Home: the net-worth decomposition", () => {
  it("with a single snapshot keeps its card and says it needs a second one", () => {
    data = withSnapshots(["2030-06-01"]);
    mount();
    const card = screen.getByTestId("card-decomposition");
    expect(within(card).getByTestId("not-enough-history").textContent).toMatch(/^Not enough history yet — .*second snapshot/);
    expect(within(card).queryByTestId("chart")).toBeNull();
    expect(within(card).queryByRole("button", { name: "All" }), "no period to pick with one snapshot").toBeNull();
  });
  it("with two snapshots draws the waterfall and offers the period toggle", () => {
    data = withSnapshots(["2029-06-01", "2030-06-01"]);
    mount();
    const card = screen.getByTestId("card-decomposition");
    expect(within(card).queryByTestId("decomposition-empty")).toBeNull();
    expect(within(card).getByTestId("chart")).toBeDefined();
    expect(within(card).getByRole("button", { name: "All" })).toBeDefined();
  });
  it("in a window with no earlier snapshot keeps the toggle, so a longer window can be picked", () => {
    // the newest snapshot is dated Jan 1: year-to-date starts on it, so the YTD window has nothing before it
    data = withSnapshots(["2029-06-01", "2030-01-01"]);
    mount();
    const card = screen.getByTestId("card-decomposition");
    fireEvent.click(within(card).getByRole("button", { name: "YTD" }));
    expect(within(card).getByTestId("decomposition-empty").textContent).toMatch(/pick a longer one/);
    fireEvent.click(within(card).getByRole("button", { name: "All" }));
    expect(within(card).queryByTestId("decomposition-empty")).toBeNull();
    expect(within(card).getByTestId("chart")).toBeDefined();
  });
});

describe("Home: the debt tile's note follows include_credit_cards", () => {
  // the Other Debt row is the card's while cards are in net worth; off, a snapshot holds no card row, so it is a loan's
  const owed = (on: boolean): AppData => ({
    ...base,
    accounts: [acctFactory()({ id: "demo_card_0000", kind: "credit-card", asset_class: "Other Debt" })],
    assumptions: { ...base.assumptions, net_worth: { include_credit_cards: on } },
    assets: { snapshots: [snap({ by_class: { "Other Debt": -900 }, rows: [{
      account: on ? "Card" : "Family Loan", account_id: on ? "demo_card_0000" : "demo_loan_0001", institution: "Sample Bank",
      owner: "Alex", asset_class: "Other Debt", value: -900, liquid: false, as_of: "2030-06-01", as_of_assumed: false,
      needs_review: false, source: "simplefin_balance", manual_id: null, carried_back: false }] })] },
  });
  const tile = () => screen.getByText("Debt in net worth").parentElement?.textContent ?? "";
  it("off: cards are not included", () => {
    data = owed(false);
    mount();
    expect(tile()).toContain("other debt · credit cards not included");
  });
  it("on: a class that holds only the card is named for it, once", () => {
    data = owed(true);
    mount();
    expect(tile()).toContain("credit cards");
    expect(tile()).not.toMatch(/other debt|not included|credit cards included/);
  });
  it("on, with no credit-card account: it says there is no credit card, never that card balances are missing", () => {
    data = { ...owed(true), accounts: [] };
    mount();
    expect(tile()).toContain("other debt · no credit card");
    expect(tile()).not.toMatch(/not available|cards included/);
    cleanup();
    data = { ...base, assumptions: { ...base.assumptions, net_worth: { include_credit_cards: true } } };
    mount();
    expect(tile()).toBe("Debt in net worth—no debt in this snapshot · no credit card");
  });
});

describe("Home's history: the newest snapshot on the month ends' footing", () => {
  const brk = (over: Partial<AssetRow>) => row("Stocks/ETF", 0, "Joint", { account: "Broker", account_id: "brk", ...over });
  const monthEnd = snap({ snapshot_date: "2030-05-31", total: 400_000, by_class: { "Stocks/ETF": 300_000, Cash: 100_000 },
    by_owner: { Joint: 400_000 }, rows: [brk({ value: 300_000, source: "simplefin_balance" }), row("Cash", 100_000)] });
  const live = snap({ snapshot_date: "2030-06-15", total: 407_500, by_class: { "Stocks/ETF": 320_000, Margin: -12_500, Cash: 100_000 },
    by_owner: { Joint: 407_500 }, rows: [brk({ value: 320_000, source: "positions" }),
      brk({ account: "Broker Margin", asset_class: "Margin", value: -12_500, liquid: false }), row("Cash", 100_000)] });
  const area = () => [...charts.options].reverse().find((o) => o.series?.some((s: any) => s.name === "Net worth (snapshots)"));
  it("by class the last point is the margin account net of its loan, as every month end holds it, and the card says why", () => {
    data = { ...base, assets: { snapshots: [monthEnd, live] } };
    mount();
    const series = Object.fromEntries(area().series.filter((s: any) => s.stack).map((s: any) => [s.name, s.data.at(-1)[1]]));
    expect(series).toEqual({ "Stocks/ETF": 307_500, Cash: 100_000 });
    // stacked bands keep the zero baseline they are drawn from
    expect(area().yAxis.scale).toBeUndefined();
    expect(screen.getByTestId("networth-history-footing").textContent).toBe("Month ends hold a margin account net of its loan; "
      + "the last point is drawn the same way, so it can differ from today's figures above, which count the positions and the "
      + "loan separately.");
    // the now tiles keep the snapshot as it is
    expect(screen.getByText("Debt in net worth", { selector: "div" }).parentElement?.textContent).toContain("−$12,500");
  });
  it("no margin account: the last point is the snapshot's own and nothing is said", () => {
    const plain = snap({ ...live, total: 420_000, by_class: { "Stocks/ETF": 320_000, Cash: 100_000 }, by_owner: { Joint: 420_000 },
      rows: live.rows.filter((r) => r.asset_class !== "Margin") });
    data = { ...base, assets: { snapshots: [monthEnd, plain] } };
    mount();
    const series = Object.fromEntries(area().series.filter((s: any) => s.stack).map((s: any) => [s.name, s.data.at(-1)[1]]));
    expect(series).toEqual({ "Stocks/ETF": 320_000, Cash: 100_000 });
    expect(screen.queryByTestId("networth-history-footing")).toBeNull();
  });
});

describe("Home by sign: an overdrawn account and a hand-stated value below zero", () => {
  it("the debt tile is every row below zero, the figure Balances' Total liabilities shows; the liquid tile its assets", () => {
    data = { ...base, assets: { snapshots: [belowZeroSnapshot()] },
      assumptions: { ...base.assumptions, mortgage: mortgageFactory({ account_id: "loan", balance: 250_000, balance_as_of: "2026-06-01" }) } };
    mount();
    const tile = (label: string) => screen.getByText(label, { selector: "div" }).parentElement?.textContent ?? "";
    expect(tile("Debt in net worth")).toBe("Debt in net worth−$252,300mortgage + Joint Checking (overdrawn) + Boat (below zero)");
    expect(tile("Liquid assets")).toBe("Liquid assets$15,000" + "3.6% of assets — cash and investment accounts other than retirement and 529, plus hand-entered assets marked liquid");
    expect(tile("Net worth")).toMatch(/^Net worth\$162,700/);
  });
});

// A share of today's assets is of the assets reported so far: while the newest snapshot misses an account that would hold
// assets (configured, its first balance not arrived), the liquid share and the donut carry the one mark that says so and
// links to Data, which names them. An account of a debt kind - the configured mortgage - is in no share of assets.
describe("Home: a share of an incomplete total says so", () => {
  const tile = () => screen.getByText("Liquid assets", { selector: "div" }).parentElement?.textContent ?? "";
  const rows = [row("Cash", 30_000), row("Retirement", 70_000)];
  const sums = { total: 100_000, liquid_total: 30_000 };
  const accounts = [acctFactory()({ id: "brk", name: "Sample Brokerage", kind: "brokerage" }),
    acctFactory()({ id: "sav", name: "Sample Savings", kind: "savings" }),
    acctFactory()({ id: "loan", name: "Sample Mortgage", kind: "loan" })];
  it("a brokerage and a savings account not reported: the liquid share and the donut are of the assets reported so far", () => {
    data = { ...base, accounts, assets: { snapshots: [snap({ ...sums, rows, missing: ["Sample Brokerage", "Sample Savings"] })] } };
    mount();
    expect(tile()).toBe("Liquid assets$30,000"
      + "30.0% of the assets reported so far — 2 accounts not yet reported · cash and investment accounts other than retirement and 529, plus hand-entered assets marked liquid");
    const donut = within(screen.getByTestId("card-allocation-donut"));
    expect(donut.getByText(/^Shares/).textContent).toBe("Shares of the assets reported so far — 2 accounts not yet reported");
    for (const mark of screen.getAllByTestId("not-yet-reported")) {
      expect(mark.tagName, "a link to Data, where the accounts are named").toBe("A");
      expect(mark.getAttribute("href")).toBe("/data?card=caveats");
    }
  });
  it("only the mortgage not reported: the assets are complete, so neither share carries a mark", () => {
    data = { ...base, accounts, assets: { snapshots: [snap({ ...sums, rows, mortgage_included: false, missing: ["Sample Mortgage"] })] } };
    mount();
    expect(tile()).toBe("Liquid assets$30,000" + "30.0% of assets — cash and investment accounts other than retirement and 529, plus hand-entered assets marked liquid");
    expect(screen.queryByTestId("not-yet-reported")).toBeNull();
  });
  it("the mortgage and the brokerage: the mark counts the brokerage alone", () => {
    data = { ...base, accounts, assets: { snapshots: [snap({ ...sums, rows, mortgage_included: false,
      missing: ["Sample Brokerage", "Sample Mortgage"] })] } };
    mount();
    expect(screen.getAllByTestId("not-yet-reported").map((m) => m.textContent))
      .toEqual(Array(2).fill("of the assets reported so far — 1 account not yet reported"));
  });
  it("the control: a snapshot that misses none carries no mark", () => {
    data = { ...base, accounts, assets: { snapshots: [snap({ ...sums, rows })] } };
    mount();
    expect(tile()).toBe("Liquid assets$30,000" + "30.0% of assets — cash and investment accounts other than retirement and 529, plus hand-entered assets marked liquid");
    expect(screen.queryByTestId("not-yet-reported")).toBeNull();
  });
});

describe("Home: the waterfall's debt bar compares like with like and is named by its sign", () => {
  const brk = (over: Partial<AssetRow>) => row("Stocks/ETF", 0, "Joint", { account: "Broker", account_id: "brk", ...over });
  // a month end holds the margin account as one row (net of its 12,500 loan); the newest snapshot as its positions and
  // the loan, and the card balance rose by 2,000 over the year
  const twoEnds = (cardFrom: number, cardTo: number): AppData => ({ ...base, assets: { snapshots: [
    snap({ snapshot_date: "2029-06-30", total: 300_000 + cardFrom, by_class: { "Stocks/ETF": 300_000, "Other Debt": cardFrom },
      rows: [brk({ value: 300_000 }), row("Other Debt", cardFrom, "Joint", { account_id: "card" })] }),
    snap({ snapshot_date: "2030-06-15", total: 320_000 - 12_500 + cardTo,
      by_class: { "Stocks/ETF": 320_000, Margin: -12_500, "Other Debt": cardTo },
      rows: [brk({ value: 320_000 }), brk({ account: "Broker Margin", asset_class: "Margin", value: -12_500, liquid: false }),
        row("Other Debt", cardTo, "Joint", { account_id: "card" })] }),
  ] } });
  const axis = () => [...charts.options].reverse().find((o) => o.xAxis?.type === "category" && o.series?.[1]?.name === "change");
  it("debt that rose reads 'Debt added', and the margin loan beside its positions is not counted as new debt", () => {
    data = twoEnds(-1_000, -3_000);
    mount();
    const option = axis();
    const labels: string[] = option.xAxis.data;
    expect(labels).toContain("Debt added");
    expect(labels.some((l) => /paydown|Debt$/.test(l)), "never a bar whose sign the reader decodes").toBe(false);
    // the bar's own figure: the card's 2,000 more, not the margin loan's 12,500 on top
    const i = labels.indexOf("Debt added");
    expect(option.series[1].data[i].value).toBeCloseTo(2_000);
    expect(screen.getByTestId("card-decomposition").textContent).toContain("A margin loan is counted inside its brokerage account");
  });
  it("debt that fell reads 'Debt paid down'", () => {
    data = twoEnds(-3_000, -1_000);
    mount();
    expect(axis().xAxis.data).toContain("Debt paid down");
  });
  // read in the light theme, where the gain green differs from POSITIVE (equal in the dark one, so a bar gone back to
  // POSITIVE would pass there)
  it("a gain is drawn in the gain green (CHART_GAIN: 3:1 on a card in both themes), a loss in red", () => {
    setChartTheme("light");
    try {
      expect(CHART_GAIN, "the light theme's gain green is not POSITIVE").not.toBe(POSITIVE);
      data = twoEnds(-3_000, -1_000);
      mount();
      const option = axis();
      const labels: string[] = option.xAxis.data;
      const colorOf = (label: string) => option.series[1].data[labels.indexOf(label)].itemStyle.color;
      expect(colorOf("Debt paid down")).toBe(CHART_GAIN);
      cleanup();
      data = twoEnds(-1_000, -3_000);
      mount();
      expect(axis().series[1].data[(axis().xAxis.data as string[]).indexOf("Debt added")].itemStyle.color).toBe(NEGATIVE);
    } finally {
      setChartTheme("dark");
    }
  });
});

describe("Home: hand-stated values carried back are named under the history", () => {
  it("each asset carried back into an earlier month end is named with its own first stated amount and day", () => {
    const car = (as_of: string, carried_back: boolean) => row("Car", 21_000, "Sam", { account: "Sedan", manual_id: "sedan",
      liquid: false, as_of, carried_back, source: "stated" });
    data = { ...base, assets: { snapshots: [
      snap({ snapshot_date: "2030-02-28", rows: [car("2030-03-26", true), row("House", 600_000, "Joint", { account: "Home",
        manual_id: "home", liquid: false, as_of: "2030-05-01", carried_back: true, source: "stated" })] }),
      snap({ snapshot_date: "2030-06-01", rows: [car("2030-03-26", false)] }),
    ] } };
    mount();
    expect(screen.getByTestId("networth-history-carried").textContent)
      .toBe("Sedan is shown at its first stated amount ($21,000, stated Mar 26, 2030) in earlier month ends; "
        + "Home is shown at its first stated amount ($600,000, stated May 1, 2030) in earlier month ends.");
  });
  it("no carried row: no such line", () => {
    data = withSnapshots(["2029-06-01", "2030-06-01"]);
    mount();
    expect(screen.queryByTestId("networth-history-carried")).toBeNull();
  });
  it("a value carried back into the current snapshot only is in no earlier month end: no such line", () => {
    // stated after the newest account balance: the current snapshot holds it carried back, the month end before does not hold it
    data = { ...base, assets: { snapshots: [
      snap({ snapshot_date: "2030-05-31", rows: [row("Cash", 1_000, "Joint")] }),
      snap({ snapshot_date: "2030-06-01", rows: [row("Car", 21_000, "Sam", { account: "Sedan", manual_id: "sedan", liquid: false,
        as_of: "2030-06-20", carried_back: true, source: "stated" })] }),
    ] } };
    mount();
    expect(screen.queryByTestId("networth-history-carried")).toBeNull();
  });
});

describe("Home: the data-quality card", () => {
  it("lists a warning the ETL raised once per statement file once per run, with no duplicate key", () => {
    const run = (detail: string) => ({ name: "t_sav.total_credits", status: "warn" as const, detail });
    data = { ...base, quality: { ...base.quality, checks: [run("file one"), run("file two")] } };
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      mount();
      const card = screen.getByTestId("card-data-quality");
      fireEvent.click(within(card).getByRole("button", { name: "show" }));
      expect(card.textContent).toContain("file one");
      expect(card.textContent).toContain("file two");
      expect(error.mock.calls.some((c) => String(c[0]).includes("same key")), "no duplicate React key").toBe(false);
    } finally {
      error.mockRestore();
    }
  });
});

describe("Home: the net-worth history's snapshot table", () => {
  const summary = () => screen.getByTestId("card-networth-history").querySelector("summary")?.textContent ?? "";
  it("a single snapshot is named by its date, never a span \"since\" its own year", () => {
    data = withSnapshots(["2030-06-01"]);
    mount();
    expect(summary()).toBe("1 snapshot, Jun 1, 2030 — show the table");
  });
  it("and more than one in the plural", () => {
    data = withSnapshots(["2029-06-01", "2030-06-01"]);
    mount();
    expect(summary()).toBe("2 snapshots since 2029 — show the table");
  });
});

// One snapshot is enough for the allocation donut by class or by owner, though not for a breakdown over time: the toggle
// stays, it moves the donut, and the history draws the line and the total alone on either side.
describe("Home: one snapshot, by class or by owner", () => {
  it("the toggle is there, by owner turns the donut to the owners, and the history keeps its line with no lone dots", () => {
    const one = snap({ snapshot_date: "2030-06-01", by_class: { Cash: 300_000, "Stocks/ETF": 700_000 },
      by_owner: { Alex: 600_000, Sam: 400_000 },
      rows: [row("Cash", 300_000, "Alex"), row("Stocks/ETF", 300_000, "Alex"), row("Stocks/ETF", 400_000, "Sam")] });
    data = { ...base, assumptions: { ...base.assumptions, owners: ["Alex", "Sam", "Joint"] }, assets: { snapshots: [one] },
      networthDaily: { series: ["2030-05-30", "2030-05-31", "2030-06-01"].map((date) => ({ date, networth: 1_000_000,
        coverage: 1, source: "simplefin" })), current_total: 1_000_000, current_snapshot_date: "2030-06-01" } };
    mount();
    const history = screen.getByTestId("card-networth-history");
    const pie = () => [...charts.options].reverse().find((o) => o.series?.[0]?.type === "pie");
    expect(pie().series[0].data.map((d: any) => d.name).sort()).toEqual(["Cash", "Stocks/ETF"]);
    fireEvent.click(within(history).getByRole("button", { name: "by owner" }));
    expect(pie().series[0].data.map((d: any) => d.name).sort()).toEqual(["Alex", "Sam"]);
    // the history, on the owner side too: the snapshot's total and the daily line, never an owner as a lone dot
    const area = [...charts.options].reverse().find((o) => o.series?.some((s: any) => s.name === "Net worth (snapshots)"));
    expect(area.series.map((s: any) => s.name)).toEqual(["Net worth (snapshots)", "Net worth (daily · bank feed)"]);
    expect(area.series.some((s: any) => s.stack), "no stacked class or owner series").toBe(false);
    expect(within(history).getByTestId("not-enough-history").textContent).toMatch(/breakdown by class and by owner/);
  });
  it("with one snapshot and no daily line the toggle still moves the donut, and the history says it needs a second one", () => {
    const one = snap({ snapshot_date: "2030-06-01", by_class: { Cash: 1_000_000 }, by_owner: { Alex: 1_000_000 },
      rows: [row("Cash", 1_000_000, "Alex")] });
    data = { ...base, assets: { snapshots: [one] } };
    mount();
    const history = screen.getByTestId("card-networth-history");
    fireEvent.click(within(history).getByRole("button", { name: "by owner" }));
    const pie = [...charts.options].reverse().find((o) => o.series?.[0]?.type === "pie");
    expect(pie.series[0].data.map((d: any) => d.name)).toEqual(["Alex"]);
    expect(within(history).queryByTestId("chart")).toBeNull();
    expect(within(history).getByTestId("not-enough-history").textContent).toMatch(/once a second snapshot exists/);
  });
});

// No figure the data cannot support: a household in its first week (no full month, one snapshot, no home, no mortgage
// account) and one whose snapshot owes nothing see no clause about what they do not have, no share of a zero total and
// no count of months it does not have.
describe("Home: the tiles on a thin household", () => {
  const tile = (label: string) => screen.getByText(label, { selector: "div" }).parentElement?.textContent ?? "";
  it("first week: the net-worth tile names no home and no mortgage, and says there is no change yet", () => {
    data = firstWeekData();
    mount();
    const nw = tile("Net worth");
    expect(nw).toContain("Not enough history yet for a change");
    expect(nw, "no home row: no home clause, never a $0 home").not.toMatch(/home|\$0/);
    expect(nw, "no mortgage account: nothing said of one").not.toMatch(/mortgage/i);
  });
  it("a House row is named in the tile, with the mortgage clause only where there is a mortgage account", () => {
    data = balancesOnlyData();
    mount();
    expect(tile("Net worth")).toMatch(/incl\. \$400K home \(owner estimate\)$/);
    cleanup();
    data = { ...balancesOnlyData(), assumptions: { ...base.assumptions, mortgage: mortgageFactory() } };
    mount();
    expect(tile("Net worth")).toMatch(/incl\. \$400K home \(owner estimate\) · mortgage included$/);
  });
  it("first week: the savings rate names no window of 0 months and says there is not enough history", () => {
    data = firstWeekData();
    mount();
    const kpi = screen.getByTestId("kpi-savings-rate-ttm").textContent ?? "";
    expect(kpi).toBe("Savings rate—Not enough history yet: no full month of cash flow");
  });
  it("one full month: the savings rate is labelled with its one month", () => {
    data = oneFullMonthData();
    mount();
    const kpi = screen.getByTestId("kpi-savings-rate-ttm").textContent ?? "";
    expect(kpi).toContain("Savings rate (1 mo)");
    expect(kpi).toContain("trailing 1 full month · cash basis");
  });
  it("the liquid tile's share is of the snapshot's assets, never of net worth, so debts cannot push it past 100%", () => {
    // 1,500 cash and 1,000 of illiquid assets against 2,000 of debt: a net worth of 500, of which 1,500 would read 300%
    data = { ...base, assets: { snapshots: [snap({ total: 500, assets_total: 2_500, liabilities_total: -2_000, liquid_total: 1_500,
      by_class: { Cash: 1_500, Car: 1_000, "Other Debt": -2_000 } })] } };
    mount();
    expect(tile("Liquid assets")).toBe("Liquid assets$1,500" + "60.0% of assets — cash and investment accounts other than retirement and 529, plus hand-entered assets marked liquid");
  });
  it("a net worth of 0 still has assets to share; with no asset above zero the liquid tile prints no share", () => {
    data = { ...base, assets: { snapshots: [snap({ total: 0, assets_total: 1_500, liabilities_total: -1_500, liquid_total: 1_500,
      by_class: { Cash: 1_500, "Other Debt": -1_500 } })] } };
    mount();
    expect(tile("Liquid assets")).toBe("Liquid assets$1,500" + "100.0% of assets — cash and investment accounts other than retirement and 529, plus hand-entered assets marked liquid");
    cleanup();
    data = { ...base, assets: { snapshots: [snap({ total: -1_500, assets_total: 0, liabilities_total: -1_500, liquid_total: 0,
      by_class: { "Other Debt": -1_500 } })] } };
    mount();
    const liquid = tile("Liquid assets");
    expect(liquid).toBe("Liquid assets$0cash and investment accounts other than retirement and 529, plus hand-entered assets marked liquid");
    expect(liquid).not.toMatch(/%|NaN|Infinity|∞/);
  });
  it("a snapshot with no debt row: the debt tile is \"—\", never a red $0", () => {
    data = oneFullMonthData();
    mount();
    const debt = screen.getByText("Debt in net worth", { selector: "div" }).parentElement!;
    // no credit-card account either: no card left out to name
    expect(debt.textContent).toBe("Debt in net worth—no debt in this snapshot");
    expect(debt.querySelector(".text-red-400"), "no warning tone without a debt").toBeNull();
  });
  it("a debt row that owes nothing is a true $0, in the neutral tone", () => {
    data = { ...base, assets: { snapshots: [snap({ by_class: { Cash: 1_000, "Other Debt": 0 }, rows: [{
      account: "Card", account_id: "card", institution: "Sample Bank", owner: "Joint", asset_class: "Other Debt", value: 0,
      liquid: false, as_of: "2026-06-01", as_of_assumed: false, needs_review: false, source: "simplefin_balance",
      manual_id: null, carried_back: false }] })] } };
    mount();
    const debt = screen.getByText("Debt in net worth", { selector: "div" }).parentElement!;
    expect(debt.textContent).toMatch(/^Debt in net worth\$0/);
    expect(debt.querySelector(".text-red-400")).toBeNull();
  });
});

describe("Home: the allocation donut's slices", () => {
  /** mounts Home with the House page showing or not, clicks the slice of `cls` and says where the page went */
  const click = (cls: string, houseShows: boolean) => {
    data = { ...base, assets: { snapshots: [snap({ by_class: { House: 600_000, Cash: 100_000 },
      rows: [row("House", 600_000), row("Cash", 100_000)] })] } };
    const open = (path: string) => houseShows || !path.startsWith("/planning/home");
    render(
      <MemoryRouter initialEntries={["/"]}>
        <OpenRoutesProvider value={open}><Home /></OpenRoutesProvider><Where />
      </MemoryRouter>);
    expect(charts.clicks.length > 0, "the donut takes clicks").toBe(true);
    act(() => charts.clicks[charts.clicks.length - 1]({ name: cls }));
    const at = where().split("|")[0];
    cleanup(); charts.clicks = [];
    return at;
  };
  it("the House slice opens the House page while it shows, and Balances while it is off", () => {
    expect(click("House", true)).toBe("/planning/home");
    expect(click("House", false)).toBe("/accounts");
    expect(click("Cash", false), "a slice whose page always shows is unchanged").toBe("/accounts");
  });
});

describe("Home: the cards on a thin household", () => {
  it("an allocation with no asset above zero is the card's empty state, not an empty ring", () => {
    data = { ...base, assets: { snapshots: [snap({ total: -1_500, by_class: { "Other Debt": -1_500 }, by_owner: { Joint: -1_500 },
      rows: [row("Other Debt", -1_500)] })] } };
    mount();
    const card = screen.getByTestId("card-allocation-donut");
    expect(card.textContent).toBe("Current allocation of assetsNothing to divide yet: no asset above zero in the newest snapshot.");
    expect(within(card).queryByTestId("chart")).toBeNull();
    expect(charts.options.some((o) => o.series?.[0]?.type === "pie"), "no pie is drawn").toBe(false);
  });
  it("by class or by owner the donut divides the same assets: a debt is in no slice, so an owner's share is not net of it", () => {
    // Joint holds 300 of cash and owes 500: its net worth is below zero, its assets are not
    data = { ...base, assets: { snapshots: [snap({ total: -200, assets_total: 300, liabilities_total: -500,
      by_class: { Cash: 300, "Other Debt": -500 }, by_owner: { Joint: -200 },
      rows: [row("Cash", 300), row("Other Debt", -500)] })] } };
    mount();
    const pie = () => [...charts.options].reverse().find((o) => o.series?.[0]?.type === "pie");
    expect(pie().series[0].data.map((d: any) => [d.name, d.value])).toEqual([["Cash", 300]]);
    expect(pie().legend.formatter("Cash")).toBe("Cash  100%");
    fireEvent.click(within(screen.getByTestId("card-networth-history")).getByRole("button", { name: "by owner" }));
    expect(pie().series[0].data.map((d: any) => [d.name, d.value])).toEqual([["Joint", 300]]);
    expect(screen.getByTestId("card-allocation-donut").querySelector("h2")?.textContent)
      .toBe("Current allocation of assets — click a slice for detail");
  });
  it("on a rewound view the empty donut names the snapshot shown, not the newest", () => {
    data = { ...base, assets: { snapshots: [snap({ snapshot_date: "2030-05-01", total: -500, by_class: { "Other Debt": -500 },
      rows: [row("Other Debt", -500)] }),
      snap({ snapshot_date: "2030-06-01", total: 1_000, by_class: { Cash: 1_000 }, rows: [row("Cash", 1_000)] })] } };
    render(<MemoryRouter initialEntries={["/?snap=2030-05-01"]}><Home /></MemoryRouter>);
    expect(screen.getByTestId("card-allocation-donut").textContent)
      .toBe("Current allocation of assetsNothing to divide yet: no asset above zero in the snapshot of May 1, 2030.");
  });
  it("a delta against an earlier snapshot whose total was 0 prints the change and no share of it", () => {
    data = { ...base, assets: { snapshots: [snap({ snapshot_date: "2030-05-01", total: 0 }), snap({ snapshot_date: "2030-06-01", total: 5_000 })] } };
    mount();
    const chips = [...screen.getByTestId("card-snapshot-deltas").querySelectorAll("li")].map((c) => c.textContent);
    expect(chips).toEqual(["vs May 1, 2030: +$5,000"]);
    expect(screen.getByText("Net worth", { selector: "div" }).parentElement?.textContent).not.toMatch(/\(—\)|NaN|Infinity|∞/);
  });
  it("the chips sit under a heading that says what they compare, the newest snapshot first, and wrap rather than scroll", () => {
    data = { ...base, assets: { snapshots: ["2030-03-01", "2030-04-01", "2030-05-01", "2030-06-01"].map((snapshot_date, i) =>
      snap({ snapshot_date, total: 1_000 * (i + 1) })) } };
    mount();
    const strip = screen.getByTestId("card-snapshot-deltas");
    expect(within(strip).getByRole("heading", { level: 2 }).textContent).toBe("Net worth now against each earlier snapshot, newest first");
    expect(strip.getAttribute("aria-labelledby")).toBe("snapshot-deltas-heading");
    expect([...strip.querySelectorAll("li")].map((c) => c.textContent?.split(":")[0]))
      .toEqual(["vs May 1, 2030", "vs Apr 1, 2030", "vs Mar 1, 2030"]);
    const list = strip.querySelector("ul") as HTMLElement;
    expect(list.className).toMatch(/\bflex-wrap\b/);
    expect(list.className).not.toMatch(/overflow-x-auto/);
  });
  it("the newest six chips show; the rest come with one control that says how many and goes back", () => {
    const dates = Array.from({ length: 10 }, (_, i) => `2030-${String(i + 1).padStart(2, "0")}-01`);
    data = { ...base, assets: { snapshots: dates.map((snapshot_date, i) => snap({ snapshot_date, total: 1_000 * (i + 1) })) } };
    mount();
    const strip = screen.getByTestId("card-snapshot-deltas");
    const chips = () => [...strip.querySelectorAll("li")].map((c) => c.textContent?.split(":")[0]);
    expect(chips()).toEqual(["vs Sep 1, 2030", "vs Aug 1, 2030", "vs Jul 1, 2030", "vs Jun 1, 2030", "vs May 1, 2030", "vs Apr 1, 2030"]);
    const more = within(strip).getByRole("button", { name: "Show all 9" });
    expect(more.getAttribute("aria-expanded")).toBe("false");
    expect(more.getAttribute("aria-controls")).toBe(strip.querySelector("ul")!.id);
    fireEvent.click(more);
    expect(chips().length).toBe(9);
    expect(chips().at(-1)).toBe("vs Jan 1, 2030");
    const fewer = within(strip).getByRole("button", { name: "Show fewer" });
    expect(fewer.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(fewer);
    expect(chips().length).toBe(6);
    expect(within(strip).getByRole("heading", { level: 2 })).toBeDefined();
  });
  // the two oldest snapshots pre-date the mortgage data; neither is among the newest six chips, nor the one the net-worth
  // tile's change is measured from (the snapshot nearest a year back, Oct 1, 2029)
  const daggerDates = ["2028-01-01", "2028-02-01", "2029-10-01", "2029-11-01", "2029-12-01", "2030-01-01", "2030-02-01",
    "2030-03-01", "2030-04-01", "2030-05-01"];
  const daggered = (before: number) => ({ ...base, assets: { snapshots: daggerDates.map((snapshot_date, i) =>
    snap({ snapshot_date, total: 1_000 * (i + 1), mortgage_included: i >= before })) } });
  it("the † footnote explains a † on screen: none while the chips that carry it are folded, then with them", () => {
    data = daggered(2);
    mount();
    const strip = screen.getByTestId("card-snapshot-deltas");
    expect([...strip.querySelectorAll("li")].some((c) => c.textContent?.includes("†")), "no † shown").toBe(false);
    expect(screen.queryByTestId("dagger-footnote"), "folded: no footnote").toBeNull();
    fireEvent.click(within(strip).getByRole("button", { name: "Show all 9" }));
    expect([...strip.querySelectorAll("li")].filter((c) => c.textContent?.includes("†")).length).toBe(2);
    expect(screen.getByTestId("dagger-footnote").textContent).toMatch(/^†/);
    fireEvent.click(within(strip).getByRole("button", { name: "Show fewer" }));
    expect(screen.queryByTestId("dagger-footnote"), "folded again").toBeNull();
  });
  it("the control: a † on a chip among the six shown brings the footnote at once (rewound, so the tile shows no change)", () => {
    data = daggered(5);
    render(<MemoryRouter initialEntries={["/?snap=2030-04-01"]}><Home /></MemoryRouter>);
    const strip = screen.getByTestId("card-snapshot-deltas");
    expect([...strip.querySelectorAll("li")].filter((c) => c.textContent?.includes("†")).length, "Dec and Nov shown").toBe(2);
    expect(screen.getByTestId("dagger-footnote")).toBeTruthy();
  });
  it("with six other snapshots or fewer every chip shows and there is no control", () => {
    const dates = Array.from({ length: 7 }, (_, i) => `2030-${String(i + 1).padStart(2, "0")}-01`);
    data = { ...base, assets: { snapshots: dates.map((snapshot_date, i) => snap({ snapshot_date, total: 1_000 * (i + 1) })) } };
    mount();
    const strip = screen.getByTestId("card-snapshot-deltas");
    expect(strip.querySelectorAll("li").length).toBe(6);
    expect(within(strip).queryByRole("button")).toBeNull();
  });
  it("rewound, the heading names the snapshot shown and the chips hold every other one, the newest first", () => {
    data = { ...base, assets: { snapshots: ["2030-03-01", "2030-04-01", "2030-05-01", "2030-06-01"].map((snapshot_date, i) =>
      snap({ snapshot_date, total: 1_000 * (i + 1) })) } };
    render(<MemoryRouter initialEntries={["/?snap=2030-04-01"]}><Home /></MemoryRouter>);
    const strip = screen.getByTestId("card-snapshot-deltas");
    expect(within(strip).getByRole("heading", { level: 2 }).textContent)
      .toBe("Net worth on Apr 1, 2030 against each other snapshot, newest first");
    expect([...strip.querySelectorAll("li")].map((c) => c.textContent?.split(":")[0]))
      .toEqual(["vs Jun 1, 2030", "vs May 1, 2030", "vs Mar 1, 2030"]);
  });
  it("two snapshots and no $100K crossing: no word about pins", () => {
    data = { ...base, assets: { snapshots: [snap({ snapshot_date: "2030-05-01", total: 10_000 }), snap({ snapshot_date: "2030-06-01", total: 12_000 })] } };
    mount();
    expect(screen.getByTestId("card-networth-history").textContent).not.toMatch(/pins mark/);
    expect(screen.queryByTestId("networth-history-notes")).toBeNull();
  });
  it("a $100K crossing between two snapshots: the pins are named", () => {
    data = { ...base, assets: { snapshots: [snap({ snapshot_date: "2030-05-01", total: 90_000 }), snap({ snapshot_date: "2030-06-01", total: 110_000 })] } };
    mount();
    expect(screen.getByTestId("networth-history-notes").textContent).toMatch(/^pins mark the latest interpolated \$100K milestone crossings/);
  });
});

// "Today" is the household's day: Home reads it in the household's zone and hands it to the next-30-days card, so a
// household opened from a browser in another zone sees the bills due it sees at home. At this instant it is Jul 21 in
// Kiritimati (UTC+14) and Jul 20 from Pago Pago (UTC-11) to UTC.
describe("Home: the next 30 days are judged on the household's day, not the browser's", () => {
  const INSTANT = "2026-07-20T12:00:00Z";
  const dueChip = () => within(screen.getByTestId("card-next-30-days")).queryByText(/^due · expected /);
  it("at an instant where the two days differ, a bill due on the household's day is not due on the browser's", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    try {
      vi.setSystemTime(new Date(INSTANT));
      const browserDay = clock.today(null);
      const zone = clock.today("Pacific/Kiritimati") !== browserDay ? "Pacific/Kiritimati" : "Pacific/Pago_Pago";
      const householdDay = clock.today(zone);
      expect(householdDay, "the two days differ at this instant").not.toBe(browserDay);
      // a household a day ahead is on the bill's first due day (the day before it is expected) while the browser is a day
      // before that; a household a day behind is on the last day of the grace window while the browser is past it
      const expected = dayjs(householdDay).add(householdDay > browserDay ? 1 : -6, "day").format("YYYY-MM-DD");
      const bill = recFactory()({ vendor: "Water bill", schedule: true, grace_days: 6, last_seen: "2026-05-01",
        next_expected: expected, expected_dates: [expected] });
      const at = (timezone: string | null): AppData => ({ ...base, recurring: [bill],
        assumptions: { ...base.assumptions, household: { ...base.assumptions.household, timezone } } });
      data = at(zone);
      mount();
      expect(dueChip()?.firstChild?.textContent, "due on the household's day").toBe(`due · expected ${fmtDate(expected)}`);
      cleanup();
      data = at(null);   // an export with no zone: the browser's own day
      mount();
      expect(dueChip(), "on the browser's day the same bill is not due").toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

// Owners are data: the by-owner chart and donut colour an owner by its place in assumptions.owners, never by its name.
describe("Home: the by-owner colours", () => {
  it("each owner takes the palette entry at its index in the owner list; an owner the list lacks is the neutral grey", () => {
    const by_owner = { Alex: 400_000, Sam: 300_000, Joint: 200_000, Pat: 100_000 };
    const rows = Object.entries(by_owner).map(([owner, v]) => row("Cash", v, owner));
    // two snapshots: the history draws a line over time (one point alone is no chart)
    data = { ...base, assumptions: { ...base.assumptions, owners: ["Sam", "Alex", "Joint"] },
      assets: { snapshots: [snap({ snapshot_date: "2030-05-31", by_owner, rows }), snap({ snapshot_date: "2030-06-01", by_owner, rows })] } };
    mount();
    fireEvent.click(within(screen.getByTestId("card-networth-history")).getByText("by owner"));
    const want = { Sam: OWNER_PALETTE[0], Alex: OWNER_PALETTE[1], Joint: OWNER_PALETTE[2], Pat: UNKNOWN_COLOR };
    const newest = (pick: (o: any) => boolean) => [...charts.options].reverse().find(pick);
    const area = newest((o) => o.series?.some((s: any) => s.stack === "assets" && s.name === "Pat"));
    expect(Object.fromEntries(area.series.filter((s: any) => s.stack).map((s: any) => [s.name, s.color]))).toEqual(want);
    const donut = newest((o) => o.series?.[0]?.type === "pie" && o.series[0].data.some((d: any) => d.name === "Pat"));
    expect(Object.fromEntries(donut.series[0].data.map((d: any) => [d.name, d.itemStyle.color]))).toEqual(want);
  });
});

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Holding } from "../data/types";
import {
  acctFactory, appDataFactory, cfmFactory, firstWeekData, oneFullMonthData, snapFactory, txFactory,
} from "../lib/__fixtures__";
import { MASK, setPrivate } from "../lib/privacy";

const holding = (over: Partial<Holding>): Holding => ({
  account: "Brokerage (Joint)", account_id: null, institution: "Broker", owner: "Joint", symbol: "VTI", name: "Total Market",
  asset_class: "Stocks/ETF", quantity: 1, value: 1000, cost_basis: null, as_of: "2026-07-01", liquid: true, notes: null, ...over,
});
const baseData = appDataFactory(snapFactory({ total: 100_000, by_class: {} }))({});
let data = baseData;
// the family's in-app asset-class targets (a test sets some)
const saved = vi.hoisted(() => ({ targets: {} as Record<string, number> }));
vi.mock("../data/DataContext", () => ({
  useData: () => ({
    data, missing: new Set<string>(),
    indexes: { dataRange: { first: "2025-01-01", last: "2026-07-20" } },
    prefs: { doc: { targets: saved.targets, target_mix: null, budgets: {}, projections: {}, payroll: { phases: [] }, savedViews: [], reports: [] },
      localOnly: false, setTarget: () => {}, setTargetMix: () => true },
  }),
}));
vi.mock("../components/EChart", () => ({ default: () => <div data-testid="chart" /> }));

import Investments from "./Investments";

afterEach(() => { cleanup(); data = baseData; saved.targets = {}; });
const callout = () => screen.getByText("Positions >10% of investable assets").parentElement!.textContent ?? "";

describe("Investments: the concentration callouts", () => {
  // a feed can name a transferred lot "<holding> (acquired 11/18/2019) 2024-03-07": a US date and an ISO date in one label
  it("names a position in the app's date form: an ISO date and a US slash date both read \"Mon d, yyyy\"", () => {
    data = { ...baseData, holdings: [
      holding({ symbol: null, name: "Sample Co. shares (lot) — Lot Transfer (acquired 11/18/2019) 2024-03-07", asset_class: "Private Stock", value: 60_000 }),
      holding({ symbol: "VTI", value: 40_000 }),
    ] };
    render(<MemoryRouter><Investments /></MemoryRouter>);
    const text = callout();
    expect(text).toContain("Sample Co. shares (lot) — Lot Transfer (acquired Nov 18, 2019) Mar 7, 2024 60.0%");
    expect(text).not.toMatch(/\d{4}-\d{2}-\d{2}|\d{1,2}\/\d{1,2}\/\d{4}/);
    expect(text).toContain("VTI 40.0%");
  });
  it("lists only the positions over 10% of the total, a symbol as it is", () => {
    data = { ...baseData, holdings: [holding({ symbol: "VTI", value: 95_000 }), holding({ symbol: "BND", value: 5_000 })] };
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(callout()).toBe("Positions >10% of investable assetsVTI 95.0%");
  });
  it("with no position above 10% it says so in words instead of an empty line", () => {
    // twenty equal positions: each is 5% of the investable base
    data = { ...baseData, holdings: Array.from({ length: 20 }, (_, i) => holding({ symbol: `SYM${i}`, value: 5_000 })) };
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(screen.getByTestId("card-concentration").textContent)
      .toBe("Positions >10% of investable assetsNo single position is above 10% of investable assets.");
  });
});

describe("Investments: the passive-income KPI", () => {
  const tx = txFactory({ category: "Income", account: "sample_brokerage", vendor: null });
  // cash flow from June 2025 to June 2026, the month in progress (the ingest ran on 2026-06-10), all in coverage: the
  // window is the twelve full months June 2025 .. May 2026
  const cfm = cfmFactory();
  const months = Array.from({ length: 13 }, (_, i) => `${i < 7 ? 2025 : 2026}-${String(((i + 5) % 12) + 1).padStart(2, "0")}`);
  const withIncome = () => ({ ...baseData, holdings: [holding({ value: 50_000 })], cashflow: months.map((month) => cfm({ month })),
    transactions: [
      tx({ date: "2026-03-10", amount: 1_200, type: "dividend", subcategory: "Dividends" }),
      tx({ date: "2026-04-01", amount: 34, type: "interest", subcategory: "Interest" }),
    ] });
  const tile = () => within(screen.getByTestId("kpis-investments")).getByText("Passive income (12 mo)").parentElement!;
  afterEach(() => setPrivate(false));

  it("sits in the KPI row and totals the window's dividends and interest", () => {
    data = withIncome();
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(tile().textContent).toContain("$1,234");
    expect(tile().textContent).toContain("dividends $1,200 · interest $34");
  });
  it("prints its figures through the masked formatter: in privacy mode no amount is on the tile", () => {
    data = withIncome();
    setPrivate(true);
    render(<MemoryRouter><Investments /></MemoryRouter>);
    const text = tile().textContent ?? "";
    expect(text).toContain(MASK);
    expect(text, "no amount while private").not.toMatch(/\$\d|1,2|\b34\b/);
  });
});

// No figure the data cannot support: a household with no investment account, no margin account or no full month sees
// "—" and what is missing, never a total over nothing, a red $0 or a twelve-month figure over a window before its data.
describe("Investments on a thin household", () => {
  const tiles = () => [...screen.getByTestId("kpis-investments").children].map((t) => t.textContent ?? "");
  const brokerage = acctFactory()({ id: "brk", name: "Broker", kind: "brokerage", asset_class: "Stocks/ETF" });

  it("no investment account: the total is \"—\", the concentration says what to add, no sentence about positions", () => {
    data = oneFullMonthData();
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(tiles()[0]).toBe("Total invested assets—no investment account");
    const concentration = screen.getByTestId("card-concentration").textContent ?? "";
    expect(concentration).toContain("No investment account yet. Add a brokerage account connected to the bank feed");
    expect(concentration, "no claim about positions there are none of").not.toMatch(/No single position/);
    expect(within(screen.getByTestId("card-holdings")).getByTestId("empty-state").textContent).toBe("No investment account yet.");
  });
  it("an investment account with no holdings: the total and the concentration say there are none yet", () => {
    data = { ...oneFullMonthData(), accounts: [...oneFullMonthData().accounts, brokerage] };
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(tiles()[0]).toBe("Total invested assets—no holdings yet");
    expect(screen.getByTestId("card-concentration").textContent)
      .toBe("Positions >10% of investable assetsNo holdings yet, so no position to weigh.");
  });
  it("one account is counted in the singular", () => {
    data = { ...baseData, holdings: [holding({ value: 50_000 })] };
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(tiles()[0]).toBe("Total invested assets$50,000across 1 account (excl. house & bank cash)");
  });
  it("holdings that add up to 0: no share of them is printed", () => {
    data = { ...baseData, holdings: [holding({ symbol: "VTI", value: 0 }), holding({ symbol: "BND", value: 0 })] };
    render(<MemoryRouter><Investments /></MemoryRouter>);
    const text = screen.getByTestId("card-concentration").textContent ?? "";
    expect(text).toBe("Positions >10% of investable assetsThe holdings add up to zero, so no position has a share.");
  });
  it("no margin account: the margin tile is \"—\" and not red; a margin account with nothing owed is a neutral $0", () => {
    data = oneFullMonthData();
    render(<MemoryRouter><Investments /></MemoryRouter>);
    const margin = () => screen.getByText("Margin debt", { selector: "div" }).parentElement!;
    expect(margin().textContent).toBe("Margin debt—no margin account");
    expect(margin().querySelector(".text-red-400")).toBeNull();
    cleanup();
    data = { ...oneFullMonthData(), assumptions: { ...oneFullMonthData().assumptions,
      household: { ...oneFullMonthData().assumptions.household, margin_account: "Broker" } } };
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(margin().textContent).toMatch(/^Margin debt\$0/);
    expect(margin().querySelector(".text-red-400"), "red needs a debt").toBeNull();
  });
  it("no full month: the passive income is unknown, never a twelve-month $0 over a window before the data", () => {
    data = firstWeekData();
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(tiles()[1]).toBe("Passive income—Not enough history yet");
  });
  it("one full month: the passive income is labelled with its one month", () => {
    const tx = txFactory({ category: "Income", account: "chk", vendor: null });
    const thin = oneFullMonthData();
    data = { ...thin, transactions: [...thin.transactions, tx({ date: "2026-05-28", amount: 4, type: "interest", subcategory: "Interest" })] };
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(tiles()[1], "no investment account: its parts are real figures, and no sentence about a feed")
      .toBe("Passive income (1 mo)$4dividends $0 · interest $4");
  });
  it("one balance per investment account: the chart draws the point and says the line comes with the second", () => {
    data = { ...baseData, holdings: [holding({ value: 50_000 })], balances: {
      series: { brk: [{ date: "2026-06-01", balance: 50_000, available: null, limit: null, utilization: null, source: "simplefin" }] },
      accounts: { brk: { name: "Broker", institution: "Broker", owner: "Joint", kind: "brokerage", asset_class: "Stocks/ETF" } } } };
    render(<MemoryRouter><Investments /></MemoryRouter>);
    const card = screen.getByTestId("card-investment-balances");
    expect(within(card).getByTestId("chart")).toBeDefined();
    expect(within(card).getByTestId("not-enough-history").textContent)
      .toBe("Not enough history yet — one balance so far for each account; the line draws once a second one is recorded.");
  });
  it("asset-class targets against a net worth of 0, or below it: one line, no share and no flipped sign", () => {
    for (const [total, by_class] of [[0, { Cash: 1_000, "Other Debt": -1_000 }], [-5_000, { Cash: 1_000, "Other Debt": -6_000 }]] as const) {
      cleanup();
      data = { ...baseData, assumptions: { ...baseData.assumptions, targets: { Cash: 50, "Stocks/ETF": 50 } },
        assets: { snapshots: [snapFactory({ total, by_class })({})] } };
      render(<MemoryRouter><Investments /></MemoryRouter>);
      const card = screen.getByTestId("card-class-drift");
      expect(within(card).getByTestId("empty-state").textContent, `net worth ${total}`)
        .toBe("With a net worth at or below zero, a share of it means nothing: there is no drift to measure against the targets.");
      expect(card.querySelector("table"), "no drift table").toBeNull();
      expect(card.textContent).not.toMatch(/NaN|Infinity|∞|\d%/);
      expect(within(card).getByRole("button", { name: "Edit asset-class targets" })).toBeDefined();
      fireEvent.click(within(card).getByRole("button", { name: "Edit asset-class targets" }));
      expect(screen.getByTestId("card-class-drift").textContent, "the editor shows no share either").not.toMatch(/now|NaN|Infinity/);
    }
  });
});

describe("Investments: the household's own asset-class targets drive the one card that advises", () => {
  const withTargets = (targets: Record<string, number>) => ({ ...baseData, assumptions: { ...baseData.assumptions, targets },
    assets: { ...baseData.assets, snapshots: [snapFactory({ total: 100_000, by_class: { "Stocks/ETF": 60_000, Retirement: 40_000 } })({})] } });

  it("no asset-class target stated: the card says so and where to set them, and drifts against nothing", () => {
    render(<MemoryRouter><Investments /></MemoryRouter>);
    const card = screen.getByTestId("card-class-drift");
    expect(card.querySelector("h2")?.textContent).toBe("Asset-class targets (% of net worth)");
    expect(within(card).getByTestId("empty-state").textContent).toBe("No asset-class targets are stated, so there is nothing "
      + "to measure against: set them with “Set asset-class targets” (or targets: in assumptions.yaml).");
    expect(card.querySelector("table"), "no drift table").toBeNull();
    expect(card.textContent).not.toMatch(/\d%|pts|sell|buy/);
    fireEvent.click(within(card).getByRole("button", { name: "Set asset-class targets" }));
    expect(screen.getByTestId("card-class-drift").querySelector("h2")?.textContent).toBe("Edit asset-class targets (% of net worth)");
  });

  it("ONE asset-class target is enough for a table", () => {
    data = withTargets({ Retirement: 40 });
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(screen.getByTestId("card-class-drift").querySelectorAll("tbody tr")).toHaveLength(1);
  });

  it("targets from the configuration: their table, under its own heading, after the target mix, saying where they come from", () => {
    data = withTargets({ "Stocks/ETF": 50, Retirement: 50 });
    render(<MemoryRouter><Investments /></MemoryRouter>);
    const legacy = screen.getByTestId("card-class-drift");
    expect(legacy.querySelector("h2")?.textContent).toBe("Asset-class targets (% of net worth)");
    expect(within(legacy).getByTestId("class-drift-source").textContent)
      .toBe("The household's own targets, from targets: in assumptions.yaml. They add up to 100% of net worth.");
    expect(legacy.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(within(legacy).getByText("+10.0 pts")).toBeTruthy();
    expect(legacy.textContent).toContain("everything owned less every debt, not the share of assets Home's allocation shows");
    expect(screen.getByTestId("card-target-mix").compareDocumentPosition(legacy) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(within(legacy).getByRole("button", { name: "Edit asset-class targets" }));
    const editor = screen.getByTestId("card-class-drift");
    expect(editor.querySelector("h2")?.textContent).toBe("Edit asset-class targets (% of net worth)");
    expect(editor.textContent, "the editor says saving replaces the configuration's").toContain("The targets in use come from "
      + "assumptions.yaml; the first one saved here replaces them all.");
  });

  it("targets saved in the app say so, and what they add up to, with no push toward 100", () => {
    saved.targets = { "Stocks/ETF": 30, Retirement: 50 };
    data = withTargets({ "Stocks/ETF": 30, Retirement: 50 });          // the merged assumptions carry the saved ones
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(screen.getByTestId("class-drift-source").textContent).toBe("The household's own targets, saved in the app for the "
      + "whole family. They add up to 80% of net worth, leaving 20% for what has no target.");
    expect(screen.getByTestId("card-class-drift").textContent).not.toMatch(/not 100|aim for/);
  });

  // on a net-worth base the classes held add up to more than 100% and the debts sit below zero: what the targets leave is
  // for the classes with no target less the debts, and the line says what those are today, never that the debts "make
  // up" a remainder they are subtracted from
  it("what the targets leave is for what has no target: the classes with no target and the debts, as they are today", () => {
    data = { ...baseData, assumptions: { ...baseData.assumptions, targets: { Cash: 10, "Stocks/ETF": 30, Retirement: 40 } },
      assets: { snapshots: [snapFactory({ total: 100_000, by_class: { Cash: 15_000, "Stocks/ETF": 30_000, Retirement: 32_000,
        House: 60_000, Car: 5_000, Mortgage: -40_000, "Other Debt": -2_000 } })({})] } };
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(screen.getByTestId("class-drift-source").textContent).toBe("The household's own targets, from targets: in "
      + "assumptions.yaml. They add up to 80% of net worth, leaving 20% for what has no target: today the classes with no "
      + "target (House, Car) are 65.0% of it and the debts −42.0%.");
  });
  it("a first-week brokerage household: the debts are named at their real share, never as the remainder", () => {
    // targets on stocks alone, 80%: cash is untargeted and the margin loan and a card are 6.3% of net worth
    data = { ...baseData, assumptions: { ...baseData.assumptions, targets: { "Stocks/ETF": 80 } },
      assets: { snapshots: [snapFactory({ total: 223_366, by_class: { Cash: 20_267, "Stocks/ETF": 217_111, Margin: -12_500,
        "Other Debt": -1_512 } })({})] } };
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(screen.getByTestId("class-drift-source").textContent).toBe("The household's own targets, from targets: in "
      + "assumptions.yaml. They add up to 80% of net worth, leaving 20% for what has no target: today the classes with no "
      + "target (Cash) are 9.1% of it and the debts −6.3%.");
  });

  it("the editor says what the saved targets add up to and that, as shares of net worth, they need not add up to 100", () => {
    saved.targets = { "Stocks/ETF": 30, Retirement: 50 };
    data = withTargets({ "Stocks/ETF": 30, Retirement: 50 });
    render(<MemoryRouter><Investments /></MemoryRouter>);
    fireEvent.click(within(screen.getByTestId("card-class-drift")).getByRole("button", { name: "Edit asset-class targets" }));
    const sum = screen.getByTestId("class-targets-sum");
    expect(sum.textContent).toBe("These add up to 80% of net worth. Targets are shares of net worth, so they need not add up "
      + "to 100: what they leave is for the classes with no target, less the debts.");
    expect(sum.className, "no warning colour").not.toMatch(/amber|red/);
  });
});

// Dividends and margin interest come only from a brokerage's own rows: an investment account with no
// transaction at all reports balances only, so what its rows would show is unknown ("—"), never $0
describe("Investments: brokerage income with no transaction feed", () => {
  const months = Array.from({ length: 13 }, (_, i) => `${i < 7 ? 2025 : 2026}-${String(((i + 5) % 12) + 1).padStart(2, "0")}`);
  const tx = txFactory({ category: "Income", vendor: null });
  const brokerage = acctFactory()({ id: "brk", name: "Broker", kind: "brokerage", asset_class: "Stocks/ETF", txn_data_start: null,
    txn_data_end: null });
  const household = (over: Partial<typeof baseData> = {}) => ({ ...baseData, holdings: [holding({ value: 50_000 })],
    cashflow: months.map((month) => cfmFactory()({ month })), accounts: [acctFactory()({}), brokerage],
    transactions: [tx({ date: "2026-03-10", amount: 34, type: "interest", subcategory: "Interest", account: "chk" })], ...over });
  const tile = (label: RegExp) => within(screen.getByTestId("kpis-investments")).getByText(label).parentElement!.textContent ?? "";
  const withMargin = (d: typeof baseData) => ({ ...d,
    assumptions: { ...d.assumptions, household: { ...d.assumptions.household, margin_account: "Broker" } },
    assets: { snapshots: [snapFactory({ total: 100_000, by_class: { "Stocks/ETF": 110_000, Margin: -10_000 } })({})] } });

  it("a brokerage with no transaction at all: the interest is the bank's, dividends read — with why just after, and what that leaves the figure", () => {
    data = household();
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(tile(/^Passive income/)).toBe("Passive income (12 mo)$34bank interest $34 · dividends — (no transaction "
      + "feed for Broker, so the figure leaves them out)");
  });
  it("a live margin account: the invested total and its holdings row are its positions before the loan, the chart after it", () => {
    const row = (over: Record<string, unknown>) => ({ account: "Broker", account_id: "brk", institution: "Sample", owner: "Joint",
      asset_class: "Stocks/ETF", value: 110_000, liquid: true, as_of: "2026-06-01", as_of_assumed: false, needs_review: false,
      source: "positions", manual_id: null, carried_back: false, ...over });
    const margined = { ...household({ holdings: [holding({ value: 110_000, account: "Broker", account_id: "brk" })] }),
      assets: { snapshots: [snapFactory({ total: 100_000, by_class: { "Stocks/ETF": 110_000, Margin: -10_000 },
        rows: [row({}), row({ account: "Sample Margin", asset_class: "Margin", value: -10_000, liquid: false, source: "simplefin_balance" })] })({})] },
      balances: { series: { brk: [{ date: "2026-06-01", balance: 100_000, available: null, limit: null, utilization: null, source: "simplefin" }] },
        accounts: { brk: { name: "Broker", institution: "Sample", owner: "Joint", kind: "brokerage", asset_class: "Stocks/ETF" } } } } as typeof baseData;
    data = margined;
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(tile(/^Total invested assets$/)).toMatch(/· positions before the margin loan$/);
    expect(screen.getByTestId("holdings-gross").textContent).toBe("before the margin loan");
    expect(screen.getByTestId("card-investment-balances").textContent).toContain("point-in-time account value, after the margin loan");
    cleanup();
    data = household();                      // no margin account: no clause anywhere
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(screen.getByTestId("kpis-investments").textContent).not.toMatch(/margin loan/);
    expect(screen.queryByTestId("holdings-gross")).toBeNull();
  });
  it("some investment account with rows and one without: real figures, and the one without is named as not counted", () => {
    const other = acctFactory()({ id: "ira", name: "Sample IRA", kind: "brokerage", asset_class: "Retirement", txn_data_start: null,
      txn_data_end: null });
    data = household({ accounts: [acctFactory()({}), { ...brokerage, txn_data_start: "2025-06-01", txn_data_end: "2026-06-01" }, other] });
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(tile(/^Passive income/)).toBe("Passive income (12 mo)$34dividends $0 · interest $34 · Sample IRA: no "
      + "transaction feed, not counted");
  });
  it("a margin owed that no account can be told to be: its interest reads — with the reason, never $0", () => {
    data = { ...withMargin(household()), assumptions: { ...withMargin(household()).assumptions,
      household: { ...withMargin(household()).assumptions.household, margin_account: null } } };
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(tile(/^Margin debt$/)).toBe("Margin debt$10,000margin interest — · no account is marked as the margin account");
  });
  it("a brokerage with transactions and no dividend: a real $0", () => {
    data = household({ accounts: [acctFactory()({}), { ...brokerage, txn_data_start: "2025-06-01", txn_data_end: "2026-06-01" }] });
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(tile(/^Passive income/)).toBe("Passive income (12 mo)$34dividends $0 · interest $34");
  });
  it("the margin account with no transaction at all: its interest reads — with the reason; with transactions, a real $0", () => {
    data = withMargin(household());
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(tile(/^Margin debt$/)).toBe("Margin debt$10,000margin interest — · no transaction feed for this account");
    cleanup();
    // a household with a bank feed (one connected days ago): the account has had no transaction yet, no feed is denied
    const fed = withMargin(household());
    data = { ...fed, manifest: { ...fed.manifest, setup: { ...fed.manifest.setup, feed: true } } };
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(tile(/^Margin debt$/)).toBe("Margin debt$10,000margin interest — · no transaction from this account yet");
    cleanup();
    data = withMargin(household({ accounts: [acctFactory()({}), { ...brokerage, txn_data_start: "2025-06-01", txn_data_end: "2026-06-01" }] }));
    render(<MemoryRouter><Investments /></MemoryRouter>);
    expect(tile(/^Margin debt$/)).toBe("Margin debt$10,000$0 margin interest paid to date");
  });
});

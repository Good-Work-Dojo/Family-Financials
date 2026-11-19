// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Household, HouseholdPerson, RecurringItem, Transaction } from "../data/types";
import { cfmFactory, recFactory, txFactory } from "../lib/__fixtures__";
import { monthsBetween } from "../lib/agg";
import { SHIPPED_RESERVED } from "../lib/categories";
import { _resetEfundOverridesMemory, EFUND_OVERRIDES_KEY } from "../lib/efundOverrides";
import { forecastCashflow } from "../lib/forecast";
import { fmtUSD0 } from "../lib/format";
import { fireCrossing, fireNumber, projectGrowth } from "../lib/projections";

// The emergency-fund card reads the transactions and the data range; the gauge is stubbed (no canvas).
const tx = txFactory({ type: "purchase" });
// the ingest ran in June 2026, which holds the newest row: June is the month in progress, and the emergency fund's six
// full months are December to May
const IN_PROGRESS = "2026-06";
const txns = [
  tx({ date: "2026-02-02", category: "Utilities", subcategory: "Water/Trash", amount: -600 }),
  tx({ date: "2026-02-03", category: "Subscriptions", subcategory: "News/Media", amount: -120 }),
  tx({ date: "2026-02-04", category: "Subscriptions", subcategory: "Streaming", amount: -60 }),
  tx({ date: "2026-06-20", category: "Groceries", subcategory: "Groceries", amount: -5 }),   // the month in progress: in no window
];
// twelve full months of cash flow, June 2025 to May 2026: enough history for the forecast (it needs six) and for the
// emergency fund's six months, December to May - the forecast card and the gauge read it
const cfm = cfmFactory();
const TWELVE_MONTHS = monthsBetween("2025-06", "2026-05").map((month) => cfm({ month, income: 9000, spend: 8000 }));
let cashflow = TWELVE_MONTHS;                       // a test shortens it
const renames = vi.hoisted(() => ({ list: [] as { from: { category: string; subcategory?: string | null }; to: { category: string; subcategory?: string | null } }[] }));
const splits = vi.hoisted(() => ({ parents: new Map<string, unknown>(), children: new Map<string, unknown[]>() }));
let purchases: Transaction[] = [];                   // the vendor view; the forecast reads it
let recurring: RecurringItem[] = [];
// a test empties the household's rows (no transaction at all)
const rows = vi.hoisted(() => ({ none: false }));
// the projection keys the configuration states (every one unless a test says otherwise) and those saved in the app
const ALL_STATED = ["inflation_pct", "ltcg_pct", "return_scenarios_pct", "state_income_tax_pct", "withdrawal_rate_pct"];
// and the return scenarios the export carries (an export older than the validator's check can carry an empty list)
const projections = vi.hoisted(() => ({ stated: [] as string[], saved: {} as Record<string, number>, scenarios: [5, 7, 9] }));
// the date of the balance the forecast starts from: its first month is the rest of June after it
const AS_OF = "2026-06-20";
vi.mock("../data/DataContext", () => ({
  // a copy each time: the tests add and remove rows, and the app reads a row list once (lib/agg.ts caches its first day)
  useData: () => ({ data: { transactions: rows.none ? [] : [...txns], purchases, cashflow, recurring, splitParents: splits.parents,
                            manifest: { generated_at: "2026-06-21T09:00:00-05:00" },
                            assumptions: { category_renames: renames.list, reserved: SHIPPED_RESERVED,
                                           projections: { return_scenarios_pct: projections.scenarios, withdrawal_rate_pct: 4 },
                                           projections_stated: projections.stated } },
                    indexes: { dataRange: { first: "2024-06-01", last: AS_OF }, dataThrough: AS_OF, childrenByParent: splits.children },
                    prefs: { doc: { projections: projections.saved } } }),
}));
// the chart is stubbed (no canvas) and records the option it was handed
const chart = vi.hoisted(() => ({ option: null as any }));
vi.mock("../components/EChart", () => ({
  default: (p: { option: unknown }) => { chart.option = p.option; return <div data-testid="chart" />; },
}));
import { EmergencyFund, ForecastCard, RetirementCard } from "./Tools";

beforeEach(() => {
  localStorage.clear(); _resetEfundOverridesMemory(); projections.stated = ALL_STATED; projections.saved = {};
  projections.scenarios = [5, 7, 9];
});

/** the crossing's last sentence, escaped for a pattern: its growth is nominal against a FIRE number in today's dollars */
const NOMINAL = "\\. The growth is not adjusted for inflation while the FIRE number is in today's dollars, so the real "
  + "crossing is later\\.";
/** the retirement chart's line ends saying a typed contribution is kept for the visit only */
const VISIT = " An amount typed in a contribution box lasts for this visit.";
afterEach(() => { cleanup(); localStorage.clear(); _resetEfundOverridesMemory(); purchases = []; recurring = []; renames.list = []; splits.parents.clear(); splits.children.clear(); });

const box = (name: string) => within(screen.getByTitle(name)).getByRole("checkbox") as HTMLInputElement;
const stored = () => JSON.parse(localStorage.getItem(EFUND_OVERRIDES_KEY) ?? "{}");

describe("Planning's emergency-fund list: slash subcategories", () => {
  it("lists each by its whole name and classifies by the full pair (Utilities / Water/Trash is essential, News/Media is not)", () => {
    render(<EmergencyFund liquidCash={1000} />);
    expect(box("Utilities / Water/Trash").checked).toBe(true);
    expect(box("Subscriptions / News/Media").checked).toBe(false);
    expect(box("Subscriptions / Streaming").checked).toBe(false);
  });

  it("a toggle writes the full key", () => {
    render(<EmergencyFund liquidCash={1000} />);
    fireEvent.click(box("Subscriptions / News/Media"));
    expect(stored()).toEqual({ "Subscriptions / News/Media": true });
    expect(box("Subscriptions / News/Media").checked).toBe(true);
    fireEvent.click(box("Utilities / Water/Trash"));
    expect(stored()).toEqual({ "Subscriptions / News/Media": true, "Utilities / Water/Trash": false });
  });

  it("a toggle saved under the OLD truncated key still shows and counts, and the next click writes the full key", () => {
    localStorage.setItem(EFUND_OVERRIDES_KEY, JSON.stringify({ "Subscriptions / News": true, "Utilities / Water": false }));
    render(<EmergencyFund liquidCash={1000} />);
    expect(box("Subscriptions / News/Media").checked, "the legacy 'on' still applies").toBe(true);
    expect(box("Utilities / Water/Trash").checked, "the legacy 'off' still applies").toBe(false);
    expect(screen.getByText(/1 counted as essential/)).toBeDefined();
    fireEvent.click(box("Subscriptions / News/Media"));                                // turn it off
    expect(stored()).toEqual({ "Subscriptions / News": true, "Utilities / Water": false, "Subscriptions / News/Media": false });
    expect(box("Subscriptions / News/Media").checked, "the full key wins over the legacy one").toBe(false);
    expect(screen.getByText(/0 counted as essential/)).toBeDefined();
  });
});

// A configured rename moves the category a row carries; the gauge, its checklist and its months must keep the
// renamed essential category on the essential side.
describe("Planning's emergency-fund list follows a category rename", () => {
  const withFuel = (category: string, run: () => void) => {
    const row = tx({ date: "2026-02-05", category, subcategory: "Fuel", amount: -600 });
    txns.push(row);
    try { run(); } finally { txns.splice(txns.indexOf(row), 1); }
  };
  it("Transport renamed to Transportation: its row is checked and its dollars are in the essentials", () => {
    renames.list = [{ from: { category: "Transport" }, to: { category: "Transportation" } }];
    withFuel("Transportation", () => {
      render(<EmergencyFund liquidCash={1000} />);
      expect(box("Transportation / Fuel").checked).toBe(true);
      expect(screen.getByTestId("efund-essentials").querySelector("summary")!.textContent).toBe("Essentials: 2 of 4 counted — edit");
    });
  });
  it("with no rename known, the same row is just an unlisted category (control)", () => {
    withFuel("Transportation", () => {
      render(<EmergencyFund liquidCash={1000} />);
      expect(box("Transportation / Fuel").checked).toBe(false);
    });
  });
});

// 53 rows were always open: two phone screens, and a tall empty column beside the short projection card at 1280.
describe("the essentials checklist is one disclosure", () => {
  const card = () => screen.getByTestId("card-emergency-fund");
  const list = () => screen.getByTestId("efund-essentials") as HTMLDetailsElement;
  it("is closed on arrival, its line says how many of the rows count, and the figure above it stays in view", () => {
    render(<EmergencyFund liquidCash={1000} />);
    expect(list().open).toBe(false);
    expect(list().querySelector("summary")!.textContent).toBe("Essentials: 1 of 3 counted — edit");
    expect(within(card()).getByText(/essentials =/), "the sentence is outside the disclosure").toBeDefined();
    expect(list().contains(card().querySelector("p.text-center")), "and not inside it").toBe(false);
  });
  it("keeps its function: the list is one click away, and a tick changes the stored toggle and the count in the line", async () => {
    render(<EmergencyFund liquidCash={1000} />);
    fireEvent.click(list().querySelector("summary")!);
    await waitFor(() => expect(list().querySelector("summary")!.textContent).toContain("— hide"));
    expect(list().open).toBe(true);
    fireEvent.click(box("Subscriptions / News/Media"));
    expect(stored()).toEqual({ "Subscriptions / News/Media": true });
    expect(list().querySelector("summary")!.textContent).toContain("Essentials: 2 of 3 counted");
    expect(list().open, "ticking a row does not fold the list under the finger").toBe(true);
  });
  it("its toggles are the device's saved ones, not the disclosure's state: closing and reopening loses nothing", () => {
    localStorage.setItem(EFUND_OVERRIDES_KEY, JSON.stringify({ "Subscriptions / Streaming": true }));
    const first = render(<EmergencyFund liquidCash={1000} />);
    expect(list().open).toBe(false);
    expect(box("Subscriptions / Streaming").checked, "a ticked row is ticked while the list is closed").toBe(true);
    expect(list().querySelector("summary")!.textContent).toContain("2 of 3 counted");
    first.unmount();
    render(<EmergencyFund liquidCash={1000} />);
    expect(box("Subscriptions / Streaming").checked).toBe(true);
  });
  it("opens by itself when nothing is counted - the list is then the way out of the amber hint", () => {
    localStorage.setItem(EFUND_OVERRIDES_KEY, JSON.stringify({ "Utilities / Water/Trash": false }));
    render(<EmergencyFund liquidCash={1000} />);
    expect(card().textContent).toContain("no row is marked essential");
    expect(list().open).toBe(true);
    expect(list().querySelector("summary")!.textContent).toContain("Essentials: 0 of 3 counted");
  });
  it("an uncounted row is dim, not faint: zinc-400 text, never the opacity that read at 2:1", () => {
    render(<EmergencyFund liquidCash={1000} />);
    const off = screen.getByTitle("Subscriptions / News/Media");
    expect(off.className).toContain("text-zinc-400");
    expect(off.className).not.toMatch(/opacity-/);
    expect(screen.getByTitle("Utilities / Water/Trash").className).not.toContain("text-zinc-400");
  });
});

describe("the forecast's essentials floor", () => {
  const floorLine = () => chart.option.series.find((x: { name?: string }) => x.name === "Projected liquid cash").markLine;
  it("draws the dashed floor line at a positive floor (the control: the test below can fail)", () => {
    render(<ForecastCard liquidCash={20_000} floor={9_000} asOf={AS_OF} />);
    expect(floorLine()?.data?.[0]?.yAxis).toBe(9_000);
    expect(screen.getByTestId("card-forecast").textContent).not.toMatch(/Cash floor check—/);
  });
  it("draws NO floor line at floor 0 (every essential row toggled off): the check reads —, and so does the chart", () => {
    render(<ForecastCard liquidCash={20_000} floor={0} asOf={AS_OF} />);
    expect(floorLine(), "no markLine over an undefined floor").toBeUndefined();
    expect(screen.getByTestId("card-forecast").textContent).toMatch(/Cash floor check—/);
  });
});

// Spending well over income (the no-income demo household's shape): the line falls below zero within months. The band is
// its low edge with its width stacked on it; both stack on each other whatever their sign, so the band's top is the low
// edge plus the width (the high) below zero as above it, and the band follows the line down.
describe("the forecast's band follows the line below zero", () => {
  afterEach(() => { cashflow = TWELVE_MONTHS; });
  it("the band's two series stack whatever their sign, and its edges sit either side of the line every month", () => {
    cashflow = monthsBetween("2025-06", "2026-05").map((month, i) => cfm({ month, income: 185, spend: i % 2 ? 8_800 : 7_000 }));
    render(<ForecastCard liquidCash={20_000} floor={9_000} asOf={AS_OF} />);
    const series = (name: string) => chart.option.series.find((x: { name?: string }) => x.name === name);
    const [low, range, line] = [series("low"), series("range"), series("Projected liquid cash")];
    expect([low.stack, range.stack]).toEqual(["band", "band"]);
    expect([low.stackStrategy, range.stackStrategy], "a width stacks on a low edge below zero too").toEqual(["all", "all"]);
    expect(Math.min(...line.data), "the line falls below zero").toBeLessThan(0);
    expect(low.data.some((v: number) => v < 0)).toBe(true);
    line.data.forEach((v: number, i: number) => {
      expect(low.data[i]).toBeLessThanOrEqual(v);
      expect(low.data[i] + range.data[i]).toBeGreaterThanOrEqual(v);
    });
  });
});

describe("the forecast's spend baseline says over how many months it is averaged", () => {
  afterEach(() => { cashflow = TWELVE_MONTHS; });
  const baseline = () => within(screen.getByTestId("card-forecast")).getByText("Baseline spend").parentElement!.textContent ?? "";
  /** the household's rows begin on the 1st of the first cash-flow month, as an ingest's do (the forecast reads the
   *  full months from the data's first day) */
  const fromFirstMonth = (run: () => void) => {
    const row = tx({ date: `${cashflow[0].month}-01`, category: "Dining", subcategory: "Coffee", amount: -1 });
    txns.push(row);
    try { run(); } finally { txns.splice(txns.indexOf(row), 1); }
  };
  it("twelve full months: the trailing twelve", () => {
    fromFirstMonth(() => {
      render(<ForecastCard liquidCash={20_000} floor={9_000} asOf={AS_OF} />);
      expect(baseline()).toMatch(/trailing 12 mo, seasonality applied per month/);
    });
  });
  it("eight full months: those eight, never a trailing twelve", () => {
    cashflow = TWELVE_MONTHS.slice(4);
    fromFirstMonth(() => {
      render(<ForecastCard liquidCash={20_000} floor={9_000} asOf={AS_OF} />);
      expect(baseline()).toMatch(/trailing 8 full months, seasonality applied per month/);
      expect(baseline()).not.toMatch(/12/);
    });
  });
});

describe("the forecast card reads the vendor view", () => {
  it("a scheduled charge in data.purchases leaves the baseline; the same charge only in data.transactions does not", () => {
    const gym = txFactory({ type: "purchase", vendor: "Netflix", account: "demo_checking_0101" });
    const charges = cashflow.map((m) => gym({ date: `${m.month}-05`, amount: -500 }));
    recurring = [recFactory()({})];                                            // a promoted schedule for that vendor and account
    purchases = charges;
    const firstDay = "2025-06-01";                                             // the card reads the rows' first day
    const withCharges = forecastCashflow({ cashflow, inProgress: IN_PROGRESS, firstDay, startCash: 20_000, asOf: AS_OF, schedules: recurring, transactions: charges })!;
    const without = forecastCashflow({ cashflow, inProgress: IN_PROGRESS, firstDay, startCash: 20_000, asOf: AS_OF, schedules: recurring, transactions: txns })!;
    expect(withCharges.months[0].spend, "the control: the view the forecast reads matters for these rows").not.toBe(without.months[0].spend);
    render(<ForecastCard liquidCash={20_000} floor={0} asOf={AS_OF} />);
    const tip: string = chart.option.tooltip.formatter([{ dataIndex: 0 }]);
    expect(tip).toContain(`spend ~${fmtUSD0(withCharges.months[0].spend)}`);
    expect(tip).not.toContain(`spend ~${fmtUSD0(without.months[0].spend)}`);
  });
});

// The essentials basis counts every part of a split purchase (a Groceries share is essential), so the card says a rule is in play.
describe("the emergency-fund card names the split rule behind a category it shows", () => {
  const withSplit = () => {
    const parent = tx({ id: "sp", date: "2026-02-05", category: "Shopping", subcategory: "Amazon", amount: -1000, is_parent: true, vendor: "Synth Mart" });
    const parts = [
      tx({ id: "spa", date: "2026-02-05", category: "Shopping", subcategory: "Amazon", amount: -800, parent_id: "sp", category_source: "split", vendor: "Synth Mart" }),
      tx({ id: "spb", date: "2026-02-05", category: "Groceries", subcategory: "Food", amount: -200, parent_id: "sp", category_source: "split", vendor: "Synth Mart" }),
    ];
    splits.parents.set("sp", parent); splits.children.set("sp", parts);
    txns.push(...parts);
  };
  afterEach(() => { txns.length = 3; });                   // back to the three module rows
  it("says which share of each order a listed category holds", () => {
    withSplit();
    render(<EmergencyFund liquidCash={1000} />);
    expect(screen.getByTestId("split-note").textContent).toMatch(/^Split rule: Shopping and Groceries include 80% and 20% of each Synth Mart order\./);
  });
  it("prints nothing without a split rule", () => {
    render(<EmergencyFund liquidCash={1000} />);
    expect(screen.queryByTestId("split-note")).toBeNull();
  });
});

// With no FIRE number the two tiles say why, truthfully: no transaction at all, or transactions and too few full months
// (the core spend is the twelve full months before the month in progress, or as many as there are, at least three)
describe("the FIRE tiles with no core spend", () => {
  const mountCard = () => render(
    <RetirementCard hh={{ name: "The Sample household", timezone: "America/New_York", people: [{ name: "Alex", role: "owner", birth_year: 1980 }],
      margin_apr: 0.1, retirement_accounts: { "401k A": "Alex" }, margin_account: null }} year={2030} retirementBy={{ Alex: 1000 }}
      reported={["Alex"]} reportedAccounts={["401k A"]} retirementAsOf={{}} balanceOnly={[]} coreSpend={null} />);
  const sub = (label: string) => within(screen.getByTestId("card-retirement")).getByText(label).parentElement!.textContent ?? "";
  afterEach(() => { rows.none = false; cashflow = TWELVE_MONTHS; });
  it("transactions and too few full months: — and what it needs and how many there are, never 'no transactions'", () => {
    cashflow = [...TWELVE_MONTHS.slice(-2), cfm({ month: "2026-06" })];   // April and May full, June in progress
    mountCard();
    for (const label of ["FIRE number", "FIRE crossing @7%"]) {
      expect(sub(label), label).toBe(`${label}—Not enough history yet: needs 3 full months of transactions, 2 so far`);
    }
    expect(screen.queryByTestId("not-enough-history"), "no line of its own").toBeNull();
  });
  it("no transaction at all: it says so", () => {
    rows.none = true;
    mountCard();
    for (const label of ["FIRE number", "FIRE crossing @7%"]) {
      expect(sub(label), label).toContain("No transactions yet");
      expect(sub(label), label).not.toContain("Not enough history");
    }
    expect(screen.queryByTestId("not-enough-history")).toBeNull();
  });
  it("a year of spending from twelve full months is named as the trailing twelve; from fewer, annualised from how many", () => {
    const mount = (coreSpend: { annual: number; months: number }) => render(
      <RetirementCard hh={{ name: "The Sample household", timezone: "America/New_York", people: [{ name: "Alex", role: "owner", birth_year: 1980 }],
        margin_apr: 0.1, retirement_accounts: { "401k A": "Alex" }, margin_account: null }} year={2030} retirementBy={{ Alex: 1000 }}
        reported={["Alex"]} reportedAccounts={["401k A"]} retirementAsOf={{}} balanceOnly={[]} coreSpend={coreSpend} />);
    // the line says what the year of spending leaves out, by rule: the money movements, by their names (no "core")
    mount({ annual: 40_000, months: 12 });
    expect(sub("FIRE number")).toBe(`FIRE number${fmtUSD0(1_000_000)}trailing-12mo spending less Checks, Wire and ATM: `
      + `${fmtUSD0(40_000)} ÷ 4%`);
    cleanup();
    mount({ annual: 40_000, months: 4 });
    expect(sub("FIRE number")).toBe(`FIRE number${fmtUSD0(1_000_000)}spending less Checks, Wire and ATM: ${fmtUSD0(40_000)} a year, `
      + "from 4 full months, annualised, ÷ 4%");
    cleanup();
    // a renamed money movement is named as the household calls it
    renames.list = [{ from: { category: "Cash & Misc", subcategory: "Checks" }, to: { category: "Cash & Misc", subcategory: "Cheques" } }];
    mount({ annual: 40_000, months: 12 });
    expect(sub("FIRE number")).toContain("spending less Cheques, Wire and ATM:");
  });
});

// The retirement projection draws one line per return scenario. The config check refuses an empty list, but an export
// written before it can still carry one: the card says no scenario is stated, instead of drawing nothing or crashing.
describe("the retirement projection with no return scenario stated", () => {
  const household: Household = { name: "The Sample household", timezone: "America/New_York",
    people: [{ name: "Sam", role: "owner", birth_year: 1980 }], margin_apr: 0.1, retirement_accounts: { "401k B": "Sam" },
    margin_account: null };
  const mountCard = () => render(
    <RetirementCard hh={household} year={2030} retirementBy={{ Sam: 200_000 }} reported={["Sam"]}
      reportedAccounts={["401k B"]} retirementAsOf={{}} balanceOnly={[]} coreSpend={{ annual: 40_000, months: 12 }} />);
  const card = () => screen.getByTestId("card-retirement");
  it("an empty list: the card says no return scenario is stated and where to state one, and draws no chart", () => {
    projections.scenarios = [];
    chart.option = null;
    mountCard();
    expect(within(card()).getByTestId("retirement-no-scenarios").textContent).toBe("No return scenario is stated, so "
      + "there is no projection to draw: state at least one yearly return and it appears here (return_scenarios_pct: "
      + "under projections: in assumptions.yaml).");
    expect(within(card()).queryByTestId("chart")).toBeNull();
    expect(chart.option).toBeNull();
    expect(card().textContent).not.toMatch(/NaN|undefined/);
  });
  it("the control: one scenario stated draws its one line", () => {
    projections.scenarios = [6];
    mountCard();
    expect(within(card()).queryByTestId("retirement-no-scenarios")).toBeNull();
    expect(chart.option.series.map((s: { name: string }) => s.name)).toEqual(["6%", "FIRE number (4% WR)"]);
  });
});

// household.yaml's birth_year is optional: the retirement projection takes its ages from the adults that have one
describe("the retirement projection with adults who have no birth year", () => {
  const ALEX: HouseholdPerson = { name: "Alex", role: "owner" };                  // no birth_year key, as the export writes it
  const SAM: HouseholdPerson = { name: "Sam", role: "owner", birth_year: 1980 };  // 50 in 2030
  const household = (people: HouseholdPerson[]): Household => ({ name: "The Sample household", timezone: "America/New_York",
    people, margin_apr: 0.1, retirement_accounts: { "401k A": "Alex", "401k B": "Sam" }, margin_account: null });
  const mountCard = (people: HouseholdPerson[]) => render(
    <RetirementCard hh={household(people)} year={2030} retirementBy={{ Alex: 300_000, Sam: 200_000 }}
      reported={["Alex", "Sam"]} reportedAccounts={["401k A", "401k B"]} retirementAsOf={{}} balanceOnly={[]}
      coreSpend={{ annual: 40_000, months: 12 }} />);
  const card = () => screen.getByTestId("card-retirement");
  const kpi = (label: string) => within(card()).getByText(label).nextElementSibling?.textContent;
  // the FIRE line: both balances at 7%, growing alone (no contribution is assumed until one is set)
  const crossing = fireCrossing(projectGrowth({ principal: 500_000, monthlyContribution: 0, annualReturnPct: 7, years: 40 }),
    fireNumber(40_000, 4));

  it("the control: the crossing exists, so the FIRE tile prints an age when someone has one", () => {
    expect(crossing).not.toBeNull();
  });
  it("one adult without one keeps the contribution field and the tile; the age axis and FIRE age come from the adult who has one", () => {
    mountCard([ALEX, SAM]);
    expect(kpi("Alex retirement today")).toBe(fmtUSD0(300_000));
    expect(kpi("Sam retirement today")).toBe(fmtUSD0(200_000));
    expect(within(card()).getByText("Alex contrib")).toBeDefined();
    expect(within(card()).getByText("Sam contrib")).toBeDefined();
    expect(chart.option.xAxis.name).toBe("Sam's age");
    expect(chart.option.xAxis.data[0]).toBe("50");
    expect(chart.option.xAxis.data.at(-1)).toBe("65");                              // retires at 65: 15 years
    expect(kpi("FIRE crossing @7%")).toBe(`age ${Math.round(50 + crossing! / 12)}`);
    expect(card().textContent).not.toMatch(/NaN|undefined/);
  });
  it("picking the adult without one draws the projection on a year axis and prints no age", () => {
    mountCard([ALEX, SAM]);
    fireEvent.click(within(card()).getByRole("button", { name: "Alex" }));
    expect(chart.option.xAxis.name).toBe("Year");
    expect(chart.option.xAxis.data[0]).toBe("2030");
    expect(chart.option.xAxis.data.at(-1)).toBe("2070");                            // the 40-year horizon
    expect(chart.option.xAxis.data.join(" ")).not.toMatch(/NaN|undefined/);
    expect(card().textContent).not.toMatch(/NaN|undefined/);
  });
  it("no adult with one: the year axis by default, and the FIRE tile shows — for the age", () => {
    mountCard([ALEX, { name: "Sam", role: "owner" }]);
    expect(chart.option.xAxis.name).toBe("Year");
    expect(chart.option.xAxis.data[0]).toBe("2030");
    expect(kpi("FIRE crossing @7%")).toBe("—");
    expect(within(card()).getByText("Sam contrib")).toBeDefined();
    expect(card().textContent).not.toMatch(/NaN|undefined/);
  });
  it("a household with no adult at all (only a child) still renders: no contribution field, no projection, no crossing", () => {
    chart.option = null;
    mountCard([{ name: "Robin", role: "kid", birth_year: 2020 }]);
    expect(within(card()).queryByText(/contrib$/), "a child has no contribution field").toBeNull();
    expect(within(card()).queryByText(/retirement today$/), "nor a retirement balance tile").toBeNull();
    expect(chart.option, "no adult with a retirement account: nothing to project").toBeNull();
    expect(kpi("FIRE crossing @7%")).toBe("—");
    expect(card().textContent).toContain("No retirement account is mapped to anyone yet");
    expect(card().textContent).not.toMatch(/NaN|undefined/);
  });
  it("an adult with no retirement account mapped reads —, gets no contribution, and is left out of the projection", () => {
    const hh = { ...household([SAM, { name: "Alex", role: "owner", birth_year: 1985 }]), retirement_accounts: { "401k B": "Sam" } };
    render(<RetirementCard hh={hh} year={2030} retirementBy={{ Alex: 0, Sam: 200_000 }}
      reported={["Sam"]} reportedAccounts={["401k B"]} retirementAsOf={{}} balanceOnly={[]} coreSpend={{ annual: 40_000, months: 12 }} />);
    expect(kpi("Alex retirement today")).toBe("—");
    expect(within(card()).getByText("Alex retirement today").parentElement!.textContent).toContain("no retirement account mapped");
    expect(within(card()).queryByText("Alex contrib")).toBeNull();
    expect(within(card()).getByText("Sam contrib")).toBeDefined();
    // the projection is Sam's balance alone, growing with no contribution set: the FIRE crossing is the one it makes
    const alone = fireCrossing(projectGrowth({ principal: 200_000, monthlyContribution: 0, annualReturnPct: 7, years: 40 }),
      fireNumber(40_000, 4));
    expect(alone, "the control: Sam alone crosses within the horizon").not.toBeNull();
    expect(kpi("FIRE crossing @7%")).toBe(`age ${Math.round(50 + alone! / 12)}`);
    expect(chart.option.series[0].data[0]).toBe(200_000);
    expect(card().textContent).toContain("Alex has no retirement account mapped, so the projection leaves that balance and "
      + "contribution out until one is (retirement_accounts: under household: in assumptions.yaml).");
  });
  it("one adult projected: the card is that adult's, with no Combined toggle, and the crossing says whose accounts and who has none", () => {
    const hh = { ...household([SAM, { name: "Alex", role: "owner", birth_year: 1985 }]), retirement_accounts: { "401k B": "Sam" } };
    render(<RetirementCard hh={hh} year={2030} retirementBy={{ Alex: 0, Sam: 200_000 }}
      reported={["Sam"]} reportedAccounts={["401k B"]} retirementAsOf={{}} balanceOnly={[]} coreSpend={{ annual: 40_000, months: 12 }} />);
    expect(within(card()).getByRole("heading").textContent).toBe("Retirement projection — Sam's retirement accounts");
    expect(within(card()).queryByRole("button", { name: "Combined" }), "no toggle between two identical options").toBeNull();
    expect(within(card()).getByText("FIRE crossing @7%").parentElement!.textContent).toMatch(new RegExp(
      `Sam's retirement accounts alone \\(Alex has none mapped\\) — taxable assets would move this earlier; growth alone, no monthly contribution set${NOMINAL}$`));
  });
  it("two adults projected keep the combined title, the toggle and the plain crossing line (control)", () => {
    mountCard([ALEX, SAM]);
    expect(within(card()).getByRole("heading").textContent).toBe("Retirement projection — combined retirement accounts");
    expect(within(card()).getByRole("button", { name: "Combined" })).toBeDefined();
    expect(within(card()).getByText("FIRE crossing @7%").parentElement!.textContent)
      .toMatch(new RegExp(`retirement accounts alone — taxable assets would move this earlier; growth alone, no monthly contribution set${NOMINAL}$`));
  });
  it("an adult with two mapped accounts, one of which has reported nothing: the tile names the silent one", () => {
    const hh = { ...household([ALEX, SAM]), retirement_accounts: { "401k A": "Alex", "IRA A": "Alex", "401k B": "Sam" } };
    render(<RetirementCard hh={hh} year={2030} retirementBy={{ Alex: 300_000, Sam: 200_000 }} reported={["Alex", "Sam"]}
      reportedAccounts={["401k A", "401k B"]} retirementAsOf={{ Alex: "2030-01-31" }} balanceOnly={[]}
      coreSpend={{ annual: 40_000, months: 12 }} />);
    expect(within(card()).getByText("Alex retirement today").parentElement!.textContent)
      .toBe(`Alex retirement today${fmtUSD0(300_000)}401k A (as of Jan 31, 2030) · IRA A: no balance reported yet`);
  });
  it("an adult whose mapped account has reported nothing yet reads —, not $0, and is left out until it does", () => {
    render(<RetirementCard hh={household([ALEX, SAM])} year={2030} retirementBy={{ Alex: 0, Sam: 200_000 }}
      reported={["Sam"]} reportedAccounts={["401k B"]} retirementAsOf={{}} balanceOnly={[]} coreSpend={{ annual: 40_000, months: 12 }} />);
    expect(within(card()).getByText("Alex retirement today").parentElement!.textContent)
      .toBe("Alex retirement today—401k A · no balance reported yet");
    expect(within(card()).queryByText("Alex contrib")).toBeNull();
    expect(chart.option.series[0].data[0]).toBe(200_000);
    expect(card().textContent).toContain("Alex: no retirement balance reported yet, so the projection leaves that person out "
      + "until one is.");
    cleanup();
    chart.option = null;
    render(<RetirementCard hh={household([ALEX, SAM])} year={2030} retirementBy={{ Alex: 0, Sam: 0 }}
      reported={[]} reportedAccounts={[]} retirementAsOf={{}} balanceOnly={[]} coreSpend={{ annual: 40_000, months: 12 }} />);
    expect(chart.option, "no balance anyone reported: nothing to project").toBeNull();
    expect(kpi("FIRE crossing @7%")).toBe("—");
    expect(card().textContent).toContain("No retirement account has reported a balance yet, so there is nothing to project: "
      + "the projection appears here once one does.");
    expect(card().textContent).not.toMatch(/\$0(?![\d,])/);
  });
});

// No contribution is assumed: an adult's starts unset, and the projection is today's balance growing alone, said so
describe("the retirement projection's contributions start unset", () => {
  const SAM: HouseholdPerson = { name: "Sam", role: "owner", birth_year: 1980 };      // 50 in 2030
  const ALEX: HouseholdPerson = { name: "Alex", role: "owner", birth_year: 1985 };
  const hh = (people: HouseholdPerson[]): Household => ({ name: "The Sample household", timezone: "America/New_York", people,
    margin_apr: 0.1, retirement_accounts: { "401k A": "Alex", "401k B": "Sam" }, margin_account: null });
  const mountCard = (people: HouseholdPerson[] = [SAM]) => render(
    <RetirementCard hh={hh(people)} year={2030} retirementBy={{ Alex: 100_000, Sam: 200_000 }}
      reported={people.map((p) => p.name)} reportedAccounts={["401k A", "401k B"]} retirementAsOf={{}} balanceOnly={[]}
      coreSpend={{ annual: 40_000, months: 12 }} />);
  const card = () => screen.getByTestId("card-retirement");
  const box = (name: string) =>
    within(card()).getByTitle(new RegExp(`^${name} contrib`)).querySelector('input[type="text"]') as HTMLInputElement;
  const type = (el: HTMLInputElement, value: string) => { fireEvent.focus(el); fireEvent.change(el, { target: { value } }); fireEvent.blur(el); };
  const basis = () => screen.getByTestId("retirement-basis").textContent;
  const sevenPct = () => chart.option.series.find((s: { name: string }) => s.name === "7%").data as number[];

  it("unset: an empty box, the 7% run is today's balance growing alone, and the line under the chart says so", () => {
    mountCard();
    expect(box("Sam").value).toBe("");
    const alone = projectGrowth({ principal: 200_000, monthlyContribution: 0, annualReturnPct: 7, years: 15 })
      .filter((p) => p.month % 12 === 0).map((p) => Math.round(p.balance));
    expect(sevenPct()).toEqual(alone);
    expect(basis()).toBe("Drawn to retirement at 65default: The built-in starting age of 65, not one the household stated. "
      + "Move the slider to set one., at 5%, 7% and 9% a year, from today's balance growing alone: no monthly contribution is set."
      + VISIT);
    expect(within(card()).getByText("FIRE crossing @7%").parentElement!.textContent)
      .toMatch(new RegExp(`; growth alone, no monthly contribution set${NOMINAL}$`));
  });
  it("an amount typed sets it: the projection adds it, and neither line says none is set; the line says it lasts for this visit", () => {
    mountCard();
    type(box("Sam"), "1000");
    const withIt = projectGrowth({ principal: 200_000, monthlyContribution: 1000, annualReturnPct: 7, years: 15 })
      .filter((p) => p.month % 12 === 0).map((p) => Math.round(p.balance));
    expect(sevenPct()).toEqual(withIt);
    expect(basis()).toMatch(new RegExp(`with the monthly contributions set above\\.${VISIT}$`));
    expect(within(card()).getByText("FIRE crossing @7%").parentElement!.textContent).not.toMatch(/no monthly contribution/);
    type(box("Sam"), "");                                           // emptied: unset again
    expect(sevenPct()).toEqual(projectGrowth({ principal: 200_000, monthlyContribution: 0, annualReturnPct: 7, years: 15 })
      .filter((p) => p.month % 12 === 0).map((p) => Math.round(p.balance)));
    expect(basis()).toMatch(new RegExp(`no monthly contribution is set\\.${VISIT}$`));
  });
  it("two adults, one set: the combined line and the crossing name the one whose is unset", () => {
    mountCard([SAM, ALEX]);
    type(box("Sam"), "500");
    expect(basis()).toMatch(new RegExp(`with no monthly contribution set for Alex\\.${VISIT}$`));
    expect(within(card()).getByText("FIRE crossing @7%").parentElement!.textContent)
      .toMatch(new RegExp(`; no monthly contribution set for Alex${NOMINAL}$`));
  });
  it("the crossing says its growth is nominal against a FIRE number in today's dollars, so the real crossing is later", () => {
    mountCard();
    expect(within(card()).getByText("FIRE crossing @7%").parentElement!.textContent).toContain(
      "The growth is not adjusted for inflation while the FIRE number is in today's dollars, so the real crossing is later.");
  });
});

// A figure that rests on a value nobody stated says "default" beside it, and so does the control that holds it
describe("Projections tags what rests on a default", () => {
  const hh: Household = { name: "The Sample household", timezone: "America/New_York",
    people: [{ name: "Sam", role: "owner", birth_year: 1980 }], margin_apr: 0.1, retirement_accounts: { "401k B": "Sam" },
    margin_account: null };
  const mountCard = () => render(<RetirementCard hh={hh} year={2030} retirementBy={{ Sam: 200_000 }} reported={["Sam"]}
    reportedAccounts={["401k B"]} retirementAsOf={{}} balanceOnly={[]} coreSpend={{ annual: 40_000, months: 12 }} />);
  const card = () => screen.getByTestId("card-retirement");
  const valueOf = (label: string) => within(card()).getByText(label).nextElementSibling as HTMLElement;
  const field = (label: string) => within(card()).getByTitle(new RegExp(`^${label}`));
  const tagIn = (el: HTMLElement) => el.querySelector('[data-testid="default-tag"]');

  it("an unstated withdrawal rate: its control and the FIRE number and crossing carry the tag, a link to Data's card", () => {
    projections.stated = ALL_STATED.filter((k) => k !== "withdrawal_rate_pct");
    mountCard();
    for (const label of ["FIRE number", "FIRE crossing @7%"]) {
      const tag = tagIn(valueOf(label));
      expect(tag?.textContent, label).toMatch(/^default/);
      expect(tag?.getAttribute("href"), label).toBe("#/data?card=projection-defaults");
      expect(tag?.getAttribute("title"), label).toContain("The built-in default withdrawal rate of 4%, not one the household "
        + "stated. Set it on Data → Projection defaults.");
    }
    expect(tagIn(field("Withdrawal rate"))).not.toBeNull();
    expect(within(card()).getAllByTestId("default-tag").every((t) => /default/.test(t.textContent ?? "")), "a word, not a colour").toBe(true);
  });
  it("stated in the configuration, or saved on Data's card: no tag on the rate or the figures resting on it", () => {
    mountCard();
    expect(tagIn(valueOf("FIRE number"))).toBeNull();
    expect(tagIn(field("Withdrawal rate"))).toBeNull();
    cleanup();
    projections.stated = ALL_STATED.filter((k) => k !== "withdrawal_rate_pct");
    projections.saved = { withdrawal_rate_pct: 3.5 };
    mountCard();
    expect(tagIn(valueOf("FIRE number"))).toBeNull();
    expect(tagIn(valueOf("FIRE crossing @7%"))).toBeNull();
  });
  it("the person moving the slider makes it theirs: the tags go; reset brings them back", () => {
    projections.stated = ALL_STATED.filter((k) => k !== "withdrawal_rate_pct");
    mountCard();
    fireEvent.change(field("Withdrawal rate").querySelector('input[type="range"]')!, { target: { value: "3.5" } });
    expect(tagIn(valueOf("FIRE number"))).toBeNull();
    expect(tagIn(field("Withdrawal rate"))).toBeNull();
    fireEvent.click(within(card()).getByRole("button", { name: "reset" }));
    expect(tagIn(valueOf("FIRE number"))).not.toBeNull();
  });
  it("the built-in retirement age is tagged on its control and in the chart's line until it is moved", () => {
    mountCard();
    expect(tagIn(field("Retirement age"))).not.toBeNull();
    expect(tagIn(screen.getByTestId("retirement-basis"))).not.toBeNull();
    fireEvent.change(field("Retirement age").querySelector('input[type="range"]')!, { target: { value: "60" } });
    expect(tagIn(field("Retirement age"))).toBeNull();
    expect(screen.getByTestId("retirement-basis").textContent).toMatch(/^Drawn to retirement at 60, at 5%/);
  });
  it("unstated return scenarios: the chart's line tags them, linked to Data's card", () => {
    projections.stated = ALL_STATED.filter((k) => k !== "return_scenarios_pct");
    mountCard();
    const tags = [...screen.getByTestId("retirement-basis").querySelectorAll('[data-testid="default-tag"]')];
    expect(tags.map((t) => t.getAttribute("href"))).toEqual([null, "#/data?card=projection-defaults"]);
    expect(tags[1].getAttribute("title")).toMatch(/^The built-in default return scenarios, 5% \/ 7% \/ 9%, not ones the household stated/);
  });
});

// Today's cash already holds every day up to its balance's date: the forecast's first point is the rest of that month
describe("the forecast's first month and caption", () => {
  afterEach(() => { cashflow = TWELVE_MONTHS; });
  const caption = () => screen.getByTestId("forecast-caption").textContent ?? "";
  const firstPoint = () => chart.option.series.find((s: { name: string }) => s.name === "Projected liquid cash").data[0];
  it("the first point is the rest of June after the balance's date, and the caption says so", () => {
    render(<ForecastCard liquidCash={20_000} floor={9_000} asOf={AS_OF} />);
    expect(chart.option.xAxis.data[0]).toBe("2026-06");
    // June's net prorated to its last 10 days: a whole month would add 1,000
    expect(firstPoint()).toBe(Math.round(20_000 + 1000 * 10 / 30));
    expect(caption()).toContain("Today's cash is the balance of Jun 20, 2026, which already holds every day up to it, so the "
      + "first point is only the rest of Jun 2026, the month in progress (10 days).");
    expect(chart.option.tooltip.formatter([{ dataIndex: 0 }])).toContain("Jun 2026 — the rest of the month, 10 days");
  });
  it("is anchored on the balance's date, not the transactions' last day: a feed balance two days newer leaves 5 days", () => {
    // the transactions run through June 20 (the data context's last day); the balance is of June 25
    render(<ForecastCard liquidCash={20_000} floor={9_000} asOf="2026-06-25" />);
    expect(firstPoint()).toBe(Math.round(20_000 + 1000 * 5 / 30));
    expect(caption()).toContain("(5 days)");
  });
  it("names only what the chart draws: a net that never varied has no band and the caption names none; no floor, no floor line named", () => {
    render(<ForecastCard liquidCash={20_000} floor={0} asOf={AS_OF} />);
    expect(chart.option.series.map((s: { name: string }) => s.name)).toEqual(["Projected liquid cash"]);
    expect(caption()).toMatch(/^The line is projected liquid cash\. /);
    expect(caption()).not.toMatch(/band|dashed/);
  });
  it("a varying net draws the band and a floor its line, and the caption names both", () => {
    cashflow = TWELVE_MONTHS.map((m, i) => ({ ...m, income: 9000 + (i % 2 ? 300 : -300) }));
    render(<ForecastCard liquidCash={20_000} floor={9_000} asOf={AS_OF} />);
    expect(chart.option.series.map((s: { name: string }) => s.name)).toEqual(["low", "range", "Projected liquid cash"]);
    expect(caption()).toMatch(/^The line is projected liquid cash, the shaded band ±1σ of monthly net, widening with √months and the dashed line three months of essential spending\. /);
  });
});

// A value past the dial's end shows in the gauge's text; the needle is pinned at the end, whose label reads "12+"
describe("the emergency-fund gauge past its range", () => {
  const gauge = () => chart.option.series[0];
  it("fifty months: the needle at 12, the end label 12+, the text the real value", () => {
    render(<EmergencyFund liquidCash={5000} />);
    expect(gauge().data[0].value).toBe(12);
    expect([gauge().axisLabel.formatter(12), gauge().axisLabel.formatter(9)]).toEqual(["12+", "9"]);
    expect(gauge().detail.formatter()).toBe("50.0 mo");
  });
  it("within the range (the control): the needle at the value and no +", () => {
    render(<EmergencyFund liquidCash={1000} />);
    expect(gauge().data[0].value).toBe(10);
    expect(gauge().axisLabel.formatter(12)).toBe("12");
  });
});

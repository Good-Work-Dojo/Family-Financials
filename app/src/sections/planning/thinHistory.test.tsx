// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// Projections on households with little history: a first week (no full month), one full month, and three or five full
// months. A run-rate a plan is built on (the FIRE number's core spend, the savings pace and the prefills from it, the
// emergency fund's essentials and the runway, the stress test's years of saving, the spending-cut card) needs three full
// months: below that each one reads "—" with the line that says how many there are, and from three to eleven it says
// from how many months it is worked out. A control whose seed would come from an unknown rate starts at its minimum,
// and what rests on it waits for an amount the person sets.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NO_TRANSACTIONS_YET, NOT_ENOUGH_HISTORY } from "../../components/NoData";
import { buildIndexes } from "../../data/DataContext";
import type { AppData, HouseholdPerson } from "../../data/types";
import { cfmFactory, firstWeekData, fullMonthsData, oneFullMonthData, recFactory, txFactory } from "../../lib/__fixtures__";
import { monthsBetween } from "../../lib/agg";
import { _resetEfundOverridesMemory } from "../../lib/efundOverrides";
import { fmtCompact, fmtUSD0 } from "../../lib/format";
import { OpenRoutesProvider } from "../../lib/modules";
import { emptyPrefs } from "../../lib/prefs";
import { projectGrowth } from "../../lib/projections";

const state = vi.hoisted(() => ({ ctx: null as unknown, charts: [] as any[] }));
vi.mock("../../data/DataContext", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../data/DataContext")>()),
  useData: () => state.ctx,
}));
vi.mock("../../components/EChart", () => ({
  default: (p: { option: unknown }) => { state.charts.push(p.option); return <div data-testid="chart" />; },
}));

import Tools from "../Tools";

/** two adults with birth years, and a retirement account mapped to `mapped` (none by default) */
const ADULTS: HouseholdPerson[] = [{ name: "Alex", role: "owner", birth_year: 1986 }, { name: "Sam", role: "owner", birth_year: 1988 }];
function withAdults(d: AppData, mapped: Record<string, string> = {}): AppData {
  return { ...d, assumptions: { ...d.assumptions, household: { ...d.assumptions.household, people: ADULTS,
    retirement_accounts: mapped } } };
}

/** the household with its first month's rows moved from the 1st to the 12th: a month the data covers in part */
function beginsOn12th(d: AppData): AppData {
  const first = d.transactions.reduce((m, t) => (t.date < m ? t.date : m), "9999-12-31").slice(0, 7);
  const move = (rows: AppData["transactions"]) => rows.map((t) => (t.date === `${first}-01` ? { ...t, date: `${first}-12` } : t));
  return { ...d, transactions: move(d.transactions), purchases: move(d.purchases) };
}

function use(data: AppData) {
  state.ctx = {
    data, missing: new Set<string>(), indexes: buildIndexes(data.transactions, data.purchases, data.manifest.generated_at),
    prefs: { loading: false, localOnly: false, refused: null, doc: emptyPrefs(), setBudget: () => {}, setTarget: () => {},
      setTargetMix: () => true, setProjection: () => {}, setPayrollPhases: () => {}, saveFilter: () => true,
      removeSavedFilter: () => {}, renameSavedFilter: () => true, saveReport: () => true, removeReport: () => {},
      renameReport: () => true },
    answers: {}, overlay: { pendingIds: new Set<string>(), conflictedKeys: new Set<string>() }, reload: async () => {},
  };
}

beforeEach(() => { localStorage.clear(); _resetEfundOverridesMemory(); state.charts = []; });
afterEach(cleanup);

const mount = (page: ReactElement) => render(<MemoryRouter>{page}</MemoryRouter>);
const card = (id: string) => screen.getByTestId(id);
const text = (id: string) => card(id).textContent ?? "";
/** a tile's text, label, value and sub-line run together */
const tile = (id: string, label: string) => within(card(id)).getByText(label).parentElement?.textContent ?? "";
const slider = (id: string, label: string) =>
  within(card(id)).getByTitle(new RegExp(`^${label}`)).querySelector('input[type="range"]') as HTMLInputElement;
const button = (id: string, name: string) => within(card(id)).getByRole("button", { name });
/** the page's one line, with `have` full months so far and what it says of the next: "the next one ends <ends>." */
const pageLine = (have: number, next: string) => "Projections plans from the household's own spending and saving: the "
  + "savings pace, the FIRE number and the emergency fund need 3 full months of transactions, the cash-flow forecast 6 full "
  + `months. So far: ${have} full month${have === 1 ? "" : "s"}; ${next}`;
const ENDS_JUNE = "the next one ends Jun 30, 2026.";
/** a run-rate tile's own line with `have` full months so far: what it needs and how many there are */
const waits = (have: number) => `Not enough history yet: needs 3 full months of transactions, ${have} so far`;

describe("Projections with too little history to plan from", () => {
  // the first week's data begins on June 6th, in the month in progress: June will be no full month, so the next is July
  for (const [name, data, have, next] of [["a first week (no full month)", firstWeekData, 0, "the next one ends Jul 31, 2026."],
    ["a month the data begins on the 3rd of (a full month: the 3rd is within its first days)", oneFullMonthData, 1, ENDS_JUNE],
    ["one whole month", () => fullMonthsData(1), 1, ENDS_JUNE],
    ["two whole months after a month begun on the 12th", () => beginsOn12th(fullMonthsData(3)), 2, ENDS_JUNE]] as const) {
    describe(name, () => {
      it("one line at the top says what the page plans from, what it needs, how many full months there are and when the next ends", () => {
        use(withAdults(data()));
        mount(<Tools />);
        expect(screen.getByTestId("planning-history").textContent).toBe(pageLine(have, next));
        const page = document.body.textContent ?? "";
        expect(page, "the long sentence is said once, at the top").not.toMatch(/this needs 3 full months|there (is|are) \d/);
        expect(page.split(NOT_ENOUGH_HISTORY).length - 1,
          "the short line on the two gated cards, and the reason on each of the four run-rate tiles").toBe(6);
      });
      it("the runway is unknown: — and what it needs; the emergency fund and the forecast keep one short line each", () => {
        use(withAdults(data()));
        mount(<Tools />);
        expect(text("kpi-cash-runway")).toBe(`Cash runway—${waits(have)}`);
        expect(card("kpi-cash-runway").tagName, "nothing to scroll to").not.toBe("BUTTON");
        expect(text("card-emergency-fund")).toBe("Emergency fund — months of ESSENTIAL spending covered by cashNot enough history yet.");
        expect(within(card("card-emergency-fund")).queryByTestId("chart"), "no gauge").toBeNull();
        expect(text("card-forecast")).toBe("Cash flow forecast — next 12 months (estimate)Not enough history yet.");
      });
      it("the FIRE number and its crossing read — with what they need; no core spend is named", () => {
        use(withAdults(data()));
        mount(<Tools />);
        expect(tile("card-retirement", "FIRE number")).toBe(`FIRE number—${waits(have)}`);
        expect(tile("card-retirement", "FIRE crossing @7%")).toBe(`FIRE crossing @7%—${waits(have)}`);
        expect(text("card-retirement")).not.toMatch(/spending less|trailing/);
      });
      it("the net-worth projection waits, unset, for an amount the person sets; one they set draws it, none included", () => {
        use(withAdults(data()));
        mount(<Tools />);
        expect(Number(slider("card-networth-projection", "Monthly savings").value)).toBe(0);
        expect(within(card("card-networth-projection")).queryByTestId("chart")).toBeNull();
        expect(text("card-networth-projection")).toContain("Set a monthly amount above to draw the projection.");
        expect(text("card-networth-projection")).not.toMatch(/your actual|prefilled|Not enough history/);
        fireEvent.change(slider("card-networth-projection", "Monthly savings"), { target: { value: "1000" } });
        expect(within(card("card-networth-projection")).getByTestId("chart"), "an amount the person set draws it").toBeDefined();
        fireEvent.change(slider("card-networth-projection", "Monthly savings"), { target: { value: "0" } });
        expect(within(card("card-networth-projection")).getByTestId("chart"), "none, chosen, is growth alone: drawn").toBeDefined();
        fireEvent.click(button("card-networth-projection", "reset"));
        expect(within(card("card-networth-projection")).queryByTestId("chart"), "reset: unset again").toBeNull();
      });
      it("the stress test names no savings pace: its years read — with what they need", () => {
        use(withAdults(data()));
        mount(<Tools />);
        expect(tile("card-market-shock", "≈ Years of saving")).toBe(`≈ Years of saving—${waits(have)}`);
        expect(text("card-market-shock")).not.toMatch(/savings pace|your actual/);
      });
      it("the spending cut waits, unset, for a cut the person sets: any cut draws its tiles, none included", () => {
        use(withAdults(data()));
        mount(<Tools />);
        expect(Number(slider("card-spending-cut", "Monthly spending cut").value)).toBe(0);
        for (const y of [10, 20, 30]) expect(tile("card-spending-cut", `After ${y} years`)).toBe(`After ${y} years—`);
        expect(text("card-spending-cut")).toContain("Set a monthly cut above to see what it grows to.");
        expect(text("card-spending-cut"), "no amount, no share of net worth, no history line").not.toMatch(/\$\d|net worth|Not enough history/);
        fireEvent.change(slider("card-spending-cut", "Monthly spending cut"), { target: { value: "500" } });
        expect(tile("card-spending-cut", "After 10 years")).toMatch(/^After 10 years\$\d/);
        expect(text("card-spending-cut")).toContain("of today's net worth");
        expect(text("card-spending-cut")).not.toContain("Set a monthly cut");
        fireEvent.change(slider("card-spending-cut", "Monthly spending cut"), { target: { value: "0" } });
        expect(tile("card-spending-cut", "After 10 years"), "a cut of none, set, grows to $0").toMatch(/^After 10 years\$0/);
      });
    });
  }

  it("no retirement account in the data: each adult reads —, nothing is projected, and the card says what brings one", () => {
    use(withAdults(oneFullMonthData()));
    mount(<Tools />);
    for (const who of ["Alex", "Sam"]) {
      expect(tile("card-retirement", `${who} retirement today`)).toBe(`${who} retirement today—no retirement account yet`);
    }
    expect(within(card("card-retirement")).queryByText(/contrib$/), "no contribution to an account that does not exist").toBeNull();
    expect(within(card("card-retirement")).queryByTestId("chart"), "no projection drawn from a made-up contribution").toBeNull();
    expect(text("card-retirement")).toContain("No retirement account in the data yet. One arrives with an account whose class "
      + "is Retirement; map it to the person it belongs to and the projection appears here (retirement_accounts: under "
      + "household: in assumptions.yaml).");
    expect(text("card-retirement")).not.toMatch(/mapped to anyone/);
    expect(text("card-retirement")).not.toMatch(/\$0(?![\d,])/);
  });

  it("the net-worth projection speaks of house appreciation only for a household holding a home", () => {
    const d = withAdults(oneFullMonthData());
    use(d);
    mount(<Tools />);
    expect(screen.getByTestId("networth-projection-basis").textContent).toMatch(/^Growth applies to the full net worth\. It grows at/);
    expect(text("card-networth-projection")).not.toMatch(/house/i);
    expect(within(card("card-market-shock")).queryByText("Home value"), "no home: no home slider").toBeNull();
    cleanup();
    const snap = d.assets.snapshots[d.assets.snapshots.length - 1];
    use({ ...d, assets: { snapshots: [{ ...snap, by_class: { ...snap.by_class, House: 400_000 }, total: snap.total + 400_000 }] } });
    mount(<Tools />);
    expect(screen.getByTestId("networth-projection-basis").textContent)
      .toMatch(/^Growth applies to the full net worth incl\. house \(house appreciation folded into the single return knob\)\./);
    expect(within(card("card-market-shock")).getByText("Home value")).toBeDefined();
  });
  it("a retirement account in the data that no one is mapped to: each adult reads —, and the card says to map it", () => {
    const d = withAdults(oneFullMonthData());
    use({ ...d, accounts: [...d.accounts, { ...d.accounts[0], id: "ira", name: "Sample IRA", kind: "brokerage",
      asset_class: "Retirement" }] });
    mount(<Tools />);
    for (const who of ["Alex", "Sam"]) {
      expect(tile("card-retirement", `${who} retirement today`)).toBe(`${who} retirement today—no retirement account mapped`);
    }
    expect(text("card-retirement")).toContain("No retirement account is mapped to anyone yet, so there is nothing to project: "
      + "map each one to the person it belongs to and the projection appears here (retirement_accounts: under household: in "
      + "assumptions.yaml).");
  });

  it("one adult mapped: the other reads —, is left out of the projection, and the card names them", () => {
    const d = withAdults(fullMonthsData(5), { "Alex 401k": "Alex" });
    use({ ...d, holdings: [{ account: "Alex 401k", account_id: null, institution: "Sample Bank", owner: "Alex", symbol: "VTI",
      name: "Index", asset_class: "Retirement", quantity: 1, value: 50_000, cost_basis: null, as_of: "2026-06-09", liquid: false,
      notes: null }] });
    mount(<Tools />);
    expect(tile("card-retirement", "Alex retirement today")).toContain(fmtUSD0(50_000));
    expect(tile("card-retirement", "Sam retirement today")).toBe("Sam retirement today—no retirement account mapped");
    expect(within(card("card-retirement")).getByText("Alex contrib")).toBeDefined();
    expect(within(card("card-retirement")).queryByText("Sam contrib")).toBeNull();
    expect(within(card("card-retirement")).queryByRole("button", { name: "Sam" }), "no projection to pick for Sam").toBeNull();
    expect(text("card-retirement")).toContain("Sam has no retirement account mapped, so the projection leaves that balance "
      + "and contribution out until one is (retirement_accounts: under household: in assumptions.yaml).");
    // the combined projection is Alex's 50,000 alone, growing with no contribution (none is assumed until one is set)
    const growth = state.charts.find((o) => o?.series?.[0]?.name === "5%");
    expect(growth.series[0].data[0]).toBe(50_000);
    expect(growth.series[0].data[1]).toBe(Math.round(projectGrowth({ principal: 50_000, monthlyContribution: 0, annualReturnPct: 5,
      years: 1 }).at(-1)!.balance));
    expect(screen.getByTestId("retirement-basis").textContent).toMatch(
      /from today's balance growing alone: no monthly contribution is set\. An amount typed in a contribution box lasts for this visit\.$/);
  });
});

describe("a retirement account the feed reports at $0", () => {
  it("is a true $0: the adult keeps the contribution field and the projection is drawn from 0", () => {
    const d = withAdults(fullMonthsData(5), { "Alex 401k": "Alex" });
    const snap = d.assets.snapshots[0];
    const zero = { ...snap.rows[0], account: "Alex 401k", account_id: "k401", asset_class: "Retirement", value: 0, liquid: false,
      source: "simplefin_balance" };
    use({ ...d, assets: { snapshots: [{ ...snap, rows: [...snap.rows, zero] }] } });
    mount(<Tools />);
    expect(tile("card-retirement", "Alex retirement today")).toMatch(new RegExp(`^Alex retirement today\\${fmtUSD0(0)}Alex 401k`));
    expect(within(card("card-retirement")).getByText("Alex contrib")).toBeDefined();
    const growth = () => [...state.charts].reverse().find((o) => o?.series?.[0]?.name === "5%");
    expect(growth()?.series[0].data[0], "the projection starts from the reported $0").toBe(0);
    expect(growth().series[0].data[1], "with no contribution set, $0 stays $0").toBe(0);
    // a contribution set: the projection grows from the $0
    const contrib = within(card("card-retirement")).getByTitle(/^Alex contrib/).querySelector('input[type="text"]') as HTMLInputElement;
    fireEvent.focus(contrib); fireEvent.change(contrib, { target: { value: "1000" } }); fireEvent.blur(contrib);
    expect(growth().series[0].data[0]).toBe(0);
    expect(growth().series[0].data[1]).toBeGreaterThan(12_000);
  });
});

describe("an unset amount is an empty box, and any amount typed into it sets it, none included", () => {
  /** the card's money box (the text box beside the slider) */
  const moneyBox = (id: string, label: string) =>
    within(card(id)).getByTitle(new RegExp(`^${label}`)).querySelector('input[type="text"]') as HTMLInputElement;
  const type = (el: HTMLInputElement, value: string) => {
    fireEvent.focus(el); fireEvent.change(el, { target: { value } }); fireEvent.blur(el);
  };
  const chart = () => within(card("card-networth-projection")).queryByTestId("chart");
  it("unset: the box is empty, with no 0 in it, and the slider at its minimum", () => {
    use(withAdults(fullMonthsData(1)));
    mount(<Tools />);
    expect(moneyBox("card-networth-projection", "Monthly savings").value).toBe("");
    expect(moneyBox("card-networth-projection", "Monthly savings").getAttribute("placeholder"), "no 0 shown, even faint").toBeNull();
    expect(Number(slider("card-networth-projection", "Monthly savings").value)).toBe(0);
    expect(moneyBox("card-spending-cut", "Monthly spending cut").value).toBe("");
  });
  it("unset, then 0 typed: the projection draws at none a month", () => {
    use(withAdults(fullMonthsData(1)));
    mount(<Tools />);
    type(moneyBox("card-networth-projection", "Monthly savings"), "0");
    expect(chart(), "0, typed, is a choice: growth alone").not.toBeNull();
    expect(text("card-networth-projection")).not.toContain("Set a monthly amount");
  });
  it("unset, then 500 typed: the projection draws, and the slider follows", () => {
    use(withAdults(fullMonthsData(1)));
    mount(<Tools />);
    type(moneyBox("card-networth-projection", "Monthly savings"), "500");
    expect(chart()).not.toBeNull();
    expect(Number(slider("card-networth-projection", "Monthly savings").value)).toBe(500);
  });
  it("set, then the box emptied: unset again, the chart waits and the box is empty", () => {
    use(withAdults(fullMonthsData(1)));
    mount(<Tools />);
    type(moneyBox("card-networth-projection", "Monthly savings"), "500");
    type(moneyBox("card-networth-projection", "Monthly savings"), "");
    expect(chart(), "back to unset: —").toBeNull();
    expect(text("card-networth-projection")).toContain("Set a monthly amount above to draw the projection.");
    expect(moneyBox("card-networth-projection", "Monthly savings").value).toBe("");
    expect(Number(slider("card-networth-projection", "Monthly savings").value)).toBe(0);
  });
  it("the spending cut the same way: 0 typed draws its tiles at $0; the box emptied unsets them", () => {
    use(withAdults(fullMonthsData(1)));
    mount(<Tools />);
    type(moneyBox("card-spending-cut", "Monthly spending cut"), "0");
    expect(tile("card-spending-cut", "After 10 years")).toMatch(/^After 10 years\$0/);
    type(moneyBox("card-spending-cut", "Monthly spending cut"), "");
    expect(tile("card-spending-cut", "After 10 years")).toBe("After 10 years—");
  });
});

describe("the line names no day that has passed", () => {
  it("data that stops in April, ingested in June: no date, the count grows once later transactions are read", () => {
    const d = fullMonthsData(5);
    const upToApril = d.transactions.filter((t) => t.date < "2026-05-01");
    use(withAdults({ ...d, transactions: upToApril, purchases: upToApril,
      cashflow: d.cashflow.filter((m) => m.month <= "2026-04") }));
    mount(<Tools />);
    // April is the month in progress (the data's newest), so three full months: the run-rates are known, the forecast waits
    expect(screen.getByTestId("planning-history").textContent)
      .toBe(pageLine(3, "the count grows once later transactions are read."));
  });
});

describe("the forecast counts the months the page's line counts", () => {
  it("a first month begun on the 12th, then five whole months: the line says 5 and the forecast waits", () => {
    use(withAdults(beginsOn12th(fullMonthsData(6))));
    mount(<Tools />);
    expect(screen.getByTestId("planning-history").textContent).toContain("So far: 5 full months;");
    expect(text("card-forecast")).toBe("Cash flow forecast — next 12 months (estimate)Not enough history yet.");
  });
  it("the same with six whole months: the forecast draws, from those six, and the line has nothing left to wait for", () => {
    use(withAdults(beginsOn12th(fullMonthsData(7))));
    mount(<Tools />);
    expect(screen.queryByTestId("planning-history"), "every part has what it needs").toBeNull();
    expect(within(card("card-forecast")).getByTestId("chart")).toBeDefined();
    expect(tile("card-forecast", "Baseline spend")).toContain("trailing 6 full months");
  });
});

describe("Projections over fewer than twelve full months", () => {
  it("the emergency fund averages the five full months there are and says so", () => {
    use(withAdults(fullMonthsData(5)));
    mount(<Tools />);
    expect(text("card-emergency-fund")).toContain("basis: Jan 2026 → May 2026 averages (5 full months)");
    expect(text("card-emergency-fund")).toContain(`${fmtUSD0(12_000)} cash ÷ ${fmtUSD0(400)}/mo essentials`);
    expect(text("kpi-cash-runway")).toContain("30.0 mo");
  });
  it("the FIRE number is the five months' spending annualised, and says so", () => {
    use(withAdults(fullMonthsData(5)));
    mount(<Tools />);
    expect(tile("card-retirement", "FIRE number")).toBe(
      `FIRE number${fmtUSD0(180_000)}spending less Checks, Wire and ATM: ${fmtUSD0(7_200)} a year, from 5 full months, annualised, ÷ 4%`);
    expect(text("card-retirement")).not.toMatch(/trailing-12mo/);
  });
  it("the savings pace is prefilled from the five months and named as such, never as a trailing twelve", () => {
    use(withAdults(holdingStocks(fullMonthsData(5))));
    mount(<Tools />);
    expect(Number(slider("card-networth-projection", "Monthly savings").value), "the seed: the pace rounded to the slider's step")
      .toBe(4_500);
    expect(within(card("card-networth-projection")).getByTitle(/^Monthly savings/).getAttribute("title"))
      .toBe(`Monthly savings — prefilled from your average of income − spending, from 5 full months (${fmtUSD0(4_400)}/mo), `
        + `rounded to the slider's ${fmtUSD0(250)} steps`);
    expect(within(card("card-networth-projection")).getByTestId("chart"), "a seed from a real pace draws it").toBeDefined();
    // the years count at the real pace, which the tile names
    expect(tile("card-market-shock", "≈ Years of saving")).toContain(`at your ${fmtUSD0(4_400)}/mo savings pace, from 5 full months`);
    expect(screen.queryByTestId("planning-history"), "three to five full months: the forecast still waits, so the line stays")
      .not.toBeNull();
  });
  it("three full months are enough: the spending cut starts at its usual 500", () => {
    use(withAdults(fullMonthsData(3)));
    mount(<Tools />);
    expect(Number(slider("card-spending-cut", "Monthly spending cut").value)).toBe(500);
    const grown = projectGrowth({ principal: 0, monthlyContribution: 500, annualReturnPct: 7, years: 10 }).at(-1)!.balance;
    expect(tile("card-spending-cut", "After 10 years")).toContain(fmtCompact(grown));
    expect(text("card-spending-cut")).not.toContain(NOT_ENOUGH_HISTORY);
  });
});

/** a household whose income does not cover its spending: `n` whole months to May 2026 of 600 spent and 200 earned (a
 *  pace of −400 a month), June in progress */
function spendsMoreThanItEarns(n: number): AppData {
  const tx = txFactory({ account: "chk", account_name: "Checking" });
  const months = monthsBetween("2026-01", "2026-05").slice(5 - Math.min(n, 5));
  const before = n > 5 ? monthsBetween("2024-01", "2025-12").slice(24 - (n - 5)) : [];
  const all = [...before, ...months];
  const transactions = [
    ...all.flatMap((m) => [
      tx({ date: `${m}-01`, category: "Groceries", subcategory: "Groceries", vendor: "Market", amount: -600 }),
      tx({ date: `${m}-20`, category: "Income", subcategory: "Interest", vendor: "Bank", amount: 200, type: "interest" }),
    ]),
    tx({ date: "2026-06-04", category: "Groceries", subcategory: "Groceries", vendor: "Market", amount: -50 }),
  ];
  const base = fullMonthsData(1);
  const cfm = cfmFactory({ accounts_covered: ["chk"] });
  return { ...base, transactions, purchases: transactions,
    cashflow: [...all.map((month) => cfm({ month, income: 200, spend: 600, savings_rate: -2 })),
      cfm({ month: "2026-06", income: 0, spend: 50, savings_rate: null })] };
}

/** `d` holding stocks too (10,000 in its newest snapshot): a market fall has something to move, so the stress test's
 *  years of saving count against a loss and name the pace */
function holdingStocks(d: AppData): AppData {
  const snaps = d.assets.snapshots, newest = snaps[snaps.length - 1];
  return { ...d, assets: { ...d.assets, snapshots: [...snaps.slice(0, -1),
    { ...newest, by_class: { ...newest.by_class, "Stocks/ETF": 10_000 }, total: newest.total + 10_000 }] } };
}

describe("a savings pace at or below none", () => {
  for (const [name, n] of [["five months", 5], ["fourteen months (a household with no salary)", 14]] as const) {
    it(`${name}: nothing calls the seed 'your actual'; the slider starts at none, the line says why, the years read —`, () => {
      use(withAdults(holdingStocks(spendsMoreThanItEarns(n))));
      mount(<Tools />);
      const months = Math.min(n, 12);
      const why = `Income did not cover spending over the last ${months} full months (${fmtUSD0(-400)}/mo)`;
      expect(Number(slider("card-networth-projection", "Monthly savings").value)).toBe(0);
      expect(within(card("card-networth-projection")).getByTitle(/^Monthly savings/).getAttribute("title"))
        .toBe(`Monthly savings — ${why}, so this starts at none.`);
      expect(text("card-networth-projection")).toContain(`${why}, so savings start at none: set a monthly amount above to draw the projection.`);
      expect(within(card("card-networth-projection")).queryByTestId("chart"), "unset: no projection at $0 called the household's own")
        .toBeNull();
      expect(tile("card-market-shock", "≈ Years of saving"))
        .toBe(`≈ Years of saving—no savings pace: income did not cover spending over the last ${months} full months (${fmtUSD0(-400)}/mo)`);
      expect(document.body.textContent ?? "", "no pace or seed is called the household's actual").not.toMatch(/your actual (trailing|\$|−)/);
    });
  }
});

describe("the bear case and the stress test on a household with nothing a market fall moves", () => {
  it("cash only: the bear case is its net worth with no warning tone and says why; the stress test names no loss", () => {
    use(withAdults(oneFullMonthData()));             // 8,000 of cash and nothing else
    mount(<Tools />);
    expect(text("kpi-bear-case")).toBe(`Bear-case net worth${fmtUSD0(8_000)}nothing held falls in this case (no Stocks/ETF, `
      + "Retirement, 529, Real Estate/Venture, Crypto or Private Stock) — sliders in the stress test below");
    expect(card("kpi-bear-case").querySelector(".text-amber-400"), "no warning tone on an unchanged net worth").toBeNull();
    expect(tile("card-market-shock", "Paper loss")).toBe("Paper loss—nothing held that the sliders move");
    const after = within(card("card-market-shock")).getByText("Net worth after shock").nextElementSibling!;
    expect(after.textContent).toBe(fmtUSD0(8_000));
    expect(after.className, "no warning tone").not.toMatch(/amber|red/);
  });
});

describe("the lumpy-bills tile", () => {
  it("nothing detected: — and says so, never $0", () => {
    use(withAdults(oneFullMonthData()));
    mount(<Tools />);
    expect(text("kpi-big-bills")).toBe("Lumpy bills, next 12 months—none detected yet");
  });
  it("only monthly charges detected: — and no renewal due, never $0", () => {
    const d = oneFullMonthData();
    use(withAdults({ ...d, recurring: [recFactory()({ vendor: "Gym", account_id: "chk", cadence: "monthly", typical_amount: 40,
      active: true, next_expected: "2026-06-20" })] }));
    mount(<Tools />);
    expect(text("kpi-big-bills")).toBe("Lumpy bills, next 12 months—no annual or quarterly renewal due");
  });
  it("no transaction at all: the no-transactions line, as before", () => {
    use(withAdults({ ...oneFullMonthData(), transactions: [], purchases: [] }));
    mount(<Tools />);
    expect(text("kpi-big-bills")).toBe(`Lumpy bills, next 12 months—${NO_TRANSACTIONS_YET}`);
  });
});

describe("the page's footnote", () => {
  const footnote = () => screen.getByText(/All projections are deterministic illustrations with the stated assumptions/);
  it("points at the mortgage what-if only while the House page shows: no sentence about a page the household lacks", () => {
    use(withAdults(oneFullMonthData()));
    mount(<Tools />);
    expect(footnote().textContent).toMatch(/^The mortgage extra-payment what-if lives in Planning → House\. All projections are/);
    expect(within(footnote()).getByRole("link", { name: "Planning → House" }).getAttribute("href")).toBe("/planning/home");
    cleanup();
    const houseOff = (path: string) => !path.startsWith("/planning/home");
    render(<MemoryRouter><OpenRoutesProvider value={houseOff}><Tools /></OpenRoutesProvider></MemoryRouter>);
    expect(footnote().textContent).toMatch(/^All projections are deterministic illustrations/);
    expect(footnote().textContent).not.toMatch(/mortgage|House/);
  });
});

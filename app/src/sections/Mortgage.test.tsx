// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MortgageBlock } from "../data/types";
import { mortgageFactory } from "../lib/__fixtures__";
import { fmtUSD, fmtUSD0 } from "../lib/format";
import { MASK, setPrivate } from "../lib/privacy";

// the stubbed chart records the option it is handed, so a test can read which lines are drawn
const charts = vi.hoisted(() => ({ options: [] as any[] }));
vi.mock("../components/EChart", () => ({
  default: (p: { option: unknown }) => { charts.options.push(p.option); return <div data-testid="chart" />; },
}));

// Mortgage reads only assumptions, the accounts (the loan's name), the current snapshot's House-class rows (the home's
// value) + the manifest, and the family's saved projection defaults; a hand-built slice is enough.
let mortgage: MortgageBlock | null = null;
let marginAccount: string | null = null;
let marginApr: number | null = 0.11;
let rows: Record<string, unknown>[] = [];
// the return scenarios the household states (a one-rate figure grows at their middle) and whether it states them
let returns = [5, 7, 9];
let stated = ["return_scenarios_pct"];
vi.mock("../data/DataContext", () => ({
  useData: () => ({
    data: {
      manifest: { current_snapshot_date: "2026-06-01", generated_at: "2026-06-02T00:00:00-05:00" },
      accounts: [{ id: "demo_loan_0000", name: "Sample Home Loan" }],
      assets: { snapshots: [{ snapshot_date: "2026-06-01", rows }] },
      assumptions: {
        household: { people: [], margin_apr: marginApr, retirement_accounts: {}, margin_account: marginAccount },
        projections: { return_scenarios_pct: returns },
        projections_stated: stated,
        mortgage,
      },
    },
    prefs: { doc: { projections: {} } },
  }),
}));

import Mortgage from "./Mortgage";

const EMPTY = mortgageFactory();
const FULL = mortgageFactory({
  balance: 300000, balance_as_of: "2026-05-18", rate_pct: 3, monthly_payment: 1400, monthly_escrow: 1000,
  escrow_balance: 8000,
  schedule: [
    { date: "2025-12-31", principal_balance: 305000 },
    { date: "2026-05-31", principal_balance: 301000 },
  ],
});

/** a House-class row of the current snapshot: a home stated by hand, as an accounts.yaml manual entry states it */
const house = (value: number) => ({ account: "Sample Home", account_id: null, institution: "Self", owner: "Joint",
  asset_class: "House", value, liquid: false, as_of: "2026-01-31", as_of_assumed: false, needs_review: false, source: "stated",
  manual_id: "sample-home", carried_back: false });

beforeEach(() => {
  mortgage = EMPTY; marginAccount = null; marginApr = 0.11; rows = [house(900000)]; returns = [5, 7, 9];
  stated = ["return_scenarios_pct"]; charts.options = [];
});
afterEach(cleanup);

// "$0" not followed by more digits/commas, and a bare "0%" (not 10%, 2.0%)
const FABRICATED = /\$0(?![\d,])|(^|[^\d.])0%|NaN|undefined|null/;
const kpis = () => screen.getByTestId("kpis-mortgage").textContent ?? "";
/** the source tooltip of the slider whose label starts `label` */
const sourceOf = (c: HTMLElement, label: string) =>
  [...c.querySelectorAll("label[title]")].map((l) => l.getAttribute("title") ?? "").find((t) => t.startsWith(label)) ?? "";
/** the range input of the slider whose label starts `label` */
const slider = (c: HTMLElement, label: string) => [...c.querySelectorAll("label[title]")]
  .find((l) => (l.getAttribute("title") ?? "").startsWith(label))?.querySelector<HTMLInputElement>('input[type="range"]') ?? null;
/** the sentence that asks for a loan term, as Accounts -> Liabilities' mortgage card words it */
const add = (key: string, shows: string) => `Add ${key} under loan: on this account in accounts.yaml to see ${shows}.`;

describe("Mortgage with no balance, rate or payment in the data", () => {
  it("renders em dashes and a note, never an invented number", () => {
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(container.textContent).not.toMatch(FABRICATED);
    expect(screen.getByText("no mortgage balance in the data")).toBeDefined();
    expect(kpis()).toMatch(/Principal balance—no balance from the bank feed yet/);
    // the prepay-vs-invest card is replaced by a one-line note, not drawn from a 0% rate
    expect(screen.queryByText(/Prepay vs invest/)).toBeNull();
    expect(screen.getByTestId("no-prepay-compare").textContent).toMatch(/needs the mortgage balance, rate and monthly P&I/);
    // the footnote says what is missing instead of quoting a payment and rate
    expect(container.textContent).toMatch(/No projection yet/);
    expect(container.textContent).toMatch(/No balance history from the bank feed yet\./);
  });

  it("a balance without a rate or payment still shows nothing invented", () => {
    mortgage = { ...EMPTY, balance: 250000, balance_as_of: "2026-05-18" };
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(container.textContent).not.toMatch(FABRICATED);
    expect(screen.queryByTestId("no-prepay-compare")).not.toBeNull();
  });

  it("a zero balance reads as paid off, not as missing data", () => {
    mortgage = { ...FULL, balance: 0, balance_as_of: "2026-05-18" };
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(kpis()).toMatch(/Principal balance\$0paid off · from the bank feed · May 18, 2026/);
    expect(kpis()).toMatch(/Payoff at current pacePaid offthe bank feed shows nothing owed/);
    expect(container.textContent).toMatch(/No projection: the loan is paid off\./);
    expect(screen.getByTestId("no-prepay-compare").textContent).toBe("The loan is paid off: there is nothing left to prepay.");
    expect(container.textContent).not.toMatch(/no balance|No projection yet|needs the mortgage balance/);
  });
});

describe("Mortgage with every loan term", () => {
  it("keeps the footnote clauses and the prepay card, with a four-digit-year history range", () => {
    mortgage = FULL;
    marginAccount = "Brokerage X";
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    const t = container.textContent ?? "";
    expect(t).toMatch(/History: the loan account's balance from the bank feed, Dec 2025 → May 2026\./);
    expect(t).toMatch(/Projection assumes monthly P&I of \$1,400 at 3%/);
    expect(t).toMatch(/escrow \(\$12,000\/yr taxes \+ insurance, \$8,000 escrow balance\)/);
    expect(t).toMatch(/At 3%, each dollar prepaid saves 3% a year in interest; a dollar paid off the Brokerage X margin saves 11%\./);
    expect(screen.getByText(/Prepay vs invest/)).toBeDefined();
    expect(screen.queryByTestId("no-prepay-compare")).toBeNull();
    expect(t).not.toMatch(FABRICATED);
  });

  it("the prepay card names the loan terms' rate, never an estimate", () => {
    mortgage = FULL;
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(screen.getByTestId("prepay-basis").textContent).toMatch(
      /^Prepaying is worked out as the loan charges interest: 3% a year accrued monthly, 3\.04% over a year/);
    expect(screen.getByTestId("prepay-basis").textContent).toContain("before tax on either side");
    expect(container.textContent).not.toMatch(/estimated/);
  });

  // The page claims nothing about a market instrument it has no data for: the loan's own rate and the arithmetic of the
  // choice, and a comparison of the two numbers the card computed, in neutral words.
  it("names no market instrument, yield or winner anywhere", () => {
    mortgage = FULL; marginAccount = "Brokerage X";
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    fireEvent.change(slider(container, "Extra $/month")!, { target: { value: "500" } });
    expect(container.textContent).not.toMatch(/treasur|beats|risk-free|bond|market|index portfolio|Cost of prepaying|expensive/i);
  });

  // the copy rule: a figure says where it comes from, an absent term says what to add; the account's name, not a
  // fragment of its number, names the loan. A rate stated as 0 is stated.
  it("the slider sources say the rate and payment come from the loan terms, or what to add", () => {
    mortgage = { ...FULL, rate_pct: 0 };
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(sourceOf(container, "Rate (APR)")).toBe("Rate (APR) — from your loan terms (Sample Home Loan)");
    expect(sourceOf(container, "Monthly P&I")).toBe(
      "Monthly P&I — from your loan terms (Sample Home Loan) — escrow $1,000/mo is separate");
    cleanup();
    mortgage = { ...FULL, rate_pct: null, monthly_payment: null };
    const again = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(sourceOf(again.container, "Rate (APR)")).toMatch(
      /no rate in your loan terms — state it in the mortgage account's loan terms in the configuration \(rate under loan:\), or set it here$/);
    expect(sourceOf(again.container, "Monthly P&I")).toMatch(
      /no monthly payment in your loan terms — state it in the mortgage account's loan terms in the configuration \(monthly_payment under loan:\), or set it here$/);
  });

  it("the bank feed's balance drives the KPIs, equity and LTV, and the page names its source", () => {
    mortgage = { ...FULL, balance: 280000, balance_as_of: "2026-06-01" };
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(kpis()).toMatch(/Principal balance\$280,000from the bank feed · Jun 1, 2026/);
    expect(kpis()).toMatch(/Home equity\$620,000at \$900,000 home value · LTV 31\.1%/);
    expect(container.textContent).toMatch(/The projection starts from the bank feed's balance of Jun 1, 2026\./);
    expect(container.textContent).toMatch(/as of Jun 1, 2026/);                                              // the as-of badge follows
  });

  it("does not claim a margin loan when the household has none", () => {
    mortgage = FULL;
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(container.textContent).not.toMatch(/margin/);
    expect(container.textContent).toMatch(/At 3%, each dollar prepaid saves 3% a year in interest\./);
  });
});

// One precision for every rate in prose ("8.8%"; this sentence and Investments used to round it to "9%").
describe("Mortgage quotes the margin APR at the shared precision", () => {
  it("a whole rate stays whole and a fractional one keeps its decimal", () => {
    mortgage = FULL; marginAccount = "Brokerage X";
    expect(render(<Mortgage />, { wrapper: MemoryRouter }).container.textContent).toMatch(/the Brokerage X margin saves 11%\./);
    cleanup();
    marginApr = 0.0875;
    expect(render(<Mortgage />, { wrapper: MemoryRouter }).container.textContent).toMatch(/the Brokerage X margin saves 8\.8%\./);
  });

  it("a margin account with no stated rate is never compared, and the footnote names the key to add", () => {
    mortgage = FULL; marginAccount = "Brokerage X"; marginApr = null;
    const t = render(<Mortgage />, { wrapper: MemoryRouter }).container.textContent ?? "";
    expect(t).not.toMatch(FABRICATED);
    expect(t).not.toMatch(/margin saves/);
    expect(t).toMatch(/At 3%, each dollar prepaid saves 3% a year in interest\. To compare paying down the Brokerage X margin, state that loan's yearly rate \(household\.margin_apr in assumptions\.yaml\)\./);
  });
});

// The prepay card sets an extra amount against the loan's own rate and the rate the person sets: the amount starts unset
// (nobody stated one), and the rate at the household's middle return scenario, tagged "default" while that is unstated.
describe("Mortgage prepay vs invest: the arithmetic, at the person's rates", () => {
  const card = () => screen.getByTestId("card-prepay-vs-invest");
  const tiles = () => [...screen.getByTestId("prepay-tiles").children].map((t) => t.textContent);
  /** the number box of the field whose label starts `label` */
  const box = (c: HTMLElement, label: string) => [...c.querySelectorAll("label[title]")]
    .find((l) => (l.getAttribute("title") ?? "").startsWith(label))?.querySelector<HTMLInputElement>('input[type="number"]') ?? null;
  it("with no extra amount on the card or the page, every figure reads '—' and one line says which boxes set it", () => {
    mortgage = FULL;
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    const boxes = [...card().querySelectorAll<HTMLInputElement>('input[type="text"]')].map((i) => i.value);
    expect(boxes, "the extra amount's box is empty").toEqual([""]);
    expect(tiles()).toEqual(["Prepaying @ 3%—", "Investing @ 7%—", "Investing − prepaying—"]);
    expect(card().textContent).toContain("Set this card's Extra $/month, or the Extra monthly above the chart, to compare "
      + "prepaying that amount with investing it.");
    expect(container.textContent).not.toMatch(FABRICATED);
  });
  it("an amount set: what it grows to at each rate in whole dollars, and the difference in neutral words", () => {
    mortgage = FULL;
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    fireEvent.change(slider(container, "Extra $/month")!, { target: { value: "500" } });
    const [prepay, invest, diff] = tiles();
    expect(prepay).toMatch(/^Prepaying @ 3%\$\d{3},\d{3}\$500\/mo for ~\d+ yrs at the loan's rate, accrued monthly$/);
    expect(invest).toMatch(/^Investing @ 7%\$\d{3},\d{3}the same \$500\/mo at the rate set in this card$/);
    expect(diff).toMatch(/^Investing − prepaying\+\$[\d,]+at these rates, investing ends \$[\d,]+ higher than prepaying$/);
    // a rate set below the loan's: investing ends lower, said the same way
    fireEvent.change(slider(container, "Return if invested")!, { target: { value: "1" } });
    expect(tiles()[2]).toMatch(/^Investing − prepaying−\$[\d,]+at these rates, investing ends \$[\d,]+ lower than prepaying$/);
  });
  // The loan accrues a twelfth of its APR each month, so a prepaid dollar stops 3.0416% a year at a 3% APR: a return of
  // exactly that ends level, and one a hair either side of it puts the other side ahead.
  it("both sides on one footing: at the loan's effective rate they end the same, either side of it the other leads", () => {
    mortgage = FULL;
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    fireEvent.change(slider(container, "Extra $/month")!, { target: { value: "500" } });
    const effective = ((1 + 3 / 1200) ** 12 - 1) * 100;
    fireEvent.change(box(container, "Return if invested")!, { target: { value: String(effective) } });
    expect(tiles()[2]).toBe("Investing − prepaying$0at these rates, investing and prepaying end the same");
    fireEvent.change(box(container, "Return if invested")!, { target: { value: String(effective - 0.01) } });
    expect(tiles()[2]).toMatch(/investing ends \$[\d,]+ lower than prepaying$/);
    fireEvent.change(box(container, "Return if invested")!, { target: { value: String(effective + 0.01) } });
    expect(tiles()[2]).toMatch(/investing ends \$[\d,]+ higher than prepaying$/);
    // the nominal 3% itself is below the loan's own yearly cost: prepaying leads
    fireEvent.change(box(container, "Return if invested")!, { target: { value: "3" } });
    expect(tiles()[2]).toMatch(/lower than prepaying$/);
  });
  it("the complete demo household's loan: $500 a month for 25 years, investing at 7% ends $38,282 higher", () => {
    // the demo's loan terms: 419,452.29 owed at 6.125%, 2,746.40 a month (24 years 9 months left)
    mortgage = mortgageFactory({ balance: 419_452.29, balance_as_of: "2026-10-31", rate_pct: 6.125, monthly_payment: 2_746.4 });
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    fireEvent.change(slider(container, "Extra $/month")!, { target: { value: "500" } });
    expect(tiles()).toEqual([`Prepaying @ 6.125%${fmtUSD0(353_239)}$500/mo for ~25 yrs at the loan's rate, accrued monthly`,
      `Investing @ 7%${fmtUSD0(391_521)}the same $500/mo at the rate set in this card`,
      `Investing − prepaying+${fmtUSD0(38_282)}at these rates, investing ends ${fmtUSD0(38_282)} higher than prepaying`]);
  });
  it("the page's Extra monthly is the card's amount until the card's own box is set", () => {
    mortgage = FULL;
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    fireEvent.change(slider(container, "Extra monthly")!, { target: { value: "500" } });
    expect(tiles()[0]).toMatch(/\$500\/mo for/);
    expect(card().textContent).not.toMatch(/Set this card's Extra/);
    fireEvent.change(slider(container, "Extra $/month")!, { target: { value: "1000" } });
    expect(tiles()[0]).toMatch(/\$1,000\/mo for/);
    fireEvent.change(slider(container, "Extra monthly")!, { target: { value: "200" } });
    expect(tiles()[0], "the card's own amount, once set, stays").toMatch(/\$1,000\/mo for/);
  });
  it("the return the household states seeds the rate with no tag; an unstated one carries 'default'", () => {
    mortgage = FULL; returns = [4, 6, 8];
    render(<Mortgage />, { wrapper: MemoryRouter });
    expect(tiles()[1]).toMatch(/^Investing @ 6%/);
    expect(within(card()).queryByTestId("default-tag")).toBeNull();
    cleanup();
    stated = [];
    render(<Mortgage />, { wrapper: MemoryRouter });
    expect(within(card()).queryAllByTestId("default-tag").length).toBeGreaterThan(0);
  });
});

// The trajectory chart draws what there is: current pace whenever there is a projection, the extra-payments line only
// with an extra payment or a lump, the history only with points; its legend lists what is drawn and nothing else.
describe("Mortgage balance trajectory", () => {
  const lines = () => (charts.options.at(-1)?.series ?? []).map((s: { name: string }) => s.name);
  it("no extra payment: current pace and history, no extra-payments line", () => {
    mortgage = FULL;
    render(<Mortgage />, { wrapper: MemoryRouter });
    expect(lines()).toEqual(["History (bank feed)", "Current pace"]);
  });
  it("an extra monthly payment or a lump draws the extra-payments line beside current pace", () => {
    mortgage = FULL;
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    fireEvent.change(slider(container, "Extra monthly")!, { target: { value: "500" } });
    expect(lines()).toEqual(["History (bank feed)", "Current pace", "With extra payments"]);
    fireEvent.change(slider(container, "Extra monthly")!, { target: { value: "0" } });
    expect(lines()).toEqual(["History (bank feed)", "Current pace"]);
    fireEvent.change(slider(container, "One-time lump (now)")!, { target: { value: "10000" } });
    expect(lines()).toEqual(["History (bank feed)", "Current pace", "With extra payments"]);
  });
  it("each projection starts where the history ends: its first point is the balance it projects from, on that date", () => {
    // the feed's newest row is the balance the projection starts from, as on a household's own data
    mortgage = { ...FULL, schedule: [{ date: "2025-12-31", principal_balance: 305000 }, { date: "2026-05-18", principal_balance: 300000 }] };
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    fireEvent.change(slider(container, "Extra monthly")!, { target: { value: "500" } });
    const series = charts.options.at(-1).series as { name: string; data: [number, number][] }[];
    const history = series.find((s) => s.name === "History (bank feed)")!.data;
    const newest = history.reduce((a, b) => (b[0] > a[0] ? b : a));
    for (const name of ["Current pace", "With extra payments"]) {
      const first = series.find((s) => s.name === name)!.data[0];
      expect(first[1], name).toBe(Math.round(FULL.balance!));
      expect(first[0], name).toBeCloseTo(newest[0], 6);
    }
  });
  it("no history: no history series, so the legend does not list one", () => {
    mortgage = { ...FULL, schedule: [] };
    render(<Mortgage />, { wrapper: MemoryRouter });
    expect(lines()).toEqual(["Current pace"]);
  });
  it("nothing to draw (no history, no projection): no chart frame, the line says why", () => {
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(within(screen.getByTestId("card-mortgage-trajectory")).queryByTestId("chart")).toBeNull();
    expect(container.textContent).toMatch(/No balance history from the bank feed yet\. /);
  });
  it("a balance with no date carries no 'as of unknown' badge", () => {
    mortgage = { ...FULL, balance_as_of: null };
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(container.textContent).not.toMatch(/as of unknown/);
  });
});

// A term the loan terms leave out is the person's to set: the box keeps what is typed, a leading 0 included; and a stated
// payment is used as stated, to the cent.
describe("Mortgage terms the person sets", () => {
  it("an unstated rate's box keeps a typed 0, so 0.5 can be typed, and the projection runs at it", () => {
    mortgage = { ...FULL, rate_pct: null };
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    const apr = () => [...container.querySelectorAll("label[title]")]
      .find((l) => (l.getAttribute("title") ?? "").startsWith("Rate (APR)"))!.querySelector<HTMLInputElement>('input[type="number"]')!;
    expect(apr().value, "unset: empty").toBe("");
    fireEvent.change(apr(), { target: { value: "0" } });
    expect(apr().value).toBe("0");
    fireEvent.change(apr(), { target: { value: "0.5" } });
    expect(apr().value).toBe("0.5");
    expect(container.textContent).toMatch(/Projection assumes monthly P&I of \$1,400 at 0\.5%/);
    // emptied again: the rate is unstated again, not 0%
    fireEvent.change(apr(), { target: { value: "" } });
    expect(apr().value).toBe("");
    expect(container.textContent).toMatch(/No projection yet: it needs the rate/);
  });
  it("the payment prints as the projection uses it, cents included, in the footnote and the error box", () => {
    mortgage = mortgageFactory({ balance: 419_452.29, balance_as_of: "2026-10-31", rate_pct: 6.125, monthly_payment: 2_746.4 });
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(container.textContent).toMatch(/Projection assumes monthly P&I of \$2,746\.40 at 6\.125%/);
    cleanup();
    mortgage = { ...FULL, monthly_payment: 700.5 };                     // 3% of 300,000 is 750 a month
    render(<Mortgage />, { wrapper: MemoryRouter });
    expect(screen.getByTestId("mortgage-payment-error").textContent).toBe(`The monthly P&I of ${fmtUSD(700.5)} does not pay `
      + `the loan down: a month's interest at 3% on ${fmtUSD0(300_000)} is ${fmtUSD0(750)}.`);
  });
  it("a stated payment with cents is the payment used: the payoff and the interest follow it, and its box shows the cents", () => {
    mortgage = mortgageFactory({ balance: 419_452.29, balance_as_of: "2026-10-31", rate_pct: 6.125, monthly_payment: 2_746.4 });
    render(<Mortgage />, { wrapper: MemoryRouter });
    expect(kpis()).toMatch(/24y 9m · \$396,228 total interest/);
    const panel = screen.getByTestId("card-mortgage-assumptions");
    expect([...panel.querySelectorAll<HTMLInputElement>('input[type="text"]')].map((i) => i.value)).toContain("2,746.40");
  });
});

// A payment that does not pay the loan down is the projection's error: the box says it, and no hint beside it says
// anything else (the extra-payments tile's "set extra/lump below" gives way to the error's words).
describe("Mortgage with a payment that does not pay the loan down", () => {
  it("the error and every hint beside it say the same thing", () => {
    mortgage = { ...FULL, monthly_payment: 500 };                       // 3% of 300,000 is 750 a month
    render(<Mortgage />, { wrapper: MemoryRouter });
    const error = screen.getByTestId("mortgage-payment-error").textContent ?? "";
    expect(error).toBe(`The monthly P&I of ${fmtUSD0(500)} does not pay the loan down: a month's interest at 3% on `
      + `${fmtUSD0(300_000)} is ${fmtUSD0(750)}.`);
    expect(kpis()).not.toMatch(/set extra\/lump/);
    expect(kpis()).toMatch(/With extra payments—the monthly P&I does not pay the loan down/);
    expect(kpis()).toMatch(/Payoff at current pace—the monthly P&I does not pay the loan down/);
    expect(screen.queryByText(/^Error:/)).toBeNull();
  });
  it("with every term and no extra payment, the tile says to set one (control)", () => {
    mortgage = FULL;
    render(<Mortgage />, { wrapper: MemoryRouter });
    expect(kpis()).toMatch(/With extra payments—set an extra monthly payment or a lump below$/);
  });
});

// Planning -> House and Second house are one family of pages: a money box reads "900,000" in both, never the bare "900000"
describe("Mortgage inputs", () => {
  it("its money boxes group thousands like Second house's (the shared CurrencyInput), the rate box stays a number box", () => {
    mortgage = FULL;
    render(<Mortgage />, { wrapper: MemoryRouter });
    const panel = screen.getByTestId("card-mortgage-assumptions");
    const text = [...panel.querySelectorAll<HTMLInputElement>('input[type="text"]')].map((i) => i.value);
    expect(text).toEqual(["900,000", "1,400", "", ""]);   // home value, monthly P&I, extra and one-time lump (no placeholder digits)
    expect(panel.querySelectorAll('input[type="number"]')).toHaveLength(1);   // the APR
    expect(panel.textContent).not.toMatch(/\b900000\b/);
  });
});

// The home's value is the current snapshot's House-class rows: the slider starts there and is sized from it; without a
// House row there is no slider, one sentence says what to add, and equity and loan-to-value read "—".
describe("Mortgage home value", () => {
  const range = (s: HTMLInputElement | null) => s && [s.min, s.max, s.step, s.value].map(Number);

  it("with a House row the slider starts at its value and runs from half of it to twice it", () => {
    mortgage = FULL;
    rows = [house(400000), house(100000)];                                        // two House rows: one home value, 500,000
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(range(slider(container, "Home value"))).toEqual([250000, 1000000, 25000, 500000]);
    expect(sourceOf(container, "Home value")).toBe(
      "Home value — the home as entered by hand, in Settings → Manual assets (the House class) — slide for a what-if");
    expect(kpis()).toMatch(/Home equity\$200,000at \$500,000 home value · LTV 60\.0%/);   // 500,000 − 300,000
    expect(screen.queryByTestId("no-home-value")).toBeNull();
  });
  it("loan-to-value at the one precision Accounts → Liabilities' card prints it at", () => {
    mortgage = { ...FULL, balance: 280000 };
    rows = [house(500000)];
    render(<Mortgage />, { wrapper: MemoryRouter });
    expect(kpis()).toMatch(/at \$500,000 home value · LTV 56\.0%/);              // Liabilities.test: "(LTV 56.0%)"
  });
  it("a small home value gets a smaller, rounder step, so the range never holds fewer than ten steps", () => {
    mortgage = FULL;
    rows = [house(100000)];
    expect(range(slider(render(<Mortgage />, { wrapper: MemoryRouter }).container, "Home value"))).toEqual([50000, 200000, 10000, 100000]);
  });
  it("without a House row: no slider, the sentence in its place, and equity and loan-to-value read '—'", () => {
    mortgage = FULL;
    rows = [];
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(slider(container, "Home value")).toBeNull();
    expect(screen.getByTestId("no-home-value").textContent).toBe("Enter your home in Settings → Manual assets, in the "
      + "House class, to see equity and loan-to-value (or manual_entries: in accounts.yaml).");
    expect(screen.getByRole("link", { name: "Settings → Manual assets" }).getAttribute("href"),
      "it lands on the card").toBe("/settings?card=manual-assets");
    expect(kpis()).toMatch(/Home equity—no home in the current snapshot · LTV —/);
    const panel = screen.getByTestId("card-mortgage-assumptions");
    expect([...panel.querySelectorAll<HTMLInputElement>('input[type="text"]')].map((i) => i.value))
      .toEqual(["1,400", "", ""]);                                                // monthly P&I, extra and lump only
    expect(container.textContent).not.toMatch(FABRICATED);
    expect(container.textContent).not.toMatch(/Infinity|home value ·/);
  });
  it("a House row of 0 is a value: the slider still has a range, equity is what is owed and loan-to-value reads '—'", () => {
    mortgage = FULL;
    rows = [house(0)];
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(range(slider(container, "Home value"))).toEqual([0, 250000, 25000, 0]);
    expect(kpis()).toMatch(/Home equity−\$300,000at \$0 home value · LTV —/);
    expect(container.textContent).not.toMatch(/NaN|Infinity/);
  });
  it("a reload with another home value starts the what-if there, inside its new range; the other sliders keep theirs", () => {
    mortgage = FULL;
    const { container, rerender } = render(<Mortgage />, { wrapper: MemoryRouter });
    fireEvent.change(slider(container, "Extra monthly")!, { target: { value: "500" } });
    rows = [house(200000)];
    rerender(<Mortgage />);
    expect(range(slider(container, "Home value"))).toEqual([100000, 400000, 25000, 200000]);
    expect(kpis()).toMatch(/Home equity−\$100,000at \$200,000 home value/);              // 200,000 − 300,000
    expect(slider(container, "Extra monthly")?.value).toBe("500");
  });
});

// A rate the loan terms state as 0 is a rate (an interest-free loan): the payoff is the balance over the payments and the
// interest is 0. A rate they leave out (null) is unknown: nothing is derived from it and the page says what to add.
describe("Mortgage at a stated 0% rate", () => {
  it("an interest-free loan with a known payment shows a payoff year and no interest", () => {
    mortgage = { ...FULL, rate_pct: 0 };                                     // 300,000 at 1,400 a month: 215 payments
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(kpis()).toMatch(/Payoff at current pace204417y 11m · \$0 total interest/);
    expect(container.textContent).toMatch(/Projection assumes monthly P&I of \$1,400 at 0%/);
    expect(screen.getByText(/Prepay vs invest/)).toBeDefined();
    expect(screen.queryByTestId("mortgage-missing-terms")).toBeNull();
    expect(container.textContent).not.toMatch(/NaN|Infinity|undefined|null|No projection/);
  });
  it("a rate the loan terms leave out derives nothing, and the page says to add it", () => {
    mortgage = { ...FULL, rate_pct: null };
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(kpis()).toMatch(/Payoff at current pace—needs the rateWith extra payments/);
    expect(container.textContent).not.toMatch(/total interest|Projection assumes|Prepay vs invest/);
    expect(screen.getByTestId("mortgage-missing-terms").textContent).toBe(add("rate", "the interest cost and the payoff year"));
    expect(container.textContent).not.toMatch(FABRICATED);
  });
  it("a stated payment of 0 does not pay the loan down: no payoff, and no term is asked for", () => {
    mortgage = { ...FULL, rate_pct: 0, monthly_payment: 0 };
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(kpis()).toMatch(/Payoff at current pace—the monthly P&I does not pay the loan downWith extra payments/);
    expect(container.textContent).toMatch(/No projection: the monthly P&I does not pay the loan down/);
    expect(screen.getByTestId("no-prepay-compare").textContent).toBe(
      "The prepay-vs-invest comparison needs a monthly P&I that pays the loan down.");
    expect(screen.queryByTestId("mortgage-missing-terms")).toBeNull();
    expect(container.textContent).not.toMatch(/NaN|Infinity|undefined|null|total interest|No projection yet/);
  });
});

// The extra-payments card and the footnotes name what the projection lacks: a loan term in the sentence Accounts ->
// Liabilities uses for it, and the balance when the bank feed has not reported one
describe("Mortgage names the term the projection lacks", () => {
  it("the balance unknown: the balance is named, and no loan term is asked for", () => {
    mortgage = { ...FULL, balance: null, balance_as_of: null };
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(kpis()).toMatch(/With extra payments—needs the mortgage balance$/);
    expect(container.textContent).toMatch(/No projection yet: it needs the mortgage balance —/);
    expect(screen.getByTestId("no-prepay-compare").textContent).toBe("The prepay-vs-invest comparison needs the mortgage balance.");
    expect(screen.queryByTestId("mortgage-missing-terms")).toBeNull();
    expect(container.textContent).not.toMatch(/rate and monthly P&I|set extra\/lump/);
    expect(container.textContent).not.toMatch(FABRICATED);
  });
  it("the rate unknown: the rate is named and the page says to add it", () => {
    mortgage = { ...FULL, rate_pct: null };
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(kpis()).toMatch(/With extra payments—needs the rate$/);
    expect(container.textContent).toMatch(/No projection yet: it needs the rate —/);
    expect(screen.getByTestId("no-prepay-compare").textContent).toBe("The prepay-vs-invest comparison needs the rate.");
    expect(screen.getByTestId("mortgage-missing-terms").textContent).toBe(add("rate", "the interest cost and the payoff year"));
    expect(container.textContent).not.toMatch(/mortgage balance|set extra\/lump/);
  });
  it("the payment unknown: the monthly P&I is named and the page says to add it", () => {
    mortgage = { ...FULL, monthly_payment: null };
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(kpis()).toMatch(/With extra payments—needs the monthly P&I$/);
    expect(container.textContent).toMatch(/No projection yet: it needs the monthly P&I —/);
    expect(screen.getByTestId("no-prepay-compare").textContent).toBe("The prepay-vs-invest comparison needs the monthly P&I.");
    expect(screen.getByTestId("mortgage-missing-terms").textContent).toBe(add("monthly_payment", "the payoff year"));
    expect(container.textContent).not.toMatch(/mortgage balance|set extra\/lump/);
  });
});

// Privacy mode hides every amount on screen, an input's too: each money box shows the mask and each money slider runs
// over its steps, so no input carries an amount in its value (as shown, or as the attribute the page set) in any spelling
describe("Mortgage in privacy mode", () => {
  afterEach(() => setPrivate(false));
  /** every way a box could print an amount: unrounded, to one decimal, whole, grouped, grouped to the cent */
  const spellings = (...vs: number[]) => vs.flatMap((v) => [String(v), v.toFixed(1), String(Math.round(v)),
    Math.round(v).toLocaleString("en-US"), v.toLocaleString("en-US", { minimumFractionDigits: 2 })]);
  /** the inputs whose value holds one of `amounts`, each as its type and the label it sits in */
  const leaking = (c: HTMLElement, amounts: string[]) => [...c.querySelectorAll("input")]
    .filter((el) => [el.value, el.getAttribute("value") ?? ""].some((v) => amounts.some((s) => v.includes(s))))
    .map((el) => `${el.type} ${el.closest("label")?.getAttribute("title")?.split(" — ")[0] ?? "?"}`);
  /** the $ box of the field whose label starts `label` */
  const box = (c: HTMLElement, label: string) => [...c.querySelectorAll("label[title]")]
    .find((l) => (l.getAttribute("title") ?? "").startsWith(label))?.querySelector<HTMLInputElement>('input[type="text"]') ?? null;
  const type = (el: HTMLInputElement | null, text: string) => {
    if (!el) throw new Error("no such box");
    fireEvent.focus(el); fireEvent.change(el, { target: { value: text } }); fireEvent.blur(el);
  };

  it("no input's value holds the stated payment, the balance or the home value, however it is spelt", () => {
    mortgage = mortgageFactory({ ...FULL, monthly_payment: 1234.56, balance: 287_654.32 });
    rows = [house(654_321.09)];
    setPrivate(true);
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(container.querySelectorAll("input").length, "the page has its boxes and sliders").toBeGreaterThan(4);
    expect(box(container, "Monthly P&I")?.value).toBe(MASK);
    expect(box(container, "Home value")?.value).toBe(MASK);
    expect(leaking(container, spellings(1234.56, 287_654.32, 654_321.09)), "inputs whose value holds an amount").toEqual([]);
    // the control: with privacy off the payment's box and slider show it
    cleanup();
    setPrivate(false);
    const plain = render(<Mortgage />, { wrapper: MemoryRouter }).container;
    expect(leaking(plain, spellings(1234.56))).toEqual(["range Monthly P&I", "text Monthly P&I"]);
  });

  it("an extra monthly and a lump sum typed here, and the prepay card's amount that follows the extra, stay masked", () => {
    mortgage = FULL;
    setPrivate(true);
    const { container } = render(<Mortgage />, { wrapper: MemoryRouter });
    expect(screen.getByTestId("prepay-tiles").textContent, "no amount is set yet").toContain("—");
    type(box(container, "Extra monthly"), "345.67");
    type(box(container, "One-time lump"), "4321.09");
    expect(box(container, "Extra monthly")?.value).toBe(MASK);
    expect(box(container, "One-time lump")?.value).toBe(MASK);
    expect(box(screen.getByTestId("card-prepay-vs-invest"), "Extra $/month")?.value, "the card's box follows the extra").toBe(MASK);
    expect(screen.getByTestId("prepay-tiles").textContent, "the card has an amount to work with").not.toContain("—");
    expect(leaking(container, spellings(345.67, 4321.09)), "inputs whose value holds an amount").toEqual([]);
  });
});

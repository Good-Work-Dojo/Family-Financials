// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CODE_PILL } from "../components/NoData";
import type { BalancePoint, MortgageBlock } from "../data/types";
import { mortgageFactory } from "../lib/__fixtures__";
import { OpenRoutesProvider } from "../lib/modules";
import { accountColor, categoryColor } from "../lib/tokens";

// the stubbed chart records the option it is handed, so a test can read each line's colour
const charts = vi.hoisted(() => ({ options: [] as any[] }));
vi.mock("../components/EChart", () => ({
  default: (p: { option: unknown }) => { charts.options.push(p.option); return <div data-testid="chart" />; },
}));

// Liabilities reads balances + assumptions + the newest asset snapshot + the manifest's stamp only; a hand-built slice is enough.
type Doc = Record<string, unknown>;
let mortgage: MortgageBlock | null = null;
let balances: { series: Record<string, BalancePoint[]>; accounts: Record<string, Doc> } = { series: {}, accounts: {} };
let snapshot: { snapshot_date: string; by_class: Record<string, number>; rows: Doc[] } = { snapshot_date: "2026-10-03", by_class: {}, rows: [] };
let missing = new Set<string>();
let accounts: { id: string; name: string; kind?: string }[] = [];
let includeCards = false;
// the `margin: true` account's name; null: the household has none
let marginAccount: string | null = null;
vi.mock("../data/DataContext", () => ({
  useData: () => ({
    data: { accounts, balances, assets: { snapshots: [snapshot] },
            assumptions: { projections: {}, mortgage, net_worth: { include_credit_cards: includeCards },
              household: { margin_account: marginAccount } },
            manifest: { generated_at: "2026-10-04T08:00:00-05:00" } },
    missing,
  }),
}));

import Liabilities from "./Liabilities";

// the demo loan: its balance from the bank feed (the account's row in the current snapshot), its terms from accounts.yaml
const MORTGAGE = mortgageFactory({ balance: 280000, balance_as_of: "2026-10-03", rate_pct: 3, monthly_payment: 1400,
  monthly_escrow: 1000 });
const pt = (balance: number): BalancePoint =>
  ({ date: "2026-07-01", balance, available: 9000, limit: 10000, utilization: 0.1, source: "simplefin" });
/** a House-class row of the current snapshot: a home stated by hand, as an accounts.yaml manual entry states it */
const houseRow = (value: number): Doc => ({ account: "Sample Home", account_id: null, institution: "Self", owner: "Joint",
  asset_class: "House", value, liquid: false, as_of: "2026-01-31", as_of_assumed: false, needs_review: false, source: "stated",
  manual_id: "sample-home", carried_back: false });

beforeEach(() => {
  mortgage = MORTGAGE; includeCards = false; accounts = []; balances = { series: {}, accounts: {} }; missing = new Set<string>();
  marginAccount = null;
  snapshot = { snapshot_date: "2026-10-03", by_class: {}, rows: [] };
});
afterEach(cleanup);

const mortgageText = () => screen.getByTestId("card-mortgage-summary").textContent ?? "";

const add = (key: string, shows: string) => `Add ${key} under loan: on this account in accounts.yaml to see ${shows}.`;

describe("Liabilities mortgage card", () => {
  it("the rate and the payment are the loan terms', the payment carries its escrow, and the card says where each comes from", () => {
    snapshot = { ...snapshot, rows: [houseRow(900000)] };                  // the home present too: nothing to add
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(mortgageText()).toMatch(/Balance\$280,000/);
    expect(mortgageText()).toMatch(/Rate3%/);
    expect(mortgageText()).toMatch(/\$1,400 \+ \$1,000\/mo/);
    expect(mortgageText()).toMatch(/balance from the bank feed, as of Oct 3, 2026 · rate, payment and escrow from your loan terms/);
    expect(mortgageText()).not.toMatch(/est\.|estimated|fixed|Add /);
  });
  it("an escrow the loan terms do not state is not printed as '+ $0', and the card says what to add", () => {
    mortgage = { ...MORTGAGE, monthly_escrow: null };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(mortgageText()).toMatch(/P&I only/);
    expect(mortgageText()).not.toMatch(/\+ \$0|\$0\/mo/);
    expect(mortgageText(), "the escrow is not said to come from the loan terms").toMatch(/· rate and payment from your loan terms/);
    expect(screen.getByTestId("mortgage-missing-terms").textContent).toBe(
      add("monthly_escrow", "the monthly cost with taxes and insurance"));
  });
  it("a balance the bank feed has not reported reads as no data, never 0", () => {
    mortgage = { ...MORTGAGE, balance: null, balance_as_of: null };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(screen.getByTestId("mortgage-no-balance").textContent).toBe("No balance from the bank feed yet.");
    expect(mortgageText()).not.toMatch(/\$0|Payoff|Balance\$/);
    expect(screen.getByTestId("kpi-mortgage-balance").textContent).toBe("Mortgage—No balance from the bank feed yet");
  });
  it("without a mortgage account the card and the tile say so, and nothing is unknown", () => {
    mortgage = null;
    accounts = [{ id: "car-loan", name: "Car Loan", kind: "loan" }];
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(mortgageText()).toContain("No mortgage. Mark the loan account that is the mortgage and its balance and terms "
      + "appear here after the next ingest (mortgage: true on the account in accounts.yaml).");
    expect([...screen.getByTestId("card-mortgage-summary").querySelectorAll("code")].map((c) => c.textContent))
      .toEqual(["mortgage: true", "accounts.yaml"]);
    expect(screen.getByTestId("kpi-mortgage-balance").textContent).toBe("Mortgage—No mortgage account");
    expect(screen.getByTestId("kpi-total-debt").textContent).not.toMatch(/mortgage balance not available/);
  });
  it("a zero balance reads as paid off, not as missing data", () => {
    mortgage = { ...MORTGAGE, balance: 0 };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(screen.getByTestId("mortgage-paid-off").textContent).toBe("Paid off: the bank feed shows nothing owed, as of Oct 3, 2026.");
    expect(screen.getByTestId("kpi-mortgage-balance").textContent).toBe("Mortgage$0Paid off · from the bank feed · Oct 3, 2026");
    expect(mortgageText()).not.toMatch(/no balance|not available|Add /);
    expect(screen.getByTestId("kpi-total-debt").textContent).not.toMatch(/mortgage balance not available/);
  });
  // nothing stated: the balance is known, the rate and the payment are not. One sentence per missing term says what to
  // add and what it shows; nothing stands in for a missing term
  it("a balance with no loan terms prints the balance and one sentence per missing term", () => {
    mortgage = { ...MORTGAGE, rate_pct: null, monthly_payment: null, monthly_escrow: null };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(mortgageText()).toMatch(/Balance\$280,000/);
    expect(screen.getByTestId("mortgage-summary-partial").textContent).toBe(
      "balance from the bank feed, as of Oct 3, 2026"
      + add("rate", "the interest cost") + add("monthly_payment", "the payoff year, with the rate")
      + add("monthly_escrow", "the monthly cost with taxes and insurance"));
    expect(mortgageText()).not.toMatch(/Payoff|Rate|Interest cost|equity|\$0/);
  });
  it("a balance and a rate with no payment shows the rate and its interest cost, and asks only for the payment", () => {
    mortgage = { ...MORTGAGE, monthly_payment: null };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(mortgageText()).toMatch(/Balance\$280,000/);
    expect(mortgageText()).toMatch(/Rate3%/);
    expect(mortgageText()).toMatch(/Interest cost\$23\.01\/day/);              // 280,000 × 3% / 365
    expect(screen.getByTestId("mortgage-summary-partial").textContent).toBe(
      "balance from the bank feed, as of Oct 3, 2026 · the rest from your loan terms" + add("monthly_payment", "the payoff year"));
    expect(mortgageText()).not.toMatch(/Payoff|equity/);
  });
  it("a balance and a payment with no rate asks only for the rate", () => {
    mortgage = { ...MORTGAGE, rate_pct: null };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(mortgageText()).toMatch(/Balance\$280,000/);
    expect(mortgageText()).toMatch(/P&I \+ escrow\$1,400 \+ \$1,000\/mo/);
    expect(screen.getByTestId("mortgage-missing-terms").textContent).toBe(add("rate", "the interest cost and the payoff year"));
    expect(mortgageText()).not.toMatch(/Rate|Interest cost|equity/);
  });
  it("home equity and loan-to-value are the current snapshot's House rows less the balance, named by their source", () => {
    snapshot = { ...snapshot, rows: [houseRow(400000), houseRow(100000)] };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    // 500,000 − 280,000; 280,000 / 500,000, at Planning → House's one precision (lib/lifeSummary fmtLoanToValue)
    expect(mortgageText()).toMatch(/Home equity\$220,000 \(LTV 56\.0%\)/);
    expect(mortgageText()).toMatch(/home value as entered by hand, in Settings → Manual assets/);
    expect(screen.getByRole("link", { name: "Settings → Manual assets" }).getAttribute("href")).toBe("/settings?card=manual-assets");
    expect(screen.queryByTestId("no-home-value")).toBeNull();
  });
  it("without a House row equity reads '—' and one sentence says what to add", () => {
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(mortgageText()).toMatch(/Home equity—Interest cost/);
    expect(mortgageText()).not.toMatch(/LTV|home value as|\$0\b|NaN|null|Infinity/);
    expect(screen.getByTestId("no-home-value").textContent).toBe("Enter your home in Settings → Manual assets, in the "
      + "House class, to see equity and loan-to-value (or manual_entries: in accounts.yaml).");
    expect(within(screen.getByTestId("no-home-value")).getByRole("link", { name: "Settings → Manual assets" })
      .getAttribute("href")).toBe("/settings?card=manual-assets");
  });
  it("a payment that does not cover a month's interest says so instead of a payoff year", () => {
    mortgage = { ...MORTGAGE, monthly_payment: 500 };                        // 3% of 280,000 is 700 a month
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(mortgageText()).toMatch(/Rate3%/);
    expect(mortgageText()).toMatch(/does not pay the balance down, so the payoff year is not worked out\./);
    expect(mortgageText()).not.toMatch(/Payoff at current pace|Add /);
  });
  it("a payment that does not cover a month's interest still names every other term the loan terms lack", () => {
    mortgage = { ...MORTGAGE, monthly_payment: 500, monthly_escrow: null };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(mortgageText()).toMatch(/does not pay the balance down, so the payoff year is not worked out\./);
    expect(screen.getByTestId("mortgage-missing-terms").textContent).toBe(
      add("monthly_escrow", "the monthly cost with taxes and insurance"));
  });
});

describe("Liabilities credit cards", () => {
  it("an empty balance history shows an explanation, not a green '$0 / 0 cards' tile", () => {
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    // with no credit-card account the card, its title and both tiles say the one thing: there is no credit card yet
    const standIn = screen.getByTestId("card-no-credit-cards");
    expect(standIn.textContent).toMatch(/^Credit cardsNo credit card yet\. Add a credit-card account/);
    expect(standIn.textContent).not.toMatch(/history/);
    // the debt row answers for the mortgage and margin; its two card tiles say in a few words there is no card, with no
    // figure (the card below says where card balances come from)
    expect(screen.queryByTestId("kpis-liabilities")).not.toBeNull();
    expect(screen.getByTestId("kpi-card-debt").textContent).toBe("Credit cards—No credit card yet");
    expect(screen.getByTestId("kpi-available-credit").textContent).toBe("Available credit—No credit card yet");
    // the one card stands in for both card charts
    expect(screen.queryByTestId("card-card-balances")).toBeNull();
    expect(screen.queryByTestId("card-utilization")).toBeNull();
    expect(screen.getByTestId("card-mortgage-summary")).toBeDefined();
    expect(screen.getByTestId("kpis-liabilities").className).toContain("lg:grid-cols-5");
  });
  it("history without any credit-card account is the same explained empty state", () => {
    balances = {
      series: { chk: [pt(500)] },
      accounts: { chk: { name: "Checking", institution: "B", owner: "J", kind: "checking", asset_class: "Cash" } },
    };
    accounts = [{ id: "chk", name: "Checking", kind: "checking" }];
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(screen.getByTestId("kpi-card-debt").textContent).toBe("Credit cards—No credit card yet");
    // with no credit-card account the card below the tiles says what to add, in plain words, the keys as a hint after
    // them: a card's balance comes from the bank feed alone, so the account is connected to it
    const standIn = screen.getByTestId("card-no-credit-cards");
    expect(within(standIn).getByTestId("empty-state").textContent).toBe("No credit card yet. Add a credit-card account "
      + "connected to the bank feed and its balance, available credit and utilization appear here after the next refresh "
      + "(kind: credit-card and simplefin_id: in accounts.yaml).");
    expect([...standIn.querySelectorAll("code")].map((c) => [c.textContent, c.className]))
      .toEqual([["kind: credit-card", CODE_PILL], ["simplefin_id:", CODE_PILL], ["accounts.yaml", CODE_PILL]]);
  });
  it("credit-card accounts with no balance history: the card below says where card balances come from", () => {
    accounts = [{ id: "c1", name: "Card One", kind: "credit-card" }];
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(screen.getByTestId("card-no-credit-cards").textContent).toContain("No credit-card balance history yet. A credit "
      + "card's balance comes from the bank feed alone (its statement files state none): once a card is connected to it, its "
      + "balance, available credit and utilization appear here after the next refresh that reports them (simplefin_id: on "
      + "the card in accounts.yaml).");
    expect(screen.getByTestId("card-no-credit-cards").textContent).not.toMatch(/SimpleFIN/);
    expect(screen.getByTestId("kpi-card-debt").textContent).toBe("Credit cards—No credit-card balance yet");
    expect(screen.getByTestId("kpi-available-credit").textContent).toBe("Available credit—No credit-card balance history yet");
  });
  it("a card shows debt, headroom and its charts; the KPI row steps to two columns before five", () => {
    balances = {
      series: { c1: [pt(-250)] },
      accounts: { c1: { name: "Card One", institution: "B", owner: "J", kind: "credit-card", asset_class: "Other Debt" } },
    };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(screen.getByTestId("kpi-card-debt").textContent).toMatch(/\$250/);
    expect(screen.getByTestId("kpi-available-credit").textContent).toMatch(/\$9,000/);
    expect(screen.getByTestId("card-card-balances")).toBeDefined();
    expect(screen.getByTestId("card-utilization")).toBeDefined();
    expect(screen.queryByTestId("card-no-credit-cards")).toBeNull();
    const grid = screen.getByTestId("kpis-liabilities").className;
    expect(grid).toMatch(/\bsm:grid-cols-2\b/);
    expect(grid).toMatch(/\blg:grid-cols-5\b/);
  });
});

// Accounts -> Liabilities answers "what do we owe": the total and its parts, the mortgage first, every part from its one source.
describe("Liabilities answers what we owe", () => {
  const cardAcct = { c1: { name: "Card One", institution: "B", owner: "J", kind: "credit-card", asset_class: "Other Debt" } };
  const text = (id: string) => screen.getByTestId(id).textContent ?? "";
  const mortgageRow = (over: Doc = {}) => ({ asset_class: "Mortgage", account_id: "demo_mortgage_0707", value: -280000, liquid: false,
    as_of: "2026-10-03", as_of_assumed: false, ...over });
  const marginRow = { asset_class: "Margin", account_id: "demo_brokerage_0505", value: -10000, liquid: false, as_of: "2026-10-03",
    as_of_assumed: false };

  it("the KPI row is Total debt, Mortgage, Cards and Margin (plus available credit), the total being their sum", () => {
    balances = { series: { c1: [pt(-250)] }, accounts: cardAcct };
    snapshot = { ...snapshot, by_class: { Mortgage: -280000, Margin: -10000 }, rows: [mortgageRow(), marginRow] };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(text("kpi-total-debt")).toContain("$290,250");                     // 280,000 + 250 + 10,000
    expect(text("kpi-total-debt")).toContain("Mortgage + credit cards + margin = net-worth debt + credit-card balances");
    expect(text("kpi-mortgage-balance")).toContain("$280,000");
    expect(text("kpi-card-debt")).toContain("$250");
    expect(text("kpi-margin-debt")).toContain("$10,000");
    expect(screen.getByTestId("kpi-margin-debt").getAttribute("href")).toBe("#/accounts/investments");
  });
  it("the mortgage card comes before the credit-card charts", () => {
    balances = { series: { c1: [pt(-250)] }, accounts: cardAcct };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    const order = screen.getByTestId("card-mortgage-summary").compareDocumentPosition(screen.getByTestId("card-card-balances"));
    expect(order & Node.DOCUMENT_POSITION_FOLLOWING, "the card charts follow the mortgage card").toBeTruthy();
    const kpis = screen.getByTestId("kpis-liabilities").compareDocumentPosition(screen.getByTestId("card-mortgage-summary"));
    expect(kpis & Node.DOCUMENT_POSITION_FOLLOWING, "the KPI row is first").toBeTruthy();
  });
  // an account name is a link to its page, wherever it is printed
  it("the mortgage card links the loan account the balance and terms belong to, by its name, to /accounts/:id", () => {
    accounts = [{ id: "demo_loan_0000", name: "Sample Home Loan" }];
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    const link = within(screen.getByTestId("card-mortgage-summary")).getByRole("link", { name: "Sample Home Loan" });
    expect(link.getAttribute("href")).toBe("/accounts/demo_loan_0000");
    expect(link.className, "a resting link style, not only a hover").toMatch(/\btext-blue-\d+\b[\s\S]*\bunderline\b/);
  });
  it("and links nothing when the registry does not know the account", () => {
    accounts = [];                                                           // an account the registry has no page for
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(within(screen.getByTestId("card-mortgage-summary")).queryByRole("link", { name: /Home Loan/ })).toBeNull();
  });
  it("the block's balance is the one printed, and both cards say it is the bank feed's", () => {
    snapshot = { ...snapshot, by_class: { Mortgage: -280000 }, rows: [mortgageRow()] };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(text("kpi-mortgage-balance")).toBe("Mortgage$280,000From the bank feed · Oct 3, 2026");
    const card = text("card-mortgage-summary");
    expect(card).toMatch(/Balance\$280,000/);
    expect(card).toMatch(/from the bank feed, as of Oct 3, 2026/);
    expect(card).not.toMatch(/newer than|statement/);
  });
  it("without card balances the total leaves cards out and says so (never a $0 card line)", () => {
    snapshot = { ...snapshot, by_class: { Mortgage: -280000, Margin: -10000 }, rows: [mortgageRow(), marginRow] };
    accounts = [{ id: "demo_card_0000", name: "Card One", kind: "credit-card" }];        // a card the history has no balance for
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(text("kpi-total-debt")).toContain("$290,000");
    expect(text("kpi-total-debt")).toMatch(/Mortgage \+ margin · credit-card balances not available/);
    missing = new Set(["balances_timeseries.json"]);
    cleanup();
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(text("kpi-total-debt")).toMatch(/credit-card balances not available/);
    // the two card tiles read the history that did not load: left out, as the card charts are, and the row has three
    expect(screen.queryByTestId("kpi-card-debt")).toBeNull();
    expect(screen.queryByTestId("kpi-available-credit")).toBeNull();
    expect(screen.getByTestId("kpis-liabilities").className).toContain("lg:grid-cols-3");
  });
  it("a household with no credit-card account: the total says nothing about card balances", () => {
    snapshot = { ...snapshot, by_class: { Mortgage: -280000 }, rows: [mortgageRow()] };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(text("kpi-total-debt")).toBe("Total debt$280,000Mortgage");
    expect(text("kpi-total-debt")).not.toMatch(/card/);
  });
  it("a margin account with nothing owed reads as no margin balance, still linking Investments", () => {
    marginAccount = "Broker";
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(text("kpi-margin-debt")).toBe("Margin$0No margin balance · Investments →");
    expect(screen.getByTestId("kpi-margin-debt").getAttribute("href")).toBe("#/accounts/investments");
  });
  it("no margin account: the margin tile is \"—\", not a $0, and says there is none", () => {
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(text("kpi-margin-debt")).toBe("Margin—No margin account · Investments →");
    expect(screen.getByTestId("kpi-margin-debt").querySelector(".text-red-400")).toBeNull();
  });
  it("one balance per card: the chart draws the point and says the line comes with the second", () => {
    accounts = [{ id: "demo_card_0000", name: "Card One", kind: "credit-card" }];
    balances = { series: { demo_card_0000: [pt(-250)] },
      accounts: { demo_card_0000: { name: "Card One", institution: "Sample Bank", owner: "Alex", kind: "credit-card", asset_class: "Other Debt" } } };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    for (const id of ["card-card-balances", "card-utilization"]) {
      expect(within(screen.getByTestId(id)).getByTestId("chart"), id).toBeDefined();
      expect(within(screen.getByTestId(id)).getByTestId("not-enough-history").textContent, id)
        .toBe("Not enough history yet — one balance so far for each account; the line draws once a second one is recorded.");
    }
  });
});

// A card's colour is its position in the household's account list, in both card charts: never the order the charts ask
// for colours (the utilisation chart, built first, would hand the card that alone has a limit the first colour)
describe("Liabilities' card charts colour each card one way", () => {
  const cards = { demo_card_0001: { name: "Card One", institution: "Sample Bank", owner: "Alex", kind: "credit-card", asset_class: "Other Debt" },
    demo_card_0002: { name: "Card Two", institution: "Sample Bank", owner: "Sam", kind: "credit-card", asset_class: "Other Debt" } };
  const noLimit = (balance: number): BalancePoint => ({ ...pt(balance), available: null, limit: null, utilization: null });
  /** each chart's line colours by card name: the balance chart first, then the utilisation chart */
  const colours = () => charts.options.map((o) => Object.fromEntries((o.series as { name: string; color: string }[])
    .map((s) => [s.name, s.color])));
  it("Card Two, the only one with a limit, has the same colour in both charts, and each card the colour of its place in the list", () => {
    // a colour asked for elsewhere first, in the other order, moves nothing here
    categoryColor("demo_card_0002");
    categoryColor("demo_card_0001");
    accounts = [{ id: "demo_checking_0101", name: "Checking", kind: "checking" },
      { id: "demo_card_0001", name: "Card One", kind: "credit-card" }, { id: "demo_card_0002", name: "Card Two", kind: "credit-card" }];
    balances = { series: { demo_card_0001: [noLimit(-400)], demo_card_0002: [pt(-250)] }, accounts: cards };
    charts.options = [];
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    const ids = accounts.map((a) => a.id);
    const [balanceChart, utilChart] = colours();
    expect(balanceChart).toEqual({ "Card One": accountColor("demo_card_0001", ids), "Card Two": accountColor("demo_card_0002", ids) });
    expect(utilChart).toEqual({ "Card Two": accountColor("demo_card_0002", ids) });
    expect(balanceChart["Card One"]).not.toBe(balanceChart["Card Two"]);
  });
  it("both charts keep zero on their axes, however far above it the lines run: zero is the goal for a debt and a utilization", () => {
    accounts = [{ id: "demo_card_0001", name: "Card One", kind: "credit-card" }, { id: "demo_card_0002", name: "Card Two", kind: "credit-card" }];
    // owed 1,300 to 1,520 and utilization about 13% to 15%: a level the axis would otherwise leave zero for
    const at = (date: string, balance: number): BalancePoint => ({ ...pt(balance), date });
    balances = { series: { demo_card_0001: [at("2026-07-01", -1_300), at("2026-08-01", -1_520)],
      demo_card_0002: [at("2026-07-01", -1_310), at("2026-08-01", -1_480)] }, accounts: cards };
    charts.options = [];
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    const [balanceChart, utilChart] = charts.options as { yAxis: { scale?: boolean } }[];
    expect([balanceChart.yAxis.scale, utilChart.yAxis.scale]).toEqual([undefined, undefined]);
  });
  it("the colours are the account list's, from render to render", () => {
    accounts = [{ id: "demo_card_0001", name: "Card One", kind: "credit-card" }, { id: "demo_card_0002", name: "Card Two", kind: "credit-card" }];
    balances = { series: { demo_card_0001: [noLimit(-400)], demo_card_0002: [pt(-250)] }, accounts: cards };
    const seen: Record<string, string>[] = [];
    for (let run = 0; run < 2; run++) {
      charts.options = [];
      render(<MemoryRouter><Liabilities /></MemoryRouter>);
      seen.push(colours()[0]);
      cleanup();
    }
    expect(seen[0]).toEqual(seen[1]);
    expect(seen[0]).toEqual({ "Card One": accountColor("demo_card_0001", ["demo_card_0001", "demo_card_0002"]),
      "Card Two": accountColor("demo_card_0002", ["demo_card_0001", "demo_card_0002"]) });
  });
});

// A card in credit owes nothing: beside a card owing 1,000, one in credit by 150 leaves the "Credit cards" tile at 1,000
// whether cards are counted in net worth (the snapshot's rows) or left out (the balance history), and the tile says it
describe("Liabilities' Credit cards tile with a card in credit", () => {
  const cards = { demo_card_0001: { name: "Card One", institution: "Sample Bank", owner: "Alex", kind: "credit-card", asset_class: "Other Debt" },
    demo_card_0002: { name: "Card Two", institution: "Sample Bank", owner: "Sam", kind: "credit-card", asset_class: "Other Debt" } };
  const cardRow = (account_id: string, value: number): Doc => ({ asset_class: "Other Debt", account_id, value, liquid: false,
    as_of: "2026-10-03", as_of_assumed: false, source: "simplefin_balance" });
  for (const counted of [true, false]) {
    it(`reads 1,000 and "2 credit cards · 1 in credit" with cards ${counted ? "counted in" : "left out of"} net worth`, () => {
      includeCards = counted;
      accounts = [{ id: "demo_card_0001", name: "Card One", kind: "credit-card" }, { id: "demo_card_0002", name: "Card Two", kind: "credit-card" }];
      balances = { series: { demo_card_0001: [pt(-1000)], demo_card_0002: [pt(150)] }, accounts: cards };
      snapshot = { ...snapshot, by_class: counted ? { "Other Debt": -850 } : {},
        rows: counted ? [cardRow("demo_card_0001", -1000), cardRow("demo_card_0002", 150)] : [] };
      render(<MemoryRouter><Liabilities /></MemoryRouter>);
      expect(screen.getByTestId("kpi-card-debt").textContent).toBe("Credit cards$1,0002 credit cards · 1 in credit");
    });
  }
});

// With include_credit_cards the snapshot holds a row per card account: the total reads them (once), the history is only the detail.
describe("Liabilities with cards in the snapshot", () => {
  const text = (id: string) => screen.getByTestId(id).textContent ?? "";
  const cardId = "demo_card_0000";
  const cardAcct = { [cardId]: { name: "Card One", institution: "Sample Bank", owner: "Alex", kind: "credit-card", asset_class: "Other Debt" } };
  const row = (over: Doc = {}) => ({ asset_class: "Other Debt", account_id: cardId, value: -900, liquid: false,
    as_of: "2026-10-03", as_of_assumed: false, source: "simplefin_balance", ...over });
  const mortgageRow = { asset_class: "Mortgage", account_id: "demo_mortgage_0000", value: -280000, liquid: false,
    as_of: "2026-10-03", as_of_assumed: false };
  const setup = (rows: Doc[], byClass: Record<string, number>) => {
    includeCards = true;
    accounts = [{ id: cardId, name: "Card One", kind: "credit-card" }, { id: "demo_loan_0000", name: "Loan", kind: "loan" }];
    // the history's newest point differs from the snapshot row, so a double count or the wrong source shows
    balances = { series: { [cardId]: [pt(-250)] }, accounts: cardAcct };
    snapshot = { ...snapshot, by_class: byClass, rows };
  };
  it("the total counts card debt once, from the snapshot row; the card tile prints the same number; the detail keeps the history", () => {
    setup([mortgageRow, row(), row({ account_id: "demo_loan_0000", value: -5000 })], { Mortgage: -280000, "Other Debt": -5900 });
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(text("kpi-total-debt")).toContain("$285,900");                     // 280,000 + 900 cards + 5,000 loan
    expect(text("kpi-total-debt")).toContain("Mortgage + credit cards + other debt");
    expect(text("kpi-total-debt")).not.toContain("net-worth debt + credit-card balances");
    expect(text("kpi-card-debt")).toContain("$900");
    expect(text("kpi-available-credit")).toContain("$9,000");                  // headroom still reads the history
    expect(screen.getByTestId("card-card-balances")).toBeDefined();
  });
  it("a card with a snapshot row but no balance series is still a card: the total counts it", () => {
    setup([mortgageRow, row({ asset_class: "Margin" })], { Mortgage: -280000, Margin: -900 });
    balances = { series: {}, accounts: {} };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(text("kpi-total-debt")).toContain("$280,900");
    expect(text("kpi-card-debt")).toContain("$900");
    // identified by account: not also margin, which this household (no margin account) has none of
    expect(text("kpi-margin-debt")).toBe("Margin—No margin account · Investments →");
  });
  it("with no balance history the card tile counts the cards of the snapshot rows it reads, and available credit (the history's) is not shown", () => {
    setup([mortgageRow, row(), row({ account_id: "demo_card_0001", value: -100 })], { Mortgage: -280000, "Other Debt": -1000 });
    accounts = [...accounts, { id: "demo_card_0001", name: "Card Two", kind: "credit-card" }];
    balances = { series: {}, accounts: {} };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(text("kpi-card-debt")).toContain("$1,000");
    expect(text("kpi-card-debt")).toContain("2 credit cards");                // the two rows it sums, never "0 credit cards"
    // no history: nothing to read headroom from, and the tile says so
    expect(text("kpi-available-credit")).toBe("Available credit—No credit-card balance history yet");
    expect(screen.getByTestId("kpis-liabilities").className).toContain("lg:grid-cols-5");
    expect(screen.getByTestId("card-no-credit-cards")).toBeDefined();
  });
  it("card accounts with no snapshot row leave cards out of the total and name them as missing, whatever the history holds", () => {
    // the history holds the card (available credit reads it), the newest snapshot has no row for it yet: the card tile
    // says that, never "no card balance" beside a headroom figure from that very card's balance
    setup([mortgageRow], { Mortgage: -280000 });
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(text("kpi-total-debt")).toContain("$280,000");
    expect(text("kpi-total-debt")).toMatch(/credit-card balances not available/);
    expect(text("kpi-card-debt"), "no figure, never $0").toBe("Credit cards—Not in the newest snapshot yet");
    expect(text("kpi-available-credit"), "the headroom reads the history, which holds the card").toContain("$9,000");
  });
  it("cards in net worth with neither a snapshot row nor a history: the card tile says no card balance yet", () => {
    setup([mortgageRow], { Mortgage: -280000 });
    balances = { series: {}, accounts: {} };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(text("kpi-card-debt")).toBe("Credit cards—No credit-card balance yet");
    expect(text("kpi-available-credit")).toBe("Available credit—No credit-card balance history yet");
  });
});

describe("Liabilities wording and tap targets", () => {
  const card = (name: string) => ({ name, institution: "B", owner: "J", kind: "credit-card", asset_class: "Other Debt" });
  it("a feed with limits but no available-credit figure derives it as limit − owed, and says so", () => {
    balances = { series: { c1: [{ ...pt(-250), available: null }] }, accounts: { c1: card("Card One") } };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    const kpi = screen.getByTestId("kpi-available-credit").textContent ?? "";
    expect(kpi).toMatch(/\$9,750/);                                    // 10,000 limit − 250 owed
    expect(kpi).toMatch(/limit − balance/);
    expect(kpi).not.toMatch(/No credit limit known|issuer/);
    expect(screen.getByTestId("card-utilization")).toBeDefined();
  });
  it("utilisation is the one figure Balances prints for the card: owed over the limit, at one decimal", () => {
    // owes 851.88 of an 8,000 limit, 7,148.12 available: 10.6%, the same on both pages
    balances = { series: { c1: [{ ...pt(-851.88), available: 7148.12, limit: 8000, utilization: 0.1065 }] },
      accounts: { c1: card("Card One") } };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(screen.getByTestId("kpi-available-credit").textContent).toContain("10.6% utilized");
  });
  it("a card with available credit and no limit: headroom, but no utilisation figure, never 0%", () => {
    balances = { series: { c1: [{ ...pt(-250), available: 9000, limit: null, utilization: null }] }, accounts: { c1: card("Card One") } };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    const kpi = screen.getByTestId("kpi-available-credit").textContent ?? "";
    expect(kpi).toContain("$9,000");
    expect(kpi).not.toMatch(/utilized|0\.0%/);
  });
  it("some cards without a limit: the figure covers the others and says 'N of M credit cards'", () => {
    balances = {
      series: { c1: [{ ...pt(-250), available: null }], c2: [{ ...pt(-400), available: null, limit: null, utilization: null }] },
      accounts: { c1: card("Card One"), c2: card("Card Two") },
    };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    const kpi = screen.getByTestId("kpi-available-credit").textContent ?? "";
    expect(kpi).toMatch(/\$9,750/);
    expect(kpi).toMatch(/1 of 2 credit cards/);
  });
  it("no card has a limit or an available figure: '—' and 'No credit limit known', never '$0' or 'not reported by issuer'", () => {
    balances = { series: { c1: [{ ...pt(-250), available: null, limit: null, utilization: null }] }, accounts: { c1: card("Card One") } };
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    const kpi = screen.getByTestId("kpi-available-credit").textContent ?? "";
    expect(kpi).toContain("—");
    expect(kpi).toContain("No credit limit known");
    expect(kpi).not.toMatch(/\$0|issuer/);
    // the utilization chart has nothing to draw: its card says why and what to add, the key as a hint, with no chart
    const util = screen.getByTestId("card-utilization");
    expect(util.textContent).toBe("Credit utilization"
      + "No credit limit is known for any credit card, so utilization cannot be shown. Add each card's limit and it appears "
      + "here after the next ingest (credit_limit: on the card in accounts.yaml).");
    expect([...util.querySelectorAll("code")].every((c) => c.className === CODE_PILL), "the hints are code pills").toBe(true);
    expect(within(util).queryByTestId("chart")).toBeNull();
    expect(screen.getByTestId("card-card-balances")).toBeDefined();
  });
  it("the what-ifs link is a 40px tap target on a phone", () => {
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    const link = screen.getByRole("link", { name: /what-ifs/ });
    expect(link.className.split(/\s+/)).toContain("min-h-[40px]");
    expect(link.getAttribute("href")).toBe("/planning/home");
  });
  it("the card's ways into Planning → House are links while that page shows, and say nothing of it while it does not", () => {
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    const on = screen.getByTestId("card-mortgage-summary");
    expect(within(on).getByRole("link", { name: "Planning → House" }).getAttribute("href")).toBe("/planning/home");
    expect(on.textContent).toContain("at 3%, each dollar prepaid saves 3% a year in interest — the extra-payment what-if is in "
      + "Planning → House · balance");
    cleanup();
    const shown = (path: string) => !path.startsWith("/planning/home");
    render(<MemoryRouter><OpenRoutesProvider value={shown}><Liabilities /></OpenRoutesProvider></MemoryRouter>);
    const card = screen.getByTestId("card-mortgage-summary");
    expect(within(card).queryByRole("link", { name: /what-ifs|Planning → House/ }), "no way into a page that is off").toBeNull();
    expect(card.textContent, "no dead arrow in the header").not.toContain("what-ifs");
    expect(card.textContent, "and no sentence about the page").not.toContain("Planning → House");
    expect(card.textContent, "the rest of the line stays").toContain("saves 3% a year in interest · balance");
  });
  it("the card names the loan's own rate and what a prepaid dollar saves at it, and no market instrument", () => {
    render(<MemoryRouter><Liabilities /></MemoryRouter>);
    expect(mortgageText()).not.toMatch(/treasur|earns|beats|market/i);
  });
  it("with no mortgage account the card says so, with nothing in its header", () => {
    mortgage = null;
    const shown = (path: string) => !path.startsWith("/planning/home");
    render(<MemoryRouter><OpenRoutesProvider value={shown}><Liabilities /></OpenRoutesProvider></MemoryRouter>);
    expect(screen.getByTestId("card-mortgage-summary").textContent, "no loan account: nothing to mark")
      .toBe("MortgageNo loan account, so no mortgage is tracked.");
  });
});

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CODE_PILL } from "../components/NoData";
import type { AssetRow, BalancePoint, MortgageBlock } from "../data/types";
import { acctFactory, appDataFactory, belowZeroSnapshot, mortgageFactory, oneFullMonthData, snapFactory } from "../lib/__fixtures__";

const pt = (date: string, balance: number): BalancePoint => ({ date, balance, available: null, limit: null, utilization: null, source: "simplefin" });
const meta = (name: string, kind: string, asset_class = "Cash") => ({ name, institution: "Bank", owner: "Joint", kind, asset_class });
const baseData = appDataFactory(snapFactory({}))({
  balances: {
    series: { chk: [pt("2026-07-01", 900), pt("2026-07-20", 1234)], one: [pt("2026-07-20", 5678)] },   // one: a single point, no history to open
    accounts: { chk: meta("Checking A", "checking"), one: meta("Savings B", "checking") },
  },
});
let data = baseData;
vi.mock("../data/DataContext", () => ({ useData: () => ({ data, missing: new Set<string>() }) }));
vi.mock("../components/EChart", () => ({ default: () => <div data-testid="chart" /> }));

import Balances from "./Balances";

afterEach(() => { cleanup(); data = baseData; });
const mount = () => render(<MemoryRouter><Balances /></MemoryRouter>);
/** the phone cards (the md:hidden half of the card; jsdom shows both halves) */
const phone = () => within(screen.getByTestId("card-current-balances").querySelector("div.md\\:hidden") as HTMLElement);

describe("Balances account links", () => {
  it("each account name links its page, in the desktop table and on the phone cards", () => {
    mount();
    const table = within(screen.getByTestId("card-current-balances"));
    const links = table.getAllByRole("link", { name: "Checking A" });
    expect(links).toHaveLength(2);
    expect(links.every((a) => a.getAttribute("href") === "/accounts/chk")).toBe(true);
    expect(table.getAllByRole("link", { name: "Savings B" })[0].getAttribute("href")).toBe("/accounts/one");
  });
  it("the name link has a resting link style (colour and underline), not only a hover", () => {
    mount();
    for (const a of screen.getAllByRole("link", { name: "Checking A" })) {
      const styled = [a, ...a.querySelectorAll("*")].map((e) => e.className).join(" ");
      expect(styled).toMatch(/\btext-blue-\d+\b/);
      expect(styled).toMatch(/\bunderline\b/);
    }
  });
  it("clicking the name in the desktop table does not toggle the row's history", () => {
    mount();
    const row = within(screen.getByTestId("card-current-balances")).getAllByRole("link", { name: "Checking A" })[0].closest("tr")!;
    fireEvent.click(within(row).getByRole("link"));
    expect(row.getAttribute("aria-expanded")).toBe("false");
  });
});

describe("Balances phone cards", () => {
  it("the amount is plain text a screen reader reads, never inside the toggle button whose label would replace it", () => {
    mount();
    const toggle = phone().getByRole("button", { name: "Show balance history for Checking A" });
    expect(toggle.textContent).toBe("›");                                        // chevron only
    expect(toggle.querySelector("a")).toBeNull();
    const card = toggle.parentElement!.parentElement!;
    expect(within(card).getByText("$1,234")).toBeDefined();                      // the amount sits beside the controls, in text
    expect(card.contains(toggle) && !toggle.contains(within(card).getByText("$1,234"))).toBe(true);
  });
  it("the toggle states itself (aria-expanded, aria-controls, Show/Hide) and opens the history", () => {
    mount();
    const toggle = phone().getByRole("button", { name: "Show balance history for Checking A" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    const open = phone().getByRole("button", { name: "Hide balance history for Checking A" });
    expect(open.getAttribute("aria-expanded")).toBe("true");
    expect(document.getElementById(open.getAttribute("aria-controls")!)).not.toBeNull();
  });
  it("an account with a single point has no toggle (nothing to open) but still shows its amount and link", () => {
    mount();
    expect(phone().queryByRole("button", { name: /balance history for Savings B/ })).toBeNull();
    expect(phone().getByRole("link", { name: "Savings B" })).toBeDefined();
    expect(phone().getByText("$5,678")).toBeDefined();
  });
  it("no link is inside a button anywhere in the card", () => {
    mount();
    expect(screen.getByTestId("card-current-balances").querySelectorAll("button a, a button").length).toBe(0);
  });
});

// "Cash on hand" and Planning's "Liquid cash today" / runway used to be two different sums (the bank feed's cash
// accounts vs the snapshot's liquid Cash rows) with no note. One definition now - lib/lifeSummary liquidCash - and the
// sub-line says when a liquid cash row has no bank feed (a balance entered by hand).
describe("Balances 'Cash on hand' is the app's one cash figure", () => {
  const cashRow = (over: Partial<AssetRow>): AssetRow => ({
    account: "Acct", account_id: "chk", institution: "Bank", owner: "Joint", asset_class: "Cash", value: 1234, liquid: true,
    as_of: "2026-07-20", as_of_assumed: false, needs_review: false, source: "bank_balance", manual_id: null,
    carried_back: false, ...over });
  const withRows = (rows: AssetRow[]) => {
    data = { ...baseData, assets: { ...baseData.assets, snapshots: [snapFactory({})({ rows })] } };
  };
  const kpi = () => screen.getByTestId("kpis-balances").textContent ?? "";
  it("reads the snapshot's liquid Cash rows - the figure Planning's runway divides - not the feed accounts' sum", () => {
    // the feed accounts sum to $6,912; the snapshot's liquid cash is $1,234 + $5,678 + $500 entered by hand = $7,412
    withRows([cashRow({}), cashRow({ account_id: "one", value: 5678 }),
      cashRow({ account_id: null, value: 500, source: "stated" }),
      cashRow({ account_id: null, value: 900, liquid: false, source: "stated" })]);       // escrow-style: Cash but not liquid
    mount();
    expect(kpi()).toContain("Cash on hand$7,412");
    expect(kpi()).not.toContain("$6,912");
  });
  it("says how many liquid cash rows were entered by hand, with no bank feed, so the table below is not expected to add up to it", () => {
    withRows([cashRow({}), cashRow({ account_id: "one", value: 5678 }), cashRow({ account_id: null, value: 500, source: "stated" })]);
    mount();
    expect(kpi()).toContain("3 cash accounts · 1 entered by hand (no bank feed), so not in the table below");
  });
  it("counts every liquid cash row entered by hand", () => {
    withRows([cashRow({}), cashRow({ account_id: null, value: 500, source: "stated" }),
      cashRow({ account_id: null, value: 70, source: "stated" })]);
    mount();
    expect(kpi()).toContain("3 cash accounts · 2 entered by hand (no bank feed), so not in the table below");
  });
  it("an account's row the balance history does not hold has no balance history yet - never 'no bank feed'", () => {
    withRows([cashRow({}), cashRow({ account_id: "sav", value: 300 }), cashRow({ account_id: "sav2", value: 200 }),
      cashRow({ account_id: null, value: 70, source: "stated" })]);
    mount();
    expect(kpi()).toContain("4 cash accounts · 1 entered by hand (no bank feed), so not in the table below"
      + " · 2 have no balance history yet, so not in the table below");
    cleanup();
    withRows([cashRow({}), cashRow({ account_id: "sav", value: 300 })]);
    mount();
    expect(kpi()).toContain("2 cash accounts · 1 has no balance history yet, so not in the table below");
    expect(kpi()).not.toMatch(/no bank feed/);
  });
  it("adds no such note when every liquid cash row is a feed account", () => {
    withRows([cashRow({}), cashRow({ account_id: "one", value: 5678 })]);
    mount();
    expect(kpi()).toContain("2 cash accounts");
    expect(kpi()).not.toMatch(/no bank feed/);
  });
});

// The balance sheet's debt is the DEBT IN NET WORTH (mortgage + margin; a snapshot holds no card balance) and says so in the
// words Home's tile uses; Liabilities has the card balances. Its Cash class row (escrow included) is told apart from "Cash on hand".
describe("Balances' balance sheet explains its two easily confused figures", () => {
  const row = (over: Partial<AssetRow>): AssetRow => ({
    account: "Acct", account_id: "chk", institution: "Bank", owner: "Joint", asset_class: "Cash", value: 1234, liquid: true,
    as_of: "2026-07-20", as_of_assumed: false, needs_review: false, source: "bank_balance", manual_id: null,
    carried_back: false, ...over });
  /** the snapshot of `by_class`: the rows given, and a row for whatever of each class they leave (the sheet reads rows) */
  const sheet = (rows: AssetRow[], by_class: Record<string, number>, mortgage?: Partial<MortgageBlock>) => {
    const rest = Object.entries(by_class).map(([asset_class, v]) => [asset_class,
      v - rows.filter((r) => r.asset_class === asset_class).reduce((s, r) => s + r.value, 0)] as const)
      .filter(([, v]) => v !== 0)
      .map(([asset_class, value]) => row({ account: asset_class, account_id: asset_class, asset_class, value,
        liquid: asset_class === "Cash" }));
    data = { ...baseData,
      assets: { ...baseData.assets, snapshots: [snapFactory({})({ rows: [...rows, ...rest], by_class })] },
      assumptions: { ...baseData.assumptions, mortgage: mortgage ? mortgageFactory(mortgage) : baseData.assumptions.mortgage } };
  };
  const liab = () => screen.getByTestId("card-balance-sheet-liabilities").textContent ?? "";
  it("Total liabilities says it is the debt in net worth, mortgage + margin, cards not included, with a link to Liabilities", () => {
    sheet([], { Mortgage: -283_026, Margin: -8_289, Cash: 50_000 });
    data = { ...data, accounts: [acctFactory()({ id: "demo_card_0000", kind: "credit-card", asset_class: "Other Debt" })] };
    mount();
    expect(screen.getByTestId("liabilities-note").textContent).toContain("Total liabilities = the debt in net worth: mortgage + margin · credit cards not included.");
    expect(screen.getByTestId("liabilities-note").querySelector("a")?.getAttribute("href")).toBe("#/accounts/liabilities");
  });
  it("a household with no credit-card account is told nothing about cards left out", () => {
    sheet([], { Mortgage: -283_026, Margin: -8_289, Cash: 50_000 });
    mount();
    expect(screen.getByTestId("liabilities-note").textContent).toContain("Total liabilities = the debt in net worth: mortgage + margin.");
    expect(screen.getByTestId("liabilities-note").textContent).not.toMatch(/card/);
  });
  it("a sheet with no asset above zero says so on the assets side, and totals nothing to no $0", () => {
    sheet([], { "Other Debt": -1_500 });
    mount();
    const assets = screen.getByTestId("card-balance-sheet-assets").textContent ?? "";
    expect(assets).toContain("No asset above zero in this snapshot.");
    expect(assets).toContain("Total assets—");
    expect(assets).not.toMatch(/\$0(?!\d)/);
  });
  it("with cards in the snapshot the line holding only them is called credit cards, and the sheet still sums to the total", () => {
    sheet([row({ account_id: "demo_card_0000", asset_class: "Other Debt", value: -900, liquid: false, source: "simplefin_balance" })],
      { Mortgage: -283_026, "Other Debt": -900, Cash: 50_000 });
    data = { ...data, accounts: [acctFactory()({ id: "demo_card_0000", kind: "credit-card", asset_class: "Other Debt" })],
      assumptions: { ...data.assumptions, net_worth: { include_credit_cards: true } } };
    mount();
    expect(screen.getByTestId("liabilities-note").textContent).toContain("the debt in net worth: mortgage + credit cards.");
    expect(liab()).toContain("Credit cards−$900");
    expect(liab()).not.toMatch(/Other [Dd]ebt/);
  });
  it("a debt class that holds a card and a loan says the cards are in it; one with no card is plain other debt", () => {
    const cardRow = row({ account_id: "demo_card_0000", asset_class: "Other Debt", value: -900, liquid: false, source: "simplefin_balance" });
    const loanRow = row({ account: "Family Loan", account_id: "demo_loan_0001", asset_class: "Other Debt", value: -5_000, liquid: false,
      source: "simplefin_balance" });
    const card = acctFactory()({ id: "demo_card_0000", kind: "credit-card", asset_class: "Other Debt" });
    sheet([cardRow, loanRow], { Mortgage: -283_026, "Other Debt": -5_900, Cash: 50_000 });
    data = { ...data, accounts: [card], assumptions: { ...data.assumptions, net_worth: { include_credit_cards: true } } };
    mount();
    expect(liab()).toContain("Other debt, credit cards included−$5,900");
    expect(screen.getByTestId("liabilities-note").textContent).toContain("mortgage + other debt, credit cards included.");
    cleanup();
    sheet([loanRow], { Mortgage: -283_026, "Other Debt": -5_000, Cash: 50_000 });
    data = { ...data, accounts: [card], assumptions: { ...data.assumptions, net_worth: { include_credit_cards: true } } };
    mount();
    expect(liab()).toContain("Other debt−$5,000");
    expect(liab()).not.toMatch(/Credit cards−|credit cards included/);
  });
  it("the Assets card tells the Cash class (escrow included) from 'Cash on hand' when a Cash row is not liquid", () => {
    sheet([row({}), row({ account_id: null, value: 9_000, liquid: false, source: "stated" })], { Cash: 10_234 });
    mount();
    const assets = screen.getByTestId("card-balance-sheet-assets").textContent ?? "";
    expect(assets).toContain("Cash here is the whole Cash class, including a row that cannot be spent (an escrow balance, say);");
    expect(assets).toContain('"Cash on hand" above counts liquid cash only');
  });
  it("and says nothing about escrow when every Cash row is liquid", () => {
    sheet([row({})], { Cash: 1_234 });
    mount();
    expect(screen.getByTestId("card-balance-sheet-assets").textContent).not.toMatch(/whole Cash class/);
  });
  it("the Mortgage line is the one mortgage balance, the bank feed's, and the note claims no second figure", () => {
    const mort = row({ asset_class: "Mortgage", account_id: "demo_loan_0000", value: -250_000, liquid: false, as_of: "2030-10-03" });
    sheet([mort], { Mortgage: -250_000, Cash: 50_000 }, { balance: 250_000, balance_as_of: "2030-10-03" });
    mount();
    expect(liab()).toContain("\u2212$250,000");
    expect(liab()).toContain("net = assets \u2212 liabilities, by sign: every row above zero is an asset, every row below zero a liability");
    expect(liab()).not.toMatch(/mortgage line|newer than/);
  });
});

// One sign convention: a debt on the balance sheet reads "\u2212$X" in red, the U+2212 minus every money formatter prints -
// never "($X)", which the same tab's other numbers do not use.
describe("Balances' balance sheet prints a debt with the one minus, not parentheses", () => {
  const row = (over: Partial<AssetRow>): AssetRow => ({
    account: "Acct", account_id: "chk", institution: "Bank", owner: "Joint", asset_class: "Mortgage", value: -283_026, liquid: false,
    as_of: "2026-07-20", as_of_assumed: false, needs_review: false, source: "bank_balance", manual_id: null,
    carried_back: false, ...over });
  it("each liability line and the total are \u2212$ amounts", () => {
    data = { ...baseData, assets: { ...baseData.assets,
      snapshots: [snapFactory({})({ rows: [row({}), row({ account: "Margin", account_id: "brk", asset_class: "Margin", value: -8_289 })],
        by_class: { Mortgage: -283_026, Margin: -8_289 } })] } };
    mount();
    const text = screen.getByTestId("card-balance-sheet-liabilities").textContent ?? "";
    expect(text).toContain("Mortgage\u2212$283,026");
    expect(text).toContain("Margin\u2212$8,289");
    expect(text).toContain("Total liabilities\u2212$291,315");
    expect(text, "no parenthesized money").not.toMatch(/\(\$|\(\u2212\$/);
  });
});

// One date convention: ISO only in dense registers (the transaction register) and CSV; a card, a sentence or a short
// table that has a phone twin reads "Jul 20, 2026" (fmtDate), and the header's as-of is the shared AsOfBadge, so one screen does not
// mix "as of 2026-07-20" text with "as of Jul 20, 2026" chips - nor does the desktop table disagree with the phone cards.
describe("Balances' as-of dates read like the rest of the cards", () => {
  it("the Accounts-tracked sub-line and the current-balances badge say Jul 20, 2026 - no ISO date in prose", () => {
    mount();
    expect(screen.getByTestId("kpis-balances").textContent).toContain("as of Jul 20, 2026");
    expect(screen.getByTestId("kpis-balances").textContent).not.toMatch(/\d{4}-\d{2}-\d{2}/);
    expect(screen.getByTestId("card-current-balances").textContent).toContain("as of Jul 20, 2026");   // the header's AsOfBadge
    expect(screen.getByTestId("card-current-balances").textContent).not.toMatch(/\d{4}-\d{2}-\d{2}/);
  });
});

// No figure the data cannot support: a balance history of one point is drawn as that point and says so; a share of a
// net worth of 0 is not drawn; a sheet with no line on one side says so instead of totalling nothing to $0.
describe("Balances on a thin household", () => {
  it("one balance per cash account: the chart draws the point and the line under it says a second one draws the line", () => {
    data = oneFullMonthData();
    mount();
    const card = screen.getByTestId("card-cash-balances");
    expect(within(card).getByTestId("chart")).toBeDefined();
    expect(within(card).getByTestId("not-enough-history").textContent)
      .toBe("Not enough history yet — one balance so far for each account; the line draws once a second one is recorded.");
  });
  it("two balances or more: no such line", () => {
    mount();
    expect(within(screen.getByTestId("card-cash-balances")).queryByTestId("not-enough-history")).toBeNull();
  });
  it("no asset above zero: the liquidity card draws no share and says why", () => {
    data = { ...baseData, assets: { snapshots: [snapFactory({})({ total: -1_500, assets_total: 0, liabilities_total: -1_500,
      liquid_total: 0, by_class: { "Other Debt": -1_500 } })] } };
    mount();
    const card = screen.getByTestId("card-liquidity");
    expect([...card.querySelectorAll("[style]")].map((e) => e.getAttribute("style")).join(" "), "no bar of a zero total")
      .not.toMatch(/width/);
    expect(card.textContent).toContain("With no asset above zero there is no share to draw.");
    expect(card.textContent).not.toMatch(/NaN|Infinity|∞/);
  });
  it("a net worth of 0 still splits its assets: the bar is of assets, never of net worth", () => {
    data = { ...baseData, assets: { snapshots: [snapFactory({})({ total: 0, assets_total: 1_500, liabilities_total: -1_500,
      liquid_total: 1_500, by_class: { Cash: 1_500, "Other Debt": -1_500 } })] } };
    mount();
    const widths = [...screen.getByTestId("card-liquidity").querySelectorAll("[style]")].map((e) => e.getAttribute("style"));
    expect(widths).toEqual(["width: 100%;", "width: 0%;"]);
    expect(screen.getByTestId("liquidity-split").textContent).toBe("Liquid $1,500Illiquid $0Debts $1,500");
  });
  it("a value entered by hand in the liquidity list links to where it is entered; an account's row does not", () => {
    const row = (pin: Partial<AssetRow>): AssetRow => ({ account: "Home", account_id: null, institution: "", owner: "Joint",
      asset_class: "House", value: 500_000, liquid: false, as_of: "2026-06-01", as_of_assumed: false, needs_review: false,
      source: "stated", manual_id: "family-home", carried_back: false, ...pin });
    data = { ...baseData, assets: { snapshots: [snapFactory({})({ rows: [row({}),
      row({ account: "Retirement Fund", account_id: "ret", source: "simplefin_balance", manual_id: null, asset_class: "Retirement" })] })] } };
    mount();
    const card = within(screen.getByTestId("card-liquidity"));
    expect(card.getByRole("link", { name: "Home" }).getAttribute("href")).toBe("/settings?card=manual-assets");
    expect(card.queryByRole("link", { name: "Retirement Fund" })).toBeNull();
  });
  it("a sheet with no liability says so, and totals nothing to no $0", () => {
    data = oneFullMonthData();
    mount();
    const liab = screen.getByTestId("card-balance-sheet-liabilities").textContent ?? "";
    expect(liab).toContain("No debt in this snapshot.");
    expect(liab).toContain("Total liabilities—");
    expect(liab, "nothing totalled to $0").not.toMatch(/\$0(?!\d)/);
    expect(liab).toContain("Net worth$8,000");
    // no credit-card account: the link to Liabilities names no card balances
    expect(screen.getByTestId("liabilities-note").querySelector("a")?.textContent).toBe("Liabilities →");
  });
});

// Illiquid is the asset rows that are not liquid, never net worth less the liquid part: a debt is its own line, so a
// household whose debts are larger than its illiquid assets reads a positive Illiquid beside its Debts.
describe("Balances' Illiquid assets and the liquidity card", () => {
  const r = (over: Partial<AssetRow>): AssetRow => ({ account: "Acct", account_id: "chk", institution: "Bank", owner: "Joint",
    asset_class: "Cash", value: 100_000, liquid: true, as_of: "2026-07-20", as_of_assumed: false, needs_review: false,
    source: "bank_balance", manual_id: null, carried_back: false, ...over });
  // 100,000 of cash, a car stated in March worth 21,000 and a 400,000 mortgage: a net worth of -279,000
  const rows = [r({}), r({ account: "Sedan", account_id: null, institution: "Example Motors", asset_class: "Car", value: 21_000,
    liquid: false, as_of: "2026-03-26", source: "stated", manual_id: "sedan" }),
  r({ account: "Home Mortgage", account_id: "loan", asset_class: "Mortgage", value: -400_000, liquid: false, source: "simplefin_balance" })];
  const deep = () => {
    data = { ...baseData, assets: { snapshots: [snapFactory({})({ snapshot_date: "2026-07-20", total: -279_000,
      assets_total: 121_000, liabilities_total: -400_000, liquid_total: 100_000,
      by_class: { Cash: 100_000, Car: 21_000, Mortgage: -400_000 }, rows })] } };
  };
  it("debts larger than the illiquid assets: Illiquid is still the car, and the debts are their own line", () => {
    deep();
    mount();
    expect(screen.getByTestId("kpi-illiquid").textContent)
      .toBe("Illiquid assets$21,000car — not spendable soon; debts are not taken off");
    expect(screen.getByTestId("liquidity-split").textContent).toBe("Liquid $100,000Illiquid $21,000Debts $400,000");
    // the bar splits the 121,000 of assets
    const widths = [...screen.getByTestId("card-liquidity").querySelectorAll("[style]")].map((e) => e.getAttribute("style"));
    const shares = widths.map((w) => Number(/width: ([\d.]+)%/.exec(w ?? "")?.[1]));
    expect(shares).toHaveLength(2);
    expect(shares[0]).toBeCloseTo(100_000 / 1_210);
    expect(shares[1]).toBeCloseTo(21_000 / 1_210);
  });
  it("the list under it is the rows Illiquid sums, each with its holder and the date of a value older than the snapshot", () => {
    deep();
    mount();
    const list = screen.getByTestId("liquidity-rows").textContent ?? "";
    expect(list).toBe("Sedan (Car · Example Motors) as of Mar 26, 2026$21,000");
  });
  it("an asset entered in the app names no institution: its row says it was entered by hand, never a blank", () => {
    data = { ...baseData, assets: { snapshots: [snapFactory({})({ snapshot_date: "2026-07-20", rows: [r({ account: "Home",
      account_id: null, institution: "", asset_class: "House", value: 640_000, liquid: false, as_of: "2026-07-20",
      source: "stated", manual_id: "family-home" })] })] } };
    mount();
    expect(screen.getByTestId("liquidity-rows").textContent).toBe("Home (House · entered by hand)$640,000");
  });
  it("past eight rows the rest are one line with their count and sum, so the list still adds up to Illiquid", () => {
    const many = Array.from({ length: 10 }, (_, i) => r({ account: `Fund ${i}`, account_id: `f${i}`, asset_class: "Retirement",
      value: 1_000 * (10 - i), liquid: false, source: "simplefin_balance" }));
    data = { ...baseData, assets: { snapshots: [snapFactory({})({ snapshot_date: "2026-07-20", total: 55_000, assets_total: 55_000,
      liabilities_total: 0, liquid_total: 0, rows: many })] } };
    mount();
    const items = [...screen.getByTestId("liquidity-rows").querySelectorAll("li")].map((li) => li.textContent);
    expect(items).toHaveLength(9);
    expect(items[8]).toBe("2 more assets$3,000");
    expect(screen.getByTestId("kpi-illiquid").textContent).toContain("$55,000");
  });
  it("no illiquid row: Illiquid is a true $0 and says so, never a class the snapshot does not hold", () => {
    data = oneFullMonthData();
    mount();
    expect(screen.getByTestId("kpi-illiquid").textContent).toBe("Illiquid assets$0No illiquid asset in this snapshot");
  });
  // the card prints the debts too: while the snapshot misses any account - an asset's or a debt's - its figures are of the
  // balances reported so far, and it says so, linking to Data
  it("a snapshot that misses an account, the mortgage alone included: the card says so; one that misses none says nothing", () => {
    const accounts = [acctFactory()({ id: "loan", name: "Sample Mortgage", kind: "loan" }),
      acctFactory()({ id: "brk", name: "Sample Brokerage", kind: "brokerage" })];
    const missing = (names: string[]) => {
      deep();
      data = { ...data, accounts, assets: { snapshots: [{ ...data.assets.snapshots[0], missing: names }] } };
    };
    const line = () => within(screen.getByTestId("card-liquidity")).getByText(/^Figures/).textContent;
    missing(["Sample Mortgage"]);
    mount();
    expect(line()).toBe("Figures of the balances reported so far — 1 account not yet reported");
    expect(within(screen.getByTestId("card-liquidity")).getByTestId("not-yet-reported").getAttribute("href")).toBe("/data?card=caveats");
    cleanup();
    missing(["Sample Brokerage", "Sample Mortgage"]);
    mount();
    expect(line()).toBe("Figures of the balances reported so far — 2 accounts not yet reported");
    cleanup();
    deep();
    mount();
    expect(within(screen.getByTestId("card-liquidity")).queryByTestId("not-yet-reported")).toBeNull();
  });
});

// One balance sheet, by sign: an overdrawn account and a hand-stated value below zero are debts on every card
describe("Balances by sign: an overdrawn account and a hand-stated value below zero", () => {
  const withBelowZero = () => {
    data = { ...baseData, assets: { snapshots: [belowZeroSnapshot()] },
      assumptions: { ...baseData.assumptions, mortgage: mortgageFactory({ account_id: "loan", balance: 250_000, balance_as_of: "2026-06-01" }) } };
  };
  it("every card reads the same assets and the same debts", () => {
    withBelowZero();
    mount();
    const assets = screen.getByTestId("card-balance-sheet-assets").textContent ?? "";
    expect(assets).toContain("Total assets$415,000");
    expect(assets).toContain("Cash$5,000");
    const liab = screen.getByTestId("card-balance-sheet-liabilities").textContent ?? "";
    expect(liab).toContain("Joint Checking (overdrawn)−$300");
    expect(liab).toContain("Boat (below zero)−$2,000");
    expect(liab).toContain("Total liabilities−$252,300");
    expect(liab).toContain("Net worth$162,700");
    expect(screen.getByTestId("liabilities-note").textContent)
      .toContain("Total liabilities = the debt in net worth: mortgage + Joint Checking (overdrawn) + Boat (below zero).");
    expect(screen.getByTestId("liquidity-split").textContent).toBe("Liquid $15,000Illiquid $400,000Debts $252,300");
    expect(screen.getByTestId("kpi-illiquid").textContent).toContain("$400,000");
  });
  it("cash on hand stays the cash accounts' net, and says one of them is below zero", () => {
    withBelowZero();
    mount();
    expect(screen.getByTestId("kpis-balances").textContent).toContain("Cash on hand$4,7002 cash accounts · net of one below zero");
  });
});

// A card in credit (the issuer owes the household 150) beside one owing 1,000: an asset line of its own, named for its
// account, and the cards' subtotal is what they owe - never 850, the one netted against the other
describe("Balances by sign: a card in credit", () => {
  const card = (account: string, account_id: string, value: number): AssetRow => ({ account, account_id,
    institution: "Bank", owner: "Joint", asset_class: "Other Debt", value, liquid: false, as_of: "2026-07-20",
    as_of_assumed: false, needs_review: false, source: "simplefin_balance", manual_id: null, carried_back: false });
  const withCards = (includeCards: boolean) => {
    const cash = { ...card("Checking A", "chk", 1_234), asset_class: "Cash", liquid: true };
    const rows = includeCards ? [cash, card("Card One", "c1", -1_000), card("Card Two", "c2", 150)] : [cash];
    data = { ...baseData,
      accounts: [acctFactory()({ id: "chk" }), acctFactory()({ id: "c1", name: "Card One", kind: "credit-card", asset_class: "Other Debt" }),
        acctFactory()({ id: "c2", name: "Card Two", kind: "credit-card", asset_class: "Other Debt" })],
      assets: { snapshots: [snapFactory({})({ snapshot_date: "2026-07-20", rows, total: includeCards ? 384 : 1_234,
        assets_total: includeCards ? 1_384 : 1_234, liabilities_total: includeCards ? -1_000 : 0, liquid_total: 1_234 })] },
      assumptions: { ...baseData.assumptions, net_worth: { include_credit_cards: includeCards } },
      balances: { series: { ...baseData.balances.series, c1: [pt("2026-07-20", -1_000)], c2: [pt("2026-07-20", 150)] },
        accounts: { ...baseData.balances.accounts, c1: meta("Card One", "credit-card", "Other Debt"),
          c2: meta("Card Two", "credit-card", "Other Debt") } } };
  };
  for (const includeCards of [true, false]) {
    it(`the cards' subtotal is what they owe, with the one in credit named (cards ${includeCards ? "in" : "left out of"} net worth)`, () => {
      withCards(includeCards);
      mount();
      const table = screen.getByTestId("card-current-balances");
      const head = [...table.querySelectorAll("tbody tr:first-child")].find((tr) => tr.textContent?.startsWith("Credit cards"));
      expect(head?.textContent).toBe("Credit cards2 accounts · 1 in credit, not in the subtotal−$1,000");
      expect(phone().getByText("1 in credit, not in the subtotal").parentElement?.parentElement?.textContent)
        .toBe("Credit cards1 in credit, not in the subtotal−$1,000");
    });
  }
  it("with cards in net worth the Assets card lists the card in credit under its own name, never its debt class's", () => {
    withCards(true);
    mount();
    const assets = screen.getByTestId("card-balance-sheet-assets").textContent ?? "";
    expect(assets).toContain("Card Two (in credit)$150");
    expect(assets).not.toContain("Other Debt");
    expect(assets).toContain("Total assets$1,384");
    // the class's line holds the card that owes, and is called for it
    expect(screen.getByTestId("card-balance-sheet-liabilities").textContent).toContain("Credit cards−$1,000");
  });
  it("the Illiquid tile and the Liquidity list name the card in credit as the Assets card does, never by its debt class", () => {
    withCards(true);
    mount();
    expect(screen.getByTestId("kpi-illiquid").textContent)
      .toBe("Illiquid assets$150Card Two (in credit) — not spendable soon; debts are not taken off");
    const list = screen.getByTestId("liquidity-rows").textContent ?? "";
    expect(list).toBe("Card Two (in credit) · Bank$150");
    expect(list).not.toContain("Other Debt");
  });
});

// A household that counts cards in net worth and has no credit-card account has no card balance to be missing
describe("Balances' liabilities note with cards in net worth and no credit-card account", () => {
  it("says there is no credit card, never that card balances are not available", () => {
    data = { ...oneFullMonthData(), assumptions: { ...oneFullMonthData().assumptions, net_worth: { include_credit_cards: true } } };
    mount();
    const note = screen.getByTestId("liabilities-note").textContent ?? "";
    expect(note).toContain("Total liabilities = the debt in net worth: no debt in this snapshot · no credit card.");
    expect(note).not.toMatch(/not available/);
  });
});

describe("Balances with no balance history", () => {
  it("with no bank feed it says in words what setting one up takes, the config key in the shared code pill and no command", () => {
    data = { ...baseData, balances: { series: {}, accounts: {} },
      manifest: { ...baseData.manifest, setup: { ...baseData.manifest.setup, feed: false } } };
    mount();
    const card = screen.getByTestId("card-current-balances");
    const steps = [...within(card).getByTestId("feed-setup-steps").querySelectorAll("li")].map((li) => li.textContent);
    expect(steps).toEqual(["Claim the connection once, with the setup token SimpleFIN gives you.",
      "Fetch the last 90 days, then map each account to its feed id (simplefin_id: in accounts.yaml).",
      "Refresh regularly: each refresh records every connected account's balance."]);
    expect(card.textContent).toContain("A bank feed adds a daily history, and any credit card's balance and utilization. To set one up:");
    expect(card.textContent).toContain("The exact commands are in the README, under “The bank feed (SimpleFIN)”.");
    expect(card.textContent).not.toMatch(/\bmake\b|TOKEN/);
    const codes = [...card.querySelectorAll("li code")];
    expect(codes.map((c) => c.textContent)).toEqual(["simplefin_id:", "accounts.yaml"]);
    expect(codes.every((c) => c.className === CODE_PILL)).toBe(true);
  });
  it("balances from statement files alone carry a note that says so in words, with no command in its title", () => {
    const csv = (date: string, balance: number): BalancePoint => ({ ...pt(date, balance), source: "csv" });
    data = { ...baseData, balances: { series: { chk: [csv("2026-07-01", 900), csv("2026-07-20", 1234)] },
      accounts: { chk: meta("Checking A", "checking") } } };
    mount();
    const note = screen.getByText("Balances from statement files only — a bank feed adds a daily history");
    expect(note.getAttribute("title")).toBe("Each balance here is one a statement file states. A bank feed records every "
      + "connected account's balance at each pull, which builds a daily history.");
    cleanup();
    data = baseData;
    mount();
    expect(screen.queryByText(/statement files only/), "one bank-feed point is a live history").toBeNull();
  });
  it("with a bank feed set up it says the balances come with the next refresh, and shows no setup steps", () => {
    data = { ...baseData, balances: { series: {}, accounts: {} },
      manifest: { ...baseData.manifest, setup: { ...baseData.manifest.setup, feed: true } } };
    mount();
    const card = screen.getByTestId("card-current-balances");
    expect(card.textContent).toContain("The bank feed is set up; its balances appear after the next refresh.");
    expect(within(card).queryByTestId("feed-setup-steps")).toBeNull();
    expect(card.textContent).not.toMatch(/SimpleFIN/);
  });
});

describe("Balances' as-of dates: the table and the phone cards are two views of one list", () => {
  // the newest snapshot is dated Jul 20; "Savings B" last reported on Jul 12
  const stale = () => {
    data = { ...baseData, assets: { snapshots: [snapFactory({})({ snapshot_date: "2026-07-20" })] },
      balances: { ...baseData.balances, series: { ...baseData.balances.series, one: [pt("2026-07-12", 5678)] } } };
  };
  it("a balance older than the snapshot says 'as of' its date, in amber, in the table and on its phone card alike", () => {
    stale();
    mount();
    const table = screen.getByTestId("card-current-balances").querySelector("table")!;
    const old = within(table).getAllByRole("link", { name: "Savings B" })[0].closest("tr")!;
    expect(within(old).getByText("as of Jul 12, 2026").className).toContain("text-amber-300");
    const phoneCard = phone().getByRole("link", { name: "Savings B" }).closest("div.p-3")!;
    expect(within(phoneCard as HTMLElement).getByText(/as of Jul 12, 2026/).className).toContain("text-amber-300");
    expect(table.textContent).not.toMatch(/\d{4}-\d{2}-\d{2}/);
  });
  it("a balance is dated against the date the badge shows: a card reported after the snapshot dates the badge, and the rest say 'as of'", () => {
    // cards out of net worth: the card's balance of Jul 22 is in the table, after the snapshot of Jul 20
    data = { ...baseData, assets: { snapshots: [snapFactory({})({ snapshot_date: "2026-07-20" })] },
      balances: { series: { chk: [pt("2026-07-20", 1234)], card: [pt("2026-07-22", -400)] },
        accounts: { chk: meta("Checking A", "checking"), card: meta("Card C", "credit-card", "Other Debt") } } };
    mount();
    const table = screen.getByTestId("card-current-balances");
    expect(table.textContent).toContain("as of Jul 22, 2026");                                    // the badge
    const chk = within(table.querySelector("table")!).getAllByRole("link", { name: "Checking A" })[0].closest("tr")!;
    expect(within(chk).getByText("as of Jul 20, 2026").className).toContain("text-amber-300");
    const card = within(table.querySelector("table")!).getAllByRole("link", { name: "Card C" })[0].closest("tr")!;
    expect(card.textContent).not.toMatch(/as of/);
  });
  it("a current balance carries no date of its own (the card's badge dates the table), and the history line still says its span", () => {
    stale();
    mount();
    const table = screen.getByTestId("card-current-balances").querySelector("table")!;
    const row = within(table).getAllByRole("link", { name: "Checking A" })[0].closest("tr")!;
    expect(row.textContent).not.toMatch(/as of|Jul 20/);
    expect(phone().getByRole("link", { name: "Checking A" }).closest("div.p-3")!.textContent).not.toMatch(/as of/);
    fireEvent.click(row);
    expect(screen.getByTestId("card-current-balances").textContent).toContain("history since Jul 1, 2026 · 2 observations");
  });
});

describe("Balances' table says what each figure is", () => {
  const point = (balance: number, available: number | null, limit: number | null): BalancePoint =>
    ({ date: "2026-07-20", balance, available, limit, utilization: limit ? Math.round(-balance / limit * 1e4) / 1e4 : null,
       source: "simplefin" });
  const withSeries = (series: Record<string, BalancePoint[]>, accounts: Record<string, ReturnType<typeof meta>>,
                      rows: AssetRow[] = []) => {
    data = { ...baseData, assets: { snapshots: [snapFactory({})({ snapshot_date: "2026-07-20", rows })] },
      balances: { series, accounts } };
  };
  const rowOf = (name: string) => within(screen.getByTestId("card-current-balances").querySelector("table")!)
    .getAllByRole("link", { name })[0].closest("tr")!;
  const cells = (name: string) => [...rowOf(name).querySelectorAll("td")].map((c) => c.textContent);
  it("a loan shows no available credit and no utilisation: the feed's 'available' on a loan is its balance again", () => {
    withSeries({ loan: [point(-250_000, -250_000, null)] }, { loan: meta("Home Loan", "loan", "Mortgage") });
    mount();
    expect(cells("Home Loan").slice(3)).toEqual(["−$250,000", "—", "—", "—"]);
    expect(phone().getByRole("link", { name: "Home Loan" }).closest("div.p-3")!.textContent).not.toMatch(/avail/);
  });
  it("a card's utilisation is the one figure Liabilities prints, at one decimal; with no limit it is a dash, never 0%", () => {
    withSeries({ c1: [point(-851.88, 7148.12, 8000)], c2: [point(-1084.92, null, null)] },
      { c1: meta("Card One", "credit-card", "Other Debt"), c2: meta("Card Two", "credit-card", "Other Debt") });
    mount();
    expect(cells("Card One")[6]).toBe("10.6%");
    expect(cells("Card Two")[6]).toBe("—");
    expect(phone().getByRole("link", { name: "Card One" }).closest("div.p-3")!.textContent).toContain("10.6% used");
  });
  it("a margin account's balance is named net of the margin loan; an account held as one row is not", () => {
    const r = (over: Partial<AssetRow>): AssetRow => ({ account: "Broker", account_id: "brk", institution: "Bank", owner: "Joint",
      asset_class: "Stocks/ETF", value: 5600, liquid: true, as_of: "2026-07-20", as_of_assumed: false, needs_review: false,
      source: "positions", manual_id: null, carried_back: false, ...over });
    withSeries({ brk: [point(5000, null, null)], chk: [point(1234, null, null)] },
      { brk: meta("Broker C", "brokerage", "Stocks/ETF"), chk: meta("Checking A", "checking") },
      [r({}), r({ account: "Broker Margin", asset_class: "Margin", value: -600, liquid: false, source: "simplefin_balance" }),
       r({ account: "Checking A", account_id: "chk", asset_class: "Cash", value: 1234, source: "bank_balance" })]);
    mount();
    expect(rowOf("Broker C").textContent).toContain("net of the margin loan");
    expect(phone().getByRole("link", { name: "Broker C" }).closest("div.p-3")!.textContent).toContain("net of the margin loan");
    expect(rowOf("Checking A").textContent).not.toContain("margin");
    // the section's subtotal says it is after the loan (Investments counts the same account's positions before it); the
    // cash section says nothing of a loan
    const heads = [...document.querySelectorAll("tbody > tr:first-child")].map((tr) => tr.textContent ?? "");
    expect(heads.find((h) => h.startsWith("Investments"))).toContain("· after the margin loan");
    expect(heads.filter((h) => !h.startsWith("Investments")).join(" ")).not.toContain("margin loan");
  });
});

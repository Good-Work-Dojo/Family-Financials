// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// The seven pages that read the newest net-worth snapshot, each mounted whole on the households the app must cope with:
// none at all (no snapshot, no transaction); transactions and no snapshot (statement files that carry no balance); a
// snapshot and no transaction (a balance-only account and a home stated by hand); one snapshot (nothing earlier to
// compare it with). With no snapshot every card of the page is on screen and each one that needs a snapshot says so in
// the shared line (components/NoData.tsx), with no figure and no chart; with one snapshot a delta or a trend says there
// is not enough history yet, never a change of 0. The cards a page leaves out for another reason are named per page.
import { cleanup, render, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CAPABILITIES } from "../capabilities";
import { NO_BALANCES, NO_BALANCES_YET, NOT_ENOUGH_HISTORY, NO_TRANSACTIONS, NO_TRANSACTIONS_YET } from "../components/NoData";
import { buildIndexes } from "../data/DataContext";
import type { AppData, MortgageBlock } from "../data/types";
import { acctFactory, appDataFactory, balancesOnlyData, snapFactory, transactionsOnlyData } from "../lib/__fixtures__";
import { _resetEfundOverridesMemory } from "../lib/efundOverrides";
import { emptyPrefs } from "../lib/prefs";
import { addScenario, emptyStore, type Scenario } from "../lib/scenarioStore";
import { missingCards } from "../test/matrixChecks";

const state = vi.hoisted(() => ({ ctx: null as unknown, scenarios: [] as Scenario[], charts: [] as unknown[] }));
vi.mock("../data/DataContext", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../data/DataContext")>()),
  useData: () => state.ctx,
}));
vi.mock("../components/EChart", () => ({
  default: (p: { option: unknown }) => { state.charts.push(p.option); return <div data-testid="chart" />; },
}));
// Data's refresh panel talks to the store; the scenario store is the family's document: neither is what is tested here
vi.mock("../components/DataRefresh", () => ({ RefreshPanel: () => <div /> }));
vi.mock("../data/useScenarioStore", () => ({
  useScenarioStore: () => ({
    loading: false, localOnly: false, saveState: "idle",
    store: { ...emptyStore(), scenarios: state.scenarios },
    activeId: state.scenarios[0]?.id ?? null, active: state.scenarios[0] ?? null,
    setActiveId: () => {}, create: () => {}, rename: () => {}, duplicate: () => {}, remove: () => {},
    setInputs: () => {}, setDraw: () => {},
  }),
}));

import Balances from "./Balances";
import Data from "./Data";
import Home from "./Home";
import Investments from "./Investments";
import Liabilities from "./Liabilities";
import SecondHouse from "./SecondHouse";
import Tools from "./Tools";

/** the households */
const NONE: AppData = { ...appDataFactory(snapFactory())({}), assets: { snapshots: [] }, cashflow: [],
  manifest: { ...appDataFactory(snapFactory())({}).manifest, current_snapshot_date: null,
    setup: { accounts: 0, feed: false, import_files: 0, manual_assets: 0 } } };
const ONE: AppData = appDataFactory(snapFactory({ snapshot_date: "2026-06-01" }))({});

function use(data: AppData) {
  const doc = emptyPrefs();
  state.ctx = {
    data, missing: new Set<string>(), indexes: buildIndexes(data.transactions, data.purchases),
    prefs: { loading: false, localOnly: false, refused: null, doc, setBudget: () => {}, setTarget: () => {},
      setTargetMix: () => true, setProjection: () => {}, setPayrollPhases: () => {}, saveFilter: () => true,
      removeSavedFilter: () => {}, renameSavedFilter: () => true, saveReport: () => true, removeReport: () => {},
      renameReport: () => true },
    answers: {}, overlay: { pendingIds: new Set<string>(), conflictedKeys: new Set<string>() }, reload: async () => {},
  };
}

let errors: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  localStorage.clear(); _resetEfundOverridesMemory();
  state.scenarios = []; state.charts = [];
  errors = vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => { cleanup(); errors.mockRestore(); });

const mount = (page: ReactElement) => render(<MemoryRouter>{page}</MemoryRouter>);
const card = (id: string) => screen.getByTestId(id);
const text = (id: string) => card(id).textContent ?? "";
/** Investments' full line for a household with no investment account (its code pills' text included) */
const NO_INVESTMENT_ACCOUNT = "No investment account yet. Add a brokerage account connected to the bank feed and it appears "
  + "here after the next refresh (kind: brokerage and simplefin_id: in accounts.yaml).";
/** what no page may print: a broken value, and on a household with no data at all, a zero standing for "unknown" */
const BROKEN = /NaN|Infinity|undefined|Invalid Date|∞/;
const ZERO = /\$0(?![\d,.])|(?<![\d.])0(?:\.0)?%/;
/** a figure: an amount or a percentage */
const FIGURE = /\$\s?\d|\d\s?%/;

/** the page renders: no error-boundary card, no console.error, no broken value */
function renders() {
  expect(document.body.textContent ?? "").not.toMatch(BROKEN);
  expect(errors.mock.calls.map((c) => String(c[0])), "nothing logged to console.error").toEqual([]);
}
/** every card of `route` the route matrix looks for (its rows with no `when`, but those a stand-in on screen replaces:
 *  missingCards) is on screen but those `absent` names (left out for a reason other than the snapshot: the line beside
 *  each says which) */
function everyCard(route: string, absent: Record<string, string> = {}) {
  const missing = missingCards(document.body, route);
  expect(missing.sort()).toEqual(Object.keys(absent).sort());
}
/** a card that needs the snapshot: it says so in `line` and shows no figure (its title aside: a card heading such as
 *  "Positions >10% of …" names a threshold, not a figure; `heading` is one that is no <h2>) and no chart */
function saysNoBalances(id: string, line: string = NO_BALANCES, heading = "") {
  const shell = card(id).cloneNode(true) as HTMLElement;
  shell.querySelectorAll("h2").forEach((h) => h.remove());
  const body = (shell.textContent ?? "").replace(heading, "");
  expect(body, id).toContain(line);
  expect(body, `${id} shows a figure`).not.toMatch(FIGURE);
  expect(within(card(id)).queryByTestId("chart"), `${id} draws a chart`).toBeNull();
}
/** the same, in the short line only: a KPI tile, or a card after the one that says it in full */
function saysItShort(id: string, heading = "") {
  saysNoBalances(id, NO_BALANCES_YET, heading);
  expect(text(id), `${id} says the long line`).not.toContain(NO_BALANCES);
}
/** a page says the long no-balances line once: in its first card that needs a snapshot (or under a KPI row no card
 *  below speaks for), never in a tile, and the cards after it in short */
function saysTheLongLineOnce() {
  expect((document.body.textContent ?? "").split(NO_BALANCES).length - 1, "times the long line is on the page").toBe(1);
}

describe("Home", () => {
  const ROUTE = "/";
  it("with no snapshot: every card is there, each snapshot card says what is missing, no figure, no $0", () => {
    use(NONE);
    mount(<Home />);
    renders();
    everyCard(ROUTE);
    // the line right under the tiles says it in full, once; the cards after it say it short
    saysNoBalances("card-snapshot-deltas");
    for (const id of ["card-decomposition", "card-networth-history", "card-allocation-donut"]) saysItShort(id);
    saysTheLongLineOnce();
    // the KPI row: every snapshot tile reads a bare "—", so nothing above the decomposition card repeats the line
    const tiles = [...card("kpis-home").children].map((t) => t.textContent ?? "");
    for (const tile of tiles.slice(0, 3)) expect(tile).toMatch(/^[A-Za-z ]+—$/);
    const before = (document.body.textContent ?? "").split(card("card-decomposition").textContent ?? "")[0];
    expect(before.split(NO_BALANCES_YET).length - 1, "times the short or long line is said above the decomposition").toBe(1);
    expect(document.body.textContent ?? "").not.toMatch(ZERO);
    expect(text("card-month-to-date")).toContain(NO_TRANSACTIONS);
  });
  it("with transactions and no snapshot: the transaction cards work, the snapshot cards say what is missing", () => {
    use(transactionsOnlyData());
    mount(<Home />);
    renders();
    everyCard(ROUTE);
    saysNoBalances("card-snapshot-deltas");
    for (const id of ["card-decomposition", "card-networth-history", "card-allocation-donut"]) saysItShort(id);
    saysTheLongLineOnce();
    expect(text("card-month-to-date")).not.toContain(NO_TRANSACTIONS_YET);
    expect(text("kpi-savings-rate-ttm")).toMatch(/\d%/);
  });
  it("with no snapshot and no cash-flow file, the line under the tiles still carries the long line, the history the short", () => {
    use(NONE);
    state.ctx = { ...(state.ctx as object), missing: new Set(["cashflow_monthly.json"]) };
    mount(<Home />);
    expect(screen.queryByTestId("card-decomposition")).toBeNull();
    saysNoBalances("card-snapshot-deltas");
    saysItShort("card-networth-history");
    saysTheLongLineOnce();
  });
  it("with one snapshot: the change, the chips, the waterfall and the history say there is not enough history yet", () => {
    // the snapshot holds an asset above zero, so the donut has a total to divide
    use({ ...ONE, assets: { snapshots: [snapFactory({ snapshot_date: "2026-06-01" })({ by_class: { Cash: 2_000_000 },
      rows: [{ account: "Checking", account_id: "chk", institution: "Sample Bank", owner: "Joint", asset_class: "Cash",
        value: 2_000_000, liquid: true, as_of: "2026-06-01", as_of_assumed: false, needs_review: false, source: "bank_balance",
        manual_id: null, carried_back: false }] })] } });
    mount(<Home />);
    renders();
    everyCard(ROUTE);
    const tile = [...card("kpis-home").children][0].textContent ?? "";
    expect(tile).toContain(`${NOT_ENOUGH_HISTORY} for a change`);
    expect(tile).not.toMatch(/[+−]\$0|\(0%\)|vs /);
    for (const id of ["card-snapshot-deltas", "card-decomposition", "card-networth-history"]) {
      expect(text(id), id).toContain(NOT_ENOUGH_HISTORY);
      expect(within(card(id)).queryByTestId("chart"), `${id} draws a chart of one point`).toBeNull();
    }
    expect(within(card("card-allocation-donut")).getByTestId("chart")).toBeDefined();   // the newest snapshot alone is enough
  });
  it("with one snapshot and a daily line of several days the history draws them: the line and the total, no breakdown", () => {
    use({ ...ONE, networthDaily: { series: ["2026-05-29", "2026-05-30", "2026-05-31", "2026-06-01"].map((date) =>
      ({ date, networth: 2_000_000, coverage: 1, source: "simplefin" })), current_total: 2_000_000,
      current_snapshot_date: "2026-06-01" } });
    mount(<Home />);
    renders();
    const history = card("card-networth-history");
    expect(within(history).getByTestId("chart")).toBeDefined();
    // no class drawn as a lone dot with a legend entry: the snapshot's total and the daily line only
    const option = state.charts.find((o) => (o as { series?: { name: string }[] }).series?.some((s) => s.name === "Net worth (snapshots)"));
    expect((option as { series: { name: string }[] }).series.map((s) => s.name))
      .toEqual(["Net worth (snapshots)", "Net worth (daily · bank feed)"]);
    // the two lines are a level, not stacked bands: the value axis is scaled to them, never a $0 floor under a flat line
    expect((option as { yAxis: { scale?: boolean } }).yAxis.scale).toBe(true);
    // ... at a step its compact labels tell apart ($10K steps at $2M: "$2.00M", "$2.01M")
    expect((option as { yAxis: { minInterval?: number } }).yAxis.minInterval).toBe(10_000);
    // the plain title (nothing to rewind to); the by-class / by-owner toggle stays for the allocation donut, and the
    // breakdown here waits for a second snapshot
    expect(history.querySelector("h2")?.textContent).toBe("Net worth over time");
    expect(within(history).getByRole("button", { name: "by owner" })).toBeDefined();
    expect(within(history).getByTestId("not-enough-history").textContent)
      .toBe(`${NOT_ENOUGH_HISTORY} — the breakdown by class and by owner appears once a second snapshot exists.`);
  });
  it("with a snapshot and no transaction: the snapshot cards show it, the month so far says no transactions yet", () => {
    use(balancesOnlyData());
    mount(<Home />);
    renders();
    everyCard(ROUTE);
    expect(text("card-month-to-date")).toContain(NO_TRANSACTIONS);
    expect(within(card("card-allocation-donut")).getByTestId("chart")).toBeDefined();
    expect([...card("kpis-home").children][0].textContent).toContain("$412,000");
  });
});

describe("Balances", () => {
  const ROUTE = "/accounts";
  // the cash history is drawn from the balance history, which a household with no balance has none of either: the card
  // says whether there is no cash account or none has reported
  const cashLine: [string, () => AppData, RegExp][] = [["no data at all", () => NONE, /^No cash account yet\./],
    ["transactions and no snapshot", transactionsOnlyData, /^No balance recorded for the cash accounts yet\./]];
  for (const [name, data, line] of cashLine) {
    it(`with ${name}: every card is there, the snapshot cards and the cash history say what is missing, no figure`, () => {
      use(data());
      mount(<Balances />);
      renders();
      everyCard(ROUTE);
      saysItShort("kpi-illiquid");
      expect(text("kpi-illiquid")).toContain("—");
      saysNoBalances("card-balance-sheet-assets");
      for (const id of ["card-balance-sheet-liabilities", "card-liquidity"]) saysItShort(id);
      saysTheLongLineOnce();
      saysNoBalances("card-cash-balances", "");
      expect(within(card("card-cash-balances")).getByTestId("empty-state").textContent).toMatch(line);
      expect(document.body.textContent ?? "").not.toMatch(ZERO);
    });
  }
  it("with one snapshot the snapshot cards show it", () => {
    use(ONE);
    mount(<Balances />);
    renders();
    everyCard(ROUTE);
    expect(text("kpi-illiquid")).toContain("$1,500,000");
    expect(text("card-balance-sheet-liabilities")).toContain("Net worth");
  });
});

describe("Investments", () => {
  const ROUTE = "/accounts/investments";
  const tiles = () => [...card("kpis-investments").children].map((t) => t.textContent ?? "");
  const NONE_DATA = () => NONE;
  for (const [name, data] of [["no data at all", NONE_DATA], ["transactions and no snapshot", transactionsOnlyData]] as const) {
    it(`with ${name}: every card is there, the snapshot cards say what is missing, no figure`, () => {
      use(data());
      mount(<Investments />);
      renders();
      everyCard(ROUTE);
      expect(tiles()[0]).toBe(`Total invested assets—${NO_BALANCES_YET}`);
      expect(tiles()[2]).toBe(`Margin debt—${NO_BALANCES_YET}`);
      saysNoBalances("card-concentration", NO_BALANCES, "Positions >10% of investable assets");
      saysItShort("card-holdings");
      saysTheLongLineOnce();
      // the account values over time: the household has no investment account, and the card says what to add
      saysNoBalances("card-investment-balances", NO_INVESTMENT_ACCOUNT);
      // with no data at all, no $0 or 0% standing for "unknown" anywhere but the target mix, whose age-based suggestion
      // prints its buckets' dollars, $0 of a $0 total (a suggestion's arithmetic, no figure of the household's); with
      // transactions, a passive income of $0 is the data's own figure
      if (data === NONE_DATA) {
        const page = document.body.cloneNode(true) as HTMLElement;
        expect(page.querySelector('[data-testid="card-target-mix"]'), "the exception is on the page").not.toBeNull();
        page.querySelector('[data-testid="card-target-mix"]')?.remove();
        expect(page.textContent ?? "").not.toMatch(ZERO);
      }
    });
  }
  it("brokerage accounts with no balance history: the account values say none has reported", () => {
    const data = NONE;
    use({ ...data, accounts: [acctFactory()({ id: "brk", name: "Broker", kind: "brokerage", asset_class: "Stocks/ETF" })] });
    mount(<Investments />);
    renders();
    saysNoBalances("card-investment-balances", "No balance recorded for the investment accounts yet.");
  });
  it("with no transaction the passive income says so, in the shared words", () => {
    use(NONE);
    mount(<Investments />);
    expect(tiles()[1]).toContain(`—${NO_TRANSACTIONS_YET}`);
  });
  it("asset-class targets with no snapshot: the card and its edit button stay, the line in place of the drift", () => {
    const data = NONE;
    use({ ...data, assumptions: { ...data.assumptions, targets: { "Stocks/ETF": 60, Cash: 40 } } });
    mount(<Investments />);
    renders();
    saysItShort("card-class-drift");
    saysTheLongLineOnce();
    expect(within(card("card-class-drift")).getByRole("button", { name: "Edit asset-class targets" })).toBeDefined();
  });
  it("with one snapshot the snapshot cards show it", () => {
    use(ONE);
    mount(<Investments />);
    renders();
    everyCard(ROUTE);
    expect(text("card-concentration")).not.toContain(NO_BALANCES_YET);
  });
  it("a snapshot and no investment account: the concentration says what to add, in one line, and the cards after it say it short", () => {
    use(balancesOnlyData());
    mount(<Investments />);
    renders();
    const concentration = card("card-concentration");
    expect(concentration.textContent).toBe(`Positions >10% of investable assets${NO_INVESTMENT_ACCOUNT}`);
    expect([...concentration.querySelectorAll("code")].map((c) => c.textContent)).toEqual(["kind: brokerage", "simplefin_id:", "accounts.yaml"]);
    const holdings = card("card-holdings");
    expect(holdings.textContent, "no caption under an empty list").toBe("Holdings by accountNo investment account yet.");
    expect(within(card("card-investment-balances")).getByTestId("empty-state").textContent).toBe("No investment account yet.");
    expect((document.body.textContent ?? "").split(NO_INVESTMENT_ACCOUNT).length - 1, "the long line once").toBe(1);
  });
  it("investment accounts with no positions or balance yet: the holdings say so", () => {
    const data = balancesOnlyData();
    use({ ...data, accounts: [...data.accounts, acctFactory()({ id: "brk", name: "Broker", kind: "brokerage", asset_class: "Stocks/ETF",
      latest_balance: null, latest_balance_as_of: null })] });
    mount(<Investments />);
    renders();
    expect(within(card("card-holdings")).getByTestId("empty-state").textContent)
      .toBe("No holdings yet. Each investment account appears here once it reports its positions or a balance.");
  });
});

describe("Liabilities", () => {
  const ROUTE = "/accounts/liabilities";
  // the two card charts read the balance history, which holds no card here: the one card that says so stands in for
  // both (its row names them in `insteadOf`, so everyCard excuses them while it is on screen)
  for (const [name, data] of [["no data at all", () => NONE], ["transactions and no snapshot", transactionsOnlyData]] as const) {
    it(`with ${name}: every debt tile reads "—" and says what is missing in a few words, the long line under the row, no $0`, () => {
      use(data());
      mount(<Liabilities />);
      renders();
      everyCard(ROUTE);
      for (const id of ["kpi-total-debt", "kpi-card-debt", "kpi-margin-debt", "kpi-available-credit"]) saysItShort(id);
      // no account is the mortgage: the tile says so, as the mortgage card below does
      expect(text("kpi-mortgage-balance")).toBe("Mortgage—No mortgage account");
      expect(text("card-mortgage-summary")).toContain("No loan account, so no mortgage is tracked.");
      // the long line is a full-width line right after the row, never in a tile
      expect(card("kpis-liabilities").nextElementSibling?.textContent).toBe(NO_BALANCES);
      saysTheLongLineOnce();
      expect(card("card-no-credit-cards")).toBeDefined();
      expect(document.body.textContent ?? "").not.toMatch(ZERO);
    });
  }
  it("with a mortgage account and no snapshot the mortgage tile says the feed has not reported it", () => {
    const data = NONE;
    const mortgage: MortgageBlock = { account_id: "loan", balance: null, balance_as_of: null, rate_pct: null,
      monthly_payment: null, monthly_escrow: null, escrow_balance: null, origination: null, term_months: null, schedule: [] };
    use({ ...data, assumptions: { ...data.assumptions, mortgage } });
    mount(<Liabilities />);
    renders();
    expect(text("kpi-mortgage-balance")).toBe("Mortgage—No balance from the bank feed yet");
  });
  it("with one snapshot the tiles show it, and the card tiles say in a few words there is no credit card", () => {
    use(ONE);
    mount(<Liabilities />);
    renders();
    everyCard(ROUTE);
    expect(text("kpi-total-debt")).not.toContain(NO_BALANCES_YET);
    expect(text("kpi-margin-debt"), "no margin account: no $0 for one").toBe("Margin—No margin account · Investments →");
    expect(text("kpi-card-debt")).toBe("Credit cards—No credit card yet");
    expect(text("kpi-available-credit")).toBe("Available credit—No credit card yet");
    // the card below the row says it in full: what to add, connected to the feed, the keys as a hint
    expect(text("card-no-credit-cards")).toContain("No credit card yet. Add a credit-card account connected to the bank feed");
  });
});

describe("Projections", () => {
  const ROUTE = "/planning";
  const SNAPSHOT_CARDS = ["card-forecast", "card-retirement", "card-networth-projection", "card-emergency-fund", "card-market-shock"];
  for (const [name, data] of [["no data at all", () => NONE], ["transactions and no snapshot", transactionsOnlyData]] as const) {
    it(`with ${name}: every card is there, the snapshot cards and tiles say what is missing, no figure`, () => {
      use(data());
      mount(<Tools />);
      renders();
      everyCard(ROUTE);
      for (const id of ["kpi-cash-runway", "kpi-bear-case"]) saysItShort(id);
      // the forecast is the first card that needs a snapshot: it says the line in full, the cards after it short
      saysNoBalances("card-forecast");
      for (const id of SNAPSHOT_CARDS.slice(1)) saysItShort(id);
      saysTheLongLineOnce();
      expect(text("card-spending-cut"), "no share of a net worth there is none of").not.toMatch(/net worth/);
    });
  }
  it("with no snapshot and no cash-flow or holdings file, the emergency fund carries the long line", () => {
    use(NONE);
    state.ctx = { ...(state.ctx as object), missing: new Set(["cashflow_monthly.json", "holdings.json"]) };
    mount(<Tools />);
    saysNoBalances("card-emergency-fund");
    saysTheLongLineOnce();
  });
  it("with no data at all no figure stands for one the data lacks: no $0, no 0%", () => {
    use(NONE);
    mount(<Tools />);
    expect(text("kpi-big-bills")).toContain(`—${NO_TRANSACTIONS_YET}`);
    for (const id of ["kpis-forward-look", ...SNAPSHOT_CARDS]) expect(text(id), id).not.toMatch(ZERO);
    // nothing to scroll to: the cards below say the same, so neither empty tile is a button
    for (const id of ["kpi-bear-case", "kpi-cash-runway"]) expect(card(id).tagName, id).not.toBe("BUTTON");
  });
  it("with a snapshot and no transaction: the essentials, the floor and the FIRE number say no transactions yet, never $0", () => {
    use(balancesOnlyData());
    mount(<Tools />);
    renders();
    everyCard(ROUTE);
    // the tile in short and no button (the emergency-fund card says the same in full); the bear case has its figure
    expect(text("kpi-cash-runway")).toBe(`Cash runway—${NO_TRANSACTIONS_YET}`);
    expect(card("kpi-cash-runway").tagName).not.toBe("BUTTON");
    expect(card("kpi-bear-case").tagName).toBe("BUTTON");
    expect(text("kpi-big-bills")).toContain(`—${NO_TRANSACTIONS_YET}`);
    expect(text("kpi-bear-case")).toContain("$412,000");
    for (const id of ["card-emergency-fund", "card-forecast"]) {
      expect(text(id), id).toContain(NO_TRANSACTIONS);
      expect(text(id), `${id}: no $0 a month, no floor, no claim about one`).not.toMatch(/\$0|floor|essentials all year/);
    }
    const fire = within(card("card-retirement")).getByText("FIRE number").parentElement?.textContent ?? "";
    expect(fire).toContain(`—${NO_TRANSACTIONS_YET}`);
    expect(text("card-retirement")).not.toMatch(/\$0(?![\d,])|core spend/);
  });
  it("with one snapshot the snapshot cards show it", () => {
    use(ONE);
    mount(<Tools />);
    renders();
    // two months of cash flow, the newer one still in progress: the forecast needs six full months, and until then its
    // card says so and how many there are, with no figure and no chart
    everyCard(ROUTE);
    saysNoBalances("card-forecast", `${NOT_ENOUGH_HISTORY}.`);
    expect(text("kpi-bear-case")).toContain("$2,000,000");
    expect(text("card-networth-projection")).toContain("from $2,000,000 today");
  });
});

describe("Second house", () => {
  const ROUTE = "/planning/second-house";
  const scenarios = (n: number) => Array.from({ length: n }).reduce<Scenario[]>((list, _, i) =>
    addScenario({ ...emptyStore(), scenarios: list }, `s${i}`, "2026-06-10T09:00:00Z")?.scenarios ?? list, []);
  const tiles = () => [...card("kpis-guardrails").children].map((t) => t.textContent ?? "");
  /** the cards a scenario brings (the capability rows `when: "scenario"`) */
  const scenarioCards = CAPABILITIES.filter((c) => c.route === ROUTE && c.when === "scenario").map((c) => c.id);
  for (const [name, data] of [["no data at all", () => NONE], ["transactions and no snapshot", transactionsOnlyData]] as const) {
    it(`with ${name} and no scenario: the page and its first-scenario card`, () => {
      use(data());
      mount(<SecondHouse />);
      renders();
      everyCard(ROUTE);
    });
    it(`with ${name} and a scenario: every scenario card is there; the cash tiles read "—" and say it short, the line under them in full`, () => {
      use(data());
      state.scenarios = scenarios(2);
      mount(<SecondHouse />);
      renders();
      expect(scenarioCards.filter((id) => screen.queryByTestId(id) === null), "scenario cards not on screen").toEqual([]);
      expect(tiles()[0]).toBe(`Essentials cushion after purchase—${NO_BALANCES_YET}`);
      expect(tiles()[1]).toBe(`Liquid cash after cash draws—${NO_BALANCES_YET}`);
      expect(card("kpis-guardrails").nextElementSibling?.textContent).toBe(NO_BALANCES);
      saysTheLongLineOnce();
      const left = [...card("card-scenario-comparison").querySelectorAll("tr")].find((r) => r.textContent?.startsWith("Liquid cash left"));
      expect(left?.textContent).toBe("Liquid cash left——");
    });
  }
  it("with a snapshot and no transaction: the cushion says no transactions yet in short and in full under the row; the cash is the snapshot's", () => {
    use(balancesOnlyData());
    state.scenarios = scenarios(1);
    mount(<SecondHouse />);
    renders();
    expect(tiles()[0]).toBe(`Essentials cushion after purchase—${NO_TRANSACTIONS_YET}`);
    expect(card("kpis-guardrails").nextElementSibling?.textContent).toBe(NO_TRANSACTIONS);
    expect(tiles()[1]).toContain("$12,000");
  });
});

describe("Data", () => {
  const ROUTE = "/data";
  /** the freshness card's key / value lines */
  const lines = () => Object.fromEntries([...card("card-freshness").querySelectorAll("li")]
    .map((li) => [li.firstElementChild?.textContent ?? "", li.lastElementChild?.textContent ?? ""]));
  it("with no data at all: every card is there, the snapshot and transaction lines say what is missing", () => {
    use(NONE);
    mount(<Data />);
    renders();
    everyCard(ROUTE);
    expect(lines()).toEqual({ "Transactions through": NO_TRANSACTIONS_YET, "Asset snapshot": NO_BALANCES_YET });
    expect(text("card-assumptions-in-force")).not.toMatch(/Home value/);
    // no account: the per-account card says what to add, in one line, with no empty list and no legend
    const perAccount = card("card-account-freshness");
    expect(perAccount.textContent).toBe("Data freshness by account"
      + "No account yet. Add one and the day its transactions reach appears here after the next ingest (accounts.yaml).");
    expect(perAccount.querySelector("ul")).toBeNull();
  });
  it("with transactions and no snapshot: the data's last day, and no snapshot yet", () => {
    use(transactionsOnlyData());
    mount(<Data />);
    renders();
    everyCard(ROUTE);
    expect(lines()).toEqual({ "Transactions through": "Jun 4, 2026", "Asset snapshot": NO_BALANCES_YET });
  });
  it("with a snapshot and no transaction: the snapshot's date and its home, no transaction yet", () => {
    use(balancesOnlyData());
    mount(<Data />);
    renders();
    expect(lines()).toEqual({ "Transactions through": NO_TRANSACTIONS_YET, "Asset snapshot": "Jun 1, 2026" });
    expect(text("card-assumptions-in-force")).toContain("Home value (current snapshot)$400,000");
  });
});

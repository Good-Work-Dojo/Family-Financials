// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// Second house on households with little history or no balance: its free cash flow and cushion are planning run-rates,
// read from three full months or more (fewer: "—" and the line that says how many there are; three to eleven: from how
// many); with no account to draw from, or no balance known for any, the funding plan names no funded amount; and a
// household with no bank feed is told of no feed.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NOT_ENOUGH_HISTORY } from "../components/NoData";
import { TAP } from "../components/ui";
import { buildIndexes } from "../data/DataContext";
import type { AppData, AssetRow } from "../data/types";
import { acctFactory, firstWeekData, fullMonthsData, oneFullMonthData, transactionsOnlyData } from "../lib/__fixtures__";
import { _resetEfundOverridesMemory } from "../lib/efundOverrides";
import { fmtUSD, fmtUSD0 } from "../lib/format";
import { emptyPrefs } from "../lib/prefs";
import { addScenario, emptyStore, type Scenario } from "../lib/scenarioStore";
import { DEFAULT_INPUTS } from "../lib/secondHouse";

const state = vi.hoisted(() => ({ ctx: null as unknown, scenarios: [] as Scenario[], charts: [] as any[], loading: false,
  draws: [] as [string, number][] }));
vi.mock("../data/DataContext", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../data/DataContext")>()),
  useData: () => state.ctx,
}));
vi.mock("../components/EChart", () => ({
  default: (p: { option: unknown }) => { state.charts.push(p.option); return <div data-testid="chart" />; },
}));
// the store holds the scenarios it starts with in the page's own state, so a draw the page sets re-renders it
vi.mock("../data/useScenarioStore", async () => {
  const { useState } = await import("react");
  const store = await import("../lib/scenarioStore");
  return {
    useScenarioStore: () => {
      const [scenarios, setScenarios] = useState(state.scenarios);
      return {
        loading: state.loading, localOnly: false, saveState: "idle",
        store: { ...store.emptyStore(), scenarios },
        activeId: scenarios[0]?.id ?? null, active: scenarios[0] ?? null,
        setActiveId: () => {}, create: () => {}, rename: () => {}, duplicate: () => {}, remove: () => {},
        setInputs: () => {},
        // every draw saved is recorded, so a test can tell a save from none
        setDraw: (id: string, accountId: string, amount: number) => {
          state.draws.push([accountId, amount]);
          setScenarios((list) =>
            store.patchDraw({ ...store.emptyStore(), scenarios: list }, id, accountId, amount, "2026-06-10T09:00:00Z").scenarios);
        },
      };
    },
  };
});

import SecondHouse from "./SecondHouse";

function use(data: AppData, scenarioCount: number) {
  state.ctx = {
    data, missing: new Set<string>(), indexes: buildIndexes(data.transactions, data.purchases, data.manifest.generated_at),
    prefs: { loading: false, localOnly: false, refused: null, doc: emptyPrefs(), setBudget: () => {}, setTarget: () => {},
      setTargetMix: () => true, setProjection: () => {}, setPayrollPhases: () => {}, saveFilter: () => true,
      removeSavedFilter: () => {}, renameSavedFilter: () => true, saveReport: () => true, removeReport: () => {},
      renameReport: () => true },
    answers: {}, overlay: { pendingIds: new Set<string>(), conflictedKeys: new Set<string>() }, reload: async () => {},
  };
  state.scenarios = Array.from({ length: scenarioCount }).reduce<Scenario[]>((list, _, i) =>
    addScenario({ ...emptyStore(), scenarios: list }, `s${i}`, "2026-06-10T09:00:00Z")?.scenarios ?? list, []);
}

beforeEach(() => {
  localStorage.clear(); _resetEfundOverridesMemory(); state.scenarios = []; state.charts = []; state.loading = false;
  state.draws = [];
});
afterEach(cleanup);

const mount = (page: ReactElement) => render(<MemoryRouter>{page}</MemoryRouter>);
const card = (id: string) => screen.getByTestId(id);
const text = (id: string) => card(id).textContent ?? "";
/** the page's one line about history, with `n` full months so far and the day the next one ends */
const needs = (n: number, ends: string) => "The cushion divides by the household's own essential spending, which needs 3 "
  + `full months of transactions. So far: ${n} full month${n === 1 ? "" : "s"}; the next one ends ${ends}.`;
const kpi = (label: string) => within(card("kpis-second-house")).getByText(label).parentElement!;
/** the cost waterfall's option (the chart whose categories end in "Net /mo") */
const costChart = () => state.charts.find((o) => o?.xAxis?.data?.includes?.("Net /mo"));

describe("Second house with too little history to plan from", () => {
  // the first week's data begins on June 6th, in the month in progress: June will be no full month, so the next is July
  for (const [name, data, have, ends] of [["a first week (no full month)", firstWeekData, 0, "Jul 31, 2026"],
    ["a month the data begins on the 3rd of (a full month: the 3rd is within its first days)", oneFullMonthData, 1, "Jun 30, 2026"],
    ["one whole month", () => fullMonthsData(1), 1, "Jun 30, 2026"]] as const) {
    it(`${name}: no free cash flow to set the cost against, no cushion, and the line says how many full months there are`, () => {
      use(data(), 1);
      mount(<SecondHouse />);
      expect(kpi("Net after rental").textContent).toMatch(/carry minus expected rental income$/);
      expect(kpi("Net after rental").textContent).not.toMatch(/free cash/);
      expect(kpi("Net after rental").querySelector(".text-amber-400"), "no warning tone against an unknown").toBeNull();
      const tiles = [...card("kpis-guardrails").children].map((t) => t.textContent ?? "");
      expect(tiles[0], "a bare —: the line under the row says why").toBe("Essentials cushion after purchase—");
      expect(card("kpis-guardrails").nextElementSibling?.textContent).toBe(needs(have, ends));
      expect((document.body.textContent ?? "").split(NOT_ENOUGH_HISTORY).length - 1, "said once, in that line's words").toBe(0);
      expect(text("card-cost-breakdown")).not.toMatch(/amber|free cash/);
      const marks = costChart()?.series?.[1]?.markLine?.data ?? [];
      expect(marks.map((m: { name: string }) => m.name)).not.toContain("free\ncash");
    });
  }
  it("five full months: the free cash flow is named with its five months, never as a trailing twelve", () => {
    use(fullMonthsData(5), 1);
    mount(<SecondHouse />);
    expect(kpi("Net after rental").textContent).toContain(`vs ${fmtUSD0(4_400)}/mo avg free cash flow (5 full months)`);
    expect(text("kpis-second-house")).not.toMatch(/trailing 12mo/);
    expect(costChart().series[1].markLine.data.map((m: { name: string }) => m.name)).toContain("free\ncash");
  });
});

describe("Second house's scenario chip on a phone", () => {
  it("each glyph button is named in words and, like the chip and + New, is a 44 x 44 target below md", () => {
    use(fullMonthsData(5), 2);
    mount(<SecondHouse />);
    const name = state.scenarios[0].name;
    const buttons = [
      screen.getByRole("button", { name: `Rename ${name}` }), screen.getByRole("button", { name: `Duplicate ${name}` }),
      screen.getByRole("button", { name: `Delete ${name}…` }), screen.getByRole("button", { name: /\+ New/ }),
    ];
    for (const b of buttons) expect(b.className.split(/\s+/), b.textContent ?? "").toEqual(expect.arrayContaining(TAP.split(" ")));
    fireEvent.click(screen.getByRole("button", { name: `Delete ${name}…` }));
    for (const label of [`Delete ${name}`, `Keep ${name}`]) {
      expect(screen.getByRole("button", { name: label }).className.split(/\s+/)).toEqual(expect.arrayContaining(TAP.split(" ")));
    }
  });
});

describe("Second house's balances: which there are, and where they come from", () => {
  it("a household with no bank feed is told of no feed's pulls, in its text and its tooltips", () => {
    const titles = () => [...document.querySelectorAll("[title]")].map((e) => e.getAttribute("title") ?? "");
    use(oneFullMonthData(), 1);
    mount(<SecondHouse />);
    const page = document.body.textContent ?? "";
    expect(text("card-funding-plan")).toContain("Balances are each account's latest (badges show each account's as-of date).");
    expect(page).toContain("Account balances are each account's latest — each shows its as-of date.");
    expect(page).not.toMatch(/3×-daily|SimpleFIN|\bpull\b/);
    expect(titles().filter((t) => /feed|SimpleFIN|\bpull\b/.test(t))).toEqual([]);
    cleanup();
    // an account with no balance known: its tooltip says so in words true of a household with no feed
    use(transactionsOnlyData(), 1);
    mount(<SecondHouse />);
    expect(titles()).toContain("no balance is known for this account yet");
    expect(titles().filter((t) => /feed|SimpleFIN|\bpull\b/.test(t))).toEqual([]);
  });
  it("a known free cash flow at or below none: the warning tone says why", () => {
    const d = fullMonthsData(5);
    use({ ...d, cashflow: d.cashflow.map((m) => ({ ...m, income: 0 })) }, 1);
    mount(<SecondHouse />);
    expect(kpi("Net after rental").textContent).toMatch(new RegExp("no free cash flow to carry it: income did not cover spending "
      + `over the last 5 full months \\(${fmtUSD0(-600).replace("$", "\\$")}/mo\\)$`));
    expect(kpi("Net after rental").querySelector(".text-amber-400"), "a cost against no free cash flow is a warning").not.toBeNull();
  });
  it("a household with a feed keeps the feed's words (control)", () => {
    use(firstWeekData(), 1);
    mount(<SecondHouse />);
    expect(text("card-funding-plan")).toContain("Balances are each account's latest from the bank feed (badges show each");
    expect(document.body.textContent).toContain("Account balances come from the bank feed — each shows its as-of date.");
  });
  it("accounts and no balance known for any: no funded amount, no shortfall, and the comparison's funded row reads —", () => {
    use(transactionsOnlyData(), 2);
    mount(<SecondHouse />);
    expect(text("card-funding-plan")).toContain("No balance is known for these accounts yet, so nothing can be drawn");
    expect(text("card-funding-plan")).not.toMatch(/funded| short\b/);
    const funded = [...card("card-scenario-comparison").querySelectorAll("tr")].find((r) => r.textContent?.startsWith("Funded so far"));
    expect(funded?.textContent).toBe("Funded so far——");
  });
  it("no account that can fund it: the card says so in place of the table", () => {
    use({ ...oneFullMonthData(), accounts: [] }, 1);
    mount(<SecondHouse />);
    expect(text("card-funding-plan")).toBe("Funding plan — where the cash comes from"
      + "No account to fund it from yet: a checking, savings or brokerage account appears here with its balance once the "
      + "ingest reads one.");
    expect(within(card("card-funding-plan")).queryByRole("table")).toBeNull();
  });
  it("a balance to draw from: the plan says what is funded and what is short (control)", () => {
    use(oneFullMonthData(), 1);
    mount(<SecondHouse />);
    expect(text("funding-verdict")).toBe(`funded ${fmtUSD0(0)} of ${fmtUSD0(140_000)}; ${fmtUSD0(140_000)} short`);
  });
});

/** a snapshot row of the funding household */
const row = (account: string, id: string, value: number, cls = "Cash", liquid = true): AssetRow => ({ account,
  account_id: id, institution: "Sample Bank", owner: "Joint", asset_class: cls, value, liquid, as_of: "2026-06-09",
  as_of_assumed: false, needs_review: false, source: "statement_balance", manual_id: null, carried_back: false });

/** Five full months and three accounts to draw from: Joint Checking (12,000) and Joint Savings (50,000), the household's
 *  liquid cash (62,000), and Alex's 401(k) (200,000), a brokerage account in the Retirement class */
function fundingData(): AppData {
  const d = fullMonthsData(5);
  const acct = acctFactory({ latest_balance_as_of: "2026-06-09" });
  return {
    ...d,
    accounts: [
      acct({ id: "chk", name: "Joint Checking", latest_balance: 12_000 }),
      acct({ id: "sav", name: "Joint Savings", kind: "savings", latest_balance: 50_000 }),
      acct({ id: "k401", name: "Alex's 401(k)", owner: "Alex", kind: "brokerage", asset_class: "Retirement", latest_balance: 200_000 }),
    ],
    assets: { snapshots: [{ ...d.assets.snapshots[0], rows: [row("Joint Checking", "chk", 12_000),
      row("Joint Savings", "sav", 50_000), row("Alex's 401(k)", "k401", 200_000, "Retirement", false)] }] },
  };
}

/** a saved scenario at the default deal (140,000 of cash needed) with these draws */
const scenario = (id: string, name: string, draws: Record<string, number>): Scenario => ({
  id, name, created_at: "2026-06-01T00:00:00Z", updated_at: "2026-06-01T00:00:00Z", inputs: { ...DEFAULT_INPUTS }, draws });
const OVERDRAWS = { chk: 20_000, sav: 50_000, k401: 70_000 };    // 140,000: Joint Checking 8,000 below zero
const FUNDED = { chk: 10_000, sav: 50_000, k401: 80_000 };       // 140,000, every account at or above zero
const SHORT = { sav: 40_000 };                                    // 100,000 short
const OVER = { k401: 150_000 };                                   // 10,000 more than needed

function useFunding(...list: Scenario[]) {
  use(fundingData(), 0);
  state.scenarios = list;
}
const verdict = () => card("funding-verdict");

describe("Second house's funding verdict claims only what every draw shows", () => {
  it("draws that add up while one overdraws its account: the account and by how much, in the warning tone, no check", () => {
    useFunding(scenario("a", "Lake house", OVERDRAWS));
    mount(<SecondHouse />);
    expect(verdict().textContent).toBe(`Joint Checking would be overdrawn by ${fmtUSD0(8_000)}`);
    expect(verdict().className).toMatch(/text-amber-400/);
    expect(text("card-funding-plan")).not.toMatch(/fully funded|✓/);
    expect(verdict().className).not.toMatch(/emerald/);
  });
  it("draws that add up with every account at or above zero: fully funded, in green", () => {
    useFunding(scenario("a", "Lake house", FUNDED));
    mount(<SecondHouse />);
    expect(verdict().textContent).toBe("fully funded ✓");
    expect(verdict().className).toMatch(/text-emerald-400/);
  });
  it("draws that add up to less: what is funded of what, and the shortfall", () => {
    useFunding(scenario("a", "Lake house", SHORT));
    mount(<SecondHouse />);
    expect(verdict().textContent).toBe(`funded ${fmtUSD0(40_000)} of ${fmtUSD0(140_000)}; ${fmtUSD0(100_000)} short`);
    expect(verdict().className).toMatch(/text-red-400/);
  });
  it("draws that add up to more: the excess, with no check and no green", () => {
    useFunding(scenario("a", "Lake house", OVER));
    mount(<SecondHouse />);
    expect(verdict().textContent).toBe(`drawn ${fmtUSD0(150_000)} for ${fmtUSD0(140_000)}; ${fmtUSD0(10_000)} more than needed`);
    expect(verdict().className).not.toMatch(/emerald|amber|red/);
  });
  it("labels each account by its class, never its kind, and calls Cash-class draws cash draws", () => {
    useFunding(scenario("a", "Lake house", FUNDED), scenario("b", "Hill cabin", SHORT));
    mount(<SecondHouse />);
    const plan = text("card-funding-plan");
    expect(plan).toContain("Joint · Cash");
    expect(plan).toContain("Alex · Retirement");
    // the warning beside it says what the risk is, not the class a second time
    expect(plan).toContain("penalty ⚠");
    expect(plan).not.toMatch(/retirement ⚠/i);
    expect(plan).not.toMatch(/· (checking|savings|brokerage)\b/);
    const page = document.body.textContent ?? "";
    expect(page).not.toMatch(/checking\/savings/);
    expect(text("kpis-guardrails")).toContain("cash draws");
    expect(text("card-scenario-comparison")).toContain("only cash draws");
  });
  it("cash draws past today's liquid cash: a shortfall in words, never a negative balance presented as cash", () => {
    useFunding(scenario("a", "Lake house", OVERDRAWS), scenario("b", "Hill cabin", FUNDED));
    mount(<SecondHouse />);
    const left = [...card("kpis-guardrails").children].find((t) => t.textContent?.startsWith("Liquid cash after cash draws"));
    expect(left?.textContent).toBe(`Liquid cash after cash draws${fmtUSD0(8_000)} short`
      + `${fmtUSD0(62_000)} today, ${fmtUSD0(70_000)} in cash draws`);
    const cushion = [...card("kpis-guardrails").children].find((t) => t.textContent?.startsWith("Essentials cushion"));
    expect(cushion?.textContent).toBe(`Essentials cushion after purchasenonethe cash draws are ${fmtUSD0(8_000)} more than `
      + "today's liquid cash");
    const cells = [...card("card-scenario-comparison").querySelectorAll("tr")]
      .find((r) => r.textContent?.startsWith("Liquid cash left"))!.querySelectorAll("td");
    expect([...cells].map((c) => c.textContent)).toEqual(["Liquid cash left", `${fmtUSD0(8_000)} short`, fmtUSD0(2_000)]);
    expect(cells[1].className, "a shortfall is never the best of the scenarios").not.toMatch(/emerald/);
    expect(text("kpis-guardrails") + text("card-scenario-comparison")).not.toContain(fmtUSD0(-8_000));
  });
  it("a scenario that does not close is marked on its tab and in the comparison; one that does is not", () => {
    useFunding(scenario("a", "Lake house", OVERDRAWS), scenario("b", "Hill cabin", SHORT), scenario("c", "Shore cottage", FUNDED));
    mount(<SecondHouse />);
    const tabs = within(card("card-scenario-tabs")).getAllByRole("tab").map((t) => t.querySelector("button")?.textContent);
    expect(tabs).toEqual(["Lake house⚠ overdraws", "Hill cabin⚠ short", "Shore cottage"]);
    const head = [...card("card-scenario-comparison").querySelectorAll("th")].map((h) => h.textContent);
    expect(head).toEqual(["Metric", "Lake house⚠ overdraws", "Hill cabin⚠ short", "Shore cottage"]);
    const funding = [...card("card-scenario-comparison").querySelectorAll("tr")]
      .find((r) => r.textContent?.startsWith("Funding"))!.querySelectorAll("td");
    expect([...funding].map((c) => c.textContent)).toEqual(["Funding", "overdraws 1 account",
      `${fmtUSD0(100_000)} short`, "fully funded"]);
    // one tone for one state: a shortfall is red on the tab as on the card and in the Funding cell; an overdraw amber
    const mark = (k: number) => within(card("card-scenario-tabs")).getAllByRole("tab")[k].querySelector("button span");
    expect(mark(0)?.className).toMatch(/text-amber-400/);
    expect(mark(1)?.className).toMatch(/text-red-400/);
    expect(funding[2].className).toMatch(/text-red-400/);
  });
  it("an overdraw never hides a shortfall: the line and the comparison's cell carry both", () => {
    useFunding(scenario("a", "Lake house", { chk: 20_000, sav: 50_000 }), scenario("b", "Hill cabin", FUNDED));
    mount(<SecondHouse />);
    expect(verdict().textContent).toBe(`Joint Checking would be overdrawn by ${fmtUSD0(8_000)}; funded ${fmtUSD0(70_000)} `
      + `of ${fmtUSD0(140_000)}; ${fmtUSD0(70_000)} short`);
    const funding = [...card("card-scenario-comparison").querySelectorAll("tr")]
      .find((r) => r.textContent?.startsWith("Funding"))!.querySelectorAll("td");
    expect(funding[1].textContent).toBe(`overdraws 1 account; ${fmtUSD0(70_000)} short`);
  });
  it("an account already below zero with no draw is shown at its balance and keeps no plan from closing", () => {
    const d = fundingData();
    use({ ...d, accounts: [acctFactory({ latest_balance_as_of: "2026-06-09" })({ id: "od", name: "Spare Checking",
      latest_balance: -50 }), ...d.accounts] }, 0);
    state.scenarios = [scenario("a", "Lake house", FUNDED)];
    mount(<SecondHouse />);
    expect(verdict().textContent).toBe("fully funded ✓");
    expect(within(card("card-scenario-tabs")).getAllByRole("tab")[0].textContent).not.toMatch(/⚠/);
    const spare = [...card("card-funding-plan").querySelectorAll("tbody tr")].find((r) => r.textContent?.includes("Spare Checking"))!;
    const after = [...spare.querySelectorAll("td")].pop()!;
    expect(after.textContent, "its balance, as it is").toBe(fmtUSD0(-50));
    expect(after.getAttribute("title"), "no draw to speak of").toBeNull();
  });
  it("a draw saved against a balance nobody knows keeps the plan from closing, and the line names the account", () => {
    const d = fundingData();
    use({ ...d, accounts: [...d.accounts, acctFactory()({ id: "wallet", name: "Sample Wallet", kind: "wallet",
      asset_class: "Crypto", latest_balance: null, latest_balance_as_of: null })] }, 0);
    state.scenarios = [scenario("a", "Lake house", { chk: 10_000, sav: 50_000, k401: 79_500, wallet: 500 })];
    mount(<SecondHouse />);
    expect(verdict().textContent).toBe(`the balance of Sample Wallet is not known, so its draw of ${fmtUSD0(500)} cannot be `
      + "counted on");
    expect(verdict().className).toMatch(/text-amber-400/);
    expect(text("card-funding-plan")).not.toMatch(/fully funded|✓/);
    expect(within(card("card-scenario-tabs")).getAllByRole("tab")[0].querySelector("button")?.textContent)
      .toBe("Lake house⚠ balance unknown");
  });
  it("a draw saved against a balance nobody knows can be cleared through its box, which then closes again", () => {
    const d = fundingData();
    use({ ...d, accounts: [...d.accounts, acctFactory()({ id: "wallet", name: "Sample Wallet", kind: "wallet",
      asset_class: "Crypto", latest_balance: null, latest_balance_as_of: null })] }, 0);
    state.scenarios = [scenario("a", "Lake house", { chk: 10_000, sav: 50_000, k401: 80_000, wallet: 500 })];
    mount(<SecondHouse />);
    const walletBox = () => [...card("card-funding-plan").querySelectorAll("tbody tr")]
      .find((r) => r.textContent?.includes("Sample Wallet"))!.querySelector<HTMLInputElement>("input")!;
    expect(walletBox().disabled, "a saved draw on an unknown balance stays editable").toBe(false);
    fireEvent.focus(walletBox());
    fireEvent.change(walletBox(), { target: { value: "0" } });
    fireEvent.blur(walletBox());
    expect(verdict().textContent, "the verdict leaves the unknown-balance state").toBe("fully funded ✓");
    expect(walletBox().disabled, "cleared: nothing can be drawn on it again").toBe(true);
    expect(within(card("card-scenario-tabs")).getAllByRole("tab")[0].textContent).not.toMatch(/⚠/);
  });
  it("emptying a draw box that already draws nothing saves nothing; emptying one that draws saves the clearing", () => {
    useFunding(scenario("a", "Lake house", { chk: 10_000, sav: 50_000 }));
    mount(<SecondHouse />);
    const box = (name: string) => [...card("card-funding-plan").querySelectorAll("tbody tr")]
      .find((r) => r.textContent?.includes(name))!.querySelector<HTMLInputElement>("input")!;
    const empty = (name: string) => {
      fireEvent.focus(box(name));
      fireEvent.change(box(name), { target: { value: "" } });
      fireEvent.blur(box(name));
    };
    empty("Alex's 401(k)");
    expect(state.draws, "a box already at no draw").toEqual([]);
    empty("Joint Checking");
    expect(state.draws, "a box with a draw").toEqual([["chk", 0]]);
  });
  it("mortgage insurance is one untoned tile at every down payment: a short headline, the sentence under it", () => {
    const tile = () => [...card("kpis-guardrails").children].find((t) => t.textContent?.startsWith("Mortgage insurance"))!;
    useFunding(scenario("a", "Lake house", FUNDED));                                    // the default deal: 20% down
    mount(<SecondHouse />);
    expect(tile().textContent).toBe("Mortgage insuranceNot includedNone in the payment shown");
    expect(tile().querySelector(".text-emerald-400, .text-amber-400, .text-red-400"), "no tone").toBeNull();
    cleanup();
    useFunding({ ...scenario("a", "Lake house", FUNDED), inputs: { ...DEFAULT_INPUTS, downPct: 10 } });
    mount(<SecondHouse />);
    expect(tile().textContent).toBe("Mortgage insuranceNot included"
      + "Under 20% down a lender may require mortgage insurance; its cost is not in the payment shown.");
    expect(document.body.textContent).not.toMatch(/not required|PMI likely|lenders typically/);
  });
  it("a scenario that does not close never wins the liquid cash it leaves", () => {
    // Hill cabin draws only 40,000 of cash and leaves 22,000, but is 100,000 short; Shore cottage closes and leaves 2,000
    useFunding(scenario("b", "Hill cabin", SHORT), scenario("c", "Shore cottage", FUNDED));
    mount(<SecondHouse />);
    const left = () => [...[...card("card-scenario-comparison").querySelectorAll("tr")]
      .find((r) => r.textContent?.startsWith("Liquid cash left"))!.querySelectorAll("td")];
    expect(left().map((c) => c.textContent)).toEqual(["Liquid cash left", fmtUSD0(22_000), fmtUSD0(2_000)]);
    expect(left()[1].className).not.toMatch(/emerald/);
    expect(left()[2].className).toMatch(/emerald/);
    cleanup();
    useFunding(scenario("b", "Hill cabin", SHORT), scenario("a", "Lake house", OVERDRAWS));
    mount(<SecondHouse />);
    expect(left().some((c) => /emerald/.test(c.className)), "no scenario closes: no best").toBe(false);
  });
  it("cash left a hair under zero is none; a few cents under is a shortfall said in cents", () => {
    useFunding(scenario("a", "Lake house", { chk: 12_000, sav: 50_000.004, k401: 77_999.996 }));
    mount(<SecondHouse />);
    const tile = (label: string) => [...card("kpis-guardrails").children].find((t) => t.textContent?.startsWith(label))?.textContent;
    expect(tile("Liquid cash after cash draws")).not.toMatch(/short/);
    cleanup();
    useFunding(scenario("a", "Lake house", { chk: 12_000, sav: 50_000.3, k401: 77_999.7 }));
    mount(<SecondHouse />);
    expect(tile("Liquid cash after cash draws")).toMatch(new RegExp(`^Liquid cash after cash draws${fmtUSD(0.3).replace("$", "\\$")} short`));
    expect(tile("Essentials cushion")).toContain(`the cash draws are ${fmtUSD(0.3)} more than today's liquid cash`);
  });
  it("while the scenarios load, the line is a busy status, announced once", () => {
    useFunding(scenario("a", "Lake house", FUNDED));
    state.loading = true;
    mount(<SecondHouse />);
    const line = screen.getByText("Loading scenarios…");
    expect(line.getAttribute("role")).toBe("status");
    expect(line.getAttribute("aria-busy")).toBe("true");
    expect(screen.getAllByRole("status").filter((s) => /Loading scenarios/.test(s.textContent ?? ""))).toHaveLength(1);
  });
});

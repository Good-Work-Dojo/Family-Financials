// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { AssetRow, Assumptions, CashflowMonth, Snapshot } from "../data/types";
import { cfmFactory, mortgageFactory, snapFactory } from "./__fixtures__";
import { foldedDebt } from "./comparableSnapshots";
import { debtBarLabel, decompose, firstMortgageBalance, mortgageGap, snapshotOnOrBefore, waterfallLabels } from "./decomposition";

const snapBase = snapFactory({ source: "test", total_excl_house_car: 0, liquid_total: 0 });
const snap = (date: string, byClass: Record<string, number>,
              mortgageIncluded = true): Snapshot =>
  snapBase({ snapshot_date: date, by_class: byClass, mortgage_included: mortgageIncluded,
             total: Object.values(byClass).reduce((s, v) => s + v, 0) });

const cfm = cfmFactory({ savings_rate: null });
const month = (m: string, income: number, spend: number, full = true): CashflowMonth =>
  cfm({ month: m, income, spend, full_coverage: full });

const noMortgage = { mortgage: { schedule: [] } } as unknown as Assumptions;

describe("decompose", () => {
  it("components sum exactly to the total delta (residual balances)", () => {
    const from = snap("2025-06-15", { "Stocks/ETF": 500_000, House: 1_000_000, Mortgage: -300_000 });
    const to = snap("2026-06-15", { "Stocks/ETF": 560_000, House: 1_000_000, Mortgage: -290_000 });
    const cashflow = [month("2025-07", 20_000, 15_000), month("2026-06", 20_000, 15_000)];
    const d = decompose(from, to, cashflow, noMortgage);
    expect(d.delta).toBeCloseTo(70_000);
    expect(d.cashSavings).toBeCloseTo(10_000);
    expect(d.debt).toBeCloseTo(10_000);            // mortgage paydown improves NW
    expect(d.home).toBe(0);
    expect(d.cashSavings + d.retirement + d.home + d.debt + d.market)
      .toBeCloseTo(d.delta);
    expect(d.market).toBeCloseTo(50_000);          // stock growth is the residual
    expect(d.mortgageExcluded).toBe(false);
    expect(d.coverageIncomplete).toBe(false);
  });

  it("mortgage-boundary rule: Δ across the flip is excluded, schedule used", () => {
    const from = snap("2025-06-15", { Cash: 100_000 }, false);        // no mortgage booked
    const to = snap("2026-06-15", { Cash: 100_000, Mortgage: -290_000 }, true);
    const assumptions = { mortgage: mortgageFactory({ schedule: [
      { date: "2025-12-31", principal_balance: 292_000 },
      { date: "2026-05-31", principal_balance: 290_000 },
    ] }) } as unknown as Assumptions;
    const d = decompose(from, to, [], assumptions);
    expect(d.mortgageExcluded).toBe(true);
    expect(d.debt).toBeCloseTo(2_000);             // the schedule's principal Δ, NOT -290k
    // the artifact lands in the residual where the footnote explains it
    expect(d.cashSavings + d.retirement + d.home + d.debt + d.market)
      .toBeCloseTo(d.delta);
  });

  it("a live schedule (the loan account's balance at each snapshot date it is in) gives the paydown once", () => {
    // the account first reports in February: January's month end holds no mortgage row, and the schedule no row
    const from = snap("2030-01-31", { Cash: 50_000 }, false);
    const to = snap("2030-04-10", { Cash: 52_000, Mortgage: -249_000 }, true);
    const schedule = [{ date: "2030-02-28", principal_balance: 250_000 }, { date: "2030-03-31", principal_balance: 249_500 },
      { date: "2030-04-10", principal_balance: 249_000 }];
    const assumptions = { mortgage: mortgageFactory({ balance: 249_000, balance_as_of: "2030-04-08", schedule }) } as unknown as Assumptions;
    const d = decompose(from, to, [], assumptions);
    expect(d.mortgageExcluded).toBe(true);
    expect(d.debt).toBeCloseTo(1_000);             // 250,000 at the first month end that holds it, 249,000 now
    expect(d.market).toBeCloseTo(2_000 - 250_000); // the cash rise and the balance appearing, which the footnote explains
    expect(d.cashSavings + d.retirement + d.home + d.debt + d.market).toBeCloseTo(d.delta);
    // both snapshots hold the mortgage: the class Δ is the paydown, and the schedule is not added on top of it
    const mid = snap("2030-02-28", { Cash: 50_000, Mortgage: -250_000 }, true);
    const both = decompose(mid, to, [], assumptions);
    expect(both.mortgageExcluded).toBe(false);
    expect(both.debt).toBeCloseTo(1_000);
    expect(both.market).toBeCloseTo(2_000);
  });

  it("flags incomplete coverage (and empty windows)", () => {
    const from = snap("2025-06-15", { Cash: 10 });
    const to = snap("2026-06-15", { Cash: 20 });
    expect(decompose(from, to, [month("2025-08", 5, 3, false)], noMortgage)
      .coverageIncomplete).toBe(true);
    expect(decompose(from, to, [], noMortgage).coverageIncomplete).toBe(true);
  });

  it("retirement contributions reduce the residual when payroll is configured", () => {
    const from = snap("2025-12-31", { Retirement: 100_000 });
    const to = snap("2026-06-30", { Retirement: 112_000 });
    const cashflow = [month("2026-01", 0, 0), month("2026-02", 0, 0)];
    const phases = [{ person: "Alex", from: "2026-01", to: null,
      gross_annual: 240_000, employee_401k_pct: 10, employer_match_pct: 5 }];
    const d = decompose(from, to, cashflow, noMortgage, phases);
    expect(d.retirement).toBeCloseTo(6_000);       // 2 months × $3k
    expect(d.market).toBeCloseTo(6_000);           // growth beyond contributions
  });
});

describe("snapshotOnOrBefore", () => {
  const snaps = [snap("2024-01-01", {}), snap("2025-01-01", {}), snap("2026-01-01", {})];
  it("picks the latest snapshot on/before the date", () => {
    expect(snapshotOnOrBefore(snaps, "2025-06-01")!.snapshot_date).toBe("2025-01-01");
    expect(snapshotOnOrBefore(snaps, "2026-01-01")!.snapshot_date).toBe("2026-01-01");
  });
  it("falls back to the earliest when the date predates all snapshots", () => {
    expect(snapshotOnOrBefore(snaps, "2023-01-01")!.snapshot_date).toBe("2024-01-01");
  });
  it("null on empty", () => {
    expect(snapshotOnOrBefore([], "2025-01-01")).toBeNull();
  });
});

describe("firstMortgageBalance", () => {
  it("is the mortgage owed at the earliest snapshot that includes it, as a positive number", () => {
    const snaps = [
      snap("2025-06-15", { "Stocks/ETF": 500_000 }, false),
      snap("2026-01-15", { "Stocks/ETF": 520_000, Mortgage: -300_000 }),
      snap("2026-06-15", { "Stocks/ETF": 560_000, Mortgage: -290_000 }),
    ];
    expect(firstMortgageBalance(snaps)).toBe(300_000);
  });
  it("does not depend on array order", () => {
    const snaps = [
      snap("2026-06-15", { Mortgage: -290_000 }),
      snap("2026-01-15", { Mortgage: -300_000 }),
    ];
    expect(firstMortgageBalance(snaps)).toBe(300_000);
  });
  it("is null when no snapshot includes the mortgage, or it carries no usable balance", () => {
    expect(firstMortgageBalance([])).toBeNull();
    expect(firstMortgageBalance([snap("2025-06-15", { Mortgage: -300_000 }, false)])).toBeNull();
    expect(firstMortgageBalance([snap("2026-01-15", { "Stocks/ETF": 1 })])).toBeNull();
    expect(firstMortgageBalance([snap("2026-01-15", { Mortgage: 0 })])).toBeNull();
  });
});

describe("mortgageGap", () => {
  const sched = (...dates: string[]) => dates.map((date) => ({ date }));
  it("is null once every snapshot subtracts the mortgage, so no page prints a † note", () => {
    expect(mortgageGap([snap("2022-07-25", { Mortgage: -1 }), snap("2026-01-15", { Mortgage: -1 })],
      sched("2020-06-30"))).toBeNull();
    expect(mortgageGap([])).toBeNull();
  });
  it("counts the snapshots that leave it out and names where the mortgage data starts", () => {
    const snaps = [snap("2019-03-01", {}, false), snap("2020-01-01", {}, false), snap("2022-07-25", { Mortgage: -1 })];
    expect(mortgageGap(snaps, sched("2021-02-01", "2020-06-30"))).toEqual(
      { excluded: 2, since: "2020-06-30", sinceLabel: "Jun 2020" });
  });
  it("without a schedule it falls back to the earliest snapshot that carries the mortgage, then to words", () => {
    const snaps = [snap("2026-02-06", { Mortgage: -1 }), snap("2025-07-31", {}, false), snap("2025-12-15", { Mortgage: -1 })];
    expect(mortgageGap(snaps)).toEqual({ excluded: 1, since: "2025-12-15", sinceLabel: "Dec 2025" });
    expect(mortgageGap([snap("2025-07-31", {}, false)])).toEqual({ excluded: 1, since: null, sinceLabel: "the mortgage data" });
  });
});

describe("waterfallLabels", () => {
  const parts = ["Cash savings", "Retirement", "Home", "Debt paid down*", "Market & other"];
  it("names every column in full on a desktop", () => {
    expect(waterfallLabels(parts, "2025-07-31", "2026-10-03", false)).toEqual([
      "Start (Jul 31, 2025)", ...parts, "Now (Oct 3, 2026)"]);
  });
  it("on a phone every name is short lines, so all of them fit a 390px axis", () => {
    const l = waterfallLabels(parts, "2025-07-31", "2026-10-03", true);
    expect(l).toEqual(["Start\nJul \u201925", "Cash\nsavings", "Retire-\nment", "Home", "Debt\npaid\ndown*",
      "Market\n& other", "Now\nOct \u201926"]);
    const debt = waterfallLabels(["Debt added", "Debt unchanged"], "2025-07-31", "2026-10-03", true);
    expect(debt.slice(1, 3)).toEqual(["Debt\nadded", "Debt\nunchan-\nged"]);
    for (const s of [...l, ...debt]) for (const line of s.split("\n")) expect(line.length, line).toBeLessThanOrEqual(8);
  });
});

describe("the debt bar: one footing, named by its sign", () => {
  const row = (over: Partial<AssetRow>): AssetRow => ({ account: "Broker", account_id: "brk", institution: "Example Brokerage",
    owner: "Joint", asset_class: "Stocks/ETF", value: 0, liquid: true, as_of: "2026-06-15", as_of_assumed: false,
    needs_review: false, source: "positions", manual_id: null, carried_back: false, ...over });
  // a month end holds the margin account as one row at the feed's balance (300,000, net of its loan); the newest
  // snapshot as its positions (320,000) beside the loan (-12,500), and the mortgage was paid down by 10,000
  const monthEnd = { ...snap("2025-06-30", { "Stocks/ETF": 300_000, Mortgage: -400_000 }),
    rows: [row({ value: 300_000, source: "simplefin_balance" }),
      row({ account: "Home Mortgage", account_id: "loan", asset_class: "Mortgage", value: -400_000, liquid: false })] };
  const newest = { ...snap("2026-06-15", { "Stocks/ETF": 320_000, Margin: -12_500, Mortgage: -390_000 }),
    rows: [row({ value: 320_000 }), row({ account: "Example Brokerage Margin", asset_class: "Margin", value: -12_500,
      liquid: false, source: "simplefin_balance" }),
    row({ account: "Home Mortgage", account_id: "loan", asset_class: "Mortgage", value: -390_000, liquid: false })] };
  it("folds a live margin account's loan into its account, so the bar compares the same debts at both ends", () => {
    expect(foldedDebt(newest).map((r) => r.account)).toEqual(["Example Brokerage Margin"]);
    expect(foldedDebt(monthEnd), "a month end holds the account as one row").toEqual([]);
    const d = decompose(monthEnd, newest, [], noMortgage);
    expect(d.debt, "the mortgage paid down, the margin loan not counted as new debt").toBeCloseTo(10_000);
    expect(d.marginFolded).toBe(true);
    // the account's own change (320,000 - 12,500 - 300,000) is in the residual, and the parts still sum to the change
    expect(d.market).toBeCloseTo(7_500);
    expect(d.cashSavings + d.retirement + d.home + d.debt + d.market).toBeCloseTo(d.delta);
    expect(debtBarLabel(d.debt)).toBe("Debt paid down");
  });
  it("a card's or a loan's own row is never folded: only a debt row beside other rows of its account", () => {
    const card = { ...snap("2026-06-15", { "Other Debt": -900 }), rows: [row({ account: "Card", account_id: "card",
      asset_class: "Other Debt", value: -900, liquid: false, source: "simplefin_balance" })] };
    expect(foldedDebt(card)).toEqual([]);
    expect(decompose(snap("2025-06-15", { "Other Debt": -300 }), card, [], noMortgage).marginFolded).toBe(false);
  });
  it("is named by its sign: paid down when debt fell, added when it rose, unchanged under a dollar", () => {
    const more = decompose(snap("2025-06-15", { "Other Debt": -1_000 }), snap("2026-06-15", { "Other Debt": -3_000 }), [], noMortgage);
    expect(more.debt).toBeCloseTo(-2_000);
    expect(debtBarLabel(more.debt)).toBe("Debt added");
    expect(debtBarLabel(0.4)).toBe("Debt unchanged");
    expect(debtBarLabel(-0.4)).toBe("Debt unchanged");
    expect(debtBarLabel(-0.5), "rounds as the bar's own figure does").toBe("Debt added");
    expect(debtBarLabel(2_000, true), "the footnote's mark").toBe("Debt paid down*");
  });
});

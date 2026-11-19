// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { CashflowMonth, CategoryRename } from "../data/types";
import { cfmFactory, txFactory, vocabAssumptions } from "./__fixtures__";
import { nextMonth } from "./agg";
import { categoryVocab } from "./categories";
import { passiveIncomeTTM } from "./passiveIncome";

const tx = txFactory({ category: "Income" });
const plain = categoryVocab(vocabAssumptions());
const cfm = cfmFactory();
/** one cash-flow month each from `from` through `to`, in coverage unless listed in `out` */
const months = (from: string, to: string, out: string[] = []): CashflowMonth[] => {
  const ms: CashflowMonth[] = [];
  for (let m = from; m <= to; m = nextMonth(m)) ms.push(cfm({ month: m, in_coverage: !out.includes(m) }));
  return ms;
};
// June 2030 is the month in progress and the data begins in January 2029, so the window is the twelve full months
// 2029-06-01 .. 2030-05-31
const IN_PROGRESS = "2030-06";
const CASHFLOW = months("2029-01", "2030-06");
const passive = (rows: Parameters<typeof passiveIncomeTTM>[0], vocab = plain) => passiveIncomeTTM(rows, vocab, CASHFLOW, IN_PROGRESS);

describe("passiveIncomeTTM", () => {
  it("sums passive income inside the trailing-12 window only", () => {
    const rows = [
      tx({ date: "2030-03-10", amount: 120, type: "dividend", subcategory: "Dividends" }),
      tx({ date: "2030-02-01", amount: 45, type: "interest", subcategory: "Interest" }),
      tx({ date: "2028-01-01", amount: 999, type: "dividend", subcategory: "Dividends" }),   // out of window
    ];
    const p = passive(rows)!;
    expect(p.dividends).toBe(120);
    expect(p.total).toBe(165);
    expect(p.months).toBe(12);
  });
  it("the window is the twelve full months before the month in progress: its first day counts, the day before it and the month in progress do not", () => {
    const rows = [
      tx({ date: "2029-06-01", amount: 7, type: "dividend", subcategory: "Dividends" }),     // the window's first day
      tx({ date: "2030-05-31", amount: 3, type: "interest", subcategory: "Interest" }),      // its last day
      tx({ date: "2029-05-31", amount: 999, type: "dividend", subcategory: "Dividends" }),   // the day before it
      tx({ date: "2030-06-05", amount: 999, type: "interest", subcategory: "Interest" }),    // the month in progress
      tx({ date: "2030-07-02", amount: 999, type: "interest", subcategory: "Interest" }),    // dated into a month not begun
    ];
    expect(passive(rows)).toEqual({ dividends: 7, interest: 3, total: 10, months: 12 });
  });
  it("counts income rows with a positive amount only", () => {
    const rows = [
      tx({ date: "2030-03-10", amount: 50, type: "dividend", subcategory: "Dividends" }),
      tx({ date: "2030-03-11", amount: -20, type: "dividend", subcategory: "Dividends" }),   // a reversal
      tx({ date: "2030-03-12", amount: 80, type: "transfer", subcategory: "Interest" }),     // not an income type
      tx({ date: "2030-03-13", amount: 900, type: "income", subcategory: "Tax Refund" }),    // income, but not passive
    ];
    expect(passive(rows)).toEqual({ dividends: 50, interest: 0, total: 50, months: 12 });
  });
  it("keeps summing passive income after its subcategories are renamed", () => {
    const rows = [
      tx({ date: "2030-03-10", amount: 120, type: "dividend", subcategory: "Dividend Income" }),
      tx({ date: "2030-02-01", amount: 45, type: "interest", subcategory: "Interest" }),
      tx({ date: "2030-01-15", amount: 30, type: "interest", subcategory: "Savings Interest" }),
      tx({ date: "2030-01-16", amount: 500, type: "income", subcategory: "Tax Refund" }),
    ];
    const renames: CategoryRename[] = [
      { from: { category: "Income", subcategory: "Dividends" }, to: { category: "Income", subcategory: "Dividend Income" } },
      { from: { category: "Income", subcategory: "Interest" }, to: { category: "Income", subcategory: "Savings Interest" } }];
    expect(passive(rows, categoryVocab(vocabAssumptions(renames))))
      .toEqual({ dividends: 120, interest: 75, total: 195, months: 12 });
    // the same rows read without the renames: the dividends are no passive income (the control)
    expect(passive(rows)!.dividends).toBe(0);
  });
  it("with fewer than twelve full months the window starts at the first full month and says how many it spans", () => {
    const rows = [
      tx({ date: "2030-02-10", amount: 20, type: "dividend", subcategory: "Dividends" }),    // before the coverage floor
      tx({ date: "2030-03-10", amount: 5, type: "dividend", subcategory: "Dividends" }),
    ];
    // March 2030 is the floor (January and February are before it), June is in progress: three full months
    const p = passiveIncomeTTM(rows, plain, months("2030-01", "2030-06", ["2030-01", "2030-02"]), IN_PROGRESS);
    expect(p).toEqual({ dividends: 5, interest: 0, total: 5, months: 3 });
  });
  it("with no full month there is no window: the figure is unknown (null), never $0 over months before the data", () => {
    const rows = [tx({ date: "2030-06-03", amount: 9, type: "interest", subcategory: "Interest" })];
    expect(passiveIncomeTTM(rows, plain, months("2030-06", "2030-06"), IN_PROGRESS)).toBeNull();
    expect(passiveIncomeTTM([], plain, [], null)).toBeNull();
  });
});

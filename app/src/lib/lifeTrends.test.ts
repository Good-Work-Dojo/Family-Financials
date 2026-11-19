// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { CashflowMonth, Snapshot } from "../data/types";
import { belowZeroSnapshot, cfmFactory, snapFactory } from "./__fixtures__";
import { assetsBy } from "./lifeSummary";
import { allocationTrend, burnTrend, liquidityTrend, savingsRateTrend } from "./lifeTrends";

const cfm = cfmFactory({ savings_rate: null });
const month = (m: string, income: number, spend: number,
               by: Record<string, number> = {}): CashflowMonth =>
  cfm({ month: m, income, spend, income_by_subcategory: by });

const snapBase = snapFactory({ source: "t", total_excl_house_car: 0 });
const snap = (date: string, byClass: Record<string, number>, liquid = 0): Snapshot =>
  snapBase({ snapshot_date: date, by_class: byClass, liquid_total: liquid,
             total: Object.values(byClass).reduce((s, v) => s + v, 0) });
/** the snapshot with one row per class at its value, as an export holds them */
const withRows = (s: Snapshot): Snapshot => ({ ...s, rows: Object.entries(s.by_class).map(([asset_class, value]) => ({
  account: asset_class, account_id: asset_class, institution: "B", owner: "Joint", asset_class, value, liquid: false,
  as_of: s.snapshot_date, as_of_assumed: false, needs_review: false, source: "bank_balance", manual_id: null,
  carried_back: false })) });

describe("lifeTrends", () => {
  it("savings rate uses a trailing window and skips pre-coverage months", () => {
    const t = savingsRateTrend([
      { ...month("2024-01", 99, 0), in_coverage: false },   // before the coverage floor — dropped
      month("2024-06", 10_000, 8_000),
      month("2024-07", 10_000, 6_000),
    ], "2024-08", 2);
    expect(t.map((p) => p.month)).toEqual(["2024-06", "2024-07"]);
    expect(t[0].value).toBeCloseTo(0.2);             // single-month window at the start
    expect(t[1].value).toBeCloseTo((20_000 - 14_000) / 20_000);
  });

  it("both lines end on the last full month: no point for the month in progress or a month after it", () => {
    const months = [month("2024-06", 10_000, 8_000), month("2024-07", 10_000, 6_000),
      month("2024-08", 10_000, 9_900), month("2024-09", 0, 40)];   // August in progress; one row dated into September
    expect(savingsRateTrend(months, "2024-08", 2).map((p) => p.month)).toEqual(["2024-06", "2024-07"]);
    expect(burnTrend(months, "2024-08", 6).map((p) => p.month)).toEqual(["2024-06", "2024-07"]);
    expect(savingsRateTrend(months, null), "no month in progress: no full month").toEqual([]);
  });

  it("savings rate is null when the window has no income", () => {
    expect(savingsRateTrend([month("2024-06", 0, 500)], "2024-07", 3)[0].value).toBeNull();
  });

  it("burn averages the trailing window", () => {
    const t = burnTrend([month("2024-06", 0, 6_000), month("2024-07", 0, 8_000)], "2024-08", 6);
    expect(t[1].value).toBe(7_000);
  });

  it("allocation restricts to the latest snapshot's top classes and % of positives", () => {
    const { classes, rows } = allocationTrend([
      withRows(snap("2024-01-01", { Cash: 100 })),
      withRows(snap("2026-01-01", { Cash: 100, "Stocks/ETF": 300, Mortgage: -200 })),
    ], 2);
    expect(classes).toEqual(["Stocks/ETF", "Cash"]);
    expect(rows[0].pct).toEqual({ "Stocks/ETF": 0, Cash: 100 });
    expect(rows[1].pct).toEqual({ "Stocks/ETF": 75, Cash: 25 });  // % of positive assets
  });

  it("allocation is of the snapshot's assets, the rows above zero, as Home's donut: a row below zero nets no class down", () => {
    // the overdrawn checking (−300) is a debt: Cash is the savings' 5,000 of the 415,000 of assets, never 4,700 of 414,700
    const { classes, rows } = allocationTrend([belowZeroSnapshot()]);
    expect(classes).toEqual(["House", "Stocks/ETF", "Cash"]);
    expect(rows[0].pct).toEqual({ House: 96.4, "Stocks/ETF": 2.4, Cash: 1.2 });
    expect(rows[0].pct).toEqual(Object.fromEntries(assetsBy(belowZeroSnapshot(), "asset_class")
      .map((l) => [l.name, +((l.value / 415_000) * 100).toFixed(1)])));
  });

  it("liquidity trend computes the liquid share of assets and keeps the liquid dollars and the assets beside it", () => {
    const t = liquidityTrend([snap("2026-01-01", { Cash: 200, House: 800 }, 200)]);
    expect(t[0].liquidPct).toBeCloseTo(0.2);
    expect(t[0].liquid).toBe(200);
    expect(t[0].assets).toBe(1000);
  });

  it("the share is of assets, not net worth: debts larger than the illiquid assets never push it past 100%", () => {
    // 500 cash, a 100 car, a 400 debt: net worth 200, of which the old share read 250%
    const s = { ...snap("2026-01-01", { Cash: 500, Car: 100, "Other Debt": -400 }, 500), assets_total: 600, liabilities_total: -400 };
    const [p] = liquidityTrend([s]);
    expect(p.liquidPct).toBeCloseTo(500 / 600);
    expect(p.assets).toBe(600);
  });

  it("a snapshot with no asset above zero has no liquid share (null), never 0%; its dollars stay", () => {
    const none = { ...snap("2026-01-01", { "Other Debt": -500 }, 0), assets_total: 0, liabilities_total: -500 };
    const t = liquidityTrend([none]);
    expect(t[0].liquidPct).toBeNull();
    expect(t[0].liquid).toBe(0);
    const zeroNet = { ...snap("2026-01-01", { Cash: 500, "Other Debt": -500 }, 500), assets_total: 500, liabilities_total: -500 };
    expect(liquidityTrend([zeroNet])[0].liquidPct, "a net worth of 0 still has assets to share").toBe(1);
  });
});

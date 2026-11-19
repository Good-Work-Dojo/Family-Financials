// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { CashflowMonth } from "../data/types";
import { cfmFactory } from "./__fixtures__";
import type { PayrollPhase } from "./prefs";
import {
  grossRateSeries, grossSavingsRate, monthlyRetirement, payrollConfigured,
} from "./payroll";

const cfm = cfmFactory({ savings_rate: null });
const month = (m: string, income: number, spend: number): CashflowMonth =>
  cfm({ month: m, income, spend });

const alex: PayrollPhase = {
  person: "Alex", from: "2024-01", to: null,
  gross_annual: 240_000, employee_401k_pct: 10, employer_match_pct: 5,
};
// 240k/12 = 20k gross/mo → 15% = $3,000/mo retirement

describe("payroll", () => {
  it("dormant until a phase carries real contributions", () => {
    expect(payrollConfigured([])).toBe(false);
    expect(payrollConfigured([{ ...alex, employee_401k_pct: 0, employer_match_pct: 0 }]))
      .toBe(false);
    expect(payrollConfigured([alex])).toBe(true);
  });

  it("month coverage is inclusive and to=null is ongoing", () => {
    const bounded = { ...alex, from: "2024-03", to: "2024-05" };
    expect(monthlyRetirement([bounded], "2024-02")).toBe(0);
    expect(monthlyRetirement([bounded], "2024-03")).toBeCloseTo(3000);
    expect(monthlyRetirement([bounded], "2024-05")).toBeCloseTo(3000);
    expect(monthlyRetirement([bounded], "2024-06")).toBe(0);
    expect(monthlyRetirement([alex], "2030-01")).toBeCloseTo(3000);
  });

  it("overlapping phases (two earners) sum", () => {
    const sam: PayrollPhase = { person: "Sam", from: "2026-02", to: null,
      gross_annual: 120_000, employee_401k_pct: 6, employer_match_pct: 3 };
    // sam: 10k/mo * 9% = $900
    expect(monthlyRetirement([alex, sam], "2026-03")).toBeCloseTo(3900);
  });

  it("gross rate adds retirement to both sides of the ratio", () => {
    // take-home 10k, spend 8k → cash rate 20%; +3k retirement:
    // (10-8+3)/(10+3) = 5/13 ≈ 38.5%
    const rate = grossSavingsRate([month("2025-01", 10_000, 8_000)], [alex]);
    expect(rate).toBeCloseTo(5 / 13, 4);
  });

  it("degrades to the cash-basis rate with no phases", () => {
    const rate = grossSavingsRate([month("2025-01", 10_000, 8_000)], []);
    expect(rate).toBeCloseTo(0.2, 6);
  });

  it("null when the window has no income", () => {
    expect(grossSavingsRate([month("2025-01", 0, 500)], [])).toBeNull();
    expect(grossRateSeries([month("2025-01", 0, 500)], [])).toEqual([null]);
  });

  it("series matches the aggregate for a single month", () => {
    const [v] = grossRateSeries([month("2025-01", 10_000, 8_000)], [alex]);
    expect(v).toBeCloseTo(5 / 13, 3);
  });
});

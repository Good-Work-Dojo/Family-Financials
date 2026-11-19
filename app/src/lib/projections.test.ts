// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { amortize, fireCrossing, fireNumber, loanEffectivePct, monthlyRate, projectGrowth } from "./projections";

describe("loanEffectivePct", () => {
  it("a loan's APR accrued monthly is its effective yearly rate, so growth at it is the loan's own month by month", () => {
    expect(loanEffectivePct(6.125)).toBeCloseTo(6.2999, 3);
    expect(loanEffectivePct(0)).toBe(0);
    // $500 a month for 25 years at the loan's twelfth of 6.125% a month, compounded month by month
    let byMonth = 0;
    for (let m = 0; m < 300; m++) byMonth = byMonth * (1 + 6.125 / 1200) + 500;
    const grown = projectGrowth({ principal: 0, monthlyContribution: 500, annualReturnPct: loanEffectivePct(6.125), years: 25 });
    expect(grown[grown.length - 1].balance).toBeCloseTo(byMonth, 6);
    expect(Math.round(byMonth)).toBe(353_239);
  });
});

describe("projectGrowth", () => {
  it("matches closed-form future value of an annuity", () => {
    const r = monthlyRate(7);
    const n = 120;
    const P = 10000, C = 500;
    const fv = P * (1 + r) ** n + C * (((1 + r) ** n - 1) / r);
    const series = projectGrowth({ principal: P, monthlyContribution: C, annualReturnPct: 7, years: 10 });
    expect(series[n].balance).toBeCloseTo(fv, 4);
    expect(series[n].contributed).toBeCloseTo(C * n, 4);
  });

  it("handles zero return", () => {
    const series = projectGrowth({ principal: 1000, monthlyContribution: 100, annualReturnPct: 0, years: 1 });
    expect(series[12].balance).toBeCloseTo(1000 + 1200);
  });

  it("uses geometric monthly compounding, not r/12", () => {
    const series = projectGrowth({ principal: 10000, monthlyContribution: 0, annualReturnPct: 12, years: 1 });
    expect(series[12].balance).toBeCloseTo(11200, 0);  // exactly 12%/yr
  });
});

describe("amortize", () => {
  // $287,409 @ 6.5%, $2,644/mo -> compare against standard amortization math
  it("pays off and accumulates plausible interest", () => {
    const res = amortize({ balance: 287409, annualRatePct: 6.5, monthlyPayment: 2644 });
    expect(res.schedule[res.schedule.length - 1].balance).toBe(0);
    // closed-form n = -log(1 - rB/P) / log(1+r)
    const r = 0.065 / 12;
    const n = Math.ceil(-Math.log(1 - (r * 287409) / 2644) / Math.log(1 + r));
    expect(res.payoffMonths).toBe(n);
  });

  it("extra payments strictly shorten payoff and reduce interest", () => {
    const base = amortize({ balance: 200000, annualRatePct: 6, monthlyPayment: 1500 });
    const extra = amortize({ balance: 200000, annualRatePct: 6, monthlyPayment: 1500, extraMonthly: 300 });
    expect(extra.payoffMonths).toBeLessThan(base.payoffMonths);
    expect(extra.totalInterest).toBeLessThan(base.totalInterest);
  });

  it("lump sum applies at the given month", () => {
    const res = amortize({ balance: 100000, annualRatePct: 5, monthlyPayment: 1000, lumpSum: { month: 1, amount: 50000 } });
    expect(res.schedule[0].balance).toBeLessThan(50500);
  });

  it("throws when payment doesn't cover interest", () => {
    expect(() => amortize({ balance: 300000, annualRatePct: 8, monthlyPayment: 100 })).toThrow();
  });
});

describe("FIRE", () => {
  it("number = spending / withdrawal rate", () => {
    expect(fireNumber(120000, 4)).toBe(3_000_000);
  });
  it("crossing is consistent with the growth series", () => {
    const series = projectGrowth({ principal: 500000, monthlyContribution: 3000, annualReturnPct: 7, years: 40 });
    const m = fireCrossing(series, 2_000_000)!;
    expect(series[m].balance).toBeGreaterThanOrEqual(2_000_000);
    expect(series[m - 1].balance).toBeLessThan(2_000_000);
  });
});

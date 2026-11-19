// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Pure projection math — no React, no charts. The most-tested code in the app.
// All rates are annual percentages (7 = 7%); compounding is geometric monthly:
// monthlyRate = (1+r)^(1/12) - 1, NOT r/12.

export interface GrowthPoint { month: number; balance: number; contributed: number; growth: number }

export function monthlyRate(annualPct: number): number {
  return Math.pow(1 + annualPct / 100, 1 / 12) - 1;
}

export function projectGrowth(p: {
  principal: number;
  monthlyContribution: number;
  annualReturnPct: number;
  years: number;
  annualContributionGrowthPct?: number;
}): GrowthPoint[] {
  const r = monthlyRate(p.annualReturnPct);
  const cg = monthlyRate(p.annualContributionGrowthPct ?? 0);
  const out: GrowthPoint[] = [{ month: 0, balance: p.principal, contributed: 0, growth: 0 }];
  let bal = p.principal;
  let contrib = p.monthlyContribution;
  let contributed = 0;
  for (let m = 1; m <= p.years * 12; m++) {
    bal = bal * (1 + r) + contrib;
    contributed += contrib;
    contrib *= 1 + cg;
    out.push({ month: m, balance: bal, contributed, growth: bal - p.principal - contributed });
  }
  return out;
}

/** The yearly rate a loan's APR comes to: the loan accrues a twelfth of it each month on the balance (amortize's
 *  convention), so a year's is (1 + APR/12)^12 − 1. A prepaid dollar stops exactly that, so set beside money growing at
 *  a yearly rate (projectGrowth) the two are on one footing. */
export function loanEffectivePct(aprPct: number): number {
  return (Math.pow(1 + aprPct / 1200, 12) - 1) * 100;
}

export interface AmortizationResult {
  schedule: { month: number; interest: number; principal: number; balance: number }[];
  payoffMonths: number;
  totalInterest: number;
}

export function amortize(p: {
  balance: number;
  annualRatePct: number;
  monthlyPayment: number;
  extraMonthly?: number;
  lumpSum?: { month: number; amount: number };
}): AmortizationResult {
  const r = p.annualRatePct / 100 / 12;   // note: mortgage interest IS r/12 by convention
  const pay = p.monthlyPayment + (p.extraMonthly ?? 0);
  if (p.balance > 0 && pay <= p.balance * r) {
    throw new Error("the payment does not pay the balance down");
  }
  const schedule: AmortizationResult["schedule"] = [];
  let bal = p.balance;
  let totalInterest = 0;
  let month = 0;
  while (bal > 0 && month < 12 * 80) {
    month++;
    const interest = bal * r;
    let principal = Math.min(pay - interest, bal);
    if (p.lumpSum && month === p.lumpSum.month) principal = Math.min(principal + p.lumpSum.amount, bal);
    bal -= principal;
    totalInterest += interest;
    schedule.push({ month, interest, principal, balance: bal });
  }
  return { schedule, payoffMonths: month, totalInterest };
}

export function fireNumber(annualCoreSpending: number, withdrawalRatePct: number): number {
  return annualCoreSpending / (withdrawalRatePct / 100);
}

/** first month the growth series crosses target, or null */
export function fireCrossing(series: GrowthPoint[], target: number): number | null {
  for (const pt of series) if (pt.balance >= target) return pt.month;
  return null;
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// True savings rate: bank data only sees take-home pay, so pre-tax 401(k)
// deferrals and employer match are invisible — the headline savings rate is
// systematically understated. Payroll phases (entered in-app, prefs store)
// add those flows back:
//
//   total savings rate = (take-home − spend + 401k + match)
//                        ─────────────────────────────────
//                        (take-home + 401k + match)
//
// Take-home basis + retirement contributions; taxes are NOT modeled. `invested`
// is deliberately excluded — transfers to brokerage come out of take-home and
// are already inside (income − spend). Phases are month-granular (YYYY-MM,
// inclusive); a phase with to=null is ongoing.
import type { CashflowMonth } from "../data/types";
import type { PayrollPhase } from "./prefs";

export function payrollConfigured(phases: PayrollPhase[]): boolean {
  return phases.some((p) => p.gross_annual > 0
    && (p.employee_401k_pct > 0 || p.employer_match_pct > 0));
}

function covers(p: PayrollPhase, month: string): boolean {
  // an ongoing phase (no `to`) covers every month from its first
  return p.from.slice(0, 7) <= month && (p.to === null || month <= p.to.slice(0, 7));
}

/** $ of retirement contributions (employee deferral + employer match) landing
 *  in a given YYYY-MM month across all covering phases. */
export function monthlyRetirement(phases: PayrollPhase[], month: string): number {
  let sum = 0;
  for (const p of phases) {
    if (!covers(p, month)) continue;
    sum += p.gross_annual / 12 * (p.employee_401k_pct + p.employer_match_pct) / 100;
  }
  return sum;
}

/** Gross-basis savings rate over a set of cashflow months; null when there is
 *  no income in the window (mirrors the cash-basis savingsRate convention). */
export function grossSavingsRate(months: CashflowMonth[],
                                 phases: PayrollPhase[]): number | null {
  let income = 0, spend = 0, retire = 0;
  for (const m of months) {
    income += m.income;
    spend += m.spend;
    retire += monthlyRetirement(phases, m.month);
  }
  const grossIncome = income + retire;
  if (grossIncome <= 0) return null;
  return (income - spend + retire) / grossIncome;
}

/** Per-month gross savings rate series for charting (null months mirror the
 *  exported savings_rate convention). */
export function grossRateSeries(months: CashflowMonth[],
                                phases: PayrollPhase[]): (number | null)[] {
  return months.map((m) => {
    const retire = monthlyRetirement(phases, m.month);
    const grossIncome = m.income + retire;
    if (grossIncome <= 0) return null;
    return +((m.income - m.spend + retire) / grossIncome).toFixed(4);
  });
}

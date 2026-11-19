// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// 12-month cash flow forecast: trailing baselines + month-of-year seasonality.
// An estimate, not a promise — render with the same `approx` honesty as the
// rest of the app. Seasonality only uses coverage-reliable months.
// Promoted schedules are counted once: their past charges leave the baseline,
// their expected dates add them back.
// The forecast starts from the liquid cash of a balance dated `asOf`, which already holds every day up to that date, so
// a first month that date falls in adds only its rest (`forecastStart`).
//
// A schedule's future charges are the bills chart's (lib/obligations.ts expectedCharges): the dates the export gives
// (a monthly bill on its own day of the month), then calendar months for monthly / quarterly / annual schedules and
// the median gap for weekly / biweekly ones, so the forecast and the bills chart put a charge in the same month.
import type { CashflowMonth, RecurringItem, Transaction } from "../data/types";
import { daysInMonth, isSpend, nextMonth, runRateMonths } from "./agg";
import { expectedCharges } from "./obligations";
import { mean, stdev } from "./stats";

/** the full months of cash flow a forecast needs: with fewer, forecastCashflow gives null */
export const FORECAST_MIN_MONTHS = 6;

export interface ForecastMonth {
  month: string;
  income: number;
  spend: number;              // baseline × seasonality + scheduled
  scheduled: number;          // Σ promoted schedules expected this month; 0 without schedules
  net: number;
  cumCash: number;            // projected liquid cash
  low: number;                // cum ± stdev·√k uncertainty band (baseline part only)
  high: number;
  /** the days of the month the forecast counts: all of them, except in a first month the starting balance already
   *  reaches into (`CashForecast.partialFirst`) */
  days: number;
}

/** the first projected month when it is the rest of a month the starting balance already reaches into: the days after
 *  the balance's date (`asOf`) out of the month's */
export interface PartialFirstMonth { month: string; asOf: string; days: number; of: number }

export interface CashForecast {
  months: ForecastMonth[];
  baselineIncome: number;     // mean of trailing 3 full months
  baselineSpend: number;      // mean of trailing 12 NON-scheduled spend, before seasonality
  /** the full months `baselineSpend` averages: 12 once the history is that long, else as many as there are (at least
   *  FORECAST_MIN_MONTHS), which its label states */
  spendMonths: number;
  netStdev: number;
  /** first month whose LOW band dips under the floor (conservative), or null */
  floorBreachMonth: string | null;
  seasonality: number[];      // index 0 = January
  /** the first month is the rest of a month the data reaches into (null: every month is whole) */
  partialFirst: PartialFirstMonth | null;
}

const key = (vendor: string, account: string) => `${vendor}\u0000${account}`;

/** scheduled (vendor, account) pairs' past charges by month — what leaves the baseline. Keys by vendor and
 *  account, which every part of a split purchase copies, so the category view and the vendor view give the
 *  same map (pinned). */
export function scheduledHistory(schedules: readonly RecurringItem[], txns: readonly Transaction[]): Map<string, number> {
  const keys = new Set(schedules.map((r) => key(r.vendor, r.account_id)));
  const out = new Map<string, number>();
  if (!keys.size) return out;
  for (const t of txns) {
    if (!isSpend(t) || t.amount >= 0 || !t.vendor || !keys.has(key(t.vendor, t.account))) continue;
    const ym = t.date.slice(0, 7);
    out.set(ym, (out.get(ym) ?? 0) - t.amount);
  }
  return out;
}

/** future charge dates up to (and a little past) month `last`: the bills chart's projection (`expectedCharges`); none
 *  for a row with no expected date, the same inactive rule as scheduleStatus */
export function chargeDates(r: RecurringItem, last: string): string[] {
  return r.expected_dates.length ? expectedCharges(r, `${last}-31`) : [];
}

/** each schedule's future charges at typical_amount, bucketed by month within [first, last]; a charge dated on or
 *  before `after` (the starting balance's date) is in that balance already, so it is not counted again */
function scheduledAhead(schedules: readonly RecurringItem[], first: string, last: string,
                        after: string | null): Map<string, number> {
  const out = new Map<string, number>();
  for (const r of schedules) {
    for (const x of chargeDates(r, last)) {
      const ym = x.slice(0, 7);
      if (ym >= first && ym <= last && (after === null || x > after)) out.set(ym, (out.get(ym) ?? 0) + r.typical_amount);
    }
  }
  return out;
}

/** Where the forecast starts. The month after the history's last full month (`afterHistory`, usually the month in
 *  progress), unless the starting balance is dated in a later month (`asOf`): that month then. A start month the
 *  balance's date falls in is only its rest - the days after `asOf`, prorated - since the balance already holds the
 *  days up to it; when no day is left (`asOf` is the month's last day) the forecast starts with the next month, whole.
 *  Without `asOf` every month is whole. */
export function forecastStart(afterHistory: string, asOf: string | null): { first: string; partial: PartialFirstMonth | null } {
  if (asOf === null) return { first: afterHistory, partial: null };
  const reached = asOf.slice(0, 7);
  if (reached < afterHistory) return { first: afterHistory, partial: null };
  const of = daysInMonth(reached);
  const days = of - Number(asOf.slice(8, 10));
  if (days <= 0) return { first: nextMonth(reached), partial: null };
  return { first: reached, partial: { month: reached, asOf, days, of } };
}

export function forecastCashflow(p: {
  cashflow: CashflowMonth[];
  /** the month in progress (agg.ts `monthInProgress`): the history is the full months before it, and the forecast
   *  starts with it; null (no row) gives no forecast */
  inProgress: string | null;
  /** the data's first day (agg.ts `dataFirstDay`): the history is the full months a planning run-rate reads
   *  (`runRateMonths`), so a first month the data covers only in part is no month of it, and the count of months it
   *  has is the one Projections' line states */
  firstDay: string | null;
  startCash: number;
  /** the date of the balance `startCash` is read from (the newest snapshot's date): it already holds every day up to
   *  that date - a bank feed's balance can include a day or two the transactions do not show yet -, so a first month the
   *  date falls in counts only the days after it (`forecastStart`); null or absent, every month is whole */
  asOf?: string | null;
  floor?: number;
  horizonMonths?: number;
  /** promoted schedules (schedule === true) are counted once — removed from the baseline,
   *  added back at their future charge dates (`chargeDates`: the bills chart's, from the exported expected dates) */
  schedules?: readonly RecurringItem[];
  transactions?: readonly Transaction[];
}): CashForecast | null {
  const history = runRateMonths(p.cashflow, p.inProgress, p.firstDay);
  const hist24 = history.slice(-24);
  const hist12 = history.slice(-12);
  const hist3 = history.slice(-3);
  if (hist12.length < FORECAST_MIN_MONTHS) return null;

  const promoted = (p.schedules ?? []).filter((r) => r.schedule);
  const past = scheduledHistory(promoted, p.transactions ?? []);
  const nonScheduled = (m: CashflowMonth) => Math.max(0, m.spend - (past.get(m.month) ?? 0));

  const avgSpend = mean(hist24.map(nonScheduled));
  const byCal = new Map<number, number[]>();
  for (const m of hist24) {
    const c = Number(m.month.slice(5, 7));
    byCal.set(c, [...(byCal.get(c) ?? []), nonScheduled(m)]);
  }
  const seasonality = Array.from({ length: 12 }, (_, i) => {
    const xs = byCal.get(i + 1);
    return xs?.length && avgSpend > 0 ? mean(xs) / avgSpend : 1;
  });

  const baselineIncome = mean(hist3.map((m) => m.income));
  const baselineSpend = mean(hist12.map(nonScheduled));
  const netStdev = stdev(hist12.map((m) => m.income - nonScheduled(m)));

  const horizon = p.horizonMonths ?? 12;
  const asOf = p.asOf ?? null;
  const { first, partial } = forecastStart(nextMonth(hist12[hist12.length - 1].month), asOf);
  let last = first;
  for (let k = 1; k < horizon; k++) last = nextMonth(last);
  const ahead = scheduledAhead(promoted, first, last, asOf);

  const months: ForecastMonth[] = [];
  let ym = first;
  let cum = p.startCash;
  let floorBreachMonth: string | null = null;
  // the months elapsed at each step's end: a prorated first month is that share of one
  let elapsed = 0;
  for (let k = 1; k <= horizon; k++) {
    const share = k === 1 && partial !== null ? partial.days / partial.of : 1;
    elapsed += share;
    const scheduled = ahead.get(ym) ?? 0;
    const income = baselineIncome * share;
    const spend = baselineSpend * seasonality[Number(ym.slice(5, 7)) - 1] * share + scheduled;
    const net = income - spend;
    cum += net;
    const band = netStdev * Math.sqrt(elapsed);
    months.push({
      month: ym, income, spend, scheduled, net,
      cumCash: cum, low: cum - band, high: cum + band,
      days: k === 1 && partial !== null ? partial.days : daysInMonth(ym),
    });
    if (floorBreachMonth === null && p.floor !== undefined && cum - band < p.floor) {
      floorBreachMonth = ym;
    }
    ym = nextMonth(ym);
  }
  return { months, baselineIncome, baselineSpend, spendMonths: hist12.length, netStdev, floorBreachMonth, seasonality,
    partialFirst: partial };
}

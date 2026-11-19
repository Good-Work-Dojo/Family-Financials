// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Trends: the headline metrics (once the Life Report's) recomputed HISTORICALLY from
// already-loaded exports — no server archive, no duplicated ETL logic, and
// backfill for free. Only genuinely rewindable metrics appear here (liquidity,
// allocation, savings rate, burn; net worth over time is Home's chart and income
// by stream is Cash Flow → Overview's); current-only data (holdings
// concentration, mortgage summary) is excluded rather than fabricated.
import type { CashflowMonth, Snapshot } from "../data/types";
import { fullMonths } from "./agg";
import { assetsBy, liquidity } from "./lifeSummary";

export interface TrendPoint {
  month: string;               // YYYY-MM
  value: number | null;
}

/** trailing window of up to `window` months ending at index `i` */
const trailing = (months: CashflowMonth[], i: number, window: number) =>
  months.slice(Math.max(0, i - window + 1), i + 1);

/** Trailing-N-month savings rate per full month ((Σincome − Σspend)/Σincome over the window ending at that month); null
 *  while income is zero. The line ends on the last full month before `inProgress` (the month in progress, agg.ts
 *  `monthInProgress`): a window holding a month still in progress would set its half-paid bills against whole months. */
export function savingsRateTrend(cashflow: CashflowMonth[], inProgress: string | null, window = 3): TrendPoint[] {
  const months = fullMonths(cashflow, inProgress);
  return months.map((m, i) => {
    const win = trailing(months, i, window);
    const income = win.reduce((s, x) => s + x.income, 0);
    const spend = win.reduce((s, x) => s + x.spend, 0);
    return { month: m.month, value: income > 0 ? +((income - spend) / income).toFixed(4) : null };
  });
}

/** Trailing-N average monthly spend ("burn"), per full month, ending on the last full month before `inProgress`. */
export function burnTrend(cashflow: CashflowMonth[], inProgress: string | null, window = 6): TrendPoint[] {
  const months = fullMonths(cashflow, inProgress);
  return months.map((m, i) => {
    const win = trailing(months, i, window);
    return { month: m.month,
             value: +(win.reduce((s, x) => s + x.spend, 0) / win.length).toFixed(0) };
  });
}

/** The liquid assets and their share of the snapshot's assets, per snapshot (liquidity trend; `liquidity`, the one split
 *  Balances and Home read). A snapshot with no asset above zero has no share: null, never 0. */
export function liquidityTrend(snaps: Snapshot[]):
    { date: string; assets: number; liquid: number; liquidPct: number | null }[] {
  return snaps.map((s) => {
    const l = liquidity(s);
    return { date: s.snapshot_date, assets: l.assets, liquid: l.liquid,
             liquidPct: l.liquidShare === null ? null : +l.liquidShare.toFixed(4) };
  });
}

/** Allocation % by class per snapshot: each class's assets (its rows above zero, `assetsBy`, the base Home's donut and
 *  the liquid share read) over the snapshot's assets, restricted to the top classes of the latest snapshot (early
 *  snapshots lack some classes — missing = 0). A row below zero is a debt, in no class's share. */
export function allocationTrend(snaps: Snapshot[], topN = 6):
    { classes: string[]; rows: { date: string; pct: Record<string, number> }[] } {
  if (!snaps.length) return { classes: [], rows: [] };
  const classes = assetsBy(snaps[snaps.length - 1], "asset_class").slice(0, topN).map((l) => l.name);
  const rows = snaps.map((s) => {
    const lines = assetsBy(s, "asset_class");
    const assets = lines.reduce((sum, l) => sum + l.value, 0);
    const byClass = new Map(lines.map((l) => [l.name, l.value]));
    const pct: Record<string, number> = {};
    for (const c of classes) pct[c] = assets > 0 ? +(((byClass.get(c) ?? 0) / assets) * 100).toFixed(1) : 0;
    return { date: s.snapshot_date, pct };
  });
  return { classes, rows };
}

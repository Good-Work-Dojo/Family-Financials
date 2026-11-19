// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Balance-over-time chart options shared by Accounts → Balances / Investments / Liabilities and an account's page.
import type { BalancePoint, BalancesTimeseries } from "../data/types";
import { baseGrid, CHART_SPLIT, compactStep, levelScale, moneyAxis, moneyValue, timeAxis, wrapLegend } from "./echarts";
import { fmtCompact, fmtUSD0 } from "./format";
import { accountColor } from "./tokens";

type Series = BalancesTimeseries["series"];
type Accounts = BalancesTimeseries["accounts"];

/** every account the chart draws has exactly one balance so far: a point each and no line over time yet (the card says
 *  so under the chart, components/NoData.tsx OneBalanceEach) */
export function onePointEach(series: Series, ids: readonly string[]): boolean {
  return ids.length > 0 && ids.every((id) => (series[id]?.length ?? 0) === 1);
}

/** two points this many days apart or more are a monthly history's (a month is 28 to 31 days) */
const MONTHLY_GAP_DAYS = 25;
const daysBetween = (a: string, b: string) => (Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000;

/** Where a balance history turns from one balance a month to frequent ones: the last point of the monthly run it STARTS
 *  with (its first two gaps or more each a month or longer: statement files, the feed's early reports), when it ENDS
 *  frequent (its last gap under a month, once the feed reports every few days). Its line changes character there - a
 *  month's swings (a card's payment dip, a paycheck landing) show only where the points are frequent - and the chart says
 *  so (components/BalanceSpacingNote.tsx); every point is drawn. A date given twice is one point. null for any other
 *  history: monthly throughout, frequent throughout (outages included), or one whose start is not monthly. */
export function monthlyThrough(points: readonly { date: string }[]): string | null {
  const dates = points.map((p) => p.date).filter((d, i, all) => i === 0 || d !== all[i - 1]);
  let k = 0;
  while (k + 1 < dates.length && daysBetween(dates[k], dates[k + 1]) >= MONTHLY_GAP_DAYS) k++;
  if (k < 2 || k >= dates.length - 1) return null;
  return daysBetween(dates[dates.length - 2], dates[dates.length - 1]) < MONTHLY_GAP_DAYS ? dates[k] : null;
}

/** What a chart's line under it says about spacing (monthlyThrough on each of its lines): null when no line turns from
 *  monthly to frequent; `every` when every line turns, on one date; else (dates differ, or some lines never turn) the
 *  latest date any line turns on, of "some accounts". */
export function chartSpacing(series: Series, ids: readonly string[]): { date: string; every: boolean } | null {
  const turns = ids.map((id) => monthlyThrough(series[id] ?? []));
  const dates = turns.filter((d): d is string => d !== null);
  if (dates.length === 0) return null;
  return { date: dates.reduce((a, b) => (b > a ? b : a)), every: turns.every((d) => d === dates[0]) };
}

/** how a balance chart prints a value: in the tooltip, and on its axis (compact, as every money axis), with the finest
 *  step between two ticks the axis labels can tell apart for values up to a size (an axis that leaves zero can span a
 *  narrow range) */
export interface BalanceFormat {
  tooltip: (v: number) => string;
  axis: (v: number) => string;
  step: (max: number) => number;
}
/** a chart of dollars: whole dollars in the tooltip, "$18K" on the axis */
export const MONEY_FORMAT: BalanceFormat = { tooltip: fmtUSD0, axis: fmtCompact, step: compactStep };

/** One time-series line per account, every point its history holds, `value` mapping a point to its plotted y. An
 *  account's colour is its place in the household's own account list (`accountIds`, accounts.json's order: tokens.ts
 *  accountColor), so one account has one colour on every chart and page, whichever is built first. */
export function balanceLines(series: Series, accounts: Accounts, ids: string[], value: (p: BalancePoint) => number,
                             accountIds: readonly string[]) {
  return ids.map((id) => ({
    name: accounts[id]?.name || id, type: "line" as const,
    showSymbol: series[id].length === 1, symbolSize: 6, smooth: false, connectNulls: true,
    color: accountColor(id, accountIds), data: series[id].map((p) => [p.date, value(p)] as [string, number]),
  }));
}

export interface BalanceChartOptions {
  /** how values print (MONEY_FORMAT by default) */
  fmt?: BalanceFormat;
  /** the date axis starts at this day */
  xMin?: string;
  /** the value axis keeps zero whatever the lines hold: an amount owed, a utilization, where zero is the goal */
  keepZero?: boolean;
}

/** The option for lines already built: a caller that also lists them in a "view data" table builds them once and hands
 *  the same array to both, so the table cannot disagree with the chart. A balance is a level: the value axis leaves zero
 *  only by the one rule (lib/echarts.ts levelScale: every value drawn above zero, the smallest at least half the
 *  largest), never for a chart that `keepZero`. */
export function balanceTimeOptionOf(lines: ReturnType<typeof balanceLines>, opts: BalanceChartOptions = {}) {
  const fmt = opts.fmt ?? MONEY_FORMAT;
  const values = lines.flatMap((l) => l.data.filter(([d]) => opts.xMin === undefined || d >= opts.xMin).map(([, v]) => v));
  const largest = Math.max(0, ...values.map((v) => Math.abs(v)));
  return {
    grid: baseGrid,
    legend: wrapLegend(),
    tooltip: { trigger: "axis" as const, valueFormatter: (v: number | null | undefined) => moneyValue(v, fmt.tooltip) },
    xAxis: { ...timeAxis(), ...(opts.xMin === undefined ? {} : { min: opts.xMin }) },
    yAxis: { ...moneyAxis(fmt.axis), ...(opts.keepZero ? {} : levelScale(values, fmt.step(largest))),
      splitLine: { lineStyle: { color: CHART_SPLIT } } },
    series: lines,
  };
}

export function balanceTimeOption(series: Series, accounts: Accounts, ids: string[], value: (p: BalancePoint) => number,
                                  accountIds: readonly string[], opts: BalanceChartOptions = {}) {
  return balanceTimeOptionOf(balanceLines(series, accounts, ids, value, accountIds), opts);
}

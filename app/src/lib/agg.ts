// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Client-side aggregations over (already filtered) transactions.
// Accounting rules (locked, must match the ETL):
//   spending = purchase + fee   |   income = income + dividend + interest
//   transfer / cc-payment excluded everywhere; investment its own series.
import type { CashflowMonth, Manifest, Transaction } from "../data/types";
import { NO_CATEGORY } from "./categories";
import { fmtMonth } from "./format";
import { purchaseKey } from "./splits";

export const SPEND_TYPES = new Set(["purchase", "fee"]);
export const INCOME_TYPES = new Set(["income", "dividend", "interest"]);

/** The cash-flow months in coverage: by each, enough of the household's cash-flow accounts have begun their history
 *  (the export's `in_coverage`, from assumptions.yaml coverage.min_share; a month in coverage is followed only by months
 *  in coverage). Rates, baselines and averages read only these: an earlier month is a coverage artifact, not history. */
export function coverageMonths(cashflow: CashflowMonth[]): CashflowMonth[] {
  return cashflow.filter((m) => m.in_coverage);
}

/** The coverage floor: the first month in coverage (YYYY-MM); null when no month is, so nothing may be compared. */
export function coverageFloor(cashflow: CashflowMonth[]): string | null {
  return cashflow.find((m) => m.in_coverage)?.month ?? null;
}

/** The floor as prose ("Mar 2031") for every sentence that names it; null when there is none, and that sentence or
 *  clause is then left out. */
export function coverageFloorLabel(cashflow: CashflowMonth[]): string | null {
  const floor = coverageFloor(cashflow);
  return floor === null ? null : fmtMonth(floor);
}

export function isSpend(t: Transaction): boolean {
  return SPEND_TYPES.has(t.type);
}
export function isIncome(t: Transaction): boolean {
  return INCOME_TYPES.has(t.type);
}

/** net spend in positive dollars (refunds reduce it) */
export function totalSpend(txns: Transaction[]): number {
  let s = 0;
  for (const t of txns) if (isSpend(t)) s -= t.amount;
  return s;
}

export function prevMonth(ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, "0")}`;
}

export function nextMonth(ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  return m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, "0")}`;
}

/** aggregate savings rate over a set of cashflow months: (income − spend) / income.
 *  null when the window has no income — "no data", not 0% (fmtPct renders "—") */
export function savingsRate(ms: CashflowMonth[]): number | null {
  const inc = ms.reduce((s, m) => s + m.income, 0);
  return inc > 0 ? (inc - ms.reduce((s, m) => s + m.spend, 0)) / inc : null;
}

export function monthsBetween(a: string, b: string): string[] {
  const out: string[] = [];
  let [y, m] = a.slice(0, 7).split("-").map(Number);
  const end = b.slice(0, 7);
  while (true) {
    const ym = `${y.toString().padStart(4, "0")}-${m.toString().padStart(2, "0")}`;
    out.push(ym);
    if (ym >= end) break;
    m++;
    if (m > 12) { m = 1; y++; }
  }
  return out;
}

/** month -> category -> net spend ($ positive). Months are contiguous. */
export function monthlyByCategory(txns: Transaction[]): { months: string[]; categories: string[]; data: Map<string, Map<string, number>> } {
  const data = new Map<string, Map<string, number>>();
  const cats = new Map<string, number>();
  // the first and last month holding a spending row; none while no row is spending
  let span: { min: string; max: string } | null = null;
  for (const t of txns) {
    if (!isSpend(t)) continue;
    const ym = t.date.slice(0, 7);
    if (span === null) span = { min: ym, max: ym };
    else if (ym < span.min) span.min = ym;
    else if (ym > span.max) span.max = ym;
    const cat = t.category ?? NO_CATEGORY;
    let row = data.get(ym);
    if (!row) data.set(ym, (row = new Map()));
    row.set(cat, (row.get(cat) ?? 0) - t.amount);
    cats.set(cat, (cats.get(cat) ?? 0) - t.amount);
  }
  const categories = [...cats.entries()].sort((a, b) => b[1] - a[1]).map(([c]) => c);
  return { months: span === null ? [] : monthsBetween(span.min, span.max), categories, data };
}

export interface VendorStat {
  vendor: string;
  total: number;
  count: number;
  avgTicket: number;
  first: string;
  last: string;
  avgDaysBetween: number | null;
}

/** per vendor over the given (category-view) rows: total = Σ parts (so a category filter keeps a split trip's
 *  matching parts only), count = purchases (a split trip once) */
export function vendorStats(txns: Transaction[]): VendorStat[] {
  const by = new Map<string, Transaction[]>();
  for (const t of txns) {
    if (!isSpend(t) || t.amount >= 0 || !t.vendor) continue;
    let arr = by.get(t.vendor);
    if (!arr) by.set(t.vendor, (arr = []));
    arr.push(t);
  }
  const out: VendorStat[] = [];
  for (const [vendor, rows] of by) {
    rows.sort((a, b) => a.date.localeCompare(b.date));
    const total = rows.reduce((s, t) => s - t.amount, 0);
    const days = (new Date(rows[rows.length - 1].date).getTime()
      - new Date(rows[0].date).getTime()) / 86_400_000;
    const count = new Set(rows.map(purchaseKey)).size;        // purchases, never parts
    out.push({
      vendor, total, count, avgTicket: total / count,
      first: rows[0].date, last: rows[rows.length - 1].date,
      avgDaysBetween: count > 1 ? days / (count - 1) : null,
    });
  }
  return out.sort((a, b) => b.total - a.total);
}

/** the day of the week of a YYYY-MM-DD date (0=Sun..6=Sat), in UTC arithmetic so no zone moves it */
const weekdayOf = (day: string): number => new Date(`${day}T00:00:00Z`).getUTCDay();

/** how many days [from, to] holds, both ends counted (YYYY-MM-DD, in UTC arithmetic); 0 when `from` is after `to` */
export function spanDays(from: string, to: string): number {
  return from > to ? 0 : Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000) + 1;
}

/** the days a window must hold for the weekday averages: four weeks, so every weekday is averaged over four of itself
 *  at least (fewer, and one purchase is a weekday's "average", a weekday with none a $0 bar) */
export const WEEKDAY_MIN_DAYS = 28;

/** Spend by day of week (0=Sun..6=Sat) over the days [from, to] (inclusive): each weekday's outflow (purchases and fees
 *  that took money out, refunds aside) on those days, divided by the number of times THAT weekday falls among them - so
 *  five days of data are averaged over five days, never over a whole month. A weekday that does not fall in the days at
 *  all has no average: null, never 0. The caller passes the days the window and the data share (the later of the
 *  window's start and the first data day, the earlier of its end and the day the data reaches). */
export function weekdaySpend(txns: readonly Transaction[], from: string, to: string): (number | null)[] {
  const totals = new Array<number>(7).fill(0);
  const days = new Array<number>(7).fill(0);
  if (from <= to) {
    const span = spanDays(from, to);
    const first = weekdayOf(from);
    // whole weeks give every weekday once each; the days left over are the first ones from the starting weekday
    for (let w = 0; w < 7; w++) days[(first + w) % 7] = Math.floor(span / 7) + (w < span % 7 ? 1 : 0);
  }
  for (const t of txns) {
    if (!isSpend(t) || t.amount >= 0 || t.date < from || t.date > to) continue;
    totals[weekdayOf(t.date)] -= t.amount;
  }
  return totals.map((v, i) => (days[i] === 0 ? null : v / days[i]));
}

/** category totals for two periods, for MoM / YoY comparison panels */
export function periodCompare(
  txns: Transaction[], curFrom: string, curTo: string, prevFrom: string, prevTo: string,
): { category: string; current: number; previous: number }[] {
  const cur = new Map<string, number>();
  const prev = new Map<string, number>();
  for (const t of txns) {
    if (!isSpend(t)) continue;
    const cat = t.category ?? NO_CATEGORY;
    if (t.date >= curFrom && t.date <= curTo) cur.set(cat, (cur.get(cat) ?? 0) - t.amount);
    else if (t.date >= prevFrom && t.date <= prevTo) prev.set(cat, (prev.get(cat) ?? 0) - t.amount);
  }
  const cats = new Set([...cur.keys(), ...prev.keys()]);
  return [...cats]
    .map((category) => ({
      category,
      current: cur.get(category) ?? 0,
      previous: prev.get(category) ?? 0,
    }))
    .sort((a, b) => Math.abs(b.current - b.previous) - Math.abs(a.current - a.previous));
}

/** the number of days in a YYYY-MM month (UTC arithmetic: no timezone or DST can move it) */
export function daysInMonth(ym: string): number {
  const [y, m] = ym.split("-").map(Number);
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

/** The day the ingest ran (YYYY-MM-DD): the first ten characters of `generatedAt` (manifest.generated_at, the
 *  household's own wall clock at the ingest); null when the stamp does not start with a date. */
export function ingestDay(generatedAt: string): string | null {
  const day = generatedAt.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : null;
}

/** A row dated after the day the ingest ran: the bank dated it ahead (a scheduled payment, a pending charge's expected
 *  settlement). It is a real row - the register lists it, marked - but it is no day of data. False when the ingest
 *  stamp is unreadable. */
export function datedAhead(date: string, generatedAt: string): boolean {
  const day = ingestDay(generatedAt);
  return day !== null && date > day;
}

/** The newest date the data really reaches: the newest transaction date, but never past the day the ingest ran. A
 *  future-dated row (a pending charge's expected settlement) is not data "through tomorrow". `generatedAt` is the
 *  manifest's stamp, whose first ten characters are the ingest's own local date. With no transaction (a null newest
 *  date) the data reaches no day: null. */
export function dataThrough(maxTxnDate: string, generatedAt: string): string;
export function dataThrough(maxTxnDate: string | null, generatedAt: string): string | null;
export function dataThrough(maxTxnDate: string | null, generatedAt: string): string | null {
  if (maxTxnDate === null) return null;
  const day = ingestDay(generatedAt);
  return day !== null && day < maxTxnDate ? day : maxTxnDate;
}

/** the newest day each row list holds (null for none), read once per list: the exports never change in place */
const newestDays = new WeakMap<readonly Pick<Transaction, "date">[], string | null>();

function newestDay(rows: readonly Pick<Transaction, "date">[]): string | null {
  if (!newestDays.has(rows)) {
    let newest: string | null = null;
    for (const t of rows) if (newest === null || t.date > newest) newest = t.date;
    newestDays.set(rows, newest);
  }
  return newestDays.get(rows) ?? null;
}

/** the oldest day each row list holds (null for none), read once per list */
const oldestDays = new WeakMap<readonly Pick<Transaction, "date">[], string | null>();

/** The day the data begins: the oldest transaction's date (YYYY-MM-DD); null with no transaction. The same day as the
 *  data context's `Indexes.dataRange.first`, read straight from a row list so a pure function can take it. */
export function dataFirstDay(rows: readonly Pick<Transaction, "date">[]): string | null {
  if (!oldestDays.has(rows)) {
    let oldest: string | null = null;
    for (const t of rows) if (oldest === null || t.date < oldest) oldest = t.date;
    oldestDays.set(rows, oldest);
  }
  return oldestDays.get(rows) ?? null;
}

/** The month in progress (YYYY-MM): the EARLIER of the newest month that holds a row and the ingest month (the first
 *  seven characters of `manifest.generated_at`). It is a property of the exports, never of the wall clock, so the same
 *  exports show the same months wherever and whenever they are opened.
 *  - A month after the ingest month has not begun: a row the bank dated into it does not make it the month in
 *    progress, and it is never counted, shown or charted (`countedRows`) until its month begins.
 *  - The ingest month is in progress even on its last day (the ingest day itself is still coming in), and so is the
 *    month before it when the newest row is in it: rows of a month keep arriving for days after it ends.
 *  - Data that ends before the ingest month (statement files not yet brought up to date) keeps its own newest month in
 *    progress: nothing says the file covered the whole of it, and no later month is invented.
 *  The newest month that holds a row is the later of the newest transaction's and the newest cash-flow month's (the
 *  ETL writes a cash-flow month only for a month that holds a row). null when no month holds a row. */
export function monthInProgress(data: {
  transactions: readonly Pick<Transaction, "date">[];
  cashflow: readonly Pick<CashflowMonth, "month">[];
  manifest: Pick<Manifest, "generated_at">;
}): string | null {
  let newest = newestDay(data.transactions)?.slice(0, 7) ?? null;
  for (const m of data.cashflow) if (newest === null || m.month > newest) newest = m.month;
  if (newest === null) return null;
  const ingestMonth = ingestDay(data.manifest.generated_at)?.slice(0, 7) ?? null;
  return ingestMonth !== null && ingestMonth < newest ? ingestMonth : newest;
}

/** Whether the data is really behind the ingest: at least one WHOLE month lies between the month in progress
 *  (`monthInProgress`) and the ingest month (data that ends in August, an ingest in October - statement files not yet
 *  brought up to date). That month is then where the data ends: no bills are "still coming in" for it, and later
 *  transactions have not been read yet. The month just before the ingest month is not behind: an ingest on the 1st,
 *  before any row of the new month has posted, still has last month's rows arriving. False with no month in progress,
 *  and with an ingest stamp that does not start with a date. */
export function dataBehindIngest(inProgress: string | null, generatedAt: string): boolean {
  const ingestMonth = ingestDay(generatedAt)?.slice(0, 7) ?? null;
  return inProgress !== null && ingestMonth !== null && inProgress < prevMonth(ingestMonth);
}

/** The full months: the coverage months (`coverageMonths`) before the month in progress (`monthInProgress`), so neither
 *  that month nor a month after it (one a row was dated ahead into) is ever one. The one full-month cut behind every
 *  trailing rate and average (Home's twelve-month rate, Cash Flow's KPIs and their "through <month>" stamps, burn,
 *  income streams, the budget grid, Trends' lines, Planning's pace and forecast, Second house's free cash), so two pages
 *  can never disagree on a window. None while no month is in progress (no month holds a row). */
export function fullMonths(cashflow: CashflowMonth[], inProgress: string | null): CashflowMonth[] {
  return inProgress === null ? [] : coverageMonths(cashflow).filter((m) => m.month < inProgress);
}

/** The fewest full months (`runRateMonths`) a planning run-rate is read from: the FIRE number's core spend, the savings
 *  pace and what is prefilled or annualised from it, the emergency fund's essentials and the runway, the stress test's
 *  years of saving, Second house's free cash flow and the spending-cut card. With fewer the rate is unknown ("—"), never
 *  one taken from a week or a single month. */
export const RUN_RATE_MIN_MONTHS = 3;

/** The full months a planning run-rate may read: `fullMonths`, less the first of them when the data begins after its
 *  first day (`firstDay`: the data's first transaction day; the test `partialMonths` applies to the first month a
 *  chart or a report shows). The ETL puts the month of an account's first row in coverage whatever its day, so a first
 *  pull that reaches back to the 12th makes a month of 20 days look full, and a rate averaged over it reads low. With no
 *  first day (no transaction) nothing is cut. `fullMonths` itself, and every figure that is not a planning run-rate,
 *  keep that month. */
export function runRateMonths(cashflow: CashflowMonth[], inProgress: string | null, firstDay: string | null): CashflowMonth[] {
  const full = fullMonths(cashflow, inProgress);
  if (!full.length || firstDay === null) return full;
  const last = full[full.length - 1].month;
  const partial = partialMonths(full.map((m) => m.month), firstDay, `${last}-${String(daysInMonth(last)).padStart(2, "0")}`);
  return partial.has(full[0].month) ? full.slice(1) : full;
}

/** The day the next full month a planning run-rate can count ends (YYYY-MM-DD), for a line that says when its count
 *  grows: the last day of the month in progress, or of the month after it when the month in progress is the first
 *  month in coverage and the data begins after its COVERED_FROM_DAY-th day (that month will be no full month:
 *  `runRateMonths`). null when the month in progress ended before the day the ingest ran - the data stops short of the
 *  ingest month (statement files not yet brought up to date), so the count grows only once later transactions are
 *  read - or when no month is in progress. Read from the exports, never the clock. */
export function nextFullMonthEnds(data: {
  transactions: readonly Pick<Transaction, "date">[];
  cashflow: CashflowMonth[];
  manifest: Pick<Manifest, "generated_at">;
}): string | null {
  const inProgress = monthInProgress(data);
  if (inProgress === null) return null;
  const lastDay = (m: string) => `${m}-${String(daysInMonth(m)).padStart(2, "0")}`;
  const ingest = ingestDay(data.manifest.generated_at);
  if (ingest !== null && lastDay(inProgress) < ingest) return null;
  const firstDay = dataFirstDay(data.transactions);
  const partialFirst = coverageFloor(data.cashflow) === inProgress && firstDay !== null
    && !coversFromStart(`${inProgress}-01`, firstDay);
  return lastDay(partialFirst ? nextMonth(inProgress) : inProgress);
}

/** the months a planning run-rate reads: [from, to] (YYYY-MM, inclusive) and how many months that spans */
export interface RunRateWindow { from: string; to: string; months: number }

/** The window a planning run-rate reads: the trailing `n` months ending at the last full month (`runRateMonths`), cut at
 *  the first of them, so it never reaches back before the data or into a month it covers in part; `months` is its real
 *  length (`n` once the history is long enough), which a label states. null with fewer than RUN_RATE_MIN_MONTHS such
 *  months: the rate is unknown. */
export function runRateWindow(cashflow: CashflowMonth[], inProgress: string | null, n: number,
                              firstDay: string | null): RunRateWindow | null {
  const full = runRateMonths(cashflow, inProgress, firstDay);
  if (full.length < RUN_RATE_MIN_MONTHS) return null;
  const first = full[0].month, to = full[full.length - 1].month;
  const start = trailingWindow(to, n)[0].slice(0, 7);
  const from = start < first ? first : start;
  return { from, to, months: monthsBetween(from, to).length };
}

/** The rows a monthly sum, a chart or a report counts: every row but those dated in a month after the month in progress
 *  (a month that has not begun; the register and the account pages still list them). All of them while no month is in
 *  progress (no row at all). */
export function countedRows<T extends Pick<Transaction, "date">>(rows: readonly T[], inProgress: string | null): T[] {
  return inProgress === null ? [...rows] : rows.filter((t) => t.date.slice(0, 7) <= inProgress);
}

export interface ComparePeriods {
  /** inclusive [from, to] of the current and the comparison period */
  cur: [string, string];
  prev: [string, string];
  /** their months, YYYY-MM */
  curMonth: string;
  prevMonth: string;
  /** days into the current month when it is still in progress (month-to-date), else null for a whole month */
  mtdDays: number | null;
  /** days the comparison period covers (the same as `mtdDays`, unless the older month is shorter) */
  prevDays: number;
}

/** The two periods Explore's month-over-month / year-over-year table compares, ending at `endDate`. When that is the
 *  last day of its month the periods are whole months - unless that month is `inProgress` (the month in progress,
 *  `monthInProgress`), which is never whole, even on its last day. A month still in progress is month-to-date and
 *  the older period is cut to the SAME number of days - an in-progress month set against a whole one made every
 *  category look like a big decrease on the 4th. Pure month arithmetic on the strings (no `Date` round trip through
 *  UTC, which can land on the wrong month in a timezone east of Greenwich). With no end date (no data) there is nothing
 *  to compare: null. */
export function comparePeriods(endDate: string, mode: "mom" | "yoy", inProgress?: string | null): ComparePeriods;
export function comparePeriods(endDate: string | null, mode: "mom" | "yoy", inProgress?: string | null): ComparePeriods | null;
export function comparePeriods(endDate: string | null, mode: "mom" | "yoy", inProgress: string | null = null): ComparePeriods | null {
  if (endDate === null) return null;
  const curMonth = endDate.slice(0, 7);
  const last = daysInMonth(curMonth);
  const day = Math.min(Number(endDate.slice(8, 10)), last);
  const partial = day < last || curMonth === inProgress;
  const prevM = mode === "mom" ? prevMonth(curMonth)
    : `${String(Number(curMonth.slice(0, 4)) - 1).padStart(4, "0")}${curMonth.slice(4)}`;
  const prevDays = partial ? Math.min(day, daysInMonth(prevM)) : daysInMonth(prevM);
  const pad = (n: number) => String(n).padStart(2, "0");
  return {
    cur: [`${curMonth}-01`, `${curMonth}-${pad(day)}`],
    prev: [`${prevM}-01`, `${prevM}-${pad(prevDays)}`],
    curMonth, prevMonth: prevM,
    mtdDays: partial ? day : null, prevDays,
  };
}

/** The last day of a month's start on which the data may begin and still cover the month from its start: a statement
 *  file whose first row falls on the 2nd (nothing happened on the 1st) covers its month. */
export const COVERED_FROM_DAY = 3;

/** how a window's first period is judged (`coversFromStart`): `interval`, the period's length (default a month), and
 *  `picked`, whether the window begins on a From date a person chose rather than on the data's own first day */
export interface CoverJudging {
  interval?: "week" | "month" | "quarter" | "year";
  picked?: boolean;
}

/** Does a window beginning on `windowStart` (YYYY-MM-DD) cover the period that starts on `periodStart` from its start?
 *  It does when it begins on or before that day. One tolerance: a window that begins on the data's own first day
 *  covers a month, a quarter or a year it begins in by the period's COVERED_FROM_DAY-th day, since no row on the 1st
 *  proves nothing about where the data begins. A From date a person picked (`picked`) gets none - a From after the 1st
 *  leaves the days before it out on purpose - and neither does a week (`interval: "week"`), whatever day it starts on.
 *  The one rule behind every "first period covered in part" mark: Explore's and Reports' stars (`partialMonths`,
 *  `windowPartialMonths`, lib/reportBuilder.ts) and the planning run-rates' first month (`runRateMonths`,
 *  `nextFullMonthEnds`), which read the data's first day. */
export function coversFromStart(periodStart: string, windowStart: string,
                                { interval = "month", picked = false }: CoverJudging = {}): boolean {
  const tolerant = !picked && interval !== "week" && periodStart.endsWith("-01");
  const lastDay = tolerant ? `${periodStart.slice(0, 8)}${String(COVERED_FROM_DAY).padStart(2, "0")}` : periodStart;
  return windowStart <= lastDay;
}

/** The months of an ascending month list that the window [windowStart, windowEnd] covers only in part - only the
 *  first and last can be - and `inProgress` (the month in progress, `monthInProgress`), which no window covers whole,
 *  even one that ends on its last day. The same test the Reports tab stars ("Apr 26*"), so Explore's monthly chart marks
 *  an in-progress last month (or a first month the From date or the start of the data cuts) the way Reports does: a
 *  first month is covered in part when the window begins after its 1st, or after its COVERED_FROM_DAY-th day when it
 *  begins on the data's first day rather than on a From date a person `picked` (`coversFromStart`). A window with no
 *  data at an end (null) stars nothing: there is no data to cover a month in part. */
export function partialMonths(months: readonly string[], windowStart: string | null, windowEnd: string | null,
                              inProgress: string | null = null, { picked = false }: { picked?: boolean } = {}): Set<string> {
  const out = new Set<string>();
  if (!months.length || windowStart === null || windowEnd === null) return out;
  const first = months[0], last = months[months.length - 1];
  if (!coversFromStart(`${first}-01`, windowStart, { picked })) out.add(first);
  if (`${last}-${String(daysInMonth(last)).padStart(2, "0")}` > windowEnd || last === inProgress) out.add(last);
  return out;
}

/** The months of a monthly chart over filtered rows (`months`, ascending) that its window covers only in part
 *  (`partialMonths`): the window is the From and To dates a person picked (`from`, `to`), cut to the data (`dataStart`,
 *  its first day; `dataEnd`, the day it runs through). A From after the data's first day is a pick, so its month is
 *  partial unless the From is the 1st; a window that begins where the data does gets the data's first-days tolerance.
 *  With no data (a null end) nothing is starred. */
export function windowPartialMonths(months: readonly string[],
  { dataStart, dataEnd, from, to }: { dataStart: string | null; dataEnd: string | null; from?: string; to?: string },
  inProgress: string | null): Set<string> {
  const picked = dataStart !== null && !!from && from > dataStart;
  const start = picked ? from! : dataStart;
  const end = dataEnd !== null && !!to && to < dataEnd ? to : dataEnd;
  return partialMonths(months, start, end, inProgress, { picked });
}

/** trailing N-month window ending at `end` (YYYY-MM), as [fromDate, toDate] */
export function trailingWindow(end: string, months: number): [string, string] {
  let [y, m] = end.split("-").map(Number);
  // real last day of the month — this date can reach the page
  const lastDay = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const to = `${end}-${String(lastDay).padStart(2, "0")}`;
  m -= months - 1;
  while (m <= 0) { m += 12; y--; }
  return [`${y.toString().padStart(4, "0")}-${m.toString().padStart(2, "0")}-01`, to];
}

/** Calendar year of the data: the snapshot date, else the ingest time. */
export function currentYear(manifest: Pick<Manifest, "current_snapshot_date" | "generated_at">): number {
  return Number((manifest.current_snapshot_date ?? manifest.generated_at).slice(0, 4));
}

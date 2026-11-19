// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Home's "This month so far": spending in the data's current calendar month, through its last day of data, set
// against the TYPICAL spend by the same day over the months before it. Read-only - no budget to set, no envelope,
// no manual step. Spend is the app's one definition (agg.ts isSpend: purchases and fees, refunds netted), read on
// the category view (split parts, never a parent), summed in integer cents.
import type { Transaction } from "../data/types";
import { coversFromStart, dataThrough, daysInMonth, isSpend, prevMonth, RUN_RATE_MIN_MONTHS } from "./agg";
import { NO_CATEGORY } from "./categories";
import { countOf } from "./format";

/** how many complete months before this one the typical pace is drawn from */
export const MTD_PRIOR_MONTHS = 6;
/** within this many days of the month's start the comparison rests on a few days of spending: the card says so */
export const MTD_EARLY_DAYS = 6;
/** a pace within this share of typical reads "about typical" */
export const MTD_ABOUT_BAND = 0.1;
/** categories listed */
export const MTD_TOP = 3;

export type Pace = "about" | "above" | "below";

export interface PriorMonth {
  month: string;              // YYYY-MM
  /** the days counted: the same day of the month as `through`, or the whole month when it is shorter */
  days: number;
  /** net spend from the 1st through `days`, dollars */
  spend: number;
}

export interface MonthToDate {
  month: string;              // YYYY-MM of `through`
  through: string;            // ISO: the last day of data
  day: number;                // day of the month of `through`
  /** net spend from the 1st through `through`, dollars (refunds reduce it) */
  spend: number;
  /** the prior months compared, newest first: up to MTD_PRIOR_MONTHS, each inside the coverage floor and the data */
  priors: PriorMonth[];
  /** median of the priors' spend; null with fewer than RUN_RATE_MIN_MONTHS prior months to compare */
  typical: number | null;
  /** spend / typical - 1; null when there is no typical figure or it is not above 0 */
  ratio: number | null;
  pace: Pace | null;
  /** the month is at most MTD_EARLY_DAYS days old */
  early: boolean;
  /** the largest categories by net spend so far (only those above 0), largest first */
  top: { category: string; spend: number }[];
}

const cents = (dollars: number) => Math.round(dollars * 100);

/** The last COMPLETE day of data: `dataThrough` (the newest transaction date, never past the ingest day), or the day
 *  before it when that is the ingest day itself - that day's spending is still coming in, so a figure cut at it, and
 *  every prior month cut at the same day, would set a partial day against whole ones. On the 1st that is the last day
 *  of the month before: the caller says the month has no complete day yet. With no transaction there is none: null. */
export function lastCompleteDay(maxTxnDate: string, generatedAt: string): string;
export function lastCompleteDay(maxTxnDate: string | null, generatedAt: string): string | null;
export function lastCompleteDay(maxTxnDate: string | null, generatedAt: string): string | null {
  const end = dataThrough(maxTxnDate, generatedAt);
  if (end === null || end !== generatedAt.slice(0, 10)) return end;
  const d = new Date(`${end}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}

/** the middle value (the mean of the two middle ones for an even count); null for none */
export function median(xs: readonly number[]): number | null {
  if (xs.length === 0) return null;
  const s = [...xs].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/** "about" within MTD_ABOUT_BAND of typical, else "above" / "below"; null without a ratio */
export function paceOf(ratio: number | null): Pace | null {
  if (ratio === null) return null;
  if (Math.abs(ratio) < MTD_ABOUT_BAND) return "about";
  return ratio > 0 ? "above" : "below";
}

/** `through` is the data's last complete day (`lastCompleteDay`); rows dated after it are not counted. A prior month is
 *  compared whenever it is a full month - on or after `floor` (the coverage floor, agg.ts coverageFloor: earlier months
 *  are coverage artifacts; null - no month in coverage - compares none, an empty baseline rather than a guessed one) and
 *  covered from its start by the app's one rule (agg.ts `coversFromStart`: the data begins by its COVERED_FROM_DAY-th
 *  day, the tolerance the planning run-rates' first month reads, so a statement whose first row is on the 2nd counts its
 *  month) - and the data holds at least one transaction in it (a month with none is a gap in the feeds, unknown - not a
 *  month of zero spending). A month with transactions but no spend through the day is a real 0. */
export function monthToDate(txns: readonly Transaction[], through: string, floor: string | null): MonthToDate {
  const month = through.slice(0, 7);
  const day = Number(through.slice(8, 10));
  // the first transaction's date; null with none, when no month is covered
  let dataStart: string | null = null;
  const alive = new Set<string>();
  for (const t of txns) {
    if (dataStart === null || t.date < dataStart) dataStart = t.date;
    alive.add(t.date.slice(0, 7));
  }
  // the prior months to compare, newest first, and the day each is cut at
  const priors: { month: string; days: number; last: string }[] = [];
  let m = month;
  for (let i = 0; i < MTD_PRIOR_MONTHS; i++) {
    m = prevMonth(m);
    if (floor === null || m < floor || dataStart === null || !coversFromStart(`${m}-01`, dataStart)
        || !alive.has(m)) continue;
    const days = Math.min(day, daysInMonth(m));
    priors.push({ month: m, days, last: `${m}-${String(days).padStart(2, "0")}` });
  }
  const priorCents = new Map<string, number>(priors.map((p) => [p.month, 0]));
  const byCategory = new Map<string, number>();
  let spendCents = 0;
  for (const t of txns) {
    // a split parent is display-only: its parts carry the amounts (the category view holds none; a raw list might)
    if (t.is_parent || !isSpend(t)) continue;
    const c = -cents(t.amount);
    const ym = t.date.slice(0, 7);
    if (ym === month) {
      if (t.date > through) continue;
      spendCents += c;
      const cat = t.category ?? NO_CATEGORY;
      byCategory.set(cat, (byCategory.get(cat) ?? 0) + c);
      continue;
    }
    const p = priors.find((x) => x.month === ym);
    if (p && t.date <= p.last) priorCents.set(ym, priorCents.get(ym)! + c);
  }
  const priorRows: PriorMonth[] = priors.map((p) => ({ month: p.month, days: p.days, spend: priorCents.get(p.month)! / 100 }));
  // a typical pace is the median of at least RUN_RATE_MIN_MONTHS earlier months, as every planning run-rate is: the
  // "median" of one or two months is that month, not what is typical
  const typical = priorRows.length >= RUN_RATE_MIN_MONTHS ? median(priorRows.map((p) => p.spend)) : null;
  const spend = spendCents / 100;
  const ratio = typical !== null && typical > 0 ? spend / typical - 1 : null;
  const top = [...byCategory.entries()]
    .filter(([, c]) => c > 0)
    .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
    .slice(0, MTD_TOP)
    .map(([category, c]) => ({ category, spend: c / 100 }));
  return { month, through, day, spend, priors: priorRows, typical, ratio, pace: paceOf(ratio), early: day <= MTD_EARLY_DAYS, top };
}

/** the pace in words (a percentage is a ratio, so privacy mode leaves it); with fewer than RUN_RATE_MIN_MONTHS earlier
 *  months there is no typical pace, and the line says how many there are */
export function paceSentence(r: Pick<MonthToDate, "pace" | "ratio" | "priors">): string {
  if (r.priors.length === 0) return "No earlier months to compare with yet.";
  if (r.priors.length < RUN_RATE_MIN_MONTHS) {
    return `Only ${countOf(r.priors.length, "earlier month")} to compare with yet: a typical pace needs ${RUN_RATE_MIN_MONTHS}.`;
  }
  if (r.pace === null || r.ratio === null) return "No spending by this day in the months before, so there is no pace to compare.";
  if (r.pace === "about") return "About typical for this point in the month.";
  return `${Math.round(Math.abs(r.ratio) * 100)}% ${r.pace} typical so far.`;
}

/** a day of the month in words: "1st", "2nd", "3rd", "4th", "11th", "21st", "31st" */
export function ordinal(n: number): string {
  const tens = n % 100;
  const suffix = tens >= 11 && tens <= 13 ? "th" : n % 10 === 1 ? "st" : n % 10 === 2 ? "nd" : n % 10 === 3 ? "rd" : "th";
  return `${n}${suffix}`;
}

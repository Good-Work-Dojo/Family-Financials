// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Forward obligations calendar: roll each active recurring charge forward
// from its next_expected date (the ETL's: a monthly bill on its own day of the
// month). The lumpy ones (annual/quarterly renewals, big bills) are the ones
// worth seeing coming.
import dayjs from "dayjs";
import type { RecurringItem } from "../data/types";
import { nextMonth } from "./agg";

export interface Obligation {
  vendor: string;
  category: string | null;
  date: string;
  amount: number;
  cadence: RecurringItem["cadence"];
  lumpy: boolean;             // an annual/semiannual/quarterly renewal, or a big charge on an irregular cadence (isLumpy)
}

/** the amount from which a charge on a cadence that is neither weekly, biweekly nor monthly counts as big */
export const LUMPY_MIN = 500;
const RENEWAL_CADENCES = new Set<string>(["annual", "semiannual", "quarterly"]);
const FREQUENT_CADENCES = new Set<string>(["weekly", "biweekly", "monthly"]);

/** A "lumpy" bill is one worth seeing coming: a renewal (annual / semiannual / quarterly), or a big charge that does
 *  not recur every week or month. A large charge that DOES recur that often (the biweekly mortgage) is routine, however
 *  big: it used to count, so the lumpy chart, the 90-day list and Planning's KPI were mostly the mortgage. (Today's
 *  cadences are weekly / biweekly / monthly / quarterly / annual, so the amount clause only matters for a cadence the
 *  ETL adds later.) One predicate for Spending -> Recurring's chart and lists and Planning's "Lumpy bills" KPI. */
export function isLumpy(cadence: string, amount: number): boolean {
  return RENEWAL_CADENCES.has(cadence) || (amount >= LUMPY_MIN && !FREQUENT_CADENCES.has(cadence));
}

/** The date the "next 90 days" renewals start from: the newest asset snapshot's date, else the day the data reaches
 *  (`dataThrough`: the newest transaction's day, never past the ingest day, so a row dated ahead does not start it
 *  late); null with neither, when there is nothing to roll forward from. Also the day Recurring judges its age and
 *  renewal flags at. */
export function obligationsFrom(currentSnapshotDate: string | null, dataThrough: string | null): string | null {
  return currentSnapshotDate ?? dataThrough;
}

/** The months a "next 12 months" view covers: the `n` months after the month in progress (agg.ts `monthInProgress`),
 *  so none of them is already under way; none while no month is in progress. One window for Spending → Recurring's
 *  obligations chart, its list of renewals and Planning's lumpy-bills KPI, so their totals agree. */
export function obligationMonths(inProgress: string | null, n = 12): string[] {
  if (inProgress === null) return [];
  const out: string[] = [];
  for (let m = nextMonth(inProgress); out.length < n; m = nextMonth(m)) out.push(m);
  return out;
}

/** the calendar months a step spans, for the cadences paid on a day of the month */
const MONTH_STEP: Partial<Record<RecurringItem["cadence"], number>> = { monthly: 1, quarterly: 3, annual: 12 };
/** the days a weekly or biweekly step spans when the export states no median gap */
const DAY_STEP: Partial<Record<RecurringItem["cadence"], number>> = { weekly: 7, biweekly: 14 };
/** the most steps an item is rolled through: a weekly charge over twenty years */
const MAX_STEPS = 1100;
const ISO = "YYYY-MM-DD";

/** An item's expected charge dates through `until` (an ISO day; the list may run one date past it), the one projection
 *  Spending → Recurring's bills and Planning's cash-flow forecast both read. First the dates the export gives
 *  (`expected_dates`: the ETL's, so a monthly bill on its own day of the month, a business-day end included; a monthly
 *  item carries thirteen, which reach the twelfth month after the month in progress, so a next-12-months view reads them
 *  as they are; `next_expected` alone from an export that has none). Only past them, the fallback for a date the export
 *  does not reach (a bill already late, a longer horizon), later ones by its cadence: a monthly, quarterly or annual
 *  item by calendar months, each counted
 *  from the exported date with the latest day of the month (so a clamped month end never becomes the day: 28 Feb is
 *  followed by 31 Mar) and kept from the month after the last exported date on, so a monthly item falls once in every
 *  month after its exported ones; a weekly or biweekly one by its median gap in days (7 or 14 without one), as the ETL
 *  steps it. Dates before a caller's window are the caller's to drop: a late or lapsed charge projects from its first
 *  date inside it. */
export function expectedCharges(r: RecurringItem, until: string): string[] {
  const out = r.expected_dates.length ? [...r.expected_dates] : [r.next_expected];
  const last = out[out.length - 1];
  const months = MONTH_STEP[r.cadence];
  if (months !== undefined) {
    const anchor = out.reduce((a, d) => (d.slice(8) >= a.slice(8) ? d : a));
    for (let k = 1; k < MAX_STEPS && out[out.length - 1] <= until; k++) {
      const d = dayjs(anchor).add(k * months, "month").format(ISO);
      if (d.slice(0, 7) > last.slice(0, 7)) out.push(d);
    }
    return out;
  }
  const gap = r.median_gap_days >= 1 ? r.median_gap_days : (DAY_STEP[r.cadence] ?? 7);
  for (let k = 1; k < MAX_STEPS && out[out.length - 1] <= until; k++) out.push(dayjs(last).add(k * gap, "day").format(ISO));
  return out;
}

/** an item's expected charges dated from `from` through `to` (ISO days, both included) */
function chargesBetween(r: RecurringItem, from: string, to: string): string[] {
  return expectedCharges(r, to).filter((d) => d >= from && d <= to);
}

/** every active item's expected charges from `from` through `to` (ISO days, both included), by date */
function obligationsBetween(items: readonly RecurringItem[], from: string, to: string): Obligation[] {
  const out: Obligation[] = [];
  for (const r of items) {
    if (!r.active) continue;
    for (const date of chargesBetween(r, from, to)) {
      out.push({ vendor: r.vendor, category: r.category, date, amount: r.typical_amount, cadence: r.cadence,
        lumpy: isLumpy(r.cadence, r.typical_amount) });
    }
  }
  return out.sort((a, b) => a.date.localeCompare(b.date));
}

/** the expected charges from `fromDate` through the same day `horizonMonths` later: the "next 90 days" renewals */
export function upcomingObligations(
  items: readonly RecurringItem[], fromDate: string, horizonMonths = 12,
): Obligation[] {
  return obligationsBetween(items, fromDate, dayjs(fromDate).add(horizonMonths, "month").format(ISO));
}

/** the expected charges dated in `months` (consecutive YYYY-MM, `obligationMonths`): from the first one's first day
 *  through the last one's last day; none for no month */
export function obligationsInMonths(items: readonly RecurringItem[], months: readonly string[]): Obligation[] {
  if (months.length === 0) return [];
  const last = months[months.length - 1];
  return obligationsBetween(items, `${months[0]}-01`, dayjs(`${last}-01`).endOf("month").format(ISO));
}

export interface ObligationMonth { month: string; total: number; lumpyTotal: number; lumpy: Obligation[] }

/** one row per month of `months`, in their order, a month no charge falls in at zero (a chart's twelve bars); a charge
 *  dated in no month of them is left out */
export function obligationsByMonth(obs: readonly Obligation[], months: readonly string[]): ObligationMonth[] {
  const by = new Map<string, ObligationMonth>(months.map((month) => [month, { month, total: 0, lumpyTotal: 0, lumpy: [] }]));
  for (const o of obs) {
    const row = by.get(o.date.slice(0, 7));
    if (!row) continue;
    row.total += o.amount;
    if (o.lumpy) {
      row.lumpyTotal += o.amount;
      row.lumpy.push(o);
    }
  }
  return [...by.values()];
}

export interface ObligationGroup {
  vendor: string;
  cadence: RecurringItem["cadence"];
  amount: number;
  /** the first date in the list, and how many times the same charge falls in it ("× N") */
  date: string;
  count: number;
}

/** collapse a date-ordered obligation list to one entry per vendor and amount ("Insurance × 2"): a quarterly bill can
 *  fall twice in 90 days, and the same line twice reads as a bug. Different amounts from one vendor stay separate. */
export function groupObligations(obs: readonly Obligation[]): ObligationGroup[] {
  const by = new Map<string, ObligationGroup>();
  for (const o of obs) {
    const key = `${o.vendor}\u0000${o.amount}`;
    const g = by.get(key);
    if (g) g.count++;
    else by.set(key, { vendor: o.vendor, cadence: o.cadence, amount: o.amount, date: o.date, count: 1 });
  }
  return [...by.values()];
}

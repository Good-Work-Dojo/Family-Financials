// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Explore's month-over-month / year-over-year compare: which days of which two months it sets side by side, and the
// words its title says that in. Pure: the page passes the data's last day, the ingest stamp, the filter's To date and
// the month in progress.
import { type ComparePeriods, comparePeriods } from "./agg";
import { fmtMonth } from "./format";
import { lastCompleteDay } from "./monthToDate";

export interface CompareWindow {
  periods: ComparePeriods;
  /** what the title says after "Month-over-month" / "Year-over-year": the two months, and - when the current one is cut
   *  short - how many days of each are compared, in plain words */
  words: string;
}

/** "the first day" / "the first 30 days" */
const firstDays = (n: number) => (n === 1 ? "the first day" : `the first ${n} days`);

/** The compare ends at the data's last COMPLETE day (`lastCompleteDay`, the day "This month so far" on Home stops at:
 *  the ingest day itself is still coming in) or at the filter's To date when that is earlier. A month the end cuts short
 *  - or the month in progress (`inProgress`), which is never whole - is set against the SAME number of days of the
 *  older month (all of it when it is shorter), and the words say so: "Oct 2026 vs Sep 2026, the first 30 days of each",
 *  "Mar 2026 vs Feb 2026, the first 30 days of Mar 2026 against all 28 of Feb 2026"; two whole months are just "Sep
 *  2026 vs Aug 2026". `dataThrough` is the day the data reaches (never past the ingest day); null with no transaction,
 *  when there is nothing to compare: null. */
export function compareWindow(p: {
  dataThrough: string | null; generatedAt: string; to?: string; mode: "mom" | "yoy"; inProgress: string | null;
}): CompareWindow | null {
  const complete = lastCompleteDay(p.dataThrough, p.generatedAt);
  if (complete === null) return null;
  const periods = comparePeriods(p.to && p.to < complete ? p.to : complete, p.mode, p.inProgress);
  const cur = fmtMonth(periods.curMonth), prev = fmtMonth(periods.prevMonth);
  const days = periods.mtdDays;
  const words = days === null ? `${cur} vs ${prev}`
    : periods.prevDays === days ? `${cur} vs ${prev}, ${firstDays(days)} of each`
    : `${cur} vs ${prev}, ${firstDays(days)} of ${cur} against all ${periods.prevDays} of ${prev}`;
  return { periods, words };
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The months Explore's daily spend calendar draws.

/** a YYYY-MM month `n` months before `ym` */
function monthsBefore(ym: string, n: number): string {
  const [y, m] = ym.split("-").map(Number);
  const i = y * 12 + (m - 1) - n;
  return `${Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}`;
}

/** the last day of a YYYY-MM month, ISO */
function monthEnd(ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  return `${ym}-${String(new Date(y, m, 0).getDate()).padStart(2, "0")}`;
}

export interface CalendarSpan {
  /** the first and the last month drawn, YYYY-MM */
  from: string;
  to: string;
  /** the calendar's own range: the first month's first day to the last month's end, or to `end` in the window's own
   *  last month */
  range: [string, string];
  /** the window holds spending in months before `from` that the calendar leaves out (it draws at most `maxMonths`) */
  capped: boolean;
}

/** The rows of weeks a calendar of `range` draws, its weeks starting on Sunday (ECharts' calendar default): the days it
 *  spans and the days before its first in that first week, in sevens, rounded up. */
export function weeksSpanned(range: readonly [string, string]): number {
  const day = (iso: string) => Date.UTC(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)) - 1, Number(iso.slice(8, 10)));
  const days = Math.round((day(range[1]) - day(range[0])) / 86_400_000) + 1;
  return Math.ceil((new Date(day(range[0])).getUTCDay() + days) / 7);
}

/** The months the daily spend calendar draws. They end with the month of the newest day with spending (`days`, ISO) on or
 *  before the window's end (`end`: its To date, else the data's last day) and run back at most `maxMonths` from it, from
 *  the first month among them that holds spending: so a short history is not drawn among months of empty days, and
 *  spending that stopped long before the window's end (a category, an account no longer used) is still drawn, never a
 *  calendar that says there is none. null only when no day with spending falls on or before `end`. */
export function calendarSpan(days: Iterable<string>, end: string, maxMonths: number): CalendarSpan | null {
  const reach = [...days].filter((d) => d <= end);
  if (reach.length === 0) return null;
  const last = reach.reduce((a, b) => (b > a ? b : a)).slice(0, 7);
  const floor = monthsBefore(last, maxMonths - 1);
  let first = last, capped = false;
  for (const d of reach) {
    const ym = d.slice(0, 7);
    if (ym < floor) capped = true;
    else if (ym < first) first = ym;
  }
  return { from: first, to: last, range: [`${first}-01`, last === end.slice(0, 7) ? end : monthEnd(last)], capped };
}

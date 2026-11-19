// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Page-scoped date ranges share Spending's URL vocabulary (?from=&to=, ISO,
// inclusive) so deep links compose across pages with zero translation.
// Pages whose default isn't "all" also get ?win=<preset> so an explicit
// "All" click is distinguishable from a bare URL.
import dayjs from "dayjs";
import { localISO } from "../lib/format";
import { dateParam, ignoreParam } from "./urlValues";

export interface DateRange {
  from?: string;                 // ISO date, inclusive
  to?: string;                   // ISO date, inclusive
}

export type RangePreset = "3m" | "6m" | "12m" | "24m" | "ytd" | "all";

export const PRESET_LABELS: Record<RangePreset, string> = {
  "3m": "3M", "6m": "6M", "12m": "12M", "24m": "24M", ytd: "YTD", all: "All",
};

/** the presets the Spending FilterBar offers (and DateRangeBar's default row): the same five everywhere a window is chosen */
export const SPENDING_PRESETS: RangePreset[] = ["3m", "6m", "12m", "ytd", "all"];

const WIN_KEY = "win";

/** `?from=&to=` as a range. A value that is not a real `YYYY-MM-DD` day is dropped with one console.warn (like parseFilters), so
 *  `from=garbage` and an empty `from=` read as no bound - the page falls through to `?win=` or its own default instead of showing
 *  an empty or explicitly empty range. */
export function parseDateRange(sp: URLSearchParams): DateRange {
  return { from: dateParam(sp, "from"), to: dateParam(sp, "to") };
}

/** Patch only the keys we own into a copy of `sp` — never rebuild the param
 *  set from scratch (that would clobber a page's other params). */
export function writeDateRange(sp: URLSearchParams, r: DateRange): URLSearchParams {
  const next = new URLSearchParams(sp);
  if (r.from) next.set("from", r.from); else next.delete("from");
  if (r.to) next.set("to", r.to); else next.delete("to");
  next.delete(WIN_KEY);
  return next;
}

/** `?win=<preset>`; an unknown value is ignored with one console.warn. */
export function parseWin(sp: URLSearchParams): RangePreset | null {
  const w = sp.get(WIN_KEY);
  if (!w) return null;
  if (Object.prototype.hasOwnProperty.call(PRESET_LABELS, w)) return w as RangePreset;
  ignoreParam(WIN_KEY, w, "a window preset (3m, 6m, 12m, 24m, ytd, all)");
  return null;
}

export function writeWin(sp: URLSearchParams, p: RangePreset): URLSearchParams {
  const next = new URLSearchParams(sp);
  next.set(WIN_KEY, p);
  next.delete("from");
  next.delete("to");
  return next;
}

/** Range for a preset anchored at `maxDate` (the newest data date).
 *  Non-"all" presets leave `to` open so freshly ingested data stays visible.
 *  Month granularity counts month BUCKETS including the current one — "6M" on
 *  a monthly chart means six columns, not "six calendar months back from the
 *  4th", which would drag in a seventh partial bucket. All is the empty window
 *  and needs no anchor; with no data date (no transaction yet) every other
 *  preset is null and the control that offers them disables them (DateRangeBar
 *  `unavailable`). */
export function presetRange(preset: RangePreset, maxDate: string, granularity?: "day" | "month"): DateRange;
export function presetRange(preset: RangePreset, maxDate: string | null, granularity?: "day" | "month"): DateRange | null;
export function presetRange(
  preset: RangePreset, maxDate: string | null, granularity: "day" | "month" = "day",
): DateRange | null {
  if (preset === "all") return {};
  if (maxDate === null) return null;
  if (preset === "ytd") return { from: `${maxDate.slice(0, 4)}-01-01` };
  const months = Number(preset.slice(0, -1));
  const d = new Date(maxDate + "T00:00:00");
  if (granularity === "month") {
    d.setDate(1);                          // normalize before month math
    d.setMonth(d.getMonth() - (months - 1));
    return { from: localISO(d) };
  }
  d.setMonth(d.getMonth() - months);
  return { from: localISO(d) };
}

/** Which preset (if any) reproduces the given range exactly; without a data date only All can (the empty window). */
export function matchPreset(
  r: DateRange, maxDate: string | null, presets: RangePreset[],
  granularity: "day" | "month" = "day",
): RangePreset | null {
  for (const p of presets) {
    const pr = presetRange(p, maxDate, granularity);
    if (pr !== null && pr.from === r.from && pr.to === r.to) return p;
  }
  return null;
}

/** why a control offering the presets disables those that cannot be resolved (presetRange's null: every one but All) */
export const NO_PRESET_REASON = "No transactions yet";

/** [first day, last day] of a YYYY-MM month — valid in URLs and date inputs
 *  (unlike agg.ts's lexicographic "-31" upper bound). */
export function monthBounds(ym: string): [string, string] {
  const start = `${ym}-01`;
  return [start, dayjs(start).endOf("month").format("YYYY-MM-DD")];
}

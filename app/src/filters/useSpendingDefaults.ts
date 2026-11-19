// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useEffect, useRef } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { useData } from "../data/DataContext";
import { arrivedWithCarriedSearch, CARRIED_SEARCH } from "../lib/hubNav";
import { parseWin, presetRange, type RangePreset, SPENDING_PRESETS } from "./dateRange";
import { FILTER_PARAM_KEYS, type Filters, serializeFilters } from "./filters";

/** the window a fresh Spending visit opens on: the last six months, the preset the 6M chip lights */
const AUTO_WINDOW: RangePreset = "6m";

/** Is `from`/`to` exactly the window the page wrote by itself (a `from` of AUTO_WINDOW before the newest data, no `to`)?
 *  With no data date (`maxDate` null) the page writes none, so no window is it. */
export function isAutoWindow(from: string | undefined, to: string | undefined, maxDate: string | null): boolean {
  return !to && from !== undefined && from === presetRange(AUTO_WINDOW, maxDate)?.from;
}

/** The filters with the automatic window taken out: what is left is what the USER chose. The Save view button is offered only
 *  while something of that is on (a bare fresh visit has nothing worth naming). */
export function withoutAutoWindow(f: Filters, maxDate: string | null): Filters {
  return isAutoWindow(f.from, f.to, maxDate) ? { ...f, from: undefined } : f;
}

/** The preset a window IS, if it is one: a `from` that is exactly the start of 3M / 6M / 12M / YTD for the data's end, with no
 *  `to`. (The All preset has no `from` and needs no name: a saved item without dates already means all time.) With no data
 *  date no preset resolves, so none is. */
export function rollingPreset(from: string | undefined, to: string | undefined, maxDate: string | null): RangePreset | null {
  if (to || from === undefined) return null;
  return SPENDING_PRESETS.find((p) => p !== "all" && presetRange(p, maxDate)?.from === from) ?? null;
}

/** The query a SAVED view stores. A window that is a preset - the automatic six months included - is saved as `win=<preset>`,
 *  not as the dates it happened to be on the day of the save: a view of "Groceries, last 6 months" has to still be that in
 *  a year, not an ever-growing window from a frozen start (it is resolved against the data's end when it is opened, see
 *  useSpendingDefaults). Dates the user picked - another start, any end - are saved as the exact days, and so is every
 *  window while there is no data date (`maxDate` null). */
export function savedFilterQuery(f: Filters, maxDate: string | null): string {
  const p = rollingPreset(f.from, f.to, maxDate);
  if (!p) return serializeFilters(f).toString();
  const sp = serializeFilters({ ...f, from: undefined });
  sp.set("win", p);
  return sp.toString();
}
/** the same for a serialized filter query (a saved report's `spec.filters`); with no data date the query as it is */
export function savedQuery(query: string, maxDate: string | null): string {
  const sp = new URLSearchParams(query);
  const p = rollingPreset(sp.get("from") ?? undefined, sp.get("to") ?? undefined, maxDate);
  if (!p) return query;
  sp.delete("from");
  sp.set("win", p);
  return sp.toString();
}

/** Fresh visits (no FILTER keys in the URL) default to the last 6 months, where account coverage is
 *  strongest; deep links keep their own ranges. Shared by the Explore and Transactions tabs so a
 *  bare URL on either lands on the same window. Only filter keys count: a URL carrying just a
 *  page-scoped param (`?sort=`, `?groupBy=`) is still a fresh visit, and the default is written
 *  beside it. An entry that carries the CARRIED_SEARCH router-state marker is never fresh, whatever its query:
 *  RouteTabs stamps it on a hand-over from the other tab, and every useFilters/useSort write stamps
 *  its own entry, so a cleared or All-time state survives a tab switch and Back (lib/hubNav.ts).
 *
 *  A `?win=<preset>` (a saved view or report that was saved on a preset) is a window REQUEST, not a filter: it is resolved
 *  against the newest data date the moment it is seen - `from` becomes the preset's start, `win` is removed - on mount and
 *  on every later navigation (the Views menu opens a saved view on the tab you are already on). A `win` beside explicit dates
 *  is dropped (the dates win); an unknown one is ignored with a warning (parseWin) and the entry is judged as if it had none.
 *  With no transaction yet there is no newest date: neither the default nor a `win` resolves, so no `from` is written (a
 *  `win` is still removed). */
export function useSpendingDefaults(): void {
  const { indexes } = useData();
  // the day the data reaches, never past the ingest day: every preset's anchor (Indexes.dataThrough)
  const maxDate = indexes.dataThrough;
  const { search, state } = useLocation();
  const [, setSp] = useSearchParams();
  const arrived = useRef(false);
  useEffect(() => {
    const first = !arrived.current;      // the default is for the entry this tab MOUNTED on, never a later filter change
    arrived.current = true;
    const sp = new URLSearchParams(search);
    const win = parseWin(sp);            // an unknown value warns here (once) and reads as none
    const hasFilter = FILTER_PARAM_KEYS.some((k) => sp.has(k));
    const fresh = first && !hasFilter && win === null && !arrivedWithCarriedSearch(state);
    if (!fresh && !sp.has("win")) return;
    // ONE write for both jobs: setSearchParams' updater sees the params of the last RENDER, so two writes in a row would undo
    // each other (a fresh visit with a junk `win` needs the default AND the junk removed)
    setSp((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("win");
      const dated = sp.has("from") || sp.has("to");
      const from = fresh ? presetRange(AUTO_WINDOW, maxDate)?.from : win && !dated ? presetRange(win, maxDate)?.from : undefined;
      if (from) next.set("from", from);
      return next;
    }, { replace: true, state: CARRIED_SEARCH });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, maxDate]);
}

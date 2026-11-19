// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useData } from "../data/DataContext";
import {
  DateRange, matchPreset, parseDateRange, parseWin, presetRange, RangePreset,
  writeDateRange, writeWin,
} from "./dateRange";

export interface DateRangeControl {
  range: DateRange;              // effective range (page default applied)
  explicit: boolean;             // URL carries a range or preset
  /** the data's last day (`Indexes.dataThrough`: the newest transaction date, never past the ingest day) — the preset
   *  anchor every window shares, so a row the bank dated ahead never moves a window (Cash Flow's 6M stays six months
   *  that hold data); null with no transaction, when no preset resolves */
  maxDate: string | null;
  set: (r: DateRange) => void;
  setPreset: (p: RangePreset) => void;
  /** preset to highlight, or null when the range is custom (or no preset resolves) */
  active: (presets: RangePreset[]) => RangePreset | null;
}

/**
 * Page-scoped date range with the URL as source of truth. A bare URL renders
 * the page's default preset without writing it back (defaults stay out of
 * URLs, matching the filters.ts convention). With no transaction yet a preset
 * (the default, a `?win=`) resolves to no bound, and All is the one highlighted.
 */
export function useDateRange(
  defaultPreset: RangePreset = "all",
  granularity: "day" | "month" = "day",
): DateRangeControl {
  const [sp, setSp] = useSearchParams();
  const { indexes } = useData();
  const maxDate = indexes.dataThrough;

  const { range, explicit, urlPreset } = useMemo(() => {
    const r = parseDateRange(sp);
    if (r.from !== undefined || r.to !== undefined)
      return { range: r, explicit: true, urlPreset: null as RangePreset | null };
    const win = parseWin(sp);
    if (win) return { range: presetRange(win, maxDate, granularity) ?? {}, explicit: true, urlPreset: win };
    return { range: presetRange(defaultPreset, maxDate, granularity) ?? {}, explicit: false, urlPreset: defaultPreset };
  }, [sp, maxDate, defaultPreset, granularity]);

  const set = useCallback(
    (r: DateRange) => setSp((prev) => writeDateRange(prev, r), { replace: true }),
    [setSp]);
  const setPreset = useCallback(
    (p: RangePreset) => setSp((prev) => writeWin(prev, p), { replace: true }),
    [setSp]);
  // with no data date only All can be the window shown (every other preset resolves to no bound), so it is lit then
  const active = useCallback(
    (presets: RangePreset[]) =>
      maxDate === null ? matchPreset(range, null, presets, granularity)
        : urlPreset && presets.includes(urlPreset)
        ? urlPreset
        : matchPreset(range, maxDate, presets, granularity),
    [urlPreset, range, maxDate, granularity]);

  return { range, explicit, maxDate, set, setPreset, active };
}

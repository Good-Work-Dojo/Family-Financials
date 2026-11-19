// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { DateRange, monthBounds, PRESET_LABELS, RangePreset } from "../filters/dateRange";
import { TAP } from "./ui";

/**
 * The one date-range control: preset buttons + from/to inputs. Presentational —
 * pages own the state (useDateRange) or map it into their filter set (FilterBar).
 * `unavailable` is why the presets cannot be resolved (no data date: presetRange's
 * null); when it is set every preset button but All (the empty window, which needs
 * no anchor) is disabled with it as its title.
 */
export default function DateRangeBar({
  from, to, onChange, onPreset, presets = ["3m", "6m", "12m", "ytd", "all"],
  activePreset = null, granularity = "day", unavailable, openEnds,
}: {
  from?: string;
  to?: string;
  onChange: (r: DateRange) => void;
  onPreset: (p: RangePreset) => void;
  presets?: RangePreset[];
  activePreset?: RangePreset | null;
  granularity?: "day" | "month";
  unavailable?: string;
  /** the days an open end of the window stands for (ISO; null when there is none): a preset leaves `to` open and All
   *  leaves `from` open too, and each open end stands for the newest (or oldest) day the open window really lets in - a
   *  row the bank dated ahead of the data's last day included - so typing the shown day into the box changes nothing. An
   *  open end's input shows that day, so a preset reads as the dates it covers, never as an empty "mm/dd/yyyy" or
   *  dashes; the window itself stays open. */
  openEnds?: { first: string | null; last: string | null };
}) {
  const month = granularity === "month";
  // what an input shows: the window's own bound, else the day its open end stands for
  const shownFrom = from ?? openEnds?.first ?? undefined;
  const shownTo = to ?? openEnds?.last ?? undefined;
  const asValue = (d: string | undefined) => (month ? d?.slice(0, 7) : d) ?? "";
  // a phone's chips and date boxes are 44px targets (TAP; the boxes 44px tall)
  const inputCls = "min-h-11 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-base text-zinc-300 sm:text-xs md:min-h-0";
  return (
    <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2 print:hidden">
      <div className="flex items-center gap-1">
        {presets.map((p) => {
          const off = unavailable !== undefined && p !== "all";
          return (
            <button key={p} type="button" onClick={() => onPreset(p)} disabled={off} title={off ? unavailable : undefined}
              className={`${TAP} rounded-md border px-2 py-1 text-xs ${off
                ? "cursor-not-allowed border-zinc-800 text-faint"
                : p === activePreset
                ? "border-zinc-500 bg-zinc-800 text-zinc-100"
                : "border-zinc-700 text-zinc-400 hover:text-zinc-100"}`}>
              {PRESET_LABELS[p]}
            </button>
          );
        })}
      </div>
      {/* from → to is one unit: on a phone it takes its own row and the two inputs share it, so the arrow can never be left
          at the end of a line with the second date wrapped away from it (the inputs shrink; on a wider screen it sits inline) */}
      <div data-testid="daterange-dates" className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
        <input type={month ? "month" : "date"} aria-label="from"
          value={asValue(shownFrom)}
          onChange={(e) => {
            const v = e.target.value;
            onChange({ from: v ? (month ? `${v}-01` : v) : undefined, to });
          }}
          className={`${inputCls} min-w-0 flex-1 sm:flex-none`} />
        <span aria-hidden="true" className="shrink-0 text-xs text-faint">→</span>
        <input type={month ? "month" : "date"} aria-label="to"
          value={asValue(shownTo)}
          onChange={(e) => {
            const v = e.target.value;
            onChange({ from, to: v ? (month ? monthBounds(v)[1] : v) : undefined });
          }}
          className={`${inputCls} min-w-0 flex-1 sm:flex-none`} />
      </div>
    </div>
  );
}

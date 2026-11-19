// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Tree-shaken ECharts bundle: register exactly what the app uses.
import { BarChart, GaugeChart, HeatmapChart, LineChart, PieChart, SankeyChart, ScatterChart, TreemapChart } from "echarts/charts";
import {
  CalendarComponent, DataZoomComponent, GridComponent, LegendComponent,
  MarkAreaComponent, MarkLineComponent, MarkPointComponent, TitleComponent,
  TooltipComponent, VisualMapComponent,
} from "echarts/components";
import { LabelLayout } from "echarts/features";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { fmtMonthAxis, fmtPct, fmtUSD0 } from "./format";

echarts.use([
  BarChart, LineChart, PieChart, HeatmapChart, GaugeChart, TreemapChart, ScatterChart, SankeyChart,
  GridComponent, TooltipComponent, LegendComponent, DataZoomComponent,
  MarkLineComponent, MarkAreaComponent, MarkPointComponent, TitleComponent,
  CalendarComponent, VisualMapComponent, LabelLayout, CanvasRenderer,
]);

// Chart color tokens. Canvas can't read CSS variables, so these are live `let`
// bindings (named ES imports stay current) swapped by setChartTheme(); the
// theme toggle remounts the chart tree so every memoized option rebuilds.
export let CHART_TEXT = "#a1a1aa";     // axis labels, legends
export let CHART_AXIS = "#3f3f46";     // axis lines, muted strokes
export let CHART_SPLIT = "#27272a";    // grid splitlines, hairlines
export let CHART_STRONG = "#fafafa";   // "ink" series: net-worth lines, gauge pointer
export let CHART_LABEL = "#e4e4e7";    // on-chart labels (sankey, heatmap cells)
export let CHART_SURFACE = "#09090b";  // empty calendar cells, gauge tick gaps
// the calendar's heat ramp, smallest day to largest: every colour along it, as ECharts interpolates it, at least 3:1
// against an empty day's cell (CHART_SURFACE) in both themes, so a day with spending never reads as a day with none
// (lib/contrast.test.ts samples it)
export let CHART_RAMP: readonly string[] = ["#3b82f6", "#f59e0b", "#ef4444"];
// grey roll-up series and context marks (Other, Target, Routine, fixed spend): at least 3:1 on a card in both themes
export let CHART_MUTED = "#71717a";
// a gain or an income drawn as a mark (bars, lines, a gauge's good band): POSITIVE in the dark theme, a deeper green in the
// light one, where POSITIVE stands at about 2.2:1 on a white card (lib/contrast.test.ts holds it to 3:1 in both). Text in
// green is not a mark and keeps its own classes
export let CHART_GAIN = "#22c55e";
// Cash Flow's total savings rate (incl. retirement), a dashed line beside the cash-basis rate (CHART_STRONG: white in the
// dark theme, near-black in the light one): a teal at least 3:1 on a card in both themes, told from the cash-basis line
// by its hue and its dashes, and from the green income bars by its hue
export let CHART_TOTAL_RATE = "#2dd4bf";

export function setChartTheme(theme: "dark" | "light"): void {
  const L = theme === "light";
  CHART_TEXT = L ? "#52525b" : "#a1a1aa";
  CHART_AXIS = L ? "#d4d4d8" : "#3f3f46";
  CHART_SPLIT = L ? "#e4e4e7" : "#27272a";
  CHART_STRONG = L ? "#18181b" : "#fafafa";
  CHART_LABEL = L ? "#3f3f46" : "#e4e4e7";
  CHART_SURFACE = L ? "#ffffff" : "#09090b";
  CHART_RAMP = L ? ["#2563eb", "#b45309", "#dc2626"] : ["#3b82f6", "#f59e0b", "#ef4444"];
  CHART_MUTED = L ? "#8a8a93" : "#71717a";
  CHART_GAIN = L ? "#15803d" : "#22c55e";
  CHART_TOTAL_RATE = L ? "#0f766e" : "#2dd4bf";
}

// self-initialize from the html class (set pre-paint by index.html's inline
// script) so a fresh import — including a dev-server hot reload — always
// agrees with the active theme
if (typeof document !== "undefined"
  && document.documentElement.classList.contains("light")) {
  setChartTheme("light");
}

export const baseGrid = { left: 8, right: 16, top: 32, bottom: 8, containLabel: true };

/** the phone legend's metrics: 10px names in 12px rows, 14 x 8 icons, 6px between two entries and between two rows,
 *  2px / 4px padding, a name drawn whole up to 200px (a row is about 300px at 390) */
const PHONE_LEGEND = { font: 10, row: 12, icon: 14, iconHeight: 8, gap: 6, padding: [2, 4], nameMax: 200 } as const;

/**
 * Phone-legibility pass over a chart option: a compact legend that wraps onto as many rows as it
 * needs (never a pager, which cut entries at its page edge), smaller axis labels with overlap
 * hiding (the newest category always named), confined tooltips, markPoint pins thinned on series
 * that opt in. Pure — every touched level is spread into a fresh object so callers' memoized
 * options are never mutated. Non-cartesian options (sankey/pie/gauge/treemap/calendar carry no
 * xAxis/yAxis) pass through untouched, but for a legend-bearing pie.
 */
export function tuneForMobile(option: any): object {
  if (!option) return option;
  // A plain legend in a box 92% of the chart wide, which ECharts wraps (wrapLegend's shape, with the phone's metrics):
  // every entry is on screen with its name, and EChart counts its rows (legendRows) to move the plot down. A name wider
  // than the phone's cap is cut in the MIDDLE (the full name shows in the legend's tooltip), keeping the tail that tells
  // two similar names apart. An author's own formatter is kept.
  const compactLegend = (l: any) => ({ ...l, type: "plain", wrap: true, left: "center", width: `${LEGEND_WIDTH * 100}%`,
    right: undefined, rowHeight: PHONE_LEGEND.row,
    itemWidth: PHONE_LEGEND.icon, itemHeight: PHONE_LEGEND.iconHeight, itemGap: PHONE_LEGEND.gap, padding: [...PHONE_LEGEND.padding],
    ...(l.formatter === undefined || l.formatter?.keepsTail
      ? { formatter: tailKeepingLegend(PHONE_LEGEND.nameMax, PHONE_LEGEND.font) } : {}),
    textStyle: { ...l.textStyle, fontSize: PHONE_LEGEND.font }, tooltip: { show: true } });
  if (option.xAxis == null && option.yAxis == null) {
    // Legend-bearing pie: the legend carries the names, so at phone width the
    // outside slice labels go (they collide with the legend) and the legend
    // drops to horizontal rows under a recentered pie (withLegendRows keeps the
    // pie its size when they are more than one). Other non-cartesian charts
    // (sankey, gauge, legendless pies) pass through.
    if (!option.legend || !option.series?.some?.((s: any) => s?.type === "pie"))
      return option;
    return {
      ...option,
      legend: { ...compactLegend(option.legend), orient: "horizontal", bottom: 0, top: undefined },
      ...(option.tooltip ? { tooltip: { ...option.tooltip, confine: true } } : {}),
      series: option.series.map((s: any) => s?.type !== "pie" ? s : {
        ...s, center: ["50%", "46%"],
        label: { ...s.label, show: false }, labelLine: { ...s.labelLine, show: false },
      }),
    };
  }
  // A markPoint pin is a ~34px head with a label wider than it. A series that sets `phoneMaxPins`
  // (Home's net-worth line) keeps only its newest N pins on a phone - pin data runs oldest ->
  // newest - because its pins sit a few months apart on a multi-year time axis and stack into one
  // blob. Opt-in, never global: a chart that never set the flag keeps every pin.
  const thinPins = (s: any): any => {
    const max = s?.phoneMaxPins;
    return typeof max === "number" && s?.markPoint?.data?.length > max
      ? { ...s, markPoint: { ...s.markPoint, data: s.markPoint.data.slice(-Math.max(1, max)) } } : s;
  };
  // Axis labels shrink to 10px (an author's smaller size is kept) and hide on overlap, unless the
  // author opted out with `hideOverlap: false` (labels already sized to fit, e.g. Home's waterfall,
  // whose five categories must all be named). A category x-axis also keeps its LAST label: the
  // auto interval would otherwise skip it, dropping the newest month at 390px. Under a chart of
  // lines alone that label ends at its point instead of centring on it: a line's months are narrow
  // bands, and a centred "Sep ’26" hung past the chart's edge, cut to "Sep ’2".
  const linesOnly = Array.isArray(option.series) && option.series.length > 0
    && option.series.every((s: any) => s?.type === "line");
  const axis = (a: any, isX: boolean): any => Array.isArray(a) ? a.map((x) => axis(x, isX))
    : a ? { ...a, axisLabel: {
      ...a.axisLabel,
      fontSize: Math.min(a.axisLabel?.fontSize ?? 10, 10),
      hideOverlap: a.axisLabel?.hideOverlap !== false,
      ...(isX && a.type === "category" ? { showMaxLabel: a.axisLabel?.showMaxLabel ?? true } : {}),
      ...(isX && a.type === "category" && linesOnly ? { alignMaxLabel: a.axisLabel?.alignMaxLabel ?? "right" } : {}),
    } } : a;
  return {
    ...option,
    ...(option.legend ? { legend: compactLegend(option.legend) } : {}),
    // the phone gutter is 8px, with two exceptions that keep the author's: more than the default 16
    // (a second value axis, a label drawn outside the plot) and a VALUE x-axis, whose last tick
    // label ("$18K", a year) hangs half its width past the plot edge and would be cut off at 8
    ...(option.grid ? { grid: { ...option.grid, right:
      (option.grid.right ?? 0) > 16 || [option.xAxis].flat().some((a: any) => a?.type === "value")
        ? option.grid.right : 8 } } : {}),
    ...(option.tooltip ? { tooltip: { ...option.tooltip, confine: true } } : {}),
    xAxis: axis(option.xAxis, true),
    yAxis: axis(option.yAxis, false),
    ...(Array.isArray(option.series) ? { series: option.series.map(thinPins) } : {}),
  };
}

/** A value axis in the theme's colours: its labels through `formatter`, its split lines the theme's hairline (ECharts'
 *  own default is a light grey that glares on the dark theme). A line chart of a level spreads levelScale over it, which
 *  leaves zero only for a level that never nears it; a bar chart or a stacked area keeps the zero baseline, where it
 *  means something. */
export function valueAxis(formatter: (v: number) => string) {
  return {
    type: "value" as const,
    axisLabel: { color: CHART_TEXT, formatter },
    splitLine: { lineStyle: { color: CHART_SPLIT } },
  };
}
/** a value axis of money (valueAxis) */
export const moneyAxis = valueAxis;

/** The smallest step between two ticks at which fmtCompact prints them apart, for values up to `max` in size: a dollar
 *  under $1K, $100 to $10K ("$1.4K"), $1K to $1M ("$14K"), $10K to $10M ("$1.23M"), $100K beyond. An axis scaled to its
 *  data spans a narrow range, where ECharts' own step can be finer than the labels print ("$1.4K" three times): such an
 *  axis takes this as its `minInterval`. */
export function compactStep(max: number): number {
  const a = Math.abs(max);
  return a >= 10_000_000 ? 100_000 : a >= 1_000_000 ? 10_000 : a >= 10_000 ? 1_000 : a >= 1_000 ? 100 : 1;
}
/** the step of a percent axis labelled in whole percents (fmtPct(v, 0)), a share as a fraction */
export const WHOLE_PERCENT_STEP = 0.01;

/** Whether a line chart's value axis may leave zero: only for a level that never nears it - every value drawn above
 *  zero and the smallest at least half the largest. Otherwise the axis keeps zero: a line that dips near it or crosses
 *  it, anything negative, nothing drawn. */
export function leavesZero(values: readonly number[]): boolean {
  if (values.length === 0) return false;
  let lo = Infinity, hi = -Infinity;
  for (const v of values) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return lo > 0 && lo >= hi / 2;
}

/** The value axis' scaling for a line chart of a level (spread over its valueAxis): `scale`, at a step (`minInterval`)
 *  its labels tell apart over the narrow range, when the values leave zero (leavesZero); nothing otherwise, so the axis
 *  keeps zero. Home's net-worth lines, Trends' liquidity lines and the balance charts take it; a bar chart or a stacked
 *  area never does. */
export function levelScale(values: readonly number[], step: number): { scale?: true; minInterval?: number } {
  return leavesZero(values) ? { scale: true, minInterval: step } : {};
}

/** The bounds of a share's axis (a fraction from 0 to 1) scaled to its line: never past 100% nor below 0%, where
 *  ECharts pads a lone point, or a line that touches 100%, out to 160%. A line within the range keeps its own. */
export function shareBounds() {
  return {
    min: (v: { min: number }) => (v.min <= 0 ? 0 : undefined),
    max: (v: { max: number }) => (v.max >= 1 ? 1 : undefined),
  };
}

export function catAxis(data: string[]) {
  return {
    type: "category" as const,
    data,
    axisLabel: { color: CHART_TEXT },
    axisLine: { lineStyle: { color: CHART_AXIS } },
  };
}

const HALF_DAY = 43_200_000;
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A date axis (ECharts' time axis; the data are days, read at local midnight). Its labels are dates, never clock times:
 *  a tick between two midnights is left unlabelled. A year's first day reads the year, a month's first day the month,
 *  any other day its day of the month, except the first labelled day of each month, which names the month too ("Oct
 *  27"): so an axis of day numbers says which month it counts on its first tick and wherever the month changes. With
 *  every point on one date the axis spans that day alone (half a day each side, where ECharts would add a day each
 *  side and label six-hour ticks), so its one label is that date in full. The labels are drawn in tick order, so the
 *  months already named restart with the first tick; ECharts adds a tick at each end of the axis, which can fall on an
 *  inner tick's day, so a month's naming tick is known by its value and a second tick there reads the same. */
export function timeAxis() {
  let single = false;
  /** each month named so far (year * 12 + month) and the value of the tick that names it */
  let named = new Map<number, number>();
  return {
    type: "time" as const,
    min: (v: { min: number; max: number }) => {
      single = v.min === v.max;
      return single ? v.min - HALF_DAY : undefined;
    },
    max: (v: { min: number; max: number }) => (v.min === v.max ? v.max + HALF_DAY : undefined),
    axisLabel: {
      color: CHART_TEXT,
      formatter: (value: number, index: number) => {
        if (index === 0) named = new Map();
        const d = new Date(value);
        if (d.getHours() !== 0 || d.getMinutes() !== 0) return "";
        const month = MONTH_NAMES[d.getMonth()];
        if (single) return `${month} ${d.getDate()}, ${d.getFullYear()}`;
        const key = d.getFullYear() * 12 + d.getMonth();
        if (!named.has(key)) named.set(key, value);
        if (d.getDate() === 1) return d.getMonth() === 0 ? String(d.getFullYear()) : month;
        return named.get(key) === value ? `${month} ${d.getDate()}` : String(d.getDate());
      },
    },
    axisLine: { lineStyle: { color: CHART_AXIS } },
  };
}

/** the foot and the top of rateAxis, and its step */
export const RATE_FOOT = -0.5;
export const RATE_TOP = 1;
export const RATE_STEP = 0.25;

/** A savings-rate axis on the right of a money chart: −50% to 100% in even 25-point steps. The range is fixed (a rate is
 *  at most 100%; a household with little income would otherwise stretch it to thousands of percent), and a month below
 *  the foot is drawn AT the foot (pinRates). The step is fixed too: ECharts' own step over a fixed range left the two
 *  ends uneven (100%, 90%, 60%, …, −30%, −50%). No split lines: the money axis draws those. */
export function rateAxis() {
  return { type: "value" as const, min: RATE_FOOT, max: RATE_TOP, interval: RATE_STEP,
    axisLabel: { color: CHART_TEXT, formatter: (v: number) => fmtPct(v, 0) }, splitLine: { show: false } };
}

/** the multiples of a power of ten an axis step is taken from */
export const STEP_LADDER: readonly number[] = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
/** the least step on STEP_LADDER at or above `need` (at least 1) */
export function ladderStep(need: number): number {
  const n = Math.max(1, need);
  const p = 10 ** Math.floor(Math.log10(n));
  return STEP_LADDER.map((m) => m * p).find((s) => s >= n - 1e-9)!;
}

/** The money axis beside rateAxis. It shares the rate axis' grid - the same six even steps, $0 on the line of 0% (two
 *  steps above the foot) - only when that costs nothing: the tallest bar above zero is at least as tall as the deepest
 *  below it, so the four steps above zero are the room the bars need anyway. Otherwise (one month's spending deeper
 *  than any month's income is tall) the shared grid would leave most of the plot above zero empty, and the money axis
 *  takes even steps of its own (the least ladder step that spans both ends in at most six), its top the first step at
 *  or above the tallest bar; the two zeros may then sit apart. No bar is clipped either way and no value moves. */
export function moneyAxisBesideRate(values: readonly number[], formatter: (v: number) => string) {
  const up = Math.max(0, ...values), down = Math.max(0, ...values.map((v) => -v));
  if (up >= down) {
    const above = (RATE_TOP - 0) / RATE_STEP, below = (0 - RATE_FOOT) / RATE_STEP;
    const step = ladderStep(Math.max(up / above, down / below));
    return { ...moneyAxis(formatter), min: -below * step, max: above * step, interval: step };
  }
  for (let step = ladderStep((up + down) / 6); ; step = ladderStep(step * 1.0001)) {
    const above = Math.ceil(up / step), below = Math.ceil(down / step);
    if (above + below <= 6) return { ...moneyAxis(formatter), min: -below * step, max: above * step, interval: step };
  }
}

/** a rate drawn at rateAxis' foot: a downward triangle, so the pin reads as "below the chart" */
export type PinnedRate = { value: number; symbol: "triangle"; symbolRotate: 180; symbolSize: number };

/** A rate line's points on rateAxis: a rate below the foot is drawn at the foot as a downward triangle, so the line stays
 *  on the plot and the pin is seen; a gap stays a gap. The real rates are the caller's for the tooltip and the table;
 *  `pinned` says whether any month was pinned, for the line under the chart, and `all` whether every month with a rate
 *  was (at least one has one): a line of pins along the foot says nothing, so the caller draws no line then. */
export function pinRates(rates: readonly (number | null)[]): {
  data: (number | null | PinnedRate)[]; pinned: boolean; all: boolean;
} {
  let pinned = false;
  const data = rates.map((r) => {
    if (r === null || r >= RATE_FOOT) return r;
    pinned = true;
    return { value: RATE_FOOT, symbol: "triangle" as const, symbolRotate: 180 as const, symbolSize: 9 };
  });
  return { data, pinned, all: pinned && rates.every((r) => r === null || r < RATE_FOOT) };
}

/** The legend of a chart that lists only the series it draws: a series every point of which is null (a rate with no
 *  full month yet) draws nothing, so it has no entry, though it stays in the tooltip ("—"); so does a bar series every
 *  value of which is 0 (nothing invested in any month), which draws no bar. */
export function drawnLegend(series: readonly { name: string; type?: string; data: readonly unknown[] }[]) {
  const drawn = (v: unknown, bar: boolean) => v !== null && v !== undefined
    && !(Array.isArray(v) && (v[1] === null || v[1] === undefined)) && !(bar && v === 0);
  return { ...baseLegend(), data: series.filter((s) => s.data.some((v) => drawn(v, s.type === "bar"))).map((s) => s.name) };
}

/** category axis of YYYY-MM labels rendered through fmtMonthAxis ("Oct ’26"); a month in `partial` is starred
 *  ("Oct ’26*") on the axis only - the category value stays the month key a click handler reads */
export function monthAxis(labels: string[], partial?: ReadonlySet<string>) {
  return { ...catAxis(labels), axisLabel: { color: CHART_TEXT,
    formatter: partial?.size ? (m: string) => fmtMonthAxis(m) + (partial.has(m) ? "*" : "") : fmtMonthAxis } };
}

/** One money value in a tooltip. ECharts hands a valueFormatter `undefined` for a null point (a gap in a line), so a
 *  formatter that coalesces to 0 prints "$0" for "no data" - a number the chart never drew. A gap reads "—" (not
 *  an amount, so privacy mode leaves it); everything else goes through `fmt`, which masks. */
export function moneyValue(v: number | null | undefined, fmt: (n: number) => string = fmtUSD0): string {
  return v == null ? "—" : fmt(v);
}

/** Escape text for a tooltip's HTML string (ECharts sets a formatter's return as innerHTML). Any text that comes
 *  from the data - a vendor name built from a bank descriptor - goes through this; amounts need none (the money
 *  formatters build them). */
export const encodeHTML = (text: string): string => echarts.format.encodeHTML(text);

/** A gauge's reading of `value` on a dial from 0 to `max`: the needle stays on the dial, pinned at its end when the value
 *  is past it (`over`), and the end's tick label then reads "max+", so a pinned needle never claims the end value; the
 *  real value is the gauge's own text (its detail), never the needle. `null` (nothing to read) puts the needle at 0,
 *  which the caller hides. `tick` labels the dial's own ticks, and prints nothing for a value off the dial. */
export function gaugeReading(value: number | null, max: number): { needle: number; over: boolean; tick: (v: number) => string } {
  const over = value !== null && value > max;
  return {
    needle: value === null ? 0 : Math.max(0, Math.min(max, value)),
    over,
    tick: (v: number) => (typeof v !== "number" || v < 0 || v > max ? "" : over && v === max ? `${v}+` : String(v)),
  };
}

/** the money-flow sankey's label size, and the gap between two nodes of one column: more than a label's line (1.2 x
 *  its size), so the thin nodes of the small income streams and spending categories each keep their label */
export const SANKEY_LABEL_SIZE = 11;
export const SANKEY_NODE_GAP = 16;

/** The money-flow sankey (Cash Flow → Income statement): the income streams → the household → where the money went.
 *  Every node is labelled: the streams to their right, the household to its right (over its own outflows, half the
 *  chart away from the terminal column), the terminal column to its left, over the flows ("right" and "insideLeft" clip
 *  at the chart's right edge). hideOverlap stays as the guard of a chart too narrow for every label; the tooltip names
 *  every node. */
export function sankeyOption(flows: { nodes: { name: string }[]; links: { source: string; target: string; value: number }[] }) {
  return {
    tooltip: { trigger: "item", valueFormatter: (v: number | null | undefined) => moneyValue(v) },
    series: [{
      type: "sankey", nodeAlign: "left", left: 8, right: 16, top: 10, bottom: 10, nodeGap: SANKEY_NODE_GAP,
      // the reveal is a left-to-right clip: a capture inside its first second shows the
      // right-hand nodes (and their labels) missing, which reads as a layout bug
      animation: false,
      emphasis: { focus: "adjacency" },
      data: flows.nodes, links: flows.links,
      lineStyle: { color: "gradient", opacity: 0.35, curveness: 0.55 },
      label: { color: CHART_LABEL, fontSize: SANKEY_LABEL_SIZE, overflow: "truncate", width: 120 },
      labelLayout: { hideOverlap: true },
      levels: [{ depth: 0 }, { depth: 1, label: { position: "right" } }, { depth: 2, label: { position: "left" } }],
      itemStyle: { borderWidth: 0 },
    }],
  };
}

/** the sankey's height: at least 360px, and enough that its fullest column keeps about 220px for the flows themselves
 *  once the gaps between its nodes are taken out (the outer columns are the links' sources and targets, less the
 *  household node in the middle, which is both) */
export function sankeyHeight(option: ReturnType<typeof sankeyOption>): number {
  const links = option.series[0].links;
  const fullest = Math.max(new Set(links.map((l) => l.source)).size, new Set(links.map((l) => l.target)).size) - 1;
  return Math.max(360, 240 + Math.max(0, fullest - 1) * SANKEY_NODE_GAP);
}

/** the standard money tooltip: whole-dollar USD values, a dash for a gap */
export function moneyTooltip(trigger: "axis" | "item" = "axis") {
  return { trigger, valueFormatter: (v: number | null | undefined) => moneyValue(v) };
}

// Legend fragments are functions (like the axis helpers above), not consts:
// a module-level object would freeze CHART_TEXT at load time and go stale
// when setChartTheme() swaps the live bindings.
export function baseLegend() {
  return { textStyle: { color: CHART_TEXT } };
}

// ---------------------------------------------------------------- a legend that wraps

/** ECharts' legend metrics (its defaults): 5px padding, 10px between two entries and between two rows, a 25px icon with
 *  5px before the 12px text, 14px rows */
const LEGEND = { pad: 5, gap: 10, icon: 25, iconGap: 5, row: 14, font: 12 } as const;
/** the share of the chart's width a wrapping legend's rows may take (centred, as a one-row legend is) */
const LEGEND_WIDTH = 0.92;
/** the widest a legend name is drawn before it is cut in the middle */
const LEGEND_NAME_MAX = 240;
/** what each row past the first takes: a row and the gap above it */
export const LEGEND_ROW_STEP = LEGEND.row + LEGEND.gap;

/** A legend that wraps onto as many rows as its entries need and never pages, so every entry is on screen with its
 *  name: a plain legend whose box is LEGEND_WIDTH of the chart (ECharts wraps a plain legend only within a set width).
 *  EChart moves the plot down, and makes the chart taller, by the rows past the first it takes at the chart's own width
 *  (legendRows). A name wider than LEGEND_NAME_MAX is cut in the middle (middleEllipsis: two accounts that share a long
 *  prefix keep their last-four apart) with its full name in the legend's tooltip. On a phone tuneForMobile turns it into
 *  the compact strip. */
export function wrapLegend() {
  return { type: "plain" as const, wrap: true as const, left: "center", width: `${LEGEND_WIDTH * 100}%`,
    textStyle: { color: CHART_TEXT }, formatter: tailKeepingLegend(LEGEND_NAME_MAX, LEGEND.font), tooltip: { show: true } };
}

/** the names a legend lists: its own `data`, else every named series' name (a pie's slices by theirs), each once */
function legendNames(option: any): string[] {
  const own = option?.legend?.data;
  const names: string[] = Array.isArray(own)
    ? own.map((d: unknown) => (typeof d === "string" ? d : String((d as { name?: unknown })?.name ?? "")))
    : [option?.series ?? []].flat().flatMap((s: any) => (s?.type === "pie"
      ? (s.data ?? []).map((d: any) => String(d?.name ?? "")) : [s?.name == null ? "" : String(s.name)]));
  return [...new Set(names.filter((n) => n !== ""))];
}

/** A wrapping legend's metrics as ECharts lays it out: its own where it states them (the phone's compact legend), else
 *  ECharts' defaults (LEGEND); `row` is the height of a row of names (`rowHeight`, a key ECharts ignores) */
function legendMetrics(l: any): { pad: number; gap: number; icon: number; font: number; row: number } {
  const p = l?.padding;
  const pad = typeof p === "number" ? p : Array.isArray(p) ? Number(p[1] ?? p[0]) : LEGEND.pad;
  return { pad, gap: l?.itemGap ?? LEGEND.gap, icon: l?.itemWidth ?? LEGEND.icon,
    font: l?.textStyle?.fontSize ?? LEGEND.font, row: l?.rowHeight ?? LEGEND.row };
}

/** what each row of a wrapping legend past the first takes: a row and the gap above it (LEGEND_ROW_STEP for the
 *  desktop legend, less for the phone's compact one) */
export function legendRowStep(option: any): number {
  const m = legendMetrics(option?.legend);
  return m.row + m.gap;
}

/** How many rows a wrapping legend (wrapLegend, or the phone's compact legend) takes on a chart `width` px wide, laid
 *  out as ECharts lays it: each entry is the icon, its gap and the name as the legend's formatter draws it, entries
 *  `itemGap` apart, a new row when an entry would pass the legend's width less its padding. null for an option with no
 *  wrapping legend and for a chart with no width yet. */
export function legendRows(option: any, width: number, measure: (t: string, size: number) => number = textWidth): number | null {
  const l = option?.legend;
  if (!l?.wrap || l.type === "scroll" || !(width > 0)) return null;
  const m = legendMetrics(l);
  // the legend's box less its padding on each side: an entry ending inside that padding wraps (counting it on the row
  // would leave a row drawn over the plot; counting one row too many only leaves a little room)
  const room = width * LEGEND_WIDTH - 2 * m.pad;
  const label = (n: string) => (typeof l.formatter === "function" ? String(l.formatter(n)) : n);
  let rows = 1, x = 0;
  for (const name of legendNames(option)) {
    const w = m.icon + LEGEND.iconGap + measure(label(name), m.font);
    if (x > 0 && x + w > room) { rows++; x = 0; }
    x += w + m.gap;
  }
  return rows;
}

/** `option` with room for `extraRows` legend rows (the rows past the first a wrapping legend takes), each `step` px
 *  (legendRowStep), in a chart made that much taller: a plot moves down (the grid's top holds one row); a pie whose
 *  legend sits under it (the phone's) keeps the centre and radius it had in the chart's `size` before the rows were
 *  added, in px, so the taller chart does not grow it into its legend. The same object when there are none. */
export function withLegendRows(option: any, extraRows: number, step: number = LEGEND_ROW_STEP,
                               size?: { width: number; height: number }): any {
  if (!(extraRows > 0) || !option) return option;
  if (option.grid && !Array.isArray(option.grid)) {
    return { ...option, grid: { ...option.grid, top: (option.grid.top ?? baseGrid.top) + extraRows * step } };
  }
  if (!size || !(size.width > 0) || !Array.isArray(option.series) || option.legend?.bottom === undefined) return option;
  const px = (v: unknown, of: number) => (typeof v === "string" && v.endsWith("%") ? (parseFloat(v) / 100) * of : Number(v));
  const half = Math.min(size.width, size.height) / 2;
  return {
    ...option,
    series: option.series.map((s: any) => {
      if (s?.type !== "pie") return s;
      const center = s.center ?? ["50%", "50%"];
      const radius = Array.isArray(s.radius) ? s.radius : [0, s.radius ?? "75%"];
      return { ...s, center: [center[0], px(center[1], size.height)], radius: radius.map((r: unknown) => px(r, half)) };
    }),
  };
}

// ---------------------------------------------------------------- text that must fit on the canvas

/** em widths for estimateTextWidth, erring WIDE of the fonts a "sans-serif" resolves to (DejaVu Sans on the Linux
 *  screenshot box, Helvetica/SF on the family's devices), so a fit test errs towards hiding a label */
const NARROW = new Set([..." .,:;'|!ilj"]);
const SEMI = new Set([..."ftrI()[]-/\"*"]);
function emWidth(ch: string): number {
  if (NARROW.has(ch)) return 0.34;
  if (SEMI.has(ch)) return 0.44;
  if ("mwMW".includes(ch)) return 1.0;
  if (/[a-z0-9$]/.test(ch)) return 0.65;
  if (/[A-Z]/.test(ch)) return 0.8;
  return 1.0;                                        // anything else (…, ▶, •, %, &, accents): a full em
}
/** a canvas-free width estimate (jsdom, a browser without OffscreenCanvas) */
export function estimateTextWidth(text: string, fontSize = 12): number {
  let em = 0;
  for (const ch of text) em += emWidth(ch);
  return em * fontSize;
}

let measureCtx: OffscreenCanvasRenderingContext2D | null | undefined;
/** The width in px of `text` at `fontSize`px in the charts' font (ECharts/zrender draw "<n>px sans-serif"):
 *  measured on an OffscreenCanvas - the same metrics the chart draws with - where there is one (every current
 *  browser), else estimated (estimateTextWidth). OffscreenCanvas, not a <canvas>: jsdom has none, so tests take
 *  the estimate without jsdom's "not implemented" error. */
export function textWidth(text: string, fontSize = 12): number {
  if (measureCtx === undefined) {
    try {
      measureCtx = typeof OffscreenCanvas === "function" ? new OffscreenCanvas(1, 1).getContext("2d") : null;
    } catch {
      measureCtx = null;
    }
  }
  if (!measureCtx) return estimateTextWidth(text, fontSize);
  measureCtx.font = `${fontSize}px sans-serif`;
  return measureCtx.measureText(text).width;
}

const ELLIPSIS = "…";
/** `text` cut in the MIDDLE to fit `maxWidth` px, keeping its head and the tail that tells it apart from a
 *  neighbour with the same prefix: what follows the last "..." (the accounts' last-four convention, "Sample Bank
 *  Card ...0000"), else the last word, else the last four characters - at most 60% of the room. An end ellipsis
 *  cut "...0000" and "...1111 (Sample)" to the same "Sample Bank Ca…". */
export function middleEllipsis(text: string, maxWidth: number, fontSize = 12,
                               measure: (t: string, size: number) => number = textWidth): string {
  const w = (t: string) => measure(t, fontSize);
  if (w(text) <= maxWidth) return text;
  const dots = Math.max(text.lastIndexOf("..."), text.lastIndexOf(ELLIPSIS));
  let tail = (dots >= 0 ? text.slice(dots).replace(/^[.…\s]+/, "") : (text.match(/\S+\s*$/)?.[0] ?? "")).trim();
  if (!tail || tail === text.trim()) tail = [...text.trim()].slice(-4).join("");
  const kept = [...tail];
  while (kept.length > 1 && w(ELLIPSIS + kept.join("")) > maxWidth * 0.6) kept.shift();
  tail = kept.join("").trimStart();
  if (w(ELLIPSIS + tail) > maxWidth) return ELLIPSIS;
  const head = [...text];
  let n = 0;
  while (n < head.length && w(head.slice(0, n + 1).join("").trimEnd() + ELLIPSIS + tail) <= maxWidth) n++;
  return head.slice(0, n).join("").trimEnd() + ELLIPSIS + tail;
}

/** a legend formatter that cuts in the middle to `width` px at `fontSize`; marked, so the phone pass
 *  (tuneForMobile) knows it may swap in its narrower one */
function tailKeepingLegend(width: number, fontSize: number) {
  const f = (name: string) => middleEllipsis(String(name), width - 2, fontSize);
  return Object.assign(f, { keepsTail: true as const });
}

/** An option drawn with no motion (a viewer who asked for less): `animation: false` chart-wide and on every series,
 *  since a series type's own default (a treemap's `animation: true`) outranks the chart-wide flag. */
export function stillOption<T extends object>(option: T): T {
  const series = (option as { series?: unknown }).series;
  const still = (s: unknown) => (s && typeof s === "object" ? { ...s, animation: false } : s);
  return { ...option, animation: false,
    ...(series === undefined ? {} : { series: Array.isArray(series) ? series.map(still) : still(series) }) };
}

// ---------------------------------------------------------------- treemap tile labels

/** A tile's content box (inside its border) and whether ECharts prefixes the drill-down icon to its label. */
export interface TileBox { width: number; height: number; drillIcon: boolean }
const TILE_BOXES = new Map<string, Map<number, TileBox>>();

/** A layout stage that runs right after ECharts' own treemap layout (a higher priority number runs later) and
 *  before the render that formats the labels: it records every tile's box by series id and data index, so a
 *  label formatter can ask whether its text fits (treemapTileBox / fitTileLabel). Exported for its test. */
export const TREEMAP_TILE_STAGE = {
  seriesType: "treemap",
  reset(seriesModel: any): void {
    const data = seriesModel.getData();
    const boxes = new Map<number, TileBox>();
    data.each((i: number) => {
      const l = data.getItemLayout(i);
      if (!l) return;
      const b = l.borderWidth ?? 0;
      boxes.set(i, { width: Math.max(0, l.width - 2 * b), height: Math.max(0, l.height - 2 * b), drillIcon: !!l.isLeafRoot });
    });
    TILE_BOXES.set(seriesModel.id, boxes);
  },
};
echarts.registerLayout(echarts.PRIORITY.VISUAL.POST_CHART_LAYOUT, TREEMAP_TILE_STAGE as any);

/** the box of tile `dataIndex` in the treemap series `seriesId` (its option `id`), as last laid out */
export function treemapTileBox(seriesId: string, dataIndex: number | undefined): TileBox | undefined {
  return dataIndex === undefined ? undefined : TILE_BOXES.get(seriesId)?.get(dataIndex);
}

/** The first candidate label (lines) that fits the tile WHOLE - inside ECharts' 5px label padding - else "".
 *  ECharts truncates a label that does not fit, which on a phone's small tiles read as one letter and an ellipsis
 *  ("T…", "$4…"); the tooltip names every tile, so a label is shown whole or not at all. A category that has
 *  children gets the drill-down icon ("▶ ") on its first line HERE, measured with it: the series must set
 *  `drillDownIcon: ""`, because ECharts' own icon is drawn even on an empty label (a lone ▶ on every unlabelled
 *  tile). An unknown box (no layout recorded) takes the first candidate. */
export function fitTileLabel(box: TileBox | undefined, candidates: string[][], fontSize = 12,
                             measure: (t: string, size: number) => number = textWidth): string {
  if (!candidates.length) return "";
  if (!box) return candidates[0].join("\n");
  const PAD = 5, lineHeight = fontSize * 1.25;
  const room = { w: box.width - 2 * PAD, h: box.height - 2 * PAD };
  for (const lines of candidates) {
    const drawn = lines.map((l, i) => (i === 0 && box.drillIcon ? `▶ ${l}` : l));
    if (drawn.length * lineHeight <= room.h && drawn.every((l) => measure(l, fontSize) <= room.w)) return drawn.join("\n");
  }
  return "";
}

/** A treemap's last word on its labels, as a series `labelLayout`, with the label drawn uncut (`overflow: "none"`):
 *  ECharts calls it as it draws, with the tile's rect and the label's own, and a label that does not fit its tile inside
 *  8px a side is hidden (font size 0) rather than cut. fitTileLabel chooses the lines from the tiles the layout
 *  stage recorded; in the browser that record can belong to an earlier layout of the chart (a phone drew "▶ Trans..."
 *  from a tile 15% wider than the one on screen), so this check, on the rects drawn, is what keeps a word whole. ECharts
 *  hands it the breadcrumb's crumbs too, which belong to no tile (no data index): a crumb is sized to its own text, so
 *  it is left as it is - hidden, it left an empty dark bar under the tiles where "All categories" belongs. */
export function treemapLabelFit(p: { dataIndex?: number; rect?: { width: number; height: number };
  labelRect?: { width: number; height: number } }): { fontSize?: number } {
  // 5px is the label's own padding; the rect handed over is the tile's box before the drawn tile loses its border and
  // the gap to its neighbour, which in the browser left a label 2px from the tile's edge where 5 were asked: 3px more
  // a side covers them, so a label kept is at least the padding inside the tile it is drawn on
  const PAD = 8;
  if (p.dataIndex === undefined || p.dataIndex === null || !p.rect || !p.labelRect) return {};
  return p.labelRect.width > p.rect.width - 2 * PAD || p.labelRect.height > p.rect.height - 2 * PAD ? { fontSize: 0 } : {};
}

export default echarts;

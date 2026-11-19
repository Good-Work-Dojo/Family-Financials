// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { afterEach, describe, expect, it } from "vitest";
import {
  baseGrid, baseLegend, compactStep, encodeHTML, estimateTextWidth, fitTileLabel, gaugeReading, LEGEND_ROW_STEP,
  ladderStep, leavesZero, legendRows, legendRowStep, levelScale, middleEllipsis, moneyAxisBesideRate, pinRates, moneyTooltip, moneyValue, monthAxis, SANKEY_LABEL_SIZE, SANKEY_NODE_GAP, sankeyHeight,
  sankeyOption, shareBounds, stillOption, timeAxis, TREEMAP_TILE_STAGE, treemapLabelFit, treemapTileBox, tuneForMobile, withLegendRows,
  wrapLegend,
} from "./echarts";
import { fmtCompact } from "./format";
import { MASK, setPrivate } from "./privacy";

/** one unit per character: the tests below reason in characters, not font metrics */
const perChar = (s: string) => [...s].length;

afterEach(() => setPrivate(false));

describe("the money-flow sankey labels every node", () => {
  const HUB = "Household";
  const cats = ["Housing", "Saved (cash)", "Transport", "Groceries", "Invested", "Dining", "Utilities", "Taxes", "Shopping",
    "Cash & Misc", "Subscriptions", "Health", "Other spending"];
  const flows = {
    nodes: [{ name: "Salary" }, { name: "Interest" }, { name: "Other" }, { name: HUB }, ...cats.map((name) => ({ name }))],
    links: [
      ...["Salary", "Interest", "Other"].map((source, i) => ({ source, target: HUB, value: [139_000, 2_200, 1_200][i] })),
      ...cats.map((target, i) => ({ source: HUB, target, value: 40_000 / (i + 1) })),
    ],
  };
  const series = sankeyOption(flows).series[0];
  it("no depth hides its label: the streams, the household in the middle and the terminal column each name their nodes", () => {
    for (const level of series.levels) expect((level as { label?: { show?: boolean } }).label?.show, `depth ${level.depth}`).not.toBe(false);
    expect((series.label as { show?: boolean }).show).not.toBe(false);
  });
  it("two neighbours of a column sit further apart than a label's line, so the thin small ones keep theirs", () => {
    expect(series.nodeGap).toBe(SANKEY_NODE_GAP);
    expect(SANKEY_NODE_GAP).toBeGreaterThan(SANKEY_LABEL_SIZE * 1.2);
  });
  it("the chart grows with its fullest column, so the gaps never squeeze the flows under about 220px", () => {
    // 13 terminal nodes: 12 gaps
    expect(sankeyHeight(sankeyOption(flows))).toBe(240 + 12 * SANKEY_NODE_GAP);
    const few = { nodes: flows.nodes.slice(0, 6), links: flows.links.slice(0, 5) };
    expect(sankeyHeight(sankeyOption(few))).toBe(360);
  });
});

describe("gaugeReading", () => {
  it("past the dial's end: the needle pinned at the end, whose tick reads with a +", () => {
    const r = gaugeReading(28.6, 12);
    expect([r.needle, r.over, r.tick(12), r.tick(9), r.tick(0)]).toEqual([12, true, "12+", "9", "0"]);
  });
  it("at the end exactly, within, below and with nothing to read: no +, the needle on the value (0 for nothing)", () => {
    expect([gaugeReading(12, 12).needle, gaugeReading(12, 12).tick(12)]).toEqual([12, "12"]);
    expect([gaugeReading(4.5, 12).needle, gaugeReading(4.5, 12).over]).toEqual([4.5, false]);
    expect(gaugeReading(-1, 12).needle).toBe(0);
    expect([gaugeReading(null, 12).needle, gaugeReading(null, 12).tick(12)]).toEqual([0, "12"]);
  });
  it("a value off the dial has no tick label: the formatter never prints an arbitrary number", () => {
    expect([gaugeReading(30, 12).tick(13), gaugeReading(30, 12).tick(-1), gaugeReading(30, 12).tick(123_456)]).toEqual(["", "", ""]);
  });
});

// ECharts hands a tooltip's valueFormatter `undefined` for a null point (a gap in a line), so a formatter that
// does `fmtUSD0(v ?? 0)` prints "$0" for "no data" - a number the chart never drew. A gap reads "—".
describe("moneyTooltip", () => {
  const fmt = (v: unknown) => moneyTooltip().valueFormatter(v as number);
  it("prints whole dollars, and a missing point (undefined or null) as a dash - never $0 or $NaN", () => {
    expect(fmt(1234.5)).toBe("$1,235");
    expect(fmt(0)).toBe("$0");                       // a real zero is still a zero
    expect(fmt(undefined)).toBe("—");
    expect(fmt(null)).toBe("—");
  });
  it("takes the trigger it is given and defaults to axis", () => {
    expect(moneyTooltip().trigger).toBe("axis");
    expect(moneyTooltip("item").trigger).toBe("item");
  });
  it("masks an amount while privacy mode is on, but a gap stays a dash (it is not an amount)", () => {
    setPrivate(true);
    expect(fmt(1234.5)).toBe(MASK);
    expect(fmt(0)).toBe(MASK);
    expect(fmt(undefined)).toBe("—");
  });
});

describe("moneyValue", () => {
  it("formats through the given formatter (whole dollars by default) and dashes a gap", () => {
    expect(moneyValue(2500)).toBe("$2,500");
    expect(moneyValue(2500, (v) => `<${v}>`)).toBe("<2500>");
    expect(moneyValue(undefined, () => "never")).toBe("—");
    expect(moneyValue(null, () => "never")).toBe("—");
  });
});

// Two accounts that share a long prefix ("Example Credit Card ...0303", "Example Credit Card ...0404 (Sample)") were
// cut at the END to the same "Example Credit Ca…" in a legend: the cut goes in the MIDDLE and keeps the tail that
// tells them apart (what follows the last "...", the last-four convention; else the last word).
describe("middleEllipsis", () => {
  const cut = (t: string, max: number) => middleEllipsis(t, max, 12, perChar);
  it("leaves a name that fits alone", () => {
    expect(cut("Joint Checking ...0101", 30)).toBe("Joint Checking ...0101");
  });
  it("keeps the distinguishing tail, so two names with one prefix stay distinct, and never exceeds the room", () => {
    const a = cut("Example Credit Card ...0303", 16);
    const b = cut("Example Credit Card ...0404 (Sample)", 16);
    expect(a).toBe("Example Cre…0303");
    expect(b).toBe("Example…(Sample)");                              // the tail never takes more than 60% of the room
    for (const x of [a, b]) expect(perChar(x)).toBeLessThanOrEqual(16);
    expect(cut("Example Brokerage Joint Investing (4F9Q7BK2)", 22)).toBe("Example Bro…(4F9Q7BK2)");   // no "...": the last word
  });
  it("a single long word keeps its last four characters; no room at all is a bare ellipsis", () => {
    expect(cut("Supercalifragilisticexpialidocious", 12)).toBe("Superca…ious");
    expect(cut("Anything long", 0)).toBe("…");
  });
});

describe("estimateTextWidth (no canvas: jsdom, an old Safari)", () => {
  it("errs wide of a 12px sans-serif and orders glyphs sensibly", () => {
    expect(estimateTextWidth("Groceries", 12)).toBeGreaterThanOrEqual(56);     // ~55px in DejaVu Sans, ~50 in Helvetica
    expect(estimateTextWidth("WWW", 12)).toBeGreaterThan(estimateTextWidth("iii", 12));
    expect(estimateTextWidth("abc", 24)).toBe(2 * estimateTextWidth("abc", 12));
  });
});

// A treemap tile gets a label only when all of it fits (ECharts truncates the rest: a phone showed "T…" and
// "$4…"). The tile's real box comes from a layout stage registered right after ECharts' own treemap layout.
describe("treemap tile labels", () => {
  const box = (width: number, height: number, drillIcon = false) => ({ width, height, drillIcon });
  const fit = (b: ReturnType<typeof box> | undefined, c: string[][]) => fitTileLabel(b, c, 12, (t) => perChar(t) * 7);
  const both = [["Groceries", "$4.2K"], ["Groceries"]];
  it("two lines when both fit, the name alone when only it fits, nothing when even the name does not", () => {
    expect(fit(box(200, 100), both)).toBe("Groceries\n$4.2K");
    expect(fit(box(80, 30), both)).toBe("Groceries");                   // 9 x 7 = 63 + 10 padding fits; two lines do not
    expect(fit(box(60, 100), both)).toBe("");                           // 63 + 10 > 60: never "Groc…"
    expect(fit(box(200, 12), both)).toBe("");                           // no line fits the height
  });
  it("a category that has children carries the drill-down icon IN its label (the series sets drillDownIcon: \"\"), so an unlabelled tile shows no lone ▶", () => {
    expect(fit(box(78, 30), both)).toBe("Groceries");
    expect(fit(box(78, 30, true), both)).toBe("");                      // "▶ Groceries" is wider: nothing at all
    expect(fit(box(200, 100, true), both)).toBe("▶ Groceries\n$4.2K");
  });
  it("an unknown box (no layout recorded) falls back to the first candidate", () => {
    expect(fit(undefined, both)).toBe("Groceries\n$4.2K");
  });
  it("the layout stage records each tile's content box by series id and data index", () => {
    const layouts = [{ width: 120, height: 50, borderWidth: 1, isLeafRoot: true }, null, { width: 30, height: 20 }];
    const series = { id: "test-treemap", getData: () => ({
      each: (cb: (i: number) => void) => layouts.forEach((_, i) => cb(i)),
      getItemLayout: (i: number) => layouts[i],
    }) };
    TREEMAP_TILE_STAGE.reset(series);
    expect(treemapTileBox("test-treemap", 0)).toEqual({ width: 118, height: 48, drillIcon: true });
    expect(treemapTileBox("test-treemap", 1)).toBeUndefined();
    expect(treemapTileBox("test-treemap", 2)).toEqual({ width: 30, height: 20, drillIcon: false });
    expect(treemapTileBox("other", 0)).toBeUndefined();
    expect(treemapTileBox("test-treemap", undefined)).toBeUndefined();
  });
});

describe("legends cut in the middle", () => {
  it("the desktop legend shows a name whole up to 240px and keeps the tail of a longer one", () => {
    const f = (wrapLegend() as { formatter: (n: string) => string }).formatter;
    expect(f("Cash")).toBe("Cash");
    expect(f("Net worth (daily · SimpleFIN)")).toBe("Net worth (daily · SimpleFIN)");
    const a = f("Example Credit Card ...0303 and a very long nickname indeed, longer than any row of a legend should be");
    expect(a.includes("…") && a.endsWith("be")).toBe(true);
  });
  it("the phone legend swaps in a 200px/10px cut for a tail-keeping or formatter-less legend, never an author's own formatter", () => {
    const tuned = (l: object): any => tuneForMobile({ legend: l, xAxis: { type: "category", data: [] }, yAxis: {}, series: [] });
    const name = "Example Card Platinum Credit Card, a nickname longer than a phone's row ...0404";
    expect(tuned(wrapLegend()).legend.formatter(name).endsWith("0404")).toBe(true);
    expect(tuned({}).legend.formatter(name).endsWith("0404")).toBe(true);
    const own = (n: string) => `${n} 12%`;
    expect(tuned({ formatter: own }).legend.formatter).toBe(own);
    expect(tuned({ formatter: "{name}" }).legend.formatter).toBe("{name}");
  });
  it("on a phone the names the demo's charts carry are drawn whole: no cut inside a word", () => {
    const f = (tuneForMobile({ legend: {}, xAxis: { type: "category", data: [] }, yAxis: {}, series: [] }) as any).legend.formatter;
    for (const name of ["Fixed (essentials by default)", "Renewals (annual / quarterly)", "Net worth (daily · SimpleFIN)",
      "Example Credit Card ...0303", "Stocks/ETF", "FIRE number (4% WR)", "Discretionary"]) {
      expect(f(name), name).toBe(name);
    }
    // and nothing in the legend's text style truncates either (ECharts' own `overflow: truncate` cut at a set width)
    const style = (tuneForMobile({ legend: { textStyle: { color: "#fff" } }, xAxis: {}, yAxis: {}, series: [] }) as any).legend.textStyle;
    expect(style).toEqual({ color: "#fff", fontSize: 10 });
  });
});

// A legend that wraps: Explore's thirteen categories, Home's long series names. Every entry is on screen, in rows; the
// plot moves down by the rows past the first, laid out as ECharts lays out a plain legend within its set width.
describe("a legend wraps instead of paging", () => {
  const option = (names: string[], legend: object = wrapLegend()) =>
    ({ grid: { ...baseGrid }, legend, xAxis: { type: "category", data: [] }, yAxis: {}, series: names.map((name) => ({ name, type: "bar" })) });
  // 10 units a character: "Housing" is 70 wide, an entry 25 + 5 + 70 = 100, entries 10 apart
  const tenPerChar = (s: string) => 10 * [...s].length;
  const seven = ["Housing", "Grocery", "Dinings", "Transit", "Utility", "Leisure", "Healthy"];
  it("counts the rows its entries take at the chart's width: the legend's box is 92% of it", () => {
    // 92% of 400 = 368, less 5px of padding each side = 358: three entries (100 + 10 + 100 + 10 + 100 = 320) fit a row, a
    // fourth (430) does not
    expect(legendRows(option(seven), 400, tenPerChar)).toBe(3);
    expect(legendRows(option(seven), 1000, tenPerChar)).toBe(1);
    expect(legendRows(option(seven.slice(0, 1)), 120, tenPerChar)).toBe(1);   // one entry is one row, however narrow
  });
  it("an entry that ends inside the legend's padding wraps: the room is the box less 5px each side", () => {
    // two entries end at 210: a box of 92% of 235 = 216.2 is wider than that, but less its padding (206.2) it is not; a box
    // of 92% of 240 = 220.8 less its padding (210.8) holds them
    const two = option(seven.slice(0, 2));
    expect(legendRows(two, 235, tenPerChar)).toBe(2);
    expect(legendRows(two, 240, tenPerChar)).toBe(1);
  });
  it("reads the legend's own data when it has any, a pie's slice names, and each name once", () => {
    expect(legendRows({ ...option(seven), legend: { ...wrapLegend(), data: ["Housing"] } }, 400, tenPerChar)).toBe(1);
    const pie = { legend: wrapLegend(), series: [{ type: "pie", data: seven.map((name) => ({ name, value: 1 })) }] };
    expect(legendRows(pie, 400, tenPerChar)).toBe(3);
    expect(legendRows(option([...seven, ...seven]), 400, tenPerChar)).toBe(3);
  });
  it("is null for a legend that does not wrap (an author's own on a desktop) and before the chart has a width", () => {
    expect(legendRows(option(seven, baseLegend()), 400, tenPerChar)).toBeNull();
    expect(legendRows(option(seven), 0, tenPerChar)).toBeNull();
  });
  it("moves the plot down by each row past the first, from the grid's own top; nothing to move is the same option", () => {
    const o = option(seven);
    expect(withLegendRows(o, 2).grid.top).toBe(baseGrid.top + 2 * LEGEND_ROW_STEP);
    expect(withLegendRows({ ...o, grid: { ...baseGrid, top: 48 } }, 1).grid.top).toBe(48 + LEGEND_ROW_STEP);
    expect(withLegendRows(o, 0)).toBe(o);
    expect(legendRowStep(o)).toBe(LEGEND_ROW_STEP);
  });
});

// A treemap label that does not fit the tile it is drawn on is hidden, never cut: the rects are ECharts' own, at draw time
describe("treemapLabelFit", () => {
  it("keeps a label 8px a side inside its tile (the padding and the tile's border and gap) and hides one wider or taller", () => {
    expect(treemapLabelFit({ dataIndex: 1, rect: { width: 166, height: 210 }, labelRect: { width: 60, height: 30 } })).toEqual({});
    // "▶ Transport" (69px) on the 68px tile the phone drew: hidden, where ECharts cut it to "▶ Trans..."
    expect(treemapLabelFit({ dataIndex: 1, rect: { width: 68, height: 146 }, labelRect: { width: 69, height: 12 } })).toEqual({ fontSize: 0 });
    // the margin is 8px a side: 85px holds 69, 84 does not
    expect(treemapLabelFit({ dataIndex: 1, rect: { width: 85, height: 146 }, labelRect: { width: 69, height: 12 } })).toEqual({});
    expect(treemapLabelFit({ dataIndex: 1, rect: { width: 84, height: 146 }, labelRect: { width: 69, height: 12 } })).toEqual({ fontSize: 0 });
    expect(treemapLabelFit({ dataIndex: 1, rect: { width: 120, height: 39 }, labelRect: { width: 60, height: 24 } }),
      "two lines in a short tile").toEqual({ fontSize: 0 });
    expect(treemapLabelFit({}), "anything without rects").toEqual({});
  });
  it("leaves the breadcrumb's crumbs alone: they belong to no tile, and each is sized to its own text", () => {
    // "All categories" (≈80px) in its crumb (its text and 8px a side): the tile rule would hide it, leaving an empty bar
    expect(treemapLabelFit({ rect: { width: 96, height: 22 }, labelRect: { width: 80, height: 14 } })).toEqual({});
    expect(treemapLabelFit({ dataIndex: undefined, rect: { width: 96, height: 22 }, labelRect: { width: 80, height: 14 } })).toEqual({});
  });
  it("a still option turns motion off on every series too: a treemap's own default animates otherwise", () => {
    const o = stillOption({ series: [{ type: "treemap" }, { type: "bar", animation: true }] }) as
      { animation?: boolean; series: { animation?: boolean }[] };
    expect(o.animation).toBe(false);
    expect(o.series.map((s: { animation?: boolean }) => s.animation)).toEqual([false, false]);
    expect(stillOption({ title: {} })).toEqual({ title: {}, animation: false });
  });
});

// On a phone every legend wraps too (the pager it used to be cut an entry at its page edge and hid the rest behind arrows):
// the compact metrics, rows counted at the chart's width, the plot or the pie kept its size.
describe("the phone legend wraps instead of paging", () => {
  const pie = (legend: object, s: object = {}): any =>
    tuneForMobile({ legend, series: [{ type: "pie", radius: ["45%", "72%"], data: [], ...s }] });
  const cartesian = (legend: object): any => tuneForMobile({ legend, xAxis: { type: "category", data: [] }, yAxis: {}, series: [] });
  it("an author's plain legend, a wrapping one and a pie's are all a plain wrapping legend with the compact metrics", () => {
    for (const l of [pie({ orient: "vertical", right: 0 }).legend, cartesian({}).legend, cartesian(wrapLegend()).legend,
      cartesian({ type: "scroll" }).legend]) {
      expect([l.type, l.wrap, l.left, l.width, l.right]).toEqual(["plain", true, "center", "92%", undefined]);
      expect([l.itemWidth, l.itemHeight, l.itemGap, l.rowHeight, l.textStyle.fontSize]).toEqual([14, 8, 6, 12, 10]);
    }
    expect(pie({ orient: "vertical" }).legend).toMatchObject({ orient: "horizontal", bottom: 0 });
  });
  it("counts its rows with its own metrics: 14px icons, 6px apart, 4px padding, 18px a row", () => {
    // 10 units a character: "Housing" is 70 wide, an entry 14 + 5 + 70 = 89, entries 6 apart; 92% of 360 = 331.2, less 4px
    // each side = 323.2: three entries (89 + 6 + 89 + 6 + 89 = 279) fit a row, a fourth (374) does not
    const tenPerChar = (s: string) => 10 * [...s].length;
    const seven = ["Housing", "Grocery", "Dinings", "Transit", "Utility", "Leisure", "Healthy"];
    const o = cartesian({});
    o.series = seven.map((name) => ({ name, type: "bar" }));
    expect(legendRows(o, 360, tenPerChar)).toBe(3);
    expect(legendRowStep(o)).toBe(18);
  });
  it("a pie under a legend of several rows keeps the centre and radius it had before the rows were added, in px", () => {
    const o = pie({});
    const kept = withLegendRows(o, 2, 18, { width: 330, height: 240 }).series[0];
    expect(kept.center).toEqual(["50%", 0.46 * 240]);
    expect(kept.radius).toEqual([0.45 * 120, 0.72 * 120]);           // of half the smaller side of the 330 x 240 chart
    expect(withLegendRows(o, 0, 18, { width: 330, height: 240 })).toBe(o);
    // a pie whose legend is not under it (a desktop's) is left alone
    const side = { legend: { right: 0 }, series: [{ type: "pie", radius: "70%" }] };
    expect(withLegendRows(side, 2, 18, { width: 330, height: 240 })).toBe(side);
  });
});

// A date axis says which month its day numbers count, and never prints a clock time: the data are days. ECharts calls the
// formatter tick by tick, in order, after it has asked the axis for its bounds.
describe("timeAxis", () => {
  const at = (y: number, m: number, d: number, h = 0) => new Date(y, m - 1, d, h).getTime();
  /** the labels of `ticks` as ECharts draws them, after it has asked for the bounds of data from `lo` to `hi` */
  const labels = (ticks: number[], lo = ticks[0], hi = ticks[ticks.length - 1]) => {
    const axis = timeAxis();
    axis.min({ min: lo, max: hi });
    axis.max({ min: lo, max: hi });
    return ticks.map((t, i) => axis.axisLabel.formatter(t, i));
  };
  it("an axis of day numbers names the month on its first tick and again where the month changes", () => {
    expect(labels([27, 28, 29, 30].map((d) => at(2026, 10, d)))).toEqual(["Oct 27", "28", "29", "30"]);
    expect(labels([30, 31].map((d) => at(2026, 10, d)).concat([1, 2].map((d) => at(2026, 11, d)))))
      .toEqual(["Oct 30", "31", "Nov", "2"]);
  });
  it("the tick ECharts adds at the axis' start, on the first inner tick's day, reads the same: the month is named either way", () => {
    expect(labels([27, 27, 28, 29, 30].map((d) => at(2026, 10, d)))).toEqual(["Oct 27", "Oct 27", "28", "29", "30"]);
  });
  it("a long axis keeps the year at a year's start and the month at a month's start", () => {
    expect(labels([at(2025, 1, 1), at(2025, 4, 1), at(2025, 7, 1), at(2026, 1, 1)])).toEqual(["2025", "Apr", "Jul", "2026"]);
  });
  it("never prints a clock time: a tick between two midnights is unlabelled, and the next day names its month", () => {
    expect(labels([at(2026, 10, 29, 12), at(2026, 10, 29, 18), at(2026, 10, 30), at(2026, 10, 30, 6)]))
      .toEqual(["", "", "Oct 30", ""]);
  });
  it("the months named restart with the first tick of each drawing (a zoom draws the labels again)", () => {
    const axis = timeAxis();
    axis.min({ min: at(2026, 10, 27), max: at(2026, 10, 30) });
    const draw = () => [27, 28].map((d, i) => axis.axisLabel.formatter(at(2026, 10, d), i));
    expect(draw()).toEqual(["Oct 27", "28"]);
    expect(draw()).toEqual(["Oct 27", "28"]);
  });
  it("with every point on one date the axis spans that day alone and its one label is the date in full", () => {
    const t = at(2026, 10, 30);
    const axis = timeAxis();
    expect(axis.min({ min: t, max: t })).toBe(t - 12 * 3_600_000);
    expect(axis.max({ min: t, max: t })).toBe(t + 12 * 3_600_000);
    expect([18, 0, 6].map((h, i) => axis.axisLabel.formatter(h === 18 ? at(2026, 10, 29, 18) : at(2026, 10, 30, h), i)))
      .toEqual(["", "Oct 30, 2026", ""]);
  });
  it("with two dates or more the axis keeps ECharts' own bounds", () => {
    const axis = timeAxis();
    expect(axis.min({ min: at(2026, 10, 27), max: at(2026, 10, 30) })).toBeUndefined();
    expect(axis.max({ min: at(2026, 10, 27), max: at(2026, 10, 30) })).toBeUndefined();
  });
});

// An axis scaled to its data spans a narrow range: its step must be one the compact labels can tell apart ("$1.4K" three
// times was a $50 step under labels that print $100s).
describe("compactStep", () => {
  it("ticks a step apart, at the size of the axis' largest value, never print the same", () => {
    for (const max of [600, 1_550, 9_400, 14_000, 223_000, 980_000, 1_030_000, 9_900_000, 14_000_000]) {
      const step = compactStep(max);
      const ticks = Array.from({ length: 6 }, (_, k) => Math.floor(max / step) * step - k * step).filter((v) => v > 0);
      const labels = ticks.map((v) => fmtCompact(v));
      expect(new Set(labels).size, `${max}: ${labels.join(" ")}`).toBe(labels.length);
    }
  });
  it("is no coarser than the labels need: a dollar under $1K, $100 to $10K, $1K to $1M", () => {
    expect([compactStep(999), compactStep(1_000), compactStep(10_000), compactStep(-250_000), compactStep(2_000_000)])
      .toEqual([1, 100, 1_000, 1_000, 10_000]);
  });
});

// One rule for which line charts leave zero: a level that never nears it.
describe("leavesZero and levelScale", () => {
  it("one point, or all equal, above zero: the line leaves zero", () => {
    expect(leavesZero([42])).toBe(true);
    expect(leavesZero([7, 7, 7])).toBe(true);
  });
  it("the smallest at half the largest leaves zero; just under half keeps it", () => {
    expect(leavesZero([50, 100])).toBe(true);
    expect(leavesZero([49.99, 100])).toBe(false);
  });
  it("a line that crosses zero, a negative one, one that touches zero, or nothing drawn keeps zero", () => {
    expect(leavesZero([-10, 100])).toBe(false);
    expect(leavesZero([-100, -60])).toBe(false);
    expect(leavesZero([0, 1])).toBe(false);
    expect(leavesZero([])).toBe(false);
  });
  it("levelScale scales, at the given step, only when the line leaves zero", () => {
    expect(levelScale([60, 100], 10)).toEqual({ scale: true, minInterval: 10 });
    expect(levelScale([20, 100], 10)).toEqual({});
  });
});

describe("ladderStep and the money axis beside the rate axis", () => {
  it("the ladder steps 1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10: a need of 5.5K is a 6K step, never a 10K one", () => {
    expect([5_500, 7_000, 3_150, 1, 0.2, 10_001].map(ladderStep)).toEqual([6_000, 8_000, 4_000, 1, 1, 15_000]);
  });
  it("shares the grid when income is at least as tall as spending is deep, else takes its own even steps", () => {
    const shared = moneyAxisBesideRate([13_000, -6_300, 1_250], fmtCompact);
    expect(shared).toMatchObject({ min: -8_000, max: 16_000, interval: 4_000 });
    const own = moneyAxisBesideRate([12_000, -27_700, 1_250], fmtCompact);
    expect(own).toMatchObject({ min: -32_000, max: 16_000, interval: 8_000 });
  });
});

describe("pinRates: a rate below the foot drawn at it", () => {
  it("pins a rate below −50%, keeps the rest and the gaps; all only when every month with a rate is pinned", () => {
    const some = pinRates([0.2, -1.4, null]);
    expect(some.data[0]).toBe(0.2);
    expect(some.data[1]).toMatchObject({ value: -0.5, symbol: "triangle" });
    expect(some.data[2]).toBeNull();
    expect([some.pinned, some.all]).toEqual([true, false]);
    expect(pinRates([-3, null, -0.6])).toMatchObject({ pinned: true, all: true });
    expect(pinRates([null, null])).toMatchObject({ pinned: false, all: false });
    expect(pinRates([-0.5])).toMatchObject({ pinned: false, all: false });
  });
});

describe("shareBounds", () => {
  it("holds a share's scaled axis to 0%–100% where its line reaches an end, and leaves a line inside the range its own", () => {
    const b = shareBounds();
    expect([b.min({ min: 0 }), b.max({ max: 1 })]).toEqual([0, 1]);          // a lone point at 100% is not padded to 160%
    expect([b.min({ min: -0.01 }), b.max({ max: 1.2 })]).toEqual([0, 1]);
    expect([b.min({ min: 0.26 }), b.max({ max: 0.31 })]).toEqual([undefined, undefined]);
  });
});

describe("monthAxis", () => {
  const label = (axis: ReturnType<typeof monthAxis>, m: string) => (axis.axisLabel.formatter as (v: string) => string)(m);
  it("labels months through fmtMonthAxis, starring only the partial ones - the category VALUES keep the month key", () => {
    const axis = monthAxis(["2026-09", "2026-10"], new Set(["2026-10"]));
    expect(axis.data).toEqual(["2026-09", "2026-10"]);      // a click handler reads this key
    expect(label(axis, "2026-09")).toBe("Sep \u201926");
    expect(label(axis, "2026-10")).toBe("Oct \u201926*");
  });
  it("without a partial set it is the plain fmtMonthAxis axis", () => {
    expect(label(monthAxis(["2026-10"]), "2026-10")).toBe("Oct \u201926");
    expect(label(monthAxis(["2026-10"], new Set()), "2026-10")).toBe("Oct \u201926");
  });
});

describe("encodeHTML", () => {
  it("escapes markup in data text bound for a tooltip's innerHTML", () => {
    expect(encodeHTML("<img src=x onerror=alert(1)>")).toBe("&lt;img src=x onerror=alert(1)&gt;");
    expect(encodeHTML("Ben & Jerry's")).toBe("Ben &amp; Jerry&#39;s");
  });
});

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { tuneForMobile, wrapLegend } from "./echarts";

describe("tuneForMobile", () => {
  it("passes non-cartesian options through untouched (same reference)", () => {
    const sankey = { tooltip: {}, series: [{ type: "sankey", data: [], links: [] }] };
    expect(tuneForMobile(sankey)).toBe(sankey);
    const gauge = { series: [{ type: "gauge" }] };
    expect(tuneForMobile(gauge)).toBe(gauge);
    expect(tuneForMobile(null)).toBe(null);
  });

  it("tunes legend-bearing pies: bottom wrapping legend, labels off, recentered", () => {
    // Home's allocation donut's outside labels collide with its vertical
    // right legend at phone width — the legend carries the names, so on mobile
    // the labels go and the legend drops to horizontal rows under the pie.
    const tuned: any = tuneForMobile({
      tooltip: {},
      legend: { textStyle: { color: "#fff" }, orient: "vertical", right: 0, top: "middle" },
      series: [{ type: "pie", radius: ["45%", "72%"], center: ["38%", "50%"],
                 label: { color: "#fff", formatter: "{b}\n{d}%" }, data: [] }],
    });
    expect(tuned.legend).toMatchObject({ type: "plain", wrap: true, orient: "horizontal", bottom: 0, left: "center" });
    expect(tuned.legend.right).toBeUndefined();
    expect(tuned.legend.top).toBeUndefined();
    expect(tuned.series[0].label.show).toBe(false);
    expect(tuned.series[0].labelLine.show).toBe(false);
    expect(tuned.series[0].center).toEqual(["50%", "46%"]);
    expect(tuned.tooltip.confine).toBe(true);
  });

  it("passes legendless pies through untouched (labels are the author's contract)", () => {
    const mute = { series: [{ type: "pie", label: { show: false }, data: [] }] };
    expect(tuneForMobile(mute)).toBe(mute);
  });

  it("does not mutate the input option", () => {
    const option = {
      legend: { textStyle: { color: "#fff" } },
      grid: { left: 8, right: 16 },
      tooltip: { trigger: "axis" },
      xAxis: { type: "time", axisLabel: { color: "#aaa" } },
      yAxis: { type: "value" },
      series: [],
    };
    const snapshot = JSON.parse(JSON.stringify(option));
    tuneForMobile(option);
    expect(option).toEqual(snapshot);
  });

  it("applies legend, grid, tooltip, and axis-label transforms", () => {
    const tuned: any = tuneForMobile({
      legend: { textStyle: { color: "#fff" } },
      grid: { left: 8, right: 16 },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", axisLabel: { color: "#aaa" } },
      yAxis: { type: "value" },
    });
    expect(tuned.legend.type).toBe("plain");
    expect(tuned.legend.wrap).toBe(true);
    expect(tuned.legend.textStyle).toEqual({ color: "#fff", fontSize: 10 });
    expect(tuned.grid).toEqual({ left: 8, right: 8 });
    expect(tuned.tooltip.confine).toBe(true);
    expect(tuned.xAxis.axisLabel).toEqual(
      { color: "#aaa", fontSize: 10, hideOverlap: true, showMaxLabel: true });
    expect(tuned.yAxis.axisLabel).toEqual({ fontSize: 10, hideOverlap: true });
  });

  it("handles array-form axes and options without legend/grid", () => {
    const tuned: any = tuneForMobile({
      xAxis: [{ type: "category" }, { type: "category" }],
      yAxis: [{ type: "value" }],
    });
    expect(tuned.xAxis).toHaveLength(2);
    for (const a of tuned.xAxis) expect(a.axisLabel.hideOverlap).toBe(true);
    expect(tuned.yAxis[0].axisLabel.fontSize).toBe(10);
    expect(tuned.legend).toBeUndefined();
    expect(tuned.grid).toBeUndefined();
  });

  it("thins markPoint pins only on a series that opts in with phoneMaxPins", () => {
    // Home's net-worth milestones: consecutive $100K crossings sit a few months apart on a
    // multi-year time axis, so three pin heads and their labels land on top of each other
    const pins = [{ coord: ["2026-01-01", 700_000], value: 700_000 },
      { coord: ["2026-03-01", 800_000], value: 800_000 }, { coord: ["2026-05-01", 900_000], value: 900_000 }];
    const nw = { name: "Net worth", type: "line", phoneMaxPins: 1, markPoint: { symbol: "pin", data: pins } };
    const other = { name: "Cash", type: "line", data: [] };
    const input = { xAxis: { type: "time" }, yAxis: { type: "value" }, series: [other, nw] as any[] };
    const tuned: any = tuneForMobile(input);
    expect(tuned.series[1].markPoint.data).toEqual([pins[2]]);
    expect(tuned.series[1].markPoint.symbol).toBe("pin");        // the rest of the markPoint survives
    expect(tuned.series[1].phoneMaxPins).toBe(1);
    expect(tuned.series[0]).toBe(other);                           // pin-less series keep their reference
    expect(input.series[1].markPoint.data).toHaveLength(3);        // the input is never mutated
    const two: any = tuneForMobile({ xAxis: {}, yAxis: {}, series: [{ ...nw, phoneMaxPins: 2 }] });
    expect(two.series[0].markPoint.data).toEqual([pins[1], pins[2]]);   // the newest N, oldest dropped
  });

  it("never thins a series that did not opt in (its pins all stay)", () => {
    const pins = [1, 2, 3, 4, 5].map((n) => ({ coord: [`2026-0${n}-01`, n * 100_000], value: n * 100_000 }));
    const plain = { type: "line", markPoint: { symbol: "pin", data: pins } };
    const tuned: any = tuneForMobile({ xAxis: {}, yAxis: {}, series: [plain] });
    expect(tuned.series[0]).toBe(plain);
    expect(tuned.series[0].markPoint.data).toHaveLength(5);
  });

  it("leaves a lone pin, an opted-in series within its limit, and markPoint-free series alone", () => {
    const one = { type: "line", phoneMaxPins: 1, markPoint: { data: [{ coord: ["2026-01-01", 1] }] } };
    const noPins = { type: "line", phoneMaxPins: 1, data: [] };
    const tuned: any = tuneForMobile({ xAxis: {}, yAxis: {}, series: [one, noPins] });
    expect(tuned.series[0]).toBe(one);
    expect(tuned.series[1]).toBe(noPins);
  });

  it("keeps the newest category label on a category x-axis (390px used to drop the last point's label)", () => {
    const t: any = tuneForMobile({ xAxis: { type: "category", data: ["a", "b"] }, yAxis: { type: "value" } });
    expect(t.xAxis.axisLabel.showMaxLabel).toBe(true);
    expect(t.yAxis.axisLabel.showMaxLabel).toBeUndefined();          // value / y axes untouched
    const time: any = tuneForMobile({ xAxis: { type: "time" }, yAxis: {} });
    expect(time.xAxis.axisLabel.showMaxLabel).toBeUndefined();
    const authored: any = tuneForMobile({ xAxis: { type: "category", axisLabel: { showMaxLabel: false } }, yAxis: {} });
    expect(authored.xAxis.axisLabel.showMaxLabel).toBe(false);       // the author's choice wins
  });

  it("respects an author's hideOverlap: false and a font already under 10px", () => {
    const t: any = tuneForMobile({
      xAxis: { type: "category", axisLabel: { interval: 0, hideOverlap: false, fontSize: 9 } }, yAxis: {} });
    expect(t.xAxis.axisLabel).toMatchObject({ interval: 0, hideOverlap: false, fontSize: 9 });
    const big: any = tuneForMobile({ xAxis: { axisLabel: { fontSize: 14 } }, yAxis: {} });
    expect(big.xAxis.axisLabel.fontSize).toBe(10);
  });

  it("keeps a right gutter the author reserved on purpose (>16px) instead of squeezing it to 8", () => {
    const wide: any = tuneForMobile({ grid: { left: 8, right: 48 }, xAxis: {}, yAxis: {} });
    expect(wide.grid.right).toBe(48);
    const std: any = tuneForMobile({ grid: { left: 8, right: 16 }, xAxis: {}, yAxis: {} });
    expect(std.grid.right).toBe(8);
  });

  it("keeps the author's right gutter under a value x-axis: its last tick label hangs past the plot", () => {
    const bars: any = tuneForMobile({ grid: { left: 8, right: 16 }, xAxis: { type: "value" }, yAxis: { type: "category" } });
    expect(bars.grid.right).toBe(16);
    const arr: any = tuneForMobile({ grid: { left: 8, right: 16 }, xAxis: [{ type: "category" }, { type: "value" }], yAxis: {} });
    expect(arr.grid.right).toBe(16);
  });

  it("the compact legend cuts no entry at a set width (ECharts' truncate) and names each in its tooltip", () => {
    const out: any = tuneForMobile({ legend: {}, xAxis: { type: "category", data: [] }, yAxis: {}, series: [] });
    expect(out.legend.textStyle.overflow).toBeUndefined();
    expect(out.legend.textStyle.width).toBeUndefined();
    expect(out.legend.tooltip).toEqual({ show: true });
  });

  it("the pie's compact legend keeps the author's text style", () => {
    const out: any = tuneForMobile({
      legend: { textStyle: { color: "#fff" } },
      series: [{ type: "pie", data: [] }],
    });
    expect(out.legend.textStyle).toEqual({ color: "#fff", fontSize: 10 });
    expect(out.legend.tooltip).toEqual({ show: true });
  });

  it("a legend wraps and never pages, on a desktop and on a phone", () => {
    const l: any = wrapLegend();
    expect(l.type).toBe("plain");
    expect(l.wrap).toBe(true);
    expect(l.tooltip).toEqual({ show: true });
    expect(Object.keys(l).filter((k) => /^page/.test(k))).toEqual([]);
    for (const legend of [l, {}, { type: "scroll" }]) {
      const phone: any = tuneForMobile({ legend, xAxis: { type: "category", data: [] }, yAxis: {}, series: [] });
      expect([phone.legend.type, phone.legend.wrap]).toEqual(["plain", true]);
      expect(Object.keys(phone.legend).filter((k) => /^page/.test(k))).toEqual([]);
    }
  });

  it("under a chart of lines alone a category axis' last label ends at its point, so it cannot hang past the chart", () => {
    const lines: any = tuneForMobile({ xAxis: { type: "category", data: [] }, yAxis: {}, series: [{ type: "line" }, { type: "line" }] });
    expect(lines.xAxis.axisLabel.alignMaxLabel).toBe("right");
    // bars are centred under their own band, and an author's choice is kept
    const bars: any = tuneForMobile({ xAxis: { type: "category", data: [] }, yAxis: {}, series: [{ type: "bar" }, { type: "line" }] });
    expect(bars.xAxis.axisLabel.alignMaxLabel).toBeUndefined();
    const own: any = tuneForMobile({ xAxis: { type: "category", axisLabel: { alignMaxLabel: "center" } }, yAxis: {}, series: [{ type: "line" }] });
    expect(own.xAxis.axisLabel.alignMaxLabel).toBe("center");
    const time: any = tuneForMobile({ xAxis: { type: "time" }, yAxis: {}, series: [{ type: "line" }] });
    expect(time.xAxis.axisLabel.alignMaxLabel).toBeUndefined();
  });
});

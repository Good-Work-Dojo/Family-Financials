// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { BalancePoint } from "../data/types";
import {
  balanceLines, balanceTimeOption, balanceTimeOptionOf, chartSpacing, monthlyThrough, onePointEach,
} from "./balanceCharts";
import { compactStep, timeAxis } from "./echarts";
import { fmtCompact, fmtUSD0 } from "./format";
import { groupColorOf, reportOption, runReport } from "./reportBuilder";
import type { ReportSpec } from "./reportSpec";
import { accountColor, CATEGORY_PALETTE, UNKNOWN_COLOR } from "./tokens";
import { txFactory } from "./__fixtures__";

const pt = (date: string, balance: number): BalancePoint => ({ date, balance, available: null, limit: null, utilization: null, source: "simplefin" });
const series = { a: [pt("2026-01-01", 10), pt("2026-01-02", 12)], b: [pt("2026-01-01", -3)] };
const accounts = { a: { name: "Acct A", institution: "I", owner: "J", kind: "checking", asset_class: "Cash" } };
/** the household's own account order (accounts.json) */
const ORDER = ["z", "b", "a"];

describe("onePointEach", () => {
  it("is true only when every account drawn has exactly one balance, and never for no account", () => {
    expect(onePointEach(series, ["b"])).toBe(true);
    expect(onePointEach({ ...series, c: [pt("2026-01-05", 1)] }, ["b", "c"])).toBe(true);
    expect(onePointEach(series, ["a", "b"]), "one account has a line").toBe(false);
    expect(onePointEach(series, []), "no account: nothing drawn at all").toBe(false);
  });
});

describe("balance chart helpers", () => {
  it("one named line per id, mapped through `value`; a lone point shows its symbol", () => {
    const lines = balanceLines(series, accounts, ["a", "b"], (p) => -p.balance, ORDER);
    expect(lines.map((l) => l.name)).toEqual(["Acct A", "b"]);
    expect(lines[0].data).toEqual([["2026-01-01", -10], ["2026-01-02", -12]]);
    expect(lines[0].showSymbol).toBe(false);
    expect(lines[1].showSymbol).toBe(true);
  });
  it("an option built from lines already in hand carries those very lines (one source for a chart and its table)", () => {
    const lines = balanceLines(series, accounts, ["a", "b"], (p) => p.balance, ORDER);
    const opt = balanceTimeOptionOf(lines, { xMin: "2025-12-01" });
    expect(opt.series).toBe(lines);
    expect(opt.xAxis.min).toBe("2025-12-01");
    expect(balanceTimeOption(series, accounts, ["a", "b"], (p) => p.balance, ORDER).series).toEqual(lines);
  });
  it("with no minimum the date axis keeps its own bounds", () => {
    const own = (axis: object) => String((axis as { min?: unknown }).min);
    expect(own(balanceTimeOption(series, accounts, ["a"], (p) => p.balance, ORDER).xAxis)).toBe(own(timeAxis()));
  });
  it("money prints compact on the axis, as on every other money axis, and in whole dollars in the tooltip", () => {
    const o = balanceTimeOption(series, accounts, ["a"], (p) => p.balance, ORDER);
    expect(o.yAxis.axisLabel.formatter(18_000)).toBe(fmtCompact(18_000));
    expect(o.tooltip.valueFormatter(18_000)).toBe(fmtUSD0(18_000));
    const pct = balanceTimeOption(series, accounts, ["a"], (p) => p.balance, ORDER,
      { fmt: { tooltip: (v) => `${v} t`, axis: (v) => `${v} a`, step: () => 1 } });
    expect([pct.yAxis.axisLabel.formatter(1), pct.tooltip.valueFormatter(1)]).toEqual(["1 a", "1 t"]);
  });
});

// A balance is a level: its axis leaves zero only by the one rule (lib/echarts.ts leavesZero: every value above zero, the
// smallest at least half the largest); an amount owed and a utilization keep zero, where zero is the goal.
describe("a balance chart's value axis and zero", () => {
  const axisOf = (points: BalancePoint[], keepZero = false) =>
    balanceTimeOption({ a: points }, accounts, ["a"], (p) => p.balance, ORDER, { keepZero }).yAxis as
      { scale?: boolean; minInterval?: number };
  it("a level that never nears zero leaves it, at a step its labels tell apart", () => {
    const y = axisOf([pt("2026-01-01", 1_310), pt("2026-01-02", 1_520)]);
    expect([y.scale, y.minInterval]).toEqual([true, compactStep(1_520)]);
  });
  it("a balance that dips under half its high, or below zero, keeps zero", () => {
    expect(axisOf([pt("2026-01-01", 700), pt("2026-01-02", 1_520)]).scale).toBeUndefined();
    expect(axisOf([pt("2026-01-01", -30), pt("2026-01-02", 1_520)]).scale).toBeUndefined();
  });
  it("a chart that keeps zero never leaves it, whatever its lines hold", () => {
    expect(axisOf([pt("2026-01-01", 1_310), pt("2026-01-02", 1_520)], true).scale).toBeUndefined();
  });
  it("points before the date axis' minimum are not read: they are off the chart", () => {
    const lines = balanceLines({ a: [pt("2025-01-01", 10), pt("2026-01-01", 1_310), pt("2026-01-02", 1_520)] },
      accounts, ["a"], (p) => p.balance, ORDER);
    expect((balanceTimeOptionOf(lines, { xMin: "2025-06-01" }).yAxis as { scale?: boolean }).scale).toBe(true);
    expect((balanceTimeOptionOf(lines).yAxis as { scale?: boolean }).scale).toBeUndefined();
  });
});

// One account, one colour: its place in the household's own account list, on every chart that draws accounts, whichever is
// built first (a first-asked colour gave an account one colour on one page and another on the next).
describe("an account's colour", () => {
  it("is its place in the household's account list, whatever the order the chart lists its accounts in", () => {
    const one = balanceLines(series, accounts, ["a", "b"], (p) => p.balance, ORDER);
    const other = balanceLines(series, accounts, ["b", "a"], (p) => p.balance, ORDER);
    expect(one.map((l) => l.color)).toEqual([CATEGORY_PALETTE[2], CATEGORY_PALETTE[1]]);
    expect(other.find((l) => l.name === "Acct A")?.color).toBe(one[0].color);
  });
  it("is the same on a balance chart and on a report grouped by account, which knows the account by its name", () => {
    const household = [{ id: "z", name: "Joint Savings" }, { id: "b", name: "Card" }, { id: "a", name: "Acct A" }];
    const rows = [txFactory()({ date: "2026-05-03", amount: -40, account: "a", account_name: "Acct A", type: "purchase" }),
      txFactory()({ date: "2026-05-04", amount: -20, account: "b", account_name: "Card", type: "purchase" })];
    const spec: ReportSpec = { filters: "", measure: "spend", groupBy: "account", interval: "month", chart: "stacked" };
    const res = runReport(rows, spec);
    const report = reportOption(res, spec, groupColorOf(spec, household, res.groups)) as
      { series: { name: string; color: string }[] };
    const balance = balanceLines(series, accounts, ["a"], (p) => p.balance, household.map((h) => h.id));
    expect(report.series.find((s) => s.name === "Acct A")?.color).toBe(balance[0].color);
    expect(report.series.find((s) => s.name === "Card")?.color).toBe(accountColor("b", ORDER));
  });
  it("an account the list lacks takes the palette's colours after the listed ones, by name, never the grey of Other", () => {
    const household = [{ id: "z", name: "Joint Savings" }];
    const color = groupColorOf({ groupBy: "account" }, household, ["Zeta Card", "Old Checking", "Joint Savings"]);
    expect(color("Joint Savings")).toBe(CATEGORY_PALETTE[0]);
    expect(color("Old Checking")).toBe(CATEGORY_PALETTE[1]);
    expect(color("Zeta Card")).toBe(CATEGORY_PALETTE[2]);
    expect([color("Old Checking"), color("Zeta Card")]).not.toContain(UNKNOWN_COLOR);
  });
  it("a report grouped by anything else keeps the first-asked palette", () => {
    expect(groupColorOf({ groupBy: "category" }, [], ["Groceries"])("Groceries")).toMatch(/^#/);
  });
});

// A history can START with month-end balances (statement files, the feed's early reports), then frequent ones once the
// feed reports often. Every point is drawn - a card's balance really was low ten days after its payment - and the chart
// says where the spacing changes, so the reader knows why the line moves more after it. Only that shape is told: a
// frequent history with outages, or one whose start is not monthly, gets no line.
describe("monthlyThrough: where a history that starts monthly turns frequent", () => {
  const monthly = ["2026-06-30", "2026-07-31", "2026-08-31", "2026-09-30"].map((d, i) => pt(d, 1000 + i));
  const frequent = ["2026-10-10", "2026-10-13", "2026-10-16", "2026-10-28", "2026-10-31"].map((d, i) => pt(d, 300 + i));
  it("month ends then frequent points: the last point of the leading monthly run", () => {
    expect(monthlyThrough([...monthly, ...frequent])).toBe("2026-09-30");
  });
  it("a date given twice in the leading run is one point and does not end the run", () => {
    expect(monthlyThrough([monthly[0], ...monthly, ...frequent])).toBe("2026-09-30");
  });
  it("frequent points that start after a long gap: the leading run goes on through the first of them", () => {
    expect(monthlyThrough([...monthly, pt("2026-12-01", 1), pt("2026-12-04", 2), pt("2026-12-07", 3)])).toBe("2026-12-01");
  });
  it("a history whose first gaps are not both a month or longer gets no line, whatever follows", () => {
    expect(monthlyThrough([pt("2025-12-31", 1), pt("2026-01-11", 2), pt("2026-01-31", 3), ...monthly, ...frequent]),
      "a mid-month point second").toBeNull();
    expect(monthlyThrough([monthly[2], monthly[3], ...frequent]), "one monthly gap only").toBeNull();
  });
  it("a frequent history with outages gets no line, however many and however long", () => {
    const later = ["2026-12-05", "2026-12-08", "2026-12-11"].map((d) => pt(d, 1));
    const latest = ["2027-02-01", "2027-02-04"].map((d) => pt(d, 1));
    expect(monthlyThrough([...frequent, ...later])).toBeNull();
    expect(monthlyThrough([...frequent, ...later, ...latest])).toBeNull();
    expect(monthlyThrough([...frequent, ...monthly.map((p) => ({ ...p, date: p.date.replace("2026", "2027") })),
      ...latest.map((p) => ({ ...p, date: p.date.replace("2027-02", "2027-10") }))]), "a long outage of month ends").toBeNull();
  });
  it("none for a history that ends monthly, is monthly throughout, or has one or two points", () => {
    expect(monthlyThrough([...monthly, ...frequent, pt("2026-12-31", 1)]), "ends on a gap of a month").toBeNull();
    expect(monthlyThrough(monthly)).toBeNull();
    expect(monthlyThrough([monthly[0]])).toBeNull();
    expect(monthlyThrough([monthly[3], frequent[0]])).toBeNull();
    expect(monthlyThrough([])).toBeNull();
  });
});

// The chart's line is true of every line on it: one date when every line turns on that date; "some accounts" and the
// latest date when the dates differ or some lines never turn; nothing when none turns.
describe("chartSpacing: what the line under a chart of several histories says", () => {
  const monthly = ["2026-06-30", "2026-07-31", "2026-08-31", "2026-09-30"].map((d) => pt(d, 1));
  const frequent = ["2026-10-10", "2026-10-13", "2026-10-16", "2026-10-31"].map((d) => pt(d, 1));
  const early = [pt("2026-03-31", 1), pt("2026-04-30", 1), pt("2026-05-31", 1), pt("2026-06-05", 1), pt("2026-06-08", 1)];
  it("every line turns on one date: that date, of every line", () => {
    expect(chartSpacing({ a: [...monthly, ...frequent], b: [...monthly, ...frequent] }, ["a", "b"]))
      .toEqual({ date: "2026-09-30", every: true });
  });
  it("lines that turn on different dates: the latest, of some", () => {
    expect(chartSpacing({ a: [...monthly, ...frequent], b: early }, ["a", "b"])).toEqual({ date: "2026-09-30", every: false });
  });
  it("a line frequent throughout beside one that turns: of some", () => {
    expect(chartSpacing({ a: [...monthly, ...frequent], c: frequent }, ["a", "c"])).toEqual({ date: "2026-09-30", every: false });
  });
  it("no line turns: nothing", () => {
    expect(chartSpacing({ c: frequent, d: monthly }, ["c", "d"])).toBeNull();
    expect(chartSpacing({}, [])).toBeNull();
  });
  it("every point the history holds is drawn: the line through month ends and frequent points has them all", () => {
    const held = [...monthly, ...frequent];
    const lines = balanceLines({ a: held }, accounts, ["a"], (p) => p.balance, ORDER);
    expect(lines[0].data).toEqual(held.map((p) => [p.date, p.balance]));
  });
});

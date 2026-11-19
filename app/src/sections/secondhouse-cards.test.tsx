// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// the chart is stubbed (jsdom has no canvas) and records the option it was handed
const chart = vi.hoisted(() => ({ option: null as any }));
vi.mock("../components/EChart", () => ({
  default: (p: { option: unknown }) => { chart.option = p.option; return <div data-testid="chart" />; },
}));
const media = vi.hoisted(() => ({ phone: false }));
vi.mock("../lib/useMediaQuery", () => ({ useIsMobile: () => media.phone, useReducedMotion: () => false }));

import { fmtUSD, fmtUSD0 } from "../lib/format";
import {
  breakevenOccupancyPct, carryBreakdown, DEFAULT_INPUTS, rentalBreakdown, type SecondHouseInputs,
} from "../lib/secondHouse";
import { CHART_GAIN, CHART_MUTED, setChartTheme } from "../lib/echarts";
import { NEUTRAL_MARK, POSITIVE } from "../lib/tokens";
import { CostBreakdownCard, type Ctx, type Derived, EquityCurveCard, evenSpan, RentalCard, SweepCard } from "./secondhouse-cards";

const d = {
  carry: { pi: 2000, tax: 400, insurance: 150, hoa: 0, utilities: 200, maintenance: 250 },
  rental: { net: 1200 }, netCost: 1800,
} as unknown as Derived;
const ctx = (over: Partial<Ctx> = {}): Ctx => ({
  liquidCash: 50_000, essential: 6000, freeCash: { monthly: 2500, months: 12 }, noTransactions: false, fullMonths: 24,
  nextFullMonthEnds: "2026-06-30",
  primaryHomeMonthly: 2400, ltcgPct: 15, generatedAt: "2026-06-10T00:00:00-05:00", feed: true, ...over,
});
const marks = () => chart.option.series[1].markLine;
const caption = () => screen.getByTestId("card-cost-breakdown").textContent ?? "";

beforeEach(() => { media.phone = false; chart.option = null; });
afterEach(cleanup);

// A free-cash line above every bar raises the axis' top; with a rental that earns more than the carry the net sits below
// zero, and a fixed top over ECharts' own bottom left the ticks uneven. One even step spans both ends.
describe("Second House cost waterfall: a raised top over a negative net", () => {
  const earning = { ...d, rental: { net: 3500 }, netCost: -500 } as unknown as Derived;
  it("the axis runs in one even step from below the net to above the free-cash line, zero on a tick", () => {
    render(<CostBreakdownCard d={earning} ctx={ctx({ freeCash: { monthly: 5000, months: 12 }, primaryHomeMonthly: null })} />);
    const y = chart.option.yAxis as { min: number; max: number; interval: number };
    expect(y).toMatchObject({ min: -1000, max: 5000, interval: 1000 });
    expect(Number.isInteger((y.max - y.min) / y.interval) && y.min % y.interval === 0).toBe(true);
  });
  it("evenSpan: the least 1-2-2.5-5 step within seven, both ends on it", () => {
    expect(evenSpan(-500, 5000)).toEqual({ min: -1000, max: 5000, interval: 1000 });
    expect(evenSpan(-2_200, 2_500)).toEqual({ min: -3000, max: 3000, interval: 1000 });
    expect(evenSpan(-120, 600)).toEqual({ min: -200, max: 600, interval: 200 });
  });
  it("a raised top over a net above zero keeps the axis from zero with ECharts' own steps", () => {
    render(<CostBreakdownCard d={d} ctx={ctx({ freeCash: { monthly: 5000, months: 12 } })} />);
    expect(chart.option.yAxis.max).toBe(5000);
    expect(chart.option.yAxis.min).toBeUndefined();
  });
});

// Marks are drawn in colours that stand out from a card in both themes (lib/contrast.test.ts): a gain in CHART_GAIN, the
// down-payment sweep's context bars in NEUTRAL_MARK.
describe("Second house's marks take the shared mark colours", () => {
  // read in the light theme, where the gain green differs from POSITIVE (equal in the dark one, so a mark gone back to
  // POSITIVE would pass there) and the neutral mark from the muted grey it replaced
  afterEach(() => setChartTheme("dark"));
  it("the rental's offset in the cost waterfall, the gauge's good band and the equity line are the gain green", () => {
    setChartTheme("light");
    expect(CHART_GAIN).not.toBe(POSITIVE);
    render(<CostBreakdownCard d={d} ctx={ctx()} />);
    expect(chart.option.series[1].data[6].itemStyle.color).toBe(CHART_GAIN);
    cleanup();
    render(<EquityCurveCard i={DEFAULT_INPUTS} />);
    expect(chart.option.series.find((s: { name: string }) => s.name === "Home equity").color).toBe(CHART_GAIN);
    cleanup();
    const i = DEFAULT_INPUTS;
    render(<RentalCard i={i} set={() => {}}
      d={{ rental: rentalBreakdown(i), breakeven: breakevenOccupancyPct(i), carry: carryBreakdown(i) } as unknown as Derived} />);
    const bands = chart.option.series[0].axisLine.lineStyle.color as [number, string][];
    expect(bands.at(-1)![1]).toBe(CHART_GAIN);
  });
  it("the down-payment sweep's bars are the neutral mark, solid, and its band says what it is", () => {
    setChartTheme("light");
    expect(NEUTRAL_MARK).not.toBe(CHART_MUTED);
    render(<SweepCard i={DEFAULT_INPUTS} />);
    const bars = chart.option.series.find((s: { name: string }) => s.name === "Cash to close");
    expect(bars.itemStyle).toEqual({ color: NEUTRAL_MARK });
    // the shaded band is the down payments under 20%: it names that, and claims nothing about what lenders do
    const band = chart.option.series.find((s: { name: string }) => s.name === "Monthly P&I").markArea.data[0];
    expect([band[0].name, band[0].xAxis, band[1].xAxis]).toEqual(["under 20% down", 10, 20]);
  });
});

// At 390px the two dashed lines' end labels ("free cash", "current home") collided with the Net bar's value
// label. On a phone the lines go unlabelled (and the plot gets its gutter back); the caption names each line by
// its colour at every width, so nothing is lost.
describe("Second House cost waterfall: the dashed lines' labels", () => {
  it("desktop: each line names itself at its end, in a right gutter", () => {
    render(<CostBreakdownCard d={d} ctx={ctx()} />);
    expect(marks().data.map((m: { name: string }) => m.name)).toEqual(["current\nhome", "free\ncash"]);
    expect(marks().label.show).not.toBe(false);
    expect(chart.option.grid.right).toBe(44);
  });
  it("phone: the lines are drawn without labels and the plot keeps no label gutter", () => {
    media.phone = true;
    render(<CostBreakdownCard d={d} ctx={ctx()} />);
    expect(marks().data.length).toBe(2);
    expect(marks().label.show).toBe(false);
    expect(chart.option.grid.right).toBeLessThanOrEqual(16);
  });
  it("the caption names each line that is drawn by its colour, and only those", () => {
    render(<CostBreakdownCard d={d} ctx={ctx()} />);
    expect(caption()).toMatch(/grey: what the current home costs per month/);
    expect(caption()).toMatch(/amber: the household's average monthly free cash flow/);
    cleanup();
    render(<CostBreakdownCard d={d} ctx={ctx({ primaryHomeMonthly: null })} />);
    expect(caption()).not.toMatch(/grey/);
    expect(caption()).toMatch(/amber/);
    cleanup();
    render(<CostBreakdownCard d={d} ctx={ctx({ primaryHomeMonthly: null, freeCash: { monthly: 0, months: 12 } })} />);
    expect(marks()).toBeUndefined();
    expect(caption()).not.toMatch(/Dashed/);
  });
  it("with too little history to know the free cash flow, no line is drawn at it and the caption names none", () => {
    render(<CostBreakdownCard d={d} ctx={ctx({ freeCash: null, fullMonths: 1 })} />);
    expect(marks().data.map((m: { name: string }) => m.name)).toEqual(["current\nhome"]);
    expect(caption()).not.toMatch(/amber|free cash/);
  });
});

// The caption names each dashed line, so each is on the chart: a line above every bar raises the axis to hold it (a
// markLine past the axis's top is clipped away, and the caption would name a line nobody sees).
describe("Second House cost waterfall: the axis holds every line its caption names", () => {
  /** the value a named dashed line is drawn at */
  const at = (name: string) => marks().data.find((m: { name: string }) => m.name === name).yAxis;
  it("a free cash flow above every bar: the axis reaches past it, on a round step", () => {
    render(<CostBreakdownCard d={d} ctx={ctx({ freeCash: { monthly: 9_412, months: 12 } })} />);
    expect(caption()).toMatch(/amber: the household's average monthly free cash flow/);
    expect(chart.option.yAxis.max).toBeGreaterThanOrEqual(at("free\ncash"));
    expect(chart.option.yAxis.max).toBe(10_000);
  });
  it("the current home's cost above every bar: the same", () => {
    render(<CostBreakdownCard d={d} ctx={ctx({ primaryHomeMonthly: 13_000 })} />);
    expect(chart.option.yAxis.max).toBeGreaterThanOrEqual(at("current\nhome"));
    expect(chart.option.yAxis.max).toBe(15_000);
  });
  it("lines inside the bars' range leave the axis its own top (the bars reach 3,000: the carry)", () => {
    render(<CostBreakdownCard d={d} ctx={ctx()} />);
    expect(chart.option.yAxis.max).toBeUndefined();
  });
  it("no line at all leaves the axis its own top", () => {
    render(<CostBreakdownCard d={d} ctx={ctx({ freeCash: null, primaryHomeMonthly: null })} />);
    expect(chart.option.yAxis.max).toBeUndefined();
  });
});

// Each line of the rental arithmetic is shown rounded for reading; the total is the rounded sum of the unrounded lines,
// never a sum of rounded ones. Three lines of x.50 each round up when shown, so their shown sum is a dollar or two off
// the total, which is the true one.
describe("Second House rental arithmetic: the total is rounded once", () => {
  const rental = (over: Partial<SecondHouseInputs>) => {
    const i = { ...DEFAULT_INPUTS, ...over };
    render(<RentalCard i={i} set={() => {}}
      d={{ rental: rentalBreakdown(i), breakeven: breakevenOccupancyPct(i), carry: carryBreakdown(i) } as unknown as Derived} />);
    return [...screen.getByTestId("card-rental-model").querySelectorAll("tbody tr")]
      .map((r) => [...r.querySelectorAll("td")].map((c) => c.textContent));
  };
  it("three fee lines of $100.50: the total is $1,005 − $301.50 rounded, $704, not the $702 of the rounded lines", () => {
    // 5 nights at $201: 10% platform, 10% management and 3 stays at $33.50 are $100.50 each
    const rows = rental({ nightlyRate: 201, occupancyPct: 50, nightsPerMonth: 10, platformFeePct: 10, mgmtFeePct: 10,
      cleaningPerStay: 33.5, staysPerMonth: 3 });
    expect(rows).toEqual([
      [`Gross (5 nights × ${fmtUSD0(201)})`, fmtUSD0(1_005)],
      ["− Platform fee (10%)", `−${fmtUSD0(100.5)}`],
      ["− Management (10%)", `−${fmtUSD0(100.5)}`],
      [`− Cleaning (3 × ${fmtUSD(33.5)})`, `−${fmtUSD0(100.5)}`],
      ["Net rental /mo", fmtUSD0(703.5)],
    ]);
    expect(fmtUSD0(703.5)).toBe("$704");
  });
  it("the gross line's own arithmetic holds: the booked nights it multiplies are the ones the gross is of", () => {
    // 27 nights at 48% occupancy are 12.96 booked nights; at $285 that is $3,693.60
    const rows = rental({ nightlyRate: 285, occupancyPct: 48, nightsPerMonth: 27 });
    expect(rows[0]).toEqual([`Gross (12.96 nights × ${fmtUSD0(285)})`, fmtUSD0(3_693.6)]);
  });
});

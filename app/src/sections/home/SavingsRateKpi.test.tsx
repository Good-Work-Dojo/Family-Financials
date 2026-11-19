// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { fmtUSD0 } from "../../lib/format";
import SavingsRateKpi from "./SavingsRateKpi";

afterEach(cleanup);

const card = () => screen.getByTestId("kpi-savings-rate-ttm");
/** the value line carries the tone class (StatCard: text-emerald-400 / text-red-400 / text-zinc-100) */
const value = () => card().querySelector("div.tabular-nums")!;

describe("SavingsRateKpi", () => {
  it("a full year reads 12 mo (not TTM), in the positive tone", () => {
    render(<SavingsRateKpi rate={0.19} months={12} net={1_900} />);
    expect(card().textContent).toContain("Savings rate (12 mo)");
    expect(card().textContent).not.toContain("TTM");
    expect(card().textContent).toContain("19%");
    expect(card().textContent).toContain("trailing 12 full months");
    expect(value().className).toContain("text-emerald-400");
  });
  it("a negative rate is red", () => {
    render(<SavingsRateKpi rate={-0.04} months={12} net={-400} />);
    expect(value().className).toContain("text-red-400");
  });
  it("fewer than 12 months says how many were used", () => {
    render(<SavingsRateKpi rate={0.1} months={5} net={1_000} />);
    expect(card().textContent).toContain("Savings rate (5 mo)");
    expect(card().textContent).not.toContain("TTM");
    expect(card().textContent).toContain("trailing 5 full months");
  });
  it("one month is singular", () => {
    render(<SavingsRateKpi rate={0.1} months={1} net={1_000} />);
    expect(card().textContent).toContain("trailing 1 full month ·");
  });
  it("no full month: no count in the label, an em dash in the neutral tone, and not enough history yet", () => {
    render(<SavingsRateKpi rate={null} months={0} net={0} />);
    expect(card().textContent).toBe("Savings rate—Not enough history yet: no full month of cash flow");
    expect(card().textContent, "no window of 0 months").not.toMatch(/0 mo|\(\d+ mo\)/);
    expect(value().className).not.toContain("text-emerald-400");
    expect(value().className).not.toContain("text-red-400");
  });
  it("no full month reads the same whatever rate it is handed: there is no window to rate", () => {
    render(<SavingsRateKpi rate={0.4} months={0} net={0} />);
    expect(value().textContent).toBe("—");
    expect(card().textContent).not.toContain("40%");
  });
  it("a null rate over real months (no income) is also neutral", () => {
    render(<SavingsRateKpi rate={null} months={12} net={-500} />);
    expect(value().textContent).toBe("—");
    expect(value().className).not.toContain("text-emerald-400");
  });
});

describe("SavingsRateKpi when spending is over twice the income", () => {
  it("interest as the only income: no percentage, the shortfall in dollars, in the neutral tone", () => {
    render(<SavingsRateKpi rate={-41.77} months={12} net={-7_715} />);
    expect(value().textContent).toBe("—");
    expect(card().textContent).not.toMatch(/%/);
    expect(card().textContent).toContain(`income did not cover spending: ${fmtUSD0(-7_715)} a month · trailing 12 full months`);
    expect(value().className).not.toContain("text-red-400");
  });
  it("at −100% (spending twice the income) the rate is still printed; just below it is not", () => {
    render(<SavingsRateKpi rate={-1} months={12} net={-1_000} />);
    expect(value().textContent).toBe("−100%");
    expect(card().textContent).not.toContain("did not cover");
    cleanup();
    render(<SavingsRateKpi rate={-1.0001} months={12} net={-1_000} />);
    expect(value().textContent).toBe("—");
    expect(card().textContent).toContain("did not cover spending");
  });
});

describe("SavingsRateKpi while Home is rewound", () => {
  it("says it is still the latest rate, not as of the viewed snapshot", () => {
    render(<SavingsRateKpi rate={0.19} months={12} net={1_900} rewoundTo="Jul 31, 2025" />);
    expect(card().textContent).toContain("Savings rate (12 mo) · latest");
    expect(card().textContent).toContain("not rewound to Jul 31, 2025");
  });
  it("carries no such note at the latest snapshot", () => {
    render(<SavingsRateKpi rate={0.19} months={12} net={1_900} />);
    expect(card().textContent).not.toContain("latest");
    expect(card().textContent).not.toContain("rewound");
  });
});

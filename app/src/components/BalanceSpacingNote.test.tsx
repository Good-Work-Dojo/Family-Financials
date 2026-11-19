// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { BalancePoint } from "../data/types";
import BalanceSpacingNote from "./BalanceSpacingNote";

afterEach(cleanup);
const pt = (date: string): BalancePoint => ({ date, balance: 1, available: null, limit: null, utilization: null, source: "simplefin" });
const monthly = ["2026-06-30", "2026-07-31", "2026-08-31", "2026-09-30"].map(pt);
const frequent = ["2026-10-10", "2026-10-13", "2026-10-16", "2026-10-31"].map(pt);
const early = ["2026-03-31", "2026-04-30", "2026-05-31", "2026-06-05", "2026-06-08"].map(pt);
const line = () => screen.getByTestId("balance-spacing").textContent;

// The line under a balance chart whose histories start monthly and turn frequent: every point is drawn, and the reader
// is told why the line moves more after a date, in words true of every line on the chart.
describe("BalanceSpacingNote", () => {
  it("one history of month ends then frequent points: one line naming the last month end, through fmtDate", () => {
    render(<BalanceSpacingNote series={{ a: [...monthly, ...frequent] }} ids={["a"]} />);
    expect(line()).toBe("One balance a month through Sep 30, 2026; more often since.");
  });
  it("every history turning on one date: the same line", () => {
    render(<BalanceSpacingNote series={{ a: [...monthly, ...frequent], b: [...monthly, ...frequent] }} ids={["a", "b"]} />);
    expect(line()).toBe("One balance a month through Sep 30, 2026; more often since.");
  });
  it("histories turning on different dates: some accounts, the latest date", () => {
    render(<BalanceSpacingNote series={{ a: [...monthly, ...frequent], b: early }} ids={["a", "b"]} />);
    expect(line()).toBe("Some accounts have one balance a month in their early history, the latest through Sep 30, 2026; "
      + "more often since.");
  });
  it("a history frequent throughout beside one that turns: some accounts", () => {
    render(<BalanceSpacingNote series={{ a: [...monthly, ...frequent], c: frequent }} ids={["a", "c"]} />);
    expect(line()).toMatch(/^Some accounts have one balance a month in their early history, the latest through Sep 30, 2026;/);
  });
  it("no line for histories of one spacing, frequent or monthly, or a frequent one with an outage", () => {
    const outage = [...frequent, ...["2026-12-05", "2026-12-08"].map(pt)];
    render(<BalanceSpacingNote series={{ a: frequent, b: monthly, c: outage }} ids={["a", "b", "c"]} />);
    expect(screen.queryByTestId("balance-spacing")).toBeNull();
  });
});

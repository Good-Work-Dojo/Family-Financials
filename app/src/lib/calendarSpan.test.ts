// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { calendarSpan, weeksSpanned } from "./calendarSpan";

// Explore's daily spend calendar draws the months that hold spending in the window, at most a year (half on a phone):
// never a year of which most months are empty days.
describe("calendarSpan", () => {
  it("a short history is drawn as the months it holds, not as a year of mostly empty months", () => {
    const days = ["2026-10-27", "2026-10-28", "2026-10-30"];
    expect(calendarSpan(days, "2026-10-30", 12)).toEqual({ from: "2026-10", to: "2026-10", range: ["2026-10-01", "2026-10-30"], capped: false });
  });
  it("six months of spending in a 6-month window: six months, from the first day of the first", () => {
    const days = ["2026-05-01", "2026-07-14", "2026-10-29"];
    expect(calendarSpan(days, "2026-10-30", 12)).toEqual({ from: "2026-05", to: "2026-10", range: ["2026-05-01", "2026-10-30"], capped: false });
  });
  it("draws at most `maxMonths`, ending with the newest month that holds spending, and says when it leaves earlier spending out", () => {
    const days = ["2025-01-15", "2025-11-02", "2026-10-29"];
    expect(calendarSpan(days, "2026-10-30", 12)).toEqual({ from: "2025-11", to: "2026-10", range: ["2025-11-01", "2026-10-30"], capped: true });
    expect(calendarSpan(days, "2026-10-30", 6)).toEqual({ from: "2026-10", to: "2026-10", range: ["2026-10-01", "2026-10-30"], capped: true });
  });
  it("a To date past the last spending ends the calendar with the last month that holds any, at that month's end", () => {
    expect(calendarSpan(["2026-03-03", "2026-04-20"], "2026-09-30", 12))
      .toEqual({ from: "2026-03", to: "2026-04", range: ["2026-03-01", "2026-04-30"], capped: false });
  });
  it("a day after the window's end is not drawn, and with no day on or before it there is no span", () => {
    expect(calendarSpan(["2026-11-02"], "2026-10-30", 12)).toBeNull();
    expect(calendarSpan([], "2026-10-30", 12)).toBeNull();
  });
  it("spending that stopped two years before the window's end: the calendar ends with its last month, never 'no spending'", () => {
    const days = ["2023-03-05", "2024-02-11", "2024-08-20", "2024-10-03"];
    expect(calendarSpan(days, "2026-10-30", 12)).toEqual({ from: "2024-02", to: "2024-10", range: ["2024-02-01", "2024-10-31"], capped: true });
    expect(calendarSpan(days, "2026-10-30", 6)).toEqual({ from: "2024-08", to: "2024-10", range: ["2024-08-01", "2024-10-31"], capped: true });
  });
  it("counts months across a year end", () => {
    expect(calendarSpan(["2025-12-31", "2026-01-02"], "2026-01-31", 2)?.from).toBe("2025-12");
    expect(calendarSpan(["2025-11-30", "2026-01-02"], "2026-01-31", 2)?.capped).toBe(true);
  });
});

describe("weeksSpanned", () => {
  it("counts the rows of Sunday-first weeks a range draws, its first week's earlier days included", () => {
    expect(weeksSpanned(["2026-11-01", "2026-11-07"])).toBe(1);              // a Sunday to a Saturday
    expect(weeksSpanned(["2026-11-01", "2026-11-08"])).toBe(2);
    expect(weeksSpanned(["2026-10-31", "2026-10-31"])).toBe(1);              // one Saturday
    expect(weeksSpanned(["2026-10-31", "2026-11-01"])).toBe(2);              // a Saturday and the Sunday after it
    // August to October 2026: August 1 is a Saturday, October 31 a Saturday - 92 days from the week's sixth day
    expect(weeksSpanned(["2026-08-01", "2026-10-31"])).toBe(Math.ceil((6 + 92) / 7));
  });
});

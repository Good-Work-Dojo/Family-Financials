// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { compareWindow } from "./compareWindow";

// Explore's compare ends where Home's "this month so far" does - the data's last COMPLETE day, the ingest day itself
// still coming in - and its title says in plain words which days of each month it sets side by side.
describe("compareWindow: Explore's month-over-month and year-over-year", () => {
  const at = (dataThrough: string | null, generatedAt: string, inProgress: string | null, more: { to?: string; mode?: "mom" | "yoy" } = {}) =>
    compareWindow({ dataThrough, generatedAt, inProgress, to: more.to, mode: more.mode ?? "mom" });

  it("the last day of the month: the ingest day is still coming in, so the first 30 days of October against September's 30", () => {
    const w = at("2026-10-31", "2026-10-31T18:00:00-04:00", "2026-10")!;
    expect(w.periods.cur).toEqual(["2026-10-01", "2026-10-30"]);
    expect(w.periods.prev).toEqual(["2026-09-01", "2026-09-30"]);
    expect(w.words).toBe("Oct 2026 vs Sep 2026, the first 30 days of each");
    expect(w.words).not.toMatch(/MTD/);
  });

  it("the 1st, with nothing of the new month complete yet: the month before, whole, against the whole one before it", () => {
    const w = at("2026-11-01", "2026-11-01T08:00:00-04:00", "2026-11")!;
    expect(w.periods.cur).toEqual(["2026-10-01", "2026-10-31"]);
    expect(w.periods.mtdDays).toBeNull();
    expect(w.words).toBe("Oct 2026 vs Sep 2026");
  });

  it("mid-month: the first 14 days of each, through the day before the ingest", () => {
    const w = at("2026-10-15", "2026-10-15T12:00:00-04:00", "2026-10")!;
    expect(w.periods.cur).toEqual(["2026-10-01", "2026-10-14"]);
    expect(w.periods.prev).toEqual(["2026-09-01", "2026-09-14"]);
    expect(w.words).toBe("Oct 2026 vs Sep 2026, the first 14 days of each");
  });

  it("data that stop before the ingest day end the compare on their own last day, which is complete", () => {
    const w = at("2026-08-20", "2026-10-31T18:00:00-04:00", "2026-08")!;
    expect(w.periods.cur).toEqual(["2026-08-01", "2026-08-20"]);
    expect(w.words).toBe("Aug 2026 vs Jul 2026, the first 20 days of each");
  });

  it("a shorter older month is compared whole, and the words name both counts", () => {
    const w = at("2026-03-31", "2026-03-31T18:00:00-05:00", "2026-03")!;
    expect(w.periods.prev).toEqual(["2026-02-01", "2026-02-28"]);
    expect(w.words).toBe("Mar 2026 vs Feb 2026, the first 30 days of Mar 2026 against all 28 of Feb 2026");
  });

  it("a To date before the last complete day ends it there; on a month's last day of a month not in progress it is whole", () => {
    expect(at("2026-10-31", "2026-10-31T18:00:00-04:00", "2026-10", { to: "2026-10-15" })!.words)
      .toBe("Oct 2026 vs Sep 2026, the first 15 days of each");
    expect(at("2026-10-31", "2026-10-31T18:00:00-04:00", "2026-10", { to: "2026-09-30" })!.words).toBe("Sep 2026 vs Aug 2026");
    expect(at("2026-10-31", "2026-10-31T18:00:00-04:00", "2026-10", { to: "2026-12-31" })!.periods.cur[1], "a later To changes nothing")
      .toBe("2026-10-30");
  });

  it("year-over-year sets the same days of the same month a year apart; one day is said as one", () => {
    expect(at("2026-10-31", "2026-10-31T18:00:00-04:00", "2026-10", { mode: "yoy" })!.words)
      .toBe("Oct 2026 vs Oct 2025, the first 30 days of each");
    expect(at("2026-10-02", "2026-10-02T09:00:00-04:00", "2026-10")!.words).toBe("Oct 2026 vs Sep 2026, the first day of each");
  });

  it("with no transaction there is nothing to compare", () => {
    expect(at(null, "2026-10-31T18:00:00-04:00", null)).toBeNull();
  });
});

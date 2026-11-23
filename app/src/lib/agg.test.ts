// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { AppData, CashflowMonth, Transaction } from "../data/types";
import { HAVE_EXPORTS, readExport, requireExports } from "../test/exports";
import { withoutComments } from "../test/sourceText";
import { cfmFactory, txFactory } from "./__fixtures__";
import {
  comparePeriods, countedRows, coverageFloor, coverageFloorLabel, coverageMonths, currentYear, dataThrough, datedAhead, daysInMonth,
  fullMonths, isIncome, monthInProgress,
  monthlyByCategory, monthsBetween, nextMonth, partialMonths, periodCompare, prevMonth, RUN_RATE_MIN_MONTHS, runRateMonths,
  runRateWindow, dataBehindIngest, dataFirstDay, COVERED_FROM_DAY, coversFromStart, nextFullMonthEnds,
  savingsRate, spanDays, totalSpend, trailingWindow, vendorStats, WEEKDAY_MIN_DAYS, weekdaySpend, windowPartialMonths,
} from "./agg";
import { partitionSplits } from "./splits";
import { latestOnOrBefore, mean, median, slope, stdev } from "./stats";

const tx = txFactory();
const totalIncome = (ts: Transaction[]) => ts.filter(isIncome).reduce((s, t) => s + t.amount, 0);   // income = income + dividend + interest

describe("accounting rules", () => {
  it("transfers and cc-payments never count as spend or income (regression guard)", () => {
    const txns = [
      tx({ amount: -100, type: "purchase" }),
      tx({ amount: 20, type: "purchase" }),            // refund nets out
      tx({ amount: -5, type: "fee" }),
      tx({ amount: -3000, type: "cc-payment" }),
      tx({ amount: -2000, type: "transfer" }),
      tx({ amount: 2000, type: "transfer" }),
      tx({ amount: -1500, type: "investment" }),
      tx({ amount: 5000, type: "income" }),
      tx({ amount: 12, type: "dividend" }),
      tx({ amount: 1, type: "interest" }),
    ];
    expect(totalSpend(txns)).toBe(100 - 20 + 5);
    expect(totalIncome(txns)).toBe(5013);
  });
});

describe("monthly rollups", () => {
  it("sums equal raw sums and months are contiguous", () => {
    const txns = [
      tx({ date: "2025-01-05", category: "Dining", amount: -50 }),
      tx({ date: "2025-03-20", category: "Dining", amount: -30 }),
      tx({ date: "2025-03-22", category: "Travel", amount: -200 }),
    ];
    const m = monthlyByCategory(txns);
    expect(m.months).toEqual(["2025-01", "2025-02", "2025-03"]);
    const total = m.months.reduce((s, ym) =>
      s + [...(m.data.get(ym)?.values() ?? [])].reduce((a, b) => a + b, 0), 0);
    expect(total).toBe(280);
  });

  it("no spending row (none at all, or only income) is no month and no category", () => {
    for (const txns of [[], [tx({ date: "2025-01-05", type: "income", amount: 900 })]]) {
      const m = monthlyByCategory(txns);
      expect(m.months).toEqual([]);
      expect(m.categories).toEqual([]);
      expect(m.data.size).toBe(0);
    }
  });

  it("monthsBetween spans year boundary", () => {
    expect(monthsBetween("2024-11-15", "2025-02-01"))
      .toEqual(["2024-11", "2024-12", "2025-01", "2025-02"]);
  });

  it("periodCompare buckets both windows", () => {
    const txns = [
      tx({ date: "2025-05-10", amount: -100 }),
      tx({ date: "2025-04-10", amount: -60 }),
    ];
    const rows = periodCompare(txns, "2025-05-01", "2025-05-31", "2025-04-01", "2025-04-30");
    expect(rows[0]).toEqual({ category: "Dining", current: 100, previous: 60 });
  });

  it("trailingWindow computes correct start", () => {
    expect(trailingWindow("2026-05", 12)[0]).toBe("2025-06-01");
    expect(trailingWindow("2026-01", 3)[0]).toBe("2025-11-01");
  });

  it("trailingWindow ends on the real last day of the month", () => {
    // the end date can reach the page — "2026-06-31" is not a date
    expect(trailingWindow("2026-06", 3)[1]).toBe("2026-06-30");
    expect(trailingWindow("2026-02", 1)[1]).toBe("2026-02-28");
    expect(trailingWindow("2024-02", 1)[1]).toBe("2024-02-29");   // leap year
    expect(trailingWindow("2026-07", 1)[1]).toBe("2026-07-31");
  });
});

// Explore's weekday card: each weekday's outflow over the days the window and the data share, divided by how many times
// that weekday falls among those days - never by whole months the days do not fill.
describe("weekdaySpend", () => {
  // 2026-06-01 is a Monday
  it("five days of data average over the five days: each weekday once, the two days not reached have no average", () => {
    const rows = [tx({ date: "2026-06-04", amount: -45 }), tx({ date: "2026-06-04", amount: -5 }),   // Thursday
      tx({ date: "2026-06-06", amount: -55 })];                                                       // Saturday
    // Tue Jun 2 .. Sat Jun 6: Tue, Wed, Thu, Fri, Sat once each; Sun and Mon not at all
    expect(weekdaySpend(rows, "2026-06-02", "2026-06-06")).toEqual([null, null, 0, 0, 50, 0, 55]);
  });
  it("two whole weeks and three days: the first three weekdays fall three times, the rest twice", () => {
    const mondays = [tx({ date: "2026-06-01", amount: -30 }), tx({ date: "2026-06-08", amount: -30 }), tx({ date: "2026-06-15", amount: -30 })];
    const avg = weekdaySpend(mondays, "2026-06-01", "2026-06-17");   // Mon Jun 1 .. Wed Jun 17: 17 days
    expect(avg[1], "Monday: three Mondays").toBe(30);
    expect(avg[4], "Thursday: two").toBe(0);
  });
  it("counts outflows inside the days only: a refund, income, a row before or after the days are no spend of them", () => {
    const rows = [tx({ date: "2026-06-03", amount: -70 }), tx({ date: "2026-06-03", amount: 20 }),          // a refund
      tx({ date: "2026-06-03", amount: 900, type: "income" }), tx({ date: "2026-05-27", amount: -999 }),     // before
      tx({ date: "2026-06-10", amount: -999 })];                                                              // after
    expect(weekdaySpend(rows, "2026-06-01", "2026-06-07")[3]).toBe(70);
  });
  it("days that end before they start reach no weekday", () => {
    expect(weekdaySpend([], "2026-06-10", "2026-06-01")).toEqual([null, null, null, null, null, null, null]);
  });
  it("the card's minimum is four weeks of days, each end counted, so every weekday falls four times", () => {
    expect(WEEKDAY_MIN_DAYS).toBe(28);
    expect([spanDays("2026-06-01", "2026-06-28"), spanDays("2026-06-06", "2026-06-09"), spanDays("2026-06-09", "2026-06-01")])
      .toEqual([28, 4, 0]);
    expect(spanDays("2026-02-15", "2026-03-14"), "across a short month").toBe(28);
  });
});

describe("stats", () => {
  it("mean/median/stdev/slope against hand-computed values", () => {
    expect(mean([1, 2, 3, 4])).toBe(2.5);
    expect(median([5, 1, 3])).toBe(3);
    expect(median([4, 1, 3, 2])).toBe(2.5);
    expect(stdev([2, 4, 4, 4, 5, 5, 7, 9])).toBe(2);
    expect(slope([1, 2, 3, 4])).toBeCloseTo(1);
    expect(slope([4, 4, 4])).toBe(0);
  });
});

// ETL <-> client cross-check: the client's unfiltered monthly rollup must equal
// the ETL's cashflow_monthly.json. Catches Python/TS accounting drift.
describe("month helpers", () => {
  it("prevMonth / nextMonth handle year rollovers", () => {
    expect(prevMonth("2026-01")).toBe("2025-12");
    expect(prevMonth("2026-07")).toBe("2026-06");
    expect(nextMonth("2025-12")).toBe("2026-01");
    expect(nextMonth("2026-06")).toBe("2026-07");
  });
});

// Explore's month-over-month table ended at the newest data date and compared that whole calendar month, in progress or
// not, against a whole older month: on the 4th every category looked like a big decrease. An in-progress month is
// compared month-to-date against the SAME days of the older period, and the title says so.
describe("comparePeriods (Explore's MoM / YoY)", () => {
  it("on day 4 the month-over-month is the first four days against the first four days of the month before", () => {
    const p = comparePeriods("2026-10-04", "mom");
    expect(p.cur).toEqual(["2026-10-01", "2026-10-04"]);
    expect(p.prev).toEqual(["2026-09-01", "2026-09-04"]);        // not "2026-09-30": partial against full is the bug
    expect([p.curMonth, p.prevMonth, p.mtdDays, p.prevDays]).toEqual(["2026-10", "2026-09", 4, 4]);
  });
  it("a whole month (its last day) is compared whole against the whole month before - no MTD", () => {
    const p = comparePeriods("2026-09-30", "mom");
    expect(p.cur).toEqual(["2026-09-01", "2026-09-30"]);
    expect(p.prev).toEqual(["2026-08-01", "2026-08-31"]);
    expect(p.mtdDays).toBeNull();
    expect(comparePeriods("2026-03-31", "mom").prev).toEqual(["2026-02-01", "2026-02-28"]);
  });
  it("a month-to-date longer than the older month is cut to the older month and says how many days it has", () => {
    const p = comparePeriods("2026-03-30", "mom");              // 30 days of March against all 28 of February
    expect(p.prev).toEqual(["2026-02-01", "2026-02-28"]);
    expect([p.mtdDays, p.prevDays]).toEqual([30, 28]);
  });
  it("year-over-year is the same month a year earlier, same days (Feb 29 clips to Feb 28)", () => {
    expect(comparePeriods("2026-10-04", "yoy").prev).toEqual(["2025-10-01", "2025-10-04"]);
    expect(comparePeriods("2024-02-29", "yoy").cur).toEqual(["2024-02-01", "2024-02-29"]);
    expect(comparePeriods("2024-02-29", "yoy").mtdDays).toBeNull();                          // the 29th IS Feb 2024's last day
    expect(comparePeriods("2024-02-28", "yoy").prev).toEqual(["2023-02-01", "2023-02-28"]);
  });
  it("crosses a year boundary by string arithmetic, not a Date round trip", () => {
    expect(comparePeriods("2026-01-10", "mom").prev).toEqual(["2025-12-01", "2025-12-10"]);
    expect(daysInMonth("2024-02")).toBe(29);
    expect(daysInMonth("2026-12")).toBe(31);
  });
  it("with no data date there is nothing to compare: no periods at all", () => {
    expect(comparePeriods(null, "mom")).toBeNull();
    expect(comparePeriods(null, "yoy")).toBeNull();
  });
});

describe("dataThrough", () => {
  it("is the newest transaction date, clamped to the day the ingest ran (a future-dated row is not data through tomorrow)", () => {
    expect(dataThrough("2026-10-05", "2026-10-04T14:44:11-05:00")).toBe("2026-10-04");
    expect(dataThrough("2026-10-03", "2026-10-04T14:44:11-05:00")).toBe("2026-10-03");
    expect(dataThrough("2026-10-04", "2026-10-04T14:44:11-05:00")).toBe("2026-10-04");
  });
  it("an unreadable ingest stamp leaves the newest transaction date alone", () => {
    expect(dataThrough("2026-10-05", "")).toBe("2026-10-05");
    expect(dataThrough("2026-10-05", "unknown")).toBe("2026-10-05");
  });
  it("with no transaction the data runs through no day, whatever the ingest stamp says", () => {
    expect(dataThrough(null, "2026-10-04T14:44:11-05:00")).toBeNull();
    expect(dataThrough(null, "")).toBeNull();
  });
});

describe("partialMonths (the months a window covers only in part)", () => {
  it("stars an in-progress last month and a first month the data starts partway through", () => {
    const months = ["2024-06", "2024-07", "2024-08"];
    expect([...partialMonths(months, "2024-06-12", "2024-08-04")]).toEqual(["2024-06", "2024-08"]);
  });
  it("a window that is whole months at both ends stars nothing", () => {
    expect(partialMonths(["2024-06", "2024-07"], "2024-06-01", "2024-07-31").size).toBe(0);
    expect(partialMonths([], "2024-06-01", "2024-07-31").size).toBe(0);
  });
  it("a single month cut at both ends is one partial month", () => {
    expect([...partialMonths(["2024-06"], "2024-06-05", "2024-06-20")]).toEqual(["2024-06"]);
  });
  it("a window with no data at one end or both stars nothing", () => {
    expect(partialMonths(["2024-06"], null, null).size).toBe(0);
    expect(partialMonths(["2024-06"], "2024-06-05", null).size).toBe(0);
    expect(partialMonths(["2024-06"], null, "2024-06-20").size).toBe(0);
  });
  it("a first month the window begins in by its COVERED_FROM_DAY-th day is covered: the 1st and the 3rd, never the 4th", () => {
    expect(COVERED_FROM_DAY).toBe(3);
    for (const [start, partial] of [["2024-06-01", false], ["2024-06-02", false], ["2024-06-03", false], ["2024-06-04", true]] as const) {
      expect(partialMonths(["2024-06", "2024-07"], start, "2024-07-31").has("2024-06"), start).toBe(partial);
    }
  });
});

describe("coversFromStart: the one rule for a first period covered in part", () => {
  it("a period that starts on a month's 1st (a month, a quarter, a year): covered from a start on or before its 3rd", () => {
    expect(coversFromStart("2026-04-01", "2026-04-01")).toBe(true);
    expect(coversFromStart("2026-04-01", "2026-04-03")).toBe(true);
    expect(coversFromStart("2026-04-01", "2026-04-04")).toBe(false);
    expect(coversFromStart("2026-01-01", "2026-01-03"), "a year").toBe(true);
    expect(coversFromStart("2026-04-01", "2026-03-20"), "a window that began before it").toBe(true);
  });
  it("a period that starts on another day (a week): covered only from a start on or before that day", () => {
    expect(coversFromStart("2026-06-15", "2026-06-15")).toBe(true);
    expect(coversFromStart("2026-06-15", "2026-06-16")).toBe(false);
  });
  it("a From a person picked is judged strictly: after the 1st its month is partial, the 2nd and the 3rd included", () => {
    expect(coversFromStart("2026-04-01", "2026-04-01", { picked: true })).toBe(true);
    expect(coversFromStart("2026-04-01", "2026-04-02", { picked: true })).toBe(false);
    expect(coversFromStart("2026-04-01", "2026-04-03", { picked: true })).toBe(false);
    expect(coversFromStart("2026-04-01", "2026-03-20", { picked: true }), "a From before the period").toBe(true);
    expect(coversFromStart("2026-01-01", "2026-01-02", { interval: "year", picked: true }), "a year").toBe(false);
  });
  it("a week is judged strictly even when it starts on a month's 1st", () => {
    // 2026-06-01 is a Monday: a weekly report's first week starts on a date ending -01
    expect(coversFromStart("2026-06-01", "2026-06-01", { interval: "week" })).toBe(true);
    expect(coversFromStart("2026-06-01", "2026-06-02", { interval: "week" })).toBe(false);
    expect(coversFromStart("2026-06-01", "2026-06-03", { interval: "week" })).toBe(false);
    expect(coversFromStart("2026-06-01", "2026-06-03", { interval: "month" }), "the same day, for a month").toBe(true);
  });
});

describe("windowPartialMonths: a monthly chart's window, the From and To a person picked cut to the data", () => {
  const months = ["2026-04", "2026-05", "2026-06"];
  const data = { dataStart: "2026-04-01", dataEnd: "2026-06-30" };
  it("a picked From on the 2nd or the 3rd stars its month; on the 1st it does not", () => {
    expect([...windowPartialMonths(months, { ...data, from: "2026-04-02" }, null)]).toEqual(["2026-04"]);
    expect([...windowPartialMonths(months, { ...data, from: "2026-04-03" }, null)]).toEqual(["2026-04"]);
    expect(windowPartialMonths(months, { ...data, from: "2026-04-01" }, null).size).toBe(0);
  });
  it("a window that starts on the data's first day, the 3rd, does not star it; one on the 4th does", () => {
    expect(windowPartialMonths(months, { ...data, dataStart: "2026-04-03" }, null).size).toBe(0);
    expect(windowPartialMonths(months, { ...data, dataStart: "2026-04-03", from: "2026-04-03" }, null).size,
      "a From on the data's own first day is where the data begins").toBe(0);
    expect(windowPartialMonths(months, { ...data, dataStart: "2026-04-03", from: "2026-03-01" }, null).size,
      "a From before the data is cut to it").toBe(0);
    expect([...windowPartialMonths(months, { ...data, dataStart: "2026-04-04" }, null)]).toEqual(["2026-04"]);
  });
  it("the To date and the month in progress star the last month, and no data stars nothing", () => {
    expect([...windowPartialMonths(months, { ...data, to: "2026-06-20" }, null)]).toEqual(["2026-06"]);
    expect([...windowPartialMonths(months, data, "2026-06")]).toEqual(["2026-06"]);
    expect(windowPartialMonths(months, { dataStart: null, dataEnd: null, from: "2026-04-02" }, null).size).toBe(0);
  });
});

describe("savingsRate", () => {
  const cfm = cfmFactory({ savings_rate: null });
  const m = (income: number, spend: number) => cfm({ income, spend });
  it("aggregates income and spend across months before dividing", () => {
    expect(savingsRate([m(10000, 5000), m(10000, 9000)])).toBeCloseTo(6000 / 20000);
  });
  it("returns null (no data) when the window has no income", () => {
    expect(savingsRate([m(0, 5000)])).toBeNull();
    expect(savingsRate([])).toBeNull();
  });
});

describe("coverageMonths / coverageFloor / coverageFloorLabel (the export's in_coverage)", () => {
  const cfm = cfmFactory();
  // the export's flags: false up to the floor, true from it on
  const months = [["2030-01", false], ["2030-02", false], ["2030-03", true], ["2030-04", true]]
    .map(([month, in_coverage]) => cfm({ month: month as string, in_coverage: in_coverage as boolean }));
  it("drops the months before the coverage floor, keeps the floor itself", () => {
    expect(coverageMonths(months).map((m) => m.month)).toEqual(["2030-03", "2030-04"]);
  });
  it("the floor is the first month in coverage, and its label the month in prose", () => {
    expect(coverageFloor(months)).toBe("2030-03");
    expect(coverageFloorLabel(months)).toBe("Mar 2030");
  });
  it("no month in coverage: nothing to average over, and no floor or label (null, never a made-up month)", () => {
    const none = months.map((m) => ({ ...m, in_coverage: false }));
    expect(coverageMonths(none)).toEqual([]);
    expect(coverageFloor(none)).toBeNull();
    expect(coverageFloorLabel(none)).toBeNull();
    expect(coverageFloor([])).toBeNull();
    expect(coverageFloorLabel([])).toBeNull();
  });
});

describe("runRateWindow: the full months a planning run-rate reads", () => {
  const cfm = cfmFactory();
  /** cash-flow months from `first` through `last`, the ones before `floor` out of coverage */
  const months = (first: string, last: string, floor = first) =>
    monthsBetween(first, last).map((month) => cfm({ month, in_coverage: month >= floor }));
  it("needs RUN_RATE_MIN_MONTHS full months: with fewer the rate is unknown, never one from a week or a month", () => {
    expect(RUN_RATE_MIN_MONTHS).toBe(3);
    expect(runRateWindow([], null, 12, null), "no row at all").toBeNull();
    expect(runRateWindow(months("2026-06", "2026-06"), "2026-06", 12, "2026-06-01"), "only the month in progress").toBeNull();
    expect(runRateWindow(months("2026-05", "2026-06"), "2026-06", 12, "2026-05-01"), "one full month").toBeNull();
    expect(runRateWindow(months("2026-04", "2026-06"), "2026-06", 12, "2026-04-01"), "two").toBeNull();
    expect(runRateWindow(months("2026-03", "2026-06"), "2026-06", 12, "2026-03-01"))
      .toEqual({ from: "2026-03", to: "2026-05", months: 3 });
  });
  it("cut at the first full month: it never reaches back before the data, and says how many months it spans", () => {
    expect(runRateWindow(months("2025-12", "2026-06"), "2026-06", 12, "2025-12-01")).toEqual({ from: "2025-12", to: "2026-05", months: 6 });
    expect(runRateWindow(months("2025-12", "2026-06"), "2026-06", 6, "2025-12-01")).toEqual({ from: "2025-12", to: "2026-05", months: 6 });
    expect(runRateWindow(months("2025-12", "2026-06"), "2026-06", 4, "2025-12-01")).toEqual({ from: "2026-02", to: "2026-05", months: 4 });
  });
  it("counts only months in coverage, and none at or after the month in progress", () => {
    const cf = months("2025-01", "2026-07", "2026-02");                  // in coverage from Feb 2026; July dated ahead
    expect(runRateWindow(cf, "2026-06", 12, "2025-01-01")).toEqual({ from: "2026-02", to: "2026-05", months: 4 });
    expect(runRateWindow(months("2025-01", "2026-06", "2026-04"), "2026-06", 12, "2025-01-01"), "two months in coverage").toBeNull();
  });
  it("with the history long enough it is the trailing n months", () => {
    expect(runRateWindow(months("2023-01", "2026-06"), "2026-06", 12, "2023-01-01"))
      .toEqual({ from: "2025-06", to: "2026-05", months: 12 });
  });
});

describe("runRateMonths: a first month the data covers in part is no full month for a run-rate", () => {
  const cfm = cfmFactory();
  const months = (first: string, last: string) => monthsBetween(first, last).map((month) => cfm({ month }));
  it("data from the 12th of a month, then two whole months: two full months, and no run-rate", () => {
    const cf = months("2026-08", "2026-11");                             // November in progress
    expect(fullMonths(cf, "2026-11"), "the ETL's coverage keeps August").toHaveLength(3);
    expect(runRateMonths(cf, "2026-11", "2026-08-12").map((m) => m.month)).toEqual(["2026-09", "2026-10"]);
    expect(runRateWindow(cf, "2026-11", 12, "2026-08-12")).toBeNull();
  });
  it("the same months from the 1st: three full months, and the run-rate reads them", () => {
    const cf = months("2026-08", "2026-11");
    expect(runRateMonths(cf, "2026-11", "2026-08-01")).toHaveLength(3);
    expect(runRateWindow(cf, "2026-11", 12, "2026-08-01")).toEqual({ from: "2026-08", to: "2026-10", months: 3 });
  });
  it("an established household's trailing twelve is unchanged: its data begins long before its first full month", () => {
    const cf = monthsBetween("2023-01", "2026-06").map((month) => cfm({ month, in_coverage: month >= "2023-06" }));
    expect(runRateMonths(cf, "2026-06", "2023-01-17")).toEqual(fullMonths(cf, "2026-06"));
    expect(runRateWindow(cf, "2026-06", 12, "2023-01-17")).toEqual({ from: "2025-06", to: "2026-05", months: 12 });
    // with a history long enough, a partial first month is far outside the trailing twelve anyway
    expect(runRateWindow(months("2023-01", "2026-06"), "2026-06", 12, "2023-01-20")).toEqual({ from: "2025-06", to: "2026-05", months: 12 });
  });
  it("a first month whose data begins on the 1st, the 2nd or the 3rd is full; from the 4th it is not", () => {
    const cf = months("2026-08", "2026-11");
    for (const [day, have] of [["01", 3], ["02", 3], ["03", 3], ["04", 2]] as const) {
      expect(runRateMonths(cf, "2026-11", `2026-08-${day}`), `data from Aug ${day}`).toHaveLength(have);
    }
  });
  it("nextFullMonthEnds: the month in progress's last day, the next month's when it is a partial first month, none when "
    + "the data stops short of the ingest month", () => {
    const at = (txDates: string[], cf: CashflowMonth[], stamp: string) =>
      nextFullMonthEnds({ transactions: txDates.map((date) => tx({ date })), cashflow: cf, manifest: { generated_at: stamp } });
    // a month in progress after full ones: it ends on its own last day
    expect(at(["2026-04-01", "2026-06-04"], months("2026-04", "2026-06"), "2026-06-10T09:00:00-05:00")).toBe("2026-06-30");
    // a first month begun on the 29th, in progress: it will be no full month, so the next one is November
    expect(at(["2026-10-29"], months("2026-10", "2026-10"), "2026-10-31T18:00:00-04:00")).toBe("2026-11-30");
    // the same begun on the 2nd counts: it is the next one
    expect(at(["2026-10-02"], months("2026-10", "2026-10"), "2026-10-31T18:00:00-04:00")).toBe("2026-10-31");
    // data that ends in April with the ingest in June: April's end has passed, so no date is named
    expect(at(["2026-02-01", "2026-04-20"], months("2026-02", "2026-04"), "2026-06-10T09:00:00-05:00")).toBeNull();
    // no row at all: no month in progress
    expect(at([], [], "2026-06-10T09:00:00-05:00")).toBeNull();
  });
  it("dataBehindIngest: a whole month lies between the month in progress and the ingest month - the data is behind", () => {
    expect(dataBehindIngest("2026-04", "2026-06-10T09:00:00-05:00"), "data ends in April, ingest in June").toBe(true);
    expect(dataBehindIngest("2025-11", "2026-01-01T09:00:00-05:00"), "across a year: November, ingest in January").toBe(true);
    expect(dataBehindIngest("2026-05", "2026-06-01T09:00:00-05:00"),
      "the month just before the ingest month, on the 1st: last month's rows are still arriving").toBe(false);
    expect(dataBehindIngest("2025-12", "2026-01-02T09:00:00-05:00"), "the month just before, across a year").toBe(false);
    expect(dataBehindIngest("2026-06", "2026-06-10T09:00:00-05:00"), "the ingest month itself is filling in").toBe(false);
    expect(dataBehindIngest("2026-06", "2026-06-30T23:00:00-05:00"), "even on its last day").toBe(false);
    expect(dataBehindIngest(null, "2026-06-10T09:00:00-05:00"), "no month in progress").toBe(false);
    expect(dataBehindIngest("2026-04", "not a stamp"), "an unreadable stamp claims nothing").toBe(false);
  });
  it("no transaction (no first day): nothing is cut", () => {
    expect(runRateMonths(months("2026-02", "2026-06"), "2026-06", null)).toHaveLength(4);
  });
  it("dataFirstDay is the oldest row's date, or null", () => {
    expect(dataFirstDay([{ date: "2026-03-04" }, { date: "2026-01-12" }, { date: "2026-02-01" }])).toBe("2026-01-12");
    expect(dataFirstDay([])).toBeNull();
  });
});

describe("latestOnOrBefore", () => {
  const pts = [{ d: "2025-01-01" }, { d: "2025-03-01" }, { d: "2025-06-01" }];
  const key = (p: { d: string }) => p.d;
  it("picks the latest item at or before the date", () => {
    expect(latestOnOrBefore(pts, "2025-04-15", key)?.d).toBe("2025-03-01");
    expect(latestOnOrBefore(pts, "2025-03-01", key)?.d).toBe("2025-03-01");
    expect(latestOnOrBefore(pts, "2026-01-01", key)?.d).toBe("2025-06-01");
  });
  it("returns null when everything is after the date (or the list is empty)", () => {
    expect(latestOnOrBefore(pts, "2024-12-31", key)).toBeNull();
    expect(latestOnOrBefore([], "2025-01-01", key)).toBeNull();
  });
  it("equal-date ties keep the last item in input order (fresher source wins)", () => {
    const dup = [{ d: "2025-03-01", s: "csv" }, { d: "2025-03-01", s: "simplefin" }];
    expect(latestOnOrBefore(dup, "2025-03-01", (p) => p.d)?.s).toBe("simplefin");
  });
});

describe("ETL cross-check (real exports)", () => {
  requireExports();
  it.skipIf(!HAVE_EXPORTS)("client rollup === cashflow_monthly.json", () => {
    const rows: Transaction[] = readExport("transactions.json").transactions;
    const txns = partitionSplits(rows).transactions;     // the category view: a split parent is display-only, its parts carry the spend
    const months: { month: string; income: number; spend: number }[] = readExport("cashflow_monthly.json").months;
    // a household with no transactions has no months either: the loop below compares nothing, and this says so
    if (rows.length === 0) expect(months, "months of a household with no transactions").toEqual([]);
    for (const m of months) {
      const inMonth = txns.filter((t) => t.date.slice(0, 7) === m.month);
      expect(totalSpend(inMonth), `spend ${m.month}`).toBeCloseTo(m.spend, 2);
      expect(totalIncome(inMonth), `income ${m.month}`).toBeCloseTo(m.income, 2);
    }
  });
});

describe("currentYear", () => {
  // The snapshot lags the ingest across a month AND year boundary, so reading generated_at first changes the answer.
  const manifest = { current_snapshot_date: "2026-12-31", generated_at: "2027-01-01T03:00:00-06:00" };
  it("reads the year from the snapshot date, else the ingest time", () => {
    expect(currentYear(manifest)).toBe(2026);
    expect(currentYear({ ...manifest, current_snapshot_date: null })).toBe(2027);
  });
});

describe("monthInProgress takes a whole AppData", () => {
  it("compile-time: tsc fails if the narrowed parameter rejects it", () => {
    const fromAppData = (d: AppData): string | null => monthInProgress(d);
    expect(typeof fromAppData).toBe("function");
  });
});

// On a household's own exports (and every demo household's): the month in progress is never after the ingest month,
// every month after it holds only rows dated after the ingest day, and the full months end before it.
describe("the month in progress on the exports", () => {
  requireExports();
  it.skipIf(!HAVE_EXPORTS)("is at most the ingest month; a later month holds only rows dated ahead; no full month reaches it", () => {
    const rows: Transaction[] = partitionSplits(readExport("transactions.json").transactions).transactions;
    const cashflow: CashflowMonth[] = readExport("cashflow_monthly.json").months;
    const manifest = readExport("manifest.json");
    const inProgress = monthInProgress({ transactions: rows, cashflow, manifest });
    if (rows.length === 0) {
      expect(inProgress, "no row, no month in progress").toBeNull();
      return;
    }
    const ingestMonth = String(manifest.generated_at).slice(0, 7);
    expect(inProgress !== null && inProgress <= ingestMonth, "never after the ingest month").toBe(true);
    const later = rows.filter((t) => t.date.slice(0, 7) > inProgress!);
    expect(later.every((t) => datedAhead(t.date, manifest.generated_at)), "a later month's rows are all dated ahead").toBe(true);
    expect(countedRows(rows, inProgress).length, "only those are left out of the counted rows").toBe(rows.length - later.length);
    expect(fullMonths(cashflow, inProgress).every((m) => m.month < inProgress!), "no full month reaches it").toBe(true);
  });
});

describe("vendorStats over split purchases", () => {
  it("sums the parts and counts purchases: a split trip is one ticket, its parts never a cadence of 0 days", () => {
    const m = txFactory({ vendor: "Synth Mart" });
    const rows = [
      m({ date: "2026-01-05", amount: -210, parent_id: "p1", category: "Groceries" }),
      m({ date: "2026-01-05", amount: -90, parent_id: "p1", category: "Shopping" }),
      m({ date: "2026-01-19", amount: -100, parent_id: "p2", category: "Groceries" }),
      m({ date: "2026-02-02", amount: -50 }),                                            // an ordinary trip
    ];
    const [s] = vendorStats(rows);
    expect([s.vendor, s.total, s.count, s.avgTicket]).toEqual(["Synth Mart", 450, 3, 150]);
    expect(s.avgDaysBetween).toBe(14);
    // a category filter that keeps one part still counts the purchase once at the part's amount
    const [g] = vendorStats(rows.filter((t) => t.category === "Groceries"));
    expect([g.total, g.count]).toEqual([310, 2]);
  });
});

// the income types are one set (INCOME_TYPES, the ETL's models.INCOME_TYPES): a page that needs them imports it
describe("the income types are declared once", () => {
  it("no application file but lib/agg.ts spells the set out", () => {
    const root = join(dirname(fileURLToPath(import.meta.url)), "..");
    const files = (dir: string): string[] => readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
      e.isDirectory() ? (e.name === "test" ? [] : files(join(dir, e.name)))
      : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [join(dir, e.name)] : []);
    const spelled = files(root).filter((f) =>
      /\[\s*"income"\s*,\s*"dividend"\s*,\s*"interest"\s*\]/.test(withoutComments(readFileSync(f, "utf8"))));
    expect(spelled.map((f) => f.slice(root.length + 1))).toEqual(["lib/agg.ts"]);
  });
});

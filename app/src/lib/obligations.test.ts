// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import dayjs from "dayjs";
import { describe, expect, it } from "vitest";
import { HAVE_EXPORTS, readExport, requireExports } from "../test/exports";
import type { RecurringItem } from "../data/types";
import { recFactory } from "./__fixtures__";
import {
  expectedCharges, groupObligations, isLumpy, LUMPY_MIN, obligationMonths, obligationsByMonth, obligationsFrom,
  obligationsInMonths, upcomingObligations,
} from "./obligations";

const rec = recFactory();

describe("obligationMonths - the twelve months a next-12-months view covers", () => {
  it("the twelve months after the month in progress, the first the month after it, across a year end", () => {
    expect(obligationMonths("2026-10")).toEqual(["2026-11", "2026-12", "2027-01", "2027-02", "2027-03", "2027-04",
      "2027-05", "2027-06", "2027-07", "2027-08", "2027-09", "2027-10"]);
  });
  it("no month in progress (no month holds a row): no month", () => {
    expect(obligationMonths(null)).toEqual([]);
  });
});

describe("the next-12-months chart's bars", () => {
  const months = obligationMonths("2026-10");
  it("twelve bars, a month no bill falls in at zero; a monthly item once in every month, an annual one in its month", () => {
    const byM = obligationsByMonth(obligationsInMonths([
      rec({ vendor: "Mortgage", typical_amount: 2000, last_seen: "2026-10-01", next_expected: "2026-11-02" }),
      rec({ vendor: "Insurance", cadence: "annual", typical_amount: 1800, next_expected: "2027-03-15" }),
    ], months), months);
    expect(byM.map((m) => m.month)).toEqual(months);
    expect(byM.map((m) => m.total)).toEqual(months.map((m) => (m === "2027-03" ? 3800 : 2000)));
    expect(byM.map((m) => m.lumpyTotal)).toEqual(months.map((m) => (m === "2027-03" ? 1800 : 0)));
  });
  it("a bill expected in the month in progress is not in the first bar twice, and a late one is projected forward", () => {
    // expected 25 Oct (the month in progress), and one whose next date passed in September: once in each bar
    const obs = obligationsInMonths([rec({ vendor: "Now", next_expected: "2026-10-25" }),
      rec({ vendor: "Late", next_expected: "2026-09-05" })], months);
    expect(obs.filter((o) => o.vendor === "Now").map((o) => o.date.slice(0, 7))).toEqual(months);
    expect(obs.filter((o) => o.vendor === "Late").map((o) => o.date.slice(0, 7))).toEqual(months);
  });
  it("a month-end bill steps from its own date each time, so the clamp never drifts", () => {
    const dates = obligationsInMonths([rec({ next_expected: "2027-01-31" })], obligationMonths("2026-12", 4))
      .map((o) => o.date);
    expect(dates).toEqual(["2027-01-31", "2027-02-28", "2027-03-31", "2027-04-30"]);
  });
  it("the exported dates first, then a monthly item from the one with the latest day: no month twice, the 31st kept", () => {
    expect(expectedCharges(rec({ expected_dates: ["2026-11-30", "2026-12-31", "2027-01-31"] }), "2027-04-30"))
      .toEqual(["2026-11-30", "2026-12-31", "2027-01-31", "2027-02-28", "2027-03-31", "2027-04-30", "2027-05-31"]);
    // a first-business-day bill's dates (2 Nov, 1 Dec, 1 Jan): the months after them, once each
    expect(expectedCharges(rec({ expected_dates: ["2026-11-02", "2026-12-01", "2027-01-01"] }), "2027-03-31")
      .map((d) => d.slice(0, 7))).toEqual(["2026-11", "2026-12", "2027-01", "2027-02", "2027-03", "2027-04"]);
  });
  // the ETL exports a monthly item's next thirteen dates (etl/enrich/recurring.py MONTHLY_EXPECTED_DATES), each on its
  // own rule: the twelve months after the month in progress read them as they are, never a stepped one
  it("a monthly item's exported year of dates is read as it is: a last-business-day bill never lands on a weekend", () => {
    const lbd = ["2026-11-30", "2026-12-31", "2027-01-29", "2027-02-26", "2027-03-31", "2027-04-30", "2027-05-31",
      "2027-06-30", "2027-07-30", "2027-08-31", "2027-09-30", "2027-10-29", "2027-11-30"];
    const months = obligationMonths("2026-11");
    const dates = obligationsInMonths([rec({ expected_dates: lbd })], months).map((o) => o.date);
    expect(dates).toEqual(lbd.slice(1));
    for (const d of dates) expect([0, 6], d).not.toContain(dayjs(d).day());
  });
  it("a weekly or biweekly item continues by its median gap, as the ETL steps it; with none stated, by 7 or 14 days", () => {
    expect(expectedCharges(rec({ cadence: "biweekly", expected_dates: ["2026-07-03", "2026-07-17", "2026-07-31"],
      median_gap_days: 14 }), "2026-08-31")).toEqual(["2026-07-03", "2026-07-17", "2026-07-31", "2026-08-14", "2026-08-28",
      "2026-09-11"]);
    expect(expectedCharges(rec({ cadence: "weekly", expected_dates: ["2026-07-03"], median_gap_days: 0 }), "2026-07-20"))
      .toEqual(["2026-07-03", "2026-07-10", "2026-07-17", "2026-07-24"]);
  });
  it("a charge outside the months is in no bar", () => {
    expect(obligationsByMonth([{ vendor: "V", category: null, date: "2028-01-05", amount: 9, cadence: "annual", lumpy: true }],
      months).every((m) => m.total === 0)).toBe(true);
  });
});

describe("obligationsFrom - the day the forward window starts", () => {
  it("the newest snapshot's date, else the newest transaction's", () => {
    expect(obligationsFrom("2026-07-01", "2026-07-20")).toBe("2026-07-01");
    expect(obligationsFrom(null, "2026-07-20")).toBe("2026-07-20");
    expect(obligationsFrom("2026-07-01", null)).toBe("2026-07-01");
  });
  it("with neither there is nothing to roll forward from: null", () => {
    expect(obligationsFrom(null, null)).toBeNull();
  });
});

describe("upcomingObligations", () => {
  it("rolls monthly and annual charges forward within the horizon", () => {
    const obs = upcomingObligations([
      rec({}),
      rec({ vendor: "Insurance", cadence: "annual", typical_amount: 1800, next_expected: "2026-09-15" }),
      rec({ vendor: "Lapsed", active: false }),
    ], "2026-06-12", 12);
    expect(obs.filter((o) => o.vendor === "Netflix")).toHaveLength(12);
    const ins = obs.filter((o) => o.vendor === "Insurance");
    expect(ins).toHaveLength(1);
    expect(ins[0].lumpy).toBe(true);
    expect(obs.some((o) => o.vendor === "Lapsed")).toBe(false);
  });
  it("skips overdue occurrences instead of projecting the past", () => {
    const obs = upcomingObligations([rec({ next_expected: "2026-04-05" })], "2026-06-12", 3);
    expect(obs[0].date >= "2026-06-12").toBe(true);
  });
  it("groups by month with lumpy detail", () => {
    const months = obligationMonths("2026-06");
    const byM = obligationsByMonth(obligationsInMonths(
      [rec({ vendor: "Insurance", cadence: "annual", typical_amount: 1800, next_expected: "2026-09-15" })], months), months);
    expect(byM).toHaveLength(12);
    expect(byM.filter((m) => m.total > 0).map((m) => m.month)).toEqual(["2026-09"]);
    expect(byM.find((m) => m.month === "2026-09")!.lumpy[0].vendor).toBe("Insurance");
  });
});

describe("isLumpy - what is worth seeing coming", () => {
  it("a big charge that recurs weekly, biweekly or monthly is routine, however large (the biweekly mortgage)", () => {
    for (const cadence of ["weekly", "biweekly", "monthly"] as const) {
      expect(isLumpy(cadence, 1500), `${cadence} $1,500`).toBe(false);
      expect(isLumpy(cadence, LUMPY_MIN), `${cadence} at the threshold`).toBe(false);
    }
  });
  it("a renewal is lumpy at any size: annual, quarterly - and a semiannual one if the ETL ever emits it", () => {
    expect(isLumpy("annual", 18)).toBe(true);
    expect(isLumpy("quarterly", 40)).toBe(true);
    expect(isLumpy("semiannual", 40)).toBe(true);
  });
  it("a big charge on a cadence that is not weekly / biweekly / monthly is lumpy from the threshold up", () => {
    expect(isLumpy("every-6-weeks", LUMPY_MIN)).toBe(true);
    expect(isLumpy("every-6-weeks", LUMPY_MIN - 1)).toBe(false);
  });
  it("upcomingObligations flags with it: the projected biweekly mortgage is never lumpy, a $18 annual bill is", () => {
    const obs = upcomingObligations([
      rec({ vendor: "Home loan", cadence: "biweekly", typical_amount: 1500, next_expected: "2026-07-03", median_gap_days: 14 }),
      rec({ vendor: "Domain", cadence: "annual", typical_amount: 18, next_expected: "2026-08-15" }),
    ], "2026-07-01", 12);
    expect(obs.filter((o) => o.vendor === "Home loan").length).toBeGreaterThan(20);
    expect(obs.filter((o) => o.vendor === "Home loan").some((o) => o.lumpy)).toBe(false);
    expect(obs.find((o) => o.vendor === "Domain")!.lumpy).toBe(true);
    const byM = obligationsByMonth(obs, obligationMonths("2026-06"));
    expect(byM.flatMap((m) => m.lumpy).map((o) => o.vendor)).toEqual(["Domain"]);
  });
});

describe("groupObligations - one entry per vendor and amount", () => {
  it("collapses repeats to a count and keeps the first date", () => {
    const obs = upcomingObligations([
      rec({ vendor: "Property tax", cadence: "quarterly", typical_amount: 900, next_expected: "2026-07-01" }),
      rec({ vendor: "Insurance", cadence: "annual", typical_amount: 1800, next_expected: "2026-08-10" }),
    ], "2026-07-01", 3);
    const groups = groupObligations(obs.filter((o) => o.lumpy));
    expect(groups.map((g) => [g.vendor, g.count, g.date])).toEqual([
      ["Property tax", 2, "2026-07-01"], ["Insurance", 1, "2026-08-10"]]);
  });
  it("different amounts from one vendor stay separate lines (no pretending they are the same charge)", () => {
    const o = (amount: number, date: string) => ({ vendor: "V", category: null, date, amount, cadence: "annual" as const, lumpy: true });
    expect(groupObligations([o(100, "2026-07-01"), o(120, "2026-08-01"), o(100, "2026-09-01")])
      .map((g) => [g.amount, g.count])).toEqual([[100, 2], [120, 1]]);
  });
});

// On a household's own exports: every active monthly item carries a year of dates, and the next-12-months window after
// the ingest month holds exactly the exported dates that fall in it (none stepped by the dashboard)
describe("the exported expected dates reach the next-12-months window", () => {
  requireExports();
  it.skipIf(!HAVE_EXPORTS)("each active monthly item's charges in the window are its exported dates there", () => {
    const items: RecurringItem[] = readExport("recurring.json").items;
    const months = obligationMonths(readExport("manifest.json").generated_at.slice(0, 7));
    const [from, to] = [`${months[0]}-01`, dayjs(`${months[11]}-01`).endOf("month").format("YYYY-MM-DD")];
    // a household with no monthly bill (the demo's tiny one) has nothing to check here
    const monthly = items.filter((r) => r.active && r.cadence === "monthly");
    expect(monthly.every((r) => r.expected_dates.length === 13), "a year of dates each").toBe(true);
    // a bill already late (its next date before the month in progress) runs past its exported dates in the last month
    for (const r of monthly.filter((m) => m.expected_dates[12].slice(0, 7) >= months[11])) {
      const charges = obligationsInMonths([r], months).map((o) => o.date);
      expect(charges.join() === r.expected_dates.filter((d) => d >= from && d <= to).join(), "the exported dates")
        .toBe(true);
    }
  });
});

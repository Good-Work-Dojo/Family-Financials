// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { CashflowMonth } from "../data/types";
import { cfmFactory, recFactory, txFactory } from "./__fixtures__";
import { chargeDates, forecastCashflow, scheduledHistory } from "./forecast";
import { obligationsByMonth, obligationsInMonths } from "./obligations";

const cfm = cfmFactory();

// 18 full months Jan-2025..Jun-2026 with a December spike, plus a partial month
const HIST: CashflowMonth[] = [
  ...Array.from({ length: 18 }, (_, i) => {
    const y = 2025 + Math.floor(i / 12);
    const m = (i % 12) + 1;
    const month = `${y}-${String(m).padStart(2, "0")}`;
    return cfm({ month, spend: m === 12 ? 28000 : 14000 });
  }),
  cfm({ month: "2026-07", spend: 300 }),   // the month in progress, must be dropped
];
const IN_PROGRESS = "2026-07";

describe("forecastCashflow", () => {
  it("projects the horizon with December seasonality and a widening band", () => {
    const f = forecastCashflow({ cashflow: HIST, inProgress: IN_PROGRESS, firstDay: null, startCash: 50000 })!;
    expect(f.months).toHaveLength(12);
    expect(f.months[0].month).toBe("2026-07");
    const dec = f.months.find((m) => m.month === "2026-12")!;
    const nov = f.months.find((m) => m.month === "2026-11")!;
    expect(dec.spend).toBeGreaterThan(nov.spend * 1.3);   // December effect
    expect(f.months[11].high - f.months[11].low)
      .toBeGreaterThan(f.months[0].high - f.months[0].low);
  });
  it("flags a floor breach conservatively (low band)", () => {
    const draining = HIST.map((m) => cfm({ ...m, income: 10000 }));  // net −4k/mo
    const f = forecastCashflow({ cashflow: draining, inProgress: IN_PROGRESS, firstDay: null, startCash: 30000, floor: 20000 })!;
    expect(f.floorBreachMonth).not.toBeNull();
  });
  it("returns null on insufficient history", () => {
    expect(forecastCashflow({ cashflow: HIST.slice(0, 4), inProgress: "2025-04", firstDay: null, startCash: 0 })).toBeNull();
    expect(forecastCashflow({ cashflow: HIST, inProgress: null, firstDay: null, startCash: 0 }), "no month in progress: no row at all")
      .toBeNull();
  });
  it("says how many full months the spend baseline averages: twelve, or the six to eleven there are", () => {
    expect(forecastCashflow({ cashflow: HIST, inProgress: IN_PROGRESS, firstDay: null, startCash: 0 })!.spendMonths).toBe(12);
    expect(forecastCashflow({ cashflow: HIST.slice(0, 9), inProgress: "2025-09", firstDay: null, startCash: 0 })!.spendMonths).toBe(8);
  });
  it("reads the planning run-rates' months: a first month the data covers in part is none of its history", () => {
    // seven months in coverage before the month in progress, the first begun on the 12th: six full months for a forecast
    const seven = HIST.slice(0, 8);                                   // 2025-01..2025-08, August in progress
    const f = forecastCashflow({ cashflow: seven, inProgress: "2025-08", firstDay: "2025-01-12", startCash: 0 })!;
    expect(f.spendMonths, "January is left out").toBe(6);
    // six months in coverage, the first begun on the 12th: five full months, no forecast (the page's line says 5)
    expect(forecastCashflow({ cashflow: HIST.slice(0, 7), inProgress: "2025-07", firstDay: "2025-01-12", startCash: 0 })).toBeNull();
    expect(forecastCashflow({ cashflow: HIST.slice(0, 7), inProgress: "2025-07", firstDay: "2025-01-02", startCash: 0 }),
      "the same months from the 2nd: six full months").not.toBeNull();
  });
  it("reads the full months before the month in progress: a month dated ahead moves nothing", () => {
    const ahead = [...HIST, cfm({ month: "2026-08", spend: 90_000 })];      // one row the bank dated into August
    const f = forecastCashflow({ cashflow: ahead, inProgress: IN_PROGRESS, firstDay: null, startCash: 50000 })!;
    expect(f).toEqual(forecastCashflow({ cashflow: HIST, inProgress: IN_PROGRESS, firstDay: null, startCash: 50000 }));
    expect(f.months[0].month, "the forecast starts with the month in progress").toBe(IN_PROGRESS);
  });
});

// 18 flat full months (seasonality exactly 1) so the arithmetic below is exact
const FLAT: CashflowMonth[] = [
  ...Array.from({ length: 18 }, (_, i) => cfm({ month: `${2025 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}`, spend: 14000 })),
  cfm({ month: "2026-07", spend: 300 }),
];

describe("schedules in the forecast", () => {
  const rec = recFactory({ vendor: "Gym", account_id: "a", typical_amount: 1000, schedule: true,
    last_seen: "2026-06-05", expected_dates: ["2026-07-05", "2026-08-05", "2026-09-05"], median_gap_days: 31 });
  const tx = txFactory({ vendor: "Gym", account: "a", amount: -1000 });
  const gymRows = FLAT.map((m) => tx({ date: `${m.month}-05` }));      // the schedule's own history, every month

  it("counts a schedule's spend once: removed from the baseline, added back at its expected dates", () => {
    const plain = forecastCashflow({ cashflow: FLAT, inProgress: IN_PROGRESS, firstDay: null, startCash: 50000 })!;
    const f = forecastCashflow({ cashflow: FLAT, inProgress: IN_PROGRESS, firstDay: null, startCash: 50000, schedules: [rec()], transactions: gymRows })!;
    expect(plain.baselineSpend).toBeCloseTo(14000, 6);
    expect(f.baselineSpend).toBeCloseTo(13000, 6);                        // the gym's $1,000 left the baseline
    expect(f.months).toHaveLength(12);
    for (const m of f.months) {
      expect(m.scheduled).toBe(1000);                                     // ...and comes back every month, rolled forward by median_gap_days
      expect(m.spend).toBeCloseTo(14000, 6);                              // not 15000: never double counted
    }
    expect(f.netStdev).toBeCloseTo(plain.netStdev, 6);                   // the band is the baseline's
    expect(plain.months.every((m) => m.scheduled === 0)).toBe(true);
  });
  it("ignores recurring items that were not promoted", () => {
    const f = forecastCashflow({ cashflow: FLAT, inProgress: IN_PROGRESS, firstDay: null, startCash: 50000, schedules: [rec({ schedule: false })], transactions: gymRows })!;
    expect(f.baselineSpend).toBeCloseTo(14000, 6);
    expect(f.months.every((m) => m.scheduled === 0)).toBe(true);
  });
});

describe("calendar-aware roll-forward (amends plan decision 11)", () => {
  const MONTHS = Array.from({ length: 12 }, (_, i) => `${2026 + Math.floor((i + 6) / 12)}-${String(((i + 6) % 12) + 1).padStart(2, "0")}`);  // 2026-07 .. 2027-06
  const gym = recFactory({ vendor: "Gym", account_id: "a", typical_amount: 1000, schedule: true, cadence: "monthly" });
  const scheduledBy = (r: ReturnType<typeof gym>) =>
    forecastCashflow({ cashflow: FLAT, inProgress: IN_PROGRESS, firstDay: null, startCash: 50000, schedules: [r] })!.months.map((m) => m.scheduled);

  it.each([
    ["the 31st", ["2026-06-30", "2026-07-31", "2026-08-31"]],
    ["the 30th", ["2026-06-30", "2026-07-30", "2026-08-30"]],
    ["the 5th", ["2026-07-05", "2026-08-05", "2026-09-05"]],
    ["the first business day", ["2026-07-01", "2026-08-03", "2026-09-01"]],
  ])("a monthly schedule on %s: its exported dates, then exactly one charge in each of the 12 months", (_, expected_dates) => {
    expect(scheduledBy(gym({ last_seen: "2026-06-01", expected_dates }))).toEqual(MONTHS.map(() => 1000));
  });
  it("continues from the exported date with the latest day, so a clamped month end never becomes the day", () => {
    const dates = chargeDates(gym({ expected_dates: ["2026-02-28", "2026-03-31", "2026-04-30"] }), "2026-08");
    expect(dates).toEqual(["2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31", "2026-06-30", "2026-07-31", "2026-08-31",
      "2026-09-30"]);                                                          // 31 again after April, not 30 -> 30 -> 30
    const f = forecastCashflow({ cashflow: FLAT, inProgress: IN_PROGRESS, firstDay: null, startCash: 0, schedules: [gym({ last_seen: "2026-05-31", expected_dates: ["2026-06-30"] })] })!;
    expect(f.months.map((m) => m.scheduled)).toEqual(MONTHS.map(() => 1000));
  });
  it("a payment a day early: the forecast and the bills chart put the charge in the same months", () => {
    // due on the 1st, paid on 30 June: July's payment is made, so the export expects August's first (on the 3rd)
    const early = gym({ last_seen: "2026-06-30", expected_dates: ["2026-08-03", "2026-09-01", "2026-10-01"] });
    const bills = obligationsByMonth(obligationsInMonths([early], MONTHS), MONTHS).map((m) => m.total);
    expect(scheduledBy(early)).toEqual(bills);
    expect(bills).toEqual(MONTHS.map((m) => (m === "2026-07" ? 0 : 1000)));    // nothing in July, once in every month after
  });
  it("quarterly: one charge every third month; annual: one charge in the horizon", () => {
    const q = scheduledBy(gym({ cadence: "quarterly", last_seen: "2026-05-15", median_gap_days: 91, expected_dates: ["2026-08-14"] }));
    expect(q).toEqual(MONTHS.map((m) => (["2026-08", "2026-11", "2027-02", "2027-05"].includes(m) ? 1000 : 0)));
    const a = scheduledBy(gym({ cadence: "annual", last_seen: "2025-09-10", median_gap_days: 365, expected_dates: ["2026-09-10"] }));
    expect(a).toEqual(MONTHS.map((m) => (m === "2026-09" ? 1000 : 0)));
    // a leap-day anchor lands on Feb 28 in common years instead of skipping or drifting
    const leap = scheduledBy(gym({ cadence: "annual", last_seen: "2024-02-29", median_gap_days: 365, expected_dates: ["2025-02-28"] }));
    expect(leap).toEqual(MONTHS.map((m) => (m === "2027-02" ? 1000 : 0)));
  });
  it("weekly / biweekly keep the day-gap step (exact for them)", () => {
    const b = scheduledBy(gym({ cadence: "biweekly", last_seen: "2026-06-19", median_gap_days: 14,
      expected_dates: ["2026-07-03", "2026-07-17", "2026-07-31"] }));
    expect(b.reduce((s, x) => s + x, 0) / 1000).toBe(26);                           // 2026-07-03 + 14k through 2027-06-30
  });
  it("a row with no expected dates contributes nothing, whatever its cadence", () => {
    expect(scheduledBy(gym({ expected_dates: [] })).every((x) => x === 0)).toBe(true);
  });
});

// The starting balance already holds every day up to its date, so the first projected month adds only the days after
// the balance's date: the month in progress (July 2026, 31 days) prorated, or the next month whole when none is left.
describe("the first month is the rest of the month the starting balance is dated in", () => {
  const NET = 20000 - 14000;                                              // FLAT's income less its spend, a whole month
  const at = (asOf: string | null, extra: Partial<Parameters<typeof forecastCashflow>[0]> = {}) =>
    forecastCashflow({ cashflow: FLAT, inProgress: IN_PROGRESS, firstDay: null, startCash: 50000, asOf, ...extra })!;

  it("a balance of the month's first day: the 30 days after it, 30/31 of a month", () => {
    const f = at("2026-07-01");
    expect(f.partialFirst).toEqual({ month: "2026-07", asOf: "2026-07-01", days: 30, of: 31 });
    expect([f.months[0].month, f.months[0].days]).toEqual(["2026-07", 30]);
    expect(f.months[0].net).toBeCloseTo(NET * 30 / 31, 6);
    expect(f.months[0].cumCash).toBeCloseTo(50000 + NET * 30 / 31, 6);
    expect(f.months[1].net, "the months after it are whole").toBeCloseTo(NET, 6);
    expect(f.months).toHaveLength(12);
  });
  it("a balance of mid-month: the 16 days after the 15th", () => {
    const f = at("2026-07-15");
    expect(f.partialFirst?.days).toBe(16);
    expect(f.months[0].income).toBeCloseTo(20000 * 16 / 31, 6);
    expect(f.months[0].spend).toBeCloseTo(14000 * 16 / 31, 6);
    expect(f.months[0].cumCash).toBeCloseTo(50000 + NET * 16 / 31, 6);
  });
  it("a balance of the month's last day: nothing of it is left, so the forecast starts with the next month, whole", () => {
    const f = at("2026-07-31");
    expect(f.partialFirst).toBeNull();
    expect(f.months.map((m) => m.month)).toEqual([
      "2026-08", "2026-09", "2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03", "2027-04", "2027-05",
      "2027-06", "2027-07"]);
    expect(f.months[0].cumCash).toBeCloseTo(50000 + NET, 6);
    expect(f.months[0].days).toBe(31);
  });
  it("the control: with no balance date every month is whole and the first is the month in progress", () => {
    const f = at(null);
    expect([f.partialFirst, f.months[0].month, f.months[0].days]).toEqual([null, "2026-07", 31]);
    expect(f.months[0].cumCash).toBeCloseTo(50000 + NET, 6);
  });
  it("a scheduled charge on or before the balance's date is in that balance already; one after it is counted", () => {
    const gym = recFactory({ vendor: "Gym", account_id: "a", typical_amount: 1000, schedule: true, cadence: "monthly",
      last_seen: "2026-06-10", expected_dates: ["2026-07-10"] });
    expect(at("2026-07-15", { schedules: [gym()] }).months[0].scheduled, "the 10th is before the 15th").toBe(0);
    expect(at("2026-07-05", { schedules: [gym()] }).months[0].scheduled, "the 10th is after the 5th").toBe(1000);
  });
  it("the band widens from the share of a month the first point covers", () => {
    const varied = FLAT.map((m, i) => cfm({ ...m, income: 20000 + (i % 2 ? 500 : -500) }));
    const f = forecastCashflow({ cashflow: varied, inProgress: IN_PROGRESS, firstDay: null, startCash: 0, asOf: "2026-07-15" })!;
    expect(f.months[0].high - f.months[0].low).toBeCloseTo(2 * f.netStdev * Math.sqrt(16 / 31), 6);
    expect(f.months[1].high - f.months[1].low).toBeCloseTo(2 * f.netStdev * Math.sqrt(1 + 16 / 31), 6);
  });
  it("a balance dated a month after the history's next one starts the forecast there, never in a month already past", () => {
    // full months end in May; the balance is of July 15 (June held no row): June is behind today's cash already
    const gapped = FLAT.filter((m) => m.month !== "2026-06");
    const f = forecastCashflow({ cashflow: gapped, inProgress: IN_PROGRESS, firstDay: null, startCash: 0, asOf: "2026-07-15" })!;
    expect(f.months[0].month).toBe("2026-07");
    expect(f.partialFirst?.days).toBe(16);
  });
});

describe("scheduledHistory over split purchases", () => {
  it("the parts of a scheduled vendor's purchase sum to the purchase: category view and vendor view agree", () => {
    const rec = recFactory();
    const box = txFactory({ vendor: "Netflix", account: "demo_checking_0101", date: "2026-05-05" });
    const parent = box({ id: "p", amount: -20, is_parent: true });
    const parts = [box({ id: "a", amount: -14, parent_id: "p" }), box({ id: "b", amount: -6, parent_id: "p" })];
    const schedules = [rec({ vendor: "Netflix", account_id: "demo_checking_0101", schedule: true })];
    expect(scheduledHistory(schedules, parts)).toEqual(new Map([["2026-05", 20]]));
    expect(scheduledHistory(schedules, [parent])).toEqual(new Map([["2026-05", 20]]));
  });
});

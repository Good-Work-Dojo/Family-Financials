// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { txFactory } from "./__fixtures__";
import { lastCompleteDay, median, monthToDate, MTD_PRIOR_MONTHS, ordinal, paceOf, paceSentence } from "./monthToDate";

// synthetic rows only; the floor is passed explicitly so each case says what it relies on (FLOOR: one before every
// month the cases compare, so it cuts nothing)
const tx = txFactory({ category: "Dining", amount: -10 });
const FLOOR = "2025-01";
/** one spend row per (date, amount) */
const spends = (rows: [string, number][], over = {}) => rows.map(([date, amount]) => tx({ date, amount, ...over }));
/** a row that is not spend, so a month counts as covered (alive) without adding spend. A case also uses one to start the
 *  data before its first compared month, which is then covered whole */
const alive = (date: string) => tx({ date, amount: 500, type: "income", category: "Income" });

describe("median", () => {
  it("odd and even counts, unsorted input, none", () => {
    expect(median([3, 1, 2])).toBe(2);
    expect(median([4, 1, 3, 2])).toBe(2.5);
    expect(median([])).toBeNull();
  });
});

describe("lastCompleteDay", () => {
  it("the newest transaction date when the ingest ran on a later day", () => {
    expect(lastCompleteDay("2026-10-04", "2026-10-05T11:19:37-05:00")).toBe("2026-10-04");
  });
  it("the day before when the newest data IS the ingest day (still coming in)", () => {
    expect(lastCompleteDay("2026-10-05", "2026-10-05T11:19:37-05:00")).toBe("2026-10-04");
  });
  it("a row dated after the ingest day is clamped to it first, then the ingest day is cut", () => {
    expect(lastCompleteDay("2026-10-09", "2026-10-05T11:19:37-05:00")).toBe("2026-10-04");
  });
  it("on the 1st it is the last day of the month before (and of a year before, in January)", () => {
    expect(lastCompleteDay("2026-10-01", "2026-10-01T08:00:00-05:00")).toBe("2026-09-30");
    expect(lastCompleteDay("2026-03-01", "2026-03-01T08:00:00-06:00")).toBe("2026-02-28");
    expect(lastCompleteDay("2027-01-01", "2027-01-01T08:00:00-06:00")).toBe("2026-12-31");
  });
  it("with no transaction there is no complete day of data, whatever day the ingest ran", () => {
    expect(lastCompleteDay(null, "2026-10-05T11:19:37-05:00")).toBeNull();
  });
});

describe("paceOf", () => {
  it("about within 10% either way, above / below past it, null without a ratio", () => {
    expect(paceOf(0)).toBe("about");
    expect(paceOf(0.0999)).toBe("about");
    expect(paceOf(-0.0999)).toBe("about");
    expect(paceOf(0.1)).toBe("above");
    expect(paceOf(-0.1)).toBe("below");
    expect(paceOf(null)).toBeNull();
  });
});

describe("monthToDate", () => {
  // the data starts in March; Apr..Sep 2026 each spend 100 on the 2nd and 900 on the 20th; Oct spends 50 on the 1st and
  // 70 on the 4th
  const sixMonths = [alive("2026-03-15"), ...["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
    .flatMap((m) => spends([[`${m}-02`, -100], [`${m}-20`, -900]]))];
  const october = spends([["2026-10-01", -50], ["2026-10-04", -70]]);

  it("spends the current month through the data's last day, against the median of the six months before by the same day", () => {
    const r = monthToDate([...sixMonths, ...october], "2026-10-04", FLOOR);
    expect(r.month).toBe("2026-10");
    expect(r.day).toBe(4);
    expect(r.spend).toBe(120);
    expect(r.priors.map((p) => p.month)).toEqual(["2026-09", "2026-08", "2026-07", "2026-06", "2026-05", "2026-04"]);
    expect(r.priors.every((p) => p.days === 4 && p.spend === 100), "each prior month cut at the 4th: the 20th is not counted").toBe(true);
    expect(r.typical).toBe(100);
    expect(r.ratio).toBeCloseTo(0.2, 10);
    expect(r.pace).toBe("above");
    expect(r.early).toBe(true);
    expect(MTD_PRIOR_MONTHS).toBe(6);
  });

  it("is the MEDIAN month, not the mean: one huge early bill does not move the typical pace", () => {
    const rows = [...sixMonths, ...spends([["2026-09-03", -5000]]), ...spends([["2026-10-03", -105]])];
    const r = monthToDate(rows, "2026-10-04", FLOOR);
    expect(r.priors.find((p) => p.month === "2026-09")!.spend).toBe(5100);
    expect(r.typical, "median of five 100s and one 5100").toBe(100);
    expect(r.pace, "105 against 100 is about typical (against the mean of 933 it would read far below)").toBe("about");
  });

  it("an even count of months takes the mean of the two middle ones", () => {
    const rows = [alive("2026-05-15"), ...spends([["2026-07-02", -100], ["2026-08-02", -200], ["2026-09-02", -400], ["2026-06-02", -300]]),
      ...spends([["2026-10-02", -250]])];
    const r = monthToDate(rows, "2026-10-04", "2026-06");
    expect(r.priors).toHaveLength(4);
    expect(r.typical).toBe(250);
    expect(r.pace).toBe("about");
  });

  it("month boundaries: the prior month's day N counts and day N+1 does not; the current month starts on the 1st", () => {
    const rows = [alive("2026-08-15"),
      ...spends([["2026-09-04", -40], ["2026-09-05", -1000], ["2026-09-30", -1000]]),
      ...spends([["2026-10-01", -10], ["2026-10-04", -20]]),
    ];
    const r = monthToDate(rows, "2026-10-04", "2026-09");
    expect(r.priors).toEqual([{ month: "2026-09", days: 4, spend: 40 }]);
    expect(r.spend, "the last day of September is not October's").toBe(30);
  });

  it("a 31st set against a 30-day and a 28-day month counts those months whole", () => {
    const rows = [alive("2025-12-15"),
      ...spends([["2026-01-31", -31], ["2026-02-28", -28], ["2026-02-01", -1]]),
      ...spends([["2026-03-31", -5]]),
    ];
    const mar = monthToDate(rows, "2026-03-31", "2026-01");
    expect(mar.priors).toEqual([{ month: "2026-02", days: 28, spend: 29 }, { month: "2026-01", days: 31, spend: 31 }]);
    expect(mar.early).toBe(false);
    const oct = monthToDate([alive("2026-08-15"), ...spends([["2026-09-30", -30], ["2026-10-31", -1]])], "2026-10-31", "2026-09");
    expect(oct.priors).toEqual([{ month: "2026-09", days: 30, spend: 30 }]);
  });

  it("data ending on the 1st: one day of spending, against each prior month's 1st alone, and flagged early", () => {
    const rows = [...spends([["2026-09-01", -60], ["2026-09-02", -999], ["2026-08-01", -40], ["2026-07-01", -50]]),
      ...spends([["2026-10-01", -50]])];
    const r = monthToDate(rows, "2026-10-01", "2026-07");
    expect(r.day).toBe(1);
    expect(r.early).toBe(true);
    expect(r.spend).toBe(50);
    expect(r.priors.map((p) => [p.days, p.spend])).toEqual([[1, 60], [1, 40], [1, 50]]);
    expect(r.typical).toBe(50);
    expect(monthToDate(rows, "2026-10-06", "2026-07").early, "the 6th is still early").toBe(true);
    expect(monthToDate(rows, "2026-10-07", "2026-07").early, "a week in, not early").toBe(false);
  });

  it("a month with no spend: this month reads 0 with no categories, and a covered prior month with none is a real 0", () => {
    const rows = [alive("2026-06-15"), alive("2026-09-02"), ...spends([["2026-08-02", -100], ["2026-07-02", -50]]), alive("2026-10-02")];
    const r = monthToDate(rows, "2026-10-04", "2026-07");
    expect(r.spend).toBe(0);
    expect(r.top).toEqual([]);
    expect(r.priors).toEqual([{ month: "2026-09", days: 4, spend: 0 }, { month: "2026-08", days: 4, spend: 100 },
      { month: "2026-07", days: 4, spend: 50 }]);
    expect(r.typical).toBe(50);
    expect(r.pace).toBe("below");
  });

  it("a prior month with no transaction at all is a gap in the feeds, not a month of zero spending", () => {
    const rows = [alive("2026-07-15"), ...spends([["2026-08-02", -100], ["2026-10-02", -100]])];       // nothing in September
    const r = monthToDate(rows, "2026-10-04", "2026-08");
    expect(r.priors.map((p) => p.month)).toEqual(["2026-08"]);
    expect(r.typical, "one month is no typical pace").toBeNull();
  });

  it("a typical pace needs three earlier months: with one or two there is none, and the line says how many there are", () => {
    // the data begins on Jun 1: June, July, August and September are full months before October
    const months = (n: number) => [...["2026-09", "2026-08", "2026-07", "2026-06"].slice(0, n).flatMap((m) => spends([[`${m}-02`, -100]])),
      alive("2026-06-01"), ...spends([["2026-10-02", -114]])];
    const floor = (n: number) => ["2026-09", "2026-08", "2026-07", "2026-06"][n - 1];
    for (const n of [1, 2]) {
      const r = monthToDate(months(n), "2026-10-04", floor(n));
      expect(r.priors, `${n}`).toHaveLength(n);
      expect([r.typical, r.ratio, r.pace], `${n}`).toEqual([null, null, null]);
      expect(paceSentence(r)).toBe(`Only ${n} earlier month${n === 1 ? "" : "s"} to compare with yet: a typical pace needs 3.`);
    }
    const three = monthToDate(months(3), "2026-10-04", floor(3));
    expect([three.typical, three.pace]).toEqual([100, "above"]);
    expect(paceSentence(three)).toBe("14% above typical so far.");
  });

  it("fewer than six prior months: the coverage floor and the start of the data cut the list, and none leaves no comparison", () => {
    const rows = [...sixMonths, ...october];
    expect(monthToDate(rows, "2026-10-04", "2026-07").priors.map((p) => p.month), "the floor").toEqual(["2026-09", "2026-08", "2026-07"]);
    // data that starts on 2026-06-15: June is covered only in part, so the compared months are July..September
    const late = [...spends([["2026-06-15", -100]]), ...rows.filter((t) => t.date >= "2026-07")];
    expect(monthToDate(late, "2026-10-04", FLOOR).priors.map((p) => p.month), "a partial first month").toEqual(["2026-09", "2026-08", "2026-07"]);
    // data that starts on 2026-06-04, past the 3rd: June is covered only in part too
    const fourth = [...spends([["2026-06-04", -100]]), ...rows.filter((t) => t.date >= "2026-07")];
    expect(monthToDate(fourth, "2026-10-04", FLOOR).priors.map((p) => p.month)).toEqual(["2026-09", "2026-08", "2026-07"]);
    const none = monthToDate(october, "2026-10-04", FLOOR);
    expect(none.priors).toEqual([]);
    expect(none.typical).toBeNull();
    expect(none.ratio).toBeNull();
    expect(none.pace).toBeNull();
    expect(none.spend).toBe(120);
  });

  it("a full earlier month whose statement begins on the 2nd is compared: the app's one rule covers a month from its 3rd", () => {
    // one full month (September, its first row on the 2nd, as a statement file with nothing on the 1st), October in progress
    const rows = [...spends([["2026-09-02", -40], ["2026-09-03", -60], ["2026-09-20", -400]]), ...october];
    const r = monthToDate(rows, "2026-10-04", "2026-09");
    expect(r.priors).toEqual([{ month: "2026-09", days: 4, spend: 100 }]);
    expect(paceSentence(r), "never 'no earlier months' beside a full one")
      .toBe("Only 1 earlier month to compare with yet: a typical pace needs 3.");
  });

  it("with no transaction at all no prior month is covered: no comparison and nothing spent", () => {
    const r = monthToDate([], "2026-10-04", FLOOR);
    expect(r.priors).toEqual([]);
    expect(r.typical).toBeNull();
    expect(r.spend).toBe(0);
  });

  it("months before the floor are never compared, even when the data covers them whole", () => {
    const old = spends([["2030-01-02", -100], ["2030-02-02", -100], ["2030-03-02", -100], ["2030-04-02", -100], ["2030-05-02", -100]]);
    const r = monthToDate([...old, ...spends([["2030-06-02", -100]])], "2030-06-04", "2030-05");
    expect(r.priors.map((p) => p.month)).toEqual(["2030-05"]);
  });

  it("no floor (no month in coverage yet) compares no month: an empty baseline, never a guessed one", () => {
    const r = monthToDate([...sixMonths, ...october], "2026-10-04", null);
    expect(r.priors).toEqual([]);
    expect(r.typical).toBeNull();
    expect(r.ratio).toBeNull();
    expect(r.pace).toBeNull();
    expect(r.spend, "this month's own spend is still counted").toBe(120);
    expect(r.top.map((c) => c.category)).toEqual(["Dining"]);
    expect(paceSentence(r)).toBe("No earlier months to compare with yet.");
  });

  it("a split purchase counts once: its parts carry the amount, and a parent in the list is never added to them", () => {
    const parent = tx({ id: "p", date: "2026-10-02", amount: -100, is_parent: true, category: "Shopping" });
    const parts = [tx({ date: "2026-10-02", amount: -80, parent_id: "p", category: "Shopping", category_source: "split" }),
      tx({ date: "2026-10-02", amount: -20, parent_id: "p", category: "Groceries", category_source: "split" })];
    const r = monthToDate([parent, ...parts], "2026-10-04", FLOOR);
    expect(r.spend).toBe(100);
    expect(r.top).toEqual([{ category: "Shopping", spend: 80 }, { category: "Groceries", spend: 20 }]);
    const prior = monthToDate([alive("2026-08-15"), { ...parent, date: "2026-09-02" }, ...parts.map((p) => ({ ...p, date: "2026-09-02" })),
      ...spends([["2026-10-02", -10]])], "2026-10-04", "2026-09");
    expect(prior.priors[0].spend, "the same in a prior month").toBe(100);
  });

  it("refunds reduce the spend and their category; a category that nets to a refund is not a top category", () => {
    const rows = [
      ...spends([["2026-10-02", -100]], { category: "Groceries" }),
      ...spends([["2026-10-03", 30]], { category: "Groceries" }),
      ...spends([["2026-10-03", 45]], { category: "Shopping" }),         // a refund of an earlier month's purchase
      ...spends([["2026-10-03", -10]], { category: "Dining" }),
    ];
    const r = monthToDate(rows, "2026-10-04", FLOOR);
    expect(r.spend).toBe(35);
    expect(r.top).toEqual([{ category: "Groceries", spend: 70 }, { category: "Dining", spend: 10 }]);
  });

  it("spend is purchases and fees: income, transfers and investments are not counted; no category is Uncategorized", () => {
    const rows = [
      tx({ date: "2026-10-02", amount: -5, type: "fee", category: null }),
      tx({ date: "2026-10-02", amount: -1000, type: "transfer", category: "Internal" }),
      tx({ date: "2026-10-02", amount: -1000, type: "investment", category: "Internal" }),
      tx({ date: "2026-10-02", amount: 2000, type: "income", category: "Income" }),
      tx({ date: "2026-10-02", amount: -0.1 }), tx({ date: "2026-10-02", amount: -0.2 }),
    ];
    const r = monthToDate(rows, "2026-10-04", FLOOR);
    expect(r.spend, "integer cents: 5 + 0.1 + 0.2 is exactly 5.3").toBe(5.3);
    expect(r.top[0]).toEqual({ category: "Uncategorized", spend: 5 });
  });

  it("the top categories are the three largest, a tie broken by name, and a row dated after the data's last day is not counted", () => {
    const rows = [
      ...spends([["2026-10-02", -30]], { category: "Kids" }), ...spends([["2026-10-02", -30]], { category: "Dining" }),
      ...spends([["2026-10-02", -50]], { category: "Groceries" }), ...spends([["2026-10-02", -10]], { category: "Travel" }),
      ...spends([["2026-10-09", -999]], { category: "Travel" }),
    ];
    const r = monthToDate(rows, "2026-10-04", FLOOR);
    expect(r.top.map((x) => x.category)).toEqual(["Groceries", "Dining", "Kids"]);
    expect(r.spend).toBe(120);
  });

  it("no typical figure to divide by: a prior median of 0 leaves the ratio and the pace null", () => {
    const rows = [alive("2026-06-15"), alive("2026-07-02"), alive("2026-08-02"), alive("2026-09-02"), ...spends([["2026-10-02", -10]])];
    const r = monthToDate(rows, "2026-10-04", "2026-07");
    expect(r.typical).toBe(0);
    expect(r.ratio).toBeNull();
    expect(r.pace).toBeNull();
  });

  it("across a year boundary: January is set against December and the months before it", () => {
    const rows = [alive("2025-09-15"), ...spends([["2025-12-03", -100], ["2025-11-03", -100], ["2025-10-03", -100]]),
      ...spends([["2026-01-03", -100]])];
    const r = monthToDate(rows, "2026-01-05", "2025-10");
    expect(r.priors.map((p) => p.month)).toEqual(["2025-12", "2025-11", "2025-10"]);
    expect(r.pace).toBe("about");
  });
});

describe("paceSentence", () => {
  const three = ["2026-09", "2026-08", "2026-07"].map((month) => ({ month, days: 4, spend: 1 }));
  it("says the pace in plain words, rounding the percentage", () => {
    expect(paceSentence({ priors: three, pace: "about", ratio: 0.04 })).toBe("About typical for this point in the month.");
    expect(paceSentence({ priors: three, pace: "above", ratio: 0.234 })).toBe("23% above typical so far.");
    expect(paceSentence({ priors: three, pace: "below", ratio: -0.5 })).toBe("50% below typical so far.");
  });
  it("with nothing to compare it says so instead of inventing a pace", () => {
    expect(paceSentence({ priors: [], pace: null, ratio: null })).toBe("No earlier months to compare with yet.");
    expect(paceSentence({ priors: three.slice(0, 2), pace: null, ratio: null }))
      .toBe("Only 2 earlier months to compare with yet: a typical pace needs 3.");
    expect(paceSentence({ priors: three, pace: null, ratio: null })).toMatch(/^No spending by this day in the months before/);
  });
});

describe("ordinal", () => {
  it("st, nd, rd, th - and the teens are th", () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 30, 31].map(ordinal))
      .toEqual(["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "30th", "31st"]);
  });
});

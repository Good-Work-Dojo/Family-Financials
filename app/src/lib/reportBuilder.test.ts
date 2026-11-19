// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { txFactory } from "./__fixtures__";
import {
  cellValue, MAX_GROUPS, MEASURE_DEFINITIONS, nextPeriod, periodEnd, periodLabel, periodOf, periodsBetween, periodStart, reportInput, reportOption, reportTable, runReport,
} from "./reportBuilder";
import { MEASURES, type ReportSpec } from "./reportSpec";
import { CHART_MUTED } from "./echarts";
import { fmtDate, fmtMonth, fmtMonthAxis, fmtUSD0 } from "./format";

const tx = txFactory({ type: "purchase" });
/** the cases' base: spending by category, per month, stacked (each case overrides what it is about) */
const BASE: ReportSpec = { filters: "", groupBy: "category", interval: "month", measure: "spend", chart: "stacked" };
const spec = (over: Partial<ReportSpec>) => ({ ...BASE, ...over });

describe("periods", () => {
  it("week = the ISO week's Monday (across a year boundary), month, quarter, year", () => {
    expect(periodOf("2026-01-01", "week")).toBe("2025-12-29");     // a Thursday -> the Monday before
    expect(periodOf("2026-01-05", "week")).toBe("2026-01-05");     // a Monday is its own period
    expect(periodOf("2026-03-15", "month")).toBe("2026-03");
    expect(periodOf("2026-03-15", "quarter")).toBe("2026-Q1");
    expect(periodOf("2026-12-31", "quarter")).toBe("2026-Q4");
    expect(periodOf("2026-03-15", "year")).toBe("2026");
  });
  it("a week runs Monday to Sunday: Sunday belongs to the week it ends, the next Monday starts a new one", () => {
    expect(periodOf("2026-01-11", "week")).toBe("2026-01-05");     // Sunday
    expect(periodOf("2026-01-12", "week")).toBe("2026-01-12");     // the next Monday
    expect(periodOf("2026-01-10", "week")).toBe("2026-01-05");     // Saturday
    expect(periodOf("2024-02-29", "week")).toBe("2024-02-26");     // a leap day (a Thursday)
    expect(periodOf("2026-01-04", "week")).toBe("2025-12-29");     // the Sunday before the first Monday of 2026
  });
  it("quarters change on the first day of Jan / Apr / Jul / Oct", () => {
    const q = (d: string) => periodOf(d, "quarter");
    expect([q("2026-03-31"), q("2026-04-01"), q("2026-06-30"), q("2026-07-01"), q("2026-09-30"), q("2026-10-01")])
      .toEqual(["2026-Q1", "2026-Q2", "2026-Q2", "2026-Q3", "2026-Q3", "2026-Q4"]);
  });
  it("nextPeriod rolls over every boundary", () => {
    expect(nextPeriod("2026-12", "month")).toBe("2027-01");
    expect(nextPeriod("2026-Q4", "quarter")).toBe("2027-Q1");
    expect(nextPeriod("2026-Q2", "quarter")).toBe("2026-Q3");
    expect(nextPeriod("2025-12-29", "week")).toBe("2026-01-05");
    expect(nextPeriod("2026", "year")).toBe("2027");
  });
  it("periodsBetween fills the gaps so an axis never skips a bucket", () => {
    expect(periodsBetween("2025-11", "2026-02", "month")).toEqual(["2025-11", "2025-12", "2026-01", "2026-02"]);
    expect(periodsBetween("2025-Q3", "2026-Q1", "quarter")).toEqual(["2025-Q3", "2025-Q4", "2026-Q1"]);
    expect(periodsBetween("2025-12-29", "2026-01-12", "week")).toEqual(["2025-12-29", "2026-01-05", "2026-01-12"]);
    expect(periodsBetween("2024", "2026", "year")).toEqual(["2024", "2025", "2026"]);
  });
  it("a period's first and last day, leap years and year ends included", () => {
    expect([periodStart("2026-03", "month"), periodEnd("2026-03", "month")]).toEqual(["2026-03-01", "2026-03-31"]);
    expect(periodEnd("2026-02", "month")).toBe("2026-02-28");
    expect(periodEnd("2028-02", "month")).toBe("2028-02-29");
    expect([periodStart("2026-Q2", "quarter"), periodEnd("2026-Q2", "quarter")]).toEqual(["2026-04-01", "2026-06-30"]);
    expect([periodStart("2026-Q4", "quarter"), periodEnd("2026-Q4", "quarter")]).toEqual(["2026-10-01", "2026-12-31"]);
    expect([periodStart("2025-12-29", "week"), periodEnd("2025-12-29", "week")]).toEqual(["2025-12-29", "2026-01-04"]);
    expect([periodStart("2026", "year"), periodEnd("2026", "year")]).toEqual(["2026-01-01", "2026-12-31"]);
  });
});

describe("runReport", () => {
  const rows = [
    tx({ date: "2026-01-05", category: "Dining", vendor: "Cafe", amount: -100 }),
    tx({ date: "2026-01-20", category: "Dining", vendor: "Cafe", amount: 20 }),                 // a refund reduces spend
    tx({ date: "2026-03-02", category: "Groceries", vendor: "Market", amount: -300, owner: "Alex" }),
    tx({ date: "2026-03-09", category: "Income", vendor: "Employer", amount: 5000, type: "income", earner: "Alex", owner: "Alex" }),
    tx({ date: "2026-02-01", category: "Internal", vendor: "Bank", amount: -900, type: "transfer", is_internal: true }),
  ];
  it("spend by category per month: contiguous months, refunds netted, income and internal rows ignored", () => {
    const r = runReport(rows, spec({}));
    expect(r.periods).toEqual(["2026-01", "2026-02", "2026-03"]);
    expect(r.groups).toEqual(["Groceries", "Dining"]);                 // by |total|
    expect(r.cells.get("Dining")!.get("2026-01")).toBe(80);
    expect(r.cells.get("Groceries")!.get("2026-03")).toBe(300);
    expect(r.totals.get("Groceries")).toBe(300);
  });
  it("income, net and count measures; a vendor / owner / earner group-by", () => {
    expect([...runReport(rows, spec({ measure: "income", groupBy: "earner" })).totals]).toEqual([["Alex", 5000]]);
    const net = runReport(rows, spec({ measure: "net", groupBy: "owner", interval: "year" }));
    expect(net.cells.get("Joint")!.get("2026")).toBe(-80);
    expect(net.cells.get("Alex")!.get("2026")).toBe(4700);
    const count = runReport(rows, spec({ measure: "count", groupBy: "vendor", interval: "quarter" }));
    expect(count.cells.get("Cafe")!.get("2026-Q1")).toBe(2);
    expect(count.groups).not.toContain("Bank");                        // internal rows are out unless internal=1
    expect(runReport(rows, spec({ measure: "count", groupBy: "vendor", filters: "internal=1" })).groups).toContain("Bank");
  });
  it("applies the spec's filters and rolls groups past the cap into Other (n)", () => {
    expect(runReport(rows, spec({ filters: "categories=Groceries" })).groups).toEqual(["Groceries"]);
    const capped = runReport(rows, spec({}), 1);
    expect(capped.groups).toEqual(["Groceries", "Other (1)"]);
    expect(capped.cells.get("Other (1)")!.get("2026-01")).toBe(80);
  });
  it("an empty result has no periods; the table view formats counts as plain numbers", () => {
    expect(runReport([], spec({})).periods).toEqual([]);
    // no row, and rows the filter leaves none of: no period, no group, nothing partial or silent - the whole result
    for (const [why, r] of [["no row", runReport([], spec({ filters: "from=2026-01-05" }))],
      ["no row passes the filter", runReport(rows, spec({ filters: "categories=Nothing&from=2026-01-05" }))]] as const) {
      expect({ periods: r.periods, groups: r.groups, partial: [...r.partial], silent: [...r.silent], other: r.other,
        cells: r.cells.size, totals: r.totals.size }, why)
        .toEqual({ periods: [], groups: [], partial: [], silent: [], other: null, cells: 0, totals: 0 });
    }
    const t = reportTable(runReport(rows, spec({ measure: "count" })), "count", "month");
    expect(t.columns[0]).toBe("Period");
    expect(t.format!(3, 1)).toBe("3");
    expect(reportTable(runReport(rows, spec({})), "spend", "month").format).toBeUndefined();   // money: the default fmtUSD0 (masked by privacy)
  });

  describe("each measure's sign convention, on one set of rows", () => {
    const m = [
      tx({ date: "2026-05-01", amount: -50 }),                                          // spend 50
      tx({ date: "2026-05-02", amount: -25, type: "fee" }),                              // a fee is spend too: 25
      tx({ date: "2026-05-03", amount: 10 }),                                            // a refund: spend -10
      tx({ date: "2026-05-04", amount: 1000, type: "income", category: "Income" }),      // income 1000
      tx({ date: "2026-05-05", amount: 3, type: "interest", category: "Income" }),         // interest is income
      tx({ date: "2026-05-06", amount: 7, type: "dividend", category: "Income" }),         // dividend is income
      tx({ date: "2026-05-07", amount: -400, type: "investment", category: "Investing" }), // neither spend nor income
    ];
    const total = (measure: "spend" | "income" | "net" | "count") =>
      [...runReport(m, spec({ measure, groupBy: "owner", interval: "year", filters: "internal=1" })).totals.values()];
    it("spend = -sum of spend rows, a refund reduces it, income and investment rows are not spend", () => {
      expect(total("spend")).toEqual([65]);
    });
    it("income = sum of income, dividend and interest rows only", () => {
      expect(total("income")).toEqual([1010]);
    });
    it("net = income - spend, signed (negative when spending outruns income)", () => {
      expect(total("net")).toEqual([1010 - 65]);
      const poor = [tx({ date: "2026-05-01", amount: -80 }), tx({ date: "2026-05-02", amount: 30, type: "income", category: "Income" })];
      expect([...runReport(poor, spec({ measure: "net", interval: "year", groupBy: "owner" })).totals.values()]).toEqual([-50]);
    });
    it("count = every purchase the filters pass, whatever its type (an investment row counts)", () => {
      expect(total("count")).toEqual([7]);
    });
    it("a measure with nothing to count leaves no group, no period", () => {
      const r = runReport([tx({ amount: -5 })], spec({ measure: "income" }));
      expect(r.groups).toEqual([]);
      expect(r.periods).toEqual([]);
    });
  });

  describe("grouping", () => {
    it("subcategory shows category / subcategory; a missing one reads — and a missing category Uncategorized", () => {
      const g = runReport([
        tx({ category: "Dining", subcategory: "Coffee", amount: -5 }),
        tx({ category: "Dining", subcategory: null, amount: -4 }),
        tx({ category: null, subcategory: null, amount: -3 }),
      ], spec({ groupBy: "subcategory", interval: "year" })).groups;
      expect(g).toEqual(["Dining / Coffee", "Dining / —", "Uncategorized / —"]);
    });
    it("account groups by account name; vendor and earner show — when absent", () => {
      const r = runReport([
        tx({ account_name: "Checking", vendor: null, amount: -5 }),
        tx({ account_name: "Visa", vendor: "Shop", amount: -9 }),
      ], spec({ groupBy: "account", interval: "year" }));
      expect(r.groups).toEqual(["Visa", "Checking"]);
      expect(runReport([tx({ vendor: null, amount: -5 })], spec({ groupBy: "vendor", interval: "year" })).groups).toEqual(["—"]);
      expect(runReport([tx({ amount: 5, type: "income", category: "Income", earner: null })],
        spec({ groupBy: "earner", measure: "income", interval: "year" })).groups).toEqual(["—"]);
    });
    it("groups are ranked by |total|, so a big negative net outranks a small positive one", () => {
      const r = runReport([
        tx({ owner: "Joint", amount: -900 }),
        tx({ owner: "Alex", amount: 40, type: "income", category: "Income" }),
      ], spec({ measure: "net", groupBy: "owner", interval: "year" }));
      expect(r.groups).toEqual(["Joint", "Alex"]);
      expect(r.totals.get("Joint")).toBe(-900);
    });
  });

  describe("the top groups and Other (n)", () => {
    const many = Array.from({ length: 14 }, (_, i) => tx({ date: "2026-02-10", category: `Cat${String(i).padStart(2, "0")}`, amount: -(100 - i) }));
    it("keeps the top 12 by total and rolls the rest into one Other (2), losing nothing", () => {
      const r = runReport(many, spec({}));
      expect(MAX_GROUPS).toBe(12);
      expect(r.groups).toHaveLength(13);
      expect(r.groups.slice(0, 12)).toEqual(Array.from({ length: 12 }, (_, i) => `Cat${String(i).padStart(2, "0")}`));
      expect(r.groups[12]).toBe("Other (2)");
      expect(r.other).toBe("Other (2)");
      expect(r.totals.get("Other (2)")).toBe(88 + 87);
      expect(r.cells.get("Other (2)")!.get("2026-02")).toBe(88 + 87);
      expect([...r.totals.values()].reduce((a, b) => a + b, 0)).toBe(many.reduce((s, t) => s - t.amount, 0));   // nothing dropped
    });
    it("exactly 12 groups need no Other", () => {
      const r = runReport(many.slice(0, 12), spec({}));
      expect(r.groups).toHaveLength(12);
      expect(r.other).toBeNull();
    });
    it("the Other roll-up merges period by period", () => {
      const r = runReport([
        tx({ date: "2026-01-10", category: "A", amount: -100 }),
        tx({ date: "2026-01-11", category: "B", amount: -10 }),
        tx({ date: "2026-02-11", category: "C", amount: -20 }),
        tx({ date: "2026-02-12", category: "B", amount: -5 }),
      ], spec({}), 1);
      expect(r.groups).toEqual(["A", "Other (2)"]);
      expect(r.cells.get("Other (2)")!.get("2026-01")).toBe(10);
      expect(r.cells.get("Other (2)")!.get("2026-02")).toBe(25);
    });
  });

  // A split purchase (a vendor rule's allocation) is ONE purchase however many parts: a count measure counts it once per
  // cell. The roll-up into "Other (n)" added the rolled-up groups' counts, so a purchase whose parts landed in two groups
  // past the cap counted twice there.
  describe("count in Other (n): a split purchase is one purchase", () => {
    const part = (category: string, parent: string, date = "2026-02-10") =>
      tx({ date, category, amount: -10, parent_id: parent });
    // A has the most rows (kept); B and C are rolled up into Other (2). p1 is split across B and C; p2 across B only
    const rows = [
      tx({ date: "2026-02-03", category: "A" }), tx({ date: "2026-02-04", category: "A" }), tx({ date: "2026-02-05", category: "A" }),
      part("B", "p1"), part("C", "p1"),
      part("B", "p2", "2026-03-02"), part("B", "p2", "2026-03-02"),
      tx({ date: "2026-03-09", category: "C" }),
    ];
    const r = runReport(rows, spec({ measure: "count", groupBy: "category" }), 1);
    it("counts the purchase once in the period's Other cell, not once per part", () => {
      expect(r.groups).toEqual(["A", "Other (2)"]);
      expect(r.cells.get("Other (2)")!.get("2026-02")).toBe(1);             // p1 touches B and C: one purchase
      expect(r.cells.get("Other (2)")!.get("2026-03")).toBe(2);             // p2 (two parts, both B) + one plain C row
    });
    it("and once in the Other total", () => {
      expect(r.totals.get("Other (2)")).toBe(3);                            // p1, p2, the C row
      expect(r.totals.get("A")).toBe(3);
    });
    it("a plain (unsplit) rolled-up row still counts, and money measures still add the parts", () => {
      const plain = runReport([tx({ date: "2026-02-03", category: "A" }), tx({ date: "2026-02-03", category: "A" }),
        tx({ date: "2026-02-04", category: "B" }), tx({ date: "2026-02-04", category: "C" })], spec({ measure: "count" }), 1);
      expect(plain.cells.get("Other (2)")!.get("2026-02")).toBe(2);
      const spend = runReport(rows, spec({ measure: "spend", groupBy: "category" }), 1);
      expect(spend.cells.get("Other (2)")!.get("2026-02")).toBe(20);        // the parts' dollars add: 10 + 10
    });
  });

  describe("money is summed in cents", () => {
    it("0.1 + 0.2 is 0.3 and 1.1 + 2.2 is 3.3, in the cell, the total and the table (no 3+ decimal float noise in the CSV)", () => {
      const r = runReport([tx({ date: "2026-05-01", amount: -0.1 }), tx({ date: "2026-05-02", amount: -0.2 }),
        tx({ date: "2026-06-01", amount: -1.1 }), tx({ date: "2026-06-02", amount: -2.2 })], spec({}));
      expect(r.cells.get("Dining")!.get("2026-05")).toBe(0.3);
      expect(r.cells.get("Dining")!.get("2026-06")).toBe(3.3);
      expect(r.totals.get("Dining")).toBe(3.6);
      expect(reportTable(r, "spend", "month").rows.map((row) => row[1])).toEqual([0.3, 3.3]);
      const net = runReport([tx({ date: "2026-05-01", amount: 0.3, type: "income", category: "Income" }), tx({ date: "2026-05-02", amount: -0.1 })],
        spec({ measure: "net", groupBy: "owner", interval: "year" }));
      expect([...net.totals.values()]).toEqual([0.2]);                // 0.3 - 0.1 is 0.19999999999999998 in floats
    });
    it("the Other roll-up sums in cents too, and a refund that nets a cell to exactly zero is 0, not -0", () => {
      const r = runReport([tx({ category: "A", amount: -9 }), tx({ category: "B", amount: -0.1 }), tx({ category: "C", amount: -0.2 }),
        tx({ category: "D", amount: -5 }), tx({ category: "D", amount: 5 })], spec({ interval: "year" }), 1);
      expect(r.groups).toEqual(["A", "Other (3)"]);
      expect(r.cells.get("Other (3)")!.get("2025")).toBe(0.3);        // 0.2 + 0.1 + 0 in cents; floats give 0.30000000000000004
      const all = runReport([tx({ category: "D", amount: -5 }), tx({ category: "D", amount: 5 })], spec({ interval: "year" }));
      expect(Object.is(all.cells.get("D")!.get("2025"), 0), "an exact net-out is +0, which fmtUSD0 prints as $0, never -$0").toBe(true);
    });
    it("a count is a whole number however many rows", () => {
      const rows3 = Array.from({ length: 7 }, () => tx({ date: "2026-05-01", amount: -0.1 }));
      expect(runReport(rows3, spec({ measure: "count", interval: "year" })).totals.get("Dining")).toBe(7);
    });
  });

  describe("ties at the top-12 cut", () => {
    const names = Array.from({ length: 14 }, (_, i) => `G${String(i).padStart(2, "0")}`);
    const ties = (order: string[]) => order.map((c) => tx({ date: "2026-02-10", category: c, amount: -50 }));
    it("equal totals break by group name, so which groups make the top 12 does not depend on row order", () => {
      const forward = runReport(ties(names), spec({}));
      const reversed = runReport(ties([...names].reverse()), spec({}));
      const shuffled = runReport(ties(names.map((_, i) => names[(i * 5) % names.length])), spec({}));       // 5 is coprime with 14: a permutation
      for (const r of [forward, reversed, shuffled]) {
        expect(r.groups.slice(0, 12)).toEqual(names.slice(0, 12));
        expect(r.groups[12]).toBe("Other (2)");
        expect(r.totals.get("Other (2)")).toBe(100);
      }
    });
    it("a real difference still outranks the name: the bigger group wins the last slot", () => {
      const r = runReport([...ties(names.slice(0, 12)), tx({ date: "2026-02-10", category: "ZZ", amount: -60 })], spec({}));
      expect(r.groups[0]).toBe("ZZ");
      expect(r.groups).toHaveLength(13);
      expect(r.groups).not.toContain("G11");
      expect(r.other).toBe("Other (1)");
    });
  });

  describe("weeks and the data's own edges", () => {
    it("rows on a Sunday and the next Monday land in different weeks, and the empty week between is filled", () => {
      const r = runReport([
        tx({ date: "2026-01-04", amount: -10 }),        // Sunday: week of 2025-12-29
        tx({ date: "2026-01-19", amount: -20 }),        // Monday two weeks on
      ], spec({ interval: "week" }));
      expect(r.periods).toEqual(["2025-12-29", "2026-01-05", "2026-01-12", "2026-01-19"]);
    });
  });

  describe("an empty period: 0 only where the data really has no activity to report, otherwise a gap", () => {
    const r = runReport([
      tx({ date: "2026-01-10", amount: -10, account: "a" }),
      tx({ date: "2026-03-10", amount: -30, account: "a" }),
    ], spec({ groupBy: "owner" }));
    it("a month in which the data holds no transaction at all is silent: unknown, never 0", () => {
      expect(r.periods).toEqual(["2026-01", "2026-02", "2026-03"]);
      expect([...r.silent]).toEqual(["2026-02"]);
      expect(cellValue(r, "Joint", "2026-02")).toBeNull();
      expect(cellValue(r, "Joint", "2026-01")).toBe(10);
    });
    it("a month with transactions that none of the report's rows match is a real zero (a transfer proves the feed is alive)", () => {
      const withTransfer = runReport([
        tx({ date: "2026-01-10", amount: -10 }),
        tx({ date: "2026-02-10", amount: -500, type: "transfer", is_internal: true }),
        tx({ date: "2026-03-10", amount: -30 }),
      ], spec({ groupBy: "owner" }));
      expect(withTransfer.silent.size).toBe(0);
      expect(cellValue(withTransfer, "Joint", "2026-02")).toBe(0);
    });
    it("a month where another group had rows but this one did not is a real zero too", () => {
      const two = runReport([
        tx({ date: "2026-01-10", amount: -10, category: "A" }),
        tx({ date: "2026-02-10", amount: -10, category: "B" }),
      ], spec({}));
      expect(cellValue(two, "A", "2026-02")).toBe(0);
      expect(cellValue(two, "B", "2026-01")).toBe(0);
    });
    it("silence is judged on every row handed in, not just the filtered ones (a filter does not make a month silent)", () => {
      const filtered = runReport([
        tx({ date: "2026-01-10", amount: -10, category: "A" }),
        tx({ date: "2026-02-10", amount: -99, category: "Elsewhere" }),
        tx({ date: "2026-03-10", amount: -30, category: "A" }),
      ], spec({ filters: "categories=A" }));
      expect(filtered.periods).toEqual(["2026-01", "2026-02", "2026-03"]);
      expect(filtered.silent.size).toBe(0);
      expect(cellValue(filtered, "A", "2026-02")).toBe(0);
    });
  });

  describe("partial periods", () => {
    const day = (date: string, amount = -10) => tx({ date, amount });
    // every fixture below starts on a period's first day, so only the edge under test is partial
    it("the period holding the newest data is partial until the data reaches its last day", () => {
      const mid = runReport([day("2026-09-01"), day("2026-10-14")], spec({}));
      expect([...mid.partial]).toEqual(["2026-10"]);
      const full = runReport([day("2026-09-01"), day("2026-10-31")], spec({}));
      expect(full.partial.size).toBe(0);
    });
    it("the month in progress is partial even when the data reaches its last day, and a month after it is no period", () => {
      const rows2 = [day("2026-09-01"), day("2026-10-31"), day("2026-11-02", -999)];     // one row dated into November
      const r = runReport(rows2, spec({}), MAX_GROUPS, "2026-10");
      expect(r.periods).toEqual(["2026-09", "2026-10"]);
      expect([...r.partial]).toEqual(["2026-10"]);
      expect([...r.totals.values()].reduce((s, v) => s + v, 0), "the November row is in no total").toBe(20);
      expect(r.silent.size).toBe(0);
      // the quarter and the year holding it are partial; a week that ends before the month does is judged by the data
      const fromQ3 = [day("2026-07-01"), ...rows2], fromJanuary = [day("2025-01-01"), ...rows2];
      expect([...runReport(fromQ3, spec({ interval: "quarter" }), MAX_GROUPS, "2026-10").partial]).toEqual(["2026-Q4"]);
      expect([...runReport(fromJanuary, spec({ interval: "year" }), MAX_GROUPS, "2026-10").partial]).toEqual(["2026"]);
      expect(runReport([day("2026-10-12"), day("2026-10-18")], spec({ interval: "week" }), MAX_GROUPS, "2026-10").partial.size).toBe(0);
      // control: without the month in progress the last day makes October whole and November a period
      expect(runReport(rows2, spec({})).periods).toEqual(["2026-09", "2026-10", "2026-11"]);
    });
    it("the newest data is judged over every row, so a filter that stops earlier shows complete periods", () => {
      const r = runReport([tx({ date: "2026-08-01", category: "A" }), tx({ date: "2026-10-14", category: "B" })], spec({ filters: "categories=A" }));
      expect(r.periods).toEqual(["2026-08"]);
      expect(r.partial.size).toBe(0);
    });
    it("the first period is partial when the data starts inside it, judged over every row too", () => {
      const mid = runReport([day("2026-04-10"), day("2026-05-31")], spec({}));
      expect([...mid.partial]).toEqual(["2026-04"]);
      expect(runReport([day("2026-04-01"), day("2026-05-31")], spec({})).partial.size).toBe(0);
      // a filter that starts later does not move where the DATA starts: April is complete when the data begins on the 1st
      const later = runReport([tx({ date: "2026-04-01", category: "B" }), tx({ date: "2026-05-12", category: "A" }), tx({ date: "2026-05-31", category: "A" })],
        spec({ filters: "categories=A" }));
      expect(later.periods).toEqual(["2026-05"]);
      expect(later.partial.size).toBe(0);
      // ...and one that begins mid-month in the data but whose first MATCHED row is a later month leaves that month whole
      const skip = runReport([tx({ date: "2026-04-10", category: "B" }), tx({ date: "2026-05-12", category: "A" }), tx({ date: "2026-05-31", category: "A" })],
        spec({ filters: "categories=A" }));
      expect(skip.partial.size).toBe(0);
    });
    it("a From date before the data starts changes nothing: the window is cut to the data", () => {
      expect([...runReport([day("2026-04-10"), day("2026-05-31")], spec({ filters: "from=2026-01-01" })).partial]).toEqual(["2026-04"]);
    });
    it("week, quarter and year judge both edges the same way", () => {
      expect([...runReport([day("2026-10-13"), day("2026-10-14")], spec({ interval: "week" })).partial]).toEqual(["2026-10-12"]);   // Tue-Wed of one week
      expect(runReport([day("2026-10-12"), day("2026-10-18")], spec({ interval: "week" })).partial.size).toBe(0);                      // Monday through Sunday
      expect([...runReport([day("2026-10-14"), day("2026-10-18")], spec({ interval: "week" })).partial]).toEqual(["2026-10-12"]);     // starts Wednesday
      expect([...runReport([day("2026-07-01"), day("2026-12-30")], spec({ interval: "quarter" })).partial]).toEqual(["2026-Q4"]);
      expect([...runReport([day("2026-08-01"), day("2026-12-31")], spec({ interval: "quarter" })).partial]).toEqual(["2026-Q3"]);
      expect([...runReport([day("2025-01-01"), day("2026-06-30")], spec({ interval: "year" })).partial]).toEqual(["2026"]);
      expect([...runReport([day("2025-08-01"), day("2026-12-31")], spec({ interval: "year" })).partial]).toEqual(["2025"]);
      expect(runReport([day("2025-01-01"), day("2026-12-31")], spec({ interval: "year" })).partial.size).toBe(0);
    });
    it("a From date after the first period's 1st makes that period partial, the 2nd and the 3rd included", () => {
      const rows2 = [day("2026-04-01"), day("2026-04-10"), day("2026-05-12"), day("2026-06-30")];
      expect([...runReport(rows2, spec({ filters: "from=2026-04-04" })).partial]).toEqual(["2026-04"]);
      expect([...runReport(rows2, spec({ filters: "from=2026-04-03" })).partial], "the one rule: lib/agg.ts coversFromStart")
        .toEqual(["2026-04"]);
      expect([...runReport(rows2, spec({ filters: "from=2026-04-02" })).partial]).toEqual(["2026-04"]);
      expect(runReport(rows2, spec({ filters: "from=2026-04-01" })).partial.size).toBe(0);
      expect(runReport(rows2, spec({})).partial.size).toBe(0);                                  // no From, and the data starts on the 1st
    });
    it("a weekly report whose first week starts on a month's 1st is judged strictly, like every week", () => {
      // 2026-06-01 is a Monday: the first week's key ends -01, and data from its Tuesday leaves its Monday out
      expect([...runReport([day("2026-06-02"), day("2026-06-07")], spec({ interval: "week" })).partial]).toEqual(["2026-06-01"]);
      expect([...runReport([day("2026-06-03"), day("2026-06-07")], spec({ interval: "week" })).partial]).toEqual(["2026-06-01"]);
      expect(runReport([day("2026-06-01"), day("2026-06-07")], spec({ interval: "week" })).partial.size).toBe(0);
      expect(runReport([day("2026-06-03"), day("2026-06-30")], spec({ interval: "month" })).partial.size,
        "the same data, by month, keeps the data's first-days tolerance").toBe(0);
    });
    it("data that begins on the 2nd covers its first month (nothing happened on the 1st); from the 4th it does not", () => {
      expect(runReport([day("2026-04-02"), day("2026-05-12"), day("2026-06-30")], spec({})).partial.size).toBe(0);
      expect([...runReport([day("2026-04-04"), day("2026-05-12"), day("2026-06-30")], spec({})).partial]).toEqual(["2026-04"]);
    });
    it("a To date inside the last period makes it partial even when the data runs on", () => {
      const rows2 = [day("2026-04-01"), day("2026-05-12"), day("2026-06-30")];
      expect([...runReport(rows2, spec({ filters: "to=2026-05-20" })).partial]).toEqual(["2026-05"]);
      expect(runReport(rows2, spec({ filters: "to=2026-05-31" })).partial.size).toBe(0);
    });
    it("first and last can both be partial", () => {
      const r = runReport([day("2026-04-10"), day("2026-10-14")], spec({ filters: "from=2026-04-03" }));
      expect([...r.partial]).toEqual(["2026-04", "2026-10"]);
    });
  });

  describe("reportInput: the one switch between the category view and the vendor view", () => {
    const category = [tx({ id: "c1" })], vendor = [tx({ id: "p1", is_parent: true })];
    const data = { transactions: category, purchases: vendor };
    it("a vendor group-by reads the vendor view (one row per purchase, a split at its full amount)", () => {
      expect(reportInput(data, spec({ groupBy: "vendor" }))).toBe(vendor);
    });
    it("every other group-by reads the category view", () => {
      for (const g of ["category", "subcategory", "account", "owner", "earner"] as const) {
        expect(reportInput(data, spec({ groupBy: g }))).toBe(category);
      }
    });
    it("a vendor group-by under a category filter reads the category view: a parent's category is display-only, only the parts can answer", () => {
      expect(reportInput(data, spec({ groupBy: "vendor", filters: "categories=Groceries" }))).toBe(category);
      expect(reportInput(data, spec({ groupBy: "vendor", filters: "from=2026-01-01&vendors=Synth+Mart" }))).toBe(vendor);
    });
    it("and under a tag, an amount bound or the review flag: those fields can differ between a part and its parent", () => {
      for (const filters of ["tags=Birthday", "min=50", "max=100", "min=0&max=500", "review=1"]) {
        expect(reportInput(data, spec({ groupBy: "vendor", filters })), filters).toBe(category);
      }
      // filters that parts copy from the parent stay on the vendor view
      for (const filters of ["accounts=a&owners=Joint&types=purchase&dir=expense&q=mart", "to=2026-12-31"]) {
        expect(reportInput(data, spec({ groupBy: "vendor", filters })), filters).toBe(vendor);
      }
    });
  });

  describe("a vendor report under a part-level filter finds what the FilterBar shows", () => {
    // a 300 trip untagged (parent), split 210 / 90; a tags-only answer tagged only the 90 part
    const m = txFactory({ vendor: "Synth Mart", date: "2026-03-05" });
    const parent = m({ id: "p1", amount: -300, is_parent: true, category: "Groceries" });
    const parts = [
      m({ id: "c1", amount: -210, parent_id: "p1", category: "Groceries" }),
      m({ id: "c2", amount: -90, parent_id: "p1", category: "Shopping", tags: ["Birthday"] }),
    ];
    const data = { transactions: parts, purchases: [parent] };
    const report = (filters: string, measure: "spend" | "count" = "spend") => {
      const s = spec({ groupBy: "vendor", measure, filters });
      return runReport(reportInput(data, s), s);
    };
    it("a tag on one part: the vendor's spend is that part's amount, not an empty report", () => {
      expect(cellValue(report("tags=Birthday"), "Synth Mart", "2026-03")).toBe(90);
    });
    it("an amount bound judges the part, and a purchase whose two parts both pass still counts once", () => {
      expect(cellValue(report("min=100"), "Synth Mart", "2026-03")).toBe(210);          // only the 210 part is >= 100
      expect(cellValue(report("max=250", "count"), "Synth Mart", "2026-03")).toBe(1);    // both parts pass: one purchase
      expect(cellValue(report("max=250"), "Synth Mart", "2026-03")).toBe(300);           // ... at the parts' sum
    });
  });

  describe("count counts a split purchase once, never its parts", () => {
    const m = txFactory({ vendor: "Synth Mart", date: "2026-03-05" });
    const rows = [
      m({ id: "c1", amount: -210, parent_id: "p1", category: "Groceries" }),
      m({ id: "c2", amount: -90, parent_id: "p1", category: "Shopping" }),
      m({ id: "o1", amount: -40, category: "Groceries" }),
    ];
    it("by vendor: one split trip is one; by category: once in each category it touches; spend still sums the parts", () => {
      const byVendor = runReport(rows, spec({ groupBy: "vendor", measure: "count" }));
      expect(cellValue(byVendor, "Synth Mart", "2026-03")).toBe(2);
      const byCat = runReport(rows, spec({ groupBy: "category", measure: "count" }));
      expect([cellValue(byCat, "Groceries", "2026-03"), cellValue(byCat, "Shopping", "2026-03")]).toEqual([2, 1]);
      const spend = runReport(rows, spec({ groupBy: "vendor", measure: "spend" }));
      expect(cellValue(spend, "Synth Mart", "2026-03")).toBe(340);
    });
    it("whatever the group-by: parts that share an account count one purchase there (not two)", () => {
      const byAccount = runReport(rows, spec({ groupBy: "account", measure: "count" }));
      expect(cellValue(byAccount, "A", "2026-03")).toBe(2);        // the split trip + the ordinary one
      const byOwner = runReport(rows, spec({ groupBy: "owner", measure: "count" }));
      expect(cellValue(byOwner, "Joint", "2026-03")).toBe(2);
    });
  });
});

describe("MEASURE_DEFINITIONS", () => {
  it("defines every measure, and the spend and net words name what the arithmetic does", () => {
    expect(Object.keys(MEASURE_DEFINITIONS).sort()).toEqual([...MEASURES].sort());
    expect(MEASURE_DEFINITIONS.spend).toContain("refunds netted");
    expect(MEASURE_DEFINITIONS.net).toContain("income − spend");
    expect(MEASURE_DEFINITIONS.count).toContain("counts once");
  });
});

describe("reportTable and reportOption", () => {
  const rows = [
    tx({ date: "2026-01-01", category: "Dining", amount: -100 }),
    tx({ date: "2026-03-09", category: "Groceries", amount: -300 }),
    tx({ date: "2026-04-14", category: "Dining", amount: -20 }),
  ];
  const res = runReport(rows, spec({ filters: "from=2026-01-01" }));      // Jan..Apr; April is partial (data ends the 14th)

  it("one row per period, one column per group; a silent month is a null cell, a zero a 0; the partial period is starred", () => {
    const t = reportTable(res, "spend", "month");
    expect(t.columns).toEqual(["Period", "Groceries", "Dining"]);          // by |total|: 300 then 120
    // the periods as the page names them in prose, the partial one starred as the line under the report stars it
    expect(t.rows.map((r) => r[0])).toEqual([fmtMonth("2026-01"), fmtMonth("2026-02"), fmtMonth("2026-03"), `${fmtMonth("2026-04")}*`]);
    expect(t.rows[0]).toEqual([fmtMonth("2026-01"), 0, 100]);            // Groceries had no January rows, but the data is alive that month
    expect(t.rows[1]).toEqual([fmtMonth("2026-02"), null, null]);        // no transaction anywhere in February
    expect(t.rows[3]).toEqual([`${fmtMonth("2026-04")}*`, 0, 20]);
    expect(t.rows.map((r) => r[0]).join(" "), "no raw year-month").not.toMatch(/\d{4}-\d{2}/);
    // a week by its Monday, as a date
    expect(reportTable(runReport(rows, spec({ interval: "week" })), "spend", "week").rows[1][0]).toBe(fmtDate("2026-01-05"));
  });
  it("counts format as whole numbers with thousands separators, money keeps the default formatter", () => {
    const t = reportTable(runReport(rows, spec({ measure: "count" })), "count", "month");
    expect(t.format!(1234, 1)).toBe("1,234");
    expect(t.format!(7, 1)).toBe("7");
  });
  it("the chart draws the same numbers as the table (a gap is null, so a bar is absent and a line breaks)", () => {
    const t = reportTable(res, "spend", "month");
    const o = reportOption(res, spec({})) as any;
    expect(o.series.map((s: any) => s.name)).toEqual(["Groceries", "Dining"]);
    o.series.forEach((s: any, j: number) => expect(s.data).toEqual(t.rows.map((r) => r[j + 1])));
  });
  it("stacked bars stack, a line chart does not stack, and the kind is bar / line", () => {
    const stacked = reportOption(res, spec({ chart: "stacked" })) as any;
    expect(stacked.series.every((s: any) => s.type === "bar" && s.stack === "report")).toBe(true);
    const line = reportOption(res, spec({ chart: "line" })) as any;
    expect(line.series.every((s: any) => s.type === "line" && s.stack === undefined)).toBe(true);
  });
  it("the x axis marks the partial period with an asterisk and reads months as Jan ’26", () => {
    const o = reportOption(res, spec({})) as any;
    const labels: string[] = o.xAxis.data;
    expect(labels).toHaveLength(4);
    expect(labels[3].endsWith("*")).toBe(true);
    expect(labels.slice(0, 3).some((l) => l.endsWith("*"))).toBe(false);
    expect(labels[0]).toBe(fmtMonthAxis("2026-01"));
    const wk = reportOption(runReport(rows, spec({ interval: "week" })), spec({ interval: "week" })) as any;
    expect(wk.xAxis.data[0]).toBe("2025-12-29*");                        // weeks keep their Monday, ISO; the data starts on the Thursday after it
    expect(wk.xAxis.data[1]).toBe("2026-01-05");
  });
  it("the first period is starred and labelled too when the data starts inside it", () => {
    const mid = runReport([tx({ date: "2026-04-10", amount: -5 }), tx({ date: "2026-05-31", amount: -5 })], spec({}));
    const o = reportOption(mid, spec({})) as any;
    expect(o.xAxis.data[0].endsWith("*")).toBe(true);
    expect(o.xAxis.data[1].endsWith("*")).toBe(false);
    expect(reportTable(mid, "spend", "month").rows.map((r) => r[0])).toEqual([`${fmtMonth("2026-04")}*`, fmtMonth("2026-05")]);
  });
  it("periodLabel: a month as Jan 2026 (Jan \u201926 on the chart's axis), other intervals raw, partial starred", () => {
    expect(periodLabel("2026-04", "month", false)).toBe(fmtMonth("2026-04"));
    expect(periodLabel("2026-04", "month", true)).toBe(`${fmtMonth("2026-04")}*`);
    expect(periodLabel("2026-04", "month", false, true)).toBe(fmtMonthAxis("2026-04"));
    expect(periodLabel("2026-04", "month", true, true)).toBe(`${fmtMonthAxis("2026-04")}*`);
    expect(periodLabel("2026-04", "month", false, true)).not.toBe(periodLabel("2026-04", "month", false));
    expect(periodLabel("2026-Q2", "quarter", true)).toBe("2026-Q2*");
    expect(periodLabel("2026-04-06", "week", false)).toBe(fmtDate("2026-04-06"));
    expect(periodLabel("2026-04-06", "week", false, true), "the axis keeps the short key").toBe("2026-04-06");
    expect(periodLabel("2026", "year", true)).toBe("2026*");
  });
  it("a week chart keeps a wider right gutter so its 10-character last label is not clipped; the others keep the base grid", () => {
    const wk = reportOption(runReport(rows, spec({ interval: "week" })), spec({ interval: "week" })) as any;
    expect(wk.grid.right).toBeGreaterThan(16);                          // the phone tuning keeps an author's gutter past 16
    expect((reportOption(res, spec({})) as any).grid.right).toBe(16);
  });
  it("a count report's axis and tooltip are plain numbers; a money report's are dollars", () => {
    const money = reportOption(res, spec({})) as any;
    expect(money.tooltip.valueFormatter(1234)).toContain("$");
    const count = reportOption(runReport(rows, spec({ measure: "count" })), spec({ measure: "count" })) as any;
    expect(count.tooltip.valueFormatter(1234)).toBe("1,234");
    expect(count.yAxis.axisLabel.formatter).toBeUndefined();
  });
  it("a gap reaches the tooltip as null / undefined and reads a dash, a real 0 stays $0 (count: 0), for every measure", () => {
    const money = reportOption(res, spec({})) as any;
    expect(money.tooltip.trigger).toBe("axis");
    expect(money.tooltip.valueFormatter(undefined)).toBe("—");
    expect(money.tooltip.valueFormatter(null)).toBe("—");
    expect(money.tooltip.valueFormatter(0)).toBe("$0");
    expect(money.tooltip.valueFormatter(-1234.4)).toBe(fmtUSD0(-1234.4));
    const count = reportOption(runReport(rows, spec({ measure: "count" })), spec({ measure: "count" })) as any;
    expect(count.tooltip.valueFormatter(undefined)).toBe("—");
    expect(count.tooltip.valueFormatter(null)).toBe("—");
    expect(count.tooltip.valueFormatter(0)).toBe("0");
    // and the series really carry that gap: February has no transaction anywhere, so its point is null in every series
    expect((money.series as any[]).every((s) => s.data[1] === null)).toBe(true);
  });
  it("the Other roll-up is drawn in the muted colour, never a category's", () => {
    const many = Array.from({ length: 3 }, (_, i) => tx({ category: `C${i}`, amount: -(10 - i) }));
    const r = runReport(many, spec({}), 1);
    const o = reportOption(r, spec({})) as any;
    const other = o.series.find((s: any) => s.name === "Other (2)");
    const first = o.series.find((s: any) => s.name === "C0");
    expect(other.color).toBe(CHART_MUTED);
    expect(first.color).not.toBe(CHART_MUTED);
  });
});

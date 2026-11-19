// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { cfmFactory, txFactory, vocabAssumptions } from "./__fixtures__";
import { budgetGrid, budgetRows } from "./budgets";
import { categoryVocab } from "./categories";

const cfm = cfmFactory();
const V = categoryVocab(vocabAssumptions());

describe("budgetRows", () => {
  const months = [
    ...Array.from({ length: 12 }, (_, i) => cfm({
      month: `2025-${String(i + 1).padStart(2, "0")}`,
      by_category: {
        Groceries: { total: 1000 + (i % 3) * 100, by_subcategory: {} },  // median 1100
        Dining: { total: 800, by_subcategory: {} },
      },
    })),
    cfm({
      month: "2026-01",
      by_category: {
        Groceries: { total: 1500, by_subcategory: {} },
        Dining: { total: 700, by_subcategory: {} },
      },
    }),
  ];
  it("auto-baselines from the trailing median and ranks by |variance|", () => {
    const rows = budgetRows(V, months, "2026-01");
    expect(rows[0].category).toBe("Groceries");
    expect(rows[0].budget).toBe(1100);
    expect(rows[0].variance).toBe(400);
    expect(rows[0].source).toBe("auto");
  });
  it("explicit overrides win and are labeled", () => {
    const rows = budgetRows(V, months, "2026-01", { Dining: 500 });
    const dining = rows.find((r) => r.category === "Dining")!;
    expect(dining.budget).toBe(500);
    expect(dining.variance).toBe(200);
    expect(dining.source).toBe("set");
  });
});

describe("budgetGrid", () => {
  // 'Internal' is in every month's by_category on purpose: a grid that listed it would fail below
  const cat = (dining: number, groceries: number) => ({
    Dining: { total: dining, by_subcategory: {} }, Groceries: { total: groceries, by_subcategory: {} },
    Tiny: { total: 10, by_subcategory: {} }, Internal: { total: 5000, by_subcategory: {} },
  });
  // 2025-01 .. 2026-02: Dining rises 100 -> 1400; Groceries flat 1000; 2026-02 is the partial month
  const cashflow = Array.from({ length: 14 }, (_, i) => {
    const y = 2025 + Math.floor(i / 12), m = (i % 12) + 1;
    return cfm({ month: `${y}-${String(m).padStart(2, "0")}`, spend: 1100 + i * 100, by_category: cat(100 + i * 100, 1000) });
  });
  const grid = budgetGrid({ vocab: V, cashflow, txns: [], inProgress: "2026-02", months: 6 });

  it("covers exactly the six full months before the month in progress — never the month in progress itself", () => {
    expect(grid.months).toEqual(["2025-08", "2025-09", "2025-10", "2025-11", "2025-12", "2026-01"]);
  });
  it("a month a row was dated ahead into is no column either, and moves no baseline (the shared full-month cut)", () => {
    const ahead = [...cashflow, cfm({ month: "2026-03", spend: 90_000, by_category: cat(90_000, 1000) })];
    const g = budgetGrid({ vocab: V, cashflow: ahead, txns: [], inProgress: "2026-02", months: 6 });
    expect(g).toEqual(grid);
    expect(g.rows.every((r) => r.cells.every((c) => c.month < "2026-02"))).toBe(true);
    expect(g.fixedVsDiscretionary.map((m) => m.month)).not.toContain("2026-03");
    expect(budgetGrid({ vocab: V, cashflow, txns: [], inProgress: null }).months, "no month in progress: no column").toEqual([]);
  });
  it("each cell is budgetRows' median baseline, actual and variance for that month", () => {
    const dining = grid.rows.find((r) => r.category === "Dining")!;
    const jan = dining.cells[5];
    expect(jan.month).toBe("2026-01");
    expect(jan.actual).toBe(1300);
    // trailing-12 median before 2026-01: Dining 100..1200 -> median 650
    expect(jan.baseline).toBe(650);
    expect(jan.variance).toBe(650);
    expect(dining.source).toBe("auto");
  });
  it("the baseline never reads the partial month: it is the trailing medians before each column", () => {
    // 2026-02 (partial) holds an absurd Dining total; no column's baseline may move because of it
    const spiked = cashflow.map((m) => m.month === "2026-02" ? { ...m, by_category: cat(900_000, 1000) } : m);
    const g = budgetGrid({ vocab: V, cashflow: spiked, txns: [], inProgress: "2026-02", months: 6 });
    expect(g.rows.find((r) => r.category === "Dining")!.cells.map((c) => c.baseline))
      .toEqual(grid.rows.find((r) => r.category === "Dining")!.cells.map((c) => c.baseline));
  });
  it("slope follows the actuals; a flat category has none; a pinned budget is 'set'", () => {
    expect(grid.rows.find((r) => r.category === "Dining")!.slope).toBeCloseTo(100, 6);
    expect(grid.rows.find((r) => r.category === "Groceries")!.slope).toBeCloseTo(0, 6);
    const pinned = budgetGrid({ vocab: V, cashflow, txns: [], overrides: { Groceries: 900 }, inProgress: "2026-02" });
    const g = pinned.rows.find((r) => r.category === "Groceries")!;
    expect(g.source).toBe("set");
    expect(g.cells.every((c) => c.baseline === 900 && c.variance === 100)).toBe(true);
  });
  it("a phone's three columns list every row, in the order, the six do: a category that cleared the floor only earlier stays", () => {
    // Shopping is 60 in Sep 2025 and 49 afterwards: on the six it clears the floor once, on the last three never
    const shop = cashflow.map((m) => ({ ...m, by_category: { ...m.by_category,
      Shopping: { total: m.month === "2025-09" ? 60 : 49, by_subcategory: {} } } }));
    const six = budgetGrid({ vocab: V, cashflow: shop, txns: [], inProgress: "2026-02", months: 6 });
    const three = budgetGrid({ vocab: V, cashflow: shop, txns: [], inProgress: "2026-02", months: 3, rowMonths: 6 });
    expect(six.rows.some((r) => r.category === "Shopping")).toBe(true);
    expect(three.months).toEqual(["2025-11", "2025-12", "2026-01"]);
    expect(three.rows.map((r) => r.category), "the same rows in the same order").toEqual(six.rows.map((r) => r.category));
    expect(three.rows.find((r) => r.category === "Shopping")!.cells.map((c) => c.actual)).toEqual([49, 49, 49]);
    expect(three.fixedVsDiscretionary.map((m) => m.month), "the split is the columns' own").toEqual(three.months);
    // the control: the three alone, deciding their own rows, drop it
    const alone = budgetGrid({ vocab: V, cashflow: shop, txns: [], inProgress: "2026-02", months: 3 });
    expect(alone.rows.some((r) => r.category === "Shopping")).toBe(false);
  });
  it("drops a category under the $50 noise floor in every month, and never lists Internal", () => {
    expect(grid.rows.some((r) => r.category === "Tiny")).toBe(false);
    // the fixture HAS Internal spend every month (and a pin on it): only the exclusion keeps it out
    expect(cashflow.every((m) => m.by_category.Internal.total === 5000)).toBe(true);
    expect(grid.rows.some((r) => r.category === "Internal")).toBe(false);
    const pinnedInternal = budgetGrid({ vocab: V, cashflow, txns: [], overrides: { Internal: 300 }, inProgress: "2026-02" });
    expect(pinnedInternal.rows.some((r) => r.category === "Internal")).toBe(false);
  });
  it("keeps a category any one of whose cells clears the floor, and ranks rows by total |variance|", () => {
    const blip = cashflow.map((m) => m.month === "2025-12"
      ? { ...m, by_category: { ...m.by_category, Tiny: { total: 400, by_subcategory: {} } } } : m);
    const g = budgetGrid({ vocab: V, cashflow: blip, txns: [], inProgress: "2026-02" });
    expect(g.rows.some((r) => r.category === "Tiny")).toBe(true);
    expect(g.rows[0].category).toBe("Dining");                                 // the only one with real variance
    const weight = (r: (typeof g.rows)[number]) => r.cells.reduce((s, c) => s + Math.abs(c.variance), 0);
    expect(g.rows.map(weight)).toEqual([...g.rows.map(weight)].sort((a, b) => b - a));
  });
  it("a month with no history before it has no baseline: it is not a column (its spend is not shown as $0)", () => {
    // coverage starts 2025-01 -> 2025-01 has no month before it to take a median of
    const g = budgetGrid({ vocab: V, cashflow: cashflow.slice(0, 5), txns: [], inProgress: "2025-05", months: 6 });
    expect(g.months).toEqual(["2025-02", "2025-03", "2025-04"]);
    expect(g.rows.every((r) => r.cells.length === 3)).toBe(true);
  });
  it("a net-refund month keeps its real (negative) actual rather than a fabricated 0", () => {
    const refund = cashflow.map((m) => m.month === "2026-01"
      ? { ...m, by_category: { ...m.by_category, Dining: { total: -80, by_subcategory: {} } } } : m);
    const jan = budgetGrid({ vocab: V, cashflow: refund, txns: [], inProgress: "2026-02" }).rows.find((r) => r.category === "Dining")!.cells[5];
    expect(jan.actual).toBe(-80);
    expect(jan.variance).toBe(-80 - jan.baseline);
  });
  it("splits each month's spend into fixed (essential by default) and discretionary from the rows", () => {
    const tx = txFactory({ type: "purchase" });
    const txns = [
      tx({ date: "2026-01-05", category: "Groceries", subcategory: "Food", amount: -400 }),
      tx({ date: "2026-01-09", category: "Housing", subcategory: "Mortgage", amount: -2000 }),
      tx({ date: "2026-01-12", category: "Dining", subcategory: "Restaurants", amount: -150 }),
      tx({ date: "2026-01-20", category: "Dining", subcategory: "Restaurants", amount: 50 }),     // a refund reduces it
      tx({ date: "2026-02-01", category: "Dining", subcategory: null, amount: -999 }),            // the partial month: ignored
      tx({ date: "2026-01-15", category: "Internal", subcategory: null, amount: -500, type: "transfer" }),
    ];
    const g = budgetGrid({ vocab: V, cashflow, txns, inProgress: "2026-02", months: 2 });
    expect(g.fixedVsDiscretionary).toEqual([
      { month: "2025-12", fixed: 0, discretionary: 0 },
      { month: "2026-01", fixed: 2400, discretionary: 100 },
    ]);
  });
  it("honours this device's essential toggles (the Planning source), per category / subcategory row", () => {
    const tx = txFactory({ type: "purchase" });
    const txns = [
      tx({ date: "2026-01-05", category: "Groceries", subcategory: "Food", amount: -400 }),
      tx({ date: "2026-01-09", category: "Housing", subcategory: "Mortgage", amount: -2000 }),
      tx({ date: "2026-01-12", category: "Dining", subcategory: "Restaurants", amount: -100 }),
      tx({ date: "2026-01-13", category: "Dining", subcategory: "Coffee", amount: -30 }),
      tx({ date: "2026-01-14", category: "Pets", subcategory: null, amount: -70 }),                  // essential by default
    ];
    const split = (essentialOverrides?: Record<string, boolean>) =>
      budgetGrid({ vocab: V, cashflow, txns, essentialOverrides, inProgress: "2026-02", months: 1 }).fixedVsDiscretionary[0];
    expect(split()).toMatchObject({ fixed: 2470, discretionary: 130 });
    expect(split({ "Dining / Restaurants": true, "Housing / Mortgage": false, "Pets / —": false }))
      .toMatchObject({ fixed: 500, discretionary: 2100 });            // 400 groceries + 100 restaurants; the rest flips
    expect(split({ "Dining / Restaurants": true }), "Coffee is a different row").toMatchObject({ fixed: 2570, discretionary: 30 });
  });
  it("a category rename keeps the fixed side fixed and the transfer bucket out of the rows", () => {
    const tx = txFactory({ type: "purchase" });
    const R = categoryVocab(vocabAssumptions([
      { from: { category: "Groceries" }, to: { category: "Food" } },
      { from: { category: "Pets" }, to: { category: "Animals" } },
      { from: { category: "Internal" }, to: { category: "Transfers" } },
    ]));
    const shipped = [
      tx({ date: "2026-01-05", category: "Groceries", subcategory: "Food", amount: -400 }),
      tx({ date: "2026-01-12", category: "Dining", subcategory: "Restaurants", amount: -100 }),
      tx({ date: "2026-01-14", category: "Pets", subcategory: null, amount: -70 }),
    ];
    const renamed = [
      tx({ date: "2026-01-05", category: "Food", subcategory: "Food", amount: -400 }),
      tx({ date: "2026-01-12", category: "Dining", subcategory: "Restaurants", amount: -100 }),
      tx({ date: "2026-01-14", category: "Animals", subcategory: null, amount: -70 }),
    ];
    const split = (vocab: typeof V, txns: typeof shipped, essentialOverrides?: Record<string, boolean>) =>
      budgetGrid({ vocab, cashflow, txns, essentialOverrides, inProgress: "2026-02", months: 1 });
    const before = split(V, shipped);
    expect(before.fixedVsDiscretionary[0]).toMatchObject({ fixed: 470, discretionary: 100 });
    expect(split(R, renamed).fixedVsDiscretionary).toEqual(before.fixedVsDiscretionary);
    expect(split(V, renamed).fixedVsDiscretionary[0], "control: unrenamed, the renamed rows are discretionary").toMatchObject({ fixed: 0, discretionary: 570 });
    // the "as toggled" count is about the same rows: a toggle that agrees with the default moves nothing
    expect(split(R, renamed, { "Food / Food": true }).toggledRows).toBe(0);
    expect(split(R, renamed, { "Food / Food": false }).toggledRows).toBe(1);
    // the renamed transfer bucket is not a budget line, and the unrenamed vocabulary would list it (control)
    const cf = cashflow.map((m) => ({ ...m, by_category: { ...m.by_category, Transfers: m.by_category.Internal } }));
    expect(budgetRows(R, cf, "2026-01").some((r) => r.category === "Transfers")).toBe(false);
    expect(budgetRows(V, cf, "2026-01").some((r) => r.category === "Transfers")).toBe(true);
  });
  it("a month the cash flow lacks is skipped, not fabricated", () => {
    const sparse = cashflow.filter((m) => m.month !== "2025-11");
    expect(budgetGrid({ vocab: V, cashflow: sparse, txns: [], inProgress: "2026-02", months: 6 }).months).not.toContain("2025-11");
  });
  it("budgetRows keeps its noise floor by default and lifts it on request", () => {
    expect(budgetRows(V, cashflow, "2026-01").some((r) => r.category === "Tiny")).toBe(false);
    expect(budgetRows(V, cashflow, "2026-01", {}, 0).some((r) => r.category === "Tiny")).toBe(true);
  });
  it("an empty cash flow is an empty grid, not a crash", () => {
    expect(budgetGrid({ vocab: V, cashflow: [], txns: [], inProgress: null })).toEqual({ months: [], rows: [], fixedVsDiscretionary: [], toggledRows: 0 });
  });
  it("the $50 floor is exact: a category at exactly $50 is kept (grid and budgetRows), at $49.99 it is not", () => {
    // 2025-01 .. 2025-05; 2025-05 is the partial month, 2025-01 has no earlier month, so the columns are 02..04
    const edge = (total: number) => Array.from({ length: 5 }, (_, i) =>
      cfm({ month: `2025-0${i + 1}`, by_category: { Edge: { total, by_subcategory: {} } } }));
    expect(budgetGrid({ vocab: V, cashflow: edge(50), txns: [], inProgress: "2025-05" }).rows.map((r) => r.category)).toEqual(["Edge"]);
    expect(budgetGrid({ vocab: V, cashflow: edge(49.99), txns: [], inProgress: "2025-05" }).rows).toEqual([]);
    expect(budgetRows(V, edge(50), "2025-04").map((r) => r.category)).toEqual(["Edge"]);
    expect(budgetRows(V, edge(49.99), "2025-04")).toEqual([]);
  });
  it("a slash subcategory is ONE row by its whole name: Water/Trash is fixed, News/Media is not, toggles use the full key", () => {
    const tx = txFactory({ type: "purchase" });
    const txns = [
      tx({ date: "2026-01-02", category: "Utilities", subcategory: "Water/Trash", amount: -600 }),
      tx({ date: "2026-01-03", category: "Subscriptions", subcategory: "News/Media", amount: -120 }),
      tx({ date: "2026-01-04", category: "Utilities", subcategory: "Electric/Gas", amount: -300 }),
    ];
    const split = (essentialOverrides?: Record<string, boolean>) =>
      budgetGrid({ vocab: V, cashflow, txns, essentialOverrides, inProgress: "2026-02", months: 1 }).fixedVsDiscretionary[0];
    expect(split()).toMatchObject({ fixed: 900, discretionary: 120 });
    expect(split({ "Subscriptions / News/Media": true })).toMatchObject({ fixed: 1020, discretionary: 0 });
    expect(split({ "Utilities / Water/Trash": false })).toMatchObject({ fixed: 300, discretionary: 720 });
    // a toggle saved by the old (truncating) model still counts until the row is toggled again, and the full key wins
    expect(split({ "Subscriptions / News": true })).toMatchObject({ fixed: 1020, discretionary: 0 });
    expect(split({ "Subscriptions / News": true, "Subscriptions / News/Media": false })).toMatchObject({ fixed: 900, discretionary: 120 });
  });
  it("toggledRows counts the distinct spend rows in the window whose answer this device's toggles changed", () => {
    const tx = txFactory({ type: "purchase" });
    const txns = [
      tx({ date: "2026-01-05", category: "Groceries", subcategory: "Food", amount: -400 }),
      tx({ date: "2026-01-06", category: "Groceries", subcategory: "Food", amount: -40 }),       // same row twice: counts once
      tx({ date: "2026-01-12", category: "Dining", subcategory: "Restaurants", amount: -100 }),
      tx({ date: "2025-12-12", category: "Dining", subcategory: "Coffee", amount: -10 }),
      tx({ date: "2026-02-03", category: "Pets", subcategory: null, amount: -70 }),               // the partial month: not in the window
    ];
    const toggled = (essentialOverrides?: Record<string, boolean>) =>
      budgetGrid({ vocab: V, cashflow, txns, essentialOverrides, inProgress: "2026-02", months: 2 }).toggledRows;
    expect(toggled()).toBe(0);
    expect(toggled({ "Groceries / Food": false, "Dining / Restaurants": true })).toBe(2);
    expect(toggled({ "Groceries / Food": true, "Dining / Restaurants": false })).toBe(0);         // the same as the default: no change
    expect(toggled({ "Pets / —": false })).toBe(0);                                                // only in the partial month
    expect(toggled({ "Housing / Mortgage": false })).toBe(0);                                      // a row with no spend here
    expect(toggled({ "Dining / Coffee": true })).toBe(1);                                          // the earlier month counts
  });
});

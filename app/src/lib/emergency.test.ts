// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { Transaction } from "../data/types";
import { dataFirstDay, monthInProgress, monthsBetween, type RunRateWindow } from "./agg";
import { cfmFactory, firstWeekData, oneFullMonthData, txFactory, vocabAssumptions } from "./__fixtures__";
import { categoryVocab } from "./categories";
import {
  emergencyCoverage, emergencySpendRows, emergencyWindow, essentialMonthly, isEssential,
  legacySpendKey, overrideFor, spendKey, toggleEssential, toggleMovesRow, trailingYearCoreSpend,
} from "./emergency";

const V = categoryVocab(vocabAssumptions());

const base = txFactory();
const tx = (date: string, category: string, sub: string | null, amount: number,
            type = "purchase" as const): Transaction =>
  base({ date, category, subcategory: sub, amount, type });

/** June 2026 in progress with a long history: the essentials average the six full months December to May */
const SIX: RunRateWindow = { from: "2025-12", to: "2026-05", months: 6 };
/** the twelve full months June 2025 to May 2026 */
const TWELVE: RunRateWindow = { from: "2025-06", to: "2026-05", months: 12 };

describe("essential classification defaults", () => {
  it("keeps the keep-paying-anyway categories", () => {
    expect(V.isEssentialDefault("Housing", "Mortgage")).toBe(true);
    expect(V.isEssentialDefault("Utilities", "Electric/Gas")).toBe(true);
    expect(V.isEssentialDefault("Kids", "Childcare")).toBe(true);
    expect(V.isEssentialDefault("Groceries", "—")).toBe(true);
  });
  it("drops the cuttable ones", () => {
    expect(V.isEssentialDefault("Housing", "Home Improvement")).toBe(false);
    expect(V.isEssentialDefault("Travel", "Lodging")).toBe(false);
    expect(V.isEssentialDefault("Dining", "Restaurants")).toBe(false);
    expect(V.isEssentialDefault("Kids", "Activities")).toBe(false);
  });
});

describe("spendKey / isEssential (one row's essential answer, shared with the Budget split)", () => {
  it("spendKey is exactly the key Planning's toggles are stored under (SpendRow.key)", () => {
    const rows = emergencySpendRows(V, [
      tx("2026-02-01", "Pets", null, -600),                // null subcategory -> "—"
      tx("2026-02-02", "Housing", "Mortgage", -2538),
    ], SIX);
    expect(rows.map((r) => r.key).sort()).toEqual([spendKey("Housing", "Mortgage"), spendKey("Pets", null)].sort());
    expect(spendKey("Pets", null)).toBe("Pets / —");
    expect(spendKey(null, null)).toBe("Uncategorized / —");
  });
  it("is the default when the device set nothing, and the device's toggle (either way) when it did", () => {
    expect(isEssential(V, "Housing", "Mortgage")).toBe(true);
    expect(isEssential(V, "Dining", "Restaurants")).toBe(false);
    expect(isEssential(V, "Dining", "Restaurants", { "Dining / Restaurants": true })).toBe(true);
    expect(isEssential(V, "Housing", "Mortgage", { "Housing / Mortgage": false })).toBe(false);
    expect(isEssential(V, "Pets", null, { "Pets / —": false })).toBe(false);
  });
  it("a toggle is for that exact category / subcategory row only", () => {
    const overrides = { "Dining / Restaurants": true };
    expect(isEssential(V, "Dining", "Coffee", overrides)).toBe(false);
    expect(isEssential(V, "Travel", "Restaurants", overrides)).toBe(false);
  });
});

// Many taxonomy subcategories contain a "/" (Electric/Gas, Water/Trash, Parking/Tolls, News/Media,
// Books/Games, Hair/Beauty, Venmo/Zelle Unknown). The row is category + the WHOLE subcategory.
describe("slash subcategories", () => {
  const txns = [
    tx("2026-02-01", "Utilities", "Water/Trash", -600),
    tx("2026-02-02", "Subscriptions", "News/Media", -120),
    tx("2026-02-03", "Subscriptions", "News/Other", -60),
    tx("2026-02-04", "Utilities", "Electric/Gas", -300),
    tx("2026-02-05", "Cash & Misc", "Venmo/Zelle Unknown", -90),
  ];
  const rows = emergencySpendRows(V, txns, SIX);
  const row = (category: string, sub: string) => rows.find((r) => r.category === category && r.subcategory === sub);

  it("a row keeps its whole subcategory, and subcategories sharing a first segment stay separate rows", () => {
    expect(row("Utilities", "Water/Trash")?.key).toBe("Utilities / Water/Trash");
    expect(row("Subscriptions", "News/Media")?.key).toBe("Subscriptions / News/Media");
    expect(row("Subscriptions", "News/Other")?.key).toBe("Subscriptions / News/Other");
    expect(row("Utilities", "Electric/Gas")?.key).toBe("Utilities / Electric/Gas");
    expect(row("Cash & Misc", "Venmo/Zelle Unknown")?.key).toBe("Cash & Misc / Venmo/Zelle Unknown");
    expect(rows).toHaveLength(5);
    expect(row("Subscriptions", "News/Media")!.monthly).toBe(120 / 6);          // not merged with News/Other
  });
  it("classifies by the full pair: Utilities / Water/Trash is essential, News/Media is not", () => {
    expect(row("Utilities", "Water/Trash")!.essential).toBe(true);
    expect(row("Subscriptions", "News/Media")!.essential).toBe(false);
    expect(row("Utilities", "Electric/Gas")!.essential).toBe(true);
    expect(isEssential(V, "Utilities", "Water/Trash")).toBe(true);
  });
  it("spendKey equals SpendRow.key for every row - the key Planning writes is the key every reader looks up", () => {
    for (const r of rows) expect(spendKey(r.category, r.subcategory), r.key).toBe(r.key);
    expect(rows.map((r) => r.key).sort()).toEqual([
      spendKey("Cash & Misc", "Venmo/Zelle Unknown"), spendKey("Subscriptions", "News/Media"), spendKey("Subscriptions", "News/Other"),
      spendKey("Utilities", "Electric/Gas"), spendKey("Utilities", "Water/Trash")].sort());
  });
  it("a device toggle under the full key moves exactly that row", () => {
    const flipped = essentialMonthly(rows, { "Subscriptions / News/Media": true, "Utilities / Water/Trash": false });
    const base = essentialMonthly(rows);
    expect(flipped - base).toBeCloseTo((120 - 600) / 6);
  });
});

// The old model cut "Category/Sub" at its first two "/" pieces, so a toggle on a slash row was stored
// under the truncated key ("Utilities / Electric"). Those stored toggles keep working until the row is
// toggled again, which writes the full key.
describe("legacy truncated toggle keys", () => {
  it("legacySpendKey is the key the old model used, or null when it would be the same key", () => {
    expect(legacySpendKey("Subscriptions", "News/Media")).toBe("Subscriptions / News");
    expect(legacySpendKey("Utilities", "Water/Trash")).toBe("Utilities / Water");
    expect(legacySpendKey("Dining", "Restaurants")).toBeNull();
    expect(legacySpendKey("Pets", null)).toBeNull();
  });
  it("overrideFor: the full key, else the legacy key, else nothing - the full key always wins", () => {
    expect(overrideFor({ "Subscriptions / News": true }, "Subscriptions", "News/Media")).toBe(true);
    expect(overrideFor({ "Subscriptions / News": true, "Subscriptions / News/Media": false }, "Subscriptions", "News/Media")).toBe(false);
    expect(overrideFor({ "Subscriptions / News/Media": false, "Subscriptions / News": true }, "Subscriptions", "News/Media")).toBe(false);
    expect(overrideFor({ "Subscriptions / News": true }, "Subscriptions", "Streaming")).toBeUndefined();
    expect(overrideFor({}, "Subscriptions", "News/Media")).toBeUndefined();
    expect(overrideFor({ "Dining / Restaurants": true }, "Dining", "Restaurants")).toBe(true);
  });
  it("every reader honours a stored legacy toggle: isEssential, essentialMonthly, emergencyCoverage", () => {
    const legacy = { "Subscriptions / News": true, "Utilities / Water": false };
    expect(isEssential(V, "Subscriptions", "News/Media", legacy)).toBe(true);
    expect(isEssential(V, "Utilities", "Water/Trash", legacy)).toBe(false);
    const txns = [tx("2026-02-02", "Subscriptions", "News/Media", -120), tx("2026-02-01", "Utilities", "Water/Trash", -600)];
    const rows = emergencySpendRows(V, txns, SIX);
    expect(essentialMonthly(rows, legacy)).toBeCloseTo(120 / 6);
    expect(emergencyCoverage(V, txns, SIX, 1000, legacy).essential).toBeCloseTo(120 / 6);
  });
  it("the next toggle writes the FULL key (which then wins) and leaves the rest of the document alone", () => {
    const txns = [tx("2026-02-02", "Subscriptions", "News/Media", -120)];
    const [r] = emergencySpendRows(V, txns, SIX);
    const stored = { "Subscriptions / News": true, "Pets / —": false };
    const next = toggleEssential(stored, r);                       // it reads as essential (legacy), so this turns it off
    expect(next).toEqual({ "Subscriptions / News": true, "Pets / —": false, "Subscriptions / News/Media": false });
    expect(isEssential(V, "Subscriptions", "News/Media", next)).toBe(false);
    expect(toggleEssential({}, r)).toEqual({ "Subscriptions / News/Media": true });   // default off -> on
  });
});

describe("essentialLabels (the list the Budget footnote is generated from)", () => {
  it("every listed category and pair really is essential by default - the list cannot drift from the rule", () => {
    const { categories, pairs } = V.essentialLabels();
    expect(categories.length).toBeGreaterThan(0);
    expect(pairs.length).toBeGreaterThan(0);
    for (const c of categories) expect(V.isEssentialDefault(c, "anything"), c).toBe(true);
    for (const label of pairs) {
      const [category, sub] = label.split(" / ", 2);        // a subcategory may hold a "/": only the first separator splits
      expect(V.isEssentialDefault(category, label.slice(category.length + 3)), label).toBe(true);
      expect(sub.length).toBeGreaterThan(0);
    }
  });
  it("names the two kinds readably: whole categories, then 'Category / Sub' pairs", () => {
    const { categories, pairs } = V.essentialLabels();
    expect(categories).toContain("Utilities");
    expect(pairs).toContain("Housing / Mortgage");
    expect(pairs).not.toContain("Housing / Rent/HOA");        // not a subcategory in the taxonomy
  });
});

describe("emergencyWindow: the months the essentials average", () => {
  const cfm = cfmFactory();
  const months = (first: string, last: string) => monthsBetween(first, last).map((month) => cfm({ month }));
  it("the trailing six full months before the month in progress once the history is long enough", () => {
    expect(emergencyWindow(months("2024-01", "2026-06"), "2026-06", "2024-01-05")).toEqual(SIX);
  });
  it("fewer while the history is shorter, never reaching back before the first full month, and saying how many", () => {
    expect(emergencyWindow(months("2026-02", "2026-06"), "2026-06", "2026-02-01")).toEqual({ from: "2026-02", to: "2026-05", months: 4 });
    expect(emergencyWindow(months("2026-02", "2026-06"), "2026-06", "2026-02-14"), "February is covered in part")
      .toEqual({ from: "2026-03", to: "2026-05", months: 3 });
  });
  it("none below three full months: a household in its first week, and one with one full month", () => {
    for (const d of [firstWeekData(), oneFullMonthData()]) {
      expect(emergencyWindow(d.cashflow, monthInProgress(d), dataFirstDay(d.transactions))).toBeNull();
    }
  });
});

describe("emergencySpendRows", () => {
  it("uses a trailing 6-FULL-month window, the months before the month in progress, so ended expenses drop out", () => {
    const txns = [
      // childcare that ended before the window: must not appear
      tx("2025-10-15", "Kids", "Childcare", -3500),
      // current daycare inside the window
      tx("2026-01-10", "Kids", "Childcare", -1200),
      tx("2026-03-10", "Kids", "Childcare", -1200),
      // one-off check excluded entirely
      tx("2026-04-20", "Cash & Misc", "Checks", -4000),
      // the month in progress excluded (window ends 2026-05-31), and a row dated into a month not begun
      tx("2026-06-05", "Groceries", null, -999),
      tx("2026-07-02", "Groceries", null, -999),
      tx("2026-02-12", "Groceries", null, -600),
    ];
    const rows = emergencySpendRows(V, txns, SIX);
    const childcare = rows.find((r) => r.subcategory === "Childcare")!;
    expect(childcare.monthly).toBe(2400 / 6);            // only in-window months
    expect(rows.find((r) => r.subcategory === "Checks")).toBeUndefined();
    expect(rows.find((r) => r.category === "Groceries")!.monthly).toBe(100);
  });

  it("a shorter window averages over the months it spans, not over six", () => {
    const rows = emergencySpendRows(V, [tx("2026-03-12", "Groceries", null, -600), tx("2026-01-12", "Groceries", null, -999)],
      { from: "2026-02", to: "2026-05", months: 4 });
    expect(rows.find((r) => r.category === "Groceries")!.monthly, "January is before the window").toBe(150);
  });

  it("essentialMonthly honors defaults and user overrides", () => {
    const txns = [
      tx("2026-02-01", "Housing", "Mortgage", -2538),
      tx("2026-02-02", "Travel", "Lodging", -1200),
    ];
    const rows = emergencySpendRows(V, txns, SIX);
    expect(essentialMonthly(rows)).toBeCloseTo(2538 / 6);
    // user decides travel is essential and mortgage isn't (their call)
    const flipped = essentialMonthly(rows, {
      "Housing / Mortgage": false, "Travel / Lodging": true,
    });
    expect(flipped).toBeCloseTo(1200 / 6);
  });

  it("refunds net against spending and transfers are ignored", () => {
    const txns = [
      tx("2026-02-01", "Groceries", null, -500),
      tx("2026-02-15", "Groceries", null, 100),          // refund
      tx("2026-02-20", "Internal", "transfer", -9999, "transfer" as any),
    ];
    const rows = emergencySpendRows(V, txns, SIX);
    expect(rows.find((r) => r.category === "Groceries")!.monthly).toBeCloseTo(400 / 6);
    expect(rows.find((r) => r.category === "Internal")).toBeUndefined();
  });
});

describe("emergencyCoverage (the gauge's and the runway KPI's one arithmetic)", () => {
  const txns = [                                       // in SIX, 2025-12..2026-05:
    tx("2026-01-10", "Groceries", "—", -600),         // essential by default: $100/mo
    tx("2026-02-10", "Dining", "Restaurants", -1200),  // discretionary by default: $200/mo
  ];
  it("divides liquid cash by the essentials, and by everything", () => {
    const c = emergencyCoverage(V, txns, SIX, 1200);
    expect(c.essential).toBe(100);
    expect(c.everything).toBe(300);
    expect(c.months).toBe(12);
    expect(c.monthsAll).toBe(4);
    expect(c.window).toEqual(SIX);
  });
  it("applies the device's toggles", () => {
    const c = emergencyCoverage(V, txns, SIX, 1200, { "Dining / Restaurants": true, "Groceries / —": false });
    expect(c.essential).toBe(200);
    expect(c.months).toBe(6);
  });
  it("nothing essential to divide by is null (unknown), never 0 months", () => {
    expect(emergencyCoverage(V, [], SIX, 1200).months).toBeNull();
    expect(emergencyCoverage(V, [], SIX, 1200).monthsAll).toBeNull();
    const off = emergencyCoverage(V, txns, SIX, 1200, { "Groceries / —": false });
    expect(off.essential).toBe(0);
    expect(off.months).toBeNull();
    expect(off.monthsAll).toBe(4);
  });
  it("with no cash to divide (no snapshot) the months are unknown and the essentials are still the rows'", () => {
    const c = emergencyCoverage(V, txns, SIX, null);
    expect([c.months, c.monthsAll]).toEqual([null, null]);
    expect([c.essential, c.everything]).toEqual([100, 300]);
  });
  it("with no window (too little history) there is no row: nothing to average, the essentials and months unknown", () => {
    const c = emergencyCoverage(V, txns, null, 1200);
    expect(c.window).toBeNull();
    expect(c.rows).toEqual([]);
    expect([c.essential, c.everything], "no window: unknown, never $0 a month").toEqual([null, null]);
    expect(c.months).toBeNull();
    expect(c.monthsAll).toBeNull();
    expect(emergencySpendRows(V, txns, null)).toEqual([]);
  });
  it("a household in its first week or with one full month: no essentials and no runway, from its own rows", () => {
    for (const d of [firstWeekData(), oneFullMonthData()]) {
      const c = emergencyCoverage(V, d.transactions,
        emergencyWindow(d.cashflow, monthInProgress(d), dataFirstDay(d.transactions)), 12_000);
      expect([c.essential, c.months, c.window], "never a runway from a week or a month").toEqual([null, null, null]);
    }
  });
  it("a window with no spending in it is a real 0 a month (the window exists; nothing was spent in it)", () => {
    expect(emergencyCoverage(V, [], SIX, 1200).essential).toBe(0);
  });
});

describe("trailingYearCoreSpend: the twelve full months before the month in progress", () => {
  it("is unknown with no window (too little history): no year to sum, never a spend of 0", () => {
    expect(trailingYearCoreSpend(V, [], null)).toBeNull();
    for (const d of [firstWeekData(), oneFullMonthData()]) {
      const w = emergencyWindow(d.cashflow, monthInProgress(d), dataFirstDay(d.transactions));
      expect(trailingYearCoreSpend(V, d.transactions, w), "a week, or one full month, is no year of spending").toBeNull();
    }
  });
  it("sums the twelve months before the month in progress: not the month itself, nor a row dated into the next", () => {
    const txns = [
      tx("2025-05-31", "Groceries", null, -1000),        // the day before the window
      tx("2025-06-01", "Groceries", null, -100),         // its first day
      tx("2026-05-31", "Groceries", null, -200),         // its last day
      tx("2026-06-10", "Groceries", null, -999),         // the month in progress
      tx("2026-07-02", "Groceries", null, -999),         // dated into a month that has not begun
    ];
    expect(trailingYearCoreSpend(V, txns, TWELVE)).toEqual({ annual: 300, months: 12 });
  });
  it("over fewer than twelve full months it is annualised, and says from how many", () => {
    const txns = [tx("2026-03-03", "Groceries", null, -300), tx("2026-05-20", "Groceries", null, -300),
      tx("2026-02-27", "Groceries", null, -999)];        // before the window
    expect(trailingYearCoreSpend(V, txns, { from: "2026-03", to: "2026-05", months: 3 })).toEqual({ annual: 2400, months: 3 });
  });
});

// A configured rename moves the categories the rows carry; the essentials, the money-movement exclusions and the
// core-spend figure must move with it, or the cash runway and the forecast floor silently shrink.
describe("a category rename keeps the essentials essential and the money movements out", () => {
  const R = categoryVocab(vocabAssumptions([
    { from: { category: "Transport" }, to: { category: "Transportation" } },
    { from: { category: "Cash & Misc", subcategory: "Checks" }, to: { category: "Cash & Misc", subcategory: "Paper Checks" } },
    { from: { category: "Housing", subcategory: "Mortgage" }, to: { category: "Housing", subcategory: "Home Loan" } },
  ]));
  // the same household twice: once under the shipped names, once under the renamed ones
  const shipped = [
    tx("2026-01-10", "Transport", "Fuel", -600),
    tx("2026-02-10", "Housing", "Mortgage", -1200),
    tx("2026-02-11", "Dining", "Restaurants", -1800),
    tx("2026-03-10", "Cash & Misc", "Checks", -4000),
    tx("2026-03-11", "Cash & Misc", "ATM", -300),
  ];
  const renamed = [
    tx("2026-01-10", "Transportation", "Fuel", -600),
    tx("2026-02-10", "Housing", "Home Loan", -1200),
    tx("2026-02-11", "Dining", "Restaurants", -1800),
    tx("2026-03-10", "Cash & Misc", "Paper Checks", -4000),
    tx("2026-03-11", "Cash & Misc", "ATM", -300),
  ];
  it("the renamed rows give the same rows, essentials and coverage as the shipped ones", () => {
    const before = emergencySpendRows(V, shipped, SIX);
    const after = emergencySpendRows(R, renamed, SIX);
    expect(after.map((r) => [r.monthly, r.essential])).toEqual(before.map((r) => [r.monthly, r.essential]));
    expect(after.map((r) => r.essential).sort()).toEqual([false, true, true]);     // fuel and the mortgage are essential, dining is not
    expect(after.find((r) => r.category === "Cash & Misc")).toBeUndefined();      // checks (renamed) and ATM stay out of the basis
    expect(essentialMonthly(after)).toBeCloseTo((600 + 1200) / 6);
    const cov = emergencyCoverage(R, renamed, SIX, 1200);
    expect(cov.essential).toBeCloseTo(300);
    expect(cov.everything).toBeCloseTo(600);
    expect(cov.months).toBeCloseTo(4);
  });
  it("without the rename the same renamed rows would drop out - the vocabulary is what keeps them (control)", () => {
    expect(emergencyCoverage(V, renamed, SIX, 1200).essential).toBe(0);
    expect(emergencySpendRows(V, renamed, SIX).some((r) => r.subcategory === "Paper Checks")).toBe(true);
  });
  it("the per-row readers agree: isEssential and toggleMovesRow", () => {
    expect(isEssential(R, "Transportation", "Fuel")).toBe(true);
    expect(isEssential(R, "Housing", "Home Loan")).toBe(true);
    expect(isEssential(R, "Dining", "Restaurants")).toBe(false);
    expect(toggleMovesRow(R, "Transportation", "Fuel", { "Transportation / Fuel": true })).toBe(false);     // already its default side
    expect(toggleMovesRow(R, "Transportation", "Fuel", { "Transportation / Fuel": false })).toBe(true);
  });
  it("the trailing-year core spend leaves out the renamed checks and the ATM cash but keeps the rest", () => {
    expect(trailingYearCoreSpend(R, renamed, TWELVE)?.annual).toBe(600 + 1200 + 1800);
    expect(trailingYearCoreSpend(V, shipped, TWELVE)?.annual).toBe(600 + 1200 + 1800);
    expect(trailingYearCoreSpend(V, renamed, TWELVE)?.annual).toBe(600 + 1200 + 1800 + 4000);             // control: the renamed checks would count
  });
});

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it, vi } from "vitest";
import type { AppData, CashflowMonth, Transaction } from "../data/types";
import { appDataFactory, snapFactory, txFactory, vocabAssumptions } from "./__fixtures__";
import {
  comparePeriods, countedRows, dataFirstDay, dataThrough, datedAhead, fullMonths, ingestDay, monthInProgress, partialMonths,
  prevMonth, runRateWindow, savingsRate,
} from "./agg";
import { budgetGrid } from "./budgets";
import { categoryVocab } from "./categories";
import { emergencyCoverage, emergencyWindow, trailingYearCoreSpend } from "./emergency";
import { forecastCashflow } from "./forecast";
import { cashRunway, incomeStreams, monthlyBurn, pnl, recentMonths, savingsKpis, savingsPace } from "./lifeSummary";
import { burnTrend, savingsRateTrend } from "./lifeTrends";
import { lastCompleteDay } from "./monthToDate";
import { passiveIncomeTTM } from "./passiveIncome";

// Small synthetic households for the month in progress: every month from January 2025 holds a salary on the 1st, a
// dividend on the 3rd, groceries on the 5th, the power bill on the 18th, dining on the 25th and shopping on the 28th
// (a different amount each month, so a moved window moves every figure), each only while the household's data reaches
// that day. The cash-flow months are summed from the same rows, as the ETL writes them.
const tx = txFactory({ account: "chk", account_name: "Checking" });
const appData = appDataFactory(snapFactory());

const MONTHLY: [number, (month: number) => Partial<Transaction>][] = [
  [1, () => ({ amount: 5000, type: "income", category: "Income", subcategory: "Salary - Alex", vendor: "Employer" })],
  [3, () => ({ amount: 40, type: "dividend", category: "Income", subcategory: "Dividends", vendor: "Broker" })],
  [5, () => ({ amount: -400, category: "Groceries", subcategory: "Groceries", vendor: "Market" })],
  [18, () => ({ amount: -120, category: "Utilities", subcategory: "Electric/Gas", vendor: "Power Co" })],
  [25, () => ({ amount: -150, category: "Dining", subcategory: "Restaurants", vendor: "Cafe" })],
  [28, (i) => ({ amount: -(200 + 25 * i), category: "Shopping", subcategory: "Online", vendor: "Store" })],
];

/** the household's rows from January 2025 through `last` (YYYY-MM-DD) */
function rowsThrough(last: string): Transaction[] {
  const out: Transaction[] = [];
  for (let i = 0; ; i++) {
    const month = `${2025 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}`;
    if (`${month}-01` > last) return out;
    for (const [day, row] of MONTHLY) {
      const date = `${month}-${String(day).padStart(2, "0")}`;
      if (date <= last) out.push(tx({ date, ...row(i) }));
    }
  }
}

/** cashflow_monthly.json's months for these rows: one per month that holds a row, every one in coverage */
function cashflowOf(rows: readonly Transaction[]): CashflowMonth[] {
  const months = new Map<string, CashflowMonth>();
  for (const t of rows) {
    const ym = t.date.slice(0, 7);
    const m = months.get(ym) ?? { month: ym, income: 0, spend: 0, invested: 0, savings_rate: null, by_category: {},
      income_by_subcategory: {}, accounts_covered: ["chk"], full_coverage: true, in_coverage: true };
    months.set(ym, m);
    if (t.type === "income" || t.type === "dividend" || t.type === "interest") {
      m.income += t.amount;
      const sub = t.subcategory ?? "Other";
      m.income_by_subcategory[sub] = (m.income_by_subcategory[sub] ?? 0) + t.amount;
    } else if (t.type === "purchase" || t.type === "fee") {
      m.spend -= t.amount;
      const cat = (m.by_category[t.category ?? "?"] ??= { total: 0, by_subcategory: {} });
      cat.total -= t.amount;
      const sub = t.subcategory ?? "—";
      cat.by_subcategory[sub] = (cat.by_subcategory[sub] ?? 0) - t.amount;
    }
  }
  return [...months.values()].sort((a, b) => a.month.localeCompare(b.month))
    .map((m) => ({ ...m, savings_rate: m.income > 0 ? (m.income - m.spend) / m.income : null }));
}

/** a household whose ingest ran at `generatedAt` (the household's wall clock with its offset) holding `rows` */
function household(generatedAt: string, rows: Transaction[]): AppData {
  const base = appData({});
  return { ...base, manifest: { ...base.manifest, generated_at: generatedAt, transaction_count: rows.length },
    transactions: rows, purchases: rows, cashflow: cashflowOf(rows) };
}

const EVENING_OCT_31 = "2026-10-31T21:00:00-05:00";
const MORNING_NOV_1 = "2026-11-01T08:00:00-05:00";
/** the one row the bank dated ahead: a pharmacy charge three days after the ingest day */
const AHEAD_ROW = (): Transaction =>
  tx({ id: "ahead", date: "2026-11-03", amount: -250, category: "Health", subcategory: "Pharmacy", vendor: "Pharmacy" });

/** the four boundaries of the month in progress, and the morning after a month whose rows still arrive */
const BOUNDARIES = {
  // the ingest on the 1st: the 1st holds a row, but no complete day yet
  first: () => household(MORNING_NOV_1, rowsThrough("2026-11-01")),
  // the ingest on the last day of the month, which holds a row
  lastDay: () => household(EVENING_OCT_31, [...rowsThrough("2026-10-31"), tx({ date: "2026-10-31", amount: 3, type: "interest",
    category: "Income", subcategory: "Interest", vendor: "Bank" })]),
  // the last day again, the rows ending on the 30th, and one row the bank dated three days into November
  ahead: () => household(EVENING_OCT_31, [...rowsThrough("2026-10-30"), tx({ date: "2026-10-30", amount: -60,
    category: "Dining", subcategory: "Coffee", vendor: "Coffee Bar" }), AHEAD_ROW()]),
  // the same household without that row
  notAhead: () => household(EVENING_OCT_31, [...rowsThrough("2026-10-30"), tx({ date: "2026-10-30", amount: -60,
    category: "Dining", subcategory: "Coffee", vendor: "Coffee Bar" })]),
  // statement files not brought up to date: the rows end two months before the ingest
  stale: () => household(EVENING_OCT_31, rowsThrough("2026-08-20")),
  // the morning of the 1st with nothing of the new month yet: October's rows are still arriving
  morningAfter: () => household(MORNING_NOV_1, rowsThrough("2026-10-31")),
};

const newestFull = (d: AppData) => fullMonths(d.cashflow, monthInProgress(d)).at(-1)?.month ?? null;

describe("monthInProgress: the earlier of the newest month holding a row and the ingest month", () => {
  it("the ingest on the 1st: the new month is in progress, the month before is the newest full one", () => {
    const d = BOUNDARIES.first();
    expect(monthInProgress(d)).toBe("2026-11");
    expect(newestFull(d)).toBe("2026-10");
    // "this month so far" has no complete day yet: the last complete day is in the month before
    expect(lastCompleteDay("2026-11-01", d.manifest.generated_at)).toBe("2026-10-31");
  });

  it("the ingest on the month's last day: that month is still in progress (the day itself is still coming in)", () => {
    const d = BOUNDARIES.lastDay();
    expect(monthInProgress(d)).toBe("2026-10");
    expect(newestFull(d)).toBe("2026-09");
  });

  it("a row dated into the next month: the ingest month stays in progress and the next month is no month at all", () => {
    const d = BOUNDARIES.ahead();
    expect(d.cashflow.at(-1)?.month, "the export keeps a month for the row").toBe("2026-11");
    expect(monthInProgress(d)).toBe("2026-10");
    expect(newestFull(d)).toBe("2026-09");
    expect(fullMonths(d.cashflow, monthInProgress(d)).some((m) => m.month === "2026-11")).toBe(false);
    expect(countedRows(d.transactions, monthInProgress(d)).some((t) => t.date.slice(0, 7) === "2026-11"), "no November row is counted").toBe(false);
    expect(countedRows(d.transactions, monthInProgress(d))).toHaveLength(d.transactions.length - 1);
    expect(monthInProgress(BOUNDARIES.notAhead()), "the same as without the row").toBe("2026-10");
  });

  it("files that end two months before the ingest: their own newest month stays in progress, no later month is invented", () => {
    const d = BOUNDARIES.stale();
    expect(monthInProgress(d)).toBe("2026-08");
    expect(newestFull(d)).toBe("2026-07");
    expect(fullMonths(d.cashflow, monthInProgress(d)).map((m) => m.month).filter((m) => m >= "2026-08")).toEqual([]);
  });

  it("the morning of the 1st with no row of the new month: the month before is still in progress", () => {
    const d = BOUNDARIES.morningAfter();
    expect(monthInProgress(d)).toBe("2026-10");
    expect(newestFull(d)).toBe("2026-09");
  });

  it("no month holds a row: no month is in progress, and none is full", () => {
    const d = household(EVENING_OCT_31, []);
    expect(monthInProgress(d)).toBeNull();
    expect(fullMonths(d.cashflow, null)).toEqual([]);
    expect(countedRows([], null)).toEqual([]);
  });

  it("reads no clock: the exports alone decide, whatever the instant", () => {
    const d = BOUNDARIES.ahead();
    try {
      vi.useFakeTimers();
      const answers = ["2026-10-01T00:00:00Z", "2026-11-15T12:00:00Z", "2031-06-30T23:59:59Z"].map((at) => {
        vi.setSystemTime(new Date(at));
        return monthInProgress(d);
      });
      expect(answers).toEqual(["2026-10", "2026-10", "2026-10"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("the newest month is the later of the newest row's and the newest cash-flow month's", () => {
    const base = household(EVENING_OCT_31, rowsThrough("2026-09-30"));
    expect(monthInProgress({ ...base, cashflow: [] }), "the rows alone (no cash-flow file)").toBe("2026-09");
    expect(monthInProgress({ ...base, transactions: [] }), "the cash-flow months alone").toBe("2026-09");
  });

  it("an unreadable ingest stamp leaves the newest month holding a row in progress", () => {
    const d = BOUNDARIES.ahead();
    expect(monthInProgress({ ...d, manifest: { ...d.manifest, generated_at: "" } })).toBe("2026-11");
    expect(monthInProgress({ ...d, manifest: { ...d.manifest, generated_at: "unknown" } })).toBe("2026-11");
  });
});

describe("fullMonths: the coverage months before the month in progress", () => {
  it("leaves out the months before the coverage floor, the month in progress and any month after it", () => {
    const cf = cashflowOf([...rowsThrough("2026-10-30"), AHEAD_ROW()])
      .map((m) => ({ ...m, in_coverage: m.month >= "2025-03" }));
    const full = fullMonths(cf, "2026-10").map((m) => m.month);
    expect(full[0]).toBe("2025-03");
    expect(full.at(-1)).toBe("2026-09");
    expect(full).toHaveLength(19);
  });
});

/** every trailing figure a page shows, read as the pages read it: through the month in progress `inProgress` */
function trailingFigures(d: AppData, inProgress: string | null) {
  const V = categoryVocab(vocabAssumptions());
  const twelve = recentMonths(d.cashflow, inProgress, 12);
  return {
    fullMonths: fullMonths(d.cashflow, inProgress).map((m) => m.month),
    cashFlowKpis: savingsKpis(d.cashflow, inProgress),
    homeTwelveMonthRate: savingsRate(twelve),
    freeCashAndPace: savingsPace(d.cashflow, inProgress, dataFirstDay(d.transactions)),
    burn: monthlyBurn(d.cashflow, inProgress),
    streams: incomeStreams(d.cashflow, inProgress, 3, Number.POSITIVE_INFINITY),
    trendSavings: savingsRateTrend(d.cashflow, inProgress),
    trendBurn: burnTrend(d.cashflow, inProgress),
    budgetGrid: budgetGrid({ vocab: V, cashflow: d.cashflow, txns: d.transactions, inProgress, months: 6 }),
    emergencyFund: emergencyCoverage(V, d.transactions, emergencyWindow(d.cashflow, inProgress, dataFirstDay(d.transactions)), 10_000),
    runway: cashRunway(V, d.transactions, null, emergencyWindow(d.cashflow, inProgress, dataFirstDay(d.transactions))),
    passiveIncome: passiveIncomeTTM(d.transactions, V, d.cashflow, inProgress),
    coreSpend: trailingYearCoreSpend(V, d.transactions, runRateWindow(d.cashflow, inProgress, 12, dataFirstDay(d.transactions))),
    forecast: forecastCashflow({ cashflow: d.cashflow, inProgress, firstDay: dataFirstDay(d.transactions), startCash: 10_000 }),
    profitAndLoss: inProgress === null ? null : pnl(d.cashflow, "2025-10", prevMonth(inProgress)),
  };
}

describe("a row dated ahead changes no trailing figure", () => {
  it("every trailing rate, average and window is the same with the row and without it", () => {
    const withRow = BOUNDARIES.ahead(), without = BOUNDARIES.notAhead();
    const figures = trailingFigures(withRow, monthInProgress(withRow));
    expect(figures).toEqual(trailingFigures(without, monthInProgress(without)));
    expect(figures.fullMonths.at(-1)).toBe("2026-09");
    expect(figures.budgetGrid.months.at(-1)).toBe("2026-09");
    expect(figures.trendSavings.at(-1)?.month).toBe("2026-09");
    expect(figures.emergencyFund.window).toEqual({ from: "2026-04", to: "2026-09", months: 6 });
    expect(figures.forecast?.months[0].month, "the forecast starts with the month in progress").toBe("2026-10");
  });

  it("control: taking the newest month holding a row as the month in progress moves them", () => {
    const withRow = BOUNDARIES.ahead();
    expect(trailingFigures(withRow, "2026-11")).not.toEqual(trailingFigures(withRow, monthInProgress(withRow)));
  });

  it("stale files: every window ends on the month before the data's own newest month, and nothing later is invented", () => {
    const d = BOUNDARIES.stale();
    const figures = trailingFigures(d, monthInProgress(d));
    expect(figures.cashFlowKpis.through).toBe("2026-07");
    expect(figures.budgetGrid.months.at(-1)).toBe("2026-07");
    expect(figures.trendBurn.at(-1)?.month).toBe("2026-07");
    expect(figures.emergencyFund.window).toEqual({ from: "2026-02", to: "2026-07", months: 6 });
    expect(figures.forecast?.months[0].month).toBe("2026-08");
  });

  it("the ingest on the 1st: every window ends on the month before, which is full", () => {
    const d = BOUNDARIES.first();
    const figures = trailingFigures(d, monthInProgress(d));
    expect(figures.cashFlowKpis.through).toBe("2026-10");
    expect(figures.budgetGrid.months.at(-1)).toBe("2026-10");
    expect(figures.emergencyFund.window).toEqual({ from: "2026-05", to: "2026-10", months: 6 });
  });
});

describe("dated ahead: a row after the ingest day", () => {
  it("is a row dated after the day the ingest ran, in its month or the next", () => {
    expect(ingestDay(EVENING_OCT_31)).toBe("2026-10-31");
    expect(datedAhead("2026-11-03", EVENING_OCT_31)).toBe(true);
    expect(datedAhead("2026-10-31", EVENING_OCT_31), "the ingest day itself is not ahead").toBe(false);
    expect(datedAhead("2026-10-25", "2026-10-20T09:00:00-05:00"), "ahead inside the ingest month").toBe(true);
    expect(datedAhead("2026-11-03", ""), "no readable stamp, nothing is ahead").toBe(false);
    expect(dataThrough("2026-11-03", EVENING_OCT_31), "the data runs through the ingest day").toBe("2026-10-31");
  });
});

describe("the month in progress is never compared or charted whole", () => {
  it("Explore compares the month in progress month-to-date even on its last day", () => {
    const p = comparePeriods("2026-10-31", "mom", "2026-10");
    expect(p.mtdDays).toBe(31);
    expect(p.prev).toEqual(["2026-09-01", "2026-09-30"]);
    expect(comparePeriods("2026-09-30", "mom", "2026-10").mtdDays, "a month before it is whole").toBeNull();
    expect(comparePeriods("2026-10-31", "mom").mtdDays, "with no month in progress named, the end decides").toBeNull();
  });

  it("the monthly chart stars the month in progress even when the window ends on its last day", () => {
    expect([...partialMonths(["2026-09", "2026-10"], "2026-09-01", "2026-10-31", "2026-10")]).toEqual(["2026-10"]);
    expect(partialMonths(["2026-08", "2026-09"], "2026-08-01", "2026-09-30", "2026-10").size, "a whole earlier window").toBe(0);
  });
});

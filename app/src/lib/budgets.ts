// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Budget vs actual with zero-config auto-baselines: a category's "budget" is
// the median of its monthly spend over the trailing 12 full months, unless
// an explicit budget is set in config/assumptions.yaml (budgets: key).
import type { CashflowMonth, Transaction } from "../data/types";
import { coverageMonths, fullMonths, isSpend, monthsBetween, trailingWindow } from "./agg";
import type { CategoryVocab } from "./categories";
import { isEssential, spendKey, toggleMovesRow } from "./emergency";
import { median, slope } from "./stats";

/** a category under this in both its baseline and its actual is noise, not a budget line */
export const BUDGET_FLOOR = 50;

export interface BudgetRow {
  category: string;
  budget: number;
  actual: number;
  variance: number;           // actual − budget; positive = over
  source: "auto" | "set";
}

/** `minDollars` is the noise floor (BUDGET_FLOOR by default): a category under it in both its baseline
 *  and its actual is left out; the grid lifts it (-Infinity) to read every category's cell itself. */
export function budgetRows(
  vocab: CategoryVocab, cashflow: CashflowMonth[], month: string, overrides: Record<string, number> = {}, minDollars = BUDGET_FLOOR,
): BudgetRow[] {
  const hist = coverageMonths(cashflow)
    .filter((m) => m.month < month)
    .slice(-12);
  const target = cashflow.find((m) => m.month === month);
  if (!hist.length || !target) return [];
  const cats = new Set([
    ...hist.flatMap((m) => Object.keys(m.by_category)),
    ...Object.keys(target.by_category),
    ...Object.keys(overrides),
  ]);
  const rows: BudgetRow[] = [];
  for (const c of cats) {
    if (vocab.isInternal(c)) continue;
    const auto = median(hist.map((m) => m.by_category[c]?.total ?? 0));
    const budget = overrides[c] ?? auto;
    const actual = target.by_category[c]?.total ?? 0;
    if (budget < minDollars && actual < minDollars) continue;     // noise floor
    rows.push({
      category: c, budget, actual, variance: actual - budget,
      source: overrides[c] !== undefined ? "set" : "auto",
    });
  }
  return rows.sort((a, b) => Math.abs(b.variance) - Math.abs(a.variance));
}

export interface BudgetCell { month: string; baseline: number; actual: number; variance: number }
export interface BudgetGridRow { category: string; source: "auto" | "set"; cells: BudgetCell[]; slope: number }
export interface BudgetGrid {
  months: string[];
  rows: BudgetGridRow[];
  /** per month: spend in essential categories vs everything else (lib/emergency.ts `isEssential`:
   *  the defaults, flipped by this device's toggles when `essentialOverrides` is passed) */
  fixedVsDiscretionary: { month: string; fixed: number; discretionary: number }[];
  /** how many distinct category / subcategory spend rows in these months this device's toggles moved
   *  from their default side (0 with no toggles, or when every toggle is for a row not spent in them) */
  toggledRows: number;
}

/** Budget vs actual over the trailing `months` FULL months before `inProgress` (the month in progress, agg.ts
 *  `monthInProgress`). Every column comes from the shared full-month cut (`fullMonths`), so the month in progress and
 *  a month a row was dated ahead into are never a column or a baseline input; a month the cash flow lacks is skipped,
 *  and so is one with no earlier coverage month to take a median of (its baseline does not exist, and showing its spend
 *  against a made-up $0 would be a fabrication). Each cell comes from budgetRows for that month, so the median / pinned
 *  baseline lives in one place; a category stays when any of its cells clears the floor; rows sort by total |variance|.
 *  `rowMonths` (at least `months`) is the window those two read: a phone's three columns take the rows, and their order,
 *  of the six a wider screen shows, so the two tables never list different categories. The fixed / discretionary split
 *  sums the spend rows themselves, month by month. */
export function budgetGrid(p: {
  vocab: CategoryVocab;
  cashflow: CashflowMonth[]; txns: readonly Transaction[];
  overrides?: Record<string, number>;
  /** this device's essential toggles (lib/efundOverrides): which spend counts as fixed */
  essentialOverrides?: Record<string, boolean>;
  inProgress: string | null; months?: number; rowMonths?: number;
}): BudgetGrid {
  const overrides = p.overrides ?? {};
  const full = fullMonths(p.cashflow, p.inProgress).map((m) => m.month);
  const lastFull = full[full.length - 1];
  if (!lastFull) return { months: [], rows: [], fixedVsDiscretionary: [], toggledRows: 0 };
  const usable = new Set(full);
  const covered = coverageMonths(p.cashflow);
  const windowOf = (n: number) => {
    const [from, to] = trailingWindow(lastFull, n);
    return monthsBetween(from, to).filter((m) => usable.has(m) && covered.some((c) => c.month < m));
  };
  const shown = p.months ?? 6;
  const months = windowOf(shown);
  const rowWindow = windowOf(Math.max(shown, p.rowMonths ?? shown));
  const byMonth = new Map(rowWindow.map((m) =>
    [m, new Map(budgetRows(p.vocab, p.cashflow, m, overrides, Number.NEGATIVE_INFINITY).map((r) => [r.category, r]))] as const));
  const categories = new Set<string>();
  for (const rows of byMonth.values()) for (const c of rows.keys()) categories.add(c);
  const rows: BudgetGridRow[] = [];
  const weight = new Map<string, number>();
  for (const c of categories) {
    // absent from a month's rows = no spend in it or in the 12 before it: a true zero, not a gap
    const cellOf = (m: string): BudgetCell => {
      const r = byMonth.get(m)!.get(c);
      return r ? { month: m, baseline: r.budget, actual: r.actual, variance: r.variance }
               : { month: m, baseline: 0, actual: 0, variance: 0 };
    };
    const decide = rowWindow.map(cellOf);
    if (!decide.some((x) => x.baseline >= BUDGET_FLOOR || x.actual >= BUDGET_FLOOR)) continue;
    const cells = months.map(cellOf);
    rows.push({ category: c, source: overrides[c] !== undefined ? "set" : "auto", cells, slope: slope(cells.map((x) => x.actual)) });
    weight.set(c, decide.reduce((s, x) => s + Math.abs(x.variance), 0));
  }
  rows.sort((a, b) => weight.get(b.category)! - weight.get(a.category)!);
  const split = new Map<string, BudgetGrid["fixedVsDiscretionary"][number]>(
    months.map((m) => [m, { month: m, fixed: 0, discretionary: 0 }]));
  const moved = new Map<string, boolean>();           // row key -> did a toggle change its side
  for (const t of p.txns) {
    const bucket = isSpend(t) ? split.get(t.date.slice(0, 7)) : undefined;
    if (!bucket) continue;
    const essential = isEssential(p.vocab, t.category, t.subcategory, p.essentialOverrides);
    if (essential) bucket.fixed -= t.amount;
    else bucket.discretionary -= t.amount;
    const key = spendKey(t.category, t.subcategory);
    if (!moved.has(key)) moved.set(key, toggleMovesRow(p.vocab, t.category, t.subcategory, p.essentialOverrides ?? {}));
  }
  return { months, rows, fixedVsDiscretionary: months.map((m) => split.get(m)!),
           toggledRows: [...moved.values()].filter(Boolean).length };
}

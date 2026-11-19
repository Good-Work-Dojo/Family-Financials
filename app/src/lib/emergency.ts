// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Emergency-fund "essential spend" model.
//
// Basis: trailing 6 FULL months, the months before the month in progress (so
// spending that has stopped before the window naturally drops out), cut at the
// first full month and averaged over the months it really spans, and none below
// agg.ts RUN_RATE_MIN_MONTHS full months; broken down by
// category/subcategory with an essential-by-default classification the user
// can toggle. One-off money movements (checks, wires, ATM) are excluded from
// the basis entirely.
import type { CashflowMonth, Transaction } from "../data/types";
import { isSpend, type RunRateWindow, runRateWindow, totalSpend, trailingWindow } from "./agg";
import { type CategoryVocab, NO_CATEGORY } from "./categories";

/** the full months the essentials average once the history is long enough */
export const EMERGENCY_WINDOW_MONTHS = 6;

/** THE window the emergency fund's essentials average, for the gauge, the runway KPI and Second house's cushion alike:
 *  the trailing six full months before `inProgress` (the month in progress, agg.ts `monthInProgress`), fewer when the
 *  history is shorter, none (null) below RUN_RATE_MIN_MONTHS full months (agg.ts runRateWindow; `firstDay`, the data's
 *  first transaction day, leaves out a first month the data covers in part). */
export function emergencyWindow(cashflow: CashflowMonth[], inProgress: string | null,
                                firstDay: string | null): RunRateWindow | null {
  return runRateWindow(cashflow, inProgress, EMERGENCY_WINDOW_MONTHS, firstDay);
}

/** the first and last day (YYYY-MM-DD) of a run-rate window */
const windowDays = (w: RunRateWindow): [string, string] => trailingWindow(w.to, w.months);

export interface SpendRow {
  key: string;                // "Housing / Mortgage"
  category: string;
  subcategory: string;
  monthly: number;            // avg $/month over the window
  essential: boolean;         // default classification (user can override)
}

/** the key one spend row's essential toggle is stored under - exactly `SpendRow.key`, so the Planning
 *  gauge that writes the toggles (`lib/efundOverrides`) and every other reader agree on the row. The
 *  subcategory is the WHOLE subcategory: many contain a "/" (Electric/Gas, News/Media, Parking/Tolls). */
export function spendKey(category: string | null, subcategory: string | null): string {
  return `${category ?? NO_CATEGORY} / ${subcategory ?? "—"}`;
}

/** The key the OLD emergency model stored a row's toggle under: it cut "Category/Sub" at the first two
 *  "/" pieces, so a slash subcategory was truncated at its slash ("Utilities / Electric" for
 *  Electric/Gas). null when that would be the same key. Device toggles saved before the fix still sit
 *  under it, so `overrideFor` falls back to it. */
export function legacySpendKey(category: string | null, subcategory: string | null): string | null {
  const legacy = `${(category ?? NO_CATEGORY).split("/")[0]} / ${(subcategory ?? "—").split("/")[0]}`;
  return legacy === spendKey(category, subcategory) ? null : legacy;
}

/** This device's toggle for one row: the full key, else a legacy truncated key (until the row is
 *  toggled again, which writes the full key), else undefined = no opinion. Every reader of the toggles
 *  goes through this, never `overrides[key]` directly. */
export function overrideFor(overrides: Record<string, boolean>, category: string | null,
                            subcategory: string | null): boolean | undefined {
  const full = overrides[spendKey(category, subcategory)];
  if (full !== undefined) return full;
  const legacy = legacySpendKey(category, subcategory);
  return legacy === null ? undefined : overrides[legacy];
}

/** Is this category / subcategory essential on this device: its toggle when it set one, else the
 *  default (`vocab.isEssentialDefault`). The per-row form of `essentialMonthly`'s `overrideFor(...) ?? r.essential`. */
export function isEssential(vocab: CategoryVocab, category: string | null, subcategory: string | null,
                            overrides: Record<string, boolean> = {}): boolean {
  return overrideFor(overrides, category, subcategory) ?? vocab.isEssentialDefault(category ?? "", subcategory ?? "");
}

/** Did this device's toggles move this row off its default side? The one definition behind every "as toggled"
 *  note (the Budget grid's fixed-vs-discretionary card): a toggle for a row
 *  with no spend, or one that agrees with the default, changes nothing and is not counted. */
export function toggleMovesRow(vocab: CategoryVocab, category: string | null, subcategory: string | null,
                               overrides: Record<string, boolean>): boolean {
  return isEssential(vocab, category, subcategory, overrides) !== vocab.isEssentialDefault(category ?? "", subcategory ?? "");
}

/** the toggles after flipping one row: its FULL key is written (so it wins over a legacy truncated
 *  one) and every other entry is kept */
export function toggleEssential(overrides: Record<string, boolean>, row: SpendRow): Record<string, boolean> {
  return { ...overrides, [row.key]: !(overrideFor(overrides, row.category, row.subcategory) ?? row.essential) };
}

/** The spend rows of `window` (emergencyWindow), each averaged over the months the window spans. With no window (too
 *  little history, or no transaction) there is nothing to average over: no row. */
export function emergencySpendRows(vocab: CategoryVocab, txns: Transaction[], window: RunRateWindow | null): SpendRow[] {
  if (window === null) return [];
  const [from, to] = windowDays(window);
  // rows are keyed by the (category, whole subcategory) pair itself - never re-split from a joined
  // string, which truncated a subcategory at its own "/" (Utilities/Electric/Gas became "Utilities / Electric")
  const sums = new Map<string, { category: string; subcategory: string; total: number }>();
  for (const t of txns) {
    if (!isSpend(t) || t.date < from || t.date > to) continue;
    const sub = t.subcategory ?? "—";
    if (vocab.isMoneyMovementSub(sub)) continue;
    const category = t.category ?? NO_CATEGORY;
    const key = spendKey(category, sub);
    const cur = sums.get(key) ?? { category, subcategory: sub, total: 0 };
    cur.total -= t.amount;
    sums.set(key, cur);
  }
  const rows: SpendRow[] = [];
  for (const [key, { category, subcategory, total }] of sums) {
    const monthly = total / window.months;
    if (monthly < 5) continue;             // noise floor; shown as excluded
    rows.push({ key, category, subcategory, monthly, essential: vocab.isEssentialDefault(category, subcategory) });
  }
  rows.sort((a, b) => b.monthly - a.monthly);
  return rows;
}

/** a year of core spend and the full months it is worked out from (12, or fewer annualised) */
export interface CoreSpend { annual: number; months: number }

/** The CORE spend of a year: what the household spent over `window` (agg.ts runRateWindow: the twelve full months before
 *  the month in progress, or as many as there are), less the one-off money movements (checks, wires, ATM) - not
 *  recurring living costs - so the FIRE number is not inflated by a single large check; over fewer than twelve months it
 *  is annualised (× 12 / months), and `months` says how many. Full months, as every trailing figure: the month in
 *  progress is only part of one, and a row dated into a month that has not begun is in none. With no window (fewer than
 *  RUN_RATE_MIN_MONTHS full months) there is no such year: null (unknown), never a spend of 0. */
export function trailingYearCoreSpend(vocab: CategoryVocab, txns: readonly Transaction[],
                                      window: RunRateWindow | null): CoreSpend | null {
  if (window === null) return null;
  const [from, to] = windowDays(window);
  const spent = totalSpend(txns.filter((t) =>
    t.date >= from && t.date <= to && !vocab.isMoneyMovementSub(t.subcategory)));
  return { annual: spent * 12 / window.months, months: window.months };
}

export function essentialMonthly(rows: SpendRow[],
                                 overrides: Record<string, boolean> = {}): number {
  return rows.reduce((s, r) => s + ((overrideFor(overrides, r.category, r.subcategory) ?? r.essential) ? r.monthly : 0), 0);
}

export interface EmergencyCoverage {
  rows: SpendRow[];
  /** the months averaged; null with too little history (or no transaction), when no row is either */
  window: RunRateWindow | null;
  /** $/mo of the rows counted as essential (the defaults, flipped by the device's toggles); null with no window (too
   *  little history to average), never $0 a month */
  essential: number | null;
  /** $/mo of every row: the full current lifestyle; null with no window */
  everything: number | null;
  /** months of essentials (of everything) the cash covers; null when the divisor is 0 - no spending
   *  history yet, or every row toggled off - or there is no cash figure: "unknown", never "0 months of runway" */
  months: number | null;
  monthsAll: number | null;
}

/** The emergency-fund arithmetic behind BOTH the Planning gauge and the header's runway KPI, so the
 *  two can never disagree: the same rows, the same window (`emergencyWindow`), the same toggles
 *  (`lib/efundOverrides`). With no window (fewer than RUN_RATE_MIN_MONTHS full months, or no transaction) there is no
 *  row, so the essentials, everything and both month counts are null (unknown); the month counts are null too with no
 *  cash figure (`liquidCash` null: no snapshot). */
export function emergencyCoverage(
  vocab: CategoryVocab, txns: Transaction[], window: RunRateWindow | null, liquidCash: number | null,
  overrides: Record<string, boolean> = {},
): EmergencyCoverage {
  const rows = emergencySpendRows(vocab, txns, window);
  const essential = window === null ? null : essentialMonthly(rows, overrides);
  const everything = window === null ? null : rows.reduce((s, r) => s + r.monthly, 0);
  const over = (n: number | null) => (liquidCash !== null && n !== null && n > 0 ? liquidCash / n : null);
  return { rows, window, essential, everything, months: over(essential), monthsAll: over(everything) };
}

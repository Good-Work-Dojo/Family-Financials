// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Passive income over the trailing twelve full months, or as many as the data holds: the dividends and interest the
// Investments KPIs total. The month in progress is partial, so the window ends at the last
// full month before it, and it starts no earlier than the first full month (the coverage floor).
import type { CashflowMonth, Transaction } from "../data/types";
import { fullMonths, isIncome, monthsBetween, trailingWindow } from "./agg";
import type { CategoryVocab } from "./categories";

export interface PassiveIncome {
  dividends: number; interest: number; total: number;
  /** the months the window spans (12 once the history is long enough): the label's real count */
  months: number;
}

/** Income rows with a positive amount whose subcategory the vocabulary classifies as passive income (so a renamed
 *  subcategory still counts), summed per kind over the trailing twelve full months (agg.ts `fullMonths`: the coverage
 *  months before `inProgress`, the month in progress), cut at the first of them when the data holds fewer. With no full
 *  month there is no window: the figure is unknown (null), never 0 over months that precede the data. */
export function passiveIncomeTTM(transactions: Transaction[], vocab: CategoryVocab, cashflow: CashflowMonth[],
                                 inProgress: string | null): PassiveIncome | null {
  const full = fullMonths(cashflow, inProgress);
  if (!full.length) return null;
  const first = full[0].month, last = full[full.length - 1].month;
  const [trailingFrom, to] = trailingWindow(last, 12);
  const from = trailingFrom < `${first}-01` ? `${first}-01` : trailingFrom;
  const passive = { dividends: 0, interest: 0 };
  for (const t of transactions) {
    if (!isIncome(t) || t.amount <= 0 || t.date < from || t.date > to) continue;
    const kind = vocab.passiveIncome(t.subcategory);
    if (kind !== null) passive[kind] += t.amount;
  }
  return { ...passive, total: passive.dividends + passive.interest,
    months: monthsBetween(from, last).length };
}

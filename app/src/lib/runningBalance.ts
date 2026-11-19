// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { BalancePoint, Transaction } from "../data/types";
import { isCashLike } from "./accounts";

/** a checking, savings, card or loan account (whatever class it states), or a brokerage's cash-classed account:
 *  balances that move only with posted rows (never a brokerage's positions) */
export function runningBalanceEligible(a: { kind: string; asset_class: string }): boolean {
  return a.kind === "checking" || a.kind === "savings" || a.kind === "credit-card" || a.kind === "loan" || isCashLike(a);
}

const toCents = (v: number) => Math.round(v * 100);

/** The newest observation, a live feed's over a CSV-derived one when they share a date (the footnote says
 *  "latest feed balance"; the CSV anchor is the account's frozen file, the feed is current). */
function newest(points: readonly BalancePoint[]): BalancePoint {
  const isFeed = (p: BalancePoint) => p.source !== "csv";
  return points.reduce((a, p) => (p.date > a.date || (p.date === a.date && isFeed(p) && !isFeed(a)) ? p : a));
}

/** The END-OF-DAY balance of every date that has a row, keyed by date and anchored on the NEWEST balance
 *  observation: dates up to its day are reconstructed backwards (before = after − the day's net), dates
 *  after it add forward from it. Keyed by date, not by row: the export's order within one date is
 *  arbitrary, so a balance "after" any one row of a day would be a guess - the day's number is the same
 *  whatever order its rows came in, and the register (TxnTable) decides which displayed row carries it.
 *  Sums in integer cents. null when the account is not eligible or there is no observation to anchor on:
 *  the column is then hidden, never filled with a guess. */
export function runningBalance(rows: readonly Transaction[], points: readonly BalancePoint[],
                               account: { kind: string; asset_class: string }): Map<string, number> | null {
  if (!runningBalanceEligible(account) || points.length === 0) return null;
  const anchor = newest(points);
  const net = new Map<string, number>();
  for (const t of rows) net.set(t.date, (net.get(t.date) ?? 0) + toCents(t.amount));
  const dates = [...net.keys()].sort();
  const out = new Map<string, number>();
  let bal = toCents(anchor.balance);
  for (let i = dates.length - 1; i >= 0; i--) {
    if (dates[i] > anchor.date) continue;
    out.set(dates[i], bal / 100);
    bal -= net.get(dates[i])!;
  }
  bal = toCents(anchor.balance);
  for (const d of dates) {
    if (d <= anchor.date) continue;
    bal += net.get(d)!;
    out.set(d, bal / 100);
  }
  return out;
}

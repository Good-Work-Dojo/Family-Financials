// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Retirement balances per person for the Planning projection — from holdings.json PLUS
// balance-only retirement accounts (a feed balance but no positions), each account once.
// So a person whose only retirement account is balance-only projects from that balance, not
// from $0, and the FIRE crossing counts that account.
import type { Holding, Snapshot } from "../data/types";
import { balanceOnlyHoldings } from "./holdings";

export interface RetirementBalances {
  byPerson: Record<string, number>;   // every owner present, 0 when nothing is mapped to them
  asOf: Record<string, string>;       // newest dated as_of per person (undated sources skipped)
  balanceOnly: string[];              // retirement accounts counted from a feed balance, no positions
  /** the people at least one of whose retirement accounts reported something (a holding, or a balance of any amount, $0
   *  included): for anyone else `byPerson` is 0 for want of a figure, not a balance of 0 */
  reported: string[];
  /** the mapped retirement accounts that reported something, by name: an adult's account missing from it has reported
   *  nothing yet */
  reportedAccounts: string[];
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** each owner's retirement balance from the holdings and the newest snapshot's balance-only retirement rows; null with no
 *  snapshot (no balance has been seen, so no owner's balance is known: never 0 each) */
export function retirementBalances(holdings: readonly Holding[], snapshot: Snapshot,
                                   retirementAccounts: Record<string, string>, owners: readonly string[]): RetirementBalances;
export function retirementBalances(holdings: readonly Holding[], snapshot: Snapshot | null,
                                   retirementAccounts: Record<string, string>,
                                   owners: readonly string[]): RetirementBalances | null;
export function retirementBalances(holdings: readonly Holding[], snapshot: Snapshot | null,
                                   retirementAccounts: Record<string, string>,
                                   owners: readonly string[]): RetirementBalances | null {
  if (snapshot === null) return null;
  const byPerson: Record<string, number> = Object.fromEntries(owners.map((o) => [o, 0]));
  const asOf: Record<string, string> = {};
  const reported = new Set<string>();
  const accounts = new Set<string>();
  const add = (h: Holding) => {
    const who = retirementAccounts[h.account];
    if (who === undefined) return;
    reported.add(who);
    accounts.add(h.account);
    byPerson[who] = (byPerson[who] ?? 0) + h.value;
    // an "unknown" as_of (undated source file) sorts after every ISO date and fmtDate can't parse it
    if (ISO_DATE.test(h.as_of) && (!asOf[who] || h.as_of > asOf[who])) asOf[who] = h.as_of;
  };
  holdings.forEach(add);
  // balanceOnlyHoldings already excludes any account that reports positions, so nothing doubles; a balance of $0 counts
  // (the account reported it: a new 401(k) is a real $0, not a figure nobody has seen)
  const extra = balanceOnlyHoldings(snapshot, holdings, true).filter((h) => h.account in retirementAccounts);
  extra.forEach(add);
  return { byPerson, asOf, balanceOnly: extra.map((h) => h.account), reported: owners.filter((o) => reported.has(o)),
    reportedAccounts: [...accounts].sort() };
}

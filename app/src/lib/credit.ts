// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { BalancePoint } from "../data/types";
import { fmtPct } from "./format";

/** THE card utilisation, Balances' table and Liabilities' tile alike: what a card owes over its limit (a card in
 *  credit owes nothing: 0); null without a limit above 0 - unknown, never 0%. Read from the balance and the limit, not
 *  the export's rounded `utilization`, so every page prints the same figure. */
export function utilization(balance: number, limit: number | null): number | null {
  return limit === null || !(limit > 0) ? null : Math.max(0, -balance) / limit;
}

/** a utilisation as every page prints it: one decimal ("10.6%"), "—" when unknown */
export const fmtUtilization = (u: number | null): string => fmtPct(u, 1);

export interface CardHeadroom {
  /** Σ owed over every card (−balance: cards are stored negative = owed); a card in credit owes nothing, so it adds 0,
   *  never takes its credit off another card's debt (on the balance sheet it is an asset of its own) */
  debt: number;
  /** how many cards are in credit (a balance above zero: the issuer owes the household) */
  inCredit: number;
  /** Σ headroom over the cards where it is known; null - never $0 - when no card's is */
  available: number | null;
  /** how many cards' headroom is known, of how many cards */
  known: number;
  cards: number;
  /** at least one card's headroom is limit − owed (the feed gave a limit but no available-credit figure) */
  derived: boolean;
  /** Σ owed ÷ Σ limit (`utilization`'s terms) over the same `known` cards the headroom counts, so one count describes
   *  both figures; null when no card is counted or one of them has no limit (its utilisation is unknown) */
  utilization: number | null;
}

/** Credit-card headroom from each card's newest balance point. A card's headroom is the feed's own
 *  available-credit figure when it gives one, else limit − owed when the feed gives a limit (never below 0: an
 *  over-limit card has none), else unknown. A 0 "available" beside a limit is the field left empty, not a maxed
 *  card - the same reading as Balances' table, which shows it as "—". */
export function cardHeadroom(latest: readonly BalancePoint[]): CardHeadroom {
  let debt = 0, inCredit = 0, available = 0, known = 0, derived = false, owedLimited = 0, limits = 0, limited = 0;
  for (const p of latest) {
    const owed = -p.balance;
    debt += Math.max(0, owed);
    if (p.balance > 0) inCredit++;
    let head: number | null = null;
    if (p.available != null && p.available > 0) head = p.available;
    else if (p.limit != null) { head = Math.max(0, p.limit - owed); derived = true; }
    if (head === null) continue;
    available += head;
    known++;
    if (utilization(p.balance, p.limit) !== null) {
      owedLimited += Math.max(0, owed);
      limits += p.limit!;
      limited++;
    }
  }
  return {
    debt, inCredit, available: known ? available : null, known, cards: latest.length, derived,
    utilization: known > 0 && limited === known ? owedLimited / limits : null,
  };
}

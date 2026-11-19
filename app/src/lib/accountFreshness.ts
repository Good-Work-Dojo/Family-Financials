// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { AccountMeta } from "../data/types";
import { fmtDate } from "./format";

export interface FreshnessRow { account: AccountMeta; stale: boolean }

/** The Data page's per-account freshness list. `stale` (drawn amber) = the account's transactions stop before
 *  `behind`, the first day of the last full month. The stale rows come FIRST, oldest end first - they are the
 *  ones to look at, and in the export's order one sat below the fold of a long list - then the rest in the
 *  export's order. An account with no transactions at all is not stale (`freshnessEnd` words it differently), and with
 *  no last full month (`behind` null: no transaction yet) no account is. */
export function freshnessRows(accounts: readonly AccountMeta[], behind: string | null): FreshnessRow[] {
  const rows = accounts.map((account) => ({
    account, stale: behind !== null && account.txn_data_end !== null && account.txn_data_end < behind,
  }));
  const stale = rows.filter((r) => r.stale)
    .sort((a, b) => (a.account.txn_data_end ?? "").localeCompare(b.account.txn_data_end ?? ""));   // stable: ties keep order
  return [...stale, ...rows.filter((r) => !r.stale)];
}

/** The right-hand cell of an account's row: "→ Sep 3, 2026" where its transactions end. An account with no transaction
 *  at all says what it does have instead of a placeholder: "balance only" (a feed balance and no transactions, as the
 *  Investments list says it) or "no transactions" (neither - a registry entry still waiting for data). */
export function freshnessEnd(a: Pick<AccountMeta, "txn_data_end" | "latest_balance">): string {
  if (a.txn_data_end) return `→ ${fmtDate(a.txn_data_end)}`;
  return a.latest_balance !== null ? "balance only" : "no transactions";
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Balance-only investment accounts: a feed reports the account's balance but no positions, so
// they never land in holdings.json — yet they are invested assets. Importers: Accounts →
// Investments (portfolio total, allocation, holdings list) and Planning's retirement projection
// (lib/retirement.ts, which counts a balance-only 401k instead of projecting it from $0). Both call
// this one function rather than re-derive it, so the two can never disagree about which accounts exist.
import type { AssetRow, Holding, Snapshot } from "../data/types";
import { HOUSE_CLASS } from "./tokens";

/** asset-row sources that mark a brokerage's, loan's or card's own balance row: its balance on or before the
 *  snapshot date, whether the feed reported it in its newest file, it is carried, or it came from a statement file
 *  (today one source, `simplefin_balance`). A set so a second source is one entry here; a source the ETL emits but
 *  this set lacks would vanish from the portfolio total and the allocation chart with no error anywhere.
 *  Keep in step with etl/networth.py `_row_source` (tests/test_exports.py pins the two agree). */
export const LIVE_BALANCE_SOURCES: ReadonlySet<string> = new Set(["simplefin_balance"]);
// liabilities and non-investment classes are never an invested balance
const NOT_INVESTED: ReadonlySet<string> = new Set(["Cash", "Mortgage", HOUSE_CLASS, "Car", "Other Debt", "Margin"]);

/** the newest snapshot's live-balance investment rows whose account reports no positions, as balance-only holding lines;
 *  none without a snapshot. `withEmpty` keeps a row whose balance is 0 or less (the feed reports the account, at $0 - a
 *  new 401(k)): a reader that asks which accounts reported, not what they are worth, passes it. */
export function balanceOnlyHoldings(snapshot: Snapshot | null, holdings: readonly Holding[], withEmpty = false): Holding[] {
  if (snapshot === null) return [];
  // an account that already reports positions is valued by its balance but still appears in
  // `holdings`; adding it here too would double-count it
  const haveHoldings = new Set(holdings.map((h) => h.account));
  return snapshot.rows
    .filter((r) => LIVE_BALANCE_SOURCES.has(r.source) && (withEmpty || r.value > 0)
      && !NOT_INVESTED.has(r.asset_class) && !haveHoldings.has(r.account))
    .map(rowAsHolding);
}

function rowAsHolding(r: AssetRow): Holding {
  return {
    account: r.account, account_id: r.account_id, institution: r.institution, owner: r.owner, symbol: null,
    name: "Balance only — no security-level detail reported", asset_class: r.asset_class, quantity: null,
    value: r.value, cost_basis: null, as_of: r.as_of ?? "unknown", liquid: r.liquid, notes: "balance only",
  };
}

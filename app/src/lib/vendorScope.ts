// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Which scope the category editor offers and starts on. "Always for <vendor>" (one vendor-scope answer that
// covers every row of the vendor, past and future) is offered only where it can mean what it says, and it is the
// starting choice wherever it is offered and the vendor is one the rulebook lets the family's answers teach:
// a generic descriptor ("Check", "Withdrawal") or a mixed-basket vendor ("Target") starts on "just this one",
// because an "Always" answer there would file every different payment behind the one name alike.
import type { Transaction, VendorEntry } from "../data/types";
import { INCOME_TYPES } from "./agg";

export type AnswerScope = "txn" | "vendor";

/** The vendor an "Always for" answer would cover, or null when that scope is not offered: the rows must all be spend
 *  rows (an income row's question is its stream, and a vendor answer never repaints income), none may be a PART of a
 *  split purchase (a vendor answer never reaches a part, so offering it over one would lie), and every row must share
 *  one non-empty vendor. The editor shows the "Apply to" chips exactly when this is non-null. */
export function offeredVendor(txns: readonly Transaction[]): string | null {
  if (txns.length === 0) return null;
  if (txns.some((t) => INCOME_TYPES.has(t.type) || t.parent_id !== null)) return null;
  const v = txns[0].vendor;
  return v && txns.every((t) => t.vendor === v) ? v : null;
}

/** vendors.json's learning flag by vendor, lower-cased (the ETL matches a vendor answer to its rows case-insensitively) */
export function learnIndex(vendors: readonly VendorEntry[]): ReadonlyMap<string, boolean> {
  return new Map(vendors.map((v) => [v.vendor.toLowerCase(), v.learn]));
}

/** whether the family's answers may teach `vendor` (vendors.json `learn`: not `learn: false`, not in
 *  `learning.exclude`, not a split vendor); null when vendors.json does not list it (or is missing) */
export function vendorLearnable(index: ReadonlyMap<string, boolean>, vendor: string): boolean | null {
  return index.get(vendor.toLowerCase()) ?? null;
}

/** The scope the editor starts on: "vendor" when "Always for" is offered (`offered`, from `offeredVendor`) and the
 *  vendor is known to be learnable; "txn" otherwise - no vendor scope on offer, a generic or mixed-basket vendor, or a
 *  vendor vendors.json does not list (unknown is treated as the cautious case). The start can never be a scope the
 *  editor does not offer. */
export function defaultScope(offered: string | null, learnable: boolean | null): AnswerScope {
  return offered !== null && learnable === true ? "vendor" : "txn";
}

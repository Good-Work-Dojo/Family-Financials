// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { VendorEntry } from "../data/types";

/** The vendor rules the ETL learned from the family's answers, as vendors.json reports them
 *  (`category_source === "learned"`), by name. */
export function activeLearnedRules(vendors: readonly VendorEntry[]): VendorEntry[] {
  return vendors.filter((v) => v.category_source === "learned")
    .sort((a, b) => a.vendor.localeCompare(b.vendor));
}

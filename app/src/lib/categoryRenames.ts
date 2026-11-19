// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { CategoryRename } from "../data/types";

/** etl/enrich/renames.py CategoryRenamer.map, mirrored: first matching entry wins, one rename each
 *  (no chaining - and the ETL refuses a chain or a cycle at load, so the exported list never holds one).
 *  A `from` with a `subcategory` key moves exactly that pair (the key may be null:
 *  rows whose subcategory is null); without one it moves the whole category, each reference keeping
 *  its own subcategory unless `to` names one. */
export function mapCategory(category: string, subcategory: string | null,
                            renames: readonly CategoryRename[]): [string, string | null] {
  for (const r of renames) {
    if (category !== r.from.category) continue;
    if ("subcategory" in r.from) {
      if ((r.from.subcategory ?? null) === subcategory) return [r.to.category, r.to.subcategory ?? null];
    } else {
      return [r.to.category, r.to.subcategory ?? subcategory];
    }
  }
  return [category, subcategory];
}

/** The new name of a whole category, for the references that carry no subcategory (a saved view's
 *  `categories=`, a budget key): only a rename whose `from` names no subcategory moves it, because a
 *  pair rename (Dining/Pubs -> Dining/Bars) says nothing about the category as a whole. */
export function mapCategoryName(category: string, renames: readonly CategoryRename[]): string {
  return mapCategory(category, null, renames.filter((r) => !("subcategory" in r.from)))[0];
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo } from "react";
import { type SavedFilter } from "../lib/prefs";
import { type StaleSavedFilter, validSavedFilters } from "../lib/savedFilters";
import { useData } from "./DataContext";

/** The family's saved views split for THIS device's data: `valid` (every category still exists, a
 *  renamed one already mapped) are offered by the FilterBar menu and ⌘K; `stale` are named on Data with
 *  their reason and stay in the prefs doc. One memo for every surface so they cannot disagree. */
export function useSavedViews(): { valid: SavedFilter[]; stale: StaleSavedFilter[] } {
  const { data, indexes, prefs } = useData();
  const views = prefs.doc.saved_filters;
  return useMemo(
    () => validSavedFilters(views, indexes.categoryList, data.assumptions.category_renames),
    [views, indexes.categoryList, data.assumptions.category_renames]);
}

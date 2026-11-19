// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo } from "react";
import { type SavedReport } from "../lib/prefs";
import { type StaleSavedReport, validSavedReports } from "../lib/savedFilters";
import { useData } from "./DataContext";

/** The family's saved reports split for THIS device's data, exactly as useSavedViews splits the views:
 *  `valid` (every category still exists, a renamed one already mapped) are offered on the Reports tab and
 *  by ⌘K; `stale` are named on Data with their reason and stay in the prefs doc. One memo for every surface
 *  so they cannot disagree. */
export function useSavedReports(): { valid: SavedReport[]; stale: StaleSavedReport[] } {
  const { data, indexes, prefs } = useData();
  const reports = prefs.doc.reports;
  return useMemo(
    () => validSavedReports(reports, indexes.categoryList, data.assumptions.category_renames),
    [reports, indexes.categoryList, data.assumptions.category_renames]);
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { CategoryRename } from "../data/types";
import { parseFilters, SAVED_QUERY_KEYS, serializeFilters, WINDOW_PARAM } from "../filters/filters";
import { mapCategoryName } from "./categoryRenames";
import { MAX_SAVED, type SavedFilter, type SavedRefusal, type SavedReport } from "./prefs";
import { searchFromSpec } from "./reportSpec";

/** carry a stored query's rolling window (`win=`) over to a re-serialized filter query: serializeFilters writes filter keys only */
function withWindow(filters: URLSearchParams, stored: URLSearchParams): URLSearchParams {
  const win = stored.get(WINDOW_PARAM);
  if (win !== null) filters.set(WINDOW_PARAM, win);
  return filters;
}

export interface StaleSavedFilter { filter: SavedFilter; reason: string }

/** A saved view is the Transactions tab's query. Valid when every key is a filter key and every
 *  category (after the renames map) is still in the data; a renamed category is rewritten in the
 *  query. Stale views are returned with a reason, never dropped from the doc: a device with an
 *  older category list must not delete the family's view (prefs are last-write-wins). */
export function validSavedFilters(filters: readonly SavedFilter[], categoryList: readonly string[],
                                  renames: readonly CategoryRename[]): { valid: SavedFilter[]; stale: StaleSavedFilter[] } {
  const known = new Set(categoryList);
  const valid: SavedFilter[] = [], stale: StaleSavedFilter[] = [];
  for (const f of filters) {
    const sp = new URLSearchParams(f.query);
    const unknown = [...sp.keys()].find((k) => !SAVED_QUERY_KEYS.includes(k));
    if (unknown !== undefined) { stale.push({ filter: f, reason: `unknown filter key "${unknown}"` }); continue; }
    const parsed = parseFilters(sp);
    const categories = parsed.categories.map((c) => mapCategoryName(c, renames));
    const gone = categories.find((c) => !known.has(c));
    if (gone !== undefined) { stale.push({ filter: f, reason: `category "${gone}" is no longer in the data` }); continue; }
    const query = categories.some((c, i) => c !== parsed.categories[i])
      ? withWindow(serializeFilters({ ...parsed, categories }), sp).toString() : f.query;
    valid.push(query === f.query ? f : { ...f, query });
  }
  return { valid, stale };
}

/** the router path that opens a saved view: the Transactions tab with the saved query. A link to it
 *  must carry the CARRIED_SEARCH router state, so a view saved without a date window opens all-time
 *  instead of being given the six-month default (filters/useSpendingDefaults.ts). */
export function savedFilterPath(f: SavedFilter): string {
  return `/spending/transactions${f.query ? `?${f.query}` : ""}`;
}

/** The router path that opens a STALE view as it was saved: the Transactions tab with its filters,
 *  parsed defensively (parseFilters/serializeFilters keep only the filter keys, so an unknown key is
 *  dropped; parseFilters itself drops a non-numeric amount and a malformed date, so neither is written back as `NaN`
 *  or as garbage). A deleted category stays in
 *  the filter, so the register honestly shows no rows for it. The link must carry CARRIED_SEARCH too. */
export function staleOpenPath(f: SavedFilter): string {
  const sp = new URLSearchParams(f.query);
  return savedFilterPath({ ...f, query: withWindow(serializeFilters(parseFilters(sp)), sp).toString() });
}

export interface StaleSavedReport { report: SavedReport; reason: string }

/** The same split for saved reports: a report's `spec.filters` is a saved view's query, so it goes through
 *  validSavedFilters (one rule, one reason text), and a renamed category is rewritten inside the spec.
 *  Stale reports stay in the doc for the same reason stale views do. */
export function validSavedReports(reports: readonly SavedReport[], categoryList: readonly string[],
                                  renames: readonly CategoryRename[]): { valid: SavedReport[]; stale: StaleSavedReport[] } {
  const asFilters = reports.map((r) => ({ id: r.id, name: r.name, query: r.spec.filters, created_at: r.created_at }));
  const { valid, stale } = validSavedFilters(asFilters, categoryList, renames);
  const byId = new Map(reports.map((r) => [r.id, r]));
  return {
    valid: valid.map((f) => { const r = byId.get(f.id)!; return f.query === r.spec.filters ? r : { ...r, spec: { ...r.spec, filters: f.query } }; }),
    stale: stale.map((s) => ({ report: byId.get(s.filter.id)!, reason: s.reason })),
  };
}

/** the router path that opens a saved report: the Reports tab with its filters and the four report keys. A link
 *  to it must carry the CARRIED_SEARCH router state, so a report saved without a date window opens all-time
 *  instead of being given the six-month default (filters/useSpendingDefaults.ts). */
export function savedReportPath(r: SavedReport): string {
  const q = searchFromSpec(r.spec).toString();
  return `/spending/reports${q ? `?${q}` : ""}`;
}

/** what a Save box says when the prefs ops refuse the item (SavedRefusal), in the kind's own words; null for a
 *  reason the box handles itself (a blank name just disables Save) or no refusal at all. One wording for the
 *  FilterBar's views and the Reports tab's reports. */
export function saveRefusalText(refusal: SavedRefusal | null, kind: "view" | "report"): string | null {
  switch (refusal) {
    case "count": return `${MAX_SAVED} ${kind}s saved — delete one on Data first`;
    case "full": return `Settings are full — delete a ${kind} on Data first`;
    case "entry": return kind === "view" ? "these filters are too long to save as a view" : "this report is too long to save";
    default: return null;
  }
}

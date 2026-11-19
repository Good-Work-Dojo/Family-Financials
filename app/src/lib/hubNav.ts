// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
/** The router state RouteTabs puts on a tab link that hands a sibling tab's query over to a tab
 *  that shares it (Spending's Explore <-> Transactions). A page that applies a first-visit default
 *  to an empty query reads it to tell "arrived with the previous tab's state, which happens to be
 *  empty (cleared, or the All preset)" from "arrived fresh from the sidebar, a bookmark or another
 *  tab" - the first keeps what the user set, only the second gets the default. Also stamped by every
 *  useFilters/useSort write, so a cleared entry Back lands on carries it too. */
export const CARRIED_SEARCH = { carriedSearch: true } as const;

export function arrivedWithCarriedSearch(state: unknown): boolean {
  return typeof state === "object" && state !== null
    && (state as { carriedSearch?: unknown }).carriedSearch === true;
}

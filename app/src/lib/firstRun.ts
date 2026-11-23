// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The first-run state: whether a household's exports hold anything for the dashboard's pages to show, and if not, which
// of the first-run pages stands in for them (sections/FirstRun.tsx). It reads the exports alone, never the clock.
import type { AppData, Manifest } from "../data/types";

/** "setup": nothing is listed yet, no account and no hand-stated asset (the household itself is described: an ingest
 *  that wrote exports accepted its household.yaml); "waiting": something is listed and no data has arrived yet */
export type FirstRunState = "setup" | "waiting";

/** what the first-run page shows: a first-run state of the exports, or "nothing-built" — no exports at all yet (the
 *  loader found no manifest: data/DataContext.tsx `NothingBuiltError`), which no export can say about itself */
export type FirstRunPage = FirstRunState | "nothing-built";

/** The first-run state of a household's exports, or null when the app shows its pages: null as soon as the exports
 *  hold a transaction, a net-worth snapshot or a point of an account's balance series (a household with transactions
 *  and no snapshot, or with card balances left out of net worth, gets the pages, each of which says what it lacks);
 *  otherwise "setup" when `manifest.setup` counts no account and no hand-stated asset, and "waiting" when it counts
 *  either. */
export function firstRunState(manifest: Pick<Manifest, "setup">,
  data: Pick<AppData, "transactions" | "assets" | "balances">): FirstRunState | null {
  if (data.transactions.length > 0 || data.assets.snapshots.length > 0) return null;
  if (Object.values(data.balances.series).some((points) => points.length > 0)) return null;
  const { accounts, manual_assets } = manifest.setup;
  return accounts === 0 && manual_assets === 0 ? "setup" : "waiting";
}

/** the routes whose own pages stay while the household is in a first-run state: Data (what the last ingest reported)
 *  and Settings */
export const FIRST_RUN_KEEPS: readonly string[] = ["/data", "/settings"];

/** whether the first-run page stands in for the page of `route`, a route pattern of App.tsx `PAGES` (`/accounts/:id`
 *  stays a pattern), while the household is in a first-run state */
export const replacedInFirstRun = (route: string): boolean => !FIRST_RUN_KEEPS.includes(route);

/** the first-run page's place title, in the phone's top bar and before the product name in the browser tab */
export const FIRST_RUN_TITLE: Readonly<Record<FirstRunPage, string>> = {
  setup: "Set up", waiting: "Waiting for data", "nothing-built": "Set up",
};

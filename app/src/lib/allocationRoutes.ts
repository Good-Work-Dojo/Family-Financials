// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { RouteOpen } from "./modules";

/** Home's allocation donut: the page a click on each asset-class slice opens. These are router paths, which the
 *  hash-link scan in App.test.tsx cannot see, so App.test.tsx checks each one is a live route. A class missing here
 *  opens nothing. */
export const ALLOCATION_ROUTES: Record<string, string> = {
  "Private Stock": "/accounts/investments", "Stocks/ETF": "/accounts/investments",
  Crypto: "/accounts/investments", "Precious Metals": "/accounts/investments",
  Retirement: "/accounts/investments",
  Cash: "/accounts", "Real Estate/Venture": "/accounts/investments",
  "529": "/accounts/investments", House: "/planning/home", Mortgage: "/accounts/liabilities",
};

/** where a slice goes while the page ALLOCATION_ROUTES names does not show (App.tsx PAGES `needs`): the House slice to
 *  Balances while the House page is off (no account is the mortgage). App.test.tsx checks every slice whose page can be
 *  off has one, and that it always shows. */
export const ALLOCATION_FALLBACK: Record<string, string> = { House: "/accounts" };

/** the page a click on the slice of `cls` opens, given which routes show (`open`, lib/modules.ts useRouteOpen):
 *  its own while it shows, else its fallback; undefined opens nothing */
export function allocationRoute(cls: string, open: RouteOpen): string | undefined {
  const to = ALLOCATION_ROUTES[cls];
  return to === undefined || open(to) ? to : ALLOCATION_FALLBACK[cls];
}

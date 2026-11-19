// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The demo build: the dashboard built with VITE_DEMO=1 (tools/verify/demo_site.sh) is a static site of an invented
// household's exports with no server behind it. Every branch that differs reads this one flag: the family's documents
// start as the household's store held them and stay in this browser (data/useSyncedDoc.ts), nothing asks for a refresh, a sign-out or the AI step, no answer sends
// the page to a sign-in, no build is polled, the clock stands at the exports' generation time (lib/clock.ts), every key
// this browser stores under is prefixed (lib/demoKeys.ts) and a banner says what the site is
// (components/DemoBanner.tsx). Vite writes the variable into the bundle as a literal, so a demo build carries none of
// the server's paths and every other build none of the demo's branches. A test that needs the demo mocks this module
// (its storageKey too).
import { storageKeyFor } from "./demoKeys";

/** this bundle is the demo build */
export const IS_DEMO: boolean = import.meta.env.VITE_DEMO === "1";

/** the key a value this browser keeps is stored under: every storage key the app names is defined through this */
export function storageKey(key: string): string {
  return storageKeyFor(IS_DEMO, key);
}

/** the demo banner's words */
export const DEMO_BANNER_TEXT = "Demo: an invented household. Changes stay in this browser.";

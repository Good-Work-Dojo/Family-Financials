// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The names this browser's stored values go under. A project's demo site on GitHub Pages lives on <owner>.github.io,
// an origin every other project site of the owner shares, so in the demo build every key carries DEMO_KEY_PREFIX: the
// demo never reads or overwrites another site's values, nor they its. No module here reads import.meta.env, so the
// build's own code (build/themeScript.ts) uses the same rule.

/** what every key the demo build stores under starts with */
export const DEMO_KEY_PREFIX = "ff-demo:";

/** the theme choice's key, before any prefix: the app's toggle (App.tsx) writes it and build/theme.js reads it before
 *  the bundle loads */
export const THEME_KEY = "theme";

/** the key `key` is stored under in a build that is (`demo`) or is not the demo */
export function storageKeyFor(demo: boolean, key: string): string {
  return demo ? DEMO_KEY_PREFIX + key : key;
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Test support for the scans that read the application's source as text (lib/contrast.test.ts, App.test.tsx). Nothing in
// the application imports this module.

/** `src` without its comments, read left to right in one pass: a block comment, and a line comment from a `//` that
 *  starts the line or follows whitespace to the line's end (a `//` inside a string, as in a URL, is left alone) */
export const withoutComments = (src: string): string =>
  src.replace(/\/\*[\s\S]*?\*\/|(^|\s)\/\/.*$/gm, (_comment, lead: string | undefined) => lead ?? "");

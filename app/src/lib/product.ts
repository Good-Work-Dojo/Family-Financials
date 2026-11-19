// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
/** The product's name: the browser tab's title after the place, the sidebar's header, the page shown before any export
 *  exists, and Settings' About card. */
export const PRODUCT_NAME = "Family Financials";

/** Where the source code of the running program is published, the repository's address unless the build names
 *  another: a fork builds with VITE_SOURCE_URL set to its own (deploy/Dockerfile.web passes its FF_SOURCE build
 *  argument). Settings' About card links to it, so every person the dashboard serves can get the source. */
export const DEFAULT_SOURCE_URL = "https://github.com/Good-Work-Dojo/Family-Financials";

/** the source address a build's environment names: VITE_SOURCE_URL when it is an http(s) address, else the default */
export function sourceUrlFrom(env: Record<string, unknown>): string {
  const given = env.VITE_SOURCE_URL;
  if (typeof given !== "string") return DEFAULT_SOURCE_URL;
  const url = given.trim();
  return /^https?:\/\/[^\s/?#]+(\/[^\s]*)?$/.test(url) ? url : DEFAULT_SOURCE_URL;
}

export const SOURCE_URL = sourceUrlFrom(import.meta.env);

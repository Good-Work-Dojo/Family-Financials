// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Test support: where the data-gated tests find the ETL's exports. FF_EXPORTS_DIR (an absolute path) names a
// directory; without it the exports are the repository's data/exports, resolved from this file's own location.
// Nothing in the application imports this module.
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

const fromEnv = process.env.FF_EXPORTS_DIR;
if (fromEnv && !fromEnv.startsWith("/")) {
  throw new Error(`FF_EXPORTS_DIR must be an absolute path (got a relative one: ${fromEnv})`);
}

/** The directory the exports are read from, without a trailing slash. */
export const EXPORTS_DIR: string = fromEnv || resolve(__dirname, "..", "..", "..", "data", "exports");

/** True when the directory holds an export run (its manifest). */
export const HAVE_EXPORTS: boolean = existsSync(join(EXPORTS_DIR, "manifest.json"));

/** One export file of the directory, parsed. */
export function readExport<T = any>(name: string): T {
  return JSON.parse(readFileSync(join(EXPORTS_DIR, name), "utf8")) as T;
}

/** Throws when FF_REQUIRE_EXPORTS=1 and the directory holds no exports, so a gate that must run the data-gated tests
 *  cannot pass by skipping them. */
export function requireExports(): void {
  if (process.env.FF_REQUIRE_EXPORTS === "1" && !HAVE_EXPORTS) {
    throw new Error(`exports are required (FF_REQUIRE_EXPORTS=1) and ${EXPORTS_DIR} holds none`);
  }
}

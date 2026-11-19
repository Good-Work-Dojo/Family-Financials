// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { IS_DEMO } from "./demo";

/** The id of the build this bundle came from: the `<meta name="ff-build">` that app/build/versionPlugin.ts writes into index.html
 *  (the same id it writes to `version.json`). "dev" under `vite dev` and vitest, where there is no such tag and no version.json to
 *  compare - and in an older cached page that predates it. Read once, at load: it names the page this tab was loaded with. */
function readBuildId(): string {
  if (typeof document === "undefined") return "dev";
  const id = document.querySelector('meta[name="ff-build"]')?.getAttribute("content");
  return id ? id : "dev";
}

export const BUILD_ID: string = readBuildId();

/** the id of a real build, or null for "dev" (a development server, a test run, or a page cached from before the tag
 *  existed), which names no bundle: Settings' About card then says "development build" */
export const knownBuild = (id: string = BUILD_ID): string | null => (id === "dev" ? null : id);

/** where the deployed build's id is published, relative to the page (a hash route never changes the path); null in the
 *  demo build, which publishes none (app/build/versionPlugin.ts) and asks no server */
export const VERSION_URL: string | null = IS_DEMO ? null : "./version.json";

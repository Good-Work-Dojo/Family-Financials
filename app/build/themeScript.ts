// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";
import { storageKeyFor, THEME_KEY } from "../src/lib/demoKeys";

/** where the page loads the theme script from, beside index.html */
export const THEME_SCRIPT = "theme.js";
const SOURCE = fileURLToPath(new URL(`./${THEME_SCRIPT}`, import.meta.url));

/** build/theme.js as a build serves it: as it is, or in the demo build reading the theme under the demo's key
 *  (src/lib/demoKeys.ts), as the app's toggle writes it there */
export function themeSource(demo: boolean): string {
  const source = readFileSync(SOURCE, "utf8");
  if (!demo) return source;
  const literal = JSON.stringify(THEME_KEY);
  if (source.split(literal).length !== 2) throw new Error(`build/theme.js must name the key ${literal} exactly once`);
  return source.replace(literal, JSON.stringify(storageKeyFor(true, THEME_KEY)));
}

/** Serves build/theme.js as /theme.js: emitted beside index.html by `vite build` (so the build id covers it,
 *  build/versionPlugin.ts) and answered by `vite dev`. index.html loads it with `<script vite-ignore src="./theme.js">`
 *  (relative to the page, so the demo site works under any sub-path):
 *  Vite leaves a plain script alone and drops the marker, so the theme is set by a blocking script before the first
 *  paint, from a file, as the content-security policy requires (deploy/Caddyfile). `demo`: the demo build's copy
 *  (themeSource). */
export function themeScriptPlugin({ demo = false }: { demo?: boolean } = {}): Plugin {
  return {
    name: "ff-theme-script",
    configureServer(server) {
      server.middlewares.use(`/${THEME_SCRIPT}`, (_req, res) => {
        res.setHeader("Content-Type", "text/javascript; charset=utf-8");
        res.end(themeSource(demo));
      });
    },
    generateBundle() {
      this.emitFile({ type: "asset", fileName: THEME_SCRIPT, source: themeSource(demo) });
    },
  };
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { demoPagePlugin } from "./build/demoPage";
import { previewExportsPlugin } from "./build/previewExports";
import { themeScriptPlugin } from "./build/themeScript";
import { versionPlugin } from "./build/versionPlugin";

// The dev server serves the ETL's JSON exports at / (publicDir ../data/exports). A build copies no public directory:
// the web image serves the exports from the household's own data/exports and the icons and manifest from /srv/public
// (deploy/Caddyfile), so nothing of a household's is ever built in. The demo build (VITE_DEMO=1, src/lib/demo.ts) is
// the exception: a static site with relative URLs (base ./, so it works under any sub-path) whose public directory is
// the one tools/verify/demo_site.sh stages, an absolute path in FF_DEMO_PUBLIC (the demo household's exports, the icons
// and a relative manifest); it publishes no version.json (it has no deploys to tell of, and asks for none), and its page
// carries the content-security policy a static host does not send (build/demoPage.ts).
// `vite preview` (`make serve`) serves the exports beside the built page itself (build/previewExports.ts).
// versionPlugin (build only) publishes the bundle's id as dist/version.json and as a <meta name="ff-build"> in index.html.
// themeScriptPlugin serves build/theme.js as theme.js beside index.html, which loads it before the bundle (in the demo
// build, reading the theme under the demo's key: src/lib/demoKeys.ts).
export default defineConfig(({ command }) => {
  const demo = process.env.VITE_DEMO === "1";
  const staged = process.env.FF_DEMO_PUBLIC ?? "";
  if (demo && command === "build" && !isAbsolute(staged)) {
    throw new Error("VITE_DEMO=1 builds the demo site: FF_DEMO_PUBLIC must name its staged public directory, an absolute path (tools/verify/demo_site.sh)");
  }
  return {
    plugins: [react(), tailwindcss(), themeScriptPlugin({ demo }), versionPlugin({ publish: !demo }),
      previewExportsPlugin(fileURLToPath(new URL("../data/exports", import.meta.url))), ...(demo ? [demoPagePlugin()] : [])],
    base: demo ? "./" : "/",
    publicDir: command === "build" ? (demo ? staged : false) : "../data/exports",
    server: {
      host: "127.0.0.1",
      // Scenario store in dev (`make scenario-store`, optional). When it isn't
      // running the proxy errors and the app degrades to its localStorage fallback.
      proxy: { "/api": "http://127.0.0.1:8123" },
    },
    test: {
      environment: "node",
      include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
      // every test runs in the zone FF_TEST_TZ names when vitest starts, UTC when it is unset or empty (an empty TZ is
      // no zone at all), whatever the machine's zone: a local date read from a frozen instant (test/demoClock.ts) is then
      // the same day everywhere (src/test/demoClock.test.ts); the gate runs the route matrix once more in two other
      // zones (tools/verify/ci.sh)
      env: { TZ: process.env.FF_TEST_TZ || "UTC" },
      // 3× vitest's defaults (5 s, 10 s): a slow jsdom test on a two-vCPU hosted runner's first run must not time out
      testTimeout: 15_000,
      hookTimeout: 30_000,
    },
  };
});

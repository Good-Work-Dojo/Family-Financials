// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { createHash } from "node:crypto";
import type { Plugin, Rollup } from "vite";

/** what index.html carries until the bundle is complete and its id is known */
const PLACEHOLDER = "__FF_BUILD_ID__";

/** The id of a finished bundle: a hash over every emitted file - by name and by content, JS chunks, CSS, images and the html page
 *  alike - except `version.json` itself. The id is a function of the output, so it is EXACT by construction: if any file the
 *  browser could fetch differs, the id differs (a Tailwind class added by a file nothing imports, a lazy chunk, a rename), and if
 *  the id is the same every asset name in an open tab's index.html still exists. (An id taken from the SOURCES missed files
 *  Tailwind scans, and a docs-only change renamed every asset while the id stood still.) */
export function bundleId(bundle: Rollup.OutputBundle): string {
  const hash = createHash("sha256");
  for (const name of Object.keys(bundle).sort()) {
    if (name === "version.json") continue;
    const file = bundle[name];
    hash.update(name).update("\0").update(file.type === "chunk" ? file.code : file.source).update("\0");
  }
  return hash.digest("hex").slice(0, 16);
}

/** Publishes the build's id twice, from ONE computation: `version.json` ({"build": "<id>"}) next to index.html, and a
 *  `<meta name="ff-build">` in index.html itself, which is how the running bundle knows which build it came from
 *  (src/lib/buildId.ts). A tab that compares the two (src/data/useBuildCheck.ts, fetching version.json with no-store) learns that a
 *  deploy happened under it. The id cannot live inside the JS it describes, hence the meta tag. Build only: `vite dev` has neither,
 *  and the check stays silent there. A build that cannot place the id fails instead of shipping a bundle that can never tell.
 *  `publish` false (the demo build, which has no deploys to tell of) writes the meta tag alone and no `version.json`. */
export function versionPlugin({ publish = true }: { publish?: boolean } = {}): Plugin {
  return {
    name: "ff-version",
    apply: "build",
    // after Vite's own html and asset handling: the bundle is complete (index.html is already an emitted asset)
    enforce: "post",
    transformIndexHtml: () => [{ tag: "meta", attrs: { name: "ff-build", content: PLACEHOLDER }, injectTo: "head" }],
    generateBundle(_options, bundle) {
      const id = bundleId(bundle);
      let placed = 0;
      for (const [name, file] of Object.entries(bundle)) {
        if (file.type !== "asset" || !name.endsWith(".html") || typeof file.source !== "string") continue;
        if (!file.source.includes(PLACEHOLDER)) continue;
        file.source = file.source.split(PLACEHOLDER).join(id);
        placed++;
      }
      if (placed === 0) this.error("ff-version: no emitted html page carries the build-id placeholder, so the id cannot be published");
      if (publish) this.emitFile({ type: "asset", fileName: "version.json", source: `${JSON.stringify({ build: id })}\n` });
    },
  };
}

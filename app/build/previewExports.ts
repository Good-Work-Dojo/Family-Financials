// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import type { Plugin } from "vite";

/** an export's address beside index.html: one plain file name, so no request reaches outside the exports directory */
const EXPORT_PATH = /^\/([a-z0-9_]+\.json)$/;

/** Serves the exports directory (`dir`) beside index.html under `vite preview` (`make serve`), as the dev server's
 *  publicDir does: a build copies no export into dist (vite.config.ts), so the preview reads them where the ingest
 *  wrote them. A request for any other path, or for a file the directory lacks, goes on to the preview's own files. */
export function previewExportsPlugin(dir: string): Plugin {
  return {
    name: "ff-preview-exports",
    configurePreviewServer(server) {
      server.middlewares.use((req, res, next) => {
        const name = EXPORT_PATH.exec((req.url ?? "").split("?")[0])?.[1];
        if (!name || (req.method !== "GET" && req.method !== "HEAD")) { next(); return; }
        readFile(join(dir, name)).then((body) => {
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.end(req.method === "HEAD" ? undefined : body);
        }, () => next());
      });
    },
  };
}

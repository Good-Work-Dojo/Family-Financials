// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { type PreviewServer, type Rollup, build, preview } from "vite";
import { afterAll, describe, expect, it } from "vitest";
import { DEMO_CSP, demoPagePlugin } from "../build/demoPage";
import { previewExportsPlugin } from "../build/previewExports";

// The two build plugins vite.config.ts adds around the demo: the demo page's policy (built in memory, vite's own build)
// and the preview's exports (a real `vite preview` on 127.0.0.1, an ephemeral port).
const dir = mkdtempSync(join(tmpdir(), "ff-demo-build-"));
afterAll(() => rmSync(dir, { recursive: true, force: true }));
mkdirSync(join(dir, "site"), { recursive: true });
writeFileSync(join(dir, "site", "index.html"), '<!doctype html><html><head><meta charset="UTF-8"><script src="./theme.js"></script>'
  + '</head><body><script type="module" src="./main.js"></script></body></html>');
writeFileSync(join(dir, "site", "main.js"), 'console.log("main");\n');

describe("demoPagePlugin", () => {
  it("puts the policy first in the page's head, ahead of every script it governs", async () => {
    const result = (await build({
      root: join(dir, "site"), configFile: false, logLevel: "silent", publicDir: false, plugins: [demoPagePlugin()],
      build: { write: false, minify: false, emptyOutDir: false },
    })) as Rollup.RollupOutput;
    const html = String((result.output.find((o) => o.fileName === "index.html") as Rollup.OutputAsset).source);
    const head = html.slice(html.indexOf("<head>") + "<head>".length).trimStart();
    expect(head.startsWith('<meta http-equiv="Content-Security-Policy"')).toBe(true);
    expect(html).toContain(`content="${DEMO_CSP.replaceAll("'", "&#39;")}"`);
    expect(html.indexOf("Content-Security-Policy")).toBeLessThan(html.indexOf("<script"));
    expect(DEMO_CSP, "a policy in a <meta> cannot carry it").not.toContain("frame-ancestors");
  });
});

describe("previewExportsPlugin", () => {
  it("serves the exports beside the built page under `vite preview`, and nothing outside their directory", async () => {
    const exports = join(dir, "exports");
    mkdirSync(exports, { recursive: true });
    writeFileSync(join(exports, "manifest.json"), '{"schema_version": 23}');
    writeFileSync(join(dir, "secret.json"), '{"secret": true}');
    mkdirSync(join(dir, "dist"), { recursive: true });
    writeFileSync(join(dir, "dist", "index.html"), "<!doctype html><title>page</title>");
    let server: PreviewServer | null = null;
    try {
      server = await preview({ root: dir, configFile: false, logLevel: "silent", build: { outDir: "dist" },
        preview: { host: "127.0.0.1", port: 0, strictPort: false }, plugins: [previewExportsPlugin(exports)] });
      const address = server.httpServer.address();
      const base = `http://127.0.0.1:${typeof address === "object" && address ? address.port : 0}`;
      const manifest = await fetch(`${base}/manifest.json`);
      expect(manifest.headers.get("content-type")).toMatch(/^application\/json/);
      expect(await manifest.json()).toEqual({ schema_version: 23 });
      for (const path of ["/..%2fsecret.json", "/x/manifest.json", "/missing.json"]) {
        const res = await fetch(`${base}${path}`);
        expect(await res.text(), path).not.toContain("secret");
      }
    } finally {
      await new Promise<void>((resolve) => (server ? server.httpServer.close(() => resolve()) : resolve()));
    }
  });
});

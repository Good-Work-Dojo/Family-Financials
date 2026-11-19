// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { type Rollup, build } from "vite";
import { afterAll, beforeEach, describe, expect, it } from "vitest";
import { bundleId, versionPlugin } from "../build/versionPlugin";

// The build id is a function of the EMITTED bundle, so it is exact by construction: any file the browser could fetch differs -> the
// id differs; the same tree -> the same id. These tests build a tiny real project with the real plugin (vite's own build, in memory).
const dir = mkdtempSync(join(tmpdir(), "ff-version-"));
afterAll(() => rmSync(dir, { recursive: true, force: true }));

const HTML = '<!doctype html><html><head><title>t</title></head><body><div id="root"></div><script type="module" src="/main.js"></script></body></html>';
const FILES = {
  "index.html": HTML,
  "main.js": 'import "./style.css";\nimport("./lazy.js").then((m) => console.log(m.default));\nconsole.log("main");\n',
  "lazy.js": 'export default "lazy-one";\n',
  "style.css": "body { color: red; }\n",
};
function write(files: Record<string, string>) {
  mkdirSync(dir, { recursive: true });
  for (const [name, text] of Object.entries(files)) writeFileSync(join(dir, name), text);
}
beforeEach(() => write(FILES));

async function built() {
  const result = (await build({
    root: dir, configFile: false, logLevel: "silent", publicDir: false, plugins: [versionPlugin()],
    build: { write: false, minify: false, emptyOutDir: false },
  })) as Rollup.RollupOutput;
  const files = new Map(result.output.map((o) => [o.fileName, o]));
  const text = (name: string) => String((files.get(name) as Rollup.OutputAsset).source);
  return {
    version: (JSON.parse(text("version.json")) as { build: string }).build,
    meta: /<meta name="ff-build" content="([^"]*)"/.exec(text("index.html"))?.[1],
    html: text("index.html"),
    names: [...files.keys()],
  };
}

describe("the build id published by versionPlugin", () => {
  it("is one id, in version.json and in index.html's meta tag, and no placeholder ships", async () => {
    const b = await built();
    expect(b.version).toMatch(/^[0-9a-f]{16}$/);
    expect(b.meta).toBe(b.version);
    expect(b.html).not.toContain("__FF_BUILD_ID__");
    expect(b.names).toContain("version.json");
  });
  it("with publish false (the demo build) is in the page's meta tag alone: no version.json", async () => {
    const result = (await build({
      root: dir, configFile: false, logLevel: "silent", publicDir: false, plugins: [versionPlugin({ publish: false })],
      build: { write: false, minify: false, emptyOutDir: false },
    })) as Rollup.RollupOutput;
    const files = new Map(result.output.map((o) => [o.fileName, o]));
    expect([...files.keys()]).not.toContain("version.json");
    expect(String((files.get("index.html") as Rollup.OutputAsset).source)).toMatch(/<meta name="ff-build" content="[0-9a-f]{16}"/);
  });
  it("is the same for the same tree", async () => {
    expect((await built()).version).toBe((await built()).version);
  });
  it("changes when any emitted file changes: a stylesheet, the entry, a lazy chunk, the page", async () => {
    const base = (await built()).version;
    const seen = new Set([base]);
    for (const [file, text] of [
      ["style.css", "body { color: blue; }\n"],
      ["main.js", FILES["main.js"] + 'console.log("more");\n'],
      ["lazy.js", 'export default "lazy-two";\n'],
      ["index.html", HTML.replace("<title>t</title>", "<title>other</title>")],
    ] as const) {
      write({ ...FILES, [file]: text });
      const id = (await built()).version;
      expect(id, `${file} changed`).not.toBe(base);
      expect(seen.has(id), `${file} gives its own id`).toBe(false);
      seen.add(id);
    }
  });
  it("does not change for a file the bundle never reads (docs, tests): the assets are the same, so an open tab is not stale", async () => {
    const base = await built();
    write({ ...FILES, "notes.md": "# a docs-only change\n", "main.test.js": "export {};\n" });
    const after = await built();
    expect(after.version).toBe(base.version);
    expect(after.names).toEqual(base.names);
  });
});

describe("bundleId", () => {
  const asset = (source: string) => ({ type: "asset", source }) as unknown as Rollup.OutputAsset;
  const chunk = (code: string) => ({ type: "chunk", code }) as unknown as Rollup.OutputChunk;
  const bundle = (): Rollup.OutputBundle => ({ "assets/a.js": chunk("A"), "assets/b.css": asset("B"), "index.html": asset("<html>") });
  it("is independent of key order, and ignores version.json itself", () => {
    const base = bundleId(bundle());
    const reversed: Rollup.OutputBundle = {};
    for (const k of Object.keys(bundle()).reverse()) reversed[k] = bundle()[k];
    expect(bundleId(reversed)).toBe(base);
    expect(bundleId({ ...bundle(), "version.json": asset('{"build":"x"}') })).toBe(base);
  });
  it("moves with a file's name, its content, an added file, a removed file", () => {
    const base = bundleId(bundle());
    // same content, same sort position, another name: only the name differs
    const renamed = bundle(); renamed["assets/a2.js"] = renamed["assets/a.js"]; delete renamed["assets/a.js"];
    const edited = { ...bundle(), "assets/b.css": asset("B2") };
    const added = { ...bundle(), "assets/d.png": asset("D") };
    const removed = bundle(); delete removed["assets/b.css"];
    const ids = [base, bundleId(renamed), bundleId(edited), bundleId(added), bundleId(removed)];
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("versionPlugin refuses to ship a bundle that cannot say which build it is", () => {
  it("fails the build when no html page carries the placeholder", () => {
    const plugin = versionPlugin() as unknown as {
      generateBundle: (this: { error: (m: string) => never; emitFile: () => void }, o: object, b: Rollup.OutputBundle) => void;
    };
    const ctx = { error: (m: string): never => { throw new Error(m); }, emitFile: () => {} };
    expect(() => plugin.generateBundle.call(ctx, {}, { "assets/a.js": { type: "chunk", code: "x" } as unknown as Rollup.OutputChunk }))
      .toThrow(/cannot be published/);
  });
});

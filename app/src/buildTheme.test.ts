// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { type Rollup, build } from "vite";
import { afterAll, describe, expect, it } from "vitest";
import { THEME_SCRIPT, themeScriptPlugin } from "../build/themeScript";

// The page's theme is set before the first paint by a plain blocking script loaded from a file: the content-security
// policy (deploy/Caddyfile) allows no inline script or style.
const APP = join(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = readFileSync(join(APP, "build", "theme.js"), "utf8");
/** app/index.html without its comments */
const INDEX = readFileSync(join(APP, "index.html"), "utf8").replace(/<!--[\s\S]*?-->/g, "");
const scripts = (html: string) => [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)]
  .map((m) => ({ attrs: m[1], body: m[2].trim() }));

describe("app/index.html", () => {
  it("loads every script from a file, the theme's first as a plain blocking script, then the bundle", () => {
    const [theme, bundle, ...rest] = scripts(INDEX);
    expect(rest).toEqual([]);
    expect([theme.body, bundle.body], "no inline script").toEqual(["", ""]);
    // relative to the page: the dashboard is served at /, and the demo site under any sub-path
    expect(theme.attrs).toMatch(new RegExp(`\\ssrc="\\./${THEME_SCRIPT}"`));
    expect(theme.attrs, "blocking: it runs before the first paint").not.toMatch(/\b(type="module"|defer|async)\b/);
    expect(bundle.attrs).toMatch(/\stype="module"/);
  });

  it("holds no style block, style attribute or inline event handler", () => {
    expect(INDEX).not.toMatch(/<style\b/i);
    expect(INDEX).not.toMatch(/\sstyle\s*=/i);
    expect(INDEX).not.toMatch(/\son[a-z]+\s*=/i);
  });
});

describe("the theme script", () => {
  /** the classes on <html> after the script runs, starting from the page's own `dark`, with `stored` (or a throw) as
   *  what this device's storage holds for the theme */
  const run = (stored: string | null | Error): string[] => {
    const classes = new Set(["dark"]);
    const documentElement = { classList: { add: (c: string) => classes.add(c), remove: (c: string) => classes.delete(c) } };
    const localStorage = { getItem: (key: string) => {
      if (stored instanceof Error) throw stored;
      return key === "theme" ? stored : null;
    } };
    new Function("localStorage", "document", SOURCE)(localStorage, { documentElement });
    return [...classes].sort();
  };
  it("switches to light only when this device chose light; anything else, or no storage at all, stays dark", () => {
    expect(run("light")).toEqual(["light"]);
    expect(run("dark")).toEqual(["dark"]);
    expect(run(null)).toEqual(["dark"]);
    expect(run("LIGHT")).toEqual(["dark"]);
    expect(run(new Error("SecurityError: storage is blocked"))).toEqual(["dark"]);
  });
});

describe("themeScriptPlugin", () => {
  const dir = mkdtempSync(join(tmpdir(), "ff-theme-"));
  afterAll(() => rmSync(dir, { recursive: true, force: true }));
  it("emits the theme script beside index.html as it is, and the page keeps a plain script that loads it first", async () => {
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, "index.html"), '<!doctype html><html><head><script vite-ignore src="/theme.js"></script></head>'
      + '<body><div id="root"></div><script type="module" src="/main.js"></script></body></html>');
    writeFileSync(join(dir, "main.js"), 'console.log("main");\n');
    const result = (await build({
      root: dir, configFile: false, logLevel: "silent", publicDir: false, plugins: [themeScriptPlugin()],
      build: { write: false, minify: false, emptyOutDir: false },
    })) as Rollup.RollupOutput;
    const files = new Map(result.output.map((o) => [o.fileName, o]));
    const text = (name: string) => String((files.get(name) as Rollup.OutputAsset).source);
    expect(text(THEME_SCRIPT)).toBe(SOURCE);
    const [theme, bundle] = scripts(text("index.html"));
    expect(theme.attrs.trim(), "the marker is gone, the file is loaded as it is").toBe(`src="/${THEME_SCRIPT}"`);
    expect(bundle.attrs).toMatch(/type="module"/);
  });
});

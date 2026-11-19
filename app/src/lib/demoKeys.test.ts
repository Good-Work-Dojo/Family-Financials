// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import { themeSource } from "../../build/themeScript";
import { DEMO_KEY_PREFIX, storageKeyFor, THEME_KEY } from "./demoKeys";

// The demo build (lib/demo.ts), whose storageKey prefixes as lib/demoKeys.ts says.
vi.mock("./demo", () => ({ IS_DEMO: true, DEMO_BANNER_TEXT: "Demo", storageKey: (key: string) => `ff-demo:${key}` }));

import { FAMILY_MIRRORS } from "../data/signOut";
import { ANSWERS_LOCAL_KEY } from "./answers";
import { EFUND_OVERRIDES_KEY } from "./efundOverrides";
import { MANUAL_ASSETS_LOCAL_KEY } from "./manualAssets";
import { PREFS_LOCAL_KEY } from "./prefs";
import { PRIVACY_KEY } from "./privacy";
import { ACTIVE_KEY, LOCAL_KEY } from "./scenarioStore";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");

/** the application's own sources under src/ (no test, fixture or test helper) */
function sources(dir: string = SRC): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return e.name === "test" ? [] : sources(p);
    return /\.tsx?$/.test(e.name) && !/\.test\.tsx?$|__fixtures__/.test(e.name) ? [p] : [];
  });
}

describe("the keys this browser stores under", () => {
  it("carry the demo's prefix in the demo build and are left as they are in every other", () => {
    expect(storageKeyFor(false, "ff.prefs.v1")).toBe("ff.prefs.v1");
    expect(storageKeyFor(true, "ff.prefs.v1")).toBe("ff-demo:ff.prefs.v1");
    expect(DEMO_KEY_PREFIX).toBe("ff-demo:");
  });

  it("are each named through storageKey, so the demo build prefixes every one", () => {
    const keys = [PRIVACY_KEY, EFUND_OVERRIDES_KEY, MANUAL_ASSETS_LOCAL_KEY, PREFS_LOCAL_KEY, ANSWERS_LOCAL_KEY,
      LOCAL_KEY, ACTIVE_KEY, ...FAMILY_MIRRORS];
    expect(keys.filter((k) => !k.startsWith(DEMO_KEY_PREFIX))).toEqual([]);
  });

  it("are never written as a literal where the app stores, reads or removes a value", () => {
    const found: string[] = [];
    const constants = new Map<string, string>();
    const used = new Set<string>();
    for (const file of sources()) {
      const text = readFileSync(file, "utf8");
      const rel = file.slice(SRC.length + 1);
      for (const m of text.matchAll(/\b(?:export\s+)?const\s+([A-Z][A-Z0-9_]*)\s*(?::[^=]+)?=\s*([^;\n]+)/g)) {
        constants.set(m[1], m[2]);
      }
      const calls = [
        ...text.matchAll(/\b(?:localStorage|sessionStorage)\.(?:getItem|setItem|removeItem)\(\s*([^,)\s]+)/g),
        ...text.matchAll(/\blocalFlagStore(?:<[^>(]*>)?\(\s*([^,)\s]+)/g),
        ...text.matchAll(/\blocalKey:\s*([^,}\s]+)/g),
      ];
      for (const m of calls) {
        const arg = m[1];
        if (/^["'`]/.test(arg)) found.push(`${rel}: ${m[0]}`);
        else if (/^[A-Z][A-Z0-9_]*$/.test(arg)) used.add(arg);
      }
    }
    expect(found, "a storage key written as a literal").toEqual([]);
    expect(used.size).toBeGreaterThanOrEqual(6);
    for (const name of used) {
      expect(constants.get(name), `${name} is defined through storageKey`).toMatch(/^storageKey\(/);
    }
  });

  it("include the theme's, which the demo build's theme script reads under the prefix before the bundle loads", () => {
    const plain = themeSource(false);
    expect(plain).toContain(`localStorage.getItem(${JSON.stringify(THEME_KEY)})`);
    const demo = themeSource(true);
    expect(demo).toContain(`localStorage.getItem("ff-demo:${THEME_KEY}")`);
    expect(demo.replace(`"ff-demo:${THEME_KEY}"`, JSON.stringify(THEME_KEY))).toBe(plain);
  });
});

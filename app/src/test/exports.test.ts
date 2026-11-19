// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The export locator's own tests: where the exports are, and when their absence is an error.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

const loadHelper = async () => {
  vi.resetModules();
  return import("./exports");
};

describe("exports locator", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("defaults to the repository's data/exports", async () => {
    vi.stubEnv("FF_EXPORTS_DIR", "");
    const { EXPORTS_DIR } = await loadHelper();
    // the directory that holds data/ is the repository's root: the one with its pyproject.toml
    expect(EXPORTS_DIR.endsWith("/data/exports")).toBe(true);
    expect(existsSync(join(dirname(dirname(EXPORTS_DIR)), "pyproject.toml"))).toBe(true);
  });

  it("takes FF_EXPORTS_DIR when it is absolute", async () => {
    vi.stubEnv("FF_EXPORTS_DIR", "/nowhere/exports");
    const { EXPORTS_DIR, HAVE_EXPORTS } = await loadHelper();
    expect(EXPORTS_DIR).toBe("/nowhere/exports");
    expect(HAVE_EXPORTS).toBe(false);
  });

  it("a relative FF_EXPORTS_DIR is refused", async () => {
    vi.stubEnv("FF_EXPORTS_DIR", "data/exports");
    await expect(loadHelper()).rejects.toThrow(/FF_EXPORTS_DIR must be an absolute path/);
  });

  it("requireExports throws only when required and absent", async () => {
    vi.stubEnv("FF_EXPORTS_DIR", "/nowhere/exports");
    vi.stubEnv("FF_REQUIRE_EXPORTS", "");
    const h = await loadHelper();
    expect(() => h.requireExports()).not.toThrow();
    vi.stubEnv("FF_REQUIRE_EXPORTS", "0");
    expect(() => h.requireExports()).not.toThrow();
    vi.stubEnv("FF_REQUIRE_EXPORTS", "1");
    expect(() => h.requireExports()).toThrow(
      "exports are required (FF_REQUIRE_EXPORTS=1) and /nowhere/exports holds none");
    // required and present: a directory holding a manifest
    const dir = mkdtempSync(join(tmpdir(), "ff-exports-"));
    try {
      writeFileSync(join(dir, "manifest.json"), "{}");
      vi.stubEnv("FF_EXPORTS_DIR", dir);
      const present = await loadHelper();
      expect(present.HAVE_EXPORTS).toBe(true);
      expect(() => present.requireExports()).not.toThrow();
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

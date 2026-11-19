// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// The truth loader's own tests (where the file is, what it requires, what passes through) and the demo clock's (Date
// pinned at the ingest instant while timers, and so Testing Library's waits, stay real).
import { findByText, waitFor } from "@testing-library/dom";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** A truth document carrying every part the loader requires (synthetic values). */
const TRUTH = {
  version: 1, profile: "full", end: "2030-03-31",
  household: { name: "The Test household", surname: "Test", joint_label: "Both", timezone: "UTC",
    members: [{ name: "Ada", role: "adult", birth_year: 1990 }, { name: "Kit", role: "child", birth_year: 2020 }] },
  accounts: { t_chk: { kind: "checking", name: "Test Checking", institution: "Test Bank", owner: "Both" } },
  planted: {
    "seam.account": "t_chk", "mortgage.account": "t_loan", "mortgage.payment": "tx_t_mortgage", "brokerage.account": "t_brk",
    "stale.account": "t_ira", "child.account": "t_sav",
    "split.vendor": "Test Mart", "subscriptions.monthly": "Test Stream", "learned.vendor": "Test Nursery",
    "data_gap.account": "t_chk", "future.date": "2030-04-02", "extra.handle": ["kept"],
  },
  figures: {
    comparable_month_ends: ["2030-01-31", "2030-02-28"], partial_month_ends: ["2029-12-31"],
    levels_crossed: [10_000_000], coverage_floor: "2029-12", months: { "2029-12": {}, "2030-01": {} },
    rows: { kept: true },
  },
};

let dir: string;
/** a generator run's FF_HOME tree in a temp dir: data/exports with a manifest, and truth.json at its root */
function home(truth: unknown = TRUTH, generatedAt = "2030-03-31T18:00:00-04:00"): string {
  const exports = join(dir, "data", "exports");
  mkdirSync(exports, { recursive: true });
  writeFileSync(join(exports, "manifest.json"), JSON.stringify({ generated_at: generatedAt }));
  writeFileSync(join(dir, "truth.json"), JSON.stringify(truth));
  return exports;
}
const load = async (exports: string, truth?: string) => {
  vi.stubEnv("FF_EXPORTS_DIR", exports);
  vi.stubEnv("FF_TRUTH", truth ?? "");
  vi.resetModules();
  return import("./truth");
};

beforeEach(() => {
  vi.unstubAllEnvs();
  dir = mkdtempSync(join(tmpdir(), "ff-truth-"));
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.useRealTimers();
  rmSync(dir, { recursive: true, force: true });
});

describe("loadTruth", () => {
  it("reads truth.json two directories above the exports by default", async () => {
    const { loadTruth, truthPath } = await load(home());
    expect(truthPath()).toBe(join(dir, "truth.json"));
    const t = loadTruth();
    expect(t.planted["split.vendor"]).toBe("Test Mart");
    expect(t.household.members.map((m) => m.role)).toEqual(["adult", "child"]);
    expect(t.figures.levels_crossed).toEqual([10_000_000]);
  });

  it("takes FF_TRUTH when it is set, and refuses a relative one", async () => {
    const elsewhere = join(dir, "elsewhere.json");
    writeFileSync(elsewhere, JSON.stringify({ ...TRUTH, end: "2030-04-30" }));
    const { loadTruth } = await load(home(), elsewhere);
    expect(loadTruth().end).toBe("2030-04-30");
    const relative = await load(home(), "truth.json");
    expect(() => relative.truthPath()).toThrow(/FF_TRUTH must be an absolute path/);
  });

  it("names a missing planted handle, a missing figure and a missing file", async () => {
    const planted: Record<string, unknown> = { ...TRUTH.planted };
    delete planted["split.vendor"];
    const noHandle = await load(home({ ...TRUTH, planted }));
    expect(() => noHandle.loadTruth()).toThrow(/planted\.split\.vendor/);
    const figures: Record<string, unknown> = { ...TRUTH.figures };
    delete figures.levels_crossed;
    const noFigure = await load(home({ ...TRUTH, figures }));
    expect(() => noFigure.loadTruth()).toThrow(/figures\.levels_crossed/);
    const noFile = await load(join(dir, "nowhere", "data", "exports"));
    expect(() => noFile.loadTruth()).toThrow(`no truth file at ${join(dir, "nowhere", "truth.json")}`);
  });

  it("refuses a member role or a date of the wrong shape", async () => {
    const badRole = await load(home({ ...TRUTH, household: { ...TRUTH.household, members: [{ name: "Ada", role: "owner" }] } }));
    expect(() => badRole.loadTruth()).toThrow(/household\.members\.0\.role/);
    const badDate = await load(home({ ...TRUTH, planted: { ...TRUTH.planted, "future.date": "April 2" } }));
    expect(() => badDate.loadTruth()).toThrow(/planted\.future\.date/);
  });

  it("refuses a truth of another version, or of none, naming the key", async () => {
    const newer = await load(home({ ...TRUTH, version: 2 }));
    expect(() => newer.loadTruth()).toThrow(/: version: /);
    const { version: _v, ...unversioned } = TRUTH;
    const none = await load(home(unversioned));
    expect(() => none.loadTruth()).toThrow(/: version: /);
  });

  it("requires of the household only what the suites read: its members", async () => {
    const { name: _n, surname: _s, joint_label: _j, ...rest } = TRUTH.household;
    const { loadTruth } = await load(home({ ...TRUTH, household: rest }));
    expect(loadTruth().household.members.length).toBe(2);
    const noMembers = await load(home({ ...TRUTH, household: { name: "The Test household" } }));
    expect(() => noMembers.loadTruth()).toThrow(/household\.members/);
  });

  it("passes unknown keys through", async () => {
    const { loadTruth } = await load(home());
    const t = loadTruth() as Record<string, any>;
    expect(t.version).toBe(1);
    expect(t.planted["extra.handle"]).toEqual(["kept"]);
    expect(t.figures.rows).toEqual({ kept: true });
    expect(t.household.timezone).toBe("UTC");
  });
});

describe("the demo clock", () => {
  /** the fixture manifest's generated_at (home()'s default) as an instant */
  const INSTANT = "2030-03-31T22:00:00.000Z";
  const clock = async (generatedAt?: string) => {
    vi.stubEnv("FF_EXPORTS_DIR", home(TRUTH, generatedAt));
    vi.resetModules();
    return import("./demoClock");
  };

  it("pins new Date() and Date.now() at the manifest's ingest instant, which does not advance", async () => {
    const { freezeAtIngest, thaw } = await clock();
    freezeAtIngest();
    try {
      expect(new Date().toISOString()).toBe(INSTANT);
      const before = Date.now();
      await new Promise((r) => setTimeout(r, 20));                 // a real timer: it fires without advancing anything
      expect(Date.now()).toBe(before);
    } finally {
      thaw();
    }
    // the real clock is back: Date no longer reads the frozen instant, whatever year the machine's clock is in
    expect(Date.now() !== Date.parse(INSTANT) && Math.abs(Date.now() - new Date().getTime()) < 1000, "the real clock is back").toBe(true);
  });

  it("leaves Testing Library's waitFor and findBy* on real timers", async () => {
    const { freezeAtIngest, thaw } = await clock();
    freezeAtIngest();
    try {
      let done = false;
      setTimeout(() => { done = true; }, 30);
      await waitFor(() => expect(done).toBe(true), { timeout: 1000 });
      const root = document.createElement("div");
      document.body.append(root);
      setTimeout(() => { root.textContent = "arrived"; }, 30);
      expect((await findByText(root, "arrived", {}, { timeout: 1000 })).textContent).toBe("arrived");
      root.remove();
    } finally {
      thaw();
    }
  });

  it("refuses a manifest whose generated_at is not an instant", async () => {
    const { freezeAtIngest } = await clock("yesterday-ish");
    expect(() => freezeAtIngest()).toThrow(/generated_at is not an instant/);
  });
});

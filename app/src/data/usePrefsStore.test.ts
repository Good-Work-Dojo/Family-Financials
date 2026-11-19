// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CategoryRename } from "./types";
import { emptyPrefs, MAX_SAVED, type PrefsDoc, withSaved } from "../lib/prefs";
import { DEFAULT_SPEC } from "../lib/reportSpec";

// the transport is not under test: a stand-in that holds the doc and applies every commit the store makes
const sync = vi.hoisted(() => ({ doc: null as unknown as PrefsDoc, commit: vi.fn() }));
vi.mock("./useSyncedDoc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./useSyncedDoc")>()),
  useSyncedDoc: () => ({ loading: false, localOnly: false, saveState: "idle", doc: sync.doc, commit: sync.commit }),
}));
import { usePrefsStore } from "./usePrefsStore";

const NOW = "2026-07-16T10:00:00Z";
beforeEach(() => {
  sync.doc = emptyPrefs();
  sync.commit.mockReset();
  sync.commit.mockImplementation((next: PrefsDoc | ((cur: PrefsDoc) => PrefsDoc)) => {
    sync.doc = typeof next === "function" ? next(sync.doc) : next;
  });
});
const store = () => renderHook(() => usePrefsStore()).result;
const withViews = (n: number) => {
  let p = emptyPrefs();
  for (let i = 0; i < n; i++) p = withSaved(p, "saved_filters", { id: `v${i}`, name: `v${i}`, query: "" }, NOW)!;
  return p;
};

describe("usePrefsStore saved views", () => {
  it("saveFilter writes one view with a fresh uuid, in ONE functional commit, and says true", () => {
    const s = store();
    expect(s.current.saveFilter("  Coffee runs ", "categories=Dining")).toBe(true);
    expect(sync.commit).toHaveBeenCalledTimes(1);
    expect(typeof sync.commit.mock.calls[0][0], "a functional update, never a captured doc").toBe("function");
    expect(sync.doc.saved_filters).toHaveLength(1);
    expect(sync.doc.saved_filters[0]).toMatchObject({ name: "Coffee runs", query: "categories=Dining" });
    expect(sync.doc.saved_filters[0].id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    s.current.saveFilter("Second", "q=x");
    expect(new Set(sync.doc.saved_filters.map((f) => f.id)).size, "each save gets its own id").toBe(2);
  });

  it("refuses with false, writing nothing, for a blank name, an over-long query and a full list", () => {
    const s = store();
    expect(s.current.saveFilter("   ", "q=x")).toBe(false);
    expect(s.current.saveFilter("big", "q=" + "x".repeat(3000))).toBe(false);
    expect(sync.commit).not.toHaveBeenCalled();
    sync.doc = withViews(MAX_SAVED);
    const full = store();
    expect(full.current.saveFilter("one more", "q=x")).toBe(false);
    expect(sync.commit).not.toHaveBeenCalled();
    expect(sync.doc.saved_filters).toHaveLength(MAX_SAVED);
  });

  it("a stale closure can never push the list past the cap: the commit re-checks the CURRENT doc", () => {
    sync.doc = withViews(MAX_SAVED - 1);
    const s = store();                                        // its closure holds 49
    sync.doc = withViews(MAX_SAVED);                          // another save landed meanwhile
    expect(s.current.saveFilter("late", "q=x"), "false = the commit refused, nothing was written").toBe(false);
    expect(sync.doc.saved_filters).toHaveLength(MAX_SAVED);
    expect(sync.doc.saved_filters.some((f) => f.name === "late")).toBe(false);
  });

  it("a stale closure's rename of a view that is gone says false too, and the doc is untouched", () => {
    sync.doc = withViews(2);
    const s = store();                                        // its closure still holds v0 and v1
    sync.doc = withViews(1);                                  // v1 was deleted meanwhile
    expect(s.current.renameSavedFilter("v1", "Renamed")).toBe(false);
    expect(sync.doc.saved_filters.map((f) => f.name)).toEqual(["v0"]);
  });

  it("a full prefs doc refuses a save with false and writes nothing", () => {
    let doc = emptyPrefs();
    for (let i = 0; i < MAX_SAVED; i++) {
      const next = withSaved(doc, "saved_filters", { id: `b${i}`, name: `b${i}`, query: "q=" + "x".repeat(1500) }, NOW);
      if (!next) break;
      doc = next;
    }
    expect(doc.saved_filters.length).toBeLessThan(MAX_SAVED);       // the budget stopped it, not the count
    sync.doc = doc;
    const s = store();
    expect(s.current.saveFilter("one more", "q=" + "x".repeat(1500))).toBe(false);
    expect(sync.commit).not.toHaveBeenCalled();
  });

  it("removeSavedFilter drops exactly that id (and builds on the current doc)", () => {
    sync.doc = withViews(3);
    const s = store();
    s.current.removeSavedFilter("v1");
    expect(sync.doc.saved_filters.map((f) => f.id)).toEqual(["v0", "v2"]);
    expect(typeof sync.commit.mock.calls[0][0]).toBe("function");
  });

  it("renameSavedFilter renames in place and refuses a blank name or an unknown id without writing", () => {
    sync.doc = withViews(2);
    const s = store();
    expect(s.current.renameSavedFilter("v1", "  Renamed ")).toBe(true);
    expect(sync.doc.saved_filters.map((f) => f.name)).toEqual(["v0", "Renamed"]);
    sync.commit.mockClear();
    expect(s.current.renameSavedFilter("v1", "   ")).toBe(false);
    expect(s.current.renameSavedFilter("nope", "x")).toBe(false);
    expect(sync.commit).not.toHaveBeenCalled();
  });
});

describe("usePrefsStore saved reports", () => {
  const spec = { ...DEFAULT_SPEC, filters: "categories=Dining", groupBy: "vendor" } as const;
  const withReports = (n: number) => {
    let p = emptyPrefs();
    for (let i = 0; i < n; i++) p = withSaved(p, "reports", { id: `r${i}`, name: `r${i}`, spec: DEFAULT_SPEC }, NOW)!;
    return p;
  };
  it("saveReport writes one report with a fresh uuid and its spec, in ONE functional commit, and says true; the views list is untouched", () => {
    const s = store();
    expect(s.current.saveReport("  Dining by vendor ", spec)).toBe(true);
    expect(sync.commit).toHaveBeenCalledTimes(1);
    expect(typeof sync.commit.mock.calls[0][0], "a functional update, never a captured doc").toBe("function");
    expect(sync.doc.reports).toHaveLength(1);
    expect(sync.doc.reports[0]).toMatchObject({ name: "Dining by vendor", spec });
    expect(sync.doc.reports[0].id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(sync.doc.saved_filters).toEqual([]);
    s.current.saveReport("Second", DEFAULT_SPEC);
    expect(new Set(sync.doc.reports.map((r) => r.id)).size, "each save gets its own id").toBe(2);
  });
  it("refuses with false, writing nothing, for a blank name, an over-long spec and a full list", () => {
    const s = store();
    expect(s.current.saveReport("   ", spec)).toBe(false);
    expect(s.current.saveReport("big", { ...spec, filters: "q=" + "x".repeat(3000) })).toBe(false);
    expect(sync.commit).not.toHaveBeenCalled();
    sync.doc = withReports(MAX_SAVED);
    expect(store().current.saveReport("one more", spec)).toBe(false);
    expect(sync.commit).not.toHaveBeenCalled();
    expect(sync.doc.reports).toHaveLength(MAX_SAVED);
  });
  it("a stale closure can never push the list past the cap: the commit re-checks the CURRENT doc", () => {
    sync.doc = withReports(MAX_SAVED - 1);
    const s = store();
    sync.doc = withReports(MAX_SAVED);
    expect(s.current.saveReport("late", spec)).toBe(false);
    expect(sync.doc.reports.some((r) => r.name === "late")).toBe(false);
  });
  it("removeReport drops exactly that id; renameReport renames in place and refuses a blank name or an unknown id", () => {
    sync.doc = withReports(3);
    const s = store();
    s.current.removeReport("r1");
    expect(sync.doc.reports.map((r) => r.id)).toEqual(["r0", "r2"]);
    expect(s.current.renameReport("r2", " Renamed ")).toBe(true);
    expect(sync.doc.reports.map((r) => r.name)).toEqual(["r0", "Renamed"]);
    sync.commit.mockClear();
    expect(s.current.renameReport("r2", "  ")).toBe(false);
    expect(s.current.renameReport("nope", "x")).toBe(false);
    expect(sync.commit).not.toHaveBeenCalled();
  });
});

describe("usePrefsStore setBudget", () => {
  const renames: CategoryRename[] = [{ from: { category: "Eating Out" }, to: { category: "Dining" } }];
  it("takes the category renames, so clearing a renamed category drops its old-name key in the same commit", () => {
    sync.doc = { ...emptyPrefs(), budgets: { "Eating Out": 800, Groceries: 1200 } };
    const s = store();
    s.current.setBudget("Dining", null, renames);
    expect(sync.commit).toHaveBeenCalledTimes(1);
    expect(typeof sync.commit.mock.calls[0][0], "a functional update, so rapid keystrokes chain").toBe("function");
    expect(sync.doc.budgets).toEqual({ Groceries: 1200 });
  });
  it("is the plain set/clear when no renames are passed", () => {
    sync.doc = { ...emptyPrefs(), budgets: { Dining: 800 } };
    const s = store();
    s.current.setBudget("Groceries", 1100);
    expect(sync.doc.budgets).toEqual({ Dining: 800, Groceries: 1100 });
    s.current.setBudget("Dining", null);
    expect(sync.doc.budgets).toEqual({ Groceries: 1100 });
  });
});

describe("usePrefsStore setModule", () => {
  it("writes the switch in ONE functional commit, and a false is written, not dropped", () => {
    const s = store();
    s.current.setModule("home_buying", true);
    expect(sync.commit).toHaveBeenCalledTimes(1);
    expect(typeof sync.commit.mock.calls[0][0], "a functional update, never a captured doc").toBe("function");
    expect(sync.doc.modules).toEqual({ home_buying: true });
    s.current.setModule("home_buying", false);
    expect(sync.doc.modules, "a false turns off a module the config switches on").toEqual({ home_buying: false });
    s.current.setModule("home_buying", null);
    expect(sync.doc.modules, "null clears the switch: the config's seed decides again").toEqual({});
    expect(sync.commit).toHaveBeenCalledTimes(3);
  });
});

describe("usePrefsStore setTargetMix", () => {
  const MIX = { stocks: 60, bonds: 30, cash: 5, alts: 5 };
  it("saves the mix in ONE functional commit and says true", () => {
    const s = store();
    expect(s.current.setTargetMix(MIX)).toBe(true);
    expect(sync.commit).toHaveBeenCalledTimes(1);
    expect(typeof sync.commit.mock.calls[0][0], "a functional update, never a captured doc").toBe("function");
    expect(sync.doc.target_mix).toEqual(MIX);
  });
  it("refuses a mix that does not total 100, or has a leg out of range, with false and no commit (no PUT the store would refuse)", () => {
    const s = store();
    expect(s.current.setTargetMix({ ...MIX, stocks: 70 })).toBe(false);
    expect(s.current.setTargetMix({ ...MIX, cash: -5, bonds: 40 })).toBe(false);
    expect(s.current.setTargetMix({ ...MIX, cash: NaN })).toBe(false);
    expect(sync.commit).not.toHaveBeenCalled();
    expect(sync.doc.target_mix).toBeNull();
  });
});

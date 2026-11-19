// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it, vi } from "vitest";
import type { Assumptions, CategoryRename } from "../data/types";
import {
  AI_OFF, AI_SCOPE_MAX, emptyPrefs, KEPT_WHOLE_KEYS, MAX_MODULE_ID_LEN, MAX_MODULES, MAX_PREFS_DOC_BYTES, MAX_SAVED,
  MAX_SAVED_ENTRY_CHARS, MAX_SAVED_NAME, mergeAssumptions, normalizePrefs, normalizeSaved, type PrefsDoc, renameBudgets, resolveAssumptions,
  tryWithRenamedSaved, tryWithSaved, withAi, withBudget, withModule, withoutSaved, withPayrollPhases, withProjection,
  withRenamedSaved, withSaved, withTarget, withTargetMix,
} from "./prefs";
import { effectiveModules } from "./modules";
import { DEFAULT_SPEC } from "./reportSpec";

const NOW = "2026-07-16T10:00:00Z";

const baseAssumptions = {
  projections: { inflation_pct: 2.75, ltcg_pct: 15 },
  budgets: {},
  targets: { Cash: 8 },
} as unknown as Assumptions;

describe("merge", () => {
  it("is an exact identity when prefs are empty", () => {
    expect(mergeAssumptions(baseAssumptions, emptyPrefs())).toBe(baseAssumptions);
  });

  it("budgets/targets replace when non-empty; projections merge per-key", () => {
    let p = withBudget(emptyPrefs(), "Groceries", 1200, NOW);
    p = withProjection(p, "inflation_pct", 3.5, NOW);
    const m = mergeAssumptions(baseAssumptions, p);
    expect(m.budgets).toEqual({ Groceries: 1200 });
    expect(m.targets).toEqual({ Cash: 8 });                    // untouched — prefs empty
    expect(m.projections.inflation_pct).toBe(3.5);             // overridden
    expect(m.projections.ltcg_pct).toBe(15);                   // YAML default kept
  });

  it("clearing a value falls back to the YAML default", () => {
    let p = withProjection(emptyPrefs(), "inflation_pct", 3.5, NOW);
    p = withProjection(p, "inflation_pct", null, NOW);
    expect(mergeAssumptions(baseAssumptions, p).projections.inflation_pct)
      .toBe(2.75);
  });

  it("only whitelisted scalar projection keys are accepted", () => {
    const p = withProjection(emptyPrefs(), "return_scenarios_pct", 9, NOW);
    expect(p.projections).toEqual({});                          // rejected — array-typed key
  });

  // the home's value is the snapshot's House-class rows (lib/lifeSummary homeValue), never a projection default: an
  // override a family stored under the old key is dropped on read, never merged, and gone from the doc the next save sends
  it("a stored home value override is ignored: dropped on read, never merged, and not set again", () => {
    const stored = { version: 1, updated_at: NOW, projections: { home_value_estimate: 1_250_000, inflation_pct: 3.5 } };
    const doc = normalizePrefs(stored);
    expect(doc.projections).toEqual({ inflation_pct: 3.5 });
    expect(Object.keys(mergeAssumptions(baseAssumptions, doc).projections)).toEqual(["inflation_pct", "ltcg_pct"]);
    // a doc that skipped normalizing still merges only the keys prefs may override
    const raw = { budgets: {}, targets: {}, projections: { home_value_estimate: 1_250_000 } };
    expect(mergeAssumptions(baseAssumptions, raw), "nothing to merge: the same assumptions").toBe(baseAssumptions);
    expect(withProjection(emptyPrefs(), "home_value_estimate", 1_250_000, NOW).projections).toEqual({});
  });
});

describe("ops + normalize", () => {
  it("withBudget set/clear round-trips", () => {
    let p = withBudget(emptyPrefs(), "Dining", 800, NOW);
    expect(p.budgets.Dining).toBe(800);
    p = withBudget(p, "Dining", null, NOW);
    expect(p.budgets.Dining).toBeUndefined();
    expect(p.updated_at).toBe(NOW);
  });

  it("withTarget behaves the same for asset classes", () => {
    const p = withTarget(emptyPrefs(), "Stocks/ETF", 30, NOW);
    expect(p.targets["Stocks/ETF"]).toBe(30);
  });

  it("normalize survives garbage and drops malformed payroll phases", () => {
    expect(normalizePrefs(null)).toEqual(emptyPrefs());
    const p = normalizePrefs({
      version: 1, updated_at: NOW,
      budgets: { Dining: 800, Bad: "nope" },
      projections: { inflation_pct: 3, made_up_key: 1 },
      payroll: { phases: [
        { person: "Alex", from: "2024-01", gross_annual: 200_000,
          employee_401k_pct: 10, employer_match_pct: 4 },
        { person: "Broken" },                                   // no from/gross — dropped
      ] },
    });
    expect(p.budgets).toEqual({ Dining: 800 });
    expect(p.projections).toEqual({ inflation_pct: 3 });
    expect(p.payroll.phases).toHaveLength(1);
    expect(p.payroll.phases[0].to).toBeNull();
  });

  it("withPayrollPhases stores phases verbatim", () => {
    const p = withPayrollPhases(emptyPrefs(), [
      { person: "Sam", from: "2026-02", to: null, gross_annual: 150_000,
        employee_401k_pct: 6, employer_match_pct: 3 }], NOW);
    expect(p.payroll.phases[0].person).toBe("Sam");
  });
});

describe("the target mix section", () => {
  const MIX = { stocks: 64, bonds: 26, cash: 5, alts: 5 };
  it("an older doc (no section) or an explicit null is none saved, without a warning", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(emptyPrefs().target_mix).toBeNull();
    expect(normalizePrefs({ version: 1, updated_at: NOW, budgets: {} }).target_mix).toBeNull();
    expect(normalizePrefs({ version: 1, updated_at: NOW, target_mix: null }).target_mix).toBeNull();
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });
  it("keeps a valid mix, rebuilt from its four buckets", () => {
    expect(normalizePrefs({ version: 1, updated_at: NOW, target_mix: { ...MIX, note: "x" } }).target_mix).toEqual(MIX);
  });
  it("drops junk with ONE warning each and reads as none saved, leaving every other section alone", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    for (const junk of ["mix", [64, 26, 5, 5], { ...MIX, bonds: 25 }, { ...MIX, cash: -5, bonds: 36 }, { ...MIX, stocks: 101, bonds: -11 }, { stocks: 64, bonds: 36 }]) {
      warn.mockClear();
      const p = normalizePrefs({ version: 1, updated_at: NOW, budgets: { Dining: 800 }, target_mix: junk });
      expect(p.target_mix, JSON.stringify(junk)).toBeNull();
      expect(p.budgets).toEqual({ Dining: 800 });
      expect(warn).toHaveBeenCalledTimes(1);
      expect(warn.mock.calls[0][0]).toBe("prefs: dropping a malformed target_mix");
    }
    warn.mockRestore();
  });
  it("withTargetMix saves a valid mix with a fresh updated_at and refuses anything else (null: nothing to write)", () => {
    const p = withTargetMix(emptyPrefs(), MIX, NOW)!;
    expect(p.target_mix).toEqual(MIX);
    expect(p.updated_at).toBe(NOW);
    expect(withTargetMix(emptyPrefs(), { ...MIX, stocks: 70 }, NOW), "totals 106").toBeNull();
    expect(withTargetMix(emptyPrefs(), { ...MIX, cash: -1, bonds: 27 }, NOW)).toBeNull();
  });
  it("round-trips through JSON, and never feeds data.assumptions", () => {
    const p = withTargetMix(withTarget(emptyPrefs(), "Cash", 10, NOW), MIX, NOW)!;
    expect(normalizePrefs(JSON.parse(JSON.stringify(p)))).toEqual(p);
    expect(mergeAssumptions(baseAssumptions, withTargetMix(emptyPrefs(), MIX, NOW)!)).toBe(baseAssumptions);
  });
});

describe("the modules and AI sections", () => {
  const AI = { enabled: true, consent_policy: 2, consent_scope: "anthropic||cards",
    consented_at: "2026-07-16T09:00:00.000Z" };
  it("a document with neither (an older bundle's) reads as no module switched and AI off", () => {
    const p = normalizePrefs({ version: 1, updated_at: NOW, budgets: { Dining: 800 } });
    expect(p.modules).toEqual({});
    expect(p.ai).toEqual(AI_OFF);
    expect(emptyPrefs().modules).toEqual({});
    expect(emptyPrefs().ai).toEqual({ enabled: false, consent_policy: null, consent_scope: null, consented_at: null });
  });
  it("a document this bundle did not write keeps both sections whole, beside every other section", () => {
    // what the store holds after a newer tab switched a module and consented, and an older one saved a budget over it
    const stored = { version: 1, updated_at: NOW, budgets: { Dining: 800 }, target_mix: null, saved_filters: [],
      modules: { home_buying: true }, ai: AI, a_later_section: { x: 1 } };
    const p = normalizePrefs(JSON.parse(JSON.stringify(stored)));
    expect(p.modules).toEqual({ home_buying: true });
    expect(p.ai).toEqual(AI);
    expect(p.budgets).toEqual({ Dining: 800 });
    expect("a_later_section" in p, "a section no bundle here knows is not carried").toBe(false);
    expect(normalizePrefs(JSON.parse(JSON.stringify(p))), "and round-trips").toEqual(p);
  });
  it("keeps an unknown id, ignores it for effect, caps at 16 with the known ids first, and drops what the store would refuse", () => {
    const p = normalizePrefs({ modules: { later: true, home_buying: false, constructor: true } });
    expect(p.modules, "carried, the known id first").toEqual({ home_buying: false, later: true, constructor: true });
    expect(Object.keys(p.modules)).toEqual(["home_buying", "later", "constructor"]);
    expect(effectiveModules({}, p.modules), "an id this bundle does not know does nothing").toEqual({ home_buying: false });
    // a value that is no boolean, or an id longer than the store takes, is dropped
    expect(normalizePrefs({ modules: { home_buying: "yes", later: 1, ["m".repeat(MAX_MODULE_ID_LEN + 1)]: true,
      ["m".repeat(MAX_MODULE_ID_LEN)]: true } }).modules).toEqual({ ["m".repeat(MAX_MODULE_ID_LEN)]: true });
    for (const junk of ["home_buying", ["home_buying"], null, 1]) expect(normalizePrefs({ modules: junk }).modules).toEqual({});
    // at most MAX_MODULES: the known id is kept ahead of later ones, whatever the stored order
    const many = Object.fromEntries([...Array.from({ length: MAX_MODULES + 2 }, (_, i) => [`later_${i}`, true]), ["home_buying", true]]);
    const capped = normalizePrefs({ modules: many }).modules;
    expect(MAX_MODULES).toBe(16);
    expect(Object.keys(capped)).toHaveLength(MAX_MODULES);
    expect(Object.keys(capped).slice(0, 2)).toEqual(["home_buying", "later_0"]);
  });
  it("a switch carries every other id through the save, and never grows the section past the store's cap", () => {
    const stored = normalizePrefs({ modules: { home_buying: false, later: true } });
    expect(withModule(stored, "home_buying", true, NOW).modules).toEqual({ home_buying: true, later: true });
    const full = normalizePrefs({ modules: Object.fromEntries(Array.from({ length: MAX_MODULES }, (_, i) => [`later_${i}`, true])) });
    const on = withModule(full, "home_buying", true, NOW).modules;
    expect(Object.keys(on)).toHaveLength(MAX_MODULES);
    expect(on.home_buying, "the known switch takes the last later id's place").toBe(true);
    expect("later_15" in on).toBe(false);
  });
  it("reads a malformed AI section as off, one with a key beyond its four or a long scope included, as the store and the ETL do", () => {
    for (const junk of [null, "on", [], { enabled: "yes", consent_policy: 1, consented_at: null },
      { ...AI, consent_policy: true }, { ...AI, consent_policy: 1.5 }, { ...AI, consented_at: 5 }, { enabled: true },
      { enabled: true, consent_policy: 2 }, { ...AI, extra: 1 },
      { enabled: true, consent_policy: 2, consented_at: null }, { ...AI, consent_scope: 7 },
      { ...AI, consent_scope: "x".repeat(AI_SCOPE_MAX + 1) }]) {
      expect(normalizePrefs({ ai: junk }).ai, JSON.stringify(junk)).toEqual(AI_OFF);
    }
    expect(normalizePrefs({ ai: { ...AI } }).ai).toEqual(AI);
    expect(normalizePrefs({ ai: { ...AI, consent_scope: "x".repeat(AI_SCOPE_MAX) } }).ai.consent_scope).toHaveLength(AI_SCOPE_MAX);
    expect(normalizePrefs({ ai: { enabled: false, consent_policy: null, consent_scope: null, consented_at: null } }).ai)
      .toEqual(AI_OFF);
  });
  it("withModule writes the switch, a false included, with a fresh updated_at, and leaves the other sections alone", () => {
    const on = withModule(withBudget(emptyPrefs(), "Dining", 800, NOW), "home_buying", true, NOW);
    expect(on.modules).toEqual({ home_buying: true });
    expect(on.updated_at).toBe(NOW);
    expect(on.budgets).toEqual({ Dining: 800 });
    const later = "2026-07-16T11:00:00Z";
    expect(withModule(on, "home_buying", false, later)).toMatchObject({ modules: { home_buying: false }, updated_at: later });
    expect(mergeAssumptions(baseAssumptions, withModule(emptyPrefs(), "home_buying", true, NOW)), "never feeds data.assumptions")
      .toBe(baseAssumptions);
  });
  it("withModule with null clears the family's switch, and keeps every other id", () => {
    const stored = normalizePrefs({ modules: { home_buying: true, later: false } });
    const cleared = withModule(stored, "home_buying", null, NOW);
    expect(cleared.modules).toEqual({ later: false });
    expect(cleared.updated_at).toBe(NOW);
    expect(withModule(emptyPrefs(), "home_buying", null, NOW).modules, "nothing to clear: still nothing").toEqual({});
  });
  it("withAi writes the consent whole with a fresh updated_at, and leaves the other sections alone", () => {
    const on = withAi(withModule(emptyPrefs(), "home_buying", true, NOW), AI, NOW);
    expect(on.ai).toEqual(AI);
    expect(on.updated_at).toBe(NOW);
    expect(on.modules).toEqual({ home_buying: true });
    const later = "2026-07-16T11:00:00Z";
    const off = withAi(on, { ...on.ai, enabled: false }, later);
    expect(off).toMatchObject({ ai: { enabled: false, consent_policy: 2, consent_scope: AI.consent_scope,
      consented_at: AI.consented_at }, updated_at: later });
    expect(on.ai.enabled, "the earlier document is not changed").toBe(true);
    // a section the store would refuse is never written: it reads as off instead
    expect(withAi(on, { enabled: true, consent_policy: 1.5, consent_scope: null, consented_at: null } as never, later).ai)
      .toEqual(AI_OFF);
    expect(mergeAssumptions(baseAssumptions, on), "never feeds data.assumptions").toBe(baseAssumptions);
  });
  it("the sections kept whole are the target mix, the modules and the AI consent", () => {
    expect([...KEPT_WHOLE_KEYS]).toEqual(["target_mix", "modules", "ai"]);
  });
});

describe("renameBudgets", () => {
  const renames: CategoryRename[] = [{ from: { category: "Eating Out" }, to: { category: "Dining" } }];
  it("moves a budget to the new name and lets an explicit budget on the new name win", () => {
    expect(renameBudgets({ "Eating Out": 800, Groceries: 1200 }, renames)).toEqual({ Dining: 800, Groceries: 1200 });
    expect(renameBudgets({ "Eating Out": 800, Dining: 900 }, renames)).toEqual({ Dining: 900 });
    expect(renameBudgets({ Dining: 900, "Eating Out": 800 }, renames)).toEqual({ Dining: 900 });   // key order is irrelevant
  });
  it("is the same instance when nothing moves", () => {
    const b = { Groceries: 1200 };
    expect(renameBudgets(b, renames)).toBe(b);
    expect(renameBudgets(b, [])).toBe(b);
  });
  it("a pair rename never moves a category budget", () => {
    const pair: CategoryRename[] = [{ from: { category: "Dining", subcategory: "Pubs" },
                                      to: { category: "Dining", subcategory: "Bars" } }];
    const b = { Dining: 900 };
    expect(renameBudgets(b, pair)).toBe(b);
  });
  // The ETL refuses a chain or a cycle at load (etl/enrich/renames.py CategoryRenamer._refuse_chains), so
  // assumptions.json never carries one; this pins that the app would still not DROP a budget if it did. "An
  // explicit budget on the new name wins" only while that name keeps its budget: a name that is itself
  // renamed hands its budget on, so it cannot also swallow the one arriving.
  it("never drops a budget under a chain or a cycle (one pass, like the ETL's map)", () => {
    const chain: CategoryRename[] = [{ from: { category: "A" }, to: { category: "B" } },
                                     { from: { category: "B" }, to: { category: "C" } }];
    expect(renameBudgets({ A: 100, B: 200 }, chain)).toEqual({ B: 100, C: 200 });
    const cycle: CategoryRename[] = [{ from: { category: "A" }, to: { category: "B" } },
                                     { from: { category: "B" }, to: { category: "A" } }];
    expect(renameBudgets({ A: 100, B: 200 }, cycle)).toEqual({ B: 100, A: 200 });
    expect(renameBudgets({ B: 200, A: 100 }, cycle)).toEqual({ A: 200, B: 100 });   // key order is irrelevant
  });
});

describe("withBudget across a category rename (what the budget editor writes)", () => {
  const renames: CategoryRename[] = [{ from: { category: "Eating Out" }, to: { category: "Dining" } }];
  const stored = () => ({ ...emptyPrefs(), budgets: { "Eating Out": 800, Groceries: 1200 } });
  it("clearing the new name also drops the old-name key, so it cannot rename back and resurrect", () => {
    const cleared = withBudget(stored(), "Dining", null, NOW, renames);
    expect(cleared.budgets).toEqual({ Groceries: 1200 });
    expect(renameBudgets(cleared.budgets, renames)).toEqual({ Groceries: 1200 });
  });
  it("setting a value on the new name leaves exactly one key for the category, and sets the value", () => {
    expect(withBudget(stored(), "Dining", 950, NOW, renames).budgets).toEqual({ Dining: 950, Groceries: 1200 });
    const both = { ...emptyPrefs(), budgets: { "Eating Out": 800, Dining: 900 } };
    expect(withBudget(both, "Dining", 950, NOW, renames).budgets).toEqual({ Dining: 950 });
  });
  it("leaves every other category alone, and a rename that does not target this category", () => {
    const doc = { ...emptyPrefs(), budgets: { "Eating Out": 800, Groceries: 1200, Pets: 90 } };
    expect(withBudget(doc, "Groceries", null, NOW, renames).budgets).toEqual({ "Eating Out": 800, Pets: 90 });
    expect(withBudget(doc, "Pets", 100, NOW, renames).budgets).toEqual({ "Eating Out": 800, Groceries: 1200, Pets: 100 });
  });
  it("a pair rename never removes a category budget; without renames it is the plain set/clear", () => {
    const pair: CategoryRename[] = [{ from: { category: "Dining", subcategory: "Pubs" },
                                      to: { category: "Dining", subcategory: "Bars" } }];
    expect(withBudget({ ...emptyPrefs(), budgets: { Dining: 900 } }, "Dining", 950, NOW, pair).budgets).toEqual({ Dining: 950 });
    expect(withBudget(stored(), "Dining", null, NOW).budgets).toEqual({ "Eating Out": 800, Groceries: 1200 });
  });
});

describe("resolveAssumptions (the merged data memo)", () => {
  const renames: CategoryRename[] = [{ from: { category: "Eating Out" }, to: { category: "Dining" } }];
  const withRenames = (budgets: Record<string, number>) =>
    ({ ...baseAssumptions, budgets, category_renames: renames }) as Assumptions;

  it("renames the budgets pinned in assumptions.yaml when the family set none in-app", () => {
    const a = withRenames({ "Eating Out": 800, Groceries: 1200 });
    expect(resolveAssumptions(a, emptyPrefs()).budgets).toEqual({ Dining: 800, Groceries: 1200 });
    expect(a.budgets).toEqual({ "Eating Out": 800, Groceries: 1200 });         // the export is not mutated
  });
  it("renames the in-app budgets too, and they replace the YAML ones as before", () => {
    const a = withRenames({ "Eating Out": 700, Utilities: 300 });
    const p = withBudget(withBudget(emptyPrefs(), "Eating Out", 800, NOW), "Groceries", 1200, NOW);
    expect(resolveAssumptions(a, p).budgets).toEqual({ Dining: 800, Groceries: 1200 });
  });
  it("is an exact identity when nothing moves and prefs are empty", () => {
    const quiet = withRenames({ Groceries: 1200 });
    expect(resolveAssumptions(quiet, emptyPrefs())).toBe(quiet);
    const none = { ...baseAssumptions, category_renames: [] } as unknown as Assumptions;
    expect(resolveAssumptions(none, emptyPrefs())).toBe(none);
  });
});

describe("saved views", () => {
  it("adds, dedupes by id on normalize, removes, and refuses a blank name", () => {
    const p = withSaved(emptyPrefs(), "saved_filters", { id: "a", name: " Coffee runs ", query: "categories=Dining" }, NOW)!;
    expect(p.saved_filters).toEqual([{ id: "a", name: "Coffee runs", query: "categories=Dining", created_at: NOW }]);
    expect(withSaved(p, "saved_filters", { id: "b", name: "   ", query: "" }, NOW)).toBeNull();
    expect(withoutSaved(p, "saved_filters", "a", NOW).saved_filters).toEqual([]);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const n = normalizePrefs({ version: 1, updated_at: NOW, saved_filters: [p.saved_filters[0], p.saved_filters[0]] });
    expect(n.saved_filters).toHaveLength(1);
    expect(warn, "a duplicate id is a silent dedupe, not a malformed entry").not.toHaveBeenCalled();
    warn.mockRestore();
  });
  it("refuses the 51st view (the prefs doc has a 64 KB server cap)", () => {
    let p = emptyPrefs();
    for (let i = 0; i < MAX_SAVED; i++) p = withSaved(p, "saved_filters", { id: `v${i}`, name: `v${i}`, query: "" }, NOW)!;
    expect(p.saved_filters).toHaveLength(MAX_SAVED);
    expect(withSaved(p, "saved_filters", { id: "x", name: "x", query: "" }, NOW)).toBeNull();
  });
  it("normalize drops malformed entries with one warning each and strips unknown keys", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const n = normalizePrefs({ version: 1, updated_at: NOW, saved_filters: [
      { id: 7, name: null }, "junk", { id: "ok", name: "ok", query: "q=x", created_at: NOW, extra: true }] });
    expect(n.saved_filters).toEqual([{ id: "ok", name: "ok", query: "q=x", created_at: NOW }]);
    expect(warn).toHaveBeenCalledTimes(2);
    warn.mockRestore();
    expect(normalizePrefs({ version: 1, updated_at: NOW }).saved_filters).toEqual([]);
  });
  it("a non-list section normalizes to empty without a warning, and a view without a query is malformed", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(normalizePrefs({ version: 1, updated_at: NOW, saved_filters: { id: "a" } }).saved_filters).toEqual([]);
    expect(warn).not.toHaveBeenCalled();
    expect(normalizePrefs({ version: 1, updated_at: NOW, saved_filters: [{ id: "a", name: "a", created_at: NOW }] })
      .saved_filters).toEqual([]);
    expect(warn).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });
  it("an entry over the per-entry cap is refused on the way in and dropped, with one warning, on the way out", () => {
    // the store refuses an entry over 4 KB (deploy/scenario_store.py); the app's own cap is 2 KB and
    // covers the whole entry (name + query), so no honest save can be refused by the store
    const pad = (n: number) => "q=" + "x".repeat(n);
    const room = MAX_SAVED_ENTRY_CHARS - JSON.stringify({ id: "a", name: "n", query: "", created_at: NOW }).length;
    expect(withSaved(emptyPrefs(), "saved_filters", { id: "a", name: "n", query: pad(room - 2) }, NOW), "exactly at the cap").not.toBeNull();
    expect(withSaved(emptyPrefs(), "saved_filters", { id: "a", name: "n", query: pad(room - 1) }, NOW), "one over").toBeNull();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const n = normalizePrefs({ version: 1, updated_at: NOW, saved_filters: [
      { id: "big", name: "big", query: pad(MAX_SAVED_ENTRY_CHARS), created_at: NOW },
      { id: "ok", name: "ok", query: "q=x", created_at: NOW }] });
    expect(n.saved_filters.map((f) => f.id)).toEqual(["ok"]);
    expect(warn).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });
  it("a name longer than the input allows is refused, not truncated", () => {
    const long = "n".repeat(MAX_SAVED_NAME + 1);
    expect(withSaved(emptyPrefs(), "saved_filters", { id: "a", name: long, query: "" }, NOW)).toBeNull();
    expect(withSaved(emptyPrefs(), "saved_filters", { id: "a", name: long.slice(1), query: "" }, NOW)).not.toBeNull();
  });
  it("renames in place (trimmed, same id, same query) and refuses a blank name or an unknown id", () => {
    const p = withSaved(emptyPrefs(), "saved_filters", { id: "a", name: "Coffee", query: "q=x" }, NOW)!;
    const r = withRenamedSaved(p, "saved_filters", "a", "  Espresso ", "2026-08-01T00:00:00Z")!;
    expect(r.saved_filters).toEqual([{ id: "a", name: "Espresso", query: "q=x", created_at: NOW }]);
    expect(r.updated_at).toBe("2026-08-01T00:00:00Z");
    expect(withRenamedSaved(p, "saved_filters", "a", "  ", NOW)).toBeNull();
    expect(withRenamedSaved(p, "saved_filters", "zzz", "Espresso", NOW)).toBeNull();
  });
  it("normalizeSaved is generic over the kind's own fields (saved reports reuse it)", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    interface Rep { id: string; name: string; created_at: string; spec: { n: number } }
    const rows = normalizeSaved<Rep>([
      { id: "r1", name: "r1", created_at: NOW, spec: { n: 1 }, junk: 1 },
      { id: "r2", name: "r2", created_at: NOW, spec: { n: "nope" } },
      { id: "r1", name: "dup", created_at: NOW, spec: { n: 9 } }], "reports",
    (e) => (typeof (e.spec as { n?: unknown } | undefined)?.n === "number" ? { spec: { n: (e.spec as { n: number }).n } } : null));
    expect(rows).toEqual([{ id: "r1", name: "r1", created_at: NOW, spec: { n: 1 } }]);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0][0])).toContain("reports");
    warn.mockRestore();
  });
});

describe("saved reports", () => {
  const spec = { ...DEFAULT_SPEC, filters: "categories=Dining", groupBy: "vendor", measure: "count" } as const;
  it("adds a report with its spec and a created_at, trims the name, refuses a blank one, and removes by id", () => {
    const p = withSaved(emptyPrefs(), "reports", { id: "r1", name: " Dining by vendor ", spec }, NOW)!;
    expect(p.reports).toEqual([{ id: "r1", name: "Dining by vendor", spec, created_at: NOW }]);
    expect(p.updated_at).toBe(NOW);
    expect(p.saved_filters, "the views list is untouched").toEqual([]);
    expect(withSaved(p, "reports", { id: "r2", name: "   ", spec }, NOW)).toBeNull();
    expect(withoutSaved(p, "reports", "r1", NOW).reports).toEqual([]);
    expect(withoutSaved(p, "reports", "nope", NOW).reports).toHaveLength(1);
  });
  it("refuses the 51st report; the views list has its own 50, not shared", () => {
    let p = emptyPrefs();
    for (let i = 0; i < MAX_SAVED; i++) p = withSaved(p, "reports", { id: `r${i}`, name: `r${i}`, spec: DEFAULT_SPEC }, NOW)!;
    expect(p.reports).toHaveLength(MAX_SAVED);
    expect(withSaved(p, "reports", { id: "x", name: "x", spec: DEFAULT_SPEC }, NOW)).toBeNull();
    expect(withSaved(p, "saved_filters", { id: "v", name: "v", query: "" }, NOW), "a full reports list does not block a view").not.toBeNull();
  });
  it("normalize keeps a valid report and rebuilds it from the known fields (unknown keys, in the entry or its spec, do not survive)", () => {
    const n = normalizePrefs({ version: 1, updated_at: NOW, reports: [
      { id: "ok", name: "ok", created_at: NOW, extra: 1, spec: { ...spec, junk: true } }] });
    expect(n.reports).toEqual([{ id: "ok", name: "ok", created_at: NOW, spec }]);
  });
  it("normalize drops a report whose spec is invalid, malformed, or whose filters hold a non-filter key, with ONE warning each", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const bad = { id: "bad", name: "bad", spec: { filters: "", groupBy: "merchant", interval: "month", measure: "spend", chart: "stacked" }, created_at: NOW };
    const n = normalizePrefs({ version: 1, updated_at: NOW, reports: [
      "junk", bad, { id: "k", name: "k", created_at: NOW, spec: { ...spec, filters: "bogus=1" } },
      { id: "nospec", name: "nospec", created_at: NOW },
      { id: "good", name: "good", spec, created_at: NOW }] });
    expect(n.reports.map((r) => r.id)).toEqual(["good"]);
    expect(warn).toHaveBeenCalledTimes(4);
    expect(String(warn.mock.calls[0][0])).toBe("prefs: dropping a malformed reports entry");
    warn.mockRestore();
  });
  it("a repeated id dedupes silently (the first wins, no warning); an oversize entry is dropped with one", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const a = { id: "a", name: "first", spec, created_at: NOW };
    expect(normalizePrefs({ version: 1, updated_at: NOW, reports: [a, { ...a, name: "second" }] }).reports).toEqual([a]);
    expect(warn).not.toHaveBeenCalled();
    const huge = { id: "h", name: "h", created_at: NOW, spec: { ...spec, filters: "q=" + "x".repeat(MAX_SAVED_ENTRY_CHARS) } };
    expect(normalizePrefs({ version: 1, updated_at: NOW, reports: [huge, a] }).reports).toEqual([a]);
    expect(warn).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });
  it("an older doc with no reports section, or a non-list one, is an empty list without a warning", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(normalizePrefs({ version: 1, updated_at: NOW }).reports).toEqual([]);
    expect(normalizePrefs({ version: 1, updated_at: NOW, reports: { id: "a" } }).reports).toEqual([]);
    expect(normalizePrefs(null).reports).toEqual([]);
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });
  it("the per-entry cap applies: a spec whose filters are too long to store is refused, exactly at the cap is not", () => {
    const entry = (n: number) => ({ id: "a", name: "n", spec: { ...DEFAULT_SPEC, filters: "q=" + "x".repeat(n) } });
    const room = MAX_SAVED_ENTRY_CHARS - JSON.stringify({ ...entry(0), created_at: NOW }).length;
    expect(tryWithSaved(emptyPrefs(), "reports", entry(room), NOW), "exactly at the cap").toEqual(expect.objectContaining({ version: 1 }));
    expect(tryWithSaved(emptyPrefs(), "reports", entry(room + 1), NOW)).toBe("entry");
  });
  it("a doc round-trips through JSON unchanged, reports included", () => {
    const p = withSaved(withSaved(emptyPrefs(), "saved_filters", { id: "v", name: "v", query: "q=x" }, NOW)!, "reports", { id: "r", name: "r", spec }, NOW)!;
    expect(normalizePrefs(JSON.parse(JSON.stringify(p)))).toEqual(p);
  });
});

// The server refuses a prefs body over 64 KB and useSyncedDoc answers a failed PUT by going local-only, which
// would silently stop EVERY prefs edit from syncing. 50 x 2 KB can pass 64 KB, so the whole doc has a budget.
describe("the whole-doc budget", () => {
  const bytes = (d: PrefsDoc) => new TextEncoder().encode(JSON.stringify(d)).length;
  const big = (i: number) => ({ id: `v${i}`, name: `v${i}`, query: "q=" + "x".repeat(1500) });
  /** add 1.5 KB views until the doc refuses one */
  function fill(doc: PrefsDoc): { doc: PrefsDoc; stoppedBy: unknown } {
    for (let i = 0; i < MAX_SAVED; i++) {
      const r = tryWithSaved(doc, "saved_filters", big(i), NOW);
      if (typeof r === "string") return { doc, stoppedBy: r };
      doc = r;
    }
    return { doc, stoppedBy: "never" };
  }

  // The server's prefs body cap: deploy/scenario_store.py MAX_PREFS_BYTES, pinned to exactly 64 * 1024 by
  // tests/test_scenario_store.py test_the_prefs_body_cap_is_the_64_kb_the_dashboard_budgets_under. Change one
  // and change both: the pair is what keeps a full doc refused HERE (a message) rather than THERE (a 413).
  const SERVER_MAX_PREFS_BYTES = 64 * 1024;
  it("is a named constant below the server's 64 KB cap, leaving 8 KB for every other section", () => {
    expect(MAX_PREFS_DOC_BYTES).toBeLessThanOrEqual(SERVER_MAX_PREFS_BYTES - 8 * 1024);
  });

  it("stops adding with the 'full' reason before the 50-view cap, and the doc never passes the budget", () => {
    const { doc, stoppedBy } = fill(emptyPrefs());
    expect(stoppedBy).toBe("full");
    expect(doc.saved_filters.length).toBeGreaterThan(20);
    expect(doc.saved_filters.length, "the count cap was not what stopped it").toBeLessThan(MAX_SAVED);
    expect(bytes(doc)).toBeLessThanOrEqual(MAX_PREFS_DOC_BYTES);
    expect(bytes(doc)).toBeGreaterThan(MAX_PREFS_DOC_BYTES - 2048);        // refused only when the next one did not fit
    expect(withSaved(doc, "saved_filters", big(99), NOW), "the null form agrees").toBeNull();
  });

  it("counts every section, not just the list: it is the doc that must fit, for any kind", () => {
    const { doc } = fill(emptyPrefs());
    // another kind's list (saved reports): the budget is on the doc, so a full saved_filters list leaves no
    // room for a report either - and the generic op works on any SavedKey
    const report = { id: "r1", name: "r1", spec: { ...DEFAULT_SPEC, filters: "q=" + "y".repeat(1500) } };
    expect(tryWithSaved(doc, "reports", report, NOW)).toBe("full");
    const added = tryWithSaved(emptyPrefs(), "reports", report, NOW) as PrefsDoc;
    expect(typeof added, "an empty doc has room").toBe("object");
    expect(added.reports).toHaveLength(1);
    // and the other sections' size counts too: a payroll-heavy doc leaves less room
    const heavy = withBudget(emptyPrefs(), "Groceries", 1, NOW);
    for (let i = 0; i < 1500; i++) heavy.budgets[`Category number ${i}`] = i;
    expect(tryWithSaved(heavy, "saved_filters", big(0), NOW)).not.toBe("full");   // ~40 KB of budgets still fits one view
    for (let i = 1500; i < 3000; i++) heavy.budgets[`Category number ${i}`] = i;
    expect(tryWithSaved(heavy, "saved_filters", big(0), NOW)).toBe("full");
  });

  it("measures UTF-8 bytes, not characters (the body is what the server caps)", () => {
    const wide = (i: number) => ({ id: `w${i}`, name: "界".repeat(MAX_SAVED_NAME), query: "q=" + "界".repeat(600) });
    let doc = emptyPrefs();
    for (let i = 0; i < MAX_SAVED; i++) {
      const r = tryWithSaved(doc, "saved_filters", wide(i), NOW);
      if (typeof r === "string") { expect(r).toBe("full"); break; }
      doc = r;
    }
    expect(bytes(doc)).toBeLessThanOrEqual(MAX_PREFS_DOC_BYTES);
    expect(JSON.stringify(doc).length, "characters understate the body").toBeLessThan(bytes(doc));
  });

  it("a delete is never refused, so a full doc can always be made smaller; then there is room again", () => {
    const { doc } = fill(emptyPrefs());
    const smaller = withoutSaved(doc, "saved_filters", "v0", NOW);
    expect(bytes(smaller)).toBeLessThan(bytes(doc));
    expect(typeof tryWithSaved(smaller, "saved_filters", big(98), NOW)).toBe("object");
  });

  it("a rename that no longer fits is refused as 'full'; a shorter or equal one is fine", () => {
    let { doc } = fill(emptyPrefs());
    // top the doc up to the byte with small views, then a longer name has nowhere to go
    for (let i = 0; i < 300; i++) {
      const r = tryWithSaved(doc, "saved_filters", { id: `s${i}`, name: "s", query: "" }, NOW);
      if (typeof r === "string") break;
      doc = r;
    }
    expect(bytes(doc)).toBeGreaterThan(MAX_PREFS_DOC_BYTES - 200);
    expect(tryWithRenamedSaved(doc, "saved_filters", "v0", "n".repeat(MAX_SAVED_NAME), NOW)).toBe("full");
    expect(typeof tryWithRenamedSaved(doc, "saved_filters", "v0", "v", NOW)).toBe("object");
    expect(withRenamedSaved(doc, "saved_filters", "v0", "n".repeat(MAX_SAVED_NAME), NOW)).toBeNull();
  });

  it("the reasons are distinct: name, count, entry, full, unknown", () => {
    const doc = emptyPrefs();
    expect(tryWithSaved(doc, "saved_filters", { id: "a", name: " ", query: "" }, NOW)).toBe("name");
    expect(tryWithSaved(doc, "saved_filters", { id: "a", name: "a", query: "q=" + "x".repeat(MAX_SAVED_ENTRY_CHARS) }, NOW)).toBe("entry");
    let full = emptyPrefs();
    for (let i = 0; i < MAX_SAVED; i++) full = withSaved(full, "saved_filters", { id: `c${i}`, name: "c", query: "" }, NOW)!;
    expect(tryWithSaved(full, "saved_filters", { id: "z", name: "z", query: "" }, NOW)).toBe("count");
    expect(tryWithRenamedSaved(full, "saved_filters", "nope", "x", NOW)).toBe("unknown");
    expect(tryWithRenamedSaved(full, "saved_filters", "c0", "  ", NOW)).toBe("name");
  });
});

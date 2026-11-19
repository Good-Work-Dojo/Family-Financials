// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { EPOCH_ISO } from "../data/useSyncedDoc";
import {
  addScenario, duplicateScenario, emptyStore, MAX_SCENARIOS,
  newerOf, newScenarioId, normalizeStore, patchDraw, patchInputs,
  removeScenario, renameScenario, ScenarioStore, uniqueName,
} from "./scenarioStore";
import { DEFAULT_INPUTS } from "./secondHouse";

const T1 = "2026-07-01T00:00:00Z";
const T2 = "2026-07-02T00:00:00Z";

const seeded = (): ScenarioStore => addScenario(emptyStore(), "id-1", T1)!;

describe("normalizeStore", () => {
  it("round-trips a valid document", () => {
    const s = seeded();
    expect(normalizeStore(JSON.parse(JSON.stringify(s)))).toEqual(s);
  });
  it("collapses garbage to an empty store", () => {
    for (const junk of [undefined, null, 42, "hi", [], { version: 2, scenarios: [] }, { version: 1 }]) {
      expect(normalizeStore(junk)).toEqual(emptyStore());
    }
  });
  it("truncates past the scenario cap", () => {
    const many = { version: 1, updated_at: T1,
      scenarios: Array.from({ length: 7 }, (_, k) => ({
        id: `id-${k}`, name: `S${k}`, created_at: T1, updated_at: T1,
        inputs: {}, draws: {} })) };
    expect(normalizeStore(many).scenarios).toHaveLength(MAX_SCENARIOS);
  });
  it("backfills missing input keys from DEFAULT_INPUTS (forward compat)", () => {
    const doc = { version: 1, updated_at: T1, scenarios: [{
      id: "a", name: "A", created_at: T1, updated_at: T1,
      inputs: { price: 750_000 }, draws: {} }] };
    const s = normalizeStore(doc).scenarios[0];
    expect(s.inputs.price).toBe(750_000);
    expect(s.inputs.ratePct).toBe(DEFAULT_INPUTS.ratePct);
    expect(s.inputs.cleaningPerStay).toBe(DEFAULT_INPUTS.cleaningPerStay);
  });
  it("drops non-finite, non-numeric, and non-positive draws", () => {
    const doc = { version: 1, updated_at: T1, scenarios: [{
      id: "a", name: "A", created_at: T1, updated_at: T1, inputs: {},
      draws: { good: 500, str: "abc", neg: -5, zero: 0, inf: Infinity } }] };
    expect(normalizeStore(doc).scenarios[0].draws).toEqual({ good: 500 });
  });
  it("skips scenario entries without an id", () => {
    const doc = { version: 1, updated_at: T1,
      scenarios: [null, { name: "no id" }, { id: "ok", inputs: {}, draws: {} }] };
    expect(normalizeStore(doc).scenarios.map((s) => s.id)).toEqual(["ok"]);
  });
});

describe("newerOf", () => {
  it("picks the later updated_at; a never-written server store always loses", () => {
    const server = emptyStore();               // EPOCH_ISO sentinel
    const local = { ...seeded(), updated_at: T1 };
    expect(newerOf(server, local)).toBe(local);
    expect(newerOf(local, server)).toBe(local);
    expect(server.updated_at).toBe(EPOCH_ISO);
  });
});

describe("mutations", () => {
  it("addScenario names sequentially and returns null at the cap", () => {
    let s = emptyStore();
    for (let k = 1; k <= MAX_SCENARIOS; k++) s = addScenario(s, `id-${k}`, T1)!;
    expect(s.scenarios.map((x) => x.name))
      .toEqual(["Scenario 1", "Scenario 2", "Scenario 3", "Scenario 4", "Scenario 5"]);
    expect(addScenario(s, "id-6", T1)).toBeNull();
  });
  it("duplicate deep-copies inputs and draws under a unique name", () => {
    let s = seeded();
    s = patchInputs(s, "id-1", { price: 900_000 }, T1);
    s = patchDraw(s, "id-1", "chk", 5_000, T1);
    const dup = duplicateScenario(s, "id-1", "id-2", T2)!;
    const [a, b] = dup.scenarios;
    expect(b.name).toBe("Scenario 1 (2)");
    expect(b.inputs).toEqual(a.inputs);
    expect(b.draws).toEqual(a.draws);
    expect(b.inputs).not.toBe(a.inputs);
    expect(b.draws).not.toBe(a.draws);
    expect(duplicateScenario(s, "missing", "id-3", T2)).toBeNull();
  });
  it("rename trims, ignores empty, and stamps timestamps", () => {
    const s = renameScenario(seeded(), "id-1", "  Beach house  ", T2);
    expect(s.scenarios[0].name).toBe("Beach house");
    expect(s.scenarios[0].updated_at).toBe(T2);
    expect(s.updated_at).toBe(T2);
    expect(renameScenario(seeded(), "id-1", "   ", T2).scenarios[0].name).toBe("Scenario 1");
  });
  it("remove drops the scenario", () => {
    expect(removeScenario(seeded(), "id-1", T2).scenarios).toEqual([]);
  });
  it("patchDraw(≤0) deletes the key", () => {
    let s = patchDraw(seeded(), "id-1", "chk", 5_000, T1);
    expect(s.scenarios[0].draws).toEqual({ chk: 5_000 });
    s = patchDraw(s, "id-1", "chk", 0, T2);
    expect(s.scenarios[0].draws).toEqual({});
  });
  it("never mutates its argument", () => {
    const s = seeded();
    const snapshot = JSON.parse(JSON.stringify(s));
    addScenario(s, "id-2", T2);
    duplicateScenario(s, "id-1", "id-3", T2);
    renameScenario(s, "id-1", "X", T2);
    removeScenario(s, "id-1", T2);
    patchInputs(s, "id-1", { price: 1 }, T2);
    patchDraw(s, "id-1", "chk", 1, T2);
    expect(s).toStrictEqual(snapshot);
  });
});

describe("newScenarioId", () => {
  it("returns unique v4-shaped ids without needing crypto.randomUUID", () => {
    const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
    const a = newScenarioId();
    const b = newScenarioId();
    expect(a).toMatch(V4);
    expect(b).toMatch(V4);
    expect(a).not.toBe(b);
  });
});

describe("uniqueName", () => {
  it("suffixes (n) until free", () => {
    expect(uniqueName("A", [])).toBe("A");
    expect(uniqueName("A", ["A"])).toBe("A (2)");
    expect(uniqueName("A", ["A", "A (2)"])).toBe("A (3)");
  });
});

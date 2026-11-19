// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { mortgageFactory } from "./__fixtures__";
import { effectiveModules, factsOf, gateOpen, isModuleId, MODULE_IDS, MODULE_NAMES, moduleSource } from "./modules";

describe("moduleSource", () => {
  // the config states on, off or nothing; the family's switch is absent, on or off
  const table: [Record<string, unknown>, Record<string, unknown>, ReturnType<typeof moduleSource>][] = [
    [{ home_buying: true }, {}, { on: true, from: "config", config: true }],
    [{ home_buying: true }, { home_buying: true }, { on: true, from: "dashboard", config: true }],
    [{ home_buying: true }, { home_buying: false }, { on: false, from: "dashboard", config: true }],
    [{ home_buying: false }, {}, { on: false, from: "config", config: false }],
    [{ home_buying: false }, { home_buying: true }, { on: true, from: "dashboard", config: false }],
    [{ home_buying: false }, { home_buying: false }, { on: false, from: "dashboard", config: false }],
    [{}, {}, { on: false, from: "default", config: null }],
    [{}, { home_buying: true }, { on: true, from: "dashboard", config: null }],
    [{}, { home_buying: false }, { on: false, from: "dashboard", config: null }],
  ];
  it.each(table)("config %j, switch %j: %j", (seed, prefs, want) => {
    expect(moduleSource("home_buying", seed, prefs)).toEqual(want);
  });
  it("reads only a boolean as a stated value: anything else falls through to the next source", () => {
    expect(moduleSource("home_buying", { home_buying: "yes" }, { home_buying: 1 })).toEqual({ on: false, from: "default", config: null });
    expect(moduleSource("home_buying", { home_buying: true }, { home_buying: null })).toEqual({ on: true, from: "config", config: true });
  });
  it("is what effectiveModules reads, module by module", () => {
    for (const [seed, prefs, want] of table) expect(effectiveModules(seed, prefs)).toEqual({ home_buying: want.on });
  });
});

describe("effectiveModules", () => {
  it("is off with nothing stated anywhere", () => {
    expect(effectiveModules({}, {})).toEqual({ home_buying: false });
  });
  it("takes the config's seed where the family states nothing", () => {
    expect(effectiveModules({ home_buying: true }, {})).toEqual({ home_buying: true });
    expect(effectiveModules({ home_buying: false }, {})).toEqual({ home_buying: false });
  });
  it("takes the family's preference over the seed, both ways: a false turns off what the config switches on", () => {
    expect(effectiveModules({ home_buying: true }, { home_buying: false })).toEqual({ home_buying: false });
    expect(effectiveModules({ home_buying: false }, { home_buying: true })).toEqual({ home_buying: true });
    expect(effectiveModules({}, { home_buying: true })).toEqual({ home_buying: true });
  });
  it("reads only booleans, and only module ids: anything else falls through to the next source", () => {
    expect(effectiveModules({ home_buying: true }, { home_buying: "no" })).toEqual({ home_buying: true });
    expect(effectiveModules({ home_buying: 1 }, { home_buying: null })).toEqual({ home_buying: false });
    const out = effectiveModules({ budgeting: true }, { travel: true, constructor: true });
    expect(Object.keys(out)).toEqual([...MODULE_IDS]);
    expect(out).toEqual({ home_buying: false });
  });
  it("lists every module once, each with a name", () => {
    expect(new Set(MODULE_IDS).size).toBe(MODULE_IDS.length);
    for (const id of MODULE_IDS) {
      expect(isModuleId(id)).toBe(true);
      expect(MODULE_NAMES[id].length > 0, id).toBe(true);
    }
    expect(isModuleId("travel")).toBe(false);
  });
});

describe("factsOf and gateOpen", () => {
  it("the household has a mortgage when an account is marked as the mortgage", () => {
    expect(factsOf({ mortgage: null })).toEqual({ mortgage: false });
    expect(factsOf({ mortgage: mortgageFactory() })).toEqual({ mortgage: true });
  });
  it("a gate opens when its module is on and the household has what it needs; no gate is always open", () => {
    const on = { home_buying: true }, off = { home_buying: false };
    const has = { mortgage: true }, lacks = { mortgage: false };
    expect(gateOpen({}, off, lacks)).toBe(true);
    expect(gateOpen({ module: "home_buying" }, on, lacks)).toBe(true);
    expect(gateOpen({ module: "home_buying" }, off, has)).toBe(false);
    expect(gateOpen({ needs: "mortgage" }, off, has)).toBe(true);
    expect(gateOpen({ needs: "mortgage" }, on, lacks)).toBe(false);
    expect(gateOpen({ module: "home_buying", needs: "mortgage" }, on, has)).toBe(true);
    expect(gateOpen({ module: "home_buying", needs: "mortgage" }, on, lacks)).toBe(false);
  });
});

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { HouseholdPerson } from "../data/types";
import { allocationCaption, defaultAllocationTargets } from "./allocation";

const person = (name: string, role: HouseholdPerson["role"], birth_year: number): HouseholdPerson =>
  ({ name, role, birth_year });

const sum = (t: Record<string, number>) => Object.values(t).reduce((s, v) => s + v, 0);

describe("defaultAllocationTargets", () => {
  it("applies 110 − age at the OLDEST owner's age and keeps cash/alts fixed", () => {
    const people = [person("A", "owner", 1990), person("B", "owner", 1980), person("K", "kid", 2015)];
    const d = defaultAllocationTargets(people, 2026);
    expect(d.ownerAge).toBe(46);                       // 2026 − 1980; the kid is ignored
    expect(d.targets).toEqual({ stocks: 64, bonds: 26, cash: 5, alts: 5 });
  });
  it("ignores kids even when they are the oldest birth year in the list order", () => {
    const d = defaultAllocationTargets([person("K", "kid", 1950), person("A", "owner", 1996)], 2026);
    expect(d.ownerAge).toBe(30);
    expect(d.targets.stocks).toBe(80);
  });
  it("always sums to 100, bonds absorbing the remainder", () => {
    for (const birth of [1940, 1960, 1975, 1988, 2000, 2010]) {
      const t = defaultAllocationTargets([person("A", "owner", birth)], 2026).targets;
      expect(sum(t)).toBe(100);
      expect(t.bonds).toBeGreaterThanOrEqual(0);
    }
  });
  it("clamps so a very old or very young owner never yields a negative leg", () => {
    const old = defaultAllocationTargets([person("A", "owner", 1900)], 2026);   // age 126 → 110 − 126 < 0
    expect(old.targets).toEqual({ stocks: 0, bonds: 90, cash: 5, alts: 5 });
    const young = defaultAllocationTargets([person("A", "owner", 2024)], 2026); // age 2 → 108 > 90
    expect(young.targets).toEqual({ stocks: 90, bonds: 0, cash: 5, alts: 5 });
  });
  it("falls back to the fixed defaults, with no age, when there is no owner", () => {
    const d = defaultAllocationTargets([person("K", "kid", 2015)], 2026);
    expect(d.ownerAge).toBeNull();
    expect(d.targets).toEqual({ stocks: 72, bonds: 18, cash: 5, alts: 5 });
    expect(defaultAllocationTargets([], 2026).ownerAge).toBeNull();
  });
  // an adult with no birth year (household.yaml leaves `birth_year` out, so the export has no key)
  const ageless = (name: string): HouseholdPerson => ({ name, role: "owner" });
  it("uses the adults that have a birth year: one adult without one is left out of the age, not counted as NaN", () => {
    for (const people of [[ageless("Alex"), person("Sam", "owner", 1980)], [person("Sam", "owner", 1980), ageless("Alex")]]) {
      const d = defaultAllocationTargets(people, 2026);
      expect(d.ownerAge).toBe(46);                     // Sam's age; Alex has none
      expect(d.targets).toEqual({ stocks: 64, bonds: 26, cash: 5, alts: 5 });
    }
  });
  it("falls back to the fixed defaults, with no age, when no adult has a birth year", () => {
    const d = defaultAllocationTargets([ageless("Alex"), ageless("Sam"), person("Robin", "kid", 2015)], 2026);
    expect(d.ownerAge).toBeNull();
    expect(d.targets).toEqual({ stocks: 72, bonds: 18, cash: 5, alts: 5 });
  });
  it("a household whose people hold no adult (children only, one without a birth year) gets the fixed suggestion, without throwing", () => {
    const people: HouseholdPerson[] = [person("Robin", "kid", 2020), { name: "Sam", role: "kid" }];
    let d: ReturnType<typeof defaultAllocationTargets> | undefined;
    expect(() => { d = defaultAllocationTargets(people, 2030); }).not.toThrow();
    expect(d!.ownerAge).toBeNull();
    expect(d!.targets).toEqual({ stocks: 72, bonds: 18, cash: 5, alts: 5 });
    expect(allocationCaption(d!.ownerAge)).toMatch(/^a fixed starting point, since no adult's birth year is in the household config/);
  });
  it("returns a fresh object each call (the card keeps it as a draft)", () => {
    const a = defaultAllocationTargets([], 2026).targets;
    a.stocks = 1;
    expect(defaultAllocationTargets([], 2026).targets.stocks).toBe(72);
  });
});

describe("allocationCaption", () => {
  it("says the suggestion is a rule of thumb, what the rule is, and the age it was read at", () => {
    expect(allocationCaption(46)).toBe("a common rule of thumb for age 46 (the oldest adult's): 110 minus that age in stocks "
      + "(at most 90%), 5% in cash, 5% in alternatives and the rest in bonds");
  });
  it("with no age it says the starting point is fixed, and why", () => {
    expect(allocationCaption(null)).toBe("a fixed starting point, since no adult's birth year is in the household config: "
      + "72% in stocks, 5% in cash, 5% in alternatives and the rest in bonds");
  });
});

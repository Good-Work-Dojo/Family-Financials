// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { presetRange } from "./dateRange";
import { EMPTY_FILTERS } from "./filters";
import { filterSummary, otherFilterCount, windowLabel } from "./filterSummary";

const MAX = "2026-06-30";
const F = (over: object) => ({ ...EMPTY_FILTERS, ...over });

describe("filterSummary: the phone's one-line version of the filter bar", () => {
  it("names the window in the chips' own words, then counts the OTHER filters (names a lone one)", () => {
    expect(filterSummary(F({ from: presetRange("6m", MAX).from }), MAX)).toBe("6M · no filters");
    expect(filterSummary(F({ from: presetRange("6m", MAX).from, categories: ["Dining"], q: "taco" }), MAX)).toBe("6M · 2 filters");
    expect(filterSummary(F({ from: presetRange("12m", MAX).from, vendors: ["V"] }), MAX)).toBe("12M · V");
    expect(filterSummary(F({}), MAX)).toBe("All · no filters");
    expect(windowLabel(`${MAX.slice(0, 4)}-01-01`, undefined, MAX)).toBe("YTD");
  });
  it("a custom window reads as its dates, never as a preset", () => {
    expect(windowLabel("2026-01-03", "2026-03-09", MAX)).toBe("Jan 3, 2026 – Mar 9, 2026");
    expect(windowLabel("2026-01-03", undefined, MAX)).toBe("since Jan 3, 2026");
    expect(windowLabel(undefined, "2026-03-09", MAX)).toBe("through Mar 9, 2026");
  });
  it("with no transaction yet no window is a preset: no window at all reads All, dates read as dates", () => {
    expect(filterSummary(F({}), null)).toBe("All · no filters");
    expect(windowLabel(undefined, undefined, null)).toBe("All");
    expect(windowLabel("2026-01-03", undefined, null)).toBe("since Jan 3, 2026");
    expect(filterSummary(F({ categories: ["Dining"] }), null)).toBe("All · Dining");
  });
  it("the window is not also counted as a filter; the internal-transfers box is", () => {
    expect(otherFilterCount(F({ from: "2026-01-03", to: "2026-02-01" }))).toBe(0);
    expect(otherFilterCount(F({ includeInternal: true }))).toBe(1);
    expect(otherFilterCount(F({ tags: ["kids"], needsReviewOnly: true, amountMin: 5, direction: "expense" }))).toBe(4);
  });
});

describe("filterSummary names a lone filter", () => {
  const six = presetRange("6m", MAX).from;
  const one = (over: object, names?: ReadonlyMap<string, string>) => filterSummary(F({ from: six, ...over }), MAX, names);
  it("a facet by its value, in the order category, vendor, owner, account, tag, type", () => {
    expect(one({ categories: ["Dining"] })).toBe("6M · Dining");
    expect(one({ vendors: ["Costco"] })).toBe("6M · Costco");
    expect(one({ owners: ["Joint"] })).toBe("6M · Joint");
    expect(one({ tags: ["kids"] })).toBe("6M · kids");
    expect(one({ types: ["income"] })).toBe("6M · income");
    expect(one({ categories: ["Dining", "Groceries", "Travel"] })).toBe("6M · Dining +2");
  });
  it("a source group by its label, after the facet's name", () => {
    expect(one({ sources: ["ai"] })).toBe("6M · Filed by: AI");
    expect(one({ sources: ["rules", "ai"] })).toBe("6M · Filed by: Your rules +1");
  });
  it("an account by its NAME when the registry knows it, else the id", () => {
    expect(one({ accounts: ["demo_checking_0101"] }, new Map([["demo_checking_0101", "Joint Checking"]]))).toBe("6M · Joint Checking");
    expect(one({ accounts: ["old_9"] }, new Map())).toBe("6M · old_9");
  });
  it("the search by its text (cut when long); the rest by name - never an amount, which privacy mode masks", () => {
    expect(one({ q: "taco" })).toBe("6M · “taco”");
    expect(one({ q: "a very long search phrase indeed" })).toBe("6M · “a very long search phra…”");
    expect(one({ amountMin: 12345 })).toBe("6M · amount range");
    expect(one({ amountMax: 99 })).not.toMatch(/\d{2}/);
    expect(one({ direction: "expense" })).toBe("6M · money out");
    expect(one({ direction: "income" })).toBe("6M · money in");
    expect(one({ needsReviewOnly: true })).toBe("6M · needs review");
    expect(one({ includeInternal: true })).toBe("6M · with transfers");
  });
  it("two or more are counted, never named", () => {
    expect(one({ categories: ["Dining"], vendors: ["Costco"] })).toBe("6M · 2 filters");
  });
});

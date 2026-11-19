// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { CategoryRename } from "../data/types";
import { mapCategory, mapCategoryName } from "./categoryRenames";

// the same cases etl's test_category_rename_reaches_every_consumer pins, so the two sides agree
const R: CategoryRename[] = [
  { from: { category: "Eating Out" }, to: { category: "Dining" } },                                  // whole category
  { from: { category: "Dining", subcategory: "Pubs" }, to: { category: "Dining", subcategory: "Bars" } }, // one pair
  { from: { category: "Nightlife", subcategory: "Shows" }, to: { category: "Entertainment" } },     // pair -> bare
  { from: { category: "Odd", subcategory: null }, to: { category: "Dining" } },                     // the null pair
];

describe("mapCategory mirrors CategoryRenamer.map", () => {
  it("moves a whole category and keeps each reference's own subcategory", () => {
    expect(mapCategory("Eating Out", "Coffee", R)).toEqual(["Dining", "Coffee"]);
    expect(mapCategory("Eating Out", null, R)).toEqual(["Dining", null]);
  });
  it("moves exactly one pair and leaves the category's other pairs alone", () => {
    expect(mapCategory("Dining", "Pubs", R)).toEqual(["Dining", "Bars"]);
    expect(mapCategory("Dining", "Coffee", R)).toEqual(["Dining", "Coffee"]);
  });
  it("a pair renamed to a bare category drops the subcategory; a null-pair entry matches only null", () => {
    expect(mapCategory("Nightlife", "Shows", R)).toEqual(["Entertainment", null]);
    expect(mapCategory("Nightlife", "Other", R)).toEqual(["Nightlife", "Other"]);
    expect(mapCategory("Odd", null, R)).toEqual(["Dining", null]);
    expect(mapCategory("Odd", "Sub", R)).toEqual(["Odd", "Sub"]);
  });
  it("applies once, in file order, without chaining", () => {
    const chain: CategoryRename[] = [{ from: { category: "A" }, to: { category: "Dining" } },
      { from: { category: "Dining" }, to: { category: "Shopping" } }];
    expect(mapCategory("A", null, chain)).toEqual(["Dining", null]);
    expect(mapCategory("Dining", null, chain)).toEqual(["Shopping", null]);
  });
  it("is the identity with no entries", () => {
    expect(mapCategory("Dining", "Coffee", [])).toEqual(["Dining", "Coffee"]);
  });
});

describe("mapCategoryName", () => {
  it("applies whole-category entries only", () => {
    expect(mapCategoryName("Eating Out", R)).toBe("Dining");
    expect(mapCategoryName("Dining", R)).toBe("Dining");           // only a pair rename names Dining: it never moves the category
    expect(mapCategoryName("Nightlife", R)).toBe("Nightlife");     // a pair rename to a bare category is not a whole rename
    expect(mapCategoryName("Odd", R)).toBe("Odd");                 // a null-pair entry is a pair rename too
  });
});

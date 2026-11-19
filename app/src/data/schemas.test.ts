// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { AssumptionsSchema, CategoryRenameSchema } from "./schemas";

// What etl exports.export_category_renames emits for tests/test_exports.py's sample list: a
// category-only entry, a pair, a pair renamed to a bare category (no `to.subcategory` key) and the
// null pair (`from.subcategory: null`). The key-presence rules are the whole contract, so the
// schema must accept exactly these shapes and refuse the ones the ETL never writes.
const ETL_SAMPLE = [
  { from: { category: "Eating Out" }, to: { category: "Dining" } },
  { from: { category: "Dining", subcategory: "Pubs" }, to: { category: "Dining", subcategory: "Bars" } },
  { from: { category: "Nightlife", subcategory: "Shows" }, to: { category: "Entertainment" } },
  { from: { category: "Odd", subcategory: null }, to: { category: "Dining" } },
];
const list = AssumptionsSchema.shape.category_renames;

describe("category_renames schema", () => {
  it("accepts an ETL-shaped non-empty list and the empty live one, and keeps every key", () => {
    const parsed = list.safeParse(ETL_SAMPLE);
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data).toEqual(ETL_SAMPLE);
      expect("subcategory" in parsed.data[0].from).toBe(false);        // absent stays absent: a whole-category entry
      expect(parsed.data[3].from.subcategory).toBeNull();               // null stays null: the null pair
      expect("subcategory" in parsed.data[2].to).toBe(false);
    }
    expect(list.safeParse([]).success).toBe(true);
  });

  it("refuses shapes the ETL never writes: a null to.subcategory, a stray key, a missing category", () => {
    expect(CategoryRenameSchema.safeParse(
      { from: { category: "A" }, to: { category: "Dining", subcategory: null } }).success).toBe(false);
    expect(CategoryRenameSchema.safeParse(
      { from: { category: "A" }, to: { category: "Dining" }, note: "x" }).success).toBe(false);
    expect(CategoryRenameSchema.safeParse(
      { from: { category: "A", extra: 1 }, to: { category: "Dining" } }).success).toBe(false);
    expect(CategoryRenameSchema.safeParse({ from: {}, to: { category: "Dining" } }).success).toBe(false);
    expect(list.safeParse(undefined).success).toBe(false);              // the key is always present
  });
});

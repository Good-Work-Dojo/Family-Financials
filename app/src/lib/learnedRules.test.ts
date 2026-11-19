// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { VendorEntry } from "../data/types";
import { activeLearnedRules } from "./learnedRules";

const v = (vendor: string, category_source: VendorEntry["category_source"], category: string | null = "Dining"): VendorEntry => ({
  vendor, has_rule: category_source === "vendor_rule", category, subcategory: null, category_source,
  learn: true, txns: 3, spend_ttm: 30, first_seen: "2026-01-01", last_seen: "2026-03-01",
});

describe("activeLearnedRules", () => {
  it("keeps learned vendors only, sorted by name", () => {
    const out = activeLearnedRules([v("Zeta", "learned"), v("Uber", "vendor_rule"), v("Mystery", null, null), v("alpha", "learned")]);
    expect(out.map((r) => r.vendor)).toEqual(["alpha", "Zeta"]);
  });
  it("is empty on the vendors.json placeholder", () => {
    expect(activeLearnedRules([])).toEqual([]);
  });
});

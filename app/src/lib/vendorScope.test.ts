// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { VendorEntry } from "../data/types";
import { txFactory } from "./__fixtures__";
import { defaultScope, learnIndex, offeredVendor, vendorLearnable } from "./vendorScope";

const tx = txFactory({ vendor: "Alpha Cafe" });
const entry = (vendor: string, learn: boolean): VendorEntry => ({
  vendor, learn, has_rule: false, category: null, subcategory: null, category_source: null,
  txns: 1, spend_ttm: 0, first_seen: "2026-01-01", last_seen: "2026-01-01",
});

describe("offeredVendor: the one predicate behind the 'Always for' chip and its default", () => {
  it("one spend row with a vendor, and several rows sharing it, offer that vendor", () => {
    expect(offeredVendor([tx()])).toBe("Alpha Cafe");
    expect(offeredVendor([tx(), tx({ type: "fee" })])).toBe("Alpha Cafe");
  });
  it("offers nothing over no rows, a row with no vendor, or an empty vendor", () => {
    expect(offeredVendor([])).toBeNull();
    expect(offeredVendor([tx({ vendor: null })])).toBeNull();
    expect(offeredVendor([tx({ vendor: "" })])).toBeNull();
  });
  it("offers nothing over rows of two vendors", () => {
    expect(offeredVendor([tx(), tx({ vendor: "Beta Books" })])).toBeNull();
  });
  it("offers nothing over an income row, alone or in a selection (its question is the stream)", () => {
    for (const type of ["income", "dividend", "interest"] as const) {
      expect(offeredVendor([tx({ type })]), type).toBeNull();
      expect(offeredVendor([tx(), tx({ type })]), `${type} beside a purchase`).toBeNull();
    }
  });
  it("offers nothing when any row is a part of a split purchase (a vendor answer never reaches a part)", () => {
    expect(offeredVendor([tx({ parent_id: "p1" })])).toBeNull();
    expect(offeredVendor([tx(), tx({ parent_id: "p1" })])).toBeNull();
  });
});

describe("vendorLearnable reads vendors.json's learn flag", () => {
  const idx = learnIndex([entry("Alpha Cafe", true), entry("Check", false)]);
  it("true, false, and null for a vendor the export does not list", () => {
    expect(vendorLearnable(idx, "Alpha Cafe")).toBe(true);
    expect(vendorLearnable(idx, "Check")).toBe(false);
    expect(vendorLearnable(idx, "Gamma")).toBeNull();
    expect(vendorLearnable(learnIndex([]), "Alpha Cafe"), "a missing vendors.json knows nothing").toBeNull();
  });
  it("matches case-insensitively, as the ETL matches a vendor answer to its rows", () => {
    expect(vendorLearnable(idx, "alpha cafe")).toBe(true);
    expect(vendorLearnable(idx, "CHECK")).toBe(false);
  });
});

describe("defaultScope", () => {
  it("starts on the vendor exactly when it is offered AND learnable", () => {
    expect(defaultScope("Alpha Cafe", true)).toBe("vendor");
    expect(defaultScope("Check", false)).toBe("txn");
    expect(defaultScope("Gamma", null), "unknown is the cautious case").toBe("txn");
    expect(defaultScope(null, true), "never a scope that is not offered").toBe("txn");
    expect(defaultScope(null, null)).toBe("txn");
  });
  it("composed with offeredVendor, never starts on the vendor for a row the chip is not offered on", () => {
    const idx = learnIndex([entry("Alpha Cafe", true)]);
    const start = (rows: Parameters<typeof offeredVendor>[0]) => {
      const v = offeredVendor(rows);
      return defaultScope(v, v === null ? null : vendorLearnable(idx, v));
    };
    expect(start([tx()])).toBe("vendor");
    expect(start([tx({ type: "income" })])).toBe("txn");
    expect(start([tx({ parent_id: "p1" })])).toBe("txn");
    expect(start([tx(), tx({ vendor: "Beta Books" })])).toBe("txn");
    expect(start([tx({ vendor: null })])).toBe("txn");
  });
});

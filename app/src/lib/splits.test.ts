// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { txFactory } from "./__fixtures__";
import {
  childrenByParent, overlayPurchases, partitionSplits, purchaseCount, purchaseKey, regroupSplits, splitNote, splitRules,
} from "./splits";

const tx = txFactory({ vendor: "Synth Mart" });
// a split purchase (-300 = -210 Groceries + -90 Shopping), another split (-100 = -70 + -30) and two ordinary rows
const p1 = tx({ id: "p1", amount: -300, is_parent: true, category: "Groceries" });
const c1a = tx({ id: "c1a", amount: -210, parent_id: "p1", category: "Groceries", category_source: "split" });
const c1b = tx({ id: "c1b", amount: -90, parent_id: "p1", category: "Shopping", subcategory: "Home Goods", category_source: "split" });
const p2 = tx({ id: "p2", amount: -100, is_parent: true });
const c2a = tx({ id: "c2a", amount: -70, parent_id: "p2", category: "Groceries", category_source: "split" });
const c2b = tx({ id: "c2b", amount: -30, parent_id: "p2", category: "Shopping", category_source: "split" });
const o1 = tx({ id: "o1", amount: -40, vendor: "Other" });
const o2 = tx({ id: "o2", amount: -5 });
const EXPORT = [o1, p1, c1a, c1b, p2, c2a, c2b, o2];

describe("partitionSplits", () => {
  it("category view = parts + ordinary rows; vendor view = parents + ordinary rows; parents indexed; export order kept", () => {
    const v = partitionSplits(EXPORT);
    expect(v.transactions.map((t) => t.id)).toEqual(["o1", "c1a", "c1b", "c2a", "c2b", "o2"]);
    expect(v.purchases.map((t) => t.id)).toEqual(["o1", "p1", "p2", "o2"]);
    expect([...v.splitParents.keys()]).toEqual(["p1", "p2"]);
    expect(v.splitParents.get("p1")).toBe(p1);
  });
  it("with no parent at all both views ARE the export (one array, no copy) — the schema-5 and no-rule case", () => {
    const rows = [o1, o2];
    const v = partitionSplits(rows);
    expect(v.transactions).toBe(rows);
    expect(v.purchases).toBe(rows);
    expect(v.splitParents.size).toBe(0);
  });
});

describe("purchaseKey / purchaseCount", () => {
  it("a part belongs to its parent's purchase; a split counts once however many parts are present", () => {
    expect(purchaseKey(c1a)).toBe("p1");
    expect(purchaseKey(o1)).toBe("o1");
    expect(purchaseCount([c1a, c1b, c2a, o1, o2])).toBe(4);
    expect(purchaseCount([c1a])).toBe(1);
    expect(purchaseCount([])).toBe(0);
  });
});

describe("childrenByParent", () => {
  it("groups the parts by parent in row order and ignores ordinary rows", () => {
    const m = childrenByParent([o1, c1a, c1b, c2a, c2b, o2]);
    expect([...m.keys()]).toEqual(["p1", "p2"]);
    expect(m.get("p1")!.map((t) => t.id)).toEqual(["c1a", "c1b"]);
  });
});

describe("overlayPurchases", () => {
  const purchases = [o1, p1, o2];
  it("swaps in the overlaid ordinary rows the overlay changed, keeps parents as they are", () => {
    const o1b = { ...o1, category: "Pets" };
    const out = overlayPurchases(purchases, [o1b, c1a, c1b, o2], new Set(["o1"]));
    expect(out.map((t) => t.id)).toEqual(["o1", "p1", "o2"]);
    expect(out[0]).toBe(o1b);
    expect(out[1]).toBe(p1);
  });
  it("is the same instance when nothing is pending, or when the pending rows are parts only", () => {
    expect(overlayPurchases(purchases, [o1, c1a, c1b, o2], new Set())).toBe(purchases);
    expect(overlayPurchases(purchases, [o1, { ...c1a, category: "Pets" }, c1b, o2], new Set(["c1a"]))).toBe(purchases);
  });
});

describe("regroupSplits", () => {
  const children = childrenByParent([c1a, c1b, c2a, c2b]);
  const parents = new Map([["p1", p1], ["p2", p2]]);
  it("a parent whose every part passed the filter stands in for them, in the first part's place", () => {
    expect(regroupSplits([o1, c1a, c1b, o2], children, parents).map((t) => t.id)).toEqual(["o1", "p1", "o2"]);
    expect(regroupSplits([c1b, o1, c1a], children, parents).map((t) => t.id)).toEqual(["p1", "o1"]);
  });
  it("a parent with only some parts present shows those parts themselves", () => {
    expect(regroupSplits([o1, c1a, c2a, c2b], children, parents).map((t) => t.id)).toEqual(["o1", "c1a", "p2"]);
  });
  it("is the same array when no row is a part, and shows a part whose parent is unknown as itself", () => {
    const rows = [o1, o2];
    expect(regroupSplits(rows, children, parents)).toBe(rows);
    expect(regroupSplits([c1a, c1b], children, new Map()).map((t) => t.id)).toEqual(["c1a", "c1b"]);
  });
});

// The Data page's "Split rules in force" card and the one-line footnote on the pages a rule moved (Budget, the income
// statement, Explore, the essentials card): both read the split structures, never a restated rule file.
describe("splitRules - the rules in force, summarised from the parents and their parts", () => {
  const v = partitionSplits(EXPORT);
  const rules = () => splitRules(v.splitParents, childrenByParent(v.transactions));
  it("one rule per vendor: the purchases split and each leg's share of the parts' dollars", () => {
    const [r] = rules();
    expect(rules()).toHaveLength(1);
    expect([r.vendor, r.purchases]).toEqual(["Synth Mart", 2]);
    // Groceries 210 + 70 of 400; Shopping / Home Goods 90 of 400; Shopping (no subcategory) 30 of 400
    expect(r.legs.map((l) => [l.category, l.subcategory, l.share])).toEqual([
      ["Groceries", null, 0.7], ["Shopping", "Home Goods", 0.225], ["Shopping", null, 0.075]]);
    expect(r.legs.reduce((s, l) => s + l.share, 0)).toBeCloseTo(1, 10);
  });
  it("shares follow the PARTS: a part answered into another category moves its share", () => {
    const repainted = v.transactions.map((t) => (t.id === "c1b" ? { ...t, category: "Groceries", subcategory: null } : t));
    const [r] = splitRules(v.splitParents, childrenByParent(repainted));
    expect(r.legs.find((l) => l.category === "Groceries")!.share).toBeCloseTo(370 / 400, 10);
  });
  it("a refund wrapper (positive amounts) counts by absolute value; a parent with no part present is no rule", () => {
    const refund = tx({ id: "rp", amount: 50, is_parent: true });
    const parts = [tx({ id: "ra", amount: 35, parent_id: "rp", category: "Groceries", category_source: "split" }),
      tx({ id: "rb", amount: 15, parent_id: "rp", category: "Shopping", category_source: "split" })];
    const ghost = tx({ id: "gp", amount: -9, is_parent: true, vendor: "Ghost" });
    const all = partitionSplits([refund, ...parts, ghost]);
    const rs = splitRules(all.splitParents, childrenByParent(all.transactions));
    expect(rs.map((r) => r.vendor)).toEqual(["Synth Mart"]);
    expect(rs[0].legs.map((l) => l.share)).toEqual([0.7, 0.3]);
  });
  it("is empty when nothing is split", () => {
    const none = partitionSplits([o1, o2]);
    expect(splitRules(none.splitParents, childrenByParent(none.transactions))).toEqual([]);
  });
});

describe("splitNote - the footnote under a category view", () => {
  const rules = () => {
    const v = partitionSplits(EXPORT);
    return splitRules(v.splitParents, childrenByParent(v.transactions));
  };
  it("is present when a shown category holds a leg, and names only the legs that are shown", () => {
    expect(splitNote(rules(), ["Dining", "Groceries"])).toBe("Groceries includes 70% of each Synth Mart order");
    expect(splitNote(rules(), ["Groceries", "Shopping"])).toBe("Groceries and Shopping include 70% and 30% of each Synth Mart order");
  });
  it("is null when no shown category holds a part - and with no rules at all", () => {
    expect(splitNote(rules(), ["Dining", "Travel"])).toBeNull();
    expect(splitNote(rules(), [])).toBeNull();
    expect(splitNote([], ["Groceries"])).toBeNull();
  });
  it("joins several rules, and carries no dollar sign", () => {
    const two = [...rules(), { vendor: "Other Mart", purchases: 1, legs: [{ category: "Groceries", subcategory: null, share: 1 }] }];
    const note = splitNote(two, ["Groceries"])!;
    expect(note).toBe("Groceries includes 70% of each Synth Mart order; Groceries includes 100% of each Other Mart order");
    expect(note.includes("$")).toBe(false);
  });
});

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { BalancePoint } from "../data/types";
import { txFactory } from "./__fixtures__";
import { runningBalance, runningBalanceEligible } from "./runningBalance";

const tx = txFactory({ account: "chk" });
const pt = (date: string, balance: number, source = "simplefin"): BalancePoint => ({ date, balance, available: null, limit: null, utilization: null, source });
const CHK = { kind: "checking", asset_class: "Cash" };

describe("runningBalance (end of day, keyed by date)", () => {
  it("walks back from the newest observation: before = after − the day's net, ending at the older observation", () => {
    const rows = [tx({ date: "2026-01-01", amount: -100 }), tx({ date: "2026-01-02", amount: 1000 }), tx({ date: "2026-01-03", amount: -50 })];
    const rb = runningBalance(rows, [pt("2025-12-31", 400), pt("2026-01-03", 1250)], CHK)!;
    expect(rb.get("2026-01-03")).toBe(1250);
    expect(rb.get("2026-01-02")).toBe(1300);
    expect(rb.get("2026-01-01")).toBe(300);
  });
  it("the newest day carries the latest balance exactly when nothing is dated after the anchor", () => {
    const rows = [tx({ date: "2026-01-01", amount: -100 }), tx({ date: "2026-01-02", amount: -7 })];
    const rb = runningBalance(rows, [pt("2026-01-09", 777)], CHK)!;      // a feed balance newer than every row
    expect(rb.get("2026-01-02")).toBe(777);
    expect(rb.get("2026-01-01")).toBe(784);
  });
  it("dates after a stale anchor accumulate forward from it", () => {
    const rows = [tx({ date: "2026-01-05", amount: -20 }), tx({ date: "2026-01-06", amount: -30 })];
    const rb = runningBalance(rows, [pt("2026-01-03", 1000)], CHK)!;
    expect(rb.get("2026-01-05")).toBe(980);
    expect(rb.get("2026-01-06")).toBe(950);
  });
  it("a card's negative balances work and cents are summed exactly", () => {
    const rows = [tx({ date: "2026-01-01", amount: -10.1 }), tx({ date: "2026-01-02", amount: -20.2 })];
    const rb = runningBalance(rows, [pt("2026-01-02", -530.3)], { kind: "credit-card", asset_class: "Other Debt" })!;
    expect(rb.get("2026-01-02")).toBe(-530.3);
    expect(rb.get("2026-01-01")).toBe(-510.1);
  });

  // The export's order within one date is arbitrary, so the number belongs to the DATE: one entry per day,
  // identical whatever order the day's rows came in (TxnTable places it on a displayed row).
  it("one entry per date that has a row, the end-of-day number, whatever order a day's rows came in", () => {
    const a = tx({ date: "2026-01-02", amount: -10 }), b = tx({ date: "2026-01-02", amount: -20 }), c = tx({ date: "2026-01-02", amount: 5 });
    const d = tx({ date: "2026-01-01", amount: -100 });
    const rb = runningBalance([d, a, b, c], [pt("2026-01-02", 500)], CHK)!;
    expect([...rb.keys()].sort()).toEqual(["2026-01-01", "2026-01-02"]);
    expect(rb.get("2026-01-02")).toBe(500);                       // end of 01-02 = the anchor
    expect(rb.get("2026-01-01")).toBe(525);                       // the day nets −25, so 01-01 ended 25 higher
    const flipped = runningBalance([c, b, d, a], [pt("2026-01-02", 500)], CHK)!;
    expect([...flipped.entries()].sort()).toEqual([...rb.entries()].sort());
  });
  it("no rows is an empty map, not null: the account is eligible and anchored, it just has nothing to show", () => {
    expect(runningBalance([], [pt("2026-01-03", 1000)], CHK)?.size).toBe(0);
  });

  it("anchors on the newest observation; on a shared date the FEED's wins over a CSV anchor, either order", () => {
    const rows = [tx({ date: "2026-01-02", amount: -10 })];
    for (const points of [[pt("2026-01-02", 111, "csv"), pt("2026-01-02", 222)], [pt("2026-01-02", 222), pt("2026-01-02", 111, "csv")]]) {
      expect(runningBalance(rows, points, CHK)!.get("2026-01-02")).toBe(222);
    }
    // a newer CSV point still beats an older feed one: newest date first
    expect(runningBalance(rows, [pt("2026-01-01", 5), pt("2026-01-02", 111, "csv")], CHK)!.get("2026-01-02")).toBe(111);
    // any source other than a CSV anchor is a feed too
    expect(runningBalance(rows, [pt("2026-01-02", 111, "csv"), pt("2026-01-02", 333, "sample_feed")], CHK)!.get("2026-01-02")).toBe(333);
  });

  it("never for a brokerage (its balance moves with the market), never without an observation", () => {
    expect(runningBalance([tx({})], [pt("2026-01-01", 1)], { kind: "brokerage", asset_class: "Stocks/ETF" })).toBeNull();
    expect(runningBalance([tx({})], [], CHK)).toBeNull();
    expect(runningBalanceEligible({ kind: "brokerage", asset_class: "Cash" })).toBe(true);     // a brokerage's cash account is cash
    expect(runningBalanceEligible({ kind: "loan", asset_class: "Mortgage" })).toBe(true);
    expect(runningBalanceEligible({ kind: "wallet", asset_class: "Crypto" })).toBe(false);
  });

  it("a checking or savings account moves with its posted rows whatever class it states", () => {
    expect(runningBalanceEligible({ kind: "savings", asset_class: "529" })).toBe(true);
    expect(runningBalanceEligible({ kind: "checking", asset_class: "Retirement" })).toBe(true);
    expect(runningBalanceEligible({ kind: "brokerage", asset_class: "Retirement" })).toBe(false);
  });

  it("the parts of a split purchase net into their day like the purchase would, never counted twice", () => {
    const rows = [tx({ date: "2026-07-18", amount: -210, parent_id: "p" }), tx({ date: "2026-07-18", amount: -90, parent_id: "p" }),
      tx({ date: "2026-07-17", amount: -40 })];
    const out = runningBalance(rows, [pt("2026-07-18", 1000)], CHK)!;
    expect(out.get("2026-07-18")).toBe(1000);
    expect(out.get("2026-07-17")).toBe(1300);                 // before the 18th's -300 net: the two parts, once
  });
});

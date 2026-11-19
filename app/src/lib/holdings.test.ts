// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { AssetRow, Holding } from "../data/types";
import { snapFactory } from "./__fixtures__";
import { balanceOnlyHoldings, LIVE_BALANCE_SOURCES } from "./holdings";

const row = (over: Partial<AssetRow>): AssetRow => ({
  account: "Acct", account_id: "acct", institution: "I", owner: "Alex", asset_class: "Retirement",
  value: 1000, liquid: false, as_of: "2026-06-01", as_of_assumed: false, needs_review: false,
  source: "simplefin_balance", manual_id: null, carried_back: false, ...over,
});
const holding = (account: string): Holding => ({
  account, account_id: null, institution: "I", owner: "Alex", symbol: "VTI", name: "VTI",
  asset_class: "Retirement", quantity: 1, value: 500, cost_basis: 400, as_of: "2026-06-01", liquid: false, notes: null,
});
const snap = snapFactory();

describe("balanceOnlyHoldings", () => {
  it("turns a live-balance row with no positions into one balance-only holding line", () => {
    const out = balanceOnlyHoldings(snap({ rows: [row({ account: "401k B" })] }), []);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ account: "401k B", symbol: null, value: 1000, notes: "balance only" });
  });
  it("never doubles an account that already reports positions", () => {
    expect(balanceOnlyHoldings(snap({ rows: [row({ account: "Brokerage A" })] }), [holding("Brokerage A")])).toEqual([]);
  });
  // each non-investment class is pinned on its own, at a positive value, so dropping one from the
  // exclusion list fails here instead of hiding behind the value > 0 filter
  it.each(["Cash", "Mortgage", "House", "Car", "Other Debt", "Margin"])("never counts a positive %s row as an invested balance", (asset_class) => {
    expect(balanceOnlyHoldings(snap({ rows: [row({ asset_class })] }), [])).toEqual([]);
  });
  it("never lists a credit-card row with a credit balance, or a loan row, as a balance-only holding", () => {
    const rows = [row({ account_id: "demo_card_0000", asset_class: "Other Debt", value: 120 }),
      row({ account_id: "demo_loan_0000", asset_class: "Other Debt", value: -4000 })];
    expect(balanceOnlyHoldings(snap({ rows }), [])).toEqual([]);
  });
  it("skips non-live sources and non-positive values", () => {
    const rows = [row({ asset_class: "Margin", value: -5 }), row({ source: "stated" }), row({ value: 0 })];
    expect(balanceOnlyHoldings(snap({ rows }), [])).toEqual([]);
  });
  it("with no snapshot there is no balance-only line", () => {
    expect(balanceOnlyHoldings(null, [holding("Brokerage A")])).toEqual([]);
  });
  it("counts exactly the bank feed's live-balance source", () => {
    expect([...LIVE_BALANCE_SOURCES]).toEqual(["simplefin_balance"]);
  });
});

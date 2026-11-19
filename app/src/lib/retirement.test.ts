// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { AssetRow, Holding } from "../data/types";
import { snapFactory } from "./__fixtures__";
import { LIVE_BALANCE_SOURCES } from "./holdings";
import { retirementBalances } from "./retirement";

const holding = (account: string, value: number, as_of = "2026-06-01"): Holding => ({
  account, account_id: null, institution: "I", owner: "Alex", symbol: "VTI", name: "VTI",
  asset_class: "Retirement", quantity: 1, value, cost_basis: null, as_of, liquid: false, notes: null,
});
const row = (account: string, value: number): AssetRow => ({
  account, account_id: null, institution: "I", owner: "Sam", asset_class: "Retirement", value,
  liquid: false, as_of: "2026-06-02", as_of_assumed: false, needs_review: false, source: "simplefin_balance",
  manual_id: null, carried_back: false,
});
const snap = snapFactory();
const MAP = { "401k A": "Pat", "401k B": "Sam", "Roth A": "Pat" };
const OWNERS = ["Pat", "Sam"];

describe("retirementBalances", () => {
  it("sums holdings per person and counts a balance-only retirement account once", () => {
    const r = retirementBalances([holding("401k A", 100), holding("Roth A", 50)], snap({ rows: [row("401k B", 700)] }), MAP, OWNERS);
    expect(r.byPerson).toEqual({ Pat: 150, Sam: 700 });
    expect(r.balanceOnly).toEqual(["401k B"]);
    expect(r.asOf).toEqual({ Pat: "2026-06-01", Sam: "2026-06-02" });
  });
  it("never doubles an account that has holdings AND a balance row", () => {
    const r = retirementBalances([holding("401k A", 100)], snap({ rows: [row("401k A", 999)] }), MAP, OWNERS);
    expect(r.byPerson.Pat).toBe(100);
    expect(r.balanceOnly).toEqual([]);
  });
  it("ignores unmapped accounts, zero-fills owners, skips undated as_of", () => {
    const r = retirementBalances([holding("Brokerage", 5), holding("401k A", 1, "unknown")], snap({ rows: [] }), MAP, OWNERS);
    expect(r.byPerson).toEqual({ Pat: 1, Sam: 0 });
    expect(r.asOf).toEqual({});
  });
  it("names who reported anything: a zero-filled owner is a figure nobody reported, not a balance of 0", () => {
    expect(retirementBalances([holding("401k A", 1)], snap({ rows: [] }), MAP, OWNERS).reported).toEqual(["Pat"]);
    expect(retirementBalances([], snap({ rows: [row("401k B", 700)] }), MAP, OWNERS).reported).toEqual(["Sam"]);
    expect(retirementBalances([holding("Brokerage", 5)], snap({ rows: [] }), MAP, OWNERS).reported).toEqual([]);
  });
  it("an account the feed reports at $0 (a new 401(k)) has reported: a true $0, counted and named", () => {
    const r = retirementBalances([], snap({ rows: [row("401k B", 0)] }), MAP, OWNERS);
    expect(r.reported).toEqual(["Sam"]);
    expect(r.reportedAccounts).toEqual(["401k B"]);
    expect(r.byPerson.Sam).toBe(0);
    expect(r.balanceOnly).toEqual(["401k B"]);
  });
  it("names which mapped accounts reported: one of a person's two accounts reporting leaves the other out", () => {
    const r = retirementBalances([holding("401k A", 100)], snap({ rows: [] }), MAP, OWNERS);
    expect(r.reportedAccounts).toEqual(["401k A"]);
  });

  // a regression pin: the old Tools code read holdings only, so a person whose sole
  // retirement account is balance-only projected from $0. This is the case that failed before.
  it("a person whose ONLY retirement account is balance-only is no longer $0", () => {
    const r = retirementBalances([holding("401k A", 100)], snap({ rows: [row("401k B", 700)] }), MAP, OWNERS);
    expect(r.byPerson.Sam).toBe(700);
    expect(r.byPerson.Sam).not.toBe(0);
  });
  it("counts a balance-only account from every live-balance source (the one set lib/holdings defines)", () => {
    expect(LIVE_BALANCE_SOURCES.size > 0, "at least one live-balance source").toBe(true);
    for (const source of LIVE_BALANCE_SOURCES) {
      const fed = { ...row("401k B", 40), source };
      expect(retirementBalances([], snap({ rows: [fed] }), MAP, OWNERS).byPerson.Sam, source).toBe(40);
    }
  });
  it("leaves a balance-only account that is not a retirement account out", () => {
    const brokerage = { ...row("Brokerage", 900), asset_class: "Stocks/ETF" };
    const r = retirementBalances([], snap({ rows: [brokerage] }), MAP, OWNERS);
    expect(r.byPerson).toEqual({ Pat: 0, Sam: 0 });
    expect(r.balanceOnly).toEqual([]);
  });
  it("does not count a non-feed (stated by hand) row: the same live-feed definition Accounts → Investments uses", () => {
    const manual = { ...row("401k B", 700), source: "stated" };
    expect(retirementBalances([], snap({ rows: [manual] }), MAP, OWNERS).byPerson.Sam).toBe(0);
  });
  it("takes the newest dated as_of across holdings and balance-only rows", () => {
    const r = retirementBalances([holding("401k A", 1, "2026-05-01")], snap({ rows: [{ ...row("Roth A", 5), as_of: "2026-06-09" }] }), MAP, OWNERS);
    expect(r.asOf.Pat).toBe("2026-06-09");
  });
  it("with no snapshot the balances are unknown: null, never every owner at 0", () => {
    expect(retirementBalances([holding("401k A", 100)], null, MAP, OWNERS)).toBeNull();
  });
  it("a balance-only row with no date adds its value but not an as_of", () => {
    const undated = { ...row("401k B", 700), as_of: null };
    const r = retirementBalances([], snap({ rows: [undated] }), MAP, OWNERS);
    expect(r.byPerson.Sam).toBe(700);
    expect(r.asOf).toEqual({});
  });
});

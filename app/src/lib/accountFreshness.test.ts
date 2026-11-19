// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { acctFactory } from "./__fixtures__";
import { freshnessEnd, freshnessRows } from "./accountFreshness";

const acct = acctFactory();
// stale = transactions stop before `behind` (the first day of the last full month): the amber rows

describe("freshnessRows", () => {
  it("lists the stale (amber) accounts first, oldest first, then the rest in the export's order", () => {
    const rows = freshnessRows([
      acct({ id: "a", txn_data_end: "2026-09-30" }),
      acct({ id: "b", txn_data_end: "2026-07-15" }),
      acct({ id: "c", txn_data_end: null }),
      acct({ id: "d", txn_data_end: "2026-08-02" }),
      acct({ id: "e", txn_data_end: "2026-09-01" }),
    ], "2026-09-01");
    expect(rows.map((r) => [r.account.id, r.stale])).toEqual([
      ["b", true], ["d", true], ["a", false], ["c", false], ["e", false]]);
  });
  it("keeps the export's order when nothing is stale, and an empty list is empty", () => {
    expect(freshnessRows([acct({ id: "x", txn_data_end: "2026-09-30" }), acct({ id: "y", txn_data_end: "2026-09-29" })], "2026-09-01")
      .map((r) => r.account.id)).toEqual(["x", "y"]);
    expect(freshnessRows([], "2026-09-01")).toEqual([]);
  });
  it("with no last full month to lag behind (no transaction yet) no account is stale, in the export's order", () => {
    expect(freshnessRows([acct({ id: "x", txn_data_end: "2026-07-15" }), acct({ id: "y", txn_data_end: null })], null)
      .map((r) => [r.account.id, r.stale])).toEqual([["x", false], ["y", false]]);
  });
});

// the cell used to print "→ ?" for every account with no transactions: 14 of 25 on the real data, all of them balance-only
describe("freshnessEnd", () => {
  it("names the date where the transactions end", () => {
    expect(freshnessEnd(acct({ txn_data_end: "2026-09-03" }))).toBe("→ Sep 3, 2026");
  });
  it("says what an account with no transactions does have, never a question mark", () => {
    expect(freshnessEnd(acct({ txn_data_end: null, latest_balance: 1234 }))).toBe("balance only");
    expect(freshnessEnd(acct({ txn_data_end: null, latest_balance: 0 })), "a zero balance is still a balance").toBe("balance only");
    expect(freshnessEnd(acct({ txn_data_end: null, latest_balance: null }))).toBe("no transactions");
    for (const a of [acct({ txn_data_end: null }), acct({ txn_data_end: null, latest_balance: null }), acct({})]) {
      expect(freshnessEnd(a)).not.toContain("?");
    }
  });
});

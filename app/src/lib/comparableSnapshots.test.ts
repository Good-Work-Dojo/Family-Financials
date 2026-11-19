// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { AssetRow, Snapshot } from "../data/types";
import {
  comparableSnapshots, foldedDebt, leftOutMonthEnds, missingAssetAccounts, notYetReported, onMonthEndFooting, onOneFooting,
} from "./comparableSnapshots";
import { snapFactory } from "./__fixtures__";

const snap = snapFactory({});
const dates = (snaps: Snapshot[]) => snaps.map((s) => s.snapshot_date);

describe("comparableSnapshots", () => {
  it("every snapshot missing nothing: all comparable, in order", () => {
    const snaps = [snap({ snapshot_date: "2030-01-31" }), snap({ snapshot_date: "2030-02-28" }),
      snap({ snapshot_date: "2030-03-12" })];
    const { comparable, partial } = comparableSnapshots(snaps);
    expect(comparable).toEqual(snaps);
    expect(partial).toEqual([]);
  });

  it("an early month missing one account is partial", () => {
    const snaps = [snap({ snapshot_date: "2030-01-31", missing: ["Sample Brokerage"] }),
      snap({ snapshot_date: "2030-02-28" }), snap({ snapshot_date: "2030-03-12" })];
    const { comparable, partial } = comparableSnapshots(snaps);
    expect(dates(comparable)).toEqual(["2030-02-28", "2030-03-12"]);
    expect(dates(partial)).toEqual(["2030-01-31"]);
  });

  it("a month before the mortgage's first point that misses nothing else stays comparable (the † notes cover it)", () => {
    const snaps = [snap({ snapshot_date: "2030-01-31", mortgage_included: false, missing: ["Sample Mortgage"] }),
      snap({ snapshot_date: "2030-02-28" })];
    const { comparable, partial } = comparableSnapshots(snaps);
    expect(dates(comparable)).toEqual(["2030-01-31", "2030-02-28"]);
    expect(partial).toEqual([]);
  });

  it("a month missing the mortgage and one more account is partial", () => {
    const snaps = [
      snap({ snapshot_date: "2030-01-31", mortgage_included: false, missing: ["Sample Checking", "Sample Mortgage"] }),
      snap({ snapshot_date: "2030-02-28", mortgage_included: false, missing: ["Sample Mortgage"] }),
      snap({ snapshot_date: "2030-03-12" }),
    ];
    const { comparable, partial } = comparableSnapshots(snaps);
    expect(dates(comparable)).toEqual(["2030-02-28", "2030-03-12"]);
    expect(dates(partial)).toEqual(["2030-01-31"]);
  });

  it("the newest itself missing a never-observed account: earlier months missing only that account are comparable", () => {
    const never = "Sample Savings";
    const snaps = [snap({ snapshot_date: "2030-01-31", missing: [never, "Sample Brokerage"] }),
      snap({ snapshot_date: "2030-02-28", missing: [never] }), snap({ snapshot_date: "2030-03-12", missing: [never] })];
    const { comparable, partial } = comparableSnapshots(snaps);
    expect(dates(comparable)).toEqual(["2030-02-28", "2030-03-12"]);
    expect(dates(partial)).toEqual(["2030-01-31"]);
  });

  it("an older export (no `missing`, defaulted to [] by the loader) is all comparable, a pre-mortgage snapshot included", () => {
    const snaps = [snap({ snapshot_date: "2030-01-31", mortgage_included: false }),
      snap({ snapshot_date: "2030-02-28" }), snap({ snapshot_date: "2030-03-12" })];
    const { comparable, partial } = comparableSnapshots(snaps);
    expect(comparable).toEqual(snaps);
    expect(partial).toEqual([]);
  });

  it("the newest is always kept, and no snapshot is no split", () => {
    const only = snap({ snapshot_date: "2030-03-12", missing: ["Sample Brokerage"], coverage: 0.5 });
    expect(comparableSnapshots([only])).toEqual({ comparable: [only], partial: [] });
    expect(comparableSnapshots([])).toEqual({ comparable: [], partial: [] });
  });
});

describe("leftOutMonthEnds: what each month end the history leaves out misses", () => {
  it("newest first, each with the accounts it has no balance for that the newest snapshot has", () => {
    const snaps = [
      snap({ snapshot_date: "2030-01-31", mortgage_included: false, missing: ["Sample Brokerage", "Sample Checking", "Sample Mortgage"] }),
      snap({ snapshot_date: "2030-02-28", missing: ["Sample Brokerage"] }),
      snap({ snapshot_date: "2030-03-31" }),
    ];
    const { partial } = comparableSnapshots(snaps);
    expect(leftOutMonthEnds(partial, snaps[2])).toEqual([
      { date: "2030-02-28", misses: ["Sample Brokerage"] },
      { date: "2030-01-31", misses: ["Sample Brokerage", "Sample Checking", "Sample Mortgage"] },
    ]);
  });
  it("an account the newest misses too (configured, never reported) is no reason a month end is left out: not listed", () => {
    const never = "Sample Savings";
    const snaps = [snap({ snapshot_date: "2030-01-31", missing: ["Sample Brokerage", never] }),
      snap({ snapshot_date: "2030-02-28", missing: [never] })];
    const { partial } = comparableSnapshots(snaps);
    expect(leftOutMonthEnds(partial, snaps[1])).toEqual([{ date: "2030-01-31", misses: ["Sample Brokerage"] }]);
  });
  it("none left out, or no snapshot: an empty list", () => {
    expect(leftOutMonthEnds([], snap({}))).toEqual([]);
    expect(leftOutMonthEnds([], null)).toEqual([]);
  });
});

describe("missingAssetAccounts: the accounts a snapshot misses that would hold assets", () => {
  const accounts = [{ name: "Sample Mortgage", kind: "loan" as const }, { name: "Sample Card", kind: "credit-card" as const },
    { name: "Sample Brokerage", kind: "brokerage" as const }, { name: "Sample Savings", kind: "savings" as const }];
  it("leaves out an account of a debt kind (a loan, a card): a debt is in no share of assets", () => {
    expect(missingAssetAccounts(snap({ mortgage_included: false, missing: ["Sample Mortgage"] }), accounts)).toEqual([]);
    expect(missingAssetAccounts(snap({ missing: ["Sample Card", "Sample Mortgage"] }), accounts)).toEqual([]);
    expect(missingAssetAccounts(snap({ missing: ["Sample Brokerage", "Sample Mortgage", "Sample Savings"] }), accounts))
      .toEqual(["Sample Brokerage", "Sample Savings"]);
  });
  it("a name the registry does not know is counted; no snapshot misses nothing", () => {
    expect(missingAssetAccounts(snap({ missing: ["Gone Account"] }), accounts)).toEqual(["Gone Account"]);
    expect(missingAssetAccounts(null, accounts)).toEqual([]);
  });
});

describe("notYetReported: the mark on a figure of an incomplete total", () => {
  it("says what the figure is of and how many accounts are not yet reported; null with none", () => {
    expect(notYetReported(1, "assets")).toBe("of the assets reported so far — 1 account not yet reported");
    expect(notYetReported(2, "balances")).toBe("of the balances reported so far — 2 accounts not yet reported");
    expect(notYetReported(0, "assets")).toBeNull();
  });
});

describe("the newest snapshot on the month ends' footing", () => {
  const row = (over: Partial<AssetRow>): AssetRow => ({ account: "Broker", account_id: "brk", institution: "Example Brokerage",
    owner: "Joint", asset_class: "Stocks/ETF", value: 320_000, liquid: true, as_of: "2030-06-15", as_of_assumed: false,
    needs_review: false, source: "positions", manual_id: null, carried_back: false, ...over });
  // positions 320,000 beside a 12,500 loan, cash 100,000 and a card owing 900
  const live = snap({ snapshot_date: "2030-06-15", total: 406_600, assets_total: 420_000, liabilities_total: -13_400,
    liquid_total: 420_000, by_class: { "Stocks/ETF": 320_000, Margin: -12_500, Cash: 100_000, "Other Debt": -900 },
    by_owner: { Joint: 406_600 },
    rows: [row({}), row({ account: "Broker Margin", asset_class: "Margin", value: -12_500, liquid: false, source: "simplefin_balance" }),
      row({ account: "Checking", account_id: "chk", asset_class: "Cash", value: 100_000 }),
      row({ account: "Card", account_id: "card", asset_class: "Other Debt", value: -900, liquid: false })] });
  it("a live margin account becomes the one row a month end holds, net of its loan; the totals follow, the total does not", () => {
    const f = onMonthEndFooting(live, (id) => (id === "brk" ? "Stocks/ETF" : undefined));
    expect(foldedDebt(live).map((r) => r.account)).toEqual(["Broker Margin"]);
    expect(f.rows.filter((r) => r.account_id === "brk")).toEqual([row({ value: 307_500, source: "simplefin_balance" })]);
    expect(f.by_class).toEqual({ "Stocks/ETF": 307_500, Cash: 100_000, "Other Debt": -900 });
    expect([f.total, f.assets_total, f.liabilities_total, f.liquid_total]).toEqual([406_600, 407_500, -900, 407_500]);
    expect(f.by_owner).toEqual({ Joint: 406_600 });
    expect(live.rows).toHaveLength(4);                                    // the snapshot itself is left as it is
  });
  it("nothing to fold: the snapshot itself; a history keeps its month ends and folds only its newest", () => {
    const plain = { ...live, rows: live.rows.filter((r) => r.asset_class !== "Margin") };
    expect(onMonthEndFooting(plain)).toBe(plain);
    const monthEnd = snap({ snapshot_date: "2030-05-31", rows: [row({ value: 300_000, source: "simplefin_balance" })] });
    const h = onOneFooting([monthEnd, live]);
    expect(h.folded).toBe(true);
    expect(h.snaps[0]).toBe(monthEnd);
    expect(h.snaps[1].assets_total).toBe(407_500);
    expect(onOneFooting([live]), "one snapshot has no month end to set it beside").toEqual({ snaps: [live], folded: false });
    expect(onOneFooting([monthEnd, plain]).folded).toBe(false);
  });
});

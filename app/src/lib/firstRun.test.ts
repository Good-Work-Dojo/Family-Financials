// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it, vi } from "vitest";
import type { AppData, Setup } from "../data/types";
import { appDataFactory, balancesOnlyData, snapFactory, transactionsOnlyData, txFactory } from "./__fixtures__";
import { FIRST_RUN_KEEPS, FIRST_RUN_TITLE, firstRunState, replacedInFirstRun } from "./firstRun";

const base = appDataFactory(snapFactory())({});
/** a household's exports with `setup` as the manifest states it and no transaction and no snapshot unless `over` adds
 *  them */
const household = (setup: Setup, over: Partial<AppData> = {}): AppData => ({
  ...base, manifest: { ...base.manifest, transaction_count: 0, current_snapshot_date: null, setup },
  transactions: [], purchases: [], assets: { snapshots: [] }, cashflow: [], ...over,
});
const state = (d: AppData) => firstRunState(d.manifest, d);
const NOTHING: Setup = { accounts: 0, feed: false, import_files: 0, manual_assets: 0 };

describe("firstRunState", () => {
  it("nothing configured and nothing arrived: set up", () => {
    expect(state(household(NOTHING))).toBe("setup");
  });

  it("accounts configured and no data yet: waiting, whether a feed or statement files are set up or neither", () => {
    expect(state(household({ ...NOTHING, accounts: 2 }))).toBe("waiting");
    expect(state(household({ ...NOTHING, accounts: 2, feed: true }))).toBe("waiting");
    expect(state(household({ ...NOTHING, accounts: 1, import_files: 3 }))).toBe("waiting");
  });

  it("a feed that has reported one snapshot (a balance, no transaction): the app", () => {
    const fed = balancesOnlyData();
    expect(fed.transactions.length === 0 && fed.assets.snapshots.length === 1, "one snapshot and no transaction").toBe(true);
    expect(state({ ...fed, manifest: { ...fed.manifest, setup: { ...NOTHING, accounts: 1, feed: true } } })).toBeNull();
  });

  it("statement files with rows and no balance (transactions, no snapshot): the app, whose pages say what they lack", () => {
    const files = transactionsOnlyData();
    expect(files.transactions.length > 0 && files.assets.snapshots.length === 0, "transactions and no snapshot").toBe(true);
    expect(state(files)).toBeNull();
  });

  it("only a hand-stated asset: the app once the ingest made its snapshot; waiting while it is counted with none yet", () => {
    const stated = balancesOnlyData().assets.snapshots[0];
    const onlyAsset: Setup = { ...NOTHING, manual_assets: 1 };
    expect(state(household(onlyAsset, { assets: { snapshots: [stated] } }))).toBeNull();
    expect(state(household(onlyAsset))).toBe("waiting");
  });

  it("a feed that reports only credit cards, left out of net worth, with no transaction in its window: balances are data", () => {
    const cardsOnly = household({ ...NOTHING, accounts: 1, feed: true }, {
      balances: { series: { demo_card_0000: [{ date: "2026-06-01", balance: -420, available: 580, limit: 1000,
        utilization: 0.42, source: "simplefin" }] },
        accounts: { demo_card_0000: { name: "Sample Card", institution: "Sample Bank", owner: "Alex", kind: "credit-card",
          asset_class: "Other Debt" } } },
    });
    expect(cardsOnly.transactions.length === 0 && cardsOnly.assets.snapshots.length === 0, "no transaction, no snapshot").toBe(true);
    expect(state(cardsOnly)).toBeNull();
    // a series with no point is no data
    expect(state(household({ ...NOTHING, accounts: 1, feed: true }, { balances: { series: { demo_card_0000: [] }, accounts: {} } })))
      .toBe("waiting");
  });

  it("the data decides before the counts: an export holding rows is never sent to set up", () => {
    const tx = txFactory();
    expect(state(household(NOTHING, { transactions: [tx()], purchases: [] }))).toBeNull();
    expect(state(household(NOTHING, { assets: { snapshots: [snapFactory()({})] } }))).toBeNull();
  });

  it("reads the exports, never the clock", () => {
    vi.useFakeTimers();
    try {
      for (const now of ["2020-01-01T00:00:00Z", "2099-12-31T23:59:59Z"]) {
        vi.setSystemTime(new Date(now));
        expect(state(household(NOTHING))).toBe("setup");
        expect(state(household({ ...NOTHING, accounts: 2 }))).toBe("waiting");
        expect(state(transactionsOnlyData())).toBeNull();
      }
    } finally { vi.useRealTimers(); }
  });
});

describe("what the first-run page stands in for", () => {
  it("every route but Data and Settings", () => {
    expect([...FIRST_RUN_KEEPS]).toEqual(["/data", "/settings"]);
    for (const route of ["/", "/cash-flow", "/cash-flow/budget", "/accounts/:id", "/planning/second-house", "/review"]) {
      expect(replacedInFirstRun(route), route).toBe(true);
    }
    expect(replacedInFirstRun("/data")).toBe(false);
    expect(replacedInFirstRun("/settings")).toBe(false);
  });
  it("names each page in a few words: with nothing built yet, it is set up too", () => {
    expect(FIRST_RUN_TITLE).toEqual({ setup: "Set up", waiting: "Waiting for data", "nothing-built": "Set up" });
  });
});

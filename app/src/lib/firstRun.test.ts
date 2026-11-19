// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { afterEach, describe, expect, it, vi } from "vitest";
import { loadAppData } from "../data/DataContext";
import type { AppData, Setup } from "../data/types";
import { appDataFactory, balancesOnlyData, snapFactory, transactionsOnlyData, txFactory } from "./__fixtures__";
import { SHIPPED_RESERVED } from "./categories";
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

// An export older than the manifest's `setup` (schema 18) is read with the setup the loader derives: the accounts
// accounts.json lists, a feed once one was fetched, no statement file and the newest snapshot's hand-stated rows.
describe("firstRunState on an export older than the setup key", () => {
  afterEach(() => vi.unstubAllGlobals());
  const manifest18 = { schema_version: 18, generated_at: "2026-10-02T10:00:00-05:00", simplefin_fetched_at: null,
    transaction_count: 0, current_snapshot_date: null, files: [] };
  const assumptions18 = { projections: {}, category_renames: [], reserved: SHIPPED_RESERVED,
    net_worth: { include_credit_cards: true }, mortgage: null,
    household: { people: [{ name: "Alex", role: "owner" }], margin_apr: null, retirement_accounts: {}, margin_account: null } };
  const account18 = { id: "demo_checking_0000", name: "Sample Checking", institution: "Sample Bank", owner: "Alex",
    kind: "checking", asset_class: null, txn_data_start: null, txn_data_end: null, latest_balance: null,
    latest_balance_as_of: null, ledger_drift: null };
  /** loads a schema-18 export set; a file `bodies` does not name is not served */
  const load18 = (bodies: Record<string, unknown>) => {
    const served: Record<string, unknown> = { "manifest.json": manifest18, "assumptions.json": assumptions18,
      "transactions.json": { count: 0, transactions: [] }, "assets_timeseries.json": { snapshots: [] }, ...bodies };
    vi.stubGlobal("fetch", async (url: string) => {
      const name = String(url).replace(/^\.\//, "");
      return name in served ? new Response(JSON.stringify(served[name]), { status: 200 }) : new Response("no", { status: 404 });
    });
    return loadAppData();
  };

  it("with data: the app", async () => {
    const row = txFactory({ account: account18.id })();
    const { data } = await load18({ "transactions.json": { count: 1, transactions: [row] },
      "accounts.json": { accounts: [account18] } });
    expect(data.manifest.setup.accounts, "the loader derived a setup").toBe(1);
    expect(firstRunState(data.manifest, data)).toBeNull();
  });

  it("with no data: waiting when accounts.json lists an account, set up when it lists none", async () => {
    const listed = (await load18({ "accounts.json": { accounts: [account18] } })).data;
    expect(listed.manifest.setup).toEqual({ accounts: 1, feed: false, import_files: 0, manual_assets: 0 });
    expect(firstRunState(listed.manifest, listed)).toBe("waiting");
    const none = (await load18({ "accounts.json": { accounts: [] } })).data;
    expect(none.manifest.setup).toEqual(NOTHING);
    expect(firstRunState(none.manifest, none)).toBe("setup");
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

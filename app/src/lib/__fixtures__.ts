// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Closure factories for lib tests. Each test file builds its fixtures in one
// visible line pinning ITS historical defaults, so the objects (ids included —
// every factory instance counts t0, t1, … independently) are exactly what the
// file constructed before this module existed. Pinned values are shared by
// reference across builds — pin scalars or treat fixtures as immutable.
import { AI_STATUS_NONE, EXPECTED_SCHEMA_VERSION } from "../data/types";
import type {
  AccountMeta, AppData, AssetRow, Assumptions, CashflowMonth, CategoryRename, MortgageBlock, RecurringItem, Snapshot,
  Transaction,
} from "../data/types";
import { monthsBetween, trailingWindow } from "./agg";
import { SHIPPED_RESERVED } from "./categories";
import { SHIPPED_ASSET_CLASSES } from "./tokens";

/** An assumptions.json `mortgage` block for the demo loan account: no balance, every loan term null and an empty
 *  schedule unless pinned. */
export function mortgageFactory(pin: Partial<MortgageBlock> = {}): MortgageBlock {
  return {
    account_id: "demo_loan_0000", balance: null, balance_as_of: null, rate_pct: null, monthly_payment: null,
    monthly_escrow: null, escrow_balance: null, origination: null, term_months: null, schedule: [], ...pin,
  };
}

/** What `categoryVocab` reads from the assumptions: the shipped reserved names and `renames` (none by default). */
export function vocabAssumptions(renames: CategoryRename[] = []): Pick<Assumptions, "category_renames" | "reserved"> {
  return { category_renames: renames, reserved: SHIPPED_RESERVED };
}

export function txFactory(pin: Partial<Transaction> = {}) {
  let seq = 0;
  return (over: Partial<Transaction> = {}): Transaction => ({
    id: `t${seq++}`, date: "2025-06-15", account: "a", account_name: "A",
    owner: "Joint", description: "d", vendor: "V", category: "Dining",
    subcategory: null, category_source: "keyword", amount: -10, type: "purchase",
    is_internal: false, transfer_group_id: null, tags: [], needs_review: false,
    earner: null, source_file: "s", parent_id: null, is_parent: false, ...pin, ...over,
  });
}

/** A cash-flow month, in coverage by default (`in_coverage: true`): a month before the coverage floor says so with
 *  `in_coverage: false`. */
export function cfmFactory(pin: Partial<CashflowMonth> = {}) {
  return (over: Partial<CashflowMonth> = {}): CashflowMonth => ({
    month: "2025-01", income: 20000, spend: 15000, invested: 0,
    savings_rate: 0.25, by_category: {}, income_by_subcategory: {},
    accounts_covered: [], full_coverage: true, in_coverage: true, ...pin, ...over,
  });
}

/** A snapshot with every series present (`coverage: 1`, `missing: []`). Unless pinned, `assets_total` and
 *  `liabilities_total` split the snapshot's `total` by its sign, so the two always add up to it. */
export function snapFactory(pin: Partial<Snapshot> = {}) {
  return (over: Partial<Snapshot> = {}): Snapshot => {
    const s = {
      snapshot_date: "2026-06-01", source: "x", total: 2_000_000,
      total_excl_house_car: 1_000_000, mortgage_included: true,
      liquid_total: 500_000, by_owner: {}, by_class: {}, coverage: 1, missing: [], rows: [], ...pin, ...over,
    };
    return { assets_total: Math.max(s.total, 0), liabilities_total: Math.min(s.total, 0), ...s };
  };
}

export function recFactory(pin: Partial<RecurringItem> = {}) {
  // an export's next_expected is its first expected date: a layer that states one of the two gets the other to match
  // (next_expected alone is the one expected date; expected_dates alone moves next_expected to its first)
  const layer = (r: RecurringItem, l: Partial<RecurringItem>): RecurringItem => ({
    ...r, ...l,
    ...(l.next_expected !== undefined && l.expected_dates === undefined ? { expected_dates: [l.next_expected] } : {}),
    ...(l.expected_dates?.length && l.next_expected === undefined ? { next_expected: l.expected_dates[0] } : {}),
  });
  return (over: Partial<RecurringItem> = {}): RecurringItem => layer(layer({
    vendor: "Netflix", account_id: "demo_checking_0101", category: "Subscriptions",
    cadence: "monthly", amount_mode: "fixed", occurrences: 12,
    first_seen: "2025-01-05", last_seen: "2026-06-05", next_expected: "2026-07-05",
    active: true, typical_amount: 20, last_amount: 20, annualized: 240,
    monthly_equivalent: 20, price_changes: [],
    schedule: true, expected_dates: ["2026-07-05", "2026-08-05", "2026-09-05"],
    grace_days: 6, confidence: 1, median_gap_days: 31,
  }, pin), over);
}

export function acctFactory(pin: Partial<AccountMeta> = {}) {
  return (over: Partial<AccountMeta> = {}): AccountMeta => ({
    id: "chk", name: "Checking", institution: "Bank", owner: "Joint", kind: "checking",
    asset_class: "Cash", txn_data_start: "2024-01-01", txn_data_end: "2026-07-20",
    latest_balance: 1000, latest_balance_as_of: "2026-07-20", ledger_drift: null, ...pin, ...over,
  });
}

/** A minimal, valid AppData (cashflow Apr-May 2026 from one cash-flow account, both months in coverage, one snapshot,
 *  everything else empty) for tests
 *  that render a page or read the whole AppData; pass the file's `snapFactory` so the snapshot is
 *  ITS default, and override only what the test consumes. */
export function appDataFactory(snapshot: (over?: Partial<Snapshot>) => Snapshot) {
  return (over: Partial<AppData> = {}): AppData => ({
    manifest: { schema_version: EXPECTED_SCHEMA_VERSION, generated_at: "2026-06-10",
      simplefin_fetched_at: null,
      transaction_count: 0, current_snapshot_date: "2026-06-01",
      setup: { accounts: 1, feed: false, import_files: 1, manual_assets: 0 }, files: [] },
    transactions: [], purchases: [], splitParents: new Map(), accounts: [], manualAssets: [], manualOverridden: [],
    assets: { snapshots: [snapshot({})] },
    partialSnapshots: [],
    holdings: [],
    cashflow: [{ month: "2026-04", income: 10000, spend: 8000, invested: 0,
      savings_rate: 0.2, by_category: {}, income_by_subcategory: {},
      accounts_covered: ["chk"], full_coverage: true, in_coverage: true },
    { month: "2026-05", income: 10000, spend: 8000, invested: 0,
      savings_rate: 0.2, by_category: {}, income_by_subcategory: {},
      accounts_covered: ["chk"], full_coverage: true, in_coverage: true }],
    recurring: [],
    quality: { files: [], checks: [], account_month_sums: {}, top_unmapped_vendors: [], cross_file_duplicate_uids: {} },
    questions: [],
    dataGaps: [],
    assumptions: {
      projections: { return_scenarios_pct: [5, 7, 9], inflation_pct: 2.75,
        withdrawal_rate_pct: 4, ltcg_pct: 15, state_income_tax_pct: 0 },
      // every key stated, as the demo household's configuration does: a test of a default leaves its key out
      projections_stated: ["inflation_pct", "ltcg_pct", "return_scenarios_pct", "state_income_tax_pct", "withdrawal_rate_pct"],
      thresholds: {}, taxonomy: {}, owners: ["Alex", "Sam", "Joint"], targets: {}, budgets: {},
      household: { name: "The Sample household", timezone: "America/New_York", people: [], margin_apr: 0.11,
        retirement_accounts: {}, margin_account: null },
      modules: {},
      category_renames: [], reserved: SHIPPED_RESERVED, net_worth: { include_credit_cards: false },
      income_phases: [], mortgage: null,
      account_windows: {},
      asset_classes: SHIPPED_ASSET_CLASSES,
    },
    balances: { series: {}, accounts: {} },
    networthDaily: { series: [], current_total: null, current_snapshot_date: null },
    vendors: [],
    aiStatus: AI_STATUS_NONE,
    ...over,
  });
}

/** A household with transactions and no net-worth snapshot (statement files that carry no balance): one checking
 *  account with no balance, spending and a salary from January to early June 2026, the base cash flow (April and May),
 *  no snapshot and no balance history. */
export function transactionsOnlyData(): AppData {
  const tx = txFactory({ account: "chk", account_name: "Checking" });
  const transactions = [
    ...["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"].flatMap((m) => [
      tx({ date: `${m}-03`, category: "Groceries", subcategory: "Groceries", vendor: "Market", amount: -400 }),
      tx({ date: `${m}-15`, category: "Utilities", subcategory: "Electric/Gas", vendor: "Power Co", amount: -120 }),
      tx({ date: `${m}-28`, category: "Income", subcategory: "Salary - Alex", vendor: "Employer", amount: 5000, type: "income" }),
    ]),
    tx({ date: "2026-06-04", vendor: "Cafe", amount: -35 }),
  ];
  const base = appDataFactory(snapFactory())({});
  return {
    ...base,
    manifest: { ...base.manifest, transaction_count: transactions.length, current_snapshot_date: null,
      setup: { accounts: 1, feed: false, import_files: 1, manual_assets: 0 } },
    transactions, purchases: transactions,
    accounts: [acctFactory()({ latest_balance: null, latest_balance_as_of: null, txn_data_start: "2026-01-03",
      txn_data_end: "2026-06-04" })],
    assets: { snapshots: [] },
  };
}

const thinRow = (over: Partial<AssetRow>): AssetRow => ({
  account: "Checking", account_id: "chk", institution: "Sample Bank", owner: "Joint", asset_class: "Cash", value: 12_000,
  liquid: true, as_of: "2026-06-10", as_of_assumed: false, needs_review: false, source: "simplefin_balance", manual_id: null,
  carried_back: false, ...over,
});
const thinPoint = (date: string, balance: number, source = "simplefin") =>
  ({ date, balance, available: null, limit: null, utilization: null, source });

/** A household in its first week, shaped like the demo's `new`: a checking account and a credit card on the bank feed,
 *  five days of rows (a salary and three purchases) inside June 2026, the month in progress, so no full month; one
 *  snapshot on the ingest day (2026-06-10) holding the checking balance and the card's debt; five days of balance
 *  history and of the daily net-worth line; nothing recurring, no holdings, no mortgage, no margin account. */
export function firstWeekData(): AppData {
  const tx = txFactory({ account: "chk", account_name: "Checking" });
  const transactions = [
    tx({ date: "2026-06-06", category: "Groceries", subcategory: "Groceries", vendor: "Market", amount: -50 }),
    tx({ date: "2026-06-08", category: "Income", subcategory: "Salary - Alex", vendor: "Employer", amount: 3_000, type: "income" }),
    tx({ date: "2026-06-09", category: "Dining", subcategory: "Coffee", vendor: "Cafe", amount: -20 }),
    tx({ date: "2026-06-09", account: "card", account_name: "Card", category: "Dining", subcategory: "Restaurants",
      vendor: "Bistro", amount: -45 }),
  ];
  const base = appDataFactory(snapFactory())({});
  const days = ["2026-06-06", "2026-06-07", "2026-06-08", "2026-06-09", "2026-06-10"];
  const snapshot = snapFactory()({
    snapshot_date: "2026-06-10", total: 10_500, total_excl_house_car: 10_500, liquid_total: 12_000,
    by_class: { Cash: 12_000, "Other Debt": -1_500 }, by_owner: { Joint: 10_500 },
    rows: [thinRow({}), thinRow({ account: "Card", account_id: "card", asset_class: "Other Debt", value: -1_500, liquid: false })],
  });
  return {
    ...base,
    manifest: { ...base.manifest, generated_at: "2026-06-10T18:00:00-04:00", transaction_count: transactions.length,
      current_snapshot_date: "2026-06-10", simplefin_fetched_at: "2026-06-10T22:00:00Z",
      setup: { accounts: 2, feed: true, import_files: 0, manual_assets: 0 } },
    transactions, purchases: transactions,
    accounts: [
      acctFactory()({ txn_data_start: "2026-06-06", txn_data_end: "2026-06-09", latest_balance: 12_000, latest_balance_as_of: "2026-06-10" }),
      acctFactory()({ id: "card", name: "Card", kind: "credit-card", asset_class: "Other Debt", txn_data_start: "2026-06-09",
        txn_data_end: "2026-06-09", latest_balance: -1_500, latest_balance_as_of: "2026-06-10" }),
    ],
    assets: { snapshots: [snapshot] },
    cashflow: [cfmFactory()({ month: "2026-06", income: 3_000, spend: 115, savings_rate: null, accounts_covered: ["chk", "card"] })],
    balances: {
      series: { chk: days.map((d) => thinPoint(d, 12_000)), card: days.map((d) => thinPoint(d, -1_500)) },
      accounts: { chk: { name: "Checking", institution: "Sample Bank", owner: "Joint", kind: "checking", asset_class: "Cash" },
        card: { name: "Card", institution: "Sample Bank", owner: "Joint", kind: "credit-card", asset_class: "Other Debt" } },
    },
    networthDaily: { series: days.map((date) => ({ date, networth: 10_500, coverage: 1, source: "simplefin" })),
      current_total: 10_500, current_snapshot_date: "2026-06-10" },
  };
}

/** A household with one full month, shaped like the demo's `tiny`: one checking account read from statement files, rows
 *  in May 2026 (a full month: two salaries and a few purchases) and in June 2026 (the month in progress, as of the
 *  ingest on 2026-06-10); one snapshot (2026-06-09) holding the checking balance, which the balance history holds as its
 *  one point; nothing recurring, no card, no brokerage, no mortgage. */
export function oneFullMonthData(): AppData {
  const tx = txFactory({ account: "chk", account_name: "Checking" });
  const transactions = [
    tx({ date: "2026-05-03", category: "Groceries", subcategory: "Groceries", vendor: "Market", amount: -400 }),
    tx({ date: "2026-05-15", category: "Income", subcategory: "Salary - Alex", vendor: "Employer", amount: 3_000, type: "income" }),
    tx({ date: "2026-05-20", category: "Utilities", subcategory: "Electric/Gas", vendor: "Power Co", amount: -120 }),
    tx({ date: "2026-05-29", category: "Income", subcategory: "Salary - Alex", vendor: "Employer", amount: 3_000, type: "income" }),
    tx({ date: "2026-06-02", category: "Dining", subcategory: "Coffee", vendor: "Cafe", amount: -15 }),
    tx({ date: "2026-06-05", category: "Groceries", subcategory: "Groceries", vendor: "Market", amount: -90 }),
  ];
  const base = appDataFactory(snapFactory())({});
  const snapshot = snapFactory()({
    snapshot_date: "2026-06-09", total: 8_000, total_excl_house_car: 8_000, liquid_total: 8_000,
    by_class: { Cash: 8_000 }, by_owner: { Joint: 8_000 },
    rows: [thinRow({ value: 8_000, as_of: "2026-06-09", source: "statement_balance" })],
  });
  const cfm = cfmFactory({ accounts_covered: ["chk"] });
  return {
    ...base,
    manifest: { ...base.manifest, generated_at: "2026-06-10T18:00:00-04:00", transaction_count: transactions.length,
      current_snapshot_date: "2026-06-09", setup: { accounts: 1, feed: false, import_files: 2, manual_assets: 0 } },
    transactions, purchases: transactions,
    accounts: [acctFactory()({ txn_data_start: "2026-05-03", txn_data_end: "2026-06-05", latest_balance: 8_000,
      latest_balance_as_of: "2026-06-09" })],
    assets: { snapshots: [snapshot] },
    cashflow: [cfm({ month: "2026-05", income: 6_000, spend: 520, savings_rate: 0.9133 }),
      cfm({ month: "2026-06", income: 0, spend: 105, savings_rate: null })],
    balances: {
      series: { chk: [thinPoint("2026-06-09", 8_000, "csv")] },
      accounts: { chk: { name: "Checking", institution: "Sample Bank", owner: "Joint", kind: "checking", asset_class: "Cash" } },
    },
    networthDaily: { series: [{ date: "2026-06-09", networth: 8_000, coverage: 1, source: "simplefin" }],
      current_total: 8_000, current_snapshot_date: "2026-06-09" },
  };
}

/** A household with `n` whole months (its data begins on the 1st of the first) ending in May 2026, each a salary of
 *  5,000 and spending of 600 (groceries 400, essential by default; dining 200, not), then June 2026 in progress as of the
 *  ingest on 2026-06-10; one checking account read from statement files, one snapshot (2026-06-09) holding its 12,000;
 *  nothing recurring, no holdings, no adult in the household config. */
export function fullMonthsData(n: number): AppData {
  const tx = txFactory({ account: "chk", account_name: "Checking" });
  const months = monthsBetween(trailingWindow("2026-05", n)[0].slice(0, 7), "2026-05");
  const transactions = [
    ...months.flatMap((m) => [
      tx({ date: `${m}-01`, category: "Groceries", subcategory: "Groceries", vendor: "Market", amount: -400 }),
      tx({ date: `${m}-12`, category: "Dining", subcategory: "Restaurants", vendor: "Bistro", amount: -200 }),
      tx({ date: `${m}-28`, category: "Income", subcategory: "Salary - Alex", vendor: "Employer", amount: 5_000, type: "income" }),
    ]),
    tx({ date: "2026-06-04", category: "Groceries", subcategory: "Groceries", vendor: "Market", amount: -50 }),
  ];
  const snapshot = snapFactory()({ snapshot_date: "2026-06-09", total: 12_000, total_excl_house_car: 12_000,
    liquid_total: 12_000, by_class: { Cash: 12_000 }, by_owner: { Joint: 12_000 },
    rows: [thinRow({ as_of: "2026-06-09", source: "statement_balance" })] });
  const cfm = cfmFactory({ accounts_covered: ["chk"] });
  const base = appDataFactory(snapFactory())({});
  return {
    ...base,
    manifest: { ...base.manifest, generated_at: "2026-06-10T18:00:00-04:00", transaction_count: transactions.length,
      current_snapshot_date: "2026-06-09", setup: { accounts: 1, feed: false, import_files: 1, manual_assets: 0 } },
    transactions, purchases: transactions,
    accounts: [acctFactory()({ latest_balance: 12_000, latest_balance_as_of: "2026-06-09" })],
    assets: { snapshots: [snapshot] },
    cashflow: [...months.map((month) => cfm({ month, income: 5_000, spend: 600, savings_rate: 0.88 })),
      cfm({ month: "2026-06", income: 0, spend: 50, savings_rate: null })],
  };
}

/** A snapshot with rows below zero where no debt class is: an overdrawn checking account (−300) beside a savings account
 *  (5,000) and a brokerage (10,000), a home stated by hand (400,000), a boat stated by hand BELOW zero in an asset class
 *  (−2,000) and the mortgage (−250,000). Its totals are the rows' by sign, as the ETL writes them: assets 415,000,
 *  liabilities −252,300, net worth 162,700, `liquid_total` 14,700 (every liquid row, the overdraft included). Every card
 *  that reads assets or debts must agree on it: assets 415,000 (liquid 15,000, illiquid 400,000), debts 252,300. */
export function belowZeroSnapshot(): Snapshot {
  const row = (over: Partial<AssetRow>): AssetRow => ({
    account: "Joint Checking", account_id: "chk", institution: "Sample Bank", owner: "Joint", asset_class: "Cash",
    value: -300, liquid: true, as_of: "2026-06-01", as_of_assumed: false, needs_review: false, source: "bank_balance",
    manual_id: null, carried_back: false, ...over,
  });
  return snapFactory()({
    snapshot_date: "2026-06-01", total: 162_700, assets_total: 415_000, liabilities_total: -252_300, liquid_total: 14_700,
    total_excl_house_car: 162_700 - 400_000 + 2_000,
    by_class: { Cash: 4_700, "Stocks/ETF": 10_000, House: 400_000, Car: -2_000, Mortgage: -250_000 },
    by_owner: { Joint: 162_700 },
    rows: [
      row({}),
      row({ account: "Joint Savings", account_id: "sav", value: 5_000 }),
      row({ account: "Joint Brokerage", account_id: "brk", asset_class: "Stocks/ETF", value: 10_000, source: "simplefin_balance" }),
      row({ account: "Home", account_id: null, institution: "", asset_class: "House", value: 400_000, liquid: false,
        source: "stated", manual_id: "home" }),
      row({ account: "Boat", account_id: null, institution: "", asset_class: "Car", value: -2_000, liquid: false,
        source: "stated", manual_id: "boat" }),
      row({ account: "Home Mortgage", account_id: "loan", institution: "Sample Lender", asset_class: "Mortgage",
        value: -250_000, liquid: false, source: "simplefin_balance" }),
    ],
  });
}

/** A household with a net-worth snapshot and no transaction (an account that reports a balance only, and a home
 *  stated by hand): one snapshot on 2026-06-01 holding a checking balance and the home, no transaction, no cash flow. */
export function balancesOnlyData(): AppData {
  const row = (over: Partial<AssetRow>): AssetRow => ({
    account: "Checking", account_id: "chk", institution: "Sample Bank", owner: "Joint", asset_class: "Cash", value: 12_000,
    liquid: true, as_of: "2026-06-01", as_of_assumed: false, needs_review: false, source: "bank_balance", manual_id: null,
    carried_back: false, ...over,
  });
  const snapshot = snapFactory()({
    snapshot_date: "2026-06-01", total: 412_000, total_excl_house_car: 12_000, liquid_total: 12_000,
    by_class: { Cash: 12_000, House: 400_000 }, by_owner: { Joint: 412_000 },
    rows: [row({}), row({ account: "Sample Home", account_id: null, institution: "Self", asset_class: "House", value: 400_000,
      liquid: false, as_of: "2026-01-31", source: "stated", manual_id: "sample-home" })],
  });
  const base = appDataFactory(snapFactory())({});
  return {
    ...base,
    manifest: { ...base.manifest, transaction_count: 0, setup: { accounts: 1, feed: false, import_files: 0, manual_assets: 1 } },
    accounts: [acctFactory()({ latest_balance: 12_000, latest_balance_as_of: "2026-06-01", txn_data_start: null,
      txn_data_end: null })],
    assets: { snapshots: [snapshot] },
    cashflow: [],
  };
}

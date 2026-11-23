// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// TypeScript mirror of the ETL's JSON export contract (data/exports/*.json).

export type TxType =
  | "purchase" | "income" | "transfer" | "cc-payment"
  | "investment" | "dividend" | "fee" | "interest";

/** Where a row's category came from (etl/models.py CATEGORY_SOURCES). "answer" is also
 *  stamped client-side by the overlay (lib/answers.ts) so badges stay truthful before
 *  the next ingest. "learned" is a rule learned from the family's own answers; "split" is a child of a
 *  rule-driven split; "ai" is a row the AI step's stored answer filed. */
export type CategorySource =
  | "internal" | "type_default" | "parser" | "vendor_rule" | "learned" | "keyword"
  | "bank_label" | "fallback" | "answer" | "override" | "split" | "ai";

export interface Transaction {
  id: string;
  date: string;                  // ISO
  account: string;               // registry id, e.g. demo_checking_0000
  account_name: string;
  owner: string;                 // one of Assumptions.owners
  description: string;
  vendor: string | null;
  category: string | null;
  subcategory: string | null;
  category_source: CategorySource;
  amount: number;                // signed dollars; negative = outflow
  type: TxType;
  is_internal: boolean;
  transfer_group_id: string | null;
  tags: string[];
  needs_review: boolean;
  /** who earned an income row (from income_rules `earner:`), null otherwise */
  earner: string | null;
  source_file: string;
  /** Rule-driven splits (etl/enrich/splits.py): a child names its parent's id; the parent
   *  (`is_parent`) keeps its full amount and a display-only category and is in no sum — its children
   *  carry the amounts. `null` / `false` on every ordinary row. */
  parent_id: string | null;
  is_parent: boolean;
}

/** Ledger-vs-feed drift (accounts.json): the two newest SimpleFIN balances >= 7 days apart vs
 *  every row of the account posted between them (CSV and feed: one ledger). `info` only — drift is usually the pending items the ETL
 *  skips, so a non-zero value is a note, not an alarm. */
export interface LedgerDrift {
  from: string;                  // ISO date of the older balance observation
  to: string;                    // ISO date of the newer one
  expected: number;              // balance(to) − balance(from), dollars
  got: number;                   // Σ every row of the account with from < date ≤ to, dollars
  drift: number;                 // got − expected, dollars
}

export interface AccountMeta {
  id: string;
  name: string;
  institution: string;
  owner: string;                 // one of Assumptions.owners (a 529 account is owned by a child)
  kind: "checking" | "savings" | "credit-card" | "brokerage" | "loan" | "wallet";
  /** what the balance IS, the class its rows take: the one accounts.yaml states (e.g. "Cash" for a brokerage's cash
   *  account), else Mortgage for the `mortgage: true` loan, else its kind's default. Never null. */
  asset_class: string;
  txn_data_start: string | null;
  txn_data_end: string | null;
  latest_balance: number | null;
  latest_balance_as_of: string | null;
  ledger_drift: LedgerDrift | null;   // null: not a feed-backed cash account, or < 2 balances 7 days apart
}

export interface AssetRow {
  account: string;
  account_id: string | null;     // registry key when SimpleFIN-sourced (join key)
  institution: string;
  owner: string;
  asset_class: string;
  value: number;
  liquid: boolean;
  as_of: string | null;
  as_of_assumed: boolean;
  needs_review: boolean;
  source: string;
  /** the manual asset (AppData.manualAssets) a `stated` row is; null for an account's row */
  manual_id: string | null;
  /** a manual asset before its first value, held at that value (`as_of` is that value's date); false otherwise */
  carried_back: boolean;
}

/** One dated value of a manual asset, in dollars (negative for a debt) */
export interface ManualAssetValue {
  as_of: string;
  value: number;
}

/** accounts.json `manual_assets`: a value no feed or statement reports (a home, a car) that net worth counts — an
 *  accounts.yaml `manual_entries` item or an asset entered in the app (one with an entry's id replaces it) */
export interface ManualAsset {
  id: string;
  name: string;
  /** who holds or values it; null for an asset entered in the app */
  institution: string | null;
  owner: string;
  asset_class: string;
  liquid: boolean;
  /** before this day it is not held; null: held as far back as the history goes */
  acquired: string | null;
  /** oldest first, one per day; before the first, the first is carried back */
  values: ManualAssetValue[];
  origin: "app" | "config";
}

export interface Snapshot {
  snapshot_date: string;
  /** the first row's source (rows are largest first); nothing reads it */
  source: string;
  total: number;
  /** the positive rows' sum */
  assets_total: number;
  /** the negative rows' sum, a negative number: assets_total + liabilities_total = total */
  liabilities_total: number;
  total_excl_house_car: number;
  mortgage_included: boolean;
  liquid_total: number;
  by_owner: Record<string, number>;
  by_class: Record<string, number>;
  /** the share of net worth's series held on the day (every account; a manual asset from its acquisition on) with a
   *  row in this snapshot, 3 dp */
  coverage: number;
  /** the display names of the accounts with no balance on or before the snapshot date, sorted (a manual asset is
   *  never missing: before its first value it is carried back) */
  missing: string[];
  rows: AssetRow[];
}

export interface AssetsTimeseries {
  snapshots: Snapshot[];
}

export interface Holding {
  account: string;
  account_id: string | null;     // registry key when SimpleFIN-sourced (join key)
  institution: string;
  owner: string;
  symbol: string | null;
  name: string;
  asset_class: string;
  quantity: number | null;
  value: number;
  cost_basis: number | null;
  as_of: string;
  liquid: boolean;
  notes: string | null;
}

export interface CashflowMonth {
  month: string;                 // YYYY-MM
  income: number;
  spend: number;
  invested: number;
  savings_rate: number | null;
  by_category: Record<string, { total: number; by_subcategory: Record<string, number> }>;
  income_by_subcategory: Record<string, number>;
  accounts_covered: string[];
  full_coverage: boolean;
  /** at least assumptions.yaml coverage.min_share of the cash-flow accounts with any transaction have their first one
   *  in this month or earlier: rates, baselines and averages read only these months (lib/agg.ts coverageMonths). An
   *  account that has begun never stops counting, so this is false up to the floor and true from it on. */
  in_coverage: boolean;
}

/** cashflow_monthly.json */
export interface CashflowFile {
  months: CashflowMonth[];
  /** the cash-flow accounts (checking, savings, credit cards) the coverage flags count */
  cashflow_accounts: string[];
  /** the first month in coverage (YYYY-MM), null when no month is. The dashboard derives the floor from the
   *  months' flags (lib/agg.ts coverageFloor); contract.test.ts checks the two agree. */
  coverage_floor: string | null;
}

export interface RecurringItem {
  vendor: string;
  account_id: string;
  category: string | null;
  cadence: "weekly" | "biweekly" | "monthly" | "quarterly" | "annual";
  amount_mode: "fixed" | "variable";
  occurrences: number;
  first_seen: string;
  last_seen: string;
  next_expected: string;
  active: boolean;
  typical_amount: number;
  last_amount: number;
  annualized: number;
  monthly_equivalent: number;
  price_changes: { date: string; from: number; to: number }[];
  /** Schedule (etl/enrich/recurring.py promote): active, cadence set, enough occurrences
   *  (4 weekly/biweekly, else 3), confidence >= 0.7. Status is computed in lib/schedules.ts. */
  schedule: boolean;
  /** the next expected charge dates (ISO; the first is `next_expected`), three, or thirteen for a monthly item: a
   *  monthly bill on its day of the month, else last_seen stepped by median_gap_days (etl/enrich/recurring.py) */
  expected_dates: string[];
  /** weekly 3 · biweekly 4 · monthly 6 · quarterly 14 · annual 21 */
  grace_days: number;
  /** cadence-band share × amount-mode factor (fixed 1.0 / variable 0.85), 3 dp */
  confidence: number;
  median_gap_days: number;
}

export interface QualityReport {
  files: {
    source_file: string; parser: string;
    /** the registry account a statement file was read for; null for a bank-feed pull (one pull reports many accounts) */
    account: string | null;
    rows_total: number; rows_parsed: number;
    rows_skipped: number; skip_reasons: Record<string, number>;
    duplicates_removed: number; date_min: string | null; date_max: string | null;
    parsed_sum: number;
  }[];
  checks: { name: string; status: "pass" | "warn" | "fail" | "info"; detail: string }[];
  account_month_sums: Record<string, Record<string, number>>;
  top_unmapped_vendors: { vendor: string; spend: number; txns: number }[];
  /** source file -> duplicate rows removed against an earlier file's uids */
  cross_file_duplicate_uids: Record<string, number>;
}

/** One row of vendors.json: a vendor seen on spend rows, its rule status and TTM spend. */
export interface VendorEntry {
  vendor: string;
  has_rule: boolean;                              // a vendor_rules.yaml pattern names it
  category: string | null;                        // from an explicit vendor rule or a learned rule
  subcategory: string | null;
  category_source: "vendor_rule" | "learned" | null;
  learn: boolean;                                 // false = excluded from answer-derived learning
  txns: number;
  spend_ttm: number;                              // net spend over the trailing 12 months, dollars
  first_seen: string;                             // ISO
  last_seen: string;                              // ISO
}

export interface IncomePhase {
  person: string; phase: string; start: string; end: string;
  deposits: number; total: number; avg_monthly: number;
  total_cents: number; avg_monthly_cents: number;
}

/** One household.yaml member: role "owner" is an adult, "kid" a child. The joint label is an owner but no person. */
export interface HouseholdPerson {
  name: string;
  role: "owner" | "kid";
  birth_year?: number;           // absent when the config gives none; ages are shown at year granularity
}

/** household.yaml's name and zone and its members, config/assumptions.yaml `household:` and the ETL-derived
 *  margin_account */
export interface Household {
  /** the household's display name: household.yaml `name`, else its surname, else "Household" ("" shows no name) */
  name: string;
  /** household.yaml `timezone`, an IANA zone name (the ETL always writes one; null reads as the browser's own zone) */
  timezone: string | null;
  people: HouseholdPerson[];
  margin_apr: number | null;                    // fraction, e.g. 0.11; null when assumptions.yaml states none
  retirement_accounts: Record<string, string>;  // account name -> person
  margin_account: string | null;                // name of the `margin: true` account
}

/** The loan account's balance at one snapshot date (positive dollars owed) */
export interface MortgageScheduleRow { date: string; principal_balance: number }

/** assumptions.json `mortgage`: the balance comes from the bank feed (the account's row in the current snapshot), the
 *  terms from the account's accounts.yaml `loan:` block. Every absent term is null, never 0. */
export interface MortgageBlock {
  account_id: string;
  /** positive dollars owed; null when the bank feed has not reported the account yet (0 = paid off) */
  balance: number | null;
  balance_as_of: string | null;
  /** a percent: 6.5 means 6.5 % */
  rate_pct: number | null;
  monthly_payment: number | null;
  monthly_escrow: number | null;
  escrow_balance: number | null;
  origination: { date: string | null; amount: number | null } | null;
  term_months: number | null;
  /** the account's balance at every snapshot date it has one by, oldest first; [] when never observed */
  schedule: MortgageScheduleRow[];
}

/** One category_rules.yaml `renames:` entry as assumptions.json exports it. A `subcategory` key on
 *  `from` (even when null) means "move exactly that pair"; absent means "move the whole category,
 *  each reference keeping its own subcategory unless `to` names one". First matching entry wins,
 *  one rename each (no chaining) — lib/categoryRenames.ts mirrors etl/enrich/renames.py. */
export interface CategoryRename {
  from: { category: string; subcategory?: string | null };
  to: { category: string; subcategory?: string };
}

/** assumptions.json `asset_classes`: every class the engine knows (etl/models.py), the assets and the debts, each in
 *  the engine's order */
export interface AssetClasses {
  assets: string[];
  liabilities: string[];
}

export interface Assumptions {
  projections: {
    return_scenarios_pct: number[];
    inflation_pct: number;
    withdrawal_rate_pct: number;
    ltcg_pct: number;
    state_income_tax_pct: number;
  };
  /** the `projections` keys assumptions.yaml states, sorted; every other key of `projections` is at its default */
  projections_stated: string[];
  /** household.yaml `modules` exactly as stated, module id -> on: a module it leaves out is absent, not false */
  modules: Record<string, boolean>;
  asset_classes: AssetClasses;
  /** pass-through of the assumptions.yaml keys etl/pipeline.py THRESHOLD_KEYS names */
  thresholds: Record<string, unknown>;
  taxonomy: Record<string, string[]>;
  /** household.yaml's owners in order: the adults, the joint label, the children. Every row's `owner` is one. */
  owners: string[];
  income_phases: IncomePhase[];
  /** the `mortgage: true` loan account; null when no account is the mortgage */
  mortgage: MortgageBlock | null;
  account_windows: Record<string, { start: string | null; end: string | null }>;
  /** desired asset-class allocation %, from config/assumptions.yaml `targets:` ({} when unset) */
  targets: Record<string, number>;
  /** explicit monthly category budgets, from `budgets:` ({} when unset → auto-baselines) */
  budgets: Record<string, number>;
  household: Household;
  /** category_rules.yaml `renames:` ([] when none) — the dashboard applies the same map the ETL does */
  category_renames: CategoryRename[];
  /** the names the engine files rows under (etl/models.py RESERVED); lib/categories reads them from here */
  reserved: ReservedNames;
  /** household.yaml net_worth: whether the snapshot counts credit cards (their rows are Other Debt) */
  net_worth: { include_credit_cards: boolean };
}

/** The category and subcategory names the engine itself files rows under, by role: every taxonomy holds them and no
 *  category rename moves them (etl/models.py RESERVED); lib/categories.ts SHIPPED_RESERVED holds them as the starter
 *  taxonomy ships them. */
export interface ReservedNames {
  /** a purchase no rule places, and a P2P payment no rule names */
  fallback_category: string;
  /** under the fallback category: the purchase no rule places */
  uncategorized_sub: string;
  /** under the fallback category: the P2P payment no rule names */
  p2p_unknown_sub: string;
  /** money moving between the household's own accounts */
  internal_category: string;
  /** income, dividend and interest rows */
  income_category: string;
  /** under the income category: a deposit no income rule recognises */
  income_other_sub: string;
  /** fee rows */
  fees_category: string;
}

/** The export contract version this app was built against. The ETL writes
 *  manifest.schema_version (etl/exports.py SCHEMA_VERSION) and contract.test.ts asserts
 *  equality. Bump both together on any export shape change. An export key or file is
 *  removed or renamed only in a major release. 1–22 = versions before 1.0; 23 = the 1.0
 *  baseline. */
export const EXPECTED_SCHEMA_VERSION = 23;

/** The oldest export schema this app reads (data/DataContext.tsx loadAppData). A key added after it gets its default in
 *  the loader; an export older than it is not read at all (OlderExportsError): the reload notice stands until the next
 *  refresh rewrites it. 23, the 1.0 baseline: no released image writes an older one. */
export const MIN_SCHEMA_VERSION = 23;

/** The 14 export files, in the order DataContext fetches them. schemas.ts's `_files`
 *  tripwire asserts this equals the keys of FILE_SCHEMAS (two independent lists). */
export const EXPORT_FILES = [
  "manifest.json", "transactions.json", "accounts.json", "assets_timeseries.json",
  "holdings.json", "cashflow_monthly.json", "recurring.json",
  "quality_report.json", "open_questions.json", "assumptions.json",
  "balances_timeseries.json", "networth_daily.json", "vendors.json", "ai_status.json",
] as const;
export type ExportFile = (typeof EXPORT_FILES)[number];

/** How a run of the AI step ended (etl/ai/cache.py RUN_OUTCOMES): "asked" while its request was out (a run that never
 *  returned), "answered" with its answers stored, "not_stored", or the class its request failed in. */
export type AiRunOutcome =
  | "asked" | "answered" | "not_stored" | "rejected_key" | "rate_limited" | "unavailable" | "timed_out"
  | "deadline" | "refused" | "malformed" | "request_refused" | "redirect" | "failed";

/** ai_status.json: the AI step's state, written by every ingest, counts only (etl/enrich/ai.py). */
export interface AiStatus {
  /** a key file is stored (its content is never read) */
  configured: boolean;
  /** the stored answers apply: the step is switched on for the current policy version */
  enabled: boolean;
  provider: string;
  model: string | null;
  /** host[:port] of an OpenAI-compatible service's address (household.yaml `ai.base_url`); null: the provider's own */
  endpoint_host: string | null;
  /** what is sent now; a change turns the step off until the household agrees again */
  policy_version: number;
  /** who receives the vendor names and which accounts are asked about ("<provider>|<host>|<accounts>"): consent given
   *  to another scope does not hold */
  scope: string;
  /** the version the household last agreed to (null: none) */
  consent_policy: number | null;
  caps: { vendors_per_run: number; runs_per_day: number };
  /** the newest run's counts (at: UTC, with a Z), or null before any */
  last_run: { at: string; outcome: AiRunOutcome; sent: number; answered: number; cached: number } | null;
  /** answers are stored, switched on or not: they can be forgotten */
  answers_stored: boolean;
  /** what the stored answers filed */
  applied: { vendors: number; rows: number };
  /** the stored answers not applied, by reason */
  skipped: { low_confidence: number; stale_category: number; not_candidate: number };
  /** how many vendors would be sent, and how many are kept back for each reason */
  preview: { vendors: number; withheld: { name: number; digits: number; address: number; length: number; seen: number } };
}

/** ai_status.json when it could not be read (DataContext.loadAppData's placeholder): no key stored, the
 *  step not switched on, nothing run, filed or counted. The model is unknown (the ETL's default provider stands in for
 *  the provider); policy_version 0 is no version the household could have agreed to. */
export const AI_STATUS_NONE: AiStatus = {
  configured: false, enabled: false, provider: "anthropic", model: null, endpoint_host: null, policy_version: 0, scope: "",
  consent_policy: null, caps: { vendors_per_run: 0, runs_per_day: 0 }, last_run: null, answers_stored: false,
  applied: { vendors: 0, rows: 0 },
  skipped: { low_confidence: 0, stale_category: 0, not_candidate: 0 },
  preview: { vendors: 0, withheld: { name: 0, digits: 0, address: 0, length: 0, seen: 0 } },
};

export interface Manifest {
  /** export contract version — must equal EXPECTED_SCHEMA_VERSION (contract.test.ts) */
  schema_version: number;
  /** ingest time: the household time zone's wall clock (household.yaml `timezone`) WITH its UTC offset, so it
   *  compares correctly against an answer's UTC `answered_at` (lib/answers.ts).
   *  Its first 19 chars are the plain wall clock, which is what the display
   *  slices. NOT a data-freshness signal: it advances on every deploy and on a
   *  refresh whose feed fetch failed — use simplefin_fetched_at for that. */
  generated_at: string;
  /** when the bank feed was actually pulled: UTC ISO-8601 with a Z, from the
   *  snapshot's own filename. null until the store holds a snapshot. This is
   *  the honest "how old are these balances" number. */
  simplefin_fetched_at: string | null;
  transaction_count: number;
  current_snapshot_date: string | null;
  setup: Setup;
  files: string[];
}

/** manifest.json `setup`: what the household has configured, whatever data has arrived */
export interface Setup {
  /** the accounts accounts.yaml lists */
  accounts: number;
  /** some account names a bank-feed id (no snapshot needed) */
  feed: boolean;
  /** the statement files the accounts' globs match, each once */
  import_files: number;
  /** the values stated by hand that net worth counts (accounts.json manual_assets) */
  manual_assets: number;
}

export interface OpenQuestion { topic: string; question: string; context: string }

/** One account-balance observation (from a SimpleFIN snapshot or a CSV-derived
 *  latest balance). Balances are signed as the institution reports them — credit
 *  cards are negative (amount owed); utilization = |balance|/limit. */
export interface BalancePoint {
  date: string;                  // ISO
  balance: number;
  available: number | null;
  limit: number | null;
  utilization: number | null;
  source: string;                // the ETL BalanceObs.source: "simplefin" or "csv"
}

export interface BalancesTimeseries {
  series: Record<string, BalancePoint[]>;   // account id -> points (date-sorted)
  /** asset_class: the class the account's rows take, as accounts.json gives it. Never null. */
  accounts: Record<string, { name: string; institution: string; owner: string;
                             kind: string; asset_class: string }>;
}

export interface NetworthDailyPoint {
  date: string;                  // ISO
  networth: number;
  coverage: number;              // 0..1 fraction of net-worth accounts observed by this date
  source: string;                // "simplefin"
}

export interface NetworthDaily {
  series: NetworthDailyPoint[];          // date-sorted; last point == current snapshot total
  /** the current snapshot's total; null with no snapshot (the net worth is not known yet) */
  current_total: number | null;
  current_snapshot_date: string | null;
}

export interface AppData {
  manifest: Manifest;
  /** the CATEGORY view (lib/splits.ts): split children and ordinary rows, never a split parent —
   *  what every sum, the Review inbox and every category judgement read */
  transactions: Transaction[];
  /** the VENDOR view: split parents (full amount) and ordinary rows, never a child — one row per
   *  purchase, for listing and counting purchases (VendorDrill, the FilterBar's M, the footer) */
  purchases: Transaction[];
  /** split parents by id, for the register's regrouping */
  splitParents: Map<string, Transaction>;
  accounts: AccountMeta[];
  /** accounts.json `manual_assets`, sorted by name ([] when the file could not be read) */
  manualAssets: ManualAsset[];
  /** accounts.json `manual_overridden`: the accounts.yaml `manual_entries` items an asset entered in the app hides (one
   *  of the same id, which net worth counts instead), each `origin: "config"`, sorted by name ([] when none, or when the
   *  file could not be read) */
  manualOverridden: ManualAsset[];
  /** the snapshots comparable with the newest (lib/comparableSnapshots.ts), oldest first, the newest last: the only
   *  ones any page reads */
  assets: AssetsTimeseries;
  /** the earlier month ends left out of `assets` because not every account was reporting yet; only the Data page's
   *  caveats read them, listing each with what it misses */
  partialSnapshots: Snapshot[];
  holdings: Holding[];
  cashflow: CashflowMonth[];
  recurring: RecurringItem[];
  quality: QualityReport;
  questions: OpenQuestion[];
  /** unmatched-internal transfer legs: excluded from spending, not actionable */
  dataGaps: OpenQuestion[];
  assumptions: Assumptions;
  balances: BalancesTimeseries;
  networthDaily: NetworthDaily;
  /** vendors.json rows (optional export: [] when missing; read by the Data page's learned-rules card) */
  vendors: VendorEntry[];
  /** ai_status.json (a file that could not be read: AI_STATUS_NONE, nothing configured or switched on) */
  aiStatus: AiStatus;
}

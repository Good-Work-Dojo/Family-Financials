// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Zod schemas for the ETL's JSON export contract — the runtime twin of types.ts.
// Every object is strict: a field the ETL adds or drops fails contract.test.ts
// (and the AssertExact checks below fail tsc), so the two sides can't drift silently.
import { z } from "zod";
import type {
  AccountMeta, AiStatus, AssetClasses, AssetRow, Assumptions, BalancePoint, CashflowFile, CashflowMonth, CategoryRename,
  ExportFile, Holding, IncomePhase, LedgerDrift, Manifest, ManualAsset, NetworthDailyPoint, OpenQuestion,
  QualityReport, RecurringItem, ReservedNames, Setup, Snapshot, Transaction, VendorEntry,
} from "./types";

/** Compile-time bidirectional equality between a zod-inferred type and a types.ts
 *  interface (one-way `satisfies` would miss removed fields). */
type AssertExact<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;

const iso = z.string(); // dates stay strings in the contract; format is ETL-tested

export const TransactionSchema = z.strictObject({
  id: z.string(),
  date: iso,
  account: z.string(),
  account_name: z.string(),
  owner: z.string(),
  description: z.string(),
  vendor: z.string().nullable(),
  category: z.string().nullable(),
  subcategory: z.string().nullable(),
  category_source: z.enum(["internal", "type_default", "parser", "vendor_rule", "learned", "keyword",
                           "bank_label", "fallback", "answer", "override", "split", "ai"]),
  amount: z.number(),
  type: z.enum(["purchase", "income", "transfer", "cc-payment",
                "investment", "dividend", "fee", "interest"]),
  is_internal: z.boolean(),
  transfer_group_id: z.string().nullable(),
  tags: z.array(z.string()),
  needs_review: z.boolean(),
  earner: z.string().nullable(),
  source_file: z.string(),
  parent_id: z.string().nullable(),
  is_parent: z.boolean(),
});

export const LedgerDriftSchema = z.strictObject({
  from: iso, to: iso, expected: z.number(), got: z.number(), drift: z.number(),
});

export const AccountMetaSchema = z.strictObject({
  id: z.string(),
  name: z.string(),
  institution: z.string(),
  owner: z.string(),
  kind: z.enum(["checking", "savings", "credit-card", "brokerage", "loan", "wallet"]),
  asset_class: z.string(),   // resolved by the ETL: never null
  txn_data_start: z.string().nullable(),
  txn_data_end: z.string().nullable(),
  latest_balance: z.number().nullable(),
  latest_balance_as_of: z.string().nullable(),
  ledger_drift: LedgerDriftSchema.nullable(),
});

export const AssetRowSchema = z.strictObject({
  account: z.string(),
  account_id: z.string().nullable(),
  institution: z.string(),
  owner: z.string(),
  asset_class: z.string(),
  value: z.number(),
  liquid: z.boolean(),
  as_of: z.string().nullable(),
  as_of_assumed: z.boolean(),
  needs_review: z.boolean(),
  source: z.string(),
  manual_id: z.string().nullable(),
  carried_back: z.boolean(),
});

export const ManualAssetSchema = z.strictObject({
  id: z.string(),
  name: z.string(),
  institution: z.string().nullable(),
  owner: z.string(),
  asset_class: z.string(),
  liquid: z.boolean(),
  acquired: iso.nullable(),
  values: z.array(z.strictObject({ as_of: iso, value: z.number() })).min(1),
  origin: z.enum(["app", "config"]),
});

export const SnapshotSchema = z.strictObject({
  snapshot_date: iso,
  source: z.string(),
  total: z.number(),
  assets_total: z.number().nonnegative(),
  liabilities_total: z.number().nonpositive(),
  total_excl_house_car: z.number(),
  mortgage_included: z.boolean(),
  liquid_total: z.number(),
  by_owner: z.record(z.string(), z.number()),
  by_class: z.record(z.string(), z.number()),
  coverage: z.number().min(0).max(1),
  missing: z.array(z.string()),
  rows: z.array(AssetRowSchema),
});

export const HoldingSchema = z.strictObject({
  account: z.string(),
  account_id: z.string().nullable(),
  institution: z.string(),
  owner: z.string(),
  symbol: z.string().nullable(),
  name: z.string(),
  asset_class: z.string(),
  quantity: z.number().nullable(),
  value: z.number(),
  cost_basis: z.number().nullable(),
  as_of: z.string(),
  liquid: z.boolean(),
  notes: z.string().nullable(),
});

export const CashflowMonthSchema = z.strictObject({
  month: z.string(),
  income: z.number(),
  spend: z.number(),
  invested: z.number(),
  savings_rate: z.number().nullable(),
  by_category: z.record(z.string(), z.strictObject({
    total: z.number(),
    by_subcategory: z.record(z.string(), z.number()),
  })),
  income_by_subcategory: z.record(z.string(), z.number()),
  accounts_covered: z.array(z.string()),
  full_coverage: z.boolean(),
  in_coverage: z.boolean(),
});

export const CashflowFileSchema = z.strictObject({
  months: z.array(CashflowMonthSchema),
  cashflow_accounts: z.array(z.string()),
  coverage_floor: z.string().nullable(),
});

export const RecurringItemSchema = z.strictObject({
  vendor: z.string(),
  account_id: z.string(),
  category: z.string().nullable(),
  cadence: z.enum(["weekly", "biweekly", "monthly", "quarterly", "annual"]),
  amount_mode: z.enum(["fixed", "variable"]),
  occurrences: z.number(),
  first_seen: iso,
  last_seen: iso,
  next_expected: iso,
  active: z.boolean(),
  typical_amount: z.number(),
  last_amount: z.number(),
  annualized: z.number(),
  monthly_equivalent: z.number(),
  price_changes: z.array(z.strictObject({ date: iso, from: z.number(), to: z.number() })),
  schedule: z.boolean(),
  expected_dates: z.array(iso),
  grace_days: z.number(),
  confidence: z.number(),
  median_gap_days: z.number(),
});

export const QualityReportSchema = z.strictObject({
  files: z.array(z.strictObject({
    source_file: z.string(), parser: z.string(), account: z.string().nullable(), rows_total: z.number(),
    rows_parsed: z.number(), rows_skipped: z.number(),
    skip_reasons: z.record(z.string(), z.number()),
    duplicates_removed: z.number(),
    date_min: z.string().nullable(), date_max: z.string().nullable(),
    parsed_sum: z.number(),
  })),
  checks: z.array(z.strictObject({
    name: z.string(),
    status: z.enum(["pass", "warn", "fail", "info"]),
    detail: z.string(),
  })),
  account_month_sums: z.record(z.string(), z.record(z.string(), z.number())),
  top_unmapped_vendors: z.array(z.strictObject({
    vendor: z.string(), spend: z.number(), txns: z.number(),
  })),
  cross_file_duplicate_uids: z.record(z.string(), z.number()),
});

export const IncomePhaseSchema = z.strictObject({
  person: z.string(), phase: z.string(), start: iso, end: iso,
  deposits: z.number(), total: z.number(), avg_monthly: z.number(),
  total_cents: z.number(), avg_monthly_cents: z.number(),
});

export const OpenQuestionSchema = z.strictObject({
  topic: z.string(), question: z.string(), context: z.string(),
});

export const BalancePointSchema = z.strictObject({
  date: iso,
  balance: z.number(),
  available: z.number().nullable(),
  limit: z.number().nullable(),
  utilization: z.number().nullable(),
  source: z.string(),
});

export const NetworthDailyPointSchema = z.strictObject({
  date: iso,
  networth: z.number(),
  coverage: z.number(),
  source: z.string(),
});

const count = z.number().int().nonnegative();

export const SetupSchema = z.strictObject({
  accounts: count,
  feed: z.boolean(),
  import_files: count,
  manual_assets: count,
});

export const ManifestSchema = z.strictObject({
  schema_version: z.number().int().positive(),
  // offset-bearing, so it compares as an instant (lib/answers.ts) rather than as
  // a string — the first 19 chars stay the plain wall clock the display slices
  generated_at: z.string().regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/),
  // UTC with a Z: unambiguous in any browser timezone
  simplefin_fetched_at: z.string().endsWith("Z").nullable(),
  transaction_count: z.number(),
  current_snapshot_date: z.string().nullable(),
  setup: SetupSchema,
  files: z.array(z.string()),
});

// ---- file-level schemas (the 14 exports as they exist on disk) ----

export const HouseholdSchema = z.strictObject({
  name: z.string(),
  // the ETL always writes the household's zone; the contract keeps null, which reads as the browser's own zone
  timezone: z.string().nullable(),
  people: z.array(z.strictObject({
    name: z.string(), role: z.enum(["owner", "kid"]), birth_year: z.number().int().optional(),
  })),
  margin_apr: z.number().nullable(),
  retirement_accounts: z.record(z.string(), z.string()),
  margin_account: z.string().nullable(),
});

export const CategoryRenameSchema = z.strictObject({
  from: z.strictObject({ category: z.string(), subcategory: z.string().nullable().optional() }),
  to: z.strictObject({ category: z.string(), subcategory: z.string().optional() }),
});

export const ReservedNamesSchema = z.strictObject({
  fallback_category: z.string(),
  uncategorized_sub: z.string(),
  p2p_unknown_sub: z.string(),
  internal_category: z.string(),
  income_category: z.string(),
  income_other_sub: z.string(),
  fees_category: z.string(),
});

export const AssetClassesSchema = z.strictObject({
  assets: z.array(z.string()),
  liabilities: z.array(z.string()),
});

export const AssumptionsSchema = z.strictObject({
  projections: z.strictObject({
    return_scenarios_pct: z.array(z.number()),
    inflation_pct: z.number(),
    withdrawal_rate_pct: z.number(),
    ltcg_pct: z.number(),
    state_income_tax_pct: z.number(),
  }),
  projections_stated: z.array(z.string()),
  modules: z.record(z.string(), z.boolean()),
  asset_classes: AssetClassesSchema,
  targets: z.record(z.string(), z.number()),
  budgets: z.record(z.string(), z.number()),
  household: HouseholdSchema,
  category_renames: z.array(CategoryRenameSchema),
  reserved: ReservedNamesSchema,
  net_worth: z.strictObject({ include_credit_cards: z.boolean() }),
  thresholds: z.record(z.string(), z.unknown()),
  taxonomy: z.record(z.string(), z.array(z.string())),
  owners: z.array(z.string()),   // membership of every row's owner is checked in contract.test.ts
  income_phases: z.array(IncomePhaseSchema),
  mortgage: z.strictObject({
    account_id: z.string(),
    balance: z.number().nullable(),
    balance_as_of: iso.nullable(),
    rate_pct: z.number().nullable(),
    monthly_payment: z.number().nullable(),
    monthly_escrow: z.number().nullable(),
    escrow_balance: z.number().nullable(),
    origination: z.strictObject({ date: iso.nullable(), amount: z.number().nullable() }).nullable(),
    term_months: z.number().int().nullable(),
    schedule: z.array(z.strictObject({ date: iso, principal_balance: z.number() })),
  }).nullable(),
  account_windows: z.record(z.string(), z.strictObject({
    start: z.string().nullable(), end: z.string().nullable(),
  })),
});

export const VendorEntrySchema = z.strictObject({
  vendor: z.string(),
  has_rule: z.boolean(),
  category: z.string().nullable(),
  subcategory: z.string().nullable(),
  category_source: z.enum(["vendor_rule", "learned"]).nullable(),
  learn: z.boolean(),
  txns: z.number(),
  spend_ttm: z.number(),
  first_seen: iso,
  last_seen: iso,
});

export const AiStatusSchema = z.strictObject({
  configured: z.boolean(),
  enabled: z.boolean(),
  provider: z.string(),
  model: z.string().nullable(),
  endpoint_host: z.string().nullable(),
  policy_version: z.number().int(),
  scope: z.string(),
  consent_policy: z.number().int().nullable(),
  caps: z.strictObject({ vendors_per_run: count, runs_per_day: count }),
  last_run: z.strictObject({
    at: iso,
    outcome: z.enum(["asked", "answered", "not_stored", "rejected_key", "rate_limited", "unavailable", "timed_out",
                     "deadline", "refused", "malformed", "request_refused", "redirect", "failed"]),
    sent: count, answered: count, cached: count,
  }).nullable(),
  answers_stored: z.boolean(),
  applied: z.strictObject({ vendors: count, rows: count }),
  skipped: z.strictObject({ low_confidence: count, stale_category: count, not_candidate: count }),
  preview: z.strictObject({
    vendors: count,
    withheld: z.strictObject({ name: count, digits: count, address: count, length: count, seen: count }),
  }),
});

export const FILE_SCHEMAS = {
  "manifest.json": ManifestSchema,
  "transactions.json": z.strictObject({
    count: z.number(),
    transactions: z.array(TransactionSchema),
  }),
  "accounts.json": z.strictObject({ accounts: z.array(AccountMetaSchema), manual_assets: z.array(ManualAssetSchema),
    manual_overridden: z.array(ManualAssetSchema) }),
  "assets_timeseries.json": z.strictObject({
    snapshots: z.array(SnapshotSchema),
  }),
  "holdings.json": z.strictObject({ holdings: z.array(HoldingSchema) }),
  "cashflow_monthly.json": CashflowFileSchema,
  "recurring.json": z.strictObject({ items: z.array(RecurringItemSchema) }),
  "quality_report.json": QualityReportSchema,
  "open_questions.json": z.strictObject({
    questions: z.array(OpenQuestionSchema),
    data_gaps: z.array(OpenQuestionSchema),
  }),
  "assumptions.json": AssumptionsSchema,
  "balances_timeseries.json": z.strictObject({
    series: z.record(z.string(), z.array(BalancePointSchema)),
    accounts: z.record(z.string(), z.strictObject({
      name: z.string(), institution: z.string(), owner: z.string(), kind: z.string(),
      asset_class: z.string(),
    })),
  }),
  "networth_daily.json": z.strictObject({
    series: z.array(NetworthDailyPointSchema),
    current_total: z.number().nullable(),   // null with no snapshot
    current_snapshot_date: z.string().nullable(),
  }),
  "vendors.json": z.strictObject({ count: z.number(), vendors: z.array(VendorEntrySchema) }),
  "ai_status.json": AiStatusSchema,
} as const;

// ---- compile-time drift tripwires (both directions) ----
// A `false` here means schemas.ts and types.ts disagree — fix whichever is wrong.

const _tx: AssertExact<z.infer<typeof TransactionSchema>, Transaction> = true;
const _acct: AssertExact<z.infer<typeof AccountMetaSchema>, AccountMeta> = true;
const _ld: AssertExact<z.infer<typeof LedgerDriftSchema>, LedgerDrift> = true;
const _row: AssertExact<z.infer<typeof AssetRowSchema>, AssetRow> = true;
const _manual: AssertExact<z.infer<typeof ManualAssetSchema>, ManualAsset> = true;
const _snap: AssertExact<z.infer<typeof SnapshotSchema>, Snapshot> = true;
const _hold: AssertExact<z.infer<typeof HoldingSchema>, Holding> = true;
const _cf: AssertExact<z.infer<typeof CashflowMonthSchema>, CashflowMonth> = true;
const _cff: AssertExact<z.infer<typeof CashflowFileSchema>, CashflowFile> = true;
const _rec: AssertExact<z.infer<typeof RecurringItemSchema>, RecurringItem> = true;
const _qr: AssertExact<z.infer<typeof QualityReportSchema>, QualityReport> = true;
const _ip: AssertExact<z.infer<typeof IncomePhaseSchema>, IncomePhase> = true;
const _oq: AssertExact<z.infer<typeof OpenQuestionSchema>, OpenQuestion> = true;
const _bp: AssertExact<z.infer<typeof BalancePointSchema>, BalancePoint> = true;
const _np: AssertExact<z.infer<typeof NetworthDailyPointSchema>, NetworthDailyPoint> = true;
const _mf: AssertExact<z.infer<typeof ManifestSchema>, Manifest> = true;
const _asm: AssertExact<z.infer<typeof AssumptionsSchema>, Assumptions> = true;
const _cr: AssertExact<z.infer<typeof CategoryRenameSchema>, CategoryRename> = true;
const _rn: AssertExact<z.infer<typeof ReservedNamesSchema>, ReservedNames> = true;
const _vend: AssertExact<z.infer<typeof VendorEntrySchema>, VendorEntry> = true;
const _setup: AssertExact<z.infer<typeof SetupSchema>, Setup> = true;
const _ac: AssertExact<z.infer<typeof AssetClassesSchema>, AssetClasses> = true;
const _ai: AssertExact<z.infer<typeof AiStatusSchema>, AiStatus> = true;
const _files: AssertExact<keyof typeof FILE_SCHEMAS, ExportFile> = true;
void [_tx, _acct, _ld, _row, _manual, _snap, _hold, _cf, _cff, _rec, _qr, _ip, _oq, _bp, _np, _mf, _asm, _cr, _rn,
  _vend, _setup, _ac, _ai, _files];

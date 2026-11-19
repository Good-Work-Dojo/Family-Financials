// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import Skeleton from "../components/Skeleton";
import { defaultClass } from "../lib/accounts";
import { dataThrough } from "../lib/agg";
import { applyAnswers, renameAnswersDoc, type OverlayResult } from "../lib/answers";
import { SHIPPED_RESERVED } from "../lib/categories";
import { clock } from "../lib/clock";
import { comparableSnapshots } from "../lib/comparableSnapshots";
import { IS_DEMO } from "../lib/demo";
import { resolveAssumptions } from "../lib/prefs";
import { childrenByParent, overlayPurchases, partitionSplits } from "../lib/splits";
import { SHIPPED_ASSET_CLASSES } from "../lib/tokens";
import {
  AI_STATUS_NONE, EXPECTED_SCHEMA_VERSION, EXPORT_FILES, type AccountMeta, type AppData, type AssetRow, type Assumptions,
  type BalancesTimeseries, type CashflowMonth, type ExportFile, type Holding, type Household, type Manifest,
  type ManualAsset, type MortgageBlock, type NetworthDaily, type OpenQuestion, type QualityReport, type RecurringItem,
  type Snapshot, type Transaction, type VendorEntry, type AiStatus,
} from "./types";
import { isSignInAnswer, leave, LOGIN_PATH, useSignedOut } from "./session";
import { useAnswersStore, type AnswersApi } from "./useAnswersStore";
import { useBuildCheck } from "./useBuildCheck";
import { usePrefsStore, type PrefsApi } from "./usePrefsStore";

/** an export that could not be read: `absent` when the server has no such file (HTTP 404) or answered with a page
 *  instead of JSON (a static server's fallback for a path it does not have), `failed` for anything else (another
 *  status, a body that is not valid JSON, a network error, and a request redirected elsewhere: a 404 or a page at the
 *  end of a redirect is a login or portal page in front of the dashboard, which says nothing of the exports) */
export class ExportReadError extends Error {
  constructor(message: string, readonly kind: "absent" | "failed") {
    super(message);
    this.name = "ExportReadError";
  }
}

/** No export has been built yet: the manifest — the file the ingest writes last — is absent. A first-run outcome (the
 *  household's first ingest has not run, or refused its config), not a failure: the app shows the first-run page. */
export class NothingBuiltError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NothingBuiltError";
  }
}

async function fetchJson<T>(name: string): Promise<T> {
  const res = await fetch(`./${name}`);
  // the session ended (a 401, or a proxy's redirect to the sign-in page; a dev server has no sign-in): the browser goes
  // to the sign-in page instead of the "couldn't load" screen. An edit not saved yet is in this browser's copy, which
  // the page that loads after signing in sends (useSyncedDoc's load). The demo build has no sign-in: such an answer is
  // a failed read like any other.
  if (!IS_DEMO && isSignInAnswer(res)) leave.toSignIn();
  const absent = res.redirected ? "failed" : "absent";
  if (!res.ok) throw new ExportReadError(`${name}: HTTP ${res.status}`, res.status === 404 ? absent : "failed");
  const body = await res.text();
  try {
    return JSON.parse(body) as T;
  } catch (e) {
    // a page ("<!doctype html>…") where the file should be is the server saying it has none; a body that starts like
    // JSON and does not parse is a broken file
    if (/^\s*</.test(body)) {
      throw new ExportReadError(`${name}: the server answered with a page, not JSON${res.redirected ? ", after a redirect" : ""}`, absent);
    }
    throw new ExportReadError(`${name}: ${e instanceof Error ? e.message : String(e)}`, "failed");
  }
}

/** Exports without which nothing can render honestly: the Shell (manifest, transactions) and
 *  CategoryEditor + applyAnswers (assumptions, transactions) read them unguarded, and the
 *  snapshot list (assets_timeseries.json) is what every page reads for its snapshots (through
 *  `useSnapshots()`, or `data.assets.snapshots` directly where the history is the subject:
 *  Trends, House, Data, the milestones): a page with no snapshot says the household has no
 *  balances yet, which a file that failed to load must never pass for. One of these failing keeps the full-screen error. Every other file
 *  degrades to a typed EMPTY placeholder (never a made-up number) and is reported in
 *  `missing`, so RequireData can show a missing-data card where that file's cards would be. */
const FATAL_FILES: ReadonlySet<ExportFile> = new Set<ExportFile>([
  "manifest.json", "transactions.json", "assumptions.json", "assets_timeseries.json",
]);

export interface LoadedAppData { data: AppData; missing: Set<ExportFile> }

const EMPTY_QUALITY: QualityReport = {
  files: [], checks: [], account_month_sums: {}, top_unmapped_vendors: [], cross_file_duplicate_uids: {},
};

// An export set from an OLDER schema can reach this bundle: during a deploy the old image's refresher (a
// "Refresh now", a cron tick) may write exports between the deploy's own ingest and `up -d`. So every key
// added at schema 4 or later is defaulted here, on load,
// to what "not there" means - never read bare, where an `undefined.length` in a memo above every
// SectionBoundary is a white screen. Schema 4: the recurring schedule keys (unpromoted: no schedule, no
// expected dates) and accounts' ledger_drift (null: the check did not run); schema 5: assumptions'
// category_renames ([]: nothing renamed); schema 6: transactions' parent_id / is_parent (null / false: an
// ordinary row); schema 13: assumptions' reserved (the names the shipped taxonomy files rows under,
// SHIPPED_RESERVED); schema 14: the cash-flow months' in_coverage (that month's full_coverage, so the floor the
// dashboard derives is the first fully covered month); schema 15: assumptions' net_worth (cards not counted: no older
// export counted them) and each snapshot's assets_total / liabilities_total (summed from its rows), coverage (1) and
// missing ([]); schema 16: assumptions' mortgage block, an older one mapped onto its keys (withMortgageBlock); schema
// 17: the projections' own figure for the home is gone, an older export's dropped unread (withoutHomeFigure); schema
// 19: the manifest's setup (the accounts counted, a feed configured when the feed was ever fetched, no import file
// counted, the newest snapshot's stated rows as the manual assets: withSetup), the household's name ("": none stated) and
// zone (null), modules ({}: none stated),
// projections_stated ([]: every projection read as a default), asset_classes (the dashboard's own list,
// SHIPPED_ASSET_CLASSES) and each account's resolved class, in accounts.json and in balances_timeseries.json's accounts
// map (a null one is its kind's default: withAccountKeys, withBalanceAccountKeys);
// networth_daily's current_total is null with no snapshot (an older export's 0 there too: withCurrentTotal), and so is
// its placeholder; schema 20: accounts.json's manual_assets ([]: none listed) and each snapshot row's manual_id (null:
// no manual asset named) and carried_back (false: withRowKeys); schema 21: accounts.json's manual_overridden ([]: no
// config entry hidden); schema 22: the quality report's files[].account (null: no account named, withFileAccounts);
// schema 23: ai_status.json, a whole file, whose absence reads as AI_STATUS_NONE (no key, not switched on).
// Older keys predate any image
// that can still be running.
// (vendors.json, schema 3, is a whole optional file: a 404 is already its placeholder.)
// The other direction - a NEWER export under this (older, still open) bundle, which would read it the old
// way - is caught in DataProvider: a persistent "the dashboard was updated - reload" banner (the same one a newer BUILD
// raises, data/useBuildCheck.ts: most deploys change the app and bump no schema).
type Older<T, K extends keyof T> = Omit<T, K> & Partial<Pick<T, K>>;
type ScheduleKeys = "schedule" | "expected_dates" | "grace_days" | "confidence" | "median_gap_days";
const withScheduleKeys = (r: Older<RecurringItem, ScheduleKeys>): RecurringItem => ({
  ...r, schedule: r.schedule ?? false, expected_dates: r.expected_dates ?? [], grace_days: r.grace_days ?? 0,
  confidence: r.confidence ?? 0, median_gap_days: r.median_gap_days ?? 0,
});
/** accounts.json's rows as any schema writes them: before schema 19 an account that takes its kind's class says null */
type AccountAsLoaded = Omit<Older<AccountMeta, "ledger_drift">, "asset_class"> & { asset_class: string | null };
/** ledger_drift null when the export predates it; a null class read as the one the engine gives such an account (the
 *  `mortgage: true` loan is the assumptions' mortgage account) */
const withAccountKeys = (a: AccountAsLoaded, mortgageId: string | null): AccountMeta => ({
  ...a, ledger_drift: a.ledger_drift ?? null,
  asset_class: a.asset_class ?? defaultClass(a.kind, a.id === mortgageId),
});
type BalanceAccount = BalancesTimeseries["accounts"][string];
/** balances_timeseries.json as any schema writes it: before schema 19 its accounts map says null for an account that
 *  takes its kind's class */
type BalancesAsLoaded = Omit<BalancesTimeseries, "accounts">
  & { accounts: Record<string, Omit<BalanceAccount, "asset_class"> & { asset_class: string | null }> };
/** a null class in the balances' accounts map read as accounts.json's is (withAccountKeys): the class the engine gives
 *  such an account, so isCashLike, which decides by class alone, keeps a checking account on its kind's class as cash */
const withBalanceAccountKeys = (b: BalancesAsLoaded, mortgageId: string | null): BalancesTimeseries => ({
  ...b,
  accounts: Object.fromEntries(Object.entries(b.accounts).map(([id, a]): [string, BalanceAccount] => [id, {
    ...a, asset_class: a.asset_class ?? defaultClass(a.kind as AccountMeta["kind"], id === mortgageId) }])),
});
type ManifestAsLoaded = Older<Manifest, "setup">;
/** A manifest before schema 19 says nothing of what is configured: the accounts are the ones accounts.json lists, a
 *  feed counts as configured once it was fetched, no import file is counted, and the manual assets are the newest
 *  snapshot's hand-stated rows (none without a snapshot). */
const withSetup = (m: ManifestAsLoaded, accounts: number, newest: Pick<Snapshot, "rows"> | undefined): Manifest => ({
  ...m,
  setup: m.setup ?? { accounts, feed: m.simplefin_fetched_at != null, import_files: 0,
    manual_assets: newest?.rows.filter((r) => r.source === "stated").length ?? 0 },
});
/** With no snapshot no net worth is known: an export older than schema 19 summed no rows to 0 there, read as null */
const withCurrentTotal = (n: NetworthDaily): NetworthDaily =>
  ({ ...n, current_total: n.current_snapshot_date === null ? null : n.current_total });
type HouseholdAsLoaded = Older<Household, "name" | "timezone">;
const withHouseholdKeys = (h: HouseholdAsLoaded): Household =>
  ({ ...h, name: h?.name ?? "", timezone: h?.timezone ?? null });
type AssumptionKeys = "category_renames" | "reserved" | "net_worth" | "modules" | "projections_stated" | "asset_classes";
/** assumptions.json `mortgage` as a schema-15 export wrote it: the balance under latest_balance*, the terms under the
 *  names assumptions.yaml gave them, and schedule rows that carry more than a date and a principal balance */
interface MortgageV15 {
  latest_balance: number | null;
  latest_balance_as_of: string | null;
  note_rate_pct?: number | null;
  monthly_pi?: number | null;
  monthly_escrow?: number | null;
  escrow_balance?: number | null;
  schedule: { date: string; principal_balance: number }[];
}
/** The projections as the dashboard reads them: a schema-16 export also carries its own figure for the home, dropped
 *  here unread, since the home's value is the snapshot's House-class rows (lib/lifeSummary homeValue). */
const withoutHomeFigure = (p: Assumptions["projections"]): Assumptions["projections"] => {
  if (p == null) return p;
  const { home_value_estimate: _unread, ...rest } = p as Assumptions["projections"] & { home_value_estimate?: unknown };
  return rest;
};
type AssumptionsAsLoaded = Omit<Older<Assumptions, AssumptionKeys>, "mortgage" | "household">
  & { mortgage?: MortgageBlock | MortgageV15 | null; household: HouseholdAsLoaded };
/** A schema-15 block on the schema-16 keys: its balance, rate (note_rate_pct), payment (monthly_pi) and escrow keys,
 *  and its schedule's dates and balances. It named no account, so the id is the current snapshot's Mortgage-class row's
 *  ("" when it has none), and it stated no origination or term (null). A block of the current shape passes through. */
const withMortgageBlock = (m: MortgageBlock | MortgageV15 | null | undefined,
                           current: Pick<Snapshot, "rows"> | undefined): MortgageBlock | null => {
  if (m == null) return null;
  if (!("latest_balance" in m)) return m;
  return {
    account_id: current?.rows.find((r) => r.asset_class === "Mortgage" && r.account_id !== null)?.account_id ?? "",
    balance: m.latest_balance, balance_as_of: m.latest_balance_as_of,
    rate_pct: m.note_rate_pct ?? null, monthly_payment: m.monthly_pi ?? null,
    monthly_escrow: m.monthly_escrow ?? null, escrow_balance: m.escrow_balance ?? null,
    origination: null, term_months: null,
    schedule: m.schedule.map((r) => ({ date: r.date, principal_balance: r.principal_balance })),
  };
};
const withAssumptionKeys = (a: AssumptionsAsLoaded, current: Pick<Snapshot, "rows"> | undefined): Assumptions => ({
  ...a, category_renames: a.category_renames ?? [], reserved: a.reserved ?? SHIPPED_RESERVED,
  net_worth: a.net_worth ?? { include_credit_cards: false },
  mortgage: withMortgageBlock(a.mortgage, current),
  projections: withoutHomeFigure(a.projections),
  household: withHouseholdKeys(a.household),
  modules: a.modules ?? {},
  projections_stated: a.projections_stated ?? [],
  asset_classes: a.asset_classes ?? SHIPPED_ASSET_CLASSES,
});
type SnapshotKeys = "assets_total" | "liabilities_total" | "coverage" | "missing";
type RowKeys = "manual_id" | "carried_back";
/** a snapshot as any schema writes it: before schema 20 its rows lack RowKeys */
type SnapshotAsLoaded = Omit<Older<Snapshot, SnapshotKeys>, "rows"> & { rows: Older<AssetRow, RowKeys>[] };
/** a row before schema 20 names no manual asset and is never carried back */
const withRowKeys = (r: Older<AssetRow, RowKeys>): AssetRow =>
  ({ ...r, manual_id: r.manual_id ?? null, carried_back: r.carried_back ?? false });
/** the sum of the rows whose value has `sign`, added in whole cents so the total has no float residue */
const sumRows = (rows: SnapshotAsLoaded["rows"], sign: 1 | -1): number =>
  rows.reduce((cents, r) => cents + (Math.sign(r.value) === sign ? Math.round(r.value * 100) : 0), 0) / 100;
const withSnapshotKeys = (s: SnapshotAsLoaded): Snapshot => ({
  ...s,
  assets_total: s.assets_total ?? sumRows(s.rows, 1),
  liabilities_total: s.liabilities_total ?? sumRows(s.rows, -1),
  coverage: s.coverage ?? 1,
  missing: s.missing ?? [],
  rows: s.rows.map(withRowKeys),
});
type SplitKeys = "parent_id" | "is_parent";
const withSplitKeys = (t: Older<Transaction, SplitKeys>): Transaction =>
  ({ ...t, parent_id: t.parent_id ?? null, is_parent: t.is_parent ?? false });
const withCoverage = (m: Older<CashflowMonth, "in_coverage">): CashflowMonth =>
  ({ ...m, in_coverage: m.in_coverage ?? m.full_coverage });
/** cashflow_monthly.json as any schema writes it; the loader reads the floor from the months, never the file's key, and
 *  the cash-flow accounts with any transaction from the months' `accounts_covered`, never its account list */
type CashflowAsLoaded = { months: Older<CashflowMonth, "in_coverage">[] };
type FileStat = QualityReport["files"][number];
/** quality_report.json as any schema writes it: before schema 22 a file names no account */
type QualityAsLoaded = Omit<QualityReport, "files"> & { files: Older<FileStat, "account">[] };
const withFileAccounts = (q: QualityAsLoaded): QualityReport =>
  ({ ...q, files: q.files.map((f) => ({ ...f, account: f.account ?? null })) });

export async function loadAppData(): Promise<LoadedAppData> {
  // same order as EXPORT_FILES — the index is how a rejection is mapped back to its file
  const results = await Promise.allSettled([
    fetchJson<ManifestAsLoaded>("manifest.json"),
    fetchJson<{ transactions: Older<Transaction, SplitKeys>[] }>("transactions.json"),
    fetchJson<{ accounts: AccountAsLoaded[]; manual_assets?: ManualAsset[]; manual_overridden?: ManualAsset[] }>("accounts.json"),
    fetchJson<{ snapshots: SnapshotAsLoaded[] }>("assets_timeseries.json"),
    fetchJson<{ holdings: Holding[] }>("holdings.json"),
    fetchJson<CashflowAsLoaded>("cashflow_monthly.json"),
    fetchJson<{ items: Older<RecurringItem, ScheduleKeys>[] }>("recurring.json"),
    fetchJson<QualityAsLoaded>("quality_report.json"),
    fetchJson<{ questions: OpenQuestion[]; data_gaps?: OpenQuestion[] }>("open_questions.json"),
    fetchJson<AssumptionsAsLoaded>("assumptions.json"),
    fetchJson<BalancesAsLoaded>("balances_timeseries.json"),
    fetchJson<NetworthDaily>("networth_daily.json"),
    fetchJson<{ count: number; vendors: VendorEntry[] }>("vendors.json"),
    fetchJson<AiStatus>("ai_status.json"),
  ] as const);
  // no manifest: nothing has been built yet (the ingest writes it last, so a first ingest still running reads the same)
  const [manifestRead] = results;
  if (manifestRead.status === "rejected" && manifestRead.reason instanceof ExportReadError && manifestRead.reason.kind === "absent") {
    throw new NothingBuiltError(manifestRead.reason.message);
  }
  const missing = new Set<ExportFile>();
  const fatal: string[] = [];
  results.forEach((r, i) => {
    if (r.status !== "rejected") return;
    const file = EXPORT_FILES[i];
    if (FATAL_FILES.has(file)) fatal.push(String(r.reason instanceof Error ? r.reason.message : r.reason));
    else missing.add(file);
  });
  if (fatal.length) throw new Error(fatal.join("; "));
  const must = <T,>(r: PromiseSettledResult<T>): T => {
    if (r.status === "fulfilled") return r.value;
    throw r.reason instanceof Error ? r.reason : new Error(String(r.reason));
  };
  const or = <T,>(r: PromiseSettledResult<T>, placeholder: T): T =>
    r.status === "fulfilled" ? r.value : placeholder;
  const [manifest, tx, accounts, assets, holdings, cashflow, recurring,
    quality, questions, assumptions, balances, networthDaily, vendors, aiStatus] = results;
  const q = or(questions, { questions: [], data_gaps: [] });
  const cf = or<CashflowAsLoaded>(cashflow, { months: [] });
  const views = partitionSplits(must(tx).transactions.map(withSplitKeys));
  // every page compares snapshots with the newest, so a month end that lacks an account the newest has stays out of
  // them (it would show the day the account was added as a jump in net worth); the Data page counts those
  const allSnapshots = must(assets).snapshots.map(withSnapshotKeys);
  const snapshots = comparableSnapshots(allSnapshots);
  const asm = withAssumptionKeys(must(assumptions), allSnapshots[allSnapshots.length - 1]);
  const accountsFile = or(accounts, { accounts: [] });
  const accountList = accountsFile.accounts.map((a) => withAccountKeys(a, asm.mortgage?.account_id ?? null));
  return {
    data: {
      manifest: withSetup(must(manifest), accountList.length, allSnapshots[allSnapshots.length - 1]),
      transactions: views.transactions,
      purchases: views.purchases,
      splitParents: views.splitParents,
      accounts: accountList,
      manualAssets: accountsFile.manual_assets ?? [],
      manualOverridden: accountsFile.manual_overridden ?? [],
      assets: { snapshots: snapshots.comparable },
      partialSnapshots: snapshots.partial,
      holdings: or(holdings, { holdings: [] }).holdings,
      cashflow: cf.months.map(withCoverage),
      recurring: or(recurring, { items: [] }).items.map(withScheduleKeys),
      quality: withFileAccounts(or<QualityAsLoaded>(quality, EMPTY_QUALITY)),
      questions: q.questions,
      dataGaps: q.data_gaps ?? [],
      assumptions: asm,
      balances: withBalanceAccountKeys(or(balances, { series: {}, accounts: {} }), asm.mortgage?.account_id ?? null),
      // current_total is unread by the app; the daily line gates on `missing`. No file: no total known (null, never 0)
      networthDaily: withCurrentTotal(or(networthDaily, { series: [], current_total: null, current_snapshot_date: null })),
      vendors: or(vendors, { count: 0, vendors: [] }).vendors,
      aiStatus: or(aiStatus, AI_STATUS_NONE),
    },
    missing,
  };
}

export interface Indexes {
  /** vendor -> its PURCHASES (the vendor view: a split purchase once, at its full amount) */
  byVendor: Map<string, Transaction[]>;
  vendorList: string[];
  categoryList: string[];
  /** every tag any transaction carries, sorted and unique — the tag editor's suggestions */
  tagList: string[];
  /** the days (YYYY-MM-DD) of the oldest and the newest transaction (a row dated after the ingest day included);
   *  null when there is no transaction, which every reader answers in its own words. A window or a "through" day
   *  reads `dataThrough`; `last` is for what means the newest row itself (Data's "latest" row dated ahead) */
  dataRange: { first: string; last: string } | null;
  /** the day the data really reaches (lib/agg.ts `dataThrough`): the newest transaction's day, never past the ingest
   *  day. The ONE anchor of every date preset and default window (Explore's and the register's filter bar, their
   *  six-month default, a saved view's `win=`, ⌘K's "last 6 mo", Cash Flow's and Recurring's ranges), Explore's
   *  compare and calendar, "this month so far", and the obligations' start with no snapshot, so a row the bank dated
   *  ahead moves none of them; null with no transaction */
  dataThrough: string | null;
  /** the parts of each split purchase by parent id (category view) — the register regroups with it */
  childrenByParent: Map<string, Transaction[]>;
}

/** `generatedAt` is the manifest's ingest stamp (`dataThrough` clamps the newest row to its day; with none, or an
 *  unreadable one, the newest row is the day the data reaches) */
export function buildIndexes(txns: Transaction[], purchases: Transaction[] = txns, generatedAt = ""): Indexes {
  const byVendor = new Map<string, Transaction[]>();
  for (const t of purchases) {
    if (!t.vendor) continue;
    let arr = byVendor.get(t.vendor);
    if (!arr) byVendor.set(t.vendor, (arr = []));
    arr.push(t);
  }
  const categories = new Set<string>();
  const tags = new Set<string>();
  let range: { first: string; last: string } | null = null;
  for (const t of txns) {
    if (t.category) categories.add(t.category);
    for (const tag of t.tags) tags.add(tag);
    if (range === null) range = { first: t.date, last: t.date };
    else if (t.date < range.first) range.first = t.date;
    else if (t.date > range.last) range.last = t.date;
  }
  const vendorList = [...byVendor.keys()].sort((a, b) => a.localeCompare(b));
  return {
    byVendor,
    vendorList,
    categoryList: [...categories].sort(),
    tagList: [...tags].sort(),
    dataRange: range,
    dataThrough: dataThrough(range?.last ?? null, generatedAt),
    childrenByParent: childrenByParent(txns),
  };
}

interface Ctx {
  data: AppData;
  indexes: Indexes;
  /** review-inbox answers: store API + the overlay's row states */
  answers: AnswersApi;
  overlay: Pick<OverlayResult, "pendingIds" | "conflictedKeys">;
  /** in-app budgets/targets/projection-defaults/payroll (merged into data.assumptions) */
  prefs: PrefsApi;
  /** Re-fetch every export in place, for when a data refresh lands while the
   *  page is open. The old data stays on screen until the new set resolves, so
   *  there's no flash of the loading state; if the set fails, the old data stays
   *  and a banner offers a retry (never the first-load error screen). */
  reload: () => Promise<void>;
  /** optional exports that failed to load this session (RequireData reads this) */
  missing: ReadonlySet<ExportFile>;
}
const DataCtx = createContext<Ctx | null>(null);

export function useData(): Ctx {
  const ctx = useContext(DataCtx);
  if (!ctx) throw new Error("useData outside provider");
  return ctx;
}

/** a full-width note above the app, in the owner's words, with what to do about it */
function DataNotice({ testId, children, actions }: { testId: string; children: React.ReactNode; actions: React.ReactNode }) {
  return (
    <div role="status" data-testid={testId}
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-amber-900/60 bg-amber-950/40 px-4 py-2 text-sm text-amber-300 print:hidden sm:text-xs">
      <span className="min-w-0">{children}</span>
      <span className="ml-auto flex shrink-0 gap-2">{actions}</span>
    </div>
  );
}
const noticeBtn = "min-h-11 rounded-md border border-amber-700/60 px-2.5 text-amber-200 hover:bg-amber-900/40 md:min-h-0 md:py-1";

/** how often the page shown while nothing is built looks for the exports again */
export const NOTHING_BUILT_RECHECK_MS = 30_000;

/** what one look for the exports found: still no export ("nothing-built"), exports it could not read ("failed": a 403,
 *  a 500, a network error, a file that does not parse) or the data, now loaded ("loaded") */
export type LookOutcome = "nothing-built" | "failed" | "loaded";

/** `nothingBuilt` is what the app shows while no export has been built yet (the loader's NothingBuiltError), given the
 *  way to look again (it resolves to what that look found, a LookOutcome); this provider looks again by itself every
 *  NOTHING_BUILT_RECHECK_MS while it is on screen, so the app opens once the first ingest has written its exports.
 *  While it is on screen only a load that works leaves it: a look that fails for another reason (a network blip)
 *  leaves it there, looking again. Without it, that outcome shows the error screen like any other failed first load.
 *  `nothingBuilt` is also given how many of the timed looks found nothing built (`quietLooks`), so a line about a failed
 *  manual look can go once the exports have been reached again. */
export function DataProvider({ children, nothingBuilt }: {
  children: React.ReactNode;
  nothingBuilt?: (checkAgain: () => Promise<LookOutcome>, quietLooks: number) => React.ReactNode;
}) {
  const [loaded, setLoaded] = useState<LoadedAppData | null>(null);
  /** the FIRST load failed: nothing to show but the error screen */
  const [error, setError] = useState<string | null>(null);
  /** the first load found nothing built (NothingBuiltError): the first-run page, when the app gives one */
  const [unbuilt, setUnbuilt] = useState(false);
  /** a later reload failed: the last good data stays on screen under a retry banner */
  const [reloadError, setReloadError] = useState<string | null>(null);
  const hasData = useRef(false);
  /** the last first-load outcome was nothing built: a later look that fails for another reason leaves it standing */
  const wasUnbuilt = useRef(false);
  /** how many loads have succeeded: the first is this page's own, every later one is a reload (useBuildCheck's trigger) */
  const [loads, setLoads] = useState(0);
  const answers = useAnswersStore();
  const prefs = usePrefsStore();

  /** one load of every export, and what it found: nothing built (no export yet, and no data loaded before), a failure,
   *  or the data */
  const load = useCallback(
    (): Promise<LookOutcome> => loadAppData().then(
      (next): LookOutcome => {
        // the demo's clock stands at the moment its exports were built, before anything reads it
        if (IS_DEMO) clock.freeze(next.data.manifest.generated_at);
        hasData.current = true; wasUnbuilt.current = false;
        setLoaded(next); setReloadError(null); setError(null); setUnbuilt(false);
        setLoads((n) => n + 1);
        return "loaded";
      },
      (e): LookOutcome => {
        if (hasData.current) { setReloadError(String(e)); return "failed"; }
        const nothing = e instanceof NothingBuiltError;
        if (wasUnbuilt.current && !nothing) return "failed";
        wasUnbuilt.current = nothing;
        setError(String(e));
        setUnbuilt(nothing);
        return nothing ? "nothing-built" : "failed";
      }),
    [],
  );
  const reload = useCallback(() => load().then(() => undefined), [load]);
  const showUnbuilt = error !== null && unbuilt && nothingBuilt !== undefined;
  /** how many timed looks found nothing built (the nothing-built view's `quietLooks`) */
  const [quietLooks, setQuietLooks] = useState(0);
  // while nothing is built, look again now and then: the first ingest writes the exports and the app opens by itself
  useEffect(() => {
    if (!showUnbuilt) return;
    const id = setInterval(() => void load().then((outcome) => {
      if (outcome === "nothing-built") setQuietLooks((n) => n + 1);
    }), NOTHING_BUILT_RECHECK_MS);
    return () => clearInterval(id);
  }, [showUnbuilt, load]);
  // a deploy while this tab stayed open: the bundle in it is the old one until the page is reloaded (the demo build
  // asks no server: useBuildCheck)
  const newerBuild = useBuildCheck(Math.max(0, loads - 1));
  // a store call answered 401: saves are held until the person signs in again (data/session.ts)
  const signedOut = useSignedOut();
  const data = loaded?.data ?? null;

  useEffect(() => {
    void reload();
  }, [reload]);

  // The instant-feel overlay: answered categories render immediately, before
  // the next ingest makes them durable. With an empty answers doc this is an
  // exact identity (same transactions array) — zero effect on existing views.
  // The stored answers are seen through assumptions.category_renames, the same
  // map the ETL applies at load, so a renamed category never paints old answers
  // pending or conflicted (identity again when there are no renames).
  const overlaid = useMemo(
    () => (data
      ? applyAnswers(data.transactions,
          renameAnswersDoc(answers.doc, data.assumptions.category_renames),
          data.manifest.generated_at)
      : null),
    [data, answers.doc],
  );

  // Overlay + prefs merge (category-keyed budgets follow the renames); all exact
  // identities when their docs are empty and nothing was renamed. Only the prefs
  // sections the assumptions read are deps: saving a view, a report or payroll
  // facts changes prefs.doc but never rebuilds the data (and every memo keyed on it).
  const { budgets, targets, projections } = prefs.doc;
  const assumptions = useMemo(
    () => (data ? resolveAssumptions(data.assumptions, { budgets, targets, projections }) : null),
    [data, budgets, targets, projections]);
  const merged = useMemo(() => {
    if (!data || !overlaid || !assumptions) return data;
    if (overlaid.txns === data.transactions && assumptions === data.assumptions) {
      return data;
    }
    const purchases = overlayPurchases(data.purchases, overlaid.txns, overlaid.pendingIds);
    return { ...data, transactions: overlaid.txns, purchases, assumptions };
  }, [data, overlaid, assumptions]);

  const indexes = useMemo(
    () => (merged ? buildIndexes(merged.transactions, merged.purchases, merged.manifest.generated_at) : null),
    [merged],
  );

  // One value object per real change: a fresh object on every provider render (a load flag, a failed save)
  // would re-render every useData consumer even when nothing they read moved.
  const value = useMemo<Ctx | null>(
    () => (merged && indexes && overlaid && loaded
      ? { data: merged, indexes, answers, prefs, reload, missing: loaded.missing,
          overlay: { pendingIds: overlaid.pendingIds, conflictedKeys: overlaid.conflictedKeys } }
      : null),
    [merged, indexes, overlaid, loaded, answers, prefs, reload]);

  if (showUnbuilt && nothingBuilt) return <>{nothingBuilt(load, quietLooks)}</>;
  if (error)
    return (
      <div role="alert" className="m-4 max-w-xl rounded-lg border border-red-700 bg-red-950/40 p-6 text-red-200 sm:m-10">
        <h1 className="mb-2 text-lg font-semibold">Couldn't load data</h1>
        <p className="text-sm">
          The dashboard couldn't read its data. If an ingest is running or the dashboard is being updated, this clears
          up within a minute or two.
        </p>
        <button type="button" onClick={() => location.reload()}
          className="mt-3 min-h-[40px] rounded-md border border-red-700 px-3 text-sm hover:bg-red-900/40">
          Try again
        </button>
        <p className="mt-3 break-words font-mono text-[11px] opacity-70">{error}</p>
      </div>
    );
  // Wait for prefs and answers too: sections seed slider state from merged assumptions at mount (stale
  // defaults before prefs resolve), and before the answers resolve the first frame would show rows without
  // the overlay (a Review badge that jumps) and an early save would record a null undo prior.
  // (localOnly resolution is near-instant, so this never blocks noticeably.)
  if (!value || !merged || prefs.loading || answers.loading)
    return <div className="m-10"><Skeleton variant="page" /></div>;
  return (
    <DataCtx.Provider value={value}>
      {/* ONE banner, two triggers: a newer export under this older bundle (read the old way until the page reloads; an older
          one is defaulted in loadAppData), or a newer BUILD (a deploy while this tab stayed open - most deploys bump no
          schema, so the old bundle would otherwise run on, bugs and all). Persistent, and never an auto-reload: an edit
          may be in flight. */}
      {(merged.manifest.schema_version > EXPECTED_SCHEMA_VERSION || newerBuild) && (
        <DataNotice testId="notice-updated"
          actions={<button type="button" className={noticeBtn} onClick={() => location.reload()}>Reload</button>}>
          The dashboard was updated — reload this page to switch to the new version.
        </DataNotice>
      )}
      {/* ONE banner for the whole page, whichever store call found the sign-in ended; the link is a plain one, so following
          it leaves the page (the sign-in page loads no bundle), and nothing leaves by itself: the unsent edits stay here
          and in this browser's copy until then */}
      {signedOut && (
        <DataNotice testId="notice-signed-out"
          actions={<a href={LOGIN_PATH} className={`${noticeBtn} inline-flex items-center`}>Sign in</a>}>
          Signed out — sign in to save your changes.
        </DataNotice>
      )}
      {reloadError !== null && (
        <DataNotice testId="notice-reload-failed" actions={<>
          <button type="button" className={noticeBtn} onClick={() => void reload()}>Retry</button>
          <button type="button" className={noticeBtn} onClick={() => setReloadError(null)}>Dismiss</button>
        </>}>
          Couldn't load the latest data — still showing what was loaded before.
        </DataNotice>
      )}
      {children}
    </DataCtx.Provider>
  );
}

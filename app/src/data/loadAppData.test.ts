// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { afterEach, describe, expect, it, vi } from "vitest";
import { isCashLike } from "../lib/accounts";
import { coverageFloor, coverageMonths } from "../lib/agg";
import { categoryVocab, SHIPPED_RESERVED } from "../lib/categories";
import { chargeDates } from "../lib/forecast";
import { homeValue, mortgageBalance } from "../lib/lifeSummary";
import { scheduleStatus } from "../lib/schedules";
import { CLASS_COLORS, DEBT_CLASSES } from "../lib/tokens";
import { upcomingSchedules } from "../lib/upcoming";
import { loadAppData } from "./DataContext";
import { EXPORT_FILES, type AppData, type ExportFile } from "./types";

const MANIFEST = {
  schema_version: 15, generated_at: "2026-10-02T10:00:00-05:00", simplefin_fetched_at: null,
  transaction_count: 0,
  current_snapshot_date: null, files: [...EXPORT_FILES],
};
const BODIES: Record<string, unknown> = {
  "manifest.json": MANIFEST,
  "transactions.json": { count: 0, transactions: [] },
  "accounts.json": { accounts: [{ id: "chk", name: "Chk", ledger_drift: null }],
    manual_assets: [{ id: "family-home", name: "Family home", institution: null, owner: "Joint", asset_class: "House",
      liquid: false, acquired: null, values: [{ as_of: "2026-01-01", value: 400_000 }], origin: "app" }],
    manual_overridden: [{ id: "family-home", name: "Family home", institution: "Sample Assessor", owner: "Joint",
      asset_class: "House", liquid: false, acquired: null, values: [{ as_of: "2025-06-01", value: 380_000 }], origin: "config" }] },
  "assets_timeseries.json": { snapshots: [] },
  "holdings.json": { holdings: [{ symbol: "H" }] },
  "cashflow_monthly.json": { months: [{ month: "2026-01", accounts_covered: ["chk"], full_coverage: true, in_coverage: true }],
    cashflow_accounts: ["chk"], coverage_floor: "2026-01" },
  "recurring.json": { items: [{ vendor: "V", schedule: true, expected_dates: ["2026-02-01"], grace_days: 6,
    confidence: 0.9, median_gap_days: 30 }] },
  "quality_report.json": { files: [], checks: [{ name: "c", status: "warn", detail: "d" }],
    account_month_sums: {}, top_unmapped_vendors: [], cross_file_duplicate_uids: {} },
  "open_questions.json": { questions: [{ topic: "t", question: "q", context: "" }], data_gaps: [] },
  "assumptions.json": { projections: {}, category_renames: [] },
  "balances_timeseries.json": { series: { chk: [] }, accounts: {} },
  "networth_daily.json": { series: [{ date: "2026-01-01", networth: 1, coverage: 1, source: "simplefin" }],
    current_total: 1, current_snapshot_date: "2026-01-01" },
  "vendors.json": { count: 1, vendors: [{ vendor: "V" }] },
  "ai_status.json": { configured: true, enabled: true, provider: "anthropic", model: "m", endpoint_host: null,
    policy_version: 1, scope: "anthropic||cards", consent_policy: 1, caps: { vendors_per_run: 50, runs_per_day: 4 }, last_run: null,
    answers_stored: true, applied: { vendors: 2, rows: 5 }, skipped: { low_confidence: 1, stale_category: 0, not_candidate: 0 },
    preview: { vendors: 3, withheld: { name: 1, digits: 0, address: 0, length: 0, seen: 2 } } },
};

function stubFetch(missing: string[], override: Record<string, unknown> = {}) {
  vi.stubGlobal("fetch", async (url: string) => {
    const name = String(url).replace(/^\.\//, "");
    if (missing.includes(name)) return new Response("nope", { status: 404 });
    return new Response(JSON.stringify(name in override ? override[name] : BODIES[name]), { status: 200 });
  });
}
afterEach(() => vi.unstubAllGlobals());

const FATAL: readonly ExportFile[] = ["manifest.json", "transactions.json", "assumptions.json", "assets_timeseries.json"];
type Project = (d: AppData) => unknown;
const body = (f: ExportFile) => BODIES[f] as any;
// where each optional export lands in AppData, the value its stub body must yield, and the
// typed empty that replaces it. The loader maps a rejection to its file by INDEX into
// EXPORT_FILES, so every file is pinned here (a swapped pair would name the wrong file or
// blank the wrong field).
const FIELDS: Partial<Record<ExportFile, Project>> = {
  "accounts.json": (d) => [d.accounts, d.manualAssets, d.manualOverridden],
  "holdings.json": (d) => d.holdings,
  "cashflow_monthly.json": (d) => d.cashflow,
  "recurring.json": (d) => d.recurring,
  "quality_report.json": (d) => d.quality,
  "open_questions.json": (d) => [d.questions, d.dataGaps],
  "balances_timeseries.json": (d) => d.balances,
  "networth_daily.json": (d) => d.networthDaily,
  "vendors.json": (d) => d.vendors,
  "ai_status.json": (d) => d.aiStatus,
};
const REAL: Partial<Record<ExportFile, unknown>> = {
  "accounts.json": [body("accounts.json").accounts, body("accounts.json").manual_assets, body("accounts.json").manual_overridden],
  "holdings.json": body("holdings.json").holdings,
  "cashflow_monthly.json": body("cashflow_monthly.json").months,
  "recurring.json": body("recurring.json").items,
  "quality_report.json": body("quality_report.json"),
  "open_questions.json": [body("open_questions.json").questions, body("open_questions.json").data_gaps],
  "balances_timeseries.json": body("balances_timeseries.json"),
  "networth_daily.json": body("networth_daily.json"),
  "vendors.json": body("vendors.json").vendors,
  "ai_status.json": body("ai_status.json"),
};
const PLACEHOLDERS: Partial<Record<ExportFile, unknown>> = {
  "accounts.json": [[], [], []], "holdings.json": [], "cashflow_monthly.json": [],
  "recurring.json": [],
  "quality_report.json": { files: [], checks: [], account_month_sums: {}, top_unmapped_vendors: [],
    cross_file_duplicate_uids: {} },
  "open_questions.json": [[], []],
  "balances_timeseries.json": { series: {}, accounts: {} },
  "networth_daily.json": { series: [], current_total: null, current_snapshot_date: null },
  "vendors.json": [],
  // an export older than ai_status.json: nothing configured, nothing switched on, nothing filed
  "ai_status.json": { configured: false, enabled: false, provider: "anthropic", model: null, endpoint_host: null,
    policy_version: 0, scope: "", consent_policy: null, caps: { vendors_per_run: 0, runs_per_day: 0 }, last_run: null,
    answers_stored: false, applied: { vendors: 0, rows: 0 }, skipped: { low_confidence: 0, stale_category: 0, not_candidate: 0 },
    preview: { vendors: 0, withheld: { name: 0, digits: 0, address: 0, length: 0, seen: 0 } } },
};

describe("loadAppData", () => {
  it("loads everything and reports nothing missing", async () => {
    stubFetch([]);
    const { data, missing } = await loadAppData();
    expect(missing.size).toBe(0);
    expect(data.recurring).toHaveLength(1);
    expect(data.questions).toHaveLength(1);
    expect(data.networthDaily.series).toHaveLength(1);
    expect(data.vendors).toHaveLength(1);
  });

  it("substitutes typed empties for failed optional exports and names them", async () => {
    stubFetch(["recurring.json", "networth_daily.json", "open_questions.json", "quality_report.json"]);
    const { data, missing } = await loadAppData();
    expect([...missing].sort()).toEqual(
      ["networth_daily.json", "open_questions.json", "quality_report.json", "recurring.json"]);
    expect(data.recurring).toEqual([]);
    expect(data.networthDaily.series).toEqual([]);          // no fabricated points
    expect(data.questions).toEqual([]);
    expect(data.dataGaps).toEqual([]);
    expect(data.quality.checks).toEqual([]);
    expect(data.accounts).toHaveLength(1);                    // the others still loaded
  });

  it.each(["manifest.json", "transactions.json", "assumptions.json", "assets_timeseries.json"])(
    "rejects when the fatal export %s fails", async (file) => {
      stubFetch([file]);
      await expect(loadAppData()).rejects.toThrow(/HTTP 404/);
    });

  it("no manifest (a 404, or a page where it should be) is nothing built yet; any other failure is a failure", async () => {
    const answering = (manifest: () => Response | Promise<Response>) => {
      stubFetch([]);
      const base = globalThis.fetch;
      vi.stubGlobal("fetch", async (url: string) =>
        (String(url).replace(/^\.\//, "") === "manifest.json" ? manifest() : base(url)));
    };
    const outcome = async () => loadAppData().then(() => "loaded", (e: Error) => `${e.name}: ${e.message}`);
    answering(() => new Response("nope", { status: 404 }));
    expect(await outcome()).toBe("NothingBuiltError: manifest.json: HTTP 404");
    answering(() => new Response("\n<!doctype html><html></html>", { status: 200 }));
    expect(await outcome()).toBe("NothingBuiltError: manifest.json: the server answered with a page, not JSON");
    answering(() => new Response("{}", { status: 500 }));
    expect(await outcome()).toBe("Error: manifest.json: HTTP 500");
    answering(() => new Response("{\"schema_version\":", { status: 200 }));
    expect(await outcome()).toMatch(/^Error: manifest\.json: /);
    answering(() => Promise.reject(new TypeError("Failed to fetch")));
    expect(await outcome()).toBe("Error: Failed to fetch");
    // the manifest there and another fatal file absent: a failure (a half-written set is not a first run)
    stubFetch(["transactions.json"]);
    expect(await outcome()).toBe("Error: transactions.json: HTTP 404");
  });

  it.each(EXPORT_FILES.filter((f) => !FATAL.includes(f)))(
    "pins the file<->field mapping: only %s is reported missing", async (gone) => {
      stubFetch([gone]);
      const { data, missing } = await loadAppData();
      expect([...missing]).toEqual([gone]);
      for (const [file, project] of Object.entries(FIELDS) as [ExportFile, Project][]) {
        if (file === gone) expect(project(data), `${file} placeholder`).toEqual(PLACEHOLDERS[file]);
        else expect(project(data), `${file} real body`).toEqual(REAL[file]);
      }
    });
});

// An export set from an OLDER schema can reach this bundle: during a deploy the old image's refresher (a
// "Refresh now", a cron tick) can write exports between the deploy's own ingest and `up -d`, and nothing
// checks manifest.schema_version at runtime. Every key added at schema 4 or later is therefore defaulted on load,
// so an old file degrades to "nothing there" instead of a TypeError above every SectionBoundary (the
// DataProvider memo read assumptions.category_renames bare: a white screen). The keys: schema 4 = recurring
// schedule keys + accounts ledger_drift, schema 5 = assumptions category_renames, schema 6 = transactions'
// parent_id + is_parent, schema 13 = assumptions reserved, schema 14 = the cash-flow months' in_coverage, schema 15 =
// assumptions net_worth and the snapshots' assets_total, liabilities_total, coverage and missing, schema 16 = the
// assumptions' mortgage block on its new keys (an older block is mapped onto them), schema 17 = the projections without
// their own figure for the home (an older export's is dropped unread).
// (vendors.json, schema 3, is a whole optional file: a 404 is already the placeholder pinned above.)
describe("an export set from an older schema degrades, never throws", () => {
  const schema4Assumptions = { projections: {} };                       // no category_renames
  const schema3Item = { vendor: "V", cadence: "monthly", active: true, last_seen: "2026-01-02",
                        next_expected: "2026-02-02", account: "chk" };   // no schedule keys
  const oldAccount = { id: "chk", name: "Chk", txn_data_end: "2026-01-31" };   // no ledger_drift

  it("a schema-4 assumptions.json reads as no category renames", async () => {
    stubFetch([], { "assumptions.json": schema4Assumptions });
    const { data } = await loadAppData();
    expect(data.assumptions.category_renames).toEqual([]);
  });

  it("a schema-12 assumptions.json reads with the shipped reserved names, and the vocabulary files rows under them", async () => {
    stubFetch([], { "assumptions.json": { projections: {}, category_renames: [] } });   // no reserved
    const { data } = await loadAppData();
    expect(data.assumptions.reserved).toEqual(SHIPPED_RESERVED);
    const v = categoryVocab(data.assumptions);
    const S = SHIPPED_RESERVED;
    expect(v.isUncategorizedPile({ category: S.fallback_category, subcategory: S.uncategorized_sub })).toBe(true);
    expect(v.isInternal(S.internal_category)).toBe(true);
    expect(v.income).toBe(S.income_category);
  });

  it("a present reserved block is never overwritten by its default", async () => {
    const reserved = { fallback_category: "Misc", uncategorized_sub: "Unsorted", p2p_unknown_sub: "Person-to-person",
      internal_category: "Moves", income_category: "Earnings", income_other_sub: "Other earnings", fees_category: "Charges" };
    stubFetch([], { "assumptions.json": { projections: {}, category_renames: [], reserved } });
    const { data } = await loadAppData();
    expect(data.assumptions.reserved).toEqual(reserved);
    expect(categoryVocab(data.assumptions).cashMisc).toBe("Misc");
  });

  it("schema-3 recurring items read as unpromoted with no expected dates, and every reader copes", async () => {
    stubFetch([], { "recurring.json": { items: [schema3Item] } });
    const { data } = await loadAppData();
    const r = data.recurring[0];
    expect(r).toMatchObject({ vendor: "V", schedule: false, expected_dates: [] });
    expect(Number.isFinite(r.grace_days) && Number.isFinite(r.confidence) && Number.isFinite(r.median_gap_days)).toBe(true);
    // the readers that went `item.expected_dates.length` on the bare key
    expect(scheduleStatus(r, "2026-03-01", "2026-02-28")).toBe("inactive");
    expect(chargeDates(r, "2026-12")).toEqual([]);
    expect(upcomingSchedules(data.recurring, data.accounts, "2026-03-01", 30)).toEqual([]);
  });

  it("accounts without ledger_drift read as null: the drift card says the check did not run", async () => {
    stubFetch([], { "accounts.json": { accounts: [oldAccount] } });
    const { data } = await loadAppData();
    expect(data.accounts[0].ledger_drift).toBeNull();
    expect(data.accounts[0].txn_data_end).toBe("2026-01-31");           // the rest of the row is untouched
  });

  it("a present value is never overwritten by its default", async () => {
    const renames = [{ from: { category: "A" }, to: { category: "B" } }];
    const drift = { from: "2026-01-01", to: "2026-01-09", expected: 1, got: 1, drift: 0 };
    stubFetch([], {
      "assumptions.json": { projections: {}, category_renames: renames },
      "accounts.json": { accounts: [{ ...oldAccount, ledger_drift: drift }] },
    });
    const { data } = await loadAppData();
    expect(data.assumptions.category_renames).toEqual(renames);
    expect(data.accounts[0].ledger_drift).toEqual(drift);
    expect(data.recurring).toEqual(body("recurring.json").items);         // a schema-5 item comes through as is
  });

  it("a schema-13 cash-flow file reads each month's in_coverage as its full_coverage, and the floor follows", async () => {
    const m = (month: string, full: boolean) => ({ month, income: 1, spend: 1, invested: 0, savings_rate: 0, by_category: {},
      income_by_subcategory: {}, accounts_covered: full ? ["chk", "sav"] : ["chk"], full_coverage: full });   // no in_coverage
    stubFetch([], { "cashflow_monthly.json": { months: [m("2030-01", false), m("2030-02", true), m("2030-03", true)],
      cashflow_accounts: ["chk", "sav"] } });                                                               // no coverage_floor
    const { data } = await loadAppData();
    expect(data.cashflow.map((x) => x.in_coverage)).toEqual([false, true, true]);
    expect(data.cashflow[1]).toMatchObject({ month: "2030-02", accounts_covered: ["chk", "sav"], full_coverage: true });
    expect(coverageMonths(data.cashflow).map((x) => x.month)).toEqual(["2030-02", "2030-03"]);
    expect(coverageFloor(data.cashflow)).toBe("2030-02");
  });

  it("a cash-flow file without its account list loads, and present flags are never overwritten", async () => {
    const month = { month: "2030-01", accounts_covered: [], full_coverage: true, in_coverage: false };
    stubFetch([], { "cashflow_monthly.json": { months: [month] } });
    const { data } = await loadAppData();
    expect(data.cashflow.map((m) => m.month)).toEqual(["2030-01"]);
    expect(data.cashflow[0].in_coverage, "the export's own flag, not full_coverage").toBe(false);
  });

  const row = (account: string, asset_class: string, value: number) => ({ account, account_id: null,
    institution: "Sample Bank", owner: "Alex", asset_class, value, liquid: value > 0, as_of: "2030-01-10",
    as_of_assumed: false, needs_review: false, source: "stated" });
  const schema14Snapshot = { snapshot_date: "2030-01-10", source: "stated", total: -300.1, total_excl_house_car: -300.1,
    mortgage_included: true, liquid_total: 1000.2, by_owner: { Alex: -300.1 }, by_class: { Cash: 1000.2, Mortgage: -1300.3 },
    rows: [row("Sample Loan", "Mortgage", -1300.3), row("Sample Checking", "Cash", 1000.1), row("Sample Cash", "Cash", 0.1)] };
  /** a snapshot of an older export as the loader gives it: its rows with the schema-20 keys at their defaults */
  const atSchema20 = <S extends { rows: object[] }>(s: S) =>
    ({ ...s, rows: s.rows.map((r) => ({ ...r, manual_id: null, carried_back: false })) });

  it("a schema-14 export reads with cards not counted and each snapshot's roll-ups summed from its rows", async () => {
    stubFetch([], { "assets_timeseries.json": { snapshots: [schema14Snapshot] },
      "assumptions.json": { projections: {}, category_renames: [], reserved: SHIPPED_RESERVED } });   // no net_worth
    const { data } = await loadAppData();
    expect(data.assumptions.net_worth).toEqual({ include_credit_cards: false });
    const s = data.assets.snapshots[0];
    expect([s.assets_total, s.liabilities_total, s.coverage, s.missing]).toEqual([1000.2, -1300.3, 1, []]);
    expect(Math.round((s.assets_total + s.liabilities_total) * 100) / 100).toBe(s.total);
    expect(s.rows).toEqual(atSchema20(schema14Snapshot).rows);         // the rest of the snapshot is untouched
  });

  it("a schema-15 export's net_worth and snapshot keys are never overwritten by their defaults", async () => {
    const current = { ...schema14Snapshot, assets_total: 1000.2, liabilities_total: -1300.3, coverage: 0.667,
      missing: ["Sample Savings"] };
    stubFetch([], { "assets_timeseries.json": { snapshots: [current] },
      "assumptions.json": { projections: {}, category_renames: [], reserved: SHIPPED_RESERVED,
        net_worth: { include_credit_cards: true } } });
    const { data } = await loadAppData();
    expect(data.assumptions.net_worth).toEqual({ include_credit_cards: true });
    expect(data.assets.snapshots[0]).toEqual(atSchema20(current));
    expect(data.partialSnapshots).toEqual([]);
  });

  const v15Mortgage = { latest_balance: 250_000, latest_balance_as_of: "2030-05-18", estimated_apr_pct: null,
    typical_payment: null, note_rate_pct: 6.5, monthly_pi: 2_000, monthly_escrow: 450, escrow_balance: 2_100,
    account_suffix: "0000", schedule: [{ date: "2030-04-30", description: "PAYMENT", amount: -2_000, principal_balance: 250_600 }] };

  it("a schema-15 mortgage block reads on the schema-16 keys, its account the current snapshot's Mortgage row", async () => {
    const loanRow = { ...row("Sample Loan", "Mortgage", -250_000), account_id: "demo_loan_0000", source: "simplefin_balance" };
    const older = { ...schema14Snapshot, snapshot_date: "2030-01-31", rows: [{ ...loanRow, account_id: "demo_other_0000" }] };
    stubFetch([], { "assets_timeseries.json": { snapshots: [older, { ...schema14Snapshot, rows: [row("Sample Cash", "Cash", 1), loanRow] }] },
      "assumptions.json": { projections: {}, category_renames: [], reserved: SHIPPED_RESERVED, mortgage: v15Mortgage } });
    const { data } = await loadAppData();
    expect(data.assumptions.mortgage).toEqual({ account_id: "demo_loan_0000", balance: 250_000, balance_as_of: "2030-05-18",
      rate_pct: 6.5, monthly_payment: 2_000, monthly_escrow: 450, escrow_balance: 2_100, origination: null, term_months: null,
      schedule: [{ date: "2030-04-30", principal_balance: 250_600 }] });
    expect(mortgageBalance(data.assumptions)).toEqual({ balance: 250_000, asOf: "2030-05-18" });
  });

  it("a schema-15 block with no account row and no terms names no account and keeps every term null", async () => {
    stubFetch([], { "assumptions.json": { projections: {}, category_renames: [], mortgage: {
      latest_balance: null, latest_balance_as_of: null, estimated_apr_pct: null, typical_payment: null, schedule: [] } } });
    const { data } = await loadAppData();
    expect(data.assumptions.mortgage).toEqual({ account_id: "", balance: null, balance_as_of: null, rate_pct: null,
      monthly_payment: null, monthly_escrow: null, escrow_balance: null, origination: null, term_months: null, schedule: [] });
  });

  it("a schema-16 block and a null one come through as they are", async () => {
    const block = { account_id: "demo_loan_0000", balance: 0, balance_as_of: "2030-05-18", rate_pct: 6.5, monthly_payment: 2_000,
      monthly_escrow: null, escrow_balance: null, origination: { date: "2030-01-15", amount: 250_000 }, term_months: 360,
      schedule: [{ date: "2030-05-18", principal_balance: 0 }] };
    stubFetch([], { "assumptions.json": { projections: {}, category_renames: [], mortgage: block } });
    expect((await loadAppData()).data.assumptions.mortgage).toEqual(block);
    stubFetch([], { "assumptions.json": { projections: {}, category_renames: [], mortgage: null } });
    expect((await loadAppData()).data.assumptions.mortgage).toBeNull();
  });

  it("a schema-16 export's projections lose their own figure for the home; the home's value is the House rows", async () => {
    const projections = { return_scenarios_pct: [5, 7, 9], inflation_pct: 2.75, withdrawal_rate_pct: 4, ltcg_pct: 15,
      state_income_tax_pct: 0 };
    const home = { ...row("Sample Home", "House", 500_000), liquid: false };
    stubFetch([], { "assets_timeseries.json": { snapshots: [{ ...schema14Snapshot, rows: [...schema14Snapshot.rows, home] }] },
      "assumptions.json": { projections: { ...projections, home_value_estimate: 750_000 }, category_renames: [] } });
    const { data } = await loadAppData();
    expect(data.assumptions.projections).toEqual(projections);                // the rest of the block is untouched
    expect(homeValue(data.assets.snapshots[0]), "the House row, never the dropped figure").toBe(500_000);
    stubFetch([], { "assumptions.json": { projections, category_renames: [] } });
    expect((await loadAppData()).data.assumptions.projections, "a schema-17 block comes through as it is").toEqual(projections);
  });

  it("the pages get the snapshots comparable with the newest; the earlier partial month ends are set aside", async () => {
    const snap = (snapshot_date: string, missing: string[], mortgage_included = true) => ({ ...schema14Snapshot,
      snapshot_date, assets_total: 1000.2, liabilities_total: -1300.3, coverage: 1, missing, mortgage_included });
    const partial = [snap("2030-01-31", ["Sample Brokerage", "Sample Loan"], false), snap("2030-02-28", ["Sample Brokerage"])];
    const comparable = [snap("2030-03-31", ["Sample Loan"], false), snap("2030-04-30", []), snap("2030-05-10", [])];
    stubFetch([], { "assets_timeseries.json": { snapshots: [...partial, ...comparable] } });
    const { data } = await loadAppData();
    expect(data.assets.snapshots).toEqual(comparable.map(atSchema20));
    expect(data.partialSnapshots).toEqual(partial.map(atSchema20));
  });

  it("an older export's snapshots (no `missing`) are all kept, a pre-mortgage one included", async () => {
    const older = [{ ...schema14Snapshot, snapshot_date: "2029-12-31", mortgage_included: false }, schema14Snapshot];
    stubFetch([], { "assets_timeseries.json": { snapshots: older } });
    const { data } = await loadAppData();
    expect(data.assets.snapshots.map((s) => s.snapshot_date)).toEqual(["2029-12-31", "2030-01-10"]);
    expect(data.partialSnapshots).toEqual([]);
  });

  it("schema-5 transaction rows read as ordinary rows: parent_id null, is_parent false", async () => {
    const schema5Row = { id: "t1", date: "2026-01-02", account: "chk", amount: -10, type: "purchase",
                         category: "Dining", vendor: "V", tags: [], needs_review: false };   // no split keys
    stubFetch([], { "transactions.json": { count: 1, transactions: [schema5Row] } });
    const { data } = await loadAppData();
    expect(data.transactions[0]).toMatchObject({ id: "t1", parent_id: null, is_parent: false, vendor: "V" });
    expect(data.purchases).toBe(data.transactions);           // no parent: one array, both views
  });

  it("present split keys are never overwritten by their defaults", async () => {
    const parent = { id: "p", date: "2026-01-02", account: "chk", amount: -30, type: "purchase", is_parent: true, parent_id: null };
    const child = { id: "c", date: "2026-01-02", account: "chk", amount: -30, type: "purchase", is_parent: false, parent_id: "p" };
    stubFetch([], { "transactions.json": { count: 2, transactions: [parent, child] } });
    const { data } = await loadAppData();
    expect(data.transactions.map((t) => [t.id, t.parent_id, t.is_parent])).toEqual([["c", "p", false]]);   // the category view: the part
    expect(data.purchases.map((t) => t.id)).toEqual(["p"]);                                              // the vendor view: the parent
    expect([...data.splitParents.keys()]).toEqual(["p"]);
  });
});

// Schema 19 added the manifest's setup, the household's name and zone, the modules, the stated projection keys and the
// class list to the exports, and resolved every account's class; a schema-18 set reads each as "not said".
describe("a schema-18 export reads every schema-19 key at its default", () => {
  const manifest18 = { ...MANIFEST, schema_version: 18 };                                  // no setup
  const household18 = { people: [{ name: "Alex", role: "owner" }], margin_apr: null, retirement_accounts: {},
    margin_account: null };                                                                 // no name, no timezone
  const assumptions18 = { projections: {}, category_renames: [], reserved: SHIPPED_RESERVED,
    net_worth: { include_credit_cards: true }, household: household18, mortgage: null };     // no modules, no lists
  const account18 = (id: string, kind: string, asset_class: string | null) => ({ id, name: id, institution: "Sample Bank",
    owner: "Alex", kind, asset_class, txn_data_start: null, txn_data_end: null, latest_balance: null,
    latest_balance_as_of: null, ledger_drift: null });
  const load18 = (over: Record<string, unknown> = {}) => {
    stubFetch([], { "manifest.json": manifest18, "assumptions.json": assumptions18, ...over });
    return loadAppData();
  };

  it("manifest.setup counts the accounts, reads a fetched feed as configured, and counts no file or manual asset", async () => {
    expect((await load18()).data.manifest.setup).toEqual({ accounts: 1, feed: false, import_files: 0, manual_assets: 0 });
    const fetched = { ...manifest18, simplefin_fetched_at: "2026-10-02T15:00:00Z" };
    const { data } = await load18({ "manifest.json": fetched, "accounts.json": { accounts: [] } });
    expect(data.manifest.setup).toEqual({ accounts: 0, feed: true, import_files: 0, manual_assets: 0 });
    expect(data.manifest.simplefin_fetched_at).toBe(fetched.simplefin_fetched_at);    // the rest of the manifest is untouched
  });

  it("manifest.setup counts the newest snapshot's hand-stated rows as the manual assets", async () => {
    const stated = (account: string, asset_class: string, value: number) => ({ account, account_id: null,
      institution: "Sample Assessor", owner: "Joint", asset_class, value, liquid: false, as_of: "2030-01-10",
      as_of_assumed: false, needs_review: false, source: "stated" });
    const fed = { ...stated("Sample Checking", "Cash", 1000), account_id: "demo_checking_0000", liquid: true,
      source: "simplefin_balance" };
    const snapshot = (snapshot_date: string, rows: unknown[]) => ({ snapshot_date, source: "stated", total: 0,
      total_excl_house_car: 0, mortgage_included: true, liquid_total: 0, by_owner: {}, by_class: {}, rows });
    const { data } = await load18({ "accounts.json": { accounts: [] }, "assets_timeseries.json": { snapshots: [
      snapshot("2030-01-10", [stated("Sample Home", "House", 400_000), stated("Sample Car", "Car", 20_000)]),
      snapshot("2030-02-10", [stated("Sample Home", "House", 400_000), fed])] } });
    expect(data.manifest.setup).toEqual({ accounts: 0, feed: false, import_files: 0, manual_assets: 1 });
    expect((await load18()).data.manifest.setup.manual_assets, "no snapshot, no manual asset").toBe(0);
  });

  it("networth_daily's total reads as unknown with no snapshot, never the 0 an older engine summed from no rows", async () => {
    const none = await load18({ "networth_daily.json": { series: [], current_total: 0, current_snapshot_date: null } });
    expect(none.data.networthDaily.current_total).toBeNull();
    const dated = await load18({ "networth_daily.json": { series: [], current_total: 0, current_snapshot_date: "2030-01-10" } });
    expect(dated.data.networthDaily.current_total, "a real 0 on a snapshot's day stays 0").toBe(0);
  });

  it("the household's name reads as none stated (empty) and its zone as unknown; its people are kept", async () => {
    const { household } = (await load18()).data.assumptions;
    expect([household.name, household.timezone]).toEqual(["", null]);
    expect(household.people).toEqual(household18.people);
  });

  it("modules read as none stated", async () => {
    expect((await load18()).data.assumptions.modules).toEqual({});
  });

  it("projections_stated reads as none stated: every projection is a default", async () => {
    expect((await load18()).data.assumptions.projections_stated).toEqual([]);
  });

  it("asset_classes read as the dashboard's own list, in its own order", async () => {
    const classes = (await load18()).data.assumptions.asset_classes;
    expect(classes).toEqual({
      assets: ["Cash", "Stocks/ETF", "Retirement", "Crypto", "Precious Metals", "Real Estate/Venture", "House", "Car",
        "529", "Private Stock"],
      liabilities: ["Mortgage", "Margin", "Other Debt"] });
    // every class the palette colours, each once, and the debts are the ones plotted as debt bands
    expect([...classes.assets, ...classes.liabilities].sort()).toEqual(Object.keys(CLASS_COLORS).sort());
    expect(classes.liabilities).toEqual([...DEBT_CLASSES]);
  });

  const mortgage18 = { account_id: "demo_mortgage_0000", balance: null, balance_as_of: null, rate_pct: null,
    monthly_payment: null, monthly_escrow: null, escrow_balance: null, origination: null, term_months: null, schedule: [] };
  const balance18 = (kind: string, asset_class: string | null) => ({ name: kind, institution: "Sample Bank", owner: "Alex",
    kind, asset_class });

  it("an account's null class reads as the one the engine gives it: its kind's, Mortgage for the mortgage loan", async () => {
    const { data } = await load18({
      "assumptions.json": { ...assumptions18, mortgage: mortgage18 },
      "accounts.json": { accounts: [account18("demo_checking_0000", "checking", null),
        account18("demo_brokerage_0000", "brokerage", null), account18("demo_retirement_0000", "brokerage", "Retirement"),
        account18("demo_card_0000", "credit-card", null), account18("demo_loan_0000", "loan", null),
        account18("demo_mortgage_0000", "loan", null)] } });
    expect(Object.fromEntries(data.accounts.map((a) => [a.id, a.asset_class]))).toEqual({
      demo_checking_0000: "Cash", demo_brokerage_0000: "Stocks/ETF", demo_retirement_0000: "Retirement",
      demo_card_0000: "Other Debt", demo_loan_0000: "Other Debt", demo_mortgage_0000: "Mortgage" });
  });

  it("a balance-history account's null class reads as the engine's too, so a checking account on its kind's class stays cash", async () => {
    const { data } = await load18({
      "assumptions.json": { ...assumptions18, mortgage: mortgage18 },
      "balances_timeseries.json": { series: {}, accounts: {
        demo_checking_0000: balance18("checking", null), demo_savings_0000: balance18("savings", null),
        demo_brokerage_0000: balance18("brokerage", null), demo_retirement_0000: balance18("brokerage", "Retirement"),
        demo_card_0000: balance18("credit-card", null), demo_mortgage_0000: balance18("loan", null) } } });
    const accounts = data.balances.accounts;
    expect(Object.fromEntries(Object.entries(accounts).map(([id, a]) => [id, a.asset_class]))).toEqual({
      demo_checking_0000: "Cash", demo_savings_0000: "Cash", demo_brokerage_0000: "Stocks/ETF",
      demo_retirement_0000: "Retirement", demo_card_0000: "Other Debt", demo_mortgage_0000: "Mortgage" });
    expect(Object.keys(accounts).filter((id) => isCashLike(accounts[id])), "the cash accounts Balances groups")
      .toEqual(["demo_checking_0000", "demo_savings_0000"]);
    expect(accounts.demo_checking_0000.name).toBe("checking");                // the rest of the entry is kept
  });

  it("present schema-19 keys are never overwritten by their defaults", async () => {
    const setup = { accounts: 3, feed: true, import_files: 0, manual_assets: 1 };
    const classes = { assets: ["Cash", "Retirement"], liabilities: ["Other Debt"] };
    const { data } = await load18({
      "manifest.json": { ...manifest18, schema_version: 19, setup },
      "assumptions.json": { ...assumptions18, household: { ...household18, name: "The Sample household",
        timezone: "America/New_York" }, modules: { home_buying: false }, projections_stated: ["ltcg_pct"],
        asset_classes: classes },
      "accounts.json": { accounts: [account18("demo_savings_0000", "savings", "Retirement")] },
      "balances_timeseries.json": { series: {}, accounts: { demo_savings_0000: balance18("savings", "Retirement") } },
      "networth_daily.json": { series: [], current_total: null, current_snapshot_date: null } });
    expect(data.manifest.setup).toEqual(setup);
    const a = data.assumptions;
    expect([a.household.name, a.household.timezone]).toEqual(["The Sample household", "America/New_York"]);
    expect([a.modules, a.projections_stated, a.asset_classes]).toEqual([{ home_buying: false }, ["ltcg_pct"], classes]);
    expect(data.accounts[0].asset_class).toBe("Retirement");
    expect(data.balances.accounts.demo_savings_0000.asset_class).toBe("Retirement");
    expect(data.networthDaily.current_total).toBeNull();
  });
});

// Schema 20 added the manual assets to accounts.json and, to each snapshot row, the manual asset it is and whether it
// is carried back before that asset's first value; a schema-19 set reads each as "none" / "not carried back".
describe("a schema-19 export reads every schema-20 key at its default", () => {
  const manifest19 = { ...MANIFEST, schema_version: 19, setup: { accounts: 1, feed: false, import_files: 0, manual_assets: 1 } };
  const row19 = { account: "Family home", account_id: null, institution: "Sample Assessor", owner: "Joint",
    asset_class: "House", value: 400_000, liquid: false, as_of: "2030-01-01", as_of_assumed: false, needs_review: false,
    source: "stated" };                                                                    // no manual_id, no carried_back
  const snapshot19 = { snapshot_date: "2030-01-31", source: "stated", total: 401_000, assets_total: 401_000,
    liabilities_total: 0, total_excl_house_car: 1_000, mortgage_included: true, liquid_total: 1_000,
    by_owner: { Joint: 400_000, Alex: 1_000 }, by_class: { House: 400_000, Cash: 1_000 }, coverage: 1, missing: [],
    rows: [row19, { ...row19, account: "Sample Checking", account_id: "demo_checking_0000", institution: "Sample Bank",
      owner: "Alex", asset_class: "Cash", value: 1_000, liquid: true, source: "bank_balance" }] };
  const load19 = (over: Record<string, unknown> = {}) => {
    stubFetch([], { "manifest.json": manifest19, "assets_timeseries.json": { snapshots: [snapshot19] }, ...over });
    return loadAppData();
  };

  it("accounts.json without manual_assets reads as no manual asset; its accounts are kept", async () => {
    const { data } = await load19({ "accounts.json": { accounts: [{ id: "chk", name: "Chk", ledger_drift: null }] } });
    expect(data.manualAssets).toEqual([]);
    expect(data.accounts.map((a) => a.id)).toEqual(["chk"]);
    expect(data.manifest.setup.manual_assets, "the manifest's own count is kept").toBe(1);
  });

  it("a snapshot row without manual_id reads as naming no manual asset, stated or not", async () => {
    const { data } = await load19();
    expect(data.assets.snapshots[0].rows.map((r) => r.manual_id)).toEqual([null, null]);
  });

  it("a snapshot row without carried_back reads as not carried back", async () => {
    const { data } = await load19();
    expect(data.assets.snapshots[0].rows.map((r) => r.carried_back)).toEqual([false, false]);
    expect(data.assets.snapshots[0].rows[0]).toMatchObject(row19);             // the rest of the row is untouched
  });

  it("accounts.json without manual_overridden (any export before schema 21) reads as no config entry hidden", async () => {
    const { data } = await load19({ "accounts.json": { accounts: [], manual_assets: body("accounts.json").manual_assets } });
    expect(data.manualOverridden).toEqual([]);
    expect(data.manualAssets).toEqual(body("accounts.json").manual_assets);
  });

  it("present schema-20 keys are never overwritten by their defaults", async () => {
    const manual = body("accounts.json").manual_assets;
    const { data } = await load19({
      "accounts.json": { accounts: [], manual_assets: manual },
      "assets_timeseries.json": { snapshots: [{ ...snapshot19,
        rows: [{ ...row19, manual_id: "family-home", carried_back: true }] }] } });
    expect(data.manualAssets).toEqual(manual);
    expect(data.assets.snapshots[0].rows[0]).toMatchObject({ manual_id: "family-home", carried_back: true });
  });
});

// Schema 22 added to each of the quality report's files the account it was read for; a schema-21 set reads each file
// as naming no account.
describe("a schema-21 export reads every schema-22 key at its default", () => {
  const file21 = { source_file: "checking-2030-01.csv", parser: "chase_checking", rows_total: 3, rows_parsed: 3,
    rows_skipped: 0, skip_reasons: {}, duplicates_removed: 0, date_min: "2030-01-02", date_max: "2030-01-30",
    parsed_sum: 0 };                                                                           // no account
  const report = (files: object[]) => ({ ...body("quality_report.json"), files });

  it("a file without account reads as naming no account; the rest of the report is untouched", async () => {
    stubFetch([], { "quality_report.json": report([file21]) });
    const { data } = await loadAppData();
    expect(data.quality.files).toEqual([{ ...file21, account: null }]);
    expect(data.quality.checks).toEqual(body("quality_report.json").checks);
  });

  it("a present account is never overwritten by its default", async () => {
    stubFetch([], { "quality_report.json": report([{ ...file21, account: "chk" },
      { ...file21, source_file: "accounts-20300131T210000Z.json", parser: "simplefin", account: null }]) });
    const { data } = await loadAppData();
    expect(data.quality.files.map((f) => f.account)).toEqual(["chk", null]);
  });
});

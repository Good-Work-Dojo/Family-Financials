// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { afterEach, describe, expect, it, vi } from "vitest";
import { coverageFloor, coverageMonths } from "../lib/agg";
import { loadAppData, OlderExportsError } from "./DataContext";
import { EXPECTED_SCHEMA_VERSION, EXPORT_FILES, MIN_SCHEMA_VERSION, type AppData, type ExportFile } from "./types";

const MANIFEST = {
  schema_version: EXPECTED_SCHEMA_VERSION, generated_at: "2026-10-02T10:00:00-05:00", simplefin_fetched_at: null,
  transaction_count: 0, current_snapshot_date: null, files: [...EXPORT_FILES],
  setup: { accounts: 1, feed: false, import_files: 1, manual_assets: 1 },
};
const BODIES: Record<string, unknown> = {
  "manifest.json": MANIFEST,
  "transactions.json": { count: 0, transactions: [] },
  "accounts.json": { accounts: [{ id: "chk", name: "Chk", kind: "checking", asset_class: "Cash", ledger_drift: null }],
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
  "assumptions.json": { projections: {}, category_renames: [], mortgage: null },
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
  // a status that could not be read: nothing configured, nothing switched on, nothing filed
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
    expect(data.manifest).toEqual(MANIFEST);
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

// An export set OLDER than the oldest this bundle reads can reach it: during a deploy the old image's refresher (a
// "Refresh now", a cron tick) can write exports between the deploy's own ingest and `up -d`. Such a set is not read at
// all (its keys would be read bare, a TypeError above every SectionBoundary): the loader refuses it, and the provider
// shows the reload notice until the next refresh rewrites it (DataContext.test.tsx).
describe("an export set older than MIN_SCHEMA_VERSION", () => {
  it("is refused as older exports, naming its version and the oldest this dashboard reads", async () => {
    stubFetch([], { "manifest.json": { ...MANIFEST, schema_version: MIN_SCHEMA_VERSION - 1 } });
    const refused = await loadAppData().catch((e: unknown) => e);
    expect(refused).toBeInstanceOf(OlderExportsError);
    expect((refused as Error).message).toBe(
      `the exports were written at schema ${MIN_SCHEMA_VERSION - 1}; this dashboard reads schema ${MIN_SCHEMA_VERSION} and later`);
  });
  it("a manifest that states no version is refused the same way", async () => {
    const { schema_version: _none, ...unversioned } = MANIFEST;
    stubFetch([], { "manifest.json": unversioned });
    await expect(loadAppData()).rejects.toBeInstanceOf(OlderExportsError);
  });
  it("the oldest it reads, and a newer one, load (a newer one under the reload banner: DataContext.test.tsx)", async () => {
    for (const v of [MIN_SCHEMA_VERSION, EXPECTED_SCHEMA_VERSION + 1]) {
      stubFetch([], { "manifest.json": { ...MANIFEST, schema_version: v } });
      expect((await loadAppData()).data.manifest.schema_version).toBe(v);
    }
  });
});

describe("what the loader derives from a current export set", () => {
  it("the cash-flow floor is read from the months' own flags, never the file's key or its account list", async () => {
    const m = (month: string, inCoverage: boolean) => ({ month, income: 1, spend: 1, invested: 0, savings_rate: 0,
      by_category: {}, income_by_subcategory: {}, accounts_covered: ["chk"], full_coverage: true, in_coverage: inCoverage });
    stubFetch([], { "cashflow_monthly.json": { months: [m("2030-01", false), m("2030-02", true), m("2030-03", true)],
      cashflow_accounts: [], coverage_floor: "2030-01" } });
    const { data } = await loadAppData();
    expect(coverageMonths(data.cashflow).map((x) => x.month)).toEqual(["2030-02", "2030-03"]);
    expect(coverageFloor(data.cashflow)).toBe("2030-02");
  });

  const row = (account: string, asset_class: string, value: number) => ({ account, account_id: null,
    institution: "Sample Bank", owner: "Alex", asset_class, value, liquid: value > 0, as_of: "2030-01-10",
    as_of_assumed: false, needs_review: false, source: "stated", manual_id: null, carried_back: false });
  const snap = (snapshot_date: string, missing: string[], mortgage_included = true) => ({ snapshot_date, source: "stated",
    total: -300.1, total_excl_house_car: -300.1, mortgage_included, liquid_total: 1000.2, by_owner: { Alex: -300.1 },
    by_class: { Cash: 1000.2, Mortgage: -1300.3 }, assets_total: 1000.2, liabilities_total: -1300.3, coverage: 1, missing,
    rows: [row("Sample Loan", "Mortgage", -1300.3), row("Sample Checking", "Cash", 1000.1), row("Sample Cash", "Cash", 0.1)] });

  it("the pages get the snapshots comparable with the newest; the earlier partial month ends are set aside", async () => {
    const partial = [snap("2030-01-31", ["Sample Brokerage", "Sample Loan"], false), snap("2030-02-28", ["Sample Brokerage"])];
    const comparable = [snap("2030-03-31", ["Sample Loan"], false), snap("2030-04-30", []), snap("2030-05-10", [])];
    stubFetch([], { "assets_timeseries.json": { snapshots: [...partial, ...comparable] } });
    const { data } = await loadAppData();
    expect(data.assets.snapshots).toEqual(comparable);
    expect(data.partialSnapshots).toEqual(partial);
  });

  it("split rows: the category view holds the parts, the vendor view the parent", async () => {
    const parent = { id: "p", date: "2026-01-02", account: "chk", amount: -30, type: "purchase", is_parent: true, parent_id: null };
    const child = { id: "c", date: "2026-01-02", account: "chk", amount: -30, type: "purchase", is_parent: false, parent_id: "p" };
    const plain = { id: "o", date: "2026-01-03", account: "chk", amount: -10, type: "purchase", is_parent: false, parent_id: null };
    stubFetch([], { "transactions.json": { count: 2, transactions: [parent, child, plain] } });
    const { data } = await loadAppData();
    expect(data.transactions.map((t) => [t.id, t.parent_id, t.is_parent])).toEqual([["c", "p", false], ["o", null, false]]);
    expect(data.purchases.map((t) => t.id)).toEqual(["p", "o"]);
    expect([...data.splitParents.keys()]).toEqual(["p"]);
  });
});

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Contract test: the ETL exports must parse against schemas.ts exactly.
// Skips when the exports (test/exports.ts: FF_EXPORTS_DIR, else data/exports) are absent (fresh checkout / CI
// without exports), mirroring tests/test_e2e.py's source-exists guard on the Python side; FF_REQUIRE_EXPORTS=1 turns
// the absence into an error.
import { describe, expect, it } from "vitest";
import { coverageFloor } from "../lib/agg";
import { HAVE_EXPORTS, readExport, requireExports } from "../test/exports";
import { FILE_SCHEMAS } from "./schemas";
import { EXPECTED_SCHEMA_VERSION } from "./types";

requireExports();

describe.skipIf(!HAVE_EXPORTS)("ETL export contract", () => {
  for (const [file, schema] of Object.entries(FILE_SCHEMAS)) {
    it(`${file} matches its schema`, () => {
      const raw = readExport(file);
      const result = schema.safeParse(raw);
      if (!result.success) {
        // surface the first few issues with paths — enough to locate the drift
        const issues = result.error.issues.slice(0, 5)
          .map((i) => `${i.path.join(".")}: ${i.message}`).join("\n");
        expect.fail(`${file} drifted from schemas.ts:\n${issues}`);
      }
    });
  }

  it("manifest lists exactly the schema'd files", () => {
    const manifest = readExport("manifest.json");
    expect([...manifest.files].sort()).toEqual(Object.keys(FILE_SCHEMAS).sort());
  });

  it("manifest.schema_version is the version this app was built against", () => {
    const manifest = readExport("manifest.json");
    expect(manifest.schema_version).toBe(EXPECTED_SCHEMA_VERSION);
  });

  // The schemas take any owner string (the household is config), so membership is checked here: every owner a row
  // carries is one assumptions.owners lists, which the dashboard's filter and colours go by. A failure prints field
  // names and counts only, never an owner: CI logs are not a place for the household's names.
  const read = (file: string) => readExport(file);
  const ownerOf = (r: { owner: unknown }) => r.owner;

  it("every owner on a transaction, account, manual asset, asset row and holding is in assumptions.owners", () => {
    const known = new Set<unknown>(read("assumptions.json").owners);
    const fields: Record<string, unknown[]> = {
      "transactions.json transactions[].owner": read("transactions.json").transactions.map(ownerOf),
      "accounts.json accounts[].owner": read("accounts.json").accounts.map(ownerOf),
      "accounts.json manual_assets[].owner": read("accounts.json").manual_assets.map(ownerOf),
      "accounts.json manual_overridden[].owner": read("accounts.json").manual_overridden.map(ownerOf),
      "assets_timeseries.json snapshots[].rows[].owner": read("assets_timeseries.json").snapshots
        .flatMap((s: { rows: { owner: unknown }[] }) => s.rows.map(ownerOf)),
      "holdings.json holdings[].owner": read("holdings.json").holdings.map(ownerOf),
    };
    const checked = Object.values(fields).reduce((n, v) => n + v.length, 0);
    // The control guards against a vacuous pass: a household with any transaction or any account has rows whose
    // owners this check reads, so exports where those rows came out missing pass nothing unchecked. A household with
    // no account and no transaction (the empty demo profile) has nothing to check and is exempt.
    const hasRows = read("manifest.json").transaction_count > 0 || read("accounts.json").accounts.length > 0;
    if (hasRows) expect(checked > 0, "the control: the exports carry owners to check").toBe(true);
    const unknown = Object.fromEntries(Object.entries(fields)
      .map(([field, owners]) => [field, owners.filter((o) => !known.has(o)).length] as const)
      .filter(([, n]) => n > 0));
    expect(unknown, "rows whose owner assumptions.owners does not list, counted per field").toEqual({});
  });

  it("assumptions.owners lists each owner once, and every household person is one of them", () => {
    const asm = read("assumptions.json");
    const owners: unknown[] = asm.owners;
    expect(owners.length - new Set(owners).size, "names assumptions.owners repeats").toBe(0);
    const absent = asm.household.people.filter((p: { name: unknown }) => !owners.includes(p.name)).length;
    expect(absent, "household.people[] whose name assumptions.owners does not list").toBe(0);
  });

  // What the schemas cannot say alone: the setup counts the accounts accounts.json lists, every account's class is one
  // the class list names and the balance history gives it the same class, the current total is unknown exactly when
  // there is no snapshot, and the zone is stated. Boolean assertions: a failure names the rule, never a count, a class
  // or a figure.
  it("setup, the account classes, the current total and the zone agree with the files they summarise", () => {
    const manifest = read("manifest.json");
    const accounts: { id: unknown; asset_class: unknown }[] = read("accounts.json").accounts;
    const asm = read("assumptions.json");
    const daily = read("networth_daily.json");
    expect(manifest.setup.accounts === accounts.length, "setup.accounts is the number of accounts listed").toBe(true);
    const classes = new Set<unknown>([...asm.asset_classes.assets, ...asm.asset_classes.liabilities]);
    expect(accounts.every((a) => classes.has(a.asset_class)), "every account's class is in asset_classes").toBe(true);
    const classOf = new Map(accounts.map((a) => [a.id, a.asset_class]));
    const history: Record<string, { asset_class: unknown }> = read("balances_timeseries.json").accounts;
    expect(Object.entries(history).every(([id, a]) => classOf.get(id) === a.asset_class),
      "the balance history gives each account the class accounts.json gives it").toBe(true);
    expect((daily.current_total === null) === (daily.current_snapshot_date === null),
      "current_total is null exactly when there is no current snapshot").toBe(true);
    expect(daily.current_snapshot_date === manifest.current_snapshot_date, "the two files name one snapshot").toBe(true);
    expect(typeof asm.household.timezone === "string", "the household's zone is stated").toBe(true);
  });

  // The manual assets the schemas take as any strings: the setup counts the list, each id is listed once under a known
  // class, and a snapshot row names one exactly when it is a stated row, one the list holds; only such a row is ever
  // carried back. Boolean assertions: a failure names the rule, never an id, a name or a figure.
  it("the manual assets: counted in setup, each once in a known class, and named by exactly the stated rows", () => {
    const manual: { id: unknown; asset_class: unknown }[] = read("accounts.json").manual_assets;
    const asm = read("assumptions.json");
    expect(read("manifest.json").setup.manual_assets === manual.length, "setup.manual_assets is the number listed").toBe(true);
    const ids = new Set(manual.map((a) => a.id));
    expect(ids.size === manual.length, "each manual asset's id is listed once").toBe(true);
    const classes = new Set<unknown>([...asm.asset_classes.assets, ...asm.asset_classes.liabilities]);
    expect(manual.every((a) => classes.has(a.asset_class)), "every manual asset's class is in asset_classes").toBe(true);
    const rows: { source: unknown; manual_id: unknown; carried_back: unknown }[] = read("assets_timeseries.json").snapshots
      .flatMap((s: { rows: unknown[] }) => s.rows);
    expect(rows.every((r) => (r.source === "stated") === (r.manual_id !== null)), "a row names a manual asset exactly when it is stated").toBe(true);
    expect(rows.every((r) => r.manual_id === null || ids.has(r.manual_id)), "every manual asset a row names is listed").toBe(true);
    expect(rows.every((r) => r.carried_back === false || r.manual_id !== null), "only a manual asset's row is carried back").toBe(true);
    // the config entries an app asset hides: each from the config, each hidden by a counted app asset of its id
    const hidden: { id: unknown; origin: unknown }[] = read("accounts.json").manual_overridden;
    const appIds = new Set((read("accounts.json").manual_assets as { id: unknown; origin: unknown }[])
      .filter((a) => a.origin === "app").map((a) => a.id));
    expect(hidden.every((e) => e.origin === "config"), "every hidden entry is the config's").toBe(true);
    expect(hidden.every((e) => appIds.has(e.id)), "every hidden entry's id is a counted app asset's").toBe(true);
  });

  // A statement file is read for one account the registry lists; a bank-feed pull reports many and names none. Boolean
  // assertions: a failure names the rule, never a file or an account.
  it("each statement file in the quality report names an account accounts.json lists, and a feed pull names none", () => {
    const ids = new Set<unknown>(read("accounts.json").accounts.map((a: { id: unknown }) => a.id));
    const files: { parser: unknown; account: unknown }[] = read("quality_report.json").files;
    const pulls = files.filter((f) => f.parser === "simplefin");
    const statements = files.filter((f) => f.parser !== "simplefin");
    expect(pulls.every((f) => f.account === null), "a feed pull names no account").toBe(true);
    expect(statements.every((f) => ids.has(f.account)), "every statement file names a listed account").toBe(true);
  });

  // The AI step's state counts the rows its stored answers filed: exactly the transactions marked "ai" (a split parent
  // is display-only and never one), and none while the step is off. Boolean assertions: a failure names the rule only.
  it("ai_status counts the rows the AI step filed, and none is filed while it is off", () => {
    const ai = read("ai_status.json");
    const rows: { category_source: unknown; is_parent: unknown; vendor: unknown }[] = read("transactions.json").transactions;
    const filed = rows.filter((t) => t.category_source === "ai" && t.is_parent === false);
    expect(ai.applied.rows === filed.length, "applied.rows is the number of rows marked ai").toBe(true);
    expect(ai.enabled || filed.length === 0, "no row is marked ai while the step is off").toBe(true);
    expect((ai.applied.vendors === 0) === (filed.length === 0), "vendors are counted exactly when rows are").toBe(true);
    expect(ai.policy_version >= 1, "the policy version is one the household can agree to").toBe(true);
  });

  // The dashboard reads the floor from the months' flags (lib/agg.ts coverageFloor), so the file's own key must say
  // the same, and the flags must be what the rule makes them: false up to the floor, true from it on.
  it("cashflow_monthly.json's coverage_floor is the first month in coverage, and the flags are a suffix", () => {
    const cf = read("cashflow_monthly.json");
    const flags: boolean[] = cf.months.map((m: { in_coverage: boolean }) => m.in_coverage);
    expect(cf.coverage_floor === coverageFloor(cf.months), "coverage_floor equals the first month in coverage").toBe(true);
    const first = flags.indexOf(true);
    expect(first === -1 || flags.slice(first).every(Boolean), "no month out of coverage after one in it").toBe(true);
  });
});

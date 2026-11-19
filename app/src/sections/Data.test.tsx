// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { acctFactory, appDataFactory, mortgageFactory, snapFactory } from "../lib/__fixtures__";
import { emptyPrefs } from "../lib/prefs";
import { MASK, setPrivate } from "../lib/privacy";

const acct = acctFactory({ txn_data_end: "2026-07-20", latest_balance: 100 });
const base = appDataFactory(snapFactory({}))({});
let data = base;
let prefsDoc = emptyPrefs();
vi.mock("../data/DataContext", () => ({ useData: () => ({ data, missing: new Set<string>(), prefs: { doc: prefsDoc } }) }));

import { AccountFreshnessCard, AssumptionsInForceCard, CaveatsCard, IngestReadCard, OpenQuestionsCard, QualityReportCard } from "./Data";

afterEach(() => { cleanup(); setPrivate(false); data = base; prefsDoc = emptyPrefs(); });
const mount = (ui: JSX.Element) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe("Data: the account-freshness list", () => {
  it("an account with no transactions says what it has, never a question mark", () => {
    data = { ...base, accounts: [
      acct({ id: "a", name: "Checking A" }),
      acct({ id: "b", name: "Brokerage B", kind: "brokerage", txn_data_start: null, txn_data_end: null, latest_balance: 5000 }),
      acct({ id: "c", name: "Empty C", txn_data_start: null, txn_data_end: null, latest_balance: null }),
    ] };
    mount(<AccountFreshnessCard />);
    const rows = Object.fromEntries([...screen.getByTestId("card-account-freshness").querySelectorAll("li")]
      .map((li) => [li.querySelector("a")!.textContent, li.querySelectorAll("span")[li.querySelectorAll("span").length - 1].textContent]));
    expect(rows).toEqual({ "Checking A": "→ Jul 20, 2026", "Brokerage B": "balance only", "Empty C": "no transactions" });
    expect(screen.getByTestId("card-account-freshness").textContent).not.toContain("?");
  });
  it("with no month in progress no account lags: none is amber and the note says so", () => {
    data = { ...base, cashflow: [], transactions: [], accounts: [acct({ id: "a", name: "Checking A", txn_data_end: "2026-01-10" })] };
    mount(<AccountFreshnessCard />);
    const card = screen.getByTestId("card-account-freshness");
    expect(card.querySelector(".text-amber-400")).toBeNull();
    expect(card.textContent).toContain("no account lags");
    expect(card.textContent).not.toMatch(/Invalid|NaN/);
  });
  it("an account that stops before the last full month (the month before the month in progress) is amber and leads", () => {
    // April and May hold rows and the ingest is in June: May is in progress, April the last full month
    data = { ...base, accounts: [acct({ id: "a", name: "Checking A", txn_data_end: "2026-05-30" }),
      acct({ id: "b", name: "Card B", txn_data_end: "2026-03-28" })] };
    mount(<AccountFreshnessCard />);
    const items = [...screen.getByTestId("card-account-freshness").querySelectorAll("li")];
    expect(items.map((li) => li.querySelector("a")!.textContent)).toEqual(["Card B", "Checking A"]);
    expect(items[0].querySelector(".text-amber-400"), "the account that stops in March lags").not.toBeNull();
    expect(items[1].querySelector(".text-amber-400")).toBeNull();
  });
});

describe("Data: the caveats", () => {
  const snap = snapFactory({});
  const withSnapshots = (firstIncluded: boolean) => ({
    ...base,
    assets: { snapshots: [
      snap({ snapshot_date: "2019-05-01", mortgage_included: firstIncluded, by_class: firstIncluded ? { Mortgage: -1000 } : {} }),
      snap({ snapshot_date: "2022-07-25", by_class: { Mortgage: -900 } }),
    ] },
    assumptions: { ...base.assumptions, mortgage: mortgageFactory({ schedule: [
      { date: "2020-06-30", principal_balance: 1 },
    ] }) },
  });
  it("names the month the mortgage data starts while a snapshot pre-dates it", () => {
    data = withSnapshots(false);
    mount(<CaveatsCard />);
    expect(screen.getByTestId("card-caveats").textContent).toContain("Net-worth snapshots before Jun 2020 don't subtract the mortgage");
  });
  it("drops the mortgage caveat once every snapshot subtracts it", () => {
    data = withSnapshots(true);
    mount(<CaveatsCard />);
    const text = screen.getByTestId("card-caveats").textContent ?? "";
    expect(text).toContain("Projections are deterministic illustrations");
    expect(text).not.toMatch(/subtract the mortgage/);
  });
  it("says where the net-worth history starts and how many earlier month ends are left out", () => {
    const shown = [snap({ snapshot_date: "2030-03-31" }), snap({ snapshot_date: "2030-04-12" })];
    const left = (n: number) => Array.from({ length: n }, (_, i) =>
      snap({ snapshot_date: `2030-0${i + 1}-28`, missing: ["Sample Brokerage"] }));
    data = { ...base, assets: { snapshots: shown }, partialSnapshots: left(2) };
    mount(<CaveatsCard />);
    expect(screen.getByTestId("card-caveats").textContent).toContain(
      "Net-worth history starts Mar 2030: 2 earlier month-ends are left out because not every account was reporting yet.");
    cleanup();
    data = { ...base, assets: { snapshots: shown }, partialSnapshots: left(1) };
    mount(<CaveatsCard />);
    expect(screen.getByTestId("card-caveats").textContent).toContain(
      "Net-worth history starts Mar 2030: 1 earlier month-end is left out because not every account was reporting yet.");
  });
  it("leaves the history line out when no month end was left out", () => {
    mount(<CaveatsCard />);
    expect(screen.getByTestId("card-caveats").textContent).not.toMatch(/Net-worth history starts|left out/);
    expect(screen.queryByTestId("caveat-left-out")).toBeNull();
  });
});

// Each month end the history leaves out is listed, newest first, with the accounts it has no balance for (at most five
// by name, then a count), each name linking its account where the registry has it; past six month ends the list sits
// behind a count. An account the newest snapshot misses too is said once, in its own sentence: configured, its first
// balance not arrived, and what brings it.
describe("Data: the month ends the history leaves out", () => {
  const snap = snapFactory({});
  const shown = [snap({ snapshot_date: "2030-03-31" }), snap({ snapshot_date: "2030-04-30" })];
  const accounts = [acct({ id: "brk", name: "Sample Brokerage" }), acct({ id: "sav", name: "Sample Savings" })];
  const items = () => [...screen.getByTestId("caveat-left-out").querySelectorAll("li")].map((li) => li.textContent);
  it("lists each month end, newest first, with what it misses; a name links its account", () => {
    data = { ...base, accounts, assets: { snapshots: shown }, partialSnapshots: [
      snap({ snapshot_date: "2030-01-31", missing: ["Sample Brokerage", "Sample Savings"] }),
      snap({ snapshot_date: "2030-02-28", missing: ["Sample Brokerage"] }),
    ] };
    mount(<CaveatsCard />);
    expect(items()).toEqual(["Feb 28, 2030: no balance yet for Sample Brokerage",
      "Jan 31, 2030: no balance yet for Sample Brokerage, Sample Savings"]);
    const link = within(screen.getByTestId("caveat-left-out")).getAllByRole("link", { name: "Sample Brokerage" })[0];
    expect(link.getAttribute("href")).toBe("/accounts/brk");
    expect(screen.getByTestId("caveat-left-out").querySelector("details"), "two month ends: listed open").toBeNull();
  });
  it("at most five names a month end, then how many more; a name the registry does not know is plain text", () => {
    const many = ["Sample A", "Sample B", "Sample C", "Sample D", "Sample E", "Sample F", "Sample G"];
    data = { ...base, accounts, assets: { snapshots: shown }, partialSnapshots: [snap({ snapshot_date: "2030-02-28", missing: many })] };
    mount(<CaveatsCard />);
    expect(items()).toEqual(["Feb 28, 2030: no balance yet for Sample A, Sample B, Sample C, Sample D, Sample E and 2 more"]);
    expect(within(screen.getByTestId("caveat-left-out")).queryAllByRole("link")).toEqual([]);
  });
  it("more than six month ends: behind a count, newest first inside", () => {
    data = { ...base, accounts, assets: { snapshots: [snap({ snapshot_date: "2031-03-31" })] },
      partialSnapshots: Array.from({ length: 7 }, (_, i) =>
        snap({ snapshot_date: `2030-0${i + 1}-28`, missing: ["Sample Brokerage"] })) };
    mount(<CaveatsCard />);
    const details = screen.getByTestId("caveat-left-out").querySelector("details")!;
    expect(details.open).toBe(false);
    expect(details.querySelector("summary")!.textContent).toBe("Show the 7 month ends and what each misses");
    expect(items()[0]).toBe("Jul 28, 2030: no balance yet for Sample Brokerage");
    expect(items()).toHaveLength(7);
  });
  it("an account the newest snapshot misses too: one sentence says it is configured, its first balance not arrived, and what brings it", () => {
    data = { ...base, accounts, assets: { snapshots: [snap({ snapshot_date: "2030-04-30", missing: ["Sample Savings"] })] } };
    mount(<CaveatsCard />);
    const line = screen.getByTestId("caveat-never-reported");
    expect(line.textContent).toBe("Sample Savings is listed among the accounts because it is configured, but its first balance "
      + "has not arrived: net worth leaves it out until an ingest reads one (a bank-feed pull, or a statement file that "
      + "states a balance).");
    expect(within(line).getByRole("link", { name: "Sample Savings" }).getAttribute("href")).toBe("/accounts/sav");
    expect(screen.queryByTestId("caveat-left-out"), "no month end is left out for it").toBeNull();
    cleanup();
    data = { ...base, accounts, assets: { snapshots: [snap({ snapshot_date: "2030-04-30", missing: ["Sample Savings", "Sample Brokerage"] })] } };
    mount(<CaveatsCard />);
    expect(screen.getByTestId("caveat-never-reported").textContent).toMatch(
      /^Sample Savings, Sample Brokerage are listed among the accounts because they are configured, but their first balances have not arrived: net worth leaves them out until an ingest reads one/);
  });
  it("the control: a newest snapshot that misses nothing says nothing of the kind", () => {
    mount(<CaveatsCard />);
    expect(screen.queryByTestId("caveat-never-reported")).toBeNull();
  });
});

// What the last ingest read, file by file: a household whose statement files yielded no rows learns which and why.
describe("Data: what the last ingest read", () => {
  type FileStat = typeof base.quality.files[number];
  const file = (over: Partial<FileStat>): FileStat => ({ source_file: "f.csv", parser: "chase_checking", account: null, rows_total: 10,
    rows_parsed: 10, rows_skipped: 0, skip_reasons: {}, duplicates_removed: 0, date_min: null, date_max: null, parsed_sum: 0, ...over });
  const withFiles = (files: FileStat[], over: Partial<typeof base.quality> = {}) =>
    ({ ...base, accounts: [acct({ id: "card", name: "Sample Card" })], quality: { ...base.quality, files, ...over } });
  const rows = () => [...screen.getByTestId("card-ingest-read").querySelectorAll("tbody tr")]
    .map((tr) => [...tr.querySelectorAll("th, td")].map((c) => c.textContent));
  it("one row per statement file and one for the bank feed; what read nothing leads, in amber, with the reason", () => {
    data = withFiles([
      file({ source_file: "checking-2030-01.csv", rows_total: 76, rows_parsed: 76 }),
      file({ source_file: "savings-2030-01.csv", rows_total: 8, rows_parsed: 0, rows_skipped: 8, skip_reasons: { "summary-row": 4, header: 4 } }),
      file({ source_file: "accounts-20300131T210000Z.json", parser: "simplefin", rows_total: 40, rows_parsed: 40 }),
      file({ source_file: "accounts-20300228T210000Z.json", parser: "simplefin", rows_total: 45, rows_parsed: 45 }),
    ], { cross_file_duplicate_uids: { "checking-2030-01.csv": 3 },
      checks: [{ name: "source.card", status: "warn", detail: "no file matched glob 'Card*.csv'" }] });
    mount(<IngestReadCard />);
    expect(screen.queryByTestId("ingest-read-summary"), "something read nothing: no summary stands in for the table").toBeNull();
    expect(screen.getByRole("table")).toBeDefined();
    expect(rows()).toEqual([
      ["Sample Card — looks for Card*.csv", "no file found", "—"],
      ["savings-2030-01.csv", "nothing read", "8 lines4 summary lines · 4 header lines"],
      ["Bank feed · 2 pulls", "85 rows", "none"],
      ["checking-2030-01.csv3 rows another file already held, kept once", "76 rows", "none"],
    ]);
    const amber = [...screen.getByTestId("card-ingest-read").querySelectorAll("tbody tr")].map((tr) => tr.className.includes("amber"));
    expect(amber).toEqual([true, true, false, false]);
    const text = screen.getByTestId("card-ingest-read").textContent ?? "";
    expect(text, "no export file is named").not.toMatch(/\.json\b/);
    expect(text).toContain("pulls overlap");
  });
  it("every source read rows: one summary line, and the table behind a disclosure; the feed's skips are rows", () => {
    data = withFiles([
      file({ source_file: "checking-2030-01.csv", rows_total: 80, rows_parsed: 76, rows_skipped: 4, skip_reasons: { "blank-line": 4 } }),
      file({ source_file: "accounts-20300131T210000Z.json", parser: "simplefin", rows_total: 1_340, rows_parsed: 1_337,
        rows_skipped: 3, skip_reasons: { pending: 3 } }),
    ]);
    mount(<IngestReadCard />);
    const summary = screen.getByTestId("ingest-read-summary");
    expect(summary.textContent).toBe("2 sources read; none came back empty.Show the sources");
    const toggle = within(summary).getByRole("button", { name: "Show the sources" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("table"), "the table is out of view and out of reach").toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.textContent).toBe("Hide the sources");
    expect(document.getElementById(toggle.getAttribute("aria-controls")!)?.hidden).toBe(false);
    expect(rows()).toEqual([
      ["Bank feed · 1 pull", "1,337 rows", "3 rows3 pending charges"],
      ["checking-2030-01.csv", "76 rows", "4 lines4 blank lines"],
    ]);
  });
  it("past twelve sources the rest wait behind a button; what read nothing is never behind it", () => {
    data = withFiles([file({ source_file: "zero.csv", rows_total: 0, rows_parsed: 0 }),
      ...Array.from({ length: 13 }, (_, i) => file({ source_file: `stmt-${i}.csv` }))]);
    mount(<IngestReadCard />);
    expect(rows()).toHaveLength(12);
    expect(rows()[0][0]).toBe("zero.csv");
    const more = screen.getByRole("button", { name: "Show all 14 sources" });
    expect(more.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(more);
    expect(rows()).toHaveLength(14);
    expect(screen.getByRole("button", { name: "Show fewer" }).getAttribute("aria-expanded")).toBe("true");
  });
  it("a file name is the household's own and shown; in privacy mode an amount-shaped piece of it is masked", () => {
    data = withFiles([file({ source_file: "stmt 12.31.csv" })]);
    mount(<IngestReadCard />);
    fireEvent.click(screen.getByRole("button", { name: "Show the sources" }));
    expect(rows()[0][0]).toBe("stmt 12.31.csv");
    cleanup();
    setPrivate(true);
    mount(<IngestReadCard />);
    fireEvent.click(screen.getByRole("button", { name: "Show the sources" }));
    expect(rows()[0][0]).toBe(`stmt ${MASK}.csv`);
  });
  it("an ingest that read no file and no pull says so in one line, with no table", () => {
    data = withFiles([]);
    mount(<IngestReadCard />);
    const card = screen.getByTestId("card-ingest-read");
    expect(card.querySelector("table")).toBeNull();
    expect(card.textContent).toContain("The last ingest read no statement file and no bank-feed pull.");
  });
});

describe("Data: the caveats on coverage and hand-entered values", () => {
  it("names the coverage floor the data holds while a month comes before it: the first month in coverage", () => {
    data = { ...base, cashflow: [{ ...base.cashflow[0], in_coverage: false }, base.cashflow[1]] };
    mount(<CaveatsCard />);
    expect(screen.getByTestId("caveat-coverage").textContent).toBe("Rates and averages start in May 2026, the first month by "
      + "which enough of the accounts had their first transaction; the months before it are left out of them "
      + "(coverage.min_share in assumptions.yaml).");
  });
  it("without payroll facts the deductions caveat is a condition (where there are some), never a claim about every household", () => {
    data = base;
    mount(<CaveatsCard />);
    expect(screen.getByTestId("caveat-payroll").textContent).toBe("Deductions taken from pay before it reaches the bank, such "
      + "as a 401(k), are not in the bank data: where there are some, true savings is higher than the cash-flow savings "
      + "rate shows. (Enter payroll facts under Cash Flow → Budget to count them.)");
  });
  it("before the household's first data the caveats and the assumptions point at no page the first-run page stands in for", () => {
    data = { ...base, transactions: [], purchases: [], assets: { snapshots: [] }, balances: { series: {}, accounts: {} } };
    mount(<><CaveatsCard /><AssumptionsInForceCard /></>);
    expect(screen.getByTestId("caveat-payroll").textContent).toMatch(/savings rate shows\.$/);
    const inForce = screen.getByTestId("card-assumptions-in-force");
    expect(inForce.textContent).not.toMatch(/Cash Flow → Budget|Accounts → Investments/);
    expect(inForce.textContent).toContain("re-run the ingest (assumptions.yaml). Projection defaults: right here →");
    expect(inForce.querySelectorAll("a")).toHaveLength(0);
  });
  it("says nothing of a floor when no month comes before it (every account started together, or there is one)", () => {
    data = { ...base, cashflow: base.cashflow.map((m) => ({ ...m, in_coverage: true })) };
    mount(<CaveatsCard />);
    expect(screen.queryByTestId("caveat-coverage")).toBeNull();
    expect(screen.getByTestId("card-caveats").textContent).not.toMatch(/multi-account|Rates and averages start/);
  });
  it("with no month in coverage the caveat that names the floor is left out, never printed with a blank month", () => {
    data = { ...base, cashflow: base.cashflow.map((m) => ({ ...m, in_coverage: false })) };
    mount(<CaveatsCard />);
    const text = screen.getByTestId("card-caveats").textContent ?? "";
    expect(screen.queryByTestId("caveat-coverage")).toBeNull();
    expect(text).not.toMatch(/null|undefined|begins\s*[;—-]/);
    expect(text, "the other caveats stay").toContain("Projections are deterministic illustrations");
  });
  it("an asset entered by hand moves when its figure is updated, in Settings → Manual assets (a link to the card)", () => {
    mount(<CaveatsCard />);
    const line = screen.getByTestId("caveat-manual-assets");
    expect(line.textContent).toBe("An asset entered by hand (a home, a private holding) is valued at the figure entered for "
      + "it, as of the date entered; it moves only when that figure is updated, in Settings → Manual assets (or "
      + "manual_entries: in accounts.yaml).");
    expect(within(line).getByRole("link").getAttribute("href")).toBe("/settings?card=manual-assets");
  });
  it("a history that holds a hand-stated value carried back says how many month ends hold one", () => {
    const carried = { account: "Sedan", account_id: null, institution: "Example Motors", owner: "Sam", asset_class: "Car",
      value: 21_000, liquid: false, as_of: "2026-03-26", as_of_assumed: false, needs_review: false, source: "stated",
      manual_id: "sedan", carried_back: true };
    const boat = { ...carried, account: "Boat", manual_id: "boat", as_of: "2026-04-10" };
    data = { ...base, assets: { snapshots: [snapFactory({})({ snapshot_date: "2026-02-28", rows: [carried, boat] }),
      snapFactory({})({ snapshot_date: "2026-03-31", rows: [{ ...carried, carried_back: false }, boat] }),
      snapFactory({})({ snapshot_date: "2026-06-01", rows: [{ ...carried, carried_back: false }, { ...boat, carried_back: false }] })] } };
    mount(<CaveatsCard />);
    expect(screen.getByTestId("caveat-manual-assets").textContent).toContain(" Before the first figure entered for it, the "
      + "net-worth history counts it at that figure (from the day it was acquired, when one is stated): 2 month ends of the "
      + "history hold a value stated later.");
    cleanup();
    data = { ...base, assets: { snapshots: [snapFactory({})({ snapshot_date: "2026-02-28", rows: [carried] }),
      snapFactory({})({ snapshot_date: "2026-06-01", rows: [{ ...carried, carried_back: false }] })] } };
    mount(<CaveatsCard />);
    expect(screen.getByTestId("caveat-manual-assets").textContent).toContain(": 1 month end of the history holds a value stated later.");
  });
  it("the current snapshot is no month end: a value stated after the newest balance, carried back into it too, is not counted there", () => {
    // stated 2026-06-20, after the newest account balance (2026-06-01): every snapshot holds it carried back, the current one included
    const later = { account: "Sedan", account_id: null, institution: "Example Motors", owner: "Sam", asset_class: "Car",
      value: 21_000, liquid: false, as_of: "2026-06-20", as_of_assumed: false, needs_review: false, source: "stated",
      manual_id: "sedan", carried_back: true };
    data = { ...base, assets: { snapshots: ["2026-04-30", "2026-05-31", "2026-06-01"].map((snapshot_date) =>
      snapFactory({})({ snapshot_date, rows: [later] })) } };
    mount(<CaveatsCard />);
    expect(screen.getByTestId("caveat-manual-assets").textContent).toContain(": 2 month ends of the history hold a value stated later.");
    cleanup();
    data = { ...base, assets: { snapshots: [snapFactory({})({ snapshot_date: "2026-06-01", rows: [later] })] } };
    mount(<CaveatsCard />);
    expect(screen.getByTestId("caveat-manual-assets").textContent, "the current snapshot alone: no month end to count")
      .not.toContain("stated later");
  });
});

describe("Data: the quality report", () => {
  const warn = { name: "simplefin.snapshots", status: "warn" as const, detail: "the newest bank feed snapshot is 2026-07-10 but 4 bank debits ($123.45) posted after it, latest 2026-09-14" };
  it("words a warning's dates the way the app does, the code staying in the tooltip", () => {
    data = { ...base, quality: { ...base.quality, checks: [warn] } };
    mount(<QualityReportCard />);
    const card = screen.getByTestId("card-quality-report");
    expect(card.textContent).toContain("the newest bank feed snapshot is Jul 10, 2026 but 4 bank debits ($123.45) posted after it, latest Sep 14, 2026");
    expect(card.textContent).not.toMatch(/\d{4}-\d{2}-\d{2}/);
    expect(within(card).getAllByTitle("simplefin.snapshots")[0].textContent).toBe("Bank feed snapshots");
  });
  it("lists a check the ETL ran once per statement file once per run, under one name, with no duplicate key", () => {
    const run = (status: "pass" | "warn", detail: string) => ({ name: "t_sav.total_credits", status, detail });
    data = { ...base, quality: { ...base.quality, checks: [run("pass", "file one"), run("pass", "file two"), run("warn", "file three"), run("warn", "file four")] } };
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      mount(<QualityReportCard />);
      const card = screen.getByTestId("card-quality-report");
      expect(card.querySelectorAll("details li").length, "every run in the full list").toBe(4);
      expect(card.textContent).toContain("file three");
      expect(card.textContent).toContain("file four");                                // both flagged runs, not one twice
      expect(error.mock.calls.some((c) => String(c[0]).includes("same key")), "no duplicate React key").toBe(false);
    } finally {
      error.mockRestore();
    }
  });
  // checks that pass over nothing passed nothing: with no transaction and no balance read, the card says so
  const passes = [{ name: "splits.sum_identity", status: "pass" as const, detail: "ok" },
    { name: "learned_rules.active", status: "info" as const, detail: "none" }];
  it("an ingest that read no data: no claim that every check passed, and what the checks had to look at", () => {
    data = { ...base, transactions: [], assets: { snapshots: [] }, quality: { ...base.quality, checks: passes } };
    mount(<QualityReportCard />);
    const card = screen.getByTestId("card-quality-report");
    expect(card.textContent).not.toMatch(/every check passed/);
    expect(within(card).getByTestId("empty-state").textContent)
      .toBe("Nothing to check yet: the last ingest read no transaction and no balance.");
  });
  it("an ingest that read data with every check passing says so", () => {
    data = { ...base, quality: { ...base.quality, checks: passes } };
    mount(<QualityReportCard />);
    expect(screen.getByTestId("card-quality-report").textContent).toContain("every check passed on the last ingest");
  });
  it("still masks the amount while privacy mode is on, and the dates stay visible", () => {
    setPrivate(true);
    data = { ...base, quality: { ...base.quality, checks: [warn] } };
    mount(<QualityReportCard />);
    const text = screen.getByTestId("card-quality-report").textContent ?? "";
    expect(text).toContain(`4 bank debits (${MASK}) posted after it, latest Sep 14, 2026`);
    expect(text).not.toContain("123.45");
  });
});

describe("Data: the open questions", () => {
  const questions = [
    { topic: "acme-card-transfer", question: "Acme Prepaid card transfers leave the tracked accounts.", context: "e.g. 2025-03-19 Acme Prepaid Card Transfer" },
    { topic: "pet-insurance-start", question: "Pet insurance claims appear from 2023-04 on — expected?", context: "" },
  ];
  it("shows the question, never the ETL's internal topic slug in brackets; the slug rides on the row for debugging", () => {
    data = { ...base, questions, dataGaps: [] };
    mount(<OpenQuestionsCard />);
    const items = [...screen.getByTestId("card-open-questions").querySelectorAll("ol > li")];
    expect(items).toHaveLength(2);
    for (const li of items) {
      expect(li.textContent).not.toMatch(/\[[a-z0-9-]+\]/);
      expect(li.textContent).not.toMatch(/acme-card-transfer|pet-insurance-start/);
    }
    expect(items.map((li) => li.getAttribute("data-topic"))).toEqual(["acme-card-transfer", "pet-insurance-start"]);
    expect(items[0].textContent).toBe("Acme Prepaid card transfers leave the tracked accounts. — e.g. Mar 19, 2025 Acme Prepaid Card Transfer");
  });
  it("words the dates in a question, its context and a data gap", () => {
    data = { ...base, questions, dataGaps: [{ topic: "gap", question: "Transfer leg missing on 2026-08-02", context: "net 5.00, latest 2026-08-30" }] };
    mount(<OpenQuestionsCard />);
    const text = screen.getByTestId("card-open-questions").textContent ?? "";
    expect(text).toContain("Pet insurance claims appear from Apr 2023 on — expected?");
    expect(text).toContain("Transfer leg missing on Aug 2, 2026");
    expect(text).toContain("latest Aug 30, 2026");
    expect(text).not.toMatch(/\d{4}-\d{2}(-\d{2})?/);
  });
});

describe("Data: the in-app overrides line", () => {
  const line = () => [...screen.getByTestId("card-assumptions-in-force").querySelectorAll("li")]
    .find((li) => li.textContent?.startsWith("In-app overrides"))!.textContent;
  it("names the target mix, and asset-class targets only when some are set (nothing in the app starts them)", () => {
    mount(<AssumptionsInForceCard />);
    expect(line()).toBe("In-app overrides0 budgets · target mix not saved · 0 projection defaults · 0 payroll phases");
    expect(screen.getByTestId("card-assumptions-in-force").textContent).toContain("These start from the household's "
      + "assumptions file; after changing it, re-run the ingest (assumptions.yaml). Budgets and payroll: Cash Flow → Budget");
  });
  it("a saved mix and one asset-class target, singular", () => {
    prefsDoc = { ...emptyPrefs(), target_mix: { stocks: 60, bonds: 30, cash: 5, alts: 5 }, targets: { Cash: 10 } };
    mount(<AssumptionsInForceCard />);
    expect(line()).toBe("In-app overrides0 budgets · target mix saved · 1 asset-class target · 0 projection defaults · 0 payroll phases");
  });
  it("several asset-class targets, plural", () => {
    prefsDoc = { ...emptyPrefs(), targets: { Cash: 10, "Stocks/ETF": 90 } };
    mount(<AssumptionsInForceCard />);
    expect(line()).toContain("· 2 asset-class targets ·");
  });
});

// The home's value is no assumption: it is the current snapshot's House-class rows, listed beside the assumptions while
// the snapshot holds one and left out otherwise, never printed as $0 or a stand-in.
describe("Data: the home value line", () => {
  const snap = snapFactory({});
  const house = (value: number) => ({ account: "Sample Home", account_id: null, institution: "Self", owner: "Joint",
    asset_class: "House", value, liquid: false, as_of: "2026-01-31", as_of_assumed: false, needs_review: false, source: "stated",
    manual_id: "sample-home", carried_back: false });
  const lines = () => [...screen.getByTestId("card-assumptions-in-force").querySelectorAll("li")].map((li) => li.textContent);
  it("is the current snapshot's House rows, under privacy mode masked like any amount", () => {
    data = { ...base, assets: { snapshots: [snap({ snapshot_date: "2026-05-31", rows: [house(480_000)] }),
      snap({ rows: [house(400_000), house(100_000)] })] } };
    mount(<AssumptionsInForceCard />);
    expect(lines()).toContain("Home value (current snapshot)$500,000");
    cleanup();
    setPrivate(true);
    mount(<AssumptionsInForceCard />);
    expect(lines()).toContain(`Home value (current snapshot)${MASK}`);
  });
  it("is left out when the snapshot holds no House row", () => {
    mount(<AssumptionsInForceCard />);
    // "home" anywhere in a line; the group keeps a path-like sequence out of this file's text
    expect(lines().some((t) => /(?:home)/i.test(t ?? ""))).toBe(false);
  });
});

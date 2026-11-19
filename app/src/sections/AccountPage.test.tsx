// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CODE_PILL } from "../components/NoData";
import type { AssetRow } from "../data/types";
import { acctFactory, snapFactory, txFactory } from "../lib/__fixtures__";
import { MASK, setPrivate } from "../lib/privacy";

const acct = acctFactory();
const tx = txFactory();
const accounts = [
  acct({ id: "chk", name: "Checking A", ledger_drift: { from: "2026-07-01", to: "2026-07-14", expected: -100, got: -120, drift: -20 } }),
  acct({ id: "sav", name: "Savings B", kind: "savings" }),
  acct({ id: "brk", name: "Broker C", kind: "brokerage", asset_class: "Stocks/ETF", latest_balance: 5000 }),
  acct({ id: "pennies", name: "Pennies D", ledger_drift: { from: "2026-07-01", to: "2026-07-14", expected: -100, got: -100.4, drift: -0.4 } }),
  acct({ id: "mixed", name: "Mixed E" }),
  // the ingest ran on Jul 21: this account's newest row is one the bank dated Jul 23
  acct({ id: "ahead", name: "Ahead F", txn_data_end: "2026-07-23" }),
];
// two different days: a running balance is kept for each day's last row only (lib/runningBalance)
// "mixed": one split purchase held as two PARTS (the category view has no parent) and one ordinary row
const transactions = [tx({ account: "chk", date: "2026-07-18" }), tx({ account: "chk", date: "2026-07-19", type: "transfer", is_internal: true }),
  tx({ account: "sav" }),
  tx({ account: "mixed", date: "2026-07-18", amount: -210, parent_id: "p", category_source: "split" }),
  tx({ account: "mixed", date: "2026-07-18", amount: -90, parent_id: "p", category_source: "split" }),
  tx({ account: "mixed", date: "2026-07-17", amount: -40 }),
  tx({ account: "ahead", date: "2026-07-20" }), tx({ account: "ahead", date: "2026-07-23" })];
const point = (balance: number) => ({ date: "2026-07-20", balance, available: null, limit: null, utilization: null, source: "simplefin" });
const CHECKS = [{ name: "reconcile.chk.store_vs_files", status: "pass", detail: "ok" }, { name: "coverage.sav", status: "info", detail: "x" }];
let checks = CHECKS;
// the newest snapshot holds "brk" as a live margin account (its positions and a margin loan beside them, summing to the
// feed's 5,000) and "chk" as one row
const row = (over: Partial<AssetRow>): AssetRow => ({ account: "Checking A", account_id: "chk", institution: "Bank", owner: "Joint",
  asset_class: "Cash", value: 1000, liquid: true, as_of: "2026-07-20", as_of_assumed: false, needs_review: false,
  source: "bank_balance", manual_id: null, carried_back: false, ...over });
const SNAPS = [snapFactory()({ snapshot_date: "2026-07-20", rows: [row({}),
  row({ account: "Broker C", account_id: "brk", asset_class: "Stocks/ETF", value: 5600, source: "positions" }),
  row({ account: "Broker Margin", account_id: "brk", asset_class: "Margin", value: -600, liquid: false, source: "simplefin_balance" })] })];
// `feed`: whether the household has a bank feed (manifest.setup.feed); a test of a file-only household clears it
const state = vi.hoisted(() => ({ missing: new Set<string>(), feed: true }));
vi.mock("../data/DataContext", () => ({
  useData: () => ({
    data: { accounts, transactions, assets: { snapshots: SNAPS },
      manifest: { generated_at: "2026-07-21T00:00:00-05:00", setup: { accounts: 6, feed: state.feed, import_files: 1, manual_assets: 0 } },
      // DataContext hands over the same empty placeholders it does in production when a file did not load
      balances: state.missing.has("balances_timeseries.json") ? { series: {}, accounts: {} }
        : { series: { chk: [point(1000)], brk: [point(5000)] }, accounts: {} },
      quality: { checks: state.missing.has("quality_report.json") ? [] : checks } },
    missing: state.missing,
  }),
}));
vi.mock("../components/TxnTable", () => ({
  default: ({ txns, runningBalance, hideAccount }: { txns: unknown[]; runningBalance?: Map<string, number>; hideAccount?: boolean }) => (
    <div data-testid="txn-table" data-hide-account={hideAccount ? "yes" : "no"}>
      {txns.length} rows{runningBalance ? ` · ${runningBalance.size} balances` : ""}
    </div>),
}));
vi.mock("../components/EChart", () => ({ default: () => <div data-testid="chart" /> }));

import AccountPage, { checksFor } from "./AccountPage";

afterEach(() => { cleanup(); setPrivate(false); state.missing = new Set(); state.feed = true; checks = CHECKS; });
const at = (id: string) => render(
  <MemoryRouter initialEntries={[`/accounts/${id}`]}>
    <Routes><Route path="/accounts/:id" element={<AccountPage />} /></Routes>
  </MemoryRouter>);

describe("AccountPage", () => {
  it("renders the header and this account's rows, transfers included", () => {
    at("chk");
    expect(screen.getByTestId("card-account-header").textContent).toMatch(/Checking A/);
    expect(screen.getByTestId("txn-table").textContent).toBe("2 rows · 2 balances");
  });
  it("the card counts transactions, as the register's CSV button names them: a split purchase is one, however many parts it holds", () => {
    at("mixed");
    expect(screen.getByTestId("txn-table").textContent, "the register is handed the three parts").toMatch(/^3 rows/);
    expect(screen.getByTestId("card-account-transactions").textContent).toMatch(/Transactions — 2 transactions, transfers included/);
    cleanup(); at("chk");
    expect(screen.getByTestId("card-account-transactions").textContent).toMatch(/Transactions — 2 transactions, transfers included/);
  });
  it("'Transactions through' stops at the ingest day, and a row the bank dated after it is named on a line of its own", () => {
    at("ahead");
    const header = screen.getByTestId("card-account-header").textContent ?? "";
    expect(header).toContain("Transactions throughJul 21, 2026");
    expect(header).toContain("Dated ahead of that1 row, latest Jul 23, 2026");
    expect(screen.getByTestId("txn-table").textContent, "the register still lists it").toMatch(/^2 rows/);
    cleanup(); at("chk");
    const plain = screen.getByTestId("card-account-header").textContent ?? "";
    expect(plain).toContain("Transactions throughJul 20, 2026");
    expect(plain).not.toContain("Dated ahead");
  });
  it("the register drops its Account column (constant on this page) so the Balance column fits without a pan", () => {
    at("chk");
    expect(screen.getByTestId("txn-table").getAttribute("data-hide-account")).toBe("yes");
  });
  it("an unknown id renders the unknown-account card in plain words with a way to Balances, never a crash", () => {
    at("not-a-real-id");
    expect(screen.getByTestId("card-account-header").textContent).toMatch(/Unknown account/);
    const line = screen.getByTestId("unknown-account").textContent ?? "";
    expect(line).toBe("This household has no account with the id not-a-real-id: it may have been renamed or removed. "
      + "Balances lists the accounts that have reported a balance. Go to Balances →");
    expect(line, "no export file named").not.toMatch(/\.json/);
    expect(screen.getByRole("link", { name: "Go to Balances →" }).getAttribute("href")).toBe("/accounts");
    expect(screen.queryByTestId("card-account-transactions")).toBeNull();
    // the id is in the shared code pill, which reads in the light theme too
    const code = screen.getByTestId("unknown-account").querySelector("code");
    expect([code?.textContent, code?.className]).toEqual(["not-a-real-id", CODE_PILL]);
  });
  it("a margin account's two figures are named: the balance net of the margin loan, and the positions beside the loan", () => {
    at("brk");
    const header = screen.getByTestId("card-account-header").textContent ?? "";
    expect(header).toContain("Balance, net of the margin loan$5,000");
    expect(header).toContain("Positions$5,600");
    expect(header).toContain("Margin loan$600");
    expect(header, "never both called the balance").not.toContain("Latest balance");
    expect(screen.getByTestId("card-account-balance-history").textContent).toContain("Balance history — net of the margin loan");
    cleanup(); at("chk");
    const plain = screen.getByTestId("card-account-header").textContent ?? "";
    expect(plain, "an account held as one row has one balance").toContain("Latest balance$1,000");
    expect(plain).not.toMatch(/Positions|margin/);
  });
  it("shows the account's own quality checks as chips, in words, and no other account's", () => {
    at("chk");
    const header = screen.getByTestId("card-account-header");
    expect(within(header).getByText("Feed matches the files: passed")).toBeDefined();
    expect(within(header).queryByText(/coverage/i)).toBeNull();
    cleanup(); at("sav");
    expect(within(screen.getByTestId("card-account-header")).getByText("Transaction coverage: for information")).toBeDefined();
  });
  it("a balance history of one balance says why it is one dot: the line draws with the second", () => {
    at("chk");
    expect(within(screen.getByTestId("card-account-balance-history")).getByTestId("not-enough-history").textContent)
      .toBe("Not enough history yet — one balance so far; the line draws once a second one is recorded.");
  });
  it("a household with no bank feed has no feed line: its checks are of the files, and say so", () => {
    state.feed = false;
    at("chk");
    const header = screen.getByTestId("card-account-header");
    expect(header.textContent).not.toMatch(/feed/i);
    expect(within(header).getByText("Checks:")).toBeDefined();
    expect(within(header).getByText("Stored rows match the files: passed")).toBeDefined();
  });
  it("a check the ETL ran once per statement file is a chip per run, with no duplicate key", () => {
    checks = [{ name: "reconcile.chk.store_vs_files", status: "pass", detail: "file one" },
      { name: "reconcile.chk.store_vs_files", status: "warn", detail: "file two" }];
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      at("chk");
      const header = screen.getByTestId("card-account-header");
      expect(within(header).getByText("Feed matches the files: passed").getAttribute("title")).toContain("file one");
      expect(within(header).getByText("Feed matches the files: needs a look").getAttribute("title")).toContain("file two");
      expect(error.mock.calls.some((c) => String(c[0]).includes("same key")), "no duplicate React key").toBe(false);
    } finally {
      error.mockRestore();
    }
  });
  it("a chip's visible text never shows the check's code; the code rides in the tooltip with what the check found", () => {
    at("chk");
    const chip = within(screen.getByTestId("card-account-header")).getByText("Feed matches the files: passed");
    const visible = [...chip.childNodes].filter((n) => n.nodeType === Node.TEXT_NODE).map((n) => n.textContent).join("");
    expect(visible).toBe("Feed matches the files: passed");
    expect(visible).not.toMatch(/store_vs_files|reconcile\./);
    expect(chip.getAttribute("title")).toBe("reconcile.chk.store_vs_files — ok");
  });
  it("the tooltip words the check's dates the way the app does, and keeps the code as it is", () => {
    checks = [{ name: "reconcile.chk.store_vs_files", status: "warn", detail: "balance dated 2026-09-01, 12 days behind 2026-09-13 (tx_2026-09-01)" }];
    at("chk");
    const chip = within(screen.getByTestId("card-account-header")).getByText("Feed matches the files: needs a look");
    expect(chip.getAttribute("title")).toBe("reconcile.chk.store_vs_files — balance dated Sep 1, 2026, 12 days behind Sep 13, 2026 (tx_2026-09-01)");
  });
  it("the ledger-drift card renders the payload, or says the check did not run - never a zero", () => {
    at("chk");
    expect(screen.getByTestId("card-ledger-drift").textContent).toMatch(/Drift.*\u2212\$20\.00/s);
    expect(screen.getByTestId("card-ledger-drift").textContent).toMatch(/For information only/);
    expect(screen.getByTestId("card-ledger-drift").textContent).toMatch(/pending charges/);
    expect(screen.getByTestId("card-ledger-drift").textContent).not.toMatch(/SimpleFIN|ETL/);
    cleanup(); at("sav");
    expect(screen.getByTestId("card-ledger-drift").textContent).toMatch(/No ledger-drift check/);
    expect(screen.getByTestId("card-ledger-drift").textContent).not.toMatch(/\$0/);
    // the null state names the whole eligibility, not only the feed
    expect(screen.getByTestId("card-ledger-drift").textContent).toMatch(/checking, savings or credit-card account connected to the\s+bank feed/);
  });
  it("the drift card shows cents: a drift of a few cents never reads $0", () => {
    at("pennies");
    const text = screen.getByTestId("card-ledger-drift").textContent ?? "";
    expect(text).toMatch(/Drift.*\u2212\$0\.40/s);
    expect(text).toMatch(/Posted rows sum to.*\u2212\$100\.40/s);
  });
  it("balance history: a chart when the account has observations, an explanation when it has none", () => {
    at("chk");
    expect(within(screen.getByTestId("card-account-balance-history")).getByTestId("chart")).toBeDefined();
    cleanup(); at("sav");
    expect(screen.getByTestId("card-account-balance-history").textContent).toMatch(/No balance for this account yet/);
  });
  it("a running balance for a cash account, none for a brokerage, each with the reason in the footnote", () => {
    at("chk");
    const card = () => screen.getByTestId("card-account-transactions");
    expect(screen.getByTestId("txn-table").textContent).toBe("2 rows · 2 balances");
    expect(within(card()).getByText(/worked back from the account's latest balance through the posted rows/)).toBeDefined();
    expect(within(card()).getByText(/shown on each day's row nearest the later days/)).toBeDefined();
    cleanup(); at("brk");
    expect(screen.getByTestId("txn-table").textContent).toBe("0 rows");
    expect(within(card()).getByText(/moves with the market/)).toBeDefined();
  });
  it("an eligible account with no observation says why it has no running balance", () => {
    at("sav");
    expect(screen.getByTestId("txn-table").textContent).toBe("1 rows");
    expect(within(screen.getByTestId("card-account-transactions")).getByText(/has no balance to anchor on yet/)).toBeDefined();
  });
  it("a missing balances_timeseries.json gates only the balance-history card; the header, drift and register stay", () => {
    state.missing = new Set(["balances_timeseries.json"]);
    at("chk");
    expect(screen.queryByTestId("card-account-balance-history")).toBeNull();
    expect(screen.getByTestId("card-account-header")).toBeDefined();
    expect(screen.getByTestId("card-ledger-drift")).toBeDefined();
    expect(screen.getByTestId("card-account-transactions")).toBeDefined();
    // the register must not claim "no observation yet" about a file that simply did not load
    const note = within(screen.getByTestId("card-account-transactions")).getByText(/No running balance/).textContent ?? "";
    expect(note).toMatch(/the balance history could not be loaded/);
    expect(note).not.toMatch(/\.json|has no balance to anchor on yet/);
    expect(screen.getByTestId("txn-table").textContent).toBe("2 rows");
  });
  it("a missing quality_report.json says the feed status is unavailable, not that no check names the account", () => {
    state.missing = new Set(["quality_report.json"]);
    at("chk");
    const header = screen.getByTestId("card-account-header").textContent ?? "";
    expect(header).toMatch(/unavailable — the quality report could not be loaded/);
    expect(header).not.toMatch(/\.json/);
    expect(header).not.toMatch(/no checks name this account/);
  });
  it("an account no check names says so", () => {
    at("brk");
    expect(screen.getByTestId("card-account-header").textContent).toMatch(/no checks name this account/);
  });
});

describe("checksFor", () => {
  const c = (name: string) => ({ name });
  it("matches the id as a dotted segment or as the last one, never as a substring of another id", () => {
    const list = [c("reconcile.chk.store_vs_files"), c("reconcile.chk.ledger_drift"), c("coverage.chk"), c("coverage.chk2"),
      c("reconcile.chk2.store_vs_files"), c("simplefin.balance_age.chk"), c("categorization.uncategorized_share")];
    expect(checksFor(list, "chk").map((x) => x.name)).toEqual(
      ["reconcile.chk.store_vs_files", "reconcile.chk.ledger_drift", "coverage.chk", "simplefin.balance_age.chk"]);
    expect(checksFor(list, "nope")).toEqual([]);
  });
});

// privacy mode: the drift card is a money surface - feed move, posted sum and drift all print through fmtUSD
describe("AccountPage under privacy mode", () => {
  it("the ledger-drift card masks every amount (even a few cents) and keeps its words and its window", () => {
    setPrivate(true);
    at("pennies");
    const text = screen.getByTestId("card-ledger-drift").textContent ?? "";
    expect(/\$|\d\.\d\d/.test(text), "no amount").toBe(false);
    expect(text.split(MASK).length - 1, "feed move, posted rows and drift").toBe(3);
    expect(text).toMatch(/Jul 1, 2026 \u2192 Jul 14, 2026/);
    expect(text).toMatch(/For information only/);
  });
});

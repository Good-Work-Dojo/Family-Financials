// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { Transaction } from "../data/types";
import { SHIPPED_RESERVED } from "../lib/categories";
import { downloadCsv } from "../lib/csv";
import { emptyPrefs } from "../lib/prefs";
import { txFactory } from "../lib/__fixtures__";
import Transactions from "./Transactions";

// The real FilterBar over the real register: the two counts that sit together on the tab - the bar's "N of M
// transactions" and the CSV button's rows - count the same rows, and each names its unit. A split purchase is one
// transaction and as many CSV rows as its parts; income is a transaction too, never called a purchase; a row the bank
// dated into a month that has not begun is in both (the register lists it), and the bar says it is not in the spend.
const store = vi.hoisted(() => ({
  txns: [] as Transaction[], purchases: [] as Transaction[],
  splitParents: new Map<string, Transaction>(), childrenByParent: new Map<string, Transaction[]>(),
}));
vi.mock("../data/DataContext", () => ({
  useData: () => ({
    data: { transactions: store.txns, purchases: store.purchases, accounts: [], splitParents: store.splitParents,
      // the ingest ran on Jan 31, 2026: January is the month in progress, February has not begun
      manifest: { generated_at: "2026-01-31T20:00:00-05:00" }, cashflow: [],
      assumptions: { taxonomy: { Dining: ["Coffee"] }, owners: ["Joint"], category_renames: [], reserved: SHIPPED_RESERVED },
      vendors: [] },
    indexes: { dataRange: { first: "2025-01-01", last: "2026-02-02" }, dataThrough: "2026-01-31", vendorList: [],
      categoryList: ["Dining"], childrenByParent: store.childrenByParent, tagList: [] },
    missing: new Set<string>(), overlay: { pendingIds: new Set<string>(), conflictedKeys: new Set<string>() },
    prefs: { doc: emptyPrefs(), saveFilter: () => true },
    answers: { doc: { answers: [] }, answerTxns: () => {}, answerTxn: () => {}, answerVendor: () => {} },
  }),
}));
vi.mock("../lib/csv", async (importOriginal) => ({ ...(await importOriginal<typeof import("../lib/csv")>()), downloadCsv: vi.fn() }));
vi.mock("@tanstack/react-virtual", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-virtual")>()),
  useVirtualizer: (opts: { count: number }) => ({
    getTotalSize: () => opts.count * 40,
    getVirtualItems: () => Array.from({ length: opts.count }, (_, index) => ({ index, start: index * 40, key: index })),
    measureElement: () => {}, scrollToIndex: () => {},
  }),
}));

beforeAll(() => {
  (globalThis as any).ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  vi.stubGlobal("matchMedia", (query: string) => ({      // a 1280px desktop: the full grid, the full filter bar
    get matches() { const m = /max-width: (\d+)px/.exec(query); return !!m && 1280 <= Number(m[1]); },
    media: query, addEventListener() {}, removeEventListener() {},
  }));
});
afterAll(() => { vi.unstubAllGlobals(); });
afterEach(() => {
  cleanup(); vi.mocked(downloadCsv).mockClear();
  store.splitParents = new Map(); store.childrenByParent = new Map();
});

const tx = txFactory({ type: "purchase", category: "Dining", subcategory: "Coffee" });
// a -300 purchase split in two parts, an ordinary -40 purchase, a paycheck, and a -25 charge the bank dated into February
const parent = tx({ id: "p", date: "2026-01-15", vendor: "Split Mart", amount: -300, is_parent: true });
const partA = tx({ id: "pa", date: "2026-01-15", vendor: "Split Mart", amount: -210, parent_id: "p" });
const partB = tx({ id: "pb", date: "2026-01-15", vendor: "Split Mart", amount: -90, parent_id: "p" });
const plain = tx({ id: "o", date: "2026-01-10", vendor: "Plain Shop", amount: -40 });
const pay = tx({ id: "i", date: "2026-01-12", vendor: "Employer", amount: 2_000, type: "income", category: "Income", subcategory: null });
const ahead = tx({ id: "x", date: "2026-02-02", vendor: "Early Bill", amount: -25 });

// `from=` is a filter key, so this is not a fresh visit and the six-month default stays out of the way
const mount = () => render(<MemoryRouter initialEntries={["/spending/transactions?from=2020-01-01"]}><Transactions /></MemoryRouter>);
const countLine = () => [...document.querySelectorAll("span")].find((s) => /^[\d,]+ of [\d,]+ transactions?/.test(s.textContent ?? ""))!;
const csvButton = () => screen.getByRole("button", { name: /Export CSV/ });
const csvLines = () => {
  fireEvent.click(csvButton());
  const [, csv] = vi.mocked(downloadCsv).mock.calls[0];
  return csv.trim().split("\r\n").slice(1);          // the data rows, without the header
};

describe("Transactions: the filter bar's count and the CSV button count the same rows", () => {
  it("a split purchase and a paycheck: the bar counts transactions, the split once, and the button names the same count beside its rows", () => {
    store.txns = [partA, partB, plain, pay];
    store.purchases = [parent, plain, pay];
    store.splitParents = new Map([["p", parent]]);
    store.childrenByParent = new Map([["p", [partA, partB]]]);
    mount();
    expect(countLine().textContent).toMatch(/^3 of 3 transactions · spend \$340/);
    expect(csvButton().textContent).toBe("⬇ Export CSV: 4 rows (3 transactions)");
    expect(csvLines(), "the file holds the rows the button states").toHaveLength(4);
    expect(document.body.textContent, "the paycheck is no purchase, so neither count calls them purchases").not.toMatch(/\d purchases?\b/);
  });

  it("a row dated into a month not begun is in both counts and in the file; the bar says it is not in the spend", () => {
    store.txns = [partA, partB, plain, ahead];
    store.purchases = [parent, plain, ahead];
    store.splitParents = new Map([["p", parent]]);
    store.childrenByParent = new Map([["p", [partA, partB]]]);
    mount();
    expect(countLine().textContent).toBe("3 of 3 transactions · spend $340 · 1 dated into a month not begun, not in the spend or a report yet");
    expect(csvButton().textContent).toBe("⬇ Export CSV: 4 rows (3 transactions)");
    const lines = csvLines();
    expect(lines).toHaveLength(4);
    expect(lines.some((l) => l.startsWith("2026-02-02,")), "the row dated ahead is exported").toBe(true);
  });

  it("the control: with no split purchase the rows are the transactions, and the button names rows alone", () => {
    store.txns = [plain, ahead];
    store.purchases = [plain, ahead];
    mount();
    expect(countLine().textContent).toMatch(/^2 of 2 transactions/);
    expect(csvButton().textContent).toBe("⬇ Export CSV: 2 rows");
    expect(csvLines()).toHaveLength(2);
  });
});

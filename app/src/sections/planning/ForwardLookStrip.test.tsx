// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AssetRow, CategoryRename, Transaction } from "../../data/types";
import { cfmFactory, recFactory, snapFactory, txFactory } from "../../lib/__fixtures__";
import { monthsBetween } from "../../lib/agg";
import { SHIPPED_RESERVED } from "../../lib/categories";
import { _resetEfundOverridesMemory, EFUND_OVERRIDES_KEY, writeEfundOverrides } from "../../lib/efundOverrides";

// The strip reads only transactions, the newest snapshot, recurring.json and the data range.
let missing = new Set<string>();
let transactions: Transaction[] = [];
let renameList: CategoryRename[] = [];
const cashRow: AssetRow = {
  account: "Checking", account_id: "chk", institution: "Bank", owner: "Joint", asset_class: "Cash",
  value: 1000, liquid: true, as_of: "2026-06-01", as_of_assumed: false, needs_review: false, source: "simplefin_balance",
  manual_id: null, carried_back: false,
};
const snap = snapFactory()({
  total: 100_000, rows: [cashRow], by_class: { Cash: 1000, "Stocks/ETF": 99_000 },
});
const rec = recFactory({ active: true });
let currentSnapshotDate: string | null = null;
let extraRecurring: ReturnType<typeof rec>[] = [];
let cashflowMonths = monthsBetween("2025-06", "2026-06").map((month) => cfmFactory()({ month }));
const longHistory = cashflowMonths;
vi.mock("../../data/DataContext", () => ({
  useData: () => ({
    data: {
      transactions,
      assets: { snapshots: [snap] },
      recurring: [rec({ vendor: "Insurance", cadence: "annual", typical_amount: 1200, next_expected: "2026-09-01" }),
        ...extraRecurring],
      // the data reach June 2026 (the data range's last day) and the ingest ran in it: June is the month in progress, the
      // cash flow holds every month from June 2025 (`cashflowMonths`), and the runway's essentials average December to May
      cashflow: cashflowMonths,
      manifest: { current_snapshot_date: currentSnapshotDate, generated_at: "2026-06-20T20:00:00-05:00" },
      assumptions: { category_renames: renameList, reserved: SHIPPED_RESERVED },
    },
    indexes: { dataRange: { first: "2024-06-01", last: "2026-06-20" }, dataThrough: "2026-06-20" },
    missing,
  }),
}));

import ForwardLookStrip, { SCROLL_TARGETS } from "./ForwardLookStrip";

const tx = txFactory();
// 600 of essential spend inside the 6 full months before June 2026 = 100/mo against 1000 of cash
const groceries = () => tx({ date: "2026-03-10", category: "Groceries", subcategory: "Groceries", amount: -600 });

beforeEach(() => {
  missing = new Set(); transactions = [groceries()]; currentSnapshotDate = null; extraRecurring = []; renameList = [];
  cashflowMonths = longHistory;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); _resetEfundOverridesMemory(); });

describe("ForwardLookStrip", () => {
  it("shows the three forward-look numbers in one grid", () => {
    render(<ForwardLookStrip />);
    expect(screen.getByTestId("kpis-forward-look")).toBeDefined();
    expect(screen.getByTestId("kpi-cash-runway").textContent).toMatch(/10\.0 mo/);
    // stocks −30% of 99,000 = 69,300, plus 1,000 cash
    expect(screen.getByTestId("kpi-bear-case").textContent).toMatch(/\$70,300/);
    expect(screen.getByTestId("kpi-big-bills").textContent).toMatch(/\$1,200/);
  });

  it("the grid is one column on a phone and two from sm (no 1 -> 3 jump)", () => {
    render(<ForwardLookStrip />);
    const cls = screen.getByTestId("kpis-forward-look").className.split(/\s+/);
    expect(cls).toContain("sm:grid-cols-2");
    expect(cls.some((c) => /^grid-cols-/.test(c))).toBe(false);
  });

  it("the lumpy-bills KPI opens Spending -> Recurring", () => {
    render(<ForwardLookStrip />);
    expect(screen.getByTestId("kpi-big-bills").getAttribute("href")).toBe("#/spending/recurring");
  });

  it("the runway and bear-case KPIs scroll to the card they summarise", () => {
    const gauge = document.createElement("div");
    gauge.id = SCROLL_TARGETS.runway;
    const shock = document.createElement("div");
    shock.id = SCROLL_TARGETS.bearCase;
    const spies = [gauge, shock].map((el) => { el.scrollIntoView = vi.fn(); document.body.appendChild(el); return el.scrollIntoView as ReturnType<typeof vi.fn>; });
    render(<ForwardLookStrip />);
    fireEvent.click(screen.getByTestId("kpi-cash-runway"));
    expect(spies[0]).toHaveBeenCalledTimes(1);
    expect(spies[1]).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("kpi-bear-case"));
    expect(spies[1]).toHaveBeenCalledTimes(1);
    gauge.remove(); shock.remove();
  });

  it("clicking a KPI whose target card is absent (gated) does nothing and does not throw", () => {
    render(<ForwardLookStrip />);
    expect(() => fireEvent.click(screen.getByTestId("kpi-bear-case"))).not.toThrow();
  });

  it("a missing recurring.json gates only the lumpy-bills KPI", () => {
    missing = new Set(["recurring.json"]);
    render(<ForwardLookStrip />);
    expect(screen.queryByTestId("kpi-big-bills")).toBeNull();
    expect(screen.getByTestId("missing-data").textContent).toMatch(/the recurring charges could not be loaded/);
    expect(screen.getByTestId("kpi-cash-runway")).toBeDefined();
    expect(screen.getByTestId("kpi-bear-case")).toBeDefined();
  });

  it("no spending in the window reads as unknown, never '0.0 mo' of runway", () => {
    transactions = [tx({ date: "2026-06-03", category: "Groceries", subcategory: "Groceries", amount: -60 })];
    const { container } = render(<ForwardLookStrip />);
    const runway = screen.getByTestId("kpi-cash-runway").textContent ?? "";
    expect(runway).toMatch(/—/);
    expect(runway).toMatch(/no essential-spending history yet/);
    expect(container.textContent).not.toMatch(/0\.0 mo|NaN|undefined/);
  });

  it("with fewer than three full months there is no runway: — and not enough history, never one from a month", () => {
    cashflowMonths = monthsBetween("2026-04", "2026-06").map((month) => cfmFactory()({ month }));   // April and May full
    transactions = [tx({ date: "2026-05-10", category: "Groceries", subcategory: "Groceries", amount: -600 })];
    render(<ForwardLookStrip />);
    expect(screen.getByTestId("kpi-cash-runway").textContent, "— and what it needs and how many full months there are")
      .toBe("Cash runway—Not enough history yet: needs 3 full months of transactions, 1 so far");
    expect(screen.getByTestId("kpi-cash-runway").tagName, "the emergency-fund card says the same: nothing to scroll to").not.toBe("BUTTON");
  });

  it("three full months are enough: the runway averages those three, not six", () => {
    cashflowMonths = monthsBetween("2026-03", "2026-06").map((month) => cfmFactory()({ month }));
    // the data begins on March 1st, so March is a whole month (a coffee there is no essential)
    transactions = [tx({ date: "2026-03-01", category: "Dining", subcategory: "Coffee", amount: -30 }),
      tx({ date: "2026-05-10", category: "Groceries", subcategory: "Groceries", amount: -600 })];
    render(<ForwardLookStrip />);
    // 1000 ÷ (600 / 3) = 5.0 months
    expect(screen.getByTestId("kpi-cash-runway").querySelector("div.tabular-nums")?.textContent).toBe("5.0 mo");
  });
});

// A configured rename changes the category the rows carry; the runway KPI must keep dividing by the renamed essentials.
describe("ForwardLookStrip runway KPI and a category rename", () => {
  it("a renamed essential category (Groceries -> Food) is still what the cash is divided by", () => {
    renameList = [{ from: { category: "Groceries" }, to: { category: "Food" } }];
    transactions = [tx({ date: "2026-03-10", category: "Food", subcategory: "Groceries", amount: -600 })];
    render(<ForwardLookStrip />);
    expect(screen.getByTestId("kpi-cash-runway").querySelector("div.tabular-nums")?.textContent).toBe("10.0 mo");
  });
  it("with no rename known the same rows are no essential spending: unknown, not 0 months (control)", () => {
    transactions = [tx({ date: "2026-03-10", category: "Food", subcategory: "Groceries", amount: -600 })];
    render(<ForwardLookStrip />);
    expect(screen.getByTestId("kpi-cash-runway").textContent).toMatch(/no essential-spending history yet|—/);
  });
});

// The gauge below the strip (Tools.tsx EmergencyFund) applies the device-local essential toggles;
// the KPI must apply the same ones (lib/efundOverrides), or the two cards on one page disagree.
describe("ForwardLookStrip runway KPI and the essential toggles", () => {
  // groceries $100/mo (essential by default) + restaurants $200/mo (discretionary by default)
  const restaurants = () => tx({ date: "2026-04-10", category: "Dining", subcategory: "Restaurants", amount: -1200 });
  const value = () => screen.getByTestId("kpi-cash-runway").querySelector("div.tabular-nums")?.textContent;

  it("applies the device's toggles exactly as the gauge does", () => {
    transactions = [groceries(), restaurants()];
    localStorage.setItem(EFUND_OVERRIDES_KEY, JSON.stringify({ "Dining / Restaurants": true }));
    render(<ForwardLookStrip />);
    // 1000 ÷ (100 + 200) = 3.3 months, not the default-essentials 10.0
    expect(value()).toBe("3.3 mo");
    expect(screen.getByTestId("kpi-cash-runway").textContent).toContain("$300/mo essentials");
  });

  it("follows a toggle made in the gauge on the same page, live", () => {
    transactions = [groceries(), restaurants()];
    render(<ForwardLookStrip />);
    expect(value()).toBe("10.0 mo");
    act(() => writeEfundOverrides({ "Dining / Restaurants": true }));
    expect(value()).toBe("3.3 mo");
    act(() => writeEfundOverrides(null));
    expect(value()).toBe("10.0 mo");
  });

  it("every row toggled off reads —, and says why (not 'no history')", () => {
    localStorage.setItem(EFUND_OVERRIDES_KEY, JSON.stringify({ "Groceries / Groceries": false }));
    render(<ForwardLookStrip />);
    expect(value()).toBe("—");
    expect(screen.getByTestId("kpi-cash-runway").textContent).toContain("no row is marked essential");
  });
});

describe("ForwardLookStrip lumpy-bills window", () => {
  it("covers Spending → Recurring's chart's twelve months, the months after the month in progress, whatever the snapshot's date", () => {
    // June 2026 is in progress: a renewal in June 2027 (the twelfth month) is in, one in July 2027 is not
    for (const snapshotDate of ["2026-07-01", "2026-05-31", null]) {
      currentSnapshotDate = snapshotDate;
      extraRecurring = [rec({ vendor: "Domain", cadence: "annual", typical_amount: 600, next_expected: "2027-06-25" })];
      render(<ForwardLookStrip />);
      expect(screen.getByTestId("kpi-big-bills").textContent, `${snapshotDate}: the twelfth month`).toMatch(/\$1,800/);
      cleanup();
      extraRecurring = [rec({ vendor: "Domain", cadence: "annual", typical_amount: 600, next_expected: "2027-07-02" })];
      render(<ForwardLookStrip />);
      expect(screen.getByTestId("kpi-big-bills").textContent, `${snapshotDate}: the thirteenth month`).toMatch(/\$1,200/);
      cleanup();
    }
  });
});

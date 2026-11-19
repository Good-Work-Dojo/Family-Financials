// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useReducer } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CashflowMonth, CategoryRename, HouseholdPerson, Transaction } from "../data/types";
import { NO_TRANSACTIONS, NO_TRANSACTIONS_YET } from "../components/NoData";
import { cfmFactory, txFactory } from "../lib/__fixtures__";
import { _resetEfundOverridesMemory, writeEfundOverrides } from "../lib/efundOverrides";
import { emptyPrefs, type PrefsDoc, renameBudgets, withBudget } from "../lib/prefs";
import { MASK, setPrivate } from "../lib/privacy";

// The page reads the merged assumptions (taxonomy, budgets, renames), the cash flow, the transactions
// and the prefs store. The store stand-in applies the REAL doc op (lib/prefs.ts withBudget) to a doc
// this file owns, so what the editor writes is what a real commit would have stored.
const cfm = cfmFactory();
const tx = txFactory({ type: "purchase" });
const NOW = "2026-07-16T10:00:00Z";
const h = vi.hoisted(() => ({
  doc: null as unknown as PrefsDoc, renames: [] as CategoryRename[], missing: new Set<string>(),
  cashflow: [] as CashflowMonth[], txns: [] as Transaction[], charts: [] as any[], budgets: {} as Record<string, number>,
  splitParents: new Map<string, Transaction>(), children: new Map<string, Transaction[]>(),
  payrollWrites: [] as { from: string; person: string }[][],
  taxonomy: {} as Record<string, string[]>,
  people: [] as HouseholdPerson[],
  timezone: null as string | null,
}));
vi.mock("../components/EChart", () => ({
  default: ({ option }: { option: unknown }) => { h.charts.push(option); return <div data-testid="chart" />; },
}));
vi.mock("../data/DataContext", async () => {
  const prefs = await import("../lib/prefs");
  const { SHIPPED_RESERVED } = await import("../lib/categories");
  return {
    useData: () => {
      const [, bump] = useReducer((n: number) => n + 1, 0);
      return {
        missing: h.missing,
        indexes: { childrenByParent: h.children },
        data: {
          cashflow: h.cashflow, transactions: h.txns, splitParents: h.splitParents, manifest: { current_snapshot_date: null, generated_at: "2026-04-10T00:00:00" },
          assumptions: { taxonomy: h.taxonomy, category_renames: h.renames, reserved: SHIPPED_RESERVED,
            budgets: h.budgets, household: { people: h.people, timezone: h.timezone } },
        },
        prefs: {
          doc: h.doc, localOnly: false,
          setBudget: (c: string, v: number | null, renames?: CategoryRename[]) => {
            h.doc = prefs.withBudget(h.doc, c, v, NOW, renames);
            bump();
          },
          setPayrollPhases: (phases: { from: string; person: string }[]) => { h.payrollWrites.push(phases); },
        },
      };
    },
  };
});
import Budget, { BudgetEditor } from "./Budget";

// 2025-12 .. 2026-04: four months of history, then 2026-04 = the newest (partial) month
const months = (n: number, from = 2025, startMonth = 12) => Array.from({ length: n }, (_, i) => {
  const idx = startMonth - 1 + i;
  const month = `${from + Math.floor(idx / 12)}-${String((idx % 12) + 1).padStart(2, "0")}`;
  return cfm({ month, by_category: {
    Dining: { total: 600 + i * 10, by_subcategory: {} }, Groceries: { total: 900, by_subcategory: {} } } });
});

const RENAMES: CategoryRename[] = [{ from: { category: "Eating Out" }, to: { category: "Dining" } }];
beforeEach(() => {
  h.doc = emptyPrefs(); h.renames = []; h.missing = new Set(); h.charts = []; h.budgets = {};
  h.splitParents = new Map(); h.children = new Map();
  h.cashflow = months(4);                                     // 2025-12 .. 2026-03 (the newest, 2026-03, is partial)
  h.txns = [];
  h.taxonomy = { Dining: [], Groceries: [], Internal: [], Income: [] };
  h.people = [];
});
afterEach(() => {
  cleanup(); setPrivate(false); localStorage.clear(); _resetEfundOverridesMemory();
  delete (window as { matchMedia?: unknown }).matchMedia;
});

const row = (name: string) => screen.getByText(name).closest("label")!;
const CLEAR = "clear — back to the automatic baseline";

describe("BudgetEditor after a category rename: a budget saved under the old name carries to the new one", () => {
  beforeEach(() => {
    h.renames = RENAMES;
    h.doc = withBudget(withBudget(emptyPrefs(), "Eating Out", 800, NOW), "Groceries", 1200, NOW);   // saved under the OLD name
  });

  it("shows a budget saved under the old name on the renamed category's row, with its clear button", () => {
    render(<BudgetEditor month="2026-04" onDone={() => {}} />);
    expect((within(row("Dining")).getByRole("spinbutton") as HTMLInputElement).value).toBe("800");
    expect(within(row("Dining")).queryByTitle(CLEAR) !== null, "clear button on the renamed row").toBe(true);
    expect((within(row("Groceries")).getByRole("spinbutton") as HTMLInputElement).value).toBe("1200");
  });

  it("clearing it removes the old-name key too, so the budget cannot rename back and resurrect", () => {
    render(<BudgetEditor month="2026-04" onDone={() => {}} />);
    fireEvent.click(within(row("Dining")).getByTitle(CLEAR));
    expect(h.doc.budgets).toEqual({ Groceries: 1200 });
    expect(renameBudgets(h.doc.budgets, RENAMES)).toEqual({ Groceries: 1200 });          // what data.assumptions resolves to
    expect((within(row("Dining")).getByRole("spinbutton") as HTMLInputElement).value).toBe("");
    expect(within(row("Dining")).queryByTitle(CLEAR) === null, "nothing left to clear").toBe(true);
  });

  it("typing a new value leaves one key for the category, under the new name", () => {
    render(<BudgetEditor month="2026-04" onDone={() => {}} />);
    act(() => { fireEvent.change(within(row("Dining")).getByRole("spinbutton"), { target: { value: "950" } }); });
    expect(h.doc.budgets).toEqual({ Dining: 950, Groceries: 1200 });
  });

  it("pinned categories still sort first, the old-name budget counting for the renamed one", () => {
    h.doc = withBudget(emptyPrefs(), "Eating Out", 800, NOW);
    render(<BudgetEditor month="2026-04" onDone={() => {}} />);
    const order = screen.getAllByRole("spinbutton").map((i) => i.closest("label")!.textContent);
    expect(order[0]).toMatch(/^Dining/);                                                  // Groceries has the larger typical spend
  });
});

describe("BudgetEditor without renames", () => {
  it("is the plain editor: set, show, clear", () => {
    h.doc = withBudget(emptyPrefs(), "Dining", 700, NOW);
    render(<BudgetEditor month="2026-04" onDone={() => {}} />);
    expect((within(row("Dining")).getByRole("spinbutton") as HTMLInputElement).value).toBe("700");
    fireEvent.click(within(row("Dining")).getByTitle(CLEAR));
    expect(h.doc.budgets).toEqual({});
  });
});

// The editor never lists the type-driven buckets (Internal, Income); the grid's fixed side is the essential categories.
// A rename moves their names in the taxonomy and on the rows, and both must keep working.
describe("the Budget tab after a rename of an essential or a type-driven category", () => {
  beforeEach(() => {
    h.renames = [{ from: { category: "Groceries" }, to: { category: "Food" } },
      { from: { category: "Internal" }, to: { category: "Transfers" } }, { from: { category: "Income" }, to: { category: "Earnings" } }];
    h.cashflow = months(10, 2025, 6);
    h.taxonomy = { Dining: [], Food: [], Transfers: [], Earnings: [] };
  });
  it("the editor lists the spending categories and neither renamed bucket", () => {
    render(<BudgetEditor month="2026-04" onDone={() => {}} />);
    expect(screen.getByText("Dining")).toBeDefined();
    expect(screen.queryByText("Transfers")).toBeNull();
    expect(screen.queryByText("Earnings")).toBeNull();
  });
  it("a renamed essential category is still on the fixed side of the split", () => {
    h.txns = [tx({ date: "2026-02-05", category: "Food", subcategory: "Food", amount: -400 }),
      tx({ date: "2026-02-12", category: "Dining", subcategory: "Restaurants", amount: -100 })];
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const opt = h.charts.at(-1);
    expect([opt.series[0].data.at(-1), opt.series[1].data.at(-1)]).toEqual([400, 100]);
    expect(screen.getByText(/Fixed = spending in categories essential by default \(.*Food/)).toBeDefined();
  });
});

// privacy mode: the editor's boxes hold the family's budgets, and an empty box's placeholder is the automatic baseline
// (a median of real spending) - both are amounts. A box shows its number while it is being edited.
describe("BudgetEditor under privacy mode", () => {
  beforeEach(() => { h.cashflow = months(16, 2025, 1); });             // through the editor's month, with a year of history before it: Groceries has an automatic baseline
  it("masks a set budget at rest and an automatic baseline in a placeholder, and reveals a box for editing", () => {
    h.doc = withBudget(emptyPrefs(), "Dining", 700, NOW);
    setPrivate(true);
    render(<BudgetEditor month="2026-04" onDone={() => {}} />);
    const dining = within(row("Dining")).getByDisplayValue(MASK) as HTMLInputElement;
    expect(dining.readOnly).toBe(true);
    const groceries = within(row("Groceries")).getByRole("spinbutton") as HTMLInputElement;
    expect(groceries.placeholder, "the automatic baseline is masked").toBe(MASK);
    for (const box of document.querySelectorAll<HTMLInputElement>("input[type=number]")) {
      expect(/\d/.test(box.placeholder), "no numeric placeholder at rest").toBe(false);
    }
    fireEvent.focus(dining);
    expect((within(row("Dining")).getByRole("spinbutton") as HTMLInputElement).value).toBe("700");
    act(() => { fireEvent.change(within(row("Dining")).getByRole("spinbutton"), { target: { value: "950" } }); });
    expect(h.doc.budgets).toEqual({ Dining: 950 });
  });
  it("shows the real placeholders again with privacy off", () => {
    render(<BudgetEditor month="2026-04" onDone={() => {}} />);
    expect([...document.querySelectorAll<HTMLInputElement>("input[type=number]")].some((b) => /\d/.test(b.placeholder))).toBe(true);
  });
});

// the payroll card holds a salary: gross $/yr is an amount, the 401(k) and match percentages are not
describe("the payroll card under privacy mode", () => {
  const phase = { person: "Alex", from: "2024-01", to: null, gross_annual: 250000, employee_401k_pct: 10, employer_match_pct: 4 };
  it("masks the gross salary box at rest (editable on focus) and leaves the percentages alone", () => {
    h.doc = { ...emptyPrefs(), payroll: { phases: [phase] } };
    setPrivate(true);
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const payroll = within(screen.getByTestId("card-payroll"));
    expect((payroll.getByDisplayValue(MASK) as HTMLInputElement).readOnly).toBe(true);
    expect(payroll.queryByDisplayValue("250000"), "no salary at rest").toBeNull();
    expect(payroll.getByDisplayValue("10")).toBeDefined();
    expect(payroll.getByDisplayValue("4")).toBeDefined();
    fireEvent.focus(payroll.getByDisplayValue(MASK));
    expect(payroll.getByDisplayValue("250000")).toBeDefined();
  });
  it("shows the salary with privacy off", () => {
    h.doc = { ...emptyPrefs(), payroll: { phases: [phase] } };
    render(<MemoryRouter><Budget /></MemoryRouter>);
    expect(within(screen.getByTestId("card-payroll")).getByDisplayValue("250000")).toBeDefined();
  });
});

// A new payroll phase starts in the current month. `toISOString()` is UTC, which is already next month on the last evening of a
// month in the US: the month comes from the LOCAL clock.
describe("the payroll card's new phase starts in the local month", () => {
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });
  it("on the evening of the last day of July it is 2026-07, even when the UTC clock has rolled into August", () => {
    h.doc = emptyPrefs();
    h.payrollWrites.length = 0;
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 31, 23, 30));                              // 31 July, 23:30 on this machine's clock
    // the UTC reading a US evening produces: next month. Whatever zone the test runs in, this is what a UTC read would say.
    vi.spyOn(Date.prototype, "toISOString").mockReturnValue("2026-08-01T04:30:00.000Z");
    render(<MemoryRouter><Budget /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "add payroll facts" }));
    expect(h.payrollWrites).toHaveLength(1);
    expect(h.payrollWrites[0][0].from).toBe("2026-07");
  });
});

// household.yaml's birth_year is optional: the payroll card lists the adults by name and never reads an age
describe("the payroll card with adults who have no birth year", () => {
  it("offers both adults, defaults a new phase to the first, and prints no NaN or undefined", () => {
    h.people = [{ name: "Alex", role: "owner" }, { name: "Sam", role: "owner" }, { name: "Robin", role: "kid" }];
    h.doc = { ...emptyPrefs(), payroll: { phases: [{ person: "Sam", from: "2030-01", to: null, gross_annual: 90000, employee_401k_pct: 6, employer_match_pct: 3 }] } };
    h.payrollWrites.length = 0;
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const card = screen.getByTestId("card-payroll");
    const select = within(card).getByRole("combobox") as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual(["Alex", "Sam"]);
    expect(select.value).toBe("Sam");
    expect(card.textContent).not.toMatch(/NaN|undefined/);
    fireEvent.click(within(card).getByRole("button", { name: "+ Add phase" }));
    expect(h.payrollWrites.at(-1)!.at(-1)!.person).toBe("Alex");
  });

  it("a new phase starts in the household's month, not the browser's", () => {
    // Jul 31, 20:00 UTC is already Aug 1 in Kiritimati (UTC+14), still July in the suite's UTC browser
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-07-31T20:00:00Z"));
    try {
      h.people = [{ name: "Alex", role: "owner" }];
      h.doc = emptyPrefs();
      h.payrollWrites.length = 0;
      h.timezone = "Pacific/Kiritimati";
      render(<MemoryRouter><Budget /></MemoryRouter>);
      fireEvent.click(screen.getByRole("button", { name: "add payroll facts" }));
      expect(h.payrollWrites.at(-1)!.at(-1)!.from).toBe("2026-08");
    } finally {
      h.timezone = null;
      vi.useRealTimers();
    }
  });
});

// ---------------------------------------------------------------- the grid card

const phone = (on: boolean) => {
  (window as unknown as { matchMedia: unknown }).matchMedia = (q: string) => ({
    matches: on && q.includes("max-width: 767px"), media: q, addEventListener: () => {}, removeEventListener: () => {},
  });
};
const headers = () => screen.getAllByRole("columnheader").map((c) => c.textContent);

describe("Budget tab: the variance grid", () => {
  beforeEach(() => { h.cashflow = months(10, 2025, 6); });      // 2025-06 .. 2026-03; 2026-03 is the partial month

  it("shows the last six FULL months with a Trend column - never the partial current month", () => {
    render(<MemoryRouter><Budget /></MemoryRouter>);
    expect(headers()).toEqual(["Category", "Sep 2025", "Oct 2025", "Nov 2025", "Dec 2025", "Jan 2026", "Feb 2026", "Trend"]);
    expect(headers()).not.toContain("Mar 2026");
    expect(screen.getByTestId("card-budget").textContent).toContain("Budget — last 6 full months vs typical");
  });

  it("pans three month columns on a phone, and the title says three", () => {
    phone(true);
    render(<MemoryRouter><Budget /></MemoryRouter>);
    expect(headers()).toEqual(["Category", "Dec 2025", "Jan 2026", "Feb 2026", "Trend"]);
    expect(screen.getByTestId("card-budget").textContent).toContain("last 3 full months");
  });

  it("a phone's table lists every row the six-month table does, a category that cleared the floor only earlier included", () => {
    // Shopping: 60 in Oct 2025, 49 in every other month - above the floor only in a month the phone's three do not show
    h.cashflow = months(10, 2025, 6).map((m) => ({ ...m, by_category: { ...m.by_category,
      Shopping: { total: m.month === "2025-10" ? 60 : 49, by_subcategory: {} } } }));
    const rowNames = () => [...screen.getByTestId("card-budget").querySelectorAll("tbody tr")]
      .map((r) => r.firstElementChild?.textContent?.replace(/pinned$/, ""));
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const wide = rowNames();
    expect(wide).toContain("Shopping");
    cleanup();
    phone(true);
    render(<MemoryRouter><Budget /></MemoryRouter>);
    expect(rowNames()).toEqual(wide);
  });

  it("is followed by the fixed-vs-discretionary card, then the payroll tip", () => {
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const ids = [...document.querySelectorAll("[data-testid]")].map((e) => e.getAttribute("data-testid"))
      .filter((id) => id?.startsWith("card-"));
    expect(ids).toEqual(["card-budget", "card-fixed-vs-discretionary", "card-payroll"]);
  });

  it("the split follows THIS DEVICE's essential toggles, like every other essential figure", () => {
    h.txns = [
      tx({ date: "2026-02-05", category: "Groceries", subcategory: "Food", amount: -400 }),
      tx({ date: "2026-02-12", category: "Dining", subcategory: "Restaurants", amount: -100 }),
    ];
    const lastBar = (opt: any) => [opt.series[0].data.at(-1), opt.series[1].data.at(-1)];
    const { unmount } = render(<MemoryRouter><Budget /></MemoryRouter>);
    expect(lastBar(h.charts.at(-1))).toEqual([400, 100]);
    expect(screen.getByText(/Toggle rows on Planning/)).toBeDefined();
    unmount();
    h.charts = [];
    writeEfundOverrides({ "Dining / Restaurants": true, "Groceries / Food": false });
    render(<MemoryRouter><Budget /></MemoryRouter>);
    expect(lastBar(h.charts.at(-1))).toEqual([100, 400]);
    expect(screen.getByText(/2 rows toggled on Planning/)).toBeDefined();
    expect(h.charts.at(-1).series[0].name, "the legend stops saying 'by default'").toBe("Fixed (essential as marked)");
  });

  it("a device toggle for a row nothing was spent on in these months changes nothing, and the note says so", () => {
    h.txns = [tx({ date: "2026-02-12", category: "Dining", subcategory: "Restaurants", amount: -100 })];
    writeEfundOverrides({ "Travel / Lodging": true, "Dining / Restaurants": false });      // the second is already the default
    render(<MemoryRouter><Budget /></MemoryRouter>);
    expect(screen.getByText(/Toggle rows on Planning/)).toBeDefined();
    expect(h.charts.at(-1).series[0].name).toBe("Fixed (essential by default)");
    expect(screen.queryByText(/toggled on Planning/) === null).toBe(true);
  });

  it("shows each amount with its signed variance beneath it and never tells a phone to hover", () => {
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const card = screen.getByTestId("card-budget");
    const dining = within(card).getByRole("row", { name: /Dining/ });
    const lines = within(dining).getAllByRole("cell").slice(0, 6).map((c) => c.querySelector('[aria-hidden="true"]')?.textContent ?? "");
    expect(lines.every((l) => /^[+−]\$[\d,]+$|^\$0$/.test(l)), "a signed whole-dollar variance under every month").toBe(true);
    expect(/hover/i.test(card.textContent ?? ""), "no hover instruction").toBe(false);
    expect(/smaller line under each amount/.test(card.textContent ?? "")).toBe(true);
  });

  it("a toggle made while the page is open re-splits it live (the one shared store)", () => {
    h.txns = [tx({ date: "2026-02-12", category: "Dining", subcategory: "Restaurants", amount: -100 })];
    render(<MemoryRouter><Budget /></MemoryRouter>);
    expect(h.charts.at(-1).series[0].data.at(-1)).toBe(0);
    act(() => writeEfundOverrides({ "Dining / Restaurants": true }));
    expect(h.charts.at(-1).series[0].data.at(-1)).toBe(100);
  });

  it("says 'last full month' (not '1 full months') when only one month has a baseline yet", () => {
    h.cashflow = months(3);                          // 2025-12, 2026-01, 2026-02: two full months, only 2026-01 has an earlier one
    render(<MemoryRouter><Budget /></MemoryRouter>);
    expect(headers()).toEqual(["Category", "Jan 2026", "Trend"]);
    expect(screen.getByTestId("card-budget").textContent).toContain("Budget — last full month vs typical");
    expect(screen.getByTestId("card-budget").textContent).not.toMatch(/1 full months/);
  });

  it("explains a single full month has no baseline yet, under a title that says there is one", () => {
    h.cashflow = months(2);                          // one full month, then the partial one
    h.txns = [tx({ date: "2025-12-10", category: "Dining", subcategory: "Restaurants", amount: -80 })];
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const card = screen.getByTestId("card-budget");
    expect(card.querySelector("h2")?.textContent).toBe("Budget check — one full month so far");
    expect(card.textContent).toMatch(/at least two full months/);
    expect(card.textContent).toMatch(/one full month has no baseline yet/);
    // the split under it waits for what the grid waits for, never for "a full month" the household already has
    const split = screen.getByTestId("card-fixed-vs-discretionary").textContent ?? "";
    expect(split).toContain("once two full months are in");
    expect(split).not.toMatch(/has a full month/);
  });

  it("no full month at all: not enough history yet, and no word about one full month", () => {
    h.cashflow = [cfm({ month: "2026-04" })];        // the ingest month, in progress, alone
    h.txns = [tx({ date: "2026-04-02", category: "Dining", subcategory: "Restaurants", amount: -10 })];
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const card = screen.getByTestId("card-budget");
    expect(card.querySelector("h2")?.textContent).toBe("Budget check — no full month of cash-flow data yet");
    expect(within(card).getByTestId("not-enough-history").textContent).toBe("Not enough history yet — each full month is "
      + "compared with the median of the months before it (or your pinned budget), so the grid appears once two full months "
      + "are in.");
    expect(card.textContent).not.toMatch(/one full month/);
  });

  it("no transaction at all: the card says so in the shared long line, the split after it in the short one", () => {
    h.cashflow = [];
    h.txns = [];
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const card = screen.getByTestId("card-budget");
    expect(card.querySelector("h2")?.textContent).toBe("Budget check");
    expect(within(card).getByTestId("no-transactions").textContent).toBe(NO_TRANSACTIONS);
    expect(card.textContent, "no claim about full months there are none of").not.toMatch(/full month/);
    expect(within(card).getByRole("button", { name: "Edit budgets" })).toBeDefined();
    expect(screen.getByTestId("card-fixed-vs-discretionary").textContent).toBe(`Fixed vs discretionary spend${NO_TRANSACTIONS_YET}`);
  });

  it("when months exist but no category reaches the floor it says that instead; the split it still has is drawn", () => {
    h.cashflow = Array.from({ length: 4 }, (_, i) => cfm({ month: `2026-0${i + 1}`, by_category: { Tiny: { total: 10, by_subcategory: {} } } }));
    h.txns = [tx({ date: "2026-02-10", category: "Dining", subcategory: "Restaurants", amount: -10 }),
      tx({ date: "2026-03-10", category: "Dining", subcategory: "Restaurants", amount: -10 })];
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const card = screen.getByTestId("card-budget");
    expect(card.querySelector("h2")?.textContent, "full months there are: no claim of none").toBe("Budget check");
    expect(card.textContent).toMatch(/No category reached \$50 a month/);
    expect(card.textContent).not.toMatch(/at least two full months/);
    // the split sums every spend transaction of the grid's months, whatever the row floor: it is drawn
    const split = screen.getByTestId("card-fixed-vs-discretionary");
    expect(within(split).getByTestId("chart")).toBeDefined();
    expect(h.charts.at(-1).series[1].data).toEqual([10, 10]);
  });

  it("the grid's months with no spending in them: the split says so, with no chart", () => {
    h.cashflow = Array.from({ length: 4 }, (_, i) => cfm({ month: `2026-0${i + 1}`, by_category: { Tiny: { total: 10, by_subcategory: {} } } }));
    h.txns = [tx({ date: "2026-02-28", category: "Income", subcategory: "Salary", type: "income", amount: 5000 })];
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const split = screen.getByTestId("card-fixed-vs-discretionary");
    expect(split.textContent).toBe("Fixed vs discretionary spendNo spending in these months yet.");
    expect(within(split).queryByTestId("chart")).toBeNull();
  });

  it("keeps the empty-state title (and the editor) when there is no full month to show; the split says why", () => {
    h.cashflow = [];
    h.txns = [tx({ date: "2026-04-02", category: "Dining", subcategory: "Restaurants", amount: -10 })];
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const card = screen.getByTestId("card-budget");
    expect(card.textContent).toContain("Budget check — no full month of cash-flow data yet");
    const split = screen.getByTestId("card-fixed-vs-discretionary");
    expect(split.textContent).toBe("Fixed vs discretionary spend"
      + "Not enough history yet — fixed and discretionary spend appear month by month with the budget grid above, once two full months are in.");
    expect(within(split).queryByTestId("chart"), "no chart without a grid").toBeNull();
    expect(h.charts.length, "no chart option built").toBe(0);
    fireEvent.click(within(card).getByRole("button", { name: "Edit budgets" }));
    expect(screen.getByTestId("card-budget").textContent).toContain("Edit monthly budgets");
  });

  it("with no transaction at all the split says the shared no-transactions line", () => {
    h.cashflow = [];
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const split = screen.getByTestId("card-fixed-vs-discretionary");
    expect(within(split).getByTestId("no-transactions").textContent).toMatch(/^No transactions yet\b/);
    expect(within(split).queryByTestId("chart")).toBeNull();
  });

  it("Edit budgets swaps the grid for the editor (no chart card meanwhile) and Done swaps it back", () => {
    render(<MemoryRouter><Budget /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "Edit budgets" }));
    expect(screen.getByTestId("card-budget").textContent).toContain("Edit monthly budgets");
    expect(screen.queryByTestId("card-fixed-vs-discretionary") === null).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /Done/ }));
    expect(screen.getByTestId("card-budget").textContent).toContain("full months vs typical");
    expect(screen.getByTestId("card-fixed-vs-discretionary")).toBeDefined();
  });

  it("stays behind RequireData(cashflow_monthly.json): a missing export shows the missing-data card and no grid", () => {
    h.missing = new Set(["cashflow_monthly.json"]);
    render(<MemoryRouter><Budget /></MemoryRouter>);
    expect(screen.getByTestId("missing-data")).toBeDefined();
    for (const id of ["card-budget", "card-fixed-vs-discretionary", "card-payroll"]) {
      expect(screen.queryByTestId(id) === null, id).toBe(true);
    }
  });

  it("a pinned budget (as DataContext publishes it, renames applied) marks its row 'pinned' and is every cell's baseline", () => {
    h.budgets = { Dining: 700 };
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const dining = screen.getByRole("row", { name: /Dining/ });
    expect(within(dining).getByText("pinned")).toBeDefined();
    expect(within(dining).getAllByRole("cell").slice(0, 6).every((c) => /^baseline \$700 · /.test(c.getAttribute("title") ?? ""))).toBe(true);
    expect(within(screen.getByRole("row", { name: /Groceries/ })).queryByText("pinned") === null).toBe(true);
  });
});

// A vendor rule's split moved the Groceries and Shopping rows, and only the register explained it. The grid says so
// under itself - and says nothing when no category it shows holds a part.
describe("Budget tab: the split-rule footnote", () => {
  const split = () => {
    const parent = tx({ id: "sp1", date: "2026-01-10", category: "Shopping", amount: -100, is_parent: true, vendor: "Synth Mart" });
    const parts = [
      tx({ id: "sp1a", date: "2026-01-10", category: "Shopping", amount: -80, parent_id: "sp1", category_source: "split", vendor: "Synth Mart" }),
      tx({ id: "sp1b", date: "2026-01-10", category: "Groceries", amount: -20, parent_id: "sp1", category_source: "split", vendor: "Synth Mart" }),
    ];
    h.splitParents = new Map([[parent.id, parent]]);
    h.children = new Map([[parent.id, parts]]);
  };
  it("is present under the grid when a category it shows (Groceries) has parts, with the share and a link to Data", () => {
    split();
    render(<MemoryRouter><Budget /></MemoryRouter>);
    const note = screen.getByTestId("split-note");
    expect(note.textContent).toMatch(/^Split rule: Groceries includes 20% of each Synth Mart order\./);
    expect(note.querySelector("a")?.getAttribute("href")).toBe("#/data");
    expect(screen.getByTestId("card-budget").contains(note), "inside the budget card").toBe(true);
  });
  it("is absent when no split exists", () => {
    render(<MemoryRouter><Budget /></MemoryRouter>);
    expect(screen.queryByTestId("split-note")).toBeNull();
  });
  it("is absent when the split's categories are not on the grid", () => {
    split();
    h.cashflow = months(4).map((m) => ({ ...m, by_category: { Dining: m.by_category.Dining } }));
    render(<MemoryRouter><Budget /></MemoryRouter>);
    expect(screen.queryByTestId("split-note")).toBeNull();
  });
});

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TAP } from "../components/ui";
import type { Transaction } from "../data/types";
import { txFactory } from "../lib/__fixtures__";
import { fmtMonthAxis } from "../lib/format";
import { uuid4 } from "../lib/ids";
import { emptyPrefs, MAX_SAVED, type SavedReport, tryWithSaved, withSaved } from "../lib/prefs";
import { DEFAULT_SPEC } from "../lib/reportSpec";

// the chart is stubbed (jsdom has no canvas) and records what it was handed; the FilterBar is its own tested
// component, so a stand-in keeps this file about the tab's own wiring (URL <-> spec, the table, saving)
const chart = vi.hoisted(() => ({ props: null as null | { option: any; tableData?: any; height?: number; csvDisabled?: boolean } }));
vi.mock("../components/EChart", () => ({
  default: (p: { option: unknown; tableData?: unknown; height?: number }) => { chart.props = p as never; return <div data-testid="chart" />; },
}));
vi.mock("../components/FilterBar", () => ({
  default: (p: { canSaveView?: boolean }) => <div data-testid="filterbar" data-can-save-view={String(p.canSaveView)} />,
}));

// useDeferredValue can be held back on the report spec (React would hand the previous one for a beat while it
// catches up; jsdom's act() never lets that show, so a test freezes it to see the in-between frame)
const defer = vi.hoisted(() => ({ freeze: false, spec: undefined as unknown }));
vi.mock("react", async (orig) => {
  const r = await orig<typeof import("react")>();
  return { ...r, useDeferredValue: <T,>(v: T): T =>
    (defer.freeze && v && typeof v === "object" && "groupBy" in v ? (defer.spec ??= v) as T : v) };
});

// the save preview must not read the clock (it renders on every keystroke and every spec change): any read of the
// app's one clock (lib/clock.ts) while the box renders is a failure
const reads = vi.hoisted(() => ({ calls: 0 }));
vi.mock("../lib/clock", async (orig) => {
  const { clock } = await orig<typeof import("../lib/clock")>();
  const counted = <A extends unknown[], R>(read: (...a: A) => R) => (...a: A): R => { reads.calls++; return read(...a); };
  return { clock: { now: counted(clock.now), nowISO: counted(clock.nowISO), today: counted(clock.today) } };
});

const tx = txFactory({ type: "purchase" });
let txns: Transaction[] = [];
let reports: SavedReport[] = [];
let categoryList = ["Dining", "Groceries"];
const splits = vi.hoisted(() => ({ parents: new Map<string, unknown>(), children: new Map<string, unknown[]>() }));
const saveReport = vi.fn((_name: string, _spec: unknown) => true);
vi.mock("../data/DataContext", () => ({
  useData: () => ({
    data: { transactions: txns, purchases: txns, accounts: [], assumptions: { category_renames: [] }, splitParents: splits.parents,
            cashflow: [], manifest: { generated_at: "2026-07-20T20:00:00-05:00" } },
    indexes: { categoryList, dataRange: { first: "2025-01-01", last: "2026-07-20" }, dataThrough: "2026-07-20",
      childrenByParent: splits.children },
    prefs: { doc: { ...emptyPrefs(), reports }, saveReport, localOnly: false },
  }),
}));

import Reports from "./Reports";
import { Where, where } from "../test/routerProbe";

const ROWS = [
  tx({ date: "2026-01-01", category: "Dining", vendor: "Cafe", amount: -100 }),
  tx({ date: "2026-03-09", category: "Groceries", vendor: "Market", amount: -300 }),
  tx({ date: "2026-04-14", category: "Dining", vendor: "Cafe", amount: -20 }),
];
beforeEach(() => {
  txns = ROWS; reports = []; categoryList = ["Dining", "Groceries"]; chart.props = null;
  splits.parents.clear(); splits.children.clear();
  saveReport.mockClear(); saveReport.mockReturnValue(true);
  defer.freeze = false; defer.spec = undefined;
});
afterEach(cleanup);

type Entry = string | { pathname: string; search: string; state?: unknown };
const open = (entry: Entry) => render(<MemoryRouter initialEntries={[entry]}><Reports /><Where /></MemoryRouter>);
/** a From date (a filter key, so the tab is not a fresh visit) and the report most cases are written against - spending by
 *  category, per month, stacked - with `over`'s keys replacing its own */
const at = (over = "") => {
  const sp = new URLSearchParams("from=2026-01-01&groupBy=category&interval=month&chart=stacked");
  for (const [k, v] of new URLSearchParams(over)) sp.set(k, v);
  return `/spending/reports?${sp.toString()}`;
};

describe("Spending → Reports", () => {
  it("the URL is the spec: it fills the pickers, names itself in the result title and feeds the chart", () => {
    open("/spending/reports?categories=Dining&from=2026-01-01&groupBy=vendor&interval=quarter&measure=count&chart=stacked");
    expect((screen.getByLabelText("Group by") as HTMLSelectElement).value).toBe("vendor");
    expect((screen.getByLabelText("Interval") as HTMLSelectElement).value).toBe("quarter");
    expect((screen.getByLabelText("Measure") as HTMLSelectElement).value).toBe("count");
    const result = screen.getByTestId("card-report-result");
    expect(result.textContent).toContain("transactions (count) by vendor, per quarter — 2 periods");
    expect(chart.props!.option.series.map((s: any) => s.name)).toEqual(["Cafe"]);
    expect(chart.props!.option.series[0].data).toEqual([1, 1]);            // Q1 (Jan) and Q2 (Apr), one Dining row each
    expect(chart.props!.tableData.csvName).toBe("report-count-by-vendor-per-quarter.csv");
    expect(chart.props!.tableData.label).toBe("transactions (count) by vendor, per quarter");
    expect(screen.getByTestId("card-report-builder")).toBeDefined();
  });

  it("filters the compact bar hides (type, amount, direction) still apply when they arrive in the URL, as the footnote says", () => {
    txns = [tx({ date: "2026-01-02", category: "Dining", amount: -100 }), tx({ date: "2026-01-03", category: "Pay", amount: 500, type: "income" }),
      tx({ date: "2026-01-04", category: "Pay", amount: 700, type: "income" })];
    open("/spending/reports?types=income&amountMin=0&min=600&measure=count&from=2026-01-01&groupBy=category&interval=month&chart=stacked");
    expect(chart.props!.option.series.map((s: any) => [s.name, s.data])).toEqual([["Pay", [1]]]);     // income only, and only the 700 row
  });

  it("an unknown or missing report key beside others reads the link baseline (category, month) instead of crashing", () => {
    open("/spending/reports?from=2026-01-01&groupBy=merchant&interval=fortnight");
    expect((screen.getByLabelText("Group by") as HTMLSelectElement).value).toBe("category");
    expect((screen.getByLabelText("Interval") as HTMLSelectElement).value).toBe("month");
    expect(screen.getByTestId("card-report-result").textContent).toContain("spending by category, per month");
  });

  it("an older link naming only ?interval=quarter keeps opening spending by category per quarter, as stacked bars", () => {
    open("/spending/reports?from=2026-01-01&interval=quarter");
    expect(screen.getByTestId("card-report-result").textContent).toContain("spending by category, per quarter");
    expect(chart.props!.option.series.every((s: any) => s.type === "bar" && s.stack === "report"), "stacked bars").toBe(true);
  });

  it("a bare Reports tab is spending by vendor, per month, as a table - not Explore's category-by-month chart", () => {
    open("/spending/reports?from=2026-01-01");
    expect(screen.queryByTestId("chart"), "a table, no chart").toBeNull();
    const table = screen.getByRole("table", { name: /Report: spending by vendor, per month/ });
    const rows = within(table).getAllByRole("row").map((r) => [...r.querySelectorAll("th, td")].map((c) => c.textContent));
    expect(rows[0]).toEqual(["Period", "Market", "Cafe"]);
    expect(rows.slice(1).map((r) => r[0])).toEqual(["Jan 2026", "Feb 2026", "Mar 2026", "Apr 2026*"]);
  });

  it("choosing a value writes all four report keys (the one changed, the rest as on screen), keeps the filters and any page key, and stamps the marker", () => {
    open("/spending/reports?categories=Dining&sort=date%3Aasc");
    fireEvent.change(screen.getByLabelText("Group by"), { target: { value: "account" } });
    expect(where()).toBe('/spending/reports?categories=Dining&sort=date%3Aasc&groupBy=account&interval=month&measure=spend&chart=table|{"carriedSearch":true}');
    fireEvent.change(screen.getByLabelText("Measure"), { target: { value: "net" } });
    fireEvent.click(screen.getByRole("button", { name: "lines" }));
    expect(where()).toBe('/spending/reports?categories=Dining&sort=date%3Aasc&groupBy=account&interval=month&measure=net&chart=line|{"carriedSearch":true}');
    fireEvent.change(screen.getByLabelText("Group by"), { target: { value: "vendor" } });
    expect(where(), "a value equal to the default is still written").toBe(
      '/spending/reports?categories=Dining&sort=date%3Aasc&groupBy=vendor&interval=month&measure=net&chart=line|{"carriedSearch":true}');
  });

  it("every report-key writer carries the marker, so Back onto an entry with no filter keys is not read as a fresh visit", () => {
    open("/spending/reports?categories=Dining");
    for (const [label, value] of [["Group by", "owner"], ["Interval", "week"], ["Measure", "income"]] as const) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
      expect(where().endsWith('|{"carriedSearch":true}'), `${label} stamps the marker`).toBe(true);
    }
    fireEvent.click(screen.getByRole("button", { name: "table" }));
    expect(where().endsWith('|{"carriedSearch":true}'), "the chart toggle stamps the marker").toBe(true);
  });

  it("a fresh visit gets the six-month default beside its report keys; a marker-carrying entry keeps its own query", async () => {
    open({ pathname: "/spending/reports", search: "?groupBy=vendor" });
    await waitFor(() => expect(where()).toContain("from=2026-01-20"));
    expect(where()).toContain("groupBy=vendor");
    cleanup();
    open({ pathname: "/spending/reports", search: "?groupBy=vendor", state: { carriedSearch: true } });
    await new Promise((r) => setTimeout(r, 30));
    expect(where()).toBe('/spending/reports?groupBy=vendor|{"carriedSearch":true}');
  });

  it("the table chart shows a real table: a partial period labelled, a month with no transaction at all a dash, a real zero a 0; no chart is drawn", () => {
    open(at("chart=table"));
    expect(screen.queryByTestId("chart")).toBeNull();
    const table = screen.getByRole("table", { name: /Report: spending by category, per month/ });
    const rows = within(table).getAllByRole("row").map((r) => [...r.querySelectorAll("th, td")].map((c) => c.textContent));
    expect(rows[0]).toEqual(["Period", "Groceries", "Dining"]);
    expect(rows[1]).toEqual(["Jan 2026", "$0", "$100"]);             // the data was alive in January: a real zero for Groceries
    expect(rows[2]).toEqual(["Feb 2026", "—", "—"]);                 // no transaction anywhere in February: unknown
    expect(rows[4]).toEqual(["Apr 2026*", "$0", "$20"]);             // the newest data is the 14th: starred, as the line under it says
    expect(screen.getByTestId("card-report-result").textContent, "the line names the partial period as the table does")
      .toContain("partial period (Apr 2026*)");
    expect(screen.getByRole("button", { name: /CSV/ })).toBeDefined();
  });

  it("a line or stacked chart goes through EChart with the table behind 'view data'", () => {
    open(at("chart=line"));
    expect(chart.props!.option.series.every((s: any) => s.type === "line")).toBe(true);
    expect(chart.props!.tableData.columns).toEqual(["Period", "Groceries", "Dining"]);
    expect(chart.props!.height).toBe(360);
    cleanup();
    open(at());
    expect(chart.props!.option.series.every((s: any) => s.type === "bar" && s.stack === "report")).toBe(true);
  });

  it("names a partial period under the result by its axis label (Apr \u201926*), and says nothing when every period is complete", () => {
    open(at());
    const note = screen.getByTestId("card-report-result").textContent ?? "";
    expect(note).toContain(`partial period (${fmtMonthAxis("2026-04")}*)`);
    expect(note).not.toContain("2026-04");                            // the raw key is not what the chart shows
    cleanup();
    open(at("to=2026-03-31"));
    expect(screen.getByTestId("card-report-result").textContent).not.toContain("partial period");
  });

  it("names both edges when the data starts and ends inside a period, each starred as the axis shows it", () => {
    txns = [tx({ date: "2026-01-10", amount: -5 }), tx({ date: "2026-03-20", amount: -5 })];
    open("/spending/reports?q=&groupBy=category&interval=month&chart=stacked");
    const note = screen.getByTestId("card-report-result").textContent ?? "";
    expect(note).toContain(`partial periods (${fmtMonthAxis("2026-01")}*, ${fmtMonthAxis("2026-03")}*)`);
    expect(note).toContain("start or end of the data");
    const labels: string[] = chart.props!.option.xAxis.data;
    expect([labels[0], labels[2]]).toEqual([`${fmtMonthAxis("2026-01")}*`, `${fmtMonthAxis("2026-03")}*`]);
  });

  it("an empty result says so instead of drawing an empty chart or a zero table", () => {
    open(at("measure=income"));
    expect(screen.getByTestId("card-report-result").textContent).toContain("No rows match these filters for this measure.");
    expect(screen.getByTestId("card-report-result").textContent).toContain("0 periods");
    expect(screen.queryByTestId("chart")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("defines the chosen measure in words, and the footnote states the empty-period rule", () => {
    open(at("measure=net"));
    const builder = screen.getByTestId("card-report-builder").textContent ?? "";
    expect(builder).toContain("Net = income − spend");
    expect(builder).toContain("no transaction");
    expect(builder).toContain("real 0");
    // the compact bar has no transfers checkbox, so the note must not tell anyone to tick it; it points at
    // Transactions and says the filters this bar hides still apply when carried in
    expect(builder).not.toContain("ticked");
    expect(builder).toContain("turned on there");
    expect(builder).toMatch(/type, amount, direction and\s+transfers between your own accounts/);
    expect(builder).not.toMatch(/\binternal\b/);
    expect(builder).toContain("still apply when carried in from Transactions");
    fireEvent.change(screen.getByLabelText("Measure"), { target: { value: "spend" } });
    expect(screen.getByTestId("card-report-builder").textContent).toContain("refunds netted");
  });

  it("phone inputs: 16px text and 40px boxes below md, for the pickers, the name box and the buttons", () => {
    open(at());
    for (const l of ["Group by", "Interval", "Measure"]) {
      const cls = screen.getByLabelText(l).className;
      expect(cls, l).toContain("min-h-[40px]");
      expect(cls, l).toContain("text-base");
      expect(cls, l).toContain("sm:text-xs");
    }
    expect(screen.getByRole("button", { name: "★ Save report" }).className).toContain("min-h-[40px]");
    fireEvent.click(screen.getByRole("button", { name: "★ Save report" }));
    expect(screen.getByLabelText("Report name").className).toContain("text-base sm:text-xs");
    expect(screen.getByRole("button", { name: "Save" }).className).toContain("min-h-[40px]");
  });
});

// The enum tokens are URL and storage values; what a person reads is a label map: "earner" is the income earner, "net" says
// what it nets, "count" says it counts purchases - in the pickers, the card title, the view-data caption and the saved list.
describe("Spending → Reports: the words, not the tokens", () => {
  it("the pickers show the label map's words and keep the tokens as values", () => {
    open(at());
    const options = (label: string) => [...(screen.getByLabelText(label) as HTMLSelectElement).options].map((o) => [o.value, o.textContent]);
    expect(options("Group by")).toContainEqual(["earner", "income earner"]);
    expect(options("Measure")).toEqual([["spend", "spending"], ["income", "income"], ["net", "net (income − spend)"], ["count", "transactions (count)"]]);
    expect(options("Interval").map((o) => o[0])).toEqual(["week", "month", "quarter", "year"]);
  });
  it("the title and the table caption name the report in words, never 'spend by earner'", () => {
    open(at("groupBy=earner&measure=net&chart=table"));
    const result = screen.getByTestId("card-report-result").textContent ?? "";
    expect(result).toContain("net (income − spend) by income earner, per month");
    expect(result).not.toMatch(/\bby earner\b/);
    expect(screen.getByRole("table", { name: /Report: net \(income − spend\) by income earner, per month/ })).toBeDefined();
  });
  it("the chart toggle reads stacked bars / lines / table", () => {
    open(at());
    for (const name of ["stacked bars", "lines", "table"]) expect(screen.getByRole("button", { name }), name).toBeDefined();
  });
  it("hides the FilterBar's '★ Save view' (a view is filters alone and opens Transactions; the report box saves the filters AND the report)", () => {
    open(at());
    expect(screen.getByTestId("filterbar").getAttribute("data-can-save-view")).toBe("false");
  });
});

describe("Spending → Reports: the explanatory note", () => {
  it("is secondary text a reader can read (zinc-400 at 11px), not the faintest tint (zinc-600 at 10px read at about 2.6:1)", () => {
    open(at());
    const note = screen.getByText(/^rows = the filters above/);
    expect(note.className).toContain("text-zinc-400");
    expect(note.className).toContain("text-[11px]");
    expect(note.className).not.toContain("text-zinc-600");
  });
});

describe("Spending → Reports: saving", () => {
  const openBox = () => fireEvent.click(screen.getByRole("button", { name: "★ Save report" }));
  const name = () => screen.getByLabelText("Report name") as HTMLInputElement;
  const save = () => screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;

  it("is the FilterBar's Save box: a button opens a name field with Save and cancel, and nothing is shown until it is pressed", () => {
    open(at());
    expect(screen.queryByLabelText("Report name"), "closed until asked").toBeNull();
    openBox();
    expect(name().placeholder).toBe("name this report");
    expect(screen.getByRole("button", { name: "cancel" })).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "cancel" }));
    expect(screen.queryByLabelText("Report name")).toBeNull();
    openBox();
    fireEvent.keyDown(name(), { key: "Escape" });
    expect(screen.queryByLabelText("Report name"), "Escape cancels").toBeNull();
    expect(saveReport).not.toHaveBeenCalled();
  });

  it("saves the spec in the URL under the typed name, then closes the box and says so", () => {
    open("/spending/reports?categories=Dining&from=2026-03-01&groupBy=vendor&measure=net");
    openBox();
    expect(save().disabled, "nothing to save without a name").toBe(true);
    fireEvent.change(name(), { target: { value: "Dining vendors" } });
    expect(save().disabled).toBe(false);
    fireEvent.submit(name().closest("form")!);
    expect(saveReport).toHaveBeenCalledWith("Dining vendors", {
      filters: "categories=Dining&from=2026-03-01", groupBy: "vendor", interval: "month", measure: "net", chart: "stacked" });
    expect(screen.queryByLabelText("Report name"), "back to the button").toBeNull();
    expect(screen.getByRole("status").textContent).toContain("saved ✓");
  });

  // A fresh visit writes the six-month `from` into the URL itself; a report saved from it used to freeze that absolute date. A
  // window that is a preset is now stored as a rolling `win`; dates the user picked stay dates.
  it("a report saved from a fresh visit stores win=6m, not the dates the page wrote (it keeps rolling)", () => {
    open("/spending/reports?groupBy=vendor");                         // no filter key: a fresh visit, the default window is written
    expect(where()).toContain("from=2026-01-20");
    openBox();
    fireEvent.change(name(), { target: { value: "Fresh" } });
    fireEvent.submit(name().closest("form")!);
    expect(saveReport).toHaveBeenCalledWith("Fresh", { filters: "win=6m", groupBy: "vendor", interval: "month", measure: "spend", chart: "stacked" });
  });

  it("a 12M window is stored as win=12m", () => {
    open("/spending/reports?from=2025-07-20&categories=Dining");        // 12 months before the data's end (2026-07-20)
    openBox();
    fireEvent.change(name(), { target: { value: "Year" } });
    fireEvent.submit(name().closest("form")!);
    expect(saveReport).toHaveBeenLastCalledWith("Year", expect.objectContaining({ filters: "categories=Dining&win=12m" }));
  });

  it("dates the user chose are kept as dates: another start date, or an end date", () => {
    open("/spending/reports?from=2026-03-01&groupBy=vendor");
    openBox();
    fireEvent.change(name(), { target: { value: "Own start" } });
    fireEvent.submit(name().closest("form")!);
    expect(saveReport).toHaveBeenLastCalledWith("Own start", expect.objectContaining({ filters: "from=2026-03-01" }));
    cleanup();
    open("/spending/reports?from=2026-01-20&to=2026-05-31&groupBy=vendor");
    openBox();
    fireEvent.change(name(), { target: { value: "Bounded" } });
    fireEvent.submit(name().closest("form")!);
    expect(saveReport).toHaveBeenLastCalledWith("Bounded", expect.objectContaining({ filters: "from=2026-01-20&to=2026-05-31" }));
  });

  it("a saved rolling report opens with its window resolved against the data's end, and `win` gone from the URL", () => {
    open({ pathname: "/spending/reports", search: "?win=6m&groupBy=vendor", state: { carriedSearch: true } });
    expect(where()).toBe("/spending/reports?groupBy=vendor&from=2026-01-20|{\"carriedSearch\":true}");
  });

  it("the save preview reads no clock: it measures a constant stamp as long as a real one", () => {
    reads.calls = 0;
    open("/spending/reports?categories=Dining&groupBy=vendor");
    openBox();
    fireEvent.change(name(), { target: { value: "Dining vendors" } });
    fireEvent.change(name(), { target: { value: "Dining vendors 2" } });
    expect(reads.calls, "the clock is not read while the box renders").toBe(0);
    expect(save().disabled).toBe(false);
  });

  it("the name box takes at most 80 characters", () => {
    open(at());
    openBox();
    expect(name().maxLength).toBe(80);
  });

  it("a save the store refuses after the fact keeps the name and says so (never claims it saved)", () => {
    saveReport.mockReturnValue(false);
    open(at());
    openBox();
    fireEvent.change(name(), { target: { value: "raced" } });
    fireEvent.submit(name().closest("form")!);
    expect(screen.getByRole("alert").textContent).toContain("couldn't save this report");
    expect(screen.queryByRole("status")).toBeNull();
    expect(name().value).toBe("raced");
  });

  it("at the 50-report cap it says so as soon as a name is typed, and Save is off", () => {
    let doc = emptyPrefs();
    for (let i = 0; i < MAX_SAVED; i++) doc = withSaved(doc, "reports", { id: `r${i}`, name: `r${i}`, spec: DEFAULT_SPEC }, "2026-01-01T00:00:00Z")!;
    reports = doc.reports;
    open(at());
    openBox();
    fireEvent.change(name(), { target: { value: "one more" } });
    expect(screen.getByRole("alert").textContent).toBe("50 reports saved — delete one on Data first");
    expect(save().disabled).toBe(true);
  });

  it("a spec too long to store says so before the click", () => {
    open(`/spending/reports?q=${"x".repeat(1990)}`);
    openBox();
    fireEvent.change(name(), { target: { value: "huge" } });
    expect(screen.getByRole("alert").textContent).toBe("this report is too long to save");
    expect(save().disabled).toBe(true);
  });

  it("the pre-check agrees with the real save at the byte boundary: an id as long as a uuid, not a short placeholder", () => {
    const filters = (n: number) => `q=${"x".repeat(n)}`;
    // the longest filters a REAL save (a fresh uuid id, the name "x") accepts, found with the same op the store runs
    const real = (n: number) => typeof tryWithSaved(emptyPrefs(), "reports",
      { id: uuid4(), name: "x", spec: { ...DEFAULT_SPEC, filters: filters(n) } }, new Date().toISOString()) !== "string";
    let n = 1500;                                  // comfortably under the 2 KB entry cap
    while (real(n + 1)) n++;
    expect(real(n) && !real(n + 1), "found the boundary").toBe(true);
    open(`/spending/reports?${filters(n)}`);
    openBox();
    fireEvent.change(name(), { target: { value: "x" } });
    expect(screen.queryByRole("alert"), "the longest spec the real save takes is not refused in advance").toBeNull();
    expect(save().disabled).toBe(false);
    cleanup();
    open(`/spending/reports?${filters(n + 1)}`);
    openBox();
    fireEvent.change(name(), { target: { value: "x" } });
    expect(screen.getByRole("alert").textContent, "one more character is refused in advance, as the real save would").toBe("this report is too long to save");
    expect(save().disabled).toBe(true);
  });
});

describe("Spending → Reports: saved reports on the tab", () => {
  const report = (id: string, nm: string, filters: string, over: Partial<SavedReport["spec"]> = {}): SavedReport =>
    ({ id, name: nm, spec: { ...DEFAULT_SPEC, filters, ...over }, created_at: "2026-01-01T00:00:00Z" });

  it("offers nothing when the family has saved no report", () => {
    open(at());
    expect(screen.queryByTestId("saved-reports")).toBeNull();
  });

  it("lists the valid reports as links that open them exactly as saved, with the carried-search marker; a stale one is only counted, pointing at Data", () => {
    reports = [report("r1", "Dining by vendor", "categories=Dining", { groupBy: "vendor", interval: "month", chart: "line" }),
      report("r0", "Everything", ""), report("rs", "Late-night", "categories=Nightlife")];
    open(at());
    const menu = screen.getByTestId("saved-reports");
    expect(menu.textContent).toContain("Saved reports · 2");
    expect(within(menu).queryByText("Late-night")).toBeNull();
    expect(menu.textContent).toContain("1 stale report — see Data");
    expect(within(menu).getByRole("link", { name: "1 stale report — see Data" }).getAttribute("href")).toBe("/data");
    fireEvent.click(within(menu).getByRole("link", { name: "Dining by vendor" }));
    expect(where()).toBe('/spending/reports?categories=Dining&groupBy=vendor&interval=month&measure=spend&chart=line|{"carriedSearch":true}');
    fireEvent.click(within(screen.getByTestId("saved-reports")).getByRole("link", { name: "Everything" }));
    expect(where()).toBe('/spending/reports?groupBy=vendor&interval=month&measure=spend&chart=table|{"carriedSearch":true}');       // all-time: the marker stops the default window
    expect((screen.getByLabelText("Group by") as HTMLSelectElement).value).toBe("vendor");
  });

  it("every link, the summary and the stale pointer are the app's 44px tap targets below md", () => {
    reports = [report("r1", "Dining by vendor", "categories=Dining"), report("rs", "Late-night", "categories=Nightlife")];
    open(at());
    const menu = screen.getByTestId("saved-reports");
    const tap = (el: Element) => TAP.split(" ").every((c) => el.className.split(/\s+/).includes(c));
    expect(tap(within(menu).getByRole("link", { name: "Dining by vendor" }))).toBe(true);
    expect(tap(within(menu).getByRole("link", { name: /stale report/ }))).toBe(true);
    expect(tap(menu.querySelector("summary")!)).toBe(true);
  });
});

describe("Spending → Reports while the report catches up with the URL", () => {
  it("the CSV is not offered until the report on screen is the spec in the URL (table and chart alike)", () => {
    defer.freeze = true;                                                       // the report stays on the first spec
    open(at("chart=table"));
    const csv = () => screen.getByRole("button", { name: /Download CSV/ }) as HTMLButtonElement;
    expect(csv().disabled, "caught up: the CSV is the table on screen").toBe(false);
    fireEvent.change(screen.getByLabelText("Measure"), { target: { value: "count" } });
    expect(csv().disabled, "the URL moved on, the table has not").toBe(true);
    cleanup();
    defer.spec = undefined;
    open(at());
    expect(chart.props!.csvDisabled).toBe(false);
    fireEvent.change(screen.getByLabelText("Group by"), { target: { value: "vendor" } });
    expect(chart.props!.csvDisabled).toBe(true);
  });
  it("without the hold the CSV is always offered (act() lets the deferred render finish)", () => {
    open(at("chart=table"));
    fireEvent.change(screen.getByLabelText("Measure"), { target: { value: "count" } });
    expect((screen.getByRole("button", { name: /Download CSV/ }) as HTMLButtonElement).disabled).toBe(false);
  });
});

// A vendor rule's split parts sit inside the Groceries / Shopping bars of a by-category report: the same one-line
// footnote as Explore, Budget, the income statement and the emergency-fund card.
describe("Spending → Reports: the split-rule footnote", () => {
  const withSplit = () => {
    const parent = tx({ id: "sp", date: "2026-03-09", category: "Shopping", amount: -100, is_parent: true, vendor: "Synth Mart" });
    const parts = [
      tx({ id: "spa", date: "2026-03-09", category: "Shopping", subcategory: "Amazon", amount: -80, parent_id: "sp", category_source: "split", vendor: "Synth Mart" }),
      tx({ id: "spb", date: "2026-03-09", category: "Groceries", subcategory: "Food", amount: -20, parent_id: "sp", category_source: "split", vendor: "Synth Mart" }),
    ];
    splits.parents.set("sp", parent); splits.children.set("sp", parts);
    txns = [...ROWS, ...parts];
  };
  it("is present when grouped by category and a shown category holds a part", () => {
    withSplit();
    open(at("groupBy=category"));
    expect(screen.getByTestId("split-note").textContent).toMatch(/^Split rule: Shopping and Groceries include 80% and 20% of each Synth Mart order\./);
  });
  it("is present when grouped by subcategory (a group reads \"Category / Sub\")", () => {
    withSplit();
    open(at("groupBy=subcategory"));
    expect(screen.getByTestId("split-note").textContent).toMatch(/Groceries/);
  });
  it("is absent for any other grouping, and without a split", () => {
    withSplit();
    open(at("groupBy=vendor"));
    expect(screen.queryByTestId("split-note")).toBeNull();
    cleanup();
    splits.parents.clear(); splits.children.clear(); txns = ROWS;
    open(at("groupBy=category"));
    expect(screen.queryByTestId("split-note")).toBeNull();
  });
});

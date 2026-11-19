// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { __resetSortWarnings } from "../filters/useSort";
import { txFactory } from "../lib/__fixtures__";
import { MAX_ANSWERS } from "../lib/answers";
import { SHIPPED_RESERVED } from "../lib/categories";
import { SELECTION_CAP } from "../lib/useRowSelection";
import Transactions from "./Transactions";
import { Where, whereSearch } from "../test/routerProbe";

const store = vi.hoisted(() => ({
  txns: [] as unknown[],
  splitParents: new Map<string, unknown>(), childrenByParent: new Map<string, unknown[]>(),   // a test sets them per case
  refused: null as number | null,                         // the status of an answers save the store refused
  stored: 0,                                              // how many answers the family store holds "now" ...
  keys: [] as string[],                                   // ... and the keys of the first few of them
  answerTxns: vi.fn(), answerTxn: vi.fn(), answerVendor: vi.fn(),
  taxonomy: { Dining: ["Coffee"], Kids: ["School"], Income: [], Internal: [] },
}));
vi.mock("../data/DataContext", () => ({
  useData: () => ({
    data: { transactions: store.txns, assumptions: { taxonomy: store.taxonomy, category_renames: [], reserved: SHIPPED_RESERVED },
      splitParents: store.splitParents, purchases: store.txns, manifest: { generated_at: "2026-01-31T20:00:00-05:00" },
      // vendors.json: every vendor here is learnable, so a one-vendor selection's editor starts on "Always for"
      vendors: ["Alpha", "Bravo", "Charlie", "Employer"].map((vendor) => ({ vendor, learn: true })) },
    indexes: { dataRange: { first: "2025-01-01", last: "2026-01-31" }, dataThrough: "2026-01-31", tagList: ["kids", "trip"],
      childrenByParent: store.childrenByParent },
    overlay: { pendingIds: new Set<string>(), conflictedKeys: new Set<string>() },
    answers: { doc: { answers: Array.from({ length: store.stored }, (_, i) => store.keys[i] ?? `txn:old${i}`)
                 .map((key) => ({ key, scope: key.startsWith("vendor:") ? "vendor" : "txn" })) }, answerTxns: store.answerTxns, answerTxn: store.answerTxn,
               answerVendor: store.answerVendor, refused: store.refused },
  }),
}));
vi.mock("../components/FilterBar", () => ({ default: () => null }));
// jsdom has no layout: render every row
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
  vi.stubGlobal("matchMedia", (query: string) => ({      // a 1280px desktop: the full grid
    get matches() { const m = /max-width: (\d+)px/.exec(query); return !!m && 1280 <= Number(m[1]); },
    media: query, addEventListener() {}, removeEventListener() {},
  }));
});
beforeEach(() => {
  __resetSortWarnings();
  store.stored = 0; store.keys = []; store.refused = null;
  store.answerTxns.mockReset(); store.answerTxn.mockReset(); store.answerVendor.mockReset();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); store.splitParents = new Map(); store.childrenByParent = new Map(); });

const tx = txFactory();
// date desc (the default sort) = Bravo Alpha Charlie; vendor asc = Alpha Bravo Charlie; amount desc = Bravo Charlie Alpha:
// the URL's sort can never be mistaken for the default
store.txns = [
  tx({ date: "2026-01-02", vendor: "Alpha", amount: -30 }),
  tx({ date: "2026-01-03", vendor: "Bravo", amount: -10 }),
  tx({ date: "2026-01-01", vendor: "Charlie", amount: -20 }),
];
// `from=` is a filter key, so this is not a fresh visit and the 6-month default stays out of the way
const at = (search: string) =>
  render(<MemoryRouter initialEntries={[`/spending/transactions?from=2020-01-01${search}`]}><Transactions /><Where /></MemoryRouter>);
const order = () => screen.getAllByRole("row").filter((r) => r.hasAttribute("aria-expanded"))
  .map((r) => /Alpha|Bravo|Charlie/.exec(r.textContent ?? "")![0]);
const search = whereSearch;
const header = (name: RegExp) => screen.getByRole("columnheader", { name });

describe("Transactions: the register's sort lives in ?sort=", () => {
  it("opens sorted by the URL", () => {
    at("&sort=vendor:asc");
    expect(order()).toEqual(["Alpha", "Bravo", "Charlie"]);
    expect(header(/Vendor/).getAttribute("aria-sort")).toBe("ascending");
  });

  it("a header click writes the sort to the URL and keeps the filters; the default sort leaves no trace", () => {
    at("");
    expect(order()).toEqual(["Bravo", "Alpha", "Charlie"]);                           // date, newest first
    fireEvent.click(within(header(/Amount/)).getByRole("button"));
    expect(search().get("sort")).toBe("amount:desc");
    expect(search().get("from")).toBe("2020-01-01");
    expect(order()).toEqual(["Bravo", "Charlie", "Alpha"]);
    fireEvent.click(within(header(/Date/)).getByRole("button"));                       // dates open newest first: the default
    expect(search().has("sort")).toBe(false);
    expect(search().get("from")).toBe("2020-01-01");
  });

  it("a stale column id falls back to the default sort with one warning, never a crash", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    at("&sort=gone-column:asc");
    expect(order()).toEqual(["Bravo", "Alpha", "Charlie"]);
    expect(header(/Date/).getAttribute("aria-sort")).toBe("descending");
    act(() => { fireEvent.click(within(header(/Amount/)).getByRole("button")); });   // a write re-parses the query
    expect(warn).toHaveBeenCalledTimes(1);
  });
});

describe("Transactions: bulk categorize and tag", () => {
  const alpha = tx({ date: "2026-01-04", vendor: "Alpha", amount: -30, category: "Dining", subcategory: "Coffee" });
  const bravo = tx({ date: "2026-01-03", vendor: "Bravo", amount: -10, category: "Dining", subcategory: "Coffee" });
  const charlie = tx({ date: "2026-01-02", vendor: "Charlie", amount: -20, category: "Kids", subcategory: "School" });
  const salary = tx({ date: "2026-01-01", vendor: "Employer", amount: 900, type: "income", category: "Income", subcategory: null });
  const given = store.txns;
  beforeEach(() => { store.txns = [alpha, bravo, charlie, salary]; });
  afterEach(() => { store.txns = given; });
  const box = (vendor: string) => screen.getByRole("checkbox", { name: new RegExp(`^Select ${vendor},`) });
  const bar = () => screen.queryByTestId("bulk-bar");
  const open = () => fireEvent.click(screen.getByRole("button", { name: /Categorize or tag/ }));
  const ids = (...rows: { id: string }[]) => rows.map((r) => r.id).sort();
  const sentIds = (call = 0) => [...store.answerTxns.mock.calls[call][0] as string[]].sort();
  function Go({ to }: { to: string }) { const nav = useNavigate(); return <button onClick={() => nav(to)}>go</button>; }
  const atWith = (extra = "") => render(
    <MemoryRouter initialEntries={["/spending/transactions?from=2020-01-01"]}><Transactions /><Where /><Go to={`/spending/transactions?from=2020-01-01${extra}`} /></MemoryRouter>);

  it("at rest there is a select-all box and one per answerable row, and no bulk bar", () => {
    atWith();
    expect(screen.getByRole("checkbox", { name: "Select every row shown" })).toBeDefined();
    expect(screen.getAllByRole("checkbox", { name: /^Select (?!every)/ })).toHaveLength(4);
    expect(bar()).toBeNull();
  });

  it("ticking rows opens the bar: it counts them and says what a save writes and what the store holds", () => {
    store.stored = 137;
    atWith();
    fireEvent.click(box("Alpha"));
    expect(bar()!.textContent).toMatch(/1 selected/);
    expect(bar()!.textContent).toMatch(/writes 1 answer \(one per row; a tags-only answer takes a slot too\)/);
    fireEvent.click(box("Bravo"));
    expect(bar()!.textContent).toMatch(/2 selected/);
    expect(bar()!.textContent).toMatch(/writes 2 answers/);
    expect(bar()!.textContent).toMatch(/keeps up to 500 one-off answers, dropping the oldest first \(137 stored now\)/);
    expect(bar()!.textContent).toMatch(/“Always for a vendor” is one answer/);
  });

  it("categorizes the selection in ONE answerTxns call, then closes the editor and clears the selection", () => {
    atWith();
    fireEvent.click(box("Alpha"));
    fireEvent.click(box("Bravo"));
    open();
    expect(screen.getByText(/2 transactions/)).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(store.answerTxns).toHaveBeenCalledTimes(1);
    expect(sentIds()).toEqual(ids(alpha, bravo));
    expect(store.answerTxns.mock.calls[0][1]).toStrictEqual({ category: "Kids", subcategory: null, needs_review: false });
    expect(store.answerTxn).not.toHaveBeenCalled();
    expect(bar(), "the selection is spent").toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect((box("Alpha") as HTMLInputElement).checked).toBe(false);
  });

  it("tags the selection with { tags } only, whatever categories the rows have", () => {
    atWith();
    fireEvent.click(box("Alpha"));
    fireEvent.click(box("Charlie"));
    open();
    const input = screen.getByPlaceholderText("add tag…");
    fireEvent.change(input, { target: { value: "trip" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(store.answerTxns).toHaveBeenCalledTimes(1);
    expect(sentIds()).toEqual(ids(alpha, charlie));
    expect(store.answerTxns.mock.calls[0][1]).toStrictEqual({ tags: ["trip"] });
  });

  it("a selection that mixes income and spending is refused, nothing is written", () => {
    atWith();
    fireEvent.click(box("Alpha"));
    fireEvent.click(box("Employer"));
    open();
    expect(screen.getByText(/one kind/)).toBeDefined();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(store.answerTxns).not.toHaveBeenCalled();
  });

  it("offers the vendor scope when the selected rows share a vendor, and the editor starts on it", () => {
    store.txns = [alpha, tx({ date: "2026-01-05", vendor: "Alpha", amount: -5, category: "Dining", subcategory: "Coffee" })];
    atWith();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select every row shown" }));
    open();
    expect(screen.getByRole("button", { name: /Always for/ }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(store.answerVendor).toHaveBeenCalledWith("Alpha", { category: "Kids", subcategory: null, needs_review: false }, undefined);
    expect(store.answerTxns).not.toHaveBeenCalled();
  });

  it("a filter edit that hides a selected row drops it from the selection (it is never answered unseen)", () => {
    // the register's rows follow ?vendors= (applyFilters); the FilterBar itself is stubbed out here
    atWith("&vendors=Alpha");
    fireEvent.click(box("Alpha"));
    fireEvent.click(box("Bravo"));
    expect(bar()!.textContent).toMatch(/2 selected/);
    fireEvent.click(screen.getByRole("button", { name: "go" }));
    expect(screen.queryByRole("checkbox", { name: /^Select Bravo,/ }), "Bravo is filtered out").toBeNull();
    expect(bar()!.textContent, "and out of the selection").toMatch(/1 selected/);
    fireEvent.click(box("Alpha"));
    expect(bar(), "nothing left selected: no bar").toBeNull();
  });

  it("a filter edit that hides none of the selected rows leaves the selection alone (the control)", () => {
    atWith("&vendors=Alpha|Bravo");
    fireEvent.click(box("Alpha"));
    fireEvent.click(box("Bravo"));
    fireEvent.click(screen.getByRole("button", { name: "go" }));
    expect(screen.queryByRole("checkbox", { name: /^Select Charlie,/ }), "Charlie is filtered out").toBeNull();
    expect(bar()!.textContent).toMatch(/2 selected/);
  });

  it("clear drops the selection and closes the editor; an editor over an emptied selection does not come back on the next tick", () => {
    atWith();
    fireEvent.click(box("Alpha"));
    open();
    expect(screen.getByRole("button", { name: "Save" })).toBeDefined();
    fireEvent.click(box("Alpha"));                                    // untick the last row under the open editor
    expect(bar()).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    fireEvent.click(box("Bravo"));
    expect(bar()).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Save" }), "the editor did not reopen by itself").toBeNull();
    open();
    fireEvent.click(screen.getByRole("button", { name: "clear" }));
    expect(bar()).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });

  it("when stored + this save would pass 500, the bar says how many of the oldest one-off answers it would drop", () => {
    store.stored = 498;
    atWith();
    for (const v of ["Alpha", "Bravo", "Charlie"]) fireEvent.click(box(v));
    expect(bar()!.textContent).toMatch(/\(498 stored now\)/);
    expect(bar()!.textContent, "498 + 3 = 501: one over").toMatch(/this save would drop the oldest one-off answer/);
    fireEvent.click(box("Employer"));
    expect(bar()!.textContent, "498 + 4 = 502").toMatch(/this save would drop the 2 oldest one-off answers/);
    cleanup();
    store.stored = 490;
    atWith();
    fireEvent.click(box("Alpha"));
    expect(bar()!.textContent, "room to spare: no warning").not.toMatch(/would drop/);
  });

  it("a row that already has an answer merges into it and takes no new slot, so it is not counted toward the drop", () => {
    store.stored = 500;
    store.keys = [`txn:${alpha.id}`, `txn:${bravo.id}`];                 // two of the stored answers are THESE rows'
    atWith();
    fireEvent.click(box("Alpha"));
    fireEvent.click(box("Bravo"));
    expect(bar()!.textContent, "a full store, but both rows re-use their slot").not.toMatch(/would drop/);
    fireEvent.click(box("Charlie"));
    expect(bar()!.textContent, "one new row in a full store").toMatch(/would drop the oldest one-off answer/);
  });

  it("a vendor-scope save is ONE answer that takes no one-off slot: the bar says so and warns of no drop", () => {
    store.txns = [alpha, tx({ date: "2026-01-05", vendor: "Alpha", amount: -5, category: "Dining", subcategory: "Coffee" }), bravo];
    store.stored = 500;
    atWith();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select every row shown" }));
    expect(bar()!.textContent, "three rows, per row: 3 over").toMatch(/would drop the 3 oldest one-off answers/);
    cleanup();
    store.txns = [alpha, tx({ date: "2026-01-05", vendor: "Alpha", amount: -5, category: "Dining", subcategory: "Coffee" })];
    atWith();
    for (const b of screen.getAllByRole("checkbox", { name: /^Select Alpha,/ })) fireEvent.click(b);
    expect(bar()!.textContent, "two Alpha rows, one answer each").toMatch(/writes 2 answers.*would drop the 2 oldest one-off answers/);
    open();
    expect(bar()!.textContent, "the editor starts on 'Always for', and the bar counts it at once")
      .toMatch(/writes 1 answer for “Alpha”, which covers every one of its rows and is never dropped/);
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    expect(bar()!.textContent, "500 one-off answers stored + one vendor answer").not.toMatch(/would drop/);
    fireEvent.click(screen.getByRole("button", { name: "Just these rows" }));
    expect(bar()!.textContent, "back to one per row").toMatch(/writes 2 answers.*2 oldest one-off answers/);
    fireEvent.click(screen.getByRole("button", { name: /Always for/ }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(bar()!.textContent, "the editor is gone: per-row again").toMatch(/writes 2 answers/);
  });

  it("vendor answers take no one-off slot and are never dropped: only the one-off answers stored count toward the drop", () => {
    const vendors = (n: number) => Array.from({ length: n }, (_, i) => `vendor:Synthetic ${i}`);
    expect(SELECTION_CAP, "the answers a save creates are never the oldest, so the warning counts stored ones").toBeLessThan(MAX_ANSWERS);
    store.stored = 500;
    store.keys = [...vendors(499), "txn:old-one-off"];
    atWith();
    fireEvent.click(box("Alpha"));
    fireEvent.click(box("Bravo"));
    expect(bar()!.textContent, "499 vendor answers + 1 one-off stored").toMatch(/\(1 stored now\)/);
    expect(bar()!.textContent, "1 one-off stored + this save's 2").not.toMatch(/would drop/);
    cleanup();
    store.stored = 1100;                                                 // 600 vendor answers + 500 one-off ones
    store.keys = vendors(600);
    atWith();
    fireEvent.click(box("Alpha"));
    expect(bar()!.textContent).toMatch(/\(500 stored now\)/);
    expect(bar()!.textContent, "one new one-off answer past 500").toMatch(/this save would drop the oldest one-off answer/);
    open();
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    fireEvent.click(screen.getByRole("button", { name: /Always for/ }));
    expect(bar()!.textContent, "a vendor answer on top of 600 more").not.toMatch(/would drop/);
  });

  it("a bulk save the store refused is said in the card, under the bar, after the editor has closed", () => {
    atWith();
    expect(screen.queryByTestId("answers-refused"), "nothing refused: nothing said").toBeNull();
    cleanup();
    store.refused = 413;                                  // the debounced PUT came back 413 after the bulk save
    atWith();
    const card = screen.getByTestId("card-transactions");
    expect(within(card).getByRole("alert").textContent)
      .toMatch(/^The family store refused the last save as too large, which should not happen\. These answers are saved in this browser only/);
    fireEvent.click(box("Alpha"));
    expect(bar(), "the bar still opens: a refusal does not block the next save").not.toBeNull();
  });

  it("a refusal arriving after a bulk save takes back 'permanent at the next ingest'; a save that gets through gives it back", () => {
    // a fresh element per render: React skips re-rendering an identical one, and the mock is read at render time
    const tree = () => <MemoryRouter initialEntries={["/spending/transactions?from=2020-01-01"]}><Transactions /><Where /></MemoryRouter>;
    const r = render(tree());
    const saved = () => screen.getByTestId("bulk-saved").textContent;
    fireEvent.click(box("Alpha"));
    fireEvent.click(box("Bravo"));
    open();
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(saved()).toBe("Saved 2 answers — permanent at the next ingest");
    expect(screen.queryByRole("alert")).toBeNull();
    store.refused = 413;                                  // about a second later the debounced PUT comes back refused
    r.rerender(tree());
    expect(saved()).toBe("Saved 2 answers in this browser only — the family store is refusing saves (see below)");
    expect(screen.getByRole("alert").textContent).toMatch(/won't reach the next ingest/);
    store.refused = null;                                 // a later save got through: the store has them all
    r.rerender(tree());
    expect(saved()).toBe("Saved 2 answers — permanent at the next ingest");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a save made while an earlier refusal stands never claims 'permanent at the next ingest'", () => {
    store.refused = 413;
    atWith();
    for (const b of screen.getAllByRole("checkbox", { name: /^Select Alpha,/ })) fireEvent.click(b);
    open();
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    fireEvent.click(screen.getByRole("button", { name: /Always for/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(store.answerVendor).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("bulk-saved").textContent).toBe("Saved 1 answer in this browser only — the family store is refusing saves (see below)");
  });

  describe("focus follows the work", () => {
    const focused = () => document.activeElement as HTMLElement;
    it("Cancel returns focus to the button that opened the editor, and keeps the selection", () => {
      atWith();
      fireEvent.click(box("Alpha"));
      open();
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
      expect(focused()).toBe(screen.getByRole("button", { name: /Categorize or tag/ }));
      expect(bar()!.textContent).toMatch(/1 selected/);
    });

    it("a save returns focus to the register's active row (the one that was the tab stop), not to the page", () => {
      atWith();
      const rowOf = (v: string) => screen.getAllByRole("row").find((r) => r.hasAttribute("aria-expanded") && r.textContent?.includes(v))!;
      act(() => rowOf("Bravo").focus());                                  // the stop moves to row 1 (date desc: Alpha Bravo Charlie Employer)
      fireEvent.click(box("Alpha"));
      open();
      fireEvent.click(screen.getByRole("button", { name: "Kids" }));
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      expect(bar()).toBeNull();
      expect(focused().getAttribute("role")).toBe("row");
      expect(focused().getAttribute("tabindex")).toBe("0");
      expect(focused()).toBe(rowOf("Bravo"));
    });

    it("clearing the selection also puts focus back on the register, not on a vanished button", () => {
      atWith();
      fireEvent.click(box("Alpha"));
      fireEvent.click(screen.getByRole("button", { name: "clear" }));
      expect(focused().getAttribute("role")).toBe("row");
      expect(focused().getAttribute("tabindex")).toBe("0");
    });

    it("focus is left alone when nothing asked for it (ticking a box does not steal it)", () => {
      atWith();
      const b = box("Alpha") as HTMLElement;
      act(() => b.focus());
      fireEvent.click(b);
      expect(focused()).toBe(b);
    });
  });

  it("the cap: select-all over more rows than SELECTION_CAP takes the first 100 and the bar says so", () => {
    store.txns = Array.from({ length: 105 }, (_, i) => tx({ date: `2026-02-${String(1 + (i % 28)).padStart(2, "0")}`, vendor: `V${String(i).padStart(3, "0")}`, amount: -5 }));
    atWith();
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select the first 100 rows shown$/ }));
    expect(bar()!.textContent).toMatch(/100 selected \(max 100\)/);
    expect(bar()!.textContent).toMatch(/writes 100 answers/);
  });
});

describe("Transactions: the selection follows what the register displays", () => {
  const m = txFactory({ vendor: "Synth Mart", date: "2026-01-05" });
  const parent = m({ id: "p", amount: -300, is_parent: true, category: "Dining", subcategory: "Coffee" });
  const a = m({ id: "a", amount: -210, parent_id: "p", category: "Dining", subcategory: "Coffee", category_source: "split" });
  const b = m({ id: "b", amount: -90, parent_id: "p", category: "Kids", subcategory: "School", category_source: "split" });
  const other = m({ id: "o", amount: -40, vendor: "Other Shop", date: "2026-01-04", category: "Dining", subcategory: "Coffee" });
  const given = store.txns;
  beforeEach(() => {
    store.txns = [a, b, other];                                          // the category view: parts + ordinary rows
    store.splitParents = new Map([["p", parent]]);
    store.childrenByParent = new Map([["p", [a, b]]]);
  });
  afterEach(() => { store.txns = given; });
  function Go({ to }: { to: string }) { const nav = useNavigate(); return <button onClick={() => nav(to)}>go</button>; }
  const atWith = (search: string, to: string) => render(
    <MemoryRouter initialEntries={[`/spending/transactions?from=2020-01-01${search}`]}>
      <Transactions /><Go to={`/spending/transactions?from=2020-01-01${to}`} />
    </MemoryRouter>);
  const bar = () => screen.queryByTestId("bulk-bar");

  it("a part ticked while it shows alone is deselected once an edit folds it under its parent row (it would be answered unseen)", () => {
    atWith("&categories=Kids", "");                                      // only the Kids part passes: it shows as itself
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Synth Mart,/ }));
    expect(bar()!.textContent).toMatch(/1 selected/);
    fireEvent.click(screen.getByRole("button", { name: "go" }));         // every part passes now: the parent row stands in
    expect(screen.queryByRole("checkbox", { name: /^Select Synth Mart,/ }), "the parent has no box").toBeNull();
    expect(bar(), "and the hidden part is no longer selected").toBeNull();
  });

  it("an ordinary row stays selected through an edit that leaves a part showing alone (the control)", () => {
    atWith("", "&categories=Dining");                                    // every part passes: the parent row + Other Shop
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Other Shop,/ }));
    expect(bar()!.textContent).toMatch(/1 selected/);
    fireEvent.click(screen.getByRole("button", { name: "go" }));         // the Dining part is alone now, beside Other Shop
    expect(screen.getByText(/split of/), "the Dining part shows alone").toBeDefined();
    expect(bar()!.textContent).toMatch(/1 selected/);
  });
});

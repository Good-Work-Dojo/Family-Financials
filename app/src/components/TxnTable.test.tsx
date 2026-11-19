// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render as renderBare, renderHook, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { Transaction } from "../data/types";
import { txFactory } from "../lib/__fixtures__";
import { chipInk, sourceChip } from "../lib/categorySource";
import { downloadCsv } from "../lib/csv";
import { MASK, setPrivate } from "../lib/privacy";
import { runningBalance } from "../lib/runningBalance";

// the CSV download is spied (the privacy tests read what it was handed); everything else in lib/csv is real
vi.mock("../lib/csv", async (importOriginal) => ({ ...(await importOriginal<typeof import("../lib/csv")>()), downloadCsv: vi.fn() }));

// the register reads the split parents and the parts-by-parent index; a test sets them per case
const world = vi.hoisted(() => ({
  splitParents: new Map<string, unknown>(), childrenByParent: new Map<string, unknown[]>(),
  refused: null as number | null,                            // the status of an answers save the store refused
  localOnly: false,                                          // the answers are local-only (store unreachable or refused)
  // the ingest instant: after every fixture's rows unless a test dates one ahead of it
  generatedAt: "2026-12-31T20:00:00-05:00",
}));
vi.mock("../data/DataContext", () => ({
  useData: () => ({
    answers: { refused: world.refused, localOnly: world.localOnly },
    overlay: { pendingIds: new Set<string>(), conflictedKeys: new Set<string>() },
    data: { splitParents: world.splitParents, manifest: { generated_at: world.generatedAt } },
    indexes: { childrenByParent: world.childrenByParent },
  }),
}));
// jsdom has no layout, so the real virtualizer renders no rows: a stand-in that renders them all,
// recording the options it was given and the row elements handed to measureElement
// `window` is the visible range the browser would report (null = everything is in view); the rows
// rendered are whatever the component's own rangeExtractor makes of it, as in the real virtualizer.
const virt = vi.hoisted(() => ({
  opts: null as any, measured: [] as Element[], scrolled: [] as number[],
  window: null as null | { start: number; end: number; overscan: number },
}));
vi.mock("@tanstack/react-virtual", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-virtual")>();
  return {
    ...actual,
    useVirtualizer: (opts: { count: number; rangeExtractor?: (r: any) => number[] }) => {
      virt.opts = opts;
      return {
        getTotalSize: () => opts.count * 40,
        getVirtualItems: () => {
          if (!opts.count) return [];
          const w = virt.window ?? { start: 0, end: opts.count - 1, overscan: 0 };
          const range = { startIndex: w.start, endIndex: w.end, overscan: w.overscan, count: opts.count };
          return (opts.rangeExtractor ?? actual.defaultRangeExtractor)(range)
            .map((index) => ({ index, start: index * 40, key: index }));
        },
        measureElement: (el: Element | null) => { if (el) virt.measured.push(el); },
        scrollToIndex: (i: number) => { virt.scrolled.push(i); },
      };
    },
  };
});
// jsdom has no matchMedia; the register asks which layout to draw (useRegisterLayout: 1023 / 1279). One
// `width` answers every `max-width` query, and `setWidth` flips it the way a window resize would: the
// media query lists report the new value and their listeners fire.
const media = vi.hoisted(() => ({ width: 1280, touch: false, listeners: new Set<() => void>() }));
// the ResizeObserver stand-in remembers its callbacks, so a test can say "the scroller just resized"
const ro = vi.hoisted(() => ({ callbacks: new Set<() => void>() }));
import { SELECTION_CAP, useRowSelection } from "../lib/useRowSelection";
import { EDITABLE_TYPES } from "./CategoryEditor";
import TxnTable, { PHONE_PAGE, TXN_COLUMN_IDS, useRegisterLayout } from "./TxnTable";

// the Account name is a link (AccountLink), so every register mounts inside a router - re-renders too (RTL re-applies the wrapper)
const render = (ui: ReactElement) => renderBare(ui, { wrapper: MemoryRouter });

beforeAll(() => {
  (globalThis as any).ResizeObserver = class {
    private cb: () => void;
    constructor(cb: () => void) { this.cb = cb; }
    observe() { ro.callbacks.add(this.cb); }
    unobserve() {}
    disconnect() { ro.callbacks.delete(this.cb); }
  };
  vi.stubGlobal("matchMedia", (query: string) => ({
    get matches() {
      if (query === "(pointer: coarse)") return media.touch;                // the hint's words follow the pointer
      const m = /max-width: (\d+)px/.exec(query); return !!m && media.width <= Number(m[1]);
    },
    media: query,
    addEventListener: (_: string, fn: () => void) => { media.listeners.add(fn); },
    removeEventListener: (_: string, fn: () => void) => { media.listeners.delete(fn); },
  }));
});
const setWidth = (w: number) => act(() => { media.width = w; media.listeners.forEach((fn) => fn()); });
afterEach(() => { cleanup(); setPrivate(false); media.width = 1280; media.touch = false; virt.scrolled.length = 0; virt.window = null; vi.mocked(downloadCsv).mockClear(); world.refused = null; world.localOnly = false; });
const tx = txFactory();
const two = () => [tx({ date: "2026-01-02", vendor: "Alpha Cafe", amount: -12.5 }),
  tx({ date: "2026-01-01", vendor: "Beta Books", amount: -40 })];
// three rows whose date, amount and vendor orders are all different permutations, ascending and descending:
// date desc = Bravo Alpha Charlie, date asc = Charlie Alpha Bravo, amount desc (-10 first) = Bravo Charlie Alpha,
// amount asc = Alpha Charlie Bravo, vendor asc = Alpha Bravo Charlie, vendor desc = Charlie Bravo Alpha.
// So no sort can be mistaken for the default (date desc), and a flipped direction cannot hide behind a coincidence.
const three = () => [
  tx({ date: "2026-01-02", vendor: "Alpha", amount: -30 }),
  tx({ date: "2026-01-03", vendor: "Bravo", amount: -10 }),
  tx({ date: "2026-01-01", vendor: "Charlie", amount: -20 }),
];
const order = () => screen.getAllByRole("row").filter((r) => r.hasAttribute("aria-expanded"))
  .map((r) => /Alpha|Bravo|Charlie/.exec(r.textContent ?? "")![0]);
const dirButton = () => screen.getByRole("button", { name: /^Sort direction/ });
const focusRow = (el: HTMLElement) => act(() => { el.focus(); });          // a native focus() outside act would warn
const bodyRows = () => screen.getAllByRole("row").filter((r) => r.hasAttribute("aria-expanded"));

describe("TxnTable header semantics", () => {
  it("is a table of rows: sortable column headers carry aria-sort, date descending by default", () => {
    render(<TxnTable txns={two()} />);
    expect(screen.getByRole("table")).toBeDefined();
    expect(screen.getAllByRole("columnheader")).toHaveLength(8);
    const date = () => screen.getByRole("columnheader", { name: /Date/ });
    const amount = () => screen.getByRole("columnheader", { name: /Amount/ });
    expect(date().getAttribute("aria-sort")).toBe("descending");
    expect(amount().getAttribute("aria-sort")).toBe("none");
    // the sort control stays a real button inside the header cell
    fireEvent.click(within(amount()).getByRole("button"));
    expect(["ascending", "descending"]).toContain(amount().getAttribute("aria-sort"));
    expect(date().getAttribute("aria-sort")).toBe("none");
    // header row + body rows are rows; body cells are cells
    expect(screen.getAllByRole("row").length).toBeGreaterThan(1);
  });

  it("flips ascending <-> descending on a second click of the same header", () => {
    render(<TxnTable txns={two()} />);
    const date = () => screen.getByRole("columnheader", { name: /Date/ });
    fireEvent.click(within(date()).getByRole("button"));
    expect(date().getAttribute("aria-sort")).toBe("ascending");
    fireEvent.click(within(date()).getByRole("button"));
    expect(date().getAttribute("aria-sort")).toBe("descending");
  });

  it("an unnamed review-dot column still has an accessible name", () => {
    render(<TxnTable txns={two()} />);
    expect(screen.getByRole("columnheader", { name: /review/i })).toBeDefined();
  });
});

describe("TxnTable rows", () => {
  it("a row is keyboard-operable: Enter opens the raw descriptor, aria-expanded follows", () => {
    render(<TxnTable txns={two()} />);
    const row = screen.getAllByRole("row").find((r) => r.hasAttribute("aria-expanded"))!;
    expect(row.getAttribute("aria-expanded")).toBe("false");
    expect(row.getAttribute("tabindex")).toBe("0");
    fireEvent.keyDown(row, { key: "Enter" });
    expect(row.getAttribute("aria-expanded")).toBe("true");
    fireEvent.keyDown(row, { key: " " });
    expect(row.getAttribute("aria-expanded")).toBe("false");
  });
});

describe("TxnTable when the family store refuses an answers save", () => {
  // the open row's editor writes answers, so the register says a refusal where the save was made (Review says it too)
  const notice = () => screen.getByTestId("answers-refused");
  it("says it was refused as too large, where the answers are, and who should look — never 'for now'", () => {
    world.refused = 413;
    world.localOnly = true;                                  // a refused save is local-only too
    render(<TxnTable txns={two()} />);
    expect(notice().textContent).toBe("The family store refused the last save as too large, which should not happen. These answers are "
      + "saved in this browser only and won't reach the next ingest: tell whoever maintains the dashboard.");
    expect(notice().getAttribute("role")).toBe("alert");
  });
  it("a malformed-document refusal says the same without 'as too large'", () => {
    world.refused = 400;
    world.localOnly = true;
    render(<TxnTable txns={two()} />);
    expect(notice().textContent).toMatch(/^The family store refused the last save, which should not happen\. /);
  });
  it("with no refusal it says nothing (no alert, and no extra status region beside the page's own)", () => {
    render(<TxnTable txns={two()} />);
    expect(screen.queryByTestId("answers-refused")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });
  it("an unreachable store is not a refusal: it retries by itself and Review says so, so the register says nothing", () => {
    world.localOnly = true;                                  // local-only, but nothing refused (a dev server has no store)
    render(<TxnTable txns={two()} />);
    expect(screen.queryByTestId("answers-refused")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.body.textContent).not.toMatch(/Store unreachable|saved in this browser/);
  });
});

describe("TxnTable status marks", () => {
  it("the needs-review dot says so in words, not only in a title", () => {
    render(<TxnTable txns={[tx({ needs_review: true, vendor: "Alpha Cafe" })]} />);
    const row = screen.getAllByRole("row").find((r) => r.hasAttribute("aria-expanded"))!;
    expect(row.querySelector(".sr-only")?.textContent).toBe("needs review");
  });

  it("a learned chip inside a row is not a second tab stop, but its tooltip is part of the row's text", () => {
    for (const phone of [false, true]) {
      setWidth(phone ? 390 : 1280);
      const { unmount } = render(<TxnTable txns={[tx({ category_source: "learned", vendor: "Alpha Cafe" })]} />);
      const row = screen.getAllByRole("row").find((r) => r.hasAttribute("aria-expanded"))!;
      expect(row.getAttribute("tabindex")).toBe("0");
      // -1 is not a tab stop: the Account link inside the row is reachable by a pointer only, like the row's checkbox
      expect(row.querySelectorAll('[tabindex]:not([tabindex="-1"])'), phone ? "phone row" : "desktop row").toHaveLength(0);
      expect(row.textContent).toContain(": learned from your answers");
      unmount();
    }
  });
});

describe("TxnTable row measurement", () => {
  // a 1280px screenshot once showed every register row ~47px apart (the phone row height) around
  // ~29px desktop rows: measurements taken in one layout were kept after a flip to the other
  it("a phone <-> desktop flip re-measures every row with the new layout's keys and estimate", () => {
    render(<TxnTable txns={two()} />);
    const desktopKey = virt.opts.getItemKey(0);
    expect(virt.opts.estimateSize(0)).toBe(29);
    const before = [...virt.measured];
    virt.measured.length = 0;
    setWidth(1000);                                               // the stacked list of a tablet, still virtualised
    expect(virt.opts.getItemKey(0) !== desktopKey, "measurements are keyed per layout").toBe(true);
    expect(virt.opts.estimateSize(0)).toBe(47);
    expect(virt.measured.length, "both rows measured again").toBe(2);
    expect(virt.measured.every((el) => !before.includes(el)), "as fresh elements, not the old ones").toBe(true);
    virt.measured.length = 0;
    setWidth(1280);
    expect(virt.opts.getItemKey(0)).toBe(desktopKey);
    expect(virt.measured.length).toBe(2);
  });
});

describe("TxnTable at phone width", () => {
  it("shows vendor and amount on the first line of a stacked row, no header grid", () => {
    media.width = 390;
    render(<TxnTable txns={two()} />);
    expect(screen.queryAllByRole("columnheader")).toHaveLength(0);
    const row = screen.getAllByRole("row").find((r) => within(r).queryByText("Alpha Cafe"))!;
    const [line1, line2] = within(row).getAllByRole("cell");
    expect(line1.textContent).toContain("Alpha Cafe");
    expect(line1.textContent).toContain("$12.50");
    expect(line2.textContent).toContain("2026-01-02");
  });

  it("numbers its rows from 1 (there is no header row to count)", () => {
    media.width = 390;
    render(<TxnTable txns={two()} />);
    expect(screen.getByRole("table").getAttribute("aria-rowcount")).toBe("2");
    const idx = screen.getAllByRole("row").filter((r) => r.hasAttribute("aria-expanded"))
      .map((r) => r.getAttribute("aria-rowindex"));
    expect(idx).toEqual(["1", "2"]);
  });

  it("sorts through a labelled select + direction button: the ROWS reorder and the direction is honest", () => {
    media.width = 390;
    render(<TxnTable txns={three()} />);
    const sort = screen.getByLabelText("Sort by") as HTMLSelectElement;
    expect(sort.value).toBe("date");
    expect(order()).toEqual(["Bravo", "Alpha", "Charlie"]);          // date, newest first
    expect(dirButton().getAttribute("aria-label")).toMatch(/currently descending/);
    fireEvent.click(dirButton());
    expect(order()).toEqual(["Charlie", "Alpha", "Bravo"]);          // date, oldest first
    expect(dirButton().getAttribute("aria-label")).toMatch(/currently ascending/);

    fireEvent.change(sort, { target: { value: "amount" } });
    expect(sort.value).toBe("amount");
    expect(order()).toEqual(["Bravo", "Charlie", "Alpha"]);          // amounts open descending: -10, -20, -30
    fireEvent.click(dirButton());
    expect(order()).toEqual(["Alpha", "Charlie", "Bravo"]);          // flipped

    fireEvent.change(sort, { target: { value: "vendor" } });
    expect(order()).toEqual(["Alpha", "Bravo", "Charlie"]);          // text opens A to Z
    expect(dirButton().getAttribute("aria-label")).toMatch(/currently ascending/);
    fireEvent.click(dirButton());
    expect(order()).toEqual(["Charlie", "Bravo", "Alpha"]);
    // the glyph is decoration; the button's name carries the state
    expect(dirButton().querySelector("[aria-hidden='true']")).not.toBeNull();
  });

  it("the select tells the truth when the active sort is a column it does not list", () => {
    render(<TxnTable txns={three()} />);                           // desktop: sort by Type via its header
    fireEvent.click(within(screen.getByRole("columnheader", { name: /^Type/ })).getByRole("button"));
    setWidth(390);                                                // the window shrinks to a phone
    const sort = screen.getByLabelText("Sort by") as HTMLSelectElement;
    expect(sort.value).toBe("type");
    expect(within(sort).getByRole("option", { name: "Type" })).toBeDefined();
    fireEvent.change(sort, { target: { value: "vendor" } });         // choosing a listed column drops the extra option
    expect(within(sort).queryByRole("option", { name: "Type" })).toBeNull();
  });

  it("labels the sort select with a per-instance id, and offers a tap hint plus a chevron per row", () => {
    media.width = 390;
    const { container } = render(<><TxnTable txns={three()} /><TxnTable txns={three()} /></>);
    const selects = container.querySelectorAll("select");
    expect(selects).toHaveLength(2);
    expect(selects[0].id).not.toBe(selects[1].id);
    expect(screen.getAllByText("click a row for the raw descriptor")).toHaveLength(2);
    const row = screen.getAllByRole("row").find((r) => r.hasAttribute("aria-expanded"))!;
    expect(row.querySelector("[aria-hidden='true']")?.textContent).toBe("›");
  });

  it("the expanded phone descriptor spans the two cells of a phone row", () => {
    media.width = 390;
    render(<TxnTable txns={three()} />);
    const row = screen.getAllByRole("row").find((r) => r.hasAttribute("aria-expanded"))!;
    expect(within(row).getAllByRole("cell")).toHaveLength(2);
    fireEvent.click(row);
    const detail = screen.getAllByRole("cell").find((c) => c.hasAttribute("aria-colspan"))!;
    expect(detail.getAttribute("aria-colspan")).toBe("2");
  });

  it("the Edit category button is a 44px tap target on a phone", () => {
    media.width = 390;
    render(<TxnTable txns={[tx({ type: "purchase", vendor: "Alpha Cafe" })]} />);
    const row = screen.getAllByRole("row").find((r) => r.hasAttribute("aria-expanded"))!;
    fireEvent.click(row);
    const btn = screen.getByRole("button", { name: "Edit category" });
    expect(btn.className.split(/\s+/)).toContain("min-h-11");
  });
});

describe("TxnTable on a phone: the list scrolls with the page and pages", () => {
  const many = (n: number) => Array.from({ length: n }, (_, i) =>
    tx({ date: new Date(Date.UTC(2026, 5, 30) - i * 864e5).toISOString().slice(0, 10), vendor: `V${i}` }));

  it("has no body scroller and no fixed height, virtualises nothing and says how many transactions it holds", () => {
    media.width = 390;
    render(<TxnTable txns={many(12)} maxHeight={480} />);
    const body = bodyRows()[0].closest("[role=rowgroup]") as HTMLElement;
    expect(body.className).not.toMatch(/overflow-y/);
    expect(body.style.maxHeight).toBe("");
    // the rows sit in the page's flow, not at virtual offsets
    expect(bodyRows().every((r) => (r.parentElement as HTMLElement).style.position === "")).toBe(true);
    expect(virt.opts.count, "the virtualizer is handed no rows").toBe(0);
    expect(screen.getByTestId("register-count").textContent).toBe("12 transactions");
    expect(screen.queryByTestId("register-more")).toBeNull();
  });

  it("shows PHONE_PAGE rows, then a named Show-more button that adds the next page until the list is whole", () => {
    media.width = 390;
    render(<TxnTable txns={many(120)} />);
    expect(bodyRows()).toHaveLength(PHONE_PAGE);
    expect(screen.getByTestId("register-count").textContent).toBe("Showing 50 of 120 transactions");
    const more = screen.getByRole("button", { name: "Show 50 more transactions" });
    expect(more.textContent).toBe("Show 50 more");
    expect(more.className.split(/\s+/)).toContain("min-h-11");
    fireEvent.click(more);
    expect(bodyRows()).toHaveLength(100);
    expect(screen.getByTestId("register-count").textContent).toBe("Showing 100 of 120 transactions");
    fireEvent.click(screen.getByRole("button", { name: "Show 20 more transactions" }));
    expect(bodyRows()).toHaveLength(120);
    expect(screen.getByTestId("register-count").textContent).toBe("120 transactions");
    expect(screen.queryByTestId("register-more")).toBeNull();
    // the row numbers still count the whole list
    expect(screen.getByRole("table").getAttribute("aria-rowcount")).toBe("120");
  });

  it("counts a split purchase once, as the filter bar does: its parent row, or its lone parts together", () => {
    media.width = 390;
    const parts = [tx({ id: "p1", parent_id: "P", vendor: "Club", amount: -70 }), tx({ id: "p2", parent_id: "P", vendor: "Club", amount: -30 })];
    world.childrenByParent = new Map([["P", parts]]);
    world.splitParents = new Map([["P", tx({ id: "P", vendor: "Club", amount: -100, is_parent: true })]]);
    try {
      render(<TxnTable txns={[...parts, tx({ vendor: "Alpha" })]} />);
      expect(bodyRows()).toHaveLength(2);                       // the parent stands in for its parts
      expect(screen.getByTestId("register-count").textContent).toBe("2 transactions");
      cleanup();
      render(<TxnTable txns={[parts[0], tx({ vendor: "Alpha" })]} />);   // a lone part shows as itself
      expect(screen.getByTestId("register-count").textContent).toBe("2 transactions");
    } finally {
      world.childrenByParent = new Map();
      world.splitParents = new Map();
    }
  });

  it("a split whose lone parts fall on both sides of a page's end counts once: Showing K plus Show N more is the whole", () => {
    media.width = 390;
    // 71 rows, newest first; rows 49 and 50 are two of a three-part purchase's parts (the third did not pass the filter,
    // so each shows as itself), one on each side of the first page's end
    const day = (i: number) => new Date(Date.UTC(2026, 5, 30) - i * 864e5).toISOString().slice(0, 10);
    const rowsIn = Array.from({ length: 71 }, (_, i) => (i === 49 || i === 50
      ? tx({ id: `part${i}`, parent_id: "P", vendor: "Club", date: day(i), amount: -10 })
      : tx({ vendor: `V${i}`, date: day(i) })));
    const third = tx({ id: "part-3", parent_id: "P", vendor: "Club", amount: -5 });
    world.childrenByParent = new Map([["P", [rowsIn[49], rowsIn[50], third]]]);
    world.splitParents = new Map([["P", tx({ id: "P", vendor: "Club", amount: -25, is_parent: true })]]);
    try {
      render(<TxnTable txns={rowsIn} />);
      // 69 ordinary rows and one purchase: 70 transactions; the first page holds 49 of them and the purchase
      expect(screen.getByTestId("register-count").textContent).toBe("Showing 50 of 70 transactions");
      expect(screen.getByTestId("register-more").textContent, "50 + 20 = 70, the purchase not counted twice").toBe("Show 20 more");
      fireEvent.click(screen.getByTestId("register-more"));
      expect(screen.getByTestId("register-count").textContent).toBe("70 transactions");
    } finally {
      world.childrenByParent = new Map();
      world.splitParents = new Map();
    }
  });

  it("Show more hands focus to the first row it adds, on a middle page and on the last, where the button is gone", () => {
    media.width = 390;
    render(<TxnTable txns={many(120)} />);
    const active = () => (document.activeElement as HTMLElement).dataset.rowIndex;
    fireEvent.click(screen.getByTestId("register-more"));
    expect(active(), "the first row of the second page").toBe(String(PHONE_PAGE));
    expect(bodyRows().filter((r) => r.getAttribute("tabindex") === "0").map((r) => r.dataset.rowIndex), "and it is the one tab stop")
      .toEqual([String(PHONE_PAGE)]);
    fireEvent.click(screen.getByTestId("register-more"));
    expect(screen.queryByTestId("register-more"), "the last page: the button is gone").toBeNull();
    expect(active(), "focus did not fall to the page").toBe(String(2 * PHONE_PAGE));
  });

  it("a different list or sort starts again at its first page", () => {
    media.width = 390;
    const { rerender } = render(<TxnTable txns={many(120)} />);
    fireEvent.click(screen.getByTestId("register-more"));
    expect(bodyRows()).toHaveLength(100);
    rerender(<TxnTable txns={many(110)} />);
    expect(bodyRows()).toHaveLength(PHONE_PAGE);
  });

  it("the keys move among the rows shown: End stops at the last shown row, and nothing scrolls a body", () => {
    media.width = 390;
    render(<TxnTable txns={many(80)} />);
    focusRow(bodyRows()[0]);
    fireEvent.keyDown(document.activeElement!, { key: "End" });
    expect((document.activeElement as HTMLElement).dataset.rowIndex).toBe(String(PHONE_PAGE - 1));
    expect(virt.scrolled).toEqual([]);
  });

  it("its lines wrap at a word instead of cutting the vendor, the category or the account short", () => {
    media.width = 390;
    render(<TxnTable txns={[tx({ vendor: "Larkspur Robotics Direct Deposit", account_name: "Joint Checking" })]} />);
    const row = bodyRows()[0];
    expect(row.querySelector(".truncate"), "nothing truncated on a phone").toBeNull();
    const [line1, line2] = within(row).getAllByRole("cell");
    expect(line1.querySelector(".break-words")?.textContent).toContain("Larkspur Robotics Direct Deposit");
    expect(line2.className.split(/\s+/)).toContain("flex-wrap");
    // a tablet's stacked list keeps one line each
    cleanup();
    media.width = 1000;
    render(<TxnTable txns={[tx({ vendor: "Larkspur Robotics Direct Deposit" })]} />);
    expect(bodyRows()[0].querySelector(".truncate")).not.toBeNull();
  });
});

describe("TxnTable layouts", () => {
  it("stacks below lg, folds Owner and Type between lg and xl, shows every column from xl", () => {
    media.width = 1100;
    const { unmount } = render(<TxnTable txns={two()} />);
    const names = () => screen.getAllByRole("columnheader").map((h) => h.textContent ?? "");
    expect(names()).toHaveLength(6);
    expect(names().some((n) => /Owner|Type/.test(n))).toBe(false);
    unmount();
    media.width = 1000;
    const r2 = render(<TxnTable txns={two()} />);
    expect(screen.queryAllByRole("columnheader")).toHaveLength(0);          // stacked, like a phone
    expect(screen.getByLabelText("Sort by")).toBeDefined();
    r2.unmount();
    media.width = 1280;
    render(<TxnTable txns={two()} />);
    expect(screen.getAllByRole("columnheader")).toHaveLength(8);
  });

  it("the cuts are exact: 1023 stacked, 1024 compact, 1279 compact, 1280 full", () => {
    const { result } = renderHook(() => useRegisterLayout());
    for (const [w, layout] of [[390, "stacked"], [1023, "stacked"], [1024, "compact"], [1279, "compact"],
      [1280, "full"], [1920, "full"]] as const) {
      setWidth(w);
      expect(result.current, `${w}px`).toBe(layout);
    }
  });

  it("header and body rows share one inline grid template (a dynamic Tailwind class would never be generated)", () => {
    render(<TxnTable txns={two()} />);
    const header = screen.getAllByRole("row")[0];
    expect(header.style.gridTemplateColumns.split(" ")).toHaveLength(8);
    expect(bodyRows()[0].style.gridTemplateColumns).toBe(header.style.gridTemplateColumns);
  });

  it("compact folds Owner and Type out of the template too, and the table's floor is the grid's own width", () => {
    media.width = 1100;
    render(<TxnTable txns={two()} />);
    const header = screen.getAllByRole("row")[0];
    const template = header.style.gridTemplateColumns;
    const cols = template.replace(/minmax\((\d+)px,\s*1fr\)/g, "$1px").split(" ");     // a flexible track counts at its minimum
    expect(cols).toHaveLength(6);
    expect(bodyRows()[0].style.gridTemplateColumns).toBe(template);
    // the table pans only when its floor exceeds the content area, so the floor must be the columns plus gaps
    const px = cols.map((c) => Number(c.replace("px", "")));
    expect(screen.getByRole("table").style.minWidth).toBe(`${px.reduce((a, b) => a + b, 0) + 8 * (px.length - 1)}px`);
  });

  it("only the compact grid's Account track flexes (it uses the spare room, so the digits that tell two cards apart show)", () => {
    const tracks = (w: number) => {
      media.width = w;
      const { unmount } = render(<TxnTable txns={two()} />);
      const t = screen.getAllByRole("row")[0].style.gridTemplateColumns;
      unmount();
      return t.replace(/minmax\((\d+)px,\s*1fr\)/g, "minmax($1px,1fr)").split(" ");
    };
    const compact = tracks(1100);
    expect(compact.filter((c) => c.startsWith("minmax"))).toEqual(["minmax(120px,1fr)"]);
    expect(compact[1], "Account is the second column").toBe("minmax(120px,1fr)");
    expect(tracks(1280).some((c) => c.startsWith("minmax")), "the full grid keeps fixed tracks").toBe(false);
  });

  it("the header pads by the body scroller's scrollbar, so the flexible track lines up with the rows beneath it", () => {
    // classic scrollbars: the body loses ~15px when its list overflows, the header row never does
    media.width = 1100;
    render(<TxnTable txns={two()} />);
    const header = () => screen.getAllByRole("row")[0];
    expect(header().style.paddingRight, "no scrollbar, no gutter").toBe("0px");
    const scroller = bodyRows()[0].closest('[role=rowgroup]') as HTMLElement;
    const size = (offset: number, client: number) => {
      Object.defineProperty(scroller, "offsetWidth", { configurable: true, value: offset });
      Object.defineProperty(scroller, "clientWidth", { configurable: true, value: client });
      act(() => ro.callbacks.forEach((fn) => fn()));
    };
    size(757, 742);
    expect(header().style.paddingRight).toBe("15px");
    expect(screen.getByRole("table").style.minWidth, "and the floor grows by it, so the rows never get a sideways scrollbar").toBe("705px");
    size(757, 757);                                                      // the list shrank: the scrollbar went away
    expect(header().style.paddingRight).toBe("0px");
    expect(screen.getByRole("table").style.minWidth).toBe("690px");
  });

  it("the expanded row says Owner and Type where the layout folds them, and only there", () => {
    for (const [w, folded] of [[390, true], [1100, true], [1280, false]] as const) {
      media.width = w;
      const { unmount } = render(<TxnTable txns={[tx({ owner: "Alex", type: "fee", vendor: "Alpha Cafe" })]} />);
      fireEvent.click(bodyRows()[0]);
      expect(screen.queryAllByText(/Owner: Alex · Type: fee/), `${w}px`).toHaveLength(folded ? 1 : 0);
      unmount();
    }
  });

  it("each grid's floor fits the content area at the narrowest viewport that draws it, so it never pans", () => {
    // content area = viewport - 208px sidebar - 40px page padding - 32px card padding - 15px scrollbar
    const content = (w: number) => w - 208 - 40 - 32 - 15;
    for (const w of [1024, 1280]) {
      media.width = w;
      const { unmount } = render(<TxnTable txns={two()} />);
      const floor = Number(screen.getByRole("table").style.minWidth.replace("px", ""));
      expect(floor, `${w}px: ${floor}px floor in a ${content(w)}px content area`).toBeLessThanOrEqual(content(w));
      unmount();
    }
  });

  it("a stacked list has no grid template and no width floor (nothing to pan)", () => {
    media.width = 390;
    render(<TxnTable txns={two()} />);
    expect(bodyRows()[0].style.gridTemplateColumns).toBe("");
    expect(screen.getByRole("table").style.minWidth).toBe("");
  });

  it("the expanded row spans exactly the cells its layout draws (stacked 2, compact 6, full 8)", () => {
    for (const [w, cells] of [[390, 2], [1100, 6], [1280, 8]] as const) {
      media.width = w;
      const { unmount } = render(<TxnTable txns={two()} />);
      expect(within(bodyRows()[0]).getAllByRole("cell"), `${w}px: cells in a row`).toHaveLength(cells);
      fireEvent.click(bodyRows()[0]);
      const detail = screen.getAllByRole("cell").find((c) => c.hasAttribute("aria-colspan"))!;
      expect(detail.getAttribute("aria-colspan"), `${w}px: aria-colspan`).toBe(String(cells));
      unmount();
    }
  });

  it("grid layouts count the header as row 1; the list numbers every row, in every layout", () => {
    for (const [w, count, first] of [[390, "2", "1"], [1100, "3", "2"], [1280, "3", "2"]] as const) {
      media.width = w;
      const { unmount } = render(<TxnTable txns={two()} />);
      expect(screen.getByRole("table").getAttribute("aria-rowcount"), `${w}px: rowcount`).toBe(count);
      expect(bodyRows()[0].getAttribute("aria-rowindex"), `${w}px: first body row`).toBe(first);
      expect(bodyRows()[1].getAttribute("aria-rowindex"), `${w}px: second body row`).toBe(String(Number(first) + 1));
      unmount();
    }
  });

  it("measures per layout: compact and full are different columns, so a flip re-creates the rows", () => {
    render(<TxnTable txns={two()} />);
    const full = virt.opts.getItemKey(0);
    setWidth(1100);
    expect(virt.opts.getItemKey(0)).not.toBe(full);
    expect(virt.opts.estimateSize(0)).toBe(29);
  });

  it("every id of the column model is a real sortable column: as the sort, exactly one header is ascending", () => {
    // Transactions feeds TXN_COLUMN_IDS to useSort, so an id with no column behind it would be a ?sort= the
    // register accepts and cannot show; and a column missing from the list could never be sorted by URL
    render(<TxnTable txns={two()} />);
    const headerCount = screen.getAllByRole("columnheader").length;
    cleanup();
    expect(headerCount, "the full grid has a header per id").toBe(TXN_COLUMN_IDS.length);
    for (const id of TXN_COLUMN_IDS) {
      const { unmount } = render(<TxnTable txns={two()} sort={[{ id, desc: false }]} onSortChange={() => {}} />);
      const sorted = screen.getAllByRole("columnheader").filter((h) => h.getAttribute("aria-sort") === "ascending");
      expect(sorted, `sort=${id}:asc marks one header`).toHaveLength(1);
      unmount();
    }
  });
});

describe("TxnTable roving tab stop", () => {
  const stops = () => bodyRows().filter((r) => r.getAttribute("tabindex") === "0");

  it("exactly one row is a tab stop; ArrowDown/ArrowUp/Home/End move it and the focus, clamped at the ends", () => {
    render(<TxnTable txns={three()} />);
    expect(stops()).toHaveLength(1);
    expect(bodyRows()[1].getAttribute("tabindex")).toBe("-1");
    focusRow(bodyRows()[0]);
    fireEvent.keyDown(bodyRows()[0], { key: "ArrowDown" });
    expect(document.activeElement).toBe(bodyRows()[1]);
    expect(stops()).toEqual([bodyRows()[1]]);
    fireEvent.keyDown(bodyRows()[1], { key: "End" });
    expect(document.activeElement).toBe(bodyRows()[2]);
    fireEvent.keyDown(bodyRows()[2], { key: "ArrowDown" });
    expect(document.activeElement).toBe(bodyRows()[2]);
    expect(stops(), "past the end the stop stays on the last row").toEqual([bodyRows()[2]]);
    fireEvent.keyDown(bodyRows()[2], { key: "Home" });
    expect(document.activeElement).toBe(bodyRows()[0]);
    fireEvent.keyDown(bodyRows()[0], { key: "ArrowUp" });
    expect(document.activeElement).toBe(bodyRows()[0]);
    expect(stops(), "before the start the stop stays on the first row").toEqual([bodyRows()[0]]);
    expect(virt.scrolled.every((i) => i >= 0 && i <= 2), "never asks the virtualizer for a row past either end").toBe(true);
  });

  it("is the same single tab stop in the stacked list and the compact grid", () => {
    for (const w of [390, 1100]) {
      media.width = w;
      const { unmount } = render(<TxnTable txns={three()} />);
      expect(stops(), `${w}px`).toHaveLength(1);
      expect(bodyRows().filter((r) => r.getAttribute("tabindex") === "-1"), `${w}px`).toHaveLength(2);
      focusRow(bodyRows()[0]);
      fireEvent.keyDown(bodyRows()[0], { key: "ArrowDown" });
      expect(stops(), `${w}px after ArrowDown`).toEqual([bodyRows()[1]]);
      unmount();
    }
  });

  it("focusing a row by pointer makes it the tab stop", () => {
    render(<TxnTable txns={three()} />);
    fireEvent.focus(bodyRows()[2]);
    expect(bodyRows()[2].getAttribute("tabindex")).toBe("0");
    expect(bodyRows()[0].getAttribute("tabindex")).toBe("-1");
  });

  it("scrolls the virtualizer to the target before focusing it (the row may not be rendered yet)", () => {
    render(<TxnTable txns={three()} />);
    focusRow(bodyRows()[0]);
    fireEvent.keyDown(bodyRows()[0], { key: "ArrowDown" });
    fireEvent.keyDown(bodyRows()[1], { key: "End" });
    fireEvent.keyDown(bodyRows()[2], { key: "Home" });
    expect(virt.scrolled).toEqual([1, 2, 0]);
  });

  it("keeps the active row in the rendered window however far the list has scrolled, so the tab stop never vanishes", () => {
    render(<TxnTable txns={three()} />);
    const far = { startIndex: 100, endIndex: 110, overscan: 20, count: 500 };
    const win = virt.opts.rangeExtractor(far) as number[];
    expect(win[0], "the active row (0) leads the window").toBe(0);
    expect(win.slice(1)).toEqual(Array.from({ length: 51 }, (_, i) => 80 + i));   // plus the usual overscanned window
    fireEvent.focus(bodyRows()[2]);
    expect(virt.opts.rangeExtractor({ startIndex: 0, endIndex: 1, overscan: 0, count: 3 })).toEqual([0, 1, 2]);
    expect(virt.opts.rangeExtractor({ startIndex: 0, endIndex: 0, overscan: 0, count: 3 })).toEqual([0, 2]);
  });

  it("only keys pressed on the row itself move the stop (not ones from inside its cells)", () => {
    render(<TxnTable txns={three()} />);
    focusRow(bodyRows()[0]);
    fireEvent.keyDown(within(bodyRows()[0]).getAllByRole("cell")[0], { key: "ArrowDown" });
    expect(stops()).toEqual([bodyRows()[0]]);
    expect(virt.scrolled).toEqual([]);
    expect(document.activeElement).toBe(bodyRows()[0]);
  });

  it("when the list shrinks past the stop, the first row becomes it (never zero stops, and no crash on the stale index)", () => {
    const { rerender } = render(<TxnTable txns={three()} />);
    fireEvent.focus(bodyRows()[2]);
    rerender(<TxnTable txns={two()} />);
    expect(bodyRows()).toHaveLength(2);
    expect(stops()).toEqual([bodyRows()[0]]);
  });

  it("every row carries its index for the focus lookup", () => {
    render(<TxnTable txns={three()} />);
    expect(bodyRows().map((r) => r.getAttribute("data-row-index"))).toEqual(["0", "1", "2"]);
  });

  const focused = () => (document.activeElement as HTMLElement).dataset.rowIndex;
  const manyRows = (n: number) => Array.from({ length: n }, (_, i) =>
    tx({ date: new Date(Date.UTC(2026, 5, 30) - i * 864e5).toISOString().slice(0, 10), vendor: `V${i}` }));   // date desc = array order

  it("PageDown / PageUp move the stop by what the body shows less one row, clamped, and bring the row into view", () => {
    // a tablet's stacked list is virtualised in its own body like the grid (a phone's pages instead: below)
    for (const [w, clientHeight] of [[1280, 290], [1000, 470]] as const) {      // ten 29px grid rows / ten 47px stacked rows
      media.width = w;
      const { unmount } = render(<TxnTable txns={manyRows(30)} />);
      const scroller = bodyRows()[0].closest('[role=rowgroup]') as HTMLElement;
      Object.defineProperty(scroller, "clientHeight", { configurable: true, value: clientHeight });
      focusRow(bodyRows()[0]);
      const seen: (string | undefined)[] = [];
      for (const key of ["PageDown", "PageDown", "PageDown", "PageDown", "PageUp", "PageUp", "PageUp", "PageUp"]) {
        fireEvent.keyDown(document.activeElement!, { key });
        seen.push(focused());
      }
      expect(seen, `${w}px`).toEqual(["9", "18", "27", "29", "20", "11", "2", "0"]);
      expect(virt.scrolled, `${w}px: every step scrolls to its row`).toEqual([9, 18, 27, 29, 20, 11, 2, 0]);
      expect(stops().map((r) => r.dataset.rowIndex), `${w}px`).toEqual(["0"]);
      unmount();
      virt.scrolled.length = 0;
    }
  });

  it("a page is at least one row when the body has no measurable height", () => {
    render(<TxnTable txns={manyRows(5)} />);
    focusRow(bodyRows()[0]);
    fireEvent.keyDown(bodyRows()[0], { key: "PageDown" });
    expect(focused()).toBe("1");
  });

  it("leaves Ctrl / Meta / Alt combinations to the browser and the OS (Shift is not one of them)", () => {
    render(<TxnTable txns={three()} />);
    focusRow(bodyRows()[1]);
    for (const key of ["ArrowDown", "ArrowUp", "Home", "End", "PageDown", "PageUp"]) {
      for (const mod of ["ctrlKey", "metaKey", "altKey"] as const) {
        const notPrevented = fireEvent.keyDown(bodyRows()[1], { key, [mod]: true });
        expect(notPrevented, `${mod}+${key} is left alone`).toBe(true);
      }
    }
    expect(virt.scrolled).toEqual([]);
    expect(stops()).toEqual([bodyRows()[1]]);
    expect(fireEvent.keyDown(bodyRows()[1], { key: "ArrowDown", shiftKey: true }), "Shift+ArrowDown still moves").toBe(false);
    expect(stops()).toEqual([bodyRows()[2]]);
  });

  it("goes back to the first row when the list or the sort really changes, and stays put for a re-render of the same list", () => {
    const list = three();
    const { rerender } = render(<TxnTable txns={list} />);
    fireEvent.focus(bodyRows()[2]);
    fireEvent.click(bodyRows()[2]);                                  // opening a row re-renders the table: the stop stays
    expect(stops()).toEqual([bodyRows()[2]]);
    rerender(<TxnTable txns={list} />);                              // the parent re-rendered with the same list
    expect(stops()).toEqual([bodyRows()[2]]);
    rerender(<TxnTable txns={three()} />);                           // different rows (new ids): a filter edit
    expect(stops()).toEqual([bodyRows()[0]]);
    fireEvent.focus(bodyRows()[1]);
    fireEvent.click(within(screen.getByRole("columnheader", { name: /Amount/ })).getByRole("button"));   // a new sort
    expect(stops()).toEqual([bodyRows()[0]]);
  });

  // after a category save the answers overlay hands the table a NEW array, and ~800ms later the server echo
  // hands it another - same rows both times; a keyboard user on row 60 must not be thrown back to row 0
  it("a new array holding the same rows keeps the stop, even when the overlay changed a row's category", () => {
    const list = manyRows(8);
    const { rerender } = render(<TxnTable txns={list} />);
    fireEvent.focus(bodyRows()[5]);
    rerender(<TxnTable txns={list.map((t) => ({ ...t }))} />);       // new array, new objects, same content
    expect(stops()).toEqual([bodyRows()[5]]);
    rerender(<TxnTable txns={list.map((t, i) => (i === 3
      ? { ...t, category: "Groceries", category_source: "answer" as const } : t))} />);                // the overlay previewed an answer
    expect(stops()).toEqual([bodyRows()[5]]);
    rerender(<TxnTable txns={[...list]} />);
    expect(stops()).toEqual([bodyRows()[5]]);
  });

  it("each way the list can really change resets the stop: fewer or more rows, a different first row, a different last row", () => {
    const list = manyRows(8);
    const variants: [string, Transaction[]][] = [
      ["shorter", list.slice(0, 7)],
      ["a row gone from the middle (same first and last)", list.filter((_, i) => i !== 3)],
      ["a row added in the middle (same first and last)", [...list.slice(0, 4), tx({ date: "2026-06-20", vendor: "Mid" }), ...list.slice(4)]],
      ["longer", [...list, tx({ date: "2025-01-01", vendor: "Extra" })]],
      ["a different first row", [tx({ date: "2026-07-01", vendor: "New first" }), ...list.slice(1)]],
      ["a different last row", [...list.slice(0, 7), tx({ date: "2025-01-01", vendor: "New last" })]],
    ];
    for (const [what, next] of variants) {
      const { rerender, unmount } = render(<TxnTable txns={list} />);
      fireEvent.focus(bodyRows()[5]);
      expect(stops().map((r) => r.dataset.rowIndex), `${what}: starts on row 5`).toEqual(["5"]);
      rerender(<TxnTable txns={next} />);
      expect(stops().map((r) => r.dataset.rowIndex), what).toEqual(["0"]);
      unmount();
    }
  });

  it("goes back to the first row when a controlled sort changes under it too", () => {
    const list = three();
    const { rerender } = render(<TxnTable txns={list} sort={[{ id: "vendor", desc: false }]} onSortChange={() => {}} />);
    fireEvent.focus(bodyRows()[2]);
    rerender(<TxnTable txns={list} sort={[{ id: "vendor", desc: false }]} onSortChange={() => {}} />);   // same sort, new array identity
    expect(stops()).toEqual([bodyRows()[2]]);
    rerender(<TxnTable txns={list} sort={[{ id: "vendor", desc: true }]} onSortChange={() => {}} />);
    expect(stops()).toEqual([bodyRows()[0]]);
  });

  it("with the window scrolled away from the stop it is still rendered and still the only one; End focuses a row far outside the window", () => {
    virt.window = { start: 100, end: 110, overscan: 5 };
    render(<TxnTable txns={manyRows(200)} />);
    const rendered = () => bodyRows().map((r) => Number(r.dataset.rowIndex));
    const range = (a: number, b: number) => Array.from({ length: b - a + 1 }, (_, i) => a + i);
    expect(rendered(), "the stop (row 0) plus the window, in DOM order").toEqual([0, ...range(95, 115)]);
    expect(stops().map((r) => r.dataset.rowIndex)).toEqual(["0"]);
    focusRow(bodyRows()[0]);
    fireEvent.keyDown(bodyRows()[0], { key: "End" });                // the mock's window does NOT follow scrollToIndex: only the stop is rendered there
    expect(virt.scrolled).toEqual([199]);
    expect(focused(), "row 199 was rendered in the same commit and took the focus").toBe("199");
    expect(stops().map((r) => r.dataset.rowIndex)).toEqual(["199"]);
    expect(rendered(), "the stop moved with the focus; row 0 left the window").toEqual([...range(95, 115), 199]);
  });
});

describe("TxnTable provenance for touch", () => {
  it("the expanded row repeats the chip's tooltip in words (a finger never sees a title)", () => {
    render(<TxnTable txns={[tx({ category_source: "learned", vendor: "Alpha Cafe" })]} />);
    fireEvent.click(bodyRows()[0]);
    expect(screen.getByText(/learned: learned from your answers/)).toBeDefined();
  });

  it("a row filed by AI carries the AI chip in every layout, and its open row says so in words", () => {
    for (const w of [390, 1100, 1280]) {
      media.width = w;
      const { unmount } = render(<TxnTable txns={[tx({ category_source: "ai", vendor: "Tallgrass Outfitters" })]} />);
      const chip = screen.getByTitle("Filed by AI");
      expect(chip.childNodes[0].textContent, `${w}px`).toBe("AI");
      expect(bodyRows()[0].contains(chip), `${w}px: the chip is on the row`).toBe(true);
      fireEvent.click(bodyRows()[0]);
      expect(screen.queryAllByText(/^AI: Filed by AI$/), `${w}px`).toHaveLength(1);
      unmount();
      // the same row once the family answered it: the overlay stamps it an answer, and the chip goes
      const r2 = render(<TxnTable txns={[tx({ category_source: "answer", vendor: "Tallgrass Outfitters" })]} />);
      expect(screen.queryByTitle("Filed by AI"), `${w}px answered`).toBeNull();
      r2.unmount();
    }
  });

  it("says it in every layout, and says nothing for a source with no chip", () => {
    for (const w of [390, 1100, 1280]) {
      media.width = w;
      const { unmount } = render(<TxnTable txns={[tx({ category_source: "learned", vendor: "Alpha Cafe" })]} />);
      fireEvent.click(bodyRows()[0]);
      expect(screen.queryAllByText(/learned: learned from your answers/), `${w}px`).toHaveLength(1);
      unmount();
      const r2 = render(<TxnTable txns={[tx({ category_source: "keyword", vendor: "Alpha Cafe" })]} />);
      fireEvent.click(bodyRows()[0]);
      expect(screen.queryAllByText(/learned from your answers/), `${w}px keyword`).toHaveLength(0);
      r2.unmount();
    }
  });

  it("the words are never truncated away behind a long descriptor (the expanded row wraps)", () => {
    render(<TxnTable txns={[tx({ category_source: "learned", description: "X".repeat(300) })]} />);
    fireEvent.click(bodyRows()[0]);
    const words = screen.getByText(/learned: learned from your answers/);
    expect(words.closest(".truncate"), "no truncating ancestor inside the detail row").toBeNull();
  });
});

describe("TxnTable controlled sort", () => {
  it("renders the sort it is given and reports a header click through onSortChange", () => {
    const onSortChange = vi.fn();
    render(<TxnTable txns={three()} sort={[{ id: "vendor", desc: false }]} onSortChange={onSortChange} />);
    expect(order()).toEqual(["Alpha", "Bravo", "Charlie"]);          // vendor A-Z; the default (date desc) would be Bravo Alpha Charlie
    fireEvent.click(within(screen.getByRole("columnheader", { name: /Amount/ })).getByRole("button"));
    expect(onSortChange).toHaveBeenCalledWith([{ id: "amount", desc: true }]);
    expect(order()).toEqual(["Alpha", "Bravo", "Charlie"]);          // controlled: rows wait for the parent
  });

  it("follows the parent: a new sort prop reorders, and the header shows it", () => {
    const { rerender } = render(<TxnTable txns={three()} sort={[{ id: "vendor", desc: false }]} onSortChange={() => {}} />);
    rerender(<TxnTable txns={three()} sort={[{ id: "amount", desc: false }]} onSortChange={() => {}} />);
    expect(order()).toEqual(["Alpha", "Charlie", "Bravo"]);
    expect(screen.getByRole("columnheader", { name: /Amount/ }).getAttribute("aria-sort")).toBe("ascending");
  });

  it("the stacked Sort-by select and direction button report through onSortChange too", () => {
    media.width = 390;
    const onSortChange = vi.fn();
    render(<TxnTable txns={three()} sort={[{ id: "date", desc: true }]} onSortChange={onSortChange} />);
    fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "vendor" } });
    expect(onSortChange).toHaveBeenLastCalledWith([{ id: "vendor", desc: false }]);
    fireEvent.click(dirButton());
    expect(onSortChange).toHaveBeenLastCalledWith([{ id: "date", desc: false }]);
    expect(order()).toEqual(["Bravo", "Alpha", "Charlie"]);          // still date, newest first: the parent decides
  });

  it("is internal state unless BOTH props are given (a lone sort prop must not freeze the register)", () => {
    // the props type refuses one half (hence the directive); the runtime check is the backstop for a JS caller
    // @ts-expect-error `sort` without `onSortChange`
    render(<TxnTable txns={three()} sort={[{ id: "vendor", desc: false }]} />);
    expect(order()).toEqual(["Bravo", "Alpha", "Charlie"]);          // the lone sort is ignored: internal date desc
    fireEvent.click(within(screen.getByRole("columnheader", { name: /Amount/ })).getByRole("button"));
    expect(order()).toEqual(["Bravo", "Charlie", "Alpha"]);          // amount, -10 first: it moved
  });

  it("a sort on a column the compact grid folds away is still named, so the order is explained", () => {
    media.width = 1100;
    render(<TxnTable txns={three()} sort={[{ id: "type", desc: false }]} onSortChange={() => {}} />);
    expect(screen.getByText(/sorted by Type/)).toBeDefined();
  });
});

describe("TxnTable selection", () => {
  // the register with a real useRowSelection behind it, and the selection echoed into the DOM
  function Harness({ txns }: { txns: Transaction[] }) {
    const selection = useRowSelection();
    return (
      <>
        <TxnTable txns={txns} selection={selection} />
        <output data-testid="picked">{[...selection.selected].join(",")}</output>
      </>
    );
  }
  const picked = () => (screen.getByTestId("picked").textContent ?? "").split(",").filter(Boolean);
  const rowBox = (name: string) => screen.getByRole("checkbox", { name: new RegExp(`^Select ${name},`) }) as HTMLInputElement;
  const headerBox = () => screen.getByRole("checkbox", { name: /^Select (every row|the first \d+ rows) shown$/ }) as HTMLInputElement;
  const rowOf = (name: string) => bodyRows().find((r) => within(r).queryByText(name))!;
  const types = ["purchase", "fee", "income", "dividend", "interest", "transfer", "cc-payment", "investment"] as const;
  const mixed = () => [tx({ vendor: "Alpha", date: "2026-01-03" }), tx({ vendor: "Bravo", date: "2026-01-02", type: "transfer" }),
    tx({ vendor: "Charlie", date: "2026-01-01", type: "income", category: "Income" })];

  it("only the rows the category editor can answer have a checkbox: the type set is EDITABLE_TYPES, for the box AND the Edit button", () => {
    expect([...EDITABLE_TYPES].sort()).toEqual(["fee", "income", "purchase"]);
    for (const type of types) {
      const { unmount } = render(<Harness txns={[tx({ vendor: "Alpha Cafe", type })]} />);
      const editable = EDITABLE_TYPES.has(type);
      expect(screen.queryAllByRole("checkbox", { name: /^Select Alpha Cafe,/ }), `${type}: row checkbox`).toHaveLength(editable ? 1 : 0);
      fireEvent.click(bodyRows()[0]);
      expect(screen.queryAllByRole("button", { name: /Edit category/ }), `${type}: Edit button`).toHaveLength(editable ? 1 : 0);
      unmount();
    }
  });

  it("clicking a row's checkbox selects it without opening the row; the row says so (aria-selected); a second click clears", () => {
    render(<Harness txns={mixed()} />);
    expect(rowOf("Alpha").getAttribute("aria-selected")).toBe("false");
    fireEvent.click(rowBox("Alpha"));
    expect(picked()).toHaveLength(1);
    expect(rowBox("Alpha").checked).toBe(true);
    expect(rowOf("Alpha").getAttribute("aria-selected")).toBe("true");
    expect(rowOf("Alpha").getAttribute("aria-expanded"), "the click did not reach the row").toBe("false");
    fireEvent.click(rowBox("Alpha"));
    expect(picked()).toEqual([]);
    expect(rowOf("Alpha").getAttribute("aria-selected")).toBe("false");
  });

  it("a row's box is named by vendor, date and amount, so a vendor that repeats down the register is told apart", () => {
    render(<Harness txns={[tx({ vendor: "Alpha", date: "2026-01-03", amount: -12.5 }), tx({ vendor: "Alpha", date: "2026-01-02", amount: -40 }),
      tx({ vendor: null, description: "RAW DESC", date: "2026-01-01", amount: 900, type: "income" })]} />);
    const names = screen.getAllByRole("checkbox").map((b) => b.getAttribute("aria-label")).filter((n) => !/^Select (every|the first)/.test(n ?? ""));
    expect(names).toEqual(["Select Alpha, 2026-01-03, \u2212$12.50", "Select Alpha, 2026-01-02, \u2212$40.00", "Select RAW DESC, 2026-01-01, $900.00"]);
    expect(new Set(names).size).toBe(3);
  });

  it("a row that cannot be answered is not selectable and says nothing about selection", () => {
    render(<Harness txns={mixed()} />);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(3);          // header + the purchase + the income row
    expect(rowOf("Bravo").hasAttribute("aria-selected")).toBe(false);
    focusRow(rowOf("Bravo"));
    fireEvent.keyDown(rowOf("Bravo"), { key: "x" });
    expect(picked()).toEqual([]);
  });

  it("the header checkbox selects every answerable row shown (never the transfer), then clears; part-way it reads as mixed", () => {
    render(<Harness txns={mixed()} />);
    expect(headerBox().checked).toBe(false);
    expect(headerBox().indeterminate).toBe(false);
    fireEvent.click(rowBox("Alpha"));
    expect(headerBox().indeterminate, "some, not all").toBe(true);
    expect(headerBox().checked).toBe(false);
    fireEvent.click(headerBox());                                       // some selected: fill in the rest
    expect(picked()).toHaveLength(2);
    expect(headerBox().checked).toBe(true);
    expect(headerBox().indeterminate).toBe(false);
    expect(rowOf("Bravo").hasAttribute("aria-selected"), "the transfer was left out").toBe(false);
    fireEvent.click(headerBox());
    expect(picked()).toEqual([]);
    expect(headerBox().checked).toBe(false);
  });

  it("the header checkbox is not a sort button and carries no aria-sort", () => {
    render(<Harness txns={mixed()} />);
    const cell = headerBox().closest('[role="columnheader"]') as HTMLElement;
    expect(within(cell).queryAllByRole("button")).toHaveLength(0);
    expect(cell.hasAttribute("aria-sort")).toBe(false);
    expect(screen.getAllByRole("columnheader")).toHaveLength(9);
  });

  describe("past the cap", () => {
    const many = (n: number) => Array.from({ length: n }, (_, i) =>
      tx({ vendor: `Vendor ${String(i).padStart(3, "0")}`, date: `2026-02-${String(1 + (i % 28)).padStart(2, "0")}` }));

    it("the header selects the first SELECTION_CAP rows in display order, and from that capped state it CLEARS on the next click", () => {
      render(<Harness txns={many(SELECTION_CAP + 20)} />);
      expect(headerBox().getAttribute("aria-label"), "it says it will not take them all").toBe(`Select the first ${SELECTION_CAP} rows shown`);
      fireEvent.click(headerBox());
      expect(picked()).toHaveLength(SELECTION_CAP);
      expect(headerBox().checked, "full: shows the state that clears").toBe(true);
      expect(headerBox().indeterminate).toBe(false);
      fireEvent.click(headerBox());
      expect(picked(), "one click empties a capped selection").toEqual([]);
      expect(headerBox().checked).toBe(false);
    });

    it("a row beyond the cap cannot be added (and says so); a selected one can still be dropped", () => {
      render(<Harness txns={many(SELECTION_CAP + 2)} />);
      fireEvent.click(headerBox());
      const selected = bodyRows().filter((r) => r.getAttribute("aria-selected") === "true");
      const unselected = bodyRows().filter((r) => r.getAttribute("aria-selected") === "false");
      expect(selected).toHaveLength(SELECTION_CAP);
      expect(unselected).toHaveLength(2);
      const box = within(unselected[0]).getByRole("checkbox") as HTMLInputElement;
      expect(box.getAttribute("aria-disabled")).toBe("true");
      fireEvent.click(box);
      expect(picked()).toHaveLength(SELECTION_CAP);
      expect(box.checked).toBe(false);
      const drop = within(selected[0]).getByRole("checkbox") as HTMLInputElement;
      expect(drop.hasAttribute("aria-disabled")).toBe(false);
      fireEvent.click(drop);
      expect(picked()).toHaveLength(SELECTION_CAP - 1);
      expect(box.getAttribute("aria-disabled"), "room again").toBeNull();
    });
  });

  describe("keyboard (the register keeps ONE tab stop: the checkboxes are not in the tab order)", () => {
    it("every row's checkbox is tabIndex -1; the active row stays the body's only tab stop", () => {
      render(<Harness txns={mixed()} />);
      const boxes = screen.getAllByRole("checkbox").filter((b) => b !== headerBox());
      expect(boxes).toHaveLength(2);
      for (const box of boxes) expect(box.getAttribute("tabindex")).toBe("-1");
      expect(bodyRows().filter((r) => r.getAttribute("tabindex") === "0")).toHaveLength(1);
      expect(document.querySelectorAll('[tabindex="0"]')).toHaveLength(1);
    });

    it("the header's select-all stays reachable by Tab (like the sort buttons beside it) - the only keyboard way to select several", () => {
      render(<Harness txns={mixed()} />);
      expect(headerBox().tabIndex).toBe(0);                              // natural order, no negative index
      expect(headerBox().disabled).toBe(false);
      cleanup();
      render(<Harness txns={[tx({ type: "transfer", vendor: "Bravo" })]} />);
      expect(headerBox().disabled, "nothing answerable to select: the box is off").toBe(true);
    });

    it("x on the focused row toggles its selection; Enter and Space still open it; arrows still move the stop", () => {
      render(<Harness txns={mixed()} />);
      const alpha = rowOf("Alpha");
      focusRow(alpha);
      fireEvent.keyDown(alpha, { key: "x" });
      expect(alpha.getAttribute("aria-selected")).toBe("true");
      expect(rowBox("Alpha").checked).toBe(true);
      expect(alpha.getAttribute("aria-expanded"), "x does not open the row").toBe("false");
      fireEvent.keyDown(alpha, { key: "X" });
      expect(alpha.getAttribute("aria-selected")).toBe("false");
      fireEvent.keyDown(alpha, { key: "Enter" });
      expect(alpha.getAttribute("aria-expanded")).toBe("true");
      expect(picked(), "Enter does not select").toEqual([]);
      fireEvent.keyDown(alpha, { key: " " });
      expect(alpha.getAttribute("aria-expanded")).toBe("false");
      fireEvent.keyDown(alpha, { key: "ArrowDown" });
      expect(bodyRows()[1].getAttribute("tabindex")).toBe("0");
      expect(alpha.getAttribute("tabindex")).toBe("-1");
    });

    it("x with Ctrl, Meta or Alt held is the browser's, and x typed inside the expanded row is the editor's", () => {
      render(<Harness txns={mixed()} />);
      const alpha = rowOf("Alpha");
      focusRow(alpha);
      for (const mod of ["ctrlKey", "metaKey", "altKey"]) fireEvent.keyDown(alpha, { key: "x", [mod]: true });
      expect(picked()).toEqual([]);
      fireEvent.click(alpha);                                            // open the row
      // a key whose target is something inside the row (the Edit button; the editor's inputs bubble the same
      // way) is that control's, not the row's
      fireEvent.keyDown(within(alpha.parentElement!).getByRole("button", { name: /Edit category/ }), { key: "x" });
      expect(picked()).toEqual([]);
      fireEvent.keyDown(alpha, { key: "x" });                            // ... while the same key on the row does select
      expect(picked()).toHaveLength(1);
    });
  });

  describe("layouts", () => {
    const widths = (w: number) => {
      media.width = w;
      const { unmount } = render(<Harness txns={mixed()} />);
      const header = screen.getAllByRole("row")[0];
      const cols = header.style.gridTemplateColumns.replace(/minmax\((\d+)px,\s*1fr\)/g, "$1px").split(" ");
      const floor = Number(screen.getByRole("table").style.minWidth.replace("px", ""));
      const out = { cols, floor, header, firstBody: bodyRows()[0].style.gridTemplateColumns };
      unmount();
      return out;
    };

    it("the selection column comes first in the compact and the full grid, and the body rows share the template", () => {
      for (const [w, n] of [[1100, 7], [1280, 9]] as const) {
        media.width = w;
        render(<Harness txns={mixed()} />);
        const header = screen.getAllByRole("row")[0];
        expect(header.style.gridTemplateColumns.split(" "), `${w}px: tracks`).toHaveLength(n);
        expect(bodyRows()[0].style.gridTemplateColumns).toBe(header.style.gridTemplateColumns);
        expect(within(header.firstElementChild as HTMLElement).getByRole("checkbox"), `${w}px: the first header cell holds the box`).toBeDefined();
        expect(within(bodyRows()[0]).getAllByRole("cell"), `${w}px: cells`).toHaveLength(n);
        cleanup();
      }
    });

    it("neither grid pans with the extra column: each floor still fits the content area at the narrowest viewport that draws it", () => {
      // content area = viewport - 208px sidebar - 40px page padding - 32px card padding - 15px scrollbar
      const content = (w: number) => w - 208 - 40 - 32 - 15;
      for (const w of [1024, 1280]) {
        const { floor } = widths(w);
        expect(floor, `${w}px: ${floor}px floor in a ${content(w)}px content area`).toBeLessThanOrEqual(content(w));
      }
    });

    it("the expanded row spans every cell with the selection column in (stacked 3, compact 7, full 9)", () => {
      for (const [w, cells] of [[390, 3], [1100, 7], [1280, 9]] as const) {
        media.width = w;
        const { unmount } = render(<Harness txns={mixed()} />);
        fireEvent.click(rowOf("Alpha"));
        expect(within(rowOf("Alpha")).getAllByRole("cell"), `${w}px: cells in a row`).toHaveLength(cells);
        const detail = screen.getAllByRole("cell").find((c) => c.hasAttribute("aria-colspan"))!;
        expect(detail.getAttribute("aria-colspan"), `${w}px: aria-colspan`).toBe(String(cells));
        unmount();
      }
    });

    it("the stacked list puts a 44x44 checkbox target at the start of each answerable row; tapping it does not open the row", () => {
      media.width = 390;
      render(<Harness txns={mixed()} />);
      const row = rowOf("Alpha");
      const first = within(row).getAllByRole("cell")[0];
      const box = within(first).getByRole("checkbox") as HTMLInputElement;
      const label = box.closest("label") as HTMLElement;
      expect(label.className.split(/\s+/)).toEqual(expect.arrayContaining(["min-h-11", "min-w-11"]));
      fireEvent.click(label);                                             // the padding around the box, not the box
      expect(picked()).toHaveLength(1);
      expect(row.getAttribute("aria-expanded"), "the tap reached the label, not the row").toBe("false");
      expect(row.getAttribute("aria-selected")).toBe("true");
      expect(box.getAttribute("tabindex")).toBe("-1");
      // a row that cannot be answered has no target, but keeps its cell count aligned with the others
      expect(within(rowOf("Bravo")).queryAllByRole("checkbox")).toHaveLength(0);
      expect(within(rowOf("Bravo")).getAllByRole("cell")).toHaveLength(3);
    });

    it("the stacked list has no header row, so select-all rides its toolbar: a 44px target that selects, then clears", () => {
      media.width = 390;
      render(<Harness txns={mixed()} />);
      expect(screen.queryAllByRole("columnheader")).toHaveLength(0);
      const all = headerBox();
      expect((all.closest("label") as HTMLElement).className.split(/\s+/)).toContain("min-h-11");
      fireEvent.click(all);
      expect(picked()).toHaveLength(2);                                  // the purchase and the income row, not the transfer
      expect(all.checked).toBe(true);
      fireEvent.click(all);
      expect(picked()).toEqual([]);
      cleanup();
      render(<TxnTable txns={mixed()} />);
      expect(screen.queryAllByRole("checkbox"), "no selection prop, no box").toHaveLength(0);
    });

    it("the stacked hint mentions x (a keyboard reaches the stacked list below 1024px too), only when there is a selection", () => {
      media.width = 390;
      render(<Harness txns={mixed()} />);
      expect(screen.getByText(/click a row for the raw descriptor · x selects it/)).toBeDefined();
      cleanup();
      render(<TxnTable txns={mixed()} />);
      expect(screen.getByText("click a row for the raw descriptor")).toBeDefined();
    });

    it("for a finger the hint says tap and names no key, in every layout: the words follow the pointer, not the width", () => {
      media.touch = true;
      for (const w of [390, 1280]) {
        media.width = w;
        setPrivate(true);
        render(<Harness txns={mixed()} />);
        const hint = screen.getByText(/tap a row for the raw descriptor/).textContent ?? "";
        expect(hint, `${w}px`).not.toMatch(/x selects it|Alt\+Shift\+P|click/);
        cleanup();
        setPrivate(false);
      }
      // a mouse on a phone-wide window still clicks, and is told about x
      media.touch = false;
      media.width = 390;
      render(<Harness txns={mixed()} />);
      expect(screen.getByText(/click a row for the raw descriptor · x selects it/)).toBeDefined();
    });

    it("the stacked list estimates a taller row when it carries a checkbox target, and the plain list is unchanged", () => {
      media.width = 1000;                                                 // a tablet's stacked list (a phone's is not virtualised)
      render(<Harness txns={mixed()} />);
      expect(virt.opts.estimateSize(0)).toBeGreaterThanOrEqual(56);
      cleanup();
      render(<TxnTable txns={mixed()} />);
      expect(virt.opts.estimateSize(0)).toBe(47);
    });

    it("without a selection prop there is no checkbox, no aria-selected and no extra column (every other caller)", () => {
      render(<TxnTable txns={mixed()} />);
      expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
      expect(bodyRows().some((r) => r.hasAttribute("aria-selected"))).toBe(false);
      expect(screen.getAllByRole("columnheader")).toHaveLength(8);
    });
  });
});

describe("TxnTable running balance", () => {
  // date -> end-of-day balance, as lib/runningBalance hands it over
  const byDate = () => new Map([["2026-01-02", 1234.5], ["2026-01-01", 1000]]);
  const content = (w: number) => w - 208 - 40 - 32 - 15;                // viewport - sidebar - page - card padding - scrollbar
  const floorAt = (w: number, props: Record<string, unknown>) => {
    media.width = w;
    const { unmount } = render(<TxnTable txns={two()} {...props} />);
    const floor = Number(screen.getByRole("table").style.minWidth.replace("px", ""));
    unmount();
    return floor;
  };
  // three days of one, two and three rows (the amounts and balances are distinct, so a row's text names them)
  const days = () => [
    tx({ date: "2026-01-03", vendor: "Alpha", amount: -1.11 }), tx({ date: "2026-01-03", vendor: "Bravo", amount: -2.22 }),
    tx({ date: "2026-01-03", vendor: "Charlie", amount: -3.33 }),
    tx({ date: "2026-01-02", vendor: "Delta", amount: -4.44 }), tx({ date: "2026-01-02", vendor: "Echo", amount: -5.55 }),
    tx({ date: "2026-01-01", vendor: "Foxtrot", amount: -6.66 }),
  ];
  const dayBalances = () => new Map([["2026-01-03", 3000], ["2026-01-02", 2000], ["2026-01-01", 1000]]);
  /** per displayed row (top to bottom): the end-of-day balance it shows, or null */
  const shown = () => bodyRows().map((r) => /\$[123],000\.00/.exec(r.textContent ?? "")?.[0] ?? null);

  it("shows a Balance column only when a running balance is given, formatted, never sortable", () => {
    render(<TxnTable txns={two()} runningBalance={byDate()} />);
    const h = screen.getByRole("columnheader", { name: /Balance/ });
    expect(within(h).queryByRole("button")).toBeNull();
    expect(h.getAttribute("aria-sort")).toBeNull();
    expect(screen.getByText("$1,234.50")).toBeDefined();
    cleanup();
    render(<TxnTable txns={two()} />);
    expect(screen.queryByRole("columnheader", { name: /Balance/ })).toBeNull();
  });

  it("newest first: each day's number sits on the FIRST row of its run (the one nearest the later days)", () => {
    render(<TxnTable txns={days()} runningBalance={dayBalances()} />);
    expect(shown()).toEqual(["$3,000.00", null, null, "$2,000.00", null, "$1,000.00"]);
  });
  it("oldest first: the LAST row of each run, again the one nearest the later days", () => {
    render(<TxnTable txns={days()} runningBalance={dayBalances()} />);
    fireEvent.click(within(screen.getByRole("columnheader", { name: /Date/ })).getByRole("button"));
    expect(screen.getByRole("columnheader", { name: /Date/ }).getAttribute("aria-sort")).toBe("ascending");
    expect(shown()).toEqual(["$1,000.00", null, "$2,000.00", null, null, "$3,000.00"]);
  });
  it("the newest row carries the account's latest balance (a feed balance newer than every row)", () => {
    const rows = days();
    const rb = runningBalance(rows, [{ date: "2026-01-09", balance: 1234.5, available: null, limit: null, utilization: null, source: "simplefin" }],
      { kind: "checking", asset_class: "Cash" })!;
    render(<TxnTable txns={rows} runningBalance={rb} />);
    expect(within(bodyRows()[0]).getByText("$1,234.50")).toBeDefined();
  });
  it("a date the map has no entry for shows an empty cell, never a 0", () => {
    render(<TxnTable txns={days()} runningBalance={new Map([["2026-01-02", 2000]])} />);
    expect(shown()).toEqual([null, null, null, "$2,000.00", null, null]);
    expect(bodyRows().map((r) => within(r).getAllByRole("cell").length)).toEqual([9, 9, 9, 9, 9, 9]);      // the column is there on every row
    expect(screen.queryByText(/\$0\.00/)).toBeNull();
  });

  it("under any other sort the column is blank and the toolbar says when it returns", () => {
    render(<TxnTable txns={days()} runningBalance={dayBalances()} />);
    expect(screen.queryByText(/balance shows when sorted by date/)).toBeNull();
    fireEvent.click(within(screen.getByRole("columnheader", { name: /Amount/ })).getByRole("button"));
    expect(shown().every((b) => b === null), "no scattered numbers").toBe(true);
    expect(screen.getByText(/balance shows when sorted by date/)).toBeDefined();
    expect(screen.getByRole("columnheader", { name: /Balance/ })).toBeDefined();
    fireEvent.click(within(screen.getByRole("columnheader", { name: /Date/ })).getByRole("button"));
    expect(shown().filter((b) => b !== null)).toHaveLength(3);
    expect(screen.queryByText(/balance shows when sorted by date/)).toBeNull();
  });
  it("no balance map, no note", () => {
    render(<TxnTable txns={days()} sort={[{ id: "amount", desc: true }]} onSortChange={() => {}} />);
    expect(screen.queryByText(/balance shows when sorted by date/)).toBeNull();
  });

  it("the stacked row says `bal` on its second line on the same rows, and not under a non-date sort (the select)", () => {
    media.width = 390;
    render(<TxnTable txns={days()} runningBalance={dayBalances()} />);
    const bal = () => bodyRows().map((r) => /bal (\$[123],000\.00)/.exec(r.textContent ?? "")?.[1] ?? null);
    expect(bal()).toEqual(["$3,000.00", null, null, "$2,000.00", null, "$1,000.00"]);
    fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "amount" } });
    expect(bal().every((b) => b === null)).toBe(true);
    expect(screen.getByText(/click a row for the raw descriptor · balance shows when sorted by date/)).toBeDefined();
  });

  it("hideAccount drops the Account column (constant on an account page) from every layout, and the stacked row's account name", () => {
    const rows = [tx({ vendor: "Alpha Cafe", account_name: "Constant Checking" })];
    for (const [w, headers] of [[1280, 7], [1100, 5]] as const) {
      media.width = w;
      const { unmount } = render(<TxnTable txns={rows} hideAccount />);
      expect(screen.getAllByRole("columnheader"), `${w}px: headers`).toHaveLength(headers);
      expect(screen.queryByRole("columnheader", { name: /Account/ }), `${w}px`).toBeNull();
      expect(screen.queryByText("Constant Checking"), `${w}px: no cell holds the account name`).toBeNull();
      unmount();
    }
    media.width = 390;
    render(<TxnTable txns={rows} hideAccount />);
    expect(screen.queryByText("Constant Checking")).toBeNull();
    expect(screen.getByLabelText("Sort by").textContent).not.toMatch(/Account/);
  });
  it("without Account, Vendor is the one flexible track in both grids, so the columns fill the card", () => {
    for (const [w, vendorAt] of [[1280, 2], [1100, 1]] as const) {          // full: date owner VENDOR ...; compact: date VENDOR ...
      media.width = w;
      const { unmount } = render(<TxnTable txns={two()} runningBalance={byDate()} hideAccount />);
      const tracks = screen.getAllByRole("row")[0].style.gridTemplateColumns.split(" ");
      const flexible = tracks.flatMap((t, i) => (t.startsWith("minmax") ? [i] : []));
      expect(flexible, `${w}px: the flexible tracks`).toEqual([vendorAt]);
      unmount();
    }
    media.width = 1280;                                                    // and with Account the full grid still has none
    render(<TxnTable txns={two()} />);
    expect(screen.getAllByRole("row")[0].style.gridTemplateColumns).not.toMatch(/minmax/);
  });

  it("the Balance column does not make either grid pan once the account page drops Account: each floor fits its narrowest viewport", () => {
    for (const w of [1024, 1280]) {
      const floor = floorAt(w, { runningBalance: byDate(), hideAccount: true });
      expect(floor, `${w}px: ${floor}px floor in a ${content(w)}px content area`).toBeLessThanOrEqual(content(w));
    }
  });
  it("(and it would pan at 1280px if Account stayed - the reason hideAccount exists)", () => {
    expect(floorAt(1280, { runningBalance: byDate() })).toBeGreaterThan(content(1280));
  });

  it("the expanded row spans the cells its layout draws with the balance column in", () => {
    for (const [w, cells] of [[1100, 6], [1280, 8]] as const) {                  // balance in, account out
      media.width = w;
      const { unmount } = render(<TxnTable txns={two()} runningBalance={byDate()} hideAccount />);
      fireEvent.click(bodyRows()[0]);
      expect(within(bodyRows()[0]).getAllByRole("cell"), `${w}px: cells in a row`).toHaveLength(cells);
      const detail = screen.getAllByRole("cell").find((c) => c.hasAttribute("aria-colspan"))!;
      expect(detail.getAttribute("aria-colspan"), `${w}px: aria-colspan`).toBe(String(cells));
      unmount();
    }
  });
});

// Privacy mode: every amount on a register row goes through fmtUSD (so it masks with the rest of the page), the toolbar
// says amounts are hidden in EVERY layout, and the CSV export - a file for the family's own spreadsheet - stays raw
// and says so in visible words (a title never shows on a phone).
describe("TxnTable under privacy mode", () => {
  const rows = () => [tx({ date: "2026-01-02", vendor: "Alpha Cafe", amount: -12.5 }), tx({ date: "2026-01-01", vendor: "Beta Books", amount: -40 })];
  const text = () => bodyRows().map((r) => r.textContent ?? "").join(" | ");
  it("masks the amount of every row in every layout, and the Balance column too", () => {
    setPrivate(true);
    for (const w of [390, 1100, 1280]) {
      media.width = w;
      const { unmount } = render(<TxnTable txns={rows()} runningBalance={new Map([["2026-01-02", 1234.5], ["2026-01-01", 1000]])} />);
      const t = text();
      expect(t.includes(MASK), `${w}px: the mask shows`).toBe(true);
      expect(/\$\d|12\.50|1,234|1,000/.test(t), `${w}px: no amount survives`).toBe(false);
      expect(t.includes("Alpha Cafe"), `${w}px: the vendor is not an amount`).toBe(true);
      unmount();
    }
  });
  it("names a row's checkbox without its amount", () => {
    setPrivate(true);
    function Harness() { const selection = useRowSelection(); return <TxnTable txns={rows()} selection={selection} />; }
    render(<Harness />);
    const names = screen.getAllByRole("checkbox").map((b) => b.getAttribute("aria-label") ?? "");
    expect(names.some((n) => /^Select Alpha Cafe, 2026-01-02, /.test(n))).toBe(true);
    expect(names.some((n) => /\$|12\.50/.test(n)), "no amount in an accessible name").toBe(false);
    expect(names).toContain(`Select Alpha Cafe, 2026-01-02, ${MASK}`);
  });
  it("says amounts are hidden in the desktop caption and in the stacked layout, and nothing when they are shown", () => {
    for (const w of [1280, 1100, 390]) {
      media.width = w;
      const { unmount } = render(<TxnTable txns={rows()} />);
      expect(screen.queryByText(/amounts hidden/), `${w}px, privacy off`).toBeNull();
      act(() => setPrivate(true));
      expect(screen.getByText(/amounts hidden/), `${w}px, privacy on`).toBeDefined();
      act(() => setPrivate(false));
      unmount();
    }
  });
  it("the desktop caption keeps its sort note and names the hotkey", () => {
    setPrivate(true);
    render(<TxnTable txns={rows()} />);
    expect(screen.getByText(/amounts hidden \(Alt\+Shift\+P\) \u00b7 click a row for the raw descriptor.*sorted by Date/)).toBeDefined();
  });
  it("the Export CSV button says in visible text, not only a title, that the file is never masked - and the file holds the real amounts", () => {
    setPrivate(true);
    render(<TxnTable txns={rows()} />);
    const button = screen.getByRole("button", { name: /Export CSV/ });
    expect(button.getAttribute("title")).toMatch(/never masked/);
    expect(screen.getByText(/never masked/).textContent).toMatch(/real amounts/);
    fireEvent.click(button);
    const [, csv] = vi.mocked(downloadCsv).mock.calls[0];
    expect(csv).toMatch(/-12\.5/);
    expect(csv).toMatch(/-40/);
    expect(csv).not.toContain(MASK);
  });
  // a Venmo row's descriptor is "Venmo payment \u2014 <memo>": free text that can state an amount
  const memoRow = () => tx({ date: "2026-01-02", vendor: null, description: "Venmo payment \u2014 split of $40.25 dinner, net 1,234.56", amount: -61 });
  it("masks the amounts in a raw descriptor (the expanded row) and in a vendor-less row's checkbox name, and leaves the words", () => {
    setPrivate(true);
    function Harness() { const selection = useRowSelection(); return <TxnTable txns={[memoRow()]} selection={selection} />; }
    render(<Harness />);
    fireEvent.click(bodyRows()[0]);                                             // open the row: its raw descriptor shows
    const table = screen.getByRole("table").textContent ?? "";                  // the descriptor is the expanded row, beside the row
    expect(table).toContain(`split of ${MASK} dinner, net ${MASK}`);
    expect(/40\.25|1,234\.56|\$\d/.test(table), "no amount from the memo").toBe(false);
    const names = screen.getAllByRole("checkbox").map((b) => b.getAttribute("aria-label") ?? "");
    expect(names.some((n) => /^Select Venmo payment \u2014 split of /.test(n)), "the name keeps the words").toBe(true);
    expect(names.some((n) => /40\.25|1,234\.56|\$\d/.test(n)), "no amount in an accessible name").toBe(false);
  });
  it("shows the raw descriptor as written with privacy off, and the CSV keeps it raw either way", () => {
    render(<TxnTable txns={[memoRow()]} />);
    fireEvent.click(bodyRows()[0]);
    expect(screen.getByRole("table").textContent).toContain("split of $40.25 dinner, net 1,234.56");
    cleanup();
    setPrivate(true);
    render(<TxnTable txns={[memoRow()]} />);
    fireEvent.click(screen.getByRole("button", { name: /Export CSV/ }));
    const [, csv] = vi.mocked(downloadCsv).mock.calls[0];
    expect(csv).toContain("split of $40.25 dinner");
  });
  it("the note is there with privacy off as well (the Export button is the same one)", () => {
    render(<TxnTable txns={rows()} />);
    expect(screen.getByText(/never masked/)).toBeDefined();
  });
});

describe("TxnTable over split purchases", () => {
  const m = txFactory({ vendor: "Synth Mart", date: "2026-01-05" });
  const parent = m({ id: "p", amount: -300, is_parent: true, category: "Groceries" });
  const a = m({ id: "a", amount: -210, parent_id: "p", category: "Groceries", category_source: "split" });
  const b = m({ id: "b", amount: -90, parent_id: "p", category: "Shopping", subcategory: "Home Goods", category_source: "split" });
  const other = m({ id: "o", amount: -40, vendor: "Other Shop", date: "2026-01-04" });
  // the register with a real useRowSelection behind it, and the selection echoed into the DOM
  function Harness({ txns }: { txns: Transaction[] }) {
    const selection = useRowSelection();
    return (
      <>
        <TxnTable txns={txns} selection={selection} />
        <output data-testid="picked">{[...selection.selected].join(",")}</output>
      </>
    );
  }
  const picked = () => (screen.getByTestId("picked").textContent ?? "").split(",").filter(Boolean);
  const rowOf = (text: RegExp) => bodyRows().find((r) => text.test(r.textContent ?? ""))!;
  const stops = () => bodyRows().filter((r) => r.getAttribute("tabindex") === "0");
  beforeEach(() => { world.splitParents = new Map([["p", parent]]); world.childrenByParent = new Map([["p", [a, b]]]); });
  afterEach(() => { world.splitParents = new Map(); world.childrenByParent = new Map(); });

  it("every part present: ONE parent row at the full amount reading 'split · 2 parts' with the chip, no checkbox, no Edit; expanded, it lists the parts and names the rule", () => {
    render(<Harness txns={[a, b, other]} />);
    expect(bodyRows()).toHaveLength(2);
    const prow = rowOf(/Synth Mart/);
    expect(prow.textContent).toMatch(/split · 2 parts/);
    expect(prow.textContent).toMatch(/split by a vendor rule/);        // the chip's tooltip text, in the row
    expect(prow.textContent).not.toMatch(/split of/);
    expect(prow.textContent).toMatch(/300\.00/);
    expect(within(prow).queryByRole("checkbox")).toBeNull();
    expect(prow.getAttribute("aria-selected"), "a parent says nothing about selection").toBeNull();
    expect(within(rowOf(/Other Shop/)).getByRole("checkbox")).toBeDefined();
    fireEvent.click(prow);
    const parts = screen.getByTestId("split-parts");
    expect(parts.textContent).toMatch(/Groceries[^$]*\$210\.00/);
    expect(parts.textContent).toMatch(/Shopping \/ Home Goods[^$]*\$90\.00/);
    // an outflow reads negative, as everywhere else in the app (fmtUSD's U+2212): the sign is not dropped
    expect(parts.textContent).toContain("Groceries · \u2212$210.00");
    expect(parts.textContent).toContain("Shopping / Home Goods · \u2212$90.00");
    const rule = screen.getByText(/split by the Synth Mart rule into 2 parts/);
    expect(rule.querySelector("code")?.textContent).toBe("vendor_rules.yaml");
    // the parts list is plain text, so the sentence says how a part is answered: by filtering to its category
    expect(rule.textContent).toMatch(/or filter to a part's category to answer it$/);
    expect(rule.textContent).not.toMatch(/answer a part/);
    expect(screen.queryByRole("button", { name: /Edit category/ })).toBeNull();
  });

  it("one part filtered away: the present part shows as 'split of Synth Mart' with its own category, and is answerable", () => {
    render(<Harness txns={[b, other]} />);
    expect(bodyRows(), "the part itself, never its parent").toHaveLength(2);
    const prow = rowOf(/Synth Mart/);
    expect(prow.textContent).toMatch(/split of\s*Synth Mart/);
    expect(prow.textContent).not.toMatch(/split · /);
    expect(prow.textContent).toMatch(/Shopping \/ Home Goods/);
    expect(prow.textContent).toMatch(/90\.00/);
    expect(prow.textContent).not.toMatch(/300\.00/);
    fireEvent.click(within(prow).getByRole("checkbox"));
    expect(rowOf(/Synth Mart/).getAttribute("aria-selected"), "the part's box selects it").toBe("true");
    fireEvent.click(prow);
    expect(screen.getByRole("button", { name: /Edit category/ })).toBeDefined();
    const note = screen.getByText(/part of a [^ ]*300\.00 purchase/);
    expect(note.textContent, "the purchase's date reads like every other date in prose").toMatch(/purchase on [A-Z][a-z]{2} \d{1,2}, \d{4}: an answer here/);
    expect(note.textContent).not.toMatch(/\d{4}-\d{2}-\d{2}/);
  });

  it("a part whose parent is not in the export shows as itself, with no note about a purchase it cannot name", () => {
    world.splitParents = new Map();
    render(<TxnTable txns={[a, b]} />);
    expect(bodyRows()).toHaveLength(2);
    fireEvent.click(bodyRows()[0]);
    expect(screen.queryByText(/part of a/)).toBeNull();
    expect(screen.queryByTestId("split-parts")).toBeNull();
  });

  it("the CSV lists the parts the filter passed with parent_id - never the parent row - and the button counts them", () => {
    render(<TxnTable txns={[a, b, other]} />);
    expect(screen.getByRole("button", { name: /Export CSV: 3 rows \(2 transactions\)$/ })).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: /Export CSV/ }));
    const lines = (vi.mocked(downloadCsv).mock.calls[0][1] as string).trim().split("\r\n");
    expect(lines[0].endsWith(",needs_review,parent_id")).toBe(true);
    expect(lines).toHaveLength(4);
    expect(lines.filter((l) => l.endsWith(",p"))).toHaveLength(2);
    expect(lines.some((l) => l.includes("-300"))).toBe(false);
    const ordinary = lines.find((l) => l.includes("Other Shop"))!;
    expect(ordinary.endsWith(","), "an ordinary row's parent_id cell is empty").toBe(true);
    expect(ordinary.endsWith(",o"), "never its own id").toBe(false);
    cleanup();
    render(<TxnTable txns={[b, other]} />);
    expect(screen.getByRole("button", { name: /Export CSV: 2 rows$/ }), "a lone part is one transaction: rows and transactions agree").toBeDefined();
    vi.mocked(downloadCsv).mockClear();
    fireEvent.click(screen.getByRole("button", { name: /Export CSV/ }));
    const lone = (vi.mocked(downloadCsv).mock.calls[0][1] as string).trim().split("\r\n");
    expect(lone, "the part that passed and the ordinary row, not the hidden part").toHaveLength(3);
    expect(lone.some((l) => l.includes("-210"))).toBe(false);
  });

  it("x never selects a parent row, and the header box selects the answerable rows without it", () => {
    render(<Harness txns={[a, b, other]} />);
    const prow = rowOf(/Synth Mart/);
    focusRow(prow);
    fireEvent.keyDown(prow, { key: "x" });
    expect(prow.getAttribute("aria-selected")).toBeNull();
    expect(picked(), "x on a parent selects nothing").toEqual([]);
    fireEvent.click(screen.getByRole("checkbox", { name: "Select every row shown" }));
    expect(picked(), "the header box takes the answerable row only").toEqual(["o"]);
    expect(bodyRows().filter((r) => r.getAttribute("aria-selected") === "true")).toHaveLength(1);
    expect(rowOf(/Other Shop/).getAttribute("aria-selected")).toBe("true");
    cleanup();
    render(<Harness txns={[b, other]} />);                      // a lone part is answerable: x selects it
    const lone = rowOf(/Synth Mart/);
    focusRow(lone);
    fireEvent.keyDown(lone, { key: "x" });
    expect(picked()).toEqual(["b"]);
  });

  it("stacked: a parent row says 'split · 2 parts' on its second line and has no box, a lone part says 'split of' on its first and has one", () => {
    setWidth(390);
    render(<Harness txns={[a, b]} />);
    expect(bodyRows()[0].textContent).toMatch(/split · 2 parts/);
    expect(within(bodyRows()[0]).getByText(/split · 2 parts/).className, "the label is never cut to 'split · 2 …'").toContain("shrink-0");
    expect(within(bodyRows()[0]).queryByRole("checkbox")).toBeNull();
    cleanup();
    render(<Harness txns={[b]} />);
    expect(bodyRows()[0].textContent).toMatch(/split of\s*Synth Mart/);
    expect(within(bodyRows()[0]).getByRole("checkbox")).toBeDefined();
  });

  it("the tab stop follows the DISPLAYED rows: the same display rows from a re-ordered part list keep it", () => {
    const { rerender } = render(<TxnTable txns={[a, b, other]} />);
    fireEvent.focus(bodyRows()[1]);
    expect(stops()).toEqual([bodyRows()[1]]);
    rerender(<TxnTable txns={[b, a, other]} />);                // the parent row stands in for both: nothing on screen changed
    expect(stops()).toEqual([bodyRows()[1]]);
  });

  it("Enter and Space open a parent row like any other (aria-expanded follows), from the keyboard alone", () => {
    render(<TxnTable txns={[a, b, other]} />);
    const prow = rowOf(/Synth Mart/);
    focusRow(prow);
    expect(prow.getAttribute("aria-expanded")).toBe("false");
    fireEvent.keyDown(prow, { key: "Enter" });
    expect(prow.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("split-parts")).toBeDefined();
    fireEvent.keyDown(prow, { key: " " });
    expect(prow.getAttribute("aria-expanded")).toBe("false");
    fireEvent.keyDown(prow, { key: " " });
    expect(prow.getAttribute("aria-expanded")).toBe("true");
    fireEvent.keyDown(prow, { key: "Enter" });
    expect(prow.getAttribute("aria-expanded")).toBe("false");
  });

  // the running balance (an account page) is keyed by DATE and put on the first row of each day's run; the
  // parent is the one row that stands for its parts, so a day that opens with it still carries the day's number
  describe("with a running balance", () => {
    const points = [{ date: "2026-01-05", balance: 1000, available: null, limit: null, utilization: null, source: "simplefin" }];
    // built from the PARTS, as AccountPage does: the 5th nets -300 (once), so the 4th ended at 1300
    const balances = () => runningBalance([a, b, other], points, { kind: "checking", asset_class: "Cash" })!;

    it("a day whose first displayed row is a split parent shows that day's balance on it, and the purchase counts once", () => {
      const map = balances();
      expect(map.get("2026-01-05")).toBe(1000);
      expect(map.get("2026-01-04")).toBe(1300);
      render(<TxnTable txns={[a, b, other]} runningBalance={map} />);
      expect(bodyRows()).toHaveLength(2);
      const prow = rowOf(/Synth Mart/);
      expect(prow.textContent, "the parent row carries the 5th's balance").toMatch(/\$1,000\.00/);
      expect(prow.textContent).toMatch(/\u2212\$300\.00/);
      expect(prow.textContent, "the full purchase once, never a part's amount").not.toMatch(/210\.00|90\.00/);
      const orow = rowOf(/Other Shop/);
      expect(orow.textContent, "and the 4th's balance sits one purchase back, not two").toMatch(/\$1,300\.00/);
      expect(screen.getByRole("columnheader", { name: "Balance" })).toBeDefined();
    });

    it("stacked, the parent's second line says `bal` too", () => {
      setWidth(390);
      render(<TxnTable txns={[a, b, other]} runningBalance={balances()} />);
      expect(rowOf(/Synth Mart/).textContent).toMatch(/bal \$1,000\.00/);
      expect(rowOf(/Other Shop/).textContent).toMatch(/bal \$1,300\.00/);
    });

    it("a lone part carries the day's balance the same way (it is that day's first row)", () => {
      render(<TxnTable txns={[b, other]} runningBalance={balances()} />);
      expect(rowOf(/Synth Mart/).textContent).toMatch(/\$1,000\.00/);
    });
  });

  it("aria-rowcount counts the display rows: the parent once", () => {
    render(<TxnTable txns={[a, b, other]} />);
    expect(screen.getByRole("table").getAttribute("aria-rowcount")).toBe("3");      // header + 2 display rows
  });

  it("under privacy mode the parts' amounts in the expanded parent and the parent's amount in a part's note are masked", () => {
    setPrivate(true);
    render(<TxnTable txns={[a, b]} />);
    fireEvent.click(bodyRows()[0]);
    const parts = screen.getByTestId("split-parts");
    expect(parts.textContent).toContain(MASK);
    expect(parts.textContent).not.toMatch(/210|90\.00/);
    cleanup();
    render(<TxnTable txns={[b]} />);
    fireEvent.click(bodyRows()[0]);
    const note = screen.getByText(/part of a .* purchase/);
    expect(note.textContent).toContain(MASK);
    expect(note.textContent).not.toContain("300");
  });

  it("the split words in the expanded row take the chip's own ink (lib/categorySource), and a learned row keeps its own", () => {
    const ink = chipInk(sourceChip("split")!);
    expect(ink).toBe("text-sky-300");                          // the hue index.css remaps for the light theme
    render(<TxnTable txns={[b]} />);
    fireEvent.click(bodyRows()[0]);
    expect(screen.getByText(/split: split by a vendor rule/).className).toContain(ink);
    expect(screen.getByText(/part of a .* purchase/).className).toContain(ink);
    cleanup();
    render(<TxnTable txns={[a, b]} />);
    fireEvent.click(bodyRows()[0]);
    expect(screen.getByText(/split by the Synth Mart rule/).className).toContain(ink);
    cleanup();
    render(<TxnTable txns={[tx({ category_source: "learned", vendor: "Alpha Cafe" })]} />);
    fireEvent.click(bodyRows()[0]);
    expect(screen.getByText(/learned: learned from your answers/).className).toContain(chipInk(sourceChip("learned")!));
    expect(screen.getByText(/learned: learned from your answers/).className).not.toContain(ink);
  });

  it("with no part among the rows the table is exactly what it was (the same rows, no regrouping)", () => {
    world.splitParents = new Map();
    world.childrenByParent = new Map();
    render(<TxnTable txns={two()} />);
    expect(bodyRows()).toHaveLength(2);
    expect(screen.getByRole("button", { name: /Export CSV: 2 rows$/ })).toBeDefined();
  });
});

// ---------------------------------------------------------------- tags, account links, empty state, row numbers, category sort

describe("TxnTable shows tags and links the account", () => {
  const focusFirst = (row: HTMLElement) => act(() => { row.focus(); });
  const rows = () => [
    tx({ id: "t1", date: "2026-01-03", vendor: "Alpha Cafe", tags: ["kids", "trip"], account: "demo_checking_0101", account_name: "Joint Checking" }),
    tx({ id: "t2", date: "2026-01-02", vendor: "Beta Books", account: "demo_savings_0202", account_name: "Joint Savings" }),
  ];
  const rowOf = (text: RegExp) => bodyRows().find((r) => text.test(r.textContent ?? ""))!;

  it("draws a chip per tag beside the vendor (a screen reader hears 'tag kids'); an untagged row draws none", () => {
    render(<TxnTable txns={rows()} />);
    const chips = within(rowOf(/Alpha Cafe/)).getAllByText(/^(kids|trip)$/);
    expect(chips.map((c) => c.textContent)).toEqual(["tag kids", "tag trip"]);
    expect(chips[0].closest("[role=cell]")!.textContent, "in the Vendor cell").toContain("Alpha Cafe");
    expect(within(rowOf(/Beta Books/)).queryByText(/^tag /)).toBeNull();
  });

  it("stacked, the chips ride the second line", () => {
    setWidth(390);
    render(<TxnTable txns={rows()} />);
    const [line1, line2] = within(rowOf(/Alpha Cafe/)).getAllByRole("cell");
    expect(line2.textContent).toContain("kids");
    expect(line2.textContent).toContain("trip");
    expect(line1.textContent).not.toContain("kids");
    expect(within(rowOf(/Beta Books/)).getAllByRole("cell")[1].textContent).not.toContain("tag ");
  });

  it("the Account column links its page in the QUIET look: secondary text at rest, blue + underline on hover or focus", () => {
    render(<TxnTable txns={rows()} />);
    const link = within(rowOf(/Alpha Cafe/)).getByRole("link", { name: "Joint Checking" });
    expect(link.getAttribute("href")).toBe("/accounts/demo_checking_0101");
    const cls = link.className.split(/\s+/);
    expect(cls, "ordinary secondary text at rest").toContain("text-zinc-400");
    expect(cls, "no underline at rest").not.toContain("underline");
    expect(cls).not.toContain("text-blue-400");
    expect(cls).toEqual(expect.arrayContaining(["hover:text-blue-400", "hover:underline", "focus-visible:text-blue-400", "focus-visible:underline"]));
    expect(within(rowOf(/Beta Books/)).getByRole("link", { name: "Joint Savings" }).getAttribute("href")).toBe("/accounts/demo_savings_0202");
  });

  it("that per-row link is for a pointer (the register is one tab stop) and a click on it does not also open the row", () => {
    render(<TxnTable txns={rows()} />);
    const link = within(rowOf(/Alpha Cafe/)).getByRole("link", { name: "Joint Checking" });
    expect(link.getAttribute("tabindex")).toBe("-1");
    fireEvent.click(link);
    expect(rowOf(/Alpha Cafe/).getAttribute("aria-expanded"), "the row did not toggle").toBe("false");
  });

  it("the open row has a normal, tabbable 'Account page →' link beside Edit category - that is the keyboard's way", () => {
    render(<TxnTable txns={rows()} />);
    expect(screen.queryByRole("link", { name: "Account page →" }), "only while a row is open").toBeNull();
    const row = rowOf(/Alpha Cafe/);
    focusFirst(row);
    fireEvent.keyDown(row, { key: "Enter" });                              // a keyboard opens the row
    const link = screen.getByRole("link", { name: "Account page →" });
    expect(link.getAttribute("href")).toBe("/accounts/demo_checking_0101");
    expect(link.hasAttribute("tabindex"), "no tabindex at all: a normal tab stop").toBe(false);
    expect((link as HTMLAnchorElement).tabIndex).toBe(0);
    link.focus();
    expect(document.activeElement).toBe(link);
    expect(link.className, "a link that looks like one").toContain("underline");
    expect(link.closest("[role=row]"), "inside the detail row, not the clickable row").not.toBe(row);
    expect(link.parentElement!.textContent).toContain("Edit category");     // beside the Edit button
    fireEvent.keyDown(link, { key: "Enter" });                              // the row's own key handler leaves a key typed on the link alone
    expect(rowOf(/Alpha Cafe/).getAttribute("aria-expanded")).toBe("true");
  });

  it("stacked (a phone): the second line names the account as plain text, and the link is a 44px target in the open row", () => {
    setWidth(390);
    render(<TxnTable txns={rows()} />);
    const row = rowOf(/Alpha Cafe/);
    expect(within(row).queryByRole("link"), "no link on the second line").toBeNull();
    expect(row.textContent).toContain("Joint Checking");
    fireEvent.click(row);
    const link = screen.getByRole("link", { name: "Account page →" });
    expect(link.getAttribute("href")).toBe("/accounts/demo_checking_0101");
    expect(link.className.split(/\s+/)).toContain("min-h-11");
    expect(link.hasAttribute("tabindex")).toBe(false);
  });

  it("an account page (no Account column) has no account links at all, open row included", () => {
    render(<TxnTable txns={rows()} hideAccount />);
    expect(screen.queryByRole("link")).toBeNull();
    fireEvent.click(rowOf(/Alpha Cafe/));
    expect(screen.queryByRole("link"), "the open row does not link the page you are on").toBeNull();
  });
});

describe("TxnTable with no rows", () => {
  it("is one empty-state row that says so and offers to clear the filters; Export CSV is disabled", () => {
    const onClear = vi.fn();
    render(<TxnTable txns={[]} onClear={onClear} />);
    const empty = screen.getByTestId("register-empty");
    expect(empty.closest("[role=row]"), "an ARIA row, not a loose div in the table").not.toBeNull();
    expect(empty.textContent).toContain("No transactions match these filters.");
    fireEvent.click(within(empty).getByRole("button", { name: "Clear all filters" }));
    expect(onClear).toHaveBeenCalledTimes(1);
    const csv = screen.getByRole("button", { name: /Export CSV: 0 rows$/ }) as HTMLButtonElement;
    expect(csv.disabled).toBe(true);
    fireEvent.click(csv);
    expect(downloadCsv, "no header-only file").not.toHaveBeenCalled();
    expect(screen.getByRole("table").getAttribute("aria-rowcount"), "the header and the empty-state row").toBe("2");
  });

  it("without a way to clear (an account page) it still says so, with no button; stacked, it is row 1", () => {
    setWidth(390);
    render(<TxnTable txns={[]} />);
    expect(screen.getByTestId("register-empty").textContent).toContain("No transactions to show.");
    expect(within(screen.getByTestId("register-empty")).queryByRole("button")).toBeNull();
    expect(screen.getByTestId("register-empty").closest("[role=row]")!.getAttribute("aria-rowindex")).toBe("1");
    expect(screen.getByRole("table").getAttribute("aria-rowcount")).toBe("1");
  });

  it("is NOT shown while there are rows, and Export CSV works and counts them (singular for one)", () => {
    render(<TxnTable txns={[tx({ vendor: "Alpha Cafe" })]} onClear={() => {}} />);
    expect(screen.queryByTestId("register-empty")).toBeNull();
    const csv = screen.getByRole("button", { name: /Export CSV: 1 row$/ }) as HTMLButtonElement;
    expect(csv.disabled).toBe(false);
    fireEvent.click(csv);
    expect(downloadCsv).toHaveBeenCalledTimes(1);
  });
});

describe("TxnTable numbers every row, the open row's detail included (aria-rowindex)", () => {
  const three3 = () => [
    tx({ id: "r1", date: "2026-01-03", vendor: "Alpha" }), tx({ id: "r2", date: "2026-01-02", vendor: "Bravo" }), tx({ id: "r3", date: "2026-01-01", vendor: "Charlie" }),
  ];
  const indexes = () => screen.getAllByRole("row").map((r) => Number(r.getAttribute("aria-rowindex")));
  it("an open row's detail takes the next number, the rows after it move down one, and the count includes it", () => {
    for (const [w, header] of [[1280, 1], [390, 0]] as const) {
      setWidth(w);
      const { unmount } = render(<TxnTable txns={three3()} />);
      expect(indexes(), `${w}px closed`).toEqual(Array.from({ length: header + 3 }, (_, i) => i + 1));
      fireEvent.click(bodyRows()[1]);                                          // open Bravo
      const all = indexes();
      expect(all, `${w}px: no number is missing or repeated`).toEqual(Array.from({ length: header + 4 }, (_, i) => i + 1));
      expect(screen.getByRole("table").getAttribute("aria-rowcount"), `${w}px: the count includes the detail`).toBe(String(header + 4));
      const detail = screen.getAllByRole("row").find((r) => !r.hasAttribute("aria-expanded") && /d/.test(r.textContent ?? "") && r.querySelector("[aria-colspan]"))!;
      expect(Number(detail.getAttribute("aria-rowindex")), `${w}px: the detail follows its row`).toBe(Number(bodyRows()[1].getAttribute("aria-rowindex")) + 1);
      expect(Number(bodyRows()[2].getAttribute("aria-rowindex")), `${w}px: the next row moved down`).toBe(Number(detail.getAttribute("aria-rowindex")) + 1);
      fireEvent.click(bodyRows()[1]);                                          // close it again: back to the plain run
      expect(indexes(), `${w}px reclosed`).toEqual(Array.from({ length: header + 3 }, (_, i) => i + 1));
      unmount();
    }
  });
});

describe("TxnTable sorts a split parent by its display category", () => {
  // the parent's own category is display-only ("Beta cat"); its parts file under Groceries / Shopping. "Beta" sits BETWEEN the other
  // rows' categories, and the parent is the oldest row: neither the date order nor "split · 2 parts" (which sorts after every
  // capitalised name, or among the s's) can put it there by accident
  const m = txFactory({ type: "purchase", vendor: "Synth Mart" });
  const parent = m({ id: "p", amount: -300, is_parent: true, category: "Beta cat", subcategory: null });
  const pa = m({ id: "pa", amount: -240, parent_id: "p", category: "Shopping", category_source: "split" });
  const pb = m({ id: "pb", amount: -60, parent_id: "p", category: "Groceries", category_source: "split" });
  const alpha = m({ id: "o1", vendor: "Alpha Shop", category: "Alpha cat", date: "2026-01-04" });
  const tango = m({ id: "o2", vendor: "Tango Shop", category: "Tango cat", date: "2026-01-03" });
  beforeEach(() => { world.splitParents = new Map([["p", parent]]); world.childrenByParent = new Map([["p", [pa, pb]]]); });
  afterEach(() => { world.splitParents = new Map(); world.childrenByParent = new Map(); });
  const order = () => bodyRows().map((r) => /Alpha Shop|Tango Shop|Synth Mart/.exec(r.textContent ?? "")![0]);

  it("by category the parent files under its own category, not in a block of 'split · N parts' labels", () => {
    render(<TxnTable txns={[pa, pb, alpha, tango]} sort={[{ id: "category", desc: false }]} onSortChange={() => {}} />);
    expect(order(), "Alpha cat, Beta cat (the parent), Tango cat").toEqual(["Alpha Shop", "Synth Mart", "Tango Shop"]);
    cleanup();
    render(<TxnTable txns={[pa, pb, alpha, tango]} sort={[{ id: "category", desc: true }]} onSortChange={() => {}} />);
    expect(order()).toEqual(["Tango Shop", "Synth Mart", "Alpha Shop"]);
  });

  it("the parent's Category cell still says what the row IS (the sort key is not what it draws)", () => {
    render(<TxnTable txns={[pa, pb, alpha]} />);
    const prow = bodyRows().find((r) => /Synth Mart/.test(r.textContent ?? ""))!;
    expect(prow.textContent).toContain("split · 2 parts");
    expect(prow.textContent).not.toContain("Beta cat");
  });
});

// A row the bank dated after the ingest day is a real row - the register lists it - but no day of data: it carries a
// "dated ahead" text chip beside its date. A sort by date is by the date shown, the default one included: the header
// says "Date ↓", so the newest date leads, marked.
describe("TxnTable: a row dated after the ingest day", () => {
  // the ingest ran on Jan 3 (Bravo's day); the bank dated Delta's charge Jan 5
  const rows = () => [...three(), tx({ date: "2026-01-05", vendor: "Delta", amount: -50 })];
  const names = () => bodyRows().map((r) => /Alpha|Bravo|Charlie|Delta/.exec(r.textContent ?? "")![0]);
  const delta = () => bodyRows().find((r) => (r.textContent ?? "").includes("Delta"))!;
  const why = "The bank dated this row after the last ingest (Jan 3, 2026): it is listed here, and a month's figures count it "
    + "once that month begins";
  beforeEach(() => { world.generatedAt = "2026-01-03T21:00:00-05:00"; });
  afterEach(() => { world.generatedAt = "2026-12-31T20:00:00-05:00"; });

  it("carries a 'dated ahead' text chip beside its date whose title says why; no other row does, and it is no tab stop", () => {
    render(<TxnTable txns={rows()} />);
    const marks = screen.getAllByText("dated ahead");
    expect(marks).toHaveLength(1);
    expect(delta().contains(marks[0])).toBe(true);
    const dateCell = within(delta()).getAllByRole("cell")[0];
    expect(dateCell.textContent?.startsWith("2026-01-05dated ahead"), "the chip sits in the Date cell, after the date").toBe(true);
    expect(marks[0].getAttribute("title")).toBe(why);
    expect(marks[0].textContent, "a screen reader hears the reason too").toBe(`dated ahead: ${why}`);
    expect(marks[0].hasAttribute("tabindex"), "no nested tab stop inside the row").toBe(false);
  });

  it("under the default sort (Date, newest first) it leads: the order is the date shown", () => {
    render(<TxnTable txns={rows()} />);
    expect(names()).toEqual(["Delta", "Bravo", "Alpha", "Charlie"]);
    expect(screen.getByRole("columnheader", { name: /Date/ }).getAttribute("aria-sort")).toBe("descending");
  });

  // the chip marks every row after the ingest day, inside the month in progress or in a month not begun alike; the filter
  // bar's note counts only the second kind (components/FilterBar.tsx), so with one of each the register shows two chips
  it("a row dated ahead inside the month in progress and one dated into a month not begun both carry the chip", () => {
    render(<TxnTable txns={[...rows(), tx({ date: "2026-02-02", vendor: "Echo", amount: -25 })]} />);
    const marked = bodyRows().filter((r) => within(r).queryByText("dated ahead") !== null)
      .map((r) => /Delta|Echo/.exec(r.textContent ?? "")?.[0]);
    expect(marked.sort()).toEqual(["Delta", "Echo"]);
  });

  it("a sort by date chosen here orders by the date itself, both ways", () => {
    render(<TxnTable txns={rows()} />);
    const date = () => screen.getByRole("columnheader", { name: /Date/ });
    fireEvent.click(within(date()).getByRole("button"));
    expect(names(), "oldest first").toEqual(["Charlie", "Alpha", "Bravo", "Delta"]);
    fireEvent.click(within(date()).getByRole("button"));
    expect(names(), "newest first, chosen: the row dated ahead leads").toEqual(["Delta", "Bravo", "Alpha", "Charlie"]);
  });

  it("a controlled register (Transactions' ?sort=) orders by the date shown as an account page does", () => {
    const sorts: unknown[] = [];
    render(<TxnTable txns={rows()} sort={[{ id: "date", desc: true }]} onSortChange={(s) => sorts.push(s)} />);
    expect(names()).toEqual(["Delta", "Bravo", "Alpha", "Charlie"]);
    cleanup();
    render(<TxnTable txns={rows()} hideAccount />);
    expect(names(), "the account page's register, the same order").toEqual(["Delta", "Bravo", "Alpha", "Charlie"]);
  });

  it("stacked (a phone): the chip rides the second line beside the date, and the open row says why in words", () => {
    setWidth(390);
    render(<TxnTable txns={rows()} />);
    expect(within(delta()).getByText("dated ahead")).toBeDefined();
    fireEvent.click(delta());
    expect(screen.getByText(`dated ahead: ${why}`, { exact: false })).toBeDefined();
  });

  it("with an unreadable ingest stamp no row is marked, and the order is the date's", () => {
    world.generatedAt = "";
    render(<TxnTable txns={rows()} />);
    expect(screen.queryByText("dated ahead")).toBeNull();
    expect(names()).toEqual(["Delta", "Bravo", "Alpha", "Charlie"]);
  });
});

describe("TxnTable shows whole rows at the foot of a px-height body", () => {
  it("cuts a numeric maxHeight to a whole number of rows (no sliver of the next one)", () => {
    setWidth(1000);                                             // a tablet's stacked list (a phone's has no body height)
    render(<TxnTable txns={two()} maxHeight={480} />);
    const scroller = bodyRows()[0].closest("[role=rowgroup]") as HTMLElement;
    expect(scroller.style.maxHeight, "stacked rows are 47px: 10 of them").toBe("470px");
    cleanup();
    setWidth(1280);
    render(<TxnTable txns={two()} maxHeight={480} />);
    expect((bodyRows()[0].closest("[role=rowgroup]") as HTMLElement).style.maxHeight, "grid rows are 29px: 16 of them").toBe("464px");
  });
  it("leaves a CSS length (the Transactions tab's viewport-relative one) exactly as given", () => {
    render(<TxnTable txns={two()} maxHeight="max(20rem, calc(100vh - 20rem))" />);
    expect((bodyRows()[0].closest("[role=rowgroup]") as HTMLElement).style.maxHeight).toBe("max(20rem, calc(100vh - 20rem))");
  });
});

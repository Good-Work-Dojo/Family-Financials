// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// A fake ECharts instance whose own size is whatever it was last laid out at (init or resize),
// and a box whose size the test sets - the two drift apart the way a real first frame can.
const box = vi.hoisted(() => ({ w: 0, h: 0 }));
const fake = vi.hoisted(() => ({
  inits: 0, setOptions: [] as unknown[], resizes: 0, disposed: 0, w: 0, h: 0,
}));
vi.mock("../lib/echarts", async (importOriginal) => ({
  // the real still option: what reduced motion hands the chart is the library's own (lib/echarts.test.ts pins it)
  stillOption: (await importOriginal<typeof import("../lib/echarts")>()).stillOption,
  default: {
    init: () => {
      fake.inits++;
      fake.w = box.w; fake.h = box.h;
      return {
        on: () => {},
        setOption: (o: unknown) => { fake.setOptions.push(o); },
        resize: () => { fake.resizes++; fake.w = box.w; fake.h = box.h; },
        getWidth: () => fake.w,
        getHeight: () => fake.h,
        dispose: () => { fake.disposed++; },
      };
    },
  },
  // a phone's pass marks the option, so a test can see it was applied
  tuneForMobile: (o: object) => ({ ...o, phone: true }),
  // the legend's rows at a width are the test's to say (lib/echarts.test.ts holds the real count); the plot moves down
  // by the rows past the first as the real helper moves it, each a step the test also says (18 on a phone)
  legendRowStep: (o: { phone?: boolean }) => (o?.phone ? 18 : 24),
  legendRows: (_o: unknown, w: number) => (w > 0 ? legend.rowsAt(w) : null),
  withLegendRows: (o: { grid?: { top?: number } }, extra: number, step: number) =>
    (extra > 0 ? { ...o, grid: { ...o.grid, top: (o.grid?.top ?? 32) + extra * step } } : o),
}));
const legend = vi.hoisted(() => ({ rowsAt: (_w: number): number | null => null }));
// the csv helpers are spied so a test can see exactly what the CSV button hands them; one test calls through to
// the real toCsv to pin the RFC 4180 escaping of what a chart's own labels can carry
const csvMock = vi.hoisted(() => ({ toCsv: vi.fn((..._a: unknown[]) => "csv"), downloadCsv: vi.fn() }));
vi.mock("../lib/csv", () => csvMock);
let observed: (() => void) | null = null;
const media = vi.hoisted(() => ({ phone: false, reduced: false }));
vi.mock("../lib/useMediaQuery", () => ({ useIsMobile: () => media.phone, useReducedMotion: () => media.reduced }));

import { MASK, setPrivate } from "../lib/privacy";
import EChart from "./EChart";

beforeEach(() => {
  Object.assign(fake, { inits: 0, setOptions: [], resizes: 0, disposed: 0, w: 0, h: 0 });
  Object.assign(box, { w: 0, h: 0 });
  csvMock.toCsv.mockClear(); csvMock.downloadCsv.mockClear();
  observed = null;
  media.phone = false;
  media.reduced = false;
  legend.rowsAt = () => null;
  (globalThis as any).ResizeObserver = class {
    constructor(cb: () => void) { observed = cb; }
    observe() {} unobserve() {} disconnect() {}
  };
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(() => box.w);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(() => box.h);
});
afterEach(() => { cleanup(); setPrivate(false); vi.restoreAllMocks(); });

const notify = () => act(() => { observed?.(); });

describe("EChart sizing", () => {
  it("waits for a box with a real size before creating the chart, then applies the option once", () => {
    const option = { series: [] };
    render(<EChart option={option} />);
    expect(fake.inits, "no chart while the box is 0 wide (hidden / not laid out)").toBe(0);
    box.w = 998; box.h = 320;
    notify();
    expect(fake.inits).toBe(1);
    expect(fake.setOptions).toEqual([option]);
  });

  it("creates the chart on mount when the box already has its size, without applying the option twice", () => {
    box.w = 998; box.h = 320;
    render(<EChart option={{ a: 1 }} />);
    expect(fake.inits).toBe(1);
    expect(fake.setOptions.length).toBe(1);
  });

  it("a chart laid out at a stale (narrow) width is re-fitted to its box", () => {
    box.w = 30; box.h = 300;                  // the first frame measured a sliver
    render(<EChart option={{}} />);
    expect(fake.w).toBe(30);
    box.w = 998;
    notify();
    expect(fake.resizes).toBe(1);
    expect(fake.w).toBe(998);
    notify();                                  // already fitted: no redundant resize
    expect(fake.resizes).toBe(1);
  });

  it("re-fits on a window resize even when no observer notification arrives", () => {
    box.w = 30; box.h = 300;
    render(<EChart option={{}} />);
    box.w = 998;
    act(() => { window.dispatchEvent(new Event("resize")); });
    expect(fake.w).toBe(998);
  });

  it("a phone -> desktop flip re-fits at that commit, with no observer or resize event at all", () => {
    media.phone = true;
    box.w = 30; box.h = 240;                   // first laid out narrow, in the phone layout
    const { rerender } = render(<EChart option={{}} height={300} />);
    expect(fake.w).toBe(30);
    media.phone = false;
    box.w = 998; box.h = 300;
    rerender(<EChart option={{}} height={300} />);
    expect(fake.w).toBe(998);
    expect(fake.h).toBe(300);
  });

  it("a box hidden to 0 keeps the last good layout instead of collapsing", () => {
    box.w = 998; box.h = 320;
    render(<EChart option={{}} />);
    box.w = 0; box.h = 0;
    notify();
    expect(fake.resizes).toBe(0);
    expect(fake.w).toBe(998);
  });

  it("a legend that wraps onto more rows at the box's width moves the plot down and makes the box that much taller", () => {
    // three rows at 400px, one at 1000px
    legend.rowsAt = (w) => (w < 600 ? 3 : 1);
    box.w = 400; box.h = 300;
    const option = { grid: { top: 32 }, legend: {} };
    const { container } = render(<EChart option={option} height={300} />);
    const div = container.firstElementChild as HTMLElement;
    expect(div.style.height).toBe(`${300 + 2 * 24}px`);
    expect((fake.setOptions.at(-1) as { grid: { top: number } }).grid.top).toBe(32 + 2 * 24);
    expect(option.grid.top, "the caller's option is never mutated").toBe(32);
    box.w = 1000;
    notify();
    expect(div.style.height).toBe("300px");
    expect(fake.setOptions.at(-1)).toBe(option);
  });

  it("a phone's legend wraps too: the capped box grows by its rows past the first, at the phone legend's step", () => {
    media.phone = true;
    legend.rowsAt = () => 3;
    box.w = 360; box.h = 240;
    const { container } = render(<EChart option={{ grid: { top: 32 }, legend: {} }} height={300} />);
    expect((container.firstElementChild as HTMLElement).style.height).toBe(`${240 + 2 * 18}px`);
    expect((fake.setOptions.at(-1) as { grid: { top: number }; phone: boolean })).toMatchObject({ phone: true, grid: { top: 32 + 2 * 18 } });
  });

  it("a phone caps a chart at 240px unless its author keeps another height for it (phoneHeight)", () => {
    media.phone = true;
    box.w = 360; box.h = 240;
    const { container } = render(<><EChart option={{}} height={400} /><EChart option={{}} height={400} phoneHeight={520} /></>);
    const [capped, kept] = [...container.children].filter((el) => (el as HTMLElement).style.height) as HTMLElement[];
    expect([capped.style.height, kept.style.height]).toEqual(["240px", "520px"]);
    media.phone = false;
    cleanup();
    const desk = render(<EChart option={{}} height={400} phoneHeight={520} />);
    expect((desk.container.firstElementChild as HTMLElement).style.height, "a desktop keeps `height`").toBe("400px");
  });

  it("a new option is applied once; the chart is disposed on unmount", () => {
    box.w = 998; box.h = 320;
    const { rerender, unmount } = render(<EChart option={{ v: 1 }} />);
    rerender(<EChart option={{ v: 2 }} />);
    expect(fake.setOptions).toEqual([{ v: 1 }, { v: 2 }]);
    unmount();
    expect(fake.disposed).toBe(1);
  });
});

describe("EChart and prefers-reduced-motion", () => {
  it("hands the option over as is when motion is fine (the test below can fail)", () => {
    box.w = 998; box.h = 320;
    const option = { series: [] };
    render(<EChart option={option} />);
    expect(fake.setOptions).toEqual([option]);
  });
  it("turns the chart's animation off under prefers-reduced-motion: reduce, without mutating the caller's option", () => {
    media.reduced = true;
    box.w = 998; box.h = 320;
    const option = { series: [], animation: true };
    render(<EChart option={option} />);
    expect(fake.setOptions).toEqual([{ series: [], animation: false }]);
    expect(option.animation, "the memoized option is untouched").toBe(true);
  });
  it("applies it on a phone too (after the phone pass)", () => {
    media.reduced = true; media.phone = true;
    box.w = 390; box.h = 240;
    render(<EChart option={{ series: [] }} />);
    expect(fake.setOptions).toEqual([{ series: [], phone: true, animation: false }]);
  });
});

describe("EChart view data", () => {
  const table = { columns: ["Month", "Spend"], rows: [["Jan 26", 1234.5], ["Feb 26", null]], csvName: "spend.csv" };
  it("renders nothing extra without tableData, and a toggle that reveals a formatted table with it", () => {
    box.w = 998; box.h = 320;
    const { unmount } = render(<EChart option={{}} />);
    expect(screen.queryByRole("button", { name: "view data" })).toBeNull();
    unmount();
    render(<EChart option={{}} tableData={table} />);
    const toggle = screen.getByRole("button", { name: "view data" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Month", "Spend"]);
    expect(screen.getByText("$1,235")).toBeDefined();          // fmtUSD0 by default (privacy mode masks through it)
    expect(screen.getByText("—")).toBeDefined();               // a gap is a dash, never 0
  });
  it("the CSV button hands the RAW rows to the csv helper and says it is never masked", () => {
    box.w = 998; box.h = 320;
    render(<EChart option={{}} tableData={table} />);
    fireEvent.click(screen.getByRole("button", { name: "view data" }));
    const csv = screen.getByRole("button", { name: /CSV/ });
    expect(csv.getAttribute("title")).toMatch(/never masked/);
    fireEvent.click(csv);
    expect(csvMock.toCsv).toHaveBeenCalledWith(table.rows, table.columns);
    expect(csvMock.downloadCsv).toHaveBeenCalledWith("spend.csv", expect.any(String));
  });

  it("the toggle is a real button wired to its panel: aria-expanded flips, aria-controls names the panel, the visible text stays put", () => {
    box.w = 998; box.h = 320;
    render(<EChart option={{}} tableData={table} />);
    const toggle = screen.getByRole("button", { name: "view data" });
    expect(toggle.tagName).toBe("BUTTON");
    expect(toggle.getAttribute("type")).toBe("button");
    const panelId = toggle.getAttribute("aria-controls");
    expect(panelId, "aria-controls names a panel").toBeTruthy();
    expect(document.getElementById(panelId!), "the controlled panel exists even while closed").not.toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.textContent, "open or closed is aria-expanded's to say, the words do not change").toBe("view data");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(document.getElementById(panelId!)!.contains(screen.getByRole("table")), "the table lives inside the controlled panel").toBe(true);
    fireEvent.click(toggle);
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("button", { name: /CSV/ }), "the CSV button only exists while open").toBeNull();
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("a label tells charts apart: the toggle, the CSV button and the caption name the chart; the visible text does not change", () => {
    box.w = 998; box.h = 320;
    render(<EChart option={{}} tableData={{ ...table, label: "Monthly spend" }} />);
    const toggle = screen.getByRole("button", { name: "View data: Monthly spend" });
    expect(toggle.textContent).toBe("view data");
    expect(screen.queryByRole("button", { name: "view data" }), "the label replaces the bare name").toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Download CSV: Monthly spend" }).textContent).toMatch(/CSV/);
    expect(screen.getByRole("table").querySelector("caption")?.textContent).toMatch(/Monthly spend.*Month/);
  });

  it("the open table is a real table: a caption, column headers, a row header per row, inside a scroll wrapper", () => {
    box.w = 998; box.h = 320;
    const { container } = render(<EChart option={{}} tableData={table} />);
    fireEvent.click(screen.getByRole("button", { name: "view data" }));
    const t = screen.getByRole("table");
    expect(t.querySelector("caption")?.textContent, "a caption says what the table is").toMatch(/Month/);
    expect(within(t).getAllByRole("rowheader").map((h) => h.textContent)).toEqual(["Jan 26", "Feb 26"]);
    // the label column sticks to the left edge (an opaque fill, so panned numbers do not show through it) and
    // never wraps; the corner header sticks with it
    for (const h of [...within(t).getAllByRole("rowheader"), screen.getAllByRole("columnheader")[0]]) {
      expect(h.className).toMatch(/\bsticky\b/);
      expect(h.className).toMatch(/\bleft-0\b/);
      expect(h.className).toMatch(/whitespace-nowrap/);
      expect(h.className).toMatch(/color-mix/);
    }
    expect(screen.getAllByRole("columnheader")[1].className, "the other headers pan").not.toMatch(/sticky/);
    expect(within(t).getAllByRole("cell").map((c) => c.textContent)).toEqual(["$1,235", "—"]);
    const scroller = t.closest(".overflow-x-auto");
    expect(scroller, "wide tables pan inside TableScroll, never overflow the page").not.toBeNull();
    // and the scroller adds nothing to a grid card's intrinsic width (jsdom has no layout; the 390px browser run proves it)
    expect(scroller!.className).toMatch(/\bw-0\b/);
    expect(scroller!.className).toMatch(/\bmin-w-full\b/);
    // the table is the footer, below the chart's own box, so the chart keeps its height
    const chartBox = container.firstElementChild as HTMLElement;
    expect(chartBox.contains(t)).toBe(false);
    expect(chartBox.style.height).toBe("320px");
  });

  it("a long table scrolls inside a capped box under a sticky header; a short one is just a table", () => {
    box.w = 998; box.h = 320;
    const rows = (n: number) => Array.from({ length: n }, (_, i) => [`d${i}`, i]);
    const { unmount } = render(<EChart option={{}} tableData={{ columns: ["Date", "V"], rows: rows(11) }} />);
    fireEvent.click(screen.getByRole("button", { name: "view data" }));
    expect(screen.getByRole("table").closest(".overflow-x-auto")!.className).toMatch(/max-h-72/);
    expect(screen.getAllByRole("columnheader")[0].closest("thead")!.className).toMatch(/sticky/);
    unmount();
    render(<EChart option={{}} tableData={{ columns: ["Date", "V"], rows: rows(3) }} />);
    fireEvent.click(screen.getByRole("button", { name: "view data" }));
    expect(screen.getByRole("table").closest(".overflow-x-auto")!.className).not.toMatch(/max-h/);
    expect(screen.getAllByRole("columnheader")[0].closest("thead")!.className).not.toMatch(/sticky/);
  });

  it("a note about what the chart draws and the table omits shows under the table, only while open", () => {
    box.w = 998; box.h = 320;
    render(<EChart option={{}} tableData={{ ...table, note: "Snapshots only." }} />);
    expect(screen.queryByText("Snapshots only.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "view data" }));
    expect(screen.getByText("Snapshots only.")).toBeDefined();
  });

  it("the toggle and the CSV button are 44px targets below md (and shrink back at md)", () => {
    box.w = 998; box.h = 320;
    render(<EChart option={{}} tableData={table} />);
    const toggle = screen.getByRole("button", { name: "view data" });
    expect(toggle.className.split(/\s+/)).toEqual(expect.arrayContaining(["min-h-11", "min-w-11", "md:min-h-0", "md:min-w-0"]));
    fireEvent.click(toggle);
    const csv = screen.getByRole("button", { name: /CSV/ });
    expect(csv.className.split(/\s+/)).toEqual(expect.arrayContaining(["min-h-11", "min-w-11", "md:min-h-0", "md:min-w-0"]));
  });

  it("numbers go through the table's own formatter when it has one; strings pass as they are", () => {
    box.w = 998; box.h = 320;
    render(<EChart option={{}} tableData={{ columns: ["Age", "Balance"], rows: [["65", 0.5]], format: (v) => `${v * 100}pct` }} />);
    fireEvent.click(screen.getByRole("button", { name: "view data" }));
    expect(screen.getByText("50pct")).toBeDefined();
    expect(screen.getByText("65")).toBeDefined();
  });

  it("the formatter is told the column, so a ratio column can read as a percentage next to money; the CSV keeps the raw ratio", async () => {
    box.w = 998; box.h = 320;
    const real = await vi.importActual<typeof import("../lib/csv")>("../lib/csv");
    csvMock.toCsv.mockImplementationOnce(real.toCsv as never);
    const t = { columns: ["Month", "Income", "Savings rate"], rows: [["2026-01", 5000, 0.123], ["2026-02", 0, null]],
      format: (v: number, col: number) => (col === 2 ? `${(v * 100).toFixed(1)}%` : `$${v}`) };
    render(<EChart option={{}} tableData={t} />);
    fireEvent.click(screen.getByRole("button", { name: "view data" }));
    expect(within(screen.getByRole("table")).getAllByRole("cell").map((c) => c.textContent)).toEqual(["$5000", "12.3%", "$0", "—"]);
    fireEvent.click(screen.getByRole("button", { name: /CSV/ }));
    expect((csvMock.downloadCsv.mock.calls[0] as unknown as [string, string])[1]).toBe("Month,Income,Savings rate\r\n2026-01,5000,0.123\r\n2026-02,0,\r\n");
  });

  it("a real 0 renders as 0, never as the dash a gap gets", () => {
    box.w = 998; box.h = 320;
    render(<EChart option={{}} tableData={{ columns: ["Month", "Spend"], rows: [["Jan", 0], ["Feb", null]] }} />);
    fireEvent.click(screen.getByRole("button", { name: "view data" }));
    expect(within(screen.getByRole("table")).getAllByRole("cell").map((c) => c.textContent)).toEqual(["$0", "—"]);
  });

  it("the CSV filename is generic by default, and a quote or a comma in a label survives into the file", async () => {
    box.w = 998; box.h = 320;
    const real = await vi.importActual<typeof import("../lib/csv")>("../lib/csv");
    csvMock.toCsv.mockImplementationOnce(real.toCsv as never);
    const tricky = { columns: ["Vendor, inc", "Spend"], rows: [['Joe "Bob" & Sons, LLC', 1234.5], ["Plain", null]] };
    render(<EChart option={{}} tableData={tricky} />);
    fireEvent.click(screen.getByRole("button", { name: "view data" }));
    fireEvent.click(screen.getByRole("button", { name: /CSV/ }));
    const [name, body] = csvMock.downloadCsv.mock.calls[0] as unknown as [string, string];
    expect(name).toBe("chart.csv");
    // raw numbers (no $ / thousands separator), a null as an empty field, RFC 4180 quoting
    expect(body).toBe('"Vendor, inc",Spend\r\n"Joe ""Bob"" & Sons, LLC",1234.5\r\nPlain,\r\n');
  });

  it("the footer does not rebuild the chart: toggling the table applies no new option and never re-inits", () => {
    box.w = 998; box.h = 320;
    const option = { series: [] };
    render(<EChart option={option} tableData={table} />);
    fireEvent.click(screen.getByRole("button", { name: "view data" }));
    fireEvent.click(screen.getByRole("button", { name: "view data" }));
    expect(fake.inits).toBe(1);
    expect(fake.setOptions).toEqual([option]);
  });
});

// Privacy mode blurs the canvas on top of the masked axes and tooltips (the numbers never reach the canvas as text,
// but a stale label on an old frame would): the chart's own box takes the blur, the footer's controls stay sharp.
describe("EChart under privacy mode", () => {
  const table = { columns: ["Month", "Spend"], rows: [["Jan 26", 1234.5]], csvName: "spend.csv" };
  it("blurs its canvas (and only the canvas) while privacy mode is on, and only then", () => {
    box.w = 998; box.h = 320;
    const { container } = render(<EChart option={{}} className="rounded" />);
    const box_ = () => container.firstElementChild as HTMLElement;
    expect(box_().className).toBe("rounded");
    act(() => setPrivate(true));
    expect(box_().className.split(" ")).toContain("[&_canvas]:blur-[6px]");
    expect(box_().className, "the box itself is not blurred: ECharts appends its tooltip DOM inside it").not.toMatch(/(^|\s)blur-/);
    expect(box_().className).toMatch(/\brounded\b/);
    act(() => setPrivate(false));
    expect(box_().className).toBe("rounded");
  });
  it("a chart with no class of its own has none when privacy is off (no stray empty attribute)", () => {
    box.w = 998; box.h = 320;
    const { container } = render(<EChart option={{}} />);
    expect((container.firstElementChild as HTMLElement).hasAttribute("class")).toBe(false);
  });
  it("the view-data table shows the mask instead of an amount, and keeps a dash for a gap", () => {
    box.w = 998; box.h = 320;
    render(<EChart option={{}} tableData={{ ...table, rows: [["Jan 26", 1234.5], ["Feb 26", null]] }} />);
    fireEvent.click(screen.getByRole("button", { name: "view data" }));
    act(() => setPrivate(true));
    expect(screen.queryByText("$1,235")).toBeNull();
    expect(screen.getByText(MASK)).toBeDefined();
    expect(screen.getByText("—")).toBeDefined();
  });
  it("says in visible text, beside the CSV button, that the file is never masked and holds the real amounts", () => {
    box.w = 998; box.h = 320;
    render(<EChart option={{}} tableData={table} />);
    fireEvent.click(screen.getByRole("button", { name: "view data" }));
    expect(screen.getByText(/never masked/).textContent).toMatch(/real amounts/);
    act(() => setPrivate(true));
    fireEvent.click(screen.getByRole("button", { name: /CSV/ }));
    expect(csvMock.toCsv).toHaveBeenCalledWith(table.rows, table.columns);        // raw rows, not the mask
  });
});

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Transaction } from "../data/types";
import { txFactory } from "../lib/__fixtures__";
import { fmtDate } from "../lib/format";

const tx = txFactory();
// the drill reads the vendor's rows from the index and the month in progress from the data (its rows, its cash flow
// and the ingest instant); the chart is stubbed and records the option it was handed
const world = vi.hoisted(() => ({
  rows: new Map<string, unknown[]>(), generatedAt: "2026-02-20T20:00:00-05:00", option: null as any,
}));
vi.mock("../components/EChart", () => ({ default: (p: { option: unknown }) => { world.option = p.option; return <div data-testid="chart" />; } }));
vi.mock("../data/DataContext", () => ({
  useData: () => ({
    data: { transactions: [...world.rows.values()].flat(), cashflow: [], manifest: { generated_at: world.generatedAt } },
    indexes: { byVendor: world.rows },
  }),
}));
import VendorDrill from "./VendorDrill";

const acme = () => [
  tx({ date: "2026-01-05", vendor: "Acme Hardware", amount: -20 }),
  tx({ date: "2026-02-05", vendor: "Acme Hardware", amount: -30 }),
];
afterEach(() => {
  cleanup();
  world.rows = new Map([["Acme Hardware", acme()]]);
  world.generatedAt = "2026-02-20T20:00:00-05:00";
  world.option = null;
});
world.rows = new Map([["Acme Hardware", acme()]]);

describe("VendorDrill is a modal dialog", () => {
  it("is named by the vendor heading, modal, and the close button has a name", () => {
    render(<VendorDrill vendor="Acme Hardware" onClose={() => {}} onFilter={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Acme Hardware" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(screen.getByRole("button", { name: "Close" })).toBeDefined();
  });

  it("moves focus into the panel, wraps Tab, closes on Escape and on the scrim, restores focus", () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const onClose = vi.fn();
    const { unmount } = render(<VendorDrill vendor="Acme Hardware" onClose={onClose} onFilter={() => {}} />);
    const dialog = screen.getByRole("dialog");
    expect(dialog.contains(document.activeElement)).toBe(true);
    screen.getByRole("button", { name: "Close" }).focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(screen.getByRole("button", { name: "Close" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("card-vendor-drill"));
    expect(onClose).toHaveBeenCalledTimes(2);
    unmount();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it("a vendor with no spending still renders a named dialog", () => {
    render(<VendorDrill vendor="Nobody" onClose={() => {}} onFilter={() => {}} />);
    expect(screen.getByRole("dialog", { name: "Nobody" })).toBeDefined();
    expect(screen.getByText("No spending found.")).toBeDefined();
  });
});

// A charge the bank dated into a month that has not begun (an autopay) is a real row - the register lists it - but the
// drill's bars, totals, cadence, trend and "last seen" count it only once its month begins, and the panel says so.
describe("VendorDrill counts up to the month in progress", () => {
  // the ingest ran on Feb 20, 2026: February is in progress, and the autopay is dated Mar 2
  const autopay = (): Transaction => tx({ date: "2026-03-02", vendor: "Acme Hardware", amount: -500 });
  const shown = () => {
    const stat = (label: string) => screen.getByText(label).parentElement!.textContent!.slice(label.length);
    return { months: world.option.xAxis.data, bars: world.option.series[0].data,
      total: stat("Total spent"), count: stat("Transactions"), avg: stat("Average ticket"), cadence: stat("Avg days between"),
      last: stat("Last seen"), trend: stat("Monthly trend"),
      recent: screen.getAllByRole("listitem").map((li) => li.textContent) };
  };
  const drill = (rows: Transaction[]) => {
    world.rows = new Map([["Acme Hardware", rows]]);
    render(<VendorDrill vendor="Acme Hardware" onClose={() => {}} onFilter={() => {}} />);
    const out = shown();
    cleanup();
    return out;
  };

  it("a charge dated into the next month is in no bar, figure or recent row: the same as the vendor without it", () => {
    const withRow = drill([...acme(), autopay()]);
    expect(withRow).toEqual(drill(acme()));
    expect(withRow.months, "no bar after the month in progress").toEqual(["2026-01", "2026-02"]);
    expect(withRow.last).toBe(fmtDate("2026-02-05"));
  });

  it("the panel says how many charges it leaves out, and only when there are some", () => {
    world.rows = new Map([["Acme Hardware", [...acme(), autopay()]]]);
    render(<VendorDrill vendor="Acme Hardware" onClose={() => {}} onFilter={() => {}} />);
    expect(screen.getByTestId("vendor-drill-ahead").textContent).toMatch(/^1 charge dated ahead, into a month that has not begun/);
    cleanup();
    world.rows = new Map([["Acme Hardware", acme()]]);
    render(<VendorDrill vendor="Acme Hardware" onClose={() => {}} onFilter={() => {}} />);
    expect(screen.queryByTestId("vendor-drill-ahead")).toBeNull();
  });

  it("a vendor whose one charge is dated ahead has no spending counted yet, and the panel says why", () => {
    world.rows = new Map([["Acme Hardware", [autopay()]]]);
    render(<VendorDrill vendor="Acme Hardware" onClose={() => {}} onFilter={() => {}} />);
    expect(screen.getByText("No spending counted yet.")).toBeDefined();
    expect(screen.getByTestId("vendor-drill-ahead").textContent).toMatch(/^1 charge dated ahead/);
    expect(screen.queryByTestId("chart")).toBeNull();
  });

  it("once its month begins the charge counts (control: the drill reads the month in progress, not a fixed cut)", () => {
    world.generatedAt = "2026-03-03T09:00:00-05:00";
    const later = drill([...acme(), autopay()]);
    expect(later.months).toEqual(["2026-01", "2026-02", "2026-03"]);
    expect(later.last).toBe(fmtDate("2026-03-02"));
  });
});

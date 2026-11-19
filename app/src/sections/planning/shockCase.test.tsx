// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NO_TRANSACTIONS_YET } from "../../components/NoData";
import { cfmFactory, snapFactory, txFactory } from "../../lib/__fixtures__";
import { SHIPPED_RESERVED } from "../../lib/categories";
import { bearCaseTotal, SHOCK_DEFAULTS, shockedTotal } from "../../lib/forwardLook";
import { fmtDelta, fmtUSD0 } from "../../lib/format";

// The Planning header's "Bear-case net worth" and the stress-test card it scrolls to are one case: the card's
// sliders open on the numbers the KPI is computed from, so the KPI is what the card says before anything is moved.
const snap = snapFactory()({
  total: 990_000,                                     // the classes' sum, as every snapshot's total is
  by_class: { "Stocks/ETF": 500_000, Retirement: 200_000, "529": 20_000, "Real Estate/Venture": 10_000, Crypto: 30_000,
    "Private Stock": 40_000, House: 300_000, Cash: 50_000, Mortgage: -150_000, Margin: -10_000 },
});
// a test gives the household its rows: one full month (May) and the month in progress (June, the ingest's)
const rows = vi.hoisted(() => ({ transactions: [] as unknown[], cashflow: [] as unknown[], snapshot: null as unknown }));
vi.mock("../../data/DataContext", () => ({
  useData: () => ({
    data: { transactions: rows.transactions, cashflow: rows.cashflow, assets: { snapshots: [rows.snapshot ?? snap] }, recurring: [],
            manifest: { current_snapshot_date: null, generated_at: "2026-06-20T20:00:00-05:00" },
            assumptions: { category_renames: [], reserved: SHIPPED_RESERVED } },
    indexes: { dataRange: { first: "2024-06-01", last: "2026-06-20" }, dataThrough: "2026-06-20" },
    missing: new Set<string>(),
  }),
}));
import { MarketShockCard } from "../Tools";
import ForwardLookStrip from "./ForwardLookStrip";

afterEach(() => { cleanup(); rows.transactions = []; rows.cashflow = []; rows.snapshot = null; });
const afterShock = () => within(screen.getByTestId("card-market-shock")).getByText("Net worth after shock").parentElement!.textContent ?? "";
const yearsTile = () => within(screen.getByTestId("card-market-shock")).getByText("≈ Years of saving").parentElement!.textContent ?? "";
const PACE = { pace: 2_000, seed: 2_000, months: 12 };

describe("the bear case and the stress-test card are one case", () => {
  it("the KPI is the card's net worth before a slider is moved", () => {
    render(<><ForwardLookStrip /><MarketShockCard avgMonthlySavings={PACE} /></>);
    const kpi = screen.getByTestId("kpi-bear-case").textContent ?? "";
    expect(kpi).toContain(fmtUSD0(bearCaseTotal(snap)));
    expect(afterShock()).toContain(fmtUSD0(bearCaseTotal(snap)));
  });
  it("with no transaction the years read — and say so, naming no pace", () => {
    render(<MarketShockCard avgMonthlySavings={null} />);
    expect(yearsTile()).toBe(`≈ Years of saving—${NO_TRANSACTIONS_YET}`);
  });
  it("with one full month the years read — with what they need and how many full months there are, and no line under the row", () => {
    rows.transactions = [txFactory()({ date: "2026-05-01" }), txFactory()({ date: "2026-06-02" })];
    rows.cashflow = [cfmFactory()({ month: "2026-05" }), cfmFactory()({ month: "2026-06" })];
    render(<MarketShockCard avgMonthlySavings={null} />);
    expect(yearsTile()).toBe("≈ Years of saving—Not enough history yet: needs 3 full months of transactions, 1 so far");
    expect(screen.queryByTestId("not-enough-history")).toBeNull();
  });
  it("a pace from twelve full months is the household's actual; from fewer it says from how many", () => {
    render(<MarketShockCard avgMonthlySavings={PACE} />);
    expect(yearsTile()).toContain(`at your actual ${fmtUSD0(2_000)}/mo savings pace`);
    cleanup();
    render(<MarketShockCard avgMonthlySavings={{ pace: 2_000, seed: 2_000, months: 5 }} />);
    expect(yearsTile()).toContain(`at your ${fmtUSD0(2_000)}/mo savings pace, from 5 full months`);
    expect(yearsTile()).not.toMatch(/actual|12/);
  });
  it("the years are counted at the real pace, which the tile names, never at the slider's rounded seed", () => {
    render(<MarketShockCard avgMonthlySavings={{ pace: 3_986, seed: 4_000, months: 12 }} />);
    const loss = snap.total - bearCaseTotal(snap);
    expect(yearsTile()).toBe(`≈ Years of saving${(loss / (3_986 * 12)).toFixed(1)}at your actual ${fmtUSD0(3_986)}/mo savings pace`);
  });
  it("a pace at or below none counts no years and says why: income did not cover spending", () => {
    render(<MarketShockCard avgMonthlySavings={{ pace: -1_250, seed: 0, months: 12 }} />);
    expect(yearsTile()).toBe(`≈ Years of saving—no savings pace: income did not cover spending over the last 12 full months `
      + `(${fmtUSD0(-1_250)}/mo)`);
    expect(yearsTile()).not.toMatch(/your actual/);
  });
  it("a household with stocks: the bear case, the net worth after the shock and the loss carry their tones (control)", () => {
    render(<><ForwardLookStrip /><MarketShockCard avgMonthlySavings={PACE} /></>);
    expect(screen.getByTestId("kpi-bear-case").querySelector(".text-amber-400")).not.toBeNull();
    const card = within(screen.getByTestId("card-market-shock"));
    expect(card.getByText("Net worth after shock").nextElementSibling!.className).toContain("text-amber-400");
    expect(card.getByText("Paper loss").nextElementSibling!.className).toContain("text-red-400");
  });
  it("sliders moved to no fall: a true $0 loss, with no warning tone", () => {
    render(<MarketShockCard avgMonthlySavings={PACE} />);
    for (const s of [...screen.getByTestId("card-market-shock").querySelectorAll('input[type="range"]')] as HTMLInputElement[]) {
      fireEvent.change(s, { target: { value: "0" } });
    }
    const card = within(screen.getByTestId("card-market-shock"));
    expect(card.getByText("Paper loss").nextElementSibling!.textContent).toBe(fmtUSD0(0));
    expect(card.getByText("Paper loss").nextElementSibling!.className).not.toMatch(/red|amber/);
    expect(card.getByText("Net worth after shock").nextElementSibling!.className).not.toMatch(/red|amber/);
  });
  it("the card's sliders open on SHOCK_DEFAULTS", () => {
    render(<MarketShockCard avgMonthlySavings={PACE} />);
    const sliders = [...screen.getByTestId("card-market-shock").querySelectorAll('input[type="range"]')] as HTMLInputElement[];
    expect(sliders.map((s) => Number(s.value))).toEqual([SHOCK_DEFAULTS.eq, SHOCK_DEFAULTS.crypto, SHOCK_DEFAULTS.priv, SHOCK_DEFAULTS.house]);
  });
  it("the note names a mortgage only for a household that has one", () => {
    const note = () => screen.getByTestId("market-shock-note").textContent ?? "";
    render(<MarketShockCard avgMonthlySavings={PACE} />);
    expect(note()).toMatch(/^Only the classes the sliders name move: cash, the mortgage and every other debt stay as they are\./);
    cleanup();
    rows.snapshot = snapFactory()({ total: 90_000, by_class: { "Stocks/ETF": 100_000, Cash: 5_000, "Other Debt": -15_000 } });
    render(<MarketShockCard avgMonthlySavings={PACE} />);
    expect(note()).toMatch(/^Only the classes the sliders name move: cash and every debt stay as they are\./);
    expect(note()).not.toMatch(/mortgage/);
  });
  it("cash alone (the one-file household): no loss, no home slider, and the years name no pace", () => {
    rows.snapshot = snapFactory()({ total: 25_000, by_class: { Cash: 25_000 } });
    render(<MarketShockCard avgMonthlySavings={PACE} />);
    const card = within(screen.getByTestId("card-market-shock"));
    expect(card.getByText("Paper loss").parentElement!.textContent).toBe("Paper loss—nothing held that the sliders move");
    expect(yearsTile()).toBe("≈ Years of saving—no loss to make up");
    expect(card.queryByText("Home value"), "no home: no home slider").toBeNull();
  });
  it("a home and no investments, its slider at 0%: no loss and no pace named; moving the home's slider makes one", () => {
    rows.snapshot = snapFactory()({ total: 428_260, by_class: { Car: 21_000, Cash: 188_649, House: 640_000, Mortgage: -419_452,
      "Other Debt": -1_937 } });
    render(<MarketShockCard avgMonthlySavings={{ pace: 3_986, seed: 4_000, months: 12 }} />);
    const card = within(screen.getByTestId("card-market-shock"));
    expect(card.getByText("Paper loss").parentElement!.textContent).toBe("Paper loss—nothing held that the sliders move as set");
    expect(yearsTile()).toBe("≈ Years of saving—no loss to make up");
    expect(screen.getByTestId("card-market-shock").textContent).not.toMatch(/leverage cuts both ways|your actual/);
    const home = card.getByTitle(/^Home value/).querySelector('input[type="range"]') as HTMLInputElement;
    fireEvent.change(home, { target: { value: "-20" } });
    expect(card.getByText("Paper loss").parentElement!.textContent).toBe(`Paper loss${fmtDelta(-128_000)}debt balances unchanged — leverage cuts both ways`);
    expect(yearsTile()).toContain(`at your actual ${fmtUSD0(3_986)}/mo savings pace`);
  });
  it("moving a slider moves the card off the KPI, and the KPI stays the fixed case", () => {
    render(<><ForwardLookStrip /><MarketShockCard avgMonthlySavings={PACE} /></>);
    const slider = screen.getByTestId("card-market-shock").querySelector('input[type="range"]') as HTMLInputElement;
    fireEvent.change(slider, { target: { value: "-60" } });
    expect(afterShock()).not.toContain(fmtUSD0(bearCaseTotal(snap)));
    expect(afterShock()).toContain(fmtUSD0(shockedTotal(snap, { ...SHOCK_DEFAULTS, eq: -60 })));
    expect(screen.getByTestId("kpi-bear-case").textContent).toContain(fmtUSD0(bearCaseTotal(snap)));
  });
});

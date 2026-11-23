// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NO_TRANSACTIONS } from "../../components/NoData";
import type { CashflowMonth, Transaction } from "../../data/types";
import { cfmFactory, txFactory } from "../../lib/__fixtures__";
import { fmtUSD0 } from "../../lib/format";
import { MASK, setPrivate } from "../../lib/privacy";

// the cash-flow months the coverage floor is read from: by default every month is in coverage from January, before the
// data starts, so the floor cuts nothing
const cfm = cfmFactory();
const coverage = (floor: string | null): CashflowMonth[] =>
  ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10"].map((m) =>
    cfm({ month: `2026-${m}`, in_coverage: floor !== null && `2026-${m}` >= floor }));
const world = vi.hoisted(() => ({ txns: [] as Transaction[], generatedAt: "2026-10-04T09:00:00-05:00",
  cashflow: [] as CashflowMonth[] }));
vi.mock("../../data/DataContext", () => ({
  useData: () => {
    const dates = world.txns.map((t) => t.date).sort();
    // Indexes.dataThrough as the data context builds it: the newest row, never past the ingest day
    const last = dates.length ? dates[dates.length - 1] : null, ingest = world.generatedAt.slice(0, 10);
    return {
      data: { transactions: world.txns, cashflow: world.cashflow, manifest: { generated_at: world.generatedAt } },
      indexes: { dataRange: last === null ? null : { first: dates[0], last },
        dataThrough: last === null ? null : last > ingest ? ingest : last },
    };
  },
}));
import MonthToDate from "./MonthToDate";

beforeEach(() => { world.cashflow = coverage("2026-01"); });
afterEach(() => { cleanup(); setPrivate(false); world.generatedAt = "2026-10-04T09:00:00-05:00"; });

// synthetic: the data starts in March; Apr..Sep each spend 1,000 on the 2nd (and more on the 20th); October so far 1,234
const tx = txFactory({ category: "Dining" });
const history = (): Transaction[] => [
  tx({ date: "2026-03-15", amount: 900, type: "income", category: "Income" }),
  ...["04", "05", "06", "07", "08", "09"].flatMap((m) => [tx({ date: `2026-${m}-02`, amount: -1000 }), tx({ date: `2026-${m}-20`, amount: -4000 })]),
];
const october = (): Transaction[] => [
  tx({ date: "2026-10-01", amount: -634, category: "Groceries" }),
  tx({ date: "2026-10-02", amount: -400, category: "Dining" }),
  tx({ date: "2026-10-03", amount: -150, category: "Kids" }),
  tx({ date: "2026-10-03", amount: -50, category: "Travel" }),
];
const mount = () => render(<MemoryRouter><MonthToDate /></MemoryRouter>);
const card = () => screen.getByTestId("card-month-to-date");

describe("Home: This month so far", () => {
  it("shows the month's spend through the data's last day, the pace against the typical day, and the top three categories", () => {
    world.txns = [...history(), ...october()];
    mount();
    expect(card().textContent).toContain("through Oct 3, 2026");
    expect(screen.getByTestId("mtd-spend").textContent).toBe(fmtUSD0(1234));
    expect(screen.getByTestId("mtd-pace").textContent).toBe("23% above typical so far.");
    expect(screen.getByTestId("mtd-pace").className).toContain("text-amber-300");
    expect(card().textContent).toContain(`typical by the 3rd: ${fmtUSD0(1000)} — the median of 6 months (Apr 2026 – Sep 2026)`);
    const top = [...screen.getByTestId("mtd-top").querySelectorAll("li")].map((li) => li.textContent);
    expect(top).toEqual([`Groceries${fmtUSD0(634)}`, `Dining${fmtUSD0(400)}`, `Kids${fmtUSD0(150)}`]);
  });

  it("links to exactly this month's transactions (from the 1st to the data's last day) and to the budget", () => {
    world.txns = [...history(), ...october()];
    mount();
    const links = [...card().querySelectorAll("a")].map((a) => [a.textContent, a.getAttribute("href")]);
    expect(links).toContainEqual(["This month’s transactions →", "/spending/transactions?from=2026-10-01&to=2026-10-03"]);
    expect(links).toContainEqual(["Budget →", "/cash-flow/budget"]);
  });

  it("in the first six days it says the comparison is early and noisy; a week in it does not", () => {
    world.txns = [...history(), ...october()];
    mount();
    expect(screen.getByTestId("mtd-early").textContent).toMatch(/^Early in the month: 3 days of spending/);
    cleanup();
    world.txns = [...history(), ...october(), tx({ date: "2026-10-07", amount: -1 })];
    world.generatedAt = "2026-10-08T09:00:00-05:00";
    mount();
    expect(screen.queryByTestId("mtd-early")).toBeNull();
  });

  it("a row dated after the ingest day (a pending charge's settlement) is not a day of data and is not counted", () => {
    world.txns = [...history(), ...october(), tx({ date: "2026-10-09", amount: -5000, category: "Travel" })];
    world.generatedAt = "2026-10-05T09:00:00-05:00";
    mount();
    expect(card().textContent).toContain("through Oct 4, 2026");
    expect(screen.getByTestId("mtd-spend").textContent).toBe(fmtUSD0(1234));
  });

  it("the ingest day is still coming in: the month and every prior month are cut the day before, and the header says so", () => {
    world.txns = [...history(), ...october(), tx({ date: "2026-10-04", amount: -5000, category: "Travel" }),
      ...["04", "05", "06", "07", "08", "09"].map((m) => tx({ date: `2026-${m}-04`, amount: -3000 }))];
    world.generatedAt = "2026-10-04T11:00:00-05:00";
    mount();
    expect(card().textContent).toContain("through Oct 3, 2026");
    // its basis on its own line, beside the figure: the spending pages' totals count the ingest day's rows too
    expect(screen.getByTestId("mtd-basis").textContent)
      .toBe("spent in Oct 2026 through Oct 3, 2026, the last complete day (Oct 4, 2026 is still coming in)");
    expect(screen.getByTestId("mtd-spend").textContent, "the partial 4th is not counted").toBe(fmtUSD0(1234));
    expect(card().textContent, "nor the prior months' 4ths").toContain(`typical by the 3rd: ${fmtUSD0(1000)}`);
    const tx_ = [...card().querySelectorAll("a")].find((a) => /This month/.test(a.textContent ?? ""));
    expect(tx_?.getAttribute("href")).toBe("/spending/transactions?from=2026-10-01&to=2026-10-03");
  });

  it("data that ends on the 1st, which is the ingest day: no complete day of the month yet, said plainly", () => {
    world.txns = [...history(), tx({ date: "2026-10-01", amount: -50 })];
    world.generatedAt = "2026-10-01T11:00:00-05:00";
    mount();
    expect(screen.getByTestId("mtd-empty").textContent).toBe("No complete day of Oct 2026 yet: today, Oct 1, 2026, is still coming in.");
    expect(screen.queryByTestId("mtd-spend")).toBeNull();
  });

  it("a row the bank dated into the next month: this month is still the ingest month, through the day before the ingest", () => {
    world.txns = [...history(), ...october(), tx({ date: "2026-11-02", amount: -999, category: "Health" })];
    world.cashflow = [...coverage("2026-01"), cfm({ month: "2026-11" })];
    mount();
    expect(card().textContent).toContain("through Oct 3, 2026");
    expect(screen.getByTestId("mtd-spend").textContent, "the row dated ahead is not counted").toBe(fmtUSD0(1234));
    expect(card().textContent).not.toMatch(/Nov 2026|Health/);
  });

  it("with no earlier month and no spend yet it says so plainly", () => {
    world.txns = [tx({ date: "2026-10-01", amount: 900, type: "income", category: "Income" })];
    world.generatedAt = "2026-10-02T09:00:00-05:00";
    mount();
    expect(screen.getByTestId("mtd-pace").textContent).toBe("No earlier months to compare with yet.");
    expect(card().textContent).toContain("No spending yet this month.");
    expect(screen.getByTestId("mtd-early").textContent).toMatch(/one day of spending/);
  });

  it("one checking file of two months (the tiny household): one earlier month is no typical, and the card says so", () => {
    world.txns = [tx({ date: "2026-09-01", amount: 900, type: "income", category: "Income" }),
      tx({ date: "2026-09-02", amount: -1000 }), ...october()];
    mount();
    expect(screen.getByTestId("mtd-pace").textContent).toBe("Only 1 earlier month to compare with yet: a typical pace needs 3.");
    expect(screen.getByTestId("mtd-pace").className, "no amber over a comparison it cannot make").not.toContain("text-amber-300");
    expect(card().textContent).not.toMatch(/typical by the|above typical|below typical|median of \d/);
  });

  it("no transactions at all: the card stays, with nothing to say but that and what brings them", () => {
    world.txns = [];
    mount();
    expect(card().textContent).toContain(NO_TRANSACTIONS);
  });

  it("privacy mode masks every amount and keeps the pace's percentage, the dates and the category names", () => {
    world.txns = [...history(), ...october()];
    setPrivate(true);
    mount();
    const text = card().textContent ?? "";
    expect(text).toContain(MASK);
    expect(/\$\s?\d|\d,\d{3}/.test(text), "no amount survives").toBe(false);
    expect(text).toContain("23% above typical so far.");
    expect(text).toContain("Groceries");
    expect(text).toContain("through Oct 3, 2026");
  });

  it("the coverage floor comes from the data: a later floor cuts the compared months, and the fine print names it", () => {
    world.txns = [...history(), ...october()];
    world.cashflow = coverage("2026-07");
    mount();
    expect(card().textContent).toContain(`the median of 3 months (Jul 2026 – Sep 2026)`);
    expect(card().textContent).toContain("a month before Jul 2026 or with no data is left out, and so is the data’s first "
      + "month unless the data begins by its 3rd.");
  });

  it("the fine print says the day still coming in is the last ingest's, never a feed's refresh", () => {
    world.txns = [...history(), ...october()];
    mount();
    expect(card().textContent).toContain("Through the last complete day: the day of the last ingest may still be coming in, "
      + "so it waits for the next one.");
    expect(card().textContent).not.toMatch(/refresh/);
  });

  it("with no month in coverage nothing is compared, and the fine print names no floor", () => {
    world.txns = [...history(), ...october()];
    world.cashflow = coverage(null);
    mount();
    expect(screen.getByTestId("mtd-pace").textContent).toBe("No earlier months to compare with yet.");
    expect(screen.getByTestId("mtd-spend").textContent, "this month's own spend still shows").toBe(fmtUSD0(1234));
    const text = card().textContent ?? "";
    expect(text).toContain("a month with no data is left out, and so is the data’s first month unless the data begins by its 3rd.");
    expect(text).not.toMatch(/before (null|undefined|—)|null|undefined/);
  });
});

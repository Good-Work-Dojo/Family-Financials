// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RecurringItem } from "../../data/types";
import { recFactory } from "../../lib/__fixtures__";

const world = vi.hoisted(() => ({ recurring: [] as RecurringItem[] }));
vi.mock("../../data/DataContext", () => ({
  useData: () => ({
    data: { recurring: world.recurring, accounts: [] }, missing: new Set<string>(),
  }),
}));
import NextThirtyDays from "./NextThirtyDays";

const TODAY = "2026-07-01";
const rec = recFactory();
// eleven schedules, each due on a different day inside the next 30
const eleven = () => Array.from({ length: 11 }, (_, i) =>
  rec({ vendor: `Vendor ${String.fromCharCode(65 + i)}`, account_id: "none", expected_dates: [`2026-07-${String(i + 2).padStart(2, "0")}`] }));
const phone = (is: boolean) => vi.stubGlobal("matchMedia", (query: string) => ({
  matches: is && query.includes("max-width: 767px"), media: query, addEventListener: () => {}, removeEventListener: () => {} }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); world.recurring = []; });
const mount = () => render(<MemoryRouter><NextThirtyDays today={TODAY} /></MemoryRouter>);
const listed = () => within(screen.getByTestId("card-next-30-days")).queryAllByRole("listitem").length;

describe("Home's next 30 days on a phone", () => {
  it("lists five of eleven below md, with 'N more →' to the rest on Recurring", () => {
    phone(true);
    world.recurring = eleven();
    mount();
    expect(listed()).toBe(5);
    const more = screen.getByRole("link", { name: "6 more →" });
    expect(more.getAttribute("href")).toBe("/spending/recurring");
    expect(more.className, "a 40px target on a phone").toContain("min-h-[40px]");
    expect(screen.getByTestId("card-next-30-days").textContent, "the total is the five listed, and says the rest are not in it")
      .toContain("≈ $100 = the next charge of each of the 5 schedules listed, not the 6 more");
  });
  it("the ≈ total is the sum of the rows as they are printed, in whole dollars, so it adds up to what the card lists", () => {
    phone(false);
    // three charges whose cents round up, down and up: printed $16, $13 and $144, summed to $173 (the cents would make $172)
    world.recurring = [15.51, 12.99, 143.5].map((typical_amount, i) =>
      rec({ vendor: `Vendor ${i}`, account_id: "none", typical_amount, expected_dates: [`2026-07-0${i + 2}`] }));
    mount();
    const card = screen.getByTestId("card-next-30-days").textContent ?? "";
    expect(card).toContain("$16");
    expect(card).toContain("$13");
    expect(card).toContain("$144");
    expect(card).toContain("≈ $173 = the next charge of each of the 3 schedules listed ·");
  });
  it("every row says its state: a charge within 14 days is upcoming, one further off is scheduled, never an unmarked row", () => {
    phone(false);
    world.recurring = [rec({ vendor: "Soon", account_id: "none", last_seen: "2026-06-01", expected_dates: ["2026-07-09"] }),
      rec({ vendor: "Later", account_id: "none", last_seen: "2026-06-01", expected_dates: ["2026-07-21"] })];
    mount();
    const rows = within(screen.getByTestId("card-next-30-days")).getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(rows[0]).toContain("upcoming Jul 9, 2026");
    expect(rows[1]).toContain("scheduled Jul 21, 2026");
  });
  it("one schedule: the total names it alone", () => {
    phone(false);
    world.recurring = eleven().slice(0, 1);
    mount();
    expect(screen.getByTestId("card-next-30-days").textContent).toContain("≈ $20 = the next charge of the schedule listed ·");
  });
  it("lists all eleven on a desktop (twelve is the cap there), with no 'more' link", () => {
    phone(false);
    world.recurring = eleven();
    mount();
    expect(listed()).toBe(11);
    expect(screen.queryByRole("link", { name: /more →/ })).toBeNull();
  });
  it("a desktop with thirteen still caps at twelve and links the rest; a phone with five has no link", () => {
    phone(false);
    world.recurring = [...eleven(), rec({ vendor: "Vendor L", account_id: "none", expected_dates: ["2026-07-13"] }), rec({ vendor: "Vendor M", account_id: "none", expected_dates: ["2026-07-14"] })];
    mount();
    expect(listed()).toBe(12);
    expect(screen.getByRole("link", { name: "1 more →" })).toBeDefined();
    cleanup();
    phone(true);
    world.recurring = eleven().slice(0, 5);
    mount();
    expect(listed()).toBe(5);
    expect(screen.queryByRole("link", { name: /more →/ })).toBeNull();
  });
});

describe("Home's next 30 days with nothing to list", () => {
  it("with no schedule detected it says so, and what makes one; never that none is expected", () => {
    phone(false);
    mount();
    expect(screen.getByTestId("next-30-empty").textContent).toBe("No schedule detected yet: a bill or subscription becomes "
      + "one once the transactions show it repeating at a regular interval.");
  });
  it("with recurring series that are no schedule (below the promotion bar) it still says no schedule is detected", () => {
    phone(false);
    world.recurring = [rec({ vendor: "Vendor Y", account_id: "none", schedule: false, expected_dates: ["2026-07-05", "2026-08-05", "2026-09-05"] })];
    mount();
    expect(screen.getByTestId("next-30-empty").textContent).toMatch(/^No schedule detected yet:/);
  });
  it("with schedules that fall later it says none is expected in the next 30 days", () => {
    phone(false);
    world.recurring = [rec({ vendor: "Vendor Z", account_id: "none", expected_dates: ["2026-09-01"] })];
    mount();
    expect(screen.getByTestId("next-30-empty").textContent).toBe("No scheduled charge is expected in the next 30 days.");
  });
});

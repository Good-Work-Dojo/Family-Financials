// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { BalancePoint } from "../data/types";
import { cardHeadroom, fmtUtilization, utilization } from "./credit";

const pt = (balance: number, available: number | null, limit: number | null): BalancePoint =>
  ({ date: "2026-07-01", balance, available, limit, utilization: null, source: "simplefin" });

describe("utilization: the one card utilisation, at one precision", () => {
  it("is what a card owes over its limit, printed to one decimal", () => {
    expect(utilization(-851.88, 8000)).toBeCloseTo(0.106485);
    expect(fmtUtilization(utilization(-851.88, 8000)), "Balances and Liabilities print this one figure").toBe("10.6%");
    expect(utilization(-11000, 10000), "over the limit").toBeCloseTo(1.1);
    expect(utilization(50, 1000), "a card in credit owes nothing").toBe(0);
  });
  it("is unknown without a limit above zero - null, printed as a dash, never 0%", () => {
    expect(utilization(-250, null)).toBeNull();
    expect(utilization(-250, 0)).toBeNull();
    expect(fmtUtilization(null)).toBe("—");
  });
});

describe("cardHeadroom", () => {
  it("uses the feed's available-credit figure where it has one; utilisation is owed over the limit", () => {
    expect(cardHeadroom([pt(-250, 9000, 10000)])).toEqual(
      { debt: 250, inCredit: 0, available: 9000, known: 1, cards: 1, derived: false, utilization: 250 / 10000 });
  });
  it("derives limit − owed where the feed has a limit but no available figure (Balances shows the limits)", () => {
    const h = cardHeadroom([pt(-250, null, 10000), pt(-1000, 0, 5000)]);   // a 0 'available' beside a limit is not a figure
    expect(h).toMatchObject({ debt: 1250, available: 9750 + 4000, known: 2, cards: 2, derived: true });
    expect(h.utilization).toBeCloseTo(1250 / 15000);
  });
  it("counts only the cards whose headroom is known, and says how many of how many", () => {
    const h = cardHeadroom([pt(-250, null, 10000), pt(-400, null, null)]);
    expect(h).toMatchObject({ debt: 650, available: 9750, known: 1, cards: 2 });
    expect(h.utilization, "over the card with a limit only").toBeCloseTo(250 / 10000);
  });
  it("a card with an available figure and no limit has headroom but no utilisation", () => {
    expect(cardHeadroom([pt(-250, 9000, null)])).toMatchObject({ available: 9000, known: 1, utilization: null });
  });
  it("one count describes both figures: with a counted card lacking a limit, no utilisation is given for the others", () => {
    // two cards' headroom is known, only one has a limit: "2 of 2 cards" may not stand beside one card's utilisation
    const h = cardHeadroom([pt(-250, null, 10000), pt(-400, 600, null)]);
    expect(h).toMatchObject({ available: 9750 + 600, known: 2, cards: 2, utilization: null });
  });
  it("is null - never $0 - when no card has a limit or an available figure", () => {
    expect(cardHeadroom([pt(-250, null, null)])).toMatchObject({ available: null, known: 0, utilization: null });
  });
  it("an over-limit card has no headroom (0, not negative), and a card in credit has more than its limit", () => {
    expect(cardHeadroom([pt(-11000, null, 10000)])).toMatchObject({ available: 0 });
    expect(cardHeadroom([pt(-11000, null, 10000)]).utilization).toBeCloseTo(1.1);
    expect(cardHeadroom([pt(50, null, 1000)])).toMatchObject({ debt: 0, inCredit: 1, available: 1050, utilization: 0 });
  });
  it("a card in credit owes nothing: the debt is what the other cards owe, never netted down by its credit", () => {
    expect(cardHeadroom([pt(-1000, null, 5000), pt(150, null, 2000)])).toMatchObject({ debt: 1000, inCredit: 1, cards: 2 });
  });
});

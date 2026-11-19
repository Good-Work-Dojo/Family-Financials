// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { recFactory, snapFactory } from "./__fixtures__";
import { bearCaseClasses, bearCaseTotal, holdsShockable, lumpyBillsTotal, SHOCK_DEFAULTS, shockedTotal } from "./forwardLook";
import { obligationMonths } from "./obligations";

describe("forward-look strip math", () => {
  it("bear case: equities −30%, speculative −50%, cash and debt untouched", () => {
    const s = snapFactory()({ by_class: { "Stocks/ETF": 1000, Crypto: 100, "Private Stock": 200, Cash: 50, Mortgage: -300, House: 400 } });
    expect(bearCaseTotal(s)).toBe(700 + 50 + 100 + 50 - 300 + 400);
  });
  it("the bear case is shockedTotal at SHOCK_DEFAULTS - one table of which class takes which shock", () => {
    const s = snapFactory()({ by_class: { "Stocks/ETF": 1000, Retirement: 100, "529": 100, "Real Estate/Venture": 100, Crypto: 100,
      "Private Stock": 200, House: 400, Cash: 50, Mortgage: -300, Margin: -10, "Other Debt": -5, Other: 7 } });
    expect(bearCaseTotal(s)).toBe(shockedTotal(s, SHOCK_DEFAULTS));
    expect(SHOCK_DEFAULTS).toEqual({ eq: -30, crypto: -50, priv: -50, house: 0 });
    // each kind moves only its own classes; debt and cash never move, and a class nobody shocks is unchanged
    expect(shockedTotal(s, { eq: 0, crypto: 0, priv: 0, house: -10 }) - shockedTotal(s, { eq: 0, crypto: 0, priv: 0, house: 0 })).toBeCloseTo(-40);
    expect(shockedTotal(s, { eq: -100, crypto: 0, priv: 0, house: 0 }) - shockedTotal(s, { eq: 0, crypto: 0, priv: 0, house: 0 })).toBeCloseTo(-1300);
    expect(shockedTotal(s, { eq: 0, crypto: -100, priv: 0, house: 0 }) - shockedTotal(s, { eq: 0, crypto: 0, priv: 0, house: 0 })).toBeCloseTo(-100);
    expect(shockedTotal(s, { eq: 0, crypto: 0, priv: -100, house: 0 }) - shockedTotal(s, { eq: 0, crypto: 0, priv: 0, house: 0 })).toBeCloseTo(-200);
  });
  it("bearCaseClasses: exactly the classes the bear case moves, read from the calculation's own table", () => {
    const classes = bearCaseClasses();
    expect(classes).toEqual(["Stocks/ETF", "Retirement", "529", "Real Estate/Venture", "Crypto", "Private Stock"]);
    // each named class moves the bear case, and the home (0% in this case), cash and debt do not
    const s = snapFactory();
    for (const cls of classes) expect(bearCaseTotal(s({ by_class: { [cls]: 1_000 } })), cls).toBeLessThan(1_000);
    for (const cls of ["House", "Cash", "Mortgage", "Precious Metals"]) {
      expect(bearCaseTotal(s({ by_class: { [cls]: 1_000 } })), cls).toBe(1_000);
      expect(classes, cls).not.toContain(cls);
    }
  });
  it("holdsShockable: a class the sliders reach, with a value; cash and debt alone are nothing a fall moves", () => {
    const s = snapFactory();
    expect(holdsShockable(s({ by_class: { Cash: 8_000, "Other Debt": -1_500 } }))).toBe(false);
    expect(holdsShockable(s({ by_class: { Cash: 8_000, "Stocks/ETF": 0 } })), "a class with nothing in it").toBe(false);
    expect(holdsShockable(s({ by_class: { Cash: 8_000, House: 400_000 } }))).toBe(true);
    expect(holdsShockable(s({ by_class: { Retirement: 10 } }))).toBe(true);
  });
  it("with no snapshot there is no net worth to shock: null, never 0", () => {
    expect(shockedTotal(null, SHOCK_DEFAULTS)).toBeNull();
    expect(bearCaseTotal(null)).toBeNull();
  });
  it("lumpy bills: annual and quarterly renewals over the horizon, summed - a big charge that recurs monthly or biweekly is routine", () => {
    const rec = recFactory({ active: true });
    const items = [
      rec({ vendor: "Insurance", cadence: "annual", typical_amount: 1200, next_expected: "2026-09-01" }),
      rec({ vendor: "Coffee", cadence: "monthly", typical_amount: 20, next_expected: "2026-07-05" }),
      rec({ vendor: "Big", cadence: "monthly", typical_amount: 600, next_expected: "2026-07-10" }),
      rec({ vendor: "Home loan", cadence: "biweekly", typical_amount: 1500, next_expected: "2026-07-03", median_gap_days: 14 }),
      rec({ vendor: "Domain", cadence: "annual", typical_amount: 18, next_expected: "2026-08-15" }),
    ];
    // the twelve months after June 2026: the two annual renewals only (a big charge that recurs monthly or biweekly
    // never counts)
    expect(lumpyBillsTotal(items, obligationMonths("2026-06"))).toBe(1200 + 18);
  });
  it("lumpy bills over nothing are no total: null with nothing detected, or with no renewal due in the window", () => {
    const rec = recFactory({ active: true });
    const months = obligationMonths("2026-06");
    expect(lumpyBillsTotal([], months)).toBeNull();
    expect(lumpyBillsTotal([rec({ vendor: "Coffee", cadence: "monthly", typical_amount: 20, next_expected: "2026-07-05" })],
      months), "a monthly charge only").toBeNull();
    expect(lumpyBillsTotal([rec({ vendor: "Domain", cadence: "annual", typical_amount: 18, last_seen: "2026-08-15",
      next_expected: "2027-08-15", expected_dates: ["2027-08-15"] })], months), "a renewal past the twelve months").toBeNull();
  });
});

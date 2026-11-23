// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { AccountMeta } from "../data/types";
import { defaultClass } from "./accounts";
import { fmtUSD, fmtUSD0 } from "./format";
import { amortize } from "./projections";
import {
  amountText, belowZero, breakevenOccupancyPct, capGainsDragMax, carryBreakdown, cashLikeDrawTotal,
  cashNeeded, comparisonRows, DEFAULT_INPUTS, downPaymentSweep, downPaymentUSD,
  eligibleFundingAccounts, equityCurve, fundingCloses, fundingRows, fundingState, fundingSummary, type FundingVerdict,
  fundingVerdictBrief, fundingVerdictText, isRetirementName, loanAmount, monthlyPI, netMonthlyCost, pmiLikely, pmt,
  rateSensitivity, rentalBreakdown, SecondHouseInputs, TERM_MONTHS,
} from "./secondHouse";

const inputs = (over: Partial<SecondHouseInputs> = {}): SecondHouseInputs =>
  ({ ...DEFAULT_INPUTS, ...over });

// an account that states no class takes its kind's, as accounts.json resolves it
const acct = (over: Partial<AccountMeta>): AccountMeta => ({
  id: "x", name: "X", institution: "X", owner: "Alex", kind: "checking",
  txn_data_start: null, txn_data_end: null,
  latest_balance: 1000, latest_balance_as_of: "2026-06-27", ledger_drift: null,
  ...over,
  asset_class: over.asset_class ?? defaultClass(over.kind ?? "checking", false),
});

describe("pmt", () => {
  // $300,000 @ 6% / 360mo — standard amortization tables give $1,798.65
  it("matches the closed-form level payment", () => {
    expect(pmt(300_000, 6)).toBeCloseTo(1798.65, 1);
  });
  it("handles 0% (straight-line) and non-positive principal", () => {
    expect(pmt(360_000, 0)).toBeCloseTo(1000);
    expect(pmt(0, 6)).toBe(0);
    expect(pmt(-5, 6)).toBe(0);
  });
  it("round-trips with amortize: pays off in exactly the term", () => {
    const res = amortize({ balance: 480_000, annualRatePct: 6, monthlyPayment: pmt(480_000, 6) });
    expect(Math.abs(res.payoffMonths - TERM_MONTHS)).toBeLessThanOrEqual(1);
    expect(res.schedule[res.schedule.length - 1].balance).toBeCloseTo(0, 4);
  });
});

describe("deal math", () => {
  const i = inputs();  // $600k, 20% down, 6%, $10k + $10k
  it("splits price into down payment and loan", () => {
    expect(downPaymentUSD(i)).toBe(120_000);
    expect(loanAmount(i)).toBe(480_000);
  });
  it("cash needed = down + closing + post-move-in", () => {
    expect(cashNeeded(i)).toBe(140_000);
  });
  it("computes the headline P&I", () => {
    expect(monthlyPI(i)).toBeCloseTo(2877.84, 1);
  });
  it("flags PMI below 20% down only", () => {
    expect(pmiLikely(inputs({ downPct: 19.9 }))).toBe(true);
    expect(pmiLikely(i)).toBe(false);
  });
});

describe("carryBreakdown", () => {
  it("derives each monthly line from the inputs", () => {
    const c = carryBreakdown(inputs());
    expect(c.tax).toBeCloseTo(600_000 * 0.011 / 12);        // $550
    expect(c.insurance).toBeCloseTo(200);
    expect(c.hoa).toBe(0);
    expect(c.utilities).toBe(300);
    expect(c.maintenance).toBeCloseTo(500);
    expect(c.total).toBeCloseTo(c.pi + c.tax + c.insurance + c.hoa + c.utilities + c.maintenance);
  });
  it("price 0 leaves only the fixed items", () => {
    const c = carryBreakdown(inputs({ price: 0, downPct: 0 }));
    expect(c.pi).toBe(0);
    expect(c.tax).toBe(0);
    expect(c.maintenance).toBe(0);
    expect(c.total).toBeCloseTo(200 + 300);
  });
});

describe("rentalBreakdown / breakeven", () => {
  it("fees hit gross only; cleaning is flat", () => {
    const r = rentalBreakdown(inputs());  // $250 × 15 nights, 15% platform, 4×$150
    expect(r.bookedNights).toBe(15);
    expect(r.gross).toBe(3750);
    expect(r.platformFee).toBeCloseTo(562.5);
    expect(r.mgmtFee).toBe(0);
    expect(r.cleaning).toBe(600);
    expect(r.net).toBeCloseTo(3750 - 562.5 - 600);
  });
  it("occupancy 0 → net is the (negative) cleaning cost, un-clamped", () => {
    const r = rentalBreakdown(inputs({ occupancyPct: 0 }));
    expect(r.net).toBe(-600);
    expect(netMonthlyCost(inputs({ occupancyPct: 0 })))
      .toBeCloseTo(carryBreakdown(inputs()).total + 600);
  });
  it("breakeven occupancy round-trips: at that occupancy, net rental == carry", () => {
    const be = breakevenOccupancyPct(inputs());
    expect(be).not.toBeNull();
    const at = inputs({ occupancyPct: be! });
    expect(rentalBreakdown(at).net).toBeCloseTo(carryBreakdown(at).total, 6);
  });
  it("returns null when no revenue is possible", () => {
    expect(breakevenOccupancyPct(inputs({ nightlyRate: 0 }))).toBeNull();
    expect(breakevenOccupancyPct(inputs({ platformFeePct: 60, mgmtFeePct: 40 }))).toBeNull();
  });
  it("returns >100 un-clamped when carry can't be covered", () => {
    const be = breakevenOccupancyPct(inputs({ nightlyRate: 50 }));
    expect(be).not.toBeNull();
    expect(be!).toBeGreaterThan(100);
  });
});

describe("funding", () => {
  const accounts: AccountMeta[] = [
    acct({ id: "cc", name: "Alex's Card ...0303", kind: "credit-card", latest_balance: -250 }),
    acct({ id: "loan", name: "Home Mortgage", kind: "loan", latest_balance: -310_000 }),
    acct({ id: "roth", name: "Sam's Roth IRA", kind: "brokerage", latest_balance: 24_000 }),
    acct({ id: "chk", name: "Joint Checking ...0101", latest_balance: 8_500 }),
    acct({ id: "wallet", name: "Venmo wallet (Alex)", kind: "wallet", latest_balance: null, latest_balance_as_of: null }),
    acct({ id: "brk", name: "Joint Brokerage", kind: "brokerage", latest_balance: 48_000 }),
    acct({ id: "bk_cash", name: "Example Brokerage Joint Cash (7C3M2D1X)", kind: "brokerage", asset_class: "Cash", latest_balance: 6_500 }),
    acct({ id: "sav", name: "Joint Savings ...0202", kind: "savings", latest_balance: 1_200 }),
    acct({ id: "k529", name: "529 Robin (...0808)", kind: "brokerage", latest_balance: 9_500 }),
    acct({ id: "k401", name: "Alex's 401(k)", kind: "brokerage", latest_balance: 160_000 }),
  ];

  it("excludes credit cards and loans; keeps wallets; sorts cash first, null-balance last", () => {
    const ids = eligibleFundingAccounts(accounts).map((a) => a.id);
    expect(ids).not.toContain("cc");
    expect(ids).not.toContain("loan");
    // cash tier includes the brokerage-held cash account (asset_class Cash)
    expect(ids.slice(0, 3)).toEqual(["chk", "bk_cash", "sav"]);
    expect(ids[ids.length - 1]).toBe("wallet");
  });

  it("classifies retirement by name", () => {
    for (const name of ["Sam's Roth IRA", "Example Brokerage Rollover IRA", "Example Retirement 401k", "529 Jamie (...0909)"]) {
      expect(isRetirementName(name), name).toBe(true);
    }
    for (const name of ["Joint Checking ...0101", "Joint Brokerage", "Example Brokerage Joint Investing (4F9Q7BK2)"]) {
      expect(isRetirementName(name), name).toBe(false);
    }
  });

  // the exports carry each account's resolved class: a 403(b), a TSP, a 457 or an HSA the household files as
  // Retirement (or 529) is one, whatever its name says
  it("classifies retirement by the account's class first, its name second", () => {
    const by = (a: AccountMeta) => fundingRows([a], {})[0];
    for (const name of ["Alex's 403(b)", "Sam's TSP", "Example 457 plan", "Family HSA"]) {
      const r = by(acct({ id: "r", name, kind: "brokerage", asset_class: "Retirement", latest_balance: 10_000 }));
      expect([r.retirement, r.taxableDrag], name).toEqual([true, false]);
    }
    const college = by(acct({ id: "c", name: "Jamie's college fund", kind: "brokerage", asset_class: "529", latest_balance: 10_000 }));
    expect([college.retirement, college.taxableDrag]).toEqual([true, false]);
    // ranked after the taxable brokerage, as a named IRA is
    const ids = eligibleFundingAccounts([
      acct({ id: "tsp", name: "Sam's TSP", kind: "brokerage", asset_class: "Retirement", latest_balance: 10_000 }),
      acct({ id: "brk", name: "Joint Brokerage", kind: "brokerage", asset_class: "Stocks/ETF", latest_balance: 10_000 }),
    ]).map((a) => a.id);
    expect(ids).toEqual(["brk", "tsp"]);
    const taxable = by(acct({ id: "b", name: "Joint Brokerage", kind: "brokerage", asset_class: "Stocks/ETF", latest_balance: 10_000 }));
    expect([taxable.retirement, taxable.taxableDrag]).toEqual([false, true]);
  });

  it("joins draws, flags overdraw without clamping, and marks tax treatments", () => {
    const rows = fundingRows(accounts, { chk: 12_000, brk: 5_000, bogus: 99, sav: -50 });
    const by = Object.fromEntries(rows.map((r) => [r.accountId, r]));
    expect(by.chk.after).toBe(8_500 - 12_000);
    expect(by.chk.overdraw).toBe(true);           // kept, shown red — never clamped
    expect(by.sav.draw).toBe(0);                  // negative input coerced
    expect(by.brk.taxableDrag).toBe(true);
    expect(by.bk_cash.taxableDrag).toBe(false);   // brokerage-held cash — nothing to realize
    expect(by.bk_cash.cashLike).toBe(true);       // drawing it spends real cash
    expect(by.brk.cashLike).toBe(false);
    expect(by.roth.retirement).toBe(true);
    expect(by.roth.taxableDrag).toBe(false);
    expect(by.k529.retirement).toBe(true);
    expect(by.wallet.before).toBeNull();
    expect(by.wallet.after).toBeNull();
    expect(by.wallet.overdraw).toBe(false);
    expect(rows.some((r) => r.accountId === "bogus")).toBe(false);
  });

  it("counts a draw from brokerage-held cash against liquid cash", () => {
    const rows = fundingRows(accounts, { chk: 1_000, bk_cash: 2_000, brk: 500 });
    expect(cashLikeDrawTotal(rows)).toBe(3_000);   // chk + bk_cash; brk is not cash
  });

  it("summarizes gap vs surplus (mutually exclusive)", () => {
    const rows = fundingRows(accounts, { chk: 5_000 });
    const short = fundingSummary(rows, 140_000);
    expect(short.drawn).toBe(5_000);
    expect(short.gap).toBe(135_000);
    expect(short.surplus).toBe(0);
    const over = fundingSummary(rows, 4_000);
    expect(over.gap).toBe(0);
    expect(over.surplus).toBe(1_000);
    expect(fundingSummary(rows, 0).pctFunded).toBe(1);
  });

  it("cap-gains ceiling and cash-like totals", () => {
    expect(capGainsDragMax(10_000, 15)).toBe(1_500);
    const rows = fundingRows(accounts, { chk: 5_000, sav: 500, brk: 9_000 });
    expect(cashLikeDrawTotal(rows)).toBe(5_500);
  });

  it("labels each account by its class, what the dashboard calls it everywhere, never by its kind", () => {
    const rows = fundingRows([
      acct({ id: "chk", name: "Joint Checking" }),
      acct({ id: "k401", name: "Alex's 401(k)", kind: "brokerage", asset_class: "Retirement" }),
      acct({ id: "brk", name: "Joint Brokerage", kind: "brokerage" }),
    ], {});
    expect(rows.map((r) => r.assetClass)).toEqual(["Cash", "Stocks/ETF", "Retirement"]);
  });
});

// The plan's verdict is a claim about every draw: "fully funded" only when the draws add up to what the deal needs AND
// none takes its account below zero; otherwise, in this order, the accounts a draw overdraws, the shortfall, the excess.
describe("fundingState and its words", () => {
  const two = [
    acct({ id: "chk", name: "Joint Checking", latest_balance: 17_633.72 }),
    acct({ id: "sav", name: "Joint Savings", kind: "savings", latest_balance: 106_751.35 }),
  ];
  const state = (draws: Record<string, number>, needed: number, accounts = two) =>
    fundingState(fundingRows(accounts, draws), needed);

  it("draws that add up to the need with no account below zero: fully funded", () => {
    const s = state({ chk: 10_000, sav: 90_000 }, 100_000);
    expect(s.verdict).toBe("funded");
    expect(s.overdrawn).toEqual([]);
    expect(fundingVerdictText(s)).toBe("fully funded ✓");
  });

  it("a draw past its account's balance names the account and by how much, even when the draws add up", () => {
    const s = state({ chk: 24_000, sav: 76_000 }, 100_000);
    expect(s.verdict).toBe("overdraw");
    expect(s.overdrawn).toEqual([{ accountId: "chk", name: "Joint Checking", by: 24_000 - 17_633.72 }]);
    expect(fundingVerdictText(s)).toBe(`Joint Checking would be overdrawn by ${fmtUSD0(6_366.28)}`);
  });

  it("an overdraw outranks a shortfall and an excess", () => {
    expect(state({ chk: 20_000 }, 100_000).verdict).toBe("overdraw");
    expect(state({ chk: 20_000, sav: 90_000 }, 100_000).verdict).toBe("overdraw");
  });

  // the line leads with the overdraw and goes on with the totals, so a shortfall or an excess is never hidden by it
  it("overdrawn and short: the account, then what is funded of what and the shortfall", () => {
    const s = state({ chk: 20_000 }, 100_000);
    expect(s.totals).toBe("short");
    expect(fundingVerdictText(s)).toBe(`Joint Checking would be overdrawn by ${fmtUSD0(2_366.28)}; `
      + `funded ${fmtUSD0(20_000)} of ${fmtUSD0(100_000)}; ${fmtUSD0(80_000)} short`);
    expect(fundingVerdictBrief(s)).toBe(`overdraws 1 account; ${fmtUSD0(80_000)} short`);
  });
  it("overdrawn and over: the account, then the excess", () => {
    const s = state({ chk: 20_000, sav: 90_000 }, 100_000);
    expect(s.totals).toBe("over");
    expect(fundingVerdictText(s)).toBe(`Joint Checking would be overdrawn by ${fmtUSD0(2_366.28)}; `
      + `drawn ${fmtUSD0(110_000)} for ${fmtUSD0(100_000)}; ${fmtUSD0(10_000)} more than needed`);
    expect(fundingVerdictBrief(s)).toBe(`overdraws 1 account; ${fmtUSD0(10_000)} over`);
  });
  it("overdrawn with the draws equal to the need: the account alone", () => {
    const s = state({ chk: 24_000, sav: 76_000 }, 100_000);
    expect(s.totals).toBe("exact");
    expect(fundingVerdictText(s)).toBe(`Joint Checking would be overdrawn by ${fmtUSD0(6_366.28)}`);
    expect(fundingVerdictBrief(s)).toBe("overdraws 1 account");
  });

  it("an account already below zero with no draw is no overdraw: the plan can still close", () => {
    const withOverdrawn = [acct({ id: "od", name: "Overdrawn Checking", latest_balance: -50 }), ...two];
    const s = state({ sav: 100_000 }, 100_000, withOverdrawn);
    expect(s.overdrawn).toEqual([]);
    expect(s.verdict).toBe("funded");
    expect(fundingRows(withOverdrawn, { sav: 100_000 }).find((r) => r.accountId === "od")?.overdraw).toBe(false);
    // a draw on it is one: it would be overdrawn by the draw and what it was already below zero
    const drawn = state({ od: 100, sav: 99_900 }, 100_000, withOverdrawn);
    expect(fundingVerdictText(drawn)).toBe(`Overdrawn Checking would be overdrawn by ${fmtUSD0(150)}`);
  });

  it("a draw on a balance nobody knows cannot be counted on: the plan does not close, and the line names the account", () => {
    const withWallet = [...two, acct({ id: "wallet", name: "Sample Wallet", kind: "wallet", latest_balance: null, latest_balance_as_of: null })];
    const s = state({ chk: 10_000, wallet: 500 }, 10_500, withWallet);
    expect(s.verdict).toBe("uncovered");
    expect(fundingCloses(s.verdict)).toBe(false);
    expect(fundingVerdictText(s)).toBe(`the balance of Sample Wallet is not known, so its draw of ${fmtUSD0(500)} cannot be counted on`);
    expect(fundingVerdictBrief(s)).toBe("1 draw on an unknown balance");
    // with a shortfall too, the line goes on to it, and the total says how much of it rests on the unknown balance
    expect(fundingVerdictText(state({ chk: 10_000, wallet: 500 }, 20_000, withWallet))).toBe(`the balance of Sample Wallet is `
      + `not known, so its draw of ${fmtUSD0(500)} cannot be counted on; funded ${fmtUSD0(10_500)} of ${fmtUSD0(20_000)} `
      + `(incl. ${fmtUSD0(500)} on a balance that is not known); ${fmtUSD0(9_500)} short`);
    expect(fundingVerdictText(state({ chk: 10_000, wallet: 500 }, 10_000, withWallet))).toBe(`the balance of Sample Wallet is `
      + `not known, so its draw of ${fmtUSD0(500)} cannot be counted on; drawn ${fmtUSD0(10_500)} for ${fmtUSD0(10_000)} `
      + `(incl. ${fmtUSD0(500)} on a balance that is not known); ${fmtUSD0(500)} more than needed`);
    // an overdraw comes first
    const both = state({ chk: 20_000, wallet: 500 }, 20_500, withWallet);
    expect(both.verdict).toBe("overdraw");
    expect(fundingVerdictText(both)).toBe(`Joint Checking would be overdrawn by ${fmtUSD0(2_366.28)}; the balance of Sample `
      + `Wallet is not known, so its draw of ${fmtUSD0(500)} cannot be counted on`);
  });

  it("every overdrawn account is named, three at most, then how many more", () => {
    const five = ["A", "B", "C", "D", "E"].map((n, k) => acct({ id: n, name: `Account ${n}`, latest_balance: 100 * (k + 1) }));
    const draws = Object.fromEntries(five.map((a) => [a.id, a.latest_balance! + 50]));
    const all = (d: Record<string, number>) => Object.values(d).reduce((s, v) => s + v, 0);
    expect(fundingVerdictText(state(draws, all(draws), five))).toBe(
      `Account A would be overdrawn by ${fmtUSD0(50)}, Account B by ${fmtUSD0(50)}, Account C by ${fmtUSD0(50)} and 2 more`);
    expect(fundingVerdictText(state({ A: 150, B: 250 }, 400, five))).toBe(
      `Account A would be overdrawn by ${fmtUSD0(50)} and Account B by ${fmtUSD0(50)}`);
    expect(fundingVerdictText(state({ A: 150, B: 250, C: 350 }, 750, five))).toBe(
      `Account A would be overdrawn by ${fmtUSD0(50)}, Account B by ${fmtUSD0(50)} and Account C by ${fmtUSD0(50)}`);
    // three overdrawn and short: the three, then what is funded of what and the shortfall
    expect(fundingVerdictText(state({ A: 150, B: 250, C: 350 }, 10_000, five))).toBe(
      `Account A would be overdrawn by ${fmtUSD0(50)}, Account B by ${fmtUSD0(50)} and Account C by ${fmtUSD0(50)}; `
      + `funded ${fmtUSD0(750)} of ${fmtUSD0(10_000)}; ${fmtUSD0(9_250)} short`);
  });

  it("draws that add up to less than the need: what is funded, of what, and what is short", () => {
    const s = state({ sav: 83_500 }, 100_500);
    expect(s.verdict).toBe("short");
    expect(fundingVerdictText(s)).toBe(`funded ${fmtUSD0(83_500)} of ${fmtUSD0(100_500)}; ${fmtUSD0(17_000)} short`);
  });

  it("draws that add up to more than the need: the excess", () => {
    const s = state({ sav: 101_000 }, 100_000);
    expect(s.verdict).toBe("over");
    expect(fundingVerdictText(s)).toBe(`drawn ${fmtUSD0(101_000)} for ${fmtUSD0(100_000)}; ${fmtUSD0(1_000)} more than needed`);
  });

  it("an amount under a dollar is said in cents, never as $0", () => {
    expect(fundingVerdictText(state({ chk: 17_634 }, 17_634))).toBe(`Joint Checking would be overdrawn by ${fmtUSD(0.28)}`);
    expect(fundingVerdictText(state({ sav: 99_999.6 }, 100_000))).toBe(
      `funded ${fmtUSD0(99_999.6)} of ${fmtUSD0(100_000)}; ${fmtUSD(0.4)} short`);
    expect(state({ chk: 17_633.72 }, 17_633.72).verdict, "drawn to the cent: funded, not overdrawn").toBe("funded");
  });

  it("no account with a known balance: nothing can be drawn, so no verdict and no words", () => {
    const s = state({}, 100_000, [acct({ id: "chk", latest_balance: null, latest_balance_as_of: null })]);
    expect(s.verdict).toBe("unknown");
    expect(fundingVerdictText(s)).toBeNull();
    expect(fundingState([], 100_000).verdict).toBe("unknown");
  });

  it("the comparison's few words for each verdict", () => {
    expect([
      state({ chk: 10_000, sav: 90_000 }, 100_000), state({ sav: 83_500 }, 100_500), state({ sav: 101_000 }, 100_000),
      state({ chk: 24_000, sav: 76_000 }, 100_000), state({ chk: 24_000, sav: 200_000 }, 224_000),
    ].map(fundingVerdictBrief)).toEqual(["fully funded", `${fmtUSD0(17_000)} short`, `${fmtUSD0(1_000)} over`,
      "overdraws 1 account", "overdraws 2 accounts"]);
    expect(fundingVerdictBrief(state({}, 1, [acct({ latest_balance: null, latest_balance_as_of: null })]))).toBeNull();
  });

  it("closes: only a funded plan or one with more than it needs", () => {
    expect(["funded", "over", "short", "overdraw", "uncovered", "unknown"].map((v) => fundingCloses(v as FundingVerdict)))
      .toEqual([true, true, false, false, false, null]);
  });

  it("below zero and the amount's words go by whole cents", () => {
    expect([belowZero(-0.004), belowZero(-0.006), belowZero(0)]).toEqual([false, true, false]);
    expect([amountText(0.3), amountText(8_000)]).toEqual([fmtUSD(0.3), fmtUSD0(8_000)]);
  });
});

describe("chart series", () => {
  it("rate sensitivity: 17 points, monotonic, endpoints exact", () => {
    const pts = rateSensitivity(inputs());
    expect(pts).toHaveLength(17);
    expect(pts[0]).toEqual({ ratePct: 4.5, pi: pmt(480_000, 4.5) });
    expect(pts[16].ratePct).toBe(8.5);
    for (let k = 1; k < pts.length; k++) expect(pts[k].pi).toBeGreaterThan(pts[k - 1].pi);
  });
  it("down-payment sweep: 9 points, P&I falls and cash-to-close rises with down%", () => {
    const pts = downPaymentSweep(inputs());
    expect(pts).toHaveLength(9);
    expect(pts[0].downPct).toBe(10);
    expect(pts[8].downPct).toBe(50);
    expect(pts[2].cashToClose).toBe(140_000);     // 20% column matches cashNeeded
    for (let k = 1; k < pts.length; k++) {
      expect(pts[k].pi).toBeLessThan(pts[k - 1].pi);
      expect(pts[k].cashToClose).toBeGreaterThan(pts[k - 1].cashToClose);
    }
  });
  it("equity curve runs from the down payment to the full price", () => {
    const pts = equityCurve(inputs());
    expect(pts[0]).toEqual({ month: 0, balance: 480_000, equity: 120_000 });
    const last = pts[pts.length - 1];
    expect(Math.abs(last.month - TERM_MONTHS)).toBeLessThanOrEqual(1);
    expect(last.equity).toBeCloseTo(600_000, 4);
    expect(last.balance).toBeCloseTo(0, 4);
  });
  it("100% down collapses to a single fully-owned point", () => {
    expect(equityCurve(inputs({ downPct: 100 }))).toEqual([
      { month: 0, balance: 0, equity: 600_000 },
    ]);
  });
});

describe("comparisonRows", () => {
  it("derives one row per scenario; remaining liquid subtracts cash-like draws only", () => {
    const accounts = [
      acct({ id: "chk", name: "Joint Checking ...0101", latest_balance: 8_500 }),
      acct({ id: "brk", name: "Joint Brokerage", kind: "brokerage", latest_balance: 48_000 }),
    ];
    const rows = comparisonRows([
      { id: "a", name: "A", inputs: inputs(), draws: { chk: 5_000, brk: 10_000 } },
      { id: "b", name: "B", inputs: inputs({ price: 400_000 }), draws: {} },
    ], accounts, 18_000);
    expect(rows).toHaveLength(2);
    expect(rows[0].remainingLiquid).toBe(13_000);  // the brk draw is not liquid cash
    expect(rows[0].drawn).toBe(15_000);
    expect(rows[1].cashNeeded).toBe(100_000);
    expect(rows[1].remainingLiquid).toBe(18_000);
    // each row carries its plan's verdict, so the comparison can say which scenarios do not close
    expect(rows[0].funding.verdict).toBe("short");
    const full = comparisonRows([{ id: "c", name: "C", inputs: inputs({ price: 50_000, downPct: 20, closingCosts: 0,
      postMoveInCosts: 0 }), draws: { brk: 10_000 } }], accounts, 18_000);
    expect(full[0].funding.verdict).toBe("funded");
  });
  it("with no cash figure (no snapshot) the cash left is unknown, never a negative of the draws", () => {
    const accounts = [acct({ id: "chk", name: "Joint Checking ...0101", latest_balance: 8_500 })];
    const rows = comparisonRows([{ id: "a", name: "A", inputs: inputs(), draws: { chk: 5_000 } }], accounts, null);
    expect(rows[0].remainingLiquid).toBeNull();
    expect(rows[0].drawn).toBe(5_000);
  });
  it("with no account that can fund it, or none with a known balance, nothing is drawn: no '$0 funded'", () => {
    const none = comparisonRows([{ id: "a", name: "A", inputs: inputs(), draws: {} }], [], null);
    expect(none[0].drawn).toBeNull();
    const unknown = comparisonRows([{ id: "a", name: "A", inputs: inputs(), draws: {} }],
      [acct({ id: "chk", name: "Joint Checking ...0101", latest_balance: null, latest_balance_as_of: null })], null);
    expect(unknown[0].drawn).toBeNull();
    const zero = comparisonRows([{ id: "a", name: "A", inputs: inputs(), draws: {} }],
      [acct({ id: "chk", name: "Joint Checking ...0101", latest_balance: 8_500 })], 8_500);
    expect(zero[0].drawn, "a balance to draw from and no draw yet is a true $0").toBe(0);
  });
});

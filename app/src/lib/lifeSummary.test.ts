// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { Assumptions, AssetRow, MortgageBlock, Snapshot } from "../data/types";
import { belowZeroSnapshot, cfmFactory, firstWeekData, mortgageFactory, oneFullMonthData, snapFactory, txFactory,
  vocabAssumptions } from "./__fixtures__";
import { dataFirstDay, monthInProgress, nextMonth, type RunRateWindow, savingsRate } from "./agg";
import { categoryVocab } from "./categories";
import { fmtPct, fmtUSD0 } from "./format";
import { HAVE_EXPORTS, readExport, requireExports } from "../test/exports";
import {
  assetsBy, balanceHistory, balanceSheet, belowRateFloor, cardAccountIds, carriedBack, carriedBackLine, cashRunway, dailyInterest,
  debtLineLabel, debtClassesOwed, debtOverview, fmtLoanToValue, homeValue, homeValueSlider, incomeStreams, liquidCash, liquidCashRows,
  liquidity, loanToValue, marginBreakdown, missingLoanTerms, monthlyBurn, monthlyNet, mortgageBalance, netWorthDebt, netWorthDebtNote,
  mortgageSummary, nearestSnapshot, lastFullYear, netWorthDelta, plWindow, pnl, primaryHomeMonthly, printableRate, RATE_FLOOR,
  recentMonths, rowHolder, sankeyFlows, savedText, savingsKpis, savingsPace, savingsRateLabel, shortfallText, snapshotsCarrying,
} from "./lifeSummary";
import { MASK, setPrivate } from "./privacy";
import { cardHeadroom } from "./credit";

/** June 2026 in progress with a long history: the essentials average the six full months December to May */
const SIX: RunRateWindow = { from: "2025-12", to: "2026-05", months: 6 };

const snap = snapFactory();
const cfm = cfmFactory({
  invested: 1000, accounts_covered: ["a", "b", "c", "d", "e"],
});
/** manifest.generated_at: where the mortgage payoff counts from when the balance carries no date */
const STAMP = "2030-06-10T09:00:00-05:00";

describe("nearestSnapshot / netWorthDelta", () => {
  const snaps = [
    snap({ snapshot_date: "2024-08-01", total: 1_700_000, mortgage_included: false }),
    snap({ snapshot_date: "2025-07-31", total: 2_050_000, mortgage_included: false }),
    snap({ snapshot_date: "2026-02-06", total: 1_950_000 }),
    snap({ snapshot_date: "2026-06-01", total: 2_020_000 }),
  ];
  it("picks the snapshot closest in time", () => {
    expect(nearestSnapshot(snaps, "2025-06-15")?.snapshot_date).toBe("2025-07-31");
    expect(nearestSnapshot(snaps, "2026-05-30")?.snapshot_date).toBe("2026-06-01");
  });
  it("delta compares to ~12 months back and flags the mortgage discontinuity", () => {
    const d = netWorthDelta(snaps)!;
    expect(d.prior?.snapshot_date).toBe("2025-07-31");
    expect(d.delta).toBe(-30_000);
    expect(d.mortgageCaveat).toBe(true);  // prior didn't subtract the mortgage
  });
  it("handles empty input", () => {
    expect(nearestSnapshot([], "2026-01-01")).toBeNull();
    expect(netWorthDelta([])).toBeNull();
  });
  it("with a single snapshot there is nothing to compare: no delta, never a change of 0", () => {
    expect(netWorthDelta([snap({ snapshot_date: "2026-06-01", total: 2_020_000 })])).toBeNull();
  });
  it("a prior total of 0 gives the change but no percentage (nothing to divide by)", () => {
    const d = netWorthDelta([snap({ snapshot_date: "2025-06-01", total: 0 }), snap({ snapshot_date: "2026-06-01", total: 5_000 })]);
    expect([d?.delta, d?.pct]).toEqual([5_000, null]);
  });
});

// A household with no snapshot yet (no account balance seen): every helper that reads the newest snapshot answers
// "nothing" for null - null, or an empty list - and never a figure of 0.
describe("the snapshot helpers with no snapshot", () => {
  const a = { projections: {}, net_worth: { include_credit_cards: false },
    mortgage: mortgageFactory({ balance: 280_000, balance_as_of: "2030-05-18" }) } as unknown as Assumptions;
  const vocab = categoryVocab(vocabAssumptions());
  const fuel = txFactory({ type: "purchase", category: "Transport", subcategory: "Fuel" })({ date: "2026-01-10", amount: -3600 });
  it("homeValue, liquidCashRows and liquidCash", () => {
    expect(homeValue(null)).toBeNull();
    expect(liquidCashRows(null)).toEqual([]);
    expect(liquidCash(null)).toBeNull();
  });
  it("netWorthDebt and debtOverview", () => {
    expect(netWorthDebt(a, null, true)).toBeNull();
    expect(debtOverview(a, null, 4_000)).toBeNull();
    expect(debtOverview(a, null, null, true, new Set(["card"]))).toBeNull();
  });
  it("cashRunway: no cash to divide, so no runway; the essentials are still the transactions'", () => {
    const r = cashRunway(vocab, [fuel], null, SIX);
    expect(r.cash).toBeNull();
    expect(r.months).toBeNull();
    expect(r.essential).toBeCloseTo(600);
    expect(r.window).toEqual(SIX);
  });
  it("control: the same rows with a snapshot have a runway", () => {
    const cur = snap({ rows: [{ account: "Chk", account_id: "chk", institution: "B", owner: "Joint", asset_class: "Cash",
      value: 3_000, liquid: true, as_of: "2026-06-01", as_of_assumed: false, needs_review: false, source: "bank_balance",
      manual_id: null, carried_back: false }] });
    expect(cashRunway(vocab, [fuel], cur, SIX).months).toBeCloseTo(5);
  });
  it("mortgageSummary reads no home value from a missing snapshot", () => {
    const m = mortgageSummary({ ...a, mortgage: mortgageFactory({ balance: 250_000, rate_pct: 3, monthly_payment: 2_000 }) },
      null, STAMP);
    expect([m?.homeValue, m?.equity, m?.ltv]).toEqual([null, null, null]);
  });
});

describe("recentMonths / incomeStreams / monthlyBurn", () => {
  const cashflow = [
    cfm({ month: "2023-05", spend: 99999, in_coverage: false }),   // pre-coverage, ignored
    cfm({ month: "2025-01", spend: 10000, income_by_subcategory: { "Salary - Alex": 15000, "Salary - Sam": 5000 } }),
    cfm({ month: "2025-02", spend: 12000, income_by_subcategory: { "Salary - Alex": 15000, Interest: 50 } }),
    cfm({ month: "2025-03", spend: 14000, income_by_subcategory: { "Salary - Alex": 15000 } }),
    cfm({ month: "2025-04", spend: 500 }),                 // the month in progress, dropped
  ];
  it("drops pre-coverage and the month in progress", () => {
    const ms = recentMonths(cashflow, "2025-04", 12);
    expect(ms.map((m) => m.month)).toEqual(["2025-01", "2025-02", "2025-03"]);
  });
  it("a month a row was dated ahead into moves no window", () => {
    const ahead = [...cashflow, cfm({ month: "2025-05", spend: 90_000, income_by_subcategory: { Bonus: 50_000 } })];
    expect(recentMonths(ahead, "2025-04", 12)).toEqual(recentMonths(cashflow, "2025-04", 12));
    expect(incomeStreams(ahead, "2025-04", 3, 4)).toEqual(incomeStreams(cashflow, "2025-04", 3, 4));
    expect(monthlyBurn(ahead, "2025-04", 3)).toBe(monthlyBurn(cashflow, "2025-04", 3));
  });
  it("averages income streams over full months, sorted desc", () => {
    const s = incomeStreams(cashflow, "2025-04", 3, 4);
    expect(s[0]).toEqual({ name: "Salary - Alex", monthly: 15000 });
    expect(s[1].name).toBe("Salary - Sam");
    expect(s[1].monthly).toBeCloseTo(5000 / 3);
  });
  it("burn averages spend over the window", () => {
    expect(monthlyBurn(cashflow, "2025-04", 3)).toBe(12000);
  });
  it("burn is null, not $0, with no full month", () => {
    expect(monthlyBurn([], null)).toBeNull();
    expect(monthlyBurn([cfm({ month: "2025-04", spend: 500 })], "2025-04")).toBeNull();   // only the month in progress
  });
  it("divides a newly-started stream by its active months, not the whole window", () => {
    // Salary - Sam first appears in month 2 of a 3-month window: the leading
    // $0 month (before the job existed) must not drag the run-rate down.
    const cf = [
      cfm({ month: "2025-01", income_by_subcategory: { "Salary - Alex": 15000 } }),
      cfm({ month: "2025-02", income_by_subcategory: { "Salary - Alex": 15000, "Salary - Sam": 8000 } }),
      cfm({ month: "2025-03", income_by_subcategory: { "Salary - Alex": 15000, "Salary - Sam": 8000 } }),
      cfm({ month: "2025-04", spend: 1 }),  // the month in progress, dropped
    ];
    const started = incomeStreams(cf, "2025-04", 3, 4).find((x) => x.name === "Salary - Sam")!;
    expect(started.monthly).toBe(8000);   // 16000 / 2 active months, not / 3
  });
  it("merges the small-stream fold into a genuine 'Other' stream — never two rows", () => {
    const cf = [
      cfm({ month: "2025-01", income_by_subcategory: {
        A: 9000, B: 8000, C: 7000, Other: 6000, D: 100, E: 50 } }),
      cfm({ month: "2025-02", spend: 1 }),  // the month in progress, dropped
    ];
    const s = incomeStreams(cf, "2025-02", 3, 4);
    expect(s.filter((x) => x.name === "Other")).toHaveLength(1);
    expect(s.find((x) => x.name === "Other")!.monthly).toBe(6150);  // 6000 + 100 + 50
  });
});

describe("savingsPace: income less spending a month, the planning pages' one pace", () => {
  const months = (n: number) => Array.from({ length: n }, (_, i) => {
    const month = `2025-${String(i + 1).padStart(2, "0")}`;
    return cfm({ month, income: 10_000, spend: 7_000 + 100 * i });
  });
  it("over the twelve full months once there are that many, and only those", () => {
    const cf = [cfm({ month: "2024-12", income: 99_999, spend: 0 }), ...months(12), cfm({ month: "2026-01", income: 0, spend: 9_999 })];
    expect(savingsPace(cf, "2026-01", "2024-12-01")).toEqual({ monthly: 10_000 - (7_000 + 100 * 5.5), months: 12 });
  });
  it("over fewer, from as many full months as there are, and says how many: never a year's label over three", () => {
    expect(savingsPace([...months(3), cfm({ month: "2025-04", income: 0, spend: 50 })], "2025-04", "2025-01-01"))
      .toEqual({ monthly: 10_000 - 7_100, months: 3 });
  });
  it("never reaches back before the coverage floor", () => {
    const cf = [cfm({ month: "2024-11", income: 0, spend: 50_000, in_coverage: false }), ...months(4)];
    expect(savingsPace(cf, "2025-04", "2024-11-03")?.months).toBe(3);
    expect(savingsPace(cf, "2025-04", "2024-11-03")?.monthly).toBe(10_000 - 7_100);
  });
  it("leaves out a first month the data covers in part: from the 12th, three months in coverage are two full ones", () => {
    expect(savingsPace([...months(3), cfm({ month: "2025-04", income: 0, spend: 50 })], "2025-04", "2025-01-12")).toBeNull();
    expect(savingsPace([...months(4), cfm({ month: "2025-05", income: 0, spend: 50 })], "2025-05", "2025-01-12"))
      .toEqual({ monthly: 10_000 - 7_200, months: 3 });
  });
  it("unknown below three full months: a first week, one full month, two", () => {
    for (const d of [firstWeekData(), oneFullMonthData()]) {
      expect(savingsPace(d.cashflow, monthInProgress(d), dataFirstDay(d.transactions))).toBeNull();
    }
    expect(savingsPace([...months(2), cfm({ month: "2025-03" })], "2025-03", "2025-01-01")).toBeNull();
  });
});

/** assumptions with the demo loan's block: a 250,000 balance from the bank feed and the terms `over` pins */
const withLoan = (over: Partial<MortgageBlock> = {}): Assumptions => ({
  projections: {},
  mortgage: mortgageFactory({ balance: 250_000, balance_as_of: "2030-05-18", rate_pct: 6.5, monthly_payment: 2_000,
    monthly_escrow: 450, ...over }),
}) as unknown as Assumptions;

/** a House-class row of the snapshot: a home as accounts.yaml manual_entries states it */
const houseRow = (value: number, over: Partial<AssetRow> = {}): AssetRow => ({
  account: "Sample Home", account_id: null, institution: "Self", owner: "Joint", asset_class: "House", value,
  liquid: false, as_of: "2030-01-31", as_of_assumed: false, needs_review: false, source: "stated",
  manual_id: "sample-home", carried_back: false, ...over });
/** the current snapshot the summary reads the home's value from: one home at 1,000,000 */
const HOME = snap({ rows: [houseRow(1_000_000)] });
/** the mortgage summary on HOME's snapshot (`cur` to read another) */
const summary = (a: Assumptions, generatedAt: string, cur: Pick<Snapshot, "rows"> | null = HOME) =>
  mortgageSummary(a, cur, generatedAt);

describe("mortgageSummary", () => {
  const a = withLoan();
  it("amortizes to a plausible payoff and computes equity/LTV", () => {
    const m = summary(a, STAMP)!;
    expect(m.payoffYear).toBeGreaterThan(2040);
    expect(m.payoffYear).toBeLessThan(2055);
    expect(m.equity).toBe(1_000_000 - 250_000);
    expect(m.ltv).toBeCloseTo(0.25, 6);
    expect(m.dailyInterest).toBeCloseTo(250_000 * 0.065 / 365, 6);
    expect([m.balance, m.ratePct, m.monthlyPI, m.monthlyEscrow, m.asOf]).toEqual([250_000, 6.5, 2_000, 450, "2030-05-18"]);
  });
  it("returns null when the balance is missing, and without a mortgage account", () => {
    expect(summary(withLoan({ balance: null, balance_as_of: null }), STAMP)).toBeNull();
    expect(summary({ ...a, mortgage: null }, STAMP)).toBeNull();
  });
  it("returns null without a rate or without a monthly payment: no estimate stands in for either", () => {
    expect(summary(withLoan({ rate_pct: null }), STAMP)).toBeNull();
    expect(summary(withLoan({ monthly_payment: null }), STAMP)).toBeNull();
  });
  it("returns null for a paid-off loan and for a payment that does not cover a month's interest", () => {
    expect(summary(withLoan({ balance: 0 }), STAMP)).toBeNull();
    expect(summary(withLoan({ monthly_payment: 1_000 }), STAMP), "6.5% of 250,000 is about 1,354 a month")
      .toBeNull();
  });
  it("an escrow the loan terms do not state (or state as zero) is null, never a fabricated 0", () => {
    expect(summary(withLoan({ monthly_escrow: null }), STAMP)!.monthlyEscrow).toBeNull();
    expect(summary(withLoan({ monthly_escrow: 0 }), STAMP)!.monthlyEscrow).toBeNull();
  });
  it("the payoff counts from the balance's month; an undated balance counts from the ingest stamp's month", () => {
    const dated = summary(a, STAMP)!;
    expect(summary(a, "2031-06-10T09:00:00-05:00")!.payoffYear, "a dated balance ignores the stamp")
      .toBe(dated.payoffYear);
    const undated = withLoan({ balance_as_of: null });
    const fromStamp = summary(undated, STAMP)!;
    expect(fromStamp.asOf).toBeNull();
    // the same loan with its balance dated the stamp's month pays off in the same year
    expect(fromStamp.payoffYear).toBe(summary(withLoan({ balance_as_of: "2030-06-01" }), STAMP)!.payoffYear);
    // a stamp a year later moves the payoff a year later: the stamp, not a fixed year, is the start
    expect(summary(undated, "2031-06-10T09:00:00-05:00")!.payoffYear).toBe(fromStamp.payoffYear + 1);
  });
});

// The home's value is one figure: the House-class rows of the snapshot a page shows. No assumption stands in for it.
describe("homeValue - the snapshot's House-class rows", () => {
  const cash = { ...houseRow(7_000), account: "Sample Cash", asset_class: "Cash", liquid: true };
  it("is the one House row's value", () => {
    expect(homeValue(snap({ rows: [houseRow(500_000), cash] }))).toBe(500_000);
  });
  it("sums two House rows, in whole cents", () => {
    expect(homeValue(snap({ rows: [houseRow(500_000.1), cash, houseRow(250_000.2, { account: "Sample Cabin" })] })))
      .toBe(750_000.3);
  });
  it("is null - never 0 - when the snapshot holds no House row", () => {
    expect(homeValue(snap({ rows: [cash] }))).toBeNull();
    expect(homeValue(snap({ rows: [] }))).toBeNull();
  });
  it("a House row of 0 is a value, 0, not 'none'", () => {
    expect(homeValue(snap({ rows: [houseRow(0)] }))).toBe(0);
  });
  it("on a rewound view it is that snapshot's own House rows", () => {
    const older = snap({ snapshot_date: "2030-01-31", rows: [houseRow(480_000)] });
    expect([homeValue(older), homeValue(snap({ rows: [houseRow(500_000)] }))]).toEqual([480_000, 500_000]);
  });
});

describe("equity and loan-to-value from the House rows", () => {
  const a = withLoan();
  it("equity is the House rows less the balance and loan-to-value the balance over them", () => {
    const m = summary(a, STAMP, snap({ rows: [houseRow(500_000)] }))!;
    expect([m.homeValue, m.equity]).toEqual([500_000, 500_000 - 250_000]);
    expect(m.ltv).toBeCloseTo(0.5, 9);
  });
  it("without a House row, or without a snapshot, the home value, equity and loan-to-value are null, never a stand-in", () => {
    for (const cur of [snap({ rows: [] }), null, undefined]) {
      const m = mortgageSummary(a, cur, STAMP)!;
      expect([m.homeValue, m.equity, m.ltv]).toEqual([null, null, null]);
      expect(m.balance, "the rest of the summary is unchanged").toBe(250_000);
    }
  });
  it("a home stated at 0 has an equity (what is owed, negative) but no loan-to-value", () => {
    const m = summary(a, STAMP, snap({ rows: [houseRow(0)] }))!;
    expect([m.homeValue, m.equity, m.ltv]).toEqual([0, -250_000, null]);
  });
  it("loanToValue divides only by a positive home value", () => {
    expect(loanToValue(250_000, 500_000)).toBe(0.5);
    expect([loanToValue(250_000, null), loanToValue(250_000, 0), loanToValue(250_000, -1)]).toEqual([null, null, null]);
  });
  it("loan-to-value prints at one precision on every page, and '—' when it is unknown", () => {
    // 419,452.29 over 640,000 is 65.539…%: one page printed "66%", the other "65.5%"
    expect(fmtLoanToValue(loanToValue(419_452.29, 640_000))).toBe("65.5%");
    expect(fmtLoanToValue(loanToValue(250_000, 500_000))).toBe("50.0%");
    expect(fmtLoanToValue(loanToValue(250_000, null))).toBe("—");
  });
});

// Planning -> House's slider is sized from the household's own home: half to twice it, never a fixed dollar range.
describe("homeValueSlider", () => {
  const ok = (v: number) => {
    const r = homeValueSlider(v);
    expect(r.min < r.max, `${v}: a non-empty range`).toBe(true);
    expect(r.min <= v && v <= r.max, `${v}: the value is inside`).toBe(true);
    expect(r.step > 0 && r.step <= r.max - r.min, `${v}: a step that fits`).toBe(true);
    expect(Object.is(r.min, -0) || Object.is(r.max, -0), `${v}: no negative zero`).toBe(false);
    return r;
  };
  it("runs from half the value to twice it, each end on the step, the step kept while it is a tenth of the range or less", () => {
    expect(ok(500_000)).toEqual({ min: 250_000, max: 1_000_000, step: 25_000 });
    expect(ok(333_333)).toEqual({ min: 150_000, max: 675_000, step: 25_000 });     // 166,666.5 down, 666,666 up
  });
  it("a step over a tenth of the range becomes the largest 1, 2 or 5 times a power of ten that is not", () => {
    expect(ok(100_000)).toEqual({ min: 50_000, max: 200_000, step: 10_000 });       // a tenth is 15,000
    expect(ok(20_000)).toEqual({ min: 10_000, max: 40_000, step: 2_000 });          // a tenth is 3,000
    expect(ok(1)).toEqual({ min: 0, max: 2, step: 1 });                             // never under a dollar
  });
  it("is never empty or inverted: a value of 0, a tiny one, a huge one, a negative one", () => {
    expect(ok(0)).toEqual({ min: 0, max: 250_000, step: 25_000 });
    ok(0.4);
    expect(ok(5_000_000_000)).toEqual({ min: 2_500_000_000, max: 10_000_000_000, step: 25_000 });
    ok(-1);
    ok(-300_000);
  });
});

describe("mortgage facts shared by Liabilities, Planning -> House and Second House", () => {
  const bare = (over: Partial<MortgageBlock>) => withLoan({ rate_pct: null, monthly_payment: null, monthly_escrow: null,
    ...over });

  it("primaryHomeMonthly: the loan terms' payment + escrow when both are stated", () => {
    expect(primaryHomeMonthly(bare({ monthly_payment: 2_000, monthly_escrow: 450 }))).toBe(2_000 + 450);
  });
  it("primaryHomeMonthly: the payment alone when the loan terms state no (or a zero/negative) escrow", () => {
    expect(primaryHomeMonthly(bare({ monthly_payment: 2_000 }))).toBe(2_000);
    expect(primaryHomeMonthly(bare({ monthly_payment: 2_000, monthly_escrow: 0 }))).toBe(2_000);
    expect(primaryHomeMonthly(bare({ monthly_payment: 2_000, monthly_escrow: -5 }))).toBe(2_000);
  });
  it("primaryHomeMonthly: an unknown payment is null even when an escrow is known - never an escrow-only figure", () => {
    expect(primaryHomeMonthly(bare({ monthly_escrow: 450 }))).toBeNull();
    expect(primaryHomeMonthly(bare({}))).toBeNull();
    expect(primaryHomeMonthly({ ...bare({}), mortgage: null }), "no mortgage account").toBeNull();
  });
  it("primaryHomeMonthly: still answers when the balance or rate is unknown (the summary would be null)", () => {
    const a = bare({ monthly_payment: 2_000, balance: null, rate_pct: null });
    expect(summary(a, STAMP)).toBeNull();
    expect(primaryHomeMonthly(a)).toBe(2_000);
  });

  it("missingLoanTerms names each term the figures need and the loan terms lack, with what it shows", () => {
    expect(missingLoanTerms(withLoan().mortgage!)).toEqual([]);
    expect(missingLoanTerms(withLoan({ rate_pct: null }).mortgage!)).toEqual([
      { key: "rate", shows: "the interest cost and the payoff year" }]);
    expect(missingLoanTerms(withLoan({ monthly_payment: null }).mortgage!)).toEqual([
      { key: "monthly_payment", shows: "the payoff year" }]);
    expect(missingLoanTerms(withLoan({ monthly_escrow: null }).mortgage!)).toEqual([
      { key: "monthly_escrow", shows: "the monthly cost with taxes and insurance" }]);
    // the payoff year needs both: with both missing, each sentence says so
    expect(missingLoanTerms(bare({}).mortgage!).map((t) => [t.key, t.shows])).toEqual([
      ["rate", "the interest cost"], ["monthly_payment", "the payoff year, with the rate"],
      ["monthly_escrow", "the monthly cost with taxes and insurance"]]);
  });
  it("missingLoanTerms never asks for a term that is stated as 0", () => {
    expect(missingLoanTerms(withLoan({ rate_pct: 0, monthly_escrow: 0 }).mortgage!)).toEqual([]);
  });
  it("dailyInterest is the balance times the yearly rate over 365", () => {
    expect(dailyInterest(250_000, 6.5)).toBeCloseTo(250_000 * 0.065 / 365, 9);
    expect(dailyInterest(250_000, 0)).toBe(0);
  });
});

// The mortgage balance is printed by the Liabilities card and Planning -> House's KPIs/equity/LTV/payoff, and by
// Balances / Home's Debt on the latest snapshot. It has one source, the bank feed: assumptions.json `mortgage.balance`,
// the loan account's own row in the current snapshot.
describe("mortgageBalance - one balance, the bank feed's", () => {
  it("is the block's balance and its date, and the summary reads the same figure", () => {
    const a = withLoan({ balance: 280_000, balance_as_of: "2030-10-03" });
    expect(mortgageBalance(a)).toEqual({ balance: 280_000, asOf: "2030-10-03" });
    const m = summary(a, STAMP)!;
    expect([m.balance, m.asOf, m.equity]).toEqual([280_000, "2030-10-03", 1_000_000 - 280_000]);
    expect(m.dailyInterest).toBe(dailyInterest(280_000, 6.5));
  });
  it("a zero balance is a paid-off loan: a known 0, not missing data", () => {
    expect(mortgageBalance(withLoan({ balance: 0, balance_as_of: "2030-10-03" }))).toEqual({ balance: 0, asOf: "2030-10-03" });
  });
  it("is null - never 0 - before the bank feed reports the account, and without a mortgage account", () => {
    expect(mortgageBalance(withLoan({ balance: null, balance_as_of: null }))).toBeNull();
    expect(mortgageBalance({ ...withLoan(), mortgage: null })).toBeNull();
  });
});

// No component speaks of the second balance source the dashboard once had: the balance is the bank feed's and the terms
// are the account's loan terms. The word is assembled here so this file does not hold it either.
describe("the mortgage vocabulary", () => {
  const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
  const files = (dir: string): string[] => readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? files(join(dir, e.name))
    : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [join(dir, e.name)] : []);
  it("no source file under app/src names the removed second balance source", () => {
    const word = new RegExp(["ser", "vicer"].join(""), "i");
    const all = files(SRC);
    expect(all.length).toBeGreaterThan(50);
    expect(all.filter((f) => word.test(readFileSync(f, "utf8"))).map((f) => f.slice(SRC.length + 1))).toEqual([]);
  });
});

describe("liquid cash and the debt overview", () => {
  const cashRow = (over: Partial<AssetRow>): AssetRow => ({
    account: "Chk", account_id: "chk", institution: "B", owner: "Joint", asset_class: "Cash", value: 100, liquid: true,
    as_of: "2026-10-03", as_of_assumed: false, needs_review: false, source: "bank_balance", manual_id: null,
    carried_back: false, ...over });
  it("liquidCash is the liquid Cash-class rows only - escrow (Cash but not liquid) and other classes are out", () => {
    const cur = snap({ rows: [cashRow({ value: 100 }), cashRow({ account_id: null, value: 50, source: "stated" }),
      cashRow({ value: 900, liquid: false }), cashRow({ asset_class: "Stocks/ETF", value: 7_000 })] });
    expect(liquidCashRows(cur)).toHaveLength(2);
    expect(liquidCash(cur)).toBe(150);
  });
  const a = { projections: {},
    mortgage: mortgageFactory({ balance: 280_000, balance_as_of: "2030-05-18" }) } as unknown as Assumptions;
  /** a snapshot whose rows are one per class at the given values */
  const classes = (by: Record<string, number>) => snap({ by_class: by,
    rows: Object.entries(by).map(([asset_class, value]) => cashRow({ account: asset_class, account_id: asset_class, asset_class, value,
      liquid: asset_class === "Cash" })) });
  it("debtClassesOwed names only the debt classes the snapshot actually owes (a row below zero)", () => {
    expect(debtClassesOwed(classes({ Mortgage: -300_000, Margin: -10_000, Cash: 5 }))).toEqual(["Mortgage", "Margin"]);
    expect(debtClassesOwed(classes({ Mortgage: -300_000, Margin: 0, "Other Debt": 0 }))).toEqual(["Mortgage"]);
    expect(debtClassesOwed(classes({ Cash: 5 }))).toEqual([]);
    expect(debtClassesOwed(classes({ Cash: -5 })), "an overdrawn account is owed, but under no debt class").toEqual([]);
  });
  it("totals the mortgage, cards, margin and any other debt class, each from its source", () => {
    const d = debtOverview(a, classes({ Margin: -10_000, "Other Debt": -2_000 }), 4_000);
    expect(d.total).toBe(280_000 + 4_000 + 10_000 + 2_000);
    expect(d.parts).toEqual(["mortgage", "credit cards", "margin", "other debt"]);
    expect(d.unknown).toEqual([]);
  });
  it("cards nobody reported are named as missing and left out - never counted as $0; a zero margin is not a part", () => {
    const d = debtOverview(a, snap({ by_class: {} }), null);
    expect(d.total).toBe(280_000);
    expect(d.parts).toEqual(["mortgage"]);
    expect(d.unknown).toEqual(["cards"]);
    expect(debtOverview(a, snap({ by_class: {} }), 0).parts).toEqual(["mortgage", "credit cards"]);   // a real $0 of card debt is known
  });
  it("a mortgage account the bank feed has not reported is named as missing", () => {
    const none = { ...a, mortgage: mortgageFactory() } as unknown as Assumptions;
    const d = debtOverview(none, classes({ Margin: -10_000 }), 1_000);
    expect(d.mortgage).toBeNull();
    expect(d.unknown).toEqual(["mortgage"]);
    expect(d.total).toBe(11_000);
  });
  it("a household with no mortgage account has no mortgage to be missing", () => {
    const d = debtOverview({ ...a, mortgage: null }, classes({ Margin: -10_000 }), 1_000);
    expect([d.mortgage, d.unknown, d.parts, d.total]).toEqual([null, [], ["credit cards", "margin"], 11_000]);
  });
  it("a paid-off mortgage is a known part at 0", () => {
    const d = debtOverview({ ...a, mortgage: mortgageFactory({ balance: 0, balance_as_of: "2030-05-18" }) },
      snap({ by_class: {} }), 1_000);
    expect([d.mortgage, d.unknown, d.parts, d.total]).toEqual([{ balance: 0, asOf: "2030-05-18" }, [], ["mortgage", "credit cards"], 1_000]);
  });
});

// Three "debt" figures used to disagree unexplained: Home's tile and Balances' "Total liabilities" are the DEBT IN NET WORTH
// (mortgage + margin: a snapshot holds no card balance), Accounts -> Liabilities' "Total debt" adds the card balances. The
// mortgage in all three is the one mortgageBalance, so the pages cannot show different mortgages.
describe("netWorthDebt - the debt in net worth, mortgage through mortgageBalance", () => {
  const a = { projections: {},
    mortgage: mortgageFactory({ balance: 280_000, balance_as_of: "2026-10-03" }) } as unknown as Assumptions;
  const row = (over: Partial<AssetRow> = {}): AssetRow => ({
    account: "Mortgage", account_id: "demo_mortgage_0707", institution: "S", owner: "Joint", asset_class: "Mortgage", value: -280_000,
    liquid: false, as_of: "2026-10-03", as_of_assumed: false, needs_review: false, source: "simplefin_balance",
    manual_id: null, carried_back: false, ...over });
  const marginRow = (value: number) => row({ account: "Margin", account_id: "demo_brokerage_0505", asset_class: "Margin", value });
  const cur = (over: Partial<ReturnType<typeof snap>> = {}) =>
    snap({ by_class: { Mortgage: -280_000, Margin: -10_000, Cash: 5 },
      rows: [row(), marginRow(-10_000), row({ account: "Cash", account_id: "chk", asset_class: "Cash", value: 5, liquid: true })],
      ...over });

  it("is the snapshot's debt classes: mortgage + margin, named, with the one mortgage balance", () => {
    const d = netWorthDebt(a, cur(), true);
    expect(d.owed).toBe(290_000);
    expect(d.mortgage).toBe(280_000);
    expect(d.classes).toEqual(["mortgage", "margin"]);
  });
  it("is the same mortgage the Liabilities total uses: total debt = net-worth debt + the card balances", () => {
    const c = cur();
    const total = debtOverview(a, c, 4_000).total;
    expect(total).toBe(netWorthDebt(a, c, true).owed + 4_000);
    expect(debtOverview(a, c, null).total).toBe(netWorthDebt(a, c, true).owed);
  });
  it("a rewound snapshot keeps its own row - the block's balance is the current snapshot's", () => {
    const old = cur({ snapshot_date: "2026-02-06", rows: [row({ as_of: "2026-02-06", value: -305_000 })], by_class: { Mortgage: -305_000 } });
    const d = netWorthDebt(a, old, false);
    expect(d.mortgage).toBe(305_000);
    expect(netWorthDebt(a, old, true).mortgage, "latest=true reads the block's balance").toBe(280_000);
  });
  it("a snapshot that holds no mortgage (before the mortgage data) owes none, whatever the block says", () => {
    const pre = snap({ by_class: { Margin: -10_000 }, rows: [marginRow(-10_000)] });
    const d = netWorthDebt(a, pre, true);
    expect(d.mortgage).toBe(0);
    expect(d.owed).toBe(10_000);
    expect(d.classes).toEqual(["margin"]);
  });
  it("the note every debt tile prints: the classes, and that cards are not in it", () => {
    expect(netWorthDebtNote({ classes: ["mortgage", "margin"] })).toBe("mortgage + margin · credit cards not included");
    expect(netWorthDebtNote({ classes: [] })).toBe("no debt in this snapshot · credit cards not included");
  });
  describe("credit cards in the snapshot (include_credit_cards)", () => {
    const debtRow = (over: Partial<AssetRow>): AssetRow => ({
      account: "Debt", account_id: "demo_card_0000", institution: "Sample Bank", owner: "Alex", asset_class: "Other Debt",
      value: -1_000, liquid: false, as_of: "2030-06-01", as_of_assumed: false, needs_review: false,
      source: "simplefin_balance", manual_id: null, carried_back: false, ...over });
    const cardIds = new Set(["demo_card_0000", "demo_card_0001"]);
    // by class: the cards (-1000 + 150) and the loan (-5000) are all Other Debt
    const withCards = snap({
      by_class: { "Other Debt": -5_850 },
      rows: [debtRow({ value: -1_000 }), debtRow({ account_id: "demo_card_0001", value: 150 }),
        debtRow({ account_id: "demo_loan_0000", value: -5_000 })],
    });
    const cardSnap = withCards;
    it("on: cards are what the card rows below zero owe, other is the loan only, the history figure is ignored, total counts cards once", () => {
      // by sign: the card in credit (+150) is an asset, as on the balance sheet, so it offsets no other card's debt
      const d = debtOverview(a, cardSnap, 99_999, true, cardIds);
      expect(d.cards).toBe(1_000);
      expect(d.other).toBe(5_000);
      expect(d.total).toBe(d.mortgage!.balance + 1_000 + 5_000);
      expect(d.parts).toEqual(["mortgage", "credit cards", "other debt"]);
      expect(d.unknown).toEqual([]);
      expect(debtOverview(a, cardSnap, 1, true, cardIds)).toEqual(d);
      expect(debtOverview(a, cardSnap, null, true, cardIds)).toEqual(d);
    });
    it("on: a card in credit owes nothing (it is an asset), and the loan is not a card", () => {
      const credit = snap({ by_class: { "Other Debt": -4_900 },
        rows: [debtRow({ value: 100 }), debtRow({ account_id: "demo_loan_0000", value: -5_000 })] });
      const d = debtOverview(a, credit, null, true, cardIds);
      expect(d.cards).toBe(0);
      expect(d.other).toBe(5_000);
      expect(d.total).toBe(d.mortgage!.balance + 5_000);
    });
    it("on: card accounts without a row are unknown, named, and left out of the total", () => {
      const d = debtOverview(a, snap({ by_class: { "Other Debt": -5_000 }, rows: [debtRow({ account_id: "demo_loan_0000", value: -5_000 })] }),
        4_000, true, cardIds);
      expect(d.cards).toBeNull();
      expect(d.unknown).toEqual(["cards"]);
      expect(d.parts).toEqual(["mortgage", "other debt"]);
      expect(d.total).toBe(d.mortgage!.balance + 5_000);
    });
    it("off: the history figure is the cards and a card row (which no snapshot holds) stays in other, by sign", () => {
      const d = debtOverview(a, cardSnap, 4_000, false, cardIds);
      expect(d.cards).toBe(4_000);
      expect(d.other).toBe(6_000);
      expect(d.total).toBe(d.mortgage!.balance + 4_000 + 6_000);
      expect(debtOverview(a, cardSnap, 4_000)).toEqual(d);
    });
    it("netWorthDebt says whether the snapshot holds a card row, and counts the rows below zero once", () => {
      expect(netWorthDebt(a, cardSnap, true, cardIds)).toMatchObject({ owed: 6_000, cardRows: true });
      expect(netWorthDebt(a, cardSnap, true).cardRows).toBe(false);
      expect(netWorthDebt(a, snap({ by_class: { "Other Debt": -5_000 }, rows: [debtRow({ account_id: "demo_loan_0000", value: -5_000 })] }),
        true, cardIds).cardRows).toBe(false);
    });
    it("debtLineLabel: a debt class's line is credit cards when it holds only cards, says so when mixed, else is the class", () => {
      const onlyCards = snap({ by_class: { "Other Debt": -1_000 }, rows: [debtRow({ value: -1_000 })] });
      expect(debtLineLabel(onlyCards, "Other Debt", cardIds)).toBe("Credit cards");
      expect(debtLineLabel(withCards, "Other Debt", cardIds), "the cards and the loan").toBe("Other debt, credit cards included");
      const loanOnly = snap({ by_class: { "Other Debt": -5_000 }, rows: [debtRow({ account_id: "demo_loan_0000", value: -5_000 })] });
      expect(debtLineLabel(loanOnly, "Other Debt", cardIds)).toBe("Other debt");
      expect(debtLineLabel(onlyCards, "Other Debt", new Set()), "no card account: no card").toBe("Other debt");
      // a card in credit is an asset line of its own, so a class whose owing rows are all cards is still the cards'
      expect(debtLineLabel(snap({ by_class: { "Other Debt": -850 }, rows: [debtRow({ value: -1_000 }),
        debtRow({ account_id: "demo_card_0001", value: 150 })] }), "Other Debt", cardIds)).toBe("Credit cards");
      expect(debtLineLabel(withCards, "Mortgage", cardIds)).toBe("Mortgage");
      expect(debtLineLabel(withCards, "Joint Checking (overdrawn)", cardIds), "a line that is no class keeps its name")
        .toBe("Joint Checking (overdrawn)");
      // what the debt tiles print names the same lines, and says the cards once
      expect(netWorthDebt(a, onlyCards, true, cardIds).classes).toEqual(["credit cards"]);
      expect(netWorthDebtNote(netWorthDebt(a, onlyCards, true, cardIds), true)).toBe("credit cards");
      expect(netWorthDebtNote(netWorthDebt(a, withCards, true, cardIds), true)).toBe("other debt, credit cards included");
      expect(netWorthDebtNote(netWorthDebt(a, loanOnly, true, cardIds), true)).toBe("other debt");
    });
    it("cardAccountIds picks the accounts of kind credit-card, not a class", () => {
      expect([...cardAccountIds([{ id: "demo_card_0000", kind: "credit-card" }, { id: "demo_loan_0000", kind: "loan" },
        { id: "demo_checking_0000", kind: "checking" }])]).toEqual(["demo_card_0000"]);
    });
    it("on: a card declaring the Margin class is a card by account and is not also margin", () => {
      const c = snap({ by_class: { Margin: -2_500 },
        rows: [debtRow({ asset_class: "Margin", value: -500 }), debtRow({ account_id: "demo_margin_0000", asset_class: "Margin", value: -2_000 })] });
      const d = debtOverview(a, c, null, true, cardIds);
      expect(d.cards).toBe(500);
      expect(d.margin).toBe(2_000);
      expect(d.other).toBe(0);
      expect(d.total).toBe(d.mortgage!.balance + 500 + 2_000);
    });
    it("on: a card in a debt class with nothing else in it leaves that class at zero", () => {
      const c = snap({ by_class: { Margin: -500 }, rows: [debtRow({ asset_class: "Margin", value: -500 })] });
      const d = debtOverview(a, c, null, true, cardIds);
      expect(d.cards).toBe(500);
      expect(d.margin).toBe(0);
      expect(d.parts).toEqual(["mortgage", "credit cards"]);
    });
    it("a debt-class row above zero is an asset on the sheet, named for its account, and the sheet sums to the total", () => {
      const c = snap({ total: 100_000 + 3_000, by_class: { Cash: 100_000, "Other Debt": 3_000 },
        rows: [debtRow({ account_id: "chk", asset_class: "Cash", value: 100_000, liquid: true }), debtRow({ value: 3_000 })] });
      const bs = balanceSheet(c);
      expect(bs.assets).toEqual([{ name: "Cash", value: 100_000 }, { name: "Debt (in credit)", value: 3_000 }]);
      expect(bs.liabilities).toEqual([]);
      expect(bs.totalAssets - bs.totalLiabilities).toBe(c.total);
    });
    it("netWorthDebt never owes a negative amount when a debt-class row is in credit", () => {
      const d = netWorthDebt(a, snap({ by_class: { "Other Debt": 3_000 }, rows: [debtRow({ value: 3_000 })] }), true, cardIds);
      expect(d.owed).toBe(0);
      expect(d.classes).toEqual([]);
    });
    it("the note: its wordings", () => {
      expect(netWorthDebtNote({ classes: ["mortgage", "other debt"] }, false)).toBe("mortgage + other debt · credit cards not included");
      expect(netWorthDebtNote({ classes: [] }, false)).toBe("no debt in this snapshot · credit cards not included");
      expect(netWorthDebtNote({ classes: ["mortgage", "other debt"], cardRows: true }, true)).toBe("mortgage + other debt · credit cards included");
      expect(netWorthDebtNote({ classes: ["mortgage"], cardRows: false }, true)).toBe("mortgage");
      expect(netWorthDebtNote({ classes: [], cardRows: false }, true)).toBe("no debt in this snapshot");
      // cards left out of net worth by a household with no credit-card account: there is no card to leave out
      expect(netWorthDebtNote({ classes: ["mortgage"] }, false, false)).toBe("mortgage");
      expect(netWorthDebtNote({ classes: [] }, false, false)).toBe("no debt in this snapshot");
      // cards counted in net worth by a household with no credit-card account: no card, never a card balance missing
      expect(netWorthDebtNote({ classes: ["mortgage"] }, true, false)).toBe("mortgage · no credit card");
      expect(netWorthDebtNote({ classes: [] }, true, false)).toBe("no debt in this snapshot · no credit card");
    });
    it("balanceSheet carries the cards owed in its Other Debt line, a card in credit as an asset, and sums to the total", () => {
      const c = snap({ total: 100_000 - 5_850, by_class: { Cash: 100_000, "Other Debt": -5_850 },
        rows: [...withCards.rows, debtRow({ account_id: "chk", asset_class: "Cash", value: 100_000, liquid: true })] });
      const bs = balanceSheet(c);
      expect(bs.liabilities).toEqual([{ name: "Other Debt", value: 6_000 }]);
      expect(bs.assets).toEqual([{ name: "Cash", value: 100_000 }, { name: "Debt (in credit)", value: 150 }]);
      expect(bs.net).toBe(c.total);
    });
    it("liquidCash ignores a card row", () => {
      expect(liquidCash(snap({ rows: [debtRow({ value: 300 })] }))).toBe(0);
    });
  });
  it("balanceSheet takes the one mortgage figure for the Mortgage line and leaves every other line alone", () => {
    const c = cur();
    expect(balanceSheet(c).liabilities.find((l) => l.name === "Mortgage")!.value).toBe(280_000);
    const over = balanceSheet(c, 300_000);
    expect(over.liabilities.find((l) => l.name === "Mortgage")!.value).toBe(300_000);
    expect(over.liabilities.find((l) => l.name === "Margin")!.value).toBe(10_000);
    expect(over.totalLiabilities).toBe(310_000);
  });
});

/** Liquid + Illiquid − Debts = net worth, Illiquid being the non-liquid asset rows (never net worth less the liquid part) */
function expectIdentity(s: Snapshot) {
  const l = liquidity(s);
  const cents = (v: number) => Math.round(v * 100);
  const nonLiquidAssets = s.rows.filter((r) => !r.liquid && r.value > 0).reduce((c, r) => c + cents(r.value), 0);
  expect(cents(l.illiquid), `${s.snapshot_date}: Illiquid is the non-liquid asset rows`).toBe(nonLiquidAssets);
  expect(cents(l.liquid) + cents(l.illiquid) - cents(l.debts), `${s.snapshot_date}: liquid + illiquid − debts`).toBe(cents(s.total));
  expect(l.liquid + l.illiquid, `${s.snapshot_date}: the two parts are the assets`).toBeCloseTo(s.assets_total, 2);
  expect(l.liquidShare === null || (l.liquidShare >= 0 && l.liquidShare <= 1), `${s.snapshot_date}: a share of assets`).toBe(true);
}

describe("liquidity: liquid, illiquid and debts, one split", () => {
  const r = (over: Partial<AssetRow>): AssetRow => ({ account: "Acct", account_id: "chk", institution: "Sample Bank",
    owner: "Joint", asset_class: "Cash", value: 100_000, liquid: true, as_of: "2026-06-01", as_of_assumed: false,
    needs_review: false, source: "bank_balance", manual_id: null, carried_back: false, ...over });
  it("debts larger than the illiquid assets: Illiquid is the car, never a negative remainder of net worth", () => {
    // 100,000 of cash, a 21,000 car, a 400,000 mortgage: net worth -279,000; net worth less the liquid part is -379,000
    const s = snap({ total: -279_000, assets_total: 121_000, liabilities_total: -400_000, liquid_total: 100_000,
      rows: [r({}), r({ account: "Car", account_id: null, asset_class: "Car", value: 21_000, liquid: false, manual_id: "car" }),
        r({ account: "Loan", account_id: "loan", asset_class: "Mortgage", value: -400_000, liquid: false })] });
    expect(liquidity(s)).toEqual({ liquid: 100_000, illiquid: 21_000, debts: 400_000, assets: 121_000,
      liquidShare: 100_000 / 121_000 });
    expectIdentity(s);
  });
  it("an overdrawn account is a debt, not a negative liquid asset", () => {
    const s = snap({ total: 5_000 - 300 - 200, assets_total: 5_000, liabilities_total: -500, liquid_total: 5_000 - 300,
      rows: [r({ value: 5_000 }), r({ account_id: "od", value: -300 }),
        r({ account: "Card", account_id: "card", asset_class: "Other Debt", value: -200, liquid: false })] });
    expect(liquidity(s)).toMatchObject({ liquid: 5_000, illiquid: 0, debts: 500 });
    expectIdentity(s);
  });
  it("no asset above zero: no share, never 0%; a net worth of 0 with assets still has one", () => {
    expect(liquidity(snap({ total: -500, assets_total: 0, liabilities_total: -500, liquid_total: 0 })).liquidShare).toBeNull();
    expect(liquidity(snap({ total: 0, assets_total: 500, liabilities_total: -500, liquid_total: 500 })).liquidShare).toBe(1);
  });
  it("assetsBy groups the rows above zero by class or owner, largest first: a debt is in no slice", () => {
    const s = snap({ rows: [r({ value: 300, owner: "Alex" }), r({ asset_class: "House", value: 900, liquid: false }),
      r({ asset_class: "Other Debt", value: -500, liquid: false })] });
    expect(assetsBy(s, "asset_class")).toEqual([{ name: "House", value: 900 }, { name: "Cash", value: 300 }]);
    expect(assetsBy(s, "owner")).toEqual([{ name: "Joint", value: 900 }, { name: "Alex", value: 300 }]);
  });
  it("rowHolder names a holder, or 'entered by hand' for a hand-stated value that names none", () => {
    expect(rowHolder(r({ manual_id: "x", institution: "" }))).toBe("entered by hand");
    expect(rowHolder(r({}))).toBe("Sample Bank");
  });
  it("marginBreakdown: a live margin account's positions and loan; an account held as one row has none", () => {
    const s = snap({ rows: [r({ account_id: "brk", asset_class: "Stocks/ETF", value: 5_600, source: "positions" }),
      r({ account_id: "brk", asset_class: "Margin", value: -600, liquid: false }), r({})] });
    expect(marginBreakdown(s, "brk")).toEqual({ positions: 5_600, loan: 600 });
    expect(marginBreakdown(s, "chk")).toBeNull();
    const cash = snap({ rows: [r({ account_id: "brk", asset_class: "Stocks/ETF", value: 5_600 }), r({ account_id: "brk", value: 400 })] });
    expect(marginBreakdown(cash, "brk"), "a cash remainder owes nothing").toBeNull();
    expect(marginBreakdown(null, "brk")).toBeNull();
  });
});

describe("hand-stated values carried back, each named", () => {
  const carried = (account: string, id: string, value: number, stated: string): AssetRow => ({ account, account_id: null,
    institution: "", owner: "Joint", asset_class: "Car", value, liquid: false, as_of: stated, as_of_assumed: false,
    needs_review: false, source: "stated", manual_id: id, carried_back: true });
  const sedan = carried("Sedan", "sedan", 21_000, "2026-03-26");
  const boat = carried("Boat", "boat", 9_500, "2026-01-10");
  /** a month end holding `rows`, then the current snapshot (the newest, no month end) */
  const history = (rows: AssetRow[]) => [snap({ rows }), snap({ rows: [] })];
  it("one per asset, earliest first-stated first, with how many month ends hold it carried", () => {
    const list = carriedBack([snap({ rows: [sedan, boat] }), snap({ rows: [sedan] }), snap({ rows: [{ ...sedan, carried_back: false }] })]);
    expect(list).toEqual([{ name: "Boat", value: 9_500, stated: "2026-01-10", snapshots: 1 },
      { name: "Sedan", value: 21_000, stated: "2026-03-26", snapshots: 2 }]);
    expect(snapshotsCarrying([snap({ rows: [sedan, boat] }), snap({ rows: [sedan] }), snap({ rows: [] })])).toBe(2);
    expect(carriedBack(history([]))).toEqual([]);
  });
  it("the newest snapshot is no month end: a value it holds carried back is neither counted nor described there", () => {
    // stated after the newest account balance, so the current snapshot holds it carried back as the month ends do
    expect(snapshotsCarrying([snap({ rows: [sedan] }), snap({ rows: [sedan] }), snap({ rows: [sedan] })])).toBe(2);
    expect(carriedBack([snap({ rows: [sedan] }), snap({ rows: [sedan] })]))
      .toEqual([{ name: "Sedan", value: 21_000, stated: "2026-03-26", snapshots: 1 }]);
    // carried in the current snapshot alone: no earlier month end holds it, so nothing to count or describe
    expect(snapshotsCarrying([snap({ rows: [] }), snap({ rows: [sedan] })])).toBe(0);
    expect(carriedBackLine(carriedBack([snap({ rows: [] }), snap({ rows: [sedan] })]))).toBeNull();
    expect(snapshotsCarrying([snap({ rows: [sedan] })])).toBe(0);
  });
  it("the line names each asset with its first stated amount and day, never one date for all", () => {
    expect(carriedBackLine(carriedBack(history([sedan]))))
      .toBe("Sedan is shown at its first stated amount ($21,000, stated Mar 26, 2026) in earlier month ends.");
    expect(carriedBackLine(carriedBack(history([sedan, boat]))))
      .toBe("Boat is shown at its first stated amount ($9,500, stated Jan 10, 2026) in earlier month ends; "
        + "Sedan is shown at its first stated amount ($21,000, stated Mar 26, 2026) in earlier month ends.");
    const more = (n: number) => Array.from({ length: n }, (_, i) =>
      carried(`Asset ${i}`, `a${i}`, 1_000, `2026-0${i + 1}-15`));
    expect(carriedBackLine(carriedBack(history(more(3)))))
      .toMatch(/; and 1 more hand-entered asset, at its first stated amount in month ends before it was first stated\.$/);
    expect(carriedBackLine(carriedBack(history(more(5)))))
      .toMatch(/^Asset 0 is shown .*; Asset 1 is shown .*; and 3 more hand-entered assets, each at its first stated amount in month ends before it was first stated\.$/);
    expect(carriedBackLine([])).toBeNull();
  });
  it("the amount is masked with every other figure in privacy mode", () => {
    setPrivate(true);
    try {
      expect(carriedBackLine(carriedBack(history([sedan])))).toBe(
        `Sedan is shown at its first stated amount (${MASK}, stated Mar 26, 2026) in earlier month ends.`);
    } finally {
      setPrivate(false);
    }
  });
});

// One balance sheet, by sign: every row above zero is an asset, every row below zero a liability, on every card
describe("one balance sheet by sign: an overdrawn account and a hand-stated value below zero", () => {
  const s = belowZeroSnapshot();
  const a = { projections: {}, mortgage: mortgageFactory({ account_id: "loan", balance: 250_000, balance_as_of: "2026-06-01" }) } as unknown as Assumptions;
  it("the balance sheet lists the rows below zero as liabilities, each account below zero on its own line", () => {
    const bs = balanceSheet(s, netWorthDebt(a, s, true).mortgage);
    expect(bs.assets).toEqual([{ name: "House", value: 400_000 }, { name: "Stocks/ETF", value: 10_000 }, { name: "Cash", value: 5_000 }]);
    expect(bs.liabilities).toEqual([{ name: "Mortgage", value: 250_000 }, { name: "Boat (below zero)", value: 2_000 },
      { name: "Joint Checking (overdrawn)", value: 300 }]);
    expect([bs.totalAssets, bs.totalLiabilities, bs.net]).toEqual([415_000, 252_300, 162_700]);
  });
  it("Total liabilities, the Liquidity card's Debts, Home's debt in net worth and Liabilities' total are one number", () => {
    const debt = netWorthDebt(a, s, true);
    const l = liquidity(s);
    expect(debt.owed).toBe(252_300);
    expect(l.debts).toBe(252_300);
    expect(balanceSheet(s, debt.mortgage).totalLiabilities).toBe(252_300);
    expect(debtOverview(a, s, 0).total).toBe(252_300);
    expect(debtOverview(a, s, 0)).toMatchObject({ margin: 0, other: 2_300 });
    expect(debt.classes).toEqual(["mortgage", "Joint Checking (overdrawn)", "Boat (below zero)"]);
    expect(netWorthDebtNote(debt, false, false)).toBe("mortgage + Joint Checking (overdrawn) + Boat (below zero)");
    expect(debtClassesOwed(s)).toEqual(["Mortgage"]);
  });
  it("liquid, illiquid and the assets are the rows above zero; the identity holds; cash on hand is the cash accounts' net", () => {
    expect(liquidity(s)).toMatchObject({ liquid: 15_000, illiquid: 400_000, assets: 415_000 });
    expectIdentity(s);
    expect(assetsBy(s, "asset_class")).toEqual([{ name: "House", value: 400_000 }, { name: "Stocks/ETF", value: 10_000 },
      { name: "Cash", value: 5_000 }]);
    expect(liquidCash(s), "the net of the cash accounts, the overdraft taken off").toBe(4_700);
  });
});

// A card in credit (a debt-class row above zero: the issuer owes the household) is an asset of its own, named for its
// account, never an asset line under its debt class's name, and its credit never comes off another card's debt
describe("one balance sheet by sign: a card in credit", () => {
  const card = (account: string, account_id: string, value: number): AssetRow => ({ account, account_id,
    institution: "Sample Bank", owner: "Alex", asset_class: "Other Debt", value, liquid: false, as_of: "2026-06-01",
    as_of_assumed: false, needs_review: false, source: "simplefin_balance", manual_id: null, carried_back: false });
  const cash: AssetRow = { ...card("Joint Checking", "chk", 5_000), asset_class: "Cash", liquid: true, owner: "Joint" };
  // cards counted in net worth: the snapshot holds a row for each - one owing 1,000, one in credit by 150
  const s = snap({ total: 4_150, assets_total: 5_150, liabilities_total: -1_000, liquid_total: 5_000,
    rows: [cash, card("Card One", "card1", -1_000), card("Card Two", "card2", 150)] });
  const none = { projections: {}, mortgage: null } as unknown as Assumptions;
  const cardIds = new Set(["card1", "card2"]);
  it("the card in credit is its own asset line, named for its account; the debt class carries only what is owed", () => {
    const bs = balanceSheet(s);
    expect(bs.assets).toEqual([{ name: "Cash", value: 5_000 }, { name: "Card Two (in credit)", value: 150 }]);
    expect(bs.liabilities).toEqual([{ name: "Other Debt", value: 1_000 }]);
    expect([bs.totalAssets, bs.totalLiabilities, bs.net]).toEqual([5_150, 1_000, 4_150]);
    expectIdentity(s);
  });
  it("Home's donut and Trends' mix name it the same way; by owner it is its owner's", () => {
    expect(assetsBy(s, "asset_class")).toEqual([{ name: "Cash", value: 5_000 }, { name: "Card Two (in credit)", value: 150 }]);
    expect(assetsBy(s, "owner")).toEqual([{ name: "Joint", value: 5_000 }, { name: "Alex", value: 150 }]);
  });
  it("what the cards owe is 1,000 with cards in net worth and with them left out", () => {
    expect(debtOverview(none, s, null, true, cardIds).cards).toBe(1_000);
    // left out of net worth: the balance history's newest points
    const history = cardHeadroom([
      { date: "2026-06-01", balance: -1_000, available: null, limit: 5_000, utilization: 0.2, source: "simplefin" },
      { date: "2026-06-01", balance: 150, available: null, limit: 2_000, utilization: 0, source: "simplefin" }]);
    expect([history.debt, history.inCredit]).toEqual([1_000, 1]);
    expect(debtOverview(none, snap({ rows: [cash] }), history.debt, false, cardIds).cards).toBe(1_000);
  });
});

describe("liquidity on the demo household's exports", () => {
  requireExports();
  it.skipIf(!HAVE_EXPORTS)("liquid + illiquid − debts is net worth on every snapshot, the newest included", () => {
    const snaps: Snapshot[] = readExport("assets_timeseries.json").snapshots;
    expect(snaps.length).toBeGreaterThan(0);
    for (const s of snaps) expectIdentity(s);
  });
  it.skipIf(!HAVE_EXPORTS)("the balance sheet by sign is the export's own totals on every snapshot: assets, liabilities, the debt", () => {
    const snaps: Snapshot[] = readExport("assets_timeseries.json").snapshots;
    const cents = (v: number) => Math.round(v * 100);
    const none = { mortgage: null } as unknown as Assumptions;
    for (const s of snaps) {
      const bs = balanceSheet(s);
      expect(cents(bs.totalAssets), `${s.snapshot_date}: assets`).toBe(cents(s.assets_total));
      expect(cents(bs.totalLiabilities), `${s.snapshot_date}: liabilities`).toBe(-cents(s.liabilities_total));
      expect(cents(netWorthDebt(none, s, false).owed), `${s.snapshot_date}: the debt in net worth`).toBe(-cents(s.liabilities_total));
    }
  });
});

describe("balanceHistory", () => {
  it("is the schedule as exported: every row, oldest first, nothing filtered out", () => {
    const schedule = [{ date: "2030-01-31", principal_balance: 250_000 }, { date: "2030-02-28", principal_balance: 249_400 },
      { date: "2030-03-15", principal_balance: 248_800 }];
    expect(balanceHistory(schedule)).toEqual(schedule);
  });
  it("is empty before the bank feed reports the account, and without a mortgage", () => {
    expect(balanceHistory([])).toEqual([]);
    expect(balanceHistory(undefined)).toEqual([]);
  });
});

describe("balanceSheet", () => {
  it("splits debt classes from assets and reconciles to net", () => {
    const by: Record<string, number> = { House: 1_000_000, "Stocks/ETF": 400_000, Cash: 100_000, Mortgage: -287_000, Margin: -12_000 };
    const cur = snap({
      by_class: by, total: 1_201_000,
      rows: Object.entries(by).map(([asset_class, value]) => ({ account: asset_class, account_id: asset_class, institution: "B",
        owner: "Joint", asset_class, value, liquid: false, as_of: "2026-06-01", as_of_assumed: false, needs_review: false,
        source: "bank_balance", manual_id: null, carried_back: false })),
    });
    const bs = balanceSheet(cur);
    expect(bs.assets.map((l) => l.name)).toEqual(["House", "Stocks/ETF", "Cash"]);
    expect(bs.liabilities.map((l) => l.name)).toEqual(["Mortgage", "Margin"]);
    expect(bs.totalLiabilities).toBe(299_000);
    expect(bs.net).toBe(1_201_000);
  });
});

describe("pnl / sankeyFlows", () => {
  const cashflow = [
    cfm({
      month: "2025-01", income: 20000, spend: 15000, invested: 2000,
      income_by_subcategory: { "Salary - Alex": 15000, "Salary - Sam": 5000 },
      by_category: {
        Housing: { total: 5000, by_subcategory: {} },
        Groceries: { total: 2000, by_subcategory: {} },
      },
    }),
    cfm({
      month: "2025-02", income: 20000, spend: 18000, invested: 0,
      income_by_subcategory: { "Salary - Alex": 15000, "Salary - Sam": 5000 },
      by_category: { Housing: { total: 5200, by_subcategory: {} } },
    }),
  ];
  it("aggregates the window and computes the rate", () => {
    const p = pnl(cashflow, "2025-01", "2025-02", "test");
    expect(p.monthCount).toBe(2);
    expect(p.income).toBe(40000);
    expect(p.spend).toBe(33000);
    expect(p.rate).toBeCloseTo(7000 / 40000);
    expect(p.spendByCat[0]).toEqual({ name: "Housing", value: 10200 });
  });
  it("sankey conserves flow through the hub", () => {
    const p = pnl(cashflow, "2025-01", "2025-02");
    const s = sankeyFlows(p);
    const inflow = s.links.filter((l) => l.target === "Household")
      .reduce((x, l) => x + l.value, 0);
    const outflow = s.links.filter((l) => l.source === "Household")
      .reduce((x, l) => x + l.value, 0);
    expect(Math.abs(inflow - outflow)).toBeLessThanOrEqual(3);  // rounding only
    expect(s.links.some((l) => l.target === "Invested")).toBe(true);
    expect(s.links.some((l) => l.target === "Saved (cash)")).toBe(true);
  });
  it("caps the income streams as it caps the categories: the largest ten, then one Other income node holding the rest", () => {
    const subs = Object.fromEntries(Array.from({ length: 13 }, (_, i) => [`Stream ${i + 1}`, 1300 - 100 * i]));
    const total = Object.values(subs).reduce((a, b) => a + b, 0);
    const p = pnl([cfm({ month: "2025-01", income: total, spend: 0, invested: 0, income_by_subcategory: subs, by_category: {} })],
      "2025-01", "2025-01");
    const s = sankeyFlows(p);
    const into = s.links.filter((l) => l.target === "Household");
    expect(into.map((l) => l.source)).toEqual([...Array.from({ length: 10 }, (_, i) => `Stream ${i + 1}`), "Other income"]);
    expect(into.at(-1)!.value).toBe(300 + 200 + 100);
    expect(into.reduce((x, l) => x + l.value, 0)).toBe(total);
    // ten or fewer streams: no Other income node
    expect(sankeyFlows(pnl(cashflow, "2025-01", "2025-02")).nodes.some((n) => n.name === "Other income")).toBe(false);
  });
});

describe("plWindow", () => {
  // a floor before every window below, so it clamps nothing
  const EARLY = "2020-01";
  it("computes trailing windows ending at lastFull", () => {
    expect(plWindow("3m", "2026-05", EARLY)).toEqual(["2026-03", "2026-05"]);
    expect(plWindow("6m", "2026-05", EARLY)).toEqual(["2025-12", "2026-05"]);
    expect(plWindow("12m", "2026-05", EARLY)).toEqual(["2025-06", "2026-05"]);
  });
  it("computes year-to-date", () => {
    expect(plWindow("ytd", "2026-05", EARLY)).toEqual(["2026-01", "2026-05"]);
    expect(plWindow("ytd", "2026-01", EARLY)).toEqual(["2026-01", "2026-01"]);
  });
  it("clamps to the coverage floor", () => {
    // 12 months back from 2030-12 would be 2030-01, before the floor
    expect(plWindow("12m", "2030-12", "2030-05")).toEqual(["2030-05", "2030-12"]);
    expect(plWindow("ytd", "2030-08", "2030-05")).toEqual(["2030-05", "2030-08"]);
    expect(plWindow("3m", "2030-12", "2030-05"), "a window after the floor is untouched").toEqual(["2030-10", "2030-12"]);
  });
  it("with no floor (no month in coverage yet) there is no window: every month precedes the coverage", () => {
    expect(plWindow("12m", "2030-12", null)).toBeNull();
    expect(plWindow("ytd", "2030-08", null)).toBeNull();
  });
});

describe("lastFullYear (the P&L's calendar-year column)", () => {
  const months = (from: string, to: string) => {
    const out: { month: string }[] = [];
    for (let m = from; m <= to; m = nextMonth(m)) out.push({ month: m });
    return out;
  };
  it("is the greatest year whose twelve months are all in the data", () => {
    expect(lastFullYear(months("2029-03", "2031-05"), "2031-04")).toBe("2030");
    expect(lastFullYear(months("2028-01", "2030-12"), "2030-11"), "2030's December is not a full month yet").toBe("2029");
    expect(lastFullYear(months("2028-01", "2031-01"), "2030-12")).toBe("2030");
  });
  it("a year with a month missing is not whole", () => {
    const gap = months("2029-01", "2031-03").filter((m) => m.month !== "2030-07");
    expect(lastFullYear(gap, "2031-02")).toBe("2029");
  });
  it("none when no calendar year is whole", () => {
    expect(lastFullYear(months("2030-02", "2031-05"), "2031-04")).toBeNull();
    expect(lastFullYear([], "2031-04")).toBeNull();
  });
});

describe("savingsKpis (Cash Flow's headline KPIs)", () => {
  const rated = (month: string, savings_rate: number | null, income = 10000) =>
    cfm({ month, savings_rate, income, spend: savings_rate === null ? 0 : income * (1 - savings_rate) });
  const cashflow = [
    { ...rated("2023-05", 0.9), in_coverage: false },   // pre-coverage, ignored
    rated("2025-01", 0.2), rated("2025-02", -0.1), rated("2025-03", 0.3), rated("2025-04", null, 0),
    rated("2025-05", 0.95, 4000), // the month in progress: a payday with half the bills still to come
    rated("2025-06", 1, 9000),    // a month a row was dated ahead into: no month yet
  ];
  it("every window ends on the last FULL month - the month in progress and a month dated ahead feed no rate", () => {
    const k = savingsKpis(cashflow, "2025-05");
    expect(k.t3.map((m) => m.month)).toEqual(["2025-02", "2025-03", "2025-04"]);
    expect(k.t12.map((m) => m.month)).toEqual(["2025-01", "2025-02", "2025-03", "2025-04"]);
    expect(k.through).toBe("2025-04");
    expect(k.t12).toEqual(recentMonths(cashflow, "2025-05", 12));   // exactly Home's TTM window
  });
  it("neither the month in progress nor a month dated ahead can be the best (or worst) month; unrated months are skipped", () => {
    const k = savingsKpis(cashflow, "2025-05");
    expect(k.best?.month).toBe("2025-03");
    expect(k.worst?.month).toBe("2025-02");
    expect(k.inWindow.map((m) => m.month), "no window: every full month").toEqual(["2025-01", "2025-02", "2025-03", "2025-04"]);
  });
  it("the best and the worst are of the window's full months, while the trailing rates keep the whole history", () => {
    const k = savingsKpis(cashflow, "2025-05", { from: "2025-01", to: "2025-02" });
    expect([k.best?.month, k.worst?.month]).toEqual(["2025-01", "2025-02"]);
    expect(k.inWindow.map((m) => m.month)).toEqual(["2025-01", "2025-02"]);
    expect(k.t12.map((m) => m.month), "the trailing window does not follow the selected one").toEqual(["2025-01", "2025-02", "2025-03", "2025-04"]);
    expect(k.through).toBe("2025-04");
    // a window that reaches into the month in progress and past it holds only its full months
    const late = savingsKpis(cashflow, "2025-05", { from: "2025-03", to: "2025-06" });
    expect(late.inWindow.map((m) => m.month)).toEqual(["2025-03", "2025-04"]);
    expect([late.best, late.worst], "one rated month in it (April has no income): no best, no worst").toEqual([null, null]);
  });
  it("a window with one full month has no best and no worst: one month is no comparison with itself", () => {
    const k = savingsKpis(cashflow, "2025-05", { from: "2025-03", to: "2025-03" });
    expect(k.inWindow.map((m) => m.month)).toEqual(["2025-03"]);
    expect([k.best, k.worst]).toEqual([null, null]);
  });
  it("no full month: empty windows, no best/worst, no through stamp", () => {
    const k = savingsKpis([rated("2025-05", 0.5)], "2025-05");
    expect(k).toEqual({ t3: [], t12: [], inWindow: [], best: null, worst: null, through: null });
    expect(savingsKpis([], null)).toEqual(k);
  });
});

describe("savingsRateLabel - one name for a trailing-window savings rate", () => {
  it('"Savings rate (12 mo)": no "TTM", no "trailing", whatever the window', () => {
    expect(savingsRateLabel(12)).toBe("Savings rate (12 mo)");
    expect(savingsRateLabel(3)).toBe("Savings rate (3 mo)");
    for (const n of [1, 3, 5, 12]) expect(savingsRateLabel(n)).not.toMatch(/TTM|trailing/i);
  });
});

describe("savedText - a month's savings rate in words", () => {
  it("a rate at or above zero is what was saved; below zero it says what was spent beyond what came in, never a negative 'saved'", () => {
    expect(savedText(0.3, 300)).toBe("30.0% saved");
    expect(savedText(0, 0)).toBe("0.0% saved");
    expect(savedText(-0.12, -120)).toBe("spent 12.0% more than came in");
    expect(savedText(-1, -1_000), "spending twice the income is still a rate").toBe("spent 100.0% more than came in");
    for (const r of [-0.12, -0.004, -1]) expect(savedText(r, -1)).not.toMatch(/saved|−|-/);
  });
  it("below −100% it is the month's dollars in the shortfall's words, never a percentage", () => {
    expect(savedText(-1.5, -6_300)).toBe(`income did not cover spending: ${fmtUSD0(-6_300)}`);
    expect(savedText(-28.067, -5_600)).not.toMatch(/%/);
  });
  it("is a ratio: privacy mode leaves it; the dollars below the floor it masks", () => {
    setPrivate(true);
    try {
      expect(savedText(-0.12, -120)).toBe("spent 12.0% more than came in");
      expect(savedText(-28, -5_600)).not.toMatch(/\d/);
    } finally {
      setPrivate(false);
    }
  });
});

describe("the printable savings rate: one floor at −100% for every headline, tile, month and P&L row", () => {
  it("a rate at or above −100% prints; below it there is none to print, and the dollars say the shortfall", () => {
    expect(RATE_FLOOR).toBe(-1);
    expect([0.2, 0, -0.5, -1].map(printableRate)).toEqual([0.2, 0, -0.5, -1]);
    expect([-1.0001, -41.77].map(printableRate)).toEqual([null, null]);
    expect(printableRate(null)).toBeNull();
    expect([-1, -1.0001, null].map(belowRateFloor)).toEqual([false, true, false]);
    expect(shortfallText(-7_715)).toBe(`income did not cover spending: ${fmtUSD0(-7_715)} a month`);
  });
  it("interest as the only income (the no-income household's shape): its trailing rate is below the floor", () => {
    const months = Array.from({ length: 12 }, (_, i) => cfm({ month: `2025-${String(i + 1).padStart(2, "0")}`, income: 185,
      spend: 7_900, savings_rate: (185 - 7_900) / 185 }));
    const rate = savingsRate(months);
    expect(belowRateFloor(rate)).toBe(true);
    expect(fmtPct(printableRate(rate))).toBe("—");
    expect(monthlyNet(months)).toBe(185 - 7_900);
    expect(monthlyNet([])).toBe(0);
  });
});


describe("cashRunway reads the essentials through the category renames", () => {
  const tx = txFactory({ type: "purchase", account: "chk", subcategory: "Fuel" });
  const cur = snap({ rows: [{ account: "Chk", account_id: "chk", institution: "B", owner: "Joint", asset_class: "Cash", value: 3_000, liquid: true,
    as_of: "2026-06-01", as_of_assumed: false, needs_review: false, source: "bank_balance", manual_id: null,
    carried_back: false }] });
  // window 2025-12..2026-05: $600/mo of fuel (essential by default) and $300/mo of dining (not)
  const rows = (fuel: string) => [tx({ date: "2026-01-10", category: fuel, amount: -3600 }), tx({ date: "2026-02-10", category: "Dining", subcategory: "Restaurants", amount: -1800 })];
  const renames = [{ from: { category: "Transport" }, to: { category: "Transportation" } }];
  it("a renamed essential category still divides the cash: the same months as before the rename", () => {
    const before = cashRunway(categoryVocab(vocabAssumptions()), rows("Transport"), cur, SIX);
    const after = cashRunway(categoryVocab(vocabAssumptions(renames)), rows("Transportation"), cur, SIX);
    expect(before.essential).toBeCloseTo(600);
    expect(before.months).toBeCloseTo(5);
    expect(after).toEqual(before);
  });
  it("the vocabulary is what keeps it: read as unrenamed, the renamed fuel row is not essential (control)", () => {
    expect(cashRunway(categoryVocab(vocabAssumptions()), rows("Transportation"), cur, SIX).months).toBeNull();
  });
  it("with no window (too little history) the cash is still the snapshot's, and the runway is unknown: no row, no essentials, no months", () => {
    const r = cashRunway(categoryVocab(vocabAssumptions()), rows("Transport"), cur, null);
    expect(r.cash).toBe(3_000);
    expect(r.window).toBeNull();
    expect(r.rowCount).toBe(0);
    expect(r.essential, "no window: unknown, never $0 a month").toBeNull();
    expect(r.months).toBeNull();
  });
});

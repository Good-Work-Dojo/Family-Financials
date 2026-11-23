// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Pure second-house scenario math — no React, no charts.
// All rates/percents are percent numbers (6 = 6%), matching amortize();
// don't format them with fmtPct (which multiplies by 100).
import type { AccountMeta } from "../data/types";
import { isCashLike } from "./accounts";
import { countOf, fmtUSD, fmtUSD0 } from "./format";
import { amortize } from "./projections";
import { COLLEGE_CLASS, RETIREMENT_CLASS } from "./tokens";

export interface SecondHouseInputs {
  price: number;
  downPct: number;
  ratePct: number;           // 30-yr fixed; term is not an input
  closingCosts: number;
  postMoveInCosts: number;   // furniture / immediate repairs
  taxPctYr: number;          // property tax, % of price per year
  insuranceYr: number;       // $/yr
  hoaMo: number;             // $/mo
  utilitiesMo: number;       // $/mo
  maintPctYr: number;        // maintenance reserve, % of price per year
  nightlyRate: number;       // short-term rental $/night
  occupancyPct: number;
  nightsPerMonth: number;    // nights available to rent
  platformFeePct: number;    // % of gross (Airbnb/VRBO)
  mgmtFeePct: number;        // % of gross
  cleaningPerStay: number;   // $ per turnover
  staysPerMonth: number;
}

export const TERM_MONTHS = 360;

export const DEFAULT_INPUTS: SecondHouseInputs = {
  price: 600_000, downPct: 20, ratePct: 6.0,
  closingCosts: 10_000, postMoveInCosts: 10_000,
  taxPctYr: 1.1, insuranceYr: 2_400, hoaMo: 0, utilitiesMo: 300, maintPctYr: 1.0,
  nightlyRate: 250, occupancyPct: 50, nightsPerMonth: 30,
  platformFeePct: 15, mgmtFeePct: 0, cleaningPerStay: 150, staysPerMonth: 4,
};

// ---------- mortgage ----------

/** Level payment P·r/(1−(1+r)^−n) with r = annualRatePct/100/12 — the same
 *  r/12 mortgage convention as amortize(), so the two round-trip. */
export function pmt(principal: number, annualRatePct: number, months = TERM_MONTHS): number {
  if (principal <= 0) return 0;
  const r = annualRatePct / 100 / 12;
  if (r === 0) return principal / months;
  return principal * r / (1 - Math.pow(1 + r, -months));
}

export function downPaymentUSD(i: SecondHouseInputs): number {
  return i.price * i.downPct / 100;
}

export function loanAmount(i: SecondHouseInputs): number {
  return i.price - downPaymentUSD(i);
}

/** cash at/after close: down payment + closing + post-move-in */
export function cashNeeded(i: SecondHouseInputs): number {
  return downPaymentUSD(i) + i.closingCosts + i.postMoveInCosts;
}

export function monthlyPI(i: SecondHouseInputs): number {
  return pmt(loanAmount(i), i.ratePct);
}

export function pmiLikely(i: SecondHouseInputs): boolean {
  return i.downPct < 20;
}

// ---------- monthly carrying cost ----------

export interface CarryBreakdown {
  pi: number; tax: number; insurance: number; hoa: number;
  utilities: number; maintenance: number; total: number;
}

export function carryBreakdown(i: SecondHouseInputs): CarryBreakdown {
  const pi = monthlyPI(i);
  const tax = i.price * i.taxPctYr / 100 / 12;
  const insurance = i.insuranceYr / 12;
  const maintenance = i.price * i.maintPctYr / 100 / 12;
  return {
    pi, tax, insurance, hoa: i.hoaMo, utilities: i.utilitiesMo, maintenance,
    total: pi + tax + insurance + i.hoaMo + i.utilitiesMo + maintenance,
  };
}

// ---------- short-term rental ----------

export interface RentalBreakdown {
  bookedNights: number;
  gross: number;
  platformFee: number;
  mgmtFee: number;
  cleaning: number;
  net: number;               // may be negative (cleaning on a dead month) — never clamped
}

export function rentalBreakdown(i: SecondHouseInputs): RentalBreakdown {
  const bookedNights = i.nightsPerMonth * i.occupancyPct / 100;
  const gross = i.nightlyRate * bookedNights;
  const platformFee = gross * i.platformFeePct / 100;
  const mgmtFee = gross * i.mgmtFeePct / 100;
  const cleaning = i.cleaningPerStay * i.staysPerMonth;
  return { bookedNights, gross, platformFee, mgmtFee, cleaning,
    net: gross - platformFee - mgmtFee - cleaning };
}

export function netMonthlyCost(i: SecondHouseInputs): number {
  return carryBreakdown(i).total - rentalBreakdown(i).net;
}

/** Occupancy % where net rental revenue covers the full monthly carry. Net is
 *  linear in occupancy, so: o = 100·(carry + cleaning) / (rate·nights·(1−fees)).
 *  null when the denominator is ≤ 0 (no rate/nights, fees ≥ 100%); values over
 *  100 are returned un-clamped — "not achievable" is the honest answer. */
export function breakevenOccupancyPct(i: SecondHouseInputs): number | null {
  const carry = carryBreakdown(i).total;
  const cleaning = i.cleaningPerStay * i.staysPerMonth;
  const perFullMonth = i.nightlyRate * i.nightsPerMonth
    * (1 - (i.platformFeePct + i.mgmtFeePct) / 100);
  if (perFullMonth <= 0) return null;
  return 100 * (carry + cleaning) / perFullMonth;
}

// ---------- funding plan ----------

/** a dollar amount in whole cents, for the comparisons a verdict rests on (a sum of typed draws carries float dust) */
const cents = (v: number): number => Math.round(v * 100);

export interface FundingRow {
  accountId: string;
  name: string;
  kind: AccountMeta["kind"];
  /** what the dashboard calls the account everywhere (accounts.json's resolved class: Cash, Stocks/ETF, Retirement…);
   *  the funding table labels a row by it, never by its kind, a configuration word */
  assetClass: string;
  owner: AccountMeta["owner"];
  before: number | null;     // latest_balance (null = feed reports no balance, e.g. Venmo)
  asOf: string | null;
  draw: number;
  after: number | null;
  /** a draw (above zero) takes the account below zero by a cent or more — flagged, never clamped. An account already
   *  below zero with no draw is no overdraw: its balance is shown as it is */
  overdraw: boolean;
  retirement: boolean;       // a retirement or 529 account (isRetirementAccount) — caution, not a block
  taxableDrag: boolean;      // taxable brokerage/crypto — selling may realize gains
  cashLike: boolean;         // the Cash class (isCashLike): checking, savings, a brokerage's cash account
}

/** IRA/Roth/401k/529 by name: the fallback for an account whose class does not say (isRetirementAccount) */
export function isRetirementName(name: string): boolean {
  return /\b(ira|roth|401|529)/i.test(name);
}

/** A retirement or college account, whose draw carries a penalty and no capital gain: by the class the exports resolve
 *  for it (accounts.json `asset_class`: Retirement or 529, so a 403(b), a TSP, a 457 or an HSA filed as Retirement is
 *  one), else by its name. */
export function isRetirementAccount(a: Pick<AccountMeta, "asset_class" | "name">): boolean {
  return a.asset_class === RETIREMENT_CLASS || a.asset_class === COLLEGE_CLASS || isRetirementName(a.name);
}

/** Everything except credit cards and loans can fund a purchase. */
export function eligibleFundingAccounts(accounts: AccountMeta[]): AccountMeta[] {
  const rank = (a: AccountMeta) =>
    a.latest_balance === null ? 4
    : a.kind === "checking" ? 0
    : isCashLike(a) ? 1       // the other Cash-class accounts: savings, a brokerage's cash account
    : isRetirementAccount(a) ? 3
    : 2;   // taxable brokerage / crypto / wallet
  return accounts
    .filter((a) => a.kind !== "credit-card" && a.kind !== "loan")
    .map((a, idx) => ({ a, idx }))
    .sort((x, y) => rank(x.a) - rank(y.a) || x.idx - y.idx)
    .map((x) => x.a);
}

export function fundingRows(accounts: AccountMeta[], draws: Record<string, number>): FundingRow[] {
  return eligibleFundingAccounts(accounts).map((a) => {
    const raw = draws[a.id];
    const draw = Number.isFinite(raw) && raw! > 0 ? raw! : 0;
    const before = a.latest_balance;
    const after = before === null ? null : before - draw;
    const retirement = isRetirementAccount(a);
    const cashLike = isCashLike(a);
    return {
      accountId: a.id, name: a.name, kind: a.kind, assetClass: a.asset_class, owner: a.owner,
      before, asOf: a.latest_balance_as_of, draw, after,
      overdraw: after !== null && draw > 0 && cents(after) < 0,
      retirement,
      // "Cash" accounts at a brokerage (e.g. Sample Bank Joint Cash) hold no
      // appreciated positions — no gains to realize
      taxableDrag: a.kind === "brokerage" && !retirement && !isCashLike(a),
      cashLike,
    };
  });
}

export interface FundingSummary {
  drawn: number;
  needed: number;
  gap: number;               // still to find (0 when fully funded)
  surplus: number;           // drawn beyond what's needed
  pctFunded: number;         // un-clamped fraction; UI clamps the bar at 100%
}

export function fundingSummary(rows: FundingRow[], needed: number): FundingSummary {
  const drawn = rows.reduce((s, r) => s + r.draw, 0);
  return {
    drawn, needed,
    gap: Math.max(0, needed - drawn),
    surplus: Math.max(0, drawn - needed),
    // zero-target = "fully funded ✓" on purpose (nothing left to fund), not a
    // missing-data null — deliberately different from the rates convention
    pctFunded: needed > 0 ? drawn / needed : 1,
  };
}

/** what a funding plan can claim, in order of precedence: `unknown` (no account that can fund it has a known balance,
 *  so nothing can be drawn), `overdraw` (a draw takes its account below zero, whatever the total), `uncovered` (a draw
 *  saved against an account whose balance is not known, which nothing shows can be met), `short` (the draws add up to
 *  less than the cash needed), `over` (to more), `funded` (exactly, to the cent, and every draw covered) */
export type FundingVerdict = "unknown" | "overdraw" | "uncovered" | "short" | "over" | "funded";

/** an account a draw takes below zero, and by how much (positive dollars) */
export interface Overdrawn { accountId: string; name: string; by: number }

/** an account with no known balance and a draw on it (its box is closed, but a draw saved earlier still counts) */
export interface Uncovered { accountId: string; name: string; draw: number }

export interface FundingState extends FundingSummary {
  verdict: FundingVerdict;
  /** every draw that takes its account below zero, in the funding table's order */
  overdrawn: Overdrawn[];
  /** every draw on an account whose balance is not known, in the funding table's order */
  uncovered: Uncovered[];
  /** what the draws add up to against the cash needed, in whole cents, whatever the verdict: an overdraw or an
   *  uncovered draw never hides a shortfall or an excess */
  totals: "short" | "over" | "exact";
}

/** THE verdict of a funding plan (the funding card, the scenario tabs and the comparison read it): "fully funded" only
 *  when the draws add up to the cash needed, no draw takes its account below zero and none rests on a balance nobody
 *  knows. The scenario states no floor of its own, so an account's floor is zero. */
export function fundingState(rows: FundingRow[], needed: number): FundingState {
  const summary = fundingSummary(rows, needed);
  const overdrawn = rows.filter((r) => r.overdraw)
    .map((r) => ({ accountId: r.accountId, name: r.name, by: -(r.after as number) }));
  const uncovered = rows.filter((r) => r.before === null && r.draw > 0)
    .map((r) => ({ accountId: r.accountId, name: r.name, draw: r.draw }));
  const diff = cents(needed) - cents(summary.drawn);
  const totals = diff > 0 ? "short" : diff < 0 ? "over" : "exact";
  const verdict: FundingVerdict = !rows.some((r) => r.before !== null) ? "unknown"
    : overdrawn.length > 0 ? "overdraw"
    : uncovered.length > 0 ? "uncovered"
    : totals === "exact" ? "funded"
    : totals;
  return { ...summary, verdict, overdrawn, uncovered, totals };
}

/** the plan closes: the draws cover the cash needed, no account goes below zero and every draw's cover is known; null
 *  when nothing can be drawn */
export const fundingCloses = (v: FundingVerdict): boolean | null =>
  v === "unknown" ? null : v === "funded" || v === "over";

/** a dollar amount below zero by a cent or more (a sum of floats a hair under zero is none) */
export const belowZero = (v: number): boolean => cents(v) < 0;

/** an amount in a verdict's or a shortfall's words: whole dollars, or cents when it is under half a dollar (never
 *  "$0") */
export const amountText = (v: number): string => (Math.abs(v) < 0.5 ? fmtUSD(v) : fmtUSD0(v));

/** the accounts a clause names, this many at most; the rest are counted */
const NAMED_ACCOUNTS = 3;

/** "a, b and c" */
const andList = (xs: readonly string[]): string =>
  (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

/** the names, at most NAMED_ACCOUNTS of them, then "N more" */
const named = (names: readonly string[]): string[] => (names.length <= NAMED_ACCOUNTS ? [...names]
  : [...names.slice(0, NAMED_ACCOUNTS), `${names.length - NAMED_ACCOUNTS} more`]);

/** "Joint Checking would be overdrawn by $6,366", then "B by $50 and C by $20", then "and N more" */
function overdrawClause(list: readonly Overdrawn[]): string {
  const parts = list.slice(0, NAMED_ACCOUNTS)
    .map((o, k) => (k === 0 ? `${o.name} would be overdrawn by ${amountText(o.by)}` : `${o.name} by ${amountText(o.by)}`));
  if (list.length > NAMED_ACCOUNTS) parts.push(`${list.length - NAMED_ACCOUNTS} more`);
  return andList(parts);
}

/** "the balance of Wallet is not known, so its draw of $500 cannot be counted on"; several: their names (three, then
 *  "N more") and what their draws add up to */
function uncoveredClause(list: readonly Uncovered[]): string {
  if (list.length === 1) return `the balance of ${list[0].name} is not known, so its draw of ${amountText(list[0].draw)} cannot be counted on`;
  const total = list.reduce((s, u) => s + u.draw, 0);
  return `the balances of ${andList(named(list.map((u) => u.name)))} are not known, so their draws of ${amountText(total)} `
    + "cannot be counted on";
}

/** The funding plan's verdict in words, null when nothing can be drawn: "fully funded ✓", or the clauses that are true,
 *  in order and joined by "; " — the overdrawn accounts ("Joint Checking would be overdrawn by $6,366"), the draws on a
 *  balance nobody knows, then the totals when they do not match ("funded $X of $Y; $Z short", "drawn $X for $Y; $Z more
 *  than needed"), which say how much of the total rests on an unknown balance ("(incl. $500 on a balance that is not
 *  known)"). */
export function fundingVerdictText(s: FundingState): string | null {
  if (s.verdict === "unknown") return null;
  if (s.verdict === "funded") return "fully funded ✓";
  // the total counts every draw, those the line says cannot be counted on included, and says how much of it they are
  const unknownPart = s.uncovered.reduce((sum, u) => sum + u.draw, 0);
  const incl = s.uncovered.length === 0 ? ""
    : ` (incl. ${amountText(unknownPart)} on ${s.uncovered.length === 1 ? "a balance that is" : "balances that are"} not known)`;
  const clauses = [
    ...(s.overdrawn.length ? [overdrawClause(s.overdrawn)] : []),
    ...(s.uncovered.length ? [uncoveredClause(s.uncovered)] : []),
    ...(s.totals === "short"
      ? [`funded ${fmtUSD0(s.drawn)} of ${fmtUSD0(s.needed)}${incl}`, `${amountText(s.needed - s.drawn)} short`] : []),
    ...(s.totals === "over"
      ? [`drawn ${fmtUSD0(s.drawn)} for ${fmtUSD0(s.needed)}${incl}`, `${amountText(s.drawn - s.needed)} more than needed`] : []),
  ];
  return clauses.join("; ");
}

/** The same verdict in a comparison cell's few words, null when nothing can be drawn: "fully funded", or its facts
 *  joined by "; " — "overdraws 2 accounts", "1 draw on an unknown balance", "$Z short" / "$Z over". */
export function fundingVerdictBrief(s: FundingState): string | null {
  if (s.verdict === "unknown") return null;
  if (s.verdict === "funded") return "fully funded";
  return [
    ...(s.overdrawn.length ? [`overdraws ${countOf(s.overdrawn.length, "account")}`] : []),
    ...(s.uncovered.length ? [`${countOf(s.uncovered.length, "draw")} on ${
      s.uncovered.length === 1 ? "an unknown balance" : "unknown balances"}`] : []),
    ...(s.totals === "short" ? [`${amountText(s.needed - s.drawn)} short`] : []),
    ...(s.totals === "over" ? [`${amountText(s.drawn - s.needed)} over`] : []),
  ].join("; ");
}

/** Upper-bound LTCG hit if the whole draw were realized gain — the honest
 *  ceiling without lot-level basis; copy must say "up to". */
export function capGainsDragMax(draw: number, ltcgPct: number): number {
  return draw * ltcgPct / 100;
}

export function cashLikeDrawTotal(rows: FundingRow[]): number {
  return rows.reduce((s, r) => s + (r.cashLike ? r.draw : 0), 0);
}

// ---------- chart series ----------

export function rateSensitivity(
  i: SecondHouseInputs, fromPct = 4.5, toPct = 8.5, stepPct = 0.25,
): { ratePct: number; pi: number }[] {
  const loan = loanAmount(i);
  const n = Math.round((toPct - fromPct) / stepPct);
  const out: { ratePct: number; pi: number }[] = [];
  for (let k = 0; k <= n; k++) {
    const ratePct = Math.round((fromPct + k * stepPct) * 100) / 100;
    out.push({ ratePct, pi: pmt(loan, ratePct) });
  }
  return out;
}

export function downPaymentSweep(
  i: SecondHouseInputs, fromPct = 10, toPct = 50, stepPct = 5,
): { downPct: number; pi: number; cashToClose: number }[] {
  const out: { downPct: number; pi: number; cashToClose: number }[] = [];
  for (let d = fromPct; d <= toPct; d += stepPct) {
    const down = i.price * d / 100;
    out.push({
      downPct: d,
      pi: pmt(i.price - down, i.ratePct),
      cashToClose: down + i.closingCosts + i.postMoveInCosts,
    });
  }
  return out;
}

/** Loan balance + equity by month over the full term. Home value held flat at
 *  the purchase price — appreciation deliberately not assumed (say so in the UI). */
export function equityCurve(i: SecondHouseInputs): { month: number; balance: number; equity: number }[] {
  const loan = loanAmount(i);
  if (loan <= 0) return [{ month: 0, balance: 0, equity: i.price }];
  // pmt() always covers first-month interest, so amortize can't throw here
  const { schedule } = amortize({ balance: loan, annualRatePct: i.ratePct, monthlyPayment: pmt(loan, i.ratePct) });
  return [
    { month: 0, balance: loan, equity: i.price - loan },
    ...schedule.map((s) => ({ month: s.month, balance: s.balance, equity: i.price - s.balance })),
  ];
}

// ---------- scenario comparison ----------

export interface ComparisonRow {
  id: string;
  name: string;
  price: number; downPct: number; ratePct: number;
  cashNeeded: number; pi: number; fullCarry: number; netAfterRental: number;
  breakevenOcc: number | null;
  /** what the scenario's draws add up to; null when no account that can fund it has a known balance (nothing can be
   *  drawn: no "$0 funded") */
  drawn: number | null;
  /** liquidCash − this scenario's draws from Cash-class accounts; null with no cash figure (no snapshot). Below zero the
   *  cash draws are more than the cash there is: a shortfall, which the page says in words */
  remainingLiquid: number | null;
  /** the scenario's funding verdict (fundingState), so the comparison and the tabs say which scenarios do not close */
  funding: FundingState;
}

export function comparisonRows(
  scenarios: { id: string; name: string; inputs: SecondHouseInputs; draws: Record<string, number> }[],
  accounts: AccountMeta[],
  liquidCash: number | null,
): ComparisonRow[] {
  return scenarios.map((s) => {
    const rows = fundingRows(accounts, s.draws);
    const summary = fundingState(rows, cashNeeded(s.inputs));
    return {
      id: s.id, name: s.name,
      price: s.inputs.price, downPct: s.inputs.downPct, ratePct: s.inputs.ratePct,
      cashNeeded: cashNeeded(s.inputs),
      pi: monthlyPI(s.inputs),
      fullCarry: carryBreakdown(s.inputs).total,
      netAfterRental: netMonthlyCost(s.inputs),
      breakevenOcc: breakevenOccupancyPct(s.inputs),
      drawn: rows.some((r) => r.before !== null) ? summary.drawn : null,
      remainingLiquid: liquidCash === null ? null : liquidCash - cashLikeDrawTotal(rows),
      funding: summary,
    };
  });
}

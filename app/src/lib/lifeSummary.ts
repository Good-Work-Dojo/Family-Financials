// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Pure helpers behind the one-glance figures on Home, Cash Flow, Liabilities and Planning. No React - each
// owning page (Mortgage, Tools) keeps its own interactive version of the same math.
import type { AssetRow, Assumptions, CashflowMonth, MortgageBlock, MortgageScheduleRow, Snapshot, Transaction } from "../data/types";
import { fullMonths, type RunRateWindow, runRateWindow, savingsRate, trailingWindow } from "./agg";
import type { CategoryVocab } from "./categories";
import { emergencyCoverage } from "./emergency";
import { fmtDate, fmtPct, fmtUSD0, localISO } from "./format";
import { amortize } from "./projections";
import { DEBT_CLASSES, HOUSE_CLASS } from "./tokens";

/** snapshot closest in time to `targetDate` (snapshots are irregular) */
export function nearestSnapshot(snaps: Snapshot[], targetDate: string): Snapshot | null {
  if (!snaps.length) return null;
  const t = new Date(targetDate + "T00:00:00").getTime();
  let best = snaps[0];
  for (const s of snaps) {
    const d = Math.abs(new Date(s.snapshot_date + "T00:00:00").getTime() - t);
    if (d < Math.abs(new Date(best.snapshot_date + "T00:00:00").getTime() - t)) best = s;
  }
  return best;
}

export interface NetWorthDelta {
  current: Snapshot;
  prior: Snapshot;
  delta: number;
  /** the change over the prior total (changeShare); null when that total is not above 0 */
  pct: number | null;
  /** true when the prior snapshot doesn't subtract the mortgage but the
   *  current one does — the delta then understates real growth */
  mortgageCaveat: boolean;
}

/** The share a change is of the total it is measured from; null when that total is not above 0: nothing to divide by
 *  at 0, and a share of a negative total reads backwards (−$50,000 to −$25,000 is a gain, never "−50%"). */
export const changeShare = (delta: number, base: number): number | null => (base > 0 ? delta / base : null);

/** The newest snapshot against the one nearest `monthsBack` months before it. null with fewer than two snapshots:
 *  nothing to compare, never a change of 0. */
export function netWorthDelta(snaps: Snapshot[], monthsBack = 12): NetWorthDelta | null {
  if (!snaps.length) return null;
  const current = snaps[snaps.length - 1];
  const d = new Date(current.snapshot_date + "T00:00:00");
  d.setMonth(d.getMonth() - monthsBack);
  const prior = nearestSnapshot(
    snaps.filter((s) => s !== current), localISO(d));
  if (!prior) return null;
  return {
    current, prior,
    delta: current.total - prior.total,
    pct: changeShare(current.total - prior.total, prior.total),
    mortgageCaveat: current.mortgage_included && !prior.mortgage_included,
  };
}

/** the trailing `n` full months before the month in progress (`fullMonths`; fewer when the history is shorter) */
export function recentMonths(cashflow: CashflowMonth[], inProgress: string | null, n: number): CashflowMonth[] {
  return fullMonths(cashflow, inProgress).slice(-n);
}

/** a monthly run-rate and the full months it is averaged over (12, or fewer) */
export interface SavingsPace { monthly: number; months: number }

/** THE savings pace, income less spending a month, for every planning figure that reads one (Planning's net-worth
 *  prefill and stress test, Second house's free cash flow): averaged over the trailing twelve full months
 *  (runRateWindow; `firstDay`, the data's first transaction day, leaves out a first month the data covers in part), or
 *  as many as there are, and `months` says how many. null with fewer than RUN_RATE_MIN_MONTHS full months: no pace is
 *  known, never one from a week or a single month. */
export function savingsPace(cashflow: CashflowMonth[], inProgress: string | null, firstDay: string | null): SavingsPace | null {
  const window = runRateWindow(cashflow, inProgress, 12, firstDay);
  if (window === null) return null;
  const net = fullMonths(cashflow, inProgress).filter((m) => m.month >= window.from)
    .reduce((s, m) => s + m.income - m.spend, 0);
  return { monthly: net / window.months, months: window.months };
}

export interface SavingsKpis {
  /** the trailing 3 / 12 full months the savings-rate KPIs read, whatever window the page shows */
  t3: CashflowMonth[];
  t12: CashflowMonth[];
  /** the last full month the trailing windows above end on (YYYY-MM); null with no full month */
  through: string | null;
  /** the full months of the window the page shows (`MonthWindow`) */
  inWindow: CashflowMonth[];
  /** of the window's full months, the one with the highest / lowest savings rate - null unless two or more of them have
   *  a rate (one month is no comparison with itself). The month in progress, with its bills still to come, is never one */
  best: CashflowMonth | null;
  worst: CashflowMonth | null;
}

/** a window of months: its first and last month (YYYY-MM, inclusive); a side left out (or null) is open */
export interface MonthWindow { from?: string | null; to?: string | null }

/** The one name of a trailing-window savings rate - "Savings rate (12 mo)" - on Home's hero tile and Cash Flow's tiles
 *  (the income statement's 12M column is the same number). "TTM", "trailing 12 mo" and "12-month" were three names for it. */
export const savingsRateLabel = (months: number): string => `Savings rate (${months} mo)`;

/** The lowest savings rate printed as a percentage: −100%, spending twice the income. Below it (interest as the only
 *  income gives −4177%) a percentage tells a reader nothing, so a headline, a tile, a best or worst month and a P&L row
 *  print "—" and the dollars instead (`shortfallText`). A chart keeps its own pinning rule. */
export const RATE_FLOOR = -1;

/** whether a rate is below RATE_FLOOR: a figure that prints "—" and the dollars */
export const belowRateFloor = (rate: number | null): boolean => rate !== null && rate < RATE_FLOOR;

/** the rate to print as a percentage: null (so fmtPct gives "—") when there is none or it is below RATE_FLOOR */
export const printableRate = (rate: number | null): number | null => (belowRateFloor(rate) ? null : rate);

/** the shortfall in the words Projections uses: "income did not cover spending: −$7,715 a month" (`perMonth` false:
 *  one month's or a window's own dollars, "income did not cover spending: −$6,300"). Money, so privacy mode masks it. */
export const shortfallText = (net: number, perMonth = true): string =>
  `income did not cover spending: ${fmtUSD0(net)}${perMonth ? " a month" : ""}`;

/** the average month's net (income − spending) over `months`; 0 for none */
export const monthlyNet = (months: readonly Pick<CashflowMonth, "income" | "spend">[]): number =>
  (months.length ? months.reduce((s, m) => s + m.income - m.spend, 0) / months.length : 0);

/** a month's savings rate in words: "12.0% saved", and below zero what it is, "spent 12.0% more than came in" (spending
 *  over income by that share of the income), never a negative "saved"; below RATE_FLOOR the month's dollars (`net`,
 *  its income − spending) in the shortfall's words. The rate is a ratio, so privacy mode leaves it; the dollars not. */
export const savedText = (rate: number | null, net: number): string =>
  (belowRateFloor(rate) ? shortfallText(net, false)
    : rate !== null && rate < 0 ? `spent ${fmtPct(-rate)} more than came in` : `${fmtPct(rate)} saved`);

/** Cash Flow's headline KPIs over the full months before the month in progress (`fullMonths`): the trailing rates over
 *  the whole history, and the best and worst month among the full months of the window the page shows (`window`; none:
 *  every full month) */
export function savingsKpis(cashflow: CashflowMonth[], inProgress: string | null, window: MonthWindow = {}): SavingsKpis {
  const full = fullMonths(cashflow, inProgress);
  const inWindow = full.filter((m) => (!window.from || m.month >= window.from) && (!window.to || m.month <= window.to));
  const rated = inWindow.filter((m) => m.savings_rate !== null)
    .sort((a, b) => (b.savings_rate as number) - (a.savings_rate as number));
  const ranked = rated.length >= 2;
  return {
    t3: full.slice(-3), t12: full.slice(-12),
    through: full.length ? full[full.length - 1].month : null,
    inWindow,
    best: ranked ? rated[0] : null, worst: ranked ? rated[rated.length - 1] : null,
  };
}

export interface IncomeStream { name: string; monthly: number }

/** average income by subcategory over the trailing `n` full months, top streams
 *  first. Each stream is divided by the months since it FIRST appeared in the
 *  window, not the whole window — so a salary that started mid-window isn't
 *  diluted by the $0 months before the job existed (e.g. a new paycheck showing
 *  up in only the last month or two). A stream that STOPS still averages down,
 *  because its trailing $0 months keep counting — that's the honest run-rate. */
export function incomeStreams(cashflow: CashflowMonth[], inProgress: string | null, n = 3, top = 4): IncomeStream[] {
  const ms = recentMonths(cashflow, inProgress, n);
  if (!ms.length) return [];
  const sums = new Map<string, number>();
  const firstSeen = new Map<string, number>();   // window index of each stream's first inflow
  ms.forEach((m, i) => {
    for (const [k, v] of Object.entries(m.income_by_subcategory)) {
      sums.set(k, (sums.get(k) ?? 0) + v);
      if (v > 0 && !firstSeen.has(k)) firstSeen.set(k, i);
    }
  });
  const all = [...sums.entries()]
    .map(([name, total]) => ({ name, monthly: total / (ms.length - (firstSeen.get(name) ?? 0)) }))
    .filter((s) => s.monthly >= 1)
    .sort((a, b) => b.monthly - a.monthly);
  if (all.length <= top) return all;
  const head = all.slice(0, top);
  const rest = all.slice(top).reduce((s, x) => s + x.monthly, 0);
  if (rest < 1) return head;
  // the fold can collide with a genuine "Other" subcategory — merge, don't
  // emit two rows named Other (it's also a React key)
  return head.some((s) => s.name === "Other")
    ? head.map((s) => (s.name === "Other" ? { ...s, monthly: s.monthly + rest } : s))
    : [...head, { name: "Other", monthly: rest }];
}

/** average monthly spend over the trailing `n` full months; null with no full month (renders "—",
 *  never a fabricated $0 burn) */
export function monthlyBurn(cashflow: CashflowMonth[], inProgress: string | null, n = 6): number | null {
  const ms = recentMonths(cashflow, inProgress, n);
  return ms.length ? ms.reduce((s, m) => s + m.spend, 0) / ms.length : null;
}

export interface MortgageSummary {
  balance: number;
  ratePct: number;
  monthlyPI: number;
  /** null when the loan terms state none (or a non-positive amount): never a fabricated $0 */
  monthlyEscrow: number | null;
  payoffYear: number;
  monthsLeft: number;
  totalInterestRemaining: number;
  dailyInterest: number;
  /** the home's value (homeValue): null when the snapshot holds no House-class row, and equity and loan-to-value are then
   *  null too ("—"), never worked out from a stand-in */
  homeValue: number | null;
  equity: number | null;
  /** null as well for a home value of 0 or less, which no loan-to-value divides by */
  ltv: number | null;
  /** the date of `balance` (the bank feed's) and the projection's start */
  asOf: string | null;
}

/** THE home's value, for every figure that reads one (equity and loan-to-value on Accounts -> Liabilities and Planning ->
 *  House, the Data page): the sum of `cur`'s House-class rows, the home as entered by hand (Settings → Manual assets, or
 *  an accounts.yaml `manual_entries` item), added
 *  in whole cents. null when the snapshot holds no House-class row, or there is no snapshot; a row of 0 is a value (0),
 *  not "none". `cur` is the snapshot the page shows, so on a rewound (?snap=) view the figure is that snapshot's own
 *  House rows. */
export function homeValue(cur: Pick<Snapshot, "rows"> | null): number | null {
  if (cur === null) return null;
  const rows = cur.rows.filter((r) => r.asset_class === HOUSE_CLASS);
  return rows.length ? rows.reduce((cents, r) => cents + Math.round(r.value * 100), 0) / 100 : null;
}

// What a page says where equity and loan-to-value would be when the snapshot holds no House-class row (homeValue null)
// is one sentence with a link to where the home is entered: components/ManualAssetsLink.tsx NoHomeValue.

/** a balance owed (positive dollars) over the home's value: null without a home value, or with one of 0 or less, which
 *  no ratio divides by */
export const loanToValue = (balance: number, home: number | null): number | null =>
  home === null || home <= 0 ? null : balance / home;

/** loan-to-value as every page prints it (Planning -> House, Accounts -> Liabilities' mortgage card): one precision, a
 *  tenth of a percent; "—" when it is unknown (no home value above zero), which the page says the reason for */
export const fmtLoanToValue = (ltv: number | null): string => fmtPct(ltv, 1);

/** the largest 1, 2 or 5 times a power of ten that is at most `x`, and never under 1 (whole dollars) */
function roundStepAtMost(x: number): number {
  if (x < 1) return 1;
  const p = 10 ** Math.floor(Math.log10(x));
  return [5 * p, 2 * p, p].find((s) => s <= x) ?? p / 10;
}

/** Planning -> House's home-value slider around a known home value `v` (homeValue): from half of `v` to twice it, the
 *  low end rounded down and the high end up to the step, so `v` is always inside and the range is never empty or
 *  inverted. The step is `step` unless that is more than a tenth of the range; then it is the largest 1, 2 or 5 times a
 *  power of ten that is not (never under 1). A value of 0 has no half-to-twice range, so the slider runs from 0 over
 *  ten steps; a negative one (a debt stated as House) runs from twice it to half of it. */
export function homeValueSlider(v: number, step = 25_000): { min: number; max: number; step: number } {
  const lo = Math.min(v / 2, v * 2);
  const hi = Math.max(v / 2, v * 2);
  if (!(hi > lo)) return { min: 0, max: 10 * step, step };
  const s = step <= (hi - lo) / 10 ? step : roundStepAtMost((hi - lo) / 10);
  // + 0 turns the -0 a negative value's rounding can give into 0
  return { min: Math.floor(lo / s) * s + 0, max: Math.ceil(hi / s) * s + 0, step: s };
}

/** What the current home costs each month: the loan terms' monthly payment plus their escrow when it is positive.
 *  null without a mortgage or a stated payment - an escrow-only figure would understate the cost, so callers draw
 *  nothing rather than a line that is wrong. */
export function primaryHomeMonthly(a: Assumptions): number | null {
  const mg = a.mortgage;
  if (mg?.monthly_payment == null) return null;
  const escrow = mg.monthly_escrow;
  return mg.monthly_payment + (escrow != null && escrow > 0 ? escrow : 0);
}

/** The mortgage balance a page prints: positive dollars owed and the date the bank feed reported them */
export interface MortgageBalance { balance: number; asOf: string | null }

/** THE mortgage balance, for every page that prints one (Accounts -> Liabilities, Planning -> House, the latest
 *  snapshot's debt on Home and Balances): assumptions.json `mortgage.balance`, the loan account's own row in the
 *  current snapshot, so the same loan never shows two balances. 0 is a paid-off loan; null when no account is the
 *  mortgage or the bank feed has not reported it yet. */
export function mortgageBalance(a: Assumptions): MortgageBalance | null {
  const mg = a.mortgage;
  return mg?.balance == null ? null : { balance: mg.balance, asOf: mg.balance_as_of };
}

/** the interest a balance accrues per day at a yearly rate (percent), the one definition every mortgage card prints */
export const dailyInterest = (balance: number, ratePct: number): number => balance * ratePct / 100 / 365;

/** A loan term a mortgage card needs and the account's accounts.yaml `loan:` block lacks: the key to add and what it
 *  shows once it is there */
export interface MissingLoanTerm { key: "rate" | "monthly_payment" | "monthly_escrow"; shows: string }

/** The terms the mortgage figures read that the block lacks, in the order rate, monthly payment, escrow. The payoff
 *  year needs both the rate and the payment, so each one's sentence says so while the other is missing too. */
export function missingLoanTerms(mg: MortgageBlock): MissingLoanTerm[] {
  const out: MissingLoanTerm[] = [];
  if (mg.rate_pct == null) {
    out.push({ key: "rate", shows: mg.monthly_payment == null ? "the interest cost" : "the interest cost and the payoff year" });
  }
  if (mg.monthly_payment == null) {
    out.push({ key: "monthly_payment", shows: mg.rate_pct == null ? "the payoff year, with the rate" : "the payoff year" });
  }
  if (mg.monthly_escrow == null) out.push({ key: "monthly_escrow", shows: "the monthly cost with taxes and insurance" });
  return out;
}

/** a date's year and month as a fractional year (Jan = .0, Feb = +1/12, ...): where the payoff year counts from */
const fractionalYear = (iso: string): number => Number(iso.slice(0, 4)) + (Number(iso.slice(5, 7)) - 1) / 12;

/** The mortgage figures Liabilities prints: the bank feed's balance run through the loan terms' rate and monthly
 *  payment. null when any of the three is unknown, the loan is paid off, or the payment does not cover a month's
 *  interest (the loan would never be paid off). The payoff year counts from the balance's as-of month, or, when the
 *  balance carries no date, from the month of `generatedAt` (manifest.generated_at, the ingest's stamp). Equity and
 *  loan-to-value read the home's value from `cur` (homeValue; the current snapshot, null or undefined when there is none). */
export function mortgageSummary(a: Assumptions, cur: Pick<Snapshot, "rows"> | null | undefined,
                                generatedAt: string): MortgageSummary | null {
  const mg = a.mortgage;
  const mb = mortgageBalance(a);
  if (mg === null || mb === null || mg.rate_pct == null || mg.monthly_payment == null || mb.balance <= 0) return null;
  const { balance, asOf } = mb;
  const ratePct = mg.rate_pct;
  const monthlyPI = mg.monthly_payment;
  if (monthlyPI <= balance * ratePct / 100 / 12) return null;
  const { payoffMonths, totalInterest } = amortize({ balance, annualRatePct: ratePct, monthlyPayment: monthlyPI });
  const startYear = fractionalYear(asOf ?? generatedAt);
  const home = homeValue(cur ?? null);
  return {
    balance, ratePct, monthlyPI,
    monthlyEscrow: mg.monthly_escrow != null && mg.monthly_escrow > 0 ? mg.monthly_escrow : null,
    payoffYear: Math.floor(startYear + payoffMonths / 12),
    monthsLeft: payoffMonths,
    totalInterestRemaining: totalInterest,
    dailyInterest: dailyInterest(balance, ratePct),
    homeValue: home,
    equity: home === null ? null : home - balance,
    ltv: loanToValue(balance, home),
    asOf,
  };
}

/** The loan account's balance history for a chart: `assumptions.mortgage.schedule` as exported (one row per snapshot
 *  date, oldest first; [] without a mortgage or before the feed reports it). Planning -> House plots it behind its
 *  projection. */
export function balanceHistory(schedule: readonly MortgageScheduleRow[] | null | undefined): MortgageScheduleRow[] {
  return [...(schedule ?? [])];
}

export interface CashRunway {
  /** liquid cash only (escrow excluded); null with no snapshot */
  cash: number | null;
  /** $/mo of essential spending; null with no window (too little history to average), never $0 a month */
  essential: number | null;
  /** null when nothing counts as essential (no history, or every row toggled off) or there is no cash figure: unknown,
   *  not 0 */
  months: number | null;
  /** spending rows behind `essential` (0 = no spending history in the window at all) */
  rowCount: number;
  /** the months the essentials average; null with too little history */
  window: RunRateWindow | null;
}

/** liquid cash ÷ essential spending - `emergencyCoverage`, the gauge's own arithmetic, so the runway
 *  KPI and the gauge agree; `overrides` are the device's essential toggles (lib/efundOverrides). The essentials average
 *  `window` (emergency.ts `emergencyWindow`). With no window (fewer than RUN_RATE_MIN_MONTHS full months, or no
 *  transaction) the cash is still the snapshot's and the runway is unknown: no row, no essentials, months null. With no
 *  snapshot (`cur` null) the cash is null and so are the months; the essentials are still the transactions'. */
export function cashRunway(vocab: CategoryVocab, txns: Transaction[], cur: Snapshot, window: RunRateWindow | null,
                           overrides?: Record<string, boolean>): CashRunway & { cash: number };
export function cashRunway(vocab: CategoryVocab, txns: Transaction[], cur: Snapshot | null, window: RunRateWindow | null,
                           overrides?: Record<string, boolean>): CashRunway;
export function cashRunway(vocab: CategoryVocab, txns: Transaction[], cur: Snapshot | null, window: RunRateWindow | null,
                           overrides: Record<string, boolean> = {}): CashRunway {
  const cash = liquidCash(cur);
  const cov = emergencyCoverage(vocab, txns, window, cash, overrides);
  return { cash, essential: cov.essential, months: cov.months, rowCount: cov.rows.length, window: cov.window };
}

/** the snapshot rows that count as liquid cash: Cash-class and liquid (an escrow balance is Cash-class but
 *  unreachable) - bank-feed accounts and any balance stated by hand, which has no feed. None without a snapshot. */
export function liquidCashRows(cur: Snapshot | null): Snapshot["rows"] {
  return cur === null ? [] : cur.rows.filter((r) => r.asset_class === "Cash" && r.liquid);
}

/** THE "cash" of the app: Planning's liquid cash and runway, Second House's cushion and Accounts -> Balances'
 *  "Cash on hand" all read this one definition (Home's "Liquid assets" is the broad figure, `liquidity`). null
 *  with no snapshot: unknown, never $0. */
export function liquidCash(cur: Snapshot): number;
export function liquidCash(cur: Snapshot | null): number | null;
export function liquidCash(cur: Snapshot | null): number | null {
  return cur === null ? null : liquidCashRows(cur).reduce((s, r) => s + r.value, 0);
}

/** A snapshot's money by what can be spent soon. Every share of it is of `assets`, never of net worth, so none can pass
 *  100%; liquid + illiquid − debts = net worth (the snapshot's total), to the cent. */
export interface Liquidity {
  /** the liquid rows above zero (cash, taxable investments): `liquid_total` less any liquid row below zero (an overdrawn
   *  account, which is a debt) */
  liquid: number;
  /** the asset rows above zero that are not liquid, by each row's own flag (the engine's illiquid classes such as
   *  retirement, a home or a vehicle entered by hand): `assets_total` less the liquid assets */
  illiquid: number;
  /** what the snapshot owes, positive dollars: its rows below zero (`liabilities_total`) */
  debts: number;
  /** the asset rows' total (`assets_total`) = liquid + illiquid */
  assets: number;
  /** liquid ÷ assets; null with no asset above zero (no share to give) */
  liquidShare: number | null;
}

const toCents = (dollars: number): number => Math.round(dollars * 100);

/** What "liquid" counts, in the words every page that names it uses: the ETL marks a row liquid unless it is a
 *  retirement or 529 account or a debt (etl/networth.py `_liquid`), and a hand-entered asset only when it says so
 *  (`liquid:`, false by default) */
export const LIQUID_MEANS = "cash and investment accounts other than retirement and 529, plus hand-entered assets marked liquid";

/** THE liquid / illiquid / debts split of a snapshot, for Balances' Illiquid tile and Liquidity card, Home's Liquid
 *  assets tile and Trends' liquidity line, in whole cents */
export function liquidity(cur: Snapshot): Liquidity {
  const overdrawn = cur.rows.filter((r) => r.liquid && r.value < 0).reduce((c, r) => c + toCents(r.value), 0);
  const liquid = toCents(cur.liquid_total) - overdrawn;
  const assets = toCents(cur.assets_total);
  return {
    liquid: liquid / 100, illiquid: (assets - liquid) / 100, debts: (0 - toCents(cur.liabilities_total)) / 100,
    assets: assets / 100, liquidShare: assets > 0 ? liquid / assets : null,
  };
}

/** The snapshot's assets (its rows above zero) by asset class or by owner, largest first: Home's allocation donut and
 *  Trends' mix, whose every share is of the same base as the liquid share (`liquidity` `assets`). By class, a debt
 *  class's row above zero (a card in credit) is its own line (`inCreditLine`), as on the balance sheet: no asset line
 *  carries a debt class's name. A debt is in no slice. */
export function assetsBy(cur: Snapshot, by: "asset_class" | "owner"): { name: string; value: number }[] {
  const sums = new Map<string, number>();
  for (const r of cur.rows) {
    if (toCents(r.value) <= 0) continue;
    const name = by === "owner" ? r.owner : assetLine(r);
    sums.set(name, (sums.get(name) ?? 0) + toCents(r.value));
  }
  return [...sums.entries()].map(([name, c]) => ({ name, value: c / 100 })).sort((a, b) => b.value - a.value);
}

/** A hand-stated value the history holds carried back: before its first value an asset is held at that value (rows'
 *  `carried_back`) */
export interface CarriedValue {
  name: string;
  /** the first stated amount, the value the earlier month ends hold */
  value: number;
  /** the day it was first stated (the carried rows' `as_of`) */
  stated: string;
  /** how many of the snapshots hold it carried back */
  snapshots: number;
}

/** the month ends of a snapshot list (oldest first): every snapshot but the newest, which is the current snapshot, no
 *  month end - it too holds a value carried back when the value is stated after the newest account balance */
const monthEnds = (snaps: readonly Snapshot[]): readonly Snapshot[] => snaps.slice(0, -1);

/** The hand-stated values the month ends of `snaps` (oldest first, the newest last) hold carried back, one per asset
 *  (by its manual id), the earliest first-stated first */
export function carriedBack(snaps: readonly Snapshot[]): CarriedValue[] {
  const by = new Map<string, CarriedValue>();
  for (const s of monthEnds(snaps)) {
    for (const r of s.rows) {
      if (!r.carried_back || r.as_of === null) continue;
      const key = r.manual_id ?? r.account;
      const had = by.get(key);
      by.set(key, had ? { ...had, snapshots: had.snapshots + 1 } : { name: r.account, value: r.value, stated: r.as_of, snapshots: 1 });
    }
  }
  return [...by.values()].sort((a, b) => (a.stated < b.stated ? -1 : a.stated > b.stated ? 1 : 0));
}

/** how many month ends of `snaps` (every snapshot but the newest) hold at least one hand-stated value carried back: what
 *  Data's caveat counts */
export const snapshotsCarrying = (snaps: readonly Snapshot[]): number =>
  monthEnds(snaps).filter((s) => s.rows.some((r) => r.carried_back)).length;

/** Home's line under its history, one clause per carried asset (the first two, then how many more): which asset, at
 *  which first stated amount (a masked figure) and since when; null with none */
export function carriedBackLine(values: readonly CarriedValue[]): string | null {
  if (values.length === 0) return null;
  const clause = (c: CarriedValue) =>
    `${c.name} is shown at its first stated amount (${fmtUSD0(c.value)}, stated ${fmtDate(c.stated)}) in earlier month ends`;
  const named = values.slice(0, 2).map(clause).join("; ");
  const more = values.length - 2;
  if (more <= 0) return `${named}.`;
  return more === 1
    ? `${named}; and 1 more hand-entered asset, at its first stated amount in month ends before it was first stated.`
    : `${named}; and ${more} more hand-entered assets, each at its first stated amount in month ends before it was first stated.`;
}

/** Who holds or values a row, in words: its institution, or "entered by hand" for a value stated by hand that names
 *  none (an asset entered in the app carries no institution) */
export const rowHolder = (r: Pick<AssetRow, "institution" | "manual_id">): string =>
  r.institution !== "" ? r.institution : r.manual_id !== null ? "entered by hand" : "—";

/** A live margin account's two figures in a snapshot: its positions (the account's rows beside its debt row) and the
 *  margin loan that debt row owes (positive dollars). null for an account the snapshot holds as one row (a brokerage at
 *  the feed's own balance, as every month end holds it) or whose remainder owes nothing (a cash remainder). */
export function marginBreakdown(cur: Snapshot | null, accountId: string): { positions: number; loan: number } | null {
  if (cur === null) return null;
  const rows = cur.rows.filter((r) => r.account_id === accountId);
  const owed = rows.filter((r) => DEBT_CLASSES.has(r.asset_class) && r.value < 0);
  const positions = rows.filter((r) => !owed.includes(r));
  if (owed.length === 0 || positions.length === 0) return null;
  return { positions: positions.reduce((c, r) => c + toCents(r.value), 0) / 100,
           loan: -owed.reduce((c, r) => c + toCents(r.value), 0) / 100 };
}

/** a row below zero in an asset class, as its own liability line: the account named with why it is owed */
export const belowZeroLine = (r: Pick<AssetRow, "account" | "asset_class">): string =>
  `${r.account} (${r.asset_class === "Cash" ? "overdrawn" : "below zero"})`;

/** a debt class's row above zero (a card in credit: the issuer owes the household), as its own asset line: the account
 *  named with why it is held - the mirror of `belowZeroLine` */
export const inCreditLine = (r: Pick<AssetRow, "account">): string => `${r.account} (in credit)`;

/** the asset line a row above zero is summed in: its class, or a debt class's row its own line (`inCreditLine`) */
const assetLine = (r: Pick<AssetRow, "account" | "asset_class">): string =>
  DEBT_CLASSES.has(r.asset_class) ? inCreditLine(r) : r.asset_class;

/** THE snapshot by sign, the one balance sheet every page reads, in whole cents by line: each row above zero is an asset,
 *  summed by its class, a debt class's row above zero (a card in credit) its own line (`inCreditLine`); each row below
 *  zero is a liability, a debt class's rows summed by class and a row below zero in an asset class its own line
 *  (`belowZeroLine`: an overdrawn account, a hand-stated value below zero). A debt class's row of exactly zero keeps its
 *  class on the liability side, at 0. With the export's rows, the two sides total its `assets_total` and
 *  −`liabilities_total`. */
function bySign(cur: Snapshot): { assets: Map<string, number>; liabilities: Map<string, number> } {
  const assets = new Map<string, number>(), liabilities = new Map<string, number>();
  for (const r of cur.rows) {
    const c = toCents(r.value);
    if (c > 0) assets.set(assetLine(r), (assets.get(assetLine(r)) ?? 0) + c);
    else if (DEBT_CLASSES.has(r.asset_class)) liabilities.set(r.asset_class, (liabilities.get(r.asset_class) ?? 0) - c);
    else if (c < 0) liabilities.set(belowZeroLine(r), (liabilities.get(belowZeroLine(r)) ?? 0) - c);
  }
  return { assets, liabilities };
}

/** What a debt class's balance-sheet line is called, so the rows other pages call credit cards are called that here too:
 *  the line holds the class's rows at or below zero (`bySign`); when every one is a credit-card account (`cardIds`, see
 *  `cardAccountIds`) it reads "Credit cards", when some are "<class>, credit cards included", when none the class in
 *  sentence case ("Other debt"). Any other line (an account below zero) keeps its own name. */
export function debtLineLabel(cur: Snapshot, line: string, cardIds: ReadonlySet<string>): string {
  if (!DEBT_CLASSES.has(line)) return line;
  const rows = cur.rows.filter((r) => r.asset_class === line && toCents(r.value) <= 0);
  const cards = rows.filter((r) => r.account_id !== null && cardIds.has(r.account_id)).length;
  const name = line.charAt(0) + line.slice(1).toLowerCase();
  return cards === 0 ? name : cards === rows.length ? "Credit cards" : `${name}, credit cards included`;
}

/** the snapshot's debt classes that carry a balance (a row below zero), in DEBT_CLASSES order - what a "Debt" tile may
 *  name, so its sub-line can never mention a liability the snapshot does not hold */
export function debtClassesOwed(cur: Snapshot): string[] {
  const owed = bySign(cur).liabilities;
  return [...DEBT_CLASSES].filter((k) => (owed.get(k) ?? 0) > 0);
}

export interface NetWorthDebt {
  /** dollars owed (positive): every row below zero the snapshot subtracts from net worth (`bySign`'s liabilities) -
   *  mortgage, margin, any other debt class, an account below zero */
  owed: number;
  /** of which the mortgage: the one mortgage balance (mortgageBalance) on the latest snapshot, the snapshot's own row on a rewound one */
  mortgage: number;
  /** what is owed, named: the debt classes by their balance-sheet line (`debtLineLabel`: "credit cards" for a class that
   *  holds only cards), lower-case, in DEBT_CLASSES order, then each row below zero in an asset class by its line ("Joint
   *  Checking (overdrawn)") */
  classes: string[];
  /** the snapshot holds a row for at least one credit-card account (only when the household counts cards in net worth) */
  cardRows: boolean;
}

/** the ids of the accounts of kind credit-card: a snapshot row is a card row when its account is one of these - never
 *  by class, since a loan is "Other Debt" too */
export function cardAccountIds(accounts: readonly { id: string; kind: string }[]): Set<string> {
  return new Set(accounts.filter((x) => x.kind === "credit-card").map((x) => x.id));
}

/** the snapshot's rows that belong to a credit-card account */
function cardRows(cur: Snapshot, cardIds: ReadonlySet<string>): Snapshot["rows"] {
  return cur.rows.filter((r) => r.account_id !== null && cardIds.has(r.account_id));
}

/** The debt in net worth, for Home's tile, Balances' Total liabilities and its Liquidity card's Debts: every row below
 *  zero (`bySign`), with the Mortgage line read through the same `mortgageBalance` as Accounts -> Liabilities and
 *  Planning -> House so no page can show a different mortgage. `latest` is false on a rewound (?snap=) view: an older
 *  snapshot's own row is the only honest figure for its date (the block's balance is the current snapshot's). Net worth
 *  itself stays the snapshot's total. A snapshot holds card balances only when the household counts cards in net worth:
 *  they are then rows of the "Other Debt" class, so that class carries them once, and `cardRows` says whether any are
 *  there (`cardIds`: the card accounts, see `cardAccountIds`). null with no snapshot: no debt figure, never $0. */
export function netWorthDebt(a: Assumptions, cur: Snapshot, latest: boolean, cardIds?: ReadonlySet<string>): NetWorthDebt;
export function netWorthDebt(a: Assumptions, cur: Snapshot | null, latest: boolean,
                             cardIds?: ReadonlySet<string>): NetWorthDebt | null;
export function netWorthDebt(a: Assumptions, cur: Snapshot | null, latest: boolean,
                             cardIds: ReadonlySet<string> = new Set()): NetWorthDebt | null {
  if (cur === null) return null;
  const lines = bySign(cur).liabilities;
  const snapMortgage = (lines.get("Mortgage") ?? 0) / 100;
  const mb = latest && snapMortgage > 0 ? mortgageBalance(a) : null;
  const mortgage = mb ? mb.balance : snapMortgage;
  const rest = [...lines].filter(([k]) => k !== "Mortgage").reduce((c, [, v]) => c + v, 0) / 100;
  const debtClasses = new Set<string>(DEBT_CLASSES);
  return {
    owed: mortgage + rest, mortgage,
    classes: [...debtClassesOwed(cur).map((k) => debtLineLabel(cur, k, cardIds).toLowerCase()),
      ...[...lines].filter(([k, v]) => !debtClasses.has(k) && v > 0).map(([k]) => k)],
    cardRows: cardRows(cur, cardIds).length > 0,
  };
}

/** what a debt tile says it is, in one place: Home's "Debt in net worth" and Balances' balance sheet print this same line.
 *  `includeCards` is the household's include_credit_cards: off, cards are never in the snapshot and the line says so
 *  (to a household with a credit-card account: `hasCards`; one with none has no card to leave out); on, it says they are
 *  included when the snapshot holds a card row and no class it names says so already (`debtLineLabel`), says "no credit
 *  card" to a household with no credit-card account (there is no card balance to be missing), and says nothing about
 *  cards when cards it has hold no row yet. */
export function netWorthDebtNote(d: Pick<NetWorthDebt, "classes"> & { cardRows?: boolean }, includeCards = false,
                                 hasCards = true): string {
  const classes = d.classes.join(" + ");
  if (!includeCards) return `${classes || "no debt in this snapshot"}${hasCards ? " · credit cards not included" : ""}`;
  const owed = classes || "no debt in this snapshot";
  if (!hasCards) return `${owed} · no credit card`;
  // a line that names the credit cards already says they are in it
  return d.cardRows && !/credit cards/.test(owed) ? `${owed} · credit cards included` : owed;
}

export interface DebtOverview {
  /** the one mortgage balance (mortgageBalance), null when there is no mortgage account or the bank feed has not
   *  reported it yet */
  mortgage: MortgageBalance | null;
  /** a brokerage's margin loan: the snapshot's Margin rows below zero, 0 when it holds none */
  margin: number;
  /** every other row below zero the snapshot carries: another debt class (a family loan, say) or an account below zero */
  other: number;
  /** card balances owed, null when they are not available (never 0 for "unknown"): the balance history, or with cards in
   *  the snapshot the snapshot's card rows below zero (a card in credit is an asset, as on the balance sheet) */
  cards: number | null;
  /** the sum of what is known - `parts` names it in words ("mortgage", "credit cards", "margin", "other debt"),
   *  `unknown` names what is missing from it by key ("mortgage", "cards") */
  total: number;
  parts: string[];
  unknown: string[];
}

/** Accounts -> Liabilities' answer to "what do we owe": mortgage + cards + margin (+ any other debt class), each
 *  from its one source; a part nobody reported is named in `unknown` and left out, never counted as $0 (a household
 *  with no mortgage account has no mortgage to be unknown).
 *  Cards come from `cards` (the balance history's figure) unless the household counts them in net worth
 *  (`includeCards`): then the snapshot's rows for the `cardIds` accounts are the cards, `cards` is ignored, and `other`
 *  leaves those rows out so card debt is counted once. Card accounts with no row in the snapshot make cards unknown.
 *  null with no snapshot: the margin and other debt classes are the snapshot's, so no total is known. */
export function debtOverview(a: Assumptions, cur: Snapshot, cardsFromHistory: number | null,
                             includeCards?: boolean, cardIds?: ReadonlySet<string>): DebtOverview;
export function debtOverview(a: Assumptions, cur: Snapshot | null, cardsFromHistory: number | null,
                             includeCards?: boolean, cardIds?: ReadonlySet<string>): DebtOverview | null;
export function debtOverview(a: Assumptions, cur: Snapshot | null, cardsFromHistory: number | null,
                             includeCards = false, cardIds: ReadonlySet<string> = new Set()): DebtOverview | null {
  if (cur === null) return null;
  const mortgage = mortgageBalance(a);
  const cardRowList = includeCards ? cardRows(cur, cardIds) : [];
  // what rows below zero owe, in whole cents; each card row is in `cards` and nowhere else
  const owedBy = (rows: readonly AssetRow[]) => rows.reduce((c, r) => c - Math.min(0, toCents(r.value)), 0) / 100;
  const cards = !includeCards ? cardsFromHistory : cardRowList.length > 0 ? owedBy(cardRowList) : null;
  const rest = cur.rows.filter((r) => !cardRowList.includes(r) && r.asset_class !== "Mortgage");
  const margin = owedBy(rest.filter((r) => r.asset_class === "Margin"));
  const other = owedBy(rest.filter((r) => r.asset_class !== "Margin"));
  const parts = [
    ...(mortgage ? ["mortgage"] : []), ...(cards !== null ? ["credit cards"] : []),
    ...(margin > 0 ? ["margin"] : []), ...(other > 0 ? ["other debt"] : []),
  ];
  return {
    mortgage, margin, other, cards, parts,
    unknown: [...(mortgage || a.mortgage === null ? [] : ["mortgage"]), ...(cards === null ? ["cards"] : [])],
    total: (mortgage?.balance ?? 0) + (cards ?? 0) + margin + other,
  };
}

export interface SheetLine { name: string; value: number }

export interface BalanceSheet {
  assets: SheetLine[];
  liabilities: SheetLine[];
  totalAssets: number;
  totalLiabilities: number;
  net: number;
}

/** The household balance sheet of a snapshot, by sign (`bySign`): its rows above zero by class, its rows below zero by
 *  debt class or as their own line. `mortgageOwed` replaces the Mortgage liability line with the one mortgage balance
 *  (netWorthDebt) when the snapshot owes on that class, so Total liabilities is the debt in net worth. */
export function balanceSheet(cur: Snapshot, mortgageOwed?: number): BalanceSheet {
  const { assets: a, liabilities: l } = bySign(cur);
  if (mortgageOwed !== undefined && (l.get("Mortgage") ?? 0) > 0) l.set("Mortgage", toCents(mortgageOwed));
  const lines = (m: Map<string, number>) => [...m].map(([name, c]) => ({ name, value: c / 100 })).sort((x, y) => y.value - x.value);
  const sum = (m: Map<string, number>) => [...m.values()].reduce((s, c) => s + c, 0);
  const totalAssets = sum(a) / 100, totalLiabilities = sum(l) / 100;
  return { assets: lines(a), liabilities: lines(l), totalAssets, totalLiabilities, net: (sum(a) - sum(l)) / 100 };
}

export interface PnL {
  label: string;
  monthCount: number;
  income: number;
  spend: number;
  invested: number;
  net: number;
  rate: number | null;
  incomeBySub: SheetLine[];
  spendByCat: SheetLine[];
}

export type PlPreset = "3m" | "6m" | "12m" | "ytd";

const PL_MONTHS: Record<Exclude<PlPreset, "ytd">, number> = { "3m": 3, "6m": 6, "12m": 12 };

/** how many months a trailing income-statement preset asks for (YTD asks for the year so far) */
export const plPresetMonths = (preset: Exclude<PlPreset, "ytd">): number => PL_MONTHS[preset];

/**
 * [fromYm, toYm] month bounds for an income-statement preset ending at
 * `lastFull`, clamped to `floor` (the coverage floor, agg.ts coverageFloor) so
 * long windows never reach into months where too few accounts report. A null
 * floor (no month in coverage yet) has no window (null): every month precedes the coverage.
 */
export function plWindow(preset: PlPreset, lastFull: string, floor: string): [string, string];
export function plWindow(preset: PlPreset, lastFull: string, floor: string | null): [string, string] | null;
export function plWindow(preset: PlPreset, lastFull: string, floor: string | null): [string, string] | null {
  if (floor === null) return null;
  const fromYm = preset === "ytd"
    ? `${lastFull.slice(0, 4)}-01`
    : trailingWindow(lastFull, PL_MONTHS[preset])[0].slice(0, 7);
  return [fromYm < floor ? floor : fromYm, lastFull];
}

/** The last full calendar year in the cash-flow data (YYYY): the greatest year whose twelve months are all present
 *  and none after `lastFull` (the last full month). Null when no year is whole. */
export function lastFullYear(cashflow: Pick<CashflowMonth, "month">[], lastFull: string): string | null {
  const months = new Set(cashflow.map((m) => m.month));
  const years = [...new Set(cashflow.map((m) => m.month.slice(0, 4)))].sort().reverse();
  return years.find((y) => `${y}-12` <= lastFull
    && Array.from({ length: 12 }, (_, i) => `${y}-${String(i + 1).padStart(2, "0")}`).every((m) => months.has(m))) ?? null;
}

/** income statement over [fromYm, toYm] inclusive (YYYY-MM bounds) */
export function pnl(cashflow: CashflowMonth[], fromYm: string, toYm: string, label = ""): PnL {
  const ms = cashflow.filter((m) => m.month >= fromYm && m.month <= toYm);
  const incomeBySub = new Map<string, number>();
  const spendByCat = new Map<string, number>();
  let income = 0, spend = 0, invested = 0;
  for (const m of ms) {
    income += m.income;
    spend += m.spend;
    invested += m.invested;
    for (const [k, v] of Object.entries(m.income_by_subcategory))
      incomeBySub.set(k, (incomeBySub.get(k) ?? 0) + v);
    for (const [k, v] of Object.entries(m.by_category))
      spendByCat.set(k, (spendByCat.get(k) ?? 0) + v.total);
  }
  const toLines = (m: Map<string, number>) =>
    [...m.entries()].map(([name, value]) => ({ name, value }))
      .filter((l) => Math.abs(l.value) >= 1)
      .sort((a, b) => b.value - a.value);
  return {
    label, monthCount: ms.length, income, spend, invested,
    net: income - spend, rate: savingsRate(ms),
    incomeBySub: toLines(incomeBySub), spendByCat: toLines(spendByCat),
  };
}

export interface SankeyData {
  nodes: { name: string }[];
  links: { source: string; target: string; value: number }[];
}

/** money-flow graph: income streams -> household -> categories + saved/invested. The `topCats` largest categories and
 *  the `topStreams` largest income streams are nodes of their own; the rest of each side is one "Other spending" /
 *  "Other income" node, so neither column outgrows the chart's labels.
 *  A node's name is its identity in ECharts, so every name is unique: the household and the graph's own words keep
 *  theirs, a category that shares one is suffixed " (spending)", and an income stream that shares any is suffixed
 *  " (income)". One shared name would join two columns into a cycle, which ECharts' sankey refuses by throwing. */
export function sankeyFlows(p: PnL, topCats = 10, topStreams = 10): SankeyData {
  const HUB = "Household";
  /** a flow into the household (`into`) or out of it; `own` for the graph's own words, else a taxonomy name */
  type Flow = { name: string; value: number; into: boolean; own: boolean };
  const flows: Flow[] = [];
  const streams = p.incomeBySub.filter((s) => s.value > 0);
  for (const s of streams.slice(0, topStreams)) flows.push({ name: s.name, value: s.value, into: true, own: false });
  const otherIncome = streams.slice(topStreams).reduce((sum, s) => sum + s.value, 0);
  if (otherIncome >= 1) flows.push({ name: "Other income", value: otherIncome, into: true, own: true });
  const cats = p.spendByCat.filter((c) => c.value > 0);
  const head = cats.slice(0, topCats);
  // remainder vs total spend (tail categories + anything uncategorized),
  // so hub inflow equals outflow by construction
  const rest = p.spend - head.reduce((s, c) => s + c.value, 0);
  for (const c of head) flows.push({ name: c.name, value: c.value, into: false, own: false });
  if (rest >= 1) flows.push({ name: "Other spending", value: rest, into: false, own: true });
  if (p.invested > 0) flows.push({ name: "Invested", value: p.invested, into: false, own: true });
  const savedCash = p.income - p.spend - Math.max(0, p.invested);
  if (savedCash > 1) flows.push({ name: "Saved (cash)", value: savedCash, into: false, own: true });
  else if (savedCash < -1) flows.push({ name: "Drawn from savings", value: -savedCash, into: true, own: true });

  // the household and the graph's own words first, then the categories, then the streams: a later name that is taken
  // takes its side's suffix until it is free
  const taken = new Set<string>([HUB, ...flows.filter((f) => f.own).map((f) => f.name)]);
  const named = new Map<Flow, string>(flows.filter((f) => f.own).map((f) => [f, f.name]));
  for (const f of [...flows.filter((f) => !f.own && !f.into), ...flows.filter((f) => !f.own && f.into)]) {
    let name = f.name;
    while (taken.has(name)) name = `${name} ${f.into ? "(income)" : "(spending)"}`;
    taken.add(name);
    named.set(f, name);
  }
  const nodes = [HUB, ...flows.map((f) => named.get(f)!)];
  const links = flows.map((f) => {
    const name = named.get(f)!;
    return { source: f.into ? name : HUB, target: f.into ? HUB : name, value: Math.round(f.value) };
  });
  return { nodes: nodes.map((name) => ({ name })), links };
}

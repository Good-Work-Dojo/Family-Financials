// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The Planning header strip's two numbers: the bear-case net worth and the lumpy bills.
import type { RecurringItem, Snapshot } from "../data/types";
import { obligationsInMonths } from "./obligations";

/** a market drawdown as percentages, one per kind of asset: what the stress-test card's sliders hold */
export type Shocks = { eq: number; crypto: number; priv: number; house: number };

/** the bear case: stocks / retirement / 529 / venture −30%, crypto and private stock −50%, the house and precious metals unmoved. The
 *  stress-test card starts its sliders here and the Planning header's bear-case KPI is this exact case. */
export const SHOCK_DEFAULTS: Shocks = { eq: -30, crypto: -50, priv: -50, house: 0 };

/** the classes a shock moves, each with the slider that moves it, in the order a sentence names them (a list: an object's
 *  keys would put "529" first) */
const SHOCK_CLASSES: readonly (readonly [string, keyof Shocks])[] = [
  ["Stocks/ETF", "eq"], ["Retirement", "eq"], ["529", "eq"], ["Real Estate/Venture", "eq"],
  ["Crypto", "crypto"], ["Private Stock", "priv"], ["House", "house"],
];
const SHOCK_OF_CLASS: Readonly<Record<string, keyof Shocks>> = Object.fromEntries(SHOCK_CLASSES);

/** the asset classes the bear case (SHOCK_DEFAULTS) moves, from the same table the calculation reads: what a sentence
 *  names when a household holds none of them */
export function bearCaseClasses(): string[] {
  return SHOCK_CLASSES.filter(([, kind]) => SHOCK_DEFAULTS[kind] !== 0).map(([cls]) => cls);
}

/** net worth after shocking each asset class by its percentage; cash, precious metals and every debt class never move.
 *  null with no snapshot: no net worth to shock. */
export function shockedTotal(current: Snapshot, shocks: Shocks): number;
export function shockedTotal(current: Snapshot | null, shocks: Shocks): number | null;
export function shockedTotal(current: Snapshot | null, shocks: Shocks): number | null {
  if (current === null) return null;
  return Object.entries(current.by_class).reduce((s, [k, v]) => {
    const kind = SHOCK_OF_CLASS[k];                          // cash, precious metals and every debt class have none: they never move
    return s + v * (1 + (kind ? shocks[kind] : 0) / 100);
  }, 0);
}

/** Does the snapshot hold anything a shock can move (a class the sliders reach, with a value)? Without it a market fall
 *  changes nothing: there is no loss to show and no warning tone to give. With `shocks`, only a class whose slider is
 *  not at 0% counts: the sliders as set move nothing else (a home held while its slider stays at 0%). */
export function holdsShockable(current: Snapshot, shocks?: Shocks): boolean {
  return Object.entries(current.by_class).some(([k, v]) => SHOCK_OF_CLASS[k] !== undefined && v !== 0
    && (shocks === undefined || shocks[SHOCK_OF_CLASS[k]] !== 0));
}

/** net worth after the fixed bear case (the interactive sliders live in card-market-shock); null with no snapshot */
export function bearCaseTotal(current: Snapshot): number;
export function bearCaseTotal(current: Snapshot | null): number | null;
export function bearCaseTotal(current: Snapshot | null): number | null {
  return shockedTotal(current, SHOCK_DEFAULTS);
}

/** Σ of lumpy obligations (annual/quarterly renewals; `isLumpy` in lib/obligations) dated in `months` (the twelve
 *  after the month in progress, `obligationMonths`: Spending → Recurring's chart's); null when none falls in them: a
 *  sum over nothing is no "$0" */
export function lumpyBillsTotal(recurring: readonly RecurringItem[], months: readonly string[]): number | null {
  const lumpy = obligationsInMonths(recurring, months).filter((o) => o.lumpy);
  return lumpy.length ? lumpy.reduce((s, o) => s + o.amount, 0) : null;
}

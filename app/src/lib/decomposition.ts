// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Net-worth change decomposition: between two asset snapshots, split Δ into
//   cash savings        Σ(income − spend) from monthly cashflow in the window
//   retirement          401(k)+match from payroll facts (when configured)
//   home                Δ of the House class (owner-estimate — usually flat)
//   debt                Δ of Mortgage/Margin/Other Debt classes (stored negative,
//                       so paying down debt is a positive contribution), each
//                       snapshot's debt on one footing (the fold below); the bar
//                       is named by its sign (`debtBarLabel`)
//   market & other      the residual — market moves, gifts, valuation changes,
//                       and any cashflow the coverage window missed. Labeled
//                       exactly that; it is a residual, never "market".
// Components sum to Δtotal by construction.
//
// Honesty rules (hard, not cosmetic):
// - Like with like: a month end holds a margin account as one row at the feed's
//   balance (net of its loan), the newest snapshot as its positions plus a debt
//   row for the loan. Both ends are read on the month ends' footing
//   (`onMonthEndFooting`, lib/comparableSnapshots.ts), so the loan is in neither
//   end's debt and its change lands with the account's, in the residual;
//   `marginFolded` flags the footnote.
// - Mortgage-boundary: when the window crosses the mortgage_included flip
//   (`mortgageGap`), the Mortgage class Δ is an accounting artifact (the whole balance
//   appearing from nothing). It is excluded from the debt bar and replaced by the
//   paydown the mortgage schedule (the loan account's balance at each snapshot date
//   it has one) shows inside the window, so the artifact alone lands in the
//   residual; `mortgageExcluded` flags the footnote.
// - Coverage: months without full account coverage understate savings and
//   inflate the residual; `coverageIncomplete` flags the badge.
import type { Assumptions, CashflowMonth, Snapshot } from "../data/types";
import { onMonthEndFooting } from "./comparableSnapshots";
import { fmtDate, fmtMonth, fmtMonthAxis } from "./format";
import { monthlyRetirement } from "./payroll";
import type { PayrollPhase } from "./prefs";
import { latestOnOrBefore } from "./stats";
import { DEBT_CLASSES, HOUSE_CLASS } from "./tokens";

// tokens.DEBT_CLASSES preserves the same insertion order (Mortgage, Margin,
// Other Debt), so the float sum feeding the residual is bit-identical
const ALL_DEBT_CLASSES = [...DEBT_CLASSES];

export interface Decomposition {
  from: Snapshot;
  to: Snapshot;
  delta: number;
  cashSavings: number;
  retirement: number;
  home: number;
  /** the change in debt as a contribution to net worth: positive when debt fell */
  debt: number;
  market: number;              // residual
  months: number;              // cashflow months inside the window
  coverageIncomplete: boolean;
  mortgageExcluded: boolean;   // boundary rule fired (schedule Δ used instead)
  /** a margin account's loan was folded into its account at either end (`onMonthEndFooting`) */
  marginFolded: boolean;
}

/** the debt bar's name, by the sign of its change: "Debt paid down" when debt fell, "Debt added" when it rose, "Debt
 *  unchanged" when it rounds to no whole dollar (as the bar's own figure does, half a dollar away from zero);
 *  `starred` adds the footnote's mark */
export function debtBarLabel(debt: number, starred = false): string {
  const whole = Math.sign(debt) * Math.round(Math.abs(debt));
  const name = whole > 0 ? "Debt paid down" : whole < 0 ? "Debt added" : "Debt unchanged";
  return starred ? `${name}*` : name;
}

/** Months strictly after the from-snapshot's month, up to and including the
 *  to-snapshot's month — snapshot dates are mid-month sometimes, so this is a
 *  deliberate month-granular approximation (the residual absorbs the edges). */
function windowMonths(cashflow: CashflowMonth[], from: Snapshot, to: Snapshot):
    CashflowMonth[] {
  const fromYm = from.snapshot_date.slice(0, 7);
  const toYm = to.snapshot_date.slice(0, 7);
  return cashflow.filter((m) => m.month > fromYm && m.month <= toYm);
}

function classSum(s: Snapshot, classes: string[]): number {
  return classes.reduce((sum, c) => sum + (s.by_class[c] ?? 0), 0);
}

/** The mortgage schedule's principal Δ inside the window (positive = paydown): its first row after the
 *  from-snapshot's date minus its last on or before the to-snapshot's; 0 with fewer than two such rows. Its rows
 *  are the loan account's own balances, each on a date where it is in the snapshot, so the Δ is paydown the window
 *  saw after the account first appears, never the balance appearing. */
function schedulePrincipalDelta(assumptions: Assumptions, from: Snapshot,
                                to: Snapshot): number {
  const sched = assumptions.mortgage?.schedule ?? [];
  const inWindow = sched
    .filter((r) => r.date > from.snapshot_date && r.date <= to.snapshot_date
                   && typeof r.principal_balance === "number")
    .sort((a, b) => a.date.localeCompare(b.date));
  if (inWindow.length < 2) return 0;
  return inWindow[0].principal_balance - inWindow[inWindow.length - 1].principal_balance;
}

/** Net worth's change from `from` to `to`, in bars (the module comment). The debt bar reads the debt classes; an account
 *  below zero (an overdrawn checking account) moves with cash savings, whose bar already holds its movement, so its
 *  part below zero is not counted as debt a second time. */
export function decompose(from: Snapshot, to: Snapshot, cashflow: CashflowMonth[],
                          assumptions: Assumptions,
                          phases: PayrollPhase[] = []): Decomposition {
  const months = windowMonths(cashflow, from, to);
  const cashSavings = months.reduce((s, m) => s + m.income - m.spend, 0);
  const retirement = months.reduce((s, m) => s + monthlyRetirement(phases, m.month), 0);
  const home = (to.by_class[HOUSE_CLASS] ?? 0) - (from.by_class[HOUSE_CLASS] ?? 0);

  // the two ends on the month ends' footing: a live margin account's loan inside its account at both
  const a = onMonthEndFooting(from), b = onMonthEndFooting(to);
  const crossesBoundary = from.mortgage_included !== to.mortgage_included;
  let debt: number;
  let mortgageExcluded = false;
  if (crossesBoundary) {
    // Mortgage Δ across the flip is bookkeeping, not wealth change
    debt = classSum(b, ["Margin", "Other Debt"]) - classSum(a, ["Margin", "Other Debt"])
      + schedulePrincipalDelta(assumptions, from, to);
    mortgageExcluded = true;
  } else {
    debt = classSum(b, ALL_DEBT_CLASSES) - classSum(a, ALL_DEBT_CLASSES);
  }

  const delta = to.total - from.total;
  const market = delta - cashSavings - retirement - home - debt;
  return {
    from, to, delta, cashSavings, retirement, home, debt, market,
    months: months.length,
    coverageIncomplete: months.length === 0 || months.some((m) => !m.full_coverage),
    mortgageExcluded,
    marginFolded: a !== from || b !== to,
  };
}

/** Latest snapshot on/before the target date, else the earliest snapshot. */
export function snapshotOnOrBefore(snaps: Snapshot[], date: string): Snapshot | null {
  return latestOnOrBefore(snaps, date, (s) => s.snapshot_date) ?? snaps[0] ?? null;
}

/** The mortgage owed (positive dollars) at the earliest snapshot that carries the mortgage
 *  (`mortgage_included`), else null. Sizes the "snapshots before the mortgage data don't
 *  subtract it" footnote from the data instead of a literal. Never a guess: no included
 *  snapshot, or one with no negative Mortgage balance, yields null. */
export function firstMortgageBalance(snaps: Snapshot[]): number | null {
  let first: Snapshot | null = null;
  for (const s of snaps) {
    if (s.mortgage_included && (first === null || s.snapshot_date < first.snapshot_date)) first = s;
  }
  const owed = first?.by_class["Mortgage"];
  return owed !== undefined && owed < 0 ? -owed : null;
}

/** Snapshots that leave the mortgage out (`mortgage_included: false`): the ETL books it from the loan account's first
 *  balance on, so each of them pre-dates the mortgage data. `since` is where that data starts - the mortgage schedule's
 *  first row, else the earliest snapshot that carries the mortgage - and `sinceLabel` its month in words ("Jun 2020"), or
 *  "the mortgage data" when neither exists. The one source for every "† … don't subtract the mortgage" note, so the
 *  copy names the data's own month instead of a literal one; null once every snapshot subtracts the mortgage, and
 *  then no page prints the note. */
export interface MortgageGap { excluded: number; since: string | null; sinceLabel: string }
export function mortgageGap(snaps: Snapshot[], schedule: readonly { date: string }[] = []): MortgageGap | null {
  const excluded = snaps.filter((s) => !s.mortgage_included).length;
  if (excluded === 0) return null;
  const dates = schedule.length > 0 ? schedule.map((r) => r.date)
    : snaps.filter((s) => s.mortgage_included).map((s) => s.snapshot_date);
  const since = dates.length > 0 ? dates.reduce((a, b) => (b < a ? b : a)) : null;
  return { excluded, since, sinceLabel: since ? fmtMonth(since.slice(0, 7)) : "the mortgage data" };
}

/** the longest line of a phone axis name */
const PHONE_LINE = 8;

/** The waterfall's x-axis names, Start + each component + Now. A desktop column is wide enough for
 *  "Start (Jul 31, 2025)"; a 390px phone gives each of five to seven columns ~40-55px, so there each
 *  name wraps onto short lines of at most PHONE_LINE characters ("Cash\nsavings", "Debt\npaid\ndown", a
 *  long single word hyphenated) and the edges carry the month only ("Now\nOct ’26") - every column
 *  named, nothing hidden or clipped. */
export function waterfallLabels(components: string[], fromDate: string, toDate: string, phone: boolean): string[] {
  if (!phone) return [`Start (${fmtDate(fromDate)})`, ...components, `Now (${fmtDate(toDate)})`];
  const lines = (s: string): string[] => {
    const i = s.indexOf(" ");
    if (i > 0) {
      const rest = s.slice(i + 1);
      return [s.slice(0, i), ...(rest.length > PHONE_LINE ? lines(rest) : [rest])];
    }
    return s.length > 7 ? [`${s.slice(0, 6)}-`, s.slice(6)] : [s];
  };
  return [`Start\n${fmtMonthAxis(fromDate.slice(0, 7))}`, ...components.map((c) => lines(c).join("\n")),
    `Now\n${fmtMonthAxis(toDate.slice(0, 7))}`];
}

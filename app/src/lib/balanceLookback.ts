// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Per-account balance performance lookbacks (1w/1mo/3mo/6mo/1y) over the
// balances_timeseries points. Rules that keep this honest while a bank feed's
// history is still weeks deep:
// - Targets anchor to the LATEST POINT's date, not today — a stale export must
//   not silently shrink the windows ("1w" means 1 week before the balance
//   you're looking at, matching the row's "as of" date).
// - Anchor = latest point on/before the target, hidden entirely when none
//   exists, and gated when the anchor overshoots the target by more than half
//   the window (a "1w" row silently anchored 3 weeks back would lie). The
//   anchor's real date is always displayed, so even tolerated overshoot is
//   visible. The gate self-retires as history densifies.
// - Δ = latest − anchor on institution-signed balances. Cards and loans are
//   stored negative, so paydown is a positive Δ: green when Δ > 0 uniformly
//   means "net worth improved" — no per-kind sign flipping.
import type { BalancePoint } from "../data/types";
import { latestOnOrBefore } from "./stats";

const LOOKBACKS = [
  { label: "1w", days: 7 },
  { label: "1mo", days: 30 },
  { label: "3mo", days: 91 },
  { label: "6mo", days: 182 },
  { label: "1y", days: 365 },
] as const;

export interface LookbackRow {
  label: string;
  days: number;
  anchorDate: string;
  delta: number;
  /** Δ / |anchor balance|; null when the anchor balance is 0 */
  pct: number | null;
}

function addDays(iso: string, days: number): string {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Latest point with date ≤ target (points are date-sorted ascending). */
function anchorOnOrBefore(points: BalancePoint[], target: string): BalancePoint | null {
  return latestOnOrBefore(points, target, (p) => p.date);
}

export function lookbackRows(points: BalancePoint[]): LookbackRow[] {
  if (points.length < 2) return [];
  const latest = points[points.length - 1];
  const rows: LookbackRow[] = [];
  for (const { label, days } of LOOKBACKS) {
    const target = addDays(latest.date, -days);
    const anchor = anchorOnOrBefore(points, target);
    if (!anchor) continue;                                   // not enough history
    const oldestTolerated = addDays(target, -Math.ceil(days / 2));
    if (anchor.date < oldestTolerated) continue;             // staleness gate
    if (anchor.date === latest.date) continue;               // degenerate
    const delta = latest.balance - anchor.balance;
    rows.push({
      label, days,
      anchorDate: anchor.date,
      delta,
      pct: anchor.balance === 0 ? null : delta / Math.abs(anchor.balance),
    });
  }
  return rows;
}

/** "history since <date> · N observations" — the expanded panel's first line,
 *  which explains why longer lookbacks are absent while history accumulates. */
export function historySince(points: BalancePoint[]): { since: string; count: number } | null {
  if (points.length === 0) return null;
  return { since: points[0].date, count: points.length };
}

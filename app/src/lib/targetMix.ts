// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The family's target mix (Accounts → Investments): four buckets of the CLASSIFIED investable holdings - every position
// the Investments page lists; an account that reports a balance but no positions is not classified (its split is
// unknown, so no bucket counts it and the card names it apart); the house, cars, bank cash and debts are outside it -
// the target share saved for each, and the drift and rebalancing dollars between the two. Pure: the card only draws it.
import type { Holding } from "../data/types";
import { fmtUSD0 } from "./format";

export const MIX_KEYS = ["stocks", "bonds", "cash", "alts"] as const;
export type MixKey = (typeof MIX_KEYS)[number];
/** a whole mix: a percentage per bucket, the four totalling 100 */
export type TargetMix = Record<MixKey, number>;
/** the buckets a slider sets; bonds takes what they leave */
export const SET_LEGS = ["stocks", "cash", "alts"] as const satisfies readonly MixKey[];
export type SetLeg = (typeof SET_LEGS)[number];

export const MIX_LABELS: Record<MixKey, string> = { stocks: "Stocks", bonds: "Bonds", cash: "Cash", alts: "Alternatives" };
/** every bucket within this many points of its target reads "within" */
export const MIX_TOLERANCE_PTS = 5;
/** common guidance keeps speculative assets (the alternatives) under about this share; a mix above it is flagged */
export const ALTS_GUIDANCE_PCT = 10;
/** how far a saved mix's total may sit from 100 and still be one (float noise in a hand-edited doc, never a real gap) */
const SUM_SLACK = 0.01;

const BONDISH = /BND|VCIT|VGIT|LQD|SCHP|VTEB|BOND|TREASURY|CORPORATE/i;

const ALT_CLASSES: ReadonlySet<string> = new Set(["Crypto", "Precious Metals", "Private Stock", "Real Estate/Venture"]);

/** The bucket a position counts in. Approximate by design: a bond-like fund (by symbol or name) is bonds; crypto, precious
 *  metals, private stock and real estate / venture are alternatives; a holding whose class is Cash is cash; every other
 *  position is stocks. A balance-only account has no position and is no input here: it is not classified. */
export function mixBucket(h: Pick<Holding, "asset_class" | "symbol" | "name">): MixKey {
  if (ALT_CLASSES.has(h.asset_class)) return "alts";
  if (h.asset_class === "Cash") return "cash";
  if ((h.symbol && BONDISH.test(h.symbol)) || BONDISH.test(h.name)) return "bonds";
  return "stocks";
}

/** dollars per bucket and the classified total they make (the base every percentage here is of): the positions only */
export function bucketTotals(holdings: readonly Pick<Holding, "asset_class" | "symbol" | "name" | "value">[]): { by: TargetMix; total: number } {
  const by: TargetMix = { stocks: 0, bonds: 0, cash: 0, alts: 0 };
  for (const h of holdings) by[mixBucket(h)] += h.value;
  return { by, total: MIX_KEYS.reduce((s, k) => s + by[k], 0) };
}

const isPct = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 100;

/** A saved mix out of anything, or null: an object whose four buckets are each a number from 0 to 100 and together total
 *  100. A missing bucket, a negative, an over-100, a non-number or a total that is not 100 is null - never repaired into a
 *  mix nobody chose. Unknown keys never survive. */
export function parseTargetMix(raw: unknown): TargetMix | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  if (!MIX_KEYS.every((k) => isPct(r[k]))) return null;
  const mix = Object.fromEntries(MIX_KEYS.map((k) => [k, r[k] as number])) as TargetMix;
  return Math.abs(MIX_KEYS.reduce((s, k) => s + mix[k], 0) - 100) <= SUM_SLACK ? mix : null;
}

/** One slider moved: `leg` to `pct`, rounded to a whole percent and held between 0 and the room the other two set legs
 *  leave; the other set legs are rounded to whole percents too and bonds takes the rest, so the four are whole numbers
 *  that total exactly 100. A non-finite `pct` leaves the mix as it is. */
export function withLeg(mix: TargetMix, leg: SetLeg, pct: number): TargetMix {
  if (!Number.isFinite(pct)) return mix;
  const next = { ...mix };
  for (const k of SET_LEGS) next[k] = Math.min(100, Math.max(0, Math.round(next[k])));
  const others = SET_LEGS.filter((k) => k !== leg).reduce((s, k) => s + next[k], 0);
  // two other legs can already fill the room (a hand-saved 60 / 50 never comes from here, but a doc is a doc)
  next[leg] = Math.max(0, Math.min(Math.round(pct), 100 - others));
  const set = SET_LEGS.reduce((s, k) => s + next[k], 0);
  if (set > 100) {                        // only when the other two alone pass 100: shave them, the moved leg is 0
    let over = set - 100;
    for (const k of SET_LEGS) { const cut = Math.min(over, next[k]); next[k] -= cut; over -= cut; }
  }
  next.bonds = 100 - SET_LEGS.reduce((s, k) => s + next[k], 0);
  return next;
}

export function sameMix(a: TargetMix | null, b: TargetMix | null): boolean {
  if (a === null || b === null) return a === b;
  return MIX_KEYS.every((k) => Math.abs(a[k] - b[k]) < 1e-9);
}

export interface MixRow {
  key: MixKey;
  label: string;
  /** dollars held in the bucket */
  actual: number;
  /** share of the classified total (`mixRows`' `total`: the positions only, a balance-only account left out); null when
   *  that total is not above zero */
  actualPct: number | null;
  targetPct: number;
  /** actual − target, in points (positive = over); null with actualPct */
  driftPts: number | null;
  /** dollars to reach the target: positive = buy, negative = sell; null with actualPct */
  move: number | null;
}

/** one row per bucket, in MIX_KEYS order */
export function mixRows(by: TargetMix, total: number, target: TargetMix): MixRow[] {
  return MIX_KEYS.map((key) => {
    const has = total > 0;
    const actualPct = has ? by[key] * 100 / total : null;          // multiply first: 6500 of 10000 is exactly 65
    return {
      key, label: MIX_LABELS[key], actual: by[key], actualPct, targetPct: target[key],
      driftPts: actualPct === null ? null : actualPct - target[key],
      move: has ? target[key] * total / 100 - by[key] : null,
    };
  });
}

/** a drift's size as the table prints it (one decimal, as fmtPoints does): the number the tolerance and the summary
 *  read, so a row the table shows at "+5.0 pts" is never called off and the summary never names a different size */
export const shownPts = (v: number): number => Number(Math.abs(v).toFixed(1));
/** the bucket's printed drift is past MIX_TOLERANCE_PTS (a row with nothing to compare never is) */
export const isOff = (r: MixRow): boolean => r.driftPts !== null && shownPts(r.driftPts) > MIX_TOLERANCE_PTS;

/** The one-line verdict: every bucket within MIX_TOLERANCE_PTS of its target, or the bucket furthest off (the first on a
 *  tie), its drift as the table prints it and the dollars that would bring it back (through the masked formatter).
 *  `target` names what the rows are measured against. */
export function mixSummary(rows: readonly MixRow[], target = "your target"): string {
  if (rows.every((r) => r.driftPts === null)) return "Nothing invested to compare yet";
  const off = rows.filter(isOff);
  if (!off.length) return `Within ${MIX_TOLERANCE_PTS} points of ${target}`;
  const worst = off.reduce((a, b) => (shownPts(b.driftPts!) > shownPts(a.driftPts!) ? b : a));
  return `${worst.label} ${shownPts(worst.driftPts!).toFixed(1)} pts ${worst.driftPts! > 0 ? "over" : "under"}`
    + ` — about ${fmtUSD0(Math.abs(worst.move!))} to move`;
}

/** What the card says in place of a verdict, or of "save a mix to measure against it", when no classified position is
 *  there to measure (`classifiedTotal` not above zero): an account that reports a balance but no positions is not
 *  something to compare (`unclassified`: those accounts' names), so it says that and why; with nothing invested at all,
 *  that. null while some classified position is there. */
export function nothingToCompare(classifiedTotal: number, unclassified: readonly string[]): string | null {
  if (classifiedTotal > 0) return null;
  if (!unclassified.length) return "Nothing invested to compare yet.";
  const names = unclassified.length === 1 ? unclassified[0]
    : `${unclassified.slice(0, -1).join(", ")} and ${unclassified[unclassified.length - 1]}`;
  return `Nothing classified to compare yet. ${names} ${unclassified.length === 1 ? "reports" : "report"} a balance but no `
    + "positions.";
}

/** what to do with a bucket's dollars: "sell $X" over target, "buy $X" under it, "—" when it rounds to nothing */
export function moveWords(move: number | null): string {
  if (move === null || Math.abs(move) < 0.5) return "—";
  return `${move > 0 ? "buy" : "sell"} ${fmtUSD0(Math.abs(move))}`;
}

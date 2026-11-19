// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { HouseholdPerson } from "../data/types";
import type { TargetMix } from "./targetMix";

/** Fixed legs of the suggested mix (percent). Bonds is the remainder, so the four
 *  always sum to 100. */
const CASH = 5;
const ALTS = 5;
/** The fixed mix when no owner in the household config has a birth year. */
const FALLBACK: TargetMix = { stocks: 72, bonds: 18, cash: CASH, alts: ALTS };

export interface AllocationDefaults {
  targets: TargetMix;
  /** the age (at `year`) the stocks leg was computed from; null when no owner has a birth year */
  ownerAge: number | null;
}

/** The suggested target mix: the "110 − age" rule at the OLDEST owner's age (the conservative
 *  reading) for stocks, with cash and alternatives fixed and bonds taking the remainder.
 *  Stocks are clamped to the room left after cash+alternatives so no leg goes negative. Only owners
 *  (adults) with a birth year count; none → the fixed fallback and `ownerAge: null` (the caption then
 *  drops the age rather than invent one). */
export function defaultAllocationTargets(people: HouseholdPerson[], year: number): AllocationDefaults {
  const births = people.flatMap((p) => (p.role === "owner" && p.birth_year !== undefined ? [p.birth_year] : []));
  if (births.length === 0) return { targets: { ...FALLBACK }, ownerAge: null };
  const ownerAge = year - Math.min(...births);
  const stocks = Math.max(0, Math.min(110 - ownerAge, 100 - CASH - ALTS));
  return { targets: { stocks, bonds: 100 - stocks - CASH - ALTS, cash: CASH, alts: ALTS }, ownerAge };
}

/** How the suggestion was derived, in one sentence's words: the rule of thumb and the age it was read at, or the fixed
 *  starting point when no adult has a birth year. The target-mix card says it as the suggestion's basis. */
export function allocationCaption(ownerAge: number | null): string {
  return ownerAge === null
    ? `a fixed starting point, since no adult's birth year is in the household config: ${FALLBACK.stocks}% in stocks,`
      + ` ${CASH}% in cash, ${ALTS}% in alternatives and the rest in bonds`
    : `a common rule of thumb for age ${ownerAge} (the oldest adult's): 110 minus that age in stocks (at most`
      + ` ${100 - CASH - ALTS}%), ${CASH}% in cash, ${ALTS}% in alternatives and the rest in bonds`;
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useState } from "react";
import { useData } from "../data/DataContext";
import { isProjectionDefault, middleReturn } from "./projectionDefaults";

/** The yearly return a one-rate figure grows at: the middle of the household's return scenarios (middleReturn), so it is
 *  the household's own when `return_scenarios_pct` is stated and carries that key's "default" when it is not (`title`
 *  says so and where to state it). Planning's one-rate projections (the net-worth projection, the spending cut, the FIRE
 *  crossing) and Planning -> House's prepay-vs-invest card start their return control at it. `none`: no scenario is
 *  stated (an empty list, which an export older than the validator's check can still carry): the one-rate projections
 *  grow at none, and the retirement projection, which draws one line per scenario, has nothing to draw and says so. */
export function useGrowthSeed(): { pct: number; isDefault: boolean; title: string; none: boolean } {
  const { data, prefs } = useData();
  const scenarios = data.assumptions.projections.return_scenarios_pct;
  const isDefault = isProjectionDefault(data.assumptions.projections_stated, prefs.doc.projections, "return_scenarios_pct");
  return {
    pct: middleReturn(scenarios), isDefault, none: scenarios.length === 0,
    title: `The built-in default return scenarios, ${scenarios.map((r) => `${r}%`).join(" / ")}, not ones the household`
      + " stated; a one-rate projection grows at their middle. Data → Projection defaults says which defaults are stated"
      + " and where to state them.",
  };
}

/** The fields of a card's assumptions the person has set since the card opened - a slider moved or an amount typed,
 *  even the value it showed: a built-in starting value is tagged "default" until then, and a contribution or a cut is
 *  unset. `untouch` makes one unset again (its box emptied); `forget` is "reset". */
export function useTouched() {
  const [touched, setTouched] = useState<ReadonlySet<string>>(() => new Set());
  const touch = (k: string) => setTouched((t) => (t.has(k) ? t : new Set([...t, k])));
  const untouch = (k: string) => setTouched((t) => (t.has(k) ? new Set([...t].filter((x) => x !== k)) : t));
  const forget = () => setTouched(new Set());
  return { touched, touch, untouch, forget };
}

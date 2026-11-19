// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Where each projection default comes from, so a figure that rests on one the household never stated can say so. The
// exports carry every `projections` key, a key the configuration leaves out at the engine's default
// (`assumptions.projections_stated` lists the stated ones); the family may also save one on Data's Projection defaults
// card (prefs `projections`, merged over the exports). A key is the household's own when either states it, and a
// default otherwise. Pure: the cards read it.
import type { Assumptions } from "../data/types";

/** a projections key's value: stated in the configuration, saved in the app, or the engine's default */
export type ProjectionSource = "config" | "app" | "default";

export type ProjectionKey = keyof Assumptions["projections"];

/** what each projections key is, in words, for a tag's title and Data's card */
export const PROJECTION_LABELS: Readonly<Record<ProjectionKey, string>> = {
  return_scenarios_pct: "Return scenarios",
  inflation_pct: "Inflation",
  withdrawal_rate_pct: "Withdrawal rate",
  ltcg_pct: "LTCG tax",
  state_income_tax_pct: "State income tax",
};

/** Where `key`'s value comes from: the family's in-app default (`saved`, prefs `projections`) wins over the
 *  configuration, which wins over the engine's default. A saved value that is not a finite number is no value. */
export function projectionSource(stated: readonly string[], saved: Readonly<Record<string, number>>,
                                 key: ProjectionKey): ProjectionSource {
  if (Number.isFinite(saved[key])) return "app";
  return stated.includes(key) ? "config" : "default";
}

/** the key rests on the engine's default: neither the configuration nor the app states it */
export const isProjectionDefault = (stated: readonly string[], saved: Readonly<Record<string, number>>,
                                    key: ProjectionKey): boolean => projectionSource(stated, saved, key) === "default";

/** The yearly return a one-rate projection grows at (the net-worth projection, the spending cut, the FIRE crossing): the
 *  middle of the household's return scenarios, sorted (the lower middle of an even count). It is the household's own
 *  rate exactly when `return_scenarios_pct` is stated, so it carries that key's "default" when it is not. An empty list
 *  states no return: growth of none. */
export function middleReturn(scenarios: readonly number[]): number {
  if (!scenarios.length) return 0;
  const sorted = [...scenarios].sort((a, b) => a - b);
  return sorted[Math.floor((sorted.length - 1) / 2)];
}

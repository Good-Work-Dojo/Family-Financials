// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Test support: the route matrix's known gaps (matrix.test.tsx). Each row is one (demo profile, route, check) the
// dashboard does not pass yet, with one line saying why, for whoever fixes the page, and `expect`, a fragment of the
// check's own failure message today. The matrix runs a listed combination as a test that the check fails with a
// message containing that fragment: it FAILS the day the page passes (nothing is thrown) and the day the page fails
// another way (the fragment is missing), so a fixed or changed page forces its row out of this table or a rewrite of
// it. `full` has no row: a route that fails on the complete household is a defect, fixed with a unit test, never
// listed. matrix.test.tsx refuses a row naming no demo profile, route or check, or with no one-line reason and fragment.
// Nothing in the application imports this module.

export type MatrixCheck = "renders" | "values" | "private" | "console" | "cards";
export const MATRIX_CHECKS: readonly MatrixCheck[] = ["renders", "values", "private", "console", "cards"];

/** the demo generator's six households (tools/demo/profiles.py NAMES), in its order */
export const DEMO_PROFILES = ["full", "new", "tiny", "empty", "no-investments", "no-income"] as const;

export interface MatrixGap {
  profile: string; route: string; check: MatrixCheck; reason: string;
  /** a fragment the check's failure message contains today */
  expect: string;
}

/** every demo profile passes every check on every route: no known gap */
export const GAPS: MatrixGap[] = [];

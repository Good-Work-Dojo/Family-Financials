// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { storageKey } from "./demo";
import { localFlagStore } from "./localFlagStore";

/** The emergency-fund "essential" toggles (Planning → Projections): `{ "<Category> / <Sub>": bool }`,
 *  device-local by design (localStorage, never the family store). One store for every reader - the
 *  gauge that edits them, the Planning header's runway KPI and the forecast's safety floor - so the
 *  numbers on one page can never disagree about what counts as essential. */
export const EFUND_OVERRIDES_KEY = storageKey("efund-overrides");
export type EfundOverrides = Record<string, boolean>;

/** parse a stored document; anything unreadable (bad JSON, not an object, a non-boolean value) is
 *  ignored rather than crashing the page or flipping a row by accident. A toggle saved by the old model
 *  sits under a truncated key for a slash subcategory ("Utilities / Electric"); it is handed back untouched
 *  and `overrideFor` (lib/emergency.ts) resolves it for the row, so read a row's toggle through that, never
 *  `overrides[key]`. */
function parse(raw: string | null): EfundOverrides {
  if (!raw) return {};
  try {
    const v: unknown = JSON.parse(raw);
    if (!v || typeof v !== "object" || Array.isArray(v)) return {};
    return Object.fromEntries(Object.entries(v).filter(([, b]) => typeof b === "boolean")) as EfundOverrides;
  } catch {
    return {};
  }
}

const store = localFlagStore<EfundOverrides>(EFUND_OVERRIDES_KEY, parse,
  (next) => (Object.keys(next).length ? JSON.stringify(next) : null));

/** replace the toggles (null = reset to the defaults) and tell every reader on the page, and other tabs */
export function writeEfundOverrides(next: EfundOverrides | null): void {
  store.set(next ?? {});
}

/** the toggles in force on this device ({} when none, or when storage is unavailable), live: re-renders
 *  every reader when any of them writes */
export const useEfundOverrides = store.use;

/** test seam: forget an in-memory fallback left by a previous test */
export const _resetEfundOverridesMemory = store.resetMemory;

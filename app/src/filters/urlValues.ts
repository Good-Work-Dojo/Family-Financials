// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The value checks every URL-reading parser shares (filters.ts parseFilters, dateRange.ts parseDateRange / parseWin): what a date
// or a number in a query must look like, and the warn-once drop of one that does not. One module, so the Spending filters and the
// page-scoped ranges (Cash Flow, Recurring) cannot disagree about what a good `from` is.

/** Bad values already warned about. Every URL write re-parses the query, so a hand-edited value that stays in the URL
 *  while the user edits filters would otherwise warn on each keystroke (the `?sort=` pattern, filters/useSort.ts). */
const warned = new Set<string>();
/** Test-only: forget the values already warned about, so each `it` can assert its own warnings. */
export function __resetFilterWarnings(): void {
  warned.clear();
}

/** Drop a URL value the app cannot use: ONE console.warn per distinct `key=value`, then undefined (the key reads as absent). */
export function ignoreParam(key: string, raw: string, wanted: string): undefined {
  const id = `${key}=${raw}`;
  if (!warned.has(id)) {
    warned.add(id);
    console.warn(`ignoring ?${key}=${raw}: not ${wanted}`);
  }
  return undefined;
}

/** A real calendar day written `YYYY-MM-DD` - the only shape a date input, a preset or a deep link writes. `2026-02-31` is
 *  not one (it would parse as March 3rd elsewhere), and neither is `garbage`, which compares as a string and matched nothing. */
export function isISODate(v: string): boolean {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
  if (!m) return false;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return d.getUTCFullYear() === Number(m[1]) && d.getUTCMonth() === Number(m[2]) - 1 && d.getUTCDate() === Number(m[3]);
}

/** `?<key>=` as a date: absent and empty read as absent; a value that is not an ISO day is dropped with the one warning. */
export function dateParam(sp: URLSearchParams, key: string): string | undefined {
  const raw = sp.get(key);
  if (raw === null || raw === "") return undefined;
  return isISODate(raw) ? raw : ignoreParam(key, raw, "a YYYY-MM-DD date");
}

/** `?<key>=` as a finite number; `abc`, `NaN` and `Infinity` are dropped (a NaN bound counted as an active filter and matched everything) */
export function amountParam(sp: URLSearchParams, key: string): number | undefined {
  const raw = sp.get(key);
  if (!raw) return undefined;
  const n = Number(raw);
  return Number.isFinite(n) ? n : ignoreParam(key, raw, "a number");
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The dashboard's one clock. Application code reads the wall clock here and nowhere else (lib/categories.test.ts scans
// for any other read), for two reasons. There is one place to freeze time: each read is a fresh `new Date()`, so the
// tests' frozen `Date` and the screenshot harness's pinned page clock reach every page with nothing to inject. And
// there is one definition of the household's day: "today" is the date in the household's own zone (household.yaml
// `timezone`, exported as `assumptions.household.timezone`), not the browser's, so a household opened from a browser in
// another zone sees the same bills due, the same schedule statuses and the same month as at home.

/** One day formatter per zone, made on first use; null for a zone this browser's `Intl` refuses. */
const dayFormats = new Map<string, Intl.DateTimeFormat | null>();

function dayFormat(zone: string): Intl.DateTimeFormat | null {
  if (!dayFormats.has(zone)) {
    let format: Intl.DateTimeFormat | null;
    try {
      format = new Intl.DateTimeFormat("en-US", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" });
    } catch {
      format = null;   // a browser's zone table can be older than the household's config: fall back, never throw
    }
    dayFormats.set(zone, format);
  }
  return dayFormats.get(zone) ?? null;
}

const pad = (n: number, width = 2) => String(n).padStart(width, "0");

/** The ISO day (`YYYY-MM-DD`) of `at` in `zone`, or in the browser's own zone when `zone` is null or refused. Pure: the
 *  instant is the caller's (a printed date's year is judged by it, lib/format.ts). */
export function dayOf(at: Date, zone: string | null): string {
  const format = zone === null ? null : dayFormat(zone);
  if (format === null) return `${pad(at.getFullYear(), 4)}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`;
  const parts = format.formatToParts(at);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === type)?.value ?? "";
  return `${part("year").padStart(4, "0")}-${part("month")}-${part("day")}`;
}

/** the instant the demo build's clock stands at (`clock.freeze`); null: the wall clock */
let frozenAt: number | null = null;

const now = (): Date => (frozenAt === null ? new Date() : new Date(frozenAt));

export const clock = {
  /** The current instant. */
  now,
  /** From here every read is `at`: the demo build stands its clock at the exports' generation time
   *  (`manifest.generated_at`, data/DataContext.tsx), so an invented household built once never reads overdue or
   *  stale. An instant that does not parse leaves the clock as it is; `null` gives the wall clock back. */
  freeze: (at: string | null): void => {
    if (at === null) { frozenAt = null; return; }
    const ms = Date.parse(at);
    if (!Number.isNaN(ms)) frozenAt = ms;
  },
  /** The current instant as a UTC ISO stamp (`…Z`): the `updated_at` / `answered_at` the family store's documents
   *  carry. The store refuses a stamp more than a day ahead of its own clock, so this is the real instant, never the
   *  household's wall clock. */
  nowISO: (): string => now().toISOString(),
  /** The household's day: the ISO day (`YYYY-MM-DD`) of the current instant in `zone`
   *  (`assumptions.household.timezone`); with `null`, or with a zone this browser's `Intl` refuses, the browser's own
   *  day. Never throws. */
  today: (zone: string | null): string => dayOf(now(), zone),
};

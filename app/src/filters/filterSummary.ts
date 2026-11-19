// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { SOURCE_GROUPS } from "../lib/categorySource";
import { fmtDate } from "../lib/format";
import { matchPreset, PRESET_LABELS, SPENDING_PRESETS } from "./dateRange";
import { countActive, type Filters } from "./filters";

/** The date window in a few words: a preset's own chip label ("6M", "All"), else the dates ("Jan 3, 2026 – Mar 9, 2026").
 *  No window at all is All, with or without a data date (`maxDate` null: no transaction yet, when no preset resolves). */
export function windowLabel(from: string | undefined, to: string | undefined, maxDate: string | null): string {
  if (!from && !to) return PRESET_LABELS.all;
  const p = matchPreset({ from, to }, maxDate, SPENDING_PRESETS);
  if (p) return PRESET_LABELS[p];
  if (from && to) return `${fmtDate(from)} – ${fmtDate(to)}`;
  return from ? `since ${fmtDate(from)}` : `through ${fmtDate(to!)}`;
}

/** The filters other than the date window that are narrowing the rows: every active facet, the search, the review box, the
 *  amount range, the direction - and the internal-transfers box, which is not a facet but does change which rows show. */
export function otherFilterCount(f: Filters): number {
  return countActive(f) - (f.from || f.to ? 1 : 0) + (f.includeInternal ? 1 : 0);
}

const LISTS = ["categories", "vendors", "owners", "accounts", "tags", "types", "sources"] as const;

/** a sources facet group by its label ("AI", "Your rules") */
const SOURCE_LABEL: ReadonlyMap<string, string> = new Map(SOURCE_GROUPS.map((g) => [g.id, g.label]));

/** What the ONE other filter that is on is, in a word or two: a facet by its value ("Dining", "Dining +1" for two; a source
 *  group by its label, "Filed by: AI"), the search by its text, the rest by name. Amounts are never printed (privacy mode
 *  masks money; "amount range" names the filter, not the figure). */
function onlyFilter(f: Filters, accountNames: ReadonlyMap<string, string>): string {
  for (const k of LISTS) {
    const v = f[k];
    if (v.length) {
      const first = k === "accounts" ? accountNames.get(v[0]) ?? v[0]
        : k === "sources" ? `Filed by: ${SOURCE_LABEL.get(v[0]) ?? v[0]}` : v[0];
      return v.length > 1 ? `${first} +${v.length - 1}` : first;
    }
  }
  if (f.q) return `“${f.q.length > 24 ? `${f.q.slice(0, 23)}…` : f.q}”`;
  if (f.amountMin !== undefined || f.amountMax !== undefined) return "amount range";
  if (f.direction !== "all") return f.direction === "expense" ? "money out" : "money in";
  if (f.needsReviewOnly) return "needs review";
  return "with transfers";
}

/** The one line a phone shows in place of the whole filter bar ("6M · Dining", "6M · 2 filters"): the window, then the other
 *  filters - named when there is exactly one, counted when there are more. Every control is behind it, so it has to say that
 *  something is narrowing the list. */
export function filterSummary(f: Filters, maxDate: string | null, accountNames: ReadonlyMap<string, string> = new Map()): string {
  const n = otherFilterCount(f);
  const what = n === 0 ? "no filters" : n === 1 ? onlyFilter(f, accountNames) : `${n} filters`;
  return `${windowLabel(f.from, f.to, maxDate)} · ${what}`;
}

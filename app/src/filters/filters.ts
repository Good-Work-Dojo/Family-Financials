// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { Transaction, TxType } from "../data/types";
import { isSourceGroup, sourceGroup, type SourceGroupId } from "../lib/categorySource";
import { amountParam, dateParam } from "./urlValues";

export { __resetFilterWarnings, isISODate } from "./urlValues";

export interface Filters {
  from?: string;                // ISO date, inclusive
  to?: string;                  // ISO date, inclusive
  accounts: string[];
  owners: string[];
  categories: string[];
  vendors: string[];
  types: TxType[];
  tags: string[];
  /** where a row's category came from, by the facet's groups (lib/categorySource.ts SOURCE_GROUPS) */
  sources: SourceGroupId[];
  amountMin?: number;           // applied to |amount|
  amountMax?: number;
  direction: "all" | "expense" | "income";
  q?: string;
  needsReviewOnly: boolean;
  includeInternal: boolean;     // default false: transfers/cc-payments hidden
}

export const EMPTY_FILTERS: Filters = {
  accounts: [], owners: [], categories: [], vendors: [], types: [], tags: [], sources: [],
  direction: "all", needsReviewOnly: false, includeInternal: false,
};

const LIST_KEYS = ["accounts", "owners", "categories", "vendors", "types", "tags", "sources"] as const;

/** every URL search key parseFilters/serializeFilters read or write (filters.test.ts pins the list
 *  against serializeFilters). A tab that shares the FilterBar carries exactly these and nothing
 *  else, so a page-scoped key (Recurring's win, a snapshot rewind's snap, ...) never rides along. */
export const FILTER_PARAM_KEYS: readonly string[] = [
  ...LIST_KEYS, "from", "to", "min", "max", "dir", "q", "review", "internal",
];

/** `?win=<preset>`: a rolling date window - NOT a filter (it is not in FILTER_PARAM_KEYS: the tab links never carry it and
 *  writeFilters leaves it alone). Only a SAVED view or report stores it, in place of the dates a preset happened to be on the day
 *  it was saved; opening one resolves it to a real `from` (filters/useSpendingDefaults.ts) and removes it from the URL. */
export const WINDOW_PARAM = "win";
/** every key a saved view's / report's stored query may hold: the filter keys and the rolling window */
export const SAVED_QUERY_KEYS: readonly string[] = [...FILTER_PARAM_KEYS, WINDOW_PARAM];

/** Write `f` into a COPY of `prev`: exactly the filter keys are replaced, every other param
 *  (a page's `sort`, `snap`, `end`, `pl`, `win`, the report keys) survives - the pattern
 *  filters/dateRange.ts writeDateRange/writeWin already use. Both useFilters writers go through it. */
export function writeFilters(prev: URLSearchParams, f: Filters): URLSearchParams {
  const next = new URLSearchParams(prev);
  for (const k of FILTER_PARAM_KEYS) next.delete(k);
  for (const [k, v] of serializeFilters(f)) next.set(k, v);
  return next;
}

/** URL search params <-> Filters. Defaults are omitted from the URL. A date that is not `YYYY-MM-DD` and an amount that is
 *  not a finite number are dropped with ONE console warning per distinct value, like `?sort=`: a hand-edited or truncated
 *  link opens the register as if the key were absent, never empty or NaN-filtered with nothing said. */
export function parseFilters(sp: URLSearchParams): Filters {
  const f: Filters = { ...EMPTY_FILTERS, accounts: [], owners: [], categories: [],
    vendors: [], types: [], tags: [], sources: [] };
  for (const k of LIST_KEYS) {
    const v = sp.get(k);
    if (v) (f[k] as string[]) = v.split("|").filter(Boolean);
  }
  // a source group is one of the facet's own ids: anything else in a hand-edited link filters nothing
  f.sources = [...new Set(f.sources.filter(isSourceGroup))];
  f.from = dateParam(sp, "from");
  f.to = dateParam(sp, "to");
  const min = amountParam(sp, "min"), max = amountParam(sp, "max");
  if (min !== undefined) f.amountMin = min;
  if (max !== undefined) f.amountMax = max;
  const dir = sp.get("dir");
  if (dir === "expense" || dir === "income") f.direction = dir;
  f.q = sp.get("q") ?? undefined;
  f.needsReviewOnly = sp.get("review") === "1";
  f.includeInternal = sp.get("internal") === "1";
  return f;
}

export function serializeFilters(f: Filters): URLSearchParams {
  const sp = new URLSearchParams();
  for (const k of LIST_KEYS) if (f[k].length) sp.set(k, f[k].join("|"));
  if (f.from) sp.set("from", f.from);
  if (f.to) sp.set("to", f.to);
  if (f.amountMin !== undefined) sp.set("min", String(f.amountMin));
  if (f.amountMax !== undefined) sp.set("max", String(f.amountMax));
  if (f.direction !== "all") sp.set("dir", f.direction);
  if (f.q) sp.set("q", f.q);
  if (f.needsReviewOnly) sp.set("review", "1");
  if (f.includeInternal) sp.set("internal", "1");
  return sp;
}

export function countActive(f: Filters): number {
  let n = 0;
  for (const k of LIST_KEYS) n += f[k].length ? 1 : 0;
  if (f.from || f.to) n++;
  if (f.amountMin !== undefined || f.amountMax !== undefined) n++;
  if (f.direction !== "all") n++;
  if (f.q) n++;
  if (f.needsReviewOnly) n++;
  return n;
}

/**
 * The single predicate powering every combinable filter:
 * facets AND together; values within a facet OR together.
 */
export function applyFilters(txns: readonly Transaction[], f: Filters): Transaction[] {
  const q = f.q?.toLowerCase();
  return txns.filter((t) => {
    if (!f.includeInternal && (t.type === "transfer" || t.type === "cc-payment")) return false;
    if (f.from && t.date < f.from) return false;
    if (f.to && t.date > f.to) return false;
    if (f.accounts.length && !f.accounts.includes(t.account)) return false;
    if (f.owners.length && !f.owners.includes(t.owner)) return false;
    if (f.categories.length && !f.categories.includes(t.category ?? "")) return false;
    if (f.vendors.length && !f.vendors.includes(t.vendor ?? "")) return false;
    if (f.types.length && !f.types.includes(t.type)) return false;
    if (f.tags.length && !f.tags.some((tag) => t.tags.includes(tag))) return false;
    if (f.sources.length && !f.sources.includes(sourceGroup(t.category_source))) return false;
    const a = Math.abs(t.amount);
    if (f.amountMin !== undefined && a < f.amountMin) return false;
    if (f.amountMax !== undefined && a > f.amountMax) return false;
    if (f.direction === "expense" && t.amount >= 0) return false;
    if (f.direction === "income" && t.amount <= 0) return false;
    if (f.needsReviewOnly && !t.needs_review) return false;
    if (q && !(t.vendor ?? "").toLowerCase().includes(q)
      && !t.description.toLowerCase().includes(q)) return false;
    return true;
  });
}

export type SpendingTarget = "explore" | "transactions";

/** The router path of a Spending tab with the given filter patch (what `navigate()` takes).
 *  `target` picks the tab: the default lands on the table ("transactions") - every "view
 *  transactions / see charges" link wants rows, not charts. "explore" (the charts) has no caller
 *  today: Explore's own chart clicks filter in place with `update()`, and its calendar-day click and
 *  "view these N transactions" link go to the table. */
export function spendingPath(patch: Partial<Filters>, target: SpendingTarget = "transactions"): string {
  const sp = serializeFilters({ ...EMPTY_FILTERS, ...patch });
  return `${target === "transactions" ? "/spending/transactions" : "/spending"}?${sp.toString()}`;
}

/** The same as an `href`: a #/spending... URL for every deep link. */
export function spendingLink(patch: Partial<Filters>, target: SpendingTarget = "transactions"): string {
  return "#" + spendingPath(patch, target);
}

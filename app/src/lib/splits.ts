// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { Transaction } from "../data/types";
import { NO_CATEGORY } from "./categories";

/** The two views of the export. `transactions` is the CATEGORY view — every row that carries
 *  spend: split children and ordinary rows, never a parent — and what every lib/* sum, the Review
 *  inbox and every category judgement read. `purchases` is the VENDOR view — one row per purchase:
 *  split parents (full amount, display-only category) and ordinary rows, never a child — for listing
 *  and counting purchases. `splitParents` indexes the parents by id for the register. Export order
 *  is kept in both. A consumer that sums money or judges a category reads `transactions`; one that
 *  lists or counts purchases reads `purchases` or counts with purchaseKey / purchaseCount. */
export interface SplitViews {
  transactions: Transaction[];
  purchases: Transaction[];
  splitParents: Map<string, Transaction>;
}

export function partitionSplits(rows: readonly Transaction[]): SplitViews {
  const splitParents = new Map<string, Transaction>();
  for (const t of rows) if (t.is_parent) splitParents.set(t.id, t);
  if (splitParents.size === 0) {
    // the common case (no split rule, or an export from before them): both views ARE the export
    const all = rows as Transaction[];
    return { transactions: all, purchases: all, splitParents };
  }
  return {
    transactions: rows.filter((t) => !t.is_parent),
    purchases: rows.filter((t) => t.parent_id === null),
    splitParents,
  };
}

/** the purchase a row belongs to: itself, or its parent when it is a part of a split */
export const purchaseKey = (t: Transaction): string => t.parent_id ?? t.id;

/** how many purchases a set of category-view rows touches: a split counts once however many of its
 *  parts are present (the FilterBar's "N of M", a report's `count`) */
export function purchaseCount(rows: readonly Transaction[]): number {
  let n = 0;
  const seen = new Set<string>();
  for (const t of rows) {
    if (t.parent_id === null) { n++; continue; }
    if (!seen.has(t.parent_id)) { seen.add(t.parent_id); n++; }
  }
  return n;
}

/** the parts of each split purchase by parent id, in row order (the register asks "are ALL of a
 *  parent's parts here?") */
export function childrenByParent(transactions: readonly Transaction[]): Map<string, Transaction[]> {
  const out = new Map<string, Transaction[]>();
  for (const t of transactions) {
    if (t.parent_id === null) continue;
    let arr = out.get(t.parent_id);
    if (!arr) out.set(t.parent_id, (arr = []));
    arr.push(t);
  }
  return out;
}

/** The vendor view after the answers overlay: an ordinary row is the SAME object in both views, so a
 *  row the overlay repainted (pendingIds) is swapped in here too; a parent is never overlaid (it is
 *  display-only). The same array instance when nothing is pending, or when only parts are. */
export function overlayPurchases(purchases: Transaction[], overlaid: readonly Transaction[],
                                 pendingIds: ReadonlySet<string>): Transaction[] {
  if (pendingIds.size === 0) return purchases;
  const by = new Map<string, Transaction>();
  for (const t of overlaid) if (pendingIds.has(t.id)) by.set(t.id, t);
  let changed = false;
  const out = purchases.map((p) => {
    const o = by.get(p.id);
    if (o === undefined || o === p) return p;
    changed = true;
    return o;
  });
  return changed ? out : purchases;
}

/** The register's rows from the FILTERED category-view rows: a parent whose EVERY part passed the
 *  filter stands in for them (one row, the full amount, expandable to its parts); a parent with only
 *  some parts present shows those parts themselves ("split of <vendor>"); ordinary rows pass through.
 *  Input order is kept, a parent taking its first part's place. The same array when no row is a part;
 *  a part whose parent is unknown (never in this export) shows as itself. */
export function regroupSplits(rows: readonly Transaction[], children: ReadonlyMap<string, readonly Transaction[]>,
                              parents: ReadonlyMap<string, Transaction>): Transaction[] {
  if (!rows.some((t) => t.parent_id !== null)) return rows as Transaction[];
  const present = new Map<string, number>();
  for (const t of rows) if (t.parent_id !== null) present.set(t.parent_id, (present.get(t.parent_id) ?? 0) + 1);
  const whole = new Set<string>();
  for (const [pid, n] of present) {
    const all = children.get(pid)?.length ?? 0;
    if (parents.has(pid) && all > 0 && n === all) whole.add(pid);
  }
  const placed = new Set<string>();
  const out: Transaction[] = [];
  for (const t of rows) {
    if (t.parent_id === null || !whole.has(t.parent_id)) { out.push(t); continue; }
    if (!placed.has(t.parent_id)) { placed.add(t.parent_id); out.push(parents.get(t.parent_id)!); }
  }
  return out;
}

/** One leg of a split rule as the data shows it: a category (and subcategory) and its share of the split purchases' dollars. */
export interface SplitLeg { category: string; subcategory: string | null; share: number }

/** A vendor rule that is splitting purchases right now, read off the split structures themselves (no export key of
 *  its own): the vendor, how many purchases it split, and the legs' shares. */
export interface SplitRuleSummary { vendor: string; purchases: number; legs: SplitLeg[] }

/** The split rules in force, summarised from the parents and their parts (`AppData.splitParents`,
 *  `Indexes.childrenByParent`): per vendor, the purchases split and each leg's share of the parts' dollars (by
 *  absolute amount, so a refund wrapper counts like a purchase). Shares are COMPUTED FROM THE PARTS - after the answers
 *  overlay has repainted any of them - never restated from the rule file. Rules by purchases split, most first. */
export function splitRules(
  splitParents: ReadonlyMap<string, Transaction>, children: ReadonlyMap<string, readonly Transaction[]>,
): SplitRuleSummary[] {
  const by = new Map<string, { purchases: number; legs: Map<string, SplitLeg & { dollars: number }> }>();
  for (const [id, parent] of splitParents) {
    const parts = children.get(id);
    if (!parts?.length) continue;
    const vendor = parent.vendor ?? "—";
    let rule = by.get(vendor);
    if (!rule) by.set(vendor, (rule = { purchases: 0, legs: new Map() }));
    rule.purchases++;
    for (const t of parts) {
      const category = t.category ?? NO_CATEGORY;
      const key = `${category}\u0000${t.subcategory ?? ""}`;
      let leg = rule.legs.get(key);
      if (!leg) rule.legs.set(key, (leg = { category, subcategory: t.subcategory, share: 0, dollars: 0 }));
      leg.dollars += Math.abs(t.amount);
    }
  }
  return [...by.entries()].map(([vendor, { purchases, legs }]) => {
    const all = [...legs.values()].reduce((s, l) => s + l.dollars, 0);
    return {
      vendor, purchases,
      legs: [...legs.values()]
        .map(({ category, subcategory, dollars }) => ({ category, subcategory, share: all > 0 ? dollars / all : 0 }))
        .sort((a, b) => b.share - a.share || a.category.localeCompare(b.category)),
    };
  }).sort((a, b) => b.purchases - a.purchases || a.vendor.localeCompare(b.vendor));
}

const pct = (share: number) => `${Math.round(share * 100)}%`;
const andList = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

/** The footnote a page prints when a category it SHOWS contains split parts - the register explains a split on its
 *  own row, but the budget grid, the P&L and Explore moved with it and said nothing. One sentence per rule, naming only
 *  the legs in `shown` ("Water includes 20% of each bill"; "Electric and Water include 80% and 20% of
 *  each bill"); null when no shown category holds a leg of any rule. No amounts: shares only. */
export function splitNote(rules: readonly SplitRuleSummary[], shown: Iterable<string>): string | null {
  const have = new Set(shown);
  const sentences: string[] = [];
  for (const rule of rules) {
    const byCategory = new Map<string, number>();
    for (const leg of rule.legs) if (have.has(leg.category)) byCategory.set(leg.category, (byCategory.get(leg.category) ?? 0) + leg.share);
    if (!byCategory.size) continue;
    const legs = [...byCategory.entries()];
    sentences.push(`${andList(legs.map(([c]) => c))} ${legs.length === 1 ? "includes" : "include"} ${
      andList(legs.map(([, share]) => pct(share)))} of each ${rule.vendor} order`);
  }
  return sentences.length ? sentences.join("; ") : null;
}

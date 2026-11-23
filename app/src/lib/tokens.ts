// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Pinned palettes: every chart in every section uses the same color per
// owner / asset class, so views agree with each other.
import type { AssetClasses } from "../data/types";

/** Owner colours by position in the export's owner list (`assumptions.owners`: the adults, the joint label, the
 *  children), so the household is data and each owner keeps one colour everywhere. Eight entries: a household of up
 *  to eight owners never repeats a colour. */
export const OWNER_PALETTE: readonly string[] = [
  "#3b82f6", "#8b5cf6", "#14b8a6", "#f59e0b", "#ec4899",
  "#f97316", "#06b6d4", "#84cc16",
];

/** The neutral grey of a series no palette knows: an owner the owner list lacks, or an unknown asset class. */
export const UNKNOWN_COLOR = "#9ca3af";

/** `name`'s colour: the palette entry at its index in `owners` (cycling past the palette's end); a name `owners` does
 *  not list gets the neutral grey. Pure, so the same list gives the same colours whatever the call order. */
export function ownerColor(name: string, owners: readonly string[]): string {
  const i = owners.indexOf(name);
  return i < 0 ? UNKNOWN_COLOR : OWNER_PALETTE[i % OWNER_PALETTE.length];
}

/** Income-stream colours by position: the income chart orders its streams (the income subcategories in the data) by
 *  their total, largest first, and gives the n-th stream the n-th entry. Ten distinct entries. */
export const STREAM_PALETTE: readonly string[] = [
  "#3b82f6", "#8b5cf6", "#14b8a6", "#f59e0b", "#ec4899",
  "#84cc16", "#06b6d4", "#f97316", "#a855f7", "#eab308",
];

/** the colour of the stream at `index` in the chart's order, cycling past the palette's end */
export function streamColor(index: number): string {
  return STREAM_PALETTE[index % STREAM_PALETTE.length];
}

export const CLASS_COLORS: Record<string, string> = {
  Cash: "#10b981",
  "Stocks/ETF": "#3b82f6",
  Retirement: "#6366f1",
  Crypto: "#f59e0b",
  "Precious Metals": "#eab308",
  "Real Estate/Venture": "#b45309",
  House: "#64748b",
  Car: "#94a3b8",
  "529": "#ec4899",
  "Private Stock": "#8b5cf6",
  Mortgage: "#ef4444",
  Margin: "#f87171",
  "Other Debt": "#fb923c",
};

/** asset classes plotted as negative (debt) bands */
export const DEBT_CLASSES = new Set(["Mortgage", "Margin", "Other Debt"]);

/** The asset classes as the dashboard orders them (CLASS_COLORS as written, then DEBT_CLASSES): the order the
 *  manual-assets editor lists a household's classes in. Written out, since Object.keys would put "529" first. The export
 *  lists the same classes (tests/test_exports.py) and so does CLASS_COLORS (tokens.test.ts). */
export const SHIPPED_ASSET_CLASSES: AssetClasses = {
  assets: ["Cash", "Stocks/ETF", "Retirement", "Crypto", "Precious Metals", "Real Estate/Venture", "House", "Car", "529",
    "Private Stock"],
  liabilities: [...DEBT_CLASSES],
};

/** the asset class a home is stated under (accounts.yaml manual_entries `asset_class: House`): its rows are the home's
 *  value (lib/lifeSummary homeValue) */
export const HOUSE_CLASS = "House";

/** the asset class of a retirement account (accounts.yaml `asset_class: Retirement`, or the default of its kind): the
 *  accounts Planning's retirement projection can map to a person */
export const RETIREMENT_CLASS = "Retirement";

/** the asset class of a college savings account (accounts.yaml `asset_class: 529`): illiquid like Retirement, and a
 *  draw from it is penalised like one */
export const COLLEGE_CLASS = "529";

export const CATEGORY_PALETTE = [
  "#3b82f6", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#14b8a6", "#f97316",
  "#06b6d4", "#ec4899", "#84cc16", "#6366f1", "#eab308", "#a855f7", "#22c55e",
  "#fb7185", "#94a3b8", "#0ea5e9", "#d946ef", "#facc15", "#34d399",
];

/** An account's colour by its position in the household's own account list (accounts.json `accounts`, in the order
 *  accounts.yaml lists them), cycling past the palette's end; an id the list lacks gets the neutral grey. Pure, so one
 *  account has one colour on a page whichever chart asks first, and keeps it from build to build. */
export function accountColor(id: string, accountIds: readonly string[]): string {
  const i = accountIds.indexOf(id);
  return i < 0 ? UNKNOWN_COLOR : CATEGORY_PALETTE[i % CATEGORY_PALETTE.length];
}

/** a name's colour in the order names are first asked for, for the session: two charts that ask in a different order
 *  can colour one name two ways (an account's colour is `accountColor`'s) */
const assigned = new Map<string, string>();
export function categoryColor(name: string): string {
  let c = assigned.get(name);
  if (!c) {
    c = CATEGORY_PALETTE[assigned.size % CATEGORY_PALETTE.length];
    assigned.set(name, c);
  }
  return c;
}

/** a neutral mark that reads on a card in both themes (slate-500: about 3.9:1 on the dark card, 4.6:1 on the light one;
 *  lib/contrast.test.ts): bars that are context, not the chart's subject (the down-payment sweep's cash to close) */
export const NEUTRAL_MARK = "#64748b";

export const POSITIVE = "#22c55e";
export const NEGATIVE = "#ef4444";
export const WARNING = "#f59e0b";

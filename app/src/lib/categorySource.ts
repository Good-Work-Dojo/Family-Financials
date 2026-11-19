// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { CategorySource } from "../data/types";

export interface ChipSpec { label: string; title: string; cls: string }

/** Provenance worth badging: `learned` - the family should see which rows the ETL categorized
 *  by replaying their own confirmed answers -, `split` - a part of a purchase a vendor rule
 *  divided - and `ai` - a row the AI step's stored answer filed, which the family should always be able
 *  to tell from one its own rules filed. Every other source renders nothing - a chip per source would be
 *  noise, and `answer` rows already get the pending dot. Each chip has a hue of its own. */
export function sourceChip(source: CategorySource): ChipSpec | null {
  if (source === "learned") {
    return { label: "learned", title: "learned from your answers", cls: "bg-purple-950 text-purple-300" };
  }
  if (source === "split") {
    return { label: "split", title: "split by a vendor rule", cls: "bg-sky-950 text-sky-300" };
  }
  if (source === "ai") {
    return { label: "AI", title: "Filed by AI", cls: "bg-pink-950 text-pink-300" };
  }
  return null;
}

/** The chip's ink class on its own (`text-sky-300`), for the same provenance said in WORDS (the register's
 *  expanded row): taken from the chip's `cls`, so the words and the chip share a hue - and the light
 *  theme's remap of it (index.css `.light`) - by construction. */
export const chipInk = (chip: ChipSpec): string => chip.cls.split(/\s+/).find((c) => c.startsWith("text-")) ?? "";

/** The Transactions filters' "Filed by" facet: where a row's category came from, in the family's words. Each group
 *  gathers the category sources that mean the same thing to a person, so one word is never two choices: the
 *  household's own rules (vendor and keyword rules, the parts of a purchase a vendor rule splits, the transfers its
 *  transfer rules pair and the categories a row's type gives), rules learned from its answers, its answers, its
 *  overrides, the bank's own label (a file format's or the feed's category included), the AI step, and the rows
 *  nothing placed yet. The ids are what the URL carries (`?sources=`). */
export const SOURCE_GROUPS = [
  { id: "rules", label: "Your rules", sources: ["vendor_rule", "keyword", "split", "internal", "type_default"] },
  { id: "learned", label: "Learned from your answers", sources: ["learned"] },
  { id: "answers", label: "Your answers", sources: ["answer"] },
  { id: "overrides", label: "Overrides", sources: ["override"] },
  { id: "bank", label: "The bank's label", sources: ["bank_label", "parser"] },
  { id: "ai", label: "AI", sources: ["ai"] },
  { id: "unsorted", label: "Not yet sorted", sources: ["fallback"] },
] as const satisfies readonly { id: string; label: string; sources: readonly CategorySource[] }[];

export type SourceGroupId = (typeof SOURCE_GROUPS)[number]["id"];

const GROUP_OF: ReadonlyMap<CategorySource, SourceGroupId> = new Map(
  SOURCE_GROUPS.flatMap((g) => g.sources.map((s) => [s as CategorySource, g.id] as const)));

/** the facet group a row's category source falls in */
export const sourceGroup = (source: CategorySource): SourceGroupId => GROUP_OF.get(source) ?? "unsorted";

/** is `id` one of the facet's groups (a URL value is checked before it filters anything) */
export const isSourceGroup = (id: string): id is SourceGroupId => SOURCE_GROUPS.some((g) => g.id === id);

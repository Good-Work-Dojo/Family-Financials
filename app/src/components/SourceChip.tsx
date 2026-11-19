// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { CategorySource } from "../data/types";
import { sourceChip } from "../lib/categorySource";
import { Badge } from "./ui";

/** `focusable={false}` inside a row button / clickable row (see Badge) */
export default function SourceChip({ source, focusable = true }: { source: CategorySource; focusable?: boolean }) {
  const chip = sourceChip(source);
  if (!chip) return null;
  return <Badge cls={chip.cls} title={chip.title} focusable={focusable}>{chip.label}</Badge>;
}

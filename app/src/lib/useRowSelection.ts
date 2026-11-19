// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useCallback, useMemo, useState } from "react";

/** the most rows one bulk action may answer: each is one one-off answer in a store that keeps MAX_ANSWERS
 *  of them (lib/answers.ts capAnswers) — a 500-row sweep would evict every older one-off answer,
 *  including the ones the learned rules replay. Kept below MAX_ANSWERS, so the answers a save creates (stamped
 *  now) are never the oldest and never dropped by it. A row that already has an answer merges into it instead, and a
 *  tags-only merge into an answer that names a category keeps that answer's old stamp (mergeAnswer), so the same save
 *  can still drop it, new tags and all. */
export const SELECTION_CAP = 100;

export interface RowSelection {
  selected: ReadonlySet<string>;
  toggle(id: string): void;
  selectMany(ids: string[]): void;
  clear(): void;
  /** drop ids that are no longer on screen (a filter change) */
  prune(live: ReadonlySet<string>): void;
  atCap: boolean;
}

export function useRowSelection(): RowSelection {
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  // a selected row always toggles off; a new one is refused at the cap
  const toggle = useCallback((id: string) => setSelected((cur) => {
    const next = new Set(cur);
    if (!next.delete(id)) { if (next.size >= SELECTION_CAP) return cur; next.add(id); }
    return next;
  }), []);
  const selectMany = useCallback((ids: string[]) => setSelected(new Set(ids.slice(0, SELECTION_CAP))), []);
  const clear = useCallback(() => setSelected((cur) => (cur.size ? new Set() : cur)), []);
  const prune = useCallback((live: ReadonlySet<string>) => setSelected((cur) => {
    const next = new Set([...cur].filter((id) => live.has(id)));
    return next.size === cur.size ? cur : next;
  }), []);
  // one object per selection state: a consumer may depend on it without re-running on every render
  return useMemo(
    () => ({ selected, toggle, selectMany, clear, prune, atCap: selected.size >= SELECTION_CAP }),
    [selected, toggle, selectMany, clear, prune]);
}

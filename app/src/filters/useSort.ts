// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { SortingState } from "@tanstack/react-table";
import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { CARRIED_SEARCH } from "../lib/hubNav";

export const DEFAULT_SORT: SortingState = [{ id: "date", desc: true }];
const KEY = "sort";

/** Bad values already warned about. Every URL write re-parses the query, so a stale `?sort=` that
 *  stays in the URL while the user edits filters would otherwise warn on each keystroke. */
const warned = new Set<string>();
/** Test-only: forget the values already warned about, so each `it` can assert its own warnings
 *  whatever an earlier one parsed. */
export function __resetSortWarnings(): void {
  warned.clear();
}

/** `?sort=<colId>:<asc|desc>`; the default (date:desc) is omitted. A column id the register does not
 *  have (renamed, removed, hand-typed) or a malformed value falls back to the default with ONE warning
 *  per distinct value - never a crash, never an unsorted register. */
export function parseSort(sp: URLSearchParams, columnIds: readonly string[]): SortingState {
  const raw = sp.get(KEY);
  if (!raw) return DEFAULT_SORT;
  const parts = raw.split(":");
  const [id, dir] = parts;
  if (parts.length !== 2 || !columnIds.includes(id) || (dir !== "asc" && dir !== "desc")) {
    if (!warned.has(raw)) {
      warned.add(raw);
      console.warn(`ignoring ?sort=${raw}: not a register column and direction`);
    }
    return DEFAULT_SORT;
  }
  return [{ id, desc: dir === "desc" }];
}

export function writeSort(prev: URLSearchParams, sort: SortingState): URLSearchParams {
  const next = new URLSearchParams(prev);
  const s = sort[0];
  if (!s || (s.id === DEFAULT_SORT[0].id && s.desc === DEFAULT_SORT[0].desc)) next.delete(KEY);
  else next.set(KEY, `${s.id}:${s.desc ? "desc" : "asc"}`);
  return next;
}

/** The register's sort, in the URL. `sort` is NOT a filter key: writeFilters preserves it, and the
 *  Spending tab links never carry it (Explore and Reports have no register).
 *  `columnIds` must have a stable identity (pass a module constant such as TXN_COLUMN_IDS, never an
 *  array literal built in render): it is a dependency of the memoized parse, so a fresh array every
 *  render would re-parse - and re-validate - the query on each one. */
export function useSort(columnIds: readonly string[]): [SortingState, (s: SortingState) => void] {
  const [sp, setSp] = useSearchParams();
  const sort = useMemo(() => parseSort(sp, columnIds), [sp, columnIds]);
  const setSort = useCallback((s: SortingState) =>
    setSp((prev) => writeSort(prev, s), { replace: true, state: CARRIED_SEARCH }), [setSp]);
  return [sort, setSort];
}

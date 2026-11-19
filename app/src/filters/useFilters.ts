// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { CARRIED_SEARCH } from "../lib/hubNav";
import { EMPTY_FILTERS, Filters, parseFilters, writeFilters } from "./filters";

/** URL search params are the single source of truth for the global filter set. Every write goes
 *  through writeFilters (non-filter params survive) and stamps its history entry with CARRIED_SEARCH:
 *  a cleared or All-time state is a deliberate query, so Back onto that entry must not read as a
 *  fresh visit and re-apply the six-month default (filters/useSpendingDefaults.ts). */
export function useFilters(): [Filters, (patch: Partial<Filters>) => void, () => void] {
  const [sp, setSp] = useSearchParams();
  const filters = useMemo(() => parseFilters(sp), [sp]);
  const update = useCallback((patch: Partial<Filters>) =>
    setSp((prev) => writeFilters(prev, { ...parseFilters(prev), ...patch }), { replace: true, state: CARRIED_SEARCH }),
  [setSp]);
  const clear = useCallback(() =>
    setSp((prev) => writeFilters(prev, EMPTY_FILTERS), { replace: true, state: CARRIED_SEARCH }), [setSp]);
  return [filters, update, clear];
}

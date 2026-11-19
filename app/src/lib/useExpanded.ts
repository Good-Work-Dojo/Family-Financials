// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useCallback, useState } from "react";

/** Expand/collapse state for row expanders. "single" keeps at most one row
 *  open (re-clicking closes it); "multi" toggles rows independently. */
export function useExpanded(mode: "single" | "multi") {
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set<string>());
  const toggle = useCallback((id: string) => {
    setOpen((cur) => {
      if (mode === "single") return cur.has(id) ? new Set<string>() : new Set([id]);
      const next = new Set(cur);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }, [mode]);
  /** close one row, or everything when called with no id */
  const close = useCallback((id?: string) => {
    setOpen((cur) => {
      if (id === undefined) return cur.size ? new Set<string>() : cur;
      if (!cur.has(id)) return cur;
      const next = new Set(cur);
      next.delete(id);
      return next;
    });
  }, []);
  const isOpen = useCallback((id: string) => open.has(id), [open]);
  return { isOpen, toggle, close };
}

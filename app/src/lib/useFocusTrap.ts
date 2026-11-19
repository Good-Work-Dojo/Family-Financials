// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** The open traps, bottom to top. Every trap listens on `document`, so without this a ⌘K palette
 *  opened over the vendor drill closed both on one Escape and fought over Tab; only the topmost
 *  trap owns the keyboard, and the one below answers again once it unmounts. */
const stack: HTMLElement[] = [];

/** Modal-dialog keyboard behavior for an overlay: focus the first control on mount (or the dialog
 *  box itself with `initialFocus: "container"`, for a panel whose first control is an action that
 *  Enter must not fire), keep Tab inside `ref`, close on Escape, give focus back on unmount. Only
 *  the topmost open trap handles Escape and Tab, and only a trap that is topmost when it unmounts
 *  gives focus back to its opener. `onClose` is read through a ref so callers may pass
 *  an inline arrow without re-arming the effect every render. The element is read once on mount, so
 *  the dialog must be mounted only while open. */
export function useFocusTrap(
  ref: RefObject<HTMLElement | null>, onClose: () => void,
  opts: { initialFocus?: "first" | "container" } = {},
): void {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const initial = opts.initialFocus ?? "first";
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const previous = document.activeElement as HTMLElement | null;
    stack.push(el);
    const isTop = () => stack[stack.length - 1] === el;
    const focusables = () => Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE));
    (initial === "container" ? el : focusables()[0] ?? el).focus();
    const onKey = (e: KeyboardEvent) => {
      if (!isTop()) return;
      if (e.key === "Escape") { e.preventDefault(); closeRef.current(); return; }
      if (e.key !== "Tab") return;
      const items = focusables();
      if (!items.length) { e.preventDefault(); el.focus(); return; }
      const first = items[0], last = items[items.length - 1];
      const active = document.activeElement;
      const inside = !!active && el.contains(active);
      // focus that has escaped the dialog (or sits on the dialog box itself) re-enters at the
      // end Tab was heading for; at either edge Tab wraps instead of leaving
      if (e.shiftKey && (!inside || active === first || active === el)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (!inside || active === last)) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      const at = stack.lastIndexOf(el);
      // only the topmost trap hands focus back: a lower one that unmounts first (its host re-rendered
      // it away under a dialog still open) must not drag focus out of the dialog above it
      const wasTop = at === stack.length - 1;
      if (at >= 0) stack.splice(at, 1);
      if (wasTop) previous?.focus?.();
    };
  }, [ref, initial]);
}

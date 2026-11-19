// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { type CSSProperties, useCallback, useLayoutEffect, useRef, useState } from "react";

/** What a sideways scroller shows about itself: whether its content is wider than its box (`overflows`), and whether
 *  more of it lies to the right of what is on screen (`more`); a pixel of rounding is no overflow. */
export function scrollHint(el: { scrollLeft: number; clientWidth: number; scrollWidth: number }):
  { overflows: boolean; more: boolean } {
  const overflows = el.scrollWidth > el.clientWidth + 1;
  return { overflows, more: overflows && el.scrollLeft + el.clientWidth < el.scrollWidth - 1 };
}

/** The fade a scroller with more to its right draws at its right edge: its own content goes transparent over the last
 *  2rem (a mask, so it needs no colour of its own and reads the same on every surface in both themes). */
export const SCROLL_FADE: CSSProperties = {
  maskImage: "linear-gradient(to left, transparent, #000 2rem)",
  WebkitMaskImage: "linear-gradient(to left, transparent, #000 2rem)",
};

/** Follows a sideways scroller (the element the returned ref is put on): `scrollHint` of it, read again when it
 *  scrolls, when it or its content changes size and when the window does. Before the first read nothing overflows. */
export function useScrollHint<T extends HTMLElement>(): { ref: (el: T | null) => void; overflows: boolean; more: boolean } {
  const [el, setEl] = useState<T | null>(null);
  const [hint, setHint] = useState({ overflows: false, more: false });
  const last = useRef(hint);
  const ref = useCallback((node: T | null) => setEl(node), []);
  useLayoutEffect(() => {
    if (!el) return;
    const read = () => {
      const next = scrollHint(el);
      if (next.overflows !== last.current.overflows || next.more !== last.current.more) {
        last.current = next;
        setHint(next);
      }
    };
    read();
    el.addEventListener("scroll", read, { passive: true });
    window.addEventListener("resize", read);
    let ro: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(read);
      ro.observe(el);
      for (const child of Array.from(el.children)) ro.observe(child);
    }
    return () => {
      el.removeEventListener("scroll", read);
      window.removeEventListener("resize", read);
      ro?.disconnect();
    };
  }, [el]);
  return { ref, ...hint };
}

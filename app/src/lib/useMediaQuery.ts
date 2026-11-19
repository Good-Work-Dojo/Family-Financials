// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useEffect, useState } from "react";

/** Subscribe to a CSS media query; re-renders when it starts/stops matching. */
export function useMediaQuery(query: string): boolean {
  // a runtime without matchMedia (jsdom) is "does not match", never a crash
  const get = () =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
    && window.matchMedia(query).matches;
  const [matches, setMatches] = useState(get);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange(); // sync in case the query changed between render and effect
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}

/** True below Tailwind's `md` breakpoint (768px) — i.e. phones / narrow tablets. */
export function useIsMobile(): boolean {
  return useMediaQuery("(max-width: 767px)");
}

/** True when the main pointer is coarse (a finger): what lib/pointerWords.ts words a page by, never the width. */
export function useCoarsePointer(): boolean {
  return useMediaQuery("(pointer: coarse)");
}

/** True when the viewer asked the OS for less motion (prefers-reduced-motion: reduce). EChart turns chart
 *  animation off for it; the screenshot driver emulates it, so no chart is captured mid-animation. */
export function useReducedMotion(): boolean {
  return useMediaQuery("(prefers-reduced-motion: reduce)");
}

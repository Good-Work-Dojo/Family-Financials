// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useEffect, useState } from "react";
import { useCoarsePointer } from "../lib/useMediaQuery";

/** an element whose title names or explains an action it takes itself: a tap does that, never shows the title */
const ACTS = "button, a, input, select, textarea, summary, [role='button'], [role='link'], [role='checkbox'], [role='switch']";
/** the bubble's room: 16px from each edge of the screen, at most 288px wide */
const EDGE = 16, MAX_W = 288, GAP = 6;

export interface TitleBubble { text: string; top: number; left: number; above: boolean }

/** Where a tap on `target` shows a title, or null: the nearest element at or above it with a non-empty `title` that
 *  takes no action of its own (a chip, a dot, a dagger, a table cell, a label's ⓘ), placed under it - over it when
 *  there is no room below - and kept inside the screen. */
export function titleBubbleFor(target: Element | null, screen: { width: number; height: number }): TitleBubble | null {
  // a tap on or inside a control (the label's own input included) is the control's
  const el = target?.closest("[title]");
  if (!el || target!.closest(ACTS)) return null;
  const text = (el.getAttribute("title") ?? "").trim();
  if (!text) return null;
  const r = el.getBoundingClientRect();
  const left = Math.max(EDGE, Math.min(r.left, screen.width - EDGE - Math.min(MAX_W, screen.width - 2 * EDGE)));
  const above = r.bottom + GAP + 64 > screen.height && r.top > screen.height / 2;
  return { text, left, above, top: above ? r.top - GAP : r.bottom + GAP };
}

/** A title is a mouse's: a finger never hovers, so what a `title` alone says (a chip's meaning, a dot's state, a
 *  dagger's footnote, a slider's source) was out of a phone's reach. On a coarse pointer a tap on such an element shows
 *  its title in a bubble under it, until the next touch anywhere, a scroll or Escape; with a fine pointer this listens
 *  to nothing. Mounted once, around the whole app. The bubble is not read out: a screen reader already reads the title
 *  (and a Badge repeats it as text). */
export default function TouchTitles() {
  const touch = useCoarsePointer();
  const [bubble, setBubble] = useState<TitleBubble | null>(null);
  useEffect(() => {
    if (!touch) { setBubble(null); return; }
    const close = () => setBubble(null);
    // only ever opens: the touch that came before the click has closed the last one, and a label passes its click on to
    // its input, which must not close the bubble its ⓘ just opened
    const onClick = (e: MouseEvent) => {
      const found = titleBubbleFor(e.target instanceof Element ? e.target : null,
        { width: window.innerWidth, height: window.innerHeight });
      if (found) setBubble(found);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("pointerdown", close, true);
    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("pointerdown", close, true);
      document.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [touch]);
  if (!bubble) return null;
  return (
    <div aria-hidden="true" data-testid="touch-title"
      style={{ top: bubble.top, left: bubble.left, maxWidth: `min(${MAX_W}px, calc(100vw - ${2 * EDGE}px))`,
        transform: bubble.above ? "translateY(-100%)" : undefined }}
      className="pointer-events-none fixed z-50 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200 shadow-lg">
      {bubble.text}
    </div>
  );
}

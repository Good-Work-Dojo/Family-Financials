// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { ReactNode } from "react";
import { usePointerWords } from "../lib/pointerWords";
import { NAV_DRAWER_ID } from "./NavDrawer";

/** The phone's top bar (below `md`; the sidebar owns these on a desktop): the menu button, the place's title, the
 *  freshness pill (a slot - it reads the data context), the privacy eye and the ⌘K search.
 *
 *  Two things must not live only inside the drawer, two taps away: the Review inbox count and the privacy toggle. The
 *  count rides on the menu button as a chip (the inbox is not invisible until ☰ is opened) and the eye is a
 *  button here that says it is ON (amber, slashed) - so hiding every amount when someone walks up is one tap. */
export default function MobileTopBar({ title, reviewCount, privacy, menuOpen, onTogglePrivacy, onOpenMenu, onOpenSearch, refresh,
  notice }: {
  title: string;
  /** the drawer the menu button opens is open (components/NavDrawer.tsx): the button's `aria-expanded` */
  menuOpen: boolean;
  /** transactions still wanting a category (the sidebar badge's number); 0 draws no chip */
  reviewCount: number;
  privacy: boolean;
  onTogglePrivacy: () => void;
  onOpenMenu: () => void;
  onOpenSearch: () => void;
  refresh: ReactNode;
  /** a line the bar carries under its controls while it has one (a quiet reload's "Updated"): inside the bar's own
   *  area, which grows by it, never over the page */
  notice?: ReactNode;
}) {
  // the title wraps onto a second line at a word rather than being cut inside one ("Cash Flow · Income statement")
  const { click } = usePointerWords();
  return (
    <header className="sticky top-0 z-30 border-b border-zinc-800 bg-zinc-950/95 backdrop-blur md:hidden print:hidden">
    <div className="flex h-12 items-center gap-1 px-2">
      <div className="flex shrink-0 items-center">
        <button onClick={onOpenMenu} aria-label="Open menu" aria-expanded={menuOpen} aria-controls={NAV_DRAWER_ID}
          className="flex h-11 w-11 items-center justify-center rounded-lg text-lg text-zinc-300 hover:bg-zinc-900">☰</button>
        {reviewCount > 0 && (
          // a sibling of the button, not inside it: the button's accessible name stays "Open menu"
          <span role="img" aria-label={`${reviewCount} to review`} data-testid="topbar-review-count"
            className="pointer-events-none -ml-1 mr-1 min-w-[1.125rem] rounded-full bg-amber-500 px-1 text-center text-[10px] font-semibold leading-[1.125rem] tabular-nums text-zinc-950">
            {reviewCount > 99 ? "99+" : reviewCount}
          </span>
        )}
      </div>
      <span data-testid="topbar-title" className="line-clamp-2 min-w-0 break-words text-sm font-semibold leading-tight text-zinc-200">{title}</span>
      <div className="ml-auto flex shrink-0 items-center gap-1">
        {refresh}
        <button onClick={onTogglePrivacy} aria-pressed={privacy} aria-label="Hide amounts" data-testid="privacy-toggle-bar"
          title={privacy ? `Amounts are hidden - ${click} to show them` : "Hide every dollar amount on screen"}
          className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-lg hover:bg-zinc-900 ${
            privacy ? "bg-amber-500/15 text-amber-300" : "text-zinc-300"}`}>
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8"
            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
            <circle cx="12" cy="12" r="3" />
            {privacy && <path d="M4 4l16 16" />}
          </svg>
        </button>
        <button onClick={onOpenSearch} aria-label="Jump to a page, account, category, or vendor"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-zinc-300 hover:bg-zinc-900">🔍</button>
      </div>
    </div>
    {notice}
    </header>
  );
}

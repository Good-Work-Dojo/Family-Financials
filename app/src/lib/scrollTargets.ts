// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The cards a link can land on. A hash link cannot target an in-page anchor under HashRouter, so a page whose cards
// can be landed on reads the `card` query key itself on arrival (`useScrollToCardParam`): it scrolls the card into view
// and moves focus to the card's heading, so a keyboard or screen-reader user goes on from the card. Each card carries
// its id through components/ui.tsx `Card`'s `id`, which also keeps its top edge clear of the 48px sticky phone header
// and makes its heading focusable by script. Settings' Elsewhere card links to the ones on Data, Budget and Investments
// (`cardPath`); Planning's KPIs scroll to its two (sections/planning/ForwardLookStrip.tsx); the pages that need a value
// entered by hand link to Settings' Manual assets card (components/ManualAssetsLink.tsx).
import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";

/** DOM ids of the cards a `?card=` URL or a KPI can scroll to, each stamped on its card by the page that shows it;
 *  smoke.test.tsx asserts each is on its real page */
export const SCROLL_TARGETS = {
  /** Planning → Projections (sections/Tools.tsx) */
  runway: "emergency-fund", bearCase: "market-shock",
  /** Data (sections/data/PrivacyCard.tsx, ProjectionDefaultsCard.tsx, SavedViewsCard.tsx; sections/Data.tsx: the
   *  caveats, which name the accounts not yet reported, and what the last ingest read, file by file) */
  privacy: "privacy-mode", projectionDefaults: "projection-defaults", savedViews: "saved-views",
  caveats: "caveats", ingestRead: "ingest-read",
  /** Cash Flow → Budget (sections/Budget.tsx) */
  budget: "budget",
  /** Accounts → Investments (sections/investments/TargetMixCard.tsx) */
  targetMix: "target-mix",
  /** Settings (sections/settings/ManualAssetsCard.tsx): where a home, a vehicle or a debt no account reports is entered */
  manualAssets: "manual-assets",
  /** Settings (sections/settings/AiCard.tsx): what AI categorization sends and its switch; the About card links here */
  aiCategorization: "ai-categorization",
} as const;
export type ScrollTarget = (typeof SCROLL_TARGETS)[keyof typeof SCROLL_TARGETS];

/** the query key that scrolls a page to one of the cards above on arrival: `#/data?card=privacy-mode` */
export const CARD_PARAM = "card";

/** True for an id this module names - the only values the `card` query key may scroll to. */
export const isScrollTarget = (id: string | null): id is ScrollTarget =>
  id !== null && (Object.values(SCROLL_TARGETS) as string[]).includes(id);

/** the in-app path that opens the page at `route` on its card `id`: `/data?card=privacy-mode` */
export const cardPath = (route: string, id: ScrollTarget): string => `${route}?${CARD_PARAM}=${id}`;

/** Scrolls the card whose DOM id is `id` into view, its top edge at the top of the screen (smoothly, unless the
 *  reader asked for reduced motion), and with `focus` moves focus to its heading without a second scroll. A card that
 *  is not rendered (its data is gated) leaves nothing to scroll to: the call is then a no-op by design. */
export function scrollToCard(id: string, { focus = false }: { focus?: boolean } = {}): void {
  const el = document.getElementById(id);
  if (!el) return;
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView?.({ behavior: reduce ? "auto" : "smooth", block: "start" });
  if (focus) el.querySelector<HTMLElement>("h1, h2, h3")?.focus({ preventScroll: true });
}

/** `?card=<id>` (CARD_PARAM) lands on that card: the page reads the key on arrival, scrolls to the card and moves
 *  focus to its heading. An unknown value is ignored (nothing else may name an element id), and a card that is not on
 *  the page - another page's, or one whose data is gated off - is simply not there to scroll to. Called once per page,
 *  by the component that renders the card (or a parent of it), so the card is in the document when its effect runs. */
export function useScrollToCardParam(): void {
  const [sp] = useSearchParams();
  const card = sp.get(CARD_PARAM);
  useEffect(() => { if (isScrollTarget(card)) scrollToCard(card, { focus: true }); }, [card]);
}

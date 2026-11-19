// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { matchPath, NavLink, useLocation } from "react-router-dom";
import { CARRIED_SEARCH } from "../lib/hubNav";
import { SCROLL_FADE, useScrollHint } from "../lib/useScrollHint";

export interface RouteTab {
  to: string;
  label: string;
  /** exact-match this tab (use on the hub's index route) */
  end?: boolean;
  /** carry the current ?query to this tab: `true` keeps all of it, a list keeps only those keys
   *  (Spending's Explore and Transactions share the FilterBar's URL state, so they carry exactly
   *  the filter keys and leave page-scoped ones like Recurring's `win` behind); every other tab
   *  drops the query on purpose */
  keepSearch?: boolean | readonly string[];
  /** route patterns that are "inside" this tab without being its route: a hub's extra routes
   *  (`/accounts/:id`) belong to the index tab, which is highlighted there (never on a path another
   *  tab owns - `/accounts/investments` also matches `/accounts/:id`) and `end` is lifted there so
   *  NavLink itself marks the tab current (`aria-current="page"`) */
  activeOn?: readonly string[];
}

/** the part of `search` ("?a=1&b=2" or "") a tab carries; a bare list result has no "?" at all */
function carried(keep: RouteTab["keepSearch"], search: string): string {
  if (!keep) return "";
  if (keep === true) return search;
  const from = new URLSearchParams(search);
  const out = new URLSearchParams();
  for (const [k, v] of from) if (keep.includes(k)) out.append(k, v);
  const s = out.toString();
  return s ? `?${s}` : "";
}

/** The scroller clips anything drawn outside the nav box, so a default (outset) keyboard focus
 *  outline loses its top and bottom edges: draw the ring inside the link instead. */
const FOCUS_RING = "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-400";

/**
 * Underline-style top tabs for hubs. Sub-route driven (NavLinks, not ARIA tabs) so the back
 * button walks tab history and page-scoped query params drop on tab switch unless a tab opts
 * into keepSearch. The bar scrolls sideways on phones instead of wrapping or clipping, and fades out
 * at its right edge while more tabs lie that way; a tab is 44px tall below md. The
 * outer div carries the baseline so the active underline (nav -mb-px) is never hidden by the
 * scroller's overflow box, and the focus ring is drawn inset for the same reason. A link that
 * goes from one query-sharing tab to another also carries the CARRIED_SEARCH router state
 * (lib/hubNav.ts).
 */
export default function RouteTabs({ tabs, ariaLabel = "Sections" }: { tabs: RouteTab[]; ariaLabel?: string }) {
  const { pathname, search } = useLocation();
  const here = pathname.replace(/\/+$/, "") || "/";
  const sharesHere = !!tabs.find((t) => t.to === here)?.keepSearch;
  const ownedByATab = tabs.some((t) => t.to === here);
  const extraOf = (t: RouteTab) => !ownedByATab && (t.activeOn ?? []).some((p) => matchPath(p, here) !== null);
  // a bar wider than the screen fades out at its right edge while more tabs lie that way (lib/useScrollHint.ts)
  const { ref, more } = useScrollHint<HTMLElement>();
  return (
    <div className="border-b border-zinc-800 print:hidden">
      <nav ref={ref} aria-label={ariaLabel} className="-mb-px flex gap-1 overflow-x-auto" style={more ? SCROLL_FADE : undefined}>
        {tabs.map((t) => {
          const extra = extraOf(t);
          // on an extra route the index tab's own prefix match is what makes NavLink active, and so sets
          // aria-current - an exact (`end`) match would leave it visually underlined but not announced
          return (
            <NavLink key={t.to} to={t.keepSearch ? { pathname: t.to, search: carried(t.keepSearch, search) } : t.to}
              state={t.keepSearch && sharesHere ? CARRIED_SEARCH : undefined} end={t.end && !extra}
              className={({ isActive }) =>
                `shrink-0 whitespace-nowrap border-b-2 px-3 py-3 text-sm md:py-2 ${FOCUS_RING} ${isActive || extra
                  ? "border-zinc-100 font-medium text-zinc-100"
                  : "border-transparent text-zinc-400 hover:text-zinc-200"}`}>
              {t.label}
            </NavLink>
          );
        })}
      </nav>
    </div>
  );
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { MouseEventHandler, ReactNode } from "react";
import { Link } from "react-router-dom";

/** an account name that links its page: a resting underline and colour, not only a hover (a finger has none) */
export const ACCOUNT_LINK = "text-blue-400 underline decoration-blue-400/40 underline-offset-2 hover:decoration-blue-400";

/** the quiet look, for a column of names that are links only incidentally (the register's Account column): ordinary secondary text
 *  at rest, the link's blue and underline on hover or keyboard focus. A finger sees no difference; a pointer learns it on arrival. */
export const ACCOUNT_LINK_QUIET = "text-zinc-400 hover:text-blue-400 hover:underline focus-visible:text-blue-400 focus-visible:underline";

/** The one way an account's name becomes a link to `/accounts/:id`. Every place that prints an account name - Balances,
 *  the Investments holdings, the Data freshness list, the Second House funding plan, the mortgage card - uses this (or
 *  `ACCOUNT_LINK` where the markup around it is its own), so the link always looks like a link and the route is spelled once. */
export default function AccountLink({ id, children, className = "", onClick, tabIndex, appearance = "link" }: {
  id: string; children: ReactNode; className?: string; onClick?: MouseEventHandler<HTMLAnchorElement>;
  /** "link" (default): blue and underlined at rest, a link that looks like one. "quiet": ACCOUNT_LINK_QUIET */
  appearance?: "link" | "quiet";
  /** -1 for the register's per-row duplicate: the register is one tab stop (app/AGENTS.md, Accessibility), so thirty identical
   *  links per screen are not tab stops - the keyboard's way is the "Account page →" link in the open row (no tabindex at all) */
  tabIndex?: number;
}) {
  return (
    <Link to={`/accounts/${encodeURIComponent(id)}`} onClick={onClick} tabIndex={tabIndex} className={`${appearance === "quiet" ? ACCOUNT_LINK_QUIET : ACCOUNT_LINK} ${className}`.trim()}>
      {children}
    </Link>
  );
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useRouteOpen } from "../lib/modules";

/** A link into a page that may not show for this household (App.tsx PAGES `module` / `needs`): a link while the page
 *  shows, and `whenOff` while it does not — by default its words as plain text, for a sentence that still reads true
 *  with the page off; `null` where nothing should stand (a bare "what-ifs →"). Never a link to the module-off page.
 *  Every literal link or mention of such a page goes through it (App.test.tsx scans for any other). */
export default function GatedLink({ to, className, whenOff, children }: {
  to: string; className?: string; whenOff?: ReactNode; children: ReactNode;
}) {
  const open = useRouteOpen();
  if (open(to)) return <Link to={to} className={className}>{children}</Link>;
  return <>{whenOff === undefined ? children : whenOff}</>;
}

/** Words about a page that may not show, rendered only while it does: a sentence that would point at a page the
 *  household does not have says nothing instead. Its `to` is read like a GatedLink's (App.test.tsx). */
export function WhileShown({ to, children }: { to: string; children: ReactNode }) {
  const open = useRouteOpen();
  return open(to) ? <>{children}</> : null;
}

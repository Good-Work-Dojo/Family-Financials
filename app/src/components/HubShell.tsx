// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { Outlet } from "react-router-dom";
import RouteTabs, { type RouteTab } from "./Tabs";

/** The one hub shell: h1 + tabs derived from the PAGES manifest (App.tsx hubTabs) + the active
 *  tab. The header owns the spacing because the tab element below is wrapped in a
 *  display:contents div (App.tsx pageRoute), which a space-y-* parent cannot space.
 *  Below `md` the h1 is screen-reader-only: the phone's top bar already names the place ("Spending · Transactions")
 *  and the tab bar sits under it, so the visible h1 was a third copy of the same location. It stays in the
 *  document for the heading outline, and the header is a flex column so the hidden h1 adds no gap.
 *  A hub with one tab to show draws no tab bar (Planning while its other pages are off is Projections alone), and
 *  `heading={false}` leaves the h1 out where the page shown carries its own (the module-off page). */
export default function HubShell({ title, tabs, heading = true }: { title: string; tabs: RouteTab[]; heading?: boolean }) {
  const bar = tabs.length > 1;
  return (
    <div>
      {(heading || bar) && (
        // with no tab bar the phone shows nothing of the header (its h1 is screen-reader-only there), so no gap either
        <header className={`flex flex-col gap-4 ${bar ? "mb-4" : "md:mb-4"}`}>
          {heading && <h1 className="sr-only text-xl font-bold md:not-sr-only">{title}</h1>}
          {bar && <RouteTabs tabs={tabs} ariaLabel={`${title} sections`} />}
        </header>
      )}
      <Outlet />
    </div>
  );
}

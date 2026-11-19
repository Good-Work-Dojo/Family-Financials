// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { Link } from "react-router-dom";
import { Card } from "../../components/ui";
import { useData } from "../../data/DataContext";
import { firstRunState, replacedInFirstRun } from "../../lib/firstRun";
import { useRouteOpen } from "../../lib/modules";
import { cardPath, SCROLL_TARGETS, type ScrollTarget } from "../../lib/scrollTargets";

// Settings → Elsewhere: the settings that live beside what they change, each a link that lands on its card (`?card=`,
// lib/scrollTargets.ts). A link is shown only while its page shows and has something to set: not while the first-run
// page stands in for its page (a household with no data yet, lib/firstRun.ts), nor, in that state, when its card only
// works on what the data brings (`needsData`); the card says in one line that those come with data. Nor while its page
// is switched off (lib/modules.ts useRouteOpen, GatedLink's rule).

/** one setting on another page: its name, the page's route and place title (App.tsx titleFor; App.test.tsx pins the
 *  two), the card it lands on and what it sets; `needsData`: a card with nothing to set before there is data (the saved
 *  views and reports are made on pages the first-run page replaces) */
export interface ElsewhereLink {
  label: string; route: string; page: string; card: ScrollTarget; what: string; needsData?: true;
}

export const ELSEWHERE: readonly ElsewhereLink[] = [
  { label: "Privacy mode", route: "/data", page: "Data", card: SCROLL_TARGETS.privacy,
    what: "Hides every dollar amount on this device." },
  { label: "Projection defaults", route: "/data", page: "Data", card: SCROLL_TARGETS.projectionDefaults,
    what: "The inflation, withdrawal and tax rates every projection starts from." },
  { label: "Saved views and reports", route: "/data", page: "Data", card: SCROLL_TARGETS.savedViews,
    what: "Rename or delete the family's saved views and reports.", needsData: true },
  { label: "Budget", route: "/cash-flow/budget", page: "Cash Flow · Budget", card: SCROLL_TARGETS.budget,
    what: "A monthly budget for any spending category, in place of the typical month." },
  { label: "Target mix", route: "/accounts/investments", page: "Accounts · Investments", card: SCROLL_TARGETS.targetMix,
    what: "The family's target split of stocks, bonds, cash and alternatives." },
];

/** "A", "A and B", "A, B and C" */
const andList = (names: readonly string[]): string =>
  names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;

export default function ElsewhereCard() {
  const { data } = useData();
  const open = useRouteOpen();
  const firstRun = firstRunState(data.manifest, data) !== null;
  const later = firstRun ? ELSEWHERE.filter((e) => replacedInFirstRun(e.route) || e.needsData) : [];
  const shown = ELSEWHERE.filter((e) => !later.includes(e) && open(e.route));
  return (
    <Card testId="card-elsewhere" title="Elsewhere">
      <p className="text-sm text-zinc-300">Settings that live beside what they change, on other pages.</p>
      {shown.length > 0 && (
        <ul className="mt-2 space-y-2">
          {shown.map((e) => (
            <li key={e.card}>
              <Link to={cardPath(e.route, e.card)}
                className="inline-flex min-h-[40px] items-center text-sm text-blue-400 hover:underline md:min-h-0">
                {e.label}
              </Link>
              <span className="text-xs text-zinc-500"> · {e.page}</span>
              <p className="text-xs text-zinc-400">{e.what}</p>
            </li>
          ))}
        </ul>
      )}
      {later.length > 0 && (
        <p data-testid="elsewhere-later" className="mt-2 text-xs text-zinc-500">
          {andList(later.map((e) => e.label))} {later.length === 1 ? "appears" : "appear"} here once the dashboard has data.
        </p>
      )}
    </Card>
  );
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { Link } from "react-router-dom";
import { usePointerWords } from "../lib/pointerWords";
import { Card } from "./ui";

/** The catch-all route (App.tsx `<Route path="*">`): a stale bookmark or a mistyped hash lands here
 *  instead of a blank page. Any path no page owns lands here: REDIRECTS is empty, so an old
 *  path gets no redirect. Not a PAGES entry: never in the sidebar, ⌘K, the smoke route loop or the screenshot driver. */
export default function PageMoved({ onSearch }: { onSearch: () => void }) {
  // a finger on a phone is told about the top bar's 🔍; everyone else, a tablet's finger included, about ⌘K
  const { searchButton } = usePointerWords();
  return (
    <div className="space-y-4" data-testid="page-moved">
      <h1 className="text-xl font-bold">Page not found</h1>
      <Card>
        <p className="text-sm text-zinc-300">There is no page at this address.</p>
        <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
          <Link to="/" className="inline-flex min-h-11 items-center text-blue-400 hover:underline md:min-h-0">
            Go to Home →
          </Link>
          <button onClick={onSearch}
            className="inline-flex min-h-11 items-center rounded-md border border-zinc-700 px-3 text-zinc-300 hover:bg-zinc-800 md:min-h-0 md:py-1">
            Find a page{searchButton ? "" : " (⌘K)"}
          </button>
        </div>
        <p className="mt-2 text-[11px] text-zinc-500">
          {searchButton ? "Tap 🔍 at the top" : "Press ⌘K (Ctrl+K)"} to jump to any page, category or vendor.
        </p>
      </Card>
    </div>
  );
}

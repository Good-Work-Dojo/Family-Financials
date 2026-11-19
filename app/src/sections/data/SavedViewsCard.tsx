// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { type ReactNode, useState } from "react";
import { Link } from "react-router-dom";
import { Card, LocalOnlyNotice } from "../../components/ui";
import { useData } from "../../data/DataContext";
import { useSavedReports } from "../../data/useSavedReports";
import { useSavedViews } from "../../data/useSavedViews";
import { PRESET_LABELS, parseWin } from "../../filters/dateRange";
import { countActive, parseFilters } from "../../filters/filters";
import { firstRunState } from "../../lib/firstRun";
import { CARRIED_SEARCH } from "../../lib/hubNav";
import { PREVIEW_STAMP } from "../../lib/ids";
import { MAX_SAVED, MAX_SAVED_NAME, type SavedKey, tryWithRenamedSaved } from "../../lib/prefs";
import { describeReport } from "../../lib/reportSpec";
import { savedFilterPath, savedReportPath, saveRefusalText, staleOpenPath } from "../../lib/savedFilters";
import { SCROLL_TARGETS } from "../../lib/scrollTargets";
import { usePointerWords } from "../../lib/pointerWords";

/** Data → Saved views & reports: the family's named Transactions filters (prefs.saved_filters) and Reports
 *  specs (prefs.reports), with rename and delete. One row component serves both lists, so they cannot drift
 *  apart. A saved item whose category left the data is stale: still in the doc (prefs are last-write-wins, so
 *  a device with an older category list must not delete the family's item), named here with its reason,
 *  openable as saved from here (its filters, unknown keys dropped) and offered nowhere else. Settings links here
 *  (`?card=`, lib/scrollTargets.ts). */
export default function SavedViewsCard() {
  const { prefs, data } = useData();
  const pagesOpen = firstRunState(data.manifest, data) === null;
  const searchKey = usePointerWords().search;
  const views = useSavedViews();
  const reports = useSavedReports();
  const nViews = prefs.doc.saved_filters.length, nReports = prefs.doc.reports.length;
  return (
    <Card testId="card-saved-views" id={SCROLL_TARGETS.savedViews} title={`Saved views & reports — ${nViews} of ${MAX_SAVED} views · ${nReports} of ${MAX_SAVED} reports`}>
      {prefs.localOnly && (
        <p className="mb-2 text-[11px]">
          <LocalOnlyNotice what="saved views and reports" refused={prefs.refused} />
          {prefs.refused === 413 && " Delete a saved view or report to make room, and edits sync again."}
        </p>
      )}
      {/* the way to save one names Spending's pages, so only while they show the household's data: before its first
          data the first-run page stands in for them */}
      {nViews + nReports === 0 && (
        <p className="text-xs text-zinc-500">
          No saved views or reports yet.{pagesOpen && <>{" "}Filter the{" "}
          <Link to="/spending/transactions" className="text-blue-400 hover:underline">Transactions</Link>{" "}
          tab, then ★ Save view in the filter bar, or press “★ Save report” on{" "}
          <Link to="/spending/reports" className="text-blue-400 hover:underline">Spending → Reports</Link>.</>}
        </p>
      )}
      {nViews > 0 && (
        <Section heading="Views" staleNote={`not offered in the filter bar or ${searchKey}`}
          rows={views.valid.map((f) => (
            <SavedRow key={f.id} k="saved_filters" id={f.id} name={f.name} to={savedFilterPath(f)}
              note={filterCount(f.query)} />))}
          stale={views.stale.map((s) => (
            <SavedRow key={s.filter.id} k="saved_filters" id={s.filter.id} name={s.filter.name} reason={s.reason}
              to={staleOpenPath(s.filter)} note={filterCount(s.filter.query)} />))} />
      )}
      {nReports > 0 && (
        <Section heading="Reports" staleNote={`not offered on the Reports tab or in ${searchKey}`} first={nViews === 0}
          rows={reports.valid.map((r) => (
            <SavedRow key={r.id} k="reports" id={r.id} name={r.name} to={savedReportPath(r)}
              note={`${describeReport(r.spec)} · ${filterCount(r.spec.filters)}`} />))}
          stale={reports.stale.map((s) => (
            <SavedRow key={s.report.id} k="reports" id={s.report.id} name={s.report.name} reason={s.reason}
              to={savedReportPath(s.report)} note={`${describeReport(s.report.spec)} · ${filterCount(s.report.spec.filters)}`} />))} />
      )}
      <p className="mt-3 text-[10px] text-faint">
        A view is the Transactions tab's filter query and a report is a Spending → Reports spec (its filters plus group-by,
        interval, measure and chart), each saved for the whole family. A view or report saved on a preset window (3M, 6M, 12M, YTD - the
        automatic last 6 months included) rolls with the data: it reopens on that window as of the newest day then. Dates you
        picked yourself are saved as the exact days. A stale item names a category that is no longer in the data, or a filter this version does not know — open it
        as saved to see what it still matches, then delete it or re-save it from Spending.
      </p>
    </Card>
  );
}

const filterCount = (query: string) => {
  const sp = new URLSearchParams(query);
  const n = countActive(parseFilters(sp));
  const win = parseWin(sp);
  // a rolling window is not a date filter in countActive (it has no dates yet): say it beside the count
  return `${n} filter${n === 1 ? "" : "s"}${win && win !== "all" ? ` · rolling ${PRESET_LABELS[win]}` : ""}`;
};

/** one list under a heading: its valid rows, then its stale ones under an amber count */
function Section({ heading, rows, stale, staleNote, first = true }: {
  heading: string; rows: ReactNode[]; stale: ReactNode[]; staleNote: string; first?: boolean;
}) {
  const noun = heading === "Views" ? "view" : "report";
  return (
    <div className={first ? "" : "mt-3 border-t border-zinc-800 pt-3"}>
      <h3 className="mb-1 text-[11px] font-medium text-zinc-400">{heading}</h3>
      {rows.length > 0 && <ul className="space-y-1 text-xs">{rows}</ul>}
      {stale.length > 0 && (
        <div className={rows.length > 0 ? "mt-2" : ""}>
          <p className="mb-1 text-[11px] text-amber-300">
            {stale.length} stale {noun}{stale.length === 1 ? "" : "s"} — {staleNote}
          </p>
          <ul className="space-y-1 text-xs">{stale}</ul>
        </div>
      )}
    </div>
  );
}

const TAP = "min-h-[40px] md:min-h-0";
const WRAP = "min-w-0 max-w-full [overflow-wrap:anywhere]";

/** per list: the noun the labels and messages use, and the prefs ops that rename and delete (the store has a
 *  method pair per list; the saved-item layer under them is the same for both) */
const LISTS = {
  saved_filters: { noun: "view", remove: "removeSavedFilter", rename: "renameSavedFilter" },
  reports: { noun: "report", remove: "removeReport", rename: "renameReport" },
} as const satisfies Record<SavedKey, { noun: string; remove: string; rename: string }>;

/** one saved item: a link (a stale one's name is plain text, with its reason and an "open as saved" link beside
 *  it), a note, rename and delete. `to` is where the name goes: the saved view's or report's own path, or for a
 *  stale item the path that opens it as saved. Every link carries CARRIED_SEARCH, so an item saved without a
 *  date window opens all-time instead of getting the six-month default. */
function SavedRow({ k, id, name, to, note, reason }: {
  k: SavedKey; id: string; name: string; to: string; note: string; reason?: string;
}) {
  const { prefs } = useData();
  const { noun, remove, rename } = LISTS[k];
  const [draft, setDraft] = useState<string | null>(null);   // non-null while renaming
  const [refused, setRefused] = useState(false);
  const stopRenaming = () => { setDraft(null); setRefused(false); };
  // the prefs op's own verdict on the draft, so a full prefs doc says so instead of "cannot be saved"; a preview, so its
  // stamp is PREVIEW_STAMP (as long as a real one) and no clock is read per render or keystroke
  const verdict = draft === null ? null : tryWithRenamedSaved(prefs.doc, k, id, draft, PREVIEW_STAMP);
  const full = verdict === "full";

  if (draft !== null) {
    return (
      <li className="flex flex-wrap items-center gap-1">
        <form className="flex flex-wrap items-center gap-1" onSubmit={(e) => {
          e.preventDefault();
          if (prefs[rename](id, draft)) stopRenaming(); else setRefused(true);
        }}>
          <input autoFocus aria-label={`New name for saved ${noun} ${name}`} value={draft} maxLength={MAX_SAVED_NAME}
            onChange={(e) => { setDraft(e.target.value); setRefused(false); }}
            onKeyDown={(e) => { if (e.key === "Escape") stopRenaming(); }}
            className="w-44 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-base sm:text-xs" />
          <button type="submit" disabled={!draft.trim() || full}
            className={`${TAP} rounded-md border border-zinc-700 px-2 text-zinc-200 disabled:opacity-40 md:py-0.5`}>Save</button>
          <button type="button" onClick={stopRenaming} className={`${TAP} px-1 text-zinc-500`}>cancel</button>
          {full && <span role="alert" className="text-amber-300">{saveRefusalText("full", noun)}</span>}
          {refused && !full && <span role="alert" className="text-amber-300">that name cannot be saved</span>}
        </form>
      </li>
    );
  }
  return (
    <li className={`flex flex-wrap items-center gap-x-3 gap-y-0.5 ${reason ? "opacity-70" : ""}`}>
      {/* a name is up to 80 characters: it wraps (anywhere, even inside one long word) rather than pushing the row wider than a phone */}
      {reason
        ? <span className={`${WRAP} text-zinc-300`}>{name}</span>
        : <Link to={to} state={CARRIED_SEARCH}
            className={`inline-flex ${TAP} ${WRAP} items-center text-blue-400 hover:underline`}>{name}</Link>}
      <span className={`${WRAP} text-zinc-500`}>{note}</span>
      {reason && <span className="text-amber-300">stale: {reason}</span>}
      {reason && (
        // the accessible name starts with the visible words (label in name), then says which item
        <Link to={to} state={CARRIED_SEARCH} aria-label={`open as saved: stale ${noun} ${name}`}
          className={`inline-flex ${TAP} items-center text-blue-400 hover:underline`}>open as saved</Link>
      )}
      {/* end-aligned, with the destructive ✕ set well apart from "rename" (a single tap deletes, with no confirm step: the
          owner rule), so a thumb aiming at one cannot land on the other */}
      <span className="ml-auto flex items-center gap-1">
        <button type="button" onClick={() => setDraft(name)} aria-label={`Rename saved ${noun} ${name}`}
          className={`${TAP} px-2 text-zinc-400 hover:text-zinc-200`}>rename</button>
        <button type="button" data-testid="saved-delete" onClick={() => prefs[remove](id)} aria-label={`Delete saved ${noun} ${name}`}
          className={`${TAP} ml-5 px-2 text-zinc-500 hover:text-red-400 md:ml-4 md:px-1`}>✕</button>
      </span>
    </li>
  );
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import CategoryEditor from "../components/CategoryEditor";
import FilterBar from "../components/FilterBar";
import TxnTable, { editable, TXN_COLUMN_IDS } from "../components/TxnTable";
import { Card } from "../components/ui";
import { useData } from "../data/DataContext";
import { applyFilters } from "../filters/filters";
import { useFilters } from "../filters/useFilters";
import { useSort } from "../filters/useSort";
import { useSpendingDefaults } from "../filters/useSpendingDefaults";
import { MAX_ANSWERS } from "../lib/answers";
import { countOf } from "../lib/format";
import { regroupSplits } from "../lib/splits";
import { SELECTION_CAP, useRowSelection } from "../lib/useRowSelection";

/** Spending → Transactions: the full-height register under the same FilterBar as Explore (the
 *  two tabs share their filter state through the URL — keepSearch in App.tsx). The register's
 *  sort lives in `?sort=` too (useSort), so a sorted view is a shareable link. Ticking rows opens a
 *  bulk bar: one editor over the selection (categorize, tag, or both), written as one commit. */
export default function Transactions() {
  const { data, answers, indexes } = useData();
  const [filters, update, clear] = useFilters();
  useSpendingDefaults();
  const [sort, setSort] = useSort(TXN_COLUMN_IDS);
  const filtered = useMemo(() => applyFilters(data.transactions, filters), [data.transactions, filters]);

  const selection = useRowSelection();
  const { prune } = selection;
  const [bulk, setBulk] = useState(false);
  // The selection follows what the register DISPLAYS: a row a filter edit hides is no longer selected (and
  // so never answered unseen) - and neither is a part an edit folds under its parent row (every part now passes,
  // so the parent stands in for them and the part is hidden behind its collapsed row). Only answerable rows count.
  const live = useMemo(
    () => new Set(regroupSplits(filtered, indexes.childrenByParent, data.splitParents).filter(editable).map((t) => t.id)),
    [filtered, indexes.childrenByParent, data.splitParents]);
  useEffect(() => { prune(live); }, [live, prune]);
  const selectedRows = useMemo(() => filtered.filter((t) => selection.selected.has(t.id)), [filtered, selection.selected]);
  const count = selection.selected.size;
  // an editor over nothing closes (derived in render), so ticking a row later never resurrects it
  if (bulk && count === 0) setBulk(false);

  // What a save costs the family store: one one-off answer per row, but a row that already has an answer merges
  // into it and takes no new slot. The store keeps MAX_ANSWERS one-off (txn) answers and drops the oldest past
  // that; "Always for a vendor" answers are never dropped and take no slot (lib/answers.ts capAnswers). A save
  // creates at most SELECTION_CAP (< MAX_ANSWERS) new one-off answers, stamped now, so what it drops is stored answers
  // older than them - which can include a row of this selection whose tags-only merge kept its old stamp.
  const taken = useMemo(() => new Set(answers.doc.answers.map((a) => a.key)), [answers.doc.answers]);
  const storedOneOff = useMemo(() => answers.doc.answers.filter((a) => a.scope === "txn").length, [answers.doc.answers]);
  // "Always for a vendor" (chosen in the open editor, which reports it) writes no one-off answer at all
  const [vendorScope, setVendorScope] = useState<string | null>(null);
  const fresh = vendorScope !== null ? 0 : [...selection.selected].filter((id) => !taken.has(`txn:${id}`)).length;
  const drop = Math.max(0, storedOneOff + fresh - MAX_ANSWERS);
  const dropText = drop === 1 ? "the oldest one-off answer" : `the ${drop} oldest one-off answers`;

  // Focus follows the work: the editor and the bar unmount under the keyboard, so the handlers say where it
  // goes and the layout effect (after the next commit, when those targets exist or are gone) puts it there -
  // Cancel back on the button that opened the editor, a save (or clear) onto the register's one tab stop.
  const openBtn = useRef<HTMLButtonElement>(null);
  const register = useRef<HTMLDivElement>(null);
  const focusNext = useRef<"open" | "register" | null>(null);
  useLayoutEffect(() => {
    const want = focusNext.current;
    focusNext.current = null;
    if (want === "open") openBtn.current?.focus();
    else if (want === "register") {
      // the active row; with no rows on screen, the select-all box (the one checkbox left in the tab order)
      (register.current?.querySelector<HTMLElement>('[role="row"][tabindex="0"]')
        ?? register.current?.querySelector<HTMLElement>('input[type="checkbox"]:not([tabindex])'))?.focus();
    }
  });
  const closeBulk = () => { focusNext.current = "register"; setBulk(false); selection.clear(); };
  // What a bulk save says. The editor, the bar and the selection all unmount on a save, so a screen reader heard nothing at
  // all; this live region stays mounted and is filled by the save, then emptied (it would otherwise go stale on screen).
  // The words follow the store's answer: a refusal (the debounced PUT's reply, about a second later, or one still standing
  // from an earlier save) turns "permanent at the next ingest" into "in this browser only", beside the register's
  // alert; a save that then gets through turns it back.
  const [saved, setSaved] = useState<{ n: number; vendor: boolean } | null>(null);
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (savedTimer.current) clearTimeout(savedTimer.current); }, []);
  const announceSaved = (keys: string[]) => {
    setSaved({ n: keys.length, vendor: keys[0]?.startsWith("vendor:") ?? false });
    if (savedTimer.current) clearTimeout(savedTimer.current);
    savedTimer.current = setTimeout(() => setSaved(null), 8000);
  };
  const savedText = saved === null ? null
    : answers.refused ? `Saved ${saved.n} answer${saved.n === 1 ? "" : "s"} in this browser only — the family store is refusing saves (see below)`
    : saved.vendor ? "Saved 1 answer for the vendor — it covers all of its rows, and is permanent at the next ingest"
    : `Saved ${countOf(saved.n, "answer")} — permanent at the next ingest`;
  const cancelBulk = () => { focusNext.current = "open"; setBulk(false); };

  return (
    <div className="space-y-4">
      <FilterBar filters={filters} update={update} clear={clear} filtered={filtered} />
      <Card testId="card-transactions" title="Transactions">
        <p role="status" data-testid="bulk-saved"
          className={savedText === null ? "sr-only" : `mb-2 text-xs ${answers.refused ? "text-amber-400" : "text-emerald-400"}`}>{savedText}</p>
        {count > 0 && (
          <div data-testid="bulk-bar" role="region" aria-label="Selected transactions"
            className="mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-blue-900/60 bg-blue-950/30 p-2 text-xs">
            <span className="font-medium text-blue-200">
              {count} selected{selection.atCap ? ` (max ${SELECTION_CAP})` : ""}
            </span>
            <button type="button" ref={openBtn} onClick={() => setBulk(true)}
              className="min-h-[40px] rounded-md border border-blue-700 px-2.5 text-blue-200 hover:bg-blue-900/40 md:min-h-0 md:py-1">
              Categorize or tag…
            </button>
            <button type="button" onClick={closeBulk} className="min-h-[40px] text-zinc-400 underline md:min-h-0">clear</button>
            <span className="ml-auto text-[10px] text-zinc-500">
              {vendorScope !== null
                ? <>writes 1 answer for “{vendorScope}”, which covers every one of its rows and is never dropped</>
                : <>writes {count} answer{count === 1 ? "" : "s"} (one per row; a tags-only answer takes a slot too)</>}
              {" "}· the family store keeps up to {MAX_ANSWERS} one-off answers, dropping the oldest first ({storedOneOff} stored now)
              {drop > 0 && (
                <strong className="font-semibold text-amber-300">
                  {" "}— this save would drop {dropText}
                </strong>
              )}
              {vendorScope === null && <> · “Always for a vendor” is one answer</>}
            </span>
          </div>
        )}
        {bulk && selectedRows.length > 0 && (
          <div className="mb-2">
            <CategoryEditor txns={selectedRows} onSaved={(keys) => { announceSaved(keys); closeBulk(); }} onCancel={cancelBulk} onVendorScope={setVendorScope} />
          </div>
        )}
        {/* viewport height minus the page chrome above the table; never under 20rem, so a short window keeps a usable
            body (a phone's register has no body of its own: it scrolls with the page) */}
        <div ref={register}>
          <TxnTable txns={filtered} sort={sort} onSortChange={setSort} selection={selection} onClear={clear}
            maxHeight="max(20rem, calc(100vh - 20rem))" />
        </div>
      </Card>
    </div>
  );
}

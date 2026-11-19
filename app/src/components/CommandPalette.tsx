// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Cmd+K palette: jump to any page, saved view or report, account, category, or vendor. No dependency —
// a fixed overlay, one input, and substring matching over in-memory indexes. The input is an ARIA
// combobox over a listbox (focus stays in the input; aria-activedescendant names the active option).
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useData } from "../data/DataContext";
import { useSavedReports } from "../data/useSavedReports";
import { useSavedViews } from "../data/useSavedViews";
import { presetRange } from "../filters/dateRange";
import { type Filters, spendingPath } from "../filters/filters";
import { CARRIED_SEARCH } from "../lib/hubNav";
import { usePointerWords } from "../lib/pointerWords";
import { savedFilterPath, savedReportPath } from "../lib/savedFilters";
import { useFocusTrap } from "../lib/useFocusTrap";

/** `carried`: open with the CARRIED_SEARCH router state, so the destination's query is read as
 *  deliberate (a saved view or report without a date window opens all-time, not on the six-month default) */
interface Item {
  key: string; label: string; hint: string; to: string; carried?: boolean;
  /** other names the item is found by, matched together with its label and never shown (a page's alternative name) */
  aliases?: string[];
}

const MAX_RESULTS = 12;

/** a ⌘K category/vendor jump: the Transactions tab, last six months, plus the patch. Deep links
 *  always carry explicit dates (the tabs' default window only applies to bare URLs); with no data date
 *  (`maxDate` null) there is no six months to name, and the link carries the patch alone. */
export function spendTo(patch: Partial<Filters>, maxDate: string | null): string {
  return spendingPath({ ...presetRange("6m", maxDate), ...patch });
}

type NavEntry = { to: string; label: string; icon: string; aliases?: string[] };

export default function CommandPalette({ nav, open, onOpenChange }: {
  nav: NavEntry[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  // keep the keydown listener stable while still reading the latest open state
  const openRef = useRef(open);
  openRef.current = open;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        onOpenChange(!openRef.current);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onOpenChange]);

  // the dialog is its own component so every open mounts it fresh: empty query, first option
  // active, focus in the input, and focus handed back to whatever opened it when it closes
  return open ? <PaletteDialog nav={nav} onOpenChange={onOpenChange} /> : null;
}

function PaletteDialog({ nav, onOpenChange }: {
  nav: NavEntry[]; onOpenChange: (open: boolean) => void;
}) {
  const { data, indexes } = useData();
  const { valid: savedViews } = useSavedViews();   // a stale view is named on Data, never offered here
  const { valid: savedReports } = useSavedReports();   // ...and so is a stale report
  const navigate = useNavigate();
  const { touch } = usePointerWords();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const panelRef = useRef<HTMLDivElement>(null);
  useFocusTrap(panelRef, () => onOpenChange(false));

  const items = useMemo<Item[]>(() => {
    const needle = q.trim().toLowerCase();
    // `key` is the React key: a saved view's is its id (two views may share a name and a query)
    const pages: Item[] = nav.map((n) => ({
      key: `page:${n.to}`, label: `${n.icon} ${n.label}`, hint: "page", to: n.to, aliases: n.aliases }));
    // an account opens its own page (/accounts/:id); the name is what the family calls it (a last four is part of it)
    const accounts: Item[] = data.accounts.map((a) => ({
      key: `account:${a.id}`, label: a.name, hint: `account · ${a.institution}`, to: `/accounts/${encodeURIComponent(a.id)}` }));
    // the jumps' six months count back from the day the data reaches (never past the ingest day), as every preset does;
    // with no transaction the hint promises no window
    const end = indexes.dataThrough;
    const span = end === null ? "" : " · last 6 mo";
    const cats: Item[] = indexes.categoryList.map((c) => ({
      key: `category:${c}`, label: c, hint: `category${span}`, to: spendTo({ categories: [c] }, end),
    }));
    const vendors: Item[] = indexes.vendorList.map((v) => ({
      key: `vendor:${v}`, label: v, hint: `vendor${span}`, to: spendTo({ vendors: [v] }, end),
    }));
    const views: Item[] = savedViews.map((f) => ({
      key: `view:${f.id}`, label: `★ ${f.name}`, hint: "saved view", to: savedFilterPath(f), carried: true,
    }));
    // a saved report opens the Reports tab exactly as saved: its filters and report keys, with the marker
    const reports: Item[] = savedReports.map((r) => ({
      key: `report:${r.id}`, label: `★ ${r.name}`, hint: "saved report", to: savedReportPath(r), carried: true,
    }));
    if (!needle) return [...views, ...reports, ...pages];
    // the label AND any aliases the item carries
    const match = (xs: Item[]) => xs.filter((x) =>
      x.label.toLowerCase().includes(needle) || (x.aliases ?? []).some((a) => a.toLowerCase().includes(needle)));
    return [...match(views), ...match(reports), ...match(pages), ...match(accounts), ...match(cats), ...match(vendors)]
      .slice(0, MAX_RESULTS);
  }, [q, nav, data.accounts, indexes, savedViews, savedReports]);

  // the active option follows the arrow keys even when the list scrolls
  useEffect(() => {
    document.getElementById(`palette-opt-${sel}`)?.scrollIntoView?.({ block: "nearest" });
  }, [sel]);

  const go = (item: Item) => {
    onOpenChange(false);
    navigate(item.to, item.carried ? { state: CARRIED_SEARCH } : undefined);
  };

  return (
    <div className="fixed inset-0 z-[60] bg-black/60 pt-[10vh] sm:pt-[18vh]" onClick={() => onOpenChange(false)}>
      <div ref={panelRef} role="dialog" aria-modal="true" aria-label="Jump to a page, account, category, or vendor" tabIndex={-1}
        className="mx-auto w-full max-w-lg rounded-xl border border-zinc-700 bg-zinc-900 shadow-2xl outline-none"
        onClick={(e) => e.stopPropagation()}>
        <input value={q} role="combobox" aria-expanded="true" aria-controls="palette-listbox"
          aria-autocomplete="list" aria-label="Search pages, accounts, categories and vendors"
          aria-activedescendant={items[sel] ? `palette-opt-${sel}` : undefined}
          placeholder="Jump to a page, account, category, or vendor…"
          onChange={(e) => { setQ(e.target.value); setSel(0); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, items.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
            else if (e.key === "Enter" && items[sel]) go(items[sel]);
          }}
          className="w-full border-b border-zinc-800 bg-transparent px-4 py-3 text-base text-zinc-100 outline-none placeholder:text-faint sm:text-sm" />
        <ul id="palette-listbox" role="listbox" aria-label="Results" className="max-h-72 overflow-y-auto p-1">
          {items.map((item, i) => (
            <li key={item.key} id={`palette-opt-${i}`} role="option" aria-selected={i === sel}
              onClick={() => go(item)} onMouseEnter={() => setSel(i)}
              className={`flex min-h-[40px] cursor-pointer items-center justify-between rounded-lg px-3 py-2 text-sm sm:min-h-0 ${
                i === sel ? "bg-zinc-800 text-zinc-100" : "text-zinc-400"}`}>
              <span className="truncate">{item.label}</span>
              <span className="ml-2 shrink-0 text-[10px] text-faint">{item.hint}</span>
            </li>
          ))}
        </ul>
        {!items.length && <p role="status" className="px-4 py-2 text-sm text-faint">no matches</p>}
        {/* the keys are a keyboard's: a finger is not told about them (lib/pointerWords.ts) */}
        {!touch && (
          <div data-testid="palette-keys" className="border-t border-zinc-800 px-3 py-1.5 text-[10px] text-faint">
            ↑↓ navigate · ⏎ open · esc close
          </div>
        )}
      </div>
    </div>
  );
}

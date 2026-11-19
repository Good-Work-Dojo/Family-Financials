// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useData } from "../data/DataContext";
import { useSavedViews } from "../data/useSavedViews";
import type { Transaction, TxType } from "../data/types";
import { matchPreset, NO_PRESET_REASON, presetRange, SPENDING_PRESETS } from "../filters/dateRange";
import { filterSummary } from "../filters/filterSummary";
import { countActive, Filters } from "../filters/filters";
import { savedFilterQuery, withoutAutoWindow } from "../filters/useSpendingDefaults";
import { countedRows, ingestDay, isSpend, monthInProgress, totalSpend } from "../lib/agg";
import { isSourceGroup, SOURCE_GROUPS, sourceGroup } from "../lib/categorySource";
import { CARRIED_SEARCH } from "../lib/hubNav";
import { countOf, fmtDate, fmtUSD0 } from "../lib/format";
import { PREVIEW_ID, PREVIEW_STAMP } from "../lib/ids";
import { type SavedFilter, tryWithSaved } from "../lib/prefs";
import { savedFilterPath } from "../lib/savedFilters";
import { purchaseCount } from "../lib/splits";
import { useIsMobile } from "../lib/useMediaQuery";
import DateRangeBar from "./DateRangeBar";
import { MaskedNumberInput } from "./inputs";
import MultiSelect from "./MultiSelect";
import SaveNameBox from "./SaveNameBox";
import { Chip, SegmentToggle } from "./ui";

const TX_TYPES: TxType[] = ["purchase", "income", "investment", "dividend", "fee", "interest", "transfer", "cc-payment"];

// 6M is the window Spending opens on (filters/useSpendingDefaults.ts): the default is a chip, so it lights up on arrival
const PRESETS = SPENDING_PRESETS;

/** what the count line's note counts: the transactions dated into a month that has not begun, which neither the spend
 *  nor a report counts yet (lib/agg.ts countedRows) */
const NOT_BEGUN = "dated into a month not begun, not in the spend or a report yet";

/** The filter bar of the three Spending tabs. Below `md` it collapses to one line - the window and how many other filters are
 *  on ("6M · 2 filters ▾"), a real disclosure button (`aria-expanded`) - because the open bar is ~40% of a phone's first
 *  screen; the count line under it stays, and every control is behind the button, so nothing is lost. Collapsed is the phone's
 *  default and the bar is plain (not sticky) while it is open; from `md` up it is the full bar, always, as before. The filters
 *  themselves live in the URL (useFilters), so opening, closing and re-collapsing change nothing there. */
export default function FilterBar({ filters, update, clear, filtered, compact, canSaveView = true, rowsPath }: {
  filters: Filters;
  update: (p: Partial<Filters>) => void;
  clear: () => void;
  filtered: Transaction[];
  compact?: boolean;
  /** offer "★ Save view" while a filter is active (default). The Reports tab turns it off: a view is the filters alone
   *  and opens Transactions, which that tab's own "★ Save report" - the filters AND the report - does not. */
  canSaveView?: boolean;
  /** Explore only: the router path of these same rows on the Transactions tab (spendingPath of the
   *  current filters). Followed with the CARRIED_SEARCH marker, so a cleared or All-time state is not
   *  mistaken for a fresh visit and the register shows exactly the N rows counted here. */
  rowsPath?: string;
}) {
  const { data, indexes, missing, prefs } = useData();
  const saved = useSavedViews();
  // the day the data reaches (never past the ingest day: a row dated ahead moves no window), which the presets count
  // back from; null with no transaction, when the chips are disabled
  const maxDate = indexes.dataThrough;
  const isMobile = useIsMobile();
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const collapsed = isMobile && !open;
  const accountNames = useMemo(() => new Map(data.accounts.map((a) => [a.id, a.name])), [data.accounts]);

  const vendorOptions = useMemo(
    () => indexes.vendorList.map((v) => ({ value: v })), [indexes.vendorList]);
  // owners that actually appear on transactions, in assumptions.owners order
  const ownerOptions = useMemo(() => {
    const seen = new Set<string>(data.transactions.map((t) => t.owner));
    return data.assumptions.owners.filter((o) => seen.has(o)).map((v) => ({ value: v }));
  }, [data.transactions, data.assumptions.owners]);

  // every tag on any row (the bulk editor and the row editor add them) plus any the URL names, so a link that filters by a tag
  // nobody has used yet still shows its selection (and a way to clear it)
  const tagOptions = useMemo(
    () => [...new Set([...indexes.tagList, ...filters.tags])].sort().map((v) => ({ value: v })), [indexes.tagList, filters.tags]);

  // where the rows' categories came from, by the facet's groups: each group the data holds, with its count of
  // transactions (a split purchase once) among the rows the facet can show (the transfers only while they are shown),
  // plus any group the URL names, so a link's selection always shows and can be cleared
  const sourceOptions = useMemo(() => {
    const rows = filters.includeInternal ? data.transactions
      : data.transactions.filter((t) => t.type !== "transfer" && t.type !== "cc-payment");
    return SOURCE_GROUPS.flatMap((g) => {
      const n = purchaseCount(rows.filter((t) => sourceGroup(t.category_source) === g.id));
      return n > 0 || filters.sources.includes(g.id) ? [{ value: g.id, label: `${g.label} (${n.toLocaleString("en-US")})` }] : [];
    });
  }, [data.transactions, filters.includeInternal, filters.sources]);
  const sourceLabel = (id: string) => SOURCE_GROUPS.find((g) => g.id === id)?.label ?? id;

  const n = countActive(filters);
  // transactions of every type the filters pass, a split purchase once however many of its parts pass (purchaseCount,
  // and data.purchases for M: the vendor view), so "N of M transactions" and the link agree - and the sidebar's total is
  // the same M under the same word
  const shown = purchaseCount(filtered);
  // the spend is the charts' and reports' own: the rows up to the month in progress (lib/agg.ts countedRows). A row dated
  // into a month that has not begun is in the selection (counted above, listed in the register) but in no total yet,
  // and the line says how many transactions those are, in the words of what it counts: a row the register marks "dated
  // ahead" (any row after the ingest day) that falls in the month in progress is in the spend, and is not one of them
  const counted = useMemo(() => countedRows(filtered, monthInProgress(data)), [filtered, data]);
  const notBegun = shown - purchaseCount(counted);
  // the spend counts the day of the last ingest too, whose rows are still coming in (Home's "this month so far" stops
  // the day before it, at the last complete day): when a spend row is dated that day, the line says the spend reaches it
  const ingest = ingestDay(data.manifest.generated_at);
  const reachesIngestDay = ingest !== null && counted.some((t) => isSpend(t) && t.date === ingest);
  // "★ Save view" -> a name box -> saved: the Transactions tab's query under a name (Data manages them). The preview item
  // carries an id and a stamp as long as real ones (PREVIEW_ID, PREVIEW_STAMP - no clock read per keystroke), or it
  // would pass entries the real save refuses. A window that is a preset (the automatic six months too) is saved as `win=`, so the
  // view keeps rolling with the data instead of freezing the dates of the day it was saved (savedFilterQuery).
  const query = savedFilterQuery(filters, maxDate);
  // a fresh visit has nothing but the page's own window on: nothing the user chose, so nothing worth naming a view
  const chosen = countActive(withoutAutoWindow(filters, maxDate)) > 0;
  const viewRefusal = (name: string) => {
    const verdict = tryWithSaved(prefs.doc, "saved_filters", { id: PREVIEW_ID, name: name.trim() || "x", query }, PREVIEW_STAMP);
    return typeof verdict === "string" ? verdict : null;
  };

  return (
    <div data-testid="filterbar"
      className={`${isMobile && open ? "" : "sticky top-12 z-20 md:top-0 "}-mx-1 rounded-xl border border-zinc-800 bg-zinc-950/95 p-3 backdrop-blur`}>
      {isMobile && (
        <button type="button" data-testid="filterbar-toggle" aria-expanded={open} aria-controls={panelId}
          onClick={() => setOpen(!open)}
          className="flex min-h-[40px] w-full items-center justify-between gap-2 text-left text-sm text-zinc-200">
          <span><span className="sr-only">Filters: </span>{filterSummary(filters, maxDate, accountNames)}</span>
          <span aria-hidden="true" className="text-zinc-500">{open ? "▴" : "▾"}</span>
        </button>
      )}
      <div id={panelId} hidden={collapsed} data-testid="filterbar-controls"
        className={`${collapsed ? "" : "flex flex-wrap items-center gap-2"}${isMobile ? " mt-2" : ""}`}>
        <DateRangeBar from={filters.from} to={filters.to} presets={PRESETS}
          activePreset={matchPreset({ from: filters.from, to: filters.to }, maxDate, PRESETS)}
          unavailable={maxDate === null ? NO_PRESET_REASON : undefined}
          // an open end is the oldest or the newest day among the rows it lets in (a row dated ahead included), so closing
          // it at the date it shows changes nothing
          openEnds={{ first: indexes.dataRange?.first ?? null, last: indexes.dataRange?.last ?? null }}
          onPreset={(p) => {
            const r = presetRange(p, maxDate);
            if (r) update({ from: r.from, to: r.to });
          }}
          onChange={(r) => update({ from: r.from, to: r.to })} />

        {!missing.has("accounts.json") && (
          <MultiSelect label="Account" selected={filters.accounts}
            options={data.accounts.map((a) => ({ value: a.id, label: a.name }))}
            onChange={(v) => update({ accounts: v })} />
        )}
        <MultiSelect label="Owner" selected={filters.owners}
          options={ownerOptions} onChange={(v) => update({ owners: v })} />
        <MultiSelect label="Category" selected={filters.categories}
          options={indexes.categoryList.map((v) => ({ value: v }))}
          onChange={(v) => update({ categories: v })} />
        <MultiSelect label="Vendor" selected={filters.vendors}
          options={vendorOptions} onChange={(v) => update({ vendors: v })} />
        {tagOptions.length > 0 && (
          <MultiSelect label="Tags" selected={filters.tags}
            options={tagOptions} onChange={(v) => update({ tags: v })} />
        )}
        {sourceOptions.length > 0 && (
          <MultiSelect label="Filed by" selected={filters.sources} options={sourceOptions}
            onChange={(v) => update({ sources: v.filter(isSourceGroup) })} />
        )}
        {!compact && (
          <MultiSelect label="Type" selected={filters.types}
            options={TX_TYPES.map((v) => ({ value: v }))}
            onChange={(v) => update({ types: v as TxType[] })} />
        )}
        {!compact && (
          <>
            <MaskedNumberInput type="number" placeholder="min $" aria-label="Minimum amount" value={filters.amountMin ?? ""}
              onChange={(e) => update({ amountMin: e.target.value ? Number(e.target.value) : undefined })}
              className="w-20 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-base sm:text-xs" />
            <MaskedNumberInput type="number" placeholder="max $" aria-label="Maximum amount" value={filters.amountMax ?? ""}
              onChange={(e) => update({ amountMax: e.target.value ? Number(e.target.value) : undefined })}
              className="w-20 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-base sm:text-xs" />
            <SegmentToggle value={filters.direction}
              options={[{ value: "all", label: "All" }, { value: "expense", label: "Out" }, { value: "income", label: "In" }]}
              onChange={(direction) => update({ direction })} />
          </>
        )}
        <input value={filters.q ?? ""} placeholder="search description / vendor…"
          onChange={(e) => update({ q: e.target.value || undefined })}
          className="min-w-full grow rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-base sm:min-w-44 sm:text-xs" />
        <label className="flex items-center gap-1 text-xs text-zinc-400">
          <input type="checkbox" checked={filters.needsReviewOnly}
            onChange={(e) => update({ needsReviewOnly: e.target.checked })}
            className="accent-amber-500" />
          review
        </label>
        {!compact && (
          <label className="flex items-center gap-1 text-xs text-zinc-400"
            title="Show transfers between your own accounts and credit-card payments (always excluded from totals)">
            <input type="checkbox" checked={filters.includeInternal}
              onChange={(e) => update({ includeInternal: e.target.checked })}
              className="accent-zinc-500" />
            transfers
          </label>
        )}
      </div>
      {/* `relative`: the saved-views menu anchors to this row (its width and left edge), not to its own
          button, so it can never be pushed past the viewport edge on a phone */}
      <div className="relative mt-2 flex flex-wrap items-center gap-2 text-xs text-zinc-400">
        {/* Transactions of every type the filters pass, not rows: a split purchase counts once here and in M, however
            many parts pass. The register shows a whole split as one row and a partly matched one as its parts; the CSV
            button counts the parts as rows and names these same N transactions beside them when the two differ ("K rows
            (N transactions)"). */}
        {/* a spend over no spending row is no $0: the line says there is none */}
        <span className="tabular-nums">
          {countOf(shown, "of", "of")} {countOf(data.purchases.length, "transaction")}
          {counted.some(isSpend) ? <> · spend {fmtUSD0(totalSpend(counted))}</> : " · no spending"}
          {reachesIngestDay && <span data-testid="filterbar-ingest-day"> through {fmtDate(ingest!)}, a day still coming in</span>}
          {notBegun > 0 && <span data-testid="filterbar-ahead"> · {countOf(notBegun, NOT_BEGUN, NOT_BEGUN)}</span>}
        </span>
        {rowsPath && (
          <Link to={rowsPath} state={CARRIED_SEARCH}
            className="inline-flex min-h-[40px] items-center text-blue-400 hover:underline md:min-h-0">
            view {shown === 1 ? "this transaction" : `these ${countOf(shown, "transaction")}`} →
          </Link>
        )}
        {/* the rest of the row is behind the phone's disclosure; `contents` keeps its items in this flex row */}
        <div hidden={collapsed} className={collapsed ? undefined : "contents"}>
          {filters.categories.map((c) => (
            <Chip key={c} onRemove={() => update({ categories: filters.categories.filter((x) => x !== c) })}>cat: {c}</Chip>
          ))}
          {filters.vendors.map((v) => (
            <Chip key={v} onRemove={() => update({ vendors: filters.vendors.filter((x) => x !== v) })}>vendor: {v}</Chip>
          ))}
          {filters.accounts.map((a) => (
            <Chip key={a} onRemove={() => update({ accounts: filters.accounts.filter((x) => x !== a) })}>acct: {accountNames.get(a) ?? a}</Chip>
          ))}
          {filters.tags.map((t) => (
            <Chip key={t} onRemove={() => update({ tags: filters.tags.filter((x) => x !== t) })}>tag: {t}</Chip>
          ))}
          {filters.sources.map((s) => (
            <Chip key={s} onRemove={() => update({ sources: filters.sources.filter((x) => x !== s) })}
              removeLabel={`Remove the filter filed by: ${sourceLabel(s)}`}>filed by: {sourceLabel(s)}</Chip>
          ))}
          {n > 0 && (
            <button onClick={clear} className="text-red-400 underline">Clear all</button>
          )}
          {canSaveView && chosen && (
            <SaveNameBox kind="view" buttonLabel="★ Save view" buttonTestId="filterbar-save-view" nameLabel="View name"
              placeholder="name this view" refusalFor={viewRefusal} onSave={(name) => prefs.saveFilter(name, query)} />
          )}
          {saved.valid.length + saved.stale.length > 0 && <SavedViewsMenu views={saved.valid} staleCount={saved.stale.length} />}
        </div>
      </div>
    </div>
  );
}

/** The family's saved views as a dropdown of links, anchored to the filter bar's second row (the
 *  caller's `relative`): width follows the row, so on a phone it never leaves the screen and its left edge is the row's. From
 *  `md` up the row is wide and the button sits somewhere along it, so the menu opens under the BUTTON (its left edge measured
 *  after it opens), pulled back left only as far as it needs to stay inside the row - a menu 500px from the button that
 *  opened it read as detached.
 *  Each link opens the Transactions tab with the view's query and the CARRIED_SEARCH marker, so a view
 *  saved without a date window opens all-time rather than getting the six-month default. A stale view
 *  (its category is gone) is not offered; the footer counts them and points at Data, where they are
 *  named. A disclosure, not an ARIA menu: aria-expanded on the button, plain links inside. It closes on
 *  a pick, a click or focus anywhere outside, and Escape from inside (focus returns to the button). */
function SavedViewsMenu({ views, staleCount }: { views: SavedFilter[]; staleCount: number }) {
  const [open, setOpen] = useState(false);
  const wide = !useIsMobile();
  const [left, setLeft] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  useEffect(() => {
    if (!open) return;
    // focusin, not blur: a click on a link in Safari never focuses it, so a blur-based close would unmount
    // the list between mousedown and click
    const away = (e: Event) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("focusin", away);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("focusin", away); };
  }, [open]);
  // the button's offset in the row (the menu's containing block), clamped so the menu's right edge stays inside it
  useLayoutEffect(() => {
    const row = list.current?.offsetParent as HTMLElement | null;
    if (!open || !wide || !row || !button.current || !list.current) return;
    setLeft(Math.max(0, Math.min(button.current.offsetLeft, row.clientWidth - list.current.offsetWidth)));
  }, [open, wide]);
  const item = "flex min-h-[40px] items-center rounded px-2 text-xs hover:bg-zinc-800 md:min-h-0 md:py-1";
  return (
    <div ref={ref} onKeyDown={(e) => { if (e.key === "Escape" && open) { setOpen(false); button.current?.focus(); } }}>
      <button ref={button} type="button" data-testid="filterbar-views" aria-expanded={open}
        aria-controls="filterbar-views-list" onClick={() => setOpen(!open)}
        className="inline-flex min-h-[40px] items-center text-blue-400 hover:underline md:min-h-0">
        ★ Views · {views.length} ▾
      </button>
      {open && (
        <ul ref={list} id="filterbar-views-list" aria-label="Saved views" tabIndex={-1} style={wide ? { left } : undefined}
          className="absolute left-0 top-full z-30 mt-1 max-h-72 w-[min(18rem,100%)] overflow-auto rounded-lg border border-zinc-700 bg-zinc-900 p-1.5 shadow-xl outline-none">
          {views.map((f) => (
            <li key={f.id}>
              <Link to={savedFilterPath(f)} state={CARRIED_SEARCH} onClick={() => setOpen(false)}
                className={`${item} text-zinc-300 [overflow-wrap:anywhere]`}>{f.name}</Link>
            </li>
          ))}
          {views.length === 0 && <li className="px-2 py-1 text-zinc-500">no saved view fits this data</li>}
          {staleCount > 0 && (
            <li className="mt-1 border-t border-zinc-800 pt-1">
              <Link to="/data" onClick={() => setOpen(false)} className={`${item} text-amber-300`}>
                {staleCount} stale view{staleCount === 1 ? "" : "s"} — see Data
              </Link>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

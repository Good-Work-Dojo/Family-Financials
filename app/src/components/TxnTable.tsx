// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import {
  createColumnHelper, flexRender, getCoreRowModel, getSortedRowModel,
  SortingState, type Updater, useReactTable,
} from "@tanstack/react-table";
import { defaultRangeExtractor, useVirtualizer } from "@tanstack/react-virtual";
import {
  type CSSProperties, type KeyboardEvent, useCallback, useId, useLayoutEffect, useMemo, useRef, useState,
} from "react";
import { useData } from "../data/DataContext";
import type { Transaction } from "../data/types";
import { DEFAULT_SORT } from "../filters/useSort";
import { ingestDay } from "../lib/agg";
import { chipInk, sourceChip } from "../lib/categorySource";
import { downloadCsv, toCsv } from "../lib/csv";
import { countOf, fmtDate, fmtUSD } from "../lib/format";
import { useExpanded } from "../lib/useExpanded";
import { usePointerWords } from "../lib/pointerWords";
import { maskMoneyText } from "../lib/privacy";
import { purchaseCount, regroupSplits } from "../lib/splits";
import { usePrivacy } from "../lib/usePrivacy";
import { useIsMobile, useMediaQuery } from "../lib/useMediaQuery";
import { type RowSelection, SELECTION_CAP } from "../lib/useRowSelection";
import AccountLink from "./AccountLink";
import CategoryEditor, { EDITABLE_TYPES } from "./CategoryEditor";
import { CsvNote } from "./ChartDataTable";
import { Code } from "./NoData";
import SourceChip from "./SourceChip";
import { AnswersLocalOnlyNotice, Badge, ReviewDot, StatusDot } from "./ui";

const col = createColumnHelper<Transaction>();

const categoryLabel = (t: Transaction) => `${t.category ?? ""}${t.subcategory ? " / " + t.subcategory : ""}`;

/** a row the category editor can answer: an answerable type that is not a split parent (a parent is
 *  display-only — its parts carry the spend; an answer on it would change no total). The one rule for
 *  the checkbox, the `x` key, the header box's ids, the Edit button and (Transactions) the selection's
 *  prune. */
export const editable = (t: Transaction) => EDITABLE_TYPES.has(t.type) && !t.is_parent;

/** the split provenance said in words (the expanded row's sentences about a parent and its parts): the
 *  chip's own ink, so the words and the chip share a hue - and the light theme's remap - by construction */
const SPLIT_INK = chipInk(sourceChip("split")!);

function Amount({ value }: { value: number }) {
  return (
    <span className={`tabular-nums ${value < 0 ? "text-red-300" : "text-emerald-300"}`}>{fmtUSD(value)}</span>
  );
}

/** a row's tags as small chips (a screen reader hears "tag kids"); most rows have none, so most rows draw nothing */
function TagChips({ tags }: { tags: readonly string[] }) {
  if (tags.length === 0) return null;
  return (
    <>
      {tags.map((tag) => (
        <span key={tag} className="max-w-24 shrink-0 truncate rounded-full bg-zinc-800 px-1.5 text-[10px] font-normal leading-4 text-zinc-300">
          <span className="sr-only">tag </span>{tag}
        </span>
      ))}
    </>
  );
}

/** an account's name in the grid register's Account column: the link to its page (R4-23) in the quiet look - secondary text at rest,
 *  blue and underlined on hover or focus - so a column of 24 accounts does not read as 24 calls to action. The register is one tab
 *  stop and a row opens on a click, so this per-row duplicate is for a pointer (tabIndex -1) and swallows its click rather than
 *  also opening the row behind it; the keyboard's and the phone's way to the page is the "Account page →" link in the open row. */
function AccountCell({ t }: { t: Transaction }) {
  return (
    <AccountLink id={t.account} appearance="quiet" tabIndex={-1} onClick={(e) => e.stopPropagation()}>{t.account_name}</AccountLink>
  );
}

/** what the "dated ahead" chip says on hover, to a screen reader and in the open row: the bank dated the row after the
 *  last ingest (`ingest`, the ingest day), so it is no day of data yet and counts in its month's figures from that month on */
const aheadTitle = (ingest: string) =>
  `The bank dated this row after the last ingest (${fmtDate(ingest)}): it is listed here, and a month's figures count it once that month begins`;

/** the mark beside the date of a row dated after the ingest day: a text chip (not colour alone) whose title says why */
function DatedAhead({ ingest }: { ingest: string }) {
  return <Badge cls="shrink-0 bg-amber-950 text-amber-300" title={aheadTitle(ingest)} focusable={false}>dated ahead</Badge>;
}

/** the review column's mark: an answered-in-app dot (permanent at the next refresh) or the
 *  needs-review dot */
function ReviewMark({ t, pending }: { t: Transaction; pending: boolean }) {
  // answered in-app; the next ingest makes it permanent
  return pending
    ? <StatusDot title="Answer saved — permanent at the next ingest" cls="bg-emerald-500/80" />
    : <ReviewDot on={t.needs_review} />;
}

/** the selection column's header: one box over every answerable row shown (in display order), up to the cap.
 *  Checked = every one of them is selected, or the selection is full (it cannot grow past SELECTION_CAP, so a
 *  capped list could otherwise never read "all" and never offer to clear); mixed = some are; a click on a
 *  checked box clears, on any other state selects. A normal tab stop like the sort buttons beside it: the
 *  rows' own boxes are not (see RowCheck), so this is the keyboard's way to select several. */
function SelectAll({ ids, selection }: { ids: string[]; selection: RowSelection }) {
  const picked = ids.reduce((n, id) => n + (selection.selected.has(id) ? 1 : 0), 0);
  const full = ids.length > 0 && (picked === ids.length || selection.atCap);
  return (
    <input type="checkbox" disabled={ids.length === 0} checked={full}
      aria-label={ids.length > SELECTION_CAP ? `Select the first ${SELECTION_CAP} rows shown` : "Select every row shown"}
      ref={(el) => { if (el) el.indeterminate = picked > 0 && !full; }}
      onChange={() => (full ? selection.clear() : selection.selectMany(ids))}
      className="h-4 w-4 accent-blue-500" />
  );
}

/** a row's selection box: only for a row the category editor can answer (`editable`). Named by vendor, date
 *  and amount (through fmtUSD, so a masked amount reads masked) - one vendor repeats down a register. Not a tab stop (the
 *  register has ONE: the active row, which toggles on `x`); the label is the tap target, and swallows its own
 *  click so the tap never opens the row behind it. At the cap an unselected box says it is full instead of
 *  silently doing nothing. */
function RowCheck({ t, selection, tap }: { t: Transaction; selection: RowSelection; tap?: boolean }) {
  if (!editable(t)) return null;
  const on = selection.selected.has(t.id);
  const full = selection.atCap && !on;
  return (
    <label onClick={(e) => e.stopPropagation()}
      className={`flex items-center justify-center ${tap ? "min-h-11 min-w-11" : ""}`}>
      <input type="checkbox" tabIndex={-1} checked={on} onChange={() => selection.toggle(t.id)}
        aria-label={`Select ${t.vendor ?? maskMoneyText(t.description)}, ${t.date}, ${fmtUSD(t.amount)}`}
        aria-disabled={full || undefined} title={full ? `The selection is full (${SELECTION_CAP} rows)` : undefined}
        className="h-4 w-4 accent-blue-500" />
    </label>
  );
}

/** the selection column's id: its header and cells are drawn by TxnTable itself (see SelectAll / RowCheck),
 *  not by this column def - the selection changes on every tick, and a renderer closed over it would be a new
 *  component each time, remounting (and un-focusing) every checkbox */
const SELECT = "select";
/** the running-balance column's id (a display column: TxnTable draws its cells from `balanceByRow`) */
const BALANCE = "balance";

/** `ingest` is the ingest day (null when the stamp is unreadable: no row is marked). The Date column sorts by the date
 *  shown, under the default sort too: a row dated after the ingest day leads a newest-first register, marked. */
function makeColumns(pendingIds: Set<string>, selectable: boolean, partsOf: (t: Transaction) => number,
                     ingest: string | null) {
  return [
    ...(selectable ? [col.display({ id: SELECT })] : []),
    col.accessor("date", { header: "Date", sortDescFirst: true,
      // a row dated ahead wraps its chip under the date: the column has no room beside it
      cell: (c) => (ingest !== null && c.getValue() > ingest
        ? <span className="flex flex-wrap items-center gap-x-1 gap-y-0.5"><span>{c.getValue()}</span><DatedAhead ingest={ingest} /></span>
        : c.getValue()) }),
    col.accessor("account_name", { header: "Account",
      cell: (c) => <span className="truncate text-zinc-400"><AccountCell t={c.row.original} /></span> }),
    col.accessor("owner", { header: "Owner" }),
    col.accessor("vendor", { header: "Vendor",
      cell: (c) => (
        <span className="flex min-w-0 items-center gap-1">
          <span className="truncate font-medium">
            {/* a part shown on its own (its siblings did not pass the filter) says whose purchase it is */}
            {c.row.original.parent_id !== null && <span className="font-normal text-zinc-500">split of </span>}
            {c.getValue()}
          </span>
          <TagChips tags={c.row.original.tags} />
        </span>) }),
    // The column's VALUE (what it sorts by) is the row's display category, a split parent's included: its own category is
    // display-only (its parts carry the spend), but it still files under it, not in one "split · N parts" block. The cell
    // draws what the parent IS instead.
    col.accessor((t) => categoryLabel(t), {
      id: "category", header: "Category",
      cell: (c) => (
        <span className="flex min-w-0 items-center gap-1 text-zinc-400">
          <span className="truncate">{c.row.original.is_parent ? `split · ${partsOf(c.row.original)} parts` : c.getValue()}</span>
          {/* the row itself is the tab stop and carries the tooltip text; no nested focusable */}
          <SourceChip source={c.row.original.is_parent ? "split" : c.row.original.category_source} focusable={false} />
        </span>) }),
    col.accessor("type", { header: "Type",
      cell: (c) => <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px]">{c.getValue()}</span> }),
    col.accessor("amount", { header: "Amount",
      cell: (c) => <Amount value={c.getValue()} /> }),
    // an account page's end-of-day balance: not sortable, and its cells are drawn by TxnTable itself (see
    // `balanceByRow`) - which row of a day carries the number depends on the displayed order. Hidden unless
    // a map is given.
    col.display({ id: BALANCE, header: "Balance", enableSorting: false }),
    col.accessor("needs_review", { header: "",
      cell: (c) => <ReviewMark t={c.row.original} pending={pendingIds.has(c.row.original.id)} /> }),
  ];
}

/** the register's sortable columns, in header order: the ids `?sort=` may name (Transactions passes
 *  this module constant to useSort, which wants a stable identity) */
export const TXN_COLUMN_IDS: readonly string[] = ["date", "account_name", "owner", "vendor", "category", "type", "amount", "needs_review"];

type Layout = "stacked" | "compact" | "full";
/** The register's own cuts, an exception to the md phone cut (app/AGENTS.md). The content area is the
 *  viewport minus the 208px sidebar, the 40px page and 32px card padding (and a scrollbar): from lg
 *  (1024px) it holds the compact grid (690px, 722px with the selection column), from xl (1280px) the full one
 *  (930px, 966px with it), and below lg even the compact grid would pan - so there it stacks like a phone. */
export function useRegisterLayout(): Layout {
  const stacked = useMediaQuery("(max-width: 1023px)");
  const compact = useMediaQuery("(max-width: 1279px)");
  return stacked ? "stacked" : compact ? "compact" : "full";
}
// px per column - the ONE source of widths (the column defs carry no `size`): a new column (a selection
// checkbox, a running balance) gets its width here, an override in COMPACT if the compact grid needs a
// narrower one, and its visibility in `columnVisibility`.
const WIDTHS: Record<string, number> = {
  select: 28, date: 90, account_name: 170, owner: 60, vendor: 180, category: 170, type: 80, amount: 100, balance: 110, needs_review: 24,
};
/** the compact grid's narrower columns (Date's digits need ~66px, so 80 is room to spare; the selection box 24);
 *  Owner and Type leave it altogether (still in the expanded row) */
const COMPACT: Partial<Record<string, number>> = { select: 24, date: 80, account_name: 120, vendor: 170, category: 160, amount: 96 };
const COMPACT_HIDDEN = ["owner", "type"];
/** the one flexible track of a grid: in the compact grid Account takes the spare width above its COMPACT
 *  minimum, so 1100-1279px shows the digits that tell two cards apart instead of an ellipsis at 120px.
 *  Without an Account column (an account page) Vendor flexes instead - in the compact AND the full grid - so
 *  the columns fill the card rather than leaving a gap after the last one. The full grid with Account has
 *  no flexible track (fixed px, as it always had). */
function flexColumn(ids: string[], layout: Layout): string | null {
  if (ids.includes("account_name")) return layout === "compact" ? "account_name" : null;
  return "vendor";
}
/** one inline grid for header and rows (a Tailwind arbitrary class built at runtime is never generated).
 *  `minWidth` is the floor the tracks need (a flexible track counts at its minimum). */
function gridStyle(ids: string[], layout: Layout): { style: CSSProperties; minWidth: number } {
  const w = ids.map((id) => (layout === "compact" ? COMPACT[id] : undefined) ?? WIDTHS[id]);
  const flex = flexColumn(ids, layout);
  const tracks = w.map((x, i) => (ids[i] === flex ? `minmax(${x}px,1fr)` : `${x}px`));
  return {
    style: { display: "grid", gridTemplateColumns: tracks.join(" "), columnGap: 8 },
    minWidth: w.reduce((a, b) => a + b, 0) + 8 * (w.length - 1),
  };
}
/** estimated row heights: a grid row is one ~29px line, a stacked row two lines (also sizes a PageUp/PageDown step) */
const ROW_H = { grid: 29, stacked: 47, stackedSelectable: 56 };       // a 44px checkbox target + the row's padding
/** the rows a phone's register shows at first and adds per "Show more" */
export const PHONE_PAGE = 50;

/** the stacked list's sort <select>: the columns worth sorting a stacked list by (value = column id) */
const STACKED_SORTS: { id: string; label: string }[] = [
  { id: "date", label: "Date" }, { id: "amount", label: "Amount" }, { id: "vendor", label: "Vendor" },
  { id: "category", label: "Category" }, { id: "account_name", label: "Account" },
];

/** columns whose visible header is empty still need a name for their sort button */
const HEADER_NAME: Record<string, string> = { needs_review: "Needs review" };

/** the visible name of a sortable column (for the stacked select when it is sorted by a column it does not list,
 *  and for the compact grid when it is sorted by a column it folds away) */
const sortLabel = (id: string) => HEADER_NAME[id] ?? id.replace(/_name$/, "").replace(/^./, (c) => c.toUpperCase());

/** The sort is controlled (Transactions keeps it in `?sort=` via useSort) only when BOTH halves are given -
 *  the type refuses one without the other (a lone `sort` would freeze the register); with neither, the
 *  register keeps its own. */
type SortProps =
  | { sort?: undefined; onSortChange?: undefined }
  | { sort: SortingState; onSortChange: (s: SortingState) => void };

/** `maxHeight` bounds the body scroller (a px number or any CSS length): 480px suits a table
 *  inside a card, the Transactions tab passes a viewport-relative height to fill the page.
 *
 *  Below md (a phone) there is no body scroller: the list scrolls with the page, says how many
 *  transactions it holds (counted as the filter bar counts them, a split purchase once) and pages
 *  instead of virtualising - PHONE_PAGE rows, then a "Show N more" button - and its lines wrap
 *  instead of cutting a name short.
 *
 *  Three layouts (`useRegisterLayout`): a fixed-width grid from xl (all columns), a compact grid
 *  from lg to xl (Owner and Type folded into the expanded row), and below lg a stacked list -
 *  vendor and amount on the first line, date / category / account on the second - so nothing needs
 *  a sideways pan; there the header buttons give way to a Sort-by select. The grid layouts pan
 *  inside one scroller around header and body only when the viewport is narrower than their
 *  floor. ARIA table semantics hold in all three: header cells are columnheaders carrying
 *  aria-sort, body rows are rows of cells.
 *
 *  The body is ONE tab stop (roving tabindex): the active row is the only `tabIndex` 0, and
 *  ArrowUp/Down/Home/End move it. A virtualized list may not have the next row rendered, so the
 *  active row is always kept in the rendered window and the virtualizer scrolls to the target
 *  before it is focused. The expanded row repeats the category chip's tooltip in words, since a
 *  finger never sees a title.
 *
 *  Split purchases: the rows handed in are the filtered PARTS (the category view); the table
 *  regroups them (lib/splits regroupSplits) - a parent stands in for its parts when every part passed the
 *  filter, reading "split · N parts" and expanding to the parts and the rule; a lone part reads "split of
 *  <vendor>". A parent has no checkbox, no Edit and no `x` (it is in no sum); the CSV and the Export count
 *  read the parts, and the button names the transactions they are beside them when the two counts differ ("3 rows (2
 *  transactions)": a split purchase is one transaction), the unit the filter bar counts in.
 *
 *  Tags ride the Vendor cell (and the stacked list's second line) as small chips, and the Account name links its page. A body
 *  scroller given a px `maxHeight` is cut to a whole number of rows (a half-cut row at the foot reads as a glitch). With no
 *  rows the body is one empty-state row (and Export CSV is disabled, a header-only file being a lie about a download).
 *
 *  With a `selection` the register gains a first column of checkboxes over the rows the category
 *  editor can answer (`editable`): a select-all in the header (on the toolbar when stacked, which has
 *  no header row), a box per row, and `x` on the focused row. The boxes are not tab stops; the
 *  rows say `aria-selected`. The bulk bar and editor live in the caller (sections/Transactions).
 *
 *  With a `runningBalance` map (an account page; date -> end-of-day balance) the grids gain a plain,
 *  unsortable Balance column before the review dot and a stacked row says `bal $x` on its second line. A
 *  day's number sits on the row of that day's run nearest the later days and only while sorted by date
 *  (the toolbar says so otherwise). With `hideAccount` the Account column (the same on every row of that
 *  page) is dropped so the extra column fits - the compact grid's floor is then 680px (729px of room at
 *  1024) and the full grid's 870px (985px at 1280) - and Vendor becomes the flexible track. */
export default function TxnTable({ txns, maxHeight = 480, sort, onSortChange, selection, runningBalance, hideAccount, onClear }:
  { txns: Transaction[]; maxHeight?: number | string;
    /** the filters behind `txns` can be cleared (Transactions): an empty register then offers "Clear all filters" */
    onClear?: () => void;
    /** a selection column (and, stacked, a checkbox target) over the rows the editor can answer */
    selection?: RowSelection;
    /** date -> the END-OF-DAY balance (lib/runningBalance): adds a Balance column (stacked: `bal` on the
     *  second line). The register puts each day's number on the row of that day's run nearest the later
     *  days, and only while it is sorted by date (see `balanceByRow`). */
    runningBalance?: Map<string, number>;
    /** every row is one account's (an account page): the Account column is constant, so it gives its
     *  room to the Balance column and the grids still fit their content area without a pan */
    hideAccount?: boolean } & SortProps) {
  const { overlay, answers, data: { splitParents, manifest }, indexes: { childrenByParent } } = useData();
  const [privacy] = usePrivacy();
  const layout = useRegisterLayout();
  const stacked = layout === "stacked";
  // a phone (below md, inside the stacked layout): the list scrolls with the page and pages instead of virtualising
  const paged = useIsMobile();
  const [pageEnd, setPageEnd] = useState(PHONE_PAGE);
  // the hint's words follow the pointer, not the layout: "tap" for a finger, which also has no keys to be told about
  const words = usePointerWords();
  const keys = !words.touch;
  const sortSelectId = useId();
  const [internalSort, setInternalSort] = useState<SortingState>(DEFAULT_SORT);
  // the type already refuses one half; a JS caller giving one still gets the internal sort, not a frozen register
  const controlled = sort !== undefined && onSortChange !== undefined;
  const sorting = controlled ? sort : internalSort;
  const setSorting = (u: Updater<SortingState>) => {
    const next = typeof u === "function" ? u(sorting) : u;
    (controlled ? onSortChange : setInternalSort)(next);
  };
  const rowOpen = useExpanded("single");
  const editor = useExpanded("single");
  const parentRef = useRef<HTMLDivElement>(null);
  const selectable = selection !== undefined;
  const partsOf = useCallback((t: Transaction) => childrenByParent.get(t.id)?.length ?? 0, [childrenByParent]);
  // the ingest day (lib/agg.ts ingestDay): a row dated after it is marked "dated ahead" (lib/agg.ts datedAhead)
  const ingest = ingestDay(manifest.generated_at);
  const columns = useMemo(() => makeColumns(overlay.pendingIds, selectable, partsOf, ingest),
    [overlay.pendingIds, selectable, partsOf, ingest]);
  const hasBalance = runningBalance !== undefined;
  const columnVisibility = useMemo(() => ({
    ...Object.fromEntries(COMPACT_HIDDEN.map((id) => [id, layout !== "compact"])),
    [BALANCE]: hasBalance, account_name: !hideAccount,
  }), [layout, hasBalance, hideAccount]);

  // the rows the register SHOWS: a split parent stands in for its parts when every part passed the
  // filter, a lone part shows as itself; `txns` stays the parts (the CSV and the counts read it)
  const displayRows = useMemo(() => regroupSplits(txns, childrenByParent, splitParents), [txns, childrenByParent, splitParents]);
  const table = useReactTable({
    data: displayRows, columns, state: { sorting, columnVisibility }, onSortingChange: setSorting,
    // a register is always sorted: clicking the active column flips its direction, never clears it
    enableSortingRemoval: false,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
  });
  const rows = table.getRowModel().rows;
  // every row the header box may select, in display order
  const answerable = useMemo(
    () => rows.filter((r) => editable(r.original)).map((r) => r.original.id), [rows]);
  const rowH = stacked ? (selectable ? ROW_H.stackedSelectable : ROW_H.stacked) : ROW_H.grid;
  // a px max-height shows whole rows: 480px of 47px rows ends in a 10px sliver of the eleventh, which looks like a rendering bug
  const bodyMax = typeof maxHeight === "number" ? Math.max(rowH, Math.floor(maxHeight / rowH) * rowH) : maxHeight;
  const visibleIds = table.getVisibleFlatColumns().map((c) => c.id);
  const grid = gridStyle(visibleIds, layout);
  const sortId = sorting[0]?.id ?? "date";
  const sortDesc = sorting[0]?.desc ?? true;
  // an account page has no Account column to sort by (it is the same on every row)
  const stackedSorts = hideAccount ? STACKED_SORTS.filter((o) => o.id !== "account_name") : STACKED_SORTS;
  // The end-of-day balance of a date belongs to the day, not to a row, and under any other sort the rows of
  // a day are scattered, so it is shown only while sorted by date - on the row of each day's run that is
  // nearest the LATER days (newest first: the run's first row; oldest first: its last): reading toward the
  // present, that is where the day ends. Keyed by row id; a row not in the map shows nothing, never a guess.
  const byDate = sortId === "date";
  const balanceByRow = useMemo(() => {
    const out = new Map<string, number>();
    if (!runningBalance || !byDate) return out;
    rows.forEach((r, i) => {
      const v = runningBalance.get(r.original.date);
      if (v === undefined) return;
      const nearer = rows[sortDesc ? i - 1 : i + 1]?.original.date;       // the neighbour toward later days
      if (nearer !== r.original.date) out.set(r.original.id, v);
    });
    return out;
  }, [rows, runningBalance, byDate, sortDesc]);
  const balanceNote = runningBalance && !byDate ? " · balance shows when sorted by date" : "";

  // ARIA row numbers: a grid's header is row 1 (the stacked list has none). The open row's detail is a row of the table too,
  // so it takes the next number and every row after it moves down one - the numbers stay one unbroken run, and the count
  // includes it. (A detail whose row has scrolled out of the virtual window is not drawn, but the numbers still leave its place.)
  const firstRow = stacked ? 1 : 2;
  const isRowOpen = rowOpen.isOpen;
  const openAt = useMemo(() => rows.findIndex((r) => isRowOpen(r.original.id)), [rows, isRowOpen]);
  const rowIndex = (i: number) => i + firstRow + (openAt >= 0 && i > openAt ? 1 : 0);
  const rowCount = firstRow - 1 + rows.length + (openAt >= 0 ? 1 : 0) + (rows.length === 0 ? 1 : 0);

  // One tab stop (roving tabindex): `active` is the only tabIndex 0 row; arrows move it.
  const [active, setActiveRow] = useState(0);
  const [focusTarget, setFocusTarget] = useState<number | null>(null);
  // A different list (a filter edit) or a new sort reorders the rows under the stop, so it goes back to the
  // first row - which also keeps a list that shrinks from ever leaving zero stops. Keyed on what the list IS
  // (length, first and last id, sort), not on the array's identity: the answers overlay hands over a new array
  // after a category save and again when the server echoes it ~800ms later, with the same rows, and the stop
  // must not jump to row 0 under a keyboard user. Derived in render (React re-renders at once, before anything
  // is committed).
  const sortKey = sorting.map((x) => `${x.id}:${x.desc ? "d" : "a"}`).join(",");
  const listKey = JSON.stringify([displayRows.length, displayRows[0]?.id, displayRows[displayRows.length - 1]?.id, sortKey]);
  const [stopFor, setStopFor] = useState(listKey);
  if (stopFor !== listKey) {
    setStopFor(listKey);
    setActiveRow(0);
    setPageEnd(PHONE_PAGE);                      // a different list starts again at its first page
  }
  // the rows a phone shows (a prefix of the list), and the transactions they and the whole list are: a split purchase
  // counts once, as the filter bar's count does (its parent row, or its lone parts together). Each count is over a
  // PREFIX of the list, and the next page's is what the longer prefix adds: a split whose lone parts fall on both sides
  // of a page's end counts once, so "Showing K" plus "Show N more" never passes the whole
  const shownRows = paged ? Math.min(pageEnd, rows.length) : rows.length;
  const nextRows = Math.min(PHONE_PAGE, rows.length - shownRows);
  const txnsIn = useCallback((n: number) => purchaseCount(rows.slice(0, n).map((r) => r.original)), [rows]);
  const txnCount = useMemo(() => txnsIn(rows.length), [txnsIn, rows.length]);
  const shownTxns = useMemo(() => txnsIn(shownRows), [txnsIn, shownRows]);
  const nextTxns = useMemo(() => txnsIn(shownRows + nextRows) - shownTxns, [txnsIn, shownRows, nextRows, shownTxns]);
  // "Show more": the first row it adds becomes the tab stop and takes focus (the button moves below the new rows, and
  // after the last page it is gone, which would drop focus to the page)
  const showMore = () => {
    setPageEnd(shownRows + PHONE_PAGE);
    setActiveRow(shownRows);
    setFocusTarget(shownRows);
  };

  // Row heights are per layout: a grid row is one ~29px line, a stacked row two lines, ~47px.
  // Measurements are keyed by layout and the row elements are re-created on a flip, so every row is
  // measured afresh - a resize across a cut (or a first frame laid out at the other width) must never
  // leave grid rows positioned at the stacked height, gaps between them.
  const itemKey = useCallback((i: number) => `${layout}:${i}`, [layout]);
  // the active row stays rendered wherever the list is scrolled: a virtualized-away tab stop would
  // leave the register with none (Tab would skip it), and a row about to be focused must exist.
  // (`active >= r.count`: the render that finds a shorter list still holds the old index until React
  // restarts it with the reset above - an index past the end would crash that pass.)
  const rangeExtractor = useCallback((r: Parameters<typeof defaultRangeExtractor>[0]) => {
    const base = defaultRangeExtractor(r);
    return active >= r.count || base.includes(active) ? base : [...base, active].sort((a, b) => a - b);
  }, [active]);
  const virtualizer = useVirtualizer({
    count: paged ? 0 : rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowH,
    getItemKey: itemKey,
    rangeExtractor,
    overscan: 20,
  });

  // focus the row the keys moved to once it has committed (it is the active row, so it is rendered)
  useLayoutEffect(() => {
    if (focusTarget === null) return;
    parentRef.current?.querySelector<HTMLElement>(`[data-row-index="${focusTarget}"]`)?.focus();
    setFocusTarget(null);
  }, [focusTarget]);
  // The body scroller loses a scrollbar's width when its list overflows (classic scrollbars), the header
  // row does not. Fixed px tracks never notice, but the flexible Account track would sit 15px wider in the
  // header and push every column after it out of line - so the header pads by the same gutter.
  const [gutter, setGutter] = useState(0);
  useLayoutEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    const measure = () => setGutter(el.offsetWidth - el.clientWidth);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [layout, rows.length]);

  // a phone's keys move among the rows it shows (focusing a row scrolls the page to it); a virtualised list scrolls its
  // body to the row first
  const moveTo = (i: number) => {
    if (shownRows === 0) return;
    const j = Math.max(0, Math.min(shownRows - 1, i));
    setActiveRow(j);
    if (!paged) virtualizer.scrollToIndex(j);
    setFocusTarget(j);
  };

  // the PARTS the filter passed, in display order: a parent row stands for its parts, never for itself
  const csvRows = useMemo(
    () => rows.flatMap((r) => (r.original.is_parent ? childrenByParent.get(r.original.id) ?? [] : [r.original])),
    [rows, childrenByParent]);
  // the transactions those rows are (a split purchase once, however many of its parts are rows): the same count as the
  // filter bar's "N of M transactions" above the register, which the button names beside its rows when the two differ
  const csvTransactions = useMemo(() => purchaseCount(csvRows), [csvRows]);
  const exportCsv = () => {
    if (csvRows.length === 0) return;                  // the button is disabled at 0; a header-only file would be a lie about a download
    const header = ["date", "account", "owner", "vendor", "category", "subcategory",
      "type", "amount", "description", "tags", "needs_review", "parent_id"];
    const body = csvRows.map((t) => [t.date, t.account_name, t.owner, t.vendor, t.category, t.subcategory,
      t.type, t.amount, t.description, t.tags.join(";"), t.needs_review ? "1" : "", t.parent_id ?? ""]);
    const range = `${csvRows[csvRows.length - 1].date}_${csvRows[0].date}`;
    downloadCsv(`transactions_${range}.csv`, toCsv(body, header));
  };

  const toggleRow = (id: string) => {
    if (rowOpen.isOpen(id)) editor.close();
    rowOpen.toggle(id);
  };
  // a PageUp / PageDown step: what the body shows (on a phone, the window) less one row of context, at least one row
  const pageRows = () => Math.max(1,
    Math.floor((paged ? window.innerHeight : parentRef.current?.clientHeight ?? 0) / rowH) - 1);
  // Arrows / Home / End / PageUp / PageDown move the tab stop (not with Ctrl, Meta or Alt held: those are the
  // browser's and the OS's); `x` toggles the row's selection (when the table has one and the row can be
  // answered); Enter / Space open the descriptor. Only keys pressed on the row itself count - ones typed in
  // the Edit button or editor inside the expanded row are theirs.
  const onRowKey = (e: KeyboardEvent<HTMLElement>, i: number, id: string) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleRow(id); return; }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if ((e.key === "x" || e.key === "X") && selection) {
      if (editable(rows[i].original)) { e.preventDefault(); selection.toggle(id); }
      return;
    }
    const to = e.key === "ArrowDown" ? i + 1 : e.key === "ArrowUp" ? i - 1
      : e.key === "PageDown" ? i + pageRows() : e.key === "PageUp" ? i - pageRows()
      : e.key === "Home" ? 0 : e.key === "End" ? shownRows - 1 : null;
    if (to === null) return;
    e.preventDefault();
    moveTo(to);
  };

  return (
    <div>
      {/* the open row's editor (and on Transactions the bulk editor above) writes answers, so a save the store refused is
          said here as well as on Review. Only a refusal: an unreachable store retries by itself, and Review says that.
          An alert, which a screen reader announces as it appears (the save's editor has closed by then). */}
      {answers.refused ? (
        <p role="alert" data-testid="answers-refused" className="mb-2 text-xs">
          <AnswersLocalOnlyNotice refused={answers.refused} />
        </p>
      ) : null}
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        {stacked ? (
          <div className="flex items-center gap-1.5">
            {/* a stacked list has no header row, so the select-all box rides the toolbar (44px target) */}
            {selection && (
              <label className="flex min-h-11 min-w-11 items-center justify-center">
                <SelectAll ids={answerable} selection={selection} />
              </label>
            )}
            <label htmlFor={sortSelectId} className="sr-only">Sort by</label>
            <select id={sortSelectId} value={sortId}
              onChange={(e) => setSorting([{ id: e.target.value,
                desc: table.getColumn(e.target.value)?.getFirstSortDir() === "desc" }])}
              className="min-h-11 rounded-md border border-zinc-700 bg-zinc-900 px-2 text-base text-zinc-200">
              {stackedSorts.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              {/* a column sorted on a wider screen (Type, Owner...) is shown as itself, not as "Date" */}
              {!stackedSorts.some((o) => o.id === sortId) && <option value={sortId}>{sortLabel(sortId)}</option>}
            </select>
            <button type="button" onClick={() => setSorting([{ id: sortId, desc: !sortDesc }])}
              aria-label={`Sort direction, currently ${sortDesc ? "descending" : "ascending"}`}
              className="flex min-h-11 min-w-11 items-center justify-center rounded-md border border-zinc-700 text-sm text-zinc-300 hover:bg-zinc-800">
              <span aria-hidden="true">{sortDesc ? "↓" : "↑"}</span>
            </button>
          </div>
        ) : (
          // the compact grid may be sorted by a column it folds away (?sort=type:asc): this names it
          <span className="text-xs text-zinc-500">
            {privacy && `amounts hidden${keys ? " (Alt+Shift+P)" : ""} · `}{words.click} a row for the raw descriptor{selection && keys ? " · x selects it" : ""} · sorted by {sortLabel(sortId)} {sortDesc ? "↓" : "↑"}{balanceNote}
          </span>
        )}
        {/* the file is for the family's own spreadsheet: privacy mode never touches it, and the words say so */}
        <span className="flex items-center gap-2">
          <button onClick={exportCsv} disabled={csvRows.length === 0} title="CSV exports are never masked — they hold the real amounts"
            className={`rounded-md border border-zinc-700 px-2.5 py-1 text-xs text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent ${stacked ? "min-h-11" : ""}`}>
            ⬇ Export CSV: {countOf(csvRows.length, "row")}
            {csvTransactions !== csvRows.length && ` (${countOf(csvTransactions, "transaction")})`}
          </button>
          <CsvNote />
        </span>
      </div>
      {paged && rows.length > 0 && (
        <p data-testid="register-count" className="mb-1 text-xs text-zinc-400">
          {shownRows < rows.length
            ? `Showing ${shownTxns.toLocaleString("en-US")} of ${countOf(txnCount, "transaction")}`
            : countOf(txnCount, "transaction")}
        </p>
      )}
      {stacked && (
        <p className="mb-1 text-[11px] text-zinc-500">
          {privacy && "amounts hidden · "}{words.click} a row for the raw descriptor{selection && keys ? " · x selects it" : ""}{balanceNote}
        </p>
      )}
      {/* grids: one horizontal scroller around BOTH header and body so the fixed-width columns stay
          aligned if panned (they fit from lg on; the floor is the grid's own width); stacked rows fit,
          so there is no floor */}
      <div className="overflow-x-auto">
      <div role="table" aria-label="Transactions" aria-rowcount={rowCount}
        style={stacked ? undefined : { minWidth: grid.minWidth + gutter }}>
      {!stacked && (
      <div role="rowgroup">
      <div role="row" aria-rowindex={1} style={{ ...grid.style, paddingRight: gutter }}
        className="border-b border-zinc-700 pb-1 text-xs font-semibold text-zinc-400">
        {table.getFlatHeaders().map((h) => {
          if (h.column.id === SELECT && selection) {
            return (
              <div key={h.id} role="columnheader" className="flex min-w-0 items-center justify-center">
                <SelectAll ids={answerable} selection={selection} />
              </div>
            );
          }
          // a column that cannot sort (the running balance) is a plain header: no button, no aria-sort
          if (!h.column.getCanSort()) {
            return <div key={h.id} role="columnheader" className="min-w-0 truncate">{flexRender(h.column.columnDef.header, h.getContext())}</div>;
          }
          const dir = h.column.getIsSorted();
          return (
            <div key={h.id} role="columnheader" className="min-w-0"
              aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}>
              <button type="button" onClick={h.column.getToggleSortingHandler()}
                className="block w-full truncate text-left hover:text-zinc-200">
                {flexRender(h.column.columnDef.header, h.getContext())}
                {HEADER_NAME[h.column.id] && <span className="sr-only">{HEADER_NAME[h.column.id]}</span>}
                {{ asc: " ↑", desc: " ↓" }[dir as string] ?? ""}
              </button>
            </div>
          );
        })}
      </div>
      </div>
      )}
      {/* a phone's list scrolls with the page: no body scroller, no fixed height */}
      <div role="rowgroup" ref={parentRef} className={paged ? undefined : "overflow-y-auto"}
        style={paged ? undefined : { maxHeight: bodyMax }}>
        {rows.length === 0 && (
          <div role="row" aria-rowindex={firstRow}>
            <div role="cell" aria-colspan={Math.max(1, visibleIds.length)} data-testid="register-empty"
              className="px-2 py-8 text-center text-xs text-zinc-500">
              {onClear ? "No transactions match these filters. " : "No transactions to show. "}
              {onClear && (
                <button type="button" onClick={onClear}
                  className="inline-flex min-h-11 items-center text-red-400 underline md:min-h-0">Clear all filters</button>
              )}
            </div>
          </div>
        )}
        <div style={paged ? undefined : { height: virtualizer.getTotalSize(), position: "relative" }}>
          {(paged ? rows.slice(0, shownRows).map((_, index) => ({ index, start: 0 })) : virtualizer.getVirtualItems()).map((vi) => {
            const row = rows[vi.index];
            const t = row.original;
            const open = rowOpen.isOpen(t.id);
            const prov = sourceChip(t.category_source);
            const parts = t.is_parent ? childrenByParent.get(t.id) ?? [] : [];
            const whole = t.parent_id !== null ? splitParents.get(t.parent_id) : undefined;
            const rowProps = {
              // a grid's header row is row 1; the stacked list has none
              role: "row", "aria-rowindex": rowIndex(vi.index), "aria-expanded": open,
              tabIndex: vi.index === active ? 0 : -1, "data-row-index": vi.index,
              // a row the box can select says whether it is (the box itself carries the same state); a parent says
              // nothing about selection, like a transfer
              ...(selection && editable(t) ? { "aria-selected": selection.selected.has(t.id) } : {}),
              onClick: () => toggleRow(t.id),
              onFocus: () => setActiveRow(vi.index),
              onKeyDown: (e: KeyboardEvent<HTMLElement>) => onRowKey(e, vi.index, t.id),
            } as const;
            return (
              <div key={`${layout}:${row.id}`} data-index={vi.index} ref={paged ? undefined : virtualizer.measureElement}
                style={paged ? undefined : { position: "absolute", top: 0, transform: `translateY(${vi.start}px)`, width: "100%" }}
                className="border-b border-zinc-800/60 text-xs">
                {stacked ? (
                  <div {...rowProps}
                    className={`cursor-pointer px-0.5 py-1.5 hover:bg-zinc-800/40 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-400 ${selection ? "flex items-center" : ""}`}>
                    {/* the checkbox target takes the row's left edge (44px, also on rows that have none, so every
                        row's text lines up); the two lines keep the rest */}
                    {selection && <div role="cell" className="w-11 shrink-0"><RowCheck t={t} selection={selection} tap /></div>}
                    <div className={selection ? "min-w-0 grow" : undefined}>
                      <div role="cell" className="flex items-baseline justify-between gap-2">
                        {/* a phone's lines wrap at a word: no vendor, category or account is cut short */}
                        <span className={`min-w-0 text-[13px] font-medium ${paged ? "break-words" : "truncate"}`}>
                          {t.parent_id !== null && <span className="font-normal text-zinc-500">split of </span>}{t.vendor}
                        </span>
                        <span className="flex shrink-0 items-center gap-1.5 text-[13px]">
                          <ReviewMark t={t} pending={overlay.pendingIds.has(t.id)} />
                          <Amount value={t.amount} />
                          <span aria-hidden="true"
                            className={`text-zinc-500 transition-transform ${open ? "rotate-90" : ""}`}>›</span>
                        </span>
                      </div>
                      <div role="cell" className={`mt-0.5 flex min-w-0 items-center text-[11px] text-zinc-500 ${
                        paged ? "flex-wrap gap-x-1.5 gap-y-0.5" : "gap-1.5"}`}>
                        <span className="shrink-0 tabular-nums">{t.date}</span>
                        {ingest !== null && t.date > ingest && <DatedAhead ingest={ingest} />}
                        <span aria-hidden="true">·</span>
                        {/* "split · N parts" is short and says what the row is: the account name gives way, not this */}
                        {t.is_parent
                          ? <span className="shrink-0">split · {parts.length} parts</span>
                          : <span className={paged ? "min-w-0 break-words" : "min-w-0 truncate"}>{categoryLabel(t)}</span>}
                        <SourceChip source={t.is_parent ? "split" : t.category_source} focusable={false} />
                        <TagChips tags={t.tags} />
                        {!hideAccount && (
                          <>
                            <span aria-hidden="true">·</span>
                            <span className={paged ? "min-w-0 break-words" : "min-w-0 max-w-[40%] shrink-[2] truncate"}>{t.account_name}</span>
                          </>
                        )}
                        {balanceByRow.has(t.id) && (
                          <>
                            <span aria-hidden="true">·</span>
                            <span className="shrink-0 tabular-nums">bal {fmtUSD(balanceByRow.get(t.id)!)}</span>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div {...rowProps} style={grid.style}
                    className="cursor-pointer py-1.5 hover:bg-zinc-800/40 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-400">
                    {row.getVisibleCells().map((cell) => (
                      <div key={cell.id} role="cell" className="truncate">
                        {cell.column.id === SELECT
                          ? selection && <RowCheck t={t} selection={selection} />
                          : cell.column.id === BALANCE
                            ? balanceByRow.has(t.id) && <span className="tabular-nums text-zinc-400">{fmtUSD(balanceByRow.get(t.id)!)}</span>
                            : flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </div>
                    ))}
                  </div>
                )}
                {open && (
                  <div role="row" aria-rowindex={rowIndex(vi.index) + 1}>
                    {/* spans the cells its layout draws: the stacked row's two lines (plus the checkbox cell when there is one), else every visible column */}
                    <div role="cell" aria-colspan={stacked ? (selection ? 3 : 2) : visibleIds.length}
                      className="bg-zinc-900 px-2 pb-2 text-[11px] text-zinc-400">
                      <div className={`flex py-1 ${stacked ? "flex-col gap-1" : "flex-row items-center gap-2"}`}>
                        {/* wraps (never truncates): the provenance words at its end are the only way a finger reads them */}
                        <span className="min-w-0 grow break-words font-mono">
                          {maskMoneyText(t.description)}
                          <span className="ml-2 text-faint">[{t.source_file}] {t.tags.join(", ")}</span>
                          {/* the compact grid folds Owner and Type out and the stacked rows never had them: say them here */}
                          {layout !== "full" && <span className="ml-2">Owner: {t.owner} · Type: {t.type}</span>}
                          {prov && <span className={`ml-2 ${chipInk(prov)}`}>{prov.label}: {prov.title}</span>}
                          {/* a finger never sees the chip's title: the open row says it in words */}
                          {ingest !== null && t.date > ingest && (
                            <span className="ml-2 font-sans text-amber-300">dated ahead: {aheadTitle(ingest)}</span>
                          )}
                          {t.is_parent && (
                            <span className={`ml-2 font-sans ${SPLIT_INK}`}>
                              split by the {t.vendor} rule into {countOf(parts.length, "part")} (<Code>vendor_rules.yaml</Code>) — this row carries no spend of its own; edit the rule to change the parts, or filter to a part's category to answer it
                            </span>
                          )}
                          {whole && (
                            <span className={`ml-2 font-sans ${SPLIT_INK}`}>
                              part of a {fmtUSD(whole.amount)} purchase on {fmtDate(t.date)}: an answer here changes this part only
                            </span>
                          )}
                        </span>
                        <div className={`flex shrink-0 items-center gap-3 ${stacked ? "self-start" : ""}`}>
                          {editable(t) && (
                            <button type="button"
                              onClick={(e) => { e.stopPropagation(); editor.toggle(t.id); }}
                              className={`shrink-0 rounded-md border border-zinc-700 px-2 py-1 text-[11px] text-zinc-300 hover:bg-zinc-800 ${stacked ? "min-h-11" : ""}`}>
                              {editor.isOpen(t.id) ? "Close editor" : "Edit category"}
                            </button>
                          )}
                          {/* the way to the account's page for a keyboard and for a finger (the phone's second line names the account
                              as plain text): a normal link, in the tab order while the row is open. Not on the account's own page. */}
                          {!hideAccount && (
                            <AccountLink id={t.account}
                              className={`shrink-0 whitespace-nowrap text-[11px] ${stacked ? "inline-flex min-h-11 items-center" : ""}`}>
                              Account page →
                            </AccountLink>
                          )}
                        </div>
                      </div>
                      {t.is_parent && (
                        <ul data-testid="split-parts" className="mt-1 list-disc pl-4 font-sans">
                          {parts.map((k) => <li key={k.id}>{categoryLabel(k)} · {fmtUSD(k.amount)}</li>)}
                        </ul>
                      )}
                      {editor.isOpen(t.id) && (
                        <div className="pb-1 font-sans normal-case">
                          <CategoryEditor txns={[t]}
                            onSaved={() => editor.close()}
                            onCancel={() => editor.close()} />
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
      </div>
      </div>
      {/* a phone's next page: the rows it adds, named in transactions as the count line counts them */}
      {paged && nextRows > 0 && (
        <button type="button" data-testid="register-more" onClick={showMore}
          aria-label={`Show ${countOf(nextTxns, "more transaction", "more transactions")}`}
          className="mt-2 inline-flex min-h-11 w-full items-center justify-center rounded-md border border-zinc-700 text-sm text-zinc-300 hover:bg-zinc-800">
          Show {nextTxns} more
        </button>
      )}
    </div>
  );
}

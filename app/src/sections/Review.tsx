// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Review inbox — the write path for categorization. Lists flagged/unknown
// transactions; answering routes through CategoryEditor → the answers store →
// the DataContext overlay (instant) → the ETL's next ingest (durable).
// One list layout for every breakpoint: it's an inbox, not a data table.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import CategoryEditor from "../components/CategoryEditor";
import { Code } from "../components/NoData";
import SourceChip from "../components/SourceChip";
import { AnswersLocalOnlyNotice, Card, PHONE_WRAP, pillCls, TAP_AREA } from "../components/ui";
import { useData } from "../data/DataContext";
import type { Transaction } from "../data/types";
import { isReviewCandidate, ownerKey } from "../lib/answers";
import type { CategoryVocab } from "../lib/categories";
import { countOf, fmtDate, fmtUSD } from "../lib/format";
import { usePointerWords } from "../lib/pointerWords";
import { maskMoneyText } from "../lib/privacy";
import { useExpanded } from "../lib/useExpanded";
import { useVocab } from "../lib/useVocab";

type Group = "venmo" | "cash" | "deposits" | "other";
type Bucket = "all" | Group;

const BUCKETS: { key: Bucket; label: string }[] = [
  { key: "all", label: "All" },
  { key: "venmo", label: "Venmo & Zelle" },
  { key: "cash", label: "Cash & checks" },
  { key: "deposits", label: "Deposits" },
  { key: "other", label: "Other" },
];

/** The ONE group a row is in, so the pills partition the inbox: they sum to "All", every row is in exactly one, and none
 *  is in two (a Venmo deposit used to count under both Venmo & Zelle and Deposits) or in none (an uncategorized debit was
 *  in no pill at all - 241 of 250). First match wins, in the pills' order: Venmo & Zelle, Cash & checks, Deposits, then Other. */
export function groupOf(vocab: CategoryVocab, t: Transaction): Group {
  if (vocab.isVenmoUnknownSub(t.subcategory)) return "venmo";
  if (vocab.isMoneyMovementSub(t.subcategory)) return "cash";
  if (t.amount > 0) return "deposits";
  return "other";
}

const inBucket = (vocab: CategoryVocab, t: Transaction, b: Bucket): boolean => b === "all" || groupOf(vocab, t) === b;

/** the inbox draws this many rows at a time, then "show 50 more": 250 rows made a screen-and-a-half-per-hundred scroll, and a
 *  narrow column at 1280 stretched the page. An answered row leaves the list, so the next one slides up into the window. */
const PAGE = 50;
/** how long the Saved toast, and its Undo, stays after a save */
export const UNDO_MS = 6000;

export default function Review() {
  const { data, answers, overlay } = useData();
  const { click } = usePointerWords();
  const vocab = useVocab();
  const [bucket, setBucketState] = useState<Bucket>("all");
  const [limit, setLimit] = useState(PAGE);
  // another group starts again from its first page
  const setBucket = (b: Bucket) => { setBucketState(b); setLimit(PAGE); };
  const editor = useExpanded("single");
  const [undo, setUndo] = useState<{ key: string; label: string } | null>(null);
  const undoTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // a row is a conflict when the answer that owns it in the overlay (ownerKey: the category-carrying one, the
  // overlay's own precedence) is one a config override beat
  const { open, conflicted } = useMemo(() => {
    const all = data.transactions.filter((t) => isReviewCandidate(vocab, t));
    const conflictedRows: Transaction[] = [];
    const openRows: Transaction[] = [];
    for (const t of all) {
      const key = ownerKey(t, answers.doc);
      if (key && overlay.conflictedKeys.has(key)) conflictedRows.push(t);
      else openRows.push(t);
    }
    openRows.sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount));
    return { open: openRows, conflicted: conflictedRows };
  }, [vocab, data.transactions, answers.doc, overlay.conflictedKeys]);

  const inGroup = useMemo(() => open.filter((t) => inBucket(vocab, t, bucket)), [vocab, open, bucket]);
  const shown = useMemo(() => inGroup.slice(0, limit), [inGroup, limit]);
  // rows whose values currently come from a not-yet-ingested answer — NOT the
  // total answers stored (an absorbed one-off answer lives in the doc until the cap evicts it; a vendor answer stays)
  const pendingCount = overlay.pendingIds.size;

  /** the Saved toast's timer, stopped: a newer save restarts it, an undo ends the toast at once, and a page that unmounts
   *  leaves no timer to fire into a tree that is gone */
  const stopUndoTimer = useCallback(() => {
    if (undoTimer.current) clearTimeout(undoTimer.current);
    undoTimer.current = null;
  }, []);
  useEffect(() => stopUndoTimer, [stopUndoTimer]);

  const onSaved = (key: string, label: string) => {
    editor.close();
    setUndo({ key, label });
    stopUndoTimer();
    undoTimer.current = setTimeout(() => { undoTimer.current = null; setUndo(null); }, UNDO_MS);
  };

  const doUndo = () => {
    if (!undo) return;
    answers.unanswer(undo.key);
    stopUndoTimer();
    setUndo(null);
  };

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div>
        <h1 className="text-xl font-bold text-zinc-100">Review</h1>
        <p className="mt-0.5 text-xs text-zinc-500">
          {open.length > 0
            ? `${countOf(open.length, "transaction")} could use a category — biggest first.`
            : "Nothing needs attention."}
          {pendingCount > 0 && (
            <span className="ml-1 text-emerald-400/90">
              {countOf(pendingCount, "change")} saved — permanent at the next ingest.
            </span>
          )}
          {answers.localOnly && <AnswersLocalOnlyNotice refused={answers.refused} />}
        </p>
      </div>

      {open.length > 0 && (
        <div className="flex flex-wrap gap-1.5 max-md:gap-y-2">
          {BUCKETS.map((b) => {
            const n = b.key === "all" ? open.length
              : open.filter((t) => inBucket(vocab, t, b.key)).length;
            if (b.key !== "all" && n === 0) return null;
            return (
              <button key={b.key} onClick={() => setBucket(b.key)}
                className={`${TAP_AREA} ${pillCls(bucket === b.key,
                  "border-zinc-500 bg-zinc-800 font-medium text-zinc-100",
                  "border-zinc-700 text-zinc-400 hover:bg-zinc-900")}`}>
                {b.label} <span className="text-zinc-500">{n}</span>
              </button>
            );
          })}
        </div>
      )}

      {open.length === 0 ? (
        <Card testId="card-review-inbox">
          <div className="py-8 text-center">
            <div className="text-3xl">✓</div>
            <div className="mt-2 text-sm font-medium text-zinc-200">All caught up</div>
            <p className="mt-1 text-xs text-zinc-500">
              Every transaction has a home. Spot something misfiled? Fix it from{" "}
              <a href="#/spending/transactions" className="text-emerald-400 hover:underline">Spending → Transactions</a>{" "}
              — {click} a row, then “Edit category”.
            </p>
          </div>
        </Card>
      ) : (
        <div data-testid="card-review-inbox" className="divide-y divide-zinc-800 rounded-xl border border-zinc-800">
          {shown.map((t) => (
            <div key={t.id} className="p-3">
              <button type="button"
                onClick={() => editor.toggle(t.id)}
                aria-expanded={editor.isOpen(t.id)}
                className="flex min-h-[44px] w-full items-center gap-3 text-left">
                {/* one line each from md up; a phone wraps them at a word rather than cutting a name inside one */}
                <div className="min-w-0 grow">
                  <div data-testid="review-row-vendor" className={`text-sm font-medium text-zinc-200 ${PHONE_WRAP}`}>
                    {t.vendor ?? maskMoneyText(t.description)}
                  </div>
                  <div className={`text-[11px] text-zinc-500 ${PHONE_WRAP}`}>
                    {fmtDate(t.date)} · {t.account_name}
                    {t.vendor && t.vendor !== t.description ? ` · ${maskMoneyText(t.description)}` : ""}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <div className={`text-sm tabular-nums ${t.amount < 0 ? "text-red-300" : "text-emerald-300"}`}>
                    {fmtUSD(t.amount)}
                  </div>
                  <div className="flex items-center justify-end gap-1 text-[10px] text-faint">
                    <span>{t.category}{t.subcategory ? ` / ${t.subcategory}` : ""}</span>
                    <SourceChip source={t.category_source} focusable={false} />
                  </div>
                </div>
                <span aria-hidden className={`shrink-0 text-zinc-500 transition-transform ${
                  editor.isOpen(t.id) ? "rotate-90" : ""}`}>›</span>
              </button>
              {editor.isOpen(t.id) && (
                <div className="mt-2">
                  <CategoryEditor txns={[t]} allowTags={false}
                    onSaved={(keys) => onSaved(keys[0], t.vendor ?? maskMoneyText(t.description))}
                    onCancel={() => editor.close()} />
                </div>
              )}
            </div>
          ))}
          {shown.length === 0 && (
            <div className="p-6 text-center text-xs text-zinc-500">
              nothing in this group — switch back to “All”
            </div>
          )}
          {inGroup.length > shown.length && (
            <div className="p-3 text-center">
              <button type="button" data-testid="review-more" onClick={() => setLimit(limit + PAGE)}
                className="inline-flex min-h-[40px] items-center rounded-md border border-zinc-700 px-3 text-xs text-zinc-300 hover:bg-zinc-800">
                show {Math.min(PAGE, inGroup.length - shown.length)} more
                <span className="ml-1 text-zinc-500">({countOf(inGroup.length - shown.length, "left, smaller amounts", "left, smaller amounts")})</span>
              </button>
            </div>
          )}
        </div>
      )}

      {conflicted.length > 0 && (
        <Card testId="card-review-conflicts" title="Conflicts — a config override wins here">
          <p className="mb-2 text-[11px] text-zinc-500">
            These were answered in the app, but an override in the household's configuration outranked the answer at the
            last ingest. Remove that override if the answer should win (<Code>overrides.yaml</Code>).
          </p>
          <div className="divide-y divide-zinc-800/60">
            {conflicted.map((t) => (
              <div key={t.id} className="flex items-center gap-3 py-2 text-xs">
                <span className={`min-w-0 grow text-zinc-300 ${PHONE_WRAP}`}>
                  {fmtDate(t.date)} · {t.vendor ?? maskMoneyText(t.description)}
                </span>
                <span className="tabular-nums text-zinc-400">{fmtUSD(t.amount)}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* a live region has to be in the page BEFORE its text changes to be announced reliably, so the toast's frame is always
          mounted (it is empty and zero-height until a save) and only its content comes and goes */}
      <div role="status" className="fixed inset-x-0 bottom-4 z-40 flex justify-center px-4 print:hidden">
        {undo && (
          <div className="flex items-center gap-3 rounded-full border border-zinc-700 bg-zinc-900 py-2 pl-4 pr-2 text-sm text-zinc-200 shadow-lg">
            {/* the editor starts on "Always for <vendor>": say when a save answered every row of the vendor, not this one */}
            <span className="max-w-[50vw] truncate">Saved “{undo.label}”{undo.key.startsWith("vendor:") ? " for all its rows" : ""}</span>
            <button onClick={doUndo}
              className="min-h-[36px] rounded-full bg-zinc-800 px-3 text-emerald-300 hover:bg-zinc-700">
              Undo
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

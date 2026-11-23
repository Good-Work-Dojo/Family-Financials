// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The one categorization editor — used by the Review inbox, the Spending
// table's inline "edit category" and its bulk bar (over a selection). Writes to
// the answers store; the overlay in DataContext makes the change visible
// everywhere instantly, and the ETL makes it durable on its next ingest.
import { useEffect, useId, useMemo, useState } from "react";
import { useData } from "../data/DataContext";
import type { Transaction } from "../data/types";
import { INCOME_TYPES } from "../lib/agg";
import { type AnswerSet, MAX_ANSWERS } from "../lib/answers";
import { useVocab } from "../lib/useVocab";
import { type AnswerScope, defaultScope, learnIndex, offeredVendor, vendorLearnable } from "../lib/vendorScope";
import { TagInput } from "./inputs";
import { Code } from "./NoData";
import { pillCls, TAP_AREA } from "./ui";

/** the row types an answer can categorize — the one set the register's Edit button and its selection
 *  checkboxes both read (TxnTable), so a type is selectable exactly when the editor can answer it */
export const EDITABLE_TYPES: ReadonlySet<string> = new Set(["purchase", "fee", "income"]);

/** a choice chip: the pill's look, with a 44px hit area on a phone (its rows keep `CHIP_ROWS` apart) */
function chipCls(selected: boolean): string {
  return `${TAP_AREA} ${pillCls(selected,
    "border-emerald-500/60 bg-emerald-500/15 font-medium text-emerald-200",
    "border-zinc-700 text-zinc-300 hover:bg-zinc-800")}`;
}
/** a wrapping row of chips: 8px between rows on a phone, so no two chips' hit areas overlap */
const CHIP_ROWS = "flex flex-wrap gap-1.5 max-md:gap-y-2";

const SECTION = "mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-zinc-500";

export default function CategoryEditor({ txns, onSaved, onCancel, allowTags = true, onVendorScope }: {
  /** one row (the register's inline editor, the Review inbox) or a selection (the bulk bar) */
  txns: Transaction[];
  /** called with the saved answers' keys (the first is what single-level undo restores) */
  onSaved: (answerKeys: string[]) => void;
  onCancel: () => void;
  /** the Tags section; off in the Review inbox (a question about categories - tags belong to the register) */
  allowTags?: boolean;
  /** told which vendor's single answer a save would write ("Always for"), null for one answer per row - and
   *  null again when the editor goes away - so the bulk bar can count the slots a save takes honestly */
  onVendorScope?: (vendor: string | null) => void;
}) {
  const { data, answers, indexes } = useData();
  const taxonomy = useMemo(
    () => data.assumptions.taxonomy ?? {}, [data.assumptions.taxonomy]);
  const many = txns.length > 1;
  // income rows keep category Income — the answerable question is which stream. A selection of both kinds
  // has no one category list that fits it, so it is refused
  const incomeRows = txns.filter((t) => INCOME_TYPES.has(t.type)).length;
  const mixed = incomeRows > 0 && incomeRows < txns.length;
  const incomeRow = txns.length > 0 && incomeRows === txns.length;
  const hasPart = txns.some((t) => t.parent_id !== null);
  // the vendor every row shares (none a part of a split purchase), named in the selection's header line
  const vendor = txns.length > 0 && !hasPart && txns.every((t) => t.vendor === txns[0].vendor) ? txns[0].vendor : null;
  // the vendor "Always for" would cover - null when that scope is not offered (lib/vendorScope.ts)
  const offered = offeredVendor(txns);
  const learnable = useMemo(() => learnIndex(data.vendors), [data.vendors]);
  const offeredLearnable = offered === null ? null : vendorLearnable(learnable, offered);
  // a tag every row already carries is not an answer; one row's own tag is (the others lack it)
  const sharedTags = txns.length ? txns.map((t) => t.tags).reduce((a, b) => a.filter((x) => b.includes(x))) : [];
  // type-driven buckets (Internal, Income): an answer can't retype a row, so only an income row is offered its own
  const vocab = useVocab();
  const categories = useMemo(
    () => (incomeRow ? [vocab.income] : Object.keys(taxonomy).filter((c) => !vocab.isTypeDriven(c))),
    [vocab, taxonomy, incomeRow]);

  const kind = mixed ? "mixed" : incomeRow ? "income" : "spend";
  const [category, setCategory] = useState<string | null>(incomeRow ? vocab.income : null);
  const [subcategory, setSubcategory] = useState<string | null>(null);
  const [added, setAdded] = useState<string[]>([]);
  // The scope starts on "Always for <vendor>" wherever that is offered and the vendor is learnable, else on this row
  // alone (defaultScope). A click is the user's choice for THAT vendor: when the selection moves to another vendor, or
  // to rows with no vendor scope, the start is derived again, so a choice made for one vendor never carries over.
  const [chosen, setChosen] = useState<{ vendor: string; scope: AnswerScope } | null>(null);
  const scope: AnswerScope = chosen !== null && chosen.vendor === offered ? chosen.scope : defaultScope(offered, offeredLearnable);
  const setScope = (s: AnswerScope) => { if (offered !== null) setChosen({ vendor: offered, scope: s }); };
  const [note, setNote] = useState("");
  // The selection can change under an open editor (a row ticked, a filter edit). A category chosen for one
  // kind of row must not carry over to the other - "Income" must never land on a purchase - so the choice
  // restarts when the kind flips. (Derived in render: React re-renders at once, before anything commits.)
  const [kindFor, setKindFor] = useState(kind);
  if (kindFor !== kind) {
    setKindFor(kind);
    setCategory(incomeRow ? vocab.income : null);
    setSubcategory(null);
  }
  const subs: string[] = category ? taxonomy[category] ?? [] : [];
  const addedTags = allowTags ? added.filter((t) => !sharedTags.includes(t)) : [];
  // An income row starts on the "Income" type, but that is no answer: a category answer for it with no stream
  // chosen would clear the stream it already has (a salary subcategory) and pin it. So for income rows the
  // category pair is written only once a stream is explicitly picked; until then a save is tags-only.
  const categoryChosen = category !== null && (!incomeRow || subcategory !== null);
  const canSave = !mixed && txns.length > 0 && (categoryChosen || addedTags.length > 0);
  const tagId = useId();
  // the vendor whose single answer this save writes (null: one txn answer per row)
  const scopedVendor = scope === "vendor" ? offered : null;
  useEffect(() => {
    onVendorScope?.(scopedVendor);
    return () => onVendorScope?.(null);
  }, [scopedVendor, onVendorScope]);

  const save = () => {
    if (!canSave) return;
    const n = note.trim() || undefined;
    // A category answer carries the pair, the reviewed flag and any added tags. A tags-only answer is
    // { tags } and nothing else: it never names a category, so it cannot pin the row's own against a later
    // rule fix, teach the learned rules, restamp the row's provenance, or clear a subcategory.
    const set: AnswerSet = categoryChosen && category !== null
      ? { category, subcategory, needs_review: false, ...(addedTags.length ? { tags: addedTags } : {}) }
      : { tags: addedTags };
    if (scopedVendor) {
      answers.answerVendor(scopedVendor, set, n);
      onSaved([`vendor:${scopedVendor}`]);
      return;
    }
    answers.answerTxns(txns.map((t) => t.id), set, n);
    onSaved(txns.map((t) => `txn:${t.id}`));
  };

  return (
    <div className="space-y-3 rounded-lg border border-zinc-700 bg-zinc-900/70 p-3"
         onClick={(e) => e.stopPropagation()}>
      {many && (
        <div className="text-xs text-zinc-400">
          {txns.length} transactions{vendor ? ` · all ${vendor}` : ""}
        </div>
      )}
      {mixed ? (
        <>
          <p className="text-xs text-amber-300">
            Pick rows of one kind: income rows and spending rows take different categories.
          </p>
          <button type="button" onClick={onCancel}
            className="min-h-[40px] rounded-lg border border-zinc-700 px-4 text-sm text-zinc-300 hover:bg-zinc-800 sm:min-h-0 sm:py-1.5 sm:text-xs">
            Cancel
          </button>
        </>
      ) : (
        <>
          <div>
            <div className={SECTION}>{incomeRow ? "Income type" : "Category"}</div>
            <div className={CHIP_ROWS}>
              {categories.map((c) => (
                <button key={c} type="button" className={chipCls(category === c)}
                  onClick={() => { setCategory(c); setSubcategory(null); }}>
                  {c}
                </button>
              ))}
            </div>
          </div>
          {category !== null && subs.length > 0 && (
            <div>
              <div className={SECTION}>
                {incomeRow ? "Stream" : "Subcategory"}{" "}
                <span className="normal-case text-faint">
                  {incomeRow ? (allowTags ? "(pick one to change it; tags alone leave the stream as it is)" : "(pick one)") : "(optional)"}
                </span>
              </div>
              <div className={CHIP_ROWS}>
                {subs.map((s) => (
                  <button key={s} type="button" className={chipCls(subcategory === s)}
                    onClick={() => setSubcategory(subcategory === s ? null : s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {allowTags && (
            <div>
              <label htmlFor={tagId} className={`block ${SECTION}`}>
                Tags{" "}
                <span className="normal-case text-faint">
                  ({scopedVendor ? `added to every “${scopedVendor}” row, past and future` : many ? "added to every row" : "added to this row"}
                  ; answers add tags, never remove them)
                </span>
              </label>
              {sharedTags.length > 0 && (
                <p className="mb-1 text-[11px] text-zinc-500">
                  already on {many ? "every row" : "this row"}: {sharedTags.join(", ")}
                </p>
              )}
              <TagInput id={tagId} value={added} onChange={setAdded} suggestions={indexes.tagList} />
            </div>
          )}
          {offered !== null && (
            <div>
              <div className={SECTION}>Apply to</div>
              <div className={CHIP_ROWS}>
                <button type="button" className={chipCls(scope === "txn")} aria-pressed={scope === "txn"}
                  onClick={() => setScope("txn")}>
                  {many ? "Just these rows" : "Just this transaction"}
                </button>
                <button type="button" className={chipCls(scope === "vendor")} aria-pressed={scope === "vendor"}
                  onClick={() => setScope("vendor")}>
                  Always for “{offered}”
                </button>
              </div>
              {offeredLearnable === false && (
                <p data-testid="scope-not-learnable" className="mt-1 text-[11px] text-faint">
                  Starts on “{many ? "Just these rows" : "Just this transaction"}”: a vendor rule keeps “{offered}” out of
                  learning (one name for many different payments, or a mixed basket), so “Always” would file every one of its
                  rows alike (<Code>vendor_rules.yaml</Code>).
                </p>
              )}
            </div>
          )}
          {hasPart && !incomeRow && (
            <p className="text-[11px] text-zinc-500">
              A part of a split purchase takes its own answer; the vendor's split rule decides every purchase of that vendor
              (<Code>split:</Code> in <Code>vendor_rules.yaml</Code>).
            </p>
          )}
          {many && (
            <p className="text-[11px] text-zinc-500">
              {scopedVendor
                ? `Writes 1 answer for “${scopedVendor}”, which covers every one of its rows and is never dropped.`
                : `Writes ${txns.length} answers, one per row — the family store keeps up to ${MAX_ANSWERS} one-off answers, dropping the oldest first.`}
            </p>
          )}
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500}
            placeholder="Note (optional) — e.g. who or what this was for"
            className="w-full rounded-md border border-zinc-700 bg-zinc-950 px-2.5 py-2 text-base text-zinc-200 placeholder:text-faint sm:py-1.5 sm:text-xs" />
          <div className="flex items-center gap-2">
            <button type="button" onClick={save} disabled={!canSave}
              className="min-h-[40px] rounded-lg bg-emerald-600 px-4 text-sm font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40 sm:min-h-0 sm:py-1.5 sm:text-xs">
              Save
            </button>
            <button type="button" onClick={onCancel}
              className="min-h-[40px] rounded-lg border border-zinc-700 px-4 text-sm text-zinc-300 hover:bg-zinc-800 sm:min-h-0 sm:py-1.5 sm:text-xs">
              Cancel
            </button>
            <span className="ml-auto text-[10px] text-faint">
              saved answers apply at the next ingest
            </span>
          </div>
        </>
      )}
    </div>
  );
}

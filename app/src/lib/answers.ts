// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Review-inbox answers: pure document logic + the instant-feel overlay.
// An answer is a human confirmation of a transaction's category, persisted
// server-side (./api/answers) and consumed by the ETL on its next ingest.
// Until then applyAnswers() previews the result over the loaded transactions,
// faithfully mirroring the ETL's precedence (txn answer beats vendor answer;
// config/overrides.yaml beats both — which surfaces here as "conflicted").
import { EPOCH_ISO } from "../data/useSyncedDoc";
import type { CategoryRename, Transaction } from "../data/types";
import type { CategoryVocab } from "./categories";
import { mapCategory } from "./categoryRenames";
import { storageKey } from "./demo";

export const ANSWERS_LOCAL_KEY = storageKey("ff.answers.v1");
/** one-off (txn-scope) answers the family store keeps; vendor-scope answers are never dropped (capAnswers) */
export const MAX_ANSWERS = 500;

export interface AnswerSet {
  /** absent on a tags-only answer ({ tags }, what the bulk tag editor sends): it names no category, so it
   *  can neither move a row's category, nor pin it against later rule fixes, nor teach the learned rules
   *  (etl/enrich/learned.py counts only sets that name one) */
  category?: string;
  /** sent with every category so stale pairs can't form; absent exactly when `category` is */
  subcategory?: string | null;
  tags?: string[];
  needs_review?: boolean;
}

export interface Answer {
  key: string;                  // "txn:<id>" | "vendor:<name>" — the merge key
  scope: "txn" | "vendor";
  txn_id?: string;
  vendor?: string;
  set: AnswerSet;
  note?: string;
  answered_at: string;
}

export interface AnswersDoc {
  version: 1;
  updated_at: string;
  answers: Answer[];
}

export function emptyAnswers(): AnswersDoc {
  return { version: 1, updated_at: EPOCH_ISO, answers: [] };
}

/** A usable `set`: an object whose known keys are well typed, with at least one of them present. The
 *  server refuses the same shapes (deploy/scenario_store.py validate_answers) and accepts a set with no
 *  category — a tags-only one — so this must too, or the echo of a tag answer would erase it. */
function validSet(s: unknown): s is AnswerSet {
  if (!s || typeof s !== "object" || Array.isArray(s)) return false;
  const x = s as Record<string, unknown>;
  if ("category" in x && typeof x.category !== "string") return false;
  if ("subcategory" in x && x.subcategory !== null && typeof x.subcategory !== "string") return false;
  if ("tags" in x && !(Array.isArray(x.tags) && x.tags.every((g) => typeof g === "string"))) return false;
  if ("needs_review" in x && typeof x.needs_review !== "boolean") return false;
  return ["category", "subcategory", "tags", "needs_review"].some((k) => k in x);
}

/** Defensive parse of anything (server echo, localStorage, garbage). */
export function normalizeAnswers(raw: unknown): AnswersDoc {
  const doc = (raw ?? {}) as Partial<AnswersDoc>;
  const answers: Answer[] = [];
  if (Array.isArray(doc.answers)) {
    for (const a of doc.answers as Answer[]) {
      if (!a || typeof a !== "object" || typeof a.key !== "string") continue;
      if (a.scope !== "txn" && a.scope !== "vendor") continue;
      if (a.scope === "txn" && typeof a.txn_id !== "string") continue;
      if (a.scope === "vendor" && typeof a.vendor !== "string") continue;
      if (!validSet(a.set)) continue;
      if (typeof a.answered_at !== "string") continue;
      answers.push(a);
    }
  }
  return {
    version: 1,
    updated_at: typeof doc.updated_at === "string" ? doc.updated_at : EPOCH_ISO,
    answers,
  };
}

/** The ETL renames stored answers in memory at load (etl/enrich/renames.py apply_to_set); the
 *  overlay must see the same names or a renamed category would paint every old answer pending (or
 *  conflicted) forever. Mirrors apply_to_set: a `subcategory` key is written only when the answer
 *  already had one or the rename moved it. Returns the same doc when nothing moves, so the overlay
 *  memo stays an identity. The stored doc is never rewritten — this is a view for applyAnswers. */
export function renameAnswersDoc(doc: AnswersDoc, renames: readonly CategoryRename[]): AnswersDoc {
  if (!renames.length) return doc;
  let changed = false;
  const answers = doc.answers.map((a) => {
    if (a.set.category === undefined) return a;          // a tags-only set names no category to rename
    const had = a.set.subcategory;
    const [category, sub] = mapCategory(a.set.category, had ?? null, renames);
    const subMoved = sub !== (had ?? null);
    if (category === a.set.category && !subMoved) return a;
    changed = true;
    const set: AnswerSet = { ...a.set, category };
    if ("subcategory" in a.set || subMoved) set.subcategory = sub;
    return { ...a, set };
  });
  return changed ? { ...doc, answers } : doc;
}

/** What a refused answers save says, on Review and above the register (whose editors write answers too). The store's
 *  caps sit far above what the family writes (MAX_ANSWERS one-off answers, and a 1 MiB body that holds about 4,800
 *  vendor answers beside them: deploy/scenario_store.py MAX_ANSWERS_BYTES), so a refusal - 413 too large, 400
 *  malformed - means something is wrong, and every later save of the same doc is refused the same way. It says where
 *  the answers are and who should look, never "for now". */
export function answersRefusalText(status: number): string {
  return `The family store refused the last save${status === 413 ? " as too large" : ""}, which should not happen. `
    + "These answers are saved in this browser only and won't reach the next ingest: tell whoever maintains the "
    + "dashboard.";
}

export function makeTxnAnswer(txnId: string, set: AnswerSet, nowISO: string,
                              note?: string): Answer {
  return { key: `txn:${txnId}`, scope: "txn", txn_id: txnId, set,
           ...(note ? { note } : {}), answered_at: nowISO };
}

export function makeVendorAnswer(vendor: string, set: AnswerSet, nowISO: string,
                                 note?: string): Answer {
  return { key: `vendor:${vendor}`, scope: "vendor", vendor, set,
           ...(note ? { note } : {}), answered_at: nowISO };
}

/** A stamp one tick later, in the stamp's own format - or null when it is not a stamp this app writes. A stamp
 *  with milliseconds goes up 1 ms; one without (legacy, "...T10:00:00Z") goes up 1 SECOND, because the server
 *  compares stamps as STRINGS and "...T10:00:00Z" sorts above "...T10:00:00.001Z" ('Z' > '.'). */
function nextStamp(stamp: string): string | null {
  const m = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3})?Z$/.exec(stamp);
  const t = m ? Date.parse(stamp) : NaN;
  if (Number.isNaN(t)) return null;
  const next = new Date(t + (m![1] ? 1 : 1000)).toISOString();     // always "...mmmZ"
  return m![1] ? next : next.replace(/\.\d{3}Z$/, "Z");
}

/** What the doc holds under `incoming.key` once `incoming` is written over `prior`: the two MERGE, they
 *  never replace. The server keeps the newer `answered_at` per key, so a replace would let a tags-only answer
 *  erase the family's category answer for the same row or vendor (and a category save omit a tag an earlier
 *  tags-only answer added: "answers add tags, never remove them"). Keys the incoming set names win; keys it
 *  does not (a tags-only set names only `tags`) keep the prior's; tags union; a category arrives with its own
 *  subcategory (a bare one clears it - never a stale pair); a note it does not replace stays.
 *
 *  `answered_at` is the incoming's - so the server's newest-wins merge keeps this result - EXCEPT when the
 *  incoming names no category and the prior does: a tags-only save teaches nothing, so it must not move the
 *  category answer to "now". The learned rules read the newest answers by stamp (a stamp moved to the front
 *  of a vendor's window can demote its rule), an override conflict is judged by it (a newer stamp reads as
 *  "the ingest has not seen this yet", which would repaint the category the override beat), and a stale tab
 *  holding the old copy must still lose. So the merged answer is stamped one tick after the prior's (nextStamp),
 *  which sorts above it for the server and below any other answer. */
export function mergeAnswer(prior: Answer, incoming: Answer): Answer {
  const set: AnswerSet = { ...prior.set, ...incoming.set };
  if (incoming.set.category !== undefined && incoming.set.subcategory === undefined) delete set.subcategory;
  if (prior.set.tags || incoming.set.tags) {
    set.tags = Array.from(new Set([...(prior.set.tags ?? []), ...(incoming.set.tags ?? [])])).sort();
  }
  const note = incoming.note ?? prior.note;
  const keepsPlace = incoming.set.category === undefined && prior.set.category !== undefined;
  const answered_at = (keepsPlace ? nextStamp(prior.answered_at) : null) ?? incoming.answered_at;
  return { ...incoming, answered_at, set, ...(note ? { note } : {}) };
}

/** plain code-unit order - the store's Python compares strings this way; localeCompare does not */
const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

/** `answers` (keys unique) in their own order, without the oldest one-off (txn-scope) answers past `limit` of
 *  them: oldest `answered_at` first, then by key, in plain code-unit order. Vendor-scope answers are never dropped
 *  and do not count toward the cap - each is a standing rule for every row of its vendor, often one the learned
 *  rules never cover (an excluded, split or explicitly categorized vendor); the only bound on them is the answers
 *  store's body cap (MAX_ANSWERS_BYTES, 1 MiB; a 413, which answersRefusalText reports). deploy/scenario_store.py
 *  cap_answers is the same rule; answers.eviction.fixture.json pins the two together. */
export function capAnswers(answers: Answer[], limit = MAX_ANSWERS): Answer[] {
  const oneOff = answers.filter((a) => a.scope !== "vendor");
  if (oneOff.length <= limit) return answers;
  const drop = new Set(oneOff.sort((a, b) => cmp(a.answered_at, b.answered_at) || cmp(a.key, b.key))
    .slice(0, oneOff.length - limit).map((a) => a.key));
  return answers.filter((a) => !drop.has(a.key));
}

/** `answer` becomes the entry for its key (replacing whatever is there), under the cap (capAnswers) */
function withAnswer(doc: AnswersDoc, answer: Answer, nowISO: string): AnswersDoc {
  const answers = capAnswers([...doc.answers.filter((a) => a.key !== answer.key), answer]);
  return { version: 1, updated_at: nowISO, answers };
}

/** Upsert by key, MERGING into an answer already under that key (mergeAnswer); enforces the cap (capAnswers:
 *  the oldest one-off answers go, never a vendor answer; a merge takes no new slot). */
export function upsertAnswer(doc: AnswersDoc, answer: Answer, nowISO: string): AnswersDoc {
  const prior = doc.answers.find((a) => a.key === answer.key);
  return withAnswer(doc, prior ? mergeAnswer(prior, answer) : answer, nowISO);
}

/** Undo: put an earlier answer back under its key (a replace, not a merge), re-stamped so it is the newest and
 *  the server's newest-wins merge keeps it over the copy it is undoing. */
export function restoreAnswer(doc: AnswersDoc, prior: Answer, nowISO: string): AnswersDoc {
  return withAnswer(doc, { ...prior, answered_at: nowISO }, nowISO);
}

/** Several answers in one pass (the bulk editor's write): upsertAnswer for each, under the same cap. */
export function upsertAnswers(doc: AnswersDoc, answers: Answer[], nowISO: string): AnswersDoc {
  return answers.reduce((d, a) => upsertAnswer(d, a, nowISO), doc);
}

export function removeAnswer(doc: AnswersDoc, key: string, nowISO: string): AnswersDoc {
  return { version: 1, updated_at: nowISO,
           answers: doc.answers.filter((a) => a.key !== key) };
}

// ------------------------------------------------------------- the overlay

export interface OverlayResult {
  /** the SAME array instance when no answer changes anything (identity guard) */
  txns: Transaction[];
  /** txn ids whose rendered values come from a not-yet-ingested answer */
  pendingIds: Set<string>;
  /** answer keys a config override beat at the last ingest (overlay stopped) */
  conflictedKeys: Set<string>;
}

function wouldChange(t: Transaction, set: AnswerSet): boolean {
  if (set.category !== undefined && t.category !== set.category) return true;
  if (set.subcategory !== undefined && t.subcategory !== set.subcategory) return true;
  if (set.needs_review !== undefined && t.needs_review !== set.needs_review) return true;
  if (set.tags && set.tags.some((tag) => !t.tags.includes(tag))) return true;
  return false;
}

function applySet(t: Transaction, set: AnswerSet): Transaction {
  return {
    ...t,
    category: set.category !== undefined ? set.category : t.category,
    subcategory: set.subcategory !== undefined ? set.subcategory : t.subcategory,
    // the overlay IS the answer being previewed — but only a set that names a category is a category answer
    // (etl apply_set stamps provenance under the same condition; a tags-only set leaves it alone)
    category_source: set.category !== undefined || set.subcategory !== undefined ? "answer" : t.category_source,
    needs_review: set.needs_review !== undefined ? set.needs_review : t.needs_review,
    tags: set.tags ? Array.from(new Set([...t.tags, ...set.tags])).sort() : t.tags,
  };
}

interface AnswerIndex { byTxn: Map<string, Answer>; byVendor: Map<string, Answer> }
const answerIndexes = new WeakMap<AnswersDoc, AnswerIndex>();
/** a doc's answers by txn id and by lower-cased vendor (built once per doc: docs are never mutated) */
function indexAnswers(doc: AnswersDoc): AnswerIndex {
  let ix = answerIndexes.get(doc);
  if (!ix) {
    ix = { byTxn: new Map(), byVendor: new Map() };
    for (const a of doc.answers) {
      if (a.scope === "txn") ix.byTxn.set(a.txn_id!, a);
      else ix.byVendor.set(a.vendor!.toLowerCase(), a);
    }
    answerIndexes.set(doc, ix);
  }
  return ix;
}

/** The txn and the vendor answer that reach row `t`, mirroring the ETL's answerable guard (etl/enrich/answers.py):
 *  internal money movement is never answerable, and vendor answers only repaint spending - a vendor whose name
 *  also appears on transfers or income must not leak. A vendor answer never reaches the PARTS of a split
 *  purchase (mirroring etl answers._answerable): "always Costco -> Groceries" must not repaint the Shopping
 *  leg - the split rule decides the legs; a part takes a txn answer like any row. A PARENT is not excluded here:
 *  the ETL lets both scopes hit it (display-only, logged as answers.on_split_parent), so the overlay does too;
 *  loadAppData's partition keeps parents out of the overlay's input. */
function answersFor(t: Transaction, ix: AnswerIndex): { txn?: Answer; vendor?: Answer } {
  if (t.is_internal || t.type === "transfer" || t.type === "cc-payment" || t.type === "investment") return {};
  return {
    txn: ix.byTxn.get(t.id),
    vendor: (t.type === "purchase" || t.type === "fee") && t.vendor && t.parent_id === null
      ? ix.byVendor.get(t.vendor.toLowerCase()) : undefined,
  };
}

/** the answer that carries the row's category - the one a config override can outrank, so the one the overlay
 *  marks conflicted: the txn answer when it names a category, else the vendor answer when it does (a tags-only
 *  txn answer leaves the vendor answer's category standing beneath it), else whichever there is */
function ownerOf(txn: Answer | undefined, vendor: Answer | undefined): Answer | undefined {
  return (txn?.set.category !== undefined ? txn : undefined)
    ?? (vendor?.set.category !== undefined ? vendor : undefined) ?? txn ?? vendor;
}

/** The key of the answer that owns row `t` in the overlay (applyAnswers): the key `conflictedKeys` holds when a
 *  config override beat it. Review files a row under Conflicts by this, so the two can never disagree. */
export function ownerKey(t: Transaction, doc: AnswersDoc): string | undefined {
  const { txn, vendor } = answersFor(t, indexAnswers(doc));
  return ownerOf(txn, vendor)?.key;
}

/** The one set a row ends under when it has a vendor answer and/or a txn answer: the ETL applies vendor
 *  scope first, then txn scope (etl/enrich/answers.py), each key overriding the last and tags unioning.
 *  A tags-only txn answer therefore leaves the vendor answer's category standing beneath it. */
function foldSets(vendor: AnswerSet | undefined, txn: AnswerSet | undefined): AnswerSet {
  if (!vendor) return txn!;
  if (!txn) return vendor;
  const tags = vendor.tags || txn.tags ? Array.from(new Set([...(vendor.tags ?? []), ...(txn.tags ?? [])])) : undefined;
  return { ...vendor, ...txn, ...(tags ? { tags } : {}) };
}

/**
 * Overlay answers onto exported transactions.
 *
 * Per (answer, row): if the export already carries the values → absorbed
 * (no-op). Otherwise, if the export is NEWER than the answer, the ETL saw the
 * answer and something (a curated overrides.yaml entry) outranked it →
 * conflicted, the overlay steps aside so views never diverge from ETL
 * aggregates indefinitely. Only a category disagreement counts as a conflict —
 * a keyword rule refining the subcategory is not a fight worth surfacing.
 * Otherwise → pending: apply and mark, ingest will make it durable.
 *
 * Absorbed and expired answers are NOT pruned client-side: the server's
 * merge-by-key union would resurrect any removal on the next echo, so the doc
 * is bounded by the store's MAX_ANSWERS cap instead (capAnswers). An evicted
 * answer stops applying at the next ingest - the ETL rebuilds from the stored
 * answers every run - so the cap drops only the oldest one-off answers and
 * never a vendor-scope one.
 */
export function applyAnswers(txns: Transaction[], doc: AnswersDoc,
                             manifestGeneratedAt: string): OverlayResult {
  const pendingIds = new Set<string>();
  const conflictedKeys = new Set<string>();
  if (doc.answers.length === 0) {
    return { txns, pendingIds, conflictedKeys };
  }

  const ix = indexAnswers(doc);
  // Compared as INSTANTS, not as strings. `generated_at` is a local wall clock
  // with an offset and `answered_at` is UTC with a Z, so a lexical compare read
  // "11:58 Central" as earlier than "16:58Z" — the same moment — and every
  // answer looked unseen by the ingest for the length of that offset.
  // NaN (a malformed timestamp) makes every comparison below false, which lands
  // on `pending`: the overlay applies, which is the safe direction.
  const ingestedAt = Date.parse(manifestGeneratedAt);

  let changed = false;
  const out = txns.map((t) => {
    const { txn: txnAnswer, vendor: vendorAnswer } = answersFor(t, ix);
    if (!txnAnswer && !vendorAnswer) return t;
    const set = foldSets(vendorAnswer?.set, txnAnswer?.set);
    if (!wouldChange(t, set)) return t;          // absorbed — exact no-op
    const owner = ownerOf(txnAnswer, vendorAnswer)!;
    const ingestSawIt = ingestedAt > Date.parse(owner.answered_at);
    if (ingestSawIt && set.category !== undefined && t.category !== set.category) {
      conflictedKeys.add(owner.key);    // a config override won — the category steps aside, but a tag added since
      const tagsOnly: AnswerSet = { tags: set.tags };    // the ingest (an override cannot undo a tag) still shows
      if (!set.tags || !wouldChange(t, tagsOnly)) return t;
      pendingIds.add(t.id);
      changed = true;
      return applySet(t, tagsOnly);
    }
    pendingIds.add(t.id);
    changed = true;
    return applySet(t, set);
  });

  return { txns: changed ? out : txns, pendingIds, conflictedKeys };
}

// ------------------------------------------------------------ inbox rules

/** Only these types are category questions — flagged transfer legs (e.g. a
 *  prepaid card's) are data-gap notes, not something a category answer fixes. */
const REVIEW_TYPES = new Set(["purchase", "fee", "income"]);

export function isReviewCandidate(vocab: CategoryVocab, t: Transaction): boolean {
  if (!REVIEW_TYPES.has(t.type)) return false;
  if (t.needs_review) return true;
  return vocab.isUncategorizedPile(t);
}

/** The spend still in the uncategorized pile (Cash & Misc rows no rule placed, whether flagged or not): how many
 *  purchases / fees and their gross outflow - what Data's open-questions line says. */
export function uncategorizedSpend(vocab: CategoryVocab, txns: readonly Transaction[]): { count: number; total: number } {
  const rows = txns.filter((t) => vocab.isUncategorizedPile(t)
    && (t.type === "purchase" || t.type === "fee") && t.amount < 0);
  return { count: rows.length, total: rows.reduce((s, t) => s - t.amount, 0) };
}

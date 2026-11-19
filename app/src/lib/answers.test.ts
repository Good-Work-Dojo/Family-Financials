// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { CategoryRename, Transaction } from "../data/types";
import { txFactory, vocabAssumptions } from "./__fixtures__";
import { categoryVocab } from "./categories";
import { sourceChip } from "./categorySource";
import {
  type Answer, applyAnswers, capAnswers, emptyAnswers, isReviewCandidate, makeTxnAnswer, makeVendorAnswer, MAX_ANSWERS, mergeAnswer,
  normalizeAnswers, ownerKey, removeAnswer, renameAnswersDoc, restoreAnswer, uncategorizedSpend, upsertAnswer, upsertAnswers, type AnswerSet,
} from "./answers";

const tx = txFactory({
  date: "2026-06-15", account: "demo_checking_0101", account_name: "Example Bank",
  description: "VENMO PAYMENT", vendor: null, category: "Cash & Misc",
  subcategory: "Uncategorized", amount: -50, needs_review: true,
  category_source: "fallback", source_file: "x.csv",
});

// The two sides carry DIFFERENT shapes in production and the tests have to use
// them, not a convenient matching pair: `answered_at` is new Date().toISOString()
// (UTC, Z), while `manifest.generated_at` is the ETL host's local wall clock
// carrying its offset. Writing both as UTC here is what let a lexical comparison
// look correct in tests while being 5-6 hours wrong on the server.
const NOW = "2026-07-16T15:00:00Z";                 // 10:00 Central
const OLD_MANIFEST = "2026-07-16T08:00:00-05:00";   // export predates the answer
const NEW_MANIFEST = "2026-07-16T12:00:00-05:00";   // export newer than the answer

const dining = { category: "Dining", subcategory: "Restaurants" as string | null,
                 needs_review: false };

describe("overlay lifecycle", () => {
  it("is an exact identity when the answers doc is empty", () => {
    const txns = [tx({}), tx({})];
    const r = applyAnswers(txns, emptyAnswers(), NEW_MANIFEST);
    expect(r.txns).toBe(txns);                       // same reference — no churn
    expect(r.pendingIds.size).toBe(0);
  });

  it("pending: applies the answer before ingest and flags the row", () => {
    const t = tx({});
    const doc = upsertAnswer(emptyAnswers(), makeTxnAnswer(t.id, dining, NOW), NOW);
    const r = applyAnswers([t], doc, OLD_MANIFEST);
    expect(r.txns[0].category).toBe("Dining");
    expect(r.txns[0].subcategory).toBe("Restaurants");
    expect(r.txns[0].needs_review).toBe(false);
    expect(r.pendingIds.has(t.id)).toBe(true);
  });

  it("stamps category_source=answer on rows the overlay changes, and only those", () => {
    const t = tx({});
    const other = tx({});
    const doc = upsertAnswer(emptyAnswers(), makeTxnAnswer(t.id, dining, NOW), NOW);
    const r = applyAnswers([t, other], doc, OLD_MANIFEST);
    expect(r.txns[0].category_source).toBe("answer");
    expect(r.txns[1]).toBe(other);                   // untouched row keeps its ETL provenance
    expect(r.txns[1].category_source).toBe("fallback");
  });

  // A row the AI step filed leaves the uncategorized pile with its review flag cleared; the family answers it from the
  // register's editor. The answer is theirs at once: its category, a pending answer, and no AI chip.
  it("a row filed by AI and answered shows the answer's category, pending, and its AI chip is gone", () => {
    const filed = { category: "Shopping", subcategory: null, needs_review: false, category_source: "ai" as const,
      vendor: "Tallgrass Outfitters" };
    expect(sourceChip(tx(filed).category_source)?.label, "the control: an AI row carries the chip").toBe("AI");
    for (const scope of ["txn", "vendor"] as const) {
      const t = tx(filed);
      const answer = scope === "txn" ? makeTxnAnswer(t.id, dining, NOW) : makeVendorAnswer("Tallgrass Outfitters", dining, NOW);
      const r = applyAnswers([t], upsertAnswer(emptyAnswers(), answer, NOW), OLD_MANIFEST);
      expect([r.txns[0].category, r.txns[0].subcategory], scope).toEqual(["Dining", "Restaurants"]);
      expect(r.txns[0].category_source, scope).toBe("answer");
      expect(r.pendingIds.has(t.id), `${scope}: a pending answer`).toBe(true);
      expect(sourceChip(r.txns[0].category_source), `${scope}: no AI chip`).toBeNull();
    }
  });

  it("absorbed: exact no-op once the export carries the values", () => {
    const t = tx({ category: "Dining", subcategory: "Restaurants", needs_review: false });
    const doc = upsertAnswer(emptyAnswers(), makeTxnAnswer(t.id, dining, NOW), NOW);
    const r = applyAnswers([t], doc, NEW_MANIFEST);
    expect(r.txns).toBe(r.txns);                     // and structurally untouched:
    expect(r.txns[0]).toBe(t);                       // same object reference
    expect(r.pendingIds.size).toBe(0);
    expect(r.conflictedKeys.size).toBe(0);
  });

  it("conflicted: a config override won at ingest — overlay steps aside", () => {
    const t = tx({ category: "Internal", subcategory: "transfer" });
    const doc = upsertAnswer(emptyAnswers(), makeTxnAnswer(t.id, dining, NOW), NOW);
    const r = applyAnswers([t], doc, NEW_MANIFEST);
    expect(r.txns[0].category).toBe("Internal");     // NOT overlaid
    expect(r.conflictedKeys.has(`txn:${t.id}`)).toBe(true);
  });

  it("expired: an answer whose uid vanished simply stops matching", () => {
    const doc = upsertAnswer(emptyAnswers(), makeTxnAnswer("tx_gone", dining, NOW), NOW);
    const t = tx({});
    const r = applyAnswers([t], doc, NEW_MANIFEST);
    expect(r.txns[0]).toBe(t);
    expect(r.pendingIds.size + r.conflictedKeys.size).toBe(0);
  });

  it("vendor answers apply case-insensitively and txn scope beats vendor scope", () => {
    const a = tx({ vendor: "Maple Street Bakery" });
    const b = tx({ vendor: "maple street bakery" });
    let doc = upsertAnswer(emptyAnswers(),
      makeVendorAnswer("Maple Street Bakery", { category: "Groceries", subcategory: null }, NOW), NOW);
    doc = upsertAnswer(doc, makeTxnAnswer(a.id, dining, NOW), NOW);
    const r = applyAnswers([a, b], doc, OLD_MANIFEST);
    expect(r.txns[0].category).toBe("Dining");       // txn answer wins on a
    expect(r.txns[1].category).toBe("Groceries");    // vendor answer covers b
  });

});

describe("document ops", () => {
  it("upsert replaces by key and stamps updated_at", () => {
    let doc = upsertAnswer(emptyAnswers(), makeTxnAnswer("tx_1", dining, NOW), NOW);
    doc = upsertAnswer(doc,
      makeTxnAnswer("tx_1", { category: "Travel", subcategory: null }, "2026-07-16T11:00:00Z"),
      "2026-07-16T11:00:00Z");
    expect(doc.answers).toHaveLength(1);
    expect(doc.answers[0].set.category).toBe("Travel");
    expect(doc.updated_at).toBe("2026-07-16T11:00:00Z");
  });

  it("removeAnswer drops by key (single-level undo)", () => {
    let doc = upsertAnswer(emptyAnswers(), makeTxnAnswer("tx_1", dining, NOW), NOW);
    doc = upsertAnswer(doc, makeVendorAnswer("V", dining, NOW), NOW);
    const next = removeAnswer(doc, "txn:tx_1", NOW);
    expect(next.answers.map((a) => a.key)).toEqual(["vendor:V"]);
  });

  it("normalize drops malformed entries and survives garbage", () => {
    expect(normalizeAnswers(null)).toEqual(emptyAnswers());
    expect(normalizeAnswers("junk")).toEqual(emptyAnswers());
    const doc = normalizeAnswers({
      version: 1, updated_at: NOW,
      answers: [
        makeTxnAnswer("tx_1", dining, NOW),
        { key: "vendor:x", scope: "vendor" },        // no set/vendor — dropped
        { key: 3 },                                   // garbage — dropped
      ],
    });
    expect(doc.answers).toHaveLength(1);
  });
});

describe("ingest-vs-answer ordering", () => {
  const t = tx({ category: "Internal", subcategory: "transfer" });
  const doc = upsertAnswer(emptyAnswers(), makeTxnAnswer(t.id, dining, NOW), NOW);

  it("compares instants, not strings, across the two timestamp shapes", () => {
    // 11:00 Central == 16:00Z, an hour AFTER the 15:00Z answer, so the ingest
    // saw it. Lexically "2026-07-16T11:00:00-05:00" < "2026-07-16T15:00:00Z",
    // which is what the old string compare got wrong.
    const r = applyAnswers([t], doc, "2026-07-16T11:00:00-05:00");
    expect(r.conflictedKeys.has(`txn:${t.id}`)).toBe(true);
    expect(r.pendingIds.size).toBe(0);
  });

  it("still treats a genuinely older export as pending", () => {
    // 09:00 Central == 14:00Z, an hour BEFORE the answer
    const r = applyAnswers([t], doc, "2026-07-16T09:00:00-05:00");
    expect(r.pendingIds.has(t.id)).toBe(true);
    expect(r.conflictedKeys.size).toBe(0);
  });

  it("falls back to pending when the timestamp is unparseable", () => {
    const r = applyAnswers([t], doc, "not a timestamp");
    expect(r.pendingIds.has(t.id)).toBe(true);
  });
});

describe("renameAnswersDoc", () => {
  const renames: CategoryRename[] = [{ from: { category: "Eating Out" }, to: { category: "Dining" } }];
  it("maps every answer's set like the ETL does, leaving the input doc untouched", () => {
    const doc = upsertAnswer(emptyAnswers(), makeTxnAnswer("t1", { category: "Eating Out", subcategory: "Coffee" }, NOW), NOW);
    const out = renameAnswersDoc(doc, renames);
    expect(out.answers[0].set).toEqual({ category: "Dining", subcategory: "Coffee" });
    expect(doc.answers[0].set.category).toBe("Eating Out");
  });
  it("renames vendor answers too, and a pair rename moves only that pair", () => {
    const pair: CategoryRename[] = [{ from: { category: "Dining", subcategory: "Pubs" },
                                      to: { category: "Dining", subcategory: "Bars" } }];
    let doc = upsertAnswer(emptyAnswers(), makeVendorAnswer("Pub Co", { category: "Dining", subcategory: "Pubs" }, NOW), NOW);
    doc = upsertAnswer(doc, makeTxnAnswer("t2", { category: "Dining", subcategory: "Coffee" }, NOW), NOW);
    const out = renameAnswersDoc(doc, pair);
    expect(out.answers.map((a) => a.set)).toEqual([
      { category: "Dining", subcategory: "Bars" }, { category: "Dining", subcategory: "Coffee" }]);
    expect(out.answers[1]).toBe(doc.answers[1]);     // an untouched answer keeps its identity
  });
  it("leaves a set with no subcategory key without one, as etl apply_to_set does", () => {
    const bare: AnswerSet = { category: "Eating Out" };
    const doc = upsertAnswer(emptyAnswers(), makeTxnAnswer("t3", bare, NOW), NOW);
    const out = renameAnswersDoc(doc, renames);
    expect(out.answers[0].set).toEqual({ category: "Dining" });
    expect("subcategory" in out.answers[0].set).toBe(false);
  });
  it("is the same instance when there is nothing to rename (the identity-preserving overlay memo)", () => {
    const doc = upsertAnswer(emptyAnswers(), makeTxnAnswer("t1", dining, NOW), NOW);
    expect(renameAnswersDoc(doc, [])).toBe(doc);
    expect(renameAnswersDoc(doc, renames)).toBe(doc);
  });
  it("a renamed answer is absorbed by a row the ETL already renamed, not painted pending or conflicted", () => {
    const t = tx({ category: "Dining", subcategory: "Coffee", needs_review: false });
    const doc = upsertAnswer(emptyAnswers(),
      makeTxnAnswer(t.id, { category: "Eating Out", subcategory: "Coffee", needs_review: false }, NOW), NOW);
    // without the rename the stored answer disagrees with the export and the (newer) ingest "saw" it: conflicted
    expect(applyAnswers([t], doc, NEW_MANIFEST).conflictedKeys.size).toBe(1);
    const r = applyAnswers([t], renameAnswersDoc(doc, renames), NEW_MANIFEST);
    expect(r.txns).toEqual([t]);
    expect(r.pendingIds.size).toBe(0);
    expect(r.conflictedKeys.size).toBe(0);
  });
});

// The answers cap, pinned against deploy/scenario_store.py with ONE shared fixture (tests/test_scenario_store.py
// runs the same cases): only one-off (txn) answers count toward it and only they go, oldest first; a vendor-scope
// answer is never dropped.
const EVICTION = JSON.parse(readFileSync(join(__dirname, "answers.eviction.fixture.json"), "utf8")) as {
  cap_cases: { name: string; limit: number; answers: Answer[]; kept: string[] }[];
  bulk_cases: { name: string; limit: number; vendor: number; txn: number; evicted: string[] }[];
  full_store: { stored: Answer[]; incoming: Answer[]; evicted: string[] };
};
/** the fixture's bulk_about rule (tests/test_scenario_store.py bulk_answers is the same): vendor answers first, then
 *  one-off ones, the n-th answer built stamped 2025-01-01T00:00:00.000Z + n seconds */
function bulkAnswers(nVendor: number, nTxn: number): Answer[] {
  const two = (n: number) => String(n).padStart(2, "0");
  const stamp = (n: number) => `2025-01-01T${two(Math.floor(n / 3600))}:${two(Math.floor(n / 60) % 60)}:${two(n % 60)}.000Z`;
  const id = (i: number) => String(i).padStart(4, "0");
  return [
    ...Array.from({ length: nVendor }, (_, i): Answer => ({
      key: `vendor:Synthetic Vendor ${id(i)}`, scope: "vendor", vendor: `Synthetic Vendor ${id(i)}`, set: { tags: ["x"] }, answered_at: stamp(i) })),
    ...Array.from({ length: nTxn }, (_, i): Answer => ({
      key: `txn:t${id(i)}`, scope: "txn", txn_id: `t${id(i)}`, set: { tags: ["x"] }, answered_at: stamp(nVendor + i) })),
  ];
}
describe("the answers cap (capAnswers), the store's rule exactly", () => {
  it.each(EVICTION.cap_cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const kept = capAnswers(c.answers, c.limit).map((a) => a.key);
    expect([...kept].sort()).toEqual([...c.kept].sort());
    expect(kept, "in their own order").toEqual(c.answers.map((a) => a.key).filter((k) => c.kept.includes(k)));
  });
  it.each(EVICTION.bulk_cases.map((c) => [c.name, c] as const))("at its real size: %s", (_name, c) => {
    expect(c.limit).toBe(MAX_ANSWERS);
    const answers = bulkAnswers(c.vendor, c.txn);
    const kept = new Set(capAnswers(answers).map((a) => a.key));
    expect(answers.map((a) => a.key).filter((k) => !kept.has(k))).toEqual(c.evicted);
    expect([...kept].filter((k) => k.startsWith("vendor:"))).toHaveLength(c.vendor);
    // the write path: one more one-off answer on top drops exactly one more (or none while under the cap)
    const out = upsertAnswers({ version: 1, updated_at: NOW, answers }, [makeTxnAnswer("arrives", dining, NOW)], NOW);
    expect(out.answers.filter((a) => a.scope === "vendor")).toHaveLength(c.vendor);
    expect(out.answers.filter((a) => a.scope === "txn")).toHaveLength(Math.min(c.txn + 1, MAX_ANSWERS));
  });
  it("a full store (its oldest answer is vendor-scope) keeps every vendor answer when a one-off answer arrives", () => {
    const { stored, incoming, evicted } = EVICTION.full_store;
    expect(stored.filter((a) => a.scope === "txn")).toHaveLength(MAX_ANSWERS);
    expect([...stored].sort((a, b) => (a.answered_at < b.answered_at ? -1 : 1))[0].scope).toBe("vendor");
    const out = upsertAnswers({ version: 1, updated_at: NOW, answers: stored }, incoming, NOW);
    expect(out.answers).toHaveLength(stored.length);
    const left = new Set(out.answers.map((a) => a.key));
    expect([...stored, ...incoming].map((a) => a.key).filter((k) => !left.has(k))).toEqual(evicted);
    expect(out.answers.filter((a) => a.scope === "vendor")).toHaveLength(stored.filter((a) => a.scope === "vendor").length);
  });
  it("a vendor-scope answer added to a full store drops nothing", () => {
    const { stored } = EVICTION.full_store;
    const out = upsertAnswers({ version: 1, updated_at: NOW, answers: stored }, [makeVendorAnswer("Synthetic New Vendor", dining, NOW)], NOW);
    expect(out.answers).toHaveLength(stored.length + 1);
  });
});

describe("ownerKey: the answer that owns a row, by the overlay's own precedence", () => {
  const row = tx({ id: "r1", type: "purchase", vendor: "Synthetic Mart", category: "Groceries", subcategory: null, needs_review: false });
  const doc = (...answers: ReturnType<typeof makeTxnAnswer>[]) => ({ version: 1 as const, updated_at: NOW, answers });
  const vendorCat = makeVendorAnswer("Synthetic Mart", dining, NOW);
  it("a tags-only txn answer leaves the vendor answer that carries the category as the owner", () => {
    expect(ownerKey(row, doc(makeTxnAnswer("r1", { tags: ["kids"] }, NOW), vendorCat))).toBe("vendor:Synthetic Mart");
  });
  it("a txn answer that names a category owns the row over the vendor answer", () => {
    expect(ownerKey(row, doc(makeTxnAnswer("r1", dining, NOW), vendorCat))).toBe("txn:r1");
  });
  it("with no category anywhere, whichever answer there is", () => {
    expect(ownerKey(row, doc(makeTxnAnswer("r1", { tags: ["kids"] }, NOW)))).toBe("txn:r1");
    expect(ownerKey(row, doc(makeVendorAnswer("synthetic mart", { tags: ["kids"] }, NOW))), "vendor match is case-blind").toBe("vendor:synthetic mart");
    expect(ownerKey(row, doc())).toBeUndefined();
  });
  it("a vendor answer never owns an income row, a split part or internal money movement (the overlay never applies it there)", () => {
    expect(ownerKey({ ...row, type: "income" }, doc(vendorCat))).toBeUndefined();
    expect(ownerKey({ ...row, parent_id: "p1" }, doc(vendorCat))).toBeUndefined();
    expect(ownerKey({ ...row, type: "transfer" }, doc(makeTxnAnswer("r1", dining, NOW), vendorCat))).toBeUndefined();
  });
  it("is the key applyAnswers marks conflicted when an override beat the vendor answer under a tags-only txn answer", () => {
    const d = doc(makeTxnAnswer("r1", { tags: ["kids"] }, NOW), vendorCat);
    const r = applyAnswers([row], d, NEW_MANIFEST);          // the ingest saw the vendor answer; the row kept Groceries
    expect([...r.conflictedKeys]).toEqual([ownerKey(row, d)]);
  });
});

describe("upsertAnswers (bulk)", () => {
  // minute-major then second: timestamp i sorts after timestamp i-1, so "oldest" means "lowest i"
  const stamp = (i: number) => `2026-01-01T00:${String(Math.floor(i / 60)).padStart(2, "0")}:${String(i % 60).padStart(2, "0")}Z`;
  it("applies every answer in one pass and keeps the newest MAX_ANSWERS, oldest first out", () => {
    let doc = emptyAnswers();
    for (let i = 0; i < MAX_ANSWERS - 1; i++) doc = upsertAnswer(doc, makeTxnAnswer(`old${i}`, dining, stamp(i)), NOW);
    const bulk = ["b1", "b2", "b3"].map((id) => makeTxnAnswer(id, dining, NOW));
    const out = upsertAnswers(doc, bulk, NOW);
    expect(out.answers.length).toBe(MAX_ANSWERS);
    expect(out.answers.filter((a) => a.key.startsWith("txn:b")).length).toBe(3);   // the new ones all survive
    expect(out.answers.some((a) => a.key === "txn:old0")).toBe(false);              // the two oldest went
    expect(out.answers.some((a) => a.key === "txn:old1")).toBe(false);
    expect(out.answers.some((a) => a.key === "txn:old2")).toBe(true);               // and only those
    expect(out.updated_at).toBe(NOW);
  });
  it("a re-answered row keeps ONE entry and its newest category; it merges, so earlier tags and the note survive", () => {
    const first = upsertAnswer(emptyAnswers(), makeTxnAnswer("t1", { category: "Dining", subcategory: null, tags: ["a"] }, stamp(1), "first"), stamp(1));
    const out = upsertAnswers(first, [makeTxnAnswer("t1", { category: "Travel", subcategory: null }, NOW),
      makeTxnAnswer("t2", dining, NOW)], NOW);
    expect(out.answers.map((a) => a.key).sort()).toEqual(["txn:t1", "txn:t2"]);
    const t1 = out.answers.find((a) => a.key === "txn:t1")!;
    expect(t1.set).toStrictEqual({ category: "Travel", subcategory: null, tags: ["a"] });
    expect([t1.note, t1.answered_at]).toEqual(["first", NOW]);
  });
  it("an empty batch changes nothing but still returns a valid doc", () => {
    const doc = upsertAnswer(emptyAnswers(), makeTxnAnswer("t1", dining, NOW), NOW);
    expect(upsertAnswers(doc, [], NOW).answers).toEqual(doc.answers);
  });
});

describe("tags-only answers: { tags } and nothing else", () => {
  const tagged = (over = {}) => tx({ category: "Dining", subcategory: "Coffee", tags: ["a"], needs_review: true,
                                     category_source: "keyword", ...over });
  it("normalize keeps a set with no category, and still refuses sets that are empty or mistyped", () => {
    const ok = makeTxnAnswer("t1", { tags: ["kids"] }, NOW);
    const bad = (set: unknown) => ({ ...makeTxnAnswer("t2", dining, NOW), set });
    const doc = normalizeAnswers({ version: 1, updated_at: NOW, answers: [ok,
      bad({}), bad({ tags: "kids" }), bad({ tags: [1] }), bad({ category: 5 }), bad({ category: "Dining", subcategory: 3 }),
      bad({ needs_review: "no" }), bad(null)] });
    expect(doc.answers.map((a) => a.set)).toEqual([{ tags: ["kids"] }]);
  });
  it("changes the row only through the tag union: category, subcategory, provenance and review stay its own", () => {
    const tagDoc = (row: { id: string }) => upsertAnswers(emptyAnswers(), [makeTxnAnswer(row.id, { tags: ["kids"] }, NOW)], NOW);
    const t = tagged();
    const r = applyAnswers([t], tagDoc(t), OLD_MANIFEST);
    expect(r.txns[0]).toEqual({ ...t, tags: ["a", "kids"] });          // every other field identical, incl. category_source "keyword"
    expect(r.pendingIds.has(t.id)).toBe(true);
    // already carrying the tag: absorbed, same instance (the answer is FOR this row, so only absorption can leave it alone)
    const has = tagged({ tags: ["a", "kids"] });
    const r2 = applyAnswers([has], tagDoc(has), OLD_MANIFEST);
    expect(r2.txns[0]).toBe(has);
    expect(r2.pendingIds.size).toBe(0);
  });
  it("applies to a row with no category (nothing to categorize first) and leaves it uncategorized", () => {
    const t = tagged({ category: null, subcategory: null });
    const doc = upsertAnswers(emptyAnswers(), [makeTxnAnswer(t.id, { tags: ["kids"] }, NOW)], NOW);
    const r = applyAnswers([t], doc, OLD_MANIFEST);
    expect(r.txns[0].category).toBeNull();
    expect(r.txns[0].tags).toEqual(["a", "kids"]);
    expect(r.txns[0].category_source).toBe("keyword");
  });
  it("is never a conflict: only a category answer can be outranked by a config override", () => {
    const t = tagged({ category: "Internal", subcategory: "transfer" });         // an override moved the category
    const doc = upsertAnswers(emptyAnswers(), [makeTxnAnswer(t.id, { tags: ["kids"] }, NOW)], NOW);
    const r = applyAnswers([t], doc, NEW_MANIFEST);                              // the ingest already saw the answer
    expect(r.conflictedKeys.size).toBe(0);
    expect(r.txns[0].category).toBe("Internal");                                 // the category was never asked about
  });
  it("does not hide a vendor answer that is still pending: the row shows the vendor's category plus the tag", () => {
    // the ETL applies vendor scope, then txn scope; a tags-only txn answer names no category, so the vendor
    // answer's category lands beneath it (the overlay used to take the txn answer ALONE and repaint the old one)
    const t = tagged({ vendor: "Maple Street Bakery", category: "Cash & Misc", subcategory: "Uncategorized" });
    let doc = upsertAnswer(emptyAnswers(),
      makeVendorAnswer("Maple Street Bakery", { category: "Dining", subcategory: "Restaurants", needs_review: false }, NOW), NOW);
    doc = upsertAnswer(doc, makeTxnAnswer(t.id, { tags: ["kids"] }, NOW), NOW);
    const r = applyAnswers([t], doc, OLD_MANIFEST);
    expect([r.txns[0].category, r.txns[0].subcategory, r.txns[0].needs_review, r.txns[0].tags])
      .toEqual(["Dining", "Restaurants", false, ["a", "kids"]]);
    expect(r.txns[0].category_source).toBe("answer");
    // once the ingest has seen both and an override won the category, the VENDOR answer is the conflicted one
    const o = { ...t, category: "Internal", subcategory: "transfer" };        // the same row, after an override moved it
    const rc = applyAnswers([o], doc, NEW_MANIFEST);
    expect([...rc.conflictedKeys]).toEqual(["vendor:Maple Street Bakery"]);
    // a txn CATEGORY answer still outranks the vendor one, and the vendor answer's tags still reach the row
    let both = upsertAnswer(emptyAnswers(), makeVendorAnswer("Maple Street Bakery",
      { category: "Dining", subcategory: "Restaurants", tags: ["trip"] }, NOW), NOW);
    both = upsertAnswer(both, makeTxnAnswer(t.id, { category: "Groceries", subcategory: null }, NOW), NOW);
    const rb = applyAnswers([t], both, OLD_MANIFEST);
    expect([rb.txns[0].category, rb.txns[0].tags]).toEqual(["Groceries", ["a", "trip"]]);
  });
  it("rename leaves a category-less set alone", () => {
    const set: AnswerSet = { tags: ["kids"] };
    const doc = upsertAnswer(emptyAnswers(), makeTxnAnswer("t1", set, NOW), NOW);
    expect(renameAnswersDoc(doc, [{ from: { category: "Dining" }, to: { category: "Eating Out" } }])).toBe(doc);
  });
});

describe("same-key answers MERGE, they never replace (a tags-only answer must not erase a category answer)", () => {
  const LATER = "2026-07-16T16:00:00Z";
  const kids = { category: "Kids", subcategory: "School" as string | null, needs_review: false };
  const twice = (a: ReturnType<typeof makeTxnAnswer>, b: ReturnType<typeof makeTxnAnswer>) =>
    upsertAnswer(upsertAnswer(emptyAnswers(), a, a.answered_at), b, b.answered_at);

  it("tags-only over a txn category answer keeps its category pair, review flag and note, and unions the tags", () => {
    const doc = twice(makeTxnAnswer("r1", { ...kids, tags: ["a"] }, NOW, "school fees"), makeTxnAnswer("r1", { tags: ["trip"] }, LATER));
    expect(doc.answers).toHaveLength(1);
    expect(doc.answers[0].set).toStrictEqual({ category: "Kids", subcategory: "School", needs_review: false, tags: ["a", "trip"] });
    expect(doc.answers[0].note).toBe("school fees");
    expect(doc.answers[0].answered_at, "one second past the stored stamp (see the next describe), not 'now'").toBe("2026-07-16T15:00:01Z");
    expect(doc.updated_at).toBe(LATER);
  });
  it("the same for a vendor answer", () => {
    const doc = twice(makeVendorAnswer("Maple Street Bakery", kids, NOW), makeVendorAnswer("Maple Street Bakery", { tags: ["trip"] }, LATER));
    expect(doc.answers).toHaveLength(1);
    expect(doc.answers[0].set).toStrictEqual({ ...kids, tags: ["trip"] });
    expect(doc.answers[0].scope).toBe("vendor");
  });
  it("a category answer over an earlier tags-only one keeps the tags (answers add tags, never remove them) - txn and vendor", () => {
    const txn = twice(makeTxnAnswer("r1", { tags: ["trip"] }, NOW), makeTxnAnswer("r1", kids, LATER));
    expect(txn.answers[0].set).toStrictEqual({ ...kids, tags: ["trip"] });
    const vendor = twice(makeVendorAnswer("V", { tags: ["trip"] }, NOW), makeVendorAnswer("V", { ...kids, tags: ["kids"] }, LATER));
    expect(vendor.answers[0].set).toStrictEqual({ ...kids, tags: ["kids", "trip"] });
  });
  it("a category answer over a category answer takes the new pair and review flag, keeps the old tags and a note it did not replace", () => {
    const doc = twice(makeTxnAnswer("r1", { category: "Dining", subcategory: "Coffee", needs_review: true, tags: ["x"] }, NOW, "note 1"),
      makeTxnAnswer("r1", { category: "Kids", subcategory: null, needs_review: false }, LATER));
    expect(doc.answers[0].set).toStrictEqual({ category: "Kids", subcategory: null, needs_review: false, tags: ["x"] });
    expect(doc.answers[0].note).toBe("note 1");
    const renoted = twice(makeTxnAnswer("r1", dining, NOW, "note 1"), makeTxnAnswer("r1", dining, LATER, "note 2"));
    expect(renoted.answers[0].note).toBe("note 2");
  });
  it("never forms a stale pair: a bare category carries no subcategory from the answer it replaces", () => {
    const merged = mergeAnswer(makeTxnAnswer("r1", { category: "Dining", subcategory: "Coffee" }, NOW),
      makeTxnAnswer("r1", { category: "Kids" }, LATER));
    expect("subcategory" in merged.set).toBe(false);
    expect(merged.set.category).toBe("Kids");
  });
  it("does not touch the answers it merges (the doc and the prior answer are values)", () => {
    const prior = makeTxnAnswer("r1", { ...kids, tags: ["a"] }, NOW);
    const doc = upsertAnswer(emptyAnswers(), prior, NOW);
    upsertAnswer(doc, makeTxnAnswer("r1", { tags: ["b"] }, LATER), LATER);
    expect(doc.answers[0].set.tags).toEqual(["a"]);
    expect(prior.set.tags).toEqual(["a"]);
  });
  it("a merge takes no new slot: a full store keeps every answer when one of them is merged into", () => {
    let doc = emptyAnswers();
    for (let i = 0; i < MAX_ANSWERS; i++) doc = upsertAnswer(doc, makeTxnAnswer(`r${i}`, dining, `2026-01-01T00:${String(Math.floor(i / 60)).padStart(2, "0")}:${String(i % 60).padStart(2, "0")}Z`), NOW);
    expect(doc.answers).toHaveLength(MAX_ANSWERS);
    const out = upsertAnswers(doc, [makeTxnAnswer("r0", { tags: ["trip"] }, NOW)], NOW);
    expect(out.answers).toHaveLength(MAX_ANSWERS);
    expect(out.answers.find((a) => a.key === "txn:r0")!.set).toStrictEqual({ ...dining, tags: ["trip"] });   // the OLDEST answer survived
    expect(upsertAnswers(doc, [makeTxnAnswer("fresh", dining, NOW)], NOW).answers.some((a) => a.key === "txn:r0")).toBe(false);   // a new key does evict it
  });
  it("the bulk write merges key by key", () => {
    const doc = upsertAnswer(emptyAnswers(), makeTxnAnswer("r1", kids, NOW), NOW);
    const out = upsertAnswers(doc, ["r1", "r2"].map((id) => makeTxnAnswer(id, { tags: ["trip"] }, LATER)), LATER);
    expect(out.answers.map((a) => [a.key, a.set])).toEqual([
      ["txn:r1", { ...kids, tags: ["trip"] }], ["txn:r2", { tags: ["trip"] }]]);
  });
  it("the overlay then shows the family's category AND the tag, not just the tag", () => {
    const t = tx({ category: "Cash & Misc", subcategory: "Uncategorized", tags: ["a"], needs_review: true });
    const doc = twice(makeTxnAnswer(t.id, kids, NOW), makeTxnAnswer(t.id, { tags: ["trip"] }, LATER));
    const r = applyAnswers([t], doc, OLD_MANIFEST);
    expect([r.txns[0].category, r.txns[0].subcategory, r.txns[0].needs_review, r.txns[0].tags]).toEqual(["Kids", "School", false, ["a", "trip"]]);
  });
  it("restoreAnswer puts an earlier answer back (undo), re-stamped so it is the newest", () => {
    const prior = makeTxnAnswer("r1", { tags: ["trip"] }, NOW, "n");
    const merged = twice(prior, makeTxnAnswer("r1", kids, LATER));
    const back = restoreAnswer(merged, prior, "2026-07-16T17:00:00Z");
    expect(back.answers).toHaveLength(1);
    expect(back.answers[0]).toStrictEqual({ ...prior, answered_at: "2026-07-16T17:00:00Z" });
    expect(back.updated_at).toBe("2026-07-16T17:00:00Z");
  });
});

describe("the overlay's fold of a vendor answer and a txn answer", () => {
  const row = (over = {}) => tx({ vendor: "Maple Street Bakery", category: "Cash & Misc", subcategory: "Uncategorized", tags: ["a"], needs_review: true, ...over });
  it("unions the tags of BOTH answers (the ETL unions them too)", () => {
    const t = row();
    let doc = upsertAnswer(emptyAnswers(), makeVendorAnswer("Maple Street Bakery", { category: "Dining", subcategory: "Restaurants", tags: ["trip"] }, NOW), NOW);
    doc = upsertAnswer(doc, makeTxnAnswer(t.id, { category: "Groceries", subcategory: null, tags: ["kids"] }, NOW), NOW);
    const r = applyAnswers([t], doc, OLD_MANIFEST);
    expect(r.txns[0].category).toBe("Groceries");                         // the txn answer's category wins ...
    expect(r.txns[0].tags).toEqual(["a", "kids", "trip"]);                // ... and both answers' tags land
    let tagsOnly = upsertAnswer(emptyAnswers(), makeVendorAnswer("Maple Street Bakery", { tags: ["trip"] }, NOW), NOW);
    tagsOnly = upsertAnswer(tagsOnly, makeTxnAnswer(t.id, { tags: ["kids"] }, NOW), NOW);
    expect(applyAnswers([t], tagsOnly, OLD_MANIFEST).txns[0].tags).toEqual(["a", "kids", "trip"]);
  });
  it("a conflicted category answer still lets a newer tags-only answer show its tag (only the category steps aside)", () => {
    // an override beat the vendor answer's category at the ingest (conflict); the family tagged the row AFTER that ingest
    const o = row({ category: "Internal", subcategory: "transfer", tags: [] });
    let doc = upsertAnswer(emptyAnswers(), makeVendorAnswer("Maple Street Bakery", { category: "Dining", subcategory: "Restaurants", needs_review: false }, NOW), NOW);
    doc = upsertAnswer(doc, makeTxnAnswer(o.id, { tags: ["trip"] }, "2026-07-16T18:00:00Z"), "2026-07-16T18:00:00Z");
    const r = applyAnswers([o], doc, NEW_MANIFEST);                       // ingest at 17:00Z saw the vendor answer (15:00Z), not the tag (18:00Z)
    expect([...r.conflictedKeys]).toEqual(["vendor:Maple Street Bakery"]);
    expect(r.txns[0].category).toBe("Internal");                          // the category stayed the override's
    expect(r.txns[0].tags).toEqual(["trip"]);                             // the tag the family just added shows
    expect(r.pendingIds.has(o.id)).toBe(true);
    // with nothing pending beyond the conflict the row is left exactly alone
    const quiet = applyAnswers([o], upsertAnswer(emptyAnswers(), makeVendorAnswer("Maple Street Bakery", { category: "Dining", subcategory: null }, NOW), NOW), NEW_MANIFEST);
    expect(quiet.txns[0]).toBe(o);
    expect(quiet.pendingIds.size).toBe(0);
  });
});

describe("a tags-only merge keeps the stored category answer's place in time (it teaches nothing)", () => {
  // The learned rules read the newest answers by `answered_at`, an override conflict is judged by it, and the server
  // keeps the newer stamp per key comparing STRINGS. So a tags-only save must neither move the stored category answer
  // to "now" nor lose to the stored copy: it stamps prior + 1 ms, formatted like the prior stamp - and a stamp with no
  // milliseconds (legacy) goes up one SECOND, because "...T10:00:00Z" sorts above "...T10:00:00.001Z".
  // tests/test_scenario_store.py asserts the SAME pairs against deploy/scenario_store.py merge_answers.
  const BUMPS: [string, string][] = [
    ["2026-07-16T10:00:00Z", "2026-07-16T10:00:01Z"],
    ["2026-07-16T10:00:00.123Z", "2026-07-16T10:00:00.124Z"],
    ["2026-07-16T10:00:00.999Z", "2026-07-16T10:00:01.000Z"],
    ["2026-07-16T10:59:59Z", "2026-07-16T11:00:00Z"],
    ["2026-12-31T23:59:59.999Z", "2027-01-01T00:00:00.000Z"],
  ];
  const NOWISH = "2030-01-01T00:00:00.000Z";
  const kids = { category: "Kids", subcategory: "School" as string | null, needs_review: false };

  it("stamps the merged answer at prior + 1 ms, in the prior's own format, in every shape of stamp", () => {
    for (const [prior, bumped] of BUMPS) {
      const merged = mergeAnswer(makeTxnAnswer("r1", kids, prior), makeTxnAnswer("r1", { tags: ["trip"] }, NOWISH));
      expect(merged.answered_at, prior).toBe(bumped);
      expect(merged.answered_at > prior, `${bumped} sorts above ${prior} as a string, the way the server compares`).toBe(true);
      expect(merged.set).toStrictEqual({ ...kids, tags: ["trip"] });
    }
    const vendor = mergeAnswer(makeVendorAnswer("V", kids, BUMPS[0][0]), makeVendorAnswer("V", { tags: ["trip"] }, NOWISH));
    expect(vendor.answered_at, "vendor scope too").toBe(BUMPS[0][1]);
  });

  it("every other merge stamps with the incoming answer (a category save is a fresh act and does count)", () => {
    const stamped = (prior: Parameters<typeof mergeAnswer>[0], incoming: Parameters<typeof mergeAnswer>[1]) => mergeAnswer(prior, incoming).answered_at;
    const cat = (at: string) => makeTxnAnswer("r1", kids, at);
    const tags = (at: string) => makeTxnAnswer("r1", { tags: ["trip"] }, at);
    expect(stamped(cat("2026-07-16T10:00:00Z"), cat(NOWISH)), "category over category").toBe(NOWISH);
    expect(stamped(tags("2026-07-16T10:00:00Z"), cat(NOWISH)), "category over tags-only").toBe(NOWISH);
    expect(stamped(tags("2026-07-16T10:00:00Z"), tags(NOWISH)), "tags-only over tags-only (the stored one names no category)").toBe(NOWISH);
    expect(stamped(cat("not a stamp"), tags(NOWISH)), "a stored stamp it cannot bump falls back to the incoming one").toBe(NOWISH);
    expect(stamped(cat("2026-07-16T10:00:00.5Z"), tags(NOWISH)), "an odd millisecond width is not bumped either").toBe(NOWISH);
  });

  it("tagging the OLDEST answers leaves the order of the answers by stamp exactly as it was (the learned window)", () => {
    const stamps = ["2026-07-01T00:00:00Z", "2026-07-01T01:00:00Z", "2026-07-01T02:00:00Z", "2026-07-01T03:00:00Z", "2026-07-01T04:00:00Z"];
    let doc = emptyAnswers();
    stamps.forEach((at, i) => { doc = upsertAnswer(doc, makeTxnAnswer(`r${i}`, i < 2 ? kids : dining, at), at); });
    const order = (d: typeof doc) => [...d.answers].sort((a, b) => a.answered_at.localeCompare(b.answered_at)).map((a) => a.key);
    const before = order(doc);
    const out = upsertAnswers(doc, ["r0", "r1"].map((id) => makeTxnAnswer(id, { tags: ["trip"] }, "2026-07-20T00:00:00.000Z")), "2026-07-20T00:00:00.000Z");
    expect(order(out), "the two oldest are still the two oldest").toEqual(before);
    expect(out.answers.find((a) => a.key === "txn:r0")!.set.tags).toEqual(["trip"]);
  });

  it("a conflicted answer stays conflicted after a tags-only save, and the tag shows", () => {
    // the answer predates the ingest (15:00Z < 17:00Z) and a config override beat its category
    const t = tx({ category: "Internal", subcategory: "transfer", tags: [], needs_review: false });
    let doc = upsertAnswer(emptyAnswers(), makeTxnAnswer(t.id, kids, NOW), NOW);
    expect(applyAnswers([t], doc, NEW_MANIFEST).conflictedKeys.has(`txn:${t.id}`)).toBe(true);
    doc = upsertAnswer(doc, makeTxnAnswer(t.id, { tags: ["trip"] }, "2026-07-16T18:00:00.000Z"), "2026-07-16T18:00:00.000Z");
    const r = applyAnswers([t], doc, NEW_MANIFEST);
    expect([...r.conflictedKeys], "stamped 1s after the stored answer, still before the ingest").toEqual([`txn:${t.id}`]);
    expect(r.txns[0].category, "the override's category was not painted over").toBe("Internal");
    expect(r.txns[0].tags).toEqual(["trip"]);
  });
});

describe("split purchases: a vendor answer never reaches a part", () => {
  const part = (over: Partial<Transaction>) =>
    tx({ parent_id: "p1", category_source: "split", needs_review: false, vendor: "Synth Mart", ...over });
  const kids = { category: "Kids", subcategory: "Childcare" as string | null, needs_review: false };
  it("skips the parts, still repaints the vendor's ordinary rows, and marks only those pending", () => {
    const a = part({ category: "Groceries", subcategory: null, amount: -210 });
    const b = part({ category: "Shopping", subcategory: "Home Goods", amount: -90 });
    const plain = tx({ vendor: "Synth Mart", category: "Groceries", subcategory: null, needs_review: false, amount: -40 });
    const doc = upsertAnswer(emptyAnswers(), makeVendorAnswer("Synth Mart", kids, NOW), NOW);
    const r = applyAnswers([a, b, plain], doc, OLD_MANIFEST);
    expect(r.txns[0]).toBe(a);
    expect(r.txns[1]).toBe(b);
    expect(r.txns[2].category).toBe("Kids");
    expect([...r.pendingIds]).toEqual([plain.id]);
  });
  it("a txn answer on a part applies to that part only (a per-part correction), with the vendor answer still skipped", () => {
    const a = part({ category: "Groceries", subcategory: null });
    const b = part({ category: "Shopping", subcategory: "Home Goods" });
    let doc = upsertAnswer(emptyAnswers(), makeVendorAnswer("Synth Mart", kids, NOW), NOW);
    doc = upsertAnswer(doc, makeTxnAnswer(a.id, { category: "Pets", subcategory: null, needs_review: false }, NOW), NOW);
    const r = applyAnswers([a, b], doc, OLD_MANIFEST);
    expect(r.txns[0].category).toBe("Pets");
    expect(r.txns[0].category_source).toBe("answer");
    expect(r.txns[1]).toBe(b);
  });
  it("is the identity when every row of the vendor is a part", () => {
    const rows = [part({}), part({})];
    const doc = upsertAnswer(emptyAnswers(), makeVendorAnswer("Synth Mart", kids, NOW), NOW);
    expect(applyAnswers(rows, doc, OLD_MANIFEST).txns).toBe(rows);
  });
  it("keys the skip on parent_id, not on the provenance: a part a txn answer already repainted is still skipped", () => {
    const repainted = part({ category: "Pets", subcategory: null, category_source: "answer" });
    const doc = upsertAnswer(emptyAnswers(), makeVendorAnswer("Synth Mart", kids, NOW), NOW);
    const r = applyAnswers([repainted], doc, OLD_MANIFEST);
    expect(r.txns[0]).toBe(repainted);
    expect(r.pendingIds.size).toBe(0);
  });
  it("ETL parity: a parent that reaches the overlay takes both scopes (display-only), as answers.apply lets it", () => {
    // loadAppData's partition keeps parents out of this input, but for any caller that passes one
    // the overlay must agree with etl answers._answerable: a parent is answerable, the parts are the exception
    const parent = tx({ is_parent: true, vendor: "Synth Mart", category: "Groceries", subcategory: null, needs_review: false, amount: -300 });
    const byVendor = upsertAnswer(emptyAnswers(), makeVendorAnswer("Synth Mart", kids, NOW), NOW);
    const v = applyAnswers([parent], byVendor, OLD_MANIFEST);
    expect([v.txns[0].category, v.txns[0].category_source, v.txns[0].is_parent]).toEqual(["Kids", "answer", true]);
    expect([...v.pendingIds]).toEqual([parent.id]);
    const byTxn = upsertAnswer(emptyAnswers(), makeTxnAnswer(parent.id, { category: "Pets", subcategory: null, needs_review: false }, NOW), NOW);
    expect(applyAnswers([parent], byTxn, OLD_MANIFEST).txns[0].category).toBe("Pets");
  });
});

describe("the inbox rules read the uncategorized pile through the category renames", () => {
  const plain = categoryVocab(vocabAssumptions());
  const renamed = categoryVocab(vocabAssumptions([{ from: { category: "Cash & Misc" }, to: { category: "Misc" } }]));
  const pile = (category: string, subcategory: string, over: Partial<Transaction> = {}) =>
    tx({ category, subcategory, needs_review: false, ...over });
  it("an unflagged row in the pile is a review candidate under either spelling of Cash & Misc", () => {
    expect(isReviewCandidate(plain, pile("Cash & Misc", "Uncategorized"))).toBe(true);
    expect(isReviewCandidate(renamed, pile("Misc", "Uncategorized"))).toBe(true);
    expect(isReviewCandidate(renamed, pile("Misc", "Venmo/Zelle Unknown"))).toBe(true);
    expect(isReviewCandidate(renamed, pile("Cash & Misc", "Uncategorized"))).toBe(true);   // the ETL's fallback keeps its own spelling
  });
  it("what is not in the pile is not a candidate: another Misc subcategory, another category, a transfer", () => {
    expect(isReviewCandidate(renamed, pile("Misc", "Legal"))).toBe(false);
    expect(isReviewCandidate(renamed, pile("Dining", "Uncategorized"))).toBe(false);
    expect(isReviewCandidate(renamed, pile("Misc", "Uncategorized", { type: "transfer" }))).toBe(false);
    expect(isReviewCandidate(plain, pile("Misc", "Uncategorized"))).toBe(false);            // control: unrenamed, "Misc" is nothing special
  });
  it("a flagged row is a candidate whatever its category", () => {
    expect(isReviewCandidate(renamed, pile("Dining", "Restaurants", { needs_review: true }))).toBe(true);
  });
  it("uncategorizedSpend counts the pile's purchases and fees (outflows only) and their gross", () => {
    const rows = [pile("Misc", "Uncategorized", { amount: -50 }), pile("Misc", "Venmo/Zelle Unknown", { amount: -25, type: "fee" }),
      pile("Misc", "Uncategorized", { amount: 40 }), pile("Misc", "Legal", { amount: -999 }), pile("Dining", "Restaurants", { amount: -7 })];
    expect(uncategorizedSpend(renamed, rows)).toEqual({ count: 2, total: 75 });
    expect(uncategorizedSpend(plain, rows)).toEqual({ count: 0, total: 0 });                // control
  });
});

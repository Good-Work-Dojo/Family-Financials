// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { type AnswersDoc, emptyAnswers } from "../lib/answers";

// the transport is not under test: a stand-in that holds the doc and applies every commit the store makes
const sync = vi.hoisted(() => ({ doc: null as unknown as AnswersDoc, commit: vi.fn() }));
vi.mock("./useSyncedDoc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./useSyncedDoc")>()),
  useSyncedDoc: () => ({ loading: false, localOnly: false, saveState: "idle", doc: sync.doc, commit: sync.commit }),
}));
import { useAnswersStore } from "./useAnswersStore";

beforeEach(() => {
  sync.doc = emptyAnswers();
  sync.commit.mockReset();
  sync.commit.mockImplementation((next: AnswersDoc | ((cur: AnswersDoc) => AnswersDoc)) => {
    sync.doc = typeof next === "function" ? next(sync.doc) : next;
  });
});
const store = () => renderHook(() => useAnswersStore()).result;
const answerOf = (key: string) => sync.doc.answers.find((a) => a.key === key);
const KIDS = { category: "Kids", subcategory: "School", needs_review: false };

describe("useAnswersStore.answerTxns", () => {
  it("commits N rows ONCE (one synced write), one txn answer per row, all stamped alike", () => {
    const s = store();
    s.current.answerTxns(["a", "b", "c"], { category: "Kids", subcategory: null, needs_review: false }, "school run");
    expect(sync.commit).toHaveBeenCalledTimes(1);
    expect(sync.doc.answers.map((x) => x.key)).toEqual(["txn:a", "txn:b", "txn:c"]);
    expect(new Set(sync.doc.answers.map((x) => x.answered_at)).size, "one timestamp for the batch").toBe(1);
    expect(sync.doc.answers.every((x) => x.note === "school run" && x.set.category === "Kids")).toBe(true);
    expect(sync.doc.updated_at).toBe(sync.doc.answers[0].answered_at);
  });
  it("builds on the doc it is handed (a functional update, so it chains after earlier commits)", () => {
    const s = store();
    s.current.answerTxns(["a"], { category: "Dining", subcategory: null });
    s.current.answerTxns(["b"], { tags: ["kids"] });
    expect(sync.doc.answers.map((x) => x.key)).toEqual(["txn:a", "txn:b"]);
    expect(typeof sync.commit.mock.calls[0][0], "a functional update, never a captured doc").toBe("function");
  });
  it("a tags-only set goes out exactly as { tags } — no category key, no subcategory, no needs_review", () => {
    const s = store();
    s.current.answerTxns(["a", "b"], { tags: ["kids", "trip"] });
    for (const a of sync.doc.answers) expect(a.set).toStrictEqual({ tags: ["kids", "trip"] });
  });
  it("single-row writes still go through their own commit each", () => {
    const s = store();
    s.current.answerTxn("a", { category: "Dining", subcategory: null });
    s.current.answerVendor("V", { category: "Dining", subcategory: null });
    expect(sync.commit).toHaveBeenCalledTimes(2);
  });
});

describe("a new answer MERGES into the one already under its key, in the store's own commit", () => {
  it("txn scope: tags over a category answer keep the category (bulk and single-row writers alike)", () => {
    const s = store();
    s.current.answerTxns(["r1", "r2"], { ...KIDS, tags: ["a"] }, "fees");
    s.current.answerTxns(["r1", "r2", "r3"], { tags: ["trip"] });
    expect(answerOf("txn:r1")!.set).toStrictEqual({ ...KIDS, tags: ["a", "trip"] });
    expect(answerOf("txn:r1")!.note).toBe("fees");
    expect(answerOf("txn:r3")!.set, "a row with no earlier answer gets just the tag").toStrictEqual({ tags: ["trip"] });
    s.current.answerTxn("r2", { tags: ["more"] });
    expect(answerOf("txn:r2")!.set).toStrictEqual({ ...KIDS, tags: ["a", "more", "trip"] });
  });
  it("vendor scope: tags over a vendor category answer keep it", () => {
    const s = store();
    s.current.answerVendor("Maple Street Bakery", KIDS);
    s.current.answerVendor("Maple Street Bakery", { tags: ["trip"] });
    expect(answerOf("vendor:Maple Street Bakery")!.set).toStrictEqual({ ...KIDS, tags: ["trip"] });
    expect(sync.doc.answers).toHaveLength(1);
  });
  it("a category save over an earlier tags-only answer keeps its tags, txn and vendor scope", () => {
    const s = store();
    s.current.answerTxns(["r1"], { tags: ["trip"] });
    s.current.answerVendor("V", { tags: ["home"] });
    s.current.answerTxns(["r1"], KIDS);
    s.current.answerVendor("V", KIDS);
    expect(answerOf("txn:r1")!.set).toStrictEqual({ ...KIDS, tags: ["trip"] });
    expect(answerOf("vendor:V")!.set).toStrictEqual({ ...KIDS, tags: ["home"] });
  });
  it("a tags-only merge is stamped ONE tick after the category answer it merged into (not 'now': it must not teach the learned rules)", () => {
    const s = store();
    s.current.answerTxns(["r1"], KIDS);
    const before = answerOf("txn:r1")!.answered_at;
    vi.useFakeTimers({ now: new Date(Date.parse(before) + 5000) });
    try { s.current.answerTxns(["r1"], { tags: ["trip"] }); } finally { vi.useRealTimers(); }
    expect(answerOf("txn:r1")!.answered_at).toBe(new Date(Date.parse(before) + 1).toISOString());
    expect(answerOf("txn:r1")!.answered_at > before, "still newer than the stored copy as a string").toBe(true);
  });
  it("a category save over an answer is stamped now - it is a fresh answer and does count", () => {
    const s = store();
    s.current.answerTxns(["r1"], { tags: ["trip"] });
    const before = answerOf("txn:r1")!.answered_at;
    vi.useFakeTimers({ now: new Date(Date.parse(before) + 5000) });
    try { s.current.answerTxns(["r1"], KIDS); } finally { vi.useRealTimers(); }
    expect(answerOf("txn:r1")!.answered_at).toBe(new Date(Date.parse(before) + 5000).toISOString());
  });
});

describe("unanswer (Review's undo) puts back what the save replaced", () => {
  it("a save that merged into an earlier answer is undone to THAT answer, not to nothing", () => {
    const s = store();
    s.current.answerTxns(["r1"], { tags: ["trip"] }, "n");                  // the register's tag, earlier
    const earlier = answerOf("txn:r1")!;
    s.current.answerTxn("r1", KIDS);                                         // Review's category save merges into it
    expect(answerOf("txn:r1")!.set).toStrictEqual({ ...KIDS, tags: ["trip"] });
    s.current.unanswer("txn:r1");
    expect(sync.doc.answers).toHaveLength(1);
    expect(answerOf("txn:r1")!.set).toStrictEqual({ tags: ["trip"] });
    expect(answerOf("txn:r1")!.note).toBe("n");
    expect(answerOf("txn:r1")!.answered_at >= earlier.answered_at, "re-stamped, never older").toBe(true);
  });
  it("a vendor answer that merged into an earlier one is undone to the earlier one", () => {
    const s = store();
    s.current.answerVendor("V", KIDS);
    s.current.answerVendor("V", { tags: ["trip"] });
    s.current.unanswer("vendor:V");
    expect(answerOf("vendor:V")!.set).toStrictEqual(KIDS);
  });
  it("a brand-new answer is undone by dropping it (the old behaviour), and so is a key this session never saved", () => {
    const s = store();
    s.current.answerTxn("fresh", KIDS);
    s.current.unanswer("txn:fresh");
    expect(answerOf("txn:fresh")).toBeUndefined();
    sync.doc = { ...emptyAnswers(), answers: [{ key: "txn:old", scope: "txn", txn_id: "old", set: KIDS, answered_at: "2026-01-01T00:00:00Z" }] };
    s.current.unanswer("txn:old");
    expect(sync.doc.answers).toEqual([]);
  });
  it("is single-level: a second undo of the same key drops it", () => {
    const s = store();
    s.current.answerTxn("r1", { tags: ["trip"] });
    s.current.answerTxn("r1", KIDS);
    s.current.unanswer("txn:r1");
    expect(answerOf("txn:r1")!.set).toStrictEqual({ tags: ["trip"] });
    s.current.unanswer("txn:r1");
    expect(answerOf("txn:r1")).toBeUndefined();
  });
});

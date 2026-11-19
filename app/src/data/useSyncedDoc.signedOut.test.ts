// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { withSaved } from "../lib/prefs";
import { PREFS_SYNC } from "./usePrefsStore";
import { hasUnsentEdits, noteStoreStatus, resetSession, sessionState } from "./session";
import { EPOCH_ISO, RETRY_MAX_MS, useSyncedDoc } from "./useSyncedDoc";

/** A store call answered 401 means the sign-in has ended: the synced documents stop sending (no retry loop), keep every
 *  edit in memory and in this browser's copy, and send it once a store call succeeds again. */

interface Doc { updated_at: string; n: number }
const normalize = (raw: unknown): Doc => {
  const r = raw as Partial<Doc> | null;
  return { updated_at: typeof r?.updated_at === "string" ? r.updated_at : EPOCH_ISO, n: typeof r?.n === "number" ? r.n : 0 };
};
const empty = (): Doc => ({ updated_at: EPOCH_ISO, n: 0 });
const EDIT: Doc = { updated_at: "2026-07-16T10:01:00.000Z", n: 7 };
const LATER: Doc = { updated_at: "2026-07-16T10:02:00.000Z", n: 8 };

type Answer = number | (() => Response);
interface Call { method: string; body: unknown }
/** a store for ./api/doc: each method answers with the status (a number) or the response (a function) set for it now */
function store(answers: { GET?: Answer; PUT?: Answer }) {
  const s = { answers, calls: [] as Call[], stored: { updated_at: EPOCH_ISO, n: 0 } as Doc };
  vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    s.calls.push({ method, body });
    const a = s.answers[method as "GET" | "PUT"] ?? 200;
    if (typeof a === "function") return a();
    if (a === 200 && method === "PUT") s.stored = body as Doc;
    return new Response(a === 200 ? JSON.stringify(s.stored) : JSON.stringify({ error: "signed out" }), { status: a });
  });
  return s;
}
const count = (s: { calls: Call[] }, method: string) => s.calls.filter((c) => c.method === method).length;

/** let every timer due within `ms` fire, and the promises they start settle */
const pass = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

const mount = (opts: Partial<Parameters<typeof useSyncedDoc<Doc>>[0]> = {}) =>
  renderHook(() => useSyncedDoc<Doc>({ url: "./api/doc", localKey: "doc", normalize, empty, debounceMs: 5, retryMs: 50,
    ...opts })).result;

beforeEach(() => {
  resetSession();
  localStorage.clear();
  vi.useFakeTimers();
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("a 401 on a save", () => {
  it("signs the page out, keeps the edit in memory and in this browser's copy, and is never retried", async () => {
    const s = store({ PUT: 401 });
    const doc = mount();
    await pass(0);
    expect(doc.current.loading).toBe(false);
    act(() => doc.current.commit(EDIT));
    await pass(10 * RETRY_MAX_MS);                   // ten times the longest backoff: a retry loop would have fired
    expect(count(s, "PUT"), "one PUT, no retry").toBe(1);
    expect(sessionState()).toBe("out");
    expect(doc.current.localOnly).toBe(true);
    expect(doc.current.saveState).toBe("error");
    expect(doc.current.doc).toEqual(EDIT);
    expect(normalize(JSON.parse(localStorage.getItem("doc") ?? "null"))).toEqual(EDIT);
    expect(hasUnsentEdits()).toBe(true);
  });

  it("an edit made while signed out is kept the same way and sends nothing", async () => {
    const s = store({ PUT: 401 });
    const doc = mount();
    await pass(0);
    act(() => doc.current.commit(EDIT));
    await pass(100);
    act(() => doc.current.commit(LATER));
    await pass(10 * RETRY_MAX_MS);
    expect(count(s, "PUT")).toBe(1);
    expect(doc.current.doc).toEqual(LATER);
    expect(normalize(JSON.parse(localStorage.getItem("doc") ?? "null"))).toEqual(LATER);
  });

  it("once a store call succeeds again, the kept edit is sent once and the page is signed in", async () => {
    const s = store({ PUT: 401 });
    const doc = mount();
    await pass(0);
    act(() => doc.current.commit(EDIT));
    await pass(100);
    s.answers.PUT = 200;
    act(() => noteStoreStatus(200));                // another call (a refresh read, a re-read) reached the store
    await pass(1000);
    expect(count(s, "PUT")).toBe(2);
    expect(s.stored).toEqual(EDIT);
    expect(sessionState()).toBe("in");
    expect(doc.current.localOnly).toBe(false);
    expect(hasUnsentEdits()).toBe(false);
  });

  it("another document's 401 holds this one's edits too: the sign-in is the page's, not one document's", async () => {
    const s = store({});
    const doc = mount();
    await pass(0);
    act(() => noteStoreStatus(401));
    act(() => doc.current.commit(EDIT));
    await pass(10 * RETRY_MAX_MS);
    expect(count(s, "PUT")).toBe(0);
    expect(doc.current.doc).toEqual(EDIT);
    expect(normalize(JSON.parse(localStorage.getItem("doc") ?? "null"))).toEqual(EDIT);
    expect(hasUnsentEdits()).toBe(true);
  });

  it("a list edit whose re-read of the store answers 401 is held the same way, nothing PUT", async () => {
    const s = store({});
    const doc = renderHook(() => useSyncedDoc({ ...PREFS_SYNC, localKey: "prefs", debounceMs: 5, retryMs: 50 })).result;
    await pass(0);
    s.answers.GET = 401;
    act(() => doc.current.commit((cur) => withSaved(cur, "saved_filters", { id: "v", name: "v", query: "q=v" },
      "2026-07-16T10:01:00.000Z")!));
    await pass(10 * RETRY_MAX_MS);
    expect(count(s, "PUT")).toBe(0);
    expect(count(s, "GET"), "the load and the one re-read").toBe(2);
    expect(sessionState()).toBe("out");
    expect(doc.current.doc.saved_filters.map((v) => v.id)).toEqual(["v"]);
  });
});

describe("a 401 on the load", () => {
  it("signs the page out and shows this browser's copy, with no timed retry", async () => {
    localStorage.setItem("doc", JSON.stringify(EDIT));
    const s = store({ GET: 401 });
    const doc = mount({ resyncOnVisible: true });
    await pass(10 * RETRY_MAX_MS);
    expect(count(s, "GET"), "the load only").toBe(1);
    expect(sessionState()).toBe("out");
    expect(doc.current.loading).toBe(false);
    expect(doc.current.localOnly).toBe(true);
    expect(doc.current.doc).toEqual(EDIT);
  });

  it("an unreachable store is still retried a few times (a 401 is the only answer that stops it)", async () => {
    const s = store({ GET: 502 });
    mount({ resyncOnVisible: true });
    await pass(10 * RETRY_MAX_MS);
    expect(count(s, "GET")).toBeGreaterThan(1);
    expect(sessionState()).toBe("in");
  });
});

describe("unsent edits, as the sign-out asks about them", () => {
  it("are held from the commit until the store has the document", async () => {
    store({});
    const doc = mount();
    await pass(0);
    expect(hasUnsentEdits()).toBe(false);
    act(() => doc.current.commit(EDIT));
    expect(hasUnsentEdits()).toBe(true);
    await pass(1000);
    expect(hasUnsentEdits()).toBe(false);
  });

  it("stay while the store cannot be reached and its save waits for a retry", async () => {
    store({ PUT: 502 });
    const doc = mount();
    await pass(0);
    act(() => doc.current.commit(EDIT));
    await pass(1000);
    expect(hasUnsentEdits()).toBe(true);
  });
});

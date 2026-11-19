// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Generic synced-document transport: server-first (GET/PUT a JSON doc through
// the passcode-gated /api/*), transparent localStorage fallback when the API is
// unreachable (dev without the store, server down). Extracted from
// useScenarioStore so the answers and prefs stores share one battle-tested
// path: optimistic commit → local mirror → debounced PUT → pagehide flush,
// with newer-`updated_at`-wins reconciliation on load. A failed PUT is retried
// with backoff (the store restarts on every deploy) and its success clears the
// local-only state; only the newest PUT's echo is ever adopted. A 401 is not a
// failure to retry: the sign-in has ended (data/session.ts), so the edit is held
// in memory and in the mirror, nothing is sent, and the held doc goes out once a
// store call succeeds again.
//
// The demo build (lib/demo.ts) has no store: there the transport is local (useLocalDoc below), the browser's copy is
// the document (until there is one, the static file the site was built with), and nothing is sent, retried or
// reported unsaved.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { IS_DEMO } from "../lib/demo";
import { noteStoreStatus, onSessionChange, reportUnsent, sessionState } from "./session";

/** the family store's four documents (deploy/scenario_store.py STORES), by the path each is served at */
export type StoreDocName = "scenarios" | "answers" | "prefs" | "manual-assets";

/** where the family store serves a document, relative to the page. The demo build has no store and carries none of its
 *  paths: there it is the document as the invented household's store held it when the site was built, a static file
 *  beside the page (tools/verify/demo_site.py stages it; useLocalDoc reads it while this browser holds no copy). */
export const storeUrl = (name: StoreDocName): string => (IS_DEMO ? `./family/${name}.json` : `./api/${name}`);

// Shared LWW-document constants: a never-written doc must lose newer-of
// against any client copy (deploy/scenario_store.py EPOCH_ISO — keep in sync). The stores' writers stamp
// `updated_at` / `answered_at` with `clock.nowISO()` (lib/clock.ts), the real instant in UTC.
export const EPOCH_ISO = "1970-01-01T00:00:00Z";

export type SaveState = "idle" | "saving" | "saved" | "error";

export interface SyncedDoc {
  updated_at: string;
}

/** JSON with every object's keys in sorted order: two copies of a document that hold the same data compare
 *  equal whichever order their keys were built in (a normalized server copy and an op's spread differ there). */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(value, (_key, v: unknown) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : v);
}

const idOf = (x: unknown): string | null =>
  x && typeof x === "object" && typeof (x as { id?: unknown }).id === "string" ? (x as { id: string }).id : null;
const asList = (x: unknown): unknown[] => (Array.isArray(x) ? x : []);

/** Three-way merge of one list of items with a string `id` (a saved view, a saved report): `base` is the copy
 *  this browser last heard from the store, `local` this browser's copy (base plus its edits), `server` the store's
 *  copy now (base plus every other device's edits). The result is the server's items in the server's order — less
 *  the ones this browser deleted, with this browser's version of an item it edited — then the items this browser
 *  added, in its order. An item another device deleted stays deleted even if this browser edited it meanwhile:
 *  a deletion wins. */
export function mergeById(base: readonly unknown[], local: readonly unknown[], server: readonly unknown[]): unknown[] {
  const byId = (xs: readonly unknown[]) => {
    const m = new Map<string, unknown>();
    for (const x of xs) { const id = idOf(x); if (id !== null && !m.has(id)) m.set(id, x); }
    return m;
  };
  const b = byId(base), l = byId(local), s = byId(server);
  const out: unknown[] = [];
  for (const x of server) {
    const id = idOf(x);
    if (id === null) { out.push(x); continue; }
    if (b.has(id) && !l.has(id)) continue;                                     // deleted here
    const mine = l.get(id);
    const editedHere = mine !== undefined && (!b.has(id) || canonicalJson(mine) !== canonicalJson(b.get(id)));
    out.push(editedHere ? mine : x);
  }
  for (const x of local) {
    const id = idOf(x);
    if (id !== null && !s.has(id) && !b.has(id)) out.push(x);                   // added here (in base = deleted there)
  }
  return out;
}

/** the local mirror's slot for the kept sections as the server last acknowledged them (keepOmitted, keepWhole) */
const ACKED = "_acked";
const NONE: readonly never[] = [];

export interface SyncedDocOptions<T extends SyncedDoc> {
  url: string;
  localKey: string;
  /** defensive parse of anything (server, localStorage, garbage) into a valid doc */
  normalize: (raw: unknown) => T;
  empty: () => T;
  /** adopt the server's PUT response as truth (the answers store echoes a
   *  merged doc so concurrent family edits propagate; the prefs store echoes the
   *  lists it kept for a PUT that omitted them); skipped when a newer local edit
   *  is already pending. Verbatim-LWW stores leave this false. */
  adoptEcho?: boolean;
  /** Top-level sections the server KEEPS when a PUT omits them (deploy/scenario_store.py merge_prefs: the
   *  saved views and reports), each a list of items with a string `id`. A PUT carries one only when this browser
   *  changed it since the copy the store last acknowledged (the load's GET, an adopted echo, a re-read), and then
   *  only after re-reading the store and merging by id (mergeById), so another device's additions and deletions
   *  since survive. The acknowledged copies ride in the local mirror: the load and a reconnect merge the same way. */
  keepOmitted?: readonly (keyof T & string)[];
  /** Top-level sections the server also keeps when a PUT omits them (merge_prefs: the target mix) that are one value,
   *  not a list: a PUT carries one only when this browser changed it since the copy the store last acknowledged, and
   *  then whole (the section is last-write-wins), so a tab that loaded before another device saved it cannot write
   *  its older copy back with an unrelated edit. The load, an echo while an edit waits and a re-read keep this
   *  browser's copy of a section it changed and take the store's for any other. */
  keepWhole?: readonly (keyof T & string)[];
  /** re-read the server copy when the tab comes back (visible again, focus, pageshow, back online) and nothing
   *  is waiting to be sent; a failed re-read is retried with the PUT backoff, a few times */
  resyncOnVisible?: boolean;
  /** the store merges PUTs as a union (deploy/scenario_store.py merge_answers): a session whose load never reached
   *  the store just pushes its copy when the store answers again, instead of comparing updated_at */
  union?: boolean;
  /** how the load and a reconnect combine this browser's copy with the store's, for a store that merges its
   *  documents item by item (deploy/scenario_store.py merge_manual_assets): the result is the doc, and it is pushed
   *  when it holds anything the store's copy does not. Without it the newer `updated_at` wins the whole doc. */
  merge?: (local: T, server: T) => T;
  /** what "the same document" means for an echo or a re-read (an equal one is not adopted, so the doc keeps
   *  its identity and nothing downstream recomputes); canonicalJson by default */
  fingerprint?: (doc: T) => string;
  debounceMs?: number;
  /** the first retry's delay after a failed PUT or re-read; each further failure doubles it (retryDelay) */
  retryMs?: number;
}

export const RETRY_MAX_MS = 60_000;
/** the wait before retry number `failures` (0-based): `base` doubled per earlier failure, capped at RETRY_MAX_MS */
export const retryDelay = (base: number, failures: number) => Math.min(base * 2 ** failures, RETRY_MAX_MS);
/** a failed re-read is retried this many times (about two minutes at the default base), then waits for the
 *  tab's next return */
const RESYNC_RETRIES = 6;
/** the store refused this document (a 400: malformed, a 413: too large): sending it again cannot succeed,
 *  so it is not retried — the next edit makes a new document and tries again */
const REFUSED = new Set([400, 413]);
/** the store's answer to a call made without a live session: the sign-in has ended (data/session.ts) */
const SIGNED_OUT = 401;

/** a store answer that was not a 2xx, by its status */
class StoreAnswer extends Error {
  constructor(readonly status: number) {
    super(`HTTP ${status}`);
  }
}
/** the status a failed store call ended with: its answer's, or 0 when no answer came */
const statusOf = (e: unknown): number => (e instanceof StoreAnswer ? e.status : 0);

export interface SyncedDocApi<T extends SyncedDoc> {
  loading: boolean;
  /** true → the store is unreachable (or refused the last save); edits persist in this browser only until a
   *  save gets through */
  localOnly: boolean;
  /** the HTTP status (400, 413) of a save the store refused outright, until a save gets through; null otherwise */
  refused: number | null;
  /** the store's own words for that refusal (its reply's `error`), while `refused` is set; null when it gave none */
  refusedReason: string | null;
  /** how many PUTs the store has answered 200 since this hook mounted, and how many it refused: a caller that sent a
   *  save watches these move to learn its outcome (an unreachable store moves neither; the retry follows) */
  accepted: number;
  refusals: number;
  saveState: SaveState;
  /** this browser's copy: what it last committed (optimistic), or the store's when it adopted one */
  doc: T;
  /** the copy the store last acknowledged: the load's, an adopted echo or re-read, or (a store with no echo) what a PUT
   *  it answered carried; null before the store answered. A refused or unsent edit is in `doc` and not here. */
  acked: T | null;
  /** optimistic state + sync local mirror + debounced PUT. Accepts a
   *  functional update — always use it for read-modify-write ops so rapid
   *  successive commits can't clobber each other via stale closures. */
  commit: (next: T | ((cur: T) => T)) => void;
}

/** the transport through the family store (the header above) */
function useStoreDoc<T extends SyncedDoc>(opts: SyncedDocOptions<T>): SyncedDocApi<T> {
  const { url, localKey, normalize, empty, adoptEcho = false, keepOmitted = NONE, keepWhole = NONE, resyncOnVisible = false,
          union = false, merge, fingerprint = canonicalJson, debounceMs = 800, retryMs = 2000 } = opts;

  const [doc, setDoc] = useState<T>(empty);
  const [loading, setLoading] = useState(true);
  const [localOnly, setLocalOnly] = useState(false);
  const [refused, setRefused] = useState<number | null>(null);
  const [refusedReason, setRefusedReason] = useState<string | null>(null);
  const [acked, setAcked] = useState<T | null>(null);
  const [accepted, setAccepted] = useState(0);
  const [refusals, setRefusals] = useState(0);
  const [saveState, setSaveState] = useState<SaveState>("idle");

  const loadingRef = useRef(true);
  /** the load never reached the store (dev without it, the store down at page load): edits stay in this
   *  browser, nothing is PUT, until a re-read reaches it */
  const noServerRef = useRef(false);
  const putTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingDoc = useRef<T | null>(null);
  /** PUTs sent and not answered yet */
  const inFlight = useRef(0);
  /** numbers every PUT: only the newest one's echo may be adopted (an older one answering late is stale) */
  const seqRef = useRef(0);
  /** failed PUTs in a row (the retry backoff's exponent) */
  const failures = useRef(0);
  /** the store refused the last save: this browser's copy holds an edit the store does not, so a re-read must
   *  not replace it (the notice stays up until an edit that fits gets through) */
  const refusedRef = useRef(false);
  /** the waiting doc holds a list edit: it goes out at once, not after the debounce */
  const listWaiting = useRef(false);
  /** the sign-in has ended (a 401 here, or on any store call: data/session.ts): this browser's copy holds edits nothing
   *  sends, kept in memory and in the mirror, until a store call succeeds again; no retry, no re-read meanwhile */
  const heldRef = useRef(false);
  /** this browser's copy holds an edit the store has not acknowledged yet, which signing out would lose: from a commit
   *  (or a load that has something to push) until the store answers the newest save with nothing else waiting */
  const dirtyRef = useRef(false);
  /** this hook's token in the page's list of documents with unsent edits */
  const ownerRef = useRef(Symbol("synced document"));
  const setDirty = useCallback((dirty: boolean) => {
    dirtyRef.current = dirty;
    reportUnsent(ownerRef.current, dirty);
  }, []);
  const aliveRef = useRef(true);
  /** schedules the next timed re-read (the resync effect's bounded backoff); a no-op without resyncOnVisible */
  const kickRef = useRef<() => void>(() => {});
  // the latest committed doc, updated SYNCHRONOUSLY in commit() so functional
  // updates chain correctly even before React re-renders
  const docRef = useRef<T | null>(null);
  /** the keepOmitted and keepWhole sections as the server last acknowledged them (the merge base); null before the load */
  const ackedRef = useRef<Record<string, unknown> | null>(null);
  /** the keepWhole sections this browser changed (marked by commit) that no answered PUT has carried at their current
   *  value yet: each goes out with every PUT, even when it is back at the acknowledged copy (set, then cleared before
   *  the first save is answered), and leaves the set only once the store answers a PUT that carried its current value,
   *  so a failed or superseded save keeps it */
  const wholeChanged = useRef(new Set<string>());
  // stable refs so load/flush effects never re-run on option identity changes
  const normalizeRef = useRef(normalize);
  normalizeRef.current = normalize;
  const emptyRef = useRef(empty);
  emptyRef.current = empty;
  const keepRef = useRef(keepOmitted);
  keepRef.current = keepOmitted;
  const wholeRef = useRef(keepWhole);
  wholeRef.current = keepWhole;
  /** every section the store keeps when a PUT omits it: the lists and the whole ones */
  const keptKeys = useCallback((): string[] => [...keepRef.current, ...wholeRef.current], []);
  const fingerprintRef = useRef(fingerprint);
  fingerprintRef.current = fingerprint;
  const mergeRef = useRef(merge);
  mergeRef.current = merge;

  const readLocal = useCallback((): T => {
    try { return normalizeRef.current(JSON.parse(localStorage.getItem(localKey) ?? "null")); }
    catch { return emptyRef.current(); }
  }, [localKey]);

  /** The merge base for `local` (the mirror's doc): per kept section, the copy the store had acknowledged when the
   *  mirror was written, normalized like any stored section (a malformed entry dropped). A list the mirror does
   *  not hold as a list, or a whole section it does not hold at all (an older bundle wrote it, or garbage), has no
   *  known base: the local copy itself stands in, so what this browser adds from here on counts as its addition and
   *  nothing it holds counts as its edit. */
  const baseFor = useCallback((local: T): Record<string, unknown> => {
    let acked: Record<string, unknown> = {};
    try {
      const raw: unknown = JSON.parse(localStorage.getItem(localKey) ?? "null");
      const a = raw && typeof raw === "object" ? (raw as Record<string, unknown>)[ACKED] : null;
      if (a && typeof a === "object" && !Array.isArray(a)) acked = a as Record<string, unknown>;
    } catch { /* unreadable: no base */ }
    return Object.fromEntries([
      ...keepRef.current.map((k) => [k, Array.isArray(acked[k]) ? normalizeRef.current({ [k]: acked[k] })[k] : local[k]]),
      ...wholeRef.current.map((k) => [k, Object.hasOwn(acked, k) ? normalizeRef.current({ [k]: acked[k] })[k] : local[k]]),
    ]);
  }, [localKey]);

  const writeLocal = useCallback((next: T) => {
    // a page that signed out on purpose has forgotten the family's documents and is leaving: nothing writes them back
    if (sessionState() === "ended") return;
    const acked = keptKeys().length ? ackedRef.current : null;
    try { localStorage.setItem(localKey, JSON.stringify(acked ? { ...next, [ACKED]: acked } : next)); }
    catch { /* quota — server copy still saves */ }
  }, [localKey, keptKeys]);

  /** `server` is what the store holds now (a load, an echo, a re-read): the merge base of the kept sections, and the
   *  copy a caller compares its edits with (`acked`) */
  const acknowledge = useCallback((server: T) => {
    const keys = keptKeys();
    if (keys.length) ackedRef.current = Object.fromEntries(keys.map((k) => [k, server[k as keyof T]]));
    setAcked(server);
  }, [keptKeys]);

  /** a server copy becomes the doc (an equal one leaves the doc as it is, identity included) */
  const adopt = useCallback((server: T) => {
    acknowledge(server);
    const cur = docRef.current;
    if (!cur || fingerprintRef.current(server) !== fingerprintRef.current(cur)) {
      docRef.current = server;
      setDoc(server);
    }
    writeLocal(docRef.current ?? server);
  }, [acknowledge, writeLocal]);

  /** `into` with every kept list merged by id (mergeById: `base` → `local` against `server`) and every whole
   *  section taken from `local` when this browser changed it (it differs from `base`), else from `server`;
   *  `changed`: a merged section holds something the server's does not (an edit not yet sent: a keepalive flush
   *  leaves the lists out) */
  const mergeLists = useCallback((into: T, local: T, server: T, base: Record<string, unknown>) => {
    let doc = into, changed = false;
    for (const k of keepRef.current) {
      const merged = mergeById(asList(base[k]), asList(local[k]), asList(server[k]));
      if (canonicalJson(merged) !== canonicalJson(server[k])) changed = true;
      doc = { ...doc, [k]: merged };
    }
    for (const k of wholeRef.current) {
      // a section still marked as changed here is this browser's even when it is back at the base: the store may hold
      // the value it was changed to, or another device's since (wholeChanged)
      const mine = wholeChanged.current.has(k)
        || (Object.hasOwn(base, k) && canonicalJson(local[k]) !== canonicalJson(base[k]));
      const merged = mine ? local[k] : server[k];
      if (canonicalJson(merged) !== canonicalJson(server[k])) changed = true;
      doc = { ...doc, [k]: merged };
    }
    return { doc, changed };
  }, []);

  /** load and reconnect: `local` (this browser's copy, from `base`) against the store's `server`. The newer
   *  updated_at wins the doc (or the `merge` option combines the two), and every kept list merges by id (mergeById),
   *  so no item is lost to a timestamp. `push`: the result holds something the store does not. */
  const reconcile = useCallback((local: T, server: T, base: Record<string, unknown>) => {
    if (mergeRef.current) {
      const merged = mergeRef.current(local, server);
      const { doc, changed } = mergeLists(merged, local, server, base);
      return { chosen: doc, push: changed || fingerprintRef.current(merged) !== fingerprintRef.current(server) };
    }
    const localNewer = local.updated_at > server.updated_at;
    const { doc, changed } = mergeLists(localNewer ? local : server, local, server, base);
    return { chosen: doc, push: localNewer || changed };
  }, [mergeLists]);

  /** The newest PUT's echo is the store's acknowledged state, so it always becomes the merge base. The doc adopts
   *  it only when nothing newer is waiting; otherwise the waiting doc's lists are rebased onto it (their own unsent
   *  edits replayed over the echo), so the base and the doc stay a matching pair and a later merge cannot undo
   *  another device's deletion or rename. */
  const settleEcho = useCallback((echoed: T) => {
    if (!putTimer.current && !pendingDoc.current) { adopt(echoed); return; }
    const base = ackedRef.current;
    acknowledge(echoed);
    const cur = docRef.current;
    if (cur && base && keptKeys().length) {
      const { doc: rebased } = mergeLists(cur, cur, echoed, base);
      if (fingerprintRef.current(rebased) !== fingerprintRef.current(cur)) {
        if (pendingDoc.current === cur) pendingDoc.current = rebased;
        docRef.current = rebased;
        setDoc(rebased);
      }
    }
    writeLocal(docRef.current ?? echoed);
  }, [adopt, acknowledge, mergeLists, writeLocal, keptKeys]);

  /** What a PUT of `next` sends. A kept section equal to the store's acknowledged copy stays out (the store
   *  keeps its own), unless it is a whole section still marked as changed here (wholeChanged): that one goes as it
   *  is, as does any whole section that differs from the acknowledged copy. A list this browser changed is merged by
   *  id with the store's copy as it is NOW (a GET first), so a list edit never carries back the list as this tab
   *  last saw it. A keepalive flush (the page is going away) has no time to ask, so it leaves every changed list
   *  out too: the mirror keeps the edited lists with their base, and the next load, reconnect or return merges them
   *  in like any list edit not yet sent. */
  const bodyOf = useCallback(async (next: T, keepalive: boolean): Promise<unknown> => {
    const acked = ackedRef.current, keys = keepRef.current;
    if (!acked || !keptKeys().length) return next;
    const body: Record<string, unknown> = { ...(next as SyncedDoc) };
    for (const k of wholeRef.current) {
      if (!wholeChanged.current.has(k) && canonicalJson(next[k]) === canonicalJson(acked[k])) delete body[k];
    }
    const carried: string[] = [];
    for (const k of keys) {
      if (canonicalJson(next[k]) === canonicalJson(acked[k])) delete body[k];
      else carried.push(k);
    }
    if (keepalive) for (const k of carried) delete body[k];
    if (!carried.length || keepalive) return body;
    // one race remains: another device's save landing between this GET and the PUT is overwritten for this list
    const res = await fetch(url);
    noteStoreStatus(res.status);
    if (!res.ok) throw new StoreAnswer(res.status);
    const server = normalizeRef.current(await res.json()) as unknown as Record<string, unknown>;
    for (const k of carried) body[k] = mergeById(asList(acked[k]), asList(body[k]), asList(server[k]));
    return body;
  }, [url, keptKeys]);

  // send `next` after `delay` (the commit debounce, or a retry's backoff); a later call replaces it
  const putRef = useRef<(next: T, keepalive?: boolean) => Promise<void>>(async () => {});
  const schedule = useCallback((next: T, delay: number) => {
    pendingDoc.current = next;
    if (putTimer.current) clearTimeout(putTimer.current);
    putTimer.current = setTimeout(() => {
      putTimer.current = null;
      listWaiting.current = false;
      const due = pendingDoc.current;
      pendingDoc.current = null;
      if (due) void putRef.current(due);
    }, delay);
  }, []);

  const put = useCallback(async (next: T, keepalive = false) => {
    const seq = ++seqRef.current;
    inFlight.current += 1;                 // a keepalive flush too: a back/forward-cache return must not re-read under it
    let status = 0;
    let reason: string | null = null;
    try {
      const body = await bodyOf(next, keepalive);
      // a newer PUT was sent while this one asked the store for its lists: built from a later doc, it carries
      // everything this one would, and this one's merge may already be stale - so this one stands down
      if (seq !== seqRef.current) return;
      const res = await fetch(url, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        keepalive,
      });
      status = res.status;
      noteStoreStatus(status);
      if (!res.ok) {
        // a refusal says why in its `error`: a caller may show it (deploy/scenario_store.py writes plain words)
        if (REFUSED.has(status)) {
          const said: unknown = await res.json().catch(() => null);
          const error = said && typeof said === "object" ? (said as { error?: unknown }).error : null;
          reason = typeof error === "string" && error.trim() ? error : null;
        }
        throw new StoreAnswer(status);
      }
      // the store now holds what this PUT carried: a whole section it carried at the value this browser holds now is
      // confirmed; one changed again since (a newer PUT carries it) stays marked. Only the newest PUT's answer
      // confirms: an older one carrying the same value by chance may have been overtaken at the store by a newer one
      // carrying another, whose own outcome is not known yet.
      const sent = body as Record<string, unknown>;
      if (seq === seqRef.current) {
        for (const k of [...wholeChanged.current]) {
          if (Object.hasOwn(sent, k) && canonicalJson(sent[k]) === canonicalJson(docRef.current?.[k as keyof T])) {
            wholeChanged.current.delete(k);
          }
        }
      }
      // a store that stores what it is sent as it is: what this PUT carried is its copy now
      if (!adoptEcho && !keepalive && seq === seqRef.current) setAcked(next);
      if (adoptEcho && !keepalive) {
        // the server may have merged in another client's edits; adopt its echo - unless a newer PUT has been
        // sent since (its echo is the newer truth) or the user has already typed something newer here
        const merged = normalizeRef.current(await res.json());
        if (seq === seqRef.current) settleEcho(merged);
      }
      failures.current = 0;
      refusedRef.current = false;
      heldRef.current = false;
      setLocalOnly(false);
      setRefused(null);
      setRefusedReason(null);
      // an older PUT answering after a newer one was sent says nothing about the newer one's outcome
      if (seq === seqRef.current) setAccepted((n) => n + 1);
      // the store has this browser's copy, unless an edit made since waits for its own save
      if (seq === seqRef.current && !putTimer.current && !pendingDoc.current) setDirty(false);
      setSaveState("saved");
      if (savedTimer.current) clearTimeout(savedTimer.current);
      savedTimer.current = setTimeout(() => setSaveState("idle"), 2000);
    } catch (e) {
      // the list re-read before the PUT (bodyOf) may be the call that was answered
      status ||= statusOf(e);
      // a newer PUT has been sent since: its outcome, not this one's, says whether the store has the doc
      if (seq !== seqRef.current) return;
      // the localStorage mirror already holds the data — degrade, don't lose; and try again (with backoff)
      // unless the store refused this very document, the sign-in has ended, or something newer is already on its way
      setLocalOnly(true);
      setSaveState("error");
      if (status === SIGNED_OUT) {
        // sending it again cannot succeed until someone signs in: it is held, and goes out once a store call does
        heldRef.current = true;
        return;
      }
      if (REFUSED.has(status)) {
        refusedRef.current = true;
        setRefused(status);
        setRefusedReason(reason);
        setRefusals((n) => n + 1);
      }
      // retried while this hook is mounted - a failed keepalive flush too (the page was only hidden: its timers
      // resume on a back/forward-cache return, and the doc counts as waiting meanwhile)
      if (aliveRef.current && !REFUSED.has(status) && !putTimer.current && docRef.current) {
        schedule(docRef.current, retryDelay(retryMs, failures.current));
        failures.current += 1;
      }
    } finally {
      inFlight.current -= 1;
    }
  }, [url, adoptEcho, settleEcho, bodyOf, schedule, retryMs, setDirty]);
  putRef.current = put;

  const commit = useCallback((next: T | ((cur: T) => T)) => {
    const prev = docRef.current;
    const resolved = typeof next === "function"
      ? (next as (cur: T) => T)(prev ?? emptyRef.current())
      : next;
    docRef.current = resolved;
    setDoc(resolved);
    writeLocal(resolved);
    for (const k of wholeRef.current) {
      if (canonicalJson(prev?.[k as keyof T]) !== canonicalJson(resolved[k as keyof T])) wholeChanged.current.add(k);
    }
    setDirty(true);
    if (noServerRef.current) return;
    if (sessionState() !== "in") {
      // signed out (here or by another store call): kept in memory and in the mirror, sent once a store call succeeds;
      // a save still waiting on its timer would only be refused too
      if (putTimer.current) clearTimeout(putTimer.current);
      putTimer.current = null;
      pendingDoc.current = null;
      heldRef.current = true;
      setLocalOnly(true);
      setSaveState("error");
      return;
    }
    setSaveState("saving");
    // an edit of a kept section (saving, renaming or deleting a view or report, saving the target mix: one click,
    // never a slider drag) goes out at once, so a tab closed right after it holds it back for one round trip at
    // most, not a whole debounce
    if (keptKeys().some((k) => prev?.[k as keyof T] !== resolved[k as keyof T])) listWaiting.current = true;
    schedule(resolved, listWaiting.current ? 0 : debounceMs);
  }, [schedule, writeLocal, debounceMs, keptKeys, setDirty]);

  // load once: server vs local mirror (reconcile: newer updated_at wins, the kept lists merge by id)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const local = readLocal();
      const base = baseFor(local);
      let chosen = local;
      let push = false;
      let signedOut = false;
      try {
        const res = await fetch(url);
        noteStoreStatus(res.status);
        if (!res.ok) throw new StoreAnswer(res.status);
        const server = normalizeRef.current(await res.json());
        // a local copy newer than the server means a debounced save never landed (tab closed, server was
        // down) — it is pushed now, its lists merged with what the store holds
        ({ chosen, push } = reconcile(local, server, base));
        acknowledge(server);
        // a merged load may have dropped what the mirror held (an entry the store has long since removed): the mirror
        // keeps the result, so the next load does not meet it again
        if (mergeRef.current && !cancelled) writeLocal(chosen);
      } catch (e) {
        // edits made from here on are made over the mirror's copy: its base stays theirs (see baseFor)
        if (keptKeys().length) ackedRef.current = base;
        noServerRef.current = true;
        signedOut = statusOf(e) === SIGNED_OUT;
        if (!cancelled) setLocalOnly(true);
      }
      if (cancelled) return;
      docRef.current = chosen;
      setDoc(chosen);
      loadingRef.current = false;
      setLoading(false);
      if (push) {
        setDirty(true);
        void put(chosen);
      }
      // keep trying the store, a few times, on its own; signed out there is no point until a store call succeeds
      if (noServerRef.current && !signedOut) kickRef.current();
    })();
    return () => { cancelled = true; };
  }, [url, readLocal, baseFor, reconcile, acknowledge, put, keptKeys, writeLocal, setDirty]);

  // Signed in again (a store call succeeded, here or elsewhere on the page): a held edit goes out now, and a session
  // whose load was refused tries the store again as it would after an outage.
  useEffect(() => onSessionChange(() => {
    if (sessionState() !== "in" || !aliveRef.current) return;
    if (heldRef.current) {
      heldRef.current = false;
      failures.current = 0;
      if (docRef.current) schedule(docRef.current, 0);
    } else if (noServerRef.current) {
      kickRef.current();
    }
  }), [schedule]);

  // Back on the tab: another device may have saved since this one last heard from the store. With nothing
  // waiting to be sent, the server's copy is the newest there is, so it is adopted; a commit made while the
  // GET was out is newer still and wins (its own PUT carries it). Kept lists merge by id on the way in: this
  // tab's own unsent list edits (a keepalive flush leaves them out), and those another tab of this browser left
  // in the shared mirror when it closed before its save landed - never written over. A session whose load never
  // reached the store reconnects: a union store gets this browser's copy pushed (it merges it), any other
  // reconciles as the load would have. A failed re-read (or load) is retried with backoff, a few times.
  useEffect(() => {
    if (!resyncOnVisible) return;
    let alive = true, busy = false, misses = 0;
    let retry: ReturnType<typeof setTimeout> | null = null;
    const idle = () => !loadingRef.current && !refusedRef.current && !heldRef.current && !putTimer.current
      && !pendingDoc.current && inFlight.current === 0;
    const later = () => {
      if (alive && !retry && misses < RESYNC_RETRIES) {
        retry = setTimeout(() => { retry = null; void resync(); }, retryDelay(retryMs, misses++));
      }
    };
    const resync = async () => {
      if (retry) { clearTimeout(retry); retry = null; }
      if (document.visibilityState !== "visible" || busy || !idle()) return;
      busy = true;
      const before = docRef.current;
      try {
        const res = await fetch(url);
        noteStoreStatus(res.status);
        if (!res.ok) throw new StoreAnswer(res.status);
        const server = normalizeRef.current(await res.json());
        misses = 0;
        if (!alive || !idle() || docRef.current !== before) return;
        const local = docRef.current;
        const reconnect = noServerRef.current;
        noServerRef.current = false;
        setLocalOnly(false);
        if (reconnect && local && union && local.updated_at > EPOCH_ISO) {
          void putRef.current(local);                    // the store unions it with what it holds; the echo comes back
          return;
        }
        let chosen = server, push = false;
        if (reconnect && local) ({ chosen, push } = reconcile(local, server, ackedRef.current ?? {}));
        else if (local) ({ doc: chosen, changed: push } = mergeLists(server, local, server, ackedRef.current ?? {}));
        const mirror = readLocal();
        const theirs = mergeLists(chosen, mirror, chosen, baseFor(mirror));
        if (theirs.changed) { chosen = theirs.doc; push = true; }
        if (!push) {
          adopt(chosen);
          setDirty(false);                               // this browser's copy is the store's now
          return;
        }
        setDirty(true);
        acknowledge(server);
        if (!local || fingerprintRef.current(chosen) !== fingerprintRef.current(local)) {
          docRef.current = chosen;
          setDoc(chosen);
        }
        writeLocal(docRef.current ?? chosen);
        void putRef.current(docRef.current ?? chosen);
      } catch (e) {
        // signed out: asking again on a timer cannot succeed (the tab's next return asks once more)
        if (statusOf(e) !== SIGNED_OUT) later();
      }
      finally { busy = false; }
    };
    kickRef.current = later;
    const onVisibility = () => void resync();
    document.addEventListener("visibilitychange", onVisibility);
    for (const ev of ["focus", "pageshow", "online"]) window.addEventListener(ev, onVisibility);
    return () => {
      alive = false;
      kickRef.current = () => {};
      if (retry) clearTimeout(retry);
      document.removeEventListener("visibilitychange", onVisibility);
      for (const ev of ["focus", "pageshow", "online"]) window.removeEventListener(ev, onVisibility);
    };
  }, [url, resyncOnVisible, union, retryMs, adopt, acknowledge, reconcile, mergeLists, readLocal, baseFor, writeLocal,
      setDirty]);

  // a pending debounced save must survive tab close / unmount
  useEffect(() => {
    const flush = () => {
      if (!putTimer.current || !pendingDoc.current) return;
      clearTimeout(putTimer.current);
      putTimer.current = null;
      void put(pendingDoc.current, true);
      pendingDoc.current = null;
    };
    aliveRef.current = true;
    window.addEventListener("pagehide", flush);
    return () => {
      aliveRef.current = false;
      window.removeEventListener("pagehide", flush);
      flush();
      // the "saved" indicator's reset must not fire after unmount (in tests: after the environment is torn down)
      if (savedTimer.current) { clearTimeout(savedTimer.current); savedTimer.current = null; }
    };
  }, [put]);

  return useMemo(() => ({ loading, localOnly, refused, refusedReason, accepted, refusals, saveState, doc, acked, commit }),
    [loading, localOnly, refused, refusedReason, accepted, refusals, saveState, doc, acked, commit]);
}

/** The demo build's transport: this browser's copy (`localKey`) is the document. With a copy, the load reads it at once;
 *  without one, it reads the document the site was built with (`url`, a static file beside the page: storeUrl), once,
 *  and an answer that is not that document is the empty one. A commit writes this browser's copy and counts as a save
 *  the store took (`accepted` moves, `acked` is the document), so every caller reads a save as done. Nothing is sent,
 *  retried or held, nothing is ever local-only or refused, and no edit is reported unsent. A storage that refuses the
 *  write (a private window, the quota) keeps the edit for this page view. The store's own options (the merge rules,
 *  the resync, the debounce) have nothing to act on. */
function useLocalDoc<T extends SyncedDoc>(opts: SyncedDocOptions<T>): SyncedDocApi<T> {
  const { url, localKey, normalize, empty } = opts;
  /** this browser's copy, or null when it holds none (or none it can read) */
  const [kept] = useState<T | null>(() => {
    try {
      const raw = localStorage.getItem(localKey);
      return raw === null ? null : normalize(JSON.parse(raw));
    } catch { return null; }
  });
  const [doc, setDoc] = useState<T>(() => kept ?? empty());
  const [loading, setLoading] = useState(kept === null && url !== "");
  const [accepted, setAccepted] = useState(0);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const docRef = useRef(doc);
  const committed = useRef(false);
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (savedTimer.current) clearTimeout(savedTimer.current); }, []);
  // no copy in this browser yet: the document the site was built with (an edit made meanwhile wins)
  const normalizeRef = useRef(normalize);
  normalizeRef.current = normalize;
  useEffect(() => {
    if (!loading) return;
    let alive = true;
    Promise.resolve().then(() => fetch(url)).then((res) => (res.ok ? res.json() : null)).catch(() => null)
      .then((raw: unknown) => {
      if (!alive) return;
      if (raw !== null && !committed.current) {
        const seed = normalizeRef.current(raw);
        docRef.current = seed;
        setDoc(seed);
      }
      setLoading(false);
    });
    return () => { alive = false; };
  }, [loading, url]);
  const commit = useCallback((next: T | ((cur: T) => T)) => {
    const resolved = typeof next === "function" ? (next as (cur: T) => T)(docRef.current) : next;
    committed.current = true;
    docRef.current = resolved;
    setDoc(resolved);
    try { localStorage.setItem(localKey, JSON.stringify(resolved)); }
    catch { /* storage refused: the edit lasts this page view */ }
    setAccepted((n) => n + 1);
    setSaveState("saved");
    if (savedTimer.current) clearTimeout(savedTimer.current);
    savedTimer.current = setTimeout(() => setSaveState("idle"), 2000);
  }, [localKey]);
  return useMemo(() => ({ loading, localOnly: false, refused: null, refusedReason: null, accepted, refusals: 0,
                          saveState, doc, acked: doc, commit }), [loading, accepted, saveState, doc, commit]);
}

/** One family document, synced: through the family store (useStoreDoc), or in the demo build kept in this browser alone
 *  (useLocalDoc). The build decides which, once, so every caller's hooks run in the same order on every render. */
export const useSyncedDoc: <T extends SyncedDoc>(opts: SyncedDocOptions<T>) => SyncedDocApi<T> =
  IS_DEMO ? useLocalDoc : useStoreDoc;

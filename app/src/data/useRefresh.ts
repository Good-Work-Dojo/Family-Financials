// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useCallback, useEffect, useRef, useState } from "react";
import { clock } from "../lib/clock";
import { IS_DEMO, storageKey } from "../lib/demo";
import type { RefreshSchedule } from "../lib/refreshSchedule";
import { noteStoreStatus } from "./session";

/** Talks to /api/refresh (deploy/scenario_store.py): report what the data
 *  refresh is doing, and ask for a new one.
 *
 *  Unlike useSyncedDoc there is nothing to degrade to — a refresh either
 *  happens on the server or it doesn't — so this never falls back to
 *  localStorage. A 401 never leaves the page by itself (an unsaved edit may be
 *  held there): it is `signedOut`, which the control says in words with a link
 *  the person follows to sign in again. */

/** the store's refresh view; empty in the demo build, which has no store (call() below asks nothing there) */
const URL = IS_DEMO ? "" : "./api/refresh";
/** what the demo build answers in the store's place: no refresh service, with its reason in plain words */
export const DEMO_NO_REFRESH = "The demo is built once from an invented household and is never refreshed.";
const POLL_MS = 3000;
const NONCE_KEY = storageKey("ff_refresh_nonce");
/** How long an ingest seen waiting or running is followed (polled at POLL_MS) from the read that finds it after someone
 *  looks — a new view, or `watch()` after a save: the store's 60-second settle, a refresh holding the lock or a retried
 *  interruption fit in it; past it the readout stops polling and treats the ingest as no longer under way (a capped
 *  or stuck one never spins a button for ever). */
export const INGEST_FOLLOW_MS = 3 * 60_000;
/** How long a refresh seen in flight is polled from the read that first found it: the refresher's own limit on a run
 *  (deploy/refresh_runner.py RUN_TIMEOUT_S, fifteen minutes) and two minutes for its stop and the status to say so.
 *  Past it the readout stops polling and says the refresh has not reported back; a new view looks again. */
export const RUN_FOLLOW_MS = 17 * 60_000;
/** The slow cadence: a visible tab's own look at the status (components/DataRefresh.tsx), and the next try after a
 *  read that failed while something was polled. */
export const LOOK_EVERY_MS = 60_000;

/** whether the tab is visible now (true where there is no document); nothing polls a hidden tab */
export function useTabVisible(): boolean {
  const [visible, setVisible] = useState(() => typeof document === "undefined" || document.visibilityState === "visible");
  useEffect(() => {
    if (typeof document === "undefined") return;
    const onChange = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);
  return visible;
}

export type RefreshState =
  | "idle" | "queued" | "running" | "done" | "interrupted"
  | "stalled" | "worker_unavailable";

export interface RefreshStatus {
  state: RefreshState;
  trigger: string | null;
  request_nonce: string | null;
  queued_nonce: string | null;
  started_at: string | null;
  finished_at: string | null;
  rc: number | null;
  steps_failed: string[];
  warnings: string[];
  can_refresh: boolean;
  reason: string;
  retry_after_s: number;
  used_today: number;
  limit_per_day: number;
  last_refresh_at: string | null;
  /** the ingest without a fetch (a manual-assets save asks for one); absent from a store that predates it */
  ingest?: IngestStatus;
  /** the scheduled refresh's times (`HH:MM`) in the household's zone, null while the store knows none (the scheduler
   *  has not written one); absent from a store that predates it (lib/refreshSchedule.ts reads it) */
  schedule?: RefreshSchedule | null;
  /** What the last refresh's fetch learned of the bank feed (deploy/scenario_store.py), each absent from a store that
   *  predates it, which reads as nothing learned: the run had no bank feed and read the household's files alone; the
   *  errors the feed reported, each "<connection>: <message>"; the connections whose bank the household must sign in
   *  to again at the feed provider. */
  files_only?: boolean;
  feed_errors?: string[];
  reauth_needed?: string[];
}

/** `GET /api/refresh` `ingest` (docs/design/information-architecture.md): `wanted` while the marker is newer than the
 *  last ingest's start (or that ingest ended interrupted: it runs again), the last ingest's start and end (UTC ISO;
 *  the end is null while it runs) and its exit code */
export interface IngestStatus {
  wanted: boolean;
  last_started_at: string | null;
  last_finished_at: string | null;
  last_rc: number | null;
}

const at = (iso: string | null): number => (iso === null ? NaN : Date.parse(iso));

/** an ingest has started and recorded no end after its start */
export const ingestRunning = (i: IngestStatus | undefined): boolean =>
  !!i && i.last_started_at !== null && !(at(i.last_finished_at) >= at(i.last_started_at));

/** an ingest is wanted (waiting to start) or running */
export const ingestBusy = (i: IngestStatus | undefined): boolean => !!i && (i.wanted || ingestRunning(i));

/** the ingest a client asked for has ended: nothing is wanted, and the last ingest's end is at or after its start
 *  (`last_rc` says how it went) */
export const ingestEnded = (i: IngestStatus | undefined): boolean =>
  !!i && !i.wanted && i.last_started_at !== null && at(i.last_finished_at) >= at(i.last_started_at);

/** The last ingest ended well and started after the exports on screen were built (`builtAt`, manifest.generated_at, the
 *  instant the ingest that wrote them stamped): its exports are newer than these. Both stamps are the server's own
 *  clock, so no device's clock is compared. */
export const newerIngest = (i: IngestStatus | undefined, builtAt: string | undefined): boolean =>
  !!i && builtAt !== undefined && i.last_rc === 0 && ingestEnded(i) && at(i.last_started_at) > Date.parse(builtAt);

/** what `POST /api/refresh {"mode": "ingest"}` answered: taken (the store touched the marker), refused (the day's cap,
 *  `ingest_cap`, or the store's own reason), no refresh service (its 503), or nothing at all (`unreached`: no answer, or
 *  a gateway's 502 / 504 — the request never reached the store, so nothing was asked) */
export type IngestAsk =
  | { kind: "accepted" }
  | { kind: "refused"; reason: string | null }
  | { kind: "no-service" }
  | { kind: "unreached" }
  /** a 401: the sign-in has ended (the control says so, live) */
  | { kind: "signed-out" };

/** what an ingest request asks for first (deploy/scenario_store.py MODES): nothing ("ingest"), the AI step ("ai") or
 *  forgetting the AI step's stored answers ("ai_reset") */
export type IngestMode = "ingest" | "ai" | "ai_reset";

/** who asked for the ingest a follow is watching: a save (the manual assets' card), this tab's "Rebuild from files",
 *  or nobody this tab knows of (a view or a look found it under way: another device, an earlier page) */
export type IngestCause = "save" | "rebuild" | "seen";

/** What the run that just ended did, for the line under the button. It belongs to that run: the
 *  readout hides it once a newer run exists or it is old (components/DataRefresh.tsx outcomeVisible). */
export interface Outcome {
  mine: boolean;
  ok: boolean;
  stepsFailed: string[];
  /** the server's finished_at for the run (null when the status carried none) */
  finishedAt: string | null;
  /** when this client saw the run end (ISO) - the age anchor when the server has no stamp */
  seenAt: string;
}

export interface RefreshApi {
  status: RefreshStatus | null;
  loading: boolean;
  /** the newest read got no status: the store did not answer, said there is no refresh service (`noService`), or the
   *  sign-in has ended (`signedOut`) */
  unreachable: boolean;
  /** why, when the server said so (e.g. the endpoint isn't configured, which is
   *  what a dev store started without --refresh-dir looks like). null = it
   *  simply didn't respond. */
  unavailableNote: string | null;
  /** the newest read's answer was the store's 503: it runs with no refresh service (the ingest is run by hand) */
  noService: boolean;
  /** the newest request was answered 401: the sign-in has ended. Nothing redirects by itself, and nothing is polled until
   *  a read answers again (a new view, or the tab coming back) */
  signedOut: boolean;
  /** a run is in flight: mine, or a scheduled one I'm riding along with, while it is polled (not past RUN_FOLLOW_MS) —
   *  or an ingest without a fetch is waiting or running while it is followed (`ingestBusy`) */
  busy: boolean;
  /** a refresh seen in flight passed RUN_FOLLOW_MS with no end: it is no longer polled, and has not reported back */
  runLost: boolean;
  /** an ingest without a fetch is waiting or running, within INGEST_FOLLOW_MS of someone looking */
  ingestBusy: boolean;
  /** while `ingestBusy`, who asked for that ingest (null otherwise) */
  ingestCause: IngestCause | null;
  posting: boolean;
  outcome: Outcome | null;
  error: string | null;
  /** the number, in the order reads were sent, of the newest read that has completed (answered or not); an answer to
   *  an older read arriving after a newer one is dropped, so the status is always the newest read's */
  reads: number;
  /** how many reads have been sent so far: an asker that notes it when its request lands counts only the answers to
   *  reads sent after (`reads` above it) */
  sent: () => number;
  trigger: () => Promise<void>;
  /** ask for an ingest without a fetch (`POST {"mode": "ingest"}`: no refresh budget is spent) and say how the store
   *  answered; the asker then follows it (data/useIngestFollow.ts). `mode` "ai" asks for the AI step first (the family
   *  switched it on), "ai_reset" for its stored answers to be forgotten first: each touches the ingest marker too, under
   *  the same daily cap */
  requestIngest: (mode?: IngestMode) => Promise<IngestAsk>;
  /** read the status now, even with no view open, and follow an ingest it shows for INGEST_FOLLOW_MS: what a save
   *  that asks for an ingest calls, so the exports reload when it ends (`cause`: who asked). `follow` false reads once
   *  and starts no follow of its own (a look already asked for still starts one; an ingest seen under way before still
   *  reloads the exports if this read finds it ended). */
  watch: (follow?: boolean, cause?: IngestCause) => void;
}

const BUSY: RefreshState[] = ["queued", "running"];
const isBusy = (s: RefreshStatus | null) => !!s && BUSY.includes(s.state);

/** One request. A 401 (the session ended) comes back like any answer: the caller records that the sign-in has ended, and
 *  the page stays where it is. Every answer also tells the page's sign-in state (data/session.ts): a 401 signs the page
 *  out (its banner, its saves held), a 2xx back in. The demo build sends nothing: every request is answered as a store
 *  with no refresh service answers (its 503 and reason), so a save there is told the dashboard is not rebuilt. */
async function call(method: "GET" | "POST", payload?: unknown) {
  if (IS_DEMO) return { http: 503, body: { error: DEMO_NO_REFRESH } as Record<string, unknown> };
  const res = await fetch(URL, payload === undefined ? { method }
    : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  noteStoreStatus(res.status);
  if (res.status === 401) return { http: 401, body: {} as Record<string, unknown> };
  const body = await res.json().catch(() => ({}));
  return { http: res.status, body };
}

/** `enabled`: someone is looking (the modal is open, the Data page is mounted) - the status is read
 *  when it turns on and again whenever `viewKey` changes (each new view of a shared hook); otherwise only
 *  when asked (`watch`). A run seen in flight is polled to its end even after `enabled` turns off, so
 *  a refresh started on one page and finished after the user left still reloads the data (once).
 *  `builtAt` (manifest.generated_at of the exports on screen): any read that finds an ingest newer than
 *  them ended well (`newerIngest`: another device's, or one this tab did not follow) reloads them once,
 *  quietly - no outcome line (`onQuiet` when given, else `onDone`, makes that reload); without it only the
 *  runs and ingests this tab saw under way reload. */
export function useRefresh(
  { enabled, onDone, onQuiet, viewKey = 0, builtAt }:
  { enabled: boolean; onDone: () => void | Promise<void>; onQuiet?: () => void | Promise<void>; viewKey?: number;
    builtAt?: string },
): RefreshApi {
  const [status, setStatus] = useState<RefreshStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  const [unavailableNote, setNote] = useState<string | null>(null);
  const [noService, setNoService] = useState(false);
  const [signedOut, setSignedOut] = useState(false);
  const [posting, setPosting] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Survives a page reload so a refresh started before a page reload is still recognised
  // as *mine* when it lands.
  const nonce = useRef<string | null>(
    typeof sessionStorage === "undefined" ? null : sessionStorage.getItem(NONCE_KEY),
  );
  const setNonce = (v: string | null) => {
    nonce.current = v;
    if (typeof sessionStorage === "undefined") return;
    if (v) sessionStorage.setItem(NONCE_KEY, v);
    else sessionStorage.removeItem(NONCE_KEY);
  };

  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;
  const onQuietRef = useRef(onQuiet);
  onQuietRef.current = onQuiet;
  const wasBusy = useRef(false);
  const wasIngesting = useRef(false);
  const first = useRef(true);
  /** reads sent so far (a read's number is its place in that order), and the number of the newest one that completed */
  const sentRef = useRef(0);
  const appliedRef = useRef(0);
  const [reads, setReads] = useState(0);
  const sent = useCallback(() => sentRef.current, []);
  /** until when (ms) an ingest the status shows waiting or running is polled; null: not followed */
  const [followUntil, setFollowUntil] = useState<number | null>(null);
  /** who asked for the ingest the follow watches */
  const [followCause, setFollowCause] = useState<IngestCause>("seen");
  /** someone just looked (a new view, or `watch()`), why, and the number of the read that look sent: the answer to that
   *  read or a later one, when it shows an ingest under way, starts a follow (an answer to an earlier read says nothing
   *  of the look) */
  const looked = useRef<{ cause: IngestCause; from: number } | null>(null);
  const visible = useTabVisible();
  /** the newest read got no answer at all (no service and a 401 are answers) */
  const [failed, setFailed] = useState(false);

  /** One read of the status, numbered as it is sent; an answer older than the one already shown is dropped. */
  const refetch = useCallback(async () => {
    const n = ++sentRef.current;
    const apply = (f: () => void) => {
      if (n < appliedRef.current) return;   // a newer read has answered already
      appliedRef.current = n;
      f();
      setReads(n);
    };
    try {
      const r = await call("GET");
      apply(() => {
        if (r.http !== 200) {
          // 503 with a reason = deliberately off (no --refresh-dir), not broken: a household that runs the ingest by
          // hand. Worth saying out loud: "unreachable" would send someone hunting a fault.
          const note = r.http === 401 ? null : typeof r.body?.error === "string" ? r.body.error : null;
          const none = r.http === 503 && note !== null;
          setNote(note);
          setNoService(none);
          setSignedOut(r.http === 401);
          setFailed(r.http !== 401 && !none);
          setUnreachable(true);
          return;
        }
        setFailed(false);
        setUnreachable(false);
        setNote(null);
        setNoService(false);
        setSignedOut(false);
        setStatus(r.body as RefreshStatus);
      });
    } catch {
      apply(() => {
        setFailed(true);
        setNote(null);
        setNoService(false);
        setUnreachable(true);
      });
    }
  }, []);

  /** until when (ms) a refresh seen in flight is polled; null: none seen, or it ended */
  const [runUntil, setRunUntil] = useState<number | null>(null);
  /** the run passed its bound without an end: not polled until someone opens a view */
  const [runLost, setRunLost] = useState(false);
  /** the run the bound was set for, by its `started_at` and `request_nonce`; null: none yet */
  const runKey = useRef<string | null>(null);

  // Load on open (and on each new view). A stored nonce whose run has already
  // finished belongs to an earlier page view — this load already fetched the
  // fresh exports, so drop it instead of announcing a completion nobody is
  // waiting for. Each new view also follows an ingest it finds under way, and a
  // run past its bound is followed again from this look.
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    setLoading(true);
    looked.current ??= { cause: "seen", from: sentRef.current + 1 };
    runKey.current = null;
    setRunLost(false);
    setRunUntil(null);
    void refetch().finally(() => {
      if (alive) setLoading(false);
    });
    return () => { alive = false; };
  }, [enabled, viewKey, refetch]);

  // A look that finds an ingest under way follows it for INGEST_FOLLOW_MS; an ingest no longer under way ends the
  // follow, and so does its bound, by itself: no poll, and no busy label, after it. Nothing is armed while nothing is
  // under way.
  useEffect(() => {
    if (!status) return;
    const under = ingestBusy(status.ingest);
    const look = looked.current;
    if (look !== null && appliedRef.current >= look.from) {
      looked.current = null;
      if (under) {
        setFollowUntil(clock.now().getTime() + INGEST_FOLLOW_MS);
        setFollowCause(look.cause);
        return;
      }
    }
    if (!under) setFollowUntil(null);
  }, [status]);
  useEffect(() => {
    if (followUntil === null) return;
    const id = setTimeout(() => setFollowUntil(null), Math.max(0, followUntil - clock.now().getTime()));
    return () => clearTimeout(id);
  }, [followUntil]);
  const following = followUntil !== null && ingestBusy(status?.ingest);

  // A refresh seen in flight is polled for RUN_FOLLOW_MS from the read that first found it; past that it has not
  // reported back, and it is left alone until someone opens a view. The bound is each run's own: a read whose
  // `started_at` or `request_nonce` differs from the run the bound was set for is another run (a manual refresh queued
  // behind a scheduled one starts the moment that one ends, so the status never stops reporting a run), and its bound
  // starts again from that read.
  useEffect(() => {
    if (!status) return;
    if (!isBusy(status)) {
      runKey.current = null;
      setRunUntil(null);
      setRunLost(false);
      return;
    }
    const key = `${status.started_at ?? ""}|${status.request_nonce ?? ""}`;
    if (key === runKey.current) return;
    runKey.current = key;
    setRunUntil(clock.now().getTime() + RUN_FOLLOW_MS);
    setRunLost(false);
  }, [status]);
  useEffect(() => {
    if (runUntil === null) return;
    const id = setTimeout(() => setRunLost(true), Math.max(0, runUntil - clock.now().getTime()));
    return () => clearTimeout(id);
  }, [runUntil]);
  const running = isBusy(status) && !runLost;

  // Poll while something is in flight — whether or not anyone is still looking:
  // the run was seen, so its end must still reload the numbers. setTimeout, not
  // setInterval: the next poll is scheduled off the last *response*, so a slow
  // store can't pile up. Idle costs nothing (no status, or a terminal one). An
  // ingest without a fetch is polled the same way while it is followed. A read
  // that got no answer is tried again at the slow cadence (LOOK_EVERY_MS), not
  // the fast one; nothing is polled while the tab is hidden, after the store's
  // answer that there is no refresh service (nothing can be under way there), or
  // once the sign-in has ended. A poll that finds another read went out since it
  // was armed stands down: that read's answer arms the next.
  useEffect(() => {
    if ((!running && !following) || noService || signedOut || !visible) return;
    const armedAt = sentRef.current;
    const id = setTimeout(() => { if (sentRef.current === armedAt) void refetch(); }, failed ? LOOK_EVERY_MS : POLL_MS);
    return () => clearTimeout(id);
  }, [running, following, status, reads, noService, signedOut, visible, failed, refetch]);

  // busy -> terminal is the moment new exports are on disk: refresh.sh writes
  // its `done` status only after export() has finished, so refetching here can
  // never read a half-rebuilt data set. An ingest without a fetch records its end
  // after the ingest has written its exports too, so its end reloads them as well
  // (once: a refresh's own ingest step is left to the refresh's end).
  const builtAtRef = useRef(builtAt);
  builtAtRef.current = builtAt;
  /** the last ingest end a reload was made for: one end reloads the exports once, however many reads see it */
  const reloadedFor = useRef<string | null>(null);
  useEffect(() => {
    if (!status) return;
    const busy = isBusy(status);
    const ingesting = ingestBusy(status.ingest);
    let reload = false;
    if (first.current) {
      first.current = false;
      if (!busy && nonce.current && status.request_nonce === nonce.current) {
        setNonce(null);
      }
    } else {
      if (!wasBusy.current && busy) setOutcome(null);   // a new run (ours or scheduled) owns the readout
      if (wasBusy.current && !busy) {
        const mine = !!nonce.current && status.request_nonce === nonce.current;
        if (mine) setNonce(null);
        setOutcome({
          mine, ok: status.rc === 0, stepsFailed: status.steps_failed,
          finishedAt: status.finished_at, seenAt: clock.nowISO(),
        });
        // Reload even for a scheduled run we merely watched — the numbers on
        // screen are stale either way once it lands.
        reload = true;
      }
      if (wasIngesting.current && !ingesting && !busy) reload = true;
    }
    wasBusy.current = busy;
    wasIngesting.current = ingesting;
    // an ingest this tab never saw under way (another device's rebuild, a scheduled refresh while nobody looked) whose
    // exports are newer than the ones on screen: reloaded quietly, the first read included
    const end = status.ingest?.last_finished_at ?? null;
    const quiet = !reload && !busy && !ingesting && newerIngest(status.ingest, builtAtRef.current) && end !== reloadedFor.current;
    if (reload || quiet) {
      reloadedFor.current = end;
      void (quiet && onQuietRef.current ? onQuietRef.current() : onDoneRef.current());
    }
  }, [status]);

  const watch = useCallback((follow = true, cause: IngestCause = "seen") => {
    if (follow) looked.current = { cause, from: sentRef.current + 1 };
    void refetch();
  }, [refetch]);

  /** a press answered 401: the sign-in has ended, as a read's 401 says it (nothing redirects) */
  const signOut = useCallback(() => {
    setSignedOut(true);
    setNoService(false);
    setFailed(false);
    setNote(null);
    setUnreachable(true);
  }, []);

  const requestIngest = useCallback(async (mode: IngestMode = "ingest"): Promise<IngestAsk> => {
    try {
      const r = await call("POST", { mode });
      if (r.http === 401) {
        signOut();
        return { kind: "signed-out" };
      }
      if (r.http === 202) return { kind: "accepted" };
      if (r.http === 503) return { kind: "no-service" };
      if (r.http === 502 || r.http === 504) return { kind: "unreached" };
      const reason = r.body?.reason ?? r.body?.error;
      return { kind: "refused", reason: typeof reason === "string" ? reason : `HTTP ${r.http}` };
    } catch {
      return { kind: "unreached" };
    }
  }, [signOut]);

  const trigger = useCallback(async () => {
    setPosting(true);
    setError(null);
    setOutcome(null);
    try {
      const r = await call("POST");
      if (r.http === 401) {
        signOut();
        return;
      }
      // 202 = queued for me. 200 = a run was already on its way and I should
      // watch that one instead (no budget spent). Both give a nonce to follow;
      // a scheduled run has none, and the generic busy->terminal watch covers it.
      if (r.http === 202 || r.http === 200) {
        if (typeof r.body?.nonce === "string") setNonce(r.body.nonce);
        wasBusy.current = true;   // don't miss a run that finishes before the first poll
      } else if (r.http !== 429 && r.http !== 503) {
        setError(r.body?.error ? String(r.body.error) : `Refresh failed (HTTP ${r.http})`);
      }
      // Always re-read: the response body carries the new budget, and GET is
      // the single source the button renders from.
      await refetch();
    } catch {
      setUnreachable(true);
    } finally {
      setPosting(false);
    }
  }, [refetch, signOut]);

  return { status, loading, unreachable, unavailableNote, noService, signedOut, busy: running || following, runLost,
           ingestBusy: following, ingestCause: following ? followCause : null, posting, outcome, error, reads, sent,
           trigger, requestIngest, watch };
}

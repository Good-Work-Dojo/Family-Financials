// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { INGEST_FOLLOW_MS, ingestBusy, ingestEnded, ingestRunning, type IngestStatus } from "../data/useRefresh";

// Following an ingest without a fetch that this tab asked for, to its end: the one rule every asker shares (the manual
// assets' save, the refresh control's "Rebuild from files"). The store's ingest block has no nonce
// (docs/design/information-architecture.md): an ingest this tab asked for has ended when nothing is wanted any more and
// the last ingest's end is at or after its start, which started after the ask (`ingestEnded`); `last_rc` 0 is done and
// the exports reload, any other code failed (`ingestFailure`), and 143 and -2 stay wanted (the refresher runs an
// interrupted ingest again), so they are followed on. Only answers to reads SENT after the ask landed count (reads are
// numbered as they are sent, data/useRefresh.ts), so a slow earlier read never speaks for it. A read that fails (no
// answer, a 502 while the store restarts) says nothing and the follow goes on; only the store's own answer that there
// is no refresh service (its 503) ends it at once. The follow ends at a bound and never spins for ever.
// `data/useIngestFollow.ts` runs this reducer against the shared refresh.

/** How long an ask is followed: the refresh view's own bound (data/useRefresh.ts) */
export const FOLLOW_MS = INGEST_FOLLOW_MS;
/** How long the one look past the bound has to settle the follow: a look that never answers, or an ingest's end whose
 *  reload never brings new exports, ends it as `later` once this has passed */
export const FOLLOW_GRACE_MS = 10_000;

/** where an ask stands: `saving` until the store answers the request (a save, or the ingest request itself); `refused`
 *  (the asker shows the reason); then, once it landed, `asking` until the refresh status is read, `waiting` while the
 *  ingest it asked for is wanted or running, `reloading` once that ingest succeeded and until the exports reloaded,
 *  `done` (the exports hold its result), `failed` (the ingest's exit code), `later` (no ingest was asked for, the store
 *  predates the ingest block, or the bound passed; `started` says whether the ingest was seen to start) or
 *  `no-service` (the store answered that there is no refresh service: the ingest is run by hand) */
export type FollowPhase = "idle" | "saving" | "refused" | "asking" | "waiting" | "reloading" | "done" | "failed" | "later"
  | "no-service";

export interface Follow {
  phase: FollowPhase;
  /** when the request landed (ms), for the bound */
  savedAt: number | null;
  /** the exports' ingest instant when the ingest's end was seen: the reload of that ingest's exports changes it */
  generatedAt: string | null;
  /** the status reads SENT when it landed (or when the bound passed): only the answer to a read sent after that says
   *  anything about this ask's ingest */
  readsAt: number;
  rc: number | null;
  /** the asset ids a save removed (empty for an ask that saves nothing) */
  removed: string[];
  /** the bound has passed: one more look decides, and an ingest still under way then ends the follow */
  bound: boolean;
  /** the store said it touched the ingest marker (a 202 to `POST {"mode": "ingest"}`): an ingest seen ended at the first
   *  read after the landing started after the ask, so it answers it. A save says nothing of the marker, so such a read
   *  there means the marker could not be written (`later`). */
  marked: boolean;
  /** an ingest was seen running with nothing more wanted, so it started after the ask: a follow that ends at its bound
   *  without having seen this never started (the refresh service may be stopped) */
  started: boolean;
  /** some read after the landing got an answer: a follow that ends with none never knew whether the ingest ran (the
   *  store could not be reached the whole time) */
  answered: boolean;
}

export const IDLE_FOLLOW: Follow = {
  phase: "idle", savedAt: null, generatedAt: null, readsAt: 0, rc: null, removed: [], bound: false, marked: false,
  started: false, answered: false,
};

/** how an ingest that did not succeed ended, in words: its exit code as the refresher records it
 *  (deploy/refresh_state.py: -1 timed out, -2 found dead, 143 stopped from outside, 128 + N killed by signal N) */
export function ingestFailure(rc: number): string {
  if (rc === -1) return "timed out";
  if (rc === -2) return "stopped before it finished";
  if (rc === 143) return "was stopped";
  if (rc > 128 && rc < 160) return `was stopped (code ${rc})`;
  return `failed (code ${rc})`;
}

export type FollowEvent =
  | { type: "save"; removed: string[] }
  /** the store took the request; `reads` the status reads sent by then; `marked` when it says it touched the marker */
  | { type: "landed"; at: number; reads: number; marked?: boolean }
  | { type: "refused" }
  /** the newest answered read of the refresh status, `reads` its number in the order reads were sent: `ingest` null
   *  before any status was read, undefined from a store with none; `failed` the read got no answer (none, a 502, a
   *  sign-in that ended); `noService` the store answered that there is no refresh service (its 503); `generatedAt` the
   *  exports' ingest instant as the read lands */
  | { type: "status"; reads: number; ingest: IngestStatus | undefined | null; failed: boolean; noService: boolean;
      generatedAt: string }
  | { type: "exports"; generatedAt: string }
  /** the bound's timer fired (late, for a tab that slept): `reads` the status reads sent by then */
  | { type: "tick"; at: number; reads: number }
  /** FOLLOW_GRACE_MS has passed since the bound */
  | { type: "grace" }
  | { type: "reset" };

/** the phases a follow has ended in */
export const FOLLOW_ENDED: ReadonlySet<FollowPhase> = new Set(["idle", "refused", "done", "failed", "later", "no-service"]);

/** The follow as a reducer (pure). A request landing starts it; the answer to each status read sent after the landing
 *  moves it: the store's answer that there is no refresh service (its 503) ends it as `no-service`; a read that failed
 *  says nothing (the follow goes on to its bound); a status with no ingest block (an older store) ends it as `later`; an
 *  ingest wanted or running is `waiting` (one running with nothing more wanted is `started`). A read that finds nothing
 *  under way has seen the ingest
 *  end once it saw it under way before, or at once when the store said it touched the marker (nothing is wanted after
 *  that touch only when an ingest started after it); otherwise it ends as `later` (a save's marker could not be
 *  written, so the next scheduled refresh's ingest reads it). An end with rc 0 is `reloading` until the exports'
 *  ingest instant moves from what it was when the end was seen (the end's reload brings that ingest's exports), then
 *  `done`; any other code is `failed` (143 and -2, an interrupted ingest, stay wanted and are followed on: the refresher
 *  runs them again). When FOLLOW_MS has passed since the landing (`tick`) an unfinished follow looks once more
 *  (`bound`; the asker reads the status): a tab that slept past the bound and wakes after the ingest ended sees it end;
 *  one still under way then, or a look that failed, ends the follow as `later`, and so does a bound reached while
 *  reloading. The
 *  look has FOLLOW_GRACE_MS (`grace`): a phase still open then (the look never answered, or it saw the end and the
 *  reload never moved the exports' ingest instant) ends as `later`, so a follow always ends. A request that never lands
 *  (the store unreachable) has no bound: nothing is asked of the refresh service meanwhile. */
export function followStep(f: Follow, ev: FollowEvent): Follow {
  switch (ev.type) {
    case "reset": return IDLE_FOLLOW;
    case "save": return { ...IDLE_FOLLOW, phase: "saving", removed: ev.removed };
    case "landed":
      return f.phase === "saving" ? { ...f, phase: "asking", savedAt: ev.at, readsAt: ev.reads, marked: ev.marked ?? false } : f;
    case "refused": return f.phase === "saving" ? { ...f, phase: "refused" } : f;
    case "tick":
      if (FOLLOW_ENDED.has(f.phase) || f.phase === "saving" || f.bound || f.savedAt === null || ev.at - f.savedAt < FOLLOW_MS) return f;
      return f.phase === "reloading" ? { ...f, phase: "later" } : { ...f, bound: true, readsAt: Math.max(f.readsAt, ev.reads) };
    case "grace":
      return f.bound && (f.phase === "asking" || f.phase === "waiting" || f.phase === "reloading") ? { ...f, phase: "later" } : f;
    case "exports":
      return f.phase === "reloading" && ev.generatedAt !== f.generatedAt ? { ...f, phase: "done" } : f;
    case "status": {
      if ((f.phase !== "asking" && f.phase !== "waiting") || ev.reads <= f.readsAt) return f;
      if (ev.noService) return { ...f, phase: "no-service" };
      const next = ((): Follow => {
        if (ev.failed) return f;   // no answer says nothing of the ingest
        if (!ev.ingest) return ev.ingest === null ? f : { ...f, phase: "later", answered: true };
        const a = { ...f, answered: true, started: f.started || (ingestRunning(ev.ingest) && !ev.ingest.wanted) };
        if (ingestBusy(ev.ingest)) return { ...a, phase: "waiting" };
        if ((f.phase === "asking" && !f.marked) || !ingestEnded(ev.ingest)) return { ...a, phase: "later" };
        const rc = ev.ingest.last_rc;
        return rc === 0 ? { ...a, phase: "reloading", generatedAt: ev.generatedAt, started: true }
          : rc === null ? { ...a, phase: "later" } : { ...a, phase: "failed", rc, started: true };
      })();
      // past the bound, the one more look ends the follow unless it saw the ingest end
      return f.bound && (next.phase === "asking" || next.phase === "waiting") ? { ...next, phase: "later" } : next;
    }
  }
}

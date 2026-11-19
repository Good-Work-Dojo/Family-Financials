// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useData } from "../data/DataContext";
import { LOGIN_PATH } from "../data/session";
import type { Manifest } from "../data/types";
import { useIngestFollow } from "../data/useIngestFollow";
import {
  DEMO_NO_REFRESH, type IngestAsk, type IngestCause, ingestRunning, LOOK_EVERY_MS, useRefresh, type Outcome,
  type RefreshApi, type RefreshStatus, useTabVisible,
} from "../data/useRefresh";
import { clock } from "../lib/clock";
import { IS_DEMO } from "../lib/demo";
import { fmtDateTime, fmtRelative, hoursSince } from "../lib/format";
import { type Follow, ingestFailure } from "../lib/ingestFollow";
import { usePointerWords } from "../lib/pointerWords";
import { maskMoneyText } from "../lib/privacy";
import { TAP_AREA } from "./ui";
import { freshness, type FreshnessTone, type RefreshSchedule, scheduleSentence } from "../lib/refreshSchedule";
import { useFocusTrap } from "../lib/useFocusTrap";

/** How fresh the data is, and a way to ask for more — in the words of the household's own setup.
 *
 *  With a bank feed (`manifest.setup.feed`) the pill reads the age of the last feed fetch
 *  (manifest.simplefin_fetched_at, NOT generated_at: the latter is the ingest time, which moves on every deploy and
 *  even when the fetch failed), toned by the schedule the refresh status reports (lib/refreshSchedule.ts: amber past
 *  one and a quarter of its longest gap, red past three; a once-a-day schedule's when the status reports none;
 *  neutral until the status has answered). That is the stale-data tripwire — AsOfBadge only ambers at 45 days, which
 *  never fires for a dead scheduler. The button is "Refresh now": a fetch and then an ingest. With no feed there is
 *  nothing to fetch: the pill reads when the dashboard was last rebuilt (toned by the same thresholds when the status
 *  reports a schedule, neutral without one), the button is "Rebuild from files" (an ingest without a fetch, followed by
 *  the shared follow, data/useIngestFollow.ts), and nothing says feed, fetch or pull. While the store does not answer,
 *  or answers that there is no refresh service, there is nothing to press, and the line says so; the button comes back
 *  with the next answer. Every clock time is the household's (its zone, `assumptions.household.timezone`). */

const TONE_CLS: Record<FreshnessTone, string> = {
  fresh: "bg-zinc-800 text-zinc-400",
  amber: "bg-amber-900/50 text-amber-300",
  never: "bg-amber-900/50 text-amber-300",
  red: "bg-red-900/50 text-red-300",
};

/** why the status could not be read just now, for the pill: no answer, or a background read answered 401 */
export type StatusUnavailable = "unreachable" | "signed-out" | null;

/** The pill: its label, colour, tooltip and accessible name, from the manifest and the schedule — undefined until the
 *  status has answered (the pill is neutral meanwhile, so it never flips a second after the page opens), null when it
 *  reports none — and why the status could not be read, when it could not. A sign-in that ended is said on the pill
 *  itself, in a warning tone (a phone has no tooltip). `Click` is the pointer's word for what opens the panel ("Tap" for
 *  a finger, "Click" for a mouse: lib/pointerWords.ts). Exported for its unit test. */
export function pillState(m: Pick<Manifest, "simplefin_fetched_at" | "generated_at" | "setup">,
  schedule: RefreshSchedule | null | undefined, zone: string | null, now: Date, unavailable: StatusUnavailable = null,
  Click: "Tap" | "Click" = "Tap",
): { label: string; cls: string; title: string; aria: string } {
  if (unavailable === "signed-out") {
    return { label: "signed out", cls: TONE_CLS.amber, aria: "Signed out — open the data refresh panel to sign in again",
             title: `Your sign-in has ended. ${Click} to sign in again.` };
  }
  const why = unavailable === "unreachable" ? " The refresh status is unavailable right now." : "";
  if (!m.setup.feed) {
    const label = `rebuilt ${fmtRelative(m.generated_at, zone, now)}`;
    const { tone, hint } = schedule === undefined ? { tone: "fresh" as const, hint: "" }
      : freshness(hoursSince(m.generated_at, now), schedule, "rebuild");
    return { label, cls: TONE_CLS[tone], aria: `Dashboard ${label} — open the data refresh panel`,
             title: `Dashboard rebuilt ${fmtDateTime(m.generated_at, zone, now)}.${hint ? ` ${hint}` : ""}${why} `
               + `${Click} for details or to rebuild from files.` };
  }
  const at = m.simplefin_fetched_at;
  if (at === null) {
    const { hint } = freshness(null, schedule);
    return { label: "not fetched yet", cls: TONE_CLS.never, aria: "Bank feed not fetched yet — open the data refresh panel",
             title: `${hint}${why} ${Click} for details or to refresh now.` };
  }
  const { tone, hint } = schedule === undefined ? { tone: "fresh" as const, hint: "" } : freshness(hoursSince(at, now), schedule);
  const label = fmtRelative(at, zone, now);
  return { label, cls: TONE_CLS[tone], aria: `Bank feed fetched ${label} — open the data refresh panel`,
           title: `Last feed fetch ${fmtDateTime(at, zone, now)}.${hint ? ` ${hint}` : ""}${why} ${Click} for details or to refresh now.` };
}

/** `bubble`: the "Updated" line of a quiet reload hangs below the pill (the sidebar's); the phone's top bar says it in a
 *  strip of its own instead (RefreshUpdatedStrip), so it never hangs over the page under the bar */
export function RefreshPill(
  { onClick, className = "", bubble = true }: { onClick: () => void; className?: string; bubble?: boolean },
) {
  const { data } = useData();
  const zone = data.assumptions.household.timezone;
  const ctx = useContext(RefreshCtx);
  const pill = ctx?.pill ?? NO_PILL;
  const { Click } = usePointerWords();
  const { label, cls, title, aria } = pillState(data.manifest, pill.schedule, zone, clock.now(), pill.unavailable, Click);
  // the demo build is never refreshed: no pill opens a refresh there
  if (IS_DEMO) return null;
  // the pill keeps its own size; on a phone its hit area is 44px tall (TAP_AREA), drawn by nothing
  return (
    <span className="relative inline-flex shrink-0">
      <button
        onClick={onClick}
        aria-label={aria}
        title={title}
        className={`inline-flex min-h-[36px] shrink-0 items-center gap-1 rounded-full px-2 text-[10px] hover:brightness-125 md:min-h-0 md:py-0.5 ${TAP_AREA} ${cls} ${className}`}
      >
        <span aria-hidden="true">↻</span>
        <span className="whitespace-nowrap">{label}</span>
      </button>
      {/* a quiet reload brought a newer build: a line that hangs below the pill (out of the flow: nothing moves; taps
          go through it to what lies under) and goes by itself; the provider's one status region announces it, so this
          copy is not read twice */}
      {bubble && pill.updated !== null && (
        <span aria-hidden="true" data-testid="refresh-updated"
          className="pointer-events-none absolute left-0 top-full z-40 mt-1 whitespace-nowrap rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-[10px] text-zinc-200 shadow-lg">
          {updatedText(pill.updated, zone)}
        </span>
      )}
    </span>
  );
}

/** The "Updated" line of a quiet reload on a phone: a strip of the sticky top bar itself, under its row of controls (the
 *  bar is that much taller while it shows, so the page moves down rather than being covered), for as long as the pill's
 *  line would hang; the provider's status region announces it, so it is not read twice. */
export function RefreshUpdatedStrip() {
  const { data } = useData();
  const ctx = useContext(RefreshCtx);
  const updated = ctx?.pill.updated ?? null;
  if (updated === null) return null;
  return (
    <p aria-hidden="true" data-testid="refresh-updated"
      className="border-t border-zinc-800 px-3 py-1 text-[11px] leading-4 text-zinc-200">
      {updatedText(updated, data.assumptions.household.timezone)}
    </p>
  );
}

/** the line a quiet reload of a newer build shows: when that build was made */
const updatedText = (builtAt: string, zone: string | null) =>
  `Updated: the dashboard was rebuilt ${fmtRelative(builtAt, zone)}.`;

/** one dated row of the readout: its age, then the instant on the household's clock (an age of a week or more is the
 *  day itself, so the instant alone says it) */
function Row({ label, at, never, zone, now }: { label: string; at: string | null; never: string; zone: string | null; now: Date }) {
  const recent = at !== null && (hoursSince(at, now) ?? Infinity) < 7 * 24;
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-xs text-zinc-300">{label}</span>
      <span className="shrink-0 text-right text-[11px] tabular-nums text-zinc-500">
        {at === null ? never : recent ? <>{fmtRelative(at, zone, now)} · {fmtDateTime(at, zone, now)}</> : fmtDateTime(at, zone, now)}
      </span>
    </div>
  );
}

/** what the control offers: a button's label (null: nothing to press), whether it is disabled, and the line under it */
export interface ControlState { label: string | null; disabled: boolean; note: string | null }

/** Why nothing can be started right now, in words: the store answered that there is no refresh service (its 503 —
 *  a household that runs the ingest by hand; `unavailableNote` its own reason), the sign-in has ended, or the store
 *  did not answer at all */
export function serviceNote({ noService = false, unavailableNote = null, signedOut = false }: {
  noService?: boolean; unavailableNote?: string | null; signedOut?: boolean;
}): string {
  if (signedOut) return "Your sign-in has ended.";
  if (noService) {
    return `No refresh service answers here${unavailableNote ? ` (${unavailableNote})` : ""}, so nothing can be started `
      + "from the dashboard: re-run the ingest to update it.";
  }
  return "The dashboard's store is unreachable, so nothing can be started from here: try again in a moment, or re-run the "
    + "ingest to update the dashboard.";
}

/** what the store's state of the refresh service is, as the control reads it (RefreshApi's live flags; `runLost`: a
 *  refresh seen in flight passed its bound with no end, and is no longer polled) */
interface Service {
  unreachable: boolean; unavailableNote?: string | null; noService?: boolean; signedOut?: boolean; runLost?: boolean;
}

/** a refresh past its bound: still in flight as far as the last answer says, but it has not reported back */
const NOT_REPORTED = "The refresh has not reported back — it may have stopped. Open this again later to look.";

/** The feed household's button and the line under it: everything the server told us about why it will or won't run
 *  right now. `ingest`: an ingest without a fetch is followed, and who asked for it. Exported for its unit test. */
export function buttonState({ status: s, unreachable, unavailableNote = null, noService = false, signedOut = false,
  runLost = false, posting, ingest = null, zone = null }: Service & {
  status: RefreshStatus | null; posting: boolean; ingest?: IngestCause | null; zone?: string | null;
}): ControlState {
  if (unreachable) return { label: null, disabled: true, note: serviceNote({ noService, unavailableNote, signedOut }) };
  if (posting) return { label: "Starting…", disabled: true, note: null };
  if (!s) return { label: "Refresh now", disabled: true, note: null };
  if (s.state === "queued")
    return { label: "Queued…", disabled: true, note: runLost ? NOT_REPORTED : "Waiting for the refresh worker." };
  if (s.state === "running")
    return { label: "Refreshing…", disabled: true,
             note: runLost ? NOT_REPORTED
               : s.trigger === "schedule"
               ? "A scheduled refresh is already running."
               : "This usually takes about 30 seconds." };
  if (s.state === "worker_unavailable")
    return { label: "Refresh now", disabled: true,
             note: "The refresh service has not picked up the queued request — it may be stopped." };
  // an ingest without a fetch (a saved change to the hand-entered assets, or a rebuild someone asked for): the
  // dashboard is being rebuilt from the files it has, which spends none of the bank's budget
  if (ingest !== null)
    return { label: "Updating…", disabled: true,
             note: ingest === "save" ? "Rebuilding the dashboard with a saved change — no bank pull."
               : "A rebuild is under way — no bank pull." };
  if (!s.can_refresh) {
    const mins = Math.max(1, Math.ceil(s.retry_after_s / 60));
    if (s.reason === "daily_cap")
      return { label: "Refresh now", disabled: true,
               note: `All ${s.limit_per_day} manual refreshes used today — resets at midnight.` };
    return { label: "Refresh now", disabled: true,
             note: `Data was pulled ${s.last_refresh_at ? fmtRelative(s.last_refresh_at, zone) : "just now"}`
                   + ` — try again in ${mins} min.` };
  }
  return { label: "Refresh now", disabled: false, note: null };
}

/** a rebuild's follow is under way */
const rebuilding = (f: Follow) => f.phase === "asking" || f.phase === "waiting" || f.phase === "reloading";

/** a status list of text as the readout takes it: its strings, or none when it is no list */
const texts = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
/** a list in words: "A", "A and B", "A, B and C" */
const listWords = (xs: string[]): string => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

/** What the last refresh's fetch learned of the bank feed, in words: the one thing to do for the connections whose bank
 *  asks the household to sign in again (at the feed provider), and each error the feed reported, as it worded it (its
 *  amounts masked with the rest of the page's). A store that predates both lists says only its connections needing
 *  re-authorization, in its own words. Exported for its unit test. */
export function feedProblemLines(s: RefreshStatus | null): { reauth: string | null; errors: string[] } {
  if (!s) return { reauth: null, errors: [] };
  if (s.reauth_needed === undefined && s.feed_errors === undefined) {
    const w = texts(s.warnings);
    return { reauth: w.length === 0 ? null : `${w.join(", ")} ${w.length === 1 ? "needs" : "need"} re-authorization in `
      + "SimpleFIN — those balances will keep going stale.", errors: [] };
  }
  const names = texts(s.reauth_needed);
  const reauth = names.length === 0 ? null : `Sign in to ${listWords(names)} again at SimpleFIN Bridge: until you do, the `
    + `bank feed cannot bring ${names.length === 1 ? "its" : "their"} balances up to date.`;
  const errors = texts(s.feed_errors).map((e) => {
    const said = maskMoneyText(e);
    return `The bank feed reported a problem with ${said}${/[.!?…]$/.test(said) ? "" : "."}`;
  });
  return { reauth, errors };
}

/** The line that stands in for the schedule's sentence while the store knows none (it reports a null schedule: the
 *  scheduler has not written one yet), in the words of the household's setup; null while it knows one, or before an
 *  answer. Exported for its unit test. */
export function unknownScheduleLine(s: RefreshStatus | null, feed: boolean): string | null {
  if (!s || s.schedule !== null) return null;
  return `The times of the automatic ${feed ? "refresh" : "rebuild"} are not known yet: they show once the scheduler `
    + "has started.";
}

/** The button of a household with no bank feed, "Rebuild from files" (an ingest without a fetch), and the line under
 *  it, by the status, the rebuild's own follow (`follow`) and an ingest the shared refresh follows for someone else
 *  (`ingest`: a save, or one found under way). Whether there is a refresh service is read live: the button comes back
 *  with the next answer. No sentence names a feed, a fetch or a pull. Exported for its unit test. */
export function rebuildState({ status: s, unreachable, unavailableNote = null, noService = false, signedOut = false,
  runLost = false, follow, ingest = null }: Service & { status: RefreshStatus | null; follow: Follow; ingest?: IngestCause | null }): ControlState {
  if (unreachable) return { label: null, disabled: true, note: serviceNote({ noService, unavailableNote, signedOut }) };
  if (follow.phase === "saving") return { label: "Starting…", disabled: true, note: null };
  if (!s) return { label: "Rebuild from files", disabled: true, note: null };
  if (s.state === "queued")
    return { label: "Queued…", disabled: true, note: runLost ? NOT_REPORTED : "Waiting for the refresh worker." };
  if (s.state === "running")
    return { label: "Rebuilding…", disabled: true,
             note: runLost ? NOT_REPORTED
               : s.trigger === "schedule" ? "A scheduled refresh is rebuilding the dashboard." : "A refresh is rebuilding the dashboard." };
  if (rebuilding(follow)) {
    return { label: "Rebuilding…", disabled: true,
             note: follow.phase === "reloading" ? "Loading the rebuilt dashboard."
               : ingestRunning(s.ingest) ? "Reading the files and rebuilding the dashboard."
               : "It starts within about a minute of being asked." };
  }
  if (ingest === "save") return { label: "Rebuilding…", disabled: true, note: "Rebuilding the dashboard with a saved change." };
  if (ingest !== null) return { label: "Rebuilding…", disabled: true, note: "A rebuild is under way." };
  return { label: "Rebuild from files", disabled: false, note: "Reads the household's files again and rebuilds the dashboard." };
}

/** what the last rebuild this tab asked for came to, in a line (`ask`: how the store answered the request); null while
 *  it is under way, or with nothing to say (no refresh service is the control's own line, read live) */
export function rebuildLine(f: Follow, ask: IngestAsk | null): { text: string; ok: boolean } | null {
  switch (f.phase) {
    case "done": return { text: "Rebuilt — the numbers on screen are current.", ok: true };
    case "failed":
      return { text: `The rebuild ${ingestFailure(f.rc ?? 0)}. The dashboard still shows what it was last built from.`, ok: false };
    case "later":
      return { text: !f.answered ? "The dashboard's store could not be reached since the rebuild was asked for: the "
          + "dashboard updates once it answers and the rebuild has run."
        : f.started ? "The rebuild has not finished yet: the dashboard updates once it has."
        : "The rebuild has not started — the refresh service may be stopped.", ok: false };
    case "refused": {
      if (ask?.kind === "signed-out") return null;   // the control's own line says it, with the way to sign in
      if (ask?.kind === "unreached") {
        return { text: "The request did not reach the dashboard's store, so nothing was rebuilt: try again in a moment.", ok: false };
      }
      const reason = ask?.kind === "refused" ? ask.reason : null;
      return { text: reason === "ingest_cap" ? "Today's rebuilds are used up: try again after midnight."
        : `The rebuild was refused${reason ? `: ${reason}` : ""}.`, ok: false };
    }
    default: return null;
  }
}

/** How long a finished run's "what it did" line stays on the readout. */
export const OUTCOME_TTL_MS = 15 * 60_000;

/** How often a visible tab looks at the refresh status by itself (data/useRefresh.ts, the slow cadence): the schedule
 *  the pill and the readout read arrives with the first look, and an ingest another device asked for (or a scheduled
 *  refresh) reloads the exports here within about a minute of ending (useRefresh `builtAt`). A hidden tab looks at
 *  nothing, and neither does one whose store answered that there is no refresh service or whose sign-in has ended (it
 *  looks again when the tab comes back, or a view opens). */
export { LOOK_EVERY_MS };
/** The first look waits this long, and goes only when no other read went out meanwhile (the Data page's own read
 *  as the page opens): one read on load, not two. */
export const LOOK_FIRST_MS = 100;
/** How long the "Updated" line after a quiet reload stays. */
export const UPDATED_LINE_MS = 8_000;

/** The instant (ms) the outcome's age counts from: the server's finished_at, else when this client saw
 *  the run end. NaN when the stamp is unparseable. */
const outcomeEnded = (outcome: Outcome): number => Date.parse(outcome.finishedAt ?? outcome.seenAt);

/** Whether the "what the last run did" line should show: never while a run is in flight, never
 *  over a stalled status (the didn't-finish notice is the truth then), never once a newer run has
 *  started, and only for OUTCOME_TTL_MS after the run finished (the server's finished_at, else the
 *  instant this client saw it end) - a banner from yesterday is noise, not news. Pure: `now` is
 *  passed in (RefreshProvider owns the clock).
 *
 *  "Newer" is judged on the same anchor as the age: the server's own finished_at against the
 *  server's started_at, so a phone whose clock runs behind never retires the outcome of the very
 *  run it just watched. Only a status with no finished_at falls back to the client's instant. */
export function outcomeVisible(outcome: Outcome | null, status: RefreshStatus | null, busy: boolean, now: Date): boolean {
  if (!outcome || busy || status?.state === "stalled") return false;
  const ended = outcomeEnded(outcome);
  if (!Number.isFinite(ended)) return false;
  if (status?.started_at && Date.parse(status.started_at) > ended) return false;
  return now.getTime() - ended <= OUTCOME_TTL_MS;
}

/** The last refresh's outcome in the words of a household with no bank feed: its scheduled refresh's fetch has nothing
 *  to fetch, so only its ingest step says whether the dashboard was rebuilt */
function noFeedOutcome(o: Outcome): { text: string; ok: boolean } {
  const rebuilt = !o.stepsFailed.includes("ingest") && (o.ok || o.stepsFailed.length > 0);
  return rebuilt ? { text: "Rebuilt — the numbers on screen are current.", ok: true }
    : { text: "The last refresh could not rebuild the dashboard; it still shows what it was last built from.", ok: false };
}

/** The stalled notice, for the button actually shown: the way to start another run when one can be pressed, nothing
 *  more when none can */
export function stalledNotice(feed: boolean, ctl: ControlState): string {
  if (ctl.label === null || ctl.disabled) return "The last refresh didn't finish.";
  return feed ? "The last refresh didn't finish. You can start another one."
    : "The last refresh didn't finish. You can rebuild from files.";
}

interface Rebuild {
  follow: Follow;
  /** how the store answered the request (null before one, or while it is sent) */
  ask: IngestAsk | null;
  run: () => Promise<void>;
}

/** what the pill reads from the shared refresh */
interface PillInfo {
  /** undefined until the status has answered; null when it reports no schedule (or there is no refresh service) */
  schedule: RefreshSchedule | null | undefined;
  unavailable: StatusUnavailable;
  /** the generated_at of a newer build a quiet reload brought, while its line shows */
  updated: string | null;
}
const NO_PILL: PillInfo = { schedule: undefined, unavailable: null, updated: null };

interface SharedRefresh {
  api: RefreshApi;
  /** register a view (the open modal, the mounted Data panel); returns the unregister */
  view: () => () => void;
  /** the readout's clock: set when an outcome appears and again the instant it expires, so an idle page
   *  ages the line out without anything else re-rendering it (the only clock read here) */
  now: Date;
  /** "Rebuild from files": the request and its follow, which outlive the view that started them */
  rebuild: Rebuild;
  pill: PillInfo;
}
const RefreshCtx = createContext<SharedRefresh | null>(null);

/** a visible tab's own looks at the status: the first a moment after the page opens unless another read went out
 *  meanwhile, then one a minute after the last read of any kind (a view's, a poll's), at most; none while the tab is
 *  hidden, one at once when it comes back after a minute or more; and none on a timer once the store has answered that
 *  there is no refresh service or the sign-in has ended (coming back to the tab looks once). `on` false: none at all. */
function useLooks(api: RefreshApi, on: boolean) {
  const { reads, watch, sent, noService, signedOut } = api;
  const visible = useTabVisible();
  const stopped = noService || signedOut;
  const wasVisible = useRef(visible);
  useEffect(() => {
    const back = visible && !wasVisible.current;
    wasVisible.current = visible;
    if (on && back && stopped) watch(false);
  }, [on, visible, stopped, watch]);
  const lastRead = useRef<number | null>(null);
  useEffect(() => { if (reads > 0) lastRead.current = clock.now().getTime(); }, [reads]);
  useEffect(() => {
    if (!on || !visible || stopped) return;
    const armedAt = sent();
    const delay = lastRead.current === null ? LOOK_FIRST_MS
      : Math.max(0, LOOK_EVERY_MS - (clock.now().getTime() - lastRead.current));
    // another read went out meanwhile: its answer re-arms this
    const id = setTimeout(() => { if (sent() === armedAt) watch(false); }, delay);
    return () => clearTimeout(id);
  }, [on, visible, reads, stopped, watch, sent]);
}

/** "Rebuild from files": post `{"mode": "ingest"}`, then follow the ingest it asked for to its end; a request that never
 *  reached the store is no ask and is not followed; an ended rebuild's line is retired after OUTCOME_TTL_MS */
function useRebuild(api: RefreshApi, builtAt: string): Rebuild {
  const { follow, dispatch, landed } = useIngestFollow(api, builtAt, "rebuild");
  const [ask, setAsk] = useState<IngestAsk | null>(null);
  const landedRef = useRef(landed);
  landedRef.current = landed;
  const { requestIngest } = api;
  const run = useCallback(async () => {
    setAsk(null);
    dispatch({ type: "save", removed: [] });
    const answer = await requestIngest();
    setAsk(answer);
    if (answer.kind === "refused" || answer.kind === "unreached" || answer.kind === "signed-out") {
      dispatch({ type: "refused" });
      return;
    }
    // taken: the store touched the marker; no service: the read that follows says so (no-service)
    landedRef.current(answer.kind === "accepted");
  }, [dispatch, requestIngest]);
  const ended = rebuildLine(follow, ask) !== null;
  useEffect(() => {
    if (!ended) return;
    const id = setTimeout(() => dispatch({ type: "reset" }), OUTCOME_TTL_MS);
    return () => clearTimeout(id);
  }, [ended, dispatch]);
  return useMemo(() => ({ follow, ask, run }), [follow, ask, run]);
}

/** ONE useRefresh for the whole app, mounted around the Shell (App.tsx). The modal behind the
 *  freshness pill and the Data page's panel are two views of it, so a run outlives whichever view
 *  started it: the modal can close, the Data page can unmount on navigation, and the run is still
 *  polled to its end and the data reloaded exactly once (the old per-view hooks died with their
 *  view, and a remount dropped the finished run's nonce as if this page load had fetched it). The
 *  status is read when a view opens (each new view re-reads it, as the per-view hooks did), when a
 *  save asks (`watch`, useRefreshFollow) and by a visible tab's own looks (useLooks: at most one a
 *  minute); it is polled only while a run is in flight or an ingest without a fetch is followed; a
 *  POST happens only on a click. A quiet reload (another device's ingest) that brings a newer build
 *  says so for UPDATED_LINE_MS under the pill, announced once by the status region here. `looks` false
 *  turns the tab's own looks off (every read is then a view's, a save's or a poll's: what a test counts). */
export function RefreshProvider({ children, looks = true }: { children: ReactNode; looks?: boolean }) {
  const { reload, data } = useData();
  const builtAt = data.manifest.generated_at;
  const zone = data.assumptions.household.timezone;
  const builtAtRef = useRef(builtAt);
  builtAtRef.current = builtAt;
  /** the build a quiet reload started from: once the reload is over, a newer one on screen is announced */
  const [quietFrom, setQuietFrom] = useState<string | null>(null);
  const onQuiet = useCallback(async () => {
    const before = builtAtRef.current;
    await reload();
    setQuietFrom(before);
  }, [reload]);
  const [updated, setUpdated] = useState<string | null>(null);
  useEffect(() => {
    if (quietFrom === null) return;
    if (builtAt !== quietFrom) setUpdated(builtAt);
    setQuietFrom(null);
  }, [quietFrom, builtAt]);
  useEffect(() => {
    if (updated === null) return;
    const id = setTimeout(() => setUpdated(null), UPDATED_LINE_MS);
    return () => clearTimeout(id);
  }, [updated]);

  const [viewers, setViewers] = useState(0);
  const [views, setViews] = useState(0);
  const api = useRefresh({ enabled: viewers > 0, viewKey: views, onDone: reload, onQuiet, builtAt });
  // the demo build has no status to look at
  useLooks(api, looks && !IS_DEMO);
  const rebuild = useRebuild(api, builtAt);
  const view = useCallback(() => {
    setViewers((n) => n + 1);
    setViews((n) => n + 1);
    return () => setViewers((n) => n - 1);
  }, []);
  // Retires the outcome line (OUTCOME_TTL_MS) on a page nobody touches: ONE timeout at a time, set for
  // the instant the line expires, then nothing - not a tick for the rest of the session. It exists only
  // while a still-fresh outcome is held, reads no status and costs no request; an outcome that is
  // already expired when it appears (a run that ended long before this client looked) sets none. When
  // it fires it re-reads the clock and, if the line is STILL fresh by it (the device clock stepped back,
  // a coarse or throttled timer), arms another for what is left - a timeout is not a clock.
  const [now, setNow] = useState(() => clock.now());
  useEffect(() => {
    const held = api.outcome;
    if (!held) return;
    let id: ReturnType<typeof setTimeout> | undefined;
    const arm = () => {
      const at = clock.now();
      setNow(at);
      const left = outcomeEnded(held) + OUTCOME_TTL_MS - at.getTime();   // NaN for an unparseable stamp
      id = left >= 0 ? setTimeout(arm, left + 1) : undefined;            // +1: the TTL itself is still visible
    };
    arm();
    return () => clearTimeout(id);
  }, [api.outcome]);
  const schedule = api.status ? api.status.schedule ?? null : api.noService ? null : undefined;
  const unavailable: StatusUnavailable = api.signedOut ? "signed-out" : api.unreachable && !api.noService ? "unreachable" : null;
  const pill = useMemo(() => ({ schedule, unavailable, updated }), [schedule, unavailable, updated]);
  const value = useMemo(() => ({ api, view, now, rebuild, pill }), [api, view, now, rebuild, pill]);
  return (
    <RefreshCtx.Provider value={value}>
      {children}
      <div role="status" className="sr-only" data-testid="refresh-announce">{updated !== null ? updatedText(updated, zone) : ""}</div>
    </RefreshCtx.Provider>
  );
}

/** the shared refresh without registering a view: what a save that asks for an ingest follows (`api.watch()` reads
 *  the status and follows the ingest; its end reloads the exports for the whole session) */
export function useRefreshFollow(): RefreshApi {
  const ctx = useContext(RefreshCtx);
  if (!ctx) throw new Error("useRefreshFollow must run inside <RefreshProvider> (App.tsx)");
  return ctx.api;
}

/** the schedule the last status read reported, without registering a view; null before a read, when the store reports
 *  none, or outside a RefreshProvider */
export function useRefreshSchedule(): RefreshSchedule | null {
  return useContext(RefreshCtx)?.pill.schedule ?? null;
}

/** the shared refresh, registered as a view while `active` */
function useSharedRefresh(active: boolean): SharedRefresh {
  const ctx = useContext(RefreshCtx);
  if (!ctx) throw new Error("RefreshModal / RefreshPanel must render inside <RefreshProvider> (App.tsx)");
  const { view } = ctx;
  useEffect(() => (active ? view() : undefined), [active, view]);
  return ctx;
}

/** The readout and button over the shared refresh — the body of both the modal and the Data
 *  page's panel. */
function RefreshReadout({ api, now, rebuild }: { api: RefreshApi; now: Date; rebuild: Rebuild }) {
  const { data } = useData();
  const m = data.manifest;
  const feed = m.setup.feed;
  const zone = data.assumptions.household.timezone;
  const s = api.status;
  const at = clock.now();
  const service = { unreachable: api.unreachable, unavailableNote: api.unavailableNote, noService: api.noService,
                    signedOut: api.signedOut, runLost: api.runLost };
  const ctl = feed
    ? buttonState({ ...service, status: s, posting: api.posting, ingest: api.ingestCause, zone })
    : rebuildState({ ...service, status: s, follow: rebuild.follow, ingest: api.ingestCause });
  // a sign-in that ended is one line with the way back, in place of the button; nothing navigates until it is followed
  const note = api.signedOut ? null : ctl.note;
  const busy = api.busy || (!feed && rebuilding(rebuild.follow));
  const outcome = outcomeVisible(api.outcome, s, api.busy, now) ? api.outcome : null;
  const line = feed ? null : rebuildLine(rebuild.follow, rebuild.ask);
  const schedule = scheduleSentence(s?.schedule, zone, at, feed ? "Refreshes" : "Rebuilds")
    ?? (api.unreachable ? null : unknownScheduleLine(s, feed));
  const problems = feed ? feedProblemLines(s) : { reauth: null, errors: [] };

  return (
    <>
      <div className="space-y-2 border-b border-zinc-800 pb-3">
        {feed && <Row label="Last feed fetch" at={m.simplefin_fetched_at} never="not fetched yet" zone={zone} now={at} />}
        {/* generated_at carries its UTC offset, so it goes through the same Row as the feed stamp: two rows, one
            format */}
        <Row label="Dashboard rebuilt" at={m.generated_at} never="never" zone={zone} now={at} />
      </div>

      {problems.reauth && (
        <div data-testid="refresh-reauth" className="mt-3 rounded-lg bg-amber-900/30 p-2 text-[11px] leading-relaxed text-amber-300">
          ⚠ {problems.reauth}
        </div>
      )}
      {problems.errors.length > 0 && (
        <ul data-testid="refresh-feed-errors" className="mt-3 space-y-1 text-[11px] leading-relaxed text-amber-300">
          {problems.errors.map((e, i) => <li key={i}>{e}</li>)}
        </ul>
      )}

      {outcome && (feed ? (
        <div className={`mt-3 text-[11px] leading-relaxed ${
          outcome.ok ? "text-emerald-400" : "text-amber-300"}`}>
          {outcome.ok
            ? "Updated — the numbers on screen are current."
            // The worker's own last-resort quota guard (refresh_runner.py). It
            // should never fire — the rate limit keeps well inside SimpleFIN's 24
            // daily requests — but if it does it is a refusal, not a failed run.
            : outcome.stepsFailed.includes("quota-backstop")
              ? "Skipped — too many bank pulls in the last 24 hours. Try again tomorrow."
              : `Refreshed, but ${outcome.stepsFailed.join(" and ") || "a step"} failed.`
                + " Showing the newest data we have."}
        </div>
      ) : (
        <div className={`mt-3 text-[11px] leading-relaxed ${noFeedOutcome(outcome).ok ? "text-emerald-400" : "text-amber-300"}`}>
          {noFeedOutcome(outcome).text}
        </div>
      ))}
      {line && !outcome && (
        <div role="status" data-testid="rebuild-line"
          className={`mt-3 text-[11px] leading-relaxed ${line.ok ? "text-emerald-400" : "text-amber-300"}`}>
          {line.text}
        </div>
      )}
      {/* never gated on the outcome: a stalled status hides any earlier outcome (outcomeVisible), and
          this notice is the truth then */}
      {s?.state === "stalled" && (
        <div className="mt-3 text-[11px] text-amber-300">{stalledNotice(feed, ctl)}</div>
      )}
      {api.error && <div className="mt-3 text-[11px] text-red-400">{api.error}</div>}

      {ctl.label !== null && (
        <button
          onClick={() => void (feed ? api.trigger() : rebuild.run())}
          disabled={ctl.disabled}
          className={`mt-3 min-h-[44px] w-full rounded-lg px-3 text-sm font-medium ${
            ctl.disabled
              ? "cursor-not-allowed bg-zinc-800 text-zinc-500"
              : "bg-emerald-600 text-white hover:bg-emerald-500"}`}
        >
          {busy && <span className="mr-1 inline-block animate-pulse">↻</span>}
          {ctl.label}
        </button>
      )}
      {api.signedOut && (
        <p data-testid="refresh-signed-out" className="mt-3 text-sm text-amber-300">
          Your sign-in has ended.{" "}
          <a href={LOGIN_PATH} className="font-medium text-blue-400 underline hover:text-blue-300">Sign in again</a>
        </p>
      )}

      <p data-testid="refresh-fine-print" className="mt-2 text-[10px] leading-relaxed text-zinc-500">
        {note && <span className="text-zinc-400">{note}{(schedule || (feed && s && !api.unreachable)) && <br />}</span>}
        {schedule}
        {feed && s && !api.unreachable && (
          <>{schedule && " "}{Math.max(0, s.limit_per_day - s.used_today)} of {s.limit_per_day}{" "}
            manual refreshes left today.</>
        )}
      </p>
    </>
  );
}

/** The refresh readout and button as a plain panel (no scrim, no header) — rendered inline on
 *  the Data page; a view of the shared refresh while mounted. */
export function RefreshPanel() {
  const { api, now, rebuild } = useSharedRefresh(!IS_DEMO);
  // the demo build has nothing to refresh or rebuild: one line says so in place of the readout and its button
  if (IS_DEMO) return <p data-testid="refresh-demo" className="text-sm text-zinc-300">{DEMO_NO_REFRESH}</p>;
  return <RefreshReadout api={api} now={now} rebuild={rebuild} />;
}

export function RefreshModal(
  { open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void },
) {
  // mounted only while open, so the trap arms on each open (it reads its ref once) and the shared
  // refresh sees exactly one view per opening; the demo build has no refresh to open
  return open && !IS_DEMO ? <RefreshDialog onOpenChange={onOpenChange} /> : null;
}

function RefreshDialog({ onOpenChange }: { onOpenChange: (v: boolean) => void }) {
  const { api, now, rebuild } = useSharedRefresh(true);
  const panel = useRef<HTMLDivElement>(null);
  // A real modal: aria-modal + the shared focus trap (Tab stays inside, Escape closes - and only
  // when this is the topmost dialog - and focus returns to the pill). The panel itself takes the
  // initial focus, not the Refresh button: Enter must not fire a refresh.
  useFocusTrap(panel, () => onOpenChange(false), { initialFocus: "container" });
  return (
    // Same scrim + stopPropagation as the ⌘K palette; anchored to the bottom on
    // a phone so the button lands under a thumb, centred high on desktop.
    <div
      className="fixed inset-0 z-[60] flex items-end justify-center bg-black/60 p-3 print:hidden sm:items-start sm:pt-[18vh]"
      onClick={() => onOpenChange(false)}
    >
      <div
        ref={panel} role="dialog" aria-modal="true" aria-label="Data refresh" tabIndex={-1}
        className="w-full max-w-sm rounded-xl border border-zinc-700 bg-zinc-900 p-4 shadow-2xl outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-zinc-100">Data refresh</h2>
          <button onClick={() => onOpenChange(false)} aria-label="Close"
            className="flex h-8 w-8 items-center justify-center rounded-lg text-zinc-400 hover:bg-zinc-800">
            ✕
          </button>
        </div>
        <RefreshReadout api={api} now={now} rebuild={rebuild} />
      </div>
    </div>
  );
}

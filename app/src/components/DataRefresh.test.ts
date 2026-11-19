// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { type IngestAsk, type IngestCause, type IngestStatus, type Outcome, type RefreshStatus } from "../data/useRefresh";
import { type Follow, IDLE_FOLLOW } from "../lib/ingestFollow";
import { setPrivate } from "../lib/privacy";
import {
  buttonState, feedProblemLines, OUTCOME_TTL_MS, outcomeVisible, pillState, rebuildLine, rebuildState, serviceNote,
  stalledNotice, unknownScheduleLine,
} from "./DataRefresh";

/** The button must never lie about what pressing it would do — every disabled
 *  state owes the user a reason, and every reason has to be the server's. */

const IDLE: RefreshStatus = {
  state: "idle", trigger: null, request_nonce: null, queued_nonce: null,
  started_at: null, finished_at: null, rc: null, steps_failed: [], warnings: [],
  can_refresh: true, reason: "ok", retry_after_s: 0, used_today: 0,
  limit_per_day: 6, last_refresh_at: null,
};
const s = (over: Partial<RefreshStatus>): RefreshStatus => ({ ...IDLE, ...over });
const state = (st: RefreshStatus | null, unreachable = false, posting = false,
               note: string | null = null) =>
  buttonState({ status: st, unreachable, posting, unavailableNote: note, noService: note !== null });

// the reader's 12- or 24-hour clock (the browser's locale): the expected text in either
const cycle = new Intl.DateTimeFormat(undefined, { hour: "numeric" }).resolvedOptions().hourCycle;
const t = (twelve: string, twentyFour: string) => (cycle === "h23" || cycle === "h24" ? twentyFour : twelve);

describe("the pill", () => {
  const MT = "America/Denver";
  const NOW = new Date("2026-08-11T16:00:00Z");                          // 10:00 in Denver
  const THREE = { times: ["01:00", "09:00", "17:00"], timezone: MT };
  const ago = (h: number) => new Date(NOW.getTime() - h * 3_600_000).toISOString();
  const feed = (fetched: string | null) => ({ simplefin_fetched_at: fetched, generated_at: "2026-08-11T09:30:00-06:00",
    setup: { accounts: 2, feed: true, import_files: 0, manual_assets: 0 } });
  const noFeed = { simplefin_fetched_at: null, generated_at: "2026-08-11T07:00:00-06:00",
    setup: { accounts: 1, feed: false, import_files: 2, manual_assets: 0 } };
  it("with a feed: the age of the last fetch, toned by the schedule (amber past 1.25 of its longest gap, red past 3)", () => {
    const fresh = pillState(feed(ago(9)), THREE, MT, NOW);
    expect([fresh.label, fresh.cls]).toEqual(["9h ago", "bg-zinc-800 text-zinc-400"]);
    expect(fresh.title).toBe(`Last feed fetch Aug 11, ${t("1:00 AM", "01:00")} MDT. Tap for details or to refresh now.`);
    expect(fresh.aria).toBe("Bank feed fetched 9h ago — open the data refresh panel");
    const amber = pillState(feed(ago(10)), THREE, MT, NOW);
    expect([amber.label, amber.cls]).toEqual(["10h ago", "bg-amber-900/50 text-amber-300"]);
    expect(amber.title).toContain("Over 10 hours old: a scheduled refresh may have been missed.");
    const red = pillState(feed(ago(24)), THREE, MT, NOW);
    expect([red.label, red.cls]).toEqual(["1d ago", "bg-red-900/50 text-red-300"]);
    expect(red.title).toContain("Over a day old: the scheduled refresh may be failing.");
  });
  it("with no schedule in the status the thresholds are a once-a-day schedule's: amber at 30 hours, red at 72", () => {
    expect(pillState(feed(ago(29)), null, MT, NOW).cls).toBe("bg-zinc-800 text-zinc-400");
    expect(pillState(feed(ago(30)), null, MT, NOW).cls).toBe("bg-amber-900/50 text-amber-300");
    expect(pillState(feed(ago(72)), null, MT, NOW).cls).toBe("bg-red-900/50 text-red-300");
  });
  it("the title's verb is the pointer's: Tap for a finger, Click for a mouse, in every state", () => {
    expect(pillState(feed(ago(9)), THREE, MT, NOW, null, "Click").title).toMatch(/ Click for details or to refresh now\.$/);
    expect(pillState(feed(null), THREE, MT, NOW, null, "Click").title).toMatch(/ Click for details or to refresh now\.$/);
    expect(pillState(noFeed, THREE, MT, NOW, null, "Click").title).toMatch(/Click for details or to rebuild from files\.$/);
    expect(pillState(feed(ago(9)), THREE, MT, NOW, "signed-out", "Click").title).toBe("Your sign-in has ended. Click to sign in again.");
    expect(pillState(feed(ago(9)), THREE, MT, NOW, "signed-out", "Tap").title).toBe("Your sign-in has ended. Tap to sign in again.");
  });
  it("a feed never fetched says so instead of an age", () => {
    const never = pillState(feed(null), THREE, MT, NOW);
    expect([never.label, never.cls]).toEqual(["not fetched yet", "bg-amber-900/50 text-amber-300"]);
    expect(never.title).toBe("The bank feed has not been fetched yet. Tap for details or to refresh now.");
    expect(never.aria).toBe("Bank feed not fetched yet — open the data refresh panel");
  });
  it("until the status has answered the pill is neutral, so it never flips a moment after the page opens", () => {
    for (const h of [9, 30, 100]) expect(pillState(feed(ago(h)), undefined, MT, NOW).cls, `${h}h`).toBe("bg-zinc-800 text-zinc-400");
    expect(pillState(feed(ago(100)), undefined, MT, NOW).title).toBe(`Last feed fetch Aug 7, ${t("6:00 AM", "06:00")} MDT. `
      + "Tap for details or to refresh now.");
    expect(pillState(feed(null), undefined, MT, NOW).label, "a feed never fetched says so whatever the status").toBe("not fetched yet");
  });
  it("with no feed and a schedule, the last rebuild is toned by the same thresholds; with none, it stays neutral", () => {
    const at = (h: number) => ({ ...noFeed, generated_at: ago(h) });
    expect(pillState(at(9), THREE, MT, NOW).cls).toBe("bg-zinc-800 text-zinc-400");
    const amber = pillState(at(10), THREE, MT, NOW);
    expect(amber.cls).toBe("bg-amber-900/50 text-amber-300");
    expect(amber.title).toContain("Rebuilt over 10 hours ago: a scheduled rebuild may have been missed.");
    const red = pillState(at(24 * 90), THREE, MT, NOW);
    expect(red.cls, "rebuilds that stopped three months ago").toBe("bg-red-900/50 text-red-300");
    expect(red.title).toContain("Rebuilt over a day ago: the scheduled rebuild may be failing.");
    expect(JSON.stringify([amber, red])).not.toMatch(/pull|fetch|feed/i);
    expect(pillState(at(24 * 90), null, MT, NOW).cls, "no schedule: rebuilt by hand, nothing to fall behind").toBe("bg-zinc-800 text-zinc-400");
    expect(pillState(at(24 * 90), undefined, MT, NOW).cls, "not answered yet").toBe("bg-zinc-800 text-zinc-400");
  });
  it("when the status could not be read, the tooltip says so", () => {
    expect(pillState(feed(ago(2)), THREE, MT, NOW, "unreachable").title).toBe(`Last feed fetch Aug 11, ${t("8:00 AM", "08:00")} MDT. `
      + "The refresh status is unavailable right now. Tap for details or to refresh now.");
  });
  it("a sign-in that ended is said on the pill itself, in a warning tone, with or without a feed", () => {
    for (const m of [feed(ago(2)), feed(null), noFeed]) {
      expect(pillState(m, THREE, MT, NOW, "signed-out")).toEqual({ label: "signed out", cls: "bg-amber-900/50 text-amber-300",
        aria: "Signed out — open the data refresh panel to sign in again", title: "Your sign-in has ended. Tap to sign in again." });
    }
  });
  it("with no feed: when the dashboard was rebuilt, never a pull, a fetch or a feed", () => {
    const p = pillState(noFeed, THREE, MT, NOW);
    expect([p.label, p.cls]).toEqual(["rebuilt 3h ago", "bg-zinc-800 text-zinc-400"]);
    expect(p.title).toBe(`Dashboard rebuilt Aug 11, ${t("7:00 AM", "07:00")} MDT. Tap for details or to rebuild from files.`);
    expect(p.aria).toBe("Dashboard rebuilt 3h ago — open the data refresh panel");
    expect(JSON.stringify(p)).not.toMatch(/pull|fetch|feed/i);
    // a week or more on, the day itself
    expect(pillState({ ...noFeed, generated_at: "2026-07-30T07:00:00-06:00" }, null, MT, NOW).label).toBe("rebuilt Jul 30");
  });
});

describe("buttonState", () => {
  it("offers the refresh when nothing is in the way", () => {
    expect(state(IDLE)).toEqual({ label: "Refresh now", disabled: false, note: null });
  });

  it("disables while a run of ours is in flight", () => {
    const r = state(s({ state: "running", trigger: "manual" }));
    expect(r.disabled).toBe(true);
    expect(r.label).toBe("Refreshing…");
    expect(r.note).toMatch(/30 seconds/);
  });

  it("says whose run it is when a scheduled one is already going", () => {
    expect(state(s({ state: "running", trigger: "schedule" })).note)
      .toMatch(/scheduled refresh is already running/);
  });

  it("counts the wait in minutes, rounded up so it never says 0", () => {
    const r = state(s({ can_refresh: false, reason: "min_gap", retry_after_s: 541,
                        last_refresh_at: "2026-08-11T16:55:00Z" }));
    expect(r.disabled).toBe(true);
    expect(r.note).toMatch(/try again in 10 min/);
    expect(state(s({ can_refresh: false, reason: "min_gap", retry_after_s: 1 })).note)
      .toMatch(/try again in 1 min/);
  });

  it("explains the daily cap by its reset, not by a 10-hour countdown", () => {
    const r = state(s({ can_refresh: false, reason: "daily_cap",
                        retry_after_s: 37_440, used_today: 6 }));
    expect(r.note).toMatch(/All 6 manual refreshes used today/);
    expect(r.note).toMatch(/midnight/);
  });

  it("while an ingest without a fetch is followed it reads Updating…, says it spends no bank pull, and a refresh's own state wins", () => {
    const busy = (st: RefreshStatus, unreachable = false) =>
      buttonState({ status: st, unreachable, posting: false, ingest: "save" });
    expect(busy(IDLE)).toEqual({ label: "Updating…", disabled: true,
      note: "Rebuilding the dashboard with a saved change — no bank pull." });
    expect(busy(s({ state: "running", trigger: "manual" })).label).toBe("Refreshing…");
    expect(busy(s({ state: "queued" })).label).toBe("Queued…");
    expect(busy(IDLE, true).label, "an unreachable store says so first: nothing to press").toBeNull();
  });

  it("an ingest nobody here saved a change for is a rebuild under way, never a saved change", () => {
    for (const ingest of ["seen", "rebuild"] as const) {
      expect(buttonState({ status: IDLE, unreachable: false, posting: false, ingest })).toEqual({ label: "Updating…",
        disabled: true, note: "A rebuild is under way — no bank pull." });
    }
  });

  it("distinguishes a stopped worker from a rate limit, and says the request sits unclaimed", () => {
    const r = state(s({ state: "worker_unavailable", can_refresh: false,
                        reason: "worker_unavailable" }));
    expect(r.disabled).toBe(true);
    expect(r.note).toBe("The refresh service has not picked up the queued request — it may be stopped.");
  });

  it("re-enables after a run died mid-flight", () => {
    // status.json still said running, but nobody held the lock
    expect(state(s({ state: "stalled" })).disabled).toBe(false);
  });

  it("with no answer from the store there is nothing to press, and it says why", () => {
    const r = state(IDLE, true);
    expect(r).toEqual({ label: null, disabled: true, note: "The dashboard's store is unreachable, so nothing can be "
      + "started from here: try again in a moment, or re-run the ingest to update the dashboard." });
  });

  it("no refresh service (a 503, a household that runs the ingest by hand): nothing to press, the server's reason in words", () => {
    // a store started without --refresh-dir 503s with this; calling that
    // "unreachable" would send someone hunting a fault that isn't there
    const r = state(IDLE, true, false, "refresh not configured");
    expect(r).toEqual({ label: null, disabled: true, note: "No refresh service answers here (refresh not configured), so "
      + "nothing can be started from the dashboard: re-run the ingest to update it." });
    expect(serviceNote({ noService: true, unavailableNote: "refresh not configured" })).toBe(r.note);
  });

  it("a sign-in that ended: nothing to post (the readout shows the line with the way to sign in again)", () => {
    expect(buttonState({ status: IDLE, unreachable: true, signedOut: true, posting: false })).toEqual({ label: null,
      disabled: true, note: "Your sign-in has ended." });
    expect(rebuildState({ status: IDLE, unreachable: true, signedOut: true, follow: IDLE_FOLLOW }).label).toBeNull();
  });

  it("a run past its bound: still in flight as the last answer says, and it has not reported back", () => {
    const note = "The refresh has not reported back — it may have stopped. Open this again later to look.";
    expect(buttonState({ status: s({ state: "running", trigger: "schedule" }), unreachable: false, posting: false, runLost: true }))
      .toEqual({ label: "Refreshing…", disabled: true, note });
    expect(buttonState({ status: s({ state: "queued" }), unreachable: false, posting: false, runLost: true }).note).toBe(note);
    expect(rebuildState({ status: s({ state: "running", trigger: "schedule" }), unreachable: false, follow: IDLE_FOLLOW,
      runLost: true })).toEqual({ label: "Rebuilding…", disabled: true, note });
  });

  it("a wait between refreshes counts from the last one, on the household's clock", () => {
    const at = new Date(Date.now() - 3 * 3_600_000).toISOString();
    expect(buttonState({ status: s({ can_refresh: false, reason: "min_gap", retry_after_s: 60, last_refresh_at: at }),
      unreachable: false, posting: false, zone: "America/Denver" }).note).toBe("Data was pulled 3h ago — try again in 1 min.");
  });

  it("stays disabled while we have no status to judge from", () => {
    expect(state(null).disabled).toBe(true);
    expect(state(IDLE, false, true)).toEqual(
      { label: "Starting…", disabled: true, note: null });
  });
});

describe("outcomeVisible", () => {
  const seen = "2026-08-11T17:00:00Z";
  const out: Outcome = { mine: true, ok: true, stepsFailed: [], finishedAt: "2026-08-11T16:59:50Z", seenAt: seen };
  const at = (iso: string) => new Date(iso);
  it("shows a fresh outcome of a finished run", () => {
    expect(outcomeVisible(out, s({ state: "done", finished_at: out.finishedAt }), false, at("2026-08-11T17:05:00Z"))).toBe(true);
  });
  it("never while a run is in flight, never when the status says stalled (the didn't-finish notice shows instead)", () => {
    expect(outcomeVisible(out, s({ state: "running" }), true, at(seen))).toBe(false);
    expect(outcomeVisible(out, s({ state: "stalled" }), false, at(seen))).toBe(false);
  });
  it("fades after OUTCOME_TTL_MS, counted from finished_at (or from when the client saw it, when the server has no stamp)", () => {
    expect(outcomeVisible(out, s({ state: "done" }), false, at("2026-08-11T17:14:49Z"))).toBe(true);
    expect(outcomeVisible(out, s({ state: "done" }), false, new Date(Date.parse(out.finishedAt!) + OUTCOME_TTL_MS + 1))).toBe(false);
    const noStamp = { ...out, finishedAt: null };
    expect(outcomeVisible(noStamp, s({ state: "done" }), false, new Date(Date.parse(seen) + OUTCOME_TTL_MS - 1))).toBe(true);
    expect(outcomeVisible(noStamp, s({ state: "done" }), false, new Date(Date.parse(seen) + OUTCOME_TTL_MS + 1))).toBe(false);
  });
  it("the TTL is 15 minutes, to the boundary (inclusive)", () => {
    expect(OUTCOME_TTL_MS).toBe(15 * 60_000);
    expect(outcomeVisible(out, s({ state: "done" }), false, new Date(Date.parse(out.finishedAt!) + OUTCOME_TTL_MS))).toBe(true);
  });
  it("a newer run (started after the outcome was seen) retires it", () => {
    expect(outcomeVisible(out, s({ state: "done", started_at: "2026-08-11T17:01:00Z" }), false, at("2026-08-11T17:02:00Z"))).toBe(false);
  });
  it("a run that started after this one finished retires it even before the client saw that run begin", () => {
    // started between finished_at and seenAt: the server's own stamps say it is newer
    expect(outcomeVisible(out, s({ state: "done", started_at: "2026-08-11T16:59:55Z" }), false, at("2026-08-11T17:02:00Z"))).toBe(false);
  });
  it("the run the outcome describes does not retire itself, even when this device's clock runs behind the server's", () => {
    // the server's stamps for the SAME run (started 17:00:30, finished 17:01:00) against a client that
    // saw it end at what its own slow clock called 16:58:00: comparing the server's start with the
    // client's instant would call the run newer than its own outcome
    const slow: Outcome = { ...out, finishedAt: "2026-08-11T17:01:00Z", seenAt: "2026-08-11T16:58:00Z" };
    expect(outcomeVisible(slow, s({ state: "done", started_at: "2026-08-11T17:00:30Z", finished_at: slow.finishedAt }), false,
      at("2026-08-11T17:02:00Z"))).toBe(true);
  });
  it("without a server stamp the comparison falls back to when the client saw it end", () => {
    const noStamp = { ...out, finishedAt: null };
    expect(outcomeVisible(noStamp, s({ state: "done", started_at: "2026-08-11T16:59:00Z" }), false, at("2026-08-11T17:02:00Z"))).toBe(true);
    expect(outcomeVisible(noStamp, s({ state: "done", started_at: "2026-08-11T17:01:00Z" }), false, at("2026-08-11T17:02:00Z"))).toBe(false);
  });
  it("an unparseable stamp hides rather than shows (no honest age, no banner)", () => {
    expect(outcomeVisible({ ...out, finishedAt: "not a date" }, s({ state: "done" }), false, at(seen))).toBe(false);
  });
  it("nothing to show without an outcome", () => { expect(outcomeVisible(null, IDLE, false, at(seen))).toBe(false); });
  it("with no status read yet an outcome still shows within its TTL (the status only ever retires it)", () => {
    expect(outcomeVisible(out, null, false, at("2026-08-11T17:05:00Z"))).toBe(true);
  });
});

// A household with no bank feed: the control rebuilds from the files (an ingest without a fetch), and no word of it
// names a feed, a fetch or a pull.
describe("rebuildState and rebuildLine", () => {
  const ingest = (over: Partial<IngestStatus>): IngestStatus =>
    ({ wanted: false, last_started_at: "2026-08-11T14:00:00Z", last_finished_at: "2026-08-11T14:00:40Z", last_rc: 0, ...over });
  const f = (over: Partial<Follow>): Follow => ({ ...IDLE_FOLLOW, ...over });
  const rebuild = (st: RefreshStatus | null, follow: Follow = IDLE_FOLLOW,
    more: { unreachable?: boolean; note?: string | null; noService?: boolean; ingest?: IngestCause | null } = {}) =>
    rebuildState({ status: st, unreachable: more.unreachable ?? false, unavailableNote: more.note ?? null,
      noService: more.noService ?? false, follow, ingest: more.ingest ?? null });
  const asked = (reason: string | null): IngestAsk => ({ kind: "refused", reason });
  const NO_FEED_WORDS = /feed|fetch|pull/i;

  it("idle: Rebuild from files, and what it does", () => {
    expect(rebuild(IDLE)).toEqual({ label: "Rebuild from files", disabled: false,
      note: "Reads the household's files again and rebuilds the dashboard." });
    expect(rebuild(null), "no status to judge from yet").toEqual({ label: "Rebuild from files", disabled: true, note: null });
  });
  it("asked, waiting, running, reloading: Rebuilding…, each with its line", () => {
    expect(rebuild(IDLE, f({ phase: "saving" }))).toEqual({ label: "Starting…", disabled: true, note: null });
    expect(rebuild(s({ ingest: ingest({ wanted: true }) }), f({ phase: "waiting" })))
      .toEqual({ label: "Rebuilding…", disabled: true, note: "It starts within about a minute of being asked." });
    expect(rebuild(s({ ingest: ingest({ last_started_at: "2026-08-11T15:00:00Z", last_finished_at: null, last_rc: null }) }),
      f({ phase: "waiting" }))).toEqual({ label: "Rebuilding…", disabled: true, note: "Reading the files and rebuilding the dashboard." });
    expect(rebuild(IDLE, f({ phase: "reloading" })).note).toBe("Loading the rebuilt dashboard.");
    expect(rebuild(IDLE, f({ phase: "asking" })).label).toBe("Rebuilding…");
  });
  it("a scheduled refresh running, or an ingest a saved change asked for: Rebuilding…, and why", () => {
    expect(rebuild(s({ state: "running", trigger: "schedule" }))).toEqual({ label: "Rebuilding…", disabled: true,
      note: "A scheduled refresh is rebuilding the dashboard." });
    expect(rebuild(s({ state: "queued" })).label).toBe("Queued…");
    expect(rebuild(IDLE, IDLE_FOLLOW, { ingest: "save" })).toEqual({ label: "Rebuilding…", disabled: true,
      note: "Rebuilding the dashboard with a saved change." });
  });
  it("an ingest no save of this tab asked for (another device, a reload during a rebuild, after the bound) is a rebuild under way", () => {
    for (const ingest of ["seen", "rebuild"] as const) {
      expect(rebuild(IDLE, IDLE_FOLLOW, { ingest })).toEqual({ label: "Rebuilding…", disabled: true,
        note: "A rebuild is under way." });
      expect(rebuild(IDLE, f({ phase: "later" }), { ingest }).note, "the own follow ended at its bound").toBe("A rebuild is under way.");
    }
  });
  it("no refresh service, read live: nothing to press while the store says so or does not answer, the button back with the next answer", () => {
    expect(rebuild(IDLE, IDLE_FOLLOW, { unreachable: true, noService: true, note: "refresh not configured" }))
      .toEqual({ label: null, disabled: true, note: serviceNote({ noService: true, unavailableNote: "refresh not configured" }) });
    expect(rebuild(IDLE, IDLE_FOLLOW, { unreachable: true }).note).toBe(serviceNote({}));
    // a follow that ended on the store's 503: once a read answers again, the button is back
    expect(rebuild(IDLE, f({ phase: "no-service" }))).toEqual({ label: "Rebuild from files", disabled: false,
      note: "Reads the household's files again and rebuilds the dashboard." });
  });
  it("the refresh budget is a feed's: a household with none is never held back by it", () => {
    expect(rebuild(s({ can_refresh: false, reason: "daily_cap" })).disabled).toBe(false);
  });
  it("what the rebuild came to: done, failed in words, not finished by the bound, refused", () => {
    expect(rebuildLine(f({ phase: "done" }), null)).toEqual({ text: "Rebuilt — the numbers on screen are current.", ok: true });
    for (const [rc, words] of [[-1, "timed out"], [-2, "stopped before it finished"], [143, "was stopped"], [2, "failed (code 2)"]] as const) {
      expect(rebuildLine(f({ phase: "failed", rc }), null)?.text)
        .toBe(`The rebuild ${words}. The dashboard still shows what it was last built from.`);
    }
    expect(rebuildLine(f({ phase: "later", started: true, answered: true }), null)?.text)
      .toBe("The rebuild has not finished yet: the dashboard updates once it has.");
    expect(rebuildLine(f({ phase: "later", started: false, answered: true }), null)?.text, "never seen to start")
      .toBe("The rebuild has not started — the refresh service may be stopped.");
    expect(rebuildLine(f({ phase: "later", started: false, answered: false }), null)?.text, "the store never answered")
      .toBe("The dashboard's store could not be reached since the rebuild was asked for: the dashboard updates once it "
        + "answers and the rebuild has run.");
    expect(rebuildLine(f({ phase: "refused" }), { kind: "signed-out" }), "the sign-in line is the control's own").toBeNull();
    expect(rebuildLine(f({ phase: "refused" }), asked("ingest_cap"))?.text).toBe("Today's rebuilds are used up: try again after midnight.");
    expect(rebuildLine(f({ phase: "refused" }), asked("cross-origin refresh refused"))?.text)
      .toBe("The rebuild was refused: cross-origin refresh refused.");
    expect(rebuildLine(f({ phase: "refused" }), { kind: "unreached" })?.text, "the request never reached the store")
      .toBe("The request did not reach the dashboard's store, so nothing was rebuilt: try again in a moment.");
    for (const phase of ["idle", "saving", "asking", "waiting", "reloading", "no-service"] as const) {
      expect(rebuildLine(f({ phase }), null), phase).toBeNull();
    }
  });
  it("no word of the control names a feed, a fetch or a pull", () => {
    const words = [rebuild(IDLE), rebuild(IDLE, f({ phase: "waiting" })), rebuild(s({ state: "running", trigger: "schedule" })),
      rebuild(IDLE, IDLE_FOLLOW, { ingest: "save" }), rebuild(IDLE, IDLE_FOLLOW, { ingest: "seen" }),
      rebuild(IDLE, IDLE_FOLLOW, { unreachable: true }),
      ...(["done", "failed", "later", "refused"] as const).map((phase) => rebuildLine(f({ phase, rc: 1 }), asked("ingest_cap"))),
      rebuildLine(f({ phase: "refused" }), { kind: "unreached" }), stalledNotice(false, rebuild(IDLE))];
    expect(JSON.stringify(words)).not.toMatch(NO_FEED_WORDS);
  });
});

describe("what the last fetch learned of the bank feed", () => {
  it("names each connection to sign in to again at the feed provider, the one thing to do, with no command", () => {
    const one = feedProblemLines(s({ reauth_needed: ["Sample Bank"], feed_errors: [] }));
    expect(one).toEqual({ reauth: "Sign in to Sample Bank again at SimpleFIN Bridge: until you do, the bank feed cannot "
      + "bring its balances up to date.", errors: [] });
    expect(feedProblemLines(s({ reauth_needed: ["Sample Bank", "Sample Credit Union", "Sample Broker"] })).reauth)
      .toBe("Sign in to Sample Bank, Sample Credit Union and Sample Broker again at SimpleFIN Bridge: until you do, the "
        + "bank feed cannot bring their balances up to date.");
  });
  it("says each error the feed reported, naming its connection, ending each as a sentence", () => {
    const lines = feedProblemLines(s({ reauth_needed: [], feed_errors: [
      "Sample Bank: Sign in again", "Sample Credit Union: Failed to get account information. Try again later.",
      "the feed provider: Maintenance until 10:00…"] }));
    expect(lines).toEqual({ reauth: null, errors: [
      "The bank feed reported a problem with Sample Bank: Sign in again.",
      "The bank feed reported a problem with Sample Credit Union: Failed to get account information. Try again later.",
      "The bank feed reported a problem with the feed provider: Maintenance until 10:00…"] });
  });
  it("masks an amount a feed error states while privacy mode is on", () => {
    setPrivate(true);
    try {
      const [line] = feedProblemLines(s({ feed_errors: ["Sample Bank: a payment of $1,234.56 is pending"] })).errors;
      expect(line).not.toContain("1,234.56");
      expect(line).toContain("Sample Bank: a payment of");
    } finally {
      setPrivate(false);
    }
  });
  it("says nothing when the feed reported nothing, the run had no feed, or no status has answered", () => {
    expect(feedProblemLines(s({ reauth_needed: [], feed_errors: [] }))).toEqual({ reauth: null, errors: [] });
    expect(feedProblemLines(s({ files_only: true, reauth_needed: [], feed_errors: [], warnings: [] })))
      .toEqual({ reauth: null, errors: [] });
    expect(feedProblemLines(null)).toEqual({ reauth: null, errors: [] });
  });
  it("reads a store that predates the two lists as its warnings said, and junk in a list as nothing", () => {
    expect(feedProblemLines(s({ warnings: ["Sample Bank"] }))).toEqual({ reauth: "Sample Bank needs "
      + "re-authorization in SimpleFIN — those balances will keep going stale.", errors: [] });
    expect(feedProblemLines(s({ warnings: [] }))).toEqual({ reauth: null, errors: [] });
    const junk = { reauth_needed: "Sample Bank", feed_errors: [7, null, "Sample Bank: x"] } as unknown as Partial<RefreshStatus>;
    expect(feedProblemLines(s(junk))).toEqual({ reauth: null, errors: ["The bank feed reported a problem with Sample Bank: x."] });
  });
});

describe("a schedule the store does not know yet", () => {
  it("says so in the words of the household's setup, and nothing before an answer or while the store knows one", () => {
    expect(unknownScheduleLine(s({ schedule: null }), true))
      .toBe("The times of the automatic refresh are not known yet: they show once the scheduler has started.");
    const noFeed = unknownScheduleLine(s({ schedule: null }), false);
    expect(noFeed).toBe("The times of the automatic rebuild are not known yet: they show once the scheduler has started.");
    expect(noFeed).not.toMatch(/feed|fetch|pull/i);
    expect(unknownScheduleLine(s({ schedule: { times: ["01:00"], timezone: "UTC" } }), true)).toBeNull();
    expect(unknownScheduleLine(IDLE, true), "a store that predates the schedule says nothing of one").toBeNull();
    expect(unknownScheduleLine(null, true)).toBeNull();
  });
});

describe("the stalled notice, for the button actually shown", () => {
  it("says how to start another only when a button can be pressed", () => {
    expect(stalledNotice(true, { label: "Refresh now", disabled: false, note: null }))
      .toBe("The last refresh didn't finish. You can start another one.");
    expect(stalledNotice(false, { label: "Rebuild from files", disabled: false, note: null }))
      .toBe("The last refresh didn't finish. You can rebuild from files.");
    expect(stalledNotice(true, { label: "Updating…", disabled: true, note: null })).toBe("The last refresh didn't finish.");
    expect(stalledNotice(false, { label: null, disabled: true, note: "…" })).toBe("The last refresh didn't finish.");
  });
});

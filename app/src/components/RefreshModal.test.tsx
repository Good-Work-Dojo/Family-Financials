// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetSession } from "../data/session";
import { type IngestStatus, RUN_FOLLOW_MS, type RefreshStatus } from "../data/useRefresh";
import { FOLLOW_GRACE_MS, FOLLOW_MS } from "../lib/ingestFollow";

// One useRefresh for the session lives in RefreshProvider (App.tsx wraps the Shell in it); the
// modal behind the freshness pill and the Data page's panel are two views of it. A run must
// outlive the view that started it: the modal returns null while closed, and the Data page
// unmounts when the user navigates away - before the shared hook, the panel's own hook died with
// it and a remount dropped the finished run's nonce as if this page load had fetched fresh
// exports, so the run was never announced and the data never reloaded. Never a real endpoint: the
// only POST goes to the stub below.
// The exports on screen: a household with a feed by default; `next` is what a reload brings (its ingest instant moved).
const ctx = vi.hoisted(() => {
  const manifest = (over: Record<string, unknown> = {}) => ({ data: { manifest: {
    simplefin_fetched_at: null as string | null,
    generated_at: "2026-01-01T00:00:00-07:00",
    setup: { accounts: 2, feed: true, import_files: 0, manual_assets: 0 },
    ...over,
  }, assumptions: { household: { timezone: "America/Denver" } } } });
  const c = {
    manifest,
    current: manifest(),
    next: null as ReturnType<typeof manifest> | null,
    listeners: new Set<() => void>(),
    reload: vi.fn(async () => {}),
  };
  c.reload.mockImplementation(async () => {
    if (c.next) { c.current = c.next; c.next = null; c.listeners.forEach((l) => l()); }
  });
  return c;
});
vi.mock("../data/DataContext", async () => {
  const { useSyncExternalStore } = await import("react");
  const subscribe = (cb: () => void) => { ctx.listeners.add(cb); return () => { ctx.listeners.delete(cb); }; };
  return { useData: () => ({ ...useSyncExternalStore(subscribe, () => ctx.current), reload: ctx.reload }) };
});

import {
  LOOK_EVERY_MS, LOOK_FIRST_MS, RefreshModal, RefreshPanel, RefreshPill, RefreshProvider, RefreshUpdatedStrip, UPDATED_LINE_MS,
  useRefreshFollow,
} from "./DataRefresh";
import { TAP_AREA } from "./ui";

// The tab's visibility: visible unless a test says otherwise (nothing is polled while it is hidden). The tab's own
// looks at the status (at mount, then at most once a minute) are off unless a test asks for them (`looks`), so the
// reads counted are the views', the saves' and the polls'.
let visibility: DocumentVisibilityState = "visible";
Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
const setVisible = (v: DocumentVisibilityState) => {
  visibility = v;
  document.dispatchEvent(new Event("visibilitychange"));
};

const IDLE: RefreshStatus = {
  state: "idle", trigger: null, request_nonce: null, queued_nonce: null,
  started_at: null, finished_at: null, rc: null, steps_failed: [], warnings: [],
  can_refresh: true, reason: "ok", retry_after_s: 0, used_today: 0,
  limit_per_day: 6, last_refresh_at: null,
};
const RUNNING: RefreshStatus = { ...IDLE, state: "running", trigger: "manual", request_nonce: "n1", can_refresh: false };
const DONE: RefreshStatus = { ...IDLE, state: "done", trigger: "manual", request_nonce: "n1", rc: 0, can_refresh: false };

/** what GET answers: a status, or null for a store with no refresh service (a 503) */
let server: RefreshStatus | null;
let calls: string[];
beforeEach(() => {
  server = IDLE;
  calls = [];
  ctx.reload.mockClear();
  ctx.current = ctx.manifest();
  ctx.next = null;
  visibility = "visible";
  failing = 0;
  held = false;
  sessionStorage.clear();
  resetSession();                                // a test's 401 signs the page out: the next test starts signed in
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    expect(String(url)).toBe("./api/refresh");   // the stub is the only endpoint there is
    const method = init?.method ?? "GET";
    calls.push(method);
    if (method === "POST") { server = RUNNING; return new Response(JSON.stringify({ nonce: "n1" }), { status: 202 }); }
    return new Response(JSON.stringify(server), { status: 200 });
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

/** the Shell's arrangement: the provider, the (maybe closed) modal and, while on Data, the panel; `pill` adds the
 *  freshness pill, `looks` the tab's own looks */
function Shell({ modal = false, onData = false, pill = false, strip = false, looks = false, onOpenChange = () => {}, children }:
  { modal?: boolean; onData?: boolean; pill?: boolean; looks?: boolean; onOpenChange?: (v: boolean) => void;
    /** the phone's top bar: its pill hangs no line, its strip says it */
    strip?: boolean; children?: ReactNode }) {
  return (
    <RefreshProvider looks={looks}>
      {pill && <RefreshPill onClick={() => {}} />}
      {strip && <><RefreshPill onClick={() => {}} bubble={false} /><RefreshUpdatedStrip /></>}
      <RefreshModal open={modal} onOpenChange={onOpenChange} />
      {onData && <RefreshPanel />}
      {children}
    </RefreshProvider>
  );
}
const UPDATED = "Updated — the numbers on screen are current.";
/** the poll interval is 3 s (useRefresh POLL_MS); real timers, so allow a few */
const POLL_WAIT = { timeout: 8000 };

describe("RefreshModal keeps its refresh hook across close and reopen", () => {
  it("a run that finishes while the modal is closed is announced and reloads the data on reopen", async () => {
    const { rerender } = render(<Shell modal />);
    const start = await screen.findByRole("button", { name: "Refresh now" });
    await waitFor(() => expect((start as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(start);
    await screen.findByRole("button", { name: /Refreshing/ });
    expect(calls).toEqual(["GET", "POST", "GET"]);

    // shut the modal mid-run; the run finishes on the server while nobody is looking
    rerender(<Shell />);
    expect(screen.queryByRole("dialog") === null).toBe(true);
    server = DONE;
    expect(ctx.reload).not.toHaveBeenCalled();

    rerender(<Shell modal />);
    await screen.findByText(UPDATED);
    expect(ctx.reload).toHaveBeenCalledTimes(1);
    expect(calls.filter((m) => m === "POST").length, "one POST, from the click").toBe(1);
  });

  it("a fresh mount over a finished run of an earlier page view announces nothing and reloads nothing", async () => {
    // the counterpart: the stored nonce of a run that ended before this page view is dropped silently
    sessionStorage.setItem("ff_refresh_nonce", "n1");
    server = DONE;
    render(<Shell onData />);
    await screen.findByRole("button", { name: "Refresh now" });
    await waitFor(() => expect(calls).toEqual(["GET"]));
    expect(screen.queryByText(UPDATED) === null).toBe(true);
    expect(ctx.reload).not.toHaveBeenCalled();
  });
});

describe("a refresh started on the Data page outlives the page", () => {
  async function startOnData() {
    const view = render(<Shell onData />);
    const start = await screen.findByRole("button", { name: "Refresh now" });
    await waitFor(() => expect((start as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(start);
    await screen.findByRole("button", { name: /Refreshing/ });
    expect(calls).toEqual(["GET", "POST", "GET"]);
    return view;
  }

  it("leave Data, the run finishes, come back: announced and the data reloaded exactly once", async () => {
    const { rerender } = await startOnData();
    rerender(<Shell />);                                   // navigated away: the panel unmounts
    server = DONE;
    rerender(<Shell onData />);                            // back on Data
    await screen.findByText(UPDATED, {}, POLL_WAIT);
    await waitFor(() => expect(ctx.reload).toHaveBeenCalledTimes(1), POLL_WAIT);
    await new Promise((r) => setTimeout(r, 3500));        // a further poll must not reload again
    expect(ctx.reload).toHaveBeenCalledTimes(1);
    expect(calls.filter((m) => m === "POST").length, "one POST, from the click").toBe(1);
  }, 20_000);

  it("leave Data and never come back: the run is still followed and the data reloaded exactly once", async () => {
    const { rerender } = await startOnData();
    rerender(<Shell />);
    server = DONE;
    await waitFor(() => expect(ctx.reload).toHaveBeenCalledTimes(1), POLL_WAIT);
    await new Promise((r) => setTimeout(r, 3500));
    expect(ctx.reload).toHaveBeenCalledTimes(1);
    // ...and opening the modal later shows the outcome, without a second reload
    rerender(<Shell modal />);
    await screen.findByText(UPDATED);
    expect(ctx.reload).toHaveBeenCalledTimes(1);
    expect(calls.filter((m) => m === "POST").length).toBe(1);
  }, 20_000);

  it("with no view open, no run in flight and the tab hidden, nothing is fetched", async () => {
    visibility = "hidden";
    render(<Shell looks />);
    await new Promise((r) => setTimeout(r, 50));
    expect(calls).toEqual([]);
  });
});

describe("RefreshModal is a modal dialog", () => {
  it("aria-modal, focus lands on the panel (not the button), Tab stays inside, Escape closes, focus returns", async () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const onOpenChange = vi.fn();
    const { rerender } = render(<Shell modal onOpenChange={onOpenChange} />);
    const dialog = await screen.findByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(document.activeElement).toBe(dialog);
    // the label is on screen while the button is still disabled (status not loaded): a disabled
    // button is not focusable, so the wrap below would land on Close - wait for it to be enabled
    const refresh = await screen.findByRole("button", { name: "Refresh now" }) as HTMLButtonElement;
    await waitFor(() => expect(refresh.disabled).toBe(false));
    const close = screen.getByRole("button", { name: "Close" });
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });        // from the box itself, Shift+Tab wraps to the LAST control
    expect(document.activeElement, "wrapped onto the last control (Refresh now)").toBe(refresh);
    fireEvent.keyDown(document, { key: "Tab" });                        // ...and Tab from the last wraps to the FIRST (Close)
    expect(document.activeElement, "wrapped onto the first control (Close)").toBe(close);
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });        // Shift+Tab from the first wraps back to the last
    expect(document.activeElement).toBe(refresh);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onOpenChange).toHaveBeenCalledTimes(1);                      // one Escape, one close (no second listener)
    expect(onOpenChange).toHaveBeenCalledWith(false);
    rerender(<Shell onOpenChange={onOpenChange} />);
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it("opening the modal does not POST, and Enter on the freshly focused panel starts nothing", async () => {
    render(<Shell modal />);
    const dialog = await screen.findByRole("dialog");
    expect(document.activeElement).toBe(dialog);
    fireEvent.keyDown(dialog, { key: "Enter" });
    await waitFor(() => expect(calls).toEqual(["GET"]));
    expect(calls.includes("POST")).toBe(false);
  });

  it("a stalled run shows the didn't-finish notice even when an earlier outcome exists", async () => {
    const { rerender } = render(<Shell modal />);
    const start = await screen.findByRole("button", { name: "Refresh now" });
    await waitFor(() => expect((start as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(start);
    await screen.findByRole("button", { name: /Refreshing/ });
    server = DONE;
    await screen.findByText(UPDATED, {}, POLL_WAIT);
    server = { ...IDLE, state: "stalled" };
    rerender(<Shell />);
    rerender(<Shell modal />);                                          // a new view re-reads the status
    await screen.findByText(/didn't finish/, {}, POLL_WAIT);
    expect(screen.queryByText(UPDATED)).toBeNull();
  }, 20_000);
});

describe("the outcome line stops lingering", () => {
  /** drive the Shell on the Data page under a faked clock: start a run, let it finish, return the flush helper
   *  (and the timers pending while the page sat idle with no outcome, before the click) */
  async function finishedRunOnData(done: RefreshStatus) {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-11T17:00:00Z"));
    const { rerender, unmount } = render(<Shell onData />);
    const flush = (ms = 0) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
    await flush();
    const idleTimers = vi.getTimerCount();
    const start = screen.getByRole("button", { name: "Refresh now" }) as HTMLButtonElement;
    expect(start.disabled).toBe(false);
    fireEvent.click(start);
    await flush();
    expect(screen.getByRole("button", { name: /Refreshing/ })).toBeTruthy();
    server = done;
    await flush(3100);                                                  // the poll lands: the run ended
    return { flush, rerender, unmount, idleTimers };
  }
  const shown = () => screen.queryByText(UPDATED) !== null;

  it("an idle Data page retires the line 15 minutes after the client saw the run end (the provider's expiry timer)", async () => {
    const { flush } = await finishedRunOnData(DONE);
    expect(shown(), "announced when the run ended").toBe(true);
    const requests = calls.length;
    await flush(14 * 60_000);
    expect(shown(), "14 minutes on, still fresh").toBe(true);
    await flush(2 * 60_000);
    expect(shown(), "16 minutes on, retired - with nobody navigating, polling or clicking").toBe(false);
    expect(calls.filter((m) => m === "POST").length, "one POST, from the click").toBe(1);
    expect(calls.length, "an idle page costs no requests: the timer reads no status").toBe(requests);
    expect(ctx.reload).toHaveBeenCalledTimes(1);                         // retiring the line reloads nothing
  });

  it("counts from the server's finished_at when it has one (a run that ended before this client looked)", async () => {
    // finished 14 minutes before the poll saw it: only 1 minute of the TTL is left
    const { flush } = await finishedRunOnData({ ...DONE, finished_at: "2026-08-11T16:46:00Z" });
    expect(shown()).toBe(true);
    await flush(2 * 60_000);
    expect(shown(), "16 minutes after finished_at, though only 2 after it was seen").toBe(false);
  });

  // the timer is a lifecycle, not a fixture: none before there is an outcome to retire, exactly one
  // (a timeout at the expiry instant) while a fresh one is held, none once it has fired, none after unmount
  it("the expiry timer exists only while a fresh outcome is held: none before one, none after the provider unmounts", async () => {
    const { idleTimers, unmount, flush } = await finishedRunOnData(DONE);
    await flush();                                                      // jsdom's Storage defers its storage event with a 0 ms timeout
    expect(idleTimers, "an idle Data page with no outcome schedules nothing").toBe(0);
    expect(shown()).toBe(true);
    expect(vi.getTimerCount(), "one timeout is armed while the line is held").toBe(1);
    unmount();
    expect(vi.getTimerCount(), "unmounting the provider clears it").toBe(0);
  });

  it("the timer is a single timeout: once the line has expired nothing ticks for the rest of the session", async () => {
    const { flush } = await finishedRunOnData(DONE);
    await flush(14 * 60_000);                                           // (also drains jsdom's 0 ms storage-event timeout)
    expect(shown() && vi.getTimerCount() === 1, "14 minutes on: still held, still armed").toBe(true);
    await flush(2 * 60_000);
    expect(shown(), "16 minutes on: retired").toBe(false);
    expect(vi.getTimerCount(), "...and nothing is left ticking").toBe(0);
    await flush(60 * 60_000);
    expect(vi.getTimerCount(), "an hour later still nothing").toBe(0);
    expect(shown()).toBe(false);
  });

  it("an outcome that is already expired when it appears leaves no timer behind (a run that ended long before this client looked)", async () => {
    // finished 30 minutes before the poll saw it: the line never shows, so there is nothing to retire
    const { flush } = await finishedRunOnData({ ...DONE, finished_at: "2026-08-11T16:30:00Z" });
    expect(shown(), "never announced").toBe(false);
    await flush();                                                      // jsdom's Storage defers its storage event with a 0 ms timeout
    expect(vi.getTimerCount()).toBe(0);
    await flush(60_000);
    expect(vi.getTimerCount()).toBe(0);
  });

  // A timeout is not a clock: it can fire while the wall clock says the line is still fresh (the device clock
  // stepped back - an NTP correction, a manual change - or a coarse/throttled timer). The line then has to stay,
  // AND a new timeout has to be armed for what is left; otherwise nothing ever retires it.
  it("the expiry timer re-arms when it fires while the line is still fresh (the clock stepped back), then retires it", async () => {
    const { flush } = await finishedRunOnData(DONE);
    await flush();                                                      // jsdom's Storage defers its storage event with a 0 ms timeout
    expect(shown() && vi.getTimerCount() === 1, "held and armed").toBe(true);
    vi.setSystemTime(Date.now() - 5 * 60_000);                          // the device clock steps back 5 minutes
    await flush(15 * 60_000 + 1_000);                                   // the timeout fires on its own schedule
    expect(shown(), "by the wall clock the run ended ~10 minutes ago: still shown").toBe(true);
    expect(vi.getTimerCount(), "...and a timeout is armed for the remaining ~5 minutes").toBe(1);
    await flush(5 * 60_000);
    expect(shown(), "15 minutes by the wall clock: retired").toBe(false);
    expect(vi.getTimerCount(), "nothing left ticking").toBe(0);
  });

  it("a modal opened after the TTL has passed shows no stale line either (a new view re-reads, the outcome stays retired)", async () => {
    const { flush, rerender } = await finishedRunOnData(DONE);
    await flush(16 * 60_000);
    rerender(<Shell onData modal />);
    await flush();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(shown(), "neither the modal nor the panel behind it").toBe(false);
  });
  it("a modal opened inside the TTL shows the line in the modal as well as the panel", async () => {
    const { flush, rerender } = await finishedRunOnData(DONE);
    await flush(60_000);
    rerender(<Shell onData modal />);
    await flush();
    expect(screen.getAllByText(UPDATED).length).toBe(2);
  });
});

// ---------------------------------------------------------------------------------------------------------------------
// The status as a store answers it today: the schedule (the crontab's times, in its zone) and the ingest block. The
// exports on screen were built at 2026-01-01T07:00Z (generated_at, Denver's midnight); the ingest below is older.
const SCHEDULE = { times: ["01:00", "09:00", "17:00"], timezone: "America/Denver" };
const view = (over: Partial<RefreshStatus> = {}, ingest: Partial<IngestStatus> = {}): RefreshStatus => ({ ...IDLE,
  schedule: SCHEDULE, ...over,
  ingest: { wanted: false, last_started_at: "2025-12-31T21:00:00Z", last_finished_at: "2025-12-31T21:00:40Z", last_rc: 0, ...ingest } });
const ENDED = (rc: number, start = "2026-01-01T13:01:00Z", end = "2026-01-01T13:01:40Z") =>
  view({}, { last_started_at: start, last_finished_at: end, last_rc: rc });
const cycle = new Intl.DateTimeFormat(undefined, { hour: "numeric" }).resolvedOptions().hourCycle;
const t = (twelve: string, twentyFour: string) => (cycle === "h23" || cycle === "h24" ? twentyFour : twelve);
const NO_FEED = { setup: { accounts: 1, feed: false, import_files: 2, manual_assets: 0 } };

/** fake timers at 13:00 UTC on Jan 1 (06:00 in Denver); `flush(ms)` advances them inside act */
function fakeClock() {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-01-01T13:00:00Z"));
  return (ms = 0) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}
/** the next GETs that fail as a gateway's 502 (the store restarting) */
let failing = 0;
/** the next GET is slow: it answers only when `release` is called */
let held = false;
let release: (s: RefreshStatus) => void = () => {};
/** the store: GET answers `server` (a 503 while it is null); a POST is recorded with its body and answered by `post`
 *  (which may move `server`; a reply with HTTP 0 is no answer at all) */
function store(post: (body: unknown) => { http: number; body: unknown }) {
  const posts: unknown[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    expect(String(url)).toBe("./api/refresh");
    const method = init?.method ?? "GET";
    calls.push(method);
    if (method === "POST") {
      const body = init?.body === undefined ? undefined : JSON.parse(String(init.body));
      posts.push(body);
      const reply = post(body);
      if (reply.http === 0) throw new TypeError("network down");
      return new Response(JSON.stringify(reply.body), { status: reply.http });
    }
    if (held) {
      held = false;
      return new Promise<Response>((resolve) => {
        release = (s) => resolve(new Response(JSON.stringify(s), { status: 200 }));
      });
    }
    if (failing > 0) {
      failing -= 1;
      return new Response("bad gateway", { status: 502 });
    }
    if (server === null) return new Response(JSON.stringify({ error: "refresh not configured" }), { status: 503 });
    return new Response(JSON.stringify(server), { status: 200 });
  });
  return posts;
}
const finePrint = () => screen.getByTestId("refresh-fine-print").textContent ?? "";

describe("a visible tab looks at the refresh status by itself", () => {
  it("at once, then at most once a minute while visible, never while hidden, and at once on coming back after a minute", async () => {
    const flush = fakeClock();
    visibility = "visible";
    store(() => ({ http: 500, body: {} }));
    render(<Shell looks />);
    await flush(LOOK_FIRST_MS);
    expect(calls).toEqual(["GET"]);
    await flush(LOOK_EVERY_MS - 100);
    expect(calls.length, "not before the minute").toBe(1);
    await flush(200);
    expect(calls.length).toBe(2);
    act(() => setVisible("hidden"));
    await flush(5 * LOOK_EVERY_MS);
    expect(calls.length, "hidden: no look at all").toBe(2);
    act(() => setVisible("visible"));
    await flush();
    expect(calls.length, "back after more than a minute: a look at once").toBe(3);
    act(() => setVisible("hidden"));
    act(() => setVisible("visible"));
    await flush();
    expect(calls.length, "back within the minute: it waits for the minute").toBe(3);
    await flush(LOOK_EVERY_MS);
    expect(calls.length).toBe(4);
    expect(calls.includes("POST")).toBe(false);
  });

  it("one read on load, not two, when the Data page is open", async () => {
    const flush = fakeClock();
    visibility = "visible";
    server = view();
    store(() => ({ http: 500, body: {} }));
    render(<Shell onData looks />);
    await flush(LOOK_FIRST_MS + 10);
    expect(calls, "the page's own read stands for the first look").toEqual(["GET"]);
    await flush(LOOK_EVERY_MS);
    expect(calls).toEqual(["GET", "GET"]);
  });

  it("after the store answers that there is no refresh service, no look on a timer; coming back to the tab looks once", async () => {
    const flush = fakeClock();
    visibility = "visible";
    server = null;
    store(() => ({ http: 503, body: { error: "refresh not configured" } }));
    render(<Shell looks />);
    await flush(LOOK_FIRST_MS);
    expect(calls).toEqual(["GET"]);
    await flush(5 * LOOK_EVERY_MS);
    expect(calls.length, "no service: nothing on a timer").toBe(1);
    act(() => setVisible("hidden"));
    act(() => setVisible("visible"));
    await flush();
    expect(calls.length, "the tab came back: one look").toBe(2);
    await flush(5 * LOOK_EVERY_MS);
    expect(calls.length).toBe(2);
  });

  it("a look answered 401 (the sign-in ended): the pill says so itself, nothing is looked at on a timer, nothing navigates", async () => {
    const flush = fakeClock();
    visibility = "visible";
    const replace = vi.fn();
    vi.stubGlobal("location", { ...window.location, replace });
    vi.stubGlobal("fetch", async (url: string) => {
      expect(String(url)).toBe("./api/refresh");
      calls.push("GET");
      return new Response("{}", { status: 401 });
    });
    const { rerender } = render(<Shell pill looks />);
    await flush(LOOK_FIRST_MS);
    const pill = screen.getByRole("button", { name: /Signed out/ });
    expect(pill.textContent, "on the pill, not only in its tooltip").toBe("↻signed out");
    expect(pill.className, "a warning tone, not the fresh one").toContain("bg-amber-900/50");
    await flush(5 * LOOK_EVERY_MS);
    expect(calls.length, "signed out: no look on a timer").toBe(1);
    // the person opens the panel: its read is answered 401 too, and the page stays where it is
    rerender(<Shell pill looks modal />);
    await flush();
    expect(calls.length).toBe(2);
    expect(replace, "no read of the status ever navigates").not.toHaveBeenCalled();
    const line = screen.getByTestId("refresh-signed-out");
    expect(line.textContent).toBe("Your sign-in has ended. Sign in again");
    const link = within(line).getByRole("link", { name: "Sign in again" });
    expect(link.getAttribute("href"), "the person's click is what leaves the page").toBe("/login");
    expect(screen.queryByRole("button", { name: /Refresh now|Rebuild from files/ }), "nothing to post").toBeNull();
  });

  it("an ingest another device asked for, newer than the exports on screen, reloads them once, and a line says so for a while", async () => {
    const flush = fakeClock();
    visibility = "visible";
    server = view();
    store(() => ({ http: 500, body: {} }));
    render(<Shell onData pill looks />);
    await flush(LOOK_FIRST_MS);
    expect(ctx.reload, "the last ingest built what is on screen").not.toHaveBeenCalled();
    // a failed ingest after the exports: nothing new to load
    server = ENDED(2);
    await flush(LOOK_EVERY_MS + 10);
    expect(ctx.reload).not.toHaveBeenCalled();
    // one that succeeded: its exports are newer
    server = ENDED(0, "2026-01-01T13:02:00Z", "2026-01-01T13:02:40Z");
    ctx.next = ctx.manifest({ generated_at: "2026-01-01T06:02:20-07:00" });
    await flush(LOOK_EVERY_MS + 10);
    await flush();
    expect(ctx.reload).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(UPDATED), "no outcome line: it is no run this tab watched").toBeNull();
    const said = "Updated: the dashboard was rebuilt just now.";
    expect(screen.getByTestId("refresh-announce").textContent, "announced once, politely").toBe(said);
    expect(screen.getByTestId("refresh-announce").getAttribute("role")).toBe("status");
    expect(screen.getByTestId("refresh-updated").textContent, "shown under the pill").toBe(said);
    expect(screen.getByTestId("refresh-updated").getAttribute("aria-hidden"), "not read twice").toBe("true");
    expect(screen.getByTestId("refresh-updated").className, "taps go through it to what lies under").toContain("pointer-events-none");
    expect(document.activeElement, "no focus moved").toBe(document.body);
    await flush(UPDATED_LINE_MS + 10);
    expect(screen.queryByTestId("refresh-updated"), "it goes by itself").toBeNull();
    expect(screen.getByTestId("refresh-announce").textContent).toBe("");
    await flush(LOOK_EVERY_MS + 10);
    expect(ctx.reload, "the same ingest seen again reloads nothing").toHaveBeenCalledTimes(1);
  });

  it("the freshness pill keeps its size and takes a 44px hit area on a phone, drawn by nothing", () => {
    render(<Shell pill />);
    const pill = screen.getByRole("button", { name: /open the data refresh panel/ });
    const cls = pill.className.split(/\s+/);
    expect(cls).toEqual(expect.arrayContaining([...TAP_AREA.split(" "), "min-h-[36px]", "md:min-h-0"]));
    expect(cls, "not a 44px blob").not.toContain("min-h-11");
  });

  it("on a phone the line is a strip of the top bar, in the page's flow, never a bubble over the page", async () => {
    const flush = fakeClock();
    visibility = "visible";
    server = view();
    store(() => ({ http: 500, body: {} }));
    render(<Shell strip looks />);
    await flush(LOOK_FIRST_MS);
    server = ENDED(0, "2026-01-01T13:02:00Z", "2026-01-01T13:02:40Z");
    ctx.next = ctx.manifest({ generated_at: "2026-01-01T06:02:20-07:00" });
    await flush(LOOK_EVERY_MS + 10);
    await flush();
    const lines = screen.getAllByTestId("refresh-updated");
    expect(lines, "one line: the pill hangs none").toHaveLength(1);
    expect(lines[0].tagName).toBe("P");
    expect(lines[0].textContent).toBe("Updated: the dashboard was rebuilt just now.");
    expect(lines[0].getAttribute("aria-hidden"), "the status region says it once").toBe("true");
    expect(lines[0].className, "in the flow: it moves the page down instead of covering it").not.toMatch(/\babsolute\b/);
    await flush(UPDATED_LINE_MS + 10);
    expect(screen.queryByTestId("refresh-updated"), "it goes by itself").toBeNull();
  });

  it("a quiet reload that brings no newer build says nothing", async () => {
    const flush = fakeClock();
    visibility = "visible";
    server = ENDED(0, "2026-01-01T13:02:00Z", "2026-01-01T13:02:40Z");
    store(() => ({ http: 500, body: {} }));
    render(<Shell pill looks />);
    await flush(LOOK_FIRST_MS);
    await flush();
    expect(ctx.reload, "the reload was made").toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("refresh-updated"), "the exports did not move (the reload failed or brought the same)").toBeNull();
  });
});

// A refresh in flight is polled every three seconds, but never against a store that does not answer, a hidden tab or a
// sign-in that ended, and never past its bound.
describe("a refresh in flight and a store that stops answering", () => {
  const SCHEDULED: RefreshStatus = { ...IDLE, state: "running", trigger: "schedule", can_refresh: false };
  const gets = () => calls.filter((m) => m === "GET").length;
  /** advance a second at a time, so each answer is read before the next timer is due (one long jump runs the timers
   *  ahead of an answer that takes more than a microtask to read) */
  const walk = async (ms: number) => {
    for (let t = 0; t < ms; t += 1000) await act(async () => { await vi.advanceTimersByTimeAsync(Math.min(1000, ms - t)); });
  };

  it("a store that fails every read: at most one try a minute, none while the tab is hidden, none past the bound", async () => {
    const flush = fakeClock();
    server = SCHEDULED;
    store(() => ({ http: 500, body: {} }));
    render(<Shell onData />);
    await flush();
    expect(gets(), "the view's read finds the run in flight").toBe(1);
    await flush(3100);
    expect(gets(), "polled every three seconds while it answers").toBe(2);
    failing = Number.MAX_SAFE_INTEGER;                                  // the store stops answering
    const before = gets();
    await walk(10 * 60_000);
    expect(gets() - before, "ten minutes: one try a minute at most").toBeLessThanOrEqual(11);
    expect(gets() - before).toBeGreaterThanOrEqual(9);
    act(() => setVisible("hidden"));
    const hidden = gets();
    await walk(5 * 60_000);
    expect(gets(), "nothing while the tab is hidden").toBe(hidden);
    act(() => setVisible("visible"));
    await walk(3 * 60_000);                                             // past the run's bound (RUN_FOLLOW_MS from its first sight)
    const atBound = gets();
    await walk(30 * 60_000);
    expect(gets(), "past the bound nothing is polled").toBe(atBound);
  }, 60_000);

  it("a run that never reports back is polled to its bound, then left: the line says so, the button as the last answer allows", async () => {
    const flush = fakeClock();
    server = SCHEDULED;
    store(() => ({ http: 500, body: {} }));
    render(<Shell onData />);
    await flush();
    await walk(RUN_FOLLOW_MS + 1000);
    expect(gets(), "polled every three seconds up to the bound").toBeGreaterThan(RUN_FOLLOW_MS / 3000 - 10);
    const atBound = gets();
    await walk(10 * 60_000);
    expect(gets(), "no poll past the bound").toBe(atBound);
    expect(finePrint()).toContain("The refresh has not reported back — it may have stopped. Open this again later to look.");
    const button = screen.getByRole("button", { name: "Refreshing…" }) as HTMLButtonElement;
    expect(button.disabled, "the last answer says it is still running").toBe(true);
    expect(button.querySelector(".animate-pulse"), "no spinner for a run nobody follows").toBeNull();
  }, 60_000);

  it("a 401 while it runs: polling stops, and the panel says the sign-in has ended", async () => {
    const flush = fakeClock();
    server = SCHEDULED;
    store(() => ({ http: 500, body: {} }));
    render(<Shell onData />);
    await flush();
    vi.stubGlobal("fetch", async () => {
      calls.push("GET");
      return new Response("{}", { status: 401 });
    });
    await flush(3100);
    const after = gets();
    await walk(10 * 60_000);
    expect(gets(), "signed out: nothing more is asked").toBe(after);
    expect(screen.getByTestId("refresh-signed-out").textContent).toBe("Your sign-in has ended. Sign in again");
  }, 60_000);

  it("a store that comes back: the follow resumes and completes", async () => {
    const flush = fakeClock();
    server = { ...SCHEDULED, state: "running", trigger: "manual", request_nonce: "n1" };
    store(() => ({ http: 500, body: {} }));
    render(<Shell onData />);
    await flush();
    failing = 2;
    await flush(3100);                                                  // the first failure
    expect(finePrint()).toContain("The dashboard's store is unreachable");
    await walk(60_000);                                                 // the slow retry: the second failure
    server = DONE;
    await walk(61_000);                                                 // the next slow retry answers: the run ended
    expect(screen.getByText(UPDATED)).toBeTruthy();
    expect(ctx.reload).toHaveBeenCalledTimes(1);
  }, 60_000);
});

describe("the readout with a bank feed", () => {
  it("names the last feed fetch and the rebuild, and the schedule the status reports, on the household's clock", async () => {
    const flush = fakeClock();
    server = view();
    store(() => ({ http: 202, body: { accepted: true, nonce: "n9" } }));
    render(<Shell onData />);
    await flush();
    const rows = document.body.textContent ?? "";
    expect(rows).toContain("Last feed fetchnot fetched yet");
    expect(rows).toContain(`Dashboard rebuilt6h ago · Jan 1, ${t("12:00 AM", "00:00")} MST`);
    expect(finePrint()).toBe(`Refreshes automatically at ${t("1:00 AM", "01:00")}, ${t("9:00 AM", "09:00")} and `
      + `${t("5:00 PM", "17:00")} (Mountain Time). 6 of 6 manual refreshes left today.`);
    fireEvent.click(screen.getByRole("button", { name: "Refresh now" }));
    await flush();
    expect(calls.filter((m) => m === "POST").length).toBe(1);
  });
  it("Refresh now posts no body: a refresh, the nonce flow", async () => {
    const flush = fakeClock();
    server = view();
    const posts = store(() => ({ http: 202, body: { accepted: true, nonce: "n9" } }));
    render(<Shell onData />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Refresh now" }));
    await flush();
    expect(posts).toEqual([undefined]);
    expect(sessionStorage.getItem("ff_refresh_nonce")).toBe("n9");
  });
  it("with no schedule in the status, no schedule sentence: a line says its times are not known yet", async () => {
    const flush = fakeClock();
    server = view({ schedule: null });
    store(() => ({ http: 500, body: {} }));
    render(<Shell onData />);
    await flush();
    expect(finePrint()).toBe("The times of the automatic refresh are not known yet: they show once the scheduler has "
      + "started. 6 of 6 manual refreshes left today.");
  });
  it("a fetch that asks for a sign-in names the bank to sign in to at the feed provider, and every error it reported", async () => {
    const flush = fakeClock();
    server = view({ reauth_needed: ["Sample Bank"], feed_errors: ["Sample Bank: Sign in again",
      "Sample Credit Union: Failed to get account information. Try again later."] });
    store(() => ({ http: 500, body: {} }));
    render(<Shell onData />);
    await flush();
    expect(screen.getByTestId("refresh-reauth").textContent).toBe("⚠ Sign in to Sample Bank again at SimpleFIN Bridge: "
      + "until you do, the bank feed cannot bring its balances up to date.");
    expect([...screen.getByTestId("refresh-feed-errors").querySelectorAll("li")].map((li) => li.textContent)).toEqual([
      "The bank feed reported a problem with Sample Bank: Sign in again.",
      "The bank feed reported a problem with Sample Credit Union: Failed to get account information. Try again later."]);
    expect(screen.getByRole("button", { name: "Refresh now" })).toBeTruthy();
  });
  it("a fetch that reported nothing, or a run with no feed, shows neither line", async () => {
    const flush = fakeClock();
    server = view({ reauth_needed: [], feed_errors: [], files_only: true });
    store(() => ({ http: 500, body: {} }));
    render(<Shell onData />);
    await flush();
    expect(screen.queryByTestId("refresh-reauth")).toBeNull();
    expect(screen.queryByTestId("refresh-feed-errors")).toBeNull();
  });
});

describe("Rebuild from files: a household with no bank feed", () => {
  /** the Data panel of a household with no feed, its store answering a rebuild with a 202 that touches the marker */
  async function noFeedPanel(reply: { http: number; body: unknown } = { http: 202, body: { accepted: true, mode: "ingest" } }) {
    const flush = fakeClock();
    ctx.current = ctx.manifest(NO_FEED);
    server = view();
    const posts = store(() => {
      if (reply.http === 202) server = view({}, { wanted: true });
      return reply;
    });
    const { rerender } = render(<Shell onData />);
    await flush();
    return { flush, posts, rerender,
             button: () => screen.queryByRole("button", { name: /Rebuild|Starting|Queued/ }) as HTMLButtonElement | null };
  }
  const line = () => screen.queryByTestId("rebuild-line")?.textContent ?? null;

  it("idle: one row, the button and what the scheduled refresh does, with no word of a feed, a fetch or a pull", async () => {
    const { button } = await noFeedPanel();
    expect(button()?.textContent).toBe("Rebuild from files");
    expect(button()?.disabled).toBe(false);
    expect(screen.queryByText("Last feed fetch")).toBeNull();
    expect(finePrint()).toBe("Reads the household's files again and rebuilds the dashboard."
      + `Rebuilds automatically at ${t("1:00 AM", "01:00")}, ${t("9:00 AM", "09:00")} and ${t("5:00 PM", "17:00")} (Mountain Time).`);
    expect(document.body.textContent).not.toMatch(/feed|fetch|pull/i);
  });

  it("asks for an ingest with no fetch, follows it through waiting and running to its end, then reloads and says so", async () => {
    const { flush, posts, button } = await noFeedPanel();
    fireEvent.click(button()!);
    await flush();
    expect(posts, "an ingest, never a refresh").toEqual([{ mode: "ingest" }]);
    expect(button()?.textContent).toContain("Rebuilding…");
    expect(button()?.disabled).toBe(true);
    expect(finePrint()).toContain("It starts within about a minute of being asked.");
    server = view({}, { last_started_at: "2026-01-01T13:01:00Z", last_finished_at: null, last_rc: null });
    await flush(3100);
    expect(finePrint()).toContain("Reading the files and rebuilding the dashboard.");
    server = ENDED(0);
    ctx.next = ctx.manifest({ ...NO_FEED, generated_at: "2026-01-01T06:01:20-07:00" });
    await flush(3100);
    await flush();
    expect(ctx.reload).toHaveBeenCalledTimes(1);
    expect(line()).toBe("Rebuilt — the numbers on screen are current.");
    expect(button()?.textContent).toBe("Rebuild from files");
    expect(screen.queryByText(UPDATED), "an ingest is no refresh: the refresh's line is not written").toBeNull();
    const polled = calls.length;
    await flush(30_000);
    expect(calls.length, "ended: nothing more is asked").toBe(polled);
    expect(document.body.textContent).not.toMatch(/feed|fetch|pull/i);
  });

  it("a failure says how it ended in words; an interrupted ingest is run again and followed on", async () => {
    const { flush, button } = await noFeedPanel();
    fireEvent.click(button()!);
    await flush();
    server = view({}, { wanted: true, last_started_at: "2026-01-01T13:01:00Z", last_finished_at: "2026-01-01T13:01:05Z", last_rc: 143 });
    await flush(3100);
    expect(button()?.textContent, "143: still wanted, the refresher runs it again").toContain("Rebuilding…");
    server = ENDED(-1);
    await flush(3100);
    expect(line()).toBe("The rebuild timed out. The dashboard still shows what it was last built from.");
    expect(button()?.disabled).toBe(false);
  });

  it("past the day's cap: refused, in words; nothing is followed", async () => {
    const { flush, button } = await noFeedPanel({ http: 429, body: { accepted: false, reason: "ingest_cap", retry_after_s: 3600 } });
    fireEvent.click(button()!);
    await flush();
    expect(line()).toBe("Today's rebuilds are used up: try again after midnight.");
    const reads = calls.length;
    await flush(10_000);
    expect(calls.length).toBe(reads);
  });

  it("the follow ends at its bound and never spins: never seen to start, the line says so, and polling stops", async () => {
    const { flush, button } = await noFeedPanel();
    fireEvent.click(button()!);
    await flush();
    await flush(FOLLOW_MS + 100);
    await flush(FOLLOW_GRACE_MS + 100);
    expect(line()).toBe("The rebuild has not started — the refresh service may be stopped.");
    expect(button()?.textContent).toBe("Rebuild from files");
    const reads = calls.length;
    await flush(60_000);
    expect(calls.length, "never spins for ever").toBe(reads);
  });

  it("seen running and still running at the bound: it has not finished yet", async () => {
    const { flush, button } = await noFeedPanel();
    fireEvent.click(button()!);
    await flush();
    server = view({}, { last_started_at: "2026-01-01T13:01:00Z", last_finished_at: null, last_rc: null });
    await flush(3100);
    await flush(FOLLOW_MS + 100);
    await flush(FOLLOW_GRACE_MS + 100);
    expect(line()).toBe("The rebuild has not finished yet: the dashboard updates once it has.");
  });

  it("one failed read while it is followed (a 502 while the store restarts) is passed over: the follow completes", async () => {
    const { flush, button } = await noFeedPanel();
    fireEvent.click(button()!);
    await flush();
    failing = 1;
    await flush(3100);
    expect(button(), "while the store does not answer, nothing to press").toBeNull();
    expect(finePrint()).toContain("The dashboard's store is unreachable");
    server = view({}, { last_started_at: "2026-01-01T13:01:00Z", last_finished_at: null, last_rc: null });
    await flush(3100);
    expect(calls.filter((m) => m === "GET").length, "after a read with no answer, no try three seconds later").toBe(3);
    await flush(LOOK_EVERY_MS);                                          // the next try, at the slow cadence
    expect(button()?.textContent, "the next answer brings the control back, still following").toContain("Rebuilding…");
    server = ENDED(0);
    ctx.next = ctx.manifest({ ...NO_FEED, generated_at: "2026-01-01T06:01:20-07:00" });
    await flush(3100);
    await flush();
    expect(line()).toBe("Rebuilt — the numbers on screen are current.");
    expect(button()?.textContent).toBe("Rebuild from files");
  });

  it("a store that never answers after the ask: at the bound the line says the store could not be reached, not that it never started", async () => {
    const { flush, button } = await noFeedPanel();
    failing = Number.MAX_SAFE_INTEGER;
    fireEvent.click(button()!);
    await flush();
    await flush(FOLLOW_MS + 100);
    await flush(FOLLOW_GRACE_MS + 100);
    expect(line()).toBe("The dashboard's store could not be reached since the rebuild was asked for: the dashboard updates "
      + "once it answers and the rebuild has run.");
    expect(calls.filter((m) => m === "GET").length, "the view's read, the ask's read and the bound's look: no fast poll")
      .toBeLessThanOrEqual(1 + 1 + 1 + Math.ceil(FOLLOW_MS / LOOK_EVERY_MS));
  });

  it("the store's 503 while it is followed: no service, nothing to press; the next good read brings the button back", async () => {
    const { flush, button, rerender } = await noFeedPanel();
    fireEvent.click(button()!);
    await flush();
    server = null;
    await flush(3100);
    expect(button()).toBeNull();
    expect(finePrint()).toContain("No refresh service answers here (refresh not configured)");
    expect(line(), "no line of its own: the live state says it").toBeNull();
    server = view();
    rerender(<Shell onData modal />);                                   // the person opens the control: a read
    await flush();
    expect(screen.getAllByRole("button", { name: "Rebuild from files" }).length).toBe(2);
  });

  it("the request never reaches the store: nothing was asked, it says so, and the button is back", async () => {
    const { flush, button } = await noFeedPanel({ http: 0, body: null });
    const reads = calls.length;
    fireEvent.click(button()!);
    await flush();
    expect(line()).toBe("The request did not reach the dashboard's store, so nothing was rebuilt: try again in a moment.");
    expect(button()?.textContent).toBe("Rebuild from files");
    expect(button()?.disabled).toBe(false);
    await flush(FOLLOW_MS + FOLLOW_GRACE_MS + 1000);
    expect(calls.filter((m) => m === "GET").length, "nothing is followed").toBe(reads);
  });

  it("an answer to a read sent before the ask never speaks for it: a slow early read that says ended is dropped", async () => {
    const { flush, rerender } = await noFeedPanel();
    held = true;                                                        // the next read is slow
    rerender(<Shell onData modal />);                                   // a new view's read goes out and hangs
    await flush();
    fireEvent.click(screen.getAllByRole("button", { name: "Rebuild from files" })[0]);
    await flush();
    expect(screen.getAllByRole("button", { name: /Rebuilding/ }).length, "the panel and the modal").toBe(2);
    release(ENDED(0));                                                  // the early read answers now, after the ask's
    await flush();
    expect(ctx.reload, "its answer is dropped: no end was seen").not.toHaveBeenCalled();
    server = ENDED(2);
    await flush(3100);
    expect(screen.getAllByTestId("rebuild-line").map((e) => e.textContent))
      .toEqual(Array(2).fill("The rebuild failed (code 2). The dashboard still shows what it was last built from."));
  });

  it("a rebuild found under way that this tab did not ask for is a rebuild under way, never a saved change", async () => {
    fakeClock();
    ctx.current = ctx.manifest(NO_FEED);
    server = view({}, { wanted: true });
    store(() => ({ http: 500, body: {} }));
    render(<Shell onData />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole("button", { name: /Rebuilding/ })).toBeTruthy();
    expect(finePrint()).toContain("A rebuild is under way.");
    expect(finePrint()).not.toContain("saved change");
  });

  it("an ingest a save of this tab asked for is said to carry the saved change", async () => {
    fakeClock();
    ctx.current = ctx.manifest(NO_FEED);
    server = view();
    store(() => ({ http: 500, body: {} }));
    render(<Shell onData><SaveProbe /></Shell>);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    server = view({}, { wanted: true });
    fireEvent.click(screen.getByRole("button", { name: "probe: saved" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(finePrint()).toContain("Rebuilding the dashboard with a saved change.");
  });

  it("no refresh service: nothing to press, and it says so", async () => {
    fakeClock();
    ctx.current = ctx.manifest(NO_FEED);
    server = null;
    store(() => ({ http: 503, body: { error: "refresh not configured" } }));
    render(<Shell onData />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.queryByRole("button", { name: /Rebuild/ })).toBeNull();
    expect(finePrint()).toBe("No refresh service answers here (refresh not configured), so nothing can be started from the "
      + "dashboard: re-run the ingest to update it.");
  });
});

/** what a save that asks for an ingest does with the shared refresh: read it and follow the ingest, as the save's */
function SaveProbe() {
  const api = useRefreshFollow();
  return <button type="button" onClick={() => api.watch(true, "save")}>probe: saved</button>;
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetSession, sessionState } from "./session";
import { INGEST_FOLLOW_MS, ingestBusy, ingestEnded, ingestRunning, newerIngest, RUN_FOLLOW_MS, type RefreshStatus,
  useRefresh } from "./useRefresh";

/** The question this hook has to answer correctly is "did MY refresh finish?",
 *  because the answer decides when the dashboard refetches 2.5 MB of exports.
 *  A scheduled run and a manual one look identical apart from the nonce. */

const IDLE: RefreshStatus = {
  state: "idle", trigger: null, request_nonce: null, queued_nonce: null,
  started_at: null, finished_at: null, rc: null, steps_failed: [], warnings: [],
  can_refresh: true, reason: "ok", retry_after_s: 0, used_today: 0,
  limit_per_day: 6, last_refresh_at: null,
};
const status = (over: Partial<RefreshStatus>): RefreshStatus => ({ ...IDLE, ...over });

/** Answers each fetch from a scripted queue, keeping the last entry once the
 *  script runs out (polling would otherwise fall off the end). */
function stubFetch(script: Array<{ http: number; body: unknown }>) {
  const calls: string[] = [];
  let i = 0;
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${url}`);
    const step = script[Math.min(i, script.length - 1)];
    i += 1;
    return new Response(JSON.stringify(step.body), { status: step.http });
  });
  return calls;
}

beforeEach(() => { sessionStorage.clear(); resetSession(); });
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useRefresh", () => {
  it("reads the status when opened and reports the budget", async () => {
    stubFetch([{ http: 200, body: status({ used_today: 2 }) }]);
    const { result } = renderHook(() => useRefresh({ enabled: true, onDone: () => {} }));
    await waitFor(() => expect(result.current.status?.used_today).toBe(2));
    expect(result.current.busy).toBe(false);
    expect(result.current.unreachable).toBe(false);
  });

  it("fetches nothing while closed — the pill costs no requests", async () => {
    const calls = stubFetch([{ http: 200, body: IDLE }]);
    renderHook(() => useRefresh({ enabled: false, onDone: () => {} }));
    await new Promise((r) => setTimeout(r, 20));
    expect(calls).toEqual([]);
  });

  it("polls a queued run to completion, then reloads the data once", async () => {
    vi.useFakeTimers();
    const onDone = vi.fn();
    stubFetch([
      { http: 200, body: IDLE },                                        // open
      { http: 202, body: { accepted: true, nonce: "abc" } },            // POST
      { http: 200, body: status({ state: "running", request_nonce: "abc" }) },
      { http: 200, body: status({ state: "running", request_nonce: "abc" }) },
      { http: 200, body: status({ state: "done", request_nonce: "abc", rc: 0 }) },
    ]);
    const { result } = renderHook(() => useRefresh({ enabled: true, onDone }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });

    await act(async () => { await result.current.trigger(); });
    expect(sessionStorage.getItem("ff_refresh_nonce")).toBe("abc");
    expect(result.current.busy).toBe(true);

    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(result.current.busy).toBe(false);
    expect(result.current.outcome).toEqual(expect.objectContaining({ mine: true, ok: true, stepsFailed: [] }));
    // the nonce is spent — a later scheduled run must not re-announce as mine
    expect(sessionStorage.getItem("ff_refresh_nonce")).toBeNull();
  });

  it("reports which step failed when the run came back non-zero", async () => {
    vi.useFakeTimers();
    stubFetch([
      { http: 200, body: IDLE },
      { http: 202, body: { accepted: true, nonce: "abc" } },
      { http: 200, body: status({ state: "running", request_nonce: "abc" }) },
      { http: 200, body: status({ state: "done", request_nonce: "abc", rc: 1,
                                 steps_failed: ["SimpleFIN-fetch"] }) },
    ]);
    const { result } = renderHook(() => useRefresh({ enabled: true, onDone: () => {} }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await result.current.trigger(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });

    expect(result.current.outcome).toEqual(
      expect.objectContaining({ mine: true, ok: false, stepsFailed: ["SimpleFIN-fetch"] }));
  });

  it("rides along with a scheduled run and still reloads, but doesn't claim it", async () => {
    vi.useFakeTimers();
    const onDone = vi.fn();
    stubFetch([
      { http: 200, body: status({ state: "running", trigger: "schedule" }) },
      { http: 200, body: status({ state: "done", trigger: "schedule", rc: 0 }) },
    ]);
    const { result } = renderHook(() => useRefresh({ enabled: true, onDone }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.busy).toBe(true);

    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(result.current.outcome?.mine).toBe(false);
  });

  it("stamps the outcome with the server's finished_at and the instant this client saw the run end", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-11T17:00:00Z"));
    stubFetch([
      { http: 200, body: status({ state: "running", trigger: "schedule" }) },
      { http: 200, body: status({ state: "done", trigger: "schedule", rc: 0, finished_at: "2026-08-11T17:00:01Z" }) },
    ]);
    const { result } = renderHook(() => useRefresh({ enabled: true, onDone: () => {} }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(result.current.outcome).toEqual({
      mine: false, ok: true, stepsFailed: [],
      finishedAt: "2026-08-11T17:00:01Z", seenAt: expect.any(String),
    });
    // seen when the poll landed (3 s after the first read, on the faked clock), as an ISO instant
    const seen = Date.parse(result.current.outcome!.seenAt);
    expect(seen).toBeGreaterThanOrEqual(Date.parse("2026-08-11T17:00:03Z"));
    expect(seen).toBeLessThanOrEqual(Date.parse("2026-08-11T17:00:03.100Z"));
  });

  it("keeps a null finishedAt when the server has no stamp (the TTL then counts from seenAt)", async () => {
    vi.useFakeTimers();
    stubFetch([
      { http: 200, body: status({ state: "running", trigger: "schedule" }) },
      { http: 200, body: status({ state: "done", trigger: "schedule", rc: 0 }) },
    ]);
    const { result } = renderHook(() => useRefresh({ enabled: true, onDone: () => {} }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(result.current.outcome?.finishedAt).toBeNull();
    expect(Number.isFinite(Date.parse(result.current.outcome!.seenAt))).toBe(true);
  });

  it("a run seen in flight clears the previous outcome (the new run owns the readout)", async () => {
    vi.useFakeTimers();
    stubFetch([
      { http: 200, body: IDLE }, { http: 202, body: { accepted: true, nonce: "abc" } },
      { http: 200, body: status({ state: "done", request_nonce: "abc", rc: 0 }) },     // finished before the first poll
      { http: 200, body: status({ state: "running", trigger: "schedule" }) },          // a scheduled run starts
    ]);
    const { result, rerender } = renderHook(({ k }) => useRefresh({ enabled: true, viewKey: k, onDone: () => {} }), { initialProps: { k: 0 } });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await result.current.trigger(); });
    expect(result.current.outcome?.mine).toBe(true);
    rerender({ k: 1 });                                                 // a new view re-reads: the status now says running
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.busy).toBe(true);
    expect(result.current.outcome).toBeNull();
  });

  it("keeps the rate-limit refusal visible and spends no nonce", async () => {
    stubFetch([
      { http: 200, body: IDLE },
      { http: 429, body: { accepted: false, reason: "min_gap", retry_after_s: 540 } },
      { http: 200, body: status({ can_refresh: false, reason: "min_gap",
                                  retry_after_s: 540, used_today: 1 }) },
    ]);
    const { result } = renderHook(() => useRefresh({ enabled: true, onDone: () => {} }));
    await waitFor(() => expect(result.current.status).not.toBeNull());
    await act(async () => { await result.current.trigger(); });

    expect(sessionStorage.getItem("ff_refresh_nonce")).toBeNull();
    expect(result.current.status?.can_refresh).toBe(false);
    expect(result.current.status?.retry_after_s).toBe(540);
    expect(result.current.error).toBeNull();   // a refusal is not an error
  });

  it("attaches to the run already in flight rather than starting a second", async () => {
    stubFetch([
      { http: 200, body: IDLE },
      { http: 200, body: { accepted: false, reason: "already_running", nonce: "other" } },
      { http: 200, body: status({ state: "running", request_nonce: "other" }) },
    ]);
    const { result } = renderHook(() => useRefresh({ enabled: true, onDone: () => {} }));
    await waitFor(() => expect(result.current.status).not.toBeNull());
    await act(async () => { await result.current.trigger(); });

    expect(sessionStorage.getItem("ff_refresh_nonce")).toBe("other");
    expect(result.current.busy).toBe(true);
  });

  it("does not announce a run that finished before this page was opened", async () => {
    const onDone = vi.fn();
    sessionStorage.setItem("ff_refresh_nonce", "abc");
    stubFetch([{ http: 200, body: status({ state: "done", request_nonce: "abc", rc: 0 }) }]);
    renderHook(() => useRefresh({ enabled: true, onDone }));

    // this page load already fetched the fresh exports — reloading them again
    // and popping "Updated" for a refresh nobody is watching would be noise
    await waitFor(() => expect(sessionStorage.getItem("ff_refresh_nonce")).toBeNull());
    expect(onDone).not.toHaveBeenCalled();
  });

  it("follows a run it saw in flight after every view closed, reloads once, then goes quiet", async () => {
    vi.useFakeTimers();
    const onDone = vi.fn();
    const calls = stubFetch([
      { http: 200, body: status({ state: "running", trigger: "schedule" }) },
      { http: 200, body: status({ state: "done", trigger: "schedule", rc: 0 }) },
    ]);
    const { rerender } = renderHook(({ enabled }) => useRefresh({ enabled, onDone }),
      { initialProps: { enabled: true } });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    rerender({ enabled: false });   // the modal closed / the Data page unmounted mid-run
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(calls.length).toBe(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(calls.length, "idle again: no polling").toBe(2);
  });

  describe("each run is followed for RUN_FOLLOW_MS from its own first sight", () => {
    const MIN = 60_000;
    /** advance the fake clock in one-second steps, so each poll's answer is read before the next one is armed */
    const walk = async (ms: number) => {
      for (let t = 0; t < ms; t += 1000) await act(async () => { await vi.advanceTimersByTimeAsync(Math.min(1000, ms - t)); });
    };
    /** the store answers by the minute since `start`: each phase from its minute on, the last one from then on */
    const storeBy = (start: number, phases: [number, RefreshStatus][]) => {
      vi.stubGlobal("fetch", async () => {
        const min = (Date.now() - start) / MIN;
        const body = [...phases].reverse().find(([from]) => min >= from)![1];
        return new Response(JSON.stringify(body), { status: 200 });
      });
    };

    it("a manual run that starts the moment a scheduled one ends is followed from its own start, and its end is seen", async () => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2026-08-11T17:00:00Z"));
      const start = Date.now();
      const onDone = vi.fn();
      // the status never stops reporting a run: the scheduled one until minute ten, the queued manual one from then
      storeBy(start, [
        [0, status({ state: "running", trigger: "schedule", started_at: "2026-08-11T17:00:00Z" })],
        [10, status({ state: "running", trigger: "manual", request_nonce: "abc", started_at: "2026-08-11T17:10:00Z" })],
        [21, status({ state: "done", trigger: "manual", request_nonce: "abc", started_at: "2026-08-11T17:10:00Z",
          finished_at: "2026-08-11T17:21:00Z", rc: 0 })],
      ]);
      const { result } = renderHook(() => useRefresh({ enabled: true, onDone }));
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(result.current.busy).toBe(true);
      await walk(20 * MIN);
      expect(result.current.runLost, "minute twenty: the second run is ten minutes into its own bound").toBe(false);
      expect(result.current.busy).toBe(true);
      await walk(2 * MIN);
      expect(onDone, "its end is seen and reloads the data").toHaveBeenCalledTimes(1);
      expect(result.current.busy).toBe(false);
      expect(result.current.outcome).toEqual(expect.objectContaining({ ok: true }));
    }, 60_000);

    it("one run past its bound is no longer followed, and an end after that is not seen", async () => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2026-08-11T17:00:00Z"));
      const start = Date.now();
      const onDone = vi.fn();
      const running = status({ state: "running", trigger: "schedule", started_at: "2026-08-11T17:00:00Z" });
      storeBy(start, [[0, running], [RUN_FOLLOW_MS / MIN + 3, status({ ...running, state: "done", rc: 0 })]]);
      const { result } = renderHook(() => useRefresh({ enabled: true, onDone }));
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      await walk(RUN_FOLLOW_MS + MIN);
      expect(result.current.runLost, "past the bound").toBe(true);
      expect(result.current.busy).toBe(false);
      await walk(5 * MIN);
      expect(onDone, "nothing is polled past the bound, so the end is not seen").not.toHaveBeenCalled();
    }, 60_000);
  });

  it("re-reads the status for each new view of a shared hook (viewKey)", async () => {
    const calls = stubFetch([{ http: 200, body: IDLE }]);
    const { rerender } = renderHook(({ k }) => useRefresh({ enabled: true, viewKey: k, onDone: () => {} }),
      { initialProps: { k: 0 } });
    await waitFor(() => expect(calls.length).toBe(1));
    rerender({ k: 1 });
    await waitFor(() => expect(calls.length).toBe(2));
  });

  it("degrades to unreachable rather than throwing at the user", async () => {
    vi.stubGlobal("fetch", async () => { throw new TypeError("network down"); });
    const { result } = renderHook(() => useRefresh({ enabled: true, onDone: () => {} }));
    await waitFor(() => expect(result.current.unreachable).toBe(true));
    expect(result.current.status).toBeNull();
  });
});

describe("useRefresh: an ingest without a fetch", () => {
  const ingest = (over: Partial<NonNullable<RefreshStatus["ingest"]>>) =>
    status({ ingest: { wanted: false, last_started_at: null, last_finished_at: null, last_rc: null, ...over } });
  const WANTED = ingest({ wanted: true, last_started_at: "2026-07-01T10:00:00Z", last_finished_at: "2026-07-01T10:01:00Z", last_rc: 0 });
  const RUNNING = ingest({ last_started_at: "2026-07-02T10:00:00Z" });
  const ENDED = ingest({ last_started_at: "2026-07-02T10:00:00Z", last_finished_at: "2026-07-02T10:00:40Z", last_rc: 0 });

  it("counts as busy while it is wanted or running, is polled, and its end reloads the exports once", async () => {
    vi.useFakeTimers();
    const onDone = vi.fn();
    const calls = stubFetch([{ http: 200, body: WANTED }, { http: 200, body: RUNNING }, { http: 200, body: ENDED }]);
    const { result } = renderHook(() => useRefresh({ enabled: true, onDone }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.ingestBusy, "wanted").toBe(true);
    expect(result.current.busy).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(result.current.ingestBusy, "running").toBe(true);
    expect(onDone).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(result.current.ingestBusy).toBe(false);
    expect(result.current.outcome, "an ingest is no refresh: the refresh's outcome line is not written").toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(calls.length, "idle again: no polling").toBe(3);
  });

  it("is followed for INGEST_FOLLOW_MS at most: then it is no longer polled and no longer counts as busy", async () => {
    vi.useFakeTimers();
    const calls = stubFetch([{ http: 200, body: WANTED }]);       // a capped or stuck ingest: wanted for ever
    const { result } = renderHook(() => useRefresh({ enabled: true, onDone: () => {} }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.ingestBusy).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(INGEST_FOLLOW_MS + 100); });
    const polled = calls.length;
    expect(polled, "no faster than every 3 s meanwhile").toBeLessThanOrEqual(Math.ceil(INGEST_FOLLOW_MS / 3000) + 1);
    expect(result.current.ingestBusy).toBe(false);
    expect(result.current.busy).toBe(false);
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(calls.length, "the follow ended: nothing more is asked").toBe(polled);
  });

  it("watch() reads the status with no view open, follows the ingest it shows, and counts every read", async () => {
    vi.useFakeTimers();
    const onDone = vi.fn();
    const calls = stubFetch([{ http: 200, body: WANTED }, { http: 200, body: ENDED }]);
    const { result } = renderHook(() => useRefresh({ enabled: false, onDone }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(calls).toEqual([]);
    expect(result.current.reads).toBe(0);
    act(() => result.current.watch());
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.reads).toBe(1);
    expect(result.current.ingestBusy).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(result.current.reads).toBe(2);
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("a read that fails is a read too, and says the service is unreachable", async () => {
    stubFetch([{ http: 503, body: { error: "refresh not configured" } }]);
    const { result } = renderHook(() => useRefresh({ enabled: false, onDone: () => {} }));
    act(() => result.current.watch());
    await waitFor(() => expect(result.current.reads).toBe(1));
    expect(result.current.unreachable).toBe(true);
  });

  it("a refresh's own ingest step ending inside the refresh reloads once, at the refresh's end", async () => {
    vi.useFakeTimers();
    const onDone = vi.fn();
    stubFetch([
      { http: 200, body: { ...RUNNING, state: "running" } },
      { http: 200, body: { ...ENDED, state: "running" } },
      { http: 200, body: { ...ENDED, state: "done", rc: 0 } },
    ]);
    renderHook(() => useRefresh({ enabled: true, onDone }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(onDone, "the ingest step ended, the refresh has not").not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("a status with no ingest block (an older store) is never busy for it", async () => {
    stubFetch([{ http: 200, body: IDLE }]);
    const { result } = renderHook(() => useRefresh({ enabled: true, onDone: () => {} }));
    await waitFor(() => expect(result.current.status).not.toBeNull());
    expect(result.current.ingestBusy).toBe(false);
  });
});

describe("useRefresh: asking for an ingest without a fetch", () => {
  it("posts {mode: ingest} and says how the store answered: taken, refused with its reason, or no service", async () => {
    const bodies: unknown[] = [];
    const answer = (http: number, body: unknown) => vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") bodies.push(JSON.parse(String(init.body)));
      return new Response(JSON.stringify(body), { status: http });
    });
    const { result } = renderHook(() => useRefresh({ enabled: false, onDone: () => {} }));
    answer(202, { accepted: true, mode: "ingest" });
    expect(await result.current.requestIngest()).toEqual({ kind: "accepted" });
    expect(bodies).toEqual([{ mode: "ingest" }]);
    answer(429, { accepted: false, reason: "ingest_cap", retry_after_s: 60 });
    expect(await result.current.requestIngest()).toEqual({ kind: "refused", reason: "ingest_cap" });
    answer(403, { error: "cross-origin refresh refused" });
    expect(await result.current.requestIngest()).toEqual({ kind: "refused", reason: "cross-origin refresh refused" });
    answer(503, { error: "refresh not configured" });
    expect(await result.current.requestIngest()).toEqual({ kind: "no-service" });
    answer(502, {});
    expect(await result.current.requestIngest(), "a gateway's 502: the store never had it").toEqual({ kind: "unreached" });
    vi.stubGlobal("fetch", async () => { throw new TypeError("network down"); });
    expect(await result.current.requestIngest(), "no answer: nothing was asked").toEqual({ kind: "unreached" });
    expect(result.current.reads, "asking reads nothing: the asker follows").toBe(0);
  });
  it("asks for the AI step, or for its answers to be forgotten, in the mode the store names, answered the same way", async () => {
    const bodies: unknown[] = [];
    const answer = (http: number, body: unknown) => {
      vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
        bodies.push(JSON.parse(String(init?.body)));
        return new Response(JSON.stringify(body), { status: http });
      });
    };
    const { result } = renderHook(() => useRefresh({ enabled: false, onDone: () => {} }));
    answer(202, { accepted: true, mode: "ai" });
    expect(await result.current.requestIngest("ai")).toEqual({ kind: "accepted" });
    answer(429, { accepted: false, reason: "ingest_cap", retry_after_s: 60 });
    expect(await result.current.requestIngest("ai_reset")).toEqual({ kind: "refused", reason: "ingest_cap" });
    expect(bodies).toEqual([{ mode: "ai" }, { mode: "ai_reset" }]);
  });
});

describe("useRefresh: reads in the order they were sent, and what a failed one says", () => {
  it("numbers each read as it is sent; an answer to an older read that comes after a newer one is dropped", async () => {
    const answers: Array<(body: RefreshStatus) => void> = [];
    vi.stubGlobal("fetch", () => new Promise<Response>((resolve) => {
      answers.push((body) => resolve(new Response(JSON.stringify(body), { status: 200 })));
    }));
    const { result } = renderHook(() => useRefresh({ enabled: false, onDone: () => {} }));
    act(() => { result.current.watch(false); result.current.watch(false); });
    expect(result.current.sent()).toBe(2);
    await act(async () => { answers[1](status({ used_today: 2 })); });
    await waitFor(() => expect(result.current.reads).toBe(2));
    await act(async () => { answers[0](status({ used_today: 1 })); });
    expect(result.current.reads, "the older answer moves nothing").toBe(2);
    expect(result.current.status?.used_today, "the newest read's status stands").toBe(2);
  });
  it("tells a store with no refresh service (its 503) from one that did not answer", async () => {
    stubFetch([{ http: 503, body: { error: "refresh not configured" } }]);
    const { result } = renderHook(() => useRefresh({ enabled: false, onDone: () => {} }));
    act(() => result.current.watch(false));
    await waitFor(() => expect(result.current.reads).toBe(1));
    expect([result.current.unreachable, result.current.noService]).toEqual([true, true]);
    stubFetch([{ http: 502, body: {} }]);
    act(() => result.current.watch(false));
    await waitFor(() => expect(result.current.reads).toBe(2));
    expect([result.current.unreachable, result.current.noService]).toEqual([true, false]);
  });
  it("no 401 leaves the page, whether a background read's, a read on opening the readout or a press's: it is signed out", async () => {
    const replace = vi.fn();
    vi.stubGlobal("location", { ...window.location, replace });
    stubFetch([{ http: 401, body: {} }]);
    const { result, rerender } = renderHook(({ enabled }) => useRefresh({ enabled, onDone: () => {} }),
      { initialProps: { enabled: false } });
    act(() => result.current.watch(false));
    await waitFor(() => expect(result.current.reads).toBe(1));
    expect([result.current.unreachable, result.current.signedOut]).toEqual([true, true]);
    rerender({ enabled: true });                                       // the person opens the readout
    await waitFor(() => expect(result.current.reads).toBe(2));
    expect(await result.current.requestIngest()).toEqual({ kind: "signed-out" });
    await act(async () => { await result.current.trigger(); });
    expect(replace, "an unsaved edit elsewhere on the page is never lost to a redirect").not.toHaveBeenCalled();
    expect(result.current.signedOut).toBe(true);
  });
  it("a 401 signs the whole page out (its one banner, the saves held), and a read that is answered signs it back in", async () => {
    stubFetch([{ http: 401, body: {} }]);
    const { result } = renderHook(() => useRefresh({ enabled: false, onDone: () => {} }));
    act(() => result.current.watch(false));
    await waitFor(() => expect(result.current.reads).toBe(1));
    expect(sessionState()).toBe("out");
    stubFetch([{ http: 200, body: IDLE }]);
    act(() => result.current.watch(false));
    await waitFor(() => expect(result.current.reads).toBe(2));
    expect(sessionState()).toBe("in");
    expect(result.current.signedOut).toBe(false);
  });
  it("a run in flight whose store stops answering is tried again once a minute, not every three seconds", async () => {
    vi.useFakeTimers();
    let fail = false;
    const calls: string[] = [];
    vi.stubGlobal("fetch", async () => {
      calls.push("GET");
      return fail ? new Response("bad gateway", { status: 502 })
        : new Response(JSON.stringify(status({ state: "running", trigger: "schedule" })), { status: 200 });
    });
    renderHook(() => useRefresh({ enabled: true, onDone: () => {} }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    fail = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(calls.length, "the read, then the poll that fails").toBe(2);
    // a second at a time: each answer is read before the next timer is due
    for (let s = 0; s < 5 * 60; s += 1) await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(calls.length, "five tries in five minutes").toBe(7);
  });
  it("an answer to a read sent before a look never uses the look up: the follow starts on the look's own answer", async () => {
    vi.useFakeTimers();
    const answers: Array<(body: RefreshStatus) => void> = [];
    const calls: string[] = [];
    vi.stubGlobal("fetch", () => {
      calls.push("GET");
      return new Promise<Response>((resolve) => { answers.push((body) => resolve(new Response(JSON.stringify(body), { status: 200 }))); });
    });
    const { result } = renderHook(() => useRefresh({ enabled: false, onDone: () => {} }));
    act(() => result.current.watch(false));                            // an earlier read, slow
    act(() => result.current.watch(true, "save"));                     // the save's look
    const wanted = status({ ingest: { wanted: true, last_started_at: null, last_finished_at: null, last_rc: null } });
    await act(async () => { answers[0](IDLE); });                      // the earlier read: nothing under way yet
    await act(async () => { answers[1](wanted); });                    // the look's own read: the ingest is wanted
    expect(result.current.ingestCause, "the fast follow starts").toBe("save");
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(calls.length, "and it polls").toBe(3);
  });
  it("says who asked for the ingest it follows: a save, a rebuild, or nobody this tab knows of", async () => {
    stubFetch([{ http: 200, body: status({ ingest: { wanted: true, last_started_at: null, last_finished_at: null, last_rc: null } }) }]);
    const { result } = renderHook(() => useRefresh({ enabled: false, onDone: () => {} }));
    expect(result.current.ingestCause).toBeNull();
    act(() => result.current.watch(true, "save"));
    await waitFor(() => expect(result.current.ingestCause).toBe("save"));
    const seen = renderHook(() => useRefresh({ enabled: true, onDone: () => {} }));
    await waitFor(() => expect(seen.result.current.ingestCause, "a view found it under way").toBe("seen"));
  });
});

describe("useRefresh: an ingest newer than the exports on screen (another device's, or one nobody followed)", () => {
  const BUILT = "2026-07-02T05:00:00-05:00";                          // 10:00Z
  const ended = (start: string, rc = 0) => status({ ingest: { wanted: false, last_started_at: start,
    last_finished_at: start.replace(":00Z", ":40Z"), last_rc: rc } });
  it("reloads them once, quietly, the first read included; an older or failed one, or no builtAt, reloads nothing", async () => {
    const onDone = vi.fn();
    stubFetch([{ http: 200, body: ended("2026-07-02T10:05:00Z") }]);
    const { result } = renderHook(() => useRefresh({ enabled: false, onDone, builtAt: BUILT }));
    act(() => result.current.watch(false));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(result.current.outcome, "no outcome line: it is no run this tab watched").toBeNull();
    act(() => result.current.watch(false));
    await waitFor(() => expect(result.current.reads).toBe(2));
    expect(onDone, "the same end seen again").toHaveBeenCalledTimes(1);
  });
  it("an ingest that built what is on screen, one that failed, or a hook told no builtAt: nothing", async () => {
    for (const [body, builtAt] of [[ended("2026-07-02T09:59:00Z"), BUILT], [ended("2026-07-02T10:05:00Z", 2), BUILT],
      [ended("2026-07-02T10:05:00Z"), undefined]] as const) {
      const onDone = vi.fn();
      stubFetch([{ http: 200, body }]);
      const { result, unmount } = renderHook(() => useRefresh({ enabled: false, onDone, builtAt }));
      act(() => result.current.watch(false));
      await waitFor(() => expect(result.current.reads).toBe(1));
      expect(onDone).not.toHaveBeenCalled();
      unmount();
    }
    expect(newerIngest(undefined, BUILT)).toBe(false);
  });
  it("a look that follows nothing never cancels a look that asked to follow", async () => {
    vi.useFakeTimers();
    const calls = stubFetch([{ http: 200, body: status({ ingest: { wanted: true, last_started_at: null, last_finished_at: null, last_rc: null } }) }]);
    const { result } = renderHook(() => useRefresh({ enabled: false, onDone: () => {} }));
    act(() => { result.current.watch(); result.current.watch(false); });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.ingestBusy, "the first look's follow stands").toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(calls.length, "and it is polled").toBe(3);
  });
});

describe("ingestBusy, ingestRunning, ingestEnded", () => {
  const i = (over: Partial<NonNullable<RefreshStatus["ingest"]>>) =>
    ({ wanted: false, last_started_at: null, last_finished_at: null, last_rc: null, ...over });
  it("reads the store's ingest block by its rule", () => {
    expect(ingestBusy(undefined)).toBe(false);
    expect(ingestBusy(i({}))).toBe(false);
    expect(ingestEnded(i({})), "nothing ever ran").toBe(false);
    expect(ingestBusy(i({ wanted: true }))).toBe(true);
    const started = "2026-07-02T10:00:00Z";
    expect(ingestRunning(i({ last_started_at: started }))).toBe(true);
    expect(ingestRunning(i({ last_started_at: started, last_finished_at: "2026-07-01T09:00:00Z" })), "an older end").toBe(true);
    const ended = i({ last_started_at: started, last_finished_at: started, last_rc: 1 });
    expect(ingestRunning(ended)).toBe(false);
    expect(ingestEnded(ended), "an end at its start counts").toBe(true);
    expect(ingestEnded({ ...ended, wanted: true }), "wanted again (interrupted, or a newer save)").toBe(false);
  });
});

describe("useRefresh availability", () => {
  it("relays a 503 reason instead of calling the store broken", async () => {
    stubFetch([{ http: 503, body: { error: "refresh not configured" } }]);
    const { result } = renderHook(() => useRefresh({ enabled: true, onDone: () => {} }));
    await waitFor(() => expect(result.current.unreachable).toBe(true));
    expect(result.current.unavailableNote).toBe("refresh not configured");
  });

  it("has no note when the request simply failed", async () => {
    vi.stubGlobal("fetch", async () => { throw new TypeError("network down"); });
    const { result } = renderHook(() => useRefresh({ enabled: true, onDone: () => {} }));
    await waitFor(() => expect(result.current.unreachable).toBe(true));
    expect(result.current.unavailableNote).toBeNull();
  });

  it("clears both once the endpoint answers again", async () => {
    stubFetch([{ http: 503, body: { error: "refresh not configured" } },
               { http: 200, body: IDLE }]);
    const { result } = renderHook(() => useRefresh({ enabled: true, onDone: () => {} }));
    await waitFor(() => expect(result.current.unreachable).toBe(true));
    await act(async () => { await result.current.trigger(); });
    await waitFor(() => expect(result.current.unreachable).toBe(false));
    expect(result.current.unavailableNote).toBeNull();
  });
});

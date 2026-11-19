// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { IngestStatus } from "../data/useRefresh";
import { type Follow, FOLLOW_ENDED, FOLLOW_MS, followStep, IDLE_FOLLOW, ingestFailure } from "./ingestFollow";

describe("an ingest's exit code in words", () => {
  it("timed out, stopped, killed by a signal, or failed with its code", () => {
    expect(ingestFailure(-1)).toBe("timed out");
    expect(ingestFailure(-2)).toBe("stopped before it finished");
    expect(ingestFailure(143)).toBe("was stopped");
    expect(ingestFailure(137)).toBe("was stopped (code 137)");
    expect(ingestFailure(2)).toBe("failed (code 2)");
  });
});

describe("followStep: following an ingest this tab asked for", () => {
  const ing = (over: Partial<IngestStatus>): IngestStatus =>
    ({ wanted: false, last_started_at: "2026-07-16T09:00:00Z", last_finished_at: "2026-07-16T09:00:30Z", last_rc: 0, ...over });
  const WANTED = ing({ wanted: true });
  const RUNNING = ing({ last_started_at: "2026-07-16T10:02:00Z", last_finished_at: null, last_rc: null });
  const ENDED = (rc: number) => ing({ last_started_at: "2026-07-16T10:02:00Z", last_finished_at: "2026-07-16T10:02:40Z", last_rc: rc });
  const G = "2026-07-16T06:00:00-04:00";
  const run = (events: Parameters<typeof followStep>[1][], from: Follow = IDLE_FOLLOW) => events.reduce(followStep, from);
  const landed: Parameters<typeof followStep>[1][] = [{ type: "save", removed: [] }, { type: "landed", at: 1000, reads: 4 }];
  /** an ask the store answered by touching the marker (a 202 to POST {"mode": "ingest"}) */
  const marked: Parameters<typeof followStep>[1][] = [{ type: "save", removed: [] }, { type: "landed", at: 1000, reads: 4, marked: true }];
  /** the answer to read number `reads`: a status, a read that failed (no answer, a 502), or the store's 503 */
  const read = (reads: number, ingest: IngestStatus | undefined, how: "ok" | "failed" | "no-service" = "ok", generatedAt = G) =>
    ({ type: "status" as const, reads, ingest, failed: how === "failed", noService: how === "no-service", generatedAt });

  it("saving until the store answers; refused shows the refusal", () => {
    expect(run([{ type: "save", removed: ["x"] }])).toEqual({ ...IDLE_FOLLOW, phase: "saving", removed: ["x"], bound: false });
    expect(run([{ type: "save", removed: [] }, { type: "refused" }]).phase).toBe("refused");
    expect(run(landed).phase).toBe("asking");
  });
  it("wanted, then running, then ended with 0: reloading until the exports' ingest instant moves, then done", () => {
    const f = run([...landed, read(5, WANTED), read(6, RUNNING), read(7, ENDED(0))]);
    expect(f.phase).toBe("reloading");
    expect(followStep(f, { type: "exports", generatedAt: G }).phase, "not reloaded yet").toBe("reloading");
    expect(followStep(f, { type: "exports", generatedAt: "2026-07-16T06:03:00-04:00" }).phase).toBe("done");
  });
  it("an ingest that failed says its code; an interrupted one (143, -2) stays wanted and is followed on", () => {
    expect(run([...landed, read(5, WANTED), read(6, ENDED(2))])).toEqual(expect.objectContaining({ phase: "failed", rc: 2 }));
    expect(run([...marked, read(5, WANTED), read(6, ENDED(-1))])).toEqual(expect.objectContaining({ phase: "failed", rc: -1 }));
    for (const rc of [143, -2]) {
      const interrupted = ing({ wanted: true, last_started_at: "2026-07-16T10:02:00Z", last_finished_at: "2026-07-16T10:02:05Z", last_rc: rc });
      expect(run([...landed, read(5, WANTED), read(6, interrupted)]).phase, `rc ${rc}`).toBe("waiting");
      expect(run([...marked, read(5, WANTED), read(6, interrupted), read(7, ENDED(0))]).phase, `rc ${rc}, then run again`).toBe("reloading");
    }
  });
  it("a read from before the landing says nothing about this ask", () => {
    expect(run([...landed, read(4, ENDED(0))]).phase).toBe("asking");
    expect(run([...marked, read(4, ENDED(0))]).phase).toBe("asking");
  });
  it("nothing wanted at the first read after a save (its marker could not be written): later", () => {
    expect(run([...landed, read(5, ENDED(0))]).phase).toBe("later");
  });
  it("after the store said it touched the marker, an ingest seen ended at the first read started after the ask: it answers it", () => {
    expect(run([...marked, read(5, ENDED(0))]).phase).toBe("reloading");
    expect(run([...marked, read(5, ENDED(2))])).toEqual(expect.objectContaining({ phase: "failed", rc: 2 }));
    expect(run([...marked, read(5, RUNNING)]).phase, "one already running after the touch").toBe("waiting");
  });
  it("the store's answer that there is no refresh service (its 503) ends it at once; a store with no ingest block: later", () => {
    expect(run([...landed, read(5, undefined, "no-service")]).phase).toBe("no-service");
    expect(run([...marked, read(5, WANTED), read(6, undefined, "no-service")]).phase).toBe("no-service");
    expect(run([...landed, read(5, undefined)]).phase).toBe("later");
  });
  it("a read that fails says nothing: the follow goes on, and completes when a later read answers", () => {
    const afterFailure = run([...marked, read(5, WANTED), read(6, undefined, "failed")]);
    expect(afterFailure.phase, "a 502 while the store restarts").toBe("waiting");
    expect(run([...marked, read(5, undefined, "failed")]).phase, "even the first read after the ask").toBe("asking");
    const completed = run([...marked, read(5, WANTED), read(6, undefined, "failed"), read(7, RUNNING), read(8, ENDED(0))]);
    expect(completed.phase).toBe("reloading");
    expect(followStep(completed, { type: "exports", generatedAt: "2026-07-16T06:03:00-04:00" }).phase).toBe("done");
  });
  it("it says whether any read after the landing was answered: a follow the store never answered cannot know the ingest's fate", () => {
    const tick = { type: "tick" as const, at: 1000 + FOLLOW_MS, reads: 5 };
    const unanswered = followStep(followStep(run([...marked, read(5, undefined, "failed")]), tick), read(6, undefined, "failed"));
    expect([unanswered.phase, unanswered.answered, unanswered.started]).toEqual(["later", false, false]);
    const answered = followStep(followStep(run([...marked, read(5, WANTED)]), tick), read(6, undefined, "failed"));
    expect([answered.phase, answered.answered]).toEqual(["later", true]);
    expect(run([...marked, read(4, WANTED)]).answered, "a read from before the landing is no answer to it").toBe(false);
  });
  it("it says whether the ingest was seen to start: running with nothing more wanted", () => {
    expect(run([...marked, read(5, WANTED)]).started, "wanted only").toBe(false);
    expect(run([...marked, read(5, WANTED), read(6, RUNNING)]).started).toBe(true);
    // a run still going from before the ask, with the ask still wanted, is not this ask's start
    expect(run([...marked, read(5, { ...RUNNING, wanted: true })]).started).toBe(false);
    // ended at the bound without ever being seen to start: later, not started
    const never = followStep(followStep(run([...marked, read(5, WANTED)]), { type: "tick", at: 1000 + FOLLOW_MS, reads: 5 }),
      read(6, WANTED));
    expect([never.phase, never.started]).toEqual(["later", false]);
    const unfinished = followStep(followStep(run([...marked, read(5, RUNNING)]), { type: "tick", at: 1000 + FOLLOW_MS, reads: 5 }),
      read(6, RUNNING));
    expect([unfinished.phase, unfinished.started]).toEqual(["later", true]);
  });
  it("at its bound the follow looks once more: still under way (or no answer) ends it, an end seen then is followed", () => {
    const waiting = run([...landed, read(5, WANTED)]);
    expect(followStep(waiting, { type: "tick", at: 1000 + FOLLOW_MS - 1, reads: 5 })).toBe(waiting);
    const bound = followStep(waiting, { type: "tick", at: 1000 + FOLLOW_MS, reads: 5 });
    expect([bound.phase, bound.bound]).toEqual(["waiting", true]);
    expect(followStep(bound, read(5, WANTED)), "a read from before the look says nothing").toBe(bound);
    expect(followStep(bound, read(6, WANTED)).phase).toBe("later");
    expect(followStep(bound, read(6, undefined, "failed")).phase, "no answer to the look").toBe("later");
    // a tab that slept past the bound wakes after the ingest ended: the look sees it end and the exports reload
    expect(followStep(bound, read(6, ENDED(0))).phase).toBe("reloading");
    expect(followStep(bound, read(6, ENDED(2))).phase).toBe("failed");
    const reloading = run([...landed, read(5, WANTED), read(6, ENDED(0))]);
    expect(followStep(reloading, { type: "tick", at: 1000 + FOLLOW_MS, reads: 6 }).phase, "never reloaded: later").toBe("later");
    const failed = run([...landed, read(5, WANTED), read(6, ENDED(1))]);
    expect(followStep(failed, { type: "tick", at: 1e12, reads: 9 })).toBe(failed);
    expect(followStep(failed, read(9, WANTED))).toBe(failed);
    expect(run([{ type: "save", removed: [] }, { type: "tick", at: 1e12, reads: 0 }]).phase, "an ask not landed has no bound").toBe("saving");
  });
  it("the grace after the bound ends any phase still open as later, and nothing before the bound or after an end", () => {
    const waiting = run([...landed, read(5, WANTED)]);
    expect(followStep(waiting, { type: "grace" }), "before the bound the grace says nothing").toBe(waiting);
    const bound = followStep(waiting, { type: "tick", at: 1000 + FOLLOW_MS, reads: 5 });
    expect(followStep(bound, { type: "grace" }).phase, "the look never answered").toBe("later");
    const reloadingPastBound = followStep(bound, read(6, ENDED(0)));
    expect(followStep(reloadingPastBound, { type: "grace" }).phase, "the reload never moved the exports").toBe("later");
    const done = followStep(reloadingPastBound, { type: "exports", generatedAt: "2026-07-16T06:03:00-04:00" });
    expect(done.phase).toBe("done");
    expect(followStep(done, { type: "grace" }), "an end stays as it is").toBe(done);
  });
  it("the ended phases are the ones a follow stops in", () => {
    expect([...FOLLOW_ENDED].sort()).toEqual(["done", "failed", "idle", "later", "no-service", "refused"]);
  });
});

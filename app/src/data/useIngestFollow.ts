// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { type Dispatch, useEffect, useReducer } from "react";
import { clock } from "../lib/clock";
import { type Follow, FOLLOW_GRACE_MS, FOLLOW_MS, type FollowEvent, followStep, IDLE_FOLLOW } from "../lib/ingestFollow";
import type { IngestCause, RefreshApi } from "./useRefresh";

/** The shared follow (lib/ingestFollow.ts followStep) run against the session's refresh: every completed status read
 *  and every exports reload goes to the reducer, and at the bound the follow looks once more (`watch(false)`) and then
 *  has its grace. The asker dispatches `save` as it sends its request, then `landed` (and calls `watch()`, so the
 *  status is read and the ingest followed) or `refused`; `landed()` does both, noting the reads sent by then so that
 *  only the answers to later ones count. `cause` says who asks (the manual assets' save, "Rebuild from files"), which the
 *  shared refresh reports while it follows the ingest. A follow arms nothing while it is not under way. */
export function useIngestFollow(refresh: RefreshApi, generatedAt: string, cause: IngestCause): {
  follow: Follow; dispatch: Dispatch<FollowEvent>; landed: (marked?: boolean) => void;
} {
  const [follow, dispatch] = useReducer(followStep, IDLE_FOLLOW);
  const { watch, reads, sent, status, unreachable, noService } = refresh;
  useEffect(() => {
    dispatch({ type: "status", reads, ingest: status ? status.ingest : null, failed: unreachable && !noService, noService,
               generatedAt });
  }, [reads, status, unreachable, noService, generatedAt]);
  useEffect(() => { dispatch({ type: "exports", generatedAt }); }, [generatedAt]);
  // at the bound, one more look (a tab that slept past it may wake after the ingest ended), then the follow ends: the
  // look has a short grace, after which a phase still open (no answer, or a reload that never brings new exports) ends
  // as `later`; the grace's timer goes when the follow ends by itself or the asker unmounts
  const following = follow.phase === "asking" || follow.phase === "waiting" || follow.phase === "reloading";
  useEffect(() => {
    if (!following || follow.savedAt === null || follow.bound) return;
    const id = setTimeout(() => {
      dispatch({ type: "tick", at: clock.now().getTime(), reads: sent() });
      watch(false);
    }, Math.max(0, follow.savedAt + FOLLOW_MS - clock.now().getTime()));
    return () => clearTimeout(id);
  }, [following, follow.savedAt, follow.bound, watch, sent]);
  useEffect(() => {
    if (!following || !follow.bound) return;
    const id = setTimeout(() => dispatch({ type: "grace" }), FOLLOW_GRACE_MS);
    return () => clearTimeout(id);
  }, [following, follow.bound]);
  const landed = (marked = false) => {
    dispatch({ type: "landed", at: clock.now().getTime(), reads: sent(), marked });
    watch(true, cause);
  };
  return { follow, dispatch, landed };
}

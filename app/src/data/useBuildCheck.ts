// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useCallback, useEffect, useRef, useState } from "react";
import { BUILD_ID, VERSION_URL } from "../lib/buildId";
import { IS_DEMO } from "../lib/demo";

/** The build id the server is serving now (`version.json`, next to index.html), or null when it cannot be told: a dev server or
 *  an older image has no such file (404), the passcode gate may answer 401 (the data fetches already send the page to the login),
 *  the network may be down, and a host that falls back to index.html answers 200 with HTML. None of those is "an update". */
export async function fetchDeployedBuild(): Promise<string | null> {
  // the demo build is a static site with no deploys to tell of, and asks no server
  if (IS_DEMO || VERSION_URL === null) return null;
  try {
    // no-store: the answer must be the server's, never a cached copy of an older deploy's
    const res = await fetch(VERSION_URL, { cache: "no-store" });
    if (!res.ok) return null;
    const body: unknown = await res.json();
    const build = (body as { build?: unknown } | null)?.build;
    return typeof build === "string" && build !== "" ? build : null;
  } catch {
    return null;
  }
}

/** Has the dashboard been redeployed since this bundle loaded? An app-only deploy bumps no schema, so an open tab would keep
 *  running the old code (and any bug fixed in it) until someone reloaded it by hand. This asks the server for its build id when
 *  the tab comes back to the foreground and after every data reload (`reloads`: how many have finished since the first load),
 *  and answers true once the id differs from the one this bundle was built with. It never reloads anything - an edit may be in
 *  flight - the provider shows the "reload" banner. Silent under `vite dev` and vitest (BUILD_ID "dev"), on a missing
 *  version.json, on any failure, and in the demo build, which has no server to ask; once true it stays true. `current`
 *  is the seam for tests. */
export function useBuildCheck(reloads: number, current: string = BUILD_ID): boolean {
  const [stale, setStale] = useState(false);
  const inFlight = useRef(false);
  const latched = useRef(false);
  const check = useCallback(async () => {
    if (IS_DEMO || current === "dev" || inFlight.current || latched.current) return;
    inFlight.current = true;
    try {
      const deployed = await fetchDeployedBuild();
      if (deployed !== null && deployed !== current) { latched.current = true; setStale(true); }
    } finally {
      inFlight.current = false;
    }
  }, [current]);

  useEffect(() => {
    const onVisible = () => { if (document.visibilityState === "visible") void check(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [check]);

  // after each data reload (a refresh finished, a tab came back): the first load is this bundle's own page load
  useEffect(() => { if (reloads > 0) void check(); }, [reloads, check]);

  return stale;
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { appDataFactory, snapFactory } from "../lib/__fixtures__";
import { emptyPrefs, normalizePrefs, type PrefsDoc, withBudget } from "../lib/prefs";

// The demo build (lib/demo.ts): every module below reads the flag as the demo bundle does.
vi.mock("../lib/demo", () => ({ IS_DEMO: true, DEMO_BANNER_TEXT: "Demo: an invented household. Changes stay in this browser.",
  storageKey: (key: string) => `ff-demo:${key}` }));

import { clock } from "../lib/clock";
import { DataProvider, useData } from "./DataContext";
import { hasUnsentEdits, leave, LOGIN_PATH, resetSession, sessionState } from "./session";
import { signOut } from "./signOut";
import { fetchDeployedBuild, useBuildCheck } from "./useBuildCheck";
import { PREFS_SYNC } from "./usePrefsStore";
import { storeUrl, useSyncedDoc } from "./useSyncedDoc";

const base = appDataFactory(snapFactory())();
const BUILT_AT = "2026-06-10T18:00:00-04:00";
/** every request the page made, by its address */
let asked: string[] = [];

/** the static site: the four exports the page needs, nothing else (no store, no version.json) */
function serveExports(status = 200) {
  const bodies: Record<string, unknown> = {
    "manifest.json": { ...base.manifest, generated_at: BUILT_AT },
    "transactions.json": { count: 0, transactions: [] },
    "assets_timeseries.json": base.assets,
    "assumptions.json": base.assumptions,
  };
  vi.stubGlobal("fetch", async (url: string) => {
    asked.push(String(url));
    const name = String(url).replace(/^\.\//, "");
    if (status !== 200) return new Response('{"error": "no"}', { status });
    return name in bodies ? new Response(JSON.stringify(bodies[name]), { status: 200 }) : new Response("nope", { status: 404 });
  });
}

function Probe() {
  const { data } = useData();
  return <i data-testid="probe">{data.manifest.generated_at}</i>;
}

beforeEach(() => { localStorage.clear(); resetSession(); asked = []; clock.freeze(null); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); clock.freeze(null); });

describe("the demo build's stores keep the family's documents in this browser", () => {
  it("loads this browser's copy at once and saves each edit there, and never asks a server", async () => {
    const fetchSpy = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);
    const stored = withBudget(emptyPrefs(), "Groceries", 600, "2026-06-01T00:00:00Z");
    localStorage.setItem("demo-prefs", JSON.stringify(stored));
    const doc = renderHook(() => useSyncedDoc<PrefsDoc>({ ...PREFS_SYNC, localKey: "demo-prefs", debounceMs: 5 })).result;
    expect(doc.current.loading, "nothing to wait for").toBe(false);
    expect(doc.current.doc.budgets).toEqual(stored.budgets);
    act(() => doc.current.commit((d) => withBudget(d, "Dining", 250, "2026-06-02T00:00:00Z")));
    act(() => doc.current.commit((d) => withBudget(d, "Travel", 90, "2026-06-03T00:00:00Z")));
    expect(normalizePrefs(JSON.parse(localStorage.getItem("demo-prefs") ?? "null")).budgets,
      "each edit is in this browser's copy at once, the functional updates chained")
      .toEqual({ Groceries: 600, Dining: 250, Travel: 90 });
    expect(doc.current.accepted, "every save reads as taken").toBe(2);
    expect(doc.current.acked, "what is saved is the document").toBe(doc.current.doc);
    expect([doc.current.localOnly, doc.current.refused, doc.current.saveState]).toEqual([false, null, "saved"]);
    expect(hasUnsentEdits(), "nothing waits to be sent, so nothing asks before a sign-out").toBe(false);
    await new Promise((r) => setTimeout(r, 50));                   // past any debounce a store would have
    expect(fetchSpy, "a browser holding a copy reads nothing at all").not.toHaveBeenCalled();
    expect(storeUrl("prefs"), "the demo bundle carries no store path").toBe("./family/prefs.json");
  });

  it("with no copy in this browser, starts from the document the site was built with, read once and never written back", async () => {
    const built = withBudget(emptyPrefs(), "Groceries", 700, "2026-06-01T00:00:00Z");
    const calls: [string, RequestInit | undefined][] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      calls.push([String(url), init]);
      return new Response(JSON.stringify(built), { status: 200 });
    });
    const doc = renderHook(() => useSyncedDoc<PrefsDoc>({ ...PREFS_SYNC, url: storeUrl("prefs"), localKey: "fresh" })).result;
    expect(doc.current.loading, "it waits for that document").toBe(true);
    await waitFor(() => expect(doc.current.loading).toBe(false));
    expect(doc.current.doc.budgets).toEqual({ Groceries: 700 });
    act(() => doc.current.commit((d) => withBudget(d, "Dining", 250, "2026-06-02T00:00:00Z")));
    await new Promise((r) => setTimeout(r, 50));
    expect(calls.map(([u, init]) => [u, init?.method ?? "GET"]), "one read of the file beside the page, nothing sent")
      .toEqual([["./family/prefs.json", "GET"]]);
    expect(normalizePrefs(JSON.parse(localStorage.getItem("fresh") ?? "null")).budgets).toEqual({ Groceries: 700, Dining: 250 });
  });

  it("a site built with no such document, or an answer that cannot be read, starts empty", async () => {
    for (const answer of [() => new Response("nope", { status: 404 }), () => { throw new TypeError("offline"); }]) {
      vi.stubGlobal("fetch", async () => answer());
      const doc = renderHook(() => useSyncedDoc<PrefsDoc>({ ...PREFS_SYNC, url: storeUrl("prefs"), localKey: "none" })).result;
      await waitFor(() => expect(doc.current.loading).toBe(false));
      expect(doc.current.doc).toEqual(emptyPrefs());
      cleanup();
    }
  });

  it("a copy this browser cannot read counts as none, and a storage that refuses a write keeps the edit for the page", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 404 })));
    localStorage.setItem("demo-bad", "{not json");
    const doc = renderHook(() => useSyncedDoc<PrefsDoc>({ ...PREFS_SYNC, localKey: "demo-bad" })).result;
    await waitFor(() => expect(doc.current.loading).toBe(false));
    expect(doc.current.doc).toEqual(emptyPrefs());
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("QuotaExceededError"); });
    act(() => doc.current.commit((d) => withBudget(d, "Dining", 250, "2026-06-02T00:00:00Z")));
    expect(doc.current.doc.budgets).toEqual({ Dining: 250 });
    expect(doc.current.localOnly).toBe(false);
  });
});

describe("the demo build's page", () => {
  it("reads only its exports: no store, no sign-in, no build poll, and the clock stands at the exports' generation", async () => {
    serveExports();
    render(<DataProvider><Probe /></DataProvider>);
    await screen.findByTestId("probe");
    expect(clock.now().toISOString(), "the clock stands at manifest.generated_at").toBe(new Date(BUILT_AT).toISOString());
    expect(clock.today("UTC")).toBe("2026-06-10");
    await new Promise((r) => setTimeout(r, 30));
    expect(clock.now().toISOString(), "and stays there").toBe(new Date(BUILT_AT).toISOString());
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    await new Promise((r) => setTimeout(r, 30));
    expect(asked.length).toBeGreaterThan(0);
    expect(asked.filter((u) => !/^\.\/[a-z_]+\.json$/.test(u) && !/^\.\/family\/[a-z-]+\.json$/.test(u)),
      "every request is an export or a family document beside the page").toEqual([]);
    expect(asked).not.toContain("./version.json");
  });

  it("an export answered 401 is a failed read: the page stays and never leaves for a sign-in", async () => {
    const away = vi.spyOn(leave, "toSignIn");
    serveExports(401);
    render(<DataProvider><Probe /></DataProvider>);
    await screen.findByText("Couldn't load data");
    expect(away).not.toHaveBeenCalled();
    expect(sessionState()).toBe("in");
  });

  it("has no sign-in to leave for, no sign-out to post, and no build to compare", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const replace = vi.fn();
    vi.stubGlobal("location", { ...location, replace });
    leave.toSignIn();
    expect(replace).not.toHaveBeenCalled();
    expect(LOGIN_PATH, "no server path in the demo bundle").toBe("./");
    expect(await signOut()).toBe(false);
    expect(await fetchDeployedBuild()).toBeNull();
    const stale = renderHook(() => useBuildCheck(3, "build-A")).result;
    await new Promise((r) => setTimeout(r, 20));
    expect(stale.current).toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

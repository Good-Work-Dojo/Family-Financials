// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// The Second house scenarios through a stand-in for the family store that keeps each PUT whole (deploy/scenario_store.py
// keeps the scenarios document as it is sent). Each "device" is its own hook with its own local mirror, as two browsers.
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { addScenario, emptyStore, type ScenarioStore } from "../lib/scenarioStore";
import { SCENARIO_SYNC, useScenarioStore } from "./useScenarioStore";
import { useSyncedDoc } from "./useSyncedDoc";

type Json = Record<string, unknown>;
function scenarioStore(initial: ScenarioStore) {
  const s = { stored: initial as unknown as Json, puts: 0 };
  vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
    if (init?.method === "PUT" && init.body) { s.stored = JSON.parse(String(init.body)) as Json; s.puts += 1; }
    return new Response(JSON.stringify(s.stored), { status: 200 });
  });
  return s;
}
const names = (doc: unknown) => (doc as ScenarioStore).scenarios.map((x) => x.name);
const T = (min: number) => `2026-07-16T10:${String(min).padStart(2, "0")}:00.000Z`;

let visibility: DocumentVisibilityState = "visible";
beforeEach(() => {
  localStorage.clear();
  visibility = "visible";
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
function comeBack() {
  visibility = "hidden";
  document.dispatchEvent(new Event("visibilitychange"));
  visibility = "visible";
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("the scenarios document: a tab left open does not write over another device's scenarios", () => {
  it("B adds a scenario; A, loaded before it, comes back and then edits its own: the store keeps both", async () => {
    const one = addScenario(emptyStore(), "s1", T(0))!;
    const store = scenarioStore(one);
    const device = (localKey: string) =>
      renderHook(() => useSyncedDoc<ScenarioStore>({ ...SCENARIO_SYNC, localKey, debounceMs: 5 })).result;
    const a = device("dev-a");
    const b = device("dev-b");
    await waitFor(() => expect([a.current.loading, b.current.loading]).toEqual([false, false]));
    act(() => b.current.commit((cur) => addScenario(cur, "s2", T(1))!));
    await waitFor(() => expect(names(store.stored)).toEqual(["Scenario 1", "Scenario 2"]));
    expect(names(a.current.doc), "A has not heard of it yet").toEqual(["Scenario 1"]);

    comeBack();
    await waitFor(() => expect(names(a.current.doc)).toEqual(["Scenario 1", "Scenario 2"]));
    act(() => a.current.commit((cur) => ({ ...cur, updated_at: T(2),
      scenarios: cur.scenarios.map((x) => (x.id === "s1" ? { ...x, name: "Lake house", updated_at: T(2) } : x)) })));
    await waitFor(() => expect(names(store.stored)).toEqual(["Lake house", "Scenario 2"]));
  });
});

describe("the remembered active scenario", () => {
  it("a browser whose storage throws on read still opens the page, on the first scenario", async () => {
    scenarioStore(addScenario(emptyStore(), "s1", T(0))!);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
    const r = renderHook(() => useScenarioStore()).result;
    await waitFor(() => expect(r.current.loading).toBe(false));
    await waitFor(() => expect(r.current.activeId).toBe("s1"));
  });
  it("the active scenario deleted on another device: the first one left becomes active", async () => {
    const two = addScenario(addScenario(emptyStore(), "s1", T(0))!, "s2", T(0))!;
    const store = scenarioStore(two);
    const r = renderHook(() => useScenarioStore()).result;
    await waitFor(() => expect(r.current.activeId).toBe("s1"));
    store.stored = { ...two, updated_at: T(1), scenarios: two.scenarios.filter((x) => x.id !== "s1") } as unknown as Json;
    comeBack();
    await waitFor(() => expect(r.current.activeId).toBe("s2"));
    expect(r.current.active?.id).toBe("s2");
  });
});

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// The manual-assets store's load and save as a browser meets them: its mirror merged by id with the family store's copy
// (an entry only the mirror holds is sent back while it is recent, and dropped, never sent, once it is older than the
// store's memory of removals), and the copy the store acknowledged kept apart from a refused one.
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type LiveAsset, MANUAL_ASSETS_LOCAL_KEY, type ManualAssetsDoc, mergeManualAssets } from "../lib/manualAssets";
import { useManualAssetsStore } from "./useManualAssetsStore";

const NOW = Date.parse("2026-10-31T22:00:00.000Z");
const ago = (days: number) => new Date(NOW - days * 86_400_000).toISOString();
const asset = (id: string, name: string, updated_at: string): LiveAsset => ({ id, name, owner: "Joint", asset_class: "Car",
  liquid: false, values: [{ as_of: "2026-01-01", value: 1000 }], updated_at });
const doc = (...assets: LiveAsset[]): ManualAssetsDoc => ({ version: 1, updated_at: ago(1), assets });

/** the family store: GET its copy; PUT merged by id (or refused while `refuse` is set), echoed */
function store(initial: ManualAssetsDoc) {
  const s = { stored: initial, puts: [] as ManualAssetsDoc[], refuse: false };
  vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
    if ((init?.method ?? "GET") === "PUT") {
      const body = JSON.parse(String(init!.body)) as ManualAssetsDoc;
      s.puts.push(body);
      if (s.refuse) return new Response(JSON.stringify({ error: "at most 50 assets" }), { status: 400 });
      s.stored = mergeManualAssets(body, s.stored, NOW);
    }
    return new Response(JSON.stringify(s.stored), { status: 200 });
  });
  return s;
}
const mirror = (): ManualAssetsDoc => JSON.parse(localStorage.getItem(MANUAL_ASSETS_LOCAL_KEY) ?? "null");
const names = (d: ManualAssetsDoc | null) => d?.assets.map((a) => a.name) ?? [];

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  localStorage.clear();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("useManualAssetsStore: the load", () => {
  it("an entry only this browser holds, older than the store's memory of removals, is dropped and never sent back", async () => {
    const s = store(doc(asset("a-000000000001", "Home", ago(5))));
    localStorage.setItem(MANUAL_ASSETS_LOCAL_KEY, JSON.stringify(doc(asset("a-000000000001", "Home", ago(5)),
      asset("a-0000000000aa", "Sold boat", ago(91)))));
    const h = renderHook(() => useManualAssetsStore()).result;
    await waitFor(() => expect(h.current.loading).toBe(false));
    expect(names(h.current.doc)).toEqual(["Home"]);
    expect(s.puts, "nothing to send").toHaveLength(0);
    expect(names(mirror()), "the mirror keeps the merged copy").toEqual(["Home"]);
  });

  it("a recent one (an edit made offline ten days ago) is kept and sent", async () => {
    const s = store(doc(asset("a-000000000001", "Home", ago(5))));
    localStorage.setItem(MANUAL_ASSETS_LOCAL_KEY, JSON.stringify(doc(asset("a-000000000001", "Home", ago(5)),
      asset("a-0000000000bb", "Offline car", ago(10)))));
    const h = renderHook(() => useManualAssetsStore()).result;
    await waitFor(() => expect(s.puts).toHaveLength(1));
    expect(names(s.stored)).toEqual(["Home", "Offline car"]);
    await waitFor(() => expect(names(h.current.acked)).toEqual(["Home", "Offline car"]));
  });
});

describe("useManualAssetsStore: what the store acknowledged", () => {
  it("a refused save stays in this browser's copy and not in the acknowledged one", async () => {
    const s = store(doc(asset("a-000000000001", "Home", ago(5))));
    const h = renderHook(() => useManualAssetsStore()).result;
    await waitFor(() => expect(h.current.loading).toBe(false));
    expect(names(h.current.acked)).toEqual(["Home"]);
    s.refuse = true;
    act(() => h.current.save(doc(asset("a-000000000001", "Home", ago(5)), asset("a-0000000000cc", "Car", ago(0)))));
    await waitFor(() => expect(h.current.refused).toBe(400));
    expect(names(h.current.doc)).toEqual(["Home", "Car"]);
    expect(names(h.current.acked), "the store never took it").toEqual(["Home"]);
  });
});

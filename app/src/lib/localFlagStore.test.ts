// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { localFlagStore } from "./localFlagStore";

const KEY = "ff.test-flag";
const make = () => localFlagStore<boolean>(KEY, (raw) => raw === "1", (on) => (on ? "1" : null));
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });

/** what the browser fires in OTHER tabs when this origin's storage changes */
const otherTabWrote = (key: string | null) => window.dispatchEvent(new StorageEvent("storage", { key }));

describe("localFlagStore", () => {
  it("reads what is stored, writes through, and removes the key for the default", () => {
    const s = make();
    expect(s.get()).toBe(false);
    s.set(true);
    expect(localStorage.getItem(KEY)).toBe("1");
    expect(s.get()).toBe(true);
    s.set(false);
    expect(localStorage.getItem(KEY)).toBeNull();
  });
  it("sees a value written elsewhere (another tab, the test setup) on the next read - nothing is cached stale", () => {
    const s = make();
    localStorage.setItem(KEY, "1");
    expect(s.get()).toBe(true);
  });
  it("tells every subscriber of this page's writes, and stops after unsubscribe", () => {
    const s = make();
    const a = vi.fn(), b = vi.fn();
    const offA = s.subscribe(a);
    s.subscribe(b);
    s.set(true);
    expect([a.mock.calls.length, b.mock.calls.length]).toEqual([1, 1]);
    offA();
    s.set(false);
    expect([a.mock.calls.length, b.mock.calls.length]).toEqual([1, 2]);
  });
  it("another tab's write arrives as a storage event: a SECOND store on the key sees it and tells its subscribers", () => {
    const tab1 = make(), tab2 = make();
    const heard = vi.fn();
    tab2.subscribe(heard);
    tab1.set(true);                                  // tab 1 writes the shared storage...
    expect(heard).not.toHaveBeenCalled();            // ...tab 2 hears nothing until the browser says so
    otherTabWrote(KEY);
    expect(heard).toHaveBeenCalledTimes(1);
    expect(tab2.get()).toBe(true);
  });
  it("ignores another key's event, and a cleared storage (key null) counts as a change", () => {
    const s = make();
    const heard = vi.fn();
    s.subscribe(heard);
    otherTabWrote("something-else");
    expect(heard).not.toHaveBeenCalled();
    otherTabWrote(null);
    expect(heard).toHaveBeenCalledTimes(1);
  });
  it("stops listening for storage events once the last subscriber leaves", () => {
    const s = make();
    const heard = vi.fn();
    s.subscribe(heard)();
    otherTabWrote(KEY);
    expect(heard).not.toHaveBeenCalled();
  });
  it("a storage that refuses the write still holds the value for this page view, and the next good write takes over", () => {
    const s = make();
    const refuse = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
    expect(() => s.set(true)).not.toThrow();
    expect(s.get()).toBe(true);
    refuse.mockRestore();
    s.set(false);
    expect(s.get()).toBe(false);
    s.resetMemory();
    expect(s.get()).toBe(false);
  });
  it("a storage that throws on read reads as the default, and no storage at all works from memory", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
    expect(make().get()).toBe(false);
    vi.restoreAllMocks();
    vi.stubGlobal("localStorage", undefined);
    const s = make();
    s.set(true);
    expect(s.get()).toBe(true);
  });
  it("use() is the value as React state: it follows this page's writes and another tab's", () => {
    const s = make();
    const { result } = renderHook(() => s.use());
    expect(result.current).toBe(false);
    act(() => s.set(true));
    expect(result.current).toBe(true);
    act(() => { localStorage.removeItem(KEY); otherTabWrote(KEY); });
    expect(result.current).toBe(false);
  });
});

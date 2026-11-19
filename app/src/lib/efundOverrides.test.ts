// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  _resetEfundOverridesMemory, EFUND_OVERRIDES_KEY, useEfundOverrides, writeEfundOverrides,
} from "./efundOverrides";

/** the toggles a reader sees right now */
const readEfundOverrides = () => renderHook(() => useEfundOverrides()).result.current;

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  _resetEfundOverridesMemory();
});

describe("the stored toggles", () => {
  it("reads the stored toggles", () => {
    localStorage.setItem(EFUND_OVERRIDES_KEY, JSON.stringify({ "Dining / Restaurants": true }));
    expect(readEfundOverrides()).toEqual({ "Dining / Restaurants": true });
  });
  it("none stored, bad JSON, a non-object or non-boolean values read as no toggles", () => {
    expect(readEfundOverrides()).toEqual({});
    localStorage.setItem(EFUND_OVERRIDES_KEY, "{not json");
    expect(readEfundOverrides()).toEqual({});
    localStorage.setItem(EFUND_OVERRIDES_KEY, "[true]");
    expect(readEfundOverrides()).toEqual({});
    localStorage.setItem(EFUND_OVERRIDES_KEY, JSON.stringify({ a: true, b: "yes", c: 1 }));
    expect(readEfundOverrides()).toEqual({ a: true });
  });
  it("hands back a legacy truncated key as stored and never rewrites the document (lib/emergency overrideFor resolves it)", () => {
    const stored = JSON.stringify({ "Subscriptions / News": true });
    localStorage.setItem(EFUND_OVERRIDES_KEY, stored);
    expect(readEfundOverrides()).toEqual({ "Subscriptions / News": true });
    expect(localStorage.getItem(EFUND_OVERRIDES_KEY)).toBe(stored);
  });
  it("a storage that throws (private mode) reads as no toggles instead of crashing", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
    expect(readEfundOverrides()).toEqual({});
  });
});

describe("writeEfundOverrides / useEfundOverrides", () => {
  it("every reader re-renders with the new toggles - the gauge and the KPI stay in step", () => {
    const a = renderHook(() => useEfundOverrides());
    const b = renderHook(() => useEfundOverrides());
    act(() => writeEfundOverrides({ "Groceries / —": false }));
    expect(a.result.current).toEqual({ "Groceries / —": false });
    expect(b.result.current).toEqual({ "Groceries / —": false });
    expect(JSON.parse(localStorage.getItem(EFUND_OVERRIDES_KEY)!)).toEqual({ "Groceries / —": false });
    act(() => writeEfundOverrides(null));   // reset
    expect(a.result.current).toEqual({});
    expect(localStorage.getItem(EFUND_OVERRIDES_KEY)).toBeNull();
  });
  it("a toggle made in another tab arrives through the storage event", () => {
    const { result } = renderHook(() => useEfundOverrides());
    act(() => {
      localStorage.setItem(EFUND_OVERRIDES_KEY, JSON.stringify({ "Dining / Restaurants": true }));
      window.dispatchEvent(new StorageEvent("storage", { key: EFUND_OVERRIDES_KEY }));
    });
    expect(result.current).toEqual({ "Dining / Restaurants": true });
  });
  it("a write the storage refuses still applies for this page view", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
    const { result } = renderHook(() => useEfundOverrides());
    act(() => writeEfundOverrides({ x: true }));
    expect(result.current).toEqual({ x: true });
    expect(readEfundOverrides()).toEqual({ x: true });
  });
});

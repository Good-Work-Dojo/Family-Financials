// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SELECTION_CAP, useRowSelection } from "./useRowSelection";

describe("useRowSelection", () => {
  it("toggles, selects many, prunes to the live rows and clears", () => {
    const { result } = renderHook(() => useRowSelection());
    act(() => result.current.toggle("a"));
    act(() => result.current.toggle("b"));
    expect([...result.current.selected]).toEqual(["a", "b"]);
    act(() => result.current.toggle("a"));
    expect([...result.current.selected]).toEqual(["b"]);
    act(() => result.current.selectMany(["x", "y", "z"]));
    expect(result.current.selected.size).toBe(3);
    act(() => result.current.prune(new Set(["y"])));          // a filter change hid x and z
    expect([...result.current.selected]).toEqual(["y"]);
    act(() => result.current.clear());
    expect(result.current.selected.size).toBe(0);
  });

  it("caps the selection at SELECTION_CAP (the learned-rule evidence lives in a 500-answer store)", () => {
    const { result } = renderHook(() => useRowSelection());
    const ids = Array.from({ length: SELECTION_CAP + 5 }, (_, i) => `t${i}`);
    act(() => result.current.selectMany(ids));
    expect(result.current.selected.size).toBe(SELECTION_CAP);
    expect(result.current.atCap).toBe(true);
    expect(result.current.selected.has(`t${SELECTION_CAP - 1}`), "the first rows are kept").toBe(true);
    expect(result.current.selected.has(`t${SELECTION_CAP}`), "the rest are not").toBe(false);
    act(() => result.current.toggle("extra"));
    expect(result.current.selected.has("extra")).toBe(false);
    expect(result.current.selected.size).toBe(SELECTION_CAP);
  });

  it("a full selection is never a trap: a selected row still toggles off, and room reopens the cap", () => {
    const { result } = renderHook(() => useRowSelection());
    act(() => result.current.selectMany(Array.from({ length: SELECTION_CAP }, (_, i) => `t${i}`)));
    act(() => result.current.toggle("t7"));
    expect(result.current.selected.has("t7")).toBe(false);
    expect(result.current.atCap).toBe(false);
    act(() => result.current.toggle("extra"));
    expect(result.current.selected.has("extra")).toBe(true);
    expect(result.current.atCap).toBe(true);
  });

  it("prune keeps the same set when every selected row is still on screen, and the hook's callbacks are stable", () => {
    const { result, rerender } = renderHook(() => useRowSelection());
    act(() => result.current.selectMany(["a", "b"]));
    const before = result.current;
    act(() => result.current.prune(new Set(["a", "b", "c"])));
    expect(result.current.selected, "no change, no new Set (nothing to re-render)").toBe(before.selected);
    expect(result.current, "and the hook's own object is the same instance").toBe(before);
    rerender();
    expect(result.current).toBe(before);
    expect([result.current.toggle, result.current.selectMany, result.current.clear, result.current.prune])
      .toEqual([before.toggle, before.selectMany, before.clear, before.prune]);
  });
});

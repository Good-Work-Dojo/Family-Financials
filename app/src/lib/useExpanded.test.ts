// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useExpanded } from "./useExpanded";

describe("useExpanded", () => {
  it("single: at most one open, re-click closes, switching moves", () => {
    const { result } = renderHook(() => useExpanded("single"));
    act(() => result.current.toggle("a"));
    expect(result.current.isOpen("a")).toBe(true);
    act(() => result.current.toggle("b"));
    expect(result.current.isOpen("a")).toBe(false);
    expect(result.current.isOpen("b")).toBe(true);
    act(() => result.current.toggle("b"));
    expect(result.current.isOpen("b")).toBe(false);
  });
  it("multi: rows toggle independently", () => {
    const { result } = renderHook(() => useExpanded("multi"));
    act(() => result.current.toggle("a"));
    act(() => result.current.toggle("b"));
    expect(result.current.isOpen("a")).toBe(true);
    expect(result.current.isOpen("b")).toBe(true);
    act(() => result.current.toggle("a"));
    expect(result.current.isOpen("a")).toBe(false);
    expect(result.current.isOpen("b")).toBe(true);
  });
  it("close(id) closes one row; close() closes everything", () => {
    const { result } = renderHook(() => useExpanded("multi"));
    act(() => {
      result.current.toggle("a");
      result.current.toggle("b");
    });
    act(() => result.current.close("a"));
    expect(result.current.isOpen("a")).toBe(false);
    expect(result.current.isOpen("b")).toBe(true);
    act(() => result.current.close());
    expect(result.current.isOpen("b")).toBe(false);
  });
});

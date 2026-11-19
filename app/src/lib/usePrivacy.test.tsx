// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { isPrivate, setPrivate } from "./privacy";
import { usePrivacy, usePrivacyHotkey } from "./usePrivacy";

afterEach(() => { cleanup(); setPrivate(false); });

describe("usePrivacy", () => {
  it("reads the flag, follows every change, and hands back the setter", () => {
    const { result } = renderHook(() => usePrivacy());
    expect(result.current[0]).toBe(false);
    act(() => result.current[1](true));
    expect(result.current[0]).toBe(true);
    expect(isPrivate()).toBe(true);
    act(() => setPrivate(false));                    // a change from anywhere else (the hotkey, another component)
    expect(result.current[0]).toBe(false);
  });
  it("a component follows a switch made in another tab", () => {
    const { result } = renderHook(() => usePrivacy());
    act(() => { localStorage.setItem("ff.privacy", "1"); window.dispatchEvent(new StorageEvent("storage", { key: "ff.privacy" })); });
    expect(result.current[0]).toBe(true);
    act(() => { localStorage.removeItem("ff.privacy"); window.dispatchEvent(new StorageEvent("storage", { key: "ff.privacy" })); });
    expect(result.current[0]).toBe(false);
  });
  it("two components share one flag", () => {
    const a = renderHook(() => usePrivacy());
    const b = renderHook(() => usePrivacy());
    act(() => a.result.current[1](true));
    expect(b.result.current[0]).toBe(true);
  });
});

describe("usePrivacyHotkey (Alt+Shift+P)", () => {
  const press = (init: KeyboardEventInit) => {
    const e = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
    act(() => { window.dispatchEvent(e); });
    return e;
  };
  const mount = () => { function Host() { usePrivacyHotkey(); return null; } return render(<Host />); };

  it("toggles privacy mode on and off from anywhere, and the key press is consumed", () => {
    mount();
    const on = press({ code: "KeyP", altKey: true, shiftKey: true });
    expect(isPrivate()).toBe(true);
    expect(on.defaultPrevented).toBe(true);
    press({ code: "KeyP", altKey: true, shiftKey: true });
    expect(isPrivate()).toBe(false);
  });
  it("goes by the physical key: Alt+Shift+P types a symbol on a Mac, whatever e.key says", () => {
    mount();
    press({ key: "∏", code: "KeyP", altKey: true, shiftKey: true });
    expect(isPrivate()).toBe(true);
  });
  it("ignores every other chord (P alone, Alt+P, Shift+P, Ctrl+Alt+Shift+P, Alt+Shift+O)", () => {
    mount();
    for (const init of [{ code: "KeyP" }, { code: "KeyP", altKey: true }, { code: "KeyP", shiftKey: true },
      { code: "KeyP", altKey: true, shiftKey: true, ctrlKey: true }, { code: "KeyP", altKey: true, shiftKey: true, metaKey: true },
      { code: "KeyO", altKey: true, shiftKey: true }]) {
      const e = press(init);
      expect(isPrivate(), JSON.stringify(init)).toBe(false);
      expect(e.defaultPrevented, JSON.stringify(init)).toBe(false);
    }
  });
  it("a held key is one press: auto-repeat neither strobes the mode nor lets the chord through", () => {
    mount();
    press({ code: "KeyP", altKey: true, shiftKey: true });
    expect(isPrivate()).toBe(true);
    const held = press({ code: "KeyP", altKey: true, shiftKey: true, repeat: true });
    expect(isPrivate(), "a repeat is ignored (an odd number of them would otherwise flip it)").toBe(true);
    expect(held.defaultPrevented, "the chord is still consumed").toBe(true);
    press({ code: "KeyP", altKey: true, shiftKey: true });             // a fresh press toggles again
    expect(isPrivate()).toBe(false);
  });
  it("works while focus is in an input", () => {
    const { container } = render(<><HotHost /><input /></>);
    const input = container.querySelector("input")!;
    act(() => { input.focus(); });
    fireEvent.keyDown(input, { code: "KeyP", altKey: true, shiftKey: true });
    expect(isPrivate()).toBe(true);
  });
  it("stops listening when its component unmounts", () => {
    const { unmount } = mount();
    unmount();
    press({ code: "KeyP", altKey: true, shiftKey: true });
    expect(isPrivate()).toBe(false);
  });
});
function HotHost() { usePrivacyHotkey(); return null; }

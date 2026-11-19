// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useEffect, useSyncExternalStore } from "react";
import { isPrivate, setPrivate, subscribePrivacy } from "./privacy";

/** the privacy flag as React state: re-renders on every change, from any component or the hotkey */
export function usePrivacy(): [boolean, (v: boolean) => void] {
  const on = useSyncExternalStore(subscribePrivacy, isPrivate, () => false);
  return [on, setPrivate];
}

/** Alt+Shift+P toggles privacy mode from anywhere (Ctrl+Shift+P is Firefox's private window). It goes by the
 *  physical key, `e.code`: Alt+Shift+P types a symbol on a Mac, so `e.key` is no use. Ctrl / Meta chords are left
 *  alone (Ctrl+Alt is AltGr on Windows layouts), and a held key (`e.repeat`) toggles once. The listener reads the flag at press time, so it never goes stale. */
export function usePrivacyHotkey(): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.altKey || !e.shiftKey || e.ctrlKey || e.metaKey || e.code !== "KeyP") return;
      e.preventDefault();
      if (e.repeat) return;                    // a held key is one press, not a strobe
      setPrivate(!isPrivate());
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

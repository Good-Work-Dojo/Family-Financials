// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo, useSyncExternalStore } from "react";

/** One per-device flag kept in localStorage (the essential toggles, the theme): read fresh, written
 *  through, and told about by every reader on the page AND by other tabs. `parse` turns the stored string (null =
 *  nothing stored) into the value, and must never throw: anything unreadable is the default. `serialize` returns
 *  the string to store, or null to remove the key.
 *
 *  When the storage refuses a write (Safari private mode, quota) or does not exist (a test in plain node), the value
 *  still holds for the rest of this page view from memory; it just does not survive a reload. Another tab's write
 *  arrives as the `storage` event (it never fires in the tab that wrote), and the next read sees it. */
export interface LocalFlagStore<T> {
  get(): T;
  /** persist `next` (or hold it in memory) and tell every subscriber */
  set(next: T): void;
  subscribe(cb: () => void): () => void;
  /** the value as React state: re-renders on this page's writes and other tabs', one parse per stored string */
  use(): T;
  /** test seam: forget an in-memory fallback left by a previous test */
  resetMemory(): void;
}

export function localFlagStore<T>(
  key: string, parse: (raw: string | null) => T, serialize: (value: T) => string | null,
): LocalFlagStore<T> {
  let memory: { raw: string | null } | null = null;
  const listeners = new Set<() => void>();

  function rawRead(): string | null {
    if (memory) return memory.raw;
    try { return typeof localStorage === "undefined" ? null : localStorage.getItem(key); } catch { return null; }
  }
  function subscribe(cb: () => void): () => void {
    listeners.add(cb);
    const onStorage = (e: StorageEvent) => { if (e.key === null || e.key === key) cb(); };
    if (typeof window !== "undefined") window.addEventListener("storage", onStorage);
    return () => {
      listeners.delete(cb);
      if (typeof window !== "undefined") window.removeEventListener("storage", onStorage);
    };
  }

  return {
    get: () => parse(rawRead()),
    set(next) {
      const raw = serialize(next);
      try {
        if (typeof localStorage === "undefined") throw new Error("no storage");
        if (raw === null) localStorage.removeItem(key); else localStorage.setItem(key, raw);
        memory = null;
      } catch {
        memory = { raw };
      }
      listeners.forEach((l) => l());
    },
    subscribe,
    use() {
      // the raw string is the snapshot (stable by value), parsed once per change
      const raw = useSyncExternalStore(subscribe, rawRead, () => null);
      return useMemo(() => parse(raw), [raw]);
    },
    resetMemory() { memory = null; },
  };
}

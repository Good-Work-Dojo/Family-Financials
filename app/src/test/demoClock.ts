// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Test support: the suites that mount the app on a demo household's exports run at that household's ingest instant
// (manifest.json `generated_at`), whatever the machine's clock says, so every "today", "N days ago" and "next 30 days"
// the pages compute is the same on every run. Only `Date` is faked: timers stay real, so Testing Library's waitFor and
// findBy* (which poll with real timers when setTimeout is not faked) and the suites' own waits behave as without it.
// `new Date()` and `Date.now()` read the instant and do not advance. Nothing in the application imports this module.
import { vi } from "vitest";
import { readExport } from "./exports";

/** The exports' ingest instant: manifest.json `generated_at` (an ISO time with the household zone's offset). */
export function ingestInstant(): Date {
  const stamp = readExport<{ generated_at?: unknown }>("manifest.json").generated_at;
  const at = typeof stamp === "string" ? new Date(stamp) : new Date(Number.NaN);
  if (Number.isNaN(at.getTime())) throw new Error(`manifest.json generated_at is not an instant (got ${String(stamp)})`);
  return at;
}

/** Pins `Date` at the ingest instant (fake timers limited to Date). Call in a suite's beforeAll. */
export function freezeAtIngest(): void {
  const at = ingestInstant();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(at);
}

/** Gives the real clock back. Call in the suite's afterAll. */
export function thaw(): void {
  vi.useRealTimers();
}

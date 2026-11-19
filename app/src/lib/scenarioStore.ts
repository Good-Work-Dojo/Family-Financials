// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Scenario persistence document + pure store operations — no React, no fetch.
// The same JSON shape is what GET/PUT /api/scenarios exchanges with
// deploy/scenario_store.py; the server validates but never interprets it.
// Mutations take `nowISO`/`id` as parameters (deterministic, testable) and
// always return a fresh store with updated_at stamped.
import { storageKey } from "./demo";
import { uuid4 } from "./ids";
import { DEFAULT_INPUTS, SecondHouseInputs } from "./secondHouse";

export interface Scenario {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  inputs: SecondHouseInputs;
  draws: Record<string, number>;   // account id -> $ to draw
}

export interface ScenarioStore {
  version: 1;
  updated_at: string;
  scenarios: Scenario[];
}

export const MAX_SCENARIOS = 5;
export const LOCAL_KEY = storageKey("second-house-scenarios");
export const ACTIVE_KEY = storageKey("second-house-active");
/** A store that has never been written must lose newerOf() against any client
 *  copy — the server (deploy/scenario_store.py) uses the same sentinel. */
import { EPOCH_ISO } from "../data/useSyncedDoc";

export function emptyStore(): ScenarioStore {
  return { version: 1, updated_at: EPOCH_ISO, scenarios: [] };
}

const num = (v: unknown, fallback: number): number =>
  typeof v === "number" && Number.isFinite(v) ? v : fallback;
const str = (v: unknown, fallback: string): string =>
  typeof v === "string" && v ? v : fallback;

/** Defensive parse of anything (server JSON, localStorage, garbage). Wrong
 *  shapes collapse to an empty store; each scenario's inputs are backfilled
 *  key-by-key from DEFAULT_INPUTS (forward compat when new knobs appear);
 *  draws keep only finite positive numbers. */
export function normalizeStore(raw: unknown): ScenarioStore {
  if (typeof raw !== "object" || raw === null) return emptyStore();
  const doc = raw as Record<string, unknown>;
  if (doc.version !== 1 || !Array.isArray(doc.scenarios)) return emptyStore();
  const scenarios: Scenario[] = [];
  for (const item of doc.scenarios.slice(0, MAX_SCENARIOS)) {
    if (typeof item !== "object" || item === null) continue;
    const s = item as Record<string, unknown>;
    if (typeof s.id !== "string" || !s.id) continue;
    const rawInputs = (typeof s.inputs === "object" && s.inputs !== null
      ? s.inputs : {}) as Record<string, unknown>;
    const inputs = Object.fromEntries(
      Object.entries(DEFAULT_INPUTS).map(([k, d]) => [k, num(rawInputs[k], d)]),
    ) as unknown as SecondHouseInputs;
    const draws: Record<string, number> = {};
    if (typeof s.draws === "object" && s.draws !== null) {
      for (const [k, v] of Object.entries(s.draws as Record<string, unknown>)) {
        if (typeof v === "number" && Number.isFinite(v) && v > 0) draws[k] = v;
      }
    }
    scenarios.push({
      id: s.id,
      name: str(s.name, "Scenario"),
      created_at: str(s.created_at, EPOCH_ISO),
      updated_at: str(s.updated_at, EPOCH_ISO),
      inputs, draws,
    });
  }
  return { version: 1, updated_at: str(doc.updated_at, EPOCH_ISO), scenarios };
}

/** Load-time conflict pick: ISO timestamps compare lexicographically. */
export function newerOf(a: ScenarioStore, b: ScenarioStore): ScenarioStore {
  return b.updated_at > a.updated_at ? b : a;
}

export function uniqueName(base: string, existing: string[]): string {
  if (!existing.includes(base)) return base;
  for (let n = 2; ; n++) {
    const candidate = `${base} (${n})`;
    if (!existing.includes(candidate)) return candidate;
  }
}

/** A scenario's id: a v4 UUID (lib/ids.ts, shared with the saved views). */
export function newScenarioId(): string {
  return uuid4();
}

const stamp = (scenarios: Scenario[], nowISO: string): ScenarioStore =>
  ({ version: 1, updated_at: nowISO, scenarios });

/** null at the 5-scenario cap. */
export function addScenario(s: ScenarioStore, id: string, nowISO: string): ScenarioStore | null {
  if (s.scenarios.length >= MAX_SCENARIOS) return null;
  const name = uniqueName(`Scenario ${s.scenarios.length + 1}`, s.scenarios.map((x) => x.name));
  return stamp([...s.scenarios, {
    id, name, created_at: nowISO, updated_at: nowISO,
    inputs: { ...DEFAULT_INPUTS }, draws: {},
  }], nowISO);
}

export function duplicateScenario(
  s: ScenarioStore, srcId: string, newId: string, nowISO: string,
): ScenarioStore | null {
  if (s.scenarios.length >= MAX_SCENARIOS) return null;
  const src = s.scenarios.find((x) => x.id === srcId);
  if (!src) return null;
  return stamp([...s.scenarios, {
    id: newId,
    name: uniqueName(src.name, s.scenarios.map((x) => x.name)),
    created_at: nowISO, updated_at: nowISO,
    inputs: { ...src.inputs }, draws: { ...src.draws },
  }], nowISO);
}

export function renameScenario(s: ScenarioStore, id: string, name: string, nowISO: string): ScenarioStore {
  return stamp(s.scenarios.map((x) =>
    x.id === id ? { ...x, name: name.trim() || x.name, updated_at: nowISO } : x), nowISO);
}

export function removeScenario(s: ScenarioStore, id: string, nowISO: string): ScenarioStore {
  return stamp(s.scenarios.filter((x) => x.id !== id), nowISO);
}

export function patchInputs(
  s: ScenarioStore, id: string, patch: Partial<SecondHouseInputs>, nowISO: string,
): ScenarioStore {
  return stamp(s.scenarios.map((x) =>
    x.id === id ? { ...x, inputs: { ...x.inputs, ...patch }, updated_at: nowISO } : x), nowISO);
}

/** amount ≤ 0 (or non-finite) deletes the draw. */
export function patchDraw(
  s: ScenarioStore, id: string, accountId: string, amount: number, nowISO: string,
): ScenarioStore {
  return stamp(s.scenarios.map((x) => {
    if (x.id !== id) return x;
    const draws = { ...x.draws };
    if (Number.isFinite(amount) && amount > 0) draws[accountId] = amount;
    else delete draws[accountId];
    return { ...x, draws, updated_at: nowISO };
  }), nowISO);
}

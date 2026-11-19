// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Scenario persistence: server-first (GET/PUT ./api/scenarios via Caddy →
// deploy/scenario_store.py), transparent localStorage fallback when the API is
// unreachable (dev without the store, server down). All document logic lives
// in lib/scenarioStore.ts; transport lives in useSyncedDoc — this hook only
// binds the two plus the active-scenario UI state.
import { useCallback, useEffect, useState } from "react";
import {
  ACTIVE_KEY, addScenario, duplicateScenario, emptyStore, LOCAL_KEY,
  newScenarioId, normalizeStore, patchDraw, patchInputs, removeScenario,
  renameScenario, Scenario, ScenarioStore,
} from "../lib/scenarioStore";
import { clock } from "../lib/clock";
import type { SecondHouseInputs } from "../lib/secondHouse";
import { storeUrl, useSyncedDoc, type SaveState } from "./useSyncedDoc";

export type { SaveState };

export interface ScenarioStoreApi {
  loading: boolean;
  /** true → API unreachable; edits persist in this browser only */
  localOnly: boolean;
  saveState: SaveState;
  store: ScenarioStore;
  activeId: string | null;
  active: Scenario | null;
  setActiveId: (id: string) => void;
  create: () => void;
  rename: (id: string, name: string) => void;
  duplicate: (id: string) => void;
  remove: (id: string) => void;
  setInputs: (id: string, patch: Partial<SecondHouseInputs>) => void;
  setDraw: (id: string, accountId: string, amount: number) => void;
}

export function useScenarioStore(): ScenarioStoreApi {
  const { loading, localOnly, saveState, doc: store, commit } =
    useSyncedDoc<ScenarioStore>({
      url: storeUrl("scenarios"),
      localKey: LOCAL_KEY,
      normalize: normalizeStore,
      empty: emptyStore,
    });
  const [activeId, setActiveIdState] = useState<string | null>(null);

  // restore the remembered active scenario once the store has loaded
  useEffect(() => {
    if (loading) return;
    const remembered = localStorage.getItem(ACTIVE_KEY);
    setActiveIdState(
      store.scenarios.some((s) => s.id === remembered)
        ? remembered
        : store.scenarios[0]?.id ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading]);

  const setActiveId = useCallback((id: string) => {
    setActiveIdState(id);
    try { localStorage.setItem(ACTIVE_KEY, id); } catch { /* cosmetic only */ }
  }, []);


  const create = useCallback(() => {
    const id = newScenarioId();
    const next = addScenario(store, id, clock.nowISO());
    if (!next) return;                       // at the cap — UI disables the button too
    commit(next);
    setActiveId(id);
  }, [store, commit, setActiveId]);

  const duplicate = useCallback((srcId: string) => {
    const id = newScenarioId();
    const next = duplicateScenario(store, srcId, id, clock.nowISO());
    if (!next) return;
    commit(next);
    setActiveId(id);
  }, [store, commit, setActiveId]);

  const remove = useCallback((id: string) => {
    const next = removeScenario(store, id, clock.nowISO());
    commit(next);
    if (activeId === id) setActiveId(next.scenarios[0]?.id ?? "");
  }, [store, activeId, commit, setActiveId]);

  const rename = useCallback((id: string, name: string) =>
    commit(renameScenario(store, id, name, clock.nowISO())), [store, commit]);
  const setInputs = useCallback((id: string, patch: Partial<SecondHouseInputs>) =>
    commit(patchInputs(store, id, patch, clock.nowISO())), [store, commit]);
  const setDraw = useCallback((id: string, accountId: string, amount: number) =>
    commit(patchDraw(store, id, accountId, amount, clock.nowISO())), [store, commit]);

  return {
    loading, localOnly, saveState, store, activeId,
    active: store.scenarios.find((s) => s.id === activeId) ?? null,
    setActiveId, create, rename, duplicate, remove, setInputs, setDraw,
  };
}

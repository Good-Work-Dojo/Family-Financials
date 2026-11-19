// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo } from "react";
import { effectiveModules, type Facts, factsOf, type ModuleId, type Modules } from "../lib/modules";
import { useData } from "./DataContext";

export interface ModulesApi {
  /** whether each module is on (lib/modules.ts effectiveModules: the family's preference, else the config's seed) */
  modules: Modules;
  /** what the household has that a page may need (lib/modules.ts factsOf) */
  facts: Facts;
  /** switch a module on or off for the family (prefs.modules), or with `null` clear the family's switch so the
   *  config's seed decides again; every page reads it at once, with no reload and no ingest */
  setModule: (id: ModuleId, on: boolean | null) => void;
}

/** the household's modules and facts, from the loaded exports and the family's preferences */
export function useModules(): ModulesApi {
  const { data, prefs } = useData();
  const seed = data.assumptions.modules;
  const mine = prefs.doc.modules;
  const modules = useMemo(() => effectiveModules(seed, mine), [seed, mine]);
  const { mortgage } = factsOf(data.assumptions);
  const facts = useMemo((): Facts => ({ mortgage }), [mortgage]);
  const { setModule } = prefs;
  return useMemo(() => ({ modules, facts, setModule }), [modules, facts, setModule]);
}

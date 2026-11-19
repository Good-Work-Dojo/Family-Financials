// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The dashboard's switchable pages. A module is an optional part of the app, off unless switched on: in the household's
// config (household.yaml `modules`, exported as assumptions.json `modules`, the seed) or in the family's preferences
// (`prefs.modules`, which win per module). A page may instead need something the household has: the House page needs an
// account marked as the mortgage. App.tsx PAGES says which page carries which gate; a page whose gate is closed keeps
// its route, which shows components/ModuleOff.tsx, and nothing in the app leads into it (App.tsx pagesFor, GatedLink).
import { createContext, useContext } from "react";
import type { Assumptions } from "../data/types";

/** every module the dashboard knows, in the ETL's order (etl/models.py MODULE_IDS; tests/test_validate.py pins the two) */
export const MODULE_IDS = ["home_buying"] as const;
export type ModuleId = (typeof MODULE_IDS)[number];
export const isModuleId = (id: string): id is ModuleId => (MODULE_IDS as readonly string[]).includes(id);

/** each module's name, as a page and Settings print it */
export const MODULE_NAMES: Readonly<Record<ModuleId, string>> = { home_buying: "Home buying" };

/** whether each module is on */
export type Modules = Readonly<Record<ModuleId, boolean>>;

/** what a page may need the household to have: `mortgage`, an account marked as the mortgage */
export type Need = "mortgage";
/** whether the household has each thing a page may need */
export type Facts = Readonly<Record<Need, boolean>>;

/** what a page or a hub tab of App.tsx PAGES is gated by: the module it belongs to, or what it needs (or both) */
export interface Gate {
  module?: ModuleId;
  needs?: Need;
}

/** where a module's value comes from: the family's switch made in the dashboard, the household's config, or neither
 *  (off by default) */
export type ModuleOrigin = "dashboard" | "config" | "default";

/** One module's value and where it comes from: the family's preference (`prefs`, prefs.modules) where it states one
 *  ("dashboard"), else the config's seed (`seed`, assumptions.json `modules`, "config"), else off ("default"). Only a
 *  boolean states a value; anything else falls through to the next source. `config` is the seed's own value whatever
 *  the dashboard says (null when the config states none), so a page can say what clearing the switch would give. */
export function moduleSource(id: ModuleId, seed: Readonly<Record<string, unknown>>, prefs: Readonly<Record<string, unknown>>):
  { on: boolean; from: ModuleOrigin; config: boolean | null } {
  const stated = (from: Readonly<Record<string, unknown>>): boolean | null => {
    const v = Object.hasOwn(from, id) ? from[id] : undefined;
    return typeof v === "boolean" ? v : null;
  };
  const mine = stated(prefs), config = stated(seed);
  if (mine !== null) return { on: mine, from: "dashboard", config };
  if (config !== null) return { on: config, from: "config", config };
  return { on: false, from: "default", config };
}

/** Whether each module is on (moduleSource): the family's preference where it states one, else the config's seed,
 *  else off. A preference of false turns off a module the config switches on. A key that is no module is ignored. */
export function effectiveModules(seed: Readonly<Record<string, unknown>>, prefs: Readonly<Record<string, unknown>>): Modules {
  return Object.fromEntries(MODULE_IDS.map((id) => [id, moduleSource(id, seed, prefs).on])) as Record<ModuleId, boolean>;
}

/** what the household has that a page may need, from the exports: a mortgage when an account is the mortgage
 *  (assumptions.json `mortgage` is not null) */
export const factsOf = (assumptions: Pick<Assumptions, "mortgage">): Facts => ({ mortgage: assumptions.mortgage !== null });

/** whether a page behind `gate` shows: its module is on and the household has what it needs */
export const gateOpen = (gate: Gate, modules: Modules, facts: Facts): boolean =>
  (gate.module === undefined || modules[gate.module]) && (gate.needs === undefined || facts[gate.needs]);

/** whether an in-app path (a query or a hash after it is ignored) leads to a page that shows. The shell (App.tsx)
 *  provides it from the household's modules and facts; with no provider every page shows. */
export type RouteOpen = (path: string) => boolean;
const OpenRoutes = createContext<RouteOpen>(() => true);
export const OpenRoutesProvider = OpenRoutes.Provider;
export const useRouteOpen = (): RouteOpen => useContext(OpenRoutes);

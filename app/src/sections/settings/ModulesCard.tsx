// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { type ReactNode, useId, useRef } from "react";
import { Code } from "../../components/NoData";
import { Card, LocalOnlyNotice } from "../../components/ui";
import { useData } from "../../data/DataContext";
import { useModules } from "../../data/useModules";
import { MODULE_IDS, MODULE_NAMES, type ModuleId, moduleSource } from "../../lib/modules";

// Settings → Modules: one labelled switch per optional part of the dashboard (lib/modules.ts MODULE_IDS), each with what
// its page is for and where its value comes from (moduleSource) - the family's switch made in the dashboard, which wins,
// else the household's config (household.yaml `modules`, the seed), else off. A switch writes the family's preference
// (useModules().setModule) and the app follows at once, with no reload and no ingest; the button under it clears it,
// named by what that gives: "Use the config's setting (on)" or "(off)", or "Go back to the default (off)" when the
// config states nothing. The House page is no module (it needs an account marked as the mortgage), so the card says
// what brings it, and only while the household has none.

/** what each module adds, in one sentence */
const MODULE_PURPOSE: Readonly<Record<ModuleId, string>> = {
  home_buying: "Adds a Second house page under Planning that works through buying another home: its costs, how you "
    + "would fund it and the cash it would leave.",
};

/** the config key a module's seed is stated in */
const SEED_HINT = <><Code>modules:</Code> in <Code>household.yaml</Code></>;

export default function ModulesCard() {
  const { prefs } = useData();
  const { facts } = useModules();
  return (
    <Card testId="card-modules" title="Modules">
      <p className="text-sm text-zinc-300">
        Optional parts of the dashboard, each off until it is switched on. A switch here applies at once, for the whole
        family, and wins over the household's config.
        {prefs.localOnly && <LocalOnlyNotice what="switches" refused={prefs.refused} />}
      </p>
      <ul className="mt-3 divide-y divide-zinc-800 border-t border-zinc-800">
        {MODULE_IDS.map((id) => <ModuleSwitch key={id} id={id} />)}
      </ul>
      {!facts.mortgage && (
        <p data-testid="house-needs-mortgage" className="mt-3 text-xs text-zinc-400">
          The House page appears when an account is marked as the mortgage{" "}
          <span className="text-zinc-500">(<Code>mortgage: true</Code> in <Code>accounts.yaml</Code>)</span>.
        </p>
      )}
    </Card>
  );
}

/** what clearing the family's switch gives, in the words of the button that does it: the config's value when it states
 *  one, else the default (off) */
export const clearSwitchLabel = (config: boolean | null): string =>
  config === null ? "Go back to the default (off)" : `Use the config's setting (${config ? "on" : "off"})`;

/** one module: its name and switch, what it adds, where its value comes from (lib/modules.ts moduleSource), and, while
 *  the family's switch decides it, the way back to what the config (or the default) gives */
function ModuleSwitch({ id }: { id: ModuleId }) {
  const { data, prefs } = useData();
  const { setModule } = useModules();
  const nameId = useId(), purposeId = useId(), sourceId = useId();
  const control = useRef<HTMLButtonElement>(null);
  const { on, from, config } = moduleSource(id, data.assumptions.modules, prefs.doc.modules);
  const state = on ? "On" : "Off";
  const source: ReactNode = from === "dashboard"
    ? `${state} ${prefs.localOnly ? "in this browser" : "for the whole family, switched in the dashboard"}`
    : from === "config"
      ? <>{state} in the household's config <span className="text-zinc-500">({SEED_HINT})</span></>
      : "Off (the default)";
  const back = clearSwitchLabel(config);
  // clearing the family's switch removes the button that was pressed: focus goes back to the switch
  const followConfig = () => { setModule(id, null); control.current?.focus(); };
  return (
    <li data-testid={`module-${id}`} className="flex items-start justify-between gap-4 py-3">
      <div className="min-w-0">
        <p id={nameId} className="text-sm font-medium text-zinc-200">{MODULE_NAMES[id]}</p>
        <p id={purposeId} className="mt-0.5 text-xs text-zinc-400">{MODULE_PURPOSE[id]}</p>
        <p id={sourceId} data-testid="module-source" className="mt-1 text-xs text-zinc-400">{source}</p>
        {from === "dashboard" && (
          // the visible words begin its name, which says which module it is for
          <button type="button" onClick={followConfig} aria-label={`${back} for ${MODULE_NAMES[id]}`}
            className="mt-1 inline-flex min-h-[40px] items-center text-xs text-blue-400 hover:underline md:min-h-0">
            {back}
          </button>
        )}
      </div>
      <button ref={control} type="button" role="switch" aria-checked={on} aria-labelledby={nameId}
        aria-describedby={`${purposeId} ${sourceId}`} onClick={() => setModule(id, !on)}
        className="inline-flex min-h-11 shrink-0 items-center rounded-full focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-400 md:min-h-0">
        <span aria-hidden="true" className={`relative inline-block h-6 w-11 rounded-full border transition-colors ${on
          ? "border-emerald-600 bg-emerald-600" : "border-zinc-500 bg-zinc-800"}`}>
          <span className={`absolute top-0.5 size-[18px] rounded-full transition-transform ${on
            ? "left-0.5 translate-x-5 bg-white" : "left-0.5 bg-zinc-400"}`} />
        </span>
      </button>
    </li>
  );
}

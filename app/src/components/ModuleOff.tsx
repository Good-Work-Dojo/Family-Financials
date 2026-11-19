// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useModules } from "../data/useModules";
import { type Gate, MODULE_NAMES, type ModuleId, type Need } from "../lib/modules";
import { Code } from "./NoData";
import { Card } from "./ui";

// What a route shows while its page is switched off (App.tsx pageRoute): the page's own name as the page's one h1, one
// sentence saying what the page is for, and the one thing that brings it. A page of a module that is off has a button
// naming the module ("Turn on Home buying"), which switches the module on for the family (prefs.modules) and shows the
// page at once, with no reload and no ingest, focus then on the page's main landmark, and a link to Settings; a page
// that needs something the household lacks says what brings it, with the config key as a hint. No figure and no chart.
// The address stays, so a bookmark opens the page once it shows.

/** what each module's page is for, in one sentence that names the module and says it is off */
const MODULE_PAGE: Readonly<Record<ModuleId, string>> = {
  home_buying: "This page works through buying another home — its costs, how you would fund it and the cash it would "
    + `leave — and is part of ${MODULE_NAMES.home_buying}, which is off.`,
};

/** what a page that needs `Need` is for, and what brings it: plain words, then the config key */
const NEED_PAGE: Readonly<Record<Need, { purpose: string; brings: string; hint: ReactNode }>> = {
  mortgage: {
    purpose: "This page follows the mortgage: its path to payoff, the home's equity and whether extra payments beat "
      + "investing.",
    brings: "It appears after the next ingest once an account is marked as the mortgage",
    hint: <><Code>mortgage: true</Code> in <Code>accounts.yaml</Code></>,
  },
};

/** the page `label` names, in place of its own while `gate` is closed: the module part when its module is off, else
 *  the part the household lacks */
export default function ModuleOff({ label, gate }: { label: string; gate: Gate }) {
  const { modules, setModule } = useModules();
  const sentence = useId();
  const root = useRef<HTMLDivElement>(null);
  // "Turn on" goes with this page: once the module's page has taken its place (this one unmounts), focus moves to the
  // main landmark it stood in, so a keyboard or screen-reader user goes on from the page, not from the document's top
  const [handOff] = useState<{ to: HTMLElement | null }>(() => ({ to: null }));
  useEffect(() => () => handOff.to?.focus(), [handOff]);
  const turnOn = (id: ModuleId) => {
    handOff.to = root.current?.closest("main") ?? null;
    setModule(id, true);
  };
  const off = gate.module !== undefined && !modules[gate.module] ? gate.module : null;
  return (
    <div ref={root} data-testid="module-off" data-gate={off ?? gate.needs} className="max-w-2xl">
      <Card testId="card-module-off">
        <h1 className="text-xl font-bold">{label}</h1>
        {off !== null ? (
          <>
            <p id={sentence} className="mt-2 text-sm text-zinc-300">{MODULE_PAGE[off]}</p>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button type="button" aria-describedby={sentence} onClick={() => turnOn(off)}
                className="min-h-[40px] rounded-md border border-zinc-700 px-3 text-sm text-zinc-200 hover:bg-zinc-800 md:min-h-0 md:py-1">
                Turn on {MODULE_NAMES[off]}
              </button>
              <Link to="/settings" className="inline-flex min-h-[40px] items-center text-sm text-blue-400 hover:underline md:min-h-0">
                Settings
              </Link>
            </div>
          </>
        ) : gate.needs !== undefined && (
          <>
            <p className="mt-2 text-sm text-zinc-300">{NEED_PAGE[gate.needs].purpose}</p>
            <p className="mt-2 text-sm text-zinc-300">
              {NEED_PAGE[gate.needs].brings} <span className="text-zinc-500">({NEED_PAGE[gate.needs].hint})</span>.
            </p>
          </>
        )}
      </Card>
    </div>
  );
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import DefaultTag from "../../components/DefaultTag";
import { Code } from "../../components/NoData";
import PrefEditorRow from "../../components/PrefEditorRow";
import { Card, LocalOnlyNotice } from "../../components/ui";
import { useData } from "../../data/DataContext";
import { PROJECTION_LABELS, type ProjectionKey, projectionSource } from "../../lib/projectionDefaults";
import { SCROLL_TARGETS } from "../../lib/scrollTargets";

/** the keys the card edits; the others it lists, with where each comes from */
const FIELDS: { key: ProjectionKey; unit: string; step: number }[] = [
  { key: "inflation_pct", unit: "%", step: 0.25 },
  { key: "withdrawal_rate_pct", unit: "%", step: 0.25 },
  { key: "ltcg_pct", unit: "%", step: 1 },
];
const LISTED: ProjectionKey[] = ["return_scenarios_pct", "state_income_tax_pct"];

/** Durable projection defaults — these seed the assumption sliders on every
 *  page (Planning's tabs). Per-page sliders remain ephemeral what-ifs; edits
 *  here persist for the whole family via the prefs store. Each key says where its value comes from: saved here, stated
 *  in the configuration (`assumptions.projections_stated`), or the built-in default, which a figure resting on it tags
 *  "default" on its page (lib/projectionDefaults.ts). Settings links here, and so does every such tag (`?card=`,
 *  lib/scrollTargets.ts). */
export default function ProjectionDefaultsCard() {
  const { data, prefs } = useData();
  const p = data.assumptions.projections;
  const stated = data.assumptions.projections_stated;
  const saved = prefs.doc.projections;
  const value = (k: ProjectionKey): string => {
    const v = p[k];
    return Array.isArray(v) ? v.map((r) => `${r}%`).join(" / ") : `${v}%`;
  };
  /** where `k`'s value comes from, in words: a "default" tag, or the place that states it */
  const source = (k: ProjectionKey) => {
    const s = projectionSource(stated, saved, k);
    return (
      <span data-testid={`projection-source-${k}`} className="text-[10px] text-zinc-500">
        {s === "default"
          ? <DefaultTag title={`The built-in default, ${value(k)}: neither the configuration nor this card states it.`} />
          : s === "app" ? "saved here" : "from the configuration"}
      </span>
    );
  };
  return (
    <Card testId="card-projection-defaults" id={SCROLL_TARGETS.projectionDefaults} title="Projection defaults (saved for the whole family)">
      <p className="mb-2 text-[11px] text-zinc-500">
        These start every projection's sliders. A blank box uses the value the configuration states
        (<Code>projections:</Code> in <Code>assumptions.yaml</Code>, shown as the placeholder) or, when it states none, the
        built-in default; each row says which. Sliders on each page stay temporary what-ifs.
        {prefs.localOnly && <LocalOnlyNotice what="defaults" refused={prefs.refused} />}
      </p>
      {/* as many columns as fit: this card sits in a half-width column beside the assumptions list,
          where a fixed 2-3 column grid pushed the number boxes past the card edge */}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(min(19rem,100%),1fr))] gap-x-6 gap-y-1.5">
        {FIELDS.map((f) => {
          const overridden = prefs.doc.projections[f.key] !== undefined;
          const merged = p[f.key] as number;
          // the row, then where its value comes from: beside it where the column is wide enough, under it on a phone
          return (
            <div key={f.key} className="flex min-w-0 flex-wrap items-center gap-x-2">
              <PrefEditorRow label={PROJECTION_LABELS[f.key]}
                step={f.step} money={f.unit === "$"}
                value={overridden ? merged : ""}
                placeholder={String(merged)}
                onChange={(v) => prefs.setProjection(f.key, v)}
                after={<span className="w-4 text-zinc-500">{f.unit}</span>}
                onClear={overridden ? () => prefs.setProjection(f.key, null) : undefined}
                clearTitle={stated.includes(f.key) ? "clear — back to the configuration's value" : "clear — back to the built-in default"} />
              {source(f.key)}
            </div>
          );
        })}
      </div>
      <ul className="mt-2 space-y-1 text-xs">
        {LISTED.map((k) => (
          <li key={k} className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-zinc-400">{PROJECTION_LABELS[k]}</span>
            <span className="tabular-nums text-zinc-200">{value(k)}</span>
            {source(k)}
          </li>
        ))}
      </ul>
      <p className="mt-1 text-[10px] text-faint">
        These two have no box here: they are set in the configuration (<Code>projections:</Code> in{" "}
        <Code>assumptions.yaml</Code>).
      </p>
    </Card>
  );
}

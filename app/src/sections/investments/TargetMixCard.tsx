// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo, useState } from "react";
import EChart from "../../components/EChart";
import { Badge, Card, LocalOnlyNotice, TableScroll } from "../../components/ui";
import { useData } from "../../data/DataContext";
import type { Holding } from "../../data/types";
import { currentYear } from "../../lib/agg";
import { allocationCaption, defaultAllocationTargets } from "../../lib/allocation";
import { baseGrid, baseLegend, CHART_MUTED, CHART_SPLIT, CHART_TEXT } from "../../lib/echarts";
import { fmtPct, fmtPoints, fmtUSD0 } from "../../lib/format";
import { SCROLL_TARGETS } from "../../lib/scrollTargets";
import {
  ALTS_GUIDANCE_PCT, bucketTotals, isOff, MIX_KEYS, MIX_LABELS, mixRows, mixSummary, moveWords, nothingToCompare, sameMix,
  SET_LEGS, type TargetMix, withLeg,
} from "../../lib/targetMix";

/** a target share as the table prints it: whole percents stay whole, a hand-saved fraction keeps one decimal */
const pctText = (v: number) => fmtPct(v / 100, Number.isInteger(v) ? 0 : 1);
const mixLine = (m: TargetMix) => MIX_KEYS.map((k) => `${MIX_LABELS[k]} ${pctText(m[k])}`).join(" · ");

/** "A", "A and B", "A, B and C" */
const andList = (names: readonly string[]): string =>
  names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;

const BTN = "min-h-[40px] rounded-md px-3 text-sm disabled:cursor-default disabled:opacity-40 sm:min-h-0 sm:py-1 sm:text-xs";

/** Accounts → Investments' target mix: stocks, bonds, cash and alternatives as shares of the CLASSIFIED positions
 *  (`holdings`: the positions the page lists), edited in place. An account that reports a balance but no positions
 *  (`unclassified`: the page's balance-only lines) is not classified - its split is unknown - so it is named on its own
 *  line with its share of the investable assets (positions and balance-only accounts together: the Total invested
 *  assets) and the reason, no bucket counts it, and a verdict says how much of the investable assets it covers; with no
 *  classified position at all the card says there is nothing classified to compare, and why.
 *  Until the family saves a mix the card shows a SUGGESTION (lib/allocation.ts, with its basis in one sentence) and
 *  compares the holdings with it side by side: no verdict, no drift, no dollars to move and no colour of advice, so the
 *  page's one advice is the household's own asset-class targets (the card under this one). Once a mix is saved it is the
 *  family's, the card says so, and it measures against it: the verdict, the drift and the dollars, which a drag
 *  previews before Save; Revert goes back to the saved mix. When the household has both a saved mix and asset-class
 *  targets, one line says what each measures, so two numbers are not read as a contradiction.
 *  Settings links here (`?card=`, lib/scrollTargets.ts; the page reads the key). */
export default function TargetMixCard({ holdings, unclassified }: {
  holdings: readonly Holding[]; unclassified: readonly Holding[];
}) {
  const { data, prefs } = useData();
  const year = currentYear(data.manifest);
  const people = data.assumptions.household.people;
  const { targets: suggested, ownerAge } = useMemo(() => defaultAllocationTargets(people, year), [people, year]);
  const saved = prefs.doc.target_mix ?? null;
  const [draft, setDraft] = useState<TargetMix | null>(null);
  const base = saved ?? suggested;
  const shown = draft ?? base;
  const dirty = draft !== null && !sameMix(draft, base);
  // the card advises only against a mix the family saved (or a preview of a change to it)
  const advises = saved !== null;

  const { by, total } = useMemo(() => bucketTotals(holdings), [holdings]);
  const outside = unclassified.reduce((s, h) => s + h.value, 0);
  const rows = useMemo(() => mixRows(by, total, shown), [by, total, shown]);
  // with no classified position there is nothing to compare: the reason (an account with no positions, or nothing at all)
  const nothing = nothingToCompare(total, unclassified.map((h) => h.account));
  const summary = nothing ?? mixSummary(rows, dirty ? "this mix" : "your saved mix");
  // a verdict over part of the investable assets says how much it covers
  const covers = nothing === null && outside > 0 ? total / (total + outside) : null;
  const measured = rows.some((r) => r.driftPts !== null);
  const within = !rows.some(isOff);
  const altsHigh = shown.alts > ALTS_GUIDANCE_PCT;
  // what the target column holds: the family's saved mix, the suggestion, or a mix tried before any save
  const targetHead = advises ? "Target" : sameMix(shown, suggested) ? "Suggested" : "Unsaved mix";
  const both = advises && Object.keys(data.assumptions.targets).length > 0;

  const option = useMemo(() => ({
    grid: { ...baseGrid, top: 28 },
    legend: baseLegend(),
    tooltip: { trigger: "axis", valueFormatter: (v: unknown) => (typeof v === "number" ? `${v.toFixed(1)}%` : "—") },
    xAxis: { type: "category", data: MIX_KEYS.map((k) => MIX_LABELS[k]), axisLabel: { color: CHART_TEXT } },
    yAxis: { type: "value", max: 100, axisLabel: { color: CHART_TEXT, formatter: "{value}%" }, splitLine: { lineStyle: { color: CHART_SPLIT } } },
    series: [
      { name: "Actual", type: "bar", color: "#3b82f6", data: rows.map((r) => (r.actualPct === null ? null : +r.actualPct.toFixed(1))) },
      { name: targetHead, type: "bar", color: CHART_MUTED, data: rows.map((r) => r.targetPct) },
    ],
  }), [rows, targetHead]);

  const save = () => { if (prefs.setTargetMix(shown)) setDraft(null); };
  const status = dirty
    ? <Badge cls="bg-amber-950 text-amber-300">Unsaved changes</Badge>
    : saved
      ? <Badge cls="bg-emerald-950 text-emerald-300">{prefs.localOnly ? "Saved in this browser" : "Saved for the family"}</Badge>
      : <Badge cls="bg-zinc-800 text-zinc-300">Suggestion — not saved</Badge>;

  return (
    <Card testId="card-target-mix" id={SCROLL_TARGETS.targetMix} title="Target mix" right={status}>
      {advises ? (
        <p data-testid="target-mix-summary" className={`mb-3 text-sm font-medium ${!measured ? "text-zinc-400" : within ? "text-emerald-400" : "text-amber-400"}`}>
          {summary}
          {covers !== null && (
            <span className="font-normal text-zinc-400">
              {" "}— covers {fmtPct(covers)} of investable assets; the rest is not classified
            </span>
          )}
        </p>
      ) : (
        <p data-testid="target-mix-basis" className="mb-3 text-sm text-zinc-300">
          A suggestion, not the household's target: {allocationCaption(ownerAge)} ({mixLine(suggested)}).{" "}
          {nothing ?? "Save a mix to measure the holdings against it."}
        </p>
      )}
      {both && (
        <p data-testid="target-mix-measures" className="-mt-1 mb-3 text-xs text-zinc-400">
          Two measures, not a contradiction: this mix is shares of the classified positions alone (the balance-only accounts
          are outside it); the asset-class targets below are shares of net worth by asset class across everything owned,
          debts subtracted (not the share of assets Home's allocation shows).
        </p>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        <div className="min-w-0">
          {SET_LEGS.map((leg) => (
            // the "%" in the label says the boxes hold shares, not amounts; each input names itself for a screen reader
            <label key={leg} className="flex items-center gap-2 text-xs">
              <span className="w-24 shrink-0 text-zinc-300">{MIX_LABELS[leg]}</span>
              <input type="range" min={0} max={100} step={1} value={shown[leg]}
                aria-label={`${MIX_LABELS[leg]} target`} aria-valuetext={`${shown[leg]}%`}
                className="h-10 min-w-0 grow accent-blue-500"
                onChange={(e) => setDraft(withLeg(shown, leg, Number(e.target.value)))} />
              <input type="number" min={0} max={100} step={1} value={shown[leg]}
                aria-label={`${MIX_LABELS[leg]} target, percent`}
                className="h-10 w-16 shrink-0 rounded border border-zinc-700 bg-zinc-900 px-1 text-right text-base tabular-nums sm:h-7 sm:w-14 sm:text-xs"
                onChange={(e) => { if (e.target.value !== "") setDraft(withLeg(shown, leg, Number(e.target.value))); }} />
              <span className="w-4 shrink-0 text-zinc-500">%</span>
            </label>
          ))}
          <div className="flex min-h-[40px] items-center gap-2 text-xs sm:min-h-[28px]">
            <span className="w-24 shrink-0 text-zinc-300">{MIX_LABELS.bonds}</span>
            <span className="grow text-zinc-500">the rest</span>
            <span data-testid="target-mix-bonds" className="w-16 shrink-0 pr-1 text-right tabular-nums text-zinc-200 sm:w-14">{shown.bonds}</span>
            <span className="w-4 shrink-0 text-zinc-500">%</span>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button type="button" onClick={save} disabled={saved !== null && !dirty}
              className={`${BTN} bg-emerald-600 font-medium text-white hover:bg-emerald-500`}>Save</button>
            <button type="button" onClick={() => setDraft(null)} disabled={!dirty}
              className={`${BTN} border border-zinc-700 text-zinc-300 hover:bg-zinc-800`}>Revert</button>
            <button type="button" onClick={() => setDraft({ ...suggested })} disabled={sameMix(shown, suggested)}
              className={`${BTN} border border-zinc-700 text-zinc-300 hover:bg-zinc-800`}>Use suggested (age-based)</button>
          </div>
          <p className="mt-2 text-[11px] text-zinc-400">
            {saved === null
              ? dirty
                ? "Not saved: Save keeps this mix for the whole family and the card then measures the holdings against it; Revert goes back to the suggestion."
                : "Nothing is saved yet. Save keeps a mix for the whole family, and the card then measures the holdings against it."
              : dirty
                ? "Not saved: Save keeps this mix for the whole family, Revert goes back to the saved one."
                : "This is the family's saved mix. Drag a slider to try another; nothing changes until you save."}
            {prefs.localOnly && <LocalOnlyNotice what="target mix changes" refused={prefs.refused} />}
          </p>
          {advises && (
            <p data-testid="target-mix-suggestion" className="mt-1 text-[10px] text-faint">
              Suggested: {mixLine(suggested)} — {allocationCaption(ownerAge)}.
            </p>
          )}
          <p data-testid="target-mix-alts-guidance" className={`mt-1 text-[10px] ${altsHigh ? "text-amber-400" : "text-faint"}`}>
            {altsHigh
              ? `This mix puts ${pctText(shown.alts)} in alternatives, above the common guidance to keep speculative assets under ~${ALTS_GUIDANCE_PCT}%.`
              : `Common guidance: keep speculative assets (the alternatives) under ~${ALTS_GUIDANCE_PCT}%.`}
          </p>
        </div>
        <EChart option={option} height={220} />
      </div>
      <TableScroll className="mt-3" label="Target mix table" stickyFirst>
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-zinc-700 text-left text-zinc-500">
              <th scope="col" className="py-1 pr-2">Bucket</th>
              <th scope="col" className="pl-2 text-right">Actual</th>
              <th scope="col" className="pl-2 text-right">{targetHead}</th>
              {advises && <th scope="col" className="pl-2 text-right">Drift</th>}
              {advises && <th scope="col" className="pl-2 text-right">To rebalance</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} data-testid={`target-mix-row-${r.key}`} className="border-b border-zinc-800/60">
                <th scope="row" className="py-1 pr-2 text-left font-normal text-zinc-300">{r.label}</th>
                <td className="pl-2 text-right tabular-nums">{fmtPct(r.actualPct === null ? null : r.actualPct / 100)}</td>
                <td className="pl-2 text-right tabular-nums">{pctText(r.targetPct)}</td>
                {advises && (
                  <td className={`whitespace-nowrap pl-2 text-right tabular-nums ${isOff(r) ? "text-amber-400" : "text-zinc-400"}`}>
                    {r.driftPts === null ? "—" : fmtPoints(r.driftPts)}
                  </td>
                )}
                {advises && <td className="whitespace-nowrap pl-2 text-right tabular-nums">{moveWords(r.move)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      {unclassified.length > 0 && (
        <p data-testid="target-mix-unclassified" className="mt-2 text-xs text-zinc-400">
          Not classified: {andList(unclassified.map((h) => h.account))}, {fmtUSD0(outside)}
          {total + outside > 0 && ` (${fmtPct(outside / (total + outside))} of investable assets)`} —{" "}
          {unclassified.length === 1 ? "it reports a balance but no positions, so its split is unknown and the shares above leave it out"
            : "they report a balance but no positions, so their split is unknown and the shares above leave them out"}.
        </p>
      )}
      <p className="mt-2 text-[10px] text-faint">
        Shares of the classified positions{total > 0 && `, ${fmtUSD0(total)}`}: every position listed under Holdings by
        account{unclassified.length > 0 && ", not the balance-only accounts"}. Investable assets are the Total invested
        assets above, positions and balance-only accounts together. The house, cars, bank cash and debts (the
        mortgage, margin) are outside both. Buckets are approximate: bond-like funds (VTEB, VCIT, LQD, SCHP, VGIT) count as
        bonds; crypto, precious metals, private stock and real estate/venture as alternatives; brokerage cash as cash;
        every other position as stocks.{advises && " To-rebalance dollars are directional: mind taxes and let new contributions do the work where they can."}
      </p>
    </Card>
  );
}

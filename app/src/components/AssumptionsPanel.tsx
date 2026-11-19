// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { type ChangeEvent, type ReactNode, useState } from "react";
import { fmtUSD0 } from "../lib/format";
import { usePrivacy } from "../lib/usePrivacy";
import { AmountRange, CurrencyInput } from "./inputs";

export interface AssumptionField {
  key: string;
  label: string;
  min: number;
  max: number;
  step: number;
  unit?: string;          // "%", "$", "yr"
  source?: string;        // shown as tooltip — where the default comes from
  /** a mark after the label while the field holds a value nobody stated (components/DefaultTag.tsx); the collapsed
   *  summary says "(default)" after it then */
  tag?: ReactNode;
  /** a number box (not $) that the person may empty to make the field unset again: an emptied box calls `onClear`
   *  instead of setting 0 (a $ box always does, CurrencyInput's own rule) */
  clearable?: boolean;
}

/**
 * Shared assumptions UI: slider + synced numeric input per field, source
 * tooltips, reset-to-defaults. No projection runs on hidden numbers.
 */
export default function AssumptionsPanel({ fields, values, defaults, onChange, onReset, unset = [], onSet, onClear, testId }: {
  fields: AssumptionField[];
  values: Record<string, number>;
  defaults: Record<string, number>;
  onChange: (v: Record<string, number>) => void;
  /** called as well when "reset" puts every field back to its default: a card that tracks whether the person set a
   *  field (rather than its value) forgets it here */
  onReset?: () => void;
  /** the fields nobody has set yet: the box is empty (the slider sits at `values`, its minimum) until the person moves
   *  the slider or types an amount, 0 included */
  unset?: readonly string[];
  /** called with a field's key whenever the person sets it - the slider moved, an amount typed, even the one it shows */
  onSet?: (key: string) => void;
  /** called with a $ field's key when the person empties its box: the card may make the field unset again */
  onClear?: (key: string) => void;
  testId?: string;
}) {
  const [open, setOpen] = useState(true);
  // the collapsed summary prints through the masked formatter: the panel re-renders when privacy mode is toggled
  usePrivacy();
  const set = (k: string, v: number) => { onChange({ ...values, [k]: v }); onSet?.(k); };

  return (
    <div data-testid={testId} className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <button onClick={() => setOpen(!open)} className="shrink-0 font-semibold text-zinc-300">
          Assumptions {open ? "▾" : "▸"}
        </button>
        <div className="flex min-w-0 items-center gap-3">
          {!open && (
            <span className="min-w-0 truncate text-zinc-500">
              {fields.map((f) => `${f.label} ${unset.includes(f.key) ? "—"
                : f.unit === "$" ? fmtUSD0(values[f.key]) : `${values[f.key]}${f.unit ?? ""}`}${f.tag ? " (default)" : ""}`)
                .join(" · ")}
            </span>
          )}
          <button onClick={() => { onChange({ ...defaults }); onReset?.(); }}
            className="text-zinc-500 underline hover:text-zinc-300">
            reset
          </button>
        </div>
      </div>
      {/* Columns follow THIS panel's width (auto-fill), not the viewport: the same panel sits in a
          full-width card on Investments and in half-width cards on Planning, and a viewport
          breakpoint cannot tell them apart (at 1280px a half-width card got three ~133px tracks, so
          labels overprinted their sliders and the controls ran past the card edge). A track is
          never narrower than 22rem; min(100%, …) lets a narrower panel, a phone, take one
          full-width track. min-w-0 on the slider group lets the range input, whose intrinsic
          minimum is ~130px, shrink instead of pushing the row wider than its card. Labels wrap
          (nothing is cut off) and the number box is wide enough for seven digits at the phone's
          16px font — a 64px box clipped a $1,000,000 home value. */}
      {open && (
        <div className="mt-2 grid gap-x-6 gap-y-2 grid-cols-[repeat(auto-fill,minmax(min(100%,22rem),1fr))]">
          {fields.map((f) => {
            // a $ field is money: its box is the shared CurrencyInput (comma-grouped at rest like Second house's, the mask at
            // rest and the digits only while focused in privacy mode) and its slider the shared AmountRange (blurred, and
            // holding no amount, while privacy mode is on)
            const slider = "min-w-0 grow accent-blue-500";
            // an unset field's box is empty, whatever its slider rests on (a rate nobody stated is no 0%)
            const box = {
              type: "number", min: f.min, max: f.max, step: f.step, value: unset.includes(f.key) ? "" : values[f.key],
              className: "w-24 shrink-0 rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5 text-right text-base tabular-nums sm:w-20 sm:text-xs",
              onChange: (e: ChangeEvent<HTMLInputElement>) => {
                if (f.clearable && onClear && e.target.value === "") onClear(f.key);
                else set(f.key, Number(e.target.value));
              },
            };
            return (
              <label key={f.key} className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-2" title={f.source ? `${f.label} — ${f.source}` : f.label}>
                <span className="text-zinc-400 leading-tight sm:w-32 sm:shrink-0">
                  {f.label}
                  {f.source && <span className="ml-1 cursor-help text-faint">ⓘ</span>}
                  {f.tag}
                </span>
                <span className="flex min-w-0 grow items-center gap-2">
                  {f.unit === "$"
                    ? <AmountRange value={values[f.key]} min={f.min} max={f.max} step={f.step} onCommit={(v) => set(f.key, v)}
                        className={slider} />
                    : <input type="range" min={f.min} max={f.max} step={f.step} value={values[f.key]} className={slider}
                        onChange={(e) => set(f.key, Number(e.target.value))} />}
                  {f.unit === "$"
                    ? <CurrencyInput value={unset.includes(f.key) ? null : values[f.key]} onCommit={(v) => set(f.key, v)}
                        onClear={onClear ? () => onClear(f.key) : undefined} className="w-24 shrink-0 sm:w-20" />
                    : <input {...box} />}
                  <span className="w-5 shrink-0 text-zinc-500">{f.unit}</span>
                </span>
              </label>
            );
          })}
        </div>
      )}
      {open && (
        <p className="mt-2 italic text-faint">
          All projections are deterministic compound-growth illustrations, not forecasts.
        </p>
      )}
    </div>
  );
}

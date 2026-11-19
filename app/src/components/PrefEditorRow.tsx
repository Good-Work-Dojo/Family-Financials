// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { InputHTMLAttributes, ReactNode } from "react";
import { MaskedNumberInput } from "./inputs";

/** Labeled number-input row shared by the in-app preference editors (budgets,
 *  targets, projection defaults); `before`/`after` slot unit markers & extras. `money` marks a dollar amount:
 *  privacy mode masks the box at rest, and a numeric placeholder (the automatic baseline) with it. */
export default function PrefEditorRow({
  label, labelTitle, labelCls = "w-36", value, placeholder, min, max, step,
  inputCls = "w-24", onChange, before, after, onClear, clearTitle, money,
}: {
  label: string; labelTitle?: string; labelCls?: string;
  value: number | ""; placeholder?: string;
  min?: number; max?: number; step?: number; inputCls?: string;
  onChange: (v: number | null) => void;
  before?: ReactNode; after?: ReactNode;
  onClear?: () => void; clearTitle?: string; money?: boolean;
}) {
  const box: InputHTMLAttributes<HTMLInputElement> = {
    type: "number", min, max, step, value, placeholder,
    onChange: (e) => onChange(e.target.value === "" ? null : Number(e.target.value)),
    className: `${inputCls} rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-right text-base tabular-nums sm:py-0.5 sm:text-xs`,
  };
  return (
    <label className="flex min-h-[40px] items-center gap-2 text-xs sm:min-h-0">
      <span className={`${labelCls} shrink-0 truncate text-zinc-400`} title={labelTitle}>{label}</span>
      {before}
      {money ? <MaskedNumberInput {...box} /> : <input {...box} />}
      {after}
      {onClear && (
        <button onClick={onClear}
          title={clearTitle}
          className="text-faint hover:text-zinc-300">✕</button>
      )}
    </label>
  );
}

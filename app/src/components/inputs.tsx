// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Generic form primitives — currency/number/slider inputs and their label row.
import { InputHTMLAttributes, ReactNode, useEffect, useId, useRef, useState } from "react";
import { MASK } from "../lib/privacy";
import { usePrivacy } from "../lib/usePrivacy";
import { Chip } from "./ui";

export function parseMoney(text: string): number | null {
  const t = text.trim().replace(/[$,\s]/g, "").toLowerCase();
  const m = /^(\d*\.?\d+)([km])?$/.exec(t);
  if (!m) return null;
  return Number(m[1]) * (m[2] === "m" ? 1_000_000 : m[2] === "k" ? 1_000 : 1);
}

/** an amount at rest in a $ box: comma-grouped, whole dollars when it is whole to the cent ("50,000" for 50,000.004),
 *  else to the cent ("2,746.40", never "2,746.4") */
const grouped = (v: number): string => v.toLocaleString("en-US", Math.round(v * 100) % 100 === 0
  ? { maximumFractionDigits: 0 } : { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** $ text input: comma-grouped at rest (the privacy mask while privacy mode is on), raw while editing; accepts
 *  "650k"/"1.2m"; commits on blur/Enter, Esc reverts, never emits NaN or negatives. `value` null is an amount nobody has
 *  set: the box is empty (no placeholder, nothing masked) and ANY number typed into it is committed, 0 included; a
 *  number is committed only when it differs from `value`, as for every other box. `onClear`, when given, is called
 *  when the person empties a box that held an amount and leaves it (an optional amount goes back to unset); without
 *  it an emptied box keeps its amount. `id`, `label` (its accessible name when no <label> names it), `describedBy` and
 *  `invalid` tie the box to its label and to an error shown beside it. */
export function CurrencyInput({ value, onCommit, onClear, disabled, className = "w-28", id, label, describedBy, invalid }: {
  value: number | null; onCommit: (v: number) => void; onClear?: () => void; disabled?: boolean; className?: string;
  id?: string; label?: string; describedBy?: string; invalid?: boolean;
}) {
  const [text, setText] = useState<string | null>(null);   // null = not editing
  const [privacy] = usePrivacy();
  // Escape must revert, but blurring still fires onBlur with the edited DOM
  // value — the flag tells that blur to discard instead of commit.
  const escaped = useRef(false);
  const commit = (raw: string) => {
    const v = parseMoney(raw);
    if (v !== null && (value === null || v !== value)) onCommit(v);
    else if (v === null && raw.trim() === "" && value !== null) onClear?.();
    setText(null);
  };
  return (
    <input type="text" inputMode="decimal" disabled={disabled} id={id} aria-label={label} aria-describedby={describedBy}
      aria-invalid={invalid || undefined}
      className={`rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5 text-right text-base tabular-nums placeholder:text-faint disabled:opacity-40 sm:text-xs ${className}`}
      value={text ?? (value === null ? "" : privacy ? MASK : value ? grouped(value) : "")}
      placeholder={value === null ? undefined : "0"}
      // a box of 0 starts its edit at "0" (selected, so typing replaces it): leaving it untouched is no emptying
      onFocus={(e) => { setText(value === null ? "" : String(value)); e.target.select(); }}
      onChange={(e) => setText(e.target.value)}
      onBlur={(e) => {
        if (escaped.current) { escaped.current = false; setText(null); return; }
        commit(e.target.value);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") { escaped.current = true; (e.target as HTMLInputElement).blur(); }
      }} />
  );
}

export function InputRow({ label, sub, children }: {
  label: string; sub?: ReactNode; children: ReactNode;
}) {
  return (
    <label className="flex items-center justify-between gap-2 py-1 text-xs">
      <span className="text-zinc-400">
        {label}
        {sub && <span className="ml-1.5 text-faint">{sub}</span>}
      </span>
      {children}
    </label>
  );
}

/** a number box that shows the privacy mask at rest and the real input while focused (inputs are
 *  exempt while being edited - you cannot edit a number you cannot see). Use it for every money
 *  box; the props are a plain <input>'s. An EMPTY box has no amount to hide (it stays editable), but a
 *  numeric placeholder - an automatic baseline, an estimate - is masked until the box is focused. */
export function MaskedNumberInput(props: InputHTMLAttributes<HTMLInputElement>) {
  const [privacy] = usePrivacy();
  const [editing, setEditing] = useState(false);
  const ref = useRef<HTMLInputElement>(null);
  const empty = props.value === undefined || props.value === null || props.value === "";
  // the swap from the mask to the real input must not drop the focus the user just gave it (autoFocus only works on
  // mount, and a re-render may hand back a different node): the real input takes the focus itself
  useEffect(() => {
    if (privacy && editing && ref.current && document.activeElement !== ref.current) ref.current.focus();
  }, [privacy, editing]);
  // a word placeholder ("min $") is what tells two masked boxes apart: it names the box and leads its title
  const hint = typeof props.placeholder === "string" && !/\d/.test(props.placeholder) ? props.placeholder : undefined;
  if (privacy && !editing && !empty) {
    return <input ref={ref} type="text" readOnly value={MASK} aria-label={props["aria-label"] ?? hint} className={props.className}
      title={`${hint ? `${hint} — ` : ""}hidden by privacy mode — focus to edit`} onFocus={() => setEditing(true)} />;
  }
  const hidePlaceholder = privacy && !editing && typeof props.placeholder === "string" && /\d/.test(props.placeholder);
  return <input {...props} ref={ref}
    placeholder={hidePlaceholder ? MASK : props.placeholder}
    onFocus={(e) => { setEditing(true); props.onFocus?.(e); }}
    onBlur={(e) => { setEditing(false); props.onBlur?.(e); }} />;
}

export function NumberInput({ value, onCommit, min, max, step, unit, className = "w-20" }: {
  value: number; onCommit: (v: number) => void;
  min: number; max: number; step: number; unit?: string; className?: string;
}) {
  const box: InputHTMLAttributes<HTMLInputElement> = {
    type: "number", min, max, step, value,
    className: `rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5 text-right text-base tabular-nums sm:text-xs ${className}`,
    onChange: (e) => {
      const v = Number(e.target.value);
      if (Number.isFinite(v)) onCommit(Math.min(max, Math.max(min, v)));
    },
  };
  return (
    <span className="flex items-center gap-1">
      {/* a $ box is money: the mask covers it at rest while privacy mode is on (a % or a year is not an amount) */}
      {unit === "$" ? <MaskedNumberInput {...box} /> : <input {...box} />}
      {unit && <span className="w-4 text-zinc-500">{unit}</span>}
    </span>
  );
}

/** a slider over an amount (a $ field). With privacy mode off it is a plain slider over the amounts. On, its thumb is
 *  blurred, a screen reader hears "amount hidden", and nothing in it is an amount: it runs over its steps (0 to the
 *  number of steps from `min` to `max`), so its value, min and max say where the thumb sits, never the figure — a plain
 *  slider keeps the figure the page seeds it with, unrounded, in its value attribute. A move commits the amount at that
 *  step, as the plain slider's does. */
export function AmountRange({ value, min, max, step, onCommit, className }: {
  value: number; min: number; max: number; step: number; onCommit: (v: number) => void; className: string;
}) {
  const [privacy] = usePrivacy();
  if (!privacy) {
    return <input type="range" min={min} max={max} step={step} value={value} className={className}
      onChange={(e) => onCommit(Number(e.target.value))} />;
  }
  const steps = Math.max(0, Math.floor((max - min) / step + 1e-9));
  const at = Math.min(steps, Math.max(0, Math.round((value - min) / step)));
  return <input type="range" min={0} max={steps} step={1} value={at} className={`${className} blur-sm`}
    aria-valuetext="amount hidden" onChange={(e) => onCommit(Math.min(max, min + Number(e.target.value) * step))} />;
}

export function SliderRow({ label, sub, value, onCommit, min, max, step, unit }: {
  label: string; sub?: ReactNode; value: number; onCommit: (v: number) => void;
  min: number; max: number; step: number; unit?: string;
}) {
  // a $ slider's thumb position IS the number: the shared AmountRange hides it while privacy mode is on
  const cls = "w-full max-w-40 accent-blue-500";
  return (
    <label className="flex items-center justify-between gap-2 py-1 text-xs">
      <span className="shrink-0 text-zinc-400">
        {label}
        {sub && <span className="ml-1.5 text-faint">{sub}</span>}
      </span>
      <span className="flex grow items-center justify-end gap-2">
        {unit === "$"
          ? <AmountRange value={value} min={min} max={max} step={step} onCommit={onCommit} className={cls} />
          : <input type="range" min={min} max={max} step={step} value={value} className={cls}
              onChange={(e) => onCommit(Number(e.target.value))} />}
        <NumberInput value={value} onCommit={onCommit} min={min} max={max} step={step} unit={unit} />
      </span>
    </label>
  );
}

/** chip list + one text box: Enter or comma adds the draft, Backspace on an empty box removes the
 *  last chip, suggestions come through a datalist. Tags are lowercase words in this data, so a draft
 *  is lowercased, and a pasted "a,b" becomes two tags. Phone-sized text (text-base) like every input. */
export function TagInput({ id, value, onChange, suggestions }: {
  id: string; value: string[]; onChange: (tags: string[]) => void; suggestions: readonly string[];
}) {
  const [draft, setDraft] = useState("");
  const add = (raw: string) => {
    setDraft("");
    const fresh = raw.split(",").map((t) => t.trim().toLowerCase()).filter((t) => t && !value.includes(t));
    if (fresh.length) onChange([...new Set([...value, ...fresh])].sort());
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {value.map((t) => (
        <Chip key={t} removeLabel={`Remove tag ${t}`} onRemove={() => onChange(value.filter((x) => x !== t))}>{t}</Chip>
      ))}
      <input id={id} list={`${id}-list`} value={draft} placeholder="add tag…"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(draft); }
          else if (e.key === "Backspace" && !draft && value.length) onChange(value.slice(0, -1));
        }}
        onBlur={() => add(draft)}
        className="min-h-[40px] min-w-28 grow rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1 text-base text-zinc-200 placeholder:text-faint sm:py-0.5 sm:text-xs md:min-h-0" />
      <datalist id={`${id}-list`}>{suggestions.map((s) => <option key={s} value={s} />)}</datalist>
    </div>
  );
}

/** A labelled native <select> over a closed list of string values (a report's group-by, interval, measure): the
 *  label sits above it, the box is 40px tall below md and 16px text below sm (no iOS focus-zoom), like every input here. The value is
 *  also its text. */
export function Pick<T extends string>({ label, value, options, optionLabels, onChange }: {
  label: string; value: T; options: readonly T[]; onChange: (v: T) => void;
  /** the words an option is shown as, when its value is a token (reportSpec's label maps); default: the value itself */
  optionLabels?: Readonly<Record<T, string>>;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-0.5">
      <label htmlFor={id} className="text-[10px] text-zinc-500">{label}</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value as T)}
        className="min-h-[40px] rounded-md border border-zinc-700 bg-zinc-900 px-1.5 text-base text-zinc-200 sm:text-xs md:min-h-0 md:py-0.5">
        {options.map((o) => <option key={o} value={o}>{optionLabels?.[o] ?? o}</option>)}
      </select>
    </div>
  );
}

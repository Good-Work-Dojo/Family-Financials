// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useEffect, useRef, useState } from "react";

/** Searchable multi-select dropdown used by the FilterBar facets. */
export default function MultiSelect({ label, options, selected, onChange }: {
  label: string;
  options: { value: string; label?: string }[];
  selected: string[];
  onChange: (v: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const toggle = (v: string) =>
    onChange(selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v]);
  const shown = options.filter((o) =>
    (o.label ?? o.value).toLowerCase().includes(q.toLowerCase()));

  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(!open)}
        className={`min-h-[36px] rounded-lg border px-2.5 py-1.5 text-xs sm:min-h-0 ${selected.length
          ? "border-blue-700 bg-blue-950/40 text-blue-200"
          : "border-zinc-700 text-zinc-400 hover:text-zinc-200"}`}>
        {label}{selected.length ? ` · ${selected.length}` : ""} ▾
      </button>
      {open && (
        <div className="absolute z-30 mt-1 max-h-72 w-[min(15rem,calc(100vw-1.5rem))] overflow-auto rounded-lg border border-zinc-700 bg-zinc-900 p-1.5 shadow-xl">
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="search…"
            className="mb-1 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-base sm:text-xs" />
          {selected.length > 0 && (
            <button onClick={() => onChange([])}
              className="mb-1 w-full rounded px-2 py-1 text-left text-xs text-red-400 hover:bg-zinc-800">
              clear ({selected.length})
            </button>
          )}
          {shown.slice(0, 200).map((o) => (
            <label key={o.value}
              className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-800">
              <input type="checkbox" checked={selected.includes(o.value)}
                onChange={() => toggle(o.value)} className="accent-blue-500" />
              <span className="truncate">{o.label ?? o.value}</span>
            </label>
          ))}
          {shown.length > 200 && (
            <div className="px-2 py-1 text-[10px] text-zinc-500">
              {shown.length - 200} more — refine the search
            </div>
          )}
        </div>
      )}
    </div>
  );
}

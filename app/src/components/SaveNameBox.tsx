// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useEffect, useState } from "react";
import { MAX_SAVED_NAME, type SavedRefusal } from "../lib/prefs";
import { saveRefusalText } from "../lib/savedFilters";
import { usePointerWords } from "../lib/pointerWords";

/** The one "★ Save …" box (saved views on the FilterBar, saved reports on the Reports tab): a link-styled button opens an
 *  inline form - a name, Save, cancel (Escape also cancels) - and a save closes it with "saved ✓" for two seconds.
 *
 *  It says why a save would be refused BEFORE the click: `refusalFor` is the prefs ops' own verdict on saving under this name
 *  right now (the list is at its cap, the item is too long, or the whole prefs doc is full - lib/prefs tryWithSaved), worded by
 *  `saveRefusalText`, the one place those words live. `onSave` returns false when the store still refuses after the fact (the
 *  list changed under us): the box stays open and says so rather than claim a save. The two Save boxes used to differ - one
 *  always showed its name field, one opened on a click - and each had its own copy; now there is one. */
export default function SaveNameBox({ kind, buttonLabel, buttonTestId, nameLabel, placeholder, refusalFor, onSave }: {
  kind: "view" | "report";
  /** the button that opens the box: "★ Save view" */
  buttonLabel: string;
  buttonTestId?: string;
  /** the name field's accessible name: "View name" */
  nameLabel: string;
  placeholder: string;
  /** the verdict on saving under `name` now (null: it would save); called with the typed name, or "" before one is typed */
  refusalFor: (name: string) => SavedRefusal | null;
  /** save under `name`; false = refused after the fact */
  onSave: (name: string) => boolean;
}) {
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [justSaved, setJustSaved] = useState(false);
  const [refused, setRefused] = useState(false);
  // a phone has no ⌘K: its way into the palette is the search button
  const searchKey = usePointerWords().search;
  useEffect(() => {
    if (!justSaved) return;
    const t = setTimeout(() => setJustSaved(false), 2000);
    return () => clearTimeout(t);
  }, [justSaved]);
  const stop = () => { setNaming(false); setName(""); setRefused(false); };
  const refusal = naming ? refusalFor(name) : null;
  const refusalText = saveRefusalText(refusal, kind);
  return (
    <>
      {!naming && (
        <button type="button" data-testid={buttonTestId} onClick={() => setNaming(true)}
          className="inline-flex min-h-[40px] items-center text-blue-400 hover:underline md:min-h-0">{buttonLabel}</button>
      )}
      {naming && (
        <form className="flex flex-wrap items-center gap-1" onSubmit={(e) => {
          e.preventDefault();
          if (onSave(name)) { stop(); setJustSaved(true); } else setRefused(true);
        }}>
          <input autoFocus aria-label={nameLabel} value={name} maxLength={MAX_SAVED_NAME} placeholder={placeholder}
            onChange={(e) => { setName(e.target.value); setRefused(false); }}
            onKeyDown={(e) => { if (e.key === "Escape") stop(); }}
            className="w-40 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-base sm:text-xs" />
          <button type="submit" disabled={!name.trim() || refusal !== null}
            className="min-h-[40px] rounded-md border border-zinc-700 px-2 text-zinc-200 disabled:opacity-40 md:min-h-0 md:py-0.5">Save</button>
          <button type="button" onClick={stop} className="min-h-[40px] text-zinc-500 md:min-h-0">cancel</button>
          {refusalText && <span role="alert" className="text-amber-300">{refusalText}</span>}
          {refused && refusal === null && (
            <span role="alert" className="text-amber-300">couldn't save this {kind} — the list changed, try again</span>
          )}
        </form>
      )}
      {justSaved && <span role="status" className="text-emerald-400">saved ✓ ({searchKey} and Data)</span>}
    </>
  );
}

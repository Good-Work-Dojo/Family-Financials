// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { Card } from "../../components/ui";
import { SCROLL_TARGETS } from "../../lib/scrollTargets";
import { usePointerWords } from "../../lib/pointerWords";
import { usePrivacy } from "../../lib/usePrivacy";

/** Data → Privacy mode: the same toggle as the sidebar's, with the rules written out. The mask itself lives in the
 *  four money formatters (lib/format.ts), so this card only flips the per-device flag (lib/privacy.ts). Settings links
 *  here (`?card=`, lib/scrollTargets.ts). */
export default function PrivacyCard() {
  const [privacy, setPrivacy] = usePrivacy();
  // a finger has no keyboard shortcut: it is told about the Hide amounts button instead (the menu's, and the eye of a
  // phone's top bar), lib/pointerWords.ts
  const { touch } = usePointerWords();
  return (
    <Card testId="card-privacy" id={SCROLL_TARGETS.privacy} title="Privacy mode">
      <p className="text-xs text-zinc-400">
        Hides every dollar amount on screen — KPIs, tables, chart axes and tooltips, inputs at rest (an input shows its
        number while you edit it). Names, dates, counts and percentages stay. Per device, like the theme;{" "}
        {touch ? "“Hide amounts” (the eye at the top of a phone) toggles it anywhere." : (
          <><kbd className="rounded border border-zinc-700 px-1 text-[10px] text-zinc-300">Alt+Shift+P</kbd> toggles it anywhere.</>
        )}{" "}
        <b className="font-semibold text-zinc-200">CSV exports are never masked</b> — they are for your own spreadsheet.
      </p>
      <button type="button" onClick={() => setPrivacy(!privacy)} aria-pressed={privacy} data-testid="privacy-toggle"
        className={`mt-3 min-h-[40px] rounded-md border px-3 text-sm md:min-h-0 md:py-1.5 ${privacy
          ? "border-blue-500/60 bg-blue-500/15 text-blue-200" : "border-zinc-700 text-zinc-300 hover:bg-zinc-800"}`}>
        <span aria-hidden="true" className="mr-2 text-xs">{privacy ? "◉" : "◌"}</span>{privacy ? "Show amounts" : "Hide amounts"}
      </button>
    </Card>
  );
}

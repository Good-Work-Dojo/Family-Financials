// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useId, useRef } from "react";
import { Code } from "../../components/NoData";
import type { AiStatus } from "../../data/types";
import { recipientName, withheldLines } from "../../lib/aiCard";
import { countOf } from "../../lib/format";
import { useFocusTrap } from "../../lib/useFocusTrap";

// What AI categorization sends, said once and shown twice: on the card and in the dialog that asks for the family's
// consent. Counts only (ai_status.json holds no vendor): the exact list is printed by the preview command on the
// computer that runs the dashboard, named here as the one action in the code pill.

/** the one paragraph of what is sent and what never is */
export function WhatIsSentParagraph() {
  return (
    <p data-testid="ai-what-is-sent" className="text-sm text-zinc-300">
      What is sent: the names of vendors your rules have not sorted, as the bank shows them after cleanup, and the names
      of your spending categories and subcategories. Never an amount, a date, an account or a note, never a payment
      made through a payment app, and never a vendor name that holds a name from your household or a word on your
      never-send list{" "}
      <span className="text-zinc-500">(<Code>ai.never_send</Code> in <Code>household.yaml</Code>)</span>. A vendor
      name can include the shop's town and, for a person paid through a card reader (a tutor who takes cards), that
      person's name: add such names to the never-send list, and look at the list on the computer that runs the
      dashboard before turning it on:{" "}<span className="whitespace-nowrap"><Code>ff ai preview</Code></span>
    </p>
  );
}

/** the preview's counts, the provider and model, the version of what is sent, the caps, and how to see the list */
export function WhatWouldBeSent({ status }: { status: AiStatus }) {
  const kept = withheldLines(status.preview.withheld);
  return (
    <div data-testid="ai-preview" className="space-y-2 text-sm text-zinc-300">
      <p>
        {status.preview.vendors === 0 ? "No vendor name would be sent now."
          : `${countOf(status.preview.vendors, "vendor name")} would be sent now.`}
        {kept.length === 0 ? " None is kept back." : " Kept back, and never sent:"}
      </p>
      {kept.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-zinc-400">
          {kept.map((line) => <li key={line}>{line}</li>)}
        </ul>
      )}
      <p className="text-xs text-zinc-400">
        Sent to {recipientName(status)}{status.model ? ` (${status.model})` : ""}; this is version{" "}
        {status.policy_version} of what is sent.
        {status.caps.vendors_per_run > 0 && ` Each run asks about at most ${countOf(status.caps.vendors_per_run, "vendor")}, `
          + `at most ${countOf(status.caps.runs_per_day, "run")} a day.`}
      </p>
      <p className="text-xs text-faint">
        The exact list of names, categories and subcategories is printed on the computer that runs the dashboard:{" "}
        <span className="whitespace-nowrap"><Code>ff ai preview</Code></span>
      </p>
    </div>
  );
}

const BTN = "min-h-[40px] rounded-md border px-3 text-sm md:min-h-0 md:py-1";

/** The consent dialog: the paragraph and the counts again, then "Turn on" (the family agrees to this version of what
 *  is sent) or "Not now". Without `onTurnOn` it only shows what would be sent (no key is stored yet, or AI is on
 *  already), with `note` saying why, and one Close button. A real modal (lib/useFocusTrap.ts): the dialog box takes the
 *  first focus, so Enter never turns anything on; Escape and a click outside close it. */
export default function AiConsentDialog({ status, onTurnOn, onClose, note }: {
  status: AiStatus; onTurnOn?: () => void; onClose: () => void; note?: string;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useFocusTrap(panel, onClose, { initialFocus: "container" });
  return (
    <div className="fixed inset-0 z-[60] flex items-end justify-center bg-black/60 p-3 print:hidden sm:items-center"
      onClick={onClose}>
      <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}
        data-testid="ai-consent-dialog" onClick={(e) => e.stopPropagation()}
        className="max-h-[90vh] w-full max-w-lg space-y-3 overflow-y-auto rounded-xl border border-zinc-700 bg-zinc-900 p-4 shadow-2xl outline-none">
        <h2 id={titleId} className="text-sm font-semibold text-zinc-100">What AI categorization sends</h2>
        <WhatIsSentParagraph />
        <WhatWouldBeSent status={status} />
        {onTurnOn ? (
          <>
            <p className="text-sm text-zinc-300">
              Turning it on agrees to this for the whole family. Every row it files is marked AI, and it can be turned off
              at any time.
            </p>
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" onClick={onClose} className={`${BTN} border-zinc-700 text-zinc-300 hover:bg-zinc-800`}>
                Not now
              </button>
              <button type="button" onClick={onTurnOn} data-testid="ai-turn-on"
                className={`${BTN} border-emerald-700 bg-emerald-950 text-emerald-300 hover:bg-emerald-900`}>
                Turn on
              </button>
            </div>
          </>
        ) : (
          <>
            {note && <p className="text-sm text-zinc-300">{note}</p>}
            <div className="flex justify-end">
              <button type="button" onClick={onClose} className={`${BTN} border-zinc-700 text-zinc-300 hover:bg-zinc-800`}>
                Close
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

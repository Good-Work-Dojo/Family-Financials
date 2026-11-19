// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { VendorEntry } from "../data/types";
import { activeLearnedRules } from "../lib/learnedRules";
import SourceChip from "./SourceChip";

/** Data page's learned-rules card: the vendor rules the ETL learned from the family's confirmed
 *  answers (vendors.json rows with category_source "learned"). The card's own title is the heading. */
export default function LearnedRulesList({ vendors }: { vendors: readonly VendorEntry[] }) {
  const rules = activeLearnedRules(vendors);
  return (
    <div data-testid="learned-rules" className="text-zinc-400">
      {rules.length === 0 ? (
        <>None yet — a vendor is learned once 3 of its last 5 review answers agree, or after a vendor-scope answer.</>
      ) : (
        <>
          {rules.length} vendor{rules.length === 1 ? "" : "s"} categorized from your confirmed answers
          (shown as <SourceChip source="learned" /> in the transactions table).
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {rules.map((v) => (
              <li key={v.vendor}>
                <span className="text-zinc-200">{v.vendor}</span> → {v.category}{v.subcategory ? ` / ${v.subcategory}` : ""}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

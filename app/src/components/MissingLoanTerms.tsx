// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { MissingLoanTerm } from "../lib/lifeSummary";
import { Code } from "./NoData";

/** one sentence per loan term a mortgage figure needs and the account's `loan:` block lacks (lib/lifeSummary
 *  missingLoanTerms), naming the key to add: Accounts → Liabilities' mortgage card and Planning → House word it alike */
export default function MissingLoanTerms({ terms }: { terms: MissingLoanTerm[] }) {
  if (terms.length === 0) return null;
  return (
    <ul data-testid="mortgage-missing-terms" className="mt-2 space-y-0.5 text-[10px] text-faint">
      {terms.map((t) => (
        <li key={t.key}>
          Add <Code>{t.key}</Code> under <Code>loan:</Code> on this account in <Code>accounts.yaml</Code> to see {t.shows}.
        </li>
      ))}
    </ul>
  );
}

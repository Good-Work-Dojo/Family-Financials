// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { cardPath, SCROLL_TARGETS } from "../lib/scrollTargets";
import { ACCOUNT_LINK } from "./AccountLink";
import { Code } from "./NoData";

// Where a value no account reports is entered: Settings' Manual assets card (sections/settings/ManualAssetsCard.tsx),
// which a page that needs one links to, landing on the card (`?card=`, lib/scrollTargets.ts). Settings is never
// switched off and never replaced by the first-run page, so the link always leads somewhere.

/** the in-app path that opens Settings on its Manual assets card */
export const MANUAL_ASSETS_PATH = cardPath("/settings", SCROLL_TARGETS.manualAssets);

/** a link to Settings' Manual assets card, worded as the place it opens unless `children` says otherwise */
export function ManualAssetsLink({ children = "Settings → Manual assets", className = "" }: {
  children?: ReactNode; className?: string;
}) {
  return <Link to={MANUAL_ASSETS_PATH} className={`${ACCOUNT_LINK} ${className}`.trim()}>{children}</Link>;
}

/** What a page says where equity and loan-to-value would be when the snapshot holds no House-class row
 *  (lib/lifeSummary.ts homeValue null): where to enter the home, with the config key as a hint — the one sentence, on
 *  Accounts -> Liabilities and Planning -> House alike. */
export function NoHomeValue({ className = "" }: { className?: string }) {
  return (
    <p data-testid="no-home-value" className={className}>
      Enter your home in <ManualAssetsLink />, in the House class, to see equity and loan-to-value{" "}
      <span className="text-faint">(or <Code>manual_entries:</Code> in <Code>accounts.yaml</Code>)</span>.
    </p>
  );
}

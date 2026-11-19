// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Code } from "../../components/NoData";
import { Card } from "../../components/ui";
import { useData } from "../../data/DataContext";
import { recipientName } from "../../lib/aiCard";
import { BUILD_ID, knownBuild } from "../../lib/buildId";
import { fmtDateTime } from "../../lib/format";
import { PRODUCT_NAME, SOURCE_URL } from "../../lib/product";
import { cardPath, SCROLL_TARGETS } from "../../lib/scrollTargets";

// Settings → About: what this tab is running - the product, the build its bundle came from (lib/buildId.ts), the
// export's schema version and when the data was last built (manifest.json `generated_at`, the ingest instant) - and the
// promise the offline design keeps: while the last ingest found AI categorization on, the one thing that leaves, with
// a link to its card. It also says whose the program is, its licence and where its source is (NOTICE's attribution
// terms ask for the copyright line here and on the sign-in page, deploy/public/login.html, which says the same).

/** the copyright line NOTICE asks the About screen to keep */
export const COPYRIGHT = "\u00a9 2026 Jeremy Sapriel";
export const LICENCE_LINE = "Free software under the GNU AGPL 3.0, with no warranty";

/** an address as a person reads it: without its scheme */
const shownUrl = (url: string) => url.replace(/^https?:\/\//, "");

/** one line of the list: what, then its value */
function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 py-1">
      <dt className="text-zinc-500">{k}</dt>
      <dd className="min-w-0 break-words text-right text-zinc-200">{children}</dd>
    </div>
  );
}

export default function AboutCard({ build = BUILD_ID }: { build?: string }) {
  const { data } = useData();
  const id = knownBuild(build);
  return (
    <Card testId="card-about" title="About">
      <dl className="text-sm">
        <Row k="Product">{PRODUCT_NAME}</Row>
        <Row k="Build">{id === null ? "development build" : <Code>{id}</Code>}</Row>
        <Row k="Export schema">version {data.manifest.schema_version}</Row>
        <Row k="Data last built">{fmtDateTime(data.manifest.generated_at, data.assumptions.household.timezone)}</Row>
        <Row k="Copyright">{COPYRIGHT}</Row>
        <Row k="Licence">{LICENCE_LINE}</Row>
        <Row k="Source code">
          <a href={SOURCE_URL} target="_blank" rel="noopener noreferrer" className="underline hover:text-zinc-100">{shownUrl(SOURCE_URL)}</a>
        </Row>
      </dl>
      {data.aiStatus.enabled ? (
        <p data-testid="about-promise" className="mt-3 text-xs text-zinc-400">
          The dashboard makes no outside request, but{" "}
          <Link to={cardPath("/settings", SCROLL_TARGETS.aiCategorization)} className="underline hover:text-zinc-200">
            AI categorization
          </Link>{" "}
          is on: the names of vendors your rules have not sorted{" "}
          {data.aiStatus.configured ? "go" : "will go"} to {recipientName(data.aiStatus)} for sorting
          {data.aiStatus.configured ? "" : " once a key is stored"}. Nothing else is sent.
        </p>
      ) : (
        <p data-testid="about-promise" className="mt-3 text-xs text-zinc-400">
          The dashboard makes no outside request: your data goes nowhere else.
        </p>
      )}
    </Card>
  );
}

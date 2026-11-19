// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { ReactNode } from "react";
import { useData } from "../data/DataContext";
import type { ExportFile } from "../data/types";

/** What each export holds, in the words a page uses for it: a person is told what could not be loaded, never the file's
 *  name (a household acts on the dashboard's words, not on its files). */
export const EXPORT_WORDS: Record<ExportFile, string> = {
  "manifest.json": "the summary of the last ingest",
  "transactions.json": "the transactions",
  "accounts.json": "the account list",
  "assets_timeseries.json": "the net-worth snapshots",
  "holdings.json": "the investment holdings",
  "cashflow_monthly.json": "the monthly cash flow",
  "recurring.json": "the recurring charges",
  "quality_report.json": "the quality report",
  "open_questions.json": "the open questions",
  "assumptions.json": "the assumptions",
  "balances_timeseries.json": "the balance history",
  "networth_daily.json": "the daily net-worth line",
  "vendors.json": "the vendor list",
  "ai_status.json": "the automatic categorization status",
};

const andList = (xs: readonly string[]): string =>
  (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

/** Shown where a card's export failed to load: no fabricated zeros, no blank space. It says what could not be loaded in
 *  words (EXPORT_WORDS) and what to do: look at the quality report on Data, re-run the ingest, reload. */
export function MissingDataCard({ files, what, compact }: {
  files: readonly ExportFile[]; what?: string; compact?: boolean;
}) {
  // the quality report says why an ingest failed, unless the quality report is itself what did not load: then the card
  // sends the reader to the Data page alone
  const noReport = files.includes("quality_report.json");
  const body = (
    <>
      <span className="font-medium text-amber-300">{what ?? "This view"} is unavailable</span>
      {" — "}{andList(files.map((f) => EXPORT_WORDS[f]))} could not be loaded. An ingest may still be writing{" "}
      {files.length === 1 ? "it" : "them"}, or the last ingest failed:{noReport ? " check the " : " see the quality report on the "}
      <a href="#/data" className="underline hover:text-amber-200">Data page</a>, re-run the ingest if it failed, and reload.
    </>
  );
  if (compact) {
    return <p role="status" data-testid="missing-data" className="mt-1 text-[11px] text-amber-300/90">{body}</p>;
  }
  return (
    <div role="status" data-testid="missing-data"
      className="rounded-xl border border-amber-900/60 bg-amber-950/20 p-4 text-xs leading-relaxed text-zinc-300">
      {body}
    </div>
  );
}

/** Render children only when every listed export loaded; otherwise the card above. */
export default function RequireData({ files, what, compact, children }: {
  files: readonly ExportFile[]; what?: string; compact?: boolean; children: ReactNode;
}) {
  const { missing } = useData();
  const absent = files.filter((f) => missing.has(f));
  if (absent.length) return <MissingDataCard files={absent} what={what} compact={compact} />;
  return <>{children}</>;
}

// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// What the last ingest read, file by file - Data's "What the last ingest read" card. A household whose statement files
// yielded no rows learns here which file read nothing and why (its lines were skipped as headers, totals, unreadable
// rows ...), or that no file was found for an account at all. Read from the quality report's per-file counts
// (`files`), its repeated-row counts (`cross_file_duplicate_uids`) and its "no file found" checks (`source.<account>`).
import type { QualityReport } from "../data/types";
import { countOf } from "./format";

/** One line of the card: a statement file, the bank feed (all its pulls as one source), or an account the ingest found
 *  no statement file for. */
export interface IngestSource {
  kind: "file" | "feed" | "no-file";
  /** the file's own name; the account's name for "no-file" (its id when the registry has no name for it); null for the
   *  bank feed */
  name: string | null;
  /** for "no-file", the account's id when the registry has it (its name links its page); null otherwise */
  account: string | null;
  /** the bank feed's pulls (1 for a file, 0 for an account with no file) */
  pulls: number;
  /** the rows the source yielded, and what it passed over - a statement file's lines, the bank feed's rows - each
   *  reason in words (most first) */
  read: number;
  skipped: number;
  reasons: string[];
  /** rows an earlier file of the same account already held, stored once (a re-export) */
  repeats: number;
  /** "read": it yielded rows; "nothing read": it had lines and none was a row; "empty": it had no line at all; "no file":
   *  the account's pattern matched no file */
  result: "read" | "nothing read" | "empty" | "no file";
  /** for "no-file", the file pattern the account looks for (its `glob:` in accounts.yaml), when the check names it */
  hint: string | null;
}

/** A statement parser's reasons for skipping a line of its file, in words: [one, many]. */
const FILE_SKIPS: Record<string, [string, string]> = {
  "preamble": ["preamble line", "preamble lines"],
  "header": ["header line", "header lines"],
  "repeated-header": ["repeated header", "repeated headers"],
  "blank-or-repeated-header": ["blank or repeated header", "blank or repeated headers"],
  "blank-line": ["blank line", "blank lines"],
  "summary-row": ["summary line", "summary lines"],
  "pseudo-row-no-amount": ["line with no amount", "lines with no amount"],
  "no-amount": ["line with no amount", "lines with no amount"],
  "no-date": ["line with no date", "lines with no date"],
  "short-row": ["short line", "short lines"],
  "unparseable-row": ["unreadable line", "unreadable lines"],
};
/** The bank feed's reasons for skipping a row a pull reported, in words: [one, many]. */
const FEED_SKIPS: Record<string, [string, string]> = {
  "pending": ["pending charge", "pending charges"],
  "before-cutover": ["row before the bank feed takes over", "rows before the bank feed takes over"],
  "no-amount": ["row with no amount", "rows with no amount"],
  "no-date": ["row with no date", "rows with no date"],
};

/** `n` skipped for `reason`, in words: a statement file skips lines ("4 summary lines"), the bank feed (`feed`) rows
 *  ("3 pending charges"). A reason not listed reads as its own words. */
export function skipWords(reason: string, n: number, feed = false): string {
  const words = (feed ? FEED_SKIPS : FILE_SKIPS)[reason];
  return words ? countOf(n, words[0], words[1]) : `${n.toLocaleString("en-US")} ${reason.replace(/[-_]+/g, " ")}`;
}

const FEED_PARSER = "simplefin";

/** the reasons of one or more files (the bank feed's pulls: `feed`), summed, most first (a tie keeps the order the
 *  files gave them in) */
function reasonsOf(files: readonly QualityReport["files"][number][], feed = false): string[] {
  const sums = new Map<string, number>();
  for (const f of files) for (const [r, n] of Object.entries(f.skip_reasons)) sums.set(r, (sums.get(r) ?? 0) + n);
  return [...sums].filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]).map(([r, n]) => skipWords(r, n, feed));
}

const resultOf = (total: number, read: number): IngestSource["result"] =>
  read > 0 ? "read" : total > 0 ? "nothing read" : "empty";

/** Every source the last ingest read: the accounts no statement file was found for first, then the files and the feed
 *  that read nothing, then the bank feed and the files that read rows, each group in the report's order. The bank
 *  feed's pulls overlap (each repeats days the one before it read), so its rows read count a transaction once per pull
 *  that carried it. */
export function ingestSources(q: QualityReport, accounts: readonly { id: string; name: string }[]): IngestSource[] {
  const names = new Map(accounts.map((a) => [a.id, a.name]));
  const noFile: IngestSource[] = q.checks
    .filter((c) => c.name.startsWith("source.") && c.status !== "pass" && c.status !== "info")
    .map((c) => {
      const id = c.name.slice("source.".length);
      const pattern = /glob (['"])(.*)\1/.exec(c.detail);
      return { kind: "no-file", name: names.get(id) ?? id, account: names.has(id) ? id : null, pulls: 0, read: 0,
        skipped: 0, reasons: [], repeats: 0, result: "no file", hint: pattern ? pattern[2] : null };
    });
  const files: IngestSource[] = q.files.filter((f) => f.parser !== FEED_PARSER).map((f) => ({
    kind: "file", name: f.source_file, account: null, pulls: 1, read: f.rows_parsed, skipped: f.rows_skipped,
    reasons: reasonsOf([f]),
    repeats: q.cross_file_duplicate_uids[f.source_file] ?? 0, result: resultOf(f.rows_total, f.rows_parsed), hint: null,
  }));
  const pulls = q.files.filter((f) => f.parser === FEED_PARSER);
  const feed: IngestSource[] = pulls.length === 0 ? [] : [{
    kind: "feed", name: null, account: null, pulls: pulls.length,
    read: pulls.reduce((s, f) => s + f.rows_parsed, 0), skipped: pulls.reduce((s, f) => s + f.rows_skipped, 0),
    reasons: reasonsOf(pulls, true), repeats: 0, hint: null,
    result: resultOf(pulls.reduce((s, f) => s + f.rows_total, 0), pulls.reduce((s, f) => s + f.rows_parsed, 0)),
  }];
  const quiet = (s: IngestSource) => s.result !== "read";
  return [...noFile, ...files.filter(quiet), ...feed.filter(quiet), ...feed.filter((s) => !quiet(s)),
    ...files.filter((s) => !quiet(s))];
}

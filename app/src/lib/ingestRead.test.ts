// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { QualityReport } from "../data/types";
import { ingestSources, skipWords } from "./ingestRead";

type FileStat = QualityReport["files"][number];
const file = (over: Partial<FileStat>): FileStat => ({
  source_file: "f.csv", parser: "chase_checking", account: null, rows_total: 10, rows_parsed: 10, rows_skipped: 0, skip_reasons: {},
  duplicates_removed: 0, date_min: "2030-01-01", date_max: "2030-01-31", parsed_sum: 0, ...over,
});
const report = (files: FileStat[], over: Partial<QualityReport> = {}): QualityReport => ({
  files, checks: [], account_month_sums: {}, top_unmapped_vendors: [], cross_file_duplicate_uids: {}, ...over,
});
const accounts = [{ id: "sample_checking", name: "Sample Checking" }, { id: "sample_card", name: "Sample Card" }];

describe("ingestSources: what the last ingest read, file by file", () => {
  it("each statement file by its own name, with the rows it yielded, the lines it skipped and why, and the rows another file already held", () => {
    const out = ingestSources(report([
      file({ source_file: "checking-2030-01.csv", rows_total: 76, rows_parsed: 76 }),
      file({ source_file: "savings-2030-01.csv", parser: "bofa_bank", rows_total: 10, rows_parsed: 2, rows_skipped: 8,
        skip_reasons: { preamble: 1, "summary-row": 4, "blank-line": 1, header: 1, "pseudo-row-no-amount": 1 } }),
    ], { cross_file_duplicate_uids: { "checking-2030-01.csv": 22 } }), accounts);
    expect(out).toEqual([
      { kind: "file", name: "checking-2030-01.csv", account: null, pulls: 1, read: 76, skipped: 0, reasons: [], repeats: 22,
        result: "read", hint: null },
      { kind: "file", name: "savings-2030-01.csv", account: null, pulls: 1, read: 2, skipped: 8, repeats: 0, result: "read", hint: null,
        reasons: ["4 summary lines", "1 preamble line", "1 blank line", "1 header line", "1 line with no amount"] },
    ]);
  });

  it("the bank feed is one source: its pulls counted, their rows and skips summed (pulls overlap)", () => {
    const out = ingestSources(report([
      file({ source_file: "accounts-20300131T210000Z.json", parser: "simplefin", rows_total: 66, rows_parsed: 48, rows_skipped: 18,
        skip_reasons: { "before-cutover": 18 } }),
      file({ source_file: "accounts-20300228T210000Z.json", parser: "simplefin", rows_total: 75, rows_parsed: 75 }),
    ]), accounts);
    expect(out).toEqual([{ kind: "feed", name: null, account: null, pulls: 2, read: 123, skipped: 18, repeats: 0, result: "read",
      hint: null, reasons: ["18 rows before the bank feed takes over"] }]);
  });

  it("what read nothing leads: an account no file was found for, a file with no row at all, a file whose every line was skipped", () => {
    const out = ingestSources(report([
      file({ source_file: "good.csv" }),
      file({ source_file: "empty.csv", rows_total: 0, rows_parsed: 0 }),
      file({ source_file: "odd.csv", rows_total: 3, rows_parsed: 0, rows_skipped: 3, skip_reasons: { "unparseable-row": 3 } }),
      file({ source_file: "accounts-20300131T210000Z.json", parser: "simplefin", rows_total: 0, rows_parsed: 0 }),
    ], { checks: [
      { name: "source.sample_card", status: "warn", detail: "no file matched glob 'Card*.csv'" },
      { name: "source.sample_checking", status: "pass", detail: "ok" },
    ] }), accounts);
    expect(out.map((s) => [s.kind, s.name, s.result])).toEqual([
      ["no-file", "Sample Card", "no file"],
      ["file", "empty.csv", "empty"],
      ["file", "odd.csv", "nothing read"],
      ["feed", null, "empty"],
      ["file", "good.csv", "read"],
    ]);
    expect(out[0].hint, "the pattern the account looks for, as a hint").toBe("Card*.csv");
    expect(out[0].account, "its page is linked").toBe("sample_card");
    expect(out[2].reasons).toEqual(["3 unreadable lines"]);
  });

  it("an account id with no registry name is named by its id; a detail with no pattern gives no hint", () => {
    const out = ingestSources(report([], { checks: [{ name: "source.gone", status: "warn", detail: "no file" }] }), accounts);
    expect(out).toEqual([{ kind: "no-file", name: "gone", account: null, pulls: 0, read: 0, skipped: 0, reasons: [], repeats: 0,
      result: "no file", hint: null }]);
  });

  it("nothing read at all: no source", () => {
    expect(ingestSources(report([]), accounts)).toEqual([]);
  });
});

describe("skipWords: a parser's reason for skipping, in words", () => {
  it("a statement file skips lines, the bank feed rows; singular and plural; an unknown reason in its own words, never a code", () => {
    expect(skipWords("blank-line", 1)).toBe("1 blank line");
    expect(skipWords("blank-line", 2)).toBe("2 blank lines");
    expect(skipWords("no-amount", 2), "a file's line").toBe("2 lines with no amount");
    expect(skipWords("no-amount", 2, true), "a feed's row").toBe("2 rows with no amount");
    expect(skipWords("pending", 3, true)).toBe("3 pending charges");
    expect(skipWords("brand-new-reason", 1_234)).toBe("1,234 brand new reason");
  });
});

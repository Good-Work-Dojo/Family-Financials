// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
/** RFC 4180 CSV export of the currently filtered+sorted view. No library. */

/** What a cell is, by its type: a number (an amount, a count, a rate) is written as a number; anything else is text. */
export type CsvCell = string | number | null | undefined;

/** the characters a spreadsheet reads as the start of a formula, or passes over before one: a text cell that begins with
 *  one could run as a formula when the file is opened (a description reading `=HYPERLINK(…)`) */
const FORMULA_START = /^[=+\-@\t\r]/;

/** One cell as written: a number as it is (so `-12.5` stays a number a spreadsheet can sum); text that begins like a
 *  formula behind an apostrophe, which spreadsheets take as "this is text" and do not show; then the RFC 4180 quoting of
 *  a cell that holds a quote, a comma or a line break, and of one that holds a semicolon, the list separator in many
 *  locales, which would otherwise split `x;=HYPERLINK(…)` into a second cell that starts like a formula. */
function cell(v: CsvCell): string {
  if (v === null || v === undefined) return "";
  const s = typeof v === "number" ? String(v) : FORMULA_START.test(v) ? `'${v}` : v;
  return /[",;\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(rows: CsvCell[][], header: string[]): string {
  const lines = [header.map(cell).join(",")];
  for (const r of rows) lines.push(r.map(cell).join(","));
  return lines.join("\r\n") + "\r\n";
}

export function downloadCsv(filename: string, csv: string): void {
  // BOM so Excel opens UTF-8 correctly
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

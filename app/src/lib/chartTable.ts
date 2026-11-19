// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
/** The data behind a chart, for the "view data" footer (components/EChart.tsx): numbers render through
 *  `format` (default fmtUSD0, so the privacy mask applies), strings as they are, null as a dash. The CSV
 *  download takes the raw rows, so a gap is an empty field and a number is unformatted. Build one inside
 *  the memo that builds the chart's option, from the same arrays, so the table can never disagree with the chart. */
export interface ChartTable {
  columns: string[];
  rows: (string | number | null)[][];
  csvName?: string;
  /** how a number cell shows; it also gets the column index, so a ratio column can say "12.3%" next to money
   *  columns (the CSV keeps the raw number either way) */
  format?: (v: number, column: number) => string;
  /** names the chart in the toggle's accessible name ("View data: <label>") so several charts on a page are
   *  told apart; the visible text stays "view data" */
  label?: string;
  /** what the chart draws that the table does not list (a dense daily line, say) - shown under the table */
  note?: string;
}

/** one row per category, one column per series; a series shorter than the categories (or holding
 *  null/undefined) leaves a gap, which is null, never 0 */
export function categoryTable(firstColumn: string, categories: readonly string[],
                              series: readonly { name: string; data: readonly (number | null | undefined)[] }[]): ChartTable {
  return {
    columns: [firstColumn, ...series.map((s) => s.name)],
    rows: categories.map((c, i) => [c, ...series.map((s) => s.data[i] ?? null)]),
  };
}

/** the union of every series' dates, sorted, each series looked up by date (a series with no point
 *  that day leaves a gap) - ISO dates sort lexicographically */
export function timeTable(series: readonly { name: string; data: readonly (readonly [string, number])[] }[],
                          firstColumn = "Date"): ChartTable {
  const dates = [...new Set(series.flatMap((s) => s.data.map(([d]) => d)))].sort();
  const lookup = series.map((s) => new Map<string, number>(s.data.map(([d, v]) => [d, v])));
  return { columns: [firstColumn, ...series.map((s) => s.name)], rows: dates.map((d) => [d, ...lookup.map((m) => m.get(d) ?? null)]) };
}

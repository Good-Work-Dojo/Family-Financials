// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { ChartTable } from "../lib/chartTable";
import { downloadCsv, toCsv } from "../lib/csv";
import { fmtUSD0 } from "../lib/format";
import { OPAQUE_CARD, TableScroll } from "./ui";

/** rows above which the open table gets a height cap and a sticky header */
const LONG_TABLE = 10;
/** a text button under a chart or table: a 44px target on a phone */
export const FOOTER_BTN = "inline-flex min-h-11 min-w-11 items-center text-zinc-500 underline hover:text-zinc-300 disabled:cursor-wait disabled:opacity-40 md:min-h-0 md:min-w-0";

/** The visible notice that goes with every CSV download: privacy mode never touches a file, which is for the family's
 *  own spreadsheet. Words on the page, not a `title` (a phone never shows one). */
export function CsvNote() {
  return <span className="text-[10px] text-faint">real amounts, never masked</span>;
}

/** "⬇ CSV": the table's RAW rows as a download (a gap is an empty field, a number unformatted). Never masked
 *  by the privacy mode, and the words beside the button say so. `table.label` makes its accessible name distinct
 *  per chart. `disabled` while the table is not yet the one on screen (the Reports tab's deferred spec). */
export function CsvButton({ table, disabled }: { table: ChartTable; disabled?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <button type="button" title="CSV exports are never masked" disabled={disabled}
        aria-label={table.label ? `Download CSV: ${table.label}` : undefined}
        onClick={() => downloadCsv(table.csvName ?? "chart.csv", toCsv(table.rows, table.columns))}
        className={FOOTER_BTN}>⬇ CSV</button>
      <CsvNote />
    </span>
  );
}

/** A chart's own numbers as a real table: a caption, column headers, a row header per row, shown through the
 *  table's formatter (which the privacy mask rides on; a gap is a dash, never a 0), inside a scroll box.
 *  The EChart footer shows it behind "view data"; the Reports tab's table view shows it as the report, with
 *  its own `caption` (the default names a chart that table view does not have). */
export default function ChartDataTable({ table, caption }: { table: ChartTable; caption?: string }) {
  const fmt = table.format ?? fmtUSD0;
  const cell = (v: string | number | null, col: number) => (typeof v === "number" ? fmt(v, col) : v ?? "—");
  // a long table (hundreds of daily rows) scrolls inside a capped box under a header that stays put; a short one
  // is just a table
  const long = table.rows.length > LONG_TABLE;
  return (
    <>
      {/* w-0 min-w-full: the table pans inside this wrapper and adds nothing to its ancestors' intrinsic width
          (a wide table in a grid card otherwise widens the grid's implicit column past the viewport).
          The label column sticks to the left edge so a phone that pans a wide table keeps the row names;
          borders sit on the cells (separate borders), where a sticky cell keeps them. */}
      <TableScroll className={`mt-1 w-0 min-w-full ${long ? "max-h-72 overflow-y-auto" : ""}`}
        label={table.label ? `Table: ${table.label}` : "Data behind the chart"}>
        <table className="w-full border-separate border-spacing-0 text-xs">
          <caption className="sr-only">
            {caption ?? `Data behind the chart${table.label ? `: ${table.label}` : ""}, by ${table.columns[0]}`}
          </caption>
          <thead className={long ? `sticky top-0 z-10 ${OPAQUE_CARD}` : ""}>
            <tr className="text-left text-zinc-500">
              {table.columns.map((c, i) => (
                <th key={i} scope="col"
                  className={`border-b border-zinc-700 font-normal ${i
                    ? "pl-2 text-right"
                    : `sticky left-0 z-10 whitespace-nowrap py-1 pr-2 ${OPAQUE_CARD}`}`}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((r, i) => (
              <tr key={i}>
                {r.map((v, j) => j === 0
                  ? <th key={j} scope="row"
                      className={`sticky left-0 z-[1] whitespace-nowrap border-b border-zinc-800/40 py-1 pr-2 text-left font-normal text-zinc-400 ${OPAQUE_CARD}`}>{cell(v, j)}</th>
                  : <td key={j} className="border-b border-zinc-800/40 pl-2 text-right tabular-nums">{cell(v, j)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      {table.note && <p className="mt-1 text-faint">{table.note}</p>}
    </>
  );
}

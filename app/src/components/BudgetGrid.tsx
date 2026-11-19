// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { type ReactNode, useMemo } from "react";
import type { BudgetCell, BudgetGrid, BudgetGridRow } from "../lib/budgets";
import type { CategoryVocab } from "../lib/categories";
import { baseGrid, baseLegend, CHART_GAIN, CHART_MUTED, moneyAxis, moneyTooltip, monthAxis } from "../lib/echarts";
import { fmtCompact, fmtDelta, fmtMonth, fmtUSD0 } from "../lib/format";
import { useExpanded } from "../lib/useExpanded";
import EChart from "./EChart";
import { EmptyState, NotEnoughHistory, NoTransactions } from "./NoData";
import { Badge, Card, TableScroll } from "./ui";

const ROW_CAP = 15;

/** rising / flat / falling by the least-squares slope against the row's mean actual (5% a month) */
export function trendWord(r: BudgetGridRow): "rising" | "flat" | "falling" {
  const mean = r.cells.reduce((s, c) => s + c.actual, 0) / Math.max(1, r.cells.length);
  if (mean <= 0 || Math.abs(r.slope) < 0.05 * mean) return "flat";
  return r.slope > 0 ? "rising" : "falling";
}
const GLYPH = { rising: "↗", flat: "→", falling: "↘" } as const;

/** "last full month" / "last 6 full months": one month is not "1 full months" */
export function monthsPhrase(n: number): string {
  return n === 1 ? "full month" : `${n} full months`;
}

/** the cell's variance in words, for the title and a screen reader */
function varianceText(v: number): string {
  if (v > 0) return `${fmtDelta(v)} over`;
  if (v < 0) return `${fmtUSD0(-v)} under`;
  return "on baseline";
}
/** the same variance, signed, for the second line under the amount: +$40 / −$12 / $0. Through the
 *  shared formatters, so privacy mode masks it with every other money figure. */
const signed = (v: number): string => (v === 0 ? fmtUSD0(0) : fmtDelta(v));

function Cell({ c, source }: { c: BudgetCell; source: BudgetGridRow["source"] }) {
  // no typical month to compare with (a category that has not spent yet) and nothing spent: there is
  // nothing to show, so no "$0" is made up for it. A pinned budget is a baseline even at $0.
  if (c.baseline === 0 && c.actual === 0 && source === "auto") {
    return <td title="no baseline and nothing spent" className="pl-2 text-right tabular-nums text-faint">—</td>;
  }
  const text = varianceText(c.variance);
  // the amount carries the colour; the signed variance beneath it is the visible, non-colour,
  // non-hover cue (a phone has no hover and red/green alone fails colour-blind readers). A screen
  // reader gets the words from the sr-only span, so the visual line is aria-hidden.
  return (
    <td title={`baseline ${fmtUSD0(c.baseline)} · ${text}`}
      className={`pl-2 text-right tabular-nums ${c.variance > 0 ? "text-red-300" : "text-emerald-300"}`}>
      <div>{fmtUSD0(c.actual)}<span className="sr-only"> ({text})</span></div>
      <div aria-hidden="true" className="text-[10px] leading-tight text-faint">{signed(c.variance)}</div>
    </td>
  );
}

/** Months as columns (one per full month), categories as rows, each cell the month's actual coloured by
 *  its variance against the baseline in the title; the last column is the trend. The category links to
 *  that category's transactions for the newest column's month. */
export function BudgetGridTable({ grid, linkFor }: { grid: BudgetGrid; linkFor: (category: string, month: string) => string }) {
  const { isOpen, toggle } = useExpanded("multi");
  const last = grid.months[grid.months.length - 1];
  const all = isOpen("all-rows");
  const shown = all ? grid.rows : grid.rows.slice(0, ROW_CAP);
  return (
    <>
      <TableScroll label="Budget table" stickyFirst>
        <table className="w-full text-xs">
          <caption className="sr-only">
            Spending by category for the last {monthsPhrase(grid.months.length)}, each cell the actual with its difference from a typical-month baseline
          </caption>
          <thead>
            <tr className="border-b border-zinc-700 text-left text-zinc-500">
              <th className="py-1 pr-2">Category</th>
              {grid.months.map((m) => <th key={m} className="pl-2 text-right">{fmtMonth(m)}</th>)}
              <th className="pl-2 text-right">Trend</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const trend = trendWord(r);
              return (
                <tr key={r.category} className="border-b border-zinc-800/40">
                  <th scope="row" className="py-1.5 pr-2 text-left font-normal">
                    <a className="inline-flex min-h-[40px] items-center hover:text-blue-300 md:min-h-0" href={linkFor(r.category, last)}>{r.category}</a>
                    {r.source === "set" && <span className="ml-1"><Badge cls="bg-blue-950 text-blue-300"
                      title="a budget the family pinned, in the app or in the configuration, in place of the typical month">pinned</Badge></span>}
                  </th>
                  {r.cells.map((c) => <Cell key={c.month} c={c} source={r.source} />)}
                  <td className="pl-2 text-right text-zinc-400">
                    <span aria-hidden="true">{GLYPH[trend]}</span><span className="sr-only">{trend}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableScroll>
      {grid.rows.length > ROW_CAP && (
        <button type="button" aria-expanded={all} onClick={() => toggle("all-rows")}
          className="mt-1 min-h-[40px] text-[11px] text-blue-400 hover:underline md:min-h-0">
          {all ? `Show the top ${ROW_CAP}` : `Show all ${grid.rows.length} categories`}
        </button>
      )}
    </>
  );
}

/** Each month's spend split into fixed and discretionary. "Fixed" is the emergency-fund model's
 *  essential list (lib/emergency.ts) with this device's Planning toggles applied; `grid.toggledRows`
 *  is how many rows in these months those toggles moved, which is what the note and the legend say.
 *  The split covers the budget grid's months and sums every spend transaction of them, whatever the grid's row floor:
 *  it is drawn whenever those months hold some spending, rows or no rows. Otherwise the card says why, in this order:
 *  no transaction at all (`noTransactions`), no full month yet, or full months with no spending. */
export function FixedVsDiscretionaryCard({ grid, vocab, noTransactions = false }: {
  grid: BudgetGrid; vocab: CategoryVocab;
  /** the household has no transaction at all */
  noTransactions?: boolean;
}) {
  // the budget card above it says the long line
  if (noTransactions) return <FixedVsDiscretionaryEmpty><NoTransactions short /></FixedVsDiscretionaryEmpty>;
  if (!grid.months.length) {
    return (
      <FixedVsDiscretionaryEmpty>
        <NotEnoughHistory>fixed and discretionary spend appear month by month with the budget grid above, once two full months are in.</NotEnoughHistory>
      </FixedVsDiscretionaryEmpty>
    );
  }
  if (grid.fixedVsDiscretionary.every((m) => m.fixed === 0 && m.discretionary === 0)) {
    return <FixedVsDiscretionaryEmpty><EmptyState>No spending in these months yet.</EmptyState></FixedVsDiscretionaryEmpty>;
  }
  return <FixedVsDiscretionaryChart grid={grid} vocab={vocab} />;
}

/** the split's frame with one line in place of the chart */
function FixedVsDiscretionaryEmpty({ children }: { children: ReactNode }) {
  return <Card testId="card-fixed-vs-discretionary" title={SPLIT_TITLE}>{children}</Card>;
}

const SPLIT_TITLE = "Fixed vs discretionary spend";

function FixedVsDiscretionaryChart({ grid, vocab }: { grid: BudgetGrid; vocab: CategoryVocab }) {
  const toggled = grid.toggledRows;
  const option = useMemo(() => ({
    grid: baseGrid, legend: baseLegend(), tooltip: moneyTooltip(),
    xAxis: monthAxis(grid.fixedVsDiscretionary.map((m) => m.month)),
    yAxis: moneyAxis(fmtCompact),
    series: [
      { name: toggled > 0 ? "Fixed (essential as marked)" : "Fixed (essential by default)", type: "bar", stack: "burn", barMaxWidth: 56,
        color: CHART_MUTED, data: grid.fixedVsDiscretionary.map((m) => Math.round(m.fixed)) },
      { name: "Discretionary", type: "bar", stack: "burn", barMaxWidth: 56, color: CHART_GAIN, data: grid.fixedVsDiscretionary.map((m) => Math.round(m.discretionary)) },
    ],
  }), [grid, toggled]);
  const { categories, pairs } = vocab.essentialLabels();
  return (
    <Card testId="card-fixed-vs-discretionary" title={SPLIT_TITLE}>
      <EChart option={option} height={220} />
      <p className="mt-1 text-[10px] text-faint">
        Fixed = spending in categories essential by default ({categories.join(", ")}; plus {pairs.join(", ")}) —
        the emergency-fund list; everything else is discretionary.
      </p>
      <p className="text-[10px] text-faint">
        {toggled > 0
          ? `${toggled} row${toggled === 1 ? "" : "s"} toggled on Planning's emergency-fund card (this device only) ${toggled === 1 ? "moves" : "move"} between fixed and discretionary here.`
          : "Toggle rows on Planning's emergency-fund card and this split follows (this device only)."}
      </p>
    </Card>
  );
}

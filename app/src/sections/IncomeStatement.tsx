// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Cash Flow → Income statement: the money-flow sankey and the household P&L table across
// selectable trailing windows (`?pl=`; the default is the trailing 12 months).
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import EChart from "../components/EChart";
import SplitNote from "../components/SplitNote";
import { EmptyState } from "../components/NoData";
import RequireData from "../components/RequireData";
import { Card, SegmentToggle, TableScroll } from "../components/ui";
import { useData } from "../data/DataContext";
import { PRESET_LABELS } from "../filters/dateRange";
import { isInvestmentAccount } from "../lib/accounts";
import { coverageFloorLabel, fullMonths, monthInProgress } from "../lib/agg";
import { sankeyHeight, sankeyOption } from "../lib/echarts";
import { countOf, fmtMonth, fmtPct, fmtUSD0, wholeDollars } from "../lib/format";
import { belowRateFloor, lastFullYear, type PnL, PlPreset, plPresetMonths, plWindow, pnl, printableRate, sankeyFlows,
  shortfallText } from "../lib/lifeSummary";

// the shared preset words (filters/dateRange PRESET_LABELS), so "12M" is spelled one way on every page that picks a window
const PL_OPTIONS: { value: PlPreset; label: string }[] = (["3m", "6m", "12m", "ytd"] as const).map((value) => ({ value, label: PRESET_LABELS[value] }));
const PL_COL_LABEL: Record<Exclude<PlPreset, "ytd">, string> = {
  "3m": "Trailing 3 mo", "6m": "Trailing 6 mo", "12m": "Trailing 12 mo",
};
// prose form for the sankey card title (smoke.test.tsx pins the "12m" default to "trailing 12 months")
const PL_TITLE: Record<PlPreset, string> = {
  "3m": "trailing 3 months", "6m": "trailing 6 months",
  "12m": "trailing 12 months", ytd: "year to date",
};

const PNL_TITLE = "Household P&L";

export default function IncomeStatement() {
  return (
    <RequireData files={["cashflow_monthly.json"]} what="The income statement">
      <IncomeStatementPage />
    </RequireData>
  );
}

function IncomeStatementPage() {
  const { data } = useData();
  // the full months (lib/agg.ts fullMonths: the coverage months before the month in progress) are the only months a
  // column reads, so no window reaches before the coverage floor or into the month in progress; the last of them ends
  // every window, and with none there is no column to show
  const inProgress = useMemo(() => monthInProgress(data), [data]);
  const full = useMemo(() => fullMonths(data.cashflow, inProgress), [data.cashflow, inProgress]);
  const lastFull = full.at(-1)?.month ?? null;
  const [sp, setSp] = useSearchParams();
  const plRaw = sp.get("pl");
  const pl: PlPreset = plRaw === "3m" || plRaw === "6m" || plRaw === "ytd" ? plRaw : "12m";
  const setPl = (v: PlPreset) => {
    setSp((prev) => {
      const next = new URLSearchParams(prev);
      if (v === "12m") next.delete("pl"); else next.set("pl", v);
      return next;
    }, { replace: true });
  };

  const floorLabel = coverageFloorLabel(data.cashflow);
  const cols = useMemo(() => {
    const floor = full[0]?.month ?? null;
    if (lastFull === null || floor === null) return [];
    const year = lastFull.slice(0, 4);
    const [selFrom, selTo] = plWindow(pl, lastFull, floor);
    // the middle column is the last full calendar year the full months hold; none, no column
    const fullYear = lastFullYear(full, lastFull);
    const windows: [string, string, string][] = [
      [selFrom, selTo, pl === "ytd" ? `${year} YTD` : PL_COL_LABEL[pl]],
      ...(fullYear === null ? [] : [[`${fullYear}-01`, `${fullYear}-12`, fullYear] as [string, string, string]]),
      [`${year}-01` < floor ? floor : `${year}-01`, lastFull, `${year} YTD`],
    ];
    // dedupe by window (selecting YTD collapses the duplicate YTD column, and so does a trailing window the data holds
    // no more months of than the year so far)
    const seen = new Set<string>();
    const out = [];
    for (const [from, to, label] of windows) {
      if (seen.has(`${from}|${to}`)) continue;
      seen.add(`${from}|${to}`);
      const p = pnl(full, from, to, label);
      // a trailing window the data holds fewer months of is labelled with the months it has, never "12 mo" over fewer
      if (pl !== "ytd" && label === PL_COL_LABEL[pl] && p.monthCount < plPresetMonths(pl)) p.label = `Trailing ${p.monthCount} mo`;
      if (p.monthCount > 0) out.push(p);
    }
    return out;
  }, [full, lastFull, pl]);

  // the selected window: undefined when the ledger has no full month at all (empty, or only the
  // partial current month)
  const ttm: PnL | undefined = cols[0];

  const sankey = useMemo(() => (ttm ? sankeyOption(sankeyFlows(ttm)) : null), [ttm]);

  if (!ttm || !sankey || lastFull === null) {
    return (
      <div className="space-y-4">
        <Card testId="card-sankey" title="Income statement — no full month of cash-flow data yet">
          <p className="text-xs text-zinc-500">
            The money-flow graph covers full months only; it appears once a full month of account coverage exists.
          </p>
        </Card>
        <Card testId="card-pnl" title={PNL_TITLE}>
          <EmptyState>
            No full month of cash flow yet. Income, spending and savings per window appear here once a full month of
            account coverage exists.
          </EmptyState>
        </Card>
      </div>
    );
  }

  const incomeRows = ttm.incomeBySub.slice(0, 6).map((l) => l.name);
  const spendRows = ttm.spendByCat.slice(0, 9).map((l) => l.name);
  // every figure is printed in whole dollars, and each total is the sum of the whole-dollar figures above it, so a
  // reader adding a column gets the total shown
  const value = (c: PnL, kind: "income" | "spend", name: string) =>
    (kind === "income" ? c.incomeBySub : c.spendByCat).find((l) => l.name === name)?.value ?? 0;
  const cell = (c: PnL, kind: "income" | "spend", name: string) => wholeDollars(value(c, kind, name));
  const names = (kind: "income" | "spend") => (kind === "income" ? incomeRows : spendRows);
  const listed = (c: PnL, kind: "income" | "spend") => names(kind).reduce((s, n) => s + cell(c, kind, n), 0);
  // what the listed rows leave of a column's total: the streams or categories the table does not list (from the exact
  // figures, so the listed rows' rounding never lands in it)
  const other = (c: PnL, kind: "income" | "spend") => wholeDollars((kind === "income" ? c.income : c.spend)
    - names(kind).reduce((s, n) => s + value(c, kind, n), 0));
  // a row that sums nothing is left out: an "Other" row only when some column has a remainder the listed rows do not
  // hold, "Invested" only for a household with an investment account or a transfer into one in these windows
  const hasOtherIncome = cols.some((c) => other(c, "income") !== 0);
  const hasOther = cols.some((c) => other(c, "spend") !== 0);
  const totalIncome = (c: PnL) => listed(c, "income") + other(c, "income");
  const totalSpend = (c: PnL) => listed(c, "spend") + other(c, "spend");
  const hasInvested = data.accounts.some(isInvestmentAccount) || cols.some((c) => c.invested !== 0);
  // the columns whose rate is below the printable floor (spending over twice the income): their row reads "—", and
  // the line under the table gives each one's shortfall in dollars
  const short = cols.filter((c) => belowRateFloor(c.rate));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-zinc-500">
          Full months through {fmtMonth(lastFull)} — the money-flow graph and the household P&L
          over a selectable window.
        </p>
        <SegmentToggle label="Window" value={pl} options={PL_OPTIONS} onChange={setPl} />
      </div>
      <Card testId="card-sankey" title={`Where the money goes — ${pl !== "ytd" && ttm.monthCount < plPresetMonths(pl)
        ? `trailing ${countOf(ttm.monthCount, "month")}` : PL_TITLE[pl]} (${fmtUSD0(ttm.income)} in)`}>
        {/* its own height on a phone too: under the 240px cap the small nodes' labels collide and hide */}
        <EChart option={sankey} height={sankeyHeight(sankey)} phoneHeight={sankeyHeight(sankey)} />
      </Card>
      <Card testId="card-pnl" title={PNL_TITLE}>
        <TableScroll label="Household P&L table" stickyFirst>
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-zinc-700 text-left text-zinc-500">
              <th className="py-1"></th>
              {/* the months a column holds, said once: a trailing window's label already counts them */}
              {cols.map((c) => (
                <th key={c.label} className="text-right">{c.label}
                  {c.label !== `Trailing ${c.monthCount} mo` && <span className="block font-normal text-faint">{c.monthCount} mo</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {incomeRows.map((name) => (
              <tr key={name} className="border-b border-zinc-800/40">
                <td className="py-1 pl-2 text-zinc-400">{name}</td>
                {cols.map((c) => (
                  <td key={c.label} className="text-right tabular-nums">{fmtUSD0(cell(c, "income", name))}</td>
                ))}
              </tr>
            ))}
            {hasOtherIncome && (
              <tr className="border-b border-zinc-800/40">
                <td className="py-1 pl-2 text-zinc-500">Other income</td>
                {cols.map((c) => (
                  <td key={c.label} className="text-right tabular-nums text-zinc-500">{fmtUSD0(other(c, "income"))}</td>
                ))}
              </tr>
            )}
            <TotalRow label="Total income" cols={cols} get={totalIncome} cls="text-emerald-400" />
            {spendRows.map((name) => (
              <tr key={name} className="border-b border-zinc-800/40">
                <td className="py-1 pl-2 text-zinc-400">{name}</td>
                {cols.map((c) => (
                  <td key={c.label} className="text-right tabular-nums">{fmtUSD0(cell(c, "spend", name))}</td>
                ))}
              </tr>
            ))}
            {hasOther && (
              <tr className="border-b border-zinc-800/40">
                <td className="py-1 pl-2 text-zinc-500">Other categories</td>
                {cols.map((c) => (
                  <td key={c.label} className="text-right tabular-nums text-zinc-500">{fmtUSD0(other(c, "spend"))}</td>
                ))}
              </tr>
            )}
            <TotalRow label="Total spending" cols={cols} get={totalSpend} cls="text-red-400" />
            {/* toned by sign: a net below zero is no saving, so it is never green */}
            <TotalRow label="Net savings" cols={cols} get={(c) => totalIncome(c) - totalSpend(c)}
              cls={(v) => (v < 0 ? "text-red-400" : "text-emerald-400")} bold />
            {/* not a third term of the sum: the part of the net savings that moved into investment accounts */}
            {hasInvested && (
              <tr className="border-b border-zinc-800/40">
                <td className="py-1 pl-4 text-zinc-500">moved to investment accounts</td>
                {cols.map((c) => (
                  <td key={c.label} className="text-right tabular-nums text-zinc-500">{fmtUSD0(c.invested)}</td>
                ))}
              </tr>
            )}
            <tr data-testid="pnl-savings-rate">
              <td className="py-1.5 font-semibold">Savings rate</td>
              {cols.map((c) => (
                <td key={c.label} className="text-right font-semibold tabular-nums">{fmtPct(printableRate(c.rate), 0)}</td>
              ))}
            </tr>
          </tbody>
        </table>
        </TableScroll>
        {short.length > 0 && (
          <p data-testid="pnl-shortfall" className="mt-2 text-xs text-zinc-400">
            Savings rate — where {shortfallText(short[0].net / short[0].monthCount)} ({short[0].label})
            {short.slice(1).map((c) => `, ${fmtUSD0(c.net / c.monthCount)} a month (${c.label})`).join("")}.
          </p>
        )}
        <p className="mt-2 text-[10px] text-faint">
          income = salary + dividends + interest etc.; spending = purchases + fees;
          transfers and credit-card payments excluded.
          {floorLabel !== null && ` Months before account coverage (before ${floorLabel}) excluded from rates.`}
        </p>
        <SplitNote categories={ttm.spendByCat.map((l) => l.name)} />
      </Card>
    </div>
  );
}

/** a total row; `cls` is a class for every cell, or a function of each cell's value (a tone by sign) */
function TotalRow({ label, cols, get, cls = "", bold }: {
  label: string; cols: PnL[]; get: (c: PnL) => number; cls?: string | ((v: number) => string); bold?: boolean;
}) {
  return (
    <tr className="border-b border-zinc-700">
      <td className={`py-1.5 ${bold ? "font-semibold" : "font-medium"}`}>{label}</td>
      {cols.map((c) => (
        <td key={c.label} className={`text-right tabular-nums ${bold ? "font-semibold" : "font-medium"} ${typeof cls === "function" ? cls(get(c)) : cls}`}>
          {fmtUSD0(get(c))}
        </td>
      ))}
    </tr>
  );
}

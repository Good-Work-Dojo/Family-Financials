// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Headline metrics over time, recomputed from already-loaded history (lib/lifeTrends).
// Small-multiple cards, mobile-tuned. Net worth over time lives on Home and income by stream on Cash Flow → Overview;
// this page carries the rates and shares neither of those shows.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import EChart from "../components/EChart";
import { EmptyState, NoBalances, NotEnoughHistory, NoTransactions } from "../components/NoData";
import RequireData from "../components/RequireData";
import { Card, SegmentToggle } from "../components/ui";
import { useData } from "../data/DataContext";
import { isInvestmentAccount } from "../lib/accounts";
import { coverageFloorLabel, fullMonths, monthInProgress } from "../lib/agg";
import { type ChartTable, timeTable } from "../lib/chartTable";
import { footingNote, onOneFooting } from "../lib/comparableSnapshots";
import { baseGrid, catAxis, CHART_GAIN, CHART_STRONG, CHART_TEXT, compactStep, levelScale, moneyAxis, moneyTooltip, shareBounds, timeAxis,
  valueAxis, WHOLE_PERCENT_STEP, wrapLegend } from "../lib/echarts";
import { countOf, fmtCompact, fmtMonth, fmtMonthAxis, fmtPct, fmtUSD0 } from "../lib/format";
import { LIQUID_MEANS } from "../lib/lifeSummary";
import { allocationTrend, burnTrend, liquidityTrend, savingsRateTrend } from "../lib/lifeTrends";
import { milestoneCrossings } from "../lib/milestones";
import { CLASS_COLORS } from "../lib/tokens";

/** a flat page: it prints its own heading, which stays when the cash-flow export is missing */
export default function Trends() {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Trends</h1>
      <RequireData files={["cashflow_monthly.json"]} what="Trends">
        <TrendsPage />
      </RequireData>
    </div>
  );
}

function TrendsPage() {
  const { data } = useData();
  const snaps = data.assets.snapshots;
  const [liqView, setLiqView] = useState<"share" | "dollars">("share");
  // the cash-flow lines end on the last full month: the month in progress (lib/agg.ts monthInProgress) would set half its
  // bills against whole months, and a month after it has not begun
  const inProgress = useMemo(() => monthInProgress(data), [data]);
  const lastFull = fullMonths(data.cashflow, inProgress).at(-1)?.month ?? null;
  // how far back (and up to when) each kind of trend reaches, in words; a clause with nothing to name is left out
  const floorLabel = coverageFloorLabel(data.cashflow);
  // the floor is where enough of the cash-flow accounts have begun; with one such account it is where the data begins
  const cashflowAccounts = new Set(data.cashflow.flatMap((m) => m.accounts_covered)).size;
  const reach = [
    ...(floorLabel === null ? [] : [`cash-flow trends start ${floorLabel} (when ${cashflowAccounts > 1
      ? "multi-account coverage begins" : "the data begins"})`
      + (lastFull === null ? "" : ` and end ${fmtMonth(lastFull)}, the last full month`)]),
    ...(snaps.length > 1 ? [`snapshot trends reach back to ${snaps[0].snapshot_date.slice(0, 4)}`] : []),
  ];

  const savings = useMemo(() => savingsRateTrend(data.cashflow, inProgress), [data.cashflow, inProgress]);
  // the windows the two cash-flow lines really read: 3 and 6 full months once the data holds them, every full month
  // before that (savings.length is the full months), named in each card's title and series as Cash Flow names them
  const fullCount = savings.length;
  const rateMonths = Math.min(3, fullCount), burnMonths = Math.min(6, fullCount);
  const savingsOption = useMemo(() => ({
    grid: baseGrid,
    tooltip: { trigger: "axis",
      valueFormatter: (v: number | null) => (v == null ? "—" : fmtPct(v)) },
    xAxis: { ...catAxis(savings.map((p) => fmtMonthAxis(p.month))), axisLabel: { color: CHART_TEXT, hideOverlap: true } },
    yAxis: valueAxis((v) => fmtPct(v, 0)),
    series: [{ name: `Savings rate (trailing ${rateMonths} mo)`, type: "line",
      color: CHART_STRONG, symbolSize: 4, data: savings.map((p) => p.value) }],
  }), [savings, rateMonths]);

  const burn = useMemo(() => burnTrend(data.cashflow, inProgress), [data.cashflow, inProgress]);
  const burnOption = useMemo(() => ({
    grid: baseGrid,
    tooltip: moneyTooltip(),
    xAxis: { ...catAxis(burn.map((p) => fmtMonthAxis(p.month))), axisLabel: { color: CHART_TEXT, hideOverlap: true } },
    yAxis: moneyAxis(fmtCompact),
    series: [{ name: `Monthly burn (trailing ${burnMonths} mo avg)`, type: "line",
      color: "#f59e0b", symbolSize: 4, areaStyle: { opacity: 0.15 },
      data: burn.map((p) => p.value) }],
  }), [burn, burnMonths]);

  // the two snapshot series set the newest snapshot beside month ends, so they read it on the month ends' footing (a
  // live margin account net of its loan, as every month end holds it); a card whose last point moved says so
  const footed = useMemo(() => {
    const classes = new Map(data.accounts.map((a) => [a.id, a.asset_class]));
    return onOneFooting(snaps, (id) => classes.get(id));
  }, [snaps, data.accounts]);
  const alloc = useMemo(() => allocationTrend(footed.snaps), [footed]);
  // stacked areas read as a mix over time; with one snapshot there is no area, and stacked dots would plot each class at
  // the running sum (cash at 100% where it is 9%): each class is plotted at its own share instead
  const stacked = snaps.length > 1;
  const allocOption = useMemo(() => ({
    grid: baseGrid,
    legend: wrapLegend(),
    tooltip: { trigger: "axis", valueFormatter: (v: number) => `${v}%` },
    xAxis: timeAxis(),
    yAxis: { ...valueAxis((v) => `${v}%`), max: 100 },
    series: alloc.classes.map((c) => ({
      name: c, type: "line", ...(stacked ? { stack: "alloc", areaStyle: { opacity: 0.5 } } : {}),
      symbolSize: 4, color: CLASS_COLORS[c] ?? "#9ca3af",
      data: alloc.rows.map((r) => [r.date, r.pct[c]]),
    })),
  }), [alloc, stacked]);

  // Liquidity: the liquid share of assets (the base Home's tile and the allocation donut use), or the liquid assets in
  // dollars - one axis per view, never two. The view-data table lists both, with the assets they are a share of, per
  // snapshot (the arrays the two series draw). A snapshot with no asset above zero has no share: no point on that line,
  // a dash in the table
  const liq = useMemo(() => {
    const rows = liquidityTrend(footed.snaps);
    const share = rows.flatMap((p) => (p.liquidPct === null ? [] : [[p.date, p.liquidPct] as [string, number]]));
    const dollars = rows.map((p) => [p.date, p.liquid] as [string, number]);
    const base = timeTable([{ name: "Liquid assets", data: dollars },
      { name: "Assets", data: rows.map((p) => [p.date, p.assets] as [string, number]) },
      { name: "Liquid share", data: share }]);
    const SHARE_COL = base.columns.indexOf("Liquid share");
    const table: ChartTable = { ...base, format: (v, col) => (col === SHARE_COL ? fmtPct(v) : fmtUSD0(v)),
      csvName: "liquidity.csv", label: "liquid assets and their share of assets" };
    return { share, dollars, table };
  }, [footed]);
  // the liquid share and the liquid dollars are levels: each axis leaves zero by the one rule (levelScale), a share
  // never past 100%
  const liqOption = useMemo(() => (liqView === "share" ? {
    grid: baseGrid,
    tooltip: { trigger: "axis", valueFormatter: (v: number) => fmtPct(v) },
    xAxis: timeAxis(),
    yAxis: { ...valueAxis((v) => fmtPct(v, 0)), ...levelScale(liq.share.map(([, v]) => v), WHOLE_PERCENT_STEP), ...shareBounds() },
    series: [{ name: "Liquid share of assets", type: "line",
      color: CHART_GAIN, symbolSize: 5, data: liq.share }],
  } : {
    grid: baseGrid,
    tooltip: moneyTooltip(),
    xAxis: timeAxis(),
    yAxis: { ...moneyAxis(fmtCompact),
      ...levelScale(liq.dollars.map(([, v]) => v), compactStep(Math.max(0, ...liq.dollars.map(([, v]) => Math.abs(v))))) },
    series: [{ name: "Liquid assets", type: "line", color: CHART_GAIN, symbolSize: 5, data: liq.dollars }],
  }), [liq, liqView]);

  // What each chart lacks, in one line in place of the chart (a card keeps its frame and heading): the two cash-flow
  // lines with no transaction (the page's long line, then the short one), no full month, or no income to rate; the two
  // snapshot lines with no snapshot (the same pattern) or no class above zero. A line of one point is drawn and says so.
  const noTransactions = data.transactions.length === 0;
  const cashflowEmpty = (short: boolean) => (noTransactions ? <NoTransactions short={short} />
    : <NotEnoughHistory>the line is drawn month by month once a full month is in.</NotEnoughHistory>);
  const onePoint = (what: "month" | "snapshot") => (what === "month"
    ? <NotEnoughHistory>one full month so far; the line draws once a second one is in.</NotEnoughHistory>
    : <NotEnoughHistory>one snapshot so far; the line draws once a second one exists.</NotEnoughHistory>);
  // the footer names only what this household has: Home's net-worth chart where Home draws one (two snapshots, or one
  // and a daily line of more than a day), with the daily line and the pins where it draws them, the holdings
  // concentration with an investment account or holdings, the mortgage summary with a mortgage account
  const homeCharts = snaps.length > 1 || (snaps.length === 1 && data.networthDaily.series.length > 1);
  const homeHas = [data.networthDaily.series.length > 1 && "the daily line",
    milestoneCrossings(data).length > 0 && "the milestone pins"].filter((x): x is string => typeof x === "string");
  const hasHoldings = data.holdings.length > 0 || data.accounts.some(isInvestmentAccount);
  const titleOf = (base: string, months: number, steady: number, steadyWords: string, words: string) =>
    months === 0 ? base : months === steady ? `${base} — ${steadyWords}` : `${base} — ${words}`;
  const hasMortgage = data.assumptions.mortgage !== null;
  const accountsLink = <Link to="/accounts" className="text-blue-400 hover:underline">Accounts</Link>;
  const planningLink = <Link to="/planning" className="text-blue-400 hover:underline">Planning</Link>;

  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <Card testId="card-trend-savings"
          title={titleOf("Savings rate", rateMonths, 3, "trailing 3 months", `trailing ${countOf(rateMonths, "full month")}`)}>
          {fullCount === 0 ? cashflowEmpty(false)
            : savings.every((p) => p.value === null)
              ? <EmptyState>No income in the full months, so there is no savings rate to draw.</EmptyState>
              : <>
                <EChart option={savingsOption} height={240} />
                {fullCount === 1 && onePoint("month")}
              </>}
        </Card>
        <Card testId="card-trend-burn" title={titleOf("Monthly burn", burnMonths, 6, "trailing 6-month average",
          `average of the trailing ${countOf(burnMonths, "full month")}`)}>
          {burn.length === 0 ? cashflowEmpty(true) : <>
            <EChart option={burnOption} height={240} />
            {burn.length === 1 && onePoint("month")}
          </>}
        </Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card testId="card-trend-allocation" title="Allocation mix — % of positive assets per snapshot">
          {snaps.length === 0 ? <NoBalances />
            : alloc.classes.length === 0
              ? <EmptyState>No asset above zero in the newest snapshot, so there is no mix to draw.</EmptyState>
              : <>
                <EChart option={allocOption} height={260} />
                {snaps.length === 1 && onePoint("snapshot")}
                {footed.folded && <p data-testid="trend-allocation-footing" className="mt-1 text-[10px] text-faint">
                  {footingNote("on Home and Balances")}</p>}
              </>}
        </Card>
        <Card testId="card-trend-liquidity"
          title={liqView === "share" ? "Liquidity — liquid share of assets" : "Liquidity — liquid assets"}
          right={snaps.length === 0 ? undefined : (
            <SegmentToggle label="Show as" value={liqView} onChange={setLiqView}
              options={[{ value: "share", label: "% of assets" }, { value: "dollars", label: "dollars" }]} />
          )}>
          {snaps.length === 0 ? <NoBalances short /> : <>
            {/* one line only: with no share to draw there is no point to say is alone */}
            {liqView === "share" && liq.share.length === 0
              ? <EmptyState>No snapshot has an asset above zero, so there is no share to draw; the dollars view shows the liquid assets.</EmptyState>
              : <>
                <EChart option={liqOption} tableData={liq.table} height={260} />
                {snaps.length === 1 && onePoint("snapshot")}
              </>}
            {/* a share of assets, which hold no debt: a snapshot that pre-dates the mortgage data reads the same as one that
                subtracts it, so no † note belongs here */}
            <p className="mt-1 text-[10px] text-faint">
              liquid = {LIQUID_MEANS}, as a share of everything owned (debts are not subtracted), per snapshot
            </p>
            {footed.folded && <p data-testid="trend-liquidity-footing" className="mt-1 text-[10px] text-faint">
              {footingNote("on Home and Balances")}</p>}
          </>}
        </Card>
      </div>
      <p className="text-[10px] text-faint">
        {homeCharts ? <>
          Net worth over every snapshot{homeHas.length > 0 && `, with ${homeHas.join(" and ")},`} is on{" "}
          <Link to="/" className="text-blue-400 hover:underline">Home</Link>; income by stream, month by month, is on{" "}
        </> : "Income by stream, month by month, is on "}
        <Link to="/cash-flow" className="text-blue-400 hover:underline">Cash Flow → Overview</Link>.
        Everything here is recomputed from full history on load{reach.length > 0 && ` — ${reach.join("; ")}`}.
        {hasHoldings && hasMortgage && <>
          {" "}Holdings concentration and the mortgage summary are current-only data ({accountsLink} and {planningLink}) and
          deliberately have no invented history.
        </>}
        {hasHoldings && !hasMortgage && <>
          {" "}Holdings concentration is current-only data ({accountsLink}) and deliberately has no invented history.
        </>}
        {!hasHoldings && hasMortgage && <>
          {" "}The mortgage summary is current-only data ({planningLink}) and deliberately has no invented history.
        </>}
      </p>
    </div>
  );
}
